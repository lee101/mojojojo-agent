# Skills

Skills keep specialized instructions out of the permanent system prompt. Each
skill is a directory containing `SKILL.md` with small YAML-style frontmatter:

```markdown
---
name: mojo-port
description: Port a measured Python hot loop to Mojo and prove parity.
---

# Workflow
...
```

Discovery checks explicit configured paths, then these directories at the Git
root:

- `.mjj/skills/`
- `.agents/skills/`
- `.codex/skills/`
- `.claude/skills/`

Local CLI sessions also check `$MJJ_HOME/skills`, `~/.codex/skills`, and
`~/.claude/skills`. Hosted sessions intentionally skip all user directories.
Symlinked skill roots and bundled files are not followed.

```bash
mjj skills
mjj skills --json
```

The model calls `skill {}` for the bounded catalog, then
`skill {"name":"mojo-port"}` to load instructions. If two scopes expose the
same short name, the catalog's `project:mojo-port` or `user:mojo-port`
qualification removes ambiguity. Relative references in the instructions are
resolved from the reported base directory and read through the normal bounded
filesystem tools.
