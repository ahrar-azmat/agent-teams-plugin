"""Receipt equivalence for Codex 0.163.0 paginated persistence.

Keep every field. Only the two native, deterministic output transformations in
rollout/src/policy.rs are alternatives to the original receipt, never arbitrary
prefix matching or a dropped output/status field.
"""
import json

from .store import digest


# This table is also published in coordinator/README.md. Unknown types retain
# full equality, so a native schema change cannot silently weaken evidence.
ITEM_PROJECTIONS = {
    "dynamicToolCall": "all fields unchanged",
    "reasoning": "all fields unchanged",
    "commandExecution": "aggregatedOutput: native 65536-byte middle truncation; all other fields unchanged",
    "fileChange": "all fields unchanged",
    "agentMessage": "all fields unchanged",
    "userMessage": "all fields unchanged",
    "contextCompaction": "all fields unchanged",
    "sleep": "all fields unchanged",
    "mcpToolCall": "result: native 65536-byte serialized preview; all other fields unchanged",
}
PERSISTED_BYTES = 65536  # Native persistence format, not a coordinator admission cap.
COMMAND_MARKER = "\n... command output truncated for persistence ...\n"


def ends(text, budget):
    data = text.encode("utf-8")
    left, right = budget // 2, budget - budget // 2
    return data[:left].decode("utf-8", "ignore"), data[len(data) - right:].decode("utf-8", "ignore") if right else ""


def command_output(text):
    if len(text.encode("utf-8")) <= PERSISTED_BYTES:
        return text
    left, right = ends(text, PERSISTED_BYTES - len(COMMAND_MARKER.encode()))
    return left + COMMAND_MARKER + right


def native_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def mcp_result(result, error):
    # The core MCP struct has this field order. The installed binary preserves
    # embedded JSON object order, as observed in its persisted text preview.
    # ThreadItem omits isError, so try its three exact
    # native representations rather than dropping fields from the comparison.
    core = {"content": result["content"]}
    if result.get("structuredContent") is not None:
        core["structuredContent"] = result["structuredContent"]
    if error is not None:
        core["isError"] = error
    if result.get("_meta") is not None:
        core["_meta"] = result["_meta"]
    text = native_json(core)
    if len(text.encode()) <= PERSISTED_BYTES:
        return result
    budget = PERSISTED_BYTES
    while True:
        if not budget:
            preview = ""
        elif len(text.encode()) <= budget:
            preview = text
        else:
            left, right = ends(text, budget)
            preview = left + "…" + str(len(text) - len(left) - len(right)) + " chars truncated…" + right
        content = [{"text": preview, "type": "text"}]
        wrapper = {"content": content}
        if error is not None:
            wrapper["isError"] = error
        size = len(native_json(wrapper).encode())
        if size <= PERSISTED_BYTES or not budget:
            return {"content": content, "structuredContent": None, "_meta": None}
        budget = min(budget * PERSISTED_BYTES // size, budget - 1)


def receipt_digests(item):
    values = {digest(item)}
    if item.get("type") == "commandExecution" and isinstance(item.get("aggregatedOutput"), str):
        values.add(digest(dict(item, aggregatedOutput=command_output(item["aggregatedOutput"]))))
    elif item.get("type") == "mcpToolCall" and isinstance(item.get("result"), dict):
        for error in (None, False, True):
            values.add(digest(dict(item, result=mcp_result(item["result"], error))))
    return sorted(values)
