# Changelog

All notable changes to the plugins in this marketplace are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.17.2] — 2026-08-31

### Run budget (user, 2026-09-04): the 60-minute literal becomes a managed 3-hour limit
- MEASURED (SmartPay trains, 2026-09-02..04): three legitimate analysis-
  heavy runs were SIGKILLed at 62–66 min by the 60-minute literal while
  still working (exit -9); healthy runs finish in 4–30 min. The budget is
  now a MANAGED limit: default 3 h (≥2.7× the longest observed legitimate
  need, under the plugin's MCP call timeout — raised from 2 h to 4 h in
  .mcp.json — and the host's ~4 h idle abort, both of which a detached run
  survives), adjustable via `CODEX_ORACLE_MAX_RUNTIME_S` (300..12600 s;
  out-of-band values are rejected loudly on stderr and the default kept,
  never clamped), visible in `codex_runs`, warned at 80 % in the live log,
  and named with the knob in every deadline-kill message (which also tells
  the caller to `codex_resume_run` the thread rather than re-ask). Pinned
  at-cap/over-cap by tests/test_detach.py. The installed 1.17.1 cache was
  hot-patched to the same 3 h / 4 h the same day (immediate relief; this
  release is the durable copy).

### Full-access write mode (user, 2026-09-05): "allow codex to skip permission via --yolo"
- MEASURED over 36 hours of SmartPay development: the sealed writer never
  touches the real system (no PostgreSQL, no Temporal, no browser, no
  network), so almost every red of a round surfaced one round later in the
  lead's rig — a migration that only fails on a fresh database, a bind
  ambiguity only PostgreSQL raises, a lock proof that breaks under the test
  pool. `abraham(full_access=True)` (or CODEX_ORACLE_WRITE_FULL_ACCESS=1
  as the default) runs the IMPLEMENTATION phase with codex's own
  `--dangerously-bypass-approvals-and-sandbox` (`--yolo`): no sandbox, no
  approval prompts, network on, the user's configuration and MCP servers,
  the caller's full privileges — and a scaffold that orders the writer to
  run the project's real gates and report their real exit codes. The git
  contract, the one-writer lock, the changed-files report and the request
  budget still apply; the sealed mode's no-egress / no-credentials
  guarantees do NOT (documented on the tool). The mode is journaled and a
  resume keeps it; the write-capability probe (a sealed-sandbox concern) is
  bypassed; phase 1 stays read-only. The sealed mode remains the default.

### Model pin (user, 2026-09-05): GPT-6 Astra
- OpenAI released GPT-6 Astra on 2026-09-03 (API slug `gpt-6-astra`); the
  oracle follows `~/.codex/config.toml` (`model = "gpt-6-astra"`, re-read on
  every mtime change), and its FALLBACK when the file names no model moves
  from `gpt-5.6-sol` to `gpt-6-astra` — the bundled default of codex-cli
  0.153.4 (2026-09-04). MEASURED before the change, on the deployed binary's
  own registry (`models_cache.json`, 0.153.x): `gpt-6-astra` is listed with
  `context_window` 272,000 and efforts low…max (+ultra), `supported_in_api`
  true; a `code_review` completed on it at max (298 s) and a write run's
  header read `autocompact=176800 [65% of 272,000 (models_cache.json)]`, so
  the exact-slug window lookup needs no change. The API model page's
  1,050,000 window is deliberately NOT used: the registry is what the
  binary's own 90% default derives from, and 272K is also the input size
  above which the API bills its long-context premium. Reasoning stays
  pinned at `max` (never `ultra`, never the Fast tier). The installed
  1.17.1 cache was hot-patched to the same fallback the same day.

### Round 45 (Codex): SHIP — no new actionable findings
- 720 git-vs-classifier dry-run comparisons on the destination resolution (all
  six `push.default` values, repeated remote/merge values, empty and
  valueless entries): 0 disagreements among the 398 where both named a
  destination. Two notes, both fail-closed and left as is: a VALUELESS
  `branch.<b>.remote` (no `=`) reads as present here while git refuses the
  command (exit 128, "missing value") — no push happens; a bare push under
  `push.autoSetupRemote` with an upstream remote but no merge entry targets
  the current branch in git while the gate resolves no destination and
  withholds the wording.

### Round 44 (Codex, needs-changes: 1 HIGH — addressed; 1,800 git-vs-classifier destination comparisons found only this)
- HIGH — the round-43 prerequisite guard stripped the value of
  `branch.<b>.remote`, so a key set to whitespace read as absent while
  git (remote.c set_merge) tests only that the key EXISTS: with
  `branch.main.remote = " "` and `merge = refs/tags/v1.1.0`, git mapped the
  source-only push to the tag (forced update under a trailing tag lease,
  measured in both `upstream` and `tracking`) while the gate resolved
  `main`. Both guards now test key presence; the whitespace case is pinned
  for both modes and for the bare form.

### Round 43 (Codex, needs-changes: 2 HIGH — all addressed; the exact-first lease held 24/24 stale-ref cases)
- HIGH — `push.default=tracking` is git's deprecated synonym for `upstream`
  (environment.c) and the new source-only mapper recognised only
  `upstream`: with `branch.main.merge=refs/tags/main`, `--force-with-lease=
  refs/heads/main:<B> --force-with-lease=refs/tags/main:<A> origin main`
  was a forced update of the tag (measured) while the gate read REVIEW
  VERIFIED. `tracking` is normalised to `upstream` before any destination
  is resolved, and the reason names the value as written.
- HIGH — the mapper applied `branch.<b>.merge` when git ignores it: remote.c
  set_merge drops the merge configuration unless `branch.<b>.remote` is set
  (and exactly one value exists). Measured: without the key,
  `merge=refs/heads/release` still pushed `main -> main` (a lease on
  `release` bound nothing); with it, `main -> release`. The mapping — and
  the bare-push `upstream` destination — now apply only under git's
  prerequisites, with the paired calibration pinned.

### Round 42 (Codex, needs-changes: 2 HIGH, 1 LOW — all addressed)
- HIGH — a fully qualified destination does not establish the remote
  NAMESPACE either: git expands even `refs/heads/main` against the
  advertised refs with the rev-parse rules, so when the branch is absent a
  ref NAMED `refs/tags/refs/heads/main` or `refs/remotes/refs/heads/main`
  becomes the target. Calibrated: with the nested tag at A, `tags/refs/
  heads/main:<A> --force-with-lease=main:<B> origin HEAD:refs/heads/main`
  was a forced update (rc 0); with the nested remote-tracking ref at A, a
  single `main:<B>` lease never applied and the push was a plain fast-
  forward of that ref (rc 0) — both read REVIEW VERIFIED. apply_cas uses
  the same expansion for the lease entries and takes the first match, so
  ONE spelling reaches every possible target: the lease spelled EXACTLY
  like the destination (`refs/heads/<branch>`), FIRST on the command line
  (calibrated: "stale info" on every crafted remote). That is now the only
  form that earns the wording; the reason names it.
- HIGH — the source-only exception ignored git's configured MAPPINGS:
  `remote.<r>.push` maps a source-only refspec (calibrated: `refs/heads/
  main:refs/tags/main` sent `git push origin main` to the tag as a forced
  update), and `push.default=upstream` maps a source-only branch name to
  `branch.<b>.merge` (calibrated: `merge=refs/tags/main` sent it to the
  tag; `HEAD` is not mapped). Any `remote.<r>.push` now demotes before the
  refspec is read, and under `upstream` the mapped destination must be
  `refs/heads/…` to count.
- LOW — `git+ssh://` and `ssh+git://` are git's own builtin aliases
  (traced: ssh is invoked, no helper) and were classified as helpers; the
  lowercase aliases are native, their upper-case spellings stay helpers.
  http(s) run git's bundled remote-curl helper — the docs say so instead of
  calling them native.

### Round 41 (Codex, needs-changes: 1 HIGH, 1 MEDIUM — all addressed; the other seven round-40 responses re-measured and confirmed)
- HIGH — the lease order was applied to a destination whose NAMESPACE the
  hook had not established: an explicit unqualified destination
  (`HEAD:main`, `main:main`) is resolved against the remote's refs at push
  time. Calibrated on git with only `refs/tags/main` on the remote: the
  push updated the TAG, and `--force-with-lease=tags/main:<A>
  --force-with-lease=main:<B> origin HEAD:main` was a forced update
  carrying two commits where the hook measured one, while it read REVIEW
  VERIFIED. A source-only refspec (`main`, `HEAD`) and a bare push inherit
  the source's full name (measured: `[new branch] main -> main`, the tag
  untouched) and stay verifiable; an explicit destination now earns the
  wording only as `refs/heads/<name>`, and the reason says so ("Push
  form: …" — push demotions were labelled "Commit form" before).
- MEDIUM — the native-transport check was case-insensitive while git's is
  not: `SSH://[::1]/repo` runs `git-remote-SSH` (traced; `GIT://` runs
  `git-remote-GIT`). The allowlist is case-sensitive now and the
  upper-case schemes are calibrated as helpers.

### Round 40 (Codex, do-not-ship: 5 HIGH, 3 MEDIUM, 1 LOW — all addressed)
- HIGH — a FULL-ACCESS write resume never took the tree lock: the
  acquisition sat inside the sealed-only branch next to the probe, so the
  continuation ran unlocked, its child publication failed the execution
  barrier, and it released a lock it never held. Every write continuation
  now acquires the lock; only the probe is conditioned on the sealed mode.
  Pinned through the child publication under the held lock, not a mocked
  runner.
- HIGH — the stored lease was not git's effective lease: `--no-force-with-
  lease` (an inert flag until now) cancels every lease given before it,
  and with several leases git applies the FIRST entry whose ref matches
  (remote.c apply_cas) while the parser kept the LAST. Calibrated on git:
  with the remote reset to A and the hook's measured tip B, `main:<B>
  --no-force-with-lease` and `refs/heads/main:<A> --force-with-lease=main:<B>`
  were both accepted by git and both read REVIEW VERIFIED. Leases are now a
  list in command-line order, a cancel empties it, and the wording needs
  the first entry naming the destination (`main`, `heads/main`,
  `refs/heads/main`) to carry the measured tip.
- HIGH — command detection ran in the PARENT, outside any deadline, and its
  regex rescanned a segment from every `git` word: a valid 131 KB command
  (32,768 `git` words, then `; git push origin main`) stalled past a 2 s
  deadline while `echo hi` took 44 ms — and a timed-out hook does not
  block. The parent now runs only a LINEAR pre-filter (a superset of every
  detection channel: the words git/push/commit/pwsh/powershell surviving
  de-escaping and quote/dollar stripping, or ANSI-C quoting); full
  detection and the decision both run in the worker under the deadline,
  and the matcher itself is linear (only the first `git` per segment
  matters). Pinned at 32,768 words, end to end.
- HIGH — the payload read and the evaluation each had the FULL budget: a
  payload arriving over 59 s plus a 60 s worker put the denial at 119 s
  against the 90 s host timeout (fail-open). One absolute deadline now
  covers reading, parsing, the pre-filter and the worker; the worker gets
  only what is left, and under 1 s left is a deny without a spawn.
  Calibrated with a stalled worker (fault injection
  `CODEX_PUSH_GATE_STALL=worker:<s>`): a 4 s budget and a 2.5 s payload
  deny at ~4 s, where separate budgets ran ~6.5 s.
- HIGH — git booleans: `commit.gpgSign=2` is true to `git config
  --type=bool` (any non-zero integer is), while the check accepted only
  `1` — an enabled signer kept REVIEW VERIFIED; `push.followTags=2` and
  numeric mirror/recurse settings likewise. Booleans follow git's grammar
  now (true/yes/on, false/no/off, integers), and a value git would reject
  or scale — or the empty value a `--list` dump shows for both `[s] key`
  and `key =` — is not provably off.
- MEDIUM — on macOS a FAILED `ps` lookup (exit 1, empty output) read as
  "the process vanished": fault-injected, the scan returned [] over a live
  process, so cleanup could overlook surviving descendants and release
  custody. An empty `ps` answer is now confirmed by the kernel; a pid that
  still exists is alive and unreadable (UNKNOWN), never gone.
- MEDIUM — the Windows branch of the payload reader skipped select() and
  blocked in os.read with no deadline. There the reader is a daemon thread
  joined until the deadline (select() cannot watch a pipe on Windows);
  POSIX keeps select(). Pinned on a stalled pipe.
- MEDIUM — a full-access resume overwrote an explicit `web_search=False`
  with the recorded setting; the resolved override now wins (sealed
  resumes stay offline regardless).
- LOW — `::` ANYWHERE in a URL read as a remote helper, rejecting
  `ssh://[::1]/repo` and `file:///tmp/a::b` (git serves the latter itself —
  calibrated with a bare repository at such a path). Helper syntax is now
  the transport.c prefix rule: URL-scheme characters immediately followed
  by `::`.

### Round 39 (Codex, do-not-ship: 6 HIGH, 2 MEDIUM — all addressed)
- HIGH — the transferred range was measured against the remote-tracking ref
  AS OF THE LAST FETCH: a remote reset or deleted after that fetch turns
  "ahead ≤ 1" — even "nothing to push" — into a plain fast-forward or
  branch-creation push that publishes unreviewed history. A hook without
  network access cannot know the remote's tip, so a push earns the full
  wording ONLY when it binds itself to the measured tip:
  `--force-with-lease=<dst>:<oid>` with the exact tracking object id is a
  compare-and-swap git enforces at execution time (calibrated: the upstream
  reset behind the tracking ref, `ahead == 1` in the stale view, git refused
  the leased push with "stale info" and published nothing, where a plain push
  would have carried two commits). The bare and implicit lease forms stay
  forced; every plain push reads VERIFIED-BUT and names the leased command.
- HIGH — conversion attributes were consulted only after the raw worktree
  bytes differed from the index, while `git commit -a` was an allowlisted
  form: a same-byte file under a newly enabled ident/text/encoding conversion
  is re-added THROUGH the clean filters (measured: raw vs ident-filtered blob
  ids differ), so the recorded blob is not the reviewed bytes. `-a`/`--all`
  (and the `a` cluster letter) are removed from the verified commit forms.
- HIGH — `git commit` with no message source and `git commit --amend` open
  the EDITOR (a program) yet read as complete-index forms; implicit signers
  kept the wording. A commit now needs `-m`/`-F` or `--no-edit`, and
  `commit.gpgSign` / `push.gpgSign` demote naming the signing program.
- HIGH — only pre-commit and pre-push were queried. Every hook the parsed
  form reaches is now listed by git (`--no-verify` skips pre-commit and
  commit-msg only; prepare-commit-msg, post-commit, post-index-change and
  reference-transaction always run; post-rewrite on `--amend`; pre-push and
  reference-transaction on a push) and any active one demotes, named with
  its event (measured: prepare-commit-msg cannot change the recorded tree,
  but it runs code).
- HIGH — the repository-scoped program keys assumed dot-free subsections:
  `credential.https://example.com.helper`, `filter.my.driver.clean`,
  `includeIf.gitdir:/tmp/a.b/.path` and `gpg.x509.program` escaped the
  regex while URL-scoped credential helpers are shell-executed. Keys are
  parsed by SECTION and terminal VARIABLE with arbitrary subsection text.
- HIGH — remote-helper detection recognised only `remote.*.vcs` and `::`
  URLs; git runs `git-remote-<transport>` for every scheme it does not
  implement (`evil://host/repo` measured a successful range). Both EFFECTIVE
  URLs (`remote get-url` and `--push`, insteadOf applied, executes nothing)
  must resolve, be equal, and be a native transport — ssh/git/http/https/
  file, the scp-like form, a local path; anything else is a helper.
- MEDIUM — a RESUMED full-access write run executed the sealed-sandbox
  capability probe (a cached sealed failure blocked a deliberately
  full-access continuation). The probe runs only for sealed resumes; the
  one-writer lock covers both modes.
- MEDIUM — the host's payload was read with an unbounded `json.load` before
  any deadline existed and malformed JSON exited 0 silently (fail-open). The
  payload is now read from the descriptor under a cap (16 MiB; the command
  text 1 MiB) and the evaluation deadline; over-cap, malformed, non-object,
  non-string-command, oversized-command and stalled payloads are structured
  denies that name the cause, and a plain well-formed command stays silent.
- Test reconciliation: with the lease mandatory, thirteen tests that expected
  a plain push to carry the full wording moved to the leased form (the lease
  note precedes the hook/program notes, so those tests lease first); the
  stall probe closed its own stdin (`communicate()` closes it) and now holds
  the pipe open under a watchdog; the scan-budget test feeds `main()`
  through a real pipe (the payload reader needs a descriptor); the
  pre-round-39 "malformed stdin fails open" test is deleted.

### Round 38 (Codex, do-not-ship: 5 HIGH, 4 MEDIUM, 1 LOW — all addressed)
- HIGH — hooks were detected as executable FILES while Git 2.55 also runs
  CONFIGURED hooks (`hook.<name>.command` + `event`; calibrated: `git hook
  list pre-push` named one that no file scan saw). Hooks are now listed by
  git itself — `git hook list -z <event>` under the command's effective
  configuration (fsmonitor off, the hooksPath override dropped; measured:
  it executes nothing, ignores non-executable files, honours an empty
  core.hooksPath) — and a git that cannot list reads as "a hook runs".
- HIGH — `HOME=. git push` / `XDG_CONFIG_HOME=/tmp git push` were direct,
  lone pushes inspected under the worker's own environment while the
  command ran under another GLOBAL configuration. Any command-local
  environment assignment now voids the strong wording and is named.
- HIGH — `no_verify` was reconstructed by rescanning raw tokens: `git
  commit -m -n` (a message) and `-Fnotes` read as --no-verify and a later
  `--verify` was ignored, so pre-commit detection was skipped while git ran
  the hook. It is now derived in order while the arguments are consumed
  (later options override), with the three regressions pinned.
- HIGH — forced pushes were inert while the range was measured against the
  remote-tracking ref as of the LAST FETCH: a plain push that is behind is
  rejected by the remote, a forced one overwrites commits nobody here has
  seen. `--force`, `-f`, `--force-with-lease` (any form), `--force-if-
  includes` and `+refspec` now read VERIFIED-BUT.
- HIGH — execution surfaces still inside the "plain git" allowlist: `git
  -p`/`--paginate` run core.pager (a shell command), commit `-e`/`--edit`,
  `-t`/`--template` launch the editor, `-S`/`--gpg-sign` run gpg.program,
  `--trailer` runs trailer.<token>.cmd, and `remote.<name>.vcs` / `::` URLs
  invoke a remote helper. All are removed from the strong wording, and
  REPOSITORY-SCOPED configuration (local/worktree) that names a program or
  rewrites an endpoint — credential/ssh/askpass/editor/pager/proxy helpers,
  gpg and trailer programs, filters, diff/merge drivers, insteadOf, includes,
  aliases, configured hooks — demotes and names the keys (the user's global
  values are their own and do not).
- MEDIUM — `git --no-lazy-fetch --version` proved option PARSING only:
  patched 2.44.1 honours the variable without the option, 2.45.0 accepts
  the option without the CVE fix. The capability is now proven by doing
  the dangerous thing in a throwaway partial clone whose promisor remote is
  an `ext::` helper that leaves a marker — the known-red half (no safe
  environment) must run the helper or the probe reports failure, the
  known-green half (GIT_NO_LAZY_FETCH=1) must not (measured both ways on
  2.55.0). The verdict is cached per process and, when capable, on disk
  for 24 h keyed by the binary's path, size, mtime, version and mode
  (CODEX_ORACLE_LAZY_PROBE_CACHE_S=0 disables). The CVE fixed-version
  matrix stays as a separate POLICY floor. `--no-lazy-fetch` is no longer
  passed (2.44.1 would reject every read).
- MEDIUM — token consumption was not atomic: two consumers could both read
  one file before either unlinked it. The token is first RENAMED to a
  per-consumer claim (atomic within the store), so exactly one wins; stale
  claims are swept like expired tokens; pinned with four racing threads.
- MEDIUM — the token was consumed before the review state was read and its
  binding omitted the review evidence: the transcript is scanned FIRST and
  the dispatched flag, the answered digests and the wording class are part
  of the state — a token minted while the review was PENDING is refused
  once the answer lands.
- MEDIUM — a failed process-group sweep was hidden behind an earlier
  timeout/cap reason; both are now reported.
- LOW — the legacy-path lookup failure returned three values to callers
  unpacking four (a worker exception instead of the structured range
  rejection); fixed and pinned.

### Round 37 (Codex, do-not-ship: 6 HIGH, 2 MEDIUM, 1 LOW — all addressed)
- HIGH — `GIT_CONFIG` was neither scrubbed nor treated as routing. It is
  honoured by `git config` ALONE (measured on 2.55.0: `GIT_CONFIG=/dev/null
  git config --list` is empty while `git remote get-url origin` still
  answers), so an ambient value blinded every configuration read of the
  gate while the command kept the repository's configuration. Scrubbed
  from every read, reported as routing, bound into the token.
- HIGH — the push range was measured against the FETCH endpoint's tracking
  ref while git pushes to `remote.<r>.pushurl` (and rewrites the push
  endpoint with `url.<base>.pushInsteadOf`); a configured `receivepack`
  names a program the push runs. Any of them makes the range UNKNOWN
  (VERIFIED-BUT, naming the setting).
- HIGH — the token was consumed BEFORE the decision was recomputed and its
  state omitted the configuration, the symbolic branch and the tracking
  ref: an old token survived a pushurl change, a same-commit branch switch
  and a moved tracking ref. The whole decision now runs first and the
  token binds every input and the result (content digest, HEAD, index
  listing, toplevel, routing environment, branch name, a hash of the full
  configuration listing, kind, remote, destination, tracking OID, ahead/
  behind, hooks, the verdict); calibrated with all three mutations.
- HIGH — quote removal erased the difference between a shell comment and a
  quoted OPERAND: `git commit -m x "#file"` (a pathspec commit) and
  `git push origin HEAD "#evil"` (refs/heads/#evil is a valid ref) read as
  lone. Any `#` word is now unclassifiable (VERIFIED-BUT). Global options
  became an explicit inert allowlist (`--no-pager`, `-P`, `--paginate`,
  `--no-optional-locks`, `--no-replace-objects`, `--no-lazy-fetch`,
  `--literal-pathspecs`, inert `-c`): `--bare`, `--namespace=`,
  `--attr-source=`, `--super-prefix=`, `--exec-path=` are not direct.
- HIGH — a pre-commit hook runs BEFORE the commit is created and can
  re-stage content (calibrated: the committed tree gained a file the hook
  added). An ACTIVE pre-commit hook — an executable file in the
  repository's own hooks directory (`core.hooksPath` as the repository sets
  it, not the read-time override; relative to the worktree top) — now
  demotes a commit unless `--no-verify`/`-n` skips it; an active pre-push
  hook demotes a push. Measured and documented: a prepare-commit-msg hook
  cannot change the recorded tree (the index is re-read after pre-commit
  only), so it is not counted.
- HIGH — the 1.18 daemon plan specified a per-user socket with a handshake
  but no peer authentication. The plan now requires a 0700 directory and
  0600 socket, peer-credential verification (SO_PEERCRED / getpeereid)
  before any request is parsed, an owner-only DACL and remote-client
  rejection on Windows, and capped length-prefixed frames with
  backpressure.
- MEDIUM — after EPERM the sweep excluded the LEADER from the liveness
  check and swallowed a failed kill/wait, so an unsignalable live leader
  read as swept. Only an EMPTY live listing proves the group gone, a
  leader that cannot be terminated makes the sweep False, and a failed
  listing is never "gone" (calibrated with fakes for all three).
- MEDIUM — a version table was a type stub (it rejected 2.45.0, which has
  the option, and trusted every hypothetical 3.x). Per the Runtime
  Capability Law the BINARY is now probed: `git --no-lazy-fetch --version`
  exits 0 only where the option exists (known-green: the installed 2.55.0;
  known-red: a git that answers 129 "unknown option" refuses every read);
  every read passes `--no-lazy-fetch` as well as GIT_NO_LAZY_FETCH=1.
- LOW — the leader-exit drain read 64 KiB chunks past the cap; it now reads
  at most the remaining budget plus one sentinel byte and reports "cap".

### Round 36 (Codex, do-not-ship: 10 HIGH, 3 MEDIUM — all addressed)
- HIGH — the hook could CRASH to a non-blocking exit: payloads rode a
  `TemporaryFile()` created outside any guard, and on a machine with no
  writable temporary directory the review measured a traceback, exit 1 and
  EMPTY stdout — which the host treats as a non-blocking hook error, so the
  push proceeded. Payloads now travel over a pipe fed by a writer thread
  (no temporary file anywhere in the path), the worker prints a structured
  deny on any exception, and the module guard turns ANY escape from
  `main()` into a deny with exit 0. Fault injection
  (`CODEX_PUSH_GATE_FAULT=parent|worker`) calibrates both guards; a plain
  command stays silent with the fault armed; an unwritable TMPDIR/TEMP/TMP
  changes nothing.
- HIGH — the attestation bound the checked-out CONTENT but a push transfers
  HISTORY: a commit that added a secret and a later one that removed it
  leave the reviewed tree intact and still ship the secret (calibrated:
  same digest); a same-tree merge likewise. The full VERIFIED wording for a
  push now needs the transferred range to be at most ONE commit on top of
  the remote-tracking ref of the effective destination (`rev-list --count`
  both ways, replace refs disabled): a longer range, a tracking ref that is
  ahead, or a range that cannot be established (a URL target, no tracking
  ref — fetch first, a legacy `$GIT_DIR/remotes|branches` definition with
  its own Push: lines, a fetch refspec that is not the standard
  refs/heads/* mapping) reads VERIFIED-BUT and says why.
- HIGH — the one-shot token bound (digest, command) and was consumed before
  any consistency check, so `update-index --cacheinfo`, a `reset` or a HEAD
  move inside the ten-minute window kept the token. The binding now carries
  HEAD, the raw index listing, the toplevel and the git-routing environment;
  a token minted for one state is refused after the index, HEAD or
  environment changed (a fresh decision and token follow), and it is still
  one-shot.
- HIGH — every `git commit` was treated as recording the complete index.
  `git commit <pathspec>`, `--only`/`-o`, `--include`/`-i`, `--patch`/`-p`,
  `--interactive`, `--pathspec-from-file`, `--fixup`/`--squash`, a reused
  message (`-c`/`-C`) and any abbreviated or unknown option construct a
  DIFFERENT commit and now read VERIFIED-BUT with the form named; only
  forms that record the complete index (or the worktree with `-a`) keep the
  wording (`-m`, `-am`, `-qam`, `--amend --no-edit`, `-F`, `--message=`, …).
- HIGH — a porcelain approximation was the equality check. The display
  status follows git (skip-worktree entries unchanged, mode changes hidden
  under core.filemode=false, a symlink materialised as a file under
  core.symlinks=false); the strong wording now uses a separate STRICT
  verifier that records every entry the byte comparison could not vouch
  for — sparse/assume-unchanged entries, an index mode the filesystem
  cannot represent, a materialised symlink, a conversion-attributed path
  whose bytes differ, a HEAD/index mode-only difference the display
  suppresses — next to every ordinary difference; calibrated against git's
  own EMPTY status for each case. A commit is judged on the index subset,
  a push on all of it.
- HIGH — `ls-tree HEAD` and `rev-list` honoured refs/replace/* while a push
  transfers the ORIGINAL objects (measured: a replaced HEAD read as 1
  commit of history, 3 without). Every git read carries
  `--no-replace-objects` and GIT_NO_REPLACE_OBJECTS=1; calibrated: plain
  git lists the replacement tree, the gate lists the original.
- HIGH — inspection scrubbed GIT_DIR-style variables but the shell that runs
  the command inherits them (calibrated: with an ambient GIT_DIR plain
  `git -C <repo> rev-parse HEAD` answered another repository while the
  scrubbed inspection answered the target). Any routing / config-injecting
  variable present in the hook's environment now demotes the wording,
  names the variable, and is bound into the token; the scrub list gained
  GIT_CONFIG_GLOBAL/SYSTEM, GIT_CEILING_DIRECTORIES and
  GIT_DISCOVERY_ACROSS_FILESYSTEM.
- HIGH — the git floor was 2.36 while GIT_NO_LAZY_FETCH arrived with the
  May-2024 security batch (CVE-2024-32465 remediation: 2.45.1 and the
  maintenance releases 2.39.4 / 2.40.2 / 2.41.1 / 2.42.2 / 2.43.4 /
  2.44.1) — an older git ignores it and would run fetch helpers on a
  missing object. The floor is now that fixed-version matrix, patch level
  parsed and pinned both sides of every boundary.
- HIGH — legacy `$GIT_DIR/remotes/<name>` (and `branches/<name>`)
  definitions carry their own Push: lines; their presence makes the push
  range UNKNOWN (VERIFIED-BUT).
- HIGH — on Windows the git reader skipped `select()` and blocked in
  `os.read()`; a bounded reader THREAD now enforces the deadline and the
  cap while reading (killing the child so the blocked read ends), used by
  `_git_output` and `run_contained` there — exercised on POSIX, UNMEASURED
  on Windows (the platform stays unverified; the hook denies there).
- MEDIUM — `_kill_group` read EPERM as "gone". POSIX EPERM means no
  signalable member; a zombie-led group answers it on macOS (measured), but
  so would a live member we may not signal — EPERM is now checked against
  a process listing (`ps -A -o pid=,pgid=,stat=`), a live member left
  behind is a containment FAILURE (`run_contained` reports "sweep: …", the
  hook denies, `digest_hard` reads unknown).
- MEDIUM — completion waited for pipe EOF, so a leader that exited while a
  helper kept stdout held the call to the full deadline (measured: exit 7,
  returned as a timeout). The leader's exit is observed with
  `waitid(WNOWAIT)` during the read, the helper swept at once, the pipe
  drained, then the leader reaped — calibrated with a helper that inherits
  the open pipe (rc 7, no timeout, under 4 s).
- MEDIUM — tests asserted warnings and environment presence; execution-
  level RED tests now cover the token race (index mutation, HEAD move,
  environment change), commit pathspecs and modes, add-then-remove
  history, replace refs, sparse/mode/symlink entries, legacy remotes, the
  version matrix, and the crash guard.
- Framing (accepted): a text hook is not an execution boundary — it
  compares a snapshot of one repository while the command can address
  another; the PLAN's daemon residual (enforcement at the git execution
  boundary) stands. This release makes the wording TRUE for what it can
  see and loud about what it cannot.

### Round 35 (Codex, do-not-ship: 4 HIGH, 3 MEDIUM — all addressed)
- HIGH — the write-mode snapshot (`_git_state`) still ran `git status`,
  which refreshes the index and runs a configured CLEAN FILTER for any
  file whose stat data changed (calibrated on 2.55.0: same bytes, new
  mtime → the filter ran) — a helper the sealed writer could configure
  into the very tree it wrote and have the SERVER execute afterwards. The
  snapshot is now treedigest's filter-free status: the index and HEAD
  listings (`ls-files -s -t`, `ls-tree`, `check-attr`, `config` — all
  metadata reads that execute nothing) plus this process's own byte reads
  hashed with git's blob id. Vocabulary is porcelain's (matched line-for-
  line against `git status --porcelain -uall` on three real repositories)
  plus ` ~` for a path whose bytes differ from the index under a
  conversion attribute (filter/text/eol/ident/working-tree-encoding, or
  core.autocrlf) — honest, treated as dirty, explained by a legend in the
  report. Submodules: ` M` when the checked-out HEAD is not the gitlink or
  the submodule's own status is dirty (recursive, shared budget); skip-
  worktree / assume-unchanged entries and core.filemode=false are honoured
  as git does. Any failure is NOT a clean tree (refused, with the reason).
  `server.py` no longer contains a `git status` call.
- HIGH — the digest binds worktree BYTES, but git commits the INDEX and
  pushes HEAD's TREE: a staged blob that differed from the reviewed file
  kept the digest and the REVIEW VERIFIED wording while unreviewed content
  shipped (calibrated: `git add` of other bytes, worktree restored → same
  digest). The hook now inspects the tree ONCE (digest + status) and gives
  the full VERIFIED wording only when the recorded objects equal the
  reviewed worktree — a push needs HEAD ≡ index ≡ worktree with nothing
  untracked (an untracked file the review saw is content the push omits),
  a commit needs every index blob to equal its worktree bytes. Otherwise
  VERIFIED_BUT names the differing lines (`MM a.txt`, `A  b.txt`, `?? …`),
  and an unreadable status says so. `git update-index --cacheinfo` (no
  worktree involvement) is covered by the same check.
- HIGH — group identity: `communicate()` REAPS the leader, and the parent
  then `killpg`'d a pid that could already belong to a stranger. The one
  contained runner (`run_contained`, used by `digest_hard` and the hook's
  parent) streams stdout to EOF or the deadline, lets a leader that closed
  its stdout finish exiting UNREAPED (`waitid(WNOWAIT)`, measured on macOS
  to keep the pid reserved and the exit status readable), sweeps the group
  by that still-reserved id, then reaps. `_kill_group` REFUSES to signal a
  group whose leader Popen has already reaped (calibrated: the reaped case
  leaves the orphan alive and returns False). Measured: a zombie-led group
  answers `killpg` with EPERM on macOS (ESRCH on Linux) — both read as
  "nothing left to signal".
- HIGH — the file-based submodule HEAD reader could not follow commondir /
  linked worktrees, chained symrefs, reftable or a truncated packed-refs,
  and answered a STABLE "?" for all of them. A submodule is now identified
  by `git -C <sub> rev-parse --show-toplevel HEAD` under the safe
  configuration (the toplevel is checked against the submodule path, so a
  discovery that walked up to the parent is refused); an uninitialised
  submodule is the stable identity "absent" (git treats it as clean);
  every other unresolved case makes the WHOLE digest "unknown"
  (calibrated: a HEAD naming a missing ref, a garbage HEAD). Digest v6 for
  trees with submodules only; the three measured repositories keep their
  v5 digests byte-for-byte.
- MEDIUM — the watchdog's tick bound is computed by the PARENT from the
  monotonic attempt budget (`int(budget // 5) + 2`) and passed in — the
  child never derives it from `date`; the count is journaled in the spawn
  record and pinned.
- MEDIUM — /proc reads: `_read_at` takes limit + 1 bytes and REFUSES an
  over-cap file (EFBIG → UNCERTAIN) instead of returning a silently
  truncated prefix that could cut the marker's boundary; the caps are
  named (`ENVIRON_MAX_BYTES` 4 MiB = 2× the default Linux environment
  ceiling of RLIMIT_STACK/4; `STATUS_MAX_BYTES`) and pinned at-cap/over-cap.
- MEDIUM — `_wait_for_capacity` returned True on a lodged `codex_cancel_run`
  intent and the caller ignored it, spawning again over a cancel. The
  boolean now ends the run: journaled `cancelled` with the time ACTUALLY
  waited, no spawn; attempt telemetry (attempt count, retry class) is
  incremented only immediately before a real spawn, so a refused or
  cancelled wait never counts as an attempt.
- Found in-house while landing: (a) git reads scrub GIT_DIR / GIT_WORK_TREE /
  GIT_INDEX_FILE / GIT_OBJECT_DIRECTORY / GIT_COMMON_DIR / GIT_NAMESPACE and
  the GIT_CONFIG_* injectors from the environment (calibrated: a caller's
  GIT_DIR makes plain `git -C <sub> rev-parse` answer for another
  repository; MEASURED on 2.55.0 that `-c` outranks GIT_CONFIG_PARAMETERS
  and GIT_CONFIG_{COUNT,KEY,VALUE} — the scrub is defence in depth for
  those, the real fix for the identity keys); (b) on this host a same-user
  process caught mid-exec/mid-exit refused its environment in 1 of 3 runs
  and made the whole survivor scan UNKNOWN (a cancel then refused to
  terminalize) — unreadable/uncertain pids are re-examined up to 3 times
  200 ms apart before the scan gives up on them, and a pid that stays
  unreadable still makes the scan UNKNOWN; (c) the retry log line named the
  attempt off by one once the counters moved behind the wait.

### Round 34 (Codex, cut by its content classifier; partial findings folded in)
- HIGH — even with drivers and textconv disabled, `git diff` still runs the
  CLEAN FILTER of a `filter=` attribute (reproduced on Git 2.55.0 by the
  review and by a calibrated test here). Digest v5 therefore reads the
  worktree's bytes ITSELF — no `git diff`, no `git status`; the only git
  calls left are metadata listings (`rev-parse`, `ls-files`) that execute
  nothing — and identifies a submodule by its checked-out HEAD read from
  files, never by running git inside it. The digest is HEAD-independent: a
  review vouches for content, so `git add` and `git commit` of the reviewed
  content keep the review VERIFIED through `git push`. Budgets re-sized from
  MEASURED repositories (1.0 / 24.9 / 11.8 MB of content, largest file
  1.3 MB, ≤ 0.25 s to hash everything): 64 MiB per file, 1 GiB total,
  100,000 entries, 20 s — ≥ 40× headroom, pinned at-cap/over-cap.
  The review's configured-helper audit adds: GIT_NO_LAZY_FETCH=1 (a partial
  clone would otherwise run `fetch` and its transport/credential helpers on
  a missing object) and a git version floor of 2.36 (older git reads a
  boolean `core.fsmonitor=false` as a hook PATH) — below it the digest is
  "unknown".
- HIGH — containment on every completion path: an inner git timeout kills
  only the git leader, and a helper it started could outlive a NORMAL exit.
  digest_hard's parent and the hook's parent now sweep the child/worker
  process group on every completion (calibrated: a git that forks a
  background helper and hangs — both helpers die after a normal "unknown"
  result). The Windows denial is returned before any git or digest work.
- HIGH — the /proc scan pins each /proc/<pid> as a directory descriptor,
  takes the REAL uid from its `status` file (the directory is root-owned
  for a non-dumpable process of ours), reads `environ` relative to it, and
  treats only a definite disappearance (ENOENT/ESRCH) as a skip: permission
  denied on our own live process is UNREADABLE and any other failure — on
  the directory or a file — is UNCERTAIN; both raise.
- HIGH — the request budget over spawn/publication: the watchdog gains a
  TICK bound (one tick per 5 s poll) next to its wall-clock deadline, so a
  clock rollback under a detached enforcer cannot extend a run; the stream
  wait re-reads the remaining budget after publication (a zero budget times
  out immediately). Process creation itself remains outside any deadline
  (documented residual; the host's per-call timeout bounds it).
- HIGH (pre-existing) — `_git_state` discarded `git status`'s exit code: a
  failed or timed-out status read as a clean tree and could admit an
  autonomous write. It now fails closed (not a write target).
- MEDIUM — retry state (attempt count, retry class, capacity wait) is
  recorded only for a retry that actually happens, and the wait journaled
  is the wait that actually elapsed.
- LOW — the pre-spawn budget refusal closes both spool handles; a failure
  opening the second handle closes the first.
- Found in-house while landing: (a) the first cut of digest v5 hashed each
  entry's INDEX state (tracked vs untracked), so `git add` changed the
  digest — only bytes and git identity are hashed now, and the test pins
  that `git add` and `git commit` of reviewed content keep it; (b) MEASURED
  step by step: `git diff` (any flags) and `git add` run a configured clean
  filter, `rev-parse` / `ls-files` / the whole digest never do; (c) a slice
  edit deleted the macOS vanished-or-zombie helper while the guard asserted
  only the Linux names — restored, with every helper name now asserted.

### Round 33 (Codex, do-not-ship: 4 HIGH, 2 MEDIUM, 1 LOW — all addressed)
- HIGH — git honours configuration from the repository being READ:
  core.fsmonitor runs a helper on status/diff/ls-files (MEASURED here: a
  configured helper ran on `git status`), core.hooksPath aims hooks
  anywhere — the class fixed upstream in Codex (CVE-2026-19592). Every git
  call in treedigest.py and server.py now carries `-c core.fsmonitor=false
  -c core.hooksPath=<null device>` (command-line config outranks any file)
  with GIT_OPTIONAL_LOCKS=0 / GIT_TERMINAL_PROMPT=0; calibrated regression:
  the helper runs under plain git, never under a digest or config read.
- HIGH — the hook's worker computed the digest through digest_hard(), a
  SECOND session outside the parent's kill domain. The worker now digests
  in-process, so its git children share its group and the parent's group
  kill covers them (calibrated: a git that hangs inside the digest dies
  with the worker at the deadline). Process creation itself precedes the
  timed wait and is documented as the residual the host's 90 s timeout
  bounds.
- HIGH — procenv classifies a failed environment read by KIND: vanished
  (skip), permission-denied on our own live process (unreadable → UNKNOWN),
  anything else such as EMFILE/ENFILE/EIO (uncertain → UNKNOWN); the owner
  is checked BEFORE any read, so a privileged caller never inspects another
  user's environment; a final deadline check closes the scan.
- HIGH — the request budget is read at the moment of use: the attempt
  deadline is derived from the request clock AFTER the spawn (setup time is
  no longer added back), the stream wait and the 80 % sentinel read the
  remaining budget, the request clock starts at tool ENTRY (before digest /
  prompt / journal preparation), and a capacity-shed wait plus a retry must
  fit in what is left or the run ends as a timeout.
- MEDIUM — a failed `git config --list` is UNKNOWN configuration (never
  defaults → not lone); submodule.recurse is honoured as git's fallback for
  push.recurseSubmodules; a remote with several url/pushurl values fans out
  and is not lone.
- MEDIUM — the Windows token store is DENIED as unverified in this release
  (no directory handle, reparse-point or ACL validation; Python < 3.13 does
  not make a 0o700 directory private there).
- LOW — the token sweep cap is a loud limit: a store past 4,096 entries
  refuses to mint with the reason; at-cap/over-cap pinned.
- Framing correction from the review, accepted: "no decision surface" means
  detection aside — `_detected()` (deliberately over-broad) decides whether
  the hook speaks at all; classification only chooses wording.

### Round 32 (Codex, do-not-ship: 3 HIGH, 3 MEDIUM, 1 LOW — all addressed)
- HIGH — the run budget is ONE budget per MCP REQUEST: every attempt (retry)
  and both abraham phases share it (each attempt used to get a fresh
  budget, so a request could outlive the client's hard 4 h per-call
  timeout); an attempt that cannot get 120 s of what is left is refused
  as a timeout instead of being spawned. The band's maximum is 3.5 h —
  30 min under the plugin's 4 h MCP call timeout (chained ceiling test
  compares the MAXIMUM, not the default). The 80 % warning names the
  attempt budget and the request budget.
- HIGH — the hook now runs its whole evaluation in a WORKER process group
  under a hard deadline held by the parent hook process, which does
  nothing blocking itself and DENIES on timeout (calibrated: a FIFO
  transcript that blocks open() forever → deny within the deadline, worker
  gone). `CODEX_PUSH_GATE_EVAL_DEADLINE_S` (2..80 s, default 60; invalid →
  default) sits under the 90 s hooks.json timeout. Inside the worker the
  transcript scan checks its budget on EVERY line and once more before
  returning (a slow parse of one line returned evidence late), git config /
  branch lookups go through treedigest's capped streamed reader, and the
  token sweep walks the store with scandir under a count cap.
- HIGH — an unusable token store is a DENY with the reason and the fix,
  never "ask" (on Windows `os.open` on a directory fails with EACCES, so
  the store silently degraded to the mode-dependent decision). A path-based
  Windows store exists but is UNVERIFIED — it denies until it works.
- MEDIUM — REVIEW VERIFIED resolves git's effective remote
  (branch.<b>.pushRemote > remote.pushDefault > branch.<b>.remote), rejects
  remote GROUPS, push.recurseSubmodules on-demand/only, and any explicit
  destination that is not the same branch name or fully qualified under
  refs/heads/.
- MEDIUM — treedigest kills the child's process GROUP by its recorded id
  even when the leader already exited; git children stay in their
  caller's group (no nested session escapes the outer kill); a breach
  inside the worker kills the git pid.
- MEDIUM — procenv: a same-user process whose environment cannot be read
  (Linux Yama ptrace scope, or vanished mid-scan) makes the scan UNKNOWN —
  it raises, and the watchdog reads U — instead of an empty list that
  claims quiescence; foreign users' processes are skipped by uid; the scan
  has a 10 s deadline and an entry cap.
- LOW — `plugins/codex-oracle/spike/app_server_spike.py` is added by path
  in the ship commit; `__pycache__` is ignored.
- Found in-house while landing the round: (a) the first cut of the
  "unreadable same-user process → UNKNOWN" rule tripped on every scan —
  MEASURED: the only such pid on this Mac was a `(sleep)` that had exited
  between the listing and the read — so a failed read is now classified as
  vanished / zombie (skip; Linux from the /proc tree, macOS from kill(0) +
  ps state) versus alive-and-unreadable (UNKNOWN, raises); (b) the
  120 s attempt floor never exceeds half the configured budget, so a
  seconds-long test budget still spawns its first attempt.

### Round 31 (Codex, needs-changes: 2 HIGH, 3 MEDIUM, 1 LOW — all addressed)
- HIGH — `ps -axE` was never a Linux capability: procps-ng has no `-E`
  (BSD-only), so the marker scan raised on Linux and the watchdog went
  degraded. Enumeration now lives in `procenv.py` (stdlib): macOS = BSD
  `ps -ax -o pid=,stat=` + KERN_PROCARGS2 per pid; Linux = a `/proc` scan
  (`environ` + `stat` zombie state); `--list <tag>` CLI for the watchdog
  (exit 2 = unknown, never quiescence). server.py delegates; the /proc
  scanner is driven by a fake proc tree in tests; no `-axE` remains.
- HIGH — the digest is ONE implementation, `treedigest.py`, run by BOTH
  twins in a child process group under a HARD deadline (SIGKILL at
  deadline + grace): no in-process deadline can interrupt a blocking read,
  and the reviewer's probe returned a valid digest after its deadline.
  Inside the child every git output is streamed under select() with a byte
  cap (a `--binary` diff is unbounded), the deadline is re-checked after
  every blocking step, and the whole thing is "unknown" on any breach.
  Calibrated regressions: a child that hangs forever is killed; a hung git
  is killed inside the child; a late open never yields a valid digest.
  hooks.json declares a 90 s hook timeout above the hook's worst-case sum
  (the host's default is 600 s and a timed-out hook fails OPEN); the
  transcript scan has its own 10 s budget and says so.
- MEDIUM — REVIEW VERIFIED wording only when the classifier can PROVE the
  update set is the reviewed HEAD: known-inert push options only
  (abbreviated `--fol`, clustered `-fd`, unknown flags read VERIFIED-BUT),
  at most one refspec, no tag destination, and bare pushes checked against
  git's effective configuration including the command's own `-c`
  (push.default simple/current/upstream, no remote.<r>.push, no mirror, no
  push.followTags).
- MEDIUM — a token minted for an undigestable tree binds to the COMMAND
  only and its wording says so ("for this command ONLY — the tree could not
  be digested"); the tree-bound wording is reserved for a 12-hex digest.
- MEDIUM — the token store is opened as a validated directory descriptor
  (real directory, no symlink, owned by this user, mode 0700); every file
  operation is dir_fd-relative; the sweep touches only expired
  `<16hex>.json` entries, bounded; a consumed token is unlinked in
  `finally` (a malformed one too).
- MEASURED again on Claude Code 2.1.258 (auto-updated mid-program) with
  the round-32 tree via `claude -p --plugin-dir`: a `git push --dry-run` in
  an unreviewed scratch repo was DENIED with the token in the model-visible
  reason (the digest child ran inside its budget; git never ran); a plain
  command ran with no hook output. Both kill-mid-call E2Es PASS on the
  drifted codex-cli 0.153.0; the source reference was re-aligned to
  rust-v0.153.0 so the transient-retry classifier verifies against the
  installed binary's own text again.
- LOW — `procenv.py` and `treedigest.py` are new files: the ship commit
  adds them by path (never `commit -a`), and the post-install probe runs
  from the installed cache, not the working tree.

### Round 30 (Codex, do-not-ship: 3 HIGH, 2 MEDIUM — all addressed) + host measurements
- HIGH — the gate now DENIES with a one-shot acknowledgement token instead
  of returning "ask": a hook "ask" is honoured only in some host permission
  modes (upstream reproduced auto-mode auto-approval, claude-code#51255;
  MEASURED here on Claude Code 2.1.257: `claude -p` in manual mode blocks
  on both "ask" and "deny"; the other modes cannot run under `-p`), while
  "deny" is authoritative in every mode. Every detected push/commit is
  denied with the review state (VERIFIED / VERIFIED-BUT / STALE / PENDING /
  none) and a token bound to the exact command + tree digest (0600 file
  under ~/.claude/logs/codex-oracle/push-ack, 10-minute TTL, consumed on
  first read); re-running the same command with `CODEX_PUSH_ACK=<token>`
  in front (`$env:CODEX_PUSH_ACK='…';` under PowerShell) proceeds under the
  session's normal permissions. An unusable token store degrades to "ask",
  loudly. The 2026-09-02 ruling (no decision surface; parsing chooses
  wording only) is unchanged — only the mechanism moved from a host-mode-
  dependent prompt to a mode-independent deny.
- HIGH — digest v4 (both twins): git's diff is taken in its canonical,
  configuration-resistant form (`--no-ext-diff --no-textconv
  --ignore-submodules=none --binary --full-index --no-renames --no-color`;
  `status --porcelain=v1 --untracked-files=no --ignore-submodules=none`).
  MEASURED collision before: with `diff.external=/usr/bin/true` two
  different contents of a dirty file produced the same digest; a binary
  change printed only "Binary files differ". Regressions pin both.
- HIGH — untracked entries are opened ONCE with O_NOFOLLOW|O_NONBLOCK, the
  descriptor is classified with fstat, and reads are capped at cap+1 bytes:
  the previous lstat-then-open let a path swapped for a FIFO/device/symlink
  block or read unboundedly, and a PreToolUse hook that outlives its
  timeout fails OPEN. Explicit budgets, each loud (the reason says why the
  digest is unknown) and pinned at-cap/over-cap in both twins: 10 s per git
  call, 20 s total, 8 MiB per file, 64 MiB total, 20,000 entries — sized so
  the digest voids itself well inside the host's 60 s hook timeout, never
  to cap a repository. (Measured while writing the regression: git never
  lists a FIFO or socket as untracked — it cannot commit one — so the
  open-once path is reached only through the swap race, which the test
  models by feeding the names through the enumeration under a lying
  lstat; an unreadable untracked entry is the plain way to a loud
  "unknown".)
- MEDIUM — the no-server watchdog kills ONLY env-verified pids: new
  stdlib-only `procenv.py` (the single KERN_PROCARGS2 / /proc/environ
  implementation, loaded by server.py by path) is invoked by the watchdog
  per candidate at the deadline; text-only matches are never signalled and
  are logged as `unverified-marked` (degraded custody, bounded exit).
- MEDIUM — REVIEW VERIFIED wording is reserved for a LONE `git push` of the
  reviewed HEAD (no refspec, HEAD, or the current branch as the source;
  none of --all/--mirror/--tags/--delete/--repo) or a lone `git commit`;
  compound commands (`git apply … && git commit && git push`), other refs
  and tag pushes read VERIFIED-BUT with the reason. `--exec` joins the
  opaque transport flags.
- MEASURED on the deployed host (Claude Code 2.1.257, 2026-09-02): 1.17.1's
  hooks were DEAD — the host execs the hook `command` verbatim, so
  `${CODEX_ORACLE_PYTHON:-python3}` was looked up as a literal executable
  name (a14d2bf's fix never deployed: same version number, version-keyed
  cache); hooks are snapshotted at session start and `--resume` restores
  the original snapshot (a session begun before the install kept spawning
  1.17.0's `python`). The 1.17.2 form (`python3` + `${CLAUDE_PLUGIN_ROOT}`
  in args) spawns, stays silent on plain commands, and its decision stopped
  a `git push --dry-run` in a calibrated `claude -p --plugin-dir` probe
  (known-bad form red first). Live: a real Claude Code process exit
  detached the in-flight round-30 review and the next session adopted it
  via codex_resume_run.

### Round 29 (Codex, do-not-ship: 5 HIGH, 1 MEDIUM, 1 LOW — all addressed)
- HIGH — digest v3, BOTH twins: every git operation runs at
  `rev-parse --show-toplevel` (a nested cwd enumerated only its own
  subtree's untracked files — certifying a different tree), output is
  hashed as BYTES (a non-UTF-8 diff crashed text-mode decoding, and a
  nonzero hook exit lets the action proceed), untracked entries carry their
  git-relevant identity (exec bit; a symlink hashes its TARGET PATH, not
  the referent — a same-content retarget changed nothing before), fifos/
  sockets and >8MiB entries void the digest, and ANY exception returns
  "unknown" instead of escaping. Regressions: nested-cwd parity + staleness,
  chmod, symlink retarget.
- HIGH — ANSI-C quoting can BUILD the verb (`git $'\x70\x75\x73\x68'`,
  `g$'\x69't p$'\x75'sh`): `$'` anywhere is now detection by itself. Full
  evasion-proofing of a text hook is impossible (documented trust model);
  execution-boundary enforcement is written into the 1.18 plan.
- HIGH — background notifications bind to their TOOL: the host-generated
  summary before the `{"result":…}` payload must reference code_review, so
  a copied review header inside another tool's result cannot render
  VERIFIED (forged-other-tool regression; the green fixture now models the
  real notification shape).
- HIGH (residual, explicit) — `permissionDecision:"ask"` host behaviour
  remains UNPROVEN until the post-deploy walk: known-green = a prompt
  appears for `git push`; known-red fallback = switch to "deny" + an
  approval-token workflow (also written into the plan). Worst case today
  degrades to the pre-1.17.2 nudge, never below it.
- MEDIUM — marker identification is ENV-VERIFIED: `ps -E` mixes argv and
  env in one string, so a process whose ARGV merely contained the marker
  (an operator's grep, a decoy) could be SIGKILLed. Candidates are now
  verified via exact env reads (macOS KERN_PROCARGS2 — MEASURED both
  directions before wiring; Linux /proc/<pid>/environ): argv-only matches
  are excluded, unverifiable candidates stay DETECTED survivors but are
  never kill targets. The no-server watchdog keeps text-match kills as a
  documented last-resort residual.
- LOW — remaining "opens the gate" comments rewritten to wording-only
  language. (Rig lesson recurred: a function-body slice deleted the eight
  parser constants between two defs — caught by the suite, restored;
  count-invariant checks added to the workflow.)

### Round 28 + DESIGN RULING (user, 2026-09-02): the push gate ALWAYS asks
Round 28 found 3 more HIGHs in gate classification (mutate-then-push chains
approved; ANSI-C `$'\x69'` and variable-built `G=git; "$G" push` silently
missed; the digest ignored untracked file CONTENTS) — the fifth consecutive
round of shell-semantics counterexamples. The user ruled for the
future-proof shape: **a detected push/commit ALWAYS prompts; nothing
auto-opens.** Parsing now informs only the prompt's WORDING — a completed
digest-matching review renders "REVIEW VERIFIED … approve to proceed" (one
keystroke), stale/pending/absent reviews say what is wrong, and a
non-direct command carries an explanatory note — so a parsing gap can cost
a sentence, never a silent bypass. Detection stays maximally broad (four
channels + an expansion-bearing catch-all: `$`/backslash/backtick alongside
a push/commit word is itself detection), because silence is the one
remaining failure mode. Also landed from round 28's findings:
- both digest twins hash every UNTRACKED file's path + content bytes
  (unreadable/oversized voids the digest) — an untracked edit now reads
  STALE (tested);
- mutate-then-push chains (`> file`, PATH=/IFS=/LD_*/DYLD_* assignments,
  `trap`), `--receive-pack/--upload-pack/--exec-path`, and exec-capable
  `-c` keys (allowlist trimmed to presentation keys) all mark a command
  non-direct;
- `git commit -c HEAD` is commit's own flag, not global config (global
  options parsed before the subcommand only);
- a missing/unknown tool_name is treated like any non-Bash runtime.
The gate's remaining role is exactly its documented trust model: a
guardrail against forgetting, now with zero classification-bypass surface.

### Round 27 (Codex, do-not-ship: 3 HIGH, 3 MEDIUM, 1 LOW — push gate; all addressed)
- HIGH — Bash dollar quoting (`g$''it`, `g$'i't`) and quote characters
  inside inert regions (a comment's apostrophe, a here-doc body) defeated
  both the regex and the POSIX tokenizer, so the gate stayed SILENT.
  Detection gained a QUOTE/DOLLAR-STRIPPED channel (every quote character
  and `$` removed from the de-escaped copy = the worst case the shell could
  run); a miss on that channel is now a prompt, never silence. Detection
  only decides whether the gate looks — classification still fails closed
  on `$`, escapes, and untokenizable text.
- HIGH — shlex coalesces punctuation runs (`&&(`, `)&&`), so an
  exact-token check let `git status&&(eval cd /other;git push)&&true` read
  as direct: all-punctuation tokens are now classified by their
  CHARACTERS — any `(`/`)` inside is grouping (reject), a pure `;&|` run is
  a separator.
- MEDIUM — `git -c KEY=VAL` can DEFINE EXECUTION (`alias.x='!cd …'`,
  core.hooksPath, core.sshCommand, credential.helper, diff/merge drivers,
  `--config-env`, `--exec-path`): only a small inert allowlist
  (user.name/email, commit.gpgsign, push.default, color.*, advice.*,
  core.autocrlf, i18n.*) stays direct; everything else asks.
- MEDIUM — PowerShell is registered but a POSIX tokenizer cannot positively
  parse it and no PowerShell runtime exists here to calibrate one: a
  detected push under ANY non-Bash tool now ALWAYS asks, with its own
  reason — the honest fail-closed over an unproven capability (Runtime
  Capability Law).
- MEDIUM (residual, stated) — whether the installed host actually PROMPTS
  on `permissionDecision: "ask"` (anthropic/claude-code#81041 reports
  silent execution on some versions) cannot be calibrated from tests; the
  reason is duplicated into additionalContext so the worst case degrades to
  the previous nudge behaviour. First live measurement = the post-deploy
  walk: a `git push` from a session with the new hooks loaded.
- LOW — the trust-model docstring now states the digest is taken at
  DISPATCH time (edits during the review make the review STALE), matching
  the server.

### Round 26 (Codex, do-not-ship: 3 HIGH, push-gate detection — all addressed)
- HIGH — escape PARITY: `\\<newline>` keeps one backslash and a real
  newline, so a push on the next line executes while the de-escaper had
  rewritten it out of existence. Detection now runs on the RAW text, the
  de-escaped copy, AND the quote-removed token stream — any channel seeing
  a push is enough (detection decides only whether the gate looks).
- HIGH — quote removal joins words (`g""it push`, `git pu""sh`): the raw
  regex missed them and the hook stayed silent with no review. Detection
  and classification now share ONE quote-aware tokenizer; the token channel
  sees `git` + `push`. With no review these prompt; with a matching review
  they auto-open — they are direct pushes of the reviewed tree.
- HIGH — shlex's default `#` comment handling swallowed the `;` inserted
  for an unquoted newline and every later command (`git status # ok<nl>
  source ./x && git push` read as one direct segment). Comments are
  disabled in the shared tokenizer (a comment's text becomes ordinary
  tokens — over-detection only). Regressions for comment-followed
  source/eval/env -C, both parity shapes, CRLF; a trailing comment on a
  direct push still auto-opens.

### Round 25 (Codex, do-not-ship: 4 HIGH, all in the push gate — all addressed)
- HIGH — `_review_state` accepted an ok signature ANYWHERE in any
  tool-result body (a pre-existing a14d2bf defect, the same forgery class
  the abraham gate had): a status:error result quoting the ok marker opened
  the gate. Signatures are now ANCHORED — the result's first line (after
  unwrapping the MCP envelope) must fullmatch the ok header — and BOUND: a
  foreground tool_result counts only when its tool_use_id belongs to a
  code_review dispatch; background notifications are read from the
  `{"result": …}` payload's first line. Regressions: quoted marker in an
  error body, an unbound result id, and a forged notification all ask; the
  genuine foreground/wrapped/background shapes still open.
- HIGH — POSIX removes a backslash-newline pair WITHOUT inserting
  whitespace: the de-escaper's space turned `gi\<nl>t push` into `gi t
  push` and missed the detection. The pair is now removed outright; both
  `gi\<nl>t` and `pu\<nl>sh` forms are tested.
- HIGH — shlex discards newlines as whitespace, so `git status<nl>eval "cd
  /other; git push"` was ONE segment headed by git. Unquoted newlines are
  turned into `;` separators before tokenizing (a newline inside quotes is
  argument text and still auto-opens); newline-led eval/source/env forms
  are regression-tested.
- HIGH — PowerShell's escape/continuation character is the BACKTICK
  (`g`it`, "git `<nl>push"): both dialects are normalized for detection
  regardless of the hook's tool name (over-detecting a dialect the command
  is not in is harmless), and a backtick in a detected command fails
  closed. Residual stated: no pwsh on the deployed machine — PowerShell
  behaviour is verified against the documented grammar, not a runtime.

### Round 24 (Codex, do-not-ship: 2 HIGH, 1 MEDIUM, 1 LOW — all addressed)
- HIGH — backslash escapes beat the gate on both sides: `g\it push` is
  `git push` to the shell but not to the raw-text regex, and `\cd /other`
  hid the cwd verb from the parser. Detection now runs on a conservatively
  DE-ESCAPED, line-joined copy (what the shell executes), and any backslash
  in a detected command fails closed (an escape can hide a verb or a flag).
- HIGH — abraham's phase gate searched the first 300 chars for the ok
  marker, forgeable by failure text that quoted it: the FIRST LINE must now
  be exactly the server's ok header (anchored regex); a forged marker inside
  an error body is a regression test that asserts no write dispatch.
- LOW/HIGH together — the positive parse moved to a QUOTE-AWARE token
  stream (shlex, posix, punctuation_chars): quoted text is data, so
  `echo "ready" && git push` and `git commit -m "fix(parser)"` auto-open
  with a valid review, while shlex's own quote removal keeps `git "-C"` a
  repository switch; unquoted grouping, any `$`, any backslash, backticks,
  and unbalanced quotes stay fail-closed. 24 pytest cases in both
  directions.
- MEDIUM — the watchdog's consecutive-unknown counter now resets on a
  successful LEADER probe too (a live leader is positive evidence);
  regression: a marker-blind ps with a healthy leader keeps enforcing past
  the maxu budget.

### Round 23, resumed (Codex, needs-changes: 1 HIGH, 1 MEDIUM, 1 LOW — all addressed)
- HIGH — the round-22 "transparent prefix" set was too generous: `eval`
  REPARSES its arguments (`eval git "-C" /other push` → `git -C /other
  push`, the quotes vanish) and `source`/`.` EXECUTE a file. They are now
  OPAQUE verbs — any segment they lead fails closed, git text present or
  not (the first cut only failed them when `git` appeared in the segment,
  which let `eval cd /other && git push` read as an innocuous segment).
  Independently, the shell removes quotes, so `git "-C" /other push` IS a
  repository switch: flag detection now strips quotes before comparing and
  fails closed on any backslash escape; the case-sensitive belt sees quoted
  `-C` too. Matching-digest regressions for eval/source/./quoted/escaped
  forms; `git commit -m "quoted message"` still auto-opens.
- MEDIUM — a permanently blind ps left the watchdog looping forever while
  "enforcing" nothing: it now invokes an ABSOLUTE ps (/bin/ps, deployment-
  verified; injectable for tests), counts CONSECUTIVE unknown scans, and
  after `maxu` (60 ≈ 5 min) records a machine-readable `degraded
  ps-unavailable exit=2` line and exits 2 — a dead enforcer makes the run
  ORPHANED (never adoptable, still stoppable through the server's own
  marker-verified cancel), the fail-closed direction. Both shapes tested:
  recovery after transient failures, and permanent failure → exit 2.
- LOW — the app-server spike's tempdir/subprocess/provider work moved behind
  a `__main__` guard (import is side-effect free, tested); generated
  bytecode removed (gitignored).

### Round 23 (Codex, partial — the review hit the 3600s runtime deadline and was
killed by the plugin's own watchdog; its thread was resumed)
- Its partial output named one more instance of the round-22 defect: the
  marked-survivor cancel refusal interpolated survivor PIDs into a raw
  `kill -9 …` instruction — the pid form the narrow pgid lint did not search,
  with the same reuse hazard by the time an operator acts. Replaced with
  marker-verified guidance (pids listed for INSPECTION only, kills happen at
  signal time inside codex_cancel_run), and the source lint widened to ANY
  `kill -9` in Python message text (the watchdog's identity-gated sh kills
  are the only permitted occurrences).

### Round 22 (Codex, do-not-ship: 1 HIGH, 2 MEDIUM, 1 LOW — all addressed)
- HIGH — every ambiguous-survivor message handed the operator `kill -9
  -<pgid>`, outsourcing the very pgid-reuse race the code had just closed.
  All four messages (run-level survivors, collection ambiguity, both cancel
  refusals) now direct to codex_cancel_run's MARKER-VERIFIED kills and to
  `ps -axE | grep CODEX_ORACLE_RUN=<tag>` for inspection, and say out loud
  never to signal the bare group id. Pinned by a source lint (no Python
  message text contains `kill -9 -`) plus a live survivor-message check.
- MEDIUM — the watchdog's deadline sweep counted ps failures (U) toward its
  5-pass budget and exited "successfully" with survivors unverified: unknown
  now never counts as done — the sweep waits (5s, logged with throttling)
  until ps can see again; only VERIFIED kill passes are bounded, after which
  unkillable survivors are logged. Regression: a fake `ps` that fails four
  times, then recovers — the escapee still dies and the failures are
  recorded.
- MEDIUM — the positive parse strips TRANSPARENT prefixes (builtin, eval,
  command, exec, source, ., time, nohup) before judging the real verb
  (`builtin cd`, `eval cd`, `command git -C` now ask), and ANY `$` fails
  closed (parameter expansion can assemble `git -C … push` at runtime).
- LOW — belt false positives removed: `-C` lives in its own CASE-SENSITIVE
  regex (git `-c key=val` is a config flag and auto-opens), and shell-launcher
  names match only at a segment start (a commit message containing "bash"
  auto-opens). Both directions pinned in the pytest suite (21 cases).

### Round 21 (Codex, do-not-ship: 1 HIGH, 3 MEDIUM — all addressed)
(This round's first attempt hung running the repo's own process-spawning
test suites in the sandbox and was resumed on the same thread.)
- HIGH — polling is not continuity: between 5s watchdog samples the leader
  and group can die and their ids be REUSED, and the deadline killpg would
  hit an unrelated group; the post-reap sweep had the same race. Every group
  signal is now anchored to the LEADER'S IDENTITY — its `_proc_start` token,
  captured before the watchdog spawns, re-verified via a normalized
  `ps -o lstart=` immediately before any killpg; once identity is lost,
  kills are marker-verified pids only, forever. The attached sweep's numeric
  killpg is REMOVED (the leader was just reaped — its ids are free);
  `_pgid_alive` remains detection evidence routing to the nonterminal
  survivor path. (Calibration caught a real mismatch: `_proc_start`
  underscore-normalizes lstart, raw ps output does not — `lead()` now
  normalizes identically; the wrong-token regression pins group-never-
  signalled + marker-still-kills.)
- MEDIUM — the shutdown-detach branch equated a non-null watchdog HANDLE
  with a live enforcer: it now requires `watchdog.poll() is None`, and a
  crashed watchdog means the child is killed, not detached unbounded
  (tested by SIGKILLing the watchdog before shutdown).
- MEDIUM — `ps | awk` reported awk's exit status, converting a ps failure
  into an empty "quiescent" scan: the watchdog now checks ps separately,
  emits U on failure (unknown keeps the loop alive, is retried, and is
  recorded to watchdog-failures.log), and never reads unknown as clean.
- MEDIUM — the push gate's launcher DENYLIST inverted to a POSITIVE PARSE:
  the gate auto-opens only when every git-touching segment is a direct
  `git …` invocation (plain non-GIT_* VAR=val prefixes allowed; no
  -C/--git-dir/--work-tree; no cwd verbs or `env` anywhere; no grouping/
  substitution; and no QUOTE opening before the first `git` — quotes make
  naive segment boundaries untrustworthy, which is how `csh -c "…"` slipped
  a denylist). `env -C`, csh, tcsh, perl-wrapper forms are end-to-end
  tested; the old regex stays as a belt for its reasons.
- LOW (spike) — wait_event cursor race fixed (processed-snapshot advance).

### Round 20 (Codex, do-not-ship: 2 HIGH, 1 MEDIUM, 1 LOW — all addressed)
- HIGH — the marker-aware watchdog had two defects of its own: (a) its
  `grep -F CODEX_ORACLE_RUN=<tag>` pipeline matched ITS OWN grep's argv, so
  the loop could live forever; the scan now builds the needle inside awk
  from separate -v vars — no process ever carries the concatenated needle;
  (b) after the original group went extinct, the pgid could be REUSED by an
  unrelated group that the deadline branch would then SIGKILL (POSIX permits
  reuse) — group signalling is now CONTINUITY-GATED: once observed extinct,
  the pgid is never signalled again, while marked pids stay killable.
  Regressions: natural-quiescence exit (no self-match) and
  leader-exits-first escapee kill.
- HIGH — a transient retry could spawn attempt N+1 over attempt N's live
  descendants (infra survivors retain danger-full-access): unverified
  quiescence (`group_survivors`) now BREAKS the retry loop into the
  nonterminal survivor path — exactly one spawn, tested.
- MEDIUM — nested-shell detection widened to the fail-closed shape a flat
  regex can actually promise: the PRESENCE of a shell-launcher name in a
  push-matching command asks (covers `bash -lc`, quoted `"-c"`, clustered
  and prefix-abbreviated options), and an ENCODED PowerShell payload
  (`-e/-enc/-EncodedCommand`) is opaque to the gate and always asks —
  a cooperative agent has no reason to base64 its pushes.
- LOW — the app-server spike's `wait_event` advanced its cursor past events
  appended between the slice and the len() (fabricated timeouts in
  capability evidence): it now advances by the processed snapshot only.

### Round 19 (Codex, do-not-ship: 2 HIGH, 1 MEDIUM — all addressed)
- HIGH — the detached watchdog was MARKER-BLIND: an ordinary (non-scrubbed)
  marked setsid escapee survived the deadline with no server left to sweep
  it. The /bin/sh watchdog now watches leader OR group OR marked processes
  and, at the deadline, kills the group and then sweeps marker carriers
  (up to 3 passes) — deadline enforcement with no server alive covers
  everything cooperative containment can see (the reviewer's exact probe is
  now a test). The canceller also KILLS killable marked escapees
  (revalidated) before its verification, reserving the refusal for
  unkillable ones.
- HIGH — the holds-tree-lock predicate is unified: launcher refusal,
  execution barrier, and bridge-child publication all key on
  (write OR custody_cwd), and publication targets the HELD tree. Bridge and
  held-lock payloads now REPLACE the child identity instead of skipping when
  one exists — abraham's phase-2 child supersedes phase 1's, so a crashed
  server's bridges name the live writer, not a finished phase.
- MEDIUM — a NESTED SHELL with a command string (`sh -c "cd /x; git push"`,
  `pwsh -Command "…"`) hides its separators inside quotes where no flat
  regex can see: a shell launcher with -c/-Command/-EncodedCommand//c ahead
  of a push now fails closed (end-to-end decision tests for both shells).
- (Test-rig lesson, recurring: SIGKILLed direct children of the test process
  are zombies to kill-0 — reap with poll()/wait() before liveness asserts.)

### Round 18 (Codex, do-not-ship: 1 HIGH, 3 MEDIUM, 1 LOW — all addressed; the round-17 boundary ruling itself HELD)
- HIGH — `custody_cwd` was declared but not WIRED: the lock-held read child
  did not inherit the tree-lock descriptor, and a shutdown-detach let
  abraham's finally release the tree under a live (possibly
  danger-full-access) phase-1 child. Now: `custody_cwd` lives in run state;
  `_inherit_lock_kwargs` passes the held lock fd to lock-held READ children
  exactly like write children; the detach path puts the lock into custody
  (the caller's release no-ops, bridges stay planted, the child's inherited
  fd keeps the tree locked until it exits, and a dead server's custody dies
  with its fds). End-to-end test: lock held while phase 1 runs → detach →
  caller release no-ops → the DETACHED child still holds the lock → kernel
  frees it once child and holder are both gone.
- MEDIUM — cancel released custody only for `write` records: it now uses the
  journaled `custody_cwd` (start records carry it), so a lock-held read's
  custody is released by verified terminalization — the regression asserts
  the release happens via codex_cancel_run, not manual cleanup. Residual
  stated in-code: custody is in-memory, so only the OWNING server releases
  it; a cross-server cancel over-holds (fail-closed) until that server
  exits — daemon territory.
- MEDIUM — README stops claiming the OS sandbox bounds every scrubbed
  escape: read/write runs are sandbox-bounded; an INFRA run is
  danger-full-access by design and a scrubbed escapee from it retains full
  user-level filesystem capability — stated as part of what `infra: true`
  opts into.
- MEDIUM — the 1.18 plan names a CAPABILITY, not a mechanism: launchd
  cleanup kills by process-group id (Apple docs) so it does NOT catch a
  new-session escapee, and kqueue fork-tracking is documented unsupported —
  any candidate supervisor must be measured against the known-bad
  setsid+scrub probe first.
- LOW — `_kill_marked`'s docstring states the truth: the fresh re-scan
  NARROWS the pid-reuse window to the scan→signal gap, it does not close it
  (POSIX pid signalling has no atomic identity; handle-based kills are the
  daemon's).

### Round 17 (Codex, do-not-ship: 2 HIGH, 1 MEDIUM — addressed; one HIGH resolved as an explicit trust-model boundary)
- HIGH (boundary ruling) — the env marker is COOPERATIVE containment: a
  descendant spawned with env={} + close_fds + new session escapes group,
  marker and flock at once, and no in-process mechanism can hold it —
  non-bypassable custody is kernel territory (the 1.18 daemon's
  launchd/cgroup supervision, now a stated plan requirement). 1.17.2 makes
  the boundary EXPLICIT instead of implicit: the trust model is documented
  at `_marked_survivors`, in the README, and PINNED by a regression that
  proves the scrubbed escape is real and invisible to the scan (so it can
  never be mistaken for covered); the residual is bounded by codex's own
  inherited OS sandbox. Cooperative containment (which reaches everything
  codex 0.151.0 itself leaves behind) stays fully enforced.
- HIGH — custody followed WRITE MODE, not lock ownership: abraham's phase-1
  READ run (potentially infra/danger-full-access) executes while abraham
  holds the tree's write lock, and a phase-1 survivor let the finally
  release that lock. `_run_codex` now takes `custody_cwd` — the tree whose
  lock the CALLER holds — and survivors put that lock into custody whether
  the run was a write phase or a lock-held read phase; abraham passes it.
  Tested end to end: a lock-holding read run with survivors → custody, no-op
  release, second writer refused.
- MEDIUM — `_kill_marked` revalidates at KILL time: only pids present in a
  fresh marker scan are signalled, so a scan-time pid that exited and was
  reused by an unrelated process is never killed (tested both directions:
  unmarked victim untouched, marked process killed).

### Round 16 (Codex, do-not-ship: 2 HIGH, 1 MEDIUM, 1 LOW — all addressed)
- HIGH — codex 0.151.0's shell tools call setsid() (upstream source): they
  leave the run's process group, so pgid sweeps and inherited-flock
  containment cannot see them. New channel: every spawn injects a per-run
  CODEX_ORACLE_RUN env marker, which survives BOTH setsid and fd-closing;
  `_marked_survivors` finds carriers via `ps -axE` (MEASURED on a real
  setsid'd child), and the marker is verified identity for the kill. The
  sweep, the canceller's terminalization check, and the collection-ambiguity
  check all hunt marked survivors alongside the group; an unverifiable scan
  fails closed. Residual documented in PLAN_1.18: custody is process-local —
  the daemon owns it durably.
- HIGH — `turn_completed` leaked across retry attempts: attempt 1's
  completion evidence signed attempt 2's partial exit-0 as ok. Completion
  evidence (turn_completed, usage) is now attempt-scoped, reset with
  last_error/last_message at every attempt head; regression drives the exact
  two-attempt shape (complete→transient-fail, then partial exit 0) and
  asserts no status:ok.
- MEDIUM — the push gate's redirection detection covers grouped commands and
  the directory stack: `(cd /other && git push)`, `{ cd … }`, `pushd`,
  PowerShell `& { Set-Location …; Git Push }` — all end-to-end tested.
- LOW — the Windows resolver abspaths which() results (shim, node) like the
  POSIX branch.

### Round 15 (Codex, do-not-ship: 3 HIGH, 1 MEDIUM — all addressed)
- HIGH — attached runs signed `status:ok` from the exit code alone: exit 0
  with no final answer rendered a header + "[live log: …]" note that
  abraham's gate then read as a nonempty brief. OK IS EARNED on attached
  runs now, matching the adoption rule: exit 0 without (final answer AND
  (answer-file extraction OR turn.completed)) becomes a named error
  (FAKE_CODEX_NO_ANSWER regression).
- HIGH — `shutil.which()` through a RELATIVE PATH entry returns a relative
  path: inspection read a file under the server cwd while the spawn (under
  the run's workdir) would run a DIFFERENT, potentially target-controlled
  file. Every resolution (override, shim, launcher inspection) now returns
  os.path.abspath'd results (relative-PATH-entry regression).
- HIGH — the round-14 survivor branch still wrote a terminal `end` and let
  abraham/resume release the tree lock, making the tree eligible for a
  second writer while descendants might still write. Survivors now leave the
  run NONTERMINAL (a `survivors` journal phase + the cancel marker pins
  CANCELLING/stoppable), and for write runs the flock goes into CUSTODY —
  `_release_write_lock` is a no-op while custody holds, blocking new write
  dispatches and write resumes on that tree; the verified-group-death
  terminalization in codex_cancel_run ends custody and releases the lock.
- MEDIUM — the push gate's redirection detection treats newlines as command
  separators (multiline PowerShell `Set-Location …\nGit Push` was a push
  without redirection); pinned by a multiline end-to-end decision test.

### Round 14 (Codex, do-not-ship: 2 HIGH, 3 MEDIUM — all addressed)
(The review itself demonstrated the machinery: its first attempt died when a
sandboxed `mcporter` shell command hung across a system sleep; the thread was
resumed with codex_resume_run — full context, no re-ask.)
- HIGH — abraham's analysis→implementation gate enumerated three failure
  PREFIXES, so unmatched failure forms ("[dispatch refused: …]",
  status:timeout partials) flowed into phase 2 as the "implementation
  brief". The gate is now POSITIVE: phase 2 starts only on the
  server-stamped `| tool:abraham | status:ok | tree:` signature with a
  nonempty brief body (tested: a refusal-form phase 1 stops abraham with no
  write dispatch).
- HIGH — attached success/timeout/cancel paths waited only for the leader:
  codex can leave spawned processes running after its own exit, so
  descendants could keep modifying files while the end record landed and the
  write lock was released. The attached reap path now SWEEPS the process
  group (SIGKILL + verified quiescence) before anything terminalizes;
  unkillable survivors fail the run loudly (never ok) and a caller-cancel
  with survivors stays nonterminal/stoppable via the marker; the detached
  watchdog now watches the GROUP, not just the leader, so survivors cannot
  escape the deadline.
- MEDIUM — the Windows argv0 branch still carried a `codex-win32-*` wildcard
  glob: it now uses the exact `_codex_target()` package/triple, and the map
  is parametrized so all six platform mappings plus the unsupported-platform
  refusal are pinned by tests from any OS.
- MEDIUM — a relative CODEX_ORACLE_CODEX_BIN was inspected against the
  server cwd but spawned under the run's workdir (two different files):
  overrides are canonicalized once — bare names resolve in the child PATH,
  paths become absolute — before inspection or spawn.
- MEDIUM — the push gate is PowerShell-safe end to end, not just registered:
  push/commit detection is case-insensitive (`Git Push`), and repository
  redirection now covers `$env:GIT_DIR`/`$env:GIT_WORK_TREE`, `--work-tree`,
  `GIT_WORK_TREE=`, and cwd moves (`cd`/`Set-Location`/`Push-Location`) in
  either shell — always asking, with the reason independent of transcript
  availability. Pinned by end-to-end decision tests (stdin payload → JSON
  decision), not matcher-string assertions.

### Round 13 (Codex, do-not-ship: 3 HIGH, 2 MEDIUM — all addressed)
- HIGH — probe/spawn PATH divergence: native resolution used the PARENT
  env's PATH while the spawn used `_codex_env()` (which prepends
  /opt/homebrew/bin) — a parent PATH without the codex dir made the probe
  see nothing while the child still resolved the JS launcher, silently
  reviving the round-12 defect. Resolution (and the launcher-script gate)
  now run against the CHILD env's PATH and return absolute paths; a
  child-PATH-only fixture pins it.
- HIGH — the canceller could terminalize over a live GROUP: with the
  recorded launcher pid already dead, the generation loop broke straight to
  the terminal record without a pgid check. Group death is now verified
  before the canceller's end record; a surviving group fails loudly with the
  marker retained (rig: leader-dead/child-alive group; retry terminalizes
  after the group dies).
- HIGH — spool freshness was death evidence: `_writer_alive`'s 120s
  staleness clause bounds the collector's WAIT (pgid-recycling guard), but
  collection then finalized `error` over a live group. A dead pid + live
  group + stale spool is now AMBIGUOUS: collection refuses to finalize,
  names the group, and the run stays stoppable.
- MEDIUM — native discovery no longer globs `codex-*` (lexicographic
  first = wrong binary on a multi-arch install): `_codex_target()` mirrors
  the shim's exact PLATFORM_PACKAGE_BY_TARGET map (read from the installed
  0.151.0 codex.js) and only the machine's own package/triple paths are
  candidates — a wrong-arch-only install refuses rather than mis-launching.
- MEDIUM — the push gate's hook matcher covers `Bash|PowerShell` (Windows
  shell), pinned by the registration test.

### Round 12 (Codex, do-not-ship: 2 HIGH, 1 MEDIUM, 1 LOW — all addressed)
- HIGH — the deployed npm `codex` is a NODE LAUNCHER that spawns the native
  binary with stdio:"inherit" (measured in the installed 0.151.0 shim): fds
  3+ never reach the real writer, so every inherited lock/claim descriptor
  protected only the launcher, and every recorded pid was the launcher's —
  node dying released the locks while native codex kept writing. Fix:
  `_codex_argv0` resolves the launcher to the vendored NATIVE binary
  (`<pkg>/node_modules/@openai/codex-<plat>/vendor/<triple>/bin/codex`,
  calibrated: Mach-O, --version identical) and launches it directly, so pid,
  descriptors, watchdog and kill target all refer to the real writer;
  CODEX_ORACLE_CODEX_BIN overrides get the same resolution; a WRITE run
  refuses when its codex is an unresolvable launcher script. For LEGACY
  shim-spawned records, liveness became group-aware: `_writer_alive` treats
  a dead pid with live group members and a fresh spool as a live writer, the
  deadline kill and codex_cancel_run verify GROUP death (not just pid death)
  before any terminal record, and `_pgid_alive` corroborates killpg(0) with
  ps stat — measured: killpg(0) on a zombie-only group raises EPERM on
  macOS, and a zombie member answers it, so raw killpg is a union claim.
  Real-codex E2E now runs the native binary end to end.
- HIGH — `_proc_comm` treated every nonzero ps exit as "process gone": gone
  now requires kill(0) ESRCH corroboration; a live pid with a failed ps
  RAISES so the barrier fails closed (contract-tested against the real
  implementation, not the hermetic test double).
- MEDIUM — a standing cancellation source (journal record OR marker) now
  reads CANCELLING for ANY nonterminal record, before pid classification — a
  stale recorded pid previously pushed a marker-only state to INTERRUPTED,
  which is not stoppable, wedging the promised retry (the reviewer's exact
  stale-pid probe is now a test).
- LOW — README: hooks require `python3` on PATH on every OS (a custom
  CODEX_ORACLE_PYTHON runs the server without reviving hooks), and the stale
  "launches via `python`" line now states the real
  `${CODEX_ORACLE_PYTHON:-python3}` contract.

### Round 11 (Codex, do-not-ship: 2 HIGH, 3 MEDIUM, 1 LOW — all addressed)
- HIGH — the positional barrier match mis-split SPACED install paths into a
  false negative (`cmd.split()` on a space-joined ps line has no argv
  boundaries). Classification now separates the two axes: a LOOSE substring
  match nominates candidates (a superset cannot miss), and the EXECUTABLE
  identity (`ps -o comm=` — a single field, immune to spaces) separates real
  servers (python) from `codex exec` children (codex/node). An unverifiable
  candidate fails closed. Regression tests: spaced path blocks, prompt-text
  child passes, comm failure refuses.
- HIGH — the detached-run deadline kill terminalized `timeout` without
  verifying death: a failed `_kill_pgid` (or unkillable process) now returns
  a loud collection failure with NO terminal record — the run stays
  stoppable — instead of letting a later resume write a thread a live
  process is still writing.
- MEDIUM — hooks/interpreter honesty: hooks cannot read `CODEX_ORACLE_PYTHON`
  (no env expansion in the hook spawn path), so the README now states the
  real contract — hooks fire wherever `python3` resolves; python-only Windows
  needs a python3 alias until the 1.18 native launcher — instead of
  contradicting the .mcp.json docs.
- MEDIUM — spool/argv construction moved inside the machinery containment: an
  OSError from the spool mkdir escaped `_run_codex` AFTER the terminal claim
  was acquired, leaving the run unkillable until restart. Now it terminalizes
  durably and releases the claim (tested with an injected disk-full).
- MEDIUM — `_run_status` consults the cancel MARKER, not just the journal
  record: when the journal itself is unwritable, a failed pre-spawn
  terminalization leaves only the marker, and the run must still read
  CANCELLING and stay stoppable for the promised retry.
- LOW — the self-pid shortcut in `_is_detached`/`_is_orphaned_write`/
  `_run_status` bypassed start-token identity: an old record whose owner pid
  was REUSED by this very server read RUNNING forever. All three now defer to
  `_server_alive`, which verifies the token even for our own pid.

### Round 10 (Codex, do-not-ship: 2 HIGH, 3 MEDIUM — all addressed)
- HIGH (hooks) — a `.py` command in exec form cannot be spawned on Windows.
  Hooks now run `command: "python3"` with the `${CLAUDE_PLUGIN_ROOT}` script
  as the single `args` element: still exec form (no shell, no word-splitting),
  a bare name resolves via the direct spawn's PATH lookup (a14d2bf measured
  that lookup working — only `${VAR:-default}` fails to expand there), and it
  is the same interpreter the .mcp.json registration falls back to, so hooks
  work wherever the server itself works, Windows included. Shebang + exec bit
  stay as belt.
- HIGH — publish-then-claim race in the owner: the start record was journaled
  BEFORE the run-terminal claim was acquired, so a canceller in the window
  could find the apparently free claim of a just-published run, terminalize
  "never spawned", clear the marker, and the owner would then claim and spawn
  anyway under a terminal run. The owner now acquires the claim FIRST and
  releases it if publication fails; pinned by a journal spy asserting the
  claim is unacquirable at the instant the start append happens.
- MEDIUM — the unowned-pre-spawn terminalization no longer reports success
  over a failed end append (intent retained, retry terminalizes).
- MEDIUM — the mixed-version barrier matches the server script POSITIONALLY
  (executable or the interpreter's direct script argument), not anywhere in
  the command line — a `codex exec` child whose PROMPT mentioned
  "server.py … codex-oracle" was classified as a legacy server and blocked
  writes; calibrated by that exact false-positive case.
- MEDIUM — shutdown is not a decision: a server going down during a capacity
  backoff (no child to detach) journals `interrupted`, not `cancelled` —
  `cancelled` is reserved for a caller's explicit stop, so the bare-resume
  guard no longer refuses the automatic recovery an ordinary restart
  deserves.

### Round 9 (Codex, do-not-ship: 2 HIGH, 2 MEDIUM — all addressed)
- HIGH (hooks) — the two measurements reconcile as: `args` PRESENT selects
  exec form (matches a14d2bf's 237→0 fires data for the env-default command),
  `args` ABSENT risks shell dispatch (the review's binary-schema reading, where
  an unquoted spaced path word-splits). `"args": []` is correct under either
  semantics — added to all three hooks; the registration test pins explicit
  exec form.
- HIGH — cancellation resurrection: the collector's refold-found-terminal
  `None` fell through to a thread resume, restarting a just-cancelled thread.
  A run whose terminal status is `cancelled` now refuses a bare resume; an
  explicit nudge is the deliberate override (tested: `_run_codex` is never
  invoked on the bare path).
- MEDIUM — the detach path journals its `detached` transition BEFORE releasing
  the terminal claims (a collector could otherwise terminalize in the window
  and the late record landed after it), and the journal fold treats a terminal
  status as IMMUTABLE to non-terminal records.
- MEDIUM — owner-claim containment: spawn `OSError`s (PermissionError,
  ENOEXEC, fd exhaustion) become durable terminal errors instead of escaping
  `_exec_codex_once`; any other machinery escape terminalizes via the retry
  loop's catch-all; and a pre-spawn cancel of an UNOWNED run (terminal claim
  free — the owner crashed) terminalizes instead of reporting CANCELLING
  forever, while an owned pre-spawn cancel stays an intent.

### Round 8 (Codex, do-not-ship: 3 HIGH, 3 MEDIUM, 1 LOW — all addressed; one HIGH partially disputed by measurement)
- HIGH (hooks) — the reviewer read the tree AFTER another session's commit
  a14d2bf landed mid-review (hooks now self-exec via ${CLAUDE_PLUGIN_ROOT} +
  shebang; that session MEASURED that on Claude Code >=2.1.246 the hook
  "command" is a DIRECT SPAWN where ${VAR:-default} never expands — so the
  1.17.1 interpreter form was dead, and, contra the review's shell-form
  premise, direct spawn also cannot word-split a spaced path). Resolution:
  the registration test now pins the a14d2bf contract (command =
  ${CLAUDE_PLUGIN_ROOT}/hooks/<script>.py, no args, exec bit, python3
  shebang); the hooks themselves stand as committed.
- HIGH — barrier evidence now fails closed end to end: `ps` exit status is
  evidence (nonzero raises instead of reading as an empty snapshot), a
  snapshot that does not contain THIS process is partial/invalid and refuses,
  and a registry exemption requires an exact pid+start match (unknown or
  mismatched identity blocks — pid reuse).
- HIGH — terminal-claim identity is STABLE: claims are namespaced
  (`run-<tag>` = the right to write the run's terminal record, `tid-<id>` =
  thread continuations), acquired in fixed run-then-thread order by owner,
  collector, resumer and canceller alike. The attached owner holds `run-` for
  the run's whole lifetime (backoffs included), a collector holds it for the
  collection and claims a replay-recovered thread id before terminalizing,
  and the canceller's ownership test IS the claim: contention → kill + leave
  the standing intent; acquisition → refold, append-if-no-end. This also
  closes round-8's MEDIUM: "server process alive" is no longer mistaken for
  "owner task alive" — an owner whose terminal append failed releases the
  claim and a retry cancel terminalizes.
- MEDIUM — spool replay recovers thread.started from a bounded HEAD scan (a
  tail-only window on a spool larger than the budget stranded the thread id).
- MEDIUM — completion-boundary linearization defined and said out loud: a
  cancel that lands after the run completed loses; the answer is collected,
  the run is journaled ok with an explicit note, and the intent is retired by
  the durable terminal record.
- LOW — spike __pycache__ removed (already gitignored).

### Round 7 (Codex, do-not-ship: 3 HIGH, 3 MEDIUM — all addressed)
- HIGH — alias enumeration is not mixed-version safety (and the round-6 interop
  test's new-first "alias" case resolved an already-canonical path, testing
  nothing): added a MIXED-VERSION WRITE BARRIER — 1.17.2+ servers register
  themselves (pid + start + protocol) at startup, and write acquisition refuses
  while any codex-oracle server PROCESS without a matching registry entry is
  alive (old code cannot be patched inside running processes, but its processes
  can be detected; after deploy no new pre-1.17.2 server can start, so the
  barrier is the guarantee). The legacy scan + planted bridge stay as defense
  in depth; the new-first symlink-alias residue is now PINNED by a test as
  acquirable at the lock level and precluded by the barrier upstream.
- HIGH — a detached run's collector and codex_cancel_run were two
  unsynchronized terminal writers: the canceller now takes the SAME run claim
  the collector holds; claim held → it kills + leaves the standing intent and
  the collector (sole terminal writer) folds it into `cancelled` (returning a
  message, never falling through to a thread resume of a just-cancelled run);
  claim acquired → refold, append the end only when none exists, clear the
  marker only on a durable append. DETACHED-ENDED became stoppable so a failed
  terminal append is retryable.
- HIGH — bridge publication was fail-open with a post-spawn crash window:
  planting failures now REFUSE dispatch (rollback + inode release), child
  repointing is all-or-nothing with fsync, and write children start behind an
  EXECUTION BARRIER (`sh -c 'read _ok || exit 97; exec …'`): codex execs only
  after the spawn is journaled and every bridge file durably names the child;
  if the server dies first the pipe closes and the child exits 97 — fail
  closed, never an unpublished writer.
- MEDIUM — session-id journal retries are bounded and IMMEDIATE (a quiet child
  no longer outruns fail-closed); MEDIUM — adoption checks the replay-recovered
  thread-id append and returns a loud retryable failure instead of
  terminalizing past it; MEDIUM — dead-owner cancellation reports a failed
  terminal append (marker retained) instead of claiming success.

### Round 6 (Codex, do-not-ship: 3 HIGH, 4 MEDIUM — all addressed)
- HIGH — cancel in the retry-backoff window: the canceller journaled a terminal
  `cancelled` and cleared the intent because the previous attempt's pid was dead,
  and the sleeping owner then woke past the cleared marker and spawned the next
  generation under an already-terminal run. Now: while the run's owner server is
  alive (and the run is not detached), the canceller NEVER terminalizes — it
  kills the live generation, leaves the intent standing, and the OWNER retires
  the run (capacity waits poll the intent every second and write a liveness line
  every 30s); markers are cleared only after a DURABLE terminal record. A fresh
  run also claims its THREAD the moment the id exists and holds the claim across
  every retry and backoff, so a concurrent resume of an idle backoff window is
  excluded (previously unclaimed and double-executable after 60s of log idle).
  Detached runs (owner relinquished) stay canceller-terminalized.
- HIGH — legacy (1.17.1) write-lock interop gave no mutual exclusion: the probe
  checked only the exact raw-path key (a symlink/case/subdir alias was missed),
  and an old server could take its content lock after a new server already held
  the — to it invisible — inode lock. Now: the legacy scan matches by the TREE
  each file's recorded cwd resolves to (fail-closed on unparseable cwd), and
  after taking the inode lock the new writer PLANTS legacy-format bridge files
  for the alias set {cwd, realpath, git toplevel, its realpath}, re-pointed at
  the codex child on spawn so a server crash leaves them naming a live pid.
  Both orders + alias order proven against the VERBATIM 1.17.1 protocol
  (vendored in tests/legacy_lock_1171.py). Residue: an old writer on an alias
  outside that set — a hole two 1.17.1 writers already had between themselves —
  closes when every server is on 1.17.2.
- HIGH — Windows read-mode continuations had no durable claim (`pass_fds` is
  POSIX-only; region locks are not inherited and a parent's exit does not end
  its children): ALL continuations (resume/collect/adopt) now refuse on Windows
  in 1.17.x, not just write resumes; the 1.18 daemon is the fix.
- MEDIUM — fail-closed journaling now covers every publication phase: a failed
  `start` refuses dispatch before any spawn; `journaled_tid` records only what
  is on disk (a failed `session` append retries per event and kills the run
  after three); terminal records gate marker clearing everywhere (owner end
  path, caller cancel, canceller, adoption notes an unjournaled end).
- MEDIUM — a continuation's `start` record carries its thread id, so a crash
  before thread.started still keys claims/adoption by the THREAD; a thread id
  recovered only from spool replay is journaled durably during adoption.
- MEDIUM — spool replay discards a partial first line with fixed-size reads
  (`readline()` on a newline-free region allocated it whole before the 32 MiB
  record cap could apply).
- MEDIUM (plan) — the 1.18 daemon keeps the per-run detached watchdog as an
  INDEPENDENT deadline backstop: a SIGKILLed daemon must leave children
  deadline-enforced until the supervisor restarts it.

### Fixed after the Codex review of 1.17.0 (verdict: do not ship — the HIGH/MEDIUM findings below are closed)
- **HIGH — adoption could sign partial output `status:ok` with a post-hoc tree digest.**
  `_collect_detached` promoted an earlier `agent_message` to the answer, and `_answer_sig`
  hashed the workspace at *collection* time, so a review of an older tree could satisfy the
  push gate. Now OK is EARNED: the answer FILE (codex writes it only at turn end) + a
  `turn.completed` event + no terminal error; anything else renders as `[Codex error …
  status:error]` with `[partial output … NOT the answer]`. The tree digest is taken at
  DISPATCH, journaled with the run (`tree`), and stamped into every answer header (normal and
  adopted) — an edit after dispatch makes the push gate refuse, as it should.
- **HIGH — a write child that outlived a crashed server was adoptable and its lock "stale".**
  `_is_detached` is false for write runs; `codex_runs` shows them as `ORPHANED-WRITE` (stop
  before any new write run); the tree's write lock now records the codex child (`child=`) and
  is stale only when the server AND the child are gone.
- **HIGH — no atomic claim: two collectors of a dead detached run both resumed the thread.**
  Per-run O_EXCL claim (`runs/claims/<run>.lock`, stale on holder death) around adoption and
  around the thread-resume dispatch; the second caller is told to wait.
- **HIGH — `codex_cancel_run` journaled `cancelled` before verifying death; Windows
  `taskkill` return code ignored; an attached runner then overwrote it with `error`.** The
  kill is verified (≤3 s); a failed stop reports FAILED and journals nothing; a verified
  cancellation is kept as the terminal status by the attached runner.
- **MEDIUM — collector cancel:** cancelling `codex_resume_run` while it waits for a detached
  run no longer silently returns: it journals `collect_cancelled` and says the run keeps
  running (it was not the collector's to kill — deliberate disagreement with the review's
  "kill it" recommendation, see below).
- **MEDIUM — no deadline enforcement → no detach.** A run whose watchdog could not be spawned
  (Windows, or POSIX spawn failure) is killed on shutdown, never detached; the live log says so
  at spawn.
- **MEDIUM — run ids recur after PID reuse.** Tags carry a 4-hex token (`codex1·23004·9f3a`)
  and spool dirs are created exclusively (`-1`, `-2` suffixes on collision).
- **MEDIUM — unbounded memory.** In-memory stdout/stderr capture is a bounded tail
  (`CAPTURE_MAX_BYTES` 512 KiB; the spool on disk is the full record); status/log/adoption
  replay only the last `REPLAY_MAX_BYTES` (1 MiB) of a spool; drains are chunked.
- Tests (`tests/test_detach.py`, 8 new cases): partial output never signed ok; orphaned write
  run never adoptable; two collectors → one claim; failed stop journals nothing; run-tag token +
  exclusive spool; dispatch-time tree in the signature while the tree changes mid-run; write
  lock follows the child; collector cancel leaves the run running.

### Fixed after review round 2 of 1.17.2 (verdict: do not ship — closed below)
- **HIGH — legacy records (pre-1.17.2) had no dispatch digest, so adoption stamped the
  collection-time tree.** Now `tree:unknown` — syntactically accepted by the push gate, never
  equal to a real digest.
- **HIGH — live holders could be age-expired; release deleted whatever was there.** Superseded in
  round 4 by kernel-held locks (below).
- **HIGH — `ORPHANED-WRITE` runs could not be stopped.** `codex_cancel_run` accepts
  RUNNING / DETACHED / ORPHANED / ORPHANED-WRITE.
- **MEDIUM — detachment inferred without a watchdog.** `_is_detached` requires a recorded
  watchdog; a survivor without one is `ORPHANED` (unbounded — stop it), never adopted.
- **MEDIUM — split trailing JSONL record lost across replay/adoption.** `_replay_spool` hands
  its partial buffer to the adopter.
- **MEDIUM — identity failed open.** A server's own pid with a foreign recorded start is not
  "alive"; `_kill_pgid` refuses when a recorded identity cannot be verified.
- **Out-of-diff, fixed anyway — Windows `os.kill(pid, 0)` TERMINATES the process** (Python
  routes non-control signals to `TerminateProcess`): `_kill0` now queries the process handle on
  Windows (`OpenProcess` + `GetExitCodeProcess`), unmeasured there but non-destructive.
- **LOW — spool suffix cap** returned an uncreated path; a private temp dir is used instead.
- Tests: legacy `tree:unknown`; orphan without watchdog → ORPHANED + stoppable; orphaned write
  stoppable; turn cut mid-way (no error event) → not ok; aged live lock/claim not stolen +
  owned release + legacy lock expiry; split-record carry; identity fails closed; spool cap.

### Fixed after review round 3 of 1.17.2 (verdict: do not ship — closed below)
- **HIGH — a live owner's pre-spawn write lock could be age-expired** and the original then
  stamped its child into the thief's lock. Superseded in round 4 by kernel-held locks (below).
- **HIGH — stale recovery was read-then-unlink (both contenders acquired).** A rename-based
  compare-and-delete was tried and ALSO raced (a contender renamed the winner's fresh lock away;
  measured by the 6-thread test). Final mechanism: **kernel-held locks** — `flock` (POSIX) /
  `msvcrt.locking` (Windows) on an open descriptor, exclusive for the holder's lifetime and
  released by the OS on death; the write run's codex child INHERITS the descriptor (`pass_fds`) so
  the tree stays locked exactly as long as the writer, even if the server dies; run claims are the
  same primitive. No pid/age/nonce/rename heuristics remain; lock files are never unlinked (an
  unlink races a fresh opener onto a different inode). A legacy 1.17.1 lock file, held by nobody,
  is acquirable immediately. Windows region locks do not inherit (documented gap).
- **HIGH — a not-yet-spawned run was journaled "cancelled" while its owner went on to spawn.**
  `codex_cancel_run` on a run without a pid records a cancel INTENT (marker + journal
  `cancel_requested`, status `CANCELLING`); the owner honours it before its next attempt and
  right after spawn (kills the just-spawned child) and journals the cancellation itself.
- **HIGH — kills skipped identity when the start token was empty; `ps` denied read as "dead".**
  `_kill_pgid` requires a recorded AND verified identity (a legacy record is refused with an
  OS-stop hint); unknown identity (ps denied/absent) counts as alive for exclusion. Windows
  identity = process creation time via `GetProcessTimes`, liveness via a zero-timeout
  `WaitForSingleObject`, with exact ctypes prototypes (still unmeasured on Windows).
- **MEDIUM — a bare nonzero watchdog pid authorised detachment.** The spawn record stores the
  watchdog's identity; while the child lives, detachment requires a live, matching watchdog,
  else the survivor is `ORPHANED`. A watchdog whose identity cannot be recorded is terminated
  and the run runs un-detachable.
- **MEDIUM — the JSONL line buffer was unbounded until a newline.** `JSONL_RECORD_MAX_BYTES`
  (32 MiB; codex truncates tool output per event at ~10k tokens, so this is a runaway ceiling)
  drops an oversized record loudly and re-syncs at the next newline.
- The write-mode suite's "over-age lock broken despite live pid" pin was inverted — that rule was
  the unsafe one. Tests added: stale recovery race (6 threads, one winner), live-owner lock +
  owned child stamp, pre-spawn cancel intent honoured by the owner, unknown identity semantics,
  watchdog identity gate, oversized record drop.

### Fixed after review round 4 of 1.17.2 (verdict: do not ship — closed below)
- **HIGH — the watchdog was SIGTERMed by pid without identity** → verified `watchdog_start` required.
- **HIGH — cancel/publication race:** the owner checked for a cancel intent BEFORE publishing its pid,
  so a canceller could acknowledge "requested" against a child that then ran to completion. The pid is
  now published (spawn record) before the post-spawn check, and the canceller re-reads the journal
  after writing its intent and takes the kill path if a pid appeared; a marker that cannot be written
  is reported, never swallowed.
- **HIGH — a continuation child did not inherit the run claim**, so a detached resume could be resumed
  again by another server. The claim descriptor is passed to the continuation child (`pass_fds`) for
  its lifetime.
- **HIGH — Windows cannot transfer lock ownership to the child** (`_locking` ownership does not follow
  an inherited handle): write mode (abraham, write resume) now FAILS CLOSED on Windows in 1.17.x —
  it returns with the 1.18 daemon.
- **HIGH — the log pruner could unlink `runs/claims` and `runs/cancel`** (unlinking a locked file
  leaves the lock on the old inode → split-brain). Management dirs are never pruned.
- **MEDIUM — `_server_alive` read unknown evidence as dead** → unknown is alive; only a verified
  absence or identity mismatch is dead.
- **LOW — the record cap only applied to newline-free buffers** → applied to the record itself.
- Out-of-diff note fixed: write locks are keyed by the RESOLVED path (aliases → one lock). Still open
  out of diff: the tree digest does not bind untracked-file contents; the check-to-signal window of
  pid identity (closed for good by a parent supervisor in 1.18).

### Fixed after review round 5 of 1.17.2 (verdict: do not ship — closed below)
- **HIGH — cancellation could kill a stale generation.** During an automatic retry the snapshot's
  pid may be the previous (dead) attempt while a newer one runs. `codex_cancel_run` is now
  generation-aware: it re-folds the journal after recording its intent, kills the CURRENT live
  generation (verified identity), re-folds again, and journals a terminal cancellation only when no
  live generation remains — otherwise it reports REQUESTED for the owner to honour. The marker is
  cleared after the terminal record.
- **HIGH — claims were keyed by run id, so nested continuations (A → B → C) of one codex thread did
  not collide.** Claims are keyed by the THREAD id; the continuation child inherits that descriptor.
- **HIGH — `realpath` is not a filesystem identity** (case-insensitive APFS: `samefile()` true,
  different hashes) and a subdirectory got its own lock. Write locks are keyed by the git worktree
  root's `(st_dev, st_ino)`; a live pre-1.17.2 content lock (raw-path key) is respected for one
  release.
- **MEDIUM — spawn publication was not fail-closed.** `_journal` reports durability; a spawn that
  cannot be recorded is killed and refused ("refusing to run unrecorded").
- **LOW** — the Windows refusals and the watchdog-identity signalling are now directly tested
  (`_IS_WINDOWS` flag; a bystander process recorded as the watchdog with a foreign identity is left
  alone); a marker that cannot be written is reported.
- The 1.18 daemon plan the changelog referred to now exists: `PLAN_1.18_ORACLE_DAEMON.md` (staged
  per the architecture review), with the measured app-server spike in
  `plugins/codex-oracle/spike/app_server_spike.py`.

### Disputed with measurement — hooks
- The review held that `${CODEX_ORACLE_PYTHON:-python3}` is passed literally in direct-exec
  hook commands (Claude Code hooks doc) and that all three hooks still fail. MEASURED on
  Claude Code 2.1.251 after `/reload-plugins`: the session transcript carries an `attachment`
  record with the nudge hook's `LIVE VIEW:` context on each subsequent codex tool call, and no
  `Executable not found` record after the reload (one before it). The expansion works on this
  build; the form is kept. What remains unproven is Windows (`CODEX_ORACLE_PYTHON=python`
  documented) — the same caveat as `.mcp.json`.
- **HIGH — PID reuse.** Liveness and kill decisions now carry process IDENTITY = pid + start
  time (`ps -o lstart=`): spawn records store `pid_start`/`server_start`, claims and write
  locks store `start=`/`pstart=`/`cstart=`, `_pid_alive`/`_server_alive`/`_kill_pgid` refuse a
  pid whose start time changed, and a server's own pid is alive by definition (found by the
  two-collector test: macOS `ps` reports the venv python's framework binary, not `server.py`, so
  the command-line check alone judged a live claim holder dead and the second collector broke
  the claim). Windows has no start token and falls back to kill(0) — unproven there.
- The review's "no recorded exit status" point is real and only fully closed by
  a supervisor that is the codex child's PARENT (records the exit code, kills by handle, not by
  reusable pid). That is the next design step (1.18), tracked in the README; 1.17.2 narrows the
  window (unique ids, exclusive spools, verified kills) without claiming to close it.

## [1.17.1] — 2026-08-31

### Fixed — the plugin's hooks were dead on macOS too
- `hooks/hooks.json` spawned `python` directly (v1.9.0 Windows form) for all three PreToolUse
  hooks — the live-view nudge on every codex call, the plan gate, and the **git push gate** —
  so on stock macOS each fired `Executable not found in $PATH: "python"` (non-blocking) and
  did nothing: no live-view shell was ever suggested here, and pushes were never gated. Same
  root cause as the 1.17.0 `.mcp.json` fix; same form now: `${CODEX_ORACLE_PYTHON:-python3}`
  (Windows: `CODEX_ORACLE_PYTHON=python`). Pinned by `tests/test_detach.py`.

## [1.17.0] — 2026-08-31

### Added — runs survive MCP server restarts; run-operations tools
- **Root cause, measured.** A backgrounded oracle call is a child of the MCP server. Claude
  Code's `/mcp` reconnect (also plugin reload / session exit) sends SIGINT then SIGTERM
  ~100 ms apart (its mcp-logs), the caller sees "Connection closed", and the server's
  cancel-cleanup SIGKILLed the codex tree — a 25-minute max-effort review lost and
  re-dispatched from scratch (twice on 2026-08-31, in two sessions). Not killing was not
  enough: an orphaned `codex exec --json` whose stdout pipe reader is gone panics with
  "failed printing to stdout: Broken pipe" (measured).
- **File-backed spool.** codex's stdout/stderr and its `--output-last-message` answer live in
  `~/.claude/logs/codex-oracle/runs/<run>/attemptN.*`; the server TAILS them (fixed-size
  reads, `CODEX_ORACLE_TAIL_POLL`), so the child holds no pipe to the server and outlives it.
  Kept as evidence; pruned with the 7-day log retention. Spawn records (pid / pgid / spool /
  deadline) are journaled per attempt.
- **Detach on shutdown, kill on cancel.** `_install_shutdown_handlers` raises a flag on the
  first SIGINT/SIGTERM/SIGHUP (then ignores the follow-up SIGTERM so the millisecond cleanup
  is never torn) and takes the normal KeyboardInterrupt path; the cancel-cleanup sees the flag
  and DETACHES (journal `detached`; the live log says how to collect) instead of killing. A
  caller cancel with no signal still kills — "stop spending". Write (abraham) runs are never
  detached: the one-writer lock's liveness is the server pid.
  The process still exits promptly with the follow-up SIGTERM ignored: `__main__` exits as
  soon as `mcp.run` unwinds and a 3 s daemon backstop `os._exit`s if the stdio reader thread
  is stuck on a pipe the client kept open (measured: the server lingered >10 s otherwise).
  Detachment never depends on that cleanup finishing: the spawn record carries the owning
  server's pid, and "no end record + codex alive + owner dead" IS detached (`_is_detached`).
- **Deadline with no server.** A detached `/bin/sh` watchdog per run SIGKILLs the process
  group at the MAX_RUNTIME deadline (5 s ticks; exits when codex ends; reaped on a normal
  finish). Windows keeps the in-server timeout only.
- **Adoption.** `codex_resume_run` on a detached run waits for the process (heartbeating,
  replaying its spool into an `adopt` live log) and returns its answer with the normal header —
  no re-ask, no model call; a finished one returns immediately; one that died without an
  answer falls back to the existing thread resume. A nudge while it still runs is refused (the
  thread is being written). The "still running" guard now also checks pid liveness.
- **Ops tools:** `codex_runs()` (RUNNING / DETACHED / ok / error / cancelled / timeout /
  INTERRUPTED, elapsed, attempts, thread, activity, log path), `codex_run_log(run, lines)`
  (the live log in-conversation — the MCP task panel is structurally silent once Claude Code
  backgrounds a call), `codex_cancel_run(run)` (SIGKILL the group + watchdog, journal
  cancelled; the thread stays resumable). `codex_resume_run(run="list")` delegates to
  `codex_runs`.
- `CODEX_ORACLE_CODEX_BIN` pins the codex executable (the ChatGPT.app-bundled build, or a
  fake for tests). A run killed by a signal reports "codex process killed by signal N …"
  instead of echoing raw JSONL as an answer.

### Fixed — the plugin's own MCP registration was dead on macOS
- `.mcp.json` used `"command": "python"` (v1.9.0 Windows work); stock macOS has only
  `python3`, so the plugin-provided server failed ENOENT in every session since 2026-08-24 and
  the tools only worked through a hand-added direct `~/.claude.json` entry (two registrations
  = two tool sets whenever both connect). Now `"${CODEX_ORACLE_PYTHON:-python3}"` — Claude
  Code expands `${VAR:-default}` in `command`; Windows sets `CODEX_ORACLE_PYTHON=python`.

### Tests
- `tests/fake_codex.py` — a stand-in speaking codex's `exec --json` contract (real
  processes, no API spend). `tests/test_detach.py` (47 checks): spool + spawn journal,
  caller-cancel kills, shutdown detaches + adoption of a running / finished / dead run,
  watchdog deadline with no server, ops tools, signal handlers, registration.
- `plugins/codex-oracle/selftest_detach.py [--real]` — E2E over real stdio: the server is
  killed exactly like Claude Code does (SIGINT, +100 ms SIGTERM) mid-call; the run survives
  and a second server collects it. `--real` proves it on the real codex CLI mid-turn.

## [1.16.2] — 2026-08-31

### Fixed — provider capacity sheds ("at capacity") were terminal on attempt 1
- **The transient classifier matched codex's error VARIANT NAME, never its rendered
  MESSAGE.** codex maps HTTP 503 `{"error":{"code":"server_is_overloaded"|"slow_down"}}`
  (and the `response.failed` SSE equivalent) to `CodexErr::ServerOverloaded`, rendered as
  *"Selected model is at capacity. Please try a different model."* — and `is_retryable()`
  returns **false** for it, so the CLI fails the turn on the spot with no internal retry
  (`codex-rs/protocol/src/error.rs`, identical at `rust-v0.147.0` and `rust-v0.151.0`).
  The wrapper is therefore the only retry layer, and its signal list had `"overloaded"`
  (the variant) but not `"at capacity"` (the text): on 2026-08-31 four max-effort reviews
  died 89s–1163s in with `attempts=1` and the resume machinery never engaged.
- **Two transient classes, two responses.** `_transient_class()` → `overload`
  (capacity / 429 / 503: WAIT with backoff, then resume the same thread) or `disconnect`
  (dropped stream / reset / other 5xx: resume now). Overload budget `OVERLOAD_MAX_RETRIES=4`
  with `30s·2^i` capped at 300s (30/60/120/240 ≈ 7.5 min, ±20% jitter so runs shed
  together don't return together); env `CODEX_ORACLE_OVERLOAD_RETRIES` /
  `CODEX_ORACLE_OVERLOAD_BACKOFF` (0 = immediate). Disconnects keep `MAX_TRANSIENT_RETRIES=2`
  and no wait. **The model/effort pin is never touched** — a shed is ridden out on the
  pinned model, not routed around; one that outlives the budget ends in the existing
  explicit `codex_resume_run` hand-off, now with a capacity note (attempts, seconds
  waited, pin unchanged). Write runs still never auto-retry.
- **Waiting is not going dark.** `_wait_for_capacity` brackets the sleep with the same
  request-scoped `_heartbeat_loop` (geometry unchanged) and sets the spinner activity; a
  caller cancel during the wait is journaled exactly like a mid-attempt cancel
  (`_cancelled`), so the run stays resumable.
- Journal `end` records carry `retry_classes` + `capacity_wait_s`; success notes say what
  was recovered from and how long it waited.
- Tests: `tests/test_transient_retry.py` — pins the rendered ServerOverloaded text against
  the installed codex source (`~/Documents/codex-installed`, tag-aligned) when present, the
  schedule and budgets, same-thread resume with the model/effort pin held, budget exhaustion
  → hand-off + journal fields, amnesia guard under a shed, disconnect budget, non-transient
  no-retry, heartbeats during the wait, cancel during the wait.

### Fixed after the Codex review of 1.16.2 (verdict: needs changes — all addressed)
- **HIGH — a failed run's earlier commentary was returned as its answer.** `--json` runs
  that fail after emitting assistant text promoted that text to `final_message`, so the
  structured error (capacity counts, pin note, run id, resume hand-off) was skipped — true of
  all four motivating logs. Every non-zero exit now goes through ONE renderer: the failure is
  the message, prior output is appended as `[partial output before the failure — NOT the
  answer]` (bounded), raw JSONL is never an answer, and `last_message` is reset per attempt so
  attempt 1's commentary cannot leak into attempt 2's result.
- **MEDIUM — classification read model output.** The transient class was derived from
  stderr + the terminal error + the last 2,000 chars of stdout, so a quota/auth failure
  preceded by text mentioning "at capacity" was retried as an overload. Now only the terminal
  error event is classified (stderr as the sole fallback), never stdout.
- **MEDIUM — catalog coverage.** Added the exact 0.151.0 renderings codex can surface after
  its own retries are spent: `Connection failed: …`, `Error while reading the server
  response: …`, `exceeded retry limit, last status: …` (503 → overload, otherwise
  disconnect), `We're currently experiencing high demand…` (overload), `internal error; agent
  loop died unexpectedly`, `request timed out`, `timeout waiting for child process`.
- **LOW — budgets.** Per-class counters (overload 4, disconnect 2) plus an explicit total
  ceiling `MAX_TOTAL_RETRIES`; `CODEX_ORACLE_OVERLOAD_RETRIES` is bounded (≤12); the ±20%
  jitter is clamped under the 300 s cap; cancellation journal records carry `attempts`,
  `retry_classes`, `capacity_wait_s`.
- **Tests** now drive the REAL `_exec_codex_once` with `tests/fake_codex.py` emitting codex's
  JSONL failure shapes (capacity / quota / disconnect, with prior commentary), cover the pinned
  error catalog, and the source pin verifies the worktree tag matches the installed binary —
  a mismatch is a printed SKIP, never a silent fallback.
- **Hypothesis verdict UNPROVEN on the schedule itself, recorded as such:** the 30/60/120/240 s
  ladder is a documented default for short blips; today's shed lasted ~3 h (12:16→~15:07 IST;
  a probe at 15:45 answered in 6.7 s), which no in-request budget covers — that case is the
  explicit `codex_resume_run` hand-off. There is no way to force a provider shed for a
  calibrated probe; the classifier is calibrated on the four real rollouts instead.

### Verified — codex CLI 0.147.0 → 0.151.0 alignment (an install, not code)
- The desktop app (ChatGPT.app bundle, `codex-cli 0.151.0-alpha.7.2`) and the npm CLI
  share `~/.codex/models_cache.json`, keyed by whole client version; 0.147.0 also required
  a field the 0.151 schema dropped (`supports_parallel_tool_calls`), so every CLI run
  logged `failed to load models cache` and re-fetched the catalog. Upgraded the CLI to
  `@openai/codex@0.151.0` (npm `latest`; same as the Homebrew cask): calibrated known-red
  1 error line per run → 0, `models cache: cache hit`. `--strict-config` accepts every
  `-c` key this server sends at `max` (a planted bogus key exits 1); `exec --json` event
  shape unchanged; `exec resume [SESSION_ID] [PROMPT]` grammar unchanged.
  `scripts/codex_src.py` re-aligned the source worktree to `rust-v0.151.0`.
- Gotcha (both versions, not a regression): `codex exec "<prompt>"` with a non-TTY **open**
  stdin blocks on "Reading additional input from stdin..." until EOF — script it with
  `</dev/null`. This server always spawns codex with `stdin=DEVNULL`.

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
