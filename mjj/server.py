"""Stdlib HTTP/SSE backend for the mojojojo agent harness."""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shlex
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from .agent import Agent, Step
from .appnz import AuthStore, Billing, User
from .ledger import Ledger
from .model import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    READ_TIMEOUT,
    ModelClient,
    ModelError,
    _decode_sse,
    _retryable_message,
)
from .session import Session, load_items
from .tools import build_registry
from .tools.base import Registry, ToolContext, ToolResult

_RUN_PATH = re.compile(r"^/v1/agent/runs/([A-Za-z0-9_-]{8,64})$")
_INTERRUPT_PATH = re.compile(
    r"^/v1/agent/runs/([A-Za-z0-9_-]{8,64})/interrupt$"
)
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_END = object()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() == "true"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 4343
    database_path: Path = Path("/nvme0n1-disk/data/mojojojo/mojojojo.db")
    appnz_database_path: Path = Path("/nvme0n1-disk/data/appnz-sso.db")
    workspace_root: Path = Path("/nvme0n1-disk/data/mojojojo/agent-workspaces")
    allow_anonymous: bool = False
    max_runs_per_user: int = 2
    stream_queue_size: int = 128
    max_body_bytes: int = 1 << 20
    max_prompt_chars: int = 256_000
    tokens_per_credit: float = 1000.0

    @classmethod
    def from_env(cls) -> "ServerConfig":
        return cls(
            host=os.environ.get("MJJ_SERVER_HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", os.environ.get("MJJ_SERVER_PORT", "4343"))),
            database_path=Path(
                os.environ.get(
                    "DB_PATH", "/nvme0n1-disk/data/mojojojo/mojojojo.db"
                )
            ),
            appnz_database_path=Path(
                os.environ.get(
                    "APPNZ_DATABASE_PATH", "/nvme0n1-disk/data/appnz-sso.db"
                )
            ),
            workspace_root=Path(
                os.environ.get(
                    "MJJ_WORKSPACE_ROOT",
                    os.environ.get(
                        "MJJ_SANDBOX_ROOT",
                        "/nvme0n1-disk/data/mojojojo/agent-workspaces",
                    ),
                )
            ),
            allow_anonymous=_env_bool("ALLOW_ANONYMOUS", False),
            max_runs_per_user=max(
                1, int(os.environ.get("MJJ_MAX_RUNS_PER_USER", "2"))
            ),
            stream_queue_size=max(
                8, int(os.environ.get("MJJ_STREAM_QUEUE_SIZE", "128"))
            ),
            max_body_bytes=max(
                1024, int(os.environ.get("MJJ_MAX_BODY_BYTES", str(1 << 20)))
            ),
            max_prompt_chars=max(
                1, int(os.environ.get("MJJ_MAX_PROMPT_CHARS", "256000"))
            ),
            tokens_per_credit=float(
                os.environ.get(
                    "TOKENS_PER_CREDIT",
                    os.environ.get("MJJ_TOKENS_PER_CREDIT", "1000"),
                )
            ),
        )


class RequestError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class InterruptibleModelClient(ModelClient):
    """Close the active response when its run is interrupted.

    ``Agent`` is synchronous, so a stop flag alone would only be noticed after
    the next model event.  Closing the response also wakes a blocked SSE read.
    """

    def __init__(self, *args, cancel: threading.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel = cancel

    def _stream_once(self, credential, body) -> Iterator:
        if self.cancel.is_set():
            return
        url = credential.base_url.rstrip("/") + "/responses"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST"
        )
        for key, value in credential.headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")
        try:
            resp = urllib.request.urlopen(req, timeout=READ_TIMEOUT)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", "replace").strip()
            raise ModelError(
                f"HTTP {exc.code}: {detail[:600]}",
                status=exc.code,
                retryable=exc.code in (401, 408, 409, 429, 500, 502, 503, 504),
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelError(f"connection failed: {exc}", retryable=True) from exc

        ended = threading.Event()

        def close_on_cancel() -> None:
            while not ended.is_set():
                if self.cancel.wait(0.25):
                    try:
                        resp.close()
                    except Exception:
                        pass
                    return

        watcher = threading.Thread(
            target=close_on_cancel, name="mjj-model-cancel", daemon=True
        )
        watcher.start()
        try:
            with resp:
                for event in _decode_sse(resp):
                    if self.cancel.is_set():
                        return
                    if event.type == "response.completed":
                        usage = (event.data.get("response") or {}).get("usage") or {}
                        self.usage.add(usage)
                    if event.type in ("response.failed", "error"):
                        err = event.data.get("error") or event.data
                        message = (
                            err.get("message")
                            if isinstance(err, dict)
                            else str(err)
                        )
                        raise ModelError(
                            f"stream failed: {message}",
                            retryable=_retryable_message(str(message)),
                        )
                    yield event
        finally:
            ended.set()


class _WorkspaceTool:
    """Keep the hosted tool surface inside one user's workspace."""

    def __init__(self, tool, workspace: Path):
        self._tool = tool
        self._workspace = workspace.resolve()
        self.name = tool.name
        self.description = tool.description
        self.parameters = tool.parameters

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        error = self._validate(args, ctx)
        if error:
            return ToolResult.error(ctx.ledger.clip(self.name, error))
        if self.name == "py":
            return self._run_python(args, ctx)
        return self._tool.run(args, ctx)

    def _run_python(self, args: dict, ctx: ToolContext) -> ToolResult:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return ToolResult.error(
                ctx.ledger.clip("py", "code must be a non-empty string")
            )
        timeout = args.get("timeout", 10.0)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0.01 <= float(timeout) <= 300
        ):
            return ToolResult.error(
                ctx.ledger.clip("py", "timeout must be between 0.01 and 300 seconds")
            )
        if args.get("packages"):
            return ToolResult.error(
                ctx.ledger.clip(
                    "py", "packages are unavailable in the hosted local jail"
                )
            )
        # Do not call the public run_sandbox helper here: its correct CLI
        # degradation is to CPython, which would be a host escape in a
        # multi-user server.  Hosted execution fails closed when the jail is
        # unavailable.
        from .exec.local import BackendUnavailable, _run_jail

        try:
            result = _run_jail(code, timeout=float(timeout))
        except BackendUnavailable as exc:
            return ToolResult.error(
                ctx.ledger.clip("py", f"hosted Python sandbox unavailable: {exc}")
            )
        rendered = (
            f"path={result.path} tier={result.tier} exit={result.exit_code} "
            f"wall_ms={result.wall_ms:.3f}\n"
            f"stdout:\n{result.stdout.rstrip()}\n"
            f"stderr:\n{result.stderr.rstrip()}\n"
        )
        return ToolResult(
            output=ctx.ledger.clip(
                "py", rendered, hint="rerun with less program output"
            ),
            ok=result.ok,
            meta=result.metadata(),
        )

    def _validate(self, args: dict, ctx: ToolContext) -> str:
        if self.name in ("read", "list", "search") and args.get("path") is not None:
            if not self._inside(ctx, args["path"]):
                return "path must stay inside the user workspace"
        if self.name != "shell":
            return ""
        if args.get("shell", False):
            return "shell=true is unavailable on the hosted agent"
        if args.get("cwd") is not None and not self._inside(ctx, args["cwd"]):
            return "cwd must stay inside the user workspace"
        command = args.get("command")
        if isinstance(command, str):
            try:
                parts = shlex.split(command)
            except ValueError:
                return ""
        elif isinstance(command, list):
            parts = command
        else:
            return ""
        for index, part in enumerate(parts):
            if not isinstance(part, str):
                continue
            value = part.partition("=")[2] if "=" in part else part
            if index == 0 and (value.startswith(("/", "~"))):
                return "command executable must come from the server allowlist"
            if value.startswith("~") or ".." in Path(value).parts:
                return "command paths must stay inside the user workspace"
            if value.startswith("/") and not self._inside(ctx, value):
                return "command paths must stay inside the user workspace"
            candidate = (ctx.cwd / value).resolve() if value and not value.startswith("-") else None
            if candidate is not None and candidate.exists():
                try:
                    candidate.relative_to(self._workspace)
                except ValueError:
                    return "command paths must stay inside the user workspace"
        return ""

    def _inside(self, ctx: ToolContext, value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = ctx.cwd / candidate
        try:
            candidate.resolve().relative_to(self._workspace)
            return True
        except (OSError, ValueError):
            return False


def _server_registry(workspace: Path) -> Registry:
    # Hosted users may load skills from their own workspace, never from the
    # service account's ~/.codex or ~/.claude directories.
    source = build_registry(include_user_skills=False)
    registry = Registry()
    for tool in source.tools.values():
        registry.add(_WorkspaceTool(tool, workspace))
    return registry


@dataclass
class RunState:
    id: str
    user_key: str
    user: User | None
    model: str
    effort: str
    prompt: str
    cwd: Path
    session: Session
    items: list[dict]
    queue_size: int
    events: queue.Queue = field(init=False)
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    attach_lock: threading.Lock = field(default_factory=threading.Lock)
    attached: bool = False
    mirrors: dict[str, queue.Queue] = field(default_factory=dict)
    status: str = "running"

    def __post_init__(self) -> None:
        self.events = queue.Queue(maxsize=self.queue_size)

    def emit(self, step: Step) -> bool:
        payload = asdict(step)
        if not self._put(self.events, payload):
            return False
        with self.attach_lock:
            mirrors = list(self.mirrors.values())
        for target in mirrors:
            if not self._put(target, payload):
                return False
        return True

    def _put(self, target: queue.Queue, payload: object) -> bool:
        while not self.cancel.is_set():
            try:
                target.put(payload, timeout=0.25)
                return True
            except queue.Full:
                continue
        return False

    def finish(self) -> None:
        self.done.set()
        with self.attach_lock:
            targets = [self.events, *self.mirrors.values()]
        for target in targets:
            self._finish_queue(target)

    def _finish_queue(self, target: queue.Queue) -> None:
        while True:
            try:
                target.put(_END, timeout=0.25)
                return
            except queue.Full:
                if self.cancel.is_set():
                    try:
                        target.get_nowait()
                    except queue.Empty:
                        pass


class RunManager:
    def __init__(self, service: "AgentService"):
        self.service = service
        self._lock = threading.Lock()
        self._runs: dict[str, RunState] = {}
        self._reservations: dict[str, int] = {}

    def create(
        self,
        user_key: str,
        user: User | None,
        payload: dict,
        cwd: Path,
        session_factory,
    ) -> RunState:
        with self._lock:
            active = sum(
                state.user_key == user_key and not state.done.is_set()
                for state in self._runs.values()
            ) + self._reservations.get(user_key, 0)
            if active >= self.service.config.max_runs_per_user:
                raise RequestError(429, "concurrent run limit reached")
            self._reservations[user_key] = (
                self._reservations.get(user_key, 0) + 1
            )
        session = None
        try:
            session, items = session_factory()
            run_id = secrets.token_urlsafe(12)
            state = RunState(
                id=run_id,
                user_key=user_key,
                user=user,
                model=payload["model"],
                effort=payload["effort"],
                prompt=payload["prompt"],
                cwd=cwd,
                session=session,
                items=items,
                queue_size=self.service.config.stream_queue_size,
            )
        except Exception:
            if session is not None:
                session.close()
            with self._lock:
                self._unreserve(user_key)
            raise
        with self._lock:
            self._unreserve(user_key)
            self._runs[run_id] = state
        thread = threading.Thread(
            target=self.service._produce,
            args=(state,),
            name=f"mjj-run-{run_id}",
            daemon=True,
        )
        thread.start()
        return state

    def _unreserve(self, user_key: str) -> None:
        remaining = self._reservations.get(user_key, 0) - 1
        if remaining > 0:
            self._reservations[user_key] = remaining
        else:
            self._reservations.pop(user_key, None)

    def get(self, run_id: str, user_key: str) -> RunState | None:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None or state.user_key != user_key:
                return None
            return state

    def release(self, state: RunState) -> None:
        if not state.done.is_set():
            return
        with self._lock:
            if self._runs.get(state.id) is state:
                self._runs.pop(state.id, None)


class AgentService:
    def __init__(
        self,
        config: ServerConfig,
        auth: AuthStore | None = None,
        billing: Billing | None = None,
    ):
        self.config = config
        self.config.workspace_root.mkdir(parents=True, exist_ok=True)
        self.auth = auth or AuthStore(config.appnz_database_path)
        self.billing = billing or Billing(
            config.database_path, self.auth, config.tokens_per_credit
        )
        self.runs = RunManager(self)

    def authenticate(self, handler: BaseHTTPRequestHandler) -> tuple[str, User | None]:
        user = self.auth.user_from_headers(handler.headers)
        if user is not None:
            return user.id, user
        if self.auth.has_presented_api_key(handler.headers):
            raise RequestError(401, "invalid or revoked API key")
        if not self.config.allow_anonymous:
            raise RequestError(401, "app.nz sign-in or an mj_live_ API key is required")
        peer = str(handler.client_address[0])
        anonymous = "anonymous-" + _short_hash(peer)
        return anonymous, None

    def prepare_run(
        self, handler: BaseHTTPRequestHandler, payload: object
    ) -> RunState:
        user_key, user = self.authenticate(handler)
        if not isinstance(payload, dict):
            raise RequestError(400, "JSON body must be an object")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise RequestError(400, "prompt is required")
        if len(prompt) > self.config.max_prompt_chars:
            raise RequestError(413, "prompt is too large")

        model = payload.get("model") or DEFAULT_MODEL
        effort = payload.get("effort") or DEFAULT_EFFORT
        session_id = payload.get("session")
        cwd_value = payload.get("cwd") or ""
        if not isinstance(model, str) or not _MODEL.fullmatch(model):
            raise RequestError(400, "invalid model")
        if effort not in ("none", "minimal", "low", "medium", "high", "xhigh", "max"):
            raise RequestError(
                400,
                "effort must be none, minimal, low, medium, high, xhigh, or max",
            )
        if not isinstance(cwd_value, str):
            raise RequestError(400, "cwd must be a relative path")
        if session_id is not None and (
            not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id)
        ):
            raise RequestError(400, "invalid session")
        if user is not None and user.credits < 1:
            raise RequestError(402, "out of credits")

        workspace = self.workspace_for(user_key)
        cwd = self.resolve_cwd(workspace, cwd_value)
        normalized = {
            "prompt": prompt,
            "model": model,
            "effort": effort,
        }
        return self.runs.create(
            user_key,
            user,
            normalized,
            cwd,
            lambda: self.open_session(
                workspace, session_id, cwd, user_key
            ),
        )

    def workspace_for(self, user_key: str) -> Path:
        root = self.config.workspace_root.resolve()
        workspace = root / _short_hash(user_key)
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        return workspace.resolve()

    def resolve_cwd(self, workspace: Path, value: str) -> Path:
        relative = Path(value)
        if relative.is_absolute():
            raise RequestError(400, "cwd must be relative to the user workspace")
        candidate = (workspace / relative).resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError as exc:
            raise RequestError(400, "cwd escapes the user workspace") from exc
        if not candidate.is_dir():
            raise RequestError(400, "cwd does not exist in the user workspace")
        return candidate

    def open_session(
        self, workspace: Path, session_id: str | None, cwd: Path, user_key: str
    ) -> tuple[Session, list[dict]]:
        directory = workspace / ".mjj" / "sessions"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if session_id:
            matches = sorted(directory.glob(f"*-{session_id}.jsonl"))
            if not matches:
                raise RequestError(404, "session not found")
            path = matches[-1]
            return Session(id=session_id, path=path), load_items(path)
        new_id = secrets.token_hex(8)
        session = Session(
            path=directory
            / f"{time.strftime('%Y%m%dT%H%M%S')}-{new_id}.jsonl",
            id=new_id,
            meta={"cwd": str(cwd), "owner": _short_hash(user_key)},
        )
        return session, []

    def list_sessions(self, user_key: str) -> list[dict]:
        directory = self.workspace_for(user_key) / ".mjj" / "sessions"
        if not directory.exists():
            return []
        result = []
        for path in sorted(directory.glob("*.jsonl"), reverse=True)[:200]:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    first = handle.readline(65_537)
                meta = json.loads(first) if len(first) <= 65_536 else {}
            except (OSError, ValueError):
                meta = {}
            stat = path.stat()
            result.append(
                {
                    "id": meta.get("id") or path.stem.rsplit("-", 1)[-1],
                    "started": meta.get("started") or stat.st_mtime,
                    "cwd": meta.get("cwd") or "",
                }
            )
        return result

    def _produce(self, state: RunState) -> None:
        client = InterruptibleModelClient(
            model=state.model, effort=state.effort, cancel=state.cancel
        )
        agent = Agent(
            registry=_server_registry(self.workspace_for(state.user_key)),
            client=client,
            cwd=state.cwd,
            ledger=Ledger(),
            session=state.session,
            approve=lambda _name, _request: False,
        )
        agent.items = state.items
        failed = False
        try:
            steps = agent.run(state.prompt)
            try:
                for step in steps:
                    if state.cancel.is_set():
                        state.status = "interrupted"
                        break
                    failed = failed or step.kind == "error"
                    if not state.emit(step):
                        state.status = "interrupted"
                        break
            finally:
                close = getattr(steps, "close", None)
                if close:
                    close()

            tokens = client.usage.input_tokens + client.usage.output_tokens
            charge = None
            billing_error = ""
            if state.user is not None:
                try:
                    charge = self.billing.charge(
                        state.user, state.model, tokens, run_id=state.id
                    ).as_dict()
                except Exception as exc:
                    billing_error = f"{type(exc).__name__}: {exc}"
            if not state.cancel.is_set():
                meta = {
                    "final": True,
                    "run_id": state.id,
                    "tokens": tokens,
                    "usage": asdict(client.usage),
                    "tools": agent.ledger.summary(),
                    "cost": charge,
                    "anonymous": state.user is None,
                }
                if billing_error:
                    meta["billing_error"] = billing_error
                state.emit(
                    Step(
                        kind="usage",
                        text=client.usage.summary(),
                        meta=meta,
                    )
                )
                state.status = "error" if failed else "completed"
            elif state.status == "running":
                state.status = "interrupted"
            state.session.note(
                run_id=state.id,
                status=state.status,
                usage=client.usage.summary(),
                tools=agent.ledger.summary(),
                cost=charge,
            )
        except Exception as exc:
            state.status = "error"
            if not state.cancel.is_set():
                state.emit(
                    Step(kind="error", text=f"{type(exc).__name__}: {exc}")
                )
        finally:
            state.session.close()
            state.finish()
            self.runs.release(state)

    def interrupt(self, state: RunState) -> None:
        state.status = "interrupted"
        state.cancel.set()


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: AgentService):
        self.service = service
        super().__init__(address, AgentRequestHandler)


class AgentRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mojojojo-agent/0.1"

    @property
    def service(self) -> AgentService:
        return self.server.service  # type: ignore[attr-defined]

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self._json(403, {"error": "origin is not allowed"})
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._check_origin():
            return
        if self.path == "/healthz":
            self._json(200, {"ok": True, "service": "mojojojo-agent"})
            return
        if self.path == "/v1/agent/sessions":
            try:
                user_key, _ = self.service.authenticate(self)
                self._json(
                    200, {"sessions": self.service.list_sessions(user_key)}
                )
            except RequestError as exc:
                self._json(exc.status, {"error": str(exc)})
            return
        match = _RUN_PATH.fullmatch(urlsplit(self.path).path)
        if match:
            try:
                user_key, _ = self.service.authenticate(self)
                state = self.service.runs.get(match.group(1), user_key)
                if state is None:
                    raise RequestError(404, "live run not found")
                self._stream(state, mirror=True)
            except RequestError as exc:
                self._json(exc.status, {"error": str(exc)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._check_origin():
            return
        if self.path == "/v1/agent/runs":
            try:
                payload = self._read_json()
                state = self.service.prepare_run(self, payload)
                self._stream(state, mirror=False)
            except RequestError as exc:
                self._json(exc.status, {"error": str(exc)})
            return
        match = _INTERRUPT_PATH.fullmatch(urlsplit(self.path).path)
        if match:
            try:
                user_key, _ = self.service.authenticate(self)
                state = self.service.runs.get(match.group(1), user_key)
                if state is None:
                    raise RequestError(404, "live run not found")
                self.service.interrupt(state)
                self._json(
                    202, {"ok": True, "id": state.id, "status": state.status}
                )
            except RequestError as exc:
                self._json(exc.status, {"error": str(exc)})
            return
        self._json(404, {"error": "not found"})

    def _read_json(self) -> object:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise RequestError(400, "invalid Content-Length") from exc
        if length <= 0:
            raise RequestError(400, "JSON body is required")
        if length > self.service.config.max_body_bytes:
            raise RequestError(413, "request body is too large")
        body = self.rfile.read(length)
        try:
            return json.loads(body)
        except (UnicodeDecodeError, ValueError) as exc:
            raise RequestError(400, "invalid JSON body") from exc

    def _stream(self, state: RunState, mirror: bool) -> None:
        mirror_id = ""
        with state.attach_lock:
            if state.done.is_set() and state.events.empty():
                raise RequestError(410, "run is no longer live")
            if mirror:
                if len(state.mirrors) >= 4:
                    raise RequestError(429, "run attachment limit reached")
                mirror_id = secrets.token_urlsafe(8)
                events = queue.Queue(
                    maxsize=self.service.config.stream_queue_size
                )
                state.mirrors[mirror_id] = events
            else:
                if state.attached:
                    raise RequestError(409, "run already has a controlling stream")
                state.attached = True
                events = state.events
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Run-ID", state.id)
        self.send_header("Connection", "close")
        self.end_headers()
        disconnected = False
        try:
            self._write_sse(
                "run",
                {"id": state.id, "session": state.session.id, "status": "running"},
            )
            while True:
                try:
                    item = events.get(timeout=1.0)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                if item is _END:
                    break
                self._write_sse(str(item.get("kind") or "message"), item)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            disconnected = True
            self.service.interrupt(state)
        finally:
            self.close_connection = True
            with state.attach_lock:
                if mirror:
                    state.mirrors.pop(mirror_id, None)
                else:
                    state.attached = False
            if disconnected and not state.done.wait(2.0):
                state.cancel.set()
            self.service.runs.release(state)

    def _write_sse(self, event: str, data: dict) -> None:
        encoded = json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.wfile.write(b"event: " + event.encode("ascii", "replace") + b"\n")
        self.wfile.write(b"data: " + encoded + b"\n\n")
        self.wfile.flush()

    def _origin_allowed(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except ValueError:
            return False
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and port is None
            and (host == "app.nz" or host.endswith(".app.nz"))
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
        )

    def _check_origin(self) -> bool:
        if self._origin_allowed():
            return True
        self._json(403, {"error": "origin is not allowed"})
        return False

    def _cors_headers(self) -> None:
        origin = (self.headers.get("Origin") or "").strip()
        if origin and self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Api-Key",
        )
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        if _env_bool("MJJ_SERVER_LOG", False):
            super().log_message(format, *args)


def _short_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def serve(config: ServerConfig | None = None) -> None:
    config = config or ServerConfig.from_env()
    service = AgentService(config)
    server = AgentHTTPServer((config.host, config.port), service)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
