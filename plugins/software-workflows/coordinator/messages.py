"""Durable delivery attempts, native context echoes and actor-bound acknowledgements."""
import asyncio
import re
import time

from .client import CoordinatorError
from .native import page
from .store import WRITER_MAIL, uid
from .locks import KeyedLocks


STATES = ("persisted", "submitted", "entered_context", "acknowledged")
INBOX_SCOPE = ("A lead's inbox holds only its own project's mail: supply its session_id or its folder (cwd). A Claude "
               "session started before software-workflows 2.4.0 runs an MCP server that sends neither; restart it.")
RESTART = ("Restart this Claude session to load software-workflows 2.4.4: its MCP server sent neither a session_id nor a "
           "folder (cwd), and a lead's inbox holds only its own project's mail.")
# Recovery's notices to a writer before 2.4.4 were no-wake mail from `lead` (recovery.py: "died" since 2.3.0,
# "readmitted" since 2.4.2), told by their openings: each is a coordinator notice (notice), delivered as notify's are.
LEGACY_NOTICES = ("Re-admitted with this release's hooks: a replacement app-server resumed this thread without continuing it.",
                  "The listener died. A replacement cold-resumed this thread without continuing it.")
# A notice's deliveries (deliver_notice): owed, an inject in flight or its result lost, held by a running turn, refused
# by an app-server. A legacy notice that 2.4.3 steered is settled first as any steer (settle_delivery).
NOTICE_DELIVERIES = ("pending", "turn_ended", "injecting", "injected", "refused")


class Messages:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.locks = KeyedLocks()

    def recipient(self, recipient):
        if recipient == "lead":
            return "lead", None
        record = self.store.get(recipient)
        if record["_kind"] == "lead_mailbox":
            return record["id"], None
        if record["_kind"] == "unit":
            record = self.store.get(record["run"])
        if record["_kind"] == "run":
            record = self.store.get(record["root_actor"])
        if record["_kind"] != "actor":
            raise CoordinatorError("MESSAGE_RECIPIENT", "Recipient must be a run, unit, actor or lead")
        return record["id"], record

    def transition(self, message, state, evidence):
        # Each transition is append-only. Echoes can beat the submit RPC result.
        with self.store.transaction():
            if not self.store.db.execute("SELECT 1 FROM events WHERE subject=? AND kind=? LIMIT 1",
                                         (message["id"], "message/" + state)).fetchone():
                self.store.event("message/" + state, message["id"], evidence)
            if STATES.index(state) > STATES.index(message["state"]):
                message["state"] = state
            self.store.save(message)

    async def notify(self, actorid, text):
        """A coordinator notice to a writer's root thread: what it should know and need not answer. It starts no turn
        and never interrupts one (deliver_notice)."""
        actor = self.store.get(actorid)
        if actor["_kind"] != "actor" or actor.get("kind") != "root":
            raise CoordinatorError("MESSAGE_RECIPIENT", "A coordinator notice goes to a writer's root thread", {"recipient": actorid})
        return await self.send("coordinator", actorid, text, "none", notice=True)

    @staticmethod
    def notice(message):
        """A coordinator notice (notify), or one recovery sent before 2.4.4 (LEGACY_NOTICES), not woken since."""
        return message["wake"] == "none" and (message.get("notice") is True or message.get("inject_before_turn") is True or
                                              message["sender"] == "lead" and message["text"].startswith(LEGACY_NOTICES))

    async def send(self, sender, recipient, text, wake, cwd="", notice=False, ident=None):
        if wake not in ("none", "queue", "start") or not isinstance(text, str) or not text:
            raise CoordinatorError("MESSAGE_ARGUMENTS", "Message needs text and explicit wake policy: none, queue or start")
        if not isinstance(sender, str) or not sender:
            # A lead's message/send names any sender; a writer's inbox shows it (review turn 105: a list was stored).
            raise CoordinatorError("MESSAGE_ARGUMENTS", "A message's sender is a nonempty string")
        origin = None
        if recipient == "lead" and sender != "lead":
            origin = self.store.get(sender, required=False)
            if origin and origin.get("unit"):
                unit = self.store.get(origin["unit"])
                recipient = unit.get("lead_mailbox", recipient)
        actorid, actor = self.recipient(recipient)
        if actor and actor["kind"] == "lead":
            # A lead reads its mail through its inbox, never a native turn: mail to its actor goes to the mailbox
            # its session reads and acknowledges (2.4.3 left it on the actor, where no inbox showed it and no
            # acknowledgement was accepted).
            box = self.lead_box(actor)
            if box:
                actorid, actor = box, None
        ident = ident or uid("msg")
        existing = self.store.get(ident, required=False)
        if existing:
            self.cancel_pending(existing)
            return self.store.get(ident)
        if actor and actor.get("kind") != "lead":
            run = self.store.get(actor["run"])
            if run.get("cancel_requested") or run["state"] == "cancelled":
                raise CoordinatorError("RUN_CANCELLED", "Cancelled writers cannot receive new mail", {"run": run["id"]})
        fields = {"sender": sender, "recipient": actorid, "text": text, "wake": wake, "state": "persisted", "created": time.time(),
                  "attempts": [], "native_id": ident, "delivery": "pending"}
        if notice:
            fields["notice"] = True
        if actor:
            fields["unit"] = actor["unit"]
        elif actorid.startswith("lead_mailbox_"):
            fields["unit"] = self.store.get(actorid)["unit"]
        else:
            # Mail to `lead` from a writer whose unit has no lead mailbox (a plain run started without a lead
            # session) is scoped by that writer's unit: a lead reads it when the unit shares a checkout with what
            # the lead's session or folder reaches (lead.scope). A lead's own goes by its folder.
            fields["unit"] = (origin or {}).get("unit")
            if not fields["unit"] and cwd:
                from .observations import checkout_at
                checkout, _ = checkout_at(self.store, cwd)
                if checkout:
                    fields["checkout"] = checkout["id"]
        self.store.put("message", ident, fields)
        self.store.event("message/persisted", ident, {"sender": sender, "recipient": actorid})
        if actor and actor["kind"] != "lead":
            self.daemon.background(self.deliver(ident))
        return self.store.get(ident)

    def lead_box(self, actor):
        """Where mail to a lead actor goes: the mailbox of the session it is the lead actor of (lead/session), else its
        unit's lead mailbox. None for an actor lead/attach made for a unit that has none: its mail stays on it and is
        read by scope (readable)."""
        from .lead import mailbox, owners
        for session in owners(self.store, actor["id"]):
            # A 2.3 mapping holds no mailbox: its session's mailbox for the same unit is the one its inbox reads.
            return session.get("mailbox") or mailbox(self.store, session["session_id"], session["unit"])
        return self.store.get(actor["unit"]).get("lead_mailbox")

    def cancel_pending(self, message):
        """Settle unsent legacy mail and cancel races without fabricating a receipt.

        Submitted/uncertain attempts still go through native evidence recovery.
        A pending delivery has no current native effect to wait for.
        """
        if message["state"] in ("entered_context", "acknowledged") or message.get("delivery") != "pending":
            return False
        actor = self.store.get(message["recipient"], required=False)
        if not actor or actor.get("_kind") != "actor" or actor.get("kind") == "lead":
            return False
        run = self.store.get(actor["run"])
        if not (run.get("cancel_requested") or run["state"] == "cancelled"):
            return False
        with self.store.transaction():
            message.update(delivery="cancelled", cancelled=time.time())
            self.store.save(message)
            self.store.event("message/cancelled", message["id"], {"reason": "recipient run cancelled", "run": run["id"]})
        return True

    async def deliver(self, ident):
        if self.daemon.recovery_barrier:
            return
        async with self.locks.hold(ident):
            message = self.store.get(ident)
            if self.cancel_pending(message):
                return
            if message["state"] in ("entered_context", "acknowledged") or (message["recipient"] == "lead" or message["recipient"].startswith("lead_mailbox_")):
                return
            if self.notice(message) and message["delivery"] in NOTICE_DELIVERIES:
                await self.deliver_notice(message)
                return
            if message["delivery"] in ("cancelled", "turn_ended", "unproven"):
                return  # unproven: its turn may have run, so only the lead's message/retry sends it (runs.apply_message)
            actor = self.store.get(message["recipient"])
            if not actor.get("listener"):
                return
            if message["delivery"] in ("submitting", "uncertain", "submitted"):
                # Absence after an uncertain RPC is not proof it was never submitted: settled by reads in the thread's
                # order, or by what its app-server saved once it has exited, which needs no connection (runs.py).
                await self.daemon.runs.settle_delivery(message, actor)
                return
            listener = self.store.get(actor["listener"])
            if (listener["state"] in ("dormant", "parking") or listener.get("park_resume_required")) and self.daemon.delivery_owed(message):
                if not self.daemon.dormancy.start_due(actor["run"]):
                    return  # its writer's last start failed: this pass starts none (dormancy.start_due)
                await self.daemon.dormancy.start(actor["run"], "waking message")
                message = self.store.get(ident)
                if self.cancel_pending(message):
                    return
                actor = self.store.get(actor["id"])
                listener = self.store.get(actor["listener"])
            if not listener.get("admission") or listener["state"] != "connected" or listener.get("recovery_state") in ("reconciling", "failed"):
                return
            connection = self.daemon.connections.get(actor["listener"])
            if not connection or connection.closed:
                return
            native_id = message["native_id"]
            target = actor
            text = "C1_MESSAGE " + ident + " from " + message["sender"] + "\n" + message["text"] + "\nAcknowledge with ack(message=" + ident + ")."
            if actor["kind"] == "child":
                target = self.store.get(self.store.get(actor["run"])["root_actor"])
                native_id += ":relay"
                text = ("Relay C1_MESSAGE " + ident + " to child thread " + actor["thread"] + " using native collaboration.send_message "
                        "(or followup_task if wake is requested). Child must call ack with this ID, or emit C1_ACK " + ident +
                        " if it lacks inherited dynamic tools. Your own acknowledgement does not acknowledge the child.\n" + message["text"])
            active = target.get("active_turn")
            run = self.store.get(target["run"])
            if run.get("cancel_requested") or self.store.get(target["unit"])["state"] != "open":
                return
            if not active and message["wake"] == "none":
                return
            from .participants import checkout_ids
            for key in checkout_ids(self.store, target["unit"]):
                await self.daemon.ensure_lease(key)
            current = self.store.get(target["id"])
            listener = self.store.get(current["listener"])
            if (self.daemon.recovery_barrier or current["listener"] != target["listener"] or
                    listener["state"] != "connected" or listener.get("recovery_state") in ("reconciling", "failed") or
                    self.daemon.connections.get(listener["id"]) is not connection or connection.closed):
                return  # A reconnect during lease restoration must verify its pinned bindings first.
            if self.store.get(target["run"]).get("cancel_requested"):
                self.cancel_pending(self.store.get(ident))
                return
            if self.daemon.hook_stale(listener) and (not active or self.hooks_fail(listener)):
                # Admitted by another release: it is replaced first, once its unit is quiet, and this message stays
                # pending until then. A turn it is running still takes it (turn/steer) while that turn's calls pass;
                # once they fail (hooks_fail) the turn could read it but not act on it, so it waits for the replacement.
                self.daemon.readmit(target["run"])
                return
            method = "turn/steer" if active else ("thread/queue/add" if message["wake"] == "queue" else "turn/start")
            from .writer_profile import deliver_context
            await deliver_context(self.daemon, target, preparing_input=True)
            self.daemon.check_native_stop()
            message = self.store.get(ident)
            if self.cancel_pending(message) or message["delivery"] == "cancelled":
                return
            if (self.store.get(target["run"]).get("cancel_requested") or
                    self.store.get(target["id"])["listener"] != listener["id"] or connection.closed or
                    self.store.get(listener["id"]).get("recovery_state") in ("reconciling", "failed")):
                return
            params = {"threadId": target["thread"], "input": [{"type": "text", "text": text}], "clientUserMessageId": native_id}
            if active:
                params["expectedTurnId"] = active
            attempt = uid("delivery")
            message["attempts"].append(attempt)
            message["delivery"] = "submitting"
            with self.store.transaction():
                params["input"] = self.prefix_held(target, native_id, params["input"])
                self.store.save(message)
                self.store.put("delivery", attempt, {"message": ident, "method": method, "actor": target["id"], "listener": listener["id"],
                               "client_id": native_id, "state": "submitting", "created": time.time()}, ident)
            try:
                response = await connection.call(method, params)
            except CoordinatorError as exc:
                message = self.store.get(ident)
                # A definitive stale-turn rejection permits a refresh, but only after
                # history and native queue reconciliation; a lost result never does.
                message["delivery"] = "uncertain"
                message["last_error"] = exc.code
                self.store.save(message)
                self.store.event("message/delivery_uncertain", ident, {"attempt": attempt, "code": exc.code})
                await self.reconcile(message, actor)
                if exc.code == "NATIVE_RPC" and (str(exc).startswith("expected active turn id") or str(exc) == "no active turn to steer"):
                    message = self.store.get(ident)
                    if message["state"] == "persisted" and message["delivery"] == "uncertain":
                        await self.daemon.history.observe(listener, target, connection)
                        target = self.store.get(target["id"])
                        message = self.store.get(ident)
                        message["delivery"] = "pending"
                        self.store.save(message)
                return
            record = self.store.get(attempt)
            record.update(state="submitted", result=response)
            self.store.save(record)
            message = self.store.get(ident)
            message["delivery"] = "submitted"
            if actor["kind"] == "child":
                message["relay"] = {"state": "submitted", "actor": target["id"], "native_id": native_id}
            self.transition(message, "submitted", {"attempt": attempt, "native_id": native_id})

    async def deliver_held(self, runid):
        """Held mail enters before a prepared input. Refused mail stays durable."""
        run = self.store.get(runid)
        root = self.store.get(run["root_actor"])
        for message in list(self.store.selected("SELECT * FROM records WHERE kind='message' AND json_extract(data,'$.recipient')=? ORDER BY id", (root["id"],))):
            if message.get("wake") != "none" or message["state"] != "persisted" or message["delivery"] not in NOTICE_DELIVERIES:
                continue
            async with self.locks.hold(message["id"]):
                message = self.store.get(message["id"])
                if not self.notice(message):
                    message["inject_before_turn"] = True
                self.store.save(message)
                try:
                    await self.deliver_notice(message)
                except (CoordinatorError, OSError) as exc:
                    self.store.event("message/held_delivery_failed", message["id"],
                                     {"code": getattr(exc, "code", type(exc).__name__), "run": runid})

    def prefix_held(self, actor, client_id, inputs):
        """An unknown inject capability cannot authorize an injection.

        Instead carry held mail ahead of an already authorized input. Its
        durable client identity settles the mail on the same native echo,
        including after a lost reply. This creates no turn by itself.
        """
        prefix = []
        for message in self.store.selected("SELECT * FROM records WHERE kind='message' AND json_extract(data,'$.recipient')=? ORDER BY id", (actor["id"],)):
            if message["wake"] != "none" or message["state"] != "persisted" or message["delivery"] not in NOTICE_DELIVERIES:
                continue
            if message["delivery"] in ("injecting", "injected"):
                continue  # reconcile an existing injection before another delivery
            text = "C1_MESSAGE " + message["id"] + " from " + message["sender"] + "\n" + message["text"]
            if self.notice(message) and not message.get("inject_before_turn"):
                text += "\n(No reply or acknowledgement needed.)"
            else:
                text += "\nAcknowledge with ack(message=" + message["id"] + ")."
            attempt = uid("delivery")
            self.store.put("delivery", attempt, {"message": message["id"], "method": "input/prefix", "actor": actor["id"],
                           "listener": actor["listener"], "client_id": client_id, "state": "submitting", "created": time.time()}, message["id"])
            message["attempts"].append(attempt)
            message["delivery"] = "submitted"
            self.store.save(message)
            prefix.append({"type": "text", "text": text})
        return prefix + inputs

    async def deliver_notice(self, message):
        """Deliver a coordinator notice (notice), under its lock, with thread/inject_items: native records it in the
        thread's history without a turn, at once on an idle thread and in a running turn at its next sampling request,
        without preempting a running code-mode cell as a steer's user input does (native.FIELDS). Only as its
        app-server's admission allows (its inject capability, admission.CAPABILITIES): "active" proved both, "idle" the
        idle thread only, where the notice waits for the turn to end; an unknown or failed probe proves
        neither, so the notice stays held until an explicitly authorized input can carry it. Never steered, queued
        or started as an independent turn, and no reply
        is asked.
        One a running turn holds is in context once that turn completes: native records at its finish what it had not
        taken (tasks/mod.rs on_task_finished). An interrupt drops what it had not taken (abort_all_tasks), as does the
        end of its app-server, and a failed turn may have been aborted: each is injected again, at least once, its id
        in its text telling a second copy apart."""
        ident = message["id"]
        actor = self.store.get(message["recipient"])
        run, unit = self.store.get(actor["run"]), self.store.get(actor["unit"])
        if message["delivery"] != "injected" and (run.get("cancel_requested") or unit["state"] != "open"):
            message["delivery"] = "cancelled"  # as a cancel does a pending message's
            self.store.save(message)
            self.store.event("message/cancelled", ident, {"reason": "its run was cancelled or its unit closed"})
            return
        listener = self.store.get(actor["listener"]) if actor.get("listener") else None
        connection = self.daemon.connections.get(actor.get("listener"))
        if (not listener or not listener.get("admission") or listener["state"] != "connected" or
                listener.get("recovery_state") in ("reconciling", "failed") or not connection or connection.closed):
            return
        if message["delivery"] == "injected":
            taken, evidence = await self.injected(message, actor, connection)
            if taken is None:
                return
            message = self.store.get(ident)
            if taken:
                self.transition(message, "entered_context", dict(evidence, inject=True))
                return
            message["delivery"] = "pending"
            self.store.save(message)
            self.store.event("message/inject_dropped", ident, dict(evidence, possible_duplicate_native_delivery=True))
            return  # injected again by its next delivery, which reads everything again
        if message["delivery"] == "injecting":
            # Its inject's result was lost with the daemon that sent it: it may be in the thread's history.
            message["delivery"] = "pending"
            self.store.save(message)
            self.store.event("message/inject_uncertain", ident, {"attempt": message["attempts"][-1], "possible_duplicate_native_delivery": True})
        if message["delivery"] == "refused" and message.get("refused_listener") == listener["id"]:
            return  # this app-server refused it: its replacement is asked
        capability = (listener.get("capabilities") or {}).get("inject")
        active = actor.get("active_turn")
        if capability not in ("active", "idle"):
            if message.get("held") != listener["id"]:
                message["held"] = listener["id"]
                self.store.save(message)
                self.store.event("message/notice_held", ident, {"listener": listener["id"], "capability": capability,
                                                                "reason": "admission did not prove thread/inject_items on this app-server"})
            return
        if active and capability != "active":
            return  # proven on an idle thread only: it waits for this turn to end
        text = ("C1_MESSAGE " + ident + " from " + message["sender"] + "\n" + message["text"] +
                "\nAcknowledge with ack(message=" + ident + ")." if message.get("inject_before_turn") else
                "C1_NOTICE " + ident + " from the coordinator\n" + message["text"] + "\n(No reply or acknowledgement needed.)")
        attempt = uid("delivery")
        message["attempts"].append(attempt)
        message["delivery"] = "injecting"
        with self.store.transaction():
            self.store.save(message)
            self.store.put("delivery", attempt, {"message": ident, "method": "thread/inject_items", "actor": actor["id"], "listener": listener["id"],
                           "client_id": message["native_id"], "state": "submitting", "turn": active, "created": time.time()}, ident)
        try:
            await connection.call("thread/inject_items", {"threadId": actor["thread"], "items": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}]})
        except (CoordinatorError, OSError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            refused = code in ("NATIVE_RPC", "OUTBOUND_INPUT")  # answered: native holds nothing of it
            record, message = self.store.get(attempt), self.store.get(ident)
            record.update(state="refused" if refused else "uncertain", error=code, native_code=(getattr(exc, "details", None) or {}).get("native_code"))
            message["delivery"] = "refused" if refused else "pending"
            if refused:
                message["refused_listener"] = listener["id"]
            with self.store.transaction():
                self.store.save(record)
                self.store.save(message)
                self.store.event("message/inject_refused" if refused else "message/inject_uncertain", ident,
                                 {"attempt": attempt, "listener": listener["id"], "code": code, "detail": str(exc)[:300],
                                  **({} if refused else {"possible_duplicate_native_delivery": True})})
            return
        # A turn that started while the inject was in flight may hold it: it is settled as one the turn held.
        current = self.store.get(actor["id"])
        turn = active or (current.get("active_turn") if current.get("listener") == listener["id"] else None)
        message = self.store.get(ident)
        message["delivery"] = "injected"
        message.pop("held", None)
        message.pop("refused_listener", None)
        self.transition(message, "submitted" if turn else "entered_context",
                        {"inject": True, "attempt": attempt, "listener": listener["id"], "turn": turn})
        record = self.store.get(attempt)
        record.update(state="submitted", turn=turn)
        self.store.save(record)

    async def injected(self, message, actor, connection):
        """Where a notice injected while a turn ran stands, as (taken, evidence): None while that turn runs or its
        thread cannot be read; True once it completed; False once it ended otherwise or with its app-server (a
        replacement reads the thread it resumed)."""
        attempt = self.store.get(message["attempts"][-1])
        turn, same = attempt.get("turn"), actor["listener"] == attempt["listener"]
        if same and actor.get("active_turn") == turn:
            return None, {}
        status = None
        try:
            await self.daemon.history.observe(self.store.get(actor["listener"]), actor, connection)
            status = (self.daemon.history.turn(actor, turn) or {}).get("status")
        except (CoordinatorError, OSError):
            return None, {}
        if status == "inProgress" and same:
            return None, {}
        return status == "completed", {"attempt": attempt["id"], "turn": turn, "status": status, "listener": attempt["listener"]}

    def hooks_fail(self, listener):
        """Whether the calls of an app-server another release admitted (daemon.hook_stale) fail: hook() refuses a hook
        program other than this daemon's (HOOK_PROGRAM_CHANGED), and a hook whose interpreter or program file is gone
        cannot run. Otherwise they pass as a current app-server's do."""
        import os
        import shlex
        from pathlib import Path
        from .native import file_digest
        if listener.get("hook_digest") != file_digest(Path(__file__).with_name("hooks.py")):
            return True
        table = (listener.get("config") or {}).get("hooks") or {}
        commands = [h.get("command") or "" for event in ("PreToolUse", "PostToolUse") for e in table.get(event) or []
                    for h in e.get("hooks", [])]
        for command in commands:
            try:
                words = shlex.split(command)
            except ValueError:
                return True
            if not words or any(os.path.isabs(word) and not os.path.exists(word) for word in words):
                return True
        return not commands

    async def withdraw_stale(self, listener):
        """Codex 0.163 starts a thread's next queued item as soon as the thread is idle, with no request of ours
        (ext/queue/src/service.rs: on_thread_idle, then dispatch_if_idle), so on an app-server admitted by another
        release (daemon.hook_stale) a message in its native queue would start a turn on that release's hooks: one
        whose every call is refused when they fail (hooks_fail), and one that keeps it from being replaced otherwise.
        Each of ours in its threads' queues is taken back (withdraw), and deliver treats it as any message for that
        app-server. Another input there (one a user queued in Codex) is left as it is. Returns how many were taken
        back."""
        listener = self.store.get(listener["id"])
        connection = self.daemon.connections.get(listener["id"])
        if not connection or connection.closed or not listener.get("run") or not self.daemon.hook_stale(listener):
            return 0
        taken = 0
        unit = self.store.get(listener["run"])["unit"]
        for actor in [a for a in self.store.all("actor", unit) if a.get("listener") == listener["id"] and a.get("thread")]:
            entries = []
            async for queued in page(connection, "thread/queue/list", {"threadId": actor["thread"]}):
                entries.extend(queued["data"])
            for entry in entries:
                client = entry.get("clientUserMessageId") or ""
                ident = client[:-len(":relay")] if client.endswith(":relay") else client
                message = self.store.get(ident, required=False) if ident.startswith("msg_") else None
                if not message or message["_kind"] != "message":
                    continue
                if ident in self.locks.entries:
                    # A delivery under way (its lock held) is left to it, and a later pass takes it back: one may be
                    # waiting for the run's reconcile lock (runs.end_writer), which a reconcile calling this holds.
                    continue
                async with self.locks.hold(ident):
                    taken += await self.withdraw(self.store.get(ident), actor, connection, entry)
        return taken

    async def withdraw(self, message, actor, connection, entry):
        """Take one message out of a stale app-server's native queue, holding its lock. Native serializes a deletion
        with its own dispatch (dispatch_guard), so `deleted` means no turn started for it: it is pending again, and
        deliver sends it on. Otherwise native started it first, and its history settles it."""
        if message["state"] in ("entered_context", "acknowledged"):
            return 0
        result = await connection.call("thread/queue/delete", {"threadId": actor["thread"], "queuedSubmissionId": entry["id"]})
        if not result.get("deleted"):
            return 0
        message = self.store.get(message["id"])
        with self.store.transaction():
            for attempt in message.get("attempts", []):
                record = self.store.get(attempt, required=False)
                if record and record.get("method") == "thread/queue/add" and record["state"] in ("submitting", "submitted"):
                    record["state"] = "withdrawn"
                    self.store.save(record)
            message["delivery"] = "pending"
            message.pop("relay", None)
            self.store.save(message)
            self.store.event("message/withdrawn", message["id"], {"queue_id": entry["id"], "listener": actor["listener"],
                                                                  "reason": "app-server runs another release's hooks"})
        return 1

    def entered(self, actorid, native_id, evidence):
        for attempt in self.store.selected("SELECT * FROM records WHERE kind='delivery' AND json_extract(data,'$.method')='input/prefix' AND json_extract(data,'$.client_id')=? AND json_extract(data,'$.actor')=?", (native_id, actorid)):
            message = self.store.get(attempt["message"])
            if message["state"] not in ("entered_context", "acknowledged"):
                self.transition(message, "entered_context", {**evidence, "input_prefix": native_id})
            if attempt["state"] != "entered_context":
                attempt["state"] = "entered_context"
                self.store.save(attempt)
        relay = native_id.endswith(":relay")
        ident = native_id[:-6] if relay else native_id
        message = self.store.get(ident, required=False)
        if not message or message["_kind"] != "message":
            return
        if relay:
            recipient = self.store.get(message["recipient"])
            root = self.store.get(self.store.get(recipient["run"])["root_actor"])
            if actorid != root["id"]:
                raise CoordinatorError("MESSAGE_HOP", "Relay context echo came from the wrong root")
            message["relay"] = {"state": "entered_context", "actor": actorid, "evidence": evidence}
            self.store.save(message)
            self.store.event("message/relay_entered", ident, evidence)
            return
        if message["recipient"] != actorid:
            raise CoordinatorError("MESSAGE_ORIGIN", "Context echo came from another actor")
        self.transition(message, "entered_context", evidence)

    def ack(self, actorid, ident, session_id="", cwd=""):
        """A writer acknowledges mail to itself, by its native identity. A lead acknowledges its own mail (readable):
        as its session (`session_id`), its mailbox of an explicit unit, a lead actor, or `lead` by its folder."""
        message = self.store.get(ident)
        if message["_kind"] != "message":
            raise CoordinatorError("MESSAGE_ID", "No such message", {"message": ident})
        actor = None if actorid == "lead" or actorid.startswith("lead_mailbox_") else self.store.get(actorid, required=False)
        lead = bool(session_id) or actor is None or actor.get("kind") == "lead"
        if lead:
            if actorid == "lead" and not session_id and not cwd:
                raise CoordinatorError("INBOX_SCOPE", INBOX_SCOPE)
            if actor and actor.get("kind") == "lead":
                # A lead actor speaks for its own mail, wherever it went (lead_box).
                allowed = message["recipient"] in (actor["id"], self.lead_box(actor))
            else:
                allowed = self.readable(message, session_id, cwd, None if actorid == "lead" or session_id else actorid)
            if not allowed:
                raise CoordinatorError("ACK_ACTOR", "Only the lead this message is addressed to can acknowledge it: the "
                                       "session whose mailbox or lead actor received it, or for mail to `lead`, a lead "
                                       "whose project holds the sending writer's checkout", {"message": ident})
        elif message["recipient"] != actorid:
            raise CoordinatorError("ACK_ACTOR", "Only the actual recipient can acknowledge this message")
        if message["state"] == "acknowledged":
            return {"message": ident, "state": "acknowledged", "duplicate": True}
        if not lead and message["state"] != "entered_context":
            if actor["kind"] != "child":
                raise CoordinatorError("ACK_CONTEXT", "Native context echo has not yet been reconciled")
            # A child's own dynamic call proves receipt independently of the root.
            self.transition(message, "entered_context", {"child_actor": actorid, "evidence": "native child acknowledgement call"})
        evidence = {"actor": message["recipient"] if lead else actorid}
        if lead:
            evidence["by"] = {"session_id": session_id} if session_id else {"actor": actorid, **({"cwd": cwd} if cwd else {})}
        self.transition(message, "acknowledged", evidence)
        return {"message": ident, "state": "acknowledged", "duplicate": False}

    def mail_unit(self, message, record=None):
        """The unit lead mail belongs to: recorded since 2.4.4 (and for mail to a mailbox or an actor), else its
        sending writer's."""
        if message.get("unit"):
            return message["unit"]
        sender = message.get("sender")
        if not sender or sender == "lead":
            return None
        return ((record or (lambda ident: self.store.get(ident, required=False)))(sender) or {}).get("unit")

    def readable(self, message, session_id="", cwd="", box=None, reach=None, cache=None):
        """Whether a lead may read and acknowledge this message. Mail to a mailbox is its session's; with an explicit
        unit (`box`, the session's mailbox of that unit), that box's alone. Mail to a lead actor is the session's it is
        the lead actor of. Mail to `lead`, or to a lead actor no session owns, is any lead's whose scope (lead.scope;
        `reach`, computed once per read) shares a checkout with its unit's participants; with an explicit unit, that
        unit's. `cache` keeps records, owners and participants across one read."""
        from .lead import owners, scope
        from .participants import checkout_ids
        cache = {} if cache is None else cache
        def cached(key, load):
            if key not in cache:
                cache[key] = load()
            return cache[key]
        record = lambda ident: cached(("record", ident), lambda: self.store.get(ident, required=False))
        recipient = message["recipient"]
        mailbox = record(box) if box else None
        if box and (not mailbox or mailbox["_kind"] != "lead_mailbox"):
            return False
        if recipient.startswith("lead_mailbox_"):
            if box:
                return recipient == box
            target = record(recipient)
            return bool(session_id and target and target["session_id"] == session_id)
        actor = None if recipient == "lead" else record(recipient)
        if recipient != "lead" and (not actor or actor["_kind"] != "actor" or actor.get("kind") != "lead"):
            return False  # a writer's mail is its own
        if actor:
            sessions = cached(("owners", actor["id"]), lambda: owners(self.store, actor["id"]))
            if sessions:
                if box:
                    return actor["unit"] == mailbox["unit"] and any(s["session_id"] == mailbox["session_id"] for s in sessions)
                return any(s["session_id"] == session_id for s in sessions)
        unit = self.mail_unit(message, record) or (actor or {}).get("unit")
        if box:
            return unit == mailbox["unit"]
        reach = cached("reach", lambda: scope(self.store, session_id, cwd)) if reach is None else reach
        if not unit:
            # No unit: the folder a lead sent it from, if any; otherwise no project holds it, and any lead reads it.
            return message.get("checkout") is None or message["checkout"] in reach
        checkouts = cached(("unit", unit), lambda: set(checkout_ids(self.store, unit)) if record(unit) else set())
        return bool(checkouts & reach)

    def child_ack(self, actor, item):
        for ident in re.findall(r"(?m)^C1_ACK (msg_[0-9a-f]{32})\s*$", item.get("text", "")):
            self.ack(actor["id"], ident)

    def inbox(self, recipient="lead", cursor="", limit=100, session_id="", cwd=""):
        """A lead's inbox (`lead` or its mailbox of an explicit unit) is the mail it may read (readable), one page in id
        order, with its unread count. Any other recipient's (a writer's own `inbox` tool) is the mail to it."""
        if type(limit) is not int or limit < 1:
            raise CoordinatorError("PAGE_SIZE", "Page size must be positive")
        if recipient == "lead" or recipient.startswith("lead_mailbox_"):
            box = recipient if recipient.startswith("lead_mailbox_") else None
            if box and self.store.get(box)["_kind"] != "lead_mailbox":
                raise CoordinatorError("MESSAGE_RECIPIENT", "No such lead mailbox", {"mailbox": box})
            if not box and not session_id and not cwd:
                # No project to read: none of its mail, and why. A refusal here never reached the lead: the 2.4.3
                # server's unread notice swallows it and returns its tool result unchanged (r244/w-lead/logs/r2.log).
                return {"data": [], "next_cursor": None, "unread": 0, "notice": RESTART}
            return self.lead_inbox(cursor, limit, session_id, cwd, box)
        recipient, _ = self.recipient(recipient)
        query = "FROM records WHERE kind='message' AND json_extract(data,'$.recipient')=?"
        records = list(self.store.selected("SELECT * " + query + " AND id>? ORDER BY id LIMIT ?", (recipient, cursor, limit + 1)))
        unread = self.store.db.execute("SELECT count(*) " + query + " AND json_extract(data,'$.state')!='acknowledged'",
                                       (recipient,)).fetchone()[0]
        return {"data": records[:limit], "next_cursor": records[limit - 1]["id"] if len(records) > limit else None,
                "unread": unread}

    def lead_inbox(self, cursor, limit, session_id="", cwd="", box=None):
        """Read through the recipient index only: the session's mailboxes (or `box`), `lead`, and the lead actors.
        Each candidate's fields are read in SQL; only the page's records are decoded."""
        from .lead import scope
        if box:
            boxes = [box]
        elif session_id:
            boxes = [row[0] for row in self.store.db.execute("SELECT id FROM records WHERE kind='lead_mailbox' AND "
                                                             "json_extract(data,'$.session_id')=?", (session_id,))]
        else:
            boxes = []
        actors = [row[0] for row in self.store.db.execute("SELECT id FROM records WHERE kind='actor' AND json_extract(data,'$.kind')='lead'")]
        recipients = boxes + ["lead"] + actors
        reach = None if box else scope(self.store, session_id, cwd)
        cache, selected = {}, []
        fields = ("recipient", "unit", "sender", "checkout", "state")
        for start in range(0, len(recipients), 500):
            chunk = recipients[start:start + 500]
            rows = self.store.db.execute(
                "SELECT id, " + ", ".join("json_extract(data,'$." + f + "')" for f in fields) + " FROM records WHERE "
                "kind='message' AND json_extract(data,'$.recipient') IN (" + ",".join("?" for _ in chunk) + ")", chunk)
            for row in rows:
                message = dict(zip(fields, row[1:]), id=row[0])
                if self.readable(message, session_id, cwd, box, reach, cache):
                    selected.append(message)
        selected.sort(key=lambda m: m["id"])
        page = [m["id"] for m in selected if m["id"] > cursor][:limit + 1]
        records = [self.store.get(ident) for ident in page]
        return {"data": records[:limit], "next_cursor": records[limit - 1]["id"] if len(records) > limit else None,
                "unread": sum(m["state"] != "acknowledged" for m in selected)}

    async def reconcile(self, message, actor):
        target = actor if actor["kind"] != "child" else self.store.get(self.store.get(actor["run"])["root_actor"])
        connection = self.daemon.connections.get(target["listener"])
        if not connection or connection.closed:
            return
        await self.daemon.history.observe(self.store.get(target["listener"]), target, connection)
        async for queued in page(connection, "thread/queue/list", {"threadId": target["thread"]}):
            if any(entry.get("clientUserMessageId") in (message["native_id"], message["native_id"] + ":relay") for entry in queued["data"]):
                current = self.store.get(message["id"])
                current["delivery"] = "submitted"
                self.transition(current, "submitted", {"native_queue": True})

    async def retry_requested(self, ident):
        async with self.locks.hold(ident):
            message = self.store.get(ident)
            _, actor = self.recipient(message["recipient"])
            if actor:
                run = self.store.get(actor["run"])
                if run.get("cancel_requested") or run["state"] == "cancelled":
                    self.cancel_pending(message)
                    raise CoordinatorError("RUN_CANCELLED", "Cancelled writers cannot retry mail", {"run": run["id"]})
                await self.reconcile(message, actor)
            message = self.store.get(ident)
            if message["state"] in ("entered_context", "acknowledged") or message["delivery"] == "submitted":
                return message
            message["delivery"] = "pending"
            message.pop("unproven", None)  # the lead's decision now (runs.apply_message): sent again as its wake says
            self.store.save(message)
            self.store.event("message/retry_requested", ident, {"native_id": message["native_id"], "possible_duplicate_native_delivery": True})
        self.daemon.background(self.deliver(ident))
        return message

    async def retry(self):
        # Every second: decode only messages still to be delivered, not every message ever sent, and read only those
        # (their index: through 2.4.20 each pass tested every message, 19-21 ms for 5,000 on 10-29). A lead's mail (to
        # `lead`, a mailbox or a lead actor) is read through its inbox and never delivered.
        messages = list(self.store.selected("SELECT * FROM records INDEXED BY writer_mail WHERE " + WRITER_MAIL + " AND "
                "json_extract(data,'$.recipient') NOT IN (SELECT id FROM records WHERE kind='actor' AND "
                "json_extract(data,'$.kind')='lead') ORDER BY id"))
        # A message queued natively on an app-server that runs another release's hooks would start a turn there
        # (withdraw_stale): first take back what such app-servers hold, so it is delivered to their replacements.
        queued = set()
        for message in messages:
            attempt = self.store.get(message["attempts"][-1], required=False) if message.get("attempts") else None
            if message["delivery"] == "submitted" and (attempt or {}).get("method") == "thread/queue/add":
                queued.add(attempt["actor"])
        for listener in {(self.store.get(a, required=False) or {}).get("listener") for a in queued} - {None}:
            listener = self.store.get(listener, required=False)
            if listener and listener["state"] == "connected" and listener.get("recovery_state") not in ("reconciling", "failed"):
                try:
                    await self.withdraw_stale(listener)
                except (CoordinatorError, OSError):
                    pass  # read again next second
        for message in messages:
            if message["state"] not in ("entered_context", "acknowledged") and message["recipient"] != "lead" and not message["recipient"].startswith("lead_mailbox_"):
                try:
                    await self.deliver(message["id"])
                except (CoordinatorError, OSError):
                    pass  # durable pending/uncertain status remains visible
