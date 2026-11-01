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
PROJECTION_VERSION = 2
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


def raw_members(text):
    """Raw JSON subvalues, without reserializing embedded objects or numbers."""
    decoder, members, offset = json.JSONDecoder(), {}, 1
    text = text.strip()
    while offset < len(text):
        while text[offset].isspace() or text[offset] == ",":
            offset += 1
        if text[offset] == "}":
            break
        key, offset = decoder.raw_decode(text, offset)
        while text[offset].isspace() or text[offset] == ":":
            offset += 1
        start = offset
        _, offset = decoder.raw_decode(text, offset)
        members[key] = text[start:offset]
    return members


def receipt_result(data):
    """Only v2 receipts retain the serialization observed on the native wire."""
    message = json.loads(data)
    if message.get("_coordinator_receipt_version") != 2:
        return None
    value = data
    for key in ("params", "item", "result"):
        value = raw_members(value).get(key)
        if value is None or value == "null":
            return None
    return raw_members(value)


def mcp_result(result, error, raw=None):
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
    text = ("{" + ",".join(native_json(key) + ":" + (raw[key] if key in raw else native_json(value))
                           for key, value in core.items()) + "}") if raw is not None else native_json(core)
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


def receipt_digests(item, raw=None, *, legacy=True):
    values = {digest(item)}
    if item.get("type") == "commandExecution" and isinstance(item.get("aggregatedOutput"), str):
        values.add(digest(dict(item, aggregatedOutput=command_output(item["aggregatedOutput"]))))
    elif item.get("type") == "mcpToolCall" and isinstance(item.get("result"), dict):
        for error in (None, False, True):
            values.add(digest(dict(item, result=mcp_result(item["result"], error, raw))))
        if legacy:
            # Older journals sorted nested keys. Native fixtures establish the
            # simple text-block layout; accept it only by exact projected hash.
            # Do not guess the lost order of arbitrary structured data/metadata.
            result = item["result"]
            content = result.get("content")
            if isinstance(content, list) and all(isinstance(block, dict) and set(block) == {"type", "text"}
                                                 and block["type"] == "text" for block in content):
                restored = dict(result, content=[{"type": block["type"], "text": block["text"]} for block in content])
                for error in (None, False, True):
                    values.add(digest(dict(item, result=mcp_result(restored, error))))
    return sorted(values)
