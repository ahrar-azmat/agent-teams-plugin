# PLAN — `/abraham`: deep-analysis + WRITE mode for codex-oracle

**Status:** BUILT + unit-verified (84 checks green, anchor-lint regression 97 green),
2026-08-14. Cross-model review CLOSED: Codex (primary) REJECTED the first single-process
design; the shipped design is the post-review rebuild (§9). One decision remains with the
owner: packaging (§2). Nothing committed/pushed yet (§8).

## 1. Requirement (as specified by the owner)

- Codex (GPT) gains the ability to **write** — create/edit files in the calling session's
  working tree — after **deep analysis** of the codebase, **live infrastructure**, and
  **live web**.
- User trigger: **`/abraham <query>`**. The Claude session stays the **orchestrator**
  (dispatch → monitor → review), same as the read tools.
- **Mode algebra:** read ⊻ write (mutually exclusive); `infra` and `websearch` compose with
  either base.
- Long runs **auto-compact codex's own context at ~60–70%** consumption.
- Open question posed by the owner: extend `codex-oracle` vs. a separate plugin.

## 2. Architecture: two air-gapped phases in one tool (post-review design)

One `abraham` MCP tool + `commands/abraham.md`. Internally **two codex processes**:

1. **ANALYSIS (read-only).** `infra`/`web_search` apply HERE — the existing read-mode
   postures (`read-only`, or `danger-full-access` + prompt discipline for infra). Output =
   an implementation brief (findings w/ file:line, live state, external facts w/ URLs +
   exact values, ordered plan, risks, verification commands).
2. **IMPLEMENTATION (sealed).** `workspace-write` file access and NOTHING else: no shell
   network, no web search, `--ignore-user-config` (no user MCP servers), `/tmp`+`$TMPDIR`
   excluded from writable roots with TMPDIR redirected to workspace-local `.abraham/tmp`.
   Untrusted external content and live credentials never share a process with write
   capability. The brief travels in the prompt; the implementer re-reads local code freely.

Why two-phase: BOTH advisors independently demanded the air-gap (Antigravity's
"air-gapped two-phase"; Codex CRITICAL #1). Residual risk stated honestly: a poisoned
analysis phase can still distort the brief → mitigated by the diff review + no-commit
contract; what the air-gap kills structurally is exfiltration and remote mutation FROM the
write process.

**Packaging (OWNER DECISION).** Codex (primary) rules the write tool should ship as a
SEPARATE plugin (consent surface: installing an advisory pack must not silently add a
destructive tool; marketplace commands are namespaced anyway). Antigravity's idea 4 agrees.
Counterpoint (mine): Claude Code prompts per-MCP-tool at first use, so use-time consent
exists; a fourth plugin duplicates ~1k lines of hardened runtime against this repo's
no-shared-module doctrine. The code is identical either way — extraction to
`plugins/abraham/` is mechanical if the owner rules that way. Currently housed in
codex-oracle (plugin 1.10.0).

## 3. Mode → sandbox mapping (as shipped)

| mode | phase | sandbox | user config/MCP | shell net | web |
|---|---|---|---|---|---|
| read (default) | — | `read-only` | isolated | none | live |
| read + infra | — | `danger-full-access` | kept | full | live |
| abraham analysis | 1 | `read-only` (or `danger-full-access` w/ infra) | per infra | per infra | per `web_search` |
| abraham implementation | 2 | `workspace-write` + tmp exclusions | **always isolated** | **never** | **never** |

Write processes NEVER use `danger-full-access`, never open `network_access`, and are the
only place `model_auto_compact_token_limit` is emitted. Read⊻write exclusivity is
structural (separate tools; resume inherits the axis and force-seals write continuations).

## 4. Auto-compaction (60–70%)

- Key: `model_auto_compact_token_limit` (scope default `total` = full active context ✓).
- Vendor default: **90% of window** (`resolved_context_window * 9/10`).
- **A user-config value wins OUTRIGHT** (no min with the window) → a limit above the real
  window would NEVER fire → the flag is passed only when the window is KNOWN.
- **Window source is measured, never recalled** (owner caught the first draft using a
  recalled 400k): the deployed binary's own `models_cache.json` says
  **`gpt-5.6-sol: context_window = 272,000`** → 65% = **176,800** (band 60–70% =
  163,200–190,400). The draft's 260k placeholder would have been 95.6% — later than the
  90% default it was meant to improve.
- Precedence: `CODEX_ORACLE_CONTEXT_WINDOW` env → `models_cache.json` exact-slug →
  **omit flag** (vendor 90% governs). Branch recorded in the live-log header.
  `CODEX_ORACLE_AUTOCOMPACT_PCT` default 65, clamped 30–85.
- Still open (Codex HIGH #6): compaction FIRING and post-compaction continuity are
  config-verified but not behavior-observed — planned check: a live run with a tiny limit
  watching for the compaction event in the JSONL stream (§10 "next").

## 5. Git safety envelope (as shipped)

- **Work-tree precondition** (in `_run_codex`, so resumes get it too): refuse outside git.
- **Dirty-tree refusal by default** (Codex HIGH #5) with `allow_dirty=true` override —
  the implementer may rewrite files; uncommitted edits have no undo. Ignored files
  (`.env`, venvs) don't count as dirt (porcelain doesn't list them).
- **One writer per tree:** authoritative O_EXCL lockfile keyed on sha1(cwd), held across
  BOTH phases and during write resumes; stale-break by holder-pid liveness (a crashed
  server must not block its own recovery) with run-lifetime age as fallback; unusable lock
  dir FAILS CLOSED. Journal-liveness check retained as an advisory belt.
- **No auto-retry once a write process starts** (Codex CRITICAL #2): replay after partial
  writes double-applies. Read runs keep the transient retry. Recovery = explicit
  `codex_resume_run`, which force-seals write continuations (no infra/web override) and
  restates the journaled task.
- **Attribution:** porcelain snapshot before dispatch; every outcome path (success,
  timeout, hang, error) appends **[CHANGED FILES]** — new changes vs pre-existing dirt
  (with the honest caveat that further edits to already-dirty files are not separable) —
  and verifies HEAD didn't move; a violation of the no-commit contract is reported loudly.

## 6. Known limitations (stated, not hidden)

- Remote read-only in the infra ANALYSIS phase remains prompt-enforced — the pre-existing
  posture of read+infra, unchanged by this feature. Codex's container/broker proposal is
  recorded as the eventual hardening, out of scope for this personal toolchain iteration.
- The brief passed to phase 2 is the possibly-truncated in-band result (60k-char cap),
  not the full `.result.txt`.
- Attribution can't see codex piling further changes onto an already-dirty file (same
  porcelain line) — the report says so; the diff review covers it.
- Cancellation mid-phase-2 can't append a report (the call is gone) — the command doc
  directs a `git status` check after any interruption.
- Env vars are inherited by the sealed process (no allowlist yet) — exfil channel is
  closed (no network), but env values could be copied into reviewed files; future
  hardening candidate.

## 7. Files (all in this repo, working tree)

- `plugins/codex-oracle/server.py` — auto-compact block (`AUTOCOMPACT_PCT`,
  `_model_context_window`, `_auto_compact_limit`); git-safety block (`_git`, `_git_state`,
  `_write_changes_report`, `_active_write_run`, `_write_lock_path`,
  `_acquire_write_lock`, `_release_write_lock`); `_build_exec_argv(write,
  auto_compact_limit)` sealed branch; `_run_codex(write)` — precondition, TMPDIR redirect,
  sealed scaffold, mode-labelled journal/live-log, no-retry-for-write, report on every
  outcome; `abraham` tool (two-phase orchestration, refusals); `codex_resume_run` write
  inheritance + force-seal + lock.
- `plugins/codex-oracle/commands/abraham.md` — the `/abraham` command (orchestrator
  instructions).
- `tests/test_write_mode.py` — 84 dependency-free checks (see CHANGELOG for coverage).
- `plugins/codex-oracle/.claude-plugin/plugin.json` → 1.10.0; `marketplace.json`
  description; `CHANGELOG.md` → [1.13.0].

## 8. Rollout (pending owner)

1. ⚠️ The dev checkout carried a PRE-EXISTING uncommitted changeset before this session
   (auto-infra research detection, `_kill_tree` process-group fix, resume-prompt fix,
   plugin.json 1.8.1→1.9.0; comments dated 2026-08-09, another session's work). This
   feature builds ON TOP of it in the same file. Commit as two commits (theirs first) or
   as the owner directs — and nothing is pushed without the owner's say.
2. Owner rules on packaging (§2): stay in codex-oracle vs extract to `plugins/abraham/`.
3. After commit+push: `git pull` in `~/.claude/plugins/marketplaces/agent-teams`, then
   `/mcp` → Reconnect (measured: `/reload-plugins` does NOT pick up server-code changes).
4. Verify the command surface: whether the command registers as `/abraham` or namespaced
   (`/codex-oracle:abraham`) on this Claude Code build — Codex cites current docs saying
   plugin commands are namespaced; the file name gives `/abraham` if unique names resolve.
5. Post-reconnect live checks: `/abraham` on a scratch repo; the auto-compact behavioral
   probe (§4); `tail -f ~/.claude/logs/codex-oracle/latest.log`.

## 9. Cross-model review record (both dispatched blind + in parallel; Codex governs)

- **Codex (primary): REJECT of the v1 single-process design; hypothesis REFUTED.**
  Adopted: two-phase air-gap; no auto-retry for writes (CRITICAL — was a real miss);
  dirty-tree refusal default; real lockfile (mtime check ≠ lock); tmp exclusions +
  workspace TMPDIR; strict-config calibration with a known-bad key; 272k/176,800 figures
  CONFIRMED. Deviations from its recommendations, recorded deliberately: packaging left to
  the owner (§2); container isolation for infra analysis deferred (§6); env-var allowlist
  deferred (§6); its "90% clamp on configured totals" phrasing conflates the model-registry
  value (clamped) with the user override (not clamped — `or_else`, measured in
  `context_window.rs`), which does not change any shipped number.
- **Antigravity (secondary): REFUTED my original hypothesis too** — air-gap (adopted via
  Codex convergence), shadow-worktree isolation (available compositionally: run `/abraham`
  from a worktree; not built-in v1), marketplace-twin packaging (folded into §2), external
  restart-compaction (unnecessary on 0.147.0 — native key verified), container elevation
  (deferred with Codex's).

## 10. Probe & verification record (live, this session — codex-cli 0.147.0, gpt-5.6-sol)

| check | result |
|---|---|
| P1 read-only asked to write (calibration) | ✅ refused, no file, `CANNOT-WRITE` |
| P2 workspace-write + auto-compact key + `--strict-config` | ✅ file written; `curl` failed (DNS sealed); no config complaint |
| P3 workspace-write + `network_access=true` | ✅ `CURL-OK` (toggle exists — deliberately NOT used by the shipped design) |
| strict-config calibration with bogus key | ✅ exit 1, "unknown" — the P2 acceptance was recognition, not permissiveness |
| context window measurement | ✅ `models_cache.json`: gpt-5.6-sol = 272,000 (draft's recalled 400k was WRONG — owner's catch) |
| unit suites | ✅ 84/84 write-mode; 97/97 anchor-lint regression |
| live E2E (both phases, real codex, scratch repo) | ✅ PASSED 2026-08-14: phase 1 read-only produced the brief; phase 2 (sealed, live log labelled `abraham`) wrote `hello.py` with the exact requested content, ran its own verification (syntax, stdout bytes, git status), file runs; report attributed `?? hello.py`, HEAD unchanged — no commit, contract held. Empty `.abraham/tmp` correctly invisible (git doesn't list empty dirs); it will surface as `?? .abraham/` only when a build tool actually writes scratch files. |

Next: auto-compact behavioral observation (tiny limit → compaction event in JSONL), and
the post-reconnect `/abraham` smoke run (§8).
