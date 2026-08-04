"""`mjj` — the command line.

Two modes that matter: ``mjj exec "..."`` for scripts and CI, and ``mjj`` for
an interactive session. Everything else is introspection.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import threading
import time
from pathlib import Path

from . import auth
from .agent import Agent, render, render_exec
from .config import (
    ConfigError,
    EFFORTS,
    PERMISSION_MODES,
    PROVIDERS,
    VERBOSITIES,
    load as load_config,
)
from .ledger import Budget, Ledger
from .model import ModelClient, probe
from .media import ImageInputError, prepare_image
from .permissions import PermissionPolicy
from .search.cli import main as search_main
from .search.index import build_index
from .session import (
    Session,
    export_session,
    fork_session,
    import_session,
    list_sessions,
    resume,
)
from .skills import discover
from .tools import build_registry
from .version import __version__
from .visualize import KINDS, PALETTES, VisualizerError, generate_visualizer


def _agent(args) -> Agent:
    items: list[dict] = []
    cwd = Path(args.cwd).resolve()
    if getattr(args, "fork", None) is not None:
        session, items = fork_session(args.fork or None)
    elif getattr(args, "resume", None) is not None:
        session, items = resume(args.resume or None)
    elif getattr(args, "ephemeral", False):
        session = None
    else:
        session = Session(meta={"cwd": str(cwd)})
    client = ModelClient(
        model=args.model,
        provider=args.provider,
        effort=args.effort,
        verbosity=args.verbosity,
        resolver=auth.CredentialResolver(provider=args.provider),
    )
    agent = Agent(
        registry=build_registry(
            disabled=args.disabled_tools,
            skill_paths=args.skill_paths,
        ),
        client=client,
        cwd=cwd,
        ledger=Ledger(Budget(default=args.tool_budget)),
        session=session,
        project_doc_max_bytes=args.resolved_config.project_doc_max_bytes,
    )
    agent.items = items
    permission_mode = getattr(args, "permission_mode", "auto")
    if permission_mode != "auto":
        agent.approve = PermissionPolicy(permission_mode)
        agent.ctx.approve = agent.approve
    name = getattr(args, "name", None)
    if name and session:
        session.note(name=name)
    return agent


def cmd_exec(args) -> int:
    try:
        agent = _agent(args)
    except (OSError, ValueError) as exc:
        print(f"mjj exec: {exc}", file=sys.stderr)
        return 2
    positional = args.prompt
    if isinstance(positional, list):
        positional = " ".join(positional) or None
    prompt = _exec_prompt(positional)
    if args.permission_mode == "ask":
        policy = PermissionPolicy("ask", prompt=_headless_approval_prompt)
        agent.approve = policy
        agent.ctx.approve = policy
    try:
        images = tuple(prepare_image(path) for path in args.images)
    except ImageInputError as exc:
        print(f"mjj exec: {exc}", file=sys.stderr)
        return 2
    heartbeat = _Heartbeat(
        sys.stderr,
        f"{args.provider}/{args.model} · reasoning {args.effort}",
    )
    try:
        heartbeat.start()
        code, final_text = render_exec(
            agent.run(
                prompt,
                images=images,
                auto_next_steps=args.auto_next_steps,
                auto_next_idea=args.auto_next_idea,
                max_autonomous_turns=args.auto_max_turns,
            ),
            sys.stdout,
            sys.stderr,
            verbose=args.verbose,
            jsonl=args.json,
        )
    except KeyboardInterrupt:
        print("mjj exec: interrupted", file=sys.stderr)
        code, final_text = 130, ""
    finally:
        heartbeat.stop()
    if args.output_last_message:
        try:
            Path(args.output_last_message).expanduser().write_text(
                final_text, encoding="utf-8"
            )
        except OSError as exc:
            print(f"mjj exec: cannot write final message: {exc}", file=sys.stderr)
            code = 1
    if agent.session:
        agent.session.note(usage=agent.client.usage.summary(), tools=agent.ledger.summary())
        agent.session.close()
    if args.verbose:
        print(f"[{agent.ledger.summary()}]", file=sys.stderr)
    return code


def _headless_approval_prompt(message: str) -> str:
    if not sys.stdin.isatty():
        return ""
    try:
        print(message, end="", file=sys.stderr, flush=True)
        return sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return ""


class _Heartbeat:
    """Keep long reasoning turns visibly alive without touching stdout."""

    def __init__(self, out, label: str, interval: float = 30.0) -> None:
        self.out = out
        self.label = label
        self.interval = interval
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def start(self) -> None:
        self._started = time.monotonic()
        print(f"· working · {self.label}", file=self.out, flush=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stopped.wait(self.interval):
            elapsed = int(time.monotonic() - self._started)
            print(f"· still working · {elapsed}s", file=self.out, flush=True)

    def stop(self) -> None:
        self._stopped.set()
        if self._thread:
            self._thread.join(timeout=0.2)


def _exec_prompt(positional: str | None, max_stdin_chars: int = 1_048_576) -> str:
    """Combine the positional prompt and bounded piped stdin like Codex exec."""
    if positional == "-":
        positional = None
    piped = ""
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, OSError):
        interactive = False
    if not interactive:
        piped = sys.stdin.read(max_stdin_chars + 1)
        if len(piped) > max_stdin_chars:
            piped = piped[:max_stdin_chars] + (
                f"\n[stdin truncated at {max_stdin_chars} characters]"
            )
    if positional and piped:
        return f"{positional}\n\n<stdin>\n{piped}\n</stdin>"
    return positional or piped


def cmd_repl(args) -> int:
    from .tui import run

    try:
        agent = _agent(args)
    except (OSError, ValueError) as exc:
        print(f"mjj: {exc}", file=sys.stderr)
        return 2
    return run(agent, args)


def cmd_auth(args) -> int:
    status = auth.describe()
    if args.probe:
        status["probe"] = probe(args.model, provider=args.provider)
    print(json.dumps(status, indent=2))
    return 0 if not args.probe or status["probe"].get("ok") else 1


def cmd_login(args) -> int:
    if args.login_provider == "chatgpt":
        try:
            return auth.login_chatgpt(device=args.device)
        except auth.AuthError as exc:
            print(f"mjj login: {exc}", file=sys.stderr)
            return 2
    try:
        key = getpass.getpass(f"{args.login_provider} API key: ").strip()
        if not key:
            print("mjj login: API key cannot be empty", file=sys.stderr)
            return 2
        path = auth.save_provider_key(args.login_provider, key)
    except (EOFError, KeyboardInterrupt, auth.AuthError) as exc:
        print(f"mjj login: {exc or 'cancelled'}", file=sys.stderr)
        return 2
    print(f"saved {args.login_provider} credential to {path}")
    return 0


def cmd_logout(args) -> int:
    if args.login_provider == "chatgpt":
        try:
            return auth.logout_chatgpt()
        except auth.AuthError as exc:
            print(f"mjj logout: {exc}", file=sys.stderr)
            return 2
    removed = auth.remove_provider_key(args.login_provider)
    print(("removed" if removed else "no saved") + f" {args.login_provider} credential")
    return 0


def cmd_tools(args) -> int:
    registry = build_registry(
        disabled=args.disabled_tools,
        skill_paths=args.skill_paths,
    )
    for schema in registry.schemas():
        print(f"{schema['name']:12} {schema['description'].splitlines()[0]}")
    return 0


def cmd_search(args) -> int:
    argv = [
        args.query,
        args.path,
        "--root", args.cwd,
        "--mode", args.mode,
        "--limit", str(args.limit),
    ]
    if args.regex:
        argv.append("--regex")
    if args.force:
        argv.append("--force")
    if args.json:
        argv.append("--json")
    if args.stats:
        argv.append("--stats")
    return search_main(argv)


def cmd_index(args) -> int:
    root = Path(args.root or args.cwd).resolve()
    try:
        index = build_index(root, force=args.force)
    except (OSError, ValueError) as exc:
        print(f"mjj index: {exc}", file=sys.stderr)
        return 2
    action = "wrote" if index.stats.wrote_index else "reused"
    print(
        f"{action} {index.index_path}: {index.stats.files} files, "
        f"{index.stats.chunks} chunks in {index.stats.elapsed_seconds * 1000:.1f} ms "
        f"({index.backend_name})"
    )
    return 0


def cmd_skills(args) -> int:
    skills = discover(Path(args.cwd), extra_paths=args.skill_paths)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": skill.name,
                        "qualified_name": skill.qualified_name,
                        "description": skill.description,
                        "path": str(skill.path),
                    }
                    for skill in skills
                ],
                indent=2,
            )
        )
        return 0
    if not skills:
        print("no skills found")
        return 0
    for skill in skills:
        print(f"{skill.qualified_name:28} {skill.description}")
    return 0


def cmd_config(args) -> int:
    values = args.resolved_config.public()
    values.update(
        provider=args.provider,
        model=args.model,
        effort=args.effort,
        verbosity=args.verbosity,
        tool_budget=args.tool_budget,
    )
    print(json.dumps(values, indent=2))
    return 0


def cmd_sessions(args) -> int:
    sessions = list_sessions(limit=args.limit)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": session.id,
                        "name": session.name or None,
                        "path": str(session.path),
                        "cwd": session.cwd,
                        "items": session.items,
                        "modified": session.modified,
                    }
                    for session in sessions
                ],
                indent=2,
            )
        )
    else:
        for session in sessions:
            print(session.summary())
    return 0


def cmd_export(args) -> int:
    try:
        path = export_session(args.session, args.output)
    except OSError as exc:
        print(f"mjj export: {exc}", file=sys.stderr)
        return 2
    print(path)
    return 0


def cmd_import(args) -> int:
    try:
        session, items = import_session(args.input)
    except (OSError, ValueError) as exc:
        print(f"mjj import: {exc}", file=sys.stderr)
        return 2
    session.close()
    print(f"imported {len(items)} items as session {session.id}")
    return 0


def cmd_visualize(args) -> int:
    try:
        result = generate_visualizer(
            args.output,
            cwd=args.cwd,
            kind=args.kind,
            palette=args.palette,
            seed=args.seed,
            title=args.title,
            image=args.image,
            force=args.force,
        )
    except VisualizerError as exc:
        print(f"mjj visualize: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.public(), indent=2) if args.json else result.summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("-C", "--cd", "--cwd", dest="cwd", default=".")
    bootstrap.add_argument("--config")
    known, _ = bootstrap.parse_known_args(argv)
    try:
        config = load_config(known.cwd, explicit=known.config)
    except ConfigError as exc:
        print(f"mjj: {exc}", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(prog="mjj")
    parser.add_argument("--version", action="version", version=f"mjj {__version__}")
    parser.add_argument("--provider", default=config.provider, choices=PROVIDERS)
    parser.add_argument("--model", default=config.model)
    parser.add_argument("--effort", default=config.effort, choices=EFFORTS)
    parser.add_argument("--verbosity", default=config.verbosity, choices=VERBOSITIES)
    parser.add_argument(
        "--permission-mode", default=config.permission_mode, choices=PERMISSION_MODES
    )
    parser.add_argument("--tool-budget", type=int, default=config.tool_budget)
    parser.add_argument(
        "--auto-next-steps", action="store_true", default=config.auto_next_steps
    )
    parser.add_argument(
        "--auto-next-idea", action="store_true", default=config.auto_next_idea
    )
    parser.add_argument("--auto-max-turns", type=int, default=config.auto_max_turns)
    parser.add_argument("-C", "--cd", "--cwd", dest="cwd", default=known.cwd)
    parser.add_argument("--config")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("exec", help="one headless run")
    run.add_argument("prompt", nargs="*")
    run.add_argument("--provider", choices=PROVIDERS, default=argparse.SUPPRESS)
    run.add_argument("--model", default=argparse.SUPPRESS)
    run.add_argument("--effort", choices=EFFORTS, default=argparse.SUPPRESS)
    run.add_argument("--verbosity", choices=VERBOSITIES, default=argparse.SUPPRESS)
    run.add_argument(
        "--permission-mode", choices=PERMISSION_MODES, default=argparse.SUPPRESS
    )
    run.add_argument("--auto-next-steps", action="store_true", default=argparse.SUPPRESS)
    run.add_argument("--auto-next-idea", action="store_true", default=argparse.SUPPRESS)
    run.add_argument("--auto-max-turns", type=int, default=argparse.SUPPRESS)
    run.add_argument(
        "-i", "--image", dest="images", action="append", default=[], metavar="PATH",
        help="attach an image (repeatable; sent as quality-85 WebP)",
    )
    run.add_argument(
        "-C", "--cd", "--cwd", dest="cwd", default=argparse.SUPPRESS
    )
    persistence = run.add_mutually_exclusive_group()
    persistence.add_argument("--resume", nargs="?", const="", default=None)
    persistence.add_argument("--fork", nargs="?", const="", default=None)
    persistence.add_argument("--ephemeral", action="store_true")
    run.add_argument("--name", help="set the session display name")
    run.add_argument("--json", action="store_true", help="emit JSONL events")
    run.add_argument(
        "-o", "--output-last-message", metavar="PATH", help="write final text to PATH"
    )
    run.set_defaults(func=cmd_exec)

    chat = sub.add_parser("chat", help="interactive session")
    chat.add_argument("--provider", choices=PROVIDERS, default=argparse.SUPPRESS)
    chat.add_argument("--model", default=argparse.SUPPRESS)
    chat.add_argument("--effort", choices=EFFORTS, default=argparse.SUPPRESS)
    chat.add_argument(
        "--permission-mode", choices=PERMISSION_MODES, default=argparse.SUPPRESS
    )
    chat.add_argument("--auto-next-steps", action="store_true", default=argparse.SUPPRESS)
    chat.add_argument("--auto-next-idea", action="store_true", default=argparse.SUPPRESS)
    chat.add_argument("--auto-max-turns", type=int, default=argparse.SUPPRESS)
    chat_persistence = chat.add_mutually_exclusive_group()
    chat_persistence.add_argument("--resume", nargs="?", const="", default=None)
    chat_persistence.add_argument("--fork", nargs="?", const="", default=None)
    chat.add_argument("--name")
    chat.set_defaults(func=cmd_repl)

    who = sub.add_parser("auth", help="credential status")
    who.add_argument("--probe", action="store_true", help="make one real call")
    who.set_defaults(func=cmd_auth)

    login = sub.add_parser("login", help="authenticate ChatGPT or save an API key")
    login.add_argument(
        "login_provider", choices=("chatgpt", "openpaths", "openrouter", "openai", "custom"),
        nargs="?", default="chatgpt",
    )
    login.add_argument(
        "--device", action="store_true", help="use ChatGPT device-code login"
    )
    login.set_defaults(func=cmd_login)

    logout = sub.add_parser("logout", help="remove a saved login")
    logout.add_argument(
        "login_provider", choices=("chatgpt", "openpaths", "openrouter", "openai", "custom"),
        nargs="?", default="chatgpt",
    )
    logout.set_defaults(func=cmd_logout)

    listing = sub.add_parser("tools", help="what the model can call")
    listing.set_defaults(func=cmd_tools)

    searching = sub.add_parser("search", help="hybrid disk search")
    searching.add_argument("query")
    searching.add_argument("path", nargs="?", default="")
    searching.add_argument("--mode", choices=["auto", "literal", "semantic"], default="auto")
    searching.add_argument("--regex", action="store_true")
    searching.add_argument("--limit", type=int, choices=range(1, 21), default=8)
    searching.add_argument("--force", action="store_true")
    searching.add_argument("--json", action="store_true")
    searching.add_argument("--stats", action="store_true")
    searching.set_defaults(func=cmd_search)

    indexing = sub.add_parser("index", help="build or refresh the disk search index")
    indexing.add_argument("root", nargs="?")
    indexing.add_argument("--force", action="store_true")
    indexing.set_defaults(func=cmd_index)

    skills = sub.add_parser("skills", help="list discovered SKILL.md workflows")
    skills.add_argument("--json", action="store_true")
    skills.set_defaults(func=cmd_skills)

    config_command = sub.add_parser("config", help="show resolved non-secret config")
    config_command.set_defaults(func=cmd_config)

    sessions = sub.add_parser("sessions", help="list saved sessions")
    sessions.add_argument("--limit", type=int, default=20)
    sessions.add_argument("--json", action="store_true")
    sessions.set_defaults(func=cmd_sessions)

    exporting = sub.add_parser("export", help="export a session to HTML or JSONL")
    exporting.add_argument("output")
    exporting.add_argument("--session")
    exporting.set_defaults(func=cmd_export)

    importing = sub.add_parser("import", help="import a JSONL session")
    importing.add_argument("input")
    importing.set_defaults(func=cmd_import)

    visual = sub.add_parser(
        "visualize", help="scaffold a deterministic standalone WebGL visualizer"
    )
    visual.add_argument("output", help="output directory inside the working tree")
    visual.add_argument("--kind", choices=KINDS, default="aurora")
    visual.add_argument("--palette", choices=PALETTES, default="ultraviolet")
    visual.add_argument("--seed", type=int, default=17)
    visual.add_argument("--title", default="Living signal")
    visual.add_argument("--image", help="optional source image; embedded as quality-85 WebP")
    visual.add_argument("--force", action="store_true")
    visual.add_argument("--json", action="store_true")
    visual.add_argument("-C", "--cd", "--cwd", dest="cwd", default=argparse.SUPPRESS)
    visual.set_defaults(func=cmd_visualize)

    args = parser.parse_args(argv)
    if args.auto_max_turns < 0:
        parser.error("--auto-max-turns must be non-negative")
    args.disabled_tools = config.disabled_tools
    args.skill_paths = config.skill_paths
    args.resolved_config = config
    if not getattr(args, "func", None):
        args.command = "chat"
        args.resume = None
        args.func = cmd_repl
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
