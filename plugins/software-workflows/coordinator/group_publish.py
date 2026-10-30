"""Versioned project seals and ordered, recoverable repository publication."""
from pathlib import Path
import time

from .locks import finish, finish_all, finish_thread
from . import gitio, participants
from .client import CoordinatorError
from .publish import Publisher, binding
from .store import digest, uid


class GroupPublisher:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.repo = Publisher(daemon)

    def current(self, unitid):
        return next((g for g in self.store.all("publication_group", unitid)
                     if g["phase"] not in ("superseded", "abandoned")), None)

    def view(self, unit, participant):
        return dict(unit, checkout=participant["checkout"], branch=participant["branch"], base=participant["base"])

    def dependencies(self, unit):
        result = {key: {} for key in participants.checkout_ids(self.store, unit)}
        for actor in self.store.all("actor", unit["id"]):
            for read in self.store.all("read", actor["id"]):
                key = read.get("checkout", actor["checkout"])
                for path, revision in read["revisions"].items():
                    if key not in result:
                        raise CoordinatorError("PATH_ESCAPE", "Recorded read is outside the pinned participants")
                    result[key][(path, revision)] = {"path": path, "revision": revision}
        return {key: sorted(values.values(), key=lambda value: (value["path"], value["revision"] or "")) for key, values in result.items()}

    async def check_dependencies(self, unitid, checkoutid, evidence, base=None):
        """Refuse a project seal whose writers read unexplained bytes or another unit's unpublished revision in this
        participant. A read of a path outside the ledger (ledger.unwatched) is no dependency, a read of a restore depends
        on whose bytes it restored (Publisher.provenance), and a git failure refuses."""
        paths = sorted({row["path"] for row in evidence})
        if not paths:
            return []
        checkout = self.store.get(checkoutid)
        await self.daemon.ledger.reconcile(checkout, paths)
        outside = await self.daemon.ledger.unwatched(checkout, paths)
        paths = [path for path in paths if path not in outside]
        if not paths:
            return []
        unknowns = self.store.unresolved(checkoutid, paths)
        if unknowns:
            raise CoordinatorError("UNKNOWN_DEPENDENCY", "Participant read scope includes unresolved UNKNOWN",
                                   {"root": checkout["root"], "unknowns": [u["id"] for u in unknowns],
                                    "paths": sorted({u["path"] for u in unknowns})})
        unknown, rows = set(), []
        for entry in evidence:
            if entry["revision"] and entry["path"] not in outside:
                row = self.store.db.execute("SELECT * FROM revisions WHERE id=?", (entry["revision"],)).fetchone()
                if not row or row["checkout"] != checkoutid or row["path"] != entry["path"] or row["attribution"] == "UNKNOWN":
                    unknown.add(entry["path"])
                    continue
                rows.append(row)
        if unknown:
            raise CoordinatorError("UNKNOWN_DEPENDENCY", "Recorded participant read is not a known revision",
                                   {"root": checkout["root"], "paths": sorted(unknown)})
        return await self.repo.dependency_proofs(unitid, rows, {checkoutid: base})

    async def seal(self, unitid, validation, order):
        unit = self.store.get(unitid)
        if unit["state"] not in ("open", "sealed") or self.current(unitid):
            raise CoordinatorError("UNIT_FROZEN", "Reopen the remaining publication before resealing")
        members = participants.members(self.store, unit)
        ids = [p["checkout"] for p in members]
        order = ids if order is None else order
        if not isinstance(order, list) or len(order) != len(ids) or set(order) != set(ids):
            raise CoordinatorError("PUBLICATION_ORDER", "Publication order must be a complete permutation of participant checkout IDs")
        for item in validation:
            if not isinstance(item, dict) or not all(k in item for k in ("command", "exit_code", "inputs", "scope")):
                raise CoordinatorError("VALIDATION_EVIDENCE", "Validation requires command, exit_code, actual inputs and scope")
            if item["scope"] not in ("mixed-checkout", "candidate-objects"):
                raise CoordinatorError("VALIDATION_SCOPE", "Validation must identify its actual scope")
        attemptid, sealid = uid("seal_attempt"), uid("seal")
        self.store.put("seal_attempt", attemptid, {"unit": unitid, "state": "started", "generation": self.store.generation,
                       "prior_state": unit["state"], "prior_seal": unit.get("seal"), "created": time.time()}, unitid)
        unit.update(state="sealing", seal_attempt=attemptid)
        self.store.save(unit)
        try:
            return await finish(self.freeze(unit, members, order, validation, sealid))
        except BaseException:
            self.repo.interrupt_seal(unitid, "project candidate did not finish freezing")
            raise

    async def freeze(self, unit, members, order, validation, sealid):
        await self.repo.quiescent(unit)
        operations = list(self.store.all("operation", unit["id"]))
        dependencies = self.dependencies(unit)
        vector, seals = [], []
        prefix = unit.get("published_prefix", {})
        selected, bases = {}, {}
        # Pin every participant, including unchanged ones, before evaluating a
        # cross-repository operation or read. An installed prefix keeps its base.
        for member in members:
            checkout = self.store.get(member["checkout"])
            metadata = await finish_thread(self.repo.repository_identity, checkout)
            if metadata["branch"] != member["branch"]:
                raise CoordinatorError("BRANCH_CHANGED", "Participant branch changed", {"root": checkout["root"]})
            paths = self.daemon.ledger.mutation_scope(unit["id"], checkout["id"])
            rows, base = [], member["base"]
            if checkout["id"] in prefix:
                saved = prefix[checkout["id"]]
                part, journal = self.store.get(saved["seal"]), self.store.get(saved["publication"])
                if not await finish_thread(self.repo.installed_tip, checkout, part, journal, metadata["base"]):
                    raise CoordinatorError("PUBLICATION_REF_DIVERGED", "Completed prefix is no longer installed")
                base = part["base"]
            else:
                rows = await self.repo.scope_rows(self.view(unit, member), sealing=True) if paths else []
                if metadata["base"] != base:
                    old, new = await finish_all(finish_thread(gitio.entries, checkout["root"], base),
                                               finish_thread(gitio.entries, checkout["root"], metadata["base"]))
                    for row in rows:
                        if old.get(row["path"]) != new.get(row["path"]):
                            operation = self.store.get(row["operation"])
                            if not unit.get("reopened") or operation.get("completed", 0) <= unit["reopened"]:
                                raise CoordinatorError("BASE_SCOPE_CHANGED", "Changed base overlaps frozen work; a new verified edit after reopening is required",
                                                       {"root": checkout["root"], "path": row["path"]})
                    base = metadata["base"]
            selected[checkout["id"]] = (metadata, paths, rows)
            bases[checkout["id"]] = base
        operation_proofs = await self.repo.dependency_check(unit, operations, bases)
        read_proofs = {key: await self.check_dependencies(unit["id"], key, values, bases[key])
                       for key, values in dependencies.items()}
        for member in members:
            checkout = self.store.get(member["checkout"])
            metadata, paths, rows = selected[checkout["id"]]
            if checkout["id"] in prefix:
                saved = prefix[checkout["id"]]
                part = self.store.get(saved["seal"])
                journal = self.store.get(saved["publication"])
                # The prefix is already installed. Later unexplained working
                # bytes remain UNKNOWN, but cannot replace its immutable receipt
                # vector or prevent publication of the writable remainder.
                await self.daemon.ledger.reconcile(checkout, paths)
                scope = set(paths)
                latest = {row["path"]: dict(row) for row in self.store.db.execute(
                    "SELECT * FROM revisions WHERE checkout=? AND unit=? ORDER BY rowid", (checkout["id"], unit["id"]))
                    if row["path"] in scope}
                if paths != part["paths"] or [latest.get(path) for path in paths] != part["rows"]:
                    raise CoordinatorError("PREFIX_PUBLISHED", "Verified work changed in an already published participant; it cannot be omitted",
                                           {"root": checkout["root"], "checkout": checkout["id"], "commit": journal["commit"]})
                if not await finish_thread(self.repo.installed_tip, checkout, part, journal, metadata["base"]):
                    raise CoordinatorError("PUBLICATION_REF_DIVERGED", "Completed prefix is no longer installed")
                vector.append(dict(saved["vector"], commit=journal["commit"], publication=journal["id"]))
                continue
            base = bases[checkout["id"]]
            partid = uid("seal_part")
            candidate, entries = (None, {})
            if rows:
                candidate, entries = await finish_thread(self.repo.build_tree, checkout, base, rows,
                    self.store.root / "indexes" / (partid + ".index"))
                if candidate == await finish_thread(gitio.text, checkout["root"], ["rev-parse", base + "^{tree}"]):
                    candidate = None
            provenance = digest([{k: row[k] for k in ("id", "path", "previous", "operation", "blob", "mode")} for row in rows])
            part = dict(repository=checkout["repository"], checkout=checkout["id"], root=checkout["root"], identity=checkout["identity"],
                        unit=unit["id"], base=base, branch=member["branch"], candidate=candidate, unchanged=candidate is None,
                        paths=paths, rows=rows, entries=entries, provenance=provenance, validation=validation,
                        dependencies=dependencies[checkout["id"]], dependency_digest=digest(dependencies[checkout["id"]]),
                        dependency_proofs=read_proofs[checkout["id"]] + [p for p in operation_proofs if p["checkout"] == checkout["id"]],
                        validation_digest=digest(validation), seal=partid, created=time.time())
            seals.append(part)
            vector.append({k: part[k] for k in ("checkout", "root", "identity", "repository", "branch", "base", "candidate", "unchanged", "provenance", "dependencies", "dependency_digest", "validation_digest", "seal")})
        # Every candidate exists before final validation. Nothing is sealable on
        # a failed participant, and no empty commits are manufactured.
        for part in seals:
            if part["paths"]:
                await self.repo.scope_rows(dict(unit, checkout=part["checkout"]), part, sealing=True)
            metadata = await finish_thread(self.repo.repository_identity, self.store.get(part["checkout"]))
            if metadata["base"] != part["base"] or metadata["branch"] != part["branch"]:
                raise CoordinatorError("STALE_BASE", "Participant base changed while freezing", {"root": part["root"]})
        for key, saved in prefix.items():
            checkout = self.store.get(key)
            metadata = await finish_thread(self.repo.repository_identity, checkout)
            if metadata["branch"] != self.store.get(saved["seal"])["branch"] or not await finish_thread(
                    self.repo.installed_tip, checkout, self.store.get(saved["seal"]), self.store.get(saved["publication"]), metadata["base"]):
                raise CoordinatorError("PUBLICATION_REF_DIVERGED", "Completed prefix moved while freezing")
        seal = dict(binding_version=2, unit=unit["id"], seal=sealid, project_revision=unit["project_revision"],
                    generation=unit.get("generation", 1), publication_order=order, vector=vector,
                    validation=validation, validation_digest=digest(validation), created=time.time())
        with self.store.transaction():
            for part in seals:
                self.store.put("seal_part", part["seal"], part, unit["id"])
            self.store.put("seal", sealid, seal, unit["id"])
            unit.update(state="sealed", seal=sealid, review_required=True)
            self.store.save(unit)
            attempt = self.store.get(unit["seal_attempt"])
            attempt.update(state="completed", seal=sealid, completed=time.time())
            self.store.save(attempt)
            self.store.event("unit/sealed", unit["id"], binding(seal))
        return self.store.get(sealid)

    async def diff(self, unitid):
        unit = self.store.get(unitid)
        seal = self.store.get(unit["seal"])
        data = []
        for item in seal["vector"]:
            raw = await finish_thread(gitio.git, item["root"], ["diff", "--no-ext-diff", "--no-textconv", item["base"], item["candidate"]]) if item["candidate"] else None
            data.append(dict(root=item["root"], checkout=item["checkout"], base=item["base"], candidate=item["candidate"],
                             unchanged=item["unchanged"], commit=item.get("commit"), diff=raw.stdout.decode("utf-8", "strict") if raw else ""))
        return {"binding": binding(seal), "diff": "\n".join("Repository: " + d["root"] + "\n" + d["diff"] for d in data), "participants": data}

    async def preflight(self, unit, item, publication=None):
        checkout, part = self.store.get(item["checkout"]), self.store.get(item["seal"])
        config = await finish_thread(self.repo.publication_guards, checkout, part)
        await self.check_dependencies(unit["id"], checkout["id"], part.get("dependencies", []), part["base"])
        # Recheck operation dependencies against the same sealed vector, never a
        # newer branch tip. Participants already published retain their old base.
        seal = self.store.get(unit["seal"])
        bases = {v["checkout"]: v["base"] for v in seal["vector"]}
        operations = list(self.store.all("operation", unit["id"]))
        await self.repo.dependency_check(unit, operations, bases)
        if part["paths"]:
            await self.repo.scope_rows(self.view(unit, part), part)
        base, real = await finish_all(finish_thread(gitio.entries, checkout["root"], part["base"]),
                                          finish_thread(gitio.entries, checkout["root"]))
        for path in part["paths"]:
            if real.get(path, []) != base.get(path, []):
                raise CoordinatorError("UNIT_INDEX_CHANGED", "Participant has independent staged work", {"root": checkout["root"], "path": path})
        index = Path(await finish_thread(gitio.text, checkout["root"], ["rev-parse", "--path-format=absolute", "--git-path", "index"]))
        lock = str(index) + ".lock"
        if Path(lock).exists() and not (publication and publication.get("index_lock") and self.repo.pinned(publication["index_lock"], lock)):
            raise CoordinatorError("INDEX_LOCKED", "Foreign index lock blocks the complete group", {"root": checkout["root"]})
        await finish_thread(self.repo.publication_guards, checkout, part)
        return {"config_digest": config, "base_index_entries": {p: real.get(p, []) for p in part["paths"]}}

    async def publish(self, unitid, reviewid, metadata):
        existing = self.current(unitid)
        if existing:
            if existing["review"] != reviewid or existing["metadata"] != metadata:
                raise CoordinatorError("PUBLICATION_INPUT_CHANGED", "Retry must preserve group review and commit metadata")
            return await self.reconcile(existing)
        unit = self.store.get(unitid)
        if unit["state"] != "sealed":
            raise CoordinatorError("UNIT_NOT_SEALED", "Group publication needs a complete-vector seal")
        seal, review = self.store.get(unit["seal"]), self.store.get(reviewid)
        if review["_kind"] != "review" or review.get("valid") is False or review.get("verdict") != "pass" or any(review.get(k) != v for k, v in binding(seal).items()):
            raise CoordinatorError("REVIEW_BINDING", "A current complete-vector review is required")
        required = {"message", "author_name", "author_email", "author_date", "committer_name", "committer_email", "committer_date"}
        if set(metadata) != required or any(not isinstance(v, str) or not v or "\0" in v for v in metadata.values()):
            raise CoordinatorError("PUBLICATION_METADATA", "Complete explicit commit metadata is required")
        await self.repo.quiescent(unit)
        prepared = {}
        for item in seal["vector"]:
            if not item.get("commit"):
                prepared[item["checkout"]] = await self.preflight(unit, item)
        groupid, journals = uid("publication_group"), {}
        with self.store.transaction():
            for item in seal["vector"]:
                if item.get("commit"):
                    journals[item["checkout"]] = item["publication"]
                elif not item["unchanged"]:
                    key = uid("publication")
                    self.store.put("publication", key, dict(unit=unitid, checkout=item["checkout"], group=groupid,
                        seal=item["seal"], review=reviewid, metadata=metadata, phase="prepared", created=time.time(), **prepared[item["checkout"]]), unitid)
                    journals[item["checkout"]] = key
            self.store.put("publication_group", groupid, dict(unit=unitid, seal=seal["id"], review=reviewid, metadata=metadata,
                journals=journals, phase="publishing", publication_state="publishing", created=time.time()), unitid)
            unit.update(state="publishing", publication_state="publishing")
            self.store.save(unit)
        return await self.reconcile(self.store.get(groupid))

    def progress(self, group, final=False):
        journals = [self.store.get(key) for key in group["journals"].values()]
        applied = [p for p in journals if p["phase"] in ("ref_updated", "index_pending", "index_updated", "complete")]
        complete = final and all(p["phase"] == "complete" for p in journals)
        # Persist a blocking state before any ref can move, and never downgrade
        # it during a ref-update/checkpoint gap or uncertain recovery.
        group["publication_state"] = "complete" if complete else "partially_published" if applied else "publishing"
        group["phase"] = group["publication_state"]
        self.store.save(group)
        unit = self.store.get(group["unit"])
        unit.update(publication_state=group["publication_state"], state="published" if complete else "partially_published" if applied else "publishing")
        self.store.save(unit)
        return group

    async def reconcile(self, group):
        if group["phase"] in ("superseded", "abandoned", "complete"):
            return group
        self.progress(group)
        unit, seal = self.store.get(group["unit"]), self.store.get(group["seal"])
        review = self.store.get(group["review"])
        if review.get("valid") is False or any(review.get(k) != v for k, v in binding(seal).items()):
            raise CoordinatorError("REVIEW_BINDING", "Old review cannot continue this publication generation")
        items = {item["checkout"]: item for item in seal["vector"]}
        try:
            # Account for ref-update/checkpoint crash gaps before interpreting
            # the prefix. The repository journal also repairs its exact index.
            for key in seal["publication_order"]:
                journalid = group["journals"].get(key)
                if not journalid:
                    continue
                journal = self.store.get(journalid)
                if journal["phase"] == "commit_created":
                    checkout, part = self.store.get(key), self.store.get(journal["seal"])
                    tip = await finish_thread(gitio.text, checkout["root"], ["rev-parse", part["branch"]])
                    if await finish_thread(self.repo.installed_tip, checkout, part, journal, tip):
                        journal["phase"] = "ref_updated"
                        self.store.save(journal)
                if journal["phase"] in ("ref_updated", "index_pending", "index_updated"):
                    await self.repo.reconcile(journal)
            self.progress(group)
            for key in seal["publication_order"]:
                journal = self.store.get(group["journals"].get(key), required=False)
                if not journal or journal["phase"] != "complete":
                    await self.preflight(unit, items[key], journal)
            # Establish every required owned lock before changing any ref.
            for key in sorted(group["journals"]):
                journal = self.store.get(group["journals"][key])
                if journal["phase"] != "complete":
                    await finish(self.repo.ensure_index_lock(self.store.get(key), journal))
            for key in seal["publication_order"]:
                journalid = group["journals"].get(key)
                if journalid:
                    await self.repo.reconcile(self.store.get(journalid))
                    self.progress(group)
            async with self.daemon.custody.condition:
                with self.store.transaction():
                    self.progress(group, final=True)
                    for claim in self.store.all("claim", unit["id"]):
                        if claim["state"] != "released":
                            claim["state"] = "released"
                            self.store.save(claim)
                    self.store.event("publication/group_complete", group["id"], {"journals": group["journals"]})
                self.daemon.custody.condition.notify_all()
            return self.store.get(group["id"])
        except BaseException as exc:
            self.progress(group)
            group.update(refusal=getattr(exc, "code", type(exc).__name__), recovery_required=True)
            self.store.save(group)
            raise

    async def reopen(self, unitid, abandon=False):
        unit = self.store.get(unitid)
        await self.repo.quiescent(unit, allow_quarantined=True)
        group = self.current(unitid)
        prefix = dict(unit.get("published_prefix", {}))
        if group:
            if group["phase"] == "complete":
                raise CoordinatorError("UNIT_PUBLISHED", "Completed unit cannot reopen")
            seal = self.store.get(group["seal"])
            items = {v["checkout"]: v for v in seal["vector"]}
            for key, journalid in group["journals"].items():
                journal = await self.repo.abandon(unitid, self.store.get(journalid))
                if journal["phase"] == "complete":
                    prefix[key] = {"publication": journalid, "seal": journal["seal"], "vector": items[key], "commit": journal["commit"]}
                elif journal["phase"] != "abandoned":
                    raise CoordinatorError("PUBLICATION_UNCERTAIN", "Cannot supersede a journal without proving it unapplied")
            group.update(phase="abandoned" if abandon else "superseded", superseded=time.time(), completed_prefix=prefix)
            self.store.save(group)
        with self.store.transaction():
            for review in self.store.all("review", unitid):
                review.update(valid=False, invalidation="unit reopened into a new vector generation")
                self.store.save(review)
            unit = self.store.get(unitid)
            unit.update(state="open", generation=unit.get("generation", 1) + 1, reopened=time.time(),
                        previous_seal=unit.pop("seal", None), published_prefix=prefix, review_required=True,
                        publication_state="partially_published" if prefix else "")
            self.store.save(unit)
            self.store.event("unit/reopened", unitid, {"completed_prefix": prefix, "abandonment": abandon})
        return unit
