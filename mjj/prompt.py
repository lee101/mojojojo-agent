"""The system prompt.

Kept short on purpose. It is resent on every turn of every session; a
paragraph that only helps one task in fifty is a permanent tax on the other
forty-nine. Tool-specific detail belongs in the tool's own description, where
the model reads it in the same place it decides to call it.
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
- `skill` lists and loads specialized workflows. Load a matching skill before
  doing domain-specific work; its bundled paths can then be read normally.

Style:
- Match the surrounding code. Its conventions beat your preferences.
- Do not add comments that restate the code.
- If a command fails, read the error before changing anything.
- Short status lines while working are fine; the user can already see tools
  and thinking. End with one or two sentences on what changed.
"""


_PROJECT_DOC_MARKER = "\n\n--- project-doc ---\n\n"
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
    """Add one model-family hint while leaving repository rules last."""
    hint = _MODEL_HINTS.get(model_family(model))
    if not hint or hint in instructions:
        return instructions
    base, marker, project = instructions.partition(_PROJECT_DOC_MARKER)
    return f"{base}\n\n{hint}{marker}{project}"


__all__ = ["SYSTEM_PROMPT", "for_model", "model_family"]
