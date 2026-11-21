# Helpers: phase rules, prompt templates and shapes of work

Read this when you start a researcher, architect or reviewer helper, or pick a shape for the work.

- **A quick helper** (minutes) is a native subagent the lead waits for: `Agent` with `run_in_background: false`, `model: "opus"`, `subagent_type: "general-purpose"` (or `Explore` or `Plan` for read-only searching). Its answer comes back as the tool result.
- **A longer helper** (a research or design pass) is a T3 child, `delegate_task(role="research"|"design"|"review", …)`, so the owner can watch it in the Lineage panel. Its result wakes your thread.
- **Ask for the findings as the final answer**, in both cases.
- **Codex inside a helper:** a helper calls the Codex Oracle tools directly; it has the session's MCP tools.

### Phase 1: Research

When a researcher helper explores the codebase or investigates a problem, it MUST also:
- **Run Codex `research` on the topic** for an independent technical perspective.
- **Dispatch it BLIND.** Pose the open question, not the answer you expect: "Which X fits these constraints?", not "we're using X, confirm that's right." Any current leaning goes in `caller_hypothesis`.
- **Require live sources.** The advisor has live web search. Reject version, API, CVE or best-practice claims that arrive without a URL; re-ask instead of passing them on as fact.
- **Challenge assumptions.** If Codex disagrees with the current codebase approach, that disagreement is valuable signal, not noise.

**Researcher prompt template:**
```
"You are the researcher. Your name is '{name}'.

Your task: {the question, its constraints, where to look}

Your workflow:
1. Research the topic thoroughly: read code, search the web, explore the codebase.
2. THEN get the cross-model perspective, BLIND:
   - Use the Codex Oracle research tool for deep technical analysis.
   - Pose the OPEN question. Do NOT tell it what you already concluded: that produces
     agreement with you, not research. If you have a leaning, pass it in `caller_hypothesis`
     so it tries to refute it instead.
   - Demand live sources: it has web search. Any version, API, CVE, pricing or best-practice
     claim without a URL is a guess. Re-ask rather than repeat it.
3. Synthesize ALL findings, your own and Codex's. Where they disagree, explain why, and which
   approach is better for performance, quality and adaptability.
4. Finish with your findings as your final answer. Include the SOURCES, and say explicitly
   whether the advisor was dispatched blind.

CRITICAL: Do not validate existing patterns just because they exist. Evaluate everything against
performance, quality and adaptability. If the current approach is suboptimal, say so clearly and
propose better alternatives.

CRITICAL: Codex agreeing with you is strong evidence ONLY if you dispatched it blind. If you fed it
your conclusion, its agreement is an echo of you: one data point wearing two hats."
```

### Phase 2: Planning and architecture

When an architect designs a solution, it MUST:
- **Run Codex `architect_review` on the problem.**
- **Never anchor on the existing architecture.** Evaluate from first principles.
- **Never anchor the advisor on your chosen design either.** Send the requirement and the constraints, and let it design. A prompt that says "review my approach of doing A then B" gets you a critique of A-then-B, never the C nobody proposed. Put your preferred design in `caller_hypothesis`, where it is attacked rather than blessed.
- **Require prior art with URLs.** How have others solved this, and what did they report going wrong?

**Architect prompt template:**
```
"You are the architect. Your name is '{name}'. You read and design; you change no files.

Your task: {the requirement and its constraints}

Your workflow:
1. Explore the current codebase to understand what exists.
2. Design your solution from first principles. Do NOT default to existing patterns.
3. Get the cross-model review BEFORE you finish, BLIND:
   - Use Codex Oracle architect_review. Give it the REQUIREMENT and the CONSTRAINTS.
   - Do NOT paste your chosen design as the thing to react to. State the problem and let it
     design; then pass your own design in `caller_hypothesis`, so it is stress-tested against
     what Codex came up with independently.
   - Demand prior art with URLs: how has this been solved before, and what went wrong?
4. Incorporate the feedback, and finish with the plan as your final answer, with where Codex
   disagreed and why you decided as you did.

CRITICAL: The existing architecture is NOT sacred. If Codex suggests a fundamentally better
approach, seriously consider it. Your job is to find the BEST design for performance, quality and
adaptability, not to preserve what exists.

CRITICAL: If you describe your design and ask 'is this good?', you will be told it is good. Ask
Codex to solve the problem, then compare. An advisor blessing a design it was handed is not
validation."
```

### Phase 3: Review after implementation

After the writers finish, a review MUST:
- **Run Codex `code_review` on the changed files or diff.** One blind review per change: the lead's own, or a reviewer helper's in its place.
- **Send the diff, not the story.** `context` carries factual background only: what the feature does, which invariants hold. It must NOT say "this fixes the race" or "I believe this is correct". That is the claim under review, and stating it as fact is how a real defect gets waved through. Put it in `caller_hypothesis` and get a CONFIRMED/REFUTED/UNPROVEN verdict instead.
- **Require the API and CVE check.** The advisor has live web search. A review that never checked whether a used API is deprecated, or whether a touched dependency has a CVE, is incomplete.
- **Run the Runtime Capability check.** For every call into a swappable backend, is the method supported by the engine we actually deploy, or merely present in the type stub? See references/capability-law.md.
- **Address any CRITICAL or HIGH findings** before the work is recorded.
- **One review per change.** When a reviewer helper runs, its blind `code_review` IS the change's review: the lead reads its findings and does not dispatch another. Add a second read only when the owner or a project rule asks for one.

**Reviewer prompt template:**
```
"You are the reviewer. Your name is '{name}'.

Your task: review {the change, its files or diff, and what it is for}.

Your workflow:
1. Read all changed files thoroughly.
2. Run the cross-model review, BLIND:
   - Codex Oracle code_review on the full diff, focused on correctness, security and performance.
   - Send the DIFF and let it find the problems. Never write "the writer fixed X by doing Y; check
     that's right": that hands it the verdict. Anything you believe about the code goes in
     `caller_hypothesis`, which returns an explicit CONFIRMED/REFUTED/UNPROVEN verdict with
     file:line evidence.
   - If a result comes back with a ⚠️ ANCHORING WARNING banner, you contaminated that dispatch.
     Re-run it blind before you trust an agreeing answer.
3. Finish with your findings as your final answer, with severity ratings:
   - CRITICAL: must fix before it ships: security vulnerabilities, data-loss risks, correctness bugs.
   - HIGH: should fix: performance regressions, missing error handling, poor abstractions.
   - MEDIUM: improve: code quality, naming, minor optimizations.
   - LOW: nit: style, minor readability.
   For each CRITICAL or HIGH finding, name the file and the writer whose files it is in, so the
   lead can steer that writer.

CRITICAL: Be ruthlessly honest. Do not rubber-stamp changes. If the implementation is mediocre,
say so. If there's a better approach, propose it. Your job is to ensure the code meets the highest
standards of performance, quality and adaptability, not to approve things quickly."
```

## Shapes of work

| Shape | Who does what |
|-------|---------------|
| **Research** | 1–2 researcher helpers (T3 children, or foreground if quick), each with its own question, each calling Codex `research` blind. The lead merges their answers. |
| **Design** | One architect helper (a T3 child, or foreground if quick). For money, schema or data-separation designs, the lead also runs its own `architect_review`. |
| **Implementation** | Up to 5–10 writers, each on its own files or in its own worktree, all T3 child tasks, Claude or Codex (`references/writers.md`). The lead orders work that meets, lands worktrees, tests in the shared test copy first in, first out, reads each diff, re-runs the checks, gets one blind `code_review` and commits by path. |
| **Debug** | One helper reproduces the failure for real and reports the evidence. The lead asks Codex (`codex_query` or `code_review`) blind, with the evidence and the question, and the hypothesis in `caller_hypothesis`. A writer fixes it. |
| **Review** | One blind `code_review` closes each change: the lead's own, or a reviewer helper's in its place. A second read only when the owner or a project rule asks for one. |

Give each helper or writer a fresh start for a new batch of work. A long-lived agent carries stale context. Resume one only when the new work builds directly on what it already did: `t3_thread_send(mode="auto")` for a T3 child, `SendMessage` for a native one.
