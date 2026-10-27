"""Read-only native discovery and atomic activation of immutable project revisions.

Native projects are never mutated here. Prepared checkout ledgers are reusable
after interruption; only an activated revision can become a unit's membership.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import time

from .locks import finish_thread
from .client import CoordinatorError
from . import gitio, native, processes
from .ledger import visible, normalize
from .store import digest, uid


def snapshot(project):
    try:
        roots = [entry["path"] for entry in project["roots"]]
        if not all(isinstance(p, str) and os.path.isabs(p) for p in roots):
            raise ValueError("absolute roots required")
        return {"project": project["id"], "name": project["name"],
                "roots": roots, "updated_at": project["updatedAt"]}
    except (KeyError, TypeError, ValueError) as exc:
        raise CoordinatorError("PROJECT_PROTOCOL", "Native project snapshot is incomplete") from exc


def containing(root, cwd):
    root, cwd = Path(root).resolve(), Path(cwd).resolve()
    return root == cwd or root in cwd.parents


class Catalog:
    """A short-lived, threadless native connection; failures never mean no projects."""
    def __init__(self, store, fixture=None):
        self.store, self.fixture = store, fixture
        self.counter = 0

    async def call(self, method, params=None):
        params = params or {}
        self.schema.validate(method, params)
        self.counter += 1
        ident = self.counter
        self.process.stdin.write((json.dumps({"id": ident, "method": method, "params": params}) + "\n").encode())
        await self.process.stdin.drain()
        try:
            async with asyncio.timeout(60):
                while True:
                    line = await self.process.stdout.readline()
                    if not line:
                        raise CoordinatorError("PROJECT_DISCOVERY", "Native project discovery ended without a response", operational=True)
                    value = json.loads(line)
                    if value.get("id") != ident:
                        continue
                    if "error" in value:
                        raise CoordinatorError("PROJECT_DISCOVERY", "Native project discovery refused the request",
                                               {"method": method, "native_code": value["error"].get("code")})
                    return value["result"]
        except (TimeoutError, ValueError, KeyError) as exc:
            raise CoordinatorError("PROJECT_DISCOVERY", "Native project discovery did not complete", operational=True) from exc

    async def __aenter__(self):
        from .admission import Admission
        directory, _ = await Admission(self.store).schema()
        self.schema = native.Schema(directory)
        if not any(native.METHODS[m] in self.schema.files for m in native.PROJECT_METHODS):
            raise CoordinatorError("PROJECT_API_UNAVAILABLE", "Installed Codex has no native project API")
        env, config = native.environment(), {}
        if self.fixture:
            from .fixture import runtime_configuration
            home, config = runtime_configuration(self.fixture, "discovery")
            env["CODEX_HOME"] = str(home)
        self.process = await asyncio.create_subprocess_exec(native.native_codex(), "app-server", "--listen", "stdio://",
            *native.override_args(config), env=env, cwd=self.store.root,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=16 << 20, start_new_session=True)
        birth = processes.identity(self.process.pid)
        self.record = self.store.put("project_discovery", uid("discovery"), {
            "pid": self.process.pid, "birth": birth and birth["birth"], "state": "running"})
        try:
            await self.call("initialize", {"clientInfo": {"name": "software-workflows-projects", "version": "2.4.0"},
                                           "capabilities": {"experimentalApi": True}})
            self.process.stdin.write(b'{"method":"initialized"}\n')
            await self.process.stdin.drain()
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_):
        from .locks import finish
        await finish(self.close())

    async def close(self):
        from .locks import retire
        await retire(self._close)

    async def _close(self):
        if self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
        record = self.store.get(self.record["id"])
        record.update(state="stopped", exit_code=self.process.returncode)
        self.store.save(record)


class Projects:
    def __init__(self, daemon):
        self.daemon, self.store, self.ledger = daemon, daemon.store, daemon.ledger

    async def listing(self, params):
        try:
            async with Catalog(self.store, params.get("fixture")) as catalog:
                return await self.pages(catalog)
        except CoordinatorError as exc:
            if exc.code == "PROJECT_API_UNAVAILABLE":
                return {"data": [], "next_cursor": None, "supported": False}
            raise

    async def pages(self, catalog):
        data, cursors = [], set()
        args = {"limit": 100, "sortKey": "position", "sortDirection": "asc"}
        while True:
            result = await catalog.call("project/list", args)
            if not isinstance(result.get("data"), list):
                raise CoordinatorError("PROJECT_PROTOCOL", "Native project list is incomplete")
            for entry in result["data"]:
                snapshot(entry)
                data.append(entry)
            cursor = result.get("nextCursor")
            if not cursor:
                break
            if cursor in cursors:
                raise CoordinatorError("PROJECT_PROTOCOL", "Native project pagination repeated a cursor")
            cursors.add(cursor)
            args["cursor"] = cursor
        return {"data": data, "next_cursor": None}

    async def resolve(self, params):
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            raise CoordinatorError("PROJECT_CWD", "Project selection requires an absolute target cwd")
        # Lead tools reuse the native catalog captured at registration. Dispatch
        # and explicit registration still discover live membership. An absent
        # cache is never evidence of an empty native project list.
        cached = self.store.get("registered_project_catalog", required=False) if params.get("registered") else None
        covered = any(any(containing(b["root"], cwd) for b in self.store.get(p["revision"])["bindings"])
                      for p in self.store.all("project")) if cached else False
        catalog_projects = cached["data"] if covered else None
        if params.get("project"):
            project = next((p for p in catalog_projects or [] if p["id"] == params["project"]), None)
            if project is None:
                async with Catalog(self.store, params.get("fixture")) as catalog:
                    project = (await catalog.call("project/read", {"projectId": params["project"]}))["project"]
            candidates = [project] if any(containing(p, cwd) for p in snapshot(project)["roots"]) else []
            if not candidates:
                raise CoordinatorError("PROJECT_SCOPE", "Target cwd belongs to none of the selected project's roots",
                                       {"project": project["id"], "cwd": cwd, "roots": snapshot(project)["roots"]})
        else:
            projects = catalog_projects if catalog_projects is not None else (await self.listing(params))["data"]
            candidates = [p for p in projects if any(containing(r, cwd) for r in snapshot(p)["roots"])]
            if len(candidates) > 1 and params.get("dispatch_cwd"):
                narrowed = [p for p in candidates if any(containing(r, params["dispatch_cwd"]) for r in snapshot(p)["roots"])]
                if narrowed:
                    candidates = narrowed
        if len(candidates) > 1:
            raise CoordinatorError("PROJECT_AMBIGUOUS", "Select a native project explicitly",
                                   {"candidates": [{"id": p["id"], "name": p["name"]} for p in candidates], "cwd": cwd})
        if not candidates:
            return {"project": None, "cwd": str(Path(cwd).resolve()), "singleton": True}
        chosen = candidates[0]
        roots = snapshot(chosen)["roots"]
        thread_cwd = max((r for r in roots if containing(r, cwd)), key=lambda r: len(Path(r).parts))
        return {"project": {"id": chosen["id"], "name": chosen["name"]}, "cwd": str(Path(cwd).resolve()),
                "thread_cwd": thread_cwd, "runtime_workspace_roots": roots, "singleton": False,
                "registered": self.store.get("project_" + digest(chosen["id"]), required=False) is not None
                              and (not params.get("registered") or catalog_projects is not None)}

    def hook_project(self, checkoutid, folder):
        """Hooks cannot choose projects: only an unambiguous stored revision wins."""
        candidates = [self.store.get(p["revision"]) for p in self.store.all("project")]
        candidates = [r for r in candidates if any(b["checkout"] == checkoutid for b in r["bindings"])]
        if len(candidates) > 1 and folder:
            narrowed = [r for r in candidates if any(containing(b["root"], folder) for b in r["bindings"])]
            if narrowed:
                candidates = narrowed
        return candidates[0]["project"] if len(candidates) == 1 else None

    def resolve_roots(self, native_snapshot):
        bindings, directories, checkouts = [], {}, {}
        for root in native_snapshot["roots"]:
            try:
                physical = Path(root).resolve(strict=True)
                info = physical.stat()
                if not physical.is_dir():
                    raise OSError("root is not a directory")
                top = Path(gitio.text(physical, ["rev-parse", "--show-toplevel"])).resolve()
            except (CoordinatorError, OSError) as exc:
                raise CoordinatorError("PROJECT_ROOT_NOT_GIT", "Native project root is not inside a Git work tree", {"root": root}) from exc
            key = (info.st_dev, info.st_ino)
            if key in directories:
                raise CoordinatorError("PROJECT_DUPLICATE_ROOT", "Native roots resolve to the same physical directory",
                                       {"roots": [directories[key], root]})
            directories[key] = root
            metadata = self.ledger._metadata(str(top))
            prefix = physical.relative_to(top).as_posix()
            if prefix == ".":
                prefix = ""
            if prefix:
                normalize(dict(root=str(top), case_sensitive=True), prefix)
            bindings.append({"root": root, "physical_root": str(physical), "directory": list(key),
                             "checkout_root": str(top), "identity": metadata["identity"],
                             "repository": metadata["repository"], "prefix": prefix})
            checkouts[str(top)] = dict(metadata, root=str(top))
        if not checkouts:
            raise CoordinatorError("PROJECT_EMPTY", "Native project has no usable participant", {"project": native_snapshot["project"]})
        return bindings, checkouts

    def validate_ownership(self, prepared, existing):
        by_root = {c["root"]: c for c in existing}
        for root, checkout in prepared.items():
            prior = by_root.get(root)
            if prior and (prior["identity"] != checkout["identity"] or prior["repository"] != checkout["repository"]):
                raise CoordinatorError("CHECKOUT_IDENTITY_CHANGED", "Registered project root was replaced", {"root": root})
            by_root[root] = dict(prior or {}, **checkout)
        all_checkouts = list(by_root.values())
        for outer in all_checkouts:
            inner = [c for c in all_checkouts if Path(outer["root"]) in Path(c["root"]).parents]
            if not inner:
                continue
            paths = visible(outer)
            paths.update(outer.get("known_paths", []))
            for child in inner:
                prefix = Path(child["root"]).relative_to(outer["root"]).as_posix()
                owned = sorted(p for p in paths if p == prefix or p.startswith(prefix + "/"))
                if owned:
                    raise CoordinatorError("PROJECT_OWNERSHIP", "Outer Git view owns paths inside another checkout",
                                           {"roots": [outer["root"], child["root"]], "paths": owned})

    async def register(self, params):
        projectid = params.get("project")
        if not projectid:
            selected = await self.resolve(params)
            if not selected["project"]:
                raise CoordinatorError("PROJECT_NOT_FOUND", "No native project contains target cwd", {"cwd": params.get("cwd")})
            projectid = selected["project"]["id"]
        async with self.ledger.registration:
            async with Catalog(self.store, params.get("fixture")) as catalog:
                initial = snapshot((await catalog.call("project/read", {"projectId": projectid}))["project"])
                inventory = await self.pages(catalog)
                preparationid = "project_preparation_" + digest(initial)
                preparation = self.store.get(preparationid, required=False)
                if not preparation:
                    self.store.put("project_preparation", preparationid, dict(initial, state="preparing", checkouts=[]))
                try:
                    bindings, prepared = await finish_thread(self.resolve_roots, initial)
                    existing = list(self.store.all("checkout"))
                    for c in existing:
                        c["known_paths"] = [r[0] for r in self.store.db.execute("SELECT path FROM heads WHERE checkout=?", (c["id"],))]
                    await finish_thread(self.validate_ownership, prepared, existing)
                    registered = {}
                    for root in prepared:
                        try:
                            registered[root] = await self.ledger._register(root)
                        except CoordinatorError as exc:
                            raise CoordinatorError(exc.code, str(exc), dict(exc.details, root=root), exc.operational) from exc
                        record = self.store.get(preparationid)
                        record.update(state="preparing", checkouts=[c["id"] for c in registered.values()])
                        self.store.save(record)
                    final = snapshot((await catalog.call("project/read", {"projectId": projectid}))["project"])
                    if initial != final:
                        raise CoordinatorError("PROJECT_CHANGED", "Native membership changed during registration; retry", {"before": initial, "after": final})
                    final_bindings, final_checkouts = await finish_thread(self.resolve_roots, final)
                    if bindings != final_bindings or prepared != final_checkouts:
                        raise CoordinatorError("PROJECT_ROOT_CHANGED", "Project roots or repository bases changed during registration",
                                               {"roots": initial["roots"]})
                    await finish_thread(self.validate_ownership, final_checkouts, existing)
                    for binding in bindings:
                        binding["checkout"] = registered[binding["checkout_root"]]["id"]
                    revisionid = "project_revision_" + digest([initial, bindings])
                    nativeid = "project_" + digest(projectid)
                    with self.store.transaction():
                        self.store.put("project_catalog", "registered_project_catalog", inventory)
                        self.store.put("project", nativeid, {"project": projectid, "name": initial["name"], "revision": revisionid})
                        if not self.store.get(revisionid, required=False):
                            self.store.put("project_revision", revisionid, dict(initial, bindings=bindings, created=time.time()), nativeid)
                            for position, binding in enumerate(bindings):
                                self.store.put("project_root", revisionid + "_" + str(position), dict(binding, position=position), revisionid)
                        record = self.store.get(preparationid)
                        record.update(state="active", revision=revisionid)
                        self.store.save(record)
                        self.store.event("project/registered", revisionid, {"project": projectid, "checkouts": record["checkouts"]})
                    from .registry import publish
                    publish(self.store)
                    return self.store.get(revisionid)
                except BaseException as exc:
                    record = self.store.get(preparationid)
                    record.update(state="incomplete", error=getattr(exc, "code", type(exc).__name__))
                    self.store.save(record)
                    raise

    def status(self, params):
        projectid = params["project"]
        record = self.store.get("project_" + digest(projectid), required=False)
        return {"project": {"id": projectid, "name": record["name"]} if record else None,
                "revision": self.store.get(record["revision"]) if record else None,
                "revisions": [r for r in self.store.all("project_revision") if r["project"] == projectid],
                "preparations": [p for p in self.store.all("project_preparation") if p["project"] == projectid]}


def verify_thread(response, unit, cwd, roots):
    """Success from resume is insufficient: loaded threads can ignore overrides."""
    if not unit.get("project_revision"):
        return
    thread = response.get("thread", {})
    if response.get("runtimeWorkspaceRoots") != roots or thread.get("projectId") != unit["project"] or thread.get("cwd") != cwd:
        raise CoordinatorError("PROJECT_THREAD_MISMATCH", "Native thread differs from the unit's pinned project, cwd or runtime roots",
                               {"project": unit["project"], "cwd": cwd, "roots": roots,
                                "effective_project": thread.get("projectId"), "effective_cwd": thread.get("cwd"),
                                "effective_roots": response.get("runtimeWorkspaceRoots")})
