# Durable goals

Goals keep one verifiable workspace objective alive across model turns,
sessions, compaction, and process restarts. Goal state is stored separately
from the append-only transcript under `$MJJ_HOME/goals/`, mode `0600`, so
forking a conversation does not silently replace the workspace objective.

## Interactive workflow

Set a goal from the terminal app:

```text
/goal Finish the router migration without changing the public API. Stop when the focused and full test suites pass.
```

The agent begins immediately, works in checkpoints, and continues until the
goal tool records evidence-backed completion, the configured continuation
limit is reached, or a human interrupts it. Inspect and control it with:

```text
/goal
/goal pause optional checkpoint note
/goal resume
/goal complete verification evidence
/goal blocked exact external dependency
/goal clear
```

`complete` and `blocked` require a message. The model receives the same rule:
it may not claim the stopping condition without recording evidence.

## Headless workflow

Start and follow a goal in one command:

```bash
mjj exec --goal "Implement PLAN.md and stop when pytest passes" \
  --auto-max-turns 8
```

The existing `--auto-max-turns` budget also bounds goal continuations. Zero
means unlimited until the goal completes or the process is interrupted. When
the limit is reached, the goal remains active for the next invocation rather
than being discarded.

Manage state without calling a model:

```bash
mjj goal
mjj goal set "Finish the migration and verify rollback"
mjj goal pause "waiting for the fixture"
mjj goal resume
mjj goal complete "focused and full suites pass"
mjj goal clear
```

Use `mjj goal --json` for automation. Goal progress retains at most 50 bounded
entries; corrupt or partial state degrades to no active goal instead of
preventing agent startup.

## Token and safety boundary

The goal tool is registered only while the workspace has an active goal, so
ordinary sessions pay no permanent tool-schema cost. Objective text is capped
at 16 KiB, checkpoint messages and evidence are capped at 2 KiB each, and
atomic replacement prevents readers observing a partially written update.
