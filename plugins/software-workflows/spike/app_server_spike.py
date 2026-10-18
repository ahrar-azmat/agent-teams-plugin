"""codex app-server spike (0.151.0): protocol, pin, disconnect, resume, unix-socket multi-client."""
import base64, glob, json, os, secrets, socket, subprocess, sys, tempfile, threading, time, uuid
from pathlib import Path



def _main():  # round 23: no import-time side effects (tempdir, subprocesses, paid provider calls)
    WS = Path(tempfile.mkdtemp(prefix="appserver-spike-")); os.chdir(WS)
    NONCE = secrets.token_hex(3)
    FINDINGS = []
    def note(k, v): FINDINGS.append((k, v)); print(f"  · {k}: {v}", flush=True)

    class Stdio:
        def __init__(self, tag):
            self.p = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=open(WS / f"{tag}.stderr", "wb"), cwd=WS, text=False)
            self.i = 0; self.events = []; self.results = {}; self.spool = open(WS / f"{tag}.events.jsonl", "a")
            self.reader = threading.Thread(target=self._read, daemon=True); self.reader.start()
        def _read(self):
            for line in self.p.stdout:
                try: m = json.loads(line)
                except ValueError: continue
                self.spool.write(json.dumps(m) + "\n"); self.spool.flush()
                if "id" in m and "method" not in m: self.results[m["id"]] = m
                elif "method" in m and "id" in m:   # server-initiated request (approval etc.) → decline
                    self.send({"id": m["id"], "error": {"code": -1, "message": "spike: declined"}})
                else: self.events.append(m)
        def send(self, msg):
            self.p.stdin.write((json.dumps(msg) + "\n").encode()); self.p.stdin.flush()
        def call(self, method, params, timeout=60):
            self.i += 1; i = self.i; self.send({"id": i, "method": method, "params": params})
            t0 = time.time()
            while i not in self.results:
                if time.time() - t0 > timeout: raise TimeoutError(method)
                time.sleep(0.05)
            r = self.results.pop(i)
            if "error" in r: raise RuntimeError(f"{method}: {r['error']}")
            return r["result"]
        def wait_event(self, method, timeout):
            t0 = time.time(); seen = 0
            while time.time() - t0 < timeout:
                snap = list(self.events)  # advance by the PROCESSED snapshot only
                for m in snap[seen:]:     # (an append between slice and len was skipped)
                    if m.get("method") == method: return m
                seen = len(snap); time.sleep(0.1)
            return None
        def init(self):
            r = self.call("initialize", {"clientInfo": {"name": "codex_oracle_spike", "title": "spike", "version": "0"},
                                         "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized"}); return r

    def agent_text(events):
        out = []
        for m in events:
            if m.get("method") == "item/completed":
                it = (m.get("params") or {}).get("item") or {}
                if it.get("type") in ("agentMessage", "agent_message"): out.append(str(it.get("text") or ""))
        return out

    print("=== S1: stdio protocol, per-turn model/effort pin, event richness ===", flush=True)
    a = Stdio("s1"); init = a.init(); note("initialize.result keys", sorted(init.keys())[:8])
    try:
        th = a.call("thread/start", {"model": "gpt-5.6-sol", "cwd": str(WS), "approvalPolicy": "never", "sandbox": "read-only"})
    except RuntimeError as e:
        note("thread/start sandbox=readOnly rejected", str(e)[:120]); th = a.call("thread/start", {"model": "gpt-5.6-sol", "cwd": str(WS), "approvalPolicy": "never"})
    tid = th["thread"]["id"]; note("thread id", tid); note("thread/start result keys", sorted(th.keys())); note("PIN as stored on the thread", {k: th.get(k) for k in ("model", "reasoningEffort", "sandbox", "approvalPolicy")})
    t0 = time.time()
    tr = a.call("turn/start", {"threadId": tid, "effort": "max", "model": "gpt-5.6-sol",
                               "input": [{"type": "text", "text": f"Run the shell command `sleep 5`, then reply with exactly: APP-OK-{NONCE}"}]})
    note("turn/start result keys", sorted(tr.keys()))
    done = a.wait_event("turn/completed", 300)
    note("turn/completed after", f"{time.time()-t0:.0f}s" if done else "TIMEOUT")
    if done: note("turn/completed params keys", sorted((done.get("params") or {}).keys())); note("turn status/usage", {k: (done["params"].get("turn") or {}).get(k) for k in ("status", "usage")} if isinstance(done["params"].get("turn"), dict) else str(done["params"])[:200])
    methods = {}
    for m in a.events: methods[m.get("method")] = methods.get(m.get("method"), 0) + 1
    note("event methods seen", methods)
    note("agent answer(s)", [t[:60] for t in agent_text(a.events)])
    # what did the turn run with? settings notification / thread read
    try:
        rd = a.call("thread/read", {"threadId": tid}); thr = rd.get("thread") or {}
        note("thread/read keys", sorted(thr.keys())[:14]); note("thread model/effort (as stored)", {k: thr.get(k) for k in ("model", "effort", "reasoningEffort", "settings") if k in thr})
    except Exception as e: note("thread/read", f"error {str(e)[:100]}")
    a.p.stdin.close(); a.p.wait(timeout=15); note("S1 server exit after client stdin close", a.p.returncode)

    print("=== S2: client dies mid-turn (both pipes closed) ===", flush=True)
    b = Stdio("s2"); b.init()
    th2 = b.call("thread/start", {"model": "gpt-5.6-sol", "cwd": str(WS), "approvalPolicy": "never", "sandbox": "read-only"}); tid2 = th2["thread"]["id"]
    b.call("turn/start", {"threadId": tid2, "effort": "low", "input": [{"type": "text", "text": f"Run the shell command `sleep 40`, then reply with exactly: DISC-OK-{NONCE}"}]})
    b.wait_event("turn/started", 60); time.sleep(5)
    pid = b.p.pid; b.p.stdin.close(); b.p.stdout.close()   # the client is gone
    t0 = time.time()
    while b.p.poll() is None and time.time() - t0 < 90: time.sleep(0.5)
    note("S2 app-server exit code after client death", b.p.returncode if b.p.poll() is not None else f"STILL RUNNING after 90s (pid {pid})")
    roll = sorted(glob.glob(os.path.expanduser(f"~/.codex/sessions/*/*/*/rollout-*{tid2}*.jsonl")))
    txt = Path(roll[-1]).read_text(errors="replace") if roll else ""
    note("S2 rollout shows the answer token", f"DISC-OK-{NONCE}" in txt)
    note("S2 rollout last event types", [json.loads(l).get("payload", {}).get("type") for l in txt.strip().splitlines()[-3:]] if txt else "no rollout")
    if b.p.poll() is None: b.p.kill()

    print("=== S3: thread/resume in a NEW app-server, after the client death ===", flush=True)
    c = Stdio("s3"); c.init()
    try:
        rs = c.call("thread/resume", {"threadId": tid2, "excludeTurns": True}); note("thread/resume result keys", sorted(rs.keys()))
        note("resumed thread status", (rs.get("thread") or {}).get("status"))
        c.call("turn/start", {"threadId": tid2, "effort": "low", "input": [{"type": "text", "text": "What exact token were you asked to reply with in the previous turn, and did you finish that turn? Answer in one line."}]})
        done = c.wait_event("turn/completed", 240); note("S3 resumed turn completed", bool(done)); note("S3 answer", [t[:140] for t in agent_text(c.events)])
    except Exception as e: note("S3 error", str(e)[:200])
    c.p.stdin.close(); c.p.wait(timeout=15)

    print("=== S4: unix-socket transport — second client attaches mid-turn, first disconnects ===", flush=True)
    sockp = WS / "as.sock"
    d = subprocess.Popen(["codex", "app-server", "--listen", f"unix://{sockp}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(WS/"s4.stderr","wb"), cwd=WS)
    t0 = time.time()
    while not sockp.exists() and time.time() - t0 < 15: time.sleep(0.2)
    note("S4 unix socket created", sockp.exists())
    class WsClient:
        def __init__(self):
            self.s = socket.socket(socket.AF_UNIX); self.s.connect(str(sockp)); self.s.settimeout(300)
            key = base64.b64encode(os.urandom(16)).decode()
            self.s.sendall((f"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            hdr = b""
            while b"\r\n\r\n" not in hdr: hdr += self.s.recv(4096)
            self.ok = hdr.startswith(b"HTTP/1.1 101"); self.buf = hdr.split(b"\r\n\r\n", 1)[1]; self.i = 0; self.events = []; self.results = {}
        def send(self, msg):
            p = json.dumps(msg).encode(); mask = os.urandom(4)
            hdr = bytes([0x81]) + (bytes([0x80 | len(p)]) if len(p) < 126 else bytes([0x80 | 126]) + len(p).to_bytes(2, "big"))
            self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))
        def _need(self, n):
            while len(self.buf) < n:
                chunk = self.s.recv(65536)
                if not chunk: raise ConnectionError("closed")
                self.buf += chunk
        def recv(self):
            self._need(2); b0, b1 = self.buf[0], self.buf[1]; ln = b1 & 0x7F; off = 2
            if ln == 126: self._need(4); ln = int.from_bytes(self.buf[2:4], "big"); off = 4
            elif ln == 127: self._need(10); ln = int.from_bytes(self.buf[2:10], "big"); off = 10
            self._need(off + ln); payload = self.buf[off:off + ln]; self.buf = self.buf[off + ln:]
            op = b0 & 0x0F
            if op == 0x8: raise ConnectionError("close frame")
            if op == 0x9: return None  # ping — ignore for the spike
            return json.loads(payload.decode()) if op == 0x1 else None
        def pump(self, until_method=None, timeout=10):
            t0 = time.time(); self.s.settimeout(1.0)
            while time.time() - t0 < timeout:
                try: m = self.recv()
                except socket.timeout: continue
                if not m: continue
                if "id" in m and "method" not in m: self.results[m["id"]] = m
                elif "method" in m and "id" in m: self.send({"id": m["id"], "error": {"code": -1, "message": "declined"}})
                else:
                    self.events.append(m)
                    if until_method and m.get("method") == until_method: return m
            return None
        def call(self, method, params, timeout=60):
            self.i += 1; i = self.i; self.send({"id": i, "method": method, "params": params}); t0 = time.time()
            while i not in self.results:
                self.pump(timeout=1)
                if time.time() - t0 > timeout: raise TimeoutError(method)
            r = self.results.pop(i)
            if "error" in r: raise RuntimeError(f"{method}: {r['error']}")
            return r["result"]
        def init(self):
            r = self.call("initialize", {"clientInfo": {"name": "codex_oracle_spike", "title": "spike", "version": "0"}, "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized"}); return r
    try:
        w1 = WsClient(); note("S4 ws handshake (client 1)", w1.ok); w1.init()
        th4 = w1.call("thread/start", {"model": "gpt-5.6-sol", "cwd": str(WS), "approvalPolicy": "never", "sandbox": "read-only"}); tid4 = th4["thread"]["id"]
        w1.call("turn/start", {"threadId": tid4, "effort": "low", "input": [{"type": "text", "text": f"Run the shell command `sleep 30`, then reply with exactly: MULTI-OK-{NONCE}"}]})
        w1.pump("turn/started", 30)
        w2 = WsClient(); note("S4 ws handshake (client 2)", w2.ok); w2.init()
        rs = w2.call("thread/resume", {"threadId": tid4, "excludeTurns": True}); note("S4 client 2 thread/resume on the LOADED thread", {"status": (rs.get("thread") or {}).get("status")})
        w1.s.close(); note("S4 client 1 disconnected mid-turn", True)
        done = w2.pump("turn/completed", 240)
        note("S4 client 2 received turn/completed after client 1 left", bool(done))
        note("S4 client 2 saw the answer", any(f"MULTI-OK-{NONCE}" in t for t in agent_text(w2.events)))
        note("S4 app-server still alive", d.poll() is None)
    except Exception as e:
        note("S4 error", f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        d.kill()

    print("\n=== FINDINGS ===")
    for k, v in FINDINGS: print(f"{k}: {v}")
    print("workspace:", WS)


if __name__ == "__main__":
    _main()
