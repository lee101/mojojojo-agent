from __future__ import annotations

import json
import time
import urllib.error
from pathlib import Path

import pytest

from mjj.exec import execute
from mjj.exec import local, policy, remote
from mjj.ledger import Budget, Ledger
from mjj.tools.base import ToolContext
from mjj.tools.py_exec import TOOLS


def context(tmp_path: Path, *, budget: int = 1200) -> ToolContext:
    return ToolContext(
        cwd=tmp_path,
        ledger=Ledger(budget=Budget(py=budget)),
    )


def test_tool_contract_and_inproc_output(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_EXEC", "inproc")
    result = TOOLS[0].run({"code": "print(sum(range(10)))"}, context(tmp_path))
    assert result.ok
    assert "path=inproc tier=interpreted exit=0" in result.output
    assert "45" in result.output
    assert result.meta["wall_ms"] >= 0
    assert result.meta["native"] is False


def test_inproc_has_a_hard_timeout(tmp_path):
    started = time.perf_counter()
    result = local.run_inproc(
        "while True:\n    pass", timeout=0.05, cwd=tmp_path
    )
    assert result.timed_out and result.exit_code == 124
    assert "time limit exceeded" in result.stderr
    assert time.perf_counter() - started < 1.0


def test_traceback_is_at_the_tail_and_survives_ledger_clipping(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_EXEC", "inproc")
    ledger = Ledger(budget=Budget(py=80))
    ctx = ToolContext(cwd=tmp_path, ledger=ledger)
    code = "print('noise\\n' * 1000)\nraise ValueError('tail survives')"
    result = TOOLS[0].run({"code": code}, ctx)
    assert not result.ok
    assert "ValueError: tail survives" in result.output
    assert ledger.drops


def test_policy_is_safe_by_default_and_accelerates_numeric_loops():
    assert policy.choose_path("open('x', 'w').write('no')") == "sandbox"
    assert policy.choose_path("import socket\nsocket.create_connection(('x', 1))") == "sandbox"
    assert policy.choose_path("import unknown_package") == "sandbox"
    assert policy.choose_path("print(1 + 2)") == "inproc"
    numeric = (
        "def total(n: int) -> int:\n"
        "    out = 0\n"
        "    for i in range(n):\n"
        "        out += i\n"
        "    return out\n"
        "print(total(10))\n"
    )
    assert policy.choose_path(numeric) == "accelerated"


def test_where_and_environment_force_a_path(monkeypatch):
    monkeypatch.setenv("MJJ_EXEC", "remote")
    assert policy.choose_path("print(1)") == "remote"
    assert policy.choose_path("print(1)", where="inproc") == "inproc"
    with pytest.raises(ValueError):
        policy.choose_path("print(1)", where="somewhere")


def test_no_mojosub_degrades_to_inproc(tmp_path, monkeypatch):
    def missing():
        raise ImportError("deliberately absent")

    monkeypatch.setattr(local, "_load_mojosub", missing)
    code = (
        "def total(n: int) -> int:\n"
        "    out = 0\n"
        "    for i in range(n):\n"
        "        out += i\n"
        "    return out\n"
        "print(total(10))\n"
    )
    result = local.run_accelerated(code, cwd=tmp_path)
    assert result.ok and result.stdout == "45\n"
    assert result.path == "inproc"
    assert result.requested_path == "accelerated"
    assert "mojosub unavailable" in result.fallback


def test_accelerator_passes_source_and_reports_interpreted(tmp_path, monkeypatch):
    seen = {}

    class Stats:
        python_calls = 1
        mojo_calls = 0
        compiles = 0

    class Wrapped:
        _pending = {"building"}
        stats = Stats()

        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    class FakeMojosub:
        @staticmethod
        def jit(fn, **kwargs):
            seen.update(kwargs)
            return Wrapped(fn)

    monkeypatch.setattr(local, "_load_mojosub", lambda: FakeMojosub)
    monkeypatch.setattr(local, "_configure_mojo_environment", lambda: None)
    code = (
        "def total(n: int) -> int:\n"
        "    out = 0\n"
        "    for i in range(n): out += i\n"
        "    return out\n"
        "print(total(10))\n"
    )
    result = local.run_accelerated(code, cwd=tmp_path)
    assert result.ok and result.path == "accelerated"
    assert result.tier == "interpreted" and not result.native
    assert result.detail["compile_queued"] == 1
    assert seen == {"mode": "tiered", "source": code}


def test_modular_home_matches_the_resolved_mojo_binary(tmp_path, monkeypatch):
    binary = tmp_path / "env" / "bin" / "mojo"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("MOJOSUB_MOJO", str(binary))
    monkeypatch.setenv("MODULAR_HOME", "/stale/install")
    monkeypatch.setattr(local.shutil, "which", lambda value: str(binary))
    assert local._configure_mojo_environment() == binary.resolve()
    assert local.os.environ["MODULAR_HOME"] == str(
        binary.resolve().parent.parent / "share" / "max"
    )


def test_no_jail_and_no_worker_degrades_to_inproc(tmp_path, monkeypatch):
    monkeypatch.setattr(
        local, "_run_jail",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            local.BackendUnavailable("no jail")
        ),
    )
    monkeypatch.setattr(
        remote, "run_worker",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            local.BackendUnavailable("no worker")
        ),
    )
    result = local.run_sandbox("print('still runs')", fallback_cwd=tmp_path)
    assert result.ok and result.stdout == "still runs\n"
    assert result.path == "inproc" and result.requested_path == "sandbox"
    assert "no jail" in result.fallback and "no worker" in result.fallback


def test_remote_request_and_credit_cost(tmp_path, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, _limit):
            return json.dumps({
                "ok": True,
                "stdout": "remote\n",
                "stderr": "",
                "exit_code": 0,
                "wall_ms": 12.5,
                "billable_ms": 10.0,
                "isolated": True,
                "accel": {"native_calls": 2},
                "charge": {"credits": 3},
            }).encode()

    captured = {}

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("MJJ_MOJOJOJO_KEY", "mj_live_test")
    monkeypatch.setattr(remote.urllib.request, "urlopen", urlopen)
    result = remote.run_remote("print(1)", timeout=2, fallback_cwd=tmp_path)
    assert result.ok and result.path == "remote"
    assert result.native and result.tier == "native"
    assert result.credit_cost == 3
    assert captured["request"].full_url.endswith("/v1/run")
    assert captured["request"].headers["Authorization"] == "Bearer mj_live_test"


def test_remote_network_failure_degrades_to_inproc(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_MOJOJOJO_KEY", "mj_live_test")

    def offline(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(remote.urllib.request, "urlopen", offline)
    result = remote.run_remote("print('fallback')", fallback_cwd=tmp_path)
    assert result.ok and result.stdout == "fallback\n"
    assert result.path == "inproc" and result.requested_path == "remote"
    assert "could not reach" in result.fallback


def test_packages_choose_sandbox_and_bad_arguments_are_results(tmp_path):
    assert policy.choose_path("print(1)", packages=["numpy"]) == "sandbox"
    tool = TOOLS[0]
    ctx = context(tmp_path)
    assert not tool.run({}, ctx).ok
    assert not tool.run({"code": "print(1)", "timeout": "soon"}, ctx).ok
    assert not tool.run({"code": "print(1)", "packages": "numpy"}, ctx).ok


def test_public_execute_api(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_EXEC", "inproc")
    result = execute("print(__name__)", cwd=tmp_path)
    assert result.ok and result.stdout == "__main__\n"
