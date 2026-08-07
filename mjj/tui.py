"""Interactive terminal surface for ``mjj``.

It deliberately stays inline instead of taking over the alternate screen: the
terminal scrollback remains a useful, copyable session transcript. The optional
prompt-toolkit composer gives Windows and Unix the same history, completion,
multiline input, and key bindings. A dependency-free line composer keeps the
agent usable in minimal installations.
"""

from __future__ import annotations

import base64
import asyncio
import getpass
import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    if os.environ.get("MJJ_TUI", "").lower() == "basic":
        raise ImportError("basic TUI requested")
    from prompt_toolkit import PromptSession, print_formatted_text
    from prompt_toolkit.application import Application
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import ANSI, HTML, FormattedText
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.shortcuts import prompt as secure_prompt
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style

    RICH_TUI = True
except ImportError:
    RICH_TUI = False

    class Completer:
        pass

    @dataclass(frozen=True)
    class Completion:
        text: str
        start_position: int = 0
        display_meta: str = ""

    class _Text(str):
        pass

    ANSI = HTML = _Text

    def print_formatted_text(value, *, end="\n") -> None:
        print(str(value), end=end)

    class _KeyBindings:
        def __init__(self) -> None:
            self.bindings = []

        def add(self, *keys, **_kwargs):
            def register(function):
                self.bindings.append(type("Binding", (), {"keys": keys})())
                return function
            return register

    KeyBindings = _KeyBindings

    def Condition(function):
        return function

    class FileHistory:
        def __init__(self, filename: str) -> None:
            self.filename = filename

        def append_string(self, string: str) -> None:
            pass

    class InMemoryHistory(FileHistory):
        def __init__(self) -> None:
            super().__init__(":memory:")

    class DummyInput:
        pass

    class DummyOutput:
        pass

    class PromptSession:
        def __init__(self, **kwargs) -> None:
            self.history = kwargs.get("history")

        def prompt(self, message) -> str:
            rendered = re.sub(r"<[^>]+>", "", str(message))
            return input(rendered)

    def secure_prompt(message: str, *, is_password: bool = False) -> str:
        return getpass.getpass(message) if is_password else input(message)

from . import auth
from .agent import Agent, Step, tool_progress
from .config import EFFORTS, PROVIDERS, VERBOSITIES, persist_user_agent_settings
from .context_files import IMAGE_SUFFIXES, discover_project_files
from .goals import GoalStore
from .media import ImageAttachment, ImageInputError, prepare_image
from .model_routes import AUTO_MODEL_IDS, describe_model
from .permissions import PERMISSION_MODES, PermissionPolicy
from .prompt_cache import CACHE_MODES
from .session import (
    Session,
    export_session,
    fork_session,
    import_session,
    inspect_session,
    list_sessions,
    resume,
)
from .terminal_images import render_terminal_image, terminal_image_protocol
from .tools import build_registry


COMMANDS = {
    "/help": "show commands and keyboard shortcuts",
    "/commands": "alias for /help",
    "/tips": "show the short interactive workflow guide",
    "/model": "choose a model with arrows or set it by name",
    "/provider": "show or set auto/deepseek/openpaths/openrouter/openai/custom",
    "/effort": "show or set reasoning effort",
    "/reasoning": "alias for /effort",
    "/verbosity": "show or set response verbosity",
    "/image": "attach an image to the next prompt",
    "/images": "show queued image attachments",
    "/preview": "show an image in the terminal without attaching it",
    "/login": "sign in with ChatGPT or save a provider API key",
    "/logout": "remove a saved provider API key",
    "/auth": "show credential status without secrets",
    "/usage": "show model and tool token usage",
    "/cache": "show or set automatic prompt caching",
    "/models": "show models available for the active provider",
    "/settings": "show current provider, model, reasoning, and autonomy",
    "/status": "show model, permissions, session, tools, and Git status",
    "/permissions": "set auto, ask, or read-only tool permissions",
    "/init": "generate a repository-specific AGENTS.md",
    "/review": "review working-tree changes without editing",
    "/diff": "show the bounded current Git diff",
    "/undo": "restore the latest conflict-free patch checkpoint",
    "/checkpoints": "list recent automatic patch checkpoints",
    "/auto": "compatibility alias for /loop",
    "/loop": "repeat steps or ideas; 'forever' runs until interrupted",
    "/goal": "inspect, set, pause, resume, complete, or clear a durable goal",
    "/plan": "plan read-only, then explicitly approve implementation",
    "/compact": "request server-side conversation compaction now",
    "/ps": "list background shell jobs",
    "/stop": "stop running background shell jobs",
    "/mcp": "show configured MCP tools and startup warnings",
    "/skills": "list discovered reusable workflows",
    "/plugins": "list installed and enabled tool plugins",
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
    "/raw": "toggle full tool output in the inline transcript",
    "/rollout": "print the append-only session transcript path",
    "/reload": "reload tools and discovered skills",
    "/hotkeys": "show keyboard shortcuts",
    "/keys": "alias for /hotkeys",
    "/clear": "clear the terminal",
    "/new": "start a fresh conversation",
    "/exit": "leave mjj",
    "/quit": "leave mjj",
}

MODEL_PRESETS = {
    "auto": ("auto",),
    "openai": (
        "auto",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.3-codex",
        *AUTO_MODEL_IDS,
    ),
    "deepseek": ("deepseek-v4-flash", "deepseek-v4-pro"),
    "openpaths": (
        "auto",
        "grok-4.5",
        "openpaths/auto-code",
        "openpaths/auto",
        "openpaths/auto-hard",
        *AUTO_MODEL_IDS,
    ),
    "openrouter": ("auto", "x-ai/grok-4.5", "openrouter/auto", *AUTO_MODEL_IDS),
    "custom": ("auto",),
}

VALUE_CHOICES = {
    "/provider": PROVIDERS,
    "/effort": EFFORTS,
    "/reasoning": EFFORTS,
    "/verbosity": VERBOSITIES,
    "/cache": CACHE_MODES,
    "/permissions": PERMISSION_MODES,
    "/auto": ("off", "steps", "ideas", "full", "forever"),
    "/loop": ("off", "steps", "ideas", "full", "forever"),
    "/goal": ("pause", "resume", "complete", "blocked", "clear", "set"),
    "/plan": ("approve", "cancel", "clear"),
    "/raw": ("on", "off", "toggle"),
    "/login": ("chatgpt", "device", "deepseek", "openpaths", "openrouter", "openai", "custom"),
    "/logout": ("chatgpt", "deepseek", "openpaths", "openrouter", "openai", "custom"),
}


_AT_QUERY = re.compile(r"(?:^|\s)@([^\s]*)$")
MAX_HISTORY_ENTRY_CHARS = 65_536
MAX_HISTORY_BYTES = 2 << 20


def _workspace_history_path(cwd: str | Path) -> Path:
    """Return a private prompt-history file scoped to one resolved directory."""
    resolved = Path(cwd).expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:20]
    # Older releases used $MJJ_HOME/history as one global file. Keep it intact
    # and use a new directory so upgrading cannot turn that file into an error.
    directory = auth.mjj_home() / "prompt-history"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        directory.chmod(0o700)
    path = directory / f"{digest}.txt"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(descriptor)
    if os.name != "nt":
        path.chmod(0o600)
    return path


class DistinctFileHistory(FileHistory):
    """Avoid consecutive duplicate prompts without changing history format."""

    def __init__(self, filename: str) -> None:
        super().__init__(filename)
        self._path = Path(filename)
        self._last_appended: str | None = None

    def append_string(self, string: str) -> None:
        if string == self._last_appended:
            return
        self._last_appended = string
        if len(string) > MAX_HISTORY_ENTRY_CHARS:
            return
        try:
            super().append_string(string)
            self._trim()
        except OSError:
            # History is optional UI state. A full disk must not end the agent.
            return

    def _trim(self) -> None:
        if self._path.stat().st_size <= MAX_HISTORY_BYTES:
            return
        content = self._path.read_bytes()
        tail = content[-MAX_HISTORY_BYTES:]
        boundary = tail.find(b"\n# ")
        tail = tail[boundary + 1 :] if boundary >= 0 else b""
        temporary = self._path.with_suffix(f".{os.getpid()}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(tail)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _history_for_workspace(
    cwd: str | Path,
) -> tuple[FileHistory | InMemoryHistory, str]:
    try:
        return DistinctFileHistory(str(_workspace_history_path(cwd))), ""
    except OSError as exc:
        detail = str(exc).replace("\n", " ")[:160]
        return InMemoryHistory(), f"prompt history is in-memory: {detail}"


def _model_choices(provider: str) -> tuple[str, ...]:
    if provider != "auto":
        return MODEL_PRESETS.get(provider, ("auto",))
    return tuple(
        dict.fromkeys(
            (
                "auto",
                *AUTO_MODEL_IDS,
                *(
                    model
                    for models in MODEL_PRESETS.values()
                    for model in models
                ),
            )
        )
    )


_MODEL_DESCRIPTIONS = {
    "auto": "choose the best available route",
    "gpt-5.6-sol": "capability-first OpenAI coding",
    "gpt-5.6-terra": "balanced OpenAI coding",
    "gpt-5.6-luna": "fast, lower-cost OpenAI coding",
    "gpt-5.3-codex": "previous-generation Codex",
    "grok-4.5": "Grok coding through OpenPaths",
    "x-ai/grok-4.5": "Grok coding through OpenRouter",
    "openpaths/auto": "OpenPaths automatic routing",
    "openpaths/auto-code": "OpenPaths coding router",
    "openpaths/auto-hard": "OpenPaths hard-task router",
    "openrouter/auto": "OpenRouter automatic routing",
}


def _model_description(model: str) -> str:
    return describe_model(model) or _MODEL_DESCRIPTIONS.get(model, "custom model ID")


def _pick_model(
    models: tuple[str, ...],
    current: str,
    provider: str,
    *,
    input_stream=None,
    output=None,
) -> str | None:
    """Render a small inline picker and return the confirmed model."""
    choices = list(models)
    if current not in choices:
        choices.insert(0, current)
    selected = choices.index(current)
    visible_rows = min(7, len(choices))

    def fragments():
        start = max(0, min(selected - visible_rows // 2, len(choices) - visible_rows))
        end = min(len(choices), start + visible_rows)
        result = [
            ("class:title", f"  Choose model · provider {provider}\n"),
            ("class:hint", "  ↑/↓ move · Enter select · Esc cancel\n\n"),
        ]
        if start:
            result.append(("class:hint", f"    ↑ {start} more\n"))
        for index in range(start, end):
            model = choices[index]
            active = index == selected
            marker = "›" if active else " "
            current_marker = "  current" if model == current else ""
            style = "class:selected" if active else "class:model"
            result.extend(
                [
                    (style, f"  {marker} {model}"),
                    ("class:current", current_marker),
                    ("class:description", f"\n      {_model_description(model)}\n"),
                ]
            )
        if end < len(choices):
            result.append(("class:hint", f"    ↓ {len(choices) - end} more\n"))
        return FormattedText(result)

    bindings = KeyBindings()

    @bindings.add("up")
    @bindings.add("s-up")
    def move_up(event) -> None:
        nonlocal selected
        selected = (selected - 1) % len(choices)
        event.app.invalidate()

    @bindings.add("down")
    @bindings.add("s-down")
    def move_down(event) -> None:
        nonlocal selected
        selected = (selected + 1) % len(choices)
        event.app.invalidate()

    @bindings.add("home")
    def move_first(event) -> None:
        nonlocal selected
        selected = 0
        event.app.invalidate()

    @bindings.add("end")
    def move_last(event) -> None:
        nonlocal selected
        selected = len(choices) - 1
        event.app.invalidate()

    @bindings.add("enter")
    def accept(event) -> None:
        event.app.exit(result=choices[selected])

    @bindings.add("escape")
    @bindings.add("c-c")
    def cancel(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(fragments)
    application_io = {}
    if input_stream is not None:
        application_io["input"] = input_stream
    if output is not None:
        application_io["output"] = output
    picker = Application(
        layout=Layout(Window(control, height=visible_rows * 2 + 5)),
        key_bindings=bindings,
        style=Style.from_dict(
            {
                "title": "bold #2de2cf",
                "hint": "#777e9e",
                "model": "#d7daea",
                "selected": "bold #ffffff bg:#34305f",
                "current": "#2de2cf",
                "description": "#777e9e",
            }
        ),
        full_screen=False,
        erase_when_done=True,
        **application_io,
    )
    return picker.run()


class WorkspaceCompleter(Completer):
    def __init__(
        self,
        cwd: str | Path,
        *,
        provider: str | Callable[[], str] = "auto",
    ):
        self.cwd = Path(cwd)
        self._files: tuple[str, ...] | None = None
        self._provider = provider

    @property
    def files(self) -> tuple[str, ...]:
        # Normal typing and slash completion do not need a repository walk.
        # Defer it until the first @file or image completion request.
        if self._files is None:
            self._files = discover_project_files(self.cwd)
        return self._files

    @property
    def provider(self) -> str:
        return self._provider() if callable(self._provider) else self._provider

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            query = text[1:].lower()
            prefix_matches = [
                (command, description)
                for command, description in COMMANDS.items()
                if command.startswith(text)
            ]
            matches = prefix_matches or [
                (command, description)
                for command, description in COMMANDS.items()
                if query in command[1:].lower() or query in description.lower()
            ]
            matches.sort(
                key=lambda item: (
                    not item[0].startswith(text),
                    len(item[0]),
                    item[0],
                )
            )
            for command, description in matches:
                yield Completion(
                    command,
                    start_position=-len(text),
                    display_meta=description,
                )
            return
        command, separator, raw = text.partition(" ")
        if separator and (command == "/model" or command in VALUE_CHOICES):
            choices = (
                (*_model_choices(self.provider), "next", "prev")
                if command == "/model"
                else (*VALUE_CHOICES[command], "next", "prev")
                if command in {"/provider", "/effort", "/reasoning", "/verbosity"}
                else VALUE_CHOICES[command]
            )
            query = raw.lower()
            for choice in choices:
                if query in choice.lower():
                    yield Completion(
                        choice,
                        start_position=-len(raw),
                        display_meta=command[1:],
                    )
            return
        if text.startswith(("/image ", "/preview ")):
            raw = text.partition(" ")[2]
            query = raw.strip("\"'").lower()
            matches = [
                path
                for path in self.files
                if Path(path).suffix.lower() in IMAGE_SUFFIXES and query in path.lower()
            ]
            matches.sort(
                key=lambda path: (
                    not path.lower().startswith(query),
                    len(path),
                    path,
                )
            )
            for path in matches[:50]:
                rendered = f'"{path}"' if " " in path else path
                yield Completion(
                    rendered,
                    start_position=-len(raw),
                    display_meta="image",
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
    activity: list[Step] = field(default_factory=list, repr=False)
    transcript_expanded: bool = False
    pending_prompts: list[str] = field(default_factory=list, repr=False)
    queue_submission: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.permission_policy = PermissionPolicy(
            getattr(self.args, "permission_mode", "auto"),
            prompt=lambda message: secure_prompt(message),
        )
        self.agent.approve = self.permission_policy
        self.agent.ctx.approve = self.permission_policy
        goal = self.agent.current_goal()
        self._goal_status = goal.status if goal is not None else ""
        self.bindings = self._bindings()
        history, self.history_warning = _history_for_workspace(self.agent.cwd)
        prompt_io = {}
        if not sys.stdin.isatty():
            prompt_io["input"] = DummyInput()
        if not sys.stdout.isatty():
            prompt_io["output"] = DummyOutput()
        self.session = PromptSession(
            history=history,
            completer=WorkspaceCompleter(
                self.agent.cwd,
                provider=lambda: self.provider,
            ),
            complete_while_typing=True,
            key_bindings=self.bindings,
            bottom_toolbar=self._toolbar,
            reserve_space_for_menu=8,
            multiline=False,
            **prompt_io,
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
            self._cycle_effort(1)
            event.app.invalidate()

        @bindings.add("s-down")
        def shift_down(event) -> None:
            self._cycle_effort(-1)
            event.app.invalidate()

        @bindings.add("f2")
        @bindings.add("escape", "m")
        def model_next(event) -> None:
            self._cycle_model(1)
            event.app.invalidate()

        @bindings.add("f3")
        @bindings.add("escape", "r")
        def effort_next(event) -> None:
            self._cycle_effort(1)
            event.app.invalidate()

        @bindings.add("f4")
        @bindings.add("escape", "v")
        def verbosity_next(event) -> None:
            self._cycle_verbosity(1)
            event.app.invalidate()

        @bindings.add("escape", "enter")
        def newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-t")
        def transcript(event) -> None:
            self.transcript_expanded = not self.transcript_expanded
            event.app.run_in_terminal(self._show_activity)
            event.app.invalidate()

        @bindings.add("tab", filter=Condition(lambda: self._turn_active))
        def queue_follow_up(event) -> None:
            # Enter steers the active run. Tab intentionally schedules a fresh
            # turn, so a new request cannot accidentally rewrite current work.
            self.queue_submission = True
            event.current_buffer.validate_and_handle()

        return bindings

    @property
    def _turn_active(self) -> bool:
        return bool(getattr(self, "turn_active", False))

    def _cycle_effort(self, direction: int) -> None:
        self.agent.client.effort = _cycle_choice(
            self.agent.client.effort,
            EFFORTS,
            direction,
        )
        self._remember_defaults(announce=False)

    def _cycle_model(self, direction: int) -> None:
        models = _model_choices(self.provider)
        self.agent.client.model = _cycle_choice(
            self.agent.client.model,
            models,
            direction,
        )
        self._align_provider_for_model(self.agent.client.model)
        self.agent.client.resolver.model = self.agent.client.model
        self._remember_defaults(announce=False)

    def _cycle_verbosity(self, direction: int) -> None:
        self.agent.client.verbosity = _cycle_choice(
            self.agent.client.verbosity,
            VERBOSITIES,
            direction,
        )
        self._remember_defaults(announce=False)

    def _toolbar(self):
        images = f" · {len(self.attachments)} image" if self.attachments else ""
        cwd = Path(self.agent.cwd).name or str(self.agent.cwd)
        autonomy = self._autonomy_label()
        limit = self.args.auto_max_turns
        loop_value = f"{autonomy}:{limit}" if limit else autonomy
        auto = f" · loop {loop_value}" if autonomy != "off" else ""
        goal_label = f" · goal {self._goal_status}" if self._goal_status else ""
        plan_state = self.agent.ctx.state.get("plan")
        plan_items = plan_state.get("plan", []) if isinstance(plan_state, dict) else []
        completed = sum(
            item.get("status") == "completed"
            for item in plan_items
            if isinstance(item, dict)
        )
        plan_label = f" · plan {completed}/{len(plan_items)}" if plan_items else ""
        queued = (
            f" · queued {len(self.pending_prompts)}" if self.pending_prompts else ""
        )
        return HTML(
            " <b>mjj</b> · model <b>"
            f"{html.escape(self.agent.client.model)}</b>"
            f" · reasoning <b>{html.escape(self.agent.client.effort)}</b>"
            f" · provider {html.escape(self.provider)}"
            f"{auto}{goal_label}{plan_label}{images}{queued} · "
            f"{html.escape(cwd)}   Enter send · / commands · "
            "Shift+↑/↓ reasoning · Ctrl+T details "
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
        return 0

    def _welcome(self) -> None:
        input_hint = (
            "Shift+↑/↓ reasoning · Alt+Enter newline"
            if RICH_TUI
            else "/reasoning changes effort"
        )
        print_formatted_text(
            ANSI(
                "\x1b[38;5;45m╭─ mjj\x1b[0m  coding agent\n"
                f"\x1b[38;5;45m│\x1b[0m  model {self.agent.client.model}"
                f"  ·  reasoning {self.agent.client.effort}"
                f"  ·  provider {self.provider}\n"
                f"\x1b[38;5;45m│\x1b[0m  Enter sends · / opens commands · "
                f"{input_hint}\n"
                f"\x1b[38;5;45m╰─\x1b[0m  while working: Enter steers now · "
                "Tab queues next · Ctrl+T shows full tool output · /tips"
            )
        )
        if not RICH_TUI:
            print(
                "\x1b[2mstdlib composer · install mojojojo-agent[full] "
                "for completion and hotkeys\x1b[0m"
            )
        warnings = self.agent.registry.warnings
        if self.history_warning:
            print_formatted_text(ANSI(f"\x1b[33mwarning: {self.history_warning}\x1b[0m"))
        for warning in warnings[:5]:
            print_formatted_text(ANSI(f"\x1b[33mwarning: {warning}\x1b[0m"))
        if len(warnings) > 5:
            print_formatted_text(ANSI(f"\x1b[33m… {len(warnings) - 5} more warnings; /mcp\x1b[0m"))

    def turn(self, text: str) -> None:
        images = tuple(self.attachments)
        self.attachments.clear()
        prompts = [(text, images)]
        first_prompt = True
        while prompts:
            prompt, prompt_images = prompts.pop(0)
            if not first_prompt:
                print_formatted_text(
                    ANSI(
                        f"\x1b[38;5;45m⇥ starting queued follow-up"
                        f" · {len(prompts)} still queued\x1b[0m"
                    )
                )
            first_prompt = False
            self.activity.clear()
            print_formatted_text(
                ANSI(
                    "\x1b[2mworking · "
                    "Enter steers · Tab queues · Ctrl+T details\x1b[0m"
                )
            )
            try:
                steps = self.agent.run(
                    prompt,
                    images=prompt_images,
                    auto_next_steps=self.args.auto_next_steps,
                    auto_next_idea=self.args.auto_next_idea,
                    max_autonomous_turns=self.args.auto_max_turns,
                )
                if RICH_TUI and sys.stdin.isatty() and sys.stdout.isatty():
                    asyncio.run(self._interactive_render(steps))
                else:
                    self._render(steps)
            except KeyboardInterrupt:
                print_formatted_text(ANSI("\x1b[33m■ interrupted\x1b[0m"))
            if self.pending_prompts:
                prompts.extend((queued, ()) for queued in self.pending_prompts)
                self.pending_prompts.clear()

    async def _interactive_render(self, steps) -> None:
        """Render in a worker while the foreground prompt accepts steering."""
        self.turn_active = True
        worker = asyncio.create_task(asyncio.to_thread(self._render, steps))
        try:
            with patch_stdout(raw=True):
                while not worker.done():
                    self.queue_submission = False
                    prompt = asyncio.create_task(
                        self.session.prompt_async(
                            HTML("<ansicyan>↪</ansicyan> steer <ansigray>(Enter send · Tab queue · Ctrl+T details)</ansigray> ")
                        )
                    )
                    done, _ = await asyncio.wait(
                        {worker, prompt}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if worker in done:
                        prompt.cancel()
                        try:
                            await prompt
                        except asyncio.CancelledError:
                            pass
                        break
                    guidance = prompt.result().strip()
                    if guidance:
                        self._accept_live_input(guidance, queued=self.queue_submission)
            await worker
        finally:
            self.turn_active = False

    def _accept_live_input(self, text: str, *, queued: bool) -> bool:
        if queued:
            self.pending_prompts.append(text)
            print_formatted_text(
                ANSI(
                    f"\x1b[38;5;45m  ⇥ follow-up queued "
                    f"({len(self.pending_prompts)})\x1b[0m"
                )
            )
            return True
        accepted = self.agent.steer(text)
        message = "  ↪ steering sent" if accepted else "  steering queue full"
        color = "\x1b[38;5;45m" if accepted else "\x1b[31m"
        print_formatted_text(ANSI(f"{color}{message}\x1b[0m"))
        return accepted

    def _render(self, steps) -> None:
        text_open = False
        reasoning_open = False
        last_usage: Step | None = None
        failed = False
        for step in steps:
            if step.kind in {"tool_call", "tool_result"}:
                self.activity.append(step)
            if step.kind == "reasoning":
                if text_open:
                    print()
                    text_open = False
                if not reasoning_open:
                    print_formatted_text(ANSI("\x1b[2m  ░ "), end="")
                    reasoning_open = True
                print(step.text, end="", flush=True)
            elif step.kind == "text":
                if reasoning_open:
                    print_formatted_text(ANSI("\x1b[0m"))
                    reasoning_open = False
                if not text_open:
                    text_open = True
                print(step.text, end="", flush=True)
            elif step.kind == "tool_call":
                if reasoning_open:
                    print_formatted_text(ANSI("\x1b[0m"))
                    reasoning_open = False
                if text_open:
                    print()
                    text_open = False
                if step.name != "skill" or self.transcript_expanded:
                    label = _tool_label(step)
                    print_formatted_text(ANSI(f"\x1b[2m  {label}\x1b[0m"))
            elif step.kind == "tool_result":
                if reasoning_open:
                    print_formatted_text(ANSI("\x1b[0m"))
                    reasoning_open = False
                if text_open:
                    print()
                    text_open = False
                if step.name == "skill" and not self.transcript_expanded:
                    continue
                if not step.meta.get("ok", True):
                    print_formatted_text(ANSI(f"\x1b[31m    {step.text}\x1b[0m"))
                elif step.meta.get("terminal_image"):
                    self._render_image_event(step)
                elif self.transcript_expanded:
                    print(step.text or "(no output)")
                elif step.name == "update_plan":
                    summary = _plan_summary(step)
                    print_formatted_text(ANSI(f"\x1b[2m    ✓ {summary}\x1b[0m"))
                elif step.name in {"apply_patch", "checkpoint", "check"}:
                    summary = _compact_result(step.text)
                    print_formatted_text(ANSI(f"\x1b[2m    ✓ {summary}\x1b[0m"))
                else:
                    preview = _result_preview(step.text)
                    if preview:
                        print_formatted_text(ANSI(f"\x1b[2m{preview}\x1b[0m"))
            elif step.kind == "autonomous":
                if reasoning_open:
                    print_formatted_text(ANSI("\x1b[0m"))
                    reasoning_open = False
                if text_open:
                    print()
                    text_open = False
                print_formatted_text(
                    ANSI(
                        f"\x1b[38;5;45m  ↻ autonomous continuation "
                        f"{step.meta.get('turn', 0)}\x1b[0m"
                    )
                )
            elif step.kind == "status":
                if reasoning_open:
                    print_formatted_text(ANSI("\x1b[0m"))
                    reasoning_open = False
                if text_open:
                    print()
                    text_open = False
                if step.meta.get("attempt"):
                    detail = (
                        f"retry {step.meta['attempt']}/{step.meta.get('max_retries', '?')} "
                        f"in {step.meta.get('delay', 0):g}s · {step.text}"
                    )
                else:
                    detail = step.text
                print_formatted_text(ANSI(f"\x1b[33m  ↻ {detail}\x1b[0m"))
            elif step.kind == "usage":
                last_usage = step
            elif step.kind == "goal":
                if reasoning_open:
                    print_formatted_text(ANSI("\x1b[0m"))
                    reasoning_open = False
                if text_open:
                    print()
                    text_open = False
                status = step.meta.get("status", "active")
                self._goal_status = str(status)
                turn = step.meta.get("turn", 0)
                suffix = f" · continuation {turn}" if turn else ""
                print_formatted_text(
                    ANSI(f"\x1b[38;5;45m  ◎ goal {status}{suffix}\x1b[0m")
                )
            elif step.kind == "steering":
                if reasoning_open:
                    print_formatted_text(ANSI("\x1b[0m"))
                    reasoning_open = False
                if text_open:
                    print()
                    text_open = False
                print_formatted_text(ANSI("\x1b[38;5;45m  ↪ steering applied\x1b[0m"))
            elif step.kind == "error":
                failed = True
                if reasoning_open:
                    print_formatted_text(ANSI("\x1b[0m"))
                    reasoning_open = False
                if text_open:
                    print()
                    text_open = False
                print_formatted_text(ANSI(f"\x1b[31merror: {step.text}\x1b[0m"))
        if reasoning_open:
            print_formatted_text(ANSI("\x1b[0m"))
        if text_open:
            print()
        if not failed:
            detail = ""
            if last_usage is not None:
                detail = (
                    f" · {last_usage.meta.get('seconds', 0):g}s · {last_usage.text}"
                )
            print_formatted_text(ANSI(f"\x1b[2mready{detail}\x1b[0m"))

    def _show_activity(self) -> None:
        state = "full" if self.transcript_expanded else "compact"
        print_formatted_text(ANSI(f"\x1b[38;5;45m── tool transcript · {state} ──\x1b[0m"))
        if not self.activity:
            print("  no tool activity yet")
            return
        for step in self.activity:
            if step.kind == "tool_call":
                print(f"  {_tool_label(step)}")
            else:
                body = step.text if self.transcript_expanded else _result_preview(step.text)
                print(body or "    (no output)")

    def command(self, line: str) -> None:
        command, _, value = line.partition(" ")
        value = value.strip()
        if command in ("/exit", "/quit"):
            self.done = True
        elif command in ("/help", "/commands"):
            self._show_help()
        elif command == "/tips":
            self._show_tips()
        elif command in ("/effort", "/reasoning"):
            self._set_choice("effort", value, EFFORTS)
        elif command == "/verbosity":
            self._set_choice("verbosity", value, VERBOSITIES)
        elif command == "/model":
            self._set_model(value)
        elif command == "/provider":
            self._set_provider(value)
        elif command == "/image":
            self._attach(value)
        elif command == "/images":
            print("\n".join(image.summary() for image in self.attachments) or "no queued images")
        elif command == "/preview":
            self._preview(value)
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
        elif command == "/cache":
            if value:
                try:
                    self.agent.client.set_cache_mode(value)
                except ValueError as exc:
                    print(exc)
                    return
            print(json.dumps(self.agent.client.cache_status(), indent=2))
        elif command == "/models":
            self._show_models()
        elif command == "/settings":
            print(
                json.dumps(
                    {
                        "provider": self.provider,
                        "model": self.agent.client.model,
                        "effort": self.agent.client.effort,
                        "effective_effort": (
                            self.agent.client.last_effective_effort
                            or self.agent.client.effort
                        ),
                        "verbosity": self.agent.client.verbosity,
                        "cache": self.agent.client.cache_status(),
                        "permission_mode": self.permission_policy.mode,
                        "autonomy": self._autonomy_label(),
                        "auto_max_turns": self.args.auto_max_turns,
                        "goal": (
                            self.agent.current_goal().public()
                            if self.agent.current_goal() is not None
                            else None
                        ),
                        "terminal_images": terminal_image_protocol(),
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
        elif command == "/undo":
            self._checkpoint("undo", value)
        elif command == "/checkpoints":
            self._checkpoint("list", "")
        elif command in ("/auto", "/loop"):
            self._set_autonomy(value)
        elif command == "/goal":
            self._goal(value)
        elif command == "/plan":
            self._plan(value)
        elif command == "/compact":
            self._compact()
        elif command == "/ps":
            self._background_jobs()
        elif command == "/stop":
            self._stop_background_jobs()
        elif command == "/mcp":
            tools = sorted(
                name for name in self.agent.registry.tools if name.startswith("mcp__")
            )
            for warning in self.agent.registry.warnings:
                print(f"warning: {warning}")
            print("\n".join(tools) if tools else "no MCP tools available")
        elif command == "/skills":
            self._skills()
        elif command == "/plugins":
            self._plugins()
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
        elif command == "/raw":
            try:
                self.transcript_expanded = _toggle(value, self.transcript_expanded)
            except ValueError as exc:
                print(exc)
                return
            print(f"full tool output: {'on' if self.transcript_expanded else 'off'}")
        elif command == "/rollout":
            print(self.agent.session.path if self.agent.session else "ephemeral session")
        elif command == "/reload":
            self.agent.registry.close()
            self.agent.registry = build_registry(
                disabled=self.args.disabled_tools,
                skill_paths=self.args.skill_paths,
                mcp_servers=self.args.resolved_config.mcp_servers,
                plugins=self.args.plugins,
            )
            if self.agent.goal_store is not None:
                self.agent.bind_goal_store(self.agent.goal_store)
            print("reloaded tools and skills")
        elif command in ("/hotkeys", "/keys"):
            self._show_hotkeys()
        elif command == "/clear":
            print("\x1b[2J\x1b[H", end="")
        elif command == "/new":
            self._new_session()
        else:
            print(f"unknown command {command}; type /help")

    def _shell(self, line: str) -> None:
        excluded = line.startswith("!!")
        command = line[2 if excluded else 1:].strip()
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
        from .tools.shell import describe_jobs

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
            "cache": self.agent.client.cache_status(),
            "permissions": self.permission_policy.mode,
            "autonomy": self._autonomy_label(),
            "goal": (
                self.agent.current_goal().public()
                if self.agent.current_goal() is not None
                else None
            ),
            "terminal_images": terminal_image_protocol(),
            "session": session.id if session else "ephemeral",
            "transcript_items": len(self.agent.items),
            "tools": sorted(self.agent.registry.tools),
            "tool_warnings": list(self.agent.registry.warnings),
            "project_docs": [
                str(path) for path in self.agent.project_instructions.sources
            ],
            "usage": self.agent.client.usage.summary(),
            "tool_usage": self.agent.ledger.summary(),
            "background_jobs": describe_jobs(self.agent.ctx),
            "git": git.output,
        }
        print(json.dumps(status, indent=2))

    def _plan(self, value: str) -> None:
        state = self.agent.ctx.state
        if value == "clear":
            state.pop("plan", None)
            print("structured plan cleared")
            return
        if value == "approve":
            previous = state.pop("plan-permission-mode", None)
            if previous is None:
                print("no plan is awaiting approval")
                return
            self.permission_policy.set(str(previous))
            self.args.permission_mode = str(previous)
            self.turn(
                "The plan is approved. Implement it completely, validate the result, "
                "and report the outcome."
            )
            return
        if value == "cancel":
            previous = state.pop("plan-permission-mode", None)
            if previous is not None:
                self.permission_policy.set(str(previous))
                self.args.permission_mode = str(previous)
            state.pop("plan", None)
            print("plan mode cancelled")
            return
        if value:
            state.setdefault("plan-permission-mode", self.permission_policy.mode)
            self.permission_policy.set("read-only")
            self.args.permission_mode = "read-only"
            self.turn(
                "Plan this task without modifying files or external state. Inspect what "
                "you need, maintain a concise structured plan with update_plan, identify "
                "risks and validation, then stop for explicit approval.\n\nTask: " + value
            )
            print("plan ready · /plan approve to implement · /plan cancel to discard")
            return
        plan = state.get("plan")
        waiting = " · awaiting approval" if "plan-permission-mode" in state else ""
        print((json.dumps(plan, indent=2) if plan else "no structured plan yet") + waiting)

    def _compact(self) -> None:
        previous = self.agent.client.compact_threshold
        self.agent.client.compact_threshold = 1
        try:
            self.turn(
                "Compact the conversation context now, preserving the active objective, "
                "decisions, workspace state, and unfinished work."
            )
        finally:
            self.agent.client.compact_threshold = previous

    def _background_jobs(self) -> None:
        from .tools.shell import describe_jobs

        jobs = describe_jobs(self.agent.ctx)
        print("\n".join(jobs) if jobs else "no background shell jobs")

    def _stop_background_jobs(self) -> None:
        from .tools.shell import stop_jobs

        stopped = stop_jobs(self.agent.ctx)
        print(f"stopped {stopped} background job{'' if stopped == 1 else 's'}")

    def _skills(self) -> None:
        from .skills import discover

        skills = discover(self.agent.cwd, extra_paths=self.args.skill_paths)
        lines = [
            f"{skill.qualified_name} · {skill.description} · {skill.path}"
            for skill in skills
        ]
        print("\n".join(lines) if lines else "no skills found")

    def _plugins(self) -> None:
        from .plugins import plugin_inventory

        enabled = set(self.args.plugins)
        plugins = plugin_inventory()
        lines = [
            f"{'on ' if plugin.name in enabled else 'off'} · {plugin.name} · "
            f"{plugin.distribution or plugin.value}"
            for plugin in plugins
        ]
        print("\n".join(lines) if lines else "no mojojojo.tools plugins installed")

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

    def _checkpoint(self, action: str, identifier: str) -> None:
        arguments = {"action": action}
        if identifier:
            arguments["id"] = identifier
        result = self.agent.registry.dispatch(
            "checkpoint", json.dumps(arguments), self.agent.ctx
        )
        print(result.output)

    def _set_choice(self, name: str, value: str, choices: tuple[str, ...]) -> None:
        current = getattr(self.agent.client, name)
        if value:
            try:
                current = _select_choice(value, current, choices, label=name)
            except ValueError as exc:
                print(exc)
                return
            setattr(self.agent.client, name, current)
            if name in {"effort", "verbosity"}:
                self._remember_defaults()
        print(f"{name}: {current}")

    def _set_model(self, value: str) -> None:
        models = _model_choices(self.provider)
        if not value:
            if RICH_TUI and sys.stdin.isatty() and sys.stdout.isatty():
                selected = _pick_model(models, self.agent.client.model, self.provider)
                if selected is None:
                    print(f"model unchanged: {self.agent.client.model}")
                    return
                self.agent.client.model = selected
                self._align_provider_for_model(selected)
                self.agent.client.resolver.model = selected
                self._remember_defaults()
                print(
                    f"model: {selected} · reasoning: {self.agent.client.effort}"
                    f" · provider: {self.provider}"
                )
                return
            self._show_models()
            return
        try:
            selected = _select_choice(
                value,
                self.agent.client.model,
                models,
                label="model",
                allow_custom=True,
            )
        except ValueError as exc:
            print(exc)
            return
        self.agent.client.model = selected
        self._align_provider_for_model(selected)
        self.agent.client.resolver.model = selected
        self._remember_defaults()
        print(f"model: {selected} · provider: {self.provider}")

    def _align_provider_for_model(self, model: str) -> None:
        """When provider is auto, lock onto the one preset that owns this model."""
        if self.provider != "auto":
            return
        owners = [
            name
            for name, models in MODEL_PRESETS.items()
            if name not in {"auto", "custom"} and model in models
        ]
        if len(owners) != 1:
            return
        selected = owners[0]
        self.agent.client.provider = selected
        self.agent.client.resolver = auth.CredentialResolver(
            provider=selected, model=model
        )

    def _remember_defaults(self, *, announce: bool = True) -> None:
        """Persist interactive model/provider/effort/verbosity for the next launch."""
        try:
            path = persist_user_agent_settings(
                model=self.agent.client.model,
                provider=self.provider,
                effort=self.agent.client.effort,
                verbosity=self.agent.client.verbosity,
            )
        except Exception as exc:  # noqa: BLE001 - never block the session on prefs
            print(f"warning: could not save defaults ({exc})")
            return
        if announce:
            print(f"saved defaults → {path}")

    def _set_provider(self, value: str) -> None:
        if not value:
            print(f"provider: {self.provider} · choices: {', '.join(PROVIDERS)}")
            return
        try:
            selected = _select_choice(
                value,
                self.provider,
                PROVIDERS,
                label="provider",
            )
        except ValueError as exc:
            print(exc)
            return
        previous_model = self.agent.client.model
        known_models = {
            model for models in MODEL_PRESETS.values() for model in models
        }
        target_models = MODEL_PRESETS.get(selected, ("auto",))
        if previous_model in known_models and previous_model not in target_models:
            self.agent.client.model = "auto"
        self.agent.client.provider = selected
        self.agent.client.resolver = auth.CredentialResolver(
            provider=selected, model=self.agent.client.model
        )
        self._remember_defaults()
        print(f"provider: {selected} · model: {self.agent.client.model}")

    def _show_models(self) -> None:
        models = _model_choices(self.provider)
        print(f"models for provider {self.provider} · current {self.agent.client.model}:")
        for index, model in enumerate(models, 1):
            marker = "*" if model == self.agent.client.model else " "
            print(f" {marker} {index}. {model} — {_model_description(model)}")
        if self.agent.client.model not in models:
            print(f" * custom: {self.agent.client.model}")
        if self.provider == "auto":
            print("auto routing: select /provider for guaranteed compatibility")
        picker_hint = "run /model for the arrow-key picker; " if RICH_TUI else ""
        print(
            f"{picker_hint}use /model NUMBER|NAME|next|prev; "
            "custom model names are accepted"
        )

    def _show_help(self) -> None:
        for name, description in COMMANDS.items():
            print(f"{name:12} {description}")
        print()
        self._show_hotkeys()

    @staticmethod
    def _show_hotkeys() -> None:
        print(
            "/model: arrow-key model picker · F2 or Alt+M: next model\n"
            "Shift+↑/↓: reasoning higher/lower · F3 or Alt+R: next reasoning\n"
            "empty ←/→: reasoning lower/higher\n"
            "F4 or Alt+V: next verbosity · Alt+Enter: newline · Ctrl+C: interrupt\n"
            "while working: Enter sends steering · Tab queues it · Ctrl+T toggles full tool output\n"
            "↑/↓: prompts from this directory · Ctrl+R: search prompt history\n"
            "@file: attach context · !command: include output · !!command: local only"
        )

    @staticmethod
    def _show_tips() -> None:
        print(
            "Send work\n"
            "  Type a request and press Enter. Use @path to attach a file and "
            "Alt+Enter for a newline.\n\n"
            "While the agent works\n"
            "  Enter sends guidance into the active task at its next safe boundary.\n"
            "  Tab queues a separate follow-up task to run afterward.\n"
            "  Ctrl+T toggles full tool output; Ctrl+C interrupts.\n\n"
            "Useful starts\n"
            "  /plan TASK plans read-only before approval. /review checks changes.\n"
            "  /status explains the session. /help lists every command."
        )

    def _attach(self, value: str) -> None:
        if not value:
            print("usage: /image PATH")
            return
        value = _path_argument(value)
        try:
            image = prepare_image(Path(self.agent.cwd) / value)
        except ImageInputError as exc:
            print(f"image: {exc}")
            return
        self.attachments.append(image)
        print("attached", image.summary())
        render_terminal_image(image.path)

    def _preview(self, value: str) -> None:
        if not value:
            print("usage: /preview PATH")
            return
        value = _path_argument(value)
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(self.agent.cwd) / path
        result = render_terminal_image(path)
        if not result.ok:
            print(f"preview: {result.detail}")

    def _render_image_event(self, step: Step) -> None:
        relative = str(step.meta.get("terminal_image") or "")
        path = (Path(self.agent.cwd) / relative).resolve()
        try:
            path.relative_to(Path(self.agent.cwd).resolve())
        except ValueError:
            print_formatted_text(ANSI("\x1b[31m    image path left workspace\x1b[0m"))
            return
        result = render_terminal_image(path)
        if not result.ok:
            print_formatted_text(ANSI(f"\x1b[2m    {step.text} · {result.detail}\x1b[0m"))

    def _login(self, provider: str) -> None:
        if provider in ("chatgpt", "device"):
            try:
                code = auth.login_chatgpt(device=provider == "device")
            except auth.AuthError as exc:
                print(f"login: {exc}")
                return
            print("ChatGPT sign-in complete" if code == 0 else f"ChatGPT sign-in failed ({code})")
            return
        if provider not in ("deepseek", "openpaths", "openrouter", "openai", "custom"):
            print("login provider must be chatgpt, device, deepseek, openpaths, openrouter, openai or custom")
            return
        try:
            key = secure_prompt(f"{provider} API key: ", is_password=True).strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not key:
            return
        path = auth.save_provider_key(provider, key)
        self.agent.client.provider = provider
        self.agent.client.resolver = auth.CredentialResolver(
            provider=provider, model=self.agent.client.model
        )
        self._remember_defaults(announce=False)
        print(f"saved {provider} credential to {path}")

    def _new_session(self) -> None:
        if self.agent.session:
            self.agent.session.close()
        self.agent.session = Session(meta={"cwd": str(self.agent.cwd)})
        self.agent.items.clear()
        self.agent.ctx.state.pop("plan", None)
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
        if mode == "forever":
            mode = "full"
            raw_limit = "0"
        if mode not in ("off", "steps", "ideas", "full"):
            print("usage: /loop off|steps|ideas|full|forever [max-turns]")
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
        print(
            f"autonomy: {self._autonomy_label()} · max turns: "
            f"{self.args.auto_max_turns or 'unlimited'}"
        )

    def _goal(self, value: str) -> None:
        store = self.agent.goal_store or GoalStore(self.agent.cwd)
        self.agent.bind_goal_store(store)
        if not value:
            goal = store.load()
            print(goal.summary() if goal is not None else "no goal for this workspace")
            return
        action, _, message = value.partition(" ")
        action = action.lower()
        message = message.strip()
        try:
            if action == "pause":
                goal = store.transition("paused", message)
                self.agent.bind_goal_store(store)
                self._goal_status = goal.status
                print(goal.summary())
                return
            if action == "resume":
                goal = store.transition("active", message)
                self.agent.bind_goal_store(store)
                self._goal_status = goal.status
                print(goal.summary())
                self.turn("Resume the active goal from its latest verified checkpoint.")
                return
            if action in ("complete", "blocked"):
                if not message:
                    print(f"usage: /goal {action} EVIDENCE")
                    return
                goal = store.transition(action, message)
                self.agent.bind_goal_store(store)
                self._goal_status = goal.status
                print(goal.summary())
                return
            if action == "clear":
                removed = store.clear()
                self.agent.bind_goal_store(store)
                self._goal_status = ""
                print("goal cleared" if removed else "no goal for this workspace")
                return
            objective = message if action == "set" else value
            if not objective:
                print("usage: /goal OBJECTIVE")
                return
            goal = store.set(
                objective,
                session_id=self.agent.session.id if self.agent.session else "",
            )
        except ValueError as exc:
            print(f"goal: {exc}")
            return
        self.agent.bind_goal_store(store)
        self._goal_status = goal.status
        print(goal.summary())
        self.turn("Begin the active goal now and establish its first checkpoint.")

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
        self.agent.ctx.state.pop("plan", None)
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


def _compact_result(text: str, limit: int = 160) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _result_preview(text: str, lines: int = 3, width: int = 240) -> str:
    """Small useful preview; the ledger has already bounded the source text."""
    parts = text.splitlines()
    shown = [f"    {line[:width]}" for line in parts[:lines]]
    if len(parts) > lines:
        shown.append(f"    … {len(parts) - lines} more lines · Ctrl+T for full output")
    return "\n".join(shown)


def _plan_summary(step: Step) -> str:
    state = step.meta.get("plan")
    items = state.get("plan", []) if isinstance(state, dict) else []
    completed = sum(
        item.get("status") == "completed" for item in items if isinstance(item, dict)
    )
    active = next(
        (
            str(item.get("step") or "")
            for item in items
            if isinstance(item, dict) and item.get("status") == "in_progress"
        ),
        "",
    )
    summary = f"plan {completed}/{len(items)} complete"
    return f"{summary} · {active[:100]}" if active else summary


def _path_argument(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _cycle_choice(current: str, choices: tuple[str, ...], direction: int) -> str:
    if not choices:
        return current
    if current not in choices:
        return choices[0] if direction >= 0 else choices[-1]
    index = choices.index(current)
    return choices[(index + direction) % len(choices)]


def _toggle(value: str, current: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"", "toggle"}:
        return not current
    if normalized in {"on", "true", "1"}:
        return True
    if normalized in {"off", "false", "0"}:
        return False
    raise ValueError("value must be on, off, or toggle")


def _select_choice(
    value: str,
    current: str,
    choices: tuple[str, ...],
    *,
    label: str,
    allow_custom: bool = False,
) -> str:
    query = value.strip()
    lowered = query.lower()
    if lowered in {"next", "prev"}:
        return _cycle_choice(current, choices, 1 if lowered == "next" else -1)
    if query.isdigit():
        index = int(query)
        if 1 <= index <= len(choices):
            return choices[index - 1]
        raise ValueError(f"{label} number must be between 1 and {len(choices)}")
    exact = [choice for choice in choices if choice.lower() == lowered]
    if exact:
        return exact[0]
    matches = [choice for choice in choices if lowered in choice.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"ambiguous {label}: {', '.join(matches)}")
    if allow_custom and query:
        return query
    raise ValueError(f"{label} must be one of: {', '.join(choices)}")


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
    try:
        return InteractiveApp(agent, args).run()
    finally:
        if agent.session:
            agent.session.close()
        agent.registry.close()
