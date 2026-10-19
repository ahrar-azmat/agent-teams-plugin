# Agent Teams — Codex writers (abraham) and the writer coordinator

Read this before chartering an abraham run or running several writers in one checkout.

### Codex writers (abraham)

`abraham` is the one tool that edits files. It runs two Codex phases in one call, both with **full access**: no sandbox, network, live web search, your MCP servers and live infrastructure. The analysis phase, read-only by instruction, writes an implementation brief; the implementation phase carries it out, runs the project's real checks and reports their real results. There is no sealed mode: `false` for `full_access`, `infra` or `web_search` is refused.

1. **Charter the unit.** State the property and its acceptance: what must be true afterwards, and which real runs show it. Mark mechanism hints as beatable. Put what you already know in `context`, including the access paths (a key-based SSH host, a `docker exec` target, a local test database) and never credentials. Put hard boundaries in `constraints`: files not to touch, APIs to keep. Tell it to write no test cases unless the user asked for them (SKILL.md rule 10).
2. **Dispatch** with `cwd` set to the target repository's own checkout: any git repository on the machine. The tree must be clean, so commit the work first (never `git stash`), or pass `allow_dirty=true` knowingly. The call runs long and is backgrounded. Watch it with `codex_runs` and `codex_run_log`; stop it with `codex_cancel_run`; continue a dead or cancelled run with `codex_resume_run`, after checking `git status`, because a partial run may have written files.
3. **Read the result.**
   - The `[CHANGED FILES]` block separates this run's changes from what was dirty before. A `HEAD MOVED` line means audit `git log` first.
   - The **effective mode** line is what Codex recorded for each turn; anything but full access is flagged.
   - A `⚠ the tree changed during the ANALYSIS phase` note means files changed while it was only meant to read: review them in the diff.
4. **Verify it yourself.** Read the diff. Re-run the build, the linters and the changed app, command or endpoint for real, and record what you saw. Then run one blind `code_review` of the final change, continuing the review session for follow-ups, and fix CRITICAL/HIGH findings (with abraham again).
5. **Record.** Commit by path (`git add <paths>`, never `add -A`), with the observed results in the message. Push only when the user says so.

#### Several writers in one checkout (the writer coordinator)

When the machine admits it, abraham runs on the **writer coordinator**: a daemon the plugin starts on demand, which hosts each writer as a Codex app-server thread in the real checkout. The first admission takes tens of seconds, and the result says so. When admission fails, abraham runs as before, holding the checkout alone, and its result says which check failed.

- **Dispatch several at once.** Give each call its `claims`: the paths it will write. A patch into another unit's claim is refused with the holder named, and the writers can message each other and the lead. A dirty tree is refused as before, except for paths that live units or the lead's recorded edits account for.
- **Read the attribution.** In place of `[CHANGED FILES]`, the result lists each path with its label: exact (a verified receipt names the writer), custody (a command that may have changed it) or UNKNOWN (nobody can account for it).
- **Talk to the writers.** `coordinator_claims` lists, acquires and releases claims. `coordinator_send` messages a run, a unit or the lead. `coordinator_inbox` reads the lead's messages, and a coordinator-aware result carries a one-line unread notice. `coordinator_unit` shows a unit's status, its object diff and its UNKNOWN paths, and reconciles, restores, reopens or recovers.
- **Your own edits count.** In a coordinated checkout, an Edit, Write or NotebookEdit to a path a live unit holds is refused. Any other Edit or Write is recorded as yours; a NotebookEdit is not recorded, so what it wrote reads UNKNOWN (use Write for notebooks). A Bash command that changes files is recorded as yours only when, while it ran, no writer was working, no command was held, and no other lead Bash command was running in that checkout. Otherwise what it changed is UNKNOWN, and a change made during a writer's run blocks that run's hand-off. So while writers run, change files with Edit and Write, never with Bash.
- **Publish through the unit tools.** `coordinator_seal` freezes an idle unit's candidate. `code_review(sealed_unit=…)` reviews exactly those objects, and only a "Ship it" counts. `coordinator_publish` then commits that candidate and refuses without the passing review, with UNKNOWN content, or on a stale parent. No acknowledgement opens it.
- **The git gate agrees.** A plain `git commit` that would take another live unit's paths, or UNKNOWN content, is refused with the units and paths named, and no acknowledgement opens it. Leave those paths out, or publish the unit. A push is gated as before.
- **Watch them.** `codex_runs`, `codex_run_log`, `codex_cancel_run` and `codex_resume_run` handle coordinator writers too, across MCP restarts, from the dispatch folder and from the target. `/software-workflows:office` shows them, and its pause and continue act on them.
