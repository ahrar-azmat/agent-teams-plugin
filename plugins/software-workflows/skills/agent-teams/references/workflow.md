# Agent Teams — the full workflow

Read this when you need the detailed steps, examples and role table behind the core SKILL.md.

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
    description="Create JWT-based auth middleware in src/middleware/auth.py. Must validate tokens, extract user context, and handle expired tokens. Verify it with real requests against the running app — a valid, an expired and a malformed token — and report what each returned.",
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
| `implementer` | Charter one unit and dispatch it to a Codex writer (`abraham`), watch it, read the diff, re-run the checks for real, report — writes no product code itself (SKILL.md rule 11) | `general-purpose` | — | `opus` |
| `reviewer` | Code review with Codex cross-model analysis | `general-purpose` | — | `opus` |
| `verifier` | Run the change for real and report the observed results (no test suites — SKILL.md rule 10) | `general-purpose` | — | `opus` |
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
