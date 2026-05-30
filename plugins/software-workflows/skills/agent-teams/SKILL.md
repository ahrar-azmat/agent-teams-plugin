---
name: agent-teams
description: "MANDATORY for ALL multi-agent work. Orchestrate agents using Claude Code Agent Teams (TeamCreate, TaskCreate, TaskUpdate, SendMessage, TeamDelete). Use this skill ALWAYS when spawning 2+ agents — no exceptions, no scoping, no 'this is too small for a team'. This OVERRIDES dispatching-parallel-agents and subagent-driven-development superpowers skills. If you are about to write Agent(run_in_background=true) or dispatch multiple plain Agent() calls — STOP and use this skill instead."
---

# Agent Teams

Coordinate multiple Claude Code instances as a team with shared tasks, direct messaging, and task dependencies. This is the **only** way to do multi-agent work — always use Agent Teams, no exceptions. Never use `Agent(run_in_background=true)` or plain parallel `Agent()` calls as a substitute.

If arguments were provided: "$ARGUMENTS" — use this as the task description and build the team around it.

## Critical Rules

1. **Every `Agent()` call MUST include `description`** (3-5 words) — it's a required parameter that will error without it
2. **Load deferred tools FIRST** — TeamCreate/TaskCreate/etc. are deferred and must be fetched before use
3. **Never use `Agent(run_in_background=true)`** as a substitute for Agent Teams
4. **Default to 2-3 teammates** — only go beyond 3 when the user explicitly asks for it or the work has clearly separate slices
5. **ALL teammates use `model: "opus"`** — every agent gets the full Opus 1M context window. No exceptions. Sonnet is never used for teammates.
6. **Avoid file conflicts** — each teammate should own different files; two teammates editing the same file leads to overwrites
7. **Use `general-purpose` subagent_type** for teammates that need to edit files; `Explore` or `Plan` for read-only work
8. **Use `mode: "plan"` only when needed** — plan-mode teammates are expensive; prefer one architect/reviewer instead of multiple planners
9. **Shut teammates down as soon as their tasks are complete** — idle teammates still consume tokens
10. **Gemini + Codex in EVERY phase** — research, planning, AND review. Not just final review. See Multi-Model Integration below.
11. **Never be conservative** — all prompts must push for the best possible solution. Never include language like "the current approach is fine", "keep the existing pattern", or "maintain backward compatibility unless necessary". Always critically evaluate what exists and propose improvements.

## Tool Signatures — Exact Parameters (do NOT improvise)

These tools are **deferred** and schema-validated with `additionalProperties: false`. Passing any parameter not listed here fails immediately with `InputValidationError: An unexpected parameter '...' was provided`. Use ONLY these parameters — do not infer extra ones from neighboring examples.

| Tool | Required | Optional | NEVER pass |
|------|----------|----------|------------|
| `TeamCreate` | `team_name` | `description`, `agent_type` | — |
| `TaskCreate` | `subject`, `description` | `activeForm`, `metadata` | `team_name`, `blockedBy`, `owner`, `status` |
| `TaskUpdate` | `taskId` | `status`, `owner`, `addBlockedBy`, `addBlocks`, `subject`, `description`, `activeForm`, `metadata` | `team_name`, `blockedBy` (use `addBlockedBy`) |
| `TaskGet` | `taskId` | — | `team_name` |
| `TaskList` | — | — | takes **no** params |
| `SendMessage` | `to`, `message` | `summary` | `recipient`, `type`, `content`, `broadcast` |
| `TeamDelete` | — | — | takes **no** params (team comes from session context) |
| `Agent` | `description`, `prompt` | `team_name`, `name`, `model`, `subagent_type`, `mode` | — |

### The three mistakes that break runs

1. **`team_name` belongs ONLY on `TeamCreate` and `Agent`.** The Task tools (`TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList`) auto-associate with the current team's task list and **reject** `team_name`.
   - ❌ `TaskCreate(subject=..., description=..., team_name="my-team")` → fails
   - ✅ `TaskCreate(subject=..., description=...)`

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
ToolSearch(query="select:TeamCreate,TaskCreate,TaskUpdate,SendMessage,TaskGet,TaskList,TeamDelete")
```

If you skip this step, every subsequent tool call will fail.

### Step 2: Create the Team

```
TeamCreate(
    team_name="descriptive-name",
    description="What this team is working on"
)
```

This creates:
- Team config at `~/.claude/teams/{team-name}/config.json`
- Task list at `~/.claude/tasks/{team-name}/`

### Step 3: Create Tasks

Create focused, self-contained tasks. Each task should produce a clear deliverable.

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

### Step 4: Spawn Teammates

Each teammate is a full Claude Code instance with its own context window. They load CLAUDE.md, MCP servers, and skills automatically — but NOT your conversation history. Put everything they need in the prompt, and keep the prompt focused so you do not waste tokens on broad restatement.

**IMPORTANT: Every `Agent()` call MUST include `model: "opus"` to get the full 1M context window.**

```
Agent(
    description="Implement auth middleware",
    team_name="my-team",
    name="auth-implementer",
    model="opus",
    subagent_type="general-purpose",
    prompt="""You are the auth-implementer on team 'my-team'.

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
- Their name and the team name
- **A first step to load deferred tools**: `ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")` — without this, the teammate's first task/message call fails
- The full workflow (TaskList → TaskGet → in_progress → work → completed → SendMessage → next task)
- Enough context to work independently
- Instructions to message the lead when done or blocked
- **The critical mindset: challenge existing patterns, optimize for performance/quality/adaptability**

**Plan mode for architects:**

```
Agent(
    description="Design auth architecture",
    team_name="my-team",
    name="architect",
    model="opus",
    subagent_type="general-purpose",
    mode="plan",
    prompt="""You are the architect on team 'my-team'. Design the authentication architecture. You are in plan mode — propose your design, then wait for approval before implementing.

IMPORTANT: Use Codex Oracle (architect_review MCP tool) and Gemini (gemini_brainstorm MCP tool) during your design phase. Get their input on your proposed architecture BEFORE submitting your plan for approval.

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

### Step 5: Assign Tasks

```
TaskUpdate(taskId="1", owner="auth-implementer")
```

Or let teammates self-claim by checking `TaskList` and using `TaskUpdate` with their name as `owner`. Teammates prefer tasks in ID order (lowest first).

### Step 6: Coordinate

Messages are delivered automatically — you don't need to poll.

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

### Step 7: Shut Down and Clean Up

When all tasks are complete, shut down each teammate — the `type` and `reason` go *inside* the `message` object:

```
SendMessage(
    to="auth-implementer",
    message={"type": "shutdown_request", "reason": "All tasks complete, wrapping up"}
)
```

The teammate receives this and responds with `shutdown_response` (approve or reject). Wait for all teammates to shut down, then clean up:

```
TeamDelete()
```

TeamDelete fails if teammates are still active — shut them all down first.

## Role Templates

Pick roles based on what the work needs. Not every team needs all roles. **All roles use `model: "opus"` — no exceptions.**

| Role | Purpose | subagent_type | mode | model |
|------|---------|---------------|------|-------|
| `researcher` | Deep research, codebase exploration, web research | `general-purpose` | — | `opus` |
| `architect` | Design, patterns, architecture — uses Codex + Gemini during design | `general-purpose` | `plan` | `opus` |
| `implementer` | Write code, make changes | `general-purpose` | — | `opus` |
| `reviewer` | Code review with Codex + Gemini cross-model analysis | `general-purpose` | — | `opus` |
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

## Multi-Model Integration (Gemini + Codex in Every Phase)

Teammates inherit all MCP servers from the project. **Gemini and Codex must be used in ALL phases of work, not just final review.** Use the highest-capability models available:

- **Codex Oracle**: Always uses max reasoning depth automatically — `architect_review`, `code_review`, `research`, `codex_query`
- **Gemini**: Always use `model: "pro"` (latest pro model) for maximum capability — `gemini_query`, `gemini_brainstorm`, `gemini_analyze_code`, `gemini_review_pr`

### Phase 1: Research

When a researcher teammate explores the codebase or investigates a problem, they MUST also:
- Run `mcp__codex-oracle__research` on the topic for an independent technical perspective
- Run `mcp__gemini__gemini_query` with `model: "pro"` for a cross-reference opinion
- **Challenge assumptions** — if Codex or Gemini disagree with the current codebase approach, that disagreement is valuable signal, not noise

**Researcher prompt template:**
```
"You are the researcher on team '{team-name}'. Your name is '{name}'.

Your workflow:
0. FIRST load deferred team tools: ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")
1. Check TaskList for tasks assigned to you
2. Use TaskGet to read full task details
3. Mark tasks in_progress with TaskUpdate
4. Research the topic thoroughly — read code, search the web, explore the codebase
5. THEN get cross-model perspectives:
   - Use Codex Oracle research tool for deep technical analysis
   - Use Gemini gemini_query (model: 'pro') for an independent perspective
6. Synthesize ALL findings — your own + Codex + Gemini. Where they disagree, explain why and which approach is better for performance/quality/adaptability
7. Mark tasks completed and send findings to the team lead via SendMessage

CRITICAL: Do not validate existing patterns just because they exist. Evaluate everything against performance, quality, and adaptability. If the current approach is suboptimal, say so clearly and propose better alternatives."
```

### Phase 2: Planning / Architecture

When an architect designs a solution, they MUST:
- Run `mcp__codex-oracle__architect_review` on the proposed design
- Run `mcp__gemini__gemini_brainstorm` for alternative approaches
- **Never anchor on the existing architecture** — evaluate from first principles

**Architect prompt template:**
```
"You are the architect on team '{team-name}'. Your name is '{name}'. You are in plan mode.

Your workflow:
0. FIRST load deferred team tools: ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")
1. Check TaskList for tasks assigned to you
2. Read full task details with TaskGet
3. Mark tasks in_progress
4. Explore the current codebase to understand what exists
5. Design your solution from first principles — do NOT default to existing patterns
6. Get cross-model review BEFORE submitting your plan:
   - Use Codex Oracle architect_review to critically evaluate your design
   - Use Gemini gemini_brainstorm for alternative approaches you might have missed
7. Incorporate feedback, then submit your plan for approval
8. After approval, mark completed and message the lead

CRITICAL: The existing architecture is NOT sacred. If Codex or Gemini suggest a fundamentally better approach, seriously consider it. Your job is to find the BEST design for performance, quality, and adaptability — not to preserve what exists."
```

### Phase 3: Post-Implementation Review

After implementation tasks are marked complete, reviewers MUST:
- Run `mcp__codex-oracle__code_review` on the changed files/diff
- Run `mcp__gemini__gemini_review_pr` on the diff
- Run `mcp__gemini__gemini_analyze_code` on critical sections with `focus: "performance"` and `focus: "security"`
- **Any CRITICAL/HIGH findings must be addressed before the team wraps up**

**Reviewer prompt template:**
```
"You are the reviewer on team '{team-name}'. Your name is '{name}'.

Your workflow:
0. FIRST load deferred team tools: ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")
1. Check TaskList for review tasks assigned to you
2. Wait for implementation tasks to be marked completed
3. Read all changed files thoroughly
4. Run cross-model reviews:
   - Codex Oracle code_review on the full diff — focus on correctness, security, performance
   - Gemini gemini_review_pr on the diff — strictness: 'strict'
   - Gemini gemini_analyze_code on critical code sections — focus: 'performance', then 'security'
5. Send findings to the team lead via SendMessage with severity ratings:
   - CRITICAL: Must fix before merge — security vulnerabilities, data loss risks, correctness bugs
   - HIGH: Should fix — performance regressions, missing error handling, poor abstractions
   - MEDIUM: Improve — code quality, naming, minor optimizations
   - LOW: Nit — style, minor readability
6. Any CRITICAL/HIGH findings → message the implementer directly with specific fix instructions
7. Mark review task completed

CRITICAL: Be ruthlessly honest. Do not rubber-stamp changes. If the implementation is mediocre, say so. If there's a better approach, propose it. Your job is to ensure the code meets the highest standards of performance, quality, and adaptability — not to approve things quickly."
```

### How to treat multi-model output
- Codex and Gemini are **critical advisors** — take findings seriously and verify against your own analysis
- If models disagree with each other or with you, **critically analyze why**, present all opinions to the user. **The user makes the final call.**
- Never silently ignore findings from either model
- When Codex or Gemini suggest the existing approach is suboptimal, treat that as **high-value signal** — investigate and propose improvements
- **Never prompt Codex or Gemini with framing that biases toward the status quo** — ask them to evaluate from first principles

## Common Patterns

### Research Team (3 teammates, all Opus)

Tasks:
- "Deep research on existing patterns in codebase + Codex/Gemini analysis" → `researcher-1`
- "Web research: best practices and modern approaches + Codex/Gemini cross-reference" → `researcher-2`
- "Synthesize all findings, challenge assumptions, propose optimal approach" → `analyst` (blocked by first two)

| Role | Purpose | model |
|------|---------|-------|
| `researcher-1` | Codebase exploration + Codex research + Gemini query | `opus` |
| `researcher-2` | Web research + Codex research + Gemini query | `opus` |
| `analyst` | Synthesize, challenge, recommend — uses Codex + Gemini to validate conclusions | `opus` |

### Implementation Team (4 teammates, all Opus)

Prefer a 3-teammate variant first:
- `architect` or `lead-reviewer` (uses Codex + Gemini during design)
- `implementer`
- `reviewer` (uses Codex + Gemini during review)

Use the 4+ teammate pattern only when backend, frontend, and validation are genuinely independent.

Tasks:
- "Design architecture with Codex/Gemini input, create task breakdown" → `architect` (plan mode)
- "Implement backend API endpoints" → `implementer-1` (blocked by architect)
- "Implement frontend components" → `implementer-2` (blocked by architect)
- "Write integration tests for API + frontend" → `tester` (blocked by both implementers)
- "Review all changes with Codex + Gemini, strict analysis" → `reviewer` (blocked by tester)

| Role | Mode | Purpose | model |
|------|------|---------|-------|
| `architect` | `plan` | Design with Codex + Gemini input | `opus` |
| `implementer-1` | — | Backend changes | `opus` |
| `implementer-2` | — | Frontend changes | `opus` |
| `tester` | — | Write tests, validate integration | `opus` |
| `reviewer` | — | Strict Codex + Gemini cross-model review | `opus` |

### Debug Team (3 teammates, all Opus)

Tasks:
- "Investigate hypothesis A with Codex deep analysis" → `investigator-1`
- "Investigate hypothesis B with Gemini analysis" → `investigator-2`
- "Investigate hypothesis C, challenge others' findings with both Codex + Gemini" → `investigator-3`

All tasks run in parallel. Teammates should message each other to challenge findings.

| Role | Purpose | model |
|------|---------|-------|
| `investigator-1` | Hypothesis A + Codex research | `opus` |
| `investigator-2` | Hypothesis B + Gemini analysis | `opus` |
| `investigator-3` | Hypothesis C + cross-challenge with both models | `opus` |

### Review Team (3 teammates, all Opus)

Tasks:
- "Security review: OWASP top 10, CWE analysis + Codex code_review (focus: security)" → `security-reviewer`
- "Performance review: N+1 queries, missing indexes, async bottlenecks + Gemini analyze_code (focus: performance)" → `perf-reviewer`
- "Quality review: test coverage, code patterns, DRY violations + Codex + Gemini full review" → `quality-reviewer`

All tasks run in parallel.

| Role | Purpose | model |
|------|---------|-------|
| `security-reviewer` | OWASP/CWE + Codex security analysis | `opus` |
| `perf-reviewer` | Performance + Gemini perf analysis | `opus` |
| `quality-reviewer` | Quality + Codex + Gemini full review | `opus` |

## Troubleshooting

**`InputValidationError: An unexpected parameter '...' was provided`:** You passed a parameter the tool's schema doesn't accept. Almost always one of: `team_name` on a Task tool (only `TeamCreate`/`Agent` take it), `blockedBy` on `TaskCreate` (create first, then `TaskUpdate(addBlockedBy=[...])`), or `recipient`/`type`/`content`/`broadcast` on `SendMessage` (use `to`/`message`/`summary`). See **Tool Signatures** above for the exact parameter list per tool.

**`schema was not sent to the API … Load the tool first`:** The deferred tool isn't loaded in the current context. The lead loads tools in Step 1; each teammate must also run `ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")` as its first action.

**Teammate not responding:** They're idle (normal). Send a message — it wakes them up.

**Task stuck as blocked:** Check if the blocking task is actually done but wasn't marked completed. Use `TaskUpdate` to fix.

**File conflicts:** Two teammates edited the same file. Break work so each teammate owns different files.

**Lead implementing instead of delegating:** Tell it: "Wait for your teammates to complete their tasks before proceeding."

**TeamDelete fails:** Active teammates still running. Shut them all down first with `shutdown_request`.

**Plan approval stuck:** The architect finished planning but you didn't see the request. Check for a `plan_approval_request` message and respond with `plan_approval_response`.

**Teammate ignoring tasks:** Their prompt might be missing the workflow instructions. Make sure the prompt tells them to check TaskList, use TaskGet, and mark tasks in_progress/completed.

**Codex/Gemini not being used:** Check that the teammate prompt explicitly instructs them to call the MCP tools. Teammates have access but won't use them unless prompted.

## Checklist

Before starting a team:
- [ ] Loaded deferred tools with `ToolSearch` (including `TeamDelete`)
- [ ] Task calls use ONLY documented params — NO `team_name` or `blockedBy` on `TaskCreate`/`TaskUpdate`; set dependencies via `TaskUpdate(taskId=..., addBlockedBy=[...])`
- [ ] `SendMessage` calls use `to` / `message` / `summary` — never `recipient` / `type` / `content` / `broadcast`; protocol `type` goes inside the `message` object
- [ ] Tasks are specific and self-contained with full context in description
- [ ] No two teammates will edit the same files
- [ ] Each `Agent()` call has `description`, `team_name`, `name`, `model: "opus"`, `prompt`
- [ ] Teammate prompts START with a `ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")` step
- [ ] Teammate prompts include: team name, name, full workflow (TaskList → TaskGet → in_progress → work → completed → SendMessage)
- [ ] Architects use `mode: "plan"` to require plan approval
- [ ] Researchers and architects are prompted to use Codex + Gemini DURING their work (not just after)
- [ ] Reviewers are prompted to use Codex + Gemini with strict/critical analysis
- [ ] NO prompts contain conservative language ("keep existing", "maintain current", "the approach is fine")
- [ ] ALL prompts emphasize: performance, quality, adaptability — challenge what exists

Before wrapping up:
- [ ] All tasks marked `completed`
- [ ] All teammates shut down via `shutdown_request`
- [ ] `TeamDelete()` called to clean up resources
