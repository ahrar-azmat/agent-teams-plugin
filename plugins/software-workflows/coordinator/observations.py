"""Read-only registered-checkout lookup; no Git commands or cache writes."""
import os
from pathlib import Path

from .store import digest


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
            gitdir = os.stat(checkout["git_directory"])
            common = os.stat(checkout["git_common"])
            identity = digest([[work.st_dev, work.st_ino], [gitdir.st_dev, gitdir.st_ino]])
            repository = digest([checkout["git_common"], common.st_dev, common.st_ino])
            # The .git entry itself must still resolve to the registered gitdir.
            entry = root / ".git"
            if entry.is_dir():
                resolved = str(entry.resolve())
            else:
                value = entry.read_text().strip()
                resolved = str((root / value.removeprefix("gitdir: ")).resolve()) if value.startswith("gitdir: ") else ""
            changed = identity != checkout["identity"] or repository != checkout["repository"] or resolved != str(Path(checkout["git_directory"]).resolve())
            return checkout, changed
        except OSError:
            if root == target or root in target.parents:
                return checkout, True
    return None, False


def query(daemon, cwd, session_id):
    from .lead import own_unit
    checkout, changed = checkout_at(daemon.store, cwd)
    result = {"available": True, "fresh": daemon.ready, "generation": daemon.store.generation,
              "status": "identity_changed" if changed else "coordinated" if checkout else "uncoordinated",
              "registered": checkout is not None, "checkout": checkout and checkout["id"],
              "units": [], "unknown": [], "own_unit": None}
    if checkout and not changed:
        claims = daemon.custody.claims(checkout["id"])
        result["units"] = [{"unit": u["id"], "run": u["run"], "state": u["state"],
                            "paths": [c["path"] for c in claims if c["unit"] == u["id"]]}
                           for u in daemon.store.all("unit", checkout["id"]) if u["state"] != "published"]
        result["unknown"] = daemon.store.unresolved(checkout["id"])
        result["own_unit"] = own_unit(daemon.store, checkout, session_id)
        result["case_sensitive"] = bool(checkout.get("case_sensitive"))  # how its paths compare (ledger.normalize)
    return result
