#!/usr/bin/env python3
"""Pass the Claude session's directory to the Oracle tools scoped by hooks.json."""
import json
import os
import sys


def main():
    data = json.load(sys.stdin)
    cwd = data.get("cwd")
    if data.get("hook_event_name") != "PreToolUse" or not isinstance(cwd, str) or not os.path.isabs(cwd):
        return
    updated = dict(data.get("tool_input") or {})
    updated["dispatch_cwd"] = cwd
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}))


if __name__ == "__main__":
    main()
