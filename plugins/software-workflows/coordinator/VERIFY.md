# C1 rework — fresh operational verification, 2026-10-18

This replaces the previous verification record. **The eleven findings have implementation changes and direct operational evidence below. This is not an unconditional all-green release certificate.** The remaining coverage gaps and the observed process-attribution limitation are explicitly marked below.

The observations were collected during this implementation, in new disposable repositories and fixture homes beneath `/private/tmp/c1-rework-3gcqdxau`, from 2026-10-18 12:11:47 UTC through cleanup at 13:02:24 UTC. They are not reused results from the earlier C1 implementation. Intermediate failures are retained. Final-source acceptance is identified separately from runs made before the last process-tracking changes.

All repository source/documentation writes were confined to `plugins/software-workflows/coordinator/`. Operational state, scratch Git repositories, native fixture homes and evidence are outside the checkout. No test files or test harness were added. The commands below are the actual inline operational invocations, not a reusable test suite. Git publication and crash experiments ran only against disposable scratch repositories. The caller's checkout was not used for publication or staging.

## How to read this record

Each `Cnn` link identifies a **complete command and its observed exit code/output** in the appendix. Commands run from `/Users/ahrar/Documents/agent-teams-plugin`; inline commands contain their concrete paths and dependency files. They are an execution record, not a script to rerun blindly against the retained state. Some commands ran concurrently; the appendix follows capture order, not a promise of strictly sequential execution.

A Python observation command can exit **0** after recording an expected RPC refusal or an unsuccessful behavioral observation. That is not evidence that the RPC or behavior passed. The tables state the actual RPC error and outcome. Native commands have their own `exitCode` or `exit_code`. A worker deliberately killed by SIGKILL has no normal process exit; the interrupted client recorded `RPC_UNCERTAIN` where shown. An exit of 0 for the enclosing observer means the observation completed.

Captured terminal output was sometimes truncated by the tool. Those markers remain in the appendix. The corresponding JSON artifacts contain the full observations and are indexed below with size, SHA-256 and file timestamp. The timestamp is identified as the artifact's mtime where an invocation did not record its own start/end time. No missing command start time is reconstructed from a PID birth or a file timestamp.

The raw capture is [operational-transcript.json](/private/tmp/c1-rework-3gcqdxau/operational-transcript.json). The source fingerprint, per-file hashes, artifact inventory and complete invocations below make the report inspectable without trusting a success summary.

## Host and final source

| Item | Observed value |
|---|---|
| Host | macOS 26.6.2, arm64 |
| Daemon interpreter | `/opt/homebrew/opt/python@3.15/bin/python3.15`, Python 3.15.0 |
| Client/hook interpreter | `/usr/bin/python3`, Apple Python 3.9.6 |
| Linked SQLite | 3.54.0 |
| Git | 2.56.0 |
| Native Codex | codex-cli 0.163.0 |
| Native binary | `/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex` |
| Native SHA-256 | `8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31` |
| Final adapter digest | `671e505d610894921d62236357b007501c0550561200a8e52e0ba8b620c4ff03` |
| Fingerprint captured | 2026-10-18 13:00:23 UTC; [C70](#c70) |

The adapter digest includes all 19 Python source files. The final all-probe admission, both final fresh writers, service-lifetime acceptance, survivor acceptance, lead crash gap, listener death and Python 3.9 client observations used this final source. Earlier publication/custody/message observations are identified by their command and timestamp; they were not all rerun after the final daemon/helper process-race correction. README/VERIFY changes do not affect the adapter digest.

The coordinator has no existing build or linter configuration. [C70](#c70) compiled all 19 Python files in memory and parsed them using Python 3.11 grammar: exit **0**. [C71](#c71) connected the real Apple Python 3.9 client through the new endpoint manifest and obtained `ready:true` and zero lock counts: exit **0**. [C72](#c72) ran the hook's `--check`: exit **0**, output `{"compatible": true, "protocol": "jsonl-v1"}`. [C73](#c73) ran the CLI's `--help`: exit **0**. Actual Python 3.11 execution is **UNVERIFIED** because that interpreter is unavailable on this host; grammar compatibility is not runtime evidence.

Final direct file verification [C77](#c77), at 13:12:18 UTC, exited **0**: all **21** coordinator files had no trailing whitespace or missing final newline, all **19** Python files compiled and passed Python 3.11 grammar parsing, all source hashes still matched final acceptance, and the report had no missing command anchors or evidence artifacts. This check includes untracked files, which an ordinary `git diff --check` would omit.

## Socket binding, admission and fresh writers — finding 5

The final short state root was `/private/tmp/c1-rework-3gcqdxau/release-one1` (**44 encoded bytes**, matching the stated default-root length on this host). The final long state root was `/private/tmp/c1-rework-3gcqdxau/release-long-rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr/state` (**161 bytes**). Final long admission used the separate scratch path visible in [C63](#c63), also longer than the measured socket limit. No user state directory was created or read for these fixture runs.

[C74](#c74) made actual disposable `AF_UNIX` binds. A **103-byte** pathname bound successfully; **104 bytes** failed with `AF_UNIX path too long`. The runtime precheck returned `SOCKET_PATH_LENGTH` with the exact path, `bytes:104`, and `measured_limit:103`. This is bind evidence, not merely a failed connection experiment.

| Endpoint observation | Requested path | Requested / physical bytes | Physical path |
|---|---|---|---|
| Short RPC, restart generation 2 [C61](#c61) | `/private/tmp/c1-501/sxc4p_sjy/s` | 31 / 31 | same as requested |
| Long RPC, restart generation 2 [C61](#c61) | `/private/tmp/c1-501/s2ma36v0h/s` | 31 / 31 | same as requested |
| Short admission fingerprint [C62](#c62) | `/private/tmp/c1-501/stb2est4e/s` | 31 / 94 | `/private/tmp/codex-daemon-501/4d6541cb9a478f080301d59dec589fe0dae443755df0225d6ed4e35fd25caec7` |
| Long admission fingerprint [C63](#c63) | `/private/tmp/c1-501/sm2kr8uqq/s` | 31 / 94 | `/private/tmp/codex-daemon-501/e2449e710c9038450c50d5dc8fe64a96fc24f75ef68bb24dc0ec8d27544274ee` |
| Short production writer [C65](#c65) | `/private/tmp/c1-501/s_eq3iece/s` | 31 / 94 | `/private/tmp/codex-daemon-501/994f4d93bcc772a96200d898d3f2c3d74229ac7e5587277663a638ee296d1751` |
| Long production writer [C65](#c65) | `/private/tmp/c1-501/scc8l1v6t/s` | 31 / 94 | `/private/tmp/codex-daemon-501/13a5ded1bdebd37864e984882e0ca34ea54822b29863ceeb5cfb386c9e512aef` |
| Python 3.9 client after later worker restart [C71](#c71) | `/private/tmp/c1-501/sc_wr7y0e/s` | 31 / 31 | same as requested |

The endpoint evidence reports UID **501**, socket mode **0600**, and measured capacity **103**. Private runtime allocation directories use **0700** and carry the ownership token plus directory device/inode. Bootstrap discovered the replacement manifest after both final workers were killed and restarted ([C61](#c61), exit 0). Old per-incarnation paths were not reused as authority for the replacement worker.

Final standalone admission [C62](#c62) and [C63](#c63) each exited **0** with all ten `passed:true`: `hooks`, `evidence`, `dynamic`, `delivery`, `tty`, `reconnect`, `terminals`, `policy`, `validation`, `exec_policy`. The existing red-before-green observations are in their `cases[].observations`; neither old cached acceptance nor an external model generated these observations. Both report `completed:true` and **`production_admitted:false`**, as expected for standalone admission. That field is not a fresh-writer result. The respective recorded intervals were 12:55:19.989–12:55:39.478 UTC and 12:55:21.142–12:55:41.248 UTC.

[C65](#c65) separately started **fresh native production writers** in both state roots, with automatic admission bound to the final adapter digest. Both returned effective `approvalPolicy:"never"` and `sandbox.type:"dangerFullAccess"`; both reached completion. Final short listener PID/birth: `21195 / 1792328169:179042`; long: `21194 / 1792328169:179041`. The earlier initial short/long admission and writer observations are preserved as [C04](#c04), [C05](#c05), [C09](#c09), but are not substituted for these final runs.

[C58](#c58) deliberately created an unsafe **disposable** `listeners` directory with mode 0755. Actual `daemon start` exited **3**, returning `STATE_PERMISSIONS`, `Unsafe state subdirectory`, `name:"listeners"` in about **0.226 seconds**. `startup-error.json` retained the same specific error instead of losing it as a generic `DAEMON_START` timeout.

**Correction to the rework premise:** Codex 0.163.0 redirects native requested socket paths through a symlink to a hashed physical pathname. Its 94-byte physical sockets bound here. The old quoted 108/143-byte requested native names alone therefore do not establish that native binding was impossible. The coordinator's direct state-root RPC socket was the actual length defect. The rework consistently uses short allocations for RPC, native writers, fingerprint listeners and probes, and distinguishes requested from resolved paths. The previous VERIFY record is historical and is not used as evidence here.

## Registration, complete scope and custody

| Finding / requirement | Actual observation and command | Outcome |
|---|---|---|
| 3 — concurrent registration | [C06](#c06): 24 concurrent canonical and aliased registrations of `core-repo` | One ID, `checkout_dcf42099445a4e3dacd7bb25cae8b24b`; all ready. Claim through another returned handle refused `CLAIM_CONFLICT`. Another state root refused `CHECKOUT_OWNED`. Observer exit 0. |
| 1 — complete scope at seal | [C10](#c10): verified changes to `a.txt` and `b.txt`; externally changed one before sealing | `UNKNOWN_BLOCKS_SEAL`; details still list **both** paths; both claims remain held. No silent path omission. |
| 1 — interference after seal | [C11](#c11): external change after sealing the two-file unit | `UNKNOWN_BLOCKS_PUBLICATION`; frozen scope was not reduced to the remaining unit-owned head. |
| 7 — fresh unit base | [C11](#c11): publish A, then create B editing A's path | A completed at `03b52828808b0bb5dbf29d91b4ab6fdccf713b2e`. B recorded that exact new tip as its base and successfully sealed a new verified edit of `a.txt`. |
| 10 — shared/shared and patch coexistence | [C16](#c16): two declared commands hold shared custody while a disjoint attributable edit completes | Both commands coexist; edit receives exact verified receipt; both commands exit 0 and release custody; no UNKNOWN. |
| 10 — shared/exclusive ordering | [C16](#c16): request exclusive while shared holders run | Exclusive waits and proceeds after shared release. It does not invalidate the already admitted patch. |
| 10 — exclusive exclusions, both orders | [C16](#c16): existing other-unit claim is excluded; edit completes during exclusive custody; reverse-order prepared edit also completes | Exact receipts remain verified. A new claim inside exclusive scope refuses `CLAIM_CONFLICT`. No unexplained effects were adopted. |
| Native parallel roots | [C39](#c39) | Observed two active roots and two simultaneous shared reservations; two native patches verified; cross-unit claim conflict refused; final UNKNOWN list empty. |
| Native child attribution | [C43](#c43) | Child thread's patch verified and command reconciled under the child actor, with listener/thread/call binding. Custody released; UNKNOWN empty. |

The normal core publication also preserved unrelated conflict index stages **1, 2 and 3** exactly ([C11](#c11)). The index was not replaced with the sealed candidate's unrelated entries.

### Command mediation — finding 2

[C44](#c44), repeated through the real native hook path, exited **0** as an observer. Sixteen dangerous or ambiguous forms produced durable `decision:"deny"`, `state:"denied"`, `refusal:"LEAD_RECORDS"` with **“the lead records”** before native execution:

- `git commit`, `git push; true`, and `git tag` followed by a newline and `true`;
- `git reset | cat`, `true && git checkout a.txt`, `false || git restore a.txt`;
- `'git' 'commit'; true`;
- `sh -c 'git push; true'` and `bash -lc 'git tag; true'`;
- `env FOO=value command git -C . commit`;
- `git -c alias.record=commit record`, `git made-up-subcommand`, `git --git-dir=.git status`;
- `$(printf git) push`, `printf '%s' "$(git push)"`, and malformed `git push 'unterminated`.

These strings were offered to the hook for rejection, not run as Git operations against the working checkout. Two complete actor-bound declarations did execute: `printf '%s' 'git push'` printed literal text, and `git --no-pager status --short` inspected scratch status. Both obtained **shared** custody, reconciled, released custody and created no UNKNOWN. The literal argument was not mistaken for a Git executable. This does not turn arbitrary shell text into a fixed reader or establish command authorship.

## Exact crash windows and publication recovery — findings 4, 8 and 9

The crash observers watched durable SQLite records and actual filesystem state. They verified the worker PID **and birth identity**, stopped it with SIGSTOP, reread the named checkpoint where necessary, and then sent SIGKILL. A timing delay alone was not used to claim a particular window. All five enclosing observations below exited **0**. Interrupted live publication/seal requests reported `RPC_UNCERTAIN`; the later durable result was inspected independently.

| Command / checkpoint UTC | Worker PID / birth / generation | Observed before SIGKILL | Observed after recovery |
|---|---|---|---|
| [C18](#c18), 12:21:25.222 | `30833 / 1792326044:690091 / 1` | Unit `sealing`; durable seal attempt `started`, generation and intended base recorded | Generation 2: unit **open**, `review_required:true`; attempt **interrupted**; claim held. Same RPC key remained `RPC_UNCERTAIN`. A newly requested seal succeeded. |
| [C20](#c20), 12:23:22.517 | `33264 / 1792326085:477693 / 2` | Publication `index_pending`; **actual** `index.lock` hardlinked to recorded anchor: device `16777230`, inode `748326603`. Caught after link, before the later held-state checkpoint | Generation 3 completed the same commit `0749ebf6b983c1f7d324dc4fc145486909fa6b14`; total commit count 2; anchor/lock gone; claim released; unrelated staged `file09999.txt` preserved. |
| [C25](#c25), checkpoint in `branch-checkpoint.json` | `34893 / 1792326202:781846 / 3` | Ref advanced; publication pending. A separate real `git update-index --index-info` writer owned `index.lock`; symbolic HEAD had been changed to another scratch branch using Git plumbing | Generation 4 returned **BRANCH_CHANGED**, retained pending journal/claims, left other branch's index digest unchanged, and preserved foreign lock inode/bytes. Closing the foreign writer's input produced exit 0. Restoring symbolic HEAD allowed the same publication to finish. |
| [C31](#c31), 12:29:17.962 | `41665 / 1792326339:360422 / 4` | Phase `index_pending`, ownership state `replacement_ready`, **actual index inode equaled anchor inode `748475804`**, `index.lock` absent: replacement had happened before its durable completion checkpoint | Generation 5 recognized the installed inode/digest and completed the same commit `68bc0e100dfca7f33c8299ac86b8de42c55815d0`, total commit count 4, without another commit or replacement. |
| [C53](#c53), 12:47:21.204 | `53193 / 1792326558:218234 / 5` | Phase `commit_created`; HEAD still the previous `68bc0e100dfca7f33c8299ac86b8de42c55815d0` | Generation 6 completed exactly the recorded `2bf18ade66cc17e6664f3f8eb12dbb25cdbec40b`; total commit count 5. |

The foreign Git writer in [C25](#c25) was PID `41124`, birth `1792326335:370088`, with lock inode `748371102`. Its lock was not removed by age, PID comparison or coordinator restart. This is a separate control, not a substitute for the coordinator-owned lock SIGKILL in C20.

During [C31](#c31), another process performed **217 writes** to unrelated `file09998.txt`. Those unexplained effects remained UNKNOWN on that path; the reviewed unit's publication still completed. The scoped claim was released only after publication completion. Its prepared reference-verification transaction protected HEAD while the owned index lock covered replacement.

The first owned-lock observation attempt [C21](#c21) exited **1** at `REVIEW_INCOMPLETE` before publication because the supplied review lacked the completed review contract. It is not a crash-window success. C20 is the corrected complete review/publish invocation that actually caught the owned lock. The capture order of these two appendix entries does not reverse their causal relationship.

### Frozen review and UNKNOWN refusals

[C41](#c41) recorded `NOT_FOUND` for a missing review and `REVIEW_BINDING` for a mismatched frozen binding. [C47](#c47) supplied a valid object review, advanced the scratch branch parent, and observed **STALE_BASE**. It then damaged a disposable receipt blob and observed **BLOB_CORRUPT**, with claims retained; the original blob bytes were restored afterward. None of these errors was counted as a publication success.

The binding continues to contain exactly `repository`, `unit`, `base`, `candidate`, `seal`, `provenance`, and `validation_digest`; the actual object diffs and bindings appear in C10/C18/C41/C47. [C38](#c38) and [C41](#c41) recorded that restoration alone did not erase UNKNOWN authority: sealing still refused until a new verified edit established a new receipt. The first restoration attempt [C35](#c35), with an empty full-checkout revision map, exited **1** (`RESTORE_FULL_SCOPE`). C38 supplied the explicit complete known revisions and succeeded.

## Process ownership and persistent services — finding 6

The process code tracks command-specific native thread/session/tool-call markers or native terminal **OS PIDs**, always with PID/birth and ancestry checks. Opaque native `processId` values are not interpreted as OS PIDs. Observed descendants remain associated after reparenting. Listener services and birth-verified coordinator hook helpers are tracked separately.

The first service follow-up in the **same paid two-root scenario** [C48](#c48) failed its intended behavior: a lazily created native code-mode host was not in the initial service snapshot, so the command quarantined. After adding a listener-incarnation service boundary before its first command, [C56](#c56) exposed a second race: a post-hook OS process could appear in a scan just before its verified hook RPC registered it. That attempt also quarantined. These are real intermediate failures, even though their observer processes exited 0.

The final fix allows a short retry only when every ambiguous survivor is subsequently attested as the exact coordinator hook PID/birth through the owning native listener. Elapsed time or process disappearance alone does not grant an exemption. Newly seen arbitrary services after command activity remain ambiguous; executable name is not authority.

Final-source [C66](#c66) ran a normal-config native command on existing paid root B. It observed **13 recorded services alive before and the same 13 PID/birth identities alive after**, while the command's own PID `30420`, birth `1792328355:50534`, completed and its shared custody **released**. There were **zero new UNKNOWNs**. Two UNKNOWNs from the earlier failed observations were intentionally retained; the paid checkout was not represented as globally clean after those failures. [C59](#c59) records explicit reconciliation of the earlier ambiguous process before acceptance.

Separately, final-source [C67](#c67) started a fixture command with a real child that outlived the parent and turn. Root PID/birth `30361 / 1792328349:847976`, child `30362 / 1792328349:866911`. The child was observed reparented to **PPID 1**. Custody remained held after the turn ended and released only after the child exited and its own terminal inventory was empty. Final UNKNOWN list: empty. Native terminal IDs remained recorded history, not living OS processes.

**Observed limitation:** native `/bin/sleep` on this host did not expose the native environment markers to process inspection, and the corresponding terminal had `osPid:null`. Intermediate [C22](#c22), [C28](#c28), [C30](#c30), [C32](#c32) and their process-diagnosis artifacts observed `PROCESS_AMBIGUOUS`, quarantine and UNKNOWN rather than reliable attribution. Positive survivor verification therefore used actual Homebrew Python child processes whose markers were inspectable. This is a conservative refusal, not a claim that every macOS command can be tracked. Extending attribution without stronger native OS-PID evidence remains **UNVERIFIED**.

## Messages, gaps and listener loss

[C39](#c39) demonstrated parallel roots, a claim conflict and message entry/acknowledgement. [C46](#c46) distinguished queue submission/context entry from acknowledgement: an idle queued message initially entered context; an explicit later resume acknowledged it. A separate `wake:none` message was durably **persisted** when worker `62995`, birth `1792327038:406302`, generation 2 was killed at 12:45:33.233 UTC. After generation 3 recovery, explicit wake delivered and acknowledged that same message with **one delivery attempt**. Inbox reading remained observational. Lead acknowledgement returned `duplicate:false`, then `duplicate:true`; a forged writer RPC acknowledgement returned `NATIVE_BOUND`. All five lock counts returned to zero and UNKNOWN remained empty.

Final [C65](#c65) observed running **`turn/steer`** delivery and `entered_context`. It did **not** observe an acknowledgement for that particular message. The steer preempted the fixture's first command response, so that invocation had no command custody and `held_after_turn:false`; it is **not** used as evidence for command-survivor behavior. C67 provides that separate evidence. Child patch/command attribution is demonstrated, but a fresh child message relay/fallback-ack scenario is **UNVERIFIED**.

Final [C68](#c68) interrupted a lead edit after durable `operation/prepare` and real byte installation but **before** completion. It killed birth-verified worker `14368 / 1792328105:655801`. Recovery retained the bytes without replay/adoption, quarantined the operation, kept two UNKNOWN records and the claim, and refused sealing with `UNVERIFIED_OPERATION`. Matching intended bytes alone did not become a receipt.

Final [C69](#c69) killed idle native listener `21194 / 1792328169:179041`. Recovery cold-resumed its thread into a new listener, left the actor without an active turn, retained a checkout-wide native-notification-gap UNKNOWN and the existing claim, and refused `run/resume` with `RECOVERY_UNKNOWN`. Listener replacement was not treated as proof that no effects were missed.

## RPC lock lifetime — finding 11

[C23](#c23) sent **1,500 distinct keyed requests, 1,500 truly unkeyed `Client.call()` requests, and 100 concurrent callers sharing a key**. The batch took **0.699148 seconds**. CLI traffic alone would not cover unkeyed calls because the CLI supplies keys; this invocation used the client directly.

Before and after: `actor:0, checkout:0, message:0, rpc:0, unit:0`. A separate client timed out at 0.03 seconds while an actual one-second lead command continued. Repeating its key returned the recorded successful result for `op_a87711c41f23484183ec6bd96b56e954`; the ledger contained **one** matching operation, native command exit 0 and custody released. This establishes disconnected-client/idempotency behavior, not an injected internal asyncio cancellation. Forced coroutine cancellation is **UNVERIFIED**.

## The one paid scenario

[C45](#c45) started exactly **two `gpt-6-astra` roots** using the user's normal Codex configuration **through Codex itself**, in `/private/tmp/c1-rework-3gcqdxau/paid-repo`. No tooling read the user's CODEX_HOME files or credentials. All other model-generated operational plans used the existing fixture provider with disposable homes.

The two roots were `run_5fd2e92ba56d473d9d6f410dc71afd86` and `run_1c455649ba9d46b482b07d5140390dae`. They produced exact verified native patch receipts for `paid_a.py` (`add_one`) and `paid_b.py` (`double`). Root A sent root B message `msg_7ce8c84198934c7db566d1b3237e7f9a`, which reached **acknowledged** through native-bound tools. Both policy echoes were never/full-access. The original edit/message phase ran 12:44:23.130–12:45:36.673 UTC and ended with no UNKNOWN.

The service-lifetime investigation used three follow-up command observations on **these same roots**, including additional paid model turns; it did not create another paid pair/scenario. Their failures and final success are disclosed above. The final paid service observation ran 12:58:48.207–12:59:43.234 UTC. The final source was not retroactively assigned to the earlier paid edit phase.

## Performance observations

These are measured values, not invented pass thresholds or benchmarks of production capacity.

| Measurement | Observation | Command |
|---|---|---|
| Native hook round trips, final source | 110 actual pre/post hook durations; median **42 ms**, p95 **49 ms**; raw durations retained | [C65](#c65) |
| Baseline of 10,000-file scratch repository | **2.496449 seconds** | [C17](#c17) |
| Full reconciliation, same corpus | 10,000 files, **1.754477 seconds**, no new UNKNOWN | [C17](#c17) |
| Scoped reconciliation | 1 file, **0.000864291 seconds**, no new UNKNOWN | [C17](#c17) |
| Final fresh writer RSS | Short listener **114,416 KiB**; long listener **101,040 KiB** | [C65](#c65) |
| RPC-batch worker RSS | **43,424 KiB** after the request batch | [C23](#c23) |

RSS is a point-in-time `ps` observation of the named process, not an aggregate of all MCP descendants. Normal-config paid writers carry additional services; fixture RSS should not be extrapolated to them. Earlier hook measurements remain in C26/C40 but are superseded for the final-source numbers by C65.

## What remains UNVERIFIED

- **Python 3.11 runtime and Linux runtime:** only macOS Python 3.15 daemon execution, Python 3.11 grammar, and Apple Python 3.9 client/hook execution were available.
- **Legacy migration fixtures:** actual duplicate-checkout database migration and legacy `sealing` units lacking attempt records were not separately seeded. The refusal/recovery code is present; current-schema registration and journaled seal recovery are demonstrated.
- **Existing incomplete legacy seal with an omitted path:** new operation-derived scope refuses the live two-file failure, but a pre-rework partial seal was not seeded and replayed.
- **Additional seal-attempt history cases:** killing an initial sealing attempt was observed; a prior reviewed seal invalidated by an interrupted replacement attempt was not separately exercised.
- **Child message relay/fallback acknowledgement:** native child mutation and command evidence were observed, not that separate message route.
- **Production-native missed-hook injection:** admission's red hook case and the durable lead completion crash gap were observed; a deliberately disabled live production-writer hook was not separately injected.
- **Internal coroutine cancellation:** disconnected client and concurrent same-key calls were observed; an internal task cancellation was not injected.
- **All possible filesystem/power-loss windows:** the exact SIGKILL checkpoints named above were observed. Power loss, fsync failure, the tiny interval before recording a newly created anchor, and every possible ref/index race interleaving were not simulated.
- **Cross-user/tampered endpoint artifacts:** actual length overflow and unsafe private-directory permissions were exercised. A hostile cross-user runtime replacement was not attempted.
- **Every shell grammar or alias form:** the enumerated real-hook forms were observed. This conservative analyzer is not a general shell interpreter or OS security boundary.
- **Uninspectable native processes:** the `/bin/sleep` case remains quarantined, as described above. No name-based exemption was added to turn that refusal into a success.

These gaps prevent treating this document as complete coverage of every original acceptance flow or every platform. They do not turn observed refusals into successful executions. The lead retains the release decision; this run did not change other release paths or implement the fallback release choice.

## Cleanup and deviations from the brief

[C75](#c75) gracefully stopped all **seven** disposable daemon states after explicitly reconciling ended command custody. Every response was `stopping:true` and each verified worker stopped. [C76](#c76) stopped the **four** owned fixture provider processes only after checking their birth identities; all four serving commands eventually exited **0**. Scratch evidence and unresolved UNKNOWN/claim history were retained. Native/MCP services used in the lifetime comparison were still alive at that comparison; later orderly cleanup is a separate event.

The architecture and required ordering were retained. Two evidence-driven refinements were necessary: (1) native requested-path length was distinguished from Codex's resolved hashed physical socket, correcting the review's overly broad path assumption; (2) real normal-config service/helper observations required the additional listener service boundary and attested-hook race handling described above. No arbitrary sleep/name exception, blanket hook trust, weakened UNKNOWN rule, alternate delivery state machine, test harness or other-package change was introduced.

## Final Python source hashes

| File | SHA-256 |
|---|---|
| `__init__.py` | `42b1472bf64dff35243cbdc7d7c58ea01d546e12c7ea3d499d19feb69d85144d` |
| `__main__.py` | `1e0b39642b662e165fba55bc0d797b5cbf1cb08f9541c3ec3c0526536565785f` |
| `admission.py` | `8c668cf1a359700ce89f71a483fb5fd70e071718c35372f6fb004d33c7c0d11b` |
| `client.py` | `443b7363bd93d7a79f36fc636741b2b0c06610af9bbce818aaaa100a59ccd6ec` |
| `custody.py` | `d1fa40948f9654283259ba2063a8cd71fa2996975bda90f68bcf5e8258f80168` |
| `daemon.py` | `96c221675353801d3f2fdd07e052b0f869784945c9d873ddb09de21ebb654111` |
| `fixture.py` | `e13148742316639fcf19a930a41e0953ac68b87edd6e644e60bc41ebd5389d9b` |
| `gitio.py` | `9632814c06e797beee089e44a22d5a7ef86c096cf339ad627bab9e8e26318407` |
| `hooks.py` | `8368fed7db67a1f38a9875ee08a3db48f30f6b4aa3f501ee56925435b3a561a9` |
| `ledger.py` | `8ec6a11a319b3907761c47cb7e31d027761994105e78fbfa7b239f7019924e87` |
| `locks.py` | `dfbd910bd9fde55636c6bd11baf29b2c097eb3c856b8231973a2bb305b8aff7f` |
| `messages.py` | `d54ed33ad13bd30161ce506913d4a31d99fa6c4d9109e94e9e148460bf1c31a8` |
| `native.py` | `9f8afa7199d9b7c0c9d90cf61e333000a73801cdc5e60aa62feed2e55dea654a` |
| `processes.py` | `19343e95c5ec72044af38193c6a97836dadd6f25219fb8be4a6c69fbf0efef9d` |
| `publish.py` | `e494c10e94e09a5d61f51d01afd219b67152b7ae8d26f31a613a510c0b8dc19a` |
| `recovery.py` | `978e10e1f90d388da8a44f38608cf03a4140c1fab7096dfb889a94b92b75043e` |
| `runtime.py` | `c623059f191f8623d09cec3838306a7827854264df344c742247605bb5c689cb` |
| `store.py` | `7430209231c6096e864211bb53557b0fb01e1c068725d7a3947e9f7ed6faea4c` |
| `tools.py` | `d88ae85ea2ec3c0d72ffcea78c6f96afae986da3c026b3296b8e75ef840776df` |

## Evidence artifact inventory

These are newly generated observation artifacts in the disposable root, not user configuration. `mtime UTC` is the artifact write time; embedded operation/checkpoint times remain authoritative.

| Artifact | Bytes | SHA-256 | mtime UTC |
|---|---:|---|---|
| [acceptance-long.json](/private/tmp/c1-rework-3gcqdxau/acceptance-long.json) | 44069 | `9cd9c3326bf494adac60ce17f9f9125966546092bee762413ebccab6477ed517` | 2026-10-18T12:55:41.248+00:00 |
| [acceptance-short.json](/private/tmp/c1-rework-3gcqdxau/acceptance-short.json) | 43237 | `a280592ee414fa429d8a0d7f95d465b492e42777d7a7521acd978f7db4dbc1b8` | 2026-10-18T12:55:39.477+00:00 |
| [acceptance-survivor.json](/private/tmp/c1-rework-3gcqdxau/acceptance-survivor.json) | 213875 | `ab5f7594be15c91332bba2b4ce742c597037a4945ed6e19e4c33e46d146e4aa0` | 2026-10-18T12:59:22.560+00:00 |
| [acceptance-writers.json](/private/tmp/c1-rework-3gcqdxau/acceptance-writers.json) | 200195 | `224f01ee9510fefed3efe3e429579333c68c76e8536fd84d8b600abce03fe318` | 2026-10-18T12:57:14.674+00:00 |
| [admission-long.json](/private/tmp/c1-rework-3gcqdxau/admission-long.json) | 44003 | `8455e092bc212721cf92664259803dfb9c41ab7dfef333d43062bc7f596b25bd` | 2026-10-18T12:12:18.779+00:00 |
| [admission-short.json](/private/tmp/c1-rework-3gcqdxau/admission-short.json) | 43178 | `b77d3017f86ac39194fee6aea62a5e7f18cc6a4dcd028dc6ebb64b2613062fb3` | 2026-10-18T12:12:18.779+00:00 |
| [branch-checkpoint.json](/private/tmp/c1-rework-3gcqdxau/branch-checkpoint.json) | 2249 | `6bdde5c808fa7135e24943dff4aa8da983f7a7882f47e0e6a31d31f359436c9b` | 2026-10-18T12:25:39.102+00:00 |
| [branch-recovery.json](/private/tmp/c1-rework-3gcqdxau/branch-recovery.json) | 4824 | `b9d5f577442b1fdf843b2d99414053d00a4dff94d30301ab702fccbfec2f6149` | 2026-10-18T12:25:43.114+00:00 |
| [branch-setup.json](/private/tmp/c1-rework-3gcqdxau/branch-setup.json) | 3757 | `bd09a7fae39a000a6ff4577943b3a01f4b50b199faee87f329103e627613df76` | 2026-10-18T12:24:46.810+00:00 |
| [classification.json](/private/tmp/c1-rework-3gcqdxau/classification.json) | 41299 | `9ea5a568c1cd9e8d59d0f02666fdfef8de5434d1a3dfb92724b003dcc545aa9b` | 2026-10-18T12:15:24.541+00:00 |
| [cleanup-daemons.json](/private/tmp/c1-rework-3gcqdxau/cleanup-daemons.json) | 2573 | `52f758099dcc2ce92f7fd9f50b2b1b6bcbe84dc8af8935165e60e4cb8aa53648` | 2026-10-18T13:02:07.439+00:00 |
| [cleanup-providers.json](/private/tmp/c1-rework-3gcqdxau/cleanup-providers.json) | 590 | `31813082ae41472ea1c0149565bb6dc9d06988feef7bf4a918330b17ee2f6dc7` | 2026-10-18T13:02:24.747+00:00 |
| [commit-created-checkpoint.json](/private/tmp/c1-rework-3gcqdxau/commit-created-checkpoint.json) | 1168 | `ceaa935cee70e2f0355b49a2b866e8d7b6aeaf06e0e872a437d832501ed3a2cd` | 2026-10-18T12:47:21.208+00:00 |
| [commit-created-recovery.json](/private/tmp/c1-rework-3gcqdxau/commit-created-recovery.json) | 3307 | `80e3d73559f96762b2b0ae4c7fe36a92e588b9b1892b1effa88b69dca86292b6` | 2026-10-18T12:47:26.756+00:00 |
| [commit-created-setup.json](/private/tmp/c1-rework-3gcqdxau/commit-created-setup.json) | 3768 | `95e72136810c6b0d0fc3abc76218b630e151c44b32d6dbe3c9b16e7a51029a95` | 2026-10-18T12:46:49.385+00:00 |
| [custody.json](/private/tmp/c1-rework-3gcqdxau/custody.json) | 16718 | `5dea6dfe576cb0e40586cec993b407c8f622d9381eeba6172dc9b71e13373e77` | 2026-10-18T12:20:35.260+00:00 |
| [environment.json](/private/tmp/c1-rework-3gcqdxau/environment.json) | 947 | `c9261aaac95415c64e7331b755e74b391e5fc857dde76268d62d35cfe287556b` | 2026-10-18T12:11:47.858+00:00 |
| [final-admission-long.json](/private/tmp/c1-rework-3gcqdxau/final-admission-long.json) | 44110 | `ed3d335c6d14376250e9f5f0deb41c5a35bb7f7a216fc5dd999ed21ac3d260dd` | 2026-10-18T12:38:16.995+00:00 |
| [final-admission-short.json](/private/tmp/c1-rework-3gcqdxau/final-admission-short.json) | 43278 | `7df68272c6eaa7a3e21f02e3ad750575d681d582196ee3bcc1159bc802256262` | 2026-10-18T12:38:16.995+00:00 |
| [final-child.json](/private/tmp/c1-rework-3gcqdxau/final-child.json) | 10854 | `197dd3f7f589fd0bbc9725ffb9d48a5701b18efd32bd82aed2d73d1d7f17ee5b` | 2026-10-18T12:43:50.312+00:00 |
| [final-classification.json](/private/tmp/c1-rework-3gcqdxau/final-classification.json) | 41670 | `9b9a16e661d30208f6f68a5a10abfaa4408167d07915862d82546804a7adf4a1` | 2026-10-18T12:43:52.244+00:00 |
| [final-command-process.json](/private/tmp/c1-rework-3gcqdxau/final-command-process.json) | 178468 | `f4cd541b539cbfcb30d308cb1c60dc61c44bef4ab40c10b7022b7a46b6415bd5` | 2026-10-18T12:33:54.397+00:00 |
| [final-fingerprints.json](/private/tmp/c1-rework-3gcqdxau/final-fingerprints.json) | 2283 | `d767a552e0e72b09820211c4a9acd78856eaf879ae311b5f976a4e31a47dd89a` | 2026-10-18T13:00:24.034+00:00 |
| [final-hook-performance.json](/private/tmp/c1-rework-3gcqdxau/final-hook-performance.json) | 2786 | `bb06fb876c83d0011596cdd9355ff2de23c6ecd2b2522b8057dade887d15da32` | 2026-10-18T12:38:49.320+00:00 |
| [final-long-service-restart.json](/private/tmp/c1-rework-3gcqdxau/final-long-service-restart.json) | 578 | `e01b0a5adaf8fce3f34961b793bfefb89e8b908901707af9875bf0265619a148` | 2026-10-18T12:51:53.849+00:00 |
| [final-message-recovery.json](/private/tmp/c1-rework-3gcqdxau/final-message-recovery.json) | 27507 | `3cbaa25b63642406840c4495ce4537d06727fc2c93f549217f078ca3d49e03a9` | 2026-10-18T12:45:34.392+00:00 |
| [final-parallel-and-survivors.json](/private/tmp/c1-rework-3gcqdxau/final-parallel-and-survivors.json) | 431036 | `437acd72f44ca439073fa922a0e5ae4b3611323b94db7e0d793a01327fac5935` | 2026-10-18T12:40:02.534+00:00 |
| [final-restoration.json](/private/tmp/c1-rework-3gcqdxau/final-restoration.json) | 665 | `da38c86216dc16963c12830ef353f0d803e5795dd1d5842b728ed113c6f6d055` | 2026-10-18T12:38:14.772+00:00 |
| [final-service-restart.json](/private/tmp/c1-rework-3gcqdxau/final-service-restart.json) | 578 | `50b77139cc4522ccfc46113ec3f3f02b4d9a03342dd35871325cc3bff78a7882` | 2026-10-18T12:50:43.742+00:00 |
| [final-setup.json](/private/tmp/c1-rework-3gcqdxau/final-setup.json) | 1947 | `ebef9341c62d967ed2898a4341f4412befcf3b86df27f58f09c0549b6d2c5885` | 2026-10-18T12:33:08.858+00:00 |
| [fresh-writers.json](/private/tmp/c1-rework-3gcqdxau/fresh-writers.json) | 707 | `2bf44b4ed545e724c0fff840604eb42ebb094410d97bdd76a5836d16857d28dd` | 2026-10-18T12:13:47.684+00:00 |
| [hard-refusals-setup.json](/private/tmp/c1-rework-3gcqdxau/hard-refusals-setup.json) | 1825 | `65f6d96c3b1ad9f24bd55b54a8773ba5f94349a4cb8286c3b11a9df8e3c43351` | 2026-10-18T12:39:09.048+00:00 |
| [hard-refusals.json](/private/tmp/c1-rework-3gcqdxau/hard-refusals.json) | 1017 | `fa2e32bdff2712442a9156f3c46b48a0c966eebd5c841b940d87accc950a9a80` | 2026-10-18T12:45:32.405+00:00 |
| [hook-performance.json](/private/tmp/c1-rework-3gcqdxau/hook-performance.json) | 2787 | `1acf9fea0c80df65c1a11b944e4537de33face355ca01a5277c8eb4910e732b1` | 2026-10-18T12:26:37.182+00:00 |
| [installed-checkpoint.json](/private/tmp/c1-rework-3gcqdxau/installed-checkpoint.json) | 2116 | `4c20e041b482b29936aceaec245d616faa82217e89dffd984ef562a07cba813c` | 2026-10-18T12:29:17.962+00:00 |
| [installed-recovery.json](/private/tmp/c1-rework-3gcqdxau/installed-recovery.json) | 6070 | `2afb1353a38eec7dfdfa3f8e1913516d5da47bca039cd388da85727646caba98` | 2026-10-18T12:29:21.837+00:00 |
| [installed-setup.json](/private/tmp/c1-rework-3gcqdxau/installed-setup.json) | 3760 | `3ce9d210823b154771209e9558f8ba2393ccfcdd18bc6d84e4b4480e00c8faf1` | 2026-10-18T12:28:20.072+00:00 |
| [interrupt-setup.json](/private/tmp/c1-rework-3gcqdxau/interrupt-setup.json) | 2111 | `f4719b3825bfbc1f476f22d8ce7e06b18f8b2ce4fce0142b72aa36e376be3ce9` | 2026-10-18T12:20:50.811+00:00 |
| [lead-gap-checkpoint.json](/private/tmp/c1-rework-3gcqdxau/lead-gap-checkpoint.json) | 1202 | `0eacf91d76f6ac7291e276b50196623f6339369628f66ee421a267424082268b` | 2026-10-18T12:59:48.927+00:00 |
| [lead-gap.json](/private/tmp/c1-rework-3gcqdxau/lead-gap.json) | 3909 | `d553b9e910f19d357c9662651b41d311cecf7757e1945ac0d115e655604a5704` | 2026-10-18T12:59:49.457+00:00 |
| [listener-death.json](/private/tmp/c1-rework-3gcqdxau/listener-death.json) | 54525 | `dbb891b3b8df4e3a3c536375fc572421bdbbe117586eeed2fc88c1637dbc899b` | 2026-10-18T13:00:43.128+00:00 |
| [live-process-diagnosis-2.json](/private/tmp/c1-rework-3gcqdxau/live-process-diagnosis-2.json) | 4818 | `235b37744f996e7d97e6204a4fe4e7d851c105ba69d120ea037c3dc404d4449f` | 2026-10-18T12:28:53.865+00:00 |
| [live-process-diagnosis.json](/private/tmp/c1-rework-3gcqdxau/live-process-diagnosis.json) | 2500 | `762d033113a27c3c3873065fdec1d97b31d503026d8ef87799359dfad1794263` | 2026-10-18T12:27:48.019+00:00 |
| [mid-seal-checkpoint.json](/private/tmp/c1-rework-3gcqdxau/mid-seal-checkpoint.json) | 919 | `87fe2781006c55a28003710924a0777b8cc9d4d2c82b264425109b259c221e76` | 2026-10-18T12:21:25.222+00:00 |
| [mid-seal.json](/private/tmp/c1-rework-3gcqdxau/mid-seal.json) | 4557 | `6cd6d435e30aa0be3b0a1ca4260b76e635ea7122c4e2ce2aba1994c8194516f6` | 2026-10-18T12:21:27.295+00:00 |
| [native-attribution.json](/private/tmp/c1-rework-3gcqdxau/native-attribution.json) | 216756 | `ff49cb30fd4efdf0f501d4afbd4b0d1318893e0d5e364fb2f7697f0041710486` | 2026-10-18T12:20:59.901+00:00 |
| [owned-lock-checkpoint.json](/private/tmp/c1-rework-3gcqdxau/owned-lock-checkpoint.json) | 2041 | `a6265bf0db66ed7ee4182e2ad701ca8d80236d99c2b984aa8432b23de930ab10` | 2026-10-18T12:23:22.520+00:00 |
| [owned-lock.json](/private/tmp/c1-rework-3gcqdxau/owned-lock.json) | 4852 | `fbf67d9713acfee034f342ec68b293764743ad7aeec7c02a37b51291993308ad` | 2026-10-18T12:23:26.358+00:00 |
| [paid-ambiguous-reconciliation.json](/private/tmp/c1-rework-3gcqdxau/paid-ambiguous-reconciliation.json) | 1045 | `d5d2b12cc0fbb37779d5b1f6f7eab556d131f3d27a9b14511e4f656c71680263` | 2026-10-18T12:52:45.593+00:00 |
| [paid-final-worker-restart.json](/private/tmp/c1-rework-3gcqdxau/paid-final-worker-restart.json) | 578 | `dd055b07898716bd336dc2e7b4d62288be851bae65010b1c804758955b8d7e0a` | 2026-10-18T12:56:41.706+00:00 |
| [paid-service-acceptance.json](/private/tmp/c1-rework-3gcqdxau/paid-service-acceptance.json) | 303066 | `594010e546a90b3d9a31225ad3c8d3e51f7bdc6bb666f1939a840a1bc03f7ac7` | 2026-10-18T12:59:43.239+00:00 |
| [paid-service-final.json](/private/tmp/c1-rework-3gcqdxau/paid-service-final.json) | 222353 | `be30e7e42bf3905b5accbd5669f8abec576c2fe34c06774c4fcb68b2222f33a1` | 2026-10-18T12:53:06.238+00:00 |
| [paid-service-lifetime.json](/private/tmp/c1-rework-3gcqdxau/paid-service-lifetime.json) | 587865 | `1188a11ba2e37b4297482dd06e951243a3656688f3e50f8c336935081273f070` | 2026-10-18T12:48:04.280+00:00 |
| [paid.json](/private/tmp/c1-rework-3gcqdxau/paid.json) | 169023 | `ae6a3b7b3f1bfb72c3041595fdc903ed1449dddf01a5ccf735156ea52ca47b04` | 2026-10-18T12:45:36.681+00:00 |
| [parallel-and-survivors.json](/private/tmp/c1-rework-3gcqdxau/parallel-and-survivors.json) | 560417 | `216153bbd3515cb356290fd5106cc2e5e22a654d803eed0949556adadd3030ae` | 2026-10-18T12:26:16.543+00:00 |
| [process-diagnosis.json](/private/tmp/c1-rework-3gcqdxau/process-diagnosis.json) | 8601 | `612ff948cfa8da04522a3eda2ba6261b4332bbb7616cc1949693688e3ed75aec` | 2026-10-18T12:26:37.849+00:00 |
| [publication-and-base.json](/private/tmp/c1-rework-3gcqdxau/publication-and-base.json) | 3499 | `18276e5f53182844b63ed45cd665a67729530f3586fd5342954694ae395dfdaa` | 2026-10-18T12:14:27.389+00:00 |
| [release-admission-short.json](/private/tmp/c1-rework-3gcqdxau/release-admission-short.json) | 43292 | `673a90c0c621f3b4ffe76e53171b7de4b7ce4fd918cef88818489388b4575094` | 2026-10-18T12:51:27.411+00:00 |
| [release-restart.json](/private/tmp/c1-rework-3gcqdxau/release-restart.json) | 1857 | `606e93fe055c935554ae1e1cb3363e916daf2ecbda1ed531c904d3d3c1115290` | 2026-10-18T12:55:06.259+00:00 |
| [release-setup.json](/private/tmp/c1-rework-3gcqdxau/release-setup.json) | 2068 | `391f1d3c153a9788faf9ec85f3ae6027ff23268216bb1855423b6c4af48bb9e0` | 2026-10-18T12:53:08.263+00:00 |
| [restart.json](/private/tmp/c1-rework-3gcqdxau/restart.json) | 2015 | `7f20dbbd6fd4ff61cb97f65150416429dd820a011d2b64e4abc8e4cabcb936af` | 2026-10-18T12:19:16.280+00:00 |
| [rpc-memory.json](/private/tmp/c1-rework-3gcqdxau/rpc-memory.json) | 795 | `a0e3fcf5ae9d9c3c00a16d8cc0d51f6a217ca099eb4b64bbc350cee48d081979` | 2026-10-18T12:24:32.124+00:00 |
| [scope-and-seals.json](/private/tmp/c1-rework-3gcqdxau/scope-and-seals.json) | 7356 | `213b9f57f619fe9aaf1cad5891f1364b4feb8ec9bb88fd7aeff714efa1c6289a` | 2026-10-18T12:13:47.525+00:00 |
| [setup.json](/private/tmp/c1-rework-3gcqdxau/setup.json) | 1609 | `429d78a9dae0d15f87c7752e94af3f18ba7645fa01120aa41b7d9269de4f1aee` | 2026-10-18T12:12:29.825+00:00 |
| [socket-boundary.json](/private/tmp/c1-rework-3gcqdxau/socket-boundary.json) | 867 | `30694e3e765fa502b49e194f383b6d4588d73b6d697d8cad4d1c890dab32b608` | 2026-10-18T13:00:49.719+00:00 |
| [startup-refusal.json](/private/tmp/c1-rework-3gcqdxau/startup-refusal.json) | 746 | `d4eb0b9aaf116d45313aa989d749eaef0495280a1df70ec94f80805c60da663a` | 2026-10-18T12:51:21.124+00:00 |
| [final-checks.json](/private/tmp/c1-rework-3gcqdxau/final-checks.json) | 277 | `c9a6c4d53ad7447aa7cd159f7686f5fe03b1ee577f92f267aa803b84ceb2b5c9` | 2026-10-18T13:12:18.487+00:00 |

## Complete recorded operational invocations

No ellipses replace command bodies. Output is exactly the captured output, including any earlier tool truncation or recorded diagnostic summary. Full JSON artifacts are linked above. The three nonzero enclosing exits were C21 (1, incomplete review), C35 (1, incomplete restoration map), and C58 (3, deliberate startup-permission failure); other enclosing commands exited 0, which must be interpreted with the behavioral outcomes above.

| ID | Invocation | Observed exit |
|---|---|---:|
| [C01](#c01) | environment | 0 |
| [C02](#c02) | start-short | 0 |
| [C03](#c03) | start-long | 0 |
| [C04](#c04) | admission-short | 0 |
| [C05](#c05) | admission-long | 0 |
| [C06](#c06) | setup | 0 |
| [C07](#c07) | admission-summary | 0 |
| [C08](#c08) | provider | 0 |
| [C09](#c09) | fresh | 0 |
| [C10](#c10) | seals | 0 |
| [C11](#c11) | publication | 0 |
| [C12](#c12) | provider-more | 0 |
| [C13](#c13) | classification | 0 |
| [C14](#c14) | restart | 0 |
| [C15](#c15) | native-attribution | 0 |
| [C16](#c16) | custody | 0 |
| [C17](#c17) | interrupt-setup | 0 |
| [C18](#c18) | mid-seal | 0 |
| [C19](#c19) | provider3 | 0 |
| [C20](#c20) | owned-lock | 0 |
| [C21](#c21) | owned-lock-incomplete-review | 1 |
| [C22](#c22) | parallel | 0 |
| [C23](#c23) | memory | 0 |
| [C24](#c24) | branch-setup | 0 |
| [C25](#c25) | branch | 0 |
| [C26](#c26) | hooks-performance | 0 |
| [C27](#c27) | diagnostic | 0 |
| [C28](#c28) | live-diagnosis | 0 |
| [C29](#c29) | installed-setup | 0 |
| [C30](#c30) | live-diagnosis2 | 0 |
| [C31](#c31) | installed | 0 |
| [C32](#c32) | final-process | 0 |
| [C33](#c33) | scope-restoration-setup | 0 |
| [C34](#c34) | provider-owned | 0 |
| [C35](#c35) | final-restart | 1 |
| [C36](#c36) | final-admission-short | 0 |
| [C37](#c37) | final-admission-long | 0 |
| [C38](#c38) | restoration-corrected | 0 |
| [C39](#c39) | final-parallel | 0 |
| [C40](#c40) | final-hooks | 0 |
| [C41](#c41) | hard-refusals-setup | 0 |
| [C42](#c42) | process-diagnostics | 0 |
| [C43](#c43) | final-child | 0 |
| [C44](#c44) | final-class | 0 |
| [C45](#c45) | paid | 0 |
| [C46](#c46) | message-recovery | 0 |
| [C47](#c47) | hard-refusals | 0 |
| [C48](#c48) | paid-service | 0 |
| [C49](#c49) | commit-created-setup | 0 |
| [C50](#c50) | python39-help | 0 |
| [C51](#c51) | python39-hook | 0 |
| [C52](#c52) | python39-client | 0 |
| [C53](#c53) | commit-created | 0 |
| [C54](#c54) | service-restart | 0 |
| [C55](#c55) | long-service-restart | 0 |
| [C56](#c56) | paid-service-final | 0 |
| [C57](#c57) | release-admission-short | 0 |
| [C58](#c58) | startup-refusal | 3 |
| [C59](#c59) | paid-reconcile | 0 |
| [C60](#c60) | release-setup | 0 |
| [C61](#c61) | release-restart | 0 |
| [C62](#c62) | acceptance-short | 0 |
| [C63](#c63) | acceptance-long | 0 |
| [C64](#c64) | paid-final-restart | 0 |
| [C65](#c65) | acceptance-writers | 0 |
| [C66](#c66) | paid-service-acceptance | 0 |
| [C67](#c67) | acceptance-survivor | 0 |
| [C68](#c68) | lead-gap | 0 |
| [C69](#c69) | listener-death | 0 |
| [C70](#c70) | final-fingerprints | 0 |
| [C71](#c71) | final-python39-client | 0 |
| [C72](#c72) | final-python39-hook | 0 |
| [C73](#c73) | final-python39-help | 0 |
| [C74](#c74) | socket-boundary | 0 |
| [C75](#c75) | cleanup-daemons | 0 |
| [C76](#c76) | cleanup-providers | 0 |
| [C77](#c77) | final-files-and-evidence-check | 0 |

<a id="c01"></a>

<details>
<summary>C01 — environment; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json, tempfile, platform, sqlite3, sys, subprocess, time
from coordinator import runtime, native
root=Path(tempfile.mkdtemp(prefix='c1-rework-',dir='/private/tmp'))
short=root/('s'*(44-len(str(root))-1))
long=root/('long-state-'+'x'*110)/'state'
value={'root':str(root),'short':str(short),'long':str(long),'scratch_short':str(root/'admission-short'),'scratch_long':str(root/('scratch-'+'y'*110)),'python':sys.version,'sqlite':sqlite3.sqlite_version,'os':platform.platform(),'codex':native.native_codex(),'codex_sha256':native.file_digest(native.native_codex()),'time':time.time(),'capacity':runtime.capacity()}
(root/'environment.json').write_text(json.dumps(value,indent=2))
print(json.dumps(value))
PY
```

Captured output:

```text
{"root": "/private/tmp/c1-rework-3gcqdxau", "short": "/private/tmp/c1-rework-3gcqdxau/ssssssssssss", "long": "/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state", "scratch_short": "/private/tmp/c1-rework-3gcqdxau/admission-short", "scratch_long": "/private/tmp/c1-rework-3gcqdxau/scratch-yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy", "python": "3.15.0 (main, Oct  9 2026, 11:16:41) [Clang 21.0.0 (clang-2100.3.34.2)]", "sqlite": "3.54.0", "os": "macOS-26.6.2-arm64-arm-64bit-Mach-O", "codex": "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex", "codex_sha256": "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31", "time": 1792325507.85451, "capacity": 103}
```

</details>


<a id="c02"></a>

<details>
<summary>C02 — start-short; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 SOFTWARE_WORKFLOWS_PYTHON=/opt/homebrew/opt/python@3.15/bin/python3.15 /opt/homebrew/opt/python@3.15/bin/python3.15 -B -m coordinator --state /private/tmp/c1-rework-3gcqdxau/ssssssssssss daemon start
```

Captured output:

```text
{"ok": true, "result": {"birth": "1792325512:296854", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/siyj_7ei8/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/siyj_7ei8/s", "resolved_bytes": 31, "uid": 501}, "generation": 1, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "pid": 12054, "ready": true, "sqlite": "3.54.0", "state_root": "/private/tmp/c1-rework-3gcqdxau/ssssssssssss", "writers": []}}
```

</details>

<a id="c03"></a>

<details>
<summary>C03 — start-long; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 SOFTWARE_WORKFLOWS_PYTHON=/opt/homebrew/opt/python@3.15/bin/python3.15 /opt/homebrew/opt/python@3.15/bin/python3.15 -B -m coordinator --state /private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state daemon start
```

Captured output:

```text
{"ok": true, "result": {"birth": "1792325512:519366", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/su_tg08bi/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/su_tg08bi/s", "resolved_bytes": 31, "uid": 501}, "generation": 1, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "pid": 12058, "ready": true, "sqlite": "3.54.0", "state_root": "/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state", "writers": []}}
```

</details>

<a id="c04"></a>

<details>
<summary>C04 — admission-short; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B -m coordinator --state /private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state admission --scratch /private/tmp/c1-rework-3gcqdxau/scratch-yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy --probe hooks --probe evidence --probe dynamic --probe delivery --probe tty --probe reconnect --probe terminals --probe policy --probe validation --probe exec_policy > /private/tmp/c1-rework-3gcqdxau/admission-long.json
```

Captured output:

```text
(no stdout/stderr captured)
```

</details>

<a id="c05"></a>

<details>
<summary>C05 — admission-long; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B -m coordinator --state /private/tmp/c1-rework-3gcqdxau/ssssssssssss admission --scratch /private/tmp/c1-rework-3gcqdxau/admission-short --probe hooks --probe evidence --probe dynamic --probe delivery --probe tty --probe reconnect --probe terminals --probe policy --probe validation --probe exec_policy > /private/tmp/c1-rework-3gcqdxau/admission-short.json
```

Captured output:

```text
(no stdout/stderr captured)
```

</details>

<a id="c06"></a>

<details>
<summary>C06 — setup; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json, time
from concurrent.futures import ThreadPoolExecutor
from coordinator.client import Client, CoordinatorError
from coordinator import gitio
root=Path('/private/tmp/c1-rework-3gcqdxau')
env=json.loads((root/'environment.json').read_text())
metadata={k:v for k,v in [('message','C1 observed publication'),('author_name','C1 Fixture'),('author_email','c1@example.invalid'),('author_date','2026-10-18T12:30:00+00:00'),('committer_name','C1 Fixture'),('committer_email','c1@example.invalid'),('committer_date','2026-10-18T12:30:00+00:00')]}
git_env={'GIT_'+k.upper():v for k,v in metadata.items() if k!='message'}
repos={}
for name in ['core','scope','postseal','crash','custody','long']:
    repo=root/(name+'-repo'); repo.mkdir(mode=0o700)
    gitio.git(repo,['init','-q'])
    entries={}
    for path in ['a.txt','b.txt','unrelated.txt']:
        content=(path+' baseline\n').encode()
        (repo/path).write_bytes(content)
        entries[path]={'mode':'100644','oid':gitio.text(repo,['hash-object','-w','--stdin'],data=content)}
    index=root/(name+'-initial.index')
    gitio.git(repo,['read-tree','--empty'],index=index)
    gitio.put_entries(repo,index,entries)
    tree=gitio.text(repo,['write-tree'],index=index)
    commit=gitio.text(repo,['commit-tree',tree],data=b'C1 disposable baseline\n',extra_env=git_env)
    gitio.git(repo,['update-ref','HEAD',commit])
    gitio.git(repo,['read-tree',commit])
    repos[name]=str(repo)
alias=root/'core-alias';alias.symlink_to(repos['core'],target_is_directory=True)
c=Client(env['short'],timeout=300)
with ThreadPoolExecutor(max_workers=16) as pool:
    registrations=list(pool.map(lambda path: Client(env['short'],timeout=300).call('checkout/register',{'path':path}),
                                [repos['core'],str(alias),repos['core']+'/../core-repo']*8))
ids=sorted(set(r['id'] for r in registrations))
print(json.dumps({'registrations':len(registrations),'ids':ids,'all_ready':all(r.get('ready') for r in registrations)}))
handles={'core':ids[0]}
for name in ['scope','postseal','crash','custody']:
    handles[name]=c.call('checkout/register',{'path':repos[name]})['id']
handles['long']=Client(env['long'],timeout=300).call('checkout/register',{'path':repos['long']})['id']
try:
    Client(env['long'],timeout=300).call('checkout/register',{'path':repos['core']})
except CoordinatorError as exc:
    print(json.dumps({'second_state':exc.envelope()}))
a=c.call('run/start',{'checkout':ids[0],'mode':'lead','paths':['a.txt']})
b=c.call('run/start',{'checkout':ids[0],'mode':'lead'})
try:
    c.call('claim/acquire',{'actor':b['actor'],'paths':['a.txt']})
except CoordinatorError as exc:
    print(json.dumps({'alias_claim_conflict':exc.envelope()}))
value={'repos':repos,'checkouts':handles,'metadata':metadata,'core_a':a,'core_b':b,'time':time.time()}
(root/'setup.json').write_text(json.dumps(value,indent=2))
print(json.dumps(value))
PY
```

Captured output:

```text
{"registrations": 24, "ids": ["checkout_dcf42099445a4e3dacd7bb25cae8b24b"], "all_ready": true}
{"second_state": {"code": "CHECKOUT_OWNED", "message": "Another coordinator owns this canonical checkout", "details": {"identity": "36faf86edcb8ee4e70cb5d3d5a64519cfe81395a7169a1c6448a76c5409696a0"}, "kind": "refusal"}}
{"alias_claim_conflict": {"code": "CLAIM_CONFLICT", "message": "Scope held by unit_e87bf865452047a1b325114e18d2e218; send a message to that holder or the lead, then request lead-authorized handback", "details": {"conflicts": [{"holder": "unit_e87bf865452047a1b325114e18d2e218", "kind": "claim", "scope": "a.txt"}], "request": "request_d8b73f0d7b0446908dbbf4bac1f38596"}, "kind": "refusal"}}
{"repos": {"core": "/private/tmp/c1-rework-3gcqdxau/core-repo", "scope": "/private/tmp/c1-rework-3gcqdxau/scope-repo", "postseal": "/private/tmp/c1-rework-3gcqdxau/postseal-repo", "crash": "/private/tmp/c1-rework-3gcqdxau/crash-repo", "custody": "/private/tmp/c1-rework-3gcqdxau/custody-repo", "long": "/private/tmp/c1-rework-3gcqdxau/long-repo"}, "checkouts": {"core": "checkout_dcf42099445a4e3dacd7bb25cae8b24b", "scope": "checkout_f92a5a18d84249febcd9ab8827c60161", "postseal": "checkout_ed56ede87ce445289ce54200f75e6577", "crash": "checkout_67c7fa30ad3840e797849c6b0d52d2a8", "custody": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "long": "checkout_8d42e6b1edf2498090905d8730ce216b"}, "metadata": {"message": "C1 observed publication", "author_name": "C1 Fixture", "author_email": "c1@example.invalid", "author_date": "2026-10-18T12:30:00+00:00", "committer_name": "C1 Fixture", "committer_email": "c1@example.invalid", "committer_date": "2026-10-18T12:30:00+00:00"}, "core_a": {"actor": "actor_acc97d5807ca4122a03f578881ea358b", "attempt": "attempt_e5316b16dc09444f919f82e884132be4", "policy": null, "run": "run_e35baf74a899418da38fb3ee5fdd31de", "unit": "unit_e87bf865452047a1b325114e18d2e218"}, "core_b": {"actor": "actor_9c231393ec6c4389bd2b2a40822c211f", "attempt": "attempt_77b4934b9f2f4d429bd9ca522b87cf24", "policy": null, "run": "run_bd296abb758b4cac9f396f6ce03b9b5d", "unit": "unit_f80b3c3027c4498ba63332143a6cfee7"}, "time": 1792325549.825283}
```

</details>

<a id="c07"></a>

<details>
<summary>C07 — admission-summary; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json
root=Path('/private/tmp/c1-rework-3gcqdxau')
for name in ['short','long']:
    value=json.loads((root/('admission-'+name+'.json')).read_text())['result']
    print(json.dumps({'name':name,'completed':value['completed'],'production_admitted':value['production_admitted'],'cases':[(c['probe'],c['passed']) for c in value['cases']],'socket':value['fingerprint']['socket'],'adapter_digest':value['fingerprint']['adapter_digest'],'evidence':value['evidence']}))
PY
```

Captured output:

```text
{"name": "short", "completed": true, "production_admitted": false, "cases": [["hooks", true], ["evidence", true], ["dynamic", true], ["delivery", true], ["tty", true], ["reconnect", true], ["terminals", true], ["policy", true], ["validation", true], ["exec_policy", true]], "socket": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/sprqpp095/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/ce1c5be38fc70c69368af9937e6e8401abcea826aa5f1edd8e27c23a69ca1004", "resolved_bytes": 94, "uid": 501}, "adapter_digest": "d3ec752b02ed886817611521ee7e8ecae729523ceb5e00c6bfa47cd4a2cb7dd8", "evidence": "/private/tmp/c1-rework-3gcqdxau/admission-short/admission_1e4ba8fb09ec4338820abb50d91142d8"}
{"name": "long", "completed": true, "production_admitted": false, "cases": [["hooks", true], ["evidence", true], ["dynamic", true], ["delivery", true], ["tty", true], ["reconnect", true], ["terminals", true], ["policy", true], ["validation", true], ["exec_policy", true]], "socket": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/s7gnplw4c/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/918ec5c02d47aca9efb29707e41a20b169a06480f0e181a6435032f89a63c9ec", "resolved_bytes": 94, "uid": 501}, "adapter_digest": "d3ec752b02ed886817611521ee7e8ecae729523ceb5e00c6bfa47cd4a2cb7dd8", "evidence": "/private/tmp/c1-rework-3gcqdxau/scratch-yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy/admission_bd1cdf53969346d18406de04106a2535"}
```

</details>

<a id="c08"></a>

<details>
<summary>C08 — provider; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B -m coordinator fixture serve --directory /private/tmp/c1-rework-3gcqdxau/fixture --plan '{"default":[{"tool":"status","arguments":{}},{"text":"done"}],"routes":{"C1_NATIVE_A":[{"patch":"*** Begin Patch\n*** Add File: native_a.py\n+def first(value):\n+    return value + 1\n*** End Patch\n"},{"text":"done"}],"C1_NATIVE_B":[{"patch":"*** Begin Patch\n*** Add File: native_b.py\n+def second(value):\n+    return value * 2\n*** End Patch\n"},{"text":"done"}]}}'
```

Captured output:

```text
{"fixture": "/private/tmp/c1-rework-3gcqdxau/fixture", "pid": 16338, "port": 54551}
```

</details>

<a id="c09"></a>

<details>
<summary>C09 — fresh; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time
from coordinator.client import Client
root=Path('/private/tmp/c1-rework-3gcqdxau')
env=json.loads((root/'environment.json').read_text()); setup=json.loads((root/'setup.json').read_text())
def start(name,checkout):
    c=Client(env[name],timeout=1800)
    result=c.call('run/start',{'checkout':checkout,'mode':'native','task':'Report status and finish','fixture':str(root/'fixture'),'model':'gpt-6-astra'})
    return name,result
with ThreadPoolExecutor(max_workers=2) as pool:
    runs=dict(pool.map(lambda pair:start(*pair), [('short',setup['checkouts']['core']),('long',setup['checkouts']['long'])]))
for name, run in runs.items():
    c=Client(env[name],timeout=300)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        status=c.call('run/status',{'run':run['run']})
        if status['state'] in ('completed','failed','interrupted'):break
        time.sleep(.1)
    listeners=[l for l in c.call('records/list',{'kind':'listener','limit':1000})['data'] if l['run']==run['run'] and l['state']=='connected']
    print(json.dumps({'name':name,'run':run,'status':status,'listeners':[{'id':l['id'],'endpoint':l['endpoint'],'admission':l.get('admission'),'services':l.get('services')} for l in listeners]}))
(root/'fresh-writers.json').write_text(json.dumps(runs,indent=2))
PY
```

Captured output:

```text
{"name": "short", "run": {"actor": "actor_194e8af9ba284cfcb295335ab082b9fe", "attempt": "attempt_fdcd62705f82448c9ae5d5db48bdb31a", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_e2df666f93cc4298bf20a8825101652b", "unit": "unit_46c70f56947c468494a629ed34a1c371"}, "status": {"_kind": "run", "_revision": 3, "attempt": "attempt_fdcd62705f82448c9ae5d5db48bdb31a", "checkout": "checkout_dcf42099445a4e3dacd7bb25cae8b24b", "id": "run_e2df666f93cc4298bf20a8825101652b", "mode": "native", "root_actor": "actor_194e8af9ba284cfcb295335ab082b9fe", "state": "completed", "task": "Report status and finish", "unit": "unit_46c70f56947c468494a629ed34a1c371"}, "listeners": [{"id": "listener_0089d56f42d141a5821abf09dc2a05f9", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/s86kci4n9/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/56d54800d6e25228ba66c33045809895e6ec68b8a30443d4f6a6d799266bf0e6", "resolved_bytes": 94, "uid": 501}, "admission": "c9a129e01aab5ecce3671d887a9cf59d68d1c1a49384ef7abb862bde9128ff69", "services": []}]}
{"name": "long", "run": {"actor": "actor_7d12f0bc9fa1499287fdfb8d67390712", "attempt": "attempt_989229d043eb4ab1a1b26f2653f63949", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_1ea319f009e14be3a54e3b7c47f97f09", "unit": "unit_3e0b998a2bf5447cb0c0bf8166c040ff"}, "status": {"_kind": "run", "_revision": 3, "attempt": "attempt_989229d043eb4ab1a1b26f2653f63949", "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "id": "run_1ea319f009e14be3a54e3b7c47f97f09", "mode": "native", "root_actor": "actor_7d12f0bc9fa1499287fdfb8d67390712", "state": "completed", "task": "Report status and finish", "unit": "unit_3e0b998a2bf5447cb0c0bf8166c040ff"}, "listeners": [{"id": "listener_e586a2650e8f4b2bb252605a96580bcb", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/smomkswzr/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/498fdced196f720fd4dd78ef1c880ad7a3e73b6294f7ebc3347bd4ef958b2679", "resolved_bytes": 94, "uid": 501}, "admission": "99ea14f48847f4273e3edaf881c294544cfb1cab972d71bae89982dc475deb54", "services": []}]}
```

</details>

<a id="c10"></a>

<details>
<summary>C10 — seals; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,base64,time
from coordinator.client import Client,CoordinatorError
root=Path('/private/tmp/c1-rework-3gcqdxau'); env=json.loads((root/'environment.json').read_text()); setup=json.loads((root/'setup.json').read_text())
c=Client(env['short'],timeout=300); out={}
for name in ['scope','postseal','core']:
    run=setup['core_a'] if name=='core' else c.call('run/start',{'checkout':setup['checkouts'][name],'mode':'lead'})
    paths=['a.txt'] if name=='core' else ['a.txt','b.txt']
    changes={p:{'mode':'100644','bytes_base64':base64.b64encode((name+' verified '+p+'\n').encode()).decode()} for p in paths}
    edit=c.call('lead-edit',{'actor':run['actor'],'changes':changes})
    if name=='scope':
        Path(setup['repos'][name],'a.txt').write_text('external interference\n')
    try:
        seal=c.call('unit/seal',{'unit':run['unit']})
        diff=c.call('unit/diff',{'unit':run['unit']})
        out[name]={'run':run,'edit':edit['id'],'seal':seal,'diff':diff}
        print(json.dumps({'name':name,'diff':diff}))
    except CoordinatorError as exc:
        claims=[x for x in c.call('records/list',{'kind':'claim','limit':1000})['data'] if x['unit']==run['unit']]
        out[name]={'run':run,'edit':edit['id'],'refusal':exc.envelope(),'claims':claims}
        print(json.dumps({'name':name,**out[name]}))
out['time']=time.time()
(root/'scope-and-seals.json').write_text(json.dumps(out,indent=2))
PY
```

Captured output:

```text
{"name": "scope", "run": {"actor": "actor_6270789e8dab41e891f87a69460a0000", "attempt": "attempt_59e1b7ec348b49fca6801a9f37e2f830", "policy": null, "run": "run_87e41b2ffd6f4c29a7350f46c6ccf11d", "unit": "unit_d4564676463b4908afcbab189532f5c4"}, "edit": "op_c2c2b1038ad641cd8b5daf43aa58daf5", "refusal": {"code": "UNKNOWN_BLOCKS_SEAL", "message": "Complete operation scope includes unresolved UNKNOWN history", "details": {"paths": ["a.txt", "b.txt"], "unknowns": ["unknown_ea70968ef1b64781a2fe8736b3c4500b"]}, "kind": "refusal"}, "claims": [{"_kind": "claim", "_revision": 1, "actor": "actor_6270789e8dab41e891f87a69460a0000", "checkout": "checkout_f92a5a18d84249febcd9ab8827c60161", "id": "claim_941e3c657a784d32a247efe126d78ac1", "key": "a.txt", "path": "a.txt", "state": "held", "unit": "unit_d4564676463b4908afcbab189532f5c4"}, {"_kind": "claim", "_revision": 1, "actor": "actor_6270789e8dab41e891f87a69460a0000", "checkout": "checkout_f92a5a18d84249febcd9ab8827c60161", "id": "claim_b7becdde068c4d76a2b883d54feb4291", "key": "b.txt", "path": "b.txt", "state": "held", "unit": "unit_d4564676463b4908afcbab189532f5c4"}]}
{"name": "postseal", "diff": {"binding": {"base": "2a19c8a89ad7387b92fe91e69651398612ecfbae", "candidate": "0270cc729961663c4eda3218e8dc20e067371808", "provenance": "85db5eb7768e3fb509f049c956795eae1a771b83708f944cb762738dac153434", "repository": "1ed955f358b0c711bf71e5ecb9c9a14495cd345ce6d64884f8114e6d82bb1461", "seal": "seal_135766fbba814d768cbded5fb022cc2e", "unit": "unit_37bb71df08ce4b5689f6d7d1e3d9b1cf", "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "diff": "diff --git a/a.txt b/a.txt\nindex 5c18429..bf832f1 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a.txt baseline\n+postseal verified a.txt\ndiff --git a/b.txt b/b.txt\nindex 88415ee..5b6fff3 100644\n--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-b.txt baseline\n+postseal verified b.txt\n"}}
{"name": "core", "diff": {"binding": {"base": "2a19c8a89ad7387b92fe91e69651398612ecfbae", "candidate": "df6415a234ef78a8095812ecae858c3feddead6d", "provenance": "36011325be7d27ac11bae4c708f6af5ab51ba4ccc8436b4c00347775c20eb743", "repository": "525f063d73da6f1e77371c5e15eb1cb30ffdc99d7c731f905ee6eeaed25a950d", "seal": "seal_a443b5f2b3a44e41a5a7deede55d7539", "unit": "unit_e87bf865452047a1b325114e18d2e218", "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "diff": "diff --git a/a.txt b/a.txt\nindex 5c18429..67900fa 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a.txt baseline\n+core verified a.txt\n"}}
```

</details>

<a id="c11"></a>

<details>
<summary>C11 — publication; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,hashlib,base64,time
from coordinator.client import Client,CoordinatorError
from coordinator import gitio
root=Path('/private/tmp/c1-rework-3gcqdxau'); env=json.loads((root/'environment.json').read_text()); setup=json.loads((root/'setup.json').read_text()); seals=json.loads((root/'scope-and-seals.json').read_text())
c=Client(env['short'],timeout=300); out={}
for name in ['postseal','core']:
    item=seals[name]; unit=item['run']['unit']
    answer='Reviewed the printed immutable object diff: only the intended literal scratch lines changed in the recorded paths.'
    evidence=dict(item['diff']['binding'],completed=True,verdict='pass',review_identity='C1 direct object inspection 2026-10-18',answer=answer,answer_digest=hashlib.sha256(answer.encode()).hexdigest())
    review=c.call('unit/review-record',{'unit':unit,'evidence':evidence})
    if name=='postseal':
        Path(setup['repos'][name],'a.txt').write_text('external after seal\n')
    else:
        repo=setup['repos'][name]
        lines=[b'0 '+b'0'*40+b'\tunrelated.txt\0']
        for stage in [1,2,3]:
            oid=gitio.text(repo,['hash-object','-w','--stdin'],data=('conflict stage '+str(stage)+'\n').encode())
            lines.append(('100644 '+oid+' '+str(stage)+'\tunrelated.txt\0').encode())
        gitio.git(repo,['update-index','-z','--index-info'],data=b''.join(lines))
        before=gitio.entries(repo)['unrelated.txt']
    try:
        result=c.call('unit/publish',{'unit':unit,'review':review['review'],'metadata':setup['metadata']})
        out[name]={'publication':result,'unrelated_before':before,'unrelated_after':gitio.entries(repo)['unrelated.txt']}
    except CoordinatorError as exc:
        out[name]={'refusal':exc.envelope()}
    print(json.dumps({'name':name,**out[name]}))
a_commit=out.get('core',{}).get('publication',{}).get('commit')
if a_commit:
    b=c.call('run/start',{'checkout':setup['checkouts']['core'],'mode':'lead','paths':['a.txt']})
    edit=c.call('lead-edit',{'actor':b['actor'],'changes':{'a.txt':{'mode':'100644','bytes_base64':base64.b64encode(b'unit B follows published A\n').decode()}}})
    seal=c.call('unit/seal',{'unit':b['unit']})
    out['fresh_base']={'a_commit':a_commit,'b':b,'base':seal['base'],'seal':seal['id'],'paths':seal['paths']}
    print(json.dumps({'fresh_base':out['fresh_base']}))
(root/'publication-and-base.json').write_text(json.dumps(out,indent=2))
PY
```

Captured output:

```text
{"name": "postseal", "refusal": {"code": "UNKNOWN_BLOCKS_PUBLICATION", "message": "Unresolved UNKNOWN includes a frozen path", "details": {}, "kind": "refusal"}}
{"name": "core", "publication": {"_kind": "publication", "_revision": 1, "base_index_entries": {"a.txt": [{"mode": "100644", "oid": "5c184290922f49c64e1c8a8dc1dd9dc0fe40179e", "stage": "0"}]}, "commit": "03b52828808b0bb5dbf29d91b4ab6fdccf713b2e", "config_digest": "3dab0b9b9edcc59256ce5632d7c85fcc832e5f334d2e6705256789a6bb9d3fc6", "created": 1792325667.011292, "id": "publication_61fcab6e79fb44309a2546b93664ef0c", "index_lock": {"anchor": "/private/tmp/c1-rework-3gcqdxau/core-repo/.git/c1-index_owner_485852f6713f4b0ab845295829876665", "checkout_identity": "36faf86edcb8ee4e70cb5d3d5a64519cfe81395a7169a1c6448a76c5409696a0", "device": 16777230, "generation": 1, "index": "/private/tmp/c1-rework-3gcqdxau/core-repo/.git/index", "inode": 748237352, "lock": "/private/tmp/c1-rework-3gcqdxau/core-repo/.git/index.lock", "owner": {"birth": "1792325512:296854", "pid": 12054, "ppid": 12053, "uid": 501, "zombie": false}, "publication": "publication_61fcab6e79fb44309a2546b93664ef0c", "replacement_digest": "91283d98febb31aec7323f2261cf4e0dfaf0bbf473006a6a3ca9d1394a12a378", "state": "checkpointed", "token": "index_owner_485852f6713f4b0ab845295829876665"}, "metadata": {"author_date": "2026-10-18T12:30:00+00:00", "author_email": "c1@example.invalid", "author_name": "C1 Fixture", "committer_date": "2026-10-18T12:30:00+00:00", "committer_email": "c1@example.invalid", "committer_name": "C1 Fixture", "message": "C1 observed publication"}, "phase": "complete", "review": "review_f15d802f21d644c7a4cdfbd4eec37c25", "seal": "seal_a443b5f2b3a44e41a5a7deede55d7539", "unit": "unit_e87bf865452047a1b325114e18d2e218"}, "unrelated_before": [{"mode": "100644", "oid": "08ae341aaf72239213a1c124a27ccbe73336ea61", "stage": "1"}, {"mode": "100644", "oid": "3e057a63a34442af734f2d71b951810eb384e0eb", "stage": "2"}, {"mode": "100644", "oid": "95f457f37bfca9fb8c2220c3b8cf32c3b13dfc1e", "stage": "3"}], "unrelated_after": [{"mode": "100644", "oid": "08ae341aaf72239213a1c124a27ccbe73336ea61", "stage": "1"}, {"mode": "100644", "oid": "3e057a63a34442af734f2d71b951810eb384e0eb", "stage": "2"}, {"mode": "100644", "oid": "95f457f37bfca9fb8c2220c3b8cf32c3b13dfc1e", "stage": "3"}]}
{"fresh_base": {"a_commit": "03b52828808b0bb5dbf29d91b4ab6fdccf713b2e", "b": {"actor": "actor_846d1535dbf44986a68414989ba35aae", "attempt": "attempt_8135d70e7c6b4c0e9a2d19ec7bd6aaff", "policy": null, "run": "run_be7e23c4ce8b4e41af63cabb253f2c63", "unit": "unit_1c1945babca04dac9a3edcf520dcd4c2"}, "base": "03b52828808b0bb5dbf29d91b4ab6fdccf713b2e", "seal": "seal_1eaca0c4cb6f40fa929f94f10daadc9d", "paths": ["a.txt"]}}
```

</details>

<a id="c12"></a>

<details>
<summary>C12 — provider-more; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B -m coordinator fixture serve --directory /private/tmp/c1-rework-3gcqdxau/fixture-more --plan '{"routes":{"C1_CLASSIFY":[{"command":"git commit"},{"command":"git push; true"},{"command":"git tag\ntrue"},{"command":"git reset | cat"},{"command":"true && git checkout a.txt"},{"command":"false || git restore a.txt"},{"command":"'\''git'\'' '\''commit'\''; true"},{"command":"sh -c '\''git push; true'\''"},{"command":"bash -lc '\''git tag; true'\''"},{"command":"env FOO=value command git -C . commit"},{"command":"git -c alias.record=commit record"},{"command":"git made-up-subcommand"},{"command":"git --git-dir=.git status"},{"command":"$(printf git) push"},{"command":"printf '\''%s'\'' \"$(git push)\""},{"command":"git push '\''unterminated"},{"tool":"declare","arguments":{"level":"shared","command":"printf '\''%s'\'' '\''git push'\''","reason":"Print literal text only"}},{"command":"printf '\''%s'\'' '\''git push'\''"},{"tool":"declare","arguments":{"level":"shared","command":"git --no-pager status --short","reason":"Read scratch status"}},{"command":"git --no-pager status --short"},{"text":"done"}],"C1_SHARED":[{"tool":"declare","arguments":{"level":"shared","command":"sleep 8","reason":"No tracked writes"}},{"command":"sleep 8","arguments":{"yield_time_ms":1000}},{"text":"done"}],"C1_EXCLUSIVE":[{"command":"sleep 3","arguments":{"yield_time_ms":1000}},{"text":"done"}],"C1_CHILD_ROOT":[{"tool":"spawn_agent","namespace":"collaboration","arguments":{"task_name":"child","message":"C1_CHILD_WORK","fork_turns":"none"}},{"text":"root done"}],"C1_CHILD_WORK":[{"patch":"*** Begin Patch\n*** Add File: child.py\n+def child():\n+    return 1\n*** End Patch\n"},{"command":"printf child"},{"text":"child done"}]}}'
```

Captured output:

```text
{"fixture": "/private/tmp/c1-rework-3gcqdxau/fixture-more", "pid": 21055, "port": 54773}
```

</details>

<a id="c13"></a>

<details>
<summary>C13 — classification; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time
from coordinator.client import Client
root=Path('/private/tmp/c1-rework-3gcqdxau');env=json.loads((root/'environment.json').read_text());setup=json.loads((root/'setup.json').read_text())
c=Client(env['long'],timeout=1800)
run=c.call('run/start',{'checkout':setup['checkouts']['long'],'mode':'native','task':'C1_CLASSIFY','fixture':str(root/'fixture-more'),'model':'gpt-6-astra'})
deadline=time.monotonic()+180
while time.monotonic()<deadline:
    status=c.call('run/status',{'run':run['run']})
    if status['state'] in ('completed','failed','interrupted'):break
    time.sleep(.1)
operations=[o for o in c.call('records/list',{'kind':'operation','limit':10000})['data'] if o['unit']==run['unit']]
reservations=[r for r in c.call('records/list',{'kind':'custody','limit':10000})['data'] if r['unit']==run['unit']]
value={'run':run,'status':status,'operations':operations,'custody':reservations,'unknowns':c.call('unknown/list',{'checkout':setup['checkouts']['long']})}
(root/'classification.json').write_text(json.dumps(value,indent=2))
print(json.dumps({'status':status['state'],'operations':[{'command':o['intended']['tool_input']['command'],'state':o['state'],'refusal':o.get('refusal')} for o in operations],'custody':[{'command':r['command'],'level':r['level'],'state':r['state'],'reason':r.get('reason')} for r in reservations],'unknowns':value['unknowns']}))
PY
```

Captured output:

```text
{"status": "completed", "operations": [{"command": "git made-up-subcommand", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "'git' 'commit'; true", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git --no-pager status --short", "state": "reconciled", "refusal": null}, {"command": "bash -lc 'git tag; true'", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "sh -c 'git push; true'", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "true && git checkout a.txt", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git reset | cat", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git commit", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "env FOO=value command git -C . commit", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git --git-dir=.git status", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git -c alias.record=commit record", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git tag\ntrue", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "false || git restore a.txt", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "$(printf git) push", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git push 'unterminated", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "printf '%s' \"$(git push)\"", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "printf '%s' 'git push'", "state": "reconciled", "refusal": null}, {"command": "git push; true", "state": "denied", "refusal": "LEAD_RECORDS"}], "custody": [{"command": "printf '%s' 'git push'", "level": "shared", "state": "released", "reason": null}, {"command": "git --no-pager status --short", "level": "shared", "state": "released", "reason": null}], "unknowns": {"data": []}}
```

</details>

<a id="c14"></a>

<details>
<summary>C14 — restart; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
import json,os,signal,time
from pathlib import Path
from coordinator.client import Client,CoordinatorError
from coordinator.processes import identity
root=Path("/private/tmp/c1-rework-3gcqdxau"); states=[Path("/private/tmp/c1-rework-3gcqdxau/ssssssssssss"),Path("/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state")]
out=[]
for state in states:
 c=Client(state); before=c.call("daemon/status",{})
 actual=identity(before["pid"])
 if actual["birth"] != before["birth"]: raise RuntimeError("identity changed")
 os.kill(before["pid"],signal.SIGKILL)
 deadline=time.monotonic()+120
 while time.monotonic()<deadline:
  try:
   after=c.call("daemon/status",{})
   if after["ready"] and after["generation"]>before["generation"]: break
  except (CoordinatorError,OSError): pass
  time.sleep(.2)
 else: raise RuntimeError("restart timed out")
 out.append({"state":str(state),"before":{k:before[k] for k in ("pid","birth","generation","endpoint")},"after":{k:after[k] for k in ("pid","birth","generation","endpoint","locks")}})
print(json.dumps(out))
(root/"restart.json").write_text(json.dumps(out,indent=2))
PY
```

Captured output:

```text
[{"state": "/private/tmp/c1-rework-3gcqdxau/ssssssssssss", "before": {"pid": 12054, "birth": "1792325512:296854", "generation": 1, "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/siyj_7ei8/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/siyj_7ei8/s", "resolved_bytes": 31, "uid": 501}}, "after": {"pid": 26414, "birth": "1792325955:284272", "generation": 2, "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/swcwf8c8m/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/swcwf8c8m/s", "resolved_bytes": 31, "uid": 501}, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}}}, {"state": "/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state", "before": {"pid": 12058, "birth": "1792325512:519366", "generation": 1, "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/su_tg08bi/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/su_tg08bi/s", "resolved_bytes": 31, "uid": 501}}, "after": {"pid": 26452, "birth": "1792325955:921377", "generation": 2, "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/swhy1lu2q/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/swhy1lu2q/s", "resolved_bytes": 31, "uid": 501}, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}}}]
```

</details>

<a id="c15"></a>

<details>
<summary>C15 — native-attribution; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time
from coordinator.client import Client
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"setup.json").read_text()); c=Client("/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state",timeout=1800)
def start(name,fixture): return c.call("run/start",{"checkout":setup["checkouts"]["long"],"mode":"native","task":name,"fixture":str(root/fixture),"model":"gpt-6-astra"})
with ThreadPoolExecutor(3) as pool:
 jobs=[pool.submit(start,name,fixture) for name,fixture in [("C1_NATIVE_A","fixture"),("C1_NATIVE_B","fixture"),("C1_CHILD_ROOT","fixture-more")]]
 runs=[f.result() for f in jobs]
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 all_runs=c.call("records/list",{"kind":"run","limit":1000})["data"]
 selected=[r for r in all_runs if r["id"] in {x["run"] for x in runs}]
 if len(selected)==3 and all(r["state"]=="completed" for r in selected): break
 time.sleep(.2)
out={"runs":runs,"states":selected}
for kind in ("actor","operation","custody","listener"):
 out[kind]=[r for r in c.call("records/list",{"kind":kind,"limit":1000})["data"] if r.get("unit") in {x["unit"] for x in runs}]
out["unknown"]=c.call("unknown/list",{"checkout":setup["checkouts"]["long"]})
(root/"native-attribution.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"runs":runs,"states":[r["state"] for r in selected],"actors":[{"kind":a["kind"],"id":a["id"],"parent":a.get("parent")} for a in out["actor"]],"operations":[{"kind":o["kind"],"state":o["state"],"actor":o["actor"],"refusal":o.get("refusal")} for o in out["operation"]],"custody":[{"state":r["state"],"reason":r.get("reason")} for r in out["custody"]],"unknown":out["unknown"]}))
PY
```

Captured output:

```text
{"runs": [{"actor": "actor_30a55010968c48ccab848b6f33c9d83b", "attempt": "attempt_16716cea155e4a51836ac395aa3e3480", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_b0b7646031a145febc956c65869ddc89", "unit": "unit_d555cf6801504512b1c398d27f7ec612"}, {"actor": "actor_9fb78915a7674259b381373b6d054ad3", "attempt": "attempt_6bac720f2ce44f8286274643ef6f97ca", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_5f2c30433ff34f0cae5e14e98496a77d", "unit": "unit_72e26012fa4b4281a87bc6af8eab364a"}, {"actor": "actor_3daad238ca0f40bfb782ff78b21bb54c", "attempt": "attempt_a28571d0c77340f7aa005c4265a60fc5", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_014890b9e82240509a315a2b978f05b1", "unit": "unit_33aed2fee6764714bd785893da69cca1"}], "states": ["completed", "completed", "completed"], "actors": [{"kind": "root", "id": "actor_30a55010968c48ccab848b6f33c9d83b", "parent": null}, {"kind": "root", "id": "actor_3daad238ca0f40bfb782ff78b21bb54c", "parent": null}, {"kind": "child", "id": "actor_9895f5af50904ab1a9018fb775fe4e1c", "parent": null}, {"kind": "root", "id": "actor_9fb78915a7674259b381373b6d054ad3", "parent": null}], "operations": [{"kind": "patch", "state": "verified", "actor": "actor_30a55010968c48ccab848b6f33c9d83b", "refusal": null}, {"kind": "patch", "state": "verified", "actor": "actor_9895f5af50904ab1a9018fb775fe4e1c", "refusal": null}, {"kind": "patch", "state": "verified", "actor": "actor_9fb78915a7674259b381373b6d054ad3", "refusal": null}], "custody": [], "unknown": {"data": []}}
```

</details>

<a id="c16"></a>

<details>
<summary>C16 — custody; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 SOFTWARE_WORKFLOWS_PYTHON=/opt/homebrew/opt/python@3.15/bin/python3.15 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import base64,json,time
from coordinator.client import Client,CoordinatorError
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"setup.json").read_text()); c=Client("/private/tmp/c1-rework-3gcqdxau/ssssssssssss",timeout=120); checkout=setup["checkouts"]["custody"]
actors=[c.call("run/start",{"checkout":checkout,"mode":"lead","task":"custody observation "+str(i),"paths":(["a.txt"] if i==0 else [])}) for i in range(4)]
def edit(text):
 return c.call("lead-edit",{"actor":actors[0]["actor"],"changes":{"a.txt":{"mode":"100644","bytes_base64":base64.b64encode(text.encode()).decode()}}})
def active():
 return [r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["checkout"]==checkout and r["state"]!="released"]
out={"actors":actors,"steps":[]}
with ThreadPoolExecutor(4) as pool:
 for actor in actors[1:3]: c.call("command/declare",{"actor":actor["actor"],"command":"sleep 4","reason":"sleep has no file effects"})
 jobs=[pool.submit(c.call,"lead-command",{"actor":a["actor"],"argv":["sleep","4"]}) for a in actors[1:3]]
 deadline=time.monotonic()+10
 while len(active())<2 and time.monotonic()<deadline: time.sleep(.02)
 out["steps"].append({"during_shared":active(),"receipt":edit("patch during shared\n")})
 ex=pool.submit(c.call,"lead-command",{"actor":actors[3]["actor"],"argv":["sleep","3"]})
 time.sleep(.3)
 out["steps"].append({"exclusive_waiting":not ex.done(),"during":active()})
 out["steps"].append({"shared_results":[j.result() for j in jobs]})
 deadline=time.monotonic()+10
 while not any(r["level"]=="exclusive" for r in active()) and time.monotonic()<deadline: time.sleep(.02)
 out["steps"].append({"exclusive_exclusion":active(),"receipt":edit("patch on prior claim excluded from exclusive\n")})
 out["steps"].append({"exclusive_result":ex.result()})
 changes={"a.txt":{"mode":"100644","bytes_base64":base64.b64encode(b"prepared patch then command\n").decode()}}
 prepared=c.call("operation/prepare",{"actor":actors[0]["actor"],"kind":"lead-edit","changes":changes})
 ex=pool.submit(c.call,"lead-command",{"actor":actors[3]["actor"],"argv":["sleep","2"]})
 deadline=time.monotonic()+10
 while not active() and time.monotonic()<deadline: time.sleep(.02)
 Path(setup["repos"]["custody"],"a.txt").write_bytes(b"prepared patch then command\n")
 out["steps"].append({"patch_first_exclusive_exclusion":active(),"receipt":c.call("operation/complete",{"operation":prepared["id"]})})
 out["steps"].append({"result":ex.result()})
 ex=pool.submit(c.call,"lead-command",{"actor":actors[3]["actor"],"argv":["sleep","2"]})
 deadline=time.monotonic()+10
 while not active() and time.monotonic()<deadline: time.sleep(.02)
 try: c.call("claim/acquire",{"actor":actors[0]["actor"],"paths":["b.txt"]})
 except CoordinatorError as error: out["new_claim_during_exclusive"]={"code":error.code,"details":error.details}
 out["steps"].append({"result":ex.result()})
 c.call("command/declare",{"actor":actors[1]["actor"],"command":"sleep 1","reason":"read only sleep"})
 prepared=c.call("operation/prepare",{"actor":actors[0]["actor"],"kind":"lead-edit","changes":{"a.txt":{"mode":"100644","bytes_base64":base64.b64encode(b"patch first shared\n").decode()}}})
 shared=pool.submit(c.call,"lead-command",{"actor":actors[1]["actor"],"argv":["sleep","1"]})
 time.sleep(.1); Path(setup["repos"]["custody"],"a.txt").write_bytes(b"patch first shared\n")
 out["steps"].append({"patch_first_shared":active(),"receipt":c.call("operation/complete",{"operation":prepared["id"]}),"command":shared.result()})
out["unknown"]=c.call("unknown/list",{"checkout":checkout})
(root/"custody.json").write_text(json.dumps(out,indent=2))
print(json.dumps(out))
PY
```

Captured output:

```text
Warning: truncated output (original token count: 2888)
Total output lines: 1

{"actors": [{"actor": "actor_9034b704b20849eaaa111d8cddebca92", "attempt": "attempt_a454d99ba06443f59c83416cc78e748e", "policy": null, "run": "run_f7760a9ed3854862ac23ade5744c55d5", "unit": "unit_06cdf86c96a548cfb66ff906b5f6dc94"}, {"actor": "actor_b94872b3cbd24eceb0e2b4e1dde49170", "attempt": "attempt_e78b851246cf4e2d808c58ab8e77d9ba", "policy": null, "run": "run_eccec16232ad4e469b18780bb528a868", "unit": "unit_22b749fe38934fc2be28a2138e11eafa"}, {"actor": "actor_871b7bf96dde4e6ebe6cb479c75b2b47", "attempt": "attempt_06c1caabdd3b4342a65d801261ba832d", "policy": null, "run": "run_ab6c6069b9524bb499c9211e079ac201", "unit": "unit_9d346aae52a74d1ba3ed50807fc14091"}, {"actor": "actor_e16dedc385b845e08639455ad5e1324e", "attempt": "attempt_35059dfcf1a54166bfd3eb5566ce1541", "policy": null, "run": "run_d1d5a1219ea54f95a1f6c78038aef6db", "unit": "unit_ec75b373b5404de6959256a9940dc0c3"}], "steps": [{"during_shared": [{"_kind": "custody", "_revision": 2, "actor": "actor_871b7bf96dde4e6ebe6cb479c75b2b47", "checkout": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "command": "sleep 4", "created": 1792326022.920629, "excludes": ["a.txt"], "generation": 2, "id": "custody_133bd39613da41b2ad7650ec5958ed88", "lead_root": {"birth": "1792326022:920876", "pid": 28962, "ppid": 26414, "uid": 501, "zombie": false}, "level": "shared", "operation": "op_5bffe9b0b7b9453592ac01e4629b31e1", "processes": [{"birth": "1792326022:920876", "pid": 28962, "ppid": 26414, "uid": 501, "zombie": false}], "state": "held", "terminals": [], "unit": "unit_9d346aae52a74d1ba3ed50807fc14091"}, {"_kind": "custody", "_revision": 1, "actor": "actor_b94872b3cbd24eceb0e2b4e1dde49170", "checkout": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "command": "sleep 4", "created": 1792326022.922936, "excludes": ["a.txt"], "generation": 2, "id": "custody_9c080babef0542d3a35099ea8f2c08b6", "level": "shared", "operation": "op_48b68c43aeeb48c7ad647c1495b557c3", "processes": [], "state": "held", "terminals": [], "unit": "unit_22b749fe38934fc2be28a2138e11eafa"}], "receipt": {"_kind": "operation", "_revision": 1, "actor": "actor_9034b704b20849eaaa111d8cddebca92", "attempt": "attempt_a454d99ba06443f59c83416cc78e748e", "bases": {"a.txt": "rev_7b6c16fbe3784a46b16d7c1fb1920d5c"}, "checkout": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "completed": 1792326022.926998, "created": 1792326022.925895, "dependencies": [], "evidence": {"executor": "coordinator"}, "generation": 2, "hook_valid": false, "id": "op_2ea405550ed6496da06c32bbf6e0f0a1", "intended": {"expected": {"a.txt": {"blob": "e7769c743bf995d55d4356bfa5336b87400dbfc039b6012d1350f30cec611392", "mode": "100644"}}}, "kind": "lead-edit", "native": {}, "paths": ["a.txt"], "pre": {"a.txt": {"blob": "2aebf70005cda6930e8ad8323385d25e364a35d459751143bddceb53526cbb26", "mode": "100644"}}, "result": {"a.txt": {"blob": "e7769c743bf995d55d4356bfa5336b87400dbfc039b6012d1350f30cec611392", "mode": "100644"}}, "revisions": {"a.txt": "rev_441bea107c1c485185f696800068379b"}, "state": "verified", "unit": "unit_06cdf86c96a548cfb66ff906b5f6dc94"}}, {"exclusive_waiting": true, "during": [{"_kind": "custody", "_revision": 2, "actor": "actor_871b7bf96dde4e6ebe6cb479c75b2b47", "checkout": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "command": "sleep 4", "created": 1792326022.920629, "excludes": ["a.txt"], "generation": 2, "id": "custody_133bd39613da41b2ad7650ec5958ed88", "lead_root": {"birth": "1792326022:920876", "pid": 28962, "ppid": 26414, "uid": 501, "zombie": false}, "level": "shared", "operation": "op_5bffe9b0b7b9453592ac01e4629b31e1", "processes": [{"birth": "1792326022:920876", "pid": 28962, "ppid": 26414, "uid": 501, "zombie": false}], "state": "held", "terminals": [], "unit": "unit_9d346aae52a74d1ba3ed50807fc14091"}, {"_kind": "custody", "_revision": 2, "actor": "actor_b94872b3cbd24eceb0e2b4e1dde49170", "checkout": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "command": "sleep 4", "created": 1792326022.922936, "excludes": ["a.txt"], "generation": 2, "id": "custody_9c080babef0542d3a35099ea8f2c08b6", "lead_root": {"birth": "1792326022:923148", "pid": 28963, "ppid": 26414, "uid": 501, "zombie": false}, "level": "shared", "operation": "op_48b68c43aeeb48c7ad647c1495b557c3", "processes": [{"birth": "1792326022:923148", "pid": 28963, "ppid": 26414, "uid": 501, "zombie": false}], "state": "held", "terminals": [], "unit": "unit_22b749fe38934fc2be28a2138e11eafa"}]}, {"shared_results": [{"custody": "released", "exit_code": 0, "operation": "op_48b68c43aeeb48c7ad647c1495b557c3", "stderr_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "stdout_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}, {"custody": "released", "exit_code": 0, "operation": "op_5bffe9b0b7b9453592ac01e4629b31e1", "stderr_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "stdout_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}]}, {"exclusive_exclusion": [{"_kind": "custody",…388 tokens truncated…"rev_2180fa3a3ed54e78a28d2f99db955aca"}, "state": "verified", "unit": "unit_06cdf86c96a548cfb66ff906b5f6dc94"}}, {"exclusive_result": {"custody": "released", "exit_code": 0, "operation": "op_1d7a3a5c1b0d4ba28fae8372bcf01275", "stderr_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "stdout_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}}, {"patch_first_exclusive_exclusion": [{"_kind": "custody", "_revision": 2, "actor": "actor_e16dedc385b845e08639455ad5e1324e", "checkout": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "command": "sleep 2", "created": 1792326030.061934, "excludes": ["a.txt"], "generation": 2, "id": "custody_cf82335b12ae4298a279efb8707e182a", "lead_root": {"birth": "1792326030:62241", "pid": 29577, "ppid": 26414, "uid": 501, "zombie": false}, "level": "exclusive", "operation": "op_46ffb9df7f534064b701ba889c754c70", "processes": [{"birth": "1792326030:62241", "pid": 29577, "ppid": 26414, "uid": 501, "zombie": false}], "state": "held", "terminals": [], "unit": "unit_ec75b373b5404de6959256a9940dc0c3"}], "receipt": {"_kind": "operation", "_revision": 1, "actor": "actor_9034b704b20849eaaa111d8cddebca92", "attempt": "attempt_a454d99ba06443f59c83416cc78e748e", "bases": {"a.txt": "rev_2180fa3a3ed54e78a28d2f99db955aca"}, "checkout": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "completed": 1792326030.093052, "created": 1792326030.059325, "dependencies": [], "evidence": {"executor": "lead-editor"}, "generation": 2, "hook_valid": false, "id": "op_f24a45fb80474d4794a8c632e21fe148", "intended": {"expected": {"a.txt": {"blob": "1a3220467f2b0e2bfd6ac4c14b0b1d454b4894cbf21a330e883830e04be540be", "mode": "100644"}}}, "kind": "lead-edit", "native": {}, "paths": ["a.txt"], "pre": {"a.txt": {"blob": "044bf6dce7733672fb1a25e3831b0bf613973b95a1562c2287fb033aa642fcd5", "mode": "100644"}}, "result": {"a.txt": {"blob": "1a3220467f2b0e2bfd6ac4c14b0b1d454b4894cbf21a330e883830e04be540be", "mode": "100644"}}, "revisions": {"a.txt": "rev_1ccbcb5507d94819820a27ffe91d4200"}, "state": "verified", "unit": "unit_06cdf86c96a548cfb66ff906b5f6dc94"}}, {"result": {"custody": "released", "exit_code": 0, "operation": "op_46ffb9df7f534064b701ba889c754c70", "stderr_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "stdout_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}}, {"result": {"custody": "released", "exit_code": 0, "operation": "op_84752d70f1eb42549128d890526dc927", "stderr_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "stdout_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}}, {"patch_first_shared": [{"_kind": "custody", "_revision": 2, "actor": "actor_b94872b3cbd24eceb0e2b4e1dde49170", "checkout": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "command": "sleep 1", "created": 1792326034.18592, "excludes": ["a.txt"], "generation": 2, "id": "custody_ed04a76bb7894fc4b076295b3aa34a34", "lead_root": {"birth": "1792326034:186232", "pid": 29930, "ppid": 26414, "uid": 501, "zombie": false}, "level": "shared", "operation": "op_6dce6b80ca544b59940920c9de51f031", "processes": [{"birth": "1792326034:186232", "pid": 29930, "ppid": 26414, "uid": 501, "zombie": false}], "state": "held", "terminals": [], "unit": "unit_22b749fe38934fc2be28a2138e11eafa"}], "receipt": {"_kind": "operation", "_revision": 1, "actor": "actor_9034b704b20849eaaa111d8cddebca92", "attempt": "attempt_a454d99ba06443f59c83416cc78e748e", "bases": {"a.txt": "rev_1ccbcb5507d94819820a27ffe91d4200"}, "checkout": "checkout_06fcf9b4e06f4ec9a5dea67c2bf87c84", "completed": 1792326034.295635, "created": 1792326034.183993, "dependencies": [], "evidence": {"executor": "lead-editor"}, "generation": 2, "hook_valid": false, "id": "op_2aa4e13be3954b4c846faa6206d58c3a", "intended": {"expected": {"a.txt": {"blob": "549d58df254785f8f1203969ef9efa784ef91644b5ea40e1eab478848779164c", "mode": "100644"}}}, "kind": "lead-edit", "native": {}, "paths": ["a.txt"], "pre": {"a.txt": {"blob": "1a3220467f2b0e2bfd6ac4c14b0b1d454b4894cbf21a330e883830e04be540be", "mode": "100644"}}, "result": {"a.txt": {"blob": "549d58df254785f8f1203969ef9efa784ef91644b5ea40e1eab478848779164c", "mode": "100644"}}, "revisions": {"a.txt": "rev_4dc23b842dae49848e6a4ea3c4fa270f"}, "state": "verified", "unit": "unit_06cdf86c96a548cfb66ff906b5f6dc94"}, "command": {"custody": "released", "exit_code": 0, "operation": "op_6dce6b80ca544b59940920c9de51f031", "stderr_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "stdout_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}}], "new_claim_during_exclusive": {"code": "CLAIM_CONFLICT", "details": {"conflicts": [{"holder": "unit_ec75b373b5404de6959256a9940dc0c3", "kind": "custody", "reservation": "custody_a234b85c25a245e29a7b71f8395b8c41", "scope": "checkout minus existing other claims"}], "request": "request_d7ea9ee34843496dbe83b0a134d5b441"}}, "unknown": {"data": []}}
```

</details>

<a id="c17"></a>

<details>
<summary>C17 — interrupt-setup; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 SOFTWARE_WORKFLOWS_PYTHON=/opt/homebrew/opt/python@3.15/bin/python3.15 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,subprocess,time,base64
from coordinator.client import Client
root=Path("/private/tmp/c1-rework-3gcqdxau"); repo=root/"interrupt-repo"; repo.mkdir(mode=0o700); state=root/"interrupt-state"
def git(*args,data=None,env=None):
 import os
 r=subprocess.run(["git","-C",str(repo),*args],input=data,capture_output=True,env=dict(os.environ,**(env or {})))
 if r.returncode: raise RuntimeError((args,r.returncode,r.stderr.decode()))
 return r.stdout.decode().strip()
git("init","-q")
payload=b"corpus baseline\n"
for i in range(10000): (repo/("file%05d.txt"%i)).write_bytes(payload)
oid=git("hash-object","-w","--stdin",data=payload)
git("read-tree","--empty")
git("update-index","--index-info",data="".join("100644 "+oid+"\tfile%05d.txt\n"%i for i in range(10000)).encode())
tree=git("write-tree"); env={k:v for k,v in {"GIT_AUTHOR_NAME":"C1 Fixture","GIT_AUTHOR_EMAIL":"c1@example.invalid","GIT_AUTHOR_DATE":"2026-10-18T12:30:00+00:00","GIT_COMMITTER_NAME":"C1 Fixture","GIT_COMMITTER_EMAIL":"c1@example.invalid","GIT_COMMITTER_DATE":"2026-10-18T12:30:00+00:00"}.items()}
commit=git("commit-tree",tree,data=b"corpus baseline\n",env=env); git("update-ref","HEAD",commit); git("read-tree",commit)
c=Client(state,timeout=1800); status=c.ensure()
start=time.perf_counter(); checkout=c.call("checkout/register",{"path":str(repo)}); baseline=time.perf_counter()-start
start=time.perf_counter(); full=c.call("checkout/reconcile",{"checkout":checkout["id"]}); full_seconds=time.perf_counter()-start
start=time.perf_counter(); scoped=c.call("checkout/reconcile",{"checkout":checkout["id"],"paths":["file00000.txt"]}); scoped_seconds=time.perf_counter()-start
run=c.call("run/start",{"checkout":checkout["id"],"mode":"lead","task":"observed crash boundaries","paths":["file00000.txt"]})
receipt=c.call("lead-edit",{"actor":run["actor"],"changes":{"file00000.txt":{"mode":"100644","bytes_base64":base64.b64encode(b"interruption value 1\n").decode()}}})
out={"state":str(state),"repo":str(repo),"checkout":checkout["id"],"run":run,"receipt":receipt,"baseline_seconds":baseline,"full":{"result":full,"seconds":full_seconds},"scoped":{"result":scoped,"seconds":scoped_seconds},"time":time.time()}
(root/"interrupt-setup.json").write_text(json.dumps(out,indent=2)); print(json.dumps(out))
PY
```

Captured output:

```text
{"state": "/private/tmp/c1-rework-3gcqdxau/interrupt-state", "repo": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo", "checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "run": {"actor": "actor_74aa9fbaa1c64bb1a1ec7c10bdbec8a3", "attempt": "attempt_20511d33579a4a768db284cb0e8e74f8", "policy": null, "run": "run_a009346077bb46089275c39d3c4001cc", "unit": "unit_c4d9a49d3dd845e68d76edf8189ac6b4"}, "receipt": {"_kind": "operation", "_revision": 1, "actor": "actor_74aa9fbaa1c64bb1a1ec7c10bdbec8a3", "attempt": "attempt_20511d33579a4a768db284cb0e8e74f8", "bases": {"file00000.txt": "rev_13c581579e104fbc85b6e007ce760ab4"}, "checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "completed": 1792326050.810765, "created": 1792326050.8097112, "dependencies": [], "evidence": {"executor": "coordinator"}, "generation": 1, "hook_valid": false, "id": "op_3ea486f5822244758c43d9a20b191dbc", "intended": {"expected": {"file00000.txt": {"blob": "59da03af5b3cf10a64d826d3a5625212f63c4284eb3671978017753db71e7a99", "mode": "100644"}}}, "kind": "lead-edit", "native": {}, "paths": ["file00000.txt"], "pre": {"file00000.txt": {"blob": "ac3f1874bd8805f3b0e725e82c1abe27beaec10e0e9d2088f5bf6f588bd128d2", "mode": "100644"}}, "result": {"file00000.txt": {"blob": "59da03af5b3cf10a64d826d3a5625212f63c4284eb3671978017753db71e7a99", "mode": "100644"}}, "revisions": {"file00000.txt": "rev_c16504eeca624490bcc1c781c6cce277"}, "state": "verified", "unit": "unit_c4d9a49d3dd845e68d76edf8189ac6b4"}, "baseline_seconds": 2.496449250000296, "full": {"result": {"paths_scanned": 10000, "unknowns": []}, "seconds": 1.7544768750085495}, "scoped": {"result": {"paths_scanned": 1, "unknowns": []}, "seconds": 0.0008642910106573254}, "time": 1792326050.810951}
```

</details>

<a id="c18"></a>

<details>
<summary>C18 — mid-seal; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,os,signal,sqlite3,time
from coordinator.client import Client,CoordinatorError
from coordinator.processes import identity
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"interrupt-setup.json").read_text()); state=Path(setup["state"]); unit=setup["run"]["unit"]; c=Client(state,timeout=120)
db=sqlite3.connect("file:"+str(state/"ledger.sqlite")+"?mode=ro",uri=True,isolation_level=None)
def record(ident):
 row=db.execute("SELECT data FROM records WHERE id=?",(ident,)).fetchone()
 return json.loads(row[0]) if row else None
before=c.call("daemon/status",{})
with ThreadPoolExecutor(1) as pool:
 pending=pool.submit(c.call,"unit/seal",{"unit":unit,"validation":[]},key="observed-mid-seal")
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  observed=record(unit)
  if observed["state"]=="sealing":
   if identity(before["pid"])["birth"]!=before["birth"]: raise RuntimeError("worker identity changed")
   os.kill(before["pid"],signal.SIGSTOP)
   stopped=record(unit); attempt=record(stopped["seal_attempt"])
   if stopped["state"]=="sealing" and attempt["state"]=="started": break
   os.kill(before["pid"],signal.SIGCONT)
  time.sleep(.0005)
 else: raise RuntimeError("seal checkpoint not caught")
 checkpoint={"time":time.time(),"worker":{k:before[k] for k in ("pid","birth","generation")},"unit":stopped,"attempt":attempt}
 (root/"mid-seal-checkpoint.json").write_text(json.dumps(checkpoint,indent=2))
 os.kill(before["pid"],signal.SIGKILL)
 try: outcome=pending.result()
 except Exception as exc: outcome={"error":type(exc).__name__,"code":getattr(exc,"code",None)}
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>before["generation"]: break
 except (CoordinatorError,OSError): pass
 time.sleep(.1)
else: raise RuntimeError("recovery timed out")
recovered=record(unit); attempt=record(recovered["seal_attempt"])
claims=[json.loads(r[0]) for r in db.execute("SELECT data FROM records WHERE kind='claim' AND parent=?",(unit,))]
try: c.call("unit/seal",{"unit":unit,"validation":[]},key="observed-mid-seal")
except CoordinatorError as exc: retry={"code":exc.code}
seal=c.call("unit/seal",{"unit":unit,"validation":[]})
diff=c.call("unit/diff",{"unit":unit})
out={"checkpoint":checkpoint,"rpc_outcome":outcome,"after":{k:after[k] for k in ("pid","birth","generation")},"recovered":recovered,"attempt":attempt,"claims":claims,"retry":retry,"new_seal":seal,"diff":diff}
(root/"mid-seal.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"checkpoint":checkpoint,"rpc_outcome":outcome,"recovered":recovered,"attempt":attempt,"claim_states":[x["state"] for x in claims],"same_key_retry":retry,"new_seal":seal["id"],"diff":diff}))
PY
```

Captured output:

```text
{"checkpoint": {"time": 1792326085.2222762, "worker": {"pid": 30833, "birth": "1792326044:690091", "generation": 1}, "unit": {"base": "8c2e2644dd8013baa734cd50cb7955cdb20f0aec", "branch": "refs/heads/master", "checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "run": "run_a009346077bb46089275c39d3c4001cc", "seal_attempt": "seal_attempt_5b706852bc114cefba9814d62808da9f", "state": "sealing"}, "attempt": {"created": 1792326085.221774, "generation": 1, "index": "/private/tmp/c1-rework-3gcqdxau/interrupt-state/indexes/seal_attempt_5b706852bc114cefba9814d62808da9f.index", "intended_base": "8c2e2644dd8013baa734cd50cb7955cdb20f0aec", "prior_seal": null, "prior_state": "open", "state": "started", "unit": "unit_c4d9a49d3dd845e68d76edf8189ac6b4", "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}}, "rpc_outcome": {"error": "CoordinatorError", "code": "RPC_UNCERTAIN"}, "recovered": {"base": "8c2e2644dd8013baa734cd50cb7955cdb20f0aec", "branch": "refs/heads/master", "checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "review_required": true, "run": "run_a009346077bb46089275c39d3c4001cc", "seal_attempt": "seal_attempt_5b706852bc114cefba9814d62808da9f", "state": "open"}, "attempt": {"created": 1792326085.221774, "generation": 1, "index": "/private/tmp/c1-rework-3gcqdxau/interrupt-state/indexes/seal_attempt_5b706852bc114cefba9814d62808da9f.index", "intended_base": "8c2e2644dd8013baa734cd50cb7955cdb20f0aec", "prior_seal": null, "prior_state": "open", "reason": "worker interrupted sealing", "recovered_generation": 2, "state": "interrupted", "unit": "unit_c4d9a49d3dd845e68d76edf8189ac6b4", "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "claim_states": ["held"], "same_key_retry": {"code": "RPC_UNCERTAIN"}, "new_seal": "seal_62dbfe0a93c24e268e482c3127acbf36", "diff": {"binding": {"base": "8c2e2644dd8013baa734cd50cb7955cdb20f0aec", "candidate": "284fcf26599604a2696232b0d0118c2ff0c6e4dd", "provenance": "983a0779a0368a41df7c8e7c14036f2b0f92eb325a68e9f6cc33a68bb3e751d9", "repository": "5ae0f713c12a507f76a8081d0ec96074640d894b9546788f8555eb109ef07340", "seal": "seal_62dbfe0a93c24e268e482c3127acbf36", "unit": "unit_c4d9a49d3dd845e68d76edf8189ac6b4", "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "diff": "diff --git a/file00000.txt b/file00000.txt\nindex 74ccd65..18d0152 100644\n--- a/file00000.txt\n+++ b/file00000.txt\n@@ -1 +1 @@\n-corpus baseline\n+interruption value 1\n"}}
```

</details>

<a id="c19"></a>

<details>
<summary>C19 — provider3; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B -m coordinator fixture serve --directory '/private/tmp/c1-rework-3gcqdxau/fixture-parallel' --plan '{"default":[{"text":"idle"}],"routes":{"C1_PAR_A":[{"delay":2,"tool":"declare","arguments":{"command":"sleep 4","reason":"wait without filesystem effects","level":"shared"}},{"command":"sleep 4","arguments":{"yield_time_ms":1000}},{"patch":"*** Begin Patch\n*** Add File: parallel_a.py\n+def parallel_a(value):\n+    return value + 3\n*** End Patch"},{"text":"parallel A done"}],"C1_PAR_B":[{"delay":2,"tool":"declare","arguments":{"command":"sleep 4","reason":"wait without filesystem effects","level":"shared"}},{"command":"sleep 4","arguments":{"yield_time_ms":1000}},{"patch":"*** Begin Patch\n*** Add File: parallel_b.py\n+def parallel_b(value):\n+    return value * 3\n*** End Patch"},{"text":"parallel B done"}],"C1_SURVIVOR":[{"command":"sleep 15 & wait","arguments":{"yield_time_ms":1000}},{"text":"turn finished with background process"}],"C1_HOOKS":[{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"text":"hook timing completed"}]}}'
```

Captured output:

```text
{"fixture": "/private/tmp/c1-rework-3gcqdxau/fixture-parallel", "pid": 33912, "port": 55296}
```

</details>

<a id="c20"></a>

<details>
<summary>C20 — owned-lock; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,os,signal,sqlite3,subprocess,time,hashlib
from coordinator.client import Client,CoordinatorError
from coordinator.processes import identity
from coordinator.store import digest
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"interrupt-setup.json").read_text()); previous=json.loads((root/"mid-seal.json").read_text()); c=Client(setup["state"],timeout=120); unit=setup["run"]["unit"]; repo=Path(setup["repo"])
def git(*args,data=None):
 r=subprocess.run(["git","-C",str(repo),*args],input=data,capture_output=True)
 if r.returncode: raise RuntimeError((args,r.returncode,r.stderr.decode()))
 return r.stdout
answer="Inspected the complete object diff: only file00000.txt changes from corpus baseline to interruption value 1."
evidence=dict(previous["diff"]["binding"],verdict="pass",completed=True,answer=answer,answer_digest=hashlib.sha256(answer.encode()).hexdigest(),review_identity="C1 direct object inspection 2026-10-18")
review=c.call("unit/review-record",{"unit":unit,"evidence":evidence})
oid=git("hash-object","-w","--stdin",data=b"unrelated staged bytes\n").decode().strip()
git("update-index","--index-info",data=("100644 "+oid+"\tfile09999.txt\n").encode())
unrelated_before=git("ls-files","--stage","--","file09999.txt").decode()
db=sqlite3.connect("file:"+str(Path(setup["state"])/"ledger.sqlite")+"?mode=ro",uri=True,isolation_level=None)
def publication():
 row=db.execute("SELECT id,data FROM records WHERE kind='publication' AND parent=?",(unit,)).fetchone()
 return dict(json.loads(row[1]),id=row[0]) if row else None
def held(p):
 if not p or p["phase"]!="index_pending" or "index_lock" not in p: return False
 lock=p["index_lock"]
 try: return (os.stat(lock["lock"]).st_dev,os.stat(lock["lock"]).st_ino)==(lock["device"],lock["inode"]) and os.stat(lock["anchor"]).st_ino==lock["inode"]
 except FileNotFoundError:return False
before=c.call("daemon/status",{}); metadata=json.loads((root/"setup.json").read_text())["metadata"]
with ThreadPoolExecutor(1) as pool:
 pending=pool.submit(c.call,"unit/publish",{"unit":unit,"review":review["review"],"metadata":metadata},key="owned-index-crash")
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  observed=publication()
  if held(observed):
   if identity(before["pid"])["birth"]!=before["birth"]: raise RuntimeError("identity changed")
   os.kill(before["pid"],signal.SIGSTOP); stopped=publication()
   if held(stopped): break
   os.kill(before["pid"],signal.SIGCONT)
  if pending.done(): raise RuntimeError("owned index lock window missed")
  time.sleep(.0005)
 else: raise RuntimeError("lock checkpoint not caught")
 checkpoint={"time":time.time(),"worker":{k:before[k] for k in ("pid","birth","generation")},"publication":stopped,"head":git("rev-parse","HEAD").decode().strip(),"unrelated_before":unrelated_before}
 (root/"owned-lock-checkpoint.json").write_text(json.dumps(checkpoint,indent=2))
 os.kill(before["pid"],signal.SIGKILL)
 try: outcome=pending.result()
 except Exception as exc: outcome={"error":type(exc).__name__,"code":getattr(exc,"code",None)}
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>before["generation"]:break
 except (CoordinatorError,OSError):pass
 time.sleep(.1)
else:raise RuntimeError("recovery timeout")
final=publication(); unrelated_after=git("ls-files","--stage","--","file09999.txt").decode()
out={"checkpoint":checkpoint,"rpc_outcome":outcome,"after":{k:after[k] for k in ("pid","birth","generation")},"final":final,"unrelated_after":unrelated_after,"unrelated_preserved":unrelated_before==unrelated_after,"commit_count":git("rev-list","--count","HEAD").decode().strip(),"head":git("rev-parse","HEAD").decode().strip(),"lock_exists":Path(stopped["index_lock"]["lock"]).exists(),"anchor_exists":Path(stopped["index_lock"]["anchor"]).exists(),"claims":[r for r in c.call("records/list",{"kind":"claim","limit":1000})["data"] if r["unit"]==unit]}
(root/"owned-lock.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"checkpoint": {"time": 1792326202.516785, "worker": {"pid": 33264, "birth": "1792326085:477693", "generation": 2}, "publication": {"base_index_entries": {"file00000.txt": [{"mode": "100644", "oid": "74ccd65945e21968a328f6fa8ec51ef9cc2bf0a3", "stage": "0"}]}, "commit": "0749ebf6b983c1f7d324dc4fc145486909fa6b14", "config_digest": "3dab0b9b9edcc59256ce5632d7c85fcc832e5f334d2e6705256789a6bb9d3fc6", "created": 1792326200.683742, "index_lock": {"anchor": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/c1-index_owner_de9e13140c7e488185f1c5bb7e2618f3", "checkout_identity": "a4247cb6aded55d4131105fd2aa4ff7e6091cc8b4cf74ad8a5cbce2836428996", "device": 16777230, "generation": 2, "index": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index", "inode": 748326603, "lock": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index.lock", "owner": {"birth": "1792326085:477693", "pid": 33264, "ppid": 30832, "uid": 501, "zombie": false}, "publication": "publication_3abfc254924847478278560ea38136a4", "state": "recorded", "token": "index_owner_de9e13140c7e488185f1c5bb7e2618f3"}, "metadata": {"author_date": "2026-10-18T12:30:00+00:00", "author_email": "c1@example.invalid", "author_name": "C1 Fixture", "committer_date": "2026-10-18T12:30:00+00:00", "committer_email": "c1@example.invalid", "committer_name": "C1 Fixture", "message": "C1 observed publication"}, "phase": "index_pending", "review": "review_b29713a57db34c5fa67079509866413f", "seal": "seal_62dbfe0a93c24e268e482c3127acbf36", "unit": "unit_c4d9a49d3dd845e68d76edf8189ac6b4", "id": "publication_3abfc254924847478278560ea38136a4"}, "head": "0749ebf6b983c1f7d324dc4fc145486909fa6b14", "unrelated_before": "100644 ea9532f4bb6411ac8e2d22b97ec47ec7e82d5933 0\tfile09999.txt\n"}, "rpc_outcome": {"error": "CoordinatorError", "code": "RPC_UNCERTAIN"}, "after": {"pid": 34893, "birth": "1792326202:781846", "generation": 3}, "final": {"base_index_entries": {"file00000.txt": [{"mode": "100644", "oid": "74ccd65945e21968a328f6fa8ec51ef9cc2bf0a3", "stage": "0"}]}, "commit": "0749ebf6b983c1f7d324dc4fc145486909fa6b14", "config_digest": "3dab0b9b9edcc59256ce5632d7c85fcc832e5f334d2e6705256789a6bb9d3fc6", "created": 1792326200.683742, "index_lock": {"anchor": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/c1-index_owner_de9e13140c7e488185f1c5bb7e2618f3", "checkout_identity": "a4247cb6aded55d4131105fd2aa4ff7e6091cc8b4cf74ad8a5cbce2836428996", "device": 16777230, "generation": 3, "index": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index", "inode": 748326603, "lock": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index.lock", "owner": {"birth": "1792326202:781846", "pid": 34893, "ppid": 30832, "uid": 501, "zombie": false}, "publication": "publication_3abfc254924847478278560ea38136a4", "replacement_digest": "80450d8f521370c27d5d41520d02053246f85d0ee3e77bd1c999b64a43ef7b0d", "state": "checkpointed", "token": "index_owner_de9e13140c7e488185f1c5bb7e2618f3"}, "metadata": {"author_date": "2026-10-18T12:30:00+00:00", "author_email": "c1@example.invalid", "author_name": "C1 Fixture", "committer_date": "2026-10-18T12:30:00+00:00", "committer_email": "c1@example.invalid", "committer_name": "C1 Fixture", "message": "C1 observed publication"}, "phase": "complete", "review": "review_b29713a57db34c5fa67079509866413f", "seal": "seal_62dbfe0a93c24e268e482c3127acbf36", "unit": "unit_c4d9a49d3dd845e68d76edf8189ac6b4", "id": "publication_3abfc254924847478278560ea38136a4"}, "unrelated_after": "100644 ea9532f4bb6411ac8e2d22b97ec47ec7e82d5933 0\tfile09999.txt\n", "unrelated_preserved": true, "commit_count": "2", "head": "0749ebf6b983c1f7d324dc4fc145486909fa6b14", "lock_exists": false, "anchor_exists": false, "claims": [{"_kind": "claim", "_revision": 2, "actor": "actor_74aa9fbaa1c64bb1a1ec7c10bdbec8a3", "checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "id": "claim_58478a9df2724084a9d69229bc44504a", "key": "file00000.txt", "path": "file00000.txt", "state": "released", "unit": "unit_c4d9a49d3dd845e68d76edf8189ac6b4"}]}
```

</details>

<a id="c21"></a>

<details>
<summary>C21 — owned-lock-incomplete-review; observed exit 1</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,os,signal,sqlite3,subprocess,time,hashlib
from coordinator.client import Client,CoordinatorError
from coordinator.processes import identity
from coordinator.store import digest
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"interrupt-setup.json").read_text()); previous=json.loads((root/"mid-seal.json").read_text()); c=Client(setup["state"],timeout=120); unit=setup["run"]["unit"]; repo=Path(setup["repo"])
def git(*args,data=None):
 r=subprocess.run(["git","-C",str(repo),*args],input=data,capture_output=True)
 if r.returncode: raise RuntimeError((args,r.returncode,r.stderr.decode()))
 return r.stdout
evidence=dict(previous["diff"]["binding"],verdict="pass",answer_digest=digest(previous["diff"]["diff"]),review_identity="C1 direct object inspection 2026-10-18")
review=c.call("unit/review-record",{"unit":unit,"evidence":evidence})
oid=git("hash-object","-w","--stdin",data=b"unrelated staged bytes\n").decode().strip()
git("update-index","--index-info",data=("100644 "+oid+"\tfile09999.txt\n").encode())
unrelated_before=git("ls-files","--stage","--","file09999.txt").decode()
db=sqlite3.connect("file:"+str(Path(setup["state"])/"ledger.sqlite")+"?mode=ro",uri=True,isolation_level=None)
def publication():
 row=db.execute("SELECT id,data FROM records WHERE kind='publication' AND parent=?",(unit,)).fetchone()
 return dict(json.loads(row[1]),id=row[0]) if row else None
def held(p):
 if not p or p["phase"]!="index_pending" or "index_lock" not in p: return False
 lock=p["index_lock"]
 try: return (os.stat(lock["lock"]).st_dev,os.stat(lock["lock"]).st_ino)==(lock["device"],lock["inode"]) and os.stat(lock["anchor"]).st_ino==lock["inode"]
 except FileNotFoundError:return False
before=c.call("daemon/status",{}); metadata=json.loads((root/"setup.json").read_text())["metadata"]
with ThreadPoolExecutor(1) as pool:
 pending=pool.submit(c.call,"unit/publish",{"unit":unit,"review":review["id"],"metadata":metadata},key="owned-index-crash")
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  observed=publication()
  if held(observed):
   if identity(before["pid"])["birth"]!=before["birth"]: raise RuntimeError("identity changed")
   os.kill(before["pid"],signal.SIGSTOP); stopped=publication()
   if held(stopped): break
   os.kill(before["pid"],signal.SIGCONT)
  if pending.done(): raise RuntimeError("owned index lock window missed")
  time.sleep(.0005)
 else: raise RuntimeError("lock checkpoint not caught")
 checkpoint={"time":time.time(),"worker":{k:before[k] for k in ("pid","birth","generation")},"publication":stopped,"head":git("rev-parse","HEAD").decode().strip(),"unrelated_before":unrelated_before}
 (root/"owned-lock-checkpoint.json").write_text(json.dumps(checkpoint,indent=2))
 os.kill(before["pid"],signal.SIGKILL)
 try: outcome=pending.result()
 except Exception as exc: outcome={"error":type(exc).__name__,"code":getattr(exc,"code",None)}
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>before["generation"]:break
 except (CoordinatorError,OSError):pass
 time.sleep(.1)
else:raise RuntimeError("recovery timeout")
final=publication(); unrelated_after=git("ls-files","--stage","--","file09999.txt").decode()
out={"checkpoint":checkpoint,"rpc_outcome":outcome,"after":{k:after[k] for k in ("pid","birth","generation")},"final":final,"unrelated_after":unrelated_after,"unrelated_preserved":unrelated_before==unrelated_after,"commit_count":git("rev-list","--count","HEAD").decode().strip(),"head":git("rev-parse","HEAD").decode().strip(),"lock_exists":Path(stopped["index_lock"]["lock"]).exists(),"anchor_exists":Path(stopped["index_lock"]["anchor"]).exists(),"claims":[r for r in c.call("records/list",{"kind":"claim","limit":1000})["data"] if r["unit"]==unit]}
(root/"owned-lock.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
REVIEW_INCOMPLETE: A completed, identified passing review is required. This attempt stopped at unit/review-record, before publication.
```

</details>

<a id="c22"></a>

<details>
<summary>C22 — parallel; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time,subprocess
from coordinator.client import Client,CoordinatorError
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"setup.json").read_text()); c=Client("/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state",timeout=1800); checkout=setup["checkouts"]["long"]
runs=[c.call("run/start",{"checkout":checkout,"mode":"native","task":"","fixture":str(root/"fixture-parallel"),"model":"gpt-6-astra"}) for _ in range(2)]
print(json.dumps({"idle_roots":runs}),flush=True)
with ThreadPoolExecutor(2) as pool:
 messages=list(pool.map(lambda pair:c.call("message/send",{"sender":"lead","recipient":pair[0]["actor"],"text":pair[1],"wake":"start"}),zip(runs,["C1_PAR_A","C1_PAR_B"])))
observations=[];deadline=time.monotonic()+40
while time.monotonic()<deadline:
 actors=[a for a in c.call("records/list",{"kind":"actor","limit":1000})["data"] if a["id"] in {r["actor"] for r in runs}]
 reservations=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"] in {r["unit"] for r in runs}]
 observations.append({"time":time.time(),"active":[a["id"] for a in actors if a.get("active_turn")],"held":[r for r in reservations if r["state"]!="released"]})
 if time.monotonic()>deadline-35 and all(not a.get("active_turn") for a in actors) and all(r["state"]=="released" for r in reservations):break
 time.sleep(.2)
try: c.call("claim/acquire",{"actor":runs[1]["actor"],"paths":["parallel_a.py"]})
except CoordinatorError as exc: conflict=exc.envelope()
queued=c.call("message/send",{"sender":"lead","recipient":runs[0]["actor"],"text":"Idle queue delivery observation","wake":"queue"})
deadline=time.monotonic()+20
while time.monotonic()<deadline:
 records=c.call("records/list",{"kind":"message","limit":1000})["data"]
 states=[m for m in records if m["id"] in {x["id"] for x in messages+[queued]}]
 if len(states)==3 and all(m["state"]=="acknowledged" for m in states):break
 time.sleep(.1)
survivor=c.call("run/start",{"checkout":checkout,"mode":"native","task":"C1_SURVIVOR","fixture":str(root/"fixture-parallel"),"model":"gpt-6-astra"})
survivor_observations=[];deadline=time.monotonic()+30
while time.monotonic()<deadline:
 actor=next(a for a in c.call("records/list",{"kind":"actor","limit":1000})["data"] if a["id"]==survivor["actor"])
 custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==survivor["unit"]]
 terminals=c.call("run/terminals",{"run":survivor["run"]})
 survivor_observations.append({"time":time.time(),"actor_state":actor["state"],"active_turn":actor.get("active_turn"),"custody":custody,"terminals":terminals})
 if custody and all(r["state"]=="released" for r in custody):break
 time.sleep(.3)
out={"runs":runs,"messages":states,"parallel_observations":observations,"conflict":conflict,"survivor":survivor,"survivor_observations":survivor_observations}
for kind in ("operation","actor","listener"):
 out[kind]=[r for r in c.call("records/list",{"kind":kind,"limit":1000})["data"] if r.get("unit") in {x["unit"] for x in runs+[survivor]}]
out["unknown"]=c.call("unknown/list",{"checkout":checkout})
(root/"parallel-and-survivors.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"parallel_max_active":max(len(o["active"]) for o in observations),"parallel_max_shared":max(len([r for r in o["held"] if r["level"]=="shared"]) for o in observations),"messages":[{"id":m["id"],"state":m["state"],"attempts":m["attempts"]} for m in states],"conflict":conflict,"survivor_held_after_turn":any(o["active_turn"] is None and any(r["state"]!="released" and r.get("processes") for r in o["custody"]) for o in survivor_observations),"survivor_final":survivor_observations[-1],"operations":[{"kind":r["kind"],"state":r["state"],"refusal":r.get("refusal")} for r in out["operation"]],"unknown":out["unknown"]}))
PY
```

Captured output:

```text
{"idle_roots": [{"actor": "actor_c0794dd277344198b63946a2c7f2abb8", "attempt": "attempt_f78d906abecc4ddcad8e2ed2d3f66053", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_f4652cfd82a84d36ae4c55ff4706eb83", "unit": "unit_37a8f4dc9daa43fb811677f1233843ab"}, {"actor": "actor_18733dc1dead4fd7a7924c89904ee572", "attempt": "attempt_7c09fd462c4849f8bb5f898cba552938", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_73ecbb1e3d5b48a2bf59944637db232a", "unit": "unit_f2220cab1b3a4a8384555ea68f3c0794"}]}

{"parallel_max_active": 2, "parallel_max_shared": 2, "messages": [{"id": "msg_2691bbb8a88a4edda7b9ecf8861c1539", "state": "acknowledged", "attempts": ["delivery_a474fa0baeba4602a425d27592f363bd"]}, {"id": "msg_ac03c607d5f04516bc969a3c8ec73f24", "state": "acknowledged", "attempts": ["delivery_a9f1c6f54a6a4fa2a671f3ad7b51fc4a"]}, {"id": "msg_babeb8f12ed542388140444375cbe2f0", "state": "entered_context", "attempts": ["delivery_e6de51acf1e64b4387cab4a8bf914539"]}], "conflict": {"code": "CLAIM_CONFLICT", "message": "Scope held by unit_37a8f4dc9daa43fb811677f1233843ab; send a message to that holder or the lead, then request lead-authorized handback", "details": {"conflicts": [{"holder": "unit_37a8f4dc9daa43fb811677f1233843ab", "kind": "claim", "scope": "parallel_a.py"}], "request": "request_a0750de981894a1eb7b2656c2b9d5de0"}, "kind": "refusal"}, "survivor_held_after_turn": false, "survivor_final": {"time": 1792326376.222657, "actor_state": "running", "active_turn": "01a14ef9-a6c9-7112-84c5-5a992306e5ca", "custody": [], "terminals": {"data": [], "nextCursor": null}}, "operations": [{"kind": "command", "state": "running", "refusal": null}, {"kind": "command", "state": "running", "refusal": null}, {"kind": "patch", "state": "verified", "refusal": null}, {"kind": "command", "state": "prepared", "refusal": null}, {"kind": "patch", "state": "verified", "refusal": null}], "unknown": {"data": [{"checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "context": "{\"code\":\"PROCESS_AMBIGUOUS\",\"custody\":\"custody_f4ff789b4cd14f6caebbddf1f882068d\"}", "created": 1792326268.3973482, "edit": null, "id": "unknown_0582c498aae04e95baa53dd1c5b56049", "path": "*", "reason": "process inventory unverifiable", "restore": null, "revision": null}, {"checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "context": "{\"code\":\"PROCESS_AMBIGUOUS\",\"custody\":\"custody_cdecca54a90a43bc8085db8dd0632300\"}", "created": 1792326268.39777, "edit": null, "id": "unknown_38492b87e50342e8a9924b1d31daa120", "path": "*", "reason": "process inventory unverifiable", "restore": null, "revision": null}]}}
```

</details>

<a id="c23"></a>

<details>
<summary>C23 — memory; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time,uuid,subprocess
from coordinator.client import Client,CoordinatorError
root=Path("/private/tmp/c1-rework-3gcqdxau"); c=Client("/private/tmp/c1-rework-3gcqdxau/ssssssssssss",timeout=120)
before=c.call("daemon/status",{})
start=time.perf_counter()
with ThreadPoolExecutor(32) as pool:
 keyed=list(pool.map(lambda i:c.call("records/list",{"kind":"no-such-record"},key="memory-"+str(i)+"-"+uuid.uuid4().hex),range(1500)))
 unkeyed=list(pool.map(lambda i:c.call("records/list",{"kind":"no-such-record"}),range(1500)))
 same=list(pool.map(lambda i:c.call("records/list",{"kind":"no-such-record"},key="same-observation-key"),range(100)))
elapsed=time.perf_counter()-start
actor=json.loads((root/"custody.json").read_text())["actors"][1]["actor"]
with ThreadPoolExecutor(2) as pool:
 abandoned=pool.submit(Client("/private/tmp/c1-rework-3gcqdxau/ssssssssssss",timeout=.03).call,"lead-command",{"actor":actor,"argv":["sleep","1"]},"cancelled-command-observation")
 time.sleep(.05)
 repeated=pool.submit(c.call,"lead-command",{"actor":actor,"argv":["sleep","1"]},"cancelled-command-observation")
 try: lost=abandoned.result()
 except Exception as exc: lost={"error":type(exc).__name__,"code":getattr(exc,"code",None)}
 recovered=repeated.result()
after=c.call("daemon/status",{})
ops=[o for o in c.call("records/list",{"kind":"operation","limit":10000})["data"] if o["id"]==recovered["operation"]]
out={"requests":{"unique_keyed":1500,"unkeyed":1500,"same_key_concurrent":100},"seconds":elapsed,"before_locks":before["locks"],"after_locks":after["locks"],"abandoned_client":lost,"same_key_result":recovered,"matching_operation_count":len(ops),"pid":after["pid"],"rss_kib":int(subprocess.check_output(["ps","-o","rss=","-p",str(after["pid"])]).strip())}
(root/"rpc-memory.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"requests": {"unique_keyed": 1500, "unkeyed": 1500, "same_key_concurrent": 100}, "seconds": 0.69914795801742, "before_locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "after_locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "abandoned_client": {"error": "TimeoutError", "code": null}, "same_key_result": {"custody": "released", "exit_code": 0, "operation": "op_a87711c41f23484183ec6bd96b56e954", "stderr_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "stdout_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}, "matching_operation_count": 1, "pid": 26414, "rss_kib": 43424}
```

</details>

<a id="c24"></a>

<details>
<summary>C24 — branch-setup; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import base64,json
from coordinator.client import Client
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"interrupt-setup.json").read_text()); c=Client(setup["state"],timeout=120)
run=c.call("run/start",{"checkout":setup["checkout"],"mode":"lead","task":"branch recovery and foreign lock","paths":["file00001.txt"]})
receipt=c.call("lead-edit",{"actor":run["actor"],"changes":{"file00001.txt":{"mode":"100644","bytes_base64":base64.b64encode(b"branch recovery value\n").decode()}}})
seal=c.call("unit/seal",{"unit":run["unit"],"validation":[]}); diff=c.call("unit/diff",{"unit":run["unit"]})
out={"run":run,"receipt":receipt,"seal":seal,"diff":diff}; (root/"branch-setup.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"run": {"actor": "actor_4a1c8719ad0747df9ff650b6036ca468", "attempt": "attempt_01be8b5cbed74e1c844ebe0a38e12107", "policy": null, "run": "run_d27f0e502135422898f32ab77e7c4221", "unit": "unit_622309b3520a42b2a93eef8833f613b0"}, "receipt": {"_kind": "operation", "_revision": 1, "actor": "actor_4a1c8719ad0747df9ff650b6036ca468", "attempt": "attempt_01be8b5cbed74e1c844ebe0a38e12107", "bases": {"file00001.txt": "rev_752e658b1edc4d6a8405345e273a2820"}, "checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "completed": 1792326286.7407172, "created": 1792326286.739419, "dependencies": [], "evidence": {"executor": "coordinator"}, "generation": 3, "hook_valid": false, "id": "op_7111b46bc6cd457ab4c1b19285ece31b", "intended": {"expected": {"file00001.txt": {"blob": "294e7e8d2b6152e01870a4343d9ab19e773a9ba5a60c268f3b5b8c7df25ec948", "mode": "100644"}}}, "kind": "lead-edit", "native": {}, "paths": ["file00001.txt"], "pre": {"file00001.txt": {"blob": "ac3f1874bd8805f3b0e725e82c1abe27beaec10e0e9d2088f5bf6f588bd128d2", "mode": "100644"}}, "result": {"file00001.txt": {"blob": "294e7e8d2b6152e01870a4343d9ab19e773a9ba5a60c268f3b5b8c7df25ec948", "mode": "100644"}}, "revisions": {"file00001.txt": "rev_8065fdb3bda94a8c98607d38c7312c7b"}, "state": "verified", "unit": "unit_622309b3520a42b2a93eef8833f613b0"}, "seal": {"_kind": "seal", "_revision": 1, "base": "0749ebf6b983c1f7d324dc4fc145486909fa6b14", "branch": "refs/heads/master", "candidate": "c3fa42a6c04deb31af4dd0e353977307c275f677", "created": 1792326286.8037279, "entries": {"file00001.txt": {"mode": "100644", "oid": "bc876998cbaa512964b19c59e17f0cca892f810c"}}, "id": "seal_dc5f6645b329457e9ad28ce9a02e2abd", "paths": ["file00001.txt"], "provenance": "57e7578d31ea99ea32bf40f98733eb26eae82ef29ec643be4e3627aa63631f5a", "repository": "5ae0f713c12a507f76a8081d0ec96074640d894b9546788f8555eb109ef07340", "rows": [{"attribution": "exact", "blob": "294e7e8d2b6152e01870a4343d9ab19e773a9ba5a60c268f3b5b8c7df25ec948", "checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "created": 1792326286.7406762, "id": "rev_8065fdb3bda94a8c98607d38c7312c7b", "mode": "100644", "operation": "op_7111b46bc6cd457ab4c1b19285ece31b", "path": "file00001.txt", "previous": "rev_752e658b1edc4d6a8405345e273a2820", "unit": "unit_622309b3520a42b2a93eef8833f613b0"}], "seal": "seal_dc5f6645b329457e9ad28ce9a02e2abd", "unit": "unit_622309b3520a42b2a93eef8833f613b0", "validation": [], "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "diff": {"binding": {"base": "0749ebf6b983c1f7d324dc4fc145486909fa6b14", "candidate": "c3fa42a6c04deb31af4dd0e353977307c275f677", "provenance": "57e7578d31ea99ea32bf40f98733eb26eae82ef29ec643be4e3627aa63631f5a", "repository": "5ae0f713c12a507f76a8081d0ec96074640d894b9546788f8555eb109ef07340", "seal": "seal_dc5f6645b329457e9ad28ce9a02e2abd", "unit": "unit_622309b3520a42b2a93eef8833f613b0", "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "diff": "diff --git a/file00001.txt b/file00001.txt\nindex 74ccd65..bc87699 100644\n--- a/file00001.txt\n+++ b/file00001.txt\n@@ -1 +1 @@\n-corpus baseline\n+branch recovery value\n"}}
```

</details>

<a id="c25"></a>

<details>
<summary>C25 — branch; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,os,signal,sqlite3,subprocess,time,hashlib
from coordinator.client import Client,CoordinatorError
from coordinator.processes import identity
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"interrupt-setup.json").read_text()); case=json.loads((root/"branch-setup.json").read_text()); c=Client(setup["state"],timeout=120); repo=Path(setup["repo"]); unit=case["run"]["unit"]
def git(*args):
 r=subprocess.run(["git","-C",str(repo),*args],capture_output=True)
 if r.returncode: raise RuntimeError((args,r.returncode,r.stderr.decode()))
 return r.stdout.decode().strip()
answer="Inspected complete frozen object diff: only file00001.txt changes from corpus baseline to branch recovery value."
evidence=dict(case["diff"]["binding"],verdict="pass",completed=True,answer=answer,answer_digest=hashlib.sha256(answer.encode()).hexdigest(),review_identity="C1 direct object inspection 2026-10-18")
review=c.call("unit/review-record",{"unit":unit,"evidence":evidence})
metadata=json.loads((root/"setup.json").read_text())["metadata"]; params={"unit":unit,"review":review["review"],"metadata":metadata}
git("update-ref","refs/heads/c1-other",case["seal"]["base"])
foreign=subprocess.Popen(["git","-C",str(repo),"update-index","--index-info"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
lock=repo/".git/index.lock"; index=repo/".git/index"
deadline=time.monotonic()+10
while not lock.exists() and time.monotonic()<deadline:time.sleep(.01)
if not lock.exists():foreign.terminate();raise RuntimeError("Git writer did not acquire index.lock")
foreign_identity=identity(foreign.pid); foreign_inode=lock.stat().st_ino; foreign_bytes=lock.read_bytes()
try:c.call("unit/publish",params)
except CoordinatorError as exc:initial_refusal=exc.envelope()
pending=[p for p in c.call("records/list",{"kind":"publication","limit":1000})["data"] if p["unit"]==unit][0]
before=c.call("daemon/status",{}); digest_before=hashlib.sha256(index.read_bytes()).hexdigest()
git("symbolic-ref","HEAD","refs/heads/c1-other")
if identity(before["pid"])["birth"]!=before["birth"]:raise RuntimeError("identity changed")
checkpoint={"time":time.time(),"worker":{k:before[k] for k in ("pid","birth","generation")},"publication":pending,"head":git("symbolic-ref","HEAD"),"foreign":foreign_identity,"foreign_inode":foreign_inode,"foreign_unchanged":lock.stat().st_ino==foreign_inode and lock.read_bytes()==foreign_bytes,"index_digest":digest_before}
(root/"branch-checkpoint.json").write_text(json.dumps(checkpoint,indent=2));os.kill(before["pid"],signal.SIGKILL)
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>before["generation"]:break
 except(CoordinatorError,OSError):pass
 time.sleep(.1)
else:raise RuntimeError("restart timed out")
db=sqlite3.connect("file:"+str(Path(setup["state"])/"ledger.sqlite")+"?mode=ro",uri=True)
events=[json.loads(r[0]) for r in db.execute("SELECT data FROM events WHERE kind='publication/recovery_refused' AND subject=?",(pending["id"],))]
held_claims=[r["state"] for r in c.call("records/list",{"kind":"claim","limit":1000})["data"] if r["unit"]==unit]
branch_observation={"recovery_refusals":events,"index_unchanged":hashlib.sha256(index.read_bytes()).hexdigest()==digest_before,"foreign_lock_unchanged":lock.stat().st_ino==foreign_inode and lock.read_bytes()==foreign_bytes,"head":git("symbolic-ref","HEAD"),"claim_states":held_claims}
foreign.stdin.close();foreign.stdin=None; stdout,stderr=foreign.communicate(timeout=10);foreign_code=foreign.returncode
git("symbolic-ref","HEAD",case["seal"]["branch"])
final=c.call("unit/publish",params)
out={"initial_refusal":initial_refusal,"checkpoint":checkpoint,"branch_observation":branch_observation,"foreign_exit_code":foreign_code,"final":final,"head":git("rev-parse","HEAD")}
(root/"branch-recovery.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"initial_refusal": {"code": "INDEX_LOCKED", "message": "A foreign Git writer owns index.lock; it will not be removed", "details": {}, "kind": "refusal"}, "checkpoint": {"time": 1792326339.0990038, "worker": {"pid": 34893, "birth": "1792326202:781846", "generation": 3}, "publication": {"_kind": "publication", "_revision": 5, "base_index_entries": {"file00001.txt": [{"mode": "100644", "oid": "74ccd65945e21968a328f6fa8ec51ef9cc2bf0a3", "stage": "0"}]}, "commit": "908e621d4add5b42595a8adbc2a9b62491b6b1c6", "config_digest": "3dab0b9b9edcc59256ce5632d7c85fcc832e5f334d2e6705256789a6bb9d3fc6", "created": 1792326337.207362, "id": "publication_5837652e252d4b76a57bc4b5cc3f4ded", "index_lock": {"anchor": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/c1-index_owner_9e2b987e1d194836ab1c20f2e8c08c18", "checkout_identity": "a4247cb6aded55d4131105fd2aa4ff7e6091cc8b4cf74ad8a5cbce2836428996", "device": 16777230, "generation": 3, "index": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index", "inode": 748391791, "lock": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index.lock", "owner": {"birth": "1792326202:781846", "pid": 34893, "ppid": 30832, "uid": 501, "zombie": false}, "publication": "publication_5837652e252d4b76a57bc4b5cc3f4ded", "state": "recorded", "token": "index_owner_9e2b987e1d194836ab1c20f2e8c08c18"}, "metadata": {"author_date": "2026-10-18T12:30:00+00:00", "author_email": "c1@example.invalid", "author_name": "C1 Fixture", "committer_date": "2026-10-18T12:30:00+00:00", "committer_email": "c1@example.invalid", "committer_name": "C1 Fixture", "message": "C1 observed publication"}, "phase": "index_pending", "review": "review_256cad4cb2854642a31f2f0fa3dedffe", "seal": "seal_dc5f6645b329457e9ad28ce9a02e2abd", "unit": "unit_622309b3520a42b2a93eef8833f613b0"}, "head": "refs/heads/c1-other", "foreign": {"pid": 41124, "birth": "1792326335:370088", "ppid": 41095, "uid": 501, "zombie": false}, "foreign_inode": 748371102, "foreign_unchanged": true, "index_digest": "80450d8f521370c27d5d41520d02053246f85d0ee3e77bd1c999b64a43ef7b0d"}, "branch_observation": {"recovery_refusals": [{"code": "BRANCH_CHANGED"}], "index_unchanged": true, "foreign_lock_unchanged": true, "head": "refs/heads/c1-other", "claim_states": ["held"]}, "foreign_exit_code": 0, "final": {"_kind": "publication", "_revision": 6, "base_index_entries": {"file00001.txt": [{"mode": "100644", "oid": "74ccd65945e21968a328f6fa8ec51ef9cc2bf0a3", "stage": "0"}]}, "commit": "908e621d4add5b42595a8adbc2a9b62491b6b1c6", "config_digest": "3dab0b9b9edcc59256ce5632d7c85fcc832e5f334d2e6705256789a6bb9d3fc6", "created": 1792326337.207362, "id": "publication_5837652e252d4b76a57bc4b5cc3f4ded", "index_lock": {"anchor": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/c1-index_owner_9e2b987e1d194836ab1c20f2e8c08c18", "checkout_identity": "a4247cb6aded55d4131105fd2aa4ff7e6091cc8b4cf74ad8a5cbce2836428996", "device": 16777230, "generation": 4, "index": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index", "inode": 748391791, "lock": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index.lock", "owner": {"birth": "1792326339:360422", "pid": 41665, "ppid": 30832, "uid": 501, "zombie": false}, "publication": "publication_5837652e252d4b76a57bc4b5cc3f4ded", "replacement_digest": "b00f1c315623b27121e65586591f4894af6432c08c510236d69840d8c4f21f3a", "state": "checkpointed", "token": "index_owner_9e2b987e1d194836ab1c20f2e8c08c18"}, "metadata": {"author_date": "2026-10-18T12:30:00+00:00", "author_email": "c1@example.invalid", "author_name": "C1 Fixture", "committer_date": "2026-10-18T12:30:00+00:00", "committer_email": "c1@example.invalid", "committer_name": "C1 Fixture", "message": "C1 observed publication"}, "phase": "complete", "review": "review_256cad4cb2854642a31f2f0fa3dedffe", "seal": "seal_dc5f6645b329457e9ad28ce9a02e2abd", "unit": "unit_622309b3520a42b2a93eef8833f613b0"}, "head": "908e621d4add5b42595a8adbc2a9b62491b6b1c6"}
```

</details>

<a id="c26"></a>

<details>
<summary>C26 — hooks-performance; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,statistics,sqlite3,subprocess
from coordinator.client import Client
from coordinator.processes import identity
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"setup.json").read_text());c=Client("/private/tmp/c1-rework-3gcqdxau/ssssssssssss",timeout=1800)
run=c.call("run/start",{"checkout":setup["checkouts"]["crash"],"mode":"native","task":"C1_HOOKS","fixture":str(root/"fixture-parallel"),"model":"gpt-6-astra"})
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 state=c.call("run/status",{"run":run["run"]})
 if state["state"]=="completed":break
 time.sleep(.2)
db=sqlite3.connect("file:/private/tmp/c1-rework-3gcqdxau/ssssssssssss/ledger.sqlite?mode=ro",uri=True)
actor=next(r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["id"]==run["actor"])
listener=next(r for r in c.call("records/list",{"kind":"listener","limit":1000})["data"] if r["id"]==actor["listener"])
events=[{"time":r[0],"message":json.loads(r[1])} for r in db.execute("SELECT time,data FROM events WHERE kind='native' AND subject=? ORDER BY seq",(actor["listener"],))]
completed=[r for r in events if r["message"].get("method")=="hook/completed" and r["message"].get("params",{}).get("threadId")==actor["thread"]]
durations=[r["message"]["params"]["run"]["durationMs"] for r in completed if "durationMs" in r["message"]["params"].get("run",{})]
start_events={r["message"]["params"].get("run",{}).get("id"):r["time"] for r in events if r["message"].get("method")=="hook/started"}
if not durations:
 durations=[1000*(r["time"]-start_events[r["message"]["params"]["run"]["id"]]) for r in completed if r["message"]["params"].get("run",{}).get("id") in start_events]
out={"run":run,"state":state,"hook_count":len(completed),"durations_ms":durations,"example":completed[:1],"median_ms":statistics.median(durations) if durations else None,"p95_ms":sorted(durations)[int(.95*(len(durations)-1))] if durations else None,"listener":{"pid":listener["pid"],"birth":listener["birth"],"observed_birth":identity(listener["pid"])["birth"],"rss_kib":int(subprocess.check_output(["ps","-o","rss=","-p",str(listener["pid"])]).strip())}}
(root/"hook-performance.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"run": {"actor": "actor_a4888a8a938c4662a32943dd2bb15d87", "attempt": "attempt_23d695f66b26489a972eac5fb63977e8", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_a8dffcf0fd6740ce9ea4a059a96b03d0", "unit": "unit_29195aced8f5493785918aed9312d88a"}, "state": {"_kind": "run", "_revision": 3, "attempt": "attempt_23d695f66b26489a972eac5fb63977e8", "checkout": "checkout_67c7fa30ad3840e797849c6b0d52d2a8", "id": "run_a8dffcf0fd6740ce9ea4a059a96b03d0", "mode": "native", "root_actor": "actor_a4888a8a938c4662a32943dd2bb15d87", "state": "completed", "task": "C1_HOOKS", "unit": "unit_29195aced8f5493785918aed9312d88a"}, "hook_count": 110, "durations_ms": [42, 37, 37, 38, 38, 38, 38, 38, 37, 39, 39, 38, 38, 38, 39, 38, 45, 40, 38, 40, 42, 41, 38, 37, 39, 39, 38, 36, 39, 37, 37, 37, 37, 36, 37, 36, 37, 38, 38, 38, 37, 37, 37, 36, 37, 36, 39, 39, 42, 38, 37, 37, 37, 38, 39, 37, 37, 37, 37, 37, 37, 37, 37, 38, 37, 37, 39, 38, 39, 38, 40, 40, 42, 39, 37, 37, 37, 36, 37, 37, 38, 37, 37, 37, 38, 39, 39, 39, 39, 38, 39, 38, 39, 37, 37, 37, 37, 38, 39, 37, 36, 37, 37, 36, 38, 37, 37, 37, 37, 36], "example": [{"time": 1792326392.480184, "message": {"emittedAtMs": 1792326392480, "method": "hook/completed", "params": {"run": {"completedAt": 1792326392, "displayOrder": 0, "durationMs": 42, "entries": [], "eventName": "preToolUse", "executionMode": "sync", "handlerType": "command", "id": "pre-tool-use:0:/<session-flags>/config.toml:fx-2b08b17144d642568f4ef2691953a4e7", "scope": "turn", "source": "sessionFlags", "sourcePath": "/<session-flags>/config.toml", "startedAt": 1792326392, "status": "completed", "statusMessage": null}, "threadId": "01a14efa-0ce4-7f31-9659-69e43e59af93", "turnId": "01a14efa-5a59-7cb1-9816-4dcb191f37fc"}}}], "median_ms": 37.5, "p95_ms": 40, "listener": {"pid": 42984, "birth": "1792326372:391301", "observed_birth": "1792326372:391301", "rss_kib": 113120}}
```

</details>

<a id="c27"></a>

<details>
<summary>C27 — diagnostic; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
import json,subprocess
from pathlib import Path
from coordinator.client import Client
from coordinator import processes
c=Client("/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state")
actors=c.call("records/list",{"kind":"actor","limit":1000})["data"];listeners=c.call("records/list",{"kind":"listener","limit":1000})["data"];reservations=c.call("records/list",{"kind":"custody","limit":1000})["data"];ops=c.call("records/list",{"kind":"operation","limit":1000})["data"]
out=[]
for r in reservations:
 if r["state"]=="released": continue
 a=next(a for a in actors if a["id"]==r["actor"]); l=next(l for l in listeners if l["id"]==a["listener"]); op=next(o for o in ops if o["id"]==r["operation"])
 try: inventory=processes.inventory(l,a["session"],r.get("observed_processes",[]),op,l.get("services",[]))
 except Exception as exc:inventory={"code":getattr(exc,"code",None),"details":getattr(exc,"details",{})}
 try: tree=processes.inventory(l,None)
 except Exception as exc:tree={"error":str(exc)}
 out.append({"reservation":r,"actor":{k:a.get(k) for k in ("thread","session","state","active_turn")},"listener":{"pid":l["pid"],"birth":l["birth"],"services":l.get("services")},"operation":op,"current_inventory":inventory,"listener_tree":tree})
Path("/private/tmp/c1-rework-3gcqdxau/process-diagnosis.json").write_text(json.dumps(out,indent=2))
print(json.dumps(out))
PY
```

Captured output:

```text
[{"reservation": {"_kind": "custody", "_revision": 123, "actor": "actor_c0794dd277344198b63946a2c7f2abb8", "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "command": "sleep 4", "created": 1792326268.1551518, "excludes": ["native_b.py", "native_a.py", "child.py"], "generation": 2, "id": "custody_cdecca54a90a43bc8085db8dd0632300", "level": "shared", "observed_processes": [], "operation": "op_2568ad21e1994330830d0c9271607633", "processes": [], "reason": "PROCESS_UNREADABLE", "state": "quarantined", "terminals": ["29479"], "unit": "unit_37a8f4dc9daa43fb811677f1233843ab"}, "actor": {"thread": "01a14ef7-ce06-7222-9397-606591e63a14", "session": "01a14ef7-ce06-7222-9397-606591e63a14", "state": "completed", "active_turn": null}, "listener": {"pid": 35264, "birth": "1792326225:227711", "services": []}, "operation": {"_kind": "operation", "_revision": 5, "actor": "actor_c0794dd277344198b63946a2c7f2abb8", "attempt": "attempt_f78d906abecc4ddcad8e2ed2d3f66053", "bases": {"a.txt": "rev_c7e18a048dc748dfbb8487e8d54a6355", "b.txt": "rev_58a1169504794afbb5902054e9320995", "child.py": "rev_2a7c1885cec94d4d91cfad3bcbcd715c", "native_a.py": "rev_7bad7ecd9ea042d8adc1a57937abd352", "native_b.py": "rev_30b5384c31e84138b461410bb984d86d", "unrelated.txt": "rev_c1d5f886ba174603aa6dcbb63c57cc03"}, "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "completed_item": {"aggregatedOutput": null, "command": "/bin/zsh -c 'sleep 4'", "commandActions": [{"command": "sleep 4", "type": "unknown"}], "cwd": "/private/tmp/c1-rework-3gcqdxau/long-repo", "durationMs": 3852, "exitCode": 0, "id": "fx-24e5983ae53148a19699e2cb950de022", "pluginId": null, "processId": "29479", "scriptPath": null, "source": "unifiedExecStartup", "status": "completed", "type": "commandExecution"}, "created": 1792326268.15455, "custody": "custody_cdecca54a90a43bc8085db8dd0632300", "decision": "allow", "dependencies": [], "generation": 2, "hook_evidence": [{"completedAt": 1792326268, "displayOrder": 0, "durationMs": 43, "entries": [], "eventName": "preToolUse", "executionMode": "sync", "handlerType": "command", "id": "pre-tool-use:0:/<session-flags>/config.toml:fx-24e5983ae53148a19699e2cb950de022", "scope": "turn", "source": "sessionFlags", "sourcePath": "/<session-flags>/config.toml", "startedAt": 1792326268, "status": "completed", "statusMessage": null}], "hook_valid": true, "id": "op_2568ad21e1994330830d0c9271607633", "intended": {"tool": "Bash", "tool_input": {"command": "sleep 4"}}, "kind": "command", "native": {"call": "fx-24e5983ae53148a19699e2cb950de022", "listener": "listener_2fa4ff41b048420eafbc9ca7bb52e418", "thread": "01a14ef7-ce06-7222-9397-606591e63a14", "turn": "01a14ef8-6c54-7781-a657-d95cd5a334f0"}, "paths": [], "pre": {}, "started_item": {"aggregatedOutput": null, "command": "/bin/zsh -c 'sleep 4'", "commandActions": [{"command": "sleep 4", "type": "unknown"}], "cwd": "/private/tmp/c1-rework-3gcqdxau/long-repo", "durationMs": null, "exitCode": null, "id": "fx-24e5983ae53148a19699e2cb950de022", "pluginId": null, "processId": "29479", "scriptPath": null, "source": "unifiedExecStartup", "status": "inProgress", "type": "commandExecution"}, "state": "running", "unit": "unit_37a8f4dc9daa43fb811677f1233843ab"}, "current_inventory": [], "listener_tree": []}, {"reservation": {"_kind": "custody", "_revision": 123, "actor": "actor_18733dc1dead4fd7a7924c89904ee572", "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "command": "sleep 4", "created": 1792326268.1538382, "excludes": ["native_b.py", "native_a.py", "child.py"], "generation": 2, "id": "custody_f4ff789b4cd14f6caebbddf1f882068d", "level": "shared", "observed_processes": [], "operation": "op_01b19ddf71e54477b5cd04dcd1a4d1b9", "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["59145"], "unit": "unit_f2220cab1b3a4a8384555ea68f3c0794"}, "actor": {"thread": "01a14ef8-1e2a-7a61-a15a-90982d887c52", "session": "01a14ef8-1e2a-7a61-a15a-90982d887c52", "state": "completed", "active_turn": null}, "listener": {"pid": 36927, "birth": "1792326245:747843", "services": []}, "operation": {"_kind": "operation", "_revision": 5, "actor": "actor_18733dc1dead4fd7a7924c89904ee572", "attempt": "attempt_7c09fd462c4849f8bb5f898cba552938", "bases": {"a.txt": "rev_c7e18a048dc748dfbb8487e8d54a6355", "b.txt": "rev_58a1169504794afbb5902054e9320995", "child.py": "rev_2a7c1885cec94d4d91cfad3bcbcd715c", "native_a.py": "rev_7bad7ecd9ea042d8adc1a57937abd352", "native_b.py": "rev_30b5384c31e84138b461410bb984d86d", "unrelated.txt": "rev_c1d5f886ba174603aa6dcbb63c57cc03"}, "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "completed_item": {"aggregatedOutput": null, "command": "/bin/zsh -c 'sleep 4'", "commandActions": [{"command": "sleep 4", "type": "unknown"}], "cwd": "/private/tmp/c1-rework-3gcqdxau/long-repo", "durationMs": 3851, "exitCode": 0, "id": "fx-8942adbb346c41ba9ce1cc947a75321c", "pluginId": null, "processId": "59145", "scriptPath": null, "source": "unifiedExecStartup", "status": "completed", "type": "commandExecution"}, "created": 1792326268.1530619, "custody": "custody_f4ff789b4cd14f6caebbddf1f882068d", "decision": "allow", "dependencies": [], "generation": 2, "hook_evidence": [{"completedAt": 1792326268, "displayOrder": 0, "durationMs": 42, "entries": [], "eventName": "preToolUse", "executionMode": "sync", "handlerType": "command", "id": "pre-tool-use:0:/<session-flags>/config.toml:fx-8942adbb346c41ba9ce1cc947a75321c", "scope": "turn", "source": "sessionFlags", "sourcePath": "/<session-flags>/config.toml", "startedAt": 1792326268, "status": "completed", "statusMessage": null}], "hook_valid": true, "id": "op_01b19ddf71e54477b5cd04dcd1a4d1b9", "intended": {"tool": "Bash", "tool_input": {"command": "sleep 4"}}, "kind": "command", "native": {"call": "fx-8942adbb346c41ba9ce1cc947a75321c", "listener": "listener_4db572650a5540b796fd864b1dbc5028", "thread": "01a14ef8-1e2a-7a61-a15a-90982d887c52", "turn": "01a14ef8-6c55-7550-b7c9-4f333b662832"}, "paths": [], "pre": {}, "started_item": {"aggregatedOutput": null, "command": "/bin/zsh -c 'sleep 4'", "commandActions": [{"command": "sleep 4", "type": "unknown"}], "cwd": "/private/tmp/c1-rework-3gcqdxau/long-repo", "durationMs": null, "exitCode": null, "id": "fx-8942adbb346c41ba9ce1cc947a75321c", "pluginId": null, "processId": "59145", "scriptPath": null, "source": "unifiedExecStartup", "status": "inProgress", "type": "commandExecution"}, "state": "running", "unit": "unit_f2220cab1b3a4a8384555ea68f3c0794"}, "current_inventory": [], "listener_tree": []}]
```

</details>

<a id="c28"></a>

<details>
<summary>C28 — live-diagnosis; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,subprocess
from coordinator.client import Client
from coordinator import processes
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"setup.json").read_text());c=Client("/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state",timeout=1800)
for r in c.call("records/list",{"kind":"custody","limit":1000})["data"]:
 if r["state"]!="released": print(json.dumps({"reconcile":c.call("custody/reconcile",{"custody":r["id"]})}),flush=True)
run=c.call("run/start",{"checkout":setup["checkouts"]["long"],"mode":"native","task":"","fixture":str(root/"fixture-more"),"model":"gpt-6-astra"})
actor=next(a for a in c.call("records/list",{"kind":"actor","limit":1000})["data"] if a["id"]==run["actor"]); listener=next(l for l in c.call("records/list",{"kind":"listener","limit":1000})["data"] if l["id"]==actor["listener"])
c.call("message/send",{"recipient":actor["id"],"text":"C1_SHARED","wake":"start"})
seen={};deadline=time.monotonic()+12
while time.monotonic()<deadline:
 try:
  for p in processes.inventory(listener,None):
   key=str(p["pid"])+":"+p["birth"]
   if key not in seen: seen[key]=dict(p,comm=subprocess.check_output(["ps","-o","comm=","-p",str(p["pid"])]).decode().strip(),time=time.time())
 except Exception as exc: pass
 time.sleep(.01)
operations=[o for o in c.call("records/list",{"kind":"operation","limit":1000})["data"] if o["unit"]==run["unit"]]
out={"run":run,"thread":actor["thread"],"processes":list(seen.values()),"operations":operations,"custody":[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==run["unit"]]}
(root/"live-process-diagnosis.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"reconcile": {"_kind": "custody", "_revision": 160, "actor": "actor_c0794dd277344198b63946a2c7f2abb8", "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "command": "sleep 4", "completed": 1792326435.682678, "created": 1792326268.1551518, "excludes": ["native_b.py", "native_a.py", "child.py"], "generation": 2, "id": "custody_cdecca54a90a43bc8085db8dd0632300", "level": "shared", "observed_processes": [], "operation": "op_2568ad21e1994330830d0c9271607633", "processes": [], "reason": "PROCESS_UNREADABLE", "state": "released", "terminals": ["29479"], "unit": "unit_37a8f4dc9daa43fb811677f1233843ab"}}
{"reconcile": {"_kind": "custody", "_revision": 159, "actor": "actor_18733dc1dead4fd7a7924c89904ee572", "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "command": "sleep 4", "completed": 1792326435.7264152, "created": 1792326268.1538382, "excludes": ["native_b.py", "native_a.py", "child.py"], "generation": 2, "id": "custody_f4ff789b4cd14f6caebbddf1f882068d", "level": "shared", "observed_processes": [], "operation": "op_01b19ddf71e54477b5cd04dcd1a4d1b9", "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "released", "terminals": ["59145"], "unit": "unit_f2220cab1b3a4a8384555ea68f3c0794"}}

{"run": {"actor": "actor_586e5f7a13724c34a12b5b78d1b64ec2", "attempt": "attempt_55cd28890779426591fc6e1788b0a8a8", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_c1580b4f74f049dc8ac7210b96f3f666", "unit": "unit_781bdcbd5d494293938b04bf9c3fa398"}, "thread": "01a14efb-0576-75b1-b990-a4e0df20689b", "processes": [{"pid": 49262, "birth": "1792326456:128203", "ppid": 47131, "uid": 501, "zombie": false, "markers": {"CODEX_SESSION_ID": "01a14ee3-970b-7683-a166-127932d5053a", "CODEX_THREAD_ID": "01a14ee3-970b-7683-a166-127932d5053a", "CODEX_TOOL_CALL_ID": "exec-10941cca-9781-4e89-ae47-85129d844419"}, "lineage": true, "marked": false, "comm": "/opt/homebrew/Cellar/python@3.15/3.15.0/Frameworks/Python.framework/Versions/3.15/Resources/Python.app/Contents/MacOS/Python", "time": 1792326456.217648}], "operations": [{"_kind": "operation", "_revision": 1, "actor": "actor_586e5f7a13724c34a12b5b78d1b64ec2", "attempt": "attempt_55cd28890779426591fc6e1788b0a8a8", "bases": {"a.txt": "rev_c7e18a048dc748dfbb8487e8d54a6355", "b.txt": "rev_58a1169504794afbb5902054e9320995", "child.py": "rev_2a7c1885cec94d4d91cfad3bcbcd715c", "native_a.py": "rev_7bad7ecd9ea042d8adc1a57937abd352", "native_b.py": "rev_30b5384c31e84138b461410bb984d86d", "parallel_a.py": "rev_a05ce4f2edb2410c9bad78cceec767c6", "parallel_b.py": "rev_d0190183568746c98fdce3751cbc38a8", "unrelated.txt": "rev_c1d5f886ba174603aa6dcbb63c57cc03"}, "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "created": 1792326456.166246, "dependencies": [], "generation": 2, "hook_valid": false, "id": "op_50f85e46838442b08c6cfaa80436b96e", "intended": {"tool": "Bash", "tool_input": {"command": "sleep 8"}}, "kind": "command", "native": {"call": "fx-6c97d0966c6343fba84635821b9f976a", "listener": "listener_3e140ef208dc4346af7b89844b1abb1e", "thread": "01a14efb-0576-75b1-b990-a4e0df20689b", "turn": "01a14efb-52cb-77d0-9ab1-badb7fdfc9cf"}, "paths": [], "pre": {}, "state": "prepared", "unit": "unit_781bdcbd5d494293938b04bf9c3fa398"}], "custody": []}
```

</details>

<a id="c29"></a>

<details>
<summary>C29 — installed-setup; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import base64,json
from coordinator.client import Client
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"interrupt-setup.json").read_text()); c=Client(setup["state"],timeout=120)
run=c.call("run/start",{"checkout":setup["checkout"],"mode":"lead","task":"index installation checkpoint","paths":["file00002.txt"]})
receipt=c.call("lead-edit",{"actor":run["actor"],"changes":{"file00002.txt":{"mode":"100644","bytes_base64":base64.b64encode(b"installed before checkpoint\n").decode()}}})
seal=c.call("unit/seal",{"unit":run["unit"],"validation":[]}); diff=c.call("unit/diff",{"unit":run["unit"]})
out={"run":run,"receipt":receipt,"seal":seal,"diff":diff};(root/"installed-setup.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"run": {"actor": "actor_958b1e35f17f4986869410e3db894e24", "attempt": "attempt_d586f0af0f004c3b9f463ffd224f971d", "policy": null, "run": "run_c5be161de45e4b18be62a0cebdd72e4d", "unit": "unit_f9cbec34bd3d449b8a5337b66ab993ba"}, "receipt": {"_kind": "operation", "_revision": 1, "actor": "actor_958b1e35f17f4986869410e3db894e24", "attempt": "attempt_d586f0af0f004c3b9f463ffd224f971d", "bases": {"file00002.txt": "rev_ba213277263a4f9cbda0bf611dfbafc8"}, "checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "completed": 1792326500.002924, "created": 1792326500.001688, "dependencies": [], "evidence": {"executor": "coordinator"}, "generation": 4, "hook_valid": false, "id": "op_d0fb112d678d41d2a5805f2c98b8b736", "intended": {"expected": {"file00002.txt": {"blob": "cc90699f6da232c003af1cf785785977b4bcd60984017013fb8b42170b61e934", "mode": "100644"}}}, "kind": "lead-edit", "native": {}, "paths": ["file00002.txt"], "pre": {"file00002.txt": {"blob": "ac3f1874bd8805f3b0e725e82c1abe27beaec10e0e9d2088f5bf6f588bd128d2", "mode": "100644"}}, "result": {"file00002.txt": {"blob": "cc90699f6da232c003af1cf785785977b4bcd60984017013fb8b42170b61e934", "mode": "100644"}}, "revisions": {"file00002.txt": "rev_a4c9d90189104025afe61f8c62bbdf1e"}, "state": "verified", "unit": "unit_f9cbec34bd3d449b8a5337b66ab993ba"}, "seal": {"_kind": "seal", "_revision": 1, "base": "908e621d4add5b42595a8adbc2a9b62491b6b1c6", "branch": "refs/heads/master", "candidate": "0200573e29407612865f162c545802870b950f49", "created": 1792326500.064915, "entries": {"file00002.txt": {"mode": "100644", "oid": "5724a13c5697a9380f1a05dfa93eb1f9bf309d82"}}, "id": "seal_2312aa10f476475f8e4785f86ad1dade", "paths": ["file00002.txt"], "provenance": "9d161bca25b3a5790451ad9ddc574dfe41cdd289efe6ba4c2e96b2a04701c3f8", "repository": "5ae0f713c12a507f76a8081d0ec96074640d894b9546788f8555eb109ef07340", "rows": [{"attribution": "exact", "blob": "cc90699f6da232c003af1cf785785977b4bcd60984017013fb8b42170b61e934", "checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "created": 1792326500.002893, "id": "rev_a4c9d90189104025afe61f8c62bbdf1e", "mode": "100644", "operation": "op_d0fb112d678d41d2a5805f2c98b8b736", "path": "file00002.txt", "previous": "rev_ba213277263a4f9cbda0bf611dfbafc8", "unit": "unit_f9cbec34bd3d449b8a5337b66ab993ba"}], "seal": "seal_2312aa10f476475f8e4785f86ad1dade", "unit": "unit_f9cbec34bd3d449b8a5337b66ab993ba", "validation": [], "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "diff": {"binding": {"base": "908e621d4add5b42595a8adbc2a9b62491b6b1c6", "candidate": "0200573e29407612865f162c545802870b950f49", "provenance": "9d161bca25b3a5790451ad9ddc574dfe41cdd289efe6ba4c2e96b2a04701c3f8", "repository": "5ae0f713c12a507f76a8081d0ec96074640d894b9546788f8555eb109ef07340", "seal": "seal_2312aa10f476475f8e4785f86ad1dade", "unit": "unit_f9cbec34bd3d449b8a5337b66ab993ba", "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "diff": "diff --git a/file00002.txt b/file00002.txt\nindex 74ccd65..5724a13 100644\n--- a/file00002.txt\n+++ b/file00002.txt\n@@ -1 +1 @@\n-corpus baseline\n+installed before checkpoint\n"}}
```

</details>

<a id="c30"></a>

<details>
<summary>C30 — live-diagnosis2; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
import json,time,subprocess
from pathlib import Path
from coordinator.client import Client
from coordinator import processes
root=Path("/private/tmp/c1-rework-3gcqdxau");case=json.loads((root/"live-process-diagnosis.json").read_text());c=Client("/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state",timeout=120)
a=next(a for a in c.call("records/list",{"kind":"actor","limit":1000})["data"] if a["id"]==case["run"]["actor"]);l=next(l for l in c.call("records/list",{"kind":"listener","limit":1000})["data"] if l["id"]==a["listener"])
for r in c.call("records/list",{"kind":"custody","limit":1000})["data"]:
 if r["state"]=="quarantined":c.call("custody/reconcile",{"custody":r["id"]})
seen={};deadline=time.monotonic()+12
while time.monotonic()<deadline:
 try:
  for p in processes.inventory(l,None):
   key=str(p["pid"])+":"+p["birth"]
   if key not in seen:seen[key]=dict(p,comm=subprocess.check_output(["ps","-o","comm=","-p",str(p["pid"])]).decode().strip(),time=time.time())
 except Exception:pass
 time.sleep(.005)
out={"run":case["run"],"thread":a["thread"],"processes":list(seen.values()),"custody":[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==case["run"]["unit"]],"operations":[r for r in c.call("records/list",{"kind":"operation","limit":1000})["data"] if r["unit"]==case["run"]["unit"]]}
(root/"live-process-diagnosis-2.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"run": {"actor": "actor_586e5f7a13724c34a12b5b78d1b64ec2", "attempt": "attempt_55cd28890779426591fc6e1788b0a8a8", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_c1580b4f74f049dc8ac7210b96f3f666", "unit": "unit_781bdcbd5d494293938b04bf9c3fa398"}, "thread": "01a14efb-0576-75b1-b990-a4e0df20689b", "processes": [{"pid": 52320, "birth": "1792326521:838977", "ppid": 47131, "uid": 501, "zombie": false, "markers": {}, "lineage": true, "marked": false, "comm": "sleep", "time": 1792326521.912269}], "custody": [{"_kind": "custody", "_revision": 19, "actor": "actor_586e5f7a13724c34a12b5b78d1b64ec2", "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "command": "sleep 8", "created": 1792326521.83009, "excludes": ["parallel_a.py", "native_b.py", "native_a.py", "parallel_b.py", "child.py"], "generation": 2, "id": "custody_006d77971a7f45e3ba69d3d009e179b7", "level": "shared", "observed_processes": [], "operation": "op_50f85e46838442b08c6cfaa80436b96e", "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["22705"], "unit": "unit_781bdcbd5d494293938b04bf9c3fa398"}], "operations": [{"_kind": "operation", "_revision": 5, "actor": "actor_586e5f7a13724c34a12b5b78d1b64ec2", "attempt": "attempt_55cd28890779426591fc6e1788b0a8a8", "bases": {"a.txt": "rev_c7e18a048dc748dfbb8487e8d54a6355", "b.txt": "rev_58a1169504794afbb5902054e9320995", "child.py": "rev_2a7c1885cec94d4d91cfad3bcbcd715c", "native_a.py": "rev_7bad7ecd9ea042d8adc1a57937abd352", "native_b.py": "rev_30b5384c31e84138b461410bb984d86d", "parallel_a.py": "rev_a05ce4f2edb2410c9bad78cceec767c6", "parallel_b.py": "rev_d0190183568746c98fdce3751cbc38a8", "unrelated.txt": "rev_c1d5f886ba174603aa6dcbb63c57cc03"}, "checkout": "checkout_8d42e6b1edf2498090905d8730ce216b", "completed_item": {"aggregatedOutput": null, "command": "/bin/zsh -c 'sleep 8'", "commandActions": [{"command": "sleep 8", "type": "unknown"}], "cwd": "/private/tmp/c1-rework-3gcqdxau/long-repo", "durationMs": 7860, "exitCode": 0, "id": "fx-6c97d0966c6343fba84635821b9f976a", "pluginId": null, "processId": "22705", "scriptPath": null, "source": "unifiedExecStartup", "status": "completed", "type": "commandExecution"}, "created": 1792326456.166246, "custody": "custody_006d77971a7f45e3ba69d3d009e179b7", "decision": "allow", "dependencies": [], "generation": 2, "hook_evidence": [{"completedAt": 1792326521, "displayOrder": 0, "durationMs": 65707, "entries": [], "eventName": "preToolUse", "executionMode": "sync", "handlerType": "command", "id": "pre-tool-use:0:/<session-flags>/config.toml:fx-6c97d0966c6343fba84635821b9f976a", "scope": "turn", "source": "sessionFlags", "sourcePath": "/<session-flags>/config.toml", "startedAt": 1792326456, "status": "completed", "statusMessage": null}], "hook_valid": true, "id": "op_50f85e46838442b08c6cfaa80436b96e", "intended": {"tool": "Bash", "tool_input": {"command": "sleep 8"}}, "kind": "command", "native": {"call": "fx-6c97d0966c6343fba84635821b9f976a", "listener": "listener_3e140ef208dc4346af7b89844b1abb1e", "thread": "01a14efb-0576-75b1-b990-a4e0df20689b", "turn": "01a14efb-52cb-77d0-9ab1-badb7fdfc9cf"}, "paths": [], "pre": {}, "started_item": {"aggregatedOutput": null, "command": "/bin/zsh -c 'sleep 8'", "commandActions": [{"command": "sleep 8", "type": "unknown"}], "cwd": "/private/tmp/c1-rework-3gcqdxau/long-repo", "durationMs": null, "exitCode": null, "id": "fx-6c97d0966c6343fba84635821b9f976a", "pluginId": null, "processId": "22705", "scriptPath": null, "source": "unifiedExecStartup", "status": "inProgress", "type": "commandExecution"}, "state": "running", "unit": "unit_781bdcbd5d494293938b04bf9c3fa398"}]}
```

</details>

<a id="c31"></a>

<details>
<summary>C31 — installed; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,os,signal,sqlite3,subprocess,time,hashlib,threading
from coordinator.client import Client,CoordinatorError
from coordinator.processes import identity
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"interrupt-setup.json").read_text());case=json.loads((root/"installed-setup.json").read_text());c=Client(setup["state"],timeout=120);unit=case["run"]["unit"];repo=Path(setup["repo"])
answer="Inspected complete object diff: only file00002.txt changes from corpus baseline to installed before checkpoint."
review=c.call("unit/review-record",{"unit":unit,"evidence":dict(case["diff"]["binding"],verdict="pass",completed=True,answer=answer,answer_digest=hashlib.sha256(answer.encode()).hexdigest(),review_identity="C1 direct object inspection 2026-10-18")})
params={"unit":unit,"review":review["review"],"metadata":json.loads((root/"setup.json").read_text())["metadata"]}
db=sqlite3.connect("file:"+str(Path(setup["state"])/"ledger.sqlite")+"?mode=ro",uri=True,isolation_level=None)
def publication():
 row=db.execute("SELECT id,data FROM records WHERE kind='publication' AND parent=?",(unit,)).fetchone()
 return dict(json.loads(row[1]),id=row[0]) if row else None
def installed(p):
 if not p or p["phase"]!="index_pending" or p.get("index_lock",{}).get("state")!="replacement_ready":return False
 rec=p["index_lock"]
 try:return os.stat(rec["index"]).st_ino==rec["inode"] and os.stat(rec["anchor"]).st_ino==rec["inode"] and not os.path.exists(rec["lock"])
 except FileNotFoundError:return False
stop=threading.Event();changes=[]
def change_unrelated():
 i=0
 while not stop.wait(.03):
  (repo/"file09998.txt").write_text("unrelated continues "+str(i)+"\n");changes.append(time.time());i+=1
writer=threading.Thread(target=change_unrelated);writer.start();before=c.call("daemon/status",{})
with ThreadPoolExecutor(1) as pool:
 pending=pool.submit(c.call,"unit/publish",params,key="after-index-replacement")
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  p=publication()
  if installed(p):
   if identity(before["pid"])["birth"]!=before["birth"]:raise RuntimeError("identity changed")
   os.kill(before["pid"],signal.SIGSTOP);stopped=publication()
   if installed(stopped):break
   os.kill(before["pid"],signal.SIGCONT)
  if pending.done():stop.set();writer.join();raise RuntimeError("replacement window missed")
  time.sleep(.0002)
 else:stop.set();writer.join();raise RuntimeError("replacement checkpoint timed out")
 checkpoint={"time":time.time(),"worker":{k:before[k] for k in ("pid","birth","generation")},"publication":stopped,"index_digest":hashlib.sha256(Path(stopped["index_lock"]["index"]).read_bytes()).hexdigest(),"unrelated_changes":len(changes)}
 (root/"installed-checkpoint.json").write_text(json.dumps(checkpoint,indent=2));os.kill(before["pid"],signal.SIGKILL)
 try:outcome=pending.result()
 except Exception as exc:outcome={"error":type(exc).__name__,"code":getattr(exc,"code",None)}
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>before["generation"]:break
 except(CoordinatorError,OSError):pass
 time.sleep(.1)
else:stop.set();writer.join();raise RuntimeError("recovery timeout")
stop.set();writer.join()
out={"checkpoint":checkpoint,"rpc_outcome":outcome,"final":publication(),"unrelated_changes":len(changes),"unknown":c.call("unknown/list",{"checkout":setup["checkout"]}),"commit_count":subprocess.check_output(["git","-C",str(repo),"rev-list","--count","HEAD"]).decode().strip(),"index_inode":(repo/".git/index").stat().st_ino,"same_installed_inode":(repo/".git/index").stat().st_ino==stopped["index_lock"]["inode"]}
(root/"installed-recovery.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"checkpoint": {"time": 1792326557.9616299, "worker": {"pid": 41665, "birth": "1792326339:360422", "generation": 4}, "publication": {"base_index_entries": {"file00002.txt": [{"mode": "100644", "oid": "74ccd65945e21968a328f6fa8ec51ef9cc2bf0a3", "stage": "0"}]}, "commit": "68bc0e100dfca7f33c8299ac86b8de42c55815d0", "config_digest": "3dab0b9b9edcc59256ce5632d7c85fcc832e5f334d2e6705256789a6bb9d3fc6", "created": 1792326555.9031029, "index_lock": {"anchor": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/c1-index_owner_82c66f4b73ac40029866af56d7c048c3", "checkout_identity": "a4247cb6aded55d4131105fd2aa4ff7e6091cc8b4cf74ad8a5cbce2836428996", "device": 16777230, "generation": 4, "index": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index", "inode": 748475804, "lock": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index.lock", "owner": {"birth": "1792326339:360422", "pid": 41665, "ppid": 30832, "uid": 501, "zombie": false}, "publication": "publication_3c5edf92fd174d5e8844055258b446d5", "replacement_digest": "e1416521a127f86ac35006b9d838b959d00ea9b9f5c70abd322ebd0e73e17094", "state": "replacement_ready", "token": "index_owner_82c66f4b73ac40029866af56d7c048c3"}, "metadata": {"author_date": "2026-10-18T12:30:00+00:00", "author_email": "c1@example.invalid", "author_name": "C1 Fixture", "committer_date": "2026-10-18T12:30:00+00:00", "committer_email": "c1@example.invalid", "committer_name": "C1 Fixture", "message": "C1 observed publication"}, "phase": "index_pending", "review": "review_a787501d4acd48fe977c1763a6e58eb1", "seal": "seal_2312aa10f476475f8e4785f86ad1dade", "unit": "unit_f9cbec34bd3d449b8a5337b66ab993ba", "id": "publication_3c5edf92fd174d5e8844055258b446d5"}, "index_digest": "e1416521a127f86ac35006b9d838b959d00ea9b9f5c70abd322ebd0e73e17094", "unrelated_changes": 111}, "rpc_outcome": {"error": "CoordinatorError", "code": "RPC_UNCERTAIN"}, "final": {"base_index_entries": {"file00002.txt": [{"mode": "100644", "oid": "74ccd65945e21968a328f6fa8ec51ef9cc2bf0a3", "stage": "0"}]}, "commit": "68bc0e100dfca7f33c8299ac86b8de42c55815d0", "config_digest": "3dab0b9b9edcc59256ce5632d7c85fcc832e5f334d2e6705256789a6bb9d3fc6", "created": 1792326555.9031029, "index_lock": {"anchor": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/c1-index_owner_82c66f4b73ac40029866af56d7c048c3", "checkout_identity": "a4247cb6aded55d4131105fd2aa4ff7e6091cc8b4cf74ad8a5cbce2836428996", "device": 16777230, "generation": 4, "index": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index", "inode": 748475804, "lock": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index.lock", "owner": {"birth": "1792326339:360422", "pid": 41665, "ppid": 30832, "uid": 501, "zombie": false}, "publication": "publication_3c5edf92fd174d5e8844055258b446d5", "replacement_digest": "e1416521a127f86ac35006b9d838b959d00ea9b9f5c70abd322ebd0e73e17094", "state": "checkpointed", "token": "index_owner_82c66f4b73ac40029866af56d7c048c3"}, "metadata": {"author_date": "2026-10-18T12:30:00+00:00", "author_email": "c1@example.invalid", "author_name": "C1 Fixture", "committer_date": "2026-10-18T12:30:00+00:00", "committer_email": "c1@example.invalid", "committer_name": "C1 Fixture", "message": "C1 observed publication"}, "phase": "complete", "review": "review_a787501d4acd48fe977c1763a6e58eb1", "seal": "seal_2312aa10f476475f8e4785f86ad1dade", "unit": "unit_f9cbec34bd3d449b8a5337b66ab993ba", "id": "publication_3c5edf92fd174d5e8844055258b446d5"}, "unrelated_changes": 217, "unknown": {"data": [{"checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "context": "{\"custody\":[],\"label\":\"UNKNOWN\"}", "created": 1792326555.680793, "edit": null, "id": "unknown_53daa4d3553e47d1bab3096da4c3679e", "path": "file09998.txt", "reason": "unexplained filesystem change", "restore": null, "revision": "rev_421c889da3b04b3892514497b31fb91f"}, {"checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "context": "{\"custody\":[],\"label\":\"UNKNOWN\"}", "created": 1792326557.6043808, "edit": null, "id": "unknown_b008993fe6f843cdbde77c97d5a409fc", "path": "file09998.txt", "reason": "unexplained filesystem change", "restore": null, "revision": "rev_2f42f452257f4536bead2bfbb4659bd9"}, {"checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "context": "{\"custody\":[],\"label\":\"UNKNOWN\"}", "created": 1792326559.962846, "edit": null, "id": "unknown_112f0570bc0043638c6abc39b9cf3706", "path": "file09998.txt", "reason": "daemon recovery filesystem snapshot", "restore": null, "revision": "rev_7a14f374749a46fdb8b75da43b120db2"}, {"checkout": "checkout_3e4acad4b95141f080f1930c3d2d0d30", "context": "{\"custody\":[],\"label\":\"UNKNOWN\"}", "created": 1792326561.65126, "edit": null, "id": "unknown_7b86495706c3460984910c4b53e01100", "path": "file09998.txt", "reason": "unexplained filesystem change", "restore": null, "revision": "rev_05277deb2bde4b81900b7e8fb076bfc1"}]}, "commit_count": "4", "index_inode": 748475804, "same_installed_inode": true}
```

</details>

<a id="c32"></a>

<details>
<summary>C32 — final-process; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 SOFTWARE_WORKFLOWS_PYTHON=/opt/homebrew/opt/python@3.15/bin/python3.15 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,os,subprocess,time
from coordinator.client import Client
root=Path("/private/tmp/c1-rework-3gcqdxau"); states={"short":root/"final-state1","long":root/("final-long-"+"z"*110)/"state"}; out={"states":{k:str(v) for k,v in states.items()},"repos":{},"checkouts":{},"status":{}}
def git(repo,args,data=None):
 env=dict(os.environ,GIT_AUTHOR_NAME="C1 Fixture",GIT_AUTHOR_EMAIL="c1@example.invalid",GIT_AUTHOR_DATE="2026-10-18T12:30:00+00:00",GIT_COMMITTER_NAME="C1 Fixture",GIT_COMMITTER_EMAIL="c1@example.invalid",GIT_COMMITTER_DATE="2026-10-18T12:30:00+00:00")
 r=subprocess.run(["git","-C",str(repo),*args],input=data,capture_output=True,env=env)
 if r.returncode:raise RuntimeError((args,r.returncode,r.stderr.decode()))
 return r.stdout.decode().strip()
for key,state in states.items():
 repo=root/("final-"+key+"-repo");repo.mkdir(mode=0o700);(repo/"base.txt").write_bytes(b"final baseline\n");git(repo,["init","-q"]);oid=git(repo,["hash-object","-w","--stdin"],b"final baseline\n");git(repo,["read-tree","--empty"]);git(repo,["update-index","--index-info"],("100644 "+oid+"\tbase.txt\n").encode());tree=git(repo,["write-tree"]);commit=git(repo,["commit-tree",tree],b"final baseline\n");git(repo,["update-ref","HEAD",commit]);git(repo,["read-tree",commit])
 c=Client(state,timeout=1800);c.ensure();out["status"][key]=c.call("daemon/status",{});out["repos"][key]=str(repo);out["checkouts"][key]=c.call("checkout/register",{"path":str(repo)})["id"]
(root/"final-setup.json").write_text(json.dumps(out,indent=2));print(json.dumps(out),flush=True)
c=Client(states["long"],timeout=1800);run=c.call("run/start",{"checkout":out["checkouts"]["long"],"mode":"native","task":"C1_SHARED","fixture":str(root/"fixture-more"),"model":"gpt-6-astra"})
observations=[];deadline=time.monotonic()+25
while time.monotonic()<deadline:
 custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==run["unit"]]
 terminals=c.call("run/terminals",{"run":run["run"]});state=c.call("run/status",{"run":run["run"]})
 observations.append({"time":time.time(),"state":state["state"],"custody":custody,"terminals":terminals})
 if custody and all(r["state"]=="released" for r in custody) and state["state"]=="completed":break
 time.sleep(.15)
out={"run":run,"observations":observations,"unknown":c.call("unknown/list",{"checkout":out["checkouts"]["long"]})}
(root/"final-command-process.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
Warning: truncated output (original token count: 29934)
Total output lines: 2

{"states": {"short": "/private/tmp/c1-rework-3gcqdxau/final-state1", "long": "/private/tmp/c1-rework-3gcqdxau/final-long-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/state"}, "repos": {"short": "/private/tmp/c1-rework-3gcqdxau/final-short-repo", "long": "/private/tmp/c1-rework-3gcqdxau/final-long-repo"}, "checkouts": {"short": "checkout_692101887dbf43ccb80a91a4724f3d42", "long": "checkout_48b78260197747a0824637dbcd7bea82"}, "status": {"short": {"birth": "1792326787:300089", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/s32kzhvta/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/s32kzhvta/s", "resolved_bytes": 31, "uid": 501}, "generation": 1, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "pid": 56807, "ready": true, "sqlite": "3.54.0", "state_root": "/private/tmp/c1-rework-3gcqdxau/final-state1", "writers": []}, "long": {"birth": "1792326788:169857", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/sfg_dya6w/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/sfg_dya6w/s", "resolved_bytes": 31, "uid": 501}, "generation": 1, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "pid": 56853, "ready": true, "sqlite": "3.54.0", "state_root": "/private/tmp/c1-rework-3gcqdxau/final-long-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/state", "writers": []}}}
{"run": {"actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "attempt": "attempt_78f721c739fc4d6cab11d956fcf7c62a", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_05218fb24ba741c18c696eb9ddac3cec", "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b"}, "observations": [{"time": 1792326809.260488, "state": "running", "custody": [], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326809.4191082, "state": "running", "custody": [{"_kind": "custody", "_revision": 2, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "operation": "op_3d72bc964d86447ebd1441154e6be206", "processes": [], "state": "held", "terminals": [], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b"}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326809.57445, "state": "running", "custody": [{"_kind": "custody", "_revision": 2, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "operation": "op_3d72bc964d86447ebd1441154e6be206", "processes": [], "state": "held", "terminals": [], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b"}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326809.729918, "state": "running", "custody": [{"_kind": "custody", "_revision": 5, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [{"command": "sleep 8", "cpuPercent": null, "cwd": "/private/tmp/c1-rework-3gcqdxau/final-long-repo", "itemId": "fx-3abaa99517bf4bceacef27790ba98357", "osPid": null, "processId": "98052", "rssKb": null}], "nextCursor": null}}, {"time": 1792326809.884109, "state": "running", "custody": [{"_kind": "custody", "_revision": 7, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [{"command": "sleep 8", "cpuPercent": null, "cwd": "/private/tmp/c1-rework-3gcqdxau/final-long-repo", "itemId": "fx-3abaa99517bf4bceacef27790ba98357", "osPid": null, "processId": "98052", "rssKb": null}], "nextCursor": null}}, {"time": 1792326810.0468879, "state": "running", "custody": [{"_kind": "custody", "_revision": 7, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [{"command": "sleep 8", "cpuPercent": null, "cwd": "/private/tmp/c1-rework-3gcqdxau/final-long-repo", "itemId": "fx-3abaa99517bf4bceacef27790ba98357", "osPid": null, "processId": "98052", "rssKb": null}], "nextCursor": null}}, {"time": 1792326810.208394, "state": "running", "custody": [{"_kind": "custody", "_revision": 7, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 179232…26934 tokens truncated…bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326833.11242, "state": "completed", "custody": [{"_kind": "custody", "_revision": 56, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326833.270762, "state": "completed", "custody": [{"_kind": "custody", "_revision": 56, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326833.432998, "state": "completed", "custody": [{"_kind": "custody", "_revision": 56, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326833.594475, "state": "completed", "custody": [{"_kind": "custody", "_revision": 56, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326833.757379, "state": "completed", "custody": [{"_kind": "custody", "_revision": 56, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326833.918571, "state": "completed", "custody": [{"_kind": "custody", "_revision": 56, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326834.074358, "state": "completed", "custody": [{"_kind": "custody", "_revision": 56, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [], "nextCursor": null}}, {"time": 1792326834.2359731, "state": "completed", "custody": [{"_kind": "custody", "_revision": 58, "actor": "actor_aefbd5476565465bb69e3eb85ce16acc", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "sleep 8", "created": 1792326809.416079, "excludes": [], "generation": 1, "id": "custody_b14ace549f6d4bfcbba91bddf9e16095", "level": "shared", "observed_processes": [], "operation": "op_3d72bc964d86447ebd1441154e6be206", "process_error": {"pids": [58646]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["98052"], "unit": "unit_a2cc1bb8be174514a0c17b9294d6ef9b", "unmediated": true}], "terminals": {"data": [], "nextCursor": null}}], "unknown": {"data": [{"checkout": "checkout_48b78260197747a0824637dbcd7bea82", "context": "{\"code\":\"PROCESS_AMBIGUOUS\",\"custody\":\"custody_b14ace549f6d4bfcbba91bddf9e16095\"}", "created": 1792326809.62851, "edit": null, "id": "unknown_33d19f3407ed47f5bbb372a8ab590114", "path": "*", "reason": "process inventory unverifiable", "restore": null, "revision": null}]}}
```

</details>

<a id="c33"></a>

<details>
<summary>C33 — scope-restoration-setup; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,base64,hashlib
from coordinator.client import Client,CoordinatorError
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"setup.json").read_text());cases=json.loads((root/"scope-and-seals.json").read_text());c=Client("/private/tmp/c1-rework-3gcqdxau/ssssssssssss",timeout=120)
print(json.dumps({"scope_keys":list(cases)}))
scope=cases["scope"];print(json.dumps(scope))
PY
```

Captured output:

```text
{"scope_keys": ["scope", "postseal", "core", "time"]}
{"run": {"actor": "actor_6270789e8dab41e891f87a69460a0000", "attempt": "attempt_59e1b7ec348b49fca6801a9f37e2f830", "policy": null, "run": "run_87e41b2ffd6f4c29a7350f46c6ccf11d", "unit": "unit_d4564676463b4908afcbab189532f5c4"}, "edit": "op_c2c2b1038ad641cd8b5daf43aa58daf5", "refusal": {"code": "UNKNOWN_BLOCKS_SEAL", "message": "Complete operation scope includes unresolved UNKNOWN history", "details": {"paths": ["a.txt", "b.txt"], "unknowns": ["unknown_ea70968ef1b64781a2fe8736b3c4500b"]}, "kind": "refusal"}, "claims": [{"_kind": "claim", "_revision": 1, "actor": "actor_6270789e8dab41e891f87a69460a0000", "checkout": "checkout_f92a5a18d84249febcd9ab8827c60161", "id": "claim_941e3c657a784d32a247efe126d78ac1", "key": "a.txt", "path": "a.txt", "state": "held", "unit": "unit_d4564676463b4908afcbab189532f5c4"}, {"_kind": "claim", "_revision": 1, "actor": "actor_6270789e8dab41e891f87a69460a0000", "checkout": "checkout_f92a5a18d84249febcd9ab8827c60161", "id": "claim_b7becdde068c4d76a2b883d54feb4291", "key": "b.txt", "path": "b.txt", "state": "held", "unit": "unit_d4564676463b4908afcbab189532f5c4"}]}
```

</details>

<a id="c34"></a>

<details>
<summary>C34 — provider-owned; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B -m coordinator fixture serve --directory '/private/tmp/c1-rework-3gcqdxau/fixture-owned' --plan '{"default":[{"text":"idle"}],"routes":{"C1_PAR_A":[{"delay":2,"tool":"declare","arguments":{"command":"/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c '\''import time; time.sleep(4)'\''","reason":"wait without filesystem effects","level":"shared"}},{"command":"/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c '\''import time; time.sleep(4)'\''","arguments":{"yield_time_ms":1000}},{"patch":"*** Begin Patch\n*** Add File: parallel_a.py\n+def parallel_a(value):\n+    return value + 3\n*** End Patch"},{"text":"parallel A done"}],"C1_PAR_B":[{"delay":2,"tool":"declare","arguments":{"command":"/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c '\''import time; time.sleep(4)'\''","reason":"wait without filesystem effects","level":"shared"}},{"command":"/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c '\''import time; time.sleep(4)'\''","arguments":{"yield_time_ms":1000}},{"patch":"*** Begin Patch\n*** Add File: parallel_b.py\n+def parallel_b(value):\n+    return value * 3\n*** End Patch"},{"text":"parallel B done"}],"C1_SURVIVOR":[{"command":"/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c '\''import subprocess,sys,time; child=subprocess.Popen([sys.executable,\"-B\",\"-c\",\"import time; time.sleep(12)\"]); time.sleep(3)'\''","arguments":{"yield_time_ms":1000}},{"text":"turn finished with command child"}],"C1_HOOKS":[{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"tool":"status","arguments":{}},{"text":"hook timing completed"}],"C1_CHILD_ROOT":[{"tool":"spawn_agent","namespace":"collaboration","arguments":{"task_name":"native_child_observed","message":"C1_CHILD_WORK","fork_turns":"none"}},{"text":"root child launched"}],"C1_CHILD_WORK":[{"patch":"*** Begin Patch\n*** Add File: child.py\n+def child(value):\n+    return value + 7\n*** End Patch"},{"command":"/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c '\''print(\"child command\")'\''"},{"text":"child complete"}]}}'
```

Captured output:

```text
{"fixture": "/private/tmp/c1-rework-3gcqdxau/fixture-owned", "pid": 61635, "port": 56229}
```

</details>

<a id="c35"></a>

<details>
<summary>C35 — final-restart; observed exit 1</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import base64,json,os,signal,time
from coordinator.client import Client,CoordinatorError
from coordinator.processes import identity
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"final-setup.json").read_text())
for key in ("short","long"):
 c=Client(setup["states"][key],timeout=1800);before=c.call("daemon/status",{})
 if identity(before["pid"])["birth"]!=before["birth"]:raise RuntimeError("identity changed")
 os.kill(before["pid"],signal.SIGKILL)
 deadline=time.monotonic()+180
 while time.monotonic()<deadline:
  try:
   after=c.call("daemon/status",{})
   if after["ready"] and after["generation"]>before["generation"]:break
  except(CoordinatorError,OSError):pass
  time.sleep(.1)
 else:raise RuntimeError("restart timeout")
 print(json.dumps({"state":key,"generation":after["generation"],"locks":after["locks"]}),flush=True)
c=Client(setup["states"]["long"],timeout=1800)
for r in c.call("records/list",{"kind":"custody","limit":1000})["data"]:
 if r["state"]!="released":c.call("custody/reconcile",{"custody":r["id"]})
run=c.call("run/start",{"checkout":setup["checkouts"]["long"],"mode":"lead","task":"restore ambiguous process observation"})
unknown=c.call("unknown/list",{"checkout":setup["checkouts"]["long"]})["data"]
restored=[c.call("unknown/restore",{"actor":run["actor"],"unknown":u["id"],"revisions":{}}) for u in unknown]
edit=c.call("lead-edit",{"actor":run["actor"],"changes":{"base.txt":{"mode":"100644","bytes_base64":base64.b64encode(b"final baseline\n").decode()}}})
c.call("claim/handback",{"unit":run["unit"]})
out={"restored":restored,"new_edit":edit["id"],"unknown_after":c.call("unknown/list",{"checkout":setup["checkouts"]["long"]})};(root/"final-restoration.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"state": "short", "generation": 2, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}}

{"state": "long", "generation": 2, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}}
Traceback (most recent call last):
  File "<stdin>", line 24, in <module>
  File "/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/coordinator/client.py", line 95, in call
    raise CoordinatorError(error["code"], error["message"], error.get("details"),
                           error.get("kind") == "failure")
coordinator.client.CoordinatorError: A checkout-wide UNKNOWN requires an explicit known revision for every ledger path
```

</details>

<a id="c36"></a>

<details>
<summary>C36 — final-admission-short; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,subprocess,sys,time
root=Path("/private/tmp/c1-rework-3gcqdxau");scratch=root/("final-admission-short-"+"short")
args=[sys.executable,"-B","-m","coordinator","--state","/private/tmp/c1-rework-3gcqdxau/final-state1","admission","--scratch",str(scratch)]
for probe in ("hooks","evidence","dynamic","delivery","tty","reconnect","terminals","policy","validation","exec_policy"):args+=["--probe",probe]
started=time.time();result=subprocess.run(args,capture_output=True,text=True)
(root/"final-admission-short.json").write_text(result.stdout)
report=json.loads(result.stdout)
print(json.dumps({"argv":args,"started":started,"finished":time.time(),"exit_code":result.returncode,"completed":report.get("completed"),"production_admitted":report.get("production_admitted"),"cases":[{"probe":p["probe"],"passed":p["passed"]} for p in report.get("cases",[])],"socket":report.get("fingerprint",{}).get("socket"),"adapter_digest":report.get("fingerprint",{}).get("adapter_digest")}))
sys.exit(result.returncode)
PY
```

Captured output:

```text
{"argv": ["/opt/homebrew/opt/python@3.15/bin/python3.15", "-B", "-m", "coordinator", "--state", "/private/tmp/c1-rework-3gcqdxau/final-state1", "admission", "--scratch", "/private/tmp/c1-rework-3gcqdxau/final-admission-short-short", "--probe", "hooks", "--probe", "evidence", "--probe", "dynamic", "--probe", "delivery", "--probe", "tty", "--probe", "reconnect", "--probe", "terminals", "--probe", "policy", "--probe", "validation", "--probe", "exec_policy"], "started": 1792327076.512667, "finished": 1792327096.995478, "exit_code": 0, "completed": null, "production_admitted": null, "cases": [], "socket": null, "adapter_digest": null}
```

</details>

<a id="c37"></a>

<details>
<summary>C37 — final-admission-long; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,subprocess,sys,time
root=Path("/private/tmp/c1-rework-3gcqdxau");scratch=root/("final-admission-long-"+"wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww")
args=[sys.executable,"-B","-m","coordinator","--state","/private/tmp/c1-rework-3gcqdxau/final-long-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/state","admission","--scratch",str(scratch)]
for probe in ("hooks","evidence","dynamic","delivery","tty","reconnect","terminals","policy","validation","exec_policy"):args+=["--probe",probe]
started=time.time();result=subprocess.run(args,capture_output=True,text=True)
(root/"final-admission-long.json").write_text(result.stdout)
report=json.loads(result.stdout)
print(json.dumps({"argv":args,"started":started,"finished":time.time(),"exit_code":result.returncode,"completed":report.get("completed"),"production_admitted":report.get("production_admitted"),"cases":[{"probe":p["probe"],"passed":p["passed"]} for p in report.get("cases",[])],"socket":report.get("fingerprint",{}).get("socket"),"adapter_digest":report.get("fingerprint",{}).get("adapter_digest")}))
sys.exit(result.returncode)
PY
```

Captured output:

```text
{"argv": ["/opt/homebrew/opt/python@3.15/bin/python3.15", "-B", "-m", "coordinator", "--state", "/private/tmp/c1-rework-3gcqdxau/final-long-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/state", "admission", "--scratch", "/private/tmp/c1-rework-3gcqdxau/final-admission-long-wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww", "--probe", "hooks", "--probe", "evidence", "--probe", "dynamic", "--probe", "delivery", "--probe", "tty", "--probe", "reconnect", "--probe", "terminals", "--probe", "policy", "--probe", "validation", "--probe", "exec_policy"], "started": 1792327076.512636, "finished": 1792327096.995473, "exit_code": 0, "completed": null, "production_admitted": null, "cases": [], "socket": null, "adapter_digest": null}
```

</details>

<a id="c38"></a>

<details>
<summary>C38 — restoration-corrected; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import base64,json
from coordinator.client import Client,CoordinatorError
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"final-setup.json").read_text());c=Client(setup["states"]["long"],timeout=1800)
run=next(r for r in c.call("records/list",{"kind":"run","limit":1000})["data"] if r["task"]=="restore ambiguous process observation")
revisions={r["path"]:r["id"] for r in c.call("checkout/revisions",{"checkout":setup["checkouts"]["long"],"limit":1000})["data"] if r["attribution"]=="baseline"}
unknown=c.call("unknown/list",{"checkout":setup["checkouts"]["long"]})["data"]
restored=[c.call("unknown/restore",{"actor":run["root_actor"],"unknown":u["id"],"revisions":revisions}) for u in unknown]
try:c.call("unit/seal",{"unit":run["unit"],"validation":[]})
except CoordinatorError as exc:before_edit=exc.envelope()
edit=c.call("lead-edit",{"actor":run["root_actor"],"changes":{"base.txt":{"mode":"100644","bytes_base64":base64.b64encode(b"final baseline\n").decode()}}})
c.call("claim/handback",{"unit":run["unit"]})
out={"restored":restored,"before_new_edit":before_edit,"new_edit":edit["id"],"unknown_after":c.call("unknown/list",{"checkout":setup["checkouts"]["long"]})};(root/"final-restoration.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"restored": [{"required_new_edit_scope": ["base.txt"], "restore": "op_400131756370407ebcf4f6d8511ae0d0", "state": "restored_requires_new_edit", "unknown": "unknown_33d19f3407ed47f5bbb372a8ab590114"}], "before_new_edit": {"code": "UNKNOWN_BLOCKS_SEAL", "message": "Complete operation scope includes unresolved UNKNOWN history", "details": {"paths": ["base.txt"], "unknowns": ["unknown_33d19f3407ed47f5bbb372a8ab590114"]}, "kind": "refusal"}, "new_edit": "op_7a345773ccbe402c860b18e498b0693d", "unknown_after": {"data": []}}
```

</details>

<a id="c39"></a>

<details>
<summary>C39 — final-parallel; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time,subprocess
from coordinator.client import Client,CoordinatorError
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"final-setup.json").read_text()); c=Client("/private/tmp/c1-rework-3gcqdxau/final-long-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/state",timeout=1800); checkout=setup["checkouts"]["long"]
runs=[c.call("run/start",{"checkout":checkout,"mode":"native","task":"","fixture":str(root/"fixture-owned"),"model":"gpt-6-astra"}) for _ in range(2)]
print(json.dumps({"idle_roots":runs}),flush=True)
with ThreadPoolExecutor(2) as pool:
 messages=list(pool.map(lambda pair:c.call("message/send",{"sender":"lead","recipient":pair[0]["actor"],"text":pair[1],"wake":"start"}),zip(runs,["C1_PAR_A","C1_PAR_B"])))
observations=[];deadline=time.monotonic()+40
while time.monotonic()<deadline:
 actors=[a for a in c.call("records/list",{"kind":"actor","limit":1000})["data"] if a["id"] in {r["actor"] for r in runs}]
 reservations=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"] in {r["unit"] for r in runs}]
 observations.append({"time":time.time(),"active":[a["id"] for a in actors if a.get("active_turn")],"held":[r for r in reservations if r["state"]!="released"]})
 if time.monotonic()>deadline-35 and all(not a.get("active_turn") for a in actors) and all(r["state"]=="released" for r in reservations):break
 time.sleep(.2)
try: c.call("claim/acquire",{"actor":runs[1]["actor"],"paths":["parallel_a.py"]})
except CoordinatorError as exc: conflict=exc.envelope()
queued=c.call("message/send",{"sender":"lead","recipient":runs[0]["actor"],"text":"Idle queue delivery observation","wake":"queue"})
deadline=time.monotonic()+20
while time.monotonic()<deadline:
 records=c.call("records/list",{"kind":"message","limit":1000})["data"]
 states=[m for m in records if m["id"] in {x["id"] for x in messages+[queued]}]
 if len(states)==3 and all(m["state"]=="acknowledged" for m in states):break
 time.sleep(.1)
survivor=c.call("run/start",{"checkout":checkout,"mode":"native","task":"C1_SURVIVOR","fixture":str(root/"fixture-owned"),"model":"gpt-6-astra"})
survivor_observations=[];deadline=time.monotonic()+30
while time.monotonic()<deadline:
 actor=next(a for a in c.call("records/list",{"kind":"actor","limit":1000})["data"] if a["id"]==survivor["actor"])
 custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==survivor["unit"]]
 terminals=c.call("run/terminals",{"run":survivor["run"]})
 survivor_observations.append({"time":time.time(),"actor_state":actor["state"],"active_turn":actor.get("active_turn"),"custody":custody,"terminals":terminals})
 if custody and all(r["state"]=="released" for r in custody):break
 time.sleep(.3)
out={"runs":runs,"messages":states,"parallel_observations":observations,"conflict":conflict,"survivor":survivor,"survivor_observations":survivor_observations}
for kind in ("operation","actor","listener"):
 out[kind]=[r for r in c.call("records/list",{"kind":kind,"limit":1000})["data"] if r.get("unit") in {x["unit"] for x in runs+[survivor]}]
out["unknown"]=c.call("unknown/list",{"checkout":checkout})
(root/"final-parallel-and-survivors.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"parallel_max_active":max(len(o["active"]) for o in observations),"parallel_max_shared":max(len([r for r in o["held"] if r["level"]=="shared"]) for o in observations),"messages":[{"id":m["id"],"state":m["state"],"attempts":m["attempts"]} for m in states],"conflict":conflict,"survivor_held_after_turn":any(o["active_turn"] is None and any(r["state"]!="released" and r.get("processes") for r in o["custody"]) for o in survivor_observations),"survivor_final":survivor_observations[-1],"operations":[{"kind":r["kind"],"state":r["state"],"refusal":r.get("refusal")} for r in out["operation"]],"unknown":out["unknown"]}))
PY
```

Captured output:

```text
{"idle_roots": [{"actor": "actor_d731788c3cb8413885469ef2816fc580", "attempt": "attempt_bca16317a22a40148a8eec7131a6fb8c", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_c6addb5f56ad42899105fdaaa5be2475", "unit": "unit_171b48ff6d5148ee8fa7efe93cfb3d8e"}, {"actor": "actor_dacfa53ae35c4773a5247195f263d4f9", "attempt": "attempt_c82f4416302a405986b6e0fb6a3e94b1", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_73dfb032fb9d4846bd6958586e0616d9", "unit": "unit_2bced52306e44a45a05be2f1b7caff97"}]}

{"parallel_max_active": 2, "parallel_max_shared": 2, "messages": [{"id": "msg_63ec409313904e2cb6eae3fd549b65c7", "state": "entered_context", "attempts": ["delivery_988f61f71a89458197e5a19b7b237f87"]}, {"id": "msg_67901e79338e48cb8cdb593f8f4e1690", "state": "acknowledged", "attempts": ["delivery_a7b37b9efe494ced8012c8d5f4b85eaf"]}, {"id": "msg_eb430f458c6445f4af1a599aa03c0996", "state": "acknowledged", "attempts": ["delivery_45ff0b593f7941cc9a4e2280e528ab42"]}], "conflict": {"code": "CLAIM_CONFLICT", "message": "Scope held by unit_171b48ff6d5148ee8fa7efe93cfb3d8e; send a message to that holder or the lead, then request lead-authorized handback", "details": {"conflicts": [{"holder": "unit_171b48ff6d5148ee8fa7efe93cfb3d8e", "kind": "claim", "scope": "parallel_a.py"}], "request": "request_3b4e086e54de4c70b5051260a574dd7b"}, "kind": "refusal"}, "survivor_held_after_turn": true, "survivor_final": {"time": 1792327202.5302558, "actor_state": "completed", "active_turn": null, "custody": [{"_kind": "custody", "_revision": 35, "actor": "actor_9e4cfd486f30407a9f79279cb205aa53", "checkout": "checkout_48b78260197747a0824637dbcd7bea82", "command": "/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import subprocess,sys,time; child=subprocess.Popen([sys.executable,\"-B\",\"-c\",\"import time; time.sleep(12)\"]); time.sleep(3)'", "completed": 1792327202.298527, "created": 1792327189.594244, "excludes": ["parallel_b.py", "parallel_a.py"], "generation": 2, "id": "custody_fe49800c7f0f47dbb5f8d94877b29510", "level": "exclusive", "observed_processes": [{"birth": "1792327189:602873", "lineage": true, "marked": true, "markers": {"CODEX_SESSION_ID": "01a14f06-3717-7383-a962-5e3e5e687ab4", "CODEX_THREAD_ID": "01a14f06-3717-7383-a962-5e3e5e687ab4", "CODEX_TOOL_CALL_ID": "fx-83d7880df3774605a05af60eb7170f2e"}, "pid": 79825, "ppid": 76942, "uid": 501, "zombie": false}, {"birth": "1792327189:621520", "lineage": true, "marked": true, "markers": {"CODEX_SESSION_ID": "01a14f06-3717-7383-a962-5e3e5e687ab4", "CODEX_THREAD_ID": "01a14f06-3717-7383-a962-5e3e5e687ab4", "CODEX_TOOL_CALL_ID": "fx-83d7880df3774605a05af60eb7170f2e"}, "pid": 79826, "ppid": 1, "uid": 501, "zombie": false}], "operation": "op_87d57b9b5fd640b7b1e06b686fb56b74", "processes": [], "state": "released", "terminals": ["81900"], "unit": "unit_097441a5923b4e4fb659059b1dd17f85"}], "terminals": {"data": [], "nextCursor": null}}, "operations": [{"kind": "command", "state": "reconciled", "refusal": null}, {"kind": "command", "state": "reconciled", "refusal": null}, {"kind": "patch", "state": "verified", "refusal": null}, {"kind": "patch", "state": "verified", "refusal": null}, {"kind": "command", "state": "reconciled", "refusal": null}], "unknown": {"data": []}}
```

</details>

<a id="c40"></a>

<details>
<summary>C40 — final-hooks; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,statistics,sqlite3,subprocess
from coordinator.client import Client
from coordinator.processes import identity
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"final-setup.json").read_text());c=Client("/private/tmp/c1-rework-3gcqdxau/final-state1",timeout=1800)
run=c.call("run/start",{"checkout":setup["checkouts"]["short"],"mode":"native","task":"C1_HOOKS","fixture":str(root/"fixture-owned"),"model":"gpt-6-astra"})
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 state=c.call("run/status",{"run":run["run"]})
 if state["state"]=="completed":break
 time.sleep(.2)
db=sqlite3.connect("file:/private/tmp/c1-rework-3gcqdxau/final-state1/ledger.sqlite?mode=ro",uri=True)
actor=next(r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["id"]==run["actor"])
listener=next(r for r in c.call("records/list",{"kind":"listener","limit":1000})["data"] if r["id"]==actor["listener"])
events=[{"time":r[0],"message":json.loads(r[1])} for r in db.execute("SELECT time,data FROM events WHERE kind='native' AND subject=? ORDER BY seq",(actor["listener"],))]
completed=[r for r in events if r["message"].get("method")=="hook/completed" and r["message"].get("params",{}).get("threadId")==actor["thread"]]
durations=[r["message"]["params"]["run"]["durationMs"] for r in completed if "durationMs" in r["message"]["params"].get("run",{})]
start_events={r["message"]["params"].get("run",{}).get("id"):r["time"] for r in events if r["message"].get("method")=="hook/started"}
if not durations:
 durations=[1000*(r["time"]-start_events[r["message"]["params"]["run"]["id"]]) for r in completed if r["message"]["params"].get("run",{}).get("id") in start_events]
out={"run":run,"state":state,"hook_count":len(completed),"durations_ms":durations,"example":completed[:1],"median_ms":statistics.median(durations) if durations else None,"p95_ms":sorted(durations)[int(.95*(len(durations)-1))] if durations else None,"listener":{"pid":listener["pid"],"birth":listener["birth"],"observed_birth":identity(listener["pid"])["birth"],"rss_kib":int(subprocess.check_output(["ps","-o","rss=","-p",str(listener["pid"])]).strip())}}
(root/"final-hook-performance.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"run": {"actor": "actor_a113c53c255f4dec985a97be12efd7ec", "attempt": "attempt_0c163d3b7a4c4ac5a60d154050dc7067", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_8b9e8e0c83c4481fa0b04a505c455253", "unit": "unit_0d473453a93c4cc584351e637a973d7e"}, "state": {"_kind": "run", "_revision": 3, "attempt": "attempt_0c163d3b7a4c4ac5a60d154050dc7067", "checkout": "checkout_692101887dbf43ccb80a91a4724f3d42", "id": "run_8b9e8e0c83c4481fa0b04a505c455253", "mode": "native", "root_actor": "actor_a113c53c255f4dec985a97be12efd7ec", "state": "completed", "task": "C1_HOOKS", "unit": "unit_0d473453a93c4cc584351e637a973d7e"}, "hook_count": 110, "durations_ms": [50, 42, 42, 44, 46, 48, 44, 46, 47, 43, 42, 42, 42, 42, 41, 44, 43, 42, 41, 43, 47, 45, 45, 46, 46, 42, 42, 43, 42, 42, 41, 42, 48, 42, 42, 43, 46, 48, 47, 43, 42, 43, 45, 42, 42, 43, 44, 43, 43, 43, 46, 42, 43, 42, 43, 43, 43, 44, 50, 45, 45, 43, 44, 43, 43, 42, 44, 41, 42, 43, 42, 42, 43, 46, 47, 46, 47, 45, 45, 43, 44, 44, 42, 41, 43, 43, 42, 43, 44, 55, 49, 47, 49, 48, 44, 44, 46, 44, 46, 44, 45, 44, 45, 42, 43, 43, 42, 46, 42, 43], "example": [{"time": 1792327124.01833, "message": {"emittedAtMs": 1792327124018, "method": "hook/completed", "params": {"run": {"completedAt": 1792327124, "displayOrder": 0, "durationMs": 50, "entries": [], "eventName": "preToolUse", "executionMode": "sync", "handlerType": "command", "id": "pre-tool-use:0:/<session-flags>/config.toml:fx-24cddd8eaaa643448c38666c5e4c1d86", "scope": "turn", "source": "sessionFlags", "sourcePath": "/<session-flags>/config.toml", "startedAt": 1792327123, "status": "completed", "statusMessage": null}, "threadId": "01a14f05-362e-7d63-a95a-2bb24863749f", "turnId": "01a14f05-83dc-76e2-af26-ed03e27d0e42"}}}], "median_ms": 43.0, "p95_ms": 48, "listener": {"pid": 68865, "birth": "1792327103:842299", "observed_birth": "1792327103:842299", "rss_kib": 112960}}
```

</details>

<a id="c41"></a>

<details>
<summary>C41 — hard-refusals-setup; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,base64,hashlib,subprocess,os
from coordinator.client import Client,CoordinatorError
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"setup.json").read_text());cases=json.loads((root/"scope-and-seals.json").read_text());c=Client("/private/tmp/c1-rework-3gcqdxau/ssssssssssss",timeout=120);scope=cases["scope"]["run"]
baseline=next(r for r in c.call("checkout/revisions",{"checkout":setup["checkouts"]["scope"],"path":"a.txt","limit":1000})["data"] if r["attribution"]=="baseline")
unknown=c.call("unknown/list",{"checkout":setup["checkouts"]["scope"]})["data"][0]
out={"restore":c.call("unknown/restore",{"actor":scope["actor"],"unknown":unknown["id"],"revision":baseline["id"]})}
try:c.call("unit/seal",{"unit":scope["unit"],"validation":[]})
except CoordinatorError as exc:out["restoration_alone"]=exc.envelope()
out["new_edit"]=c.call("lead-edit",{"actor":scope["actor"],"changes":{"a.txt":{"mode":"100644","bytes_base64":base64.b64encode(b"restored and verified anew\n").decode()}}})["id"]
out["seal_after_new_edit"]=c.call("unit/seal",{"unit":scope["unit"],"validation":[]})["id"]
core=json.loads((root/"publication-and-base.json").read_text());unit=core["fresh_base"]["b"]["unit"];metadata=setup["metadata"]
for label,review in (("missing_review","review_missing"),("wrong_review",core["core"]["publication"]["review"])):
 try:c.call("unit/publish",{"unit":unit,"review":review,"metadata":metadata})
 except CoordinatorError as exc:out[label]=exc.envelope()
diff=c.call("unit/diff",{"unit":unit});bad=dict(diff["binding"],candidate="0"*40)
try:c.call("unit/review-record",{"unit":unit,"evidence":bad})
except CoordinatorError as exc:out["stale_binding"]=exc.envelope()
print(json.dumps({"core_B_diff":diff}),flush=True)
out["core_diff"]=diff
(root/"hard-refusals-setup.json").write_text(json.dumps(out,indent=2));print(json.dumps({k:v for k,v in out.items() if k!="core_diff"}))
PY
```

Captured output:

```text
{"core_B_diff": {"binding": {"base": "03b52828808b0bb5dbf29d91b4ab6fdccf713b2e", "candidate": "96a0e83187fb63def02f4ea6f7825b3b03e4f55a", "provenance": "14ea91da2ca79967eeafa8a769a6817af53b22d0fe7dc11b1106fb712256004f", "repository": "525f063d73da6f1e77371c5e15eb1cb30ffdc99d7c731f905ee6eeaed25a950d", "seal": "seal_1eaca0c4cb6f40fa929f94f10daadc9d", "unit": "unit_1c1945babca04dac9a3edcf520dcd4c2", "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "diff": "diff --git a/a.txt b/a.txt\nindex 67900fa..a577d49 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-core verified a.txt\n+unit B follows published A\n"}}
{"restore": {"restore": "op_d2fe5774a7fc4232a7add755a2f1740f", "state": "restored_requires_new_edit", "unknown": "unknown_ea70968ef1b64781a2fe8736b3c4500b"}, "restoration_alone": {"code": "UNKNOWN_BLOCKS_SEAL", "message": "Complete operation scope includes unresolved UNKNOWN history", "details": {"paths": ["a.txt", "b.txt"], "unknowns": ["unknown_ea70968ef1b64781a2fe8736b3c4500b"]}, "kind": "refusal"}, "new_edit": "op_9fb1ceb12da44b35b1bd3f5b8a5e69fe", "seal_after_new_edit": "seal_9ae8435279ec4a1994fe5341fb266a6b", "missing_review": {"code": "NOT_FOUND", "message": "Record does not exist", "details": {"id": "review_missing"}, "kind": "refusal"}, "wrong_review": {"code": "REVIEW_BINDING", "message": "Missing, stale or mismatched completed review", "details": {}, "kind": "refusal"}, "stale_binding": {"code": "REVIEW_BINDING", "message": "Review evidence does not match sealed candidate", "details": {}, "kind": "refusal"}}
```

</details>

<a id="c42"></a>

<details>
<summary>C42 — process-diagnostics; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json
root=Path("/private/tmp/c1-rework-3gcqdxau")
for kind in ("short","long"):
 envelope=json.loads((root/("final-admission-"+kind+".json")).read_text());print(json.dumps({"kind":kind,"envelope_keys":list(envelope)}))
 report=envelope.get("result",envelope)
 print(json.dumps({"kind":kind,"completed":report.get("completed"),"production_admitted":report.get("production_admitted"),"cases":[{"probe":p["probe"],"passed":p["passed"]} for p in report.get("cases",[])],"socket":report.get("fingerprint",{}).get("socket"),"adapter_digest":report.get("fingerprint",{}).get("adapter_digest"),"evidence":report.get("evidence")}))
PY
```

Captured output:

```text
{"kind": "short", "envelope_keys": ["ok", "result"]}
{"kind": "short", "completed": true, "production_admitted": false, "cases": [{"probe": "hooks", "passed": true}, {"probe": "evidence", "passed": true}, {"probe": "dynamic", "passed": true}, {"probe": "delivery", "passed": true}, {"probe": "tty", "passed": true}, {"probe": "reconnect", "passed": true}, {"probe": "terminals", "passed": true}, {"probe": "policy", "passed": true}, {"probe": "validation", "passed": true}, {"probe": "exec_policy", "passed": true}], "socket": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/s9g_j2t1v/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/f6686ebab6b8e09fd5619d9645973cc10378905cedca1332c21a4f39205c053d", "resolved_bytes": 94, "uid": 501}, "adapter_digest": "98fcffd8c7d97b52c2f40f6bd3d705926a14722c179b7f9db9d54fe65edf2a72", "evidence": "/private/tmp/c1-rework-3gcqdxau/final-admission-short-short/admission_73e9039dfe8742db96f2947c0cbe23dc"}
{"kind": "long", "envelope_keys": ["ok", "result"]}
{"kind": "long", "completed": true, "production_admitted": false, "cases": [{"probe": "hooks", "passed": true}, {"probe": "evidence", "passed": true}, {"probe": "dynamic", "passed": true}, {"probe": "delivery", "passed": true}, {"probe": "tty", "passed": true}, {"probe": "reconnect", "passed": true}, {"probe": "terminals", "passed": true}, {"probe": "policy", "passed": true}, {"probe": "validation", "passed": true}, {"probe": "exec_policy", "passed": true}], "socket": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/sj47jzod9/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/a346e9af33882ef5d1bc83ed33183b51f01f28d7e9add21ab0e93b8a05804fea", "resolved_bytes": 94, "uid": 501}, "adapter_digest": "98fcffd8c7d97b52c2f40f6bd3d705926a14722c179b7f9db9d54fe65edf2a72", "evidence": "/private/tmp/c1-rework-3gcqdxau/final-admission-long-wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww/admission_c1edc1420f3a4dc4b4466d167bde1e19"}
```

</details>

<a id="c43"></a>

<details>
<summary>C43 — final-child; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time
from coordinator.client import Client
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"final-setup.json").read_text()); c=Client(setup["states"]["short"],timeout=1800)
run=c.call("run/start",{"checkout":setup["checkouts"]["short"],"mode":"native","task":"C1_CHILD_ROOT","fixture":str(root/"fixture-owned"),"model":"gpt-6-astra"})
deadline=time.monotonic()+45
while time.monotonic()<deadline:
 actors=[r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["unit"]==run["unit"]]
 operations=[r for r in c.call("records/list",{"kind":"operation","limit":1000})["data"] if r["unit"]==run["unit"]]
 custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==run["unit"]]
 if len(actors)>1 and any(o["kind"]=="command" and o["state"]=="reconciled" for o in operations) and all(not a.get("active_turn") for a in actors) and all(r["state"]=="released" for r in custody):break
 time.sleep(.2)
out={"run":run,"actors":actors,"operations":operations,"custody":custody,"unknown":c.call("unknown/list",{"checkout":setup["checkouts"]["short"]})}
(root/"final-child.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"actors":[{"kind":a["kind"],"thread":a.get("thread"),"state":a["state"]} for a in actors],"operations":[{"actor":o["actor"],"kind":o["kind"],"state":o["state"]} for o in operations],"custody":[{"state":r["state"],"reason":r.get("reason")} for r in custody],"unknown":out["unknown"]}))
PY
```

Captured output:

```text
{"actors": [{"kind": "root", "thread": "01a14f09-e17a-74b0-8392-a086fab1f0ea", "state": "completed"}, {"kind": "child", "thread": "01a14f0a-2e89-7841-9e75-4d20ab81ed64", "state": "completed"}], "operations": [{"actor": "actor_c5e919d539274109bc639fc99e8c418a", "kind": "command", "state": "reconciled"}, {"actor": "actor_c5e919d539274109bc639fc99e8c418a", "kind": "patch", "state": "verified"}], "custody": [{"state": "released", "reason": null}], "unknown": {"data": []}}
```

</details>

<a id="c44"></a>

<details>
<summary>C44 — final-class; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time
from coordinator.client import Client
root=Path('/private/tmp/c1-rework-3gcqdxau');env=json.loads((root/'environment.json').read_text());setup=json.loads((root/'final-setup.json').read_text())
c=Client(setup['states']['long'],timeout=1800)
run=c.call('run/start',{'checkout':setup['checkouts']['long'],'mode':'native','task':'C1_CLASSIFY','fixture':str(root/'fixture-more'),'model':'gpt-6-astra'})
deadline=time.monotonic()+180
while time.monotonic()<deadline:
    status=c.call('run/status',{'run':run['run']})
    if status['state'] in ('completed','failed','interrupted'):break
    time.sleep(.1)
operations=[o for o in c.call('records/list',{'kind':'operation','limit':10000})['data'] if o['unit']==run['unit']]
reservations=[r for r in c.call('records/list',{'kind':'custody','limit':10000})['data'] if r['unit']==run['unit']]
value={'run':run,'status':status,'operations':operations,'custody':reservations,'unknowns':c.call('unknown/list',{'checkout':setup['checkouts']['long']})}
(root/'final-classification.json').write_text(json.dumps(value,indent=2))
print(json.dumps({'status':status['state'],'operations':[{'command':o['intended']['tool_input']['command'],'state':o['state'],'refusal':o.get('refusal')} for o in operations],'custody':[{'command':r['command'],'level':r['level'],'state':r['state'],'reason':r.get('reason')} for r in reservations],'unknowns':value['unknowns']}))
PY
```

Captured output:

```text
{"status": "completed", "operations": [{"command": "$(printf git) push", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git commit", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "false || git restore a.txt", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "printf '%s' 'git push'", "state": "reconciled", "refusal": null}, {"command": "'git' 'commit'; true", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git reset | cat", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git --no-pager status --short", "state": "reconciled", "refusal": null}, {"command": "git -c alias.record=commit record", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git made-up-subcommand", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git tag\ntrue", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "true && git checkout a.txt", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git push; true", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "env FOO=value command git -C . commit", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git push 'unterminated", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "printf '%s' \"$(git push)\"", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "bash -lc 'git tag; true'", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "git --git-dir=.git status", "state": "denied", "refusal": "LEAD_RECORDS"}, {"command": "sh -c 'git push; true'", "state": "denied", "refusal": "LEAD_RECORDS"}], "custody": [{"command": "printf '%s' 'git push'", "level": "shared", "state": "released", "reason": null}, {"command": "git --no-pager status --short", "level": "shared", "state": "released", "reason": null}], "unknowns": {"data": []}}
```

</details>

<a id="c45"></a>

<details>
<summary>C45 — paid; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time,os,subprocess
from coordinator.client import Client,CoordinatorError
from coordinator import gitio,processes
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"final-setup.json").read_text()); repo=root/"paid-repo";repo.mkdir(mode=0o700);(repo/"README.txt").write_text("Disposable C1 real-model observation. Only paid_a.py and paid_b.py may be created.\n")
env={k:"C1 Observation" if k.endswith("NAME") else "c1@example.invalid" if k.endswith("EMAIL") else "2026-10-18T13:00:00+00:00" for k in ("GIT_AUTHOR_NAME","GIT_AUTHOR_EMAIL","GIT_AUTHOR_DATE","GIT_COMMITTER_NAME","GIT_COMMITTER_EMAIL","GIT_COMMITTER_DATE")}
gitio.git(repo,["init","-q"]); oid=gitio.text(repo,["hash-object","-w","--stdin"],data=(repo/"README.txt").read_bytes());gitio.git(repo,["read-tree","--empty"]);gitio.git(repo,["update-index","--index-info"],data=("100644 "+oid+"\tREADME.txt\n").encode());tree=gitio.text(repo,["write-tree"]);base=gitio.text(repo,["commit-tree",tree],data=b"C1 paid observation baseline\n",extra_env=env);gitio.git(repo,["update-ref","HEAD",base])
c=Client(setup["states"]["short"],timeout=1800); checkout=c.call("checkout/register",{"path":str(repo)})["id"];out={"started":time.time(),"checkout":checkout,"repository":str(repo),"runs":[],"errors":[]}
(root/"paid.json").write_text(json.dumps(out,indent=2))
for _ in range(2):
 try:
  r=c.call("run/start",{"checkout":checkout,"mode":"native","task":"","model":"gpt-6-astra"});out["runs"].append(r);print(json.dumps({"admitted_root":r}),flush=True)
 except CoordinatorError as exc:
  out["errors"].append(exc.envelope());print(json.dumps({"startup_refusal":exc.envelope()}),flush=True);break
 (root/"paid.json").write_text(json.dumps(out,indent=2))
if len(out["runs"])==2:
 a,b=out["runs"]
 common="This is the one authorized paid coordinator observation in this disposable scratch repository. Work only in the single Python file named below. Use the native apply_patch tool for the edit. Do not use shell commands, web, other MCP tools, Git, or child agents. The lead records. Use the coordinator's dynamic claim/send/inbox/ack/status tools as needed. Finish after the requested edit and communication; no tests. "
 tasks=[common+"Create paid_a.py containing a small add_one(value) function returning value + 1. Use the dynamic send tool to send 'paid_a.py is ready' to recipient "+b["actor"]+" with wake='queue'. Acknowledge any incoming C1_MESSAGE using ack. Report completion.",
 common+"Create paid_b.py containing a small double(value) function returning value * 2. Read inbox and acknowledge any incoming C1_MESSAGE using ack. Report completion."]
 with ThreadPoolExecutor(2) as pool:
  out["messages"]=list(pool.map(lambda pair:c.call("message/send",{"sender":"lead","recipient":pair[0]["actor"],"text":pair[1],"wake":"start"}),zip(out["runs"],tasks)))
 deadline=time.monotonic()+300;out["observations"]=[]
 while time.monotonic()<deadline:
  actors=[r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["checkout"]==checkout]
  custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["checkout"]==checkout]
  out["observations"].append({"time":time.time(),"actors":[{"id":a["id"],"state":a["state"],"active_turn":a.get("active_turn")} for a in actors],"custody":[{"id":x["id"],"state":x["state"]} for x in custody]})
  if time.monotonic()>deadline-285 and all(not a.get("active_turn") for a in actors):break
  time.sleep(.5)
out["finished"]=time.time()
for kind in ("actor","operation","message","delivery","listener","custody"):
 rows=c.call("records/list",{"kind":kind,"limit":1000})["data"]
 units={r["unit"] for r in out["runs"]}
 out[kind]=[r for r in rows if r.get("checkout")==checkout or r.get("unit") in units or kind=="delivery" and r.get("actor") in {x["actor"] for x in out["runs"]}]
 if kind=="listener":
  out[kind]=[{k:v for k,v in r.items() if k not in ("config","startup_diagnostic")} for r in out[kind]]
out["unknown"]=c.call("unknown/list",{"checkout":checkout});out["revisions"]=c.call("checkout/revisions",{"checkout":checkout,"limit":1000})
(root/"paid.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"run_count":len(out["runs"]),"errors":out["errors"],"actors":[{"id":a["id"],"state":a["state"],"policy":a.get("policy")} for a in out["actor"]],"operations":[{"kind":o["kind"],"state":o["state"],"paths":o["paths"]} for o in out["operation"]],"messages":[{"id":m["id"],"sender":m["sender"],"recipient":m["recipient"],"state":m["state"]} for m in out["message"]],"services":[{"listener":l["id"],"services":l.get("services")} for l in out["listener"]],"unknown":out["unknown"]}),flush=True)
PY
```

Captured output:

```text
{"admitted_root": {"actor": "actor_e9f0cc75288444369bc97f8441023130", "attempt": "attempt_b6543d3d50344b2394e842118ffa3032", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_5fd2e92ba56d473d9d6f410dc71afd86", "unit": "unit_4c0df3ef4af24684a5dad246c938012e"}}

{"admitted_root": {"actor": "actor_10c952e5c2e44582ac00223245c89680", "attempt": "attempt_8b9dbc6e9d584ff690cf30cad87c5943", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_1c455649ba9d46b482b07d5140390dae", "unit": "unit_5920dc9917b94c399827be9380521c31"}}
{"run_count": 2, "errors": [], "actors": [{"id": "actor_10c952e5c2e44582ac00223245c89680", "state": "completed", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}}, {"id": "actor_e9f0cc75288444369bc97f8441023130", "state": "completed", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}}], "operations": [{"kind": "patch", "state": "verified", "paths": ["paid_a.py"]}, {"kind": "patch", "state": "verified", "paths": ["paid_b.py"]}], "messages": [{"id": "msg_4dab0b2727a94a5a8879bad2547ee1a7", "sender": "lead", "recipient": "actor_e9f0cc75288444369bc97f8441023130", "state": "acknowledged"}, {"id": "msg_7ce8c84198934c7db566d1b3237e7f9a", "sender": "actor_e9f0cc75288444369bc97f8441023130", "recipient": "actor_10c952e5c2e44582ac00223245c89680", "state": "acknowledged"}, {"id": "msg_c1d4854028f541c09c70126c2572bf96", "sender": "lead", "recipient": "actor_10c952e5c2e44582ac00223245c89680", "state": "acknowledged"}], "services": [{"listener": "listener_852f279941ca45f28e3577cffb49e1be", "services": [{"birth": "1792327467:18938", "lineage": true, "marked": false, "markers": {}, "pid": 87589, "ppid": 87495, "uid": 501, "zombie": false}, {"birth": "1792327467:19119", "lineage": true, "marked": false, "markers": {}, "pid": 87601, "ppid": 87495, "uid": 501, "zombie": false}, {"birth": "1792327467:19124", "lineage": true, "marked": false, "markers": {}, "pid": 87603, "ppid": 87495, "uid": 501, "zombie": false}, {"birth": "1792327467:19126", "lineage": true, "marked": false, "markers": {}, "pid": 87604, "ppid": 87495, "uid": 501, "zombie": false}, {"birth": "1792327467:19158", "lineage": true, "marked": false, "markers": {}, "pid": 87605, "ppid": 87495, "uid": 501, "zombie": false}, {"birth": "1792327467:19164", "lineage": true, "marked": false, "markers": {}, "pid": 87606, "ppid": 87495, "uid": 501, "zombie": false}, {"birth": "1792327467:19284", "lineage": true, "marked": false, "markers": {}, "pid": 87610, "ppid": 87495, "uid": 501, "zombie": false}, {"birth": "1792327467:19396", "lineage": true, "marked": false, "markers": {}, "pid": 87614, "ppid": 87495, "uid": 501, "zombie": false}, {"birth": "1792327467:19702", "lineage": true, "marked": false, "markers": {}, "pid": 87615, "ppid": 87495, "uid": 501, "zombie": false}]}, {"listener": "listener_8e581004ee614fb0822dc5d7f24b64d5", "services": null}, {"listener": "listener_9790b2b9d779457b9885505e822d9453", "services": [{"birth": "1792327491:625326", "lineage": true, "marked": false, "markers": {}, "pid": 89517, "ppid": 89461, "uid": 501, "zombie": false}, {"birth": "1792327491:625399", "lineage": true, "marked": false, "markers": {}, "pid": 89519, "ppid": 89461, "uid": 501, "zombie": false}, {"birth": "1792327491:625672", "lineage": true, "marked": false, "markers": {}, "pid": 89526, "ppid": 89461, "uid": 501, "zombie": false}, {"birth": "1792327491:626208", "lineage": true, "marked": false, "markers": {}, "pid": 89542, "ppid": 89461, "uid": 501, "zombie": false}, {"birth": "1792327491:626238", "lineage": true, "marked": false, "markers": {}, "pid": 89544, "ppid": 89461, "uid": 501, "zombie": false}, {"birth": "1792327491:626289", "lineage": true, "marked": false, "markers": {}, "pid": 89547, "ppid": 89461, "uid": 501, "zombie": false}, {"birth": "1792327491:626320", "lineage": true, "marked": false, "markers": {}, "pid": 89549, "ppid": 89461, "uid": 501, "zombie": false}, {"birth": "1792327491:626588", "lineage": true, "marked": false, "markers": {}, "pid": 89552, "ppid": 89461, "uid": 501, "zombie": false}, {"birth": "1792327491:626769", "lineage": true, "marked": false, "markers": {}, "pid": 89554, "ppid": 89461, "uid": 501, "zombie": false}, {"birth": "1792327491:635727", "lineage": true, "marked": false, "markers": {}, "pid": 89555, "ppid": 89554, "uid": 501, "zombie": false}]}, {"listener": "listener_f66c9f598e9e415f9c7a7fb82bfadfcc", "services": null}], "unknown": {"data": []}}
```

</details>

<a id="c46"></a>

<details>
<summary>C46 — message-recovery; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 SOFTWARE_WORKFLOWS_PYTHON=/opt/homebrew/opt/python@3.15/bin/python3.15 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,os,signal
from coordinator.client import Client,CoordinatorError
from coordinator import processes
root=Path("/private/tmp/c1-rework-3gcqdxau"); setup=json.loads((root/"final-setup.json").read_text()); c=Client(setup["states"]["long"],timeout=1800); prior=json.loads((root/"final-parallel-and-survivors.json").read_text()); actor=prior["runs"][0]["actor"];run=prior["runs"][0]["run"]
out={"before":c.call("daemon/status",{}),"started":time.time()}
out["resume"]=c.call("run/resume",{"run":run,"text":"Please acknowledge the messages already in context and finish."})
time.sleep(1)
pending=c.call("message/send",{"sender":"lead","recipient":actor,"text":"Persist across worker restart; acknowledge this exact message.","wake":"none"})
time.sleep(1)
# Wait for idle before capturing the persisted message and killing the worker.
while next(a for a in c.call("records/list",{"kind":"actor","limit":1000})["data"] if a["id"]==actor).get("active_turn"):time.sleep(.1)
rows=c.call("records/list",{"kind":"message","limit":1000})["data"];out["before_restart_message"]=next(m for m in rows if m["id"]==pending["id"])
status=c.call("daemon/status",{}); identity=processes.identity(status["pid"])
if identity["birth"]!=status["birth"]:raise RuntimeError("worker birth changed")
out["killed"]={"identity":identity,"generation":status["generation"],"message_state":out["before_restart_message"]["state"],"time":time.time()}
os.kill(identity["pid"],signal.SIGKILL)
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 try:
  state=c.call("daemon/status",{})
  if state["ready"] and state["generation"]>status["generation"]:break
 except (CoordinatorError,OSError):pass
 time.sleep(.2)
out["after"]=state
out["wake"]=c.call("message/wake",{"message":pending["id"],"wake":"start"})
deadline=time.monotonic()+20
while time.monotonic()<deadline:
 rows=c.call("records/list",{"kind":"message","limit":1000})["data"];row=next(m for m in rows if m["id"]==pending["id"])
 if row["state"]=="acknowledged":break
 time.sleep(.1)
out["recovered_message"]=row;out["all_root_messages"]=[m for m in rows if m["recipient"]==actor]
lead=c.call("message/send",{"sender":"lead","recipient":"lead","text":"Observe acknowledgement deduplication","wake":"none"})
out["inbox_before"]=c.call("message/inbox",{"recipient":"lead"})
out["lead_ack_first"]=c.call("message/ack",{"message":lead["id"]});out["lead_ack_duplicate"]=c.call("message/ack",{"message":lead["id"]})
try:c.call("message/ack",{"message":pending["id"],"actor":actor})
except CoordinatorError as exc:out["forged_actor"]=exc.envelope()
out["deliveries"]=c.call("records/list",{"kind":"delivery","limit":1000})["data"];out["locks"]=c.call("daemon/status",{})["locks"];out["unknown"]=c.call("unknown/list",{"checkout":setup["checkouts"]["long"]})
(root/"final-message-recovery.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"killed":out["killed"],"generation_after":state["generation"],"recovered_message":{k:row[k] for k in ("id","state","attempts")},"root_message_states":[m["state"] for m in out["all_root_messages"]],"ack_first":out["lead_ack_first"],"ack_duplicate":out["lead_ack_duplicate"],"forged":out["forged_actor"],"locks":out["locks"],"unknown":out["unknown"]}))
PY
```

Captured output:

```text
{"killed": {"identity": {"pid": 62995, "birth": "1792327038:406302", "ppid": 56852, "uid": 501, "zombie": false}, "generation": 2, "message_state": "persisted", "time": 1792327533.232827}, "generation_after": 3, "recovered_message": {"id": "msg_13da00aa6ab94c69ab33e9c71b287c9b", "state": "acknowledged", "attempts": ["delivery_36a1e6057f9b47f6af6d9a8970dd3dd5"]}, "root_message_states": ["acknowledged", "acknowledged", "acknowledged"], "ack_first": {"duplicate": false, "message": "msg_551780580a47466399ec21db609c8b7f", "state": "acknowledged"}, "ack_duplicate": {"duplicate": true, "message": "msg_551780580a47466399ec21db609c8b7f", "state": "acknowledged"}, "forged": {"code": "NATIVE_BOUND", "message": "Writer acknowledgements must originate from native actor metadata", "details": {}, "kind": "refusal"}, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "unknown": {"data": []}}
```

</details>

<a id="c47"></a>

<details>
<summary>C47 — hard-refusals; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,hashlib
from coordinator.client import Client,CoordinatorError
from coordinator import gitio
root=Path("/private/tmp/c1-rework-3gcqdxau");env=json.loads((root/"environment.json").read_text()); prior=json.loads((root/"hard-refusals-setup.json").read_text());c=Client(env["short"],timeout=1800);binding=prior["core_diff"]["binding"];unit=binding["unit"];repo=root/"core-repo"
answer="Inspected the actual object diff: only a.txt changes from the previously published A text to 'unit B follows published A'. The fresh base is A's recorded commit, the scope is one exact receipt, and the change matches this observation."
review=c.call("unit/review-record",{"unit":unit,"evidence":dict(binding,verdict="pass",completed=True,review_identity="C1 live object review",answer=answer,answer_digest=hashlib.sha256(answer.encode()).hexdigest())})
meta={"message":"C1 negative controls","author_name":"C1 Fixture","author_email":"c1@example.invalid","author_date":"2026-10-18T13:00:00+00:00","committer_name":"C1 Fixture","committer_email":"c1@example.invalid","committer_date":"2026-10-18T13:00:00+00:00"}
tree=gitio.text(repo,["rev-parse",binding["base"]+"^{tree}"]);external=gitio.text(repo,["commit-tree",tree,"-p",binding["base"]],data=b"External parent advancement for negative control\n",extra_env={"GIT_"+k.upper():v for k,v in meta.items() if k!="message"});gitio.git(repo,["update-ref","HEAD",external,binding["base"]]);out={"review":review,"base":binding["base"],"external_parent":external}
try:out["stale_parent_unexpected_success"]=c.call("unit/publish",{"unit":unit,"review":review["review"],"metadata":meta})
except CoordinatorError as exc:out["stale_parent"]=exc.envelope()
seal=next(r for r in c.call("records/list",{"kind":"seal","limit":1000})["data"] if r["id"]==binding["seal"]);row=seal["rows"][0];blob=Path(env["short"])/"blobs"/row["blob"];saved=blob.read_bytes();blob.write_bytes(b"Damaged disposable receipt negative control\n")
try:
 try:out["altered_blob_unexpected_success"]=c.call("unit/publish",{"unit":unit,"review":review["review"],"metadata":meta})
 except CoordinatorError as exc:out["altered_blob"]=exc.envelope()
finally:blob.write_bytes(saved)
out["claims"]=[r for r in c.call("records/list",{"kind":"claim","limit":1000})["data"] if r["unit"]==unit]
(root/"hard-refusals.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"review": {"review": "review_c65cf726fc914be5a1410836be8a876c", "seal": "seal_1eaca0c4cb6f40fa929f94f10daadc9d"}, "base": "03b52828808b0bb5dbf29d91b4ab6fdccf713b2e", "external_parent": "3ac5c1667f619b2d20ff5c24c4efa97a8c5f5a33", "stale_parent": {"code": "STALE_BASE", "message": "Branch tip changed; reseal and review against its new base", "details": {}, "kind": "refusal"}, "altered_blob": {"code": "BLOB_CORRUPT", "message": "Receipt hash does not match", "details": {"blob": "8d4f75705ed78a841fe143dd995996205b4db08d927345de4083181cefd139a6"}, "kind": "failure"}, "claims": [{"_kind": "claim", "_revision": 1, "actor": "actor_846d1535dbf44986a68414989ba35aae", "checkout": "checkout_dcf42099445a4e3dacd7bb25cae8b24b", "id": "claim_aafa32bd1734469e80e90a69be9a9a8f", "key": "a.txt", "path": "a.txt", "state": "held", "unit": "unit_1c1945babca04dac9a3edcf520dcd4c2"}]}
```

</details>

<a id="c48"></a>

<details>
<summary>C48 — paid-service; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,subprocess
from coordinator.client import Client
from coordinator import processes
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"final-setup.json").read_text());paid=json.loads((root/"paid.json").read_text());c=Client(setup["states"]["short"],timeout=1800);run=paid["runs"][0]
listeners=[r for r in c.call("records/list",{"kind":"listener","limit":1000})["data"] if r.get("unit")==run["unit"] and r["state"]=="connected"];listener=listeners[0];before=[p for p in listener.get("services",[]) if processes.identity(p["pid"]) and processes.identity(p["pid"])["birth"]==p["birth"]]
command="/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import time; time.sleep(2); print(42)'"
message=c.call("message/send",{"sender":"lead","recipient":run["actor"],"wake":"start","text":"Final observation within this same paid scenario: declare this entire exact non-writing command for one shared invocation, then execute it once with exec_command, login=false, yield_time_ms=10000, and finish. Do not edit any files or use any other shell/MCP/Git command. Exact command: "+command})
observations=[];deadline=time.monotonic()+120
while time.monotonic()<deadline:
 actor=next(r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["id"]==run["actor"])
 custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==run["unit"]]
 observations.append({"time":time.time(),"active":actor.get("active_turn"),"custody":custody})
 if custody and not actor.get("active_turn") and all(r["state"]=="released" for r in custody):break
 time.sleep(.2)
after=[p for p in before if processes.identity(p["pid"]) and processes.identity(p["pid"])["birth"]==p["birth"]]
rss=[]
for l in c.call("records/list",{"kind":"listener","limit":1000})["data"]:
 if l.get("unit") in {r["unit"] for r in paid["runs"]} and l["state"]=="connected":
  result=subprocess.run(["ps","-o","rss=","-p",str(l["pid"])],capture_output=True,text=True);rss.append({"listener":l["id"],"pid":l["pid"],"birth":l["birth"],"rss_kib":result.stdout.strip(),"exit":result.returncode})
out={"message":message,"command":command,"services_before":before,"same_services_after":after,"observations":observations,"rss":rss,"unknown":c.call("unknown/list",{"checkout":paid["checkout"]})}
(root/"paid-service-lifetime.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"command":command,"services_before":len(before),"same_services_after":len(after),"custody":custody,"rss":rss,"unknown":out["unknown"]}))
PY
```

Captured output:

```text
{"command": "/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import time; time.sleep(2); print(42)'", "services_before": 7, "same_services_after": 7, "custody": [{"_kind": "custody", "_revision": 224, "actor": "actor_e9f0cc75288444369bc97f8441023130", "checkout": "checkout_c76aa50d546243f6a11d5b78b3104889", "command": "/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import time; time.sleep(2); print(42)'", "created": 1792327571.785009, "excludes": ["paid_b.py"], "generation": 2, "id": "custody_182b99b3fd804a2fb23b274ba428cf1a", "level": "shared", "observed_processes": [], "operation": "op_945a478605294f1198f52552aa3d330c", "process_error": {"pids": [91559]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["77818"], "unit": "unit_4c0df3ef4af24684a5dad246c938012e", "unmediated": true}], "rss": [{"listener": "listener_852f279941ca45f28e3577cffb49e1be", "pid": 87495, "birth": "1792327463:680535", "rss_kib": "284688", "exit": 0}, {"listener": "listener_9790b2b9d779457b9885505e822d9453", "pid": 89461, "birth": "1792327491:44355", "rss_kib": "301472", "exit": 0}], "unknown": {"data": [{"checkout": "checkout_c76aa50d546243f6a11d5b78b3104889", "context": "{\"code\":\"PROCESS_AMBIGUOUS\",\"custody\":\"custody_182b99b3fd804a2fb23b274ba428cf1a\"}", "created": 1792327571.974553, "edit": null, "id": "unknown_ab9bd265253d4d2fba10ea84e037edb9", "path": "*", "reason": "process inventory unverifiable", "restore": null, "revision": null}]}}
```

</details>

<a id="c49"></a>

<details>
<summary>C49 — commit-created-setup; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,base64
from coordinator.client import Client
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"interrupt-setup.json").read_text());c=Client(setup["state"],timeout=120);r=c.call("run/start",{"checkout":setup["checkout"],"mode":"lead","task":"Observe commit_created crash boundary"});edit=c.call("lead-edit",{"actor":r["actor"],"changes":{"file00003.txt":{"mode":"100644","bytes_base64":base64.b64encode(b"commit created before reference update\n").decode()}}});seal=c.call("unit/seal",{"unit":r["unit"],"validation":[]});diff=c.call("unit/diff",{"unit":r["unit"]});out={"run":r,"edit":edit,"seal":seal,"diff":diff};(root/"commit-created-setup.json").write_text(json.dumps(out,indent=2));print(json.dumps({"run":r,"diff":diff}))
PY
```

Captured output:

```text
{"run": {"actor": "actor_897e12abe15546489d51b6cf04be1552", "attempt": "attempt_464b173fb8244751a788a2204c246306", "policy": null, "run": "run_e178877deb4a476eabe9dca1131027e5", "unit": "unit_325ecf3d20714b059008a4c4b3c9e4fe"}, "diff": {"binding": {"base": "68bc0e100dfca7f33c8299ac86b8de42c55815d0", "candidate": "e8c1d0ad313f816075c71d4506efe9ea57073c62", "provenance": "8ef34472b17adef57185041dea839728eb26375175dd7ead713dad3acef8688f", "repository": "5ae0f713c12a507f76a8081d0ec96074640d894b9546788f8555eb109ef07340", "seal": "seal_2eaa78b98ead4f328a60f984fd2da5c3", "unit": "unit_325ecf3d20714b059008a4c4b3c9e4fe", "validation_digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}, "diff": "diff --git a/file00003.txt b/file00003.txt\nindex 74ccd65..88b6f05 100644\n--- a/file00003.txt\n+++ b/file00003.txt\n@@ -1 +1 @@\n-corpus baseline\n+commit created before reference update\n"}}
```

</details>

<a id="c50"></a>

<details>
<summary>C50 — python39-help; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m coordinator --help
```

Captured output:

```text
usage: __main__.py [-h] [--state STATE] {daemon,rpc,admission,fixture} ...

Operational CLI. Exit 0: completed; 2: refusal; 3: operational failure.

positional arguments:
  {daemon,rpc,admission,fixture}
    daemon              Start/discover/status/stop the independent daemon
    rpc                 Call a documented C2 RPC method with JSON params
    admission           Run native capability admission
    fixture             Local product capability fixture; no model/network
                        spend

optional arguments:
  -h, --help            show this help message and exit
  --state STATE         External private state directory
```

</details>

<a id="c51"></a>

<details>
<summary>C51 — python39-hook; observed exit 0</summary>

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B plugins/software-workflows/coordinator/hooks.py --check
```

Captured output:

```text
{"compatible": true, "protocol": "jsonl-v1"}
```

</details>

<a id="c52"></a>

<details>
<summary>C52 — python39-client; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B - <<'PY'
import sys,json
from coordinator.client import Client
c=Client("/private/tmp/c1-rework-3gcqdxau/final-state1")
s=c.call("daemon/status",{})
print(json.dumps({"python":sys.version,"ready":s["ready"],"endpoint":s["endpoint"],"locks":s["locks"]}))
PY
```

Captured output:

```text
{"python": "3.9.6 (default, May 22 2026, 11:13:45) \n[Clang 21.0.0 (clang-2100.1.1.101)]", "ready": true, "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/snr5xygrp/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/snr5xygrp/s", "resolved_bytes": 31, "uid": 501}, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}}
```

</details>

<a id="c53"></a>

<details>
<summary>C53 — commit-created; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time,sqlite3,signal,os,hashlib
from coordinator.client import Client,CoordinatorError
from coordinator import gitio,processes
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"interrupt-setup.json").read_text());case=json.loads((root/"commit-created-setup.json").read_text());c=Client(setup["state"],timeout=120);unit=case["run"]["unit"]
answer="Inspected the object diff: file00003.txt alone changes from corpus baseline to commit created before reference update. The exact receipt and frozen base/candidate are bound to this completed review."
review=c.call("unit/review-record",{"unit":unit,"evidence":dict(case["diff"]["binding"],verdict="pass",completed=True,review_identity="C1 actual object inspection",answer=answer,answer_digest=hashlib.sha256(answer.encode()).hexdigest())})
params={"unit":unit,"review":review["review"],"metadata":json.loads((root/"setup.json").read_text())["metadata"]}
db=sqlite3.connect("file:"+str(Path(setup["state"])/"ledger.sqlite")+"?mode=ro",uri=True,isolation_level=None)
def publication():
 row=db.execute("SELECT id,data FROM records WHERE kind='publication' AND parent=?",(unit,)).fetchone()
 return dict(json.loads(row[1]),id=row[0]) if row else None
before=c.call("daemon/status",{})
with ThreadPoolExecutor(1) as pool:
 future=pool.submit(c.call,"unit/publish",params,key="commit-created-interruption")
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  p=publication()
  if p and p["phase"]=="commit_created":
   if processes.identity(before["pid"])["birth"]!=before["birth"]:raise RuntimeError("worker birth changed")
   os.kill(before["pid"],signal.SIGSTOP);stopped=publication()
   if stopped["phase"]=="commit_created" and gitio.text(setup["repo"],["rev-parse","HEAD"])==case["diff"]["binding"]["base"]:break
   os.kill(before["pid"],signal.SIGCONT)
  if future.done():raise RuntimeError("checkpoint missed")
  time.sleep(.0005)
 else:raise RuntimeError("checkpoint timeout")
 checkpoint={"time":time.time(),"worker":{k:before[k] for k in ("pid","birth","generation")},"publication":stopped,"head":gitio.text(setup["repo"],["rev-parse","HEAD"])}
 (root/"commit-created-checkpoint.json").write_text(json.dumps(checkpoint,indent=2));os.kill(before["pid"],signal.SIGKILL)
 try:rpc=future.result()
 except Exception as exc:rpc={"error":type(exc).__name__,"code":getattr(exc,"code",None)}
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>before["generation"]:break
 except (CoordinatorError,OSError):pass
 time.sleep(.1)
else:raise RuntimeError("recovery timeout")
out={"checkpoint":checkpoint,"rpc":rpc,"after_generation":after["generation"],"publication":publication(),"head":gitio.text(setup["repo"],["rev-parse","HEAD"]),"commit_count":gitio.text(setup["repo"],["rev-list","--count","HEAD"])}
(root/"commit-created-recovery.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"checkpoint": {"time": 1792327641.2037141, "worker": {"pid": 53193, "birth": "1792326558:218234", "generation": 5}, "publication": {"base_index_entries": {"file00003.txt": [{"mode": "100644", "oid": "74ccd65945e21968a328f6fa8ec51ef9cc2bf0a3", "stage": "0"}]}, "commit": "2bf18ade66cc17e6664f3f8eb12dbb25cdbec40b", "config_digest": "3dab0b9b9edcc59256ce5632d7c85fcc832e5f334d2e6705256789a6bb9d3fc6", "created": 1792327641.111386, "metadata": {"author_date": "2026-10-18T12:30:00+00:00", "author_email": "c1@example.invalid", "author_name": "C1 Fixture", "committer_date": "2026-10-18T12:30:00+00:00", "committer_email": "c1@example.invalid", "committer_name": "C1 Fixture", "message": "C1 observed publication"}, "phase": "commit_created", "review": "review_a5cd4d1ba44e46bda44278ec07e97254", "seal": "seal_2eaa78b98ead4f328a60f984fd2da5c3", "unit": "unit_325ecf3d20714b059008a4c4b3c9e4fe", "id": "publication_17e1312157134835a7a858cd76b12b87"}, "head": "68bc0e100dfca7f33c8299ac86b8de42c55815d0"}, "rpc": {"error": "CoordinatorError", "code": "RPC_UNCERTAIN"}, "after_generation": 6, "publication": {"base_index_entries": {"file00003.txt": [{"mode": "100644", "oid": "74ccd65945e21968a328f6fa8ec51ef9cc2bf0a3", "stage": "0"}]}, "commit": "2bf18ade66cc17e6664f3f8eb12dbb25cdbec40b", "config_digest": "3dab0b9b9edcc59256ce5632d7c85fcc832e5f334d2e6705256789a6bb9d3fc6", "created": 1792327641.111386, "index_lock": {"anchor": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/c1-index_owner_b92b9866b39942bb8062ae3a870299b7", "checkout_identity": "a4247cb6aded55d4131105fd2aa4ff7e6091cc8b4cf74ad8a5cbce2836428996", "device": 16777230, "generation": 6, "index": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index", "inode": 748630402, "lock": "/private/tmp/c1-rework-3gcqdxau/interrupt-repo/.git/index.lock", "owner": {"birth": "1792327641:466484", "pid": 93573, "ppid": 30832, "uid": 501, "zombie": false}, "publication": "publication_17e1312157134835a7a858cd76b12b87", "replacement_digest": "42266b020393ed01dfeea87ac666b0e1188b7845e4bc580a62c499c8c271e968", "state": "checkpointed", "token": "index_owner_b92b9866b39942bb8062ae3a870299b7"}, "metadata": {"author_date": "2026-10-18T12:30:00+00:00", "author_email": "c1@example.invalid", "author_name": "C1 Fixture", "committer_date": "2026-10-18T12:30:00+00:00", "committer_email": "c1@example.invalid", "committer_name": "C1 Fixture", "message": "C1 observed publication"}, "phase": "complete", "review": "review_a5cd4d1ba44e46bda44278ec07e97254", "seal": "seal_2eaa78b98ead4f328a60f984fd2da5c3", "unit": "unit_325ecf3d20714b059008a4c4b3c9e4fe", "id": "publication_17e1312157134835a7a858cd76b12b87"}, "head": "2bf18ade66cc17e6664f3f8eb12dbb25cdbec40b", "commit_count": "5"}
```

</details>

<a id="c54"></a>

<details>
<summary>C54 — service-restart; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,signal,os
from coordinator.client import Client,CoordinatorError
from coordinator import processes,native
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"final-setup.json").read_text());c=Client(setup["states"]["short"],timeout=1800);before=c.call("daemon/status",{})
if processes.identity(before["pid"])["birth"]!=before["birth"]:raise RuntimeError("worker identity changed")
os.kill(before["pid"],signal.SIGKILL);deadline=time.monotonic()+240
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>before["generation"]:break
 except (CoordinatorError,OSError):pass
 time.sleep(.2)
else:raise RuntimeError("recovery timeout")
out={"before":{k:before[k] for k in ("pid","birth","generation")},"after":{k:after[k] for k in ("pid","birth","generation","ready","endpoint")},"adapter_digest":native.adapter_digest(),"time":time.time()}
(root/"final-service-restart.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"before": {"pid": 62983, "birth": "1792327037:977521", "generation": 2}, "after": {"pid": 95288, "birth": "1792327763:470197", "generation": 3, "ready": true, "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/ssjfpi6kr/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/ssjfpi6kr/s", "resolved_bytes": 31, "uid": 501}}, "adapter_digest": "f1c904966f4ee481f2019551d84d4bf68735b37ae3dfdc72e606bcbc5ce27472", "time": 1792327843.7425978}
```

</details>

<a id="c55"></a>

<details>
<summary>C55 — long-service-restart; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,signal,os
from coordinator.client import Client,CoordinatorError
from coordinator import processes,native
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"final-setup.json").read_text());c=Client(setup["states"]["long"],timeout=1800);before=c.call("daemon/status",{})
if processes.identity(before["pid"])["birth"]!=before["birth"]:raise RuntimeError("worker identity changed")
os.kill(before["pid"],signal.SIGKILL);deadline=time.monotonic()+240
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>before["generation"]:break
 except (CoordinatorError,OSError):pass
 time.sleep(.2)
else:raise RuntimeError("recovery timeout")
out={"before":{k:before[k] for k in ("pid","birth","generation")},"after":{k:after[k] for k in ("pid","birth","generation","ready","endpoint")},"adapter_digest":native.adapter_digest(),"time":time.time()}
(root/"final-long-service-restart.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"before": {"pid": 91818, "birth": "1792327533:494578", "generation": 3}, "after": {"pid": 99458, "birth": "1792327814:180431", "generation": 4, "ready": true, "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/s6l2ln_tt/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/s6l2ln_tt/s", "resolved_bytes": 31, "uid": 501}}, "adapter_digest": "f1c904966f4ee481f2019551d84d4bf68735b37ae3dfdc72e606bcbc5ce27472", "time": 1792327913.8487942}
```

</details>

<a id="c56"></a>

<details>
<summary>C56 — paid-service-final; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,subprocess
from coordinator.client import Client
from coordinator import processes
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"final-setup.json").read_text());paid=json.loads((root/"paid.json").read_text());c=Client(setup["states"]["short"],timeout=1800);run=paid["runs"][1]
listeners=[r for r in c.call("records/list",{"kind":"listener","limit":1000})["data"] if r.get("unit")==run["unit"] and r["state"]=="connected"];listener=listeners[0];before=[p for p in listener.get("services",[]) if processes.identity(p["pid"]) and processes.identity(p["pid"])["birth"]==p["birth"]]
command="/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import time; time.sleep(2); print(42)'"
message=c.call("message/send",{"sender":"lead","recipient":run["actor"],"wake":"start","text":"Final observation within this same paid scenario: declare this entire exact non-writing command for one shared invocation, then execute it once with exec_command, login=false, yield_time_ms=10000, and finish. Do not edit any files or use any other shell/MCP/Git command. Exact command: "+command})
observations=[];deadline=time.monotonic()+120
while time.monotonic()<deadline:
 actor=next(r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["id"]==run["actor"])
 custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==run["unit"]]
 observations.append({"time":time.time(),"active":actor.get("active_turn"),"custody":custody})
 if custody and not actor.get("active_turn") and all(r["state"]=="released" for r in custody):break
 time.sleep(.2)
after=[p for p in before if processes.identity(p["pid"]) and processes.identity(p["pid"])["birth"]==p["birth"]]
rss=[]
for l in c.call("records/list",{"kind":"listener","limit":1000})["data"]:
 if l.get("unit") in {r["unit"] for r in paid["runs"]} and l["state"]=="connected":
  result=subprocess.run(["ps","-o","rss=","-p",str(l["pid"])],capture_output=True,text=True);rss.append({"listener":l["id"],"pid":l["pid"],"birth":l["birth"],"rss_kib":result.stdout.strip(),"exit":result.returncode})
out={"message":message,"command":command,"services_before":before,"service_boundary_after":next(r for r in c.call("records/list",{"kind":"listener","limit":1000})["data"] if r["id"]==listener["id"]).get("service_boundary"),"same_services_after":after,"observations":observations,"rss":rss,"unknown":c.call("unknown/list",{"checkout":paid["checkout"]})}
(root/"paid-service-final.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"command":command,"services_before":len(before),"same_services_after":len(after),"custody":custody,"rss":rss,"unknown":out["unknown"]}))
PY
```

Captured output:

```text
{"command": "/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import time; time.sleep(2); print(42)'", "services_before": 7, "same_services_after": 7, "custody": [{"_kind": "custody", "_revision": 48, "actor": "actor_10c952e5c2e44582ac00223245c89680", "checkout": "checkout_c76aa50d546243f6a11d5b78b3104889", "command": "/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import time; time.sleep(2); print(42)'", "created": 1792327965.593948, "excludes": ["paid_a.py"], "generation": 3, "id": "custody_70a904d4d40845a1b42a33436f7f8b2e", "level": "shared", "observed_processes": [{"birth": "1792327965:602970", "lineage": true, "marked": true, "markers": {"CODEX_SESSION_ID": "01a14f0b-1fb5-7d22-8c96-37b10a5cd06c", "CODEX_THREAD_ID": "01a14f0b-1fb5-7d22-8c96-37b10a5cd06c", "CODEX_TOOL_CALL_ID": "exec-a6122b3c-14c7-49e5-9a01-193f491aa89c"}, "pid": 12265, "ppid": 89461, "uid": 501, "zombie": false}], "operation": "op_5394a9625146461fa977d630686587fe", "process_error": {"pids": [12295]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "quarantined", "terminals": ["42374"], "unit": "unit_5920dc9917b94c399827be9380521c31", "unmediated": true}], "rss": [{"listener": "listener_852f279941ca45f28e3577cffb49e1be", "pid": 87495, "birth": "1792327463:680535", "rss_kib": "288048", "exit": 0}, {"listener": "listener_9790b2b9d779457b9885505e822d9453", "pid": 89461, "birth": "1792327491:44355", "rss_kib": "303888", "exit": 0}], "unknown": {"data": [{"checkout": "checkout_c76aa50d546243f6a11d5b78b3104889", "context": "{\"code\":\"PROCESS_AMBIGUOUS\",\"custody\":\"custody_182b99b3fd804a2fb23b274ba428cf1a\"}", "created": 1792327571.974553, "edit": null, "id": "unknown_ab9bd265253d4d2fba10ea84e037edb9", "path": "*", "reason": "process inventory unverifiable", "restore": null, "revision": null}, {"checkout": "checkout_c76aa50d546243f6a11d5b78b3104889", "context": "{\"code\":\"PROCESS_AMBIGUOUS\",\"custody\":\"custody_70a904d4d40845a1b42a33436f7f8b2e\"}", "created": 1792327967.698545, "edit": null, "id": "unknown_c34709e135a34064b1390dd4f87033aa", "path": "*", "reason": "process inventory unverifiable", "restore": null, "revision": null}]}}
```

</details>

<a id="c57"></a>

<details>
<summary>C57 — release-admission-short; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,subprocess,sys,time
root=Path("/private/tmp/c1-rework-3gcqdxau");scratch=root/("release-admission-short-"+"short")
args=[sys.executable,"-B","-m","coordinator","--state","/private/tmp/c1-rework-3gcqdxau/final-state1","admission","--scratch",str(scratch)]
for probe in ("hooks","evidence","dynamic","delivery","tty","reconnect","terminals","policy","validation","exec_policy"):args+=["--probe",probe]
started=time.time();result=subprocess.run(args,capture_output=True,text=True)
(root/"release-admission-short.json").write_text(result.stdout)
report=json.loads(result.stdout).get("result",{})
print(json.dumps({"argv":args,"started":started,"finished":time.time(),"exit_code":result.returncode,"completed":report.get("completed"),"production_admitted":report.get("production_admitted"),"cases":[{"probe":p["probe"],"passed":p.get("passed")} for p in report.get("cases",[])],"socket":report.get("fingerprint",{}).get("endpoint"),"adapter_digest":report.get("fingerprint",{}).get("adapter_digest")}))
sys.exit(result.returncode)
PY
```

Captured output:

```text
{"argv": ["/opt/homebrew/opt/python@3.15/bin/python3.15", "-B", "-m", "coordinator", "--state", "/private/tmp/c1-rework-3gcqdxau/final-state1", "admission", "--scratch", "/private/tmp/c1-rework-3gcqdxau/release-admission-short-short", "--probe", "hooks", "--probe", "evidence", "--probe", "dynamic", "--probe", "delivery", "--probe", "tty", "--probe", "reconnect", "--probe", "terminals", "--probe", "policy", "--probe", "validation", "--probe", "exec_policy"], "started": 1792327867.305611, "finished": 1792327887.411807, "exit_code": 0, "completed": true, "production_admitted": false, "cases": [{"probe": "hooks", "passed": true}, {"probe": "evidence", "passed": true}, {"probe": "dynamic", "passed": true}, {"probe": "delivery", "passed": true}, {"probe": "tty", "passed": true}, {"probe": "reconnect", "passed": true}, {"probe": "terminals", "passed": true}, {"probe": "policy", "passed": true}, {"probe": "validation", "passed": true}, {"probe": "exec_policy", "passed": true}], "socket": null, "adapter_digest": "f1c904966f4ee481f2019551d84d4bf68735b37ae3dfdc72e606bcbc5ce27472"}
```

</details>

<a id="c58"></a>

<details>
<summary>C58 — startup-refusal; observed exit 3</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 SOFTWARE_WORKFLOWS_PYTHON=/opt/homebrew/opt/python@3.15/bin/python3.15 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,subprocess,sys,time
root=Path("/private/tmp/c1-rework-3gcqdxau");state=root/"unsafe-start-state";state.mkdir(mode=0o700);(state/"listeners").mkdir(mode=0o755);(state/"listeners").chmod(0o755)
args=[sys.executable,"-B","-m","coordinator","--state",str(state),"daemon","start"];started=time.time();r=subprocess.run(args,capture_output=True,text=True)
out={"argv":args,"started":started,"finished":time.time(),"exit_code":r.returncode,"stdout":json.loads(r.stdout),"diagnostic":json.loads((state/"startup-error.json").read_text())};(root/"startup-refusal.json").write_text(json.dumps(out,indent=2));print(json.dumps(out));sys.exit(r.returncode)
PY
```

Captured output:

```text
{"argv": ["/opt/homebrew/opt/python@3.15/bin/python3.15", "-B", "-m", "coordinator", "--state", "/private/tmp/c1-rework-3gcqdxau/unsafe-start-state", "daemon", "start"], "started": 1792327880.898196, "finished": 1792327881.12395, "exit_code": 3, "stdout": {"ok": false, "error": {"code": "STATE_PERMISSIONS", "message": "Unsafe state subdirectory", "details": {"name": "listeners"}, "kind": "failure"}}, "diagnostic": {"error": {"code": "STATE_PERMISSIONS", "details": {"name": "listeners"}, "kind": "refusal", "message": "Unsafe state subdirectory"}, "time": 1792327881.0848682}}
```

</details>

<a id="c59"></a>

<details>
<summary>C59 — paid-reconcile; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,os,signal,time
from coordinator import processes
from coordinator.client import Client
root=Path("/private/tmp/c1-rework-3gcqdxau");c=Client(root/"final-state1");known={"pid":91559,"birth":"1792327517:491281"};listener={"pid":87495,"birth":"1792327463:680535"}
current=processes.identity(known["pid"])
if not current or current["birth"]!=known["birth"] or not processes.descendant(known,listener):raise RuntimeError("owned code host identity changed")
# Only the native infrastructure process created by this paid scratch listener.
os.kill(known["pid"],signal.SIGTERM)
deadline=time.monotonic()+10
while processes.identity(known["pid"]) and time.monotonic()<deadline:time.sleep(.05)
out={"time":time.time(),"terminated":known,"parent":listener,"gone":processes.identity(known["pid"]) is None,"reconcile":c.call("custody/reconcile",{"custody":"custody_182b99b3fd804a2fb23b274ba428cf1a"})}
(root/"paid-ambiguous-reconciliation.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"time": 1792327965.535889, "terminated": {"pid": 91559, "birth": "1792327517:491281"}, "parent": {"pid": 87495, "birth": "1792327463:680535"}, "gone": true, "reconcile": {"_kind": "custody", "_revision": 601, "actor": "actor_e9f0cc75288444369bc97f8441023130", "checkout": "checkout_c76aa50d546243f6a11d5b78b3104889", "command": "/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import time; time.sleep(2); print(42)'", "completed": 1792327965.59342, "created": 1792327571.785009, "excludes": ["paid_b.py"], "generation": 2, "id": "custody_182b99b3fd804a2fb23b274ba428cf1a", "level": "shared", "observed_processes": [], "operation": "op_945a478605294f1198f52552aa3d330c", "process_error": {"pids": [91559]}, "processes": [], "reason": "PROCESS_AMBIGUOUS", "state": "released", "terminals": ["77818"], "unit": "unit_4c0df3ef4af24684a5dad246c938012e", "unmediated": true}}
```

</details>

<a id="c60"></a>

<details>
<summary>C60 — release-setup; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 SOFTWARE_WORKFLOWS_PYTHON=/opt/homebrew/opt/python@3.15/bin/python3.15 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,base64
from coordinator.client import Client
from coordinator import gitio,native
root=Path("/private/tmp/c1-rework-3gcqdxau");states={"short":root/"release-one1","long":root/("release-long-"+"r"*110)/"state"};out={"states":{k:str(v) for k,v in states.items()},"repos":{},"checkouts":{},"status":{},"digest":native.adapter_digest(),"started":time.time()}
env={"GIT_AUTHOR_NAME":"C1 Fixture","GIT_AUTHOR_EMAIL":"c1@example.invalid","GIT_AUTHOR_DATE":"2026-10-18T13:20:00+00:00","GIT_COMMITTER_NAME":"C1 Fixture","GIT_COMMITTER_EMAIL":"c1@example.invalid","GIT_COMMITTER_DATE":"2026-10-18T13:20:00+00:00"}
for key,state in states.items():
 repo=root/("release-"+key+"-repo");repo.mkdir(mode=0o700);(repo/"base.txt").write_bytes(b"release baseline\n");gitio.git(repo,["init","-q"]);oid=gitio.text(repo,["hash-object","-w","--stdin"],data=b"release baseline\n");gitio.git(repo,["read-tree","--empty"]);gitio.git(repo,["update-index","--index-info"],data=("100644 "+oid+"\tbase.txt\n").encode());tree=gitio.text(repo,["write-tree"]);commit=gitio.text(repo,["commit-tree",tree],data=b"release baseline\n",extra_env=env);gitio.git(repo,["update-ref","HEAD",commit])
 c=Client(state,timeout=1800);c.ensure();out["checkouts"][key]=c.call("checkout/register",{"path":str(repo)})["id"];out["repos"][key]=str(repo);out["status"][key]=c.call("daemon/status",{})
(root/"release-setup.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"states": {"short": "/private/tmp/c1-rework-3gcqdxau/release-one1", "long": "/private/tmp/c1-rework-3gcqdxau/release-long-rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr/state"}, "repos": {"short": "/private/tmp/c1-rework-3gcqdxau/release-short-repo", "long": "/private/tmp/c1-rework-3gcqdxau/release-long-repo"}, "checkouts": {"short": "checkout_f7ddbed12b8643439ef0590c9c77f4a3", "long": "checkout_7c8400ce95664a72b377f903adf4c838"}, "status": {"short": {"birth": "1792327986:437256", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/si6q9l9jm/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/si6q9l9jm/s", "resolved_bytes": 31, "uid": 501}, "generation": 1, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "pid": 12600, "ready": true, "sqlite": "3.54.0", "state_root": "/private/tmp/c1-rework-3gcqdxau/release-one1", "writers": []}, "long": {"birth": "1792327987:582476", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/syfvizfre/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/syfvizfre/s", "resolved_bytes": 31, "uid": 501}, "generation": 1, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "pid": 12642, "ready": true, "sqlite": "3.54.0", "state_root": "/private/tmp/c1-rework-3gcqdxau/release-long-rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr/state", "writers": []}}, "digest": "e658e561eec740f549274730975e472c10c3f358d2af9a59fe3ea4c9eba7c461", "started": 1792327986.3218188}
```

</details>

<a id="c61"></a>

<details>
<summary>C61 — release-restart; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,os,signal,time
from coordinator.client import Client,CoordinatorError
from coordinator import processes,native
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"release-setup.json").read_text());out={"digest":native.adapter_digest(),"states":{}}
for key,state in setup["states"].items():
 c=Client(state);before=c.call("daemon/status",{});p=processes.identity(before["pid"])
 if p["birth"]!=before["birth"]:raise RuntimeError("birth changed")
 os.kill(p["pid"],signal.SIGKILL)
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  try:
   after=c.call("daemon/status",{})
   if after["ready"] and after["generation"]>before["generation"]:break
  except (CoordinatorError,OSError):pass
  time.sleep(.1)
 out["states"][key]={"before":{k:before[k] for k in ("pid","birth","generation")},"after":after}
(root/"release-restart.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"digest": "671e505d610894921d62236357b007501c0550561200a8e52e0ba8b620c4ff03", "states": {"short": {"before": {"pid": 12600, "birth": "1792327986:437256", "generation": 1}, "after": {"birth": "1792328105:655801", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/sxc4p_sjy/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/sxc4p_sjy/s", "resolved_bytes": 31, "uid": 501}, "generation": 2, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "pid": 14368, "ready": true, "sqlite": "3.54.0", "state_root": "/private/tmp/c1-rework-3gcqdxau/release-one1", "writers": []}}, "long": {"before": {"pid": 12642, "birth": "1792327987:582476", "generation": 1}, "after": {"birth": "1792328106:85591", "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/s2ma36v0h/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/s2ma36v0h/s", "resolved_bytes": 31, "uid": 501}, "generation": 2, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}, "pid": 14380, "ready": true, "sqlite": "3.54.0", "state_root": "/private/tmp/c1-rework-3gcqdxau/release-long-rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr/state", "writers": []}}}}
```

</details>

<a id="c62"></a>

<details>
<summary>C62 — acceptance-short; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,subprocess,sys,time
root=Path("/private/tmp/c1-rework-3gcqdxau");scratch=root/("acceptance-short-"+"short")
args=[sys.executable,"-B","-m","coordinator","--state","/private/tmp/c1-rework-3gcqdxau/release-one1","admission","--scratch",str(scratch)]
for probe in ("hooks","evidence","dynamic","delivery","tty","reconnect","terminals","policy","validation","exec_policy"):args+=["--probe",probe]
started=time.time();result=subprocess.run(args,capture_output=True,text=True);(root/"acceptance-short.json").write_text(result.stdout);report=json.loads(result.stdout).get("result",{})
print(json.dumps({"argv":args,"started":started,"finished":time.time(),"exit_code":result.returncode,"completed":report.get("completed"),"production_admitted":report.get("production_admitted"),"cases":[{"probe":p["probe"],"passed":p["passed"]} for p in report.get("cases",[])],"fingerprint":report.get("fingerprint",{})}));sys.exit(result.returncode)
PY
```

Captured output:

```text
Warning: truncated output (original token count: 9010)
Total output lines: 1

{"argv": ["/opt/homebrew/opt/python@3.15/bin/python3.15", "-B", "-m", "coordinator", "--state", "/private/tmp/c1-rework-3gcqdxau/release-one1", "admission", "--scratch", "/private/tmp/c1-rework-3gcqdxau/acceptance-short-short", "--probe", "hooks", "--probe", "evidence", "--probe", "dynamic", "--probe", "delivery", "--probe", "tty", "--probe", "reconnect", "--probe", "terminals", "--probe", "policy", "--probe", "validation", "--probe", "exec_policy"], "started": 1792328119.989037, "finished": 1792328139.4784188, "exit_code": 0, "completed": true, "production_admitted": false, "cases": [{"probe": "hooks", "passed": true}, {"probe": "evidence", "passed": true}, {"probe": "dynamic", "passed": true}, {"probe": "delivery", "passed": true}, {"probe": "tty", "passed": true}, {"probe": "reconnect", "passed": true}, {"probe": "terminals", "passed": true}, {"probe": "policy", "passed": true}, {"probe": "validation", "passed": true}, {"probe": "exec_policy", "passed": true}], "fingerprint": {"adapter_digest": "671e505d610894921d62236357b007501c0550561200a8e52e0ba8b620c4ff03", "binary": "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex", "binary_digest": "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31", "case_sensitive": false, "config_digest": "5d18a7e8a3e82f13bc0212de3e4b5640d2da45d7010c10277941dfaf224296c7", "exec_policy": {"basis": "0.163.0 requirements null proves requirements_toml.is_empty including rules; trace is the resulting complete policy", "complete": true, "managed_requirements_absent": true, "trace_digests": ["d28fa40803b826f69cb35102f8d56fa91c9ef6ac380bdc2560deacecedf44a47", "d28fa40803b826f69cb35102f8d56fa91c9ef6ac380bdc2560deacecedf44a47"]}, "features": [{"announcement": "", "defaultEnabled": false, "description": "Preview five-hour and weekly allowance history for consumer accounts in /analytics.", "displayName": "Analytics plan history", "enabled": false, "name": "analytics_plan_history", "stage": "beta"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "daemon_auto_start", "stage": "stable"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "transcript_v2", "stage": "deprecated"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "undo", "stage": "removed"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "shell_tool", "stage": "stable"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "view_image", "stage": "stable"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "sleep_tool", "stage": "stable"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "secret_auth_storage", "stage": "stable"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "unified_exec", "stage": "stable"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "unified_exec_tty", "stage": "stable"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "shell_zsh_fork", "stage": "removed"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "unified_exec_zsh_fork", "stage": "removed"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": false, "name": "shell_snapshot", "stage": "stable"}, {"announcement": "", "defaultEnabled": false, "description": "Keep bundled tools such as ripgrep available when login shell sta…7010 tokens truncated…tConfig/import/readHistories", "externalAgentConfig/import/recordHistory", "feedback/upload", "fs/copy", "fs/createDirectory", "fs/getMetadata", "fs/readDirectory", "fs/readFile", "fs/remove", "fs/unwatch", "fs/watch", "fs/writeFile", "fuzzyFileSearch", "fuzzyFileSearch/sessionStart", "fuzzyFileSearch/sessionStop", "fuzzyFileSearch/sessionUpdate", "getAuthStatus", "getConversationSummary", "gitDiffToRemote", "hooks/list", "initialize", "marketplace/add", "marketplace/remove", "marketplace/upgrade", "mcpServer/event/stream/start", "mcpServer/event/stream/stop", "mcpServer/oauth/login", "mcpServer/resource/read", "mcpServer/tool/call", "mcpServerStatus/list", "memory/reset", "memory/status", "mock/experimentalMethod", "model/list", "modelProvider/capabilities/read", "permissionProfile/list", "plugin/install", "plugin/installed", "plugin/list", "plugin/read", "plugin/reconcile", "plugin/search", "plugin/share/checkout", "plugin/share/delete", "plugin/share/list", "plugin/share/save", "plugin/share/updateTargets", "plugin/skill/read", "plugin/uninstall", "process/kill", "process/resizePty", "process/spawn", "process/writeStdin", "project/create", "project/delete", "project/import", "project/list", "project/move", "project/read", "project/update", "remoteControl/client/list", "remoteControl/client/revoke", "remoteControl/disable", "remoteControl/enable", "remoteControl/pairing/start", "remoteControl/pairing/status", "remoteControl/status/read", "review/start", "rollout/compress", "server/diagnostics", "skills/config/write", "skills/extraRoots/set", "skills/list", "thread/approveGuardianDeniedAction", "thread/archive", "thread/attachment/add", "thread/attachment/list", "thread/attachment/remove", "thread/attachmentOwner/list", "thread/backgroundTerminals/clean", "thread/backgroundTerminals/list", "thread/backgroundTerminals/terminate", "thread/compact/start", "thread/decrement_elicitation", "thread/delete", "thread/fork", "thread/goal/clear", "thread/goal/get", "thread/goal/set", "thread/increment_elicitation", "thread/inject_items", "thread/items/list", "thread/items/read", "thread/list", "thread/loaded/list", "thread/memoryMode/set", "thread/metadata/update", "thread/name/set", "thread/prediction/request", "thread/queue/add", "thread/queue/delete", "thread/queue/list", "thread/queue/reorder", "thread/queue/start", "thread/queue/update", "thread/read", "thread/readState/update", "thread/realtime/appendAudio", "thread/realtime/appendSpeech", "thread/realtime/appendText", "thread/realtime/listVoices", "thread/realtime/start", "thread/realtime/stop", "thread/resume", "thread/revert", "thread/search", "thread/searchOccurrences", "thread/section/move", "thread/settings/update", "thread/shellCommand", "thread/start", "thread/timeline/list", "thread/turns/list", "thread/unarchive", "thread/unsubscribe", "threadSection/create", "threadSection/delete", "threadSection/list", "threadSection/update", "turn/interrupt", "turn/settings/update", "turn/start", "turn/steer", "userVerification/cancel", "userVerification/delete", "userVerification/enroll", "userVerification/status", "userVerification/verify", "windowsSandbox/readiness", "windowsSandbox/setupStart"], "model": "gpt-6-astra", "os": "macOS-26.6.2-arm64-arm-64bit-Mach-O", "provider": "c1_fixture", "python": "3.15.0", "requirements_digest": "d97d1a5acbd72c5a59997d3d389048a97aa10f729a4455a5efd4e4cde21a7834", "runtime_modes": {"agents_enabled": true, "web_search": "live"}, "schema_digest": "ab12ab78818d775c3720d0d1b0b4aba91d95e32c8ef47c82679661f686793bfe", "socket": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/stb2est4e/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/4d6541cb9a478f080301d59dec589fe0dae443755df0225d6ed4e35fd25caec7", "resolved_bytes": 94, "uid": 501}, "sqlite": "3.54.0", "user_agent": "software-workflows-coordinator/0.163.0 (Mac OS 26.6.2; arm64) dumb (software-workflows-coordinator; 2.0.0)", "version": "codex-cli 0.163.0"}}
```

</details>

<a id="c63"></a>

<details>
<summary>C63 — acceptance-long; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,subprocess,sys,time
root=Path("/private/tmp/c1-rework-3gcqdxau");scratch=root/("acceptance-long-"+"w"*110)
args=[sys.executable,"-B","-m","coordinator","--state","/private/tmp/c1-rework-3gcqdxau/release-long-rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr/state","admission","--scratch",str(scratch)]
for probe in ("hooks","evidence","dynamic","delivery","tty","reconnect","terminals","policy","validation","exec_policy"):args+=["--probe",probe]
started=time.time();result=subprocess.run(args,capture_output=True,text=True);(root/"acceptance-long.json").write_text(result.stdout);report=json.loads(result.stdout).get("result",{})
print(json.dumps({"argv":args,"started":started,"finished":time.time(),"exit_code":result.returncode,"completed":report.get("completed"),"production_admitted":report.get("production_admitted"),"cases":[{"probe":p["probe"],"passed":p["passed"]} for p in report.get("cases",[])],"fingerprint":report.get("fingerprint",{})}));sys.exit(result.returncode)
PY
```

Captured output:

```text
Warning: truncated output (original token count: 9065)
Total output lines: 1

{"argv": ["/opt/homebrew/opt/python@3.15/bin/python3.15", "-B", "-m", "coordinator", "--state", "/private/tmp/c1-rework-3gcqdxau/release-long-rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr/state", "admission", "--scratch", "/private/tmp/c1-rework-3gcqdxau/acceptance-long-wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww", "--probe", "hooks", "--probe", "evidence", "--probe", "dynamic", "--probe", "delivery", "--probe", "tty", "--probe", "reconnect", "--probe", "terminals", "--probe", "policy", "--probe", "validation", "--probe", "exec_policy"], "started": 1792328121.14238, "finished": 1792328141.2482991, "exit_code": 0, "completed": true, "production_admitted": false, "cases": [{"probe": "hooks", "passed": true}, {"probe": "evidence", "passed": true}, {"probe": "dynamic", "passed": true}, {"probe": "delivery", "passed": true}, {"probe": "tty", "passed": true}, {"probe": "reconnect", "passed": true}, {"probe": "terminals", "passed": true}, {"probe": "policy", "passed": true}, {"probe": "validation", "passed": true}, {"probe": "exec_policy", "passed": true}], "fingerprint": {"adapter_digest": "671e505d610894921d62236357b007501c0550561200a8e52e0ba8b620c4ff03", "binary": "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex", "binary_digest": "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31", "case_sensitive": false, "config_digest": "fa460b796d3de5e776161f272f2cd967ba8605d9c7c0ab40ad3df40c1cf80c6a", "exec_policy": {"basis": "0.163.0 requirements null proves requirements_toml.is_empty including rules; trace is the resulting complete policy", "complete": true, "managed_requirements_absent": true, "trace_digests": ["d28fa40803b826f69cb35102f8d56fa91c9ef6ac380bdc2560deacecedf44a47", "d28fa40803b826f69cb35102f8d56fa91c9ef6ac380bdc2560deacecedf44a47"]}, "features": [{"announcement": "", "defaultEnabled": false, "description": "Preview five-hour and weekly allowance history for consumer accounts in /analytics.", "displayName": "Analytics plan history", "enabled": false, "name": "analytics_plan_history", "stage": "beta"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "daemon_auto_start", "stage": "stable"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "transcript_v2", "stage": "deprecated"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "undo", "stage": "removed"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "shell_tool", "stage": "stable"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "view_image", "stage": "stable"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "sleep_tool", "stage": "stable"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "secret_auth_storage", "stage": "stable"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "unified_exec", "stage": "stable"}, {"announcement": null, "defaultEnabled": true, "description": null, "displayName": null, "enabled": true, "name": "unified_exec_tty", "stage": "stable"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "shell_zsh_fork", "stage": "removed"}, {"announcement": null, "defaultEnabled": false, "description": null, "displayName": null, "enabled": false, "name": "unified_exec_zsh_fork", "stage": "removed"}, {"announcement": null, "defaultEnabled": true, "description"…7065 tokens truncated…tConfig/import/readHistories", "externalAgentConfig/import/recordHistory", "feedback/upload", "fs/copy", "fs/createDirectory", "fs/getMetadata", "fs/readDirectory", "fs/readFile", "fs/remove", "fs/unwatch", "fs/watch", "fs/writeFile", "fuzzyFileSearch", "fuzzyFileSearch/sessionStart", "fuzzyFileSearch/sessionStop", "fuzzyFileSearch/sessionUpdate", "getAuthStatus", "getConversationSummary", "gitDiffToRemote", "hooks/list", "initialize", "marketplace/add", "marketplace/remove", "marketplace/upgrade", "mcpServer/event/stream/start", "mcpServer/event/stream/stop", "mcpServer/oauth/login", "mcpServer/resource/read", "mcpServer/tool/call", "mcpServerStatus/list", "memory/reset", "memory/status", "mock/experimentalMethod", "model/list", "modelProvider/capabilities/read", "permissionProfile/list", "plugin/install", "plugin/installed", "plugin/list", "plugin/read", "plugin/reconcile", "plugin/search", "plugin/share/checkout", "plugin/share/delete", "plugin/share/list", "plugin/share/save", "plugin/share/updateTargets", "plugin/skill/read", "plugin/uninstall", "process/kill", "process/resizePty", "process/spawn", "process/writeStdin", "project/create", "project/delete", "project/import", "project/list", "project/move", "project/read", "project/update", "remoteControl/client/list", "remoteControl/client/revoke", "remoteControl/disable", "remoteControl/enable", "remoteControl/pairing/start", "remoteControl/pairing/status", "remoteControl/status/read", "review/start", "rollout/compress", "server/diagnostics", "skills/config/write", "skills/extraRoots/set", "skills/list", "thread/approveGuardianDeniedAction", "thread/archive", "thread/attachment/add", "thread/attachment/list", "thread/attachment/remove", "thread/attachmentOwner/list", "thread/backgroundTerminals/clean", "thread/backgroundTerminals/list", "thread/backgroundTerminals/terminate", "thread/compact/start", "thread/decrement_elicitation", "thread/delete", "thread/fork", "thread/goal/clear", "thread/goal/get", "thread/goal/set", "thread/increment_elicitation", "thread/inject_items", "thread/items/list", "thread/items/read", "thread/list", "thread/loaded/list", "thread/memoryMode/set", "thread/metadata/update", "thread/name/set", "thread/prediction/request", "thread/queue/add", "thread/queue/delete", "thread/queue/list", "thread/queue/reorder", "thread/queue/start", "thread/queue/update", "thread/read", "thread/readState/update", "thread/realtime/appendAudio", "thread/realtime/appendSpeech", "thread/realtime/appendText", "thread/realtime/listVoices", "thread/realtime/start", "thread/realtime/stop", "thread/resume", "thread/revert", "thread/search", "thread/searchOccurrences", "thread/section/move", "thread/settings/update", "thread/shellCommand", "thread/start", "thread/timeline/list", "thread/turns/list", "thread/unarchive", "thread/unsubscribe", "threadSection/create", "threadSection/delete", "threadSection/list", "threadSection/update", "turn/interrupt", "turn/settings/update", "turn/start", "turn/steer", "userVerification/cancel", "userVerification/delete", "userVerification/enroll", "userVerification/status", "userVerification/verify", "windowsSandbox/readiness", "windowsSandbox/setupStart"], "model": "gpt-6-astra", "os": "macOS-26.6.2-arm64-arm-64bit-Mach-O", "provider": "c1_fixture", "python": "3.15.0", "requirements_digest": "d97d1a5acbd72c5a59997d3d389048a97aa10f729a4455a5efd4e4cde21a7834", "runtime_modes": {"agents_enabled": true, "web_search": "live"}, "schema_digest": "ab12ab78818d775c3720d0d1b0b4aba91d95e32c8ef47c82679661f686793bfe", "socket": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/sm2kr8uqq/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/e2449e710c9038450c50d5dc8fe64a96fc24f75ef68bb24dc0ec8d27544274ee", "resolved_bytes": 94, "uid": 501}, "sqlite": "3.54.0", "user_agent": "software-workflows-coordinator/0.163.0 (Mac OS 26.6.2; arm64) dumb (software-workflows-coordinator; 2.0.0)", "version": "codex-cli 0.163.0"}}
```

</details>

<a id="c64"></a>

<details>
<summary>C64 — paid-final-restart; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,signal,os
from coordinator.client import Client,CoordinatorError
from coordinator import processes,native
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"final-setup.json").read_text());c=Client(setup["states"]["short"],timeout=1800);before=c.call("daemon/status",{})
if processes.identity(before["pid"])["birth"]!=before["birth"]:raise RuntimeError("worker identity changed")
os.kill(before["pid"],signal.SIGKILL);deadline=time.monotonic()+240
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>before["generation"]:break
 except (CoordinatorError,OSError):pass
 time.sleep(.2)
else:raise RuntimeError("recovery timeout")
out={"before":{k:before[k] for k in ("pid","birth","generation")},"after":{k:after[k] for k in ("pid","birth","generation","ready","endpoint")},"adapter_digest":native.adapter_digest(),"time":time.time()}
(root/"paid-final-worker-restart.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"before": {"pid": 95288, "birth": "1792327763:470197", "generation": 3}, "after": {"pid": 14995, "birth": "1792328122:596324", "generation": 4, "ready": true, "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/srt58ihbe/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/srt58ihbe/s", "resolved_bytes": 31, "uid": 501}}, "adapter_digest": "671e505d610894921d62236357b007501c0550561200a8e52e0ba8b620c4ff03", "time": 1792328201.7066638}
```

</details>

<a id="c65"></a>

<details>
<summary>C65 — acceptance-writers; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time,statistics,sqlite3,subprocess
from coordinator.client import Client
from coordinator import native,processes
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"release-setup.json").read_text());out={"digest":native.adapter_digest(),"started":time.time(),"runs":{}}
def start(key):
 c=Client(setup["states"][key],timeout=1800)
 return key,c.call("run/start",{"checkout":setup["checkouts"][key],"mode":"native","task":"C1_HOOKS" if key=="short" else "C1_SURVIVOR","paths":[] if key=="short" else ["base.txt"],"fixture":str(root/"fixture-owned"),"model":"gpt-6-astra"})
with ThreadPoolExecutor(2) as pool:
 for key,run in pool.map(start,("short","long")):out["runs"][key]=run
print(json.dumps({"fresh_runs":out["runs"]}),flush=True)
c=Client(setup["states"]["long"],timeout=1800);run=out["runs"]["long"];out["steer"]=c.call("message/send",{"sender":"lead","recipient":run["actor"],"text":"Acknowledge this message after the command; finish without additional file edits.","wake":"queue"})
deadline=time.monotonic()+45;out["observations"]=[]
while time.monotonic()<deadline:
 actors=[a for a in c.call("records/list",{"kind":"actor","limit":1000})["data"] if a["unit"]==run["unit"]];custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==run["unit"]]
 out["observations"].append({"time":time.time(),"actors":actors,"custody":custody})
 if custody and all(r["state"]=="released" for r in custody) and all(not a.get("active_turn") for a in actors):break
 time.sleep(.2)
out["message"]=next(m for m in c.call("records/list",{"kind":"message","limit":1000})["data"] if m["id"]==out["steer"]["id"]);out["delivery"]=[d for d in c.call("records/list",{"kind":"delivery","limit":1000})["data"] if d["message"]==out["steer"]["id"]]
out["endpoints"]={};out["unknown"]={}
for key in ("short","long"):
 c=Client(setup["states"][key],timeout=1800);run=out["runs"][key]
 listeners=[r for r in c.call("records/list",{"kind":"listener","limit":1000})["data"] if r["unit"]==run["unit"] and r["state"]=="connected"]
 out["endpoints"][key]=[{"endpoint":l["endpoint"],"admission_fingerprint":l["admission_fingerprint"]["adapter_digest"],"service_boundary":l.get("service_boundary"),"pid":l["pid"],"birth":l["birth"],"rss_kib":int(subprocess.check_output(["ps","-o","rss=","-p",str(l["pid"])]).strip())} for l in listeners]
 out["unknown"][key]=c.call("unknown/list",{"checkout":setup["checkouts"][key]})
 if key=="short":
  deadline=time.monotonic()+30
  while time.monotonic()<deadline and c.call("run/status",{"run":run["run"]})["state"]!="completed":time.sleep(.1)
  db=sqlite3.connect("file:"+str(Path(setup["states"][key])/"ledger.sqlite")+"?mode=ro",uri=True)
  messages=[json.loads(r[0]) for r in db.execute("SELECT data FROM events WHERE kind='native' AND subject=?",(listeners[0]["id"],))]
  durations=[m["params"]["run"]["durationMs"] for m in messages if m.get("method")=="hook/completed"]
  out["hook_performance"]={"count":len(durations),"median_ms":statistics.median(durations),"p95_ms":sorted(durations)[int(.95*(len(durations)-1))],"durations_ms":durations}
(root/"acceptance-writers.json").write_text(json.dumps(out,indent=2));print(json.dumps({"hook_performance":out["hook_performance"],"endpoints":out["endpoints"],"message":{k:out["message"][k] for k in ("id","state")},"delivery_methods":[d["method"] for d in out["delivery"]],"custody":[{"state":x["state"],"reason":x.get("reason")} for x in custody],"held_after_turn":any(all(not a.get("active_turn") for a in o["actors"]) and any(r["state"]!="released" and r.get("processes") for r in o["custody"]) for o in out["observations"]),"unknown":out["unknown"]}))
PY
```

Captured output:

```text
{"fresh_runs": {"short": {"actor": "actor_b7883deb520c4395bee83b7827ae562f", "attempt": "attempt_5883769ec30849e9a72fd478a78d32cf", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_5492d519e7cf4640af4551e26423b1f6", "unit": "unit_fb6bb3364e5143859657f30fd190d9c9"}, "long": {"actor": "actor_43124083341445d5921eeebe512ae051", "attempt": "attempt_2a24a33b691e441ea1340422255f42a2", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_5fef8ea5ade7446f81060a60c4ffdfd9", "unit": "unit_506373ef87424e5a8bcec0b5b1115a70"}}}
{"hook_performance": {"count": 110, "median_ms": 42.0, "p95_ms": 49, "durations_ms": [49, 42, 42, 43, 43, 42, 41, 41, 41, 40, 47, 49, 46, 43, 43, 42, 41, 41, 44, 41, 42, 42, 42, 43, 48, 48, 45, 47, 47, 43, 42, 42, 44, 42, 41, 41, 42, 41, 43, 46, 43, 43, 42, 44, 42, 42, 41, 43, 42, 42, 47, 48, 42, 42, 42, 43, 43, 42, 42, 42, 46, 42, 45, 49, 45, 49, 44, 42, 43, 42, 42, 42, 42, 41, 42, 42, 49, 50, 57, 47, 46, 45, 47, 45, 44, 42, 43, 42, 42, 44, 42, 43, 41, 42, 42, 42, 42, 41, 41, 42, 44, 45, 44, 45, 42, 42, 42, 42, 42, 43]}, "endpoints": {"short": [{"endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/s_eq3iece/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/994f4d93bcc772a96200d898d3f2c3d74229ac7e5587277663a638ee296d1751", "resolved_bytes": 94, "uid": 501}, "admission_fingerprint": "671e505d610894921d62236357b007501c0550561200a8e52e0ba8b620c4ff03", "service_boundary": null, "pid": 21195, "birth": "1792328169:179042", "rss_kib": 114416}], "long": [{"endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/scc8l1v6t/s", "requested_bytes": 31, "resolved": "/private/tmp/codex-daemon-501/13a5ded1bdebd37864e984882e0ca34ea54822b29863ceeb5cfb386c9e512aef", "resolved_bytes": 94, "uid": 501}, "admission_fingerprint": "671e505d610894921d62236357b007501c0550561200a8e52e0ba8b620c4ff03", "service_boundary": null, "pid": 21194, "birth": "1792328169:179041", "rss_kib": 101040}]}, "message": {"id": "msg_4c2f2dd16cd14ab68bbf51895c6dfb23", "state": "entered_context"}, "delivery_methods": ["turn/steer"], "custody": [], "held_after_turn": false, "unknown": {"short": {"data": []}, "long": {"data": []}}}
```

</details>

<a id="c66"></a>

<details>
<summary>C66 — paid-service-acceptance; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time
from coordinator.client import Client
from coordinator import processes,native
root=Path("/private/tmp/c1-rework-3gcqdxau");paid=json.loads((root/"paid.json").read_text());c=Client(root/"final-state1",timeout=1800);run=paid["runs"][1]
old=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==run["unit"] and r["state"]!="released"]
for r in old:c.call("custody/reconcile",{"custody":r["id"]})
listener=next(r for r in c.call("records/list",{"kind":"listener","limit":1000})["data"] if r["unit"]==run["unit"] and r["state"]=="connected")
before=[p for p in listener.get("services",[]) if processes.identity(p["pid"]) and processes.identity(p["pid"])["birth"]==p["birth"]]
unknown_before={r["id"] for r in c.call("unknown/list",{"checkout":paid["checkout"]})["data"]}
command="/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import time; time.sleep(2); print(43)'";started=time.time()
message=c.call("message/send",{"sender":"lead","recipient":run["actor"],"wake":"start","text":"Complete the same paid scenario's final service-lifetime observation. Prior custody has been explicitly reconciled and the hook observer has been corrected. Declare this exact non-writing command, execute it once with exec_command login=false and yield_time_ms=10000, acknowledge this message, then finish. No file edits or other commands. Exact command: "+command})
observations=[];deadline=time.monotonic()+90
while time.monotonic()<deadline:
 actor=next(r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["id"]==run["actor"])
 custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==run["unit"] and r["created"]>=started]
 observations.append({"time":time.time(),"active":actor.get("active_turn"),"custody":custody})
 if custody and not actor.get("active_turn") and all(r["state"]=="released" for r in custody):break
 time.sleep(.15)
after=[p for p in before if processes.identity(p["pid"]) and processes.identity(p["pid"])["birth"]==p["birth"]]
unknown_after=c.call("unknown/list",{"checkout":paid["checkout"]})["data"]
out={"started":started,"finished":time.time(),"digest":native.adapter_digest(),"message":message,"command":command,"services_before":before,"same_services_after":after,"observations":observations,"new_unknowns":[r for r in unknown_after if r["id"] not in unknown_before],"retained_prior_unknowns":len(unknown_after)-len([r for r in unknown_after if r["id"] not in unknown_before])}
(root/"paid-service-acceptance.json").write_text(json.dumps(out,indent=2));print(json.dumps({"digest":out["digest"],"services_before":len(before),"same_services_after":len(after),"custody":custody,"new_unknowns":out["new_unknowns"],"retained_prior_unknowns":out["retained_prior_unknowns"]}))
PY
```

Captured output:

```text
{"digest": "671e505d610894921d62236357b007501c0550561200a8e52e0ba8b620c4ff03", "services_before": 13, "same_services_after": 13, "custody": [{"_kind": "custody", "_revision": 16, "actor": "actor_10c952e5c2e44582ac00223245c89680", "checkout": "checkout_c76aa50d546243f6a11d5b78b3104889", "command": "/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import time; time.sleep(2); print(43)'", "completed": 1792328357.216266, "created": 1792328355.0457118, "excludes": ["paid_a.py"], "generation": 4, "id": "custody_d8ad0e478c3146878911c9e7715fe033", "level": "shared", "observed_processes": [{"birth": "1792328355:50534", "lineage": true, "marked": true, "markers": {"CODEX_SESSION_ID": "01a14f0b-1fb5-7d22-8c96-37b10a5cd06c", "CODEX_THREAD_ID": "01a14f0b-1fb5-7d22-8c96-37b10a5cd06c", "CODEX_TOOL_CALL_ID": "exec-27cacc41-ffdf-4591-98db-2263a47d5382"}, "pid": 30420, "ppid": 89461, "uid": 501, "zombie": false}], "operation": "op_3f5905a0306d4ce79ed03a53037eba18", "processes": [], "state": "released", "terminals": ["19111"], "unit": "unit_5920dc9917b94c399827be9380521c31"}], "new_unknowns": [], "retained_prior_unknowns": 2}
```

</details>

<a id="c67"></a>

<details>
<summary>C67 — acceptance-survivor; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time
from coordinator.client import Client
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"release-setup.json").read_text());c=Client(setup["states"]["long"],timeout=1800)
run=c.call("run/start",{"checkout":setup["checkouts"]["long"],"mode":"native","task":"C1_SURVIVOR","fixture":str(root/"fixture-owned"),"model":"gpt-6-astra"})
observations=[];deadline=time.monotonic()+40
while time.monotonic()<deadline:
 actor=next(r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["id"]==run["actor"])
 custody=[r for r in c.call("records/list",{"kind":"custody","limit":1000})["data"] if r["unit"]==run["unit"]]
 observations.append({"time":time.time(),"active":actor.get("active_turn"),"custody":custody})
 if custody and not actor.get("active_turn") and all(r["state"]=="released" for r in custody):break
 time.sleep(.15)
out={"run":run,"observations":observations,"unknown":c.call("unknown/list",{"checkout":setup["checkouts"]["long"]})};(root/"acceptance-survivor.json").write_text(json.dumps(out,indent=2))
print(json.dumps({"run":run,"held_after_turn":any(not o["active"] and any(r["state"]!="released" and r.get("processes") for r in o["custody"]) for o in observations),"custody":custody,"unknown":out["unknown"]}))
PY
```

Captured output:

```text
{"run": {"actor": "actor_ac8e2e87b6774ed489a561211a59bb30", "attempt": "attempt_5dd1515f9af84104bf9c9356447ca7f7", "policy": {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}, "run": "run_2f869c7eab074882972dd8935a463453", "unit": "unit_c8a8e0399c14462493503d31fb5f13a0"}, "held_after_turn": true, "custody": [{"_kind": "custody", "_revision": 35, "actor": "actor_ac8e2e87b6774ed489a561211a59bb30", "checkout": "checkout_7c8400ce95664a72b377f903adf4c838", "command": "/opt/homebrew/opt/python@3.15/bin/python3.15 -B -c 'import subprocess,sys,time; child=subprocess.Popen([sys.executable,\"-B\",\"-c\",\"import time; time.sleep(12)\"]); time.sleep(3)'", "completed": 1792328362.533946, "created": 1792328349.840048, "excludes": ["base.txt"], "generation": 2, "id": "custody_c324a357ee644e7ea34e46e82a779d68", "level": "exclusive", "observed_processes": [{"birth": "1792328349:847976", "lineage": true, "marked": true, "markers": {"CODEX_SESSION_ID": "01a14f17-ea3a-7153-bcd9-c236b9a91972", "CODEX_THREAD_ID": "01a14f17-ea3a-7153-bcd9-c236b9a91972", "CODEX_TOOL_CALL_ID": "fx-ca4ffe003e804794a7844264b18fcc66"}, "pid": 30361, "ppid": 28668, "uid": 501, "zombie": false}, {"birth": "1792328349:866911", "lineage": true, "marked": true, "markers": {"CODEX_SESSION_ID": "01a14f17-ea3a-7153-bcd9-c236b9a91972", "CODEX_THREAD_ID": "01a14f17-ea3a-7153-bcd9-c236b9a91972", "CODEX_TOOL_CALL_ID": "fx-ca4ffe003e804794a7844264b18fcc66"}, "pid": 30362, "ppid": 1, "uid": 501, "zombie": false}], "operation": "op_f242e3455a9143c3ae4036aa0b0bade2", "processes": [], "state": "released", "terminals": ["4372"], "unit": "unit_c8a8e0399c14462493503d31fb5f13a0"}], "unknown": {"data": []}}
```

</details>

<a id="c68"></a>

<details>
<summary>C68 — lead-gap; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,base64,signal,os
from coordinator.client import Client,CoordinatorError
from coordinator import processes
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"release-setup.json").read_text());c=Client(setup["states"]["short"],timeout=1800);run=c.call("run/start",{"checkout":setup["checkouts"]["short"],"mode":"lead","task":"Observe intent/completion crash gap"});image=b"intended bytes written before completion\n"
intent=c.call("operation/prepare",{"actor":run["actor"],"kind":"lead-edit","changes":{"base.txt":{"mode":"100644","bytes_base64":base64.b64encode(image).decode()}}})
(Path(setup["repos"]["short"])/"base.txt").write_bytes(image)
status=c.call("daemon/status",{});identity=processes.identity(status["pid"])
if identity["birth"]!=status["birth"]:raise RuntimeError("worker changed")
checkpoint={"time":time.time(),"worker":identity,"generation":status["generation"],"operation":intent,"actual_bytes_match_intent":(Path(setup["repos"]["short"])/"base.txt").read_bytes()==image}
(root/"lead-gap-checkpoint.json").write_text(json.dumps(checkpoint,indent=2));os.kill(status["pid"],signal.SIGKILL)
deadline=time.monotonic()+120
while time.monotonic()<deadline:
 try:
  after=c.call("daemon/status",{})
  if after["ready"] and after["generation"]>status["generation"]:break
 except (CoordinatorError,OSError):pass
 time.sleep(.1)
operation=next(r for r in c.call("records/list",{"kind":"operation","limit":1000})["data"] if r["id"]==intent["id"]);unknown=c.call("unknown/list",{"checkout":setup["checkouts"]["short"]})
try:c.call("unit/seal",{"unit":run["unit"],"validation":[]})
except CoordinatorError as exc:seal=exc.envelope()
out={"checkpoint":checkpoint,"operation_after":operation,"unknown":unknown,"seal_refusal":seal,"claims":[r for r in c.call("records/list",{"kind":"claim","limit":1000})["data"] if r["unit"]==run["unit"]],"unchanged_bytes":(Path(setup["repos"]["short"])/"base.txt").read_bytes()==image}
(root/"lead-gap.json").write_text(json.dumps(out,indent=2));print(json.dumps({"operation_state":operation["state"],"unknown_count":len(unknown["data"]),"seal_refusal":seal,"claim_states":[x["state"] for x in out["claims"]],"unchanged_bytes":out["unchanged_bytes"],"checkpoint_worker":identity}))
PY
```

Captured output:

```text
{"operation_state": "quarantined", "unknown_count": 2, "seal_refusal": {"code": "UNVERIFIED_OPERATION", "message": "Unit has a pending or quarantined operation", "details": {"operation": "op_0634544cb9b34dcda0c6639fde46db7d"}, "kind": "refusal"}, "claim_states": ["held"], "unchanged_bytes": true, "checkpoint_worker": {"pid": 14368, "birth": "1792328105:655801", "ppid": 12599, "uid": 501, "zombie": false}}
```

</details>

<a id="c69"></a>

<details>
<summary>C69 — listener-death; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time,os,signal
from coordinator.client import Client,CoordinatorError
from coordinator import processes
root=Path("/private/tmp/c1-rework-3gcqdxau");setup=json.loads((root/"release-setup.json").read_text());runs=json.loads((root/"acceptance-writers.json").read_text());run=runs["runs"]["long"];c=Client(setup["states"]["long"],timeout=1800)
actor=next(r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["id"]==run["actor"]);listener=next(r for r in c.call("records/list",{"kind":"listener","limit":1000})["data"] if r["id"]==actor["listener"]);identity=processes.identity(listener["pid"])
if identity["birth"]!=listener["birth"] or actor.get("active_turn"):raise RuntimeError("listener not verified idle")
checkpoint={"time":time.time(),"identity":identity,"listener":listener["id"],"thread":actor["thread"],"active_turn":actor.get("active_turn")};os.kill(listener["pid"],signal.SIGKILL)
deadline=time.monotonic()+90
while time.monotonic()<deadline:
 actor=next(r for r in c.call("records/list",{"kind":"actor","limit":1000})["data"] if r["id"]==run["actor"])
 replacement=next(r for r in c.call("records/list",{"kind":"listener","limit":1000})["data"] if r["id"]==actor["listener"])
 if replacement["id"]!=listener["id"] and replacement.get("recovery_state")=="reconciled":break
 time.sleep(.1)
try:resume=c.call("run/resume",{"run":run["run"],"text":"Do not mutate; report status."})
except CoordinatorError as exc:resume=exc.envelope()
out={"checkpoint":checkpoint,"replacement":{k:replacement.get(k) for k in ("id","pid","birth","state","recovery_state","replaces","services","admission_fingerprint")},"actor":actor,"resume":resume,"unknown":c.call("unknown/list",{"checkout":setup["checkouts"]["long"]}),"claims":[r for r in c.call("records/list",{"kind":"claim","limit":1000})["data"] if r["unit"]==run["unit"]]}
(root/"listener-death.json").write_text(json.dumps(out,indent=2));print(json.dumps({"checkpoint":checkpoint,"replacement":{k:replacement.get(k) for k in ("id","state","recovery_state","replaces")},"same_thread":actor["thread"]==checkpoint["thread"],"active_turn":actor.get("active_turn"),"resume":resume,"claim_states":[r["state"] for r in out["claims"]],"unknown_count":len(out["unknown"]["data"])}))
PY
```

Captured output:

```text
{"checkpoint": {"time": 1792328422.685834, "identity": {"pid": 21194, "birth": "1792328169:179041", "ppid": 14380, "uid": 501, "zombie": false}, "listener": "listener_8bfb5092613f4d32ae80308e6c8821af", "thread": "01a14f15-779f-7f52-bb54-11b9fc6ae183", "active_turn": null}, "replacement": {"id": "listener_215f599a2fdc4dc19fa463926ec8da5d", "state": "connected", "recovery_state": "reconciled", "replaces": "listener_8bfb5092613f4d32ae80308e6c8821af"}, "same_thread": true, "active_turn": null, "resume": {"code": "RECOVERY_UNKNOWN", "message": "Resolve UNKNOWN effects before resuming a writer", "details": {}, "kind": "refusal"}, "claim_states": ["held"], "unknown_count": 1}
```

</details>

<a id="c70"></a>

<details>
<summary>C70 — final-fingerprints; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import ast,json,sys,hashlib,subprocess,platform,sqlite3,time
from coordinator.native import adapter_digest,native_codex,file_digest
root=Path("/private/tmp/c1-rework-3gcqdxau");directory=Path("plugins/software-workflows/coordinator");files=sorted(directory.glob("*.py"))
for p in files:
 content=p.read_text();compile(content,str(p),"exec");ast.parse(content,filename=str(p),feature_version=(3,11))
out={"time":time.time(),"files":len(files),"compiled_in_memory":True,"python311_grammar":True,"python":sys.version,"sqlite":sqlite3.sqlite_version,"os":platform.platform(),"git":subprocess.check_output(["git","--version"]).decode().strip(),"codex":subprocess.check_output([native_codex(),"--version"]).decode().strip(),"binary":native_codex(),"binary_sha256":file_digest(native_codex()),"adapter_digest":adapter_digest(),"hashes":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(root/"final-fingerprints.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"time": 1792328423.916447, "files": 19, "compiled_in_memory": true, "python311_grammar": true, "python": "3.15.0 (main, Oct  9 2026, 11:16:41) [Clang 21.0.0 (clang-2100.3.34.2)]", "sqlite": "3.54.0", "os": "macOS-26.6.2-arm64-arm-64bit-Mach-O", "git": "git version 2.56.0", "codex": "codex-cli 0.163.0", "binary": "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex", "binary_sha256": "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31", "adapter_digest": "671e505d610894921d62236357b007501c0550561200a8e52e0ba8b620c4ff03", "hashes": {"__init__.py": "42b1472bf64dff35243cbdc7d7c58ea01d546e12c7ea3d499d19feb69d85144d", "__main__.py": "1e0b39642b662e165fba55bc0d797b5cbf1cb08f9541c3ec3c0526536565785f", "admission.py": "8c668cf1a359700ce89f71a483fb5fd70e071718c35372f6fb004d33c7c0d11b", "client.py": "443b7363bd93d7a79f36fc636741b2b0c06610af9bbce818aaaa100a59ccd6ec", "custody.py": "d1fa40948f9654283259ba2063a8cd71fa2996975bda90f68bcf5e8258f80168", "daemon.py": "96c221675353801d3f2fdd07e052b0f869784945c9d873ddb09de21ebb654111", "fixture.py": "e13148742316639fcf19a930a41e0953ac68b87edd6e644e60bc41ebd5389d9b", "gitio.py": "9632814c06e797beee089e44a22d5a7ef86c096cf339ad627bab9e8e26318407", "hooks.py": "8368fed7db67a1f38a9875ee08a3db48f30f6b4aa3f501ee56925435b3a561a9", "ledger.py": "8ec6a11a319b3907761c47cb7e31d027761994105e78fbfa7b239f7019924e87", "locks.py": "dfbd910bd9fde55636c6bd11baf29b2c097eb3c856b8231973a2bb305b8aff7f", "messages.py": "d54ed33ad13bd30161ce506913d4a31d99fa6c4d9109e94e9e148460bf1c31a8", "native.py": "9f8afa7199d9b7c0c9d90cf61e333000a73801cdc5e60aa62feed2e55dea654a", "processes.py": "19343e95c5ec72044af38193c6a97836dadd6f25219fb8be4a6c69fbf0efef9d", "publish.py": "e494c10e94e09a5d61f51d01afd219b67152b7ae8d26f31a613a510c0b8dc19a", "recovery.py": "978e10e1f90d388da8a44f38608cf03a4140c1fab7096dfb889a94b92b75043e", "runtime.py": "c623059f191f8623d09cec3838306a7827854264df344c742247605bb5c689cb", "store.py": "7430209231c6096e864211bb53557b0fb01e1c068725d7a3947e9f7ed6faea4c", "tools.py": "d88ae85ea2ec3c0d72ffcea78c6f96afae986da3c026b3296b8e75ef840776df"}}
```

</details>

<a id="c71"></a>

<details>
<summary>C71 — final-python39-client; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B - <<'PY'
import json,sys
from coordinator.client import Client
c=Client("/private/tmp/c1-rework-3gcqdxau/release-one1");s=c.call("daemon/status",{});print(json.dumps({"python":sys.version,"ready":s["ready"],"endpoint":s["endpoint"],"locks":s["locks"]}))
PY
```

Captured output:

```text
{"python": "3.9.6 (default, May 22 2026, 11:13:45) \n[Clang 21.0.0 (clang-2100.1.1.101)]", "ready": true, "endpoint": {"measured_limit": 103, "mode": "0o600", "requested": "/private/tmp/c1-501/sc_wr7y0e/s", "requested_bytes": 31, "resolved": "/private/tmp/c1-501/sc_wr7y0e/s", "resolved_bytes": 31, "uid": 501}, "locks": {"actor": 0, "checkout": 0, "message": 0, "rpc": 0, "unit": 0}}
```

</details>

<a id="c72"></a>

<details>
<summary>C72 — final-python39-hook; observed exit 0</summary>

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B plugins/software-workflows/coordinator/hooks.py --check
```

Captured output:

```text
{"compatible": true, "protocol": "jsonl-v1"}
```

</details>

<a id="c73"></a>

<details>
<summary>C73 — final-python39-help; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m coordinator --help
```

Captured output:

```text
usage: __main__.py [-h] [--state STATE] {daemon,rpc,admission,fixture} ...

Operational CLI. Exit 0: completed; 2: refusal; 3: operational failure.

positional arguments:
  {daemon,rpc,admission,fixture}
    daemon              Start/discover/status/stop the independent daemon
    rpc                 Call a documented C2 RPC method with JSON params
    admission           Run native capability admission
    fixture             Local product capability fixture; no model/network
                        spend

optional arguments:
  -h, --help            show this help message and exit
  --state STATE         External private state directory
```

</details>

<a id="c74"></a>

<details>
<summary>C74 — socket-boundary; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
import json,os,socket,tempfile,time
from pathlib import Path
from coordinator import runtime
from coordinator.client import CoordinatorError
root=Path("/private/tmp/c1-rework-3gcqdxau");directory=Path(tempfile.mkdtemp(prefix="bind-",dir=runtime.runtime_root()));limit=runtime.capacity();rows=[]
try:
 for length in (limit,limit+1):
  path=directory/("x"*(length-len(os.fsencode(directory))-1));row={"path":str(path),"bytes":len(os.fsencode(path))}
  with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
   try:s.bind(str(path));row["bind"]="success"
   except OSError as exc:row["bind_error"]=str(exc)
  if path.exists():path.unlink()
  try:runtime.check_length(path);row["precheck"]="accepted"
  except CoordinatorError as exc:row["precheck"]=exc.envelope()
  rows.append(row)
finally:directory.rmdir()
out={"time":time.time(),"measured_limit":limit,"binds":rows};(root/"socket-boundary.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
{"time": 1792328449.719106, "measured_limit": 103, "binds": [{"path": "/private/tmp/c1-501/bind-aw_7q99d/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "bytes": 103, "bind": "success", "precheck": "accepted"}, {"path": "/private/tmp/c1-501/bind-aw_7q99d/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "bytes": 104, "bind_error": "AF_UNIX path too long", "precheck": {"code": "SOCKET_PATH_LENGTH", "message": "Physical Unix socket path exceeds the measured bind limit", "details": {"path": "/private/tmp/c1-501/bind-aw_7q99d/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "bytes": 104, "measured_limit": 103}, "kind": "refusal"}}]}
```

</details>

<a id="c75"></a>

<details>
<summary>C75 — cleanup-daemons; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,time
from coordinator.client import Client,CoordinatorError
from coordinator import processes
root=Path("/private/tmp/c1-rework-3gcqdxau");env=json.loads((root/"environment.json").read_text());final=json.loads((root/"final-setup.json").read_text());release=json.loads((root/"release-setup.json").read_text());states=[env["short"],env["long"],str(root/"interrupt-state"),*final["states"].values(),*release["states"].values()];out=[]
for state in states:
 c=Client(state,timeout=120);row={"state":state,"started":time.time()}
 try:
  status=c.call("daemon/status",{});row["worker"]={k:status[k] for k in ("pid","birth","generation")};row["reconciliation"]=[]
  for reservation in c.call("records/list",{"kind":"custody","limit":10000})["data"]:
   if reservation["state"]!="released":
    try:
     result=c.call("custody/reconcile",{"custody":reservation["id"]});row["reconciliation"].append({"id":reservation["id"],"state":result["state"]})
    except CoordinatorError as exc:row["reconciliation"].append({"id":reservation["id"],"error":exc.envelope()})
  row["stop"]=c.call("daemon/stop",{})
  deadline=time.monotonic()+10
  while time.monotonic()<deadline:
   p=processes.identity(status["pid"])
   if not p or p["birth"]!=status["birth"] or p["zombie"]:break
   time.sleep(.05)
  row["worker_stopped"]=not p or p["birth"]!=status["birth"] or p["zombie"]
 except (CoordinatorError,OSError) as exc:row["error"]=exc.envelope() if isinstance(exc,CoordinatorError) else {"type":type(exc).__name__}
 out.append(row);print(json.dumps(row),flush=True)
(root/"cleanup-daemons.json").write_text(json.dumps(out,indent=2))
PY
```

Captured output:

```text
{"state": "/private/tmp/c1-rework-3gcqdxau/ssssssssssss", "started": 1792328525.731487, "worker": {"pid": 26414, "birth": "1792325955:284272", "generation": 2}, "reconciliation": [], "stop": {"stopping": true}, "worker_stopped": true}
{"state": "/private/tmp/c1-rework-3gcqdxau/long-state-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/state", "started": 1792328525.9045582, "worker": {"pid": 26452, "birth": "1792325955:921377", "generation": 2}, "reconciliation": [{"id": "custody_006d77971a7f45e3ba69d3d009e179b7", "state": "released"}], "stop": {"stopping": true}, "worker_stopped": true}
{"state": "/private/tmp/c1-rework-3gcqdxau/interrupt-state", "started": 1792328526.516387, "worker": {"pid": 93573, "birth": "1792327641:466484", "generation": 6}, "reconciliation": [], "stop": {"stopping": true}, "worker_stopped": true}

{"state": "/private/tmp/c1-rework-3gcqdxau/final-state1", "started": 1792328526.574222, "worker": {"pid": 14995, "birth": "1792328122:596324", "generation": 4}, "reconciliation": [], "stop": {"stopping": true}, "worker_stopped": true}
{"state": "/private/tmp/c1-rework-3gcqdxau/final-long-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/state", "started": 1792328526.845402, "worker": {"pid": 99458, "birth": "1792327814:180431", "generation": 4}, "reconciliation": [], "stop": {"stopping": true}, "worker_stopped": true}
{"state": "/private/tmp/c1-rework-3gcqdxau/release-one1", "started": 1792328527.168424, "worker": {"pid": 30807, "birth": "1792328389:184870", "generation": 3}, "reconciliation": [], "stop": {"stopping": true}, "worker_stopped": true}
{"state": "/private/tmp/c1-rework-3gcqdxau/release-long-rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr/state", "started": 1792328527.274954, "worker": {"pid": 14380, "birth": "1792328106:85591", "generation": 2}, "reconciliation": [], "stop": {"stopping": true}, "worker_stopped": true}
```

</details>

<a id="c76"></a>

<details>
<summary>C76 — cleanup-providers; observed exit 0</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import json,os,signal,time
from coordinator import processes
root=Path("/private/tmp/c1-rework-3gcqdxau");out=[]
for name in ("fixture","fixture-more","fixture-parallel","fixture-owned"):
 record=json.loads((root/name/"fixture.json").read_text());p=processes.identity(record["pid"])
 if not p or p["birth"]!=record["birth"]:raise RuntimeError("fixture birth mismatch")
 os.kill(p["pid"],signal.SIGTERM);out.append({"fixture":name,"pid":p["pid"],"birth":p["birth"],"signal":"SIGTERM","time":time.time()})
(root/"cleanup-providers.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
PY
```

Captured output:

```text
[{"fixture": "fixture", "pid": 16338, "birth": "1792325575:684145", "signal": "SIGTERM", "time": 1792328544.746847}, {"fixture": "fixture-more", "pid": 21055, "birth": "1792325701:631835", "signal": "SIGTERM", "time": 1792328544.7471738}, {"fixture": "fixture-parallel", "pid": 33912, "birth": "1792326136:396475", "signal": "SIGTERM", "time": 1792328544.747401}, {"fixture": "fixture-owned", "pid": 61635, "birth": "1792326966:20597", "signal": "SIGTERM", "time": 1792328544.747455}]
```

</details>

<a id="c77"></a>

<details>
<summary>C77 — final-files-and-evidence-check; observed exit 0</summary>

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.15/bin/python3.15 -B - <<'PY'
from pathlib import Path
import ast,datetime,hashlib,json,re,sys
p=Path('plugins/software-workflows/coordinator');issues=[];files=sorted(q for q in p.iterdir() if q.is_file())
for f in files:
 data=f.read_text()
 for n,line in enumerate(data.splitlines(),1):
  if line.rstrip(' \t')!=line:issues.append({'file':f.name,'line':n,'issue':'trailing whitespace'})
 if not data.endswith('\n'):issues.append({'file':f.name,'issue':'missing final newline'})
 if f.suffix=='.py':compile(data,str(f),'exec');ast.parse(data,filename=str(f),feature_version=(3,11))
root=Path('/private/tmp/c1-rework-3gcqdxau');fp=json.loads((root/'final-fingerprints.json').read_text())
changed=[name for name,value in fp['hashes'].items() if hashlib.sha256((p/name).read_bytes()).hexdigest()!=value]
verify=(p/'VERIFY.md').read_text();missing_commands=sorted(set(re.findall(r'\]\(#(c\d+)\)',verify))-set(re.findall(r'<a id="(c\d+)">',verify)))
missing_artifacts=sorted(set(path for path in re.findall(r'\]\((/private/tmp/c1-rework-3gcqdxau/[^)]+)\)',verify) if not Path(path).exists()))
out={'time':datetime.datetime.now(datetime.UTC).isoformat(),'files_checked':len(files),'python_files':len(fp['hashes']),'compiled_in_memory':True,'python311_grammar':True,'changed_since_final_acceptance':changed,'whitespace_issues':issues,'missing_command_links':missing_commands,'missing_artifacts':missing_artifacts}
(root/'final-checks.json').write_text(json.dumps(out,indent=2));print(json.dumps(out));sys.exit(1 if issues or changed or missing_commands or missing_artifacts else 0)
PY
```

Captured output:

```text
{"time": "2026-10-18T13:12:18.487692+00:00", "files_checked": 21, "python_files": 19, "compiled_in_memory": true, "python311_grammar": true, "changed_since_final_acceptance": [], "whitespace_issues": [], "missing_command_links": [], "missing_artifacts": []}
```

</details>

## C2 implementation observations — 2026-10-18 (ongoing, not a completion claim)

All C2 scratch repositories, coordinator state, provider state, disposable homes,
server logs and plugin venvs are under
`/private/tmp/software-workflows-c2-wrGSzU`. No paid model call has been made.
No test files or test harness were added. The inline commands below operate the
actual installed Codex executable, coordinator, Git plumbing and MCP server.

### Step 0a: observations and repairs

- Ordinary-command ownership initially failed to yield native command lifecycle
  events. The first observer exited **1 (StopIteration)**. A subsequent diagnostic
  exited **0**, showing commands had run through inherited caller routing.
  Clearing only `CODEX_SESSION_ID`, `CODEX_THREAD_ID`, and
  `CODEX_TOOL_CALL_ID` restored private native commandExecution events for
  `/usr/bin/true`. This is an additional installed-runtime integration finding,
  not a change to user configuration or MCP servers.
- The first repaired ownership probe (non-detached background sleep) exited **0**
  but reported **positive=false, negative=true**. It did not demonstrate a
  reparented survivor. Its evidence remains in `ownership-green.json`.
- The revised native probe ran `/bin/zsh`, `/usr/bin/true`, and a detached
  `/bin/sleep` child that outlived its parent. It exited **0** with
  **positive=true, negative=true, settled=true**. The observed survivor was PID
  44935, birth `1792336235:215289`, kernel identity 8188321, original-parent
  kernel identity 8188320, PPID 1, and empty markers. It was attributed only to
  the listener ownership domain; custody remained until verified disappearance.
  The unrelated live child caused PROCESS_AMBIGUOUS. Evidence:
  `ownership-green2.json`.
- Equivalent fresh-listener fingerprints previously differed because socket
  paths were included. After normalization, both were
  `f7d82054d0db45b39495f7bd59d64b301ee59e479a424e9b6f85b985146b9a06`.
  Changing effort to max produced
  `ff9b0a9c0a895d2419309f50ea80197fb0820017c4cdeeef8dfb0a2e4ca35aea`.
  Observer exit **0**.
- Full writer admission through the actual daemon hooks passed twice: fingerprint
  `54ffaadf39653ee7421d2df7e19e8a48d3d67bc24f31b5b52c875cdf07d2094e`;
  first cache miss, second cache hit. Runs
  `run_15dd6a45c7724ed3acf15097ee47f201` and
  `run_18acff778f8b42b4867a001550ed0d04`. Observer exit **0**.
- Three discovery/attachment/stop cycles returned connection, queue, worker and
  pending-request registry lengths to **0/0/0/0**. Observer exit **0**.
  The pre-fix leak is grounded in C1's implementation; this run did not execute
  a second copy of the old daemon as a separate leak baseline.
- Publication with a real foreign Git index lock advanced the ref, refused with
  INDEX_LOCKED, and left an index_pending journal. After an unexplained edit and
  the index-lock holder's exit **0**, recovery reached complete, kept the changed
  working bytes, and retained UNKNOWN for a.txt. Observer exit **0**.
- A real reference-verification transaction prevented publication before ref
  installation (REF_TRANSACTION, journal commit_created). After an independent
  scratch parent advance, recovery refused STALE_BASE. Explicit abandonment
  invalidated the review, reopening and a fresh seal succeeded, and a new lead
  run was admitted. Observer exit **0**. Publication:
  `publication_26663fec232440af9e9b8ab6688e6175`; reopened unit:
  `unit_3fc0f4a207b94b978c440f2b6fe2f0aa`.
- Two setup mistakes were retained as failed observations: wrong RPC spelling
  `lead/edit` (exit **1**, corrected to `lead-edit`), and registering a
  checkout already owned by a different scratch state (CHECKOUT_OWNED, exit
  **1**, corrected by using its owning state). Neither is a successful check.

### Startup and first actual MCP observations

`CLAUDE_PLUGIN_DATA=/private/tmp/software-workflows-c2-wrGSzU/plugin-data
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.15 -B
plugins/software-workflows/run_server.py --prepare` exited **0**:
the prepared venv is `plugin-data/venv/91e1e736a04fad0e`.

The first two simultaneous MCP Abraham calls in `mcp-repo` were refused by the
mixed-version barrier before inference: an existing server's registry is in the
normal log root, which this task forbids reading. Provider requests **0**;
working files unchanged; daemon/stop returned stopping=true; observer exit **0**.
This is a refusal observation, not successful native execution.

The explicit scratch log-root override now limits old-server discovery to the
same verified environment namespace **only when both target and logs share an
owned private temporary ancestor**. Ordinary checkouts keep the global barrier.
Unknown process environment/identity still refuses. This is a necessary
deviation for the brief's isolated verification; it does not make separately
configured log namespaces mutually exclusive.

After that fix, two simultaneous calls through the actual MCP stdio server in
`mcp2-repo` completed both native turns:

| Task | Durable run | Unit | Result |
|---|---|---|---|
| CASE_A | run_14a4281654714498ac4f8eb551fdf79a | unit_2995237bbf8d48da92463a0a1ddab86c | completed, 27.95 s; exact attribution only a.txt |
| CASE_B | run_c54029cf724b47898315479a8ff70915 | unit_7e5a00c5219c407a8e02487feeb9dde8 | completed, 27.19 s; exact attribution only b.txt |

Both first lines were signed status:ok, reasoning:max. Policy echoes were
approvalPolicy=never and dangerFullAccess. Both used fingerprint
`e857b526c7fe8b4de6408ffbae6f65fd7604ad4cb581a2f258eb6f5895895c2d`,
one miss and one hit. Provider requests **6**, errors **[]**; files contained
CASE_A and CASE_B respectively. Both native runs appeared in codex_runs with
claims, actors, phase, unit, custody=[], unknown_count=0. Daemon/stop returned
stopping=true. Observer exit **0**. The two MCP calls overlapped; because the
fixture answers quickly, this observation alone does not establish prolonged
overlap of their production turns.

MCP tools/list at this point exposed the original nine tools. Lead tools had not
yet been added. In-memory compile and Python 3.11 grammar parse of server.py and
all coordinator Python files exited **0** before these MCP runs.

Early Step 0a probes used C1's default short runtime allocation root
`/private/tmp/c1-501`, and Codex may place physical sockets under
`/private/tmp/codex-daemon-501`. Their listeners were stopped. No default
coordinator state root was created. Later runs use SOFTWARE_WORKFLOWS_RUNTIME
under the scratch root. Final residue/process inspection is still required.

### Exact first successful MCP operational command

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio, json, os, time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio, processes
from coordinator.fixture import Provider
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU')
repo=root/'mcp2-repo'; repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q'])
rows=[]
for name in ('a.txt','b.txt'):
    (repo/name).write_text('baseline\n')
    oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n')
    rows.append('100644 '+oid+'\t'+name+'\n')
gitio.git(repo,['read-tree','--empty'])
gitio.git(repo,['update-index','--index-info'],data=''.join(rows).encode())
tree=gitio.text(repo,['write-tree'])
meta={'GIT_AUTHOR_NAME':'C2 scratch','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2 scratch','GIT_COMMITTER_EMAIL':'c2@invalid'}
base=gitio.text(repo,['commit-tree',tree],data=b'C2 scratch baseline\n',extra_env=meta); gitio.git(repo,['update-ref','HEAD',base])
home=root/'mcp2-home'; home.mkdir(mode=0o700)
fixture=root/'mcp2-fixture';fixture.mkdir(mode=0o700)
state=root/'mcp2-state'; runtime=root/'rt-mcp2'; logroot=root/'mcp2-logs'
plan={'routes':{tag:[{'text':'Implementation brief: change only '+name+' through apply_patch.'},{'patch':'*** Begin Patch\n*** Update File: '+name+'\n@@\n-baseline\n+'+tag+'\n*** End Patch\n'},{'text':'Implemented '+name+'.'}] for tag,name in [('CASE_A','a.txt'),('CASE_B','b.txt')]}}
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(runtime),CODEX_ORACLE_LOG_DIR=str(logroot),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='300')
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')})
async def main():
    params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
    async with stdio_client(params,errlog=open(root/'mcp-server-stderr.log','w')) as streams:
        async with ClientSession(*streams) as session:
            initialized=await session.initialize()
            catalog=await session.list_tools();print('MCP initialized; tools:',[t.name for t in catalog.tools],flush=True)
            async def dispatch(tag,name):
                t=time.monotonic()
                answer=await session.call_tool('abraham',{'task':tag+' Change only '+name,'target':str(repo),'claims':[name]})
                text='\n'.join(c.text for c in answer.content if c.type=='text')
                (root/(tag+'-result.txt')).write_text(text)
                print(tag,'elapsed',round(time.monotonic()-t,2),'isError',answer.isError,'result',text[:18000],flush=True)
            await asyncio.gather(dispatch('CASE_A','a.txt'),dispatch('CASE_B','b.txt'))
            listing=await session.call_tool('codex_runs',{});print('RUNS',listing.model_dump_json(),flush=True)
with Provider(plan=plan) as provider:
    (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
    try:
        asyncio.run(main())
        print('Provider requests:',len(provider.requests),'errors:',provider.errors,flush=True)
        print('Files:',{p.name:p.read_text() for p in [repo/'a.txt',repo/'b.txt']},flush=True)
    finally:
        client=Client(state,timeout=45)
        try:
            status=client.call('daemon/status')
            for run in client.call('run/list',{'cwd':str(repo)})['data']:
                if run['state'] not in ('completed','cancelled','failed','refused','blocked'):
                    print('Cleanup cancellation',client.call('run/interrupt',{'run':run['run']}),flush=True)
            print('Daemon stop',client.call('daemon/stop'),flush=True)
        except Exception as exc:print('Cleanup requires attention:',type(exc).__name__,str(exc),flush=True)
PY
```

## C2 implementation verification, continued — 2026-10-18

This section supersedes the earlier **ongoing** C2 status, without changing the
C1 history or treating an earlier failed observation as a pass. The allowed paid
scenario has now run **once**, as two concurrent Abraham calls using the user's
normal Codex configuration. Other inference observations used the existing
loopback fixture provider with the actual installed Codex executable.

### Observed environment and scope

- macOS; Codex `0.163.0`; Git `2.56.0`; hook interpreter
  `/usr/bin/python3` (`3.9.6`); server launcher
  `/opt/homebrew/bin/python3.15 -B plugins/software-workflows/run_server.py`.
  The launcher selected its real scratch venv; SQLite was `3.54.0`.
- Scratch root: `/private/tmp/software-workflows-c2-wrGSzU`.
  All C2 repositories, fixture providers, later admission calibrations,
  disposable homes, server logs and plugin data were isolated there.
  Early admission calibration directories also used the OS temporary directory;
  that implementation was corrected to place isolated calibrations under the
  supplied state root. Early allocator paths used `/private/tmp/c1-501`.
  Codex itself can redirect physical sockets to
  `/private/tmp/codex-daemon-501`; those are runtime-selected paths, not
  evidence that the coordinator used its normal state root.
- No test files or reusable test harness were added. The commands below are
  inline operational observations against real APIs/processes. Scratch baseline
  commits and publication observations used Git plumbing. This checkout's index,
  refs and working changes belonging to the lead were not used for verification.
- The installed host was validated with scratch `CLAUDE_CONFIG_DIR` values.
  No transcript, environment file, credential file, or `~/.claude` content was
  read. The paid scenario used the normal home **through Codex**; only its own
  returned rollout's selected policy fields were read back by the implementation.

### Native workflow, attribution, messages and publication

The `mcp7` observation exited **0**, provider requests **8**, provider errors
**[]**, and `daemon/stop` returned `stopping:true`.

- A: `run_d5806ad23ab34c5e9c56159b47464ab6`,
  `unit_dd6fdb9a40c748dbbc9d26a3d726dc7b`.
- B: `run_3f78a53e71ea4e3da6f133a9bf7d0b39`,
  `unit_98f8878c64384ff8aa33fc7b457c6c79`.
- Both native Abraham workflows completed. Their results credited only their
  own verified `a.txt` or `b.txt` edit, with native actor/thread identity.
  Overlap was refused and named the holder.
- Actual MCP initialization exposed all nine existing tools plus six lead tools.
  An inherited instance's actual writer catalog exposed only
  `architect_review`, `codex_query`, and `research`.
- An unread lead message remained unread across two inbox reads; explicit
  acknowledgement changed its state.
- While B's implementation turn remained active, A was sealed, reviewed through
  the real `code_review` exec path against its complete immutable object diff,
  and published. Seal `seal_0890fd4953e34caf8dac2f130e92784f`;
  review job `review_job_72de79eaba90410a8ac468031ad0efd8`;
  review `review_8126d874fe9047adbda7b31b2570db2c`;
  published commit `2d7b5b464fbd7807abe32687c9ac17c1635b1d37`.
  The commit changed only A's path; B continued. An unrelated staged `c.txt`
  entry retained its original object ID. Publication reached `complete`.

The `edge` observation exited **0**, provider requests **15**, errors **[]**.

- A native analysis `apply_patch` attempt was explicitly denied; no forbidden
  analysis bytes or exact receipt appeared. Its later implementation received
  only its own verified receipt. The same run sent messages to peer and lead;
  the peer's native acknowledgement was recorded, while the lead message
  remained unread until explicitly acknowledged.
- Native local-image input reached both phase submissions. The explicit
  200000-token window produced the measured **130000** compaction limit;
  effective effort was max, web search live, reasoning summary detailed.
- Durable result pagination advanced 0 → 40 → 80; event cursors were exposed.
- An opaque analysis shell write produced UNKNOWN and did not start
  implementation. This first edge observation also encountered a short-lived
  process-inspection race and retained a wildcard UNKNOWN/custody quarantine.
  A later explicit custody reconciliation proved the process gone and released
  custody; it did not erase UNKNOWN. That run is not the clean short-command
  acceptance observation.

Two subsequent operational repeats distinguished and repaired the remaining
short-command races:

1. `edge2` used ten sequential commands. It exposed a verified hook helper
   that had first been observed as a listener-domain survivor. Retaining that
   old classification made the next hook wait for the previous command.
   The observer exited **0** but initially could not stop the daemon:
   `NOT_QUIESCENT`. This was a failed behavior observation.
2. Exact helper identity now overrides only its prior domain observation;
   later children remain separately owned/quarantined. Darwin identity reads
   also retry transient kernel lookup failures, still requiring verified
   disappearance or identity evidence.
3. `edge3` exited **0**: nine actual `/usr/bin/true` invocations and the
   final `printf opaque > x.txt` all completed with native exit code **0** and
   operation state `reconciled`. The run
   `run_a9336d064bd64ee9be4c61f329ce685c` ended **blocked**, custody **[]**,
   UNKNOWN count **1**, for `x.txt` only. No implementation turn was submitted.
   A real resume nudge returned `RECOVERY_UNKNOWN`. Provider requests **11**,
   errors **[]**, stop succeeded.
4. The old `edge2` instance was explicitly reconciled, cancelled and stopped.
   Cleanup observer exit **0**, output `RECONCILE released`,
   `CANCEL cancelled`, `STOP {'stopping': True}`.

### Crash recovery, cancellation and lifetime protection

The corrected `recovery2` observation exited **0**, provider requests **8**,
errors **[]**, stop succeeded.

- C: `run_6b0206ccda7342ea82c19d7f398c970e`.
  D: `run_7dce304ccae946f8ad82c981daa0df33`.
- The actual MCP server PID **8555**, birth `1792339896:412457`, was
  verified and killed. Its venv's `.in-use` lock remained held.
- Worker PID **8582**, birth `1792339896:896732`, was then verified and
  killed. A competing exec lock acquisition returned **false** while the
  independent custodian PID **8579** retained the legacy bridge/kernel lease.
  Replacement worker **10443**, generation **2**, became ready.
- Reconnected MCP clients listed the same durable runs from both dispatch
  and target folders.
- C had a native child and a markerless `/bin/sleep` survivor, PID **10428**,
  birth `1792339923:697559`, kernel identity **8253343**, observed at PPID **1**.
  Cancelling C reached `cancelled`, no cancellation failures, no active root
  or child turn, empty custody and no UNKNOWN.
- D was still running after C's cancellation, then completed with only its
  `d.txt` receipt. `c.txt` retained baseline bytes.
- Each workflow retained exactly one analysis and one implementation input ID
  and native turn. Collection/recovery did not duplicate either phase.
  Rollout read-back verified never/full access/max.

The first `recovery` observer exited **0** but its crash/cancel observation was
not accepted: the first driver did not identify the actual server reliably, and
cancellation exposed stale child subscription/history handling. After correction,
a separate cleanup loaded the replacement worker (generation 3), reconciled and
cancelled C, observed D complete, and stopped the daemon; cleanup exit **0**.
Two intermediate diagnostics exited **1** (`RECOVERING`, and an observer's
incorrect assumption that raw SQLite JSON embeds its record ID).

### Compatibility locks and named exec fallback

The independent-process `locks` command exited **0**:

- A native Custodian acquired one lease; a second acquire through a symlink
  shared that same lease (`leases 1`).
- Exec acquisition through the real path, symlink, and subdirectory all refused.
- After native release, exec acquired. While a separate exec process held it,
  native acquisition refused; after exec exit **0**, native acquired.
- A live older-format content-lock holder also blocked native acquisition.
  After that disposable sleep holder was terminated (exit **-15**) and its
  owned legacy file removed, native acquired.
- Authoritative flock inode **749059249** remained unchanged throughout.
  Final Custodian and compatibility descriptor maps were empty.

The `fallback` observation forced the native binary to `/bin/true` while
leaving the existing exec Codex binary valid. Observer exit **0**:

```text
[Codex model: gpt-6-astra | reasoning: max | tool:abraham | status:ok | ...]
[Native admission/startup failed: NATIVE_START_FileNotFoundError. Using the exclusive legacy exec backend.]
```

Both legacy exec phases had journal status `ok` and subprocess return code **0**;
`f.txt` became `fallback\n`, HEAD was unchanged. The refused native attempt
`run_c857275ddbde41598967f485ac200fbf` had `production_started:false`,
no held claims and no listeners. Provider requests **3**, errors **[]**; stop
succeeded. Caller routing identities were stripped from exec's environment too.

### Bound review and publication refusals

The corrected `refusal3` command exited **0**, provider requests **7**, errors
**[]**, stop succeeded. Seven actual review turns continued native review thread
`01a14fcc-94e0-7c22-8ad4-f3eb7c6d30d3`.

- Needs changes → negative history, no passing review.
- Quoted `> Verdict: Ship it` → ambiguous/negative history.
- Conflicting Ship it / Do not ship sections → ambiguous/negative history.
- Publishing with each nonpassing job refused `REVIEW_BINDING`.
- A successful own verdict created a bound review.
- Subsequent unexplained candidate-path bytes refused
  `UNKNOWN_BLOCKS_PUBLICATION`.
- Reopening/resealing invalidated the old binding (`REVIEW_BINDING`).
- Advancing the scratch parent refused `STALE_BASE`.
- Those refusals left HEAD and the object-file inventory unchanged.
- The first repository-identity observation reached the MCP scope guard
  (`UNIT_SCOPE`), so it was not claimed as direct Publisher identity evidence.

The focused `refusal4` direct-RPC observation replaced the scratch `.git`
directory with an equal-content directory of different filesystem identity.
It exited **0**, provider requests **1**, errors **[]**, stop succeeded:

```text
DIRECT_REFUSAL CHECKOUT_IDENTITY_CHANGED Publication repository identity changed; explicit recovery required
UNCHANGED identity True True
```

That verifies identity refusal before new objects/ref mutation.

A separate detached-review observation exited **0**. After verified MCP PID
**33942** was killed, a new MCP server collected exec run
`infra1·33942·5f38`, recorded review job
`review_job_2d716aa1d5de410eb5f75b8d45117b87`, and created review
`review_74891b2a65134bf79369c29dc7cb854b`. Output confirmed
`SAME_BINDING True FULL_ANSWER True REQUESTS 1`. The original complete answer
hash was `441699de2bdbd47795096dd6cf04fee4963014d8a41ab77f48e992cc51b66002`.
No replacement inference was started; stop succeeded.

Earlier review observers are failures, not passes: one wrong input field
(`content_base64` instead of `bytes_base64`) exited **1**; one fixture
router incorrectly matched earlier continued-turn history and exited **1**.
An earlier MCP review attempt reached an unauthenticated endpoint and received
HTTP **401**; no model inference occurred. Subsequent disposable-home config
correctly routed all review scenarios to the fixture provider.

### Lead hooks and observational gate query

The Python 3.9 direct hook lifecycle observer exited **0**:

- Claimed-path Write denied and named the holder.
- Free-path Write, literal Edit and ordered MultiEdit were prepared, the real
  files were changed externally, and matching PostToolUse produced exact
  receipts. Duplicate post callbacks were idempotent.
- A failed partial edit remained uncertain. Making bytes match afterward did
  not repair the missing/failed completion (`LEAD_HOOK_UNCERTAIN`).
- A prepared operation missing its post event was quarantined during restart.
- NotebookEdit insertion was explicitly refused
  `NOTEBOOK_ADAPTER_UNVERIFIED`, with a full-file Write route.
- Bash was observed and reconciled, never rerun: observed bytes were `once`,
  `executed_again:false`, effects UNKNOWN.
- Plugin-qualified MCP input injection preserved the complete original input
  and supplied the actual host session ID without setting a permission decision.
- When down, lead edits were allowed with one warning per session.

These observations exercised the actual hook entry point and real filesystem
effects, **not Claude's native file-tool executor**. Host serializer behavior is
therefore not claimed as calibrated. All NotebookEdit modes remain explicitly
unsupported/unverified; no notebook receipt is granted.

The query/restart/sandbox observer exited **0**, and the daemon was stopped:

| Observation under /usr/bin/python3 | Measured request latency |
|---|---:|
| Coordinated: two units, held paths, UNKNOWN, own session unit | 2.691 ms |
| Coordinator down | 2.954 ms |
| Fresh uncoordinated checkout | 5.890 ms |
| Coordinated after restart (generation 2) | 2.891 ms |
| Registered repository identity changed | 2.982 ms |

Filesystem inventories before/after query matched. Query did not bootstrap,
register, bind calibration sockets or create SQLite/cache files. A sandbox
profile denied Unix-socket connectivity inside sandboxed Bash; invoking the
Python hook/query outside that sandbox still worked. Existing gate files were
not edited.

### The one paid real-model scenario

Actual MCP stdio, two simultaneous calls, scratch `real-pair-repo`, normal
user Codex configuration, gpt-6-astra/max. Observer exit **0**. No other paid
scenario was run.

| File | Durable run | Unit | Elapsed |
|---|---|---|---:|
| beta.txt | run_83fe62cd95c74bf1845b207d9037b665 | unit_c0160ff52edc403da8c5aa876cd5eb96 | 78.94 s |
| alpha.txt | run_af917cacccef408e9c277dbc40a5ac4d | unit_a2ab2111033142508adaa10f88ac4147 | 127.19 s |

Both returned a signed first-line status **ok**, completed two turns on one
native thread, used `apply_patch`, and read back the actual file with the
coordinator's `read_file`. Exact receipts:

- beta.txt: `rev_ea8b85a4624147de8ae1d54235dbc374`,
  actor `actor_9ba19b549dae43f285825ed718c84668`,
  thread `01a14fd4-e80a-7fb1-8d4a-e8f11a29ba05`.
- alpha.txt: `rev_7cce201ade31497b8ffe3b9ad023a996`,
  actor `actor_12bf613e76e24d28b9fc63049bc37098`,
  thread `01a14fd5-627f-7ca0-bb20-c3d254b898d4`.

Final bytes were `alpha ready\n` and `beta ready\n`; HEAD unchanged.
Each run had custody **[]**, UNKNOWN **0**, no active actors. Effective
compaction was **176800** from the deployed model registry, max reasoning,
detailed summaries and live web. Selected rollout turn_context fields verified
`approval_policy=never`, `sandbox=danger-full-access`,
`model=gpt-6-astra`, `effort=max` for all four turns. Stop succeeded.

The two real listeners both missed the cache. Comparing only recorded
fingerprint fields isolated the difference to exec-policy trace digests.
A further **no-inference**, disposable-home listener observation demonstrated
Rust HashMap debug-order variation for identical four-rule policies. That
command exited **0**, both listeners exited **0**, model requests **0**.
The adapter now normalizes only the measured policy's two unordered maps;
ordered rule lists/patterns and all policy values remain significant. Raw trace
digests remain observations. The final equivalent-policy/change-policy repeat
is recorded below.

### Explicit limits and deviations

- NotebookEdit: **OPEN/UNVERIFIED**, all modes refused with a complete Write
  alternative. The installed host's notebook serializer/generated-ID extension
  was not demonstrated, so support is not claimed.
- Actual Claude execution of Write/Edit/MultiEdit and actual host hook ordering:
  **UNVERIFIED**. Direct entry-point/pre/post/failure observations passed;
  byte mismatches refuse receipts rather than adopting host output.
- Linux end-to-end ownership and Windows execution: **UNVERIFIED**.
  Windows retains Abraham's explicit existing lock-lifetime refusal; it is not
  presented as a functioning fallback.
- Full daemon restart while a deliberately ambiguous, unrelated process remains
  alive was not separately exercised. Kernel identity persistence across
  reparenting/disappearance and real owned-survivor worker recovery were
  exercised; unprovable ownership still quarantines.
- Repository replacement specifically *inside* the prepared ref-transaction
  window was not injected. The check is implemented there; the measured
  replacement refusal above occurred before object/ref mutation.
- Large candidates beyond the OS argv limit were not exercised. The complete
  diff is supplied; there is no silent truncation. An exec startup limit would
  fail without recording a passing review.
- Most initial cache/lifecycle observations used empty/default fixture policies.
  The paid run exposed the nonempty-policy ordering case, leading to the final
  no-inference cache repair. No paid scenario was repeated.
- Several early observer scripts specified an invalid 180-second runtime
  override; the existing server correctly rejected that setting and used its
  normal 10800-second default. Those short runs were explicitly stopped.
  Corrected recovery/edge/cache drivers used supported budgets.
- The required order was followed for implementation. Additional fixes were
  driven by actual runs: inherited caller routing identities, quoted MCP config
  keys, intentional discovery disconnects, child subscriptions during recovery,
  helper-process races and unstable execution-policy map order.


### Exact operational commands for the continued observations

The commands below were run, not proposed. Error/retry limitations are described
above. Each command owns only its named scratch directories and processes.

<details>
<summary>MCP concurrent native writers, actual bound review and A publication while B remains active (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes,native
from coordinator.fixture import Provider,message
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU');repo=root/'mcp7-repo';repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q']);rows=[]
for name in ('a.txt','b.txt','c.txt'):
 (repo/name).write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');rows.append('100644 '+oid+'\t'+name+'\n')
gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=''.join(rows).encode());tree=gitio.text(repo,['write-tree'])
meta={'GIT_AUTHOR_NAME':'C2 scratch','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2 scratch','GIT_COMMITTER_EMAIL':'c2@invalid'}
base=gitio.text(repo,['commit-tree',tree],data=b'C2 baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
home=root/'mcp7-home';home.mkdir(mode=0o700);fixture=root/'mcp7-fixture';fixture.mkdir(mode=0o700)
state=root/'mcp7-state';runtime=root/'rt-mcp7';logs=root/'mcp7-logs'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(runtime),CODEX_ORACLE_LOG_DIR=str(logs),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='300',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')})
plan={'routes':{'PAR_A':[{'text':'Brief: edit only a.txt with apply_patch.'},{'tool':'send','arguments':{'recipient':'lead','text':'A has begun its implementation.','wake':'none'}},{'patch':'*** Begin Patch\n*** Update File: a.txt\n@@\n-baseline\n+PAR_A\n*** End Patch\n'},{'text':'Implemented a.txt.'}],'PAR_B':[{'text':'Brief: edit only b.txt with apply_patch.'},{'delay':90,'patch':'*** Begin Patch\n*** Update File: b.txt\n@@\n-baseline\n+PAR_B\n*** End Patch\n'},{'text':'Implemented b.txt.'}]}}
def router(body,index):
 if 'IMMUTABLE CANDIDATE BINDING' in json.dumps(body.get('input',[])):return message('**Verdict**: Ship it\n**Findings**: None. The object diff changes only a.txt.\n**Where I disagree with the caller\'s framing**: No unsupported framing.\n**Sources**: no external claims made.')
 users=[i for i in body.get('input',[]) if i.get('role')=='user']; latest=json.dumps(users[-1] if users else {})
 tag='PAR_A' if 'PAR_A' in latest else 'PAR_B';name='a.txt' if tag=='PAR_A' else 'b.txt'
 if 'PHASE 2 of 2' not in latest:return message('Brief: change only '+name+' with apply_patch.')
 calls=[i for i in body.get('input',[]) if i.get('type') in ('function_call','custom_tool_call')]
 if any(i.get('name')=='apply_patch' for i in calls):return message('Implemented '+name+'.')
 if tag=='PAR_A' and not any(i.get('name')=='send' for i in calls):return __import__('coordinator.fixture',fromlist=['function']).function('send',{'recipient':'lead','text':'A implementation message','wake':'none'})
 if tag=='PAR_B':time.sleep(60)
 return __import__('coordinator.fixture',fromlist=['patch']).patch('*** Begin Patch\n*** Update File: '+name+'\n@@\n-baseline\n+'+tag+'\n*** End Patch\n')
def plain(response):return '\n'.join(c.text for c in response.content if c.type=='text')
def decoded(text):return json.JSONDecoder().raw_decode(text)[0]
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 async with stdio_client(params,errlog=open(root/'mcp7-stderr.log','w')) as streams:
  async with ClientSession(*streams) as session:
   await session.initialize();print('CATALOG',[t.name for t in (await session.list_tools()).tools],flush=True)
   async def call(name,args):
    answer=plain(await session.call_tool(name,args));print(name,answer[:12000],flush=True);return answer
   a=asyncio.create_task(call('abraham',{'task':'PAR_A Change only a.txt','cwd':str(repo),'claims':['a.txt']}))
   b=asyncio.create_task(call('abraham',{'task':'PAR_B Change only b.txt','cwd':str(repo),'claims':['b.txt']}))
   answer=await a
   runs=await asyncio.to_thread(Client(state).call,'run/list',{'cwd':str(repo)})
   print('RUNS_AFTER_A',json.dumps(runs),flush=True)
   ra=next(r for r in runs['data'] if 'a.txt' in [c['path'] for c in r['claims']]);rb=next(r for r in runs['data'] if 'b.txt' in [c['path'] for c in r['claims']])
   await call('coordinator_claims',{'action':'acquire','paths':['a.txt'],'cwd':str(repo),'session_id':'c2-lead-mcp7'})
   inbox=decoded(await call('coordinator_inbox',{'cwd':str(repo)}))
   await call('coordinator_inbox',{'cwd':str(repo)})
   if inbox['data']:await call('coordinator_inbox',{'cwd':str(repo),'acknowledge':[inbox['data'][0]['id']]})
   staged=gitio.text(repo,['hash-object','-w','--stdin'],data=b'unrelated staged image\n');gitio.git(repo,['update-index','--index-info'],data=('100644 '+staged+'\tc.txt\n').encode())
   await call('coordinator_seal',{'unit':ra['unit'],'cwd':str(repo),'validation':[]})
   metadata={'message':'C2 fixture A publication','author_name':'C2 scratch','author_email':'c2@invalid','author_date':'2026-10-18T16:00:00Z','committer_name':'C2 scratch','committer_email':'c2@invalid','committer_date':'2026-10-18T16:00:00Z'}
   await call('coordinator_publish',{'unit':ra['unit'],'review':'missing-review','metadata':metadata,'cwd':str(repo)})
   await call('code_review',{'sealed_unit':ra['unit'],'cwd':str(repo),'session':'new','infra':True})
   jobs=await asyncio.to_thread(Client(state).call,'review/jobs',{'cwd':str(repo)})
   print('REVIEW_JOBS',json.dumps([{k:j.get(k) for k in ('id','run','state','verdict','review')} for j in jobs['data']]),flush=True)
   passing=next((j for j in jobs['data'] if j.get('review')),None)
   if passing:
    await call('coordinator_publish',{'unit':ra['unit'],'review':passing['review'],'metadata':metadata,'cwd':str(repo)})
    print('HEAD_CHANGED',gitio.text(repo,['rev-parse','HEAD'])!=base,'B_STILL_ACTIVE',(await asyncio.to_thread(Client(state).call,'run/status',{'run':rb['run']}))['state'],'STAGED_C',gitio.text(repo,['ls-files','--stage','--','c.txt']),'EXPECTED_C',staged,flush=True)
    print('PUBLISHED_PATHS',gitio.text(repo,['diff','--name-only',base,'HEAD']),flush=True)
   await b
   listeners=await asyncio.to_thread(Client(state).call,'records/list',{'kind':'listener'})
   print('WRITER_CATALOGS',[l.get('admission_observations',{}).get('writer_catalog') for l in listeners['data'] if l.get('admission')],flush=True)
with Provider(router=router,plan=plan) as provider:
 provider.config['mcp_servers.software-workflows']={'command':'/opt/homebrew/bin/python3.15','args':['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],'env':{k:env[k] for k in ('CLAUDE_PLUGIN_DATA','SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')}}
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  client=Client(state,timeout=45)
  for r in client.call('run/list',{'cwd':str(repo)})['data']:
   if r['state'] not in ('completed','cancelled','failed','refused','blocked'):print('CANCEL',client.call('run/interrupt',{'run':r['run']}),flush=True)
  print('STOP',client.call('daemon/stop'),flush=True);print('PROVIDER',len(provider.requests),provider.errors,flush=True);(root/'mcp7-provider-observations.json').write_text(json.dumps({'counts':provider.counts,'requests':provider.requests}))
PY
```

</details>

<details>
<summary>Python 3.9 lead hook lifecycle and query (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.15 -B - <<'PY'
import json,os,subprocess,time
from pathlib import Path
from coordinator import gitio
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU');repo=root/'hooks-repo';repo.mkdir(mode=0o700);state=root/'hooks-state';runtime=root/'rt-hooks'
os.environ.update(SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(runtime),CODEX_ORACLE_LOG_DIR=str(root/'hooks-logs'),TMPDIR=str(root))
gitio.git(repo,['init','-q']);rows=[]
for name in ('write.txt','edit.txt','multi.txt','claimed.txt','fail.txt','gap.txt','bash.txt','notebook.ipynb'):
 content=b'alpha beta\n';(repo/name).write_bytes(content);oid=gitio.text(repo,['hash-object','-w','--stdin'],data=content);rows.append('100644 '+oid+'\t'+name+'\n')
gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=''.join(rows).encode());tree=gitio.text(repo,['write-tree']);metadata={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=metadata);gitio.git(repo,['update-ref','HEAD',base])
client=Client(state);client.ensure(python='/opt/homebrew/bin/python3.15')
while not client.call('daemon/status')['ready']:time.sleep(.05)
checkout=client.call('checkout/register',{'path':str(repo)});first=client.call('lead/session',{'checkout':checkout['id'],'session_id':'host-one'});second=client.call('lead/session',{'checkout':checkout['id'],'session_id':'host-two'});client.call('claim/acquire',{'actor':second['actor'],'paths':['claimed.txt']})
hook='/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/hooks/coordinator_lead.py'
def invoke(event,tool,ident,data,**extra):
 payload={'hook_event_name':event,'tool_name':tool,'session_id':'host-one','tool_use_id':ident,'cwd':str(repo),'tool_input':data,'transcript_path':'/must-not-be-read',**extra}
 result=subprocess.run(['/usr/bin/python3','-B',hook],input=json.dumps(payload),text=True,capture_output=True,env=os.environ)
 print('HOOK',event,tool,ident,'exit',result.returncode,result.stdout.strip(),flush=True);return json.loads(result.stdout)
def filedata(name,**kw):return dict(file_path=str(repo/name),**kw)
try:
 invoke('PreToolUse','Write','conflict',filedata('claimed.txt',content='refused\n'))
 data=filedata('write.txt',content='written\n');invoke('PreToolUse','Write','write',data);(repo/'write.txt').write_bytes(b'written\n');invoke('PostToolUse','Write','write',data,tool_response={'success':True});invoke('PostToolUse','Write','write',data,tool_response={'success':True})
 data=filedata('edit.txt',old_string='alpha',new_string='gamma');invoke('PreToolUse','Edit','edit',data);(repo/'edit.txt').write_bytes(b'gamma beta\n');invoke('PostToolUse','Edit','edit',data)
 data=filedata('multi.txt',edits=[{'old_string':'alpha','new_string':'gamma'},{'old_string':'gamma beta','new_string':'delta'}]);invoke('PreToolUse','MultiEdit','multi',data);(repo/'multi.txt').write_bytes(b'delta\n');invoke('PostToolUse','MultiEdit','multi',data)
 data=filedata('fail.txt',content='intended\n');invoke('PreToolUse','Write','failure',data);(repo/'fail.txt').write_bytes(b'partial\n');invoke('PostToolUseFailure','Write','failure',data);(repo/'fail.txt').write_bytes(b'intended\n');invoke('PostToolUse','Write','failure',data)
 data=filedata('gap.txt',content='gap\n');invoke('PreToolUse','Write','gap',data);(repo/'gap.txt').write_bytes(b'gap\n')
 invoke('PreToolUse','NotebookEdit','notebook',{'notebook_path':str(repo/'notebook.ipynb'),'edit_mode':'insert','new_source':'x=1','cell_type':'code'})
 data={'command':'printf once >> bash.txt'};invoke('PreToolUse','Bash','bash',data);(repo/'bash.txt').write_bytes(b'once');invoke('PostToolUse','Bash','bash',data);print('BASH_BYTES',repr((repo/'bash.txt').read_bytes()),flush=True)
 invoke('PreToolUse','mcp__plugin_software-workflows_codex-oracle__abraham','injection',{'task':'keep me','claims':['x'],'allow_dirty':True})
 query=['/usr/bin/python3','-B','-m','coordinator.query',str(repo),'--session-id','host-one','--state',str(state)]
 def inventory():return {str(p):[p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns] for directory in (state,runtime) for p in directory.rglob('*') if p.is_file()}
 before=inventory();q=subprocess.run(query,env=os.environ,capture_output=True,text=True);after=inventory();print('QUERY',q.returncode,q.stdout.strip(),'FILES_CHANGED',sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k)),flush=True)
 q=subprocess.run(query[:5]+[str(root)]+query[6:],env=os.environ,capture_output=True,text=True);print('UNCOORDINATED',q.returncode,q.stdout.strip(),flush=True)
 q=subprocess.run(['/usr/bin/sandbox-exec','-p','(version 1) (allow default) (deny network-outbound (remote unix-socket))',*query],env=os.environ,capture_output=True,text=True);print('SANDBOX_QUERY',q.returncode,q.stdout.strip(),q.stderr.strip()[:400],flush=True)
 print('LEAD_HOOK_RECORDS',[(r['tool'],r['state']) for r in client.call('records/list',{'kind':'lead_hook'})['data']],flush=True)
 print('STOP',client.call('daemon/stop'),flush=True)
 time.sleep(.2);q=subprocess.run(query,env=os.environ,capture_output=True,text=True);print('DOWN_QUERY',q.returncode,q.stdout.strip(),flush=True)
 invoke('PreToolUse','Write','down-one',filedata('down.txt',content='x'));invoke('PreToolUse','Write','down-two',filedata('down.txt',content='x'))
finally:
 try:print('FINAL_STOP',client.call('daemon/stop'),flush=True)
 except (FileNotFoundError,ConnectionRefusedError):pass
PY
```

</details>

<details>
<summary>Restart, sandbox and identity query (exit 0)</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.15 -B - <<'PY'
import os,subprocess,time,json
from pathlib import Path
from coordinator.client import Client
from coordinator import gitio
root=Path('/private/tmp/software-workflows-c2-wrGSzU');repo=root/'hooks-repo';state=root/'hooks-state';os.environ.update(SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-hooks'),CODEX_ORACLE_LOG_DIR=str(root/'hooks-logs'),TMPDIR=str(root))
c=Client(state);c.ensure(python='/opt/homebrew/bin/python3.15')
while not c.call('daemon/status')['ready']:time.sleep(.05)
try:
 print('RECOVERED_HOOK_GAP',[(r['tool'],r['state'],r.get('operation')) for r in c.call('records/list',{'kind':'lead_hook'})['data']],flush=True)
 for directory in (root,repo):
  q=subprocess.run(['/usr/bin/python3','-B','-m','coordinator.query',str(directory),'--session-id','host-one'],env=os.environ,capture_output=True,text=True);value=json.loads(q.stdout);print('QUERY',q.returncode,{k:value[k] for k in ('status','registered','own_unit','elapsed_ms')},flush=True)
 gate=root/'gate-state';gate.mkdir(mode=0o700,exist_ok=True)
 profile='(version 1) (allow default) (deny file-write* (subpath "'+str(state)+'") (subpath "'+str(gate)+'")) (deny network-outbound (remote unix-socket))'
 q=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,'/usr/bin/python3','-B','-c','import pathlib;pathlib.Path('+repr(str(gate/'blocked'))+').write_text("x")'],capture_output=True,text=True);print('SANDBOX_WRITE',q.returncode,q.stderr.splitlines()[-1],flush=True)
 q=subprocess.run(['/usr/bin/python3','-B','-m','coordinator.query',str(repo),'--session-id','host-one'],env=os.environ,capture_output=True,text=True);print('OUTSIDE_SANDBOX_QUERY',q.returncode,json.loads(q.stdout)['status'],flush=True)
 # Replace only this disposable checkout directory, retaining its original data.
 moved=root/'hooks-repo-original';repo.rename(moved);repo.mkdir(mode=0o700);gitio.git(repo,['init','-q'])
 q=subprocess.run(['/usr/bin/python3','-B','-m','coordinator.query',str(repo),'--session-id','host-one'],env=os.environ,capture_output=True,text=True);print('REPLACED_QUERY',q.returncode,q.stdout.strip(),flush=True)
 first=next(r for r in c.call('records/list',{'kind':'lead_session'})['data'] if r['session_id']=='host-one')
 before=sorted(str(p.relative_to(repo)) for p in (repo/'.git/objects').rglob('*'))
 try:c.call('unit/seal',{'unit':first['unit'],'validation':[]})
 except Exception as e:print('REPLACED_SEAL_REFUSAL',getattr(e,'code',type(e).__name__),flush=True)
 after=sorted(str(p.relative_to(repo)) for p in (repo/'.git/objects').rglob('*'));print('REPLACEMENT_OBJECTS_UNCHANGED',before==after,flush=True)
finally:print('STOP',c.call('daemon/stop'),flush=True)
PY
```

</details>

<details>
<summary>MCP kill, worker kill, survivor cancellation and independent writer completion (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,fcntl,json,os,signal,time,shlex
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes
from coordinator.fixture import Provider,function,message,patch
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU');repo=root/'recovery2-repo';repo.mkdir(mode=0o700);dispatch=root/'recovery2-dispatch';dispatch.mkdir(mode=0o700)
gitio.git(repo,['init','-q']);rows=[]
for name in ('c.txt','d.txt'):
 (repo/name).write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');rows.append('100644 '+oid+'\t'+name+'\n')
gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=''.join(rows).encode());tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
home=root/'recovery2-home';home.mkdir(mode=0o700);fixture=root/'recovery2-fixture';fixture.mkdir(mode=0o700);state=root/'recovery2-state';logs=root/'recovery2-logs'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-recovery2'),CODEX_ORACLE_LOG_DIR=str(logs),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='300',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
from coordinator import compat
command=shlex.join(['/opt/homebrew/bin/python3.15','-B','-c','import subprocess,time;p=subprocess.Popen(["/bin/sleep","80"],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid,flush=True);time.sleep(1)'])
def router(body,index):
 data=body.get('input',[]);calls=[i for i in data if i.get('type') in ('function_call','custom_tool_call')];users=[i for i in data if i.get('role')=='user'];last=json.dumps(users[-1] if users else {})
 child='C2_RECOVERY_CHILD' in json.dumps(data) and not any(i.get('name')=='spawn_agent' for i in calls)
 if child:
  if not any(i.get('name')=='exec_command' for i in calls):return function('exec_command',{'cmd':command,'login':False,'yield_time_ms':1000})
  time.sleep(65);return message('Child finished.')
 if 'PHASE 2 of 2' not in last:return message('Brief: perform only the requested task and leave recording to the lead.')
 if 'RECOVER_C' in last:
  if not any(i.get('name')=='spawn_agent' for i in calls):return function('spawn_agent',{'task_name':'survivor','message':'C2_RECOVERY_CHILD Run the specified ordinary system command.','fork_turns':'none'},'collaboration')
  return message('Root finished; coordinator must wait for child custody.')
 if any(i.get('name')=='apply_patch' for i in calls):return message('Implemented d.txt.')
 time.sleep(90);return patch('*** Begin Patch\n*** Update File: d.txt\n@@\n-baseline\n+RECOVER_D\n*** End Patch\n')
def parameters(cwd):return StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(cwd))
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
client=Client(state,timeout=40);selected={}
async def rpc(method,params=None):return await asyncio.to_thread(client.call,method,params)
async def launch():
 try:
  async with stdio_client(parameters(dispatch),errlog=open(root/'recovery2-mcp-stderr.log','w')) as streams:
   async with ClientSession(*streams) as s:
    await s.initialize();tasks=[asyncio.create_task(s.call_tool('abraham',{'task':tag,'cwd':str(repo),'claims':[name]})) for tag,name in [('RECOVER_C','c.txt'),('RECOVER_D','d.txt')]]
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
     await asyncio.sleep(.3)
     try:runs=(await rpc('run/list',{'cwd':str(repo)}))['data']
     except (FileNotFoundError,ConnectionRefusedError):continue
     for r in runs:
      for claim in r['claims']:selected[claim['path']]=r['run']
     if 'c.txt' in selected and 'd.txt' in selected:
      c=next(r for r in runs if r['run']==selected['c.txt']);d=next(r for r in runs if r['run']==selected['d.txt'])
      if any(a['kind']=='child' and a.get('active_turn') for a in c['actors']) and any(x.get('processes') for x in c['custody']) and d['phase']=='implementation':
       print('BEFORE_CRASH',json.dumps({'C':c,'D':d}),flush=True);break
    else:raise RuntimeError('Child custody did not become observable')
    status=await rpc('daemon/status');supervisor=processes.identity(status['pid'])['ppid'];records=[json.loads(p.read_text()) for p in (logs/'servers').glob('*.json')];live=[r for r in records if r['pid'] != supervisor and processes.identity(r['pid'])]
    if len(live)!=1:raise RuntimeError('Server identity is ambiguous')
    pid=live[0]['pid'];identity=processes.identity(pid)
    if processes.identity(pid)!=identity:raise RuntimeError('Server changed identity')
    os.kill(pid,signal.SIGKILL);print('KILLED_MCP',pid,identity['birth'],flush=True)
    await asyncio.gather(*tasks,return_exceptions=True)
 except BaseException as e:print('EXPECTED_MCP_DISCONNECT',type(e).__name__,flush=True)
async def reconnect(cwd,cancel=False):
 async with stdio_client(parameters(cwd),errlog=open(root/('reconnect-'+cwd.name+'.log'),'w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize();listing=plain(await s.call_tool('codex_runs',{}));print('RECONNECTED_LIST',str(cwd),listing[:8000],flush=True)
   if cancel:
    answer=plain(await s.call_tool('codex_cancel_run',{'run':selected['c.txt']}));print('CANCEL_RESULT',answer[:10000],flush=True)
    print('OTHER_AFTER_CANCEL',(await rpc('run/status',{'run':selected['d.txt']}))['state'],flush=True)
   else:
    answer=plain(await s.call_tool('codex_resume_run',{'run':selected['d.txt']}));print('COLLECT_D',answer[:4000],flush=True)
async def main():
 await launch()
 with open(root/'plugin-data/venv/91e1e736a04fad0e/.in-use','rb') as fd:
  try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);print('VENV_PROTECTION_MISSING',flush=True);fcntl.flock(fd,fcntl.LOCK_UN)
  except BlockingIOError:print('VENV_HELD_AFTER_MCP_EXIT',flush=True)
 status=await rpc('daemon/status');identity=processes.identity(status['pid'])
 if identity['birth']!=status['birth']:raise RuntimeError('Worker identity changed')
 os.kill(status['pid'],signal.SIGKILL);print('KILLED_WORKER',status['pid'],status['birth'],flush=True)
 lock,reason=compat._acquire_write_lock(str(repo),'competing-exec');print('EXEC_DURING_WORKER_CRASH',bool(lock),reason,flush=True)
 if lock:compat._release_write_lock(lock)
 deadline=time.monotonic()+60
 while time.monotonic()<deadline:
  try:
   current=await rpc('daemon/status')
   if current['generation']>status['generation'] and current['ready']:print('RECOVERED',current['pid'],current['generation'],flush=True);break
  except (FileNotFoundError,ConnectionRefusedError,ConnectionResetError):pass
  await asyncio.sleep(.2)
 else:raise RuntimeError('Replacement worker did not recover')
 await reconnect(dispatch,True)
 await reconnect(repo)
 deadline=time.monotonic()+90
 while time.monotonic()<deadline:
  d=await rpc('run/status',{'run':selected['d.txt']})
  if d['state'] in ('completed','failed','blocked','cancelled'):break
  await asyncio.sleep(.3)
 await reconnect(repo)
 print('FINAL_RUNS',json.dumps(await rpc('run/list',{'cwd':str(repo)})),flush=True)
 print('FINAL_FILES',(repo/'c.txt').read_text(),(repo/'d.txt').read_text(),flush=True)
 print('PHASE_INPUTS',{tag:{p:{k:v.get(k) for k in ('input_id','turn','input_observed','state')} for p,v in client.call('run/status',{'run':rid})['phases'].items()} for tag,rid in selected.items()},flush=True)
with Provider(router=router) as provider:
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 try:asyncio.run(main())
 finally:
  try:
   for r in client.call('run/list',{'cwd':str(repo)})['data']:
    if r['state'] not in ('completed','cancelled','failed','refused','blocked'):print('CLEANUP_CANCEL',client.call('run/interrupt',{'run':r['run']}),flush=True)
   print('STOP',client.call('daemon/stop'),flush=True)
  except Exception as e:print('CLEANUP_REQUIRED',type(e).__name__,str(e),flush=True)
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
PY
```

</details>

<details>
<summary>Images, compaction, analysis denial, peer/lead messages and opaque effects (exit 0; initial process quarantine retained)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,base64,json,os,re,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes
from coordinator.fixture import Provider,function,message,patch
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU')
repos={}
meta={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'}
for tag,names in [('messages',['a.txt','b.txt']),('opaque',['x.txt'])]:
 repo=root/('edge-'+tag);repo.mkdir(mode=0o700);repos[tag]=repo;gitio.git(repo,['init','-q']);rows=[]
 for name in names:
  (repo/name).write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');rows.append('100644 '+oid+'\t'+name+'\n')
 gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=''.join(rows).encode());tree=gitio.text(repo,['write-tree']);base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
image=root/'edge-image.png';image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS0AAAAASUVORK5CYII='))
home=root/'edge-home';home.mkdir(mode=0o700);fixture=root/'edge-fixture';fixture.mkdir(mode=0o700);state=root/'edge-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-edge'),CODEX_ORACLE_LOG_DIR=str(root/'edge-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_CONTEXT_WINDOW='200000',CODEX_ORACLE_MAX_RUNTIME_S='180',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
client=Client(state,timeout=60);selected={};routes=[]
def router(body,index):
 data=body.get('input',[]);marks=[(i,json.dumps(x)) for i,x in enumerate(data) if x.get('role')=='user' and 'PHASE ' in json.dumps(x)]
 if not marks:return message('done')
 at,prompt=marks[-1];impl='PHASE 2 of 2' in prompt;calls=[x for x in data[at+1:] if x.get('type') in ('function_call','custom_tool_call')];names=[x.get('name') for x in calls]
 tag=next((tag for tag in ('PATCH_DENY','PEER_B','OPAQUE_ANALYSIS') if tag in prompt),'unknown');routes.append([tag,impl,names])
 ids=re.findall(r'C1_MESSAGE (msg_[0-9a-f]{32})',json.dumps(data[at+1:]))
 for ident in reversed(ids):
  if not any(x.get('name')=='ack' and ident in x.get('arguments','') for x in calls):return function('ack',{'message':ident})
 if tag=='OPAQUE_ANALYSIS':
  if 'exec_command' not in names:return function('exec_command',{'cmd':"printf opaque > x.txt",'login':False,'yield_time_ms':1000})
  return message('Analysis complete after opaque command.')
 if not impl:
  if tag=='PATCH_DENY' and 'apply_patch' not in names:return patch('*** Begin Patch\n*** Update File: a.txt\n@@\n-baseline\n+FORBIDDEN_ANALYSIS\n*** End Patch\n')
  return message('Brief: change only the claimed file with apply_patch. The lead records.')
 if tag=='PATCH_DENY':
  if not any(x.get('name')=='send' and 'peer hello' in x.get('arguments','') for x in calls):
   deadline=time.monotonic()+20
   while 'b.txt' not in selected and time.monotonic()<deadline:time.sleep(.05)
   return function('send',{'recipient':selected['b.txt'],'text':'peer hello','wake':'none'})
  if not any(x.get('name')=='send' and 'lead hello' in x.get('arguments','') for x in calls):return function('send',{'recipient':'lead','text':'lead hello','wake':'none'})
  if 'apply_patch' not in names:return patch('*** Begin Patch\n*** Update File: a.txt\n@@\n-baseline\n+PATCH_DENY_IMPLEMENTED\n*** End Patch\n')
  return message('Implemented a.txt only.')
 if 'apply_patch' not in names:
  if not ids:time.sleep(8)
  return patch('*** Begin Patch\n*** Update File: b.txt\n@@\n-baseline\n+PEER_B_IMPLEMENTED\n*** End Patch\n')
 return message('Implemented b.txt only.')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def rpc(method,params=None):return await asyncio.to_thread(client.call,method,params)
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repos['messages']))
 async with stdio_client(params,errlog=open(root/'edge-mcp-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   tasks=[asyncio.create_task(s.call_tool('abraham',{'task':tag,'cwd':str(repos['messages']),'claims':[name],'images':[str(image)]})) for tag,name in [('PATCH_DENY','a.txt'),('PEER_B','b.txt')]]
   while not all(t.done() for t in tasks):
    try:
     for r in (await rpc('run/list',{'cwd':str(repos['messages'])}))['data']:
      for c in r['claims']:selected[c['path']]=r['run']
    except (OSError,RuntimeError):pass
    await asyncio.sleep(.2)
   for tag,result in zip(('PATCH_DENY','PEER_B'),await asyncio.gather(*tasks)):print('RESULT',tag,plain(result),flush=True)
   print('INBOX',plain(await s.call_tool('coordinator_inbox',{})),flush=True)
   for rid in selected.values():
    run=await rpc('run/status',{'run':rid});print('PHASES',rid,{p:{k:v.get(k) for k in ('state','success','input_id','turn')} for p,v in run['phases'].items()},flush=True)
    print('IMAGES',[[x['type'] for x in p['input']] for p in run['phases'].values()],flush=True)
    first=await rpc('run/result',{'run':rid,'limit':40});second=await rpc('run/result',{'run':rid,'cursor':first['next_cursor'],'limit':40});print('CURSORS',first['next_cursor'],second['cursor'],second['next_cursor'],flush=True)
    print('LOG',plain(await s.call_tool('codex_run_log',{'run':rid,'cursor':0}))[:1400],flush=True)
   print('OPAQUE',plain(await s.call_tool('abraham',{'task':'OPAQUE_ANALYSIS','cwd':str(repos['opaque']),'claims':['x.txt']})),flush=True)
   allruns=await rpc('run/list',{'cwd':str(root)})
   for r in allruns['data']:
    run=await rpc('run/status',{'run':r['run']});print('FINAL',run['task'],run['state'],list(run.get('phases',{})),r['unknown_count'],flush=True)
   print('MESSAGE_RECORDS',[(m['sender'],m['recipient'],m['text'],m['state'],m['delivery']) for m in (await rpc('records/list',{'kind':'message'}))['data']],flush=True)
   print('ANALYSIS_DENIALS',[e for rid in selected.values() for e in (await rpc('run/events',{'run':rid,'limit':400}))['data'] if e['kind']=='workflow/analysis_patch_denied'],flush=True)
with Provider(router=router) as provider:
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 try:asyncio.run(main())
 finally:
  try:
   for r in client.call('run/list',{'cwd':str(root)})['data']:
    if r['state'] not in ('completed','cancelled','failed','refused','blocked'):print('CLEANUP_CANCEL',client.call('run/interrupt',{'run':r['run']})['state'],flush=True)
   print('STOP',client.call('daemon/stop'),flush=True)
  except Exception as exc:print('CLEANUP_REQUIRED',type(exc).__name__,str(exc),flush=True)
  print('PROVIDER',len(provider.requests),provider.errors,'ROUTES',routes,flush=True)
PY
```

</details>

<details>
<summary>Corrected repeated short commands and UNKNOWN resume refusal (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,base64,json,os,re,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes
from coordinator.fixture import Provider,function,message,patch
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU')
repos={}
meta={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'}
for tag,names in [('messages',['a.txt','b.txt']),('opaque',['x.txt'])]:
 repo=root/('edge3-'+tag);repo.mkdir(mode=0o700);repos[tag]=repo;gitio.git(repo,['init','-q']);rows=[]
 for name in names:
  (repo/name).write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');rows.append('100644 '+oid+'\t'+name+'\n')
 gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=''.join(rows).encode());tree=gitio.text(repo,['write-tree']);base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
image=root/'edge3-image.png';image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS0AAAAASUVORK5CYII='))
home=root/'edge3-home';home.mkdir(mode=0o700);fixture=root/'edge3-fixture';fixture.mkdir(mode=0o700);state=root/'edge3-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-edge3'),CODEX_ORACLE_LOG_DIR=str(root/'edge3-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_CONTEXT_WINDOW='200000',CODEX_ORACLE_MAX_RUNTIME_S='300',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
client=Client(state,timeout=60);selected={};routes=[]
def router(body,index):
 data=body.get('input',[]);marks=[(i,json.dumps(x)) for i,x in enumerate(data) if x.get('role')=='user' and 'PHASE ' in json.dumps(x)]
 if not marks:return message('done')
 at,prompt=marks[-1];impl='PHASE 2 of 2' in prompt;calls=[x for x in data[at+1:] if x.get('type') in ('function_call','custom_tool_call')];names=[x.get('name') for x in calls]
 tag=next((tag for tag in ('PATCH_DENY','PEER_B','OPAQUE_ANALYSIS') if tag in prompt),'unknown');routes.append([tag,impl,names])
 ids=re.findall(r'C1_MESSAGE (msg_[0-9a-f]{32})',json.dumps(data[at+1:]))
 for ident in reversed(ids):
  if not any(x.get('name')=='ack' and ident in x.get('arguments','') for x in calls):return function('ack',{'message':ident})
 if tag=='OPAQUE_ANALYSIS':
  if names.count('exec_command') < 10:return function('exec_command',{'cmd':"printf opaque > x.txt" if names.count('exec_command') == 9 else '/usr/bin/true','login':False,'yield_time_ms':1000})
  return message('Analysis complete after opaque command.')
 if not impl:
  if tag=='PATCH_DENY' and 'apply_patch' not in names:return patch('*** Begin Patch\n*** Update File: a.txt\n@@\n-baseline\n+FORBIDDEN_ANALYSIS\n*** End Patch\n')
  return message('Brief: change only the claimed file with apply_patch. The lead records.')
 if tag=='PATCH_DENY':
  if not any(x.get('name')=='send' and 'peer hello' in x.get('arguments','') for x in calls):
   deadline=time.monotonic()+20
   while 'b.txt' not in selected and time.monotonic()<deadline:time.sleep(.05)
   return function('send',{'recipient':selected['b.txt'],'text':'peer hello','wake':'none'})
  if not any(x.get('name')=='send' and 'lead hello' in x.get('arguments','') for x in calls):return function('send',{'recipient':'lead','text':'lead hello','wake':'none'})
  if 'apply_patch' not in names:return patch('*** Begin Patch\n*** Update File: a.txt\n@@\n-baseline\n+PATCH_DENY_IMPLEMENTED\n*** End Patch\n')
  return message('Implemented a.txt only.')
 if 'apply_patch' not in names:
  if not ids:time.sleep(8)
  return patch('*** Begin Patch\n*** Update File: b.txt\n@@\n-baseline\n+PEER_B_IMPLEMENTED\n*** End Patch\n')
 return message('Implemented b.txt only.')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def rpc(method,params=None):return await asyncio.to_thread(client.call,method,params)
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repos['opaque']))
 async with stdio_client(params,errlog=open(root/'edge3-mcp-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   print('OPAQUE',plain(await s.call_tool('abraham',{'task':'OPAQUE_ANALYSIS','claims':['x.txt']})),flush=True)
   runs=(await rpc('run/list',{'cwd':str(repos['opaque'])}))['data'];r=runs[0]
   print('FINAL',r['state'],r['custody'],r['unknown_count'],flush=True)
   print('RESUME_UNKNOWN',plain(await s.call_tool('codex_resume_run',{'run':r['run'],'nudge':'Continue after unknown'})),flush=True)
   print('OPERATIONS',[(o['kind'],o['state'],o.get('completed_item',{}).get('exitCode')) for o in (await rpc('records/list',{'kind':'operation'}))['data']],flush=True)
with Provider(router=router) as provider:
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 try:asyncio.run(main())
 finally:
  try:
   for r in client.call('run/list',{'cwd':str(root)})['data']:
    if r['state'] not in ('completed','cancelled','failed','refused','blocked'):print('CLEANUP_CANCEL',client.call('run/interrupt',{'run':r['run']})['state'],flush=True)
   print('STOP',client.call('daemon/stop'),flush=True)
  except Exception as exc:print('CLEANUP_REQUIRED',type(exc).__name__,str(exc),flush=True)
  print('PROVIDER',len(provider.requests),provider.errors,'ROUTES',routes,flush=True)
PY
```

</details>

<details>
<summary>Explicit cleanup after failed short-command observation (exit 0)</summary>

```sh
SOFTWARE_WORKFLOWS_STATE=/private/tmp/software-workflows-c2-wrGSzU/edge2-state SOFTWARE_WORKFLOWS_RUNTIME=/private/tmp/software-workflows-c2-wrGSzU/rt-edge2 PYTHONPATH=plugins/software-workflows /opt/homebrew/bin/python3.15 -B - <<'PY'
from coordinator.client import Client
from pathlib import Path
c=Client(Path('/private/tmp/software-workflows-c2-wrGSzU/edge2-state'),timeout=45)
for r in c.call('run/list',{'cwd':'/private/tmp/software-workflows-c2-wrGSzU/edge2-opaque'})['data']:
 print('BEFORE',r['state'],r['actors'])
 for held in r['custody']:
  try:print('RECONCILE',c.call('custody/reconcile',{'custody':held['id']})['state'])
  except Exception as exc:print('RECONCILE_REFUSAL',getattr(exc,'code',type(exc).__name__),str(exc))
 print('CANCEL',c.call('run/interrupt',{'run':r['run']})['state'])
print('STOP',c.call('daemon/stop'))
PY
```

</details>

<details>
<summary>Negative/quoted/conflicting reviews, stale seal/parent and UNKNOWN refusals (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,base64,json,os,shutil,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,native
from coordinator.fixture import Provider,message
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU');state=root/'refusal3-state';home=root/'refusal3-home';home.mkdir(mode=0o700)
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-refusal3'),CODEX_ORACLE_LOG_DIR=str(root/'refusal3-logs'),CODEX_HOME=str(home),CODEX_ORACLE_MAX_RUNTIME_S='180',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
client=Client(state,timeout=40);meta={'message':'C2 verified publication\n','author_name':'C2','author_email':'c2@invalid','author_date':'2026-10-18T16:00:00+00:00','committer_name':'C2','committer_email':'c2@invalid','committer_date':'2026-10-18T16:00:00+00:00'}
gitmeta={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'}
def router(body,index):
 text=json.dumps([x for x in body.get('input',[]) if x.get('role')=='user'][-1:])
 if 'NEGATIVE_REVIEW' in text:return message('**Verdict**: Needs changes\n\nThe candidate needs a correction.')
 if 'QUOTED_REVIEW' in text:return message('> Verdict: Ship it\n\nNo own verdict is provided.')
 if 'CONFLICTING_REVIEW' in text:return message('Verdict: Ship it\n\nVerdict: Do not ship')
 return message('**Verdict**: Ship it\n\nThe complete bound candidate is acceptable.')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def rpc(method,params=None):return await asyncio.to_thread(client.call,method,params)
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(root))
 async with stdio_client(params,errlog=open(root/'refusal3-mcp-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   for case in ('unknown','stale','identity'):
    repo=root/('refusal3-'+case);repo.mkdir(mode=0o700);gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n')
    oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=('100644 '+oid+'\ta.txt\n').encode());tree=gitio.text(repo,['write-tree']);base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=gitmeta);gitio.git(repo,['update-ref','HEAD',base])
    print('ATTACH',case,plain(await s.call_tool('coordinator_claims',{'action':'acquire','paths':['a.txt'],'cwd':str(repo),'session_id':'refusal3-'+case})),flush=True)
    checkout=await rpc('checkout/register',{'path':str(repo)});lead=await rpc('lead/session',{'checkout':checkout['id'],'session_id':'refusal3-'+case});unit=lead['unit']
    await rpc('lead-edit',{'actor':lead['actor'],'changes':{'a.txt':{'bytes_base64':base64.b64encode(b'candidate\n').decode(),'mode':'100644'}}})
    print('SEAL',case,plain(await s.call_tool('coordinator_seal',{'unit':unit,'cwd':str(repo)})),flush=True)
    if case=='unknown':
     for focus in ('NEGATIVE_REVIEW','QUOTED_REVIEW','CONFLICTING_REVIEW'):
      answer=plain(await s.call_tool('code_review',{'sealed_unit':unit,'cwd':str(repo),'focus':focus,'infra':True}));print('REVIEW',focus,answer[-2400:],flush=True)
      jobs=(await rpc('records/list',{'kind':'review_job'}))['data'];job=next(j for j in reversed(jobs) if j['unit']==unit and j['completion']['answer'] in answer)
      print('NEGATIVE_RECORD',job['state'],job['verdict'],'passing',bool(job.get('review')),flush=True)
      print('NEGATIVE_PUBLISH',plain(await s.call_tool('coordinator_publish',{'unit':unit,'review':job['id'],'metadata':meta,'cwd':str(repo)})),flush=True)
    answer=plain(await s.call_tool('code_review',{'sealed_unit':unit,'cwd':str(repo),'focus':'BOUND_GOOD_REVIEW','infra':True}));print('POSITIVE_REVIEW',case,answer[-1800:],flush=True)
    jobs=(await rpc('records/list',{'kind':'review_job'}))['data'];review=next(j['review'] for j in jobs if j['unit']==unit and j['state']=='recorded')
    if case=='unknown':
     (repo/'a.txt').write_text('unexplained\n')
    elif case=='stale':
     print('REOPEN',plain(await s.call_tool('coordinator_unit',{'action':'reopen','unit':unit,'cwd':str(repo)})),flush=True)
     print('RESEAL',plain(await s.call_tool('coordinator_seal',{'unit':unit,'cwd':str(repo)}))[:400],flush=True)
     print('STALE_SEAL',plain(await s.call_tool('coordinator_publish',{'unit':unit,'review':review,'metadata':meta,'cwd':str(repo)})),flush=True)
     await s.call_tool('code_review',{'sealed_unit':unit,'cwd':str(repo),'focus':'BOUND_GOOD_REVIEW','infra':True})
     jobs=(await rpc('records/list',{'kind':'review_job'}))['data'];current=(await rpc('unit/diff',{'unit':unit}))['binding'];review=next(j['review'] for j in jobs if j['unit']==unit and j['state']=='recorded' and j['binding']==current)
     advanced=gitio.text(repo,['commit-tree',tree,'-p',base],data=b'unrelated parent advance\n',extra_env=gitmeta);gitio.git(repo,['update-ref','HEAD',advanced,base])
    else:
     original=root/'refusal3-identity-original-git';os.rename(repo/'.git',original);shutil.copytree(original,repo/'.git')
    before=gitio.text(repo,['rev-parse','HEAD']);objects_before=sorted(str(p.relative_to(repo/'.git/objects')) for p in (repo/'.git/objects').rglob('*') if p.is_file())
    print('REFUSAL',case,plain(await s.call_tool('coordinator_publish',{'unit':unit,'review':review,'metadata':meta,'cwd':str(repo)})),flush=True)
    print('UNCHANGED',case,before==gitio.text(repo,['rev-parse','HEAD']),objects_before==sorted(str(p.relative_to(repo/'.git/objects')) for p in (repo/'.git/objects').rglob('*') if p.is_file()),flush=True)
with Provider(router=router) as provider:
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  try:print('STOP',client.call('daemon/stop'),flush=True)
  except Exception as exc:print('CLEANUP_REQUIRED',type(exc).__name__,str(exc),flush=True)
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
PY
```

</details>

<details>
<summary>Direct publication identity refusal (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,base64,json,os,shutil,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,native
from coordinator.fixture import Provider,message
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU');state=root/'refusal4-state';home=root/'refusal4-home';home.mkdir(mode=0o700)
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-refusal4'),CODEX_ORACLE_LOG_DIR=str(root/'refusal4-logs'),CODEX_HOME=str(home),CODEX_ORACLE_MAX_RUNTIME_S='300',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
client=Client(state,timeout=40);meta={'message':'C2 verified publication\n','author_name':'C2','author_email':'c2@invalid','author_date':'2026-10-18T16:00:00+00:00','committer_name':'C2','committer_email':'c2@invalid','committer_date':'2026-10-18T16:00:00+00:00'}
gitmeta={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'}
def router(body,index):
 text=json.dumps([x for x in body.get('input',[]) if x.get('role')=='user'][-1:])
 if 'NEGATIVE_REVIEW' in text:return message('**Verdict**: Needs changes\n\nThe candidate needs a correction.')
 if 'QUOTED_REVIEW' in text:return message('> Verdict: Ship it\n\nNo own verdict is provided.')
 if 'CONFLICTING_REVIEW' in text:return message('Verdict: Ship it\n\nVerdict: Do not ship')
 return message('**Verdict**: Ship it\n\nThe complete bound candidate is acceptable.')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def rpc(method,params=None):return await asyncio.to_thread(client.call,method,params)
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(root))
 async with stdio_client(params,errlog=open(root/'refusal4-mcp-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   for case in ('identity',):
    repo=root/('refusal4-'+case);repo.mkdir(mode=0o700);gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n')
    oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=('100644 '+oid+'\ta.txt\n').encode());tree=gitio.text(repo,['write-tree']);base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=gitmeta);gitio.git(repo,['update-ref','HEAD',base])
    print('ATTACH',case,plain(await s.call_tool('coordinator_claims',{'action':'acquire','paths':['a.txt'],'cwd':str(repo),'session_id':'refusal4-'+case})),flush=True)
    checkout=await rpc('checkout/register',{'path':str(repo)});lead=await rpc('lead/session',{'checkout':checkout['id'],'session_id':'refusal4-'+case});unit=lead['unit']
    await rpc('lead-edit',{'actor':lead['actor'],'changes':{'a.txt':{'bytes_base64':base64.b64encode(b'candidate\n').decode(),'mode':'100644'}}})
    print('SEAL',case,plain(await s.call_tool('coordinator_seal',{'unit':unit,'cwd':str(repo)})),flush=True)
    if case=='unknown':
     for focus in ('NEGATIVE_REVIEW','QUOTED_REVIEW','CONFLICTING_REVIEW'):
      answer=plain(await s.call_tool('code_review',{'sealed_unit':unit,'cwd':str(repo),'focus':focus,'infra':True}));print('REVIEW',focus,answer[-2400:],flush=True)
      jobs=(await rpc('records/list',{'kind':'review_job'}))['data'];job=next(j for j in reversed(jobs) if j['unit']==unit and j['completion']['answer'] in answer)
      print('NEGATIVE_RECORD',job['state'],job['verdict'],'passing',bool(job.get('review')),flush=True)
      print('NEGATIVE_PUBLISH',plain(await s.call_tool('coordinator_publish',{'unit':unit,'review':job['id'],'metadata':meta,'cwd':str(repo)})),flush=True)
    answer=plain(await s.call_tool('code_review',{'sealed_unit':unit,'cwd':str(repo),'focus':'BOUND_GOOD_REVIEW','infra':True}));print('POSITIVE_REVIEW',case,answer[-1800:],flush=True)
    jobs=(await rpc('records/list',{'kind':'review_job'}))['data'];review=next(j['review'] for j in jobs if j['unit']==unit and j['state']=='recorded')
    if case=='unknown':
     (repo/'a.txt').write_text('unexplained\n')
    elif case=='stale':
     print('REOPEN',plain(await s.call_tool('coordinator_unit',{'action':'reopen','unit':unit,'cwd':str(repo)})),flush=True)
     print('RESEAL',plain(await s.call_tool('coordinator_seal',{'unit':unit,'cwd':str(repo)}))[:400],flush=True)
     print('STALE_SEAL',plain(await s.call_tool('coordinator_publish',{'unit':unit,'review':review,'metadata':meta,'cwd':str(repo)})),flush=True)
     await s.call_tool('code_review',{'sealed_unit':unit,'cwd':str(repo),'focus':'BOUND_GOOD_REVIEW','infra':True})
     jobs=(await rpc('records/list',{'kind':'review_job'}))['data'];current=(await rpc('unit/diff',{'unit':unit}))['binding'];review=next(j['review'] for j in jobs if j['unit']==unit and j['state']=='recorded' and j['binding']==current)
     advanced=gitio.text(repo,['commit-tree',tree,'-p',base],data=b'unrelated parent advance\n',extra_env=gitmeta);gitio.git(repo,['update-ref','HEAD',advanced,base])
    else:
     original=root/'refusal4-identity-original-git';os.rename(repo/'.git',original);shutil.copytree(original,repo/'.git')
    before=gitio.text(repo,['rev-parse','HEAD']);objects_before=sorted(str(p.relative_to(repo/'.git/objects')) for p in (repo/'.git/objects').rglob('*') if p.is_file())
    try:
     print('DIRECT_RESULT',await rpc('unit/publish',{'unit':unit,'review':review,'metadata':meta}),flush=True)
    except Exception as exc:print('DIRECT_REFUSAL',getattr(exc,'code',type(exc).__name__),str(exc),flush=True)
    print('UNCHANGED',case,before==gitio.text(repo,['rev-parse','HEAD']),objects_before==sorted(str(p.relative_to(repo/'.git/objects')) for p in (repo/'.git/objects').rglob('*') if p.is_file()),flush=True)
with Provider(router=router) as provider:
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  try:print('STOP',client.call('daemon/stop'),flush=True)
  except Exception as exc:print('CLEANUP_REQUIRED',type(exc).__name__,str(exc),flush=True)
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
PY
```

</details>

<details>
<summary>Detached completed review collection without another model call (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,base64,json,os,signal,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,native,processes
from coordinator.fixture import Provider,message
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU');repo=root/'review-detach-repo';repo.mkdir(mode=0o700);home=root/'review-detach-home';home.mkdir(mode=0o700);state=root/'review-detach-state';logs=root/'review-detach-logs'
gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n')
oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=('100644 '+oid+'\ta.txt\n').encode());tree=gitio.text(repo,['write-tree'])
meta={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'}
base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-review-detach'),CODEX_ORACLE_LOG_DIR=str(logs),CODEX_HOME=str(home),CODEX_ORACLE_MAX_RUNTIME_S='300',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
client=Client(state,timeout=60);selected={};answer='**Verdict**: Ship it\n\nThe exact sealed candidate is acceptable after checking its complete object diff.'
def router(body,index):time.sleep(20);return message(answer)
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
def parameters():return StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
async def rpc(method,params=None):return await asyncio.to_thread(client.call,method,params)
async def first():
 try:
  async with stdio_client(parameters(),errlog=open(root/'review-detach-first.log','w')) as streams:
   async with ClientSession(*streams) as s:
    await s.initialize();await s.call_tool('coordinator_claims',{'action':'acquire','paths':['a.txt'],'session_id':'review-detach'})
    checkout=await rpc('checkout/register',{'path':str(repo)});lead=await rpc('lead/session',{'checkout':checkout['id'],'session_id':'review-detach'});selected['unit']=lead['unit']
    await rpc('lead-edit',{'actor':lead['actor'],'changes':{'a.txt':{'bytes_base64':base64.b64encode(b'candidate\n').decode(),'mode':'100644'}}})
    await s.call_tool('coordinator_seal',{'unit':lead['unit']})
    task=asyncio.create_task(s.call_tool('code_review',{'sealed_unit':lead['unit'],'focus':'Review the complete bound candidate.','infra':True}))
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
     jobs=(await rpc('review/jobs',{'cwd':str(repo)}))['data']
     if jobs and jobs[0]['state']=='dispatched' and len(provider.requests)==1:
      selected.update(job=jobs[0]['id'],run=jobs[0]['run'],binding=jobs[0]['binding']);break
     await asyncio.sleep(.1)
    else:raise RuntimeError('Review did not dispatch')
    status=await rpc('daemon/status');supervisor=processes.identity(status['pid'])['ppid']
    records=[json.loads(p.read_text()) for p in (logs/'servers').glob('*.json')];live=[r for r in records if r['pid']!=supervisor and processes.identity(r['pid'])]
    if len(live)!=1:raise RuntimeError('MCP identity is ambiguous')
    ident=processes.identity(live[0]['pid'])
    if processes.identity(ident['pid'])!=ident:raise RuntimeError('MCP identity changed')
    os.kill(ident['pid'],signal.SIGKILL);print('KILLED_REVIEW_MCP',ident['pid'],ident['birth'],'JOB',selected,flush=True)
    await asyncio.gather(task,return_exceptions=True)
 except BaseException as exc:print('EXPECTED_DISCONNECT',type(exc).__name__,flush=True)
async def collect():
 await asyncio.sleep(22)
 async with stdio_client(parameters(),errlog=open(root/'review-detach-second.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   print('COLLECT',plain(await s.call_tool('codex_resume_run',{'run':selected['run']})),flush=True)
   job=next(j for j in (await rpc('review/jobs',{'cwd':str(repo)}))['data'] if j['id']==selected['job'])
   print('RECOVERED_REVIEW',job['state'],job.get('review'),'SAME_BINDING',job['binding']==selected['binding'],'FULL_ANSWER',job.get('completion',{}).get('answer')==answer,'REQUESTS',len(provider.requests),flush=True)
with Provider(router=router) as provider:
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:
  async def main():await first();await collect()
  asyncio.run(main())
 finally:
  print('STOP',client.call('daemon/stop'),flush=True);print('PROVIDER',len(provider.requests),provider.errors,flush=True)
PY
```

</details>

<details>
<summary>Forced preproduction native failure with actual legacy exec fallback (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,native
from coordinator.fixture import Provider,message,patch
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU');repo=root/'fallback-repo';repo.mkdir(mode=0o700);gitio.git(repo,['init','-q']);(repo/'f.txt').write_text('baseline\n')
oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=('100644 '+oid+'\tf.txt\n').encode());tree=gitio.text(repo,['write-tree']);base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'});gitio.git(repo,['update-ref','HEAD',base])
home=root/'fallback-home';home.mkdir(mode=0o700);state=root/'fallback-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-fallback'),CODEX_ORACLE_LOG_DIR=str(root/'fallback-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_CODEX='/bin/true',CODEX_ORACLE_MAX_RUNTIME_S='180',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
client=Client(state,timeout=40)
def router(body,index):
 users=[x for x in body.get('input',[]) if x.get('role')=='user'];prompt=json.dumps(users[-1:] if users else [])
 if 'PHASE 2 of 2' not in prompt:return message('Brief: change f.txt from baseline to fallback. Do not record Git.')
 if not any(x.get('name')=='apply_patch' for x in body.get('input',[])):return patch('*** Begin Patch\n*** Update File: f.txt\n@@\n-baseline\n+fallback\n*** End Patch\n')
 return message('Implemented f.txt through exclusive exec fallback.')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 async with stdio_client(params,errlog=open(root/'fallback-mcp-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize();print('RESULT',plain(await s.call_tool('abraham',{'task':'Use the fixture to change f.txt.','claims':['f.txt']})),flush=True)
   print('RUNS',plain(await s.call_tool('codex_runs',{})),flush=True)
 print('FILE',(repo/'f.txt').read_text(),flush=True)
 print('REFUSED_NATIVE',[(r['state'],r.get('refusal'),r.get('production_started')) for r in client.call('records/list',{'kind':'run'})['data']],flush=True)
 print('HELD_CLAIMS',[c['id'] for c in client.call('records/list',{'kind':'claim'})['data'] if c['state']=='held'],flush=True)
 print('LISTENERS',[(x['state'],x.get('allocation_cleaned')) for x in client.call('records/list',{'kind':'listener'})['data']],flush=True)
 journal=root/'fallback-logs/runs.jsonl'
 if journal.exists():print('EXEC_ENDS',[{k:r.get(k) for k in ('run','status','returncode','exit_code','thread_id')} for r in map(json.loads,journal.read_text().splitlines()) if r.get('phase')=='end'],flush=True)
with Provider(router=router) as provider:
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  try:print('STOP',client.call('daemon/stop'),flush=True)
  except Exception as exc:print('CLEANUP_REQUIRED',type(exc).__name__,str(exc),flush=True)
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
PY
```

</details>

<details>
<summary>Independent-process lease/exec/older-lock exclusion (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows CODEX_ORACLE_LOG_DIR=/private/tmp/software-workflows-c2-wrGSzU/locks-logs TMPDIR=/private/tmp/software-workflows-c2-wrGSzU /opt/homebrew/bin/python3.15 -B - <<'PY'
import json,os,subprocess,sys,time
from pathlib import Path
from coordinator import compat,gitio
from coordinator.leases import Custodian
root=Path('/private/tmp/software-workflows-c2-wrGSzU')
repo=root/'locks-repo';repo.mkdir(mode=0o700);gitio.git(repo,['init','-q'])
alias=root/'locks-alias';alias.symlink_to(repo,target_is_directory=True)
sub=repo/'nested';sub.mkdir()
custodian=Custodian()
probe="""import json,sys
from coordinator import compat
compat._register_server()
ok,holder=compat._acquire_write_lock(sys.argv[1],'exec-observer')
print(json.dumps({'acquired':ok,'holder':holder}),flush=True)
if ok:compat._release_write_lock(sys.argv[1])
"""
def attempt(path):
 done=subprocess.run([sys.executable,'-B','-c',probe,str(path)],capture_output=True,text=True,timeout=20)
 print('EXEC_PROBE',str(path),done.returncode,done.stdout.strip(),done.stderr.strip())
 return json.loads(done.stdout)
try:
 print('NATIVE_FIRST',custodian.request({'action':'acquire','cwd':str(repo)}))
 identity=compat._write_lock_path(str(repo));inode=identity.stat().st_ino
 print('GROUP_SHARE',custodian.request({'action':'acquire','cwd':str(alias)}),'leases',len(custodian.held))
 for path in (repo,alias,sub):assert not attempt(path)['acquired']
 legacy=compat._legacy_lock_paths_for(str(repo))
 print('OLDER_CONTENT_VIEW',[(p.name,p.exists(),compat._server_alive(os.getpid(),compat._SERVER_START)) for p in legacy])
 print('NATIVE_RELEASE',custodian.request({'action':'release','cwd':str(alias)}))
 assert attempt(alias)['acquired'];assert identity.stat().st_ino==inode
 holder="""import json,sys
from coordinator import compat
compat._register_server()
ok,reason=compat._acquire_write_lock(sys.argv[1],'exec-held')
print(json.dumps({'acquired':ok,'holder':reason}),flush=True)
sys.stdin.readline()
if ok:compat._release_write_lock(sys.argv[1])
"""
 child=subprocess.Popen([sys.executable,'-B','-c',holder,str(alias)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 try:
  print('EXEC_FIRST',child.stdout.readline().strip())
  response=custodian.request({'action':'acquire','cwd':str(repo)});print('NATIVE_PROBE',response);assert not response['ok']
 finally:child.communicate('\n',timeout=15);print('EXEC_HOLDER_EXIT',child.returncode)
 print('NATIVE_AFTER_EXEC',custodian.request({'action':'acquire','cwd':str(sub)}))
 custodian.request({'action':'release','cwd':str(repo)})
 old=subprocess.Popen(['/bin/sleep','30'])
 try:
  oldpath=compat._legacy_lock_paths_for(str(alias))[0]
  oldpath.write_text('old-content pid='+str(old.pid)+' pstart='+compat._proc_start(old.pid)+' cwd='+str(alias)+' t='+str(int(time.time()))+'\n')
  response=custodian.request({'action':'acquire','cwd':str(repo)});print('OLDER_FIRST',response);assert not response['ok']
 finally:
  old.terminate();old.wait();oldpath.unlink();print('OLDER_EXIT',old.returncode)
 print('NATIVE_AFTER_OLDER',custodian.request({'action':'acquire','cwd':str(repo)}))
 custodian.request({'action':'release','cwd':str(repo)})
 print('STABLE_FLOCK_INODE',inode,identity.stat().st_ino,'HELD',custodian.held,compat._HELD)
finally:
 for cwd in list(custodian.held.values()):custodian.request({'action':'release','cwd':cwd})
PY
```

</details>

<details>
<summary>Raw order variation in a disposable nonempty policy, no inference (exit 0)</summary>

```sh
PYTHONPATH=plugins/software-workflows SOFTWARE_WORKFLOWS_RUNTIME=/private/tmp/software-workflows-c2-wrGSzU/rt-policy-order TMPDIR=/private/tmp/software-workflows-c2-wrGSzU /opt/homebrew/bin/python3.15 -B - <<'PY'
import asyncio,json,os
from pathlib import Path
from coordinator import native,runtime,processes,gitio
from coordinator.fixture import Provider
root=Path('/private/tmp/software-workflows-c2-wrGSzU/policy-order');root.mkdir(mode=0o700);home=root/'home';home.mkdir(mode=0o700);rules=home/'rules';rules.mkdir();repo=root/'repo';repo.mkdir();gitio.git(repo,['init','-q'])
(rules/'c2.rules').write_text('\n'.join('prefix_rule(pattern=["c2-rule-'+str(n)+'"], decision="allow")' for n in range(4))+'\n')
async def once(provider):
 allocation=runtime.allocate('policy');socket=Path(allocation['path']);traces=[]
 process=await asyncio.create_subprocess_exec(native.native_codex(),'app-server','--listen','unix://'+str(socket),*native.override_args(provider.config),cwd=repo,env=native.environment(CODEX_HOME=str(home),RUST_LOG='codex_core::exec_policy=trace',LOG_FORMAT='json'),stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
 ident=processes.identity(process.pid)
 async def consume():
  async for line in process.stderr:
   try:
    fields=json.loads(line).get('fields',{})
    if fields.get('message')=='exec policy rules loaded':traces.append(fields.get('rules'))
   except (ValueError,UnicodeError):pass
 task=asyncio.create_task(consume());connection=None
 try:
  for i in range(200):
   if socket.exists():break
   await asyncio.sleep(.02)
  connection=await native.Connection.connect(socket,lambda e:None);await connection.initialize()
  await connection.call('thread/start',{'cwd':str(repo),'model':'gpt-6-astra','approvalPolicy':'never','sandbox':'danger-full-access'})
  await asyncio.sleep(.2)
  print('OWN_FIXTURE_POLICY_TRACE',traces,flush=True)
 finally:
  if connection:await connection.close()
  current=processes.identity(process.pid)
  if current and current['birth']==ident['birth']:process.terminate()
  print('LISTENER_EXIT',await process.wait(),flush=True);await task;runtime.cleanup(allocation)
with Provider() as provider:
 async def main():
  await once(provider);await once(provider)
 asyncio.run(main());print('MODEL_REQUESTS',len(provider.requests))
PY
```

</details>

<details>
<summary>Equivalent nonempty policies cache-hit; changed policy cache-miss (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import json,os
from pathlib import Path
from coordinator import native,gitio
from coordinator.client import Client
from coordinator.fixture import Provider
root=Path('/private/tmp/software-workflows-c2-wrGSzU');state=root/'policy-cache-state';home=root/'policy-cache-home';home.mkdir(mode=0o700);rules=home/'rules';rules.mkdir();repo=root/'policy-cache-repo';repo.mkdir(mode=0o700);gitio.git(repo,['init','-q'])
tree=gitio.text(repo,['mktree'],data=b'');meta={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
os.environ.update(SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-policy-cache'),CODEX_ORACLE_LOG_DIR=str(root/'policy-cache-logs'),CODEX_HOME=str(home),TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):os.environ.pop(key,None)
client=Client(state,timeout=120);results=[]
with Provider() as provider:
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:
  print('START',client.ensure()['ready'],flush=True)
  checkout=client.call('checkout/register',{'path':str(repo)})
  for index,changed in enumerate((False,False,True)):
   (rules/'c2.rules').write_text('\n'.join('prefix_rule(pattern=["c2-rule-'+str(n)+'"], decision="'+('forbidden' if changed and n==3 else 'allow')+'")' for n in range(4))+'\n')
   run=client.call('run/start',{'checkout':checkout['id'],'mode':'native','task':''})
   record=client.call('run/status',{'run':run['run']});results.append(record['admission']);print('ADMISSION',index,record['admission'],flush=True)
  print('SAME_EQUIVALENT_KEY',results[0]['fingerprint']==results[1]['fingerprint'],'SECOND_HIT',results[1]['cache_hit'],'CHANGED_POLICY_MISS',results[2]['fingerprint']!=results[1]['fingerprint'] and not results[2]['cache_hit'],'MODEL_REQUESTS',len(provider.requests),flush=True)
 finally:print('STOP',client.call('daemon/stop'),flush=True)
PY
```

</details>

<details>
<summary>The one allowed paid scenario, two parallel real-model Abraham calls (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU');repo=root/'real-pair-repo';repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q'])
rows=[]
for name in ('alpha.txt','beta.txt'):
 (repo/name).write_text('pending\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'pending\n');rows.append('100644 '+oid+'\t'+name+'\n')
gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=''.join(rows).encode());tree=gitio.text(repo,['write-tree'])
meta={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'}
base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
state=root/'real-pair-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-real-pair'),CODEX_ORACLE_LOG_DIR=str(root/'real-pair-logs'),CODEX_ORACLE_MAX_RUNTIME_S='900',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID','SOFTWARE_WORKFLOWS_FIXTURE','SOFTWARE_WORKFLOWS_CODEX','SOFTWARE_WORKFLOWS_ROLE','CODEX_ORACLE_CONTEXT_WINDOW'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
client=Client(state,timeout=60)
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def rpc(method,params=None):return await asyncio.to_thread(client.call,method,params)
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 async with stdio_client(params,errlog=open(root/'real-pair-mcp-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize();print('CATALOG',[t.name for t in (await s.list_tools()).tools],flush=True)
   async def writer(name,value):
    started=time.time()
    task='Change only '+name+' from the single line pending to the single line '+value+' with its final newline preserved. This is a deliberately tiny operational verification task. Use the coordinator read_file to inspect and apply_patch to edit; do not create any other files. The analysis turn should read and give a short concrete brief without editing. In implementation, verify by reading the actual file. Do not delegate or use external services. Do not run any Git write command. A separate writer owns the other file. The lead records.'
    answer=plain(await s.call_tool('abraham',{'task':task,'claims':[name],'model':'gpt-6-astra','session_id':'c2-real-pair'}))
    print('REAL_RESULT',name,'SECONDS',round(time.time()-started,2),answer,flush=True)
   await asyncio.gather(writer('alpha.txt','alpha ready'),writer('beta.txt','beta ready'))
   runs=(await rpc('run/list',{'cwd':str(repo)}))['data'];print('RUN_SUMMARIES',json.dumps(runs),flush=True)
   for r in runs:
    record=await rpc('run/status',{'run':r['run']})
    print('PHASE_INPUTS',r['run'],[(key,value['state'],value.get('input_id'),value.get('turn')) for key,value in record['phases'].items()],flush=True)
   print('FILES',[(name,(repo/name).read_text()) for name in ('alpha.txt','beta.txt')],'HEAD_UNCHANGED',gitio.text(repo,['rev-parse','HEAD'])==base,flush=True)
try:asyncio.run(main())
finally:
 try:
  for r in client.call('run/list',{'cwd':str(repo)})['data']:
   if r['state'] not in ('completed','cancelled','failed','refused','blocked'):print('CLEANUP_CANCEL',client.call('run/interrupt',{'run':r['run']})['state'],flush=True)
  print('STOP',client.call('daemon/stop'),flush=True)
 except Exception as exc:print('CLEANUP_REQUIRED',getattr(exc,'code',type(exc).__name__),str(exc),flush=True)
PY
```

</details>


## C2 completion record — 2026-10-18 16:43 UTC

This closes the ongoing implementation record. Core C2 paths are implemented
and the observations below used the final Python source. The paid pair above
preceded the final policy-order, unread-notice and orderly-stop refinements;
those refinements were exercised with the real installed binary and fixture
provider, not another paid scenario. Earlier unsuccessful observations remain
failures. NotebookEdit and the explicitly named coverage gaps remain open.

### Final cache, resume, inbox and catalog observations

The final nonempty-policy cache observation exited **0**. Three actual fresh
writers were admitted without submitting production turns:

| Listener configuration | Cache | Fingerprint |
|---|---|---|
| Four-rule policy, first listener | miss | 2f6822f9fbfec2f4d7f13e41be9f2d5f38a736073ea19960ab79faa3dae89bbb |
| Equivalent policy, fresh listener | hit | 2f6822f9fbfec2f4d7f13e41be9f2d5f38a736073ea19960ab79faa3dae89bbb |
| One rule changed from allow to forbidden | miss | 892b1820ce226cd08e698fa906482f43313970f8a5a64125624f8910d7eceb68 |

Output: `SAME_EQUIVALENT_KEY True SECOND_HIT True CHANGED_POLICY_MISS True
MODEL_REQUESTS 0`; stop returned `stopping:true`. The exact command is in
the preceding appendix. This closes the random execution-policy map-order cache
miss observed in the paid pair without removing meaningful policy differences.

The final actual-MCP resume/inbox observation exited **0**, fixture provider
requests **10**, errors **[]**, and stop succeeded:

- MCP initialization and `tools/list` exposed the existing tools plus the six
  lead tools: **15** tools total. Decorated tool functions retained their input
  schemas. Writer catalog exclusion was also measured in the earlier admissions.
- The first inbox page contained an acknowledged message while a later message
  remained unread. The independent total unread count was **1**.
- Empty Abraham input, an invalid coordinator-unit action, empty run-log input,
  and a missing bound-review unit each returned **one** unread-lead notice.
  No read implicitly acknowledged the message.
- Run `run_84338c04740e4929a1b4f73f25544951`, unit
  `unit_0e2d0e0b95f84a6a86d248502134f4f2`, completed Abraham, then an explicit
  `RESUME_C2` nudge completed a new implementation turn. The old phase remained
  in `previous_phases` (length **1**); a.txt became `resumed\n` with an exact
  receipt.
- Both wake=start and wake=queue messages reached acknowledged/submitted states.
  The queued message ran quickly, so this observation **did not** exercise the
  orderly-stop refusal while a queued delivery remains pending. That guard is
  implemented but this particular timing case is **UNVERIFIED**.
- Explicit cancellation settled to cancelled. Sealing then succeeded; a further
  resume nudge refused `UNIT_FROZEN`. Scratch HEAD was unchanged.

### Step 0b findings: separate status and evidence

These are separate from the four Step 0a usability repairs. They are not
declared closed merely because the native workflow succeeded.

| Finding | Implementation and measured result | Remaining verification limit |
|---|---|---|
| Git helpers/environment redirection | `custody.py` uses closed supported option contracts for both declaration and execution. Analysis-phase old code permitted the five named environment/pager forms. Final pure-classifier invocation refused all ten recorded unsafe/unsupported forms and allowed four plain read forms; exit **0**. None of the dangerous strings was executed. | This is conservative classification, not OS confinement for arbitrary full-access programs. Unestablished Git forms refuse. |
| Repository identity checked too late | `gitio.py` / `publish.py` revalidate before objects, before reference mutation, and inside the prepared transaction. Actual equal-content/different-inode .git replacement returned `CHECKOUT_IDENTITY_CHANGED`; HEAD and object inventory were unchanged; observer exit **0**. | Replacement specifically inside the prepared transaction window was not injected. |
| Service exemption includes later descendants | `processes.py` exempts only exact recorded service identities. A final Python 3.9 observation kept the service exempt while its new /bin/sleep child was owned; exit **0**. Child PID **52289**, birth **1792341716:103701**, kernel ID **8295072**. Both exited naturally. | This focused kernel observation complements actual native listener admission/custody observations; it does not claim every external MCP service lifecycle was exercised. |
| Ambiguous process forgotten after reparenting | `processes.py` retains ambiguous PID/birth/kernel records; daemon custody persists them and recovery reuses them. The final actual child retained `PROCESS_AMBIGUOUS` at PPID **1**, then cleared only after verified exit; observer exit **0**. PID **52327**, birth **1792341718:143413**, kernel ID **8295110**, original-parent kernel ID **8295109**. | Full daemon restart with this deliberately unrelated ambiguous process alive was not separately exercised. Its quarantine remains a refusal until valid ownership or disappearance can be verified. |

The final service/ambiguous-process command and captured output appear below.
Step 0a's listener cleanup has a measured new-green registry result, but an
operational replay of the old leaking daemon was not run; its old-red basis
remains the inspected C1 code. Do not turn that into an old/new execution claim.

### Existing project checks and final source audit

All commands below were actually run with the final Python source; exit codes
are the command results, not inferred from source inspection.

| Check | Exit | Captured result |
|---|---:|---|
| `claude plugin validate --strict plugins/software-workflows` with scratch CLAUDE_CONFIG_DIR | 0 | `✔ Validation passed` |
| `claude plugin validate --strict .` with scratch CLAUDE_CONFIG_DIR | 0 | `✔ Validation passed` |
| Actual Apple Python 3.9 in-memory compilation | 0 | `Python 3.9 in-memory compile: 8 files` |
| Python 3.11 grammar plus current-interpreter in-memory compilation | 0 | `Python 3.11 grammar and current-interpreter in-memory compile: 32 files`; `Hooks JSON parsed` |
| `git diff --check` for allowed tracked paths | 0 | No output |
| Direct source audit including untracked coordinator files | 0 | **32** source/config files; whitespace issues **[]** |

Actual execution on a Python **3.11** interpreter remains **UNVERIFIED**; the
daemon ran under **3.15**, and Python 3.11 grammar parsing is not runtime proof.
Python 3.9 hook/query/process execution was real. No dedicated build/linter
configuration or additional existing suite was found; no test files or harness
were added.

Final coordinator adapter digest:
`90c5666b86628fa4a25240f8a3a99bc2c24ed5a311be64f34a2b1c2016e576e3`.
Per-file SHA-256 output is preserved below. The source audit found two generated
bytecode files from an earlier server launch; those two known generated files
were removed individually. Remaining coordinator bytecode: **0**.
The compilation check itself wrote no bytecode.

### Final shutdown and residue inventory

Each operational coordinator was stopped with its documented shutdown path;
failed earlier observers have explicit later cleanup observations. An independent
read-only inventory inspected **28** scratch ledgers, **553** records,
**59** listeners, **14** fixture manifests and **407** endpoint manifests:

- **149** distinct recorded PID/birth identities: all gone, no reused PID or
  zombie mistaken for a live owned process.
- Live recorded processes **[]**; scratch daemon/provider command matches **[]**.
- **790** recorded requested/physical endpoints: no matching live private
  listener command.
- Two stale owned listener allocations from the earlier failed `mcp3`
  admission were found. Their recorded processes were gone; ownership token and
  directory identity were verified by `runtime.cleanup` before removing only
  those two allocations. Final remaining recorded allocations **[]**.
- Final inventory errors **[]**, exit **0**.
- Default `~/.local/state/software-workflows` existence: **false**.
- Scratch repositories, ledgers, logs, calibration evidence and stable ownership/
  compatibility lock anchors are retained for review. They are not active
  daemons or leases. No shared/default Codex socket directory was removed.
- The final focused process observation also waited for its service, parent,
  listener process and reparented child to exit; it started no daemon.

The working-tree status at final audit contained only modified server.py,
additive hooks/hooks.json, the untracked coordinator directory (C1 plus C2),
and the new hooks/coordinator_lead.py. Changes remain **uncommitted**.
run_server.py and the existing gate/shim/session hooks were not edited.

### C2 file-by-file implementation map

Paths below are relative to plugins/software-workflows. Existing C1 files not
listed here remain part of the package; the source audit includes them too.

| File | What changed and why |
|---|---|
| server.py | Native Abraham selection, async coordinator bridge, signed results, backend-aware run tools, six lead tools, session notices, bound review metadata/jobs, writer catalog registration, isolated logs and CODEX_HOME-aware config. |
| coordinator/admission.py | Kernel-ownership calibration, stable capability keys and in-flight calibration sharing, per-listener trust/policy/catalog checks, isolated calibration roots. |
| coordinator/client.py | Observational discovery, actual daemon interpreter, inherited venv protection, cwd-independent module startup, structured startup failures. |
| coordinator/compat.py | Shared existing filesystem-identity/legacy/mixed-version lock protocol, factored without importing the MCP server. |
| coordinator/custody.py | Closed Git argument/environment/helper contracts used by declaration and execution. |
| coordinator/daemon.py | Durable workflow integration, lead/intent/review/query RPCs, listener queue/request cleanup, process settlement, supervisor lease retention and safe stop guards. |
| coordinator/gitio.py | Reusable repository filesystem-identity revalidation at publication boundaries. |
| coordinator/intents.py | Durable host edit intent/preimage/expected-image adapters, idempotent completion and gap/failure quarantine; explicit NotebookEdit refusal. |
| coordinator/lead.py | Durable real Claude-session to lead actor/unit mapping and checkout scope checks. |
| coordinator/leases.py | Supervisor-owned checkout writer-group compatibility leases and replacement-worker RPC. |
| coordinator/messages.py | Durable delivery integration and pagination-independent unread totals. |
| coordinator/native.py | Supported image/outbound contracts, caller-routing isolation, source digest coverage, native lifecycle/config handling and conservative policy-map normalization. |
| coordinator/observations.py | One-request observational checkout/session snapshot and identity/down/freshness distinctions. |
| coordinator/processes.py | Darwin kernel unique/original-parent identity, marker-independent ownership domains, exact service exemptions, retained ambiguity and birth-verified termination. |
| coordinator/publish.py | Pre-ref abandonment and post-ref completion recovery; early/transactional repository identity guards while preserving review binding and UNKNOWN. |
| coordinator/query.py | Python 3.9 read-only import/CLI for the lead's gate integration. |
| coordinator/recovery.py | Durable workflow/history and lead-gap recovery, child subscriptions and listener replacement cleanup. |
| coordinator/reviews.py | Persisted immutable review-job binding, complete completion evidence, conservative verdict acceptance and detached collection. |
| coordinator/roles.py | Writer-role catalog/config overlay hiding lead authority while retaining unrelated MCP tools. |
| coordinator/runs.py | Durable scoped list/events/results, reconciliation-first resume, selected-run cancellation and settlement. |
| coordinator/runtime.py | Separate observational discovery from allocation/calibration; validated owned endpoint lifecycle. |
| coordinator/writer.py | One-thread/two-turn Abraham state machine, stable input identity, analysis gate, uncertain-submission recovery, complete results and attribution. |
| hooks/coordinator_lead.py | New Python 3.9 host entry point: session injection, file intent lifecycle, Bash observation and one down-warning per session. |
| hooks/hooks.json | Additive registrations only; existing entries preserved. |
| coordinator/README.md | Updated operator/RPC/MCP/query/recovery contract and explicit unsupported behavior. |
| coordinator/VERIFY.md | Preserved C1 history and added actual C2 commands, failures, outcomes, cleanup and coverage limits. |

### Final operational command appendix

<details>
<summary>Resume, single unread notices, wake policies and frozen-unit refusal (exit 0)</summary>

```sh
PYTHONPATH=/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c2-wrGSzU/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,re,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes
from coordinator.fixture import Provider,function,message,patch
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c2-wrGSzU');repo=root/'resume-repo';repo.mkdir(mode=0o700);home=root/'resume-home';home.mkdir(mode=0o700);fixture=root/'resume-fixture';fixture.mkdir(mode=0o700);state=root/'resume-state'
gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n')
oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['read-tree','--empty']);gitio.git(repo,['update-index','--index-info'],data=('100644 '+oid+'\ta.txt\n').encode());tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C2','GIT_AUTHOR_EMAIL':'c2@invalid','GIT_COMMITTER_NAME':'C2','GIT_COMMITTER_EMAIL':'c2@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'rt-resume'),CODEX_ORACLE_LOG_DIR=str(root/'resume-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='300',TMPDIR=str(root))
for key in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(key,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
client=Client(state,timeout=60)
def router(body,index):
 data=body.get('input',[]);users=[(i,json.dumps(x)) for i,x in enumerate(data) if x.get('role')=='user'];at,text=users[-1];calls=[x for x in data[at+1:] if x.get('type') in ('function_call','custom_tool_call')]
 ids=re.findall(r'C1_MESSAGE (msg_[0-9a-f]{32})',text)
 if ids:
  if not any(x.get('name')=='ack' for x in calls):return function('ack',{'message':ids[-1]})
  return message('Acknowledged the coordinator message.')
 if 'RESUME_C2' in text:
  if not any(x.get('name')=='apply_patch' for x in calls):return patch('*** Begin Patch\n*** Update File: a.txt\n@@\n-first\n+resumed\n*** End Patch\n')
  return message('Resumed edit completed.')
 if 'PHASE 2 of 2' not in text:return message('Brief: use apply_patch to change only a.txt.')
 if not any(x.get('name')=='apply_patch' for x in calls):return patch('*** Begin Patch\n*** Update File: a.txt\n@@\n-baseline\n+first\n*** End Patch\n')
 return message('Initial implementation completed.')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
def value(r):return json.loads(plain(r).split('\n[Coordinator:')[0])
async def rpc(method,params=None):return await asyncio.to_thread(client.call,method,params)
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 async with stdio_client(params,errlog=open(root/'resume-mcp-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize();print('LEAD_CATALOG',[t.name for t in (await s.list_tools()).tools],flush=True)
   for text in ('first lead note','second lead note'):await s.call_tool('coordinator_send',{'recipient':'lead','text':text})
   inbox=value(await s.call_tool('coordinator_inbox',{}));await s.call_tool('coordinator_inbox',{'acknowledge':[inbox['data'][0]['id']],'limit':1})
   print('PAGED_UNREAD',value(await s.call_tool('coordinator_inbox',{'limit':1}))['unread'],flush=True)
   for tool,args in [('abraham',{'task':''}),('coordinator_unit',{'action':'bad'}),('codex_run_log',{}),('code_review',{'sealed_unit':'missing'})]:
    answer=plain(await s.call_tool(tool,args));print('REFUSAL_NOTICE',tool,answer.count('[Coordinator: unread lead messages;'),answer[:350],flush=True)
   answer=plain(await s.call_tool('abraham',{'task':'RESUME_INITIAL','claims':['a.txt']}));print('INITIAL',answer[:1800],flush=True)
   run=(await rpc('run/list',{'cwd':str(repo)}))['data'][0];rid=run['run']
   print('RESUME',plain(await s.call_tool('codex_resume_run',{'run':rid,'nudge':'RESUME_C2'})),flush=True)
   for i in range(200):
    record=await rpc('run/status',{'run':rid})
    if record['state'] in ('completed','failed','blocked'):break
    await asyncio.sleep(.1)
   print('RESUMED_STATE',record['state'],'PREVIOUS_PHASES',len(record.get('previous_phases',[])),'BYTES',(repo/'a.txt').read_text(),flush=True)
   sent=value(await s.call_tool('coordinator_send',{'recipient':rid,'text':'START_WAKE_C2','wake':'start'}))
   for i in range(200):
    current=next(m for m in (await rpc('records/list',{'kind':'message'}))['data'] if m['id']==sent['id'])
    summary=(await rpc('run/list',{'cwd':str(repo)}))['data'][0]
    if current['state']=='acknowledged' and not any(a['active_turn'] for a in summary['actors']):break
    await asyncio.sleep(.1)
   print('START_WAKE',current['state'],current['delivery'],flush=True)
   queued=value(await s.call_tool('coordinator_send',{'recipient':rid,'text':'QUEUE_WAKE_C2','wake':'queue'}))
   await asyncio.sleep(.5)
   queued=next(m for m in (await rpc('records/list',{'kind':'message'}))['data'] if m['id']==queued['id'])
   print('QUEUED',queued['state'],queued['delivery'],flush=True)
   if queued['state']=='submitted':
    try:print('UNEXPECTED_STOP',await rpc('daemon/stop'),flush=True)
    except Exception as exc:print('QUEUED_STOP_REFUSAL',getattr(exc,'code',type(exc).__name__),flush=True)
   print('CANCEL_QUEUE',(await rpc('run/interrupt',{'run':rid}))['state'],flush=True)
   print('SEAL',plain(await s.call_tool('coordinator_seal',{'unit':run['unit']}))[:400],flush=True)
   print('FROZEN_RESUME',plain(await s.call_tool('codex_resume_run',{'run':rid,'nudge':'RESUME_C2'})),flush=True)
   print('HEAD_UNCHANGED',gitio.text(repo,['rev-parse','HEAD'])==base,flush=True)
with Provider(router=router) as provider:
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 try:asyncio.run(main())
 finally:
  for r in client.call('run/list',{'cwd':str(repo)})['data']:
   if r['state'] not in ('completed','cancelled','failed','refused','blocked'):print('CLEANUP_CANCEL',client.call('run/interrupt',{'run':r['run']})['state'],flush=True)
  print('STOP',client.call('daemon/stop'),flush=True);print('PROVIDER',len(provider.requests),provider.errors,flush=True)
PY
```

Observed outcomes and durable IDs are recorded above.

</details>

<details>
<summary>Final classifier observation (exit 0)</summary>

```sh
PYTHONPATH=plugins/software-workflows /opt/homebrew/bin/python3.15 -B - <<'PY'
from coordinator.custody import recording_command
blocked=["GIT_DIR=x git status","GIT_EXTERNAL_DIFF=/bin/echo git diff","GIT_PAGER='git push' git log","git grep --open-files-in-pager='git push; true' needle","git grep -O'git push' needle","git diff --ext-diff","git -c core.pager='git push' log","env GIT_DIR=x git status","git grep -O 'git push' needle","git --exec-path=x status"]
plain=['git status --short','git diff --no-ext-diff --no-textconv','git log -5 --oneline','git grep -n needle -- a.txt']
for command in blocked:
 result=recording_command(command);print('REFUSED',result,repr(command));assert result
for command in plain:
 result=recording_command(command);print('PLAIN_ALLOWED',not result,repr(command));assert not result
print('Pure classifier only: no listed command executed')
PY
```

Captured output:

```text
REFUSED True 'GIT_DIR=x git status'
REFUSED True 'GIT_EXTERNAL_DIFF=/bin/echo git diff'
REFUSED True "GIT_PAGER='git push' git log"
REFUSED True "git grep --open-files-in-pager='git push; true' needle"
REFUSED True "git grep -O'git push' needle"
REFUSED True 'git diff --ext-diff'
REFUSED True "git -c core.pager='git push' log"
REFUSED True 'env GIT_DIR=x git status'
REFUSED True "git grep -O 'git push' needle"
REFUSED True 'git --exec-path=x status'
PLAIN_ALLOWED True 'git status --short'
PLAIN_ALLOWED True 'git diff --no-ext-diff --no-textconv'
PLAIN_ALLOWED True 'git log -5 --oneline'
PLAIN_ALLOWED True 'git grep -n needle -- a.txt'
Pure classifier only: no listed command executed
```

</details>

<details>
<summary>Final Python 3.9 service child and ambiguous reparenting observation (exit 0)</summary>

```sh
PYTHONPATH=plugins/software-workflows /usr/bin/python3 -B - <<'PY'
import json,os,subprocess,sys,time
from coordinator.processes import identity,inventory
from coordinator.client import CoordinatorError
operation={'native':{'thread':'C2-final-process-observation','call':'C2-final-command'}}
env=dict(os.environ)
for key in ('CODEX_THREAD_ID','CODEX_SESSION_ID','CODEX_TOOL_CALL_ID','SOFTWARE_WORKFLOWS_OPERATION'):env.pop(key,None)
service_code="import subprocess,sys; print('ready',flush=True); sys.stdin.readline(); p=subprocess.Popen(['/bin/sleep','2']); print(p.pid,flush=True); p.wait(); sys.stdin.readline()"
service=subprocess.Popen([sys.executable,'-B','-c',service_code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=env)
try:
 assert service.stdout.readline().strip()=='ready'
 baseline=identity(service.pid);listener=identity(os.getpid())
 service.stdin.write('spawn\n');service.stdin.flush();child=int(service.stdout.readline())
 owned=inventory(listener,operation['native']['thread'],operation=operation,services=[baseline])
 print('BASELINE_SERVICE_EXEMPT',all(p['pid']!=service.pid for p in owned))
 print('LATER_CHILD_OWNED',any(p['pid']==child for p in owned),'CHILD',json.dumps(identity(child)))
 assert any(p['pid']==child for p in owned) and all(p['pid']!=service.pid for p in owned)
 service.stdin.write('finish\n');service.stdin.flush();print('SERVICE_EXIT',service.wait(timeout=5))
finally:
 if service.poll() is None:service.stdin.close();service.wait(timeout=5)
listener_process=subprocess.Popen(['/bin/sleep','6'],env=env)
parent_code="import subprocess,sys; p=subprocess.Popen(['/bin/sleep','3'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True); sys.stdin.readline()"
parent=subprocess.Popen([sys.executable,'-B','-c',parent_code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=env)
try:
 child=int(parent.stdout.readline());ambiguous=identity(child);listener=identity(listener_process.pid)
 parent.stdin.write('exit\n');parent.stdin.flush();print('ORIGINAL_PARENT_EXIT',parent.wait(timeout=2))
 deadline=time.monotonic()+2
 while time.monotonic()<deadline:
  after=identity(child)
  if after and after['ppid']==1:break
  time.sleep(.01)
 assert after and after['birth']==ambiguous['birth'] and after['kernel_id']==ambiguous['kernel_id'] and after['ppid']==1
 print('REPARENTED_AMBIGUOUS',json.dumps(after))
 try:inventory(listener,operation['native']['thread'],operation=operation,ambiguous=[ambiguous])
 except CoordinatorError as exc:
  print('RETENTION',exc.code,[p['pid'] for p in exc.details.get('processes',[])])
  assert exc.code=='PROCESS_AMBIGUOUS' and any(p['pid']==child for p in exc.details['processes'])
 else:raise RuntimeError('Ambiguous process was forgotten')
 deadline=time.monotonic()+6
 while time.monotonic()<deadline:
  after=identity(child)
  if not after or after['zombie']:break
  time.sleep(.05)
 else:raise RuntimeError('Scratch child did not exit')
 print('AFTER_VERIFIED_EXIT',inventory(listener,operation['native']['thread'],operation=operation,ambiguous=[ambiguous]))
 print('LISTENER_PROCESS_EXIT',listener_process.wait(timeout=7))
finally:
 if parent.poll() is None:parent.stdin.close();parent.wait(timeout=5)
 if listener_process.poll() is None:listener_process.wait(timeout=7)
PY
```

Captured output:

```text
BASELINE_SERVICE_EXEMPT True
LATER_CHILD_OWNED True CHILD {"pid": 52289, "birth": "1792341716:103701", "ppid": 52288, "uid": 501, "zombie": false, "kernel_id": 8295072, "parent_kernel_id": 8295071}
SERVICE_EXIT 0
ORIGINAL_PARENT_EXIT 0
REPARENTED_AMBIGUOUS {"pid": 52327, "birth": "1792341718:143413", "ppid": 1, "uid": 501, "zombie": false, "kernel_id": 8295110, "parent_kernel_id": 8295109}
RETENTION PROCESS_AMBIGUOUS [52327]
AFTER_VERIFIED_EXIT []
LISTENER_PROCESS_EXIT 0
```

</details>

<details>
<summary>Final plugin check (exit 0)</summary>

```sh
CLAUDE_CONFIG_DIR=/private/tmp/software-workflows-c2-wrGSzU/validate-final-plugin TMPDIR=/private/tmp/software-workflows-c2-wrGSzU claude plugin validate --strict plugins/software-workflows
```

Captured output:

```text
Validating plugin manifest: /Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/.claude-plugin/plugin.json

✔ Validation passed
```

</details>

<details>
<summary>Final marketplace check (exit 0)</summary>

```sh
CLAUDE_CONFIG_DIR=/private/tmp/software-workflows-c2-wrGSzU/validate-final-marketplace TMPDIR=/private/tmp/software-workflows-c2-wrGSzU claude plugin validate --strict .
```

Captured output:

```text
Validating marketplace manifest: /Users/ahrar/Documents/agent-teams-plugin/.claude-plugin/marketplace.json

✔ Validation passed
```

</details>

<details>
<summary>Final compile check (exit 0)</summary>

```sh
/opt/homebrew/bin/python3.15 -B - <<'PY'
import ast,json,subprocess
from pathlib import Path
base=Path('plugins/software-workflows')
compatible=['coordinator/__init__.py','coordinator/__main__.py','coordinator/client.py','coordinator/runtime.py','coordinator/query.py','coordinator/processes.py','coordinator/hooks.py','hooks/coordinator_lead.py']
program='import pathlib,sys; files=sys.argv[1:]; [compile(pathlib.Path(p).read_bytes(),p,"exec") for p in files]; print("Python 3.9 in-memory compile:",len(files),"files")'
done=subprocess.run(['/usr/bin/python3','-B','-c',program,*[str(base/p) for p in compatible]],text=True,capture_output=True);print(done.stdout.strip());print('Python 3.9 exit:',done.returncode);print(done.stderr.strip());done.check_returncode()
files=[base/'server.py',base/'run_server.py',*sorted((base/'coordinator').glob('*.py')),base/'hooks/coordinator_lead.py']
for p in files:ast.parse(p.read_text(),filename=str(p),feature_version=(3,11));compile(p.read_bytes(),str(p),'exec')
print('Python 3.11 grammar and current-interpreter in-memory compile:',len(files),'files')
json.loads((base/'hooks/hooks.json').read_text());print('Hooks JSON parsed')
print('No bytecode files written')
PY
```

Captured output:

```text
Python 3.9 in-memory compile: 8 files
Python 3.9 exit: 0

Python 3.11 grammar and current-interpreter in-memory compile: 32 files
Hooks JSON parsed
No bytecode files written
```

</details>

<details>
<summary>Final scope check (exit 0)</summary>

```sh
git diff --check -- plugins/software-workflows/server.py plugins/software-workflows/run_server.py plugins/software-workflows/hooks/hooks.json
```

Captured output:

```text
(no output)
```

</details>

<details>
<summary>Source hashes and direct whitespace audit (exit 0)</summary>

```sh
PYTHONPATH=plugins/software-workflows /opt/homebrew/bin/python3.15 -B - <<'PY'
import hashlib,json
from pathlib import Path
from coordinator.native import adapter_digest
base=Path('plugins/software-workflows')
files=[base/'server.py',base/'hooks/hooks.json',base/'hooks/coordinator_lead.py',*sorted((base/'coordinator').glob('*.py'))]
issues=[];hashes={}
for p in files:
 data=p.read_bytes();hashes[str(p.relative_to(base))]=hashlib.sha256(data).hexdigest()
 if not data.endswith(b'\n'):issues.append([str(p),'missing final newline'])
 for n,line in enumerate(data.splitlines(),1):
  if line.rstrip(b' \t')!=line:issues.append([str(p),n,'trailing whitespace'])
print('SOURCE_FILES',len(files),'ADAPTER_DIGEST',adapter_digest())
print('SOURCE_SHA256',json.dumps(hashes,sort_keys=True))
print('WHITESPACE_ISSUES',json.dumps(issues))
print('REPOSITORY_BYTECODE',len(list((base/'coordinator').rglob('*.pyc'))))
raise SystemExit(1 if issues else 0)
PY
```

Captured output:

```text
SOURCE_FILES 32 ADAPTER_DIGEST 90c5666b86628fa4a25240f8a3a99bc2c24ed5a311be64f34a2b1c2016e576e3
SOURCE_SHA256 {"coordinator/__init__.py": "42b1472bf64dff35243cbdc7d7c58ea01d546e12c7ea3d499d19feb69d85144d", "coordinator/__main__.py": "1e0b39642b662e165fba55bc0d797b5cbf1cb08f9541c3ec3c0526536565785f", "coordinator/admission.py": "bb59a2be1c436f3d8b281414a25cc225d6626138c928c0bf35c4433f8c8e1330", "coordinator/client.py": "ddcea30e7c487e0932cd5b805ad89acc69e2cf20dc0b4c84e44df5a5c76eae6e", "coordinator/compat.py": "68777614eb0494221308bea6c2e6d77e704b3323c6fd9e068267adc82282ae0c", "coordinator/custody.py": "2286caedfeeaec5c74a36d1e89630c1868ede888721ea9276030d63da95e8d61", "coordinator/daemon.py": "33cb9f411e1e2efdf1cb9c326f9f789e623600e02e695079f2f7e1aafd491ed2", "coordinator/fixture.py": "e13148742316639fcf19a930a41e0953ac68b87edd6e644e60bc41ebd5389d9b", "coordinator/gitio.py": "161eedb22a6748ff89112ad755dd6a96e3f00b3c83a44fe543e1b9961a2c9f48", "coordinator/hooks.py": "8368fed7db67a1f38a9875ee08a3db48f30f6b4aa3f501ee56925435b3a561a9", "coordinator/intents.py": "1714b5c1fcccf8f9ef9ba347d036038b25afdc26d25471345476a91af2003182", "coordinator/lead.py": "65b49f0e7086b0b8541105a7d19d02845c28b947a93f735050534bcdb2f6b5f5", "coordinator/leases.py": "0e19de924fd1339cd52d5c4672ed0692fd7aaa78acf53e3a33d2513624c29e4e", "coordinator/ledger.py": "8ec6a11a319b3907761c47cb7e31d027761994105e78fbfa7b239f7019924e87", "coordinator/locks.py": "dfbd910bd9fde55636c6bd11baf29b2c097eb3c856b8231973a2bb305b8aff7f", "coordinator/messages.py": "78fe1d8f2105eac7581bcae6d648f748112e16ce852c5a5e78a2d718b9f4d5de", "coordinator/native.py": "45afd8dd496beaded5be7051c6ae3487065d15d187fd69dbefb70e6b0c48e5a0", "coordinator/observations.py": "9541b0b75861026a8e4da70b71e00ed41312f5f1196d054bcbf3c7dfac370f5f", "coordinator/processes.py": "7e4425d68210651bd405b7516a74952fbc9d8e6d3a51d3f88c090c68541bde91", "coordinator/publish.py": "41428ebd57bd051b1d54e849b28f8154543f4479eee0140efe67046e5cdf6993", "coordinator/query.py": "e30720b7d8a73fdda45610640ad3c9801ce2f2199c357c1af6302023b99270fe", "coordinator/recovery.py": "81b0b88cc81bff976ef40eee7fbeee11ffae891ecb59bb80e65e9d9978a4685c", "coordinator/reviews.py": "681c679fe64152607194ab254d9cb7f98709864651cf3d8588e564bcbcea8b7a", "coordinator/roles.py": "0b50f9725ade18d823bcce8a7c2265f95e297bac973e7a8e19718f99349508c3", "coordinator/runs.py": "b3cf6ec3e545b6110999ab70b3d1bfd737a7961b462e2dc93e3bed938f5182d6", "coordinator/runtime.py": "c66325ec6c62d707c876b14909420365897b04065d6fc2c22b9f018f781056bb", "coordinator/store.py": "7430209231c6096e864211bb53557b0fb01e1c068725d7a3947e9f7ed6faea4c", "coordinator/tools.py": "d88ae85ea2ec3c0d72ffcea78c6f96afae986da3c026b3296b8e75ef840776df", "coordinator/writer.py": "81a07f9ab0d171314f50fbe9fdff49d4be180da9e125559f33a4e2cf583a0908", "hooks/coordinator_lead.py": "d415487a9909f7a842d8f2fca1e8cc42e1f0adf64e1e750c1a6c72226c74331d", "hooks/hooks.json": "48c43ebdf2b0627b19b7ebd830ec72007b99742f4828520e4892106d56b10498", "server.py": "b1113de266aaf86bf5e833631cfe2f109513c75c526aeb77c3a832d9ee62c383"}
WHITESPACE_ISSUES []
REPOSITORY_BYTECODE 2
```

</details>

<details>
<summary>Known generated-bytecode cleanup (exit 0)</summary>

```sh
/opt/homebrew/bin/python3.15 -B - <<'PY'
from pathlib import Path
cache=Path('plugins/software-workflows/coordinator/__pycache__')
for name in ('compat.cpython-315.pyc','__init__.cpython-315.pyc'):
 p=cache/name
 p.unlink()
 print('REMOVED_GENERATED_BYTECODE',str(p))
cache.rmdir()
print('REMAINING_COORDINATOR_BYTECODE',len(list(Path('plugins/software-workflows/coordinator').rglob('*.pyc'))))
PY
```

Captured output:

```text
REMOVED_GENERATED_BYTECODE plugins/software-workflows/coordinator/__pycache__/compat.cpython-315.pyc
REMOVED_GENERATED_BYTECODE plugins/software-workflows/coordinator/__pycache__/__init__.cpython-315.pyc
REMAINING_COORDINATOR_BYTECODE 0
```

</details>

<details>
<summary>Owned stale listener allocation cleanup and endpoint process inventory (exit 0)</summary>

```sh
PYTHONPATH=plugins/software-workflows /opt/homebrew/bin/python3.15 -B - <<'PY'
import json,os,sqlite3,subprocess
from pathlib import Path
from coordinator import processes,runtime
root=Path('/private/tmp/software-workflows-c2-wrGSzU')
with sqlite3.connect((root/'mcp3-state/ledger.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True) as db:
 for ident,raw in db.execute("SELECT id,data FROM records WHERE kind='listener'"):
  value=json.loads(raw); allocation=value.get('endpoint_allocation',{})
  if not allocation or not Path(allocation['directory']).exists():continue
  current=processes.identity(value['pid'])
  if current and current['birth']==value['birth'] and not current['zombie']:raise RuntimeError('Recorded listener still alive')
  if root not in Path(allocation['directory']).parents:raise RuntimeError('Not this run allocation')
  os.environ['SOFTWARE_WORKFLOWS_RUNTIME']=str(Path(allocation['directory']).parent)
  runtime.cleanup(allocation)
  print('CLEANED_OWNED_DEAD_LISTENER_ALLOCATION',ident,allocation['directory'])
endpoints=set()
for name in subprocess.check_output(['rg','--files','--hidden',str(root),'-g','endpoint.json'],text=True).splitlines():
 data=json.loads(Path(name).read_text())
 for value in (data.get('requested'),data.get('resolved'),data.get('allocation',{}).get('path')):
  if isinstance(value,str):endpoints.add(value)
matching=[]
for line in subprocess.check_output(['/bin/ps','-ax','-o','pid=,command='],text=True).splitlines():
 fields=line.strip().split(None,1)
 if len(fields)!=2:continue
 pid,command=int(fields[0]),fields[1]
 if 'codex' in command and 'app-server' in command and any(path in command for path in endpoints):
  current=processes.identity(pid)
  if current and not current['zombie']:matching.append({'pid':pid,'birth':current['birth']})
print('RECORDED_ENDPOINTS',len(endpoints),'LIVE_MATCHING_PRIVATE_LISTENERS',json.dumps(matching))
raise SystemExit(1 if matching else 0)
PY
```

Captured output:

```text
CLEANED_OWNED_DEAD_LISTENER_ALLOCATION listener_5214a5193f16496ca915153386aa2922 /private/tmp/software-workflows-c2-wrGSzU/rt-mcp3/srsnloqyj
CLEANED_OWNED_DEAD_LISTENER_ALLOCATION listener_5764a07dbe0145539ab38b7a790370ca /private/tmp/software-workflows-c2-wrGSzU/rt-mcp3/s73kwom6o
RECORDED_ENDPOINTS 790 LIVE_MATCHING_PRIVATE_LISTENERS []
```

</details>

<details>
<summary>Final read-only daemon/listener/provider process inventory (exit 0)</summary>

```sh
PYTHONPATH=plugins/software-workflows /opt/homebrew/bin/python3.15 -B - <<'PY'
import json,sqlite3,subprocess,os
from pathlib import Path
from collections import Counter
from coordinator.processes import identity
root=Path('/private/tmp/software-workflows-c2-wrGSzU')
paths=subprocess.check_output(['rg','--files','--hidden',str(root),'-g','ledger.sqlite','-g','fixture.json','-g','endpoint.json'],text=True).splitlines()
identities={};counts=Counter();allocations={};errors=[]
def retain(value,source):
 if isinstance(value,dict):
  if type(value.get('pid')) is int and isinstance(value.get('birth'),str):
   identities.setdefault((value['pid'],value['birth']),set()).add(source)
  allocation=value.get('endpoint_allocation') or value.get('allocation')
  if isinstance(allocation,dict) and allocation.get('directory'):allocations[allocation['directory']]=source
  for v in value.values():retain(v,source)
 elif isinstance(value,list):
  for v in value:retain(v,source)
for name in paths:
 p=Path(name)
 try:
  if p.name=='ledger.sqlite':
   counts['ledgers']+=1
   with sqlite3.connect(p.as_uri()+'?mode=ro&immutable=1',uri=True) as db:
    for kind,raw in db.execute('SELECT kind,data FROM records'):
     counts['records']+=1
     if kind=='listener':counts['listeners']+=1
     retain(json.loads(raw),str(p.parent.relative_to(root))+':'+kind)
  elif p.name=='fixture.json':
   counts['fixture_manifests']+=1;retain(json.loads(p.read_text()),str(p.parent.relative_to(root))+':fixture')
  elif p.name=='endpoint.json':
   counts['endpoint_manifests']+=1;retain(json.loads(p.read_text()),str(p.parent.relative_to(root))+':endpoint')
 except Exception as exc:errors.append({'source':str(p.relative_to(root)),'error':type(exc).__name__})
live=[];gone=0;reused=0;zombies=0
for (pid,birth),sources in identities.items():
 try:now=identity(pid)
 except Exception as exc:errors.append({'pid':pid,'error':type(exc).__name__});continue
 if now is None:gone+=1
 elif now['birth']!=birth:reused+=1
 elif now['zombie']:zombies+=1
 else:live.append(dict(now,sources=sorted(sources)))
matching=[]
for line in subprocess.check_output(['/bin/ps','-ax','-o','pid=,command='],text=True).splitlines():
 fields=line.strip().split(None,1)
 if len(fields)!=2:continue
 pid,command=int(fields[0]),fields[1]
 if pid==os.getpid():continue
 if str(root) in command and ('-m coordinator daemon' in command or '-m coordinator fixture' in command or ('codex' in command and 'app-server' in command)):
  now=identity(pid)
  if now and not now['zombie']:matching.append({'pid':pid,'birth':now['birth'],'role':'coordinator' if '-m coordinator' in command else 'private-listener'})
remaining=[{'directory':path,'source':source} for path,source in sorted(allocations.items()) if Path(path).exists()]
print(json.dumps({'counts':dict(counts),'recorded_identities':len(identities),'gone':gone,'reused_pids':reused,'zombies':zombies,'live_recorded':live,'scratch_command_matches':matching,'remaining_allocations':remaining,'default_state_exists':(Path.home()/'.local/state/software-workflows').exists(),'errors':errors},indent=2))
raise SystemExit(1 if live or matching or errors else 0)
PY
```

Captured output:

```text
{
  "counts": {
    "fixture_manifests": 14,
    "ledgers": 28,
    "records": 553,
    "endpoint_manifests": 407,
    "listeners": 59
  },
  "recorded_identities": 149,
  "gone": 149,
  "reused_pids": 0,
  "zombies": 0,
  "live_recorded": [],
  "scratch_command_matches": [],
  "remaining_allocations": [],
  "default_state_exists": false,
  "errors": []
}
```

</details>



## C3

Implementation verification on 2026-10-18. This section records observed runs,
including failures; an item is not passing merely because code was changed.
Scratch root: `/private/tmp/software-workflows-c3-3qhpdpn2`. No real-model calls
have been used. All provider calls below use the existing loopback fixture.
The checkout's Git index/refs and the user's CODEX_HOME are not used for writes.

### Hooks, schema, and initial corrections

Actual invocations used `/usr/bin/python3 -B hooks/coordinator_lead.py` with
JSON PreToolUse/PostToolUse/PostToolUseFailure payloads, and
`PYTHONDONTWRITEBYTECODE=1`. Every individual hook process exited 0.

| State | Event | Bash wall ms | Write wall ms | stdout / effects |
| --- | --- | ---: | ---: | --- |
| Default state absent | Pre | 22.061 | 21.287 | empty; no state created |
| Default state absent | Post | 20.787 | 20.711 | empty; no state created |
| Default state absent | Failure | 20.835 | 20.948 | empty; no state created |
| Scratch daemon up, unregistered target | Pre | 32.215 | 25.112 | empty |
| Scratch daemon up, unregistered target | Post | 25.220 | 24.789 | empty |
| Scratch daemon up, unregistered target | Failure | 25.007 | 25.242 | empty |
| Scratch daemon down, unregistered target | Pre | 27.382 | 25.212 | empty |
| Scratch daemon down, unregistered target | Post | 24.938 | 25.050 | empty |
| Scratch daemon down, unregistered target | Failure | 25.039 | 24.982 | empty |

Five `/usr/bin/python3 -c pass` processes exited 0 in 15.943, 15.074,
15.846, 15.221, and 15.326 ms. These are interpreter-floor measurements.
Default state remained absent before and after the hook runs. The MCP hook
retained run, images, and nudge and injected session_id into the complete input.

The registered scratch hook lifecycle exited 0. `Client.ensure` started worker
1079, generation 1, SQLite 3.54.0. A Write from a cwd outside the registered
checkout into claimed.txt was denied with the holding unit (77.192 ms).
A free.txt Write intent (43.511 ms), actual filesystem write, and Post event
(40.820 ms) produced a verified operation. After `daemon/stop` returned
`{"stopping":true}`, a registered-target edit warned once (40.396 ms), then
printed nothing (40.908 ms). With scratch state deliberately mode 0755 it
warned once (38.154 ms), then printed nothing (37.300 ms), and never denied.
New coordinator-created ancestors new-parent/new-child/hook-state were all
0700 before that deliberate chmod. The existing bad mode was not repaired.

Stdio MCP initialization and tools/list succeeded: 15 lead tools; public resume
schema includes images, and its description is 4,547 characters. All four run
tools reported the stopped native coordinator distinctly; automatic selection
refused. The first combined command exited 1 because its subsequent catalog
preflight used quoted dotted CLI keys that installed Codex rejected before
inference. After switching to nested TOML tables, the independent live catalog
preflight exited 0 and exposed only architect_review, codex_query, research;
all 12 lead tools were absent. No model requests occurred in catalog preflight.

The first concurrent native fixture MCP run exited 1: LISTENER_START caused
fallback; fallback's startup catalog was incomplete and was refused before
inference; the other request correctly encountered EXCLUSIVE_WRITER. Provider
requests: 0; provider errors: []; daemon/stop succeeded. This exposed overlapping
CLI table assignments. Configuration overrides are now coalesced before role
restrictions; catalog verification waits for starting servers before deciding.
These fixes require the later reruns recorded below.

In helper-repo, an unrestricted actual `git diff` exited 0 and printed
C3_EXTERNAL_HELPER; unrestricted `git diff --no-ext-diff` exited 0 and printed
C3_TEXTCONV_HELPER. The exact `gitio.safe_shell_git` command with
`diff --no-ext-diff --no-textconv`, both directly and with `-C`, exited 0 with
the ordinary old/new diff and neither marker; command classification permitted
those invocations. Bare diff, incomplete flags, inherited helper assignment,
an absolute git command without the isolated contract, and grep pager override
were refused. This combined operational command subsequently exited 1 because
process baseline encountered an already-existing zombie. No double-fork was
started. `ps` confirmed PID 14223 was Z+; baseline now excludes observed zombies.

Direct fixture-backed native app-server initialize exited 0 with Codex 0.163.0.
The temporary bootstrap venv preparation exited 0. AST parsing of the then-current
server/coordinator files exited 0 (31 files); final compatibility/check runs are
recorded separately below. No claim of complete C3 verification is made here.

### Publication, review, and handback operational run

The first direct daemon-operation invocation exited 1 with RECOVERING because
it had not run daemon recovery. It made no publication. The corrected fresh
pub2-state run executed the normal recovery initialization, then the actual
ledger, review, publication and handback implementations; exit **0**.

Captured output:

```text
OLD_COMPLETION_REPAIRED recorded
EVIDENCE_REUSED 1 review_54eed027faf24af8885bf04e97055873
REVIEW negative negative None
REVIEW stale refused REVIEW_BINDING
NEW_PUBLICATION complete
OBSERVED [('commit_created', True), ('index_pending', True), ('index_updated', False), ('prepared', False), ('prepared', True)]
LOCK_GONE True UNRELATED_STAGING True
OLD_JOURNAL_RECOVERED complete ORIGINAL_COMMIT True NEWER_TIP_PRESERVED True UNRELATED_STAGING True LOCK_GONE True
HANDBACK_EMPTY released=False count=0
HANDBACK_SUBSET released=True count=1 paths=['b.txt']
HANDBACK_OMITTED released=True count=2 paths=['c.txt','a.txt']
PROVIDER_REQUESTS 0
```

The lock observation is `(journal phase, index.lock exists)`, sampled through
an independent read-only SQLite connection. The prepared journal precedes lock
acquisition; the commit_created and index_pending observations were locked.
The old-format case used actual commit-tree/update-ref plumbing in the scratch
repository and a real old-format durable journal at commit_created, then a
second disjoint commit. It exercises the lost-ref-checkpoint compatibility path
through `unit/recover(action=abandon)`; it is **not** a timed SIGKILL publication
crash. No model call was used for saved-review repair.

<details><summary>Exact corrected operational command (exit 0)</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 CODEX_ORACLE_LOG_DIR=/private/tmp/software-workflows-c3-3qhpdpn2/pub-logs /opt/homebrew/bin/python3.15 -B - <<'PY'
import asyncio,base64,hashlib,json,os,sqlite3,threading,time
from pathlib import Path
from coordinator import gitio,reviews
from coordinator.daemon import Daemon
from coordinator.publish import Publisher
from coordinator.store import uid
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'pub2-repo';repo.mkdir(mode=0o700);state=root/'pub2-state'
gitio.git(repo,['init','-q']);rows=[]
for name in ('a.txt','b.txt','c.txt','d.txt'):
 (repo/name).write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');rows.append('100644 '+oid+'\t'+name+'\n')
gitio.git(repo,['update-index','--index-info'],data=''.join(rows).encode());tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'C3 baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
metadata={'message':'C3 reviewed publication','author_name':'C3','author_email':'c3@invalid','author_date':'2026-10-18T17:00:00Z','committer_name':'C3','committer_email':'c3@invalid','committer_date':'2026-10-18T17:00:00Z'}
d=Daemon(state);p=Publisher(d)
async def edit(checkout,name,session):
 a=await d.dispatch('lead/session',{'checkout':checkout['id'],'session_id':session});await d.lead_edit({'actor':a['actor'],'changes':{name:{'mode':'100644','bytes_base64':base64.b64encode((session+'\n').encode()).decode()}}});seal=await p.seal(a['unit'],[]);job=await reviews.prepare(d,{'unit':a['unit'],'cwd':str(repo)});reviews.dispatched(d,{'job':job['job'],'run':'review-'+session});return a,seal,job
async def main():
 from coordinator.recovery import recover
 await recover(d);d.ready=True
 checkout=await d.ledger.register(str(repo));a,seal,job=await edit(checkout,'a.txt','A')
 completion={'run':'review-A','thread':'fixture-thread-A','status':'ok','completed':True,'answer':'**Verdict**: Ship it\nFull saved review answer.'}
 saved=d.store.get(job['job']);saved.update(state='negative',completion=completion);d.store.save(saved)
 result=reviews.complete(d,{'job':job['job'],'completion':completion});print('OLD_COMPLETION_REPAIRED',result['state'],flush=True)
 saved=d.store.get(job['job']);saved['state']='negative';d.store.save(saved);result=reviews.complete(d,{'job':job['job'],'completion':completion});print('EVIDENCE_REUSED',len(list(d.store.all('review',a['unit']))),result['review'],flush=True)
 for label,answer in [('negative','**Verdict**: Do not ship\nFull negative answer.'),('stale','**Verdict**: Ship it\nStale answer.')]:
  j=await reviews.prepare(d,{'unit':a['unit'],'cwd':str(repo)});reviews.dispatched(d,{'job':j['job'],'run':'review-'+label})
  if label=='stale':s=d.store.get(j['job']);s['binding']['validation_digest']='stale';d.store.save(s)
  r=reviews.complete(d,{'job':j['job'],'completion':dict(completion,run='review-'+label,answer=answer)});print('REVIEW',label,r['state'],r.get('refusal'),flush=True)
 staged=gitio.text(repo,['hash-object','-w','--stdin'],data=b'unrelated staging\n');gitio.git(repo,['update-index','--cacheinfo','100644',staged,'d.txt'])
 observations=[];done=threading.Event()
 def observe():
  db=sqlite3.connect('file:'+str(state/'ledger.sqlite')+'?mode=ro',uri=True)
  while not done.is_set():
   for row in db.execute("select data from records where kind='publication'"):
    value=json.loads(row[0]);observations.append((value['phase'],(repo/'.git/index.lock').exists()))
   time.sleep(.0005)
  db.close()
 t=threading.Thread(target=observe);t.start()
 try:published=await p.publish(a['unit'],result['review'],metadata)
 finally:done.set();t.join()
 print('NEW_PUBLICATION',published['phase'],'OBSERVED',sorted(set(observations)),'LOCK_GONE',not (repo/'.git/index.lock').exists(),'UNRELATED_STAGING',gitio.entries(repo)['d.txt'][0]['oid']==staged,flush=True)
 b,seal2,job2=await edit(checkout,'b.txt','B');r=reviews.complete(d,{'job':job2['job'],'completion':dict(completion,run='review-B',thread='fixture-thread-B')})
 # Reproduce an old journal: real commit/ref installed, checkpoint still commit_created,
 # and no index ownership record (the pre-C3 gap).
 real=gitio.entries(repo);commit=gitio.text(repo,['commit-tree',seal2['candidate'],'-p',seal2['base']],data=b'Old journal installed commit\n',extra_env=meta)
 pid=uid('publication');d.store.put('publication',pid,{'unit':b['unit'],'seal':seal2['id'],'review':r['review'],'metadata':metadata,'phase':'commit_created','commit':commit,'base_index_entries':{q:real.get(q,[]) for q in seal2['paths']},'config_digest':p.publication_guards(checkout,seal2),'created':time.time()},b['unit']);gitio.reference_update(repo,seal2['branch'],commit,seal2['base'])
 idx=state/'indexes/disjoint.index';gitio.git(repo,['read-tree',commit],index=idx);oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'disjoint lead commit\n');gitio.git(repo,['update-index','--cacheinfo','100644',oid,'c.txt'],index=idx);tree=gitio.text(repo,['write-tree'],index=idx);tip=gitio.text(repo,['commit-tree',tree,'-p',commit],data=b'Disjoint lead advance\n',extra_env=meta);gitio.reference_update(repo,seal2['branch'],tip,commit)
 recovered=await d.dispatch('unit/recover',{'unit':b['unit'],'action':'abandon'});print('OLD_JOURNAL_RECOVERED',recovered['phase'],'ORIGINAL_COMMIT',recovered['commit']==commit,'NEWER_TIP_PRESERVED',gitio.text(repo,['rev-parse','HEAD'])==tip,'UNRELATED_STAGING',gitio.entries(repo)['d.txt'][0]['oid']==staged,'LOCK_GONE',not (repo/'.git/index.lock').exists(),flush=True)
 lead=await d.dispatch('lead/session',{'checkout':checkout['id'],'session_id':'handback'});actor=d.store.get(lead['actor']);unit=d.store.get(lead['unit']);await d.custody.acquire(actor,['a.txt','b.txt','c.txt'])
 print('HANDBACK_EMPTY',await d.custody.release(unit,[]),flush=True);print('HANDBACK_SUBSET',await d.custody.release(unit,['b.txt']),flush=True);print('HANDBACK_OMITTED',await d.custody.release(unit),flush=True)
 print('PROVIDER_REQUESTS',0,flush=True)
try:asyncio.run(main())
finally:
 for fd in d.ledger.ownership.values():os.close(fd)
 d.store.db.close()
PY
```
</details>

### Native admission and custodian crash observations

The corrected native2 MCP Abraham invocation exited **0**: native admission
passed, both phases completed, a.txt contained `C3 native`, and its revision
was attributed `exact` to the root actor. Provider requests **3**, errors **[]**;
`daemon/stop` returned `{"stopping":true}`.

The crash run started a real `/bin/sleep 25`, PID 14657, under native command
custody. SIGKILL custodian 13013 left the lock refused immediately. A second
actual MCP Abraham dispatch with unusable scratch state took exec fallback,
reported STATE_PERMISSIONS and the live tree-lock holder, and made **0** extra
provider requests. The old-format legacy bridge remained live. Worker 13016
survived, transferred its same acquired lock to replacement custodian 14721,
then was itself SIGKILLed. Exclusion and the legacy bridge remained live;
worker 14784 recovered at generation 2.

The combined crash/image command later exited **1**. Process uncertainty
included the new custodian and newly started unrelated OS services. It did
not release custody or send the requested continuation. The active-run image
call reported RUN_ACTIVE/non-delivery; missing-image and empty collection
calls each added **0** provider requests. Total provider requests **3**, image
inputs **[0,0,0]**, provider errors **[]**. This is not image-transport evidence.

The controller's exact birth identity is now attested through the lease channel.
Only that identity is exempted, never its later descendants. Transient process
uncertainty retains custody without manufacturing filesystem authorship.
Reloading to generation 3 succeeded, but unrelated new mdworker/extension
identities still conservatively blocked settlement. Normal stop refused
NOT_QUIESCENT. After native terminal inventory returned `data=[]`, the scratch
custodian, worker and recorded listener (14721,16862,13083) were explicitly
stopped by verified birth identities. That cleanup exited **0**, OWNED_LIVE [].
No unrelated ambiguous process was signalled. The retained scratch journal
records the unresolved uncertainty; successful post-crash settlement is not
claimed for this run.

An immediate markerless double-fork operational run observed the intermediate
exit 0 and surviving /bin/sleep PID 12039. Two inventories returned
PROCESS_AMBIGUOUS and included that PID; `osPid:null` stayed unbound. After its
exit, a different new process still blocked a complete inventory. Listener
cleanup exited -15 and the enclosing command exited **0**. The run proves
retention of the missed-lineage survivor, but not full-system settlement.


### Additional C3 operational runs

All commands below are one-off inline invocations of the product and existing
fixture provider, not added test files. Each uses disposable scratch state and
CODEX_HOME; no user's configuration is copied. Request counts are provider-side.

#### Concurrent MCP regression flow

Exit **0**. Two native Abraham units completed with exact attribution and zero UNKNOWN entries. An overlapping claim was refused with its holder; message/inbox rereads retained one unread message. Unit A sealed, received a real MCP code_review result bound to all seven fields, and published while B was still running. Unrelated staged c.txt remained staged. Empty collection, missing caller image, and list mode each added zero provider requests. Provider: 10 requests, errors=[]. daemon/stop returned stopping=true. The tiny.png fixture used here was rejected by Codex's image decoder; all provider image counts were zero. This is concurrency/publication evidence, NOT successful image transport evidence; the valid-image runs below supersede that portion.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c3-3qhpdpn2/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes,native
from coordinator.fixture import Provider,message,patch,function
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'images-repo';repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n');(repo/'b.txt').write_text('baseline\n');(repo/'c.txt').write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['update-index','--add','--cacheinfo','100644',oid,'a.txt']);gitio.git(repo,['update-index','--add','--cacheinfo','100644',oid,'b.txt']);gitio.git(repo,['update-index','--add','--cacheinfo','100644',oid,'c.txt']);tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'C3 baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
home=root/'images-home';home.mkdir(mode=0o700);fixture=root/'images-fixture';fixture.mkdir(mode=0o700);state=root/'images-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'images-rt'),CODEX_ORACLE_LOG_DIR=str(root/'images-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='180',TMPDIR=str(root))
for k in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(k,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
def router(body,index):
 data=body.get('input',[]);users=[i for i in data if i.get('role')=='user'];last=json.dumps(users[-1] if users else {})
 if 'IMMUTABLE CANDIDATE BINDING' in json.dumps(data):return message('**Verdict**: Ship it\n**Findings**: None. Only the claimed file changed.\n**Sources**: no external claims made.')
 if 'Continue the same task' in last:return message('Image continuation received.')
 name='b.txt' if 'C3_B' in last else 'a.txt'
 if 'PHASE 2 of 2' not in last:return message('Brief: change only '+name+' using apply_patch.')
 calls=[i.get('name') for i in data if i.get('type') in ('function_call','custom_tool_call')]
 if 'apply_patch' in calls:return message('Implemented '+name+'.')
 if name=='a.txt' and 'send' not in calls:return function('send',{'recipient':'lead','text':'C3 A implementation message','wake':'none'})
 if name=='b.txt':time.sleep(35)
 return patch('*** Begin Patch\n*** Update File: '+name+'\n@@\n-baseline\n+C3 native\n*** End Patch\n')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 async with stdio_client(params,errlog=open(root/'images-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   async def call(name,args):
    text=plain(await s.call_tool(name,args));print(name,text[:2300],flush=True);return text
   a=asyncio.create_task(call('abraham',{'task':'C3_A Change only a.txt using apply_patch.','cwd':str(repo),'claims':['a.txt']}))
   b=asyncio.create_task(call('abraham',{'task':'C3_B Change only b.txt using apply_patch.','cwd':str(repo),'claims':['b.txt']}))
   await a
   c=Client(state,timeout=40)
   async def rpc(method,args):return await asyncio.to_thread(c.call,method,args)
   runs=(await rpc('run/list',{'cwd':'/'}))['data'];ra=next(r for r in runs if any(q['path']=='a.txt' for q in r['claims']));rb=next(r for r in runs if any(q['path']=='b.txt' for q in r['claims']))
   await call('coordinator_claims',{'action':'acquire','paths':['a.txt'],'cwd':str(repo),'session_id':'C3-images-lead'})
   await call('coordinator_inbox',{'cwd':str(repo)});await call('coordinator_inbox',{'cwd':str(repo)})
   before=len(provider.requests);await call('codex_resume_run',{'run':ra['run']});print('COLLECTION_DELTA',len(provider.requests)-before,flush=True)
   before=len(provider.requests);await call('codex_resume_run',{'run':ra['run'],'images':[str(root/'missing.png')]});print('MISSING_DELTA',len(provider.requests)-before,flush=True)
   for nudge in ('','Explicit image continuation'):
    await call('codex_resume_run',{'run':ra['run'],'images':[str(root/'tiny.png')],'nudge':nudge})
    for n in range(150):
     status=await rpc('run/status',{'run':ra['run']})
     if status['state'] in ('completed','failed','blocked'):break
     await asyncio.sleep(.1)
    phase=status['phases'][status['phase']];print('IMAGE_PHASE',status['state'],phase['input_id'],phase['input_digest'],phase['input'],flush=True)
   before=len(provider.requests);listing=plain(await s.call_tool('codex_resume_run',{'run':'list','images':[str(root/'tiny.png')]}));print('LIST_NOTE',listing[-160:],'DELTA',len(provider.requests)-before,flush=True)
   staged=gitio.text(repo,['hash-object','-w','--stdin'],data=b'unrelated staging\n');gitio.git(repo,['update-index','--cacheinfo','100644',staged,'c.txt'])
   await call('coordinator_seal',{'unit':ra['unit'],'cwd':str(repo)})
   await call('code_review',{'sealed_unit':ra['unit'],'cwd':str(repo),'session':'new','infra':True})
   jobs=(await rpc('review/jobs',{'cwd':str(repo)}))['data'];print('REVIEW_JOBS',[(j['state'],j.get('verdict'),j.get('review')) for j in jobs],flush=True)
   passing=next((j for j in jobs if j.get('review')),None)
   if passing:
    metadata={'message':'C3 A publication','author_name':'C3','author_email':'c3@invalid','author_date':'2026-10-18T17:00:00Z','committer_name':'C3','committer_email':'c3@invalid','committer_date':'2026-10-18T17:00:00Z'}
    await call('coordinator_publish',{'unit':ra['unit'],'review':passing['review'],'metadata':metadata,'cwd':str(repo)})
    print('PUBLISHED_WHILE_B',(await rpc('run/status',{'run':rb['run']}))['state'],'STAGING_PRESERVED',gitio.entries(repo)['c.txt'][0]['oid']==staged,flush=True)
   await b
   print('FINAL_RUNS',[(r['run'],r['state'],r['unknown_count']) for r in (await rpc('run/list',{'cwd':'/'}))['data']],flush=True)
   print('IMAGE_COUNTS',[sum(c.get('type')=='input_image' for item in b.get('input',[]) for c in (item.get('content',[]) if isinstance(item.get('content'),list) else [])) for b in provider.requests],flush=True)
with Provider(router=router) as provider:
 provider.config['mcp_servers.software-workflows']={'command':'/opt/homebrew/bin/python3.15','args':['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],'env':{k:env[k] for k in ('CLAUDE_PLUGIN_DATA','SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')}}
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
  try:print('STOP',Client(state,timeout=40).call('daemon/stop'),flush=True)
  except Exception as e:print('STOP_ERROR',getattr(e,'code',type(e).__name__),flush=True)
PY
```

</details>

#### Exec fallback, public resume images, collection and list mode

Exit **0**. Forced native admission with SOFTWARE_WORKFLOWS_CODEX=/bin/false refused with NATIVE_START_FileNotFoundError; the actual exec binary remained functioning. Both Abraham phases completed through exclusive exec fallback. Completed empty collection, missing caller image, and list mode had request delta 0; image-only and explicit-nudge continuations each had delta 1. Provider: 4 requests, errors=[], input_image counts [0, 0, 1, 2] (the request includes conversation history). daemon/stop returned stopping=true.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c3-3qhpdpn2/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes,native
from coordinator.fixture import Provider,message,patch
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'exec-valid-repo';repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['update-index','--add','--cacheinfo','100644',oid,'a.txt']);tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'C3 baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
home=root/'exec-valid-home';home.mkdir(mode=0o700);fixture=root/'exec-valid-fixture';fixture.mkdir(mode=0o700);state=root/'exec-valid-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'exec-valid-rt'),CODEX_ORACLE_LOG_DIR=str(root/'exec-valid-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='180',SOFTWARE_WORKFLOWS_CODEX='/bin/false',TMPDIR=str(root))
for k in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(k,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR','SOFTWARE_WORKFLOWS_CODEX')})
def router(body,index):
 users=[i for i in body.get('input',[]) if i.get('role')=='user'];last=json.dumps(users[-1] if users else {})
 if 'PHASE 2 of 2' in last:return message('Implementation complete; no file change needed.')
 return message('Complete fixture answer.')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 async with stdio_client(params,errlog=open(root/'exec-valid-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   async def call(name,args):
    text=plain(await s.call_tool(name,args));print(name,text[:3500],flush=True);return text
   await call('abraham',{'task':'C3 forced native failure; two exec phases, no file changes.','cwd':str(repo),'claims':['a.txt']})
   events=[json.loads(line) for line in (root/'exec-valid-logs/runs.jsonl').read_text().splitlines()]
   starts=[e for e in events if e.get('phase')=='start' and e.get('write')];print('EXEC_STARTS',[(e['run'],e.get('images'),e.get('write')) for e in starts],flush=True)
   if not starts:raise RuntimeError('No exec implementation started')
   rid=starts[-1]['run']
   before=len(provider.requests);await call('codex_resume_run',{'run':rid});print('COLLECT_DELTA',len(provider.requests)-before,flush=True)
   before=len(provider.requests);await call('codex_resume_run',{'run':rid,'images':[str(root/'missing.png')]});print('MISSING_DELTA',len(provider.requests)-before,flush=True)
   for nudge in ('','Explicit exec image continuation'):
    before=len(provider.requests);await call('codex_resume_run',{'run':rid,'images':[str(root/'valid.png')],'nudge':nudge});print('CONTINUE_DELTA',len(provider.requests)-before,flush=True)
   before=len(provider.requests);text=plain(await s.call_tool('codex_resume_run',{'run':'list','images':[str(root/'valid.png')]}));print('LIST_NONDELIVERY',text[-220:],'DELTA',len(provider.requests)-before,flush=True)
   print('IMAGE_COUNTS',[sum(c.get('type')=='input_image' for item in b.get('input',[]) for c in (item.get('content',[]) if isinstance(item.get('content'),list) else [])) for b in provider.requests],flush=True)
with Provider(router=router) as provider:
 provider.config['mcp_servers.software-workflows']={'command':'/opt/homebrew/bin/python3.15','args':['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],'env':{k:env[k] for k in ('CLAUDE_PLUGIN_DATA','SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')}}
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
  try:print('STOP',Client(state,timeout=40).call('daemon/stop'),flush=True)
  except Exception as e:print('STOP_ERROR',getattr(e,'code',type(e).__name__),flush=True)
PY
```

</details>

#### Interrupted exec image recovery and restricted analysis-phase resume

Exit **0**. Cancelled the actual implementation Codex process (PID 39065, exit -9); the owner recorded cancelled. Empty cancelled resume refused, request delta 0. Removed the historical scratch image, then resumed explicitly: output named the missing historical path, reattached surviving valid.png, and made one request. Resumed the original analysis phase with a nudge: one request. Four start journal entries recorded the restricted writer catalog (architect_review, codex_query, research only), including both initial phases, implementation recovery, and the custody-holding read/analysis resume. Provider: 4 requests, errors=[], image counts [2, 2, 3, 2]. daemon/stop returned stopping=true.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c3-3qhpdpn2/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes,native
from coordinator.fixture import Provider,message,patch
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'exec-recovery-repo';repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['update-index','--add','--cacheinfo','100644',oid,'a.txt']);tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'C3 baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
home=root/'exec-recovery-home';home.mkdir(mode=0o700);fixture=root/'exec-recovery-fixture';fixture.mkdir(mode=0o700);state=root/'exec-recovery-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'exec-recovery-rt'),CODEX_ORACLE_LOG_DIR=str(root/'exec-recovery-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='180',SOFTWARE_WORKFLOWS_CODEX='/bin/false',TMPDIR=str(root))
for k in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(k,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR','SOFTWARE_WORKFLOWS_CODEX')})
def router(body,index):
 users=[i for i in body.get('input',[]) if i.get('role')=='user'];last=json.dumps(users[-1] if users else {})
 if 'C3_EXEC_WAIT' in last and 'RECOVERY CONTEXT' not in last and 'PHASE 2 of 2' in last:time.sleep(5)
 return message('Fixture answer completed.')
def plain(r):return '\\n'.join(c.text for c in r.content if c.type=='text')
async def main():
 historical=root/'exec-historical.png';historical.write_bytes((root/'valid.png').read_bytes())
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 async with stdio_client(params,errlog=open(root/'exec-recovery-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   job=asyncio.create_task(s.call_tool('abraham',{'task':'C3_EXEC_WAIT complete without edits.','cwd':str(repo),'claims':['a.txt'],'images':[str(root/'valid.png'),str(historical)]}))
   for n in range(600):
    if len(provider.requests)>=2:break
    if job.done():raise RuntimeError(plain(await job))
    await asyncio.sleep(.05)
   events=[json.loads(line) for line in (root/'exec-recovery-logs/runs.jsonl').read_text().splitlines()]
   starts=[e for e in events if e.get('phase')=='start']
   rid=starts[-1]['run'];analysis=starts[0]['run']
   print('CANCEL',plain(await s.call_tool('codex_cancel_run',{'run':rid})),flush=True)
   print('CANCELLED_JOB',plain(await job)[:600],flush=True)
   before=len(provider.requests);print('EMPTY_CANCELLED',plain(await s.call_tool('codex_resume_run',{'run':rid})),'DELTA',len(provider.requests)-before,flush=True)
   historical.unlink()
   before=len(provider.requests);print('RECOVERY',plain(await s.call_tool('codex_resume_run',{'run':rid,'nudge':'Recover and finish.'}))[:1800],'DELTA',len(provider.requests)-before,flush=True)
   before=len(provider.requests);print('ANALYSIS_CUSTODY_RESUME',plain(await s.call_tool('codex_resume_run',{'run':analysis,'nudge':'Continue the analysis, no edits.'}))[:600],'DELTA',len(provider.requests)-before,flush=True)
   starts=[json.loads(line) for line in (root/'exec-recovery-logs/runs.jsonl').read_text().splitlines() if json.loads(line).get('phase')=='start']
   print('ROLE_EVIDENCE',[(x['tool'],x.get('write'),bool(x.get('custody_cwd')),x.get('writer_mcp_catalog')) for x in starts],flush=True)
   print('IMAGES',[sum(c.get('type')=='input_image' for item in body.get('input',[]) for c in (item.get('content',[]) if isinstance(item.get('content'),list) else [])) for body in provider.requests],flush=True)
with Provider(router=router) as provider:
 provider.config['mcp_servers.software-workflows']={'command':'/opt/homebrew/bin/python3.15','args':['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],'env':{k:env[k] for k in ('CLAUDE_PLUGIN_DATA','SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')}}
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
  try:print('STOP',Client(state,timeout=40).call('daemon/stop'),flush=True)
  except Exception as e:print('STOP_ERROR',getattr(e,'code',type(e).__name__),flush=True)
PY
```

</details>

#### Native workflow and non-workflow image continuation

Exit **0**. Workflow image-only and explicit-nudge continuations each completed with request delta 1. The interrupted continuation reached cancelled; empty cancelled collection had delta 0. Recovery retained valid.png from the interrupted phase input and explicitly reported the missing historical image. A missing newly supplied image refused with delta 0. A non-workflow native continuation with localImage completed with delta 1. Provider: 7 requests, errors=[], image counts [0, 0, 1, 2, 4, 5, 1]. These counts include historical inputs; the newly supplied image was present in each intended continuation. daemon/stop returned stopping=true. No real model was used, so image interpretation is UNVERIFIED.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c3-3qhpdpn2/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes,native
from coordinator.fixture import Provider,message,patch
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'native-image-final-repo';repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['update-index','--add','--cacheinfo','100644',oid,'a.txt']);tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'C3 baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
home=root/'native-image-final-home';home.mkdir(mode=0o700);fixture=root/'native-image-final-fixture';fixture.mkdir(mode=0o700);state=root/'native-image-final-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'native-image-final-rt'),CODEX_ORACLE_LOG_DIR=str(root/'native-image-final-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='180',TMPDIR=str(root))
for k in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(k,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
def router(body,index):
 users=[i for i in body.get('input',[]) if i.get('role')=='user'];last=json.dumps(users[-1] if users else {})
 if 'C3_WAIT' in last:time.sleep(5)
 return message('Fixture answer; input received.')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 async with stdio_client(params,errlog=open(root/'native-image-final-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   async def call(name,args):
    text=plain(await s.call_tool(name,args));print(name,text[:1100],flush=True);return text
   await call('abraham',{'task':'C3 images: report completion without edits.','cwd':str(repo),'claims':['a.txt']})
   c=Client(state,timeout=40)
   async def rpc(method,args=None):return await asyncio.to_thread(c.call,method,args)
   rid=(await rpc('run/list',{'cwd':'/'}))['data'][0]['run']
   async def settled(rid):
    for n in range(200):
     v=await rpc('run/status',{'run':rid})
     if v['state'] in ('completed','failed','cancelled','blocked'):return v
     await asyncio.sleep(.1)
    raise RuntimeError('Native turn did not settle')
   for nudge in ('','Explicit valid image nudge'):
    count=len(provider.requests)
    await call('codex_resume_run',{'run':rid,'images':[str(root/'valid.png')],'nudge':nudge});v=await settled(rid)
    print('CONTINUATION',v['state'],'REQUEST_DELTA',len(provider.requests)-count,'LAST_IMAGES',image_count(provider.requests[-1]),flush=True)
   historical=root/'historical.png';historical.write_bytes((root/'valid.png').read_bytes())
   count=len(provider.requests);await call('codex_resume_run',{'run':rid,'nudge':'C3_WAIT until interrupted','images':[str(root/'valid.png'),str(historical)]})
   for n in range(100):
    if len(provider.requests)>count:break
    await asyncio.sleep(.05)
   await call('codex_cancel_run',{'run':rid});v=await settled(rid);print('INTERRUPTED',v['state'],'INPUT_IMAGES',image_count(provider.requests[-1]),flush=True)
   historical.unlink()
   count=len(provider.requests);await call('codex_resume_run',{'run':rid});print('NATIVE_EMPTY_CANCELLED_COLLECTION_DELTA',len(provider.requests)-count,flush=True)
   await call('codex_resume_run',{'run':rid,'nudge':'Continue after interruption.'});v=await settled(rid);phase=v['phases'][v['phase']];print('RECOVERED_IMAGES',[x['path'] for x in phase['input'] if x['type']=='localImage'],'REQUEST_IMAGES',image_count(provider.requests[-1]),flush=True)
   count=len(provider.requests);await call('codex_resume_run',{'run':rid,'images':[str(historical)]});print('MISSING_NEW_DELTA',len(provider.requests)-count,flush=True)
   checkout=v['checkout'];other=await rpc('run/start',{'checkout':checkout,'mode':'native','fixture':str(fixture)})
   count=len(provider.requests);r=await rpc('run/resume',{'run':other['run'],'images':[str(root/'valid.png')]});v=await settled(other['run']);print('NONWORKFLOW_IMAGES',v['state'],'DELTA',len(provider.requests)-count,'LAST_IMAGES',image_count(provider.requests[-1]),flush=True)
   print('COUNTS',[image_count(b) for b in provider.requests],flush=True)
def image_count(body):
 return sum(c.get('type')=='input_image' for item in body.get('input',[]) for c in (item.get('content',[]) if isinstance(item.get('content'),list) else []))
with Provider(router=router) as provider:
 provider.config['mcp_servers.software-workflows']={'command':'/opt/homebrew/bin/python3.15','args':['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],'env':{k:env[k] for k in ('CLAUDE_PLUGIN_DATA','SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')}}
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
  try:print('STOP',Client(state,timeout=40).call('daemon/stop'),flush=True)
  except Exception as e:print('STOP_ERROR',getattr(e,'code',type(e).__name__),flush=True)
PY
```

</details>

#### Actual MCP owner shutdown and detached collection

Exit **0**. Sent SIGTERM to MCP owner 43365 while Codex child 43375 and watchdog 43377 were running. The first MCP call ended with the expected McpError and the journal contained detached. A new MCP server received images plus a nudge: output said the run was still detached and explicitly said both were not delivered; provider delta 0. Empty resume then collected 'Detached fixture answer completed.' with no re-ask. Provider total 1, errors=[]. No coordinator was started; the final stop attempt correctly returned FileNotFoundError.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c3-3qhpdpn2/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes,native
from coordinator.fixture import Provider,message,patch
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'detached-final-repo';repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['update-index','--add','--cacheinfo','100644',oid,'a.txt']);tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'C3 baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
home=root/'detached-final-home';home.mkdir(mode=0o700);fixture=root/'detached-final-fixture';fixture.mkdir(mode=0o700);state=root/'detached-final-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'detached-final-rt'),CODEX_ORACLE_LOG_DIR=str(root/'detached-final-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='180',SOFTWARE_WORKFLOWS_CODEX='/bin/false',TMPDIR=str(root))
for k in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(k,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR','SOFTWARE_WORKFLOWS_CODEX')})
def router(body,index):
 time.sleep(8)
 return message('Detached fixture answer completed.')
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 try:
  async with stdio_client(params,errlog=open(root/'detached-first-stderr.log','w')) as streams:
   async with ClientSession(*streams) as s:
    await s.initialize()
    job=asyncio.create_task(s.call_tool('codex_query',{'prompt':'C3 detach collection fixture; give the final answer.','infra':True,'web_search':False}))
    for n in range(600):
     if provider.requests:break
     if job.done():raise RuntimeError(plain(await job))
     await asyncio.sleep(.05)
    events=[json.loads(line) for line in (root/'detached-final-logs/runs.jsonl').read_text().splitlines()]
    spawn=next(e for e in reversed(events) if e.get('phase')=='spawn');rid=spawn['run']
    print('DETACH_OWNER',spawn['server_pid'],'CHILD',spawn['pid'],'WATCHDOG',spawn['watchdog_pid'],flush=True)
    import signal
    os.kill(spawn['server_pid'],signal.SIGTERM)
    try:print('FIRST_CALL',plain(await job)[:300],flush=True)
    except Exception as e:print('EXPECTED_OWNER_SHUTDOWN',type(e).__name__,flush=True)
 except BaseException as e:
  if isinstance(e,(KeyboardInterrupt,SystemExit)):raise
  print('FIRST_SESSION_CLOSED',type(e).__name__,flush=True)
 if not locals().get('rid'):raise RuntimeError('No detached run was started')
 events=[json.loads(line) for line in (root/'detached-final-logs/runs.jsonl').read_text().splitlines()]
 print('DETACHED_JOURNAL',any(e.get('run')==rid and e.get('phase')=='detached' for e in events),flush=True)
 async with stdio_client(params,errlog=open(root/'detached-second-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize();before=len(provider.requests)
   value=plain(await s.call_tool('codex_resume_run',{'run':rid,'images':[str(root/'valid.png')],'nudge':'Do not silently lose this instruction.'}))
   print('DETACHED_INSTRUCTIONS',value[:1800],'REQUEST_DELTA',len(provider.requests)-before,flush=True)
   if 'Not delivered with this call' not in value:raise RuntimeError('Missing detached non-delivery note')
   value=plain(await s.call_tool('codex_resume_run',{'run':rid}))
   print('DETACHED_COLLECT',value[:1000],'TOTAL_REQUESTS',len(provider.requests),flush=True)
   if len(provider.requests)!=before:raise RuntimeError('Detached collection caused inference')
with Provider(router=router) as provider:
 provider.config['mcp_servers.software-workflows']={'command':'/opt/homebrew/bin/python3.15','args':['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],'env':{k:env[k] for k in ('CLAUDE_PLUGIN_DATA','SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')}}
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
  try:print('STOP',Client(state,timeout=40).call('daemon/stop'),flush=True)
  except Exception as e:print('STOP_ERROR',getattr(e,'code',type(e).__name__),flush=True)
PY
```

</details>

#### Redundant lock protocol, lost acknowledgments and custodian replacement

Exit **0**. Actual production custodian and worker lease protocol, with real kernel locks and bridge files: initial acquisition true; a broken channel reconnected to the same custodian; lost acquisition acknowledgment recovered; lost release acknowledgment recovered; SIGKILL of the custodian returned -9; a new custodian restored redundancy. At every stage an independent process's flock attempt was refused and the legacy bridge was live. Final release returned true, worker held count 0, legacy holder false. Both controller processes stopped. No provider requests.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.15 -B - <<'PY'
import asyncio,fcntl,json,os,socket,subprocess,sys,time
from pathlib import Path
from coordinator import client,leases,processes
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');state=root/'lease-protocol2-state';client.validate_state(state,create=True)
os.environ.update(SOFTWARE_WORKFLOWS_RUNTIME=str(root/'lease-protocol2-rt'),CODEX_ORACLE_LOG_DIR=str(root/'lease-protocol2-logs'),CODEX_HOME=str(root/'lease-protocol2-home'))
from coordinator import compat
repo=root/'exec-valid-repo'
parent,child=socket.socketpair();env=client.launch_environment();env.update(SOFTWARE_WORKFLOWS_RECOVERY_FD=str(child.fileno()),SOFTWARE_WORKFLOWS_RECOVERY_WORKER=json.dumps(processes.identity(os.getpid())),SOFTWARE_WORKFLOWS_RECOVERY_LEASES='[]')
supervisor=subprocess.Popen([sys.executable,'-B','-m','coordinator','daemon','serve','--state',str(state)],env=env,pass_fds=(child.fileno(),),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);child.close()
os.environ['SOFTWARE_WORKFLOWS_CUSTODIAN_FD']=str(parent.detach());worker=leases.Leases(state)
def excluded():
 path=next(iter(worker.held));code='import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
 r=subprocess.run([sys.executable,'-B','-c',code,path],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 print('MODERN_LOCK_REFUSED',r.returncode!=0,'LEGACY_LIVE',bool(compat._legacy_write_lock_holder(str(repo))),flush=True)
 if r.returncode==0:raise RuntimeError('Lock exclusion lost')
async def main():
 print('ACQUIRE',(await worker.call('acquire',str(repo)))['ok'],flush=True);excluded()
 old=worker.controller['pid'];worker.connection.shutdown(socket.SHUT_RDWR)
 print('BROKEN_CHANNEL',(await worker.call('ping'))['ok'],'SAME_CUSTODIAN',worker.controller['pid']==old,flush=True);excluded()
 # The real custodian sends a duplicate, but its acknowledgment is lost.
 leases.send(worker.connection,{'action':'acquire','cwd':str(repo),'worker':os.getpid()});reply,fds=leases.receive(worker.connection,1);compat.adopt_lease(reply['lease'],fds[0]);worker.held[reply['lease']['path']]=reply['lease'];worker.connection.close()
 print('LOST_ACQUIRE_ACK',(await worker.call('ping'))['ok'],flush=True);excluded()
 # Lose a release receipt after the custodian has closed its copy. The worker
 # still owns the original lock, then restores both copies on reconnection.
 leases.send(worker.connection,{'action':'release','cwd':str(repo),'worker':os.getpid()});time.sleep(.1);worker.connection.close()
 print('LOST_RELEASE_ACK',(await worker.call('ping'))['ok'],flush=True);excluded()
 os.kill(worker.controller['pid'],9);supervisor.wait(timeout=5);print('CUSTODIAN_EXIT',supervisor.returncode,flush=True);excluded()
 print('REPLACEMENT',(await worker.call('ping'))['ok'],'NEW_PID',worker.controller['pid']!=old,flush=True);excluded()
 print('RELEASE',(await worker.call('release',str(repo)))['ok'],'HELD',len(worker.held),'LEGACY_LIVE',bool(compat._legacy_write_lock_holder(str(repo))),flush=True)
try:asyncio.run(main())
finally:
 if worker.connection:worker.connection.close()
 for p in (supervisor,worker.replacement):
  if p is not None and p.poll() is None:p.terminate();p.wait(timeout=5)
 print('CONTROLLERS_STOPPED',all(p is None or p.poll() is not None for p in (supervisor,worker.replacement)),flush=True)
PY
```

</details>

#### Exec-first/native-second compatibility exclusion

Exit **0**. Acquired the actual exec compatibility lock first. Native custodian acquisition refused EXCLUSIVE_WRITER and identified the live legacy bridge. After exec release, native acquisition succeeded. An independent modern flock contender was refused and the legacy holder remained live until native release. All controllers stopped. This is the production admission-lock path without inference, not another model scenario.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.15 -B - <<'PY'
import asyncio,fcntl,json,os,socket,subprocess,sys,time
from pathlib import Path
from coordinator import client,leases,processes
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');state=root/'inverse-lock2-state';client.validate_state(state,create=True)
os.environ.update(SOFTWARE_WORKFLOWS_RUNTIME=str(root/'inverse-lock2-rt'),CODEX_ORACLE_LOG_DIR=str(root/'inverse-lock2-logs'),CODEX_HOME=str(root/'inverse-lock2-home'))
from coordinator import compat
repo=root/'exec-valid-repo'
parent,child=socket.socketpair();env=client.launch_environment();env.update(SOFTWARE_WORKFLOWS_RECOVERY_FD=str(child.fileno()),SOFTWARE_WORKFLOWS_RECOVERY_WORKER=json.dumps(processes.identity(os.getpid())),SOFTWARE_WORKFLOWS_RECOVERY_LEASES='[]')
supervisor=subprocess.Popen([sys.executable,'-B','-m','coordinator','daemon','serve','--state',str(state)],env=env,pass_fds=(child.fileno(),),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);child.close()
os.environ['SOFTWARE_WORKFLOWS_CUSTODIAN_FD']=str(parent.detach());worker=leases.Leases(state)
def excluded():
 path=next(iter(worker.held));code='import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
 r=subprocess.run([sys.executable,'-B','-c',code,path],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 print('MODERN_LOCK_REFUSED',r.returncode!=0,'LEGACY_LIVE',bool(compat._legacy_write_lock_holder(str(repo))),flush=True)
 if r.returncode==0:raise RuntimeError('Lock exclusion lost')
async def main():
 acquired,holder=compat._acquire_write_lock(str(repo),'C3-exec-first')
 print('EXEC_COMPAT_ACQUIRED',acquired,flush=True)
 if not acquired:raise RuntimeError(holder)
 try:
  try:await worker.call('acquire',str(repo))
  except client.CoordinatorError as e:
   print('NATIVE_ADMISSION_WITH_EXEC',e.code,str(e),flush=True)
   if e.code!='EXCLUSIVE_WRITER':raise
  else:raise RuntimeError('Native acquisition bypassed exec lock')
 finally:compat._release_write_lock(str(repo))
 print('NATIVE_AFTER_EXEC_RELEASE',(await worker.call('acquire',str(repo)))['ok'],flush=True)
 excluded()
 print('RELEASE',(await worker.call('release',str(repo)))['ok'],flush=True)
try:asyncio.run(main())
finally:
 if worker.connection:worker.connection.close()
 for p in (supervisor,worker.replacement):
  if p is not None and p.poll() is None:p.terminate();p.wait(timeout=5)
 print('CONTROLLERS_STOPPED',all(p is None or p.poll() is not None for p in (supervisor,worker.replacement)),flush=True)
PY
```

</details>

#### Immediate markerless double fork with unavailable terminal PID

Exit **0**. Captured 404 baseline birth identities, then ran an immediate double fork without delaying intermediate exit. Intermediate exited 0; markerless /bin/sleep 1 survivor PID 35044 remained in PROCESS_AMBIGUOUS on two inventories. After its verified exit, inventory returned []. osPid:null produced None, never a reinterpretation of processId. Listener cleanup exit -15. The earlier run (already described above) retained a separate newly appeared process after the target descendant exited; it did not falsely release.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.15 -B - <<'PY'
import os,subprocess,time,json
from coordinator import processes
from coordinator.client import CoordinatorError
listener=subprocess.Popen(['/bin/sleep','15'])
try:
 origin=processes.identity(listener.pid);before=processes.baseline();print('BASELINE',len(before),flush=True)
 code='import os; p=os.fork();\nif p: os._exit(0)\nos.setsid(); p=os.fork();\nif p: os._exit(0)\nprint(os.getpid(),flush=True);os.execve("/bin/sleep",["sleep","1"],{})'
 launched=subprocess.Popen(['/opt/homebrew/bin/python3.15','-B','-c',code],stdout=subprocess.PIPE,text=True)
 pid=int(launched.stdout.readline());launched.stdout.close();print('INTERMEDIATE_EXIT',launched.wait(), 'SURVIVOR',pid,flush=True)
 operation={'native':{'thread':'C3-doublefork','call':'opaque-call'}};ambiguous=[]
 for iteration in range(2):
  try:print('INVENTORY',processes.inventory(origin,'C3-doublefork',operation=operation,baseline=before,ambiguous=ambiguous),flush=True)
  except CoordinatorError as e:
   ambiguous=e.details.get('processes',ambiguous);print('CUSTODY',e.code,'CONTAINS_SURVIVOR',pid in e.details.get('pids',[]),flush=True)
  time.sleep(.1)
 time.sleep(1.1)
 try:print('AFTER_EXIT',processes.inventory(origin,'C3-doublefork',operation=operation,baseline=before,ambiguous=ambiguous),flush=True)
 except CoordinatorError as e:print('AFTER_EXIT_REFUSAL',e.code,{'pids':e.details.get('pids')},flush=True)
 print('OPAQUE_NULL_PID',processes.terminal_root(origin,operation,{'osPid':None,'processId':'opaque-not-a-pid'}),flush=True)
finally:listener.terminate();print('LISTENER_EXIT',listener.wait(),flush=True)
PY
```

</details>

#### Publication foreign-lock refusal and final recovery/review/handback rerun

Exit **0**. Old completed Ship it review repaired to recorded; matching evidence reused (exactly 1). Negative remained negative; stale binding refused REVIEW_BINDING. A foreign index.lock produced INDEX_LOCKED with FOREIGN_PRESERVED=True and HEAD_UNCHANGED=True. New publication completed: observed commit_created and index_pending with index.lock present; final owned lock gone and unrelated staging preserved. Old-format commit_created journal with its commit already installed below a newer disjoint tip recovered to complete without recreating the commit or moving the newer tip; unrelated staging preserved. Handback explicit [] released false/count0; subset b.txt released count1; omitted paths released a.txt and c.txt/count2. Provider requests 0. This uses actual Git objects/refs and real journals; it is not a timed SIGKILL publication crash.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 CODEX_ORACLE_LOG_DIR=/private/tmp/software-workflows-c3-3qhpdpn2/pub-logs /opt/homebrew/bin/python3.15 -B - <<'PY'
import asyncio,base64,hashlib,json,os,sqlite3,threading,time
from pathlib import Path
from coordinator import gitio,reviews
from coordinator.daemon import Daemon
from coordinator.publish import Publisher
from coordinator.store import uid
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'pub3-repo';repo.mkdir(mode=0o700);state=root/'pub3-state'
gitio.git(repo,['init','-q']);rows=[]
for name in ('a.txt','b.txt','c.txt','d.txt'):
 (repo/name).write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');rows.append('100644 '+oid+'\t'+name+'\n')
gitio.git(repo,['update-index','--index-info'],data=''.join(rows).encode());tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'C3 baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
metadata={'message':'C3 reviewed publication','author_name':'C3','author_email':'c3@invalid','author_date':'2026-10-18T17:00:00Z','committer_name':'C3','committer_email':'c3@invalid','committer_date':'2026-10-18T17:00:00Z'}
d=Daemon(state);p=Publisher(d)
async def edit(checkout,name,session):
 a=await d.dispatch('lead/session',{'checkout':checkout['id'],'session_id':session});await d.lead_edit({'actor':a['actor'],'changes':{name:{'mode':'100644','bytes_base64':base64.b64encode((session+'\n').encode()).decode()}}});seal=await p.seal(a['unit'],[]);job=await reviews.prepare(d,{'unit':a['unit'],'cwd':str(repo)});reviews.dispatched(d,{'job':job['job'],'run':'review-'+session});return a,seal,job
async def main():
 from coordinator.recovery import recover
 await recover(d);d.ready=True
 checkout=await d.ledger.register(str(repo));a,seal,job=await edit(checkout,'a.txt','A')
 completion={'run':'review-A','thread':'fixture-thread-A','status':'ok','completed':True,'answer':'**Verdict**: Ship it\nFull saved review answer.'}
 saved=d.store.get(job['job']);saved.update(state='negative',completion=completion);d.store.save(saved)
 result=reviews.complete(d,{'job':job['job'],'completion':completion});print('OLD_COMPLETION_REPAIRED',result['state'],flush=True)
 saved=d.store.get(job['job']);saved['state']='negative';d.store.save(saved);result=reviews.complete(d,{'job':job['job'],'completion':completion});print('EVIDENCE_REUSED',len(list(d.store.all('review',a['unit']))),result['review'],flush=True)
 for label,answer in [('negative','**Verdict**: Do not ship\nFull negative answer.'),('stale','**Verdict**: Ship it\nStale answer.')]:
  j=await reviews.prepare(d,{'unit':a['unit'],'cwd':str(repo)});reviews.dispatched(d,{'job':j['job'],'run':'review-'+label})
  if label=='stale':s=d.store.get(j['job']);s['binding']['validation_digest']='stale';d.store.save(s)
  r=reviews.complete(d,{'job':j['job'],'completion':dict(completion,run='review-'+label,answer=answer)});print('REVIEW',label,r['state'],r.get('refusal'),flush=True)
 staged=gitio.text(repo,['hash-object','-w','--stdin'],data=b'unrelated staging\n');gitio.git(repo,['update-index','--cacheinfo','100644',staged,'d.txt'])
 foreign=repo/'.git/index.lock';foreign.write_bytes(b'C3 foreign lock')
 try:await p.publish(a['unit'],result['review'],metadata)
 except Exception as exc:print('FOREIGN_LOCK_REFUSAL',getattr(exc,'code',type(exc).__name__),'FOREIGN_PRESERVED',foreign.read_bytes()==b'C3 foreign lock','HEAD_UNCHANGED',gitio.text(repo,['rev-parse','HEAD'])==base,flush=True)
 finally:
  if foreign.read_bytes()==b'C3 foreign lock':foreign.unlink()
 observations=[];done=threading.Event()
 def observe():
  db=sqlite3.connect('file:'+str(state/'ledger.sqlite')+'?mode=ro',uri=True)
  while not done.is_set():
   for row in db.execute("select data from records where kind='publication'"):
    value=json.loads(row[0]);observations.append((value['phase'],(repo/'.git/index.lock').exists()))
   time.sleep(.0005)
  db.close()
 t=threading.Thread(target=observe);t.start()
 try:published=await p.publish(a['unit'],result['review'],metadata)
 finally:done.set();t.join()
 print('NEW_PUBLICATION',published['phase'],'OBSERVED',sorted(set(observations)),'LOCK_GONE',not (repo/'.git/index.lock').exists(),'UNRELATED_STAGING',gitio.entries(repo)['d.txt'][0]['oid']==staged,flush=True)
 b,seal2,job2=await edit(checkout,'b.txt','B');r=reviews.complete(d,{'job':job2['job'],'completion':dict(completion,run='review-B',thread='fixture-thread-B')})
 # Reproduce an old journal: real commit/ref installed, checkpoint still commit_created,
 # and no index ownership record (the pre-C3 gap).
 real=gitio.entries(repo);commit=gitio.text(repo,['commit-tree',seal2['candidate'],'-p',seal2['base']],data=b'Old journal installed commit\n',extra_env=meta)
 pid=uid('publication');d.store.put('publication',pid,{'unit':b['unit'],'seal':seal2['id'],'review':r['review'],'metadata':metadata,'phase':'commit_created','commit':commit,'base_index_entries':{q:real.get(q,[]) for q in seal2['paths']},'config_digest':p.publication_guards(checkout,seal2),'created':time.time()},b['unit']);gitio.reference_update(repo,seal2['branch'],commit,seal2['base'])
 idx=state/'indexes/disjoint.index';gitio.git(repo,['read-tree',commit],index=idx);oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'disjoint lead commit\n');gitio.git(repo,['update-index','--cacheinfo','100644',oid,'c.txt'],index=idx);tree=gitio.text(repo,['write-tree'],index=idx);tip=gitio.text(repo,['commit-tree',tree,'-p',commit],data=b'Disjoint lead advance\n',extra_env=meta);gitio.reference_update(repo,seal2['branch'],tip,commit)
 recovered=await d.dispatch('unit/recover',{'unit':b['unit'],'action':'abandon'});print('OLD_JOURNAL_RECOVERED',recovered['phase'],'ORIGINAL_COMMIT',recovered['commit']==commit,'NEWER_TIP_PRESERVED',gitio.text(repo,['rev-parse','HEAD'])==tip,'UNRELATED_STAGING',gitio.entries(repo)['d.txt'][0]['oid']==staged,'LOCK_GONE',not (repo/'.git/index.lock').exists(),flush=True)
 lead=await d.dispatch('lead/session',{'checkout':checkout['id'],'session_id':'handback'});actor=d.store.get(lead['actor']);unit=d.store.get(lead['unit']);await d.custody.acquire(actor,['a.txt','b.txt','c.txt'])
 print('HANDBACK_EMPTY',await d.custody.release(unit,[]),flush=True);print('HANDBACK_SUBSET',await d.custody.release(unit,['b.txt']),flush=True);print('HANDBACK_OMITTED',await d.custody.release(unit),flush=True)
 print('PROVIDER_REQUESTS',0,flush=True)
try:asyncio.run(main())
finally:
 for fd in d.ledger.ownership.values():os.close(fd)
 d.store.db.close()
PY
```

</details>

#### Available-empty versus unavailable discovery, Python 3.9 clients and final catalogs

Exit **0**. Actual stdio MCP initialization/tools/list: lead tools 15; images is in public resume schema; description 4547 characters. Writer tools exactly architect_review, codex_query, research. All four run tools distinguished an available empty coordinator from a stopped coordinator. Automatic resume/log/cancel refused ambiguity during outage; explicit native resume went directly to RPC and reported non-delivery. A hook whose cwd was registered but target was outside the registry returned exit0 with stdout='' and stderr='' both before and after daemon stop. Python 3.9 stopped query returned available=false, fresh=false, registered=null, generation=null, status=coordinator_down (2.646ms); list/events/result reported FileNotFoundError. Three newly created state ancestors were 0700; a user-created symlink ancestor refused STATE_SYMLINK. Default state root absent. daemon/stop returned stopping=true.

<details><summary>Exact command</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c3-3qhpdpn2/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,subprocess,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator.client import Client,validate_state,CoordinatorError
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');state=root/'final-observe-state';repo=root/'exec-valid-repo'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'final-observe-rt'),CODEX_ORACLE_LOG_DIR=str(root/'final-observe-logs'),CODEX_HOME=str(root/'final-observe-home'),TMPDIR=str(root))
for k in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID','SOFTWARE_WORKFLOWS_FIXTURE','SOFTWARE_WORKFLOWS_CODEX'):env.pop(k,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
c=Client(state);c.ensure(python='/opt/homebrew/bin/python3.15')
for n in range(100):
 if c.call('daemon/status')['ready']:break
 time.sleep(.05)
def hook():
 payload={'hook_event_name':'PreToolUse','tool_name':'Write','session_id':'c3-inverse','cwd':str(repo),'tool_use_id':'outside','tool_input':{'file_path':str(root/'unregistered-final.txt'),'content':'unused'}}
 r=subprocess.run(['/usr/bin/python3','-B','plugins/software-workflows/hooks/coordinator_lead.py'],input=json.dumps(payload),env=env,capture_output=True,text=True);print('INVERSE_TARGET',r.returncode,repr(r.stdout),repr(r.stderr),flush=True)
def text(r):return '\n'.join(v.text for v in r.content if v.type=='text')
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(repo))
 async with stdio_client(params,errlog=open(root/'final-observe-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize();catalog=await s.list_tools();resume=next(t for t in catalog.tools if t.name=='codex_resume_run')
   print('LEAD_SCHEMA',len(catalog.tools),'images' in resume.inputSchema['properties'],len(resume.description),flush=True)
   for name in ('codex_runs','codex_run_log','codex_resume_run','codex_cancel_run'):print('AVAILABLE_EMPTY',name,text(await s.call_tool(name,{}))[:450],flush=True)
   c.call('checkout/register',{'path':str(repo)});hook()
   print('STOP',c.call('daemon/stop'),flush=True);await asyncio.sleep(.2);hook()
   for name in ('codex_runs','codex_run_log','codex_resume_run','codex_cancel_run'):print('STOPPED',name,text(await s.call_tool(name,{}))[:450],flush=True)
   print('EXPLICIT_NATIVE',text(await s.call_tool('codex_resume_run',{'run':'run_'+'0'*32,'images':[str(root/'valid.png')]})),flush=True)
 writer=StdioServerParameters(command=params.command,args=params.args,env=dict(env,SOFTWARE_WORKFLOWS_ROLE='writer'),cwd=str(repo))
 async with stdio_client(writer,errlog=open(root/'final-writer-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize();print('WRITER_SCHEMA',sorted(t.name for t in (await s.list_tools()).tools),flush=True)
try:asyncio.run(main())
finally:
 try:c.call('daemon/stop')
 except Exception:pass
code="from coordinator.query import query_checkout;from coordinator.client import Client;import json,sys;print(json.dumps(query_checkout(sys.argv[2],'c3',state=sys.argv[1]),sort_keys=True));c=Client(sys.argv[1]);\nfor method,params in [('run/list',{'cwd':'/'}),('run/events',{'run':'run_'+'0'*32,'cwd':'/'}),('run/result',{'run':'run_'+'0'*32,'cwd':'/'})]:\n try:c.call(method,params)\n except Exception as e:print(method,getattr(e,'code',type(e).__name__))"
r=subprocess.run(['/usr/bin/python3','-B','-c',code,str(state),str(repo)],env=dict(env,PYTHONPATH='/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows'),capture_output=True,text=True);print('PY39_STOPPED_EXIT',r.returncode,r.stdout,r.stderr,flush=True)
created=root/'private-components/a/b';validate_state(created,create=True);print('PRIVATE_MODES',[oct(p.stat().st_mode&0o777) for p in (created,created.parent,created.parent.parent)],flush=True)
alias=root/'state-alias';alias.symlink_to(created.parent)
try:validate_state(alias/'child',create=True)
except CoordinatorError as e:print('SYMLINK_ANCESTOR',e.code,flush=True)
print('DEFAULT_STATE_EXISTS',Path('~/.local/state/software-workflows').expanduser().exists(),flush=True)
PY
```

</details>

### Failures observed and corrections made during these runs

- The first valid native image run exited **1**: replay of a terminal event
  changed a cancelled run back to settling, so resume returned RUN_ACTIVE.
  Non-workflow first submission also tried to read a rollout that Codex had not
  materialized. The next native run exited **1** after cancellation recovery
  passed but the non-workflow history read still refused. The final run above
  exited **0** after both fixes. No blind retry of an uncertain turn was added.
- The initial exec/tiny-PNG run exited **0**, with four requests and zero image
  inputs because the fixture file could not be decoded. It did not prove image
  delivery. The valid-image exec and native runs above provide that evidence.
- The first direct lease-protocol command exited **1** because compatibility
  code was imported before setting the scratch log namespace. Adoption refused
  the mismatched path; it did not read the default log location. Controllers
  were stopped. Setting the namespace before import produced the passing run.
- The first inverse-lock command exited **1** because its inline caller expected
  a false return instead of catching the product's EXCLUSIVE_WRITER exception.
  The exception was the intended refusal. The corrected caller produced the
  passing inverse run above; no product patch was needed.

### Ordinary native command: conservative settlement remains unresolved

The native-sleep operational invocation exited **1**. The actual native command
`/bin/sleep 0.2` returned exitCode=0, status=completed; the actor completed and
there were zero UNKNOWN file changes. The run nevertheless stayed settling:
new markerless processes appeared after the operation baseline and remained
PROCESS_AMBIGUOUS. These included a separate lead shell's sleep, Docker helpers,
and macOS services. They were never attributed to the writer and were not killed.

Native background terminal inventory was empty. Handback correctly refused
CUSTODY_HELD. The client also encountered an uncertain ConnectionRefusedError;
it did not replay the native operation through exec. This is **not a passing
ordinary-command settlement result**. The conservative fix protects the
immediate-double-fork case but can hold custody for unrelated process activity
on this busy Darwin host. A full safe-and-usable ordinary command lifecycle
remains **UNVERIFIED**, and C3 must not be described as fully shippable.

While that daemon was live, actual Python 3.9 calls succeeded (exit **0**):
query available=true/fresh=true/registered=true/status=coordinated/generation=1,
unit path a.txt and unknown=[] (1.601ms); run/list returned one run for "/" and
both dispatch and target scope; run/events retained cursor/data/more/run/state;
run/result returned settling. Handback returned CUSTODY_HELD.

Normal daemon stop refused NOT_QUIESCENT. Cleanup therefore signaled only the
birth-verified scratch custodian 35466, worker 35469, and listener 35521 after
the terminal inventory was empty. The immediate cleanup command exited **1**
because a disappearing PID was transiently unreadable; it did not treat that
as proof of exit. A later `/bin/ps -p 35466,35469,35521 -o pid=,ppid=,stat=,comm=`
exited **1** with empty output, confirming all three were then absent. Global
final cleanup verification is recorded below.


### Exact commands for crash and unresolved-command observations

<details><summary>Native custodian SIGKILL, competing exec, then worker SIGKILL (enclosing exit 1; observations above)</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c3-3qhpdpn2/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,signal,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes,native
from coordinator.fixture import Provider,message,function
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'crash-repo';repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['update-index','--add','--cacheinfo','100644',oid,'a.txt']);tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'C3 crash baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
home=root/'crash-home';home.mkdir(mode=0o700);fixture=root/'crash-fixture';fixture.mkdir(mode=0o700);state=root/'crash-state';badstate=root/'crash-unusable-state';badstate.mkdir(mode=0o755);badstate.chmod(0o755)
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'crash-rt'),CODEX_ORACLE_LOG_DIR=str(root/'crash-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='240',TMPDIR=str(root))
for k in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(k,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
from coordinator import compat
client=Client(state,timeout=45)
def router(body,index):
 users=[i for i in body.get('input',[]) if i.get('role')=='user'];last=json.dumps(users[-1] if users else {})
 if 'Continue the same task' in last:return message('Continuation received.')
 if 'PHASE 2 of 2' not in last:return message('Brief: run /bin/sleep 25 without modifying files.')
 if any(i.get('name')=='exec_command' for i in body.get('input',[])):return message('Command started; custody must await actual exit.')
 return function('exec_command',{'cmd':'/bin/sleep 25','login':False,'yield_time_ms':1000})
def plain(r):return '\n'.join(c.text for c in r.content if c.type=='text')
def params(e):return StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=e,cwd=str(repo))
async def rpc(method,p=None):return await asyncio.to_thread(client.call,method,p)
async def waitrun(rid,seconds=50):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  r=await rpc('run/status',{'run':rid})
  if r['state'] in ('completed','failed','blocked','cancelled','refused'):return r
  await asyncio.sleep(.3)
 return r
async def main():
 async with stdio_client(params(env),errlog=open(root/'crash-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize();job=asyncio.create_task(s.call_tool('abraham',{'task':'C3 custody crash: run /bin/sleep 25, no edits.','cwd':str(repo),'claims':['a.txt']}))
   end=time.monotonic()+160;selected=None
   while time.monotonic()<end:
    await asyncio.sleep(.3)
    try:runs=(await rpc('run/list',{'cwd':'/'}))['data']
    except Exception:continue
    for r in runs:
     if r['custody'] and any(c.get('processes') or c.get('terminals') for c in r['custody']):selected=r;break
    if selected:break
    if job.done():print('EARLY_JOB',plain(await job),flush=True);return
   if not selected:raise RuntimeError('No live command custody observed')
   rid=selected['run'];status=await rpc('daemon/status');worker=processes.identity(status['pid']);custodian=processes.identity(worker['ppid'])
   print('LIVE_CUSTODY',rid,[(c['id'],[p['pid'] for p in c.get('processes',[])],c.get('terminals')) for c in selected['custody']],flush=True)
   os.kill(custodian['pid'],signal.SIGKILL);print('KILLED_CUSTODIAN',custodian['pid'],custodian['birth'],flush=True)
   ok,holder=compat._acquire_write_lock(str(repo),'C3-immediate-exec');print('IMMEDIATE_EXEC_LOCK',ok,holder,flush=True)
   if ok:compat._release_write_lock(str(repo));raise RuntimeError('Lock escaped on custodian death')
   print('LEGACY_BRIDGE_AFTER_CUSTODIAN',bool(compat._legacy_write_lock_holder(str(repo))),flush=True)
   before=len(provider.requests)
   competing=dict(env,SOFTWARE_WORKFLOWS_STATE=str(badstate),SOFTWARE_WORKFLOWS_CODEX='/bin/false')
   async with stdio_client(params(competing),errlog=open(root/'crash-competing-stderr.log','w')) as streams2:
    async with ClientSession(*streams2) as other:
     await other.initialize();answer=plain(await other.call_tool('abraham',{'task':'Competing exec must refuse before inference','cwd':str(repo),'allow_dirty':True}));print('COMPETING_ABRAHAM',answer,flush=True)
   print('COMPETING_PROVIDER_DELTA',len(provider.requests)-before,flush=True)
   end=time.monotonic()+20
   while time.monotonic()<end:
    await asyncio.sleep(.2)
    lease=(await rpc('records/list',{'kind':'checkout'}))['data'][0].get('compatibility_lease',{})
    bridges=compat._legacy_write_lock_holder(str(repo))
    if 'pid='+str(custodian['pid']) not in bridges and bridges:break
   print('REDUNDANCY_BRIDGE',bridges,flush=True)
   current=await rpc('daemon/status');os.kill(current['pid'],signal.SIGKILL);print('KILLED_WORKER',current['pid'],flush=True)
   ok,holder=compat._acquire_write_lock(str(repo),'C3-worker-crash');print('WORKER_CRASH_EXEC_LOCK',ok,holder,flush=True)
   if ok:compat._release_write_lock(str(repo));raise RuntimeError('Lock escaped on worker death')
   print('LEGACY_BRIDGE_AFTER_WORKER',bool(compat._legacy_write_lock_holder(str(repo))),flush=True)
   end=time.monotonic()+40
   while time.monotonic()<end:
    try:
     recovered=await rpc('daemon/status')
     if recovered['ready'] and recovered['generation']>current['generation']:print('RECOVERED',recovered,flush=True);break
    except Exception:pass
    await asyncio.sleep(.3)
   result=await waitrun(rid);print('SETTLED',result['state'],result.get('settlement_refusal'),flush=True)
   print('JOB',plain(await job)[:3500],flush=True)
   before=len(provider.requests);print('COLLECT',plain(await s.call_tool('codex_resume_run',{'run':rid}))[:200],flush=True);print('COLLECT_DELTA',len(provider.requests)-before,flush=True)
   before=len(provider.requests);print('MISSING',plain(await s.call_tool('codex_resume_run',{'run':rid,'images':[str(root/'absent.png')]})),flush=True);print('MISSING_DELTA',len(provider.requests)-before,flush=True)
   print('IMAGE_ONLY',plain(await s.call_tool('codex_resume_run',{'run':rid,'images':[str(root/'tiny.png')]}))[:1600],flush=True)
   result=await waitrun(rid);print('IMAGE_STATE',result['state'],'INPUT',result.get('phases',{}).get(result.get('phase'),{}).get('input'),flush=True)
   print('IMAGE_NUDGE',plain(await s.call_tool('codex_resume_run',{'run':rid,'nudge':'Describe the supplied image transport.','images':[str(root/'tiny.png')]}))[:500],flush=True);await waitrun(rid)
   before=len(provider.requests);print('LIST_NONDELIVERY',plain(await s.call_tool('codex_resume_run',{'run':'list','images':[str(root/'tiny.png')]}))[-400:],flush=True);print('LIST_DELTA',len(provider.requests)-before,flush=True)
with Provider(router=router) as provider:
 provider.config['mcp_servers.software-workflows']={'command':'/opt/homebrew/bin/python3.15','args':['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],'env':{k:env[k] for k in ('CLAUDE_PLUGIN_DATA','SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')}}
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  print('PROVIDER',len(provider.requests),'IMAGES',[sum(c.get('type')=='input_image' for item in b.get('input',[]) for c in (item.get('content',[]) if isinstance(item.get('content'),list) else [])) for b in provider.requests],provider.errors,flush=True)
  try:
   for r in client.call('run/list',{'cwd':'/'})['data']:
    if r['state'] not in ('completed','failed','cancelled','refused','blocked'):print('CANCEL',client.call('run/interrupt',{'run':r['run']}),flush=True)
   print('STOP',client.call('daemon/stop'),flush=True)
  except Exception as e:print('CLEANUP_ERROR',getattr(e,'code',type(e).__name__),str(e),flush=True)
PY
```

</details>

<details><summary>Ordinary native /bin/sleep 0.2 command with unresolved settlement (enclosing exit 1; observations above)</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 /private/tmp/software-workflows-c3-3qhpdpn2/plugin-data/venv/91e1e736a04fad0e/bin/python -B - <<'PY'
import asyncio,json,os,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import gitio,processes,native
from coordinator.fixture import Provider,message,function
from coordinator.client import Client
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'native-sleep-repo';repo.mkdir(mode=0o700)
gitio.git(repo,['init','-q']);(repo/'a.txt').write_text('baseline\n');oid=gitio.text(repo,['hash-object','-w','--stdin'],data=b'baseline\n');gitio.git(repo,['update-index','--add','--cacheinfo','100644',oid,'a.txt']);tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'};base=gitio.text(repo,['commit-tree',tree],data=b'C3 baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
home=root/'native-sleep-home';home.mkdir(mode=0o700);fixture=root/'native-sleep-fixture';fixture.mkdir(mode=0o700);state=root/'native-sleep-state'
env=dict(os.environ,CLAUDE_PLUGIN_DATA=str(root/'plugin-data'),SOFTWARE_WORKFLOWS_STATE=str(state),SOFTWARE_WORKFLOWS_RUNTIME=str(root/'native-sleep-rt'),CODEX_ORACLE_LOG_DIR=str(root/'native-sleep-logs'),CODEX_HOME=str(home),SOFTWARE_WORKFLOWS_FIXTURE=str(fixture),CODEX_ORACLE_MAX_RUNTIME_S='180',TMPDIR=str(root))
for k in ('CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_TOOL_CALL_ID'):env.pop(k,None)
os.environ.update({k:env[k] for k in ('SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR','TMPDIR')})
def router(body,index):
 users=[i for i in body.get('input',[]) if i.get('role')=='user'];last=json.dumps(users[-1] if users else {})
 if 'PHASE 2 of 2' not in last:return message('Brief: run /bin/sleep 0.2 with no edits.')
 if any(i.get('name')=='exec_command' for i in body.get('input',[])):return message('Ordinary system command completed.')
 return function('exec_command',{'cmd':'/bin/sleep 0.2','yield_time_ms':1000,'login':False})
async def main():
 params=StdioServerParameters(command='/opt/homebrew/bin/python3.15',args=['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],env=env,cwd=str(root))
 async with stdio_client(params,errlog=open(root/'native-sleep-stderr.log','w')) as streams:
  async with ClientSession(*streams) as s:
   await s.initialize()
   empty=await s.call_tool('codex_runs',{});print('EMPTY_CATALOG','\\n'.join(c.text for c in empty.content if c.type=='text'),flush=True)
   response=await s.call_tool('abraham',{'task':'C3 run /bin/sleep 0.2, with no file changes.','cwd':str(repo),'claims':['a.txt']});print('ABRAHAM','\n'.join(c.text for c in response.content if c.type=='text'),flush=True)
   runs=await asyncio.to_thread(Client(state).call,'run/list',{'cwd':'/'});print('RUNS',json.dumps(runs),flush=True)
   print('ACTUAL_FILE',(repo/'a.txt').read_text(),flush=True)
   rid=runs['data'][0]['run']
   import subprocess
   code="from coordinator.client import Client;from coordinator.query import query_checkout;import json,sys;c=Client(sys.argv[1]);print('QUERY',json.dumps(query_checkout(sys.argv[2],'c3',state=sys.argv[1]),sort_keys=True));print('LIST_ROOT',len(c.call('run/list',{'cwd':'/'})['data']));print('LIST_DISPATCH',len(c.call('run/list',{'cwd':sys.argv[3]})['data']));print('EVENTS',sorted(c.call('run/events',{'run':sys.argv[4],'cwd':'/'})));print('RESULT',c.call('run/result',{'run':sys.argv[4],'cwd':'/'})['state'])"
   r=subprocess.run(['/usr/bin/python3','-B','-c',code,str(state),str(repo),str(root),rid],env=dict(env,PYTHONPATH='/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows'),capture_output=True,text=True);print('PY39_EXIT',r.returncode,r.stdout,r.stderr,flush=True)
with Provider(router=router) as provider:
 provider.config['mcp_servers.software-workflows']={'command':'/opt/homebrew/bin/python3.15','args':['-B','/Users/ahrar/Documents/agent-teams-plugin/plugins/software-workflows/run_server.py'],'env':{k:env[k] for k in ('CLAUDE_PLUGIN_DATA','SOFTWARE_WORKFLOWS_STATE','SOFTWARE_WORKFLOWS_RUNTIME','CODEX_ORACLE_LOG_DIR')}}
 (fixture/'fixture.json').write_text(json.dumps({'pid':os.getpid(),'birth':processes.identity(os.getpid())['birth'],'config':provider.config}));os.chmod(fixture/'fixture.json',0o600)
 (home/'config.toml').write_text('\n'.join(k+' = '+native.toml(v) for k,v in provider.config.items())+'\n')
 try:asyncio.run(main())
 finally:
  print('PROVIDER',len(provider.requests),provider.errors,flush=True)
  try:print('STOP',Client(state,timeout=40).call('daemon/stop'),flush=True)
  except Exception as e:print('STOP_ERROR',getattr(e,'code',type(e).__name__),flush=True)
PY
```

</details>

### Final Git helper contract verification

The final inspection replaced PATH-resolved `env` with the absolute system
`/usr/bin/env` in the supported invocation. Both declaration and reservation
refuse an unqualified or unattested env wrapper, bare diff, and textconv override.
The initial registration attempt exited **1** because the earlier helper scratch
repository had no HEAD. Creating its scratch baseline with Git plumbing allowed
the corrected operational run below; no product change was needed for that setup.

Corrected run exit **0**, captured output:

```text
UNRESTRICTED ['diff'] EXIT 0 EXTERNAL_MARKER True TEXTCONV_MARKER False
UNRESTRICTED ['diff', '--no-ext-diff'] EXIT 0 EXTERNAL_MARKER False TEXTCONV_MARKER True
REFUSAL declare LEAD_RECORDS
REFUSAL reserve LEAD_RECORDS
[the same pair occurred for all four unsafe invocations]
SAFE_GRANTED shared True BASELINE 407
SAFE_EXECUTION CLASSIFIER_REFUSED False EXIT 0 EXTERNAL_MARKER False TEXTCONV_MARKER False
SAFE_EXECUTION CLASSIFIER_REFUSED False EXIT 0 EXTERNAL_MARKER False TEXTCONV_MARKER False
PROVIDER_REQUESTS 0
```

The accepted exact string was recorded in custody and executed through `/bin/sh`.
The second safe execution also used `git -C`. This direct in-process daemon run
closed its ledger ownership descriptors and SQLite connection; it did not start
a detached daemon or claim that its scratch custody journal had been settled.

<details><summary>Exact helper operational command (exit 0)</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 CODEX_ORACLE_LOG_DIR=/private/tmp/software-workflows-c3-3qhpdpn2/helper-final2-logs /opt/homebrew/bin/python3.15 -B - <<'PY'
import asyncio,os,subprocess
from pathlib import Path
from coordinator import gitio
from coordinator.client import CoordinatorError
from coordinator.daemon import Daemon
from coordinator.recovery import recover
from coordinator.custody import recording_command
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');repo=root/'helper-repo'
if gitio.git(repo,['rev-parse','--verify','HEAD'],check=False).returncode:
 tree=gitio.text(repo,['write-tree']);meta={'GIT_AUTHOR_NAME':'C3','GIT_AUTHOR_EMAIL':'c3@invalid','GIT_COMMITTER_NAME':'C3','GIT_COMMITTER_EMAIL':'c3@invalid'}
 base=gitio.text(repo,['commit-tree',tree],data=b'C3 helper baseline\n',extra_env=meta);gitio.git(repo,['update-ref','HEAD',base])
d=Daemon(root/'helper-final2-state')
async def main():
 await recover(d);d.ready=True
 checkout=await d.ledger.register(str(repo));lead=await d.dispatch('lead/session',{'checkout':checkout['id'],'session_id':'C3-helper-final2'});actor=d.store.get(lead['actor'])
 for args in (['diff'],['diff','--no-ext-diff']):
  r=subprocess.run(['git',*args],cwd=repo,capture_output=True,text=True)
  print('UNRESTRICTED',args,'EXIT',r.returncode,'EXTERNAL_MARKER','C3_EXTERNAL_HELPER' in r.stdout,'TEXTCONV_MARKER','C3_TEXTCONV_HELPER' in r.stdout,flush=True)
 safe=gitio.safe_shell_git(['diff','--no-ext-diff','--no-textconv'])
 for command in ('git diff',safe.replace(gitio.shell_env_binary(),'env',1),safe.replace(gitio.shell_env_binary(),'/private/tmp/unattested/env',1),safe.replace('--no-textconv','--textconv')):
  for stage in ('declare','reserve'):
   try:
    if stage=='declare':d.custody.declare(actor,command,'C3 helper safety')
    else:await d.custody.reserve(actor,command)
   except CoordinatorError as e:print('REFUSAL',stage,e.code,flush=True)
   else:raise RuntimeError('Unsafe command admitted at '+stage)
 declaration=d.custody.declare(actor,safe,'C3 helper-free actual execution');reservation=await d.custody.reserve(actor,safe)
 print('SAFE_GRANTED',reservation['level'],reservation['command']==safe,'BASELINE',len(reservation['process_baseline']),flush=True)
 for command in (safe,gitio.safe_shell_git(['-C',str(repo),'diff','--no-ext-diff','--no-textconv'])):
  r=subprocess.run(['/bin/sh','-c',command],cwd=repo,capture_output=True,text=True)
  print('SAFE_EXECUTION','CLASSIFIER_REFUSED',recording_command(command),'EXIT',r.returncode,'EXTERNAL_MARKER','C3_EXTERNAL_HELPER' in r.stdout,'TEXTCONV_MARKER','C3_TEXTCONV_HELPER' in r.stdout,flush=True)
  if r.returncode or 'C3_EXTERNAL_HELPER' in r.stdout or 'C3_TEXTCONV_HELPER' in r.stdout:raise RuntimeError('Unsafe execution')
 print('PROVIDER_REQUESTS',0,flush=True)
try:asyncio.run(main())
finally:
 for fd in d.ledger.ownership.values():os.close(fd)
 d.store.db.close()
PY
```

</details>

### Final existing project checks

Both strict validators used fresh disposable Claude configuration directories.

```sh
CLAUDE_CONFIG_DIR=/private/tmp/software-workflows-c3-3qhpdpn2/claude-final-plugin TMPDIR=/private/tmp/software-workflows-c3-3qhpdpn2 claude plugin validate --strict plugins/software-workflows
CLAUDE_CONFIG_DIR=/private/tmp/software-workflows-c3-3qhpdpn2/claude-final-marketplace TMPDIR=/private/tmp/software-workflows-c3-3qhpdpn2 claude plugin validate --strict .
```

Each exited **0**, output `✔ Validation passed` after naming its respective
plugin/marketplace manifest. No separate build or linter target exists in this
project. These manifest checks do not prove coordinator behavior.

<details><summary>Python 3.9 hook/client parsing and actual imports: exit 0</summary>

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B - <<'PY'
import ast,sys
from pathlib import Path
root=Path('plugins/software-workflows')
paths=list((root/'hooks').glob('*.py'))+[root/'coordinator'/f for f in ('__init__.py','client.py','query.py','runtime.py','registry.py')]
for p in paths:ast.parse(p.read_text(),filename=str(p))
sys.path.insert(0,str(root.resolve()))
from coordinator import client,query,runtime,registry
print('Python',sys.version.split()[0],'parsed',len(paths),'hook/client files; actual imports OK')
print('DEFAULT_STATE_EXISTS',Path('~/.local/state/software-workflows').expanduser().exists())
PY
```

Output:

```text
Python 3.9.6 parsed 11 hook/client files; actual imports OK
DEFAULT_STATE_EXISTS False

```

</details>

<details><summary>Python 3.15 server/coordinator parsing: exit 0</summary>

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.15 -B - <<'PY'
import ast,sys
from pathlib import Path
root=Path('plugins/software-workflows')
paths=[root/'server.py',root/'run_server.py']+list((root/'coordinator').glob('*.py'))
for p in paths:ast.parse(p.read_text(),filename=str(p))
print('Python',sys.version.split()[0],'parsed',len(paths),'server/coordinator files')
PY
```

Output:

```text
Python 3.15.0 parsed 32 server/coordinator files

```

</details>

<details><summary>Whitespace check: exit 0</summary>

```sh
git diff --check
```

Output:

```text
(empty stdout/stderr)
```

</details>

### Final cleanup and residue

The final read-only audit exited **0**. It checked every recorded native
listener/helper/current-custody birth identity, every exec child/server/watchdog
start identity in the scratch run journals, and a redacted process-command scan.
No unrelated ambiguous process was signalled. Result:

```json
{"default_state_exists":false,"errors":[],"exec_live":[],"ledgers":18,"native_live":[],"ps_exit":0,"recorded_exec_identities":33,"recorded_native_identities":42,"scratch_command_matches":[]}
```

All fixture-provider context managers closed their listeners. All started
daemons/controllers/native listeners and surviving scratch command processes
were stopped; the two non-quiescent crash/command scenarios required the
birth-verified cleanup described above. Scratch evidence and unresolved scratch
journals remain under `/private/tmp/software-workflows-c3-3qhpdpn2` for review.
The default `~/.local/state/software-workflows` directory remains absent; nothing
was left there. Warning deduplication used an owned private temporary directory.
No user's CODEX_HOME was used; real-model runs used: **0 of the allowed 1**.

<details><summary>Exact final cleanup audit</summary>

```sh
PYTHONPATH=plugins/software-workflows PYTHONDONTWRITEBYTECODE=1 CODEX_ORACLE_LOG_DIR=/private/tmp/software-workflows-c3-3qhpdpn2/final-audit-logs /opt/homebrew/bin/python3.15 -B - <<'PY'
import json,os,sqlite3,subprocess
from pathlib import Path
from coordinator import processes,compat
root=Path('/private/tmp/software-workflows-c3-3qhpdpn2');seen={};errors=[]
ledgers=list(root.rglob('ledger.sqlite'))
for path in ledgers:
 db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
 for kind,data in db.execute("SELECT kind,data FROM records WHERE kind IN ('listener','custody')"):
  r=json.loads(data);records=[r]+r.get('services',[])+r.get('helpers',[]) if kind=='listener' else r.get('processes',[])
  for p in records:
   if p.get('pid') and p.get('birth'):seen[(p['pid'],p['birth'])]=p
 db.close()
live=[]
for (pid,birth),record in seen.items():
 try:now=processes.identity(pid)
 except Exception as e:errors.append({'pid':pid,'code':getattr(e,'code',type(e).__name__)});continue
 if now and not now['zombie'] and now['birth']==birth:live.append({'pid':pid,'birth':birth})
exec_ids=set()
for path in root.glob('*logs/runs.jsonl'):
 for line in path.read_text().splitlines():
  e=json.loads(line)
  if e.get('phase')=='spawn':
   for pk,sk in [('pid','pid_start'),('server_pid','server_start'),('watchdog_pid','watchdog_start')]:
    if e.get(pk) and e.get(sk):exec_ids.add((int(e[pk]),e[sk]))
exec_live=[pid for pid,start in exec_ids if compat._pid_alive(pid,start,strict=True)]
r=subprocess.run(['/bin/ps','-ax','-o','pid=,ppid=,stat=,command='],capture_output=True,text=True,check=True);rows=[]
for line in r.stdout.splitlines():
 p=line.strip().split(None,3)
 if len(p)==4:rows.append((int(p[0]),int(p[1]),p[2],p[3]))
parents={p:pp for p,pp,_,_ in rows};own=set();p=os.getpid()
while p and p not in own:own.add(p);p=parents.get(p,0)
matches=[{'pid':p,'ppid':pp,'stat':st,'exe':cmd.split()[0]} for p,pp,st,cmd in rows if p not in own and str(root) in cmd]
print(json.dumps({'ledgers':len(ledgers),'recorded_native_identities':len(seen),'native_live':live,'errors':errors,'recorded_exec_identities':len(exec_ids),'exec_live':exec_live,'scratch_command_matches':matches,'ps_exit':r.returncode,'default_state_exists':Path('~/.local/state/software-workflows').expanduser().exists()},sort_keys=True))
if live or errors or exec_live or matches:raise SystemExit(1)
PY
```

</details>

### C3 file-by-file implementation map

These are C3 changes layered onto the existing uncommitted C1/C2 implementation.
Concurrent lead-owned changes are not included.

| File | C3 change and purpose |
| --- | --- |
| ../hooks/coordinator_lead.py | Read-only registry routing before coordinator import/RPC; target-path membership; silent uninvolved hooks; independent full MCP input/session injection; fail-open infrastructure handling; private warning deduplication. |
| client.py | Private component-by-component directory creation, ancestor ownership/symlink validation, structured malformed-RPC failures; stays Python 3.9-compatible. |
| runtime.py | Uses validated private runtime paths without creating permissive ancestors. |
| registry.py (new) | Atomically publishes persistent registered checkout roots and filesystem identities as routing hints. |
| ledger.py | Publishes registry after registration; retains authoritative checkout ownership/identity checks. |
| observations.py | Chooses the most specific registered target root while keeping query fields stable. |
| intents.py | Routes file mutations by their actual target and records intended/observed lead edits; Bash retains context-only observation. |
| query.py | Keeps the Python 3.9 query interface and unavailable shape stable even for malformed responses. |
| ../server.py | Restores 2.2.0 images/defaults/recovery/non-delivery behavior; full public resume docstring; keyword forwarding; exec writer catalog preflight (also custody-holding analysis resumes); omitted handback semantics; explicit native discovery availability at every caller. |
| runs.py | Native workflow/non-workflow image continuation, reconciliation before submission, interrupted phase image recovery, empty-call collection and non-delivery/refusal behavior. |
| writer.py | Persists complete native input and identity/digest before submission, rechecks image files immediately before RPC, preserves verified cancellation on event replay. |
| leases.py | Framed acknowledged descriptor transfer, worker/custodian redundancy, reconnect/replacement without unlocking, retained exclusion on lost acknowledgments. |
| compat.py | Adoption/sharing of the same acquired lock; durable legacy bridge ownership for both controllers; close-only descriptor cleanup; safe private lock-directory creation. |
| daemon.py | Registry and saved-review recovery, additive images dispatch, lease restoration/admission integration, durable process uncertainty and exact controller/helper exemptions. |
| roles.py | Shared role overlay, correctly serialized/coalesced TOML configuration, explicit stdio role, independent actual-binary exec catalog preflight before inference. |
| custody.py | Enforces helper-free Git invocation at declaration and reservation; records pre-command process baseline; accurate omitted/subset/empty handback with actual released claim IDs/paths/count. |
| gitio.py | Supported isolated absolute-env Git invocation and prepared ref-verification helper for publication reconciliation. |
| publish.py | Acquires/journals index.lock before ref mutation, rechecks staged base under that lock, retains ownership through installation/checkpoint, recovers installed commits below disjoint newer tips without overwriting unrelated staging. |
| processes.py | Complete pre-command birth baseline, persistent markerless ambiguity, verified disappearance, exact service exemptions, no opaque processId-to-PID conversion. |
| recovery.py | Preserves ambiguity for lead commands, restores listener lease ownership, safely handles attached or unmaterialized native thread history. |
| reviews.py | Atomic completion history/verdict/evidence/job/event transaction; repairs old saved passing completions without inference; deduplicates evidence and rejects stale bindings. |
| VERIFY.md | Appends real C3 commands, exit codes, observations, failures, cleanup and explicit limitations. |

No C3 edits were made to run_server.py or hooks/hooks.json. The lead's existing
hook registrations and concurrently edited protected files were preserved.

### Deviations and explicitly UNVERIFIED acceptance

Implementation followed sections 1, 2, then 3, with custodian exclusion first
within section 3. Section 4's public query/list/events/result interfaces and
Python 3.9 readability remain unchanged; run/resume images is additive.

Measured code/runtime findings required these adjustments to the brief:
- Installed Codex rejected the initially attempted quoted dotted CLI overrides;
  nested TOML tables and coalescing preserved both original server configuration
  and role restrictions.
- Native terminal replay and first-turn history materialization required changes
  in writer.py and recovery.py so cancellation and non-workflow resume behaved
  correctly. The final image rerun verified those paths.
- The accepted safe Git invocation now uses absolute /usr/bin/env; declaration
  and reservation both verify that exact isolation contract.
- Publication was additionally rechecked under the acquired index lock to close
  the preflight-to-lock staging race.

Remaining acceptance limits, not hidden as passing checks:

| Item | Status / reason |
| --- | --- |
| Ordinary native command and post-crash full settlement | **UNVERIFIED / observed blocked.** Actual /bin/sleep completed, but unrelated new markerless processes kept custody quarantined on this Darwin host. Do not ship as fully verified. |
| Every publication crash boundary, including timed SIGKILL exactly after ref installation | **UNVERIFIED.** Real old-format installed-ref journals and disjoint advancement recovered, and the new lock was observed across ref installation; no timed kill at every boundary was run. |
| Review crash at every individual SQLite write | **UNVERIFIED.** Actual persisted old partial completions repaired exactly once without inference; the new transaction was exercised, not killed at every internal write. |
| Truncated ancillary descriptors / every partial lock acquisition or release interleaving | **UNVERIFIED.** Broken channel, lost acquire/release acknowledgment, custodian kill, worker kill and both lock acquisition orders ran; not every malformed-frame/interleaving case was injected. |
| Exec automatic transient-provider retry | **UNVERIFIED as a separate scenario.** Initial phases and resumed write/custody analysis runs verified the restricted catalog; no automatic retry was forced. |
| Complete C2 regression matrix in one post-C3 run | **UNVERIFIED.** Concurrent units, attribution, messages, overlap, lead edits, seal/review/publication, negative/stale review, worker recovery and MCP restart/detached collection were exercised across runs. A complete fresh UNKNOWN refusal/native MCP restart recovery sequence was not rerun. |
| Real-model visual understanding | **UNVERIFIED.** Valid fixture image transport passed on both paths; no real-model inference was used. |
| Platforms other than this macOS host | **UNVERIFIED.** Runtime evidence is Python 3.9.6/3.15.0, Codex 0.163.0, Git 2.56.0 and SQLite 3.54.0 on this host. |

All edits remain uncommitted working-tree changes. No branch, index, ref, commit,
push, worktree, stash, or checkout operation was performed on this checkout.
Scratch Git plumbing was limited to the explicitly authorized disposable
verification repositories.


## Lead record for 2.3.0 — 2026-10-19

The lead's own measurements after C3 and after the blind Codex review of the coordinator. Every case
below ran on this macOS host (Darwin 25.6, Python 3.15.0 and 3.9.6, Codex 0.163.0) with the fixture
provider, so no model was paid for, on short scratch roots under `/private/tmp` (a socket path must fit
macOS's 104 bytes). The scripts are lead scratch, not part of the repository.

### C3 outcomes this record supersedes

- **"Ordinary native command: conservative settlement remains unresolved"** and its row in the
  UNVERIFIED table. Resolved by the 2.3.0 ownership policy in `processes.py`: a process is the
  writer's only with Codex's markers in its environment (`CODEX_SESSION_ID`, `CODEX_THREAD_ID`,
  `CODEX_TOOL_CALL_ID`, set by Codex 0.163's `exec_command`) or descent from the command. Measured:
  - Each case is one `abraham` call, timed from dispatch to its answer, while unrelated markerless
    processes start every 0.3 s. Final tree:
    - `/bin/sleep 0.2`: completed, custody 0, in 26.9 s. It runs first, so this includes the
      daemon's start and native admission.
    - A marker-carrying survivor that left the command's session with `setsid` and sleeps 8 s: held
      until it exited, completed in 10.1 s.
    - The same survivor with a cleared environment: completed in 2.1 s, not held.
  - A `setsid` survivor with a cleared environment, or an Apple platform binary (macOS returns an
    empty environment for it, without an error), is not held. What it writes later is UNKNOWN. This
    is a documented limit (README, "Known limits").
  - Post-crash full settlement was not rerun, and stays UNVERIFIED.
- **"Bash retains context-only observation"** (`intents.py` in the C3 file map). A lead Bash command
  run in a quiet window is now attributed `lead-command`; see below.

### Measured fixes

| Fix | Measurement |
| --- | --- |
| Git classification of writer commands (`custody.invocation`) | Classification matrix: 0 mismatches. The pre-fix copy, as calibration: 5 false refusals (`PYTHONPATH=. python -m …`, `env FOO=1 make`, plain `env`, …). Startup-code assignments (`BASH_ENV`, `PYTHONSTARTUP`, `LD_PRELOAD`, `NODE_OPTIONS`, …) still refuse. |
| Writer MCP catalog (`roles.verify`) | Real configuration: verified in 6.0 s, with two unrelated servers down, listed as unavailable. The pre-fix code refused every write. |
| Lead Bash attribution (`intents.window`, `ledger.reconcile(attribute=…)`) | Beside a native writer: alone, the change is `lead-command`; with the writer's turn running, UNKNOWN; an outside edit followed by `pwd`, UNKNOWN (the Pre reconcile takes the baseline). The pre-fix code, as calibration: UNKNOWN in every case. |
| NotebookEdit on a claimed path | Refused as CLAIM_CONFLICT before the adapter's own refusal. |
| `production_started` before a non-workflow first turn | A lost reply no longer releases claims (code path read; no lost reply injected). |
| Push gate commit scope (`_commit_scope`, `_pathspec_paths`, reconcile before deciding) | 26 of 26 gate cases, under Python 3.9.6 and 3.15; the 2.2.0 gate as red calibration; the git shim path. |
| `codex_runs` without coordinator state | Empty list, no daemon started. |
| Startup errors (`__main__`) | A 105-byte socket path: the client gets SOCKET_CAPACITY in 0.1 s, instead of DAEMON_START after 30 s. |
| Office Send now | Steer to a running writer: `entered_context` after 36 s, shown as entered. |
| Pause after an unentered steer (`runs._settle_cancel`, `turn_ended`) | Cancel done, run cancelled, the note `turn_ended`. Before: the run stayed settling. |
| Lead hooks cost | Outside a registered checkout, every hook event (Edit, Write, Bash × Pre, Post, Failure) gives empty output and creates no state: about 18–20 ms each, under Python 3.15 and 3.9.6. |

### Two writers in one checkout, driven through the Codex Office

Two native `abraham` units in one scratch checkout, B on b.txt and C on c.txt, dispatched through the
plugin's own MCP server, with the office on its own port. Driven through the page itself (Chrome
DevTools protocol):

1. Send now to B and to C, mid-turn.
2. Pause C while B runs. The office's actions for C read: steer `held` / `turn_ended` ("the turn ended
   (or was paused) before taking it"), cancel `done`, run `cancelled`.
3. B completed. Its lease was released: only the never-unlinked tree lock file was left, and no
   legacy bridge.
4. Continue B from the office. Its answer before: no `CONTINUED_ANSWER`. After: the continuation's
   answer, on the same thread, read on the reopened page. This also covers the answer-cache fix.
5. Cleanup: run/interrupt for anything live, then daemon/stop. No coordinator or app-server was left.

B's scripted patch did not land in this run. The steer reached B while the fixture was still
delaying the response that carried the patch. The rollout records the note, then the script's next
step, and no tool call: the patch went to a request whose response the turn did not use. Without a
steer, the same plan shape applied its patch (`LEASE_B` below).

### The EXCLUSIVE_WRITER refusal on a continuation: not reproduced

An earlier office run refused B's continuation with EXCLUSIVE_WRITER, "legacy (pre-1.17.2) lock
live", naming the coordinator's own worker. Evidence kept from that run:

- The run's logs start at 00:59:45 (`20261019-005945-p20359-*-abraham-native.log`), and its start
  failed to remove the state folder ("Directory not empty").
- The bridge `546ac81e7ac9364e.lock` and the tree lock `tree-16777230-749311210.lock` both read
  `coordinator pid=10632 pstart=Mon_Oct_19_00:50:58_2026 child=10628 cstart=Mon_Oct_19_00:50:58_2026
  lease=d7a5a83c2fd1491c9c19ae2579fd9146 v=1.17.2-bridge`, rewritten at 01:06:44.

So the refusing controllers were a generation started at 00:50:58, nine minutes before that run, on
the code of that time. Why that generation refused its own lease is **not established**: its
earlier history was not kept. On the current code, the refusal was not reproduced on a fresh state
root:

- Direct repro, two units on b.txt and c.txt, then run/resume of B: with both completed, `running`;
  with C interrupted while B ran, `running`. No daemon was left after daemon/stop either time, and
  after the second b.txt read `LEASE_B`.
- The office run above.

daemon/stop refuses NOT_QUIESCENT while a run is live (measured during cleanup); interrupting the run
first lets it stop.

### Fixes after the follow-up review (session 01a14eac, turn 5)

| Fix | Measurement (final tree) |
| --- | --- |
| Overlapping lead Bash (`intents.window`: another lead Bash in the checkout still running, until its host deadline, makes the window not quiet) | Two lead sessions: A (`sleep 1`) and B (writes f.txt) overlap, A ends first. Both completions UNKNOWN-context-only, f.txt UNKNOWN. Then A alone writes g.txt: `lead-command`, attributed. No red run on the turn-4 code was kept. |
| Duplicate completion of one Bash call | A PostToolUseFailure after that call's PostToolUse, with g.txt changed between them: attributes nothing (`duplicate: true`), g.txt UNKNOWN. |
| Gate re-query after reconcile | The coordinator stopped between the gate's reconcile and its second query (injected in the gate's own worker): B's b.txt still refused, with the "could not settle" note. |
| `git commit --only/-o --amend` | With B's b.txt staged: token, no coordinator refusal. `git commit --amend` (takes the index): refused, naming B's unit. |
| Staged content differing from the working file | a.txt staged, then its working file written outside the tools: refused as UNKNOWN content, with the staged-content note. |
| Startup diagnostic only in a valid state folder | `daemon serve --state <dir inside a checkout>`: STATE_IN_CHECKOUT, exit 2, no file written. A 90-character runtime folder: SOCKET_CAPACITY in 0.1 s, `startup-error.json` written. |
| Office Send now on an outage or deadline (`message_outcome`) | Read in the code; not injected. |
| Office record refresh on revision (`run/list` carries `revision`) | The office scenario, rerun on this tree: Send now to B and C, pause C (steer `turn_ended`, cancel done, run cancelled), B completed with its lease released, Continue B answered `CONTINUED_ANSWER` on the reopened page; nothing left running. lead_bash and lead_bash2 reran with the results recorded above. A run continued between two office polls was not staged. |

### Fixes after turn 7 of the same review

| Fix | Measurement (final tree) |
| --- | --- |
| Lead Bash transitions serialized per checkout (`intents.BASH_TRANSITIONS`); the window's one await comes before its store reads | The overlap and duplicate cases above, rerun: same results. The interleaving of one session's Post with another's Pre was not staged; it is closed by the lock, read in the code. |
| Host Bash ceiling = the larger of BASH_MAX_TIMEOUT_MS and BASH_DEFAULT_TIMEOUT_MS | Read in the code. |
| `--no-only`, `--no-all`, `--no-include`; `--fixup`, `--squash`, `--reuse-message`, `--reedit-message` take a value | Gate, B's b.txt staged, session A: `--only --no-only` refused; `--fixup HEAD` refused (before, `HEAD` was read as a pathspec and nothing was checked); `--fixup=reword:HEAD` token. |
| Staged-content note only for paths the commit takes from the index | The split a.txt: `git commit -m x` gives the note; `git commit -m x -- a.txt` does not. Both refused as UNKNOWN content, since the working file was written outside the tools. |

The first 23 gate cases gave the same decisions as before this round.

### 2.3.1: the hook-timeout race (review turn 8)

`lead_bash5.py <plugin> <work dir>` with `BULK=20000`: a registered checkout of 20,000 files plus the
named ones (one full reconcile 8.2–8.5 s). Session B is attached first with one quiet `true`. Then A's
Bash Pre; A's Post starts; 0.3 s later B's Pre, whose hook gives up after 3.0 s (the daemon still
processes it); B writes `bulk/019999.txt`; B's Post later. Each run exited 0 and left no daemon.

| Code | `bulk/019999.txt` after the race |
| --- | --- |
| 2.3.0 (tag export), calibration | `lead-command`, naming A's actor (the one with a single lead hook): **red** |
| 2.3.1 | UNKNOWN |

Two earlier attempts did not reproduce the red on 2.3.0 and are not evidence either way: with a new
session B, attaching it changed A's fingerprint; with 16,000 files, A's scan finished before B wrote.

### Commands and exit codes for the lead record

The scripts are lead scratch in the session's scratchpad (`…/scratchpad/m3/`), not part of the
repository. Every run below exited 0.

| Script | Command |
| --- | --- |
| classify.py | `python3.15 -B classify.py <plugin>`, then `… classify.py <pre-fix copy>` as calibration |
| settle_check.py | `<plugin venv python> -B settle_check.py <plugin> /private/tmp/swfc` |
| lead_bash.py, lead_bash3.py | `python3.15 -B lead_bash.py <plugin> /private/tmp/swfl` (lead_bash3: overlap and duplicate) |
| lead_bash2.py | `<plugin venv python> -B lead_bash2.py <plugin> /private/tmp/swfl2` |
| gate_coord2.py, gate_coord3.py | `python3.15 -B gate_coord3.py <plugin> <2.2.0 push_gate.py> /private/tmp/sw-gate-c3` (gate_coord3 adds the turn-5 cases) |
| gate_shim.py | `python3.15 -B gate_shim.py <plugin> /private/tmp/sw-gate-coord` |
| hooks_cost.py | `python3.15 -B hooks_cost.py <plugin> /private/tmp/swfh`, and under `/usr/bin/python3` |
| lease_repro.py | `<plugin venv python> -B lease_repro.py <plugin> /private/tmp/swlr both`, then `… cancel` |
| office_native2.py, drive3.sh | `drive3.sh`: the scenario on `/private/tmp/swon3`, port 8768, driven by `node cdp.mjs <steps>.json` |

