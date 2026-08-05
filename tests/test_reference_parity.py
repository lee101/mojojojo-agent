"""Regression contract for the audited Codex coding workflows."""

from mjj.tui import COMMANDS


CODEX_WORKFLOW_COMMANDS = {
    "/model",
    "/permissions",
    "/review",
    "/new",
    "/resume",
    "/fork",
    "/init",
    "/compact",
    "/plan",
    "/goal",
    "/copy",
    "/raw",
    "/diff",
    "/status",
    "/usage",
    "/mcp",
    "/plugins",
    "/logout",
    "/rollout",
    "/ps",
    "/stop",
    "/clear",
}


def test_audited_codex_workflows_remain_discoverable() -> None:
    missing = CODEX_WORKFLOW_COMMANDS.difference(COMMANDS)
    assert not missing, f"Codex workflow commands lost from the TUI: {sorted(missing)}"


def test_parity_document_does_not_reintroduce_resolved_steering_gap() -> None:
    document = open("docs/pi-parity.md", encoding="utf-8").read()
    assert "inline terminal still waits" not in document
    assert "Enter steers; Tab queues" in open(
        "docs/reference-harness-audit.md", encoding="utf-8"
    ).read()
