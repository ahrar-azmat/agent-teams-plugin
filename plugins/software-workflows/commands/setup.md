---
description: "Set up software-workflows on this machine: turn agent teams on in your own settings (asks first), check the Codex CLI, codex login, Python 3.11+ and git, and build the Codex Oracle server's environment"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/plugin_setup.py:*), AskUserQuestion
---

The setup check ran on this machine:

!`python3 ${CLAUDE_PLUGIN_ROOT}/plugin_setup.py check --data ${CLAUDE_PLUGIN_DATA}`

Take the person through it:

1. Show the result as a short table: item, ready or not, detail.
2. Under "Setup can do these itself":
   - **agent-teams**: ask the person with AskUserQuestion whether to turn agent teams on now. Say
     exactly what changes: one key, `env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1"`, is added to the
     settings file the check named, and nothing else in it changes. Only on a yes, run the printed
     `apply agent-teams` command exactly as printed.
   - **prepare**: run the printed `prepare` command exactly as printed, without asking. It only
     builds the server's environment inside the plugin's own data directory (about 10 seconds).
3. For every other item that is not ready, give the person its `fix:` line as it is. For codex
   login, tell them to type `! codex login` in this prompt; it opens a browser.
4. Never edit a settings file any other way, and never ask the person to edit one by hand.
5. If anything was applied or built, run the check again with the command on its last line
   ("Check again: …"), exactly as printed, and show what is ready now. If agent teams were turned on, tell the person they start in
   their next Claude Code session. If the server environment was just built, or Python or Codex
   was just installed, `/mcp` → codex-oracle → Reconnect starts the server without a restart.
