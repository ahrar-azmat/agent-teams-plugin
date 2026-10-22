"""Read-only registered-checkout lookup; no Git commands or cache writes."""
import os
from pathlib import Path

from .store import digest
from . import participants


def identity_changed(checkout, work=None):
    root = Path(checkout["root"])
    work = work or root.stat()
    gitdir = os.stat(checkout["git_directory"])
    common = os.stat(checkout["git_common"])
    identity = digest([[work.st_dev, work.st_ino], [gitdir.st_dev, gitdir.st_ino]])
    repository = digest([checkout["git_common"], common.st_dev, common.st_ino])
    entry = root / ".git"
    if entry.is_dir():
        resolved = str(entry.resolve())
    else:
        value = entry.read_text().strip()
        resolved = str((root / value.removeprefix("gitdir: ")).resolve()) if value.startswith("gitdir: ") else ""
    return (identity != checkout["identity"] or repository != checkout["repository"] or
            resolved != str(Path(checkout["git_directory"]).resolve()))


def checkout_at(store, cwd):
    target = Path(cwd).resolve()
    ancestors = set()
    for path in (target, *target.parents):
        try:
            info = path.stat()
            ancestors.add((info.st_dev, info.st_ino))
        except OSError:
            pass
    for checkout in sorted(store.all("checkout"), key=lambda c: len(Path(c["root"]).parts), reverse=True):
        root = Path(checkout["root"])
        try:
            work = root.stat()
            belongs = (work.st_dev, work.st_ino) in ancestors or root == target or root in target.parents
            if not belongs:
                continue
            # The .git entry itself must still resolve to the registered gitdir.
            return checkout, identity_changed(checkout, work)
        except OSError:
            if root == target or root in target.parents:
                return checkout, True
    return None, False


def repository_checkouts(store, git_common):
    """The registered checkouts of the repository whose common git directory is `git_common`: the same
    directory each recorded at registration (ledger._metadata's `repository`), whatever folder or path
    git reached it by. A linked worktree, a separate git directory and a --git-dir invocation share it;
    a nested repository or a re-clone in place does not."""
    try:
        info = os.stat(git_common)
    except (OSError, TypeError, ValueError):
        return []
    return [c for c in store.all("checkout")
            if c.get("repository") == digest([c.get("git_common"), info.st_dev, info.st_ino])]


def publications(daemon, checkouts):
    """The unfinished publications of units with a participant among `checkouts` (ids)."""
    found = []
    # Publication history is not the query workload. Use the partial index
    # to select only unfinished journals before inspecting participants.
    for row in daemon.store.db.execute("SELECT parent AS id FROM records INDEXED BY open_publication "
            "WHERE kind='publication' AND json_extract(data,'$.phase') NOT IN ('complete','abandoned') UNION "
            "SELECT id FROM records INDEXED BY unfinished_unit_v2 WHERE kind='unit' AND "
            "json_extract(data,'$.publication_state') IN ('prepared','publishing','partially_published')"):
        unit = daemon.store.get(row["id"])
        if not any(participants.contains(daemon.store, unit, key) for key in checkouts):
            continue
        surface = participants.surface(daemon, unit, publication_only=True)
        if surface["publication_state"] and surface["publication_state"] != "complete":
            found.append({"unit": unit["id"], "phase": unit["state"], "state": surface["publication_state"],
                "participants": [{k: p[k] for k in ("root", "phase", "commit") if k in p} for p in surface["participants"]]})
    return found


def query(daemon, cwd, session_id, git_common=None):
    """What the coordinator knows of the checkout at `cwd`. With `git_common` (an absolute common git
    directory, as git resolved it), `publications` covers the registered checkouts of that repository,
    and only those, and `repository` lists their roots: the refs a push publishes are the repository's,
    not a folder's."""
    from .lead import own_unit
    checkout, changed = checkout_at(daemon.store, cwd)
    result = {"available": True, "fresh": daemon.ready, "generation": daemon.store.generation,
              "status": "identity_changed" if changed else "coordinated" if checkout else "uncoordinated",
              "registered": checkout is not None, "checkout": checkout and checkout["id"],
              "units": [], "unknown": [], "own_unit": None, "root": checkout and checkout["root"], "projects": [], "publications": []}
    members = [checkout["id"]] if checkout and not changed else []
    if git_common is not None:
        same = repository_checkouts(daemon.store, git_common)
        result["repository"] = {"roots": sorted(c["root"] for c in same)}
        members = [c["id"] for c in same]
    if members:
        result["publications"] = publications(daemon, members)
    # Fresh once startup recovery is done with what this answer covers: its checkouts and their publications' units.
    result["fresh"] = daemon.ready and not any(scope in daemon.recovering for scope in
                                               [("checkout", key) for key in members] + [("unit", p["unit"]) for p in result["publications"]])
    if checkout and not changed:
        projects = {}
        for revision in daemon.store.all("project_revision"):
            if checkout["id"] in {b["checkout"] for b in revision["bindings"]}:
                projects[revision["project"]] = {"id": revision["project"], "name": revision["name"]}
        result["projects"] = list(projects.values())
        claims = daemon.custody.claims(checkout["id"])
        scopes = {}
        for claim in claims:
            scopes.setdefault(claim["unit"], []).append(claim["path"])
        result["units"] = [{"unit": u["id"], "run": u["run"], "state": u["state"],
                            "paths": scopes.get(u["id"], [])}
                           for u in participants.at_checkout(daemon.store, checkout["id"]) if u["state"] != "published"]
        result["unknown"] = daemon.store.unresolved(checkout["id"])
        result["own_unit"] = own_unit(daemon.store, checkout, session_id)
        result["case_sensitive"] = bool(checkout.get("case_sensitive"))  # how its paths compare (ledger.normalize)
    return result
