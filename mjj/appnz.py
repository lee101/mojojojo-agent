"""app.nz identity and credit-ledger integration.

The shared database belongs to app.nz.  This module only reads its identity
tables and, when a whole credit is due, updates the existing balance and ledger
rows.  Fractional agent usage is kept in mojojojo's own database.
"""

from __future__ import annotations

import base64
import hashlib
import math
import secrets
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.message import Message
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Mapping
from urllib.parse import quote

APP_ID = "mojojojo"
KEY_PREFIX = "mj_live_"
SESSION_COOKIES = ("__Host-appnz_sso_session", "appnz_session")


def hash_token(token: str) -> str:
    """Match app.nz: SHA-256 encoded with unpadded URL-safe base64."""
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _read_only_uri(path: Path) -> str:
    return "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"


def _expired(value: object, now: datetime | None = None) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        instant = value
    else:
        text = str(value).strip()
        if not text:
            return False
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            instant = datetime.fromisoformat(text)
        except ValueError:
            return True
    now = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc) < now.astimezone(timezone.utc)


@dataclass(frozen=True)
class User:
    id: str
    email: str
    handle: str = ""
    free_credits: int = 0
    plan_credits: int = 0
    paid_credits: int = 0
    is_admin: bool = False
    via: str = ""

    @property
    def credits(self) -> int:
        return self.free_credits + self.plan_credits + self.paid_credits

    @property
    def display_name(self) -> str:
        if self.handle:
            return self.handle
        return self.email.partition("@")[0] or self.email


class AuthStore:
    """Resolve app.nz sessions and mojojojo API keys.

    Authentication queries use SQLite's ``mode=ro``.  The only best-effort
    authentication write is the same ``last_used_at`` stamp made by the Go
    service for an accepted API key.
    """

    def __init__(self, database_path: str | Path, timeout: float = 10.0):
        self.path = Path(database_path)
        self.timeout = timeout
        if not self.path.is_file():
            raise FileNotFoundError(f"app.nz database not found: {self.path}")

    def _read(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            _read_only_uri(self.path),
            uri=True,
            timeout=self.timeout,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
        return conn

    def _write(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=self.timeout,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
        return conn

    def user_from_headers(self, headers: Mapping[str, str] | Message) -> User | None:
        """Resolve browser session first, then bearer or ``X-Api-Key``."""
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
        except Exception:
            cookie = SimpleCookie()
        for name in SESSION_COOKIES:
            morsel = cookie.get(name)
            if morsel and morsel.value.strip():
                user = self.user_from_session(morsel.value.strip())
                if user is not None:
                    return user
                break

        authorization = (headers.get("Authorization") or "").strip()
        token = ""
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token:
            token = (headers.get("X-Api-Key") or "").strip()
        return self.user_from_api_key(token)

    def user_from_session(self, token: str) -> User | None:
        if not token:
            return None
        with self._read() as conn:
            row = conn.execute(
                "SELECT user_id, expires_at FROM sso_sessions WHERE id = ?",
                (hash_token(token),),
            ).fetchone()
        if row is None or _expired(row["expires_at"]):
            return None
        return self.user_by_id(str(row["user_id"]), via="session")

    def user_from_api_key(self, token: str) -> User | None:
        if not token.startswith(KEY_PREFIX):
            return None
        token_hash = hash_token(token)
        with self._read() as conn:
            row = conn.execute(
                "SELECT user_id FROM api_keys "
                "WHERE key_hash = ? AND app_id = ? AND revoked_at IS NULL",
                (token_hash, APP_ID),
            ).fetchone()
        if row is None:
            return None
        try:
            with self._write() as conn:
                conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE key_hash = ?",
                    (_utc_text(), token_hash),
                )
        except sqlite3.Error:
            pass
        return self.user_by_id(str(row["user_id"]), via="api_key")

    def user_by_id(self, user_id: str, via: str = "") -> User | None:
        with self._read() as conn:
            row = conn.execute(
                """
                SELECT id, email, COALESCE(handle,'') AS handle,
                       COALESCE(free_credits,0) AS free_credits,
                       COALESCE(plan_credits,0) AS plan_credits,
                       plan_credits_expire_at,
                       COALESCE(paid_credits,0) AS paid_credits,
                       COALESCE(is_admin,0) AS is_admin
                  FROM users
                 WHERE id = ? AND disabled_at IS NULL
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        plan = int(row["plan_credits"])
        if _expired(row["plan_credits_expire_at"]):
            plan = 0
        return User(
            id=str(row["id"]),
            email=str(row["email"]),
            handle=str(row["handle"]),
            free_credits=int(row["free_credits"]),
            plan_credits=plan,
            paid_credits=int(row["paid_credits"]),
            is_admin=bool(row["is_admin"]),
            via=via,
        )

    def has_presented_api_key(self, headers: Mapping[str, str] | Message) -> bool:
        authorization = (headers.get("Authorization") or "").strip()
        return bool(
            authorization.lower().startswith("bearer ")
            or (headers.get("X-Api-Key") or "").strip()
        )


@dataclass(frozen=True)
class Charge:
    tokens: int
    credits: int
    owed_tokens: float
    balance: int
    tokens_per_credit: float

    def as_dict(self) -> dict:
        return asdict(self)


class Billing:
    """Accrue token usage locally and spend whole app.nz credits."""

    def __init__(
        self,
        database_path: str | Path,
        shared: AuthStore,
        tokens_per_credit: float,
    ):
        if not math.isfinite(tokens_per_credit) or tokens_per_credit <= 0:
            raise ValueError("tokens_per_credit must be positive")
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.shared = shared
        self.tokens_per_credit = float(tokens_per_credit)
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._create_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _create_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_accrual (
                    user_id     TEXT NOT NULL,
                    app_id      TEXT NOT NULL,
                    owed_tokens REAL NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    updated_at  TIMESTAMP NOT NULL,
                    PRIMARY KEY (user_id, app_id)
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id         TEXT PRIMARY KEY,
                    user_id    TEXT NOT NULL,
                    app_id     TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    model      TEXT NOT NULL,
                    tokens     INTEGER NOT NULL,
                    credits    INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_user
                    ON agent_runs(user_id, app_id, created_at);
                """
            )

    def _lock_for(self, user_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(user_id, threading.Lock())

    def owed(self, user_id: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owed_tokens FROM agent_accrual "
                "WHERE user_id = ? AND app_id = ?",
                (user_id, APP_ID),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def charge(
        self,
        user: User,
        model: str,
        tokens: int,
        run_id: str | None = None,
    ) -> Charge:
        tokens = max(0, int(tokens))
        with self._lock_for(user.id):
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT owed_tokens FROM agent_accrual "
                    "WHERE user_id = ? AND app_id = ?",
                    (user.id, APP_ID),
                ).fetchone()
            owed = (float(row[0]) if row else 0.0) + tokens
            due = int(math.floor(owed / self.tokens_per_credit))
            if due:
                owed -= due * self.tokens_per_credit

            credits = 0
            balance = user.credits
            if due:
                credits, balance = self._spend(user.id, due, model, tokens)
                if credits < due:
                    owed += (due - credits) * self.tokens_per_credit

            now = _utc_text()
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(
                        """
                        INSERT INTO agent_accrual
                            (user_id, app_id, owed_tokens, total_tokens, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(user_id, app_id) DO UPDATE SET
                            owed_tokens = excluded.owed_tokens,
                            total_tokens = agent_accrual.total_tokens
                                           + excluded.total_tokens,
                            updated_at = excluded.updated_at
                        """,
                        (user.id, APP_ID, owed, tokens, now),
                    )
                    conn.execute(
                        """
                        INSERT INTO agent_runs
                            (id, user_id, app_id, created_at, model, tokens, credits)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id or secrets.token_urlsafe(12),
                            user.id,
                            APP_ID,
                            now,
                            model,
                            tokens,
                            credits,
                        ),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        return Charge(
            tokens=tokens,
            credits=credits,
            owed_tokens=round(owed, 3),
            balance=balance,
            tokens_per_credit=self.tokens_per_credit,
        )

    def _spend(
        self, user_id: str, requested: int, model: str, tokens: int
    ) -> tuple[int, int]:
        with self.shared._write() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT COALESCE(free_credits,0),
                           COALESCE(plan_credits,0),
                           plan_credits_expire_at,
                           COALESCE(paid_credits,0)
                      FROM users WHERE id = ?
                    """,
                    (user_id,),
                ).fetchone()
                if row is None:
                    raise LookupError(f"unknown app.nz user {user_id!r}")
                free, plan, paid = int(row[0]), int(row[1]), int(row[3])
                if _expired(row[2]):
                    plan = 0
                available = free + plan + paid
                take = min(requested, available)
                if take <= 0:
                    conn.rollback()
                    return 0, available

                remaining = take
                use_free = min(remaining, free)
                remaining -= use_free
                use_plan = min(remaining, plan)
                remaining -= use_plan
                use_paid = remaining
                free -= use_free
                plan -= use_plan
                paid -= use_paid
                after = free + plan + paid
                conn.execute(
                    "UPDATE users SET free_credits = ?, plan_credits = ?, "
                    "paid_credits = ? WHERE id = ?",
                    (free, plan, paid, user_id),
                )
                conn.execute(
                    """
                    INSERT INTO credit_ledger
                        (id, user_id, delta, reason, app_id, source,
                         balance_after, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        secrets.token_urlsafe(18),
                        user_id,
                        -take,
                        f"agent:{model}:{tokens}",
                        APP_ID,
                        "spend",
                        after,
                        _utc_text(),
                    ),
                )
                conn.commit()
                return take, after
            except Exception:
                conn.rollback()
                raise


def _utc_text() -> str:
    return datetime.now(timezone.utc).isoformat()
