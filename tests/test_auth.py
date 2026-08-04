"""Credential handling. Offline: every HTTP call is stubbed.

The behaviour worth pinning is the destructive bit — a refresh retires the old
refresh_token at the issuer, so the rotated one must reach disk even if
everything after it fails.
"""

from __future__ import annotations

import base64
import json
import os
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


def test_reads_and_refreshes_without_overwriting_owner(tmp_path, monkeypatch):
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
    # codex-infinity owns this file; refreshes stay in memory by default.
    assert json.loads((tmp_path / "auth.json").read_text())["tokens"]["refresh_token"] == "rt.old"


def test_fresh_token_is_not_refreshed(tmp_path, monkeypatch):
    _write_auth(tmp_path, expired=False)
    monkeypatch.setattr(
        auth, "refresh_tokens", lambda _t: pytest.fail("refreshed a live token")
    )
    cred = auth.MaxPlanCredentials(home=tmp_path).credential()
    assert cred.token


def test_write_back_can_be_enabled(tmp_path, monkeypatch):
    _write_auth(tmp_path)
    monkeypatch.setenv("MJJ_WRITE_BACK_AUTH", "1")
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
    assert on_disk["tokens"]["refresh_token"] == "rt.new"


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


def test_can_prefer_env_key_after_max_plan_auth_failure(tmp_path, monkeypatch):
    _write_auth(tmp_path, expired=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-metered")
    resolver = auth.CredentialResolver(
        max_plan=auth.MaxPlanCredentials(home=tmp_path)
    )
    cred = resolver.resolve(fallback=True)
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


def test_auto_prefers_openpaths_but_does_not_adopt_ambient_openrouter(tmp_path, monkeypatch):
    _write_auth(tmp_path, expired=False)
    monkeypatch.setenv("OPENPATHS_API_KEY", "op-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    resolver = auth.CredentialResolver(max_plan=auth.MaxPlanCredentials(home=tmp_path))
    credential = resolver.resolve()
    assert (credential.provider, credential.api_style, credential.token) == (
        "openpaths",
        "chat_completions",
        "op-test",
    )

    monkeypatch.delenv("OPENPATHS_API_KEY")
    assert resolver.resolve().kind == "chatgpt"


def test_saved_provider_key_is_private_and_resolvable(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MJJ_OPENROUTER_API_KEY", raising=False)
    path = auth.save_provider_key("openrouter", "sk-secret")
    assert path == tmp_path / "auth.json"
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    credential = auth.CredentialResolver(provider="openrouter").resolve()
    assert credential.token == "sk-secret"
    assert "sk-secret" not in json.dumps(auth.describe())
    assert auth.remove_provider_key("openrouter") is True
    assert auth.remove_provider_key("openrouter") is False


def test_deepseek_uses_compatible_chat_api(tmp_path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-secret")

    credential = auth.CredentialResolver(provider="deepseek").resolve()

    assert credential == auth.Credential(
        kind="api_key",
        token="ds-secret",
        base_url="https://api.deepseek.com",
        source="env:DEEPSEEK_API_KEY",
        provider="deepseek",
        api_style="chat_completions",
        default_model="deepseek-v4-flash",
    )


@pytest.mark.parametrize("malformed", ["[]", '{"providers": []}'])
def test_saving_provider_key_repairs_malformed_auth_document(
    tmp_path, monkeypatch, malformed
):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(malformed, encoding="utf-8")

    auth.save_provider_key("openpaths", "op-secret")

    assert auth.provider_key("openpaths")[0] == "op-secret"


def test_chatgpt_login_delegates_to_codex_device_flow(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("MJJ_CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(auth.shutil, "which", lambda name: "/bin/codex" if name == "codex" else None)

    class Completed:
        returncode = 0

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(auth.subprocess, "run", run)
    assert auth.login_chatgpt(device=True) == 0
    command, kwargs = calls[0]
    assert command == ["/bin/codex", "login", "--device-auth"]
    assert kwargs["env"]["CODEX_HOME"] == str(tmp_path / "codex-home")
    assert (tmp_path / "codex-home").is_dir()
