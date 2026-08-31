---
name: codex-planning
description: Codex planning workflow. Activates when planning features, architecture, or non-trivial changes. Ensures Codex Oracle is independently consulted before implementation begins.
---

# Codex Planning

When planning any non-trivial feature, fix, or architectural change, you MUST gather an
independent perspective before finalizing.

## Step 1: Launch the advisory — BLIND

- **Codex Oracle**: `architect_review` with the **requirement, the constraints, and the
  relevant file paths**
- **Your own agents**: Explore/Plan subagents for codebase investigation (dispatch them in the
  same batch — they are independent of the advisory call)

> **No plan is finalized until CODEX has answered.** It runs at max effort and often takes many
> minutes (backgrounded, returning as a task notification — normal). Never commit to a design
> while its answer is pending — wait for Codex and do other work meanwhile.

**Ask them to solve the problem. Do not ask them to bless your solution.**

| ❌ Anchored | ✅ Blind |
|------------|---------|
| "Review my approach: a Redis queue with a worker pool" | "We need durable retries across restarts, ~5k jobs/day, existing stack is Python + Postgres. What should we use?" |
| "We picked Temporal because Celery couldn't do X — sanity-check" | "Here are the durability and observability requirements. What fits?" |
| "Is this schema correct?" | "Here are the entities, the access patterns, and the consistency requirements. Design the schema." |

A prompt that names your design gets you a critique of your design — never the better option
nobody put on the table. **Put your preferred design in `caller_hypothesis`** instead: it is
presented as an unverified claim to refute and returns an explicit
**CONFIRMED / REFUTED / UNPROVEN** verdict.

If a result carries a **⚠️ ANCHORING WARNING** banner, that dispatch was contaminated — re-run it
blind before treating agreement as validation.

## Step 2: Expect prior art, not opinion
Codex runs with live web search (`web_search=live`). A design opinion with no reference to how
this has been solved before is speculation:

- How have others solved this, and what did they report going wrong?
- What are the current versions/limits/pricing of anything proposed?
- Are there known failure reports or migration-away write-ups?

The server requires a **Sources** section. Unsourced external claims were answered from memory —
re-ask.

When a needed source is beyond the advisor's own web reach (YouTube subtitles, RSS feeds,
semantic search, JS-heavy pages), fetch it yourself with a channel CLI such as `agent-reach`
(if installed) and pass the curated content — with its origin URL — as context. **The caller
fetches; the advisor receives.** Never hand the advisor a network-enabled sandbox to fetch for
itself: untrusted web content, full-disk read, and network egress must never share a process.

## Step 3: Synthesize findings
Once everything returns — **and that means Codex too; a pending backgrounded call is not an
answer**:
1. Summarize Codex's key findings alongside your own investigation
2. Identify agreements and disagreements
3. If Codex raised CONCERNS/REJECT — critically analyze why. Do you agree?
4. **Weigh agreement by independence**: Codex converging on your design after a blind dispatch
   is strong evidence; Codex blessing a design you handed it is your own opinion echoed back
5. **Where Codex contradicts you, its design judgment carries** — unless measurement of the
   deployed system disproves it (measurement outranks the model)
6. Present all perspectives to the user with your own assessment

## Step 4: Optional round 2 — adversarial
After Codex has answered blind, going back with "here is my design, try to break it" is
legitimate and valuable. Independent first, adversarial second.

## Step 5: User decides
**The user makes the final call.** Never proceed past planning without presenting the
advisory synthesis.

## When upstream vendor source informs the plan
Source is the MAP; the installed binary is the TERRITORY. Read vendor source at the ref
matching the INSTALLED version (for codex: `python3 scripts/codex_src.py` aligns
`~/Documents/codex-installed` to the installed CLI's release tag), state which ref was read,
and confirm anything load-bearing against the running binary — main-HEAD and release tags
are divergent, and either direction of drift has produced wrong conclusions here before.

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
Only skip if the user explicitly says "skip review", "skip codex", or "just do it".
