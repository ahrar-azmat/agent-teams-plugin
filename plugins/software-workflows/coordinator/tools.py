"""Native dynamic tools with actor identity supplied only by the listener."""
import asyncio
import base64

from .client import CoordinatorError
from .ledger import normalize, read_path
from .store import uid, encoded


CONTRACTS = {
    "claim": ({"paths": {"type": "array", "items": {"type": "string"}}}, ["paths"], "Atomically claim paths for your unit."),
    "release": ({"paths": {"type": "array", "items": {"type": "string"}}}, ["paths"], "Request lead-authorized handback; claims remain held."),
    "declare": ({"command": {"type": "string"}, "reason": {"type": "string"}, "level": {"enum": ["shared"]}},
                ["command", "reason", "level"], "Declare one exact command invocation shared; opaque effects remain UNKNOWN."),
    "send": ({"recipient": {"type": "string"}, "text": {"type": "string"}, "wake": {"enum": ["none", "queue", "start"]}},
             ["recipient", "text", "wake"], "Persist a message to a run, actor, unit or lead, with explicit idle wake policy."),
    "inbox": ({"cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}}, [], "Read messages without acknowledging or confirming delivery."),
    "ack": ({"message": {"type": "string"}}, ["message"], "Acknowledge a message as its actual recipient."),
    "status": ({}, [], "Show writers, tasks, claims, custody and pending requests/messages."),
    "read_file": ({"path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                   "length": {"type": "integer", "minimum": 1}}, ["path"], "Read receipt-backed raw file bytes and record the revision dependency; supports paging."),
    "list_paths": ({"cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}}, [], "List known checkout revisions without invoking shell helpers."),
}


def specifications():
    return [{"type": "function", "name": name, "description": description,
             "inputSchema": {"type": "object", "properties": fields, "required": required, "additionalProperties": False}}
            for name, (fields, required, description) in CONTRACTS.items()]


def result(value, success=True):
    return {"contentItems": [{"type": "inputText", "text": encoded(value)}], "success": success}


async def dispatch(daemon, actor, name, args):
    if name not in CONTRACTS:
        raise CoordinatorError("DYNAMIC_TOOL", "Unknown coordinator tool")
    fields, required, _ = CONTRACTS[name]
    if not isinstance(args, dict) or set(args) - set(fields) or set(required) - set(args):
        raise CoordinatorError("TOOL_ARGUMENTS", "Unknown or missing tool arguments; actor identity is native-bound")
    if name == "claim":
        return await daemon.custody.acquire(actor, args["paths"])
    if name == "release":
        return daemon.custody.release_request(actor, args["paths"])
    if name == "declare":
        if args["level"] != "shared":
            raise CoordinatorError("DECLARATION", "Only shared declarations are supported")
        return daemon.custody.declare(actor, args["command"], args["reason"])
    if name == "send":
        return await daemon.messages.send(actor["id"], **args)
    if name == "inbox":
        return daemon.messages.inbox(actor["id"], **args)
    if name == "ack":
        return daemon.messages.ack(actor["id"], args["message"])
    if name == "status":
        return daemon.status(actor["checkout"])
    checkout = daemon.store.get(actor["checkout"])
    if name == "read_file":
        path = normalize(checkout, args["path"])["path"]
        await daemon.ledger.reconcile(checkout, [path])
        image = await asyncio.to_thread(read_path, daemon.store, checkout, path)
        head = daemon.store.head(checkout["id"], path)
        if head and (image["blob"], image["mode"]) != (head["blob"], head["mode"]):
            raise CoordinatorError("UNSTABLE_READ", "Read revision changed")
        data = daemon.store.read_blob(image["blob"]) if image["blob"] else b""
        offset, length = args.get("offset", 0), args.get("length", 65536)
        if type(offset) is not int or type(length) is not int or offset < 0 or length < 1:
            raise CoordinatorError("READ_RANGE", "Invalid reader page")
        daemon.store.put("read", uid("read"), {"revisions": {path: head and head["id"]}}, actor["id"])
        return {"path": path, "revision": head and head["id"], "mode": image["mode"], "size": len(data),
                "bytes_base64": base64.b64encode(data[offset:offset + length]).decode(),
                "next_offset": offset + length if offset + length < len(data) else None}
    cursor, limit = args.get("cursor", ""), args.get("limit", 100)
    if type(limit) is not int or limit < 1:
        raise CoordinatorError("PAGE_SIZE", "Page size must be positive")
    rows = list(daemon.store.db.execute("SELECT path,revision FROM heads WHERE checkout=? AND path>? ORDER BY path LIMIT ?",
                                       (checkout["id"], cursor, limit + 1)))
    daemon.store.put("read", uid("read"), {"revisions": {row["path"]: row["revision"] for row in rows[:limit]}}, actor["id"])
    return {"data": [dict(row) for row in rows[:limit]], "next_cursor": rows[limit - 1]["path"] if len(rows) > limit else None}
