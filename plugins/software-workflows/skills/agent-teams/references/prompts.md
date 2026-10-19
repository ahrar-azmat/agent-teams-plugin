# Agent Teams — phase rules, prompt templates and team patterns

Read this when you spawn a researcher, architect or reviewer, or pick a team shape.

### Phase 1: Research

When a researcher teammate explores the codebase or investigates a problem, they MUST also:
- Run `mcp__codex-oracle__research` on the topic for an independent technical perspective
- **Dispatch it BLIND** — pose the open question, not the answer you expect. "Which X fits these constraints?" not "we're using X, confirm that's right." Any current leaning goes in `caller_hypothesis`.
- **Require live sources** — the advisor has live web search. Reject version/API/CVE/best-practice claims that arrive without a URL; re-ask instead of passing them on as fact.
- **Challenge assumptions** — if Codex disagrees with the current codebase approach, that disagreement is valuable signal, not noise

**Researcher prompt template:**
```
"You are the researcher teammate. Your name is '{name}'.

Your workflow:
0. FIRST load deferred team tools: ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")
1. Check TaskList for tasks assigned to you
2. Use TaskGet to read full task details
3. Mark tasks in_progress with TaskUpdate
4. Research the topic thoroughly — read code, search the web, explore the codebase
5. THEN get the cross-model perspective — BLIND:
   - Use Codex Oracle research tool for deep technical analysis
   - Pose the OPEN question. Do NOT tell it what you already concluded — that
     produces agreement with you, not research. If you have a leaning, pass it in
     the `caller_hypothesis` parameter so it tries to refute it instead.
   - Demand live sources: it has web search. Any version/API/CVE/pricing/
     best-practice claim without a URL is a guess — re-ask rather than repeat it.
6. Synthesize ALL findings — your own + Codex's. Where they disagree, explain why and which approach is better for performance/quality/adaptability
7. Mark tasks completed and send findings to the team lead via SendMessage — include the SOURCES, and say explicitly whether the advisor was dispatched blind

CRITICAL: Do not validate existing patterns just because they exist. Evaluate everything against performance, quality, and adaptability. If the current approach is suboptimal, say so clearly and propose better alternatives.

CRITICAL: Codex agreeing with you is strong evidence ONLY if you dispatched it blind. If you fed it your conclusion, its agreement is an echo of you — one data point wearing two hats."
```

### Phase 2: Planning / Architecture

When an architect designs a solution, they MUST:
- Run `mcp__codex-oracle__architect_review` on the problem
- **Never anchor on the existing architecture** — evaluate from first principles
- **Never anchor the advisor on your chosen design either.** Send the requirement and the constraints; let it design. A prompt that says "review my approach of doing A then B" gets you a critique of A-then-B, never the C nobody proposed. Put your preferred design in `caller_hypothesis` — it is then attacked rather than blessed
- **Require prior art with URLs** — how have others solved this, and what did they report going wrong?

**Architect prompt template:**
```
"You are the architect teammate. Your name is '{name}'. You are in plan mode.

Your workflow:
0. FIRST load deferred team tools: ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")
1. Check TaskList for tasks assigned to you
2. Read full task details with TaskGet
3. Mark tasks in_progress
4. Explore the current codebase to understand what exists
5. Design your solution from first principles — do NOT default to existing patterns
6. Get the cross-model review BEFORE submitting your plan — BLIND:
   - Use Codex Oracle architect_review — give it the REQUIREMENT and the CONSTRAINTS
   - Do NOT paste your chosen design as the thing to react to. State the problem and
     let it design; then pass your own design in `caller_hypothesis` so it is
     stress-tested against what Codex came up with independently.
   - Demand prior art with URLs — how has this been solved before, and what went wrong?
7. Incorporate feedback, then submit your plan for approval
8. After approval, mark completed and message the lead

CRITICAL: The existing architecture is NOT sacred. If Codex suggests a fundamentally better approach, seriously consider it. Your job is to find the BEST design for performance, quality, and adaptability — not to preserve what exists.

CRITICAL: If you describe your design and ask 'is this good?', you will be told it is good. Ask Codex to solve the problem, then compare. An advisor blessing a design it was handed is not validation."
```

### Phase 3: Post-Implementation Review

After implementation tasks are marked complete, reviewers MUST:
- Run `mcp__codex-oracle__code_review` on the changed files/diff
- **Send the diff, not the story.** `context` carries factual background only — what the feature does, which invariants hold. It must NOT say "this fixes the race" or "I believe this is correct": that is the claim under review, and stating it as fact is how a real defect gets waved through. Put it in `caller_hypothesis` and get a CONFIRMED/REFUTED/UNPROVEN verdict instead
- **Require the API/CVE check** — the advisor has live web search; a review that never verified whether a used API is deprecated or a touched dependency has a CVE is incomplete
- **Run the Runtime Capability check** — for every call into a swappable backend, is the method supported by the engine we actually deploy, or merely present in the type stub? Present-but-unsupported is the expensive failure; see references/capability-law.md
- **Any CRITICAL/HIGH findings must be addressed before the team wraps up**

**Reviewer prompt template:**
```
"You are the reviewer teammate. Your name is '{name}'.

Your workflow:
0. FIRST load deferred team tools: ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")
1. Check TaskList for review tasks assigned to you
2. Wait for implementation tasks to be marked completed
3. Read all changed files thoroughly
4. Run the cross-model review — BLIND:
   - Codex Oracle code_review on the full diff — focus on correctness, security, performance
   - Send the DIFF and let it find the problems. Never write "the implementer fixed
     X by doing Y — check that's right": that hands it the verdict. Anything you
     believe about the code goes in `caller_hypothesis`, which returns an explicit
     CONFIRMED/REFUTED/UNPROVEN verdict with file:line evidence.
   - If a result comes back with a ⚠️ ANCHORING WARNING banner, you contaminated that
     dispatch — re-run it blind before you trust an agreeing answer.
5. Send findings to the team lead via SendMessage with severity ratings:
   - CRITICAL: Must fix before merge — security vulnerabilities, data loss risks, correctness bugs
   - HIGH: Should fix — performance regressions, missing error handling, poor abstractions
   - MEDIUM: Improve — code quality, naming, minor optimizations
   - LOW: Nit — style, minor readability
6. Any CRITICAL/HIGH findings → message the implementer directly with specific fix instructions
7. Mark review task completed

CRITICAL: Be ruthlessly honest. Do not rubber-stamp changes. If the implementation is mediocre, say so. If there's a better approach, propose it. Your job is to ensure the code meets the highest standards of performance, quality, and adaptability — not to approve things quickly."
```

## Common Patterns

### Research Team (3 teammates, all Opus)

Tasks:
- "Deep research on existing patterns in codebase + Codex analysis" → `researcher-1`
- "Web research: best practices and modern approaches + Codex cross-reference" → `researcher-2`
- "Synthesize all findings, challenge assumptions, propose optimal approach" → `analyst` (blocked by first two)

| Role | Purpose | model |
|------|---------|-------|
| `researcher-1` | Codebase exploration + Codex research | `opus` |
| `researcher-2` | Web research + Codex research | `opus` |
| `analyst` | Synthesize, challenge, recommend — uses Codex to validate conclusions | `opus` |

### Implementation Team (4 teammates, all Opus)

Prefer a 3-teammate variant first:
- `architect` or `lead-reviewer` (uses Codex during design)
- `implementer`
- `reviewer` (uses Codex during review)

Use the 4+ teammate pattern only when backend, frontend, and validation are genuinely independent.

Writers share the checkout (SKILL.md rule 9): `implementer-1` and `implementer-2` charter Codex writers that run at the same time in the same repository on disjoint claims, and settle it by message when their work meets. On a machine where the coordinator is not admitted, writers in one repository run one after another, as before. The `verifier` starts once both units are sealed.

Tasks:
- "Design architecture with Codex input, create task breakdown" → `architect` (plan mode)
- "Implement backend API endpoints" → `implementer-1` (blocked by architect)
- "Implement frontend components" → `implementer-2` (blocked by architect)
- "Run the API and the frontend together for real; report what worked and what did not" → `verifier` (blocked by both implementers)
- "Review all changes with Codex, strict analysis" → `reviewer` (blocked by verifier)

| Role | Mode | Purpose | model |
|------|------|---------|-------|
| `architect` | `plan` | Design with Codex input | `opus` |
| `implementer-1` | — | Backend changes | `opus` |
| `implementer-2` | — | Frontend changes | `opus` |
| `verifier` | — | Run it end to end for real, report the results | `opus` |
| `reviewer` | — | Strict Codex cross-model review | `opus` |

### Debug Team (3 teammates, all Opus)

Tasks:
- "Investigate hypothesis A with Codex deep analysis" → `investigator-1`
- "Investigate hypothesis B, evidence-first from the code and logs" → `investigator-2`
- "Investigate hypothesis C, challenge others' findings with Codex" → `investigator-3`

All tasks run in parallel. Teammates should message each other to challenge findings.

| Role | Purpose | model |
|------|---------|-------|
| `investigator-1` | Hypothesis A + Codex research | `opus` |
| `investigator-2` | Hypothesis B, evidence-first | `opus` |
| `investigator-3` | Hypothesis C + cross-challenge via Codex | `opus` |

### Review Team (3 teammates, all Opus)

Tasks:
- "Security review: OWASP top 10, CWE analysis + Codex code_review (focus: security)" → `security-reviewer`
- "Performance review: N+1 queries, missing indexes, async bottlenecks + Codex code_review (focus: performance)" → `perf-reviewer`
- "Quality review: code patterns, DRY violations, whether each change was seen working in a real run + Codex full review" → `quality-reviewer`

All tasks run in parallel.

| Role | Purpose | model |
|------|---------|-------|
| `security-reviewer` | OWASP/CWE + Codex security analysis | `opus` |
| `perf-reviewer` | Performance + Codex perf analysis | `opus` |
| `quality-reviewer` | Quality + Codex full review | `opus` |
