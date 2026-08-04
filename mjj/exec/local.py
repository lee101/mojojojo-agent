"""Current-interpreter, mojosub, and local-jail execution."""

from __future__ import annotations

import ast
import contextlib
import importlib
import io
import linecache
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from ..platforms import split_command
from . import ExecutionResult
from .policy import acceleratable_functions

_CAPTURE_CHARS = 1024 * 1024


class BackendUnavailable(RuntimeError):
    """An optional execution backend is not usable on this machine."""


class _ExecutionTimedOut(BaseException):
    pass


class _CappedText(io.TextIOBase):
    """A bounded text sink that retains both context and diagnostics."""

    def __init__(self, limit: int = _CAPTURE_CHARS):
        self.limit = limit
        self.head_limit = int(limit * 0.55)
        self.tail_limit = limit - self.head_limit
        self.head = ""
        self.tail = ""
        self.total = 0

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, value) -> int:
        text = str(value)
        size = len(text)
        self.total += size
        if len(self.head) < self.head_limit:
            take = min(self.head_limit - len(self.head), size)
            self.head += text[:take]
            text = text[take:]
        if text:
            self.tail = (self.tail + text)[-self.tail_limit:]
        return size

    def getvalue(self) -> str:
        dropped = self.total - len(self.head) - len(self.tail)
        if dropped <= 0:
            return self.head + self.tail
        return self.head + f"\n… [{dropped} chars omitted] …\n" + self.tail


@contextlib.contextmanager
def _deadline(seconds: float):
    """Interrupt Python in the current interpreter and restore prior alarms."""
    deadline = time.monotonic() + seconds

    def expired(*_args):
        raise _ExecutionTimedOut()

    if _signal_deadline_available():
        old_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, expired)
        timer_started = time.monotonic()
        old_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            if old_timer[0] > 0:
                signal.setitimer(
                    signal.ITIMER_REAL,
                    max(0.000001, old_timer[0] - (time.monotonic() - timer_started)),
                    old_timer[1],
                )
        return

    # Signals can only be installed by the main thread. The trace deadline is
    # still hard for the pure-Python snippets policy permits in-process.
    previous = sys.gettrace()

    def trace(_frame, _event, _arg):
        if time.monotonic() >= deadline:
            expired()
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)


def _signal_deadline_available() -> bool:
    return (
        threading.current_thread() is threading.main_thread()
        and hasattr(signal, "SIGALRM")
        and hasattr(signal, "ITIMER_REAL")
        and hasattr(signal, "setitimer")
    )


def _run_current(
    code: str,
    *,
    timeout: float,
    cwd: Path,
    namespace_extra: dict | None = None,
    tree: ast.AST | None = None,
) -> tuple[ExecutionResult, dict]:
    stdout, stderr = _CappedText(), _CappedText()
    filename = str(cwd / "<mjj-py>")
    linecache.cache[filename] = (
        len(code),
        None,
        code.splitlines(keepends=True),
        filename,
    )
    namespace = {
        "__name__": "__main__",
        "__file__": filename,
        **(namespace_extra or {}),
    }
    started = time.perf_counter()
    exit_code = 0
    timed_out = False
    previous_cwd = Path.cwd()
    try:
        os.chdir(cwd)
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            _deadline(timeout),
        ):
            compiled = compile(tree if tree is not None else code, filename, "exec")
            exec(compiled, namespace)  # noqa: S102 - this module is the executor
    except _ExecutionTimedOut:
        timed_out = True
        exit_code = 124
        stderr.write(f"mjj: time limit exceeded after {timeout:g}s\n")
    except SystemExit as exc:
        if exc.code is None:
            exit_code = 0
        elif isinstance(exc.code, int):
            exit_code = exc.code
        else:
            exit_code = 1
            stderr.write(f"{exc.code}\n")
    except KeyboardInterrupt:
        exit_code = 130
        traceback.print_exc(file=stderr)
    except BaseException:  # user failures are results, including SyntaxError
        exit_code = 1
        traceback.print_exc(file=stderr)
    finally:
        os.chdir(previous_cwd)
        linecache.cache.pop(filename, None)
    result = ExecutionResult(
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        exit_code=exit_code,
        timed_out=timed_out,
        wall_ms=round((time.perf_counter() - started) * 1000.0, 3),
    )
    return result, namespace


def run_inproc(
    code: str, *, timeout: float = 10.0, cwd: Path | None = None
) -> ExecutionResult:
    result, _ = _run_current(
        code, timeout=timeout, cwd=Path.cwd() if cwd is None else Path(cwd)
    )
    return result


def run_accelerated(
    code: str, *, timeout: float = 10.0, cwd: Path | None = None
) -> ExecutionResult:
    """Tier eligible functions without ever waiting for a Mojo build."""
    workdir = Path.cwd() if cwd is None else Path(cwd)
    try:
        tree = ast.parse(code)
        names = acceleratable_functions(tree)
        if not names:
            result = run_inproc(code, timeout=timeout, cwd=workdir)
            result.path = "accelerated"
            result.requested_path = "accelerated"
            result.fallback = "no function matched the conservative accelerator subset"
            return result
        _configure_mojo_environment()
        mojosub = _load_mojosub()
    except Exception as exc:
        result = run_inproc(code, timeout=timeout, cwd=workdir)
        result.requested_path = "accelerated"
        result.fallback = f"mojosub unavailable: {type(exc).__name__}: {exc}"
        return result

    wrappers = []

    def accelerate(fn):
        wrapped = mojosub.jit(fn, mode="tiered", source=code)
        wrappers.append(wrapped)
        return wrapped

    selected = set(names)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in selected:
            node.decorator_list.insert(0, ast.Name(id="__mjj_accelerate", ctx=ast.Load()))
    ast.fix_missing_locations(tree)
    result, _ = _run_current(
        code,
        timeout=timeout,
        cwd=workdir,
        namespace_extra={"__mjj_accelerate": accelerate},
        tree=tree,
    )
    python_calls = sum(
        int(getattr(getattr(wrapper, "stats", None), "python_calls", 0))
        for wrapper in wrappers
    )
    native_calls = sum(
        int(getattr(getattr(wrapper, "stats", None), "mojo_calls", 0))
        for wrapper in wrappers
    )
    compiles = sum(
        int(getattr(getattr(wrapper, "stats", None), "compiles", 0))
        for wrapper in wrappers
    )
    queued = sum(
        len(getattr(wrapper, "_pending", ())) for wrapper in wrappers
    )
    result.path = "accelerated"
    result.native = native_calls > 0
    result.tier = (
        "mixed" if native_calls and python_calls
        else "native" if native_calls
        else "interpreted"
    )
    result.detail.update({
        "accelerated": names,
        "python_calls": python_calls,
        "native_calls": native_calls,
        "compiles": compiles,
        "compile_queued": queued,
    })
    return result


def _load_mojosub():
    """Import mojosub lazily, including an adjacent development checkout."""
    try:
        return importlib.import_module("mojosub")
    except ImportError as original:
        configured = os.environ.get("MJJ_MOJOSUB_PATH")
        adjacent = Path(__file__).resolve().parents[3] / "mojosub"
        candidate = Path(configured).expanduser() if configured else adjacent
        if not (candidate / "mojosub" / "__init__.py").is_file():
            raise original
        value = str(candidate)
        if value not in sys.path:
            sys.path.insert(0, value)
        return importlib.import_module("mojosub")


def _configure_mojo_environment() -> Path | None:
    """Make ``MODULAR_HOME`` agree with the compiler that will actually run."""
    binary: Path | None = None
    override = os.environ.get("MOJOSUB_MOJO", "")
    if override:
        argv = split_command(override)
        if len(argv) == 1:
            found = shutil.which(argv[0])
            if found:
                binary = Path(found).resolve()
    if binary is None:
        found = shutil.which("mojo")
        if found:
            binary = Path(found).resolve()
    if binary is None:
        project_value = os.environ.get("MOJOSUB_PIXI_PROJECT")
        project = (
            Path(project_value).expanduser()
            if project_value
            else Path(__file__).resolve().parents[3] / "mojosub"
        )
        candidates = (
            project / ".pixi" / "envs" / "default" / "bin" / "mojo",
            project / ".pixi" / "envs" / "default" / "Scripts" / "mojo.exe",
            project / ".pixi" / "envs" / "default" / "Library" / "bin" / "mojo.exe",
        )
        candidate = next((path for path in candidates if path.is_file()), None)
        if candidate is not None:
            binary = candidate.resolve()
            os.environ.setdefault("MOJOSUB_PIXI_PROJECT", str(project))
            os.environ.setdefault("MOJOSUB_MOJO", str(binary))
    if binary is not None:
        # Mojo's binary lives at <env>/bin/mojo, while MODULAR_HOME is the Max
        # package root at <env>/share/max. Pointing at <env> itself starts the
        # compiler but makes every build fail with "unable to locate std".
        os.environ["MODULAR_HOME"] = str(_modular_home(binary))
    return binary


def _modular_home(binary: Path) -> Path:
    parent = binary.parent
    if (
        parent.name.casefold() == "bin"
        and parent.parent.name.casefold() == "library"
    ):
        prefix = parent.parent.parent
    else:
        prefix = parent.parent
    candidates = (
        prefix / "share" / "max",
        prefix / "Library" / "share" / "max",
    )
    existing = next((path for path in candidates if path.is_dir()), None)
    if existing is not None:
        return existing
    return candidates[1] if binary.suffix.casefold() == ".exe" else candidates[0]


def run_sandbox(
    code: str,
    *,
    timeout: float = 10.0,
    packages: list[str] | None = None,
    fallback_cwd: Path | None = None,
) -> ExecutionResult:
    """Run in the host jail or loopback worker, degrading visibly to inproc."""
    failures: list[str] = []
    if not packages:
        try:
            return _run_jail(code, timeout=timeout)
        except BackendUnavailable as exc:
            failures.append(str(exc))
    try:
        from .remote import run_worker

        return run_worker(code, timeout=timeout, packages=packages)
    except BackendUnavailable as exc:
        failures.append(str(exc))
    result = run_inproc(
        code,
        timeout=timeout,
        cwd=Path.cwd() if fallback_cwd is None else fallback_cwd,
    )
    result.requested_path = "sandbox"
    result.fallback = "; ".join(failures) or "sandbox unavailable"
    return result


def _run_jail(code: str, *, timeout: float) -> ExecutionResult:
    """Use the installed root-owned jail without exposing any compiler."""
    jail = Path(os.environ.get("MOJOJOJO_JAIL", "/usr/local/bin/mojojail"))
    sudo = shutil.which("sudo")
    if not jail.is_file() or sudo is None:
        raise BackendUnavailable("local jail is not installed")
    data_root = Path(os.environ.get("MOJOJOJO_DATA", "/nvme0n1-disk/data/mojojojo"))
    run_root = data_root / "runs"
    try:
        run_root.mkdir(parents=True, exist_ok=True)
        work = run_root / uuid.uuid4().hex
        work.mkdir(mode=0o777)
        work.chmod(0o777)
        program = work / "program.py"
        program.write_text(code, encoding="utf-8")
        program.chmod(0o644)
    except OSError as exc:
        raise BackendUnavailable(f"could not stage a jail run: {exc}") from None

    stdout_path, stderr_path = work / "stdout", work / "stderr"
    seconds = max(1, min(86400, math.ceil(timeout)))
    command = [
        sudo, "-n", str(jail), str(seconds), "--work", str(work),
        "/usr/bin/env", "-i",
        "PATH=/usr/bin:/bin",
        f"HOME={work}",
        f"TMPDIR={work}",
        "PYTHONDONTWRITEBYTECODE=1",
        "/usr/bin/python3", str(program),
    ]
    started = time.perf_counter()
    timed_out = False
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                proc = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=seconds + 5,
                    check=False,
                )
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = 124
        out = _read_capped(stdout_path)
        err = _read_capped(stderr_path)
        if exit_code in (124, 137, -9, -24):
            timed_out = True
        if (
            exit_code in (1, 2)
            and not out
            and (err.lstrip().startswith("sudo:") or err.lstrip().startswith("mojojail:"))
        ):
            raise BackendUnavailable(err.strip()[:300])
        return ExecutionResult(
            stdout=out,
            stderr=err,
            exit_code=exit_code,
            wall_ms=round((time.perf_counter() - started) * 1000.0, 3),
            path="sandbox",
            tier="interpreted",
            timed_out=timed_out,
            detail={"isolated": True, "backend": "mojojail"},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _read_capped(path: Path, limit: int = _CAPTURE_CHARS) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size <= limit:
                return handle.read().decode("utf-8", "replace")
            head_size = int(limit * 0.55)
            tail_size = limit - head_size
            head = handle.read(head_size)
            handle.seek(-tail_size, os.SEEK_END)
            tail = handle.read(tail_size)
        return (
            head.decode("utf-8", "replace")
            + f"\n… [{size - limit} bytes omitted] …\n"
            + tail.decode("utf-8", "replace")
        )
    except OSError:
        return ""


__all__ = [
    "BackendUnavailable", "run_accelerated", "run_inproc", "run_sandbox",
]
