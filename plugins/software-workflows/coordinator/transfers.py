"""Publication rights follow authorized handbacks; revision authors never change.

Each path has one current holder. Transfers carry revision identities, not
unit dependency edges, so A -> B -> A is a sequence of grants, never a cycle.
Call mutators inside the custody transaction; release also holds lifecycle locks.
"""
import os
import time
from contextlib import nullcontext

from .client import CoordinatorError
from .ledger import key, overlaps
from .store import digest, uid


def identity(checkout, path):
    return "publication_right_" + digest([checkout, path])


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


def segment(store, unit, head, carried):
    """Unpublished effective ancestry; a restore replaces, rather than inherits, its previous bytes."""
    from types import SimpleNamespace
    from .publish import Publisher
    provenance = Publisher(SimpleNamespace(store=store))
    revisions, seen, row = set(), set(), head
    while row and row["id"] not in seen:
        seen.add(row["id"])
        if row["attribution"] == "baseline" or published(store, row, carried):
            break
        if row["attribution"] == "restore":
            source = provenance.provenance(row, unit)
            if source is None:
                break
            revisions.add(row["id"])
            if source["id"] != row["id"]:
                row = source
                continue
            # Legacy restoration has no source identity. Its byte-matched
            # effective source is authoritative; its overwritten history is not.
            if row["unit"]:
                revisions.add(row["id"])
            break
        if row["attribution"] == "UNKNOWN":
            resolutions = list(store.db.execute("SELECT s.restore,s.edit FROM unknowns u LEFT JOIN resolutions s "
                "ON s.unknown_id=u.id WHERE u.revision=?", (row["id"],)))
            if not resolutions or any(not r["edit"] for r in resolutions):
                raise CoordinatorError("UNKNOWN_BLOCKS_HANDBACK", "Resolve unexplained bytes before transferring provenance",
                                       {"path": row["path"], "revision": row["id"]})
            # Resolution is separate from the immutable UNKNOWN. Follow the
            # restoring operation's known source, never the UNKNOWN's previous
            # pointer and never attribute its old unexplained bytes to the lead.
            restored = store.get(resolutions[-1]["restore"])
            if restored.get("resolution") == "baseline" or restored.get("kind") == "outside-ledger":
                break
            source = restored.get("restored_from", {}).get(row["path"], "unrecorded")
            if source is None or source == "baseline":
                break
            found = store.db.execute("SELECT * FROM revisions WHERE id=?", (source,)).fetchone()
            if not found or found["checkout"] != row["checkout"] or found["path"] != row["path"]:
                raise CoordinatorError("HANDBACK_PROVENANCE", "Resolved UNKNOWN lacks a known effective source",
                                       {"path": row["path"], "revision": row["id"]})
            row = found
            continue
        if row["unit"]:
            revisions.add(row["id"])
        row = store.db.execute("SELECT * FROM revisions WHERE id=?", (row["previous"],)).fetchone() if row["previous"] else None
    return revisions


def release(store, unit, checkout, paths, authorizer, carried=None):
    """Revoke and save a pending grant for each exact path, in one transaction."""
    result = []
    carried = publications(store, checkout) if carried is None else carried
    for path in sorted(set(paths)):
        head = store.head(checkout, path)
        current = right(store, checkout, path, carried)
        if current and current.get("owner") not in (unit, None):
            raise CoordinatorError("PUBLICATION_RIGHTS_TRANSFERRED", "Another unit owns the path's publication rights", {"path": path})
        revisions = segment(store, unit, head, carried)
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


def acquire(store, actor, checkoutid, paths):
    checkout = store.get(checkoutid)
    result = []
    for current in store.all("publication_right", checkoutid):
        if current.get("state") == "retired" or current.get("owner") is not None or not any(overlaps(key(checkout, current["path"]), p["key"]) for p in paths):
            continue
        transfer = store.get(current["transfer"])
        transfer.update(to_unit=actor["unit"], acquired_by=actor["id"], state="transferred", acquired=time.time())
        store.save(transfer)
        current["owner"] = actor["unit"]
        current["state"] = "transferred"
        store.save(current)
        store.event("provenance/transferred", transfer["id"], {k: transfer[k] for k in
                    ("authorizer", "from_unit", "to_unit", "checkout", "path", "revisions", "previous_transfer")})
        result.append(transfer["id"])
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
