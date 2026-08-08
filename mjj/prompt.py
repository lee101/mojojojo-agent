"""The system prompt.

Kept short on purpose. It is resent on every turn of every session; a
paragraph that only helps one task in fifty is a permanent tax on the other
forty-nine. Tool-specific detail belongs in the tool's own description, where
the model reads it in the same place it decides to call it.

Layout is cache-aware: the stable spine (base contract + project docs) comes
first; model-family hints are appended last so switching models only changes
the suffix and shared prefixes stay reusable.
"""

SYSTEM_PROMPT = """You are mjj, a coding agent working in a real repository.

Act fast. Prefer doing the next concrete step over planning out loud. Keep
working through tools until the request is finished or blocked; do not stop
after reconnaissance unless the user only asked for information.

Tools:
- `search` before `read`. It ranks by relevance and returns line anchors; a
  full file read costs 10-100x more and usually tells you less.
- `read` with a line range once you know where to look.
- `apply_patch` for edits. Never rewrite a file to change five lines.
- `list`, `search`, and ranged `read` replace shell `find`, `tree`, `rg`, and
  `sed`; they are faster in-process and return bounded output. Use `shell` for
  builds, tests, git, and anything the other tools do not cover.
- `py` to compute. It runs natively (Python compiled to Mojo), so measuring is
  cheaper than reasoning about performance in your head. Use it for real work:
  parsing, counting, simulating, checking a hypothesis against data.
- `navigate` for LSP definition/references/diagnostics/format/fix_all/rename
  when a language server is installed.
- `check` for syntax, optional format/fix/typecheck; `verify` for the
  project's self-test after meaningful edits; `commit` once checks are clean.
- Patches may auto-format/autofix changed files when `tools.post_edit` is on.
- `skill` lists and loads specialized workflows. Load a matching skill before
  doing domain-specific work; its bundled paths can then be read normally.
- `read_image` attaches a workspace screenshot/mockup as quality-85 WebP for
  vision. Prefer it over guessing UI feel from CSS alone.

Style:
- Match the surrounding code. Its conventions beat your preferences.
- Do not add comments that restate the code.
- If a command fails, read the error before changing anything.
- Short status lines while working are fine; the user can already see tools
  and thinking. End with one or two sentences on what changed.
"""


_PROJECT_DOC_MARKER = "\n\n--- project-doc ---\n\n"
_MODEL_HINT_MARKER = "\n\n--- model-hint ---\n\n"
_MODEL_HINTS = {
    "codex": "For Codex models, keep working through tools until the requested change is verified.",
    "grok": "For Grok models, favor exact tool calls over narration and keep working until verified.",
    "deepseek": "For DeepSeek models, keep calling tools until the task is done; do not stop after the first search or listing.",
}


def model_family(model: str) -> str:
    """Return the small prompt profile implied by a concrete model ID."""
    leaf = model.strip().lower().rsplit("/", 1)[-1]
    if leaf.startswith("grok-"):
        return "grok"
    if leaf.startswith("deepseek"):
        return "deepseek"
    if "codex" in leaf or leaf.startswith(
        ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
    ):
        return "codex"
    return "neutral"


def for_model(instructions: str, model: str) -> str:
    """Append one model-family hint after the stable instruction spine."""
    hint = _MODEL_HINTS.get(model_family(model))
    if not hint or hint in instructions:
        return instructions
    stable, _volatile = split_cache_layers(instructions)
    return f"{stable}{_MODEL_HINT_MARKER}{hint}"


def split_cache_layers(instructions: str) -> tuple[str, str]:
    """Split instructions into a reusable prefix and a volatile suffix.

    Project docs stay in the stable spine. Model-family hints are volatile so
    Anthropic-style block caching can mark only the shared prefix.
    """
    if _MODEL_HINT_MARKER in instructions:
        stable, _, volatile = instructions.partition(_MODEL_HINT_MARKER)
        return stable, volatile
    # Legacy mid-spine hints from older builds: keep project docs stable.
    if _PROJECT_DOC_MARKER in instructions:
        base, marker, rest = instructions.partition(_PROJECT_DOC_MARKER)
        for hint in _MODEL_HINTS.values():
            needle = f"\n\n{hint}"
            if needle in base:
                base = base.replace(needle, "", 1)
                return f"{base}{marker}{rest}", hint
            if base.endswith(hint):
                return f"{base[: -len(hint)].rstrip()}{marker}{rest}", hint
    for hint in _MODEL_HINTS.values():
        needle = f"\n\n{hint}"
        if instructions.endswith(needle):
            return instructions[: -len(needle)], hint
        if needle in instructions and _PROJECT_DOC_MARKER not in instructions:
            return instructions.replace(needle, "", 1), hint
    return instructions, ""


__all__ = [
    "SYSTEM_PROMPT",
    "for_model",
    "model_family",
    "split_cache_layers",
]
