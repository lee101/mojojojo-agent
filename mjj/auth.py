"""Credentials for ChatGPT, OpenAI, OpenPaths and OpenRouter.

The ChatGPT path reuses the sign-in that Codex / Codex Infinity put in
``$CODEX_HOME/auth.json``. Browser and device login delegate to ``codex login``;
we read that cache, refresh its OAuth tokens in memory, and use the same
Responses backend as Codex. Provider API keys live separately under ``~/.mjj``.

Ownership matters here. codex-infinity owns ``~/.codexinfinity/auth.json`` and
rotates it on its own schedule. We only write it when the operator explicitly
opts in with ``MJJ_WRITE_BACK_AUTH=1``.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ISSUER = os.environ.get("MJJ_OPENAI_ISSUER", "https://auth.openai.com")
CLIENT_ID = os.environ.get("MJJ_OPENAI_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
TOKEN_ENDPOINT = ISSUER.rstrip("/") + "/oauth/token"

# Refresh well before the hour-ish expiry the access token carries, and well
# before the 4h cadence openpaths uses for the same credential.
REFRESH_AFTER_SECONDS = 3 * 3600
# A token this close to its own `exp` is refreshed regardless of the cadence.
EXPIRY_SKEW_SECONDS = 300

CANDIDATE_HOMES = ("~/.codexinfinity", "~/.codex")


class AuthError(RuntimeError):
    pass


def _post(url: str, body: bytes, content_type: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1 << 20)
    except urllib.error.HTTPError as exc:  # pragma: no cover - network shape
        detail = exc.read(4096).decode("utf-8", "replace").strip()
        raise AuthError(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AuthError(f"{url} unreachable: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise AuthError(f"{url} returned non-JSON body") from exc


def jwt_claims(token: str) -> dict:
    """Best-effort claim decode. Signature is not our business — the issuer
    checks that. We only want ``exp`` and the account id."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def token_expiry(token: str) -> float | None:
    exp = jwt_claims(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def codex_home() -> Path | None:
    """Where the existing max-plan credential lives."""
    explicit = os.environ.get("MJJ_CODEX_HOME") or os.environ.get("CODEX_HOME")
    homes = [explicit] if explicit else list(CANDIDATE_HOMES)
    for home in homes:
        if not home:
            continue
        path = Path(home).expanduser()
        if (path / "auth.json").is_file():
            return path
    return None


@dataclass
class MaxPlanTokens:
    id_token: str = ""
    access_token: str = ""
    refresh_token: str = ""
    account_id: str = ""

    @classmethod
    def from_auth_json(cls, doc: dict) -> "MaxPlanTokens":
        tokens = doc.get("tokens") or {}
        return cls(
            id_token=tokens.get("id_token", "") or "",
            access_token=tokens.get("access_token", "") or "",
            refresh_token=tokens.get("refresh_token", "") or "",
            account_id=tokens.get("account_id", "") or "",
        )


CHATGPT_BASE_URL = os.environ.get(
    "MJJ_CHATGPT_BASE_URL", "https://chatgpt.com/backend-api/codex"
)
API_BASE_URL = os.environ.get("MJJ_OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENPATHS_BASE_URL = os.environ.get(
    "MJJ_OPENPATHS_BASE_URL", "https://openpaths.io/v1"
)
OPENROUTER_BASE_URL = os.environ.get(
    "MJJ_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)

PROVIDER_DEFAULTS = {
    "openai": (API_BASE_URL, "responses", "gpt-5.6-sol"),
    "openpaths": (OPENPATHS_BASE_URL, "chat_completions", "openpaths/auto-code"),
    "openrouter": (OPENROUTER_BASE_URL, "chat_completions", "openrouter/auto"),
}


@dataclass
class Credential:
    """What the model client needs: a bearer token and where to send it.

    ``kind`` is ``"chatgpt"`` (max plan, talks to the ChatGPT backend, refreshes
    itself) or ``"api_key"`` (plain key against the public API).
    """

    kind: str
    token: str
    base_url: str
    account_id: str = ""
    source: str = ""
    provider: str = "openai"
    api_style: str = "responses"
    default_model: str = "gpt-5.6-sol"

    @property
    def headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.token}"}
        if self.kind == "chatgpt":
            # The backend routes on the account, and refuses the request
            # without an originator it recognises.
            if self.account_id:
                h["chatgpt-account-id"] = self.account_id
            h["OpenAI-Beta"] = "responses=experimental"
            h["originator"] = os.environ.get("MJJ_ORIGINATOR", "codex_cli_rs")
        return h


def refresh_tokens(refresh_token: str) -> dict:
    body = json.dumps(
        {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    ).encode()
    doc = _post(TOKEN_ENDPOINT, body, "application/json")
    if not doc.get("id_token") and not doc.get("access_token"):
        raise AuthError("refresh returned neither id_token nor access_token")
    return doc


def exchange_id_token_for_api_key(id_token: str) -> str:
    form = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "client_id": CLIENT_ID,
            "requested_token": "openai-api-key",
            "subject_token": id_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
        }
    ).encode()
    doc = _post(TOKEN_ENDPOINT, form, "application/x-www-form-urlencoded")
    key = (doc.get("access_token") or "").strip()
    if not key:
        raise AuthError("token exchange did not return an api key")
    return key


class MaxPlanCredentials:
    """Reads the on-disk credential, keeps a usable API key in memory.

    Thread-safe: the agent loop and any background compaction share one
    instance, and a 401 from either can trigger the same refresh.
    """

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or codex_home()
        self._lock = threading.Lock()
        self._key: str = ""
        self._key_obtained: float = 0.0
        self._tokens = MaxPlanTokens()
        self._loaded_mtime: float = -1.0

    # -- disk ---------------------------------------------------------------

    @property
    def auth_path(self) -> Path | None:
        return (self.home / "auth.json") if self.home else None

    def _load(self) -> dict:
        path = self.auth_path
        if not path or not path.is_file():
            raise AuthError(
                "no max-plan credential found; looked for auth.json in "
                + ", ".join(CANDIDATE_HOMES)
            )
        mtime = path.stat().st_mtime
        doc = json.loads(path.read_text())
        # codex-infinity may have rotated the file under us. Adopting its
        # tokens is strictly better than refreshing our stale copy.
        if mtime != self._loaded_mtime:
            self._tokens = MaxPlanTokens.from_auth_json(doc)
            self._loaded_mtime = mtime
            self._key_obtained = mtime
        return doc

    def _write_back(self, doc: dict) -> None:
        # codex-infinity owns this file. Operators that know the credential is
        # shared as one rotation chain can opt into coordinated write-back.
        if os.environ.get("MJJ_WRITE_BACK_AUTH") != "1":
            return
        path = self.auth_path
        if not path:
            return
        doc.setdefault("tokens", {})
        doc["tokens"].update(
            {
                "id_token": self._tokens.id_token,
                "access_token": self._tokens.access_token,
                "refresh_token": self._tokens.refresh_token,
                "account_id": self._tokens.account_id,
            }
        )
        doc["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        self._loaded_mtime = path.stat().st_mtime

    # -- token --------------------------------------------------------------

    def _stale(self) -> bool:
        token = self._tokens.access_token
        if not token:
            return True
        exp = token_expiry(token)
        if exp is not None:
            return time.time() > exp - EXPIRY_SKEW_SECONDS
        # No `exp` claim to go on: fall back to the refresh cadence, measured
        # from whenever this token landed (disk mtime on the first load).
        return time.time() - self._key_obtained > REFRESH_AFTER_SECONDS

    def _flock(self):
        """Cross-process lock so two agents cannot both spend the refresh
        token. Best effort: without fcntl (or a writable home) we fall back to
        the in-process lock alone."""
        path = self.home / "auth.lock" if self.home else None
        if path is None:
            return None
        try:
            import fcntl

            handle = open(path, "a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            return handle
        except Exception:
            return None

    def bearer(self, force: bool = False) -> tuple[str, str]:
        """Return ``(token, kind)`` — kind is ``chatgpt`` or ``api_key``."""
        with self._lock:
            self._load()
            if not force and not self._stale():
                return self._tokens.access_token, "chatgpt"
            lock = self._flock()
            try:
                return self._refresh_locked(force)
            finally:
                if lock is not None:
                    lock.close()

    def _refresh_locked(self, force: bool) -> tuple[str, str]:
        # Re-read under the lock: another process may have rotated the file
        # while we waited, in which case its tokens are the live ones.
        doc = self._load()
        plain = (doc.get("OPENAI_API_KEY") or "").strip()
        if plain and doc.get("auth_mode") != "chatgpt":
            # An operator pasted a raw key into auth.json. Honour it; there
            # is nothing to refresh.
            self._key, self._key_obtained = plain, time.time()
            return plain, "api_key"
        if not force and not self._stale():
            return self._tokens.access_token, "chatgpt"
        if not self._tokens.refresh_token:
            if plain:
                self._key, self._key_obtained = plain, time.time()
                return plain, "api_key"
            raise AuthError("credential has no refresh_token and no api key")
        fresh = refresh_tokens(self._tokens.refresh_token)
        self._tokens = MaxPlanTokens(
            id_token=fresh.get("id_token") or self._tokens.id_token,
            access_token=fresh.get("access_token") or self._tokens.access_token,
            refresh_token=fresh.get("refresh_token") or self._tokens.refresh_token,
            account_id=self._tokens.account_id,
        )
        # Persist BEFORE anything else can fail. The issuer has already retired
        # the old refresh_token; if we crashed here without writing, the
        # on-disk credential would be permanently dead and every other codex
        # process on the box would be locked out.
        self._write_back(doc)
        self._key_obtained = time.time()
        return self._tokens.access_token, "chatgpt"

    def credential(self, force: bool = False) -> Credential:
        token, kind = self.bearer(force=force)
        if kind == "chatgpt" and os.environ.get("MJJ_EXCHANGE_API_KEY") == "1":
            # Only workspace-backed accounts can do this: the exchange needs an
            # organization_id in the id_token, and a personal Plus/Pro plan has
            # none ("Invalid ID token: missing organization_id"). Opt-in.
            return Credential(
                kind="api_key",
                token=exchange_id_token_for_api_key(self._tokens.id_token),
                base_url=API_BASE_URL,
                source=str(self.auth_path),
            )
        return Credential(
            kind=kind,
            token=token,
            base_url=CHATGPT_BASE_URL if kind == "chatgpt" else API_BASE_URL,
            account_id=self._tokens.account_id or self._account_from_token(),
            source=str(self.auth_path),
        )

    def _account_from_token(self) -> str:
        auth = jwt_claims(self._tokens.access_token).get(
            "https://api.openai.com/auth"
        )
        if isinstance(auth, dict):
            return str(auth.get("chatgpt_account_id") or "")
        return ""


@dataclass
class CredentialResolver:
    """Resolve a requested provider without leaking or silently mixing keys.

    In ``auto`` mode, explicit Mojojojo credentials win, followed by
    OpenPaths, an existing ChatGPT/Codex session, and finally an OpenAI key.
    Explicit provider selection only considers that provider's credentials.
    """

    max_plan: MaxPlanCredentials = field(default_factory=MaxPlanCredentials)
    provider: str = "auto"

    def resolve(self, force: bool = False, fallback: bool = False) -> Credential:
        requested = self.provider.strip().lower()
        if requested not in ("auto", "openai"):
            return provider_credential(requested)
        if requested == "auto":
            # MJJ_OPENAI_API_KEY is explicitly scoped to this harness and wins.
            # Otherwise an OpenPaths key selects the project's native multi-LLM
            # path. OpenRouter is supported with --provider openrouter but is
            # not auto-selected from a key exported for an unrelated process.
            if (os.environ.get("MJJ_OPENAI_API_KEY") or "").strip():
                return Credential(
                    kind="api_key",
                    token=os.environ["MJJ_OPENAI_API_KEY"].strip(),
                    base_url=API_BASE_URL,
                    source="env",
                )
            if provider_key("openpaths")[0]:
                return provider_credential("openpaths")
        if requested == "openai" and (
            (os.environ.get("MJJ_OPENAI_API_KEY") or "").strip()
            or _stored_provider_keys().get("openai")
        ):
            return provider_credential("openai")
        explicit = (os.environ.get("MJJ_OPENAI_API_KEY") or "").strip()
        if explicit:
            return Credential(
                kind="api_key", token=explicit, base_url=API_BASE_URL, source="env"
            )
        metered = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if fallback and metered:
            return Credential(
                kind="api_key", token=metered, base_url=API_BASE_URL, source="env"
            )
        try:
            return self.max_plan.credential(force=force)
        except AuthError:
            if metered:
                return Credential(
                    kind="api_key",
                    token=metered,
                    base_url=API_BASE_URL,
                    source="env",
                )
            raise


def mjj_home() -> Path:
    return Path(os.environ.get("MJJ_HOME") or "~/.mjj").expanduser()


def provider_auth_path() -> Path:
    return mjj_home() / "auth.json"


def _stored_provider_keys() -> dict[str, str]:
    path = provider_auth_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    providers = doc.get("providers") or {}
    if not isinstance(providers, dict):
        return {}
    return {
        str(name): str(value.get("api_key") or "")
        for name, value in providers.items()
        if isinstance(value, dict) and value.get("api_key")
    }


def provider_key(provider: str) -> tuple[str, str]:
    names = {
        "openpaths": ("MJJ_OPENPATHS_API_KEY", "OPENPATHS_API_KEY"),
        "openrouter": ("MJJ_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
        "openai": ("MJJ_OPENAI_API_KEY", "OPENAI_API_KEY"),
        "custom": ("MJJ_API_KEY",),
    }.get(provider, ())
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, f"env:{name}"
    stored = _stored_provider_keys().get(provider, "").strip()
    return (stored, str(provider_auth_path())) if stored else ("", "")


def provider_credential(provider: str) -> Credential:
    provider = provider.strip().lower()
    if provider == "custom":
        key, source = provider_key(provider)
        base_url = (os.environ.get("MJJ_BASE_URL") or "").strip()
        api_style = (os.environ.get("MJJ_API_STYLE") or "chat_completions").strip()
        default_model = (os.environ.get("MJJ_DEFAULT_MODEL") or "auto").strip()
        if not base_url:
            raise AuthError("custom provider requires MJJ_BASE_URL")
        if api_style not in ("responses", "chat_completions"):
            raise AuthError("MJJ_API_STYLE must be responses or chat_completions")
    else:
        try:
            base_url, api_style, default_model = PROVIDER_DEFAULTS[provider]
        except KeyError as exc:
            raise AuthError(f"unknown provider {provider!r}") from exc
        key, source = provider_key(provider)
    if not key:
        variable = {
            "openpaths": "OPENPATHS_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "custom": "MJJ_API_KEY",
        }.get(provider, "API key")
        raise AuthError(
            f"no {provider} credential; run `mjj login {provider}` or set {variable}"
        )
    return Credential(
        kind="api_key",
        token=key,
        base_url=base_url,
        source=source,
        provider=provider,
        api_style=api_style,
        default_model=default_model,
    )


def save_provider_key(provider: str, api_key: str) -> Path:
    provider = provider.strip().lower()
    if provider not in PROVIDER_DEFAULTS and provider != "custom":
        raise AuthError(f"unknown provider {provider!r}")
    if not api_key.strip():
        raise AuthError("API key cannot be empty")
    path = provider_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    providers = doc.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = doc["providers"] = {}
    providers[provider] = {"api_key": api_key.strip()}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:  # Windows ACLs, packaged stores and unusual filesystems
        pass
    temporary.replace(path)
    return path


def remove_provider_key(provider: str) -> bool:
    path = provider_auth_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(doc, dict):
        return False
    providers = doc.get("providers") or {}
    if not isinstance(providers, dict):
        return False
    if provider not in providers:
        return False
    del providers[provider]
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return True


def login_chatgpt(*, device: bool = False) -> int:
    """Run Codex's supported browser/device flow into the cache mjj reuses."""
    executable = shutil.which("codex")
    if not executable:
        raise AuthError(
            "ChatGPT sign-in requires the Codex CLI; install it or use "
            "`mjj login openai` for an API key"
        )
    target = Path(
        os.environ.get("MJJ_CODEX_HOME")
        or os.environ.get("CODEX_HOME")
        or codex_home()
        or "~/.codex"
    ).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    command = [executable, "login"]
    if device:
        command.append("--device-auth")
    environ = os.environ.copy()
    environ["CODEX_HOME"] = str(target)
    return subprocess.run(command, env=environ, check=False).returncode


def logout_chatgpt() -> int:
    executable = shutil.which("codex")
    if not executable:
        raise AuthError("ChatGPT logout requires the Codex CLI")
    target = Path(
        os.environ.get("MJJ_CODEX_HOME")
        or os.environ.get("CODEX_HOME")
        or codex_home()
        or "~/.codex"
    ).expanduser()
    environ = os.environ.copy()
    environ["CODEX_HOME"] = str(target)
    return subprocess.run([executable, "logout"], env=environ, check=False).returncode


def describe() -> dict:
    """What `mjj auth status` prints. Never returns secret material."""
    home = codex_home()
    stored = _stored_provider_keys()
    out: dict = {
        "codex_home": str(home) if home else None,
        "providers": {
            name: {
                "available": bool(provider_key(name)[0]),
                "source": provider_key(name)[1] or None,
            }
            for name in ("openpaths", "openrouter", "openai")
        },
        "saved_providers": sorted(stored),
    }
    if os.environ.get("MJJ_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        out["env_api_key"] = True
    if not home:
        return out
    try:
        doc = json.loads((home / "auth.json").read_text())
    except Exception as exc:
        out["error"] = str(exc)
        return out
    tokens = MaxPlanTokens.from_auth_json(doc)
    out["auth_mode"] = doc.get("auth_mode")
    out["account_id"] = tokens.account_id
    out["has_refresh_token"] = bool(tokens.refresh_token)
    out["last_refresh"] = doc.get("last_refresh")
    exp = token_expiry(tokens.id_token)
    if exp:
        out["id_token_expires_in"] = int(exp - time.time())
    claims = jwt_claims(tokens.access_token)
    auth = claims.get("https://api.openai.com/auth") or {}
    if isinstance(auth, dict):
        out["plan"] = auth.get("chatgpt_plan_type")
    return out
