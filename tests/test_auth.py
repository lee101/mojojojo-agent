"""Credential handling. Offline: every HTTP call is stubbed.

The behaviour worth pinning is the destructive bit — a refresh retires the old
refresh_token at the issuer, so the rotated one must reach disk even if
everything after it fails.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from mjj import auth


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _write_auth(home, *, expired=True, refresh="rt.old"):
    exp = time.time() + (-100 if expired else 3600)
    (home / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "id_token": _jwt({"exp": exp}),
                    "access_token": _jwt({"exp": exp}),
                    "refresh_token": refresh,
                    "account_id": "acct-1",
                },
                "last_refresh": "2026-07-28T11:32:34Z",
            }
        )
    )


def test_reads_and_refreshes(tmp_path, monkeypatch):
    _write_auth(tmp_path)
    calls = []

    def fake_refresh(token):
        calls.append(token)
        return {
            "id_token": _jwt({"exp": time.time() + 3600}),
            "access_token": _jwt({"exp": time.time() + 3600}),
            "refresh_token": "rt.new",
        }

    monkeypatch.setattr(auth, "refresh_tokens", fake_refresh)
    cred = auth.MaxPlanCredentials(home=tmp_path).credential()

    assert calls == ["rt.old"]
    assert cred.kind == "chatgpt"
    assert cred.base_url.endswith("/codex")
    assert cred.headers["chatgpt-account-id"] == "acct-1"
    # Rotated token reached disk, or the next codex process is locked out.
    assert json.loads((tmp_path / "auth.json").read_text())["tokens"]["refresh_token"] == "rt.new"


def test_fresh_token_is_not_refreshed(tmp_path, monkeypatch):
    _write_auth(tmp_path, expired=False)
    monkeypatch.setattr(
        auth, "refresh_tokens", lambda _t: pytest.fail("refreshed a live token")
    )
    cred = auth.MaxPlanCredentials(home=tmp_path).credential()
    assert cred.token


def test_write_back_can_be_disabled(tmp_path, monkeypatch):
    _write_auth(tmp_path)
    monkeypatch.setenv("MJJ_WRITE_BACK_AUTH", "0")
    monkeypatch.setattr(
        auth,
        "refresh_tokens",
        lambda _t: {
            "id_token": _jwt({"exp": time.time() + 3600}),
            "access_token": _jwt({"exp": time.time() + 3600}),
            "refresh_token": "rt.new",
        },
    )
    auth.MaxPlanCredentials(home=tmp_path).credential()
    on_disk = json.loads((tmp_path / "auth.json").read_text())
    assert on_disk["tokens"]["refresh_token"] == "rt.old"


def test_explicit_env_key_wins(tmp_path, monkeypatch):
    _write_auth(tmp_path)
    monkeypatch.setenv("MJJ_OPENAI_API_KEY", "sk-explicit")
    resolver = auth.CredentialResolver(max_plan=auth.MaxPlanCredentials(home=tmp_path))
    cred = resolver.resolve()
    assert (cred.kind, cred.token, cred.source) == ("api_key", "sk-explicit", "env")


def test_max_plan_outranks_stray_openai_api_key(tmp_path, monkeypatch):
    _write_auth(tmp_path, expired=False)
    monkeypatch.delenv("MJJ_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-metered")
    resolver = auth.CredentialResolver(max_plan=auth.MaxPlanCredentials(home=tmp_path))
    assert resolver.resolve().kind == "chatgpt"


def test_falls_back_to_env_key_when_no_credential(tmp_path, monkeypatch):
    monkeypatch.delenv("MJJ_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-metered")
    empty = auth.MaxPlanCredentials(home=tmp_path)  # no auth.json here
    cred = auth.CredentialResolver(max_plan=empty).resolve()
    assert (cred.kind, cred.token) == ("api_key", "sk-metered")


def test_raises_when_nothing_is_available(tmp_path, monkeypatch):
    monkeypatch.delenv("MJJ_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(auth.AuthError):
        auth.CredentialResolver(
            max_plan=auth.MaxPlanCredentials(home=tmp_path)
        ).resolve()


def test_api_key_credential_sends_no_chatgpt_headers():
    cred = auth.Credential(kind="api_key", token="sk-x", base_url=auth.API_BASE_URL)
    assert cred.headers == {"Authorization": "Bearer sk-x"}


def test_describe_never_leaks_secrets(tmp_path, monkeypatch):
    _write_auth(tmp_path)
    monkeypatch.setenv("MJJ_CODEX_HOME", str(tmp_path))
    blob = json.dumps(auth.describe())
    assert "rt.old" not in blob
    assert json.loads(blob)["has_refresh_token"] is True
