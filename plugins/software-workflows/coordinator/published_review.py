"""Python 3.9 read-only publication proof lookup. Never opens Store or a daemon."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time


def lookup(commit, tree, branch, state=None):
    """Return evidence only for the exact published commit and destination ref.

    The record_kind index excludes unrelated operation/event history. A time
    budget limits gate latency; an unavailable proof retains the normal gate.
    Object identity works in a clean clone without trusting its filesystem ID.
    """
    root = Path(state or os.environ.get("SOFTWARE_WORKFLOWS_STATE") or
                Path.home() / ".local" / "state" / "software-workflows")
    database = root / "ledger.sqlite"
    connection = None
    try:
        for path in (root, database):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022 or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
                return None
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.2)
        deadline = time.monotonic() + 1.0
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        def get(ident, kind):
            row = connection.execute("SELECT data FROM records WHERE id=? AND kind=?", (ident, kind)).fetchone()
            return json.loads(row[0]) if row else {}
        for ident, raw in connection.execute("SELECT id,data FROM records INDEXED BY record_kind WHERE kind='publication' "
                "AND json_extract(data,'$.phase')='complete' AND json_extract(data,'$.commit')=?", (commit,)):
            publication = json.loads(raw)
            group = None
            if publication.get("group"):
                # Recovery reuses completed prefix journals. Their original
                # group/review are superseded; the completing group's full
                # vector and new passed review cover those exact receipts.
                for (group_raw,) in connection.execute("SELECT data FROM records INDEXED BY record_kind "
                        "WHERE kind='publication_group' AND json_extract(data,'$.phase')='complete' "
                        "AND json_extract(data,'$.unit')=?", (publication.get("unit"),)):
                    candidate_group = json.loads(group_raw)
                    if ident in candidate_group.get("journals", {}).values():
                        group = candidate_group
                        break
                if group is None:
                    continue
            sealid = group.get("seal") if group else publication.get("seal")
            seal = get(sealid, "seal")
            reviewid = group.get("review") if group else publication.get("review")
            review = get(reviewid, "review")
            if not seal or review.get("valid") is False or review.get("completed") is not True or review.get("verdict") != "pass":
                continue
            keys = ("binding_version", "unit", "seal", "project_revision", "generation", "publication_order", "vector", "validation_digest") if seal.get("binding_version") == 2 else (
                    "repository", "unit", "base", "candidate", "seal", "provenance", "validation_digest")
            if any(key not in seal or review.get(key) != seal[key] for key in keys):
                continue
            answer = review.get("answer")
            identity = review.get("review_identity") or {}
            if not isinstance(answer, str) or not answer or not identity.get("run") or not identity.get("thread") or hashlib.sha256(answer.encode()).hexdigest() != review.get("answer_digest"):
                continue
            part = get(publication.get("seal"), "seal_part") if group else seal
            if group and not any(item.get("seal") == publication.get("seal") and item.get("candidate") == tree
                                 and item.get("branch") == branch for item in seal.get("vector", [])):
                continue
            if part.get("candidate") != tree or part.get("branch") != branch or publication.get("unit") != seal.get("unit"):
                continue
            return {"publication": ident, "commit": commit, "tree": tree, "seal": sealid, "review": reviewid, "branch": branch}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None
    finally:
        if connection is not None:
            connection.close()
    return None
