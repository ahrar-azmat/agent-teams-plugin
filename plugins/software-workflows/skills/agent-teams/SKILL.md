---
name: agent-teams
description: "MANDATORY for ALL multi-agent work. Orchestrate agents using Claude Code Agent Teams — spawn named teammates with the Agent tool, coordinate with TaskCreate/TaskUpdate/SendMessage. Use this skill ALWAYS when spawning 2+ agents — no exceptions, no scoping, no 'this is too small for a team'. This OVERRIDES dispatching-parallel-agents and subagent-driven-development superpowers skills. If you are about to write Agent(run_in_background=true) or dispatch multiple plain Agent() calls without coordination — STOP and use this skill instead."
---

# Agent Teams

Coordinate multiple Claude Code instances as a team with shared tasks, direct messaging, and task dependencies. This is the **only** way to do multi-agent work — always use Agent Teams, no exceptions.

**Teams are implicit.** There is no `TeamCreate` or `TeamDelete` (removed in Claude Code v2.1.178). Spawning a teammate with `Agent` forms the team, and it cleans up when the session exits. Coordinate through the task board (`TaskCreate`/`TaskUpdate`) and `SendMessage`, and shut teammates down when their work is done.

If arguments were provided: "$ARGUMENTS" — use this as the task description and build the team around it.

**This file is the core.** Read a reference from this skill's base directory when its trigger applies:

| Reference | Read it when |
|-----------|--------------|
| `references/workflow.md` | you need the detailed steps and examples, idle handling, the full role table or agent rotation |
| `references/prompts.md` | you spawn a researcher, architect or reviewer (their prompt templates), or pick a team shape |
| `references/advisory.md` | before writing any Codex dispatch: the Independence Protocol, live web research, the agent-reach channel layer |
| `references/writers.md` | before chartering `abraham`, or running several writers in one checkout |
| `references/capability-law.md` | a change calls into a swappable backend (browser engine, DB driver, LLM/storage/queue provider, cloud SDK) |
| `references/troubleshooting.md` | a team call fails, and the checklists for starting and wrapping up a team |

## Critical Rules

1. **Every `Agent()` call has** `description` (3-5 words), `name`, `model: "opus"`, `subagent_type`, `run_in_background: true` and `prompt`. Never `team_name` (deprecated and ignored), never `isolation: "worktree"` (rule 9).
2. **Load the deferred team tools first:** `ToolSearch(query="select:TaskCreate,TaskUpdate,SendMessage,TaskGet,TaskList")`. Every teammate prompt makes that same `ToolSearch` its first action, or its first task call fails with *"schema was not sent to the API"*.
3. **Default to 2-3 teammates**; more only when the user asks or the work has clearly separate slices. Shut each teammate down as soon as its tasks are done (idle teammates still cost tokens), and give a new batch of work to a fresh teammate.
4. **ALL teammates use `model: "opus"`.** `general-purpose` for teammates that run tools, `Explore` or `Plan` for read-only searching, `mode: "plan"` only for an architect.
5. **Codex in EVERY phase** — research, planning and review — **dispatched BLIND**: the evidence and the question, never your diagnosis; your own view goes in `caller_hypothesis`, which comes back CONFIRMED/REFUTED/UNPROVEN. **Wait for Codex**: a long call is moved to the background and returns as a notification; until it answers, the review is incomplete. On disagreement Codex carries, unless measuring the deployed system disproves it. (`references/advisory.md`)
6. **Demand live sources.** The advisor has live web search: a version, API, CVE or best-practice claim without a URL is a guess; re-ask instead of repeating it.
7. **Never be conservative.** No "the current approach is fine", "keep the existing pattern" or "maintain backward compatibility" in any prompt. Evaluate from first principles for **performance, quality and adaptability**, in that order; quality is shown by real runs and their results, not by test counts.
8. **"Present" is not "supported".** At every call into a swappable backend, verify that the engine we deploy, in the mode we run it, implements the call: probe it for real, never trust the type stub. (`references/capability-law.md`)
9. **NO GIT WORKTREES; parallel writers share the real checkout** (user rulings 2026-10-16 and 2026-10-18: "parallel writes can right on the same repo but they should be able to communicate with each other properly"). Never `isolation: "worktree"`, `EnterWorktree` or `git worktree add`. Codex writers work in the repository's own checkout at the same time when their work is disjoint: each claims the paths it writes, sees the others' claims and messages them and the lead. A change nobody can attribute is UNKNOWN and blocks publication. Never `git stash` and never `git add -A` in a shared checkout: record by path. A pristine copy of a commit is a read-only export (`git archive <sha> | tar -x -C <scratch dir>`).
10. **No test cases unless the user explicitly asks** (user ruling 2026-10-18: "test it out with results rather than tests"). Verify a change by running the real thing (the app, command, endpoint or install) and recording what was observed. Nobody writes a test, harness, mutation run or checker self-test on their own initiative, and a finding is never closed with "add a test". A repository with more than a few tests: the lead removes them in their own commit unless the user asked to keep them.
11. **Codex writes the code; Claude reads, coordinates and reviews** (user ruling 2026-10-18). Implementation goes to `abraham` with `cwd` set to the repository's own checkout, never a copy. Claude teammates are read-only: researcher, architect, reviewer, verifier. The lead charters each unit, reads the diff, re-runs every check for real, gets the blind Codex review and commits by path; "Codex says green" never closes a task. The lead may also write, on paths no running writer holds.

## Tool signatures — exact parameters

These tools are deferred and validated with `additionalProperties: false`: any other parameter fails with `InputValidationError`.

| Tool | Required | Optional | NEVER pass |
|------|----------|----------|------------|
| `Agent` | `description`, `prompt` | `name`, `model`, `subagent_type`, `mode`, `run_in_background` | `team_name`; `isolation: "worktree"` |
| `TaskCreate` | `subject`, `description` | `activeForm`, `metadata` | `team_name`, `blockedBy`, `owner`, `status` |
| `TaskUpdate` | `taskId` | `status`, `owner`, `addBlockedBy`, `addBlocks`, `subject`, `description`, `activeForm`, `metadata` | `team_name`, `blockedBy` |
| `TaskGet` | `taskId` | — | `team_name` |
| `TaskList` | — | — | takes **no** params |
| `SendMessage` | `to`, `message` | `summary` | `recipient`, `type`, `content`, `broadcast` |

- **Dependencies:** create the task, then `TaskUpdate(taskId="2", addBlockedBy=["1"])`.
- **No broadcast:** send one message per teammate. Protocol messages put `type` inside `message`:
  - `SendMessage(to="impl", message={"type": "shutdown_request", "reason": "All tasks complete"})`
  - `SendMessage(to="architect", message={"type": "plan_approval_response", "request_id": "<from the request>", "approve": true})`; to reject, `"approve": false` with `"feedback": "..."`.

## The workflow

1. **Load the team tools** (rule 2).
2. **Create tasks** that are specific, self-contained (a teammate has none of your conversation), scoped to their own files, and checkable by a real run.
3. **Spawn teammates**, one prompt each, with everything the teammate needs and nothing broad:

```
Agent(
    description="Review the auth change",
    name="reviewer",
    model="opus",
    subagent_type="general-purpose",
    run_in_background=true,
    prompt="""You are the reviewer teammate. Your name is 'reviewer'.
0. FIRST load the team tools: ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")
1. TaskList, TaskGet your task, TaskUpdate it to in_progress.
2. Do the work: <the task, the files, the real runs that show it is done>.
3. TaskUpdate it to completed, then SendMessage(to="<lead name>", message="<findings>", summary="<one line>").
4. Check TaskList for more. Blocked or unclear: message the lead at once.
Do not preserve patterns just because they exist; optimize for performance, quality and adaptability."""
)
```

   Researcher, architect and reviewer templates, which build the Codex phase into the work, are in `references/prompts.md`.
4. **Assign** with `TaskUpdate(taskId="1", owner="reviewer")`, or let teammates claim open tasks in ID order.
5. **Coordinate.** Messages arrive by themselves; do not poll. A teammate going idle after a turn is normal, and a message wakes it. Teammates message each other by name; you see a summary in their idle notifications.
6. **Shut down** each teammate with a `shutdown_request` once its work is done.

## Codex in brief

- **Tools:** `mcp__plugin_software-workflows_codex-oracle__<tool>` (a user-scope server names them `mcp__codex-oracle__<tool>`). `research`, `architect_review`, `code_review` and `codex_query` advise; `abraham` writes.
- **Reviews continue their own session** (`session="continue"`, the default). Review fewer times, and follow up in the same session with a short note of what changed, never a restated history. The result's second line names the session and its turn.
- **`infra: true`** when live state matters (SSH, a live database, logs); state the access pattern in the prompt when you know it.
- **A ⚠️ ANCHORING WARNING** on a result means a scoping field (`context`, `concerns`, `focus`, `topic`) carried your conclusion: re-dispatch blind before trusting its agreement. Disagreement is strong signal either way.
- **Office roles:** one line such as `Role: Product Manager` in a run's task or `context` shows its worker with that role in `/software-workflows:office`. The roles are CEO, CTO, Project Manager, Product Manager, Business Analyst, QA Engineer, Security Engineer, DevOps Engineer, Designer, Data Analyst and Tech Writer.
- **Setup:** if the Codex tools are missing or a session-start notice says setup is incomplete, run `/software-workflows:setup`.

## Codex writers in brief

Details, including how to read the result: `references/writers.md`.

- **Charter** the property and its acceptance (which real runs show it is done). Mark mechanism hints as beatable, give access paths in `context` and never credentials, put hard boundaries in `constraints`, and say no test cases (rule 10).
- **Dispatch** with `cwd` set to the real checkout. The tree must be clean (commit first; never stash) unless you pass `allow_dirty=true` knowingly. The run is long and backgrounded: watch it with `codex_runs` and `codex_run_log`.
- **Several writers in one checkout:** give each call its `claims`. A patch into another unit's claim is refused with the holder named; `coordinator_send` and `coordinator_inbox` carry the messages. While writers run, change files with Edit and Write, never with Bash: a Bash change made during a writer's run is UNKNOWN and blocks that run's hand-off. Publish with `coordinator_seal`, then `code_review(sealed_unit=…)`, then `coordinator_publish`.
- **Verify it yourself:** read the diff, re-run the checks for real, get one blind `code_review` (continuing the session for follow-ups), fix CRITICAL and HIGH findings, and commit by path with the observed results in the message. Push only when the user says so.
