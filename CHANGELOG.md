# Changelog

All notable changes to the `software-workflows` plugin are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

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

[1.1.0]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.1.0
[1.0.0]: https://github.com/ahrar-azmat/agent-teams-plugin/releases/tag/v1.0.0
