# Changelog

All notable changes to the plugins in this marketplace are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.16.1] — 2026-08-21

### Fixed — Windows field incident (three measured bugs from a second machine)
- **Heartbeat stopped on the WRONG side of the backgrounding boundary.** v1.12.1 stopped
  progress at 150s, but the client deregisters the progress token at ~120s — guaranteeing
  2-3 dead-token sends per backgrounded call. macOS tolerated those few; on Windows the
  client's reaction WEDGED the stdio channel: completed results never delivered (background
  task "running" forever) and NEW tool calls never reached dispatch — one bug wearing two
  costumes. Default is now **100s** (last send ≤100s, ≥20s margin under deregistration; no
  send ever targets a dead token), each send is bounded by `asyncio.wait_for` so a blocking
  transport can never wedge the heartbeat task, and `CODEX_ORACLE_PROGRESS_MAX_SECONDS=0`
  is a documented kill-switch. Geometry pinned by test.
- **abraham now PROVES write capability before running.** Measured on Windows (codex
  0.147.0): `--sandbox workspace-write` was accepted and the run exited 0 while codex's own
  tool router rejected every write ("writing is blocked by read-only sandbox; rejected by
  user approval settings") — the restricted-token sandbox couldn't be built, and a write
  run that wrote nothing looked like success. First abraham dispatch per server process now
  runs a sealed low-effort probe in a temp git repo; the verdict is STRUCTURAL (probe file
  exists on disk — flag acceptance and exit code are union claims), the refusal quotes the
  measured marker, probe-infra failures fail CLOSED, and
  `CODEX_ORACLE_SKIP_WRITE_PROBE=1` is the hand-verified escape hatch. The probe argv
  shares `WRITE_SANDBOX_ARGS` with the real implementation phase (single source, parity
  pinned by test).
- **abraham journals a dispatch tracer at function entry**, so "the call never entered the
  function" vs "entered and stalled later" is decidable from `runs.jsonl` alone — the
  distinction this incident's diagnosis needed. Evidence-only journal groups
  (dispatch tracers, probe verdicts) are excluded from the resume listing (`has_start`).
- **Round-2 review hardening (2 CRITICAL + 4 HIGH, each verified before fixing):** the probe
  child now gets `stdin=DEVNULL` — it previously inherited the MCP JSON-RPC stdin, which
  codex exec APPENDS TO ITS PROMPT (session corruption + client-traffic leak; the real
  runner already guarded this). The heartbeat deadline is REQUEST-scoped, not per-subprocess
  (`_heartbeat_loop` extracted, `request_started` threaded through both abraham phases) —
  a per-run clock resurrected dead-token sends in phase 2. `WRITE_SANDBOX_ARGS` now seals
  `windows.sandbox="unelevated"` explicitly: in codex 0.147 the Windows sandbox mode
  defaults to DISABLED (WorkspaceWrite silently downgrades to ReadOnly) and the enable
  lives in the user config our `--ignore-user-config` strips — key calibrated on the
  installed binary (accepts elevated/unelevated, rejects others; non-Windows parses and
  ignores). Resumed write runs pass the same capability gate as fresh dispatches. The probe
  verdict is tri-state: capable/incapable are cached, but a no-file-no-marker outcome
  (auth, rate limit, CLI failure) is INCONCLUSIVE — refused but re-probed next dispatch.
  Probe timeouts reap the process tree; concurrent first dispatches single-flight the
  probe; the dispatch tracer journals off-thread; env knobs validate finite/positive;
  `check()` now raises so pytest can no longer report green over failing checks (that
  exact blindness hid 5 failures this round).
- **Round-2b (2 more CRITICAL + 2 HIGH, verified in upstream source before fixing):**
  `windows.sandbox` is sealed **"elevated"**, not "unelevated" — the unelevated backend
  only injects proxy/offline env vars (advisory; a raw-socket child ignores them,
  reproduced upstream in codex#35940), while real firewall enforcement is tied to the
  elevated sandbox identities; abraham's air-gap promises OS-enforced no-egress, so a
  machine that can't run the elevated sandbox fails the probe and abraham refuses (fail
  closed). `codex_resume_run` anchors `request_started` at entry — its capability gate can
  spend up to the probe timeout before codex spawns, and the un-anchored call recreated
  dead-token sends. The probe spawns in its own process group and reaps on ANY
  BaseException (CancelledError bypassed the old `except Exception`; the npm shim forwards
  INT/TERM but not KILL). The probe now runs INSIDE the target workspace (`.abraham/`,
  removed afterwards) with verdicts cached per normalized workspace — a green on some
  other volume proves nothing about this repo's ACLs; inconclusive verdicts TTL-cache
  (60s) so a dispatch burst shares one probe instead of serializing three; git setup
  dropped from the probe (the argv carries --skip-git-repo-check). Journal writes are
  thread-serialized (the tracer runs via to_thread). Env geometry is clamped to the safe
  envelope (MAX + INTERVAL ≤ 115s) so no knob combination can restore dead-token sends.
  Acknowledged, not built: incremental ring-buffer capture of probe output (the low-effort
  probe's output is trivially small; communicate + tail-slice retained).
- **Round-3 (the sealed writer's environment + kill-tree honesty):** `WRITE_SANDBOX_ARGS`
  now also seals `shell_environment_policy.inherit="core"` +
  `ignore_default_excludes=false` — codex 0.147 defaults shell children to inherit the FULL
  parent environment with secret-name filtering off, so a prompt-injected phase-2 command
  could read API keys into tool output (both keys calibrated on the installed binary; this
  hardening gap predates this changeset — it shipped with abraham 1.13.0 and two advisors
  passed it then). `_kill_tree` on Windows now falls through to `proc.kill()` unless
  `taskkill` actually exited 0 (access-denied exits nonzero without raising and previously
  left the root alive); the probe's reap is bounded. The inconclusive TTL uses the
  monotonic clock. Refusal messages name the elevated backend and no longer claim every
  failure "exits 0".
  **Declined with reasons, for the record:** probing raw-socket/credential denial via a
  model-driven probe (delegated to codex's elevated backend — a model-compliance-dependent
  denial probe is theater); entry anchors for the four advisory tools (their pre-spawn
  setup is in-memory string work, milliseconds against a 15-20s envelope margin);
  cross-process journal file locks and git-toplevel canonicalization of lock/journal
  identity (pre-existing 1.13.0 design, noted as follow-ups); executable-fingerprint cache
  keys (a reconnect re-probes); transport-flush acknowledgment for heartbeats (the
  envelope margin is the mitigation; disabling heartbeats would resurrect the 30-min
  idle-abort).

## [1.16.0] — 2026-08-21

### Added
- **abraham `cwd` parameter** — target a git repo BELOW the server's workspace root (for
  multi-repo project roots that are not themselves work trees); fenced to the workspace
  subtree, threaded as `workdir` through `_run_codex`/`_exec_codex_once`, journaled per
  run; the resume listing scopes by workspace subtree. codex-oracle 1.16.0. (Shipped from
  a second machine; folded into this changelog during the 1.16.1 rebase.)

## [1.15.0] — 2026-08-16

### Removed — Antigravity, per user ruling ("it's not a better model")
- **The `antigravity` plugin is deleted from the marketplace and the tree.** Codex Oracle is
  now the SOLE cross-model advisor; its verdict still governs, measurement still outranks it,
  and the Independence Protocol (blind dispatch + `caller_hypothesis`) is unchanged — it now
  guards the Claude↔Codex pair instead of a two-advisor panel.
- Every dispatch surface rewritten to single-advisor: the agent-teams skill (advisory section,
  all phase templates, team patterns, checklist), the codex-planning and codex-review skills,
  the code-reviewer agent, the plan/push hooks (the push gate now requires a completed Codex
  review; the two-advisor parallel-dispatch requirement is gone), the live-view nudge and
  mcp-live (codex log only), the hooks matcher, and the MCP instruction block.
- `tests/test_anchor_lint.py` now tests the codex-oracle server only; the cross-plugin parity
  checks (pattern-table and capability-hunt drift) died with the second copy.
- codex-oracle 1.11.0, software-workflows 1.8.0.

### Hardened — the push gate became a real gate (two Codex review rounds, findings probed live)
- **Signed answers.** server.py now stamps every answer header with
  `tool:<name> | status:<ok|timeout> | tree:<12-hex workspace digest>` (`_answer_sig` /
  `_workspace_digest`: HEAD + tracked diff + porcelain status). The push gate verifies the
  signature instead of sniffing shapes, which closes four probed forgeries at once: a TIMEOUT
  partial that carries the answer header (status:timeout), another tool's answer whose text
  mentions code_review (tool:codex_query), a review of an older tree (digest mismatch →
  STALE), and transcript pollution from reading the gate's own source. Pre-signature answers
  (an old server still running before `/mcp` Reconnect) fail verification, so the gate asks —
  the safe direction; reconnect self-heals it.
- **The gate now actually gates**: `permissionDecision: "ask"` (+ the same text as
  `additionalContext` so the model sees it too) instead of an additionalContext-only nudge
  that could not stop a call already executing. Known limitation, disclosed: the digest is
  computed at answer time, so edits made *while* the review runs are not distinguished from
  the reviewed state — binding covers everything after the answer.
- **Command detection is over-broad by design** (`git … push|commit` within one shell
  segment): catches `npm test && git push`, `git -C /repo push`, `FOO=1 git commit`; a false
  positive costs one permission prompt, a false negative skipped the gate entirely.
- **Foreground MCP wrappers decoded** (`{"result": "..."}` / list shapes) — real transcripts
  wrap results, and the previous startswith check missed them.
- **`tests/test_push_gate.py`** pins all of the above (19 cases), including behavioral parity
  between the gate's and the server's digest twins, on real measured JSONL entry shapes.
- **Round-3 fixes (probed):** `status:ok` is now EARNED by exit 0 — a failed run with partial
  output stamps `status:error` and never opens the gate; the digest voids itself (`unknown`)
  if ANY git command fails instead of hashing partial state; opening requires the structural
  dispatch leg AND the digest leg (a forged tool_result alone is insufficient); pushes that
  redirect to another repository (`-C`/`--git-dir`/`GIT_DIR=`) always ask, since the cwd
  digest cannot vouch for them; the venv marker includes the interpreter identity and is
  written atomically.
- **Declared trust model** (in the gate's docstring): this is a guardrail against FORGETTING,
  not a security boundary — any Bash-command-text hook is bypassable by a deliberately
  evasive agent by construction, so keyed signatures would add ceremony, not security.
  Disclosed residuals: edits made while a review runs are stamped into the answer-time
  digest; whether the installed host enforces `permissionDecision: "ask"` is unverified
  upstream (anthropic/claude-code#81041) — additionalContext duplication keeps the previous
  nudge as the floor. A recovered completed run (resume fast path) returns its stored result
  without a fresh signature and therefore asks — the safe direction.
- **Plugin-scoped tool naming**: the code-reviewer agent allowlists both
  `mcp__codex-oracle__code_review` and `mcp__plugin_codex-oracle_codex-oracle__code_review`
  (plugin-bundled servers expose the scoped name; a user-scope server the bare one) and drops
  the unsupported `mcpServers` frontmatter; the agent-teams skill documents the same naming
  rule; `git diff HEAD` replaces plain `git diff` (which silently omits staged changes) in
  the agent and the codex-review skill.
- **`renames: {"antigravity": null}`** in marketplace.json migrates existing installs off the
  removed plugin (Claude Code ≥ 2.1.193); README now states the real version floor (2.1.178+
  for implicit agent teams).
- **run_server.py rebuilds the venv on requirements changes** (sha256 marker; installs with
  `--upgrade` so a re-install moves within the declared constraint range) — previously a
  deployed venv kept its original dependency set forever, and the maintained-checkout fast
  path could mask a shipped dependency change.

## [1.14.0] — 2026-08-15

### Added
- **Caller-side internet channel layer (agent-reach integration).** The agent-teams and
  codex-planning skills now teach a fetch discipline for sources beyond advisor web search
  (YouTube subtitles, RSS, Exa semantic search, Jina-rendered pages): the ORCHESTRATOR or a
  teammate runs the `agent-reach` CLI, curates the output, and passes it to Codex/Antigravity
  as cited context data. Optional — an absent CLI changes nothing. software-workflows 1.7.0.

### Architecture decision (measured)
- The alternative — a networked "research sandbox" so Codex could fetch for itself — was
  REJECTED by measurement on the installed binary: codex 0.147.0 has **no mechanism for
  network egress without full disk read**. `--strict-config` rejects `sandbox_permissions`
  as *"unknown configuration field … in -c/--config override"* (identically for a
  real-looking and a bogus token — the KEY is unknown, not the value); those tokens exist
  only in main-HEAD test harnesses, not in the `rust-v0.147.0` config surface. Any
  networked posture on 0.147.0 reads everything, and untrusted web content + full-disk
  read + egress in one process is an exfiltration triangle. Caller-side fetch preserves
  the advisor context boundary and abraham's air-gap unchanged.
- `agent-reach doctor` verified in source to report **local readiness, not reachability**
  (its web channel's `check()` returns "ok" unconditionally) — the skills now say so, and
  channels count as unverified until a fetch succeeds in-session.

## [1.13.1] — 2026-08-15

### Added
- **`scripts/codex_src.py` — tag-matched upstream source reference (map vs territory).**
  Keeps a stable worktree (`~/Documents/codex-installed`) checked out at
  `rust-v<installed codex version>`, re-aligning itself after every CLI update; the base
  clone is never touched, dirty reference trees are refused loudly, deleted worktrees
  self-heal via prune. Motivation (measured): the clone's main-branch HEAD is DIVERGENT
  from the `rust-v0.147.0` release tag — releases are cut on branches — and reading the
  wrong ref has produced wrong conclusions twice in this repo's history ("verify the
  backend you run, not the source you read"). codex-oracle 1.10.1: the codex-review and
  codex-planning skills now require stating WHICH ref was read whenever upstream source is
  part of the evidence, and confirming load-bearing claims against the running binary.

## [1.13.0] — 2026-08-14

### Added
- **`/abraham` — write mode for codex-oracle (plugin 1.10.0), as two air-gapped phases.**
  One `abraham` MCP tool + `commands/abraham.md` slash command; the Claude session stays the
  orchestrator (dispatch → monitor → review the diff). Phase 1 ANALYSIS is read-only — this
  is where `infra` (live SSH/DB/logs + user MCP servers) and `web_search` apply — and
  produces an implementation brief. Phase 2 IMPLEMENTATION is **sealed**: workspace-write
  file access and nothing else — no network, no web search, no user MCP servers — so
  untrusted web content and live credentials never share a process with write capability.
  (The air-gap was demanded independently by BOTH cross-model reviewers; the original
  single-process write+infra design was rejected in review and rebuilt.)
  - **Mode algebra:** read and write are separate tools — structural mutual exclusion.
    `infra`/`web_search` compose with abraham by governing its analysis phase; the write
    phase is always sealed, and a write process NEVER uses `danger-full-access`.
  - **Sandbox facts probed live on codex-cli 0.147.0, calibrated both ways:** read-only
    refused the very write workspace-write then performed; sealed egress measured (`curl`
    cannot resolve DNS); `--strict-config` rejects a bogus key (exit 1) and accepts
    `model_auto_compact_token_limit` — recognition, not a permissive parser. `/tmp` and
    `$TMPDIR` are excluded from the writable roots (an artifact there would outlive the run
    OUTSIDE the reviewed diff); build tools get a TMPDIR redirected to a workspace-local
    `.abraham/tmp` instead.
  - **Git safety:** refuses outside a git work tree (autonomous writes with no undo);
    refuses a DIRTY tree unless `allow_dirty=true` (the implementer may legitimately rewrite
    files, and uncommitted edits it touches are unrecoverable — committed work never is);
    one-writer-per-tree via an authoritative O_EXCL lockfile held across both phases
    (pid-liveness stale-break so a crashed server never blocks its own recovery), plus the
    journal-liveness check as an advisory belt; codex is contract-bound never to
    commit/push/reset; every outcome — success, timeout, hang, error — ends with a
    **[CHANGED FILES]** report separating this run's changes from pre-existing dirt and
    verifying HEAD did not move (a violation is called out loudly instead of trusted).
  - **Write runs are never auto-retried** (review CRITICAL): replaying "implement X" after
    half of X was written double-applies. Recovery is the explicit `codex_resume_run` path,
    which resumes write runs SEALED regardless of caller overrides; read runs cannot
    escalate to write by resuming. Read runs keep their transient auto-retry.
  - **Auto-compaction at ~65% (owner-specified 60–70%) for the implementation phase**, via
    `-c model_auto_compact_token_limit`. The window comes from the deployed binary's own
    registry (`models_cache.json` — `gpt-5.6-sol: 272,000` measured 2026-08-14; 65% =
    176,800), with `CODEX_ORACLE_CONTEXT_WINDOW` / `CODEX_ORACLE_AUTOCOMPACT_PCT` env
    overrides; unknown model → flag omitted so the vendor's 90% default governs, and the
    chosen branch is recorded in the live-log header. Two measured traps drove this: a
    user-set limit beats the vendor default OUTRIGHT (no min with the window — a too-high
    limit would silently never fire), and the first draft's recalled 400k window would have
    put "65%" at 95.6% of the real 272k window (caught by the owner mid-build).
  - 84-check dependency-free suite (`tests/test_write_mode.py`): sealed-argv matrix,
    auto-compact derivation incl. corrupt-cache and env precedence, changed-files set math,
    lockfile semantics incl. dead-pid break, no-retry-for-writes (and read-retry
    regression), two-phase orchestration incl. analysis-failure stop, resume inheritance.
  - Full design + probe record + review reconciliation: `PLAN_ABRAHAM_WRITE_MODE.md`.

## [1.12.1] — 2026-08-09

### Fixed
- **The progress heartbeat was getting the whole MCP server killed — taking every sibling in-flight run with it.** MEASURED from the client log 2026-08-09: Claude Code moves an MCP call to a background task at ~120s and **deregisters that request's progress token**. The 10s heartbeat kept sending on it, so every tick came back as `Connection error: Received a progress notification for an unknown token`, and after enough of them the client killed the server — `SIGINT failed, sending SIGTERM to MCP server process` — ending unrelated concurrent calls with `Tool 'architect_review' failed after 269s: MCP error -32000: Connection closed`. One long backgrounded call could therefore kill a whole batch of parallel advisory runs.
  - Heartbeats only ever existed to hold off the client's 30-minute idle-abort **while it is waiting** on the call. Once the call is backgrounded the client stops waiting (it gets a completion notification instead), so further progress is useless *and* actively harmful.
  - Both servers now stop heartbeating at `PROGRESS_MAX_SECONDS` (default **150s**, comfortably past the ~120s backgrounding threshold, env-tunable) and RECORD the stop in the live log rather than going silently quiet. The live log keeps streaming every event, so nothing observable is lost — `mcp-live` / `tail -f` is unaffected.
  - 11 heartbeat tests: the loop ends by itself, sends stop at the bound, the guard and its log line are present in both shipped servers, and both defaults clear the threshold.

## [1.12.0] — 2026-08-09

### Changed
- **Codex is now the declared PRIMARY advisor; Antigravity is SECONDARY — and the workflow WAITS for Codex.** The two were documented as peers, which produced a real failure mode: Codex runs at max reasoning and its long calls get backgrounded, Antigravity answers in a fraction of the time, and work proceeds on the fast answer as though the review were finished. **Returning first is not being right.**
  - **Codex Oracle = PRIMARY/authoritative** on review, architecture, research, web research and synthesis (strongest OpenAI model at max effort, repo access, live web search). **Its verdict governs.**
  - **Antigravity = SECONDARY/corroborating.** Never ship, commit, or declare a decision on its answer alone.
  - **Never conclude while Codex is still running.** A backgrounded Codex call returning later as a task notification is NORMAL — block on it (Monitor / notification) and do other work meanwhile.
  - **On disagreement Codex carries** — unless MEASURING the deployed system disproves it. Measurement outranks both models (Codex has been wrong reading newer upstream source instead of the installed binary).
  - **An Antigravity-only finding is still real** and must be verified, never discarded because Codex didn't mention it. Both directions of miss are documented: Codex caught a secret-exfiltration CRITICAL Antigravity missed; Antigravity caught a symlink-traversal CRITICAL Codex missed — in the same review round. The secondary earns its slot by independence, not precedence.
  - Applied at every surface the model actually reads: both MCP servers' `instructions` (loaded at tool-listing time — Antigravity had none at all before), the `agent-teams` skill, `codex-planning`, `codex-review`, and the `code-reviewer` agent (whose verdict line must now read "INCOMPLETE — Codex did not return" rather than a ship/no-ship call).

### Fixed
- **The push gate was passing on a half review.** It checked whether `code_review` and `antigravity_review_pr` appeared in the transcript — but a tool NAME is present the moment the call is MADE, so a backgrounded Codex that never returned still satisfied it. The gate now requires an actual Codex RESULT (its `[Codex model: …]` header, or a terminal TIMEOUT/health-check outcome) and, when the call was dispatched but never came back, says exactly that: Codex has not returned, do not push on Antigravity alone, wait. Stays advisory and fail-open. 11 gate tests cover silent/generic/pending/complete/terminal/malformed.

## [1.11.0] — 2026-08-09

### Added
- **Curated project context for both advisors — `ADVISOR_CONTEXT.md`.** Drop an `ADVISOR_CONTEXT.md` at your repo root (or anywhere up to `$HOME`) with the architecture/conventions notes you're happy to send to an external model, and every codex-oracle and antigravity call is prefixed with it. This is the ONLY file the wrappers inject. Absent file = nothing sent (default CLOSED). Live-verified on both engines.

### Security
- **The obvious version of this feature would have exfiltrated your secrets — it was built, caught in review, and rebuilt.** The first implementation injected `CLAUDE.md`/`AGENTS.md` plus "facts-only" project memory. Verified against a real workspace: that `CLAUDE.md` carries **live SSH/DB passwords in its first 3 KB** (and says *"never copy into any repo"*), and `type: project` memories carry credentials too — so it would have shipped live secrets to OpenAI **and Google** on every call, and written them to world-readable logs. `metadata.type` is a *factuality* boundary, never a *confidentiality* one. Both injectors were removed entirely; only the curated file remains.
- **`ADVISOR_CONTEXT.md` is symlink-hardened.** A hostile repo could otherwise ship `ADVISOR_CONTEXT.md → ~/.ssh/id_rsa` (or `→ CLAUDE.md`) and exfiltrate the target, since `Path.is_file()` follows symlinks. Symlinks are refused, the resolved path must stay inside the directory it was found in, the read is bounded, and the upward walk stops at `$HOME` (outside `$HOME` only `cwd` is searched, so a planted `/tmp/ADVISOR_CONTEXT.md` is ignored).
- **Logs, journals, stream files and stored results are now `0600`, their directories `0700`** (were `0644`/`0755`). They contain full prompts, model output and command output — other local users could read them.
- **No `CLAUDE.md` project-doc pin.** MEASURED twice on codex 0.144.1: codex reads `CLAUDE.md` natively and NEITHER `project_doc_max_bytes=0` NOR a `project_doc_fallback_filenames` override suppresses it. That codex→OpenAI exposure is pre-existing and outside this wrapper's control; we no longer pin it, so we never add to it. (A newer upstream source commit suggests it is only a configurable fallback *there* — the deployed binary behaves otherwise. Verify the backend you run, not the source you read.)

### Deferred
- **MCP-forwarding (Claude's tools → codex) is NOT in this release**, after being built twice. Both reviewers independently established the blocker is inherent rather than a wrapper bug: to give codex your MCP tools, those servers' credentials must be readable by a codex process that can be prompt-injected, and codex's own recommendation is a sanitized container plus a capability broker — a different product. The secure-transport work (0600 config in a private `CODEX_HOME`, additive config merge preserving proxies/TLS/providers, fail-closed prep, resume-home retention) is kept for a focused round.
## [1.10.0] — 2026-08-09

### Added
- **Live view autostarts (codex-oracle 1.6.0).** Pressing Enter on a backgrounded advisory MCP task shows nothing — MCP tasks carry status pings, not a stdout stream, and only agent-launched SHELLS get an Enter-to-view buffer (host limitation; the reason mcp-live exists). Previously the user had to know to ask for mcp-live per session. Now a PreToolUse hook on `mcp__.*(codex-oracle|antigravity).*` injects an instruction to start the mcp-live tail as a background shell the moment any advisory call begins — scoped to sessions that actually use the advisors (no standing SessionStart noise), idempotent (one shell per session), and in place while a >120s call is still running, since backgrounding returns control mid-turn. A hook process cannot own a Shells-panel row itself, so hook→additionalContext→agent-starts-shell is the only mechanism that yields the panel-native live view.

## [1.9.0] — 2026-08-08

### Added
- **Native Windows support for both advisory MCPs** (codex-oracle 1.5.0, antigravity 1.5.0). The stack was POSIX-only in five verified places; a fresh Windows 11 machine now works end-to-end:
  - **Launcher.** `.mcp.json` pointed at `run-server.sh` — Windows cannot exec a `.sh` file at all, and a Windows venv keeps its interpreter at `.venv/Scripts/python.exe`, not `.venv/bin/python`. Both plugins now launch `run_server.py` via `python` (exec-form args; `${CLAUDE_PLUGIN_ROOT}` expansion unchanged). Same behavior as the shell version — marketplace-checkout venv fast path, cold venv bootstrap with all output on stderr — on both platforms. `run-server.sh` is deleted: one launcher, no drift.
  - **codex argv[0].** npm installs only `.cmd`/`.ps1` shims on Windows, and `CreateProcess` cannot resolve a bare `codex` (WinError 2 — MEASURED on codex-cli 0.147.0: the spawn died before the server's own install-hint path could even fire). `_codex_argv0()` resolves the shim PATHEXT-aware via `shutil.which` and prefers the vendored native `codex.exe` (keeps untrusted prompt text away from cmd.exe argument parsing), falling back to node + `codex.js`, then the full-path shim (measured spawnable when given a full path).
  - **PATH corruption.** The codex spawn env unconditionally prepended `/opt/homebrew/bin:` — on Windows, where the separator is `;`, that silently corrupted the first real PATH entry. Homebrew prepend is now macOS-only and uses `os.pathsep`.
  - **agy resolution.** The Windows installer places `agy.exe` at `%LOCALAPPDATA%\agy\bin` and only updates the User PATH *registry* — a process started before the install (or before a terminal restart) never sees it, so a bare PATH lookup misses agy for the whole session (observed live). `_resolve_agy()` now checks the per-platform install dir absolutely (`~/.local/bin` POSIX, `%LOCALAPPDATA%\agy\bin` Windows) before any PATH lookup, and `_build_environment` prepends the same dir.
  - **Hooks.** The sh+jq one-liners (POSIX-shell-only) became `hooks/plan_gate.py` + `hooks/push_gate.py`, wired in exec form (`command` + `args`) so NO shell parses them on either platform. Gate message text unchanged; the jq dependency is gone on POSIX too. `push_gate.py` stays fail-open: malformed payloads or unreadable transcripts never block the tool call.

### Changed
- **POSIX launcher requirement.** The MCP command is now `python` (≥3.11) — plugin `.mcp.json` has no per-platform command mechanism, so one name must resolve everywhere. On python3-only systems (stock macOS), point `python` at python3 (pyenv, or an alias/symlink) — the changelogged alternative was keeping `.sh` and having no Windows story at all.

### Unchanged by design
- `latest.log` symlinks were already `contextlib.suppress(OSError)`-guarded ("observability must never break the run") — on Windows without Developer Mode the symlink is silently skipped; `stream.log` and the per-run files remain the live view.

## [1.8.0] — 2026-08-08

### Added
- **Image references to both advisors.** Pass local image paths (UI screenshots, diagrams, error dialogs) and the model views them — visual context beats prose. codex via native `-i FILE`; agy via its own file tools. Verified live: both read agent names straight off a real screenshot. Missing paths are rejected before any spend. On `architect_review`, `code_review`, `research`, `codex_query`, and `antigravity_query`.
- **CLAUDE.md as project context for codex** — pinned via `project_doc_fallback_filenames` (survives `--ignore-user-config`; codex also reads it natively — measured both ways).

### Fixed
- **Default-mode MCP isolation — codex was silently starting all ~12 user MCP servers on every "MCP-free" call.** MEASURED on 0.144.1: `-c mcp_servers={}` (used for this since forever) is a NO-OP — `codex mcp list -c mcp_servers={}` still shows every server, and default runs emitted rmcp worker/auth-failure lines (the noise seen in the live logs). Switched default mode to `--ignore-user-config`, which starts ZERO MCP servers, still honors the explicit `-c`/`--model` overrides, and still reads CLAUDE.md (all verified live: MCP-worker noise 1 → 0). Infra mode deliberately keeps the user config so codex has its own tools. Faster default calls, no more auth-hang noise. Surfaced by codex-oracle's own review of this change.
- **Output cap lowered to 60K chars (env-adjustable), full answer preserved.** MCP results stay in the caller's context all session (measured: antigravity 18% / codex 8%+ of a session's usage). The complete answer is still saved to the per-run `.result.txt`, and the truncation notice keeps the live-log pointer. A malformed cap env value now falls back instead of crashing the server at import.

### Reviewed
- Both changes went through parallel codex-oracle + Antigravity review; every CONFIRMED CRITICAL/HIGH was addressed before ship:
  - **MCP-forwarding into codex was PULLED** — both reviewers independently showed the design was unsound: `-c mcp_servers={...}` deep-merges rather than replaces (so the exclusion set could exclude nothing) and forwarding server `env` through argv exposes credentials. It returns later built correctly (config-file transport + `--ignore-user-config` isolation + 0600), tracked as an open item.
  - **agy image handling hardened** — `--add-dir` on a real image would have granted its entire parent directory (a screenshot in `$HOME` → `$HOME`). Images are now copied into a private per-run `0700` staging dir that is granted alone and removed after the call.
  - **Cross-workspace disclosure in resume closed** — the run journal is global; `resume`/`list` now scope to the current workspace and refuse an explicit run id from another workspace, so an untrusted repo can't retrieve another project's answers.
  - codex image paths resolved to absolute (a `-`-leading relative name can't be parsed as a flag); lone-string `images` coerced to a list.

## [1.7.0] — 2026-08-08

### Added
- **Failures resume instead of restarting — the advisors now behave like sessions, not one-shots.** A codex/antigravity call that died (transient API error, MCP-server restart, plugin reload, cancelled call) previously lost everything and had to be re-asked from scratch, which is both expensive and lossy at max effort. Now every run is journaled with its VENDOR SESSION ID and can be continued with its original context:
  - **Automatic in-call retry.** A transient failure (stream disconnect, 5xx, 429, timeout, agy's missing-result-event) is retried up to twice by RESUMING the same vendor session — the caller never sees it, and the answer carries a `[note: recovered automatically…]` line. Auth errors, argument errors, and capacity/quota (already handled by the Flash fallback) are deliberately NOT retried: retrying can't fix them.
  - **`codex_resume_run` / `antigravity_resume_run` tools** for what retries can't cover — a run killed with the server. Both take an optional run id (printed in every failure message as `[recoverable: …]`) or default to the most recent recoverable run. **A run that had already finished returns its stored answer instantly with no model call** (measured: 0.01s vs 31.6s).
  - **codex resumes genuinely mid-run.** `--ephemeral` is gone (it discarded the session); resume uses `codex exec <opts> resume <thread_id> <nudge>` — argv grammar read from `openai/codex` `exec/src/cli.rs` (parent options MUST precede the subcommand) and verified live. Proven: a run SIGKILLed mid-reasoning was recovered **by a different server process**, with both its codeword and its interrupted computation intact.
  - **AMNESIA GUARD.** Measured: `codex exec resume` with an unknown id silently starts a fresh, context-less thread and still exits 0. Every resume therefore verifies the resumed `thread.started` id equals the expected one and fails loudly on mismatch — a resume must never return a confident answer written without the context it claims to have.
  - **agy resume is fail-safe by construction.** Measured: agy recalls a COMPLETED turn, but a mid-turn kill loses the context while returning the SAME conversation id — so no mismatch signal exists. Its continuation prompt therefore always re-states the full original request: context carried → the model continues; context lost → it still answers the real question correctly.
  - Session ids are journaled the INSTANT they stream in, not when the attempt returns — regression-caught by the kill-then-recover test, which is exactly the case where the late write never happened.

## [1.6.2] — 2026-08-08

### Added
- **Merged live stream — concurrent runs are no longer invisible.** `latest.log` follows only the newest run, so parallel advisors (multi-agent fan-out) scrolled unseen in their own files. Every run now ALSO appends its lines to a per-server `stream.log`, tagged with a run id (`[codex5·21746]`); O_APPEND interleaves runs chronologically across sessions and processes. `mcp-live` now tails the two `stream.log`s — ALL concurrent runs in one view (verified live: 2 codex + 2 agy storm, both tags interleaved per server). Truncated at run-start past 128 MiB with a loud notice (it duplicates the per-run archive, so nothing durable is lost); per-run files and `latest.log` unchanged for single-run focus.

### Fixed
- **Plugin-cache MCP registration ENOENT'd on every version bump.** `.mcp.json` pointed at `${CLAUDE_PLUGIN_ROOT}/.venv/bin/python`, but Claude Code materializes plugins into versioned cache copies where the gitignored venv never exists (observed failures for 1.0.1/1.1.0/1.2.1; sessions survived only via the direct ~/.claude.json registrations). Both plugins now launch via `run-server.sh`: reuses the marketplace checkout's venv when present, otherwise bootstraps a venv (python ≥3.11, all output to stderr — stdout stays a clean JSON-RPC channel). Proven on both paths, including a fresh-HOME cold bootstrap.
- **mcp capped `<2.0.0` in both requirements.** The cold-bootstrap test pulled the brand-new mcp 2.0.0 and the server died at import (`'Server' object has no attribute 'list_tools'` — 2.0 removed the 1.x low-level decorator API). Floor stays at measured-working 1.26; the 2.x port is a deliberate future migration. This would have broken every fresh install.
- **Cancelled runs are labeled honestly.** A caller-aborted MCP call (Esc / turn end) logged `run finished: exit=None`, which read as a defect in the 08-08 log sweep; it now says `exit=none (cancelled by caller)`.

## [1.6.1] — 2026-08-08

### Added
- **`mcp-live` — Enter-to-view for MCP runs via the Shells panel.** Claude Code's background panel opens output buffers only for SHELLS (MCP tasks carry status + progress pings, no stdout stream — native task mode is still an open host feature request). `plugins/software-workflows/scripts/mcp-live` bridges the gap: one command that `tail -F`s both servers' `latest.log` feeds (follow-the-name verified across per-run symlink repoints). Run it as a background shell ("run mcp-live in the background") and the Shells row's Enter-to-view IS the live reasoning/search/command feed of both advisors. Also installable to `~/.local/bin`.

## [1.6.0] — 2026-08-08

### Added
- **Live view for both advisory MCPs — watch the model think, search, run commands, and fail, in real time.** Until now a codex/antigravity call was a black box: nothing visible until the final answer or a timeout. Both servers now stream the run's full event feed to a per-run log with a stable `latest.log` symlink:
  - `tail -f ~/.claude/logs/codex-oracle/latest.log` and `tail -f ~/.claude/logs/antigravity/latest.log`
  - **codex-oracle (1.2.2):** switched to `codex exec --json` (JSONL ThreadEvents; schema read from the `openai/codex` repo at `8e4b104` and verified against the installed 0.144.1 binary) plus `-c model_reasoning_summary=detailed` — the previous runs had *"reasoning summaries: none"*, i.e. no thinking output existed to watch. The log carries reasoning summaries, web-search queries, command executions with exit codes, MCP tool calls, errors, and token usage. The 10s progress heartbeat now shows the CURRENT ACTIVITY ("thinking: …", "web search: …", "exec: …") instead of a byte counter. Final-answer extraction is unchanged (`--output-last-message` verified to work alongside `--json`); the JSONL fallback is the last parsed `agent_message`, never raw event soup. Every result and every error now ends with the live-log path.
  - **antigravity (1.2.3):** switched to `agy --output-format stream-json` (documented contract: one `init`, N `step_update`, exactly one `result` — verified incremental on agy 1.1.11). The final answer is extracted from the terminal `result` event; a stream that ends WITHOUT one is a loud failure, never returned as if it were an answer; a non-SUCCESS terminal status is propagated even when agy exits 0. Added a 10s MCP progress heartbeat (low-level Server plumbing via the request context; silently a no-op for clients that send no progressToken). **Forward/backward agy compatibility:** unknown event shapes degrade to raw JSON lines (never a crash), and an older agy that rejects `--output-format` triggers a one-time downgrade to plain-text mode for the process lifetime (classifier calibrated on reject/non-reject texts).
  - Concurrency-safe for multi-agent fan-out: per-run files named `<timestamp>-p<pid>-<seq>-<label>.log`, per-call state, context-var-scoped progress. Verified live with a 4-way storm (2 codex + 2 agy in parallel — 4 correct answers, 4 distinct logs). Logs are pruned after 7 days.
  - Verified end-to-end: 32/32 checks including live codex (gpt-5.6-sol @ max — the effort pin re-verified against a config file that had drifted to xhigh again), live agy Pro-High, chunk-boundary JSONL parsing via a stub binary, the old-agy downgrade path, and the missing-result-event failure path.

## [1.5.2] — 2026-08-07

### Fixed
- **antigravity (1.2.2): model discovery broken by the agy 1.1.x `models` format change.** `agy models` now emits tab-separated ``slug\tDisplay Name`` lines (e.g. ``gemini-3.1-pro-high\tGemini 3.1 Pro (High)``); the old parser kept the whole line as the model name, so every query died with `invalid model selection (--model "gemini-3.1-pro-high\tGemini 3.1 Pro (High)")`. The failure was silent-by-accident: the parens filter and the ranking regex both matched the *display half* of the line, so the picker "worked" and handed agy a tab-joined string. Now:
  - The registry stores ``(slug, display)`` pairs — ranking parses the DISPLAY name (where the thinking depth lives), ``--model`` gets the SLUG. Measured on agy 1.1.11 headless (`agy -p --model …`): both slug and display name are accepted; slug is canonical (space/paren-free). Old display-only output still parses (slug == display), so downgrade-compatible.
  - Constructor defaults are now slugs (``gemini-3.1-pro-high`` / ``gemini-3.6-flash-high``), used only until the first live discovery.
  - **Self-heal for the next lineup change:** agy rejects an unknown model with a fast exit 1 *before any spend*, so a non-pinned query that hits ``invalid model selection`` / ``not recognized as a known model`` now force-refreshes the registry once and retries with the re-discovered default. Pinned models still fail verbatim. The new classifier was calibrated on the real incident text (red) and a capacity-error text (must NOT match) before being trusted.
  - Flash fallback auto-upgraded to **Gemini 3.6 Flash (High)** (shipped 2026-07-21); the deepest reasoning tier in Antigravity is still **Gemini 3.1 Pro (High)** — Gemini 4 is announced as in pre-training, not released.
  - Verified end-to-end through the real MCP code path (`AntigravityCLIClient.query`) against live agy 1.1.11: 16/16 checks green, incl. old-format stub, no-Gemini-list default survival, and a live Pro-High round-trip.

## [1.5.1] — 2026-08-07

### Changed
- **Runtime Capability Law sharpened after it bit a second time, one level down.** Swapping the browser engine to branded Chrome was supposed to resurrect `page.pdf()` — it did not, because the browser launches **headful** and that API is **headless**-Chromium-only. Same call, correct engine, still dead for a *different reason*. The review directive now carries what that taught:
  - **Configuration gates capabilities independently of the vendor** — headful/headless, pooled/direct connection, sync/async driver, persistent/ephemeral context, edge/regional runtime, free/paid tier, emulator vs real device. "Which backend implements this?" is necessary and insufficient; the question is *which backend, in the configuration it runs in*.
  - **A capability comment recording a version but not the MODE is a union claim** — the type-stub defect in the reviewer's own handwriting. Record backend + version + mode + venue, or it is not evidence.
  - **A swap can keep an error-string classifier matching while inverting its meaning** (`"Headless Chromium" in msg` stayed true after the engine was fixed — engine fine, mode refusing). Every diagnostic that parses a vendor message is re-audited when that vendor, version, or mode changes.
  - **When a wrapper refuses, the underlying protocol may not** — the gate is often a check in the wrapper's own driver, not a limit of the engine. Flagged as a hypothesis needing a probe, never as fact.
  - **A probe that has never returned a red is not a probe** — capability checks must be calibrated against a known-bad configuration before their greens mean anything.
  - Mobile platform/native modules added to the swappable-backend list.

## [1.5.0] — 2026-08-07

### Added
- **The Runtime Capability Law — "present" is not "supported".** *A missing method fails at lint time — you find out in seconds. A present-but-unsupported method fails in production, on a real portal, on a real customer's document.* That asymmetry is now a first-class review dimension, because static analysis is structurally blind to it: type stubs, autocomplete, `hasattr` and a clean import describe the **union of every backend a library supports**, never the one actually deployed. A call can be present, type-clean, lint-clean and import-clean while being unimplemented by the engine underneath.
  - **The case that named it:** Playwright's `page.pdf()` is Headless-Chromium-only. A project switched its browser engine to Camoufox — which *is* Firefox. The method still existed and still type-checked, and raised *"PDF generation is only supported for Headless Chromium"* on **every page**, silently costing runs their bill/statement evidence **for a month** before a needs-review ask surfaced it. Nothing static would have caught it; one call on the real engine would have.
  - **Injected server-side into every review**, so the advisors hunt for it whether or not the caller remembers to ask: `code_review`, `architect_review`, `antigravity_analyze_code`, and `antigravity_review_pr` all carry the capability hunt. `architect_review` additionally treats the capability surface of every candidate backend as part of the *design* — an abstraction that assumes the union of all backends breaks on whichever one lacks a method.
  - **What reviewers are told to hunt:** calls crossing into a swappable backend (browser engine, DB driver/dialect, storage/LLM/queue provider, cloud SDK against a compatible-but-not-identical endpoint, container-provided binary); engine/driver/provider/version **swaps**, which is where this bug is born; `try`/`except` handlers that degrade a capability miss into a silent no-op **without recording which failure occurred**; parameters accepted then ignored, clamped or silently downgraded; and tests that only exercise the library's *default* backend rather than the deployed one.
  - Documented in the `agent-teams` skill (new Critical Rule 15 + a full section), the `codex-review` skill (a dedicated step), and the `code-reviewer` agent.

## [1.4.0] — 2026-08-07

### Added
- **Independence Protocol — anti-anchoring across all three plugins.** The dominant failure mode of cross-model advice was the caller (another LLM) writing up its own diagnosis, pasting it into the prompt, and asking the advisor to react. That returns a critique of the *caller's framing*, not an independent read of the evidence — and when both advisors are handed the same framing, their agreement reads like corroboration while being nothing but an echo of the caller. Independence is now enforced by the servers, not left to the caller remembering to ask for it:
  - **`caller_hypothesis` parameter** on every advisory tool (`architect_review`, `code_review`, `research`, `codex_query`, `antigravity_query`, `antigravity_analyze_code`, `antigravity_brainstorm`, `antigravity_review_pr`). It is the one correct channel for the caller's own view: rendered inside an explicit `<caller_hypothesis>` block labelled *UNVERIFIED CLAIM UNDER TEST*, with instructions to actively **refute** it and return a **CONFIRMED / REFUTED / UNPROVEN** verdict naming the evidence that decided it. `antigravity_brainstorm` goes further — it generates its ideas *before* the hypothesis is revealed, so a stated preference cannot collapse the search space, then critiques the preference against what it produced independently.
  - **Server-injected independence contract** on every prompt both servers build: reach your own conclusion from primary evidence first; treat every caller statement about cause, correctness, safety or intent as an unverified claim; investigate what the caller did *not* ask about; lead with disagreement; never agree because the caller sounded confident.
  - **Anchoring lint** on the neutral scoping fields (`context`, `concerns`, `focus`, `topic`, `prompt`). 15 conclusion-language patterns ("the root cause is", "I fixed", "confirm that", "does this look right", "obviously", …). A hit injects a stronger counter-anchoring block into the prompt **and** prepends a loud `⚠️ ANCHORING WARNING` banner to the result telling the caller their agreement is now weak evidence. Never silently strips (silent mutation of a caller's prompt is its own defect) and never blocks (that would break legitimate round-2 adversarial dispatch). On the Antigravity side the banner is prepended *after* truncation so it can never be the thing that gets cut.
  - **Mandatory "where I disagree with the caller's framing" section** in the required output format of every review/architecture tool — it must be answered, including with "none — framing held up", so silent absorption of a wrong premise is no longer possible.
- **Live web research is now real, not just requested.** `research()` had always instructed Codex to *"search the web extensively, cite URLs for every claim"* — while codex-cli's default is `web_search = "cached"`, an OpenAI-maintained snapshot index. The instruction had no mechanism behind it, so time-sensitive answers came from a cache or from training data.
  - **codex-oracle now forces `-c web_search=live` on every invocation**, including under `--sandbox read-only` — web search is a native Responses tool and does not traverse the shell sandbox (verified empirically: a read-only run retrieved a same-day GitHub release tag). Key choice verified against codex-cli 0.144.1 and the current config reference: `web_search` (top-level, values `disabled|cached|indexed|live`) is current; `features.web_search_request` is rejected as deprecated; `tools.web_search_request` is not a valid key; `tools.web_search` is the superseded legacy boolean.
  - **antigravity** now instructs the model to use its live `search_web` tool (available on every query via `--dangerously-skip-permissions`, but only *used* when the prompt asks for it — capability without a directive is capability unused).
  - Both servers require **primary sources with URLs**, mark unverifiable load-bearing claims `UNVERIFIED`, and end with a mandatory **Sources** section. Code reviews additionally verify API usage against current upstream docs and check touched dependencies for known CVEs.

### Changed
- **`antigravity_review_pr` strictness is a severity threshold, not a tone dial, and defaults to `strict`.** The old guide encoded agreeableness — `lenient: "be encouraging"`, `balanced: "acknowledge good practices"` (the default) — which is the opposite of what an independent advisor is for. Now: `lenient` = CRITICAL/HIGH only, `balanced` = adds MEDIUM, `strict` = everything including nits, and the review is blunt at every level. The sycophantic `What's Good` and `Questions for the Author` output sections are gone, replaced by a verdict, severity-ranked findings, the mandatory disagreement section, and sources.
- **`agent-teams` SKILL.md** gained a full **Independence Protocol** section (blind round 1 → adversarial round 2, a send/withhold table, five before-and-after prompt rewrites) and a **Mandatory web research** section. Two new Critical Rules (13: dispatch blind; 14: demand live web research). All three phase templates (researcher / architect / reviewer) now dispatch blind. Four new troubleshooting entries, including *"both models agreed with you and the code still broke"*.
- **Corrected an actively wrong inference rule.** `codex-review` previously said *"if both models flag the same issue — it's almost certainly real"* with no independence condition. Two models agreeing is strong evidence **only if they were dispatched independently**; agreement after the caller anchored them is one data point wearing two hats. Disagreement, by contrast, is strong evidence either way — an advisor that contradicts the caller despite being nudged toward them has found something real. Fixed in `codex-review`, `codex-planning`, the `code-reviewer` agent, and `agent-teams`.
- **Both hooks now carry the blind-dispatch rule.** The planning gate and the push gate fire at the exact moment a dispatch is about to be written, making them the highest-leverage injection point for it.

### Fixed
Found by the cross-model review of this very change — dispatched blind and in parallel, per the protocol it introduces. Every finding below was verified against the code before being accepted; two were rejected (see *Known limitations*).

- **The push-gate hook never short-circuited.** It piped stdin to `jq` twice; the second call read an already-consumed stream, so `TRANSCRIPT` was *always* empty, the "reviews already ran" branch was unreachable, and the gate nagged on every single push even immediately after a review. Proven empirically, then fixed by reading stdin once into a variable. The gate now also requires evidence of **both** a `code_review` and an `antigravity_review_pr` rather than either one, and uses `printf` instead of `echo` so a command starting with `-n`/`-e` is not eaten as a flag. Eight behavioural cases now cover it.
- **`antigravity_brainstorm`'s independence claim was an overclaim — the exact defect this release exists to prevent.** The schema said the caller's leaning was "kept out of idea generation", but it was appended to the *same* prompt behind a "generate your ideas first" instruction — and a model sees the whole prompt at once, so the preference was in context the entire time. Now genuinely two isolated calls: one generates and freezes the ideas with no knowledge of the leaning, a second critiques the leaning against that frozen set and returns the verdict. Documented as costing two model calls.
- **A curly apostrophe bypassed the entire lint.** `I’ve fixed` (U+2019) walked straight past the `I've fixed` pattern — a one-character defeat of the whole mechanism. Apostrophe variants are now normalised before matching, and five more phrasings are caught (`my diagnosis is`, `root cause:`, `the bug:`, `looks fine to me`, …). The regression test pins both curly forms.
- **Silence can no longer read as agreement.** The CONFIRMED/REFUTED/UNPROVEN verdict is a *prompt* instruction that no server can force a model to honour. When a `caller_hypothesis` was sent and no verdict comes back, both servers now prepend a loud **⚠️ NO HYPOTHESIS VERDICT RETURNED** notice instead of letting the caller assume their hypothesis survived.
- **`KeyError` on any out-of-enum value** in `antigravity_review_pr`, `antigravity_summarize`, and `antigravity_generate_tests` — an LLM caller hallucinating `"super strict"` crashed the tool and returned a stack trace. All three now fall back to their documented default. (The review found one; the same defect existed in three places.)
- **Truncation could exceed `MAX_OUTPUT_CHARS`.** The banner and the `[TRUNCATED: …]` notice are now reserved *out of* the budget rather than added on top of it, and Antigravity checks for the verdict against the post-truncation text so a verdict cut off by truncation is reported as missing.
- **`antigravity_with_tools` was skipped** by the anchoring lint and had no `caller_hypothesis` — a caller could anchor freely through it. Now consistent with the other advisory tools.
- **`additionalProperties: False`** added to the `analyze_code` / `brainstorm` / `review_pr` schemas, which previously accepted and silently ignored unknown parameters.
- **New `web_search` parameter on every codex-oracle tool** (default `True`). Live search is the right default and is why the feature exists, but a caller reviewing sensitive material can now set `False` for a genuinely offline call (`web_search=disabled` removes the tool rather than falling back to the cached index). The docstring states plainly that turning it off means version and API claims come from stale training data.
- **`tests/test_anchor_lint.py`** — 95 checks with no dependencies: 19 must-flag and 15 must-not-flag cases against **both** plugin copies, curly-apostrophe bypass cases, verdict-notice accountability, banner/neutralizer pairing, reservation-length sanity, and a **parity check** that fails if the duplicated pattern tables drift apart.
- README corrected for the v1.2.0 implicit-team migration (it still documented the removed `TeamCreate`/`TeamDelete`) and rebranded Gemini → Antigravity.

### Known limitations (deliberate, documented rather than silently accepted)
- **The lint is a heuristic, not enforcement.** It catches common phrasings; a determined or unusual framing gets through. The docs now say so explicitly — the absence of a banner is not a certificate of independence, and claiming otherwise would be the same overclaim the release is fixing.
- **The `_ANCHOR_PATTERNS` block is duplicated across the two plugins on purpose.** They are independently installable and run from separate in-tree venvs, so a shared module would make each plugin unusable without the other and unimportable across venvs. The parity test is the guard against drift; the rationale is recorded at both copies.
- **`agy` still runs with `--dangerously-skip-permissions`** (pre-existing). It is what makes `search_web` available at all, but it also auto-approves every tool the CLI offers, so a prompt-injected diff has a wider blast radius than a sandboxed reviewer would. Flagged rather than changed, because narrowing it would disable the web research this release adds.
- **The hooks advise, they do not deny.** Both emit `additionalContext` rather than `permissionDecision: "deny"`, so they are strong nudges, not enforcement. Turning them into hard gates is a behaviour change worth making deliberately.

## [1.3.0] — 2026-07-10

### Added
- **`codex-oracle` plugin now ships in this marketplace** (moved from its standalone repo, now retired): the cross-model advisory MCP server (GPT-5.6 Sol at `max` reasoning auto-detected from the Codex CLI config; `infra: true` read-only live-infrastructure investigation with project-agnostic access discovery; 60-min wall clock; partial-output recovery), plus its planning/push-gate hooks, code-reviewer agent, and codex-planning/codex-review skills.
- **`antigravity` plugin now ships in this marketplace**: the Gemini advisory MCP server wrapping the Antigravity CLI (`agy`) with automatic strongest-Pro model selection, packaged with a plugin manifest and portable `${CLAUDE_PLUGIN_ROOT}` MCP config for the first time.
- Marketplace description updated: the orchestration skill and both cross-model MCPs it integrates with are now distributed together.

## [1.2.1] — 2026-07-10

### Changed
- **Mandated parallel cross-model dispatch**: Codex + Antigravity MCP calls must be batched in the same message — sequential dispatch doubles wall-clock time and lets one opinion contaminate the other.
- **Documented Codex Oracle `infra: true`** (codex-oracle plugin ≥ today's build): opt-in read-only live-infrastructure investigation (SSH, live DB, logs, dashboards) with project-agnostic access discovery.
- Codex Oracle model note generalized: the oracle auto-detects the strongest configured OpenAI model from the Codex CLI config (GPT-5.6 Sol at `max` reasoning as of 2026-07-10) — no plugin change needed on model bumps.

## [1.2.0] — 2026-06-22

### Changed
- **Migrated to the implicit-team model (Claude Code v2.1.178+).** Removed all `TeamCreate` / `TeamDelete` usage — those tools no longer exist. A team now forms by spawning a teammate with the `Agent` tool and is cleaned up automatically on session exit. The "Create the Team" step is gone; teammates are spawned directly with `name`, `model: "opus"`, `subagent_type`, and `run_in_background: true`.
- **`team_name` documented as deprecated/ignored** and removed from every example and from the signature table.
- **Narrowed the Step 1 `ToolSearch` query** to `select:TaskCreate,TaskUpdate,SendMessage,TaskGet,TaskList` (dropped `TeamCreate`/`TeamDelete`).
- **Rebranded Gemini → Antigravity** throughout: cross-model tool references are now `mcp__antigravity__antigravity_*` (`antigravity_query`, `antigravity_brainstorm`, `antigravity_analyze_code`, `antigravity_review_pr`), matching the MCP server's migration to the Antigravity CLI (`agy`).

### Fixed
- Removed the incorrect `model: "pro"` guidance — the Antigravity tools take **no** `model` parameter (the wrapper always selects the strongest Gemini Pro model, with Flash fallback only on capacity errors); passing `model` is rejected with `additionalProperties` validation.
- Rewrote the signature table, the "three mistakes that break runs", troubleshooting, and the checklist around the implicit-team mechanism; added a worktree-isolation note for teammates that must edit overlapping files.

## [1.1.0] — 2026-05-30

### Fixed
- **Corrected all Agent Teams tool signatures** so orchestration runs no longer fail with `InputValidationError`:
  - `team_name` is now documented as valid **only** on `TeamCreate` and `Agent` — removed any implication it belongs on the Task tools.
  - Replaced inline `blockedBy` on `TaskCreate` with the correct two-step pattern: `TaskCreate` then `TaskUpdate(taskId, addBlockedBy=[…])`.
  - Rewrote every `SendMessage` example to the real schema — `to` / `message` / `summary` instead of `recipient` / `type` / `content`; protocol `type` now nested inside the `message` object.
  - Removed the non-existent `SendMessage` "broadcast" — to reach everyone, send one message per teammate.

### Added
- **Tool Signatures reference table** (exact required/optional/forbidden params per tool) near the top of the skill.
- **Per-context deferred-tool loading**: every teammate prompt template now begins with a `ToolSearch(...)` step so a teammate's first task/message call doesn't fail with "schema was not sent to the API".
- Troubleshooting entries mapping `InputValidationError` and the deferred-tool-loading error to their fixes.
- Published under the **PolyForm Noncommercial 1.0.0** license (noncommercial use only).

## [1.0.0] — 2026-03-13

### Added
- Initial `agent-teams` orchestration skill: team lifecycle, role templates, team patterns, and optional Codex + Gemini multi-model review integration.

[1.3.0]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.3.0
[1.2.1]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.2.1
[1.2.0]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.2.0
[1.1.0]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.1.0
[1.0.0]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.0.0
