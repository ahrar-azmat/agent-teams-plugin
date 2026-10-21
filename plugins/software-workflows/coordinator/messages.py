"""Durable delivery attempts, native context echoes and actor-bound acknowledgements."""
import asyncio
import re
import time

from .client import CoordinatorError
from .native import page
from .store import uid
from .locks import KeyedLocks


STATES = ("persisted", "submitted", "entered_context", "acknowledged")


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

    async def send(self, sender, recipient, text, wake):
        if wake not in ("none", "queue", "start") or not isinstance(text, str) or not text:
            raise CoordinatorError("MESSAGE_ARGUMENTS", "Message needs text and explicit wake policy: none, queue or start")
        if recipient == "lead" and sender != "lead":
            origin = self.store.get(sender, required=False)
            if origin and origin.get("unit"):
                unit = self.store.get(origin["unit"])
                recipient = unit.get("lead_mailbox", recipient)
        actorid, actor = self.recipient(recipient)
        ident = uid("msg")
        self.store.put("message", ident, {"sender": sender, "recipient": actorid, "unit": actor["unit"] if actor else self.store.get(actorid)["unit"] if actorid.startswith("lead_mailbox_") else None,
                       "text": text, "wake": wake, "state": "persisted", "created": time.time(),
                       "attempts": [], "native_id": ident, "delivery": "pending"})
        self.store.event("message/persisted", ident, {"sender": sender, "recipient": actorid})
        if actor:
            self.daemon.background(self.deliver(ident))
        return self.store.get(ident)

    async def deliver(self, ident):
        if self.daemon.recovery_barrier:
            return
        async with self.locks.hold(ident):
            message = self.store.get(ident)
            if message["state"] in ("entered_context", "acknowledged") or (message["recipient"] == "lead" or message["recipient"].startswith("lead_mailbox_")) or message["delivery"] in ("cancelled", "turn_ended"):
                return
            actor = self.store.get(message["recipient"])
            if not actor.get("listener"):
                return
            listener = self.store.get(actor["listener"])
            if not listener.get("admission") or listener["state"] != "connected" or listener.get("recovery_state") in ("reconciling", "failed"):
                return
            connection = self.daemon.connections.get(actor["listener"])
            if not connection or connection.closed:
                return
            if message["delivery"] in ("submitting", "uncertain", "submitted"):
                await self.reconcile(message, actor)
                # Absence after an uncertain RPC is not proof it was never submitted.
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
            if not active and self.daemon.hook_stale(listener):
                # Admitted by another release, its calls would be refused: it is replaced first, once its unit is
                # quiet, and this message stays pending until then.
                self.daemon.readmit(target["run"])
                return
            method = "turn/steer" if active else ("thread/queue/add" if message["wake"] == "queue" else "turn/start")
            params = {"threadId": target["thread"], "input": [{"type": "text", "text": text}], "clientUserMessageId": native_id}
            if active:
                params["expectedTurnId"] = active
            attempt = uid("delivery")
            message["attempts"].append(attempt)
            message["delivery"] = "submitting"
            with self.store.transaction():
                self.store.save(message)
                self.store.put("delivery", attempt, {"message": ident, "method": method, "actor": target["id"],
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
                        target["active_turn"] = None
                        async for turns in page(connection, "thread/turns/list", {"threadId": target["thread"], "itemsView": "full"}):
                            for turn in turns["data"]:
                                if turn["status"] == "inProgress":
                                    target["active_turn"] = turn["id"]
                        self.store.save(target)
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

    def entered(self, actorid, native_id, evidence):
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

    def ack(self, actorid, ident, session_id=""):
        message = self.store.get(ident)
        if session_id:
            mailbox = self.store.get(message["recipient"], required=False)
            if not mailbox or mailbox["_kind"] != "lead_mailbox" or mailbox["session_id"] != session_id:
                raise CoordinatorError("ACK_ACTOR", "Only the owning lead session can acknowledge this message")
            actorid = mailbox["id"]
        if message["recipient"] != actorid:
            raise CoordinatorError("ACK_ACTOR", "Only the actual recipient can acknowledge this message")
        if message["state"] == "acknowledged":
            return {"message": ident, "state": "acknowledged", "duplicate": True}
        if actorid != "lead" and not actorid.startswith("lead_mailbox_") and message["state"] != "entered_context":
            actor = self.store.get(actorid)
            if actor["kind"] != "child":
                raise CoordinatorError("ACK_CONTEXT", "Native context echo has not yet been reconciled")
            # A child's own dynamic call proves receipt independently of the root.
            self.transition(message, "entered_context", {"child_actor": actorid, "evidence": "native child acknowledgement call"})
        self.transition(message, "acknowledged", {"actor": actorid})
        return {"message": ident, "state": "acknowledged", "duplicate": False}

    def child_ack(self, actor, item):
        for ident in re.findall(r"(?m)^C1_ACK (msg_[0-9a-f]{32})\s*$", item.get("text", "")):
            self.ack(actor["id"], ident)

    def inbox(self, recipient="lead", cursor="", limit=100, session_id=""):
        if type(limit) is not int or limit < 1:
            raise CoordinatorError("PAGE_SIZE", "Page size must be positive")
        if session_id:
            # Session mail is the union of its durable unit mailboxes. Keep the
            # original recipients, message IDs and acknowledgement history.
            scope = ("kind='message' AND json_extract(data,'$.recipient') IN "
                     "(SELECT id FROM records WHERE kind='lead_mailbox' AND json_extract(data,'$.session_id')=?)")
            records = list(self.store.selected("SELECT * FROM records WHERE " + scope + " AND id>? ORDER BY id LIMIT ?",
                                              (session_id, cursor, limit + 1)))
            unread = self.store.db.execute("SELECT count(*) FROM records WHERE " + scope +
                                           " AND json_extract(data,'$.state')!='acknowledged'", (session_id,)).fetchone()[0]
            return {"data": records[:limit], "next_cursor": records[limit - 1]["id"] if len(records) > limit else None,
                    "unread": unread}
        recipient, _ = self.recipient(recipient)
        inbox = [m for m in self.store.all("message") if m["recipient"] == recipient]
        records = [m for m in inbox if m["id"] > cursor]
        return {"data": records[:limit], "next_cursor": records[limit - 1]["id"] if len(records) > limit else None,
                "unread": sum(m["state"] != "acknowledged" for m in inbox)}

    async def reconcile(self, message, actor):
        target = actor if actor["kind"] != "child" else self.store.get(self.store.get(actor["run"])["root_actor"])
        connection = self.daemon.connections.get(target["listener"])
        if not connection or connection.closed:
            return
        async for result in page(connection, "thread/items/list", {"threadId": target["thread"], "sortDirection": "asc"}):
            for entry in result["data"]:
                item = entry["item"]
                if item.get("type") == "userMessage" and item.get("clientId") in (message["native_id"], message["native_id"] + ":relay"):
                    self.entered(target["id"], item["clientId"], {"history": True, "turn": entry["turnId"], "item": item["id"]})
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
                await self.reconcile(message, actor)
            message = self.store.get(ident)
            if message["state"] in ("entered_context", "acknowledged") or message["delivery"] == "submitted":
                return message
            message["delivery"] = "pending"
            self.store.save(message)
            self.store.event("message/retry_requested", ident, {"native_id": message["native_id"], "possible_duplicate_native_delivery": True})
        self.daemon.background(self.deliver(ident))
        return message

    async def retry(self):
        # Every second: decode only messages still to be delivered, not every message ever sent.
        for message in list(self.store.selected("SELECT * FROM records WHERE kind='message' AND "
                "json_extract(data,'$.state') NOT IN ('entered_context','acknowledged') AND "
                "json_extract(data,'$.recipient')!='lead' AND substr(json_extract(data,'$.recipient'),1,13)!='lead_mailbox_' ORDER BY id")):
            if message["state"] not in ("entered_context", "acknowledged") and message["recipient"] != "lead" and not message["recipient"].startswith("lead_mailbox_"):
                try:
                    await self.deliver(message["id"])
                except (CoordinatorError, OSError):
                    pass  # durable pending/uncertain status remains visible
