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
from .agent import Agent, render
from .ledger import Ledger
from .model import ModelClient, probe
from .session import Session, resume
from .tools import build_registry


def _agent(args) -> Agent:
    items: list[dict] = []
    if getattr(args, "resume", None) is not None:
        session, items = resume(args.resume or None)
    else:
        session = Session()
    client = ModelClient(model=args.model, effort=args.effort)
    agent = Agent(
        registry=build_registry(),
        client=client,
        cwd=Path(args.cwd).resolve(),
        ledger=Ledger(),
        session=session,
    )
    agent.items = items
    return agent


def cmd_exec(args) -> int:
    agent = _agent(args)
    prompt = args.prompt or sys.stdin.read()
    code = render(agent.run(prompt), sys.stdout, verbose=args.verbose)
    if agent.session:
        agent.session.note(usage=agent.client.usage.summary(), tools=agent.ledger.summary())
        agent.session.close()
    if args.verbose:
        print(f"[{agent.ledger.summary()}]", file=sys.stderr)
    return code


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
    registry = build_registry()
    for schema in registry.schemas():
        print(f"{schema['name']:12} {schema['description'].splitlines()[0]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mjj")
    parser.add_argument("--model", default=ModelClient.model)
    parser.add_argument("--effort", default=ModelClient.effort,
                        choices=["low", "medium", "high", "xhigh"])
    parser.add_argument("--cwd", default=".")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("exec", help="one headless run")
    run.add_argument("prompt", nargs="?")
    run.add_argument("--resume", nargs="?", const="", default=None)
    run.set_defaults(func=cmd_exec)

    chat = sub.add_parser("chat", help="interactive session")
    chat.add_argument("--resume", nargs="?", const="", default=None)
    chat.set_defaults(func=cmd_repl)

    who = sub.add_parser("auth", help="credential status")
    who.add_argument("--probe", action="store_true", help="make one real call")
    who.set_defaults(func=cmd_auth)

    listing = sub.add_parser("tools", help="what the model can call")
    listing.set_defaults(func=cmd_tools)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args.command = "chat"
        args.resume = None
        args.func = cmd_repl
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
