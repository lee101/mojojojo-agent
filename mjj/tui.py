"""Interactive terminal surface for ``mjj``.

It deliberately stays inline instead of taking over the alternate screen: the
terminal scrollback remains a useful, copyable session transcript. The composer
is cross-platform prompt-toolkit, so Windows gets the same history, completion,
multiline input and key bindings as Unix terminals.
"""

from __future__ import annotations

import html
import json
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
from .media import ImageAttachment, ImageInputError, prepare_image
from .session import Session


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
    "/clear": "clear the terminal",
    "/new": "start a fresh conversation",
    "/exit": "leave mjj",
}

MODEL_PRESETS = {
    "auto": ("auto",),
    "openai": ("auto", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"),
    "openpaths": ("auto", "openpaths/auto-code", "openpaths/auto", "openpaths/auto-hard"),
    "openrouter": ("auto", "openrouter/auto"),
    "custom": ("auto",),
}


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for command, description in COMMANDS.items():
            if command.startswith(text):
                yield Completion(
                    command,
                    start_position=-len(text),
                    display_meta=description,
                )


@dataclass
class InteractiveApp:
    agent: Agent
    args: object
    attachments: list[ImageAttachment] = field(default_factory=list)
    done: bool = False

    def __post_init__(self) -> None:
        self.bindings = self._bindings()
        history = auth.mjj_home() / "history"
        history.parent.mkdir(parents=True, exist_ok=True)
        self.session = PromptSession(
            history=FileHistory(str(history)),
            completer=SlashCompleter(),
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
        return HTML(
            " <b>mjj</b> · "
            f"{html.escape(self.provider)}/{html.escape(self.agent.client.model)} · "
            f"reasoning <b>{html.escape(self.agent.client.effort)}</b>{images} · "
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
            self._render(self.agent.run(text, images=images))
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
        elif command == "/clear":
            print("\x1b[2J\x1b[H", end="")
        elif command == "/new":
            self._new_session()
        else:
            print(f"unknown command {command}; type /help")

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
        self.agent.session = Session()
        self.agent.items.clear()
        self.agent.client.cache_key = f"mjj-{self.agent.session.id}"
        print(f"new session {self.agent.session.id}")


def _tool_label(step: Step) -> str:
    return tool_progress(step)


def run(agent: Agent, args) -> int:
    return InteractiveApp(agent, args).run()
