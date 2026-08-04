"""Interactive terminal surface for ``mjj``.

It deliberately stays inline instead of taking over the alternate screen: the
terminal scrollback remains a useful, copyable session transcript. The composer
is cross-platform prompt-toolkit, so Windows gets the same history, completion,
multiline input and key bindings as Unix terminals.
"""

from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import prompt as secure_prompt

from . import auth
from .agent import Agent, Step, tool_progress
from .config import EFFORTS, PROVIDERS, VERBOSITIES
from .context_files import discover_project_files
from .media import ImageAttachment, ImageInputError, prepare_image
from .permissions import PermissionPolicy
from .session import (
    Session,
    export_session,
    fork_session,
    import_session,
    inspect_session,
    list_sessions,
    resume,
)
from .tools import build_registry


COMMANDS = {
    "/help": "show commands and keyboard shortcuts",
    "/model": "show or set the model",
    "/provider": "show or set auto/openpaths/openrouter/openai/custom",
    "/effort": "show or set reasoning effort",
    "/verbosity": "show or set response verbosity",
    "/image": "attach an image to the next prompt",
    "/images": "show queued image attachments",
    "/login": "sign in with ChatGPT or save a provider API key",
    "/logout": "remove a saved provider API key",
    "/auth": "show credential status without secrets",
    "/usage": "show model and tool token usage",
    "/models": "show models available for the active provider",
    "/settings": "show current provider, model, reasoning, and autonomy",
    "/status": "show model, permissions, session, tools, and Git status",
    "/permissions": "set auto, ask, or read-only tool permissions",
    "/init": "generate a repository-specific AGENTS.md",
    "/review": "review working-tree changes without editing",
    "/diff": "show the bounded current Git diff",
    "/auto": "set autonomy: off, steps, ideas, or full",
    "/session": "show current session information",
    "/history": "list recent sessions",
    "/resume": "resume a saved session by id or path",
    "/fork": "fork the current or a saved session",
    "/clone": "duplicate the current session into a new session",
    "/tree": "show conversation points or branch from an item number",
    "/name": "set the current session display name",
    "/export": "export the session to HTML or JSONL",
    "/import": "import and resume a JSONL session",
    "/copy": "copy the last assistant response with OSC 52",
    "/reload": "reload tools and discovered skills",
    "/hotkeys": "show keyboard shortcuts",
    "/clear": "clear the terminal",
    "/new": "start a fresh conversation",
    "/exit": "leave mjj",
    "/quit": "leave mjj",
}

MODEL_PRESETS = {
    "auto": ("auto",),
    "openai": ("auto", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"),
    "openpaths": ("auto", "openpaths/auto-code", "openpaths/auto", "openpaths/auto-hard"),
    "openrouter": ("auto", "openrouter/auto"),
    "custom": ("auto",),
}


_AT_QUERY = re.compile(r"(?:^|\s)@([^\s]*)$")


class WorkspaceCompleter(Completer):
    def __init__(self, cwd: str | Path):
        self.files = discover_project_files(cwd)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for command, description in COMMANDS.items():
                if command.startswith(text):
                    yield Completion(
                        command,
                        start_position=-len(text),
                        display_meta=description,
                    )
            return
        match = _AT_QUERY.search(text)
        if not match:
            return
        query = match.group(1).lower()
        matches = [path for path in self.files if query in path.lower()]
        matches.sort(key=lambda path: (not path.lower().startswith(query), len(path), path))
        for path in matches[:50]:
            if " " in path:
                path = f'"{path}"'
            yield Completion(path, start_position=-len(match.group(1)), display_meta="file")


class SlashCompleter(WorkspaceCompleter):
    """Compatibility name retained for callers of the original completer."""

    def __init__(self, cwd: str | Path = "."):
        super().__init__(cwd)


@dataclass
class InteractiveApp:
    agent: Agent
    args: object
    attachments: list[ImageAttachment] = field(default_factory=list)
    done: bool = False

    def __post_init__(self) -> None:
        self.permission_policy = PermissionPolicy(
            getattr(self.args, "permission_mode", "auto"),
            prompt=lambda message: secure_prompt(message),
        )
        self.agent.approve = self.permission_policy
        self.agent.ctx.approve = self.permission_policy
        self.bindings = self._bindings()
        history = auth.mjj_home() / "history"
        history.parent.mkdir(parents=True, exist_ok=True)
        self.session = PromptSession(
            history=FileHistory(str(history)),
            completer=WorkspaceCompleter(self.agent.cwd),
            complete_while_typing=True,
            key_bindings=self.bindings,
            bottom_toolbar=self._toolbar,
            reserve_space_for_menu=8,
            multiline=False,
        )

    @property
    def provider(self) -> str:
        return self.agent.client.provider

    def _bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("left")
        def left(event) -> None:
            if not event.current_buffer.text:
                self._cycle_effort(-1)
                event.app.invalidate()
            else:
                event.current_buffer.cursor_left()

        @bindings.add("right")
        def right(event) -> None:
            buffer = event.current_buffer
            if not buffer.text:
                self._cycle_effort(1)
                event.app.invalidate()
            else:
                buffer.cursor_right()

        @bindings.add("s-up")
        def shift_up(event) -> None:
            self._cycle_model(1)
            event.app.invalidate()

        @bindings.add("s-down")
        def shift_down(event) -> None:
            self._cycle_model(-1)
            event.app.invalidate()

        @bindings.add("escape", "enter")
        def newline(event) -> None:
            event.current_buffer.insert_text("\n")

        return bindings

    def _cycle_effort(self, direction: int) -> None:
        current = self.agent.client.effort
        index = EFFORTS.index(current) if current in EFFORTS else 0
        self.agent.client.effort = EFFORTS[(index + direction) % len(EFFORTS)]

    def _cycle_model(self, direction: int) -> None:
        models = MODEL_PRESETS.get(self.provider, ("auto",))
        current = self.agent.client.model
        index = models.index(current) if current in models else 0
        self.agent.client.model = models[(index + direction) % len(models)]

    def _toolbar(self):
        images = f" · {len(self.attachments)} image" if self.attachments else ""
        cwd = Path(self.agent.cwd).name or str(self.agent.cwd)
        autonomy = self._autonomy_label()
        auto = f" · auto {autonomy}" if autonomy != "off" else ""
        return HTML(
            " <b>mjj</b> · "
            f"{html.escape(self.provider)}/{html.escape(self.agent.client.model)} · "
            f"reasoning <b>{html.escape(self.agent.client.effort)}</b>{auto}{images} · "
            f"{html.escape(cwd)}   ←/→ effort · ⇧↑/↓ model · / commands "
        )

    def run(self) -> int:
        self._welcome()
        while not self.done:
            try:
                line = self.session.prompt(HTML("<ansicyan>›</ansicyan> ")).strip()
            except EOFError:
                break
            except KeyboardInterrupt:
                print_formatted_text(ANSI("\x1b[2m(interrupted input)\x1b[0m"))
                continue
            if not line:
                continue
            if line.startswith("/"):
                self.command(line)
                continue
            if line.startswith("!"):
                self._shell(line)
                continue
            self.turn(line)
        if self.agent.session:
            self.agent.session.close()
        return 0

    def _welcome(self) -> None:
        print_formatted_text(
            ANSI(
                "\x1b[38;5;45m╭─ mjj\x1b[0m  coding agent\n"
                f"\x1b[38;5;45m│\x1b[0m  {self.provider}/{self.agent.client.model}"
                f"  ·  reasoning {self.agent.client.effort}\n"
                "\x1b[38;5;45m╰─\x1b[0m  Type / for commands; Alt+Enter inserts a newline."
            )
        )

    def turn(self, text: str) -> None:
        images = tuple(self.attachments)
        self.attachments.clear()
        print_formatted_text(ANSI("\x1b[2m◆ working · ctrl-c interrupts\x1b[0m"))
        try:
            self._render(
                self.agent.run(
                    text,
                    images=images,
                    auto_next_steps=self.args.auto_next_steps,
                    auto_next_idea=self.args.auto_next_idea,
                    max_autonomous_turns=self.args.auto_max_turns,
                )
            )
        except KeyboardInterrupt:
            print_formatted_text(ANSI("\x1b[33m■ interrupted\x1b[0m"))

    def _render(self, steps) -> None:
        text_open = False
        for step in steps:
            if step.kind == "text":
                if not text_open:
                    print_formatted_text(ANSI("\x1b[38;5;45m●\x1b[0m "), end="")
                    text_open = True
                print(step.text, end="", flush=True)
            elif step.kind == "tool_call":
                if text_open:
                    print()
                    text_open = False
                label = _tool_label(step)
                print_formatted_text(ANSI(f"\x1b[2m  ↳ {label}\x1b[0m"))
            elif step.kind == "tool_result" and not step.meta.get("ok", True):
                print_formatted_text(ANSI(f"\x1b[31m    {step.text}\x1b[0m"))
            elif step.kind == "autonomous":
                if text_open:
                    print()
                    text_open = False
                print_formatted_text(
                    ANSI(
                        f"\x1b[38;5;45m  ↻ autonomous continuation "
                        f"{step.meta.get('turn', 0)}\x1b[0m"
                    )
                )
            elif step.kind == "error":
                if text_open:
                    print()
                    text_open = False
                print_formatted_text(ANSI(f"\x1b[31merror: {step.text}\x1b[0m"))
        if text_open:
            print()

    def command(self, line: str) -> None:
        command, _, value = line.partition(" ")
        value = value.strip()
        if command in ("/exit", "/quit"):
            self.done = True
        elif command == "/help":
            for name, description in COMMANDS.items():
                print(f"{name:12} {description}")
        elif command == "/effort":
            self._set_choice("effort", value, EFFORTS)
        elif command == "/verbosity":
            self._set_choice("verbosity", value, VERBOSITIES)
        elif command == "/model":
            if value:
                self.agent.client.model = value
            print(f"model: {self.agent.client.model}")
        elif command == "/provider":
            if value:
                if value not in PROVIDERS:
                    print("provider must be one of: " + ", ".join(PROVIDERS))
                    return
                self.agent.client.provider = value
                self.agent.client.resolver = auth.CredentialResolver(provider=value)
            print(f"provider: {self.provider}")
        elif command == "/image":
            self._attach(value)
        elif command == "/images":
            print("\n".join(image.summary() for image in self.attachments) or "no queued images")
        elif command == "/login":
            self._login(value or (self.provider if self.provider != "auto" else "chatgpt"))
        elif command == "/logout":
            provider = value or (self.provider if self.provider != "auto" else "chatgpt")
            if provider == "chatgpt":
                try:
                    code = auth.logout_chatgpt()
                except auth.AuthError as exc:
                    print(f"logout: {exc}")
                    return
                print("ChatGPT signed out" if code == 0 else f"ChatGPT logout failed ({code})")
            else:
                print("removed" if auth.remove_provider_key(provider) else "no saved key", provider)
        elif command == "/auth":
            print(json.dumps(auth.describe(), indent=2))
        elif command == "/usage":
            print(self.agent.client.usage.summary(), "·", self.agent.ledger.summary())
        elif command == "/models":
            print("\n".join(MODEL_PRESETS.get(self.provider, ("auto",))))
        elif command == "/settings":
            print(
                json.dumps(
                    {
                        "provider": self.provider,
                        "model": self.agent.client.model,
                        "effort": self.agent.client.effort,
                        "verbosity": self.agent.client.verbosity,
                        "permission_mode": self.permission_policy.mode,
                        "autonomy": self._autonomy_label(),
                        "auto_max_turns": self.args.auto_max_turns,
                    },
                    indent=2,
                )
            )
        elif command == "/status":
            self._status()
        elif command == "/permissions":
            if value:
                try:
                    self.permission_policy.set(value)
                except ValueError as exc:
                    print(exc)
                    return
                self.args.permission_mode = value
            print(f"permissions: {self.permission_policy.mode}")
        elif command == "/init":
            self._init()
        elif command == "/review":
            focus = f"\nAdditional review focus: {value}" if value else ""
            self.turn(
                "Review the current working tree. Find correctness, security, and "
                "regression risks plus missing tests. Report findings in severity order "
                "with precise file and line references. Do not modify files."
                + focus
            )
        elif command == "/diff":
            self._diff()
        elif command == "/auto":
            self._set_autonomy(value)
        elif command == "/session":
            self._show_session()
        elif command == "/history":
            self._show_history()
        elif command == "/resume":
            self._resume(value)
        elif command == "/fork":
            self._fork(value)
        elif command == "/clone":
            self._fork("")
        elif command == "/tree":
            self._tree(value)
        elif command == "/name":
            self._name(value)
        elif command == "/export":
            self._export(value)
        elif command == "/import":
            self._import(value)
        elif command == "/copy":
            self._copy_last()
        elif command == "/reload":
            self.agent.registry = build_registry(
                disabled=self.args.disabled_tools,
                skill_paths=self.args.skill_paths,
            )
            print("reloaded tools and skills")
        elif command == "/hotkeys":
            print(
                "←/→ effort · Shift+↑/↓ model · Alt+Enter newline · "
                "@ file · ! shell+context · !! shell-only · Ctrl+C interrupt"
            )
        elif command == "/clear":
            print("\x1b[2J\x1b[H", end="")
        elif command == "/new":
            self._new_session()
        else:
            print(f"unknown command {command}; type /help")

    def _shell(self, line: str) -> None:
        excluded = line.startswith("!!")
        command = line[2 if excluded else 1 :].strip()
        if not command:
            print("usage: !COMMAND (send output to model) or !!COMMAND (local only)")
            return
        result = self.agent.registry.dispatch(
            "shell",
            json.dumps({"command": command, "shell": True}),
            self.agent.ctx,
        )
        print(f"\x1b[2m  ↳ ! {command}\x1b[0m")
        print(result.output)
        if not excluded:
            self.agent.user(
                "<local_shell>\n"
                f"<command>{command}</command>\n"
                f"<output ok=\"{str(result.ok).lower()}\">\n{result.output}\n</output>\n"
                "</local_shell>"
            )

    def _status(self) -> None:
        session = inspect_session(self.agent.session.path) if self.agent.session else None
        git = self.agent.registry.dispatch(
            "shell",
            json.dumps({"command": ["git", "status", "--short", "--branch"]}),
            self.agent.ctx,
        )
        status = {
            "cwd": str(self.agent.cwd),
            "provider": self.provider,
            "model": self.agent.client.model,
            "effort": self.agent.client.effort,
            "verbosity": self.agent.client.verbosity,
            "permissions": self.permission_policy.mode,
            "autonomy": self._autonomy_label(),
            "session": session.id if session else "ephemeral",
            "transcript_items": len(self.agent.items),
            "tools": sorted(self.agent.registry.tools),
            "project_docs": [
                str(path) for path in self.agent.project_instructions.sources
            ],
            "usage": self.agent.client.usage.summary(),
            "tool_usage": self.agent.ledger.summary(),
            "git": git.output,
        }
        print(json.dumps(status, indent=2))

    def _init(self) -> None:
        target = Path(self.agent.cwd) / "AGENTS.md"
        if target.exists():
            print(f"AGENTS.md already exists: {target}")
            return
        self.turn(
            "Inspect this repository and create a concise AGENTS.md in the current "
            "directory. Document its real layout, coding conventions, focused test "
            "commands, and validation expectations. Do not invent commands; verify "
            "them from repository files before writing the scaffold."
        )

    def _diff(self) -> None:
        commands = (
            ("status", ["git", "status", "--short"]),
            ("unstaged", ["git", "diff", "--no-ext-diff", "--"]),
            ("staged", ["git", "diff", "--cached", "--no-ext-diff", "--"]),
        )
        for label, command in commands:
            result = self.agent.registry.dispatch(
                "shell", json.dumps({"command": command}), self.agent.ctx
            )
            print(f"--- {label} ---")
            print(result.output or "(none)")

    def _set_choice(self, name: str, value: str, choices: tuple[str, ...]) -> None:
        current = getattr(self.agent.client, name)
        if value:
            if value not in choices:
                print(f"{name} must be one of: {', '.join(choices)}")
                return
            setattr(self.agent.client, name, value)
            current = value
        print(f"{name}: {current}")

    def _attach(self, value: str) -> None:
        if not value:
            print("usage: /image PATH")
            return
        try:
            image = prepare_image(Path(self.agent.cwd) / value)
        except ImageInputError as exc:
            print(f"image: {exc}")
            return
        self.attachments.append(image)
        print("attached", image.summary())

    def _login(self, provider: str) -> None:
        if provider in ("chatgpt", "device"):
            try:
                code = auth.login_chatgpt(device=provider == "device")
            except auth.AuthError as exc:
                print(f"login: {exc}")
                return
            print("ChatGPT sign-in complete" if code == 0 else f"ChatGPT sign-in failed ({code})")
            return
        if provider not in ("openpaths", "openrouter", "openai", "custom"):
            print("login provider must be chatgpt, device, openpaths, openrouter, openai or custom")
            return
        try:
            key = secure_prompt(f"{provider} API key: ", is_password=True).strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not key:
            return
        path = auth.save_provider_key(provider, key)
        self.agent.client.provider = provider
        self.agent.client.resolver = auth.CredentialResolver(provider=provider)
        print(f"saved {provider} credential to {path}")

    def _new_session(self) -> None:
        if self.agent.session:
            self.agent.session.close()
        self.agent.session = Session(meta={"cwd": str(self.agent.cwd)})
        self.agent.items.clear()
        self.agent.client.cache_key = f"mjj-{self.agent.session.id}"
        print(f"new session {self.agent.session.id}")

    def _autonomy_label(self) -> str:
        if self.args.auto_next_steps and self.args.auto_next_idea:
            return "full"
        if self.args.auto_next_steps:
            return "steps"
        if self.args.auto_next_idea:
            return "ideas"
        return "off"

    def _set_autonomy(self, value: str) -> None:
        if not value:
            print(
                f"autonomy: {self._autonomy_label()} · max turns: "
                f"{self.args.auto_max_turns or 'unlimited'}"
            )
            return
        mode, _, raw_limit = value.partition(" ")
        if mode not in ("off", "steps", "ideas", "full"):
            print("usage: /auto off|steps|ideas|full [max-turns]")
            return
        if raw_limit:
            try:
                limit = int(raw_limit)
            except ValueError:
                print("max-turns must be a non-negative integer")
                return
            if limit < 0:
                print("max-turns must be a non-negative integer")
                return
            self.args.auto_max_turns = limit
        self.args.auto_next_steps = mode in ("steps", "full")
        self.args.auto_next_idea = mode in ("ideas", "full")
        print(f"autonomy: {self._autonomy_label()}")

    def _show_session(self) -> None:
        if not self.agent.session:
            print("ephemeral session")
            return
        info = inspect_session(self.agent.session.path)
        print(
            json.dumps(
                {
                    "id": info.id,
                    "name": info.name or None,
                    "path": str(info.path),
                    "cwd": info.cwd,
                    "items": info.items,
                },
                indent=2,
            )
        )

    def _show_history(self) -> None:
        sessions = list_sessions(limit=20)
        print("\n".join(session.summary() for session in sessions) or "no sessions")

    def _resume(self, value: str) -> None:
        if not value:
            self._show_history()
            print("usage: /resume ID|PATH")
            return
        try:
            session, items = resume(value)
        except (OSError, ValueError) as exc:
            print(f"resume: {exc}")
            return
        self._switch_session(session, items)
        print(f"resumed session {session.id}")

    def _fork(self, value: str) -> None:
        source = value or (str(self.agent.session.path) if self.agent.session else None)
        try:
            session, items = fork_session(source)
        except (OSError, ValueError) as exc:
            print(f"fork: {exc}")
            return
        self._switch_session(session, items)
        print(f"forked session {session.id}")

    def _tree(self, value: str) -> None:
        points = [
            (index + 1, item)
            for index, item in enumerate(self.agent.items)
            if item.get("type") == "message"
        ]
        if not value:
            if not points:
                print("empty session")
                return
            for index, item in points:
                role = str(item.get("role") or "message")
                preview = _message_text(item).replace("\n", " ")[:80]
                print(f"{index:4} {role:9} {preview}")
            print("use /tree ITEM to branch after that transcript item")
            return
        if not self.agent.session:
            print("ephemeral session cannot be branched")
            return
        try:
            through = int(value)
            session, items = fork_session(self.agent.session.path, through=through)
        except (OSError, ValueError) as exc:
            print(f"tree: {exc}")
            return
        self._switch_session(session, items)
        print(f"branched at item {through} into session {session.id}")

    def _switch_session(self, session: Session, items: list[dict]) -> None:
        if self.agent.session:
            self.agent.session.close()
        self.agent.session = session
        self.agent.items = items
        self.agent.client.cache_key = f"mjj-{session.id}"

    def _name(self, value: str) -> None:
        if not self.agent.session:
            print("ephemeral session cannot be named")
            return
        if not value:
            print("usage: /name DISPLAY NAME")
            return
        self.agent.session.note(name=value)
        print(f"session name: {value}")

    def _export(self, value: str) -> None:
        if not self.agent.session:
            print("ephemeral session cannot be exported")
            return
        destination = value or str(self.agent.cwd / f"mjj-{self.agent.session.id}.html")
        try:
            path = export_session(self.agent.session.path, destination)
        except OSError as exc:
            print(f"export: {exc}")
            return
        print(f"exported {path}")

    def _import(self, value: str) -> None:
        if not value:
            print("usage: /import PATH.jsonl")
            return
        try:
            session, items = import_session(Path(self.agent.cwd) / value)
        except (OSError, ValueError) as exc:
            print(f"import: {exc}")
            return
        self._switch_session(session, items)
        print(f"imported {len(items)} items as session {session.id}")

    def _copy_last(self) -> None:
        text = _last_assistant_text(self.agent.items)
        if not text:
            print("no assistant response to copy")
            return
        encoded = base64.b64encode(text[:100_000].encode("utf-8")).decode("ascii")
        print(f"\x1b]52;c;{encoded}\x07", end="")
        print("copied last assistant response")


def _tool_label(step: Step) -> str:
    return tool_progress(step)


def _message_text(item: dict) -> str:
    return "".join(
        str(part.get("text") or "[image]")
        for part in item.get("content") or []
        if isinstance(part, dict)
    )


def _last_assistant_text(items: list[dict]) -> str:
    for item in reversed(items):
        if item.get("type") == "message" and item.get("role") == "assistant":
            return _message_text(item)
    return ""


def run(agent: Agent, args) -> int:
    return InteractiveApp(agent, args).run()
