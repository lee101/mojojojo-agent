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


def load_items(path: Path) -> list[dict]:
    items: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if doc.get("kind") == "item":
            items.append(doc["item"])
    return prune_to_latest_compaction(items)[0]


def latest() -> Path | None:
    files = sorted(sessions_dir().glob("*.jsonl"))
    return files[-1] if files else None


def resume(session_id: str | None = None) -> tuple[Session, list[dict]]:
    if session_id:
        matches = sorted(sessions_dir().glob(f"*{session_id}*.jsonl"))
        path = matches[-1] if matches else None
    else:
        path = latest()
    if path is None:
        raise FileNotFoundError("no session to resume")
    items = load_items(path)
    session = Session(id=path.stem.split("-")[-1], path=path)
    return session, items
