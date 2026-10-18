#!/usr/bin/env python3
"""Measure native exec JSON file-change fidelity; deliberately calibrate its blind spot."""
import sys
sys.dont_write_bytecode = True

import json
from pathlib import Path
import shlex

from _common import main, patch_add, patch_update, require, states, tool_items, write_json
from _responses_fixture import ResponsesFixture, patch_call


def run(probe):
    repo = probe.repo("calibration")
    before = states(repo, ["shell.txt"])
    command = shlex.join([sys.executable, "-c", "from pathlib import Path; Path('shell.txt').write_text('calibration')"])
    result = probe.codex("00-calibration", repo,
        "Use exec_command once, login=false, to run exactly: " + command + ". Then say done. No other tools.",
        {"features.hooks": False})
    require((repo / "shell.txt").read_text() == "calibration" and tool_items(result["records"], "command_execution"),
            "Calibration shell command did not execute")
    require(not tool_items(result["records"], "file_change"), "Calibration unexpectedly reported a file_change")
    write_json(probe.evidence / "calibration-states.json", {"before": before, "after": states(repo, ["shell.txt"])})
    probe.calibrate("hooks disabled; authenticated direct shell write", "Verified shell mutation emitted no file_change item.")
    probe.record("calibration", "Authenticated shell write succeeded without a file_change event.")

    patches = {
        "create": patch_add("created.txt", "created"),
        "update": patch_update("updated.txt"),
        "delete": "*** Begin Patch\n*** Delete File: deleted.txt\n*** End Patch\n",
        "rename": "*** Begin Patch\n*** Update File: source.txt\n*** Move to: destination.txt\n@@\n-base\n+moved\n*** End Patch\n",
        "delete-plus-add": "*** Begin Patch\n*** Delete File: explicit-old.txt\n*** Add File: explicit-new.txt\n+base\n*** End Patch\n",
        "partial-failure": "*** Begin Patch\n*** Update File: updated.txt\n@@\n-base\n+changed\n*** Add File: blocker/child.txt\n+child\n*** End Patch\n",
    }
    observed = {}
    for index, (name, patch) in enumerate(patches.items(), 1):
        repo = probe.repo(name)
        paths = ["created.txt", "updated.txt", "deleted.txt", "source.txt", "destination.txt", "explicit-old.txt", "explicit-new.txt", "blocker", "blocker/child.txt"]
        for p in ("updated.txt", "deleted.txt", "source.txt", "explicit-old.txt"):
            (repo / p).write_text("base\n")
        (repo / "blocker").write_text("regular file\n")
        before = states(repo, paths)
        with ResponsesFixture(probe.scratch, [patch_call(patch)]) as transport:
            result = probe.codex(f"{index:02}-{name}", repo, "Execute the supplied fixture operation.", transport=transport)
        items = tool_items(result["records"], "file_change")
        expected_status = "failed" if name == "partial-failure" else "completed"
        require(items and any(i.get("status") == expected_status for i in items), name + ": expected native patch result missing")
        if name == "create":
            correct = (repo / "created.txt").read_text() == "created\n"
        elif name == "update":
            correct = (repo / "updated.txt").read_text() == "changed\n"
        elif name == "delete":
            correct = not (repo / "deleted.txt").exists()
        elif name == "rename":
            correct = not (repo / "source.txt").exists() and (repo / "destination.txt").read_text() == "moved\n"
        elif name == "delete-plus-add":
            correct = not (repo / "explicit-old.txt").exists() and (repo / "explicit-new.txt").read_text() == "base\n"
        else:
            correct = (repo / "updated.txt").read_text() == "changed\n" and not (repo / "blocker/child.txt").exists()
        require(correct, name + ": filesystem result does not match patch")
        observed[name] = items
        write_json(probe.evidence / (name + "-states.json"), {"before": before, "after": states(repo, paths), "items": items})
        probe.record(name, "Native filesystem result verified; exact items: " + json.dumps(items), transport="local_fixture_transport")

    repo = probe.repo("authenticated-patch")
    (repo / "old.txt").write_text("base\n")
    patch = "*** Begin Patch\n*** Add File: real.txt\n+real\n*** Update File: old.txt\n*** Move to: new.txt\n@@\n-base\n+moved\n*** End Patch\n"
    before = states(repo, ["old.txt", "new.txt", "real.txt"])
    result = probe.codex("07-authenticated-patch", repo,
        "Use apply_patch directly once with this exact patch, no shell or other tools; then say done.\n" + patch)
    items = tool_items(result["records"], "file_change")
    require(items and (repo / "real.txt").read_text() == "real\n" and (repo / "new.txt").read_text() == "moved\n"
            and not (repo / "old.txt").exists(), "Authenticated direct patch not verified")
    write_json(probe.evidence / "authenticated-states.json", {"before": before, "after": states(repo, ["old.txt", "new.txt", "real.txt"]), "items": items})
    probe.record("authenticated-patch", "Real model produced direct create/rename patch; exact items: " + json.dumps(items), transport="authenticated_model")
    rename = [c for i in observed["rename"] for c in i["changes"]]
    loses_destination = not any(Path(c["path"]).name == "destination.txt" for c in rename)
    all_changes = [c for items in observed.values() for i in items for c in i["changes"]]
    probe.record("conclusion", "Paths absolute=" + str(all(Path(c["path"]).is_absolute() for c in all_changes))
                 + "; kinds=" + str(sorted({c["kind"] for c in all_changes}))
                 + "; native rename omits destination=" + str(loses_destination)
                 + ". Events do not carry postimage bytes and cannot alone reconstruct all writes.")
    return "red" if loses_destination else "green"


if __name__ == "__main__":
    raise SystemExit(main("probe_file_change_events", "Can exec file_change events alone reconstruct exact filesystem changes?", run))
