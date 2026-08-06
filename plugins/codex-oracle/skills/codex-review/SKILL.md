---
name: codex-review
description: Multi-model code review workflow. Activates before committing or pushing code. Ensures Codex Oracle and Antigravity independently review all changes before they ship.
---

# Multi-Model Code Review

Before committing or pushing any code changes, you MUST get **independent** reviews.

Independent is the operative word. Handing an advisor your diagnosis and asking it to check
your work does not produce a review — it produces agreement. See Step 2.

## Step 1: Gather the diff
Run `git diff` to collect all staged and unstaged changes. If the diff is large, also read the
changed files so you can answer follow-ups without re-dispatching.

## Step 2: Dispatch both reviewers — IN PARALLEL and BLIND

Batch both MCP calls in the **same message**. Neither advisor may see the other's answer before
forming its own.

- **Codex Oracle**: `code_review` with the diff
- **Antigravity**: `antigravity_review_pr` (strictness: `strict`) with the same diff

**Send the diff. Do not send your conclusion.**

| ❌ Anchored dispatch | ✅ Blind dispatch |
|---------------------|------------------|
| `context: "I fixed the N+1 by adding selectinload — confirm that's right"` | `context: "Loads order lines for the invoice grid. Runs under RLS."` |
| `context: "The root cause was the missing await"` | (say nothing about cause — let them find it) |
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
Both advisors run with live web search (Codex `web_search=live`; Antigravity `search_web`). A
complete review checks the *current* upstream reality, not remembered API shapes:

- Are any APIs used here deprecated or changed upstream?
- Does any dependency touched here have a known CVE?
- Do version-specific claims come with a URL?

Both servers require a **Sources** section. An external claim with no source was answered from
memory — push back and re-ask rather than acting on it.

## Step 4: Process findings
1. Collect all findings from both models
2. Categorize by severity: CRITICAL > HIGH > MEDIUM > LOW
3. Any CRITICAL or HIGH finding MUST be addressed or explicitly acknowledged to the user
4. **If both models flag the same issue AND both were dispatched blind — it's almost certainly
   real, fix it.** If you anchored them, their agreement is one data point wearing two hats;
   re-dispatch blind before relying on it.
5. **Disagreement is always strong signal** — an advisor that contradicts you even after being
   nudged toward you has found something. Investigate it before dismissing it.
6. Verify file:line citations in the tree that is canon for this task — Codex reads the whole
   workspace and can cite a sibling checkout.

## Step 5: Optional round 2 — adversarial
Once both have answered blind, it is legitimate to go back with your hypothesis and ask them to
refute it, or to put one model's finding to the other. Order matters: independent first,
adversarial second.

## Step 6: Present to user
Summarize:
- What Codex found
- What Antigravity found
- Your own assessment
- Where they disagree — with both perspectives
- Which findings you've already fixed vs which need user input

## Skip conditions
Only skip if the user explicitly says "skip review" or "just push it".
