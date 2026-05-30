# agent-teams-plugin

A Claude Code plugin marketplace providing **`software-workflows`** — a skill that orchestrates **multi-agent work** using Claude Code [Agent Teams](https://code.claude.com/docs/en/agent-teams) (shared task lists, inter-agent messaging, task dependencies), with **correct, schema-validated tool signatures** so runs don't fail on malformed tool calls.

> Marketplace name: `agent-teams` · Plugin name: `software-workflows`

## What it does

When you ask Claude Code to do anything involving 2+ agents, the `agent-teams` skill kicks in and drives the full lifecycle:

1. Loads the deferred team tools (`TeamCreate`, `TaskCreate`, `TaskUpdate`, `SendMessage`, `TaskGet`, `TaskList`, `TeamDelete`)
2. Creates a team + a shared task list
3. Spawns focused teammates (each its own context window) with self-contained prompts
4. Wires up task dependencies, assigns/claims work, coordinates via direct messages
5. Shuts teammates down and cleans up when the work is done

It ships **role templates** (researcher, architect, implementer, reviewer, tester, …) and **team patterns** (research, implementation, debug, review), plus an optional **multi-model review** layer that uses Codex and Gemini as independent advisors when those MCP servers are present.

## Why this exists (the v1.1.0 fix)

Agent Teams tools are **deferred** and **schema-validated** (`additionalProperties: false`). The most common way orchestration runs fail is passing parameters the tools don't accept. This skill pins the exact signatures so the model can't improvise:

- `team_name` belongs **only** on `TeamCreate` and `Agent` — never on the Task tools (they auto-associate with the session's team).
- `TaskCreate` has **no** `blockedBy` — create first, then `TaskUpdate(taskId, addBlockedBy=[…])`.
- `SendMessage` is `to` + `message` (+ optional `summary`) — **not** `recipient`/`type`/`content`, and there is **no broadcast**. Protocol `type` goes *inside* the `message` object.
- Deferred tools load **per context** — every teammate prompt loads them first.

The skill includes a Tool-Signatures reference table and troubleshooting entries mapping each error to its fix.

## Install

```text
/plugin marketplace add ahrar-azmat/agent-teams-plugin
/plugin install software-workflows@agent-teams
```

Then just ask for multi-agent work (e.g. *"Create an agent team to review this PR from security, performance, and test-coverage angles"*).

## Requirements

- **Claude Code v2.1.32+** (Agent Teams support).
- **Agent Teams enabled** — they're experimental and off by default. Enable in `settings.json`:
  ```json
  { "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
  ```
- **Optional — multi-model review:** the Codex Oracle and Gemini MCP servers. If they aren't configured, the skill still runs normally; the cross-model review steps are simply skipped.

## License

[PolyForm Noncommercial 1.0.0](./LICENSE) © Ahrar Ahmad

**Noncommercial use only.** You may use, modify, and share this plugin for any
noncommercial purpose (personal, research, education, nonprofits). Commercial
use is not permitted under this license — contact the author for commercial terms.
