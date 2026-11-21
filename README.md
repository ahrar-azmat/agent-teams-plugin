# agent-teams-plugin

A Claude Code plugin marketplace, **`agent-teams`**, with one plugin, **`software-workflows`** (2.5.1).

2.5.0 removed the writer coordinator, the `abraham` writer and the Codex Office. The lead now starts,
steers (soft or hard) and stops writers itself (see [Writers and steering](#writers-and-steering)).
2.5.1 brings four changes:
- Every writer, Claude or Codex, is a T3 child task, visible in T3's Lineage panel. Quick helpers are native
  foreground subagents.
- Writers may work in worktrees.
- The push gate checks what a push carries and leaves commits alone.
- Cleanup is enforced by hooks and systemd: [Cleanup](#cleanup).

What changed, and how it was checked, is in [CHANGELOG.md](./CHANGELOG.md).

It adds four things to Claude Code:

- **Multi-agent work you can watch.** A skill that says which agent goes on which tool: T3 child tasks
  for writers, native subagents for quick helpers. It also says how the lead starts, steers, stops and
  verifies them.
- **Codex as the second model.** The bundled **Codex Oracle** MCP server puts OpenAI Codex, at
  maximum reasoning effort with live web search, beside Claude, as a blind reviewer, architect and
  researcher.
- **Guards.** Hooks that ask for a Codex architecture review before plan mode, and a git pre-push
  check that lets a push through when Codex reviewed everything it carries.
- **Cleanup that runs by itself.** Whatever a command leaves running is stopped when it returns.
  Finished writers' worktrees are landed or dropped. A timer catches what slips through. See
  [Cleanup](#cleanup).

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

Claude writers are native Claude Code subagents. Codex writers run as child tasks of
[T3 Code](https://github.com/pingdotgg/t3code) (see [Writers and steering](#writers-and-steering)).
Reviews and the guards need only Claude Code and the Codex CLI.

Agent teams start in your next Claude Code session. After that, just ask, for example *"Create an
agent team to review this PR from security, performance and correctness angles"*, or *"Have Codex
review this diff"*. A session that starts with something missing says so and points you to setup;
nobody has to edit `settings.json` by hand.

### Requirements

- **A recent Claude Code.** Tested on 2.1.329. Native steering needs 2.1.317 or newer (a message to a running
  background agent arrives without stopping its command), and moving a 1.x install over needs 2.1.193 or newer
  (the marketplace's `renames` entry).
- **Codex CLI, logged in.** Needs codex-cli 0.163.0 or newer; in daily use on 0.170.0, and the CLI
  may update itself. Codex uses the model set in your `~/.codex/config.toml` (gpt-6.2-astra on the
  author's machine) at reasoning effort `max`. The server pins the effort, so the Codex app's slider
  cannot lower it.
- **Python.** The Codex Oracle server needs Python 3.11 or newer, which setup finds for you. The
  hooks run with whatever `python3` is on PATH, 3.9 or newer (the one macOS ships is fine).
- **git 2.54 or newer** for the push gate: it is a configuration-defined pre-push hook, which older
  git ignores. Then the session-start notice says the gate is inactive.
- **For the cleanup:** Linux with a systemd user manager (`systemctl --user`). The cleanup does nothing
  on macOS and Windows yet. Codex hooks need Codex CLI 0.171.0 or newer, where hooks are trusted
  through its config.

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
| Skill `agent-teams` | Runs every multi-agent task: which agent goes on which tool (native Claude subagents, T3 child tasks for Codex, the Codex Oracle for reviews), and how the lead starts, steers (soft or hard), stops and verifies them. Includes researcher, architect and reviewer prompt templates with the Codex phase built in, and a troubleshooting table. |
| Skill `codex-planning` | Before a feature or non-trivial change: consult Codex independently before building. |
| Skill `codex-review` | Before committing or pushing: Codex reviews the change independently. |
| Agent `code-reviewer` | Dispatches a blind Codex review, verifies its findings and writes one report. |
| `/software-workflows:setup` | The setup above. |
| MCP server `codex-oracle` | The Codex tools below. The plugin registers it itself (`.mcp.json`); do not add a second `mcpServers` entry for it. |
| Hooks | The plan gate, the push gate (a git pre-push hook installed for the session), the cleanup hooks, the session-start check, and one that passes the session's folder to the Codex tools. |
| `swctl` | The cleanup's command line, installed in `~/.local/bin`: `swctl run` (kept processes with a deadline), `swctl wt list`, `swctl wt check <branch>` (untracked files a landing would overwrite), `swctl leftovers`, `swctl sweep`. |

### Agent tools

Writers use T3's `delegate_task`, `t3_thread_send`, `t3_pending_request` and `task_cancel`. Quick helpers use Claude Code's own `Agent`, plus `SendMessage` and `TaskStop` outside T3. The skill doesn't use Agent Teams: T3 runs Claude as an Agent SDK session, where teammates never spawn. Deferred tools load per context, so an agent loads the tools it needs with `ToolSearch` first.

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

The plugin writes no code itself. The lead starts, steers and stops writers. The `agent-teams` skill
says which tool to use; its `references/writers.md` has the charter lines and the exact calls.

| Agent | How it runs |
|---|---|
| Every writer, Claude or Codex, and any helper longer than a few minutes | A [T3 Code](https://github.com/pingdotgg/t3code) child task: `delegate_task(title=…, task=<charter>, isolation="shared"\|"worktree", target=…)`. Claude: `claudeAgent` / `claude-opus-5-5`, effort max. Codex: `codex` / `gpt-6.2-astra`, effort max. The owner sees each one in T3's Lineage panel, can open its thread, follow it live and stop it. It also survives the lead's restarts. |
| A quick helper the lead waits for | A native foreground subagent: `Agent(…, run_in_background=false)`. |
| A writer in a plain terminal (no T3) | A native background subagent: `Agent(name=…, model="opus", …)`. |

On 2026-11-21 the owner tried native background writers inside T3 and chose T3 children for that
visibility: "current background doesn't offer me visibilty as t3 with this".

| The lead wants to | T3 child (every writer) | Codex Oracle run | Native subagent (outside T3) |
|---|---|---|---|
| **Soft steer:** nothing running is stopped; the writer takes the message in at its next step | `t3_thread_send(mode="steer")` | `codex_resume_run(nudge=…)` after it answers | `SendMessage(to=<name>)` |
| **Hard steer:** the step stops at once, then the writer takes the message in | `t3_thread_send(mode="restart")` | `codex_cancel_run`, then `codex_resume_run(nudge=…)` | `TaskStop(task_id=<name>)`, then `SendMessage` |
| Stop it | `task_cancel` (or Stop in the Lineage panel) | `codex_cancel_run` | `TaskStop` |

**A steer adds information; it never cancels the task.** Head it `LEAD STEER (soft)` or
`LEAD STEER (hard)`, and say that the task and every earlier instruction still stand. If a steer
seems to conflict with an earlier instruction, the writer keeps the earlier one and tells the lead
before acting on that part. To change an instruction, the lead answers that question, or stops the task
and starts a new one. Every charter tells the writer the same thing, and the server heads every Oracle
nudge with it.

**Agent teams are not used.** Teammates and the shared task board need an interactive CLI with
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. T3 runs Claude as an Agent SDK session, where Claude never
spawns teammates and a named agent is a plain subagent.

**Measured on nucleus:**
- **Native subagent** (2026-11-21, Claude Code 2.1.329 in T3 0.1.10): `SendMessage` left the running
  command alone, and the writer took it in after that command finished. `TaskStop` stopped the running
  command at once. A `SendMessage` then resumed the writer, which redid the stopped round and finished
  its task with the addition.
- **T3 children** (2026-11-20, T3 0.1.10, Codex 0.170.0):
  - **Soft steer:** the running command finished, then the writer followed the steer, Claude children
    included.
  - **Hard steer:** the running command was gone within a second.
- **Session release:** a native writer lives inside the lead's session. T3 keeps that session open for
  up to 4 hours while it runs. After a release, `SendMessage` resumes it with its memory, but the command
  it was running is lost.

**Several writers.** Up to 5–10 writers may run at once (owner, 2026-11-21), each on its own files or in
its own worktree.
- **In the shared checkout,** the lead orders work that meets. Broad commands (formatters, installs,
  generators) run one at a time.
- **A writer in a worktree** gets `isolation: "worktree"` on `Agent` or `delegate_task`. When it is done:
  - the writer commits its work on the worktree's own branch (never pushes);
  - before its final reply, the writer checks for ignored files and flag-hidden edits and copies out what
    the lead needs: Claude may remove a native writer's worktree before the lead reads the reply;
  - the lead sees everything the worktree holds (`git status --short --ignored`, `git ls-files -v`),
    notes the main checkout's HEAD and the writer's tip as literal SHAs, and runs
    `swctl wt check <branch>`, which lists the untracked files that landing would overwrite or delete,
    across every commit picked, and prints the exact range to land;
  - the lead lands that range with `git cherry-pick <base>..<tip>` (or `git merge --squash` and
    `git commit`) and reviews `git diff <noted HEAD>`;
  - then `git worktree remove <path>`, and the branch once its content is confirmed in HEAD, at that tip
    (`git update-ref -d refs/heads/<branch> <tip>`).
    `git worktree remove` deletes ignored files and edits hidden by index flags without asking, and Git
    overwrites ignored files on a cherry-pick even with `--no-overwrite-ignore` (Git 2.56.0, measured).
    The full steps: the skill's `references/writers.md` §5.
- **The lead reads** each writer's changes by its paths (in the shared checkout `git diff HEAD -- <paths>`,
  plus `git status --short -- <paths>` for new files; for landed commits `git diff <noted HEAD> -- <paths>`)
  and commits by path.
- **The lead tests.** Writers run only quick checks that end by themselves. The project's one shared
  test copy is the lead's, used first in, first out.

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

**Commits are never gated.** Every `git push` Claude runs (the lead, its subagents, scripts, any git
on any path) runs one check as a git **pre-push hook**. Git hands that hook the exact commits the push
carries.

- **It goes through in one call** when every file those commits change, at exactly the pushed bytes,
  was in the tree of a completed Codex `code_review`. A commit that was already in the history of a
  reviewed HEAD counts as reviewed, so you can commit first and then review the committed range. One
  line on stderr says so.
- **Otherwise git refuses the push.** The message names the unreviewed paths and gives the ways
  forward:

```text
# run code_review on the change, then push again; or re-run the same push with the one-shot token:
CODEX_PUSH_ACK=<token> git push …
# or say it up front, for a push that needs no review (tooling, docs):
CODEX_PUSH_UNREVIEWED="<reason>" git push …
```

The token is bound to those exact ref updates (remote and commits), not to the command text, so pipes
and redirects don't matter. It lives 10 minutes and works once. Every decision goes to
`~/.claude/logs/codex-oracle/push-log.jsonl`, unreviewed pushes and their reasons included.

**Where the review evidence comes from.** For every `code_review`, the Codex Oracle server records
what it was dispatched on: the HEAD commit, plus the bytes of every file that differed from it. On
a completed review it writes that record to `~/.claude/logs/codex-oracle/reviews/`. The hook accepts
reviews from the last 14 days of the same repository, found by its root commit. So a review counts
for a push from a fresh clone, and another writer's unrelated edit in the checkout no longer makes it
"stale".

**Where it runs.** A session-start hook adds the hook to the session's environment, as a git
configuration hook (`GIT_CONFIG_COUNT`). It removes the 2.0–2.5.0 git shim from a resumed session's
environment. `git push --no-verify` skips it, as it skips every pre-push hook. The gate checks that
Codex saw the content, not what Codex concluded: acting on its findings is the lead's job. It is a
guardrail against forgetting, not a barrier against code that sets out to get around it.

## Cleanup

The owner's rule, "stop what you start", is enforced by mechanisms that run every time, not by
instructions (owner, 2026-11-21). Linux with a systemd user manager. All of it lives in
`plugins/software-workflows/cleanup/swclean.py`.

| When | What happens |
|---|---|
| A Bash command returns | Every process it left running is stopped, with its children, and the agent is told. Claude: a PreToolUse hook tags each call (`SW_CALL`, bypassPermissions sessions only), and PostToolUse stops what carries the tag. A call Claude moved to the background keeps its processes. Codex: it tags every command itself (`CODEX_TOOL_CALL_ID`), and a user-level PostToolUse hook does the same. |
| A subagent finishes, a turn ends | Leftovers of finished calls are stopped. At a Claude lead's turn end, the stop is refused once while the session's own finished writers' worktrees hold unlanded work. Nothing in the cleanup deletes a worktree: Claude removes a writer's clean one itself, and the lead removes the rest with `git worktree remove` after landing their commits. |
| A session ends | A detached sweep (systemd-run) stops what the session left. |
| Every 10 minutes | `sw-sweep.timer` stops leftovers of ended sessions and stale calls, removes `swctl run` environment files no unit uses any more, and re-trusts the Codex hooks when `~/.codex/hooks.json` changed. It never touches a worktree. |

**Kept on purpose:**
- `swctl run --name N --ttl 2h [--mem 8G] [--cpu 400%] [--compose P] -- CMD` starts a systemd user
  service in `sw.slice`, with a deadline (`RuntimeMaxSec`), memory, CPU and task limits, and
  `KillMode=control-group`. `--compose` takes its Compose project down when it stops.
- `swctl run list|logs|stop`.
- Anything in another systemd unit, and anything matched by `~/.config/janitor/keep`, is never stopped,
  and neither are its parents and children: a parent such as `timeout` passes its signal on, and a kept
  supervisor keeps its workers. The keep file is read again right before every signal.

**Installed by** the first 2.5.1 session start, or `swctl install`:
- `~/.local/share/software-workflows/swclean.py`, `~/.local/bin/swctl`;
- `sw-sweep.timer`, `sw.slice`;
- four entries in `~/.codex/hooks.json` (SessionStart, PostToolUse, Stop, SessionEnd), trusted through Codex's
  own `config/batchWrite` and verified one by one through `hooks/list`. Codex skips an untrusted user hook
  without a word (measured on 0.171.0).

The log is `~/.local/state/software-workflows/cleanup/cleanup.log`: pids, names and reasons, never
command lines.

**Not covered:**
- Docker containers, which belong to dockerd: use `swctl run --compose`, or Platform's run-guard.
- Processes that clear their environment (`env -i`, `sudo`).
- Calls in a Claude session that does not run with bypassPermissions, since the tag would change the
  command text that permission rules read.

## Configuration

Set these in the environment Claude Code starts with. Every one is optional.

| Variable | Effect |
|---|---|
| `CODEX_ORACLE_PYTHON` | The Python (3.11+) for the Codex Oracle server. Windows: `python`. |
| `CODEX_ORACLE_CODEX_BIN` | A specific `codex` executable, for example the one bundled with ChatGPT.app. |
| `CODEX_ORACLE_MAX_RUNTIME_S` | A run's wall-clock budget, 300–12600 s (default 10800). |
| `CODEX_PUSH_ACK_DIR` | Where the push gate keeps its tokens (default `~/.claude/logs/codex-oracle/push-ack`). |
| `CODEX_REVIEW_WINDOW_DAYS` | How old a review the push gate still accepts (default 14). |
| `CODEX_PUSH_GATE_MAX_PAIRS` | The most changed (path, content) pairs one push is checked for before it is refused as too large to check (default 50,000). |
| `SW_RUN_MAX_TTL_S` | The longest `swctl run --ttl` (default 86400). |
| `SW_RUN_DEFAULT_MEM`, `SW_RUN_DEFAULT_CPU` | `swctl run` defaults (8G, 400%). |
| `SW_SLICE_MEM`, `SW_SLICE_CPU` | The caps on all `swctl run` services together (56G, 1200%). |
| `SW_KEEP_FILE` | The keep file the cleanup honours (default: the janitor's own, from its unit's `--keep`, else `~/.config/janitor/keep`). If the file named here is missing, nothing is stopped. |
| `SW_CLEANUP_TAG=always` | Tag Bash calls in any permission mode, not only bypassPermissions. |

The server's venv lives in the plugin's data directory
(`~/.claude/plugins/data/software-workflows-agent-teams/venv/`) and survives updates. A new one is
built when the requirements or the interpreter change.

## Platforms

- **macOS:** everything, measured.
- **Linux:** in daily use on Ubuntu 24.04 x86-64 (kernel 6.8, systemd 255, Codex 0.171.0, git 2.56): the
  oracle tools, the push gate, the cleanup, and T3 writers with soft and hard steering.
  - Ubuntu 24.04 refuses bubblewrap the namespaces Codex's sandbox needs, and a read-only run then
    reads no file. Setup checks this and prints the fix.
  - Git older than 2.54 ignores configuration-defined hooks. There, setup and the session-start notice
    say the push gate is inactive, naming the measured version.
- **Windows:** no WSL needed for the agent teams and the Codex tools.
  - Set `CODEX_ORACLE_PYTHON=python`: python.org builds ship no `python3.exe`.
  - The hooks need `python3` on PATH. Create a `python3` alias next to `python.exe`, or use the
    Store alias; without one the hooks do not fire.
  - The push gate and the cleanup are not active on Windows.
  - Optional: Developer Mode gives the `latest.log` live-view symlink; without it, use `stream.log`.

## Troubleshooting

| Symptom | What to do |
|---|---|
| No Codex tools after installing or updating | Restart Claude Code, then check `/mcp`. `/reload-plugins` does not restart MCP servers. |
| "Server timed out" on the first start | The venv was still building. Run `/mcp` → codex-oracle → Reconnect; the next start finishes the build from pip's cache. |
| Two sets of Codex tools | A hand-added registration from 1.x. Run `/software-workflows:setup` and use the `claude mcp remove` command it prints. |
| A push was refused: `push gate: review coverage incomplete` | Run `code_review` on the change and push again; or re-run with `CODEX_PUSH_ACK=<token>` from the message; or `CODEX_PUSH_UNREVIEWED="<reason>"`. |
| "This command left N process(es) running … they were stopped" | The cleanup at work. Keep a process with `swctl run --name … --ttl … -- …`. |
| The turn end was refused over worktrees | For each one listed, follow the steps in the hook's message (also in the skill's `references/writers.md` §5): see what it holds, run `swctl wt check <branch>`, land, review `git diff <noted HEAD>`, then remove it. |
| Codex hooks don't run | `swctl install` re-adds and re-trusts them; see `cleanup.log`. |
| A Codex call seems stuck | `codex_runs` and `codex_run_log`, or `tail -F ~/.claude/logs/codex-oracle/stream.log`. Max-effort runs often take many minutes. |
| A run shows DETACHED after a reconnect | `codex_resume_run(run=<id>)` collects its answer. |
| Hooks never fire on Windows | Add a `python3` alias (see Platforms). |

## For maintainers

- **Layout.** The marketplace is `.claude-plugin/marketplace.json`, and the plugin is
  `plugins/software-workflows/`. In it: `server.py` (the Codex Oracle server), `run_server.py`
  (its launcher), `review_ledger.py` (the review records the push gate reads), `cleanup/swclean.py`
  (the cleanup), `hooks/`, `skills/`, `agents/` and `commands/`.
- **The record.** `CHANGELOG.md` holds what changed in each release and how it was verified. There
  is no test harness, by decision: every change is verified by real runs, with the results recorded
  there.
- **Checks before a release:** `claude plugin validate --strict plugins/software-workflows` and
  `claude plugin validate --strict .`, then a fresh install in an empty `CLAUDE_CONFIG_DIR`. Bump
  `version` in `plugin.json`, because sessions get a new copy only when it changes. Then tag with
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
