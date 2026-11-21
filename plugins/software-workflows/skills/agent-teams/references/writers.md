# Writers: start, steer, stop, verify

Read this before you start a writer, steer one, or run several writers at once.

**Every writer, Claude or Codex, is a T3 child task** (`delegate_task`). The owner sees each one in T3's Lineage panel, can open its thread, follow it live and stop it (owner, 2026-11-21: "current background doesn't offer me visibilty as t3 with this"). A child also survives the lead's restarts.

A native background subagent (`Agent`) is a writer only in a plain Claude Code terminal, where T3 is not running.

A writer works in the project's own checkout, or in a worktree of its own (section 5).

### 1. Charter the unit

State the property and its acceptance: what must be true afterwards, and which real runs show it.

- Mark mechanism hints as beatable.
- Give access paths (a key-based SSH host, a `docker exec` target, a local database), never credentials.
- Put hard boundaries in plain words: files not to touch, APIs to keep.

Every charter also carries these lines:

- **Your files:** `<paths>`. Edit nothing else; if you must, ask the lead first.
- **Commits:** in the shared checkout, don't commit or push; the lead commits by path. In a worktree of your own, commit your work on its branch when you are done, and never push. (Codex's shell refuses `git push` on this machine anyway.)
- **Worktree writers, before your final reply:**
  - Run `git status --short --ignored` and `git ls-files -v | grep -v '^H '`.
  - Commit every source change that should land.
  - Clear any assume-unchanged or skip-worktree flag you set.
  - Copy anything else you made that the lead needs, such as an ignored report or data file, to `<folder the lead names>`, and say where.

  Claude removes a native writer's worktree by itself when plain `git status` shows nothing and no commit is ahead. That check does not see ignored files or flag-hidden edits, and it can run before the lead reads your reply.
- **Start nothing** (owner, 2026-11-13): no servers, containers or databases. Quick checks that end by themselves (type check, lint, build) are fine. Put what must run, and how to see it, under WHAT TO RUN in your report: the lead tests. (Enforced: whatever a command leaves running is stopped when the command returns.)
- **No test cases** unless the user asked for them.
- **Steering:** messages headed LEAD STEER add information. Your task and every earlier instruction still stand. If a steer seems to conflict with an earlier instruction, keep the earlier one and tell the lead before acting on that part.

### 2. Start it

```
delegate_task(
    title="<unit>",                  # what the owner sees in the Lineage panel: make it say what the writer does
    task="<the charter>", role="implementation",
    isolation="shared", mode="async",          # or isolation="worktree" (section 5)
    target=<a target below>,
)
```

| Writer | `target` |
|--------|----------|
| Claude | `{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "max"}}` |
| Codex | `{"providerInstanceId": "codex", "model": "gpt-6.2-astra", "options": {"reasoningEffort": "max"}}` |

- Call `orchestrator_capabilities` once per thread before your first `delegate_task`, and keep each `taskId` and `childThreadId`.
- **Outside T3** (a plain terminal), start a Claude writer natively: `Agent(description=…, name=…, subagent_type="general-purpose", model="opus", prompt=<charter>)`. It runs in the background by default; add `isolation="worktree"` for a checkout of its own.

Then wait. A T3 child's completion wakes your thread, and a native writer's arrives as a notification. Don't poll. To read a T3 child's progress, use `t3_thread_read` once in a while. Never read a native subagent's output file with the shell: it is the whole JSONL transcript.

A writer's question waits in its thread. List it with `t3_pending_request(action="list", threadId=<child>)`, read it, and answer with `respond`.

### 3. Steer it: soft or hard

A steer **adds information**. It never cancels the writer's task or its earlier instructions. If a steer seems to conflict with one, the writer keeps the earlier instruction and tells the lead before acting on that part. To change an instruction, answer the writer's question, or stop the task and start a new one with a new charter.

Head the steer with its mode:

```
LEAD STEER (soft): additional information for your current task. Your task and every earlier
instruction still stand. If this seems to conflict with an earlier instruction, keep the earlier one
and tell me before acting on that part.
<the information>
```

```
LEAD STEER (hard): I stopped your current step to give you this now. Your task and every earlier
instruction still stand. If this seems to conflict with an earlier instruction, keep the earlier one
and tell me before acting on that part.
<the information; say whether to redo the step that was stopped>
```

| Mode | T3 child (every writer) | Native writer (outside T3) |
|------|-------------------------|----------------------------|
| **Soft:** nothing running is stopped; the writer takes the message in at its next step | `t3_thread_send(threadId=<child>, mode="steer", message=…)` | `SendMessage(to=<name>, message=…)` |
| **Hard:** the current step stops, then the writer takes the message in with its whole history | `t3_thread_send(threadId=<child>, mode="restart", message=…)` | `TaskStop(task_id=<name>)`; once the stop notice arrives, `SendMessage(to=<name>, message=…)` |

- **Soft steer and a long command.** When the steer must not reach the writer before its current command finishes, wait for that command to finish, then send it.
- **A writer that has finished.** A T3 child needs `t3_thread_send(mode="auto")`, because `steer` needs an active turn. `SendMessage` to a finished native writer resumes it with its history.
- **A writer the user stopped** with `x` in `/tasks`, or with an SDK stop, does not auto-resume: a message to it is refused.
- `mode="queue"` on T3 is not a steer. It runs as a new turn after the current one ends.

**Measured on nucleus.**

On 2026-11-21, Claude Code 2.1.329 inside a T3 0.1.10 session, with a native writer running `python3 -c 'import time; time.sleep(41)'` in the foreground four times:

- **Soft (`SendMessage`):** the running command kept going. The next round started only after it finished.
- **Hard (`TaskStop`):** the running command was gone at once. A `SendMessage` after the stop notice resumed the writer, which started the stopped round again.

On 2026-11-20, T3 0.1.10 with Codex 0.170.0, gpt-6.2-astra and claude-opus-5-5:

- **Soft (`steer`):** the running command finished, then the writer followed the steer and carried on with its task. This included a Claude child running a foreground command.
- **Hard (`restart`):** the running command was gone within a second, for Codex and Claude children alike. The writer redid the step, added what the steer asked, and carried on.
- **Claude long commands:** Claude Code refuses a standalone foreground `sleep` and runs long commands as background tasks. A Claude writer waiting on one takes a soft steer in at once, while the command keeps running.

### 4. Stop it

- **T3:** `task_cancel(taskId)`. It stopped the running command and unloaded the thread's tool servers (measured 2026-11-20). Never call `t3_thread_interrupt` on a child. The owner can also stop one from the Lineage panel.
- **Native (outside T3):** `TaskStop(task_id=<name>)`. It stopped the writer and its running command at once (measured 2026-11-21).

### 5. Several writers: shared checkout or worktrees

Up to 5–10 writers may run at once (owner, 2026-11-21). The project's one shared test copy is the lead's: it tests each writer's work first in, first out.

**Shared checkout** (`isolation="shared"`, or no isolation on `Agent`): the default for units on separate files.
- Give each writer its own files. When two units must touch the same file, order them: the second starts when the first is done, or you soft-steer one to wait.
- Broad commands (formatters, dependency installs, code generators, migrations) run one at a time, and only when you say so.
- Read each writer's changes by its own paths:
  - tracked files: `git diff HEAD -- <paths>`;
  - new files: `git status --short -- <paths>`, then open each untracked file it lists.
- Never `git stash` and never `git add -A` in a shared checkout.

**Worktree** (`isolation="worktree"` on `Agent` or `delegate_task`): for units whose files overlap other writers', large refactors, and long runs.
- Native: Claude makes `<repo>/.claude/worktrees/agent-<id>` on branch `worktree-agent-<id>` and locks it while the writer runs. It removes the worktree itself when the writer changed nothing (measured 2026-11-21). A worktree with changes stays.
- T3: the child gets a prepared checkout under `~/.t3/worktrees/`. `t3_thread_read` shows it as the thread's `worktreePath`; land it when the child's completion arrives.
- A worktree starts from the last commit: uncommitted edits in the main checkout are not in it. `.worktreeinclude` lists gitignored files (such as `.env`) that Claude copies in.
- When the writer is done, it has committed its work on the worktree's branch (its charter says so). Before you touch the worktree, confirm the writer has finished: a T3 child's completion, or a native writer's notification. Then land it with Git's own commands, in this order. Every warning below was measured on Git 2.56.0 (2026-11-21).
  1. **See everything the worktree holds.** In the worktree, run `git status --short --ignored` and `git ls-files -v | grep -v '^H '`. Plain `git status` hides two things: ignored files, and edits to files flagged assume-unchanged (`h`) or skip-worktree (`S`). Have the writer commit what should land. Its charter has it do this before its final reply too, since Claude may remove a native writer's worktree first (above).
  2. **Note two SHAs, as literal text.** A shell variable does not survive from one Bash call to the next.
     - The main checkout's `git rev-parse HEAD`: note it once, before the first landing of this batch, so the one final review covers every landing.
     - The writer's tip, `git rev-parse <branch>`.
  3. **Protect the main checkout's untracked files.** In the main checkout, run `swctl wt check <branch>` (add `--base <sha>` when the writer started from somewhere other than the fork point). It lists the untracked files here that landing would overwrite or delete, such as an `.env`, and prints the exact range to land.
     - It counts every commit being picked, not only the end result: a path one commit adds and a later one removes is written, then deleted.
     - File-versus-folder clashes count too.
     - It lists every untracked file, not only ignored ones, because a picked commit can change `.gitignore` before the next one writes.
     - Move each listed file aside first. A cherry-pick, or a squash merge that is not a fast-forward, overwrites an ignored file without asking. `--no-overwrite-ignore` guards only a fast-forward.
     - Not covered: when the main checkout renamed a folder the writer added files to, Git can place those files at the new folder (directory rename detection). Look at where the files land before you remove anything.
  4. **Land exactly the range it printed.** Either `git cherry-pick <base>..<tip>`, or `git merge --squash --no-overwrite-ignore <tip>` and then `git commit`, because a squash only stages the result.
  5. **Review what landed: `git diff <noted HEAD>`**, with the SHA itself. It shows the landed commits plus any later edits. `git diff HEAD` shows nothing a cherry-pick committed, and `git diff $pre` in a new shell is a plain `git diff`. The blind review gets the same: the commit range `<noted HEAD>..HEAD` as literal SHAs, later edits, and the contents of new files.
  6. **Remove the worktree.** `git worktree remove <path>` deletes ignored files and edits hidden by index flags without asking. Run it only after step 1 shows nothing left that you want.
  7. **Delete the branch only after checking its content is in HEAD.** Compare the noted writer tip with HEAD over the writer's paths: `git diff <writer tip> HEAD -- <its paths>`. That diff should show only changes made after landing.
     - If the comparison is unclear, keep the branch. A branch costs nothing.
     - `git cherry` is no proof. It matches patches by their text, so it shows `-` for a change that was later reverted, and for patches that differ only in indentation.
     - Neither is `git branch -d` succeeding: it compares with the branch's upstream when one is set. It refuses after a cherry-pick or a squash anyway.
     - Delete it at the tip you checked: `git update-ref -d refs/heads/<branch> <writer tip>`. It refuses if the branch moved since, so a commit that arrived after your check keeps its branch. `git branch -D` would delete whatever the branch names by then.
  - To throw a worktree away: step 1 first, then `git worktree remove --force <path>`.
  - `swctl wt list` shows every linked worktree: Git's lock state, the writer's state where it is known, changed, ignored and flag-hidden files, commits ahead, idle time. An unlocked worktree can still have a writer at work: T3 releases its creation lock before the writer starts.
- At your turn end, the Stop hook refuses the stop once while one of your finished native writers' worktrees holds work that was neither landed nor dropped. Nothing in the cleanup deletes a worktree: Claude removes a writer's worktree itself when the writer changed nothing, and every other removal is yours, with `git worktree remove`.
- Never `git worktree add` by hand, and never a custom `WorktreeCreate` hook (it turns off Claude's automatic removal).

### 6. Verify and record

- **Land a worktree writer's commits first** (§5, steps 1–5), so its changes sit in the main checkout like any other writer's. Read them as `git diff <noted HEAD> -- <paths>`, with the SHA you noted before the first landing.
- **Read the diff, and re-run the checks yourself for real:** the build, the linters, and the changed app, command or endpoint. Record what you saw.
- **To keep a server or a test stack running across calls**, start it with `swctl run --name <name> --ttl <2h> -- <command>`; otherwise it is stopped when the command that started it returns.
- **Get one blind `code_review` of the final change.** For landed work, name the range with literal SHAs: `<noted HEAD>..HEAD`, plus any edits after them and the contents of new files. A review that looks only at uncommitted changes sees none of a landed writer's work. Its HEAD also makes those commits count as reviewed history at the push gate. Continue the review session for follow-ups.
- **Fix CRITICAL and HIGH findings.** Send them to the same writer (`t3_thread_send(mode="auto")` for a finished T3 child, `SendMessage` for a native one), or fix small ones yourself.
- **Commit by path** (`git add <paths>`, never `add -A`), with the observed results in the message. Only Claude pushes, through the push gate.

### Reviews and other Codex Oracle runs

`code_review`, `architect_review`, `research` and `codex_query` run in the lead's own session. Watch them with `codex_runs` and `codex_run_log`, and steer them like writers:

- **Soft:** after the run answers, continue it with `codex_resume_run(run, nudge=…)` or the tool's own `session="continue"`.
- **Hard:** `codex_cancel_run(run)`, then `codex_resume_run(run, nudge=…)` on the same thread. Its reasoning and reads so far are kept.
- **On Windows,** `codex_resume_run` cannot continue a run, so a hard steer there only stops it.

The server heads every delivered nudge with the same rule: it adds information, and the original question and earlier instructions stand.

### Cost

- **A T3 Codex writer** starts its own copy of every MCP server in `~/.codex/config.toml`: about 20 processes and 1.2 GB on nucleus (measured 2026-11-20).
- **A T3 Claude writer** is a whole separate Claude Code session, about 370 MB plus its MCP servers (measured 2026-11-21).
- **A native writer** (outside T3) runs inside the lead's session, with the session's tools.
- **A review** starts no MCP servers unless `infra: true`.
- **A worktree** costs disk: a full checkout, plus whatever its writer installs (`node_modules`). Landing or dropping it gives the space back.
