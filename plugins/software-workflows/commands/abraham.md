---
description: "Abraham: Codex analyzes deeply (codebase + live infra + live web), then implements in the target working tree with full access — you review the diff"
argument-hint: <task — what to build/fix/change, with the desired outcome>
---

Dispatch the following task to the **`abraham`** tool (`mcp__plugin_software-workflows_codex-oracle__abraham`;
`mcp__codex-oracle__abraham` from a user-scope server) — the
write-capable Codex mode — and orchestrate it exactly as you do the read tools: dispatch,
monitor, review. You remain the orchestrator; Codex does the implementation.

Abraham runs **two phases** inside one call, both with **full access**: no sandbox, network,
live web search, your MCP servers and your live infrastructure. An ANALYSIS phase (read-only by
instruction) produces an implementation brief; the IMPLEMENTATION phase
(`--dangerously-bypass-approvals-and-sandbox`) carries it out, runs the project's real checks
and reports their real results. There is no sealed mode: `full_access`, `infra` and
`web_search` are always on, and `false` for any of them is refused.

## Task

$ARGUMENTS

## How to dispatch

1. **Gather context first.** Identify the relevant paths, the current behavior, and any
   decisions already made this session. Pass what you know via `context` — codex should not
   have to rediscover what you already established — including the access paths you know (a
   key-based SSH host, a `docker exec` target, a local test database). Never credentials:
   codex finds its own way in from the project's docs and your local configuration. Put hard
   boundaries (files not to touch, APIs to keep stable) in `constraints`.
2. **Pick the target.** `cwd` may name any git repository on this machine: one below this
   workspace or another project. A run writes inside its target repository and the
   repositories nested in it; a change another repository needs is its own abraham run there.
3. **Preconditions the tool enforces** (resolve the reason rather than fighting them):
   - Must run inside a **git work tree** — autonomous writes without version control have
     no undo.
   - A **dirty tree is refused** by default: the implementer may legitimately rewrite files,
     and uncommitted edits have no undo. Commit that work first (never `git stash`); pass
     `allow_dirty=true` only when you and the user accept that risk knowingly.
   - **One write run per tree** (authoritative lockfile, held across both phases).

## While it runs

- The call will be backgrounded (long max-effort run) — normal. Watch it with `codex_runs` and
  `codex_run_log` (the analysis phase logs as `infra`, the implementation phase as `abraham`).
- **Do NOT edit files in this tree while the run is live** — one writer per tree. Queue your
  own edits for after the review.
- Write runs are **never auto-retried** (a replay after partial writes double-applies). If a
  run dies or is cancelled, check `git status` first — a partial run may have written files —
  then `codex_resume_run` continues it with full access and the same contract.

## After it returns

1. Read codex's report, the **[CHANGED FILES]** block (this run's changes are separated
   from pre-existing dirt; HEAD is verified unmoved — if it says HEAD MOVED, audit
   `git log` before anything else) and its **effective mode** line: what codex recorded for
   each of the run's turns, with anything but full access flagged.
2. **Review the actual diff** (`git diff`, plus new untracked files) — the report is a
   summary, the diff is the truth. Verify the change does what the task asked by running it
   for real yourself — the build and linters, and the app, command or endpoint it changes —
   and look at the results; no test cases unless the user explicitly asked for them.
3. Report to the user: what was implemented, what you verified, anything you'd push back on.
   **Commit only when the user says so** — never as part of this command; nothing is pushed.
