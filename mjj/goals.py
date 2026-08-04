"""Durable, workspace-scoped goals with a bounded progress log.

Goals are deliberately separate from model transcripts. A session may be
forked or compacted while the workspace objective stays stable, and a broken
or partially written goal file must never prevent the coding harness starting.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


GOAL_STATUSES = ("active", "paused", "complete", "blocked")
MAX_OBJECTIVE_CHARS = 16_384
MAX_PROGRESS_CHARS = 2_000
MAX_PROGRESS_ENTRIES = 50


def goals_dir() -> Path:
    root = Path(os.environ.get("MJJ_HOME") or "~/.mjj").expanduser()
    path = root / "goals"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Goal:
    id: str
    objective: str
    cwd: str
    status: str = "active"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    session_id: str = ""
    progress: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict) -> "Goal":
        objective = str(value.get("objective") or "").strip()
        status = str(value.get("status") or "active")
        if not objective or status not in GOAL_STATUSES:
            raise ValueError("invalid goal document")
        progress = value.get("progress")
        if not isinstance(progress, list):
            progress = []
        return cls(
            id=str(value.get("id") or uuid.uuid4().hex[:16]),
            objective=objective[:MAX_OBJECTIVE_CHARS],
            cwd=str(value.get("cwd") or ""),
            status=status,
            created=float(value.get("created") or time.time()),
            updated=float(value.get("updated") or time.time()),
            session_id=str(value.get("session_id") or ""),
            progress=[item for item in progress[-MAX_PROGRESS_ENTRIES:] if isinstance(item, dict)],
        )

    def public(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        checkpoint = ""
        if self.progress:
            checkpoint = f"\nlatest: {self.progress[-1].get('message', '')}"
        return f"goal {self.id} · {self.status}\n{self.objective}{checkpoint}"


class GoalStore:
    """One durable goal per resolved workspace."""

    def __init__(self, cwd: str | Path):
        self.cwd = Path(cwd).expanduser().resolve()
        digest = hashlib.sha256(str(self.cwd).encode("utf-8")).hexdigest()[:20]
        self.path = goals_dir() / f"{digest}.json"

    def load(self) -> Goal | None:
        if not self.path.is_file():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            goal = Goal.from_dict(value)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if Path(goal.cwd).expanduser().resolve() != self.cwd:
            return None
        return goal

    def set(self, objective: str, *, session_id: str = "") -> Goal:
        objective = objective.strip()
        if not objective:
            raise ValueError("goal objective must not be empty")
        if len(objective) > MAX_OBJECTIVE_CHARS:
            raise ValueError(
                f"goal objective exceeds {MAX_OBJECTIVE_CHARS} characters"
            )
        goal = Goal(
            id=uuid.uuid4().hex[:16],
            objective=objective,
            cwd=str(self.cwd),
            session_id=session_id,
        )
        self.save(goal)
        return goal

    def save(self, goal: Goal) -> Goal:
        goal.updated = time.time()
        encoded = json.dumps(goal.public(), ensure_ascii=False, indent=2) + "\n"
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return goal

    def transition(self, status: str, message: str = "") -> Goal:
        if status not in GOAL_STATUSES:
            raise ValueError("goal status must be one of: " + ", ".join(GOAL_STATUSES))
        goal = self.load()
        if goal is None:
            raise ValueError("no goal exists for this workspace")
        goal.status = status
        if message.strip():
            self._append(goal, message, kind=status)
        return self.save(goal)

    def record(self, message: str, *, evidence: str = "") -> Goal:
        goal = self.load()
        if goal is None:
            raise ValueError("no goal exists for this workspace")
        self._append(goal, message, evidence=evidence, kind="checkpoint")
        return self.save(goal)

    def clear(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def _append(
        goal: Goal,
        message: str,
        *,
        evidence: str = "",
        kind: str,
    ) -> None:
        message = message.strip()
        if not message:
            raise ValueError("goal progress message must not be empty")
        entry = {
            "at": time.time(),
            "kind": kind,
            "message": message[:MAX_PROGRESS_CHARS],
        }
        if evidence.strip():
            entry["evidence"] = evidence.strip()[:MAX_PROGRESS_CHARS]
        goal.progress.append(entry)
        goal.progress = goal.progress[-MAX_PROGRESS_ENTRIES:]
