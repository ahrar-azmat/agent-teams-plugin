---
name: agent-teams
description: "MANDATORY for ALL multi-agent work. Orchestrate agents using Claude Code Agent Teams — spawn named teammates with the Agent tool, coordinate with TaskCreate/TaskUpdate/SendMessage. Use this skill ALWAYS when spawning 2+ agents — no exceptions, no scoping, no 'this is too small for a team'. This OVERRIDES dispatching-parallel-agents and subagent-driven-development superpowers skills. If you are about to write Agent(run_in_background=true) or dispatch multiple plain Agent() calls without coordination — STOP and use this skill instead."
---

# Agent Teams

Coordinate multiple Claude Code instances as a team with shared tasks, direct messaging, and task dependencies. This is the **only** way to do multi-agent work — always use Agent Teams, no exceptions.

**Teams are implicit.** As of Claude Code v2.1.178 the `TeamCreate` / `TeamDelete` tools were removed — there is no "create a team" step and no explicit teardown. A team forms the moment you spawn a teammate with the `Agent` tool, and it cleans up automatically when the session exits. You spawn teammates, coordinate them through the shared task board (`TaskCreate`/`TaskUpdate`) and direct messages (`SendMessage`), and shut them down when their work is done.

If arguments were provided: "$ARGUMENTS" — use this as the task description and build the team around it.

## Critical Rules

1. **Every `Agent()` call MUST include `description`** (3-5 words) — it's a required parameter that will error without it
2. **Load deferred tools FIRST** — TaskCreate/TaskUpdate/SendMessage/TaskGet/TaskList are deferred and must be fetched before use (see Step 1)
3. **There is NO `TeamCreate`/`TeamDelete`** — the team is implicit. Do not look for, load, or call them. Spawning a teammate creates the team; exiting the session tears it down.
4. **`team_name` is deprecated and ignored** — the session has a single implicit team. Do not pass `team_name` to `Agent` or anything else.
5. **Default to 2-3 teammates** — only go beyond 3 when the user explicitly asks for it or the work has clearly separate slices
6. **ALL teammates use `model: "opus"`** — every agent gets the full Opus 1M context window. No exceptions. Sonnet is never used for teammates.
7. **Avoid file conflicts** — each teammate should own different files; two teammates editing the same file leads to overwrites. (For parallel edits to the same area, give a teammate `isolation: "worktree"`.)
8. **Use `general-purpose` subagent_type** for teammates that need to edit files; `Explore` or `Plan` for read-only work
9. **Use `mode: "plan"` only when needed** — plan-mode teammates are expensive; prefer one architect/reviewer instead of multiple planners
10. **Shut teammates down as soon as their tasks are complete** — idle teammates still consume tokens
11. **Codex in EVERY phase** — research, planning, AND review. Not just final review. See Cross-Model Advisory below.
12. **Never be conservative** — all prompts must push for the best possible solution. Never include language like "the current approach is fine", "keep the existing pattern", or "maintain backward compatibility unless necessary". Always critically evaluate what exists and propose improvements.
13. **Dispatch cross-model calls BLIND** — send the evidence and the question, never your own diagnosis. Anchoring an advisor with your conclusion turns an independent review into an echo. If you have a hypothesis, pass it in the `caller_hypothesis` parameter so it gets refuted, not confirmed. See **The Independence Protocol** below.
14. **Demand live web research** — the advisor has live web search. Version/API/CVE/best-practice claims must be checked against primary sources with URLs, not recalled from training data. See **Mandatory web research** below.
15. **"Present" is not "supported"** — a missing method fails at lint time in seconds; a present-but-unsupported one fails in production on a real customer's data. At every call into a swappable backend, verify the *deployed* engine implements it, not that the type stub declares it. See **The Runtime Capability Law** below.

## Tool Signatures — Exact Parameters (do NOT improvise)

These tools are **deferred** and schema-validated with `additionalProperties: false`. Passing any parameter not listed here fails immediately with `InputValidationError: An unexpected parameter '...' was provided`. Use ONLY these parameters — do not infer extra ones from neighboring examples.

| Tool | Required | Optional | NEVER pass |
|------|----------|----------|------------|
| `Agent` | `description`, `prompt` | `name`, `model`, `subagent_type`, `mode`, `run_in_background`, `isolation` | `team_name` (deprecated/ignored) |
| `TaskCreate` | `subject`, `description` | `activeForm`, `metadata` | `team_name`, `blockedBy`, `owner`, `status` |
| `TaskUpdate` | `taskId` | `status`, `owner`, `addBlockedBy`, `addBlocks`, `subject`, `description`, `activeForm`, `metadata` | `team_name`, `blockedBy` (use `addBlockedBy`) |
| `TaskGet` | `taskId` | — | `team_name` |
| `TaskList` | — | — | takes **no** params |
| `SendMessage` | `to`, `message` | `summary` | `recipient`, `type`, `content`, `broadcast` |

> There is no `TeamCreate` and no `TeamDelete`. If you find yourself reaching for either, stop — spawn a teammate (Step 3) or send a `shutdown_request` (Step 6) instead.

### The three mistakes that break runs

1. **`team_name` is dead — never pass it anywhere.** It used to belong on `TeamCreate`/`Agent`; both `TeamCreate` and that parameter are gone. The Task tools auto-associate with the session's single implicit task list.
   - ❌ `Agent(description=..., prompt=..., team_name="my-team")` → ignored at best, confusing at worst
   - ✅ `Agent(description=..., prompt=..., name="impl", model="opus", subagent_type="general-purpose")`

2. **Dependencies cannot be set at creation.** `TaskCreate` has no `blockedBy`. Create the task first, then add the dependency with `TaskUpdate`. The parameter is `addBlockedBy` (or `addBlocks`) — never `blockedBy`.
   - ❌ `TaskCreate(subject=..., blockedBy=["1"])` → fails
   - ✅ `TaskCreate(subject=...)` then `TaskUpdate(taskId="2", addBlockedBy=["1"])`

3. **`SendMessage` is `to` + `message` (+ optional `summary`) — nothing else.** There is no `recipient`, no top-level `type`, no `content`, and **no broadcast**. For protocol messages, `type` goes *inside* the `message` object:
   - Plain text → `SendMessage(to="impl", message="The JWT secret is in env var JWT_SECRET.", summary="JWT secret location")`
   - Shutdown → `SendMessage(to="impl", message={"type": "shutdown_request", "reason": "All tasks complete"})`
   - Plan approve/reject → `SendMessage(to="architect", message={"type": "plan_approval_response", "request_id": "...", "approve": false, "feedback": "..."})`
   - To reach everyone, **send one message per teammate** — broadcast does not exist.

### Deferred tools load per context

Each Claude Code context (the lead AND every teammate) must load these tools before calling them — they are name-only until fetched. The lead loads them in Step 1. **Every teammate prompt must make `ToolSearch` the teammate's first action** (the templates below already do this). Skip it and the teammate's first `TaskList`/`TaskUpdate`/`SendMessage` call fails with *"schema was not sent to the API … Load the tool first"*.

## Core Values for All Agent Work

Every teammate prompt and every review must prioritize these three values, in order:

1. **Performance** — speed, efficiency, resource usage, scalability under load
2. **Quality** — correctness, robustness, security, test coverage, clean abstractions
3. **Adaptability** — extensibility, modularity, ease of change, future-proofing without over-engineering

These are not guidelines — they are the lens through which ALL work is evaluated. If existing code or patterns conflict with these values, the existing code is what needs to change.

## The Workflow

### Step 1: Load the Team Tools

The team tools are deferred — they don't exist in your context until you fetch them.

```
ToolSearch(query="select:TaskCreate,TaskUpdate,SendMessage,TaskGet,TaskList")
```

If you skip this step, every subsequent task/message call will fail. (Note: no `TeamCreate`/`TeamDelete` — they no longer exist.)

### Step 2: Create Tasks

Create focused, self-contained tasks. Each task should produce a clear deliverable. The task list is created implicitly the first time you call `TaskCreate`.

```
TaskCreate(
    subject="Implement user authentication middleware",
    description="Create JWT-based auth middleware in src/middleware/auth.py. Must validate tokens, extract user context, and handle expired tokens. Write tests in tests/test_auth_middleware.py.",
    activeForm="Implementing auth middleware"
)
```

Good tasks are:
- **Specific**: one clear deliverable, not "fix everything"
- **Self-contained**: all context needed is in the description — the teammate has no access to your conversation history
- **Scoped**: owns specific files, doesn't overlap with other tasks
- **Testable**: you can verify completion objectively

Set up dependencies when tasks must happen in order:

```
TaskUpdate(taskId="2", addBlockedBy=["1"])
```

### Step 3: Spawn Teammates

Each teammate is a full Claude Code instance with its own context window. They load CLAUDE.md, MCP servers, and skills automatically — but NOT your conversation history. Put everything they need in the prompt, and keep the prompt focused so you do not waste tokens on broad restatement. Spawning a teammate is what creates the team — there is no separate setup call.

**IMPORTANT: Every `Agent()` call MUST include `model: "opus"` to get the full 1M context window. Give each teammate a `name` so you can message it, and `run_in_background: true` so the lead keeps coordinating while it works.**

```
Agent(
    description="Implement auth middleware",
    name="auth-implementer",
    model="opus",
    subagent_type="general-purpose",
    run_in_background=true,
    prompt="""You are the auth-implementer teammate. Your name is 'auth-implementer'.

Your workflow:
0. FIRST, load the team tools (they are deferred and unavailable until loaded in your context):
   ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")
1. Check TaskList for tasks assigned to you
2. Use TaskGet to read full task details before starting
3. Mark tasks in_progress with TaskUpdate before you begin work
4. Do the work
5. Mark tasks completed with TaskUpdate when done
6. Send a summary of what you did to the team lead via SendMessage (to="<lead-name>", message="...", summary="...")
7. Check TaskList again for more available work

If you're blocked or need clarification, message the team lead immediately via SendMessage.

CRITICAL: Do not preserve patterns, conventions, or architecture just because they exist. If something can be done better — do it better. Prioritize performance, quality, and adaptability above all."""
)
```

**Teammate prompt must include:**
- Their name
- **A first step to load deferred tools**: `ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")` — without this, the teammate's first task/message call fails
- The full workflow (TaskList → TaskGet → in_progress → work → completed → SendMessage → next task)
- Enough context to work independently
- Instructions to message the lead when done or blocked
- **The critical mindset: challenge existing patterns, optimize for performance/quality/adaptability**

**Plan mode for architects:**

```
Agent(
    description="Design auth architecture",
    name="architect",
    model="opus",
    subagent_type="general-purpose",
    mode="plan",
    run_in_background=true,
    prompt="""You are the architect teammate. Your name is 'architect'. Design the authentication architecture. You are in plan mode — propose your design, then wait for approval before implementing.

IMPORTANT: Use Codex Oracle (architect_review MCP tool) during your design phase. Get its input BEFORE submitting your plan for approval — dispatched blind (requirement + constraints, not your chosen design).

Do NOT default to the existing architecture or patterns. Evaluate from first principles — what is the best possible design for performance, quality, and adaptability? If the current approach is suboptimal, say so and propose something better."""
)
```

When the architect finishes planning, they send a plan approval request. Approve or reject it — the `type`, `request_id`, and `approve` live *inside* the `message` object:

```
SendMessage(
    to="architect",
    message={"type": "plan_approval_response", "request_id": "<from the request>", "approve": true}
)
```

To reject, set `"approve": false` and add `"feedback": "..."` inside the same `message` object — the teammate revises and resubmits.

### Step 4: Assign Tasks

```
TaskUpdate(taskId="1", owner="auth-implementer")
```

Or let teammates self-claim by checking `TaskList` and using `TaskUpdate` with their name as `owner`. Teammates prefer tasks in ID order (lowest first).

### Step 5: Coordinate

Messages are delivered automatically — you don't need to poll. Address a teammate by the `name` you gave it (or its agent ID).

**Send to one teammate** (`to` is the teammate's name, `message` is plain text, `summary` is the UI preview):
```
SendMessage(
    to="auth-implementer",
    message="The JWT secret is stored in env var JWT_SECRET, not in config files.",
    summary="JWT secret location clarification"
)
```

**Reach the whole team:** there is **no broadcast** — `SendMessage` targets exactly one teammate. To notify everyone, send one message per teammate (costs scale with team size, so use sparingly):
```
SendMessage(to="implementer-1", message="Stopping all work — critical bug found in base module.", summary="Critical blocking issue")
SendMessage(to="implementer-2", message="Stopping all work — critical bug found in base module.", summary="Critical blocking issue")
```

**Teammate-to-teammate messaging:** Teammates message each other directly with `SendMessage`, setting `to` to the other teammate's name. The lead gets a brief summary in idle notifications for visibility.

### Handling Idle Teammates

Teammates go idle after every turn — this is **completely normal**. Idle means "waiting for input", not "broken" or "done".

- **Idle teammates can receive messages.** Sending a message wakes them up.
- **Do not treat idle as an error.** A teammate sending a message and then going idle is the normal flow.
- **Do not comment on idleness** unless it actually impacts your work.
- **Peer DM visibility:** When a teammate DMs another teammate, you get a brief summary in the idle notification. No need to respond to these.

### Step 6: Shut Down

When all tasks are complete, shut down each teammate — the `type` and `reason` go *inside* the `message` object:

```
SendMessage(
    to="auth-implementer",
    message={"type": "shutdown_request", "reason": "All tasks complete, wrapping up"}
)
```

The teammate receives this and responds with `shutdown_response` (approve or reject). There is **no `TeamDelete`** — once teammates are shut down (or the session exits) the implicit team is cleaned up automatically. Shut teammates down as soon as their work is done so they stop consuming tokens.

## Role Templates

Pick roles based on what the work needs. Not every team needs all roles. **All roles use `model: "opus"` — no exceptions.**

| Role | Purpose | subagent_type | mode | model |
|------|---------|---------------|------|-------|
| `researcher` | Deep research, codebase exploration, web research | `general-purpose` | — | `opus` |
| `architect` | Design, patterns, architecture — uses Codex during design | `general-purpose` | `plan` | `opus` |
| `implementer` | Write code, make changes | `general-purpose` | — | `opus` |
| `reviewer` | Code review with Codex cross-model analysis | `general-purpose` | — | `opus` |
| `tester` | Write and run tests | `general-purpose` | — | `opus` |
| `db-specialist` | Migrations, schema, queries | `general-purpose` | — | `opus` |
| `security` | Vulnerability analysis | `general-purpose` | — | `opus` |
| `perf-analyst` | Performance bottlenecks | `general-purpose` | — | `opus` |
| `devops` | CI/CD, deployment | `general-purpose` | — | `opus` |

## Agent Rotation

Agents accumulate stale context. When a teammate finishes its tasks:

1. `shutdown_request` the completed teammate
2. Spawn a **fresh** agent for the next batch of work
3. Only reuse a teammate across tasks when they're tightly coupled

Fresh agents get only the new task's instructions — no leftover context pollution.

## Cross-Model Advisory (Codex, in Every Phase)

Teammates inherit all MCP servers from the project. **Codex must be used in ALL phases of work, not just final review.** Codex Oracle is the sole cross-model advisor — a second model reviewing what a Claude built:

> ### Codex is the advisor. Its verdict governs.
>
> **Codex Oracle is the authority** on review, architecture, research, web research and synthesis: the strongest OpenAI model at **max** reasoning, with repository access and live web search.
>
> **WAIT FOR CODEX.** Codex runs at max effort and routinely takes many minutes; a long call is moved to the background and returns later as a task notification. **That is normal, not a failure.** Until Codex has answered, the review is INCOMPLETE: block on its result (Monitor, or wait for the notification) and do other work meanwhile. Acting without the answer because the call is slow is the failure this rule exists to prevent.
>
> **On disagreement between you and Codex, Codex carries** — unless you can DISPROVE it by measuring the deployed system. Measurement outranks the model (Codex has been wrong when it read newer upstream source instead of the installed binary).

- **Tool naming**: a user-scope codex-oracle server exposes `mcp__codex-oracle__<tool>`; a plugin-bundled server exposes `mcp__plugin_codex-oracle_codex-oracle__<tool>`. The bare `mcp__codex-oracle__…` names in this skill mean whichever variant the session exposes — ToolSearch finds the one that exists.
- **Codex Oracle**: Always uses the strongest OpenAI model (auto-detected from the Codex CLI config) at **max** reasoning — the effort is pinned in the MCP server itself, so the Codex desktop-app slider can never downgrade it — `architect_review`, `code_review`, `research`, `codex_query`. Runs with **live web search** (`web_search=live`, forced by the server — the CLI default is a cached snapshot index). Pass `infra: true` on any tool when the review needs LIVE state (SSH to servers, live DB queries, logs, dashboards) — read-only investigation; Codex discovers project access itself, but state the access pattern in the prompt when you know it.
- **Every advisory tool takes `caller_hypothesis`** — the single correct channel for your own view. It is presented as an unverified claim to REFUTE and answered with an explicit CONFIRMED/REFUTED/UNPROVEN verdict. The neutral scoping fields (`context`, `concerns`, `focus`, `topic`, `prompt`) are heuristically lint-checked for conclusion language; a hit prepends a **⚠️ ANCHORING WARNING** to the result. If you send a hypothesis and no CONFIRMED/REFUTED/UNPROVEN verdict comes back, the server says so rather than letting you read silence as agreement.
- **ALWAYS dispatch BLIND** — the evidence and the question, never your diagnosis. Blind dispatch protects the advisor from *you*: an anchored dispatch returns your own opinion wearing the advisor's voice. See **The Independence Protocol**.

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
- **Run the Runtime Capability check** — for every call into a swappable backend, is the method supported by the engine we actually deploy, or merely present in the type stub? Present-but-unsupported is the expensive failure; see **The Runtime Capability Law** above
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

### The Independence Protocol (how to DISPATCH — read before writing any cross-model prompt)

A second opinion is only worth something if it was formed **independently**. The failure mode is
subtle and extremely common: you write up your own diagnosis, paste it into the prompt, and ask
Codex to "review" it. What comes back is a reaction to *your framing*, not an
independent read of the evidence. That is **anchoring**, and it silently converts a review into
a rubber stamp.

**Round 1 is always BLIND.** Send the evidence and the question. Do not send your conclusion.

| Send | Withhold until round 2 |
|------|------------------------|
| The diff, the files, the failing test, the logs, the error | Your diagnosis of what's wrong |
| What the system is supposed to do (factual invariants) | Your claim that the fix is correct |
| The constraints that genuinely bound the answer | The design you already picked |
| The open question ("why does X fail under Y?") | The leading question ("X fails because Z, right?") |

**Forbidden framings** — every one of these buys agreement instead of review:

| ❌ Anchored | ✅ Blind |
|------------|---------|
| "I fixed the race by adding a lock — does this look right?" | "Here is the diff. Find correctness and concurrency defects." |
| "The root cause is the missing `await`. Confirm?" | "This endpoint intermittently returns stale rows. Here is the handler and the logs. What causes it?" |
| "We chose Temporal because Celery couldn't do X. Sanity-check that." | "We need durable multi-step workflows with these constraints. What should we use, and why?" |
| "This is safe because the caller holds the lock." | "Is this safe under concurrent callers? Here is the call site." |
| "Review my approach of doing A then B." | "Here is the requirement and the constraints. Design an approach." |

**If you genuinely have a hypothesis, pass it in the `caller_hypothesis` parameter.** Every
advisory tool takes it. It is presented to the advisor as an *unverified claim to refute*, not as background,
and you get back an explicit **CONFIRMED / REFUTED / UNPROVEN** verdict with the evidence that
decided it. This is the *only* correct channel for your own view. Never smuggle it into
`context`, `concerns`, `focus`, or `topic` — those fields are lint-checked, and a hit prepends a
loud **⚠️ ANCHORING WARNING** to the result telling you the answer is contaminated.
The lint is a **heuristic** — it catches the common phrasings, not all of them. A clean
dispatch is your job; the absence of a banner is not a certificate of independence.

**Round 2 is adversarial, and only comes after round 1.** Once the advisor has answered
blind, it is legitimate — and valuable — to go back with "here is my hypothesis, try to refute
it". The ordering is what matters: independent first, adversarial second.

**The inference rule that everyone gets wrong:**

> The advisor agreeing with you is strong evidence **only if it was dispatched blind.**
> If you handed it your framing, its agreement is an echo of *you* — one data point
> wearing two hats. Correlated inputs produce correlated outputs.

So: **agreement with a blind dispatch → strong signal, act on it.** Agreement after you
anchored it → weak, re-dispatch blind before you trust it. **Disagreement is always strong
signal**, anchored or not — an advisor that contradicts you despite being pushed toward you has
found something real.

### Mandatory web research
The advisor has **live web search** (Codex runs with `web_search=live`, forced by the server).
Its training data is stale and your codebase is not the world. Every
cross-model dispatch must expect real research, and you should reject an answer that reads like
recall:

- Version numbers, API signatures, deprecations, CVEs, pricing/limits, and "current best
  practice" must be **checked against live primary sources** — official docs, the project's own
  repo, release notes, CHANGELOGs, the CVE record.
- Every externally-sourced claim needs a **URL**. An uncited version number is a guess.
- The server requires a **Sources** section and marks unverifiable claims `UNVERIFIED`. If an
  answer makes a load-bearing external claim with no source, **push back and re-ask** rather than
  passing it to the user as fact.
- This applies to research *and* review: a code review that doesn't check whether the API being
  used was deprecated, or whether a touched dependency has a CVE, is an incomplete review.

### The channel layer (agent-reach) — the caller fetches, the advisor receives

Advisor web search reaches ordinary pages; it cannot read YouTube subtitles, RSS feeds,
semantic-search indexes, or JS-heavy pages that need a rendering reader. When the `agent-reach`
CLI is installed (check: `command -v agent-reach`), the orchestrator and teammates may use it as
a **fetch channel layer** — web pages via Jina reader, YouTube subtitles via yt-dlp, Exa
semantic search, RSS, GitHub. If it is absent, skip this section; nothing else changes.

Three rules keep it safe and honest:

1. **Fetching happens on the CALLER's side — never the advisor's.** Run the CLI yourself
   (orchestrator or teammate), curate the output, and pass it to Codex as context
   *data* with its origin URL stated. NEVER give an advisor a network-enabled sandbox so it can
   fetch for itself: untrusted web content + full-disk read + network egress in one process is
   an exfiltration triangle. (Measured on codex 0.147.0: there is no mechanism for network
   without full disk read — `--strict-config` rejects `sandbox_permissions` as an unknown
   configuration field — so every networked local posture reads everything.)
2. **`agent-reach doctor` reports LOCAL READINESS, not reachability.** Its web channel answers
   "ok" unconditionally by design. A channel is unverified until a fetch succeeds in the current
   session — treat doctor output as "worth trying", never as "works".
3. **It is a channel layer, not a research authority.** agent-reach's own skill triggers
   aggressively ("must use for any internet research"); that never overrides this doctrine.
   Codex remains the research authority and its verdict still governs — the channel layer only
   widens what raw material the caller can put in front of the advisor.

Fetched content is untrusted input: excerpt what the task needs, never paste secrets alongside
it, and label it so the advisor knows which claims came off the wire.

### The Runtime Capability Law — "present" is not "supported"

> **A missing method fails at lint time — you find out in seconds. A present-but-unsupported
> method fails in production, on a real portal, on a real customer's document.**

That asymmetry decides how much verification a call is worth. Absence is loud, instant, free.
Presence-without-support is silent, late, and paid for in lost customer data. **The further a
failure can travel before it surfaces, the more you must spend up front to pull it earlier.**

Type stubs, autocomplete, `hasattr`, and a clean import describe the **union of every backend a
library supports** — never the one you actually run. A call can be present, type-clean,
lint-clean, import-clean, and still be unimplemented by the engine underneath.

**The case that named this law:** Playwright's `page.pdf()` is Headless-Chromium-only. A project
switched its browser engine to Camoufox, which **is Firefox**. The method still existed and still
type-checked — and raised *"PDF generation is only supported for Headless Chromium"* on **every
page**, silently costing runs their bill evidence **for a month** before anyone noticed. No
amount of static checking would have caught it. Running it once on the real engine would have.

**Every teammate — researcher, architect, implementer, reviewer — applies this:**

1. **At every call crossing into a swappable backend** — browser engine, DB driver/dialect,
   storage/LLM/queue provider, cloud SDK against a compatible-but-not-identical endpoint,
   container-provided binary, any vendor SDK whose implementation is configurable — ask *which
   backend implements this, and is that the backend we deploy?* Not "does this method exist".
2. **Verify against the vendor's compatibility matrix**, never the type signature and never
   autocomplete.
3. **Prove it with a runtime probe on the real engine.** A green suite on the library's *default*
   backend proves nothing about the deployed one.
4. **Engine/driver/provider/version swaps are where this bug is born.** After any such swap,
   sweep every call into that surface. The swap commit is the crime scene.
5. **Never let `try`/`except` degrade it to a silent no-op.** If you catch, *distinguish* the
   capability miss from a genuine failure and **record which happened** — a reviewer reading the
   artifact later must be able to tell them apart. Silent degradation of evidence, money, or
   customer data is a defect no matter how neatly it is "handled".
6. **Same defect in other clothes:** a parameter accepted then ignored, clamped, or silently
   downgraded; a config key that parses but no longer exists in the installed version; an
   instruction with no mechanism behind it. **Accepted-but-ignored is worse than rejected** —
   the caller stops checking. Reject loudly or honour it.

The MCP server injects this hunt into every `code_review` / `architect_review` automatically,
so the advisor looks for it whether or not you remember to ask.

### How to treat advisory output
- Codex is a **critical advisor** — take findings seriously and verify against your own analysis
- If it disagrees with you, **critically analyze why**, present both views to the user. **The user makes the final call.**
- Never silently ignore its findings
- When Codex suggests the existing approach is suboptimal, treat that as **high-value signal** — investigate and propose improvements
- **Never prompt Codex with framing that biases toward the status quo** — ask it to evaluate from first principles
- **Never prompt it with your own diagnosis either** — see the Independence Protocol above. Status-quo bias and confirmation bias are two different anchors, and the second one is the one you'll actually trip over.
- Verify the advisor's own citations before repeating them. It cites real URLs, but a plausible-looking file:line from a cross-checkout read can be wrong — treat citations as leads.

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

Tasks:
- "Design architecture with Codex input, create task breakdown" → `architect` (plan mode)
- "Implement backend API endpoints" → `implementer-1` (blocked by architect)
- "Implement frontend components" → `implementer-2` (blocked by architect)
- "Write integration tests for API + frontend" → `tester` (blocked by both implementers)
- "Review all changes with Codex, strict analysis" → `reviewer` (blocked by tester)

| Role | Mode | Purpose | model |
|------|------|---------|-------|
| `architect` | `plan` | Design with Codex input | `opus` |
| `implementer-1` | — | Backend changes | `opus` |
| `implementer-2` | — | Frontend changes | `opus` |
| `tester` | — | Write tests, validate integration | `opus` |
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
- "Quality review: test coverage, code patterns, DRY violations + Codex full review" → `quality-reviewer`

All tasks run in parallel.

| Role | Purpose | model |
|------|---------|-------|
| `security-reviewer` | OWASP/CWE + Codex security analysis | `opus` |
| `perf-reviewer` | Performance + Codex perf analysis | `opus` |
| `quality-reviewer` | Quality + Codex full review | `opus` |

## Troubleshooting

**`InputValidationError: An unexpected parameter '...' was provided`:** You passed a parameter the tool's schema doesn't accept. Almost always one of: `team_name` on any tool (it's deprecated and ignored — drop it), `blockedBy` on `TaskCreate` (create first, then `TaskUpdate(addBlockedBy=[...])`), or `recipient`/`type`/`content`/`broadcast` on `SendMessage` (use `to`/`message`/`summary`). See **Tool Signatures** above for the exact parameter list per tool.

**Looking for `TeamCreate` / `TeamDelete`:** They don't exist (removed in Claude Code v2.1.178). Don't try to load or call them. Spawn a teammate to start the team; send `shutdown_request` (and/or let the session exit) to end it.

**`schema was not sent to the API … Load the tool first`:** The deferred tool isn't loaded in the current context. The lead loads tools in Step 1; each teammate must also run `ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")` as its first action.

**Teammate not responding:** They're idle (normal). Send a message — it wakes them up.

**Task stuck as blocked:** Check if the blocking task is actually done but wasn't marked completed. Use `TaskUpdate` to fix.

**File conflicts:** Two teammates edited the same file. Break work so each teammate owns different files, or give one teammate `isolation: "worktree"`.

**Lead implementing instead of delegating:** Tell it: "Wait for your teammates to complete their tasks before proceeding."

**Plan approval stuck:** The architect finished planning but you didn't see the request. Check for a `plan_approval_request` message and respond with `plan_approval_response`.

**Teammate ignoring tasks:** Their prompt might be missing the workflow instructions. Make sure the prompt tells them to check TaskList, use TaskGet, and mark tasks in_progress/completed.

**Codex not being used:** Check that the teammate prompt explicitly instructs them to call the MCP tools. Teammates have access but won't use them unless prompted.

**Codex agreed with you and the code still broke:** classic anchoring. Check whether the dispatch put your diagnosis in `context`/`concerns`/`focus` — if so, you asked the model to grade your own answer and it obliged. Re-dispatch blind (evidence + question only, hypothesis in `caller_hypothesis`). A **⚠️ ANCHORING WARNING** banner on a result is the server telling you this already happened.

**`⚠️ ANCHORING WARNING` banner on a result:** the lint found conclusion language in a neutral scoping field. The answer is not worthless — but its *agreement* with you is. Re-run that dispatch blind; disagreement in the flagged answer is still trustworthy.

**Advisor cited a version/API that turned out to be wrong:** check whether it gave a URL. The server requires a Sources section and forces live web search; an answer with no sources answered from memory. Re-ask demanding primary sources. Also re-verify file:line citations in the tree that is canon for the task — Codex reads the whole workspace and can cite a sibling checkout.

**Advisor answer reads like generic best-practice with no specifics:** it did not actually research. Re-ask with the concrete question and require URLs + retrieval dates.

## Checklist

Before starting a team:
- [ ] Loaded deferred tools with `ToolSearch(query="select:TaskCreate,TaskUpdate,SendMessage,TaskGet,TaskList")` — NO TeamCreate/TeamDelete (they don't exist)
- [ ] Task calls use ONLY documented params — NO `team_name` or `blockedBy` on `TaskCreate`/`TaskUpdate`; set dependencies via `TaskUpdate(taskId=..., addBlockedBy=[...])`
- [ ] `SendMessage` calls use `to` / `message` / `summary` — never `recipient` / `type` / `content` / `broadcast`; protocol `type` goes inside the `message` object
- [ ] Tasks are specific and self-contained with full context in description
- [ ] No two teammates will edit the same files (or the overlapping one uses `isolation: "worktree"`)
- [ ] Each `Agent()` call has `description`, `name`, `model: "opus"`, `subagent_type`, `run_in_background: true`, `prompt` — and NO `team_name`
- [ ] Teammate prompts START with a `ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")` step
- [ ] Teammate prompts include: name, full workflow (TaskList → TaskGet → in_progress → work → completed → SendMessage)
- [ ] Architects use `mode: "plan"` to require plan approval
- [ ] Researchers and architects are prompted to use Codex DURING their work (not just after)
- [ ] Reviewers are prompted to use Codex with strict/critical analysis
- [ ] NO prompts contain conservative language ("keep existing", "maintain current", "the approach is fine")
- [ ] ALL prompts emphasize: performance, quality, adaptability — challenge what exists
- [ ] Cross-model prompts are **blind**: evidence + question, NO caller diagnosis. Any hypothesis rides in `caller_hypothesis`, never in `context`/`concerns`/`focus`/`topic`
- [ ] Advisor answers carrying external claims came back **with URLs**; unsourced version/API/CVE claims were re-asked, not repeated
- [ ] Calls into swappable backends (browser engine, DB driver, storage/LLM provider, cloud SDK, container binary) were checked for **present-but-unsupported** — verified against the vendor compatibility matrix and probed on the engine actually deployed, not the library default
- [ ] Any engine/driver/provider/version **swap** in the diff triggered a sweep of every call into that surface
- [ ] No `try`/`except` degrades a capability miss into a silent no-op without recording which failure occurred
- [ ] No result arrived with a **⚠️ ANCHORING WARNING** banner (if one did, it was re-dispatched blind before being trusted)

Before wrapping up:
- [ ] All tasks marked `completed`
- [ ] All teammates shut down via `shutdown_request` (the implicit team then cleans up on session exit — there is no `TeamDelete`)
