---
description: "Abraham: Codex analyzes deeply (codebase + live infra + live web), then a SEALED process implements in this working tree — you review the diff"
argument-hint: <task — what to build/fix/change, with the desired outcome>
---

Dispatch the following task to the **`abraham`** tool (`mcp__codex-oracle__abraham`) — the
write-capable Codex mode — and orchestrate it exactly as you do the read tools: dispatch,
monitor, review. You remain the orchestrator; Codex does the implementation.

Abraham runs as **two air-gapped phases** inside one call: a read-only ANALYSIS phase (this is
where `infra` and `web_search` apply) produces an implementation brief, then a **sealed**
IMPLEMENTATION phase — workspace-write file access and nothing else: no network, no web, no
MCP servers — carries it out. Untrusted web content and live credentials never share a process
with write capability.

## Task

$ARGUMENTS

## How to dispatch

1. **Gather context first.** Identify the relevant paths, the current behavior, and any
   decisions already made this session. Pass what you know via `context` — codex should not
   have to rediscover what you already established. Put hard boundaries (files not to touch,
   APIs to keep stable) in `constraints`.
2. **Pick the analysis-phase toggles:**
   - `infra=true, web_search=true` — the DEFAULT for `/abraham`: deepest analysis (live
     systems read-only + current vendor docs) before writing.
   - Drop `infra` when the task is purely local code with no live-system questions.
   - Drop `web_search` for sensitive trees — or run `research` first and feed its findings
     into `context`.
3. **Preconditions the tool enforces** (resolve the reason rather than fighting them):
   - Must run inside a **git work tree** — autonomous writes without version control have
     no undo.
   - A **dirty tree is refused** by default: the implementer may legitimately rewrite files,
     and uncommitted edits have no undo. Commit or stash first; pass `allow_dirty=true` only
     when you and the user accept that risk knowingly.
   - **One write run per tree** (authoritative lockfile, held across both phases).

## While it runs

- The call will be backgrounded (long max-effort run) — normal. Watch progress via
  `tail -f ~/.claude/logs/codex-oracle/latest.log` (analysis phase logs as `codex`/`infra`,
  implementation phase as `abraham`).
- **Do NOT edit files in this tree while the run is live** — one writer per tree. Queue your
  own edits for after the review.
- Write runs are **never auto-retried** (a replay after partial writes double-applies). If a
  run dies or is cancelled, check `git status` first — a partial run may have written files —
  then `codex_resume_run` continues it, sealed, with the same contract.

## After it returns

1. Read codex's report and the **[CHANGED FILES]** block (this run's changes are separated
   from pre-existing dirt; HEAD is verified unmoved — if it says HEAD MOVED, audit
   `git log` before anything else).
2. **Review the actual diff** (`git diff`, plus new untracked files) — the report is a
   summary, the diff is the truth. Verify the change does what the task asked; run the
   repo's checks (build/lint/targeted tests) yourself. A `.abraham/tmp/` scratch dir may
   appear — that's the sandboxed TMPDIR; safe to delete, never commit it.
3. Report to the user: what was implemented, what you verified, anything you'd push back on.
   **Commit only when the user says so** — never as part of this command; nothing is pushed.
