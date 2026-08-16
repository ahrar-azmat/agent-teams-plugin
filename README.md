# agent-teams-plugin

A Claude Code plugin marketplace providing **`software-workflows`** — a skill that orchestrates **multi-agent work** using Claude Code [Agent Teams](https://code.claude.com/docs/en/agent-teams) (shared task lists, inter-agent messaging, task dependencies), with **correct, schema-validated tool signatures** so runs don't fail on malformed tool calls.

> Marketplace name: `agent-teams` · Plugin name: `software-workflows`

## What it does

When you ask Claude Code to do anything involving 2+ agents, the `agent-teams` skill kicks in and drives the full lifecycle:

1. Loads the deferred team tools (`TaskCreate`, `TaskUpdate`, `SendMessage`, `TaskGet`, `TaskList`)
2. Spawns focused teammates (each its own context window) with self-contained prompts — the team is **implicit**, formed by the first `Agent` call
3. Wires up task dependencies, assigns/claims work, coordinates via direct messages
4. Shuts teammates down when the work is done (the implicit team cleans up on session exit)

It ships **role templates** (researcher, architect, implementer, reviewer, tester, …) and **team patterns** (research, implementation, debug, review), plus an optional **cross-model advisory** layer that uses Codex Oracle as an independent senior advisor when its MCP server is present.

The codex-oracle plugin also ships **`/abraham`** (v1.13.0): a write-capable mode that runs as two air-gapped codex phases — read-only deep analysis (codebase + live infra + live web) producing an implementation brief, then a sealed implementer (workspace-write file access, no network/web/MCP) that edits the working tree under git preconditions, a one-writer lock, and changed-files attribution for the orchestrator to review.

## Why this exists (the v1.1.0 fix)

Agent Teams tools are **deferred** and **schema-validated** (`additionalProperties: false`). The most common way orchestration runs fail is passing parameters the tools don't accept. This skill pins the exact signatures so the model can't improvise:

- `TeamCreate` / `TeamDelete` **no longer exist** (removed in Claude Code v2.1.178) and `team_name` is deprecated and ignored — the team is implicit and the Task tools auto-associate with the session.
- `TaskCreate` has **no** `blockedBy` — create first, then `TaskUpdate(taskId, addBlockedBy=[…])`.
- `SendMessage` is `to` + `message` (+ optional `summary`) — **not** `recipient`/`type`/`content`, and there is **no broadcast**. Protocol `type` goes *inside* the `message` object.
- Deferred tools load **per context** — every teammate prompt loads them first.

The skill includes a Tool-Signatures reference table and troubleshooting entries mapping each error to its fix.

## The Independence Protocol (v1.4.0)

A second opinion is only worth something if it was formed **independently** — and the way that
guarantee gets destroyed is subtle. You write up your own diagnosis, paste it into the prompt,
and ask Codex to "review" it. What comes back is a reaction to *your framing*, not an
independent read of the evidence — your own opinion wearing the advisor's voice.

The MCP enforces independence server-side rather than trusting the caller to ask for it:

- **`caller_hypothesis`** on every advisory tool — the one correct channel for your own view.
  It is presented as an *unverified claim to refute* and answered with an explicit
  **CONFIRMED / REFUTED / UNPROVEN** verdict naming the evidence that decided it.
- **An independence contract** injected into every prompt: reason from primary evidence first,
  treat caller claims as unverified, investigate what the caller *didn't* ask about, lead with
  disagreement.
- **An anchoring lint** on the neutral scoping fields (`context`, `concerns`, `focus`, `topic`).
  Conclusion language ("the root cause is", "I fixed", "does this look right") triggers a
  counter-anchoring injection *and* a loud `⚠️ ANCHORING WARNING` on the result telling you your
  agreement is now weak evidence. It never silently strips your text and never blocks the call.
- **A mandatory "where I disagree with the caller's framing"** section in every review's output.

Round 1 is blind; round 2 can be adversarial. The rule to remember:

> The advisor agreeing with you is strong evidence **only if it was dispatched blind.**
> Disagreement is strong evidence either way.

## Live web research (v1.4.0)

The advisor actually researches instead of recalling. Codex runs with `web_search=live` —
its default is `cached`, an OpenAI-maintained snapshot index, so the previous instruction to
"search the web and cite URLs" had no mechanism behind it. It requires primary sources with
URLs, marks unverifiable load-bearing claims `UNVERIFIED`, and ends with a Sources section;
code reviews additionally check APIs against current upstream docs and touched dependencies
for known CVEs.

## The channel layer (v1.14.0)

Advisor web search reaches ordinary pages; it can't read YouTube subtitles, RSS feeds, or
semantic-search indexes. When the [agent-reach](https://github.com/Panniantong/agent-reach)
CLI is installed, the orchestration skill uses it as a **caller-side fetch layer**: the
orchestrator or a teammate fetches, curates, and passes content to the advisor as cited
context data. The advisor is never given a networked sandbox to fetch for itself —
untrusted web content, full-disk read, and network egress must never share a process
(measured on codex 0.147.0: no mechanism exists for network without full disk read).
Entirely optional — without the CLI, nothing changes.

## Upstream codex source as reference (map vs territory)

The `openai/codex` CLI is Apache-2.0 open source, and its internals are the reference for
everything these plugins wrap. Two rules keep that reference honest:

1. **Read the ref that matches the installed binary, never main-HEAD.** Releases are cut on
   branches, so main and the release tags are divergent — either direction of drift has
   produced wrong conclusions here. `python3 scripts/codex_src.py` keeps a stable worktree
   (`~/Documents/codex-installed`, override via `CODEX_SRC_WORKTREE`) checked out at
   `rust-v<installed version>`, re-aligning itself after every codex update. It never touches
   the base clone (`~/Documents/codex`, override via `CODEX_SRC_CLONE`) and refuses to run
   over local modifications.
2. **Source is the map; the installed binary is the territory.** Source names the config
   keys, events, and mechanisms worth probing — but anything load-bearing is confirmed
   against the running binary (`--strict-config` with a known-bad key first, live probes,
   the binary's own `models_cache.json`), never asserted from source alone.

## Install

```text
/plugin marketplace add ahrar-azmat/agent-teams-plugin
/plugin install software-workflows@agent-teams
```

Then just ask for multi-agent work (e.g. *"Create an agent team to review this PR from security, performance, and test-coverage angles"*).

## Requirements

- **Claude Code v2.1.178+** — the skill drives the implicit-team API introduced when `TeamCreate`/`TeamDelete` were removed in v2.1.178. (v2.1.193+ additionally auto-migrates installs that still have the removed `antigravity` plugin enabled, via the marketplace `renames` mapping.)
- **Agent Teams enabled** — they're experimental and off by default. Enable in `settings.json`:
  ```json
  { "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
  ```
- **Optional — cross-model advisory:** the `codex-oracle` MCP server (ships in this marketplace). If it isn't configured, the skill still runs normally; the cross-model steps are simply skipped.
  - The server is Python and launches via `python` (≥3.11) on PATH — on python3-only systems (stock macOS), point `python` at python3.
  - `codex-oracle` needs the Codex CLI (`npm i -g @openai/codex`) authenticated (`codex`); live web search is forced on per call, so no config change is required.
  - **Windows (v1.9.0+):** fully supported — no WSL needed. Optional: enable Developer Mode if you want the `latest.log` convenience symlink; without it the merged `stream.log` is the live view.

## License

[PolyForm Noncommercial 1.0.0](./LICENSE) © Ahrar Ahmad

**Noncommercial use only.** You may use, modify, and share this plugin for any
noncommercial purpose (personal, research, education, nonprofits). Commercial
use is not permitted under this license — contact the author for commercial terms.
