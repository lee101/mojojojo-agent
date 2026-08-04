"""Rollout files: append-only JSONL, one per session, resumable.

The format is deliberately dumb — one JSON object per line, the same items we
send to the model — so a session can be replayed, forked, diffed, or read by a
human with ``jq`` and nothing else.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from html import escape
from pathlib import Path


COMPACTION_TYPES = {"compaction"}


def prune_to_latest_compaction(items: list[dict]) -> tuple[list[dict], int]:
    """Return the canonical small window after server-side compaction."""
    for index in range(len(items) - 1, -1, -1):
        if items[index].get("type") in COMPACTION_TYPES:
            return items[index:], index
    return items, 0


def sessions_dir() -> Path:
    root = os.environ.get("MJJ_HOME") or "~/.mjj"
    path = Path(root).expanduser() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    path: Path | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path is None:
            stamp = time.strftime("%Y%m%dT%H%M%S")
            self.path = sessions_dir() / f"{stamp}-{self.id}.jsonl"
        new = not self.path.exists()
        self._handle = self.path.open("a", encoding="utf-8")
        if new:
            self._write(
                {
                    "kind": "meta",
                    "id": self.id,
                    "started": time.time(),
                    "cwd": str(Path.cwd()),
                    **self.meta,
                }
            )

    def _write(self, doc: dict) -> None:
        self._handle.write(json.dumps(doc, ensure_ascii=False) + "\n")
        self._handle.flush()

    def record(self, item: dict) -> None:
        self._write({"kind": "item", "item": item})

    def note(self, **fields) -> None:
        self._write({"kind": "note", **fields})

    def close(self) -> None:
        try:
            self._handle.close()
        except Exception:
            pass


@dataclass(frozen=True)
class SessionInfo:
    id: str
    path: Path
    cwd: str
    started: float
    modified: float
    items: int
    name: str = ""

    def summary(self) -> str:
        label = f" · {self.name}" if self.name else ""
        return f"{self.id}{label} · {self.items} items · {self.cwd}"


def _documents(path: Path) -> list[dict]:
    documents: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            document = json.loads(line)
        except ValueError:
            continue
        if isinstance(document, dict):
            documents.append(document)
    return documents


def load_items(path: Path) -> list[dict]:
    items = []
    for doc in _documents(path):
        if doc.get("kind") == "item":
            item = doc.get("item")
            if isinstance(item, dict):
                items.append(item)
    return prune_to_latest_compaction(items)[0]


def latest() -> Path | None:
    files = sorted(sessions_dir().glob("*.jsonl"))
    return files[-1] if files else None


def resolve_session(reference: str | Path | None = None) -> Path:
    if reference:
        candidate = Path(reference).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        needle = str(reference)
        matches = sorted(
            path for path in sessions_dir().glob("*.jsonl") if needle in path.name
        )
        if matches:
            return matches[-1]
        raise FileNotFoundError(f"no session matching {reference!r}")
    path = latest()
    if path is None:
        raise FileNotFoundError("no session to resume")
    return path


def inspect_session(reference: str | Path | None = None) -> SessionInfo:
    path = resolve_session(reference)
    documents = _documents(path)
    meta = next((doc for doc in documents if doc.get("kind") == "meta"), {})
    notes = [doc for doc in documents if doc.get("kind") == "note"]
    name = next((str(doc["name"]) for doc in reversed(notes) if doc.get("name")), "")
    return SessionInfo(
        id=str(meta.get("id") or path.stem.split("-")[-1]),
        path=path,
        cwd=str(meta.get("cwd") or ""),
        started=float(meta.get("started") or path.stat().st_mtime),
        modified=path.stat().st_mtime,
        items=sum(doc.get("kind") == "item" for doc in documents),
        name=name,
    )


def list_sessions(*, limit: int = 20) -> list[SessionInfo]:
    paths = sorted(sessions_dir().glob("*.jsonl"), reverse=True)
    infos = []
    for path in paths[: max(0, limit)]:
        try:
            infos.append(inspect_session(path))
        except (OSError, TypeError, ValueError):
            continue
    return infos


def resume(session_id: str | None = None) -> tuple[Session, list[dict]]:
    path = resolve_session(session_id)
    items = load_items(path)
    session = Session(id=inspect_session(path).id, path=path)
    return session, items


def fork_session(
    reference: str | Path | None = None,
    *,
    through: int | None = None,
) -> tuple[Session, list[dict]]:
    source = resolve_session(reference)
    source_info = inspect_session(source)
    items = load_items(source)
    if through is not None:
        if through < 0 or through > len(items):
            raise ValueError(f"branch point must be between 0 and {len(items)}")
        items = items[:through]
    session = Session(
        meta={
            "forked_from": str(source),
            "forked_through": through,
            "cwd": source_info.cwd or str(Path.cwd()),
        }
    )
    for item in items:
        session.record(item)
    session.note(forked_from=str(source))
    return session, items


def import_session(source: str | Path) -> tuple[Session, list[dict]]:
    """Copy a native mjj JSONL transcript into managed session storage."""
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"session file does not exist: {path}")
    documents = _documents(path)
    if not documents:
        raise ValueError(f"session file contains no JSON objects: {path}")
    items = [
        document["item"]
        for document in documents
        if document.get("kind") == "item" and isinstance(document.get("item"), dict)
    ]
    if not items:
        # Also accept one raw Responses item per line for simple interchange.
        items = [document for document in documents if document.get("type")]
    if not items:
        raise ValueError(f"session file contains no importable items: {path}")
    metadata = next(
        (document for document in documents if document.get("kind") == "meta"),
        {},
    )
    session = Session(
        meta={
            "imported_from": str(path),
            "cwd": str(metadata.get("cwd") or Path.cwd()),
        }
    )
    for item in prune_to_latest_compaction(items)[0]:
        session.record(item)
    session.note(imported_from=str(path))
    return session, prune_to_latest_compaction(items)[0]


def export_session(
    reference: str | Path | None,
    destination: str | Path,
) -> Path:
    source = resolve_session(reference)
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".jsonl":
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return target
    if target.suffix.lower() not in (".html", ".htm"):
        target = target.with_suffix(".html")
    info = inspect_session(source)
    blocks = []
    for item in load_items(source):
        item_type = item.get("type")
        if item_type == "message":
            role = escape(str(item.get("role") or "message"))
            text = "".join(
                str(part.get("text") or "[image]")
                for part in item.get("content") or []
                if isinstance(part, dict)
            )
            blocks.append(
                f'<section class="message {role}"><b>{role}</b>'
                f'<pre>{escape(text)}</pre></section>'
            )
        elif item_type == "function_call":
            name = escape(str(item.get("name") or "tool"))
            arguments = escape(str(item.get("arguments") or ""))
            blocks.append(
                f'<section class="tool"><b>{name}</b>'
                f"<pre>{arguments}</pre></section>"
            )
        elif item_type == "function_call_output":
            output = escape(str(item.get("output") or ""))
            blocks.append(
                '<section class="tool result"><b>result</b>'
                f"<pre>{output}</pre></section>"
            )
    title = escape(info.name or f"mjj session {info.id}")
    target.write_text(
        "<!doctype html><meta charset=utf-8><title>"
        + title
        + "</title><style>body{max-width:920px;margin:40px auto;padding:0 20px;"
        "background:#0b0d0e;color:#e7ece8;font:15px system-ui}"
        "section{border-left:3px solid #38413d;padding:8px 14px;margin:14px 0}"
        ".user{border-color:#bdff39}.assistant{border-color:#36d9ff}"
        ".tool{border-color:#737b77;color:#c7cfca}pre{white-space:pre-wrap;"
        "overflow-wrap:anywhere;font:13px ui-monospace,monospace}</style>"
        f"<h1>{title}</h1><p>{escape(str(source))}</p>"
        + "".join(blocks),
        encoding="utf-8",
    )
    return target
