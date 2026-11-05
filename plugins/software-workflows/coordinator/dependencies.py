"""Immutable admission reads and lossless historical dependency objects.

Read ordinals are assigned by the daemon, in the read transaction. Backfill is
completed before an actor uses cutoffs; timestamps never establish membership.
Publication groups cutoffs and reads each actor/scope once, rather than rebuilding
every operation's cumulative prefix.
"""
import asyncio
from bisect import bisect_left, bisect_right
from collections import deque
import json
import threading
import time

from . import archive
from .client import CoordinatorError
from .locks import finish_thread
from .store import digest, encoded, uid


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


class Evidence:
    """Snapshot-local lookups. The caller owns the connection and its lifetime."""
    readonly = True

    def __init__(self, db, root):
        self.db, self.root = db, root
        self.records, self.revisions, self.events, self.witnesses, self.unknowns = {}, {}, {}, {}, {}
        self.tokens, self.places = {}, {}

    def get(self, ident, required=True):
        if ident not in self.records:
            row = self.db.execute("SELECT id,kind,revision,json_remove(data,'$.output','$.post_payload',"
                                  "'$.evidence','$.native','$.intended.patch') AS data FROM records WHERE id=?", (ident,)).fetchone()
            self.tokens[ident] = row["revision"] if row else None
            self.records[ident] = dict(json.loads(row["data"]), id=ident, _kind=row["kind"],
                                       _revision=row["revision"]) if row else None
        value = self.records[ident]
        if value is None and required:
            raise CoordinatorError("PROVENANCE_MISSING", "Dependency record is unavailable", {"record": ident})
        return value

    def revision(self, ident):
        if ident not in self.revisions:
            row = self.db.execute("SELECT * FROM revisions WHERE id=?", (ident,)).fetchone()
            self.revisions[ident] = dict(row) if row else None
        return self.revisions[ident]

    def restore_events(self, operation, checkout, path):
        """A restore's unknown/restored and unknown/checkout_restored events at one path: those of the path's UNKNOWNs
        and of the checkout-wide ones ("*"), read through unknown_checkout and event_subject_kind. An index over every
        event's restore took 6.5 s to build on a copy of the live ledger (11-05, 1.05 M events), all of it inside the
        first start's switch window."""
        if checkout not in self.unknowns:
            paths = {}
            for row in self.db.execute("SELECT id,path FROM unknowns WHERE checkout=? ORDER BY id", (checkout,)):
                paths.setdefault(row["path"], []).append(row["id"])
            self.unknowns[checkout] = paths
        found = []
        for where in dict.fromkeys((path, "*")):
            for unknown in self.unknowns[checkout].get(where, ()):
                if unknown not in self.events:
                    self.events[unknown] = [dict(row, data=json.loads(row["data"])) for row in self.db.execute(
                        "SELECT seq,subject,data FROM events WHERE subject=? AND kind IN ('unknown/restored',"
                        "'unknown/checkout_restored') ORDER BY seq", (unknown,))]
                found += [dict(event, checkout=checkout, path=where) for event in self.events[unknown]
                          if event["data"].get("restore") == operation]
        return sorted(found, key=lambda event: event["seq"])

    def outside(self, checkout, paths):
        """Of these paths, those outside the ledger now (ledger.unwatched; asked once per path): no dependency, as
        through 2.4.35, since nothing there is recorded or published. A git failure refuses (GIT_FAILED). The root is
        read without a token: a checkout's root never changes, and its record changes with every lease."""
        from .ledger import unwatched
        unknown = {path for path in paths if (checkout, path) not in self.places}
        if unknown:
            row = self.db.execute("SELECT json_extract(data,'$.root') FROM records WHERE id=?", (checkout,)).fetchone()
            found = unwatched({"root": row[0]}, unknown) if row and row[0] else set()
            self.places.update({(checkout, path): path in found for path in unknown})
        return {path for path in paths if self.places[(checkout, path)]}

    def matching(self, row):
        key = (row["checkout"], row["path"], row["blob"], row["mode"])
        if key not in self.witnesses:
            self.witnesses[key] = [dict(value) for value in self.db.execute(
                "SELECT * FROM revisions WHERE checkout=? AND path=? AND attribution IN ('baseline','exact','restore') "
                "AND blob IS ? AND mode IS ? ORDER BY id", key)]
        return [value for value in self.witnesses[key] if value["id"] != row["id"]]


def restore_source(evidence, operation, row):
    """A missing source is distinct from a recorded removal. Events outlive resolution upserts."""
    events = evidence.restore_events(operation["id"], row["checkout"], row["path"])
    recorded = operation.get("restored_from") or {}
    sources = []
    for event in events:
        value = event["data"]
        if row["path"] in (value.get("known_revisions") or {}):
            sources.append(value["known_revisions"][row["path"]])
        elif event["path"] == row["path"]:
            if value.get("known_revision"):
                sources.append(value["known_revision"])
            elif "requested" in value:
                sources.append(value["requested"])
            elif row["path"] not in recorded and value.get("resolution") == "baseline":
                sources.append("baseline")
    if sources and any(source != sources[0] for source in sources):
        raise CoordinatorError("PROVENANCE_MISSING", "Restore events disagree about their source",
                               {"operation": operation["id"], "revision": row["id"]})
    if row["path"] in recorded:
        if sources and sources[0] != recorded[row["path"]]:
            raise CoordinatorError("PROVENANCE_MISSING", "Restore record and event sources disagree",
                                   {"operation": operation["id"], "revision": row["id"]})
        return True, recorded[row["path"]], events
    return (True, sources[0], events) if sources else (False, None, events)


class Evaluator:
    """AND edges carry inputs; legacy byte witnesses are grounded alternatives."""
    def __init__(self, evidence, unit, bases, read_blob):
        from . import transfers
        self.evidence, self.unit, self.bases, self.read_blob = evidence, unit, bases, read_blob
        self.carried = transfers.Lookup(evidence)
        self.nodes, self.edges, self.pending = {}, {}, deque()
        self.reads, self.objects, self.entries, self.blobs = {}, {}, {}, {}
        self.read_queries = 0
        self.roots, self.excluded = set(), set()

    def node(self, ident, **value):
        if ident not in self.nodes:
            self.nodes[ident] = dict(value, id=ident, required=set(), alternatives=set(), proofs=[])
            if value.get("kind") == "revision":
                self.pending.append(ident)
        return self.nodes[ident]

    def edge(self, source, target, kind, alternative=False, **use):
        self.nodes[source]["alternatives" if alternative else "required"].add(target)
        value = dict(source=source, target=target, kind=kind, **use)
        self.edges[digest(value)] = value

    def fail(self, node, code, message, **details):
        node["error"] = dict(code=code, message=message, **details)

    def reference(self, source, revision, kind, checkout=None, path=None, alternative=False, **use):
        row = self.evidence.revision(revision)
        target = self.node(revision, kind="revision", revision=revision,
                           checkout=row["checkout"] if row else checkout, path=row["path"] if row else path)
        self.edge(source, revision, kind, alternative=alternative, checkout=checkout, path=path, **use)
        if not row:
            self.fail(target, "PROVENANCE_MISSING", "Dependency revision is unavailable")
        elif checkout is not None and row["checkout"] != checkout or path is not None and row["path"] != path:
            self.fail(self.nodes[source], "PROVENANCE_MISSING", "Dependency revision has the wrong location", revision=revision)
        return row

    def base_matches(self, row):
        from . import gitio
        base = self.bases.get(row["checkout"])
        if not base:
            return False, None
        checkout = self.evidence.get(row["checkout"])
        key = (row["checkout"], base)
        if key not in self.entries:
            entries = {}
            # Include directories so absence proofs cannot mistake a tree for a missing path.
            for value in gitio.git(checkout["root"], ["ls-tree", "-rtz", "--full-tree", base]).stdout.split(b"\0"):
                if value:
                    meta, name = value.split(b"\t", 1)
                    mode, kind, oid = meta.decode("ascii").split()
                    path = name.decode("utf-8", "strict")
                    entries[path] = {"path": path, "mode": mode, "type": kind, "oid": oid}
            self.entries[key] = entries
        entry = self.entries[key].get(row["path"])
        if row["blob"] is None:
            return row["mode"] is None and entry is None, entry
        if row["mode"] not in ("100644", "100755"):
            return False, entry
        key = (row["blob"], checkout["object_format"])
        if key not in self.blobs:
            self.blobs[key] = gitio.blob_oid(self.read_blob(row["blob"]), checkout["object_format"])
        return entry == {"path": row["path"], "type": "blob", "mode": row["mode"], "oid": self.blobs[key]}, entry

    def authorize(self, row, node):
        from . import transfers
        if transfers.published(self.evidence, row, self.carried):
            node["proofs"].append(dict(revision=row["id"], checkout=row["checkout"], path=row["path"],
                                       publication=self.carried.get(row["id"])["publication"]))
            return True
        grant = transfers.proof(self.evidence, self.unit, row, self.carried)
        current = transfers.right(self.evidence, row["checkout"], row["path"], self.carried)
        if grant:
            transfers.transfer_proof(self.evidence, current)
            node["proofs"].append(grant)
        if row["attribution"] == "restore":
            return False  # Its source, not the restoring unit, owns the bytes.
        if (row["unit"]
              and not (row["unit"] == self.unit and (not current or current.get("owner") == self.unit))):
            matches, entry = self.base_matches(row)
            if matches:
                node["proofs"].append(dict(revision=row["id"], source_revision=row["id"], checkout=row["checkout"],
                                           base=self.bases[row["checkout"]], path=row["path"], entry=entry))
                return True
            elif not grant:
                self.fail(node, "UNPUBLISHED_DEPENDENCY", "Dependency has no publication or transfer proof", unit=row["unit"])
        return False

    def read_context(self, operation):
        ident = "context:" + operation["id"]
        if ident in self.nodes:
            return ident
        node = self.node(ident, kind="context", operation=operation["id"])
        ref = operation.get("dependency_ref")
        if ref and ref.get("form") == "read_cutoff":
            actor, checkout, through, count = (ref.get(k) for k in ("actor", "checkout", "through", "count"))
            if (not {"actor", "checkout", "through", "count"} <= ref.keys()
                    or actor != operation.get("actor") or checkout not in (None, operation["checkout"])
                    or type(through) is not int or type(count) is not int or through < 0 or count < 0):
                raise CoordinatorError("DEPENDENCY_COVERAGE", "Admission read scope is inconsistent")
            group = self.reads.setdefault((actor, checkout), {"through": -1, "ordinals": [], "nodes": []})
            if through > group["through"]:
                query = "SELECT ordinal,read_id,checkout,data FROM read_ordinals WHERE actor=? AND ordinal>? AND ordinal<=?"
                args = (actor, group["through"], through)
                if checkout is not None:
                    query, args = query + " AND checkout=?", (*args, checkout)
                rows = self.evidence.db.execute(query + " ORDER BY ordinal", args)
                self.read_queries += 1
                for row in rows:
                    readid = "read:" + digest([actor, checkout, row["ordinal"]])
                    self.node(readid, kind="read", actor=actor, checkout=row["checkout"], read=row["read_id"], ordinal=row["ordinal"])
                    if group["nodes"]:
                        self.edge(readid, group["nodes"][-1], "read/prefix")
                    for path, revision in sorted(json.loads(row["data"]).items()):
                        if revision:
                            self.reference(readid, revision, "read", checkout=row["checkout"], path=path,
                                           read=row["read_id"], ordinal=row["ordinal"])
                    self.nodes[readid]["revisions"] = json.loads(row["data"])
                    group["ordinals"].append(row["ordinal"])
                    group["nodes"].append(readid)
                group["through"] = through
            size = bisect_right(group["ordinals"], through)
            if size != count:
                raise CoordinatorError("DEPENDENCY_COVERAGE", "Admission read range is incomplete",
                                       {"operation": operation["id"], "through": through, "expected": count, "actual": size})
            cutoff = "cutoff:" + digest(ref)
            self.node(cutoff, kind="cutoff", **{k: ref[k] for k in ("actor", "checkout", "through", "count")})
            self.edge(ident, cutoff, "read/context", operation=operation["id"])
            if size:
                self.edge(cutoff, group["nodes"][size - 1], "read/prefix")
            return ident
        value = operation
        if ref:
            if ref.get("form") != "dependency_object":
                raise CoordinatorError("DEPENDENCY_ENCODING", "Unknown dependency representation")
            key = encoded(ref)
            if key not in self.objects:
                self.objects[key] = archive.read_object(self.evidence.root, ref)
            value = self.objects[key]
        qualified = value.get("qualified_dependencies")
        if qualified is not None:
            entries = [(read["checkout"], read["revisions"]) for read in qualified]
        else:
            entries = [(operation["checkout"], read) for read in value.get("dependencies", [])]
        for index, (checkout, revisions) in enumerate(entries):
            readid = "read:" + digest([checkout, revisions])
            if readid not in self.nodes:
                self.node(readid, kind="read", checkout=checkout, revisions=revisions)
                for path, revision in sorted(revisions.items()):
                    if revision:
                        self.reference(readid, revision, "read", checkout=checkout, path=path)
            self.edge(ident, readid, "read/context", operation=operation["id"], entry=index)
        return node["id"]

    def mutation_base(self, source, operation, path, kind="base"):
        if path in operation.get("outside", ()):
            return  # written outside the ledger, never recorded (ledger.prepare): no base, no dependency
        if path not in operation.get("bases", {}):
            self.fail(self.nodes[source], "PROVENANCE_MISSING", "Mutation base was not recorded",
                      operation=operation["id"], path=path)
        elif operation["bases"][path]:
            self.reference(source, operation["bases"][path], kind, checkout=operation["checkout"], path=path,
                           operation=operation["id"])
        elif (operation.get("pre", {}).get(path) or {}).get("blob") is not None:
            self.fail(self.nodes[source], "PROVENANCE_MISSING", "Present preimage has no mutation receipt",
                      operation=operation["id"], path=path)

    def restore(self, row, operation):
        node = self.nodes[row["id"]]
        found, source, events = restore_source(self.evidence, operation, row)
        if found and source not in (None, "baseline"):
            receipt = self.reference(row["id"], source, "restore/source", checkout=row["checkout"], path=row["path"],
                                     operation=operation["id"])
            if receipt and ((receipt["blob"], receipt["mode"]) != (row["blob"], row["mode"])
                            or receipt["attribution"] not in ("baseline", "exact", "restore")):
                self.fail(node, "PROVENANCE_MISSING", "Restore source differs from its receipt", operation=operation["id"])
            return
        if found:
            if source is None and (row["blob"] is not None or row["mode"] is not None):
                self.fail(node, "PROVENANCE_MISSING", "Removal receipt contains bytes", operation=operation["id"])
                return
            verified = [event["seq"] for event in events if event["data"].get("resolution") == "baseline"]
            matches, entry = (False, None) if verified else self.base_matches(row)
            if verified or matches:
                node["witness"] = dict(kind="baseline", events=verified, base=self.bases.get(row["checkout"]), entry=entry)
                return
            candidates = [value for value in self.evidence.matching(row) if value["attribution"] == "baseline"]
        else:
            candidates = self.evidence.matching(row)
        if not candidates:
            self.fail(node, "PROVENANCE_MISSING", "Restore has no concrete source or byte-and-mode witness",
                      operation=operation["id"])
        for candidate in candidates:
            self.reference(row["id"], candidate["id"], "restore/witness", alternative=True,
                           checkout=row["checkout"], path=row["path"], operation=operation["id"])

    def expand(self, ident):
        node, row = self.nodes[ident], self.evidence.revision(ident)
        if not row:
            return
        node.update(operation=row["operation"], attribution=row["attribution"], unit=row["unit"])
        if row["attribution"] == "UNKNOWN":
            unknowns = [dict(value) for value in self.evidence.db.execute(
                "SELECT u.id AS unknown,s.restore,s.edit FROM unknowns u LEFT JOIN resolutions s ON s.unknown_id=u.id "
                "WHERE u.revision=? ORDER BY u.id", (ident,))]
            self.fail(node, "UNKNOWN_DEPENDENCY", "Dependency reaches unexplained bytes")
            for unknown in unknowns:
                key = "unknown:" + unknown["unknown"]
                self.node(key, kind="unknown", revision=ident, **unknown)
                self.edge(ident, key, "unknown/resolution")
            return
        if row["attribution"] == "baseline":
            return
        if row["attribution"] not in ("exact", "restore", "lead-command"):
            self.fail(node, "PROVENANCE_MISSING", "Dependency has no recognized attribution")
            return
        if self.authorize(row, node) or node.get("error"):
            return  # Publication/proof ends the path; failed authorization blocks it here.
        operation = self.evidence.get(row["operation"], required=False)
        if (not operation or operation.get("checkout") != row["checkout"]
                or operation.get("revisions", {}).get(row["path"]) != row["id"]
                or operation.get("state") not in ("verified", "resolved", "reconciled")):
            self.fail(node, "PROVENANCE_MISSING", "Producing operation does not attest this receipt")
            return
        self.edge(ident, self.read_context(operation), "read/context", operation=operation["id"])
        parent = self.evidence.get(operation["parent_operation"]) if operation.get("parent_operation") else operation
        if parent is not operation:
            self.evidence.get(parent.get("checkout"))
            if operation["id"] not in parent.get("parts", []):
                self.fail(node, "PROVENANCE_MISSING", "Routed operation does not contain its producing part")
                return
            self.edge(ident, self.read_context(parent), "read/context", operation=parent["id"])
        if row["attribution"] == "restore":
            self.restore(row, operation)
            return
        if operation.get("kind") in ("command", "mcp", "lead-command"):
            return  # Opaque snapshots are not exact mutation inputs.
        self.mutation_base(ident, operation, row["path"])
        intended = parent.get("intended", {})
        for move in intended.get("routed_scope", []):
            if move.get("moved") and move["target"] == {"checkout": row["checkout"], "path": row["path"]}:
                self.evidence.get(move["source"].get("checkout"))
                parts = [self.evidence.get(key) for key in parent["parts"]]
                source = next((part for part in parts if part["checkout"] == move["source"]["checkout"]), None)
                if source is None:
                    self.fail(node, "PROVENANCE_MISSING", "Move source part is unavailable")
                else:
                    self.mutation_base(ident, source, move["source"]["path"], "move/source")
        for move in intended.get("scope", []):
            if move.get("move") == row["path"]:
                self.mutation_base(ident, operation, move["path"], "move/source")

    def classify(self, idents):
        """As through 2.4.35, a base or read at a path outside the ledger now (ledger.unwatched) is no dependency:
        nothing there is published. Such a revision is not expanded, so it stands as a satisfied leaf. An output's own
        path is never asked; each level asks each checkout once, of paths not asked before."""
        rows = [(ident, self.evidence.revision(ident)) for ident in idents]
        rows = [(ident, row) for ident, row in rows if row and (row["checkout"], row["path"]) not in self.roots]
        wanted = {}
        for _, row in rows:
            wanted.setdefault(row["checkout"], set()).add(row["path"])
        outside = {checkout: self.evidence.outside(checkout, paths) for checkout, paths in wanted.items()}
        for ident, row in rows:
            if row["path"] in outside[row["checkout"]]:
                self.excluded.add(ident)
                self.nodes[ident]["outside"] = True

    def evaluate(self, roots):
        rootids = []
        self.roots = {(row["checkout"], row["path"]) for row in roots}
        for row in roots:
            ident = "output:" + row["id"]
            self.node(ident, kind="output", revision=row["id"], checkout=row["checkout"], path=row["path"])
            rootids.append(ident)
            actual = self.reference(ident, row["id"], "output", checkout=row["checkout"], path=row["path"])
            if actual != row:
                self.fail(self.nodes[ident], "PROVENANCE_MISSING", "Frozen output differs from its receipt")
        while self.pending:
            level = list(self.pending)
            self.pending.clear()
            self.classify(level)
            for ident in level:
                if ident in self.excluded:
                    continue
                try:
                    self.expand(ident)
                except CoordinatorError as exc:
                    if exc.code != "PROVENANCE_MISSING":
                        raise
                    self.fail(self.nodes[ident], exc.code, str(exc), **exc.details)
        parents, remaining, choices, valid = {}, {}, {}, set()
        for ident, node in self.nodes.items():
            remaining[ident] = len(node["required"])
            for child in node["required"]:
                parents.setdefault(child, []).append((ident, False))
            for child in node["alternatives"]:
                parents.setdefault(child, []).append((ident, True))
        ready = deque(sorted(ident for ident, node in self.nodes.items()
                             if not node.get("error") and not remaining[ident] and not node["alternatives"]))
        while ready:
            ident = ready.popleft()
            if ident in valid:
                continue
            valid.add(ident)
            for parent, alternative in parents.get(ident, []):
                if alternative:
                    choices.setdefault(parent, ident)
                else:
                    remaining[parent] -= 1
                if (not remaining[parent] and not self.nodes[parent].get("error")
                        and (not self.nodes[parent]["alternatives"] or parent in choices)):
                    ready.append(parent)
        selected, pending = set(), list(rootids)
        while pending:
            ident = pending.pop()
            if ident in selected:
                continue
            selected.add(ident)
            pending.extend(self.nodes[ident]["required"])
            pending.extend([choices[ident]] if ident in choices else self.nodes[ident]["alternatives"])
        failures = [dict(self.nodes[ident]["error"], node=ident) for ident in sorted(selected) if self.nodes[ident].get("error")]
        if not all(ident in valid for ident in rootids) and not failures:
            failures = [dict(code="PROVENANCE_MISSING", message="Dependency cycle has no grounded witness", node=ident)
                        for ident in rootids if ident not in valid]
        graph = [dict(type="node", **{k: v for k, v in self.nodes[ident].items() if k not in ("required", "alternatives", "proofs", "revisions")})
                 for ident in sorted(selected)]
        graph += [dict(type="edge", id=key, **edge) for key, edge in sorted(self.edges.items())
                  if edge["source"] in selected and edge["target"] in selected
                  and (edge["kind"] != "restore/witness" or edge["source"] not in choices or choices[edge["source"]] == edge["target"])]
        graph += [dict(type="failure", **failure) for failure in failures]
        reads = {}
        for ident in sorted(selected):
            node = self.nodes[ident]
            if node["kind"] == "read":
                for path, revision in node["revisions"].items():
                    reads.setdefault(node["checkout"], {})[(path, revision)] = dict(path=path, revision=revision)
        return {"proofs": [proof for ident in sorted(selected) for proof in self.nodes[ident]["proofs"]],
                "reads": {key: sorted(values.values(), key=lambda r: (r["path"], r["revision"] or "")) for key, values in reads.items()},
                "tokens": self.evidence.tokens, "graph": graph, "digest": digest(graph), "failures": failures,
                "stats": {"revisions": len(self.evidence.revisions), "records": len(self.evidence.records),
                          "read_ranges": self.read_queries, "witness_queries": len(self.evidence.witnesses),
                          "thread": threading.get_ident()}}


def evaluate_sync(root, unit, rows, bases, read_blob):
    started = time.monotonic()
    db = archive.readonly(root)
    try:
        db.execute("BEGIN")
        result = Evaluator(Evidence(db, root), unit, bases, read_blob).evaluate(rows)
        result["stats"]["seconds"] = time.monotonic() - started
        return result
    finally:
        db.close()


def validate(store, result, heads=()):
    """No await between this check and the seal transaction's writes."""
    for ident, revision in result["tokens"].items():
        row = store.db.execute("SELECT revision FROM records WHERE id=?", (ident,)).fetchone()
        if (row[0] if row else None) != revision:
            raise CoordinatorError("REVISION_CHANGED", "Dependency authorization changed; repeat the preflight", {"record": ident})
    for row in heads:
        current = store.head(row["checkout"], row["path"])
        if not current or current["id"] != row["id"]:
            raise CoordinatorError("REVISION_CHANGED", "Frozen dependency output changed", {"revision": row["id"], "path": row["path"]})


def write_report(root, unit, result):
    # Page size bounds transport work, never the evidence retained or inspected.
    pages, page, size, paths = [], [], 0, set()
    def write(items):
        found = sorted({item["path"] for item in items if item.get("error") and item.get("path")})
        paths.update(found)
        return archive.write_object(root, {"items": items, "paths": found})
    for item in result["graph"]:
        length = len(encoded(item).encode())
        if page and size + length > 24000:
            pages.append(write(page))
            page, size = [], 0
        page.append(item)
        size += length
    if page:
        pages.append(write(page))
    priority = {"UNKNOWN_DEPENDENCY": 0, "UNPUBLISHED_DEPENDENCY": 1, "PROVENANCE_MISSING": 2}
    first = min(result["failures"], key=lambda error: priority.get(error["code"], 3))
    return dict(unit=unit, digest=result["digest"], total=len(result["graph"]), pages=pages,
                paths_total=len(paths), refusal=first)


async def report(store, unitid, reportid, cursor=None):
    manifest = store.get(reportid)
    if manifest.get("_kind") != "dependency_report" or manifest.get("unit") != unitid:
        raise CoordinatorError("PROVENANCE_MISSING", "Dependency report does not belong to this unit")
    index = 0
    if cursor is not None:
        try:
            identity, offset = cursor.rsplit(":", 1)
            index = int(offset)
            if identity != manifest["digest"] or index < 0 or index >= len(manifest["pages"]):
                raise ValueError()
        except (AttributeError, ValueError) as exc:
            raise CoordinatorError("DEPENDENCY_CURSOR", "Dependency report cursor is invalid") from exc
    page = await finish_thread(archive.read_object, store.root, manifest["pages"][index]) if manifest["pages"] else {"items": []}
    return dict(report=reportid, digest=manifest["digest"], total=manifest["total"], items=page["items"],
                paths=page.get("paths", []), paths_total=manifest.get("paths_total", 0),
                next_cursor=manifest["digest"] + ":" + str(index + 1) if index + 1 < len(manifest["pages"]) else None)


async def evaluate(store, unit, rows, bases):
    # Initialize the blob reader on its owner; its read method has no SQLite access.
    storage = store.blobs
    result = await finish_thread(evaluate_sync, store.root, unit, rows, bases, lambda key: storage.read(key)[0])
    if result["failures"]:
        manifest = await finish_thread(write_report, store.root, unit, result)
        ident = "dependency_report_" + digest([unit, result["digest"]])
        store.put("dependency_report", ident, manifest, unit)
        page = await report(store, unit, ident)
        first = manifest["refusal"]
        raise CoordinatorError(first["code"], first["message"], page)
    return result
