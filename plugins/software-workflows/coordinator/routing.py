"""One physical-checkout router for native, lead and dynamic tool paths."""
import os
from pathlib import Path

from .client import CoordinatorError
from .ledger import normalize
from .participants import members


def route(store, actor, value, cwd=None, directory=False):
    participants = members(store, actor["unit"])
    allowed = {p["checkout"] for p in participants}
    explicit = None
    if isinstance(value, dict):
        if set(value) != {"root_id", "path"} or not isinstance(value["root_id"], str):
            raise CoordinatorError("PATH_FIELDS", "Qualified paths require root_id and repository-relative path")
        explicit, value = value["root_id"], value["path"]
        checkout = store.get(explicit)
        if checkout["_kind"] != "checkout" or not isinstance(value, str) or os.path.isabs(value):
            raise CoordinatorError("PATH_FIELDS", "root_id must name a checkout and path must be repository-relative")
        # Reject escape before selecting the actual inner boundary.
        normalize(checkout, value)
        cwd = checkout["root"]
    if not isinstance(value, str) or not value:
        raise CoordinatorError("PATH_ENCODING", "Path must be a nonempty string or a qualified path")
    cwd = cwd or actor.get("cwd") or store.get(actor["checkout"])["root"]
    target = Path(os.path.abspath(os.path.join(cwd, value)))
    # Native roots can name aliases. Translate only a pinned root binding;
    # normalize still refuses symlinks within the repository-relative path.
    bindings = [b for p in participants for b in p.get("bindings", [])
                if Path(b["root"]) == target or Path(b["root"]) in target.parents]
    if bindings and not explicit:
        selected = max(bindings, key=lambda b: len(Path(b["root"]).parts))
        if Path(selected["root"]).resolve() != Path(selected["physical_root"]):
            raise CoordinatorError("PROJECT_ROOT_CHANGED", "Native root alias changed", {"root": selected["root"]})
        target = Path(selected["physical_root"]) / target.relative_to(selected["root"])
    candidates = [c for c in store.all("checkout") if Path(c["root"]) == target or Path(c["root"]) in target.parents]
    if not candidates:
        raise CoordinatorError("PATH_ESCAPE", "Path is outside this unit's participants",
                               {"path": str(target), "participants": [p["root"] for p in participants]})
    owner = max(candidates, key=lambda c: len(Path(c["root"]).parts))
    for ancestor in (target, *target.parents):
        if ancestor == Path(owner["root"]):
            break
        if os.path.lexists(ancestor / ".git"):
            raise CoordinatorError("PATH_UNREGISTERED_CHECKOUT", "Path enters an unregistered nested Git boundary",
                                   {"path": str(target), "root": str(ancestor), "outer_root": owner["root"]})
    if explicit and owner["id"] != explicit:
        raise CoordinatorError("PATH_ROOT_MISMATCH", "Explicit root does not own this path; use the inner checkout",
                               {"root": owner["root"], "checkout": owner["id"], "path": str(target)})
    if owner["id"] not in allowed:
        raise CoordinatorError("PATH_ESCAPE", "Path belongs to a registered checkout outside this unit",
                               {"root": owner["root"], "checkout": owner["id"], "participants": [p["root"] for p in participants]})
    normalized = {"path": "", "key": ""} if directory and target == Path(owner["root"]) else normalize(owner, str(target))
    return dict(normalized, checkout=owner["id"], root=owner["root"])


def grouped(store, actor, paths, cwd=None):
    if not isinstance(paths, list):
        raise CoordinatorError("CLAIM_PATHS", "Paths must be a list")
    result = {}
    for path in paths:
        routed = route(store, actor, path, cwd)
        result.setdefault(routed["checkout"], []).append(routed["path"])
    return result


def qualified(checkout, path):
    return {"root_id": checkout, "path": path}
