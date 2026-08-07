# Changelog

All notable changes to the plugins in this marketplace are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

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
