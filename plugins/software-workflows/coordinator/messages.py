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
        actorid, actor = self.recipient(recipient)
        ident = uid("msg")
        self.store.put("message", ident, {"sender": sender, "recipient": actorid, "unit": actor and actor["unit"],
                       "text": text, "wake": wake, "state": "persisted", "created": time.time(),
                       "attempts": [], "native_id": ident, "delivery": "pending"})
        self.store.event("message/persisted", ident, {"sender": sender, "recipient": actorid})
        if actor:
            self.daemon.background(self.deliver(ident))
        return self.store.get(ident)

    async def deliver(self, ident):
        async with self.locks.hold(ident):
            message = self.store.get(ident)
            if message["state"] in ("entered_context", "acknowledged") or message["recipient"] == "lead" or message["delivery"] in ("cancelled", "turn_ended"):
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
            await self.daemon.ensure_lease(target["checkout"])
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

    def ack(self, actorid, ident):
        message = self.store.get(ident)
        if message["recipient"] != actorid:
            raise CoordinatorError("ACK_ACTOR", "Only the actual recipient can acknowledge this message")
        if message["state"] == "acknowledged":
            return {"message": ident, "state": "acknowledged", "duplicate": True}
        if actorid != "lead" and message["state"] != "entered_context":
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

    def inbox(self, recipient="lead", cursor="", limit=100):
        recipient, _ = self.recipient(recipient)
        if type(limit) is not int or limit < 1:
            raise CoordinatorError("PAGE_SIZE", "Page size must be positive")
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
        for message in list(self.store.all("message")):
            if message["state"] not in ("entered_context", "acknowledged") and message["recipient"] != "lead":
                try:
                    await self.deliver(message["id"])
                except (CoordinatorError, OSError):
                    pass  # durable pending/uncertain status remains visible
