from __future__ import annotations

import http.client
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from mjj.appnz import AuthStore, Billing, hash_token
from mjj.ledger import Ledger
from mjj.model import Event, ModelClient
from mjj.server import AgentHTTPServer, AgentService, RemoteCharge, ServerConfig, _server_registry
from mjj.tools.base import ToolContext


SHARED_SCHEMA = """
CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL DEFAULT '', salt TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMP NOT NULL, disabled_at TIMESTAMP,
  free_credits INTEGER NOT NULL DEFAULT 0, paid_credits INTEGER NOT NULL DEFAULT 0,
  plan_credits INTEGER NOT NULL DEFAULT 0, plan_credits_expire_at TIMESTAMP,
  handle TEXT NOT NULL DEFAULT '', is_admin INTEGER NOT NULL DEFAULT 0);
CREATE TABLE credit_ledger (id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  delta INTEGER NOT NULL, reason TEXT NOT NULL, app_id TEXT NOT NULL,
  source TEXT NOT NULL, balance_after INTEGER NOT NULL, created_at TIMESTAMP NOT NULL);
CREATE TABLE api_keys (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, app_id TEXT NOT NULL,
  name TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL, key_prefix TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL, last_used_at TIMESTAMP, revoked_at TIMESTAMP);
CREATE TABLE sso_sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL, expires_at TIMESTAMP NOT NULL);
"""


@pytest.fixture
def server_env(tmp_path):
    shared_path = tmp_path / "shared.db"
    local_path = tmp_path / "mojojojo.db"
    workspace = tmp_path / "workspaces"
    now = datetime.now(timezone.utc)
    token = "signed-in-browser"
    with sqlite3.connect(shared_path) as conn:
        conn.executescript(SHARED_SCHEMA)
        conn.execute(
            "INSERT INTO users "
            "(id,email,created_at,free_credits,handle) VALUES (?,?,?,?,?)",
            ("u1", "person@example.com", now.isoformat(), 10, "person"),
        )
        conn.execute(
            "INSERT INTO sso_sessions (id,user_id,created_at,expires_at) "
            "VALUES (?,?,?,?)",
            (
                hash_token(token),
                "u1",
                now.isoformat(),
                (now + timedelta(hours=1)).isoformat(),
            ),
        )
    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        database_path=local_path,
        appnz_database_path=shared_path,
        workspace_root=workspace,
        max_runs_per_user=1,
        stream_queue_size=8,
        tokens_per_credit=100,
    )
    auth = AuthStore(shared_path)
    service = AgentService(config, auth=auth, billing=Billing(local_path, auth, 100))
    server = AgentHTTPServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "server": server,
            "service": service,
            "shared": shared_path,
            "token": token,
            "port": server.server_address[1],
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(env, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", env["port"], timeout=5)
    encoded = None if body is None else json.dumps(body).encode()
    sent = dict(headers or {})
    if encoded is not None:
        sent.setdefault("Content-Type", "application/json")
        sent.setdefault("Content-Length", str(len(encoded)))
    conn.request(method, path, body=encoded, headers=sent)
    response = conn.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    conn.close()
    return result


def session_headers(env, **extra):
    return {"Cookie": f"appnz_session={env['token']}", **extra}


def sse_events(body):
    events = []
    current = {}
    for line in body.decode().splitlines():
        if not line:
            if "data" in current:
                events.append((current.get("event", ""), json.loads(current["data"])))
            current = {}
        elif line.startswith("event:"):
            current["event"] = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            current["data"] = line.partition(":")[2].strip()
    return events


def fake_stream(self, input_items, instructions, tools=None):
    self.usage.add(
        {
            "input_tokens": 80,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 30},
            "output_tokens_details": {"reasoning_tokens": 5},
        }
    )
    yield Event(type="response.output_text.delta", data={"delta": "hello"})
    yield Event(
        type="response.output_item.done",
        data={
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hello"}],
            }
        },
    )


def test_health_auth_and_appnz_only_cors(server_env):
    status, _, body = request(server_env, "GET", "/healthz")
    assert status == 200 and json.loads(body)["ok"] is True

    status, _, _ = request(
        server_env, "GET", "/v1/agent/sessions", headers={"Origin": "https://evil.test"}
    )
    assert status == 403

    status, response_headers, _ = request(
        server_env,
        "OPTIONS",
        "/v1/agent/runs",
        headers={"Origin": "https://editor.app.nz"},
    )
    assert status == 204
    assert response_headers["Access-Control-Allow-Origin"] == "https://editor.app.nz"

    status, _, _ = request(server_env, "GET", "/v1/agent/sessions")
    assert status == 401


def test_run_streams_steps_final_cost_and_resumes_session(
    server_env, monkeypatch
):
    monkeypatch.setattr(ModelClient, "stream", fake_stream)
    status, response_headers, body = request(
        server_env,
        "POST",
        "/v1/agent/runs",
        {"prompt": "say hello", "model": "gpt-test", "effort": "low"},
        session_headers(server_env, Origin="https://mojojojo.app.nz"),
    )
    assert status == 200
    assert response_headers["Content-Type"].startswith("text/event-stream")
    assert response_headers["Access-Control-Allow-Origin"] == "https://mojojojo.app.nz"
    assert response_headers["X-Run-ID"]

    events = sse_events(body)
    run = next(data for event, data in events if event == "run")
    assert any(event == "text" and data["text"] == "hello" for event, data in events)
    final = [
        data
        for event, data in events
        if event == "usage" and data["meta"].get("final")
    ]
    assert len(final) == 1
    assert final[0]["meta"]["tokens"] == 100
    assert final[0]["meta"]["cost"]["credits"] == 1
    assert final[0]["meta"]["cost"]["balance"] == 9

    with sqlite3.connect(server_env["shared"]) as conn:
        assert conn.execute(
            "SELECT delta,reason,app_id,source FROM credit_ledger"
        ).fetchone() == (-1, "agent:gpt-test:100", "mojojojo", "spend")

    status, _, body = request(
        server_env,
        "POST",
        "/v1/agent/runs",
        {"prompt": "again", "session": run["session"], "model": "gpt-test"},
        session_headers(server_env),
    )
    assert status == 200
    assert any(data["meta"].get("final") for event, data in sse_events(body) if event == "usage")

    status, _, body = request(
        server_env,
        "GET",
        "/v1/agent/sessions",
        headers=session_headers(server_env),
    )
    sessions = json.loads(body)["sessions"]
    assert status == 200
    assert [item["id"] for item in sessions] == [run["session"]]


@pytest.mark.parametrize("cwd", ["/tmp", "..", "missing"])
def test_request_cannot_select_a_host_cwd(server_env, monkeypatch, cwd):
    monkeypatch.setattr(ModelClient, "stream", fake_stream)
    status, _, body = request(
        server_env,
        "POST",
        "/v1/agent/runs",
        {"prompt": "hello", "cwd": cwd},
        session_headers(server_env),
    )
    assert status == 400
    assert "cwd" in json.loads(body)["error"]


def test_hosted_registry_blocks_absolute_tool_access(server_env, monkeypatch):
    from mjj.exec import local
    from mjj.tools import navigate as navigate_module

    workspace = server_env["service"].workspace_for("u1")
    registry = _server_registry(workspace)
    context = ToolContext(cwd=workspace, ledger=Ledger())
    read = registry.dispatch("read", '{"path":"/etc/passwd"}', context)
    shell = registry.dispatch(
        "shell", '{"command":["cat","/etc/passwd"]}', context
    )
    assert not read.ok and "workspace" in read.output
    assert not shell.ok and "workspace" in shell.output

    def unavailable(code, timeout):
        raise local.BackendUnavailable("test jail missing")

    monkeypatch.setattr(local, "_run_jail", unavailable)
    python = registry.dispatch("py", '{"code":"print(1)"}', context)
    assert not python.ok
    assert "sandbox unavailable" in python.output

    background = registry.dispatch(
        "shell", '{"command":["echo","ok"],"background":true}', context
    )
    assert not background.ok and "background" in background.output

    outside_navigation = registry.dispatch(
        "navigate", '{"action":"symbols","path":"/etc/passwd"}', context
    )
    assert not outside_navigation.ok and "workspace" in outside_navigation.output
    (workspace / "module.py").write_text("def hosted_symbol():\n    pass\n")
    monkeypatch.setattr(
        navigate_module,
        "server_for",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("hosted navigation must not start LSP")
        ),
    )
    hosted_navigation = registry.dispatch(
        "navigate", '{"action":"symbols","path":"module.py"}', context
    )
    assert hosted_navigation.ok and hosted_navigation.meta["strategy"] == "index"


def test_get_can_attach_to_a_live_stream(server_env, monkeypatch):
    first_sent = threading.Event()
    continue_run = threading.Event()

    def paused_stream(self, input_items, instructions, tools=None):
        yield Event(type="response.output_text.delta", data={"delta": "before"})
        first_sent.set()
        assert continue_run.wait(2)
        yield Event(type="response.output_text.delta", data={"delta": "after"})

    monkeypatch.setattr(ModelClient, "stream", paused_stream)
    payload = json.dumps({"prompt": "pause"}).encode()
    primary = http.client.HTTPConnection("127.0.0.1", server_env["port"], timeout=5)
    primary.request(
        "POST",
        "/v1/agent/runs",
        body=payload,
        headers={
            **session_headers(server_env),
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    primary_response = primary.getresponse()
    run_id = primary_response.getheader("X-Run-ID")
    assert first_sent.wait(2)

    attached = http.client.HTTPConnection("127.0.0.1", server_env["port"], timeout=5)
    attached.request(
        "GET",
        f"/v1/agent/runs/{run_id}",
        headers=session_headers(server_env),
    )
    attached_response = attached.getresponse()
    assert attached_response.status == 200
    continue_run.set()
    primary_events = sse_events(primary_response.read())
    attached_events = sse_events(attached_response.read())
    primary.close()
    attached.close()
    assert any(data.get("text") == "after" for _, data in primary_events)
    assert any(data.get("text") == "after" for _, data in attached_events)


def test_concurrent_cap_and_interrupt_stop_the_generator(server_env, monkeypatch):
    closed = threading.Event()

    def slow_stream(self, input_items, instructions, tools=None):
        try:
            while True:
                yield Event(
                    type="response.output_text.delta", data={"delta": "working"}
                )
                time.sleep(0.01)
        finally:
            closed.set()

    monkeypatch.setattr(ModelClient, "stream", slow_stream)
    first = http.client.HTTPConnection("127.0.0.1", server_env["port"], timeout=5)
    payload = json.dumps({"prompt": "keep working"}).encode()
    first.request(
        "POST",
        "/v1/agent/runs",
        body=payload,
        headers={
            **session_headers(server_env),
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    response = first.getresponse()
    assert response.status == 200
    run_id = response.getheader("X-Run-ID")

    status, _, _ = request(
        server_env,
        "POST",
        "/v1/agent/runs",
        {"prompt": "second"},
        session_headers(server_env),
    )
    assert status == 429

    status, _, body = request(
        server_env,
        "POST",
        f"/v1/agent/runs/{run_id}/interrupt",
        {},
        session_headers(server_env),
    )
    assert status == 202
    assert json.loads(body)["status"] == "interrupted"
    response.read()
    first.close()
    assert closed.wait(2)


def test_live_run_accepts_queued_steering(server_env, monkeypatch):
    first_response = threading.Event()
    release = threading.Event()
    rounds = 0

    def steerable_stream(self, input_items, instructions, tools=None):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            first_response.set()
            assert release.wait(2)
            yield Event(
                type="response.output_item.done",
                data={
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "first"}],
                    }
                },
            )
        else:
            assert "new priority" in str(input_items)
            yield Event(
                type="response.output_item.done",
                data={
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "second"}],
                    }
                },
            )

    monkeypatch.setattr(ModelClient, "stream", steerable_stream)
    payload = json.dumps({"prompt": "start"}).encode()
    primary = http.client.HTTPConnection(
        "127.0.0.1", server_env["port"], timeout=5
    )
    primary.request(
        "POST",
        "/v1/agent/runs",
        body=payload,
        headers={
            **session_headers(server_env),
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    response = primary.getresponse()
    run_id = response.getheader("X-Run-ID")
    assert first_response.wait(2)

    status, _, body = request(
        server_env,
        "POST",
        f"/v1/agent/runs/{run_id}/steer",
        {"prompt": "new priority"},
        session_headers(server_env),
    )
    assert status == 202
    assert json.loads(body)["status"] == "queued"
    release.set()
    events = sse_events(response.read())
    primary.close()

    assert rounds == 2
    assert any(event == "steering" for event, _data in events)


def test_disconnecting_client_cancels_instead_of_orphaning(
    server_env, monkeypatch
):
    closed = threading.Event()

    def noisy_stream(self, input_items, instructions, tools=None):
        try:
            while True:
                yield Event(
                    type="response.output_text.delta", data={"delta": "x" * 32_768}
                )
        finally:
            closed.set()

    monkeypatch.setattr(ModelClient, "stream", noisy_stream)
    conn = http.client.HTTPConnection("127.0.0.1", server_env["port"], timeout=5)
    payload = json.dumps({"prompt": "stream forever"}).encode()
    conn.request(
        "POST",
        "/v1/agent/runs",
        body=payload,
        headers={
            **session_headers(server_env),
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    response = conn.getresponse()
    assert response.status == 200
    response.close()
    conn.close()
    assert closed.wait(3)


def test_trusted_proxy_identity_runs_and_settles_without_shared_database(
    tmp_path, monkeypatch
):
    charges = []

    class Billing:
        def charge(self, user, model, tokens, run_id):
            charges.append((user.id, model, tokens, run_id))
            return RemoteCharge({"tokens": tokens, "charged_credits": 1, "balance": 9})

    monkeypatch.setattr(ModelClient, "stream", fake_stream)
    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        workspace_root=tmp_path / "workspaces",
        service_token="internal-secret",
    )
    service = AgentService(config, billing=Billing())
    server = AgentHTTPServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {"port": server.server_address[1]}
    try:
        status, _, body = request(env, "POST", "/v1/agent/runs", {"prompt": "hello"})
        assert status == 401
        assert b"service identity" in body

        headers = {
            "Authorization": "Bearer internal-secret",
            "X-Mojojojo-User": "user_abcdefgh",
        }
        status, response_headers, body = request(
            env, "POST", "/v1/agent/runs", {"prompt": "hello"}, headers
        )
        assert status == 200
        assert response_headers["X-Run-ID"]
        events = sse_events(body)
        usage = [data for event, data in events if event == "usage"][-1]
        assert usage["meta"]["cost"]["charged_credits"] == 1
        assert charges and charges[0][:3] == ("user_abcdefgh", "gpt-5.6-sol", 100)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_health_is_available_on_both_conventional_paths(server_env):
    for path in ("/health", "/healthz"):
        status, _, body = request(server_env, "GET", path)
        assert status == 200
        assert json.loads(body)["service"] == "mojojojo-agent"
