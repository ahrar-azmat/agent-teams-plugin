# agent-teams-plugin

A Claude Code plugin marketplace, **`agent-teams`**, with one plugin, **`software-workflows`** (2.0.0).
It adds three things to Claude Code:

- **Agent teams that work.** A skill that runs multi-agent work through Claude Code's
  [Agent Teams](https://code.claude.com/docs/en/agent-teams), with every tool signature pinned so
  runs don't fail on malformed calls.
- **Codex as the second model.** The bundled **Codex Oracle** MCP server puts OpenAI Codex, at
  maximum reasoning effort with live web search, beside Claude. It acts as a blind reviewer,
  architect and researcher, and, through `abraham`, as a writer that edits your real checkout with
  full access.
- **Guards.** Hooks that ask for a Codex architecture review before plan mode, and that stop every
  `git commit` and `git push` Claude runs until the review state has been read.

Marketplace `agent-teams` · plugin `software-workflows` ·
[PolyForm Noncommercial 1.0.0](./LICENSE)

## Quick start

In Claude Code:

```text
/plugin marketplace add ahrar-azmat/agent-teams-plugin
/plugin install software-workflows@agent-teams
/software-workflows:setup
```

Setup checks everything the plugin needs and prints a table of what is ready. It fixes what it can
and gives you the exact command for the rest:

| Item | What setup does |
|---|---|
| agent teams | Asks you, then adds the one switch a plugin cannot set: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, in your own settings. Nothing else in the file changes. |
| Codex CLI | Checks `codex` is installed (`npm i -g @openai/codex`). |
| codex login | Checks you are logged in; if not, type `! codex login` in the prompt. |
| Python 3.11+ | Finds one for the Codex Oracle server. |
| server environment | Builds the server's venv in the plugin's data directory (about 10 s). |
| codex-oracle registration | Finds a codex-oracle server you once added by hand (the 1.x way) and prints the `claude mcp remove` command for it. Two registrations give two tool sets. |
| git | Checks git is on PATH. |

Agent teams start in your next Claude Code session. After that, just ask, for example *"Create an
agent team to review this PR from security, performance and correctness angles"*, or *"Have Codex
review this diff"*. A session that starts with something missing says so and points you to setup;
nobody has to edit `settings.json` by hand.

### Requirements

- **A recent Claude Code.** Tested on 2.1.302. The skill needs at least 2.1.178 (agent teams without
  `TeamCreate`/`TeamDelete`), and moving a 1.x install over needs 2.1.193 or newer (the marketplace's
  `renames` entry).
- **Codex CLI, logged in.** Tested with codex-cli 0.163.0. Codex uses the model set in your
  `~/.codex/config.toml` (gpt-6-astra by default) at reasoning effort `max`. The server pins the
  effort, so the Codex app's slider cannot lower it.
- **Python.** The Codex Oracle server needs Python 3.11 or newer, which setup finds for you. The
  hooks run with whatever `python3` is on PATH, 3.9 or newer (the one macOS ships is fine).
- **git** with the CVE-2024-32465 fix: 2.39.4, 2.40.2, 2.41.1, 2.42.2, 2.43.4, 2.44.1, 2.45.1, or
  anything from 2.46 on. The push gate refuses older gits.

## Updating

**Once:** turn on auto-update. Open `/plugin` → **Marketplaces** → `agent-teams` and enable
auto-update. Claude Code then fetches new versions when it starts.

**By hand:**

```text
claude plugin marketplace update agent-teams
claude plugin update software-workflows@agent-teams
```

Then **restart Claude Code**: `/reload-plugins` does not restart a running MCP server. Run
`/software-workflows:setup` once after any update; it says what the new version needs.

**Coming from 1.x**, where codex-oracle and software-workflows were two plugins: whichever of them
you had, run these once in a terminal, then restart Claude Code and run `/software-workflows:setup`:

```text
claude plugin marketplace update agent-teams
claude plugin install software-workflows@agent-teams
claude plugin update software-workflows@agent-teams
```

The marketplace's `renames` entry retires `codex-oracle` at the next start, but it neither installs
nor updates `software-workflows` (measured on Claude Code 2.1.302). A machine that had only
codex-oracle is left with no plugin until `install` runs. One that had software-workflows 1.x stays
on 1.x until `update` runs. Each command does nothing where it is not needed. The Codex tools are now
named `mcp__plugin_software-workflows_codex-oracle__<tool>`.

## What's in the plugin

| Part | What it does |
|---|---|
| Skill `agent-teams` | Runs every multi-agent task: spawns named teammates, wires tasks and dependencies, messages, shuts the team down. Includes role templates (researcher, architect, implementer, reviewer, verifier), team patterns (research, implementation, debug, review) and a troubleshooting table that maps each tool error to its fix. |
| Skill `codex-planning` | Before a feature or non-trivial change: consult Codex independently before building. |
| Skill `codex-review` | Before committing or pushing: Codex reviews the change independently. |
| Agent `code-reviewer` | Dispatches a blind Codex review, verifies its findings and writes one report. |
| `/software-workflows:setup` | The setup above. |
| `/software-workflows:abraham` | Hands a task to Codex as the writer (see below). |
| `/software-workflows:office` | Your Codex runs as a live pixel-art office in the browser, where you can read and steer each one (see [The Codex Office](#the-codex-office)). |
| MCP server `codex-oracle` | The Codex tools below. The plugin registers it itself (`.mcp.json`); do not add a second `mcpServers` entry for it. |
| Hooks | The plan gate, the push gate and its git shim, the session-start check, and a live-view nudge. |

### Pinned tool signatures

Agent Teams tools are deferred and schema-checked (`additionalProperties: false`), and the usual way
orchestration fails is a parameter they don't accept. The skill pins them:

- `TeamCreate` and `TeamDelete` are gone. The team forms with the first `Agent` call, and
  `team_name` is ignored.
- `TaskCreate` has no `blockedBy`. Create the task, then `TaskUpdate(taskId, addBlockedBy=[…])`.
- `SendMessage` is `to` + `message` (+ optional `summary`). There is no `recipient`, `type` or
  `content`, and no broadcast.
- Deferred tools load per context, so every teammate prompt loads them first.

Optional: with the [agent-reach](https://github.com/Panniantong/agent-reach) CLI installed, the
skill uses it to fetch what web search can't read, such as subtitles, RSS and semantic-search
indexes. The fetched content goes to Codex as cited context, never as a network connection of its own.

## The Codex tools

| Tool | Use it for |
|---|---|
| `architect_review` | Judge a design or plan before anything is built. |
| `code_review` | Review a change before it is committed or pushed. |
| `research` | Technical research from live sources, with URLs. |
| `codex_query` | Anything else you want Codex's reasoning on. |
| `abraham` | Codex implements a task in a git checkout (see below). |
| `codex_runs`, `codex_run_log`, `codex_cancel_run`, `codex_resume_run` | See, watch, stop and collect runs. `codex_resume_run` continues a run's thread with a nudge, and with `images` too. |
| `coordinator_claims`, `coordinator_send`, `coordinator_inbox`, `coordinator_unit`, `coordinator_seal`, `coordinator_publish` | Several writers in one checkout: claims, messages, a unit's state and content of unknown origin, and publishing a unit's work (see below). |

**Dispatch blind.** A second opinion counts only if it was formed independently. So send Codex the
evidence and the question, never your diagnosis. Put your own view in `caller_hypothesis`. Codex
treats it as a claim to refute and answers CONFIRMED, REFUTED or UNPROVEN, naming the evidence that
decided it. The scoping fields (`context`, `concerns`, `focus`, `topic`) are linted for conclusion
language such as "the root cause is" or "I fixed". A hit adds a counter-anchoring instruction and a
loud `⚠️ ANCHORING WARNING` on the answer. Every review also has a "where I disagree with the
caller's framing" section. The advisor agreeing with you is strong evidence only if it was
dispatched blind; disagreement is strong evidence either way.

**Live web.** Every call runs Codex with `web_search=live`; Codex's own default is a cached
snapshot. Answers must cite primary sources with URLs and mark unverifiable load-bearing claims
`UNVERIFIED`. Code reviews also check APIs against current upstream docs and touched dependencies
for known CVEs.

**Reviews continue their session.** A follow-up `code_review` or `architect_review` is the next turn
of the workspace's latest review session (`codex exec resume`). The reviewer starts from its earlier
findings and says which are fixed, which still stand and what is new. The answer's second line names
the session: thread id, turn, tokens, context size and compactions. Use `session="new"` for a fresh,
independent opinion, or a thread id to continue a particular one. `research` and `codex_query` start
fresh unless you pass `session="continue"` or an id.

**Long runs survive.** Codex runs at max effort, so a call often takes many minutes. Claude Code then
moves it to the background and it returns as a task notification; that is normal, so wait for it.
A run lives on a file-backed spool, so `/mcp` → Reconnect or a server restart detaches it instead
of killing it, and `codex_resume_run` collects the answer at no model cost. `codex_runs` shows every
run (RUNNING, DETACHED, ok, error, cancelled, timeout), and `codex_run_log` shows its live log in
the conversation. To watch runs as they happen, ask Claude to "run mcp-live in the background", or
`tail -F ~/.claude/logs/codex-oracle/stream.log` in a terminal. A hook reminds Claude to start that
view at a session's first Codex call.

**Run budget.** A run is stopped at its wall-clock budget: 3 hours by default (healthy runs take 4–30
minutes). Set `CODEX_ORACLE_MAX_RUNTIME_S` (300 to 12600 seconds) in the environment Claude Code
starts with. Retries and abraham's two phases share one budget. The live log warns at 80 %, and a
stopped run tells you to `codex_resume_run` its thread instead of asking again.

## Codex as the writer: abraham

`abraham` (or `/software-workflows:abraham <task>`) runs two Codex phases in one call. Both have
**full access**: no sandbox, network, live web search, your MCP servers and your live
infrastructure.

1. **Analysis.** Codex studies the code, the live web and your infrastructure (read-only by
   instruction) and writes an implementation brief.
2. **Implementation.** Codex implements the brief with
   `--dangerously-bypass-approvals-and-sandbox`, runs the project's real checks (migrations, test
   suites, browsers) and reports their real exit codes.

What it guarantees:

- Full access is always on. `full_access`, `infra` or `web_search` set to `false` is refused.
- The target (`cwd`) may be any git repository on the machine. The run is listed and resumable from
  the folder you dispatched it from and from the target.
- A dirty tree is refused unless you pass `allow_dirty=true`. Uncommitted edits Codex touches have no
  undo.
- Several writers can share a checkout, each on its own **claims** (next section). A run on the
  fallback `codex exec` backend still holds its checkout alone, across both phases.
- Codex never commits or pushes. Every run ends with a **CHANGED FILES** report, compared by
  content, inside submodules and across every index stage. The report also gives the mode Codex
  recorded for each turn. Review the diff, then record it yourself.

How the team works with it:

- Codex writes the code through abraham, in the repository's real checkout, never in a copy or a
  git worktree.
- Claude plans, coordinates, reviews and records.
- Put the access paths you know in `context`: a key-based SSH host, a `docker exec` target, a local
  test database. Never put credentials there; Codex finds its own way in from the project's docs and
  your configuration.
- Run parallel writers in one checkout on disjoint claims, or in different repositories.

Full access means full access. Processes a run leaves behind are swept cooperatively, but one that
deliberately escapes keeps your user's rights. That is part of what full access accepts.

## Several writers in one checkout: the writer coordinator

The first `abraham` call starts the **writer coordinator**, a per-user daemon (state in
`~/.local/state/software-workflows`). It runs each writer as a native Codex thread through
`codex app-server` (Codex 0.163) and keeps a ledger of the checkout: who claimed which paths, which
change each writer made, and what nobody can explain. When native admission fails, `abraham` falls
back to `codex exec` and says so at the top of its answer.

- **Claims.** `abraham(claims=["src/api/", "tests/api/"])` gives the writer those paths, relative to
  the repository root. A claim that overlaps another writer's is refused, naming the holder. Writers
  ask each other, and you, through messages (`coordinator_send`, `coordinator_inbox`).
- **Attribution.** A writer's `apply_patch` edits are recorded exactly. Its commands are held in
  custody: files they may have changed, until the command and anything it left running have exited.
  Whatever nobody can explain is **UNKNOWN**.
- **A writer's tools.** It sees your MCP servers, minus the lead's tools: no `abraham`, no
  `coordinator_*` lead tools. A server of yours that fails to start does not stop a write.
- **Your own edits.** In a checkout the coordinator has registered, Claude's Edit and Write go through
  a hook. They are recorded as yours, and refused on a path another writer claims. A Bash command
  that changes files is recorded as yours when, while it ran, no writer was working, no command was
  held and no other lead Bash command was running in that checkout; otherwise what it changed is
  UNKNOWN. A change made while a writer's run is under way is
  UNKNOWN, and it stops that run's hand-off as blocked. So while writers run, change files with Edit
  and Write.
- **Publishing a unit.** A writer's work is published with `coordinator_seal`, then
  `code_review(sealed_unit=…)`, then `coordinator_publish`, which makes the commit. The push gate
  refuses a plain `git commit` that would take another writer's paths or UNKNOWN content (see Push
  gate).
- **UNKNOWN content.** `coordinator_unit(action="unknown")` lists it; `action="restore"` puts the last
  known content back, and you then make the change again with Edit or Write.
- **Watching.** The Codex Office shows these writers, and Send now puts a note into a running turn.

Known limits, measured on macOS:

- A process that leaves its command's session (`setsid`, a daemon) and whose environment cannot be
  read is not held: it cleared its environment, or it is an Apple system binary, whose environment
  macOS does not show. Its writer's hand-off does not wait for it, and what it writes later is not
  the writer's: it shows as UNKNOWN, or as yours if it lands while one of your Bash commands runs in
  a quiet window (next item). Codex itself ends a command's process group when the command ends, so
  `( … &)` and `nohup` children do not outlive it.
- While your Bash command runs with no writer working, another program writing in the checkout (an
  editor saving a file, a background command still running) is recorded as yours.
- The push gate checks the content in your working tree. When a commit takes a path whose staged
  content differs from its working file (`git add`, then an edit), the staged bytes are not
  checked; the gate says so in a note.

## The Codex Office

`/software-workflows:office` opens a pixel-art office in your browser, at
`http://127.0.0.1:8765/`. Every Codex run on this machine is a worker at a desk, from every
session and project.

- **Live workers** face their monitor and type. A bubble shows what they're doing right now:
  thinking, a command, a web search, a file edit, or waiting for capacity.
- **Finished workers** turn around and wear a badge: ✓ done, ! failed, ⏸ paused, z interrupted.
- **Statuses follow the server's own rules**, the ones `codex_runs` uses, with two additions:
  "Starting" for a run its server has taken but not spawned yet, and "Starting the writer" between
  abraham's analysis and its writer. A run whose cancel never finished stays "Pausing". Once its
  server and its Codex process are both gone, it shows "Cancel unfinished" with a **Retry cancel**
  button, which ends it.
- **One worker per conversation.** A conversation continued over several runs stays one worker. That
  covers a review session's turns, a resumed run, and abraham's analysis followed by its writer.
- **Writers on the writer coordinator show too.** They are not `codex exec` runs, so the office asks
  the coordinator for them and reads each one's conversation from its Codex thread's own rollout
  file. Their header shows the paths they claim, and any UNKNOWN content or unread messages. When the
  coordinator does not answer, they show as last seen, and the page says so.

**Roles.** Each worker's role comes from the first of these that applies:
1. The role you pick from the role menu in its header.
2. A role its caller named with one line in the task or `context`, such as `Role: Product Manager`.
3. The role its Codex tool implies: Builder (abraham writing), Planner (abraham's analysis),
   Reviewer, Architect, Researcher or Analyst.

The other roles are CEO, CTO, Project Manager, Product Manager, Business Analyst, QA Engineer,
Security Engineer, DevOps Engineer, Designer, Data Analyst and Tech Writer. Each has its own look:
the CEO wears a suit and tie, the Project Manager carries a clipboard, the Business Analyst a
tablet. Executives get a gold-trimmed desk. The agent-teams skill tells Claude to name the role when
a run does that role's work.

The office doesn't guess a role from the task's wording. Measured on the real journal, keyword
guesses mislabelled most of the runs they touched.

**Click a worker** to read the whole conversation on the right. It shows Claude's task, Codex's
messages, its reasoning steps, every command with its exit code and output, web searches, file
changes and token use. You can filter what you see.

Tabs:
- **Answer**: the final answer.
- **Prompt**: the exact text Codex received.
- **Runs**: run ids, thread ids and log paths.

**Act on a worker** from the composer under its conversation:

| Control | What it does |
|---|---|
| ⏸ Pause | Stops the run now. Its Codex thread stays on disk. |
| Continue | Picks a stopped or finished thread up again, with your instructions. |
| Interrupt & send | Pauses a live run, then continues the same thread with your note at once. |
| Send now | For a writer on the coordinator: puts your note into the turn it is running, without stopping it (Codex's `turn/steer`). The note is text, so images go by path, for Codex to open itself. |
| Send when done | Delivers your note when the current run finishes. Use this for abraham's planner: interrupting it ends the dispatch before the writer starts. A note sent while abraham hands over to its writer waits for the writer. The office knows the writer is still to come because abraham's call still holds the tree's write lock. Without `lsof` it can't read that, and the note is refused rather than sent to the analysis. Until a note is delivered, Withdraw takes it back. |
| 📎, drop or paste | Attaches images and files. |
| `@` | Picks a file from the worker's workspace. |

Every action goes through this plugin's own `codex_cancel_run`, `codex_resume_run` and, for Send
now, `coordinator_send`, run by its own MCP server. So the run journal, the locks and the run budget
work exactly as when Claude Code calls them. An action's outcome comes from the journal, or for a
writer on the coordinator from the coordinator's state of the run, not from the reply alone:
- A pause is done once the run reads stopped.
- A continuation is done once the run it started has answered. Only a run started by the action's own
  server counts, never another caller's continuation of the same thread.
- A refusal shows as failed, with the server's words.
- A continuation gets as long as Claude Code gives the same call: the plugin's 4 h call timeout.
- Continuing a detached run that had already finished first collects its answer, then delivers your
  note.
- An attachment or `@`-file that's gone by the time you send is refused by name, and your draft stays.

Images are attached to the continuation itself: from 2.2.0, `codex_resume_run` takes `images`, and
Codex sees them as part of the turn. The office asks the server whether it takes them rather than
going by the version. Against an older server, the note gives the images' paths and asks Codex to
open each with its `view_image` tool. Codex's look at an image that way doesn't show in the stream,
because `codex exec` reports no event for it.

A note to a live worker interrupts it, except for a writer on the coordinator: Send now reaches its
running turn instead.

The office and every action it sends run detached from Claude Code. Closing the page, ending the
session or restarting Claude Code stops none of them. Manage the office with these commands:
- `/software-workflows:office`: start it, or find the one already running.
- `/software-workflows:office status`
- `/software-workflows:office stop`

Only you can run the command; Claude cannot start it on its own. The command never puts its argument
into a shell command: Claude reads it and runs one of three fixed command lines, for start, stop or
status. One office runs per data directory: a second start finds the first.

Uploads, the action records and the running office's record live in the plugin's data directory,
under `office/`.

The office listens on 127.0.0.1 only. It refuses API calls without the per-start token its page
carries, or with a foreign Host header. With Claude Code's Bash sandbox on, the command can't write
the plugin's data directory, so it prints the command to run in your own terminal instead.

## The guards

### Plan gate

Before Claude enters plan mode, the hook reminds it to get a blind Codex `architect_review`. It
should send the requirement and constraints, not the design it already picked, with any preferred
design in `caller_hypothesis`.

### Push gate

Every `git commit` and `git push` Claude runs is **denied once**, with the review state in the
reason and a one-shot token. Re-run the same command with the token in front to go ahead:

```text
CODEX_PUSH_ACK=<token> git push …                  # Bash
$env:CODEX_PUSH_ACK='<token>'; git push …          # PowerShell
```

The token is bound to that command and tree, lives 10 minutes and works once. The gate denies
rather than asks, because a hook's "ask" is honoured only in some permission modes, while "deny"
works in all of them. The review states are:

- **REVIEW VERIFIED.** A completed Codex review in this session whose signed content digest matches
  the tree, and a command that records exactly what was reviewed:
  - for a commit, a form that records the whole index, with its message given so no editor runs;
  - for a push, HEAD, index and worktree equal, nothing untracked, at most one commit on top of the
    remote-tracking ref, and the push bound to that ref:
    `git push --force-with-lease=refs/heads/<branch>:<oid> <remote> <branch>`.
- **VERIFIED-BUT.** A review exists, but something the gate cannot vouch for is present, and the
  reason names it. Examples: another command form, a hook or signer that would run, configuration
  that names a program, an environment that re-aims git, or a rewritten history.
- **STALE.** The tree changed after the review.
- **PENDING / none.** No completed review covers this change.

The acknowledgement is always yours to make. The token binds the whole decision: command, content,
HEAD, index, branch, configuration, tracking ref, environment and review evidence.

**In a checkout the writer coordinator has registered**, a `git commit` that would take another live
unit's paths, or content of unknown origin, is refused with the units and paths named, and no token
is given: publish that work with the unit tools, or leave those paths out. What a commit takes is
read from its own arguments: the index, `-a`, or its pathspecs. Your own unit's paths and free paths
get the usual decision. A command the gate can't read as one plain commit keeps its token and gets
the list as a note. A push is gated as before. Without coordinator state on the machine, the check
costs one directory lookup; with the coordinator down, the reason says so.

**Where it runs.** A session-start hook puts the plugin's git shim first on the Bash tool's PATH,
for the session, its subagents and teammates. `git commit` and `git push` reach the gate with their
exact arguments, directory and environment. Aliases, scripts and tools that run git are seen too, and
so is a git that git itself starts (through an alias, an external command or a step of its own).
Every other git command runs at once, adding about 3 ms. Once the shim is live, a grep, `sed` or
heredoc that merely mentions push or commit is never denied. A denial that a command's own output
hides (`2>/dev/null`) still reaches Claude through the notice hook. A git run by its full path is
not seen. The gate is a guardrail for Claude's own commands, not a barrier against code that sets
out to get around it.

**It never fails open.** The hook evaluates under a hard deadline (`CODEX_PUSH_GATE_EVAL_DEADLINE_S`,
60 s by default, under the 90 s hook timeout). A stall, a crash, an unusable token store or a git
without the CVE fix is a deny that names the cause. Its reads never run repository-controlled
programs: no `git diff` or `git status`, and fsmonitor and hooks forced off.

**With Claude Code's Bash sandbox on**, Bash can read the gate's state but not write it. Every git
command still runs. Run a commit or push as one plain git command in its own Bash call (`git commit
-F <file>` for a long message); it is denied with a token as usual and runs once acknowledged. A
commit or push the shim must decide inside the sandbox (an alias, a script, a push a hook starts) is
refused with what to do. Once a git command of the session has run outside the sandbox, a commit or
push inside it can no longer be acknowledged, so run that one outside the sandbox too.

## Configuration

Set these in the environment Claude Code starts with. Every one is optional.

| Variable | Effect |
|---|---|
| `CODEX_ORACLE_PYTHON` | The Python (3.11+) for the Codex Oracle server. Windows: `python`. |
| `CODEX_ORACLE_CODEX_BIN` | A specific `codex` executable, for example the one bundled with ChatGPT.app. |
| `CODEX_ORACLE_MAX_RUNTIME_S` | A run's wall-clock budget, 300–12600 s (default 10800). |
| `CODEX_PUSH_GATE_EVAL_DEADLINE_S` | The push gate's evaluation deadline (default 60 s). |
| `CODEX_PUSH_ACK_DIR` | Where the push gate keeps its tokens (default `~/.claude/logs/codex-oracle/push-ack`). |
| `SOFTWARE_WORKFLOWS_STATE` | The writer coordinator's state: its ledger and run records (default `~/.local/state/software-workflows`). |
| `SOFTWARE_WORKFLOWS_RUNTIME` | Where the coordinator's sockets live (default `/tmp/c1-<uid>`). Keep it short: macOS limits a socket path to 104 bytes. |
| `SOFTWARE_WORKFLOWS_PYTHON` | The Python (3.11+) the coordinator daemon runs on (default: the server's). |

The server's venv lives in the plugin's data directory
(`~/.claude/plugins/data/software-workflows-agent-teams/venv/`) and survives updates. A new one is
built when the requirements or the interpreter change.

## Platforms

- **macOS:** everything, measured.
- **Linux:** the same code. The git shim's Linux-specific paths have not yet been measured end to end
  for 2.0, nor the writer coordinator's process inventory for 2.3.
- **Windows:** no WSL needed for the agent teams and the Codex tools.
  - Set `CODEX_ORACLE_PYTHON=python`: python.org builds ship no `python3.exe`.
  - The hooks need `python3` on PATH. Create a `python3` alias next to `python.exe`, or use the
    Store alias; without one the hooks do not fire.
  - The git shim is not used on Windows. The push gate refuses every commit and push Claude runs,
    because its token store is not verified there, so commit and push yourself in a terminal.
  - Optional: Developer Mode gives the `latest.log` live-view symlink; without it, use `stream.log`.

## Troubleshooting

| Symptom | What to do |
|---|---|
| No Codex tools after installing or updating | Restart Claude Code, then check `/mcp`. `/reload-plugins` does not restart MCP servers. |
| "Server timed out" on the first start | The venv was still building. Run `/mcp` → codex-oracle → Reconnect; the next start finishes the build from pip's cache. |
| Two sets of Codex tools | A hand-added registration from 1.x. Run `/software-workflows:setup` and use the `claude mcp remove` command it prints. |
| A commit or push was denied | That is the push gate. Read the review state, then re-run the same command with the token. |
| A commit inside the sandbox can't be acknowledged | Run it as one plain git command in its own Bash call, or outside the sandbox. |
| A Codex call seems stuck | `codex_runs` and `codex_run_log`, or the live view. Max-effort runs often take many minutes. |
| A run shows DETACHED after a reconnect | `codex_resume_run(run=<id>)` collects its answer. |
| Hooks never fire on Windows | Add a `python3` alias (see Platforms). |
| A commit is refused as "content of unknown origin" | Something changed those files outside Edit and Write while a writer was working, or outside Claude. `coordinator_unit(action="unknown")` lists them; restore and redo the change with Edit or Write. |
| `abraham` says "Native admission/startup failed" | It ran on `codex exec` instead; the reason follows. `DAEMON_START` with a long `SOFTWARE_WORKFLOWS_RUNTIME` path is the 104-byte socket limit. |

## For maintainers

- **Layout.** The marketplace is `.claude-plugin/marketplace.json`, and the plugin is
  `plugins/software-workflows/`. In it: `server.py` (the Codex Oracle server), `run_server.py`
  (its launcher), `hooks/`, `skills/`, `agents/`, `commands/`, and `treedigest.py`, the content
  digest the server and the push gate share.
- **The record.** `CHANGELOG.md` holds what changed in each release and how it was verified. There
  is no test harness, by decision: every change is verified by real runs, with the results recorded
  there.
- **Checks before a release:** `claude plugin validate --strict plugins/software-workflows` and
  `claude plugin validate --strict .`, then a fresh install in an empty `CLAUDE_CONFIG_DIR`. Bump
  `version` in `plugin.json`, because teammates get a new copy only when it changes. Then tag with
  `claude plugin tag plugins/software-workflows`.
- **The Codex source as reference.** `python3 scripts/codex_src.py` keeps
  `~/Documents/codex-installed` pointing at a read-only export of the release tag that matches
  `codex --version`, never a git worktree. Read the ref that matches the installed binary, never
  main. Treat the source as the map and the installed binary as the territory: confirm anything
  load-bearing against the running binary, starting with a known-bad probe.

## License

[PolyForm Noncommercial 1.0.0](./LICENSE) © Ahrar Ahmad

**Noncommercial use only.** You may use, modify and share this plugin for any noncommercial purpose
(personal, research, education, nonprofits). Commercial use is not permitted under this license.
Contact the author for commercial terms.
