# agent-teams-plugin

A Claude Code plugin marketplace providing **`software-workflows`** — a skill that orchestrates **multi-agent work** using Claude Code [Agent Teams](https://code.claude.com/docs/en/agent-teams) (shared task lists, inter-agent messaging, task dependencies), with **correct, schema-validated tool signatures** so runs don't fail on malformed tool calls.

> Marketplace name: `agent-teams` · Plugin name: `software-workflows`

## What it does

When you ask Claude Code to do anything involving 2+ agents, the `agent-teams` skill kicks in and drives the full lifecycle:

1. Loads the deferred team tools (`TaskCreate`, `TaskUpdate`, `SendMessage`, `TaskGet`, `TaskList`)
2. Spawns focused teammates (each its own context window) with self-contained prompts — the team is **implicit**, formed by the first `Agent` call
3. Wires up task dependencies, assigns/claims work, coordinates via direct messages
4. Shuts teammates down when the work is done (the implicit team cleans up on session exit)

It ships **role templates** (researcher, architect, implementer, reviewer, tester, …) and **team patterns** (research, implementation, debug, review), plus an optional **cross-model advisory** layer that uses Codex Oracle as an independent senior advisor when its MCP server is present.

The codex-oracle plugin also ships **`/abraham`** (v1.13.0): a write-capable mode that runs as two air-gapped codex phases — read-only deep analysis (codebase + live infra + live web) producing an implementation brief, then a sealed implementer (workspace-write file access, no network/web/MCP) that edits the working tree under git preconditions, a one-writer lock, and changed-files attribution for the orchestrator to review.

## Why this exists (the v1.1.0 fix)

Agent Teams tools are **deferred** and **schema-validated** (`additionalProperties: false`). The most common way orchestration runs fail is passing parameters the tools don't accept. This skill pins the exact signatures so the model can't improvise:

- `TeamCreate` / `TeamDelete` **no longer exist** (removed in Claude Code v2.1.178) and `team_name` is deprecated and ignored — the team is implicit and the Task tools auto-associate with the session.
- `TaskCreate` has **no** `blockedBy` — create first, then `TaskUpdate(taskId, addBlockedBy=[…])`.
- `SendMessage` is `to` + `message` (+ optional `summary`) — **not** `recipient`/`type`/`content`, and there is **no broadcast**. Protocol `type` goes *inside* the `message` object.
- Deferred tools load **per context** — every teammate prompt loads them first.

The skill includes a Tool-Signatures reference table and troubleshooting entries mapping each error to its fix.

## The Independence Protocol (v1.4.0)

A second opinion is only worth something if it was formed **independently** — and the way that
guarantee gets destroyed is subtle. You write up your own diagnosis, paste it into the prompt,
and ask Codex to "review" it. What comes back is a reaction to *your framing*, not an
independent read of the evidence — your own opinion wearing the advisor's voice.

The MCP enforces independence server-side rather than trusting the caller to ask for it:

- **`caller_hypothesis`** on every advisory tool — the one correct channel for your own view.
  It is presented as an *unverified claim to refute* and answered with an explicit
  **CONFIRMED / REFUTED / UNPROVEN** verdict naming the evidence that decided it.
- **An independence contract** injected into every prompt: reason from primary evidence first,
  treat caller claims as unverified, investigate what the caller *didn't* ask about, lead with
  disagreement.
- **An anchoring lint** on the neutral scoping fields (`context`, `concerns`, `focus`, `topic`).
  Conclusion language ("the root cause is", "I fixed", "does this look right") triggers a
  counter-anchoring injection *and* a loud `⚠️ ANCHORING WARNING` on the result telling you your
  agreement is now weak evidence. It never silently strips your text and never blocks the call.
- **A mandatory "where I disagree with the caller's framing"** section in every review's output.

Round 1 is blind; round 2 can be adversarial. The rule to remember:

> The advisor agreeing with you is strong evidence **only if it was dispatched blind.**
> Disagreement is strong evidence either way.

## Live web research (v1.4.0)

The advisor actually researches instead of recalling. Codex runs with `web_search=live` —
its default is `cached`, an OpenAI-maintained snapshot index, so the previous instruction to
"search the web and cite URLs" had no mechanism behind it. It requires primary sources with
URLs, marks unverifiable load-bearing claims `UNVERIFIED`, and ends with a Sources section;
code reviews additionally check APIs against current upstream docs and touched dependencies
for known CVEs.

## The channel layer (v1.14.0)

Advisor web search reaches ordinary pages; it can't read YouTube subtitles, RSS feeds, or
semantic-search indexes. When the [agent-reach](https://github.com/Panniantong/agent-reach)
CLI is installed, the orchestration skill uses it as a **caller-side fetch layer**: the
orchestrator or a teammate fetches, curates, and passes content to the advisor as cited
context data. The advisor is never given a networked sandbox to fetch for itself —
untrusted web content, full-disk read, and network egress must never share a process
(measured on codex 0.147.0: no mechanism exists for network without full disk read).
Entirely optional — without the CLI, nothing changes.

## Run operations and survivability (v1.17.0)

A backgrounded oracle call used to be a child of the MCP server: Claude Code's `/mcp`
reconnect (SIGINT, then SIGTERM ~100 ms later) killed the run and the caller saw
"Connection closed" — a 25-minute max-effort review lost. Now codex runs on a **file-backed
spool** (`~/.claude/logs/codex-oracle/runs/<run>/`) that the server tails, a **detached
watchdog** enforces the runtime deadline with no server alive, the shutdown signal makes the
cancel-cleanup **detach instead of kill**, and `codex_resume_run` **adopts** the run from
the next connection — waiting for it if it is still running, returning its answer at no
model cost. A caller cancel (no shutdown signal) still kills. Write runs are never detached.

Operations tools, so nobody tails log files by hand: `codex_runs()` (status of every run —
RUNNING / DETACHED / ok / error / cancelled / timeout), `codex_run_log(run, lines)` (the live
log in-conversation; the MCP task panel is structurally silent once a call is backgrounded),
`codex_cancel_run(run)`. `CODEX_ORACLE_CODEX_BIN` pins the codex executable (e.g. the
ChatGPT.app-bundled build). E2E proof: `plugins/codex-oracle/selftest_detach.py [--real]`
kills the server exactly like Claude Code does mid-call and collects the run afterwards.

**Run budget.** A run is SIGKILLed at its wall-clock budget by the detached watchdog (no
server needed). Default **3 hours** (measured: healthy runs take 4–30 min; the old 60-minute
literal killed legitimate analysis-heavy runs at 62–66 min). Adjust with
`CODEX_ORACLE_MAX_RUNTIME_S=<seconds>` in the environment Claude Code starts the MCP server
with (300..12600 — 30 min under the plugin's 4 h MCP call timeout; anything else is rejected
loudly and the default kept). It is ONE budget per request: retries and abraham's two phases
share it. `codex_runs()` prints
the effective budget, the live log warns at 80 %, and a kill message names the knob and
tells you to `codex_resume_run` the thread instead of re-asking. The plugin's MCP call
timeout (`.mcp.json`, 4 h) and the host's idle abort sit above it; a detached run survives both.

**Next (1.18, staged):** a per-user *oracle daemon* that owns the codex processes (exit codes,
deadlines, journal) with a CLI follower run through Claude Code's background Bash and a thin MCP
façade — first on `codex exec`, then `codex app-server` read runs once the pin/isolation/recovery
probes pass. Plan: `PLAN_1.18_ORACLE_DAEMON.md`; measured spike: `plugins/codex-oracle/spike/`.

## Upstream codex source as reference (map vs territory)

The `openai/codex` CLI is Apache-2.0 open source, and its internals are the reference for
everything these plugins wrap. Two rules keep that reference honest:

1. **Read the ref that matches the installed binary, never main-HEAD.** Releases are cut on
   branches, so main and the release tags are divergent — either direction of drift has
   produced wrong conclusions here. `python3 scripts/codex_src.py` keeps a stable worktree
   (`~/Documents/codex-installed`, override via `CODEX_SRC_WORKTREE`) checked out at
   `rust-v<installed version>`, re-aligning itself after every codex update. It never touches
   the base clone (`~/Documents/codex`, override via `CODEX_SRC_CLONE`) and refuses to run
   over local modifications.
2. **Source is the map; the installed binary is the territory.** Source names the config
   keys, events, and mechanisms worth probing — but anything load-bearing is confirmed
   against the running binary (`--strict-config` with a known-bad key first, live probes,
   the binary's own `models_cache.json`), never asserted from source alone.

## Install

```text
/plugin marketplace add ahrar-azmat/agent-teams-plugin
/plugin install software-workflows@agent-teams
```

Then just ask for multi-agent work (e.g. *"Create an agent team to review this PR from security, performance, and test-coverage angles"*).

The plugin registers its MCP server itself (`plugins/codex-oracle/.mcp.json`, launcher
`run_server.py` bootstraps its venv). Its interpreter is `${CODEX_ORACLE_PYTHON:-python3}`:
macOS/Linux need nothing; on Windows set `CODEX_ORACLE_PYTHON=python` (python.org builds
ship no `python3.exe`). Do not add a second, direct `mcpServers` entry for the same server —
two registrations give two tool sets.

The plugin's PreToolUse **hooks** (live-view nudge, plan gate, git-push gate) run
`python3` directly — the hook spawn path performs no `${VAR:-default}` expansion (measured
across Claude Code versions; see CHANGELOG 1.17.2), so `CODEX_ORACLE_PYTHON` cannot reach
them. They fire ONLY where `python3` resolves on PATH — on every OS (a custom
`CODEX_ORACLE_PYTHON` can run the MCP server without making the hooks work). On Windows
create a `python3` alias/shim next to your `python.exe` (or use the Store alias); with only
`python` on PATH the hooks do not fire (Claude Code treats an unstartable hook as
non-blocking), while the MCP server itself still runs via `CODEX_ORACLE_PYTHON`. The 1.18
daemon replaces this with a native launcher.

The **git-push gate** never auto-approves and never blocks silently: every detected
`git push` / `git commit` is DENIED with the review state in the reason (REVIEW VERIFIED for a
completed, digest-matching Codex review of a lone push/commit of the reviewed HEAD; STALE,
PENDING, or none otherwise) plus a one-shot acknowledgement token. Re-run the same command as
`CODEX_PUSH_ACK=<token> git push …` (`$env:CODEX_PUSH_ACK='<token>'; git push …` in PowerShell)
to proceed under the session's normal permissions; the token is bound to that command and tree,
lives 10 minutes, and is consumed on first use. "deny" rather than "ask" because a hook "ask"
is honoured only in some permission modes, while "deny" is authoritative in all of them.
The tree digest is one module, `treedigest.py`, shared by the server and the hook. The hook
itself evaluates in a worker process group under a hard deadline held by its parent
(`CODEX_PUSH_GATE_EVAL_DEADLINE_S`, default 60 s, under the declared 90 s hook timeout); a
stalled worker is killed and the command DENIED — a timed-out hook would fail open. An
unusable token store is likewise a deny that names the fix. Windows: the token store is
UNVERIFIED and the gate denies there until a native walk passes. The digest reads the
worktree's bytes itself (no `git diff`, no `git status` — those run configured filters and
drivers) and every remaining git read forces repository-controlled execution off
(`core.fsmonitor`, `core.hooksPath`; GIT_DIR-style environment re-aiming is scrubbed) — a
repository cannot run a helper through the hook. The digest ignores HEAD: committing reviewed
content keeps the review VERIFIED through the push. Because git commits the INDEX and pushes
HEAD's TREE — and a push transfers HISTORY — the full VERIFIED wording also needs the recorded
objects to equal the reviewed worktree under a strict verifier (a push: HEAD ≡ index ≡ worktree,
nothing untracked, no sparse/unrepresentable entries, at most ONE commit on top of the
remote-tracking ref, and the push BOUND to that ref's measured object id —
`git push --force-with-lease=refs/heads/<branch>:<oid> <remote> <branch>`, the lease spelled exactly
like the destination and first on the command line (git expands even a full destination against the
remote's refs, and applies the first lease that matches the same way), a compare-and-swap git refuses
if the remote moved, because a tracking ref is only as fresh as the last fetch; a commit: a form that
records the complete index whose every blob equals its file, names a message source or `--no-edit`
so no editor runs, and does not re-add through clean filters with `-a`), judged by the same
module's filter-free status (the server's write-mode snapshot
uses it too, so no `git status` runs anywhere in the plugin) with replace refs disabled. A
GIT_DIR/GIT_CONFIG-style variable in the environment, a reviewed-then-rewritten history, a
pathspec or `#`-word commit, a forced push (only `--force-with-lease=<ref>:<oid>` with an explicit
object id is a lease; every other force form is a force; `--no-force-with-lease` cancels the ones
before it), an unleased push, a push whose destination is mapped by `remote.<r>.push` or, under
`push.default=upstream` (or its synonym `tracking`, and only when `branch.<b>.remote` is set, as git
requires), by `branch.<b>.merge`, a command-local
`VAR=value` assignment, a push-only endpoint (`pushurl`, `pushInsteadOf`, `receivepack`, or an
effective fetch/push URL that is not a native transport — git runs a `git-remote-<transport>`
helper for every scheme it does not implement — schemes are case-sensitive to git; ssh, git, file,
git+ssh and ssh+git are builtin, http(s) run git's bundled remote-curl helper), an explicit
destination that is not `refs/heads/<name>` (git resolves `<src>:<name>` against the remote's refs at
push time, so a tag of that name would receive the push; `main`, `HEAD` and a bare push inherit the
source's full name), an active hook for ANY event the command reaches
as git itself lists them (configured hooks included; `--no-verify` skips pre-commit and commit-msg
only — prepare-commit-msg and the post-* hooks always run), a commit form that opens a program (an
editor when no message source is given, a signer — explicit or via commit.gpgSign/push.gpgSign —
a trailer command), repository-scoped configuration that names a program or rewrites an endpoint
(parsed by section and variable, so URL-scoped credential helpers and dotted driver names count),
or a stale token (the token binds the
command, the content, HEAD, the index listing, the branch, the configuration, the tracking ref,
the environment and the review evidence — the whole decision, consumed atomically) all read
VERIFIED-BUT and say why; the acknowledgement is yours to make. The hook never crashes to a
non-blocking exit: any failure past detection is a structured deny, and the host's payload itself
is read from the descriptor under a cap and ONE deadline that also bounds detection (linear, run in
the worker; the parent only pre-filters) and the evaluation — an over-cap, malformed or stalled
payload is a deny that names the cause, never a silent exit. Git booleans are read with git's own
grammar (any non-zero integer is true; an unreadable value is not provably off). Before any read the git on
PATH is PROBED in a throwaway partial clone (a promisor helper must not run under the safe
environment; the probe must go red without it) and checked against the CVE-2024-32465
fixed-version policy; a git that fails either refuses every read.

**Full-access writes (user ruling 2026-09-05):** `abraham(full_access=true)` — or
`CODEX_ORACLE_WRITE_FULL_ACCESS=1` as the default — runs the implementation phase with codex's
`--dangerously-bypass-approvals-and-sandbox` (`--yolo`): no sandbox, no prompts, network on,
your MCP servers, your privileges, so the writer can run the project's real gates (fresh-database
migrations, Temporal suites, browsers) and report real exit codes instead of a sealed guess. The
git contract, one-writer lock, changed-files report and request budget still hold; the sealed
mode's no-egress / no-credentials guarantees do not. Sealed stays the default.

**Survivor-containment trust model (1.17.x):** runaway processes left behind by a codex run
are contained COOPERATIVELY — the process group is swept, and descendants that `setsid()` out
of it (codex 0.151.0's shell tools do) are found and killed via an inherited per-run
environment marker. A descendant that deliberately scrubs its environment, descriptors and
session escapes every userspace channel at once; no in-process mechanism can hold it. For
read-only and write runs that residual is bounded by codex's own OS sandbox (which descendants
inherit); an **infra run is `danger-full-access` by design** — a scrubbed escapee from an infra
run retains unrestricted user-level filesystem capability, which is part of what opting into
`infra: true` accepts (the caller already trusts the investigation prompt). Pinned by a
regression test; durable kernel-side custody is the 1.18 daemon's job.

## Requirements

- **Claude Code v2.1.178+** — the skill drives the implicit-team API introduced when `TeamCreate`/`TeamDelete` were removed in v2.1.178. (v2.1.193+ additionally auto-migrates installs that still have the removed `antigravity` plugin enabled, via the marketplace `renames` mapping.)
- **Agent Teams enabled** — they're experimental and off by default. Enable in `settings.json`:
  ```json
  { "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
  ```
- **Optional — cross-model advisory:** the `codex-oracle` MCP server (ships in this marketplace). If it isn't configured, the skill still runs normally; the cross-model steps are simply skipped.
  - The server is Python (≥3.11) and launches via `${CODEX_ORACLE_PYTHON:-python3}` (see the note above); the hooks additionally require a resolvable `python3`.
  - `codex-oracle` needs the Codex CLI (`npm i -g @openai/codex`) authenticated (`codex`); live web search is forced on per call, so no config change is required.
  - **Windows (v1.9.0+):** fully supported — no WSL needed. Optional: enable Developer Mode if you want the `latest.log` convenience symlink; without it the merged `stream.log` is the live view.

## License

[PolyForm Noncommercial 1.0.0](./LICENSE) © Ahrar Ahmad

**Noncommercial use only.** You may use, modify, and share this plugin for any
noncommercial purpose (personal, research, education, nonprofits). Commercial
use is not permitted under this license — contact the author for commercial terms.
