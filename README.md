# agent-teams-plugin

A Claude Code plugin marketplace, **`agent-teams`**, with one plugin, **`software-workflows`** (2.5.0).

2.5.0 removes the writer coordinator, the `abraham` writer and the Codex Office. Writers are now T3
child tasks that the lead starts, steers (soft or hard) and stops, in the project's own checkout (see
[Writers and steering](#writers-and-steering)). What changed, and how it was checked, is in
[CHANGELOG.md](./CHANGELOG.md).

It adds three things to Claude Code:

- **Agent teams that work.** A skill that runs multi-agent work through Claude Code's
  [Agent Teams](https://code.claude.com/docs/en/agent-teams), with every tool signature pinned so
  runs don't fail on malformed calls.
- **Codex as the second model.** The bundled **Codex Oracle** MCP server puts OpenAI Codex, at
  maximum reasoning effort with live web search, beside Claude, as a blind reviewer, architect and
  researcher.
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

Writers (see [Writers and steering](#writers-and-steering)) run as child tasks of
[T3 Code](https://github.com/pingdotgg/t3code), which starts, steers and stops them. Reviews and the
guards need only Claude Code and the Codex CLI.

Agent teams start in your next Claude Code session. After that, just ask, for example *"Create an
agent team to review this PR from security, performance and correctness angles"*, or *"Have Codex
review this diff"*. A session that starts with something missing says so and points you to setup;
nobody has to edit `settings.json` by hand.

### Requirements

- **A recent Claude Code.** Tested on 2.1.328. The skill needs at least 2.1.178 (agent teams without
  `TeamCreate`/`TeamDelete`), and moving a 1.x install over needs 2.1.193 or newer (the marketplace's
  `renames` entry).
- **Codex CLI, logged in.** Needs codex-cli 0.163.0 or newer; in daily use on 0.170.0, and the CLI
  may update itself. Codex uses the model set in your `~/.codex/config.toml` (gpt-6.2-astra on the
  author's machine) at reasoning effort `max`. The server pins the effort, so the Codex app's slider
  cannot lower it.
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
| MCP server `codex-oracle` | The Codex tools below. The plugin registers it itself (`.mcp.json`); do not add a second `mcpServers` entry for it. |
| Hooks | The plan gate, the push gate and its git shim, the session-start check, and one that passes the session's folder to the Codex tools. |

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
| `codex_runs`, `codex_run_log`, `codex_cancel_run`, `codex_resume_run` | See, watch, stop and continue runs. `codex_resume_run` continues a run's thread with a nudge, and with `images` too: a soft or hard steer (see [Writers and steering](#writers-and-steering)). |

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

**A review in another folder.** `code_review(cwd=…)` runs the review in that folder: a checkout, or a
frozen export that is not a repository. The answer's tree stamp is that folder's (`nogit` outside a
repository, which vouches for no push), `session="continue"` carries the session there, and a path
that is not a directory is refused. `cwd` is not a fence: a read-only run can still read any file you
can, so a brief that needs a boundary has to state it.

**Long runs survive.** Codex runs at max effort, so a call often takes many minutes. Claude Code then
moves it to the background and it returns as a task notification; that is normal, so wait for it.
A run lives on a file-backed spool, so `/mcp` → Reconnect or a server restart detaches it instead
of killing it, and `codex_resume_run` collects the answer at no model cost. `codex_runs` shows every
run (RUNNING, DETACHED, ok, error, cancelled, timeout), and `codex_run_log` shows its live log in
the conversation. To watch runs as they happen, run
`tail -F ~/.claude/logs/codex-oracle/stream.log` in a terminal of your own; it adds nothing to
Claude's context.

**Run budget.** A run is stopped at its wall-clock budget: 3 hours by default (healthy runs take 4–30
minutes). Set `CODEX_ORACLE_MAX_RUNTIME_S` (300 to 12600 seconds) in the environment Claude Code
starts with. Retries share one budget. The live log warns at 80 %, and a
stopped run tells you to `codex_resume_run` its thread instead of asking again.

## Writers and steering

The plugin writes no code itself. A writer is a **T3 child task** that the lead starts, steers and
stops: a Codex writer (provider `codex`) or a Claude writer (provider `claudeAgent`), at effort
`max`, in the project's own checkout (`isolation: "shared"`, never a worktree). The `agent-teams`
skill's `references/writers.md` has the charter lines and the exact calls.

| The lead wants to | Call |
|---|---|
| Start a writer | `delegate_task(task=<charter>, target={providerInstanceId, model, options}, isolation="shared", mode="async")` |
| **Soft steer:** nothing running is stopped; the writer takes the message in at its next step | `t3_thread_send(threadId, mode="steer", message)` |
| **Hard steer:** the step stops at once, then the writer takes the message in | `t3_thread_send(threadId, mode="restart", message)` |
| Stop it | `task_cancel(taskId)` |

**A steer adds information; it never cancels the task.** Head it `LEAD STEER (soft)` or
`LEAD STEER (hard)`, and say that the task and every earlier instruction still stand. If a steer seems
to conflict with an earlier instruction, the writer keeps the earlier one and tells the lead before
acting on that part. To change an instruction, the lead answers that question, or cancels the task and
starts a new one. Every charter tells the writer the same thing.

A steer needs an active turn. Send follow-up work to a writer that has finished with
`t3_thread_send(mode="auto")`.

Measured on 2026-11-20 (T3 0.1.10, Codex 0.170.0):
- **Soft steer:** a Codex writer finished its running command, then followed the steer in the same
  turn. A Claude writer waiting on a background command took the steer in at once, while the command kept
  running. When a steer must not arrive before a command finishes, wait for the command, then send it.
- **Hard steer:** the running command was gone within a second, for Codex and Claude children alike.
  The writer carried on with its task plus the new information.

Codex Oracle runs (reviews and the rest) steer the same way:
- **Soft:** `codex_resume_run(nudge=…)` after the run answers.
- **Hard:** `codex_cancel_run`, then `codex_resume_run(nudge=…)` on the same thread.

The server heads every nudge with the same rule.

**Several writers in one checkout:**
- Each writer gets its own files, and the lead orders work that meets.
- Broad commands (formatters, installs, generators) run one at a time.
- The lead reads each writer's changes by its paths (`git diff HEAD -- <paths>`, plus
  `git status --short -- <paths>` for new files) and commits by path.
- At most 2–3 writers run at once per project, since the project's one shared test copy goes to one
  writer at a time.

**Codex never pushes; the lead does, through the push gate.** The Oracle's own Codex runs carry Git
settings that refuse every push. A T3 Codex writer needs the same settings in `~/.codex/config.toml`,
because T3 starts Codex with your user config:

```toml
[shell_environment_policy.set]
GIT_CONFIG_PARAMETERS = "'url.sw-codex-no-push::.pushInsteadOf'='' 'protocol.sw-codex-no-push.allow'='always' 'hook.sw-codex-no-push.event'='' 'hook.sw-codex-no-push.event'='pre-push' 'hook.sw-codex-no-push.command'='printf '\\''%s\\n'\\'' '\\''LEAD_PUBLISHES: the lead (Claude) pushes; Codex cannot publish commits.'\\'' >&2; exit 1' 'hook.sw-codex-no-push.enabled'='true' 'hook.pre-push.enabled'='true'"
```

- **Remote helper:** copy `plugins/software-workflows/hooks/codex-publish/git-remote-sw-codex-no-push` to a folder on
  Codex's PATH, such as `~/.local/bin`.
- **What refuses which push:** the `pushInsteadOf` rewrite sends every ordinary push to that helper,
  which refuses it. The pre-push hook entries also refuse a push to a remote with an explicit `pushurl`,
  which the rewrite does not cover.
- **Git 2.54 or newer** is needed for those hook entries, since they are configuration-defined hooks. An
  older Git, Apple Git 2.50.1 included, ignores them. There a push to an explicit `pushurl` is not
  refused, and setup and the session-start notice say so.
- **Measured on 2026-11-20:** `git push` from a T3 Codex child was refused with
  `LEAD_PUBLISHES: the lead (Claude) pushes; Codex cannot publish commits.` (Git exited 128 after the helper
  refused).
- **Limits:** like the push gate, it is a guardrail against forgetting, not a security boundary.
  `--no-verify` skips the hook.

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

When acknowledgement is required, it is yours to make. The token binds the whole decision: command, content,
HEAD, index, branch, configuration, tracking ref, environment and review evidence.

The gate checks that a completed review of the exact tree exists, not what the review concluded:
acting on its findings is the lead's job.

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

The server's venv lives in the plugin's data directory
(`~/.claude/plugins/data/software-workflows-agent-teams/venv/`) and survives updates. A new one is
built when the requirements or the interpreter change.

## Platforms

- **macOS:** everything, measured.
- **Linux:** in daily use on Ubuntu 24.04 x86-64 (kernel 6.8, Codex 0.170.0, git 2.56): the oracle
  tools, the push gate, and T3 writers with soft and hard steering.
  - Ubuntu 24.04 refuses bubblewrap the namespaces Codex's sandbox needs, and a read-only run then
    reads no file. Setup checks this and prints the fix.
  - A git without `git hook list` publishes, and the push gate reads its hooks as unknown. Setup says
    so.
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
| A Codex call seems stuck | `codex_runs` and `codex_run_log`, or `tail -F ~/.claude/logs/codex-oracle/stream.log`. Max-effort runs often take many minutes. |
| A run shows DETACHED after a reconnect | `codex_resume_run(run=<id>)` collects its answer. |
| Hooks never fire on Windows | Add a `python3` alias (see Platforms). |

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
