"""`mjj` — the command line.

Two modes that matter: ``mjj exec "..."`` for scripts and CI, and ``mjj`` for
an interactive session. Everything else is introspection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import auth
from .agent import Agent, render, render_exec
from .config import ConfigError, EFFORTS, VERBOSITIES, load as load_config
from .ledger import Budget, Ledger
from .model import ModelClient, probe
from .search.cli import main as search_main
from .search.index import build_index
from .session import Session, resume
from .skills import discover
from .tools import build_registry
from .version import __version__


def _agent(args) -> Agent:
    items: list[dict] = []
    if getattr(args, "resume", None) is not None:
        session, items = resume(args.resume or None)
    elif getattr(args, "ephemeral", False):
        session = None
    else:
        session = Session()
    client = ModelClient(
        model=args.model,
        effort=args.effort,
        verbosity=args.verbosity,
    )
    agent = Agent(
        registry=build_registry(
            disabled=args.disabled_tools,
            skill_paths=args.skill_paths,
        ),
        client=client,
        cwd=Path(args.cwd).resolve(),
        ledger=Ledger(Budget(default=args.tool_budget)),
        session=session,
        project_doc_max_bytes=args.resolved_config.project_doc_max_bytes,
    )
    agent.items = items
    return agent


def cmd_exec(args) -> int:
    agent = _agent(args)
    prompt = _exec_prompt(args.prompt)
    code, final_text = render_exec(
        agent.run(prompt),
        sys.stdout,
        sys.stderr,
        verbose=args.verbose,
        jsonl=args.json,
    )
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
    agent = _agent(args)
    print("mjj — ctrl-d to exit")
    while True:
        try:
            line = input("› ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line == "/usage":
            print(agent.client.usage.summary(), "·", agent.ledger.summary())
            continue
        try:
            render(agent.run(line), sys.stdout, verbose=args.verbose)
        except KeyboardInterrupt:
            print("\n[interrupted]")
    if agent.session:
        agent.session.close()
    return 0


def cmd_auth(args) -> int:
    status = auth.describe()
    if args.probe:
        status["probe"] = probe(args.model)
    print(json.dumps(status, indent=2))
    return 0 if not args.probe or status["probe"].get("ok") else 1


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
        model=args.model,
        effort=args.effort,
        verbosity=args.verbosity,
        tool_budget=args.tool_budget,
    )
    print(json.dumps(values, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--cwd", default=".")
    bootstrap.add_argument("--config")
    known, _ = bootstrap.parse_known_args(argv)
    try:
        config = load_config(known.cwd, explicit=known.config)
    except ConfigError as exc:
        print(f"mjj: {exc}", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(prog="mjj")
    parser.add_argument("--version", action="version", version=f"mjj {__version__}")
    parser.add_argument("--model", default=config.model)
    parser.add_argument("--effort", default=config.effort, choices=EFFORTS)
    parser.add_argument("--verbosity", default=config.verbosity, choices=VERBOSITIES)
    parser.add_argument("--tool-budget", type=int, default=config.tool_budget)
    parser.add_argument("--cwd", default=known.cwd)
    parser.add_argument("--config")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("exec", help="one headless run")
    run.add_argument("prompt", nargs="?")
    persistence = run.add_mutually_exclusive_group()
    persistence.add_argument("--resume", nargs="?", const="", default=None)
    persistence.add_argument("--ephemeral", action="store_true")
    run.add_argument("--json", action="store_true", help="emit JSONL events")
    run.add_argument(
        "-o", "--output-last-message", metavar="PATH", help="write final text to PATH"
    )
    run.set_defaults(func=cmd_exec)

    chat = sub.add_parser("chat", help="interactive session")
    chat.add_argument("--resume", nargs="?", const="", default=None)
    chat.set_defaults(func=cmd_repl)

    who = sub.add_parser("auth", help="credential status")
    who.add_argument("--probe", action="store_true", help="make one real call")
    who.set_defaults(func=cmd_auth)

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

    args = parser.parse_args(argv)
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
