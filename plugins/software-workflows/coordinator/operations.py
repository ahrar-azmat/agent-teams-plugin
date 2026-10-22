"""One invocation, many checkout parts, one receipt transaction."""
import asyncio
from contextlib import AsyncExitStack
import time

from .client import CoordinatorError
from .ledger import patch_scope, unified_post
from .routing import route, qualified


async def prepare_patch(daemon, actor, command, identity, cwd, deadline=None):
    scope, groups = [], {}
    for entry in patch_scope(command):
        source = route(daemon.store, actor, entry["path"], cwd)
        target = route(daemon.store, actor, entry["move"], cwd) if entry.get("move") else source
        scope.append(dict(kind=entry["kind"], source=source, target=target, moved=bool(entry.get("move"))))
        for endpoint in (source, target):
            groups.setdefault(endpoint["checkout"], set()).add(endpoint["path"])
    await daemon.custody.acquire(actor, [qualified(key, path) for key, paths in groups.items() for path in paths])
    return await prepare(daemon.ledger, actor, "patch", {"patch": command, "routed_scope": scope, "cwd": cwd}, groups, identity, deadline)


async def prepare(ledger, actor, kind, intended, groups, identity=None, deadline=None):
    parts = []
    try:
        # Persist parts before allowing execution. Pending parts exclude every
        # competing operation while subsequent pre-images are being collected.
        for checkout, paths in sorted(groups.items()):
            part = await ledger.prepare(dict(actor, checkout=checkout), kind, {}, list(paths), deadline=deadline)
            parts.append(part)
        parent = await ledger.prepare(actor, kind, intended, [], identity, deadline)
        with ledger.store.transaction():
            parent["parts"] = [part["id"] for part in parts]
            ledger.store.save(parent)
            for part in parts:
                part["parent_operation"] = parent["id"]
                ledger.store.save(part)
        return parent
    except BaseException:
        for part in parts:
            part.update(state="denied", no_effect=True)
            ledger.store.save(part)
        raise


async def complete_patch(ledger, operation, item):
    store = ledger.store
    try:
        if item.get("status") != "completed" or not operation.get("hook_valid") or not operation.get("post_hook"):
            raise CoordinatorError("PATCH_EVIDENCE", "Patch lacks successful native lifecycle evidence")
        if operation.get("post_payload", {}).get("command") != operation["intended"]["patch"]:
            raise CoordinatorError("PATCH_REWRITTEN", "Post-tool patch differs from admitted input")
        actor = store.get(operation["actor"])
        parts = {store.get(key)["checkout"]: store.get(key) for key in operation["parts"]}
        scope = operation["intended"]["routed_scope"]
        changes = item.get("changes", [])
        if len(changes) != len(scope):
            raise CoordinatorError("PATCH_SCOPE", "Native changes differ from admitted complete scope")
        expected, seen = {key: {} for key in parts}, set()
        for change in changes:
            source = route(store, actor, change["path"], operation["intended"]["cwd"])
            move = change["kind"].get("move_path") or change["kind"].get("movePath")
            target = route(store, actor, move, operation["intended"]["cwd"]) if move else source
            key = (source["checkout"], source["path"])
            match = next((s for s in scope if s["source"] == source), None)
            kind = change["kind"]["type"]
            if not match or match["kind"] != kind or match["target"] != target or match["moved"] != bool(move) or key in seen:
                raise CoordinatorError("PATCH_SCOPE", "Native computed endpoints differ from admitted patch")
            seen.add(key)
            outside = parts[source["checkout"]].get("outside", ())  # written, never recorded (ledger.prepare)
            if target["path"] in parts[target["checkout"]].get("outside", ()):
                # What lands outside the ledger is not recorded; a source in the ledger is gone.
                if source["path"] not in outside:
                    expected[source["checkout"]][source["path"]] = {"blob": None, "mode": None}
                continue
            pre = parts[source["checkout"]]["pre"][source["path"]]
            if pre is None:
                raise CoordinatorError("PATCH_EVIDENCE", "A move from outside the ledger left no readable pre-image", {"path": source["path"]})
            before = store.read_blob(pre["blob"]) if pre["blob"] else b""
            diff = change["diff"]
            if kind == "add":
                if pre["blob"] is not None:
                    raise CoordinatorError("PATCH_ADD", "Addition overwrites existing bytes")
                after = diff.encode("utf-8")
            elif kind == "delete":
                if pre["blob"] is None or before != diff.encode("utf-8"):
                    raise CoordinatorError("PATCH_DELETE", "Deletion differs from the source pre-image")
                expected[source["checkout"]][source["path"]] = {"blob": None, "mode": None}
                continue
            elif kind == "update":
                if pre["blob"] is None:
                    raise CoordinatorError("PATCH_UPDATE", "Update has no source pre-image")
                if move:
                    suffix = "\n\nMoved to: " + move
                    if not diff.endswith(suffix):
                        raise CoordinatorError("PATCH_MOVE", "Native move suffix differs from destination")
                    diff = diff[:-len(suffix)]
                    if source["path"] not in outside:
                        expected[source["checkout"]][source["path"]] = {"blob": None, "mode": None}
                    if parts[target["checkout"]]["pre"][target["path"]]["blob"] is not None:
                        raise CoordinatorError("PATCH_MOVE", "Move destination already exists")
                after = unified_post(before, diff)
            else:
                raise CoordinatorError("PATCH_KIND", "Unknown native patch kind")
            expected[target["checkout"]][target["path"]] = {
                "blob": await asyncio.to_thread(store.blob, after),
                "mode": parts[target["checkout"]]["pre"][target["path"]]["mode"] or "100644"}
        return await verify(ledger, operation, expected, "exact", item)
    except BaseException as exc:
        await ledger.quarantine(operation, getattr(exc, "code", type(exc).__name__))
        raise


async def verify(ledger, operation, expected, attribution, evidence):
    parts = [ledger.store.get(key) for key in operation["parts"]]
    async with AsyncExitStack() as stack:
        for checkout in sorted({part["checkout"] for part in parts}):
            await stack.enter_async_context(ledger.lock(checkout))
        for part in parts:
            # Paths outside the ledger are written, never recorded: no receipt covers them (ledger.prepare).
            if set(expected[part["checkout"]]) - set(part.get("outside", ())) != set(part["paths"]):
                raise CoordinatorError("PATCH_SCOPE", "Computed result does not cover every endpoint")
        observations = await asyncio.gather(*(ledger.verify_images(p, expected[p["checkout"]]) for p in parts))
        # No receipt is visible until every repository has passed validation.
        # Recheck heads/custody after all asynchronous observations finish.
        from .custody import interfering
        for part in parts:
            checkout = ledger.store.get(part["checkout"])
            if interfering(ledger.store, checkout, part["paths"], part["id"], part["created"]):
                raise CoordinatorError("OPAQUE_OVERLAP", "Custody changed while collecting group evidence")
            for path, base in part["bases"].items():
                head = ledger.store.head(part["checkout"], path)
                if (head and head["id"]) != base:
                    raise CoordinatorError("REVISION_CHANGED", "A group part's ledger head changed")
        with ledger.store.transaction():
            operation = ledger.completable(operation)
            if operation["parts"] != [part["id"] for part in parts]:
                raise CoordinatorError("OPERATION_STATE", "Operation parts changed during verification")
            parts = [ledger.completable(part) for part in parts]
            for part, actual in zip(parts, observations):
                ledger.apply_verified(part, actual, attribution, evidence, transaction=False)
            operation.update(state="verified", completed=time.time(), evidence=evidence,
                             results={p["checkout"]: p["revisions"] for p in parts})
            ledger.store.save(operation)
        return operation
