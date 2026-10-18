# U0: full-access feasibility and the multi-repository target model

U0 adds measurement tools and this specification. It does not implement the 1.18
orchestrator or change the behavior of `server.py`, hooks, `treedigest.py`,
`procenv.py`, `run_server`, or existing tests. "1.18" names the release as it was
planned when U0 was written; it ships as software-workflows 2.0.0, with
codex-oracle merged into it.

The 2026-10-18 ruling is an invariant of the **future 1.18 model**: every abraham
phase has full access through `--dangerously-bypass-approvals-and-sandbox`, with
the user's Codex configuration, MCP servers, and infrastructure access. There is
no selectable sealed mode. Writers use the original checkouts. Targets and
coordinator claims are coordination records, never sandbox guarantees. Existing
1.17 sealed behavior remains untouched by U0.

## Result and evidence standard

The adoption question is whether hooks and file events can provide complete edit
attribution among concurrent full-access writers. The measured answer is **red**
for mandatory interception. The useful design is a **cooperative hybrid**:
coordinator-applied patches with recorded preconditions, persistent per-repository
mutator windows for shell/MCP work, and reconciliation for everything without a
reliable receipt. A red capability verdict is a conclusive result, exit `0`; it
does not mean the harness failed. An unexecuted case, wrong tool, unavailable
authentication, malformed stream, cleanup failure, or failed calibration yields
`inconclusive`, exit `2`.

Installed target: `codex-cli 0.163.0`; native SHA-256
`8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31`.
Each run records its actual version, hash, date, argv, prompts, streams, receipts,
and cleanup status, rather than assuming this description is still current.
Implementation measurements were made on 2026-10-18, Asia/Kolkata.

The supplied analysis used source tag `rust-v0.163.0`, tag object
`129b8ec7ee6f4511557a4856279f529025c25af4`, resolved commit
`83086188ae0614bd882818ff1aa23f3194962012`. No web lookup or external source
checkout was used in this implementation phase. Source predictions from that
brief are hypotheses, not measurements. Relevant pinned source locations are
`core/src/tools/registry.rs`, `core/src/tools/handlers/apply_patch.rs`,
`hooks/src/events/pre_tool_use.rs`, `hooks/src/engine/discovery.rs`,
`config/src/fingerprint.rs`, `core/src/agents_md.rs`, and
`exec/src/event_processor_with_jsonl_output.rs` in `codex-rs`.

There are three distinct evidence classes:

| Label | What it establishes | What it does not establish |
|---|---|---|
| `authenticated_model` | Installed Codex, normal user configuration/authentication/MCP setup, real low-effort model behavior, observed fixture edits | A guarantee that future models choose the same tool or interpret a denial identically |
| `local_fixture_transport` | Installed Codex dispatch, exact tool input/output, hooks, trust, native events, and synthetic initial context, with deterministic localhost Responses SSE | Real-model understanding; effects of private configuration that the synthetic home does not load |
| Local subprocess / app-server metadata | Native patch-engine application, cooperative lock/CAS behavior, or actual `hooks/list` definitions and trust hashes | A model-backed invocation, filesystem isolation, or enforcement against arbitrary writers |

Authenticated runs never proxy traffic through the local transport. Synthetic
homes contain no copied or linked authentication material. HTTP headers,
environment dumps, credential files, and real hook/configuration contents are
not evidence inputs. All hook canaries are generated fixtures. The local
transport captures Codex's request body, including its built-in instructions and
synthetic sentinel files; it never asks a model to read host files.

## Running and reading the probes

From the checkout root:

```sh
python3 plugins/codex-oracle/spike/u0/probe_apply_patch_mediation.py --real --update-report
python3 plugins/codex-oracle/spike/u0/probe_mutator_hooks.py --real --update-report
python3 plugins/codex-oracle/spike/u0/probe_instruction_loading.py --real --update-report
python3 plugins/codex-oracle/spike/u0/probe_file_change_events.py --real --update-report
```

A probe's own writes are its scratch root (removed at the end), its evidence
directory, and, only with `--update-report`, the measured section of this file.
Its authenticated model calls run with the caller's `CODEX_HOME`, where Codex
leaves what any run of it leaves (state databases, logs, installation metadata),
and with the caller's own Codex configuration: its MCP servers, hooks and plugins
run too and write wherever they write, which U0 neither inspects nor bounds.
Fixture-transport calls get a scratch `CODEX_HOME`.

The four probes use only Python's standard library. No dependency is installed by U0.
Importing these modules performs no probe, and bytecode generation is disabled.
Every probe requires `--real`, creates new `git init` repositories below one
owned temporary root, and cleans that root in `finally`/the harness finalizer.
No worktree, repository copy, commit, or branch operation is used by the probes.
Cleanup after an uncatchable termination such as SIGKILL cannot be promised.

Each probe runs its negative calibration first and verifies the relevant tool or
context path. Only then does it enter the tested configuration. The final stdout
line is one JSON object with `probe`, `calibration`, `result`, `verdict`, and
`evidence_paths`, preceded by a short human summary. Retained paths point into
`evidence/`, not deleted temporary fixtures. Raw case files include expanded
argv and monotonic timestamps; `*-receipts.json` contains independently sampled
file states and callback input. `result.json` records cleanup.

The generated section below always selects the latest run of each probe. It
names this checkout `<checkout>` and the home directory `~` (the repository is
public); the retained evidence keeps the full paths. Earlier development
failures remain in evidence as failures; they are not
silently converted to green or overwritten. Calibration occurs before the
listed tested cases. An app-server `hooks/list` subprocess is deliberately
terminated after its response, so its termination exit code is not a failed
model call. The interrupted-command case also intentionally records a nonzero
CLI exit.

## Measured results and exact invocations

<!-- U0 MEASURED START -->

Generated from retained measurements. All argv below are expanded argument arrays; stdin prompts and raw output are in the linked case files.

### probe_apply_patch_mediation

Verdict: **red**. [Durable result](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/result.json).

```json
{
  "metadata": {
    "version": "codex-cli 0.163.0",
    "native": "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
    "sha256": "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31",
    "scratch_root": "/private/tmp/codex-u0-7rezqt2_",
    "date": "2026-10-18T08:38:19+0530",
    "python": "3.15.0 (main, Oct  9 2026, 11:16:41) [Clang 21.0.0 (clang-2100.3.34.2)]",
    "probe_code_sha256": {
      "_command_fixture.py": "737c1df82b4865976170f65b52b57888093d148cd9601c1f77aef221d6646d0c",
      "_common.py": "814827a2acdf81d5a9ed0c46d8ece6c4b9bc97c7f22fe9b1e088ded7103d3715",
      "_coordinator.py": "c766fd5604497880e6726fc37c0baa7b0c7e3f7d6a0d77132241a2f76f1f04d3",
      "_discovery.py": "169dfde0bde10dd738a108cd953975ce75205bc493bbbdc7d9d016f2b51030d0",
      "_mcp_canary.py": "656f0118deeb456adf088ad2f1256a8aa0705efa6cc80fb19ba2495befee88a2",
      "_responses_fixture.py": "3d4c5c068fa9fc6dd8e27c2ba52608413908ca18cde29e679166e36d0176a450",
      "_scope_selftest.py": "145725dfa512bee2c04d12794dfdf007784d62e168b29ffae9e901502b24677a",
      "_verify.py": "7e8c5cc2d1f357b3812cdaa4d6e8997d12cda309a2eab227e3edd67a4fa97751",
      "probe_apply_patch_mediation.py": "361625c61ef9f8f4cc4766c24225c5e378ccb33fbeb5ae04e01502e81ba347af",
      "probe_file_change_events.py": "bae5cb5e0b4cbfe37b63b2e89b838c578af0f2ebb94be9637762a01c55268fa4",
      "probe_instruction_loading.py": "0880857dc1ea5a1b52eb96c99ec140c77968e201d10fa88b1290662323d565ea",
      "probe_mutator_hooks.py": "3f355e9895d5c17b132c4512d473daa16d6315fe74c1bf858359e8bfd6e31032"
    }
  },
  "calibration": {
    "verdict": "red",
    "configuration": "features.hooks=false; authenticated direct patch",
    "observed": "Patch changed the fixture without a coordinator receipt."
  },
  "cleanup": {
    "scratch_root": "/private/tmp/codex-u0-7rezqt2_",
    "gone": true,
    "owned_processes_reaped": true,
    "fixture_pids_checked": [
      55136,
      55169,
      55202,
      55229,
      55259,
      55265,
      55549,
      55612,
      55625,
      55626,
      55645,
      55674,
      55696,
      55727,
      55759,
      55792,
      55824,
      55851,
      55884,
      55930,
      55985,
      56056,
      56082,
      56115,
      56116,
      56117,
      56118,
      56152,
      56182,
      56247,
      56248,
      56249,
      56285,
      56286,
      56287,
      56288
    ],
    "fixture_processes_remaining": [],
    "errors": [],
    "elapsed_seconds": 0.232627417
  },
  "error": null
}
```

- **calibration**: Authenticated patch applied without mediation.
- **01-complete-input**: Mode=observe; callbacks=['apply_patch']; changed=True; CAS=[]
- **02-explicit-deny**: Mode=deny; callbacks=['apply_patch']; changed=False; CAS=[]
- **03-rewrite**: Mode=rewrite; callbacks=['apply_patch']; changed=True; CAS=[]
- **04-coordinator-apply**: Mode=apply-and-deny; callbacks=['apply_patch']; changed=True; CAS=['applied']
- **04b-duplicate-operation**: Mode=apply-and-deny; callbacks=['apply_patch', 'apply_patch']; changed=True; CAS=['applied', 'duplicate']
- **05-authenticated-coordinator**: Mode=apply-and-deny; callbacks=['apply_patch']; changed=True; CAS=['applied']
- **authenticated-model-response**: Model response after coordinator denial: I’ll apply the patch exactly once and report the tool result.

The tool reported that the coordinator applied the patch and blocked native execution: `U0_COORDINATOR applied; native apply suppressed`. I did not retry or use another tool.
- **06-stale-preimage**: Mode=apply-and-deny; callbacks=['apply_patch']; changed=False; CAS=['stale_preimage']
- **concurrent-cooperative-cas**: Two operations with the same base and shared lock: ['applied', 'stale_preimage']. Only one applied.
- **07-validation-failure**: Mode=observe; callbacks=['apply_patch']; changed=False; CAS=[]
- **08-runtime-partial-failure**: Mode=observe; callbacks=['apply_patch']; changed=True; CAS=[]
- **09-coordinator-partial-failure**: Mode=apply-and-deny; callbacks=['apply_patch']; changed=True; CAS=['failed_or_partial']
- **failure-crash**: Mode=crash; callbacks=['apply_patch']; changed=True; CAS=[]
- **failure-exit1**: Mode=exit1; callbacks=['apply_patch']; changed=True; CAS=[]
- **failure-exit2-reason**: Mode=exit2-reason; callbacks=['apply_patch']; changed=False; CAS=[]
- **failure-exit2-empty**: Mode=exit2-empty; callbacks=['apply_patch']; changed=True; CAS=[]
- **failure-malformed**: Mode=malformed; callbacks=['apply_patch']; changed=True; CAS=[]
- **failure-timeout**: Mode=timeout; callbacks=['apply_patch']; changed=True; CAS=[]
- **failure-apply-and-crash**: Mode=apply-and-crash; callbacks=['apply_patch']; changed=True; CAS=['applied']
- **failure-matrix**: {"crash": {"native_attempted": true, "changed": true, "native_validation_failed": false, "tool_outputs": ["Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM a.txt\n"], "statuses": ["in_progress", "completed"]}, "exit1": {"native_attempted": true, "changed": true, "native_validation_failed": false, "tool_outputs": ["Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM a.txt\n"], "statuses": ["in_progress", "completed"]}, "exit2-reason": {"native_attempted": false, "changed": false, "native_validation_failed": false, "tool_outputs": ["Command blocked by PreToolUse hook: U0_EXIT2_DENY. Command: *** Begin Patch\n*** Update File: a.txt\n@@\n-base\n+changed\n*** End Patch\n"], "statuses": []}, "exit2-empty": {"native_attempted": true, "changed": true, "native_validation_failed": false, "tool_outputs": ["Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM a.txt\n"], "statuses": ["in_progress", "completed"]}, "malformed": {"native_attempted": true, "changed": true, "native_validation_failed": false, "tool_outputs": ["Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM a.txt\n"], "statuses": ["in_progress", "completed"]}, "timeout": {"native_attempted": true, "changed": true, "native_validation_failed": false, "tool_outputs": ["Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM a.txt\n"], "statuses": ["in_progress", "completed"]}, "apply-and-crash": {"native_attempted": true, "changed": true, "native_validation_failed": true, "tool_outputs": ["apply_patch verification failed: Failed to find expected lines in /private/tmp/codex-u0-7rezqt2_/failure-apply-and-crash/a.txt:\nbase"], "statuses": []}}
- **shell-bypass**: Mode=deny; callbacks=[]; changed=True; CAS=[]
- **shell-patch**: Mode=observe; callbacks=['Bash']; changed=True; CAS=[]
- **shell-patch-callbacks**: Shell apply_patch callback names=['Bash']
- **trust-no-trust**: Canaries executed=[]; patch applied=True; hooks/list identity and trust state retained.
- **trust-coordinator-only**: Canaries executed=['coordinator']; patch applied=False; hooks/list identity and trust state retained.
- **trust-persisted-trust**: Canaries executed=['coordinator']; patch applied=False; hooks/list identity and trust state retained.
- **trust-trusted-user-sources**: Canaries executed=['adjacent-json', 'coordinator', 'project-toml', 'user-toml']; patch applied=False; hooks/list identity and trust state retained.
- **trust-untrusted-user**: Canaries executed=['coordinator']; patch applied=False; hooks/list identity and trust state retained.
- **trust-disabled-user**: Canaries executed=['coordinator']; patch applied=False; hooks/list identity and trust state retained.
- **trust-stale-trust**: Canaries executed=[]; patch applied=True; hooks/list identity and trust state retained.
- **trust-adjacent-ignore-user-config**: Canaries executed=['adjacent-json', 'coordinator', 'project-toml']; patch applied=False; hooks/list identity and trust state retained.
- **trust-blanket-bypass**: Canaries executed=['adjacent-json', 'coordinator', 'project-toml', 'user-toml']; patch applied=False; hooks/list identity and trust state retained.
- **trust-plugin-source**: No synthetic plugin is configured; plugin loading is not claimed measured. User TOML, adjacent JSON, project TOML, and invocation sources were measured.
- **conclusion**: Complete input, rejection, rewrite and cooperative CAS are usable. Full-access complete mediation is disproved by shell bypass and measured hook failure behavior.

<details><summary>Exact invocation arrays (calibration first)</summary>

[00-calibration](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/00-calibration.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/calibration",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=false",
  "-"
]
```

[01-complete-input](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/01-complete-input.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/01-complete-input",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:b2dbc78c574ec39b34d1f35401467403786b586254aa8be7af641a0d11e697cb\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-9e678a0fe6/manifest.json observe coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53327/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[02-explicit-deny](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/02-explicit-deny.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/02-explicit-deny",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:fa9eb8d43316a6d973684c63e4546393217c5ed30c3112294c427b3d5353ff59\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-dcbec9ee61/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53333/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[03-rewrite](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/03-rewrite.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/03-rewrite",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:010478e947abf621963cb813723482c0a7d7cd7ab32cdd0c9410e5fc3955a4a2\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-6ad9d38b9e/manifest.json rewrite coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53343/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[04-coordinator-apply](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/04-coordinator-apply.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/04-coordinator-apply",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:b88a5955649dd622f5f84f8809db78fd19f988c9cbdef4c5915d95fe41ba3a14\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-bb701ecc3e/manifest.json apply-and-deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53349/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[04b-duplicate-operation](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/04b-duplicate-operation.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/04b-duplicate-operation",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:42e47951ed846b91c863909c351b0326d7d144d368f96315a5edaf01b8d64014\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-742e93452c/manifest.json apply-and-deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53359/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[05-authenticated-coordinator](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/05-authenticated-coordinator.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/05-authenticated-coordinator",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:279297d31b6b70a86d07568e71dfebb92e4e699ac74883ff49d59fdc68379d6d\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-4562121fcc/manifest.json apply-and-deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-"
]
```

[06-stale-preimage](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/06-stale-preimage.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/06-stale-preimage",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:a4f2a032051dbbe119eb5d603de4028af8773d57837b66f920fc22cc0d2e6df9\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-d01322da03/manifest.json apply-and-deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53460/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[concurrent-0](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/concurrent-0.json) — local subprocess; exit 0.

```json
[
  "/opt/homebrew/opt/python@3.15/bin/python3.15",
  "<checkout>/plugins/codex-oracle/spike/u0/_coordinator.py",
  "hook",
  "/private/tmp/codex-u0-7rezqt2_/control-c6c2458083/concurrent-0.json",
  "apply-and-deny",
  "concurrent-0"
]
```

[concurrent-1](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/concurrent-1.json) — local subprocess; exit 0.

```json
[
  "/opt/homebrew/opt/python@3.15/bin/python3.15",
  "<checkout>/plugins/codex-oracle/spike/u0/_coordinator.py",
  "hook",
  "/private/tmp/codex-u0-7rezqt2_/control-c6c2458083/concurrent-1.json",
  "apply-and-deny",
  "concurrent-1"
]
```

[07-validation-failure](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/07-validation-failure.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/07-validation-failure",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:e7a8fdfa20f1c1228f478d6937dd48927505a2d8d1a38a47fe451dd38285b718\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-b9acc117e4/manifest.json observe coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53470/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[08-runtime-partial-failure](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/08-runtime-partial-failure.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/08-runtime-partial-failure",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:e40434f0f9940e3f13462c467ef5da423388f3dd9d91efb8d9f0c079d4a725a3\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-07ded74c77/manifest.json observe coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53480/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[09-coordinator-partial-failure](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/09-coordinator-partial-failure.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/09-coordinator-partial-failure",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:c13a0683040160408cde5544474221861c5aab45a3ff330ce17308deb8df357c\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-248e96c325/manifest.json apply-and-deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53488/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[failure-crash](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/failure-crash.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/failure-crash",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:69e0ae1cbc8f432ec5aebc74ad426a32360bf50b753dd09088fa90eae902b382\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-57c4cc038c/manifest.json crash coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53499/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[failure-exit1](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/failure-exit1.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/failure-exit1",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:bab5d75351c623ba3a76c90e0da37201f936971ff8764ec653ec466a7de897db\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-bbe76154b0/manifest.json exit1 coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53505/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[failure-exit2-reason](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/failure-exit2-reason.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/failure-exit2-reason",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:a8fdcbbbeef300ed8845b4831fa2c5ad84cc28054cd2a0247845d9c022fdc6c3\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-33805aad95/manifest.json exit2-reason coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53515/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[failure-exit2-empty](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/failure-exit2-empty.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/failure-exit2-empty",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:66a7206fdb1a2bb4d48c41798d771024fa2b16a4250d644577e4f9b574c79d79\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-e7648c9101/manifest.json exit2-empty coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53525/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[failure-malformed](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/failure-malformed.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/failure-malformed",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:393594eedaa7c0111059d8d60283b20d302cb2343d8d827616a3c616ef9d6ead\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-37669bdf7f/manifest.json malformed coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53535/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[failure-timeout](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/failure-timeout.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/failure-timeout",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:c11d3a292bf1307b85b6c86c839a8a8263a08a7edfa255534667acc7f3a702ea\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-2cc94e9994/manifest.json timeout coordinator\",\"timeout\"=1,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53541/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[failure-apply-and-crash](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/failure-apply-and-crash.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/failure-apply-and-crash",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:5013c67df8133c470f530987f0722c0a7c168b0b5c0cc7e50beb6d85a2560239\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-b3022df20b/manifest.json apply-and-crash coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53557/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[shell-bypass](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/shell-bypass.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/shell-bypass",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:d4469927dec0c38848b527597356297afa4f4b2668c91773252f8ff83273340c\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-3863445c79/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53567/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[shell-patch](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/shell-patch.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/shell-patch",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:7bb38692ca099567fc93371822e0b434ed5213e803af901c85f6d00cf0f5f470\"},\"/<session-flags>/config.toml:pre_tool_use:1:0\"={\"trusted_hash\"=\"sha256:8507ff81efb53299d3673ef306c9cd277579886b9b4b4122a4c863cdcdb4b84f\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-538fbd0a9f/manifest.json observe coordinator\",\"timeout\"=2,\"async\"=false}]},{\"matcher\"=\"^Bash$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-538fbd0a9f/manifest.json observe bash\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53573/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[trust-00-no-trust-hooks-list](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-00-no-trust-hooks-list.json) — local_app_server_metadata; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "app-server",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-b1b5aee308/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]"
]
```

[trust-no-trust](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-no-trust.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/trust-no-trust",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-b1b5aee308/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53583/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[trust-01-coordinator-only-hooks-list](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-01-coordinator-only-hooks-list.json) — local_app_server_metadata; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "app-server",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:bfc7c4acf06054ba573fbe3f75de7ae7e1a7219e32567d6bad42471f24bebaee\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-a53eb01437/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]"
]
```

[trust-coordinator-only](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-coordinator-only.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/trust-coordinator-only",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:bfc7c4acf06054ba573fbe3f75de7ae7e1a7219e32567d6bad42471f24bebaee\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-a53eb01437/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53594/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[trust-02-persisted-trust-hooks-list](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-02-persisted-trust-hooks-list.json) — local_app_server_metadata; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "app-server"
]
```

[trust-persisted-trust](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-persisted-trust.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/trust-persisted-trust",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53601/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[trust-03-trusted-user-sources-hooks-list](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-03-trusted-user-sources-hooks-list.json) — local_app_server_metadata; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "app-server",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:dfb6ac6275d2e435d180a562f00af7c4f7ce9bb62ec613c79d26437872c6bf3a\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-8e2bc938f2/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]"
]
```

[trust-trusted-user-sources](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-trusted-user-sources.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/trust-trusted-user-sources",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:dfb6ac6275d2e435d180a562f00af7c4f7ce9bb62ec613c79d26437872c6bf3a\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-8e2bc938f2/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53612/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[trust-04-untrusted-user-hooks-list](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-04-untrusted-user-hooks-list.json) — local_app_server_metadata; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "app-server",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:78940038755d27c837997be997a888a55188d765d32dc8fb605a3f2329b1228f\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-63943bbc07/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]"
]
```

[trust-untrusted-user](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-untrusted-user.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/trust-untrusted-user",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:78940038755d27c837997be997a888a55188d765d32dc8fb605a3f2329b1228f\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-63943bbc07/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53620/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[trust-05-disabled-user-hooks-list](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-05-disabled-user-hooks-list.json) — local_app_server_metadata; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "app-server",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:750ca111d43f8e61206d4e2f9143c82889a754e86d2854ed81b308a730cb4071\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-f385d600c2/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]"
]
```

[trust-disabled-user](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-disabled-user.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/trust-disabled-user",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:750ca111d43f8e61206d4e2f9143c82889a754e86d2854ed81b308a730cb4071\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-f385d600c2/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53631/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[trust-06-stale-trust-hooks-list](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-06-stale-trust-hooks-list.json) — local_app_server_metadata; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "app-server",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:be793fa40a1718dd3a56785cd60140b23fef81742ad7c7c4ba244c3d1fb1c920\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-1266d96c2d/manifest.json deny changed-definition\",\"timeout\"=2,\"async\"=false}]}]"
]
```

[trust-stale-trust](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-stale-trust.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/trust-stale-trust",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:be793fa40a1718dd3a56785cd60140b23fef81742ad7c7c4ba244c3d1fb1c920\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-1266d96c2d/manifest.json deny changed-definition\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53642/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[trust-07-adjacent-ignore-user-config-hooks-list](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-07-adjacent-ignore-user-config-hooks-list.json) — local_app_server_metadata; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "app-server",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:6cf3a2413f523f929b515f416b1a757e7df6299d43194344dcc5b9f201bfc699\"},\"/private/tmp/codex-u0-7rezqt2_/trust-home-adjacent-ignore-user-config/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:e46808dec46fdb9363f17e859c261006e165d5ac629398d96ecb581128785413\"},\"/private/tmp/codex-u0-7rezqt2_/trust-home-adjacent-ignore-user-config/hooks.json:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:8a3edd22e5d56d32c04f3d08495363ad9b0d05ae09168da3fc0420965fe6720d\"},\"/private/tmp/codex-u0-7rezqt2_/trust-adjacent-ignore-user-config/.codex/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:0d0cf5c361f20b084715f5d5e4053769631921e757ca9085eb44103c876c404d\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-9ca1a6909f/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "projects={\"/private/tmp/codex-u0-7rezqt2_/trust-adjacent-ignore-user-config\"={\"trust_level\"=\"trusted\"}}"
]
```

[trust-adjacent-ignore-user-config](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-adjacent-ignore-user-config.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/trust-adjacent-ignore-user-config",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:6cf3a2413f523f929b515f416b1a757e7df6299d43194344dcc5b9f201bfc699\"},\"/private/tmp/codex-u0-7rezqt2_/trust-home-adjacent-ignore-user-config/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:e46808dec46fdb9363f17e859c261006e165d5ac629398d96ecb581128785413\"},\"/private/tmp/codex-u0-7rezqt2_/trust-home-adjacent-ignore-user-config/hooks.json:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:8a3edd22e5d56d32c04f3d08495363ad9b0d05ae09168da3fc0420965fe6720d\"},\"/private/tmp/codex-u0-7rezqt2_/trust-adjacent-ignore-user-config/.codex/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:0d0cf5c361f20b084715f5d5e4053769631921e757ca9085eb44103c876c404d\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-9ca1a6909f/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "projects={\"/private/tmp/codex-u0-7rezqt2_/trust-adjacent-ignore-user-config\"={\"trust_level\"=\"trusted\"}}",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53649/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "--ignore-user-config",
  "-"
]
```

[trust-08-blanket-bypass-hooks-list](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-08-blanket-bypass-hooks-list.json) — local_app_server_metadata; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "app-server",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:aa658223dd587fe527d03ff7a6ba29f182d42a2860a1cd5fb0de9034aab1060c\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-2a2bab0f1b/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]"
]
```

[trust-blanket-bypass](evidence/probe_apply_patch_mediation/20261018T083819-8c8a25c5/trust-blanket-bypass.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-7rezqt2_/trust-blanket-bypass",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:aa658223dd587fe527d03ff7a6ba29f182d42a2860a1cd5fb0de9034aab1060c\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^apply_patch$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-7rezqt2_/control-2a2bab0f1b/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53660/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "--dangerously-bypass-hook-trust",
  "-"
]
```

</details>

### probe_mutator_hooks

Verdict: **red**. [Durable result](evidence/probe_mutator_hooks/20261018T083906-61a51673/result.json).

```json
{
  "metadata": {
    "version": "codex-cli 0.163.0",
    "native": "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
    "sha256": "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31",
    "scratch_root": "/private/tmp/codex-u0-ducixce9",
    "date": "2026-10-18T08:39:07+0530",
    "python": "3.15.0 (main, Oct  9 2026, 11:16:41) [Clang 21.0.0 (clang-2100.3.34.2)]",
    "probe_code_sha256": {
      "_command_fixture.py": "737c1df82b4865976170f65b52b57888093d148cd9601c1f77aef221d6646d0c",
      "_common.py": "814827a2acdf81d5a9ed0c46d8ece6c4b9bc97c7f22fe9b1e088ded7103d3715",
      "_coordinator.py": "c766fd5604497880e6726fc37c0baa7b0c7e3f7d6a0d77132241a2f76f1f04d3",
      "_discovery.py": "169dfde0bde10dd738a108cd953975ce75205bc493bbbdc7d9d016f2b51030d0",
      "_mcp_canary.py": "656f0118deeb456adf088ad2f1256a8aa0705efa6cc80fb19ba2495befee88a2",
      "_responses_fixture.py": "3d4c5c068fa9fc6dd8e27c2ba52608413908ca18cde29e679166e36d0176a450",
      "_scope_selftest.py": "145725dfa512bee2c04d12794dfdf007784d62e168b29ffae9e901502b24677a",
      "_verify.py": "7e8c5cc2d1f357b3812cdaa4d6e8997d12cda309a2eab227e3edd67a4fa97751",
      "probe_apply_patch_mediation.py": "361625c61ef9f8f4cc4766c24225c5e378ccb33fbeb5ae04e01502e81ba347af",
      "probe_file_change_events.py": "bae5cb5e0b4cbfe37b63b2e89b838c578af0f2ebb94be9637762a01c55268fa4",
      "probe_instruction_loading.py": "0880857dc1ea5a1b52eb96c99ec140c77968e201d10fa88b1290662323d565ea",
      "probe_mutator_hooks.py": "3f355e9895d5c17b132c4512d473daa16d6315fe74c1bf858359e8bfd6e31032"
    }
  },
  "calibration": {
    "verdict": "red",
    "configuration": "features.hooks=false; authenticated shell mutation",
    "observed": "Shell mutated the file without any coordinator pre/post window."
  },
  "cleanup": {
    "scratch_root": "/private/tmp/codex-u0-ducixce9",
    "gone": true,
    "owned_processes_reaped": true,
    "fixture_pids_checked": [
      56645,
      56896,
      56904,
      56905,
      56935,
      56952,
      56957,
      56958,
      56967,
      56984,
      56989,
      56990,
      56999,
      57017,
      57024,
      57041,
      57046,
      57069,
      57084,
      57101,
      57106,
      57114,
      57124,
      57141,
      57146,
      57147,
      57148,
      57162,
      57187,
      57192,
      57193,
      57195,
      57215,
      57220,
      57251,
      57300,
      57540,
      57544,
      57619,
      57623,
      57642,
      57643,
      57676,
      57689,
      57695,
      57714,
      57716,
      57720,
      57746,
      57747
    ],
    "fixture_processes_remaining": [],
    "errors": [],
    "elapsed_seconds": 0.18657
  },
  "error": null
}
```

- **calibration**: Authenticated shell write succeeded without pre/post receipts.
- **01-authenticated-short**: {"pre_count": 1, "post_count": 1, "ordered_window": true, "mutation_after_post": false, "tool_names": ["Bash"], "tool_use_ids": ["exec-c2e47b59-d8c8-4e30-87e3-bdfa6cb5e55d", "exec-c2e47b59-d8c8-4e30-87e3-bdfa6cb5e55d"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "f9b0078b5df596d2ea19010c001bbd009e651de2c57e8fb7e355f31eb9d3f739", "size": 5}}}
- **02-short**: {"pre_count": 1, "post_count": 1, "ordered_window": true, "mutation_after_post": false, "tool_names": ["Bash"], "tool_use_ids": ["u0-093e128c8a0a469da9d40fffda5addb8", "u0-093e128c8a0a469da9d40fffda5addb8"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "f9b0078b5df596d2ea19010c001bbd009e651de2c57e8fb7e355f31eb9d3f739", "size": 5}}}
- **03-nonzero**: {"pre_count": 1, "post_count": 1, "ordered_window": true, "mutation_after_post": false, "tool_names": ["Bash"], "tool_use_ids": ["u0-8238db34485b4652b317c6327248b32c", "u0-8238db34485b4652b317c6327248b32c"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "a294469efce3e2bf005427a1180ae291eb19d65b7c3c745b4d185df23eae6235", "size": 7}}}
- **04-handler-error**: {"pre_count": 1, "post_count": 0, "ordered_window": false, "mutation_after_post": false, "tool_names": ["Bash"], "tool_use_ids": ["u0-56175d9b0c864b61978365e632572eae"], "window_acquires": 1, "window_releases": 0, "file_states": {"marker.txt": {"type": "absent"}}}
- **05-yield**: {"pre_count": 1, "post_count": 1, "ordered_window": true, "mutation_after_post": false, "tool_names": ["Bash"], "tool_use_ids": ["u0-ca1b0dc06b0e403abef11fa8903ba0e9", "u0-ca1b0dc06b0e403abef11fa8903ba0e9"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "8bbcba42d48dcffe6ea6efa3f5656ec8b8e0d5c931091c17a12516eb5d193e41", "size": 5}}}
- **06-interactive**: {"pre_count": 1, "post_count": 1, "ordered_window": true, "mutation_after_post": false, "tool_names": ["Bash"], "tool_use_ids": ["u0-c4cde6d940364c31a87de24af9d7287e", "u0-c4cde6d940364c31a87de24af9d7287e"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "0eb42ac4a5a8a5854f10dee4626428dae886152474ae31a2d2987a50b64a5947", "size": 11}}}
- **07-background**: {"pre_count": 1, "post_count": 1, "ordered_window": false, "mutation_after_post": true, "tool_names": ["Bash"], "tool_use_ids": ["u0-f181484e305d4bc3bd67ec0412080354", "u0-f181484e305d4bc3bd67ec0412080354"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "ddc9e669194254cef019a29d3619a2c16592e5d52e1a81e98b01bd52319149a3", "size": 5}}}
- **08-workdir**: {"pre_count": 1, "post_count": 1, "ordered_window": true, "mutation_after_post": false, "tool_names": ["Bash"], "tool_use_ids": ["u0-369d22568fef4cd9b7a72975b6e39a47", "u0-369d22568fef4cd9b7a72975b6e39a47"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "2a3fed0cd25640b301de61516322115f77c886d097f0f7fa1d6964090b468104", "size": 7}}}
- **09-interrupted**: {"pre_count": 1, "post_count": 0, "ordered_window": false, "mutation_after_post": false, "tool_names": ["Bash"], "tool_use_ids": ["u0-ee3658ad2f924affbe825b3e077f0189"], "window_acquires": 1, "window_releases": 0, "file_states": {"marker.txt": {"type": "absent"}}}
- **10-authenticated-mcp**: {"pre_count": 1, "post_count": 1, "ordered_window": true, "mutation_after_post": false, "tool_names": ["mcp__u0_canary__write_marker"], "tool_use_ids": ["exec-f5f1531c-db9f-485d-9f01-6380146b4b80", "exec-f5f1531c-db9f-485d-9f01-6380146b4b80"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "e724bd8cad27d8fd6137bd6dd892468b54248fa5b7b50fb77207906e526f541d", "size": 10}}}
- **11-mcp-success**: {"pre_count": 1, "post_count": 1, "ordered_window": true, "mutation_after_post": false, "tool_names": ["mcp__u0_canary__write_marker"], "tool_use_ids": ["exec-5e84dc82-0696-4d40-8289-e86cccbc2b48", "exec-5e84dc82-0696-4d40-8289-e86cccbc2b48"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "e724bd8cad27d8fd6137bd6dd892468b54248fa5b7b50fb77207906e526f541d", "size": 10}}}
- **12-mcp-denial**: {"pre_count": 1, "post_count": 0, "ordered_window": false, "mutation_after_post": false, "tool_names": ["mcp__u0_canary__write_marker"], "tool_use_ids": ["exec-346cfdd2-170a-4e3c-8465-a4393a3d4857"], "window_acquires": 0, "window_releases": 0, "file_states": {"marker.txt": {"type": "absent"}}}
- **13-mcp-error**: {"pre_count": 1, "post_count": 0, "ordered_window": false, "mutation_after_post": false, "tool_names": ["mcp__u0_canary__write_marker"], "tool_use_ids": ["exec-d919186d-787d-48d7-b776-d5bc1814660f"], "window_acquires": 1, "window_releases": 0, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "e724bd8cad27d8fd6137bd6dd892468b54248fa5b7b50fb77207906e526f541d", "size": 10}}}
- **14-mcp-delayed**: {"pre_count": 1, "post_count": 1, "ordered_window": true, "mutation_after_post": false, "tool_names": ["mcp__u0_canary__write_marker"], "tool_use_ids": ["exec-378acdd3-3287-4544-a4da-c9a476aa38df", "exec-378acdd3-3287-4544-a4da-c9a476aa38df"], "window_acquires": 1, "window_releases": 1, "file_states": {"marker.txt": {"mode": 420, "type": "file", "sha256": "e724bd8cad27d8fd6137bd6dd892468b54248fa5b7b50fb77207906e526f541d", "size": 10}}}
- **conclusion**: Shell Bash and canonical MCP pre/post interception exist. Background mutations after post and missing error/interruption post callbacks prevent treating post as a universal finally block. Windows require persistent custody and explicit recovery.

<details><summary>Exact invocation arrays (calibration first)</summary>

[00-calibration](evidence/probe_mutator_hooks/20261018T083906-61a51673/00-calibration.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/calibration",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=false",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:014e97e828148defac943abb0963e973cf462e6610a2411a4e337a34f7f7708f\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:3b6aae2149f21afb4ffdaa0501527f81c6295bbe6a83d8f17c1ecc5eac1c96fd\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^Bash$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-5bd075225a/manifest.json observe coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^Bash$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-5bd075225a/manifest.json observe coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-"
]
```

[01-authenticated-short](evidence/probe_mutator_hooks/20261018T083906-61a51673/01-authenticated-short.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/01-authenticated-short",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:7398d22196776e3d45cb6cfa527526f5efcf1f96593f89bf11071d3cf2579f94\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:e004c641f5a4cb29e7ddfc1f283f2bd277f0b1611697288c6038f7ab9bd150f1\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-760f368600/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-760f368600/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-"
]
```

[02-short](evidence/probe_mutator_hooks/20261018T083906-61a51673/02-short.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/02-short",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:e9a81b668e006364ebab0c5f8b7d8cb5bd3f2e0a75beeab1db364187e83242fb\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:f9146bbdf268104bc0f81f4266eafb30ceb57d547028ca7d22f4c43c2fc5c197\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-95c4a07351/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-95c4a07351/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53835/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[03-nonzero](evidence/probe_mutator_hooks/20261018T083906-61a51673/03-nonzero.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/03-nonzero",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:d1ee26e37e49d8a16d038fc4d195cc3719a541134490eeb53d1dc3e52afa8ab3\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:cd2dc7aee26ed6fb2a8a4554eb1b076d02e6605abbd30672c566ee5666c04436\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-fad6e59301/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-fad6e59301/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53848/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[04-handler-error](evidence/probe_mutator_hooks/20261018T083906-61a51673/04-handler-error.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/04-handler-error",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:c4efef2cbd4afaf32970bf9f4e612a66ab1ed0970d913a33a69e8bcf6a81c3ec\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:12b2b41759aaaac2d52cf6a51f5b73b2c04418f7ab9a34f2ecfbbe8f6999dc43\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-8fd2ed1a31/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-8fd2ed1a31/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53861/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[05-yield](evidence/probe_mutator_hooks/20261018T083906-61a51673/05-yield.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/05-yield",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:82ce6f52c646f26d2adf118c286df10b994037f49b64f081731d52b6698d7f49\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:9c5e73dbf55ebf159c182f480cc05a23c2beb9fb92d40fdb03a0ae5ef32641fd\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-03d9db262d/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-03d9db262d/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53873/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[06-interactive](evidence/probe_mutator_hooks/20261018T083906-61a51673/06-interactive.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/06-interactive",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:4ae755c61ab271eb6fbf10be238b1181bb368dd5add94d551e689b41afd2cb15\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:ac399f2de4ae506410ff7f77f075cb96624797d84ddf0536f4737da187918443\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-f0be342eac/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-f0be342eac/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53896/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[07-background](evidence/probe_mutator_hooks/20261018T083906-61a51673/07-background.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/07-background",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:541870ea2142e429f74ed4234c8cb8c7c8449979eaa42b71aefe4e56f0352a46\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:3064d8b9661306b1622099e8585c5594bd56efb18aea310e503bb331914111ed\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-9a65b57958/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-9a65b57958/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53915/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[08-workdir](evidence/probe_mutator_hooks/20261018T083906-61a51673/08-workdir.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/08-workdir",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:d48799fed6be82c5b86260d8111395cece837237ace4434713153bf078588034\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:d507c18c219ffa7197dfc31cc7bbbf70456c16d27ecbefc4c19a0f367cb583f1\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-f7a55b8f10/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-f7a55b8f10/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53928/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[09-interrupted](evidence/probe_mutator_hooks/20261018T083906-61a51673/09-interrupted.json) — local_fixture_transport; exit 1.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/09-interrupted",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:cdfa389d0c9f8bc6c5b8fb9447e90f09498dfd25f876fb92b93dda972ca5b84b\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:03849e2eb27b066697584f749487756ea6050e0886275f5dfa60397719f9936a\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-33af9a8675/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-33af9a8675/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:53941/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[10-authenticated-mcp](evidence/probe_mutator_hooks/20261018T083906-61a51673/10-authenticated-mcp.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/10-authenticated-mcp",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:308be2f6a0ef257b60388dbe22e8d63f2e69519703e3d133e5fb9a13a4aded03\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:c795b6360fc5a20a4b0cfd8fcbe3e02599f941bbcbb256fa19a8be0d61bb72a9\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-80fda7aed9/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-80fda7aed9/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "mcp_servers.u0_canary={\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15\",\"args\"=[\"<checkout>/plugins/codex-oracle/spike/u0/_mcp_canary.py\",\"/private/tmp/codex-u0-ducixce9/control-80fda7aed9/manifest.json\"],\"startup_timeout_sec\"=10,\"tool_timeout_sec\"=10}",
  "-"
]
```

[11-mcp-success](evidence/probe_mutator_hooks/20261018T083906-61a51673/11-mcp-success.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/11-mcp-success",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:9eab7dfc0ae83857ba1c0ef7e4f0bfa851eaa2cd3b4755d8c2743b029dfbb6d6\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:b8b8a7700d318a0608c91d5312e5deb0656a87da25febfa3d27e7c37aca57296\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-eb922b3bd7/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-eb922b3bd7/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "mcp_servers.u0_canary={\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15\",\"args\"=[\"<checkout>/plugins/codex-oracle/spike/u0/_mcp_canary.py\",\"/private/tmp/codex-u0-ducixce9/control-eb922b3bd7/manifest.json\"],\"startup_timeout_sec\"=10,\"tool_timeout_sec\"=10}",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:54049/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[12-mcp-denial](evidence/probe_mutator_hooks/20261018T083906-61a51673/12-mcp-denial.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/12-mcp-denial",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:85a2a0f8e726ca5903375655ec6eedafc0405842aec6b3e5d97d3f0f925708e4\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:f37943d2f832502aa5408ab097afe9c1531c6d531789ec62aba2a2572b4e7074\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-b7f9e6ad56/manifest.json deny coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-b7f9e6ad56/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "mcp_servers.u0_canary={\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15\",\"args\"=[\"<checkout>/plugins/codex-oracle/spike/u0/_mcp_canary.py\",\"/private/tmp/codex-u0-ducixce9/control-b7f9e6ad56/manifest.json\"],\"startup_timeout_sec\"=10,\"tool_timeout_sec\"=10}",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:54063/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[13-mcp-error](evidence/probe_mutator_hooks/20261018T083906-61a51673/13-mcp-error.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/13-mcp-error",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:0ca0c00205453879c4bbe240630e1c50484078ee92bf12feab0e2344dd2c57ab\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:d1748ffa1ba407ad464aabd68ed1ef73f7eeefd6f11aa29f0d402c29d412636b\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-b7c604c319/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-b7c604c319/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "mcp_servers.u0_canary={\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15\",\"args\"=[\"<checkout>/plugins/codex-oracle/spike/u0/_mcp_canary.py\",\"/private/tmp/codex-u0-ducixce9/control-b7c604c319/manifest.json\"],\"startup_timeout_sec\"=10,\"tool_timeout_sec\"=10}",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:54070/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[14-mcp-delayed](evidence/probe_mutator_hooks/20261018T083906-61a51673/14-mcp-delayed.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-ducixce9/14-mcp-delayed",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=true",
  "-c",
  "hooks.state={\"/<session-flags>/config.toml:pre_tool_use:0:0\"={\"trusted_hash\"=\"sha256:16c1306fa3fe052e585020bab91a438a97fe75b7bb5b4412bc9bd038d8a16417\"},\"/<session-flags>/config.toml:post_tool_use:0:0\"={\"trusted_hash\"=\"sha256:eb7d63cf34691536f946f1d4953a71cd9a74eecbcb4763732339f611fc16cf92\"}}",
  "-c",
  "hooks.PreToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-8a03afabf1/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "hooks.PostToolUse=[{\"matcher\"=\"^(Bash|mcp__u0_canary__write_marker)$\",\"hooks\"=[{\"type\"=\"command\",\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15 <checkout>/plugins/codex-oracle/spike/u0/_coordinator.py hook /private/tmp/codex-u0-ducixce9/control-8a03afabf1/manifest.json window coordinator\",\"timeout\"=2,\"async\"=false}]}]",
  "-c",
  "mcp_servers.u0_canary={\"command\"=\"/opt/homebrew/opt/python@3.15/bin/python3.15\",\"args\"=[\"<checkout>/plugins/codex-oracle/spike/u0/_mcp_canary.py\",\"/private/tmp/codex-u0-ducixce9/control-8a03afabf1/manifest.json\"],\"startup_timeout_sec\"=10,\"tool_timeout_sec\"=10}",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:54082/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

</details>

### probe_instruction_loading

Verdict: **red**. [Durable result](evidence/probe_instruction_loading/20261018T083040-691c0866/result.json).

```json
{
  "metadata": {
    "version": "codex-cli 0.163.0",
    "native": "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
    "sha256": "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31",
    "scratch_root": "/private/tmp/codex-u0-dloul8gh",
    "date": "2026-10-18T08:30:40+0530",
    "python": "3.15.0 (main, Oct  9 2026, 11:16:41) [Clang 21.0.0 (clang-2100.3.34.2)]",
    "probe_code_sha256": {
      "_command_fixture.py": "737c1df82b4865976170f65b52b57888093d148cd9601c1f77aef221d6646d0c",
      "_common.py": "814827a2acdf81d5a9ed0c46d8ece6c4b9bc97c7f22fe9b1e088ded7103d3715",
      "_coordinator.py": "c766fd5604497880e6726fc37c0baa7b0c7e3f7d6a0d77132241a2f76f1f04d3",
      "_discovery.py": "169dfde0bde10dd738a108cd953975ce75205bc493bbbdc7d9d016f2b51030d0",
      "_mcp_canary.py": "656f0118deeb456adf088ad2f1256a8aa0705efa6cc80fb19ba2495befee88a2",
      "_responses_fixture.py": "3d4c5c068fa9fc6dd8e27c2ba52608413908ca18cde29e679166e36d0176a450",
      "_scope_selftest.py": "145725dfa512bee2c04d12794dfdf007784d62e168b29ffae9e901502b24677a",
      "_verify.py": "7e8c5cc2d1f357b3812cdaa4d6e8997d12cda309a2eab227e3edd67a4fa97751",
      "probe_apply_patch_mediation.py": "361625c61ef9f8f4cc4766c24225c5e378ccb33fbeb5ae04e01502e81ba347af",
      "probe_file_change_events.py": "bae5cb5e0b4cbfe37b63b2e89b838c578af0f2ebb94be9637762a01c55268fa4",
      "probe_instruction_loading.py": "0880857dc1ea5a1b52eb96c99ec140c77968e201d10fa88b1290662323d565ea",
      "probe_mutator_hooks.py": "3f355e9895d5c17b132c4512d473daa16d6315fe74c1bf858359e8bfd6e31032"
    }
  },
  "calibration": {
    "verdict": "red",
    "configuration": "project_doc_max_bytes=0",
    "observed": "Authenticated model reported no project sentinels; exact synthetic initial context contained none."
  },
  "cleanup": {
    "scratch_root": "/private/tmp/codex-u0-dloul8gh",
    "gone": true,
    "owned_processes_reaped": true,
    "fixture_pids_checked": [],
    "fixture_processes_remaining": [],
    "errors": [],
    "elapsed_seconds": 0.044389875
  },
  "error": null
}
```

- **01-calibration-context**: Exact initial context contains [].
- **02-defaults-ancestry**: Exact initial context contains ['execution_agents', 'middle_agents', 'root_agents'].
- **03-inside-nested**: Exact initial context contains ['nested_agents'].
- **04-non-repository**: Exact initial context contains ['standalone_agents'].
- **05-fallback-with-agents**: Exact initial context contains ['execution_agents', 'middle_agents', 'root_agents'].
- **06-claude-only-defaults**: Exact initial context contains ['middle_agents', 'root_agents'].
- **07-claude-only-fallback**: Exact initial context contains ['execution_claude', 'middle_agents', 'root_agents'].
- **08-override-precedence**: Exact initial context contains ['execution_override', 'middle_agents', 'root_agents'].
- **09-authenticated-inherited**: Real model reported ['execution_agents_restored', 'middle_agents', 'root_agents']; nonappearance is weaker than exact context exclusion.
- **10-authenticated-defaults**: Real model reported ['execution_agents_restored', 'middle_agents', 'root_agents']; nonappearance is weaker than exact context exclusion.
- **discovery-1000**: {"ordinary_files": 1000, "planted_repositories": 7, "construction_seconds": 0.1128557079937309, "first_traversal_seconds": 0.03584887500619516, "subsequent_seconds": {"min": 0.032532500015804544, "median": 0.03382416698150337, "max": 0.03398099998594262}, "case_insensitive_volume": true, "case_alias_identity_equal": true, "cleanup_seconds": 0.04178095899987966, "scratch_root": "/private/tmp/codex-u0-dloul8gh/discovery-1000-wc5wxx0e", "cleanup_verified": true, "cache_control": "none; first traversal is not claimed cold", "entries_visited": 1029, "directories_visited": 20, "git_metadata_subprocess_count": 7, "repositories_found": 7, "errors": [], "aliases_or_cycles_found": 5}
- **discovery-10000**: {"ordinary_files": 10000, "planted_repositories": 7, "construction_seconds": 0.5764130830066279, "first_traversal_seconds": 0.0441669580177404, "subsequent_seconds": {"min": 0.04259270799229853, "median": 0.04296749999048188, "max": 0.04359154202393256}, "case_insensitive_volume": true, "case_alias_identity_equal": true, "cleanup_seconds": 0.3294240419927519, "scratch_root": "/private/tmp/codex-u0-dloul8gh/discovery-10000-0bv3xriw", "cleanup_verified": true, "cache_control": "none; first traversal is not claimed cold", "entries_visited": 10119, "directories_visited": 110, "git_metadata_subprocess_count": 7, "repositories_found": 7, "errors": [], "aliases_or_cycles_found": 5}
- **discovery-100000**: {"ordinary_files": 100000, "planted_repositories": 7, "construction_seconds": 5.426105125021422, "first_traversal_seconds": 0.12505154201062396, "subsequent_seconds": {"min": 0.1165541659865994, "median": 0.11719587500556372, "max": 0.11792862502625212}, "case_insensitive_volume": true, "case_alias_identity_equal": true, "cleanup_seconds": 3.368558208021568, "scratch_root": "/private/tmp/codex-u0-dloul8gh/discovery-100000-z6cccjw9", "cleanup_verified": true, "cache_control": "none; first traversal is not claimed cold", "entries_visited": 101019, "directories_visited": 1010, "git_metadata_subprocess_count": 7, "repositories_found": 7, "errors": [], "aliases_or_cycles_found": 5}
- **conclusion**: Default instructions follow execution ancestry to the nearest repository boundary. CLAUDE.md requires a fallback and loses to AGENTS.md; nested repositories are not recursively loaded. Discovery is separately required.

<details><summary>Exact invocation arrays (calibration first)</summary>

[00-calibration-model](evidence/probe_instruction_loading/20261018T083040-691c0866/00-calibration-model.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=0",
  "-"
]
```

[01-calibration-context](evidence/probe_instruction_loading/20261018T083040-691c0866/01-calibration-context.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=0",
  "-c",
  "project_doc_fallback_filenames=[]",
  "-c",
  "project_root_markers=[\".git\"]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51050/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[02-defaults-ancestry](evidence/probe_instruction_loading/20261018T083040-691c0866/02-defaults-ancestry.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=32768",
  "-c",
  "project_doc_fallback_filenames=[]",
  "-c",
  "project_root_markers=[\".git\"]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51059/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[03-inside-nested](evidence/probe_instruction_loading/20261018T083040-691c0866/03-inside-nested.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution/nested",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=32768",
  "-c",
  "project_doc_fallback_filenames=[]",
  "-c",
  "project_root_markers=[\".git\"]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51068/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[04-non-repository](evidence/probe_instruction_loading/20261018T083040-691c0866/04-non-repository.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/standalone",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=32768",
  "-c",
  "project_doc_fallback_filenames=[]",
  "-c",
  "project_root_markers=[\".git\"]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51078/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "--skip-git-repo-check",
  "-"
]
```

[05-fallback-with-agents](evidence/probe_instruction_loading/20261018T083040-691c0866/05-fallback-with-agents.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=32768",
  "-c",
  "project_doc_fallback_filenames=[\"CLAUDE.md\"]",
  "-c",
  "project_root_markers=[\".git\"]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51087/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[06-claude-only-defaults](evidence/probe_instruction_loading/20261018T083040-691c0866/06-claude-only-defaults.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=32768",
  "-c",
  "project_doc_fallback_filenames=[]",
  "-c",
  "project_root_markers=[\".git\"]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51096/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[07-claude-only-fallback](evidence/probe_instruction_loading/20261018T083040-691c0866/07-claude-only-fallback.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=32768",
  "-c",
  "project_doc_fallback_filenames=[\"CLAUDE.md\"]",
  "-c",
  "project_root_markers=[\".git\"]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51105/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[08-override-precedence](evidence/probe_instruction_loading/20261018T083040-691c0866/08-override-precedence.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=32768",
  "-c",
  "project_doc_fallback_filenames=[]",
  "-c",
  "project_root_markers=[\".git\"]",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51110/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[09-authenticated-inherited](evidence/probe_instruction_loading/20261018T083040-691c0866/09-authenticated-inherited.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution",
  "-c",
  "model_reasoning_effort=low",
  "-"
]
```

[10-authenticated-defaults](evidence/probe_instruction_loading/20261018T083040-691c0866/10-authenticated-defaults.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-dloul8gh/instructions/root/middle/execution",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "project_doc_max_bytes=32768",
  "-c",
  "project_doc_fallback_filenames=[]",
  "-c",
  "project_root_markers=[\".git\"]",
  "-"
]
```

</details>

### probe_file_change_events

Verdict: **red**. [Durable result](evidence/probe_file_change_events/20261018T083129-87d0773d/result.json).

```json
{
  "metadata": {
    "version": "codex-cli 0.163.0",
    "native": "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
    "sha256": "8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31",
    "scratch_root": "/private/tmp/codex-u0-jrmef4or",
    "date": "2026-10-18T08:31:29+0530",
    "python": "3.15.0 (main, Oct  9 2026, 11:16:41) [Clang 21.0.0 (clang-2100.3.34.2)]",
    "probe_code_sha256": {
      "_command_fixture.py": "737c1df82b4865976170f65b52b57888093d148cd9601c1f77aef221d6646d0c",
      "_common.py": "814827a2acdf81d5a9ed0c46d8ece6c4b9bc97c7f22fe9b1e088ded7103d3715",
      "_coordinator.py": "c766fd5604497880e6726fc37c0baa7b0c7e3f7d6a0d77132241a2f76f1f04d3",
      "_discovery.py": "169dfde0bde10dd738a108cd953975ce75205bc493bbbdc7d9d016f2b51030d0",
      "_mcp_canary.py": "656f0118deeb456adf088ad2f1256a8aa0705efa6cc80fb19ba2495befee88a2",
      "_responses_fixture.py": "3d4c5c068fa9fc6dd8e27c2ba52608413908ca18cde29e679166e36d0176a450",
      "_scope_selftest.py": "145725dfa512bee2c04d12794dfdf007784d62e168b29ffae9e901502b24677a",
      "_verify.py": "7e8c5cc2d1f357b3812cdaa4d6e8997d12cda309a2eab227e3edd67a4fa97751",
      "probe_apply_patch_mediation.py": "361625c61ef9f8f4cc4766c24225c5e378ccb33fbeb5ae04e01502e81ba347af",
      "probe_file_change_events.py": "bae5cb5e0b4cbfe37b63b2e89b838c578af0f2ebb94be9637762a01c55268fa4",
      "probe_instruction_loading.py": "0880857dc1ea5a1b52eb96c99ec140c77968e201d10fa88b1290662323d565ea",
      "probe_mutator_hooks.py": "3f355e9895d5c17b132c4512d473daa16d6315fe74c1bf858359e8bfd6e31032"
    }
  },
  "calibration": {
    "verdict": "red",
    "configuration": "hooks disabled; authenticated direct shell write",
    "observed": "Verified shell mutation emitted no file_change item."
  },
  "cleanup": {
    "scratch_root": "/private/tmp/codex-u0-jrmef4or",
    "gone": true,
    "owned_processes_reaped": true,
    "fixture_pids_checked": [],
    "fixture_processes_remaining": [],
    "errors": [],
    "elapsed_seconds": 0.03294875
  },
  "error": null
}
```

- **calibration**: Authenticated shell write succeeded without a file_change event.
- **create**: Native filesystem result verified; exact items: [{"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/create/created.txt", "kind": "add"}], "status": "in_progress"}, {"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/create/created.txt", "kind": "add"}], "status": "completed"}]
- **update**: Native filesystem result verified; exact items: [{"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/update/updated.txt", "kind": "update"}], "status": "in_progress"}, {"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/update/updated.txt", "kind": "update"}], "status": "completed"}]
- **delete**: Native filesystem result verified; exact items: [{"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/delete/deleted.txt", "kind": "delete"}], "status": "in_progress"}, {"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/delete/deleted.txt", "kind": "delete"}], "status": "completed"}]
- **rename**: Native filesystem result verified; exact items: [{"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/rename/source.txt", "kind": "update"}], "status": "in_progress"}, {"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/rename/source.txt", "kind": "update"}], "status": "completed"}]
- **delete-plus-add**: Native filesystem result verified; exact items: [{"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/delete-plus-add/explicit-new.txt", "kind": "add"}, {"path": "/private/tmp/codex-u0-jrmef4or/delete-plus-add/explicit-old.txt", "kind": "delete"}], "status": "in_progress"}, {"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/delete-plus-add/explicit-new.txt", "kind": "add"}, {"path": "/private/tmp/codex-u0-jrmef4or/delete-plus-add/explicit-old.txt", "kind": "delete"}], "status": "completed"}]
- **partial-failure**: Native filesystem result verified; exact items: [{"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/partial-failure/blocker/child.txt", "kind": "add"}, {"path": "/private/tmp/codex-u0-jrmef4or/partial-failure/updated.txt", "kind": "update"}], "status": "in_progress"}, {"id": "item_0", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/partial-failure/blocker/child.txt", "kind": "add"}, {"path": "/private/tmp/codex-u0-jrmef4or/partial-failure/updated.txt", "kind": "update"}], "status": "failed"}]
- **authenticated-patch**: Real model produced direct create/rename patch; exact items: [{"id": "item_3", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/authenticated-patch/old.txt", "kind": "update"}, {"path": "/private/tmp/codex-u0-jrmef4or/authenticated-patch/real.txt", "kind": "add"}], "status": "in_progress"}, {"id": "item_3", "type": "file_change", "changes": [{"path": "/private/tmp/codex-u0-jrmef4or/authenticated-patch/old.txt", "kind": "update"}, {"path": "/private/tmp/codex-u0-jrmef4or/authenticated-patch/real.txt", "kind": "add"}], "status": "completed"}]
- **conclusion**: Paths absolute=True; kinds=['add', 'delete', 'update']; native rename omits destination=True. Events do not carry postimage bytes and cannot alone reconstruct all writes.

<details><summary>Exact invocation arrays (calibration first)</summary>

[00-calibration](evidence/probe_file_change_events/20261018T083129-87d0773d/00-calibration.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-jrmef4or/calibration",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "features.hooks=false",
  "-"
]
```

[01-create](evidence/probe_file_change_events/20261018T083129-87d0773d/01-create.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-jrmef4or/create",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51395/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[02-update](evidence/probe_file_change_events/20261018T083129-87d0773d/02-update.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-jrmef4or/update",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51401/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[03-delete](evidence/probe_file_change_events/20261018T083129-87d0773d/03-delete.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-jrmef4or/delete",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51411/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[04-rename](evidence/probe_file_change_events/20261018T083129-87d0773d/04-rename.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-jrmef4or/rename",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51417/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[05-delete-plus-add](evidence/probe_file_change_events/20261018T083129-87d0773d/05-delete-plus-add.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-jrmef4or/delete-plus-add",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51427/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[06-partial-failure](evidence/probe_file_change_events/20261018T083129-87d0773d/06-partial-failure.json) — local_fixture_transport; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-jrmef4or/partial-failure",
  "-c",
  "model_reasoning_effort=low",
  "-c",
  "model=\"gpt-5.6-sol\"",
  "-c",
  "model_provider=\"u0_fixture\"",
  "-c",
  "model_providers.u0_fixture={\"name\"=\"U0 deterministic transport\",\"base_url\"=\"http://127.0.0.1:51434/v1\",\"wire_api\"=\"responses\",\"requires_openai_auth\"=false,\"supports_websockets\"=false,\"request_max_retries\"=0,\"stream_max_retries\"=0}",
  "-c",
  "features.shell_snapshot=false",
  "-"
]
```

[07-authenticated-patch](evidence/probe_file_change_events/20261018T083129-87d0773d/07-authenticated-patch.json) — authenticated_model; exit 0.

```json
[
  "/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex",
  "exec",
  "--dangerously-bypass-approvals-and-sandbox",
  "--json",
  "--ephemeral",
  "--color",
  "never",
  "-C",
  "/private/tmp/codex-u0-jrmef4or/authenticated-patch",
  "-c",
  "model_reasoning_effort=low",
  "-"
]
```

</details>

<!-- U0 MEASURED END -->

## Interpretation of the measurements

### Patch mediation and failure recovery

The pre-hook receives `tool_name="apply_patch"` and the complete freeform patch
in `tool_input.command`, including all file sections. Explicit denial prevents
native application. `updatedInput.command` replaces the native input. The
coordinator fixture locks one repository, checks every previously recorded
preimage/type/expected absence, invokes the installed native patch engine, and
logs actual before/after states and an operation ID. Two operations sharing the
same base and lock cannot both commit. Duplicate operation IDs are remembered.

Codex 0.163.0 has no successful "already handled; skip native execution" hook
response. The measured workaround is to apply through the coordinator and then
deny. The model sees `Command blocked by PreToolUse hook:` with the coordinator
receipt reason. A real model run reported that outcome without retrying. This
single observed response is not a promise that every model will refrain from a
retry or alternative edit. Idempotency and reconciliation remain necessary.

Invalid hunks can fail before any mutation. A runtime failure creating a child
below a regular file leaves an earlier file updated. The coordinator records
`failed_or_partial`; its lock does not make the native multi-file patch engine
transactional. A failure after coordinator mutation can allow native validation
to run again. In the measured case native reapplication rejected the now-stale
original hunk, before emitting a `file_change` event. Therefore absence of that
event must not be described as proof that native execution was suppressed.

Hook crashes, exit `1`, exit `2` with empty stderr, malformed control output, and
timeout permit native execution in the measured matrix. Exit `2` with a nonempty
stderr reason blocks. Explicit supported denial is distinct from hook failure.
Direct shell writes bypass an apply-patch-only hook. Running `apply_patch` through
the shell produces a `Bash` callback in the measured invocation, not an
`apply_patch` callback. These are conclusive counterexamples to mandatory patch
mediation in full-access mode.

### Trust and coexistence

The computed normalized fingerprint matches the installed app-server's
`hooks/list.currentHash`. Coordinator-only trust and persisted scratch trust
execute the coordinator without `--dangerously-bypass-hook-trust`. A changed
definition with stale trust does not execute. Enabled, untrusted synthetic user
hooks remain inactive; disabled synthetic hooks remain inactive. Trusted user
TOML, adjacent `hooks.json`, project TOML, and invocation hooks can run together.
`--ignore-user-config` still discovers adjacent JSON hooks. Blanket trust bypass
also runs the enabled untrusted canaries, demonstrating why it is unsuitable as
a coordinator-only switch. No synthetic plugin was configured; plugin-source
loading is explicitly unmeasured.

The brief's quoted dotted-key `-c` example was rejected by the installed CLI as
an unrecognized setting. CLI override keys are split on dots without interpreting
those quotes as TOML key syntax. The tested correction supplies a single inline
state table, whose keys are parsed as TOML values:

```text
-c 'hooks.state={"/<session-flags>/config.toml:pre_tool_use:0:0"={trusted_hash="sha256:<computed digest>"}}'
```

This is a syntax template; real, expanded values are above and in case evidence.
Persisted TOML is generated under scratch `CODEX_HOME` only. Trust fingerprints
cover the normalized hook definition, not the referenced script's bytes. A
future product must separately protect and version its coordinator executable
within its cooperative trust model.

### Mutator windows

The shell hook name is `Bash`. Its `tool_input` carries `command` but omits the
separate `exec_command.workdir`. A session's cwd is therefore insufficient to
derive a command's effects. A normal command, including a process exiting `7`,
has measured ordering `pre ≤ start ≤ end ≤ post`. A yielding command receives
its delayed post after completion through `write_stdin`; interactive mutating
stdin does not get another pre callback. A handler error and an interrupted
command lack post callbacks in the measurements.

The pre/post fixture uses a separate persistent coordinator process. Its flock
survives the short-lived pre-hook, stays held during the command, and is released
by the matching post. Missing post leaves it held until explicit fixture
teardown. A hook process that simply opens a lock and exits cannot provide this
custody. The background-child case creates a detached child and demonstrates a
mutation after the parent command's post callback. A non-detached child in an
earlier development attempt was terminated by command cleanup and did not prove
that bypass; that attempt remains visibly inconclusive in evidence.

The local MCP canary implements the newline-delimited 2025-11-25 protocol and
accepts a generated fixture ID, never an arbitrary destination. Its canonical
hook name is `mcp__u0_canary__write_marker`. Successful, denied, delayed, and
write-then-`isError:true` cases are separately recorded. Successful and delayed
calls have ordered pre/post callbacks; denial prevents server entry. The
write-then-error call mutates the marker but has no post callback, leaving its
coordinator window held until explicit teardown. MCP interception exists;
it is incorrect to infer that all MCP tools bypass native hooks. In this
installed CLI's code-mode host, deterministic MCP calls must go through the
`exec` tool bridge (`tools.mcp__u0_canary__write_marker(...)`); sending that name
as a direct Responses function call was rejected as unsupported. Real-model
MCP execution and the corrected deterministic dispatch both use the same inner
hook path.

An MCP callback only bounds the synchronous request it observes. A remote job,
server background task, delayed infrastructure operation, or filesystem effect
after tool return requires its own lifetime and reconciliation evidence. These
remote residuals were not simulated against real infrastructure.

### Instructions, discovery, and events

The exact synthetic context confirms that default project instructions follow
the execution directory's ancestry from the nearest Git root. An ancestor above
that root and a nested independent checkout below execution cwd are absent.
Launching inside the nested checkout loads that checkout's instructions instead.
Without a repository marker, only the execution directory is considered.
`AGENTS.override.md` beats `AGENTS.md`; `AGENTS.md` beats configured fallbacks.
`CLAUDE.md` is absent by default and loads as an explicit fallback only where
higher-priority instruction candidates are absent. The authenticated inherited
configuration run is reported separately: token appearance proves presence,
but nonappearance in an answer alone is weak evidence of absence.

Metadata discovery is a separate operation from instruction loading. The
benchmark plants a root checkout, ignored independent and deeper nested
checkouts, a `.git` file, a dependency-tree checkout, a case-sensitive/insensitive
identity case, symlink aliases, a cycle, and an external checkout reachable
through a workspace symlink. Each size requires every planted checkout to be
found. The 1,000, 10,000, and 100,000 file workloads are measurement sizes, not
caps. The generated measurements report construction, entries/directories,
Git subprocesses, repositories, aliases/cycles, errors, first traversal,
subsequent min/median/max, and cleanup. Caches were not controlled; the first
traversal is not called cold. The benchmark is one synthetic volume/workload,
not a universal performance bound.

Native `file_change` items use absolute paths and `add`, `update`, `delete` kinds.
They report `in_progress`, `completed`, and, for partial failure, `failed`.
A native `*** Move to:` rename is an `update` of the source path and omits its
destination. Explicit delete-plus-add exposes both paths. Shell mutation emits
no `file_change` item. These events contain no authoritative postimage bytes and
cannot independently reconstruct edits or prove authorship. Event paths on a
failed operation must be reconciled against actual disk state.

## Recommended edit-attribution mechanism

Adopt the hybrid for **cooperating writers**, subject to the measured residuals.
For supported explicit patches, collect the complete intended operation, resolve
actual repository ownership, acquire all affected claims, check recorded
preconditions, apply under coordinator custody, and persist an operation receipt.
Record expected absence as well as preimage hashes, modes, and file types. Do
not sample a new hash at callback time and call that the operation's base.

For shell and MCP mutations, a persistent coordinator owns per-repository
windows. The command must declare its complete possible repository set before
starting. When its effects cannot be narrowed, use the run's entire target set.
Also acquire appropriate infrastructure claims. A multi-target command acquires
the complete set as one admission attempt; it does not hold some claims while
waiting for others. Interactive sessions retain custody through their final
completion. Descendants and remote jobs must be tracked separately. If the
coordinator cannot establish that mutation has stopped, move the window to an
unknown/recovery state instead of releasing it because a timeout elapsed.

For supported patches touching disjoint claims, cooperating writers may proceed
concurrently. A broad mutator window conflicts with all relevant path claims in
that repository. Reconcile unmediated edits and ambiguous infrastructure changes;
never assign a writer because its event arrived just before a postimage hash was
sampled.

### Required Codex configuration

Both abraham phases use the normal user's configuration and MCP servers:

```text
NATIVE exec --dangerously-bypass-approvals-and-sandbox --json --ephemeral \
  --color never -C EXECUTION_CWD -c model_reasoning_effort=low \
  -c features.hooks=true [coordinator hook and narrowly scoped trust overrides] -
```

`low` is the probe's spending policy, not a proposed product restriction on
reasoning effort. The user-configured model remains unchanged in authenticated
measurements. All model invocations are full access; no sealed variant exists.
No ordinary invocation adds `--ignore-user-config` or blanket hook-trust bypass.

The tested hook definitions are synchronous command hooks. Use
`PreToolUse` matcher `^apply_patch$` for explicit patch mediation, and
`^(Bash|mcp__.*)$` pre/post groups for shell and MCP windows. The probe narrows
the MCP matcher to its canary. A command entry is:

```json
{"type":"command","command":"<shell-quoted Python/coordinator argv>","timeout":2,"async":false}
```

The 1-second timeout case is intentional fault injection. A production timeout
must match the actual coordinator protocol and workload; increasing it does not
convert failure into denial. The measured command and state syntax can be
generated without touching a real configuration file:

```python
groups = {
    "PreToolUse": [patch_group, mutator_group],
    "PostToolUse": [mutator_group],
}
config = trusted_config(groups)  # _common.py: definition hash and inline state map
argv += override_args(config)    # alternating '-c', 'key=<TOML value>' arguments
```

For each handler, the trust key is
`<source_path>:<event_label>:<group_index>:<handler_index>`. Session flags use
`/<session-flags>/config.toml`. Event labels are `pre_tool_use` and
`post_tool_use`. Hash the normalized group
`{"event_name":label,"matcher":matcher,"hooks":[handler]}` with recursively
sorted keys, compact UTF-8 JSON, SHA-256, and prefix `sha256:`. Absent optional
fields stay absent. This implementation uses one handler per group. Confirm
definitions with `hooks/list` before adoption and refresh trust if their
definition changes. Preserve unrelated user sources and state.

Supported synchronous denial and rewrite payloads are respectively:

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"<receipt or refusal>"}}
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","updatedInput":{"command":"<complete replacement patch>"}}}
```

The coordinator's apply-and-deny reason must say what actually happened,
including partial application. It must never claim successful native tool
execution. Native patch-engine invocation is
`[NATIVE, "--codex-run-as-apply-patch", complete_patch]`, with the intended
fixture/checkout cwd; it introduces no sandbox.

### Residuals and evidence strength

- Hook crash, timeout, nonblocking exit status, and malformed output can fail
  open. There is no measured mandatory failure barrier around these hooks.
- Shell writes and shell-invoked patches bypass an apply-patch-only hook.
  Other full-access processes can ignore or alter cooperative controls.
- Interactive input and descendants can extend mutation beyond apparent tool
  duration. Missing post is a recovery condition, not permission to unlock.
- User hooks run concurrently according to the supplied pinned-source analysis.
  Their denials or rewrites can conflict with coordinator actions. The canary
  coexistence test proves execution, not a deterministic ordering between hooks.
- MCP asynchronous effects and infrastructure jobs may outlive tool results.
- File-change events lack postimages and native rename destinations. They are
  hints for reconciliation, not attribution receipts.
- Hash-check-then-write is not an OS-atomic CAS against an unrelated writer.
  Advisory locks establish ordering only among participating writers.
- A coordinator crash between disk mutation and durable receipt needs recovery;
  the U0 fixture is not a crash-consistent production journal. Partial patches,
  duplicate operations, and identity changes must be reconciled independently.
- Unmediated filesystem and infrastructure changes remain **inferred**,
  **conflicted**, or **unknown**. Full-access operation does not justify stronger
  authorship claims.

## Multi-repository target-model specification

This is the normative target for later 1.18 units, not functionality added by U0.

### Records and identity

| Record | Required fields |
|---|---|
| Run | Run ID; dispatch workspace identity; execution cwd; fixed full-access capability; selection mode; target registry and discovery completeness/generation; unit IDs; Codex thread/resume IDs; lifecycle status; evidence references |
| Repository | Repository ID; target host identity; canonical checkout path; checkout filesystem identity; resolved Git-directory path and filesystem identity; common-directory metadata; `.git` directory/file form; aliases; identity generation |
| Repository baseline | HEAD or unborn state; index identity/digest; pre-existing dirty state; relevant path preimages; expected absences; file types/modes; baseline generation |
| Claim | Run/unit/operation owner; repository ID; repository-relative path; read/write/directory/mutator-window type; expected preimage; acquisition and lifecycle state |
| Infrastructure resource | Target-host-qualified key; provider/service identity; resource scope; available version/precondition; operation ID; synchronous/asynchronous lifetime; reconciliation evidence |
| Spanning unit | Per-repository candidate vector; inter-component dependencies; validations; application state; publication state per component; explicit partial-publication status |
| Change receipt | Operation ID; repository/resource identity and generation; intended operation; observed outcome and postimage where available; evidence strength; timestamps; reconciliation status |

A workspace identity records its host, canonical path, filesystem identity,
aliases, and generation even if it is not itself a repository. A repository ID
binds **host + canonical checkout + checkout identity + resolved git-dir
identity + incarnation**. On the local filesystem, device/inode pairs are useful
components, with generation/revalidation to detect replacement; they are not
permanent globally unique identifiers. Store exact path spellings and canonical
paths separately. A common Git directory, remote URL, or HEAD is insufficient:
two existing checkouts sharing common storage are distinct targets. Existing
`.git` files are supported even though this design never creates worktrees.

Re-stat and validate at admission and resume. A deleted/recreated checkout,
replaced Git directory, changed symlink target, or unresolved identity is a new
incarnation or an unknown state requiring reconciliation; do not silently reuse
the previous claims or baseline.

### Workspace, target selection, and authorization

Execution cwd defaults to the dispatch workspace, matching the workspace from
which Claude dispatches the run. Default targets are **every actual checkout
discovered in that workspace**: the workspace's own repository, independent
nested repositories, and ignored nested repositories. All are authorized by
default. Discovery also follows workspace directory symlinks, including links
to checkouts outside the lexical workspace, with the identity rules below.

Explicit selection has two modes: `add` unions the explicit repositories with
discovery; `replace` supplies the exact narrower set. An explicit target can be
any original checkout on the accessible host, including outside the dispatch
workspace. Changing execution cwd alone never changes target selection.

Targets define coordination, authorization records, discovery expectations, and
reporting scope. They do **not** create a folder fence. Full access remains full
access. A mutation outside the declared target set is visible as an out-of-set
change requiring classification and reconciliation; it is never silently
credited to the closest declared target. Writers always use existing original
checkouts, never copies or newly created worktrees.

### Deepest-boundary ownership and claims

Resolve each path against actual repository boundaries, including ignored and
undeclared repositories. The **deepest actual repository** owns the path.
For example, `A/vendor/B/src/x.py` belongs to B when B is a checkout, even if B
is ignored by A or omitted from the target registry. It never becomes A's edit
because A is the only declared target. An invalid, replaced, or unreadable
nested `.git` boundary makes ownership unknown rather than falling back to A.

Re-resolve boundaries and filesystem identity at mutation time, since a new
nested repository may appear after discovery. A `.git` boundary change itself
requires registry/identity reconciliation. Repository metadata mutations are
reported explicitly; they are not ordinary source-file claims that can silently
replace a repository's identity.

Claims are keyed by `(repository_id, relative_path)`, within an identity and
baseline generation. Relative paths retain filesystem semantics; reject lexical
escapes and resolve existing parent directories for new names, then recheck
immediately before application. Distinguish replacing a symlink itself from
following the symlink and mutating its referent. The latter belongs to the
referent's actual repository or to an explicitly unknown/out-of-set resource.

Do not blindly lowercase paths. Deduplicate existing case aliases through
filesystem identity. For not-yet-existing names whose case equivalence cannot
be established, conservatively conflict possible aliases. Directory claims
conflict with descendants, and broad mutator windows conflict with path claims
in their repository. Hardlink aliases can connect different paths or
repositories: detect known shared file identity, acquire the necessary claims,
and mark unresolved aliasing as ambiguous rather than asserting independence.

### Preconditions, cross-repository moves, and infrastructure

Maintain a separate baseline and claim ledger for each repository. Preserve
existing dirt as pre-existing; do not credit it to the new writer. HEAD may be
unborn. Check relevant content hashes, expected absence, file type/mode, index
and HEAD assumptions, and identity generation. Ignored files count when touched;
Git's tracked-file inventory is not the entire mutation domain.

A cross-repository move is a **delete in the source and create in the
destination**, linked by one operation ID. Acquire both claims before either
operation; check the source preimage and destination absence/preimage. Track a
partial outcome if one component succeeds. Do not depend on native
`file_change` rename encoding, even for within-repository moves.

An infrastructure resource key must identify the target system, for example:

```text
(
  target_host_identity,
  service_or_provider,
  endpoint_or_account_or_cluster,
  namespace_or_database,
  resource_identifier
)
```

The host is the remote target's identity, not merely the dispatch machine.
`localhost`, a database name, cloud resource label, or filesystem path alone is
insufficient. Resolve aliases and record unresolved aliases explicitly. Store
available version/ETag/transaction preconditions, generated job IDs, pending
asynchronous effects, and observed reconciliation state. An acknowledged API
request is not evidence that every eventual resource mutation completed.

### Reconciliation and change reports

Reconcile on success, failure, interruption, timeout, partial application, and
resume. Compare operation receipts, each repository baseline, current content,
HEAD and index state, identity generations, and infrastructure observations.
Keep intended operations separate from observed state. Later content that
differs from a recorded postimage is not silently attributed to the receipt's
writer.

Use four evidence strengths: **mediated/cooperative** for a checked operation
under coordinator custody; **inferred** for compatible but nonexclusive evidence;
**conflicted** for incompatible claims/preimages/postimages; **unknown** when
ownership, lifetime, or evidence is incomplete. Even a mediated receipt proves
only the custody and observations its mechanism supports, never exclusion of
arbitrary full-access processes.

A change report groups changes by repository ID and incarnation, then relative
path; includes explicit external/unknown ownership entries; separates
pre-existing dirt, intended operations, actual observations, and attribution;
links cross-repository components and infrastructure jobs; and lists pending
claims, partial operations, identity changes, and reconciliation requirements.
No event-timing heuristic upgrades inferred changes to mediated authorship.

### Spanning units and partial publication

Model a spanning unit as a vector rather than one synthetic repository tree:

```text
repository_id -> {
  base,
  preconditions,
  candidate_operations,
  validations,
  application_state,
  publication_state
}
```

Dependencies and validations may span components, but each component retains its
own baseline, operation IDs, results, and publication state. Track pending,
applied, partially applied, validated, accepted, conflicted, and unknown states
as appropriate. If A is accepted and B fails, record partial publication; never
label the whole unit published. Here **publication means coordinator acceptance
of a component**, not an automatic Git commit. Physical writes in original
checkouts are already visible. There is no globally atomic multi-repository
transaction or automatic rollback guarantee.

Retry only unresolved components after refreshing preconditions. Use idempotent
operation IDs to avoid replaying accepted changes. Recovery must distinguish an
applied operation with a lost receipt from an unapplied one before retrying.

### Listing and resuming

Index each run from the dispatch workspace and from **every target repository**,
including explicit targets outside that workspace. Use identity-based
membership, not cwd-prefix matching. Record aliases so callers arriving through
another spelling find the same identity. Keep historical membership when an
identity changes, with an explicit stale/reconciliation state.

Resume preserves the original execution cwd, target selection, fixed full-access
mode, thread IDs, and unresolved candidate vector. Revalidate workspace,
repository, Git-directory, and infrastructure identities; verify outstanding
claims and mutator custody before admitting new work. A moved/recreated checkout
cannot silently inherit another incarnation's run or claims. Listing from an
enclosing directory is not a substitute for membership in all targets.

### Metadata-only discovery algorithm and cost

1. Canonicalize the dispatch workspace and initialize a pending directory list,
   a physical-directory visited set, an alias/cycle ledger, a repository registry,
   and an explicit completeness state.
2. Traverse using `os.scandir`. Follow directory symlinks; deduplicate traversal
   using filesystem identity, recording alternate spellings and cycles. This
   includes repositories reachable outside the lexical workspace through links.
3. At each directory, detect a `.git` directory or file and validate it with Git.
   Use `rev-parse --show-toplevel --absolute-git-dir --path-format=absolute
   --git-common-dir --is-bare-repository`, and `--resolve-git-dir` where needed.
   Require the validated checkout root to match the boundary being registered.
   Record metadata identities and distinguish bare metadata from checkouts.
4. Skip actual Git metadata internals, including separately located git-dirs;
   recognize a separate metadata directory before descending even if encountered
   before its checkout. Continue walking below checkouts to find deeper ones.
5. Ignore neither dependency directory names nor Git ignore rules. Do not read
   workspace documents, `.gitignore`, or `git ls-files` to discover targets.
   There is no depth, repository-count, entry-count, `node_modules`, or `vendor`
   cap. A large tree has a real cost, reported rather than concealed by omission.
6. Scrub Git redirection/config injection environment variables; disable
   fsmonitor and hooks, optional locks, lazy fetch, and replacement objects for
   metadata queries. The prototype additionally ignores global/system Git
   configuration. Re-stat identities at admission/mutation, because cached
   `DirEntry` metadata is only a traversal optimization.
7. Unreadable entries, dangling/unresolvable aliases, cancellation, replacement
   during traversal, and failed metadata validation mark discovery incomplete.
   Do not present such an inventory as "every repository." Refresh or require
   explicit resolution before claiming complete default target selection.

The executable prototype is [_discovery.py](_discovery.py). Its benchmark performs
four traversals at each of three workload sizes, reports the first separately,
and asserts exact equality with the planted checkout set. The generated evidence
contains measured costs and filesystem case behavior. Real admission must add
race/replacement validation and registry persistence; this benchmark is not
itself a product-ready concurrent inventory service.

### Legacy 1.17 tree-lock interoperation

Compute a conservative overlap closure containing the 1.18 target set, all
enclosing repositories, and relevant nested repositories, including undeclared
boundaries. An enclosing 1.17 writer may affect a nested checkout while holding
only the enclosing tree lock; a nested legacy writer holds its own tree lock.
Both forms must be excluded by the closure. If the closure is incomplete, do
not admit work with reduced legacy coverage.

One 1.18 coordinator owns the legacy exclusion locks for an active overlap
component and multiplexes cooperating 1.18 writers internally. Giving every new
writer a separate exclusive legacy tree lock would serialize the writers and
remove the intended concurrency.

Use the existing 1.17 canonical tree-lock identities and bridge protocol, not a
new path spelling that old servers would miss. Sort the complete canonical lock
identity set deterministically, using bytewise UTF-8 ordering of
`(host_identity, legacy_lock_identity)` rather than locale-sensitive path order.
Attempt **every lock nonblocking**. On any
failure, release the entire tentative set before waiting/backoff/retry. Never
wait while holding a partial required set. Preserve the old-server barrier,
legacy-holder checks, kernel lock custody, inherited-descriptor semantics, and
legacy bridge publication/release behavior. Never unlink an active flock file.

If discovery or a new explicit target expands the required overlap closure,
quiesce affected work and reacquire the full set, or refuse admission. The
coordinator cannot add a target by dropping coverage of existing ones. Recovery
after coordinator failure must reconcile inherited custody and active writers
before granting another owner. These are requirements for future implementation;
U0 does not change existing lock code.

## Verification

The verification is the probes' own runs: each runs its negative calibration
first, must see it come back red, and only then measures; its exit code, verdict
and case records are in the generated section above, and its streams and
receipts in `evidence/` (regenerated, never committed). The last recorded runs,
2026-10-18 from 08:30 IST (two authenticated runs first came back inconclusive,
exit 2, because the provider answered "Selected model is at capacity"; both were
rerun at 08:38 and are conclusive):

| Entry point | Exit | Actual output summary |
|---|---:|---|
| Each of the four `--real --update-report` probe runs | 0 | Calibration red, conclusive red; respectively 35, 16, 14, and 9 measured case records; scratch removed |

U0 changes no behavior outside this directory. The verifier that also ran the
project's test suites and compared the checkout with a baseline was removed with
the test harness (user ruling 2026-10-18: "test it out with results rather than
tests"); the run records above still list it among the U0 files present when
they ran.

### The implementation run

The run that wrote U0 recorded its own starting state,
[implementation-baseline.json](evidence/implementation-baseline.json). The
checkout already held seven modified tracked files and an untracked
`.abraham/tmp/node-compile-cache` tree, which U0 had to preserve. The literal
acceptance condition "status contains only new U0 files" therefore could not hold
for the whole checkout; the applicable check was that every new status entry lay
under U0 and all pre-existing tracked bytes and the worktree listing matched that
baseline. A one-off [final audit](evidence/verification/final-audit.json),
written by that run and not by a committed script, checked final-line JSON, code
hashes recorded at probe startup, durable evidence paths, all 18 historical probe
scratch roots, recorded fixture-process cleanup, report links, and scope: all 39
pre-existing tracked file hashes matched, the worktree listing was unchanged, no
new status entry lay outside U0, and no `__pycache__` directory was created under
U0. Its [final probe invocations](evidence/verification/probes.json) retain each
process's complete stdout/stderr and real exit code. Its first push-gate attempt
exited `1` because Python 3.15 had no pytest;
[that output is retained](evidence/verification/test_push_gate.py.previous-failure.json).

## Deviations from the supplied plan

- The trust override uses an inline `hooks.state` table because the installed
  CLI ignored the quoted dotted-key version. Actual `hooks/list` hashes and
  trust statuses validate the correction; no real trust state is modified.
- Deterministic transport is also used for exact dispatch of fault-injection,
  shell-lifetime, and file-event cases. Authenticated calibration and representative
  authenticated patch, shell, MCP, instruction, and rename runs remain required.
  This keeps spend low and avoids interpreting a model choosing a different tool
  as a native capability result. Every measurement is labeled by transport.
- The installed code-mode host requires the `exec` bridge for deterministic MCP
  dispatch. A direct canonical MCP Responses function name was rejected; the
  corrected bridge is verified by actual MCP mutation and hook receipts.
- The lingering-child fixture explicitly detaches its child. A nondetached
  child's cleanup prevented the intended late mutation in a development run.
- Additional small helpers implement timestamped command fixtures and uncapped
  discovery. They remain entirely under U0 and do not alter product behavior.
  (A suite runner and its scope self-test also lived here until the test
  harness was removed on 2026-10-18.)

These deviations respond to measured installed behavior or keep required
verification within the caller's boundaries. No sandbox guarantee, production
feature change, remote infrastructure experiment, or external source lookup was
substituted for a missing observation.
