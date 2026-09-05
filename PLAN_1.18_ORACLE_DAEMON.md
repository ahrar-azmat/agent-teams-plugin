# codex-oracle 1.18a — oracle daemon on `codex exec` (plan, post architecture review)

Decisions (user, 2026-08-31): daemon design; STAGED — 1.18a on `codex exec`, 1.18b app-server read runs later.
Architect review (Codex): topology accepted (per-user daemon, CLI follower via background Bash, thin MCP
façade, sealed exec for writes); corrections adopted below.

## Components
1. `oracle_core.py` — shared, no MCP import: request envelope (versioned JSON schema), anchoring lint,
   prompt assembly (ADVISOR_CONTEXT, independence preamble, capability hunt), answer rendering + signature
   (dispatch-time digest of the TARGET tree), transient classification (typed where available), output cap,
   process identity helpers, kernel locks (from 1.17.2), journal READER for legacy 1.17 records (read-only).
2. `oracle_daemon.py` — one per user (`$CODEX_HOME/codex-oracle/`): lifetime-held OS lock + a versioned
   handshake on a daemon-owned unix socket (macOS/Linux) / named pipe (Windows); lazily started by any
  SOCKET SECURITY (round 37 review of this plan): the daemon is credential-bearing and can run
  `danger-full-access` work, so the transport is an authentication boundary, not a convenience —
  a versioned handshake is not authentication. Requirements: a `0700` parent directory and a
  `0600` Unix-domain socket created by the daemon; every connection verified by peer credentials
  (`SO_PEERCRED` on Linux, `getpeereid`/`LOCAL_PEERCRED` on macOS) against the daemon's own uid
  before any request is parsed; on Windows an owner-only DACL on the named pipe and rejection of
  remote clients (`PIPE_REJECT_REMOTE_CLIENTS`); length-prefixed frames with a hard per-frame cap
  and per-connection backpressure (no unbounded buffering); cancel/result endpoints gated by the
  same peer identity as the submitting connection.
   front-end; idle exit (configurable) only when no run is active. Durable state = SQLite (WAL) `state.db`
   with runs/attempts/events index committed BEFORE any acknowledgement; per-run dir keeps spool, live.log,
   result.txt. Run state machine: accepted → dispatching → running → completed | failed | interrupted |
   timeout | unknown; `unknown` is reconciled (child exit status + spool + rollout) before anything is re-sent.
   Per-run CHILD ownership: the daemon is the PARENT of every codex process (exit code recorded), spawns with
   file-backed stdio in its own process group, enforces the deadline itself (interrupt → grace → killpg /
   Job Object on Windows) AND still arms the 1.17 per-run detached watchdog as an INDEPENDENT backstop:
   a SIGKILLed daemon must leave every child deadline-enforced until the supervisor restarts it
   (review round 6: a sole enforcer the E2E plan kills is no enforcer). The watchdog is reaped on a
   normal finish exactly as in 1.17; reconciliation on restart adopts or reaps survivors. Retries (capacity/disconnect classes) = a
   continuation on the same thread after backoff, pin unchanged; write runs never retried. Sealed write runs
   = `codex exec` with WRITE_SANDBOX_ARGS under the lock inherited by the child. All read runs use a
   sealed `CODEX_HOME` (auth + models cache symlinked, minimal config) unless `infra` is requested.
3. `oracle` CLI (front-end A): `review|architect|research|query|abraham` (envelope → lint → submit over the
   socket → FOLLOW: tail live.log to stdout until terminal → print capped answer + result path; exit code =
   run status), `runs|log|collect|cancel|resume|daemon (status|stop)`. Run by Claude Code as background Bash.
4. MCP façade (front-end B): `start` (returns run id at once), `status`, `log`, `collect(run, wait_seconds
   ≤ 100)`, `cancel`, `resume`; legacy tool names = start + bounded wait, never a 60-min request.
5. Hooks: PreToolUse Bash matcher for `oracle …` → parse `--envelope <path>` → lint → deny on anchoring
   (configurable warn); keep nudge; push gate unchanged (reads result headers).
6. Migration: 1.17 runs.jsonl is read-only history; a live 1.17 run stays adoptable through the old server
   for one release; new runs never touch runs.jsonl.

## Residuals 1.17.2 → 1.18 requirements
- Write-lock CUSTODY is process-local (round 16): a server restart drops the flock and the
  custody set, and codex shell tools setsid() out of the run's group — the env-marker scan
  (`ps -axE` for CODEX_ORACLE_RUN=<tag>) detects and kills them at sweep/cancel/collect, but only
  while a server is running. The daemon owns custody durably (SQLite state + it holds the locks),
  supervises marked survivors across restarts, and is the sole releaser.
- The env marker is COOPERATIVE containment (round 17): a descendant spawned with env={} +
  close_fds + new session escapes group, marker and flock at once — pinned by a regression as a
  1.17.x residual (read/write runs bounded by codex's inherited OS sandbox; infra runs are
  danger-full-access and are not). The daemon needs a CAPABILITY, not a named mechanism
  (round 18: launchd's cleanup kills by process-group id — Apple's own docs — so it does NOT
  catch a new-session escapee; kqueue fork-tracking is documented unsupported on macOS): any
  candidate supervisor must be MEASURED against the known-bad probe (setsid + env/FD scrub)
  before it is trusted, per the Runtime Capability Law. Linux: cgroup v2. macOS: to be
  measured (candidates: per-run dedicated UID, endpoint-security audit, or accepting a
  documented residual).

- Push-gate endgame (rounds 29-30): a text hook is evasion-bypassable by construction (ANSI-C-
  built verbs). 1.17.2 enforces with a mode-independent DENY + one-shot acknowledgement token
  (a hook "ask" is auto-approved in auto mode upstream, claude-code#51255; measured on 2.1.257:
  `-p` manual blocks on both, the other modes cannot run under `-p`). Still to walk after the
  install, in the user's own interactive mode: a `git push` shows the deny reason and the
  acknowledged re-run proceeds (known-green); a plain command runs unprompted (known-red
  calibration). The daemon enforces at the GIT EXECUTION BOUNDARY instead (its own git wrapper /
  credential boundary), ending shell-text analysis entirely.
- Watchdog custody (rounds 30-31): the deadline sweep enumerates AND verifies through procenv.py
  (macOS KERN_PROCARGS2 per pid; Linux /proc — `ps -E` is BSD-only and never existed on
  procps-ng); unverifiable nominees are logged `unverified-marked` and left alive — degraded
  custody the daemon's handle-based supervision replaces.
- Digest (round 31): treedigest.py runs in a child process group under a hard deadline; the daemon
  can compute it once per dispatch and serve it, ending the per-push child spawn.
- Index/HEAD/history binding (rounds 35-37): the hook binds the VERIFIED wording to worktree
  bytes AND the index/HEAD objects a lone push/commit records (strict verifier), the pushed range
  (≤ 1 commit over the remote-tracking ref), the commit form, and the git-routing environment;
  the token binds the same state. A text hook still compares a snapshot of ONE repository while
  the command can address another (round 36 framing) — the daemon, enforcing at the git
  execution boundary, binds the review to the exact commit/tree object ids it lets through and
  the repository it executes against, instead of re-deriving them from a worktree walk.
- Hook bound (round 32): the hook's parent/worker split is the interim hard bound; the daemon's
  git-execution-boundary enforcement removes the hook's evaluation from the tool-call path.
- Windows token store (round 32-33): DENIED as unverified (no directory handle, reparse-point or
  ACL validation; Python < 3.13 cannot make a 0o700 directory private there); a native Windows
  known-green/known-red walk gates any token issuance there. The worker's process creation itself
  precedes the timed wait (Python documents it may be uninterruptible) — the host's 90 s hook
  timeout is the residual bound; the daemon removes hook evaluation from the tool-call path.

## Probes before 1.18b (app-server read runs)
P1 pin: model+effort on every turn/start and resume; effective settings validated; `model/rerouted` → run FAILED.
P2 isolation: sealed CODEX_HOME under app-server → zero user MCP servers; `thread/shellCommand` never exposed
   (strict method allowlist in the adapter). P3 recovery: kill the daemon mid-turn → reconcile via
   thread/read + turns/list → no duplicate turn; typed error info drives the classifier.

## Tests
Unit (fake codex): state machine, reconciliation of `unknown`, per-run child ownership + exit codes, deadline
kill, cancel semantics (intent before spawn / kill after), claims, locks, envelope schema, lint gate, output
cap. E2E: daemon killed mid-run (SIGKILL) → children stay deadline-bounded by the independent watchdog
(a no-restart variant proves the kill actually fires) → restart → reconcile → collect; Claude Code kill sequence against
the FOLLOWER only (run unaffected); real-codex run through the CLI with the live log streaming.
