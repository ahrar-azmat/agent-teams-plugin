# Troubleshooting and checklists

Read this when a call fails, or before you start or finish work with other agents.

## Troubleshooting

**`InputValidationError: An unexpected parameter '...' was provided`:** you passed a parameter the tool's schema doesn't accept. Usually it is `team_name` on `Agent` (dropped), or `recipient`, `type`, `content` or `broadcast` on `SendMessage` (use `to`, `message` and `summary`).

**`schema was not sent to the API … Load the tool first`:** a deferred tool isn't loaded in this context. Load it with `ToolSearch(query="select:<name>")`, batching several names in one call.

**A named `Agent` didn't become a teammate:** expected here. T3 runs Claude as an Agent SDK session, where teammates never spawn, so a named `Agent` is a plain subagent. Writers belong in T3 children anyway (SKILL.md).

**A message to a native writer (outside T3) was refused as cancelled:** the user stopped it (`x` in `/tasks`, or an SDK stop). Ask the user to resume it, or start a new writer with a new charter.

**A native subagent died with the session:** T3 released or restarted the lead's session (after the 4-hour pin, or on a restart). `SendMessage` to its name resumes it with its memory, but the command it was running is lost: tell it to re-run that step. This is one more reason writers are T3 children.

**`steer` to a T3 child was refused or queued:** the child has finished, and `steer` needs an active turn. Use `t3_thread_send(mode="auto")`.

**File conflicts:** two writers need the same file. In a shared checkout, order them: soft-steer one to leave that file until the other is done, or run the overlapping work in sequence. Or give the second writer a worktree (`isolation: "worktree"`) and land its work after the first writer's is committed. Read each writer's changes by its own paths before committing.

**A writer went the wrong way:** hard-steer it, saying what to do instead and whether to redo the step that was stopped. Its task and earlier instructions still stand. Stop it for good only when the task itself is no longer wanted: `task_cancel` for a T3 child, `TaskStop` for a native one.

**The turn end was refused with "Finished writers' worktrees still hold work":** a writer in a worktree has finished, and its work is not yet in the main checkout. For each listed path, follow `writers.md` §5 (the hook's message carries the same steps):
- see everything it holds: `git status --short --ignored` and `git ls-files -v | grep -v '^H '`, and have the writer commit what should land;
- note the main checkout's `git rev-parse HEAD` and the writer's tip as literal SHAs (a shell variable does not survive between Bash calls);
- run `swctl wt check <branch>` in the main checkout, and move aside each untracked file it lists;
- land exactly the range it prints: `git cherry-pick <base>..<tip>`, or `git merge --squash --no-overwrite-ignore <tip>` and `git commit`;
- review `git diff <noted HEAD>`;
- then `git worktree remove <path>`, which deletes ignored files and flag-hidden edits without asking;
- delete the branch only once `git diff <writer tip> HEAD -- <its paths>` shows only later changes, and at that tip: `git update-ref -d refs/heads/<branch> <writer tip>`; otherwise keep it.

If one must wait for the next turn, say which and why, then end the turn. The hook refuses once per turn.

**"This command left N process(es) running … they were stopped":** something the command started outlived it: a server sent to the background, a daemon, `nohup`, `setsid`. That is the rule working. To keep something running, start it with `swctl run --name <name> --ttl <2h> -- <command>`, or add a line to `~/.config/janitor/keep`. Every stop is logged in `~/.local/state/software-workflows/cleanup/cleanup.log`.

**Codex hooks don't run:** Codex runs a user-level hook only once it is trusted, and an untrusted one is skipped without a word (measured on 0.171.0). `swctl install` re-adds and re-trusts ours, and the 10-minute timer re-checks whenever `~/.codex/hooks.json` changes. `codex_trust` lines in `cleanup.log` show the outcome.

**The push was refused with `push gate: review coverage incomplete`:** the push contains content that no completed `code_review` saw. The message names the paths. Run `code_review` on the change and push again. Or re-run the same push with the token from the message (`CODEX_PUSH_ACK=<token> git push …`), or say it up front with `CODEX_PUSH_UNREVIEWED="<reason>" git push …`. Commits are never gated.

**Codex not being used:** the helper's prompt must tell it to call the Codex Oracle tools. Subagents have them but won't use them unless prompted.

**Codex agreed with you and the code still broke:** classic anchoring. Check whether the dispatch put your diagnosis in `context`, `concerns` or `focus`. If so, you asked the model to grade your own answer and it obliged. Re-dispatch blind: the evidence and the question only, with any hypothesis in `caller_hypothesis`. A **⚠️ ANCHORING WARNING** banner on a result is the server telling you this already happened.

**`⚠️ ANCHORING WARNING` banner on a result:** the lint found conclusion language in a neutral scoping field. The answer is not worthless, but its *agreement* with you is. Re-run that dispatch blind. Disagreement in the flagged answer is still trustworthy.

**The advisor cited a version or API that turned out to be wrong:** check whether it gave a URL. The server requires a Sources section and forces live web search, so an answer with no sources came from memory. Re-ask, demanding primary sources. Also re-check file:line citations in the tree that is canon for the task: Codex reads the whole workspace and can cite a sibling checkout.

**The advisor's answer reads like generic best practice with no specifics:** it did not actually research. Re-ask with the concrete question, and require URLs and retrieval dates.

## Checklist

Before starting:
- [ ] Each piece of work is on the right tool (SKILL.md, "Which tool for which agent"): writers and anything longer than a few minutes are T3 children with a `title` that says what they do.
- [ ] Each `Agent` call (a quick helper) has `description`, `name`, `subagent_type`, `model: "opus"` and `prompt`, with no `team_name`.
- [ ] At most 5–10 writers at once. Each charter names the writer's own files (or its worktree). It says the writer never pushes and starts nothing, and that LEAD STEER messages add information and never cancel its task. A writer in the shared checkout doesn't commit. A writer in its own worktree commits on that worktree's branch, after the pre-reply check (`writers.md` §1).
- [ ] No hand-made worktrees: no `git worktree add` and no `EnterWorktree` in any prompt or script; worktrees come only from `isolation: "worktree"`.
- [ ] Researcher and architect prompts tell the helper to use Codex DURING the work, not just after.
- [ ] No prompt uses conservative language ("keep existing", "maintain current", "the approach is fine"). Every prompt pushes performance, quality and adaptability.
- [ ] Cross-model prompts are **blind**: the evidence and the question, with no diagnosis from the caller. Any hypothesis rides in `caller_hypothesis`, never in `context`, `concerns`, `focus` or `topic`.

Before finishing:
- [ ] Advisor answers that carry external claims came back **with URLs**. Unsourced version, API or CVE claims were re-asked, not repeated.
- [ ] Calls into swappable backends were checked for **present-but-unsupported**, and probed on the engine actually deployed, in the mode it runs.
- [ ] No result arrived with a **⚠️ ANCHORING WARNING** banner, or that dispatch was re-run blind before being trusted.
- [ ] Each writer's changes were read by its paths and its checks re-run. One blind `code_review` covered the final change, and its CRITICAL and HIGH findings were addressed.
- [ ] Every writer and helper has finished or been stopped. Every finished writer's worktree is landed and removed (`swctl wt list` shows none finished). What must keep running runs under `swctl run` with a TTL (`swctl run list`).
