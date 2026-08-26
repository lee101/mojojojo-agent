"""Optional semantic compression for oversized tool results.

Off by default and never load-bearing. When ``MJJ_SUMMARIZER_URL`` points at an
OpenAI-compatible chat endpoint (a local llama.cpp server serving a small model
such as SmolLM3 is the intended shape), the ledger may spend one bounded call
compressing a tool dump that aggregation alone would gut. Any failure — no URL,
timeout, garbage response — returns ``None`` and deterministic clipping takes
over. The full text is always spilled to disk first, so a lossy summary costs
at most a re-read, never information.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 12.0
PROMPT = (
    "Compress this tool output for a coding agent that must decide what to "
    "do next. Keep: every error and warning, exit codes, file paths with line "
    "numbers, counts, and the final status. Drop repeated lines, progress "
    "bars, and boilerplate. Answer with the compressed output only.\n\n"
    "TOOL OUTPUT:\n"
)


def summarizer_url() -> str:
    return (os.environ.get("MJJ_SUMMARIZER_URL") or "").strip().rstrip("/")


def _model() -> str:
    return (os.environ.get("MJJ_SUMMARIZER_MODEL") or "").strip() or "local"


def _timeout() -> float:
    raw = (os.environ.get("MJJ_SUMMARIZER_TIMEOUT") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def _post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_timeout()) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _completions_url(base: str) -> str:
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def semantic_summary(text: str, max_chars: int, *, spill: str = "") -> str | None:
    """Compress ``text`` to at most ``max_chars``, or give up with None."""
    base = summarizer_url()
    if not base or max_chars < 240:
        return None
    budget_tokens = max(64, max_chars // 4)
    try:
        document = _post_json(
            _completions_url(base),
            {
                "model": _model(),
                "temperature": 0,
                "max_tokens": budget_tokens,
                "messages": [
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": text[:200_000]},
                ],
            },
        )
    except (OSError, ValueError, urllib.error.URLError, KeyError, TypeError):
        return None
    choices = document.get("choices") if isinstance(document, dict) else None
    if not choices:
        return None
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    header = f"… summarized by {_model()} · {spill or 'full output discarded'} …"
    body = content.strip()
    if len(body) > max_chars - len(header) - 1:
        body = body[: max_chars - len(header) - 1]
    return f"{header}\n{body}"
