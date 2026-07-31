# Agent server

`python -m mjj.server` serves the same `Agent` loop over a stdlib
`ThreadingHTTPServer`. It is intended to sit behind the mojojojo.app.nz reverse
proxy and provide the agent panel beside the editor.

## Configuration

| variable | default | purpose |
| --- | --- | --- |
| `MJJ_SERVER_HOST` | `127.0.0.1` | listen address |
| `PORT` / `MJJ_SERVER_PORT` | `4343` | listen port; `PORT` wins. The mojojojo Go tier proxies here by default (`MJJ_AGENT_URL`). |
| `DB_PATH` | `/nvme0n1-disk/data/mojojojo/mojojojo.db` | mojojojo-owned fractional token accrual |
| `APPNZ_DATABASE_PATH` | `/nvme0n1-disk/data/appnz-sso.db` | shared app.nz identity and credit ledger |
| `MJJ_WORKSPACE_ROOT` / `MJJ_SANDBOX_ROOT` | `/nvme0n1-disk/data/mojojojo/agent-workspaces` | parent of per-user workspaces; the first wins |
| `TOKENS_PER_CREDIT` / `MJJ_TOKENS_PER_CREDIT` | `1000` | agent tokens accrued per app.nz credit; the first wins |
| `MJJ_MAX_RUNS_PER_USER` | `2` | live-run cap per account |
| `MJJ_STREAM_QUEUE_SIZE` | `128` | bounded events waiting behind an SSE client |
| `MJJ_MAX_BODY_BYTES` | `1048576` | request-body limit |
| `MJJ_MAX_PROMPT_CHARS` | `256000` | prompt limit |
| `ALLOW_ANONYMOUS` | `false` | permit unbilled anonymous runs |

Set the token price deliberately for the deployed model mix. A credit is only
moved when accumulated usage reaches this threshold; a short run is not rounded
up.

The shared database must already contain app.nz's `users`, `sso_sessions`,
`api_keys`, and `credit_ledger` tables. The server never migrates that database.
It creates only `agent_accrual` and `agent_runs` in mojojojo's own database,
with every row scoped to `app_id = "mojojojo"`.

## Authentication and billing

Browser authentication checks `__Host-appnz_sso_session` and then
`appnz_session`. Both carry a raw token; the database stores its SHA-256 digest
as unpadded URL-safe base64. API clients may send either:

```text
Authorization: Bearer mj_live_...
X-Api-Key: mj_live_...
```

Only active keys whose `app_id` is `mojojojo` are accepted. Browser sessions
take precedence over keys, matching the execution service. Anonymous access is
off unless `ALLOW_ANONYMOUS=true`.

Before a signed-in run starts, the account must have at least one available
credit. After it ends, input plus output tokens are added to that user's local
accrual. Each whole-credit threshold spends from free, then unexpired plan,
then paid credits in one immediate transaction against the shared database.
The ledger line is:

```text
reason = agent:<model>:<ntokens>
app_id = mojojojo
source = spend
```

If the account runs out during a multi-credit charge, available credits are
taken and the unpaid token amount remains accrued for a later top-up.

## HTTP API

### Start a run

```http
POST /v1/agent/runs
Content-Type: application/json

{
  "prompt": "Fix the failing tests",
  "cwd": "project",
  "model": "gpt-5.6-sol",
  "effort": "high",
  "session": "optional-session-id"
}
```

`prompt` is required. `cwd` is relative to the caller's workspace; absolute
paths, traversal, symlink escapes, and missing directories are rejected.
Omitting `session` creates one. Supplying an owned session ID appends to its
existing rollout.

The response is `text/event-stream`. `X-Run-ID` contains the run ID. Every
`Step` is encoded as its own named SSE event:

```text
event: text
data: {"kind":"text","text":"Done","name":"","meta":{}}
```

The stream begins with a small `run` event carrying the run and session IDs.
Agent events use `reasoning`, `text`, `tool_call`, `tool_result`, `usage`, or
`error`. The last `usage` event has `meta.final = true` and includes exact model
usage, total billed tokens, the tool-output ledger summary, and the app.nz
credit charge.

### Attach, interrupt, and list sessions

```text
GET  /v1/agent/runs/<run-id>
POST /v1/agent/runs/<run-id>/interrupt
GET  /v1/agent/sessions
GET  /healthz
```

Run lookup is account-scoped. The attach endpoint follows future events from a
live stream (up to four attachments); completed runs are represented by their
session rollout rather than retained in memory. The interrupt endpoint sets the
run's cancellation flag and closes an active model response.

The per-run event queue is bounded. A slow client therefore backpressures the
agent instead of growing memory without limit. SSE keepalives detect a dead
socket even while the model is quiet; disconnecting the attached client
interrupts the run.

## Isolation and origins

Each account maps to a non-identifying hashed directory below
`MJJ_WORKSPACE_ROOT`. Hosted file, search, and shell arguments are checked
against that workspace. Shell interpretation is disabled, non-allowlisted
commands are denied, and the Python tool runs only through the installed local
jail. Hosted Python fails closed if that jail is unavailable; it never takes
the CLI's in-process fallback. This guard is server-local and does not weaken
the less restrictive local CLI.

Requests without an `Origin` header are accepted for API clients. Browser CORS
is reflected only for HTTPS origins at `app.nz` or a subdomain of `app.nz`,
with credentials enabled. Other origins receive `403`.

The stdlib server should bind to loopback and run behind the existing TLS
reverse proxy. Do not expose its plain HTTP listener directly.
