# Changelog

All notable changes to the plugins in this marketplace are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [software-workflows 2.4.20] — 2026-10-29

Two changes, both in the custody of a writer's commands (2.4.17's known limits): a process is matched against a
record by its identity, pid and birth together, and a local command whose app-server has exited is ended by that exit.
(`processes.py`, `store.py`, `daemon.py`; coordinator/VERIFY.md, 2.4.20.)

### Fixed: a writer's live MCP server held as a command's survivor
- Found live on 10-29, read-only: custody `83a293c0` held `docker` and `docker-mcp`, the MCP server of its writer's
  current app-server, as a command's survivors, for as long as that app-server ran.
- `processes.inventory` leaves a listener's services and helpers alone, and `observe_processes` passes those of every
  listener (3,673 on the user's ledger). The inventory keyed them by pid alone. A dead listener's record of another
  process that had once held the same pid (5.7 days old) won the map, so the live server matched no service and was
  taken as the command's. The processes a command was seen with before (`known`) were keyed the same way.
- Now both are matched by pid and birth together. A record naming a pid that another process now holds never decides,
  and no identity is read per service record any more.
- Building that list decoded every listener record on the event loop at each command's process inventory: 142 ms on
  the live ledger (3,686 records, 45 MB, read-only). The store now keeps each listener's service and helper identities
  as the listener is written (`Store.listener_processes`). Its first call reads them from SQLite without decoding the
  records, and so does the first call after a transaction that wrote a listener rolls back.
- Measured (VERIFY 2.4.20 (A), (B)):
  - on real processes, a stand-in app-server with a service and a genuine survivor, and a stale record of the
    service's pid with another birth after it: 2.4.19 took the service as a survivor, this release only the survivor;
  - at the live ledger's listener shape (3,686 records): 153 ms for 2.4.19's list, 52 ms for the first call, 0.44 ms
    after, the same identities; and the kept identities equal what SQLite holds after plain writes, a commit, and
    rollbacks with and without a listener written.

### Fixed: a command whose app-server had exited held its custody for good
- Found live on 10-29, read-only: custody `139aac4d` and `83a293c0`, of two commands run at 08:33 and 08:37 that had
  returned to their code-mode cells. The app-servers that ran them were replaced at the 08:41 switch, before their
  Post hooks or completed items were read. Settling waited for that end (its result, Post hook or completed item),
  which no app-server can deliver once the one that ran the call has exited; a read of native's history after that
  exit found none. So both stayed held for 8.5 hours.
- The cancels of both runs waited for that custody since 09:34, and each cancel pass reconciled its writer with a full
  scan of the checkout every few seconds. The startup incidents of the same checkout (`RESTORE_CUSTODY`) wait for it
  too.
- Now a local command whose call returned, with no process or terminal of it left, is ended by its app-server's exit
  once native's end of it can no longer be read (`Daemon.native_end_lost`): the app-server that ran it is not its
  writer's now and was ended (`dead` or `stopped`, so what it sent was applied), its exit is evidenced
  (`processes.exit_evidence`), and a read of the call's turn in native's history made since then (history's closure
  read) did not find the end. Each of those is read again after the last await, and the command is ended with no
  await between (review turn 116). Its completed item records `observedGone`, `missingNativeCompletion` and
  `executorExit` (the evidence), and its custody is released after the opaque-effects scan, as any command's
  (`custody/ended_with_listener`).
- An MCP call is never ended so: its server, an HTTP one, can go on with the call after the app-server exits. It keeps
  its custody until its own end or a lead's reconciliation, as before (review turn 116).
- What the command may have done unobserved is its app-server's UNKNOWN. An app-server's death records one for the
  whole checkout of each command it held (`listener died with potentially escaped effects`). A checkout with no
  checkout-wide UNKNOWN naming that app-server since the command began, as after a stop by the coordinator, has one
  recorded then (`a command's app-server exited before native's end of it was read`).
- Measured (VERIFY 2.4.20 (C)) on a real daemon and a real Codex writer, whose command's end was dropped and whose
  app-server was then killed:
  - this release ended the command, released its custody and reported the run cancelled at once, with 2 reconciles
    and 3 scans after the cancel and no UNKNOWN beyond the app-server's own;
  - 2.4.19 left the run cancelling and the custody held for the probe's 150 s, reconciling the writer and scanning
    the checkout 69 times meanwhile, and refused the stop (`NOT_QUIESCENT`): its supervisor had to be signalled;
  - with every checkout-wide UNKNOWN naming the app-server left out (as after a stop), this release recorded one, naming
    the custody, the app-server and the operation.

### Known limits
- The first `Store.listener_processes` call after a start, and after a rolled-back transaction that wrote a listener,
  reads every listener record's service and helper fields on the event loop (52 ms at the live shape). Listener
  records are never deleted, so what the store keeps grows with them (4,213 identities at that shape).
- An MCP call whose app-server exited before its end was read keeps its custody until its own end or a lead's
  `custody/reconcile`, as before. A command whose writer has no connected app-server is settled by no one, as before.
- The rest of the series stands: 2.4.21 `RESTORE_CUSTODY` waiting for custody, cancel passes and `NATIVE_RPC`, and the
  startup incidents' listeners, quarantined with their app-servers still running; the whole-family replies of
  2.4.18's known limits; then shared dependency evidence, native compaction and archive retention, reclaiming the
  file's space, and parking and resource budgets.

## [software-workflows 2.4.19] — 2026-10-29

One change: an append to the owner journal, and the supervisor's memory of it, no longer cost more as its history
grows. A start and a client's control read still scan every record, the control read decoding only the control
ones. (`owner_journal.py`, `owners.py`, `client.py`, `leases.py`; coordinator/VERIFY.md, 2.4.19.)

### Fixed: the supervisor's journal cost grew with every record
- Measured on the user's live journal on 10-29, read-only, at 55,932 records (60.5 MB of 128 MiB):
  - every append refolded the journal's state from every record kept: 17 to 20 ms an append;
  - every record stayed decoded in the supervisor until a compaction (past 64 MiB): reading them took a process from
    24 to 309 MB resident, and the live supervisor held 227 MB after its start;
  - `Client.ensure` (every CLI `rpc`, every MCP server call that starts the coordinator), `Client.start` and
    `Client.resume`, `leases.control` and `Custodian.repair` read and decoded the whole journal for two control
    values: 0.77 s each time.
- A hook helper's `GONE` family is kept for the journal's three-day retention, and the user's sessions finish about
  500 helpers an hour. Each took 1,137 to 1,161 bytes in a compaction, its member, supervisor and worker identities
  included, though only the handle that launched it asks about it, and only for its root, state, exit evidence and
  return code.
- Now:
  - the journal keeps its state, not its records: an append applies its own record (`fold`), and a start reads the
    journal record by record (`replay`);
  - those clients read `control_state`, which checks every record's frame, sequence and CRC but decodes only the
    control records (`CUSTODIAN`, `INHIBIT`, `WORKER`, `ADOPTED_WORKER`, `WORKER_GONE`);
  - a compaction keeps a `GONE` family that is no listener's in a retained form: its root, state, exit evidence,
    return code and `gone_at`, with an empty member list, its member, supervisor and worker identities dropped
    (`retained`). The supervisor's memory takes that form too after the compaction (`Owner.prune`). A listener's
    family stays whole.
- Measured (VERIFY 2.4.19) against the installed 2.4.18, on journals written as the live one is (helper lifecycles of
  7 records):
  - at 70,017 records (69.5 MB): an append 0.03 ms (p99 0.07) instead of 20.5 (p99 103); opening the journal 74 MB
    at its peak instead of 355; `control_state` 0.20 s where `inspect` takes 0.53; a compaction 4.59 MB, 458 bytes a
    family, instead of 11.39 MB, 1,137 bytes;
  - a real daemon started on that journal: its supervisor 105 MB resident instead of 386, its first status (the start
    included) 1.53 s instead of 4.16, a status after 0.07 s instead of 0.15;
  - on the user's live journal (read-only): `control_state` and `inspect` answer the same, in 0.032 s and 0.089 s.

### Known limits
- A journal is still read whole at a supervisor's start (0.53 s at 69.5 MB), and between compactions it grows to half
  its size. The supervisor's memory then holds every family the journal does, one dict per family (105 MB at 10,014).
- The whole-family replies of 2.4.18's known limits (`spawn`, `adopt`, `snapshot`, `end`, `end-member`) are not
  bounded yet. Queued.
- The rest of the series stands: 2.4.20 the custody of 2.4.17's known limits; 2.4.21 `RESTORE_CUSTODY`, cancel passes
  and `NATIVE_RPC`, and the 12 startup incidents' listeners, quarantined with their app-servers still running (12 of
  the 14 app-servers after the 2.4.18 switch, which held 1,163 MB resident together); then shared dependency evidence,
  native compaction and archive retention, reclaiming the file's space, and parking and resource budgets.

## [software-workflows 2.4.18] — 2026-10-29

One change: the supervisor lists the families its journal keeps a page at a time, instead of all at once in a status
that could not be sent. `daemon/status` answers again, and with it `Client.ensure`, every CLI `rpc`, the MCP
server's calls that start the coordinator, and `daemon stop`. (`owners.py`, `daemon.py`; coordinator/VERIFY.md,
2.4.18.)

### Fixed: every daemon/status failed once the owner journal kept ~6,400 finished families
- `daemon/status` includes the supervisor's `owner/status`, which comes over the owner channel, whose frame is 1 MiB
  (`leases.MAX_FRAME`). Through 2.4.17 that status listed every family the journal holds, `GONE` ones too.
- A `GONE` family that is not a listener's stays for the journal's retention, three days by default (2.4.12), and the
  user's sessions finish about 500 hook helpers an hour. On 10-29 at 15:30 the live journal held 6,652 `GONE`
  families, the oldest 13.2 hours old.
- At 15:07 the list held 6,463 families (6,446 `GONE`, 6,437 of them helpers) and encoded to 1,064,936 bytes. The
  supervisor's `send` refused the frame (`ValueError`), so `owner/status` answered `OWNER_OPERATION`, and every
  `daemon/status` failed with it:
  - `Client.ensure` asks `daemon/status` first, so every CLI `rpc` and every MCP server call that starts the
    coordinator failed;
  - `Client.stop` asks it before stopping, so `daemon stop` failed and the switch to 2.4.17 could not begin (2.4.17 is
    installed; the live daemon is still 2.4.16);
  - the start's sweep of abandoned launches read the same list and recorded `owner/forget_failed` (stage `status`).
  The lead hooks call their RPCs directly and kept working.
- Now `owner/status` counts every family by kind and state (`counts`) and lists those not `GONE` a page at a time.
  A page holds up to 512 KiB of entries (`PAGE_BYTES`, half the frame), in listener order, and `next` names where the
  following page starts. `daemon/status` shows the first page; `supervision_after`, the `next` of the page before,
  continues. A cursor that is not a string of at most 256 characters (`SUPERVISION_CURSOR_CHARS`) is refused
  `PAGE_CURSOR`: a longer one would only pass the owner channel's request frame. Not `GONE` is no live process: a
  family whose every member has exited stays `REAPING` while its `FAMILY_GONE` cannot be written (a journal past its
  cleanup reserve), so that list too is bounded by the journal alone (Codex review turn 111).
- The `GONE` families of one kind are read with `owner/gone`, a page at a time in the same way. The start's sweep
  reads listeners that way.
- `daemon/status` no longer fails when it cannot get the supervisor's report. `supervision` then holds `error`
  (`code`, `details`), and the rest of the status answers.
- A reply the supervisor sends once a family has ended (to an `end` that waited for it) and cannot send, as a family
  past the frame, is answered `OWNER_OPERATION` as a failed request is. Through 2.4.17 that `ValueError` was raised
  out of the supervisor's loop, and ended the supervisor (review turn 111).
- Measured (VERIFY 2.4.18):
  - on a journal seeded with 7,000 helpers shaped as the live ones and 4,000 abandoned listener launches, 2.4.17
    failed every status and the stop `OWNER_OPERATION`, and its supervisor had to be signalled. This release answered
    with a supervision report of 999 bytes, forgot the 4,000 launches across pages and stopped gracefully; the whole
    run took 20.2 s, 17.7 s of it seeding the journal;
  - on a real `Owner` holding 7,000 each of `GONE` listeners, `GONE` helpers and `REAPING` helpers whose members had
    all exited, 2.4.17's one status reply was 3,482,471 bytes. This release's took 3 pages, the largest frame
    524,921 bytes, each family listed once.

### Known limits
- The owner journal's own cost grows with what it holds. Measured on the live journal, read-only, at 15:45 (by the
  2.4.16 code; this release's journal code is the same), at 55,932 records and 60.5 MB:
  - an append rebuilds the journal's state from every record: 17 to 20 ms;
  - every record stays in memory until a compaction, which runs only past half the journal (64 MiB of the live
    128 MiB). Reading them took a process from 24 to 309 MB resident, and the live supervisor held 173 MB;
  - `Client.ensure`, `Client.start`, `Client.resume`, `leases.control` and `Custodian.repair` each read the whole
    journal for its control records: 0.77 s and those 309 MB each time;
  - a helper's `GONE` family stays three days, though only the handle that launched it asks about it, within seconds.
  Queued for 2.4.19.
- `spawn`, `adopt`, `adopt-legacy`, `snapshot`, `end` and `end-member` still answer with the whole family, every
  member it ever recorded (`processes.lineage` keeps each identity it saw). A family past the frame is answered
  `OWNER_OPERATION` now, no longer ending the supervisor, but its stop then cannot complete. The largest such reply
  the user's journal could give is 9,014 bytes, 1/116 of the frame: a listener's `FAMILY_GONE` with 30 members
  (sequence 16776; review turn 112). Bounded receipts, with the member list read in pages where it is needed
  (parking), are queued with 2.4.19 (review turn 111).
- The series moves by one:
  - 2.4.19: the owner journal's cost;
  - 2.4.20: the custody in 2.4.17's known limits (services matched by birth; a call whose app-server exited ended by
    that exit);
  - 2.4.21: `RESTORE_CUSTODY` waiting for custody, cancel passes and `NATIVE_RPC`;
  - then shared dependency evidence, native compaction and archive retention, reclaiming the file's space, and
    parking and resource budgets.

## [software-workflows 2.4.17] — 2026-10-29

Two changes. Every coordinator tool result now fits what a writer's model is shown whole, and a completed call's
record keeps a bounded form of its answer instead of the answer itself. And a stop no longer waits for a writer whose
retry the policy is holding back. (`tools.py`, `store.py`, `daemon.py`, `routing.py`, `services.py`, `messages.py`,
`ledger.py`, `operations.py`, `recovery.py`, `runs.py`; coordinator/VERIFY.md, 2.4.17.)

### Fixed: tool results longer than a writer's model is shown
- Writers run gpt-6-astra in `code_mode_only`, so each coordinator tool returns into a code-mode cell. Codex 0.163.0
  shows the model a cell's output whole only up to a ceiling. Measured 10-29: whole up to 32,047 characters; from
  64,000 on, cut to about 40,150 with its middle left out (10,000 tokens, `unified_exec`'s
  `DEFAULT_MAX_OUTPUT_TOKENS`). A direct call was cut at about 48,000. The cell's own JavaScript sees all of a result.
- Through 2.4.16 nothing bounded a result:
  - `read_file`'s default page (65,536 bytes) encoded to 87,720 characters, and the model saw 40,150 of them;
  - `status` listed every unacknowledged message on the checkout, other writers' too, and every custody record whole:
    on the user's ledger one answer was 19.5 MB;
  - `inbox` returned up to 100 messages whatever their length.
- Now one budget for a result's encoded JSON: 32,000 bytes, 80 % of the ceiling, so a cell that prints one result
  keeps room for its own text. `SOFTWARE_WORKFLOWS_TOOL_RESULT_BYTES` sets it from 4,096 to 40,000; any other value
  refuses the daemon's start (`CONFIG`).
  - `read_file` checks its range before anything is reconciled or read. A page holds what the budget leaves after its
    fields (23,601 bytes). A larger `length` gets that page, with `next_offset` and a `limited` record (requested,
    returned, `page_bytes`, reason `TOOL_RESULT_BUDGET`), never a silent cut.
  - A later page passes `revision`, the one the first page returned. A file changed since is refused
    `REVISION_CHANGED`, naming its current revision. Through 2.4.16 the later page came from the changed file, with
    nothing to say so.
  - Only a page's bytes are read from its receipt, off the event loop. A receipt not verified since its file last
    changed is hashed first, 1 MiB at a time. Through 2.4.16 each page read the whole receipt, on the loop.
  - `status` shows the caller's own run first, then the other runs on its checkout in run id order, a page at a time
    (`cursor`). Each run shows its task (its first 400 bytes as encoded, the full length named), its state and its
    writer's, its claims, active custody (each command's first 200 bytes, the full length named) and pending requests
    in brief, and how many of its unit's messages are unacknowledged. A list too long for a page is cut, with its total
    named. The open UNKNOWNs per checkout take at most a quarter of the budget. Mail is read with `inbox`.
  - `inbox` and `list_paths` keep `limit` and end a page at the budget: `next_cursor` points after the last entry
    shown, and `limited` is set when fewer than asked were shown. Neither loads more than a page can show.
  - `inbox` shows each message's id, sender, recipient, state, delivery, wake, created time, notice and text, with its
    delivery attempts counted. Through 2.4.16 it showed the record whole, every attempt listed. Each field but the
    text is cut as an error's echo is (120 bytes, its full length named), whatever its type. A lead's `message/send`
    took any sender, and now refuses one that is not a nonempty string (`MESSAGE_ARGUMENTS`); a record stored before
    shows its sender as its JSON, cut. A message longer than a page shows the part of its text that fits
    (`text_chars`, `text_offset`, `text_next_offset`); `inbox` with `message` and `text_offset` reads the rest, for a
    message to the caller only.
  - A path no page can hold is shown alone, over the budget and named (`ENTRY_OVER_BUDGET`), so a walk never stops.
  - An error answer stays within the budget too. A caller's own string it echoes is cut to 120 bytes, its length
    named; details too large are left out, their size named. Through 2.4.16 `PATH_ESCAPE` echoed the whole path given.
  - A `read_file` offset or a `limit` past 2^63 is answered: an empty page, or a page. Measured by direct calls, which
    carry such a number; whether a code-mode cell can send one was not measured. Through 2.4.16 such a `limit` raised
    an error that nothing answered, so the call waited.
  - Every argument is checked against the tool's declared schema before anything is marked or read. Codex passes a
    dynamic call's arguments on as the model wrote them, unchecked (codex 0.163.0
    `core/src/tools/handlers/dynamic.rs:141`). A value of another type is refused `TOOL_ARGUMENTS`, naming the tool's
    own argument names (`invalid`, `missing`) and counting unknown ones. A project cursor is checked once decoded too:
    one whose JSON decodes to a lone surrogate, or nests too deep, is refused `PAGE_CURSOR`. A path the filesystem
    refuses (a name past its length limit, a folder that cannot be searched) is refused `PATH_INVALID`, and a file
    the coordinator cannot open `READ_FAILED`. Through 2.4.16 each of these raised an error that nothing answered, so
    the call waited: a list as `list_paths`' cursor, a 100,000-character relative path, an unreadable file, a claim of
    such a path, a service `kind` that is a list, a lone surrogate or a 200,000-deep nesting in a project cursor. A
    local resource failure while a receipt is written or read, or while a writer's patch is checked (`ENOSPC`,
    `EMFILE`, ...), is neither a refusal nor evidence: it stops native intake, as every local resource failure does,
    and what native sent is applied again after. A receipt that cannot be opened for want of a descriptor is no
    longer taken for a missing one (`BLOB_MISSING`), which answered the call and, from a scan or a publication's
    check, quarantined the checkout or the operations that wrote it. A patch of a writer outside a Codex project,
    checked then, is no longer quarantined for it, its paths left UNKNOWN, nor is a patch native refused settled from
    its logged receipt left to `background/failure` alone. A project writer's patch, which has parts even when it
    touches one checkout, is still fenced at the next start (Known limits).
  - `run/start` refuses a task that is not a string (`RUN_FIELDS`): every writer's `status` shows it. A run stored
    before with another type shows its task as its JSON, cut.
  - `send` answers the message's id, recipient, state, delivery and wake; through 2.4.16 it echoed the whole record,
    text included.
  - New arguments are optional, and none was removed or renamed.
  - `daemon/status` shows `tool_results`: results since the daemon started, the largest, how many came within 80 % of
    the budget, and per tool how many were limited to it or went over it.
- On a real daemon with real codex 0.163.0 writers (the fixture provider), each tool's whole answer and then a walk
  of every page, under 2.4.16 and this release:
  - a default `read_file` page: 31,855 characters, shown whole (2.4.16: cut to 40,150 of 87,720);
  - every page of a 1 MiB file: 45 pages, all 1,048,576 bytes, matching the file's FNV-1a; the largest 31,810
    characters (2.4.16: 16 pages, up to 87,725);
  - one page of a 128 MiB file: 1.6 MB of Python allocation (2.4.16: 134 MB);
  - a second page after its file changed: `REVISION_CHANGED` (2.4.16: the changed file's bytes);
  - `status` with 41 messages waiting: 452 characters (2.4.16: cut, from 60,844 tokens);
  - `inbox` (41 messages, one of 100,000 characters): 7 pages and one message read in parts, all 220,030 characters,
    the largest page 31,999 (2.4.16: one answer of 236,601, cut);
  - `list_paths` over 1,205 paths: 14 pages, the largest 31,989 (2.4.16: up to 36,228);
  - `send`: 166 characters (2.4.16: 416).
- At the smallest budget (4,096 bytes), with three writers on one checkout, every page of `status`, `inbox` and
  `list_paths` stayed within it and the walks reached every entry. 2.4.16's `status` there was 79,886 characters.
- And the cases Codex's review of the tree asked for, at that budget, each answered within it:
  - a task of 400 emoji, each 12 bytes as encoded: `status` 794 characters (the review's tree: over 4,096);
  - messages carrying 150 more delivery attempts each: every message walked, the largest page 3,405 (the review's
    tree: refused `PAGE_SIZE`, with no way past);
  - a 100,000-character revision, message id or path: 384, 258 and 145 characters;
  - by direct call, an offset of 10^19: an empty page (the review's tree: no answer at all); a `limit` of 10^19 for
    `list_paths` and `inbox`: a page each (2.4.16: no answer at all);
  - a list as `list_paths`' cursor, a 100,000-character relative path, a file of mode 000, a claim of a long path, a
    service `kind` that is a list: `TOOL_ARGUMENTS`, `PATH_INVALID` (`ENAMETOOLONG`), `READ_FAILED` (`EACCES`),
    `PATH_INVALID` and `SERVICE_SPEC`, each within 200 characters (2.4.16: none answered, each cell still waiting at
    31 s);
  - a message from a 5,000-character sender, and one whose stored sender is a list: shown as 120 characters with
    `sender_chars`, the largest page 3,800 (with the message view before this fix: 5,456, over the budget);
  - a run whose stored task is a number: every `status` answered, 1,966 characters (with the projection before this
    fix: never answered);
  - a project cursor decoding to a lone surrogate or nesting 200,000 deep: `PAGE_CURSOR` (2.4.16: no answer);
  - the receipt volume full while a page's receipt is written: native intake closed, as for every local resource
    failure (with the read before this fix: answered `READ_FAILED`, intake going on);
  - no descriptor left as an existing receipt is opened, by a read's page or by a scan checking it: native intake
    closed, nothing quarantined (with the receipt readers before this fix: answered `BLOB_MISSING`, intake going on,
    and through the scan the checkout quarantined);
  - no descriptor left as a writer's patch is verified: native intake closed, and the next worker applied the
    notification again and verified the patch 0.5 s later, its path's revision exact (with the verification before
    this fix: quarantined, two UNKNOWNs on its path, intake going on);
  - no descriptor left as a patch native refused is settled from its logged receipt: native intake closed, and the
    next worker settled it from its receipt 5.5 s later, no effect, no UNKNOWN (with the settlement before this fix:
    `background/failure` alone, intake going on).

### Fixed: each completed call's record kept its answer whole
- On the user's ledger, 34,874 call records held 3.93 GB, 66 % of all record bytes. `status`'s 702 answers held
  3.30 GB and `read_file`'s 9,455 held 573 MB; the largest record was 19.5 MB.
- A record now keeps what rebuilds the answer exactly when a native server repeats the request:
  - a `read_file` page, as its fields without the bytes plus the receipt range they came from (receipts are immutable,
    and nothing deletes one);
  - any other answer over 2,048 bytes, compressed;
  - a smaller one as sent, as every record kept it before.
  A repeated request is answered from the record, off the event loop, without running the tool again.
- On the probe above: 78 records, 91 KB, the largest 3.7 KB (2.4.16: 39 records, 4.3 MB, the largest 1.33 MB). Each
  answer, applied once more as a repeated request, was identical to the one sent (78 of 78). Each record, rebuilt from
  the state's files after the daemon stopped, matched the answer sent (78 of 78: 49 pages, 26 compressed, 3 as sent).
- A worker that ended just after saving a page's record left it complete, and the page rebuilt from it after the
  restart matched the file. One that ended just before left the call to be retired, as in 2.4.16.

### Fixed: a stop waited for a writer whose retry was backing off
- 2.4.16 stopped waiting for a writer the retry policy holds, but counted only a failure that cannot heal. A writer
  whose failure can heal, with mail owed to it, refused every graceful stop `NOT_QUIESCENT` until its backoff passed
  and a retry succeeded.
- Now a writer counts as held while its reconcile failed and is not due (`recovery.retry_due`), and while its demand
  start failed and is not due (`dormancy.start_due`). The next daemon keeps the failure until it is due, as in
  2.4.16, and then retries it.
- 2.4.16's overlap case (VERIFY 2.4.16 (F)): this release stopped gracefully, and the installed 2.4.16 refused the
  stop `NOT_QUIESCENT` (`deliveries: 1`). In both, the failure survived the overlapping gap and nothing was attempted
  before its deadline.

### Known limits
- A record this release writes cannot be replayed by 2.4.16, which would send the stored form as the answer. Roll back
  only with no call in flight.
- The 3.93 GB of answers already in the user's ledger stay as they are. Native compaction and reclaiming the file's
  space, later in this series, take them up.
- A thread begun before 2.4.17 keeps the tool schemas it started with, because Codex keeps a thread's dynamic tools
  from its start. Its model is not told about `status`'s `cursor` or `inbox`'s `message` and `text_offset`. The
  coordinator accepts them from any thread, checked against this release's schemas, and Codex passes a call's
  arguments on unchecked (codex 0.163.0 `core/src/tools/handlers/dynamic.rs:141`, read in the installed source; not
  measured with a thread begun before 2.4.17).
- Codex yields a running cell whenever a message is steered into its turn (`watch_user_input`, codex 0.163.0), and
  the cell's output then arrives by `wait`. That is Codex's own behavior: the probe above delivers every message
  before its first measured cell.
- The lead's own `daemon/status` is not bounded; it is the lead's RPC, not a model's tool result.
- `run/start` refuses `OPERATIONAL_FAILURE` (`PermissionError`, no path named) while the checkout holds a file the
  coordinator cannot read, as 2.4.16 does (found by this release's probe). Queued.
- A project writer's patch, checked while a local resource ran out, stops native intake as above, but the next start
  still quarantines it, its paths UNKNOWN, as 2.4.16 did at once. Such a patch has parts even when it touches one
  checkout (`operations.prepare`), and startup fences every operation with parts whose atomic completion did not
  commit (`recovery.recover`). Verifying one there from its durable completion evidence is queued (Codex review turns
  108 and 109; not measured: it needs a Codex project writer).
- Found live after the 2.4.16 switch, read-only, for 2.4.18:
  - of the 12 startup incidents, 7 became `NATIVE_RPC` (`thread/resume` refused, -32600) and are held;
  - 4 wait as `RESTORE_CUSTODY`, retried at each backoff, each retry a full readmission with every calibration probe
    before it is refused at its last step: 22 admissions in 32 minutes;
  - all of them are on one checkout, whose two custody records can never be released. One holds its writer's own
    MCP server (`docker mcp`) as a command's survivor, because a dead writer's service record with the same pid
    shadows the live one: the inventory matches services by pid alone. Both wait for the end of a call whose
    app-server was replaced before that end was read, and which no app-server can now deliver;
  - so their two runs stay cancelling, and each cancel pass reconciles its writer, with a full scan of the
    checkout, every ~5.3 s: 889 scans in 32 minutes.
- Writer records keep `process_lineage` inline. Queued.
- With 2.4.18 (the custody above) and 2.4.19 (`RESTORE_CUSTODY` waiting for custody, cancel passes, `NATIVE_RPC`)
  inserted, the rest of the series moves by two: 2.4.20 shared dependency evidence; 2.4.21 native compaction and
  archive retention; 2.4.22 reclaiming the file's space; 2.4.23 parking and resource budgets.

## [software-workflows 2.4.16] — 2026-10-29

Two changes. A writer readmitted again after a release change no longer runs the hooks of the release that first
admitted it, and is no longer refused `HOOK_DEFINITIONS` for it. And every caller that retries a writer on its own
schedule follows one retry policy, so a failure that cannot heal no longer starts app-servers again and again, fills
the ledger with refusals, or holds a stop. (`daemon.py`, `recovery.py`, `dormancy.py`, `memory.py`, `runs.py`,
`messages.py`, `writer.py`; coordinator/VERIFY.md, 2.4.16.)

### Fixed: a writer's second readmission ran the first release's hooks, and was refused
- On the user's coordinator, two writers first admitted by 2.4.11 were refused `HOOK_DEFINITIONS` at every readmission
  from 10-29 02:22. Their runs stayed cancelling, with 121 failed readmissions in 6.3 h, each with a new app-server.
- The cause:
  - a first admission sets the coordinator's hooks as dotted `hooks.PreToolUse` and `hooks.PostToolUse` keys;
  - a readmission added a `hooks` table naming its own release, and kept the dotted keys;
  - the store sorts keys, so at the next readmission the dotted keys came after the table in its `-c` overrides;
  - the app-server applies overrides in order, so the dotted keys won and named the first release's `hooks.py`;
  - admission found none of its own hooks, and refused.
- Measured on a real codex 0.163.0 app-server: with the table first and then the dotted keys, hooks/list listed the
  dotted keys' hooks. With the table alone, the dotted keys alone, or the dotted keys first, it listed the table's.
- A readmission now sets the hooks once, in the table, and drops the dotted keys. The refusal records how many session
  hooks were listed and how many run this release's `hooks.py`.
- On a real daemon, a writer admitted by the installed 2.4.14 was readmitted by the installed 2.4.15:
  - its first readmission passed and its second was refused `HOOK_DEFINITIONS`;
  - this tree then readmitted that same writer, healing the state 2.4.15 left;
  - readmitted twice by this tree from 2.4.14 instead, it passed both times.

### Fixed: writers that could not start were started again without end
- Each failure is classified retryable or not (`retryable_failure`), but the callers did not apply it:
  - a cancel's settlement checked only the backoff;
  - a demand start (a waking message, a workflow's advance) was made again at every pass.
- On the user's ledger:
  - four runs' demand starts were made 1,072, 2,012, 2,774 and 2,788 times, each failing `OWNER_UNREGISTERED` or
    `HOOK_DEFINITIONS`, each with a new app-server;
  - the two cancelling runs' turns were refused 3,734 interrupts in 5.6 h.
- One policy now, for every caller on its own schedule: startup recovery, maintenance, a cancel's settlement, a
  transport gap, a workflow's submission, and demand starts:
  - a failure that can heal is retried after the backoff, counted over consecutive failures;
  - one that cannot heal is tried once by each new release, since its cause may have been the coordinator's own
    (`HOOK_DEFINITIONS` was);
  - after that, only a resume tries it;
  - a resource refusal backs off too, and one the memory gate made, directly or in every failed case of a
    calibration, is also due as soon as the gate would admit a launch (`Memory.admits`, read without side effects);
  - a stop's own refusal (`DAEMON_DRAINING`) fails no demand start: it stays pending for the next daemon.
- The policy is decided under the run's reconcile lock, on the incarnation current by then, so a pass queued behind
  one that failed (a cancel's behind a transport gap's) is not made.
- A writer whose reconcile failed keeps that failure across a restart and a transport gap until the policy makes it
  due: neither marks it reconciling nor tries it before then. A gap is applied to the writer's record as it is when
  the gap is handled, so a gap that waited behind a failing reconcile keeps that failure too. A startup incident
  rebuilt from an earlier release's quarantine keeps that failure's release (none before 2.4.16), so this release
  still tries it once.
- A cancel interrupts a turn only through a live connection. A turn whose writer is not connected is interrupted after
  the writer reconnects, with no event at each pass.
- A stop no longer waits for a held writer's workflow or its mail not yet sent, nor for its turn once its app-server
  has exited with no live connection. A live app-server's turn is still waited for: a hold does not show that a turn
  ended. Custody, operations and calls in flight are still waited for, and the next daemon takes the rest up. Through
  2.4.15 such a writer refused every graceful stop.
- On a real daemon, each case was run under both releases with an injected readmission failure that both classify
  permanent (a third coordinator hook):
  - a cancelled run whose writer died mid-turn: in the cancel's 30 s and the 60 s after, 2.4.15 made 24 app-servers,
    each a failed readmission, and recorded 44 refused interrupts; this tree made none of either;
  - a waking message to a parked writer: in 60 s, 2.4.15 made 46 starts, each with an app-server; this tree made one;
  - stopping afterwards: 2.4.15 refused `NOT_QUIESCENT` both times; this tree stopped gracefully both times.
- And the cases Codex's first review of the tree asked for, once its four findings were fixed:
  - a run cancelled during its writer's first readmission: in the 40 s after, 2.4.15 made 20 failed readmissions;
    this tree made one;
  - a permanent failure after the replacement was bound: 2.4.15 tried 4 more times in 15 s and once more at the next
    start; this tree tried none until a resume, and a resume without the fault reconciled the writer;
  - an Abraham workflow whose writer is held: 2.4.15 refused the stop `NOT_QUIESCENT`; this tree stopped gracefully;
  - a rebuilt pre-2.4.16 quarantine: this tree tried it once, and zero times when this release had already tried it;
  - a memory refusal: the retry began 0.5 s after the gate reopened (2.4.15: 57 s later, at its backoff); a waking
    message under the shut gate: one start in 20 s (2.4.15: 20), and the next began 0.2 s after the gate reopened.
- And the cases Codex's second review asked for:
  - a live writer mid-turn whose reconcile failed permanently, then lost its connection: this tree refused the stop
    `NOT_QUIESCENT` and left its app-server running; with that turn counted gone without exit evidence, as the
    reviewed tree did, the stop ended the app-server mid-turn;
  - a failure that can heal, then a gap or a restart before its deadline: this tree made no attempt; 2.4.15 made one
    at the gap, and at the restart launched two app-servers;
  - a memory refusal inside calibration (`ADMISSION_FAILED`): the retry began 0.5 s after the gate reopened, with
    59.9 s of backoff left (2.4.15: at its deadline).
- And the case its third review asked for: a gap read before a failing reconcile saved its failure, and applied
  after. This tree kept that failure (its retries and deadline) and made no attempt. With the gap applied to the
  record read before, as the reviewed tree did, and in 2.4.15: one more attempt 0.2 s after the gap.

### Known limits
- On the user's coordinator, 2.4.16 tries each of the 12 permanent startup incidents once. Two are the
  `HOOK_DEFINITIONS` writers, which this release fixes. Ten are `OWNER_UNREGISTERED`, from the owner journal moved aside
  at the forced 2.4.11 switch. 2.4.13's reading of unheld families may now pass them; any that fail again wait for a
  resume.
- A cancelled run whose writer is held stays cancelling until a resume or a new release: nothing can read whether its
  turn ended.
- A writer held after its replacement was bound keeps that app-server alive, idle and refused by every hook, until a
  resume or the next release; after a restart it is not reconnected. Ending idle app-servers is 2.4.21's parking work.
- A stop still waits for mail owed to a writer whose failure can heal, while its backoff runs: it is refused
  `NOT_QUIESCENT` until a retry passes, as in 2.4.15. Queued.
- Writer records keep `process_lineage` inline: on the user's ledger one record holds 7,656 processes (2.2 MB), and
  is rewritten at each command's process inventory. Queued.
- Codex's architect review of 10-29 sets the order of the rest: 2.4.17 bounded tool paging and stored tool responses,
  2.4.18 shared dependency evidence, 2.4.19 native compaction and archive retention, 2.4.20 reclaiming the file's
  space, 2.4.21 parking and resource budgets.

## [software-workflows 2.4.15] — 2026-10-29

Two changes in the coordinator, both to load it made for itself. Writers' app-servers no longer send it
`turn/diff/updated`, which repeated each turn's whole diff at every change: 87% of what the user's ledger grew by,
about 2 GiB a day. And the Codex Office's poll no longer holds its event loop for seconds at a time. (`native.py`,
`store.py`, `runs.py`, `participants.py`, `dormancy.py`, `daemon.py`; coordinator/VERIFY.md, 2.4.15.)

### Fixed: the ledger grew about 2 GiB a day from a notification nothing read
- The coordinator journals every notification a writer's app-server sends before applying it. `turn/diff/updated`
  carries the turn's whole unified diff and comes again at every change. Nothing applies it, and the run log keeps
  each file's diff in its `fileChange` item. In the 6.5 h to 10-29 07:32 it was 895 rows and 535 MiB, 87.3% of the
  native bytes journaled; since 10-19, 2.53 of the 6.97 GiB.
- The coordinator now opts out of it when it initializes a connection that can run a turn: writers, admission probes
  and history readers (`optOutNotificationMethods`, codex 0.163.0). The app-server never sends it.
- On a real codex 0.163.0 app-server, one turn that adds a file: with 2.4.14's initialize, 4 `turn/diff/updated`
  arrived; with the opt-out none, and every other notification the same number of times, the `fileChange` item with
  its diff included. On a real daemon the installed 2.4.14 journaled those 4 rows for the same turn; this tree none.

### Fixed: the Codex Office's poll held the coordinator for seconds at a time
- While its page is open the Office asks for `run/list` (up to 100 runs) and a brief `daemon/status` every 2 s, and
  gives up after 2 s. On the user's coordinator `run/list` took 7 to 30 s and the brief status 0.2 to 7 s. Neither
  freed the event loop until it was done. The loop stalled 0.75 s or more about every 3.5 s, 4,641 times in 6 h, and
  every writer's and lead's hooks, native intake and replies waited behind it.
- `run/list`:
  - each unit's sum of claim and custody revisions comes from a covering index of those two kinds, not the records
    (every command leaves a custody record, and `revision` follows `data` in a row);
  - the unread-mail count comes from a partial index;
  - UNKNOWNs are counted, not read;
  - the listener of each listed run is read in SQL, without its writer profile (`run/events` and full
    `daemon/status` keep it; the profiles were 130 of the 420 KiB of one answer);
  - the runs are chosen newest first through their index, which the query names, reading no further than the last
    one listed (unnamed, SQLite read and sorted every run first, in 2.4.14 too);
  - the runs are summarised one at a time, with the event loop free between them.
- Brief status: the dormancy counts are found through each actor's current listener in one query, and read again
  only after a listener, actor, writer start or history gap was written. Through 2.4.14 every call decoded every
  listener record: 3,417 of them, 40 MB.
- Measured with the plugin's own code on a copy of the user's 15 GiB ledger:
  - `run/list` went from 0.37 to 2.95 s, all of it without a yield, to 0.17 to 0.33 s, at most 11 to 88 ms without one;
  - its answer went from 420 to 288 KiB;
  - the dormancy part of the brief status went from 143 to 357 ms to 25 ms once, then nothing until a relevant write.
- The two new indexes are built at the first start, 3.1 s on that copy. The revision index holds claim and custody
  records only because every save changes a record's revision, so an index over all records is updated at every
  save: on the copy, 2,000 custody saves wrote 1.3 more WAL pages each with it, and 80% of its 15.4 M saves were of
  other kinds.

### Known limits
- What the ledger already holds stays: 16.3 GB, of which 2.53 GiB is old `turn/diff/updated`. Codex's architect
  review of 10-29 set the order of the rest: 2.4.16 one retry policy for every caller (and the hook-inventory
  failure), 2.4.17 bounded tool paging and stored tool responses, 2.4.18 shared dependency evidence, 2.4.19 native
  compaction and archive retention, 2.4.20 reclaiming the file's space, 2.4.21 parking and resource budgets.
- Streaming deltas stay journaled. History keeps their final text, but not command output beyond what history keeps.
- The Office still polls every 2 s; each poll now costs a few hundred milliseconds of the coordinator, in small steps.
- Writer records are rewritten whole at each save: on the copy, 10.3 M listener saves, some of records of 1 to 2 MB.
  Measured here, queued for a later release.

## [software-workflows 2.4.14] — 2026-10-29

The coordinator no longer reads every operation it has ever recorded to answer a question about a few of them. Through
2.4.13 each restore of an UNKNOWN read the whole history twice on the event loop, and each start three times. On the
user's ledger that is 22,344 operations and 1.7 GB, at 14 to 21 s per read. Now a restore reads none of it and a start
reads it once (Known limits). One change, in the coordinator (`recovery.py`; coordinator/VERIFY.md, 2.4.14).

### Fixed: restores and starts read the whole operation history
- An operation record keeps its tool output. A restore asks for the quarantined operations it resolves: those on its
  path alone, and the parents whose parts are all resolved. Startup recovery asks for the operations interrupted in
  flight. Each question was answered by decoding every operation ever recorded, on the event loop, so the coordinator
  answered nothing else meanwhile: about 30 to 40 s per restore on the user's ledger, as much again per start. On
  10-23 a settlement of 43 paths stalled it until one status call took 94 s.
- Now each reads through the partial index the ledger already keeps for that question, in the same id order:
  `blocker_quarantine` for the quarantined operations, `window_operation` for those prepared, allowed or running.
- Measured on the user's live ledger, read-only and in one snapshot: reading and decoding every operation took 14.4 s
  (20.7 s in a first run). The indexed reads returned the same records in the same order: the 6 quarantined in 1.3 ms
  and the 2 in flight in 0.3 ms.
- On a real daemon on a disposable state, an UNKNOWN's restore behaved the same on the installed 2.4.13 and on this
  tree, with a quarantined operation on its path and a quarantined parent whose part was resolved. In both, the file was
  restored to its baseline, the UNKNOWN and both operations were resolved, and the restart was clean.

### Known limits
- `lead_calls.recover` still reads and decodes every operation once per start, to keep the lead-edit ones: 14 to 21 s
  on the user's ledger. It uses that map after the records change, so its indexed form needs a check of its own.
- The stop's quiescence check still reads every lead hook, actor, run and message record at each refused stop (about
  0.7 s on the user's ledger).
- Still for later: review turn 93's push-gate items, the limits 2.4.9 carried, and fenced park refusals.

## [software-workflows 2.4.13] — 2026-10-29

Two changes in the coordinator. A writer whose process family no owner journal holds no longer blocks the
coordinator's stop: through 2.4.12 such a writer was quarantined at the next start and every draining stop refused on
it, for as long as its record stayed. And a park that memory pressure asks for but that cannot start no longer writes
to the ledger. (`daemon.py`, `parking.py`; coordinator/VERIFY.md, 2.4.13.)

### Fixed: a replaced owner journal blocked every graceful stop
- When the owner journal is replaced, the supervisor holds none of the families recorded in the old one. The user's
  was moved aside at the forced switch to 2.4.11 on 10-28, because it was full. A listener record that named such a
  family and held no receipt was still asked of the supervisor's `end`, which refuses a family it does not hold
  (`OWNER_UNREGISTERED`). So recovery quarantined the writer for good, and every draining stop aborted on it.
- On the user's coordinator, the switch from 2.4.11 to 2.4.12 waited 4 hours and was refused 1,053 times. It could
  never have completed: at each idle moment the stop reached such a listener first and refused.
- Now `family_receipt` answers such a family from the processes themselves, once nothing of it can still run, and
  saves the root's exit evidence as its receipt. The check:
  - every identity the ledger recorded for the family must have exited: its app-server, the services and helpers it
    started with, the processes its commands were seen to own, and the processes its unit's custody holds, saw or
    could not attribute;
  - then a search of the process table must find no survivor that no record names, by the thread's markers or by
    kernel lineage (on macOS an orphan keeps its dead parent's kernel id, measured);
  - a record without its root's identity, its thread or its service inventory keeps the refusal, as does anything
    alive. Recovery, the draining stop, dormancy and parking all read receipts through it.
- Codex review turn 95 found that the first version checked only the root and the custody records' process lists: a
  dead app-server whose MCP service survived would have been answered `GONE`. The check above is the fix.
- Measured on a real daemon, its supervisor and worker, and a real writer app-server against the loopback fixture
  provider, on a disposable state. The setup: a connected, idle writer; the daemon ended by signal and its journal
  moved aside; then a restart and a draining stop.
  - The installed 2.4.12 quarantined the writer at its restart (`reconcile_failed`, permanent) and refused the stop
    with `OWNER_UNREGISTERED` on that listener. Its daemon kept serving.
  - This tree recovered the writer to dormant, with `pid_absent` as its receipt, and the stop completed (35 s in all).
  - The user's case: 2.4.12 had already quarantined the writer when this tree started on the same state. This tree
    kept the quarantine, and its draining stop stopped the writer, saved `pid_absent` as its receipt and completed
    (36 s in all).
  - Survivors, on the tree's own `family_receipt` over a real process tree (a stand-in app-server with a detached
    child, then SIGKILLed): while the orphaned child lives, recorded nowhere or in the services, the lineage, or a
    custody record's observed or unattributed processes, the answer is still `OWNER_UNREGISTERED`. So it is for a
    process carrying the thread's markers with no lineage, and for a record without a service inventory. Once all of
    them exit it is `GONE`. The first version answered `GONE` in every one of these cases.
  - A live root, any other refusal, and a record without its root's birth time are still refused, as 2.4.12 refuses
    all of them.

### Fixed: parks that cannot start filled the ledger
- At each launch under memory pressure (kernel level 2 is common in ordinary use), the coordinator asks for a park of
  every family it has seen active since it started, stopped, dead and quarantined ones included. Each park refused
  before its fence still saved the listener record and logged `listener/park_abandoned`. On the user's coordinator in
  the hour to 01:3x on 10-29 that was 1,752 of 11,862 events (15%), 1,562 of them for writers that were not even
  connected; one 2.4.9 writer logged 3,524 in 10.7 hours on 10-28.
- Now only a connected family is attempted, and one that left is no longer asked until it is active again. A park
  refused before its fence changes nothing and writes nothing; status still counts it by reason. A park refused after
  its fence is recorded as before.
- Measured on the real `Parking` class and a real coordinator store on a disposable state, under 100 rounds of the
  requests memory pressure sends, for four listeners (stopped, quarantined, connected with an active turn, connected
  and refused after its fence): the installed 2.4.12 logged 400 `park_abandoned` events and wrote the three that never
  fenced 100 times each. This tree logged 100, all of them the fenced listener's, wrote the other three not at all, and
  stopped asking the stopped and quarantined ones after the first round. Status counted the same refusals by reason
  on both.

### Known limits
- The running 2.4.12 still has the stop defect. The user's coordinator holds three writers that were quarantined
  without a receipt, so 2.4.12's draining stop refuses at every idle moment. Its switch to 2.4.13 is therefore made
  the way the switch to 2.4.12 was: the stop is asked repeatedly, and at an idle moment the old daemon is ended by
  signal and this release started under the upgrade lock.
- Writers quarantined before this release stay quarantined, because a permanent quarantine is not retried. This
  release's draining stop now stops them.
- What the survivor search cannot see: a process whose environment is unreadable (macOS system binaries) and whose
  parent and grandparent have both died, recorded nowhere. Its lineage ends at a kernel id no live process holds. This
  is the coordinator's existing ownership limit; the family is then answered `GONE`.
- A park refused after its fence (the writer's native thread was found active) is still attempted and logged at each
  launch under pressure: 109 of the 1,752 in that hour.
- Left for 2.4.14: review turn 93's push-gate items, the limits 2.4.9 carried, and `restore_unknown`.

## [software-workflows 2.4.12] — 2026-10-28

The owner journal no longer keeps every process family it has ever recorded. Its compaction now keeps only what can
still be asked about: live families, a writer's finished family until the ledger holds its receipt, and other finished
families for a retention window. Through 2.4.11 it rewrote every family, finished ones included, so the journal only
grew; the user's 8 MiB journal filled in 9 days, refused new ownership (`RESOURCE_EXHAUSTED`) and blocked the switch
to 2.4.11 until it was moved aside. One change, in the coordinator (`owner_journal.py`, `owners.py`, `daemon.py`,
`dormancy.py`, `parking.py`, `owned.py`; coordinator/VERIFY.md, 2.4.12).

### Fixed: the owner journal kept every family it had ever recorded
- The journal's state kept the latest record of every family, `GONE` ones included, and compaction wrote a snapshot of
  each, so finished writers, helpers and probes stayed forever. Compaction refuses when what it keeps exceeds half the
  journal, and from then on new ownership is refused.
- Now a family leaves the journal once nothing can still ask for its `FAMILY_GONE` receipt:
  - **A writer's app-server family** leaves when the worker holds its receipt in the ledger. The worker sends
    `forget` (worker channel only; the supervisor forgets only `GONE` families no request waits on) and the supervisor
    records `FORGOTTEN`. The worker forgets when a writer stops, parks, dies, goes dormant or fails to start, and at
    every start again for each listener record holding a receipt: a forget lost to a crash, and the receipts saved
    before 2.4.12.
  - **A writer launch abandoned before its listener record exists** leaves too. A launch cancelled after the
    supervisor granted it forgets the family it ends, and every start forgets each `GONE` listener family that no
    listener record names and an earlier worker was granted (a worker that died between the grant and the record).
    The current worker's own launches are never swept.
  - **Every other family** (admission, project and role app-servers, helpers, members, watcher probes, workers) is
    asked about only by the handle that launched it, within seconds of its exit. Compaction keeps it for
    `SOFTWARE_WORKFLOWS_OWNER_JOURNAL_RETENTION_S` seconds after its `FAMILY_GONE`, three days by default, read at
    every start; one recorded before 2.4.12 has no exit time and goes at the first compaction.
  - A receipt the ledger holds is answered from the ledger, so nothing asks the supervisor for a family it has
    forgotten. Parking reads its receipt there too, instead of searching `owner/status`.
  - `daemon/status` reports the supervisor's `forgotten` and `gone_retained`, the journal's `retention_s`, and the
    worker each family was granted to.
  - A process handle stays correct now that families leave. Its `end` is idempotent: once answered `GONE` it answers
    again from what it holds, and an `end` after its family has left (`OWNER_UNREGISTERED`), with the root's exit
    evidenced, is that finished end. A `poll` queued behind an `end` that finished the family returns that end's
    outcome, and the supervisor answers a `poll` for a family it no longer holds with `OWNER_UNREGISTERED`, no longer
    a bare lookup error. Admission's schema step, the watchdog and project discovery all end or poll a process after
    it exits; under a short retention their late ask failed a writer's admission or discovery's cleanup. A family the
    owner does not hold, with a live root, is still an error.
- Measured:
  - **The real journal and owner classes, real child processes, a 256 KiB journal, 600 lifecycles** (one helper per
    two writers): the installed 2.4.11 refused new ownership at lifecycle 155, holding 155 `GONE` families
    (198,219 bytes used; 15 compactions refused). This tree completed all 600 with 20 compactions and none refused,
    never above 128,260 bytes, keeping 6 families. Ending a family took 14 ms at the median on both, 5.02 s at most
    on both (the root's five-second grace).
  - **A real daemon, supervisor and worker on a disposable state, with real writer app-servers against the loopback
    fixture provider** (256 KiB journal, retention 0, writers parking after a second): the installed 2.4.11 refused
    the fourth writer at admission (`RESOURCE_EXHAUSTED`, 197,736 bytes used, 116 families kept, 8 of them
    writers'), and its restarted supervisor did not answer. This tree ran all 12 writers; it forgot all 24 writer
    families, kept none of them, and held 6 families in 36,404 bytes before the restart, after which
    the startup sweep found 24 receipts and nothing left to forget. An earlier run, before the handle
    changes, refused its tenth writer: that is how the late ask was found.
  - **A handle's late ask** (the real journal and owner, retention 0, the supervisor's own `end` and `poll`, calls
    serialized as the worker's client does): with 2.4.11's handle, an end after a poll saw the exit, a second end, and
    a poll queued behind discovery's `terminate` all failed (`OWNER_UNREGISTERED`, `OWNER_OPERATION`); with this
    tree's, all three answer, and a family the owner does not hold, whose root lives, is still refused.
  - **Abandoned launches:** a listener launch cancelled after its grant left a `GONE` family that 2.4.11's compaction
    kept; this tree forgets it. On a real daemon, a `GONE` listener family that no record names, granted to an earlier
    worker, survived two restarts of 2.4.11; this tree forgot it at the first start and it stayed gone.
  - Codex review turn 94 found the abandoned launches and the queued poll; both are fixed above.

### Known limits
- A family whose writer is not settled (running, orphaned, or not yet recovered) stays: it is live custody. A launch
  abandoned by a worker that died stays until the next start's sweep.
- The size of an existing journal is fixed at its creation: the user's 128 MiB journal from the 2.4.11 switch stays
  128 MiB, and a new one defaults to 8 MiB.
- A handle whose family has left and that never saw its exit code reports none (`returncode` None).
- The switch from 2.4.11 is the usual one: with writers in flight a graceful stop still waits on `NOT_QUIESCENT`.
- Review turn 93's items for the push gate and 2.4.9's limits marked for 2.4.12 move to 2.4.13.

## [software-workflows 2.4.11] — 2026-10-28

The push gate asks for an acknowledgement only for a Bash command whose structure puts a push or commit where the
shell would run it: it reads the command's structure, not only its words, so the read-only commands that mention push
or commit now run without one. It reads a command's shape well enough for ordinary use; some constructed shapes still
over-ask, listed under Known limits and left for 2.4.12, and the git shim is the backstop that stops an assembled or
hidden push once git runs. The git shim no longer runs git unchecked when a command's `PATH` holds no python3. Both come from review turn 92 of 2.4.10
(coordinator/VERIFY.md, 2.4.11). The coordinator's code is 2.4.9's.

### Fixed: words in a read-only command asked for an acknowledgement
- 2.4.10 stopped a lone `$'` from counting as a push. In a session that had not run git yet (its shim not yet proven
  live), the gate's other readings still matched words:
  - `git` followed by push or commit anywhere in a segment: `git log --grep=push`, `git stash push`,
    `git help commit`, `grep -rn "git push" docs/`, and a here-document that writes notes or a script;
  - push or commit anywhere beside a `$`, `\` or backtick: `grep -c 'push$' log`, `echo "commit: $x"`, and a
    `gh pr create --body "$(cat <<'EOF' …)"` whose text says push.
- Now a Bash command is looked at only for:
  - a `git` word (any path, any case) whose subcommand, past git's global options, is push or commit, or is a word
    only the shell can read (`git $'\x70\x75\x73\x68'`, `git "$@"`);
  - a word an expansion builds standing where git would, before push or commit (`"$G" push`, `g$'\x69t' push`);
  - `git-push` or `git-commit` as the command;
  - the same inside what a shell runs: `bash -c` and `sh -lc` strings, a here-string a shell reads, `eval`, `$(…)`
    and backtick text, and a here-document a shell reads (`bash <<'EOF'`, `cat <<EOF | sh`);
  - an encoded PowerShell payload, as before.
  - Quoted words, and here-documents anything else reads, are data.
- The PowerShell tool keeps the word readings: the shim is not measured for PowerShell commands.
- Measured: 81 commands replayed as PreToolUse payloads through the real hook, with no shim marker, on Python 3.9.6
  and 3.15.2.
  - 19 read-only commands that 2.4.10 denied are silent. The user's command of 10-28 stays silent.
  - 3 that 2.4.10 let through are denied: `git $'\x70\x75\x73\x68'`, `command -p git $'…'` and `git "$@"`.
  - Every other push and commit in the set is denied as before. The 9 PowerShell commands are decided exactly as
    2.4.10 decides them.

### Fixed: the shim ran git unchecked without python3
- The shim started the gate with `python3` from the command's own `PATH`. A `PATH` that kept the shim first but held
  no python3 made it run the real git directly; with the shim, then git's exec-path, a push, an encoded push and an
  alias for push landed, and a commit was made.
- Now the SessionStart hook also writes `CODEX_GATE_PYTHON`, the interpreter it runs under, and the shim starts the
  gate with it, else with python3 from `PATH`. With neither, commit, push and every subcommand the shim does not know
  are refused; the git commands it knows still run.
- Measured in a disposable repo with a bare remote, `PATH` = the shim, then
  `/Library/Developer/CommandLineTools/usr/libexec/git-core`.
  - With 2.4.10's shim, all three pushes landed and the commit was made.
  - With this shim, nothing landed and no commit was made: without `CODEX_GATE_PYTHON` the shim refused, and with it
    (3.15 or 3.9) the gate refused. `git status` ran every time.
  - Under Homebrew's Python the hook writes the version-independent `/opt/homebrew/opt/python@3.15/bin/python3.15`.

### Known limits
- Once a session's first git call shows the shim on `PATH`, the text gate stands down for Bash, as since 2.0. From
  then on a push or commit whose git does not run through the shim goes unchecked: by absolute path, through
  `command -p`, or with a `PATH` without the shim.
- What a program other than a shell runs (`python3 -c "…"`, `ssh host 'git push'`) is not read. A local one reaches
  the shim through `PATH`.
- A word an expansion builds before push or commit is looked at even when it is not git (`cp "$src" push`). A
  comment that contains `git push` is still read as words.
- With no interpreter at all, a git command the shim knows runs without its exec-path mirror, so a push that git
  itself starts (`git rebase --exec 'git push'`) is not seen.
- A `git` or shell word in an argument position, not a command position, is still looked at: `printf '%s\n' git push`
  asks though printf runs no git. Review turn 93; to be addressed in 2.4.12.
- An unquoted here-document body is data to the gate, though the shell expands its `$(…)`: a push in such a
  substitution run by a path the shim does not see is not read, and a quoted literal `'<<'` is read as an operator.
  Review turn 93; to be addressed in 2.4.12.
- A command that repeats git's value-taking options tens of thousands of times is parsed in time that grows with the
  repetition, bounded by the worker's 60 s deadline. Review turn 93; to be addressed in 2.4.12.
- The trust model is unchanged: a guardrail against forgetting a review, not a security boundary. 2.4.9's known
  limits stand; those marked for 2.4.11 move to 2.4.12.

## [software-workflows 2.4.10] — 2026-10-28

The push gate no longer asks for an acknowledgement because a single-quoted pattern ends in `$`, as in
`grep -c ': OK$' sums.txt`. A push or commit the shell assembles is decided by the git shim when git runs through it
(coordinator/VERIFY.md, 2.4.10). The coordinator's code is 2.4.9's.

### Fixed: `$'` in an ordinary command asked for an acknowledgement
- Until now `$'` anywhere in a Bash command counted as a possible push, because ANSI-C quoting can build the verb
  (`git $'\x70\x75\x73\x68'`). Every single-quoted word that ends in `$` contains `$'` as well.
  - On 10-28 a session's read-only check of release hashes was refused this way:
    `cd …; shasum -a 256 -c SHA256SUMS > …; grep -vc ': OK$' …; grep '^diff --git' …`. That session had not run git
    yet, so the shim was not proven live and the text gate was in charge.
  - The worker's exemption for such commands covered only a closed list of readers without redirection. This command
    had `cd`, `shasum`, `echo` and redirections.
- Now `$'` alone is not a push. The other readings are unchanged: a literal `git`, `push` or `commit` after
  de-escaping and quote removal, and a push or commit word next to `$`, `\` or a backtick. The exemption is gone with
  the rule it softened.
  - A verb the shell assembles reaches the git shim as plain `git push`, and the shim decides it on its exact
    arguments.
- Measured: each command was replayed as a PreToolUse payload in a session without the shim's marker, on Python 3.9.6
  and 3.15.
  - For the user's command (its folder moved to scratch), the installed 2.4.9 gate denied it, and 2.4.10 is silent.
  - It is the same for `sed -n '/^done$/p'`, `awk '/x$/'`, `echo $'a\tb'` and a `grep … ': OK$' > out.txt`.
  - `git push`, `git commit`, `G=git; "$G" push`, `g$'\x69t' push` and `$'/usr/bin/git' push` are denied as before.
- Measured at exec, in a disposable repo with a bare remote: `git $'\x70\x75\x73\x68' origin HEAD:refs/heads/main`,
  `u='t pu'; g$'i'$u'sh' …` and `g$'\x69t' push …`.
  - Without the shim each push landed.
  - With the shim each was refused with `git push origin HEAD:refs/heads/main` named, and the remote stayed empty.

### Known limits
- The text gate no longer sees a verb assembled from ANSI-C quoting or from an expansion's value; the shim decides
  that push or commit when git runs through it. It goes unchecked when git does not: by absolute path, through
  `command -p`, in a session whose shim is not on `PATH`, or (review turn 92, fixed in 2.4.11) when the command's
  `PATH` keeps the shim but holds no python3. The trust model is unchanged: the gate is a guardrail against forgetting
  a review, not a security boundary.
- 2.4.9's known limits stand. Those marked for 2.4.10 move to 2.4.11.

## [software-workflows 2.4.9] — 2026-10-27

The coordinator no longer dumps its threads' stacks from a signal handler, and a late diagnostic signal can no longer
end its worker. On 10-27 at 21:44 that dump crashed the user's live 2.4.7 worker, and 2.4.8 has the same dump. Both
changes are to the stall watchdog's SIGUSR2, each with a real run on Codex 0.163.0 (coordinator/VERIFY.md, 2.4.9).

### Fixed: a stall diagnostic crashed the worker
- When the event loop missed its heartbeat for 0.75 s, the watchdog asked the supervisor to send the worker SIGUSR2.
  faulthandler's C signal handler then walked every thread's frames into `loop-stalls.log`.
  - On the live 2.4.7 daemon it fired 1,219 times between 17:22 and 21:44, every 2 to 5 s while the user's writers
    were busy.
  - At 21:44:38 the walk crashed the worker with SIGSEGV (`faulthandler_user` → `dump_traceback` →
    `_PyFrame_SafeGetCode` → `_PyObject_IsFreed`), while its main thread was inside `sqlite3_step`.
  - The supervisor restarted the worker 60 s later, and recovery re-attached the five live app-servers in 39.3 s.
    2.4.8 would have ended them instead (Known limits).
- Now the worker only counts the signal.
  - The stall is still recorded in `loop-stalls.log`: its estimated start, the heartbeat's age and the threshold.
  - A change in the signal count is recorded, best effort: a `watchdog_diagnostic_signal` line there and a
    `watchdog/diagnostic_signal` event in the ledger. Several signals between two polls make one record.
  - Status reports `diagnostic_signals`, with `dumps: 0` and `dump_integrity: no_dump`.
  - 2.4.8's `possibly_truncated` labels and `watchdog/dump_possibly_truncated` events are gone.
- No thread stacks are written for a stall any more. A diagnostic that cannot crash the worker, sampled from another
  process, comes with the lifecycle work below.

### Fixed: a late diagnostic signal could end the worker
- When the worker retired its watchdog helper (the helper exited, or the worker stopped), it restored SIGUSR2's
  previous disposition, normally the default, whose action ends the process.
  - The owner signals the worker by its recorded identity alone. So a diagnostic request the retired helper had
    queued could still arrive in the backoff before a replacement started, and end the worker.
  - Under 2.4.8's supervisor that also ends every running writer turn.
  - Present in 2.4.8, where the owner relays the helper's request (2.4.7's helper signalled the worker itself); found
    by review.
- Now the worker never returns SIGUSR2 to its default: between helpers and after the last one, a no-op handler takes
  it.
  - It never sets SIG_IGN either. Every process the worker starts would keep an ignored signal across exec, so a
    script's USR2 trap would silently fail there. A caught signal is back at SIG_DFL in the new process.
- Measured on a disposable coordinator: the helper was killed and SIGUSR2 sent 0.1 s later, in the backoff.
  - 2.4.8's worker ended, and its supervisor started another.
  - 2.4.9's worker kept its PID and started a replacement helper.
- Measured on the mechanism: a process at each disposition started bash with a USR2 trap, then signalled itself.
  - At SIG_DFL the trap ran and the process ended.
  - At SIG_IGN the trap never ran.
  - With 2.4.9's handler the trap ran and the process survived.

### Known limits
- A worker crash still ends every running writer turn, as in 2.4.8.
  - When its worker exits, the supervisor ends every process family it owns, app-servers included
    (`owners.py:360`). 2.4.7 kept them and re-attached.
  - With the dump removed, no cause of a worker crash is known.
  - Keeping app-servers across a worker restart is part of the lifecycle redesign: replacing, restarting and stopping
    the coordinator without waiting on writers. It is being designed and reviewed first, because re-attaching alone
    loses the hook receipts that Codex does not save.
- 2.4.8's known limits stand. Those marked for 2.4.9 move to 2.4.10.

## [software-workflows 2.4.8] — 2026-10-27

A tool call no longer makes the coordinator re-read every file of the checkout: only files whose stat identity
changed are hashed again. On the user's 3,300-file checkout a scan read 91.5 MB. An idle writer now hands back
its processes while the coordinator runs. After 30 minutes without activity its saved history is checked, its threads
are unloaded natively, and its app-server stops with every MCP server. It starts again when it is next used. A
dormant writer's native journal moves out of the ledger into a compressed archive, in slices that do not hold the
coordinator. Writers of one project share one set of local service containers (MinIO, PostgreSQL, Redis), each
writer with its own namespace and credentials, instead of starting a container per test run. The coordinator records
every process family it starts, and at its next start ends the ones a crash left behind. It holds new launches while
the Mac is short of memory. Plugin options now reach the Codex Oracle server, mail to a cancelled writer no longer
blocks every stop, and 2.4.7's known limits from review are fixed. On 10-26 the user's 42 idle writers held about
23 GB, the ledger grew about 1.5 GB a day, and the Mac ran out of memory. Each change has a real run on Codex 0.163.0
(coordinator/VERIFY.md, 2.4.8).

### Changed: a tool call hashes what it changed, not the whole checkout
- Before and after each lead command, the coordinator used to re-read and re-hash every file of the checkout.
  - On the user's 3,300-file Sales_Profiler checkout a scan read 91.5 MB and took about 0.5 s, measured on the live
    2.4.7 daemon.
  - On battery the daemon was a main drain, and Claude sessions began keeping their tool calls to a minimum.
- Now a file is hashed again only when its stat identity changed since it was hashed: device, inode, size, mtime,
  ctime, mode and links.
  - A hash counts only if it began at least 3 s after the file's mtime and ctime (git's racy-clean rule), so a file
    written within a timestamp tick of its hash is hashed again.
  - The cache is in memory and advisory: after a restart the first scan reads everything.
- Attribution is unchanged. A rewrite that restores the old mtime still moves the ctime and is seen, and a rename
  over a file is a new inode.
- Measured on a 3,300-file, 90 MB replica, three lead commands that each changed one file:
  - before: 720 MB hashed, 2.24 s of worker CPU;
  - after: 50 KB hashed, 1.44 to 1.52 s of worker CPU (two runs).
- Each scan still stats every file and lists the checkout with git, so a tool call still costs CPU. The two CPU
  figures have different warm-up boundaries: they are observations, not an energy measurement (Known limits).
- Status reports, per checkout, the bytes hashed and the bytes skipped.

### Changed: an idle writer hands back its processes, and starts again when used
- A writer idle for `writer_park_idle_seconds` (default 1800) is parked, in this order:
  1. its saved history is checked against what was received;
  2. its threads are unloaded natively (`thread/unsubscribe`), children included;
  3. its saved history is checked again;
  4. only then is its app-server stopped, with its MCP servers, and the family's exit confirmed.
- A writer with anything in flight is not parked: a turn, queued input, a delivery, a background terminal, a tool
  request, an operation, custody or a lead hold. Output not yet saved abandons the park (`PERSISTENCE_PENDING`),
  and it is retried later.
- The default comes from 388 measured gaps between one thread's turns (10-19 to 10-26): p50 792 s, and 27 % longer
  than 30 minutes. `0` turns off parking on the idle timer, and a malformed value is shown in status and does the
  same. Parking that memory pressure asks for still happens (Known limits).
- `run/resume`, a waking message or a workflow advance starts a parked writer through 2.4.7's shared demand start.
- Measured on a real Codex 0.163.0 writer with the user's MCP profile (`MCP_DOCKER`, `openaiDeveloperDocs`). Both
  servers were connected at the first launch, and again after a park and a resume on the same thread. The cycle took
  6.36 s, its short idle interval included.
- Status (`daemon/status.dormancy`) counts parks attempted, completed and abandoned, with their reasons.

### Changed: a dormant writer's journal leaves the ledger
- Once a writer and the app-servers it replaced are dormant and verified, their applied native events are written
  to `<state>/archive/native/<listener>/`, compressed, fsynced and digested, then deleted from the ledger in batches
  of 64 rows.
- A writer whose unit has an unresolved UNKNOWN, a quarantine or an open operation naming it keeps its events.
- Each batch re-checks eligibility from indexes kept in the same transactions as the records, instead of decoding
  the unit's operations again. On a replica of the user's heaviest unit (1,400 operations, 750 MB of JSON, 110,000
  rows), a check took at most 1.6 ms where the build took 1.04 s. All 110,000 rows were archived and verified, the
  event loop lagged at most 2 ms, and a lead hook's Pre took 0.19 s meanwhile.
- Readers of native events:
  - `run/events` lists each archive with its range, SHA-256 and path;
  - `run/log` and the Office read Codex's saved history, which retention never touches;
  - history checks read only receipts after the verified checkpoint.
- SQLite reuses the freed pages, so the archived journal no longer adds to the ledger. The file does not shrink, and
  the records that stay keep growing. To shrink it, VACUUM it while the coordinator is stopped
  (coordinator/README.md).
- What can move: on the user's 12.1 GB ledger, events hold 6.4 GB, and 91 % of all event rows are native rows of
  dormant writers. Tool-call records (3.9 GB) and operations (1.5 GB) stay in the ledger.
- Measured:
  - a startup on a replica shaped like the user's ledger (43 dormant writers without the 2.4.8 proof, 184 replaced
    app-servers) verified all 227 listeners and archived 1,250,000 rows into 227 archives. It was ready in 0.78 s,
    had every dormant proof in 23.2 s and finished archiving in 33.5 s. The event loop lagged at most 0.04 s, with no
    history gap and no UNKNOWN. That replica's ledger was 559 MB.
  - the first open of a copy of the user's 12.1 GB ledger built the 12 new partial indexes in 73 s (13 s of CPU),
    before the daemon serves; the next open took 0.01 s.
  - 27 synthetic histories: 321,497 rows into 28 archives, 23.0 s of archive writes and 6.1 s of deletes in all,
    with `daemon/status` at p99 0.068 s meanwhile.

### Added: writers of one project share its service containers
- A project declares MinIO, PostgreSQL or Redis services in `.software-workflows/services.json`, using images
  already present locally. The coordinator never pulls.
- The first writer that needs them gets one pool per project and spec. Containers are created stopped, labelled,
  with Docker-assigned loopback ports, and their full IDs are recorded before they start. A pool is ready only after
  an authenticated call (an S3 call, `SELECT 1`, an ACL `PING`).
- Each writer gets its own namespace and credentials: a MinIO bucket with a scoped user, a PostgreSQL database
  cloned from a template with its own role, a Redis ACL user limited to a key prefix. The writer's commands receive
  them as environment variables through Codex's `shell_environment_policy.set`. Status and events show names, never
  values.
- Credential files are published whole (a synced temporary file, then a hard link), and an empty or partial one is
  refused (`SERVICE_SECRET_INCOMPLETE`), never rotated silently.
- An idle pool is stopped (`docker stop`) and started again on demand, with its Redis ACL users restored before any
  credential is handed out. A writer's namespace is dropped when its run ends, after re-checking under the start
  and pool locks that the run is still ended, so a resume keeps it. A changed spec makes a new pool, and the old one
  is removed after its last lease.
- Removal checks the exact recorded container ID and labels, then `docker rm -v`. Containers the plugin did not
  create are reported, never touched.
- Measured: two writers shared one PostgreSQL and MinIO pool, and each was refused the other's namespace. Cold
  readiness 0.98 and 0.65 s, warm 0.14 and 0.60 s.

### Added: the coordinator owns its processes, and a crash leaves none behind
- Every process family the coordinator starts is recorded in an owner journal, by kernel identity, not by PID or
  name.
- A family is ended by snapshot, TERM, wait, snapshot, then TERM and KILL for the survivors. It counts as gone only
  on exit evidence, and its GONE record is kept through compaction.
- At the next start, families of a crashed coordinator are ended. A process the journal does not record is reported,
  not signalled, with one exception: when a cleanup snapshot cannot be journaled, the emergency cleanup also signals
  the members it has just found (Known limits). A bootstrap left by a daemon from before the owner journal is retired without a signal once its
  exit is verified.
- A worker survives the death of its supervisor and is adopted by the next one.
- `daemon install-agent` / `uninstall-agent` installs an opt-in LaunchAgent that restarts the coordinator after a
  crash. It keeps, for its state directory, the settings (MCP profile, parking, memory) of the environment it is
  installed from: run it where those are set, since from a clean shell it keeps the defaults (Known limits).
  Nothing installs it by itself. Measured with a throwaway agent: adoption in 5.32, 5.30 and 5.30 s after each of three supervisor kills.

### Added: launches wait while memory is short
- Before an app-server launch (a writer, a demand start, an offline reader or a calibration), the coordinator reads
  the kernel's pressure level and the compressor's fill ratio.
- At a compressor ratio of 0.50 it holds the launch with retryable `RESOURCE_PAUSED` and parks idle writers first.
- At 0.62, or at the kernel's critical level (4), it refuses with retryable `RESOURCE_EXHAUSTED`.
- At the kernel's warning level (2) alone, it parks idle writers and lets the launch through. Nothing running is
  interrupted.
- Why the ratio and not the warning level:
  - On 10-26 Jetsam killed processes at ratios 0.68 to 0.70, with the level not warning.
  - On 10-27 the level sat at 2 for 19 % of the day, in runs of over 23 minutes, at ratios 0.32 to 0.40, with no
    Jetsam.
- Both ratios and the hysteresis are settings (`memory_warn_ratio`, `memory_critical_ratio`,
  `SOFTWARE_WORKFLOWS_MEMORY_HYSTERESIS`). Status shows the reading each decision used.

### Fixed: plugin options never reached the Codex Oracle server
- Claude Code exports `CLAUDE_PLUGIN_OPTION_*` to hooks only. `.mcp.json` now maps each option into the server's
  environment, so `writer_mcp_servers` and the new settings apply.

### Fixed: mail to a cancelled writer kept every stop from completing
- `message/send` accepted a waking message to the writer of a run that was already cancelled.
  - Its delivery returned early for the cancelled run.
  - Only a cancel's own pass settles a unit's pending mail, and that pass had already run.
  - So the message stayed owed a delivery for good, and every `daemon/stop` answered `NOT_QUIESCENT`.
- On the user's 2.4.6 this held the switch to 2.4.7 for 2.4 days. It was settled by hand with `run/interrupt`.
- Now mail to a cancelled or cancelling writer is refused (`RUN_CANCELLED`). Mail a cancel overtakes is settled with
  a durable `message/cancelled` event; a delivery that may have reached Codex keeps its evidence.

### Fixed: the build's review findings (Codex code_review, turn 85)
- A lead edit's admission now includes the owner's check within its 2.0 s budget. During a slow family retirement a
  Write was refused `LEAD_ADMISSION_BUSY` in 2.01 s, where the build let it run after the hook's 3 s timeout.
- After a failed journal replace, the owner hands over to the new file at once, and refuses further records if its
  sync fails, instead of appending to the removed file.
- A spawn cancelled after its child was granted retires that child by its launch identity.
- Retiring a dead connection's pending tool requests walks the whole chain of verified-exited predecessors, keeping
  the evidence of any effect, so an upgraded unit is not held `PENDING_TOOL`.
- A pool that started but never got its first lease is reconciled to idle once its container's identity is verified.
- The service, ownership and LaunchAgent fixes are in their sections above.

### Fixed: 2.4.7's known limits
- A sealed unit whose writers were dormant could not be reopened or published. Quiescence now uses the dormant
  writer's recorded evidence.
- A tool call cut off when its app-server died stayed pending for good. It now gets an outcome that keeps the
  evidence it may have had an effect, and it never runs again.
- A cancel during a dormant writer's start left the new app-server running. That start's replacement is now retired.
- `run/terminals` failed for a dormant writer. It now returns an empty list without starting the writer.
- Output that Codex shortens when it saves no longer reads as a `HISTORY_GAP`. The table is in
  coordinator/README.md.
- A hook's classification is cached only against the files it was parsed from, re-checked around parsing. A child
  watcher that cannot start makes readiness qualified. A stop asked for while a worker starts is kept through its
  cleanup.

### Known limits
- Codex code_review turn 86 found no CRITICAL or HIGH defect in the fix round, and said Do not ship for six it rates
  lower. They are pushed at the user's direction and are to be fixed in 2.4.12 (2.4.9 is the watchdog hotfix, 2.4.10
  and 2.4.11 the push gate's):
  - A memory refusal while a dormant writer is re-verified (`RESOURCE_PAUSED`: the compressor at its warning line,
    a malformed memory setting or an unreadable kernel reading) records UNKNOWNs for its checkout, and the writer is then replaced instead of verified again read-only (MEDIUM).
  - A crash in the middle of dropping a writer's service namespace leaves its lease active, with credentials for
    resources that were deleted (MEDIUM).
  - `daemon install-agent` run from a clean shell records no settings, and the agent then starts with the defaults
    (MEDIUM).
  - A stopped (SIGSTOP) app-server from 2.4.7 is retired before it is adopted, and stays quarantined
    `OWNER_UNREGISTERED` (MEDIUM).
  - When the owner journal cannot record a cleanup snapshot, the emergency cleanup still signals the members it has
    just found (MEDIUM).
  - `writer_park_idle_seconds` set to `0` does not stop the parking that memory pressure asks for (LOW).
- A scan still stats every file and lists the checkout with git, so a tool call still costs CPU. The before and
  after CPU figures have different warm-up boundaries.
- Catalog case 073 (`repeat-command`) fails on the final source. Its command exits, but its ordering check is false.
  It passed on 2.4.7, and the cause is not established.
- The full first start on a 12.1 GB ledger, with recovery and retention over real content, has not been run. The
  shape replica's ledger was 559 MB, and the user's index build was measured on a copy.
- The MCP profile run used an empty Docker gateway registry, so the user's container-backed tool catalog was not
  covered. Redis was run with a local `redis-server`, as no Redis image was present.
- Two of the three matched N2 pairs against 2.4.6 completed, at rate ratios of 0.993 and 1.002.
- Upgrading: the first start of 2.4.8 builds 12 new ledger indexes before it serves: 73 s on a copy of the user's
  12.1 GB ledger. Meanwhile a lead hook waits at most about 11 s for the new daemon and then lets the edit run
  without attribution, and a call that starts the daemon may answer `DAEMON_START` (retryable) after 30 s while
  the daemon keeps starting. Make no edits until the coordinator is ready.
- `unknown/restore` still reads every actor and operation record of the ledger on the event loop, as in 2.4.7: on
  the user's ledger (15,261 operations, 1.5 GB) each restore holds the coordinator for seconds. The lead's release
  settlement of 43 paths held the live 2.4.7 coordinator this way, and a status call waited 94 s. To be fixed
  first in 2.4.12, with the other whole-kind reads on the loop.
- The ledger does not shrink by itself: VACUUM it while the coordinator is stopped.

## [software-workflows 2.4.7] — 2026-10-27

A finished writer whose app-server has exited no longer comes back as an app-server when the coordinator starts. Its
saved history is checked read-only, and it stays dormant until it is next used. On 10-26, 2.4.6 kept 42 idle writer
families running, about 23 GB with every MCP server. Writers can also be limited to the MCP servers you name.
Native intake keeps up with 2.4.6 under load, and a failed journal write (a full disk) stops intake locally instead
of marking the checkout UNKNOWN. A lead's file edit has two seconds from its hook's start to be decided: it is
admitted, or denied before it runs, with what it waited for and when to retry. On 10-23 a lead's Writes on a large
checkout waited behind command settlements, their hooks gave up at 3 s, and the Writes ran unadmitted, leaving 12
UNKNOWNs. Settlements now share one scan per checkout, read outside its lock. 2.4.6's two known limits are fixed.
Each change has a real run on Codex 0.163.0 (coordinator/VERIFY.md, 2.4.7).

### Changed: an exited writer stays dormant until it is used
- A writer is recovered read-only when the coordinator starts if all of these hold: its app-server has exited, its
  run is finished, it owes no work, and it has no startup incident.
  - A temporary reader lists its saved history without loading a thread.
  - Its received items are compared with what Codex saved, and the writer becomes dormant.
  - 2.4.6 started a replacement app-server for each one, with all of its MCP servers.
- `run/resume`, a waking message or a workflow advance starts a dormant writer once, through one persisted start that
  every caller shares. The cold start measured 0.60 s at p50 in the fixture runs.
  - Mail that does not wake the writer waits, and enters before its next input.
  - Cancelling or reading a dormant run starts nothing.
- A received item that Codex did not save is reported as `HISTORY_GAP` in status, the run log and the start notice.
  It is not repaired.
- Measured after an accepted 2.4.6 drain:
  - 42 terminal writers became dormant in 8.06 s, with no replacement app-server;
  - 27 synthetic histories (2.0 GB, a 2.66 GB journal) took 146 s, and 144 s in the lead's re-run.
- A writer whose app-server is still running keeps it. This release has no idle timer.

### Added: writers start only the MCP servers you name
- `writer_mcp_servers` is `all` (the default) or a JSON list of exact server names; `[]` starts none.
  - `SOFTWARE_WORKFLOWS_WRITER_MCP_SERVERS` overrides it.
  - It applies when the coordinator starts.
  - The list is the exact set of servers writers start, including one disabled in your Codex configuration.
- Excluded servers are switched off with Codex's own per-server keys, so a plugin keeps its skills.
- Refusals:
  - an unknown name refuses the writer's launch with `WRITER_MCP_UNKNOWN`;
  - a key Codex ignores refuses it with `WRITER_MCP_UNENFORCEABLE`;
  - a malformed setting is shown in status and refuses writer launches, and the daemon keeps serving.
- Status suggests a list from the last 30 days of writer MCP calls. It never applies the suggestion by itself.
- Context from 10-26: each live writer family was 13 to 16 processes, about 546 MiB, and writers had made 88 MCP calls
  in a week, 80 of them to one server.

### Fixed: the daemon recorded notifications more slowly than 2.4.6 under load (a known limit of the development build)
- Native responses are read ahead of the notifications queued before them. Notifications are committed in groups, and
  the one native turn is shared in fair time slices.
- Measured: five bursts of 27 writers × 20 turns recorded all 540 turns, with no UNKNOWN.
- In the lead's runs at load average 32 to 37, 2.4.7 recorded 17,659 and 18,140 events a minute, against 18,191 for
  2.4.6.

### Fixed: a full disk marked the whole checkout UNKNOWN
- On 10-20 and 10-25, live coordinators recorded checkout-wide `*` UNKNOWNs ("native evidence processing failed") when
  the disk reached 0 bytes.
- A journal write that fails now stops native intake locally and keeps what was already applied. A restart replays
  everything that was journaled.
- Measured with an injected COMMIT failure: the replay took 0.154 s, with no UNKNOWN.

### Fixed: a lead's Write ran unadmitted behind command settlements
- On 10-23, on the user's coordinator (2.4.5), a checkout with 4,733 files in the ledger: command settlements took
  76 to 122 s, each holding the checkout's lock while it read every file. Lead Writes waited 67 s and more behind
  them. The lead hook gave up at its 3 s client timeout and let each Write run unadmitted: 12 UNKNOWNs.
- An anonymous replica of that checkout reproduced it on 2.4.6: 3,284 files (90.46 MB), 4,733 heads, a 2.66 GB
  journal, 27 writers, four real commands completing and four lead Writes through the installed hook. Every Pre took
  3.11 s, all four Writes ran unadmitted, and each path got an UNKNOWN. Most of a settlement was spent waiting for
  the checkout's lock: 13 to 15 s.
- Now a lead's Write, Edit, MultiEdit or NotebookEdit has one decision budget, 2.0 s from its hook process's start,
  covering every wait: the hook's own start and connect, the daemon's accept and read, the call's own lock, custody,
  the checkout's lock and its file reads. It is admitted, or refused before it runs with a non-operational
  `CLAIM_CONFLICT`: `LEAD_ADMISSION_BUSY`, the paths, what it waited for and in which phase, and an estimated retry
  time. Every installed lead hook (2.4.4 and later) denies such an answer; it does not let the call run. The lead
  retries the edit as a new tool call.
- On macOS the daemon takes the start from the kernel: the birth of the process at the other end of the socket, when
  that process is a Python interpreter running a software-workflows `hooks/coordinator_lead.py` exactly as the
  plugin's `hooks.json` starts it (the installed 2.4.4 to 2.4.6 hooks and 2.4.7's). The 2.4.7 hook also sends its
  own deadline, which can only shorten the budget. Any other client's budget starts when its request arrives, however
  long that client has run. Each admission records where its deadline came from (`deadline_source`: peer birth,
  supplied or receipt), and why when the lookup fell back.
- Writers' native work no longer runs ahead of a waiting hook: the daemon takes native steps (receiving a
  notification, applying one, reading a log) one at a time, in arrival order, between its other work. An admission
  reads only the ledger heads of the paths it edits, and its first file read, blob check and intended result are one
  thread job.
- A file call's pre-image reads have a thread pool of their own, so they never queue behind a checkout scan. A read
  still running at the deadline is discarded: it grants nothing and records nothing.
- A call's end (PostToolUse, PostToolUseFailure, PostToolBatch) is recorded at once, even while its preparation is
  still running. A grant whose answer was lost keeps its paths until the call's end is seen.
- The replica, 27 and 30 writers, both hooks, on the release tree, the host at load average 10.8 to 14.4:

  | | 2.4.6 | 2.4.7 |
  |---|---|---|
  | lead PreToolUse, installed 2.4.6 hook | 3.11 s, then the Write ran unadmitted | admitted at once, at most 0.758 to 1.563 s a run (eight runs); the lead's re-runs at load average 16.3 to 28.6 (twelve runs): 34 of 48 first Writes admitted at once, at most 1.170 to 2.629 s a run; 14 denied `LEAD_ADMISSION_BUSY` at 2.135 to 3.038 s and retried, each admitted; none ran unadmitted |
  | lead PreToolUse, 2.4.7 hook | — | admitted at once, at most 0.960 to 1.196 s a run (six runs); the lead's re-runs at load average 16.2 to 35.8 (seven runs): 25 of 28 admitted at once, at most 1.125 to 2.400 s a run; 3 denied busy at 2.623 to 2.630 s and retried, each admitted |
  | Writes run unadmitted / lead-path UNKNOWNs | 4 / 4 | 0 / 0 |
  | a settlement, receipt to custody release | 16.3 to 18.3 s | 2.35 to 6.55 s (the lead's re-runs at load average 16.2 to 28.6: 2.83 to 11.64 s) |

### Fixed: every settlement read the whole checkout under its lock
- A command's settlement read and hashed every file of its checkout while holding the checkout's lock, so
  settlements queued behind one another and everything else waited behind them.
- Now each checkout has one scan producer. A request arriving during a scan joins the next one, whose set of
  requests is frozen when it starts; no later request overtakes it. The scan runs outside the lock. Under the lock,
  the commit checks each path's head and an exclusion version (bumped by every lead call and every patch, lead edit
  or restore that names the path); only paths that changed while the scan ran are read again.
- A command can change a file and put it back before its settlement, which then records no change. A command
  window's start and end each bump a version too, in the same transaction as its custody: its declared paths', or
  the whole checkout's for a command that declared none. A window lasts while its custody is held or quarantined (a
  failed process check quarantines it, and a later check can release it directly); a scope change while it lasts
  bumps both the old scope and the new. One definition of an active custody serves these versions and every
  query that asks what custody is active, so they cannot disagree. An observation whose scan overlapped a window reads its
  scope again before it commits; a whole-checkout one joins the checkout's next shared scan. So no observation (a
  repair, a settlement, an unadmitted call's completion, a turn end, a recovery or quarantine snapshot, a restore
  check) records bytes from a window that has since closed. The versions are in the ledger, so a restart keeps them.
  With no window during a scan, nothing is read again; a window during one costs a re-read of its own paths
  (one 17-byte path in each narrow case measured).
- One SQLite transaction commits an observation's revisions, its UNKNOWNs, its acknowledgement and the custody
  release it completes. Ordinary observations of the same scope that share a scan also share the commit: four
  settlements of one change get one UNKNOWN naming all four custodies.
- Each observation is durable from its start. A daemon killed during the scan, or before the commit, observes again
  at restart, once; one killed after the commit keeps the original UNKNOWN and release.
- An observation of named paths sees only those paths, even when it shares a whole-checkout scan: an unreadable path
  outside its scope is not its UNKNOWN, and it releases nothing outside its scope.
- Every path an observation names is read and checked, even one the shared scan's inventory and the ledger both lack:
  absent is a result of its own. A lead's verified create of that path during the scan is read again, never
  recorded as a deletion.
- An observation is acknowledged only by a scan that started after it asked. A retry gets a new identity even when
  it reuses its pending record; a later turn never takes over an earlier turn's record.
- An observation that fails before its commit keeps its pending record. Maintenance hands the failed records of one
  set of checkouts to a single repair observation (an `observation/repair_handoff` event names each one and records
  that this is not an acknowledgement). The operation, turn, lead call or restore that asked keeps its own
  completion.
- A repair never reads a path another change may still be writing. Before it reads, and again under the commit
  lock, it waits while a native patch or a part of one, a lead edit, a restore, an executing operation, a lead call or
  an external editor holds an overlapping path, or any custody holds the checkout. It is retried every 5 s and never
  acknowledged with those paths left out. A lead call's completion that was never admitted uses the same check.
- A recovery or quarantine observation leaves another current change's paths alone: a native patch's now too, not
  only a lead edit's or a restore's. A restore is refused `RESTORE_EXECUTING` while a native patch on its paths is
  prepared, allowed or running.
- A committed observation's record is removed by the observer that reads it, after it has read it. Maintenance
  never removes one, so a settlement whose acknowledgement another settlement committed still finds it. A restart
  removes leftover records and replays only pending ones, found through an index. The audit event remains.

### Fixed: a snapshot wrote and synced every file it already had
- A full snapshot wrote each file's bytes to a temporary file and synced it before finding the blob was already
  stored: 3,284 temporary files and 3,284 fsyncs a snapshot on the replica.
- Now bytes are hashed first. A stored blob is reused while its file identity (device, inode, size, mtime, ctime) is
  the one this daemon validated; otherwise it is read and checked again. A corrupt blob is refused `BLOB_CORRUPT`,
  whether read or reused. New content is still synced and renamed into place.
- A steady snapshot of the replica: 0.96 to 1.08 s before, 0.42 to 0.44 s after, with no temporary file or fsync.

### Fixed: a direct `operation/prepare` had no lifetime (a 2.4.6 known limit)
- A direct `operation/prepare` (the two-step editor protocol) now names its editor process, `executor` {pid, birth}.
  It is refused `EXECUTOR_REQUIRED` without one, `EXECUTOR_GONE` when that process is not running, and
  `EXECUTOR_LINEAGE` on a host without kernel process identities that survive reparenting (Linux).
- While the editor or any descendant lives, its paths stay held and a draining stop answers `NOT_QUIESCENT` naming
  it. Descendants are found by kernel lineage, so one reparented to launchd still counts; a process whose ancestry
  cannot be proven holds too. When all have exited, matching bytes are verified; other bytes are quarantined, with
  their UNKNOWN. Only that editor's claims are released.
- After a daemon restart the operation stays a crash gap: its bytes are observed as UNKNOWN, never taken as its
  intent. An explicit `operation/complete` then observes the editor's final bytes before ending its lifetime.
- `operation/complete` on an operation that already ended returns that operation; it used to refuse
  `OPERATION_STATE`.

### Fixed: a malformed `SOFTWARE_WORKFLOWS_HISTORY_CONCURRENCY` did not name itself (a 2.4.6 known limit)
- `abc`, `0`, `-1`, `" 2"`, `+2` and `2.0` are now refused `CONFIG` with the setting's name; 2.4.6 refused some
  with only `ValueError` and took `" 2"` as 2.
- Any other startup failure's `DAEMON_STARTUP` now carries the exception's message (up to 1,024 characters), not only
  its type.

### Fixed: a verified patch could be quarantined by a second verifier or by its listener's retirement
- Recovery and the notification worker could both verify one patch; the second found the head the first had moved and
  quarantined the verified patch `REVISION_CHANGED`, with permanent UNKNOWNs (also in 2.4.6). Every path that
  verifies a patch now takes the operation's completion lock (the parent's, for a part of a patch over several
  checkouts) and reads the operation again inside it; one already verified with the same evidence or results is
  returned as it is.
- A verifier never changes an operation that is already terminal. A quarantined or restored operation keeps its
  outcome for unchanged evidence, checked again after every wait and before any write. Evidence that contradicts a
  terminal outcome is recorded once (an `operation_conflict` record, with UNKNOWNs) and answered
  `OPERATION_EVIDENCE_CONFLICT`; a restoration is never undone.
- Stopping one app-server's listener while the daemon keeps serving (a detach, a wedged app-server's replacement, a
  readmission) finishes the work it interrupts: a patch verification in flight completes, a completed patch whose
  evidence is still queued is applied first, and the listener's outstanding patches are settled after its journal is
  replayed. A `turn/start` cut off this way keeps its input id and is marked uncertain; maintenance reads native for
  it and never sends it again.

### Fixed: a lead command's exit status and custody
- A running lead command's call owns its exit status: maintenance and `custody/reconcile` leave it to the call, so a
  command that has just exited is never settled as absent without it (a checkout-wide `*` UNKNOWN). A restarted
  daemon, which has no such call, keeps the crash gap.
- A lead command's custody is released only by a process inventory that began after its completion was recorded.
  One settlement of a custody runs at a time and reads the custody and its members again on every pass; an inventory
  that a newer inventory, a new process or a state change has overtaken is discarded, never saved. A child the
  command started that outlives it keeps the paths held until it exits.

### Changed: a lead's file edit during startup recovery is denied
- Until the daemon serves, a lead's Write, Edit, MultiEdit or NotebookEdit waits within its 2.0 s budget, then is
  denied `LEAD_ADMISSION_BUSY` naming "coordinator startup recovery", with the daemon's generation, what it is still
  recovering and an estimated retry time. 2.4.6 answered `RECOVERING`, on which the hooks let the call run
  unadmitted. Other lead calls (Bash) are answered `RECOVERING` as before.

### Changed: what `daemon/status` shows
- The event loop's lag (last, maximum), each checkout's lock (holder, phase, waiters, the oldest waiter's age) and
  each checkout's scan producer (the scan running and its phase, requests queued, the last scan's seconds).
- Counts of running observations, pre-image reads and external editors; an editor's operation, scope and
  survivors are among the pending lead edits a draining stop names.
- Each settlement records an `operation/settlement` event: from the notification's receipt to the custody release,
  its time split into phases that add up (journal, lock waits, terminal and process inspection, scan, commit,
  release, and what is not attributed).
- The full status answer is cached until a record it shows changes: its median fell from 7.5 to 3.0–5.6 ms in the
  27-writer recovery runs.

### Changed: event-loop stalls are diagnosed
- A watchdog process reads a heartbeat the daemon publishes to shared memory every 100 ms. When the loop misses it
  for 0.75 s, the watchdog signals the daemon, which dumps every thread's stack at C level (it works while the loop
  holds the GIL) into `<state>/loop-stalls.log`, after a line with the stall's wall-clock start. The log is kept to
  two 16 MiB segments. `daemon/status` names the watchdog, the file, the threshold and the limit.
- The dump goes through a nonblocking pipe that the watchdog drains on its own, so a stopped watchdog or a slow disk
  loses diagnostics instead of blocking the daemon. Every dump is marked possibly truncated, in status and in a
  `watchdog/dump_possibly_truncated` event: faulthandler does not report a short write.
- A daemon stop waits 1 s for the watchdog to exit, then kills it and waits 1 s more. The watchdog is the daemon's own
  child, not yet reaped, so its process id cannot have been reused: the stop starts no process to check it. A stop
  whose caller is cancelled still finishes, and SIGUSR2 is given back only once the watchdog is gone. With the
  watchdog stopped (SIGSTOP) during a dump five times the pipe's size, the daemon kept answering (loop lag at most
  12.3 ms) and stopped in 1.15 to 1.17 s.
- Before signalling, the watchdog checks the daemon's command line. A check that fails or times out is counted in
  status and journaled (`watchdog/lookup_failed`), then retried after 1 s, doubling to 30 s; the watchdog keeps
  draining meanwhile. A watchdog that exits on its own is journaled (`watchdog/exited`), reaped and replaced with the
  same backoff (`watchdog/restarted`). There is no retry limit, and `daemon/status` shows the next attempt. A
  watchdog that fails to start gives SIGUSR2 back.
- The watchdog is supervised until the daemon's stop can no longer roll back: a draining stop refused after it began
  (`LISTENER_DRAIN`, a lease that would not release) keeps it running. `daemon/status` shows the supervisor (running,
  restarting and why, stopped). A supervisor that ends while the daemon serves is journaled
  (`watchdog/supervisor_exited`, `watchdog/supervisor_task_exited`) and restarted after 1 s, doubling to 30 s. A
  watchdog still unreaped after its SIGKILL is journaled (`watchdog/supervisor_exited` naming it while the daemon
  serves, `watchdog/reap_failed` while it stops) and reaped again on the same backoff, SIGUSR2 ignored and its descriptors kept meanwhile; no replacement starts until it is reaped. A
  retirement attempt that fails or is itself cancelled is started again.
- Leaving `Daemon.serve` is final on every path: a stop, SIGINT, the task cancelled, or an exception. SIGINT is
  handed to the event loop and wakes it; a second or third SIGINT asks for the same stop and never cuts the cleanup
  short. The daemon marks itself stopping before it cancels anything, so the supervisor cannot restart during
  teardown. Then it stops accepting connections and closes every connection that has not sent a whole request. A
  request already admitted gets the 1 s drain budget to finish, then is cancelled and waited for: a lead command
  still running is quarantined with its custody kept, for the next start to settle. Then the daemon stops taking
  notifications from the app-servers and ends its background work while their connections are still open, so work
  interrupted in the middle of a call records nothing that a crash at that moment would not: no new UNKNOWN or
  evidence failure, and a notification not yet applied is applied by the next start. Only then does it close the
  connections, waiting for each receiver to finish. The app-server processes keep running; whatever was not yet
  journaled is read again from their history at the next start, as after a crash. Then the daemon reaps the
  watchdog, gives SIGUSR2 back, and releases its runtime directory, locks and ledger. 2.4.6 skipped that cleanup on
  SIGINT and on an exception. Measured: a SIGINT, a cancelled `serve()` and an exception in it each ended the daemon
  in 0.125 to 0.183 s (the lead's re-runs: 0.124 to 0.233 s), and the real `worker()` entry point in 0.067 to
  0.089 s (0.086 to 0.155 s), the watchdog reaped and `worker.lock` free.
- A draining stop (`daemon/stop`) also waits for the native work the daemon has taken on: a notification it has
  journaled but not yet applied, and an app-server's request still being handled. Until they finish it answers
  `NOT_QUIESCENT`, naming the listener, the notification's sequence and its method. A stop accepted while a finished
  turn's terminal inventory was still being read failed that read, and a checkout-wide `*` UNKNOWN followed (measured
  on the development tree before this fix).
- Every close the daemon makes of a native connection, or stop of an app-server's listener, is its own retirement,
  never a lost peer: a native call it interrupts is cancelled and applied by the next start, and nothing records
  `NATIVE_DISCONNECTED`, an evidence failure or an UNKNOWN for it. A connection the app-server drops while the daemon
  serves is still recorded as one.
- Such a close waits at most 1 s for its close frame to flush, then cuts the connection off, whatever task is
  waiting for it, and journals `native/close_escalated` with how long it waited. With an app-server stopped behind a
  full socket buffer (8.4 MB unread), the daemon exited 1.04 s after SIGINT. 100 real close round trips with Codex
  0.163.0 took at most 0.43 ms.
- Cancelling work never abandons a file read or write already running in a thread. A cancelled lead edit, restore
  or patch waits for its writes to end, then quarantines what they left; when one of several sibling writes fails,
  the others are waited for first. A task whose work fails while it is being cancelled still stops, and the failure
  is kept for diagnosis. The daemon cancels any task still running again every second until all have ended.
- Retiring something the daemon owns (a child process's TERM, the wait, KILL and the reap; its runtime directory; a
  connection) finishes before its owner sees a cancellation, however many arrive. A retirement starts again only
  when it is itself cancelled again; a wait it depends on that was cancelled for good is an error, never a retry
  loop, and the waits shared with other tasks (a stream's close) are joined without being cancelled. The daemon's
  calibration app-servers, which run in a session of their own, are ended before it exits: with one that could not
  stop, the stop took 11.49 s, the 1 s drain budget and the 10 s grace, then KILL and the reap.
- The daemon's long-lived services (maintenance, the external-editor observer and the lead-call observer) are
  supervised while it serves: one that ends is journaled (`service/restarting`) and started again after 1 s, doubling to 30 s, and the
  delay resets after 30 s of healthy service. A native connection's consumer that ends unexpectedly stops the daemon
  (`service/failed`) instead of leaving it serving without one. A native call cut off by its connection's retirement
  is that item's outcome (`maintenance/retry`), retried after the listener rebinds; it never ends maintenance.
- The daemon no longer calls Python 3.13's `Server.abort_clients`: it runs on Python 3.11 and later, as declared.
- An idle daemon writes nothing and starts no thread for it: 0 bytes and 0 re-arms in 10 minutes, where the
  development build wrote 541 KB and re-armed 5,955 times.

### Measured: first recovery
- 27 writers holding 1,997.86 MB of synthetic histories and a 2.66 GB journal (321,357 rows), first recovery,
  installed 2.4.6 and 2.4.7 in both orders, every history synthesized fresh:
  - a fixed set of requests (500 full and 500 brief status calls, 25 lead Writes, 25 writer turns), 2.4.7 first and
    then 2.4.6 first, the host at load average 13.4 to 15.3: 101.25 and 110.66 s on 2.4.6, 85.73 and 89.31 s on
    2.4.7 (17.4 % faster on average), with 7.0 % less event-loop CPU and 20.2 % less SQL time. 2.4.7 runs 292 more
    SQLite transactions a recovery (1.19 %). Earlier development rounds measured both ways (round 16: 58.34 and
    60.00 s on 2.4.6, 59.88 and 56.99 s on the tree), so this is the measured comparison, not a promise of a faster
    recovery under every load;
  - the lead's re-runs of those pairs, the host at load average 17.6 to 20.0: 132.76 and 110.62 s on 2.4.6, 139.41
    and 118.70 s on 2.4.7 (6.1 % slower on average). The two sets disagree, so neither shows which version recovers
    faster.
- The cost behind slower tree recoveries in earlier development rounds: a lead edit's admission saved each phase
  label in a commit of its own, about 25 a call. A label is now saved with the call's next durable change.
- A hard-kill restart took 1.62 to 1.63 s on 2.4.7, decoding 42.7 to 46.0 KB of history (the lead's: 3.81 and 3.88 s,
  decoding 44.9 and 46.0 KB, at load average 17.6 to 20.0; 2.4.6 there 3.61 and 4.65 s).
- A burst of 27 writers × 20 turns with streamed deltas: 61.34 s, lead Pre p50 / p99 / max 0.106 / 1.109 / 1.128 s
  (n=154), writer Pre 0.624 / 0.938 / 1.042 s (n=540); no hook warning, denial or quarantine. The lead's three runs, at
  load average 19 to 62, recorded only 189 to 270 of the 540 turns by the time they were measured (7,351 to 10,378
  events a minute), with lead Pre p99 2.238 to 2.346 s and no warning, denial or quarantine either. Installed 2.4.6,
  run between them, recorded all 540 (18,540 a minute) with lead Pre p99 3.282 s, and in its next run let a lead
  hook return before its admission ended.

### Known limits
- The 2.0 s budget is measured, not a real-time guarantee: the daemon answers at its deadline only when its event
  loop is free to run the timer, so a loop held by one long step, or a host that does not schedule the daemon, can
  still push an answer past the installed hook's 3 s timeout, and the installed hooks then let the call run
  unadmitted, as before. On the release tree the slowest answer was 1.563 s (installed hook) and 1.196 s (2.4.7
  hook) at load average 10.8 to 14.4, the hook's start to the daemon's receipt at most 0.765 s; the lead's re-runs:
  admitted at most 2.629 s (installed hook) and 2.400 s (2.4.7 hook) at load average 16.2 to 35.8, and 17 of 76
  first edits denied busy at 2.135 to 3.038 s, the daemon deciding up to 0.97 s past the deadline; each was retried and
  admitted, and none ran unadmitted.
- The kernel start is used on macOS only. Elsewhere the 2.4.7 hook's own deadline applies, and an installed hook's
  budget starts at the daemon's receipt. A failed lookup also falls back to the receipt, and the admission records
  why.
- Whether a hook file is the plugin's own is decided once per daemon and path: a checkout whose `hooks.json` is
  edited while the daemon runs keeps its first answer until the daemon restarts (only where the budget starts can
  change), and each distinct script path a client runs stays in that cache.
- A stop waits for file reads and writes already running in threads. A filesystem call that never returns keeps
  the daemon from exiting: nothing is released while a write might still be running. A stop also waits for the
  daemon's calibration app-servers to end: up to 10 s for one to exit after TERM, then KILL; one the kernel cannot
  reap keeps the daemon from exiting.
- A second SIGINT that comes after `serve` has returned, while `asyncio.run` closes its loop, ends the worker by
  `KeyboardInterrupt` once its stop is complete, and the supervisor, which ends with its worker only on exit 0 or 3,
  then starts a new worker. 2.4.6 ended that way on any second SIGINT during its stop. Seen once, in the lead's
  re-run of a recipe that sends a second SIGINT.
- The 1 s bound on a native connection's close is fixed, not a setting. Each connection is escalated at most once.
- A draining stop stops the app-servers one after another (30 to 106 s with 27 to 30 writers on the loaded host; 8.7
  to 136.4 s in the lead's re-runs at higher load). One
  still exiting 5 s after its TERM refuses the stop with `LISTENER_DRAIN`, as in 2.4.6; asking again then stops it.
- The stream close and the drain's queue count use CPython internals (`StreamReaderProtocol._get_close_waiter`,
  `asyncio.Queue._unfinished_tasks`), read on the installed Python 3.15; 3.11 to 3.14 were not run.
- A denied edit is not retried by the hook. The lead (the model) reads the denial and retries.
- A 2.4.4 lead hook sends no host or transcript, so a denied call keeps its paths until the 120 s legacy expiry
  (120.2 s in the run, from the call to the release; the lead's re-run 120.09 s).
- A direct `operation/prepare` needs kernel process lineage: macOS only. Elsewhere use `lead-edit`, which the daemon
  installs and verifies itself. An unrelated process whose ancestry cannot be proven keeps an editor's paths held
  until it exits.
- A repair observation waits while another change, a call, a process or custody holds its paths or its checkout. On
  a checkout that is never idle it stays pending, one per set of checkouts, until the checkout is free or the next
  start observes its paths.
- Every watchdog dump is marked possibly truncated, even a complete one: faulthandler cannot report a short write.
- A command that declares no paths makes every whole-checkout observation that overlapped its window read the whole
  checkout again (once, shared by every observer waiting). Commands that keep opening and closing windows can make
  an observation read again several times; it never commits a stale image to finish sooner.
- A watchdog the kernel never reaps after its SIGKILL delays the daemon's exit. Status and the journal show it, and
  the daemon keeps trying.
- The replica's journal was 2.66 GB, not the live 6.46 GB, and it gave one UNKNOWN per file, not the incident's
  three.

- Review turn 84 found four defects, fixed in 2.4.8 and not in this release:
  - sealing, publishing or reopening a unit whose writer is dormant is refused with `LISTENER_UNAVAILABLE`. Resume
    the writer first;
  - a tool call cut off when its app-server died stays pending, so its unit cannot be sealed;
  - a cancel during a dormant writer's start leaves the new app-server running until the coordinator restarts;
  - `run/terminals` fails for a dormant writer.
- The `writer_mcp_servers` plugin option reaches the coordinator only when a hook starts it. If the Codex Oracle MCP
  server starts it, the option is not passed: set `SOFTWARE_WORKFLOWS_WRITER_MCP_SERVERS` there (fixed in 2.4.8).
- Plugins installed after the coordinator starts stay off for writers under a list profile, until it restarts.
- Output that Codex shortens when it saves (long command or MCP output) can be reported as a `HISTORY_GAP`.
- The first comparison after the switch reads each writer's whole received journal: 146 s for 27 synthetic histories
  (2.0 GB). About 5 minutes is estimated for the live 5.5 GB.
- This release's acceptance did not complete two runs: the matched burst pairs against 2.4.6, and a real full disk.

Not measured:
- weeks of daemon uptime with the watchdog (10 idle minutes, forced stalls and rotation were);
- a host other than macOS for the editor lifetime;
- the incident's own checkout and histories (client data, read by shape only).

## [software-workflows 2.4.6] — 2026-10-23

Recovery reads native history from where it last stopped, a page at a time, and recovers at most four writers at
once. This fixes the two known limits 2.4.5's live switch exposed:
- 27 recovering writers saturated the daemon for about 5 minutes;
- a writer whose 247 MB history timed out was retried every 5 minutes for good.

A lead's edit made while a writer of the same checkout reconciles is no longer quarantined. Each change has a real
run on Codex 0.163.0 (coordinator/VERIFY.md, 2.4.6).

### Fixed: recovering many writers saturated the daemon (a 2.4.5 known limit)
- At 2.4.5's switch, 27 writers recovered at once:
  - `daemon/status` took 20 to 25 s for about 5 minutes, past the lead hooks' 14 s;
  - app-servers answered in up to 20.5 s, against their 10 s bound, so writers were quarantined.

  Each recovery read its writer's whole history, decoded on the daemon's event loop.
- Now at most 4 writers are recovered at once (`SOFTWARE_WORKFLOWS_RECOVERY_CONCURRENCY`, a positive integer,
  default 4). A full history read takes one of those slots. Waiting for a slot has no timeout, retry or quarantine
  of its own.
- Short reads have a lane of their own (`SOFTWARE_WORKFLOWS_HISTORY_CONCURRENCY`, default 2). These are a live
  writer's catch-up, a message's delivery check, and settling a resume's or a cancel's inputs. With all 4 recovery
  slots held by 151 MB histories, they finished in 6 to 61 ms; sharing the slots, they waited 2.8 s.
- `daemon/status` shows both lanes (`recovery_capacity` and `history_capacity`, each with limit, active and
  queued), the observers running (`history_observers`), and the JSON bytes decoded (`native_decode`).
- 27 writers holding 1,997.86 MB of synthetic histories, with a 2.66 GB journal:

  | | 2.4.5 | 2.4.6 |
  |---|---|---|
  | First recovery | 89.4 s, 7 writers quarantined | 45 to 65 s, all 27 recovered (88 s once, with the host at load average 11) |
  | Restart after a hard kill | 67.5 s, the same 7 still pending | 1.2 to 1.5 s, all recovered |
  | History decoded on that restart | 915 MB | 32 to 46 KB |

  During the 2.4.6 first recovery, `daemon/status` p99 stayed under 32 ms, lead PreToolUse p99 under 0.55 s and
  writer PreToolUse p99 under 0.23 s, against the hooks' 14 s. There were no hook warnings.

### Fixed: a long history was retried for good (a 2.4.5 known limit)
- Recovery asked native for every turn with all its items in one response: `thread/turns/list` with `itemsView`
  "full", then all of `thread/items/list`. A 247 MB history could not answer within `native.call`'s 60 s, and the
  writer was retried every 5 minutes, starting an app-server each time.
- Now turns are listed without their items, 25 at a time, and each turn's items are read 25 at a time. A durable
  checkpoint per writer (`native_history`) holds a native cursor saying where reading stopped. A position is saved
  only after everything before it has been applied.
- Any of these starts a full re-read:
  - a checkpoint that is missing or malformed;
  - a cursor native refuses, whatever native's wording;
  - a `thread/reverted` notification;
  - a replaced or truncated rollout file.
- A 251 MB history was read in 2.67 s (30,002 items), where 2.4.5 timed out at 60 s. The next read of the idle
  thread decoded 1,092 bytes.
- A thread that native keeps in `historyMode` "legacy" is refused with `NATIVE_HISTORY_UNSUPPORTED`. The coordinator
  starts writer threads without naming a mode, and on Codex 0.163.0 every such thread in these runs was
  "paginated". A thread started "legacy", whether asked for or by a configuration these runs did not use, is refused.

### Fixed: a lead's edit was quarantined while a writer of its checkout reconciled
- After every reconcile, a writer's recovery records what changed in its unit's checkouts. It also recorded a
  lead's edit whose PostToolUse had not yet settled. The edit then failed its own check with `REVISION_CHANGED`,
  was quarantined, and left its file UNKNOWN. This happened at every writer reconcile, including the 27 at a live
  switch.
- Now that snapshot leaves out paths held by lead calls this daemon admitted. Those settle through their own check,
  as with no reconcile running. A call an earlier daemon admitted is judged as before: its intent is no proof.
- A command's quarantine scan also keeps unrelated lead calls, and still records its own bytes.
- A restore, and a lead PostToolUse with no hold of its own, refuse or wait while an overlapping admitted call can
  still write. A restore used to apply its snapshot first and only then find the conflict.
- A lead edit or restore spanning two checkouts is kept the same way. A writer's reconnect used to quarantine such
  a group while it was unfinished, leaving 4 to 6 UNKNOWNs on its two paths. It now completes, with a receipt in each
  checkout.
  A group an earlier daemon admitted, and a native writer's, are quarantined as before.
- 27 writers with changing lead Writes: every Write verified (141 to 204 in a run), with no path UNKNOWN. Before
  the fix, the same run had one quarantined with `REVISION_CHANGED`, two paths left UNKNOWN, and 111 later
  admissions refused.

### Fixed during review: a command that finished after its turn
- A code-mode command can keep running after its turn ends, and native lists it only once it completes. A turn now
  stays under observation while any call admitted in it lacks its completion. When the call's end shows (its
  result, its Post hook, or its app-server's exit) and its completion is not after the turn's last read item,
  that turn alone is read again, once.
- On real 0.163.0: a command finished while the daemon was down, after its turn ended and a newer turn had moved
  the checkpoint. Its completion was applied at restart, its custody released, with no UNKNOWN. Before the fix the
  completion was missed and custody stayed held.

### Changed: the notification journal is replayed from where it was applied
- Recovery replayed every journaled notification of a writer at every reconcile. Each listener now has an applied
  prefix (`native_applied`):
  - recovery replays only what lies past it, 25 rows at a time, yielding between rows;
  - the prefix moves in one transaction every 100 ms or 256 notifications;
  - a failed notification freezes it until a recovery replays what follows;
  - a reconnect first holds it at the last notification received before.
- A tool call native sends (`item/tool/call`) is recorded as owned by the daemon that received it, in the same
  transaction as its journal row. A replay never takes such a call from its handler.
- A daemon killed while a tool call ran no longer leaves it silently pending:
  - the next daemon records a `*` UNKNOWN, "native server request interrupted before durable completion";
  - a repeated request is answered `DYNAMIC_UNCERTAIN`;
  - the tool is never run again.
- The first reconcile after upgrading replays each writer's 2.4.5 journal once. A 700,000-row journal of the live
  shape (2.58 GB, 15,000 completed tool calls) took 19 to 20 s, with event-loop delay p99 2.4 ms (25.5 s and
  4.9 ms with the host at load average 11). A tool call 2.4.5 already answered gets no new record.

### Changed: rollout files are read incrementally
- Policy readback (a workflow phase's) and input diagnostics read a writer's rollout from where they last stopped:
  - the position is a complete-record offset;
  - only small facts are kept: hashed user texts and turn-context policy;
  - an unchanged compressed rollout is not opened again.
- They no longer decide where an input went; native's own history does that. The rollout's facts (damage, an
  unmatched text) are told to the lead as before and never taken as proof.

### Changed: an exited writer's inputs are read through a temporary app-server
- An input sent to a writer whose app-server has exited is looked up in native's own history. The coordinator
  starts a temporary app-server of its own to read it, and stops it after.
- On 0.163.0 that reader leaves the thread unloaded, and never starts a queued input. With two queued inputs,
  reading the queue, the thread, its turns and its items, then deleting one entry, left the other queued. No turn
  started.
- A cancel deletes a queued input of an exited writer through the same reader. Before, it was refused while the
  writer's app-server was not connected.

### Known limits
- A direct `operation/prepare`, the two-step editor protocol, has no lifetime of its own. An editor that disappears
  without `operation/complete` leaves its operation prepared: it holds its paths and refuses a draining stop until it
  is completed, or a later daemon takes it as a crash gap. The plugin's lead hooks and the daemon's `lead-edit`
  settle their own.
- A malformed `SOFTWARE_WORKFLOWS_HISTORY_CONCURRENCY` stops the daemon from starting, but the error does not name
  it: `DAEMON_STARTUP`, "ValueError". `SOFTWARE_WORKFLOWS_RECOVERY_CONCURRENCY` names itself (`CONFIG`).

Not measured:
- an unfinished code-mode item followed by hundreds of large outputs, as native would naturally persist it (the run
  used a synthesized projection read through the real app-server);
- a day-long soak;
- a cold supervisor and admission launch;
- arbitrary single-item sizes (native's pages bound item counts, not bytes).

## [software-workflows 2.4.5] — 2026-10-23

The office opens by itself, and Claude no longer runs a log tail. Three of 2.4.4's known limits are fixed: a lead edit
lost its hold after 120 s, a stop could leave an app-server running, and a writer quarantined at startup stayed
quarantined. Each fix has a real run (coordinator/VERIFY.md, 2.4.5).

### Changed: the Codex Office opens by itself, and Claude no longer runs a log tail
- Until now, every Codex call made a hook tell Claude to start a background `tail -F` of the runs' stream log. That
  cost context on every call. The office, where the user actually watches runs, started only when they typed
  `/software-workflows:office`. On 10-22 the office still running was the 2.4.0 copy. A Homebrew upgrade had
  removed the Python it ran on, so its requests failed with `ModuleNotFoundError`.
- Now `hooks/office_autostart.py` starts the office in the background on a session's first Codex call. It opens the
  office in the browser once per session, and later in the session starts it again only if it has stopped. It
  prints nothing, so it adds nothing to Claude's context. The tail instruction is gone; `hooks/live_view_nudge.py`
  only names the log for the lead's coordination exemption.
- `office.py start` replaces an office started by another plugin version, as before, and now also one whose
  recorded Python no longer exists. `server.json` records the interpreter.
- The office runs detached and outlives the session that started it. It now starts without that session's
  variables (Claude Code's session and hook variables, `CODEX_GATE_*`, the push gate's git shims on PATH), the same
  scrub the daemon has had since 2.4.4.
- A start never imports anything from the session's project folder: the hook runs the office with `python -I` from
  the office's own folder, and so does the office's own detach step.
- A session's claim on its first start is a locked marker. The start inherits the lock, and marks the claim done once
  it has run and the office runs, so two calls at once open the office once. Anything else leaves the claim not done
  and unlocked:
  - a start that fails or dies;
  - a start that gave up waiting for the start lock;
  - a hook that dies before spawning one.

  Until the office runs, each Codex call tries again (and opens the office once it is up).
- A failed start is told to the person once per session: at once with its reason when the hook could not spawn it,
  otherwise at the next call, naming `office/autostart.log`. A start that works clears the failure. The log restarts
  after 1 MiB.
- An office counts as running only while its recorded pid still has its recorded start time, so a pid the system
  has reused does not count. Every 10 minutes a session's call also has it checked in full: that it answers and
  that it is this plugin's.
- The hook keeps its per-session markers in a private directory of the user's own. A symlinked or shared directory
  is refused with a message. The daily sweep deletes only regular files named like markers, and never a marker a
  start or a claim holds.
- Verified for real, against a scratch office on another port and data directory, with a browser stub that records
  each open:
  - the first call started the office and opened it once;
  - a second call in the same session started and opened nothing;
  - a new session opened it again;
  - a killed office was restarted, without opening another tab;
  - an office whose recorded Python was removed was replaced;
  - `off` started nothing;
  - an invalid value showed one message per session;
  - the office's own environment held none of the session's variables or shims.
- Codex's review (session 01a14f98) found one HIGH and four MEDIUM findings at turn 5.
  - Turns 6 to 8 found more paths of two of them: a start that failed, gave up or never ran yet kept or completed
    the session's claim, and the delivery wait below.
  - Turn 7 also found two LOW findings: the sweep could remove a claim being taken over, and the page did not redraw
    a note's delivery change.
  - Turn 8 found two more interleavings: a start that gave up just before its office answered, and a sweep acting on
    an age it read before taking the marker's lock.
  - Turn 9 said "Ship it", with one LOW finding: a claim could lock a marker the sweep had just replaced.

  Each is fixed, and every case above ran again on the final code, plus these:
  - a `subprocess.py` planted in the session's folder was not imported;
  - a start that failed in the office (its `office.log` made a directory):
    - left the claim not done each time, and the person was told on the next call only, not on the one after;
    - after the repair, the next call started the office, opened it once and cleared the failure;
  - a start the hook could not spawn (`autostart.log` made a directory) was told in that same call, naming
    `IsADirectoryError`; after the repair, the next call started the office and opened it once;
  - a claim left empty by a hook that died, and a start killed with SIGKILL while it waited for the start lock, were
    each taken over by the next call, which opened the office once;
  - four calls of a new session at once opened it once;
  - with an office running and the start lock held 45 s, a new session's start gave up after 30 s and opened
    nothing. The claim stayed not done, and the office opened once after the lock was released;
  - a 40-day-old empty claim taken over while the daily sweep ran was kept, and opened the office once. Another
    session's 40-day-old marker was swept;
  - turn 8's two interleavings, forced:
    - a start that gave up while the office answered by the time the claim was judged left the claim not done; a
      start that found the office and opened it completed it;
    - a marker judged old at listing time, then taken over and completed before the sweep locked it, was kept; one
      still old under the lock was removed;
  - turn 9's interleaving, forced: a claim whose marker was swept and claimed anew between its open and its lock did
    not proceed; without that interference it claimed;
  - a symlinked sessions directory, a symlinked state directory and a sessions directory open to others were each
    refused with a message, and nothing was started;
  - the symlink's target kept its files, including a 40-day-old file named like a marker.

### Added: the office's settings explain themselves
- The plugin has its first user setting, `office`: `open` (default), `background` or `off`. Claude Code shows it,
  with an explanation of each choice, in `/plugin` (Configure options) and `/config`.
- The `SOFTWARE_WORKFLOWS_OFFICE` environment variable overrides it for the sessions that have it.
- `CODEX_OFFICE_PORT` still picks the port.
- The README's "Office settings" section gives each setting's values, default and effect.
- `claude plugin validate` passes on 2.1.306.

### Added: Expand, to read a worker at full width
- The worker panel always shared the window with the office floor: at most the window width minus 340 px,
  560 px by default.
- **⤢ Expand**, or the `E` key, now gives an open worker the whole window, keeping its text to a readable width.
  `E` or `Esc` brings the office back. The choice is remembered.
- Measured in Chrome on a 1512 px window: the panel went from 560 px to 1512 px, and the Answer tab rendered in
  full. Esc returned to 560 px with the worker still open, and closing the worker showed the office floor.
- While expanded, the roster stays closed. Otherwise it would sit open, hidden behind the panel, and take the next
  Esc.
  - The roster button leaves Expand first.
  - A worker revealed while expanded no longer opens the roster.
  - Checked on the final page in Chrome with 42 workers, step by step:
    - `E` expanded, and a reveal opened no roster;
    - the roster button left Expand and opened the roster, and the next Esc closed it;
    - `E` and Esc returned to the office with the worker still open, and a second Esc closed the worker.

### Fixed: the office had no label for an unproven delivery (a 2.4.4 known limit)
- The office now reads the coordinator's `message/delivery_unproven` as a final outcome and labels the note
  **⚠ unproven**. The label says the note may already have run, and that a new Send could run it twice.
- Before, it read "could not tell whether it reached the turn" or "send it again". Checked against recorded event
  sequences: an `entered_context` event still wins over `delivery_unproven`.
- A run's stop no longer settles a note by itself, because a run's status follows its turns, not its deliveries.
  Some notes are in flight with no event that says so:
  - a send whose answer the coordinator still awaits (it records "submitting" without an event);
  - a note Codex had (`submitted`, or `delivery_uncertain` after a lost send result), which the coordinator settles
    from the thread's history after the turn: entered, unproven or cancelled, or back to wait for the next turn.

  Once the run reads stopped, the office reads the note's own record (`records/list`): after 2 stopped polls, then 4,
  8, … and then a minute apart. The live ledger's 2,087 notes take 12 ms to read and decode. Only a note the record
  shows waiting ends as held for the next turn. Event or record reads that fail twice end as unconfirmed, never as
  held.
  - 2.4.4 stopped waiting at `delivery_uncertain` and labelled the note "could not tell".
  - It stopped waiting at a submitted note once the run read stopped twice, and advised sending it again.
- A note whose run was cancelled before the turn took it reads **✖ not delivered**. Before, it read as waiting for the
  next turn.
- The page redraws a note when only its delivery changes, so "Codex has it" and "the coordinator is checking" show as
  they happen.
- Checked against stubbed coordinator answers, the run stopped throughout:

  | Events and the note's record | The office's wait ended |
  |---|---|
  | none; record waiting | held for the next turn, after 2 stopped polls (one record read) |
  | none; record submitting until the events show it entered at the 9th read | entered (done); records read at polls 2, 4, 8 |
  | uncertain; record back to waiting | held for the next turn |
  | submitted; record entered (its event not read yet) | entered (done) |
  | submitted; record unproven, or none and record cancelled | unproven; not delivered |
  | event reads failing; record reads failing | unconfirmed (the coordinator stopped answering), after 2 failed reads |
  | submitted; record submitted for 600 polls | unconfirmed at the call's budget; records read at polls 2, 4, 8, 16, 32, 64, 124, 184, … |
  | a run still running, never sent | no record read |
  | entered, or unproven, then cancelled | entered, or unproven: the earlier outcome stands |

### Fixed: a stop leaves no app-server running (2.4.4 review turn 48)
- On 2.4.4, a listener reconcile could start an app-server after a stop had taken its snapshot of listeners.
  Maintenance settling a plain run's cancel did this, and nothing stopped the new app-server.
- Measured on 2.4.4: a forced switch with 13 writers in 4 checkouts and a reconcile injected into the drain ended
  with a leftover app-server.
- Now every listener reconcile is counted from the one entry point they share, including while it waits for its
  run's lock.
  - A stop's drain waits for the reconciles under way, and `NOT_QUIESCENT` names them in
    `details.listener_reconciles`.
  - Once the stop closes, every new reconcile is refused `DAEMON_DRAINING`: maintenance, cancel, resume,
    re-admission, transport-gap recovery and startup retries alike.
- `daemon/status` shows the reconciles under way.
- The same injected run on 2.4.5: the stop was accepted, a writer's patch verified, and no app-server the run
  started was left.

### Fixed: a writer quarantined at startup heals itself
- On 10-22 a live switch re-admitted 26 writers at a load average of about 17. Nine writers' `thread/resume` did not
  answer in time, and each was quarantined with a checkout-wide `*` UNKNOWN. Nothing retried them, so a lead
  narrowed the UNKNOWNs by hand.
- Now a startup failure that can pass on a later attempt is a durable incident, and the daemon retries it itself.
  - Retryable failures: native or transport failures (`NATIVE_UNCERTAIN`, `NATIVE_DISCONNECTED`, `LISTENER_SILENT`)
    and resource or lease contention.
  - Delays start at 5 s and double up to 300 s, with no attempt limit. `SOFTWARE_WORKFLOWS_RECONCILE_RETRY_FIRST_S`
    and `SOFTWARE_WORKFLOWS_RECONCILE_RETRY_MAX_S` set them.
  - A retry makes the same history, admission and replacement checks as a successful startup. It then reads each
    checkout's bytes and records a `recovery_resolution` for exactly the `*` UNKNOWNs that startup left. Paths that
    differ keep their own UNKNOWNs.
  - Policy, project, schema, configuration and missing-history refusals stay quarantined, as before.
  - Startup adopts 2.4.4's quarantines and their UNKNOWNs, including ones a lead has already narrowed.
  - Events record each incident's progress: `listener/startup_retry`, `listener/startup_quarantined` and
    `listener/startup_recovered`. `daemon/status` counts the incidents still outstanding.
- Recovery resumes ask Codex for `excludeTurns:true`, honoured by Codex 0.163.0, and still read every turn through
  pagination afterward.
- Measured with injected lost resume responses in a forced switch of 13 writers:
  - two transient failures healed by themselves, 62.7 s after recovery began, with no lead call;
  - their `*` UNKNOWNs carry a recorded resolution (nothing found);
  - a timeout that never passes failed 5 attempts, 6, 11, 20 and 40 s apart, and stays quarantined;
  - a policy mismatch stayed quarantined without a retry.
- Measured to size it: 26 writers in 4 checkouts of 3,700 files each.
  - Recovery took 66.4 s before and 68.5 s after, with no quarantines or resume timeouts in either run.
  - The slowest resume took 0.117 s before and 0.094 s after, at a load of up to 18.7 with 26 reconciles under way
    together.
  - That leaves the 60 s native timeout ample headroom, so no concurrency bound and no longer timeout were added.
- A dead listener that another listener replaced now reads `recovery_state: "replaced"`, with `replaced_by`. Before,
  it read `reconciling` forever: the live ledger had 43 such records, 17 of them from 10-22. Startup repairs the old
  records whose replacement it can prove.

### Fixed: a lead edit lost its hold after 120 s (a 2.4.4 known limit)
- On 2.4.4, a lead's Write or Edit that no Post or batch settled within 120 s was quarantined. A permission prompt
  left open, an interrupt or a killed session did this. The call could still write, but from then on it held neither
  the checkout's compatibility lease, a stop, other leads' quiet windows nor a restore. After its Post, the edit stayed
  uncertain, with UNKNOWNs that needed a restore or a narrow.
- Now a lead's file call holds its paths from its PreToolUse until the call has really ended. No timer ends it. Any of
  these does:
  - its PostToolUse or PostToolUseFailure;
  - its PostToolBatch;
  - its `tool_result` in the session's transcript;
  - the exit of the Claude Code process that made it, known by its pid and start time.
- Until then the call keeps its claims and the checkout's compatibility lease. It holds a stop (`NOT_QUIESCENT`, which
  names it in `details.lead_edits`) and other leads' quiet windows. A restore of its path is refused
  (`RESTORE_EXECUTING`), and so is a handback. `daemon/status` lists open calls under `lead_edits`.
- The daemon looks at each open call's transcript and process every 0.25 s. A call that ended without a Post is
  settled by its file's bytes:
  - unchanged: no effect;
  - the intended image: verified;
  - anything else: quarantined with an UNKNOWN, at once.
- A call whose transcript or process cannot be read stays open until its Post, and `daemon/status` reports why
  (`observation_problem`). It never holds up startup or another checkout. A read that hangs holds up nothing either:
  - the call's own Post or batch still settles it;
  - a settlement that fails is still retried every 3 s;
  - a stopped daemon still exits, so the next one can start.
- At the switch, lead calls that 2.4.4 had settled keep their outcomes, and calls still open continue under the new
  rule. A late Post or batch of a settled call changes nothing, so it cannot disturb a restore of its file.
- A PostToolBatch's calls now go to the daemon in one request, and the hook first writes them to a private outbox in
  the state directory. The daemon records every call before it reads any file. A call no daemon has acknowledged stays
  in the outbox for the next daemon. So no call is lost to the hook's 14 s, to a hung or stopped daemon, to a 2.4.4
  daemon still running during the switch, or to the hook being killed while writing the outbox. A refusal ends a
  call's delivery. Any other failure is retried every 3 s; once the call's own record holds its completion, that
  record alone retries it, apart from the daemon's other work.
- An overlap with an open lead call is refused as a claim conflict naming the holder, even when the holder is the
  caller's own session or a call still being admitted. 2.4.4's hooks deny such a conflict too, so a session not yet
  restarted after the upgrade is held to the same rule.
- As on 2.4.4, only a refusal the daemon answers denies a lead's file call. The call runs when:
  - the daemon is unreachable, stopping or failing;
  - its error answer has no valid `kind`;
  - the hook's own checks of the state directory or the daemon's endpoint fail. Then it does not contact the
    endpoint at all.

  The person is told once per session. A call that runs without admission has its file reconciled at its Post or
  its failure, as on 2.4.4, so changed bytes are UNKNOWN at once. Without either, its batch reconciles it, even when
  its Pre left no marker. Only that call's file is read, and nothing is admitted for it, so a legacy writer's tree
  lock does not stop it. A file that cannot be read yet is retried by the daemon until it can. The call gets no
  receipt and holds nothing.
- During the switch, a 2.4.5 session's hooks accept the replies of a 2.4.4 daemon that is still running.
- Measured with real `claude -p` sessions (2.1.306). In each, another PreToolUse hook held the Write about 150 s after
  the coordinator admitted it:

  | | 2.4.4 | 2.4.5 |
  |---|---|---|
  | the edit 125 s after its Pre | quarantined | still open |
  | a legacy writer's tree lock, meanwhile | taken | refused: the lease is held |
  | `daemon/stop`, meanwhile | accepted | refused `NOT_QUIESCENT`, naming the call |
  | a restore of the path, meanwhile | ran | refused `RESTORE_EXECUTING` |
  | another lead's Bash, meanwhile | given a quiet window | no quiet window: what it changed is UNKNOWN |
  | after the Post | uncertain, with an UNKNOWN | verified with an exact receipt; a stop is accepted |

  - An interrupt while the Write waited: 2.4.4 quarantined the edit at 125 s with an UNKNOWN. 2.4.5 ended it from the
    transcript 10 s after the interrupt, with no effect and nothing unresolved.
  - A killed session: 2.4.4 quarantined the edit at 125 s with an UNKNOWN. 2.4.5 ended it within 15 s of the kill,
    with no effect, as its file was unchanged. With partial bytes written before the kill, the edit was quarantined
    with an UNKNOWN within 5 s.
  - Twelve lead-hook cases from 2.4.4 (refused, ignored and stale paths, failed and denied edits, claims, handbacks,
    prompts), run through the real hook: ten gave 2.4.4's outcomes. The two whose permission prompt stayed open past
    120 s now end verified, where 2.4.4 left 3 and 2 UNKNOWNs.
  - With the daemon stopped, two Writes ran with one warning, and the next reconcile recorded the file UNKNOWN.
    Injected failures (recovering, lease, malformed and lost answers) each let the call run. One that ran after a
    failed admission had its file UNKNOWN at its Post.
  - A Write whose Pre never reached the daemon, run while a legacy writer held the tree's lock: its file was UNKNOWN
    at its Post, with no unit, claim or hold, and the legacy writer kept its lock. With only a batch and no marker,
    the batch recorded it at once. A file unreadable at the Post was recorded once it could be read.
  - A batch of four settled Writes and one whose Pre and Post never reached the daemon, with the daemon's worker
    stopped while the hook ran: the hook returned in 3.06 s. Once the worker continued, the fifth file was UNKNOWN.
    With a 2.4.4 daemon answering, or no daemon at all, the next 2.4.5 daemon recorded it from the outbox. Batches of
    1, 3 and 5 settled calls took a median 0.07 to 0.08 s through the hook (2.4.4: about 0.045 s).
  - A 2.4.4 Write left `failed`, then its late batch while its file was being restored: the record was kept, nothing
    was recorded, and the restore resolved.
  - With a call's transcript read held stalled, a stop's worker still exited in 0.26 s, and the next daemon served in
    0.94 s. A settlement that failed meanwhile was retried after 3.2 s and verified, with the read still stalled.
  - With the installed 2.4.4 hook against 2.4.5, a second Write of a path an open call holds was denied, whether it
    came from the same session, another session, or arrived while the first call was still being admitted.
  - A state directory open to others, or an endpoint manifest naming another state root: the Write ran with one
    warning, and the hook made no connection.
  - Four 2.4.4 lead-call records were left `prepared` after their operations had ended, as when a hook dies before
    saving. At the switch they took their operations' outcomes: one verified, three with no effect. The ledger still
    held 202 receipts, and a stop was accepted.
- A session still running 2.4.4's hooks after the upgrade sends neither its process's identity nor its transcript.
  Its file calls end at their Post or batch, or 120 s after their Pre, as on 2.4.4. At 120 s the call is quarantined
  with an UNKNOWN, counted as `lead_hook/legacy_limit` and in `daemon/status` as `lead_edit_legacy_limit`. Measured
  with the installed 2.4.4 hook against 2.4.5: released at 120 s with an UNKNOWN, the lease freed, and a stop
  accepted.

### Known limits
- A lead's PreToolUse hook that Claude Code ends at its 15 s timeout lets the call run without admission (review turn
  49, HIGH). The plugin's hooks do not set `onFailure: "block"`, which Claude Code 2.1.306 supports. A hook that
  cannot finish counts as an unavailable coordinator, which never blocks a lead's call; with that setting, a broken
  `python3` would block every lead edit. Nothing holds such a call's paths, and the next reconcile records its changes
  as UNKNOWN. The hook's own daemon calls are budgeted to end by 14 s.
- A permission prompt left open holds a stop, the checkout's lease and other leads' quiet windows until it is
  answered, interrupted or its session ends. `daemon/stop` answers `NOT_QUIESCENT` and names the call.
- A session on 2.4.4's hooks or older keeps 2.4.4's 120 s limit until it restarts. An interrupted file call of such
  a session holds its file for those 120 s, so retrying that edit is refused until then. Restart sessions after
  upgrading.
- A lead file call open while the daemon is replaced (a version switch, a worker restart) keeps its hold until it
  ends, but its edit is quarantined: its file is left UNKNOWN, for a restore or a narrow.
- A daemon that recovers many writers at once is saturated while it does. Measured at the live switch on 10-23 with
  27 writers (coordinator/VERIFY.md, 2.4.5):
  - For about 5 minutes `daemon/status` took 20 to 25 s, past the lead hooks' 14 s. A lead edit then would run
    without admission and leave its file UNKNOWN; none did.
  - App-servers answered in up to 20.5 s, against their 10 s bound, so writers were quarantined. All but one healed
    within 25 minutes.

  A bound on concurrent recoveries, sized from this measurement, is planned for 2.4.6.
- A writer whose native thread keeps failing recovery (`thread/turns/list` answering `NATIVE_UNCERTAIN`) is retried
  every 5 minutes for good, and each retry starts an app-server.

## [software-workflows 2.4.4] — 2026-10-22

Everything 2.4.3 left open: its known limit (a lost start judged by a 2 s rule), the candidates reported by the
SmartPay and Sales_Profiler leads since the 2.4.1 switch, and the defects found while fixing them. Each fix has a real
run that fails on 2.4.3 and passes on 2.4.4 (coordinator/VERIFY.md, 2.4.4). A forced switch with 13 writers now
answers leads after about 2 s instead of after the whole recovery (187 s).

### Fixed: the daemon and its writers carried the launching session's identity
- The coordinator daemon and every writer app-server inherited the environment of the Claude Code session whose Bash
  started them: its messaging socket and token, its session id, `CLAUDECODE`, the push gate's `CODEX_GATE_*` wiring,
  and a PATH whose first entry was that session's plugin copy's git shim. Every writer's git ran through an old
  release's push gate, attributed to that session.
- The daemon (serve and worker) and every app-server now start without Claude Code's session and hook variables,
  without `CODEX_GATE_*`, without any plugin version's shim or exec-path mirror on PATH, and without a mirror
  `GIT_EXEC_PATH`. User configuration is kept.

### Fixed: git failed in a session resumed after an upgrade
- A resumed session has the new version's shim first on PATH and the old one after it. The new shim took the old
  shim for the real git, and git failed: "The push gate could not build or inherit a mirror of git's exec-path".
- The shim, the push gate and SessionStart now skip every plugin version's shim and every mirror. SessionStart leaves
  exactly one shim on PATH, this version's, first.

### Fixed: the push gate asked about read-only commands with a regex end anchor
- Any `$'` sent a command to the full detection, so `grep -E '^[0-9a-f]{64}$'` or a `docker ps` pipeline asked for an
  acknowledgement whenever a review was stale.
- When `$'` is the only trigger, the worker now allows the command if it proves a closed grammar: allowlisted programs
  that cannot run their arguments (docker limited to inspection), every `$` single-quoted, no substitution,
  redirection or expansion. Anything else is asked about as before (round 52 in `_ansi_c_quote`).

### Fixed: a cancel judged a lost start by elapsed time
- 2.4.3 marked an input whose `turn/start` result was lost `not_started` after two idle reads 2 s apart. Native bounds
  nothing there: a late turn could start after the run read `cancelled` (its calls were refused until a resume).
- The thread is read in its own request order. Where that cannot decide, the writer's app-server is ended (birth
  identity checked), so that nothing can start the input any more, and its rollout, final once the process has
  exited, is read. No elapsed time decides anything.
- Only native's own record of an input settles it: its client id's record in the rollout or in the thread's history
  (the turn it entered), native's queue holding it, or `thread/queue/delete` answering `deleted: true`. No silence
  proves an input never ran: native goes on from memory when a rollout append fails, and its log drops a line it
  cannot write (a full disk). An input that nothing can start any more and that no such record shows is `unproven`:
  one sent to an app-server that has exited, a queued one native's queue no longer holds, and on a running app-server
  one whose turn has ended or a steer while no turn runs. Its reason names what the rollout and the log show (a
  failure native logged after the send, a log showing nothing of native's session after it, damage to the rollout, a
  user message holding its text without its client id's record, by turn), none of it as proof: a text match never
  settles an input.
- The same settlement now ends the two other waits nobody could end: a resume of a run whose phase was left uncertain
  (2.4.3 refused it `SUBMISSION_UNCERTAIN` forever) and a message delivery left uncertain (2.4.3 held every stop
  `NOT_QUIESCENT`). Maintenance settles uncertain deliveries in the background.
- Nothing sends an `unproven` input again of itself. The run reads `submission_uncertain` (the writer's result carries
  `input_unproven`) until the lead resumes it, and holds no stop and no compatibility lease meanwhile, as it runs
  nothing; the resume sends the input ahead of its own text (`run/input_resent`,
  `reason: resumed_unproven`, with the `proof`). `message/wake` refuses an `unproven` delivery (`MESSAGE_WAKE`, naming
  `message/retry`), and only `message/retry` sends it; either may run it twice. A cancel ends the run without it; a
  coordinator notice is owed again (it starts no turn, and its id tells a copy apart). Its run never ends `failed` for
  it.
- `run/cancelled` is emitted once per cancel generation and carries the generation. A cancel still waits for a phase's
  `turn/start` in flight.

### Fixed: a coordinator notice could cost a writer its patch
- Coordinator notices (a replacement's "runtime values did not carry over") were steered into the writer's running
  turn. Codex 0.163 preempts a running code-mode cell when user input is steered into its turn: the cell yields to the
  model and runs on, and when the turn ended first, native abandoned the patch the cell then made (the "unexplained
  run" of 2.4.3).
- Notices with no wake now go through native `thread/inject_items`, which neither preempts a cell nor starts a turn,
  where an admission probe proved it on that Codex binary. A probe that failed or timed out counts as idle-only: its
  notices are injected only between turns. Where the probe found it absent, a notice waits for an app-server that has
  it; it is never steered or started as a turn.

### Fixed: lead mail and the lead's inbox
- A lead's inbox showed every project's mail when read without a session, and a session's inbox missed mail from
  CLI-started writers. A lead now reads its sessions' mailboxes, mail to its own lead actors, and writers' lead mail
  whose unit shares a checkout with its scope. An unscoped read is answered empty with a notice to restart the session.
- A lead session could not acknowledge mail to its lead actor (`ACK_ACTOR` / `NATIVE_BOUND`), and that mail was
  retried every second. A lead now acknowledges its own mail; lead mail is never delivered natively or retried.

### Fixed: refused lead calls held every drain
- A lead call that another hook denied (the push gate, refuse_cd), that permission refused or that the user rejected
  gets no PostToolUse from Claude Code. Its record held `stop` and every lead's quiet window until its 180 s deadline.
- A `PostToolBatch` hook now settles each such call when its batch ends.
- The plugin's own live-view `tail` is no longer coordinated. It never held a drain (its Post comes 0.1 s after its
  Pre), but it cost a record in every session.

### Fixed: lead edits and ignored files became UNKNOWNs
- A lead's edit of a file git ignores (Smartpay_POC's `.gitignore` is `*`) recorded two UNKNOWNs and warned
  "Writer coordinator is unavailable". A writer's read of ignored test output recorded one, and every rerun of the
  tests added more. A patch creating an ignored file was force-added into the seal, and its claim refused other units.
- A path git ignores (or inside an embedded repository) that neither HEAD nor the index holds is now outside the
  ledger, whoever writes it: no receipt, claim, UNKNOWN, refusal or seal entry. A tracked path stays in the ledger.
- Open UNKNOWNs at such paths are resolved at startup (`outside-ledger`, nothing deleted) and each unit's lead gets one
  notice. A lead's edit of a path whose pre-image was stale is prepared again, so it gets an exact receipt.

### Fixed: a restore could not clear an UNKNOWN
- A path the baseline lacked could not be restored, and every restore still needed a new verified edit. A restore by
  the lead's session kept its claims and counted as that unit's unpublished work, so the writer's next edit was refused
  and its seal blocked.
- A restore to the baseline (what HEAD and the index hold, or absence) resolves its UNKNOWNs and joins no unit's scope.
  A restore keeps no claim, and what follows it depends on whose bytes it restored (the baseline: no one's).
- A restore resolves only the UNKNOWNs and quarantined edits it found before its edit, and holds its claims until that
  resolution commits; it is resolved only while the path still holds the restore's own revision. Another unit's later
  failure at the path stays open.

### Fixed: custody refused processes that had just exited
- A zombie, and every `ps` this user ran (setuid root), read as `PROCESS_UNREADABLE`, quarantining custody on gate
  commands. Exited processes are now gone, zombies are read as zombies, and a setuid program of this user is read
  through `sysctl`.
- A writer's symlink failed its turn-end reconcile, which recorded checkout-wide `*` UNKNOWNs and kept its custody
  held. The reconcile now records one UNKNOWN at the link and releases custody. Any other notification failure that
  names a path records that path's UNKNOWN in the checkout it names, not a checkout-wide one.

### Fixed: startup recovery blocked every lead for its whole length
- Recovery reconciled each writer in turn before serving: 187.2 s with 13 writers in a run, 847 s at the 2.4.3 switch.
  It now makes the ledger consistent, serves, and reconciles writers in parallel in the background. A call for a
  writer still reconciling waits up to 20 s, then is answered `WRITER_RECOVERING` with `retry_after`: the 2.4.4 MCP
  server calls again within its own 90 s, and the CLI prints the refusal (exit 3) for its caller to run again.
- A stopped child app-server froze the daemon: CPython 3.15's threaded child watcher wakes on a stop on macOS and then
  blocks the loop in `waitpid`. A kqueue exit watcher replaces it, verified at every start.
- An app-server stopped by a signal (at two reads 30 s apart), or alive behind a socket that refuses or closes, is
  ended and replaced instead of quarantined; an UNKNOWN is recorded only where custody was held. A silent one is
  reported (`daemon/status` `silent`) and never ended for it: one that is only slow would lose its turn and its
  runtime values.
- A supervisor restarting its worker could start a daemon during an upgrade. It now restarts only under
  `upgrade.lock`: while another process holds it, a supervisor with no lease steps aside (`UPGRADE_HELD`), and one
  holding leases keeps them and restarts its worker once the lock is free (`supervisor-wait.json`), since the dead
  worker's app-servers may still be writing.
- A writer whose app-server died while the daemon wrote to it was never replaced: closing its connection re-raised
  the write error. A plain run whose turn ended while no daemon was attached stayed `running`. Both fixed.
- A stop could leave a writer's app-server running: a transport gap's reconcile (as in 2.4.3) or a deadline cancel
  during the drain started a replacement the stop never ended. Neither starts while a stop drains, and the drain waits
  for a reconcile under way. A writer whose connection drops then is left failed for the next daemon's recovery.

### Known limits
- A message an earlier release's daemon left in a writer's native queue, which native starts while no daemon runs,
  cannot be taken back: its turn runs on that release's hooks. A running 2.4.4 daemon withdraws such a message
  (`thread/queue/delete`, `deleted: true`) before it starts.
- Where a writer's app-server died before its native queue was read and its rollout cannot be read, a queued message
  that neither the replacement's queue nor its history holds costs one more app-server: the replacement, which may be
  starting it, is ended too, and the message is `unproven`.
- Ending a writer's app-server to settle an input holds the maintenance loop while it ends (about 50 s at worst).
- An app-server stopped while the daemon runs is ended 30 to 60 s after the stop (measured 55.5 and 56.2 s). A
  debugger pause longer than one probe period (`SOFTWARE_WORKFLOWS_ANSWER_EVERY_S`, 30 s) counts as a stop.
- A writer whose app-server stays silent is only reported, never ended (answer latency through a context compaction
  is unmeasured): it stays failed, and a cancel of its run stays `cancelling`, until it answers or its process is
  ended by hand, after which a reconcile replaces it.
- `retry_after` is coarse on the first switch from 2.4.3, which recorded no recovery length; later switches use the
  last recovery's measured length.
- A stop sent just as a re-admission begins can be refused `NOT_QUIESCENT {readmissions: 1}`; sending it again passes.
- Startup recovery serves after about 2 s, but 13 writers are reconciled only after about 55 s: each checkout's
  calibration (about 26 s) is the long pole. A call for a writer still reconciling waits up to 20 s, then is refused
  `WRITER_RECOVERING`; the 2.4.4 MCP server calls again, a 2.4.3 one (a session not yet restarted) does not.
- A call made with an idempotency key and refused `WRITER_RECOVERING` gets the same refusal again under that key. The
  MCP server never retries a keyed call.
- A cancel of a writer whose app-server was stopped while disconnected records one failure, then settles.
- A killed app-server's delivery leaves one "native notification gap" UNKNOWN for its transport gap.
- A worker that dies while another process holds `upgrade.lock` is restarted only once that process lets go; its
  supervisor keeps every lease meanwhile. A client waiting for the daemon then is refused `DAEMON_UNRESPONSIVE` after
  its own timeout (30 s), one replacing it `DAEMON_OUTDATED`; the worker restarts about 0.1 s after the release. Only a
  signal to the supervisor, as in a forced switch, ends its leases while the dead worker's app-servers may still write.
- An input or delivery that nothing can start any more is `unproven` unless native's own record shows where it went
  (its client id's record in the rollout or in history, its queue, `thread/queue/delete` `deleted: true`): a rollout,
  and history read from it, can miss a record when native's append fails, and native's log can miss the error. So an
  input that never reached native (resumelost, maintlost, dropstart) also waits for the lead. Only the lead's resume
  or `message/retry` sends it again, which may run it twice.
- The office console has no label for an `unproven` delivery (`message/delivery_unproven`): it reads "could not tell
  whether it reached the turn" or, for a steer, "send it again", and its Send sends a new message rather than
  `message/retry`. Either way the message may already have run.
- A lead edit that no Post or batch settles within 120 s (a slow permission prompt, an interrupt) is quarantined, as
  in 2.4.3: overlapping operations and a handback are refused, and its UNKNOWN needs a restore or a narrow. The
  quarantine does not hold the checkout's compatibility lease, a stop, other leads' quiet windows or a restore while
  the call may still run (review turns 43 to 45, HIGH; as in 2.4.3). Claude Code re-checks a file before it writes it,
  so a late Write that finds the file changed is refused by the host (measured on 2.1.306). Planned for 2.4.5: an
  executing state ended only by the call's Post, its batch, its transcript result or its process's exit.
- A workflow phase 2.4.3 left `not_started` is resumed without its saved input, as in 2.4.3. The live ledger held none
  at the switch, and 2.4.4 records none.
- A stop can still leave a writer's app-server running when a reconcile asked for late in a maintenance pass
  (settling a plain run's cancel) replaces a writer the stop has already ended (review turn 48; as in 2.4.3). Planned
  for 2.4.5: no reconcile starts once a stop has listed the app-servers it ends, and the stop waits for those under
  way.
- A lead's background command: what it writes after its hook's Post is not covered and becomes UNKNOWN at the next
  reconcile. Only the plugin's own live-view `tail` is exempt from coordination; any other command is coordinated.
- Smartpay_Backend keeps its 3 checkout-wide `*` UNKNOWNs until its lead narrows them (one narrow).

## [software-workflows 2.4.3] — 2026-10-21

The six findings 2.4.2 shipped as known limits (review session 01a14eac, turn 34) are fixed, with those of review
turns 35 to 41 and three reports from the Sales_Profiler lead after the 2.4.2 switch. They concern a writer replaced
after recovery, a writer with sub-agents, cancels, and failures along the way. Each has a real run that fails on 2.4.2,
or on the tree before its fix, and passes on 2.4.3 (Measured, below). The exceptions are the order of a lost start's
reads and the rule for a thread that cannot be read: their windows are inside Codex, so they were checked against
Codex 0.163's source, and the runs show only that the cancels they serve still settle.

### Fixed: a replacement app-server took work before its reconcile ended
- A replacement was connected and admitted, and its writer bound to it, before its native history and its unit's
  operations were read back. A hook, a message's delivery or a workflow's submission could start work on it
  meanwhile. A call started then could be judged a crash gap and quarantined as UNKNOWN.
- A replacement now counts as reconciling from the save that makes it findable until its reconcile ends. Calls are
  withdrawn (`LISTENER_RECOVERING`), and deliveries and submissions wait.

### Fixed: two callers could replace the same app-server
- A cancel's reconcile used another lock than a re-admission. While a message or a workflow phase was replacing a
  writer, a cancel could admit a second replacement for the same old app-server. The writer was then bound to
  whichever finished last, and the other stayed connected.
- Every reconcile of a run's writer now takes one lock for the run: recovery, resume, re-admission, cancel, a
  transport gap. Each reconciles the incarnation that is current once it holds the lock.
- A cancel requested while its writer is replaced starts no further workflow phase.
- A cancel requested while a workflow phase's input waited for its leases was undone: the submission saved the
  run as it had read it before, which cleared the cancel, and started the phase's turn (as in 2.4.2). The
  submission now reads the run again after every wait, and starts nothing for a cancelled run, another input, a
  draining daemon or a writer being replaced.
- `run/interrupt` of a workflow whose writer had no connection failed (`KeyError`); it now cancels.

### Fixed: a cancel could be undone by a phase's settlement or by a resume
- A workflow phase's settlement read the run, then waited: for its quiescence check, its checkouts' reconcile and the
  readback of its rollout. It then saved the run as it had read it. A cancel requested during those waits was undone:
  the phase completed, and the next phase's turn started after `run/interrupt` had answered. A resume saved the run
  the same way after its quiescence check. That cleared a cancel requested meanwhile, and the resume's turn started.
- The settlement now reads the run again after its waits. For a run cancelled meanwhile it completes nothing, as for
  one cancelled before it began. A resume is refused (`RUN_CANCELLED`) when a cancel was requested after it began.
  Each cancel counts a new generation of the run, so the order holds when the clock moves.
- A cancel interrupted only the turns it saw when it began. A turn whose `turn/start` was in flight then (a phase's
  submission, a message's delivery), or that a queued message started, ran to its end, patches included. Such a turn
  is now interrupted as soon as it is seen, and the cancel's reconcile interrupts every turn still active again.
  Until the turn ends, none of its effect-capable calls runs (`RUN_CANCELLED`). The cancel reports `cancelled` only
  once no phase's `turn/start` is in flight.
- A message's delivery reads the run again after taking its leases, and starts no turn for a cancelled run. A cancel
  reads the run before saving it, and so does its settlement.

### Fixed: an earlier cancel could act on a resumed run
- A cancel's pass (its queue deletions, interrupts and terminations) ran outside the lock its settlement takes.
  Maintenance could report the run `cancelled` while that pass was still under way. A resume could then clear the
  cancel and start a turn, and the pass's next interrupt reached that turn. A later settlement of the old cancel could
  also save the resumed run `cancelled`.
- Each step of a cancel's pass and of its settlement now first checks that its cancel still stands: no resume has
  cleared it, and no newer cancel has replaced it. A run is not reported `cancelled` while a cancel's pass runs, so a
  resume meanwhile is refused (`RUN_ACTIVE`).

### Fixed: `run/interrupt` of a plain run interrupted only its current turn
- A plain run's `run/interrupt` sent one turn interrupt to its root thread. It cancelled no queued work, no
  sub-agent's turn and no start in flight, and nothing refused the next turn's calls. With no turn active it failed
  (`SCHEMA_TYPE`).
- It is now the same cancel as a workflow's: the unit's queued work, turns and terminals, and its calls are refused
  (`RUN_CANCELLED`) until a resume. A lead-mode run has no writer turns, and is refused (`RUN_LEAD_MODE`). The end of a
  turn no longer overwrites a cancelling or cancelled run's state, and a lead's mail is never cancelled.

### Fixed: a cancel could report `cancelled` with a turn's start unresolved
- When the result of a phase's `turn/start` was lost, the phase stayed `uncertain`, and a cancel reported `cancelled`
  once it saw no turn. That held even when it could not read native history to tell whether the turn had started.
- The run now stays `cancelling` until two reads of the thread sent after the loss (`thread/read`), of its status and
  then of its turns, show whether the input entered a turn. Codex runs a thread's requests one at a time in arrival
  order, and saves the live thread before it reads the turns; its history pages (`thread/turns/list`,
  `thread/items/list`) run beside those requests, so they cannot tell. One read cannot tell either, as Codex builds its
  turns before it reads the status. A turn the input entered is interrupted and settled like any other. While the
  thread is active the outcome is read again once the running turn has been interrupted.
- An input that entered no turn while the thread was idle is marked `not_started` only when a second pair of reads, at
  least 2 s later, agrees: `turn/start` answers once Codex's core holds the turn, but the thread reads active only once
  that turn's own task reports it started. A resume continues after a `not_started` input.
- A cancel reports `cancelled` only once Codex reports each of the unit's threads idle. A thread it cannot read counts
  as running unless Codex does not hold it loaded (`thread/loaded/list`), and an app-server still running without a
  connection counts as running. A turn whose interrupt was answered is not interrupted again: a second interrupt of an
  aborted turn went unanswered for 60 s.

### Fixed: an old deadline could cancel a resumed workflow
- Maintenance read its list of runs, then awaited the runs ahead of each one. A workflow that finished and was resumed
  with a new deadline meanwhile was still judged by the record it had read: past its old deadline, it was cancelled,
  and its resumed turn interrupted.
- The deadline cancel now checks the deadline again on the run as it is when the cancel begins.

### Fixed: a resume could be left `transition_pending` for good
- A resume reconciles first, then prepares its input and submits it. When the writer was still waiting to be
  replaced, the submission returned, and only workflow runs were submitted again: a plain run stayed
  `transition_pending`, with no turn, and a second resume was refused `RUN_ACTIVE`.
- A writer was kept waiting because another unit's call was being admitted in the same checkout: the quiet test
  counted admissions by checkout. It now counts the writer's own unit.
- Maintenance now submits again every plain run's prepared continuation, as it does a workflow's.

### Fixed: a sub-agent's own sub-agents were not reloaded on a replacement
- Sub-agent threads were resumed in record order, and Codex refuses a sub-agent before its parent ("resume the
  parent first"). A tree deeper than one level could fail its replacement: quarantined, with an UNKNOWN, and its
  resume refused. They are now resumed parent first.

### Fixed: a failed re-admission was never retried
- A reconcile that failed after recovery left its writer reconciling. Messages, submissions and calls waited for it,
  and nothing reconciled it again until a resume.
- It is now marked failed (`listener/reconcile_failed`) and reconciled again from maintenance: first after 5 s, then
  doubling to 5 min. Its unit's work keeps waiting meanwhile.
- A replacement stays initializing until a pass over it ends. A retry after a pass that failed part-way resumes its
  thread and each sub-agent thread again and passes its writer gate, and it sends the lead the notice the failed
  pass owed.
- A writer still bound to a dead app-server when a daemon starts (it stopped before the replacement bound) is
  reconciled by the same retry once the daemon serves, when work is owed to it.
- A finished run's writer is retried only while work is owed to it. That means its unit is not quiet (a turn,
  custody, an admission, a live operation, a workflow input or a native delivery in flight), or a message to one of
  its writers is pending with a wake. A lead's mail never counts, including mail to an actor of kind lead. Otherwise
  its next resume reconciles the writer.
- At startup, a checkout whose lease is refused (`EXCLUSIVE_WRITER`) fails only its own writers' recovery: they are
  quarantined, with an UNKNOWN. This happens when a writer outside the coordinator took the checkout while no daemon
  ran. The daemon starts, and every other checkout recovers. A record left by an app-server start that never got a
  thread is retired, and needs no lease.
- No re-admission or retry starts while the daemon drains, and a drain waits for those under way.

### Fixed: a failed app-server start left its process running
- `spawn_listener` recorded its app-server before waiting for its socket. When the socket never appeared, it raised
  `LISTENER_START` and left the process running, with a `starting` record. A run's start stops every app-server of
  its run when it fails, but a re-admission or a recovery did not.
- A start that fails now ends its app-server and what it started (its process group) and marks the record stopped.
  If its endpoint cannot be removed, that is recorded (`listener/cleanup_refused`), and the start's own error is the
  one raised.
- A failed admission's app-servers, and one whose attach fails, are killed if they do not drain: none was given work.

### Fixed: a writer's patch right after its own command was refused
- A command's custody covers its whole checkout, the unit's own claims included, until the coordinator settles it:
  its call has returned and its processes are gone. Maintenance settles it about once a second. A patch the same
  unit made meanwhile was refused `CLAIM_CONFLICT` naming its own unit (reported by the Sales_Profiler lead after the
  2.4.2 switch). A project unit's patch already settled its writer's commands first. A checkout unit's patch now
  does too, when its unit holds custody.
- Codex can report a command's end after the next call's hook: its result in the log, its Post hook, its completed
  item. A command with no process or terminal left holds its custody only until that end is read. The patch's hook
  now waits for it, until the hook's deadline, before it claims paths. A command still running conflicts
  (`CLAIM_CONFLICT`), as before.

### Fixed: Codex's clock tools were refused
- Codex 0.163 puts `sleep` and `curr_time` in its `clock` tool namespace, and the hook names a namespaced tool by its
  namespace and name: `clocksleep`, `clockcurr_time`. Only a bare `sleep` was in the effect-free set, so a writer's
  wait was refused `HOOK_TOOL_UNSUPPORTED` (reported by the Sales_Profiler lead after the 2.4.2 switch). Both are
  allowed now. Neither takes custody: they read or wait on time.

### Changed: the re-admission and replacement notices say what is lost
- A replacement keeps the thread's history only. Values the old app-server held in its tool runtime (code-mode
  variables and functions) are gone. Both notices now say so (reported by the Sales_Profiler lead after the 2.4.2
  switch). Writers should keep drafts in files before an upgrade.

### Measured (2.4.2 as calibration, then 2.4.3)
- **How these runs were made.** All used the real Codex 0.163 engine with the loopback fixture
  model, on a 3,700-file checkout. A forced switch from the 2.4.2 tag sent SIGTERM to the supervisor
  and then the worker, as on 10-21, and the writer's app-server kept running. The new daemon was a
  copy of 2.4.2 or of 2.4.3 that adds one delay or fault, inert unless its variable is set.
- **A message to a writer whose replacement is held 15 s after its admission.** 2.4.2 started the
  message's turn at +155.6 s and admitted its patch on the replacement at +155.7 s. The replacement's
  reconcile ended at +171.6 s. 2.4.3 started the turn at +173.0 s, after the reconcile ended at
  +172.3 s. The patch was verified each time, with no UNKNOWN.
- **A cancel 20 s before a workflow's next phase stops its writer's app-server to replace it.**
  - 2.4.2 replaced that app-server twice. Both replacements stayed connected, and the writer was
    bound to the second. `run/interrupt` failed `NATIVE_RPC` ("thread not found"), the run stayed
    `cancelling`, and a stop was refused `NOT_QUIESCENT`.
  - 2.4.3 made one replacement and answered the interrupt once it was bound (51 s). It started no
    phase and cancelled the run.
- **A cancel 25 s into the replacement's admission.** 2.4.2's `run/interrupt` failed `KeyError`, and
  the implementation phase's turn ran after the cancel. 2.4.3 answered and started no phase.
- **A resume while another unit's call in the same checkout is held 45 s in admission.**
  - 2.4.2 started no turn. 150 s later the run was still `transition_pending`, though the other
    admission had ended more than 100 s earlier.
  - 2.4.3 started the turn at the resume. With 2.4.2's checkout-wide count put back, the turn
    started once the other admission ended, submitted again by maintenance. The patch was verified
    each time.
- **A sub-agent of a sub-agent, whose record sorts before its parent's, idle at the switch.** 2.4.2's
  replacement failed `NATIVE_RPC` in recovery. The writer was quarantined with an UNKNOWN ("listener
  recovery incomplete"), and its resume was refused `RECOVERY_UNKNOWN`. 2.4.3 resumed the threads
  parent first, and the resume's patch was verified, with no UNKNOWN.
- **One failed `hooks/list` in a re-admission.** 2.4.2 left the writer reconciling. The message
  stayed pending, and a stop was refused `NOT_QUIESCENT` (`deliveries: 1`). 2.4.3 retried 5 s later
  and re-admitted the writer. The message was delivered, and the patch was verified.
- **A re-admission whose first app-server never creates its socket** (`/bin/sleep` in its place).
  - 2.4.2 left that process running and its record `starting`, and nothing retried the re-admission.
  - 2.4.3 ended the process and marked the record stopped. Its retry re-admitted the writer, and the
    message's patch was verified.
- **A message's wake delivery whose reply is lost after native took it, then a re-admission whose
  first start stalls.** 2.4.2 retried nothing. The message stayed `uncertain`, the stalled start was
  left running, and a stop was refused `NOT_QUIESCENT` (`deliveries: 1`). 2.4.3 retried the writer
  5 s after the failure and found the message in the thread's history (`entered_context`). The stop
  was answered.
- **Mail to the lead attached to a writer's unit, after that writer's re-admission failed.** An
  earlier 2.4.3 candidate re-admitted the writer for that mail. 2.4.3 leaves the writer for its next
  resume, and the stop is answered.
- **A re-admission whose sub-agent's `thread/resume` fails once.** An earlier 2.4.3 candidate's
  retry did not resume the sub-agent, and the next resume failed `NATIVE_RPC` ("thread not found").
  2.4.3's retry resumed it. The resume passed, and the patch was verified.
- **A daemon restart with one writer still bound to a dead app-server, and writers outside the
  coordinator holding two checkouts.** 2.4.2 did not start (`EXCLUSIVE_WRITER`). 2.4.3 started:
  - It quarantined the live writer whose lease was refused, with an UNKNOWN.
  - The third writer recovered.
  - The dead-bound writer of a finished run was left for its next resume.
- **`clock.sleep`, then `clock.curr_time`, in one turn.** 2.4.2 refused both (`HOOK_TOOL_UNSUPPORTED`).
  2.4.3 allowed both, and the run completed.
- **A cancel while a workflow's next phase waits for its leases (held 20 s).** 2.4.2 completed the run,
  with no cancel recorded, and the implementation's turn ran. 2.4.3 cancelled the run, and the
  implementation's input stayed prepared, with no turn.
- **A cancel while a phase's settlement reads its rollout (held 20 s).** 2.4.2 answered the
  interrupt, then completed the analysis. The implementation's turn started, its patch was verified
  19 s after the answer, and the run ended `completed`, with no cancel recorded. 2.4.3 kept the run
  cancelled, with the analysis left settling and the file unchanged.
- **A cancel while a resume's quiescence check waits (held 20 s).** 2.4.2's resume returned after
  the interrupt had answered, and its turn's patch was verified. 2.4.3 refused the resume
  (`RUN_CANCELLED`), kept the run cancelled and left the file unchanged.
- **A cancel while a phase's `turn/start` is held 20 s after its `submitting` save.** 2.4.2 reported
  `cancelled` at once. The turn then started, and its patch was verified 19 s later. 2.4.3
  interrupted the turn when it started, reported `cancelled` after that, and left the file unchanged.
- **The same cancel during a resume, with the wall clock set 1 h back.** An earlier 2.4.3 candidate,
  which compared times, let the resume clear the cancel, and its patch was verified. 2.4.3 refused
  the resume (`RUN_CANCELLED`).
- **A cancel whose pass is held 30 s, with a resume during the hold.** 2.4.2 reported the run
  `cancelled` during the hold. The resume started its turn, and the held pass then interrupted it:
  the run ended `failed`, and the resume's patch was never made. An earlier 2.4.3 candidate did the
  same. 2.4.3 kept the run `cancelling` during the hold and refused the resume (`RUN_ACTIVE`). After
  the cancel ended, a resume ran and its patch was verified.
- **`run/interrupt` of a finished plain run while a message's `turn/start` is held 20 s.** 2.4.2's
  interrupt failed `SCHEMA_TYPE` ("Wrong type at params.turnId": no turn was active), and the
  message's turn then ran and made its patch. 2.4.3 cancelled the run, interrupted the message's
  turn when it started, and left the file unchanged.
- **A cancel when a phase's `turn/start` result was lost, and no history could be read for 25 s.**
  An earlier 2.4.3 candidate reported `cancelled` at once, 36 s before a history read could show
  whether the turn had started. 2.4.3 found the turn in a read ordered after the start, interrupted
  it, and reported `cancelled` 9 s after the fault lifted. When the start had never reached Codex,
  it marked the phase `not_started` at once, and a resume continued. The earlier candidate refused
  that resume (`SUBMISSION_UNCERTAIN`).
- **A lost start that Codex runs between the cancel's history reads and its ordered requests, with
  its notifications dropped.** The tree reviewed at turn 39 marked the input `not_started` and
  reported `cancelled` in the second Codex returned the turn, and interrupted the turn only 2 s
  later. 2.4.3 recorded the running turn, interrupted it, and reported `cancelled` once the thread
  was idle; the phase kept its turn.
- **A workflow resumed with a new deadline while maintenance waits on another run, past its old
  deadline.** The tree reviewed at turn 39 cancelled it when maintenance went on, and its resumed
  turn was interrupted. 2.4.3 left it running, and its patch was verified.
- **A writer's patch right after its own command, in one cell.** 2.4.2 refused the patch
  (`CLAIM_CONFLICT`, naming the writer's own unit). The command's custody was released 0.2 s
  later, and the file was unchanged. 2.4.3 admitted and verified the patch. It did the same when
  each command's completed item was processed 5 s late. On 2.4.2 that case was refused the same way,
  and custody was released 5.1 s later.
- **2.4.2's own runs, 2.4.2 to 2.4.3**: a writer idle at the switch, a sub-agent, a switch during a
  90 s command followed by a resume or by a message, an app-server stopped with SIGSTOP, and unread
  lead mail. Each gave 2.4.2's result. The
  app-server stopped with SIGSTOP is still quarantined with an UNKNOWN, a known limit.

### Known limits
- An app-server that does not answer the new daemon cannot be read, so it is not replaced: its recovery fails with an
  UNKNOWN, as in 2.4.2.
- A message the earlier release had already put in a writer's native queue starts its turn there without the
  coordinator, so it runs on the earlier release's hooks. The writer is replaced before its next turn after that.
- `run/status` reports a run's last state while a turn started by a message runs (planned for 2.4.4).
- Outside a cancel, a phase whose `turn/start` result was lost still refuses a resume (`SUBMISSION_UNCERTAIN`) until
  native history shows its input, as in 2.4.2.
- In a cancel, a lost start's `not_started` rests on Codex reporting a started turn within 2 s of `turn/start`'s
  answer, which neither Codex nor its runtime guarantees (review turn 41, MEDIUM). If the turn's task does not run for
  that long, the run can read `cancelled` while that turn can still start. Such a turn is interrupted when it is seen,
  and its calls are refused (`RUN_CANCELLED`) until a resume; a resume before it starts can join it. Planned for 2.4.4:
  end the app-server (verified), then read what it saved.
- Mail to a lead-mode run's actor still cannot be acknowledged (`ACK_ACTOR` / `NATIVE_BOUND`), as in 2.4.2. It blocks
  nothing.
- A replaced writer is first reconciled as it is, then replaced, so recovery takes about 40 s for each writer it
  replaces, one after another (515 s for 13 writers at the 2.4.2 switch).

## [software-workflows 2.4.2] — 2026-10-21

### Fixed: after an upgrade, writers admitted by the earlier release were refused every call
- A writer's app-server keeps the hook command it was admitted with: the path of that release's
  `hooks.py`, and the trust Codex holds for it. The daemon refuses every hook from a writer whose
  hook program is not its own (`HOOK_PROGRAM_CHANGED`).
  - On 10-21 a 2.4.0 daemon was replaced by force, and 2.4.1 adopted its 13 live app-servers. Every
    tool call they made was refused.
  - A draining stop stops every app-server, and 2.4.1 started each again from its stored config. So
    it ran the earlier release's `hooks.py` while the daemon recorded its own.
- The coordinator now re-admits such a writer when nothing of its unit is in flight. It checks for
  turns, custody, admissions, operations, workflow inputs and deliveries.
  - This happens at the end of its reconcile, once its native turns are read, and while no hook,
    delivery or submission can start work.
  - Its app-server is stopped. A new one is admitted with this release's hooks and resumes the same
    thread and each of its sub-agent threads.
- A writer that is busy at that moment is replaced before its next turn: a resume, a message that
  wakes it, or a workflow's next phase. The message or the phase waits until then.
- An app-server that has not exited 45 s after its SIGTERM is killed, since it has nothing in flight.
  - The bound is Codex 0.163's own (10 s for its threads' shutdown, 30 s for a connection's RPC
    drain), plus 5 s.
  - Each stop records how long the app-server took to exit (`listener/exit`).
- A call that was being admitted when its app-server began to be reconciled or replaced is
  withdrawn (`LISTENER_RECOVERING`) instead of allowed.
- A failed admission no longer leaves its app-servers running.
- A writer whose recovery failed could not be resumed: `run/resume` looked up its connection first
  and failed `OPERATIONAL_FAILURE` (`KeyError`), as in 2.4.1. A resume now reconciles it, as for any
  writer. An interrupt of such a run is refused `LISTENER_RECOVERING`.

### Fixed: a writer's sub-agents were not reloaded after its app-server was replaced
- A replacement resumed only the root thread. Its first resume or seal then failed `NATIVE_RPC`
  ("thread not found") on a sub-agent's thread, as in 2.4.1 after an app-server died. Each sub-agent
  thread is now resumed on the replacement too.

### Fixed: unread mail to a lead blocked every stop and upgrade
- Writers can address a lead-mode run's actor. Codex never delivers that mail as a turn: the lead
  reads it through its inbox.
- 2.4.1 counted each unread message of that kind that had a wake as a delivery in flight, so:
  - a draining stop was refused `NOT_QUIESCENT` (`deliveries`) for as long as one stayed unread;
  - the checkout's compatibility lease was never released.
- On 10-21, 23 such messages blocked the upgrade to 2.4.1, which then had to be forced. Mail to a
  lead no longer counts.

### Corrected: 2.4.1's notes on adopted app-servers
- 2.4.1 said that app-servers adopted from a 2.4.0 daemon that died "hold what they held in 2.4.0
  until those app-servers are replaced". In fact every call they made was refused
  (`HOOK_PROGRAM_CHANGED`), and nothing replaced them. 2.4.2 replaces them.

### Measured (2.4.1 as calibration, then 2.4.2)
- **How these runs were made.** All used the real Codex 0.163 engine with the loopback fixture
  model, on a 3,700-file checkout. A forced switch sent SIGTERM to the supervisor and then the
  worker, as on 10-21, and the writer's app-server kept running.
- **A forced switch from 2.4.0, then a resume for a patch.** 2.4.1 refused the patch
  (`HOOK_PROGRAM_CHANGED`), and the file was unchanged. 2.4.2 replaced the app-server, which exited
  0.05 s after its SIGTERM. The patch was verified on the same thread, with no UNKNOWN. A switch from
  2.4.1 gave the same result.
- **An MCP server that ignores SIGTERM and takes 60 s to exit.** The app-server still exited in
  0.05 s.
- **A sub-agent whose turn had ended.** The first 2.4.2 candidate's resume failed "thread not found"
  on the sub-agent's thread. 2.4.2 resumed it and verified the patch.
- **A switch while a 90 s command still holds custody.** 2.4.2 kept the app-server at recovery. It
  replaced it once the custody was released and the lead resumed the run, or sent the writer a
  message instead. The patch was verified each time, with no UNKNOWN.
  - Without the wait for a message, the message's turn ran on the old hooks and its patch was
    refused.
  - Without the in-flight check at recovery, a switch during a running command left a
    checkout-wide UNKNOWN, and the run stayed running.
- **An app-server that could not act on its SIGTERM** (stopped with SIGSTOP as it was stopped).
  2.4.2 killed it after 45.0 s and replaced it, with no UNKNOWN.
- **A re-admission that fails at its first app-server.** The first candidate left that app-server
  running. 2.4.2 leaves none.
- **An unread message to a lead-mode run's actor, then a native run on the same checkout.** 2.4.1 kept
  the lease and refused the stop (`deliveries: 1`). 2.4.2 released the lease and stopped.
- **The live ledger, read-only.** Each of the 13 adopted writers reads as admitted by another
  release, and its replacement config carries this release's hooks in both events. The ledger showed
  no sub-agent, turn, custody, live operation or in-flight delivery for them.

### Known limits
- A replaced writer is first reconciled as it is, then replaced, so recovery takes about 1 min for
  each writer it replaces on the fixture (two admissions), one after another.
- An app-server that does not answer the new daemon cannot be read, so it is not replaced. Its
  recovery fails with an UNKNOWN, as in 2.4.1.
- A message that the earlier release had already put in a writer's native queue starts its turn
  there without the coordinator, so it runs on the earlier release's hooks. The writer is replaced
  before its next turn after that.
- Mail to a lead-mode run's actor still cannot be acknowledged (`ACK_ACTOR` / `NATIVE_BOUND`), as in
  2.4.1. It no longer blocks anything.
- Open from review turn 34 (session 01a14eac), released on the user's decision, to be fixed in 2.4.3.
  Every writer an upgrade finds idle is replaced in recovery, before the daemon serves. The findings
  concern a replacement made later, or a writer with sub-agents.
  - A replacement made after recovery is admitted before its reconcile has finished, so a hook or a
    message could start work on it meanwhile. A patch started then could be quarantined as a crash
    gap.
  - Two callers can replace the same writer at once: a cancel's reconcile and a message's or
    workflow's re-admission.
  - A resume of a plain native run can be left `transition_pending`, with no turn started, if its
    writer is still waiting to be replaced. That happens when another unit's call is being admitted
    in the same checkout.
  - Sub-agent threads are resumed in record order, not parent first, so a sub-agent of a sub-agent
    can be refused. 2.4.1 does the same when an app-server dies.
  - A re-admission that fails after recovery leaves its writer reconciling, and nothing retries
    it. A resume then admits it again.
  - An app-server whose socket never appears is left running when its start times out, as in
    2.4.1.

## [software-workflows 2.4.1] — 2026-10-20

### Fixed: a busy coordinator let native calls run unadmitted (reported from a seven-writer session)
- **A native call is decided before Codex stops waiting for its hook.** Codex 0.163 ends a hook at
  120 s and then runs the call anyway. The hook client counted its 115 s from its own request, so a
  Pre queued behind others outlived Codex's limit. The command then ran without admission, and the
  coordinator recorded an UNMEDIATED `*` UNKNOWN on the checkout, which stops every writer there until
  a restore. Now:
  - the client counts from its own start and answers 8 s before Codex's limit. That is one deadline
    across connecting, sending and every receive: a socket timeout bounds only one operation;
  - the coordinator refuses with `COORDINATOR_BUSY` ("nothing ran; retry it") once a lock or custody
    wait would pass 4 s before that;
  - a call it admitted too late is withdrawn.
- **A call that native refuses, or that is withdrawn, holds nothing.** When native reports a Pre hook
  `blocked`, ours or another, the call's admission is withdrawn and its custody given back. That
  includes the parts of a patch across a project's repositories. In 2.4.0 an admission completed
  after the client gave up stayed allowed and held exclusive custody. Later commands waited on it,
  and the daemon refused to stop.
- **A call that Codex refuses after admitting it holds nothing.** Codex 0.163 can refuse a call
  inside its handler, after an allowed Pre hook. Examples are `tty` on the TTY-disabled runtime the
  coordinator configures, a patch whose context is not in the file, or an unusable `workdir`. It
  runs no Post hook for such a call, and for the first two it sends no item either. Writers on
  gpt-6-astra make every call from inside a code-mode cell, because the model catalog sets
  `code_mode_only` (all 778 calls in the reporting session's ledger). A cell's calls are not joined
  by the end of their turn.
  - In 2.4.0 such a command kept exclusive custody for the rest of the run. Every later command in
    the checkout waited out its deadline and was refused, and the daemon refused to stop
    (`NOT_QUIESCENT`).
  - Such a patch kept its paths until turn end and was then quarantined. Every retry in that turn
    was refused (`OPERATION_BUSY`), and the turn left an UNKNOWN that only a restore with every
    writer idle cleared.

  Now:
  - **The writer's log is a file.** A writer's Codex app-server writes its log to a private file
    that it holds open itself (`listeners/<id>/native.log` in the coordinator's state), with
    Codex's trace-safe target on.
  - **Codex's own result line is the evidence.** Once a call's handler has returned, Codex 0.163
    writes `codex.tool_result` there, for direct and nested calls alike, with `success=false` for a
    failure. That target carries identifiers, names and lengths, never arguments or output.
  - **The daemon reads the file from a durable offset.** It reads at each Pre hook, at turn end,
    every 50 ms, and in recovery, so a result written while the daemon restarts is still read.
    Codex writes a call's result before it hands the failure to the model or the cell, so the retry
    of a refused call finds that call settled.
  - **A failed result proves the call ended, not that it changed nothing.**
    - A patch is settled as having no effect when every endpoint still holds its pre-image, and its
      paths are released. Any other bytes quarantine it. A `failed` or `declined` item settles a
      patch the same way.
    - A command's checkouts are reconciled, as after any command, once no process or terminal of it
      remains. Its custody is released in the same transaction that records it reconciled.
  - **Results never cross app-server incarnations.** A result settles only the call that its own
    listener incarnation admitted, by thread and call. The results a dead app-server wrote settle
    its calls before its replacement starts.
  - **Late items never reopen a settled call.** Items that arrive after a failed result complete
    the call's record; they are not unmediated. A patch settled as having no effect that Codex then
    reports applied is quarantined. A refused or withdrawn call that native runs after all is still
    recorded as unmediated, and its custody is held again.
  - **A call Codex gives up on is settled by what it can still do.** Codex 0.163 writes
    `codex.tool_result_ready` once a call's dispatch has ended. For a call it aborts, it writes that
    line and no result: an interrupted turn, or a cell that completes with the call still pending.
    - A call aborted while its Pre hook waits never runs. Its admission is withdrawn
      (`NATIVE_ABANDONED`) and its custody given back. The hook reads the log once more before it
      allows. A hook still queued behind others when Codex gives up has no record yet. The end of
      its dispatch is kept for Codex's 120 s hook limit, and the hook refuses the call when its
      turn comes.
    - An aborted command is settled as any command, whether Codex dropped it before or while its
      handler ran. Its checkouts are reconciled once no process or terminal of it remains, and its
      custody is released. Codex starts a local command's process before its handler can be
      dropped, so the coordinator sees any process the command started.
    - An aborted patch can still finish a write it began, since 0.163 runs local writes on their own
      thread. It is quarantined, and its paths stay held until Codex reports that its handler ended
      (its result, its completed item or its Post hook) or its app-server exits. Until then, no
      command is admitted over those paths, a restore of them is refused (`RESTORE_EXECUTING`), and
      none of your Bash commands counts as quiet. The unit is not published either, and the
      checkout's compatibility lease stays held, so a `codex exec` writer outside the coordinator
      cannot start over it. 2.4.0 released that lease once the run ended, live patches or not. A
      call still in its Pre hook when its run ends takes the lease back before it is admitted, and
      is refused (`EXCLUSIVE_WRITER`) if such a writer took the checkout meanwhile.
    - A patch whose handler finished, but whose Post hook outcome had not arrived when its dispatch
      ended, waits for it, also after its turn. Codex sent that outcome first, but over the app-server
      connection, which the log is not ordered against. When it arrives, the patch is verified or
      quarantined. With no Post hook of the coordinator's run for it, it is quarantined at once.
  - **Turn end keeps that barrier, for its own calls.** Codex can start a thread's next turn before
    the coordinator reads the end of the last one, so a turn's end judges only that turn's calls, as
    in 2.4.0. A patch whose handler may still run is quarantined if the turn was interrupted or
    failed, and its paths stay held as above. After a completed turn it stays admitted, because a
    cell can outlive its turn, and its own result settles it.
    - A direct call's patch whose handler ended without a complete lifecycle is quarantined, as in
      2.4.0. A turn ends only after its direct calls, and Codex sends a thread's notifications in
      order, so nothing more comes for it.
    - A cell's patch waits for its outcome or its app-server's exit, since its Post hook can end
      after its turn. That holds after an interrupted turn too: Codex reports every abort reason as
      `interrupted`, and only an interrupt with `code_mode_interrupt` on terminates the turn's cells.
      The coordinator tells direct and cell calls apart from the line Codex writes on receiving each
      call.
    - A stop, or an app-server's death, ends execution for every call of that app-server, and
      quarantines a patch the app-server never reported ending. A stop does not wait for a patch whose
      handler and dispatch have both ended, nor for an Abraham workflow whose settlement waits only on
      such patches, since stopping their app-server settles them. A drain waits for the workflow
      advances under way, and each stops at its next step: no phase completes and no turn starts
      while the daemon drains.
  - **Recovery reads the log before it quarantines.** On restart and on reconnect, the coordinator
    applies each writer's log before it quarantines interrupted cross-repository patches. A refusal
    written or recorded before a crash is settled at its pre-images in every repository it spans,
    and the corrected retry is admitted. A result now applies to its call in any state, so a
    quarantine never discards it.
  - **Admission checks both lines on the installed binary.** A new probe requires, under the
    writer's log filter, a failed result for each of two refused nested calls and one refused direct
    call, each followed by the end of its dispatch. Under the filter 2.4.0 used, it requires neither.
  - The log is deleted when its listener stops.
- One UNMEDIATED UNKNOWN per native item, not one per notification.

### Fixed: the coordinator's cost grew with its history
- **Hot paths no longer decode finished records.** In 2.4.0 every command's record copied every head
  of the checkout (365 KB on a 3,679-file checkout), and its custody record kept a table of the
  user's processes (about 80 KB). Admission, reconcile, `daemon/status` and every native notification
  decoded all of them again, on the event loop. On the reporting session's ledger (730 MB),
  `daemon/status` took 3.3 s, a reconcile's scan 2.9 s and a patch admission's custody check 1.4 s.
  Every lead tool call asks for status at least four times, and one `coordinator_inbox` acknowledgement
  took 996 s. Now:
  - A command records a fingerprint of the heads (count and SHA-256) instead of a copy. Publication
    never read the copy.
  - Released custody drops its process table.
  - Finding an operation by native call, the checkout's live operations, held custody, and custody
    released since a time all go through indexes.
  - A readiness check asks for a brief `daemon/status`, without the writers listing. An older daemon
    ignores the parameter.
  - Records from before 2.4.1 are compacted in the background after start. There is no schema
    change: 2.4.0 can still open the ledger.
  - Maintenance, each second, selects only held custody, runs still in flight and pending lead
    hooks, and message retry only undelivered messages, instead of decoding every record.
- **The office's run view no longer reads the whole ledger on each poll.** `run/events` found a
  run's records by decoding every actor, operation, custody, claim and message record. It now reads
  their ids through indexes.

### Fixed: another user's process marked a checkout UNKNOWN, and nothing practical cleared it (reported from SmartPay)
- **A process id that another user's process now holds no longer fails the coordinator.** The
  coordinator checks again every process id it recorded for a writer: hook processes, services, a
  command's survivors. When one of them exits and a root process takes its id, macOS refuses to
  describe that process, and 2.4.0 let the refusal escape as a `PermissionError`. On the reporting
  Mac, two background tasks failed at 14:33, then the settlement of a writer's `git status`. That
  recorded a checkout-wide UNKNOWN, which stopped every seal, publish, resume and new writer run in
  Smartpay_Backend.
  - Now such a process id counts as gone on macOS, since none of a writer's processes runs as
    another user: macOS still gives every process's user ids. On Linux with `/proc` mounted
    `hidepid=1`, which shows only the effective user, an unreadable process keeps custody held.
  - A process of yours that cannot be read keeps its custody held (`PROCESS_UNREADABLE`), as before.
  - Measured over every live process on the Mac: 2.4.0's lookup raised for 225 of the 226 processes
    of other users; now none raises, and the result for each of your 675 processes is unchanged.
- **A failed process scan holds custody instead of marking the checkout.** A refused fork or a `ps`
  that hangs while the coordinator lists processes now keeps the command's custody held until a
  later scan succeeds (`PROCESS_SCAN`), as a failing `ps` already did.
- **A checkout-wide UNKNOWN can be cleared without touching the checkout.**
  `coordinator_unit(action="narrow", unknown=<its id>)` reads the whole checkout again. Each file
  whose bytes differ from what the coordinator knows becomes its own UNKNOWN, and every checkout-wide
  UNKNOWN recorded before the read is resolved.
  - Nothing is written, and writers keep working. It is refused while a command holds custody in
    the checkout; call it again once that command is reconciled.
  - Seal and publish read their paths again before they freeze them, so a change made after the
    read is still caught.
  - In 2.4.0 the only way was a restore naming a known revision for every path of the checkout
    (about 1,300 in Smartpay_Backend), with every writer idle, and then one exact edit covering
    all of them.

### Fixed: a start right after a stop failed
- `daemon stop` returned when the daemon answered, before its processes had released their locks.
  A call made at once found no daemon and started one. That daemon lost the lock and exited, so the
  call failed `DAEMON_START` after 30 s. 2.4.0 does the same (2 of 2 runs). The client now waits
  while a daemon holds its locks without answering: 4 of 4 runs started in 0.56 s.
- A daemon that keeps its locks without answering for the whole wait, for example one paused while
  exiting, is refused `DAEMON_UNRESPONSIVE`, naming the locks. The client no longer starts a daemon
  that loses them. `abraham` falls back to `codex exec`, as for any coordinator that cannot start.

### Measured (2.4.0 as calibration, then 2.4.1)
All runs used the real Codex 0.163 engine with the loopback fixture model, on a 3,700-file checkout.
The ledger timings used a SQLite backup copy of the reporting session's live ledger, opened
read-only.
- **Twelve parallel commands in one response**, with command admission slowed to 11 s each:
  - 2.4.0: nine ran. Three hooks were refused at 115 s, reporting the coordinator "unavailable".
    One allow then came after the client had given up and kept exclusive custody for a command that
    never ran, so the daemon refused to stop.
  - 2.4.1: seven ran. Four were refused `COORDINATOR_BUSY` at 108 s and one `CUSTODY_WAIT` at
    112 s, all before Codex's 120 s. No custody held, no UNKNOWN, a clean stop.
- **A patch whose context is not in the file:**
  - Then the turn ends: 2.4.0 quarantines it, leaving an UNKNOWN on the unchanged file. 2.4.1
    settles it as no effect, with no UNKNOWN.
  - Then a corrected patch and a `cat` in the same turn:
    - 2.4.0 refuses the patch ("An overlapping operation is unreconciled"). The `cat` waits out its
      custody deadline and is refused. The turn leaves an UNKNOWN. The run took 161 s.
    - 2.4.1 settles the failed patch at the corrected patch's hook. The corrected patch is verified
      with an exact receipt and the `cat` prints the new value, with no UNKNOWN. The run took 38 s.
- **A correct patch of a read-only file** (Codex reports "Failed to write file"):
  - 2.4.0 quarantines it with an UNKNOWN on the file, plus a checkout-wide `*` UNKNOWN when the error
    escaped notification processing.
  - 2.4.1 settles it as no effect, with no UNKNOWN.
- **The live ledger** (730 MB, 11 runs, 890 held claims):

  | Call | 2.4.0 | 2.4.1 |
  |---|---|---|
  | Readiness `daemon/status` | 3.3 s | under 1 ms |
  | Full `daemon/status` | 3.3 s | 0.24 s |
  | Drain readiness | 4.2 s | 12 ms |
  | A reconcile's scans | 2.9 s | under 1 ms |
  | A patch admission's custody check | 1.4 s | under 1 ms (3 ms with released custody) |
  | `run/events` record lookup (same 857 ids) | 5.1 s | 2 ms |

  The compaction took 9.3 s. It covered 712 command records and 676 custody records, and custody went
  from 49 MB to 1.3 MB.
- **A call interrupted while its Pre hook runs** (a 20 s command, then a `cat` whose hook is still
  deciding when the turn is interrupted):
  - 2.4.0 allowed the `cat` after the interrupt. It held exclusive custody for a call that never
    ran, and the daemon refused to stop.
  - 2.4.1 withdraws it (`NATIVE_ABANDONED`), also when twelve hooks are still queued behind each
    other (twelve parallel commands, direct or in one cell). Nothing is held, and the stop is clean.
- **An interrupted patch of a 300 MB file:** 2.4.0 quarantined it at turn end and let a restore of
  the file go ahead while the patch could still be writing. Once the run was cancelled, a legacy
  `codex exec` writer could take the checkout too. 2.4.1 refuses the restore (`RESTORE_EXECUTING`)
  and keeps the checkout's lease until the app-server stops, then restores the file.
- **The daemon killed after a refusal, before settling it:**
  - A refused command: 2.4.0 still held its custody 90 s after the restart, and refused to stop.
    2.4.1's recovery settles it.
  - A project patch refused in Backend and Frontend: 2.4.0 quarantined both parts, left an UNKNOWN
    on each file and refused the corrected retry. 2.4.1 settles it at its pre-images, 0.7 s after
    recovery begins, and verifies the retry.
- **The SmartPay incident**, reproduced by giving each writer record a helper whose process id
  launchd (root) holds. With 2.4.0's process lookup, the next command's settlement failed with
  `PermissionError` twice and recorded two checkout-wide UNKNOWNs, and the command kept custody. With
  2.4.1, the command was reconciled and released, with no UNKNOWN.
- **Narrowing** on the 3,700-file checkout read every path in 0.9 s. It resolved the checkout-wide
  UNKNOWN and found the one file changed outside the coordinator, which became its own UNKNOWN. The
  seal it had blocked then succeeded.
- **A patch whose Pre hook was still deciding when its run ended**, with a legacy `codex exec` writer
  taking the checkout as soon as the run ended: 2.4.0 allowed the patch 6 s later and the file
  changed while that writer held the checkout, and the coordinator verified it as if nothing was
  wrong. 2.4.1 refuses it (`EXCLUSIVE_WRITER`) and the file is unchanged. A later run on the
  checkout then takes the lease and releases it as usual.

### Known limits
- A writer's undeclared shell commands still run one at a time in a checkout. Each holds the
  checkout exclusively, and its reconcile takes about 1 s on 3,700 files. Calls that cannot be
  admitted before their hook's deadline are refused as busy, and Codex shows the writer the reason.
- An aborted patch keeps its paths until its app-server exits, unless Codex reports its handler
  ended. A run interrupt does not end that. Stopping the coordinator does, and the next daemon
  resumes the run's writer. In an Abraham workflow, the quarantined patch also keeps the phase from
  settling, so the stop is refused (`NOT_QUIESCENT`, `workflows`), as in 2.4.0: cancel the run
  (`codex_cancel_run`), wait until it reports cancelled, then stop.
- A cell's patch whose Post hook Codex cancels (an interrupt while it runs, or its cell closing)
  sends no outcome. It keeps its paths the same way, until its app-server exits. A stop does not
  wait for it, nor for a workflow whose settlement waits only on such patches.
- A command whose writer's app-server dies keeps its custody, with a checkout-wide UNKNOWN, until the
  lead reconciles that custody (`coordinator_unit` action `reconcile`), as in 2.4.0. The coordinator
  ends the processes it recorded, but it cannot tell whether one escaped. Narrowing then clears the
  UNKNOWN.
- A command's child that starts its own session (`setsid`) and is an Apple platform binary is not
  held once its command ends, as in 2.4.0: macOS hides such a process's environment, where the
  coordinator's markers are.
- An MCP call that Codex aborts after admitting it keeps its custody until the lead reconciles it,
  as in 2.4.0. The end of its dispatch does not show that the server stopped working on it.
- An aborted command is settled on Codex's macOS spawn path, read in the 0.163 source and run on
  macOS. Other platforms were not run.
- Narrowing a checkout-wide UNKNOWN cannot see a change that was made and undone before its read.
- A process that a writer's command turns into another user's (through `sudo`, say) is not held, as
  in 2.4.0.
- An upgrade stops the 2.4.0 daemon, which stops every writer's app-server, and 2.4.1 starts each
  again with its log. If a 2.4.0 daemon dies without stopping instead, 2.4.1 adopts its live
  app-servers, which log to a pipe 2.4.1 cannot read. Their refused calls hold what they held in
  2.4.0 until those app-servers are replaced. (Corrected in 2.4.2: every call they made was
  refused, and nothing replaced them.)
- The log grows by about 2 KB per call.
- A hook that spans a system sleep is refused when the machine wakes, because its deadline counts
  wall-clock time. Nothing runs, and Codex shows the writer the reason.
- A command's admission is refused (`PROCESS_UNREADABLE`) when one of the user's processes ends while
  the coordinator takes its baseline: `ps` listed it, then the kernel read failed while the process
  still existed. 2.4.0 does the same. This was seen once in 300 commands and once in 13 runs, both
  while other test runs were starting processes. Nothing is held, and the writer retries.

## [software-workflows 2.4.0] — 2026-10-19

### Added: a Codex project is the writer coordinator's unit
- **One writer unit spans a Codex project's repositories.** When the folder `abraham` targets belongs
  to a Codex project (the Codex app's Projects: `project/list` / `project/read` of `codex app-server`,
  Codex 0.163), the coordinator registers every root of the project in one step and starts the writer
  thread under that project (`projectId`, `runtimeWorkspaceRoots`), so it shows there in the Codex
  app and can edit any of its repositories. The coordinator only reads projects; it never creates,
  edits, moves or deletes one.
  - **Selection:** the projects with a root containing the target folder; when several, those that
    also contain the folder of the Claude session that dispatched; still several → refused
    (`PROJECT_AMBIGUOUS`, naming each), and `abraham(project=<id>)` picks one. No project → one
    checkout, exactly as in 2.3.5.
  - **Revisions:** a unit keeps the project membership it started with; a root added or removed in the
    app takes effect for the next dispatch. A registration that cannot finish leaves no usable
    revision and resumes on retry. Refused, naming the roots: a root outside git
    (`PROJECT_ROOT_NOT_GIT`), two roots that are one folder, an outer repository whose git view holds
    a path inside an inner one (`PROJECT_OWNERSHIP`). The SmartPay shape (an outer repository that
    ignores its two nested repositories) registers.
  - **Paths:** a writer's paths may be absolute or relative to its folder, and are routed to the
    repository that owns them, the deepest one; a path in a registered repository outside the unit, or
    in none, is refused, naming it. Claims and conflicts are per repository, also between units of
    different projects that share one. One patch across repositories gets receipts in all of them or
    none; interrupted, it leaves UNKNOWN in each.
  - **Custody:** a writer's command is held in every repository of its unit and reconciled in all.
  - **Publication:** one seal covers every repository that changed (unchanged ones are recorded as
    such), one `code_review(sealed_unit=…)` reviews them together, and `coordinator_publish` preflights
    every repository before it moves any ref, then commits them in order. A unit stopped midway is
    **partly published**: its committed repositories keep their commits, its claims stay held, and
    `coordinator_unit(action="recover")` continues it; if a repository's branch moved meanwhile, it
    stops and asks for a new review (`reopen`, seal, review, publish), never rebasing under the old one.
  - **Lead tools** (`coordinator_*`, `code_review(sealed_unit=…)`) take a `cwd` in any repository of the
    unit, or a subfolder, and `project` when the folder is in several.
  - **Upgrade:** coordinator state migrates in place (schema 3); every 2.3.x unit becomes a
    one-repository unit with its receipts, UNKNOWN and journals unchanged. 2.3.x refuses migrated state
    (`SCHEMA_VERSION`).
  - **A newer release replaces an older coordinator without cutting work off.** `daemon/status`
    reports the plugin version, a `boot` id for the process and the `draining-stop` capability.
    The first `abraham` or lead tool call of a newer release:
    - holds `<state>/upgrade.lock`;
    - looks at the daemon again;
    - stops only that process (`daemon/stop` with `expect`: its pid and boot).

    That daemon refuses at once while anything a stop would cut off is pending: a lead edit, hook or
    command, a publication, a writer's turn, custody, a delivery, any write in progress. Otherwise it
    admits no new write for at most a second while the calls in progress finish, then stops.
    - If the stop is refused, the daemon keeps serving and the call is refused (`DAEMON_OUTDATED`).
      `abraham` then falls back to `codex exec`, as for any coordinator that cannot start.
    - Two sessions upgrading at once replace it once.
    - The lock is held until the new daemon admits work, and the lead hooks wait while it is held.
      So a lead edit or command made during a replacement is recorded by the new daemon.

    A daemon from before 2.4.0 cannot drain, so a newer call never stops it. The call is refused
    with `DAEMON_OUTDATED`, naming a stop command to run once when no session is editing. That
    command reads the old daemon's ledger first and refuses while lead work is in flight there.
    Hooks never replace a daemon. Sessions that still run an older release keep working with the new
    daemon until they are restarted.

### Push gate and Codex Office (2.4.0 consumers)
- **The push gate refuses a push of any repository of a unit that is being published or is partly
  published**, naming which
  repositories have its commit, and no acknowledgement opens it; plain, `-C`, compound and
  acknowledgement-prefixed commands alike, and the git shim refuses the real `git push`. A repository
  nested inside a registered one but not registered itself is not affected. A commit with `git -C`
  is checked against the repository it records into.
- **The Codex Office** shows a writer's project (🗂, its repositories with claims, UNKNOWN and
  publication phase) and ⚠ partly published; a partly published unit of a Claude session is named in
  the status line. It no longer re-reads every coordinator run on every poll.

### Fixed: from the review of 2.3.5 (session 01a14eac, turn 15)
- **A finished coordinated change voids a lead Bash window.** A lead-command RPC, or a writer on the
  older exec backend, that started and ended while a lead Bash call's Pre was waiting left the
  window's fingerprint unchanged, and the call was credited with its file. Every coordinated change
  now advances a durable counter in the same SQLite transaction (operations, custody, writer turns,
  other lead calls), and each registered repository's exec write lock is fingerprinted, in every
  registered repository. Measured on 2.3.5: `lead-command`; now UNKNOWN (also across repositories).
- **A coordinator restart voids every open lead Bash window.** An armed window survived recovery and
  credited a change made after the restart. Windows carry the coordinator generation; recovery
  invalidates them before it admits hooks.
- **The hook reports a lost confirmation as `"uncertain"`**, not `false`, since the coordinator may
  already have armed the window; a repeated confirmation is idempotent.

### Fixed: from the lead's runs of the first 2.4.0 tree
- **`abraham` falls back to `codex exec` again when the coordinator cannot serve it.** With the
  coordinator unable to start, the first tree refused and named a "durable handle" that did not
  exist. With a Codex that has no project API, it refused (`SCHEMA_MISSING`). Now the first falls
  back as 2.3.5 did, and the second treats the folder as one repository. A refusal names a durable
  handle only when the run exists. Measured: the coordinator unable to start falls back (`STATE_PERMISSIONS`); a Codex without the project API selects one repository (`singleton: true`); a target outside git still falls back (`CHECKOUT_ROOT`).
- **Lead tools no longer start Codex to choose a project.** They choose from the project list the
  coordinator kept at its last registration, and register a project only once. On the SmartPay
  shape, a lead tool call took 4,525 ms the first time and about 880 ms after (2.3.5: 460 and 62 ms);
  now 3,494 ms for the first call, which registers the project once, then 65–69 ms.
- **A session whose folder is in two projects works.** Its hooks were refused ("Writer coordinator is
  unavailable"), its edits were UNKNOWN, and another session's Bash window was voided. A hook now
  takes a stored project only when exactly one fits, else the repository alone. Measured: both sessions' Edit calls are exact, their Bash calls `lead-command`, and nothing is UNKNOWN.
- **`checkout/query` no longer reads unit history.** At 1,000 finished units it took 3.75 s, longer
  than the push gate's 250 ms client waits, so the gate reported the coordinator unavailable 7 times
  in 7. Now 0.4, 0.6, 1.2, 3.1 and 3.8 ms at 10, 100, 300, 600 and 1,000 units (2.3.5: 0.4, 0.6, 1.2, 2.1 and 3.4 ms), and the gate's client answered 7 times in 7 at every size.
- **A lead Bash command run from a folder that holds several registered repositories is credited in
  each of them.** What it wrote into the nested repositories was UNKNOWN. Measured: from the outer folder, `echo >> Backend/b.txt`, then `echo >> Frontend/f.txt; echo >> Backend/b.txt`, are `lead-command` in each repository, with nothing UNKNOWN.
- **A deleted or re-created registered repository no longer voids every lead Bash window.** This was
  found in the fix round's own tree. From the moment one registered repository was deleted, or
  re-created at the same path, a lead Bash change in any other repository was UNKNOWN (2.3.5 credited
  it). The quiet check now reads the exec lock of each repository's tree as it is now, and a deleted
  repository holds none. Measured: a lead Bash change in repository A is `lead-command` with B registered, deleted, then re-created.
- **`code_review(sealed_unit=…)` refuses an explicit session.** It started a fresh session whatever
  `session` said. A bound review still always starts fresh, and an explicit thread id is now refused
  before inference.
- Writer paths are relative to the writer's folder, or absolute (2.3.5: relative to the repository
  root). The two differ only when the folder is a subfolder.

### Fixed: from the review of the 2.4.0 tree (session 01a14eac, turn 16)
- **The push gate refuses a push while a unit is being published, not only once it is partly
  published.** A repository's ref moves before the unit's state changes. A crash there leaves the unit
  `prepared` with the commit installed, and a push then got the ordinary token. The gate now refuses
  while any repository of the unit has the commit or a publication step under way, until the unit is
  complete. Measured with the publication paused right after Backend's ref moved (`prepared`, Backend
  `ref_updated` with its commit): before, a token at the hook and in the git shim; now refused at both.
- **The gate checks the repository git will push.** `git --git-dir=… --work-tree=… push`,
  `GIT_DIR=… git push` and git named by path were checked against the folder the command ran in. The
  hook now asks git which repository one plain git command selects, and the shim asks with its exact
  options and environment. Measured from an unregistered repository, pushing a partly published one:
  before, five tokens (three at the hook, two in the shim); now five refusals.
- **Reopening no longer drops new work in an already published repository.** Reopen made every
  repository of the unit writable again, including those that already had its commit. The next seal
  reused their old content, and the unit then reported published with the new edit left out. Now an
  edit or command in such a repository is refused (`PREFIX_PUBLISHED`, naming it and its commit). A lead
  Edit or Write there is denied with that reason, and a lead Bash call runs with a note that what it
  changes there is UNKNOWN. A seal whose published part changed is refused too.
- **`publishing` is recorded before any ref can move,** in every repository of the unit. It is kept
  through a crash and recovery until the unit is complete or partly published.
- **A quarantined operation no longer receives receipts.** A patch across repositories that completed
  while reconciliation quarantined it could record exact receipts over the quarantine. Completion now
  re-reads the operation's and its parts' state inside the receipt transaction. In the writer's forced
  races, every part stayed quarantined, the content stayed UNKNOWN, and no receipt was recorded.
- **A session's inbox includes its writers' messages.** `coordinator_inbox(session_id=…)` without
  `unit` reads every mailbox of the session, and the unread notice counts them all.
- **Listing a project's paths is bounded.** A page reads only its own rows, in SQL. With 100,000
  paths, the pages read 2, 101, 101 and 101 rows, in 0.2 to 0.4 ms each.

### Fixed: from the review of the turn-16 fixes (session 01a14eac, turn 17)
- **The gate finds the repository by its git directory, wherever that lives.** A repository whose git
  directory lives outside its folder (`git init --separate-git-dir`) was not recognized when a push
  reached it with `-C`, `--git-dir` or `GIT_DIR`: the gate took the folder above the git directory to
  be the repository. Now git resolves the pushed repository's common git directory. The coordinator
  matches it against what each registered repository recorded at registration, so a linked worktree
  and a `--git-dir` push without a work tree are matched too. Measured with Backend's git directory
  kept apart, pushing from an unregistered repository: before, 10 tokens (7 new routes, plus 3 that
  name `Backend/.git`, which is now a file); now 10 refusals.
- **An upgrade no longer cuts off work in progress, and it stops only the daemon it looked at.** A
  newer release stopped an older daemon whenever that daemon's own guard allowed it, and that guard
  did not wait for lead edits, lead hooks or publications. A lead edit in flight then became UNKNOWN
  (measured on a real 2.3.5 daemon). The stop was also not tied to the process whose version was
  checked: of two sessions upgrading together, the second could stop the first's new daemon. See
  "A newer release replaces an older coordinator" above.
- **A lead Bash command does not credit a change in an already published repository.** That holds
  from the moment the unit's publication moves the repository's ref, before any reopen. Its changes
  there are UNKNOWN. The Pre names those repositories, and the hook tells the lead before the
  command runs. A lead Edit or Write there is denied. Changes the command makes in the unit's other
  repositories are credited as before. That remainder can still be sealed and published, unless a
  writer of the unit recorded a read of a path that became UNKNOWN (`UNKNOWN_DEPENDENCY`).
- **A denied edit leaves nothing behind.** An Edit or Write refused because its repository is
  already published stayed `received`. It counted as possibly writing for 180 s, and every other
  Bash call's changes were UNKNOWN meanwhile. It is now recorded as refused before the denial
  returns, and a replay is decided again.
- **Every lead tool's unread notice reads the calling session's mailboxes,** including `abraham`, the
  run tools and the reviews.

### Fixed: from the review of the turn-17 fixes (session 01a14eac, turn 18)
- **A repository counts as published as soon as the unit's commit is recorded for it, not only
  after a reopen.** That record precedes the ref update; it stops counting only when the coordinator
  proves the ref was not moved (or a reopen keeps it as published). Before, a lead Bash command in
  such a repository was still credited to the unit: before a reopen, and in the window between the
  ref update and its checkpoint (review turn 19). The reseal after the reopen then refused
  (`PREFIX_PUBLISHED`). Measured, both before a reopen and inside that window: before,
  `lead-command`, a lead Edit there let through as "coordinator unavailable", and the reseal stuck;
  now the change is UNKNOWN, the Edit is denied ("already published", or "being published" inside
  the window), and the remainder publishes after the reopen.
- **An upgrader that arrives while the old daemon is closing waits for the replacement.** It got
  `DAEMON_DRAINING` from its first status call and failed. Measured with the closing step paused for
  3 s: before, refused in 3 ms; now it waits 2.6 s and ends on the same daemon as the first upgrader.
- The coordinator README states when UNKNOWN content blocks publishing the remainder: a writer of
  the unit recorded a read of it.

### Measured
The lead's suite ran on a snapshot of the final tree. The pause points ran in a scratch calibration
copy. Every check used a disposable coordinator state, Codex home and log folder.

| Check | 2.4.0 |
|---|---|
| Turn-15 cases | A quiet lead Bash call is `lead-command`. When a lead RPC overlaps the window (in the same repository or another one), or an exec writer does, the change is UNKNOWN. A stop is refused while the call is in flight; after a coordinator crash its change is UNKNOWN. A lost confirmation reports `"uncertain"`, and the change is then `lead-command`. |
| The 2.3.x checks (r11–r14, snapcheck, edgecheck, lead_bash, lead_bash2, lead_bash3, lead_hooks_edge, settle_check) | Identical to the 2.3.5 records |
| A partly published unit | Every push form of each of its repositories is refused, at the hook and in the git shim, with or without a token. A nested repository that is not registered gets the usual token. The Office names the unit. After `reopen`, seal, review and publish, the token flow pushes to the remote. |
| One folder in two projects | Both sessions: Edit exact, Bash `lead-command`, nothing UNKNOWN |
| Bash from the outer folder | `lead-command` in Backend and in Frontend, nothing UNKNOWN |
| No coordinator / no project API / target outside git | Falls back (`STATE_PERMISSIONS`) / one repository / falls back (`CHECKOUT_ROOT`) |
| A registered repository deleted, then re-created | `lead-command` throughout (the fix round's tree: UNKNOWN) |
| Lead tool call, SmartPay shape | 3,494 ms first (registers the project), then 65–69 ms. 2.3.5: 460, then 62 ms |
| `checkout/query` at 10 / 100 / 300 / 600 / 1,000 units | 0.4 / 0.6 / 1.2 / 3.1 / 3.8 ms, gate client 7 of 7 each. 2.3.5: 0.4 / 0.6 / 1.2 / 2.1 / 3.4 ms |
| A push while a ref has just moved (publication paused there) | `checkout/query` reports `publishing` (Backend `ref_updated` with its commit). The hook and the shim refuse. Before: `prepared`, and a token at both. |
| A push naming another repository (`--git-dir`/`--work-tree`, `GIT_DIR=`, git by path) | Refused at the hook and in the shim, 5 of 5. Before: 5 tokens. |
| Lead tools after reopen | A lead Edit in the published Backend is denied (`PREFIX_PUBLISHED`). `git status` there runs. A lead Edit in Frontend is recorded exactly and published. |
| A push of a repository whose git directory lives apart (`--separate-git-dir`) | Refused at the hook and in the shim, 10 of 10 (`-C`, `--git-dir`, `--work-tree`, `GIT_DIR`, and `.git` named as a file). Before: 10 tokens. The unregistered repository's own pushes get the usual token. |
| A running 2.3.5 daemon, then a 2.4.0 call | Never stopped by the call: `DAEMON_OUTDATED` in 44 ms, naming the stop command. Run as given, that command stops it, and the next call starts 2.4.0 (migrations 1, 2 → 1, 2, 3). With a lead Edit or Bash in flight, the stop is refused (`NOT_QUIESCENT`, naming it). After its Post the stop goes through, and nothing is UNKNOWN. Before: the 2.4.0 call stopped it with the edit in flight, and the edit became UNKNOWN. 2.3.5 hooks work with the 2.4.0 daemon: Edit exact, Bash `lead-command`. A 2.4.0 daemon is used as it is. |
| A draining daemon replaced by a newer release (the tree, and a copy labelled 2.4.1) | Idle: replaced in 587–649 ms (new process and boot). With a lead Edit or Bash in flight: refused in 4 ms, the old daemon keeps serving (same pid and boot), the Post is recorded there, then it is replaced, and nothing is UNKNOWN. Two upgraders at once: one replacement, both on the same daemon, one worker, also when the second arrives while the old daemon is closing. A stale status: refused (`DAEMON_REPLACED`), and the replacement keeps serving. An older client uses a newer daemon as it is. A lead Edit sent while the lock was held across a stop and a start waited 1.5 s and was recorded by the new daemon. |
| Bash in a reopened unit's published repository (the writer's runs) | Its change there is UNKNOWN, the Pre names the repository and its commit, and the Frontend change is `lead-command`. The remainder published. A denied Write's replay is denied again, and a Bash call in a third repository right after it is `lead-command`. |
| Bash in the published repository of a partly published unit, before any reopen | The Pre names Backend as published, and the change is UNKNOWN. A lead Edit there is denied (`PREFIX_PUBLISHED`). After the reopen, the reseal and publish complete. Before: `lead-command`, the Edit let through, and the reseal refused. |
| The same while Backend's ref has moved and its journal still says `commit_created` (publication paused there) | The same outcome, the Edit denied as "being published". Before: the same failure. |

### Known limits
- Quiet means quiet in every registered repository. When two sessions' Bash calls overlap anywhere,
  both calls' changes are UNKNOWN.
- Not run: a project list from a real user's Codex app and where its threads appear there, a real
  model (the writer used a scripted provider throughout), SmartPay's own clones, and other platforms.
- The stop command for a daemon from before 2.4.0 reads that daemon's ledger, then stops it. A lead
  call admitted in the milliseconds between the two is cut off, and its change is UNKNOWN.
- A lead hook waits for a replacement for up to 8 s (Bash) or 11 s (file tools) of its 15 s. If
  the new daemon takes longer to admit work (a large state to recover), calls made meanwhile go
  unrecorded, and their changes are UNKNOWN.
- Seen once in a test, not yet investigated: when the coordinator's socket folder is forced onto a
  path longer than macOS allows (104 bytes), `abraham`'s fallback to `codex exec` hung past its
  runtime limit. The default socket folder is short.

## [software-workflows 2.3.5] — 2026-10-19

### Fixed: from the review of 2.3.4's lead-Bash rule (session 01a14eac, turn 14)
- **A lead Bash command is credited only when the hook had the coordinator's answer before the
  command ran.** The answer to Pre carries a one-time token and the hook confirms it
  (`lead-hook/armed`); only a confirmed window can name the lead. A hook that gave up waiting (3 s)
  let the command run while Pre was still taking its listings, so content the command moved into an
  ignored folder and back, then force-added, was credited as the lead's. It is UNKNOWN now.
- **Quiet from the call's arrival to the end of Pre, not only at its end.** The fingerprint is
  taken twice and also covers every other lead call's record in the checkout, finished ones
  included. Another session's command that wrote an ignored file while this one's Pre waited to
  attach, then ended, left this window quiet, and the force-added file became this lead's.
- **A folder named like pathspec magic (`:(glob)cache`) no longer fails every lead Bash Pre.**
  `git check-ignore` reads its input as pathspecs; names are passed as `./name`.

### Measured (2.3.4 as calibration, then 2.3.5)
- Pre paused 6 s before its listing, the command `mv work saved; sleep 8; mv saved work; git add -f
  work/old.log`: 2.3.4 `lead-command`, 2.3.5 UNKNOWN.
- Session B's Pre paused 2 s before attaching while session A wrote an ignored `gen.txt`; B then
  force-added it: 2.3.4 B's `lead-command`, 2.3.5 UNKNOWN (B's hook answered in 2.2 s).
- `:(glob)cache/old.log` with `*.log` ignored, then `echo made > made.txt`: 2.3.4 the hook reported
  the coordinator unavailable and `made.txt` was UNKNOWN; 2.3.5 `lead-command`.
- A plain quiet command is still `lead-command`, with the hook under Homebrew 3.15 and Apple 3.9.6;
  Pre still answers in 0.2 s. Every 2.3.4 run repeated with the same results.

## [software-workflows 2.3.4] — 2026-10-19

### Fixed: the coordinator refused real repositories
- **A checkout's content is git's view of it**, the same set Codex itself diffs: tracked paths
  (deleted ones too) plus untracked paths no ignore rule covers (`git ls-files --cached --others
  --exclude-standard`). 2.3.3 walked every file on disk, so the first symlink anywhere —
  `node_modules/.bin`, an ignored `.venv` link — refused registration with `PATH_SYMLINK`, after
  copying ignored files into the coordinator state. On SmartPay_Frontend that was 178,768 files
  on disk against the 1,495 git sees; one failed registration left 1.1 GB of copies.
  - Paths the ledger already holds stay in scope after an ignore rule starts covering them.
  - An embedded repository is skipped; its files belong to that checkout.
  - A path in git's view that is not an ordinary file (an untracked symlink) is refused with the
    path named and the remedy: ignore it or remove it.
  - A tracked path whose folder became a file reads as deleted instead of failing the scan.
- **A failed first registration can be retried.** It wrote no revisions and allowed no effects,
  but every later attempt answered `CHECKOUT_QUARANTINED`, so the repository could never be
  registered again with that state.
- **Registration reads the base tree's blobs through one `git cat-file --batch`** instead of one
  git process per file.

### Fixed: from the review of the first 2.3.4 draft (session 01a14eac, turn 11)
- **Old content that becomes visible is not credited to a quiet lead command.** A `git add -f` of
  an ignored file, or an ignore rule removed, shows bytes the coordinator never saw written. As a
  lead Bash call arrives, before anything can wait, the coordinator now lists what exists outside
  git's view: ignored files, ignored folders and embedded repositories once each, and file by file
  a folder git lists only because everything in it is ignored. A path new to the ledger at the
  command's end is the command's only if none of those covers it, compared as the volume tells
  names apart (Unicode, and case on a case-insensitive volume), and UNKNOWN otherwise, even after a
  `touch` or an unchanged rewrite. Files the command creates are still the lead's, also in a folder
  of ignored files. (Turn 12 refuted the first fix, a file-timestamp rule: `touch` moves the
  timestamp without writing the bytes. Turn 13 found the listing taken after Pre's reconcile, where
  a hook that gave up at 3 s lets the command run first; compared by spelling, so a case-only
  rename passed; and too coarse in folders of ignored files, where new files became UNKNOWN.)
- **A scan notices a tracked file deleted or replaced after it was read.** Equal listings miss it
  (the path stays listed), so the scan also compares its folders' identity and times, and is
  taken again when an entry changed.
- **A tracked file that became a folder reads as deleted**, in full scans, scoped reconciliation
  and sealing alike; the folder's files are paths of their own. Each crashed with
  `IsADirectoryError`.
- **Registration reads the base tree's paths too**, so a file removed from the index but kept on
  disk (`git rm --cached` plus an ignore rule) is not recorded as deleted, then as restored.
- **The batch blob reader has a deadline**: no progress for 120 s fails with `GIT_TIMEOUT`, and the
  git child is killed and reaped. A stopped child held registration forever.
- **A registration killed in the middle of its scan is taken again**, like one that failed: never
  ready, no history, nothing admitted. It stayed `CHECKOUT_QUARANTINED`.
- **A submodule in the index is refused with its path**, instead of crashing the scan.

### Changed: the agent-teams skill loads a quarter of the tokens
- `skills/agent-teams/SKILL.md` is a core of 11,472 bytes (was 57,898, about 14k tokens each time
  the skill loads). The detailed workflow, prompt templates, advisory doctrine, Runtime Capability
  Law, writer guide and troubleshooting moved unchanged into `skills/agent-teams/references/`, which
  the core names with when to read each.
- The start checklist no longer says one writer per checkout: writers in one checkout hold
  disjoint claims.

### Measured (2.3.3 as calibration, then 2.3.4), on clones of SmartPay_Frontend (with its real
node_modules, 82,843 files on disk) and Smartpay_Backend (with its `.venv` link)
- 2.3.3: `PATH_SYMLINK` on both; a second attempt `CHECKOUT_QUARANTINED`.
- 2.3.4 on that same stuck state: ready. Frontend 1,494 revisions, registration 1.5 s (13.5 s with
  one git process per file, identical revisions), full reconcile 0.42–0.46 s. Backend 1,289
  revisions, 1.3 s, 0.31–0.33 s. No UNKNOWN.
- Edge repository: an untracked symlink refused with its path; once ignored, the retried baseline
  is ready and records nothing from ignored folders, the embedded repository or the link; a
  ledger path that becomes ignored is still read (unchanged: nothing new; changed: UNKNOWN); a
  tracked folder replaced by a file records the deletion (without the new handling: a
  `NotADirectoryError` crash).
- The three real SmartPay checkouts, read only: 57, 1,291 and 1,495 paths in git's view, listed in
  0.01–0.02 s, all ordinary files.

## [software-workflows 2.3.3] — 2026-10-19

### Fixed: from the review of 2.3.2
- **An edit that was never prepared keeps counting as possibly writing until its Post has
  recorded its bytes.** In 2.3.2 its Post stopped counting it before waiting for the ledger, so
  another session's Bash completion, scanning meanwhile, could be credited with it.
- **A replayed denied edit is denied again.** The same Pre for a path another unit claims answered
  "uncertain" the second time, which the hook does not deny.
- **An edit that loses its claim to a concurrent edit** is recorded as denied, so it no longer keeps
  other lead commands from being quiet for 180 s.
- **A second completion of an unprepared edit** answers `LEAD_PRE_MISSING` again instead of failing.

### Measured (2.3.2 as calibration, then 2.3.3)
- **The race**, 20,000-file schedule (one full reconcile: 4.6–7.7 s): session B's NotebookEdit is
  refused by the adapter, the host writes anyway, and its Post arrives while A's Bash completion is
  scanning. 2.3.2: the file recorded as `lead-command` for A. 2.3.3: UNKNOWN.
- **Through the daemon's own dispatch**: the replayed denied Pre answered `LEAD_HOOK_UNCERTAIN` on
  2.3.2 and `CLAIM_CONFLICT` with its conflicts on 2.3.3; the second Post of an unprepared
  NotebookEdit answered `OPERATIONAL_FAILURE` on 2.3.2 and `LEAD_PRE_MISSING` on 2.3.3.
- An edit losing its claim inside the claim step was not staged; read in the code.
- Rerun on 2.3.3, same results as before: lead Edit verified, lead Bash cases, every gate decision.

## [software-workflows 2.3.2] — 2026-10-19

### Fixed: from the review of 2.3.1
- **A lead Edit or Write counts as possibly writing from the moment the coordinator receives it**,
  until it is prepared (a prepared edit is a pending operation, which reconciliation skips). In
  2.3.1 only Bash calls counted: an Edit whose hook gave up waiting (3 s) could write while another
  session's Bash completion scanned, and that session could be credited with the edit.
  - A denied edit (another unit's claim) counts as nothing. One whose Pre never prepared (an
    uncalibrated adapter, an encoding) counts until its Post, which records what it wrote as
    UNKNOWN, or for 180 s: the daemon's 120 s pre/post gap for prepared edits, plus 60 s.
- **The quiet check after the scan compares the whole fingerprint again**: another lead call, a
  writer's turn, command custody or a pending operation that began during the scan voids the
  attribution.

### Measured
- **Codex's variant**, on the same 20,000-file schedule as 2.3.1 (one full reconcile: 7.6–8.5 s),
  with session B using Write instead of Bash: its hook gives up after 3 s, and the host writes the
  file that A's Bash completion reads late in its scan.
  - 2.3.1, as calibration: the file was recorded as `lead-command` for A, and B's own edit was
    quarantined ("pre-image changed").
  - 2.3.2: the file is UNKNOWN. B's edit is still quarantined, since the host wrote before it was
    prepared, and its Post records the edit as unrecorded.
- Rerun on 2.3.2, same results as before: lead Edit verified, lead Bash alone attributed,
  overlapping and duplicate cases UNKNOWN, an outside edit before `pwd` UNKNOWN; every gate decision
  unchanged, the lead edits of its setup verified.

## [software-workflows 2.3.1] — 2026-10-19

### Fixed: from the review of 2.3.0's last fixes
- **A lead Bash call counts as running from the moment the coordinator receives it**, before it
  waits for anything: attaching a new session, or another session's transition in the same
  checkout. In 2.3.0, a call that waited longer than its hook's 3 s client timeout let its
  command run unregistered, and another lead command could then be credited with what it wrote.
  The quiet check is also made again after the scan, just before anything is recorded.
- **`git commit --fixup`**: the gate keeps git's last value. `--fixup=reword:X --fixup=Y` and
  `--fixup=reword:X --no-fixup` take the index again, and are judged on it.

### Measured
- **The race, as Codex described it**, on a registered checkout of 20,000 files (one full reconcile:
  8.2 s): two established lead sessions; A's Bash completion scanning; B's Bash call arriving 0.3 s
  later, its hook giving up after 3.0 s; B's command writing a file that A's scan reads late.
  - 2.3.0, as calibration: the file was recorded as `lead-command` for **A**'s unit (wrong).
  - 2.3.1: the file is UNKNOWN.
- Rerun on 2.3.1, same results as 2.3.0: lead Bash alone (attributed), overlapping (UNKNOWN),
  duplicate completion (nothing attributed), an outside edit before `pwd` (UNKNOWN); every gate case,
  plus `--fixup=reword:HEAD --fixup=HEAD` and `--fixup=reword:HEAD --no-fixup` refused with another
  unit's file staged.

### Noticed, not changed
- Registering a 30,000-file checkout took over 60 s on this Mac, and one full reconcile of a
  16,000-file checkout about 3.1–3.6 s. A lead hook gives up after 3 s, so in a large registered
  checkout lead hooks report the coordinator as unavailable while it scans (the daemon still
  completes each call). Open for a later release.

## [software-workflows 2.3.0] — 2026-10-19

### Added: the writer coordinator — several Codex writers in one checkout
- **`abraham` runs each writer as a native Codex thread** through `codex app-server` (Codex 0.163),
  under a per-user daemon, the writer coordinator. Its state is in `~/.local/state/software-workflows`
  (`SOFTWARE_WORKFLOWS_STATE`). Several writers can share one checkout, each on its own claims. When
  native admission fails, `abraham` falls back to the `codex exec` backend, which holds its checkout
  alone, and says so at the top of its answer.
- **Claims.** `abraham(claims=[…])` gives a writer its paths. An overlapping claim is refused, naming
  its holder.
- **Attribution ledger.** A writer's `apply_patch` edits are recorded exactly. Its commands are held in
  custody until they, and every process they left behind, have exited. What nobody can explain is
  UNKNOWN, and `coordinator_unit(action="restore")` puts back the last known content.
- **Messages.** Writers and the lead talk through `coordinator_send` and `coordinator_inbox`. A
  message records each stage: persisted, submitted, entered the turn, acknowledged.
- **Publishing.** `coordinator_seal`, then `code_review(sealed_unit=…)`, then `coordinator_publish`,
  which makes the commit.
- **The lead's hooks.** In a registered checkout, Claude's Edit and Write are recorded as the lead's,
  and refused on a path another writer claims. Bash is recorded too (see Fixed).
- **New tools**: `coordinator_claims`, `coordinator_send`, `coordinator_inbox`, `coordinator_unit`,
  `coordinator_seal` and `coordinator_publish`. Writers do not see them.
- The verification record, with commands, exit codes and the limits left open, is
  `plugins/software-workflows/coordinator/VERIFY.md`.

### Added: the git gate knows the coordinator
- **A commit that would take another writer's files is refused**, with no token. In a checkout the
  coordinator has registered, a `git commit` that would take a path another live unit holds, or
  content of unknown origin, is refused with the units and paths named. The reason says to publish
  that work with the unit tools, or to leave those paths out.
  - What a commit takes is read from its own arguments: the index, `-a`, or its pathspecs.
  - Your own unit's paths and free paths get the usual decision.
  - A command the gate can't read as one plain commit (a compound command, or a read-only one that
    only mentions commit) keeps its token and gets the list as a note.
  - A push is gated as before.
- **Measured** on a live coordinator, with a scratch repository: two lead sessions held a.txt and b.txt,
  and c.txt had an unexplained edit. Under `/usr/bin/python3` (3.9.6) and Homebrew python3 (3.15):
  - The 2.2.0 gate, the calibration, gave a token for session A's commit of B's b.txt.
  - The 2.3.0 gate refused, without a token:
    - that commit, naming B's unit;
    - a commit of c.txt, naming it as content of unknown origin;
    - `commit -a`, naming both;
    - a pathspec commit of b.txt.
  - It gave the usual decision for:
    - session B committing its own b.txt;
    - a free path;
    - a pathspec commit of A's own a.txt while b.txt and c.txt were dirty;
    - a push;
    - an uncoordinated repository;
    - a machine with no coordinator state, with the same timing as 2.2.0.
  - Through the git shim (`--argv`), the same refusals held.
  - With the daemon stopped, the reason said it is not running.

### Added: the office shows writers on the coordinator, and steers them live
- **Writers on the coordinator are workers too.** The office asks the coordinator for its runs and reads
  each conversation from the Codex thread's own rollout file: messages, reasoning, commands, file
  changes, web searches, tool calls and each turn's input. The header shows the unit's claimed paths,
  UNKNOWN content and unread messages. When the coordinator does not answer, its writers show as last
  seen, and the page says so.
- **Send now** puts a note into a running writer's turn without stopping it, through `coordinator_send`
  (Codex's `turn/steer`). Pause and Continue act on these writers as on any other run.
- **Measured** on two real native abraham runs (fixture provider, scratch state):
  - One run showed live, then its whole stream, then "Finished".
  - A Continue from the office reached the same thread, and the open page showed the new turn.
  - "Send now" to the second, mid-turn, entered its running turn: the rollout records the
    `C1_MESSAGE … from lead` input before the writer's next answer.
  - Pause turned the third to cancelled.
  - With the coordinator stopped, the page said so.

### Changed: the agent-teams skill
- Rules 7 and 16 now let Codex writers share the real checkout on disjoint claims. They coordinate by
  message, the lead writes on paths no writer holds, and there are still no worktrees, no stash and no
  `add -A`.
- A new section, "Several writers in one checkout", covers:
  - claims at dispatch, and the attribution block;
  - the lead's tools;
  - the lead's own edits;
  - seal, `code_review(sealed_unit=…)` and publish;
  - the gate's refusal;
  - watching the writers.

### Fixed: from the review of the coordinator
- **A command's processes.** A process now counts as the writer's only with evidence: Codex's own
  markers in its environment (`CODEX_SESSION_ID`, `CODEX_THREAD_ID`, `CODEX_TOOL_CALL_ID`), or
  descent from the command. Unrelated processes starting elsewhere on a busy Mac no longer hold
  custody open.
  - Measured: `/bin/sleep 0.2` settled under churn of unrelated processes.
  - A marked survivor that left the command's session and slept 8 s was held until it exited: its
    call took 10.1 s. With its environment cleared, the same survivor was not held: 2.1 s.
- **Git detection in a writer's commands.** `PYTHONPATH=. python -m …`, `env FOO=1 make` and plain
  `env` are no longer refused as unestablished Git execution. A leading assignment that makes a
  shell or interpreter run code the reading can't see (`BASH_ENV`, `PYTHONSTARTUP`, `LD_PRELOAD`,
  `NODE_OPTIONS` and the like) is still refused.
  - Measured on the classification matrix: 0 mismatches. The pre-fix copy, as calibration, gave 5
    false refusals.
- **A writer's MCP servers.** A server of yours that is down no longer stops every write. Only
  servers that can expose the lead's tools must answer; the others are listed as unavailable and
  stay hidden from the writer.
  - Measured on the real configuration: verified in 6.0 s, with two servers down. Before, it refused.
- **The lead's Bash.** A command that changes files, run while no writer is working, no command is
  held and no other lead Bash command is running in that checkout, is recorded as the lead's
  (`lead-command`), never as another unit's. Lead Bash hooks run one at a time per checkout, so no
  other lead command starts between the quiet check and its use. A second completion for the same
  call attributes nothing. Otherwise what it
  changed stays UNKNOWN, as before. The checkout is reconciled before the command, so an edit made
  earlier by something else stays UNKNOWN.
  - Measured beside a native writer: alone, attributed; with a writer's turn running, UNKNOWN; an
    outside edit followed by `pwd`, UNKNOWN.
- **NotebookEdit** on a path another writer claims is refused as a claim conflict, as Edit and
  Write are.
- **A lost reply to a writer's first turn** no longer releases its claims. The run is marked
  started before the turn is sent. (Read in the code; no lost reply was injected.)
- **The push gate's commit scope**, from its review:
  - Pathspecs are resolved by Git itself (`git ls-files --with-tree=HEAD`), including `:(top)`,
    globs, `-C <dir>` and the pathspec flags.
  - A commit whose scope can't be read (`--pathspec-from-file`, `--git-dir`) is judged on every
    changed path.
  - The paths a commit takes are reconciled with the coordinator before the decision, so an edit the
    ledger hasn't seen yet is judged on its current content. If the answer after that is unusable,
    the first answer stands, with a note: an outage never erases a known blocker.
  - `git commit --only` without paths (`--amend` of the message, `--allow-empty`) and
    `--fixup=reword:` take no staged change, and are judged on none. `--no-only`, `--no-all` and
    `--no-include` cancel what came before them. `--fixup <commit>` is no longer read as a pathspec.
  - Paths compare by the checkout's own case rule, as the coordinator compares them.
  - A path the commit takes from the index, whose staged content differs from its working file,
    gets a note: its staged bytes were not checked.
  - Measured: 26 of 26 cases, the 2.2.0 gate as red calibration, and the git shim path.
- **`codex_runs` doesn't start the coordinator to list nothing.** On a machine that has never run a
  native writer, it gets an empty list. A daemon that stopped is started again to answer.
- **A coordinator that can't start says why**: a socket path over macOS's 104 bytes is reported as
  `SOCKET_CAPACITY` in 0.1 s, instead of `DAEMON_START` after 30 s. The diagnostic is written only
  into a state folder that passes the state checks.
- **The office, on native writers:**
  - **Send now** waits for the note to enter the running turn: "entered the running turn", "Codex has
    it — it enters the turn at its next model request" (measured: 36 s), or "not taken by the turn",
    with the reason. When the coordinator stops answering, or the call's budget ends, the note reads
    unconfirmed, never "send it again".
  - **A pause after a Send now** that the turn never took no longer stays "settling". The note reads
    `turn_ended`, and the run reads cancelled.
  - **Pause** says so when the stop can't be confirmed, rather than reporting done.
  - A run's answer is re-read when the run changes, across every page of its result. `run/list`
    now carries each run's revision, so a run continued between two polls is read again.

### Known limits
- **A process that leaves its command's session and hides its environment is not held.** That covers
  `setsid` with a cleared environment, and Apple system binaries, whose environment macOS does not
  show. Its writer's hand-off does not wait for it, and what it writes later shows as UNKNOWN, or as
  the lead's if it lands during a quiet lead Bash command. Codex ends a command's process group when
  the command ends, so `( … &)` and `nohup` children do not outlive it. Codex's review rates this
  HIGH.
- **A lead Bash command in a quiet window** is recorded as the lead's, including what another program
  (an editor saving a file, a background command still running) writes in the checkout while it
  runs. A lead Bash command whose completion hook never arrives stops counting as running after
  its host timeout plus 60 s.
- **The push gate checks the working tree's content.** A path whose staged content differs from its
  working file is not checked by its staged bytes; the gate says so in a note. Codex's review rates
  this HIGH.
- **Still UNVERIFIED from C3** (`coordinator/VERIFY.md`): settlement after a crash; a publication
  crash at every boundary; a review crash at every SQLite write; every malformed lease frame; the
  exec backend's automatic transient retry; the whole C2 matrix in one run; real-model image
  understanding; any platform other than macOS.

## [software-workflows 2.2.0] — 2026-10-18

### Added: images on `codex_resume_run`
- **`images`** attaches local image files to a continuation's turn, as the other Codex tools already
  could for a new call. Codex sees them natively, through `codex exec resume -i`.
  - Missing paths are refused before anything is sent. Measured: refused in 0.0 s, and no run started.
- **Images without a nudge** continue the thread with a default note. Before, an empty nudge on a
  finished run replayed its stored answer, and the images would have gone nowhere. Measured: a
  continuation started with that note.
- **Continuing a failed or interrupted run re-attaches its images.** The journal records the images
  the interrupted turn was given, and Codex may never have received them. One that no longer exists
  is named in the reply.
  - Measured: an image question was killed after its session started (server, watchdog and Codex
    process). A resume with no images re-attached the image, and the answer was "7".
- **Measured through the Codex Office**, on a scratch conversation:
  - The office found `images` in the server's tool list and attached the image.
  - Its note named no path, so Codex could only see the image through the attachment. It read
    "Zebra, 7." off it.

### Fixed
- **A resume never accepts instructions and drops them silently.**
  - When the call handles a detached run instead (it collects the run, or says why it can't), the
    reply now says the nudge and images were not delivered.
  - `run="list"` says the same, instead of only listing.
- **The Codex Office**, from its review:
  - A pause or interrupt dialog keeps the worker it was opened for. The `[` and `]` keys no longer
    move the selection under an open dialog.
  - A note whose write to the server stalls is ended with the server, so it can't arrive later.
  - The reply to a send clears the draft only if the draft still holds exactly what was sent.
  - Uploads land in the draft of the worker they were started for.
  - Continuing a detached run that failed collects its output, then delivers the note, as it already
    did for one that finished.

## [software-workflows 2.1.0] — 2026-10-18

### Added: the Codex Office (`/software-workflows:office`)
- **The office.** A pixel-art office in the browser where every Codex run on the machine is a worker
  at a desk. Live workers show what they're doing right now. Finished ones show how they ended.
- **The conversation.** Click a worker for its whole conversation, read from the run's own event
  spool: messages, reasoning, commands and their output, web searches, file changes and tokens. A
  thread continued over several runs is one worker: review sessions, resumes, and abraham's analysis
  with its writer.
- **Controls.** Pause a worker, continue it with instructions, interrupt it with a note, or queue a
  note for when it finishes. Attach images and files, and `@`-reference workspace files.
- **How it acts.** Every action runs through the plugin's own `codex_cancel_run` and
  `codex_resume_run`, on its own MCP server. Actions run detached: launchd is their parent, so a
  Claude Code restart does not stop them.
- **Images.** The office asks the server whether `codex_resume_run` takes `images`. If it does, the
  images are attached; if not, their paths go to Codex's `view_image` tool.
- **Measured** through the UI on a scratch conversation:
  - An image plus an `@`-file continuation answered with the image's text and the file's secret.
  - Interrupt & send cancelled the live run, and the same thread answered the new note 16 s after the
    click.
  - Pause cancelled a run in about 2 s.
- **Roles.** Besides the six roles that follow the Codex tool, a worker can be one of eleven more:
  CEO, CTO, Project Manager, Product Manager, Business Analyst, QA Engineer, Security Engineer,
  DevOps Engineer, Designer, Data Analyst or Tech Writer. Each has its own sprite and gear.
  - **How a role is chosen:** the person's pick in the office comes first, then a `Role: <name>` line
    from the caller, then the tool's own role.
  - **Claude names roles:** the agent-teams skill tells Claude to name one when a run does that
    role's work.
  - **No guessing:** guessing roles from a task's wording was tried on the real journal and dropped,
    because it mislabelled most of the runs it touched.
- **When the server goes away.** The page says so in a banner. It reloads itself when the office
  restarts with a new token, and reopens the worker that was open.
- **Safety.** It listens on 127.0.0.1 only. It needs the page's per-start token, and refuses a
  foreign Host header. One office runs per data directory, and two starts never race.
- **Only you start it, and its argument never reaches a shell.** The command is marked
  `disable-model-invocation`, so Claude cannot run it on its own.
  - Claude Code pastes `$ARGUMENTS` into a command's shell step as typed. Measured on 2.1.302, a plain
    `!` step given `$(printf INJECTED)` ran the `printf`.
  - A quoted heredoc stopped single-line expansions. A multi-line argument that repeats the
    delimiter still ended it: under bypassPermissions the next line ran (measured), while the
    default mode refused it.
  - So the command has no shell step. Claude reads the argument and runs one of three fixed command
    lines, for start, stop or status.
- **Run status follows the server's rules.** Liveness, orphaned writers and detached runs read as in
  `codex_runs`. A zombie counts as gone; a process table that can't be read counts as unknown, not
  as everything gone. Without a recorded start time, the command line must name codex or the server,
  as the server checks. The office adds two states:
  - "Starting", for a run its server has taken but not spawned yet.
  - "Starting the writer", between abraham's analysis and its writer. Its evidence is the abraham
    call's tree write lock, still held by its server. A failed or unclear read of the lock counts as
    unknown: every way of continuing the analysis then refuses, instead of guessing.
- **A cancel that never finished stays CANCELLING**, as in `codex_runs`. When its server and process
  are both gone, the office says "Cancel unfinished" and offers **Retry cancel**, which ends it.
- **abraham's writer is joined to its analysis by the brief it was handed.** That brief ends with the
  analysis run's own live-log path. All 37 writers in the journal joined their analysis this way.
- **Outcomes come from the journal.**
  - A pause is done when the journal shows the run stopped.
  - A continuation is done when the journal shows the run that the action's own server started.
    Another caller's continuation of the same run never counts.
  - A refusal from the server arrives as ordinary text, and shows as failed with the server's words.
  - A continuation waits as long as Claude Code's own call would: the plugin's 4 h call timeout.
    Writing the request to the server counts against the same deadline.
  - Continuing a detached run that had finished collects its answer first, then delivers the note.
  - An attachment that's gone by the time it's sent is refused by name, and the draft stays.
- **Drafts survive a reload.** An unsent note, its uploads and its `@`-files are kept per worker.

## [software-workflows 2.0.0] — 2026-10-18

Not in 2.0.0: parallel Codex writers in one checkout. abraham still runs one writer per
checkout; the coordinator that lets several share one follows in the next release.

### `scripts/codex_src.py`: the codex source as read-only per-release exports, never a worktree
- `~/Documents/codex-installed` is now a symlink to
  `~/Documents/.codex-installed.exports/<tag>-<sha12>/`, a read-only export of the release
  tag that matches `codex --version`. An export is the commit's blobs written verbatim from
  `git cat-file --batch`. Neither alternative is the release byte for byte: `git archive`
  drops export-ignore paths and rewrites export-subst files, and a checkout applies eol and
  filter attributes. Before the link moves, the export on disk is checked against the
  commit: every blob's content, type and mode, and no extra path except the
  `.codex-src.json` stamp.
- Everything is written through directory descriptors with O_NOFOLLOW and O_EXCL, anchored
  to an exports directory checked to be a real one owned by this user, so nothing is
  written through a symlink. Any of these fails the export, with nothing written outside
  it: a path the filesystem folds onto another (case, Unicode), a `.git` path, a path
  taking the stamp's name, or a symlink that is absolute or resolves outside the export. A
  platform without these primitives is refused up front.
- Nothing is deleted because of its name. A tree is deleted only entry by entry. Each entry
  is first moved to a name only this run knows, then checked against the commit its name
  records, and deleted only if it is still the very entry checked. A file is judged by the
  descriptor it is read through, and only if it did not change while being read. So the
  script deletes only what git can recreate, and a file saved at an entry's old name
  meanwhile is never touched. Modes are not data. Git must supply each entry's bytes
  itself, read to the end and checked against the object id: when the clone has lost a
  blob, the export's copy may be the only one, so it is kept. The one file that need not
  be whole is the one a build of this run was writing when it failed, while it holds
  exactly the bytes that reached it; its identity, length and digest are tracked as it is
  written. A file a killed run cut short cannot be told from an edit afterwards, so it is
  kept like one. The stamp goes only when it is, byte for byte, a stamp this script writes
  for that export's commit (the git-archive version's included): an edited, added or
  reordered field keeps it. Git records bytes, a mode and a type and nothing else, so an
  entry carrying an extended attribute (a resource fork, a user's tag or note) is kept too;
  `com.apple.provenance`, which macOS sets on every entry and no process can, is not data,
  and neither are Linux's `security.*` and `system.*` namespaces (labels, capabilities,
  ACLs: permissions, like modes) — `user.*`, `trusted.*` and any newer namespace are. An
  entry whose attributes cannot be read is kept the same way. On Linux, where `trusted.*`
  is listed only to a process privileged in the initial user namespace, the script
  deletes nothing and says so. A link the script made that carries one is kept aside beside the stable path as
  `.<dir name>.kept-<time>` and named on every run, never deleted.
  Anything git cannot recreate is moved aside as `.<export>.kept-<time>` and listed on every
  run, an aligned one included, never deleted: an edit, an added file, a file at a
  leftover's name, a write a killed run cut short, an extended attribute, or a tree whose
  commit or blobs the clone no longer has.
  Anything else in the exports directory is left alone and listed. A link a killed run
  left beside the stable path goes only if it is one this script writes: a symlink naming
  an export.
- No rename replaces anything: every one uses `renameatx_np` with `RENAME_EXCL` on macOS,
  or `renameat2` with `RENAME_NOREPLACE` on Linux, and a platform without either is
  refused. An entry that cannot go back to its name, because something was created there
  meanwhile, stays beside it, and the refusal names both.
- Reruns re-verify the current export. A damaged one is rebuilt beside it and swapped in
  with two renames, so a failed rebuild leaves it published. A version switch keeps the
  export it replaces, since a session may still be reading it.
- The stable symlink is created exclusively. An existing one is first moved aside and
  checked to be the very link this run inspected, so something created at the path, or put
  in place of the link, while a run is under way is never replaced. Exports are kept by
  identity as well as by name: a name the filesystem folds onto a kept export (case) is
  that very directory, never swept. The link, a new one or the one an aligned run found,
  stays only if what a reader following it reaches, checked after the cleanup, is the
  export this run verified; otherwise it is withdrawn and the run fails. Anything at the stable path that this script did not create
  is refused untouched, including a worktree an older version kept there: this script never
  deletes a worktree. A stamp that is not a regular file (a FIFO, a directory) is read
  without blocking and repaired.
- `GIT_DIR` and the other repository-local git variables (`git rev-parse --local-env-vars`)
  are cleared first, so an inherited one can no longer point the export or the fetch at
  another repository. This was so before the rewrite too.
- What it cannot promise: a process of the same user racing on purpose can still swap an
  entry in the instant between the last check and the delete, or replace the exports
  after the link is checked. POSIX has no unlink-if-unchanged. A stamp edited into exactly
  the form this script writes (another well-formed time) cannot be told from one it wrote.
- Measured on the maintainer's machine 2026-10-18 (macOS 26.6.2, APFS):
  - Replacing a symlink is not atomic for concurrent lookups: 2,988 EINVAL failures in
    349,736 lookups over 20,000 swaps. Moving directories instead failed 887,379 of
    1,213,821.
  - `openat()` with O_CREAT fails with ENOENT when another process is creating the same
    name: 1,180 of 1,600 racing opens. The lock file is therefore opened with O_EXCL first,
    then plainly (0 of 1,600 failed).
- Verified by real runs on the maintainer's machine, 2026-10-18 (codex-cli 0.163.0, the
  real `~/Documents/codex` clone, a scratch stable path; every exit code is the script's
  own):
  - A fresh export, 9,176 files: exit 0. A rerun: `already aligned`, exit 0.
  - One file's mode changed and another file given a resource fork in the published
    export: the next run rebuilt it, kept the replaced export aside with the fork intact,
    and named it (exit 0); the new export carries no fork. The aligned run after it named
    the kept export again (exit 0).
  - The stable link given an attribute, then a repair that replaces it: the old link was
    kept beside the stable path, its attribute intact, and named (exit 0); the new link
    carries only `com.apple.provenance`; the aligned run after it named the kept link again.
  - The stable link's attributes made unreadable (an ACL denying `readextattr`), then a
    repair that replaces it: the old link was kept aside and named, exit 0.
  - The real `~/Documents/codex-installed`: `already aligned`, exit 0.
  - Linux was not run: its rules follow xattr(7), unmeasured.
- Six blind reviews shaped it: 3 HIGH and 7 MEDIUM, then 2 HIGH and 4 MEDIUM, then 4 HIGH
  and 4 MEDIUM, then 3 HIGH, then 2 HIGH and 1 MEDIUM, then 1 HIGH and 1 LOW (an extended
  attribute deleted with its entry; an aligned run that named nothing kept). Follow-ups in
  the sixth review's own Codex session then found 2 HIGH and 1 LOW (a replaced link's
  attribute deleted with it; Linux's `trusted.*` and newer namespaces counted as not data;
  stale references to the removed tests), then 1 HIGH and 1 MEDIUM (`trusted.*` invisible
  to an unprivileged process; an unreadable link attribute raised instead of keeping it),
  then 1 HIGH (a capability held in a child user namespace taken for one that lists
  `trusted.*`). Linux now deletes nothing, the fix that last answer named.
- The README and both Codex skills describe the export.
- Migrated on the maintainer's machine 2026-10-18: the rust-v0.153.4 worktree was replaced
  by the rust-v0.163.0 export. A full export (9,176 files, 91 MB) builds in about 2.4 s,
  and a rerun's content check takes about 0.25 s.

### Spike U0: what codex-cli 0.163.0 offers parallel writers in one checkout
- `plugins/codex-oracle/spike/u0/`: four probes, each run red first against a deliberately
  bad configuration before it measures — apply_patch mediation, mutator hooks, instruction
  loading, file-change events. `FEASIBILITY.md` records what each measured (its generated
  section) and the multi-repository target model it led to. Measurement only: no plugin
  behavior changes, and the probes' `evidence/` directories are regenerated and
  git-ignored.

### No test harness: verify with results (user ruling 2026-10-18)
- The ruling: no test cases unless the user explicitly asks for them; where a repository
  already has more than a few, they are removed unless the user asks to keep them; a change
  is verified by running the real thing and recording what was observed. This release
  removes the harness: `tests/` (eight files, about 9,350 lines — the detach, push-gate,
  write-mode, transient-retry and anchor-lint suites, the retry-pin check and their
  fixtures) and the plugin's `selftest_detach.py` and `selftest_progress.py`. One revert
  of that commit brings them back.
- The plugin stops asking for tests. The agent-teams skill's new Critical Rule 17 states
  the ruling; the `tester` role is now `verifier`, which runs the change for real and
  reports what it observed; the example task, the implementation-team pattern and the
  quality review no longer write tests or measure coverage.
- Codex is told the same. `code_review` asks for the real run that would show a claim,
  never for new test cases. The planning brief's Verification section and the full-access
  implementation prompt say no test cases unless the user asked for them. The capability
  text, the code-reviewer agent and the codex-review skill ask for one real run on the
  deployed engine instead of coverage. `/abraham` tells the lead to run the change for real.
- Comments that pointed at removed tests now say how to check by hand: for the
  ServerOverloaded text, grep the installed release's source. The README says how to see
  detach work: `/mcp` → Reconnect during a long call, then `codex_runs()`.
- Verified 2026-10-18: the checkout's server, started over stdio, initializes and lists its
  nine tools; the checkout's push gate passes `ls -la` and denies a commit with no review;
  `claude plugin validate --strict` passes for the marketplace and both plugins; every
  changed Python file compiles.

### One plugin: codex-oracle is part of software-workflows
- The Codex Oracle MCP server, its hooks (plan gate, push gate, live-view nudge), `/abraham`,
  the code-reviewer agent and the two Codex skills now ship inside `software-workflows`
  (`plugins/software-workflows/`), with their history (`git mv`). The separate `codex-oracle`
  plugin is gone; the marketplace's `renames` maps `codex-oracle` to `software-workflows`, so
  an install that still has the old plugin enabled moves to this one, as `antigravity` was
  handled (Claude Code v2.1.193+).
- Codex tools now surface as `mcp__plugin_software-workflows_codex-oracle__<tool>`. The push
  gate already recognizes a review under any `mcp__…__code_review` name; the server
  recognizes its own processes under either layout; the code-reviewer agent lists every
  name; the agent-teams skill and `/abraham` say which name to expect.
- Verified 2026-10-18 by real runs: `claude plugin validate --strict` passes for the
  marketplace and the plugin; in an empty `CLAUDE_CONFIG_DIR`, `plugin marketplace add` of
  this checkout and `plugin install software-workflows@agent-teams` succeed, `plugin list`
  shows 2.0.0 enabled, and `claude mcp list` connects `plugin:software-workflows:codex-oracle`.

### Launcher: any python3 starts the server; its venv lives in the plugin's data directory
- `run_server.py` runs under any Python 3, including the 3.9 macOS ships as `python3`, and
  picks the interpreter the server needs (3.11+): `CODEX_ORACLE_PYTHON` when set (used as
  given, or refused with the reason), else the newest `python3.N` on PATH, `/opt/homebrew/bin`,
  `/usr/local/bin` or `~/.local/bin`, else itself.
- The venv moves to `${CLAUDE_PLUGIN_DATA}/venv/<key>` (`.venvs/` beside the launcher when
  Claude Code sets no data directory), so a plugin update no longer rebuilds it. The key is the
  requirements, the interpreter the venv links to and its version, and the platform; a change
  to any of them builds a new generation.
- Builds are serialized by a lock, and a generation counts only once `.ready` is written,
  after the server's imports load in it. The build's own processes hold that lock as well:
  Claude Code kills a server slow to start (`MCP_TIMEOUT`) but not the pip it started, so the
  next start waits for that pip to stop instead of building over it.
- A running server holds a shared lock on its generation and hands it to each detached
  watchdog, which runs that venv's python after the server may be gone. An old generation is
  deleted only when no process holds it.
- Verified 2026-10-18 by real runs (macOS 26.6, Homebrew Python 3.15.0, Apple Python 3.9.6):
  - under `/usr/bin/python3` with an empty data directory: python3.15 found, venv built in
    4 s (10 s with no pip cache), nine tools listed; the next start reused it in under a second;
  - two first starts at once: both list nine tools, one build;
  - an old generation that a process holds stays and one nobody holds is removed; a
    half-built one (no `.ready`) is removed and rebuilt;
  - `CODEX_ORACLE_PYTHON=/usr/bin/python3` is refused ("is 3.9; the server needs >= 3.11"), a
    missing path is refused ("not runnable"), `python3.15` is used; with no
    `CLAUDE_PLUGIN_DATA` the venv is built in `.venvs/`;
  - during a `codex_query`, `lsof` shows the server, its watchdog (`/bin/sh`) and the
    watchdog's `sleep` holding the generation, and an exclusive lock on it is refused; after
    the call nothing holds it;
  - `claude mcp list` in a fresh config connects; the venv is in
    `plugins/data/software-workflows-agent-teams/venv/`, and the generation an earlier key had
    built there was removed;
  - with `MCP_TIMEOUT=4000` and no pip cache, Claude Code reports the timeout and its pip runs
    on as an orphan holding the build lock; the next start waits for it, rebuilds and connects
    in 6 s, never two pips at once. Calibrated on a copy without the handoff: there the lock is
    free right after the kill, and the next start deletes the venv under the running pip.
  - Not measured: Windows (no machine here). Its build lock retries instead of using
    msvcrt's blocking mode, which gives up after 10 s; old generations are not removed there.

### Push gate: decided where git runs, so read-only text is never denied again
- A SessionStart hook (`hooks/session_start.py`) puts `hooks/gitshim` first on the Bash tool's
  PATH through `CLAUDE_ENV_FILE`, with the session's id and transcript. The main session, its
  subagents and its agent-team teammates share that file (measured, Claude Code 2.1.302).
- `hooks/gitshim/git` runs the real git — the next one on PATH — at once for every git command
  but commit and push. Those two, and whatever the shim does not recognize (an alias, an
  external command, an unknown option), go to `push_gate.py --argv` with their exact argv, cwd
  and environment. There they get the same decision, wording and one-shot token as before
  (`_decide`, shared with the text hook): aliases are expanded as git expands them, `-C` moves
  the inspection with it, and a script or tool that runs git is seen as well. git runs with a
  mirror of its exec-path, so the gits git itself starts come back through the shim (see
  Review fixes).
- Once a session's git has gone through the shim (a `.live` marker; cleared at every session
  start), the PreToolUse hook stops reading that session's Bash text, which ends the false
  denials of greps, `sed` and heredocs that merely mention push or commit. PowerShell, and a
  session whose shim has not run yet, keep the text gate unchanged; Windows does not install
  the shim (not measured there).
- A denial the command's own output hides reaches Claude through the PostToolUse and
  PostToolUseFailure hooks (`hooks/gate_notice.py`): only denials from that call, and only when
  the result does not already show their token.
- Verified 2026-10-18 by real runs against a scratch repository with a bare remote (macOS 26.6,
  git 2.56.0):
  - through the shim, `git status` and `git log` run the real git and write the live marker;
  - five read-only commands from the false-positive corpus: 0 denied in the live session,
    3 denied in a session whose shim never ran; PowerShell `git push` still denied;
  - `git commit -m second` is not run and hands out a token; with the token it is recorded;
    the token works once and only for its own command;
  - with a signed `code_review` answer for the tree in the session's transcript, the wording
    reads REVIEW VERIFIED; `git push origin main` is not run until its token, then lands;
  - a configured alias (`ci` = commit), `sh -c "git …"`, `git -C <repo>` run from `/`, and the
    shim under dash are all stopped the same way;
  - with stderr or all output redirected, the notice hook delivers the denial (twice in the
    run, once per hidden denial) and stays silent when the output showed it;
  - every hook, `treedigest.py` and `procenv.py` run under Apple's Python 3.9.6, the gate's
    `--argv` mode included;
  - cost per git call: 3.8 ms for the real git, 6.7 ms through the shim (builtins, `-C`,
    `--version`), 50 ms for an alias resolved in Python;
  - end to end, a headless Claude Code 2.1.302 session with this plugin (the installed
    agent-teams plugins switched off for the run): the corpus grep ran, the commit was stopped
    with a token and recorded with it, a fully redirected commit reached the model through
    the notice hook, and a subagent's commit was stopped; the repository held exactly the one
    acknowledged commit;
  - `claude plugin validate --strict` passes for the plugin and the marketplace.

### Reviews continue their own Codex session
- A `code_review` or `architect_review` is now the next turn of this workspace's latest
  completed review of the same kind: `codex exec resume <thread id>`, Codex's own resume, so
  the reviewer starts from its earlier findings and its reads of the code instead of a
  restated summary, and says which findings are fixed, which still stand and what is new.
  `session="new"` starts a fresh session; a thread id (or a run id) continues that session;
  `research` and `codex_query` start fresh unless given `session="continue"` or an id.
- The result's second line names the session: thread id, new or continued, turn number,
  this call's tokens (input, cached, output), the context size against the window, and how
  often Codex compacted it. The figures come from the thread's rollout file, because on a
  resume `turn.completed` carries the thread's total and the exec JSON shows no compaction.
  The signed first line is unchanged, so the push gate and abraham's gate read it as before.
- One call per thread: the plugin holds the thread claim while a session is continued. A
  session another call is using falls back to a fresh one, with a note; an explicit id that
  is busy, archived, unknown or ambiguous is refused before anything reaches Codex.
- Images now attach to a continued turn too (`-i` before `resume`; codex 0.163.0 chains them
  into the resumed turn's input).
- `codex_resume_run` with a nudge on a run that completed no longer restates the original
  task as a recovery block: the thread holds it.
- The comment on auto-compaction is corrected for 0.163.0: a configured limit is clamped to
  90 % of the window under the default scope, so it can only lower the threshold.
- Verified 2026-10-18 by real runs (codex-cli 0.163.0, gpt-6-astra at max) against a scratch
  repository:
  - the first review started a session (turn 1); after a fix, the next one continued it as
    turn 2, reported both earlier findings "STILL OPEN" and noted the empty-input change as
    the behaviour it had earlier classed as an open question; nothing about the first review
    was in its prompt;
  - `session="new"` gave a different thread;
  - of two reviews started 3 s apart, the first continued the session and the second started
    a new one with the busy note;
  - an explicit thread id with an image continued that session as turn 3; the image reached
    the thread (0 → 2 `input_image` records) and the answer named its colour;
  - `session="deadbeef"` was refused with nothing sent;
  - `codex_resume_run` with a nudge on a completed review sent the nudge alone and was
    answered from the thread's context;
  - the push gate's parser read the signed first line of both continued answers.

### `/software-workflows:setup`, and a session-start check that points to it
- `/software-workflows:setup` runs `plugin_setup.py check` and takes the person through the
  result. What a plugin cannot switch on, agent teams, it turns on in the person's own
  settings after asking: one key, `env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1"`, added in
  place (every other key kept, file mode kept, written atomically and through a symlink; a
  file that is not a JSON object is refused unchanged). It builds the Codex Oracle server's
  environment if it is missing (`run_server.py --prepare`). For the Codex CLI, `codex login`,
  Python 3.11+ and git it prints the exact fix. Nothing asks anyone to edit settings.json by
  hand any more; the README's settings snippet is gone.
- The command is allowed to run only its own script (`Bash(python3
  ${CLAUDE_PLUGIN_ROOT}/plugin_setup.py:*)`), so it works in the default permission mode
  without opening `python3` in general.
- The SessionStart hook runs the quick checks when a session starts or resumes, about 120 ms.
  When one fails it shows the person a line naming what is missing and tells the model to
  point to setup; when everything is ready it says nothing.
- Measured on Claude Code 2.1.302 before building it: `${CLAUDE_PLUGIN_ROOT}` and
  `${CLAUDE_PLUGIN_DATA}` expand in a command's body, its `!` steps and its `allowed-tools`,
  but the step's process does not get them as variables; a SessionStart `additionalContext`
  reaches the model and a `systemMessage` does not; a settings `env` value reaches the hook.
  In the default permission mode a `!` step without a matching `allowed-tools` rule is
  refused, and with the narrow rule it runs.
- Verified 2026-10-18 by real runs:
  - on this machine the check reports 6 of 6 ready under Python 3.9.6 and 3.15;
  - each item goes red when its cause is present (agent teams off, Codex logged out, no codex
    or git on PATH, `CODEX_ORACLE_PYTHON` forced to 3.9, no server venv), with its fix;
  - `prepare` built the server venv in an empty data directory in 4 s and it imports the
    server's modules;
  - `apply` created a settings file, merged into one with other keys (mode 600 kept),
    reported "already on" the second time, refused invalid JSON byte for byte and an `env`
    that is not an object (exit 1), and wrote through a symlinked file, leaving the link;
  - the hook said nothing on a ready machine, named agent teams and codex login when both
    were missing, and said nothing on compact;
  - headless Claude Code sessions with this plugin in the default permission mode: on a
    ready machine setup reported 6 of 6 and changed nothing; with Codex logged out the
    session-start notice reached the model and setup told the person to type
    `! codex login`; with the server venv missing setup built it without a question, checked
    again and reported 6 of 6.

### abraham: full access in both phases, always (user ruling 2026-10-18)
- Every abraham run now has full access in both phases: no sandbox, network, live web
  search, the person's MCP servers and live infrastructure. The implementation phase runs
  with codex's `--dangerously-bypass-approvals-and-sandbox`. The analysis phase is read-only
  by instruction and always gets the live-state and external-facts sections of the brief.
- The sealed mode is gone ("let us remove the option wherein they can keep full access
  off"). So are the write-capability probe, its cache and the `.abraham/tmp` TMPDIR redirect.
  A request for the old mode is refused before anything reaches Codex, never ignored:
  `full_access`, `infra` or `web_search` set to false on abraham, `infra` or `web_search`
  false on a write `codex_resume_run`, or `CODEX_ORACLE_WRITE_FULL_ACCESS` set to a false
  value. A run that began sealed under 1.x continues with full access, and the resume says so.
- No folder fence: `cwd` may name any git repository on the machine. The run is journaled
  with its target and the folder it was dispatched from (`dispatch_cwd`), so `codex_runs`,
  `codex_run_log`, `codex_cancel_run` and `codex_resume_run` find it from either folder. The
  writer is told to write inside its target repository and the repositories nested in it,
  and to name a change another repository needs instead of making it. Git as the undo, the
  tree's write lock and the changed-files report work in every repository.
- The write report ends with the mode Codex recorded for each of the run's turns, read
  back from the thread's rollout (`turn_context`: sandbox policy and approval policy). The
  report says "full access" only when every turn ran `danger-full-access/never`. Anything
  else is flagged ⚠, and no record at all reads UNVERIFIED. The modes are journaled as
  `effective_modes`. A flag and an exit code are not evidence: the old sealed sandbox once
  downgraded itself to read-only and still exited 0.
- `context` carries the access paths the lead knows (a key-based SSH host, a `docker exec`
  target, a local test database), never credentials. The writer's brief tells it the same.
- Verified 2026-10-18 by real runs (codex-cli 0.163.0, gpt-6-astra at max):
  - all four refusals (`full_access=false`; `infra` and `web_search` false; the variable set
    to 0; a dirty tree) returned with nothing sent to Codex;
  - dispatched from one folder against a repository outside it, abraham added `slug()` to
    that repository's `lib.py`. The writer ran `python3 -c …` (printed `hello-world`, exit 0)
    and `curl -sI https://example.com` (`HTTP/2 200`, exit 0). CHANGED FILES named `lib.py`,
    HEAD stayed unmoved, and the effective mode read full access. The lead's own re-run
    printed `hello-world a-b`;
  - `codex_runs` listed both phases from the dispatching folder and from the target;
    `codex_resume_run` from the target returned the stored answer; a write resume with
    `infra=false` was refused;
  - the read-back, calibrated: a read-only `code_review` thread gave `read-only/never` and the
    ⚠ line; the write run gave `danger-full-access/never`, journaled the same.

### abraham reports what changed during its analysis phase, and every write report is by content
- The analysis phase has full access and is read-only only by instruction. Until now, anything
  that changed during it was filed as pre-existing dirt by the implementation phase's report.
  abraham now reads the tree before and after the analysis phase, on every way out of it. When
  they differ, the result starts with a ⚠ note naming the paths: changed by the analysis phase
  or by another writer meanwhile. When phase 1 fails, the failure no longer says "nothing was
  written": it says phase 2 never ran, and either names what changed or says the tree is as it
  was. A tree that cannot be read before or after makes the note say the answer is UNKNOWN.
  Found by the architect review of 2026-10-18; the code review the same day found that status
  lines could not see a further edit to a file that was already dirty.
- The comparison is by content. treedigest's one walk of the tree now also returns a digest of
  each path (content, exec bit, symlink target, gitlink head), so a further edit to an
  already-dirty or untracked file shows. The CHANGED FILES block of every write run uses the
  same snapshots: it names each changed path with its status line, marks the ones that were
  dirty before as `(dirty before)`, and counts the dirty paths the run left as they were. Its
  header says that another writer's changes in the same tree meanwhile count too.
- Verified 2026-10-18:
  - in-process, on real files in a scratch repository: status lines alone missed a further
    edit to a dirty `b.txt` and to an untracked `u.txt`; the snapshots named both, a staged
    change, a new file and a clean file made dirty, counted the dirty `c.txt` left alone, and
    reported UNKNOWN for a tree that could not be read;
  - by a real run (codex-cli 0.163.0, gpt-6-astra at max), with `b.txt` dirty and `u.txt`
    untracked before dispatch and both appended to while phase 1 ran. The note named
    ` M b.txt (dirty before)` and `?? u.txt (dirty before)`, CHANGED FILES named ` M a.py` and
    "dirty before dispatch and left as they were: 3 path(s)", HEAD was unchanged, and the
    effective mode read full access;
  - by a real run whose phase 1 was cancelled (`codex_cancel_run` from a second server) right
    after another process appended to the already-dirty `b.txt`: abraham answered "ANALYSIS
    phase failed — phase 2 never ran", with the note naming ` M b.txt (dirty before)`.

### Review fixes (code review of 2026-10-18)
- **The git shim sees every git that git starts.** git puts its own exec-path first on PATH
  for everything it starts (`setup_path` in exec-cmd.c), so a git that git itself started,
  through an alias, an external command or a step of its own command, never came back through
  a shim on PATH. The gate now gives git a mirror of that exec-path as `GIT_EXEC_PATH`
  (`push_gate.py` `_git_exec_farm`). In the mirror, `git` leads back to the shim, each dashed
  builtin runs `git <name>` through it, and every other entry links to the real one. It is
  built once per git binary, exec-path and plugin version, so sessions of two versions can run
  side by side, and the shim's fast path finds it through an index. External commands and shell
  aliases therefore run, and only the git commands they start are decided. Also:
  - The fast path lists only the builtins every supported git has (≥ 2.39.4, checked against
    that version's own command table), except commit and push, because a name that is a builtin
    in a newer git can be an alias in an older one. Anything else goes to the gate. The gate
    asks the real git for its builtins, then looks for an external command (from the directory
    `-C` leads to, as git does), then for an alias.
  - A command git does not know is answered by the gate and never handed to git, whose
    autocorrection could run a different command in its place.
  - A global option the gate does not read, an exec-path of the command's own, and an alias
    chain it cannot follow are each decided like a push.
  - A commit or push that git starts as a step of the command that started it runs as that
    step: a commit while a rebase, cherry-pick, revert or merge continues, a push for each
    submodule of a push, or the commit an acknowledged alias to commit runs (`git ci`). The
    parent's exact argv is read from the kernel (sysctl on macOS, /proc on Linux), never from
    `ps`, which joins arguments with spaces. Its verb is read the way git dispatches it (a
    builtin before an alias of its name), in the directory the parent's `-C` already led to.
  - A git that git started is decided like any other: its builtins and its dashed helpers run,
    an alias is followed, and a name git does not know is refused. An alias chain that ends in
    a push, or a misspelling git would autocorrect into one, cannot skip the gate.
  - Where no mirror can be built or passed down for the git that runs (an unwritable state
    directory, an exec-path that cannot be mirrored), the gate refuses the command and says
    why, rather than run git where what it starts would not come back. A mirror cached for
    another git does not count. The text gate stays in charge until a usable mirror exists.
  - The command's stdin stays its own on the fast path.
  - A session start removes a mirror nothing can use any more: its git binary, exec-path or
    shim is gone, or its exec-path changed more than a day ago. It also removes a build that
    died.
  - The boundary is stated in the gate: a git that the shell does not find through PATH by
    name (one run by its full path) is not seen, and a commit started inside an allowed parent
    runs as that parent's step.
  Verified 2026-10-18 by real runs in scratch repositories with no remote, under Apple git
  2.50.1 and Homebrew git 2.56.0, comparing HEAD's shim and gate (a read-only `git archive`
  export) with the checkout's:
  - Six ways for a git command to start a push ran it through HEAD's shim; the new one decided
    all six, under both gits.
  - A plain commit and an alias to commit are denied by both.
  - An acknowledged alias to commit completes as one commit. `git submodule status
    --recursive`, which enters a submodule, runs. A git that git starts under a custom
    exec-path still finds that exec-path's commands.
  - Each later fix was run against a copy with only that fix taken out, under both gits. An
    alias chain to push and a misspelling, each started by git, pushed through the copy and
    were denied or refused by the checkout. With one git's mirror cached and none buildable
    for the other, a builtin whose step runs a push pushed through the copy while the text
    gate would have stood down, and was refused by the checkout, in both directions. An
    acknowledged alias to commit run with a relative `-C` needed a second acknowledgement
    through the copy and committed once through the checkout.
  - The four `--continue` flows finish through the new shim. In the traced flows,
    `cherry-pick` and `revert` each start one nested commit, and `rebase` and `merge` commit
    in-process. With the step rule taken out, the nested commit of `cherry-pick --continue` is
    denied.
  - A git child sees the mirror as its exec-path, and `git-sh-setup` sources through it. stdin
    is intact on both paths, and a `file://` clone works.
  - With two copies of the hooks sharing one state directory, each version's gits reach its own
    shim, through the fast path alone too. The cleanup kept and removed exactly the seven kinds
    of mirror and five kinds of file set up for it.
  - Cost per git call on the fast path, on the final code: 6.1 ms against 3.4 ms for the real
    git under Homebrew git (HEAD's shim: 6.7 ms), and 14.2 ms against 10.6 ms under Apple git
    (HEAD's: 14.2 ms).
  - The Linux branch of the argv read (/proc) was not measured.
- **The denial notices: failed calls too, and crash-safe.** A denial exits 1, and a Bash call
  that exits non-zero fires PostToolUseFailure, never PostToolUse. Measured on Claude Code
  2.1.302: that payload carries the call's output in `error`, plus `duration_ms`, and its
  `additionalContext` reaches the model. The notice hook now runs on both events and answers
  with the one it ran for.
  Bash calls run side by side. Readers and writers take one lock file, which is never removed.
  A reader writes what it keeps to a new file and renames it over the notice, so a reader that
  dies midway leaves the notice whole. It states its denials before it lets go of them.
  A denial from before this call started stays for the call that made it. Once no foreground
  call can still be running (the Bash tool's longest timeout: 600 s, or `BASH_MAX_TIMEOUT_MS`),
  the next call shows it as an earlier command's, which also covers a background command's
  denial. A denial counts as shown only by its token: a header without its token cannot be
  acted on. The window rests on the payload's `duration_ms` (3074 for a `sleep 3` call).
  Verified 2026-10-18 by real runs of the hooks, comparing the version the review read with the
  checkout's:
  - After a failed call, the old hook repeated a denial the output already showed, under the
    wrong event; the new one showed only what was hidden, under PostToolUseFailure.
  - Killed at the moment it rewrites the notice, the old hook lost a running call's denial; the
    new one lost nothing.
  - With 12 writers and 4 readers side by side, three rounds of 720 denials: each shown once,
    none lost.
  - A writer that does not take the lock file loses its record whenever a reader is inside its
    rewrite (calibrated by holding the reader there for 1 s). The new writer waited 0.6 s and
    its record was kept.
  - End to end, in a headless Claude Code 2.1.302 session with each plugin: a failed call's
    fully redirected denial reached the model with the checkout's hooks and not with HEAD's.
- **Setup quotes its paths.** Measured on Claude Code 2.1.302 with a space in the plugin root:
  an unquoted `!` step breaks, and a double-quoted step runs only under a double-quoted
  allowed-tools rule (not under the unquoted one, nor under an unrelated one). The command's
  step, its rule and every command `plugin_setup.py` prints now use that form. Verified at a
  path holding a space: the real `/software-workflows:setup` ran its step and reported all 7
  items, and the model ran a printed quoted command under the rule with no permission denial.
- **Setup never loses a settings write made meanwhile.** Claude Code writes `settings.json` too.
  `apply agent-teams` now exchanges its merged file with the one in place in one atomic step
  (macOS `renameatx_np` RENAME_SWAP, Linux `renameat2` RENAME_EXCHANGE). If the exchange
  displaced something other than the text the merge was made from, a write landed meanwhile.
  That text is merged and exchanged in turn, until a swap displaces only setup's own text. A
  file that did not exist is created only if it still does not. A displaced text that is not
  JSON goes back as it was. After five such swaps setup stops, keeps the newest displaced text
  beside the file and says where. Where the system has no exchange (Windows, or a filesystem
  that refuses it), the file is read again just before an ordinary replace, and a write landing
  in that last instant is the one setup cannot see. A file that is not valid UTF-8 is refused
  like invalid JSON. What an exchange displaces goes to a recovery file before setup reads it,
  so a crash at any moment leaves a concurrent write on disk. The temporary and the recovery
  file get names no file held before, created exclusively: what an earlier run left, even
  under a reused process id, is never truncated or overwritten.
  Verified 2026-10-18 under Apple's Python 3.9.6, with another writer's change injected inside
  the call that puts setup's file in place:
  - The version the review read lost the change in every case: once, every time, and a file
    created meanwhile.
  - The new one kept the change in each case. It restored a half-written file byte for byte and
    refused it. After five swaps, every foreign change was in the file or in the kept text.
  - With the exchange taken away, it loses the injected change, as stated above.
  - Killed with no cleanup right after the exchange, right after the recovery name exists, and
    right after the recovery file is written: the concurrent write was on disk each time.
  - With files present beforehand under the names the previous version would use for this
    process, and under the first names the exclusive allocation tries: the previous version
    lost the first two; the new one left all four as they were and wrote under other names.
  - The ordinary paths are unchanged: create, merge, already on, through a symlink, and not
    JSON.
  - The Linux branch was not measured.
- **Change reports see inside submodules, and paths that display alike.** The snapshots behind
  the CHANGED FILES block and abraham's analysis note now cover what is inside a checked-out
  submodule: its paths and its status, recursively. Before, a further change inside an
  already-dirty submodule was invisible to them. Two byte paths that display alike (bytes that
  are not UTF-8) share one entry made of both and keep both status lines, so a change to either
  shows. They also carry each tracked path's staged identity, at the top level, inside
  submodules, and for every conflict stage, so a change to the index alone shows. The digest
  the push gate binds reviews to is unchanged and still takes a submodule as its HEAD.
  Verified 2026-10-18 on real files in scratch repositories, comparing the code before this fix
  with the checkout's:
  - Five changes were missed before and are named now: a file edited again in a dirty
    submodule, a file edited in a submodule whose HEAD moved, a change staged inside a
    submodule, a new file in a dirty submodule, and an edit to one of two paths that display
    alike.
  - A staged blob swapped under unchanged worktree bytes and status letters, at the top level
    and inside a dirty submodule, and a change to one conflict stage alone: each missed before
    and named now.
  - A tree where nothing changed still reads as unchanged.
  - One tree held a dirty submodule, a dirty file, an untracked file and the colliding pair.
    HEAD's treedigest, the earlier one and the checkout's gave the same digest and the same
    status lines on it.
- **`scripts/codex_src.py`**: an entry whose extended attributes cannot be listed, for any
  reason (ENOTSUP included: CIFS returns it when attributes are only switched off), is kept,
  never deleted. Verified by injecting ENOTSUP into the listing call.
- **Claude Code's Bash sandbox.** With the sandbox on, Bash may read the gate's state and token
  store but never write them: they live under `~/.claude`, which the sandbox protects (measured
  on Claude Code 2.1.302: EPERM under macOS Seatbelt). HEAD's shim failed every git command
  there, because it wrote its live marker by a redirection on `:`. That is a special built-in,
  and a failed redirection on one exits a non-interactive shell (measured under sh, dash and
  bash --posix). Now:
  - The marker is written by `true` with stderr redirected first, and the shim's reads of the
    mirror index say nothing when a file is gone, so git runs whether or not the state can be
    written. The mirror index and the denial notices are best effort.
  - The session-start hook, which runs outside the sandbox, builds the exec-path mirror for the
    git the session finds on PATH, so the shim has one without writing. Where none exists for
    the git that runs (another git first on PATH, an upgrade during the session), the shim
    builds it in a private directory of its temp dir, and the gits it starts look there.
  - The shim cannot mark the session live, so the PreToolUse gate stays in charge of the text
    and hands out the token as before. When it accepts a token for a command that is exactly
    one plain `git commit …` or `git push …`, it leaves a pass in the token store. The shim
    decides that commit or push again on its exact argv, finds the token gone and the pass
    there, and lets that one argv run in that directory, on that tree and index, within 120 s.
    The shim removes the pass it uses; where it cannot, the PostToolUse hook removes it after
    the call. The same pass spares an unsandboxed session a second token when its first git
    command is a commit or push. Like the rest of the gate, the pass is a guardrail for
    Claude's own commands, not a barrier against a process set on getting around it. Known
    from the code review of 2026-10-18 (turn 9), not fixed in 2.0.0: once a git command of
    the session has run outside the sandbox, a commit or push inside it can no longer be
    acknowledged (run it outside the sandbox); the pass does not bind an alternate index or
    git directory named in the environment, and inside the sandbox it is not single-use
    until the PostToolUse hook removes it.
  - A commit or push the shim must decide inside the sandbox (an alias, a script, a push a hook
    starts) is refused, with what to do: run it as one plain git command in its own Bash call
    (`git commit -F <file>` for a long message), or run it outside the sandbox.
  Verified 2026-10-18:
  - Real headless Claude Code 2.1.302 sessions with the sandbox on, the gate's state where the
    sandbox lets Bash read but not write (under `~/.cache`, then under `~/.claude/logs`). HEAD's
    plugin failed `git status`; the checkout's ran it. Before the pass, an acknowledged commit
    was refused again by the shim, which could not mint a token, so it was a dead end (both
    locations). With the pass, under `~/.claude/logs`, a plain commit and a plain push were each
    denied with a token, then recorded once acknowledged: one commit, and the remote's `main` at
    it. An alias commit was refused with the guidance. No pass was left afterwards.
  - The pass was 9.5 s old when the commit's post-commit hook ran in that session. That is the
    sandbox and shell starting plus the shim's own inspection, which the hook's 60 s deadline
    bounds; the 120 s lifetime is that bound twice.
  - A real headless session: the session-start hook built the mirror before the first git
    command.
  - 22 checks under Apple git 2.50.1 and Homebrew git 2.56.0, with macOS `sandbox-exec` (the
    mechanism Claude Code's sandbox uses on macOS) denying writes to the gate's state:
    - The pass runs exactly its own argv, once. Unsandboxed, the shim removes it. Sandboxed, it
      stays until the PostToolUse hook removes it.
    - A pass for another argv, an expired pass, and a push that the acknowledged commit's
      pre-commit hook starts each ran nothing.
    - The alias commit was refused with the guidance, and the acknowledged push landed.
    - With no mirror for its git, `git status` ran on a mirror in a private temp directory and
      wrote nothing into the state.
    - With the pass taken out of a copy, 7 checks failed: the acknowledged commit, the commit
      with a hook and the push were refused. With the fallback taken out, 3 failed: `git
      status` was refused.
  - Not reproduced in a real session: a git that the pre-build did not cover. Claude Code's
    Bash kept the launching PATH (both found Apple's git), and the auto-mode classifier refused
    a command that put another git first.

### Setup finds a codex-oracle server registered by hand
- A `codex-oracle` server added with `claude mcp add` (the 1.x way), next to the one this
  plugin registers, gives two tool sets. `/software-workflows:setup` and the session-start
  check now look for one, user or local scope, in `.claude.json`. Only server names are read,
  never their settings. The check prints the exact `claude mcp remove <name> -s <scope>`.
- Verified 2026-10-18: on this machine the check reports OK, and reading the file takes 1 ms.
  Against a config directory holding `codex-oracle` (user) and `codex_oracle_dev` (local) it
  reports MISSING, names both, and gives `claude mcp remove codex-oracle -s user`. It runs under
  Python 3.9.6 and 3.15. The README's "Coming from 1.x" paragraph points to this check instead
  of asking the person to find the entry themselves.

### Docs: Codex writes, reviews continue their session, how to update
- The agent-teams skill has a new rule 18 (Codex writes the code; Claude reads, coordinates
  and reviews; the lead may write on paths no running writer holds) and a "Codex writers
  (abraham)" section. The section covers: charter, dispatch, the run tools, the CHANGED FILES
  block, the effective-mode line, the analysis-phase note, verification and recording by path.
  The `implementer` role now charters and dispatches a Codex writer instead of writing.
- The skill and the codex-review and codex-planning skills explain that reviews continue
  their session: one review, then short follow-ups in the same session; `session="new"` for
  unrelated work. They also point to `/software-workflows:setup`.
- The README gains an "Updating" section: turn on auto-update once under `/plugin` →
  Marketplaces, or run `claude plugin marketplace update agent-teams` and `claude plugin
  update software-workflows@agent-teams`. It covers the 1.x `renames` move and runs setup
  after updating.
- The staged "1.18 daemon" is gone from the README, the code comments and two refusals.
  abraham and the continuations on Windows now say they are unavailable there and why,
  without promising a release. The push gate's comments name the exact-argv git shim (2.0) as
  the execution boundary. The README no longer says write runs are bounded by codex's sandbox:
  infra and abraham runs have none.

## [software-workflows 1.8.1] — 2026-10-16

### No git worktrees: one writer per repository checkout (user ruling 2026-10-16)
- The agent-teams skill offered `isolation: "worktree"` as the way to run
  overlapping edits in parallel. New Critical Rule 16 forbids git worktrees
  outright: no `isolation: "worktree"` on `Agent`, no `EnterWorktree`, no
  `git worktree add`, in any prompt or script.
- Teammates who write code work in the repository's own checkout, one writer
  per checkout at a time. Parallel writers only in DIFFERENT repositories;
  overlapping work in one repository runs in sequence. A paused writer hands
  its work back as a patch file (never `git stash` / `git add -A` in a shared
  checkout); a pristine copy of a commit is a read-only `git archive` export.
- Rule 7, the `Agent` signature table, the implementation-team pattern, the
  troubleshooting entries (plus a new recovery entry for a worktree that
  appears anyway) and the pre-team checklist follow the rule.
- Why: worktrees accumulated faster than anyone cleaned them up. One workspace
  measured 201 trees, 116 of them holding uncommitted work, about 140 GB.
- codex-oracle unchanged (1.17.3).

## [1.17.3] — 2026-09-06

### Push gate: behaviourally IDENTICAL to 1.17.2 — two exemptions attempted, both withdrawn, the arc recorded
- MEASURED 2026-09-06 against the INSTALLED 1.17.2, not inferred: the shipped
  hook denies `grep -v '^\s*$'` on read-only commands. The idiom carries `$'`
  as the last character of a single-quoted word, and the round-29 rule (`$'`
  anywhere is detection, since ANSI-C quoting can build the verb: `g$'\x69t'`)
  fires on it. **That denial is NOT fixed in this release**, and the twelve
  review rounds that established why are the deliverable.
- **Five narrowings of the `$'` rule were refuted (rounds 46–51)**, each by a
  mechanism that RE-READS or EXPANDS text: a quote-state scan (2 HIGH); a
  "closing shape" rule (1 HIGH — the delimiter can be the string's FIRST
  character); a terminated-body rule (2 HIGH — reparsing changes backslash
  PARITY, three backslashes reach the inner shell as two); a parity-INDEPENDENT
  span rule (3 HIGH — `eval "g$'"''"\x69t' …"` invents no backslash, it DELETES
  the empty quotes, so the span's closing quote never reaches the inner shell);
  and a single-parse restriction (6 HIGH — a launcher denylist falls to a quoted
  `"eval"` and to `/bin/bash`, an expansion can supply a word SEPARATOR
  (`u='t pu'; g$'i'$u'sh'` is `git push`), and a delimiter stack balances on
  QUOTED delimiters). Three of those are not decidable from text, because the
  deciding content is an expansion's VALUE. Round 52 WITHDREW the exemption:
  a `$'` is detection, exactly as in 1.17.2.
- **Six rounds of ADDITIVE readings were refuted too (rounds 52–57).** To catch
  a verb the shell assembles from pieces (`git p$()ush`, `git p${x}ush`, nested
  forms — all silent on 1.17.2), two normalized copies were OR'd into detection:
  a substitution delimiter read as a space, and a substitution elided by a
  single delimiter-stack pass. Each blind review found a new read-only denial
  from them one position further along: a verb manufactured inside quoted data
  (52), a manufactured `git` word too (53), an ordinary `rg` search through the
  encoded-PowerShell branch (54), the ARGUMENT of `git grep -F 'p${x}ush'` (55),
  `grep -e git -e 'p${x}ush'` (56), and then — with the copies anchored to the
  command word — a quoted `;` that becomes a separator after quote removal, an
  empty `""` argument that vanishes, and bare `--exec-path`, which terminates
  git (57). Round 57 named the cause: the readings run AFTER normalization has
  discarded the quote structure that says which word is the COMMAND. The fix is
  to tokenize first and normalize within tokens — but a tokenizer that is both
  quote-aware and substitution-aware is a full shell lexer, which rounds 46–51
  showed cannot be written soundly here, and the quote-aware `shlex` path splits
  `p$(echo $(true))ush` on its own space and loses the nested shapes anyway.
  **Round 58 WITHDREW the readings.** A denylist of shapes recurs forever; this
  module already said so.
- **What ships in the gate:** nothing behavioural. A 300,000-command
  differential against `82fe2aa` shows 0 lost and 0 added detections, the
  parent pre-filter identical on 50,000 more, and every shape any review named
  identical. A regression pins that identity in BOTH directions by loading the
  baseline module, and lists the seven KNOWN MISSED shapes — each calibrated to
  run a real push in bash 3.2.57 AND zsh 5.9 — asserted missed on both trees,
  so the day one is caught it shows as a change. The `$'` docstring records the
  twelve rounds so a thirteenth is not attempted.
- **For the 1.18 daemon**, which decides at the execution boundary instead of
  reading text: the read-only `$'` denial; a verb split by an empty expansion
  (`git p``ush`, `git p$()ush`, `git p${x}ush`, nested); a verb completed by an
  expansion's VALUE (`git p${x:-u}sh`); a word separator supplied by an
  expansion; a command hidden inside a substitution body; a launcher assembled
  by expansion (`p${x}wsh -e …`); and, if a text-side attempt is ever made
  again, the tokenize-first design and the eleven false-positive shapes above
  as its first fixtures.

### Server: a failed tool command is context, never a run's terminal error
- Round 50 (Codex, MEDIUM, pre-existing): a non-fatal WARNING is emitted by
  the CLI as a completed `error` ITEM while the turn keeps running, and that
  item was written to `last_error` too — so a run that finished with a real
  answer collected as an error, and a warning could mask a later stderr-only
  disconnect from retry classification exactly as a failed command had. Item
  errors now land in `last_item_error` and appear as context; only
  `turn.failed` and a stream-level `error` EVENT are terminal.
- MEASURED 2026-09-06 (run codex40·31320): a detached run whose stream
  stopped mid-turn — no answer file, no error event, empty stderr — was
  collected as "ended with an error: command failed: … git check-ignore …
  → exit 1": the run's last benign non-zero command, presented as the cause
  (the "exit 1" is synthesised; a detached child's exit status is not
  observable). The same slot made the adoption completion predicate refuse
  status ok for any completed detached run that had run a failing command.
  Tool failures now land in `last_command_failure`; the structural reason
  ("ended without completing its turn; exit status not observable after
  detachment") is stated, with the last failed command as context.
- Round 46 measured a LIVE-path effect too: retry classification and the
  error rendering read the same slot, so a failed tool command preceding a
  real disconnect masked it — the run was NOT retried (class None) where a
  disconnect earns one. Round 47 (Codex, MEDIUM) showed the first pin was
  vacuous: the fake's disconnect emitted JSONL `error`/`turn.failed` events,
  which overwrite the slot on the baseline too, so the test passed on both
  trees. The masked shape is a disconnect that reaches STDERR ONLY (the
  stream stops with no terminal event); `FAKE_CODEX_FAIL_STDERR_ONLY` models
  it, and the test now fails on 82fe2aa (one attempt, class None) and passes
  here (classified and retried) — `tests/check_retry_pin_sensitivity.py`
  measures both trees.

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
