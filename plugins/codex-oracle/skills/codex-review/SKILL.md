---
name: codex-review
description: Codex code review workflow. Activates before committing or pushing code. Ensures Codex Oracle independently reviews all changes before they ship.
---

# Codex Code Review

Before committing or pushing any code changes, you MUST get an **independent** review.

Independent is the operative word. Handing the advisor your diagnosis and asking it to check
your work does not produce a review — it produces agreement. See Step 2.

## Step 1: Gather the diff
Run `git diff HEAD` to collect all tracked changes — staged AND unstaged (plain `git diff`
silently omits staged changes, so a staged-only commit would produce an empty review). Check
`git status --short` for untracked files that belong to the change and include them. If the
diff is large, also read the changed files so you can answer follow-ups without re-dispatching.

## Step 2: Dispatch Codex — BLIND

- **Codex Oracle**: `code_review` with the diff

> **The review is NOT complete until CODEX has answered.** Codex runs at max effort and often
> takes many minutes; long calls are backgrounded and return later as a task notification —
> that is normal. Never ship, commit, or declare the review done while the call is pending.
> WAIT for it (Monitor / the notification) and do other work meanwhile.

**Send the diff. Do not send your conclusion.**

| ❌ Anchored dispatch | ✅ Blind dispatch |
|---------------------|------------------|
| `context: "I fixed the N+1 by adding selectinload — confirm that's right"` | `context: "Loads order lines for the invoice grid. Runs under RLS."` |
| `context: "The root cause was the missing await"` | (say nothing about cause — let it find it) |
| `focus: "just double-check the lock is correct"` | `focus: "concurrency, correctness"` |

`context` and `focus` are **factual scoping fields** — what the code does, which invariants hold,
which axes matter. They are lint-checked for conclusion language, but the lint is a **heuristic**:
it catches common phrasings, not all of them. No banner is not proof you dispatched blind.

**If you have a belief about the code, pass `caller_hypothesis`.** It is presented to the advisor
as an unverified claim to *refute*, and returns an explicit **CONFIRMED / REFUTED / UNPROVEN**
verdict with file:line evidence. That is the only safe channel for your own view.

If a result comes back with a **⚠️ ANCHORING WARNING** banner, you contaminated that dispatch.
Re-run it blind before trusting any agreement in it.

## Step 3: Expect real web research
Codex runs with live web search (`web_search=live`). A complete review checks the *current*
upstream reality, not remembered API shapes:

- Are any APIs used here deprecated or changed upstream?
- Does any dependency touched here have a known CVE?
- Do version-specific claims come with a URL?

The server requires a **Sources** section. An external claim with no source was answered from
memory — push back and re-ask rather than acting on it.

## Step 4: Run the Runtime Capability check

> **A missing method fails at lint time — you find out in seconds. A present-but-unsupported
> method fails in production, on a real portal, on a real customer's document.**

Static checks cannot see this one. Type stubs describe the union of every backend a library
supports, not the one you deploy — a call can be present, type-clean and lint-clean while being
unimplemented by the engine underneath. (Playwright's `page.pdf()` is Headless-Chromium-only; on
a Firefox-based engine it failed on every page for a month, silently costing real evidence.)

Ask of this diff:
- Does it call into a **swappable backend** — browser engine, DB driver/dialect, storage/LLM/queue
  provider, cloud SDK against a compatible-but-not-identical endpoint, container-provided binary?
  If so: which backend implements each call, and is that the one deployed?
- Does it **swap** an engine/driver/provider/version? Then every call into that surface is suspect.
- Does any `try`/`except` degrade such a call into a no-op, a default, or a skipped write **without
  recording** whether it was a capability miss or a genuine failure?
- Is any parameter **accepted then ignored, clamped, or silently downgraded**?
- Does the test coverage run on the **production engine**, or only on the library's default?

The server injects this hunt into `code_review` automatically — but check it yourself too,
since it is the finding a fast reviewer most reliably misses.

## Step 5: Process findings
0. **Confirm Codex actually answered.** A dispatched call is not an answered call — a
   backgrounded call that never returned is no review at all. If there is no result yet,
   STOP and wait.
1. Collect all findings
2. Categorize by severity: CRITICAL > HIGH > MEDIUM > LOW
3. Any CRITICAL or HIGH finding MUST be addressed or explicitly acknowledged to the user
4. **Where Codex contradicts YOU, Codex carries** — unless you can disprove it by MEASURING
   the deployed system (measurement outranks the model; Codex has been wrong when it read
   newer upstream source instead of the installed binary).
5. Verify file:line citations in the tree that is canon for this task — Codex reads the whole
   workspace and can cite a sibling checkout.

## Step 6: Optional round 2 — adversarial
Once Codex has answered blind, it is legitimate to go back with your hypothesis and ask it to
refute it. Order matters: independent first, adversarial second.

## Step 7: Present to user
Summarize:
- What Codex found
- Your own assessment
- Where you disagree — both perspectives, and what measurement would settle it
- Which findings you've already fixed vs which need user input

## When upstream vendor source is part of the evidence
Source is the MAP; the installed binary is the TERRITORY. If a finding rests on reading
vendor source (codex internals, a library's repo), read the checkout that matches the
INSTALLED version — for codex, `python3 scripts/codex_src.py` keeps
`~/Documents/codex-installed` aligned to the installed CLI's release tag — and **state the
ref you read** in the finding. Main-branch HEAD and release tags are divergent; conclusions
drawn from the wrong ref have been wrong twice in this repo's history. Anything load-bearing
still gets confirmed against the running binary (probe / `--strict-config` / registry), not
the source alone.

## Operations: watching, collecting and stopping runs (v1.17.0)

Long Codex calls are backgrounded by Claude Code at ~120 s; the MCP task panel then only
says "working" — the server's progress token is deregistered at that point, so nothing more
can be pushed to it. Use the plugin's own tools instead of tailing log files:

- `codex_runs()` — every run in this workspace: RUNNING / DETACHED / ok / error / cancelled /
  timeout / INTERRUPTED, elapsed, attempts, thread id, current activity, live-log path.
- `codex_run_log(run=..., lines=40)` — what the model is doing right now (reasoning
  summaries, commands, web searches, errors, retries), in-conversation.
- `codex_cancel_run(run=...)` — stop a run (SIGKILL its process group). Its thread stays on
  disk, so `codex_resume_run(run=..., nudge=...)` can still continue it.
- **"Connection closed" is NOT a failed review.** It means the MCP server restarted (`/mcp`
  reconnect, plugin reload, session exit). The codex process keeps running DETACHED on a
  file-backed spool with its own deadline watchdog. Do **not** re-dispatch: call
  `codex_resume_run(run=<id>)` — or with no argument, for the most recent recoverable run —
  from the new connection. It waits for the detached process if it is still running and
  returns its answer with the normal header, at no model cost. `codex_runs()` shows it as
  DETACHED meanwhile.
- A provider capacity shed ("Selected model is at capacity") is retried automatically with
  backoff on the SAME thread and model (v1.16.2). If it outlives the in-request budget the
  failure message says so — resume later; never switch models.

## Skip conditions
Only skip if the user explicitly says "skip review" or "just push it".
