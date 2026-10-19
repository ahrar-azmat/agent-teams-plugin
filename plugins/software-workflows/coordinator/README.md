# Writer coordinator — software-workflows 2.4.0 / P1

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
an orderly stop. The worker has a separate lock. Native listeners do not inherit
either descriptor. Repeated bootstrap discovers the existing daemon. PID files
and heartbeats are never authority to steal a lock or custody.

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
and unrelated hook trust. C1 passes session flags for approvals `never`, sandbox
`danger-full-access`, live web search, V2 agents, coordinator hooks, and disabled
TTY. It validates the effective modes and the start/resume policy echoes.
Incompatible managed settings cause refusal. This is cooperative coordination
with full access, not operating-system confinement against arbitrary code.

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
both source deletion and destination creation to the source pre-image. A partial
or interrupted group is quarantined in every affected checkout, with no successful
part receipts. Quarantine marks the parent and all parts in one transaction before
waiting for reconciliation locks. Completion re-reads their durable states inside
the receipt transaction: a quarantined invocation cannot become verified again.
Recovery does not grant group authorship retrospectively from
matching history. Restoring known bytes still requires a new verified edit to
resolve authorship; it never adopts unexplained bytes.

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
identified by **session plus unit**; reading does not acknowledge messages, and
another session gets another mailbox. Legacy pending messages and acknowledgment
records are preserved, including the raw RPC's legacy `lead` mailbox.
Without an explicit unit, `coordinator_inbox(session_id=S)` reads the union of
S's writer and lead mailboxes. Its acknowledgements check the owning session;
an explicit unit keeps the original single-mailbox scope. Lead-tool unread
notices use the same session union. Aggregation changes neither message IDs nor
recipients and never acknowledges a message by reading it.
The common lead-tool wrapper carries the caller's session through normal and
early-return notices, including Abraham completion, `codex_runs`,
`codex_resume_run`, `codex_cancel_run`, `codex_run_log`, and `code_review`.
Helpers inherit that call's session context. An explicitly empty session retains
the legacy `lead` mailbox; another session does not see this session's mail.

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
also counted separately. This refusal leaves admission unchanged. Otherwise,
new mutations receive retryable `DAEMON_DRAINING` while admitted requests and
completion calls finish. RPC settlement has a one-second bound; a timeout returns
`NOT_QUIESCENT` and immediately resumes admission without cancelling the requests.
Once settled, the daemon closes admission, detaches listeners and releases leases
before stopping. Listener identity and settlement safeguards still apply.

Host hooks use three-second client calls within a fourteen-second budget, below
their fifteen-second host timeout. If a call encounters an unavailable or draining
daemon while the client's `upgrade.lock` is held, the hook waits for that lock to
be released and retries once with the same host invocation identity. It reserves
time for the retry and, for Bash Pre, the arm call. Without a held upgrade lock it
takes the existing unavailable path immediately. A replacement cannot rearm a
Bash window that belonged to the retired generation.

| Method | Parameters and result |
|---|---|
| `daemon/status` | Optional `checkout`; generation, PID/birth, `boot`, `capabilities`, `draining`, readiness, version, writers, claims, custody, pending requests and messages. |
| `checkout/register` | `path`; serializes canonical registration and returns only a ready, complete baseline. Pre-existing dirty bytes are UNKNOWN. |
| `checkout/reconcile` | `checkout`, optional `paths`; returns new UNKNOWN IDs and scanned count. `resolve_quarantine:true` with a full scan clears a resolved I/O quarantine, never UNKNOWN history. |
| `checkout/revisions` | `checkout`, optional `path`, `cursor`, `limit`; paginated immutable revision records. |
| `run/start` | `checkout`, optional `mode`, `task`, `paths`, `model`, `fixture`; returns separate run, attempt, unit and actor IDs plus native policy echoes. `fixture` is only for an owned disposable provider manifest. |
| `run/status` | `run`, or optional `checkout`; persisted run or detailed checkout status. |
| `run/interrupt` | `run`; cancel the selected run's root/child turns and queued work, then verify process/terminal settlement. Custody and claims remain until reconciled. |
| `run/resume` | `run`, `text`; reconcile first, refuse unresolved recovery UNKNOWN, then explicitly start a new turn. |
| `run/terminals` | `run`; native terminal inventory. |
| `run/terminate-terminal` | `run`, `process_id`; native termination, followed by custody reconciliation. This ID is not an OS PID. |
| `lead/attach` | `unit`; create a lead actor in an existing unit, useful for recovery and C2 editor integration. |
| `claim/acquire` | `actor`, `paths`; all-or-none acquisition using the shared claim/custody conflict model. |
| `claim/release-request` | `actor`, `paths`; records a pending lead request and retains claims. |
| `claim/handback` | `unit`, optional `paths`; explicit lead release after operations/custody reconcile. |
| `command/declare` | `actor`, exact `command`, nonempty `reason`; grants one shared invocation to that actor. |
| `custody/reconcile` | `custody`; lead-requested process/terminal reconciliation. An uninspectable survivor keeps quarantine. |
| `lead-edit` | `actor`, `changes`; durably record complete intended images, install them, then verify bytes and modes. |
| `operation/prepare` | `actor`, `kind:"lead-edit"`, `changes`; record intent **before** an external editor writes. |
| `operation/complete` | `operation`; compare the actual result to the already recorded intent. An after-the-fact hash cannot establish authorship. |
| `lead-command` | `actor`, explicit `argv` array; opaque command with custody, actual exit code and output digests. |
| `unknown/list` | `checkout`, optional `paths`; unresolved UNKNOWNs, context and restoration links. |
| `unknown/restore` | `actor`, `unknown`, `revision`; restore a known receipt for a path. For checkout-wide `*`, use `revisions:{path:revision_id_or_null}` covering every ledger path. Null is accepted only for baseline absence. |
| `message/send` | `recipient`, `text`, required `wake:"none|queue|start"`, optional lead `sender`; durable message record. |
| `message/inbox` | Optional `recipient` (default lead), `cursor`, `limit`, `session_id`; a nonempty session selects all its unit mailboxes. Includes total unread count independently of pagination. Reading does not acknowledge or claim context entry. |
| `message/ack` | `message`, optional lead `actor` or `session_id`; a session may acknowledge only messages in its own mailboxes. Writers acknowledge through native-bound tools. |
| `message/wake` | `message`, `wake:"queue|start"`; explicitly wake a pending idle recipient. |
| `message/retry` | `message`; reconcile native history/queue before an explicitly requested retry of uncertain delivery. |
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
`node_modules`, virtualenvs and build output are never read or stored. A path
the ledger already holds stays in a full scan after an ignore rule covers it, and
registration also reads every path of the base tree. An embedded repository is
its own checkout; a submodule in the index is refused with its path. A path in
git's view that is not an ordinary single-link file is refused with that path;
ignore it or remove it. A tracked file that became a folder reads as deleted,
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
`read_file` returns base64 bytes with offsets and records the returned revision;
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

Hook waits are asynchronous: custody waits end before the 120-second native hook
timeout. Hook crashes/timeouts can still fail open in Codex. C1 compares native
effect items with successful recorded decisions, creates UNMEDIATED context and
UNKNOWNs, and retains the opaque operation window. Nonempty `write_stdin` remains
part of its existing command window; it has no native PreToolUse hook.

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
backwards. An inbox read changes none of these states.

A running root receives `turn/steer` with its expected turn ID. An idle root obeys
the sender's wake policy. A lost result is reconciled against full native history
and every queue page before a retry is considered. Native delivery is not promised
exactly once; application-level acknowledgement is deduplicated.

A child message has a separate root relay hop. The root is instructed to use its
native collaboration tools; its echo or acknowledgement cannot acknowledge the
child. A child without inherited dynamic tools can emit `C1_ACK <message-id>` in
its own native assistant message. Only that child's verified native origin can
complete the child acknowledgement. The lead inbox remains durable with no lead
client connected.

## Seals, reviews and publication

Sealing checks the root and all discoverable descendants, active turns, pending
hooks/tools, native terminals, process custody and unverified operations. It
refuses UNKNOWN paths and dependencies on unpublished other-unit revisions.
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

Worker generations and attempt generations fence interrupted work. Startup first
invalidates outstanding lead Bash windows, reestablishes canonical checkout ownership,
then reconnects registered listeners,
initializes/resumes without starting inference,
pages full history, resolves children lazily, replays durable received evidence,
checks terminals/processes and reconciles filesystem bytes. It then reconciles
seal attempts, owned index locks/publications and messages before normal dispatch.
A replacement listener and any changed implementation fingerprint require fresh
admission before work can continue.
Uncertain mutation requests are never automatically replayed.

Use `daemon/status`, `records/list`, `unknown/list`, and `run/terminals` to inspect
an incident. Terminate a known native terminal through the native endpoint. Use
`custody/reconcile` to request lead resolution after survivors can be proved gone.
Claims never expire because a heartbeat stopped. Quarantine cannot be cleared by
submitting a newer PID or an after-the-fact content hash.

UNKNOWN is permanent history. To resolve it, first reconcile custody and stop
active writers, reopen a sealed unit if necessary, then restore stored known
revisions with `unknown/restore`. A restore alone still blocks publication. A new
verified edit must follow; a checkout-wide gap requires that edit to cover the
entire restored scope. No `adopt`, risk acknowledgement or publication bypass is
provided. Missing/corrupt receipt data refuses verification or publication and
does not release claims.

Common refusal codes include `CLAIM_CONFLICT`, `OPERATION_BUSY`, `CUSTODY_WAIT`,
`LEAD_RECORDS`, `UNIT_FROZEN`, `STALE_BASE`, `REVIEW_BINDING`,
`UNKNOWN_BLOCKS_SEAL`, `UNKNOWN_BLOCKS_PUBLICATION`, `UNPUBLISHED_DEPENDENCY`,
`UNIT_INDEX_CHANGED`, `INDEX_LOCKED`, and `ADMISSION_EXEC_POLICY_UNOBSERVABLE`.
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

| State entry | Purpose |
|---|---|
| `ledger.sqlite`, WAL/SHM | Versioned schema, identities, revisions, events, UNKNOWNs, idempotency and journals; daemon only. |
| `blobs/<sha256>` | Immutable raw receipt bytes, fsynced before any reference is committed. |
| `listeners/<incarnation>/` | Persistent listener evidence; endpoint allocations live in the short runtime directory. |
| `indexes/` | Private candidate and publication index copies. |
| `schemas/<binary-digest>/` | Native generated experimental schema and completion marker. |
| `calibrations/` | Successful capability bindings and evidence locations. |
| `supervisor.lock`, `worker.lock` | Singleton lifecycle. |
| `endpoint.json`, `startup-error.json` | Private endpoint discovery and structured startup diagnostics. |
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
do not own its lifetime. An inherited, inode-verified venv `.in-use` descriptor
also survives worker replacement. Queued/uncertain deliveries and process custody
retain exclusion. Pre-production startup/admission failures clean up and name
the failed check before selecting exclusive exec fallback. Started or uncertain
native work returns its durable handle instead. Windows retains the existing
explicit write-mode refusal; its lock lifetime has not been implemented.

The server starts the coordinator on demand with its actual Python >=3.11
interpreter. Module resolution is independent of the target checkout. First
admission can take tens of seconds and is visible in progress and durable events.
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
and refuses active/uncertain turns, frozen units and UNKNOWN. Cancellation targets
the selected unit's actors, queue and terminals, and reports `cancelling` until
terminal events and verified process disappearance establish settlement.

| Lead MCP tool | Operations |
|---|---|
| `coordinator_claims` | list, acquire for this Claude session, release request, lead-authorized handback |
| `coordinator_send` | durable send to run/unit/actor/lead; explicit none/queue/start wake policy |
| `coordinator_inbox` | cursor read; acknowledge only explicitly supplied IDs |
| `coordinator_unit` | status, object diff, UNKNOWN, reconcile, restore, reopen, recover, abandon |
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

`hooks/coordinator_lead.py` runs under Python 3.9 for PreToolUse, PostToolUse and
PostToolUseFailure. It never starts a daemon or registers an unrelated checkout.
Write, literal Edit and ordered MultiEdit prepare a complete intended image and
bind it to checkout/session/tool-use identity before the host edit. Successful
completion verifies bytes, mode and preconditions; duplicate posts are idempotent.
Ambiguous replacements, failure, missing pre/post events or altered inputs grant
no receipt. Pending gaps are quarantined after 120 seconds or during recovery.
Encoding/BOM and CRLF preservation are checked by expected bytes; host behavior
that differs is refused or quarantined, never adopted after the fact.

**NotebookEdit is UNVERIFIED and not recorded**, including insertion. Its native
cell ID and serializer have not been calibrated through the installed host. On a
path another unit claims it is denied as CLAIM_CONFLICT; elsewhere the host's
edit goes ahead unrecorded, so what it wrote reconciles as UNKNOWN. The
refusal names a full-file Write route. Direct hook observations are not evidence
of the host's encoding/serialization behavior; see VERIFY.md for coverage.

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
