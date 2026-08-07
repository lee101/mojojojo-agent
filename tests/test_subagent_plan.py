from __future__ import annotations

from mjj.subagents import SubagentResult, SubagentTask, advance_plan_for_tasks


def test_advance_plan_checks_off_matching_steps() -> None:
    plan = {
        "explanation": "",
        "plan": [
            {"step": "Fix jump", "status": "in_progress"},
            {"step": "Verify", "status": "pending"},
        ],
    }
    tasks = [
        SubagentTask(prompt="jump", role="worker", plan_step="Fix jump"),
    ]
    results = [SubagentResult(identifier="a", role="worker", ok=True, answer="done")]
    out = advance_plan_for_tasks(plan, tasks, results)
    assert out["plan"][0]["status"] == "completed"
    assert out["plan"][1]["status"] == "in_progress"


def test_advance_plan_ignores_failed_tasks() -> None:
    plan = {
        "plan": [
            {"step": "A", "status": "in_progress"},
            {"step": "B", "status": "pending"},
        ]
    }
    tasks = [SubagentTask(prompt="x", plan_step="A")]
    results = [SubagentResult(identifier="a", role="reviewer", ok=False, error="nope")]
    out = advance_plan_for_tasks(plan, tasks, results)
    assert out["plan"][0]["status"] == "in_progress"
