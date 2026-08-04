# Loops and goals

Loops keep the current session moving; goals keep one workspace objective
durable across sessions. Neither changes tool permissions.

## Interactive loops

```text
/loop steps 4
/loop ideas 2
/loop full 8
/loop forever
/loop off
```

`steps` executes the next concrete work on the current objective. `ideas`
waits for that objective to finish, selects a new high-impact improvement that
has not already been completed or rejected, and starts it. `full` does both.
The optional number caps synthetic continuation turns; `forever` is `full`
with a zero (unlimited) cap. `/auto` remains an alias.

Unlimited means until `Ctrl+C`, process exit, an unrecoverable model error, or
an active goal reaches `complete` or `blocked`. It does not bypass Auto, Ask,
or Read-only permission policy. Prefer a finite turn count for unattended work:
every continuation is another model request, even when prompt caching reduces
the input cost.

## Headless loops

```bash
mjj exec --loop steps --loop-turns 4 "finish the failing tests"
mjj exec --loop forever "work through TODO.md, then choose the next improvement"
```

The longer `--auto-next-steps`, `--auto-next-idea`, and `--auto-max-turns`
flags remain compatible. `--loop forever` resets a configured finite limit to
unlimited.

## Goal mode

Use a goal when the stopping condition matters more than the current session:

```bash
mjj exec --goal "Migrate the router; stop when focused and full tests pass" \
  --loop-turns 8
```

An active goal continues automatically without also enabling loop mode. It
exposes the goal tool only while active, persists bounded checkpoints outside
the transcript, and remains active when a turn cap is reached. See
[durable goals](goals.md) for pause, resume, completion, and evidence commands.

## Prompt history

In the full terminal UI, Up and Down recall prompts from the current resolved
working directory. Consecutive duplicates are stored once; `Ctrl+R` searches
that directory's history. Files are keyed by a hash of the directory under
`$MJJ_HOME/prompt-history/`, with directory mode `0700` and file mode `0600`.
The older global `$MJJ_HOME/history` file is left untouched during upgrade.
