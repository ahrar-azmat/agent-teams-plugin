---
name: agent-teams
description: "MANDATORY for ALL multi-agent work: how the lead starts, steers, stops and verifies helpers and writers. Writers, Claude and Codex, run as T3 child tasks the owner can watch in T3's Lineage panel; quick helpers the lead waits for are native foreground subagents; reviews go to the Codex Oracle, dispatched blind. Use it whenever you are about to spawn, message, steer or stop another agent, or run several writers at once."
---

# Agent work: T3 children for writers, native helpers, the Oracle for reviews

How the lead runs other agents, and the owner's rules around them.

If arguments were provided: "$ARGUMENTS". Use them as the task description.

**This file is the core.** Read a reference from this skill's base directory when its trigger applies:

| Reference | Read it when |
|-----------|--------------|
| `references/writers.md` | before starting a writer (Claude or Codex), steering one, or running several at once. It has the charter lines, the exact calls and the measured behavior. |
| `references/prompts.md` | you start a researcher, architect or reviewer helper (prompt templates with the Codex phase built in), or pick a shape for the work |
| `references/advisory.md` | before writing any Codex dispatch: the Independence Protocol, live web research, the agent-reach channel layer |
| `references/capability-law.md` | a change calls into a swappable backend (browser engine, DB driver, LLM/storage/queue provider, cloud SDK) |
| `references/troubleshooting.md` | a call fails, and the checklists for starting and finishing work |

## Which tool for which agent

| Work | Tool | Why |
|------|------|-----|
| A writer, Claude or Codex, and any helper that runs longer than a few minutes | T3 `delegate_task`. Claude: `claudeAgent`, `claude-opus-5-5`, effort max. Codex: `codex`, `gpt-6.2-astra`, `reasoningEffort` max. | The owner can see it in T3's Lineage panel, open its thread, follow it live and stop it (owner, 2026-11-21: "current background doesn't offer me visibilty as t3 with this"). It also survives the lead's restarts. |
| A quick helper the lead waits for: a lookup, a search, a short read-only check | `Agent`, `run_in_background: false` | The answer comes back in the same turn. |
| Review, architecture, research, a second opinion | Codex Oracle: `code_review`, `architect_review`, `research`, `codex_query` | Cross-model and blind |
| A writer in a plain Claude Code terminal, where T3 is not running | `Agent` in the background | There is no T3 there. Steer it with `SendMessage` / `TaskStop` (rule 2). |

**No native background writers in T3 sessions.** The owner tried them on 2026-11-21 and chose T3 children for visibility. A native background agent runs inside the lead's session, out of sight.

**Agent teams do not exist here.** Teammates, the shared task board and `shutdown_request` need an interactive CLI with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. T3 runs Claude as an Agent SDK session, where Claude never spawns teammates: a named `Agent` call is a plain subagent. Use `TaskCreate` and `TaskList` only as your own checklist, if they help.

## Rules

1. **Start a writer with `delegate_task`** (`references/writers.md` §2): `title`, `task` (the charter), `role="implementation"`, `mode="async"`, `isolation="shared"` or `"worktree"` (rule 6), and the `target` above.
   - Call `orchestrator_capabilities` once per thread before the first `delegate_task`, and keep each `taskId` and `childThreadId`.
   - **Every `Agent` call** (a helper, or a writer outside T3) has `description` (3–5 words), `name` (the address for `SendMessage` and `TaskStop`), `subagent_type` (`general-purpose` for anything that runs tools, `Explore` or `Plan` for read-only searching), `model: "opus"` and `prompt`. Never `team_name`.
2. **Steer, with the owner's two modes** (owner, 2026-11-20). A steer adds information and never cancels the task:

   | Mode | T3 child (every writer) | Codex Oracle run | Native subagent (outside T3) |
   |------|-------------------------|------------------|------------------------------|
   | **Soft:** nothing running is stopped; the writer takes the message in at its next step | `t3_thread_send(threadId=<child>, mode="steer", message=…)` | after it answers: `codex_resume_run(run, nudge=…)` | `SendMessage(to=<name>, message=…)` |
   | **Hard:** the current step stops, then the writer takes the message in with its whole history | `t3_thread_send(threadId=<child>, mode="restart", message=…)` | `codex_cancel_run(run)`, then `codex_resume_run(run, nudge=…)` | `TaskStop(task_id=<name>)`, then `SendMessage(to=<name>, …)` once the stop notice arrives |

   - **Header:** start every steer with `LEAD STEER (soft)` or `LEAD STEER (hard)` and say: "Your task and every earlier instruction still stand. If this seems to conflict with an earlier instruction, keep the earlier one and tell me before acting on that part." Every charter tells the writer the same thing.
   - **Changing an instruction:** answer the writer's question, or stop the task and start a new one.
   - **Order:** when a steer must not arrive before the writer's current command finishes, wait for that command to finish, then send it.
   - **A finished writer:** a T3 child needs `t3_thread_send(mode="auto")`, because `steer` needs an active turn. A native one resumes on `SendMessage`.
   - **A writer's question** waits in its thread: `t3_pending_request(action="list")`, then `read`, then `respond`.
   - **On Windows,** `codex_resume_run` cannot continue an Oracle run, so a hard steer of one there only stops it.
3. **Stop** a T3 child with `task_cancel(taskId)`, never `t3_thread_interrupt`. Stop an Oracle run with `codex_cancel_run`, and a native subagent with `TaskStop(task_id=<name>)`.
4. **Wait; don't poll.** A T3 child's completion wakes your thread; a native subagent's arrives as a notification.
   - The owner watches writers in T3's Lineage panel. To read a child's progress yourself, use `t3_thread_read` once in a while, not in a loop.
   - Never read a native subagent's output file with the shell; it is the whole JSONL transcript.
5. **Up to 5–10 writers may run at once** (owner, 2026-11-21: "most of the time we will be working with 5-10 parallel writers working on multiple things"), each on its own files or in its own worktree.
   - Writers write code and run quick checks that end by themselves (type check, lint, build). They start no servers, containers or databases; their report lists WHAT TO RUN and HOW TO SEE IT.
   - The lead tests. It runs the project's one shared test copy and hands it out first in, first out. No test databases for writers (owner, 2026-11-21).
   - Hooks enforce this: whatever a command leaves running is stopped when the command returns (see "Cleanup that runs by itself").
6. **Worktrees are allowed** (owner, 2026-11-21: "I think worktrees are fine"). Give a writer one with `isolation: "worktree"` on `delegate_task` (or on `Agent`, outside T3) when its work overlaps other writers' files or it runs long. Never a hand-made `git worktree add`, and never a custom `WorktreeCreate` hook: it turns off Claude's automatic removal of clean subagent worktrees.
   - A T3 child's worktree is under `~/.t3/worktrees/`, and `t3_thread_read` shows its `worktreePath`. Claude removes a native writer's worktree by itself when the writer changed nothing. Any worktree with changes stays until the lead lands or drops it:
     - The writer commits its work on the worktree's own branch when it is done, and never pushes.
     - Before its final reply, the writer checks for ignored files and flag-hidden edits, and copies out anything the lead needs (charter line, `references/writers.md` §1). Claude may remove a native writer's worktree before the lead reads that reply.
     - The lead lands it with Git's own commands, following `references/writers.md` §5:
       - see everything the worktree holds (`git status --short --ignored`, `git ls-files -v`);
       - note the main checkout's HEAD and the writer's tip as literal SHAs (a shell variable does not survive between Bash calls);
       - `swctl wt check <branch>`, and move aside every untracked file it lists;
       - land exactly the range it prints: `git cherry-pick <base>..<tip>`, or `git merge --squash` and `git commit`;
       - review `git diff <noted HEAD>`;
       - then `git worktree remove <path>`;
       - delete the branch only once its content is confirmed in HEAD, and at the tip you checked (`git update-ref -d refs/heads/<branch> <tip>`).
     - `git worktree remove` deletes ignored files, and edits hidden by index flags, without asking. Git's cleanliness check does not see either (measured on 2.56.0).
     - `swctl wt list` shows every worktree: Git's lock state, the writer's state where known, changed files, commits ahead, idle time.
   - At the lead's turn end, the Stop hook refuses the stop once while one of its own finished native writers' worktrees still holds work that was neither landed nor dropped. Land a T3 child's worktree when its completion arrives.
   - **In a shared checkout:**
     - Each writer gets its own files, and the lead orders units that must touch the same file.
     - Broad commands (formatters, dependency installs, code generators, migrations) run one at a time, on the lead's word.
     - Never `git stash` and never `git add -A`.
   - In the shared checkout, writers neither commit nor push, and the lead commits by path. In its own worktree, a writer commits on that worktree's branch and never pushes.
   - A pristine copy of a commit is a read-only export: `git archive <sha> | tar -x -C <scratch dir>`.
7. **Claude and Codex both write** (owner, 2026-11-01). The scenario picks the writer:
   - Claude for small changes, review-fix rounds and edits it already holds the context for;
   - Codex for large units, deep native or Codex-internals work, or work running in parallel with the lead.

   The lead charters each unit, reads the diff, re-runs every check for real, gets one blind Codex review and commits by path. "Codex says green" never closes a task.
8. **Codex in every phase** (research, planning and review), **dispatched blind**: the evidence and the question, never your diagnosis.
   - Your own view goes in `caller_hypothesis`, which comes back CONFIRMED, REFUTED or UNPROVEN.
   - Wait for Codex. A long call returns as a notification.
   - On disagreement Codex carries, unless measuring the deployed system disproves it (`references/advisory.md`).
9. **Demand live sources.** A version, API, CVE or best-practice claim without a URL is a guess; re-ask.
10. **Never be conservative.** Evaluate from first principles, for performance, quality and adaptability. Quality is shown by real runs and their results.
11. **"Present" is not "supported".** Probe a swappable backend in the mode we run it; never trust the type stub (`references/capability-law.md`).
12. **No test cases unless the user explicitly asks** (owner, 2026-10-18). Verify by running the real thing and recording what was observed.

## Codex in brief

- **Tools:** `mcp__plugin_software-workflows_codex-oracle__<tool>`.
  - `research`, `architect_review`, `code_review` and `codex_query` advise.
  - `codex_runs`, `codex_run_log`, `codex_cancel_run` and `codex_resume_run` watch, stop and continue those runs.
- **Reviews continue their own session** (`session="continue"`, the default). Follow up in it with a short note of what changed. The result's second line names the session and turn.
- **`infra: true`** when live state matters (SSH, a live database, logs). State the access pattern when you know it.
- **A ⚠️ ANCHORING WARNING** means a scoping field carried your conclusion. Re-dispatch blind before trusting agreement.
- **Setup:** if the Codex tools are missing, run `/software-workflows:setup`.

## Cleanup that runs by itself

The owner's rule ("stop what you start") is enforced by hooks and systemd, not by memory (owner, 2026-11-21: "hooks or whatever is best and can act as a rule rather than just suggestion which LLM model can forget").

| When | What happens |
|------|--------------|
| A Bash command returns (Claude: lead and subagents; Codex: every writer) | Every process the command left running is stopped, with its children, and the agent is told what was stopped. Claude tags each call (SW_CALL); Codex tags each command itself (CODEX_TOOL_CALL_ID). |
| A subagent finishes, or the lead's turn ends | Leftovers of finished calls are stopped. At turn end the stop is refused once while the session's own finished native writers' worktrees hold unlanded work. No worktree is ever deleted automatically. |
| A session ends | A detached sweep stops what the session left behind. |
| Every 10 minutes (`sw-sweep.timer`) | Leftovers of ended sessions and stale calls are stopped, `swctl run` environment files that no unit uses are removed, and the Codex hooks' trust is checked. Worktrees are never touched. |

**To keep something running** (the shared test copy, a server for a walkthrough), give it a unit with a deadline:

```
swctl run --name <name> --ttl 2h [--mem 8G] [--cpu 400%] [--compose <project>] -- <command…>
swctl run list | swctl run logs <name> | swctl run stop <name>
```

It runs as a systemd user service in `sw.slice`, which caps memory and CPU across all such services. It stops by itself at the TTL, everything it started included, and `--compose` takes its Compose project down too.

**Never stopped:**
- a process given its own systemd unit (`swctl run`, `systemd-run`);
- a process matched by a line in the janitor's keep file, `~/.config/janitor/keep`;
- the parents and children of either of those, since a parent such as `timeout` passes its signal on, and a kept supervisor keeps its workers.

**Not covered:**
- Docker containers, which belong to dockerd. They go through `swctl run --compose` or Platform's run-guard.
- Anything that clears its environment (`env -i`, `sudo`).
- Calls in a Claude session that does not run with bypassPermissions. The tag is added only in that mode, where no permission rule reads the command text.

## Verify and record

- Read each writer's changes by its paths:
  - in the shared checkout: `git diff HEAD -- <paths>`, plus `git status --short -- <paths>` for new files;
  - for a landed worktree writer: `git diff <noted HEAD> -- <paths>`, with the literal SHA noted before the first landing. Its commits are already in HEAD, so `git diff HEAD` shows none of them.
- Re-run the checks for real.
- Get one blind `code_review` of the final change. Name landed commits as the literal range `<noted HEAD>..HEAD`, plus any later edits and the contents of new files. Fix CRITICAL and HIGH findings: steer the writer, or fix small ones yourself.
- Commit by path, with the observed results in the message. Only Claude pushes, through the push gate, and only when the user says so.
