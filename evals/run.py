"""End-to-end evals: can the agent build and fix real things efficiently?

Each case under ``evals/cases/<name>/`` contains a repository, a prompt, and an
independent ``check.sh`` verifier. Runs happen in throwaway workspaces. Optional
artifacts preserve a summary, bounded trajectory, verifier output, and failed
workspace so a failure can be diagnosed without putting its full history back
into model context.

    python evals/run.py
    python evals/run.py port-to-mojo
    python evals/run.py --artifacts build/evals/experiment-a
    python evals/run.py port-mandelbrot-function --without-skills
    python evals/run.py --json build/evals/latest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mjj.agent import Agent  # noqa: E402
from mjj.ledger import Ledger  # noqa: E402
from mjj.model import ModelClient  # noqa: E402
from mjj.tools import build_registry  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
MAX_TRACE_TEXT = 4_000
MAX_VERIFIER_BYTES = 1 << 20


@dataclass
class Result:
    case: str
    passed: bool
    seconds: float
    verifier_seconds: float
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    tool_calls: int
    tool_tokens: int
    failure_stage: str = ""
    detail: str = ""
    artifact: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record(self) -> dict:
        record = asdict(self)
        record["total_tokens"] = self.total_tokens
        record["tokens_per_pass"] = self.total_tokens if self.passed else None
        return record

    def line(self) -> str:
        mark = "pass" if self.passed else "FAIL"
        stage = f" [{self.failure_stage}]" if self.failure_stage else ""
        detail = f"  {self.detail}" if self.detail else ""
        return (
            f"{mark:4} {self.case:28} {self.seconds:6.1f}s  "
            f"in {self.input_tokens:>7} ({self.cached_tokens:>7} cached)  "
            f"out {self.output_tokens:>6}  tools {self.tool_calls:>3}/"
            f"{self.tool_tokens:<5} tok {self.total_tokens:>7}{stage}{detail}"
        )


def run_case(
    case: Path,
    model: str,
    effort: str,
    *,
    artifacts: Path | None = None,
    keep_workspaces: bool = False,
    verbose: bool = False,
    check_timeout: float = 120.0,
    use_skills: bool = True,
) -> Result:
    error = _validate_case(case)
    if error:
        return _empty_failure(case.name, "case", error)

    prompt = (case / "prompt.txt").read_text(encoding="utf-8").strip()
    artifact = artifacts / case.name if artifacts is not None else None
    if artifact is not None:
        artifact.mkdir(parents=True, exist_ok=True)
    eval_environ = _eval_environment()
    for name in ("MJJ_EVAL_MOJOSUB_ROOT", "MOJOSUB_PIXI_PROJECT", "PYTHONPATH"):
        if name in eval_environ:
            os.environ[name] = eval_environ[name]
    variant = "skills" if use_skills else "no-skills"
    config = {"model": model, "effort": effort, "variant": variant}
    print(f"start {case.name} ({model}, {effort}, {variant})", flush=True)

    with tempfile.TemporaryDirectory(prefix="mjj-eval-") as tmp:
        work = Path(tmp) / case.name
        shutil.copytree(case / "repo", work)
        setup = case / "setup.sh"
        if setup.is_file():
            setup_result = subprocess.run(
                ["bash", str(setup)],
                cwd=work,
                capture_output=True,
                text=True,
                env=eval_environ,
            )
            if setup_result.returncode:
                result = _empty_failure(
                    case.name,
                    "setup",
                    _last_detail(setup_result.stdout, setup_result.stderr),
                )
                _persist_artifacts(
                    artifact,
                    case,
                    result,
                    setup_result.stdout,
                    setup_result.stderr,
                    work,
                    keep_workspace=True,
                    config=config,
                )
                return result

        client = ModelClient(model=model, effort=effort)
        ledger = Ledger()
        agent = Agent(
            registry=build_registry(
                disabled=("skill",) if not use_skills else (),
                include_user_skills=False,
            ),
            client=client,
            cwd=work,
            ledger=ledger,
            include_user_instructions=False,
        )
        trace = artifact / "trace.jsonl" if artifact is not None else None
        if trace is not None and trace.exists():
            trace.unlink()
        started = time.monotonic()
        detail = ""
        failure_stage = ""
        try:
            for step in agent.run(prompt):
                _record_step(trace, step)
                if verbose and step.kind in {
                    "tool_call",
                    "tool_result",
                    "error",
                    "usage",
                }:
                    label = f" {step.name}" if step.name else ""
                    print(f"  {case.name}: {step.kind}{label}", flush=True)
                if step.kind == "error":
                    failure_stage = "agent"
                    detail = step.text[:120]
        except Exception as exc:  # one broken case must not hide later cases
            failure_stage = "agent"
            detail = f"{type(exc).__name__}: {exc}"[:120]
        elapsed = time.monotonic() - started

        verify_started = time.monotonic()
        try:
            check = subprocess.run(
                ["bash", str(case / "check.sh")],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=check_timeout,
                env=eval_environ,
            )
            stdout, stderr = check.stdout, check.stderr
            passed = check.returncode == 0
            if not passed:
                failure_stage = "verifier"
                detail = _last_detail(stdout, stderr) or detail or "check failed"
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_output(exc.stdout)
            stderr = _decode_output(exc.stderr)
            passed = False
            failure_stage = "verifier-timeout"
            detail = f"check exceeded {check_timeout:g}s"
        verifier_seconds = time.monotonic() - verify_started
        usage = client.usage
        result = Result(
            case=case.name,
            passed=passed,
            seconds=elapsed,
            verifier_seconds=verifier_seconds,
            input_tokens=usage.input_tokens,
            cached_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            tool_calls=ledger.tool_calls,
            tool_tokens=ledger.tool_tokens,
            failure_stage="" if passed else failure_stage,
            detail="" if passed else detail,
            artifact=str(artifact) if artifact is not None else "",
        )
        _persist_artifacts(
            artifact,
            case,
            result,
            stdout,
            stderr,
            work,
            keep_workspace=keep_workspaces or not passed,
            config=config,
        )
        return result


def _validate_case(case: Path) -> str:
    for name in ("repo", "prompt.txt", "check.sh"):
        if not (case / name).exists():
            return f"missing {name}"
    if not (case / "repo").is_dir():
        return "repo must be a directory"
    if not (case / "prompt.txt").read_text(encoding="utf-8").strip():
        return "prompt.txt is empty"
    return ""


def _empty_failure(case: str, stage: str, detail: str) -> Result:
    return Result(case, False, 0.0, 0.0, 0, 0, 0, 0, 0, stage, detail[:120])


def _eval_environment() -> dict[str, str]:
    environ = os.environ.copy()
    peer = ROOT.parent / "mojosub"
    if peer.is_dir():
        environ.setdefault("MJJ_EVAL_MOJOSUB_ROOT", str(peer))
        environ.setdefault("MOJOSUB_PIXI_PROJECT", str(peer))
        current = environ.get("PYTHONPATH")
        environ["PYTHONPATH"] = str(peer.parent) + (
            os.pathsep + current if current else ""
        )
    return environ


def _record_step(path: Path | None, step) -> None:
    if path is None:
        return
    record = {
        "kind": step.kind,
        "name": step.name,
        "text": step.text[:MAX_TRACE_TEXT],
        "text_chars": len(step.text),
        "meta": step.meta,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def _persist_artifacts(
    artifact: Path | None,
    case: Path,
    result: Result,
    stdout: str,
    stderr: str,
    work: Path,
    *,
    keep_workspace: bool,
    config: dict[str, str] | None = None,
) -> None:
    if artifact is None:
        return
    _write_bounded(artifact / "verifier.stdout", stdout)
    _write_bounded(artifact / "verifier.stderr", stderr)
    manifest = {
        "result": result.record(),
        "case_sha256": _case_digest(case),
        "prompt_sha256": _sha256(case / "prompt.txt"),
        "check_sha256": _sha256(case / "check.sh"),
        "eval_config": config or {},
    }
    (artifact / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if keep_workspace:
        target = artifact / "workspace"
        if target.exists():
            target = artifact / f"workspace-{int(time.time())}"
        shutil.copytree(work, target, symlinks=True)


def _write_bounded(path: Path, value: str) -> None:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) > MAX_VERIFIER_BYTES:
        removed = len(encoded) - MAX_VERIFIER_BYTES
        encoded = encoded[:MAX_VERIFIER_BYTES] + (
            f"\n[truncated {removed} verifier bytes]\n".encode()
        )
    path.write_bytes(encoded)


def _case_digest(case: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in case.rglob("*") if item.is_file()):
        digest.update(path.relative_to(case).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _last_detail(stdout: str, stderr: str) -> str:
    lines = (stdout + stderr).strip().splitlines()
    return lines[-1][:120] if lines else ""


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="*", help="case names; default is all")
    parser.add_argument("--model", default=ModelClient.model)
    parser.add_argument("--effort", default="high")
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--keep-workspaces", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--check-timeout", type=float, default=120.0)
    parser.add_argument(
        "--without-skills",
        action="store_true",
        help="disable the skill tool for an isolated workflow A/B",
    )
    args = parser.parse_args()

    selected = (
        sorted(
            path
            for path in CASES.iterdir()
            if path.is_dir() and (not args.cases or path.name in args.cases)
        )
        if CASES.is_dir()
        else []
    )
    if not selected:
        print("no cases found", file=sys.stderr)
        return 2

    if args.artifacts is not None:
        args.artifacts.mkdir(parents=True, exist_ok=True)
    results = [
        run_case(
            case,
            args.model,
            args.effort,
            artifacts=args.artifacts,
            keep_workspaces=args.keep_workspaces,
            verbose=args.verbose,
            check_timeout=args.check_timeout,
            use_skills=not args.without_skills,
        )
        for case in selected
    ]
    for result in results:
        print(result.line())
    failed = sum(not result.passed for result in results)
    total_in = sum(result.input_tokens for result in results)
    total_out = sum(result.output_tokens for result in results)
    total_tools = sum(result.tool_tokens for result in results)
    print(
        f"\n{len(results) - failed}/{len(results)} passed · "
        f"{total_in} input · {total_out} output · {total_tools} tool-result tokens"
    )
    records = [result.record() for result in results]
    if args.json_path:
        output = Path(args.json_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    if args.artifacts is not None:
        (args.artifacts / "summary.json").write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
