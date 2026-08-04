"""External, bounded snapshots for conflict-safe patch undo."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_CHECKPOINTS = 50
MAX_FILES = 256
MAX_BYTES = 20 * 1024 * 1024


class CheckpointError(ValueError):
    pass


class CheckpointConflict(CheckpointError):
    pass


@dataclass(frozen=True)
class CheckpointInfo:
    identifier: str
    created: float
    files: int
    bytes: int
    undone: bool = False


@dataclass(frozen=True)
class PendingCheckpoint:
    identifier: str
    directory: Path
    manifest: dict


class CheckpointStore:
    """Secure checkpoint storage outside the user's working tree."""

    def __init__(self, workspace: str | Path, root: str | Path | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        configured = root or os.environ.get("MJJ_CHECKPOINT_ROOT")
        if configured:
            base = Path(configured).expanduser().resolve()
        else:
            cache = os.environ.get("XDG_CACHE_HOME")
            base = (
                Path(cache).expanduser()
                if cache
                else Path.home() / ".cache"
            ) / "mjj" / "checkpoints"
        workspace_key = hashlib.sha256(
            os.fsencode(str(self.workspace))
        ).hexdigest()[:20]
        self.root = base / workspace_key
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.cleanup()

    def begin(self, paths: Iterable[Path]) -> PendingCheckpoint:
        resolved = list(dict.fromkeys(path.resolve() for path in paths))
        if not resolved:
            raise CheckpointError("checkpoint has no files")
        if len(resolved) > MAX_FILES:
            raise CheckpointError(f"checkpoint exceeds {MAX_FILES} files")
        for path in resolved:
            self._relative(path)
            if path.exists() and (not path.is_file() or path.is_symlink()):
                raise CheckpointError(f"checkpoint path is not a regular file: {path}")

        identifier = f"{time.time_ns():x}-{secrets.token_hex(3)}"
        directory = self.root / identifier
        directory.mkdir(mode=0o700)
        records: list[dict] = []
        total = 0
        try:
            for number, path in enumerate(resolved):
                existed = path.exists()
                content = path.read_bytes() if existed else b""
                total += len(content)
                if total > MAX_BYTES:
                    raise CheckpointError(
                        f"checkpoint exceeds {MAX_BYTES // (1024 * 1024)} MiB"
                    )
                blob = None
                if existed:
                    blob = f"{number:04}.bin"
                    destination = directory / blob
                    destination.write_bytes(content)
                    os.chmod(destination, 0o600)
                records.append(
                    {
                        "path": self._relative(path),
                        "existed": existed,
                        "mode": stat.S_IMODE(path.stat().st_mode) if existed else None,
                        "before": _digest(content) if existed else None,
                        "after": None,
                        "after_mode": None,
                        "blob": blob,
                    }
                )
            manifest = {
                "version": 1,
                "id": identifier,
                "workspace": str(self.workspace),
                "created": time.time(),
                "bytes": total,
                "files": records,
                "complete": False,
                "undone": False,
            }
            self._write_manifest(directory, manifest)
            return PendingCheckpoint(identifier, directory, manifest)
        except Exception:
            _remove_tree(directory)
            raise

    def finish(
        self,
        pending: PendingCheckpoint,
        *,
        expected: Mapping[Path, bytes | None] | None = None,
    ) -> CheckpointInfo:
        manifest = pending.manifest
        for record in manifest["files"]:
            path = (self.workspace / record["path"]).resolve()
            if expected is not None and path in expected:
                content = expected[path]
                record["after"] = _digest(content) if content is not None else None
                record["after_mode"] = (
                    int(record.get("mode") or 0o644)
                    if content is not None
                    else None
                )
            else:
                record["after"] = _path_digest(path)
                record["after_mode"] = (
                    stat.S_IMODE(path.stat().st_mode)
                    if path.exists() and path.is_file() and not path.is_symlink()
                    else None
                )
        manifest["complete"] = True
        self._write_manifest(pending.directory, manifest)
        self.cleanup()
        return self._info(manifest)

    def cancel(self, pending: PendingCheckpoint) -> None:
        _remove_tree(pending.directory)

    def list(self) -> list[CheckpointInfo]:
        checkpoints = []
        for directory in self._directories():
            try:
                manifest = self._read_manifest(directory)
            except (OSError, ValueError, KeyError):
                continue
            if manifest.get("complete"):
                checkpoints.append(self._info(manifest))
        return sorted(checkpoints, key=lambda item: item.created, reverse=True)

    def undo(self, identifier: str | None = None) -> CheckpointInfo:
        if identifier:
            if not _safe_identifier(identifier):
                raise CheckpointError("invalid checkpoint id")
            directory = self.root / identifier
            if not directory.is_dir():
                raise CheckpointError(f"unknown checkpoint: {identifier}")
        else:
            latest = next((item for item in self.list() if not item.undone), None)
            if latest is None:
                raise CheckpointError("no checkpoint to undo")
            directory = self.root / latest.identifier
        manifest = self._read_manifest(directory)
        if not manifest.get("complete"):
            raise CheckpointError("checkpoint is incomplete")
        if manifest.get("undone"):
            raise CheckpointError("checkpoint was already undone")
        if manifest.get("workspace") != str(self.workspace):
            raise CheckpointError("checkpoint belongs to another workspace")

        conflicts = []
        for record in manifest["files"]:
            path = self._manifest_path(record["path"])
            current_mode = (
                stat.S_IMODE(path.stat().st_mode)
                if path.exists() and path.is_file() and not path.is_symlink()
                else None
            )
            if (
                _path_digest(path) != record.get("after")
                or current_mode != record.get("after_mode")
            ):
                conflicts.append(record["path"])
        if conflicts:
            shown = ", ".join(conflicts[:8])
            raise CheckpointConflict(
                f"files changed after checkpoint; refusing undo: {shown}"
            )

        current = {
            self._manifest_path(item["path"]): _capture(
                self._manifest_path(item["path"])
            )
            for item in manifest["files"]
        }
        try:
            for record in manifest["files"]:
                path = self._manifest_path(record["path"])
                if not record["existed"]:
                    path.unlink(missing_ok=True)
                    continue
                blob = self._blob_path(directory, record.get("blob"))
                _atomic_write(path, blob.read_bytes(), int(record["mode"] or 0o644))
        except Exception:
            _restore_captures(current)
            raise
        manifest["undone"] = True
        manifest["undone_at"] = time.time()
        self._write_manifest(directory, manifest)
        return self._info(manifest)

    def cleanup(self) -> None:
        cutoff = time.time() - RETENTION_SECONDS
        for position, directory in enumerate(self._directories()):
            try:
                created = directory.stat().st_mtime
                manifest = directory / "manifest.json"
                if manifest.is_file():
                    created = float(json.loads(manifest.read_text())["created"])
                if position >= MAX_CHECKPOINTS or created < cutoff:
                    _remove_tree(directory)
            except (OSError, ValueError, KeyError):
                continue

    def _directories(self) -> list[Path]:
        try:
            return sorted(
                (path for path in self.root.iterdir() if path.is_dir()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []

    def _relative(self, path: Path) -> str:
        try:
            relative = path.relative_to(self.workspace)
        except ValueError:
            raise CheckpointError(f"checkpoint path escapes workspace: {path}") from None
        if relative == Path("."):
            raise CheckpointError("checkpoint path must name a file")
        return relative.as_posix()

    def _manifest_path(self, value: object) -> Path:
        if not isinstance(value, str) or not value:
            raise CheckpointError("invalid checkpoint path")
        path = (self.workspace / value).resolve()
        self._relative(path)
        return path

    @staticmethod
    def _blob_path(directory: Path, value: object) -> Path:
        if (
            not isinstance(value, str)
            or len(value) != 8
            or not value[:4].isdigit()
            or value[4:] != ".bin"
        ):
            raise CheckpointError("invalid checkpoint blob")
        path = directory / value
        if not path.is_file() or path.is_symlink():
            raise CheckpointError("checkpoint blob is missing")
        return path

    @staticmethod
    def _write_manifest(directory: Path, manifest: dict) -> None:
        destination = directory / "manifest.json"
        temporary = directory / ".manifest.tmp"
        temporary.write_text(json.dumps(manifest, separators=(",", ":")))
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)

    @staticmethod
    def _read_manifest(directory: Path) -> dict:
        value = json.loads((directory / "manifest.json").read_text())
        if not isinstance(value, dict) or value.get("version") != 1:
            raise CheckpointError("unsupported checkpoint manifest")
        if (
            not isinstance(value.get("id"), str)
            or not _safe_identifier(value["id"])
            or value["id"] != directory.name
            or not isinstance(value.get("workspace"), str)
            or not isinstance(value.get("created"), (int, float))
            or not isinstance(value.get("complete"), bool)
            or not isinstance(value.get("undone"), bool)
        ):
            raise CheckpointError("invalid checkpoint manifest")
        files = value.get("files")
        if not isinstance(files, list) or len(files) > MAX_FILES or any(
            not isinstance(item, dict) for item in files
        ):
            raise CheckpointError("invalid checkpoint file manifest")
        for item in files:
            if (
                not isinstance(item.get("path"), str)
                or not isinstance(item.get("existed"), bool)
                or not isinstance(item.get("mode"), (int, type(None)))
                or not isinstance(item.get("after"), (str, type(None)))
                or not isinstance(item.get("after_mode"), (int, type(None)))
            ):
                raise CheckpointError("invalid checkpoint file record")
        return value

    @staticmethod
    def _info(manifest: dict) -> CheckpointInfo:
        return CheckpointInfo(
            identifier=str(manifest["id"]),
            created=float(manifest["created"]),
            files=len(manifest["files"]),
            bytes=int(manifest.get("bytes", 0)),
            undone=bool(manifest.get("undone")),
        )


def store_for(workspace: str | Path, state: dict) -> CheckpointStore:
    """Reuse one store per tool context without adding a process-global cache."""
    cached = state.get("checkpoint-store")
    root = Path(workspace).expanduser().resolve()
    if isinstance(cached, CheckpointStore) and cached.workspace == root:
        return cached
    store = CheckpointStore(root)
    state["checkpoint-store"] = store
    return store


def _safe_identifier(value: str) -> bool:
    return bool(value) and all(character in "0123456789abcdef-" for character in value)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _path_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        return "invalid"
    return _digest(path.read_bytes())


def _capture(path: Path) -> tuple[bool, bytes, int]:
    if not path.exists():
        return False, b"", 0o644
    return True, path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.mjj-undo-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_captures(captures: dict[Path, tuple[bool, bytes, int]]) -> None:
    for path, (existed, content, mode) in captures.items():
        try:
            if existed:
                _atomic_write(path, content, mode)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _remove_tree(directory: Path) -> None:
    try:
        for path in directory.iterdir():
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
        directory.rmdir()
    except OSError:
        return
