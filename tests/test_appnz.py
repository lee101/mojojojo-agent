from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import Message

import pytest

from mjj.appnz import APP_ID, AuthStore, Billing, hash_token


# Copied from ../mojojojo/billing_test.go.  It is deliberately the exact
# subset used by auth.go and billing.go, not a schema invented for these tests.
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
def stores(tmp_path):
    shared_path = tmp_path / "shared.db"
    local_path = tmp_path / "mojojojo.db"
    now = datetime.now(timezone.utc)
    with sqlite3.connect(shared_path) as conn:
        conn.executescript(SHARED_SCHEMA)
        conn.execute(
            """
            INSERT INTO users
                (id, email, created_at, free_credits, plan_credits,
                 plan_credits_expire_at, paid_credits, handle)
            VALUES ('u1', 'person@example.com', ?, 3, 2, ?, 4, 'person')
            """,
            (now.isoformat(), (now + timedelta(days=1)).isoformat()),
        )
    auth = AuthStore(shared_path)
    billing = Billing(local_path, auth, tokens_per_credit=100)
    return shared_path, local_path, auth, billing


def headers(**values) -> Message:
    result = Message()
    for name, value in values.items():
        result[name.replace("_", "-")] = value
    return result


def test_hash_token_matches_raw_urlsafe_sha256():
    assert hash_token("hello") == "LPJNul-wow4m6DsqxbninhsWHlwfp0JecwQzYpOLmCQ"


def test_shared_cookie_and_api_key_authentication(stores):
    shared_path, _, auth, _ = stores
    now = datetime.now(timezone.utc)
    session = "browser-secret"
    api_key = "mj_live_testing-secret"
    with sqlite3.connect(shared_path) as conn:
        conn.execute(
            "INSERT INTO sso_sessions (id,user_id,created_at,expires_at) "
            "VALUES (?,?,?,?)",
            (
                hash_token(session),
                "u1",
                now.isoformat(),
                (now + timedelta(hours=1)).isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO api_keys
                (id,user_id,app_id,name,key_hash,key_prefix,created_at)
            VALUES ('k1','u1',?,'test',?,'mj_live_test',?)
            """,
            (APP_ID, hash_token(api_key), now.isoformat()),
        )

    user = auth.user_from_headers(
        headers(Cookie=f"appnz_session={session}", Authorization="Bearer bad")
    )
    assert user is not None
    assert user.id == "u1"
    assert user.via == "session"
    assert user.credits == 9
    assert user.display_name == "person"

    user = auth.user_from_headers(headers(Authorization=f"Bearer {api_key}"))
    assert user is not None and user.via == "api_key"
    with sqlite3.connect(shared_path) as conn:
        assert conn.execute(
            "SELECT last_used_at FROM api_keys WHERE id='k1'"
        ).fetchone()[0]


def test_expired_disabled_and_wrong_app_credentials_are_rejected(stores):
    shared_path, _, auth, _ = stores
    now = datetime.now(timezone.utc)
    with sqlite3.connect(shared_path) as conn:
        conn.execute(
            "INSERT INTO sso_sessions VALUES (?,?,?,?)",
            (
                hash_token("expired"),
                "u1",
                now.isoformat(),
                (now - timedelta(seconds=1)).isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO api_keys "
            "(id,user_id,app_id,name,key_hash,key_prefix,created_at) "
            "VALUES ('other','u1','other','x',?,'x',?)",
            (hash_token("mj_live_other"), now.isoformat()),
        )
    assert auth.user_from_session("expired") is None
    assert auth.user_from_api_key("mj_live_other") is None

    with sqlite3.connect(shared_path) as conn:
        conn.execute("UPDATE users SET disabled_at=? WHERE id='u1'", (now.isoformat(),))
    assert auth.user_by_id("u1") is None


def test_expired_plan_credits_are_not_spendable(stores):
    shared_path, _, auth, _ = stores
    now = datetime.now(timezone.utc)
    with sqlite3.connect(shared_path) as conn:
        conn.execute(
            "UPDATE users SET plan_credits_expire_at=? WHERE id='u1'",
            ((now - timedelta(days=1)).isoformat(),),
        )
    user = auth.user_by_id("u1")
    assert user is not None
    assert user.plan_credits == 0
    assert user.credits == 7


def test_short_agent_runs_accrue_without_rounding_up(stores):
    shared_path, _, auth, billing = stores
    user = auth.user_by_id("u1")
    assert user is not None

    first = billing.charge(user, "gpt-test", 40, run_id="run-one")
    assert first.credits == 0
    assert first.owed_tokens == 40

    second = billing.charge(user, "gpt-test", 60, run_id="run-two")
    assert second.credits == 1
    assert second.owed_tokens == 0
    assert second.balance == 8

    with sqlite3.connect(shared_path) as conn:
        row = conn.execute(
            "SELECT delta,reason,app_id,source,balance_after "
            "FROM credit_ledger"
        ).fetchone()
        balances = conn.execute(
            "SELECT free_credits,plan_credits,paid_credits FROM users WHERE id='u1'"
        ).fetchone()
    assert row == (-1, "agent:gpt-test:60", APP_ID, "spend", 8)
    assert balances == (2, 2, 4)


def test_spend_order_and_unpaid_debt_match_exec_billing(stores):
    shared_path, _, auth, billing = stores
    with sqlite3.connect(shared_path) as conn:
        conn.execute(
            "UPDATE users SET free_credits=1,plan_credits=1,paid_credits=1 "
            "WHERE id='u1'"
        )
    user = auth.user_by_id("u1")
    assert user is not None

    charge = billing.charge(user, "gpt-test", 500, run_id="run-debt")
    assert charge.credits == 3
    assert charge.balance == 0
    assert charge.owed_tokens == 200
    with sqlite3.connect(shared_path) as conn:
        assert conn.execute(
            "SELECT free_credits,plan_credits,paid_credits FROM users WHERE id='u1'"
        ).fetchone() == (0, 0, 0)
    assert billing.owed("u1") == 200
