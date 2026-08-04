# Reviewer and worker subagents

MJJ can delegate one to four independent tasks in a single model tool call.
The tasks execute concurrently, but their results are returned in the original
request order so the parent transcript stays deterministic.

Two roles are available:

- `reviewer` receives a permission-enforced read-only tool policy and must
  return prioritized findings with file and line evidence;
- `worker` may edit and test inside an isolated Git worktree. Its changes never
  land directly in the parent's checkout. MJJ captures only the worker's delta
  as a commit under `refs/mjj/subagents/<id>` and returns the exact
  `git cherry-pick <sha>` command for review.

Every child starts from a snapshot of the current checkout, including tracked
and untracked working changes. MJJ first commits that snapshot as an internal
baseline, so the returned worker commit excludes changes that already belonged
to the user. Snapshot input is capped at 64 MiB and 1,000 untracked files.

Each child has a separate append-only session, prompt-cache key, ledger, and
tool context. The default ceilings are 40 tool rounds, 8,000 output tokens, and
1,200 tokens per tool result. Full child transcripts remain available through
the normal session history, while the parent sees one bounded delegation
result. Child usage is merged into the parent usage total.

Example model call:

```json
{
  "tasks": [
    {"role": "reviewer", "prompt": "Audit the auth changes for regressions."},
    {"role": "worker", "prompt": "Add focused tests for expired credentials."}
  ]
}
```

Delegation requires Git because worker isolation and snapshot provenance depend
on worktrees. Setup failures are returned as ordinary failed tool results; they
do not crash the parent turn. Worktrees are removed after successful capture.
If a worker fails after changing files, MJJ retains its isolated worktree and
reports the path so partial work is recoverable.

Worktree isolation protects the parent's files and makes merges reviewable; it
is not an operating-system security sandbox. Workers inherit the same local
command authority as an auto-approved parent session.
