# Changelog

All notable changes to the `software-workflows` plugin are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

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

[1.2.1]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.2.1
[1.2.0]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.2.0
[1.1.0]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.1.0
[1.0.0]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.0.0
