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
| `references/writers.md` | before starting a writer (Codex or Claude, as a T3 child task), steering one, or running several writers in one checkout |
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
9. **NO GIT WORKTREES; parallel writers share the real checkout** (user rulings 2026-10-16 and 2026-10-18: "parallel writes can right on the same repo but they should be able to communicate with each other properly"). Never `isolation: "worktree"`, `EnterWorktree` or `git worktree add`.
   - Writers work in the repository's own checkout at the same time, each on the files the lead gave it.
   - When two units must touch the same file, the lead orders them.
   - Broad commands (formatters, dependency installs, code generators, migrations) run one at a time, on the lead's word.
   - Writers neither commit nor push; the lead commits by path.
   - Never `git stash` and never `git add -A` in a shared checkout.
   - A pristine copy of a commit is a read-only export (`git archive <sha> | tar -x -C <scratch dir>`).
10. **No test cases unless the user explicitly asks** (user ruling 2026-10-18: "test it out with results rather than tests"). Verify a change by running the real thing (the app, command, endpoint or install) and recording what was observed. Nobody writes a test, harness, mutation run or checker self-test on their own initiative, and a finding is never closed with "add a test". A repository with more than a few tests: the lead removes them in their own commit unless the user asked to keep them.
11. **Claude and Codex both write, and the lead controls every writer** (user rulings 2026-11-01 and 2026-11-20). The scenario picks the writer:
    - Claude for small changes, review-fix rounds and edits it already holds the context for;
    - Codex for large units, deep native or Codex-internals work, or work running in parallel with the lead.

    Every background writer is a T3 child task in the project's own checkout. The lead starts it (`delegate_task`), steers it (`t3_thread_send`, rule 12) and stops it (`task_cancel`); see `references/writers.md`. At most 2–3 writers run at once per project. Writers start no servers or containers, and their report lists what to run (owner, 2026-11-13). The lead charters each unit, reads the diff, re-runs every check for real, gets one blind Codex review and commits by path. "Codex says green" never closes a task.
12. **A steer adds information; it never cancels the task** (user ruling 2026-11-20). There are two modes:
    - **Soft steer:** nothing running is stopped, and the writer takes the message in at its next step: `t3_thread_send(mode="steer")`. A Claude writer waiting on a background command takes it in at once, while the command keeps running.
    - **Hard steer:** the step stops at once, then the writer takes the message in with its whole history: `t3_thread_send(mode="restart")`. For a Codex Oracle run, use `codex_cancel_run`, then `codex_resume_run(nudge=…)`.

    Head every steer `LEAD STEER (soft)` or `LEAD STEER (hard)` and say: "Your task and every earlier instruction still stand. If this seems to conflict with an earlier instruction, keep the earlier one and tell me before acting on that part." Every charter tells the writer the same thing. To change an instruction, answer the writer's question, or cancel the task and start a new one.
    - A steer needs an active turn. Send follow-up work to a writer that has finished with `t3_thread_send(mode="auto")`.

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

- **Tools:** `mcp__plugin_software-workflows_codex-oracle__<tool>` (a user-scope server names them `mcp__codex-oracle__<tool>`).
  - `research`, `architect_review`, `code_review` and `codex_query` advise.
  - `codex_runs`, `codex_run_log`, `codex_cancel_run` and `codex_resume_run` watch, stop and continue those runs (rule 12).
  - Writers are T3 child tasks, not Oracle tools.
- **Reviews continue their own session** (`session="continue"`, the default). Review fewer times, and follow up in the same session with a short note of what changed, never a restated history. The result's second line names the session and its turn.
- **`infra: true`** when live state matters (SSH, a live database, logs); state the access pattern in the prompt when you know it.
- **A ⚠️ ANCHORING WARNING** on a result means a scoping field (`context`, `concerns`, `focus`, `topic`) carried your conclusion: re-dispatch blind before trusting its agreement. Disagreement is strong signal either way.
- **Setup:** if the Codex tools are missing or a session-start notice says setup is incomplete, run `/software-workflows:setup`.

## Writers in brief

Details, the charter lines and the measured behavior: `references/writers.md`.

- **Charter** the property and its acceptance (which real runs show it is done). The charter also:
  - names the writer's own files;
  - says it commits and pushes nothing, starts nothing, and writes no test cases (rule 10);
  - says that LEAD STEER messages add information and never cancel its task, and that a seeming conflict keeps the earlier instruction and goes back to the lead before the writer acts on it.

  Mark mechanism hints as beatable. Give access paths, never credentials.
- **Start** it with `delegate_task`: provider `codex` or `claudeAgent`, effort `max`, `isolation: "shared"`, `mode: "async"`. Its completion wakes you, so don't poll. Read its transcript with `t3_thread_read` when you need to see progress.
- **Steer** it (rule 12): soft with `t3_thread_send(mode="steer")`, hard with `mode="restart"`. **Stop** it with `task_cancel`.
- **Verify it yourself.**
  - Read its changes by its paths: `git diff HEAD -- <paths>`, plus `git status --short -- <paths>` for new files.
  - Re-run the checks for real.
  - Get one blind `code_review`, continuing the session for follow-ups.
  - Send CRITICAL and HIGH findings to the writer as a new turn (`mode="auto"`), or fix small ones yourself.
  - Commit by path, with the observed results in the message. Push only when the user says so.
