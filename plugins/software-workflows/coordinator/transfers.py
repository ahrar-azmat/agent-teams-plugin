"""Publication rights follow authorized handbacks; revision authors never change.

Each path has one current holder. Transfers carry revision identities, not
unit dependency edges, so A -> B -> A is a sequence of grants, never a cycle.
Call mutators inside the custody transaction; release also holds lifecycle locks.
"""
import os
import time
from contextlib import nullcontext

from .client import CoordinatorError
from .ledger import key
from .store import digest, uid


def identity(checkout, path):
    return "publication_right_" + digest([checkout, path])


def covers(claim_key, path_key):
    """A descendant claim cannot carry its ancestor's publication right."""
    return path_key == claim_key or path_key.startswith(claim_key + "/")


def never_produced(run):
    # A bootstrap thread can exist before admission; a missing old flag proves nothing.
    return run.get("mode") == "native" and run.get("production_started") is False


def consume(store, part, completion):
    """Index the exact grants in a validated part, including pin-only parts.

    Called in the completion transaction. Repository prefixes carry receipts
    immediately, but group grants survive until the whole group completes.
    """
    checkout = part.get("checkout") or store.get(part["unit"])["checkout"]
    for grant in part.get("transfer_proofs", []):
        if grant["path"] not in part["paths"]:
            raise CoordinatorError("PUBLICATION_PROOF", "Grant is outside the completed part", {"seal": part["id"]})
        ident = "consumed_transfer_" + grant["transfer"]
        value = dict(grant, checkout=checkout, owner=part["unit"], completion=completion, seal=part["id"])
        prior = store.get(ident, required=False)
        if prior is None:
            store.put("consumed_transfer", ident, value, grant["transfer"])


def record_publication(store, journal):
    if journal["phase"] != "complete":
        return
    part = store.get(journal["seal"])
    checkout = part.get("checkout") or store.get(part["unit"])["checkout"]
    for row in part.get("rows", []):
        entry = part.get("entries", {}).get(row["path"])
        if (row["checkout"] == checkout and row["path"] in part["paths"] and entry
                and entry["mode"] == row["mode"] and bool(row["blob"]) == bool(set(entry["oid"]) - {"0"})):
            store.put("published_revision", "published_revision_" + row["id"],
                      {"row": row, "publication": journal["id"], "seal": part["id"]}, checkout)
    if not journal.get("group"):
        consume(store, part, journal["id"])


def record_group(store, group):
    if group["phase"] != "complete":
        return
    seal = store.get(group["seal"])
    for item in seal["vector"]:
        if not item["unchanged"]:
            journal = store.get(group["journals"][item["checkout"]])
            if journal["phase"] != "complete":
                raise CoordinatorError("PUBLICATION_PROOF", "Group has an incomplete participant", {"group": group["id"]})
        consume(store, store.get(item["seal"]), group["id"])


def initialize(store):
    """Backfill old completed evidence once; no history scans on later lookups.

    The durable marker and evidence commit together, so an interrupted upgrade
    can retry. Startup calls this before readiness; standalone callers are safe
    too. No schema migration or rewrite of revision authorship is required.
    """
    if store.get("publication_evidence_v1", required=False):
        return
    with nullcontext() if store.db.in_transaction else store.transaction():
        for journal in store.all("publication"):
            record_publication(store, journal)
        for group in store.all("publication_group"):
            record_group(store, group)
        store.put("publication_evidence", "publication_evidence_v1", {"version": 1, "created": time.time()})


class Lookup:
    """One operation's lazy, primary-key lookups; never a global history map."""
    def __init__(self, store):
        if getattr(store, "readonly", False):
            if not store.get("publication_evidence_v1", required=False):
                raise CoordinatorError("PROVENANCE_MISSING", "Publication evidence has not been initialized")
        else:
            initialize(store)
        self.store, self.receipts, self.rights = store, {}, {}

    def get(self, revision):
        if revision not in self.receipts:
            self.receipts[revision] = self.store.get("published_revision_" + revision, required=False)
        return self.receipts[revision]


def publications(store, checkout=None):
    return Lookup(store)


def published(store, row, carried=None):
    carried = publications(store, row["checkout"]) if carried is None else carried
    proof = carried.get(row["id"])
    return bool(proof and dict(row) == proof["row"])


def right(store, checkout, path, carried=None):
    carried = publications(store) if carried is None else carried
    cachekey = (checkout, path)
    if cachekey in carried.rights:
        return carried.rights[cachekey]
    current = store.get(identity(checkout, path), required=False)
    if current and current.get("state") == "retired":
        current = None
    if current:
        consumed = store.get("consumed_transfer_" + current["transfer"], required=False)
        if consumed and all(consumed[k] == current[k] for k in ("checkout", "path", "owner", "transfer", "revisions")):
            current = None
    carried.rights[cachekey] = current
    return current


def owned(store, unit, checkouts):
    carried = publications(store)
    return [current for checkout in checkouts for current in store.all("publication_right", checkout)
            if current.get("owner") == unit and right(store, checkout, current["path"], carried)]


def transfer_proof(store, current):
    """Fail closed before changing either half of a live grant."""
    transfer = store.get(current.get("transfer"), required=False)
    state = "pending" if current.get("owner") is None else "transferred"
    if (not transfer or transfer.get("_kind") != "provenance_transfer"
            or current["id"] != identity(current["checkout"], current["path"])
            or not current.get("revisions")
            or any(transfer.get(k) != current.get(k) for k in ("checkout", "path", "revisions"))
            or transfer.get("to_unit") != current.get("owner")
            or transfer.get("state") != state or current.get("state") != state):
        raise CoordinatorError("PUBLICATION_RIGHT_PROOF", "Publication right and transfer do not agree",
                               {"right": current["id"], "transfer": current.get("transfer")})
    return transfer


def return_acquisition(store, current, unit, run, reason):
    """Undo one acquisition in the caller's transaction, without adding a provenance hop."""
    current = right(store, current["checkout"], current["path"])
    if not current or current.get("owner") != unit:
        return None
    transfer = transfer_proof(store, current)
    transfer.update(to_unit=None, state="pending")
    transfer.pop("acquired", None)
    transfer.pop("acquired_by", None)
    current.update(owner=None, state="pending")
    store.save(transfer)
    store.save(current)
    store.event("provenance/acquisition_returned", transfer["id"],
                {"run": run, "previous_owner": unit, "reason": reason, "right": current["id"],
                 "checkout": current["checkout"], "path": current["path"], "revisions": current["revisions"]})
    return transfer["id"]


def return_acquisitions(store, unit, run, checkouts, reason):
    returned = [return_acquisition(store, current, unit, run["id"], reason)
                for current in owned(store, unit, checkouts)]
    returned = [ident for ident in returned if ident]
    run["acquisitions_returned"] = run.get("acquisitions_returned", 0) + len(returned)
    store.save(run)
    return returned


def repair(store):
    """Exhaust indexed right pages, without reading event history or writing an empty scan.

    Startup runs this after publication evidence is initialized, before admitting callers.
    Each return and its durable total commit together. A crash leaves a pending grant for
    its covering claimant; subsequent starts neither return it twice nor change the total.
    """
    started, scanned, returned, after = time.monotonic(), 0, 0, ""
    total = store.get("publication_right_repair_v1", required=False)
    while True:
        page = list(store.selected("SELECT * FROM records INDEXED BY record_kind WHERE kind=? AND id>? ORDER BY id LIMIT ?",
                                   ("publication_right", after, 256)))
        if not page:
            break
        after = page[-1]["id"]
        for current in page:
            scanned += 1
            if not current.get("owner") or not right(store, current["checkout"], current["path"]):
                continue
            unit = store.get(current["owner"])
            run = store.get(unit["run"])
            if not never_produced(run):
                continue
            checkout = store.get(current["checkout"])
            path_key = key(checkout, current["path"])
            if any(c.get("state") == "held" and c["checkout"] == checkout["id"] and covers(c["key"], path_key)
                   for c in store.all("claim", unit["id"])):
                continue
            with store.transaction():
                ident = return_acquisition(store, current, unit["id"], run["id"], "startup: never produced and no covering claim")
                if ident:
                    run["acquisitions_returned"] = run.get("acquisitions_returned", 0) + 1
                    store.save(run)
                    count = (total or {}).get("total_returned", 0) + 1
                    total = store.put("publication_right_repair", "publication_right_repair_v1", {"total_returned": count})
                    returned += 1
    return {"scanned": scanned, "returned": returned, "total_returned": (total or {}).get("total_returned", 0),
            "seconds": time.monotonic() - started}


def proof(store, unit, row, carried=None):
    current = right(store, row["checkout"], row["path"], carried)
    if current and current.get("owner") == unit and row["id"] in current["revisions"]:
        return {"transfer": current["transfer"], "revision": row["id"], "checkout": row["checkout"],
                "path": row["path"], "author_unit": row["unit"], "publication_unit": unit}
    return None


def scope(ledger, unit, checkout, carried=None):
    paths = set(ledger.mutation_scope(unit, checkout))
    carried = publications(ledger.store) if carried is None else carried
    for current in ledger.store.all("publication_right", checkout):
        if not right(ledger.store, checkout, current["path"], carried):
            continue
        if current.get("owner") == unit:
            paths.add(current["path"])
        elif current["path"] in paths:
            paths.remove(current["path"])
    return sorted(paths)


def publishable(ledger, unit):
    """Whether a seal of the unit has anything to carry in any participant checkout: scope() above (receipts of
    its patches, lead edits and restores, and the live rights it owns), or a pending live right its held claims
    cover, which its seal would acquire."""
    from .participants import checkout_ids
    store, carried = ledger.store, publications(ledger.store)
    held = [c for c in store.all("claim", unit["id"]) if c["state"] == "held"]
    for checkoutid in checkout_ids(store, unit):
        if scope(ledger, unit["id"], checkoutid, carried):
            return True
        keys = [c["key"] for c in held if c["checkout"] == checkoutid]
        if keys:
            checkout = store.get(checkoutid)
            if any(current.get("owner") is None and right(store, checkoutid, current["path"], carried)
                   and any(covers(k, key(checkout, current["path"])) for k in keys)
                   for current in store.all("publication_right", checkoutid)):
                return True
    return False


def require_nonempty(store, unit, paths):
    if not paths:
        handed = [r for r in store.all("provenance_transfer", unit) if r["from_unit"] == unit]
        code = "PUBLICATION_RIGHTS_TRANSFERRED" if handed else "UNIT_EMPTY"
        raise CoordinatorError(code, "Unit has no retained publication scope", {"unit": unit})


def snapshot(store, unit, checkout, paths, carried=None):
    carried = publications(store) if carried is None else carried
    result = []
    for path in paths:
        current = right(store, checkout, path, carried)
        if current and current.get("owner") != unit:
            raise CoordinatorError("PUBLICATION_RIGHTS_TRANSFERRED", "Publication rights were handed back",
                                   {"unit": unit, "checkout": checkout, "path": path, "transfer": current["transfer"], "owner": current.get("owner")})
        if current:
            result.append({"path": path, "transfer": current["transfer"], "revisions": current["revisions"]})
    return result


def check_scope(store, unit, checkout, paths, carried=None):
    snapshot(store, unit, checkout, paths, carried)


def validate_seal(store, seal):
    carried = publications(store)
    unit = store.get(seal["unit"])
    parts = [store.get(item["seal"]) for item in seal["vector"]] if seal.get("binding_version") == 2 else [seal]
    for part in parts:
        checkout = part.get("checkout", unit["checkout"])
        if snapshot(store, unit["id"], checkout, part["paths"], carried) != part.get("transfer_proofs", []):
            raise CoordinatorError("PUBLICATION_RIGHTS_TRANSFERRED", "Seal predates a handback or acquisition", {"seal": seal["seal"], "checkout": checkout})


def segment(evidence, unit, head, carried, read_blob):
    """Unpublished effective ancestry; a restore replaces, rather than inherits, its previous bytes. Reads one snapshot
    (segments())."""
    from .dependencies import Evaluator
    revisions, seen, row = set(), set(), head
    while row and row["id"] not in seen:
        seen.add(row["id"])
        if row["attribution"] == "baseline" or (row["attribution"] != "UNKNOWN" and published(evidence, row, carried)):
            break
        if row["attribution"] == "restore":
            evaluator = Evaluator(evidence, unit, {}, read_blob)
            result = evaluator.evaluate([dict(row)])
            if result["failures"]:
                failure = result["failures"][0]
                raise CoordinatorError("UNKNOWN_BLOCKS_HANDBACK" if failure["code"] == "UNKNOWN_DEPENDENCY" else "HANDBACK_PROVENANCE",
                                       failure["message"], {"path": row["path"], "revision": row["id"], "failure": failure})
            # Reads validate bytes but do not convey ownership, even on this path.
            nodes = {value["id"]: value for value in result["graph"] if value["type"] == "node"}
            sources = {value["source"]: value["target"] for value in result["graph"]
                       if value["type"] == "edge" and value["kind"] in ("restore/source", "restore/witness")}
            source, restored = row["id"], set()
            while source and nodes[source].get("attribution") == "restore":
                restored.add(nodes[source]["revision"])
                source = sources.get(source)
            if not source or nodes[source].get("attribution") == "baseline":
                break
            revisions.update(restored)
            row = evidence.revision(nodes[source]["revision"])
            continue
        if row["attribution"] == "UNKNOWN":
            resolutions = [dict(value) for value in evidence.db.execute("SELECT u.id AS unknown,s.restore,s.edit FROM unknowns u "
                "LEFT JOIN resolutions s ON s.unknown_id=u.id WHERE u.revision=? ORDER BY u.id", (row["id"],))]
            raise CoordinatorError("UNKNOWN_BLOCKS_HANDBACK", "Handback reaches unexplained bytes",
                                   {"path": row["path"], "revision": row["id"], "unknowns": resolutions})
        if row["unit"]:
            revisions.add(row["id"])
        row = evidence.revision(row["previous"]) if row["previous"] else None
    if row and row["id"] in seen and row["attribution"] not in ("baseline", "restore") and not published(evidence, row, carried):
        raise CoordinatorError("HANDBACK_PROVENANCE", "Dependency cycle has no grounded witness", {"revision": row["id"]})
    return revisions


def segments(root, unit, wanted, read_blob):
    """segment() of each wanted path's head ({checkout: paths}), on one read snapshot off the event loop: a restore's
    walk reads its dependency graph (2.4.36), too much for the handback transaction. A path's refusal is kept, as plain
    data, for release() to raise in that path's turn: the exception's traceback would keep that path's whole graph
    alive. Returns the walks and the records they read: the caller validates those (dependencies.validate) and
    release() each head, inside the handback transaction."""
    from . import archive
    from .dependencies import Evidence
    db = archive.readonly(root)
    try:
        db.execute("BEGIN")
        evidence = Evidence(db, root)
        carried, walked = Lookup(evidence), {}
        for checkout, paths in wanted.items():
            for path in sorted(paths):
                head = db.execute("SELECT r.* FROM revisions r JOIN heads h ON h.revision=r.id WHERE h.checkout=? AND h.path=?",
                                  (checkout, path)).fetchone()
                head = dict(head) if head else None
                try:
                    walked[(checkout, path)] = (head and head["id"], segment(evidence, unit, head, carried, read_blob), None)
                except CoordinatorError as exc:
                    walked[(checkout, path)] = (head and head["id"], None, (exc.code, str(exc), exc.details, exc.operational))
        return walked, dict(evidence.tokens)
    finally:
        db.close()


def release(store, unit, checkout, paths, authorizer, walked, carried=None):
    """Revoke and save a pending grant for each exact path, in one transaction. `walked` is segments() of these paths:
    a head that moved since refuses the handback (HANDBACK_CHANGED)."""
    result = []
    carried = publications(store, checkout) if carried is None else carried
    for path in sorted(set(paths)):
        head = store.head(checkout, path)
        current = right(store, checkout, path, carried)
        if current and current.get("owner") not in (unit, None):
            raise CoordinatorError("PUBLICATION_RIGHTS_TRANSFERRED", "Another unit owns the path's publication rights", {"path": path})
        if current:
            transfer_proof(store, current)
            if not head:
                raise CoordinatorError("HANDBACK_PROVENANCE", "Live publication right has no recorded head", {"path": path})
        found = walked.get((checkout, path))
        if not found or found[0] != (head["id"] if head else None):
            raise CoordinatorError("HANDBACK_CHANGED", "Claims, rights or revisions changed during handback preparation; retry", {"path": path})
        if found[2]:
            raise CoordinatorError(*found[2])
        revisions = found[1]
        # A known restore to baseline legitimately retires the previous grant.
        # Missing heads and inconsistent grant records were refused above.
        if not revisions and not current:
            continue
        prior = store.get(identity(checkout, path), required=False)
        ident = uid("transfer")
        transfer = {"authorizer": authorizer, "from_unit": unit, "to_unit": None, "checkout": checkout,
                    "path": path, "revisions": sorted(revisions), "head": head["id"] if head else None,
                    "previous_transfer": prior["transfer"] if prior else None,
                    "state": "pending" if revisions else "retired", "created": time.time()}
        store.put("provenance_transfer", ident, transfer, unit)
        value = {"checkout": checkout, "path": path, "owner": None, "transfer": ident, "revisions": sorted(revisions),
                 "state": "pending" if revisions else "retired"}
        if prior:
            prior.update(value)
            store.save(prior)
        else:
            store.put("publication_right", identity(checkout, path), value, checkout)
        store.event("provenance/handback", ident, transfer)
        result.append(ident)
    # A dispatched answer for the old binding must never revive it afterwards.
    for review in store.all("review", unit):
        review["valid"] = False
        store.save(review)
    return result


def grant(store, actor, current):
    transfer = transfer_proof(store, current)
    if current.get("owner") is not None:
        raise CoordinatorError("PUBLICATION_RIGHTS_TRANSFERRED", "Only pending publication rights can be acquired",
                               {"right": current["id"], "owner": current["owner"]})
    transfer.update(to_unit=actor["unit"], acquired_by=actor["id"], state="transferred", acquired=time.time())
    store.save(transfer)
    current.update(owner=actor["unit"], state="transferred")
    store.save(current)
    store.event("provenance/transferred", transfer["id"], {k: transfer[k] for k in
                ("authorizer", "from_unit", "to_unit", "checkout", "path", "revisions", "previous_transfer")})
    return transfer["id"]


def acquire(store, actor, checkoutid, paths):
    checkout = store.get(checkoutid)
    result = []
    carried = publications(store)
    for current in store.all("publication_right", checkoutid):
        if (current.get("owner") is not None or not right(store, checkoutid, current["path"], carried)
                or not any(covers(p["key"], key(checkout, current["path"])) for p in paths)):
            continue
        result.append(grant(store, actor, current))
    return result


def authorizer(envelope):
    if isinstance(envelope, dict):
        if envelope.get("handback_version") != 1 or set(envelope) != {"handback_version", "paths", "authorizer"}:
            raise CoordinatorError("CLAIM_PATHS", "Unsupported internal handback envelope")
        who = envelope["authorizer"]
        if not isinstance(who, dict) or who.get("kind") != "lead" or not isinstance(who.get("session_id", ""), str):
            raise CoordinatorError("HANDBACK_AUTHORIZER", "Handback must identify the lead authorizer")
        return envelope["paths"], who
    # Legacy local lead-control calls have no session identity. Record exactly
    # what the authenticated local control channel establishes, not an author.
    return envelope, {"kind": "local-lead-control", "uid": os.getuid() if hasattr(os, "getuid") else None}
