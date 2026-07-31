"""Atomic application of the Codex patch envelope."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .base import ToolContext, ToolResult

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File: "
_DELETE = "*** Delete File: "
_UPDATE = "*** Update File: "
_EOF = "*** End of File"


class PatchError(ValueError):
    pass


@dataclass
class _Chunk:
    section: str | None
    old: list[str]
    new: list[str]
    end_of_file: bool = False
    additions: int = 0
    deletions: int = 0


@dataclass
class _Operation:
    kind: str
    path: str
    added: list[str] | None = None
    chunks: list[_Chunk] | None = None


@dataclass
class _Snapshot:
    existed: bool
    content: bytes | None
    mode: int | None


def _result(
    ctx: ToolContext,
    text: str,
    *,
    ok: bool = True,
    hint: str = "",
    **meta: object,
) -> ToolResult:
    return ToolResult(
        ctx.ledger.clip("apply_patch", text, hint),
        ok=ok,
        meta=dict(meta),
    )


class ApplyPatchTool:
    name = "apply_patch"
    description = "Atomically apply a Codex *** Begin Patch file patch."
    parameters = {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "Complete *** Begin Patch / *** End Patch envelope",
            }
        },
        "required": ["input"],
        "additionalProperties": False,
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        patch = args.get("input")
        if not isinstance(patch, str):
            return _result(ctx, "input must be a string", ok=False)
        try:
            operations = _parse(patch)
            summaries = _plan_and_commit(operations, ctx)
        except (PatchError, OSError) as exc:
            return _result(
                ctx,
                f"patch failed: {exc}",
                ok=False,
                hint="read the target around the failed context and retry",
            )
        lines = [f"{path}: +{counts[0]} -{counts[1]}" for path, counts in summaries.items()]
        return _result(
            ctx,
            "\n".join(lines),
            hint="split very large patches into smaller patches",
            files=list(summaries),
        )


def _parse(text: str) -> list[_Operation]:
    lines = text.strip().splitlines()
    if len(lines) < 3 or lines[0] != _BEGIN or lines[-1] != _END:
        raise PatchError("expected *** Begin Patch and *** End Patch")
    operations: list[_Operation] = []
    index = 1
    while index < len(lines) - 1:
        line = lines[index]
        if line.startswith(_ADD):
            path = _header_path(line, _ADD, index + 1)
            index += 1
            content = []
            while index < len(lines) - 1 and not _file_header(lines[index]):
                if not lines[index].startswith("+"):
                    raise PatchError(
                        f"line {index + 1}: add-file lines must start with +"
                    )
                content.append(lines[index][1:])
                index += 1
            if not content:
                raise PatchError(f"line {index + 1}: add file has no content")
            operations.append(_Operation("add", path, added=content))
            continue
        if line.startswith(_DELETE):
            path = _header_path(line, _DELETE, index + 1)
            operations.append(_Operation("delete", path))
            index += 1
            continue
        if line.startswith(_UPDATE):
            path = _header_path(line, _UPDATE, index + 1)
            index += 1
            chunks, index = _parse_chunks(lines, index)
            if not chunks:
                raise PatchError(f"line {index + 1}: update has no hunks")
            operations.append(_Operation("update", path, chunks=chunks))
            continue
        raise PatchError(f"line {index + 1}: expected a file operation")
    if not operations:
        raise PatchError("patch contains no file operations")
    return operations


def _header_path(line: str, marker: str, line_number: int) -> str:
    path = line[len(marker) :]
    if not path:
        raise PatchError(f"line {line_number}: missing path")
    return path


def _file_header(line: str) -> bool:
    return line.startswith((_ADD, _DELETE, _UPDATE)) or line == _END


def _parse_chunks(lines: list[str], index: int) -> tuple[list[_Chunk], int]:
    chunks: list[_Chunk] = []
    while index < len(lines) - 1 and not _file_header(lines[index]):
        section = None
        if lines[index] == "@@" or lines[index].startswith("@@ "):
            section = lines[index][3:] if lines[index].startswith("@@ ") else None
            index += 1
        elif not lines[index].startswith((" ", "+", "-")):
            raise PatchError(f"line {index + 1}: expected @@ or a diff line")

        old: list[str] = []
        new: list[str] = []
        end_of_file = False
        additions = 0
        deletions = 0
        while index < len(lines) - 1:
            line = lines[index]
            if line == _EOF:
                end_of_file = True
                index += 1
                break
            if line == "@@" or line.startswith("@@ ") or _file_header(line):
                break
            if not line.startswith((" ", "+", "-")):
                raise PatchError(
                    f"line {index + 1}: hunk lines must start with space, +, or -"
                )
            prefix, content = line[0], line[1:]
            if prefix in {" ", "-"}:
                old.append(content)
            if prefix in {" ", "+"}:
                new.append(content)
            additions += prefix == "+"
            deletions += prefix == "-"
            index += 1
        if not old and not new and section is None:
            raise PatchError(f"line {index + 1}: empty hunk")
        chunks.append(
            _Chunk(section, old, new, end_of_file, additions, deletions)
        )
    return chunks, index


def _patch_path(path_arg: str, ctx: ToolContext) -> Path:
    candidate = Path(path_arg)
    if candidate.is_absolute():
        raise PatchError(f"absolute paths are not allowed: {path_arg}")
    root = ctx.cwd.resolve()
    path = (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise PatchError(f"path escapes the workspace: {path_arg}") from None
    if path == root:
        raise PatchError("a patch path must name a file")
    return path


def _plan_and_commit(
    operations: list[_Operation], ctx: ToolContext
) -> dict[str, list[int]]:
    plans: dict[Path, bytes | None] = {}
    snapshots: dict[Path, _Snapshot] = {}
    summaries: dict[str, list[int]] = {}

    for operation in operations:
        path = _patch_path(operation.path, ctx)
        if path not in snapshots:
            snapshots[path] = _snapshot(path)
        exists = plans.get(path) is not None if path in plans else path.exists()
        counts = summaries.setdefault(operation.path, [0, 0])

        if operation.kind == "add":
            if exists:
                raise PatchError(f"file already exists: {operation.path}")
            assert operation.added is not None
            plans[path] = ("\n".join(operation.added) + "\n").encode()
            counts[0] += len(operation.added)
            continue

        if not exists:
            raise PatchError(f"file does not exist: {operation.path}")
        current = plans[path] if path in plans else path.read_bytes()
        assert current is not None

        if operation.kind == "delete":
            plans[path] = None
            counts[1] += len(current.splitlines())
            continue

        try:
            source = current.decode("utf-8")
        except UnicodeDecodeError:
            raise PatchError(f"cannot update non-UTF-8 file: {operation.path}") from None
        assert operation.chunks is not None
        updated = _apply_chunks(source, operation.chunks, operation.path)
        plans[path] = updated.encode("utf-8")
        for chunk in operation.chunks:
            counts[0] += chunk.additions
            counts[1] += chunk.deletions

    _commit(plans, snapshots)
    return summaries


def _snapshot(path: Path) -> _Snapshot:
    if not path.exists():
        return _Snapshot(False, None, None)
    if not path.is_file():
        raise PatchError(f"not a regular file: {path}")
    return _Snapshot(
        True,
        path.read_bytes(),
        stat.S_IMODE(path.stat().st_mode),
    )


def _apply_chunks(source: str, chunks: list[_Chunk], path: str) -> str:
    lines = source.splitlines()
    newline = "\r\n" if "\r\n" in source else "\n"
    final_newline = source.endswith(("\n", "\r"))
    cursor = 0

    for chunk in chunks:
        if chunk.section is not None:
            section_at = _find_section(lines, chunk.section, cursor)
            if section_at is None:
                raise PatchError(
                    f"context not found in {path}: @@ {chunk.section}"
                )
            cursor = section_at + 1
        if not chunk.old and not chunk.new:
            continue
        if not chunk.old:
            at = len(lines) if chunk.end_of_file else cursor
        else:
            at = _find_block(lines, chunk.old, cursor, chunk.end_of_file)
            if at is None:
                preview = next((line for line in chunk.old if line.strip()), "<blank>")
                raise PatchError(f"context not found in {path}: {preview}")
        lines[at : at + len(chunk.old)] = chunk.new
        cursor = at + len(chunk.new)

    result = newline.join(lines)
    if lines and (final_newline or not source):
        result += newline
    return result


def _find_section(lines: list[str], section: str, start: int) -> int | None:
    needle = section.strip()
    for index in range(start, len(lines)):
        candidate = lines[index].strip()
        if candidate == needle or needle in candidate:
            return index
    return None


def _find_block(
    lines: list[str], wanted: list[str], start: int, at_end: bool
) -> int | None:
    if len(wanted) > len(lines):
        return None
    last = len(lines) - len(wanted)
    if last < start:
        return None
    candidates = [last] if at_end else list(range(start, last + 1))
    for normalise in (lambda value: value, str.rstrip, str.strip):
        normal_wanted = [normalise(line) for line in wanted]
        for index in candidates:
            if [normalise(line) for line in lines[index : index + len(wanted)]] == normal_wanted:
                return index
    return None


def _commit(
    plans: dict[Path, bytes | None], snapshots: dict[Path, _Snapshot]
) -> None:
    staged: dict[Path, Path] = {}
    try:
        for path, content in plans.items():
            if content is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.mjj-", dir=path.parent
            )
            temporary_path = Path(temporary)
            staged[path] = temporary_path
            mode = snapshots[path].mode if snapshots[path].existed else 0o644
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary_path, mode or 0o644)
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise

        try:
            for path, content in plans.items():
                if content is None:
                    path.unlink()
                else:
                    os.replace(staged.pop(path), path)
        except Exception:
            _restore(snapshots)
            raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _restore(snapshots: dict[Path, _Snapshot]) -> None:
    for path, snapshot in snapshots.items():
        try:
            if not snapshot.existed:
                path.unlink(missing_ok=True)
                continue
            assert snapshot.content is not None
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.mjj-restore-", dir=path.parent
            )
            temporary_path = Path(temporary)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(snapshot.content)
            os.chmod(temporary_path, snapshot.mode or 0o644)
            os.replace(temporary_path, path)
        except OSError:
            pass


TOOLS = [ApplyPatchTool()]
