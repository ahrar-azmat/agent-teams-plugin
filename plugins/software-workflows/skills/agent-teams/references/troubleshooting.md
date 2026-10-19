# Agent Teams — troubleshooting and checklists

Read this when a team call fails or before you start or wrap up a team.

## Troubleshooting

**`InputValidationError: An unexpected parameter '...' was provided`:** You passed a parameter the tool's schema doesn't accept. Almost always one of: `team_name` on any tool (it's deprecated and ignored — drop it), `blockedBy` on `TaskCreate` (create first, then `TaskUpdate(addBlockedBy=[...])`), or `recipient`/`type`/`content`/`broadcast` on `SendMessage` (use `to`/`message`/`summary`). See the tool signatures in SKILL.md for the exact parameter list per tool.

**Looking for `TeamCreate` / `TeamDelete`:** They don't exist (removed in Claude Code v2.1.178). Don't try to load or call them. Spawn a teammate to start the team; send `shutdown_request` (and/or let the session exit) to end it.

**`schema was not sent to the API … Load the tool first`:** The deferred tool isn't loaded in the current context. The lead loads tools in Step 1; each teammate must also run `ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")` as its first action.

**Teammate not responding:** They're idle (normal). Send a message — it wakes them up.

**Task stuck as blocked:** Check if the blocking task is actually done but wasn't marked completed. Use `TaskUpdate` to fix.

**File conflicts:** a writer's patch into another unit's claim is refused with the holder named. Let the two writers settle it by message (the holder releases, or the second waits), or run the overlapping work in sequence. Never isolate a writer in a git worktree (SKILL.md rule 9).

**A worktree appeared anyway** (a teammate ran `git worktree add`, or an `Agent` call carried `isolation: "worktree"`): stop that teammate, save the tree's changes as a patch (`git -C <tree> diff` plus any untracked files), apply the patch in the real checkout by path, then `git worktree remove <tree>` and `git worktree prune`. Never delete a tree with plain `rm`.

**Lead implementing instead of delegating:** Tell it: "Wait for your teammates to complete their tasks before proceeding."

**Plan approval stuck:** The architect finished planning but you didn't see the request. Check for a `plan_approval_request` message and respond with `plan_approval_response`.

**Teammate ignoring tasks:** Their prompt might be missing the workflow instructions. Make sure the prompt tells them to check TaskList, use TaskGet, and mark tasks in_progress/completed.

**Codex not being used:** Check that the teammate prompt explicitly instructs them to call the MCP tools. Teammates have access but won't use them unless prompted.

**Codex agreed with you and the code still broke:** classic anchoring. Check whether the dispatch put your diagnosis in `context`/`concerns`/`focus` — if so, you asked the model to grade your own answer and it obliged. Re-dispatch blind (evidence + question only, hypothesis in `caller_hypothesis`). A **⚠️ ANCHORING WARNING** banner on a result is the server telling you this already happened.

**`⚠️ ANCHORING WARNING` banner on a result:** the lint found conclusion language in a neutral scoping field. The answer is not worthless — but its *agreement* with you is. Re-run that dispatch blind; disagreement in the flagged answer is still trustworthy.

**Advisor cited a version/API that turned out to be wrong:** check whether it gave a URL. The server requires a Sources section and forces live web search; an answer with no sources answered from memory. Re-ask demanding primary sources. Also re-verify file:line citations in the tree that is canon for the task — Codex reads the whole workspace and can cite a sibling checkout.

**Advisor answer reads like generic best-practice with no specifics:** it did not actually research. Re-ask with the concrete question and require URLs + retrieval dates.

## Checklist

Before starting a team:
- [ ] Loaded deferred tools with `ToolSearch(query="select:TaskCreate,TaskUpdate,SendMessage,TaskGet,TaskList")` — NO TeamCreate/TeamDelete (they don't exist)
- [ ] Task calls use ONLY documented params — NO `team_name` or `blockedBy` on `TaskCreate`/`TaskUpdate`; set dependencies via `TaskUpdate(taskId=..., addBlockedBy=[...])`
- [ ] `SendMessage` calls use `to` / `message` / `summary` — never `recipient` / `type` / `content` / `broadcast`; protocol `type` goes inside the `message` object
- [ ] Tasks are specific and self-contained with full context in description
- [ ] No two writers will write the same paths: writers in one checkout hold disjoint claims (SKILL.md rule 9)
- [ ] NO git worktrees: no `isolation: "worktree"`, no `EnterWorktree`, no `git worktree add` in any prompt or script (SKILL.md rule 9)
- [ ] Each `Agent()` call has `description`, `name`, `model: "opus"`, `subagent_type`, `run_in_background: true`, `prompt` — and NO `team_name`
- [ ] Teammate prompts START with a `ToolSearch(query="select:TaskList,TaskGet,TaskUpdate,TaskCreate,SendMessage")` step
- [ ] Teammate prompts include: name, full workflow (TaskList → TaskGet → in_progress → work → completed → SendMessage)
- [ ] Architects use `mode: "plan"` to require plan approval
- [ ] Researchers and architects are prompted to use Codex DURING their work (not just after)
- [ ] Reviewers are prompted to use Codex with strict/critical analysis
- [ ] NO prompts contain conservative language ("keep existing", "maintain current", "the approach is fine")
- [ ] ALL prompts emphasize: performance, quality, adaptability — challenge what exists
- [ ] Cross-model prompts are **blind**: evidence + question, NO caller diagnosis. Any hypothesis rides in `caller_hypothesis`, never in `context`/`concerns`/`focus`/`topic`
- [ ] Advisor answers carrying external claims came back **with URLs**; unsourced version/API/CVE claims were re-asked, not repeated
- [ ] Calls into swappable backends (browser engine, DB driver, storage/LLM provider, cloud SDK, container binary) were checked for **present-but-unsupported** — verified against the vendor compatibility matrix and probed on the engine actually deployed, not the library default
- [ ] Any engine/driver/provider/version **swap** in the diff triggered a sweep of every call into that surface
- [ ] No `try`/`except` degrades a capability miss into a silent no-op without recording which failure occurred
- [ ] No result arrived with a **⚠️ ANCHORING WARNING** banner (if one did, it was re-dispatched blind before being trusted)

Before wrapping up:
- [ ] All tasks marked `completed`
- [ ] All teammates shut down via `shutdown_request` (the implicit team then cleans up on session exit — there is no `TeamDelete`)
