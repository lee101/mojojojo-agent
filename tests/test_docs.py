from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
AGENT_INSTRUCTION_MAX_BYTES = 4 * 1024


def test_repository_local_markdown_links_resolve() -> None:
    documents = [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "DEV.md",
        ROOT / "developers.md",
    ]
    documents.extend(sorted((ROOT / "docs").glob("*.md")))
    broken: list[str] = []

    for document in documents:
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            target = raw_target.split("#", 1)[0].strip()
            if not target or "://" in target or target.startswith(("#", "/", "mailto:")):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{document.relative_to(ROOT)} -> {raw_target}")

    assert broken == []


def test_always_loaded_agent_instructions_stay_concise() -> None:
    instructions = (ROOT / "AGENTS.md").read_bytes()

    assert len(instructions) <= AGENT_INSTRUCTION_MAX_BYTES, (
        "AGENTS.md is injected into every repository turn; move discoverable "
        "detail to DEV.md, developers.md, or docs/ instead of increasing its "
        "4 KiB budget"
    )
