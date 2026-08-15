---
name: codex-planning
description: Multi-model planning workflow. Activates when planning features, architecture, or non-trivial changes. Ensures Codex Oracle and Antigravity are independently consulted before implementation begins.
---

# Multi-Model Planning

When planning any non-trivial feature, fix, or architectural change, you MUST gather
independent perspectives before finalizing.

## Step 1: Launch all advisors in parallel (SAME tool call batch) — and BLIND

- **Codex Oracle** — **PRIMARY, authoritative**: `architect_review` with the **requirement, the
  constraints, and the relevant file paths**
- **Antigravity** — **SECONDARY, corroborating**: `antigravity_brainstorm` or
  `antigravity_analyze_code` for alternatives
- **Your own agents**: Explore/Plan subagents for codebase investigation

> **No plan is finalized until CODEX has answered.** It runs at max effort and often takes many
> minutes (backgrounded, returning as a task notification — normal). Antigravity nearly always
> answers first; **first is not authoritative.** Never commit to a design on Antigravity alone —
> wait for Codex and do other work meanwhile.

**Ask them to solve the problem. Do not ask them to bless your solution.**

| ❌ Anchored | ✅ Blind |
|------------|---------|
| "Review my approach: a Redis queue with a worker pool" | "We need durable retries across restarts, ~5k jobs/day, existing stack is Python + Postgres. What should we use?" |
| "We picked Temporal because Celery couldn't do X — sanity-check" | "Here are the durability and observability requirements. What fits?" |
| "Is this schema correct?" | "Here are the entities, the access patterns, and the consistency requirements. Design the schema." |

A prompt that names your design gets you a critique of your design — never the better option
nobody put on the table. **Put your preferred design in `caller_hypothesis`** instead: it is
presented as an unverified claim to refute and returns an explicit
**CONFIRMED / REFUTED / UNPROVEN** verdict. `antigravity_brainstorm` goes further and generates
its ideas *before* seeing your hypothesis, so your pick cannot narrow the search space, then
critiques it against what it came up with independently.

If a result carries a **⚠️ ANCHORING WARNING** banner, that dispatch was contaminated — re-run it
blind before treating agreement as validation.

## Step 2: Expect prior art, not opinion
Both advisors run with live web search (Codex `web_search=live`; Antigravity `search_web`). A
design opinion with no reference to how this has been solved before is speculation:

- How have others solved this, and what did they report going wrong?
- What are the current versions/limits/pricing of anything proposed?
- Are there known failure reports or migration-away write-ups?

Both servers require a **Sources** section. Unsourced external claims were answered from memory —
re-ask.

## Step 3: Synthesize findings
Once all return — **and "all" means Codex too; if only Antigravity is back, you are not here yet**:
1. Summarize each model's key findings
2. Identify agreements and disagreements
3. If Codex or Antigravity raised CONCERNS/REJECT — critically analyze why. Do you agree?
4. **Weigh agreement by independence**: two blind advisors converging is strong evidence; two
   advisors you handed the same design are one opinion echoed twice
4b. **Where they contradict each other, Codex's design judgment carries** — unless measurement of
   the deployed system disproves it. An Antigravity-only idea or objection is still worth
   evaluating on its merits; the secondary is there to surface what the primary didn't see.
5. Present ALL perspectives to the user with your own assessment

## Step 4: Optional round 2 — adversarial
After both have answered blind, going back with "here is my design, try to break it" is
legitimate and valuable. Independent first, adversarial second.

## Step 5: User decides
**The user makes the final call.** Never proceed past planning without presenting the
multi-model synthesis.

## When upstream vendor source informs the plan
Source is the MAP; the installed binary is the TERRITORY. Read vendor source at the ref
matching the INSTALLED version (for codex: `python3 scripts/codex_src.py` aligns
`~/Documents/codex-installed` to the installed CLI's release tag), state which ref was read,
and confirm anything load-bearing against the running binary — main-HEAD and release tags
are divergent, and either direction of drift has produced wrong conclusions here before.

## Skip conditions
Only skip if the user explicitly says "skip review", "skip codex", or "just do it".
