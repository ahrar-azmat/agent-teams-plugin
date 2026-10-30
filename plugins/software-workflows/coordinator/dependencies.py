"""Immutable admission reads and lossless historical dependency objects.

Read ordinals are assigned by the daemon, in the read transaction. Backfill is
completed before an actor uses cutoffs; timestamps never establish membership.
Publication groups cutoffs and reads each actor/scope once, rather than rebuilding
every operation's cumulative prefix.
"""
import asyncio
from bisect import bisect_left, bisect_right
import json

from . import archive
from .client import CoordinatorError
from .locks import finish_thread
from .store import encoded, uid


def record_read(store, actor, checkout, revisions):
    ident = uid("read")
    with store.transaction():
        store.put("read", ident, {"checkout": checkout, "revisions": revisions}, actor["id"])
        store.db.execute("INSERT INTO read_ordinals(read_id,actor,checkout,data) VALUES(?,?,?,?)",
                         (ident, actor["id"], checkout, encoded(revisions)))


def missing_reads(root, actor, checkout):
    db = archive.readonly(root)
    try:
        rows = db.execute("SELECT id,data FROM records r WHERE parent=? AND kind='read' "
                          "AND NOT EXISTS(SELECT 1 FROM read_ordinals o WHERE o.read_id=r.id) ORDER BY id LIMIT 64", (actor,)).fetchall()
    finally:
        db.close()
    return [(row["id"], actor, json.loads(row["data"]).get("checkout", checkout),
             encoded(json.loads(row["data"])["revisions"])) for row in rows]


async def ensure_actor(store, actor):
    key = "read_index:" + actor["id"]
    if (store.get(key, required=False) or {}).get("generation") == store.generation:
        return
    while True:
        rows = await finish_thread(missing_reads, store.root, actor["id"], actor["checkout"])
        if not rows:
            # No await between the final empty snapshot and the ready marker;
            # concurrent new reads already insert their ordinal atomically.
            store.put("read_index", key, {"generation": store.generation}, actor["id"])
            return
        with store.transaction():
            store.db.executemany("INSERT OR IGNORE INTO read_ordinals(read_id,actor,checkout,data) VALUES(?,?,?,?)", rows)
        await asyncio.sleep(0)


def capture(store, actor, checkout, project):
    if not store.db.in_transaction:
        raise CoordinatorError("STORAGE_TRANSACTION", "Capture dependency membership at operation admission")
    if (store.get("read_index:" + actor["id"], required=False) or {}).get("generation") != store.generation:
        raise CoordinatorError("DEPENDENCY_INDEX", "Historical reads have not been indexed")
    scope = None if project else checkout
    row = store.db.execute("SELECT coalesce(max(ordinal),0),count(*) FROM read_ordinals WHERE actor=? "
                           "AND (? IS NULL OR checkout=?)", (actor["id"], scope, scope)).fetchone()
    store.storage4()
    return {"form": "read_cutoff", "actor": actor["id"], "checkout": scope,
            "through": row[0], "count": row[1], "qualified": bool(project)}


def historical(root, data):
    value = {"dependencies": data["dependencies"]}
    if "qualified_dependencies" in data:
        value["qualified_dependencies"] = data["qualified_dependencies"]
    raw, ref = archive.object_reference(root, value)
    if len(raw) <= len(encoded({"dependency_ref": ref})):
        return None  # no file is published for a non-beneficial rewrite
    return archive.write_object(root, value)


def references_sync(root, operations):
    """Return each operation's newly encountered read revisions, in old order.

    Increasing maxima partition each actor/scope by the first operation that
    includes an ordinal. Sort each partition by original random read id, just
    as Store.all did. Thus refusal details retain their first-seen provenance.
    Counts authenticate empty ranges too; missing evidence is never an empty set.
    """
    groups, buckets, objects = {}, [[] for _ in operations], {}
    for index, operation in enumerate(operations):
        ref = operation.get("dependency_ref")
        if not ref:
            buckets[index] = [revision for read in operation.get("dependencies", []) for revision in read.values()]
        elif ref.get("form") == "dependency_object":
            key = encoded(ref)
            if key not in objects:
                value = archive.read_object(root, ref)
                objects[key] = list(dict.fromkeys(revision for read in value["dependencies"] for revision in read.values()))
            buckets[index] = objects[key]
        elif ref.get("form") == "read_cutoff":
            groups.setdefault((ref["actor"], ref["checkout"]), []).append((index, ref))
        else:
            raise CoordinatorError("DEPENDENCY_ENCODING", "Unknown dependency representation")
    db = archive.readonly(root)
    try:
        for (actor, checkout), entries in groups.items():
            maximum = max(ref["through"] for _, ref in entries)
            rows = db.execute("SELECT ordinal,read_id,data FROM read_ordinals WHERE actor=? AND ordinal<=? "
                              "AND (? IS NULL OR checkout=?) ORDER BY ordinal", (actor, maximum, checkout, checkout)).fetchall()
            ordinals = [row["ordinal"] for row in rows]
            cutoffs, owners = [], []
            for index, ref in entries:
                if bisect_right(ordinals, ref["through"]) != ref["count"]:
                    raise CoordinatorError("DEPENDENCY_COVERAGE", "Admission read range is incomplete")
                if not cutoffs or ref["through"] > cutoffs[-1]:
                    cutoffs.append(ref["through"])
                    owners.append(index)
            assigned = {}
            for row in rows:
                owner = owners[bisect_left(cutoffs, row["ordinal"])]
                assigned.setdefault(owner, []).append(row)
            for owner, values in assigned.items():
                buckets[owner] = [revision for row in sorted(values, key=lambda r: r["read_id"])
                                  for revision in json.loads(row["data"]).values()]
    finally:
        db.close()
    return buckets


async def references(store, operations):
    return await finish_thread(references_sync, store.root, operations)
