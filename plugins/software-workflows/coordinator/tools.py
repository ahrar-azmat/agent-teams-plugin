"""Native dynamic tools with actor identity supplied only by the listener."""
import base64

from .locks import finish_thread
from .client import CoordinatorError
from .ledger import normalize, read_path
from .store import uid, encoded
from . import routing, participants

PATH = {"anyOf": [{"type": "string"}, {"type": "object", "properties": {"root_id": {"type": "string"}, "path": {"type": "string"}}, "required": ["root_id", "path"], "additionalProperties": False}]}


CONTRACTS = {
    "claim": ({"paths": {"type": "array", "items": PATH}}, ["paths"], "Atomically claim paths for your unit."),
    "release": ({"paths": {"type": "array", "items": PATH}}, ["paths"], "Request lead-authorized handback; claims remain held."),
    "declare": ({"command": {"type": "string"}, "reason": {"type": "string"}, "level": {"enum": ["shared"]}},
                ["command", "reason", "level"], "Declare one exact command invocation shared; opaque effects remain UNKNOWN."),
    "send": ({"recipient": {"type": "string"}, "text": {"type": "string"}, "wake": {"enum": ["none", "queue", "start"]}},
             ["recipient", "text", "wake"], "Persist a message to a run, actor, unit or lead, with explicit idle wake policy."),
    "inbox": ({"cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}}, [], "Read messages without acknowledging or confirming delivery."),
    "ack": ({"message": {"type": "string"}}, ["message"], "Acknowledge a message as its actual recipient."),
    "status": ({}, [], "Show writers, tasks, claims, custody and pending requests/messages."),
    "read_file": ({"path": PATH, "offset": {"type": "integer", "minimum": 0},
                   "length": {"type": "integer", "minimum": 1}}, ["path"], "Read receipt-backed raw file bytes and record the revision dependency; supports paging."),
    "list_paths": ({"cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}}, [], "List known checkout revisions without invoking shell helpers."),
}


def specifications():
    return [{"type": "function", "name": name, "description": description,
             "inputSchema": {"type": "object", "properties": fields, "required": required, "additionalProperties": False}}
            for name, (fields, required, description) in CONTRACTS.items()]


def result(value, success=True):
    return {"contentItems": [{"type": "inputText", "text": encoded(value)}], "success": success}


async def dispatch(daemon, actor, name, args, effect=None):
    if name not in CONTRACTS:
        raise CoordinatorError("DYNAMIC_TOOL", "Unknown coordinator tool")
    fields, required, _ = CONTRACTS[name]
    if not isinstance(args, dict) or set(args) - set(fields) or set(required) - set(args):
        raise CoordinatorError("TOOL_ARGUMENTS", "Unknown or missing tool arguments; actor identity is native-bound")
    if name == "claim":
        return await daemon.custody.acquire(actor, args["paths"], effect=effect)
    if effect is not None:
        effect()
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
        routed = routing.route(daemon.store, actor, args["path"])
        checkout = daemon.store.get(routed["checkout"])
        path = routed["path"]
        await daemon.ledger.reconcile(checkout, [path])
        image = await finish_thread(read_path, daemon.store, checkout, path)
        head = daemon.store.head(checkout["id"], path)
        if head and (image["blob"], image["mode"]) != (head["blob"], head["mode"]):
            raise CoordinatorError("UNSTABLE_READ", "Read revision changed")
        data = daemon.store.read_blob(image["blob"]) if image["blob"] else b""
        offset, length = args.get("offset", 0), args.get("length", 65536)
        if type(offset) is not int or type(length) is not int or offset < 0 or length < 1:
            raise CoordinatorError("READ_RANGE", "Invalid reader page")
        daemon.store.put("read", uid("read"), {"checkout": checkout["id"], "revisions": {path: head and head["id"]}}, actor["id"])
        return {"path": path, "root_id": checkout["id"], "root": checkout["root"], "revision": head and head["id"], "mode": image["mode"], "size": len(data),
                "bytes_base64": base64.b64encode(data[offset:offset + length]).decode(),
                "next_offset": offset + length if offset + length < len(data) else None}
    cursor, limit = args.get("cursor", ""), args.get("limit", 100)
    if type(limit) is not int or limit < 1:
        raise CoordinatorError("PAGE_SIZE", "Page size must be positive")
    ids = participants.checkout_ids(daemon.store, actor["unit"])
    if len(ids) == 1:
        rows = [dict(row, root_id=ids[0], root=checkout["root"]) for row in daemon.store.db.execute(
            "SELECT path,revision FROM heads WHERE checkout=? AND path>? ORDER BY path LIMIT ?", (ids[0], cursor, limit + 1))]
        next_cursor = rows[limit - 1]["path"] if len(rows) > limit else None
    else:
        import json
        try:
            position = json.loads(cursor) if cursor else ["", ""]
            if not isinstance(position, list) or len(position) != 2 or not all(isinstance(p, str) for p in position):
                raise ValueError("expected checkout/path cursor")
        except (TypeError, ValueError) as exc:
            raise CoordinatorError("PAGE_CURSOR", "Project cursor must identify a checkout and repository-relative path") from exc
        rows = []
        for key in sorted(set(ids)):
            if key < position[0]:
                continue
            root = checkout["root"] if key == checkout["id"] else daemon.store.get(key)["root"]
            rows.extend(dict(row, root_id=key, root=root) for row in daemon.store.db.execute(
                "SELECT path,revision FROM heads WHERE checkout=? AND path>? ORDER BY path LIMIT ?",
                (key, position[1] if key == position[0] else "", limit + 1 - len(rows))))
            if len(rows) > limit:
                break
        next_cursor = encoded([rows[limit - 1]["root_id"], rows[limit - 1]["path"]]) if len(rows) > limit else None
    revisions = {key: {} for key in ids}
    for row in rows[:limit]:
        revisions[row["root_id"]][row["path"]] = row["revision"]
    for key in ids:
        daemon.store.put("read", uid("read"), {"checkout": key, "revisions": revisions[key]}, actor["id"])
    return {"data": rows[:limit], "next_cursor": next_cursor}
