"""Unit membership sidecars; checkout ledgers remain global across projects."""
from .store import digest
from .client import CoordinatorError


def capture(store, unitid, checkout, snapshot, bindings, position):
    ident = "participant_" + digest([unitid, checkout["id"]])
    return store.put("participant", ident, {
        "unit": unitid, "checkout": checkout["id"], "root": checkout["root"],
        "identity": checkout["identity"], "repository": checkout["repository"],
        "branch": snapshot["branch"], "base": snapshot["base"],
        "bindings": bindings, "position": position}, unitid)


def members(store, unit):
    unit = store.get(unit) if isinstance(unit, str) else unit
    found = list(store.all("participant", unit["id"]))
    if found:
        return sorted(found, key=lambda p: (p["position"], p["checkout"]))
    # Also supports legacy units created during a rolling client transition.
    checkout = store.get(unit["checkout"])
    return [dict(checkout=checkout["id"], root=checkout["root"], identity=checkout["identity"],
                 repository=checkout["repository"], base=unit["base"], branch=unit["branch"],
                 bindings=[], position=0, unit=unit["id"])]


def checkout_ids(store, unit):
    return [p["checkout"] for p in members(store, unit)]


# Journal phases at which a participant's branch has the unit's commit, or may have it: the commit is
# recorded before the ref update, the ref update before its checkpoint, and a journal stops counting
# only once abandon() has proved it unapplied (or a reopen moved it into the prefix).
INSTALLED = ("ref_updated", "index_pending", "index_updated", "complete")
FENCED = ("commit_created",) + INSTALLED


def published_participant(store, unit, checkout):
    """This participant's published part of the unit, or None. Its branch has the unit's commit once a
    reopen recorded it in the prefix, or, before any reopen, once the unit's current publication is past
    creating it (a partial publication still attached to its lead session). At `commit_created` the ref
    update may or may not have happened: `uncertain` says so."""
    unit = store.get(unit) if isinstance(unit, str) else unit
    if checkout in unit.get("published_prefix", {}):
        return {"root": store.get(checkout)["root"], "checkout": checkout,
                "commit": unit["published_prefix"][checkout]["commit"]}
    groups = [g for g in store.all("publication_group", unit["id"]) if g["phase"] not in ("superseded", "abandoned")]
    if groups:  # the latest, as surface() reads it
        journal = store.get(groups[-1]["journals"].get(checkout), required=False)
    else:  # one repository's journal, without a group
        journal = next((p for p in store.all("publication", unit["id"]) if p["phase"] != "abandoned"
                        and p.get("checkout", unit.get("checkout")) == checkout), None)
    if journal and journal["phase"] in FENCED and journal.get("commit"):
        return {"root": store.get(checkout)["root"], "checkout": checkout, "commit": journal["commit"],
                **({"uncertain": True} if journal["phase"] not in INSTALLED else {})}
    return None


def require_writable(store, unit, checkout):
    published = published_participant(store, unit, checkout)
    if published and published.get("uncertain"):
        raise CoordinatorError(
            "PREFIX_PUBLISHED", "This participant is being published: its branch may already have the unit's "
            "commit. Let the publication finish, or recover or reopen the unit, first", published)
    if published:
        raise CoordinatorError(
            "PREFIX_PUBLISHED", "This participant is already published; new edits require a new unit",
            published)


def contains(store, unit, checkout):
    unitid = unit if isinstance(unit, str) else unit["id"]
    if store.get("participant_" + digest([unitid, checkout]), required=False):
        return True
    unit = store.get(unit) if isinstance(unit, str) else unit
    return unit["checkout"] == checkout


def at_checkout(store, checkout):
    return store.selected("SELECT u.* FROM records p JOIN records u ON u.id=p.parent "
                          "WHERE p.kind='participant' AND json_extract(p.data,'$.checkout')=? ORDER BY u.id", (checkout,))


def roots(store, unit):
    unit = store.get(unit) if isinstance(unit, str) else unit
    if unit.get("project_revision"):
        return [b["root"] for b in store.get(unit["project_revision"])["bindings"]]
    return [p["root"] for p in members(store, unit)]


def project(store, unit):
    unit = store.get(unit) if isinstance(unit, str) else unit
    if not unit.get("project_revision"):
        return None
    revision = store.get(unit["project_revision"])
    return {"id": revision["project"], "name": revision["name"]}


def surface(daemon, unit, publication_only=False):
    store = daemon.store
    unit = store.get(unit) if isinstance(unit, str) else unit
    groups = [g for g in store.all("publication_group", unit["id"]) if g["phase"] not in ("superseded", "abandoned")]
    journals = [p for p in store.all("publication", unit["id"]) if p["phase"] != "abandoned"]
    group = groups[-1] if groups else None
    data, membership = [], members(store, unit)
    for member in membership:
        journal = store.get(group["journals"].get(member["checkout"]), required=False) if group else next(
            (p for p in journals if p.get("checkout", unit["checkout"]) == member["checkout"]), None)
        data.append(dict(root=member["root"], checkout=member["checkout"], branch=member["branch"], base=member["base"],
                         **({} if publication_only else dict(
                             claims=daemon.custody.claims(member["checkout"], unit["id"]),
                             unknown_count=len(store.unresolved(member["checkout"])))), phase=journal["phase"] if journal else "",
                         **({"commit": journal["commit"]} if journal and journal.get("commit") else {})))
    state = group["publication_state"] if group else unit.get("publication_state", "")
    if not state and journals:
        state = "complete" if all(p["phase"] == "complete" for p in journals) else "prepared"
    if publication_only:
        return {"participants": data, "publication_state": state}
    # Pollers must observe changes in member progress even when run metadata is idle.
    revision = sum(r.get("_revision", 0) for r in [unit, *membership, *groups, *journals])
    # Every command leaves a custody record: sum their revisions without decoding them.
    revision += store.db.execute("SELECT coalesce(sum(revision),0) FROM records WHERE parent=? AND kind IN ('claim','custody')",
                                 (unit["id"],)).fetchone()[0]
    for member in membership:
        revision += store.db.execute("SELECT count(*) + coalesce(sum(s.restore IS NOT NULL),0) + "
            "coalesce(sum(s.edit IS NOT NULL),0) FROM unknowns u LEFT JOIN resolutions s ON s.unknown_id=u.id "
            "WHERE u.checkout=?", (member["checkout"],)).fetchone()[0]
    return {"project": project(store, unit), "participants": data, "publication_state": state, "participant_revision": revision}
