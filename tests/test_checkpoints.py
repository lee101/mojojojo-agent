from __future__ import annotations

import json
import os

from mjj import checkpoints as checkpoint_module
from mjj.checkpoints import CheckpointStore
from mjj.ledger import Ledger
from mjj.permissions import PermissionPolicy
from mjj.tools import build_registry
from mjj.tools.base import ToolContext


def _patch(registry, context, body: str):
    return registry.dispatch("apply_patch", json.dumps({"input": body}), context)


def test_patch_checkpoint_restores_add_update_delete_and_mode(
    tmp_path, monkeypatch
) -> None:
    checkpoint_root = tmp_path / "external-checkpoints"
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(checkpoint_root))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    editable = workspace / "edit.sh"
    editable.write_text("old\n")
    editable.chmod(0o755)
    deleted = workspace / "deleted.txt"
    deleted.write_text("restore me\n")
    context = ToolContext(workspace, Ledger())
    registry = build_registry(only=["patch", "checkpoint"])

    changed = _patch(
        registry,
        context,
        """*** Begin Patch
*** Update File: edit.sh
@@
-old
+new
*** Add File: added.txt
+created
*** Delete File: deleted.txt
*** End Patch""",
    )
    restored = registry.dispatch(
        "checkpoint", '{"action":"undo"}', context
    )

    assert changed.ok and changed.meta["checkpoint"]
    assert restored.ok
    assert editable.read_text() == "old\n"
    if os.name != "nt":
        assert os.stat(editable).st_mode & 0o777 == 0o755
    assert deleted.read_text() == "restore me\n"
    assert not (workspace / "added.txt").exists()
    assert checkpoint_root.resolve() not in workspace.resolve().parents
    store = CheckpointStore(workspace)
    checkpoint_dir = store.root / changed.meta["checkpoint"]
    if os.name != "nt":
        assert checkpoint_dir.stat().st_mode & 0o777 == 0o700
        assert (checkpoint_dir / "manifest.json").stat().st_mode & 0o777 == 0o600


def test_checkpoint_refuses_to_overwrite_later_user_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(tmp_path / "checkpoints"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "value.txt"
    target.write_text("one\n")
    context = ToolContext(workspace, Ledger())
    registry = build_registry(only=["patch", "checkpoint"])
    changed = _patch(
        registry,
        context,
        """*** Begin Patch
*** Update File: value.txt
@@
-one
+two
*** End Patch""",
    )
    target.write_text("user changed this after the patch\n")

    restored = registry.dispatch(
        "checkpoint",
        json.dumps({"action": "undo", "id": changed.meta["checkpoint"]}),
        context,
    )

    assert not restored.ok
    assert "refusing undo" in restored.output
    assert target.read_text() == "user changed this after the patch\n"


def test_checkpoint_undo_obeys_read_only_permissions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(tmp_path / "checkpoints"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "value.txt"
    target.write_text("one\n")
    context = ToolContext(workspace, Ledger())
    registry = build_registry(only=["patch", "checkpoint"])
    changed = _patch(
        registry,
        context,
        """*** Begin Patch
*** Update File: value.txt
@@
-one
+two
*** End Patch""",
    )
    context.approve = PermissionPolicy("read-only")

    restored = registry.dispatch(
        "checkpoint",
        json.dumps({"action": "undo", "id": changed.meta["checkpoint"]}),
        context,
    )

    assert not restored.ok and restored.meta["denied"]
    assert target.read_text() == "two\n"


def test_patch_reports_when_checkpoint_safety_limit_is_exceeded(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(tmp_path / "checkpoints"))
    monkeypatch.setattr(checkpoint_module, "MAX_BYTES", 1)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "value.txt"
    target.write_text("one\n")

    result = _patch(
        build_registry(only=["patch"]),
        ToolContext(workspace, Ledger()),
        """*** Begin Patch
*** Update File: value.txt
@@
-one
+two
*** End Patch""",
    )

    assert result.ok
    assert result.meta["checkpoint"] is None
    assert "checkpoint unavailable" in result.output
    assert target.read_text() == "two\n"


def test_checkpoint_rejects_tampered_blob_paths(tmp_path, monkeypatch) -> None:
    root = tmp_path / "checkpoints"
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(root))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "value.txt"
    target.write_text("one\n")
    context = ToolContext(workspace, Ledger())
    registry = build_registry(only=["patch", "checkpoint"])
    changed = _patch(
        registry,
        context,
        """*** Begin Patch
*** Update File: value.txt
@@
-one
+two
*** End Patch""",
    )
    store = CheckpointStore(workspace)
    manifest_path = store.root / changed.meta["checkpoint"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["blob"] = "../../outside"
    manifest_path.write_text(json.dumps(manifest))

    restored = registry.dispatch(
        "checkpoint",
        json.dumps({"action": "undo", "id": changed.meta["checkpoint"]}),
        context,
    )

    assert not restored.ok
    assert "invalid checkpoint blob" in restored.output
    assert target.read_text() == "two\n"


def test_checkpoint_cleanup_enforces_count_and_age(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(tmp_path / "checkpoints"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "value.txt"
    target.write_text("value\n")
    store = CheckpointStore(workspace)
    identifiers = []
    for _ in range(3):
        identifiers.append(store.finish(store.begin([target])).identifier)

    monkeypatch.setattr(checkpoint_module, "MAX_CHECKPOINTS", 2)
    store.cleanup()

    retained = store.list()
    assert len(retained) == 2
    old = store.root / retained[-1].identifier / "manifest.json"
    manifest = json.loads(old.read_text())
    manifest["created"] = 0
    old.write_text(json.dumps(manifest))
    store.cleanup()
    assert not old.parent.exists()


def test_expected_post_patch_hash_detects_commit_finish_race(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MJJ_CHECKPOINT_ROOT", str(tmp_path / "checkpoints"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "value.txt"
    target.write_text("before\n")
    store = CheckpointStore(workspace)
    pending = store.begin([target])
    target.write_text("user raced after commit\n")
    checkpoint = store.finish(pending, expected={target.resolve(): b"patch\n"})

    try:
        store.undo(checkpoint.identifier)
    except checkpoint_module.CheckpointConflict:
        pass
    else:  # pragma: no cover - the conflict is the safety property
        raise AssertionError("undo should refuse a post-commit race")
    assert target.read_text() == "user raced after commit\n"
