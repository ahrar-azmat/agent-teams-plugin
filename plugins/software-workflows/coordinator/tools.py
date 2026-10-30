"""Native dynamic tools with actor identity supplied only by the listener."""
import base64
import errno
import json
import os
import zlib

from .locks import finish_thread
from .client import CoordinatorError
from .ledger import normalize, read_path
from .store import encoded
from . import routing, participants, runtime

VERSION = runtime.plugin_version()
PATH = {"anyOf": [{"type": "string"}, {"type": "object", "properties": {"root_id": {"type": "string"}, "path": {"type": "string"}}, "required": ["root_id", "path"], "additionalProperties": False}]}

# What one tool result may encode to, in bytes: store.encoded writes ASCII, so a character is a byte. Measured 10-29 on
# codex 0.163.0 with gpt-6-astra in code_mode_only, the writers' mode (r2417/ceiling_probe.py): a code-mode cell's
# output reached the model whole up to 32,047 characters, and from 64,000 on was cut to about 40,150 with its middle
# left out (codex keeps 10,000 tokens of a cell's output at about 4 bytes each: unified_exec DEFAULT_MAX_OUTPUT_TOKENS);
# a direct call was cut at about 48,000. The cell's JavaScript saw all 1,398,444 characters of a 1 MiB read_file page.
# Through 2.4.16 nothing bounded a result: read_file's default page encoded to 87,720 characters, and one status answer
# on the user's ledger to 19.5 MB (its 702 status answers kept 3.3 GB there). 32,000 is 80 % of that ceiling: a cell
# printing one result keeps 8,000 bytes for its own text. Adjustable from 4,096 to RESULT_CEILING with
# SOFTWARE_WORKFLOWS_TOOL_RESULT_BYTES; above the ceiling a result is shown cut.
RESULT_CEILING = 40_000


def _bytes(name, default):
    value = os.environ.get(name)
    if not value:
        return default
    try:
        result = int(value)
    except ValueError:
        result = 0
    if not 4096 <= result <= RESULT_CEILING:
        raise CoordinatorError("CONFIG", name + " must be a whole number of bytes from 4096 to " + str(RESULT_CEILING),
                               {"value": value})
    return result


RESULT_BYTES = _bytes("SOFTWARE_WORKFLOWS_TOOL_RESULT_BYTES", 32_000)
# A completed call's response up to this size is kept as sent; a larger one is kept compressed (stored).
STORED_PLAIN_BYTES = 2048
# How much of a run's task and of each custody command status shows, in bytes as encoded (ensure_ascii: an astral
# character is 12); the full length is named (task_chars, command_chars).
TASK_BYTES, COMMAND_BYTES = 400, 200
# The most entries any page can hold: every path row, message and run shown encodes to more than 64 bytes. Lists load
# at most this many and one more, so a larger `limit` is the budget's to cut (named limited), never SQLite's to refuse.
PAGE_ROWS = RESULT_BYTES // 64
# How many messages one inbox query loads: a page fills from as many slices as it needs, never loading mail it cannot
# show (one message is up to 393,664 characters on the user's ledger).
INBOX_SLICE = 16
# What an inbox shows of a message, besides its text and a count of its delivery attempts (the list grows with each
# retried delivery).
MESSAGE_FIELDS = ("id", "sender", "recipient", "state", "delivery", "wake", "created", "notice")
# How much of a caller's own string an error echoes back (a revision or message id is under 64 bytes).
ECHO_BYTES = 120
# What a read_file page's value carries to the record (stored), popped before the value is encoded.
SOURCE = "\x00source"
# The fields a list or page cut to the budget names.
LIMITED = {"reason": "TOOL_RESULT_BUDGET", "budget_bytes": RESULT_BYTES}
# Tool results since the daemon started (daemon/status tool_results): how many, the largest, how many came within 80 %
# of the budget, and by tool those limited to it (a page or list shorter than asked) or over it (only an effect's own
# answer can be: every reading tool pages within it).
METRICS = {"budget_bytes": RESULT_BYTES, "results": 0, "largest_bytes": 0, "near_budget": 0, "limited": {}, "over_budget": {}}

CONTRACTS = {
    "services_private": ({"specification": {"type": "object"}}, [], "Allocate isolated private project services for server-level tests, optionally with a different local image or configuration; read the returned owner-only environment file. These containers are removed when your run ends."),
    "claim": ({"paths": {"type": "array", "items": PATH}}, ["paths"], "Atomically claim paths for your unit."),
    "release": ({"paths": {"type": "array", "items": PATH}}, ["paths"], "Request lead-authorized handback; claims remain held."),
    "declare": ({"command": {"type": "string"}, "reason": {"type": "string"}, "level": {"enum": ["shared"]}},
                ["command", "reason", "level"], "Declare one exact command invocation shared; opaque effects remain UNKNOWN."),
    "send": ({"recipient": {"type": "string"}, "text": {"type": "string"}, "wake": {"enum": ["none", "queue", "start"]}},
             ["recipient", "text", "wake"], "Persist a message to a run, actor, unit or lead, with explicit idle wake policy."),
    "inbox": ({"cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}, "message": {"type": "string"},
               "text_offset": {"type": "integer", "minimum": 0}}, [],
              "Read messages without acknowledging or confirming delivery, a page at a time (next_cursor). A message "
              "too long for one page shows text_next_offset: read the rest with message and text_offset."),
    "ack": ({"message": {"type": "string"}}, ["message"], "Acknowledge a message as its actual recipient."),
    "status": ({"cursor": {"type": "string"}}, [], "Show the writers on your checkout, yours first: tasks, claims, custody, "
               "pending requests and unacknowledged message counts, a page at a time (next_cursor)."),
    "read_file": ({"path": PATH, "offset": {"type": "integer", "minimum": 0}, "length": {"type": "integer", "minimum": 1},
                   "revision": {"type": "string"}}, ["path"],
                  "Read receipt-backed raw file bytes and record the revision dependency. A page holds what reaches you "
                  "whole (about 23 KB): continue at next_offset with the revision it returned; a file changed since "
                  "is refused REVISION_CHANGED."),
    "list_paths": ({"cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}}, [],
                   "List known checkout revisions without invoking shell helpers, a page at a time (next_cursor)."),
}


def specifications():
    return [{"type": "function", "name": name, "description": description,
             "inputSchema": {"type": "object", "properties": fields, "required": required, "additionalProperties": False}}
            for name, (fields, required, description) in CONTRACTS.items()]


def conforms(value, schema):
    """Whether value has the shape schema declares, for the JSON Schema subset CONTRACTS uses. Native forwards a
    dynamic call's arguments as the model wrote them, unchecked (codex 0.163.0 core/src/tools/handlers/dynamic.rs:141):
    through 2.4.17's review a value of another type reached SQLite or a comparison, whose error nothing answered (a
    list as list_paths' cursor, review turn 104), so the call waited."""
    if "anyOf" in schema:
        return any(conforms(value, option) for option in schema["anyOf"])
    if "enum" in schema:
        return any(type(value) is type(option) and value == option for option in schema["enum"])
    kind = schema.get("type")
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return type(value) is int and value >= schema.get("minimum", value)
    if kind == "array":
        return isinstance(value, list) and all(conforms(item, schema["items"]) for item in value)
    if kind == "object":
        fields = schema.get("properties")
        return isinstance(value, dict) and (fields is None or (
            set(schema.get("required", ())) <= set(value)
            and (schema.get("additionalProperties", True) or set(value) <= set(fields))
            and all(conforms(item, fields[key]) for key, item in value.items() if key in fields)))
    return True


def result(value, success=True, name=None):
    text = encoded(value)
    METRICS["results"] += 1
    METRICS["largest_bytes"] = max(METRICS["largest_bytes"], len(text))
    if len(text) * 5 >= RESULT_BYTES * 4:
        METRICS["near_budget"] += 1
    if len(text) > RESULT_BYTES:
        METRICS["over_budget"][name or "unnamed"] = METRICS["over_budget"].get(name or "unnamed", 0) + 1
    return {"contentItems": [{"type": "inputText", "text": text}], "success": success}


def limited(name, **fields):
    METRICS["limited"][name] = METRICS["limited"].get(name, 0) + 1
    return dict(LIMITED, **fields)


def metrics():
    return dict(METRICS, limited=dict(METRICS["limited"]), over_budget=dict(METRICS["over_budget"]))


async def invoke(daemon, actor, name, args, effect=None):
    """One dynamic call: the response native is sent, and the form its record keeps (stored)."""
    try:
        value = await dispatch(daemon, actor, name, args, effect=effect)
    except CoordinatorError as exc:
        response = result({"error": bounded_error(exc.envelope())}, False, name)
        return response, stored(response)
    source = value.pop(SOURCE, None) if isinstance(value, dict) else None
    response = result(value, True, name)
    return response, stored(response, value, source)


def bounded_error(envelope):
    """An error within the budget. Details too large for it (a caller's own path or string echoed back, PATH_ESCAPE's
    among them) are left out, their size named (details_omitted_bytes); code, message and kind stay."""
    if len(encoded({"error": envelope})) <= RESULT_BYTES:
        return envelope
    bounded = dict(envelope, details={"details_omitted_bytes": len(encoded(envelope.get("details")))})
    message = str(bounded.get("message") or "")
    room = RESULT_BYTES - len(encoded({"error": dict(bounded, message="")}))
    return dict(bounded, message=message[:fitted_chars(message, 0, room)])


def as_text(value):
    """A stored field shown as text: a string as it is, nothing as "", anything else as its JSON. A record a lead's RPC
    wrote before its field was checked can hold any JSON there (review turn 105: a numeric task, a list as sender), and
    every projection cuts text by encoded size."""
    return value if isinstance(value, str) else "" if value is None else encoded(value)


def echoed(details, name, text):
    """details with a caller's own string under name as an error echoes it: cut by encoded size (ECHO_BYTES), its full
    length then named (<name>_chars)."""
    count = fitted_chars(text, 0, ECHO_BYTES)
    details[name] = text[:count]
    if count < len(text):
        details[name + "_chars"] = len(text)
    return details


def stored(response, value=None, source=None):
    """What a completed call's record keeps, from which replayed() rebuilds the response exactly (never from the
    working file): a read_file page as its value without the bytes and the receipt range they came from (receipts are
    immutable, and nothing deletes one: a reclamation that does must keep those a stored page names); any other
    response over STORED_PLAIN_BYTES compressed; else the response itself, as every record before 2.4.17 kept it."""
    if source is not None:
        rest = {k: v for k, v in value.items() if k != "bytes_base64"}
        return {"form": "receipt_range", "value": rest, "receipt": source["receipt"], "offset": source["offset"],
                "length": source["length"], "success": response["success"]}
    text = encoded(response)
    if len(text) <= STORED_PLAIN_BYTES:
        return response
    return {"form": "deflate", "bytes": len(text), "data": base64.b64encode(zlib.compress(text.encode(), 6)).decode()}


def replayed(store, kept):
    """The exact response a completed call sent, from its record (stored). Reads a receipt: run it off the loop."""
    form = kept.get("form") if isinstance(kept, dict) else None
    if form is None:
        return kept
    if form == "deflate":
        return json.loads(zlib.decompress(base64.b64decode(kept["data"])))
    if form == "receipt_range":
        data, _ = store.read_blob_range(kept["receipt"], kept["offset"], kept["length"])
        if len(data) != kept["length"]:
            raise CoordinatorError("BLOB_CORRUPT", "A stored page's receipt is shorter than the page", {"blob": kept["receipt"]}, True)
        value = dict(kept["value"], bytes_base64=base64.b64encode(data).decode())
        return {"contentItems": [{"type": "inputText", "text": encoded(value)}], "success": kept["success"]}
    raise CoordinatorError("DYNAMIC_STORED_FORM", "Unknown stored response form", {"form": form})


async def dispatch(daemon, actor, name, args, effect=None):
    if name not in CONTRACTS:
        raise CoordinatorError("DYNAMIC_TOOL", "Unknown coordinator tool")
    fields, required, _ = CONTRACTS[name]
    if not isinstance(args, dict):
        raise CoordinatorError("TOOL_ARGUMENTS", "Unknown or missing tool arguments; actor identity is native-bound")
    # Checked against the declared schema before anything is marked or read; a refusal names only the tool's own
    # argument names, and counts the caller's unknown ones.
    invalid = sorted(key for key in fields if key in args and not conforms(args[key], fields[key]))
    missing, unknown = sorted(set(required) - set(args)), sum(key not in fields for key in args)
    if invalid or missing or unknown:
        raise CoordinatorError("TOOL_ARGUMENTS", "Unknown, missing or invalid tool arguments; actor identity is native-bound",
                               {"invalid": invalid, "missing": missing, "unknown": unknown})
    if name == "claim":
        return await daemon.custody.acquire(actor, args["paths"], effect=effect)
    if effect is not None:
        effect()
    if name == "services_private":
        await daemon.broker.acquire(actor["run"], private=True, specification=args.get("specification"))
        return daemon.broker.private_files(actor["run"])
    if name == "release":
        return daemon.custody.release_request(actor, args["paths"])
    if name == "declare":
        if args["level"] != "shared":
            raise CoordinatorError("DECLARATION", "Only shared declarations are supported")
        return daemon.custody.declare(actor, args["command"], args["reason"])
    if name == "send":
        sent = await daemon.messages.send(actor["id"], **args)
        # The text is the caller's own: the answer says only what became of it (2.4.16 echoed the whole record).
        return {k: sent[k] for k in ("id", "recipient", "state", "delivery", "wake") if k in sent}
    if name == "inbox":
        if "message" in args or "text_offset" in args:
            return message_text(daemon, actor, args)
        return writer_inbox(daemon, actor, args.get("cursor", ""), args.get("limit", 100))
    if name == "ack":
        return daemon.messages.ack(actor["id"], args["message"])
    if name == "status":
        return writer_status(daemon, actor, args.get("cursor", ""))
    if name == "read_file":
        return await read_page(daemon, actor, args)
    checkout = daemon.store.get(actor["checkout"])
    cursor, limit = args.get("cursor", ""), args.get("limit", 100)
    if type(limit) is not int or limit < 1:
        raise CoordinatorError("PAGE_SIZE", "Page size must be positive")
    fetch = min(limit, PAGE_ROWS)  # through 2.4.16 a limit past 2**63 - 2 reached SQLite, which refused it unanswered
    ids = participants.checkout_ids(daemon.store, actor["unit"])
    if len(ids) == 1:
        rows = [dict(row, root_id=ids[0], root=checkout["root"]) for row in daemon.store.db.execute(
            "SELECT path,revision FROM heads WHERE checkout=? AND path>? ORDER BY path LIMIT ?", (ids[0], cursor, fetch + 1))]
    else:
        try:
            position = json.loads(cursor) if cursor else ["", ""]
            if not isinstance(position, list) or len(position) != 2 or not all(isinstance(p, str) for p in position):
                raise ValueError("expected checkout/path cursor")
            for part in position:
                # A JSON escape can decode to a lone surrogate, which SQLite cannot bind (UnicodeEncodeError is a
                # ValueError); a deep nesting can exhaust the decoder (review turn 105).
                part.encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise CoordinatorError("PAGE_CURSOR", "Project cursor must identify a checkout and repository-relative path") from exc
        rows = []
        for key in sorted(set(ids)):
            if key < position[0]:
                continue
            root = checkout["root"] if key == checkout["id"] else daemon.store.get(key)["root"]
            rows.extend(dict(row, root_id=key, root=root) for row in daemon.store.db.execute(
                "SELECT path,revision FROM heads WHERE checkout=? AND path>? ORDER BY path LIMIT ?",
                (key, position[1] if key == position[0] else "", fetch + 1 - len(rows))))
            if len(rows) > fetch:
                break

    def cursor_of(row):
        return row["path"] if len(ids) == 1 else encoded([row["root_id"], row["path"]])
    # The rows that fit the budget, at most `limit` of them; the next page starts after the last one shown, so each row
    # is shown only with room left for its own cursor.
    room = RESULT_BYTES - len(encoded({"data": [], "next_cursor": None,
                                       "limited": dict(LIMITED, requested=limit, returned=limit)}))
    shown = []
    for row in rows[:fetch]:
        size = len(encoded(row)) + 1
        if size + len(encoded(cursor_of(row))) > room:
            break
        shown.append(row)
        room -= size
    alone = not shown and bool(rows)
    if alone:
        # A path and root no page can hold (up to about 12 KB escaped) are shown alone, over the budget and named,
        # never refused: refusing one would stop every walk at it.
        shown = rows[:1]
    page = {"data": shown, "next_cursor": cursor_of(shown[-1]) if len(rows) > len(shown) else None}
    if alone:
        page["limited"] = limited("list_paths", requested=limit, returned=1, reason="ENTRY_OVER_BUDGET")
    elif len(shown) < min(limit, len(rows)):
        page["limited"] = limited("list_paths", requested=limit, returned=len(shown))
    revisions = {key: {} for key in ids}
    for row in shown:
        revisions[row["root_id"]][row["path"]] = row["revision"]
    for key in ids:
        from .dependencies import record_read
        record_read(daemon.store, actor, key, revisions[key])
    return page


async def read_page(daemon, actor, args):
    """One read_file page. Its range is checked before anything is reconciled or read; it holds what the budget
    leaves after its envelope (about 23 KB); it continues a revision only while the file still has it; and only its
    bytes are read from the receipt, off the loop (store.read_blob_range), never the whole file per page."""
    offset, requested, pinned = args.get("offset", 0), args.get("length"), args.get("revision")
    if (type(offset) is not int or offset < 0 or (requested is not None and (type(requested) is not int or requested < 1))
            or (pinned is not None and (not isinstance(pinned, str) or not pinned))):
        raise CoordinatorError("READ_RANGE", "Invalid reader page")
    routed = routing.route(daemon.store, actor, args["path"])
    checkout = daemon.store.get(routed["checkout"])
    path = routed["path"]
    try:
        await daemon.ledger.reconcile(checkout, [path])
        image = await finish_thread(read_path, daemon.store, checkout, path)
    except OSError as exc:
        # A file the coordinator cannot open (its permissions) is refused, never left unanswered; a storage failure
        # while its receipt is written (ENOSPC, EMFILE) still stops native intake (routing.ACCESS_REFUSALS).
        if exc.errno not in routing.ACCESS_REFUSALS:
            raise
        raise CoordinatorError("READ_FAILED", "The file cannot be read", {"path": path, "errno": errno.errorcode.get(exc.errno, exc.errno)}) from exc
    head = daemon.store.head(checkout["id"], path)
    if head and (image["blob"], image["mode"]) != (head["blob"], head["mode"]):
        raise CoordinatorError("UNSTABLE_READ", "Read revision changed")
    revision = head and head["id"]
    if pinned is not None and pinned != revision:
        raise CoordinatorError("REVISION_CHANGED", "The file changed since the page this continues: read it again from "
                               "offset 0", echoed({"path": path, "revision": revision}, "continued", pinned))
    value = {"path": path, "root_id": checkout["id"], "root": checkout["root"], "revision": revision, "mode": image["mode"]}
    widest = 10 ** 19  # room for any size, offset and count
    envelope = dict(value, size=widest, next_offset=widest, bytes_base64="",
                    limited=dict(LIMITED, requested=widest, returned=widest, page_bytes=widest))
    page = (RESULT_BYTES - len(encoded(envelope))) // 4 * 3
    if page < 1:
        raise CoordinatorError("READ_RANGE", "This path leaves no room for a page within the result budget", {"path": path})
    want = requested if requested is not None else page
    data, size = (await finish_thread(daemon.store.read_blob_range, image["blob"], offset, min(want, page))
                  if image["blob"] else (b"", 0))
    from .dependencies import record_read
    record_read(daemon.store, actor, checkout["id"], {path: revision})
    value.update(size=size, bytes_base64=base64.b64encode(data).decode(),
                 next_offset=offset + len(data) if offset + len(data) < size else None)
    if len(data) < min(want, max(0, size - offset)):
        value["limited"] = limited("read_file", requested=want, returned=len(data), page_bytes=page)
    if data:
        value[SOURCE] = {"receipt": image["blob"], "offset": offset, "length": len(data)}
    return value


def fitted_chars(text, offset, room):
    """The most characters of text from offset that encode within room bytes (a code point is never split): a binary
    search over the prefix, whose encoded size only grows with it; every character encodes to at least one byte."""
    low, high = 0, max(0, min(len(text) - offset, room))
    while low < high:
        middle = (low + high + 1) // 2
        if len(encoded(text[offset:offset + middle])) - 2 <= room:
            low = middle
        else:
            high = middle - 1
    return low


def message_view(record):
    """What an inbox shows of a message: MESSAGE_FIELDS, its text, and how many delivery attempts it took
    (delivery_attempts). All but the text are bounded: a string field is cut as an error's echo is (ECHO_BYTES), its
    full length named (<field>_chars) - a lead's message/send takes any sender (review turn 104: a 5,000-character
    sender kept a page over a 4,096-byte budget), the rest are ids, states, a time and a flag. Through 2.4.16 the
    record was shown whole, its attempts listed, which grow with each retried delivery: 100 attempts encode to more
    than 4,096 bytes."""
    view = {}
    for key in MESSAGE_FIELDS:
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, str) or len(encoded(value)) > ECHO_BYTES:
            echoed(view, key, as_text(value))  # any other JSON a lead's RPC stored, as its JSON, cut (review turn 105)
        else:
            view[key] = value
    attempts = record.get("attempts")
    view.update(text=as_text(record.get("text")), delivery_attempts=len(attempts) if isinstance(attempts, list) else 0)
    return view


def text_page(view, offset, room):
    """view with its text from offset cut to what room leaves, the cut named: text_chars, text_offset and
    text_next_offset (None once the text ends). Never refused: room too small for any of the text shows none of it,
    and text_next_offset says where inbox with message and text_offset reads it."""
    text = view.get("text") or ""
    base = dict(view, text="", text_chars=len(text), text_offset=offset, text_next_offset=len(text))
    end = offset + fitted_chars(text, offset, room - len(encoded(base)))
    return dict(base, text=text[offset:end], text_next_offset=end if end < len(text) else None)


def writer_inbox(daemon, actor, cursor, limit):
    """A writer's inbox page within the budget: its messages in id order (message_view), loaded INBOX_SLICE at a time
    until the page is full or `limit` are shown, so no more mail is loaded than a page can show. A first message too
    long for the page shows the part of its text that fits (text_page), and the next page starts after it."""
    if type(limit) is not int or limit < 1:
        raise CoordinatorError("PAGE_SIZE", "Page size must be positive")
    if not isinstance(cursor, str):
        raise CoordinatorError("PAGE_CURSOR", "An inbox cursor is the last message id of the page before")
    room = RESULT_BYTES - len(encoded({"data": [], "next_cursor": "x" * 64, "unread": 10 ** 9,
                                       "limited": dict(LIMITED, requested=limit, returned=limit)}))
    shown, after, unread, more, full = [], cursor, None, True, False
    while more and not full and len(shown) < limit:
        page = daemon.messages.inbox(actor["id"], after, min(INBOX_SLICE, limit - len(shown)))
        unread = page["unread"] if unread is None else unread
        more = page["next_cursor"] is not None
        for record in page["data"]:
            view = message_view(record)
            size = len(encoded(view)) + 1
            if size > room:
                if not shown:
                    shown.append(text_page(view, 0, room))
                full = True
                break
            shown.append(view)
            room -= size
        if shown:
            after = shown[-1]["id"]
    inbox = {"data": shown, "next_cursor": shown[-1]["id"] if shown and (full or more) else None, "unread": unread}
    if full:
        inbox["limited"] = limited("inbox", requested=limit,
                                   returned=sum(1 for view in shown if view.get("text_next_offset") is None))
    return inbox


def message_text(daemon, actor, args):
    """More of one message's text, from text_offset: only a message to the caller (Messages.recipient, as its inbox)."""
    ident, offset = args.get("message"), args.get("text_offset", 0)
    if not isinstance(ident, str) or not ident or type(offset) is not int or offset < 0:
        raise CoordinatorError("PAGE_CURSOR", "A message's text is read by its id and a character offset")
    recipient, _ = daemon.messages.recipient(actor["id"])
    record = daemon.store.get(ident, required=False)
    if not record or record.get("_kind") != "message" or record.get("recipient") != recipient:
        raise CoordinatorError("MESSAGE_NOT_FOUND", "No such message to you", echoed({}, "message", ident))
    text = as_text(record.get("text"))
    if offset > len(text):
        raise CoordinatorError("PAGE_CURSOR", "The offset is past the message's text", {"text_chars": len(text)})
    part = {"message": ident, "text": "", "text_chars": len(text), "text_offset": offset, "text_next_offset": len(text)}
    count = fitted_chars(text, offset, RESULT_BYTES - len(encoded(part)))
    end = offset + count
    return dict(part, text=text[offset:end], text_next_offset=end if end < len(text) else None)


def writer_status(daemon, actor, cursor):
    """What a writer's status shows, within the budget: the coordinator's readiness, the UNKNOWNs open on its
    checkouts, its own run, then the other runs on its checkout in run id order, a page at a time. Each run (brief): its
    task, state, claims, custody and pending requests, and how many of its unit's messages are unacknowledged. Mail is read with inbox: through 2.4.16 this listed every unacknowledged message on the checkout,
    other writers' too, with every custody record whole (one is about 94 KB on the user's ledger)."""
    store = daemon.store
    if not isinstance(cursor, str):
        raise CoordinatorError("PAGE_CURSOR", "A status cursor is the last run id of the page before")
    unacknowledged = dict(store.db.execute(
        "SELECT json_extract(data,'$.unit'), count(*) FROM records WHERE kind='message' AND "
        "json_extract(data,'$.state')!='acknowledged' GROUP BY 1").fetchall())
    runs = [r for r in store.all("run") if r["id"] != actor["run"] and participants.contains(store, r["unit"], actor["checkout"])]
    runs.sort(key=lambda r: r["id"])
    status = {"ready": daemon.ready, "draining": daemon.draining, "version": VERSION, "unknowns": {},
              "self": None, "writers": [], "writers_total": len(runs), "next_cursor": None}
    # Every checkout's open UNKNOWNs, in id order, within a quarter of the budget: a project of dozens of repositories
    # shows the rest counted (unknowns_checkouts, unknowns_total).
    counts = {key: store.unresolved_count(key) for key in participants.checkout_ids(store, actor["unit"])}
    room = RESULT_BYTES // 4 - len(encoded({"unknowns_checkouts": 10 ** 9, "unknowns_total": 10 ** 9}))
    for key in sorted(counts):
        room -= len(encoded({key: counts[key]}))
        if room < 0:
            status.update(unknowns_checkouts=len(counts), unknowns_total=sum(counts.values()))
            break
        status["unknowns"][key] = counts[key]
    room = RESULT_BYTES - len(encoded(dict(status, next_cursor="x" * 64, limited=dict(LIMITED, requested=10 ** 9, returned=10 ** 9))))
    own = brief(daemon, store.get(actor["run"]), unacknowledged, room // 2)
    status["self"] = own
    room -= len(encoded(own))
    page = room  # each run is cut only when it alone exceeds a page; one that fits a page waits for the next
    later = [r for r in runs if r["id"] > cursor]
    for index, run in enumerate(later):
        entry = brief(daemon, run, unacknowledged, page - 1)
        size = len(encoded(entry)) + 1
        if size > room:
            if not status["writers"]:
                raise CoordinatorError("PAGE_SIZE", "A writer's status does not fit one page", {"run": run["id"]})
            status["next_cursor"] = later[index - 1]["id"]
            break
        status["writers"].append(entry)
        room -= size
    else:
        return status
    status["limited"] = limited("status", requested=len(later), returned=len(status["writers"]))
    return status


def brief(daemon, run, unacknowledged, room):
    """One run in a writer's status, within room bytes. Its task and each custody command show TASK_BYTES and
    COMMAND_BYTES of them as encoded, their full length named (task_chars, command_chars); a list too long for room is
    halved until it fits, its total named (<list>_total); and only then is the task shortened further."""
    store = daemon.store
    unit = store.get(run["unit"])
    listener = store.get((store.get(run.get("root_actor") or "", required=False) or {}).get("listener") or "",
                         required=False) or {}
    task = as_text(run.get("task"))
    custody = []
    for c in daemon.custody.unit_active(unit["id"]):
        command = as_text(c.get("command"))
        shown = {"id": c["id"], "level": c.get("level"), "state": c.get("state"),
                 "command": command[:fitted_chars(command, 0, COMMAND_BYTES)]}
        if len(shown["command"]) < len(command):
            shown["command_chars"] = len(command)
        custody.append(shown)
    entry = {"run": run["id"], "unit": unit["id"], "state": daemon.runs.reported(run)[0], "writer": listener.get("state"),
             "task": task[:fitted_chars(task, 0, TASK_BYTES)],
             "claims": [{"root_id": c["checkout"], "path": c["path"]} for member in participants.members(store, unit)
                        for c in daemon.custody.claims(member["checkout"], unit["id"])],
             "custody": custody,
             "pending_requests": [{"id": r["id"], "kind": r["_kind"], "state": r["state"]}
                                  for kind in ("claim_request", "release_request", "custody_request")
                                  for r in store.all(kind, unit["id"]) if r["state"] in ("pending", "pending_lead", "waiting")],
             "messages_unacknowledged": unacknowledged.get(unit["id"], 0)}
    if len(entry["task"]) < len(task):
        entry["task_chars"] = len(task)
    while len(encoded(entry)) > room:
        name = max(("claims", "custody", "pending_requests"), key=lambda k: len(encoded(entry[k])))
        if not entry[name]:
            break
        entry.setdefault(name + "_total", len(entry[name]))
        entry[name] = entry[name][:len(entry[name]) // 2]
    if len(encoded(entry)) > room:
        entry.update(task="", task_chars=len(task))
        entry["task"] = task[:fitted_chars(task, 0, room - len(encoded(entry)))]
    if len(encoded(entry)) > room:
        raise CoordinatorError("PAGE_SIZE", "A writer's status entry does not fit one page", {"run": run["id"]})
    return entry
