#!/usr/bin/env python3
"""Measure project-document loading and uncapped metadata-discovery cost."""
import sys
sys.dont_write_bytecode = True

import json
import uuid

from _common import init_repo, main, require, tool_items, write_json
from _discovery import benchmark
from _responses_fixture import ResponsesFixture


PROMPT = "Without tools, output every U0TOKEN_ token already present in your initial instructions. Output none if absent."


def run(probe):
    outer = probe.scratch / "instructions"
    root = init_repo(outer / "root")
    execution = root / "middle" / "execution"
    execution.mkdir(parents=True)
    nested = init_repo(execution / "nested")
    standalone = probe.scratch / "standalone"
    standalone.mkdir()
    tokens = {}

    def sentinel(directory, filename, label):
        token = "U0TOKEN_" + uuid.uuid4().hex
        (directory / filename).write_text("Harmless U0 loading sentinel: " + token + ". Report it only if asked.\n")
        tokens[label] = token

    for directory, label in ((outer, "above"), (root, "root"), (root / "middle", "middle"),
                             (execution, "execution"), (nested, "nested"), (standalone, "standalone")):
        sentinel(directory, "AGENTS.md", label + "_agents")
        sentinel(directory, "CLAUDE.md", label + "_claude")
    defaults = {"project_doc_max_bytes": 32768, "project_doc_fallback_filenames": [], "project_root_markers": [".git"]}

    def exact(name, cwd, config, expected, flags=()):
        with ResponsesFixture(probe.scratch) as transport:
            result = probe.codex(name, cwd, PROMPT, config, transport=transport, flags=flags)
        require(len(result["requests"]) == 1, "Context fixture unexpectedly requested another turn")
        context = json.dumps(result["requests"][0])
        present = sorted(label for label, token in tokens.items() if token in context)
        require(present == sorted(expected), name + ": unexpected native context: " + str(present))
        probe.record(name, "Exact initial context contains " + str(present) + ".", transport="local_fixture_transport")
        return result

    # Calibration is an authenticated run plus exact controlled context. Absence
    # in a model answer alone is deliberately not treated as proof of exclusion.
    result = probe.codex("00-calibration-model", execution, PROMPT, {"project_doc_max_bytes": 0})
    require(not any(tool_items(result["records"], k) for k in ("command_execution", "file_change", "mcp_tool_call")), "Calibration model used a tool")
    require(not any(t in result["stdout"] for t in tokens.values()), "Zero-byte calibration model reported a project token")
    exact("01-calibration-context", execution, {**defaults, "project_doc_max_bytes": 0}, [])
    probe.calibrate("project_doc_max_bytes=0", "Authenticated model reported no project sentinels; exact synthetic initial context contained none.")
    exact("02-defaults-ancestry", execution, defaults, ["root_agents", "middle_agents", "execution_agents"])
    exact("03-inside-nested", nested, defaults, ["nested_agents"])
    exact("04-non-repository", standalone, defaults, ["standalone_agents"], flags=["--skip-git-repo-check"])
    exact("05-fallback-with-agents", execution, {**defaults, "project_doc_fallback_filenames": ["CLAUDE.md"]},
          ["root_agents", "middle_agents", "execution_agents"])
    # These unlink operations remove only this probe's generated fixture files.
    (execution / "AGENTS.md").unlink()
    exact("06-claude-only-defaults", execution, defaults, ["root_agents", "middle_agents"])
    exact("07-claude-only-fallback", execution, {**defaults, "project_doc_fallback_filenames": ["CLAUDE.md"]},
          ["root_agents", "middle_agents", "execution_claude"])
    sentinel(execution, "AGENTS.md", "execution_agents_restored")
    sentinel(execution, "AGENTS.override.md", "execution_override")
    exact("08-override-precedence", execution, defaults, ["root_agents", "middle_agents", "execution_override"])
    (execution / "AGENTS.override.md").unlink()
    for name, config in (("09-authenticated-inherited", {}), ("10-authenticated-defaults", defaults)):
        result = probe.codex(name, execution, PROMPT, config)
        require(not any(tool_items(result["records"], k) for k in ("command_execution", "file_change", "mcp_tool_call")), name + ": model used tools")
        messages = "\n".join(i.get("text", "") for i in tool_items(result["records"], "agent_message"))
        present = sorted(label for label, token in tokens.items() if token in messages)
        require(present, name + ": no loaded sentinel reported by real model")
        probe.record(name, "Real model reported " + str(present) + "; nonappearance is weaker than exact context exclusion.", transport="authenticated_model")
    write_json(probe.evidence / "sentinels.json", tokens)
    measurements = benchmark(probe.scratch)
    write_json(probe.evidence / "discovery-benchmark.json", measurements)
    for measurement in measurements:
        first = measurement["traversals"][0]
        summary = {k: v for k, v in measurement.items() if k != "traversals"}
        summary.update({k: first[k] for k in ("entries_visited", "directories_visited", "git_metadata_subprocess_count", "repositories_found", "errors")})
        summary["aliases_or_cycles_found"] = len(first["aliases_or_cycles"])
        probe.record("discovery-" + str(measurement["ordinary_files"]), json.dumps(summary))
    probe.record("conclusion", "Default instructions follow execution ancestry to the nearest repository boundary. CLAUDE.md requires a fallback and loses to AGENTS.md; nested repositories are not recursively loaded. Discovery is separately required.")
    return "red"


if __name__ == "__main__":
    raise SystemExit(main("probe_instruction_loading", "Does native default instruction loading cover all workspace repositories and CLAUDE.md files?", run))
