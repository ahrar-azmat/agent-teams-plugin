"""Native project calibration in a disposable home, separate from singleton admission."""
import json
import sqlite3

from . import native
from .client import CoordinatorError
from .fixture import function, message
from .projects import verify_thread


async def probe(directory):
    from .admission import ProbeListener, serving
    observations = {}
    def router(body, index):
        items = [i for i in body.get("input", []) if i.get("type") in ("message", "agent_message", "function_call", "function_call_output")]
        calls = [i for i in items if i.get("name") == "spawn_agent"]
        if "P1_ROOT_CHILD_PROBE" in json.dumps(items) and not calls:
            return message("child inherited roots")
        if not calls:
            return function("spawn_agent", {"task_name": "roots", "message": "P1_ROOT_CHILD_PROBE", "fork_turns": "none"}, "collaboration")
        return message()
    async with serving(router) as provider:
        listener = ProbeListener(directory, provider, experimental=False, config={"features.unified_exec_tty": False})
        try:
            await listener.start()
            negative = []
            for method, args in (("project/list", {}), ("project/read", {"projectId": "missing"})):
                try:
                    await listener.connection.call(method, args, unchecked=True)
                    negative.append(False)
                except CoordinatorError as exc:
                    negative.append(exc.code == "NATIVE_RPC" and "experimentalApi" in str(exc))
                    observations[method + "_experimental_off"] = exc.envelope()
            await listener.connection.close()
            listener.connection = await native.Connection.connect(listener.socket, listener.events.append)
            await listener.connection.initialize(True)
            extra = directory / "sibling"
            extra.mkdir()
            roots = [str(listener.repo), str(extra)]
            # Fixture-only provisioning in ProbeListener's validated disposable
            # CODEX_HOME. Catalog exposes no project mutation capability.
            project = (await listener.connection.call("project/create", {"name": "P1 admission", "roots": [{"path": p} for p in roots],
                       "metadata": {}, "idempotencyKey": "p1-admission"}, unchecked=True))["project"]
            observations["project"] = project
            omitted = await listener.start_thread()
            observations["omitted_roots"] = omitted.get("runtimeWorkspaceRoots")
            negative.append(omitted.get("runtimeWorkspaceRoots") == [str(listener.repo)])
            result = await listener.start_thread(projectId=project["id"], runtimeWorkspaceRoots=roots)
            unit = {"project": project["id"], "project_revision": "disposable-calibration"}
            verify_thread(result, unit, str(listener.repo), roots)
            observations["start"] = {"thread": listener.thread, "roots": result["runtimeWorkspaceRoots"], "project": result["thread"].get("projectId")}
            await listener.turn()
            # A loaded thread ignores this override: the adapter must refuse the
            # mismatched effective response before any follow-up turn or delivery.
            mismatched = await listener.connection.call("thread/resume", {"threadId": listener.thread, "runtimeWorkspaceRoots": [str(extra)]})
            requests = len(provider.requests)
            try:
                verify_thread(mismatched, unit, str(listener.repo), [str(extra)])
                negative.append(False)
            except CoordinatorError as exc:
                negative.append(exc.code == "PROJECT_THREAD_MISMATCH")
                observations["mismatch"] = exc.envelope()
            observations["no_inference_after_mismatch"] = len(provider.requests) == requests
            read = (await listener.connection.call("thread/read", {"threadId": listener.thread, "includeTurns": False}))["thread"]
            with sqlite3.connect("file:" + str(listener.home / "state_5.sqlite") + "?mode=ro", uri=True) as db:
                persisted = db.execute("SELECT project_id FROM threads WHERE id=?", (listener.thread,)).fetchone()
            observations["thread_read_project"] = read.get("projectId")
            observations["thread_store_project"] = persisted[0] if persisted else None
            children = []
            async for page in native.page(listener.connection, "thread/list", {"ancestorThreadId": listener.thread}):
                children.extend(page["data"])
            observations["children"] = []
            for child in children:
                response = await listener.connection.call("thread/resume", {"threadId": child["id"]})
                observations["children"].append({"thread": child["id"], "roots": response.get("runtimeWorkspaceRoots"),
                                                 "project": response.get("thread", {}).get("projectId"),
                                                 "inherited": response.get("runtimeWorkspaceRoots") == roots})
            if not children:
                observations["child_inheritance"] = {"state": "UNVERIFIED", "reason": "Fixture spawn was attempted; native ancestor listing returned no child", "provider_requests": requests}
            await listener.connection.close()
            listener.connection = await native.Connection.connect(listener.socket, listener.events.append)
            await listener.connection.initialize(True)
            resume = {"threadId": listener.thread, "cwd": str(listener.repo), "runtimeWorkspaceRoots": roots,
                      "approvalPolicy": "never", "sandbox": "danger-full-access"}
            reconnected = await listener.connection.call("thread/resume", resume)
            verify_thread(reconnected, unit, str(listener.repo), roots)
            observations["reconnect"] = reconnected["runtimeWorkspaceRoots"]
            await listener.close()
            listener.experimental = True
            await listener.spawn()
            cold = await listener.connection.call("thread/resume", resume)
            verify_thread(cold, unit, str(listener.repo), roots)
            observations["cold_resume"] = cold["runtimeWorkspaceRoots"]
            for child in observations["children"]:
                response = await listener.connection.call("thread/resume", dict(resume, threadId=child["thread"]))
                verify_thread(response, dict(unit, project=child["project"]), str(listener.repo), roots)
                child["cold_roots"] = response["runtimeWorkspaceRoots"]
                child["cold_project"] = response["thread"].get("projectId")
            observations.update(negative_observed=all(negative), positive_observed=(read.get("projectId") == project["id"]
                and bool(persisted) and persisted[0] == project["id"] and all(c["inherited"] for c in observations["children"])),
                mode={"approvalPolicy": "never", "sandbox": "danger-full-access", "web_search": "live", "agents": True},
                backend="native app-server with loopback fixture provider", venue=str(directory))
            return observations
        finally:
            await listener.close()
