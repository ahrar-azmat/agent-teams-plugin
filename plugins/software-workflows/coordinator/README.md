# Writer coordinator — software-workflows 2.4.12 / P1

C1 runs native Codex app-server writers in their original checkouts. A native
Codex project is one unit spanning all of its repositories; several projects can
share the same checkout ledger, claims and custody. C2
connects Abraham, the run tools and Claude lead operations to that durable
coordinator. The package does not import `server.py`; the MCP server uses the
same Python client and JSONL RPC as the CLI. Actual verification results are in
[VERIFY.md](VERIFY.md).

The central rule is that **claims and custody coordinate access; only a verified
operation establishes authorship**. Shell commands, MCP calls, external editors,
missing hook evidence and crash gaps cannot acquire authorship from matching
hashes. Their unexplained changes are UNKNOWN and block publication. One
exception rests on timing: a lead Bash command whose armed window stays globally
quiet is recorded as `lead-command` throughout its reconciliation reach (see the
Bash contract below and its known limits).

## Start and discover

Set `PYTHONPATH` to the parent of this directory. The daemon requires Python
3.11+ and a SQLite release containing the WAL-reset fix. The CLI/client and hook
entry point also run under Python 3.9; bootstrap selects a suitable daemon
interpreter. `SOFTWARE_WORKFLOWS_PYTHON` can select that interpreter explicitly.

```sh
export PYTHONPATH=/absolute/path/to/plugins/software-workflows
export PYTHONDONTWRITEBYTECODE=1
python3 -B -m coordinator --help
python3 -B -m coordinator daemon start
python3 -B -m coordinator daemon status
```

State defaults to `~/.local/state/software-workflows`. `--state DIR` or
`SOFTWARE_WORKFLOWS_STATE` selects a development state directory. State must be
owned, private, outside every checkout and outside the plugin installation.
Directories are `0700`; sockets are `0600`. Do not put state in the repository.

Clients read the private, atomically replaced `endpoint.json` manifest. The RPC
socket lives in a unique short directory beneath canonical `/tmp/c1-<uid>/`,
independently of the state-root length. Each allocation records its token and
directory device/inode in `owner.json`. Native and admission listeners use the
same allocator. Codex may redirect its requested path to a different physical
socket; C1 validates the resolved path and records both byte lengths. It measures
the pathname limit with actual disposable binds, rather than a platform constant.
Do not construct `<state>/rpc.sock` yourself. Startup failures are preserved in
`startup-error.json` and returned by bootstrap with their specific error code.

The detached supervisor owns a `flock`, restarts a failed worker, and exits after
an orderly stop. A restart is a daemon start like any other, made under
`upgrade.lock`. While another process holds that lock (it is starting, replacing or
stopping the daemon), a supervisor that holds no lease exits instead (`UPGRADE_HELD`
in `supervisor-exit.json`). One that holds leases keeps them and restarts its worker
once the lock is free (`supervisor-wait.json`): the app-servers of the worker that
died may still be writing, and they hold no lease themselves. The lock's holder
lets go when its own wait ends: a client waiting for the daemon to answer is refused
`DAEMON_UNRESPONSIVE` (retryable) after its timeout, 30 s. Only a signal ends a
supervisor that holds leases. A restarted worker holds the lock until it serves, at
most 30 s. The worker has a separate lock. Native
listeners do not inherit either descriptor. Repeated bootstrap discovers the
existing daemon. PID files and heartbeats are never authority to steal a lock or
custody.

```sh
python3 -B -m coordinator rpc checkout/register '{"path":"/absolute/checkout"}'
python3 -B -m coordinator rpc run/start '{"checkout":"checkout_…","mode":"native","task":"Add the requested function","paths":["src/feature.py"],"model":"gpt-6-astra"}'
python3 -B -m coordinator rpc run/status '{"checkout":"checkout_…"}'
```

`run/start.mode` is `native` by default. Use **`"mode":"lead"`** for a lead
unit without starting a native listener. `task` may be empty to admit an idle
writer before sending its first message. Each writer owns one private listener,
one coordinator WebSocket connection and a namespace for its native IDs.

Native writers retain the user's normal CODEX_HOME, provider and unrelated hook
trust. Their MCP servers follow the explicit writer profile described below;
`all` preserves the normal MCP configuration. The supervisor, the worker and every app-server they
start run without the launching Claude Code session's identity and messaging
variables (`CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_ENV_FILE`, `CLAUDE_EFFORT`,
`CLAUDE_PROJECT_DIR`, `CLAUDE_JOB_DIR`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`,
`CLAUDE_PLUGIN_OPTION_*`, `CLAUDE_CODE_*`), without the push gate's `CODEX_GATE_*`
wiring, without any plugin version's git shim or exec-path mirror on `PATH`, and
without a mirror `GIT_EXEC_PATH`. C1 passes session flags for approvals `never`,
sandbox `danger-full-access`, live web search, V2 agents, coordinator hooks, and
disabled TTY. It validates the effective modes and the start/resume policy echoes.
Incompatible managed settings cause refusal. This is cooperative coordination
with full access, not operating-system confinement against arbitrary code.

Native app-servers and asynchronous helpers are children of the supervisor,
which checks their exit with `waitpid(WNOHANG)`. A stopped child remains owned.
Before serving, the worker asks that supervisor to stop an owned probe, measures
continued event-loop service, then requires its exit evidence and `FAMILY_GONE`.
A failed probe refuses readiness with `CHILD_WATCHER_UNSAFE`;
`daemon/status.child_watcher` reports the actual observation. The existing
kqueue exit watcher remains available to standalone capability fixtures.

### Process ownership and requested stops

The supervisor starts native app-servers, discovery/readers, calibration and
asynchronous helpers behind a closed gate. It fsyncs `OWNED` in `owner.journal`
before releasing that gate, then snapshots and grants the family to the worker.
The journal contains identities and state, never command arguments or environment
values. Each worker has its own session. macOS identity contains the kernel unique
ID and exec version read together; an identity-conditioned signal returning ESRCH
requires another identity read and does not establish exit. Cleanup snapshots
lineage before signalling, gives the root five seconds, snapshots again, gives
survivors TERM and two seconds, then retries KILL against surviving identities.
Custody and execution claims stay owned until durable `FAMILY_GONE`.

The preallocated journal defaults to 8 MiB, configurable through
`SOFTWARE_WORKFLOWS_OWNER_JOURNAL_BYTES` (read when the journal is created). Its
working model is 30 writers, two incarnations each and 16 family members with
repeated snapshots. Normal records use at most its first 75%; the last quarter is
cleanup reserve. Compaction starts above 50% and keeps what can still be asked
for: the custodian, the inhibit flag, the worker, every family not yet `GONE`, and
the `GONE` families whose receipt a consumer may still request. A writer's
app-server family stays until the worker holds its `FAMILY_GONE` receipt in the
ledger (the listener record's `family_gone`); the worker then sends `forget`, the
supervisor records `FORGOTTEN`, and the family leaves the journal. The worker
forgets when a writer stops, parks, dies, goes dormant or fails to start, and at
every start again for each listener record that holds a receipt, so a forget lost
to a crash is sent again. A writer launch abandoned before its listener record
exists is forgotten too: by the launch's own compensation when it is cancelled, or
by the next start's sweep when the worker it was granted to has died. From then on
a receipt is answered from the ledger, never from the supervisor, and a process
handle asking late about a family that has left answers from what it already
holds. Any other `GONE` family (admission, project and role
app-servers, helpers, members, watcher probes, workers) is asked about only by the
handle that launched it, within seconds of its exit: compaction keeps it for
`SOFTWARE_WORKFLOWS_OWNER_JOURNAL_RETENTION_S` seconds after its `FAMILY_GONE`
(default three days; read at every start). Through 2.4.11 compaction kept every
family ever recorded, and an 8 MiB journal filled in 9 days. Status alarms at
60% and reports `retention_s`, `gone_retained` and `forgotten`. An exhausted or
damaged journal refuses new ownership with retryable `RESOURCE_EXHAUSTED`; it
never opens an unregistered payload's gate. The lifecycle measurements and any
remaining validation limitations are recorded in the build report.

After a supervisor failure, the surviving worker transfers its held lease
descriptors with SCM_RIGHTS. Acknowledgement of those descriptors precedes
compatibility-bridge refresh; no release/reacquire window authorizes another
writer. Recorded orphan families are ended on the next supervisor start.
Unrecorded processes are not adopted or signalled by executable name.
The owner blocks in `select(None)` when no end deadline is pending.

SIGINT and SIGTERM latch an idempotent requested stop before the worker Runner
starts and through executor, generator and loop cleanup. Requested stop exits
with its explicit outcome; it does not request a replacement worker.
The journal's inhibit also survives a supervisor restart. LaunchAgent support is
opt-in through `daemon install-agent` and `daemon uninstall-agent`, with explicit
consent; normal setup does not install it. The agent uses socket activation and
the inhibit. Without one, crash cleanup runs at the next client start. The MCP
pre-exec root-registration gate (O8a) remains out of scope; MCP fan-out is managed
by the explicit writer profile and verified parking.

### Shared project services and launch pressure

A project can declare `services` in `.software-workflows/services.json` at its
checkout roots, or through `services/declare`. A declaration maps names to
`kind` (`minio`, `postgres`, `redis`), a locally present `image`, optional `config`
and environment-variable mappings. Conflicting checkout declarations refuse.
Supported configuration is `migration_version`, SQL `migrations` for PostgreSQL,
and `startup_seconds`. Images are inspected locally and never pulled.

Pool identity includes the project/checkout set, the image IDs and configuration,
Docker engine identity and context, plus generation. A per-key lock serializes
acquisition. Containers are created stopped with installation, pool, service and
nonce labels, loopback ephemeral ports and restart=no. Their full IDs are durable
before start. Readiness requires authenticated S3/admin, PostgreSQL SELECT or
Redis ACL PING operations. Per-writer MinIO users have their own bucket policies;
PostgreSQL writers receive a non-superuser role and a database cloned from the
migration template; Redis users receive a key prefix and no flush/config/ACL power.

Credentials stay in owner-only state files, never events. Endpoints and credentials
enter `shell_environment_policy.set` at start and resume and effective launch
configuration is checked. Developer instructions name the variables and direct
writers to use those services. `services_private` supplies a separately owned,
run-labelled pool for server-control tests. Namespace release waits until the
run ends and its clients' family has exited. Shared idle pools stop after the
parking interval, restart on demand and refresh ephemeral endpoint assignments.
Superseded/private pools are removed after their last lease. Removal verifies full
IDs and every ownership label before `docker rm -v`; no prune or foreign-container
adoption occurs. Failed cleanup remains durable and visible for retry/recovery.
Status includes pools, ports, namespaces, leases, idle time, cleanup obligations
and report-only historical `lead.run` containers.

Before every native app-server launch, kernel pressure and the greater of the
compressor page/segment ratios are read without a helper process. The measured
Jetsam range was 0.68–0.70. Warning defaults to 0.50 (18 points below the lower
observation), critical to 0.62 (six points below it). Pressure level 2 also warns;
level 4 is critical. Warning defers with retryable `RESOURCE_PAUSED` and asks the
oldest eligible idle families to park; critical refuses with retryable
`RESOURCE_EXHAUSTED`. Neither interrupts an active turn. Recovery thresholds use
five percentage points of hysteresis. `memory_warn_ratio` and
`memory_critical_ratio` plugin settings and their `SOFTWARE_WORKFLOWS_` overrides
configure the levels; `SOFTWARE_WORKFLOWS_MEMORY_HYSTERESIS` configures hysteresis.
Invalid settings or unavailable readings defer launches. Status/Office expose the
last sample and level; transitions produce one event. Temporary resource refusals
are not completed idempotent results.

## Native projects, revisions and selection

Production discovery only calls native `project/list` and `project/read`, with
the experimental capability enabled. It follows every native list page. It
never creates or changes native projects, reads a guessed project file, or
treats discovery failure as an empty list. The adapter matches Codex 0.163.0;
unknown methods and fields are refused.

```sh
python3 -B -m coordinator rpc project/list '{}'
python3 -B -m coordinator rpc project/resolve '{"cwd":"/workspace/Frontend/sub","dispatch_cwd":"/workspace"}'
python3 -B -m coordinator rpc project/register '{"project":"native-project-id"}'
python3 -B -m coordinator rpc project/status '{"project":"native-project-id"}'
python3 -B -m coordinator rpc run/start '{"project":"native-project-id","cwd":"/workspace/Frontend/sub","dispatch_cwd":"/workspace","task":"Implement the change across the project"}'
```

`project/list` returns native project objects in native order as `data`, with
`next_cursor:null` after full pagination. `project/resolve` accepts absolute
`cwd`, optional `dispatch_cwd` and optional explicit `project`; it returns
`project:{id,name}|null`, `singleton`, and, for a project, `thread_cwd` and ordered
`runtime_workspace_roots`. `project/register` accepts either `project` or `cwd`
(and optional `dispatch_cwd`), and returns the activated revision. `project/status`
returns the active revision, revision history and resumable preparations. These
methods also accept `fixture` for a validated disposable provider home.
`project/resolve {registered:true,...}` reuses the complete native catalog saved
by registration when an active revision covers the target. It otherwise discovers
live projects. A project result includes `registered`, indicating whether that
revision is ready for lead attachment. Default resolution remains live discovery.

Selection finds projects whose native roots contain the target cwd. When several
match, it narrows them using the actual host session folder. Remaining ambiguity
returns `PROJECT_AMBIGUOUS` with candidate IDs and names. An explicit `project`
must contain the target. Successful discovery with no candidate keeps the legacy
singleton path. A Codex binary with neither project method also selects singleton.
An unavailable coordinator or singleton startup/admission failure before production
keeps 2.3.5's exec fallback. Once a project is selected, no error falls back.
An unresolved discovery error refuses before dispatch and never advertises a run
that does not exist. The host lead hook supplies `dispatch_cwd` from its
payload; the server does not invent it from its process working directory.

Registration resolves each ordered native root to a canonical Git checkout and
optional repository-relative prefix. Multiple subdirectory roots may share a
single checkout participant; duplicate physical directories are refused. An
outer Git view that owns an inner repository's path is refused with both roots.
An outer repository that ignores its nested repositories can register normally.
Non-Git roots, disappearing or replaced identities, submodules, unsupported base
modes and unsupported paths retain explicit refusals naming the root.

All checkouts are prepared, then native membership and physical identities are
read again before one SQLite transaction activates an immutable revision and its
bindings. Interrupted preparation can leave reusable checkout baselines, but no
usable partial project revision. A changed membership activates a new revision;
existing units and lead sessions remain pinned to their original participants.
The shared checkout identity is independent of project identity.

Schema migration **3** adds singleton participant sidecars to legacy units in one
transaction. It does not rewrite their record revisions, receipt IDs, UNKNOWN
history, seals or publication journals. In-flight legacy publication keeps its
original binding format and engine. An actual 2.3.5 reader refuses schema 3 with
`SCHEMA_VERSION: Unknown ledger migration version`; downgrade is unsupported.

Project admission adds measured project capabilities to singleton admission. Its
fingerprint includes the native binary, generated experimental schemas, adapter,
all-root effective config/hook evidence, revision, root order and cwd. Identical
hook definitions repeated across roots are deduplicated after coverage checks.
`hooks/list` takes `cwds`; `config/read` is queried with each singular `cwd`.
Native start receives `projectId` and every `runtimeWorkspaceRoots` entry. Resume
receives the pinned cwd and roots, **not** a nonexistent resume `projectId` field.
Effective roots, cwd and persisted project association must match before turns,
queued delivery or wakes. On 0.163.0, measured child threads inherit roots while
their own native project association is null; their recorded native ancestry
keeps them in the parent's unit, and resume checks that observed association.

For operational fixtures, `fixture serve --native-home DIR` accepts a private,
physical directory beneath the disposable fixture directory. Discovery and all
writers then use that same native home. Provision projects externally through
scratch native RPC; production discovery has no project mutation interface.

## Project paths, custody and receipts

String paths are absolute or relative to the actual actor/tool cwd. Lead tools
use their supplied cwd, including participant subfolders. Explicit qualification
uses `{"root_id":"checkout_…","path":"repository/relative.txt"}`. The router
finds the deepest physical Git boundary, including registered non-participants.
Explicit outer qualification entering an inner repository returns
`PATH_ROOT_MISMATCH` and its correct root; an ordinary relative path crossing to
a participating inner repository routes there. Unregistered nested Git boundaries
and paths outside the unit are refused. Existing symlink, hardlink, case,
Unicode, `.git` and path-type checks still apply.

Persisted paths remain `(checkout, repository-relative path)`. Claims, reads,
dependencies, pages and diffs include root identity so two `same.txt` files never
collide. Existing singleton string arguments and scalar response fields remain.
`lead-edit` and `operation/prepare` accept a path-to-image map, or a list of
`{"path":<string or qualified path>,"image":<complete image>}` entries.

Prefer `lead-edit` when the daemon can install and verify the images itself. Direct
`operation/prepare` requires a live executor `{pid,birth}` and records its generation,
actor, scope and kernel lineage before preparation. Missing identity is refused as
`EXECUTOR_REQUIRED`; a dead/reused birth as `EXECUTOR_GONE`; a platform without
reparenting-safe lineage as `EXECUTOR_LINEAGE`. A one-second observer retains the
hold while that editor or its descendants remain, including descendants reparented
after the editor exits. Ambiguous orphan lineage retains the hold too; heartbeat
expiry never releases it. Status and `NOT_QUIESCENT` name the editor and scope.
After proven exit, matching intended bytes with intact preconditions get exact
receipts and the owner's reservations are released. Differing bytes use the
existing no-effect/quarantine settlement. Across generations, intent never repairs
crash-gap attribution. Explicit `operation/complete` remains terminal caller
evidence. Cleanup releases only claims still tagged to that executor; reused claims
are not its own to release.

A native project patch has one parent invocation and per-checkout parts. Every
claim and pre-image is reserved before the hook allows execution. All parts must
pass native lifecycle, post-payload, computed-image, head and custody checks
before one SQLite transaction writes any receipts. Cross-repository moves bind
both source deletion and destination creation to the source pre-image. A path
outside the ledger (below) is written but gets no claim, pre-image or receipt, and
a move across that edge records only its endpoint inside the ledger. A partial
or interrupted group is quarantined in every affected checkout, with no successful
part receipts. Quarantine marks the parent and all parts in one transaction before
waiting for reconciliation locks. Completion re-reads their durable states inside
the receipt transaction: a quarantined invocation cannot become verified again.
Recovery does not grant group authorship retrospectively from
matching history. Restoring known bytes still requires a new verified edit to
resolve authorship, unless they are the path's baseline (what the branch commit
and the index both hold, or absence where both lack it): read again and found
equal to it, the restore resolves the UNKNOWNs it found there before its edit
(resolution `baseline`) and adds the path to no unit's scope (Recovery and
refusals). A restore never adopts unexplained bytes.

Opaque commands reserve every participant together, excluding other units'
existing claims independently in each checkout. Conflicts are rechecked after
asynchronous process collection. The complete group is reconciled concurrently
before release; descendants and uncertain process identity retain custody.
Checkout lifecycle locks are acquired in sorted, deduplicated checkout-ID order,
then the unit publication lock. Ledger locks protect observations and receipt
application. Publication order is separate from lock order.

Lead Bash quietness covers all registered checkouts, active writer turns,
custody, pending operations, lead-hook records and exclusive execution. Durable
mutation counters retain every operation/custody transition, writer actor update
and other lead call, including completed work. SQL triggers advance the counters
atomically with record changes; only the current hook's own revision is subtracted.
Partial indexes check live occupancy without rescanning finished history. Retained
exec-lock payload digests and file identities detect writers that already exited.
Pre/Post reconciliation reaches every checkout in every stored project revision
containing the target; hidden listings start at arrival, before attachment.
Observations finish for all roots before the final synchronous quiet check and
receipt transaction. Quiet-window attribution applies to every checkout in that
reach, using each checkout's arrival-time hidden listing and excluding its other
units' claims. Participants already published for the receiving unit are excluded
at the final receipt check: their later changes become UNKNOWN. Bash Pre lists
their roots and installed commits, and the host hook explains this before the
command runs. Writable participants in the same command retain normal attribution.
Post reports attributed paths per participant. Older-revision
checkouts outside the current pinned unit use a separate session unit that owns
them; no existing unit's membership is widened. Exact mediated edits retain
their stronger evidence. The first arm must arrive within three seconds; retries
with the same token acknowledge the persisted result without extending or reopening
the window. Arrival, confirmation and Post are generation-fenced. Recovery clears
every outstanding Bash window before hooks are admitted, including unarmed calls.
File-tool refusals that deny execution (`PREFIX_PUBLISHED` and authoritative
`CLAIM_CONFLICT`) are durably terminal before returning. They leave no live hook
occupancy. Replaying a denied Pre reevaluates it: an unchanged restriction denies
again; a cleared restriction permits normal preparation.

Recovery restores canonical ownership and every recoverable participant lease
before reconnecting listeners. It quarantines interrupted group operations,
verifies resumed native bindings, and only then advances turns or deliveries.

## Whole-unit review and publication

Project sealing freezes a version-2 vector containing the pinned revision,
generation, recorded publication order and every participant's identity, branch,
base, candidate or unchanged marker, provenance, dependencies and validation
digest. UNKNOWN overlapping mutation scope or recorded reads in any participant
blocks the whole seal. Unrelated UNKNOWN remains non-blocking. Unchanged
participants do not manufacture empty commits. `unit/diff` retains a combined
`diff` and adds root-labelled `participants`; review preparation binds its complete
digest before inference. Bound `code_review` always starts a fresh independent
session and rechecks the complete vector when recording the answer.

`unit/seal.publication_order`, when supplied, is a complete permutation of
participant checkout IDs. The default is native root order deduplicated by
checkout. Changing the order changes the binding and requires a new review.
Publication preflights **every** participant, including unchanged ones, before
any ref update, creates the group/repository journals and owns every required
index lock first, then publishes in the recorded order. Commits retain the
existing deterministic metadata and compare-and-swap ref/index recovery.
The group and unit persist `publication_state:"publishing"` before repository
publication starts. Every participant's query retains that blocking state through
the ref-update/checkpoint gap and recovery, including the interval after the last
repository journal completes but before group finalization.

Git cannot atomically update independent repositories. A completed prefix is
reported as `publication_state:"partially_published"`, visible from every
participant, with per-repository phase and commit. Claims remain held until the
whole group completes. Recovery examines actual refs/commits/indexes to recognize
a ref installed before its SQLite checkpoint, and repeats idempotently.

If a later base moves, recovery stops and preserves the installed prefix. Use
`unit/reopen` explicitly, then seal and obtain a new complete-vector review before
publishing the suffix. Reopen increments the generation, preserves prefix commits
in the next vector, invalidates old reviews and supersedes only journals proven
unapplied. Overlapping changed bases require new verified work after reopening;
there is no silent rebase. Already published participants remain read-only for
that unit: claims, mediated edits and command custody refuse `PREFIX_PUBLISHED`,
naming the root and installed commit. Further edits there require a new unit.
Resealing also refuses if a completed prefix's receipt rows differ from its
published seal, rather than omitting work recorded by an older implementation.
This compares the unit's own scoped receipts with the installed vector, not with
later UNKNOWN heads. Those later bytes remain UNKNOWN and cannot enter the old
unit's candidate. They do not by themselves prevent its writable remainder from
being reviewed and published with the original prefix commit preserved. The
exception is a read dependency: when a writer of the unit recorded a read
(`read_file`, `list_paths`) of a path whose bytes later became UNKNOWN, the seal
refuses `UNKNOWN_DEPENDENCY`, as for any UNKNOWN content the unit's work read.
A participant counts as published from the moment the unit's current
publication records the unit's commit for it (`commit_created`, which precedes
the ref update), before any reopen, until `abandon()` proves the ref unmoved or a
reopen keeps it in the prefix. A lead Bash change there is UNKNOWN, and a lead
file edit is refused: "already published", or "being published" while the ref
update may or may not have happened.
`unit/recover {unit,action:"abandon"}` records explicit
abandonment with the installed prefix retained. Claim handback is still explicit
and subject to settled custody; it never erases installed commit evidence.

## Consumer and lead session surfaces

`checkout/query {cwd,session_id}` is observational: it starts no daemon and does
no registration, native discovery, Git command or reconciliation. Alongside its
legacy fields it returns `root`, `projects:[{id,name}]`, and every non-complete
participating publication as `{unit,phase,state,participants:[{root,phase,commit?}]}`.
Older pinned revisions remain visible. Partial publication survives `fresh:false`
during recovery. Python 3.9 query clients supply empty defaults for old daemons.

`run/list`, each `run/status` form, and `unit/status` expose `project:{id,name}|null`,
`participants:[{root,checkout,branch,base,claims,unknown_count,phase,commit?}]`, and
`publication_state:""|"prepared"|"publishing"|"partially_published"|"complete"`. Legacy scalar
fields retain their original checkout meaning. `participant_revision` changes
with membership, claim, UNKNOWN and publication progress; run summaries include
it in their observable revision. `claim/list {unit}` covers all participants;
`claim/list {checkout}` keeps the original checkout-global view.

Server lead tools accept `project`, trusted `dispatch_cwd`, and cwd in any
participant. A supplied `unit` uses its pinned membership. Lead mailboxes are
identified by **session plus unit**; another session gets another mailbox. A lead's
inbox, its unread count and every lead tool's unread notice hold only the mail that
lead may read: its session's mailboxes (with an explicit `unit`, its mailbox of that
unit alone); mail to its own lead actor, the one its session's `lead/session` mapping
names, whose new mail is stored in that session's mailbox; and mail to the legacy
`lead` recipient, or to a lead actor no session owns, whose unit shares a checkout
with the lead's scope. That scope is the reach of every unit the session leads or
holds a mailbox for, plus the reach of the checkout its folder (`cwd`) is in; a
checkout's reach is itself plus every checkout a registered project revision binds
with it. A writer's mail to `lead` from a unit without a lead mailbox (a plain run
started without a lead session) belongs to that writer's unit, a lead's own `lead`
mail to the checkout of the folder it was sent from, and mail with neither to every
lead. Another project's writers share no checkout with that scope, so their mail
reaches neither this lead's inbox, its count nor its notice. Legacy pending messages
and acknowledgment records are preserved. Reading never acknowledges a message, and
aggregation changes neither message IDs nor recipients.
The common lead-tool wrapper carries the caller's session through normal and
early-return notices, including Abraham completion, `codex_runs`,
`codex_resume_run`, `codex_cancel_run`, `codex_run_log`, and `code_review`.
Helpers inherit that call's session context. A call with an empty session reads by
its folder alone: every lead tool sends its folder, and since 2.4.4 so does the
unread notice. Another session does not see this session's mail.

A lead read that names neither a session nor a folder has no project to read; a
2.4.0-2.4.3 server's unread notice makes such a read when its session is unknown.
`message/inbox` answers it with no mail (`data: []`, `unread: 0`) and a `notice`,
"Restart this Claude session to load software-workflows 2.4.4: its MCP server sent
neither a session_id nor a folder (cwd), and a lead's inbox holds only its own
project's mail.", never with another project's mail. A refusal would not reach that
lead: such a server's unread notice swallows any failure and shows only `unread`, so
the lead sees nothing until the session restarts. `message/ack` with neither is
refused `INBOX_SCOPE` ("A lead's inbox holds only its own project's mail: supply its
session_id or its folder (cwd). …"). Every lead tool's acknowledgement has sent its
folder since 2.4.0.

Lead tools reuse the registered native catalog instead of spawning native discovery
or re-registering roots on every call. Explicit `project/register` and each Abraham
dispatch refresh that catalog and membership. New lead units adopt the active
revision; existing session units keep their pinned revision until published, or
until the caller explicitly selects another unit/project. Native ambiguity rules
still apply to explicit lead tools, including unregistered projects in the catalog.

Host hooks have no project selector. They use only registered revisions, narrowed
by the session folder, and never start native discovery. One candidate selects
that project; no candidate or several candidates select a singleton checkout
unit. An existing matching session unit stays pinned. Selection ambiguity never
fails a hook. Checkout queries select unfinished publication journals and indexed
session/participant records before building responses; run lists construct full
status only for the returned page, rather than every historical run.

These are cooperative guardrails. Writers retain full filesystem/network access;
project membership is not an OS boundary. Effects outside participants become
UNKNOWN when that registered checkout is reconciled. A globally quiet window
does not establish causality against arbitrary external editors anywhere in its
reconciliation reach. Such an edit during an armed quiet window can be credited
to the lead. Existing limitations in detecting fully detached, markerless processes
remain. The push gate's behavior when the coordinator is unreachable is unchanged;
gate and Office consumer changes are outside P1's write boundary.

The git shim (`hooks/gitshim/git`), the `PATH` line the SessionStart hook writes and
the push gate skip every plugin version's shim (a folder holding `git` beside a
`push_gate.py`) and every exec-path mirror: `PATH` holds one shim, this version's,
first, and the shim runs the real git behind it. The shim starts the gate with
`CODEX_GATE_PYTHON`, the interpreter the SessionStart hook ran under, else with
`python3` from `PATH`; with neither, it refuses commit, push and every subcommand it
does not know, and runs the ones it knows (2.4.11). Until a session's first git call
shows the shim on `PATH`, the push gate reads a Bash command for what it runs, not
for its words (2.4.11): a `git` whose subcommand is push or commit or a word only the
shell can read, a word an expansion builds standing where git would before push or
commit, and the same inside `bash -c` strings, here-strings, `eval`, command
substitutions and here-documents a shell reads. Quoted words, and here-documents
anything else reads, are data, so `grep -c 'push$' log`, `git log --grep=push` and
`grep -rn "git push" docs/` run without an acknowledgement. A command the shell
assembles out of that reading's sight (`u='t pu'; g$'i'$u'sh'`) reaches the shim as
plain `git push`, and the shim decides it on its exact arguments. A push or commit whose
git does not run through the shim (by absolute path, through `command -p`, or in a
session whose shim is not on `PATH`) is outside this guardrail once the text gate
has stood down. The PowerShell tool keeps the word readings.

## RPC and C2 contract

One connection carries one UTF-8 JSON line and one response. Supply a unique
request ID and a durable idempotency key for effects:

```json
{"id":"caller-request-1","key":"logical-action-1","method":"message/send","params":{"recipient":"run_…","text":"Please coordinate this change","wake":"queue"}}
```

The response is `{"id":"…","result":…}` or
`{"id":"…","error":{"code":"…","message":"…","details":{},"kind":"refusal|failure"}}`.
The CLI wraps these as `{"ok":true,"result":…}` or `{"ok":false,"error":…}`.
CLI exits are **0 completed, 2 refused, 3 operational failure**. The CLI accepts
JSON from stdin with `rpc METHOD -`, `--key KEY`, and `--timeout SECONDS`.

```python
from coordinator.client import Client, CoordinatorError

client = Client(timeout=180)
client.ensure()
result = client.call("run/start", {
    "checkout": checkout_id,
    "mode": "lead",
    "task": "Lead's scoped change",
}, key="integration-action-42")
```

An identical completed key returns its recorded result. Different input with the
same key is refused. An interrupted request returns `RPC_UNCERTAIN`; inspect its
durable operation/message/publication records before retrying. Reusing a key is
not permission to replay an uncertain filesystem mutation.

Unkeyed calls allocate no RPC lock entry. Keyed locks count holders and waiters,
including cancelled waiters, and retire after the last caller leaves. Durable
SQLite idempotency records remain. `daemon/status.locks` exposes the RPC, actor,
message, checkout and unit registry sizes for diagnosis.

`daemon/status` advertises `capabilities:["draining-stop"]` and a unique `boot`
for this process start. Automatic replacement can bind `daemon/stop` to
`expect:{pid,boot}`. A mismatch returns `DAEMON_REPLACED` with the current PID,
boot and version before entering a drain. The CLI may omit `expect`.

Stop first refuses `NOT_QUIESCENT` with counts under `details.pending` when
recovery, workflows, writer turns, custody, deliveries, live lead hooks,
pending operations or mutating RPCs would be interrupted. Publication RPCs are
also counted separately. Mail to a lead-mode run's actor is no delivery: the lead
reads it through its inbox, and no native turn starts for it. This refusal leaves
admission unchanged. A Claude file call with a host birth or transcript path has
no time limit on this hold. A durable admission identity alone is not an end-evidence
channel. Calls without a usable host birth or transcript path retain the
120-second legacy limit, including calls from installed 2.4.4 hooks. At that
limit the operation is quarantined, the lifetime reservation is released and
UNKNOWN remains; this availability fallback does not prove the tool ended.
`details.lead_edits` names its session, tool-use id, operation, paths, host birth,
phase and the evidence or byte settlement it awaits; `daemon/status.lead_edits`
reports the same information. The daemon never ends the user's Claude process to
make a stop succeed. Its file-call observer and completion RPCs continue during a
drain. Otherwise,
new mutations receive retryable `DAEMON_DRAINING` while admitted requests and
completion calls finish. RPC settlement has a one-second bound; a timeout returns
`NOT_QUIESCENT` and immediately resumes admission without cancelling the requests.
Every listener reconcile is counted from entry, including time waiting for its
run's lock and work finishing after its caller is cancelled. The drain waits for
these invocations too; `NOT_QUIESCENT` names their runs, listeners and start times
in `details.listener_reconciles`. Once settled, closing admission and taking the
listener snapshot happen without yielding. The shared reconcile entry point then
refuses `DAEMON_DRAINING`, including maintenance settling a cancel, a resume,
re-admission, transport-gap recovery and a startup-quarantine retry. Nothing can
replace a writer outside the stop's snapshot. The daemon stops those listeners
and releases leases before stopping. Existing cancel, re-admission and transport-gap
guards also refuse new work during draining; a dropped connection is left failed
for the next daemon (or maintenance if the stop is refused). Backoff waiting alone
does not prevent a stop. Listener identity and settlement safeguards still apply.

Host hooks use three-second client calls within a fourteen-second budget, below
their fifteen-second host timeout. If a call encounters an unavailable or draining
daemon while the client's `upgrade.lock` is held, the hook waits for that lock to
be released and retries once with the same host invocation identity. It reserves
time for the retry and, for Bash Pre, the arm call. Without a held upgrade lock it
takes the existing unavailable path immediately. A replacement cannot rearm a
Bash window that belonged to the retired generation.

| Method | Parameters and result |
|---|---|
| `daemon/status` | Optional `checkout`, `brief`; generation, PID/birth, `boot`, `capabilities`, `draining`, readiness, version, `child_watcher`, `recovering` (finite initial startup scopes), `listener_reconciles` (active invocations, each with `run`, `listener`, Unix `started` time), `startup_recovery` (outstanding incident counts: `pending`, `retryable`, `permanent`), `ended` (writer app-servers the coordinator ended, by reason), `silent` (those not answering now), and unless `brief` the writers (each with its app-server's admitted `capabilities` and, when its probe fell back, `capability_fallback`) with their claims, custody, pending requests and messages. |
| `checkout/register` | `path`; serializes canonical registration and returns only a ready, complete baseline. Pre-existing dirty bytes are UNKNOWN. |
| `checkout/reconcile` | `checkout`, optional `paths`; returns new UNKNOWN IDs and scanned count. `resolve_quarantine:true` with a full scan clears a resolved I/O quarantine, never UNKNOWN history. |
| `checkout/revisions` | `checkout`, optional `path`, `cursor`, `limit`; paginated immutable revision records. |
| `run/start` | `checkout`, optional `mode`, `task`, `paths`, `model`, `fixture`; returns separate run, attempt, unit and actor IDs plus native policy echoes. `fixture` is only for an owned disposable provider manifest. |
| `run/status` | `run`, or optional `checkout`; persisted run or detailed checkout status. A run whose writer runs a turn after the run's own work ended (a message's turn) reads `running`, and `turns` lists each running writer turn (`actor`, `kind`, `thread`, `turn`, and for a root turn the `phase` or `message` that started it); the stored state is unchanged. `run/list`, the run's events and the checkout status report the same state. |
| `run/interrupt` | `run`; cancel the selected run's root/child turns and queued work, then verify process/terminal settlement, for a plain run as for a workflow. Its calls are refused (`RUN_CANCELLED`) until a resume. A lead-mode run has no writer turns (`RUN_LEAD_MODE`). Custody and claims remain until reconciled. |
| `run/resume` | `run`, `text`; refuse a frozen unit before starting a dormant writer, share its persisted demand start, then reconcile as needed (including replacement of an unavailable or stale app-server), refuse unresolved recovery UNKNOWN, and explicitly submit the new turn. |
| `run/terminals` | `run`; native terminal inventory. |
| `run/terminate-terminal` | `run`, `process_id`; native termination, followed by custody reconciliation. This ID is not an OS PID. |
| `lead/attach` | `unit`; create a lead actor in an existing unit, useful for recovery and C2 editor integration. |
| `claim/acquire` | `actor`, `paths`; all-or-none acquisition using the shared claim/custody conflict model. A path outside the ledger is neither claimed nor checked against other claims; the result lists it under `outside`. |
| `claim/release-request` | `actor`, `paths`; records a pending lead request and retains claims. |
| `claim/handback` | `unit`, optional `paths`; explicit lead release after operations/custody reconcile. |
| `command/declare` | `actor`, exact `command`, nonempty `reason`; grants one shared invocation to that actor. |
| `custody/reconcile` | `custody`; lead-requested process/terminal reconciliation. An uninspectable survivor keeps quarantine. |
| `lead-edit` | `actor`, `changes`; durably record complete intended images, install them, then verify bytes and modes. A path outside the ledger is installed but not recorded; the operation (each part, in a project) lists it under `outside`. |
| `operation/prepare` | `actor`, `kind:"lead-edit"`, `changes`, `executor:{pid,birth}`; bind intent to the live external editor **before** it writes. A path outside the ledger is neither recorded nor verified; the operation lists it under `outside`. |
| `operation/complete` | `operation`; compare the actual result to the already recorded intent. An after-the-fact hash cannot establish authorship. |
| `lead-command` | `actor`, explicit `argv` array; opaque command with custody, actual exit code and output digests. |
| `unknown/list` | `checkout`, optional `paths`; unresolved UNKNOWNs, context and restoration links. |
| `unknown/restore` | `actor`, `unknown`, `revision` (a known revision, or `baseline`); for a checkout-wide `*`, `revisions:{path: revision_id\|"baseline"\|null}` covering every path in the ledger. Null only where the branch commit and the index both lack the path; a path without a baseline is refused `RESTORE_BASELINE` with its `latest_known_revision`. Found at the baseline afterwards: `state: resolved, resolution: baseline`; otherwise `restored_requires_new_edit`. A path outside the ledger: `resolution: outside-ledger`, nothing written. A restore resolves only what it found before its edit, and holds its claims until that resolution commits. It then releases the claims its edit created (not one another call has acquired since) and is no unit's work: an edit or read that follows depends on whose bytes it restored. |
| `unknown/narrow` | `actor`, `unknown` (a checkout-wide `*`); read the whole checkout again, record each path whose bytes differ from its ledger head, and each path it cannot read (a symlink, a special file, bytes changing while read), as its own UNKNOWN, leave out a path outside the ledger (its UNKNOWNs resolved `outside-ledger`), and resolve every checkout-wide UNKNOWN recorded before the read. Writes nothing; refused (`RESTORE_CUSTODY`) while custody is active in the checkout, `RESTORE_EXECUTING` while a patch may still be writing, `NARROW_SCOPE` for a path's UNKNOWN. |
| `message/send` | `recipient`, `text`, required `wake:"none\|queue\|start"`, optional lead `sender`; durable message record. |
| `message/inbox` | Optional `recipient` (default `lead`, or a lead mailbox), `cursor`, `limit`, `session_id`, `cwd`. A lead's inbox is the mail its session or folder may read (lead session surfaces), one page in id order, with that mail's unread count independent of pagination. A lead read with neither session nor folder answers an empty inbox with a restart `notice`. Any other recipient's inbox (a writer's own `inbox` tool) is the mail to it. Reading does not acknowledge or claim context entry. |
| `message/ack` | `message`; optional lead `actor` (`lead`, a lead mailbox or a lead actor), `session_id`, `cwd`. A lead acknowledges the mail it may read, as its inbox reads it: as its session, as its mailbox of an explicit unit, as a lead actor (its own mail wherever it went), or as `lead` by its folder. Anything else is `ACK_ACTOR`; `lead` with neither session nor folder is `INBOX_SCOPE`. The evidence records who acknowledged (`by`). A second acknowledgement answers `duplicate:true`. A writer acknowledges only through its own native-bound `ack` tool, after its context echo (`NATIVE_BOUND` here). |
| `message/wake` | `message`, `wake:"queue\|start"`; explicitly wake a pending idle recipient; an `unproven` delivery is refused `MESSAGE_WAKE`, naming `message/retry`. |
| `message/retry` | `message`; reconcile native history/queue before an explicitly requested retry of an uncertain or `unproven` delivery, which may deliver it twice. |
| `unit/seal` | `unit`, optional `validation` array; freezes a candidate from verified receipt blobs. |
| `unit/diff` | `unit`; object diff and exact review binding. |
| `unit/review-record` | `unit`, `evidence`; lead-only completed review contract described below. |
| `unit/publish` | `unit`, `review`, complete `metadata`; lead-only journaled publication. |
| `unit/reopen` | `unit`; reopen a sealed unit with no publication or only provably abandoned journals, invalidating its review. |
| `admission/check` | `scratch`, optional `probes`; real native capability observations, never external model inference. |
| `records/list` | `kind`, optional `cursor`, `limit`; paginated diagnostic records, not a way to adopt changes. |
| `daemon/stop` | Optional `expect:{pid,boot}`; bound, draining stop. Refuses pending work with `NOT_QUIESCENT` counts; new admissions during drain return retryable `DAEMON_DRAINING`. |

Read every returned page until its cursor is null. Page sizes are not total
history limits. No successful result means a truncated scan was considered clean.
Real memory, disk, protocol or process-inspection failures refuse or quarantine.

`changes` maps repository-relative paths to complete raw images. A deletion uses
`{"mode":null}`; an ordinary file uses
`{"mode":"100644","bytes_base64":"…"}`; an executable uses `100755`.
Paths escaping the checkout, Git metadata, symlink traversal, hardlinks, special
nodes and unreliable native path encodings are refused. Case behavior is measured
on the checkout volume. Aliases and ancestor/descendant scopes conflict, including
operations within the same unit.

The socket is a same-user lead interface. Native dynamic calls cannot select an
actor or unit: C1 binds `threadId`, `turnId`, `callId` and listener incarnation.
C2 keeps lead-only operations out of model-exposed writer tools. A malicious
full-access process of the same user is outside this cooperative trust model.

Checkout identity includes the canonical checkout directory and its Git directory,
including their filesystem identities; a shared Git common directory is not a
checkout identity. A versioned SQLite uniqueness table and an external ownership
lock prevent duplicate handles and independent coordinators for the same checkout.
The ownership anchor also binds the original state root after a worker dies.
Legacy duplicate records keep their history and claims but are quarantined with
`CHECKOUT_IDENTITY_COLLISION`; C1 never silently merges them or returns a partial
baseline. Each new unit captures the actual branch and tip after pending
publication reconciliation. Registration's original baseline remains available
for UNKNOWN restoration.

A checkout's content is git's view of it, the set Codex itself diffs: tracked
paths, deleted ones included, plus untracked paths no ignore rule covers
(`git ls-files --cached --others --exclude-standard`). Ignored folders such as
`node_modules`, virtualenvs and build output are never read or stored. A path git
ignores (or one inside an embedded repository) that neither the branch commit nor
the index holds is outside the ledger, whoever wrote it: a patch, edit, read or
command there gets no receipt, claim, UNKNOWN or refusal, and no seal includes it.
A tracked path stays in the ledger though an ignore rule matches it, as does one
beyond a symlink, and registration also reads every path of the base tree. A head
or open UNKNOWN the ledger holds at an outside path, as releases before 2.4.4
recorded them, is let go at startup or the next reconcile that reads the path: its
UNKNOWNs are resolved by one record (`outside-ledger`, their reasons and times kept,
nothing deleted), and the lead mailbox of each unit they name, else the checkout's
leads, gets one notice listing them. A git failure while classifying keeps the
paths in the ledger, and a later reconcile lets them go. An embedded repository is
its own checkout; a submodule in the index is refused with its path at registration
and is its path's UNKNOWN afterwards. A scan records a path in git's view that is
not an ordinary single-link file, or that kept changing while read, as that path's
UNKNOWN, once per reason; ignore it or remove it. Registration still refuses it
with its path. A tracked file that became a folder reads as deleted,
in every scan and at sealing. A
scan whose folders gained, lost or replaced an entry while it read them is taken
again (three attempts). A first registration that failed or was killed, wrote no
history and admitted nothing is taken again on the next attempt.

As a lead Bash call arrives, before anything can wait, the coordinator lists what
exists outside git's view (`ls-files --others --ignored --exclude-standard
--directory`, plus embedded repositories). An ignored folder or an embedded
repository is one entry; a folder git lists only because everything in it is
ignored is opened, file by file. A path new to the ledger at the command's end
is credited to a quiet command only when no entry of that list covers it,
compared as the volume tells names apart (Unicode normalization, and case where
the volume ignores it); otherwise it may have existed before the command,
unseen, and is UNKNOWN. These listings are a state from before the command only
because the host has not run it yet: see the armed window below.

## Dynamic tools, hooks and custody

Native function tools are `claim`, `release`, `declare`, `send`, `inbox`, `ack`,
`status`, `read_file`, and `list_paths`. Their input schemas reject unknown fields.
`read_file` returns base64 bytes with offsets and records the returned revision
(`revision:null` for a path outside the ledger, which is no dependency);
`list_paths` pages known revisions and records those dependencies. They invoke no
shell, filters, credential helpers or Git hooks. Arbitrary shell strings never
receive the fixed-reader exemption.
Project pages use an opaque JSON `[checkout_id, repository_relative_path]`
cursor, ordered by checkout ID and then path. Indexed SQL applies the cursor and
limit in each repository, materializing at most the page size plus one row across
the participants. Checkout metadata is read at most once per root per call.
Singleton cursors remain plain paths; malformed project cursors refuse `PAGE_CURSOR`.

The fixed Python hook command contains only its program and state-root path.
Session, child, turn and tool-use identity come from the native payload. C1 calls
`hooks/list`, obtains the exact definition hashes, merges precise trust with the
user's existing trust, and restarts the still-idle discovery listener. It does not
write user configuration or enable blanket trust. The hook-program digest is
checked independently because native trust covers the definition, not script bytes.

For a patch, C1 records the complete add/update/delete/move scope, pre-images,
base revisions and original input before allowing execution. Successful matching
native hook lifecycle and `fileChange` computed diffs reconstruct the expected
post-images. Both move endpoints, raw bytes and executable bits are checked.
Failed/partial patches, rewritten inputs and insufficient lifecycle evidence
cannot produce exact receipts. Native computed diffs avoid duplicating Codex's
fuzzy patch matching algorithm.

Shared custody requires an actor-bound declaration of the **entire exact command**
for one invocation. Shared holders may overlap with each other and attributable
patches. One conflict predicate governs claims, preparation, command reservation
and final receipt verification, including released reservations that overlapped
an operation. Undeclared commands wait for
exclusive custody over the checkout minus other units' existing claims. New
claims cannot enter that reservation. Neither kind of custody proves authorship.
Publishing/rewriting Git commands are refused with “the lead records,” including
shell boundaries, quoted executable words, literal shell/env/command/exec wrappers
and Git global options. Unknown aliases/subcommands, executable substitutions and
ambiguous Git behavior refuse. Literal arguments such as `printf '%s' 'git push'`
are not Git invocations. Passing this check does not grant shared custody or a
fixed-reader exemption. Exclusions are directional: excluding `dir/file` cannot
authorize an operation over all of `dir`. Unknown-scope effects remain checkout-wide.

Hook waits are asynchronous and end at the hook's own deadline. Codex ends a native
hook 120 s after spawning it and then runs the call anyway (measured on 0.163). The
hook client counts from its own birth and denies 8 s before that, one deadline across
connecting, sending and every receive; the coordinator decides 4 s before the client. A call it cannot admit in time, waiting for the
writer's admission lock, the checkout lock or custody, is refused `COORDINATOR_BUSY`
(nothing ran; retry), and one admitted after that point is withdrawn instead of
allowed. A call whose writer is being reconciled (startup recovery, a reconnect, a
re-admission) waits for that reconcile until the same point, then is refused
`LISTENER_RECOVERING`. A call that a Pre hook blocked, ours or another, never runs:
its admission is withdrawn and its custody released, in every repository it spans.
A hook that still dies or times out fails open in Codex.

Codex 0.163 can refuse a call inside its handler, after an allowed Pre hook. Examples
are `tty` on the TTY-disabled runtime C1 configures, a patch whose context is not in
the file, and an unusable `workdir`. It runs no Post hook for such a call, and for the
first two it sends no item. Writers on gpt-6-astra make every call from inside a
code-mode cell: the model catalog sets `code_mode_only`, whatever the feature flags
say. A cell's nested calls are not joined by its turn's end, so a turn end proves
nothing about them.

The evidence C1 settles such a call by is Codex's own result line. A writer's
app-server writes its log (JSON, Codex's stderr) to `listeners/<id>/native.log` in the
state root, a private file it holds open itself, with the filter
`codex_core::exec_policy=trace,codex_otel.trace_safe=info,codex_core::session=warn,codex_core::session::handlers=info,codex_rollout::recorder=warn`.
Once a call's handler has returned, direct or nested, Codex writes `codex.tool_result`
to the trace-safe target, with `success=false` for a failure. That target carries
identifiers, names and lengths, never arguments or output. The session and
rollout-writer targets show how the thread's rollout is being written. Each operation
native's session dispatches writes a line: a turn's input, an interrupt, a shutdown.
When native fails to append a record, it logs a warning or an error
(`failed to record rollout items`, `rollout writer failed for …`) and the turn goes on
from memory. The daemon keeps on the log's record when the latest line of each kind
was written (`written`; `failed`, with what it said; a line that is not native's JSON
counts as `failed`, as of when it is read). The settlement of an input of unknown
outcome names them in its reason (below), never as proof: native's log drops a line it
cannot write, as when its disk is full.

The daemon reads the log from an offset in a record only the reader writes. It reads
at each Pre hook, at turn end, every 50 ms, at a stop, and in recovery, so a result
written while the daemon restarts is still read. Codex writes a call's result before
it hands the failure to the model or the cell, so a retry finds the refused call settled.
A result settles only a call that its own listener incarnation admitted, matched by
thread and call; the results of an app-server that died settle its calls before its
replacement starts.

A failed result proves that the call ended, not that it changed nothing. A patch is
settled as no effect, releasing its paths, when every endpoint still holds its
pre-image; other bytes quarantine it. A `failed` or `declined` item settles a patch the
same way. A command or MCP call is reconciled, as after any command, once no process
or terminal of it remains, and its custody is released in the same transaction. Items
that arrive after such a settlement complete the call's record and never reopen it. A
patch settled as no effect that Codex then reports applied is quarantined.

Codex also writes `codex.tool_result_ready` once a call's dispatch has ended: after
its result and Post hook, or alone for a call it aborted (an interrupted turn, a cell
that completed with the call pending). A call aborted while its Pre hook waits never
runs: its admission is withdrawn (`NATIVE_ABANDONED`) and its custody released, and the
hook reads the log once more before it allows. A dispatch end read before the call has
any record (its Pre hook still queued behind others) is kept on the reader's record
for Codex's 120 s hook limit, and that hook refuses the call. An aborted command is
reconciled, as after any command, once no process or terminal of it remains, and its
custody released: 0.163 spawns a local command's process synchronously in the handler,
which is dropped only at an await, so a process it started exists when the dispatch
end is read. An aborted MCP call keeps its custody until the lead reconciles it, since
its server may still be working on it. An aborted patch can still finish a write it
began, since 0.163 runs local writes on their own thread. It is quarantined
`executing`. Until Codex reports that its handler ended (its result, its completed
item or its Post hook) or its app-server exits, its paths stay busy for command
admission, attribution, restoration (`RESTORE_EXECUTING`), quiet windows and
publication, and the checkout's compatibility lease is kept (below).

A patch whose dispatch ended after its handler with no Post hook RPC of ours is
quarantined: its lifecycle can never complete. With the RPC received, Codex sent the
patch's completed item and its Post hook's outcome before it wrote the dispatch end,
but over the app-server connection, which the log is not ordered against, and no
request answer is ordered against them either. The patch then waits for them, holding
its paths, and they verify or quarantine it as they arrive. Its handler is done, so
nothing more is written for it. A Post hook that Codex cancels sends no outcome, and
such a patch waits for its app-server's exit (below).

A turn's end judges only that turn's calls: Codex can start the thread's next turn
before the coordinator reads the end, and that turn's calls reach their hooks first. At
a turn's end, a patch of a direct call whose handler ended without a complete
lifecycle is quarantined: the turn ends only after its direct calls' dispatches, and
Codex sends a thread's notifications in order, so nothing more comes for it. A call's
source is in the line Codex writes when it receives the call (`codex.tool_call_received`,
`direct` or `code_mode`), which its Pre hook reads. A cell's call can end after its
turn, so its patch waits for its own outcome or its app-server's exit, after an
interrupted turn too: Codex reports every abort reason as `interrupted`, and only an
interrupt with `code_mode_interrupt` on terminates the turn's cells. One whose handler
may still run is quarantined `executing` when the turn was interrupted or failed, or
when its app-server writes no log; otherwise it is left to its own receipts. A stop, or
an app-server's death, ends execution for every call of that app-server, and
quarantines a patch it never reported ending. A stop does not wait for a patch whose
handler and dispatch both ended, nor for an Abraham workflow whose settlement waits
only on such patches, since stopping their app-server settles them. A drain waits for
the workflow advances under way, and each stops at its next step: no phase completes
and no turn starts while the daemon drains.

Recovery and reconnect apply each log before they quarantine interrupted
cross-repository operations, and a receipt applies to its call in any state.
Admission probes both lines on the installed binary, with its red under the filter
2.4.0 used. The log is deleted when its listener stops; it also holds the policy trace
text.

A writer admitted by another release runs that release's `hooks.py`, so the daemon
refuses its calls (`HOOK_PROGRAM_CHANGED`). Its reconcile replaces it at the end, once
its native turns are read and nothing of its unit is in flight: no turn, custody,
admission, live operation, workflow input or delivery. A busy one is replaced before
its next turn instead: a resume, a message that wakes it, a workflow phase. Its
app-server is stopped, and killed if it has not exited 45 s after its SIGTERM (Codex
0.163's own bounds, 10 s for its threads and 30 s for a connection, plus 5 s). A new
one is admitted with this release's hooks and resumes the same thread and each
sub-agent thread, parent first (Codex refuses a sub-agent whose parent is not
loaded). A call still being admitted when its app-server begins to be reconciled is
withdrawn (`LISTENER_RECOVERING`). Each stop records the app-server's exit time
(`listener/exit`).

Until then nothing new is queued or started on such an app-server: a message for its
idle thread waits, and its re-admission is requested, so it is replaced once its unit
is quiet. A turn it is running still takes a message (`turn/steer`) while that turn's
calls pass, that is, while its hook program is this daemon's (`hooks_fail`). Once its
calls fail (a different hook program, `HOOK_PROGRAM_CHANGED`; a hook whose
interpreter or program file is gone; no coordinator hooks at all), the turn could
read the message but not act on it, so the message waits for the replacement too.
Codex 0.163 starts a thread's next queued input as soon as the thread is idle, inside
the app-server, with no request of ours (ext/queue `on_thread_idle`), so a message an
older daemon queued there (`thread/queue/add`) would start its turn on the old hooks.
Each of ours in such an app-server's native queue is taken back with
`thread/queue/delete`: as soon as its reconcile has resumed the thread, again just
before it is judged for replacement (a unit only that message kept busy then reads
quiet and is replaced at once), and by maintenance each second. Native runs a
deletion and its own dispatch under one guard. `deleted:true` means no turn started
for the message: it is pending again (`message/withdrawn`) and the replacement
delivers it. `deleted:false` means native started it first, and its history settles
it. A message whose delivery is under way (its lock held) is left to a later pass,
and only the coordinator's own entries (`msg_…` client ids) are taken back: an input
a user queued in Codex stays.

The replacement is reconciling from the save that makes it findable until its
reconcile ends: a call that arrives for it waits for that until its hook's cutoff,
then is refused `LISTENER_RECOVERING`, and deliveries and submissions wait. Every
reconcile of a run's writer holds one lock for the run
(recovery, resume, re-admission, cancel, a transport gap, a quarantine retry) and reconciles the
incarnation that is current once it holds it, so one replacement owns each
transition. Binding its successor records the predecessor as `state:"dead"`,
`recovery_state:"replaced"`, with `replaced_by` naming that successor; the successor
retains `replaces`, and `listener/replaced` records the transition. Startup repairs
older `dead/reconciling` records only when durable replacement lineage proves it.
A reconcile that fails after recovery marks the writer failed
(`listener/reconcile_failed`), and maintenance reconciles it again after 5 s,
doubling to 5 min; its unit's work waits meanwhile. A finished run's writer is retried
only while work is owed to it: its unit is not quiet, or a native delivery to one of its
writers is in flight or pending with a wake. A lead's mail never counts, since a lead
reads it through its inbox. An outstanding retryable startup incident is always
work owed, including for completed, failed or blocked runs. Otherwise its next resume reconciles it. At startup, a
checkout whose lease is refused (a writer outside the coordinator took it while no
daemon ran) fails only its own writers' recovery, each quarantined with an UNKNOWN;
every other checkout recovers, and the record of a start that never got a thread is
retired. Maintenance also submits again a prepared continuation left
`transition_pending`, for a plain run as for a workflow.
Only the thread's history carries over: what the old app-server held in its tool
runtime (code-mode variables and functions) is gone, and the coordinator's notice to
the writer says so (Message delivery). A start whose socket never appears ends its
app-server's process group and marks its record stopped.

A live writer app-server nothing can be done with is ended and replaced the same
way. Maintenance probes each connected one every 30 s
(`SOFTWARE_WORKFLOWS_ANSWER_EVERY_S`) with nothing that can wait behind its turn: its
process, read without a request, then `thread/loaded/list`, which native answers on
a task of its own. One stopped by a signal at two reads 30 s apart, as the same
process, is ended: SIGTERM, SIGCONT (a stopped process acts on it then), and SIGKILL
45 s later, its birth identity checked before each signal. So is one alive behind a
socket that refuses or closes on a reconcile's connection; a connection broken under
a write counts as closed, as one closed under a read or in its handshake. Its
replacement resumes the thread, and the coordinator's notice tells the writer why.
An app-server ended this way leaves an UNKNOWN only in a checkout where its unit held
custody, and `daemon/status.ended` counts them by reason. One that leaves
`thread/loaded/list` unanswered for 10 s (`SOFTWARE_WORKFLOWS_ANSWER_S`; of 2,410
measured answers on 0.163.0 the slowest took 0.70 s, with 13 writers each running a
heavy turn) is only reported (`listener/silent`, `daemon/status.silent`), and a
reconcile that needs it is refused `LISTENER_SILENT` (retryable) and made again
later: silence alone never ends a writer, since one that is only slow would lose its
turn and its runtime values.

A refused or withdrawn call that native runs after all is unmediated: UNKNOWN, and its
custody is held again. C1 compares native effect items with successful recorded
decisions, creates UNMEDIATED context and one UNKNOWN per item, and retains the opaque
operation window. Nonempty `write_stdin` remains part of its existing command window;
it has no native PreToolUse hook. A writer notification whose processing fails on a
path it cannot read (a symlink, a special file, bytes changing while read) records
that path's UNKNOWN in the checkout the failure names, or in a single-checkout
unit's checkout; any other failure is a checkout-wide UNKNOWN in each of the unit's
checkouts (`native evidence processing failed`).

An opaque operation (a writer's command or MCP call, a lead Bash command) records a
fingerprint, the count and SHA-256 of the heads it was admitted against, not a copy
of them; publish never reads it as a dependency. Records from before 2.4.1 are
compacted to the fingerprint in batches once the daemon is ready. Released custody
drops the process table its command was checked against, and older records lose it
in the same compaction. Hooks find their operation by call id; admission, reconcile,
custody and drain read only unreconciled operations and held custody, or custody
released since a time, through indexes. So does maintenance, each second: held
custody, runs still in flight, pending lead hooks, and undelivered messages to
retry. A readiness check asks for a brief `daemon/status` without the writers listing.

Before a patch claims paths, its unit's commands are settled. One whose call returned,
with no process or terminal of it left, waits only for Codex's completed item, which
can arrive after the patch's hook: the hook waits for it until its deadline. A command
still running conflicts (`CLAIM_CONFLICT`).

Custody lasts beyond a completed turn. Command roots bind the listener, thread and
tool-call identity to PID plus birth identity. Observed descendants remain recorded
after reparenting. Only that operation's native terminals participate; native
`processId` is opaque, while an available `osPid` requires ancestry and birth checks.
Persistent services are inventoried separately before a turn and again, under a
serialized boundary, before the listener's first command/MCP effect. This includes
native infrastructure started lazily after thread creation. Later survivors do not
become harmless services merely by executable name or longevity. A hook helper
must be birth-verified through its native listener before it is excluded; an
inventory racing that attestation retains custody while checking the evidence.

C1 never passes a native terminal ID to `kill()`. An unreadable or ambiguous
survivor retains quarantine. On macOS, kernel unique-process and original-parent
identities establish the private listener's ownership domain even when ordinary
system executables expose no markers and terminals return `osPid:null`. A domain
does not identify which overlapping command produced a survivor; all possible
reservations remain held. Ambiguous identities persist across reparenting. On macOS,
process environments are inspected only for the named identity markers and are
never dumped. Explicit orphan termination requires reverified birth, lineage and
session ownership or verified private-listener kernel lineage; unowned processes
are never signalled optimistically.

## Message delivery

Each message and delivery attempt is durable. Separate append-only events record:

```text
persisted → submitted → entered_context → acknowledged
```

Submission means the native RPC returned. Context entry requires the correlated
`userMessage.clientId` echo. Acknowledgement requires the recipient's native-bound
`ack`. Native echoes can arrive before the submission response; state never moves
backwards. An inbox read changes none of these states. Mail to a lead (to `lead`, a
lead mailbox or a lead actor) is never delivered natively, retried or woken: the lead
reads it through its inbox and settles it by acknowledging it there. Mail a lead
sends is settled by its writer recipient's delivery and `ack`.

A running root receives `turn/steer` with its expected turn ID. An idle root obeys
the sender's wake policy. A lost result is reconciled against full native history
and every queue page before a retry is considered. Where those reads cannot tell, it
is settled as any input of unknown outcome is (C2, cancellation): one proven
undelivered is delivered again as its wake says. Native delivery is not promised
exactly once; application-level acknowledgement is deduplicated.

A coordinator notice is what recovery tells a writer's root thread after replacing
its app-server (`messages.notify`: sender `coordinator`, wake `none`, no reply
asked). It is never steered, queued or started as a turn: Codex 0.163 preempts a
running code-mode cell whenever user input lands in the active turn (core
`input_queue.rs`, `watch_user_input`). The cell yields to the model and runs on,
and if the turn ends first, native can abandon the `apply_patch` the cell then
makes. A notice goes through `thread/inject_items` as one user message, which
native records in the thread's history without a turn: at once on an idle thread,
and at a running turn's next sampling request without preempting its cell. It
reads:

```text
C1_NOTICE <message-id> from the coordinator
<text>
(No reply or acknowledgement needed.)
```

When it is sent depends on what its app-server's admission proved
(`capabilities.inject`): with `active` at any time, with `idle` only while no turn
runs (it waits for the turn to end). With neither it is held (`message/notice_held`,
once per app-server) for the first app-server of that thread whose admission proves
one, and never steered meanwhile; a writer admitted by another release is not
calibrated, so its notices wait for its replacement. On an idle thread a notice is in
context at once; injected into a running turn, once `thread/turns/list` shows that
turn completed, since native records at a turn's finish what the turn had not taken.
An interrupt drops what a turn had not taken, as does the end of its app-server, and
a failed turn may have been aborted: each is injected again (`message/inject_dropped`,
`possible_duplicate_native_delivery`), so a notice arrives at least once and its id
tells a second copy apart. A lost RPC result is injected again the same way
(`message/inject_uncertain`); a refusal (`NATIVE_RPC`, `OUTBOUND_INPUT`) waits for
another app-server. A notice to a cancelled run or a closed unit is cancelled. Notices
recovery sent before 2.4.4 as no-wake mail from `lead` that are still pending are
recognised by their openings and delivered the same way; one that 2.4.3 already
steered is first settled as any steer.

A child message has a separate root relay hop. The root is instructed to use its
native collaboration tools; its echo or acknowledgement cannot acknowledge the
child. A child without inherited dynamic tools can emit `C1_ACK <message-id>` in
its own native assistant message. Only that child's verified native origin can
complete the child acknowledgement. The lead inbox remains durable with no lead
client connected.

## Seals, reviews and publication

Sealing checks the root and all discoverable descendants, active turns, pending
hooks/tools, native terminals, process custody and unverified operations. It
refuses UNKNOWN paths and dependencies on unpublished other-unit revisions; a
dependency refusal (`UNKNOWN_DEPENDENCY`, `UNPUBLISHED_DEPENDENCY`) names its paths
(`details.paths`). A reference to a path outside the ledger is no dependency. A
revision a restore wrote is the work of whoever wrote the bytes it restored: the
source the restore recorded, followed through restores of restores, or where it
recorded none, the receipt holding the same bytes. The path's baseline, or its
absence where the baseline lacks it, is no unit's work; bytes no receipt holds
remain the restoring unit's.
The complete mutation scope comes from operations, including add/delete and both
move endpoints. An UNKNOWN head or another unit's later revision cannot make a
path disappear. Every scoped path must still have this unit's final exact receipt,
the same current revision identity, matching bytes and mode. Seal, publication and
publication recovery rederive that scope, including for pre-existing seals.
It builds a private index outside the checkout from the base tree and **receipt
bytes**, including explicit deletions. The resulting candidate is immutable.

Seal/reopen/review/publish actions serialize per checkout and unit. A seal attempt
is durable before `sealing`, recording its generation, prior state/seal, intended
base, validation digest and private index. Success commits the seal, attempt and
unit state together. Interrupted sealing recovers to **open, review required**,
invalidates the previous seal and retains history and claims. It never treats a
partially built candidate as reviewed or replays a mutation.

If the named branch advanced only through disjoint paths, a new seal can use that
new base. An existing seal never silently advances. Its review must be repeated.
Validation entries require `command`, `exit_code`, `inputs`, and `scope` equal to
`mixed-checkout` or `candidate-objects`. An empty list records no validation.
Running a command in a mixed checkout does not validate a frozen candidate in
isolation.

Review uses exactly the object diff:

```text
git diff --no-ext-diff --no-textconv <base-oid> <candidate-tree-oid>
```

`unit/diff.binding` supplies `repository`, `unit`, `base`, `candidate`, `seal`,
`provenance`, and `validation_digest`. `unit/review-record.evidence` must contain
all those exact fields plus:

```json
{"completed":true,"verdict":"pass","review_identity":"completed-review-id","answer":"The actual completed review answer","answer_digest":"sha256 of the UTF-8 answer"}
```

C2's bound code_review integration supplies this lead-only evidence contract.
A seal alone never generates a passing review.
Missing, incomplete, stale or mismatched evidence refuses publication.

Publication metadata must contain exactly `message`, `author_name`, `author_email`,
`author_date`, `committer_name`, `committer_email`, and `committer_date`, all explicit
nonempty strings. The values are journaled before `commit-tree`, making a replay
after commit creation deterministic. Dates use a format accepted by Git.

The journal phases are `prepared`, `commit_created`, `ref_updated`, `index_pending`,
`index_updated`, and `complete`. Normal commit hooks, conversion/filter attributes,
signing, filesystem-monitor helpers and unsupported index extensions are refused.
Hooks are discovered with the real `git hook list`, without masking hooksPath.

Installed Git 2.56 rejects the proposed transaction combining `symref-verify HEAD`
and a named-branch update. C1 instead prepares an update through HEAD, acquiring
Git's native HEAD and referent locks, verifies the exact symbolic branch and base
while those locks are held, then commits the transaction. A mismatch aborts before
updating any ref. This behavior was calibrated in scratch repositories.

For the real index, C1 first fsyncs a unique ownership anchor beside the index and
durably records its inode, token, checkout, publication, owner birth and generation.
It then exclusively hard-links that already recorded inode to `index.lock`.
There is no interval with an owned lock but no durable ownership record.
After acquiring it, C1 revalidates checkout identity, symbolic HEAD and the expected
tip. A prepared native reference-verification transaction holds HEAD and its
referent fixed through index installation and then aborts without updating a ref.

C1 copies the currently locked index, changes only the unit's entries, verifies
all unrelated entries/stages, fsyncs and installs it atomically. The anchor remains
until the installation checkpoint is durable. Recovery can reuse an abandoned
owned lock only when its pinned inode and dead prior owner/generation agree; it
also recognizes installation before the checkpoint by inode and content digest.
A foreign lock is never removed based on age or a dead PID alone. Branch or
repository mismatch leaves the journal pending and claims held. Independent
staging of a unit path refuses. A ref-success /
index-failure stays recoverable; C1 never creates a second commit or blindly rolls
back the branch. Claims release only after complete reconciliation.

## Recovery and refusals

Worker generations and attempt generations fence interrupted work. Startup
recovery has two parts. Before any call is admitted, and with no request to an
app-server, it makes the ledger consistent: it invalidates outstanding lead Bash
windows, reestablishes canonical checkout ownership and every recoverable
participant lease, applies each writer log's receipts, and quarantines interrupted
cross-checkout operations, lead operations and seal attempts. The daemon then
serves, and reconciles writers in the background with bounded concurrency;
what two runs share is serialized where it is shared (a checkout's locks, a
calibration's binding). Exited terminal writers with no owed work or startup
incident are recovered read-only and become dormant, as described below. Other
writers are reconnected or replaced, initialized/resumed without starting inference, read back (incremental native
history, newly discovered children, unapplied durable evidence, terminals and
processes checked) and its workflow advanced. Each checkout's filesystem bytes are
reconciled after every run in it, each unit's publications and owned index locks
after its runs and checkouts, and messages last. Measured with 13 writers in 4
checkouts, the daemon served after about 2 s and recovery completed after about 55 s
(VERIFY.md). A plain run whose turn ended while no daemon read its app-server takes
that turn's state from history. Recovery resumes use `excludeTurns:true`: policy,
identity and ancestry are checked from the response. History metadata uses
`thread/turns/list` with `itemsView:"notLoaded"`, ascending, in pages of 25;
item bodies use turn-scoped `thread/items/list`, also in pages of 25. No history
size, item count, page count or total recovery time limit truncates observation.

### Exited writers and demand starts (round 21)

An exited, materialized writer whose run is terminal, has no work owed and has no
pending startup incident is recovered without starting a replacement writer.
Journal replay, native-log settlement, executor exit, custody reconciliation and
checkout snapshots still run. Bounded `history.offline` readers use the writer's
configuration and current MCP profile, but never start, resume or load a thread.
They discover saved descendants and read persisted turns and items, then stop.
Failed reads clean up their reader and leave an explicit retryable
`WRITER_HISTORY_READ_FAILED` outcome. A successful pass records the listener as
`dormant`, with a timestamp and reason, preserving its run, root thread and actors.
Subsequent startup passes skip it. A connected writer can also become dormant
through the verified idle-parking procedure below.

Read-only recovery compares journaled `item/completed` receipts since the last
verified receipt checkpoint against saved history, by thread, turn, item ID and
digest. Older checkpoints have no such proof, so their first comparison starts
at the beginning of that listener's lineage. One streamed pass per incarnation
groups completed receipts by thread and retains only identities and digests;
actors do not rescan the journal individually. Receipt and native item pages are
bounded; comparison counts, bytes, pages and seconds are recorded. A missing or
different item creates a `HISTORY_GAP` event and record, appears in status and the
run log, and is named in the next start notice. It still becomes dormant: its
old process has already exited. This exposes a migration limitation; it does not
repair a suffix an older native process failed to persist.

Resume, a waking message, or workflow advance requests one shared, persisted
start per run. Its reason and cancellation generation are durable before launch.
Sealed/published units and cancellation are checked before starting; cancellation
is checked again afterwards. The existing replacement path resumes the same
thread with `excludeTurns:true`, current hooks and the current writer profile.
Callers join the owned operation within the existing recovery wait bound. A
failure returns retryable `WRITER_START_FAILED`, retains the demand and dormant
binding, and can be retried. `WRITER_START_PENDING` means the operation is still
owned. These temporary outcomes are not cached as completed idempotent RPCs.
Startup recovers a persisted demand; existing input-settlement rules still
prohibit blindly resending an uncertain input after a crash.

Non-waking mail remains held and enters after a start, before the resumed input.
A failed held delivery stays pending with its failure recorded while the resume
continues. Waking mail requests the same shared start. Cancellation of a dormant
run, status, list and log reads start no writer. The start notice says "started
from dormancy", includes the runtime note and any history gap, and does not
create UNKNOWN merely because the writer was dormant. Once started, a writer is
eligible for parking again after its next idle interval.

`daemon/status.dormancy` reports current dormant/running families, history gaps,
persisted demand attempts, durable failure counters and this generation's cold-start
p50/max seconds. Run listings and logs include the listener state, dormant time,
reason, gap IDs and applied profile. Every transition is an event. Status reads
the small writer-start records; it does not count failures by scanning events.

### Verified live parking and native retention

`writer_park_idle_seconds` defaults to 1800; its
`SOFTWARE_WORKFLOWS_WRITER_PARK_IDLE_SECONDS` override takes precedence. The
measured 388 live turn gaps had a median of 792 s, with 27% exceeding 1800 s.
Zero disables parking. A malformed value disables it and reports
`WRITER_PARK_SETTING` in status; it does not stop the daemon.

A park requires a connected, reconciled family with no active turn, prepared or
queued input, owed workflow work, delivery or notice, native tool request,
background terminal, unsettled operation, custody or lead hold. Last native
activity resets the idle interval. One demand-driven timer schedules that
family's pending park; no parking timer remains for a dormant family.

Under the run's start/park and reconciliation locks, parking fences deliveries,
compares saved items with received evidence, unsubscribes every loaded thread,
confirms unload through loaded-thread inventory, compares saved items again,
and only then asks the owner to end the family. Pre-verification missing a suffix
reports `PERSISTENCE_PENDING` and retries later. A post-unload mismatch records a
`HISTORY_GAP`; it never authorizes a successful park. Demand waits for the park
outcome. A partial unload retains a durable resume requirement for recovery.
`FAMILY_GONE` precedes the dormant transition and advancement of
`received_verified`. Status includes attempts, completions, abandonment reasons,
persistence-pending count, parking p50/max and cold-start p50/max.

Receipt equality retains all fields, with only the native 0.163.0 persistence
transformations below accepted as alternatives. This is the same table as
`item_projection.ITEM_PROJECTIONS`; an unknown type requires full equality.

| Native item type | Receipt-to-saved comparison |
|---|---|
| dynamicToolCall | All fields unchanged. |
| reasoning | All fields unchanged. |
| commandExecution | aggregatedOutput: native 65,536-byte middle truncation; all other fields unchanged. |
| fileChange | All fields unchanged. |
| agentMessage | All fields unchanged. |
| userMessage | All fields unchanged. |
| contextCompaction | All fields unchanged. |
| sleep | All fields unchanged. |
| mcpToolCall | result: native 65,536-byte serialized preview; all other fields unchanged. |

A dormant listener may satisfy publication/reopen quiescence using its verified
exit and saved-family proof. A pending demand, history gap, uncovered actor,
delivery, custody, operation or tool obligation still refuses quiescence.
An ended peer's abandoned dynamic invocation retains its possible-effect evidence
and is retired explicitly; recovery never reruns it on a replacement listener.
Terminal inventory of a dormant writer is empty without waking it, and terminating
an absent terminal returns `NO_SUCH_TERMINAL`.

Retention runs one listener at a time after dormancy and at startup. The listener
and its replacement ancestry must be dormant; unresolved UNKNOWNs, quarantines,
open operations and pending obligations prevent deletion. Only rows at or below
both received verification and the native applied cursor qualify. Rows first go
to `archive/native/<listener>/<first>-<last>.jsonl.gz`; the file and directories
are fsynced and a SHA-256 archive record is committed before deletion. Deletion
uses 64-row transactions with a cooperative yield, reusing the native journal's
measured batch size rather than limiting retained evidence. A crash resumes the
same durable range and verifies its digest before deleting any remaining rows.

`run/events` includes archive paths and ranges. The office sees `native/archived`
events. Replay continues beyond its durable applied cursor, and run/log and
history use native saved history. Archive records preserve the removed raw
receipt range for diagnosis. Status reports ledger bytes, freelist pages,
archived rows/bytes and the last archive action. SQLite reuses freed pages; it
does not shrink the file automatically. To reclaim physical file space, stop the
coordinator completely, retain its backups and archives, and run SQLite `VACUUM`
on its ledger while no coordinator process has it open. Never vacuum on a timer.

### Explicit writer MCP profile

`writer_mcp_servers` accepts `all` (the default) or a JSON list of exact server
names, such as `["calendar","repository"]`; `[]` disables every server.
An explicit list enables exactly those servers, including a listed server that
the native configuration disables.
`SOFTWARE_WORKFLOWS_WRITER_MCP_SERVERS` overrides the plugin setting. The daemon
captures the selection at startup; status records its source and that changes
apply at the next daemon start. The captured selection controls subsequent
writer, demand-start and offline-reader launches. Exec preflight captures its
selection for that invocation. Existing live families retain their admitted
configuration until the existing replacement path launches a new one. An empty
or whitespace-only setting means `all`, with its source still reported. A
malformed value is visible in status and refuses writer launches with retryable
`WRITER_MCP_PROFILE`; the daemon keeps serving other operations.

Enumeration uses native `config/read`, `plugin/installed` and `plugin/read`
metadata. It never uses the eager `mcpServerStatus/list` API for discovery and
never reads the user's configuration file itself. Table-safe launch overrides
disable excluded user servers and plugin servers (or an unreadable plugin as a
whole). Per-server keys preserve a plugin's skills and other content, including
plugins with no MCP servers. Whole-plugin fallbacks are named with their reasons
in status and context. The explicit profile freezes its plugin catalog at first
admission: plugins installed later stay disabled for writers until the daemon
restarts. In `all` mode no plugin enumeration runs; status captures user-server
names from the first native config only, explicitly excluding plugin inventory.
An unknown selected name is refused with `WRITER_MCP_UNKNOWN`; ignored
disable flags are refused with `WRITER_MCP_UNENFORCEABLE`. The lead-tool
restriction remains in force for enabled related servers. Excluded names and
the action to enable them appear in the applied profile and one short line in a
new writer's developer instructions. Native resume retains an old thread's
initial context, so migrated threads instead get a durable coordinator notice
before input. Its stable message ID reuses delivery evidence across readmissions;
pending delivery refuses input with retryable `WRITER_MCP_CONTEXT`. The existing
notice protocol still permits duplicate injection after a lost native reply.
Configuration arguments and environment values are
not part of profile reporting. There is no automatic selection or fallback to
`all`. One indexed query at daemon startup reports writer MCP call counts for the
last 30 days as a suggestion only; that window is a policy choice. A missing index
or failed query reports the suggestion as unavailable without stopping service.

Round-21 fixture measurements: an accepted installed-2.4.6 drain followed by
recovery made 42 terminal writers dormant and replaced one mid-workflow writer
in 8.056 s. Offline readers peaked at four and 59,232,048 physical bytes; no
terminal writer was resumed, and no history gap, UNKNOWN or startup quarantine
was created. The mid-workflow writer completed after an explicit resume of its
previously unproven input. The next restart left all 43 dormant with no writer
process. With the same three configured fixture servers, ten same-thread cold
starts measured p50/p90/max 0.601/0.617/0.623 s under `all`, and
0.600/0.608/0.619 s with two servers selected. The explicit-profile model request
contained exactly one profile line on each of the ten resumed turns, including
after five restarts. These are local fixture
measurements, not latency guarantees. Start / mean native-launch guard sample
loads were 2.067 / 2.313 for the switch, 2.413 / 2.313 for the `all` cold starts,
and 2.525 / 2.616 for the two-server cold starts; these are sampled, not
time-weighted means. The calibrated MCP matrix covers three
user stdio servers, plugin stdio and HTTP: excluded servers had zero startups or
requests across writer, child, cold resume, exec preflight and plugin refresh;
threadless readers started no MCP clients. Skills-only plugins stayed enabled,
as did a plugin whose MCP server was excluded by a per-server key. Native child
connections are lazy until used.

The first comparison was also measured after an installed-2.4.6 accepted drain
with 27 synthetic histories (1.998 GB), 2.660 GB of journal receipts and one
additional hook writer. All 28 became dormant in 146.257 s, including memory-guard
waits. Four readers peaked at 142,266,448 physical bytes; the measured daemon and
reader family peaked at 210,293,944 bytes and 405% aggregate CPU. A second restart
took 0.450 s and made no native calls. Start / mean native-launch guard sample
load was 3.158 / 3.163. Scaling that wall time by the lead's
5.544 GB receipt measurement estimates 305 s, below the 15-minute review threshold,
so no older evidence was excluded from comparison. This projection is not a live
measurement: the synthetic receipts repeat completed-item payloads and each
thread has one incarnation, while the measured live chains have six to eight.
The final 605.003-second dormant idle observation added no maintenance arms,
native calls, threads or diagnostic bytes. The existing watchdog heartbeat
continued, and an induced stall afterwards produced its traceback. The targeted
regressions passed: U1 five times, both first-recovery orders and restarts, D2,
owned RPC twice, J1/S1/R1, deferred-COMMIT failure and one accepted burst drain.
Full release acceptance remains a separate round-21b gate.

A writer's recovery snapshot preserves paths held by lead file calls admitted
in the current daemon generation and still awaiting settlement, including calls
still admitting or settling. Current-generation prepared, allowed or running
lead-edit/restore operations have the same protection, including direct editor
operations without a host-call record and leads attached to the writer's unit.
For multi-checkout operations, recovery also exempts the current-generation parent
before its initial cross-checkout quarantine pass; every part retains its own
settlement and snapshot protection. The same kind/generation predicate covers
parents, parts and single-checkout operations. Native-writer groups remain subject
to reconnect quarantine. Startup's pre-admission crash-gap pass still quarantines
unfinished groups from the previous daemon generation.
The snapshot protection is checked after filesystem IO, immediately before applying the
observation. It grants no receipt: the call's own verification or failed-call
settlement remains responsible for those bytes. Earlier-generation holds and
native writer operations keep crash-gap observation and quarantine semantics.

Quarantine snapshots also preserve unrelated current-generation lead holds,
while excluding the operation being quarantined from that protection. A failed
lead call therefore still records changed bytes as UNKNOWN before its hold ends.
Restore pre-reads and checkout-wide post-reads require a complete snapshot:
they refuse overlapping active lead work before the scan and again after IO,
rather than skipping paths and resolving an incomplete view. A completion-only
hook has no hold of its own; its full observation stays durably pending when
another admitted call overlaps and retries after that call settles. The ordinary
lead-call settlement reads still observe their own held paths in full.

The separate, versioned `native_history` record belongs to an actor/thread across
listener replacements. Its native `backwardsCursor` is an inclusive ascending
turn boundary, obtained from a descending one-turn metadata read. The boundary
is published only after that turn and everything preceding it has been applied,
or unfinished work has a durable obligation. `native_turn` records retain status,
a conservative completed prefix, the applied item tail, and each unfinished item's
native predecessor. Catch-up reads each unfinished item with that predecessor's
exclusive item anchor and a limit of one, then reads new items after the applied
tail. Completed successors are not repeatedly decoded behind an unfinished item.
Mutable older turns remain pending after the main boundary advances. Item effects
and unfinished obligations are durable before the tail advances; a crash before
that save repeats the effects.
Native 0.163.0 does not persist `ItemStarted`: a code-mode command can still be
running after its turn finishes while no unfinished item appears in the listing.
Each observation therefore also pins turns named by the actor's prepared, allowed
or running operations that lack a completed item or a returned failure. These
pins come from durable admission and journal evidence, including operations whose
turn has already fallen behind the saved boundary. Turn records retain native
metadata positions so that finding an older pinned turn does not reread earlier
item payloads. Older records without a position locate that turn through paged
metadata once.

If the item tail does not supply an expected completion, a handler receipt,
dispatch-end receipt, Post hook or original executor exit makes closure uncertain.
The observer then reads that entire turn in bounded pages, applies its evidence,
and records which closure evidence it checked. This scoped read repeats only when
that evidence changes. It never scans other turns' item bodies, infers completion
from an empty listing, or releases custody merely because the executor ended.
If the call remains absent, existing settlement and UNKNOWN rules still apply.
`native_input` records retain client-ID entry evidence for message and uncertain
submission lookups. These facts are small; they do not copy historical payloads.

An absent, malformed or rejected checkpoint, a mismatched inclusive boundary,
reversion notification, or changed native rollout identity triggers a full paged
bootstrap. The observer checks native mode/path and file replacement/truncation,
and checks identity again before committing progress. Native cursor acceptance
alone does not establish trust. Any native refusal of a history request carrying
a stored cursor or item anchor discards trust and retries from a fresh epoch once;
the fallback records native's code and message without classifying its wording.
Cursor-free refusals and transport uncertainty still raise and retain existing
retry/quarantine behavior. Native `historyMode:"legacy"` is explicitly refused as
`NATIVE_HISTORY_UNSUPPORTED`; it differs from old coordinator records, which
bootstrap normally against paginated native history.

Normal, successfully applied notifications schedule coalesced observation through
the same observer. Receiving or dequeuing an event does not establish a history
prefix. `native_applied` retains journal application progress; successful progress
is flushed in one transaction across listeners after 100 ms or at 256 applied
messages. The timer is a scheduling target, not a hard wall-clock bound. A crash
can repeat that idempotent suffix; receipts remain immediately durable. Failed
application discards that listener's buffered progress and freezes its durable
prefix until recovery. Before reconnecting, the daemon also freezes progress
through the last previously received event. New notifications cannot skip that
unapplied suffix. Journal replay uses batches of 25 and yields between records.
Asynchronous native tool requests commit their `native_obligation` in the same
transaction as the received journal event, before enqueueing. Replay leaves
pending requests owned by the current daemon generation to the normal handler.
An ended receiving generation leaves unfinished requests owned and pending.
Durable preparation/effect stages distinguish preparation that a matching
reissued native request can resume from an effect that needs reconciliation.
Recovery never sends an old connection-local request ID on a new connection,
and a durable dynamic response is reused without repeating its effect. Legacy
requests with an already durable dynamic response need no new obligation during migration.

Lock order is the run reconciliation lock, the selected capacity lane, then the
observer's actor lock. Trusted observers use a separate short-observation lane.
An observer that discovers an invalid checkpoint releases its actor lock and short
slot before acquiring bootstrap capacity. A temporary offline reader first takes
its per-actor reader lock; its startup uses a short slot, released before history
observation can acquire bootstrap capacity.
Recursive work in the same task reuses capacity; child tasks acquire their own.
Observation does not advance workflows or deliver messages while holding its
lock. Queued reconciliations remain registered with the stop fence, including
when their callers cancel. Workflow advancement happens after capacity is freed.

`SOFTWARE_WORKFLOWS_RECOVERY_CONCURRENCY` is a positive integer, default **4**.
The shared bound covers startup, retries, gaps, re-admission, native resume and
replacement, plus untrusted history bootstraps. Trusted live catch-ups, message
lookups and input settlement use `SOFTWARE_WORKFLOWS_HISTORY_CONCURRENCY`, a
positive integer defaulting to **2**. Both settings accept only positive ASCII
decimal strings: `2` and `7` work; `abc`, `0`, `-1`, ` 2`, `+2` and `2.0` refuse
startup with `CONFIG` naming the setting. They do not wait for bootstrap slots.
Waiting in either lane creates no timeout, retry or quarantine of its own. Both
status forms expose `recovery_capacity` and `history_capacity` (each with
`limit`, `active`, `queued`), `history_observers` and `native_decode`. The latter counts actual JSON bytes at the transport decoder,
including late responses, with separate history response bytes, maximum response
size and maximum decode time. `native_intake` aggregates journal receipt/commit
counts and bytes, current/peak queue sizes, oldest queued age, batch sizes and
service times, wire-response latency, fixed journal-barrier waits and application
counts/bytes/backlog. These in-memory counters add no diagnostic commit per
notification or per Pre request. `history/observed` records page/item counts and
duration; `history/fallback` records why trust was lost.
`history/closure` identifies the turn and outstanding operations requiring a
broader read; `history/observed.closure_reads` counts those reads.

The 2026-10-23 real native fixture used 27 synthetic histories, 1,997.85 MB total,
with the supplied seven largest sizes and an explicitly synthetic tail. Limits
1/2/4 completed native start, resume and full observation in 77.0/45.5/33.3 s.
At 4, full-status p50/p99/max were 8.0/15.8/27.1 ms; actual Python 3.9 lead
PreToolUse Write p50/p99/max were 93.7/144.8/156.1 ms, with all 128 Write/Post
lifecycles verified. The slowest resume was 3.24 s. This sizes the highest
measured candidate, 4, with headroom against the native 10 s answer bound;
it is not a universal host-capacity guarantee.

A separate run exercised `recovery_plan` and `recover_writers` with those same
27 histories, real native hooks and independent RPC/hook samplers. The installed
2.4.5 initial pass took 75.235 s: 20 writers recovered and seven were quarantined
after genuine native history timeouts. The changed pass recovered all 27 in
30.207 s with zero startup quarantines. The following idle reconciliation of all
27 took 0.151 s and decoded only 29,691 history-response bytes. Latencies below
include samples during fixture preparation and cleanup as well as recovery:

| Round trip | 2.4.5 p50 / p99 / max, ms | Changed p50 / p99 / max, ms |
| --- | --- | --- |
| Brief `daemon/status` | 2.84 / 42.95 / 184.34 | 0.61 / 9.32 / 28.82 |
| Full `daemon/status` | 15.95 / 104.25 / 228.80 | 8.57 / 16.45 / 30.91 |
| Python 3.9 lead PreToolUse Write | 183.20 / 420.61 / 559.28 | 84.58 / 118.14 / 282.57 |
| Native writer PreToolUse hook | 128 / 262 / 270 | 61 / 86 / 103 |

The baseline and changed runs verified all 240 and 216 lead Write/Post
lifecycles respectively, and 169 and 156 writer admissions. The changed run's
largest history response was 206,641 bytes; maximum decode time was 2.90 ms.
These runs used pre-provisioned checkout/lease records, scoped fixture filesystem
enumeration and pre-started app-servers. They exercise startup's recovery entry
point, not the complete supervisor/admission launch. Separate daemon hard-kill
and restart runs exercise durable state recovery. The live incident's 20–30 s
status stalls were not reproduced by native histories alone; the long-history
timeouts were.

A supplemental real-daemon run isolated the local journal bottleneck: four native
writers, 80 turns, 20,080 items and 191.3 MB of native rollouts, plus 581,804
synthetic repeated deliveries of an actual native agent-message notification
(2,660,007,888 JSON bytes). On 2.4.5, full-status p50/p99/max were
2.47/4520.32/4520.32 ms; lead PreToolUse Write was 128.29/3337.35/3337.35 ms,
including one fail-open coordinator-unavailable warning. The changed run read
the entire unapplied journal: full-status was 2.53/9.70/457.11 ms; lead PreToolUse
was 78.28/755.52/755.52 ms, with all 65 Write/Post lifecycles verified and no
warnings. Writer PreToolUse was 52/194/194 ms. All four writers recovered without
quarantine. Cooperative first recovery took 19.79 s (baseline 6.46 s); the next
hard-kill restart took 0.359 s and decoded 5,467 history-response bytes without
replaying the old journal. The baseline restart still took 6.25 s and full-status
p99 reached 4.29 s. This establishes a journal-induced responsiveness red/green;
it does not reproduce the incident's exact 20–30 s maximum.

The final combined run used 27 native histories (1,997,860,144 bytes) plus a
2,660,157,537-byte journal. Baseline first recovery took 89.44 s and left seven
writers quarantined; restart took 67.48 s with the same seven still pending.
The changed first recovery completed all 27 in 46.62 s, with zero quarantines,
native slow-answer events or hook warnings. Its full-status p50/p99/max were
7.33/14.81/207.89 ms; Python 3.9 lead PreToolUse Write was
84.43/209.96/330.21 ms; writer PreToolUse was 55/112/118 ms. All 148 lead
Write/Post lifecycles were verified and 147 writer Pre/Post pairs completed.
The next changed hard-kill restart completed all writers in 1.227 s, decoding
only 32,478 native history-response bytes. The baseline restart's maximum
full-status / lead PreToolUse / writer PreToolUse latencies were
5.636 / 1.836 / 7.567 s. The combined fixture retained the same scoped admission,
filesystem enumeration and unchanged-byte lead Write adaptations described above.

A later comparison ran separate `Daemon.serve` processes and measured only the
recovery window, then killed each daemon with SIGKILL and restarted it against
the same durable state. The recreated 27 histories totalled 1,997,860,477 bytes;
one additional short writer supplied real writer-hook samples. The baseline's
initial pass took 86.535 s and left seven long writers quarantined; a second
restart still left those seven quarantined after 61.002 s. The changed daemon
recovered every writer in 36.736 s with no quarantine, then recovered all of
them in 1.238 s after the hard kill, decoding only 34,244 history-response bytes.
Its initial full scan decoded 1,970,667,741 history-response bytes.

| Recovery-window round trip | 2.4.5 p50 / p99 / max, ms | Changed p50 / p99 / max, ms |
| --- | --- | --- |
| Brief `daemon/status` | 4.33 / 41.72 / 147.54 (3,018 samples) | 0.92 / 9.73 / 25.34 (1,597) |
| Full `daemon/status` | 13.39 / 67.63 / 224.08 (2,249) | 7.38 / 14.88 / 27.08 (1,283) |
| Python 3.9 lead PreToolUse Write | 198.50 / 429.75 / 582.11 (138) | 89.42 / 160.19 / 181.41 (112) |
| Native writer PreToolUse hook | 113 / 265 / 268 (137) | 60 / 119 / 151 (110) |

All these RPC/hook samples succeeded without hook warnings, and every lead
Write/Post lifecycle was durably verified. This historical comparison wrote
unchanged fixture bytes: an earlier changing-Write variant exposed the recovery
snapshot versus lead revision conflict (`REVISION_CHANGED`). Its failed samples
are retained separately rather than counted as admitted calls. The unit-S
comparison below repeats the combined workload with changing Writes.
The separate-daemon run retains the scoped filesystem, pre-admitted lease/gate
and pre-started native fixtures; it does not measure cold admission or the
supervisor CLI. During the changed restart the full-status p99/max was 25.49 ms,
lead PreToolUse 99.54 ms, and writer PreToolUse 66 ms, with only 38/5/4 samples
respectively because recovery finished quickly. Status reported at most four
active and 24 queued recoveries (including the short sampler writer).

For one 251,241,389-byte synthetic history, 2.4.5's full turn hydration reached
the unchanged 60 s timeout. The observer bootstrapped 30,002 items in 1,202
bounded pages in 2.67 s; the subsequent idle scan decoded 1,092 history bytes.
A further fixture generated 300 actual native turns, including a 16 MB item:
bootstrap took 0.729 s, the next scan read zero items in 0.0047 s, and status
p99/max was 8.53 ms. Its largest JSON response was 16,000,766 bytes and maximum
decode time 11.61 ms. Native limits bound entry count, not individual item size;
these measurements do not promise a latency bound for arbitrarily large items.
Raw commands, recipes, samples and coverage limits are in the external
`scratchpad/r246-r` run directory's `acceptance-report.log` and
`regression-comparison.log`; synthetic histories were removed after measurement.


Round-2 review measurements on the same native 0.163.0 binary:

- With the notification worker held immediately before a real `item/tool/call`,
  the previous tree let concurrent replay create UNKNOWN and answer
  `DYNAMIC_UNCERTAIN` without dispatching the tool. Atomic receipt/obligation
  recording leaves the live request to its handler: one dispatch, successful
  response, no UNKNOWN. A separate actual SIGKILL during a pending tool still
  produced uncertainty after restart, without dispatching it again.
- Four 150.75 MB bootstraps occupied all recovery slots. Message delivery, live
  catch-up and resume input settlement previously waited about 2.8 s; with the
  separate two-slot lane they finished in 8.6, 55.3 and 9.1 ms while all four
  bootstraps were still active. An import-time negative control restoring shared
  capacity reproduced 2.50–2.51 s waits with the corrected fixture inputs.
- Twenty synchronized waves across 27 real writer app-servers produced 21,605
  native events in 61.25 s (21,165/min), including 12,420 text deltas (57.5%),
  and 1,587 item/turn completion triggers/min. The two-slot observer performed
  1,183 catch-ups, averaging 6.57 native RPCs and 1,641 history-response bytes.
  Status sampled at most two active and 25 queued short observations, with zero
  active/queued bootstrap work during steady state. All lead Write/Post
  lifecycles and 540 writer Pre/Post pairs succeeded; there were no warnings.
  One/two short slots gave lead Pre p99 643/583 ms and writer Pre p99 723/512 ms.
  Two is the highest measured candidate, with at least 1.7x headroom below the
  one-second target. This is a one-minute burst measurement, not a day-long soak.

| Round-2 streamed burst, two short slots | Samples | p50 / p99 / max, ms |
| --- | ---: | --- |
| Brief status | 2,184 | 1.23 / 84.76 / 318.08 |
| Full status | 1,739 | 7.32 / 148.11 / 339.47 |
| Python 3.9 lead PreToolUse Write | 220 | 98.48 / 583.20 / 724.06 |
| Native writer PreToolUse | 540 | 277 / 512 / 569 |

The burst fixture keeps the scoped filesystem, pre-admitted lease/gate and
unchanged-byte lead Write adaptations above. Its model provider emits real
Responses SSE text chunks, which native converts to delta notifications; the
daemon notification path and native timeouts are unchanged. In the comparable
non-streamed 27-writer runs, batching reduced `native_applied` row writes from
9,181 to 2,134, and measured SQL commits from 35,960 to 26,346 (including hook and
observer work). The streamed two-slot run made 3,671 applied-prefix row writes
for 21,605 native receipts.

A pinned-prefix fixture placed one unfinished command before 200 completed
64 KB outputs, then appended six batches of 50 outputs. Before the change,
catch-ups decoded 148,016,486 bytes for 19,656,300 new text bytes, with the reads
growing each batch. Afterward they decoded 19,751,112 bytes: 51 items and nine
RPCs per batch, consistently about 3.29 MB and 20–22 ms. Updating the pinned
item to completed was applied with a one-item read and ended its operation;
deliberately dropping its pending obligation made that control miss completion.
The unfinished projection row and large successor outputs were synthesized from
a real native code-command start event and fixture-generated records, then read
through the real app-server. Native did not expose that running command in
item history before completion, so this does **not** establish natural persistence
of an unfinished code-mode item followed by that many outputs.

Round-3 verification reproduced the missing-start case naturally on installed
0.163.0. A code-mode cell yielded while its admitted command waited on a fixture
file; turn A finished, turn B finished, and the observer advanced to B. Native's
listing for A contained no command yet. After SIGKILL of only the daemon, the
command finished and native persisted its completed item in A. Before operation
pins, restart left `completed_item` absent and custody held. With the pins,
restart applied the item, ended execution, reconciled the operation and released
custody with no UNKNOWN. No history or completion-order injection was needed.

With that real command still open, A also contained 200 completed 32 KB model
outputs. Six subsequent catch-ups, each following 50 new outputs, decoded
9,939,674 history bytes for 9,830,400 new text bytes: 51 items and 11 RPCs per
catch-up, 14.5–18.9 ms, and zero broader reads. Background scheduling was disabled
at import only for these explicit observer measurements; notification handling
and the observer were unchanged. The final hard-kill/restart still released
custody. The separate pinned-projection control exercised a completion before
the applied tail: dropping its pending-item record missed completion; a durable
handler receipt then caused one scoped 503-item read which found it. Five later
observations decoded 1,087 bytes each and performed no broader read.

An actual app-server kill while the command was still running exercised the
unfindable-completion path through an unloaded offline reader. Its first read
checked A's 201 items (6,612,021 history bytes, 52.6 ms); five subsequent reads
decoded 2,063 bytes each in 4.0–4.4 ms, with no repeated broader read. The operation
had no invented completion, custody stayed held, and the existing notification-gap
UNKNOWN remained. This preserves the previous uncertainty outcome.

On the final round-3 tree, the 27-writer streamed burst produced 21,601 events
in 61.21 s (21,173/min; 57.5% text deltas). Its 1,199 catch-ups used 7,868 RPCs
and 1,956,992 history bytes, averaging 6.56 RPCs and 1,632 bytes. Lead Pre p99/max
were 496/783 ms; writer Pre p99/max were 529/548 ms. All 540 writer Pre/Post
pairs and 204 lead Write lifecycles succeeded, without warnings or quarantines.
Short-observation capacity peaked at two active and 25 queued; bootstrap capacity
was idle. This remains a one-minute fixture burst, not a day-long soak. The late
completion probe covers a real command; the prior-position closure probe uses
the explicit native-projection adaptation described above. Neither establishes
the behavior of future native versions or every external MCP tool's lifetime.

The round-3 combined rerun used 27 synthetic writers (1,997,862,223 rollout bytes),
2,660,157,537 JSON bytes in 321,357 unapplied journal rows, and a separate hook
sampler writer. First recovery took 45.22 s and decoded 1,971,131,997 history
bytes. After an actual daemon SIGKILL, recovery took 1.54 s and decoded 42,727
history bytes (377,446 total native JSON bytes); launch through sampler completion
took 2.30 s. Both passes recovered every writer with zero quarantines and zero
slow native answers. The existing fixture adaptations remain: pre-admitted
lease/gate, scoped filesystem enumeration, app-servers started before recovery,
and real lead/writer hooks. The long external runtime path uses a fixture-only
relative Unix-socket adapter; its socket files and all native homes remain under
the external run directory.

| Round-3 combined recovery | First pass p50 / p99 / max, ms | Restart p50 / p99 / max, ms |
| --- | ---: | ---: |
| Brief status | 1.17 / 10.38 / 195.78 | 2.57 / 30.08 / 30.08 |
| Full status | 7.27 / 16.84 / 199.91 | 13.87 / 64.62 / 64.62 |
| Python 3.9 lead PreToolUse Write | 96.93 / 233.46 / 418.90 | 117.81 / 126.16 / 126.16 |
| Native writer PreToolUse | 73 / 130 / 169 | 89 / 191 / 191 |

First-pass sample counts were 1,954 brief status, 1,584 full status, 117 lead Pre
and 117 writer Pre. Restart had 58, 42, four and three respectively; its short
window does not establish a reliable tail-latency distribution. All recorded
round trips succeeded and every lead lifecycle was durably verified.

Unit S repeated the combined full-daemon comparison with changing lead Writes
on the accepted round-3 base, then the final unit-S code. The 27 native histories
totalled 1,997,862,254 bytes, with the same 2,660,157,537-byte synthetic journal
and one short writer for native hook samples. On the base, one of 60 admitted
Writes was quarantined with `lead completion: REVISION_CHANGED`, creating two
path UNKNOWN records; 111 later admissions were refused. Every Write installed
distinct new bytes between the real Python 3.9 Pre and Post hooks. This race
occurred without a timing injection in the combined run.

On the final code, all 137 first-pass Writes and four restart Writes verified
with exact receipts, with no path UNKNOWN, hook warning or admission refusal.
First recovery completed every writer in 52.49 s, with zero startup quarantines
and zero slow native answers. After SIGKILL of only the daemon, recovery completed
in 1.324 s and decoded 42,727 history bytes (378,744 total native JSON bytes).
Launch through sampler completion took 1.885 s. Capacity peaked at four active
and 24 queued recoveries.

| Unit-S changing-Write recovery | First pass p50 / p99 / max, ms | Restart p50 / p99 / max, ms |
| --- | ---: | ---: |
| Brief status | 1.13 / 11.64 / 153.98 | 2.50 / 21.80 / 21.80 |
| Full status | 7.36 / 17.36 / 160.32 | 12.29 / 23.86 / 23.86 |
| Python 3.9 lead PreToolUse Write | 95.35 / 250.47 / 376.83 | 105.49 / 123.53 / 123.53 |
| Native writer PreToolUse | 73 / 150 / 169 | 82 / 134 / 134 |

First-pass sample counts were 2,254 brief status, 1,825 full status, 137 lead
Pre and 136 writer Pre; restart counts were 54, 41, four and three. The short
restart window does not establish a reliable tail-latency distribution.
The scoped filesystem, pre-admitted lease/gate, pre-started native processes
and external relative-socket adaptations remain. These runs do not measure
cold admission, the supervisor CLI, or arbitrary concurrent filesystem writers.

Separate real-daemon cases held the snapshot at import-time barriers after a
real Pre/write and before Post. Startup and later reconciliation, a broad
command quarantine, a restore pre-read and a completion-only hook all reproduced
revision damage before their fixes and verified the held Write afterward.
An earlier-generation call still quarantined and retained its hold until Post;
its matching bytes earned no receipt. A failed call whose path recovery skipped
then recorded the differing bytes as UNKNOWN when settlement ended the hold.
Direct editor completion and a complete checkout restore also succeeded.
A lead attached to the writer's own unit remained pending through reconciliation
and verified through its own completion, instead of being treated as a crash gap.
No test files or synthetic outcome injection were used.

The unit-S completion run used a real native project with two participant roots
and a lead attached to its writer's unit. On the accepted unit-S base, reconnect
quarantined the lead-edit parent and both parts; completion refused
`OPERATION_STATE`, with four UNKNOWN records and no receipts. After the group
exemption, both parts verified with two exact receipts and zero UNKNOWNs. A
two-checkout restore held before its own verification likewise changed from
`REVISION_CHANGED` and six UNKNOWNs to two receipts and zero UNKNOWNs. An actual
daemon kill/restart retained the previous-generation group's quarantine, and a
real native two-checkout patch retained reconnect quarantine even when admitted
by the current generation. Resume still refused new writer work while the lead
group awaited completion; exemption does not authorize overlapping native work.
These cases pre-admitted fixture checkout identities and scoped snapshots to
fixture files; they exercise native project membership, admission, reconciliation
and receipts, but do not remeasure Git checkout discovery. The existing unit-S
startup/later snapshot, quarantine, restore, completion-only, same-unit editor
and checkout-restore recipes were repeated on this code. The 27-writer numbers
above are the preceding accepted run, not a new measurement of this group fix.

First-switch migration used 700,000 journal rows (2,583,617,788 JSON bytes;
2,852,339,712-byte SQLite ledger), including 15,000 legacy tool requests with
durable completed responses and 57% text deltas. Skipping redundant obligations
reduced replay from 21.81 to 19.71 s and explicit transaction commits from
15,432 to 392; obligation writes fell from 30,000 to zero. Applied-prefix batch
writes remained 28,000. Event-loop delay p99/max fell from 3.02/278.86 ms to
2.41/7.72 ms. Real lead Pre p50/p99/max changed from 75.82/356.34/1205.27 ms
(109 samples) to 87.76/132.88/132.88 ms (99), with every lifecycle verified.

Uncertain-input reads retain ordered queue/status fences around observation and
recheck the tail before treating absence as evidence. An exited writer is read
through an owned temporary app-server without loading/resuming its thread or
dispatching its queue; cleanup is covered by the stop fence. On 0.163.0 an exited
writer with two durable queued submissions remained unloaded before and after
each reader call: queue/list, thread/read, turns/list and items/list, then
queue/delete of exactly one submission. The other submission remained queued,
no item or turn/started notification arrived, and the rollout task_started count
stayed at two. This establishes the current reader call set's behavior on the
pinned native version, not a guarantee for future native releases. Raw rollout access
is supplementary diagnostics and policy readback only: complete-record offsets,
hashed user-text facts and compact turn-context policy facts preserve damaged-record
and missing-client-ID warnings and workflow policy verification. Reads serialize
per actor, and batches yield between durable updates. Unchanged compressed files
reuse those facts without reopening/decompressing. No unproven input is
automatically resent.

On 2026-10-22, a forced switch with 26 writers across 4 disposable checkouts
(3,700 files each, real Codex 0.163.0 and a scripted model) took 66.4 s before this
change and 68.5 s afterward from daemon launch to initial recovery completion;
both had zero quarantines or resume timeouts. The slowest root resume was 0.117 s
before and 0.094 s with metadata-only resume. The latter run sampled load up to
18.7 and had 26 reconciles active together. These measurements do not establish
an overall speedup, but leave ample headroom in the existing 60 s native timeout;
those short-history measurements alone did not justify a concurrency limit or a
longer timeout. The later long-history measurements above size the new bound.
Injected lost
resume responses separately exercised automatic recovery: the initial pass ended
at 60.8 s from `recovery/start`, and both transient quarantines healed by 62.7 s;
a persistent timeout and a permanent policy failure stayed quarantined.

A writer whose startup reconcile fails is quarantined with a checkout-wide UNKNOWN
in each participant checkout. A durable `startup_recovery` incident belongs to the
run and retains those exact UNKNOWN IDs across replacement attempts. Maintenance
retries temporary native/transport failures (`NATIVE_UNCERTAIN`,
`NATIVE_DISCONNECTED`, `LISTENER_SILENT`), temporary resource or lease contention,
and admission failures whose failed probes all report temporary errors. Policy,
project, schema, configuration and missing-history refusals remain quarantined;
an operational error alone is not evidence that retrying can help.

Retries run in the background, even for terminal runs, after 5 s, doubling to
300 s, with no attempt limit. `SOFTWARE_WORKFLOWS_RECONCILE_RETRY_FIRST_S` and
`SOFTWARE_WORKFLOWS_RECONCILE_RETRY_MAX_S` configure these existing delays; both
must be positive finite seconds and the maximum must be at least the first delay.
`listener/reconcile_failed` records the code, classification, attempt count and
chosen delay; `listener/startup_retry`, `listener/startup_quarantined` and
`listener/startup_recovered` record the incident's progress. Waiting incidents
do not hold the initial `recovering` barrier. Startup adopts older quarantines
and their UNKNOWNs, including those already narrowed by a lead.

Each retry completes the same history, admission and replacement checks as a
successful startup. An attached connection is resumed again when its earlier
attempt failed; stale hooks are replaced once the unit is quiet. After native
history is complete, each participant's bytes are read under its ledger lock,
with custody and executing-write safeguards. A `recovery_resolution` record and
`unknown/recovery_resolved` event name the history evidence, scan and exact startup
UNKNOWNs resolved. Both resolution links refer to that record; the immutable
UNKNOWN rows remain. Existing lead resolutions are preserved. Other writers'
UNKNOWNs and unrelated gaps are not resolved; differing or unreadable paths retain
their own UNKNOWNs. A failed scan leaves recovery owed. Only after these reads does
the current listener become reconciled and the incident complete. Workflow
advancement then runs outside the writer lock; unproven input is never resent.
A lead resume still reconciles first and refuses `RECOVERY_UNKNOWN` for remaining
UNKNOWNs that require an explicit lead decision.

A call for a run (`run/resume`, `run/terminate-terminal`), a unit's lifecycle
(`unit/seal`, `unit/review-record`, `unit/publish`, `unit/reopen`, `unit/recover`)
or a checkout's UNKNOWN (`unknown/restore`, `unknown/narrow`) that startup recovery
is still reconciling waits for it, at most 20 s per attempt
(`SOFTWARE_WORKFLOWS_RECOVERY_WAIT_S`), then is refused `WRITER_RECOVERING`
(retryable), with `retry_after` and what is still reconciling. The plugin's MCP
server sends such a call again while its own 90 s last; the CLI returns the
refusal. A keyed call keeps the refusal as its key's answer. A cancel is recorded
at once, and a hook waits for its writer as above. A replacement listener and any
changed implementation fingerprint require fresh admission before work can
continue. Uncertain mutation requests are never automatically replayed.

Use `daemon/status`, `records/list`, `unknown/list`, and `run/terminals` to inspect
an incident. Terminate a known native terminal through the native endpoint. Use
`custody/reconcile` to request lead resolution after survivors can be proved gone.
Claims never expire because a heartbeat stopped. Quarantine cannot be cleared by
submitting a newer PID or an after-the-fact content hash.

UNKNOWN is permanent history. To resolve it, first reconcile custody and stop
active writers, reopen a sealed unit if necessary, then restore with
`unknown/restore`. An admitted Claude file call on the selected paths refuses
`RESTORE_EXECUTING` until its end signal and byte settlement, even after 120 seconds
or a daemon restart. The legacy exception applies to calls without a usable host birth or transcript
path, including admission-tagged calls from older hooks. It ends that lifetime
hold at its 120-second quarantine limit, so its
UNKNOWN can then be restored. This check also precedes an outside-ledger release, and is
rechecked at mutation admission; quarantine alone never makes a running call safe
to restore. The error names the calls and the evidence they await. A restore found
at the baseline afterwards (every path of it, for
a checkout-wide gap) resolves the UNKNOWN. It is read again while it still holds
its claims, must show the restore's own revision, and that revision must still be
the head when the resolution commits. A restore resolves only the UNKNOWNs and
quarantined operations it found before its edit: one recorded later is an effect it
never saw, and stays. Another unit's claim of a restored path is refused until the
resolution commits. The restore then releases only the claims its own edit created
that no other call has acquired since: one its unit's other call also took, or one
taken again after a handback, stays. Any other restore alone still blocks
publication, and a new verified edit must follow; a checkout-wide gap requires that
edit to cover the entire restored scope. No `adopt`, risk acknowledgement or
publication bypass is
provided. Missing/corrupt receipt data refuses verification or publication and
does not release claims.

A checkout-wide gap is narrowed instead with `unknown/narrow`, without restoring
anything or stopping writers. What C1 tracks of a checkout, and so what a gap can
have left unexplained, is its bytes. C1 compares every path with its head under the
checkout lock, while no command holds custody there. Each path that differs becomes its own UNKNOWN, as in any
reconcile, so no unexplained byte is adopted; so does each path it cannot read (a
symlink, a special file), and a path outside the ledger is left out. Every
checkout-wide UNKNOWN recorded
before the read is then resolved. A patch or edit still in flight keeps its own
lifecycle. Seal reads its scope again and publish the whole checkout before they
freeze anything, so a change made after the read is still found. Not found: a change
made and undone before the read.

A recorded process id that another user's process now holds (a root daemon that
took the id of one that exited) counts as gone. macOS refuses both the process's
description and a signal to it. Its short description (`PROC_PIDT_SHORTBSDINFO`)
still answers, and none of its user ids is this user's, while every writer process,
hook and service runs as this user. A process that has exited and is not reaped yet
is read as a zombie, not refused. A process of this user that macOS will not
describe because it runs a set-user-ID program (every `/bin/ps` is setuid root) is
read through `sysctl` (`kinfo_proc`: start time and state) and its short description
(parent and user). Only a process of this user that none of these reads answers keeps
its custody held (`PROCESS_UNREADABLE`). On Linux with `/proc` mounted `hidepid=1`, a
process that cannot be read keeps custody held whoever owns it: the directory shows only
its effective user, and a set-user-ID child of a writer keeps the writer's real one.

Common refusal codes include `CLAIM_CONFLICT`, `OPERATION_BUSY`, `CUSTODY_WAIT`,
`LEAD_RECORDS`, `UNIT_FROZEN`, `STALE_BASE`, `REVIEW_BINDING`,
`UNKNOWN_BLOCKS_SEAL`, `UNKNOWN_BLOCKS_PUBLICATION`, `UNPUBLISHED_DEPENDENCY`,
`UNIT_INDEX_CHANGED`, `INDEX_LOCKED`, `WRITER_RECOVERING`, `LISTENER_RECOVERING`,
`LISTENER_SILENT`, `RESTORE_BASELINE`, and `ADMISSION_EXEC_POLICY_UNOBSERVABLE`.
Preserve the error envelope and its context; a refusal is not successful work.

## Admission, fixture and state layout

Admission fingerprints the native binary/version, generated experimental schema,
method inventory, effective config/model/provider, features, precise hook trust,
hook program, MCP definitions, native-derived execution policy, Git, SQLite,
Python, OS and measured checkout-volume behavior. Outgoing typed native objects
reject unknown keys even where the vendor accepts them; legitimate open maps
remain open. Unknown correctness-critical incoming semantics refuse.

For native 0.163.0, the policy trace is complete only when the native requirements
API proves managed requirements absent. A changed disposable `.rules` file changes
the trace while config/read stays unchanged. With an unobservable managed overlay,
C1 refuses admission instead of mislabelling a config digest as a policy digest.
The user's CODEX_HOME files are never read by the coordinator.

```sh
python3 -B -m coordinator admission --scratch /private/tmp/c1-capabilities
python3 -B -m coordinator admission --scratch /private/tmp/c1-policy --probe exec_policy
python3 -B -m coordinator fixture serve --directory /private/tmp/c1-provider --plan -
```

The fixture accepts `{"default":[…],"routes":{"task marker":[…]}}` on stdin.
Steps support `patch`, `command` plus optional exec `arguments`, `tool` plus
`arguments`/`namespace`, `text`, and `delay`. It binds loopback, implements the
Responses SSE subset used by admission, disables auth and plugin synchronization
only in disposable fixture homes, and never calls an external model. It is
product capability code, not a test suite. Production writers do not receive
those fixture settings.

The eleven required probes observe a bad configuration before accepting its good
counterpart: hooks, native evidence, dynamic tools, delivery, TTY, receipts,
reconnect, terminals, ownership, policy and outbound validation. `exec_policy` is an additional probe.
Calibration reports retain the fingerprint of the configuration actually measured. A
separate C1-owned red/green Git probe proves that missing objects cannot invoke
lazy-fetch helpers during helper-free reads.

Admission can probe one optional capability, `inject`, after the required probes.
Writer admission runs the controls and retains their report; it does not cache
them under a separate fingerprint-only listener. Disposable-provider controls
retain their own fingerprint; they do not establish a production injection capability.
An unmatched production configuration records `CAPABILITY_CONFIGURATION_UNMEASURED`.
Its red is a `turn/steer` of a note while a code-mode cell runs: the request after it
must show the cell still running, the preemption the detector looks for. A
`thread/inject_items` into the same running cell must leave the cell to complete, be
carried by the next request and start no other turn; an inject on an idle thread
must start no turn within 1.5 s and be carried by the next turn's first request. The
verdict is `active` when the red came back red and both other parts held, `idle`
when the red came back red and only the idle part held, and none otherwise (notices
are then held). A probe that raised or timed out proves neither way and takes
`None`, recorded with its error in `capability_fallback`. Held mail can instead be
durably prefixed to the next already-authorized input, before that input's text;
the native client-message echo settles both. This fallback creates no extra turn.
Each probe step has its own bound;
`admission/capability` records the verdict and its seconds.

| State entry | Purpose |
|---|---|
| `ledger.sqlite`, WAL/SHM | Versioned schema, identities, revisions, events, UNKNOWNs, idempotency and journals; daemon only. |
| `blobs/<sha256>` | Immutable raw receipt bytes, fsynced before any reference is committed. |
| `listeners/<incarnation>/` | Persistent listener evidence; endpoint allocations live in the short runtime directory. |
| `indexes/` | Private candidate and publication index copies. |
| `schemas/<binary-digest>/` | Native generated experimental schema and completion marker. |
| `calibrations/` | Successful capability bindings and evidence locations. |
| `supervisor.lock`, `worker.lock` | Singleton lifecycle. |
| `upgrade.lock` | Held by the one process starting, replacing or stopping the daemon: a client, or a supervisor restarting its worker. |
| `lead-hooks/` | One marker per lead call the coordinator may hold something of, named by the SHA-256 of its session and tool-use id, until a Post or PostToolBatch settles it. |
| `endpoint.json`, `startup-error.json` | Private endpoint discovery and structured startup diagnostics. |
| `supervisor-exit.json` | Why a supervisor exited without restarting its worker (`UPGRADE_HELD`): only one holding no lease does. |
| `supervisor-wait.json` | A supervisor waiting for `upgrade.lock` to restart its worker, its leases held: since when, how many leases, and `until` once it restarted it. |
| Canonical `/tmp/c1-<uid>/s*/` | Owned per-incarnation RPC/native/probe endpoint allocations. |
| Canonical `/tmp/c1-<uid>/<identity>.owner` | Cross-state checkout ownership anchor; never remove to steal ownership. |

Retain the state directory and scratch evidence when investigating a refusal.
There is no garbage collector or destructive reset command in C1. Do not remove
state or lock files to force publication or steal live ownership.

## C2: Abraham and lead integration

`abraham` prefers one native thread with two durable turns. The coordinator
persists the task, dispatch and target folders, constraints/prompts, local image
paths, model, max effort, compaction setting, phase input identity, native IDs and
complete answers before progressing. Analysis patches are denied for root and
child actors. Opaque analysis effects become UNKNOWN. A completed, nonempty,
successful analysis and settled operations are required before implementation.
A lost submission reply requires history reconciliation by its stable input ID;
it never authorizes automatic replay. MCP disconnects detach observation.

Initial `claims` are optional. Omitted claims are acquired as native edits declare
their paths. Existing live claims and verified lead edits can explain dirty
files. `allow_dirty` permits dispatch with other dirt but grants no receipts and
clears no UNKNOWN. Results start with the signed header and include the durable
run/unit, native policy echoes, phase result and exact path/actor/thread receipts.
Custody and UNKNOWN are labelled separately from authorship. Results include the
native rollout path and a bounded read-back of its turn-context policy fields.
An absent, unreadable or unexpected policy is explicitly unverified; no prompt,
tool output or credential content from that file is copied into the result.

The supervisor holds one compatibility lease per checkout writer group, using
the existing filesystem-identity lock, legacy bridges and mixed-version barrier.
Multiple native units share that lease. The MCP server and replacement workers
do not own its lifetime. A worker whose supervisor is gone starts a new one holding
the same leases at once, whoever holds `upgrade.lock`: that supervisor starts no
worker until this one has died, and then only under the lock, as any restart. An
inherited, inode-verified venv `.in-use` descriptor also survives worker replacement.
Queued/uncertain deliveries, process custody, a live operation and a patch native
may still be writing (`executing`) retain
exclusion, whatever the run's state; a stop releases it after every app-server has
exited and every admitted Claude file call has ended and settled (or reached the
explicit legacy no-end-evidence limit below). A lead file
call reserves its paths before admission can wait and takes the compatibility
lease before its Pre allows execution. Its durable lifetime retains that lease
even when its operation is quarantined, including during stop and recovery.
An effect-capable Pre hook holds it, under the lock its release takes, until
its operation is recorded, and takes it back if a run's end released it: a cell's call
can still wait in its hook after its turn. While a writer outside the coordinator holds
the checkout, the call is refused (`EXCLUSIVE_WRITER`). Pre-production startup/admission failures clean up and name
the failed check before selecting exclusive exec fallback. Started or uncertain
native work returns its durable handle instead. Windows retains the existing
explicit write-mode refusal; its lock lifetime has not been implemented.

The server starts the coordinator on demand with its actual Python >=3.11
interpreter. Module resolution is independent of the target checkout. First
admission can take tens of seconds and is visible in progress and durable events.
A call the daemon refuses `WRITER_RECOVERING` is sent again while the call's own
time lasts (90 s by default), only while a whole wait (the refusal's
`waited_seconds`) and 5 s of the call's own work still fit in it; a keyed call is
not, since the daemon keeps that answer as its key's.
Admission serializes calibration per requested configuration; every listener
still proves effective policy, hook trust and writer tool catalogs. Control
reports are evidence, not reusable production capability entries. The measured native execution-policy representation has
two unordered maps; only their entry order is normalized, with raw trace digests
retained as observations. Ordered rules and policy values still change the key;
unrecognized representations conservatively retain their raw digest.
`CODEX_ORACLE_LOG_DIR` overrides the existing log root for
isolated runs; `CODEX_HOME` controls configuration lookup. Only owned scratch
targets/logs sharing a private temporary ancestor use isolated legacy-server
discovery. Different such log namespaces are not a general exclusion protocol.

`codex_runs` merges native and exec records; native UUIDs start with `run_`.
Dispatch and target workspace visibility survive MCP restart. `codex_run_log`
uses native event sequence cursors, and `codex_resume_run` with an empty nudge
collects existing results without inference. A nonempty nudge reconciles first
and refuses active/uncertain turns, frozen units and UNKNOWN. Cancellation
(`run/interrupt`, for a plain run as for a workflow) targets the selected unit's
actors, queue and terminals, and reports `cancelling` until terminal events and
verified process disappearance establish settlement. A lead's mail is no work of the
run, and stays as it is; a message sent to a cancelled run's writer waits for its
resume. A cancel requested while a resume or a phase's settlement waits stands: the
resume is refused (`RUN_CANCELLED`), and no phase starts. A turn that starts after its
run's cancel (its `turn/start` was in flight, or a queued message started it) is
interrupted once it is seen, and none of its effect-capable calls runs
(`RUN_CANCELLED`). Each cancel is a new generation, and each step of its pass and of
its settlement first checks that it still stands: one that a resume cleared, or a
newer cancel replaced, interrupts and saves nothing more. While a cancel's pass runs,
the run is not reported `cancelled`, and a resume is refused (`RUN_ACTIVE`). The
cancel reports `cancelled` only once no phase's `turn/start` is in flight or
unresolved, and native reports each of the unit's threads idle (`thread/read`, in
the thread's own request order). A thread it cannot read counts as running unless
it does not hold it loaded, and an app-server that runs without a connection counts
as running. An input whose RPC is still in flight is waited for. One whose native
outcome was lost (a phase's `turn/start` result, a writer delivery not yet in
context) keeps the run `cancelling` until it is settled, with no time bound in the
decision. Its thread is read on the app-server it went to, in the thread's own
request order: `thread/queue/list`, then `thread/read` of its status and of its
turns (native runs a thread's requests one at a time in arrival order). A turn that
holds the input is its turn, interrupted and settled; a queued input is deleted, and
only native's `deleted: true` proves it withdrawn without starting a turn (one native
dispatched first is settled by its turn). The reads cannot tell when the thread is
idle and holds no record of the input: `turn/start` answers once core holds the turn,
but native records the input and reports the thread active only once that turn's own
task runs, with no bound on the delay. That app-server is then ended once nothing
else of the unit is in flight there (a `turn/interrupt` with an empty `turnId`, which
aborts a turn still starting, ordered after every input sent to the thread, then its
stop: `listener/ended`), and its rollout is read, final once the process has exited.
Native writes a turn's records in order, but a record it fails to append is missing
while the turn goes on from memory, and the line its log writes of the failure can be
lost too, so no silence proves that an input never ran: not the rollout's, not
history's, not the log's. Only native's own record of the input tells where it went:
its client id's record in the rollout or in history (that turn: it entered), native's
queue holding it (queued), or native's `deleted: true`. An input that nothing can start
any more and no such record shows is `unproven` (`workflow/submission_unproven`,
`message/delivery_unproven`, each with its reason): one sent to an exited app-server
whose rollout holds no record of it, or whose rollout cannot be read and neither the
replacement's history nor its queue holds it; a queued one once native's queue, read
after it in order on that app-server or on its replacement, no longer holds it (a
replacement that may be dispatching it from that queue is ended first); and on a
running app-server, one whose turn has ended, or a steer when no turn runs, that
history holds no record of. The reason names what the rollout and the log do show: a
failure native logged after the send, a log showing nothing of native's session after
it, damage to the rollout after it, and a user message holding the input's text
without its client id's record, naming its turn (a text proves nothing: another input
can say the same). Nothing sends an unproven input again of itself, as it may have run
with its records lost (a coordinator notice excepted, below): the run reads
`submission_uncertain`, and the writer's result carries `input_unproven`. It runs nothing
meanwhile, so it holds neither a stop nor its checkouts' compatibility lease. Only the lead
sends it again, and doing so may run it twice: a resume sends it ahead of its own text
(`run/input_resent`: `phase`, `input_id`, `resent`, the input it repeats,
`reason: resumed_unproven` and its `proof`), and `message/retry` sends a delivery. A
resume settles its run's inputs the same way before it proceeds, and maintenance settles
them in the background, at most once each 2 s per run (an uncertain delivery, and a
workflow's phase input as it advances the workflow), so neither a stop nor a cancel
waits on one indefinitely. A delivery `unproven` (its record's `unproven` says why) is
delivered again only by `message/retry`; a coordinator notice is owed again instead
(`pending`): it starts no turn, and its id tells a second copy apart. A writer ended
this way is replaced, without continuing its thread, while work is owed to it or by its
run's next resume. Each cancel generation reports `run/cancelled` once, with its
`generation`. A workflow past its deadline is cancelled by maintenance, which checks the
deadline again on the run as it is when the cancel begins: a resume renews it.

| Lead MCP tool | Operations |
|---|---|
| `coordinator_claims` | list, acquire for this Claude session, release request, lead-authorized handback |
| `coordinator_send` | durable send to run/unit/actor/lead; explicit none/queue/start wake policy |
| `coordinator_inbox` | cursor read; acknowledge only explicitly supplied IDs |
| `coordinator_unit` | status, object diff, UNKNOWN, reconcile, restore, narrow, reopen, recover, abandon |
| `coordinator_seal` | freeze exact receipt-backed candidate and explicit validation evidence |
| `coordinator_publish` | exact review binding plus all seven explicit commit metadata fields |

An optional `session_id` must be the real Claude host session identity. The new
PreToolUse hook injects it into this plugin's MCP inputs using `updatedInput` with
the entire original input preserved. Example qualified tool name:
`mcp__plugin_software-workflows_codex-oracle__abraham`. The hook accepts the
plugin/server-qualified names ending in the twelve coordinator-aware tool names;
it sets no permission decision and reads no transcript. The mapping from checkout
filesystem identity and host session to lead actor/unit is durable. Every
coordinator-aware server result includes one unread-lead notice when needed;
reading status or inbox never acknowledges messages.

Writer processes use a registration/configuration overlay that hides these six
tools, bound `code_review`, Abraham and run controls from inherited instances of
this MCP server. The unrelated advisory tools and user MCP servers remain.
Admission checks the actual native catalog. Unsupported override addressing or
an unobservable catalog refuses admission instead of exposing lead authority.

`code_review(sealed_unit=..., cwd=...)` reviews the complete immutable object diff
with its seven-field binding. Its durable review job precedes dispatch; full raw
completion metadata, thread identity and answer digest are recorded before display
truncation. Only the completed review's own unambiguous opening `**Verdict**: Ship
it` can grant passing evidence. Quoted/fenced verdicts, conflicting verdicts,
partial/failed answers and negative reviews remain history without passing
evidence. Collection after MCP restart can finish an already dispatched job
without another model call. Ordinary reviews and session continuation remain.

Pre-ref publication recovery can abandon a journal only after proving under a
Git transaction that its commit was not installed; it invalidates the review and
permits reopening/resealing. A journal whose commit is installed finishes only its
recorded index/metadata transition, retaining later UNKNOWN. Ref rollback and
working-file rewrites are never recovery steps. Abandoned journals no longer
block creation of unrelated units. Checkout/gitdir/common-repository identity is
revalidated before object creation and both before and inside ref transactions.


### Admission deadlines and settlement accounting (2.4.7)

A lead file Pre has one budget for the entire decision: recovery waits, lease and
claim work, checkout-lock waits, pre-images, intent calculation and executor
queueing all count. The deadline is two seconds after the hook starts, rather
than a new budget beginning at daemon receipt. On macOS the daemon obtains the
Unix peer PID and kernel process birth. Birth is used only for a Python
interpreter running one absolute script argument (optionally preceded by `-B`):
its resolved path must be the plugin's `hooks/coordinator_lead.py`, with the
software-workflows manifest, coordinator client and matching `python3` entry in
`hooks/hooks.json` beside it. This covers installed 2.4.4–2.4.6 and checkout hooks.
`-c`, `-m`, stdin, wrappers and filename data arguments do not qualify. This
lookup starts no process and runs only for a lead-hook Pre request.

Each new attempt records `deadline_source` (`peer birth`, `supplied` or `receipt`)
and `deadline_peer`: the validated script and birth, or the reason the peer was
not a hook or could not be inspected. A lookup/validation failure also records
its exception type. With a recognized hook, the deadline is the minimum of
birth plus two seconds and a finite supplied absolute deadline. Other clients
use the minimum of receipt plus two seconds and a finite supplied deadline;
their process age is irrelevant. Without a supplied deadline, the applicable
two-second budget is used; on ties, birth/receipt takes precedence. The new
Python 3.9 hook supplies its deadline. Non-macOS peers and failed lookups use the
same recorded request-relative fallback. Installed hooks still use their three-second Client
timeout. Two seconds leaves 0.5 seconds to the 2.5-second acceptance limit and
another 0.5 seconds to the client timeout. Round 16's calibrated 1.15-second
pre-image read could not fit the former one-second budget even with no competing
owner. The daemon returns a non-operational
`CLAIM_CONFLICT` with nonempty conflicts when preparation cannot finish. Its
message includes `LEAD_ADMISSION_BUSY`, retry guidance and an explicitly estimated
retry interval derived from the active scan's measured service time, queued
cohorts or the oldest lock waiter; details name paths, operation/actor/kind and
phase. While readiness is pending, the blocker is coordinator startup recovery,
with its generation, completed/total writers and recovery retry estimate (or
elapsed startup time before that plan exists), rather than the admission itself. The message tells the model that the denied tool did not run and to retry
with a new tool-use identity once the blocker clears. Released
2.4.4/2.4.5 denial logic understands this envelope. Hooks' own timeout behavior and
Failure blocking are unchanged.

The timeout is an event-loop callback, not a real-time scheduler. It runs only
when the loop can run it. Peer-birth accounting includes receipt delay when
lookup succeeds, but cannot bound arbitrary OS descheduling or a blocked loop;
recorded fallback loses that pre-receipt accounting. End-to-end latency claims
are measurements under the reported load, not a universal construction guarantee.
After preparation completes, the waiting dispatch checks the deadline again
before returning an allow. A preparation whose completion is observed too late
returns the existing busy denial and retains its durable ownership for settlement.

Native socket reading, durable receipt and application have separate owners.
The socket reader resolves a response as soon as it decodes that frame; it does
not wait for earlier notifications to be applied. Notifications and server
requests enter one ordered journal queue per connection. A response captures
the preceding notification ordinal. After the wire response arrives,
`Connection.call()` waits for that fixed ordinal to commit, outside its
60-second active-reader response timeout. The monotonic budget excludes actual
reader waits for journal capacity, oversized-message handoff and native service.
Overlapping pauses count once; a call sent during a pause receives its full
active-time budget. Socket reads count as active time. Pause/resume rearms one
alarm per waiting call without polling or resending; explicit timeout overrides
(including the 10-second recovery answer probe) use the same clock. Status RPC
counters expose response-wait wall, paused and active duration. This preserves the ordered durable-receipt
barrier used by recovery, history and input reconciliation without waiting for
application or for newer notifications. An unanswered request still times out.

The journal queue budgets **512 KiB of wire JSON**, including a staged batch
until its commit. When full, the reader pauses with one additional decoded
message; WebSocket assembly and transport buffers are separate. The named
threat is a producer indefinitely outrunning durable receipt. The pre-change
round-18 burst at starting host load **21.65** measured **800 notifications and
244,416 ledger JSON bytes per listener**, with maximum serialized burst
notification **584 bytes**. Final reader measurements independently observed
the same **584-byte** maximum incoming notification. The budget holds more than
two measured listener cohorts by the stored-JSON measurement. It is not a
message-size rejection limit: a message larger than the budget waits for earlier
input to drain, is handed off alone, and commits before reading resumes.
Ordinary retirement also hands the one decoded capacity waiter to the journal;
that final handoff can exceed the budget by that one message.

Each journal transaction contains at most **64 notifications** or **64 KiB**,
except that a larger single notification forms its own batch. A measured turn
has **40 notifications**, so 64 fits one turn plus 24 further notifications;
64 KiB exceeds 64 times the measured 584-byte maximum. Collection has a **2 ms**
budget and never waits to fill a batch. The same 2 ms cooperative scheduling
service is shared by readers, journal owners, application and growing log tails.
A reader's granted step may decode consecutive messages for up to 2 ms before
requesting another FIFO ticket. This preserves receipt batching while bounding
the work before another owner can run; capacity waits still stop that reader.
Each waiting task has one FIFO ticket and a private future. With **n live tickets
ahead, service arrives within n+1 grants**, including its own. A grant permits an
actual step even if a preceding synchronous step overran its slice. A backlogged
owner rejoins behind existing waiters; cheap consecutive steps share remaining
slice time only when no waiter is owed service. Demand-driven loop callbacks
grant service without holding a lock across application/RPC awaits. Cancelled
tickets are skipped, and shutdown cancels the remaining callback after joining
native owners. There is no idle slice timer. The collection budget is not a wall-clock promise for queued age
or COMMIT duration: synchronous SQLite, decoding, log reads and host
descheduling cannot be preempted. Status reports their measured maxima.
Log tails still poll every 50 ms, but take a native slice only after the log
grows. Explicit settlement reads still cover the full required log suffix.

Receipt rows and server-request obligations commit in the same transaction,
with WAL and `synchronous=FULL` unchanged. Only a successful COMMIT publishes
the application high-water mark. The committed backlog lives in the indexed
ledger, with a coalesced wakeup and one decoded application row per listener.
Its live dispatch cursor does not replace durable `native_applied` progress,
failed/pending marks or separately owned request handlers. Applied progress
retains its 256-notification/100-ms flush policy. A failed transaction body or
COMMIT explicitly rolls back an active transaction and preserves the original
error. An intake-owner failure records `native/journal_failure` once, publishes
daemon stop, and wakes consumers with `NATIVE_INTAKE_STOPPED` (`IntakeStopped`, a
`Retired`/`CancelledError` subclass). It takes precedence over response, committed
barrier, closed-connection and history-validity fast paths. Application keeps its
previous prefix; requests, input submissions and interrupted patch verification
retain durable ownership. Recovery dependency events cannot authorize snapshots,
publications, workflow advancement, message retry or recovery completion after
stop. An admitted RPC that receives this outcome sends an operational
`NATIVE_INTAKE_STOPPED` error with `retryable: true`, including an interrupted
`daemon/stop`. Its idempotency key remains pending; the envelope does not authorize
repeating an effect or cache a completed business outcome. A retry reconciles
that pending ownership on the serving daemon.
An ordinary connection retirement or task defect during recovery is different:
the finished scope leaves `recovering`, its dependents reconcile their own
evidence, defects are recorded, and message retry and `recovery/complete` proceed.
Only local intake stop or the daemon's stop fence withholds this work.
Narrowly identified legacy local-journal startup incidents become retryable
without deleting their UNKNOWNs or failure records. The daemon stops for recovery;
if the diagnostic cannot be stored, it is
written to stderr and the stop still proceeds. Uncommitted rows are never
published for application.

Local capacity failures during native application, request handling or log
processing use that same stop fence before recording an evidence failure.
This includes ENOSPC, EDQUOT, EFBIG, descriptor/memory exhaustion and SQLite
FULL/NOMEM. SQLite IOERR also means the local ledger is unavailable: APFS can
lose the underlying ENOSPC classification. It stops consumption without claiming
corrupt native evidence. Constraint errors and verified content mismatches keep
their existing reconciliation rules.

Admission's two guarded
preparation phases read heads only for the requested paths; they still compare
all overlapping exclusion versions, including case-insensitive names and custody
epochs. Pre-image reading, blob validation and intent calculation share one
admission-executor submission. The later locked validation and durable grant
remain separate. These changes reduce the work before a decision; they do not
relax stale-base, live-owner or abandonment checks. The deadline is not a
hard-real-time promise against an OS stall, an individual oversized native
payload, or an uninterruptible kernel operation.

`lead_admission` durably records identity, generation, deadline and state. Live
phase progress stays in memory; its last label is saved with the existing state
transition. Round-9 SQL profiling found 4,680 admission-record writes for 180
lead calls (4,500 standalone transactions), so intermediate diagnostic labels
no longer each force a durable commit. Busy replies still use the live phase,
including the startup-recovery blocker.
The RPC does not cancel a running executor job. A stop-fenced worker checks the
attempt after waits and before observations, quarantine or grant, discarding
stale results. Post, Failure and batch channels save terminal evidence before
waiting for preparation I/O. An unanswered grant retains its exclusion; an older
hook that may execute after a timed-out request retains its reservation until
terminal evidence. Calls without a usable host birth or transcript path retain
the existing 120-second legacy fallback: an admission id cannot replace an end
channel. A busy reply alone is not evidence that the host call ended. Cleanup releases only the
attempt's own reservations. Earlier-generation calls retain crash-gap quarantine.

For native command completions, `operation/settlement` correlates a monotonic
receipt-to-custody-release measurement with the operation and actor. It records
journal receipt/application, queue time, actor-lock wait, native terminal inventory,
process inspection, checkout-lock wait/hold, executor queue/run/return, snapshot
passes, Git calls, hashing, fsync, SQL/commit, notification delivery and release.
`phases` is a disjoint wall-time account, including residual `unattributed`;
`accounted_seconds` equals `seconds`. `inclusive` counters overlap deliberately:
for example hashing and Git run inside a snapshot, so adding them to snapshot time
would double-count. SQLite autocommit is timed as combined SQL/commit because the
Python API does not expose its internal split. The clock begins at daemon receipt,
not the native process's earlier emission. History-only recovered completions do
not invent a missing live receipt time.

Both status forms expose `event_loop_lag` (100 ms sampling, last/maximum/sample
count) and `checkout_locks` (holder identity, current phase, oldest waiter and
ages). Inactive tracing uses the raw SQLite connection and ordinary executor
path; detailed SQL, JSON and executor timing is collected only for an active
settlement. Status caches derived writer summaries until relevant records change.
The most recent 100 timestamped heartbeats stay in memory; ordinary beats do not
write a log or re-arm a watchdog. One supervised watchdog process at a time
reads shared memory independently of the daemon's GIL. After a 0.75-second missed heartbeat it
records the estimated wall-clock stall start in `loop-stalls.log` and asks the
owner to send the worker its diagnostic signal, which the worker only counts
(`diagnostic_signals` in status, a `watchdog/diagnostic_signal` event). Since
2.4.9 no thread stacks are dumped: 2.4.8's faulthandler signal-handler dump walked
every thread's frames while the loop was inside SQLite, and on 10-27 that walk
crashed the live worker (SIGSEGV). Status keeps `dumps: 0` and
`dump_integrity: no_dump`. A stopped helper or stalled filesystem can lose
diagnostics but never blocks the worker. The helper's output queue is bounded to one 16 MiB segment; overflow
drops new output, counts lost bytes in status and logs the loss when output resumes.
It never signals the worker itself: it asks the owner (`owner/diagnostic`), which
signals only the worker identity it recorded.
Status names the watchdog PID, `loop-stalls.log` path, threshold and diagnostic
byte limit. It also reports the supervisor's running, restarting or stopped
state and reason. Supervision ends only when the daemon's final stop event is
set; the reversible closing fence used by drain does not end it. Every exit from
`serve()`, including SIGINT, task cancellation, an escaping exception and startup
failure after binding, sets that final fence before closing or waiting for the
server. There is no enclosing server context whose exit could wait first.
Shutdown stops accepting and immediately aborts incomplete-request connections.
Already admitted handlers get the existing one-second drain budget to finish
and flush replies, including an accepted stop reply. The threat is a client that
never finishes its request or never reads its response: after that grace all
remaining tracked stream transports are aborted and handlers cancelled **and
joined**. This uses transport `abort()`, available throughout the accepted Python
3.11+ range, rather than the server client-close APIs added in Python 3.13.
Every owned executor call is joined through its actual executor Future: cancelling
an asyncio waiter cannot cancel a running filesystem thread. Parallel writes and
reads consume every sibling's outcome before raising an error or cancellation;
quarantine cannot read a partially installed image while another worker is still
writing it. Acquisition of publication guards and rollout streams also retains
the acquired resource for cleanup if cancellation arrives before the await returns.
New accept callbacks already queued when the listener closed see the final fence
and cannot dispatch. While `serve()` owns the daemon, every SIGINT requests the
same irreversible stop, including second and later interrupts. The raw signal
handler only schedules that whole transition with `call_soon_threadsafe`, which
also wakes the selector; it never interrupts Event waiter registration with a
direct `set()`. Cleanup joins tolerate cancellation of their own waiters,
including Runner-wide cancellation, and restore the previous SIGINT handler only
after the owned shutdown returns. A delivered cancellation takes precedence over
a joined worker's failure, with that failure chained as its cause; cancelled patch
verification also journals the worker error before completing quarantine.
Shutdown retries cancellation of unfinished consumers every one-second drain
interval. Quarantine compensation for a direct edit, explicit completion or an
independent lead-command failure is joined too, so a retry cannot interrupt it
before its observation begins. A lead command cancelled by local retirement or
daemon stop retains its prepared operation and custody for startup reconciliation;
it does not initiate a new full-checkout quarantine scan during shutdown.
Already running filesystem workers remain joined, and an independent worker
failure chained to cancellation still takes the compensation path.
Idempotent resource retirement has its own protected owner: repeated caller
cancellation cannot interrupt escalation, reaping or allocation cleanup. A Runner
cancellation directed at that retirement task retries the unfinished retirement
with the same resource identities only when that task received a new cancellation.
A cancellation returned by a dependency is a visible retirement error, not an
instruction to restart indefinitely. Native and RPC stream retirement shields
the protocol's shared close future itself (the CPython 3.11–3.15 waiter), so a
Runner-wide task cancellation cannot permanently cancel that completion.
Ordinary retirement errors still propagate.
This is a retry cadence, not a deadline that abandons executor work.
Only after the RPC joins does shutdown await server closure and fence native intake.
It cancels and joins background consumers, including owners created during
cancellation cleanup, while their native connections remain open. The watchdog
retires as its supervisor ends; scan producers are joined next. Only then does
shutdown abort native stream connections and join their receivers and journal owners, join admission
workers, and release the runtime socket, upgrade and checkout locks and database. `worker()` releases its lock as its context exits. The
`daemon/rpc_shutdown` event records handler methods, cancellation count, grace
and elapsed time. Existing client/hook error handling is unchanged; a lost reply
does not release an admitted call's durable exclusion. Forced termination keeps
the native app-server processes: journaled but unapplied rows remain pending, and
native history re-derives notifications stopped before receipt on the next start.
A racing attach closes its receiver instead of reopening intake.
Every local native close, graceful or aborting, is intentional and cancels any
remaining pending native RPC futures. This covers listener detach/replacement,
accepted drain, wedged-listener termination, temporary history readers, admission
rollback and forced shutdown. Senders interrupted by that close also receive
cancellation, rather than a transport error. Listener detach marks that listener as retiring before closing intake and joining
consumers. While service continues, it applies any durable journal suffix, then
separately verifies every eligible outstanding single-checkout or composite patch
of that listener. Receipt settlement runs even when the journal cursor already
passed the completion: that cursor proves durable evidence application, not a
finished receipt read. Stop/replacement judges a patch unfinished only after this
pass. Global shutdown leaves the evidence for restart.
Neither path waits for a queue whose consumer has ended. A draining stop counts
received-but-uncommitted notifications (including a capacity waiter and staged
batch), committed application backlog, active application and separately running
server-request handlers. Ordinary detach joins the journal owner before replaying
the durable suffix. Forced shutdown joins all receipt owners before SQLite closes;
uncommitted input retains crash semantics and committed unapplied evidence remains
replayable. Close and failure wake capacity and durability waiters. Transport-gap
markers use the same journal order as earlier accepted notifications.
`NOT_QUIESCENT` names the current
listener, sequence and method when a notification is still being applied.

Listener retirement emits monotonic phase timings in the ledger.
Each listener/detached event separates stream close, consumer joins, journal
replay and receipt settlement. Each listener/stopped event separates detach,
process exit and final log/execution settlement; listener/exit retains the
TERM/CONT-to-exit polling measurement and whether escalation killed the process.
The draining flag distinguishes stops made during a draining request. These
spans permit per-listener and between-listener attribution without changing the
existing sequential drain or process-exit policy.

Graceful native close appends its complete six-byte masked close frame and closes
the writer to flush it. A loop timer, independent of the close owner's joins,
aborts a stream still flushing after **one second**. The named threat is a live
peer that has stopped reading. In round 12, 100 real Codex 0.163.0 peer close
round trips had p50 **0.209 ms**, p99 **0.333 ms**, max **0.431 ms**; 100 local
flush/closes had max **0.218 ms**. One second provides over 2,300 times the
observed peer maximum and room for the measured sub-second host stalls. This
fixed grace is not configurable and imposes no history/recovery limit. Expiry
records `native/close_escalated`, naming the listener, connection and actual wait.
An explicit abort can also interrupt an already-running graceful close. Each
connection has one serialized completion and escalation state: an abort or a
completed close never arms another grace timer. The timer exists only while a
graceful flush is waiting, and expiry is recorded at most once. The close timer
receiver and journal owner end before SQLite is retired.

Intentional retirement does not turn pending calls into `NATIVE_DISCONNECTED`: an unapplied journal obligation
stays replayable, without an evidence-failure UNKNOWN, listener failure or quarantine
created solely by the stop. Pure cancellation of patch verification during global shutdown or retirement of
that operation's own listener leaves its evidence replayable. The listener
retirement marker survives removal from the connection map and is cleared only
when that listener is attached again. Log-based negative settlement and
executor-exit unfinished-patch judgement wait for the listener's unapplied
journal suffix. A patch with no completion evidence after replay retains the
existing crash-gap quarantine behavior. A joined verification worker that actually failed still
records its failure and completes compensation. Genuine transport failure while
serving retains the existing evidence-failure and transport-gap handling.
A supervisor restart callback sees the fence and cannot restart during teardown.

Calibration listeners used by admission and project checks are owned children,
unlike production writer app-servers. Their retirement aborts and joins the native
receiver, sends TERM, allows the existing ten-second child exit grace, then sends
KILL if necessary, reaps, joins stderr and removes the runtime allocation.
Catalog and policy-probe children retain their existing five-second waits and
KILL/reap fallback. Provider and file cleanup joins the actual executor work.
No cancellation retry permits these owners to release resources early. The
ten-second calibration grace plus the one-second RPC grace explains the roughly
11.5-second forced-stop measurement; it is not a bound on an uninterruptible kernel
operation or a child the kernel cannot reap.

On this host's CPython 3.15, the installed `children.ExitWatcher` only announces
exit from its kqueue thread. Its loop callback performs `waitpid` and immediately
calls the transport's `_process_exited`, which sets both transport and Popen
return codes in that same loop step. This covers calibration/probe app-servers,
catalog and policy children, schema/version children and lead-command children.
The Unix transport signals with `os.kill`, not `Popen.send_signal`. The watchdog
is a direct Popen child: its synchronous `poll()` reaps and sets its return code
together, and retirement retains ownership until that happens. Reconnected writer
app-servers instead require their recorded PID and birth identity before signalling.
These watcher details were inspected on the installed interpreter; the Python
3.11 syntax and API-surface checks do not establish another host's watcher behavior.

The round-11 read-only completed-turn discriminator held the real native terminal
inventory response across SIGINT. The prior shutdown recorded
`native/reconcile_failure NATIVE_DISCONNECTED` and a permanent checkout-wide UNKNOWN.
The changed shutdown exited in 0.074 s without either, replayed the journal after
restart, and verified a subsequent lead edit. Killing the native peer while the
daemon continued serving still produced the expected failure records. In the
calibration-child discriminator, the old retirement left the stopped real child and
its allocation after daemon exit; the changed retirement reached KILL, reaped it
and removed the allocation before exit. The negative control restored only the
previous close method at import after correcting the fixture to install the
production child watcher; it was not a second full-tree baseline run.

Round-10 real-native probes forced a patch-verification read to fail after
cancellation, a SIGINT inside Event waiter registration, and a native completion
at database retirement. The prior tree respectively remained alive beyond six
seconds, lost the stop wakeup, and raised SQLite's closed-connection error. The
fixed patch case exited in 1.268 seconds including a 1.2-second held read; its
OSError was journaled before quarantine. The waiter-window case exited in
0.097 seconds. With the selector actually waiting for 60.001 seconds, SIGINT
woke it and exit took 0.072 seconds. Native receivers were done before database
retirement, no callback reached the closed ledger, and restart recovered the
completion. Missing native Post evidence still produces the ordinary conservative
quarantine rather than an invented exact receipt. A consumer deliberately
swallowing its first cancellation was stopped by the retry; a delayed lead-command
quarantine also completed before resources closed under three SIGINTs.

Round-9 import-injected real-daemon probes paused a direct `lead-edit` or
`unknown/restore` worker after truncation, then interrupted shutdown. The prior
tree quarantined and closed resources before the final write. The joined versions
finished the write, observed its final bytes, and then closed resources, in
3.05–3.08 seconds with a three-second fixture pause. Two-checkout sibling failure
also waited for the remaining writer before quarantine. Three SIGINTs during the
grace and joins preserved the same ordering. Restart verified the recorded final
image and SQLite integrity. With `Server.abort_clients` and `close_clients`
removed at import (the Python 3.12 API surface), a partial-client worker exited
cleanly in 0.163 seconds and released its worker lock and watchdog.
A live `lead-command` call owns its process outcome until its settlement or
compensation ends. Settlement serializes on the custody group's identity. It
reloads every member and snapshots the operation/member revisions before process
inventory. A changed revision discards that inventory without saving it. Release
requires an inventory begun after durable completion and another revision check
after checkout observation, with all releases committed together. Maintenance and explicit `custody/reconcile` may inventory
that process, but cannot certify a missing exit status while that call can still
record the real result. The live-owner registry is generation-local; after
restart, or after an owner ends without recording a result, explicit resolution
retains the ordinary missing-exit-status crash-gap UNKNOWN. Process absence
never invents a successful exit receipt. The six-second write-probe ceiling allows its three-second fixture pause,
the one-second RPC grace, watchdog retirement and scheduling margin. It is a
measurement ceiling, not a production cap that abandons owned I/O. A kernel-stalled filesystem worker or unreapable child
can still extend safe cleanup, as below. No client read, EOF or reply flush is
left as an unbounded shutdown dependency. Unexpected
heartbeat-coroutine exits and supervisor-task exits are journaled and restarted
with the same one-to-thirty-second exponential backoff used for helper failures.
A failed helper retirement remains visible with its PID and error; subsequent
attempts keep reaping that same child. No replacement starts while retirement
is incomplete: an unreaped helper may still signal, so a no-op handler (`absorb`)
takes SIGUSR2 and its descriptors remain owned. A successful retry then disposes
them, and `absorb` keeps taking SIGUSR2 until a replacement registers its handler.
Since 2.4.9 the worker never returns SIGUSR2 to SIG_DFL, whose action ends the
process: the owner can still deliver a request that a retired helper queued. Nor
does it set SIG_IGN: every process the worker starts would keep an ignored signal
across exec, while a caught one is back at SIG_DFL there.
Final shutdown also retains that obligation. Each reap attempt is bounded, but
a kernel that indefinitely refuses to reap a killed child can delay final exit;
abandoning a potentially signalling child is not safe. Shutdown posts an end marker and waits one second. A helper
that is still running is then ended by the owner through its recorded family (`owned.Process.end()`). A helper the
worker started itself is killed as its unreaped direct child and given one second to be reaped; that relationship
proves ownership without another process lookup. One shared close
task completes cleanup even when its callers cancel. If that close task itself
is cancelled or fails, the next close call starts a fresh retirement attempt
while retaining the same child, descriptors and absorbed SIGUSR2. Signal
registration is removed before any descriptor is closed; signals are absorbed
until the helper is gone. Helper exit checks use nonblocking `Popen.poll()`.
Failure to start restores the previous signal disposition, except that SIG_DFL and SIG_IGN become `absorb`. Transient parent
lookup failures are logged, exposed in status and journaled when the loop resumes;
monitoring retries with exponential backoff from one to thirty seconds. The
helper continues draining its diagnostic pipe during that backoff. An unexpected
helper exit is journaled, reaped, and replaced with the same backoff. There is no
retry-count limit. The thirty-second ceiling limits sustained process-spawn or
lookup failure to two attempts per minute, with the next retry visible in status.
To prevent weeks of recurring stalls filling the disk, diagnostics retain two
16 MiB segments (32 MiB total). The segment size allows faulthandler's bounded
100-thread, 100-frame dump with its bounded strings; inherited oversized
development logs retain only their newest segment-sized tail. Rotation and
diagnostic writes happen in the watchdog, never on the daemon loop. Inclusive spans and queued RPC time must
not be mistaken for a single uninterrupted loop stall.

Blob persistence hashes the source before publication. Existing content creates
no temporary file and performs no fsync; reuse requires a validated blob with
unchanged inode, size and change times in this Store generation. Every `read_blob`
still verifies the hash. New publication uses a temporary file, file fsync,
atomic rename and directory fsync; concurrent publishers cannot reference the
renamed file before its directory sync. Full observations read ledger heads in
one query. Unexpected startup exceptions include their type and at most 1,024
message characters in `DAEMON_STARTUP`.


The October 2026 disposable incident fixture used the supplied 3,284-file shape
(90.46 MB of seed-7 synthetic bytes), 4,733 heads, 701,847 retained native rows
(2.66 GB JSON; about 2.914 GB SQLite), four real native commands and 23 other
fixture writers. Installed 2.4.6 timed out all four lead Pre calls at 3.109–3.118 s:
all four edits ran unadmitted and each path acquired one UNKNOWN. This reproduced
the timeout/unadmitted-write pattern, but not the incident's three UNKNOWNs per
file. The 1.0 s budget returned all four busy decisions at 1.792–1.796 s with the
installed hook and 1.839 s with the new hook; neither run executed an unadmitted
edit or recorded a lead-path UNKNOWN. These are four-call samples, not a long-run
latency guarantee. The earlier 1.75 s budget reached 2.840 s under this load and
failed the chosen 2.75 s margin; its result is retained in the run log.

Before the scan-producer change, the eight measured settlements took
16.028–18.240 s. Checkout-lock waiting accounted for 13.000–15.311 s; individual
snapshot passes took 0.524–0.627 s. Those round-1 measurements motivated the
round-2 structure below. The event's disjoint account includes every measured
interval plus its explicit residual.
Steady snapshots of the same shape took 0.419–0.443 s with zero fsyncs or temp files
after initial publication, versus 0.964–1.075 s and 3,284 of each on 2.4.6. Stored
blob corruption was detected both by content reads and by attempted reuse.

The reproduction does not model the full live 6.46 GB database, arbitrary filesystem
stalls, or a frozen host scheduler. It uses read-only existing Git metadata and an
external synthetic work tree/index, preserving the no-Git-writes run boundary.
The incident fixture's journal is retained load; separate combined recovery runs
exercise unapplied journal replay and long native histories. NotebookEdit's normal
uncalibrated-adapter behavior is unchanged; its deadline/busy denial was measured,
not exact notebook attribution. A Post may outlast its Client timeout while byte
settlement continues, but its terminal evidence is recorded before preparation
I/O. Lost-reply probes intentionally produce a connection-reset log while keeping
the durable grant and verifying its later Post.

Direct-editor lifetime probes cover a live owner, a child reparented to PID 1,
an orphan whose intermediate parent exited before prepare, matching and
mismatching images, and daemon SIGKILL with the owner still alive. Initial
snapshot membership alone never proves a process is outside the editor's
descendants. Retained original-parent chains and XNU's monotonically allocated
process identities distinguish proven unrelated processes; unknown intermediate
ancestry remains a conservative hold, including in the initial snapshot. Status
and stop refusals name these ambiguous identities. An unrelated process with
unprovable ancestry can therefore delay automatic settlement until it exits.
The pre-prepare orphan probe first reproduced premature release, then verified
that the final implementation holds until that orphan exits.
Executor-owned operations are not imported as legacy hook calls on restart.
No usable pid/birth or surviving kernel lineage means direct prepare refuses;
daemon-owned lead-edit remains the alternative. These process probes ran on
macOS; direct ownership is not claimed available on the Linux backend.


The round-2 final tree was measured in three interleaved pairs against installed
2.4.6, each with 27 writers, 1.998 GB synthetic native histories and 2.66 GB
unapplied journal JSON. First recovery took 61.096, 57.127 and 54.480 seconds on
the changed tree, versus 58.625, 55.759 and 62.702 seconds on the installed
baseline. The changed-tree median was 57.127 seconds versus 58.625 seconds;
these samples remove the earlier round-1 regression without establishing
universal statistical equivalence. Hard-kill restart took 1.236–1.330 seconds
and decoded 40,914–42,727 history bytes. Every changing lead Write verified;
there were no path UNKNOWNs, quarantines or RPC errors.

The supplied sampled profile identified status materialization and store wrappers
as hot paths. Round 2 removed the per-statement SQLite proxy, uses cheap inactive
telemetry paths, and caches derived status summaries until relevant records
change. Full-status p50 was 3.816–4.565 ms on the changed tree versus
7.537–7.706 ms on 2.4.6 at the same polling workload. Lead Pre p99/max across
the three changed-tree runs were 0.242/0.261, 0.265/0.530 and 0.182/0.377 seconds.
The earlier round-1 result, 66.420 versus 60.065 seconds, remains in the run log
as the regression that prompted this change.


### Checkout scan cohorts and atomic observation

Each checkout has one scan producer. Every reconcile caller persists an
observation obligation naming its generation, checkout/path scope, causal point,
policy, reason and completion action. Custody settlement becomes eligible only
after the existing native terminal inventory and process checks succeed. The
producer freezes all currently eligible requests before submitting its reader;
arrivals during that scan enter the next cohort. It still hashes every file.
Git enumeration, reads, hashing and durable blob publication run outside the
checkout commit lock, with immutable scan images shared by the frozen cohort.
Each application receives only its own scoped images and unreadable results.
Explicitly requested paths join the read even when both inventory and ledger
heads omit them. An absent result is still an observation: its head and exclusion
version are validated before application, and a concurrent creation re-reads
only that path.
Only a whole-checkout application receives the listed inventory that authorizes
whole-checkout outside-ledger classification and release. Revalidation and
re-reads stay within that same scope. A producer setup or reader failure resolves
every frozen future exceptionally; the next request can start a new producer.

Shared producers, detached completion work, native receivers and preparation
executor jobs clear admission context and inherited lock holder/owner context.
Cohort traces are passed explicitly. A quarantine observation already required
by durable state can therefore finish without inheriting its creator's expired
admission deadline.

The producer captures head revisions and per-path mutation/exclusion versions
before reading. Exclusion epochs use the same normalized case and path-prefix
space as operation conflicts: case aliases and ancestor/descendant changes
invalidate each other, while siblings do not. Application validates those tokens
under short FIFO commit locks. A path affected by a concurrent lead admission or settlement is re-read
outside the lock; the rest of the checkout is reused. Custody acquisition, release
and scope changes also advance durable epochs in that same custody transaction.
The active-window predicate is shared by the occupancy index, active queries and
epoch triggers: both `held` and `quarantined` remain active; only `released`
ends exclusion. Entry, exit, deletion and active scope changes invalidate the old
and new active scopes. In particular, a successful retry releasing quarantined
lead-command custody invalidates any image sampled during that command. Ordinary
process-metadata saves do not advance these epochs. Startup atomically replaces
the former held-only triggers; unchanged definitions need no schema rewrite.
Finite path custody invalidates its normalized prefix scope. Shared and exclusive
command custody have no finite write set, so they invalidate the whole checkout,
including paths omitted by the previous inventory. A narrow observation re-reads
only its requested affected paths; a whole-checkout observation re-enumerates and
hashes its whole scope after such a transition. This detects A → B → A command
windows even when the final settlement changes no head. The tokens are captured
before every read and checked again under the commit lock; another transition
repeats validation. With no intervening transition, the existing scan needs no re-read. Whole-checkout
revalidation rejoins the same producer and freezes a new cohort; waiting
applications share that new immutable scan instead of each launching a full
executor read. Acknowledgements name the new scan and still check the application
incarnation. Narrow invalidations only re-read the changed requested paths.
Status reports producer scans/bytes and revalidation rereads/reread_bytes.
Whole revalidation is included in both counters, counted once per physical
scan; targeted re-reads are included only in the revalidation counters.
An observation/revalidate event records targeted bytes or refers to its shared
checkout/scan event, where whole-scan bytes are recorded once. Ordinary opaque-command
and turn-end observations with compatible guards and identical path scopes share
one application. One
byte transition records one UNKNOWN, with all contributing obligation ids and
reasons; pre-existing UNKNOWNs remain. Recovery, publication, restoration and
lead completion retain their own guards and cannot inherit another caller's
authorization. Multi-checkout obligations read all participants first, then
validate under canonical checkout-lock order and commit together.

Revisions, UNKNOWNs, observation acknowledgements and eligible custody releases
commit in the same SQLite transaction. A crash before commit leaves the
obligation pending and its custody held; startup rescans it conservatively.
The acknowledgement audit, including each contributing owner, reason, scope
and completion action, is committed in that same transaction. Once its live
callers finish, the committed obligation row is removed by its own observer's
`finally`; maintenance never removes a live member's acknowledgement. UNKNOWNs retain the
audit's obligation identities. Logical owners distinguish actor/turn, operation,
lead-call and restore identities. A retry of the same owner/scope/policy may reuse
its pending row, but receives a new application incarnation and causal point.
Merged acknowledgement requires that exact incarnation to have joined the frozen
scan; an old cohort cannot acknowledge a replacement observer.

Reader, producer, guard and cancellation failures leave a pending obligation;
they never acknowledge a scan or run its completion action. During maintenance,
failed applications without live callers transfer durably to one conservative
repair obligation per participant-checkout set. `observation/repair_handoff`
retains their identities, scopes, owners and actions and explicitly records that
the handoff is not an acknowledgement. Scope unions preserve all requested paths.
New failures do not postpone an already eligible repair. Before reading and again
under the commit lock, a repair rejects overlapping prepared, allowed or running
operations (including native patch parents and parts), executing operations,
lead calls and external-editor owners. Held opaque custody also defers it. The
whole obligation stays pending and retries after five seconds: it is never
acknowledged with the busy paths excluded. A refusal of that live guard does not
quarantine the checkout. A completion without its own hold uses the same guard.
Recovery and quarantine snapshots preserve other current-generation pending
patch, lead-edit and restore paths; their own ended operation still settles, and
earlier-generation crash gaps retain conservative observation. Operation, turn,
lead-call and restore owners still own their completion
actions. Thus an indefinitely busy checkout retains a repair row, rather than one
row per failed turn. Healthy maintenance performs no observation-history query.
Startup uses the partial pending-obligation index, replays
the original pending identity and removes leftover acknowledged rows without
materializing them. A committed acknowledgement is not repeated.
Earlier-generation lead edits retain crash-gap quarantine; intended bytes are
never recovery proof.

Lead admission does not join the scan queue. It reserves its own paths, captures
pre-images in a separate owned executor lane, then validates under the same fair
commit lock. Immediately before inserting an operation, preparation repeats
normalized live-operation overlap, ownership, checkout quarantine, custody,
unit-state and lead-call refusal checks. Exclusion remains valid across both
locked phases. Actor, custody and commit-lock waits all use the remaining decision
budget. Expiry removes queued work immediately. Already-running reads remain
owned until they return, but cannot grant, revise heads or record UNKNOWN.
Post/Failure/batch evidence persists without awaiting that read. Terminal bodies
are compacted after durable handoff; full Write contents and tool responses are
not retained as completion evidence.

All receipt verification also serializes on the canonical operation identity
(the parent identity for a composite), before checkout commit locks. After that
lock is acquired the verifier reloads durable state, and checks it again after
awaited work and under the checkout commit locks. Every terminal operation returns
its existing outcome for unchanged evidence, including quarantined and resolved
operations. quarantine() reloads the canonical parent and is a no-op for terminal
work; it cannot duplicate UNKNOWNs or undo restoration. Genuinely contradictory
completion evidence creates a separate, deduplicated operation_conflict and
UNKNOWN context without changing the original outcome. Matching completed evidence
or intended images return the existing receipt; a second verifier never treats
the revision written by that receipt as REVISION_CHANGED. Composite receipts
retain their existing atomic transaction across canonically ordered checkout
locks. A real image/evidence mismatch retains conservative failure handling.
Recovery settles an eligible completed composite patch before applying the
existing interruption rule to unfinished native groups.

A serving retirement that interrupts turn/start retains its input identity and
durably marks the phase uncertain. Workflow advancement and plain-run maintenance
own ordered input settlement after rebinding, with the existing two-second minimum
between input probes. Resume-mode maintenance selects phase inputs (turn/start),
not queued message deliveries. The replacement-app-server ending branch requires
thread/queue/add; it cannot end a replacement for such a phase input. An uncertain
phase can remain unproven until an explicit lead decision. Entered or queued evidence settles normally; an unproven
input stays submission_uncertain for explicit lead resume. No automatic resend
is introduced. Global termination retains the submitting record for startup
recovery instead.

Explicit and automatic external-editor completion serialize on the same
executor-owner lock and reload the operation there. Existing terminal receipts
are acknowledged without another receipt. A crash-gap quarantine with an active
external owner is different: explicit completion durably observes the owner's
final scope, as automatic exit settlement does, before retiring ownership. Explicit completion persists end
evidence before verification; a failed verification still retires ownership,
with retry after a crash between the outcome and cleanup commits. Process
enumeration repeats after a known ancestor exits, closing the fork-and-exit
window; an unclassifiable vanished process conservatively retains exclusion.

The round-2 incident matrix exercised 1, 4, 13, 27 and 30 real native writers
with both installed 2.4.6 and new lead hooks, using the same anonymous checkout
shape and 2.66 GB retained journal. All changing Writes were either admitted with
exact receipts or denied before execution. Installed-hook maximum decision time
was 1.874 seconds; the new-hook maximum was 2.152 seconds. Both were below
the 2.5-second acceptance threshold. Busy retries with
new identities succeeded; every denied call closed, released its own claims and
allowed draining stop. Eligible-to-release time ranged from 0.573 to
3.378 seconds across this matrix, versus roughly 13–15 seconds of checkout-lock
waiting in round 1. No measured eligible settlement was bypassed by a later
scan: it joined the next frozen cohort, even with up to 16 later obligations
arriving before its acknowledgement. At 27 writers, all four measured command
settlements shared one 90.46 MB scan with 22–23 other observations. The largest
measured observation commit was 52.5 ms; scan-executor queueing peaked at 1.49 ms. These finite samples are not a host-scheduler or arbitrary
filesystem-latency guarantee. Detailed case measurements and exact command
records are in the round-2 implementation report.

The round-2 27-writer streamed burst ran 540 turns and 21,602 native events in
61.272 seconds (21,154 events/minute, 1,586 catch-up triggers/minute). Its 1,164
catch-ups used 7,655 history RPCs and 1,921,514 history bytes, about 6.58 RPCs
and 1,651 bytes per catch-up. Lead Pre p99/max was 0.806/1.220 seconds
(n=167), writer Pre 0.757/0.856 seconds (n=540). All 167 lead Writes verified,
with no warnings, denials, quarantines or lead-path UNKNOWNs. All calls closed.
The recipe checks durable admission before executing a Write and sends the real
PostToolBatch after any answered denial; the incident matrix exercises that
denial path. This remains a one-minute burst, not a day-long soak.

The round-2 C-level watchdog identified the approximately 0.9–1.0 second heartbeat
maxima in the synthetic incident fixture: its synchronous recovery-summary
count over the retained event journal, not a snapshot worker holding the commit
lock. At 27 writers the installed/new-hook cases recorded 0.970/0.903 second
heartbeat maxima and one traceback each. Deferring only that fixture diagnostic
query until after daemon stop reduced the maximum heartbeat delay to 0.387
seconds, with no watchdog dump; the real deferred count still found zero slow
native answers. The largest sampled status round trip in that control was
0.900 seconds, which includes waiting and is not a continuous loop stall.
This attributes the fixture's maximum; it does not diagnose an unrecorded stall
in the user's live coordinator. The complete traces remain in the run directory.

The real installed 2.4.4 hook was also driven through a busy denial and its
batch, which omits that denied call. With neither host identity nor transcript
path, the reservation ended at the existing 120-second legacy limit and draining
stop was accepted. The call was not retroactively labelled as having run.
A four-command frozen cohort recorded one UNKNOWN for one byte transition,
carrying all four custody and obligation identities in the same commit.
SIGKILL immediately before or after an observation commit preserved either a pending
rescan obligation or the original committed acknowledgement and UNKNOWN identity.

### Round-3 scope, lifetime and diagnostic verification

The review probes were run red on the unchanged round-2 tree, then green on
the changed tree, with installed Codex 0.163.0 and disposable state. A narrow
completion sharing a full scan no longer records an unrelated held path as
unreadable UNKNOWN. Concurrent absent-path preparations for case aliases
`Foo`/`foo` now admit exactly one operation. An admission-triggered pre-image
quarantine can outlive the admission deadline without failing an unrelated
settlement or retaining its custody. Explicit completion after a restart
quarantine observes the external editor's final bytes before retiring ownership;
it preserves crash-gap UNKNOWN attribution.

A producer setup failure now gives both frozen observers their exception
promptly (14 ms in the probe); a later observation succeeds. After 1,000
committed observations and 300 guard-failed retries by one owner, only one
pending obligation remained. Its restart query used `observation_pending`,
replayed that original identity once and left no pending rows. The acknowledgement
audit remains in the normal event journal; this compacts observation records,
not the journal's historical audit. Startup busy refusals identify coordinator
startup recovery and use its progress estimate.

The watchdog was measured for 605 seconds of idle service: zero heartbeat log
growth, zero re-arms and no new threads. The old implementation grew its file by
541,356 bytes and re-armed 5,955 times in the same interval. A forced 1.2-second
GIL-held native stall still produced a timestamped all-thread C traceback.
The final probe also seeded two oversized legacy segments and verified trimming
and rotation within 16 MiB per segment / 32 MiB total. These are ten-minute idle
and forced-stall measurements, not a weeks-long soak.

The final incident reruns retained the anonymous 90.46 MB checkout / 4,733 heads,
2.66 GB journal and four changing lead Writes per case:

| Writers / hook | Lead Pre min–max, seconds | Eligible→release min–max, seconds | Full scans / MB read |
| --- | --- | --- | --- |
| 27 / installed 2.4.6 | 1.837–1.841 | 1.800–1.827 | 23 / 2,080.6 |
| 27 / new | 1.688–1.698 | 1.472–1.487 | 23 / 2,080.6 |
| 30 / installed 2.4.6 | 1.773–1.781 | 1.614–2.492 | 25 / 2,261.5 |
| 30 / new | 1.800–1.822 | 1.675–1.697 | 24 / 2,171.0 |

All 16 initial Writes were denied before execution, then admitted and verified
with new call identities after the blocker cleared. Every denied call closed,
its claims were released and draining stop was accepted. No lead-path UNKNOWN
was recorded. Hook process start→daemon receipt took 0.631–0.734 seconds;
receipt→decision took 1.032–1.176 seconds; reply→hook exit took 0.009–0.038
seconds. The decision timing includes dispatch and scheduling outside the
nominal one-second preparation budget. The largest measured observation commit
was 44.0 ms; scan executor delay peaked at 2.75 ms. Full timings and disjoint
settlement accounts are retained in the external round-3 report.

Three fresh, interleaved 27-writer combined recovery pairs (installed 2.4.6 /
changed tree, seconds) were 51.260/50.331, 61.507/69.136 and 56.565/51.754.
The means were 56.444/57.074 seconds. The largest paired slowdown, 7.628 seconds,
was below the installed baseline's 10.248-second spread; the finite samples
do not establish identical performance under arbitrary host load. Changed-tree
hard-kill restarts took 1.262–1.345 seconds, reading 42,727 history bytes and
385,325 total native JSON bytes each. All writers reconciled with zero
quarantines or path UNKNOWNs. Changed-tree lead Pre p99/max per pair was
0.130/0.238, 0.368/0.451 and 0.234/0.276 seconds.

The final streamed burst ran 540 turns and 21,606 native events in 61.401
seconds. Its 1,196 catch-ups used 7,877 history RPCs and 1,982,668 history
bytes (6.59 RPCs / 1,658 bytes per catch-up). Lead Pre p99/max was
0.725/1.306 seconds (197 calls); writer Pre was 0.944/1.017 seconds (540
calls). All 197 lead Writes verified and all calls closed; there were zero
hook warnings, denials, quarantines or lead-path UNKNOWNs. Short history
capacity peaked at two active and 25 queued; no bootstrap slot was active.
This is a one-minute burst; latency remains dependent on the host scheduler
and storage, rather than a hard real-time guarantee.


### Round-4 observation identity and nonblocking diagnostic verification

The review probes ran red on the unchanged round-3 tree, then green on
installed Codex 0.163.0 with disposable state. Both a narrow checkout reconcile
and an unadmitted-call completion shared a full scan while a newer lead Write
created the formerly absent path and verified. The verified head and exact
receipt now remain intact; neither narrow caller records a deletion UNKNOWN.
Validation re-read only the changed path, including a second read when another
exclusion-version change occurred.

The four-observer interleaving reproduced an old frozen scan acknowledging a
replacement request and missing bytes written before that request. Both a
same-owner retry and a later-turn request now acknowledge only their own scan,
started after the new causal point and carrying the matching incarnation.
Three cycles of 40 distinct failed owners each retained one repair row after
handoff and zero after repair. The handoff audit explicitly is not an
acknowledgement; business completion remains with its durable owner.
Pending repairs are bounded per participant-checkout set plus live applications,
not per daemon-lifetime observation count. Their audit events remain retained.

The diagnostic control stopped the helper while requesting a 90-thread,
95-recursive-frame dump. Its calibrated 337,860 bytes exceed the 65,536-byte pipe.
The old daemon blocked; the final daemon kept answering, with 12.3 ms maximum
loop lag, named possible truncation in status, and stopped in 1.729 seconds
while the helper was still stopped. The verified owned helper was killed and
reaped; none remained. Every dump is conservatively labelled possibly truncated
because faulthandler supplies no short-write count. The separately counted
logger losses do not measure bytes dropped by the nonblocking pipe.

On the final source, 605.015 idle seconds produced zero log growth, zero
faulthandler re-arms and zero new thread starts. A forced 1.2-second GIL-held
stall still emitted the wall-timestamped all-thread C dump. Files after rotation
were 16,777,216 and 3,081 bytes, within the 32 MiB diagnostic limit.

The final incident cases again used the 90.46 MB / 4,733-head anonymous checkout,
2.66 GB journal and four real completing commands. All 16 initial changing
Writes were denied before execution, all 16 retries with new call identities
verified, all denied calls closed and released their claims, and every drain
was accepted. There were zero fail-open executions or lead-path UNKNOWNs.

| Writers / hook | Lead Pre min–max, s | Eligible→release min–max, s | Scans / MB read |
| --- | --- | --- | --- |
| 27 / installed | 1.822–1.835 | 1.215–2.001 | 23 / 2080.6 |
| 27 / new | 1.727–1.733 | 1.758–2.579 | 22 / 1990.1 |
| 30 / installed | 1.840–1.845 | 1.641–2.288 | 25 / 2261.5 |
| 30 / new | 1.762–1.778 | 1.702–1.716 | 25 / 2261.5 |

Hook start→daemon receipt took 0.661–0.786 seconds;
receipt→decision 1.011–1.093; reply→exit
0.008–0.018. Dispatch and scheduling are outside
the nominal one-second preparation budget. Maximum installed-hook decision was
1.845 seconds. Maximum observation commit was
44.11 ms; scan executor delay
6.45 ms; oldest measured obligation
4.057 seconds. Full disjoint settlement accounting is in the round-4 report.

Three fresh, interleaved 27-writer first-recovery pairs (installed / tree,
seconds) were 51.680/52.564, 52.639/47.311, 77.711/68.998.
Installed range was 51.680–77.711 seconds (spread 26.031);
tree range 47.311–68.998 (spread 21.687). All writers reconciled
with zero quarantines or lead UNKNOWNs. Tree hard-kill restart took 1.212–1.291 seconds; history / total native JSON bytes per restart were 42,727/385,325, 42,727/381,606, 42,727/382,847.
Tree lead Pre p99/max per pair: 0.227/0.241, 0.172/0.205, 0.288/0.308 seconds.

The final 27×20 streamed burst produced 21,614 native events in
61.377 seconds. Its 1,174 catch-ups used
7,728 RPCs and 1,941,952 history bytes.
Lead Pre p99/max was 0.707/0.861 seconds;
writer Pre 0.654/0.689.
All 195 executed lead Writes verified, all calls closed, and there were
0 warnings, 0 denials and zero lead UNKNOWNs or quarantines.
The 32 accepted small recipes, Q1–Q8, three SIGKILL boundaries and real
four-command settlement cohort all passed after the final production edit.

These finite, shared-host measurements cover three recovery pairs, four incident
cases, a burst and ten idle minutes; they do not establish weeks of uptime or
hard real-time guarantees. The fixture journal is 2.66 GB, not the user's exact
6.46 GB live database. Round 5 replaced the watchdog's fallible shutdown
ownership lookup: an unreaped direct child is already owned, so shutdown waits
one second, kills that child if needed, and allows one second to reap it.
Parent lookups used to authorize diagnostic signals retain their one-second
timeout. Transient lookup failures and unexpected helper exits are reported
and retried with backoff. Existing hook availability and onFailure policy
remain unchanged.

### Round-5 maintenance and watchdog failure verification

The review probes ran red on the round-4 source, then green with installed
Codex 0.163.0 and disposable state. Maintenance now leaves a merged committed
acknowledgement available until its live consumer finishes. The four-observer
probe returned all four results with each completion action called exactly
once; its old-source control returned three NOT_FOUND errors. Repairs defer
their entire scope while another live mutation overlaps it. A real native
apply_patch verified with no UNKNOWN both when receipt processing was held
before repair began and when the patch started between a repair's scan and
commit guard.

Stopped-helper shutdown completed in 1.176 and 1.146 seconds in the timeout
and failed-lookup recipes, with no helper or lookup child left. Shutdown no
longer creates that lookup child. Cancelling the close caller still reaped the
helper and restored the signal disposition in 1.096 seconds. Start failure also
restored the prior SIGUSR2 disposition. A transient parent-lookup timeout was
reported in status and the journal; subsequent stalls still dumped. Killing
the verified owned helper produced durable exit/restart events, and the
replacement produced the third timestamped traceback. No retry-count limit
silently disables diagnostics.

All final-source S/R/Q probes, the repair-lifetime probe, all 32 accepted small
recipes (including the real installed 2.4.4 hook), three SIGKILL boundaries and
the four-real-command settlement cohort passed. The final incident matrix used
the same anonymous 90.46 MB checkout, 4,733 heads and 2.66 GB synthetic journal:

| Writers / hook | Lead Pre min–max, s | Eligible→release min–max, s | Scans / MB read |
| --- | --- | --- | --- |
| 27 / installed 2.4.6 | 1.647–1.658 | 1.128–1.234 | 23 / 2080.6 |
| 27 / new | 1.843–1.875 | 1.705–2.376 | 23 / 2080.6 |
| 30 / installed 2.4.6 | 2.047–2.055 | 2.024–2.845 | 25 / 2261.5 |
| 30 / new | 1.755–1.761 | 1.922–1.959 | 25 / 2261.5 |

All 16 initial changing Writes were denied before execution; all 16 retries
with new call ids verified. Every denied call closed, claims were released,
and each draining stop was accepted. There were no fail-open edits or lead-path
UNKNOWNs. Hook start→receipt took 0.574–0.888 seconds, receipt→decision
1.024–1.139, and reply→exit 0.008–0.043. The largest installed-hook round trip
was 2.055 seconds, below the 2.5-second acceptance bound. Maximum observation
commit was 43.65 ms, scan executor delay 19.77 ms, and oldest measured obligation
4.582 seconds. The external report includes all 16 disjoint settlement accounts,
including explicitly unattributed time, rather than adding overlapping spans.

The final three fresh, interleaved 27-writer first-recovery pairs (installed
2.4.6 / tree, seconds) were 55.969/53.431, 57.656/50.399 and
75.327/74.442. The installed range was 55.969–75.327 seconds; the tree
range was 50.399–74.442. Tree hard-kill restarts took 1.291–1.387 seconds,
with 42,727 history bytes and 385,325–385,326 total native JSON bytes decoded.
Tree lead Pre p99/max per pair was 0.177/0.527, 0.167/0.214 and
0.220/0.227 seconds. All writers reconciled with zero quarantines or path
UNKNOWNs. An earlier, pre-final compatibility rerun had an unexplained paired
slowdown of 16.286 seconds; it remains in the report. The final three pairs
do not explain or erase that outlier.

The final 27×20 burst produced 21,606 native events in 61.303 seconds.
Its 1,149 catch-ups used 7,553 RPCs and 1,892,108 history bytes (6.57 RPCs
and 1,647 bytes per catch-up). Lead Pre p99/max was 0.598/0.666 seconds
(n=202); writer Pre was 0.538/0.546 (n=540). All 202 lead Writes verified
and all calls closed; there were no warnings, denials, quarantines or lead-path
UNKNOWNs. History capacity peaked at two active and 25 queued observations.

The final 605.018-second idle run had zero log growth, zero re-arms and no new
thread starts. Maximum idle heartbeat delay was 23.45 ms. A forced 1.2-second
GIL-held stall still produced the timestamped all-thread C traceback, marked
possibly truncated. Rotated segments were 16,777,216 and 3,081 bytes.
These are finite shared-host measurements, not a weeks-long soak or a
hard real-time guarantee. The fixture journal remains 2.66 GB rather than the
user's 6.46 GB live database; no live coordinator or client history was accessed.
Hook availability and onFailure policy remain unchanged.

### Round-6 custody-window and supervisor verification

Real Codex 0.163.0 commands reproduced an A → B → A custody window while a
repair, an ordinary settlement, and an unadmitted completion were each delayed
after reading B. All three old-source controls committed B after the file
returned to A. The final tree retained the original A head with no UNKNOWN;
the following lead admission succeeded. The narrow cases re-read 17 bytes
each; the full ordinary case used one shared 90,460,321-byte revalidation
scan. Persisted epochs were verified by reopening each disposable database.
Import-time injection forced the scheduling window, not the command's effects.
The red lead admission hit STALE_BASE internally and retried successfully:
its extra stale-pre-image UNKNOWN proves the first failure; no outer refusal
is claimed.

A lease-release failure after the closing fence left RPC service and heartbeat
supervision running. A later forced stall dumped a timestamped C traceback.
An injected failed helper reap remained visible by PID and error, retried the
same owned helper, and replaced it only after successful retirement. Cancelling
the supervisor task itself was also journaled and restarted; the next stall
still dumped. The rollback and failed-reap controls failed on the round-5
tree and passed on final source. All S/R/Q probes, repair lifetime, three SIGKILL boundaries, the
four-command cohort and all 32 small recipes passed again. Small22 now waits
up to 15 seconds for claim retirement after receipt verification; the real
2.4.4 busy-denial/batch case passed in 128.9 seconds.

The final incident matrix retained the anonymous 90.46 MB checkout, 4,733
heads and 2.66 GB synthetic journal. MB below is decimal; whole revalidation
bytes are included in the total scan bytes, not added twice.

| Writers / hook | Lead Pre min–max, s | Native-end→eligible, s | Eligible→release, s | Scans / MB | Shared rereads / MB |
| --- | --- | --- | --- | --- | --- |
| 27 / installed | 1.695–1.704 | 2.309–2.350 | 1.388–1.413 | 26 / 2352.0 | 2 / 180.9 |
| 27 / new | 1.650–1.653 | 2.041–2.110 | 1.650–1.721 | 24 / 2171.0 | 1 / 90.5 |
| 30 / installed | 2.056–2.076 | 1.578–2.497 | 1.283–2.219 | 28 / 2532.9 | 2 / 180.9 |
| 30 / new | 1.990–1.997 | 1.517–2.219 | 1.462–2.154 | 28 / 2532.9 | 2 / 180.9 |

All 16 initial changing Writes were denied before execution; all 16 retries
with new ids verified. Calls closed, claims released, all drains were accepted,
and no fail-open edits or lead-path UNKNOWNs occurred. The installed-hook
maximum was 2.076 seconds; the new-hook maximum was 1.997. Hook start→receipt
took 0.559–0.956 seconds, decision 1.031–1.185 (including scheduler overrun),
and reply→exit 0.009–0.014. Maximum observation commit was 49.78 ms; scan
executor delay 1.50 ms. The oldest measured obligation was 6.297 seconds.
An initial private full-reread implementation exposed 8.5–9.3 seconds
eligible→release under the 27-installed load. Routing full revalidation back
through the shared producer removed that duplication; all final recipes were
rerun after that change. No intervening custody transition needs a custody
re-read; genuine new transitions may require another shared scan.

The final three fresh interleaved first-recovery pairs (installed 2.4.6 /
tree, seconds) were 53.598/49.133, 65.424/63.959, 58.235/56.390.
The installed spread was 53.598–65.424 seconds; the tree spread was
49.133–63.959 seconds. Tree hard-kill restarts took 1.279–1.367 seconds,
with 42727.000–42727.000 history bytes and
384346.000–385325.000 total native JSON bytes decoded.
Tree lead Pre p99/max per pair was 0.184/0.399, 0.255/0.313, 0.218/0.382 seconds.
All writers reconciled, with zero quarantines and path UNKNOWNs.

The final 27×20 streamed burst produced 21605 native events in
61.245 seconds. Its 1157 catch-ups used 7609 RPCs and
1,906,614 history bytes. Lead Pre p99/max was 0.699/0.851 seconds
(n=196); writer Pre was 0.568/0.672 (n=540).
Custody-triggered shared revalidation scans: 0 (0 bytes).
Concurrent lead exclusions caused 112 targeted rereads totaling 1904 bytes.
All executed Writes verified; calls closed with no hook warnings, quarantines
or lead-path UNKNOWNs. The external report retains the complete counters.

The final 605.014-second idle run had zero log growth, no re-arms and no new
thread starts. Maximum idle heartbeat delay was 11.06 ms; the next forced
1.2-second stall produced the timestamped C traceback. Rotated segments were
16,777,216 and 3,083 bytes, marked possibly truncated. These finite shared-host
measurements are not a weeks-long soak or hard real-time guarantee. The
fixture journal is 2.66 GB rather than the live 6.46 GB database. A permanent
kernel failure to reap a killed helper remains an explicit shutdown limit:
ownership and retries are retained rather than abandoning a signal owner.
Hook availability and onFailure policy are unchanged.

### Round-7 quarantined custody and termination verification

The old round-6 source reproduced a real lead command changing A → B → A
after a process-inventory error had quarantined its custody. A checkout
observer read B and waited until that quarantined reservation was released.
The old observer then recorded stale B with an empty custody context. The
final tree instead re-read the one affected 17-byte path, retained the original
A head, recorded no UNKNOWN and admitted the following lead edit. Import-time
injection forced the inventory error and scheduling window, not the command's
filesystem effects. The full state/writer audit is in the external round-7
report: held and quarantined are active; released is inactive. The same
predicate governs queries and OLD/NEW epoch scopes. A reopened old fixture
also verified trigger migration, both sides of active scope changes, deletion,
and no epoch change for metadata-only saves.

SIGINT, main serve-task cancellation and an exception after supervision started
all stayed alive past the old-source six-second observation bound. On final
source they exited in 0.080, 0.232 and 0.127 seconds, with the helper reaped,
SIGUSR2 restored and no owned tasks or checkout locks left. The actual worker
entry-point supplement exited in 0.128, 0.090 and 0.069 seconds and released
worker.lock. Cancellation/exception exit codes remain nonzero; successful
cleanup does not convert them into successful service runs. Cancelling the
retirement task itself used to make every later close raise CancelledError;
the final retry killed/reaped the stopped owned helper and restored SIGUSR2
in 1.206 seconds.

The fixture's six-second exit assertion allows the existing one-second
end-marker wait plus one-second forced reap, with four seconds of scheduling
and resource-cleanup allowance. It is an acceptance-probe bound, not a new
production timeout that discards owned work. Permanent kernel refusal to reap
a killed child, or uninterruptible filesystem/executor work, can still delay
exit. Ownership remains retained and the reap failure visible.

All 63 final focused/regression recipes passed, including T1(a)–(c), all T2,
S/R/Q probes, three SIGKILL boundaries, the four-command cohort, and the 32
small recipes. The real installed 2.4.4 denial/batch run took 132.168 seconds.
Small22 retained its bounded claim-release wait after verification.

The final incident matrix again used the anonymous 90.46 MB checkout,
4,733 heads and 2.66 GB synthetic journal:

| Writers / hook | Lead Pre min–max, s | Native-end→eligible, s | Eligible→release, s | Scans / MB | Shared reread scans |
| --- | --- | --- | --- | --- | --- |
| 27 / installed | 1.682–1.690 | 2.105–2.159 | 1.575–1.595 | 26 / 2352.0 | 2 |
| 27 / new | 1.618–1.624 | 2.087–2.146 | 1.322–1.391 | 26 / 2352.0 | 2 |
| 30 / installed | 1.874–1.881 | 1.441–2.172 | 1.698–2.411 | 26 / 2352.0 | 1 |
| 30 / new | 1.946–1.952 | 1.465–2.134 | 1.532–2.433 | 26 / 2352.0 | 1 |

All 16 initial changing Writes were denied before execution; all 16 new-id
retries verified exact receipts. Calls closed, claims released and every drain
was accepted, with no fail-open edit or lead-path UNKNOWN. Installed-hook
maximum was 1.881 seconds. Hook start→receipt took 0.583–0.880 seconds,
decision 1.016–1.060 (including scheduler overrun), and reply→exit
0.007–0.015. Maximum scan executor delay was 0.254 ms, observation commit
49.63 ms, and oldest obligation 6.092 seconds. Each shared full revalidation
read 90,460,321 bytes and is included in scan totals; narrow revalidation adds
only its affected paths. No custody reread is needed when its epoch is unchanged.

Fresh interleaved first-recovery pairs (installed 2.4.6 / tree, seconds):
57.041/76.781, 62.596/47.881, 57.019/73.251, 62.737/70.545, 58.603/59.850, 86.809/73.018.
Across all six pairs, installed range was 57.019–86.809 seconds and tree
range 47.881–76.781. The initial tree runs of 76.781 and 73.251 seconds
remain unexplained paired slowdowns; these measurements do not prove their
cause. The third original recipe aborted before restart because it omitted
the host's PostToolBatch after a busy denial and asserted every call verified.
Its 131 executed Writes verified with no UNKNOWN; 68 denied Writes did not
run, and one call awaited the omitted batch. The failure remains recorded.

Three additional pairs were fixed in advance, using the real denial/batch
lifecycle for both versions. Every executed Write required an open durable
grant and an exact receipt; every denied call had to close. Their ranges were
58.603–86.809 seconds installed and 59.850–73.018 on the tree.
The final tree range lies inside the installed spread for those three pairs;
that finite comparison does not establish the cause of the earlier paired
slowdowns. Tree hard-kill restart took 1.247–1.461 seconds, decoding
42,727–42,727 history bytes and 381,521–385,325
total native JSON bytes. All writers reconciled with zero quarantines or
lead-path UNKNOWNs. The external report retains every pair, all actual exits,
and each lead Pre p99/max rather than selecting only faster runs.

The final 27×20 streamed burst produced 21,602 native events in
61.321 seconds. Its 1,166 catch-ups used
7,672 RPCs and 1,926,246 history bytes.
Lead Pre p99/max was 0.685/1.079 seconds;
writer Pre was 0.718/0.872.
All 196 lead Writes verified and every call closed, with no
warnings, denials, quarantines or lead-path UNKNOWNs. There were zero
custody-triggered full rereads; 114 lead-path revalidations read 1,938 bytes.

The 605.016-second idle run had no log growth, watchdog re-arms or new thread
starts. Maximum idle heartbeat delay was 17.59 ms. A subsequent forced
1.2-second GIL-held stall produced a timestamped C traceback, conservatively
marked possibly truncated; rotated segments were 16,777,216 and 3,085 bytes.
These are finite shared-host measurements, not a weeks-long soak or a
hard real-time guarantee. The fixture journal is 2.66 GB rather than the
live 6.46 GB database. Hook availability and onFailure policy are unchanged.

### Round-8 open-client termination verification

The round-7 worker stayed alive with worker.lock held past six seconds when
an idle or partial Unix request remained connected during SIGINT, serve-task
cancellation, an escaping exception, startup failure after binding, or an
accepted drain. Final-source probes exited in 0.024–0.412 seconds, reaped
their watchdog, restored SIGUSR2 and released worker.lock. The drain probe
opened its idle connection after quiescence and received the accepted reply.
A live lead-command RPC, whose external process duration is unbounded, exited
in 1.526 seconds. Cancellation retained its process identity and custody;
after the process ended, restart and custody reconciliation released it with
the ordinary crash-gap UNKNOWNs. SQLite integrity was ok. The shutdown
ordering and exceptional kernel/I/O limits are described above.

All 63 focused/regression recipes passed on final Python source, including
the real installed 2.4.4 denial/batch lifecycle (132.892 seconds), the three
SIGKILL boundaries, the four-settlement cohort, and small22's bounded wait
for claim release after verification. The actual worker entry-point
supplement also passed.

| Writers / hook | Lead Pre max, s | Start→receipt max, s | Decision max, s | Reply→exit max, s | Retry max, s |
| --- | --- | --- | --- | --- | --- |
| 27 / installed | 1.916 | 0.811 | 1.123 | 0.035 | 0.222 |
| 27 / new | 2.055 | 0.714 | 1.332 | 0.012 | 0.200 |
| 30 / installed | 2.184 | 1.091 | 1.083 | 0.014 | 0.205 |
| 30 / new | 2.294 | 1.173 | 1.098 | 0.023 | 0.201 |

Phase maxima can belong to different calls. All sixteen initially denied
Writes were retried under new tool-use ids and verified; no unadmitted edit
or lead-path UNKNOWN occurred. Calls closed, claims released, and all drains
were accepted. Revalidation counts were 20/12/26/32, including 1/2/1/2
shared full reread scans respectively; total reread bytes were
90,460,929 / 180,920,702 / 90,461,133 / 180,920,702.
The installed-hook maxima stayed below 2.5 seconds.

Three fresh interleaved first-recovery pairs used the accepted 27-writer
histories and 2.66 GB journal, with a real PostToolBatch after each denial:

| Pair | Installed seconds | Tree seconds | Tree restart seconds | Tree lead Pre p99 / max, s |
| --- | --- | --- | --- | --- |
| 1 | 60.424 | 61.269 | 1.325 | 0.230 / 0.391 |
| 2 | 69.359 | 88.011 | 1.402 | 0.228 / 0.306 |
| 3 | 76.926 | 87.042 | 1.581 | 0.275 / 0.330 |

All writers reconciled without quarantines, all executed lead edits verified,
and all calls closed. Every restart read 42,727 history bytes; tree total
decoded JSON was 381,349–385,325 bytes. The installed first-recovery range
was 60.424–76.926 seconds (spread 16.502); the tree range was
61.269–88.011 (spread 26.742). These round-8 samples left first-recovery
performance unresolved: they did not establish the required absence of regression.
The round-9 comparison below adds identical CPU/SQL/IO accounting and both run
orders; it does not retroactively assign a cause to these two larger slowdowns.

Ledger counters were extracted before removing large files. All six first
recoveries recorded zero custody transitions. Tree checkout scans totalled
0.121/0.385/0.389 seconds and 4,942/6,612/6,584 bytes. Revalidation
was 1/0/1 reads, 29/0/29 bytes, under 0.6 ms. Custody-epoch rereads therefore
do not explain these slowdowns. Summed tree journal replay spans were
55.574/104.282/100.146 seconds versus 63.341/63.369/89.333 installed;
they overlap across listeners and include cooperative scheduling, so they
cannot be added as a disjoint wall-time account. The replay function's AST
is identical. Every listener's duration and raw counter events are retained
in the external report; these measurements do not prove the remaining cause.
No speculative recovery redesign was made in this shutdown round.

The final 27×20 streamed burst produced 21,602 native events in
61.246 seconds. Its 1,185 catch-ups used 7,805 RPCs and 1,962,173 history
bytes (about 1,656 bytes per catch-up). Lead Pre p99/max was
0.932/1.169 seconds; writer Pre was 1.004/1.090 seconds, slightly
above the one-second p99 aim. All 182 lead Writes verified and all calls
closed, with no warnings, denials, quarantines or UNKNOWNs. No shared
custody reread was needed; 131 narrow revalidations read 2,227 bytes.

The 605.018-second idle run had zero log growth, zero watchdog re-arms and
no new threads. A later forced 1.2-second stall produced a timestamped
2,420-byte C traceback and rotated the pre-seeded 16 MiB segment; status
reported possibly_truncated. These finite shared-host runs do not establish
a hard real-time or weeks-long guarantee. Hook availability behavior is
unchanged. The external round-8 report retains every actual exit, command,
source hash, and the interrupted harness attempt excluded from the pairs.

## Round-9 executor ownership and recovery measurements (2026-10-24)

The interrupted-write probes and Python API compatibility are described above.
All 63 accepted small/lifecycle recipes passed on the final Python source,
including the real 2.4.4 denial/batch lifecycle (130.889 seconds), the three
scan/commit SIGKILL boundaries and the four-settlement cohort. The separate
open-client, owned-RPC and Runner-wide cancellation probes also passed.
Only documentation changed after those final-source runs.

The incident fixture retained changing lead Writes and the existing real
PostToolBatch lifecycle after every denial:

| Writers / hook | Lead Pre p50 / p99 / max, s | Start→receipt max, s | Decision max, s | Reply→exit max, s | Retry max, s |
| --- | --- | --- | --- | --- | --- |
| 27 / installed 2.4.6 | 1.948 / 1.954 / 1.954 | 0.767 | 1.158 | 0.036 | 0.211 |
| 27 / new | 1.940 / 1.944 / 1.944 | 0.906 | 1.030 | 0.009 | 0.210 |
| 30 / installed 2.4.6 | 1.986 / 1.997 / 1.997 | 0.845 | 1.142 | 0.013 | 0.196 |
| 30 / new | 2.221 / 2.278 / 2.278 | 1.346 | 0.932 | 0.009 | 0.208 |

Each row has four busy calls and four admitted retries. Component maxima can
belong to different calls. Every denial prevented execution, every retry verified,
all calls and claims closed, every draining stop was accepted, and no lead-path
UNKNOWN occurred. Durable custody transitions required 2/2/2/1 shared reread
scans, reading 180,920,702 / 180,920,702 / 180,920,702 / 90,460,453 bytes.
Every settlement's disjoint accounting equals its receipt-to-release time.

The recovery comparison used fresh approximately 1.998 GB synthetic native
histories (1,997,863,415–1,997,864,627 bytes in the final six pairs; small
header/path differences are recorded per case) and exactly 321,357 pending journal rows / 2,660,157,537 JSON bytes
per run, with 27 long-history writers plus the dedicated hook writer.
Both versions had identical import-only event-loop CPU, daemon-process CPU,
SQL/commit, process IO and 50 ms callback-lag instrumentation. CPU excludes
native app-server and fixture children. Three pairs ran installed first and
three tree first. The initial six-pair comparison found admission progress
write amplification: a diagnostic pair recorded 26 admission-record writes /
25 standalone transactions per call. Coalescing diagnostic phase labels
retains durable state transitions and reduces this to approximately three
writes / two standalone transactions per call.

The following six pairs were rerun after that fix (B = installed 2.4.6,
T = final tree):

| Pair / order | Recovery B / T, s | Loop CPU B / T, s | Daemon CPU B / T, s | Commit* B / T, s | Tree restart, s | Tree lead Pre p99 / max, s |
| --- | --- | --- | --- | --- | --- | --- |

| 1 / B→T | 80.208 / 67.193 | 48.687 / 36.192 | 53.232 / 40.362 | 2.983 / 2.822 | 1.441 | 0.212 / 0.378 |
| 2 / B→T | 64.714 / 69.814 | 41.630 / 38.726 | 45.852 / 43.041 | 2.712 / 3.221 | 1.698 | 0.231 / 0.310 |
| 3 / B→T | 68.652 / 63.733 | 43.510 / 38.506 | 47.754 / 42.807 | 2.833 / 3.109 | 1.629 | 0.364 / 0.375 |
| 4 / T→B | 86.024 / 79.511 | 47.746 / 41.604 | 52.185 / 46.051 | 3.428 / 3.862 | 1.689 | 0.215 / 0.413 |
| 5 / T→B | 79.720 / 103.243 | 44.886 / 47.504 | 49.226 / 52.358 | 3.457 / 5.896 | 1.362 | 0.280 / 0.396 |
| 6 / T→B | 82.418 / 74.910 | 47.086 / 35.661 | 51.524 / 39.753 | 3.078 / 3.193 | 1.652 | 0.236 / 0.336 |


*Commit time combines explicit COMMIT and autocommit statement time; the latter
includes SQL execution too. Baseline recovery ranged
64.714–86.024 seconds (spread 21.309);
tree recovery ranged 63.733–103.243
(spread 39.510). Mean loop CPU was
45.591 versus 39.699 seconds; whole-daemon
CPU 49.962 versus 44.062.
Mean transaction counts were 33658.8 versus 34460.5;
mean commit* time 3.082 versus 3.684 seconds.
Mean positive callback overshoot was 2.423 versus
2.137 seconds. This proxy includes blocking and descheduling;
it does not establish the cause of the historical round-7/8 outliers.
The continuous samplers did more work during longer recoveries: the 103.243-second
tree run handled 219 lead calls versus its paired baseline's 181. Two additional
opposite-order pairs fixed both versions at 25 changing lead Writes, 25 native
writer turns and 500 full plus 500 brief status calls. Recovery was
60.060/59.184 seconds (B/T, baseline first) and 64.854/59.261 seconds
(B/T, tree first). Mean loop CPU was 26.517/25.874 seconds and daemon CPU
29.803/29.172 seconds. Raw mean transaction counts were 24,657/24,805.
Those include unequal fixture-only attribution writes: 54 baseline scan events
and 0.5 tree reread events per run. Excluding those gives approximately
24,603/24,804.5 transactions, a residual 201.5 (about 202), or 0.82%.
Commit* time was 1.990/2.145 seconds (+7.77%); it includes autocommit SQL
statement execution, not just commit/fsync time, and its fixture contribution was
not separately timed. The remaining durable admission/observation bookkeeping
is measured overhead; these figures do not establish a separate recovery
performance blocker. The large phase-label write amplification is fixed.
These samples do not prove that all historical slow runs were host contention.
No outlier was removed from the six-pair results.

Every writer reconciled with zero quarantines, executed lead edits verified,
and calls closed. Per-listener replay, custody transitions, scan and revalidation
counters were recorded before fixture payload deletion. Their overlapping
elapsed spans must not be summed as a wall-time partition.

The final 27×20 streamed burst produced 21,602 native events in 61.225 seconds.
Its 1,200 catch-ups used 7,899 RPCs and 1,987,496 history bytes (about 1,656
bytes per catch-up). Lead Pre p99/max was 0.726/1.426 seconds (173 calls);
writer Pre was 1.095/1.110 seconds (540 calls), above the one-second p99 aim.
All 173 lead Writes verified; there were no hook errors, warnings, denials,
quarantines or UNKNOWNs, and all calls closed. No shared custody reread was
needed; 159 narrow revalidations read 2,703 bytes.

The 605.016-second idle run added zero diagnostic bytes, watchdog re-arms or
threads. A forced 1.2-second stall produced a timestamped 3,083-byte C traceback
and rotated the seeded 16 MiB segment within the 32 MiB bound; status reported
`possibly_truncated`. Python 3.11 syntax was checked for all 71 plugin Python
files, and the real 3.15 socket run removed the 3.13-only Server APIs at import.
No native Python 3.11/3.12 interpreter run is claimed. The external round-9
report retains every command, exit, source hash, raw counter and measurement
limit. These shared-host runs do not establish hard real-time behavior under
arbitrary load or bounded completion of a kernel-stalled filesystem call.

## Round-10 termination and final-source measurements (2026-10-24)

The shutdown changes and the three calibrated red/green probes are described in
the shutdown section above. All 63 accepted small/lifecycle recipes and all nine
separate write/restore, repeated-interrupt, Runner-wide cancellation, open-client,
owned-RPC and Python 3.12 Server-surface recipes passed after the final Python
change. The real worker entry point also released its worker lock and watchdog
for SIGINT, serve-task cancellation and an escaping exception. Its forced
waiter-registration SIGINT exited in 0.553 seconds, including the fixture's
0.5-second pause. The sibling-failure recipe now includes its own verified
replacement-app-server cleanup, so replaying its recorded command leaves no child.

The final incident matrix used 3,284 synthetic files / 90,460,253 bytes, 4,733
ledger heads and 701,847 journal rows / 2,660,000,130 JSON bytes. Four actual
native commands completed alongside 23 or 26 other writers and changing lead
Writes. Hook timings include interpreter startup:

| Writers / hook | Initial Pre p50 / p99 / max, s | Start→receipt max, s | Decision max, s | Reply→exit max, s | Retry max, s |
| --- | --- | --- | --- | --- | --- |
| 27 / installed 2.4.6 | 1.608 / 1.616 / 1.616 | 0.572 | 1.034 | 0.010 | 0.205 |
| 27 / new | 1.734 / 1.746 / 1.746 | 0.771 | 0.947 | 0.037 | — |
| 30 / installed 2.4.6 | 1.951 / 1.955 / 1.955 | 0.797 | 1.146 | 0.012 | 0.213 |
| 30 / new | 1.728 / 1.731 / 1.731 | 0.696 | 1.021 | 0.015 | 0.205 |

Each row has four initial calls. The 27/new calls were admitted immediately;
the other rows denied all four and admitted all four retries after their blockers
cleared. No denied call executed. Every executed Write verified, every call and
claim closed, each drain was accepted, and there were no hook warnings or
lead-path UNKNOWNs. Component maxima may belong to different calls; p99 equals
max for these four-sample initial batches. Each case required two shared custody
reread scans, about 180.92 MB per case, counted once per physical scan rather than
once per observer.

Four opposite-order recovery pairs were rerun on the final Python source. Every
run synthesized its own approximately 1.998 GB native history and the same
321,357 pending journal rows / 2,660,157,537 JSON bytes. B is the installed 2.4.6
cache, T the working tree. Fixed-request runs each sent 500 full and 500 brief
status calls, 25 changing lead Writes and 25 writer turns; continuous runs kept
sending requests until recovery completed.

| Mode / order | Recovery B / T, s | Loop CPU B / T, s | Daemon CPU B / T, s | Commit* B / T, s | Tree restart, s | Tree lead Pre p99 / max, s |
| --- | --- | --- | --- | --- | --- | --- |
| Fixed / B→T | 61.286 / 58.661 | 23.838 / 21.854 | 27.012 / 24.982 | 1.716 / 1.708 | 1.217 | 0.226 / 0.226 |
| Fixed / T→B | 50.503 / 50.750 | 25.498 / 23.331 | 28.810 / 26.578 | 1.759 / 1.816 | 1.431 | 0.294 / 0.294 |
| Continuous / B→T | 79.469 / 52.174 | 46.376 / 31.129 | 50.881 / 35.007 | 3.024 / 2.441 | 1.342 | 0.231 / 0.265 |
| Continuous / T→B | 60.626 / 53.888 | 37.975 / 30.841 | 41.978 / 34.678 | 2.338 / 2.381 | 1.277 | 0.159 / 0.489 |

Commit* includes explicit COMMIT and autocommit SQL execution; it is not isolated
fsync time. The fixed-request spread was 10.783 seconds for B and 7.911 for T.
Mean loop CPU was 24.668/22.593 seconds, whole-daemon CPU 27.911/25.780,
SQL calls 1,369,727.5/1,188,507.5, and measured SQL time 7.100/5.641 seconds.
Removing all fixture-only attribution transactions gives means of
24,564.5/24,775.5: 211 additional tree transactions, 0.86%. Raw commit* means
were 1.737/1.762 seconds; fixture autocommit contributions were 0.0078/0.0032.
This is a small remaining bookkeeping cost, with lower measured CPU and no
separate recovery-time regression demonstrated in these pairs. The corrected
round-9 residual of about 202 transactions / 0.82% above refers to that round's
samples, not these new ones.

Continuous recovery ranged 60.626–79.469 seconds for B and 52.174–53.888 for T.
Those runs have unequal request counts because longer recovery keeps the
samplers running: lead Pre counts B/T were 173/127 and 144/133. Their elapsed and
CPU results must not be treated as a fixed-work comparison. The 50 ms callback
overshoot proxy includes event-loop blocking and host descheduling; its fixed
means were 0.982/0.808 seconds, continuous means 2.047/0.931. It cannot attribute
historical slow runs solely to host contention.

All writers reconciled, with zero quarantines or hook warnings; executed lead
Writes verified and calls closed. Tree hard-kill restarts took 1.217–1.431
seconds, decoding 40,887–43,310 history-response bytes. Total native JSON,
including concurrent hook traffic, was 373,491–389,496 bytes. Custody-transition,
scan, revalidation and individual listener-replay counters were retained before
deleting the synthetic payloads. The recovery fixture had zero custody
transitions; its tree revalidations read zero or one 27–28-byte path per run.
Overlapping listener spans are not an additive wall-time partition.

The final 27×20 streamed burst produced 21,620 native events in 61.556 seconds.
Its 1,171 catch-ups read 1,930,954 history bytes through 7,699 RPCs, about 1,649
bytes and 6.57 RPCs per catch-up. Lead Pre p50/p99/max was
0.109/0.672/0.827 seconds (194 calls); writer Pre was 0.369/0.585/0.634
(540 calls). All 194 lead Writes verified with no warnings, denials, quarantines
or lead-path UNKNOWNs; all calls closed. There were 118 narrow revalidations /
2,006 bytes and no whole-custody reread. The 605.015-second idle run added no
diagnostic bytes, threads or watchdog re-arms; a forced 1.2-second stall produced
a timestamped 3,082-byte C traceback, correctly labelled possibly truncated.

These finite, shared-host runs do not establish hard real-time behavior under
arbitrary load. Shutdown still waits for actual owned filesystem work; a
kernel-stalled call cannot safely be abandoned merely to meet a timer. Native
app-server processes deliberately survive forced production shutdown, whereas
the fixture finalizers stop their own processes. All 71 Python files passed
3.11 syntax parsing and 3.15 compilation; all 14 hook-run files compiled under
3.9. The 3.12 Server API surface was exercised under 3.15, not a native
3.11/3.12 interpreter. The external round-10 report retains the command lines,
actual exits, source identities, corrected accounting, ownership audit and
measurement limits.

## Round-11 retirement and final-source measurements (2026-10-24)

Intentional native retirement and calibration-child cleanup are described in the
shutdown section above. The final source passed the Y1 replay discriminator,
the genuine-peer-failure control and the Y2 escalation discriminator, plus the
successful-read cancellation control: a patch interrupted only by shutdown stayed
pending and verified after restart without an UNKNOWN. The actual failing-read
X1 control still journaled its error and completed conservative compensation.
The calibrated Y2 negative control restored only the old close method at import;
the initial fixture had omitted the production child watcher and its stopped-child
results were invalid. That is a method-level negative control, not a second
execution of the complete prior tree.

All 63 accepted small/lifecycle cases, the eight separate write/restore,
repeated-interrupt, Runner-wide cancellation, open-client, owned-RPC and 3.12
Server-surface cases, and the X1–X3, cancellation-retry, worker-entry and signal-window
cases passed after the final Python edit. The sibling-failure recipe includes
its own replacement-app-server finalizer. The genuine-peer-failure recipe also
has that finalizer; the final ownership sweep found no remaining fixture process.

The final incident matrix used the same 90.46 MB synthetic checkout, 4,733 heads,
and 701,847 journal rows / 2.66 GB as round 10. All times include hook startup:

| Writers / hook | Initial Pre p50 / p99 / max, s | Start→receipt max, s | Decision max, s | Decision→reply max, s | Reply→exit max, s | Retry max, s |
| --- | --- | --- | --- | --- | --- | --- |
| 27 / installed 2.4.6 | 1.695 / 1.698 / 1.698 | 0.519 | 1.034 | 0.004614 | 0.149 | 0.180 |
| 27 / new | 1.745 / 1.755 / 1.755 | 0.730 | 1.020 | 0.000032 | 0.013 | 0.115 |
| 30 / installed 2.4.6 | 1.949 / 1.949 / 1.949 | 0.933 | 1.010 | 0.000035 | 0.017 | 0.131 |
| 30 / new | 1.747 / 1.752 / 1.752 | 0.671 | 1.071 | 0.000064 | 0.011 | 0.194 |

Each row contains four initial calls. The rows denied 4, 1, 1 and 4 calls,
respectively; the other six calls were admitted immediately. Every denied call
closed through its batch and its retry was admitted with a new tool-use ID.
No denied call executed, every executed Write verified, all claims released,
all four drains succeeded, and no lead-path UNKNOWN or hook warning appeared.
Component maxima can belong to different calls. These small batches have p99
equal to maximum. Each case shared two custody reread scans (about 180.92 MB);
scope revalidation counts were 36, 15, 29 and 20.

Two fixed-request recovery pairs ran in opposite orders, each with freshly
synthesized approximately 1.998 GB native histories and exactly 321,357 pending
journal rows / 2,660,157,537 JSON bytes. Each first recovery received 500 full
status calls, 500 brief calls, 25 changing lead Writes and 25 writer turns.
B is the installed 2.4.6 cache and T is the working tree:

| Order | Recovery B / T, s | Loop CPU B / T, s | Daemon CPU B / T, s | Raw commit* B / T, s | Tree restart, s | Tree lead Pre p99 / max, s |
| --- | --- | --- | --- | --- | --- | --- |
| B→T | 55.842 / 59.969 | 24.382 / 22.300 | 27.599 / 25.470 | 1.761 / 1.701 | 1.400 | 0.424 / 0.424 |
| T→B | 68.010 / 68.163 | 24.564 / 22.600 | 27.789 / 25.732 | 2.000 / 2.123 | 1.409 | 0.258 / 0.258 |

The baseline/tree wall-time spreads were 12.168/8.193 seconds. The tree was
4.127 seconds slower in the first pair and 0.152 slower in the second.
Mean loop CPU was 24.473/22.450 seconds; whole-daemon CPU was 27.694/25.601.
SQL calls averaged 1,374,659.5/1,196,486.5 and SQL time 8.069/6.288 seconds.
After removing every fixture-only attribution transaction, the means were
24,565/24,770.5 transactions: 205.5 additional tree transactions, 0.84%.
Raw commit* averaged 1.880/1.912 seconds; fixture-only contributions were
0.0109/0.0029 seconds. Commit* includes autocommit statement execution plus
explicit COMMIT time, not isolated fsync time. These samples retain the small
bookkeeping cost and lower CPU seen in round 10. They do not establish that the
tree is never slower, or prove that scheduling explains every elapsed-time change.

The 50 ms callback overshoot proxy averaged 1.454/1.227 seconds (B/T); it includes
both loop blocking and host descheduling, not pure run-queue delay. Every writer
reconciled, with zero quarantines, warnings or lead-path UNKNOWNs. Tree hard-kill
restarts took 1.400–1.409 seconds and decoded 42,696 history-response bytes each;
total native JSON, including concurrent hook traffic, was 383,400 bytes.
All four runs had zero custody transitions, 54 checkout scans and zero
revalidations. Individual listener replay spans and scan bytes/times are retained
in the external report; overlapping spans cannot be added into a wall-time total.

The final 27×20 streamed burst produced 21,610 native events in
61.364 seconds. Its 1,175 catch-ups read 1,937,591 history bytes through
7,731 RPCs: 1649.0 bytes and 6.58 RPCs per catch-up.
Lead Pre p50/p99/max was 0.107/0.780/1.031 seconds (198 calls);
writer Pre was 0.353/0.714/0.835 (540 calls).
All 198 lead Writes verified, calls closed, and there were no warnings,
denials, quarantines or lead-path UNKNOWNs. There were 169 narrow revalidations /
2,873 bytes and no whole-custody reread. The 605.012-second idle
run added no diagnostic bytes, threads or watchdog re-arms; its forced 1.2-second
stall produced a timestamped 3,081-byte C traceback, labelled possibly truncated.

The same 71-file Python 3.15 compile and 3.11 syntax checks, 14-file Python 3.9 hook
compile, both hook protocol checks and strict plugin/marketplace validation passed.
Ruff still exits 1 with exactly the installed baseline's 35 findings and no new
ones. No native Python 3.11/3.12 interpreter run is claimed. Shared-host samples
are finite; kernel-stalled filesystem work or an unreapable child can extend safe
retirement. The round-11 report retains actual commands, exits, source hashes,
the native-consumer and retirement audits, calibration failures and limits.


## Round 12 retirement verification

All three new discriminators reproduced on the unchanged round-11 source before
the first edit. An accepted drain interrupted a read-only turn's terminal inventory
and created a permanent evidence-failure UNKNOWN. A stopped native peer with
8,380,486 buffered bytes kept the daemon alive beyond six seconds. Direct
cancellation of the retirement task poisoned the stream's shared close waiter and
left it spinning (49.6% sampled CPU). On the changed source, drain instead named
the unfinished native application; after it applied, stop and restart succeeded,
with no failure or UNKNOWN and a following lead edit verified. The stopped-peer
case escalated and exited in 1.040 seconds. The shared-waiter case exited 0.083
seconds after peer release, and the waiter was not cancelled. The one-second
grace and its calibration are documented in the shutdown description above.

All 83 selected prior small and lifecycle commands passed on the final Python
source, including the self-contained genuine-transport and sibling-failure recipes.
Genuine peer loss still produced the existing evidence-failure outcome.
No production hook failure policy or daemon Python minimum changed.

The incident matrix again used 4,733 heads, a 90.46 MB checkout and the 2.66 GB
synthetic journal. Times below include hook startup:

| Writers / hook | Initial Pre maximum, s | Start→receipt maximum, s | Decision maximum, s | Reply→exit maximum, s | Retry maximum, s |
| --- | --- | --- | --- | --- | --- |
| 27 / installed 2.4.6 | 1.823 | 0.606 | 1.148 | 0.075 | 0.201 |
| 27 / new | 1.673 | 0.640 | 1.024 | 0.011 | 0.197 |
| 30 / installed 2.4.6 | 1.684 | 0.648 | 1.026 | 0.012 | 0.188 |
| 30 / new | 2.068 | 0.931 | 1.113 | 0.032 | 0.184 |

Of 16 initial calls, 15 were denied and one admitted. All retry calls were
admitted with new tool-use IDs, all executed Writes verified, all calls closed
and claims released, with zero unadmitted executions or lead-path UNKNOWNs.
Component maxima can belong to different calls. Decision→reply was at most
0.000957 seconds. Each case made two shared custody reread scans, about
180.92 MB; scope revalidation counts were 12, 12, 7 and 12.

Draining stop is a separate measurement from hook decision latency. Matrix
drains took 44.543, 46.022, 45.759 and 45.348 seconds. Native application blockers
always overlapped existing observation/history/writer blockers; no refusal had
only a native-application blocker. Roughly four to five seconds preceded drain
eligibility; serial listener shutdown dominated the remainder. The 30-writer
installed-hook case returned one LISTENER_DRAIN refusal and then drained on retry.

Two clean fixed-request recovery pairs ran in opposite orders, with a fresh
approximately 1.998 GB native history and exactly 321,357 journal rows /
2,660,157,537 JSON bytes in every run. Each first recovery received 500 full and
500 brief status calls, 25 changing lead Writes and 25 writer turns. B is the
installed 2.4.6 cache; T is the working tree:

| Order | Recovery B / T, s | Loop CPU B / T, s | Daemon CPU B / T, s | Product commit* B / T, s | Tree restart, s | Tree lead Pre p99 / max, s |
| --- | --- | --- | --- | --- | --- | --- |
| T→B | 60.280 / 58.800 | 24.496 / 22.764 | 27.728 / 25.909 | 2.140 / 2.030 | 1.356 | 0.483 / 0.483 |
| B→T | 53.184 / 58.974 | 24.106 / 23.538 | 27.366 / 26.764 | 1.712 / 1.930 | 1.439 | 0.489 / 0.489 |

An additional B→T pair was 54.187/52.592 seconds; payload cleanup overlapped its
baseline, so it is retained in the report but excluded from the clean means.
Clean baseline/tree wall spreads were 7.096/0.174 seconds. The tree mean was
2.155 seconds slower, with 4.7% lower loop CPU, 4.4% lower whole-daemon CPU and
15.9% lower SQL time. Removing every fixture-only attribution transaction gives
24,565/24,773.5 product transactions per run: +208.5, or 0.85%. Product commit*
was 1.926/1.980 seconds (+0.054 seconds); this combines autocommit statement
execution and explicit COMMIT time, not isolated fsync time. The report also
retains the raw values and fixture-only contributions.

The 50 ms callback overshoot proxy averaged 1.157/1.205 seconds (B/T), including
both loop blocking and host descheduling. It does not identify the cause of the
wall-time difference. These finite shared-host samples do not establish that
the tree is never slower. All runs reconciled every writer with no quarantine,
warning or lead-path UNKNOWN. Clean tree hard-kill restarts decoded 42,696
history-response bytes and 380,613–383,058 total native JSON bytes, including
concurrent hook traffic. First recoveries had zero custody transitions, 54 scans
and zero revalidations; each listener's replay span and scan bytes/times remain
in the external report.

The final 27×20 streamed burst produced 21,616 native events in 61.660 seconds.
Its 1,151 catch-ups read 1,895,098 history bytes through 7,562 RPCs: 1,646.5 bytes
and 6.57 RPCs per catch-up. Lead Pre p50/p99/max was 0.119/1.085/1.135 seconds
(167 calls); writer Pre was 0.429/0.747/0.807 (540 calls). All 167 Writes verified;
there were no hook warnings, denials, quarantines or UNKNOWNs. There were 119
narrow revalidations / 2,057 bytes and no whole-custody reread. Drain took 31.043
seconds: a 28.224-second LISTENER_DRAIN refusal, then acceptance in 2.719 seconds.
There was no native-application refusal, and the post-drain ledger had no native
evidence failure or UNKNOWN. The new drain accounting therefore added no isolated
quiescence refusal in these burst/matrix samples; it did expose the deliberately
held terminal-inventory application in the decisive Z1 case.


The 605.017-second idle run added no diagnostic bytes, watchdog re-arms or
threads. Its forced 1.2-second GIL-held stall produced a timestamped 3,082-byte
C traceback, labelled possibly truncated; both rotated log segments remained
within their 16 MiB limits. The 71-file Python 3.15 compile and 3.11 syntax pass,
14-file Python 3.9 hook compile, both hook protocol checks, strict plugin and
marketplace validation and whitespace checks passed. Ruff retained exactly the
installed baseline's 35 findings (exit 1 on both, no new findings).

The round-12 external report records each command and exit, all strict pre-edit
reds, source hashes, every retirement/helper call, watcher inspection, full drain
attempts, accounting definitions, calibration failures and fixture adaptations.
Final ownership checks found no fixture process; measured synthetic payloads and
owned socket allocations were removed individually after recording evidence.
The documented kernel-stalled work/unreapable-child limit remains. No actual
Python 3.11/3.12 interpreter run or arbitrary third-party watcher behavior is
claimed, and these shared-host samples are not a universal latency guarantee.

## Claude lead edit hooks

`hooks/coordinator_lead.py` runs under Python 3.9 for PreToolUse, PostToolUse,
PostToolUseFailure and PostToolBatch. It never starts a daemon or registers an
unrelated checkout.
Write, literal Edit and ordered MultiEdit prepare a complete intended image and
bind it to checkout/session/tool-use identity before the host edit. Successful
completion verifies bytes, mode and preconditions; duplicate posts are idempotent.
Ambiguous replacements, failure, missing admission or altered inputs grant no
receipt. An edit that failed or did not reach its intended image settles as no
effect only if its bytes still match its pre-image; partial or unexplained changes
are UNKNOWN. Calls with a usable host birth or transcript path stay held until
terminal evidence; a permission prompt or slower PreToolUse hook can hold them
indefinitely. Calls without either channel use the legacy 120-second fallback,
even when the daemon assigned an admission id.
`lead_hook.execution` records its lifetime separately from its operation's receipt
state. From admission through byte settlement, it refuses overlapping operations
(including another call in its own unit), handback, stop, other leads' quiet
windows, and restoration of its paths (`RESTORE_EXECUTING`), and retains the
checkout's compatibility lease. A refusal to record an overlapping file operation
is a host `permissionDecision: deny`, not an allowing warning.

The first PostToolUse, PostToolUseFailure, PostToolBatch, matching transcript
`tool_result`, or verified host exit ends the call. Pre captures `CLAUDE_PID` only
when it is the hook's parent, including its kernel birth identity. An absent or
reused PID, or that process as a zombie, proves the original host exited; a stopped
or uninspectable process does not. An interrupted call on Claude Code 2.1.306 sends
no Post and no batch: its transcript result is needed while the session remains
alive. The observer reads complete JSONL lines from the owned regular transcript,
without following a symlink, matching session and tool-use ids. Missing files are
retried; an unreadable, replaced, truncated or incomplete transcript is no proof
of completion. A cursor never skips an incomplete line or a result preceding a
delayed admission. Both successful and failed results end the call.

Every terminal source uses the same serialized, idempotent settlement. Expected
bytes and modes with intact preconditions receive an exact receipt; unchanged
pre-images can settle as no effect; partial or unexplained effects are UNKNOWN.
A failed result never proves that no bytes were written. The lifetime hold stays
until settlement is durable, including when I/O fails and must be retried. A late
or duplicate event cannot revive a call or credit it twice. Input changes can end
the lifetime but cannot acquire authorship.

Startup restores these holds and attempts to recover their leases before serving.
One call's failed settlement, such as an unreadable target, retains its hold and
records `lead_hook/recovery_failed`; it does not prevent the daemon serving other
checkouts. Status names its `observation_problem`, and the observer retries until
its bytes can be settled. A legacy hook left `prepared` after its operation's
`verified`, `denied`, `resolved` or `reconciled` commit is finished from that durable
outcome, without a new receipt or lifetime hold (`lead_hook/recovered_terminal`).
If an older state held no lease and another writer already owns it, the hold stays,
`daemon/status.lead_edits` names `lease_problem`, and the observer retries acquisition;
new file admissions still require the lease. An unfinished edit
across daemon replacement is quarantined, preserving UNKNOWN rather than repairing
its attribution with matching bytes, and remains held until its host call ends.
Recovery also imports the `executing` lead edits left by the unreleased turn-44
implementation, including ones already quarantined. Their terminal evidence ends
execution and permits restoration; their UNKNOWN history stays. A missing legacy
path scope conservatively holds the checkout.
Encoding/BOM and CRLF preservation are checked by expected bytes; host behavior
that differs is refused or quarantined, never adopted after the fact.

**Legacy availability limit:** calls without a usable host birth or an absolute
transcript path use
`lead_calls.LEGACY_FILE_CALL_SECONDS = 120`, inherited from 2.4.3/2.4.4's
maintenance threshold `now - operation.created > 120` (the separate hook-record
deadline was 180 seconds). At 120 seconds from admission, without a Post or batch, the operation
is quarantined; an ungranted attempt’s own reservations are released, while
unit claims and UNKNOWN remain.
Its lifetime holds on the lease,
stop, other leads' quiet windows and restoration end. A late Post never creates an
exact receipt. This is an availability limit, **not evidence that the tool ended**;
an old tool can still write after it. `lead_hook/legacy_limit` records the reason,
call, paths, elapsed timestamps and cumulative use count in the same transaction
that ends the hold; `daemon/status.lead_edit_legacy_limit` exposes its count and
latest use. While waiting, `lead_edits` names its deadline and reason. A missing or
temporarily unreadable transcript does not turn a modern call into a timed call:
having its path is enough to retain the no-timer rule.
Migration retains the operation's creation time, or reconstructs admission from
an old hook's 180-second deadline. If neither timestamp survived, it starts the
120-second legacy allowance at recovery rather than inventing an earlier admission.

**NotebookEdit is UNVERIFIED and not recorded**, including insertion. Its native
cell ID and serializer have not been calibrated through the installed host. On a
path another unit claims it is denied as CLAIM_CONFLICT; elsewhere the host's
edit goes ahead without an exact receipt, with a lifetime reservation and lease,
so what it wrote reconciles as UNKNOWN after its end. The
refusal names a full-file Write route. Direct hook observations are not evidence
of the host's encoding/serialization behavior; see VERIFY.md for coverage.

Claude Code (2.1.306, measured) sends neither PostToolUse nor PostToolUseFailure for
a call that another PreToolUse hook denied (the push gate, a project hook), that a
permission refused, or that the user rejected. Such a call shows only in
PostToolBatch's `tool_calls`, sent once every call of a batch has resolved; without
it, a Bash call's record holds every lead's quiet window and `daemon/stop`
(`lead_hooks`) until its deadline, up to 180 s; file calls instead await one of the
terminal signals above. Each lead call the coordinator may
hold something of leaves a marker in `<state>/lead-hooks/`, named by the SHA-256 of
its session and tool-use id, made before its Pre's call and removed once a Post
settles the call or the daemon definitively refuses its admission. PostToolBatch sends
one `lead-hook/batch` request containing the Bash calls whose marker is left and every
file call in a registered checkout, marked ones first
(a file call whose Pre never reached the daemon may have left none). Before its first
daemon call, the hook writes every completion to a private outbox directory under
`lead-hooks/batch-<digest>/`: mode 0700, with one mode-0600 input file per item. A
directory rename commits the complete outbox atomically. Verified directory/file
descriptors refuse symlinks, foreign ownership and non-private permissions; the hook
never creates or repairs an unsafe state directory. No daemon, a timed-out request,
or the released daemon's `METHOD` reply leaves the outbox available to a later daemon.
The daemon imports it after startup and during observation, without another host event.
An acknowledgment removes input files: the whole outbox after durable batch receipt,
or individual files as the released-daemon fallback acknowledges them. A fallback
that exhausts its hook budget leaves every unsent or uncertain item in the outbox.
Staging names include the producer hook's PID and kernel birth identity before any
item is written. A live producer's stage is never taken. After that exact process
exits (including PID reuse), the next scan imports every complete JSON item, even
if the producer died before the manifest or directory rename. A partial final item
is discarded only from an abandoned stage; after durable receipt, the entire stage
is removed. No elapsed-time rule decides ownership, and an error while publishing
does not erase already-written completions.

Before observing an individual call, the daemon atomically commits a small
`lead_batch` header and independent `lead_batch_item` records. Each item uses its
host-call lock and the usual completion handler. A result or non-operational refusal
ends delivery; an operational failure retries. Once the call's own `lead_hook`
record contains end evidence or a pending byte observation, that record alone owns
the retry. Delivery then keeps its owner acknowledgment, not another copy of the
input. Completed/refused items retain identities, paths, input digests and outcomes;
they drop input bodies. Startup also converts and compacts round-5 batch records,
including already-completed ones.

Batch attempts and every observer-started call settlement run as independent guarded
jobs under their host-call locks. This includes admitted calls with saved end evidence,
transcript or host-exit evidence, the legacy limit and unadmitted byte observations.
The shared observer only schedules; it never awaits their probe, lease or settlement
I/O. Read-only transcript/host probes have separate keyed jobs from settlement retries;
they run before taking the host-call lock and do not count as mutations in flight.
A Post, Failure or batch can therefore settle the same call while its probe is stalled.
Saved end evidence also starts settlement retries independently of an outstanding
probe, including after the first settlement fails. Probe reads use daemon threads
outside the filesystem executor, with no cancellation shield: shutdown cancels their
waiting jobs without joining a stalled read, so the worker exits and releases its lock
for replacement. A late result is ignored if its future was cancelled or its loop closed.
The probe thread only reads its snapshot; ledger changes stay on the event loop.
Under the host-call lock, the settlement job reloads the record and uses
a probe's evidence, cursor, problem or error only if its revision is unchanged;
otherwise it discards that result. Actual settlement remains serialized under the
lock. A failed read, acknowledgment or problem save cannot stop that observer or make
it wait for another call's filesystem work, including in another checkout. Operational
delivery/observation retries and outbox polling use a 3 s interval, derived from the
hook's 3 s RPC timeout; it bounds repeated work and never expires a completion.
Pending-item indexes avoid rereading finished history. Stored batch-item JSON decoding,
hashing, serialization and outbox filesystem work run off the daemon event loop; SQLite
transactions stay on its owning thread. RPC envelope decoding and idempotency hashing
run inline, so even a fully occupied filesystem worker pool cannot delay decoding or
admission of status requests and immediate refusals. The 25-settled-Write plus unreadable-target
probe decoded no queued input bytes during a four-second retry interval and retained
about 17 KB of identity/acknowledgment data, compared with round 5's 350 MB of repeated
decoding and 834 KB of retained inputs. A disconnected or timed-out hook does not
cancel a received request. The daemon admits a batch during a drain as it does a Post.
Each is settled as its Post would be, from what shows. A Bash call is
reconciled with no `lead-command` credit: a batch is no evidence the command ran, so
what changed in its window is nobody's, as at its deadline. An edit is decided by its
bytes: no effect at its pre-image, verified at its intended image, quarantined
otherwise. Already-settled file calls add no scan and share one daemon round trip:
measured through the real Python 3.9 hook entry point, batches of 1, 3 and 5 took
median 0.068, 0.078 and 0.067 s, against 0.045, 0.043 and 0.048 s with the installed
2.4.4 hook, which asked about none. With the worker stopped, each size returned in about 3.06 s;
after continuing the worker, all received completions were processed. Leftovers are
durably pending, including calls with no earlier admission or host metadata.
A batch containing only already-settled Bash calls still makes no daemon call.
`HOOK_SECONDS` remains 14, below the host's 15 s timeout. Only an answered `METHOD`
refusal selects the older per-call protocol during a switch to a released 2.4.4
daemon; that fallback retains its existing per-call budget and its durable outbox
retains the unacknowledged tail across replacement. Only a
marker identified as Bash may be removed by the daily age sweep; file markers
remain until a definitive acknowledgment or durable settlement.

Legacy file-hook saves are not replaced by completion-only records. The released
2.4.4 states `verified`, `no_effect`, `failed` and `unrecorded` already record a
processed completion; `outside` already ended coordination without a ledger
observation. Startup and delayed Posts/batches preserve these saves and their
operation links without reading later bytes. A terminal operation also ends
an older `prepared` save left behind by a crash. `uncertain` needs its cause:
the first quarantine UNKNOWN naming its operation carries a bare exception code
or class name after a failed Post verification, which proves completion. The
maintenance gap and recovery quarantine reasons do not. Without completion evidence,
`received`, `prepared` and `uncertain` retain their lifetime rules, including the
legacy availability limit for calls without a usable host identity or transcript
path. An admission identity is not an end-evidence channel. A refused call
still needs a Post to prove it ran; a batch alone never creates its observation.

The live-view tail (`tail -F "$HOME/.claude/logs/codex-oracle/stream.log"`; until 2.4.5
a hook asked each lead to start it in the background, and a session on an older plugin
version still may) reads one log file and writes only its own output, so it cannot
touch a checkout. A lead Bash
call that is exactly that command is not coordinated: no record, no scan, no window
held. Exactly means three words: `tail`; `-F` or `-f`; and that log path spelled with
`$HOME`, `${HOME}`, `~` or the home directory. Any other shape, such as a pipe, a
redirection or a second command, is coordinated as any lead Bash call.

Bash hooks never run the command again. PreToolUse reconciles the project reach, so
what changed before the command is not its doing, and records a quiet-window
fingerprint: None while another lead Bash command in any registered checkout is still
running (until its host deadline), a writer's turn is active, a patch or edit is
pending, command custody is active, or an exec write holds a registered tree. The
fingerprint binds the daemon generation, durable mutation counters and retained
exec-lock fingerprints. Finished operations, released custody and completed exec
writers still change it, without a scan of their history. It is checked at arrival,
the end of Pre, confirmation, and before Post attribution. A call that started and
ended in between changes it, and the window is not opened.
The window is **armed** only when the hook received
Pre's answer before the host ran the command: the answer carries a one-time
token and the hook confirms it (`lead-hook/armed`). Losing the initial Pre reply
(the client's 3 s timeout) means no confirmation and UNKNOWN effects. Losing the
confirmation reply is different: the arm may already be durable. The hook reports
`"armed": "uncertain"` in that case, otherwise `true` or `false`; the later Post
uses the persisted decision. Same-token confirmation retries are idempotent and
do not extend the deadline. Recovery explicitly clears old windows and tokens;
their Post is UNKNOWN. Post
(or Failure) reconciles again: when the window was armed and unchanged at both
ends, what changed outside other units' claims is recorded as `lead-command` for
the lead's unit; otherwise it is UNKNOWN. A second completion for the same call
attributes nothing. On a checkout whose Pre takes longer than 3 s (a reconcile
of tens of thousands of files), no lead command is credited. An unavailable
coordinator gives Bash and file calls one warning per session in private external
warning state: lead edits are allowed, and later reconciliation exposes unexplained
changes as UNKNOWN. File Pre denies only an explicit refusal answered by the daemon
(`CoordinatorError` from its validated error envelope, with `kind:"refusal"`,
`operational=False`), including claim and
operation conflicts, published prefixes, changed checkout identity, and rewritten
or uncertain calls. Client import failures, no daemon, RPC timeouts/lost replies,
local client/endpoint/state validation failures, malformed results and operational failures (including `RECOVERING` and
`DAEMON_DRAINING` without an upgrade owner) allow the call with that warning. A
failed admission does not invent exclusion or let a later Post earn a receipt.
Its first Post or Failure immediately reconciles exactly its paths as UNKNOWN if
the bytes changed; an operational failure's batch does the same. A refusal without
a Post gets no such observation: a batch alone does not prove it ran. The observation
and completion acknowledgment commit together, so repeated Posts do not scan again.
If the observation fails, it retries without restoring any lifetime hold or lease.
A completion with no Pre record uses that same durable observation directly,
before any admission, attachment, unit, claim or compatibility lease. Its Post,
Failure or batch reconciles only the reported file's path, once; a
legacy writer can keep its lock throughout. Unchanged bytes create no UNKNOWN.
Read or checkout-identity failures leave a pending observation for the observer;
outside-ledger and unregistered paths need no byte observation. File batches
include registered paths even when no marker remains, since an unavailable Pre
may have left none. Existing completed records make these repeat batches inert;
an explicit refusal without a Post still gets no observation from a batch alone.
An admission that completed but lost its reply retains all its lifetime holds.
Open-call overlap refusals use `CLAIM_CONFLICT` with named holders, including the
same session/unit and an admitting call with no unit yet, so installed 2.4.4 hooks
also deny them. During a daemon switch, the current hook recognizes the released
2.4.4 completion reply shapes and removes their markers without an unavailable warning.
Local security failures still prevent all contact with an untrusted endpoint;
they allow the file call, with the same once-per-session unavailable warning.
Unregistered and outside-ledger files remain outside coordination.

**Known limit (HIGH):** the registered Pre hook has a 15-second host timeout and
no `onFailure:"block"`, as in 2.4.4. If Claude Code times it out before admission,
the host discards its output and can run the file call without exclusion. The
availability policy also permits a call when its coordinator RPC answer is
unavailable. No missing admission is reconstructed as an exact receipt: unexplained
bytes become UNKNOWN on reconciliation. This can race another writer, a stop or
restore; the no-timer lifetime guarantee applies to admitted calls. The registration
is unchanged. See [Claude Code's timeout behavior](https://code.claude.com/docs/en/hooks#timeouts).

## Observational gate query (Python 3.9)

From the host hook process outside Bash's sandbox:

```python
from coordinator.query import query_checkout
state = query_checkout(cwd, session_id, timeout=0.25)
```

Set PYTHONPATH to the plugin directory, or import after adding that directory.
The equivalent CLI is:

```sh
/usr/bin/python3 -B -m coordinator.query /path/to/checkout --session-id HOST_SESSION_ID
```

`SOFTWARE_WORKFLOWS_STATE` or `--state` chooses the external state root. One RPC
returns `available`, `fresh`, `generation`, `status`, `registered`, `checkout`,
live `units` with held `paths`, unresolved `unknown` with context, `own_unit` or
null, and `elapsed_ms`. Status distinguishes `coordinated`, `uncoordinated`,
`coordinator_down`, and `identity_changed`. Down replies set registered=null,
fresh=false and generation=null; stale snapshots never masquerade as current.
During recovery available can be true while fresh is false. The CLI exits 0 only
for fresh coordinated/uncoordinated answers, otherwise 2. Query code never starts
a daemon, registers a checkout, opens SQLite, calibrates/binds sockets or writes
a cache. It reports the ledger's current knowledge without filesystem reconcile.
The gate's consumer integration is lead-owned and outside this unit.

## Round 13 listener-retirement verification (2026-10-25)

Round 12's two close-escalation events came from sequential closes: a completed
graceful close followed by an explicit abort rearmed an expired timer. The
round-12 external report has been corrected. Each connection now has one
completion/escalation state. A stopped peer with 8,380,486 buffered bytes caused
one escalation after 1.001 seconds and shutdown in 1.036 seconds; aborting an
already completed connection caused none. The actual close frame is six binary
WebSocket bytes. The final-source 100-call calibrations measured local close
maximum 0.243 ms and peer acknowledgment maximum 0.341 ms, retaining ample
headroom within the documented fixed one-second graceful-flush bound.

Strict pre-edit probes reproduced the paused successful post-image read being
quarantined on ordinary listener stop and wedged-listener retirement, and the
queued completed patch being judged unfinished. On the final source, all three
patches verify before/after restart, create no UNKNOWN, and allow a following
lead edit. The listener-local marker covers downstream patch/submission
cancellation, and serving detach applies the durable native suffix before
unfinished judgement. Global shutdown leaves it for restart. A real worker
failure chained to cancellation, or genuine peer loss, retains its failure
handling; the genuine-peer control still records its expected UNKNOWNs.

All 91 selected lifecycle/small recipes passed after the final Python edit,
including the real 2.4.4 hook's legacy fallback, the three SIGKILL boundaries,
four-settlement cohort, local/global cancellation and probe-child retirement.
Each recipe includes its own verified-ownership fixture cleanup, including
replacement app-servers. Four fixture path/label mapping failures were retained
and rerun successfully with fresh paths; no acceptance assertion was removed.

The incident matrix used 4,733 heads, the 90.46 MB synthetic checkout and the
2.66 GB synthetic journal. Each row has four initial changing file-hook calls.
Times include hook startup; phase columns are maxima across those initial calls
and therefore need not belong to the same call.

| Writers / hook | Initial Pre p50 / p99 / max, s | Start→receipt max, s | Decision max, s | Reply→exit max, s | Retry max, s |
| --- | --- | --- | --- | --- | --- |
| 27-installed | 2.270 / 2.276 / 2.276 | 1.229 | 1.040 | 0.014 | 0.181 |
| 27-new | 1.711 / 1.714 / 1.714 | 0.604 | 1.105 | 0.008 | 0.199 |
| 30-installed | 1.792 / 1.799 / 1.799 | 0.761 | 1.022 | 0.017 | 0.224 |
| 30-new | 2.096 / 2.100 / 2.100 | 1.027 | 1.050 | 0.025 | 0.208 |

Of 16 initial calls, 15 were denied and one admitted. All 15 retried calls were
admitted and all executed Writes verified; there were no fail-open executions
or lead-path UNKNOWNs. Calls closed, claims released, and every drain was
eventually accepted. Draining-stop totals were 64.428 / 46.297 / 77.080 /
73.186 seconds in table order. These exceed some round-12 samples
(44.543 / 46.022 / 45.759 / 45.348 seconds); one case included a LISTENER_DRAIN
refusal. Roughly 5.5 seconds preceded eligibility, while serial listener
retirement dominated the rest. There was no native-application-only quiescence
refusal. The one-second stream-flush bound is not a one-second whole-daemon
drain guarantee. Each matrix case had two shared custody reread scans
(180.92 MB total), with 17 / 42 / 26 / 38 path revalidations.

Two fresh-history fixed-request pairs ran in opposite orders against the
installed 2.4.6 cache. Each used 27 writers, about 1.998 GB of synthetic rollouts,
321,357 legacy native journal rows / 2,660,157,537 JSON bytes, 500 full and 500
brief status calls, 25 changing lead Writes and 25 writer turns, with the
correct PostToolBatch lifecycle. All four runs had zero quarantines, warnings
or lead-path UNKNOWNs, all writers reconciled, and 25 exact lead receipts.

| Order | Recovery 2.4.6 / tree, s | Loop CPU 2.4.6 / tree, s | Daemon CPU 2.4.6 / tree, s | Tree restart, s | Tree lead Pre p99 / max, s |
| --- | --- | --- | --- | --- | --- |
| tree then 2.4.6 | 77.114 / 86.459 | 27.121 / 25.972 | 30.459 / 29.229 | 1.430 | 0.248 / 0.248 |
| 2.4.6 then tree | 83.412 / 68.645 | 28.735 / 24.872 | 32.098 / 28.075 | 1.560 | 0.536 / 0.536 |

The baseline wall range was 77.114–83.412 seconds
(spread 6.298); the tree range was
68.645–86.459 (spread 17.813).
The tree was slower in one pair and faster in the other. Mean wall time was
80.263/77.552 seconds (baseline/tree), loop CPU
27.928/25.422, daemon CPU
31.279/28.652, and measured SQL time
9.453/8.085. Removing every fixture-only attribution
transaction gives 24,572.0/24,770.0
product transactions: 198.0 extra
(0.81%).
Product commit* time was 2.684/2.920
seconds. commit* includes explicit COMMIT duration plus autocommit statement
execution and commit; it is not isolated fsync latency. CPU excludes native and
fixture children. The 50 ms heartbeat overshoot includes loop blocking and host
descheduling; it is not a pure runqueue measurement. These four samples do not
prove a universal performance bound or identify the cause of the wall variation.
Tree hard-kill restart decoded 40,887/42,696 history bytes and
373,576/386,861 total native JSON bytes.

The final 27 × 20 streamed workload completed in 61.422 seconds
at 21,100 native events/minute and
1,583 catch-up triggers/minute. Lead Pre p50/p99/max
was 0.120 / 0.985 / 1.067 seconds
(n=160); writer Pre was
0.469 / 0.753 / 0.866 (n=540). There were no
quarantines, hook denials/warnings or unclosed lead calls; all 160 lead executions
verified. 1,158 catch-ups read
1,909,845 history JSON bytes in 7,611 RPCs
(1649.3 bytes/catch-up).
There were 156 narrow revalidations / 2,652 bytes, with no whole-custody reread.
Its final drain took 68.629 seconds: LISTENER_DRAIN after
17.082 seconds, then acceptance in 51.446 seconds. The post-drain ledger had
no native evidence failure or UNKNOWN. This drain was longer than round 12's
31.043-second sample; the measurements establish completion, not an unchanged
drain latency.

The 605.011-second idle probe added no diagnostic bytes, watchdog
re-arms or threads. A forced 1.2-second loop stall produced a timestamped C-level
traceback, labelled possibly_truncated; rotation stayed within the two 16 MiB
segments. All source compiles, hook protocol checks, strict plugin/marketplace
validation, Python 3.11 syntax and whitespace checks passed. Ruff retained the
baseline's 35 findings (exit 1 on both, no new findings).

The round-13 external report retains strict pre-edit source hashes, actual
COMMAND_JSON/output/exit records, standalone recipe cleanup, per-listener
replay and scan attribution, fixture adaptations and the full command appendix.
The documented kernel-stalled work/unreapable-child limit remains. Actual
Python 3.11/3.12 execution, arbitrary third-party watchers, a separate new
composite-patch retirement discriminator, and universal shared-host latency
bounds are not claimed; the composite cancellation path was audited and uses
the same ledger cancellation policy.

### Round 14: command ownership and receipt settlement

A lead-command call registers its in-process ownership before starting its child and retains it through settlement. Maintenance and explicit custody reconciliation may inspect the process, but cannot certify a missing exit status while that owner can still supply the real one. A later daemon generation has no such owner and retains the existing crash-gap UNKNOWN behavior.

Serving listener retirement joins consumers, replays any unapplied journal suffix, and then separately examines every eligible outstanding patch receipt of that listener. Receipt settlement does not depend on the journal cursor: copying completion evidence is not proof that byte verification committed. Single and composite patches use a canonical completion lock (the parent for parts), reload state under it and return an already matching receipt. Fresh verification still enforces lifecycle, scope, byte and base checks; the actual postimage-mismatch control remains conservative. The lock order is lifecycle/call/executor ownership, operation completion, then checkout commit locks; SQLite transactions contain no awaits.

A turn/start interrupted by serving retirement keeps its input identity and is durably uncertain. Plain-run maintenance and workflow advancement own the existing ordered phase-input settlement reads, at most once per two seconds. This resume-mode selection excludes queued message deliveries; the branch that ends a replacement app-server requires thread/queue/add and therefore does not end a replacement for a phase input. They recognize entered or queued evidence, or retain unproven input for an explicit lead resume; they never resend automatically. Global termination leaves the existing restart recovery in charge.

Round-14 strict pre-edit real runs reproduced all six discriminators (B1, C1, single/composite C2, and plain/workflow C3); the final runs passed. The completed-composite retirement, explicit live custody reconciliation, killed-owner crash gap and genuine postimage mismatch controls also passed. U1 passed five times. Runtime recipes use real Codex 0.163.0 with disposable state and synthetic histories; interleavings are forced at import.

The final incident matrix (four initial lead calls per row) measured:

| Writers | Hook | Pre p50 / p99 / max (s) | Accepted drain (s) | TERM-to-exit sum (s) |
| --- | --- | --- | --- | --- |
| 27 | installed | 1.800 / 1.811 / 1.811 | 33.858 | 33.580 |
| 27 | new | 1.590 / 1.592 / 1.592 | 38.998 | 38.700 |
| 30 | installed | 1.995 / 2.002 / 2.002 | 38.285 | 37.960 |
| 30 | new | 1.966 / 1.970 / 1.970 | 39.715 | 39.480 |

Every initial busy refusal was followed by its host batch and a new-id retry. All retries were admitted, calls closed, claims released, drains accepted, and no lead UNKNOWN or fail-open edit occurred. The installed hook is the unchanged 2.4.6 hook under system Python 3.9. These admission times do not bound the full drain.

The new listener/detached event records monotonic close, consumer-join, replay and receipt-settlement spans. listener/stopped adds detach, process-exit and final-settlement spans. The existing listener/exit records TERM-to-exit time. D's per-listener measurements and gaps are retained before fixture ledgers are removed. Across the four accepted matrix drains, close/consumer/replay/receipt work was milliseconds while process-exit waits took tens of seconds. Listener stops remain sequential; this measurement did not justify a shutdown-policy change.

The fresh fixed-request recovery comparison ran in both orders (tree then installed 2.4.6, and installed 2.4.6 then tree):

| Pair | Version | First recovery (s) | Loop CPU (s) | Daemon CPU (s) | Restart (s) | Restart history JSON (B) | Lead Pre p99 / max (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | tree | 69.815 | 24.321 | 27.499 | 1.439 | 42696 | 0.351 / 0.351 |
| 24 | baseline | 86.828 | 25.876 | 29.116 | 1.314 | 40887 | 0.348 / 0.348 |
| 25 | baseline | 93.025 | 26.377 | 29.581 | 1.374 | 40887 | 0.432 / 0.432 |
| 25 | tree | 97.564 | 27.546 | 30.728 | 2.160 | 42696 | 1.427 / 1.427 |

Baseline first-recovery range: 86.828–93.025 s (spread 6.196 s). Tree first-recovery range: 69.815–97.564 s (spread 27.748 s). Each run synthesized its own history. Product transaction counts exclude fixture-only event writes on both versions. commit* includes autocommit statement execution plus explicit COMMIT time, less fixture-only writes; it is not fsync-only time. Raw explicit COMMIT time is reported separately. Loop-lag summed overshoot is a scheduling/blockage proxy. Per-listener replay, custody transitions, scan and revalidation counts/bytes/times are retained. This comparison supports the measured workload, not a universal never-slower claim. Pair 24 favored the tree by 17.013 s; pair 25 favored the baseline by 4.539 s, and that tree restart took 2.160 s. Mean product transactions were 24,565.5 for the baseline and 24,857.0 for the tree (+291.5, +1.19%); mean loop CPU was 26.127/25.934 s and SQL execution time 10.362/8.327 s. The wider tree wall-time spread and its explicit-COMMIT outlier do not isolate a host-versus-code cause.

The 27 × 20 streamed burst completed in 61.562 s: 171 verified lead Writes and 540 writer Pre calls, with lead Pre p99/max 0.840/0.924 s and writer Pre 0.805/0.936 s. There were no hook warnings, denials or quarantines. Its accepted drain took 19.576 s, including 19.280 s of TERM-to-exit waiting; the earlier LISTENER_DRAIN attempt is separately recorded.

The final idle diagnostic run lasted 605.014 seconds: routine diagnostic bytes and thread starts stayed unchanged. A forced 1.19-second loop stall then produced a timestamped C traceback and rotation, with the existing possibly_truncated status. Python 3.15 and hook Python 3.9 compilation, both hook compatibility checks, Python 3.11 syntax parsing, strict plugin/marketplace validation and whitespace checks passed. Ruff retained the installed baseline's 35 findings with no new findings.

Known limits remain: a live coroutine registry is not cross-generation exit evidence; unproven input requires an explicit lead decision; no matching receipt blesses a differing image; sequential native process retirement can take tens of seconds; and documented kernel-stalled worker limits remain. The full command/output/exit record, final-source hashes, unsuccessful fixture calibrations and per-listener attribution are in r247-w/round14/implementation-report.md and its artifacts. The earlier round-13 composite-fixture limitation is superseded by the real composite receipt/retirement runs in this round.

### Round 15: custody inventories, service lifetime and terminal outcomes

Local native retirement raises a distinct Retired call outcome (a cancellation
subclass), preserving the item's durable obligation without treating it as peer
failure. Maintenance catches that outcome and retries on its next pass after
rebinding; cancellation of maintenance's own task still propagates on stop.
Process-lifetime maintenance, executor and lead-call observers have journaled
restart supervision, with a one-to-thirty-second backoff to prevent a failed
service from forming a restart/commit busy loop. The backoff resets after thirty
seconds of healthy service; there is no attempt limit. daemon/status exposes each
service's running/restarting/stopped state and reason. An unexpected native
application or log-consumer exit instead journals service/failed and stops the
daemon visibly: restarting past an interrupted journal application could skip
evidence. Startup replays that suffix. Intentional listener retirement and daemon
termination are expected exits. A scan producer completes its failed frozen
cohort exceptionally and restarts for later queued callers; finite history and
readmission jobs retain their existing durable retry obligations.

The group settlement lock does not block the command owner from publishing its
root process identity or completion. Those writes advance durable revisions,
invalidating an older process inventory. A quarantined process-inventory window
remains active until a fresh successful inventory and observation permit release.
Neither process absence nor an in-process owner registry proves successful
execution across a daemon restart.

The four strict pre-edit probes reproduced the stale inventory, silent maintenance
exit, duplicate mismatch quarantine and resolved-operation regression. All four
passed after the final Python edit. D1 retained custody until the detached child
ended and attributed its later write to that custody; this opaque write correctly
remains UNKNOWN rather than becoming an exact patch receipt. D2 kept the original
phase input, retried after rebinding, reached unproven with zero sends, and completed
the later repair. Unexpected cancellation of each of the three process-lifetime
services was also journaled and restarted.

Round-15 acceptance is not all green: the first 27-writer installed-hook run,
concurrent with E's idle-selector fixture, took 2.687–2.801 seconds for its four
safe denials, exceeding the required maximum of 2.5 seconds. No unadmitted Write
ran. The later repeat is shown separately and does not erase that failure. The
cause of the slower sample is not isolated.

| Writers / hook / sample | Initial Pre p50 / p99 / max (s) | Start→receipt max (s) | Decision max (s) | Reply→exit max (s) | Retry max (s) |
| --- | --- | --- | --- | --- | --- |
| 27-installed | 2.690 / 2.801 / 2.801 | 1.382 | 1.415 | 0.090 | 0.271 |
| 27-new | 2.082 / 2.090 / 2.090 | 0.904 | 1.183 | 0.009 | 0.213 |
| 30-installed | 1.950 / 1.961 / 1.961 | 0.876 | 1.070 | 0.015 | 0.240 |
| 30-new | 2.219 / 2.235 / 2.235 | 1.124 | 1.118 | 0.012 | 0.232 |
| repeat 27-installed | 2.156 / 2.161 / 2.161 | 0.802 | 1.352 | 0.013 | 0.198 |
| attribution repeat 27-new | 1.996 / 2.000 / 2.000 | 0.871 | 1.108 | 0.024 | 0.212 |
| attribution repeat 30-installed | 2.188 / 2.205 / 2.205 | 1.126 | 1.064 | 0.016 | 0.247 |

Each matrix sample used four initial changing Writes and new-id retries after
the host batch. All retries were admitted and verified, denied calls closed and
their reservations retired, and drains were eventually accepted, with no lead
path UNKNOWN or fail-open execution. Successful retry unit claims are distinct
from the denied attempt's reservations. Phase maxima can belong to different calls.

Five idle-selector runs reached the approximately 60-second select and then exited
0.074–0.179 seconds after SIGINT, including one under the matrix load. The original
lead timeout was not reproduced. A deliberate busy-loop control exited red and
recorded selector waits, pending timers and recent callbacks; the recipe's wait
was not silently lengthened.

Two additional fresh-history fixed-request pairs retained every per-listener
replay, scan, custody-transition and revalidation event before fixture cleanup.
The earlier two pairs are also reported, but their individual replay events were
not exported, so the complete-attribution pairs are the primary comparison:

| Order / version | First recovery (s) | Loop CPU (s) | Daemon CPU (s) | Restart (s) | Restart history bytes | Lead Pre p99 / max (s) |
| --- | --- | --- | --- | --- | --- | --- |
| 26 / tree | 70.080 | 24.328 | 27.516 | 1.602 | 42696 | 0.299 / 0.299 |
| 26 / baseline | 82.138 | 28.786 | 32.153 | 1.522 | 34692 | 0.228 / 0.228 |
| 27 / baseline | 97.517 | 29.772 | 33.240 | 1.805 | 40887 | 0.534 / 0.534 |
| 27 / tree | 68.514 | 25.315 | 28.611 | 1.646 | 44896 | 0.558 / 0.558 |

Pair 26 ran tree then installed 2.4.6; pair 27 reversed that order. Baseline
wall range was 82.138–97.517 seconds; tree range was
68.514–70.080. Mean loop CPU was 29.279/24.821
seconds (baseline/tree), and SQL execution time 10.877/6.406.
Excluding every fixture-only event transaction on both versions gives mean
product transactions 24574.0/24855.0 (+281.0, +1.14%).
Product commit* time was 3.421/2.110 seconds; raw explicit
COMMIT time was 0.307/0.148. commit* includes autocommit
statement execution and commit, not fsync alone. CPU excludes native/fixture
children; 50 ms loop overshoot includes blocking and descheduling. These samples
do not establish a universal never-slower claim or isolate host versus code
causes of wall variation. Every run had the same 321,357 legacy native rows /
2,660,157,537 journal JSON bytes, 500 full and 500 brief status requests, 25 changing
lead Writes and 25 writer turns, plus independently synthesized native histories.

The final 27 × 20 streamed burst completed in 61.187 seconds. All 153 lead Writes
verified; lead Pre p50/p99/max was 0.124/1.097/1.469 seconds, writer Pre
0.452/0.826/1.112 (n=540). There were no hook warnings, denials or quarantines.
1,180 catch-ups read 1,952,811 history JSON bytes in 7,770 RPCs. Its 161 narrow
revalidations read 2,737 bytes. The accepted drain took 26.112 seconds after an
earlier LISTENER_DRAIN attempt; total draining time was 67.484 seconds. Per-listener
close, consumer join, replay, receipt, process-exit and settlement spans and gaps
are retained in the round-15 drain attribution. Sequential process-exit waits
dominate these drains; this round changes no listener-stop policy.

The 605.021-second idle run added no diagnostic bytes, watchdog re-arms or threads.
A forced stall then produced a timestamped C traceback and bounded rotation with
possibly_truncated status. All requested lifecycle and small-case categories have
passing final-source runs, including U1 five times and the real 2.4.4 hook.
Unsuccessful fixture/path adaptations and the original latency failure remain in
the log. Python 3.15 and hook Python 3.9 compiles, both hook checks, Python 3.11
syntax parsing, strict plugin/marketplace validation and whitespace checks passed;
Ruff retained the installed baseline's 35 findings, with no new findings.

The full report, strict-red source proof, SOURCE_CHANGE hashes, audits, actual
outputs/exits, standalone recipe cleanup and selected COMMAND_JSON appendix are in
r247-w/round15/implementation-report.md. The installed-hook 2.5-second maximum
remains an unmet acceptance result. The original intermittent idle-selector
timeout remains unexplained. Existing kernel-stalled work/unreapable-child and
cross-generation uncertainty limits remain; actual Python 3.11/3.12 execution is
not claimed by the syntax/API-surface checks. Earlier round-specific measurements
remain historical, not claims about every later tree or host.

### Round 16: whole-hook admission budget and native-burst scheduling

The measurements in this historical section used a `-c` measurement wrapper in the hook argv. Round 17 replaces that fixture instrumentation with `sitecustomize` and runs the actual hook script as the interpreter entry point. Round 16's peer-birth probe checked kernel identity lookup, not hook classification; its availability claim for long-lived clients was incorrect and is corrected by the round-17 socket probe.

The two-second decision deadline now starts at the hook, including daemon accept/read delay. Installed macOS hooks use the Unix peer PID and kernel birth; new hooks also send an absolute deadline. This historical round used one FIFO scheduling point for native reception, application and log tails. Round 18 later introduced independent receipt owners and a shared cooperative slice; Round 19 replaces that shared wakeup with the private FIFO tickets described above. Admission reads only requested heads and combines its first pre-image/intent executor work. The abandonment and live-owner guards remain in place. The derivation and platform fallback are described in the admission section above.

Three strict pre-edit 27-writer incident repetitions did not reproduce the historical 2.5-second overrun (maximum 1.637 seconds); five pre-edit lone edits also passed. A calibrated first pre-image read delayed 1.15 seconds at import did reproduce the lone-edit refusal: exit 1, safe denial at 1.079 seconds, naming its own pre-image work. The identical calibrated case passes after the change: admission at 1.497 seconds and an exact receipt. This is a deliberate scheduling-delay control, not a claim that the historical host failure was reproduced naturally.

Final incident runs used the real 0.163.0 app-server, changing Writes, the installed 2.4.6 hook or the new system-Python hook, and the same shape/journal workload. The one-minute host load at case start ranged from 5.903 to 12.490. All 56 initial Writes were admitted and verified, with no fail-open edit or lead-path UNKNOWN. There was consequently no denied initial call to retry in this matrix; the separate busy-denial, legacy-hook, abandonment and retry recipes passed. All calls ended and drains were accepted. Phase maxima can belong to different calls.

| Writers / hook / repeat | Pre p50 / p99 / max (s) | Start→receipt max (s) | Decision max (s) | Reply→exit max (s) | Host load |
| --- | --- | --- | --- | --- | --- |
| 27-installed-0 | 0.422 / 0.475 / 0.475 | 0.245 | 0.223 | 0.009 | 5.903 |
| 27-installed-1 | 0.362 / 0.380 / 0.380 | 0.189 | 0.188 | 0.009 | 6.542 |
| 27-installed-2 | 0.592 / 0.599 / 0.599 | 0.299 | 0.308 | 0.006 | 8.442 |
| 27-installed-3 | 0.531 / 0.559 / 0.559 | 0.210 | 0.341 | 0.012 | 7.353 |
| 27-installed-4 | 0.613 / 0.621 / 0.621 | 0.352 | 0.379 | 0.014 | 12.490 |
| 27-new-0 | 0.466 / 0.505 / 0.505 | 0.235 | 0.280 | 0.010 | 8.683 |
| 27-new-1 | 0.841 / 0.920 / 0.920 | 0.203 | 0.718 | 0.026 | 7.875 |
| 27-new-2 | 0.561 / 0.622 / 0.622 | 0.247 | 0.381 | 0.015 | 8.391 |
| 30-installed-0 | 1.143 / 1.344 / 1.344 | 0.537 | 0.791 | 0.073 | 9.091 |
| 30-installed-1 | 1.432 / 1.509 / 1.509 | 0.613 | 0.887 | 0.014 | 10.249 |
| 30-installed-2 | 1.811 / 1.817 / 1.817 | 0.590 | 1.242 | 0.026 | 10.399 |
| 30-new-0 | 0.917 / 1.003 / 1.003 | 0.474 | 0.518 | 0.019 | 9.528 |
| 30-new-1 | 0.754 / 1.001 / 1.001 | 0.378 | 0.575 | 0.048 | 10.440 |
| 30-new-2 | 0.811 / 0.827 / 0.827 | 0.275 | 0.595 | 0.013 | 9.319 |

The five 27-installed cases each ran beside E. Their maximum was 0.621 seconds, including a run at load 12.490; the overall installed-hook maximum was 1.817 seconds. Five ordinary lone-edit repetitions all admitted, and five additional repetitions after recovery also admitted; their individual results and host loads are in the report. The calibrated lone-read case provides the strict red/green boundary.

Import-only measurement records each loop callback/task step ≥2 ms with its awaiting owner, SQL calls ≥1 ms, executor submission/start/end and thread CPU, real hook startup/connect/exit, and C stack samples. It was calibrated with a 150 ms blocking sleep, CPU spin and GIL-holding thread. A 3-second native-burst sample contained 66/478 main-thread samples in take_gil, 224 in kevent and 58 in fsync; these are samples, not an exact per-request GIL clock. In the slowest final installed-hook window (1.815 seconds), loop CPU was 0.392 seconds, 16 admission-executor jobs used 0.032 CPU seconds and 0.268 summed job-wall seconds, and their summed queue delay was 0.021 seconds. Overlapping job durations and SQL spans must not be added to callback durations. The old instrument recorded loop CPU over each decision interval but did not yet record every loop turn. The report retains all owners and avoids attributing every non-CPU second to the scheduler.

Retired remains an intentional-retirement outcome. joined() waits for completion through a private waiter that receives only a wakeup value, then consumes the original dependency outcome. A dependency-only Retired therefore stays Retired for both Tasks and plain Futures. Cancellation delivered to the owner cancels only that private waiter; the owned dependency is still joined, then owner cancellation wins with worker failure chained for diagnosis. This also covers owner cancellation delivered while a dependency Task is ready to resume after its inner Future completed. A cancellation request that has not yet been delivered remains subject to normal asyncio scheduling. Round 17 corrects the earlier round-16 overclaim for plain Futures. Other gather/shield/timeout conversions are audited against durable native journal, operation, observation, input and lead-call obligations; none may turn cancellation into a receipt. The complete conversion audit is in the round-16 report.

Five final idle-selector probes reached a roughly 60-second selector timeout beside the matrix and exited 0.074–0.176 seconds after SIGINT. Their negative control exited 1 with pending timers and recent callback diagnostics. One additional attempt did not reach the required long-idle window because a 250 ms timer remained; that failed calibration is retained separately, and the normal wait was not silently extended.

Fresh-history fixed-request pairs ran in both orders, with equal native journal work and identical accounting. Every per-listener replay, scan, custody-transition and revalidation event was exported before payload cleanup.

| Order / version | First recovery (s) | Loop CPU (s) | Daemon CPU (s) | Restart (s) | Restart history bytes | Lead Pre p99 / max (s) |
| --- | --- | --- | --- | --- | --- | --- |
| 26-tree | 59.880 | 23.070 | 26.201 | 1.374 | 42696 | 0.387 / 0.387 |
| 26-baseline | 58.341 | 24.215 | 27.438 | 1.297 | 40887 | 0.387 / 0.387 |
| 27-baseline | 60.000 | 24.378 | 27.590 | 1.461 | 42133 | 0.492 / 0.492 |
| 27-tree | 56.993 | 22.451 | 25.608 | 1.302 | 42696 | 0.209 / 0.209 |

Baseline/tree means were 59.171/58.437 seconds wall and 24.297/22.761 seconds loop CPU. These samples support only this measured comparison; they do not prove universal never-slower behavior or isolate the cause of all wall variation. Excluding fixture-only event transactions gives 24564.000/24845.500 product transactions. Product commit* was 1.520/1.614 seconds; it includes autocommit statement execution and commit. Raw explicit COMMIT time was 0.082/0.073 seconds. CPU excludes child app-server/fixture processes; fixed-50-ms loop overshoot includes blocking and descheduling. Full definitions, IO counters and both orders are retained in the report.

Drains are measured separately from lead decisions. Across the 14 incident runs total drain time was 7.246–59.857 seconds; summed native process-exit spans were 3.088–53.330 seconds. Per-listener detach, close, consumer join, replay, receipts, process exit and settlement are exported. Nested spans are not summed twice. This round changes no drain eligibility or process-stop policy.

The final 27 × 20 streamed burst completed in 61.317 seconds. All 205 lead Writes verified; lead Pre p50/p99/max 0.103/0.575/0.746 seconds, writer Pre 0.375/0.596/0.622 (n=540). There were zero hook warnings, denials or quarantines. 1179 catch-ups read 1869160 history bytes in 7676 RPCs. Its total drain took 38.453 seconds: a LISTENER_DRAIN refusal at 32.996 seconds retained ownership, then an accepted attempt took 5.356 seconds. Per-listener spans are exported, including the unsuccessful attempt.

The 605.016-second idle run added no diagnostic bytes or watchdog re-arms. One lazy executor thread started (2→3 thread-start count), not recurring watchdog threads. A forced 1.157-second stall produced a timestamped C traceback and bounded rotation with possibly_truncated status. All requested lifecycle/small-case categories were rerun after the last Python edit, including U1 five times and the real 2.4.4 hook. Static checks pass; Ruff retains 35 installed-baseline findings and no new findings. Python 3.11 syntax and the 3.12 API-surface controls are not claims of execution on those interpreters.

The round-15 2.801-second sample remains a failed historical result; it is not erased by these passing round-16 samples. The deadline and cooperative scheduling bound coordinator preparation under the measured load, not arbitrary OS descheduling, a giant individual native payload, a blocked filesystem syscall or an unreapable kernel child. Installed hooks on non-macOS without a supplied deadline retain the documented receipt-relative fallback. The original intermittent idle-selector timeout is still not naturally reproduced. Reports, selected COMMAND_JSON lines, raw failures, SOURCE_CHANGE hashes, source proof and cleanup manifests are in r247-w/round16/implementation-report.md and selected-index.json.

### Round 17: real hook entry points and dependency retirement

Strict pre-edit socket probes reproduced the availability regression: two clients older than two seconds, one with the hook filename in a -c comment and one receiving it as a script data argument, were refused despite otherwise eligible paths. They now use per-request receipt/supplied deadlines and verify exact receipts. Actual installed 2.4.4, 2.4.5 and 2.4.6 hooks and the new hook retain peer-birth deadlines. The entry-point and min-selection rules are in the admission section; attempts retain their source and explicit fallback reason. No hook source or timeout changed this round.

The plain-Future discriminator was also red before the edit: dependency-only Retired became ordinary CancelledError. A private completion waiter now distinguishes owner cancellation from every dependency outcome without cancelling the dependency. All four requested Future/Task cases pass, as does an additional case with owner cancellation delivered while a dependency Task is ready to resume from an already-completed inner Future. An undelivered cancellation remains subject to normal asyncio scheduling. Native wrapper, maintenance, shutdown and repeated-cancellation regressions pass on the final implementation.

Instrumentation is now selected through sitecustomize on PYTHONPATH, leaving the actual hook script as the interpreter entry point. All 56 changing Writes in the fourteen incident runs admitted and verified, with zero lead-path UNKNOWNs and deadline source peer birth. Case-start one-minute load ranged 10.844–14.357. Five 27-installed runs ran beside the selector probe; the other three matrix cells have three repetitions each. No matrix retry was needed; separate busy-denial/legacy lifecycle cases exercise closure and new-id retry. All calls closed, claims released and drains were accepted. Independent phase maxima may occur on different calls.

| Writers / hook / repeat | Pre p50 / p99 / max (s) | Start→receipt max (s) | Decision max (s) | Reply→exit max (s) | Load |
| --- | --- | --- | --- | --- | --- |
| 27-installed-0-drain | 1.114 / 1.269 / 1.269 | 0.489 | 0.772 | 0.015 | 11.384 |
| 27-installed-1-drain | 0.918 / 0.986 / 0.986 | 0.307 | 0.662 | 0.021 | 11.804 |
| 27-installed-2-drain | 0.579 / 0.758 / 0.758 | 0.239 | 0.492 | 0.054 | 11.065 |
| 27-installed-3 | 1.516 / 1.563 / 1.563 | 0.765 | 1.062 | 0.028 | 10.844 |
| 27-installed-4-retry | 0.970 / 1.149 / 1.149 | 0.542 | 0.580 | 0.069 | 13.305 |
| 27-new-0 | 1.079 / 1.092 / 1.092 | 0.440 | 0.699 | 0.018 | 14.357 |
| 27-new-1 | 1.141 / 1.196 / 1.196 | 0.663 | 0.660 | 0.093 | 12.969 |
| 27-new-2 | 1.084 / 1.152 / 1.152 | 0.576 | 0.559 | 0.077 | 12.931 |
| 30-installed-0 | 0.706 / 0.816 / 0.816 | 0.436 | 0.447 | 0.046 | 12.532 |
| 30-installed-1 | 0.848 / 0.981 / 0.981 | 0.365 | 0.586 | 0.029 | 12.684 |
| 30-installed-2 | 1.264 / 1.307 / 1.307 | 0.360 | 0.978 | 0.025 | 11.764 |
| 30-new-0-drain-repeat | 0.820 / 0.960 / 0.960 | 0.351 | 0.605 | 0.018 | 11.800 |
| 30-new-1 | 0.970 / 1.069 / 1.069 | 0.494 | 0.674 | 0.032 | 11.535 |
| 30-new-2 | 1.107 / 1.119 / 1.119 | 0.348 | 0.779 | 0.036 | 11.728 |

Hook CPU/connect and daemon accept/read/decision, callback/task owners, SQL and executor queue/CPU phases are exported. Calibration distinguishes a blocking sleep, a spin and a GIL-holding worker; wall minus CPU is not attributed solely to host descheduling. Nested SQL spans and overlapping executor work are not summed twice. The timeout callback still needs an available event loop; the measured maximum is not a universal real-time guarantee. A failed peer lookup visibly uses receipt-relative budgeting and loses pre-receipt accounting.

| Lone-edit run | Load | Pre (s) | Post (s) |
| --- | --- | --- | --- |
| r17-final-release-small-14 | 14.123 | 0.115 | 0.068 |
| r17-final-extra-small14-0 | 17.244 | 0.465 | 0.075 |
| r17-final-extra-small14-1 | 17.224 | 0.111 | 0.085 |
| r17-final-extra-small14-2 | 17.224 | 0.130 | 0.077 |
| r17-final-extra-small14-3 | 17.126 | 0.108 | 0.078 |
| r17-loaded-small14-0 | 15.533 | 0.209 | 0.073 |
| r17-loaded-small14-1 | 15.113 | 0.113 | 0.070 |
| r17-loaded-small14-2 | 14.781 | 0.099 | 0.067 |
| r17-loaded-small14-3 | 14.667 | 0.124 | 0.077 |
| r17-loaded-small14-4 | 14.798 | 0.103 | 0.078 |

The ordinary lone-edit repetitions and calibrated first pre-image read delayed 1.15 seconds all admit and verify. The calibrated Pre took 1.532 seconds; its measured read delay was 1.157 seconds and its recorded source was peer birth. These hook processes are real script entry points, not the earlier -c measurement wrappers.

Five final selector probes beside the matrix reached roughly 60-second select timeouts and exited 0.074–0.181 seconds after SIGINT. Negative controls still exit 1 with wait diagnostics. Two additional attempts failed to reach long idle because 50 ms timers remained; their failures remain recorded. A fresh paired repeat kept the same prerequisite and exit assertion, with extra task-frame diagnostics, and passed. The intermittent failure to reach idle is not given an unsupported root cause.

Each recovery comparison synthesized fresh histories and exported every per-listener replay, custody transition, scan and revalidation before cleanup. Pair 26 ran tree then installed 2.4.6; pair 27 reversed the order.

| Order / version | Recovery phase / launch (s) | Loop / process CPU (s) | Restart phase / launch (s) | Restart history bytes | Lead Pre p99 / max (s) |
| --- | --- | --- | --- | --- | --- |
| 26-tree | 85.728 / 86.251 | 23.881 / 27.057 | 1.627 / 2.386 | 45989 | 0.180 / 0.180 |
| 26-baseline | 101.246 / 101.937 | 25.975 / 29.281 | 1.683 / 2.390 | 40887 | 0.527 / 0.527 |
| 27-baseline | 110.659 / 111.589 | 26.287 / 29.600 | 1.596 / 2.383 | 40887 | 0.410 / 0.410 |
| 27-tree | 89.305 / 90.360 | 24.701 / 27.951 | 1.622 / 2.422 | 42696 | 0.432 / 0.432 |

Recovery phase is the daemon’s recovery event duration; launch time includes worker startup and completion detection. Case-start one-minute loads were 13.419–15.301. All runs use 321,357 legacy rows / 2,660,157,537 native JSON bytes, 500 full and 500 brief requests, 25 changing lead Writes and 25 writer turns. Product transaction counts exclude fixture-only writes. commit* includes autocommit statement execution and commit; raw explicit COMMIT is measured separately. CPU excludes native/fixture children, process IO is not exclusively SQLite traffic, and the 50 ms lag proxy includes blocking and descheduling. These pairs establish the measured comparison only, not universally never-slower behavior.

baseline: first-recovery range 101.246–110.659 seconds; mean loop CPU 26.131; product transactions 24561.0; product commit* 1.707 seconds; raw explicit COMMIT 0.101 seconds.

tree: first-recovery range 85.728–89.305 seconds; mean loop CPU 24.291; product transactions 24853.0; product commit* 1.811 seconds; raw explicit COMMIT 0.086 seconds.

Matrix drains are measured separately: 30.267–106.242 seconds. Every per-listener detach/close/consumer/replay/receipt/exit/settlement span and refusal is exported. One first attempt exceeded the inherited 60-second fixture window; fresh final cases used a 180-second observation window without altering production bounds. One stopped-helper regression exited cleanly with its helper reaped at 5.263 seconds against its <5-second fixture check; an unchanged fresh-state repeat passed at 3.455 seconds. Both results remain. No shutdown policy changed in this round.

The final 27 × 20 streamed burst completed in 61.336 seconds. Lead Pre p50/p99/max was 0.106/1.109/1.128 seconds (n=154); writer Pre 0.624/0.938/1.042 (n=540). Hook denials/warnings/quarantines: 0/0/0. 1147 catch-ups read 1849186 history bytes in 7488 RPCs; final drain took 87.089 seconds. Per-listener phases and every drain attempt are retained.

The 605.023-second idle probe changed diagnostic bytes 16777216→16777216, thread-start count 2→2, watchdog arms 0→0. A forced stall produced a timestamped C traceback; maximum measured lag 1.161 seconds, status possibly_truncated. Q5 remains a bounded-retention diagnostic, not continuous disk logging.

The final acceptance catalog, including the real 2.4.4 hook and five U1 runs, was replayed after the last Python edit. Python 3.15 compile, hook Python 3.9 compile, both hook checks, 3.11 syntax parsing, strict plugin/marketplace validation and whitespace checks pass. Ruff retains the installed baseline’s 35 findings with no new findings. Python 3.11/3.12 syntax/API controls are not claims of runtime execution on those interpreters. Existing kernel-stalled work/unreapable-child and crash-gap limits remain. Report, raw failures, source proof, SOURCE_CHANGE manifest and selected COMMAND_JSON appendix: r247-w/round17/implementation-report.md and selected-index.json.


### Round 18: independent native receipt and final-source evidence

This historical round used a shared-slice wakeup recheck, superseded by Round 19's
FIFO service contract above. It also introduced the admission dispatch deadline
guard, which remains current. The latter was required by a
real intermediate run: its grant check was 2.494 ms before the deadline, but its
allow was sent 26.595 ms after it. The completed preparation's dispatch now checks
the deadline again and follows the existing busy-denial path if it woke too late.
No hook deadline, native response timeout or SQLite durability setting changed.

The first three final-source streamed bursts each journaled and applied all **21,600 core
notifications, 540 completed turns and 12,420 deltas**, released custody and
accepted the draining stop. Before and after drain, the full burst-method
histogram and every listener's applied prefix were checked: no unapplied burst
event, failed/pending prefix, reconcile failure, background failure or UNKNOWN
remained. Receipt, application and release below are the first successful polling
observation after measurement; they are upper bounds on catch-up time, not three
independently timestamped transitions. Drain is measured separately from its
first request to acceptance.

| Final tree run | Start / mean / peak one-minute load | Measurement (s) | Notifications/min | Receipt / applied / released after measurement (s) | Drain (s) | Lead Pre p50 / max (s) |
| --- | --- | --- | --- | --- | --- | --- |
| tree1 | 25.088 / 47.404 / 62.723 | 72.835 | 17,794.456 | 0.035 / 0.035 / 0.035 | 56.143 | 1.262 / 2.733 |
| tree2 | 24.677 / 37.160 / 49.117 | 70.735 | 18,321.851 | 0.042 / 0.042 / 0.042 | 52.795 | 1.329 / 2.252 |
| tree3 | 24.243 / 39.866 / 51.467 | 72.418 | 17,901.963 | 0.044 / 0.044 / 0.044 | 74.569 | 1.245 / 2.790 |

The host has 14 cores. Mean and peak load use the recipe's host samples; the rate
is its complete native-notification count, including incidental skills-change
notifications. All three runs had zero hook denials, warnings and quarantines.
They establish the observed N1 behavior. They do **not establish N2**. Comparisons
ran tree/base and base/tree, with 180-second cooldowns and no concurrent audits or
large reads during measurement. The first baseline attempt (starting load 23.983)
exited 1 before producing a burst measurement. The second (starting load 23.501,
mean 43.303, peak 59.287) measured 18,166.757 notifications/min in 71.339 seconds,
with lead Pre p50/max 2.340/3.272 seconds, but exited 1 for a hook warning before
catch-up and drain. Neither is a complete accepted baseline sample, and the load
windows are not matched. The slower intermediate tree sample and all failed
attempts remain in the round-18 report; no never-slower throughput claim follows.

In those three runs, mean batch service was **1.212, 1.283 and 1.689 ms**; the largest
individual batch took **212.322 ms**. The largest batch held 64 notifications /
20,187 wire JSON bytes. Per-listener peaks were **34,906 queued bytes / 115
notifications** and **274 committed application entries**. There was no capacity
wait or oversized-message handoff in these three workloads. The largest observed
receipt age was **6.728 seconds**, the largest fixed journal-barrier wait **6.727
seconds**, and the largest measured response latency **5.310 seconds**. These are
separate maxima; summed concurrent listener waits are not elapsed wall time.
They also show why the 2 ms cooperative budget must not be described as a maximum
queue age or COMMIT duration. Queue/batch constants retain their pre-change
measured workload derivation, documented beside the constants.

The required fresh pre-edit burst did not reproduce the lead's NATIVE_UNCERTAIN
failure. At its measurement end it had only 162 completed turns; receipt caught
up 3.272 seconds later and application 13.825 seconds later, with zero UNKNOWNs.
Its real recipe and property exits were both 0. The lead's retained failing run
remains historical evidence; this attempt is not relabeled a strict red.

The final 14-case F matrix retained the lead's F1–F6 mappings and both installed
and new hook entry points at 27 and 30 writers. All 14 recipes, the independent
deadline check and the final closure/release audit exited 0. There were **56
initial calls, 37 initial denials and 37 successful retries**; all 56 successful
edits were admitted before writing and verified afterward. Every recorded grant
check and every allow reply preceded its peer-birth deadline. Among successful
calls, the smallest grant margin was **94.466 ms** and the smallest allow-reply
margin **53.185 ms**. Maximum Pre was **2.475 seconds**. There were zero fail-open
edits, zero lead-path UNKNOWNs, and every final drain was accepted; drain times
ranged **14.504–116.031 seconds**.

The denial phases were pre-image IO (26), host-call lock (2), preparation
reservation (1), grant (3), pre-image executor (1), lease (2), and claim
reservation (2). Two initial settlement queries correctly refused with
`RESTORE_EXECUTING`; the later exported closure set showed every call settled
and no remaining claims. The installed hooks accounted for 22 initial denials
and new hooks for 15. Case-start loads were **24.077–42.650**, compared with
**19.09–24.90** in the lead's reference, which had 13 denials. These results keep
the measured safety properties but do not establish no-worse denial performance
at matched load. All five paired idle-selector companions exited 0.

The affected runtime catalog was replayed on this same frozen runtime: **133
positive commands exited 0**, and the two intentional idle diagnostic controls
exited their expected 1. This includes five U1 repetitions, D2, the hard-kill
restart, atomic boundaries, lifecycle/close/cancellation cases, actual legacy
hooks, lone edits and repeated small-14 cases. Earlier failed attempts remain
separate in the round-18 report and command appendix.

Fresh first-recovery comparisons ran tree/baseline and baseline/tree. All four
syntheses, four measurements and four in-recipe restarts passed. Each measured
run used **321,357 legacy rows / 2,660,157,537 native JSON bytes**, exactly **500
full and 500 brief status requests**, **25 lead Writes and 25 writer turns**.
All 25 writer receipts were exact; all listeners reconciled, with no quarantine
or path UNKNOWN. Fixture synthesis used the frozen checkout in every case; the
baseline measurement daemons loaded the installed plugin, as independently
recorded by their module-origin events.

| Order / source | Start load | Recovery phase / launch (s) | Loop / process CPU (s) | Restart phase / launch (s) | Restart history bytes | Lead Pre max (s) |
| --- | --- | --- | --- | --- | --- | --- |
| 26-tree | 28.702 | 123.116 / 123.916 | 36.114 / 39.897 | 4.588 / 5.802 | 45989 | 0.720 |
| 26-baseline | 27.906 | 120.196 / 120.900 | 39.873 / 43.635 | 4.823 / 5.968 | 45989 | 0.864 |
| 27-baseline | 24.117 | 120.434 / 121.009 | 41.011 / 44.812 | 4.441 / 5.519 | 45989 | 0.854 |
| 27-tree | 22.888 | 117.224 / 118.078 | 37.563 / 41.308 | 4.413 / 5.174 | 45989 | 0.623 |

The first pair measured a slightly slower tree recovery and the reverse pair a
faster one; these are observations under the stated loads. Product transaction,
COMMIT, CPU, IO, lag and per-listener attribution exports are retained. Fixture
transactions are excluded from product transaction totals. A read-only inspection
of legacy recipe provenance overlapped the second tree recovery; no such audit
overlapped the final burst measurements.

The final unanswered-peer control returned `NATIVE_UNCERTAIN` after **60.002
seconds**, then answered normally after the peer resumed; its recipe exited 0.
The failed-COMMIT control injected a real deferred foreign-key violation in a
native receipt transaction. It recorded `IntegrityError`, an active transaction
before rollback and no active transaction afterward. The two uncommitted native
rows were absent after rollback, application received no failed-batch work, and a
structured journal failure preceded the visible stop. Restart completed the full
burst with zero UNKNOWNs, reconcile failures or unapplied events; recipe and
independent N1 validator both exited 0. The failed batch contained status and
turn-start notifications, so this injection did not directly exercise a failed
batch containing a server-request obligation. Separate ownership and replay
controls remain in the runtime catalog.

The final Q5 idle measurement ran alone for **605.059 seconds** and exited 0.
Diagnostic bytes remained **16,777,216 → 16,777,216**, thread starts **2 → 2**,
and watchdog arms **0 → 0**. Its forced stall produced a timestamped traceback;
the final maximum measured lag was **1.146 seconds**, with one dump marked
`possibly_truncated`. The current segment was 2,402 bytes and its retained prior
segment 16,777,216 bytes. This preserves bounded diagnostic retention and the
independent stall watchdog; it does not claim complete nonblocking trace output.

Two additional final-source tree bursts also passed the complete N1 validator,
including application, custody release and final drain. They repeated both A/B
orders with 180-second cooldowns and no audits or large reads during measurement.
The complete final selection therefore contains five passing tree bursts and
four unsuccessful installed-baseline attempts; all attempts remain in the report.

| Additional run | Exit | Start / mean / peak one-minute load | Measurement (s) | Notifications/min | Lead Pre p50 / max (s) | Receipt / applied / released after measurement (s) | Drain (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| tree4 | 0 | 25.879 / 56.424 / 70.247 | 121.663 | 10,652.373 | 1.985 / 4.674 | 0.071 / 3.252 / 0.071 | 84.274 |
| base3 | 1 | 26.766 / unavailable / unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |
| base4 | 1 | 26.851 / 40.371 / 47.686 | 70.006 | 18,513.627 | 1.118 / 3.071 | unavailable | unavailable |
| tree5 | 0 | 25.244 / 41.344 / 48.903 | 70.493 | 18,388.116 | 1.324 / 2.754 | 0.038 / 0.038 / 0.038 | 89.219 |

Tree4 recorded two hook denials and zero warnings; tree5 recorded neither. The
slower tree4 sample and its 4.674-second Pre maximum are retained despite its N1
success. Base3 exited with `condition did not arrive` before exporting a burst
measurement; transport errors in its log do not establish their cause. Base4
exited for one hook warning before catch-up and drain. Its measured rate was
slightly higher than tree5's under the stated loads, but it is not a complete
accepted baseline result. N2 and no-worse burst Pre performance remain
unestablished.

The additional tree4 run raised observed per-listener peaks to **37,215 queued
bytes / 121 notifications** and **521 committed application entries**. Its
maximum receipt age was **12.528 seconds**, fixed journal-barrier wait **12.393
seconds**, and response latency **12.298 seconds**, with zero capacity waits or
oversized handoffs. These values further qualify the cooperative 2 ms budget;
they are not hidden by the faster first three samples.

### Round 19: local-stop, fairness and active-reader measurements

Round 19's initial accepted probe generation was
`f83fef3e35f64f1bf7a7cb8d5f042866a327717158511410e432eaa04782ec4a`.
The resumed targeted fixes use runtime manifest
`72d6abda8281aac502d1956df844a59be27d06f0dad72d09aec573c6970d0e4e`.
The complete source manifests, commands, actual exits and retained attempts are
in `r247-w/round19/implementation-report.md` and `selected-index.json`. README
measurement updates are recorded separately from each frozen runtime. Earlier
generations are historical evidence, not acceptance of the resumed source.

The three arranged pre-edit controls exited 1 with their required failure
discriminators. J1 rejected a real deferred-FK COMMIT while an already committed
turn completion awaited terminal inventory on the same connection; it produced
a failed applied prefix, reconcile failure and checkout-wide UNKNOWN. R1
observed the peer's actual response behind a capacity pause, but the call timed
out after 60.002637 seconds. S1 recorded 161 busy samples in which both cheap
application workers made no progress while the delayed worker advanced. Only
the heavy application steps received the synchronous 3.5 ms delay; all three
listeners had 1,213-entry streams. Thus "light" in this probe describes service
cost, not a smaller message count. An earlier S1 measurement attempt failed
because its sampler used the wrong queue field; it is not counted as a red.

| Probe on f83fef3e | Exit | Start / mean one-minute host load | Result |
| --- | ---: | --- | --- |
| J1 | 0 | 31.426 / 31.572 | Visible stop, then replay in 0.665281 s; no failed prefix, reconcile failure, UNKNOWN or startup incident |
| S1 | 0 | 32.216 / 32.535 | 150 busy samples; both cheap workers advanced 726 applications; zero service-order or zero-progress violations |
| S1 with real lead hooks | 0 | 35.540 / 34.786 | Pre 1.010142 s, Post 0.209196 s; verified edit; both cheap workers advanced 801 applications during heavy backlog |
| R1 | 0 | 33.079 / 34.471 | Same peer-observed request 14 resolved after 66.022798 wall seconds; queue high-water 522,993 bytes; all 179 receipts committed |
| Unanswered peer | 0 | 34.202 / 34.502 | `NATIVE_UNCERTAIN` after 60.000830 s, then an answered call after the owned peer resumed |

R1 asynchronously held the journal owner for more than 66 seconds while the
event loop and real native peer continued. It did not fabricate a response or
remove capacity backpressure. Its connection-wide response-wait totals were
66.105290 wall, 66.012000 reader-paused and 0.093290 active seconds across 14
responses. Those totals include setup calls and are not presented as the single
held call's active duration. One decoded 10,401-byte capacity waiter remained
outside the bounded queue, as the accounting contract describes.

The first implementation gave receivers a FIFO ticket for every message. Two
complete bursts exposed its receipt overhead, so the revised runtime lets a grant
serve the existing 2 ms read slice before rejoining FIFO. All decisive probes
and static checks above were rerun after this revision. These earlier burst
attempts are intermediate evidence, not final-runtime N2 acceptance:

| Attempt | Exit | Start / mean load | Notifications/min | Lead Pre max s | Outcome |
| --- | ---: | --- | ---: | ---: | --- |
| tree1 | 0 | 40.379 / 50.888 | 8,549.077 | 2.628265 | All core receipts applied, custody released, drain accepted |
| installed base2 | 1 | 31.051 / 47.378 | 10,625.692 | 5.312829 | Hook warning; incomplete catch-up/drain |
| tree3 | 0 | 30.821 / 43.130 | 5,190.175 | 3.561792 | All core receipts applied, custody released, drain accepted |
| installed base4 | 1 | 29.695 / 42.768 | 12,366.558 | 3.840891 | Hook warning; incomplete catch-up/drain |
| tree5 (f83fef3e) | 0 | 38.803 / 51.776 | 7,871.500 | 5.159960 | All core receipts applied, custody released, drain accepted |

Tree1/base2 had unmatched start loads. Tree3/base4 had matched start loads, but
base4 did not complete. Those baseline attempts retained the early-return
adaptation but still stopped on the separate fail-open warning assertion. The
base6 start record has no command result or retained case after the interruption;
its exit and completion are unknown. These attempts establish no qualifying N2
pair.

The resumed pass keeps the six-second owned-RPC stop bound. Round 18 passed the
same case in 1.742002 s. Two pre-resume Round 19 runs missed it at 6.027831 s and
6.043683 s. The prepared observation passed unchanged source at lower load in
1.562757 s, so it alone did not explain those misses. A targeted slow-read
injection reproduced the delay (exit 1, 6.005755 s) and captured shutdown waiting
on a new lead-command cancellation quarantine scan. That scan path also existed
in Round 18; the old failures did not retain wait traces proving their exact
timing. The fix leaves the already durable operation and custody for recovery.

| Targeted probe on 72d6abda | Exit | Start / mean load | Result |
| --- | ---: | --- | --- |
| Owned RPC, unchanged case 1 | 0 | 6.450 / 6.707 | Stop 1.023156 s; restart reconciled held custody |
| Owned RPC, unchanged case 2 | 0 | 6.941 / 6.672 | Stop 1.023549 s; restart reconciled held custody |
| Owned RPC, slow-read injection | 0 | 6.550 / 6.375 | Stop 1.021187 s; no new cancellation quarantine scan |
| Recovery with real non-stop `Retired` | 0 | 6.217 / 6.122 | No retained scopes; dependent snapshot, message retry and completion ran |
| Recovery with writer defect | 0 | 4.891 / 5.059 | Defect recorded; scopes cleared and dependents/completion ran |
| J1 restart | 0 | 6.131 / 6.152 | Replay 0.154208 s; no failed prefix, reconcile failure, UNKNOWN or incident |
| RPC local-stop envelopes | 0 | 8.031 / 10.198 | Request and `daemon/stop` answered; both idempotency rows stayed pending; restart caught up |

The L1 strict red used a real connection retirement during startup reconcile:
five scopes remained despite no stop, and dependent work and completion did not
run (exit 1). Its first attempt failed in the sampler and is not a strict red.
The first L2 attempt delivered both required envelopes but failed its inherited
two-second restart snapshot with two unapplied incidental receipts. The retained
green waits for actual application catch-up before the same zero-backlog check.
The original small-4 injection rejected the new effect callback before reaching
its intended kill point (exit 1). Its selected effect-stage adaptation preserves
the real kill and single-dispatch checks; it passed before interruption and again
on the resumed source (both exit 0).

The installed-only recipe `r19-installed246-burst-warning-recorded` now records
known hook warnings and early returns in `baseline_hook_events` and continues
through catch-up, release and drain. Tree assertions are unchanged. Its one
resumed baseline run exited 0, with independent count/application and drain-export
validators also exiting 0: 21,600 core notifications, 540 turns, 12,420 deltas,
no unapplied or failed work and no held custody. It measured 21,144.716
notifications/min and a 0.945398 s Pre maximum at start/mean load 12.137/11.125.
Drain was accepted on the third attempt after 40.091346 s; the first two retained
ownership with `LISTENER_DRAIN`. This run had no hook warnings or early returns,
so its exported event list is empty. Incidental events were one `skills/changed`
and 27 installed-runtime transport gaps during drain.

The user deferred N2 pairs, the 14-case F matrix and the full catalog until after
the next resource-handling round. No qualifying N2 pair or Round 19 F acceptance
is claimed; the earlier measurements and all failed attempts remain retained.
