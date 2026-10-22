# Writer coordinator — software-workflows 2.4.4 / P1

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

Native writers retain the user's normal CODEX_HOME, provider, MCP configuration
and unrelated hook trust. The supervisor, the worker and every app-server they
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

No child process can block the worker's event loop. On macOS, CPython's threaded
child watcher returns when a child stops (`waitid` reports `CLD_STOPPED`) and the
loop then reaps it with a blocking `waitpid`, so one app-server, probe or lead
command stopped by a signal froze the daemon until it was continued or died. As the
worker starts, before any child, a probe shown red on the interpreter's watcher
installs one that waits for the exit through kqueue (`NOTE_EXIT`, which no stop or
continue fires) and reaps without blocking. Where a pidfd watches children (Linux)
the interpreter's watcher is kept. `daemon/status.child_watcher` reports what was
measured.

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
first, and the shim runs the real git behind it. The push gate's evaluation worker
lets a Bash command through whose only trigger is ANSI-C quoting (`$'`) once it
proves the command belongs to a closed grammar: allowlisted readers that cannot run
their arguments (`grep`, `egrep`, `fgrep`, `wc`, `head`, `tail` without following,
`cat`, `cut`, `tr`, `uniq`, `sort` without a compress program, `jq`, `ls`) and Docker
inspection (`ps`, `images`, `info`, `version`, `inspect`, `logs`, `volume ls`,
`network ls`, `stats --no-stream`, with no host, context or config override),
joined by `;`, `|`, `||` or `&&`, every `$` single-quoted data, and no substitution,
expansion, redirection or background job. A command it cannot prove keeps the
existing acknowledgement.

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
admission unchanged. Otherwise,
new mutations receive retryable `DAEMON_DRAINING` while admitted requests and
completion calls finish. RPC settlement has a one-second bound; a timeout returns
`NOT_QUIESCENT` and immediately resumes admission without cancelling the requests.
Once settled, the daemon closes admission, detaches listeners and releases leases
before stopping. Re-admissions and transport-gap reconciles under way end first, and
none starts while it drains: a writer whose connection drops then is left failed, for
the next daemon's recovery (or this one's maintenance, if the stop is refused). Listener identity and settlement safeguards still apply.

Host hooks use three-second client calls within a fourteen-second budget, below
their fifteen-second host timeout. If a call encounters an unavailable or draining
daemon while the client's `upgrade.lock` is held, the hook waits for that lock to
be released and retries once with the same host invocation identity. It reserves
time for the retry and, for Bash Pre, the arm call. Without a held upgrade lock it
takes the existing unavailable path immediately. A replacement cannot rearm a
Bash window that belonged to the retired generation.

| Method | Parameters and result |
|---|---|
| `daemon/status` | Optional `checkout`, `brief`; generation, PID/birth, `boot`, `capabilities`, `draining`, readiness, version, `child_watcher`, `recovering` (the writers, units and checkouts startup recovery is still reconciling), `ended` (writer app-servers the coordinator ended, by reason), `silent` (those not answering now), and unless `brief` the writers (each with its app-server's admitted `capabilities` and, when its probe fell back, `capability_fallback`) with their claims, custody, pending requests and messages. |
| `checkout/register` | `path`; serializes canonical registration and returns only a ready, complete baseline. Pre-existing dirty bytes are UNKNOWN. |
| `checkout/reconcile` | `checkout`, optional `paths`; returns new UNKNOWN IDs and scanned count. `resolve_quarantine:true` with a full scan clears a resolved I/O quarantine, never UNKNOWN history. |
| `checkout/revisions` | `checkout`, optional `path`, `cursor`, `limit`; paginated immutable revision records. |
| `run/start` | `checkout`, optional `mode`, `task`, `paths`, `model`, `fixture`; returns separate run, attempt, unit and actor IDs plus native policy echoes. `fixture` is only for an owned disposable provider manifest. |
| `run/status` | `run`, or optional `checkout`; persisted run or detailed checkout status. A run whose writer runs a turn after the run's own work ended (a message's turn) reads `running`, and `turns` lists each running writer turn (`actor`, `kind`, `thread`, `turn`, and for a root turn the `phase` or `message` that started it); the stored state is unchanged. `run/list`, the run's events and the checkout status report the same state. |
| `run/interrupt` | `run`; cancel the selected run's root/child turns and queued work, then verify process/terminal settlement, for a plain run as for a workflow. Its calls are refused (`RUN_CANCELLED`) until a resume. A lead-mode run has no writer turns (`RUN_LEAD_MODE`). Custody and claims remain until reconciled. |
| `run/resume` | `run`, `text`; reconcile first (replacing an app-server that is gone, not connected, admitted by another release, stopped by a signal, or alive behind a socket that refuses or closes on it), refuse unresolved recovery UNKNOWN, then explicitly start a new turn. |
| `run/terminals` | `run`; native terminal inventory. |
| `run/terminate-terminal` | `run`, `process_id`; native termination, followed by custody reconciliation. This ID is not an OS PID. |
| `lead/attach` | `unit`; create a lead actor in an existing unit, useful for recovery and C2 editor integration. |
| `claim/acquire` | `actor`, `paths`; all-or-none acquisition using the shared claim/custody conflict model. A path outside the ledger is neither claimed nor checked against other claims; the result lists it under `outside`. |
| `claim/release-request` | `actor`, `paths`; records a pending lead request and retains claims. |
| `claim/handback` | `unit`, optional `paths`; explicit lead release after operations/custody reconcile. |
| `command/declare` | `actor`, exact `command`, nonempty `reason`; grants one shared invocation to that actor. |
| `custody/reconcile` | `custody`; lead-requested process/terminal reconciliation. An uninspectable survivor keeps quarantine. |
| `lead-edit` | `actor`, `changes`; durably record complete intended images, install them, then verify bytes and modes. A path outside the ledger is installed but not recorded; the operation (each part, in a project) lists it under `outside`. |
| `operation/prepare` | `actor`, `kind:"lead-edit"`, `changes`; record intent **before** an external editor writes. A path outside the ledger is neither recorded nor verified; the operation lists it under `outside`. |
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
(recovery, resume, re-admission, cancel, a transport gap) and reconciles the
incarnation that is current once it holds it, so one replacement owns each
transition. A reconcile that fails after recovery marks the writer failed
(`listener/reconcile_failed`), and maintenance reconciles it again after 5 s,
doubling to 5 min; its unit's work waits meanwhile. A finished run's writer is retried
only while work is owed to it: its unit is not quiet, or a native delivery to one of its
writers is in flight or pending with a wake. A lead's mail never counts, since a lead
reads it through its inbox. Otherwise its next resume reconciles it. At startup, a
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
serves, and reconciles writers in the background, each run in parallel with the
others; what two runs share is serialized where it is shared (a checkout's locks, a
calibration's binding). Each run's writer app-servers are reconnected or replaced,
initialized/resumed without starting inference, read back (full history paged,
children resolved lazily, durable received evidence replayed, terminals and
processes checked) and its workflow advanced. Each checkout's filesystem bytes are
reconciled after every run in it, each unit's publications and owned index locks
after its runs and checkouts, and messages last. Measured with 13 writers in 4
checkouts, the daemon served after about 2 s and recovery completed after about 55 s
(VERIFY.md). A plain run whose turn ended while no daemon read its app-server takes
that turn's state from history. A writer whose startup reconcile fails is quarantined
with a checkout-wide UNKNOWN in each of its unit's checkouts; a resume reconciles it
again, and is refused `RECOVERY_UNKNOWN` until that UNKNOWN is narrowed or restored.

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
`unknown/restore`. A restore found at the baseline afterwards (every path of it, for
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

All nine required probes observe a bad configuration before accepting its good
counterpart: hooks, native evidence, dynamic tools, delivery, TTY, reconnect,
terminals, policy and outbound validation. `exec_policy` is an additional probe.
Only completed successful calibration is reusable under its fingerprint. A
separate C1-owned red/green Git probe proves that missing objects cannot invoke
lazy-fetch helpers during helper-free reads.

Admission also probes one optional capability for each writer binding, `inject`,
once, after the required probes; it is cached with the binding and never a refusal.
Its red is a `turn/steer` of a note while a code-mode cell runs: the request after it
must show the cell still running, the preemption the detector looks for. A
`thread/inject_items` into the same running cell must leave the cell to complete, be
carried by the next request and start no other turn; an inject on an idle thread
must start no turn within 1.5 s and be carried by the next turn's first request. The
verdict is `active` when the red came back red and both other parts held, `idle`
when the red came back red and only the idle part held, and none otherwise (notices
are then held). A probe that raised or timed out proves neither way and takes
`idle`, recorded with its error in `capability_fallback`, and is not probed again for
that binding; each of its steps has its own bound, so it always ends. The probe adds
about 7 s to a binding's first admission and nothing to a cache hit;
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
exited. An effect-capable Pre hook holds it, under the lock its release takes, until
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
Capability cache keys exclude random endpoint observations and use a per-key
calibration lock; every listener still proves effective policy, hook trust and
writer tool catalogs. The measured native execution-policy representation has
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

## Claude lead edit hooks

`hooks/coordinator_lead.py` runs under Python 3.9 for PreToolUse, PostToolUse,
PostToolUseFailure and PostToolBatch. It never starts a daemon or registers an
unrelated checkout.
Write, literal Edit and ordered MultiEdit prepare a complete intended image and
bind it to checkout/session/tool-use identity before the host edit. Successful
completion verifies bytes, mode and preconditions; duplicate posts are idempotent.
Ambiguous replacements, failure, missing pre/post events or altered inputs grant
no receipt. An edit that failed (PostToolUseFailure) or never ran (its batch shows
it with no Post), and is still at its pre-image, is settled as no effect; any other
bytes are quarantined (UNKNOWN). A prepared edit that no Post or batch settles within
120 seconds is quarantined. Elapsed time is no evidence that the host will not still
run it: a permission prompt or a slower PreToolUse hook can hold the call longer. It
stays live, so overlapping operations are refused (`OPERATION_BUSY`) and its claim
cannot be handed back. A Post that comes later grants no receipt: the lead restores
the path (`unknown/restore`) and makes the edit again. During recovery such an edit
is quarantined.
Encoding/BOM and CRLF preservation are checked by expected bytes; host behavior
that differs is refused or quarantined, never adopted after the fact.

**NotebookEdit is UNVERIFIED and not recorded**, including insertion. Its native
cell ID and serializer have not been calibrated through the installed host. On a
path another unit claims it is denied as CLAIM_CONFLICT; elsewhere the host's
edit goes ahead unrecorded, so what it wrote reconciles as UNKNOWN. The
refusal names a full-file Write route. Direct hook observations are not evidence
of the host's encoding/serialization behavior; see VERIFY.md for coverage.

Claude Code (2.1.306, measured) sends neither PostToolUse nor PostToolUseFailure for
a call that another PreToolUse hook denied (the push gate, a project hook), that a
permission refused, or that the user rejected. Such a call shows only in
PostToolBatch's `tool_calls`, sent once every call of a batch has resolved; without
it, the call's record would hold every lead's quiet window and `daemon/stop`
(`lead_hooks`) until its deadline, up to 180 s. Each lead call the coordinator may
hold something of leaves a marker in `<state>/lead-hooks/`, named by the SHA-256 of
its session and tool-use id, made before its Pre's call and removed once a Post
settles the call or the daemon decides it. PostToolBatch asks the daemon
(`lead-hook/dispatch`) only about calls whose marker is left, one at a time while its
time lasts; the daemon admits these as completion calls during a drain, as it does a
Post. Each is settled as its Post would be, from what shows. A Bash call is
reconciled with no `lead-command` credit: a batch is no evidence the command ran, so
what changed in its window is nobody's, as at its deadline. An edit is decided by its
bytes: no effect at its pre-image, verified at its intended image, quarantined
otherwise. A batch whose calls all settled at their Posts makes no daemon call and
no scan. Its timeout is the other lead hooks' 15 s, under the same budget: a 3 s
daemon call starts only while a whole one fits before 14 s, so a hung daemon ends
the batch by 14 s after at most four calls, and a call left over keeps its own
deadline. A marker its session never settled (a session that ended mid-call, or a
daemon down at both events) is dropped by a later Pre once it is a day old, in a
sweep at most hourly; one dropped early only leaves its record to its own deadline.

The live-view tail that `hooks/live_view_nudge.py` asks a lead to start
(`tail -F "$HOME/.claude/logs/codex-oracle/stream.log"`, in the background) reads one
log file and writes only its own output, so it cannot touch a checkout. A lead Bash
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
of tens of thousands of files), no lead command is credited. When the coordinator is down, lead edits are allowed with one
warning per session in private external warning state. Later reconciliation must
expose unexplained effects. No supplied transcript path is read.

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
