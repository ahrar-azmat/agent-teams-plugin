# Agent Teams — writers: start, steer, stop

Read this before you start a writer, steer one, or run several writers in one checkout.

A writer is a **T3 child task** that the lead starts, steers and stops. Codex writers use provider
`codex`, and Claude writers use provider `claudeAgent`. Both work in the project's own checkout:
`isolation: "shared"`, never a worktree (SKILL.md rule 9). Call `orchestrator_capabilities` once per
thread before your first `delegate_task`; it lists the providers, models and effort options.
Writers run at effort `max`.

### 1. Charter the unit

State the property and its acceptance: what must be true afterwards, and which real runs show it.
- Mark mechanism hints as beatable.
- Give access paths (a key-based SSH host, a `docker exec` target, a local database), never
  credentials.
- Put hard boundaries in plain words: files not to touch, APIs to keep.

Every charter also carries these lines:

- **Your files:** `<paths>`. Edit nothing else; if you must, ask the lead first.
- **Don't commit or push.** The lead commits by path. (Codex's shell refuses `git push` on this
  machine anyway.)
- **Start nothing** (owner, 2026-11-13): no servers, containers or databases. Put what must run,
  and how to see it, under WHAT TO RUN in your report.
- **No test cases** unless the user asked for them (SKILL.md rule 10).
- **Steering:** messages headed LEAD STEER add information. Your task and every earlier instruction
  still stand. If a steer seems to conflict with an earlier instruction, keep the earlier one and tell
  the lead before acting on that part.

### 2. Start it

```
delegate_task(
    title="<unit>",
    task="<the charter>",
    role="implementation",
    isolation="shared",
    mode="async",
    target={"providerInstanceId": "codex", "model": "<from orchestrator_capabilities>",
            "options": {"reasoningEffort": "max"}},
)
```

- For a Claude writer, use `{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5",
  "options": {"effort": "max"}}`.
- Keep the `taskId` and `childThreadId`. The child's completion wakes your thread, so don't poll
  it. To see its progress, read its transcript with `t3_thread_read`.

### 3. Steer it: soft or hard

A steer **adds information**. It never cancels the writer's task or its earlier instructions. If a
steer seems to conflict with one, the writer keeps the earlier instruction and tells the lead before
acting on that part. To change an instruction, answer the writer's question, or cancel the task and
start a new one with a new charter. Say this in every steer, and head the steer with its mode:

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

| Mode | What happens | Call |
|---|---|---|
| **Soft** | Nothing running is stopped. A Codex writer takes the message in after the step it is on. A Claude writer waiting on a background command takes it in at once, while the command keeps running. | `t3_thread_send(threadId=<child>, mode="steer", message=…)` |
| **Hard** | The step it is on stops at once, then the writer takes the message in with its whole history. | `t3_thread_send(threadId=<child>, mode="restart", message=…)` |

- Use **soft** for context, corrections and additions that can wait for the current command.
- Use **hard** when the current step is wrong or wasteful: a long build against the wrong target, a
  command touching the wrong files.
- A steer needs an active turn. A writer that has finished is idle: send it follow-up work, such as
  review findings, with `t3_thread_send(mode="auto")`. That starts a new turn in the same thread, with
  its history.
- `mode="queue"` is not a steer. It runs as a new turn after the current one ends.

Measured on 2026-11-20 (T3 0.1.10, Codex 0.170.0, gpt-6.2-astra and claude-opus-5-5):
- **Soft:** a steer sent during a `sleep` left the command running. The writer finished it, then
  followed the steer and carried on with its remaining rounds.
- **Hard:** the running command was gone within 1 s, for Codex and Claude children alike. The writer
  redid the stopped round, added what the steer asked, and carried on.
- **Claude writers and long commands:** Claude Code refuses a standalone foreground `sleep` and runs
  long commands as background tasks.
  - A Claude writer that is waiting on one takes a soft steer in at once, while the background command
    keeps running.
  - When the steer must not reach the writer before that command finishes, wait for the command to
    finish, then send the steer. Asking the writer in the steer to wait does not delay delivery.
  - A hard steer stopped the background command too.

### 4. Stop it

`task_cancel(taskId)` stops the child's running and queued turns. It stopped the running command
within the cancel and unloaded the thread's tool servers (measured 2026-11-20). Never call
`t3_thread_interrupt` on a child.

### 5. Several writers in one checkout

- At most 2–3 writers run at once per project (owner, 2026-11-13). The project's one shared test
  copy goes to one writer at a time, first in, first out, and only the lead starts it.
- Give each writer its own files. When two units must touch the same file, order them: the second
  starts when the first is done, or you soft-steer one to wait.
- Broad commands (formatters, dependency installs, code generators, migrations) run one at a time,
  and only when you say so.
- Read each writer's changes by its own paths:
  - tracked files: `git diff HEAD -- <paths>`;
  - new files: `git status --short -- <paths>`, then open each untracked file it lists.
- Never `git stash` and never `git add -A` in a shared checkout.

### 6. Verify and record

- **Read the diff, and re-run the checks yourself for real:** the build, the linters, and the changed
  app, command or endpoint. Record what you saw.
- **Get one blind `code_review` of the final change.** Continue the review session for follow-ups.
- **Fix CRITICAL and HIGH findings.** Send them to the same writer as a new turn
  (`t3_thread_send(mode="auto")`; a finished writer is idle), or fix small ones yourself.
- **Commit by path** (`git add <paths>`, never `add -A`), with the observed results in the message.
  Only Claude pushes, through the push gate.

### Reviews and other Codex Oracle runs

`code_review`, `architect_review`, `research` and `codex_query` run in the lead's own session. Watch
them with `codex_runs` and `codex_run_log`. Steer them like writers:
- **Soft:** after the run answers, continue it with the added information, using
  `codex_resume_run(run, nudge=…)` or the tool's own `session="continue"`.
- **Hard:** `codex_cancel_run(run)`, then `codex_resume_run(run, nudge=…)` on the same thread. Its
  reasoning and reads so far are kept.

The server heads every nudge with the same rule: it adds information, and the original question and
earlier instructions stand.

### Cost

Each Codex writer starts its own copy of every MCP server in `~/.codex/config.toml`: about 20
processes and 1.2 GB on nucleus (measured 2026-11-20). A review starts none unless `infra: true`.
