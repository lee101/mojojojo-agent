"""Stdlib HTTP clients for the local worker and mojojojo.app.nz."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import ExecutionResult
from .local import BackendUnavailable, run_inproc

_MAX_RESPONSE = 2 * 1024 * 1024


def run_worker(
    code: str,
    *,
    timeout: float = 10.0,
    packages: list[str] | None = None,
) -> ExecutionResult:
    endpoint = os.environ.get(
        "MJJ_MOJOJOJO_WORKER", "http://127.0.0.1:4342"
    ).rstrip("/")
    payload = _payload(code, timeout=timeout, packages=packages)
    data = _post(endpoint + "/run", payload, timeout=timeout + 12.0)
    return _from_response(data, path="sandbox", backend="worker")


def run_remote(
    code: str,
    *,
    timeout: float = 10.0,
    packages: list[str] | None = None,
    fallback_cwd: Path | None = None,
) -> ExecutionResult:
    """Run through the billed public API, or degrade visibly to inproc."""
    key = os.environ.get("MJJ_MOJOJOJO_KEY", "")
    if not key.startswith("mj_live_"):
        return _fallback(
            code,
            timeout,
            fallback_cwd,
            "MJJ_MOJOJOJO_KEY is missing or is not an mj_live_ key",
        )
    endpoint = os.environ.get(
        "MJJ_MOJOJOJO_URL", "https://mojojojo.app.nz"
    ).rstrip("/")
    try:
        data = _post(
            endpoint + "/v1/run",
            _payload(code, timeout=timeout, packages=packages),
            timeout=timeout + 20.0,
            token=key,
        )
    except BackendUnavailable as exc:
        return _fallback(code, timeout, fallback_cwd, str(exc))
    return _from_response(data, path="remote", backend="mojojojo.app.nz")


def _payload(code: str, *, timeout: float, packages: list[str] | None) -> dict:
    return {
        "code": code,
        "timeout_ms": max(100, int(timeout * 1000)),
        "packages": list(packages or []),
        "language": "python",
        "accel": True,
        "source": "mojojojo-agent",
    }


def _post(
    url: str,
    payload: dict,
    *,
    timeout: float,
    token: str = "",
) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "mojojojo-agent/py",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, timeout)) as response:
            raw = response.read(_MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read(4096)
        try:
            message = json.loads(raw).get("error")
        except (ValueError, AttributeError):
            message = raw.decode("utf-8", "replace")
        raise BackendUnavailable(
            f"{url} returned HTTP {exc.code}: {message or exc.reason}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise BackendUnavailable(f"could not reach {url}: {reason}") from None
    if len(raw) > _MAX_RESPONSE:
        raise BackendUnavailable("execution service response exceeded 2 MiB")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise BackendUnavailable(f"execution service returned invalid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise BackendUnavailable("execution service returned a non-object response")
    if parsed.get("error"):
        raise BackendUnavailable(str(parsed["error"]))
    return parsed


def _from_response(data: dict, *, path: str, backend: str) -> ExecutionResult:
    accel = data.get("accel") if isinstance(data.get("accel"), dict) else {}
    native_calls = int(accel.get("native_calls") or 0)
    charge = data.get("charge") if isinstance(data.get("charge"), dict) else {}
    credit_cost = float(charge.get("credits") or 0)
    return ExecutionResult(
        stdout=str(data.get("stdout") or ""),
        stderr=str(data.get("stderr") or ""),
        exit_code=int(data.get("exit_code") or 0),
        wall_ms=float(data.get("wall_ms") or 0.0),
        path=path,
        tier="native" if native_calls else "interpreted",
        timed_out=bool(data.get("timed_out")),
        native=native_calls > 0,
        credit_cost=credit_cost,
        detail={
            "backend": backend,
            "isolated": bool(data.get("isolated", path in {"sandbox", "remote"})),
            "billable_ms": float(data.get("billable_ms") or 0.0),
            "native_calls": native_calls,
            "credits": credit_cost,
        },
    )


def _fallback(
    code: str,
    timeout: float,
    cwd: Path | None,
    reason: str,
) -> ExecutionResult:
    result = run_inproc(
        code, timeout=timeout, cwd=Path.cwd() if cwd is None else Path(cwd)
    )
    result.requested_path = "remote"
    result.fallback = reason
    return result


__all__ = ["run_remote", "run_worker"]
