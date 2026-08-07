"""Bounded reviewer and worker agents with deterministic result ordering."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .agent import Agent, Step
from .auth import mjj_home
from .ledger import Budget, Ledger
from .model import ModelClient, Usage
from .permissions import PermissionPolicy
from .session import Session
from .tools import build_registry


MAX_TASKS = 4
MAX_PROMPT_CHARS = 16 * 1024
DEFAULT_MAX_STEPS = 40
DEFAULT_MAX_OUTPUT_TOKENS = 8_000
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_UNTRACKED_FILES = 1_000
ROLES = ("reviewer", "worker")


@dataclass(frozen=True)
class SubagentTask:
    prompt: str
    role: str = "reviewer"
    plan_step: str | None = None


@dataclass
class SubagentResult:
    identifier: str
    role: str
    ok: bool
    answer: str = ""
    error: str = ""
    session_id: str = ""
    commit: str = ""
    ref: str = ""
    usage: Usage = field(default_factory=Usage)

    def render(self) -> str:
        header = f"[{self.role} {self.identifier}] {'ok' if self.ok else 'failed'}"
        details = []
        if self.session_id:
            details.append(f"session {self.session_id}")
        if self.commit:
            details.append(f"commit {self.commit[:12]}")
        if self.ref:
            details.append(self.ref)
        if details:
            header += " · " + " · ".join(details)
        body = self.answer.strip() or self.error.strip() or "no response"
        if self.commit:
            body += f"\napply after review: git cherry-pick {self.commit}"
        return header + "\n" + body


class SubagentRunner:
    """Run independent agents, keeping child transcripts out of the parent."""

    def __init__(
        self,
        parent_client: ModelClient,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        tool_budget: int = 1_200,
        agent_factory: Callable[..., Agent] = Agent,
    ) -> None:
        if not 1 <= max_steps <= 200:
            raise ValueError("subagent max_steps must be between 1 and 200")
        if not 1 <= max_output_tokens <= 100_000:
            raise ValueError(
                "subagent max_output_tokens must be between 1 and 100000"
            )
        if tool_budget <= 0:
            raise ValueError("subagent tool_budget must be positive")
        self.parent_client = parent_client
        self.max_steps = max_steps
        self.max_output_tokens = max_output_tokens
        self.tool_budget = tool_budget
        self.agent_factory = agent_factory

    def run(self, tasks: Iterable[SubagentTask], cwd: Path) -> list[SubagentResult]:
        ordered = list(tasks)
        if not ordered:
            return []
        if len(ordered) > MAX_TASKS:
            raise ValueError(f"subagent runner accepts at most {MAX_TASKS} tasks")
        for index, task in enumerate(ordered, 1):
            if not isinstance(task, SubagentTask):
                raise ValueError(f"subagent task {index} must be a SubagentTask")
            if task.role not in ROLES:
                raise ValueError(
                    f"subagent task {index} role must be one of: {', '.join(ROLES)}"
                )
            if not isinstance(task.prompt, str) or not task.prompt.strip():
                raise ValueError(f"subagent task {index} prompt must be non-empty")
            if len(task.prompt) > MAX_PROMPT_CHARS:
                raise ValueError(
                    f"subagent task {index} prompt exceeds {MAX_PROMPT_CHARS} characters"
                )
        with ThreadPoolExecutor(
            max_workers=min(MAX_TASKS, len(ordered)),
            thread_name_prefix="mjj-subagent",
        ) as pool:
            futures = [pool.submit(self._one, task, cwd) for task in ordered]
            results = []
            for task, future in zip(ordered, futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(
                        SubagentResult(
                            identifier=uuid.uuid4().hex[:10],
                            role=task.role,
                            ok=False,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
            return results

    def _one(self, task: SubagentTask, cwd: Path) -> SubagentResult:
        identifier = uuid.uuid4().hex[:10]
        session = Session(
            meta={
                "cwd": str(cwd),
                "subagent": True,
                "role": task.role,
                "task": task.prompt[:500],
            }
        )
        workspace: _Worktree | None = None
        result = SubagentResult(
            identifier=identifier,
            role=task.role,
            ok=False,
            session_id=session.id,
        )
        try:
            workspace = _Worktree.create(cwd, identifier)
            child_client = self._client(identifier)
            registry = build_registry(disabled=("delegate",))
            policy = PermissionPolicy("read-only" if task.role == "reviewer" else "auto")
            child = self.agent_factory(
                registry=registry,
                client=child_client,
                cwd=workspace.cwd,
                ledger=Ledger(Budget(default=self.tool_budget)),
                session=session,
                max_steps=self.max_steps,
                approve=policy,
            )
            _bind_external_spills(child, identifier)
            prompt = _role_prompt(task)
            steps = list(child.run(prompt))
            answer, errors = _final_answer(steps)
            result.answer = answer
            result.error = "\n".join(errors)
            result.usage = child_client.usage
            result.ok = not errors
            if task.role == "worker" and result.ok:
                result.commit, result.ref = workspace.capture(task.prompt)
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            session.note(
                usage=result.usage.summary(),
                subagent_ok=result.ok,
                commit=result.commit,
                ref=result.ref,
            )
            session.close()
            if workspace is not None:
                workspace.close(retain=bool(result.error and task.role == "worker"))
                if workspace.retained:
                    suffix = f"isolated worktree retained at {workspace.path}"
                    result.error = f"{result.error}\n{suffix}".strip()
        return result

    def _client(self, identifier: str) -> ModelClient:
        parent = self.parent_client
        return ModelClient(
            model=parent.model,
            provider=parent.provider,
            effort=parent.effort,
            summary=parent.summary,
            verbosity=parent.verbosity,
            resolver=parent.resolver,
            max_retries=parent.max_retries,
            compact_threshold=parent.compact_threshold,
            cache_key=f"mjj-subagent-{identifier}",
            max_output_tokens=self.max_output_tokens,
        )


@dataclass
class _Worktree:
    root: Path
    path: Path
    cwd: Path
    identifier: str
    baseline: str = ""
    retained: bool = False

    @classmethod
    def create(cls, cwd: Path, identifier: str) -> "_Worktree":
        cwd = cwd.resolve()
        root_text = _git(cwd, "rev-parse", "--show-toplevel").strip()
        if not root_text:
            raise RuntimeError("delegation requires a Git worktree")
        root = Path(root_text).resolve()
        relative = cwd.relative_to(root)
        parent = mjj_home() / "subagents"
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = parent / identifier
        _git(root, "worktree", "add", "--detach", str(path), "HEAD")
        worktree = cls(root=root, path=path, cwd=path / relative, identifier=identifier)
        try:
            worktree._seed_current_state()
            worktree.baseline = _git(worktree.path, "rev-parse", "HEAD").strip()
        except Exception:
            worktree.close()
            raise
        return worktree

    def _seed_current_state(self) -> None:
        patch = _git_bytes(self.root, "diff", "--binary", "HEAD")
        if len(patch) > MAX_SNAPSHOT_BYTES:
            raise RuntimeError(
                f"working diff exceeds the {MAX_SNAPSHOT_BYTES} byte delegation limit"
            )
        if patch:
            _git_bytes(self.path, "apply", "--binary", "-", input_bytes=patch)
        untracked = _git_bytes(
            self.root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).split(b"\0")
        names = [raw for raw in untracked if raw]
        if len(names) > MAX_UNTRACKED_FILES:
            raise RuntimeError(
                f"workspace has more than {MAX_UNTRACKED_FILES} untracked files"
            )
        untracked_bytes = 0
        for raw in names:
            relative = Path(os.fsdecode(raw))
            source = self.root / relative
            target = self.path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.symlink_to(os.readlink(source))
            elif source.is_file():
                untracked_bytes += source.stat().st_size
                if untracked_bytes > MAX_SNAPSHOT_BYTES:
                    raise RuntimeError(
                        "untracked files exceed the delegation snapshot limit"
                    )
                shutil.copy2(source, target)
        if _git(self.path, "status", "--porcelain").strip():
            _git(self.path, "add", "-A")
            _commit(self.path, "mjj subagent baseline")

    def capture(self, prompt: str) -> tuple[str, str]:
        current = _git(self.path, "rev-parse", "HEAD").strip()
        if current != self.baseline:
            _git(self.path, "reset", "--soft", self.baseline)
        if not _git(self.path, "status", "--porcelain").strip():
            return "", ""
        _git(self.path, "add", "-A")
        subject = " ".join(prompt.split())[:72] or "worker changes"
        _commit(self.path, f"mjj worker: {subject}")
        commit = _git(self.path, "rev-parse", "HEAD").strip()
        ref = f"refs/mjj/subagents/{self.identifier}"
        _git(self.root, "update-ref", ref, commit)
        return commit, ref

    def close(self, *, retain: bool = False) -> None:
        if retain:
            self.retained = True
            return
        try:
            _git(self.root, "worktree", "remove", "--force", str(self.path))
        except Exception:
            self.retained = self.path.exists()


def _commit(path: Path, message: str) -> None:
    _git(
        path,
        "-c",
        "user.name=mjj subagent",
        "-c",
        "user.email=mjj@local",
        "commit",
        "--no-gpg-sign",
        "--no-verify",
        "-m",
        message,
    )


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        timeout=120,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail[:1000]}")
    return completed.stdout


def _git_bytes(
    path: Path,
    *args: str,
    input_bytes: bytes | None = None,
) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(path), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail[:1000]}")
    return completed.stdout


def _bind_external_spills(agent: Agent, identifier: str) -> None:
    root = mjj_home() / "subagents" / "results" / identifier
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    agent.ledger.spill_root = root
    agent.ledger.spill_prefix = str(root)


def _role_prompt(task: SubagentTask) -> str:
    if task.role == "reviewer":
        contract = (
            "Act as an independent read-only reviewer. Inspect the isolated snapshot. "
            "Do not edit files. Report concrete findings with file and line evidence, "
            "prioritized by impact; say explicitly when no issue is found."
        )
    else:
        contract = (
            "Act as an implementation worker in an isolated Git worktree. Make only "
            "the requested changes, run focused checks, and report changed files and "
            "verification. Do not commit or change branches; the harness captures your "
            "delta as a reviewable commit."
        )
    return f"{contract}\n\nTask:\n{task.prompt}"


def _final_answer(steps: Iterable[Step]) -> tuple[str, list[str]]:
    response: list[str] = []
    final = ""
    called_tool = False
    errors: list[str] = []
    for step in steps:
        if step.kind == "text":
            response.append(step.text)
        elif step.kind == "tool_call":
            called_tool = True
        elif step.kind == "usage":
            if response and not called_tool:
                final = "".join(response)
            response.clear()
            called_tool = False
        elif step.kind == "error":
            errors.append(step.text)
    if response and not called_tool:
        final = "".join(response)
    return final, errors


def validate_tasks(raw: object) -> list[SubagentTask]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("tasks must be a non-empty array")
    if len(raw) > MAX_TASKS:
        raise ValueError(f"tasks may contain at most {MAX_TASKS} entries")
    tasks = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"task {index} must be an object")
        prompt = item.get("prompt")
        role = item.get("role", "reviewer")
        plan_step = item.get("plan_step")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"task {index} prompt must be a non-empty string")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"task {index} prompt exceeds {MAX_PROMPT_CHARS} characters")
        if role not in ROLES:
            raise ValueError(f"task {index} role must be one of: {', '.join(ROLES)}")
        if plan_step is not None and (
            not isinstance(plan_step, str) or not plan_step.strip()
        ):
            raise ValueError(f"task {index} plan_step must be a non-empty string")
        tasks.append(
            SubagentTask(
                prompt=prompt.strip(),
                role=role,
                plan_step=plan_step.strip() if isinstance(plan_step, str) else None,
            )
        )
    return tasks


def advance_plan_for_tasks(
    plan_state: dict | None, tasks: list[SubagentTask], results: list[SubagentResult]
) -> dict | None:
    """Mark matching plan steps completed when their subagent succeeded."""
    if not isinstance(plan_state, dict):
        return plan_state
    steps = plan_state.get("plan")
    if not isinstance(steps, list):
        return plan_state
    completed_labels = {
        task.plan_step
        for task, result in zip(tasks, results)
        if result.ok and task.plan_step
    }
    if not completed_labels:
        return plan_state
    updated = []
    for item in steps:
        if not isinstance(item, dict):
            updated.append(item)
            continue
        step = item.get("step")
        status = item.get("status")
        if step in completed_labels and status != "completed":
            updated.append({"step": step, "status": "completed"})
        else:
            updated.append({"step": step, "status": status})
    if not any(item.get("status") == "in_progress" for item in updated if isinstance(item, dict)):
        for item in updated:
            if isinstance(item, dict) and item.get("status") == "pending":
                item["status"] = "in_progress"
                break
    return {**plan_state, "plan": updated}