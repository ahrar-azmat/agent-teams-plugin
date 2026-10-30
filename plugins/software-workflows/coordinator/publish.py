"""Immutable candidate trees, review bindings and recoverable lead publication.

The real index is never rebuilt from HEAD. An index.lock excludes normal Git
writers while a copy of its exact contents receives only this unit's entries.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from .locks import finish, finish_thread
from .client import CoordinatorError
from . import gitio, processes, native
from .ledger import read_path
from .store import digest, uid, fsync_dir


BINDING = ("repository", "unit", "base", "candidate", "seal", "provenance", "validation_digest")


def binding(seal):
    keys = ("binding_version", "unit", "seal", "project_revision", "generation", "publication_order", "vector", "validation_digest") if seal.get("binding_version") == 2 else BINDING
    return {key: seal[key] for key in keys}


class Publisher:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store

    async def quiescent(self, unit, allow_quarantined=False):
        listeners = {actor["listener"] for actor in self.store.all("actor", unit["id"]) if actor.get("listener")}
        dormant = set()
        for listenerid in listeners:
            listener = self.store.get(listenerid)
            if listener["state"] == "dormant":
                self.daemon.dormancy.require_quiet(listener)
                dormant.add(listenerid)
                continue
            connection = self.daemon.connections.get(listenerid)
            if not connection or connection.closed:
                raise CoordinatorError("LISTENER_UNAVAILABLE", "Cannot enumerate every descendant before sealing")
            await self.daemon.discover_descendants(self.store.get(listenerid))
        for actor in list(self.store.all("actor", unit["id"])):
            if actor.get("listener") and actor["listener"] not in dormant:
                connection = self.daemon.connections.get(actor["listener"])
                if not connection or connection.closed:
                    raise CoordinatorError("LISTENER_UNAVAILABLE", "Cannot prove writer quiescence")
                response = await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": False})
                reason = native.quiet_status(response["thread"].get("status"))
                if reason and reason != "native_active_turn":
                    raise CoordinatorError("NATIVE_STATUS_UNKNOWN", reason, {"actor": actor["id"]})
                if reason or actor.get("active_turn"):
                    raise CoordinatorError("ACTIVE_TURN", "Root or descendant is not quiescent", {"actor": actor["id"]})
                await self.daemon.settle_actor(actor)
                terminals = await connection.call("thread/backgroundTerminals/list", {"threadId": actor["thread"]})
                if terminals["data"] or terminals.get("nextCursor"):
                    raise CoordinatorError("BACKGROUND_TERMINAL", "Background terminal inventory is not empty")
        if self.daemon.custody.unit_active(unit["id"]):
            raise CoordinatorError("CUSTODY_HELD", "Command custody has not been reconciled")
        from .lead_calls import refuse
        from .participants import checkout_ids
        for checkout in checkout_ids(self.store, unit["id"]):
            refuse(self.store, checkout, code="UNVERIFIED_OPERATION")
        for operation in self.store.all("operation", unit["id"]):
            if allow_quarantined and operation["state"] == "quarantined" and not operation.get("executing"):
                continue  # Reopening permits restoration, never sealing or publication.
            if operation["state"] not in ("verified", "reconciled", "resolved", "denied"):
                raise CoordinatorError("UNVERIFIED_OPERATION", "Unit has a pending or quarantined operation", {"operation": operation["id"]})
        if any(r["state"] == "pending" for r in self.store.all("dynamic", unit["id"])):
            raise CoordinatorError("PENDING_TOOL", "Unit has a pending dynamic tool request")
        if any(r["state"] == "waiting" for r in self.store.all("custody_request", unit["id"])):
            raise CoordinatorError("PENDING_HOOK", "A hook is waiting for command custody")

    def restored_from(self, operation, path):
        """What a restore operation wrote at `path`: a revision id, "baseline", or None (absence). Its own record
        (restored_from, from 2.4.4), else the unknown/restored or unknown/checkout_restored event 2.4.3 left beside it;
        KeyError when neither names it."""
        recorded = operation.get("restored_from") or {}
        if path in recorded:
            return recorded[path]
        for (unknown,) in self.store.db.execute("SELECT unknown_id FROM resolutions WHERE restore=?", (operation["id"],)).fetchall():
            for (data,) in self.store.db.execute("SELECT data FROM events WHERE subject=? AND kind IN ('unknown/restored', "
                                                 "'unknown/checkout_restored') ORDER BY seq", (unknown,)).fetchall():
                event = json.loads(data)
                if event.get("restore") != operation["id"]:
                    continue
                if path in (event.get("known_revisions") or {}):
                    return event["known_revisions"][path]
                if event.get("known_revision"):
                    return event["known_revision"]
        raise KeyError(path)

    def provenance(self, row, unitid):
        """The revision whose unit a reference to `row` depends on, or None for no unit's work. A restore writes known
        bytes (recovery.restore_unknown), so its dependency is whose bytes they are, never the restoring unit's: the
        revision the restore recorded as its source, followed through restores of restores, where a baseline, or
        absence where the branch commit held nothing, is no unit's work. A restore that recorded no source is matched by
        its bytes: the path's baseline bytes are no unit's; other bytes are those of a receipt holding them, the sealing
        unit's or a published one first; with none, the restoring unit's, as before. (2.4.3 refused Smartpay_Frontend's
        unit_e6acbaa9 for each of 16 paths its lead had restored to the baseline from its own session's unit.)"""
        seen = set()
        while row["attribution"] == "restore" and row["id"] not in seen:
            seen.add(row["id"])
            operation = self.store.get(row["operation"], required=False) if row["operation"] else None
            if not operation:
                return self.matched(row, unitid)
            if operation.get("resolution") == "baseline":
                return None
            try:
                source = self.restored_from(operation, row["path"])
            except KeyError:
                return self.matched(row, unitid)
            if source is None or source == "baseline":
                return None
            found = self.store.db.execute("SELECT * FROM revisions WHERE id=?", (source,)).fetchone()
            if (not found or found["checkout"] != row["checkout"] or found["path"] != row["path"]
                    or found["attribution"] not in ("baseline", "exact", "restore")):
                return self.matched(row, unitid)
            row = found
        return None if row["attribution"] == "baseline" else row

    def matched(self, row, unitid):
        """provenance() of a restore that recorded no source, by its bytes."""
        baselines = {(r["blob"], r["mode"]) for r in self.store.db.execute(
            "SELECT blob, mode FROM revisions WHERE checkout=? AND path=? AND attribution='baseline'", (row["checkout"], row["path"]))}
        if (row["blob"], row["mode"]) in baselines or row["blob"] is None and not baselines:
            return None  # the bytes registration found there, or absence where it found nothing
        holders = self.store.db.execute("SELECT * FROM revisions WHERE checkout=? AND path=? AND attribution='exact' AND blob IS ? "
                                        "AND mode IS ? ORDER BY rowid", (row["checkout"], row["path"], row["blob"], row["mode"])).fetchall()
        if any(not h["unit"] or h["unit"] == unitid or self.store.get(h["unit"])["state"] == "published" for h in holders):
            return None
        return holders[0] if holders else row

    async def dependency_check(self, unit, operations):
        """Refuse a seal whose operations started from, or read, unexplained bytes or another unit's unpublished
        revision. A reference to a path outside the ledger (ledger.unwatched) is no dependency: nothing there is
        published (2.4.0-2.4.3 refused a unit whose writer had read test output while it was UNKNOWN). A reference to a
        restore depends on whose bytes it restored (provenance). A git failure refuses (GIT_FAILED)."""
        rows = {}
        for operation in operations:
            if operation["state"] == "resolved" or operation["kind"] == "restore":
                continue  # Restores deliberately replace an UNKNOWN base with known receipt bytes.
            # Opaque-operation snapshots describe possible effects, not reads of
            # every checkout file. Only actual mutation bases and explicit reads
            # are dependencies on another unit's unpublished revisions.
            references = list(operation["bases"].values()) if operation["kind"] not in ("command", "mcp", "lead-command") else []
            references += [revision for read in operation.get("dependencies", []) for revision in read.values()]
            for revision in references:
                if not revision or revision in rows:
                    continue
                row = self.store.db.execute("SELECT * FROM revisions WHERE id=?", (revision,)).fetchone()
                if not row:
                    raise CoordinatorError("PROVENANCE_MISSING", "A base/read revision is unavailable")
                rows[revision] = row
        checkouts = {}
        for row in rows.values():
            checkouts.setdefault(row["checkout"], set()).add(row["path"])
        outside = {key: await self.daemon.ledger.unwatched(self.store.get(key), paths) for key, paths in checkouts.items()}
        rows = [row for row in rows.values() if row["path"] not in outside[row["checkout"]]]
        unknown = sorted({row["path"] for row in rows if row["attribution"] == "UNKNOWN"})
        if unknown:
            raise CoordinatorError("UNKNOWN_DEPENDENCY", "Operation depends on unexplained bytes", {"paths": unknown})
        rows = [source for source in (self.provenance(row, unit["id"]) for row in rows) if source]
        unpublished = [row for row in rows if row["unit"] and row["unit"] != unit["id"] and self.store.get(row["unit"])["state"] != "published"]
        if unpublished:
            raise CoordinatorError("UNPUBLISHED_DEPENDENCY", "Operation read an unpublished other-unit revision",
                                   {"unit": unpublished[0]["unit"], "revision": unpublished[0]["id"],
                                    "paths": sorted({row["path"] for row in unpublished})})

    async def seal(self, unitid, validation, order=None):
        unit = self.store.get(unitid)
        if unit.get("project_revision"):
            from .group_publish import GroupPublisher
            return await GroupPublisher(self.daemon).seal(unitid, validation, order)
        if unit["state"] not in ("open", "sealed"):
            raise CoordinatorError("UNIT_FROZEN", "Unit cannot be sealed in its current state")
        attemptid = uid("seal_attempt")
        attempt = {"unit": unitid, "generation": self.store.generation, "state": "started",
                   "prior_state": unit["state"], "prior_seal": unit.get("seal"), "intended_base": unit["base"],
                   "validation_digest": digest(validation), "created": time.time(),
                   "index": str(self.store.root / "indexes" / (attemptid + ".index"))}
        with self.store.transaction():
            self.store.put("seal_attempt", attemptid, attempt, unitid)
            unit.update(state="sealing", seal_attempt=attemptid)
            self.store.save(unit)
            self.store.event("seal/started", attemptid, attempt)
        try:
            return await finish(self.freeze(unitid, validation))
        except BaseException:
            if self.store.get(attemptid)["state"] != "completed":
                self.interrupt_seal(unitid, "seal did not complete")
            raise

    def interrupt_seal(self, unitid, reason):
        unit = self.store.get(unitid)
        with self.store.transaction():
            if unit.get("seal"):
                unit["previous_seal"] = unit.pop("seal")
            attempt = self.store.get(unit.get("seal_attempt"), required=False)
            if attempt and attempt["state"] != "completed":
                attempt.update(state="interrupted", reason=reason, recovered_generation=self.store.generation)
                self.store.save(attempt)
            unit.update(state="open", review_required=True)
            self.store.save(unit)
            self.store.event("seal/interrupted", unitid, {"reason": reason, "invalidated_seal": unit.get("previous_seal")})

    async def scope_rows(self, unit, seal=None, sealing=False):
        checkout = self.store.get(unit["checkout"])
        self.daemon.ledger.require_owned(checkout)
        paths = self.daemon.ledger.mutation_scope(unit["id"], unit["checkout"])
        if not paths:
            raise CoordinatorError("UNIT_EMPTY", "Unit has no mutation scope")
        await self.daemon.ledger.reconcile(checkout, paths)
        unknowns = self.store.unresolved(checkout["id"], paths)
        if unknowns:
            raise CoordinatorError("UNKNOWN_BLOCKS_SEAL" if sealing else "UNKNOWN_BLOCKS_PUBLICATION",
                                   "Complete operation scope includes unresolved UNKNOWN history",
                                   {"paths": paths, "unknowns": [u["id"] for u in unknowns]})
        rows = [self.store.head(checkout["id"], path) for path in paths]
        for path, row in zip(paths, rows):
            if not row or row["unit"] != unit["id"] or row["attribution"] != "exact":
                raise CoordinatorError("PROVENANCE_INCOMPLETE", "Every scoped path needs this unit's final exact receipt", {"path": path})
            operation = self.store.get(row["operation"])
            if operation["state"] != "verified" or operation.get("revisions", {}).get(path) != row["id"]:
                raise CoordinatorError("REVISION_CHANGED", "Final receipt is not backed by a verified operation", {"path": path})
        if seal and (seal["paths"] != paths or [r["id"] for r in seal["rows"]] != [r["id"] for r in rows]):
            raise CoordinatorError("SEAL_SCOPE_CHANGED", "Seal omits operation scope or no longer names the current exact revisions",
                                   {"paths": paths, "sealed_paths": seal["paths"]})
        await self.verify_working(checkout, rows)
        return rows

    async def freeze(self, unitid, validation):
        unit, checkout = self.store.get(unitid), self.store.get(self.store.get(unitid)["checkout"])
        if unit["state"] == "published":
            raise CoordinatorError("UNIT_PUBLISHED", "Published unit cannot be resealed")
        await self.quiescent(unit)
        rows = await self.scope_rows(unit, sealing=True)
        paths = [row["path"] for row in rows]
        operations = list(self.store.all("operation", unitid))
        await self.dependency_check(unit, operations)
        branch = await finish_thread(gitio.text, checkout["root"], ["symbolic-ref", "HEAD"])
        if branch != unit["branch"]:
            raise CoordinatorError("BRANCH_CHANGED", "Unit's named branch changed")
        current_base = await finish_thread(gitio.text, checkout["root"], ["rev-parse", branch])
        if current_base != unit["base"]:
            original = await finish_thread(gitio.entries, checkout["root"], unit["base"])
            current = await finish_thread(gitio.entries, checkout["root"], current_base)
            if any(original.get(path, []) != current.get(path, []) for path in paths):
                raise CoordinatorError("BASE_SCOPE_CHANGED", "Branch advanced through this unit's paths; a new verified operation is required")
            unit["base"] = current_base
        for item in validation:
            if not isinstance(item, dict) or not all(k in item for k in ("command", "exit_code", "inputs", "scope")):
                raise CoordinatorError("VALIDATION_EVIDENCE", "Validation records need command, exit_code, actual inputs and scope")
            if item["scope"] not in ("mixed-checkout", "candidate-objects"):
                raise CoordinatorError("VALIDATION_SCOPE", "Do not label mixed checkout validation isolated")
        self.store.save(unit)
        try:
            await self.verify_working(checkout, rows)
            attempt = self.store.get(unit["seal_attempt"])
            candidate, entries = await finish_thread(self.build_tree, checkout, unit["base"], rows, Path(attempt["index"]))
            # Validate again after asynchronous object construction; bytes alone
            # cannot resurrect an UNKNOWN or an overwritten revision identity.
            final_rows = await self.scope_rows(unit, sealing=True)
            if [r["id"] for r in final_rows] != [r["id"] for r in rows]:
                raise CoordinatorError("REVISION_CHANGED", "Candidate revisions changed while freezing")
            provenance = digest([{k: row[k] for k in ("id", "path", "previous", "operation", "blob", "mode")} for row in rows])
            sealid = uid("seal")
            seal = {"repository": checkout["repository"], "unit": unitid, "base": unit["base"], "branch": unit["branch"],
                    "candidate": candidate, "paths": paths, "entries": entries, "rows": rows, "provenance": provenance,
                    "validation": validation, "validation_digest": digest(validation), "created": time.time(), "seal": sealid}
            with self.store.transaction():
                self.store.put("seal", sealid, seal, unitid)
                self.store.event("unit/sealed", unitid, {k: seal[k] for k in BINDING})
                unit.update(state="sealed", seal=sealid, review_required=True)
                self.store.save(unit)
                attempt.update(state="completed", seal=sealid, completed=time.time())
                self.store.save(attempt)
            return self.store.get(sealid)
        except BaseException:
            unit["state"] = "open"
            self.store.save(unit)
            raise

    async def verify_working(self, checkout, rows):
        for row in rows:
            try:
                self.store.read_blob(row["blob"]) if row["blob"] else None
            except CoordinatorError as exc:
                if row.get("operation"):
                    await self.daemon.ledger.quarantine(self.store.get(row["operation"]), exc.code)
                raise
            head = self.store.head(checkout["id"], row["path"])
            if not head or head["id"] != row["id"]:
                raise CoordinatorError("REVISION_CHANGED", "Current revision differs from frozen receipt", {"path": row["path"]})
            actual = await finish_thread(read_path, self.store, checkout, row["path"])
            if actual != {"blob": row["blob"], "mode": row["mode"]}:
                raise CoordinatorError("FROZEN_BYTES_DIVERGED", "Working bytes or modes differ from the sealed receipt", {"path": row["path"]})

    def build_tree(self, checkout, base, rows, index):
        self.repository_identity(checkout)
        gitio.git(checkout["root"], ["read-tree", base], index=index)
        entries = {}
        for row in rows:
            if row["blob"]:
                content = self.store.read_blob(row["blob"])
                self.repository_identity(checkout)
                oid = gitio.text(checkout["root"], ["hash-object", "-w", "--stdin", "--no-filters"], data=content)
                if oid != gitio.blob_oid(content, checkout["object_format"]):
                    raise CoordinatorError("OBJECT_HASH", "Git object disagrees with receipt bytes")
                entries[row["path"]] = {"mode": row["mode"], "oid": oid}
            else:
                entries[row["path"]] = {"mode": None, "oid": "0" * (64 if checkout["object_format"] == "sha256" else 40)}
        gitio.put_entries(checkout["root"], index, entries)
        self.repository_identity(checkout)
        tree = gitio.text(checkout["root"], ["write-tree"], index=index)
        return tree, entries

    def review(self, unitid, evidence):
        unit = self.store.get(unitid)
        if unit["state"] != "sealed":
            raise CoordinatorError("UNIT_NOT_SEALED", "Review evidence requires an immutable seal")
        seal = self.store.get(unit["seal"])
        for key, value in binding(seal).items():
            if evidence.get(key) != value:
                raise CoordinatorError("REVIEW_BINDING", "Review evidence does not match sealed " + key)
        if evidence.get("verdict") != "pass" or evidence.get("completed") is not True or not evidence.get("review_identity"):
            raise CoordinatorError("REVIEW_INCOMPLETE", "A completed, identified passing review is required")
        answer = evidence.get("answer")
        if not isinstance(answer, str) or not answer or hashlib.sha256(answer.encode()).hexdigest() != evidence.get("answer_digest"):
            raise CoordinatorError("REVIEW_ANSWER", "Completed review answer digest is missing or mismatched")
        ident = uid("review")
        self.store.put("review", ident, dict(evidence, recorded=time.time()), unitid)
        self.store.event("unit/review_recorded", unitid, {"review": ident, "seal": seal["id"], "answer_digest": evidence["answer_digest"]})
        return {"review": ident, "seal": seal["id"]}

    async def diff(self, unitid):
        unit = self.store.get(unitid)
        if not unit.get("seal"):
            raise CoordinatorError("UNIT_NOT_SEALED", "Seal before asking for an object review")
        seal, checkout = self.store.get(unit["seal"]), self.store.get(unit["checkout"])
        if seal.get("binding_version") == 2:
            from .group_publish import GroupPublisher
            return await GroupPublisher(self.daemon).diff(unitid)
        result = await finish_thread(gitio.git, checkout["root"], ["diff", "--no-ext-diff", "--no-textconv", seal["base"], seal["candidate"]])
        return {"binding": {key: seal[key] for key in BINDING}, "diff": result.stdout.decode("utf-8", "strict")}

    def publication_guards(self, checkout, seal):
        self.repository_identity(checkout)
        root = checkout["root"]
        gitio.calibrate(self.store.root)
        if gitio.text(root, ["for-each-ref", "--format=%(refname)", "refs/replace/"]):
            raise CoordinatorError("PUBLICATION_REPLACE_REFS", "Replacement objects change normal Git interpretation")
        if gitio.text(root, ["symbolic-ref", "HEAD"]) != seal["branch"]:
            raise CoordinatorError("BRANCH_CHANGED", "Symbolic HEAD no longer names the reviewed branch")
        if gitio.text(root, ["rev-parse", seal["branch"]]) != seal["base"]:
            raise CoordinatorError("STALE_BASE", "Branch tip changed; reseal and review against its new base")
        hooks = ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "reference-transaction", "post-index-change")
        for event in hooks:
            result = gitio.git(root, ["hook", "list", "-z", event], normal_config=True, check=False)
            if result.returncode not in (0, 1) or result.stdout:
                raise CoordinatorError("PUBLICATION_HOOK", "Normal publication would run a hook or hook discovery failed", {"event": event})
        settings = {}
        for key in ("commit.gpgSign", "core.autocrlf", "core.eol", "core.fsmonitor", "core.splitIndex", "index.sparse", "core.untrackedCache"):
            result = gitio.git(root, ["config", "--get", key], normal_config=True, check=False)
            if result.returncode not in (0, 1):
                raise CoordinatorError("GIT_CONFIG", "Could not inspect publication configuration")
            settings[key] = result.stdout.decode().strip()
        false = ("", "false", "no", "off", "0")
        if settings["commit.gpgSign"].lower() not in false:
            raise CoordinatorError("PUBLICATION_SIGNING", "Configured commit signing requires a supported normal-signing path")
        if settings["core.autocrlf"].lower() not in false:
            raise CoordinatorError("PUBLICATION_CONVERSION", "core.autocrlf could transform receipt bytes")
        if settings["core.fsmonitor"].lower() not in false:
            raise CoordinatorError("PUBLICATION_HELPER", "Normal Git publication has a configured filesystem monitor")
        for key in ("core.splitIndex", "index.sparse", "core.untrackedCache"):
            if settings[key].lower() not in false:
                raise CoordinatorError("INDEX_EXTENSION", "Unsupported real-index behavior: " + key)
        paths = b"".join(p.encode() + b"\0" for p in seal["paths"])
        for source in (seal["base"], seal["candidate"], None):
            args = ["check-attr", "-z"] + (["--source=" + source] if source else [])
            args += ["--stdin", "filter", "text", "eol", "crlf", "ident", "working-tree-encoding"]
            attrs = gitio.git(root, args, data=paths, normal_config=True).stdout.split(b"\0")
            for i in range(0, len(attrs) - 1, 3):
                if attrs[i + 2] not in (b"unspecified", b"unset"):
                    raise CoordinatorError("PUBLICATION_ATTRIBUTES", "Normal publication might transform frozen bytes",
                                           {"path": attrs[i].decode(), "attribute": attrs[i + 1].decode(), "source": source})
        return digest(settings)

    async def publish(self, unitid, reviewid, metadata):
        unit = self.store.get(unitid)
        if unit.get("project_revision"):
            from .group_publish import GroupPublisher
            return await GroupPublisher(self.daemon).publish(unitid, reviewid, metadata)
        existing = next((p for p in self.store.all("publication", unitid) if p["phase"] != "abandoned"), None)
        if existing:
            if existing["review"] != reviewid or existing["metadata"] != metadata:
                raise CoordinatorError("PUBLICATION_INPUT_CHANGED", "Retry must match the journaled review and complete commit metadata")
            return await self.reconcile(existing)
        if unit["state"] != "sealed":
            raise CoordinatorError("UNIT_NOT_SEALED", "Publication requires a sealed unit")
        seal = self.store.get(unit["seal"])
        review = self.store.get(reviewid)
        if review["_kind"] != "review" or review.get("valid") is False or any(review.get(key) != seal[key] for key in BINDING) or review.get("verdict") != "pass":
            raise CoordinatorError("REVIEW_BINDING", "Missing, stale or mismatched completed review")
        required = {"message", "author_name", "author_email", "author_date", "committer_name", "committer_email", "committer_date"}
        if set(metadata) != required or any(not isinstance(value, str) or not value or "\0" in value for value in metadata.values()):
            raise CoordinatorError("PUBLICATION_METADATA", "Persist complete explicit commit identity, dates and message before publication")
        checkout = self.store.get(unit["checkout"])
        await self.quiescent(unit)
        await self.daemon.ledger.reconcile(checkout)
        if self.store.unresolved(checkout["id"], seal["paths"]):
            raise CoordinatorError("UNKNOWN_BLOCKS_PUBLICATION", "Unresolved UNKNOWN includes a frozen path")
        await self.scope_rows(unit, seal)
        config_digest = await finish_thread(self.publication_guards, checkout, seal)
        base_entries = await finish_thread(gitio.entries, checkout["root"], seal["base"])
        real_entries = await finish_thread(gitio.entries, checkout["root"])
        # A unit's staged entry must still be the reviewed base, never someone else's independent staging.
        for path in seal["paths"]:
            if real_entries.get(path, []) != base_entries.get(path, []):
                raise CoordinatorError("UNIT_INDEX_CHANGED", "Unit path has independent staged changes", {"path": path})
        ident = uid("publication")
        publication = {"unit": unitid, "seal": seal["id"], "review": reviewid, "metadata": metadata,
                       "phase": "prepared", "base_index_entries": {p: real_entries.get(p, []) for p in seal["paths"]},
                       "config_digest": config_digest, "created": time.time()}
        with self.store.transaction():
            self.store.put("publication", ident, publication, unitid)
            self.store.event("publication/prepared", ident, publication)
        return await self.reconcile(self.store.get(ident))

    async def reconcile(self, publication):
        unit = self.store.get(publication["unit"])
        seal, checkout = self.store.get(publication["seal"]), self.store.get(publication.get("checkout", unit["checkout"]))
        unit = dict(unit, checkout=checkout["id"])
        if publication["phase"] in ("complete", "abandoned"):
            return publication
        await finish_thread(self.repository_identity, checkout)
        if publication["phase"] in ("prepared", "commit_created", "ref_updated", "index_pending"):
            await finish(self.ensure_index_lock(checkout, publication))
        if publication["phase"] == "prepared":
            await finish_thread(self.verify_base_index, checkout, seal, publication)
            await self.scope_rows(unit, seal)
            await self.verify_working(checkout, seal["rows"])
            await finish_thread(self.publication_guards, checkout, seal)
            meta = publication["metadata"]
            env = {"GIT_" + key.upper(): meta[key] for key in ("author_name", "author_email", "author_date", "committer_name", "committer_email", "committer_date")}
            def create_commit():
                self.repository_identity(checkout)
                return gitio.text(checkout["root"], ["commit-tree", seal["candidate"], "-p", seal["base"]],
                                  data=meta["message"].encode(), normal_config=True, extra_env=env)
            commit = await finish_thread(create_commit)
            # Deterministic metadata means recovery after commit-tree, before this
            # journal write, recreates the identical object rather than a second commit.
            publication.update(phase="commit_created", commit=commit)
            self.store.save(publication)
            self.store.event("publication/commit_created", publication["id"], {"commit": commit})
        if publication["phase"] == "commit_created":
            tip = await finish_thread(gitio.text, checkout["root"], ["rev-parse", seal["branch"]])
            installed = await finish_thread(self.installed_tip, checkout, seal, publication, tip)
            if not installed:
                await finish_thread(self.verify_base_index, checkout, seal, publication)
                await self.scope_rows(unit, seal)
                await self.quiescent(unit)
                await self.daemon.ledger.reconcile(checkout)
                if self.store.unresolved(checkout["id"], seal["paths"]):
                    raise CoordinatorError("UNKNOWN_BLOCKS_PUBLICATION", "UNKNOWN arose before ref update")
                await self.verify_working(checkout, seal["rows"])
                if await finish_thread(self.publication_guards, checkout, seal) != publication["config_digest"]:
                    raise CoordinatorError("PUBLICATION_CONFIG_CHANGED", "Publication configuration changed")
                await finish_thread(gitio.reference_update, checkout["root"], seal["branch"], publication["commit"], seal["base"],
                                        lambda: self.repository_identity(checkout))
            publication["phase"] = "ref_updated"
            self.store.save(publication)
            self.store.event("publication/ref_updated", publication["id"], {"commit": publication["commit"]})
        if publication["phase"] in ("ref_updated", "index_pending"):
            publication["phase"] = "index_pending"
            self.store.save(publication)
            await finish(self.update_index(checkout, seal, publication))
            publication["phase"] = "index_updated"
            self.store.save(publication)
            self.store.event("publication/index_updated", publication["id"], {})
        if publication["phase"] == "index_updated":
            await self.cleanup_anchor(publication)
            await self.daemon.ledger.reconcile(checkout)
            # The irreversible ref update already happened. New working bytes
            # are separate UNKNOWN, never grounds to pretend it did not happen.
            publication["unresolved_after_publication"] = [u["id"] for u in self.store.unresolved(checkout["id"], seal["paths"])]
            # No await between completion, publication state and claim release.
            async with self.daemon.custody.condition:
                with self.store.transaction():
                    if not publication.get("group"):
                        unit.update(state="published", commit=publication["commit"])
                        self.store.save(unit)
                    for claim in self.daemon.custody.claims(checkout["id"]):
                        if claim["unit"] == unit["id"] and not publication.get("group"):
                            claim["state"] = "released"
                            self.store.save(claim)
                    publication["phase"] = "complete"
                    self.store.save(publication)
                    self.store.event("publication/complete", publication["id"], {"commit": publication["commit"]})
                self.daemon.custody.condition.notify_all()
        return publication

    def verify_base_index(self, checkout, seal, publication):
        # The preflight snapshot preceded index.lock. Recheck under the owned
        # lock before creating an object or changing a ref, including recovery.
        current = gitio.entries(checkout["root"])
        for path in seal["paths"]:
            if current.get(path, []) != publication["base_index_entries"][path]:
                raise CoordinatorError("UNIT_INDEX_CHANGED", "Unit staging changed before publication acquired index.lock; abandon this unapplied publication before reconciling staging", {"path": path})

    def repository_identity(self, checkout):
        metadata = self.daemon.ledger._metadata(checkout["root"])
        if metadata["identity"] != checkout["identity"] or metadata["repository"] != checkout["repository"]:
            raise CoordinatorError("CHECKOUT_IDENTITY_CHANGED", "Publication repository identity changed; explicit recovery required")
        return metadata

    async def abandon(self, unitid, publication=None):
        """Abandon only a provably unapplied journal, under native ref locks."""
        unit = self.store.get(unitid)
        publications = [publication] if publication else [p for p in self.store.all("publication", unitid) if p["phase"] != "abandoned"]
        if len(publications) != 1:
            raise CoordinatorError("PUBLICATION_ABANDON", "Exactly one active publication journal is required")
        publication = publications[0]
        if publication["phase"] not in ("prepared", "commit_created"):
            return await self.reconcile(publication)
        checkout, seal = self.store.get(publication.get("checkout", unit["checkout"])), self.store.get(publication["seal"])
        await finish(self.ensure_index_lock(checkout, publication))
        metadata = await finish_thread(self.repository_identity, checkout)
        if metadata["branch"] != seal["branch"]:
            raise CoordinatorError("BRANCH_CHANGED", "Cannot prove abandonment on another branch")
        tip = await finish_thread(gitio.text, checkout["root"], ["rev-parse", seal["branch"]])
        installed = False
        guard = gitio.reference_verification(checkout["root"], seal["branch"], tip)
        entered = False
        def enter():
            nonlocal entered
            guard.__enter__()
            entered = True
        try:
            await finish_thread(enter)
            await finish_thread(self.repository_identity, checkout)
            commit = publication.get("commit")
            if commit and tip == commit:
                installed = True
            else:
                if commit:
                    ancestry = await finish_thread(gitio.git, checkout["root"], ["merge-base", "--is-ancestor", commit, tip], check=False)
                    if ancestry.returncode == 0:
                        installed = True
                    elif ancestry.returncode != 1:
                        raise CoordinatorError("PUBLICATION_ANCESTRY_UNKNOWN", "Cannot prove the recorded commit is unapplied")
                if not installed:
                    with self.store.transaction():
                        publication.update(phase="abandoned", abandoned_from=publication["phase"], observed_tip=tip, abandoned=time.time())
                        self.store.save(publication)
                        review = self.store.get(publication["review"])
                        review.update(valid=False, invalidation="publication abandoned")
                        self.store.save(review)
                        unit.update(review_required=True)
                        self.store.save(unit)
                        self.store.event("publication/abandoned", publication["id"], {"observed_tip": tip})
        finally:
            if entered:
                await finish_thread(guard.__exit__, None, None, None)
        if installed:
            # A crash may have lost ref_updated. Read the actual ref, not merely
            # the journal's phase, before deciding whether abandonment is legal.
            return await self.reconcile(publication)
        await self.cleanup_anchor(publication)
        return publication

    def installed_tip(self, checkout, seal, publication, tip):
        commit = publication.get("commit")
        if not commit:
            return False
        if tip == commit:
            return True
        ancestry = gitio.git(checkout["root"], ["merge-base", "--is-ancestor", commit, tip], check=False)
        if ancestry.returncode == 1:
            return False
        if ancestry.returncode:
            raise CoordinatorError("PUBLICATION_ANCESTRY_UNKNOWN", "Recorded publication object is unavailable; retain the journal and recover the missing object")
        # Check every intervening parent edge, including edits later reverted and
        # merge parents. Prefix overlap also catches file/directory conflicts.
        from .ledger import overlaps, normalize
        scope = [normalize(checkout, p)["key"] for p in seal["paths"]]
        for row in gitio.text(checkout["root"], ["rev-list", "--parents", commit + ".." + tip]).splitlines():
            revision, *parents = row.split()
            for parent in parents:
                changed = gitio.git(checkout["root"], ["diff-tree", "--no-ext-diff", "--no-textconv", "--no-renames",
                    "--no-commit-id", "-r", "--name-only", "-z", parent, revision]).stdout.split(b"\0")
                for path in changed:
                    if path and any(overlaps(normalize(checkout, path.decode())["key"], p) for p in scope):
                        raise CoordinatorError("PUBLICATION_SCOPE_ADVANCED", "A later commit overlaps publication scope; preserve the journal and reconcile that staged/path conflict before retry", {"path": path.decode(), "tip": tip})
        return True

    def index_identity(self, checkout, seal, publication):
        metadata = self.daemon.ledger._metadata(checkout["root"])
        if metadata["identity"] != checkout["identity"] or metadata["repository"] != checkout["repository"]:
            raise CoordinatorError("CHECKOUT_IDENTITY_CHANGED", "Checkout identity changed during index reconciliation")
        if metadata["branch"] != seal["branch"]:
            raise CoordinatorError("BRANCH_CHANGED", "Symbolic HEAD no longer names the publication branch")
        if not self.installed_tip(checkout, seal, publication, metadata["base"]):
            raise CoordinatorError("PUBLICATION_REF_DIVERGED", "Publication commit is not in this branch history; restore the intended branch identity before reconciliation")
        return metadata["base"]

    def new_anchor(self, index, publication):
        token = uid("index_owner")
        anchor = index.with_name("c1-" + token)
        descriptor = os.open(anchor, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(token.encode())
            stream.flush()
            os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
        fsync_dir(index.parent)
        return {"anchor": str(anchor), "index": str(index), "lock": str(index) + ".lock", "token": token,
                "device": info.st_dev, "inode": info.st_ino, "publication": publication["id"],
                "owner": processes.identity(os.getpid()), "generation": self.store.generation, "state": "recorded"}

    def pinned(self, record, path):
        import stat
        try:
            info = Path(path).lstat()
        except FileNotFoundError:
            return False
        return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                (info.st_dev, info.st_ino) == (record["device"], record["inode"]))

    def acquire_index(self, record):
        if not self.pinned(record, record["anchor"]):
            raise CoordinatorError("INDEX_OWNERSHIP", "Durable ownership anchor is missing or changed")
        previous = record["owner"]
        current = processes.identity(previous["pid"])
        if record["generation"] != self.store.generation and current and not current["zombie"] and current["birth"] == previous["birth"]:
            raise CoordinatorError("INDEX_OWNER_LIVE", "Prior index owner is still alive")
        lock = Path(record["lock"])
        if lock.exists() or lock.is_symlink():
            if not self.pinned(record, lock):
                raise CoordinatorError("INDEX_LOCKED", "A foreign Git writer owns index.lock; it will not be removed")
            return "held"
        if self.pinned(record, record["index"]):
            if not record.get("replacement_digest") or hashlib.sha256(Path(record["index"]).read_bytes()).hexdigest() != record["replacement_digest"]:
                raise CoordinatorError("INDEX_OWNERSHIP", "Installed owned inode does not match the recorded replacement")
            return "installed"
        try:
            os.link(record["anchor"], lock, follow_symlinks=False)
        except FileExistsError as exc:
            raise CoordinatorError("INDEX_LOCKED", "A Git writer owns index.lock; publication stays pending") from exc
        fsync_dir(lock.parent)
        return "held"

    async def ensure_index_lock(self, checkout, publication):
        self.repository_identity(checkout)
        index = Path(await finish_thread(gitio.text, checkout["root"], ["rev-parse", "--path-format=absolute", "--git-path", "index"]))
        record = publication.get("index_lock")
        if not record:
            record = await finish_thread(self.new_anchor, index, publication)
            record["checkout_identity"] = checkout["identity"]
            publication["index_lock"] = record
            self.store.save(publication)
            self.store.event("publication/index_anchor", publication["id"], record)
        if record["checkout_identity"] != checkout["identity"] or record["index"] != str(index) or record["publication"] != publication["id"]:
            raise CoordinatorError("INDEX_OWNERSHIP", "Index ownership does not match this checkout/publication")
        state = await finish_thread(self.acquire_index, record)
        if state == "installed":
            # Old journals installed by renaming index.lock. Re-establish the
            # owned link before touching refs or reconciling that installation.
            await finish_thread(os.link, record["anchor"], record["lock"], follow_symlinks=False)
            await finish_thread(fsync_dir, index.parent)
        record.update(state="held", owner=processes.identity(os.getpid()), generation=self.store.generation)
        self.store.save(publication)
        self.store.event("publication/index_locked", publication["id"], record)
        return record

    async def update_index(self, checkout, seal, publication):
        record = await self.ensure_index_lock(checkout, publication)
        tip = await finish_thread(self.index_identity, checkout, seal, publication)
        guard = gitio.reference_verification(checkout["root"], seal["branch"], tip)
        entered = False
        def enter():
            nonlocal entered
            guard.__enter__()
            entered = True
        try:
            # Both checks occur after index.lock. The prepared transaction keeps
            # HEAD and its referent fixed until replacement is durable.
            await finish_thread(self.index_identity, checkout, seal, publication)
            await finish_thread(enter)
            await finish_thread(self.index_identity, checkout, seal, publication)
            if self.pinned(record, record["index"]):
                # Installation already happened before its journal checkpoint.
                # Never truncate this inode: it is also the current index and
                # contains unrelated staging that must survive another crash.
                if not record.get("replacement_digest") or hashlib.sha256(Path(record["index"]).read_bytes()).hexdigest() != record["replacement_digest"]:
                    raise CoordinatorError("INDEX_OWNERSHIP", "Installed index differs from the durable replacement; preserve it for explicit reconciliation")
                return
            record["replacement_digest"] = await finish_thread(self.build_index, checkout, seal, publication)
            record["state"] = "replacement_ready"
            self.store.save(publication)
            self.store.event("publication/index_ready", publication["id"], {"digest": record["replacement_digest"]})
            await finish_thread(self.install_index, checkout, seal, publication)
        finally:
            if entered:
                await finish_thread(guard.__exit__, None, None, None)
            # Keep owned locks on refusal. Recovery can prove/reuse their inode;
            # removing a foreign or unjournaled lock is never a recovery action.

    def build_index(self, checkout, seal, publication):
        root, record = checkout["root"], publication["index_lock"]
        index, lock = Path(record["index"]), Path(record["lock"])
        if not self.pinned(record, lock) or not self.pinned(record, record["anchor"]):
            raise CoordinatorError("INDEX_OWNERSHIP", "Owned lock changed before replacement construction")
        temporary = self.store.root / "indexes" / (publication["id"] + ".index")
        before = index.read_bytes() if index.exists() else None
        if before is not None:
            temporary.write_bytes(before)
        else:
            if temporary.exists():
                temporary.unlink()
            gitio.git(root, ["read-tree", "--empty"], index=temporary)
        current = gitio.entries(root, index=temporary)
        desired = {path: ([dict(entry, stage="0")] if entry["mode"] else []) for path, entry in seal["entries"].items()}
        for path in seal["paths"]:
            if current.get(path, []) not in (publication["base_index_entries"][path], desired[path]):
                raise CoordinatorError("UNIT_INDEX_CHANGED", "Independent staging blocks publication recovery", {"path": path})
        gitio.put_entries(root, temporary, seal["entries"])
        after = gitio.entries(root, index=temporary)
        if {p: e for p, e in current.items() if p not in seal["paths"]} != {p: e for p, e in after.items() if p not in seal["paths"]}:
            raise CoordinatorError("INDEX_PRESERVATION", "Unrelated staged entries or stages would change")
        if (index.read_bytes() if index.exists() else None) != before:
            raise CoordinatorError("INDEX_RACE", "Real index changed despite the lock")
        descriptor = os.open(lock, os.O_WRONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "wb") as output:
            info = os.fstat(output.fileno())
            if (info.st_dev, info.st_ino) != (record["device"], record["inode"]):
                raise CoordinatorError("INDEX_OWNERSHIP", "Index lock inode changed")
            output.truncate(0)
            with temporary.open("rb") as source:
                shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        return hashlib.sha256(lock.read_bytes()).hexdigest()

    def install_index(self, checkout, seal, publication):
        record = publication["index_lock"]
        self.index_identity(checkout, seal, publication)
        if not self.pinned(record, record["lock"]) or not self.pinned(record, record["anchor"]):
            raise CoordinatorError("INDEX_OWNERSHIP", "Index lock ownership changed before installation")
        if hashlib.sha256(Path(record["lock"]).read_bytes()).hexdigest() != record["replacement_digest"]:
            raise CoordinatorError("INDEX_RACE", "Owned replacement bytes changed before installation")
        # Keep index.lock occupied through the durable index_updated checkpoint.
        # Install via another hard link to the same owned replacement inode.
        install = record["anchor"] + ".install"
        if os.path.lexists(install):
            if not self.pinned(record, install):
                raise CoordinatorError("INDEX_OWNERSHIP", "Foreign install link blocks recovery")
        else:
            os.link(record["lock"], install, follow_symlinks=False)
        os.replace(install, record["index"])
        fsync_dir(Path(record["index"]).parent)

    async def cleanup_anchor(self, publication):
        record = publication.get("index_lock")
        if record and self.pinned(record, record["anchor"]):
            if os.path.lexists(record["lock"]) and not self.pinned(record, record["lock"]):
                raise CoordinatorError("INDEX_LOCKED", "A foreign index lock prevents final cleanup; retry after its owner finishes")
            if self.pinned(record, record["lock"]):
                Path(record["lock"]).unlink()
            Path(record["anchor"]).unlink()
            fsync_dir(Path(record["index"]).parent)
            record["state"] = "checkpointed"
            self.store.save(publication)
