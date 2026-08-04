from __future__ import annotations

import json
from pathlib import Path

from mjj.session import (
    Session,
    export_session,
    fork_session,
    import_session,
    inspect_session,
    list_sessions,
    load_items,
    resolve_session,
    resume,
)


def _message(role: str, text: str) -> dict:
    content_type = "input_text" if role == "user" else "output_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": content_type, "text": text}],
    }


def test_sessions_can_be_named_resolved_and_resumed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    session = Session(meta={"cwd": str(tmp_path)})
    session.record(_message("user", "hello"))
    session.record(_message("assistant", "world"))
    session.note(name="named session")
    session.close()

    info = inspect_session(session.id)
    assert info.id == session.id
    assert info.name == "named session"
    assert info.cwd == str(tmp_path)
    assert info.items == 2
    assert resolve_session(session.id) == session.path
    assert list_sessions()[0].id == session.id

    resumed, items = resume(session.id)
    try:
        assert resumed.path == session.path
        assert items == [_message("user", "hello"), _message("assistant", "world")]
    finally:
        resumed.close()


def test_fork_copies_context_without_mutating_the_source(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    source = Session(meta={"cwd": str(tmp_path)})
    source.record(_message("user", "source"))
    source.close()

    fork, items = fork_session(source.id)
    fork_path = fork.path
    try:
        assert fork.id != source.id
        assert items == [_message("user", "source")]
        fork.record(_message("assistant", "fork only"))
    finally:
        fork.close()

    assert load_items(source.path) == [_message("user", "source")]
    assert load_items(fork_path) == [
        _message("user", "source"),
        _message("assistant", "fork only"),
    ]
    documents = [json.loads(line) for line in fork_path.read_text().splitlines()]
    assert documents[0]["forked_from"] == str(source.path)


def test_export_writes_jsonl_or_self_contained_escaped_html(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    session = Session()
    session.record(_message("user", "<script>alert(1)</script>"))
    session.record(_message("assistant", "safe & sound"))
    session.close()

    jsonl = export_session(session.id, tmp_path / "transcript.jsonl")
    html = export_session(session.id, tmp_path / "transcript.html")

    assert jsonl.read_text() == session.path.read_text()
    rendered = html.read_text()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "safe &amp; sound" in rendered
    assert "<script>alert(1)</script>" not in rendered


def test_import_copies_an_external_transcript_into_managed_storage(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    external = tmp_path / "external.jsonl"
    external.write_text(
        json.dumps(_message("user", "import me")) + "\n",
        encoding="utf-8",
    )

    session, items = import_session(external)
    try:
        assert session.path != external
        assert items == [_message("user", "import me")]
        assert load_items(session.path) == items
    finally:
        session.close()


def test_fork_can_branch_at_a_transcript_item(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MJJ_HOME", str(tmp_path / "home"))
    source = Session()
    source.record(_message("user", "one"))
    source.record(_message("assistant", "two"))
    source.record(_message("user", "three"))
    source.close()

    branch, items = fork_session(source.id, through=2)
    try:
        assert items == [_message("user", "one"), _message("assistant", "two")]
        assert load_items(branch.path) == items
    finally:
        branch.close()
