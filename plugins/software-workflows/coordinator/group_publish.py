"""Versioned project seals and ordered, recoverable repository publication."""
from pathlib import Path
import time

from .locks import finish, finish_all, finish_thread
from . import dependencies as dependency_evidence, gitio, participants, transfers
from .client import CoordinatorError
from .publish import Publisher, binding, check_validation, review_refusal
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

    def dependencies(self, unit, evaluation):
        keys = participants.checkout_ids(self.store, unit)
        if set(evaluation["reads"]) - set(keys):
            raise CoordinatorError("PATH_ESCAPE", "Recorded read is outside the pinned participants")
        return {key: evaluation["reads"].get(key, []) for key in keys}

    async def check_dependencies(self, unit, rows=None, bases=None):
        return (await self.repo.dependency_preflight(unit) if rows is None
                else await self.repo.dependency_check(unit, rows, bases))

    async def seal(self, unitid, validation, order):
        unit = self.store.get(unitid)
        from .runs import refuse_empty_seal
        refuse_empty_seal(self.daemon, unit)
        if unit["state"] not in ("open", "sealed") or self.current(unitid):
            raise CoordinatorError("UNIT_FROZEN", "Reopen the remaining publication before resealing")
        members = participants.members(self.store, unit)
        ids = [p["checkout"] for p in members]
        order = ids if order is None else order
        if not isinstance(order, list) or len(order) != len(ids) or set(order) != set(ids):
            raise CoordinatorError("PUBLICATION_ORDER", "Publication order must be a complete permutation of participant checkout IDs")
        check_validation(validation)
        await self.daemon.custody.acquire_pending(unitid)
        unit = self.store.get(unitid)
        if unit["state"] not in ("open", "sealed") or self.current(unitid):
            raise CoordinatorError("UNIT_FROZEN", "Reopen the remaining publication before resealing")
        attemptid, sealid = uid("seal_attempt"), uid("seal")
        self.store.put("seal_attempt", attemptid, {"unit": unitid, "state": "started", "generation": self.store.generation,
                       "prior_state": unit["state"], "prior_seal": unit.get("seal"),
                       "prior_review_required": unit.get("review_required", True), "created": time.time()}, unitid)
        unit.update(state="sealing", seal_attempt=attemptid)
        self.store.save(unit)
        try:
            return await finish(self.freeze(unit, members, order, validation, sealid))
        except BaseException:
            self.repo.interrupt_seal(unitid, "project candidate did not finish freezing")
            raise

    async def freeze(self, unit, members, order, validation, sealid):
        await self.repo.quiescent(unit)
        vector, seals = [], []
        prefix = unit.get("published_prefix", {})
        selected, bases, pins = {}, {}, {}
        # Pin every participant, including unchanged ones, before evaluating a
        # cross-repository operation or read. An installed prefix keeps its base.
        for member in members:
            checkout = self.store.get(member["checkout"])
            metadata = await finish_thread(self.repo.repository_identity, checkout)
            if metadata["branch"] != member["branch"]:
                raise CoordinatorError("BRANCH_CHANGED", "Participant branch changed", {"root": checkout["root"]})
            paths = transfers.scope(self.daemon.ledger, unit["id"], checkout["id"])
            rows, base = [], member["base"]
            if checkout["id"] in prefix:
                saved = prefix[checkout["id"]]
                part, journal = self.store.get(saved["seal"]), self.store.get(saved["publication"])
                if not await finish_thread(self.repo.installed_tip, checkout, part, journal, metadata["base"]):
                    raise CoordinatorError("PUBLICATION_REF_DIVERGED", "Completed prefix is no longer installed")
                base, rows = part["base"], part["rows"]
                pins.update({part["id"]: part["_revision"], journal["id"]: journal["_revision"]})
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
        # A handed-away participant is pin-only. Empty is a unit-wide decision,
        # after collecting every participant (including an installed prefix).
        transfers.require_nonempty(self.store, unit["id"], [p for _, paths, _ in selected.values() for p in paths])
        evaluation = await self.check_dependencies(unit, [row for _, _, rows in selected.values() for row in rows], bases)
        evaluation["tokens"].update(pins)
        dependencies = self.dependencies(unit, evaluation)
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
                receipts = [self.store.db.execute("SELECT * FROM revisions WHERE id=?", (row["id"],)).fetchone()
                            for row in part["rows"]]
                changed = any(self.store.db.execute(
                    "SELECT 1 FROM revisions WHERE checkout=? AND path=? AND unit=? AND attribution='exact' "
                    "AND rowid>(SELECT rowid FROM revisions WHERE id=?) LIMIT 1",
                    (checkout["id"], row["path"], unit["id"], row["id"])).fetchone() for row in part["rows"])
                grants = transfers.snapshot(self.store, unit["id"], checkout["id"], part["paths"])
                if (paths != part["paths"] or [dict(row) if row else None for row in receipts] != part["rows"]
                        or changed or grants != part.get("transfer_proofs", [])):
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
            grants = transfers.snapshot(self.store, unit["id"], checkout["id"], paths)
            provenance = digest({"rows": [{k: row[k] for k in ("id", "path", "previous", "operation", "blob", "mode")} for row in rows], "transfers": grants})
            part = dict(repository=checkout["repository"], checkout=checkout["id"], root=checkout["root"], identity=checkout["identity"],
                        unit=unit["id"], base=base, branch=member["branch"], candidate=candidate, unchanged=candidate is None,
                        paths=paths, rows=rows, entries=entries, provenance=provenance, transfer_proofs=grants, validation=validation,
                        dependencies=dependencies[checkout["id"]], dependency_digest=digest(dependencies[checkout["id"]]),
                        dependency_proofs=[p for p in evaluation["proofs"] if p["checkout"] == checkout["id"]],
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
            dependency_evidence.validate(self.store, evaluation, [row for part in seals for row in part["rows"]])
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

    async def preflight(self, unit, item, publication=None, evaluation=None):
        checkout, part = self.store.get(item["checkout"]), self.store.get(item["seal"])
        evaluation = evaluation or await self.check_dependencies(unit)
        if item["unchanged"]:
            await finish_thread(self.repo.publication_pin, checkout, part)
            dependency_evidence.validate(self.store, evaluation)
            return {"config_digest": None, "base_index_entries": {}}
        config = await finish_thread(self.repo.publication_guards, checkout, part)
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
        dependency_evidence.validate(self.store, evaluation, part["rows"])
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
        seal = self.store.get(unit["seal"])
        refusal = review_refusal(self.store, reviewid, seal)
        if refusal:
            raise refusal
        required = {"message", "author_name", "author_email", "author_date", "committer_name", "committer_email", "committer_date"}
        if set(metadata) != required or any(not isinstance(v, str) or not v or "\0" in v for v in metadata.values()):
            raise CoordinatorError("PUBLICATION_METADATA", "Complete explicit commit metadata is required")
        await self.repo.quiescent(unit)
        evaluation = await self.check_dependencies(unit)
        prepared = {}
        for item in seal["vector"]:
            if not item.get("commit"):
                prepared[item["checkout"]] = await self.preflight(unit, item, evaluation=evaluation)
        groupid, journals = uid("publication_group"), {}
        with self.store.transaction():
            dependency_evidence.validate(self.store, evaluation)
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
            remaining = any(self.store.get(key)["phase"] != "complete" for key in group["journals"].values())
            evaluation = await self.check_dependencies(unit) if remaining or not group["journals"] else None
            for key in seal["publication_order"]:
                journal = self.store.get(group["journals"].get(key), required=False)
                if journal and journal["phase"] == "complete":
                    continue
                if evaluation:
                    await self.preflight(unit, items[key], journal, evaluation)
                else:  # every journal is complete: an unchanged participant is still pinned to its sealed base
                    await finish_thread(self.repo.publication_pin, self.store.get(key), self.store.get(items[key]["seal"]))
            # Establish every required owned lock before changing any ref.
            for key in sorted(group["journals"]):
                journal = self.store.get(group["journals"][key])
                if journal["phase"] != "complete":
                    await finish(self.repo.ensure_index_lock(self.store.get(key), journal))
            for key in seal["publication_order"]:
                journalid = group["journals"].get(key)
                if journalid:
                    journal = self.store.get(journalid)
                    if evaluation and journal["phase"] != "complete":
                        try:
                            dependency_evidence.validate(self.store, evaluation)
                        except CoordinatorError as exc:
                            if exc.code != "REVISION_CHANGED":
                                raise
                            evaluation = await self.check_dependencies(unit)
                    await self.repo.reconcile(journal, evaluation)
                    self.progress(group)
            async with self.daemon.custody.condition:
                with self.store.transaction():
                    self.progress(group, final=True)
                    transfers.record_group(self.store, group)
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
        await self.repo.quiescent(unit, allow_quarantined=True, history=False)
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
