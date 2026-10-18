#!/usr/bin/env python3
"""Codex Office: a pixel-art office where every Codex Oracle run is a worker at a desk. Click a
worker to read its whole conversation as it happens, pause it, or send it more instructions, images
and file references. /software-workflows:office starts it.

    python3 office.py start [--open] [--port N] [--data DIR]   # start (or find) the office; prints its address
    python3 office.py status | stop [--data DIR]
    python3 office.py serve [--port N] [--data DIR]            # in the foreground, for a terminal

It reads what this plugin's server records under ~/.claude/logs/codex-oracle: the run journal
(runs.jsonl) and each run's event spool (runs/<run>/attempt<N>.stdout.jsonl). It acts only through
this plugin's own tools, codex_cancel_run and codex_resume_run, run by this plugin's own MCP server
(run_server.py beside this folder), so a pause or a follow-up sent from here lands in the same
journal, locks and run budget as one sent from Claude Code. Actions run detached, with launchd as
their parent: stopping the office or restarting Claude Code does not stop them. Uploads, the action
records and server.json (the running office) live in <plugin data>/office.

Standard library only; Python 3.9+. It listens on 127.0.0.1 only and refuses API requests that lack
the per-start token the page carries, or that name a foreign Host (DNS rebinding).
"""
import argparse
import fcntl
import hashlib
import json
import mimetypes
import os
import queue
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # the plugin: run_server.py, .claude-plugin/plugin.json
LOG_DIR = Path(os.environ.get("CODEX_OFFICE_LOG_DIR") or Path.home() / ".claude" / "logs" / "codex-oracle")
DEFAULT_DATA = Path.home() / ".claude" / "plugins" / "data" / "software-workflows-agent-teams"
DATA = DEFAULT_DATA                     # set by configure(): ${CLAUDE_PLUGIN_DATA} when Claude Code starts it
STATE = DATA / "office"
UPLOADS = STATE / "uploads"
ACTIONS = STATE / "actions"
TASK_HEADERS = ("## Task", "## Question", "## Research topic", "## Review request", "## Code to review")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
LIVE = ("RUNNING", "DETACHED", "ORPHANED", "ORPHANED-WRITE", "CANCELLING", "QUEUED", "HANDOFF")
CHUNK = 1 << 20          # bytes of spool read per events request; the page asks again until caught up
OUT_CLIP = 6000          # command output kept in a digest; the full item is one /api/item away
FRESH_LOG_S = 60.0       # the server's own reading (_run_status): a live log written this recently is a live run
# abraham's analysis ends, then the server snapshots the tree (treedigest: 20 s deadline + 3 s grace) and
# starts the writer; measured on the journal's 37 real pairs, 0.1-1.0 s apart. The evidence that the call
# is still under way is its tree write lock (dispatch_open); only when that cannot be read (no lsof) does
# an ok analysis with no writer read HANDOFF for this long, and a queued note then refuses rather than guess.
HANDOFF_S = 90.0
# Deadlines for the plugin's MCP server when an action drives it. Its first start may build the plugin's
# venv (minutes on a cold cache); tools/list is local; a cancel kills a process group and polls ~1 s.
INIT_S, LIST_S, CANCEL_S = 600.0, 60.0, 120.0
# A resume gets what Claude Code gives the same call: the plugin's MCP call timeout (.mcp.json, 4 h), which
# already sits 30 min over the server's largest request budget. call_timeout() reads it.
CALL_DEFAULT_S = 4 * 3600.0
STOP_WAIT_S = 120.0      # how long a pause waits for the journal to show the run stopped
TOKEN = secrets.token_urlsafe(24)
NAMES = ("Sprocket", "Nibble", "Widget", "Pixel", "Gizmo", "Cobalt", "Fennel", "Juniper", "Basil", "Tango",
         "Mochi", "Biscuit", "Comet", "Pepper", "Quill", "Rivet", "Saffron", "Tofu", "Umber", "Velvet",
         "Wren", "Yuzu", "Ziggy", "Bramble", "Clover", "Dynamo", "Ember", "Flint", "Gadget", "Hopper",
         "Indigo", "Jasper", "Kiwi", "Lumen", "Marble", "Nova", "Orbit", "Pistachio", "Quasar", "Rusty",
         "Sable", "Tinker", "Unit", "Vector", "Waffle", "Xeno", "Yarrow", "Zest", "Acorn", "Bolt",
         "Cinder", "Dot", "Echo", "Fable", "Glimmer", "Hazel", "Iris", "Jolt", "Kernel", "Lark",
         "Maple", "Nugget", "Opal", "Pebble", "Quokka", "Ripple", "Sprout", "Thistle", "Ursa", "Vortex",
         "Whisk", "Axle", "Yeti", "Zephyr", "Alder", "Bean", "Crumb", "Dune", "Elm", "Fizz",
         "Gravel", "Holly", "Inkwell", "Jinx", "Koi", "Loom", "Mango", "Nimbus", "Oolong", "Puddle",
         "Quartz", "Radish", "Scout", "Truffle", "Ukulele", "Vim", "Wasabi", "Pixie", "Yodel", "Zinc",
         "Aster", "Bingo", "Cocoa", "Doodle", "Elderberry", "Fudge", "Ginger", "Hickory", "Igloo", "Jelly",
         "Kazoo", "Lentil", "Muffin", "Noodle", "Olive", "Paprika", "Quince", "Rooibos", "Sesame", "Tamarind",
         "Upsy", "Vanilla", "Walnut", "Xylo", "Yam", "Zucchini", "Anchor", "Beacon", "Compass", "Drift",
         "Ferry", "Galley", "Harbor", "Island", "Jetty", "Kayak", "Lagoon", "Marina", "Nautilus", "Oar",
         "Paddle", "Quay", "Reef", "Sail", "Tide", "Undertow", "Voyage", "Wharf", "Buoy", "Coral",
         "Atlas", "Blip", "Cipher", "Delta", "Enigma", "Fractal", "Glyph", "Helix", "Ion", "Joule",
         "Kelvin", "Lambda", "Matrix", "Neutron", "Omega", "Photon", "Qubit", "Radix", "Sigma", "Tensor",
         "Uplink", "Vertex", "Watt", "Xenon", "Yotta", "Zenith", "Abacus", "Bitty", "Cog", "Diode",
         "Emoji", "Fuse", "Gear", "Hex", "Inky", "Jumper", "Knob", "Lever", "Modem", "Nano")
# The office's cast. A worker's role is the one picked for it in the office, else the one its caller named
# with a "Role: <name>" line in the task or context, else the one its Codex tool implies (the first six).
# Guessing a role from the task's wording was measured on the real journal and mislabelled most of the runs
# it touched (regulation research read as DevOps, provider research as Data Analyst), so there is none.
ROLES = {"abraham-write": "Builder", "abraham": "Planner", "code_review": "Reviewer",
         "architect_review": "Architect", "research": "Researcher", "codex_query": "Analyst",
         "ceo": "CEO", "cto": "CTO", "project_manager": "Project Manager", "product_manager": "Product Manager",
         "business_analyst": "Business Analyst", "qa": "QA Engineer", "security": "Security Engineer",
         "devops": "DevOps Engineer", "designer": "Designer", "data_analyst": "Data Analyst",
         "tech_writer": "Tech Writer"}
ROLE_ALIASES = {"ba": "business_analyst", "qa": "qa", "tester": "qa", "sre": "devops", "devops": "devops",
                "ops": "devops", "ux": "designer", "ui": "designer", "ux designer": "designer",
                "ui designer": "designer", "program manager": "project_manager", "delivery manager": "project_manager",
                "pm (project manager)": "project_manager", "pm (product manager)": "product_manager",
                "chief executive": "ceo", "chief technology officer": "cto", "writer": "tech_writer",
                "technical writer": "tech_writer", "data scientist": "data_analyst", "analyst": "codex_query",
                "builder": "abraham-write", "planner": "abraham", "reviewer": "code_review",
                "architect": "architect_review", "researcher": "research", "security": "security"}


def configure(data):
    """Point every state path at the plugin's data directory (the command passes ${CLAUDE_PLUGIN_DATA})."""
    global DATA, STATE, UPLOADS, ACTIONS
    DATA = Path(data or os.environ.get("CLAUDE_PLUGIN_DATA") or DEFAULT_DATA).expanduser()
    STATE, UPLOADS, ACTIONS = DATA / "office", DATA / "office" / "uploads", DATA / "office" / "actions"


def plugin_version():
    return (read_json(ROOT / ".claude-plugin" / "plugin.json", {}) or {}).get("version") or "?"


def write_json(path, obj):
    tmp = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- the journal and run status

class Journal:
    """The plugin's run journal folded into one record per run, read incrementally. The fold is the
    server's own (_journal_runs): later records update the run, and a terminal status is immutable to
    later non-terminal records. A rotation (runs.jsonl moved to runs.jsonl.1) re-reads both files."""

    def __init__(self):
        self.lock = threading.Lock()
        self.runs = {}
        self.pos = {}

    def _apply(self, rec):
        run = self.runs.setdefault(rec["run"], {})
        if run.get("has_end") and rec.get("phase") != "end" and "status" in rec:
            rec = {k: v for k, v in rec.items() if k != "status"}
        run.update({k: v for k, v in rec.items() if k != "phase"})
        run["has_" + str(rec.get("phase", "?"))] = True
        run.setdefault("first_ts", rec.get("ts"))
        run["last_ts"] = rec.get("ts")

    def refresh(self):
        """Fold what was appended since the last call; returns a snapshot {run: record}."""
        with self.lock:
            paths = (LOG_DIR / "runs.jsonl.1", LOG_DIR / "runs.jsonl")
            stats = {}
            for p in paths:
                try:
                    st = p.stat()
                    stats[p] = (st.st_ino, st.st_size)
                except OSError:
                    stats[p] = None
            for p in paths:
                seen = self.pos.get(p)
                if seen and (stats[p] is None or stats[p][0] != seen[0] or stats[p][1] < seen[1]):
                    self.runs, self.pos = {}, {}
                    break
            for p in paths:
                if stats[p] is None:
                    continue
                ino, size = stats[p]
                off = self.pos.get(p, (ino, 0))[1]
                if size > off:
                    with open(p, "rb") as fh:
                        fh.seek(off)
                        data = fh.read(size - off)
                    end = data.rfind(b"\n") + 1
                    for line in data[:end].splitlines():
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(rec, dict) and rec.get("run"):
                            self._apply(rec)
                    off += end
                self.pos[p] = (ino, off)
            return {k: dict(v) for k, v in self.runs.items()}


_ps_lock = threading.Lock()
_ps = {"t": 0.0, "table": {}}


CODEX_CMD = ("codex",)                                   # HEAD's _pid_alive, when no start time was recorded
SERVER_CMD = ("server.py", "codex-oracle", "run_server")  # HEAD's _server_alive, likewise


def processes():
    """{pid: (start time, command)} for every running process; the start time in the journal's pid_start
    form (`ps` lstart joined by _), the command lowercased. Zombies are left out: they are not running.
    None when `ps` fails: unknown, not "all dead"."""
    with _ps_lock:
        if time.time() - _ps["t"] > 1.0:
            table = {}
            try:
                out = subprocess.run(["ps", "-axo", "pid=,stat=,lstart=,command="], capture_output=True,
                                     text=True, timeout=10)
                if out.returncode != 0:
                    raise OSError(out.returncode)
                for line in out.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 7 and parts[0].isdigit() and not parts[1].startswith("Z"):
                        table[int(parts[0])] = ("_".join(parts[2:7]), " ".join(parts[7:]).lower())
            except (OSError, subprocess.SubprocessError):
                table = None
            _ps.update(t=time.time(), table=table)
        return _ps["table"]


def alive(pid, start, table, strict=False, cmd=()):
    """Is this process running and still the one recorded? As the server's _pid_alive and _server_alive:
    a recorded start time settles it; without one, the command line must name what it should be (`cmd`);
    unknown evidence (no process table) reads alive, except under `strict`, where only a verified start
    time counts."""
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if table is None:
        return not strict
    if pid not in table:
        return False
    began, command = table[pid]
    if start:
        return "_".join(str(start).replace("_", " ").split()) == began
    if strict:
        return False
    return not cmd or not command or any(c in command for c in cmd)


def cancel_marker(run):
    return LOG_DIR / "runs" / "cancel" / (str(run).replace("·", "-") + ".cancel")


def _tag_pid(run):
    """The pid in a run tag (infra1·14040·ac5f → 14040): the server that took the call."""
    m = re.search(r"·(\d+)·", str(run or ""))
    return int(m.group(1)) if m else 0


def _began(lstart):
    try:
        return time.mktime(time.strptime(" ".join(str(lstart).split("_")), "%a %b %d %H:%M:%S %Y"))
    except (ValueError, OverflowError):
        return None


def owner_pid(rec):
    return int(rec.get("server_pid") or 0) or _tag_pid(rec.get("run"))


def owner_alive(rec, table):
    """Is the server that took this run still running? Its spawn record names it (server_pid and its
    start); before the spawn only the run tag does, so that pid must belong to a process that began
    before the run did (a later process reusing the pid is not it)."""
    if rec.get("server_pid"):
        return alive(rec.get("server_pid"), rec.get("server_start"), table, cmd=SERVER_CMD)
    pid = _tag_pid(rec.get("run"))
    if table is None:
        return bool(pid)
    began = _began(table[pid][0]) if pid in table else None
    return began is not None and began <= float(rec.get("first_ts") or 0) + 1


def _log_fresh(rec):
    try:
        return bool(rec.get("log")) and time.time() - os.stat(rec["log"]).st_mtime < FRESH_LOG_S
    except OSError:
        return False


def run_status(rec, table):
    """The server's reading of a run (its _run_status), made from outside it: RUNNING / DETACHED (its
    server is gone, its bounded codex process lives on) / ORPHANED (alive with no owner or no deadline
    enforcer) / CANCELLING / QUEUED (taken, nothing spawned yet) / DETACHED-ENDED / INTERRUPTED, or the
    terminal status the journal records."""
    if rec.get("has_end"):
        return str(rec.get("status") or "?")
    if rec.get("has_cancel_requested") or cancel_marker(rec.get("run")).exists():
        # As the server reads it: a standing cancel is CANCELLING whatever the pids say. The marker stays
        # while any process of the run may survive, and only codex_cancel_run, retried, can verify them
        # gone and end the run; cancel_stuck() tells the page to offer that retry.
        return "CANCELLING"
    if not rec.get("has_spawn"):
        # Taken, nothing spawned yet (a capacity wait, a lock wait): live while the server that took it is.
        return "QUEUED" if owner_alive(rec, table) else "RUNNING" if _log_fresh(rec) else "INTERRUPTED"
    child = alive(rec.get("pid"), rec.get("pid_start"), table, cmd=CODEX_CMD)
    owned = bool(rec.get("server_pid"))
    server = owned and alive(rec.get("server_pid"), rec.get("server_start"), table, cmd=SERVER_CMD)
    if rec.get("write") and child and owned and not server:
        return "ORPHANED-WRITE"  # a writer outlived its server: stop it, never adopt it
    detached = (not rec.get("write") and bool(rec.get("watchdog_pid"))
                and not (child and not alive(rec.get("watchdog_pid"), rec.get("watchdog_start"), table, strict=True))
                and (bool(rec.get("has_detached")) or (owned and not server)))
    if detached:
        return "DETACHED" if child else "DETACHED-ENDED"  # codex_resume_run collects what it left
    if child and owned and not server:
        return "ORPHANED"  # alive, owner gone, no deadline enforcer
    if child or _log_fresh(rec):
        return "RUNNING"
    return "INTERRUPTED"


def cancel_stuck(rec, table):
    """A standing cancel that nothing is acting on: its server is gone and so is its codex process.
    The page offers to retry the cancel, which is what ends it."""
    return (run_status(rec, table) == "CANCELLING" and not owner_alive(rec, table)
            and not alive(rec.get("pid"), rec.get("pid_start"), table, cmd=CODEX_CMD))


_titles = {}


def title_of(rec):
    """The caller's own words: the first line under the prompt's task header."""
    run = rec.get("run")
    if run in _titles:
        return _titles[run]
    prompt = str(rec.get("prompt") or "")
    best = None
    for h in TASK_HEADERS:
        i = 0 if prompt.startswith(h) else prompt.find("\n" + h)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, h)
    text = prompt[best[0] + len(best[1]) + (0 if best[0] == 0 else 1):] if best else prompt
    title = ""
    for line in text.splitlines():
        line = line.strip().strip("#*_`>").strip()
        if line and not line.startswith(("```", "---")) and not re.match(r"(?i)(?:office\s+)?role\s*[:=]", line):
            title = line[:160]
            break
    if rec.get("has_start"):
        _titles[run] = title
    return title


def task_of(prompt):
    """The prompt's task section (header to the next ## header), for the chat's lead bubble."""
    prompt = str(prompt or "")
    for h in TASK_HEADERS:
        i = 0 if prompt.startswith(h) else prompt.find("\n" + h)
        if i >= 0:
            body = prompt[i:].lstrip("\n")
            nxt = body.find("\n## ", len(h))
            return body[:nxt] if nxt > 0 else body
    return ""


_STAMP = re.compile(r"^\[\s*[\d.]+s[^\]]*\]\s?(.*)$")


def activity(rec):
    """The run's current step: the last stamped line of its live log (a reasoning step's headline)."""
    log = rec.get("log")
    if not log:
        return ""
    try:
        with open(log, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 16384))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for i in range(len(lines) - 1, -1, -1):
        m = _STAMP.match(lines[i])
        if not m:
            continue
        body = m.group(1).strip()
        if body.startswith("reasoning:"):
            rest = body[len("reasoning:"):].strip()
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    rest = rest or nxt.strip()
                    break
            return ("reasoning: " + rest.strip("* ")).strip()[:200]
        return body[:200]
    return ""


def role_key(name):
    """A role name as a person or a caller writes it ("Product Manager", "BA", "qa") → its key, or None."""
    n = re.sub(r"\s+", " ", str(name or "").strip().lower().strip("*_`.:;,"))
    if n in ROLES:
        return n
    for key, label in ROLES.items():
        if n in (label.lower(), key.replace("_", " ")):
            return key
    return ROLE_ALIASES.get(n)


_ROLE_LINE = re.compile(r"(?im)^[\s>*_-]*(?:office\s+)?role\s*[:=]\s*\**\s*([A-Za-z][A-Za-z ()/&-]{0,40}?)\s*\**\s*$")
_tags = {}


def tagged_role(rec):
    """The role a caller named with a "Role: <name>" line anywhere in the run's prompt, or None."""
    run = rec.get("run")
    if run not in _tags:
        found = None
        for m in _ROLE_LINE.finditer(str(rec.get("prompt") or "")):
            found = role_key(m.group(1)) or found
        if rec.get("has_start"):
            _tags[run] = found
        return found
    return _tags[run]


_overrides = {"mtime": None, "roles": {}}


def role_overrides():
    """The roles picked in the office (<state>/roles.json: worker id → role key)."""
    p = STATE / "roles.json"
    try:
        m = p.stat().st_mtime
    except OSError:
        return {}
    if _overrides["mtime"] != m:
        _overrides.update(mtime=m, roles=read_json(p, {}) or {})
    return _overrides["roles"]


_roles_lock = threading.Lock()


def set_role(worker, role):
    if role and role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    with _roles_lock:
        STATE.mkdir(parents=True, exist_ok=True)
        roles = dict(read_json(STATE / "roles.json", {}) or {})
        if role:
            roles[str(worker)] = role
        else:
            roles.pop(str(worker), None)
        write_json(STATE / "roles.json", roles)


def _hash(key):
    return int(hashlib.md5(str(key).encode()).hexdigest()[:8], 16)


def names_for(keys):
    """A name per worker from its own key, so it never changes; when two workers on screen share one,
    the later gets a numeral (Sable, Sable II)."""
    seen, out = {}, {}
    for key in keys:
        base = NAMES[_hash(key) % len(NAMES)]
        seen[base] = seen.get(base, 0) + 1
        n = seen[base]
        out[key] = base if n == 1 else f"{base} {['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX'][n] if n < 10 else n}"
    return out


_BRIEF = re.compile(r"\n## Implementation brief \(from the analysis phase\)\n"
                    r"\[Codex model: [^\]\n]*\| tool:abraham \| status:ok \|[^\]\n]*\]\n")
_LOG_PTR = re.compile(r"\[live log: ([^\]\n]+)\]")
_briefed = {}


def briefing_analysis(writer, analyses):
    """The abraham analysis that briefed this writer, or None (the writer is then a worker of its own).
    The brief is the analysis's whole reply, which ends with "[live log: <path>]", that run's own live
    log: a path no other run has. The last such pointer in the prompt (the brief is its last section)
    names the analysis; it must also share the writer's server and workspace."""
    run = writer.get("run")
    if run in _briefed:
        return _briefed[run]
    prompt = str(writer.get("prompt") or "")
    m = _BRIEF.search(prompt)
    ptrs = _LOG_PTR.findall(prompt[m.end():]) if m else []
    found = None
    if ptrs:
        a = next((a for a in analyses if a.get("log") == ptrs[-1]), None)
        if a and owner_pid(a) == owner_pid(writer) and a.get("cwd") == writer.get("cwd"):
            found = a["run"]
    if writer.get("has_start"):
        _briefed[run] = found
    return found


_dispatch = {}


def dispatch_open(rec, table):
    """Is the abraham call that ran this analysis still under way, so its writer may yet start? The server
    takes the tree's write lock before the analysis and lets it go only when the call returns, by closing
    the lock file (it never unlocks explicitly). So: does the owner still have a write-locks/tree-*.lock
    open whose payload names abraham, this workspace, and a time no later than the analysis began? A
    later abraham call on the same tree takes it afresh. None when that cannot be read (no lsof)."""
    run = rec.get("run")
    hit = _dispatch.get(run)
    if hit is False:
        return False  # a finished call never takes its lock again (a later call's lock is newer)
    if hit and time.time() - hit[1] < 3:
        return hit[0]
    pid = owner_pid(rec)
    if not pid or not owner_alive(rec, table):
        _dispatch[run] = False
        return False
    found = _tree_locks(pid)
    if found is None:  # the inspection failed: unknown, re-asked in a moment, never "over"
        _dispatch[run] = (None, time.time())
        return None
    held, unsure = False, False
    for payload in found:
        if not payload:
            unsure = True  # the server truncates before it rewrites a held lock's payload
            continue
        m = re.match(r"abraham pid=(\d+) .*?\bcwd=(.*?) t=(\d+)", payload)
        if (m and int(m.group(1)) == pid and m.group(2) == rec.get("cwd")
                and int(m.group(3)) <= float(rec.get("first_ts") or 0) + 1):
            held = True
    result = True if held else None if unsure else False
    _dispatch[run] = False if result is False else (result, time.time())
    return result


def _tree_locks(pid):
    """The payloads of the write-locks/tree-*.lock files process `pid` has open ("" for one that could not
    be read, or was empty mid-rewrite), or None when lsof could not list its files. lsof exits 1 for its
    errors too; a live process always has some file open, so 1 is a failure here, not "nothing"."""
    try:
        out = subprocess.run(["lsof", "-a", "-p", str(pid), "-Fn"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    payloads = []
    for line in out.stdout.splitlines():
        if line.startswith("n") and "/write-locks/tree-" in line and line.endswith(".lock"):
            text = ""
            for _ in range(3):
                try:
                    text = Path(line[1:]).read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    text = ""
                if text:
                    break
                time.sleep(0.05)
            payloads.append(text)
    return payloads


def workers(runs, table, since=0.0):
    """Runs grouped into workers: one Codex conversation each. A thread continued over several runs
    (review sessions, resumes) is one worker; abraham's writer joins the analysis that briefed it."""
    groups, order, of_run, by_thread, analyses = {}, [], {}, {}, []
    for rec in runs.values():
        if not rec.get("has_start"):
            continue
        rid, tid, key = rec["run"], str(rec.get("thread_id") or ""), None
        if tid and tid in by_thread:
            key = by_thread[tid]
        elif rec.get("parent_run") in of_run:
            key = of_run[rec["parent_run"]]
        elif rec.get("tool") == "abraham" and rec.get("write"):
            key = of_run.get(briefing_analysis(rec, analyses))
        if key is None or key not in groups:
            key = rid
            groups[key] = []
            order.append(key)
        groups[key].append(rec)
        of_run[rid] = key
        if tid:
            by_thread.setdefault(tid, key)
        if rec.get("tool") == "abraham" and not rec.get("write"):
            analyses.append(rec)
    overrides = role_overrides()
    out = []
    for key in order:
        recs = groups[key]
        statuses = [run_status(r, table) for r in recs]
        live = [i for i, s in enumerate(statuses) if s in LIVE]
        i = live[-1] if live else len(recs) - 1
        cur, st = recs[i], statuses[i]
        dispatch = ""
        if not live and cur.get("tool") == "abraham" and not cur.get("write") and not cur.get("parent_run") and st == "ok":
            d = dispatch_open(cur, table)
            dispatch = "open" if d else "unknown" if d is None else "closed"
            if d or (d is None and time.time() - float(cur.get("last_ts") or 0) < HANDOFF_S):
                st, live = "HANDOFF", [i]  # the analysis is done and its call is starting the writer
        last_ts = max(float(r.get("last_ts") or 0) for r in recs)
        if not live and last_ts < since:
            continue
        tool = "abraham" if any(r.get("tool") == "abraham" for r in recs) else (recs[0].get("tool") or "codex_query")
        writes = any(r.get("write") for r in recs)
        title = next((t for t in (title_of(r) for r in reversed(recs)) if t), "")
        base = "abraham-write" if tool == "abraham" and writes else tool if tool in ROLES else "codex_query"
        kind, source = base, "tool"
        tagged = next((t for t in (tagged_role(r) for r in reversed(recs)) if t), None)
        if tagged:
            kind, source = tagged, "task"
        picked = overrides.get(key)
        if picked in ROLES:
            kind, source = picked, "office"
        out.append({
            "id": key, "name": "", "tool": tool,
            "role": ROLES[kind], "kind": kind, "base_kind": base, "role_source": source,
            "title": title, "status": st, "live": bool(live), "run": cur["run"],
            "cancel_stuck": st == "CANCELLING" and cancel_stuck(cur, table), "dispatch": dispatch,
            "runs": [{"run": r["run"], "status": s, "write": bool(r.get("write")), "tool": r.get("tool") or ""}
                     for r, s in zip(recs, statuses)],
            "phase": ("writer" if cur.get("write") else "analysis") if tool == "abraham" else "",
            "write": bool(cur.get("write")), "infra": bool(cur.get("infra")),
            "web": bool(cur.get("web_search", True)), "model": cur.get("model") or "",
            "reasoning": cur.get("reasoning") or "", "cwd": cur.get("cwd") or "",
            "dispatch_cwd": cur.get("dispatch_cwd") or "", "thread": cur.get("thread_id") or "",
            "first_ts": recs[0].get("first_ts"), "run_ts": cur.get("first_ts"), "last_ts": last_ts,
            "attempts": cur.get("attempts") or 0,
            "activity": activity(cur) if live else "",
            "error": str(cur.get("error") or "")[:400],
        })
    out += native_workers(since, overrides)
    names = names_for([w["id"] for w in out])
    for w in out:
        w["name"] = names[w["id"]]
    return out


def group_of(run, runs, table):
    for w in workers(runs, table):
        if any(r["run"] == run for r in w["runs"]):
            return w
    return None


# ---------------------------------------------------------------- spools

def spool_dir(rec):
    return Path(rec["stdout"]).parent if rec.get("stdout") else None


def attempts_of(rec):
    d = spool_dir(rec)
    out = []
    if d and d.is_dir():
        for p in d.glob("attempt*.stdout.jsonl"):
            m = re.match(r"attempt(\d+)\.stdout\.jsonl$", p.name)
            if m:
                try:
                    out.append({"n": int(m.group(1)), "size": p.stat().st_size})
                except OSError:
                    pass
    return sorted(out, key=lambda a: a["n"])


def clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def clip_mid(s, n):
    s = str(s or "")
    if len(s) <= n:
        return s
    head = int(n * 0.35)
    return s[:head] + "\n\n… %d characters not shown …\n\n" % (len(s) - n) + s[len(s) - (n - head):]


_WRAP = re.compile(r"^/bin/(?:ba|z)?sh -lc (.*)$", re.S)


def unwrap(cmd):
    """`/bin/zsh -lc 'git status'` → `git status` (the quoting codex adds, undone for reading)."""
    m = _WRAP.match(str(cmd or ""))
    if not m:
        return str(cmd or "")
    inner = m.group(1)
    if len(inner) >= 2 and inner[0] == inner[-1] == "'":
        return inner[1:-1].replace("'\\''", "'")
    if len(inner) >= 2 and inner[0] == inner[-1] == '"':
        return re.sub(r'\\(["\\$`])', r"\1", inner[1:-1])
    return inner


def mcp_text(result):
    if not isinstance(result, dict):
        return "" if result is None else json.dumps(result, ensure_ascii=False)
    parts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict)]
    return "\n".join(p for p in parts if p) or json.dumps(result, ensure_ascii=False)


def digest(line, o):
    """One spool line (a codex --json event) as a compact chat event; `o` is its byte offset."""
    try:
        e = json.loads(line)
    except ValueError:
        return {"o": o, "k": "raw", "text": clip(line.decode("utf-8", "replace"), 600)}
    t = e.get("type", "")
    if t.startswith("item."):
        it = e.get("item") or {}
        typ = it.get("type")
        base = {"o": o, "id": str(it.get("id") or o), "ev": t[5:], "st": it.get("status")}
        if typ == "agent_message":
            return dict(base, k="msg", text=it.get("text") or "")
        if typ == "reasoning":
            return dict(base, k="think", text=it.get("text") or "")
        if typ == "command_execution":
            out = it.get("aggregated_output") or ""
            return dict(base, k="cmd", cmd=unwrap(it.get("command")), out=clip_mid(out, OUT_CLIP),
                        out_len=len(out), exit=it.get("exit_code"))
        if typ == "web_search":
            act = it.get("action")
            act = act if isinstance(act, dict) else {}
            q = it.get("query") or act.get("query") or "; ".join(act.get("queries") or [])
            res = [{"title": r.get("title") or "", "url": r.get("url") or "", "domain": r.get("domain") or ""}
                   for r in (it.get("results") or []) if isinstance(r, dict)]
            return dict(base, k="web", query=q, action=act.get("type") or "", url=act.get("url") or "",
                        pattern=act.get("pattern") or "", results=res[:20])
        if typ == "file_change":
            return dict(base, k="file", changes=[{"path": c.get("path"), "kind": c.get("kind")}
                                                 for c in it.get("changes") or [] if isinstance(c, dict)])
        if typ == "mcp_tool_call":
            return dict(base, k="mcp", server=it.get("server") or "", tool=it.get("tool") or "",
                        args=clip(json.dumps(it.get("arguments"), ensure_ascii=False), 2000),
                        result=clip_mid(mcp_text(it.get("result")), OUT_CLIP),
                        error=clip(json.dumps(it["error"], ensure_ascii=False), 2000) if it.get("error") else "")
        if typ == "todo_list":
            return dict(base, k="todo", items=it.get("items") or [])
        if typ == "error":
            return dict(base, k="warn", text=it.get("message") or "")
        return dict(base, k="raw", type=typ or "", text=clip(json.dumps(it, ensure_ascii=False), 800))
    if t == "turn.completed":
        return {"o": o, "k": "turn", "usage": e.get("usage") or {}}
    if t == "turn.started":
        return {"o": o, "k": "turn_start"}
    if t == "turn.failed":
        return {"o": o, "k": "err", "text": (e.get("error") or {}).get("message", "")}
    if t == "error":
        return {"o": o, "k": "err", "text": e.get("message") or ""}
    if t == "thread.started":
        return {"o": o, "k": "thread", "id": e.get("thread_id") or ""}
    return {"o": o, "k": "raw", "type": t, "text": clip(line.decode("utf-8", "replace"), 600)}


def _line_end(fh, pos, size):
    """The offset just past the next newline at or after `pos`, or None; scans without keeping the bytes."""
    fh.seek(pos)
    while pos < size:
        chunk = fh.read(min(CHUNK, size - pos))
        if not chunk:
            break
        i = chunk.find(b"\n")
        if i >= 0:
            return pos + i + 1
        pos += len(chunk)
    return None


def read_events(path, offset, ended=False, digest=digest):
    """Digested events from `offset`, at most about CHUNK bytes (always whole lines; a single line
    longer than CHUNK is read whole). A last line with no newline is still being written, unless the
    run has `ended`: then it never will be, and it becomes one notice so the page can catch up.
    `digest` may skip a line by returning None. Returns (events, new offset, file size)."""
    size = path.stat().st_size
    if offset >= size:
        return [], offset, size
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read(min(CHUNK, size - offset))
        if b"\n" not in data:
            stop = _line_end(fh, offset + len(data), size)
            if stop is None:
                if not ended:
                    return [], offset, size
                return ([{"o": offset, "k": "warn", "text": f"The spool ends in an unfinished line of {size - offset:,} "
                          "bytes (the run ended while codex was writing it); it is not shown."}], size, size)
            fh.seek(offset)
            data = fh.read(stop - offset)
    end = data.rfind(b"\n") + 1
    events, pos = [], offset
    for line in data[:end].split(b"\n")[:-1]:
        ev = digest(line, pos) if line.strip() else None
        if ev is not None:
            events.append(ev)
        pos += len(line) + 1
    return events, offset + end, size


def read_line(path, offset):
    with open(path, "rb") as fh:
        fh.seek(offset)
        buf = b""
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            i = chunk.find(b"\n")
            if i >= 0:
                buf += chunk[:i]
                break
            buf += chunk
    return json.loads(buf)


# ---------------------------------------------------------------- native runs
# abraham on the writer coordinator (2.3.0) runs as a Codex app-server thread, not a `codex exec` run,
# so it is not in the journal. The coordinator lists those runs (run/list, run/status, run/result:
# read-only, over its Python 3.9 client), and each thread's rollout file holds the whole conversation,
# read here as a spool is.

NATIVE_STATUS = {"completed": "ok", "failed": "error", "interrupted": "interrupted", "blocked": "error",
                 "refused": "error", "cancelled": "cancelled", "admitting": "QUEUED", "cancelling": "CANCELLING"}
NATIVE_TERMINAL = ("completed", "failed", "interrupted", "blocked", "refused", "cancelled")  # coordinator/writer.py
NATIVE_TIMEOUT_S = 2.0   # each call answers from the coordinator's memory
NATIVE_LIST_S = 1.5      # the page polls every 2 s; one listing serves the requests in between
_native = {"listing": [], "records": {}, "answers": {}, "at": -NATIVE_LIST_S, "error": "", "present": False}
_native_lock = threading.Lock()


def coordinator_client():
    """The writer coordinator's client (../coordinator, loaded by path under a private package name), or
    None when its state directory does not exist: the coordinator never ran on this machine."""
    state = os.environ.get("SOFTWARE_WORKFLOWS_STATE") or os.path.expanduser("~/.local/state/software-workflows")
    if not os.path.isdir(state):
        return None
    import importlib
    import importlib.util
    name = "software_workflows_coordinator"
    if name not in sys.modules:
        root = ROOT / "coordinator"
        spec = importlib.util.spec_from_file_location(name, str(root / "__init__.py"),
                                                      submodule_search_locations=[str(root)])
        if spec is None or spec.loader is None:
            raise ImportError(str(root))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return importlib.import_module(name + ".client").Client(state=state, timeout=NATIVE_TIMEOUT_S)


def native_runs(fresh=False):
    """The coordinator's abraham runs on this machine: (listing, records by run). A record is read again
    whenever its run's state or revision changes, or its rollout is not known yet. While the coordinator does not
    answer, the last listing stands and `_native["error"]` says why."""
    with _native_lock:
        if not fresh and time.monotonic() - _native["at"] < NATIVE_LIST_S:
            return _native["listing"], _native["records"]
        _native["at"] = time.monotonic()
        try:
            client = coordinator_client()
            if client is None:
                _native.update(listing=[], error="", present=False)
                return [], _native["records"]
            _native["present"] = True
            listing = client.call("run/list", {"cwd": "/", "limit": 100})["data"]
            for s in listing:
                rec = _native["records"].get(s["run"])
                # a run continued between two polls reads the same state twice: its revision moved
                if (rec is None or not rec.get("rollout") or rec.get("state") != s.get("state")
                        or ("revision" in s and rec.get("_revision") != s["revision"])):
                    _native["records"][s["run"]] = client.call("run/status", {"run": s["run"]})
            _native.update(listing=listing, error="")
        except (FileNotFoundError, ConnectionRefusedError):
            _native["error"] = "it is not running"
        except Exception as e:  # the coordinator stopped, or its client unreadable: shown, never a crash
            _native["error"] = f"{getattr(e, 'code', '') or type(e).__name__}: {e}"[:200]
        return _native["listing"], _native["records"]


def native_status(s):
    if s.get("state") in NATIVE_TERMINAL:
        return NATIVE_STATUS[s["state"]]
    if _native["error"]:
        return "UNREACHABLE"  # its coordinator does not answer: neither live nor ended, as far as anyone here knows
    return NATIVE_STATUS.get(s.get("state"), "RUNNING")


def native_rollout(rec):
    p = str((rec or {}).get("rollout") or "")
    p = p[len("file://"):] if p.startswith("file://") else p
    return Path(p) if p and os.path.isfile(p) else None


def native_prompt(rec):
    wf = (rec or {}).get("workflow") or {}
    return str(wf.get("analysis") or (rec or {}).get("task") or "")


def native_activity(path):
    """The thread's latest step, from the end of its rollout."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 65536))
            lines = fh.read().split(b"\n")[1:]
    except OSError:
        return ""
    for line in reversed(lines):
        ev = digest_rollout(line, 0) if line.strip() else None
        if not ev:
            continue
        if ev["k"] == "think" and ev["text"]:
            return ("reasoning: " + ev["text"].split("\n")[0].strip("* ")).strip()[:200]
        if ev["k"] == "cmd":
            return ("$ " + ev["cmd"])[:200]
        if ev["k"] == "file":
            return "edited " + ", ".join(os.path.basename(str(c["path"])) for c in ev["changes"])[:190]
        if ev["k"] in ("msg", "mcp", "web"):
            return {"msg": "wrote a message", "mcp": f"called {ev.get('tool', '')}", "web": f"searched {ev.get('query', '')}"}[ev["k"]][:200]
    return ""


def _texts_of(content):
    return "".join(str(c.get("text") or "") for c in content or [] if isinstance(c, dict))


def digest_rollout(line, o):
    """One rollout line as the chat events `digest` makes of a spool line, or None for lines that carry
    nothing to show (the model's own copies of items, settings, world state)."""
    try:
        e = json.loads(line)
    except ValueError:
        return None
    if e.get("type") != "event_msg":
        return None
    p = e.get("payload") or {}
    t = p.get("type")
    if t == "task_started":
        return {"o": o, "k": "turn_start"}
    if t == "task_complete":
        return {"o": o, "k": "turn", "usage": None}
    if t == "token_count":
        return {"o": o, "k": "tokens", "total": ((p.get("info") or {}).get("total_token_usage") or {})}
    if t == "error":
        return {"o": o, "k": "err", "text": str(p.get("message") or "")}
    if t != "item_completed":
        return None
    it = p.get("item") or {}
    typ = it.get("type")
    base = {"o": o, "id": str(it.get("id") or o), "ev": "completed", "st": "completed"}
    if typ == "AgentMessage":
        return dict(base, k="msg", text=_texts_of(it.get("content")))
    if typ == "UserMessage":
        return dict(base, k="user", text=clip(_texts_of(it.get("content")), 20000))
    if typ == "Reasoning":
        return dict(base, k="think", text="\n".join(str(s) for s in it.get("summary_text") or []))
    if typ == "CommandExecution":
        cmd = it.get("command")
        cmd = shlex.join(cmd) if isinstance(cmd, list) else str(cmd or "")
        out = str(it.get("aggregated_output") or "")
        return dict(base, k="cmd", cmd=unwrap(cmd), out=clip_mid(out, OUT_CLIP), out_len=len(out),
                    exit=it.get("exit_code"))
    if typ == "FileChange":
        changes = it.get("changes") or {}
        return dict(base, k="file", changes=[{"path": path, "kind": (c or {}).get("type")} for path, c in changes.items()]
                    if isinstance(changes, dict) else [])
    if typ == "Extension" and it.get("kind") == "web.search":
        act = it.get("action") if isinstance(it.get("action"), dict) else {}
        res = [{"title": r.get("title") or "", "url": r.get("url") or "", "domain": r.get("domain") or ""}
               for r in it.get("results") or [] if isinstance(r, dict)]
        return dict(base, k="web", query=it.get("query") or act.get("query") or "", action=act.get("type") or "",
                    url=act.get("url") or "", pattern=act.get("pattern") or "", results=res[:20])
    if typ in ("McpToolCall", "DynamicToolCall"):
        result = it.get("result") if typ == "McpToolCall" else {"content": it.get("content_items")}
        return dict(base, k="mcp", server=it.get("server") or "coordinator", tool=it.get("tool") or "",
                    args=clip(json.dumps(it.get("arguments"), ensure_ascii=False), 2000),
                    result=clip_mid(mcp_text(result), OUT_CLIP),
                    error=clip(json.dumps(it["error"], ensure_ascii=False), 2000) if it.get("error")
                    else ("" if it.get("success", True) is not False else "the tool reported failure"))
    if typ == "ContextCompaction":
        return dict(base, k="warn", text="Context compacted")
    if typ == "ImageView":
        return dict(base, k="raw", type="viewed image", text=str(it.get("path") or "").replace("file://", ""))
    return dict(base, k="raw", type=str(typ or ""), text=clip(json.dumps(it, ensure_ascii=False), 800))


def native_workers(since, overrides):
    """The coordinator's abraham runs as workers: one thread each, both phases in it."""
    listing, records = native_runs()
    out = []
    for s in listing:
        rec = records.get(s["run"]) or {}
        st = native_status(s)
        live = st in LIVE
        path = native_rollout(rec)
        created = float(s.get("created") or 0)
        try:
            last_ts = path.stat().st_mtime if path else created
        except OSError:
            last_ts = created
        if not live and last_ts < since:
            continue
        wf = rec.get("workflow") or {}
        probe = {"run": s["run"], "prompt": native_prompt(rec), "has_start": bool(rec)}
        kind, source = "abraham-write", "tool"
        tagged = tagged_role(probe)
        if tagged:
            kind, source = tagged, "task"
        if overrides.get(s["run"]) in ROLES:
            kind, source = overrides[s["run"]], "office"
        root = next((a for a in s.get("actors") or [] if a.get("id") == rec.get("root_actor")), {})
        out.append({
            "id": s["run"], "name": "", "tool": "abraham", "role": ROLES[kind], "kind": kind,
            "base_kind": "abraham-write", "role_source": source, "title": title_of(probe), "status": st,
            "live": live, "run": s["run"], "cancel_stuck": False, "dispatch": "",
            "runs": [{"run": s["run"], "status": st, "write": True, "tool": "abraham"}],
            "phase": "writer" if s.get("phase") == "implementation" else "analysis",
            "write": True, "infra": True, "web": True, "model": wf.get("model") or "",
            "reasoning": wf.get("effort") or "", "cwd": s.get("target_cwd") or "",
            "dispatch_cwd": s.get("dispatch_cwd") or "", "thread": root.get("thread") or "",
            "first_ts": created, "run_ts": created, "last_ts": last_ts, "attempts": 0,
            "activity": native_activity(path) if live and path else "",
            "error": str(rec.get("refusal") or rec.get("settlement_refusal") or "")[:400],
            "native": {"unit": s.get("unit"), "unit_state": s.get("unit_state"),
                       "claims": sorted(c.get("path") for c in s.get("claims") or []),
                       "custody": len(s.get("custody") or []), "unknown": s.get("unknown_count") or 0,
                       "unread": s.get("unread_messages") or 0, "coordinator": _native["error"]},
        })
    return out


def native_detail(run, light=False):
    """/api/run for a native run, in the journal record's shape, or None when the coordinator has no such run."""
    listing, records = native_runs()
    s = next((x for x in listing if x["run"] == run), None)
    rec = records.get(run)
    if s is None or rec is None:
        return None
    st = native_status(s)
    path = native_rollout(rec)
    wf = rec.get("workflow") or {}
    # A continuation keeps the run id and rewrites its record: the answer is cached per record revision.
    cached = (run, rec.get("_revision"), s.get("state"))
    answer = _native["answers"].get(cached, "")
    if not answer and s.get("state") in NATIVE_TERMINAL:
        try:
            parts, cursor = [], 0
            while cursor is not None:
                page = coordinator_client().call("run/result", {"run": run, "cursor": cursor}) or {}
                parts.append(str(page.get("text") or ""))
                cursor = page.get("next_cursor")
            answer = "".join(parts)
            _native["answers"][cached] = answer
        except Exception as e:
            answer = f"[The coordinator did not give this run's result: {getattr(e, 'code', '') or type(e).__name__}]"
    prompt = native_prompt(rec)
    try:
        size, last_ts = (path.stat().st_size, path.stat().st_mtime) if path else (0, s.get("created"))
    except OSError:
        size, last_ts = 0, s.get("created")
    return {"run": run, "tool": "abraham", "write": True, "infra": True, "web_search": True, "native": True,
            "model": wf.get("model") or "", "reasoning": wf.get("effort") or "", "cwd": s.get("target_cwd") or "",
            "dispatch_cwd": s.get("dispatch_cwd") or "", "first_ts": s.get("created"), "last_ts": last_ts,
            "status": st, "title": title_of({"run": run, "prompt": prompt, "has_start": True}),
            "prompt": "" if light else prompt, "task": "" if light else task_of(prompt),
            "spools": [{"n": 0, "size": size}] if path else [], "answer": answer,
            "has_end": s.get("state") in NATIVE_TERMINAL, "phase": s.get("phase"), "unit": s.get("unit"),
            "rollout": str(path or ""), "error": str(rec.get("refusal") or "")[:400]}


# ---------------------------------------------------------------- file references

_files_cache = {}


def list_files(cwd):
    """Paths under a workspace for @-references: git's view (tracked + untracked, ignores applied), or a
    walk for a folder outside git. A walk stops after 4 s so the picker stays responsive; it says so."""
    hit = _files_cache.get(cwd)
    if hit and time.time() - hit[0] < 30:
        return hit[1], hit[2]
    files, partial = None, False
    try:
        out = subprocess.run(["git", "-C", cwd, "ls-files", "-z", "-co", "--exclude-standard"],
                             capture_output=True, timeout=20)
        if out.returncode == 0:
            files = [f for f in out.stdout.decode("utf-8", "replace").split("\0") if f]
    except (OSError, subprocess.SubprocessError):
        pass
    if files is None:
        files, t0 = [], time.time()
        for dirpath, dirnames, filenames in os.walk(cwd):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")
                           and d not in ("node_modules", "__pycache__", "venv", ".venv", "Library")]
            rel = os.path.relpath(dirpath, cwd)
            files += [f if rel == "." else os.path.join(rel, f) for f in filenames]
            if time.time() - t0 > 4:
                partial = True
                break
    _files_cache[cwd] = (time.time(), files, partial)
    return files, partial


def match_files(files, q, n=40):
    q = q.lower().strip()
    scored = []
    for f in files:
        fl = f.lower()
        base = fl.rsplit("/", 1)[-1]
        if not q:
            s = 3
        elif base.startswith(q):
            s = 0
        elif q in base:
            s = 1
        elif q in fl:
            s = 2
        else:
            continue
        scored.append((s, len(f), f))
    scored.sort()
    return [f for _, _, f in scored[:n]]


# ---------------------------------------------------------------- actions (pause / instructions)

def plugin_paths():
    """(plugin root, plugin data dir): the plugin this office belongs to, so an action runs the same
    server version as the page that sent it."""
    if not (ROOT / "run_server.py").is_file():
        raise RuntimeError(f"no run_server.py in {ROOT}: this office is not inside the software-workflows plugin")
    return ROOT, DATA


class NoAnswer(RuntimeError):
    """The plugin's MCP server did not answer within its deadline."""


def call_timeout():
    """Claude Code's per-call timeout for this plugin's server (.mcp.json, milliseconds), in seconds."""
    try:
        ms = (read_json(ROOT / ".mcp.json", {}) or {})["mcpServers"]["codex-oracle"]["timeout"]
        return float(ms) / 1000.0 if float(ms) > 0 else CALL_DEFAULT_S
    except (KeyError, TypeError, ValueError):
        return CALL_DEFAULT_S


class McpClient:
    """The plugin's own MCP server over stdio, started for one action in the run's workspace."""

    def __init__(self, cwd, log):
        root, data = plugin_paths()
        py = os.environ.get("CODEX_ORACLE_PYTHON") or shutil.which("python3") or sys.executable
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(root), CLAUDE_PLUGIN_DATA=str(data), CLAUDE_CWD=cwd,
                   PYTHONDONTWRITEBYTECODE="1")
        self.p = subprocess.Popen([py, str(root / "run_server.py")], cwd=cwd, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=log, env=env)
        self.q = queue.Queue()
        self.n = 0
        self.cwd = cwd
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {"roots": {}},
                                        "clientInfo": {"name": "codex-office", "version": "1"}}, INIT_S)
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.p.kill()  # no caller holds this client to close it
            raise

    def _read(self):
        for line in self.p.stdout:
            try:
                self.q.put(json.loads(line))
            except ValueError:
                continue
        self.q.put(None)

    def send(self, msg, deadline=None):
        """Write one message. The pipe blocks once the server stops reading and its buffer is full, so the
        write runs where the deadline (time.monotonic) can still end the wait."""
        data, failed = (json.dumps(msg) + "\n").encode(), []

        def write():
            try:
                self.p.stdin.write(data)
                self.p.stdin.flush()
            except (OSError, ValueError) as e:
                failed.append(e)
        t = threading.Thread(target=write, daemon=True)
        t.start()
        t.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
        if t.is_alive():
            # A timed join does not stop the write: end the server, so the request can never arrive later.
            self.abort()
            t.join(10)
            raise NoAnswer(f"the plugin's MCP server stopped reading ({len(data):,} bytes not taken in time)")
        if failed:
            raise failed[0]

    def request(self, method, params, timeout=None):
        """The answer to one request. `timeout` seconds at most; None waits as long as the server lives
        (its exit ends the wait: the reader queues None at end of output)."""
        self.n += 1
        want = self.n
        deadline = None if timeout is None else time.monotonic() + timeout
        self.send({"jsonrpc": "2.0", "id": want, "method": method, "params": params}, deadline)
        while True:
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                raise NoAnswer(f"the plugin's MCP server did not answer {method} within {timeout:.0f} s")
            try:
                m = self.q.get(timeout=left)
            except queue.Empty:
                continue
            if m is None:
                raise RuntimeError(f"the plugin's MCP server exited ({self.p.poll()}) before answering {method}")
            if "method" in m and "id" in m:  # the server asking us something
                result = {"roots": [{"uri": Path(self.cwd).as_uri(), "name": "workspace"}]} \
                    if m["method"] == "roots/list" else {}
                self.send({"jsonrpc": "2.0", "id": m["id"], "result": result}, deadline)
                continue
            if m.get("id") == want:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error'].get('message')}")
                return m.get("result") or {}

    def tool_params(self, tool):
        """The argument names a tool declares (its input schema), or an empty set."""
        for t in self.request("tools/list", {}, LIST_S).get("tools") or []:
            if t.get("name") == tool:
                return set(((t.get("inputSchema") or {}).get("properties") or {}).keys())
        return set()

    def call(self, tool, args, timeout=None):
        """(reply text, whether the server flagged it an error). A refusal is ordinary text, not an
        error, so callers confirm the outcome in the journal."""
        res = self.request("tools/call", {"name": tool, "arguments": args}, timeout)
        text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
        return text.strip(), bool(res.get("isError"))

    def abort(self):
        """End the server now: nothing sent so far may still be acted on after this returns."""
        try:
            self.p.kill()
            self.p.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def close(self):
        """End of input lets the server exit; one that does not within 20 s is killed. Closing stdin waits
        on a write that is stuck in the pipe, so it runs aside and is not waited for."""
        def end_input():
            try:
                self.p.stdin.close()
            except (OSError, ValueError):
                pass
        threading.Thread(target=end_input, daemon=True).start()
        try:
            self.p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=10)


def compose_nudge(text, images, files, cwd):
    """The continuation's instructions. `images` are listed for Codex's view_image tool only when the
    server cannot attach them (a codex_resume_run without an `images` argument)."""
    parts = [text.strip()] if text.strip() else []
    if images:
        parts.append("Images the lead attached for you — open each one with your view_image tool before you go on:\n"
                     + "\n".join(f"- {p}" for p in images))
    if files:
        paths = [f if os.path.isabs(f) else os.path.join(cwd, f) for f in files]
        parts.append("Files the lead points you to — read them:\n" + "\n".join(f"- {p}" for p in paths))
    return "\n\n".join(parts)


def spawn_detached(argv, log_path):
    """Start argv with launchd as its parent: its own session, and no longer anyone's descendant here,
    so neither this server's exit nor a Claude Code restart (which ends a task's tree by parent pid)
    reaches it."""
    middle = ("import os, subprocess, sys\n"
              "p = subprocess.Popen(%r, stdin=subprocess.DEVNULL, stdout=open(%r, 'ab'),\n"
              "                     stderr=subprocess.STDOUT, start_new_session=True)\n"
              "os._exit(0)\n") % (argv, str(log_path))
    r = subprocess.run([sys.executable, "-c", middle], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError((r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1])


def decide(aid, what):
    """Settle once whether an action is delivered or withdrawn: whichever of the action ("deliver", just
    before it acts) and the page ("withdraw") claims <aid>.decision first wins, and both read the winner.
    The claim hard-links a finished file into place, so a reader never sees it half-written."""
    final = ACTIONS / f"{aid}.decision"
    tmp = ACTIONS / f"{aid}.decision.{secrets.token_hex(4)}.tmp"
    tmp.write_text(what)
    try:
        os.link(tmp, final)
        return what
    except FileExistsError:
        return final.read_text().strip()
    finally:
        tmp.unlink(missing_ok=True)


def decision(aid):
    try:
        return (ACTIONS / f"{aid}.decision").read_text().strip()
    except OSError:
        return ""


def continuation_of(runs, run, server_pid, since):
    """The continuation of `run` that this action's own server started, or None. Its run tag carries that
    server's pid (run_server.py execs in place, so the pid is the one this action spawned), and no other
    caller's call runs on that server: another caller's continuation of the same run never counts."""
    found = [r for r in runs.values() if r.get("has_start") and r.get("parent_run") == run
             and owner_pid(r) == server_pid and float(r.get("first_ts") or 0) >= since - 2]
    return max(found, key=lambda r: float(r.get("first_ts") or 0)) if found else None


def handoff_refusal(w, run):
    """Why a continuation must not go to this abraham analysis now, or "". While its call may still start
    the writer, continuing the analysis thread would race it; when that cannot be read, it is not guessed."""
    if not w or w.get("tool") != "abraham" or not w["runs"] or w["runs"][-1]["run"] != run or w["runs"][-1]["write"]:
        return ""
    if w["status"] == "HANDOFF" or w.get("dispatch") == "open":
        return "abraham is starting its writer; use “Send when done”, which waits for the writer"
    if w.get("dispatch") == "unknown":
        return ("cannot tell whether abraham's writer is still to come (lsof could not read its server's "
                "write lock); send this once the writer shows")
    return ""


def missing_attachments(a):
    """Attachments and @-files that are no longer there (an upload removed since a draft kept it)."""
    return [p for p in list(a.get("images") or []) + list(a.get("files") or [])
            if not os.path.exists(p if os.path.isabs(p) else os.path.join(a.get("cwd") or "", p))]


def native_state(run):
    """A native run's status as the coordinator says it now, or "?" when it does not know the run."""
    listing, _ = native_runs(fresh=True)
    s = next((x for x in listing if x["run"] == run), None)
    return native_status(s) if s else "?"


def native_refusal(res):
    """The coordinator's refusal in a coordinator tool's reply, or "": the reply's JSON is either what was
    done (a run summary, a message record) or an error envelope {"code", "message", "details", "kind"}."""
    i = res.find("{")
    try:
        value = json.JSONDecoder().raw_decode(res[i:])[0] if i >= 0 else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        return res.strip()[:600] or "the server's reply carried no result"
    if {"code", "message", "kind"} <= set(value):
        return f"{value['code']}: {value['message']}"[:600]
    return ""


def reply_json(res):
    """The first JSON object in a coordinator tool's reply, or None."""
    i = res.find("{")
    try:
        value = json.JSONDecoder().raw_decode(res[i:])[0] if i >= 0 else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def message_outcome(run, message, progress=None):
    """(how far a message sent to a native run got, why the wait stopped). How far, from its own
    delivery events: entered_context (in the turn), turn_ended, delivery_uncertain, submitted or
    persisted. Why: "event" for one of the first three, "ended" once the run reads stopped twice, a
    poll apart, "unreachable" when the coordinator cannot say twice, "deadline" at the call budget;
    only "ended" proves the turn is over. A running turn takes a steer at its next model request,
    which at max effort can be minutes away (measured: 36 s on a fixture whose answer was held
    50 s), so this waits for the turn, within the run's budget."""
    deadline, cursor, kinds, ended, unread = time.time() + call_timeout(), 0, set(), 0, 0
    while True:
        try:
            while True:
                page = coordinator_client().call("run/events", {"run": run, "cursor": cursor, "limit": 400}) or {}
                kinds |= {e.get("kind") for e in page.get("data") or [] if e.get("subject") == message}
                cursor = page.get("cursor", cursor)
                if not page.get("more"):
                    break
        except Exception:
            pass
        reached = next((k.split("/", 1)[1] for k in ("message/entered_context", "message/turn_ended", "message/delivery_uncertain",
                                                     "message/submitted") if k in kinds), "persisted")
        if progress:
            progress(reached)
        if reached in ("entered_context", "turn_ended", "delivery_uncertain"):
            return reached, "event"
        if time.time() >= deadline:
            return reached, "deadline"
        # the turn's end is read twice, a poll apart, so an echo recorded as it ended is not missed
        st = native_state(run)
        unread = unread + 1 if st in ("UNREACHABLE", "?") else 0
        ended = ended + 1 if st not in LIVE and not unread else 0
        if unread >= 2:
            return reached, "unreachable"
        if ended >= 2:
            return reached, "ended"
        time.sleep(1)


def run_action(aid):
    path = ACTIONS / f"{aid}.json"
    a = read_json(path)
    if not a:
        sys.exit(f"no action {aid}")

    def save(**kw):
        a.update(kw)
        write_json(path, a)

    save(status="running", pid=os.getpid(), pid_start=_proc_start(os.getpid()), started=time.time())
    journal = Journal()
    client = None
    try:
        run = a["run"]
        nat = bool(a.get("native"))  # a run on the writer coordinator: its state is the coordinator's, not the journal's
        if a["kind"] == "after":
            save(status="waiting")
            quiet = None
            while True:
                if decision(aid) == "withdraw":
                    save(status="withdrawn", ended=time.time())
                    return
                w = group_of(run, journal.refresh(), processes())
                if w is None:
                    raise RuntimeError(f"run {run} is no longer in the journal")
                if w["cancel_stuck"]:
                    raise RuntimeError(f"run {w['run']} has a cancel that never finished; pause it again "
                                       "(that retries the cancel), then send the instructions")
                if w["live"]:
                    quiet = None
                    time.sleep(3)
                    continue
                last = w["runs"][-1]["run"]
                why = handoff_refusal(w, last)
                if why:
                    raise RuntimeError(why)
                # Finished twice running with the same last run, the journal re-read after the lock was: a
                # writer that started and ended between one read and the next shows in the second.
                if quiet == last:
                    run = last
                    break
                quiet = last
                time.sleep(1)
            save(run=run)
        if a["kind"] == "steer":
            if decide(aid, "deliver") != "deliver":
                save(status="withdrawn", ended=time.time())
                return
            with open(ACTIONS / f"{aid}.server.log", "ab") as log:
                client = McpClient(a["cwd"], log)
                save(server_pid=client.p.pid, status="sending")
                # A message to a running native thread enters its active turn (turn/steer): nothing stops.
                # It carries text only, so images go by path, for Codex's view_image tool.
                nudge = compose_nudge(a.get("text") or "", a.get("images") or [], a.get("files") or [], a["cwd"])
                save(images_via="view_image" if a.get("images") else "", nudge=nudge)
                res, err = client.call("coordinator_send", {"recipient": run, "text": nudge, "wake": "none",
                                                            "cwd": a.get("target_cwd") or a["cwd"]}, CANCEL_S)
                why = res if err else native_refusal(res)
                if why:
                    save(status="failed", result=res, ended=time.time(), error=why[:600])
                    return
                # Sending only persists the message: report where it got, from its delivery events.
                save(status="delivering", result=res)
                reached, why = message_outcome(run, (reply_json(res) or {}).get("id", ""),
                                               lambda r: a.get("delivery") == r or save(delivery=r))
                if reached == "entered_context":
                    save(status="done", delivery=reached, run_status=native_state(run), ended=time.time())
                elif why in ("unreachable", "deadline"):
                    # the turn's end is not known: never advise sending again what may still enter it
                    save(status="unconfirmed", delivery=reached, run_status=native_state(run), ended=time.time(),
                         error=("the coordinator stopped answering" if why == "unreachable" else
                                "no outcome within the call's budget") + f" while the note read {reached}; it may still enter the turn")
                else:
                    save(status="unconfirmed" if reached == "delivery_uncertain" else "held", delivery=reached,
                         run_status=native_state(run), ended=time.time(),
                         error={"persisted": "the turn ended before it was sent; it waits for the writer's next turn",
                                "submitted": "Codex had it, but the turn ended before taking it; send it again to deliver it",
                                "turn_ended": "the turn ended (or was paused) before taking it; send it again to deliver it",
                                "delivery_uncertain": "the coordinator could not tell whether it reached the turn"}[reached])
                return
        if a["kind"] in ("resume", "interrupt"):
            why = handoff_refusal(group_of(run, journal.refresh(), processes()), run)
            if why:
                raise RuntimeError(why)
        gone = missing_attachments(a) if a["kind"] != "cancel" else []
        if gone:
            raise RuntimeError("no longer there: " + ", ".join(gone) + "; remove them and attach again")
        if decide(aid, "deliver") != "deliver":
            save(status="withdrawn", ended=time.time())
            return
        with open(ACTIONS / f"{aid}.server.log", "ab") as log:
            client = McpClient(a["cwd"], log)
            save(server_pid=client.p.pid)
            if a["kind"] in ("cancel", "interrupt"):
                save(status="stopping")
                res, err = client.call("codex_cancel_run", {"run": run}, CANCEL_S)
                save(cancel_result=res)
                why = res if err else native_refusal(res) if nat else ""
                if why:
                    raise RuntimeError(f"codex_cancel_run failed: {why}")
                deadline, st, stopped = time.time() + STOP_WAIT_S, "?", False
                while True:
                    st = native_state(run) if nat else run_status(journal.refresh().get(run, {}), processes())
                    # an unreachable coordinator, or a run it no longer lists, says nothing about the writer
                    stopped = st not in LIVE and not (nat and st in ("UNREACHABLE", "?"))
                    if stopped or time.time() >= deadline:
                        break
                    time.sleep(1)
                if not stopped:
                    raise RuntimeError((f"run {run} still reads {st}" if st in LIVE else
                                        f"run {run}'s stop could not be confirmed (it reads {st})")
                                       + f" {STOP_WAIT_S:.0f} s after codex_cancel_run"
                                       + ("; nothing was sent" if a["kind"] == "interrupt" else "") + f". The server said: {res}")
                if a["kind"] == "cancel":
                    save(status="done", result=res, run_status=st, ended=time.time())
                    return
            save(status="sending")
            # Attach images when this server's codex_resume_run takes them (2.2.0: `-i` on the resumed
            # turn); otherwise name their paths for Codex's view_image tool. Asked of the server itself,
            # never assumed from the version.
            native = bool(a.get("images")) and "images" in client.tool_params("codex_resume_run")
            nudge = compose_nudge(a.get("text") or "", [] if native else a.get("images") or [], a.get("files") or [], a["cwd"])
            save(images_via=("attached" if native else "view_image") if a.get("images") else "", nudge=nudge)
            args = {"run": run, "nudge": nudge}
            if native:
                args["images"] = list(a["images"])
            if not a.get("write"):
                for k in ("infra", "web_search"):
                    if a.get(k) is not None:
                        args[k] = bool(a[k])
            if nat:
                # The coordinator submits the next turn on the same thread and answers at once: the run's
                # own state, read back from the coordinator, is the proof.
                try:
                    res, err = client.call("codex_resume_run", args, call_timeout())
                except NoAnswer as e:
                    client.abort()
                    st = native_state(run)
                    save(status="failed", ended=time.time(), run_status=st, error=f"{e}; the run reads {st}")
                    return
                why = res if err else native_refusal(res)
                st = native_state(run)
                if why:
                    save(status="failed", result=res, run_status=st, ended=time.time(), error=why[:600])
                    return
                save(status="continuing" if st in LIVE else "done" if st == "ok" else "ended", result=res,
                     continuation=run, run_status=st, ended=time.time())
                return
            before = run_status(journal.refresh().get(run, {}), processes())
            sent, budget = time.time(), call_timeout()
            try:
                res, err = client.call("codex_resume_run", args, budget)
                after = journal.refresh().get(run, {})
                if (not err and before in ("DETACHED", "DETACHED-ENDED") and after.get("has_end")
                        and str(after.get("status")) != "cancelled"
                        and continuation_of(journal.refresh(), run, client.p.pid, sent) is None):
                    # The server collected what the detached run left (its answer, or its partial output when
                    # it failed) and did not deliver the note; with the run ended, send it again. A cancel
                    # reached meanwhile is someone's decision, and stands.
                    save(collect_result=res)
                    sent = time.time()
                    res, err = client.call("codex_resume_run", args, budget)
            except NoAnswer as e:
                client.abort()  # settle the transport before reading the outcome: no late delivery after it
                cont = continuation_of(journal.refresh(), run, client.p.pid, sent)
                save(status="failed", ended=time.time(), continuation=cont["run"] if cont else "",
                     run_status=run_status(cont, processes()) if cont else "",
                     error=f"{e}" + (f"; its continuation {cont['run']} reads {run_status(cont, processes())}"
                                     if cont else "; no continuation was started"))
                return
            cont = continuation_of(journal.refresh(), run, client.p.pid, sent)
            if err or cont is None:
                # Refusals ("[resume refused: …]") come back as plain text; the journal is the proof.
                save(status="failed", result=res, ended=time.time(),
                     error=res if err else f"no continuation of {run} was started. The server said: {res[:600]}")
                return
            st = run_status(cont, processes())
            save(status="done" if st == "ok" else "continuing" if st in LIVE else "ended", result=res,
                 continuation=cont["run"], run_status=st, ended=time.time())
    except Exception as e:  # recorded for the page; the action file is the only channel back
        save(status="failed", error=f"{type(e).__name__}: {e}", ended=time.time())
    finally:
        if client:
            client.close()


def new_action(body, runs):
    kind = body.get("kind")
    if kind not in ("cancel", "resume", "interrupt", "after", "steer"):
        raise ValueError(f"unknown action {kind!r}")
    rec = runs.get(str(body.get("run") or ""))
    native = False
    if not rec:
        listing, _ = native_runs()
        s = next((x for x in listing if x["run"] == str(body.get("run") or "")), None)
        if s:
            native = True
            rec = {"run": s["run"], "has_start": True, "write": True, "cwd": s.get("target_cwd"),
                   "dispatch_cwd": s.get("dispatch_cwd")}
    if not rec or not rec.get("has_start"):
        raise ValueError("unknown run")
    if kind == "steer" and not (native and native_state(rec["run"]) == "RUNNING"):
        raise ValueError("“Send now” reaches a running writer on the coordinator; use Continue or Send when done")
    cwd = rec.get("dispatch_cwd") or rec.get("cwd") or str(Path.home())
    if not os.path.isdir(cwd):
        raise ValueError(f"the run's workspace {cwd} no longer exists")
    images = [str(p) for p in body.get("images") or []]
    uploads = str(UPLOADS.resolve()) + os.sep
    if any(not str(Path(p).resolve()).startswith(uploads) for p in images):
        raise ValueError("attachments must be uploaded through the office")
    files = [str(f) for f in body.get("files") or []]
    text = str(body.get("text") or "")
    if kind != "cancel" and not (text.strip() or images or files):
        raise ValueError("write the instructions first")
    if kind != "cancel":
        gone = missing_attachments({"images": images, "files": files, "cwd": cwd})
        if gone:
            raise ValueError("no longer there: " + ", ".join(gone) + " — remove them and attach again")
    if kind in ("resume", "interrupt"):
        why = handoff_refusal(group_of(rec["run"], runs, processes()), rec["run"])
        if why:
            raise ValueError(why)
    aid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    action = {"id": aid, "kind": kind, "run": rec["run"], "target": rec["run"], "worker": body.get("worker") or "",
              "cwd": cwd, "write": bool(rec.get("write")), "text": text, "images": images, "files": files,
              "infra": body.get("infra"), "web_search": body.get("web_search"), "data": str(DATA),
              "native": native, "target_cwd": rec.get("cwd") or "", "status": "queued", "created": time.time()}
    ACTIONS.mkdir(parents=True, exist_ok=True)
    write_json(ACTIONS / f"{aid}.json", action)
    spawn_detached([sys.executable, str(Path(__file__).resolve()), "act", aid, "--data", str(DATA)], ACTIONS / f"{aid}.out")
    return action


def list_actions(worker):
    """The worker's actions as their files say, corrected by what they cannot say themselves: a withdraw
    the action has not noticed yet, and an action whose process is gone without recording an outcome."""
    out, table = [], None
    if ACTIONS.is_dir():
        for p in ACTIONS.glob("*.json"):
            a = read_json(p)
            if a and (not worker or a.get("worker") == worker):
                a.pop("nudge", None)
                if a.get("status") in ("queued", "running", "waiting"):
                    if decision(a.get("id")) == "withdraw":
                        a["status"] = "withdrawn"
                if a.get("status") in ("running", "waiting", "stopping", "sending", "delivering") and a.get("pid"):
                    table = table or processes()
                    if not alive(a["pid"], a.get("pid_start"), table):
                        a.update(status="failed", error="the action's process ended without recording an outcome "
                                                        f"(its log: {ACTIONS / (str(a.get('id')) + '.out')})")
                out.append(a)
    return sorted(out, key=lambda a: a.get("created") or 0)


# ---------------------------------------------------------------- HTTP

JOURNAL = Journal()


class Handler(BaseHTTPRequestHandler):
    server_version = "CodexOffice/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _gate(self, q):
        if (self.headers.get("Host") or "").lower() not in self.server.hosts:
            self._json({"error": "foreign Host header"}, 403)
            return False
        token = self.headers.get("X-Office-Token") or (q.get("t") or [""])[0]
        if not secrets.compare_digest(token, TOKEN):
            self._json({"error": "missing or wrong office token — reload the page"}, 403)
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        one = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
        if u.path == "/favicon.ico":  # the page carries its own icon
            return self._send(204, b"", "image/x-icon")
        if u.path in ("/", "/index.html"):
            if (self.headers.get("Host") or "").lower() not in self.server.hosts:
                return self._json({"error": "foreign Host header"}, 403)
            html = (HERE / "office.html").read_text(encoding="utf-8").replace("__OFFICE_TOKEN__", TOKEN)
            csp = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' "
                   "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                   "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            return self._send(200, html.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": csp})
        if not self._gate(q):
            return
        try:
            if u.path == "/api/state":
                since = float(one("since", "0") or 0)
                runs = JOURNAL.refresh()
                ws = workers(runs, processes(), since)
                spaces = sorted(({r.get("dispatch_cwd") or r.get("cwd") or "" for r in runs.values()
                                  if r.get("has_start")} | {s.get("dispatch_cwd") or "" for s in _native["listing"]})
                                - {""})
                coordinator = ("" if not _native["present"] else
                               f"The writer coordinator does not answer ({_native['error']}); its runs show as last seen."
                               if _native["error"] else "")
                return self._json({"now": time.time(), "workers": ws, "workspaces": spaces, "coordinator": coordinator,
                                   "log_dir": str(LOG_DIR), "version": plugin_version(), "roles": ROLES})
            if u.path == "/api/run":
                runs = JOURNAL.refresh()
                rec = runs.get(one("run"))
                if not rec:
                    native = native_detail(one("run"), light=one("light") == "1")
                    return self._json(native) if native else self._json({"error": "unknown run"}, 404)
                answer = ""
                for k in ("result_file", "output_file"):
                    f = rec.get(k)
                    if f and Path(f).is_file():
                        answer = Path(f).read_text(encoding="utf-8", errors="replace")
                        if answer.strip():
                            break
                keep = {k: v for k, v in rec.items() if not k.startswith("has_") and k != "prompt"}
                light = one("light") == "1"  # the page's re-polls of a live run: no prompt
                return self._json(dict(keep, status=run_status(rec, processes()), title=title_of(rec),
                                       prompt="" if light else rec.get("prompt") or "",
                                       task="" if light else task_of(rec.get("prompt")),
                                       spools=attempts_of(rec), answer=answer, has_end=bool(rec.get("has_end"))))
            if u.path in ("/api/events", "/api/item"):
                rec = JOURNAL.refresh().get(one("run"))
                if rec is None and one("run") in native_runs()[1]:
                    run = one("run")
                    p = native_rollout(_native["records"].get(run))
                    if p is None:
                        return self._json({"events": [], "offset": 0, "size": 0})
                    if u.path == "/api/item":
                        return self._json({"item": (read_line(p, int(one("o", "0"))).get("payload") or {}).get("item") or {}})
                    s = next((x for x in _native["listing"] if x["run"] == run), {})
                    events, off, size = read_events(p, int(one("offset", "0")), s.get("state") in NATIVE_TERMINAL,
                                                    digest_rollout)
                    return self._json({"events": events, "offset": off, "size": size})
                d = spool_dir(rec) if rec else None
                if not d:
                    return self._json({"error": "no spool for this run"}, 404)
                n = int(one("attempt", "0"))
                p = d / f"attempt{n}.stdout.jsonl"
                if not p.is_file():
                    return self._json({"events": [], "offset": 0, "size": 0})
                if u.path == "/api/item":
                    return self._json(read_line(p, int(one("o", "0"))))
                # A spool is final once its run has ended or a later attempt has begun.
                final = bool(rec.get("has_end")) or any(a["n"] > n for a in attempts_of(rec))
                events, off, size = read_events(p, int(one("offset", "0")), final)
                return self._json({"events": events, "offset": off, "size": size})
            if u.path == "/api/actions":
                return self._json({"actions": list_actions(one("worker"))})
            if u.path == "/api/files":
                cwd = one("cwd")
                runs = JOURNAL.refresh()
                known = {r.get("cwd") for r in runs.values()} | {r.get("dispatch_cwd") for r in runs.values()}
                known |= {s.get(k) for s in _native["listing"] for k in ("dispatch_cwd", "target_cwd")}
                if cwd not in known or not os.path.isdir(cwd):
                    return self._json({"error": "not a workspace of any run"}, 404)
                files, partial = list_files(cwd)
                return self._json({"files": match_files(files, one("q")), "partial": partial, "count": len(files)})
            if u.path == "/api/upload":
                p = (UPLOADS / Path(one("name")).name).resolve()
                if p.parent != UPLOADS.resolve() or not p.is_file():
                    return self._json({"error": "no such upload"}, 404)
                return self._send(200, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")
            return self._json({"error": "not found"}, 404)
        except (OSError, ValueError) as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._gate(q):
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            if u.path == "/api/upload":
                raw = Path((q.get("name") or ["file"])[0]).name
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", raw)[-80:] or "file"
                UPLOADS.mkdir(parents=True, exist_ok=True)
                dest = UPLOADS / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}-{safe}"
                left = n
                with open(dest, "wb") as fh:
                    while left > 0:
                        chunk = self.rfile.read(min(CHUNK, left))
                        if not chunk:
                            break
                        fh.write(chunk)
                        left -= len(chunk)
                return self._json({"path": str(dest), "name": raw, "stored": dest.name, "size": n - left,
                                   "image": dest.suffix.lower() in IMAGE_EXT})
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/api/action":
                return self._json({"action": new_action(body, JOURNAL.refresh())})
            if u.path == "/api/role":
                set_role(str(body.get("worker") or ""), str(body.get("role") or ""))
                return self._json({"ok": True})
            if u.path == "/api/withdraw":
                aid = re.sub(r"[^0-9a-f-]", "", str(body.get("id") or ""))
                if not aid or not (ACTIONS / f"{aid}.json").is_file():
                    return self._json({"error": "unknown action"}, 404)
                if decide(aid, "withdraw") != "withdraw":
                    return self._json({"error": "too late to withdraw: it is already being delivered"}, 409)
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def _sh(path):
    """A path quoted the way commands/office.md's allowed-tools rule matches it (as plugin_setup.py does)."""
    path = str(path)
    if re.search(r'["$`\n]|\\(?=["$`\\])', path):
        return shlex.quote(path)
    return f'"{path}"'


def _proc_start(pid):
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return "_".join(out.split())


def running():
    """The office recorded in server.json, if that process is still it and still answers."""
    info = read_json(STATE / "server.json")
    if not info or not info.get("pid") or _proc_start(info["pid"]) != info.get("pid_start"):
        return None
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/favicon.ico", timeout=3) as r:
            return info if r.status in (200, 204) else None
    except OSError:
        return None


def _lock(name, wait):
    """An exclusive lock on <state>/<name>, held while the returned file stays open, or None when
    another process still holds it after `wait` seconds. The kernel drops it when its holder exits."""
    fh = open(STATE / name, "a+")
    until = time.time() + wait
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if time.time() >= until:
                fh.close()
                return None
            time.sleep(0.1)


def serve(port, open_browser=False):
    STATE.mkdir(parents=True, exist_ok=True)
    held = _lock("serve.lock", 0)  # one office per data directory, for as long as this one serves
    if held is None:
        info = read_json(STATE / "server.json") or {}
        print(f"Another Codex Office is serving from {STATE}" + (f": {info['url']}" if info.get("url") else "") + ".")
        return 3
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        sys.exit(f"no free port in {port}-{port + 19}")
    port = httpd.server_address[1]
    httpd.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    write_json(STATE / "server.json", {"pid": os.getpid(), "pid_start": _proc_start(os.getpid()), "port": port,
                                       "url": url, "root": str(ROOT), "version": plugin_version(),
                                       "started": time.time()})
    print(f"Codex Office {plugin_version()}: {url}  (journal {LOG_DIR}; Ctrl-C stops the office — actions in flight keep running)",
          flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        info = read_json(STATE / "server.json")
        if info and info.get("pid") == os.getpid():
            try:
                (STATE / "server.json").unlink()
            except OSError:
                pass


def stop():
    """Stop the recorded office. Callers hold the start lock (office.lock)."""
    info = running()
    if not info:
        print("Codex Office is not running.")
        return 0
    os.kill(int(info["pid"]), 15)
    for _ in range(50):
        if _proc_start(info["pid"]) != info.get("pid_start"):
            break
        time.sleep(0.1)
    print(f"Codex Office stopped (was {info['url']}). Actions already sent keep running.")
    return 0


def locked(fn):
    """Run fn() holding the start lock, so two starts (or a start and a stop) never interleave."""
    me = f"python3 {_sh(Path(__file__).resolve())}"
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        held = _lock("office.lock", 30)
    except OSError as e:
        print(f"Cannot write {STATE} ({e.strerror}). Claude Code's Bash sandbox blocks the plugin's data "
              f"directory; run it from your own terminal instead:\n  {me} start --open --data {_sh(DATA)}")
        return 0
    if held is None:
        print(f"Another start or stop of the office has held {STATE / 'office.lock'} for 30 s; try again in a moment.")
        return 0
    with held:
        try:
            return fn()
        except Exception as e:  # say it and exit 0: a failing ! step would hide this message
            print(f"Codex Office could not do that: {type(e).__name__}: {e}\nRun it from your own terminal "
                  f"to see more:\n  {me} start --open --data {_sh(DATA)}")
            return 0


def start(port, open_browser):
    """Start the office detached (launchd its parent), or find the one already running. An office
    started by another version of the plugin is replaced, so the page always matches this server."""
    info = running()
    me = f"python3 {_sh(Path(__file__).resolve())}"
    data = f" --data {_sh(DATA)}"
    if info and info.get("root") != str(ROOT):
        print(f"Replacing the office of plugin {info.get('version')} ({info.get('root')}).")
        stop()
        info = None
    if not info:
        t0 = time.time()
        spawn_detached([sys.executable, str(Path(__file__).resolve()), "serve", "--port", str(port), "--data", str(DATA)],
                       STATE / "office.log")
        for _ in range(100):
            info = running()
            if info and float(info.get("started") or 0) >= t0 - 1:
                break
            time.sleep(0.1)
        else:
            tail = ""
            try:
                tail = (STATE / "office.log").read_text(errors="replace")[-1500:]
            except OSError:
                pass
            print(f"Codex Office did not come up within 10 s. Its log ({STATE / 'office.log'}) ends:\n{tail}")
            return 0
        print(f"Codex Office {info['version']} started: {info['url']}")
    else:
        print(f"Codex Office {info['version']} is already running: {info['url']}")
    if open_browser:
        try:
            webbrowser.open(info["url"])
        except Exception:  # a headless or sandboxed shell has no browser; the address is printed above
            pass
    print(f"It keeps running after this session. Status: {me} status{data}\nStop: {me} stop{data}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Codex Office — a live pixel office for your Codex runs")
    # One word: start, stop, status, serve, or act (an action process the office spawns).
    ap.add_argument("command", nargs="?", default="start", help="start (default), stop, status or serve")
    ap.add_argument("action", nargs="?", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=int(os.environ.get("CODEX_OFFICE_PORT") or 8765))
    ap.add_argument("--data", help="the plugin's data directory (default: $CLAUDE_PLUGIN_DATA)")
    ap.add_argument("--open", action="store_true", help="open the office in the browser")
    args = ap.parse_args()
    words = (args.command or "").split()
    args.command = words[0].lower() if words else "start"
    # start, stop, status and an unknown word exit 0 and say what happened, fallback included: the person
    # reads the outcome in what this prints, whoever ran it.
    if args.command not in ("start", "stop", "status", "serve", "act"):
        print(f"Unknown office command {args.command!r}: use start (the default), stop or status.")
        return 0
    configure(args.data)
    if args.command == "act":
        return run_action(args.action)
    if args.command == "serve":
        return serve(args.port, args.open)
    if args.command == "stop":
        return locked(stop)
    if args.command == "status":
        info = running()
        print(f"Codex Office {info['version']} is running: {info['url']} (pid {info['pid']})" if info
              else "Codex Office is not running.")
        return 0
    return locked(lambda: start(args.port, args.open))


if __name__ == "__main__":
    sys.exit(main())
