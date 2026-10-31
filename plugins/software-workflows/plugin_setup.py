#!/usr/bin/env python3
"""What software-workflows needs on a machine that a plugin cannot switch on by itself: checked,
and fixed with the person's OK where it can be. /software-workflows:setup runs it; the
SessionStart hook runs the quick checks and points to setup when one fails.

  plugin_setup.py check [--data DIR]    every item, with the exact fix for each one not ready
  plugin_setup.py apply agent-teams     turn agent teams on in the person's own settings.json
  plugin_setup.py prepare [--data DIR]  build the Codex Oracle server's venv now (DIR is the
                                        plugin's data directory, ${CLAUDE_PLUGIN_DATA})

It runs under any python3 from 3.9 on (what macOS ships). `check` measures push refusal
using disposable local repositories and retains a capability cache in plugin data.
`apply` adds one key to one settings file and leaves everything else as it was."""
import ctypes
import errno
import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # SessionStart loads this module by path.
TEAMS = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"
MEASURED_CODEX = (0, 163, 0)  # the codex-cli this release was measured on


def _launcher():
    """run_server.py, loaded by path: the interpreter search and venv layout the server uses."""
    spec = importlib.util.spec_from_file_location("sw_run_server", ROOT / "run_server.py")
    if spec is None or spec.loader is None:
        raise ImportError(ROOT / "run_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def settings_path():
    """The person's own settings file: Claude Code's CLAUDE_CONFIG_DIR, else ~/.claude."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return Path(base) / "settings.json"


def _run(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)


def _python_fix():
    if sys.platform == "darwin" and shutil.which("brew"):
        return "brew install python"
    if shutil.which("uv"):
        return "uv python install  (it puts python3.N in ~/.local/bin, where the server looks)"
    return "install Python 3.11 or newer from https://www.python.org/downloads/ or your package manager"


def checks(data_dir=None, quick=False):
    """[(item, ready, detail, fix, action)]. `action` names what setup may do itself, with the
    person's OK. `quick` is the session-start subset: no codex version run, and no server venv
    check (the MCP server is building it while the session starts)."""
    out = []
    if _truthy(os.environ.get(TEAMS)):
        out.append(("agent teams", True, f"on ({TEAMS}={os.environ[TEAMS]})", "", ""))
    else:
        try:
            pending = _truthy((json.loads(settings_path().read_text(encoding="utf-8")).get("env") or {}).get(TEAMS))
        except (OSError, ValueError, AttributeError):
            pending = False
        if pending:
            out.append(("agent teams", True,
                        f"on in {settings_path()}; it takes effect in a new Claude Code session", "", ""))
        else:
            out.append(("agent teams", False, f"off: Claude Code starts agent teams only with {TEAMS}=1",
                        f'setup adds env.{TEAMS} = "1" to {settings_path()}', "agent-teams"))

    codex = shutil.which("codex")
    if codex is None:
        out.append(("Codex CLI", False, "codex is not on PATH",
                    "npm install -g @openai/codex   (or on macOS: brew install --cask codex)", ""))
        out.append(("codex login", False, "needs the Codex CLI first", "", ""))
    else:
        if quick:
            out.append(("Codex CLI", True, codex, "", ""))
        else:
            rc, text = _run([codex, "--version"])
            m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
            if rc != 0 or m is None:
                out.append(("Codex CLI", False, f"{codex} does not run: {text[:200]}",
                            "npm install -g @openai/codex", ""))
            elif tuple(int(g) for g in m.groups()) < MEASURED_CODEX:
                out.append(("Codex CLI", True, f"{text} at {codex}, older than 0.163.0, the version this "
                            "release was measured on", "npm install -g @openai/codex@latest", ""))
            else:
                out.append(("Codex CLI", True, f"{text} at {codex}", "", ""))
        rc, text = _run([codex, "login", "status"])
        first = text.splitlines()[0] if text else f"codex login status exited {rc}"
        if rc == 0:
            out.append(("codex login", True, first, "", ""))
        else:
            out.append(("codex login", False, first,
                        "type  ! codex login  in the Claude prompt (it opens a browser), "
                        "or run  codex login  in a terminal", ""))

    launcher = _launcher()
    python, info = launcher._find_python()
    if python is None:
        out.append(("Python 3.11+", False, info, _python_fix(), ""))
    else:
        out.append(("Python 3.11+", True, "%d.%d at %s" % (info[0][0], info[0][1], python), "", ""))
        if not quick:
            venv = launcher._generation(info, data_dir)
            if launcher._ready(venv):
                out.append(("server environment", True, f"ready ({venv})", "", ""))
            else:
                out.append(("server environment", False, f"not built yet ({venv})",
                            "setup builds it now (about 10 s); otherwise the server builds it on its next start",
                            "prepare"))

    # A codex-oracle server registered by hand (1.x instructions) next to this plugin's own gives
    # two tool sets; only server NAMES are read, never their settings.
    try:
        conf = json.loads((Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        conf = {}
    dupes = [("user", name) for name in (conf.get("mcpServers") or {}) if "codex" in name.lower()
             and "oracle" in name.lower()] if isinstance(conf, dict) else []
    for project, entry in ((conf.get("projects") or {}).items() if isinstance(conf, dict) else ()):
        if isinstance(entry, dict) and isinstance(entry.get("mcpServers"), dict):
            dupes += [(f"local, in {project}", name) for name in entry["mcpServers"]
                      if "codex" in name.lower() and "oracle" in name.lower()]
    if dupes:
        scope, name = dupes[0]
        out.append(("codex-oracle registration", False,
                    f"also registered by hand: {', '.join(f'{n} ({s})' for s, n in dupes)}; this plugin "
                    "registers its own, and two registrations give two tool sets",
                    f"claude mcp remove {shlex.quote(name)} -s {scope.split(',')[0]}"
                    + (f"   (run it in {scope.split('in ', 1)[1]})" if scope.startswith("local") else ""), ""))
    else:
        out.append(("codex-oracle registration", True, "only the plugin's own", "", ""))

    # the real git: in the Bash tool the push gate's shim comes first on PATH
    shim = os.path.realpath(ROOT / "hooks" / "gitshim")
    git = shutil.which("git", path=os.pathsep.join(
        d for d in os.environ.get("PATH", "").split(os.pathsep) if d and os.path.realpath(d) != shim))
    if git is None:
        out.append(("git", False, "git is not on PATH",
                    "xcode-select --install" if sys.platform == "darwin"
                    else "install git with your package manager", ""))
    else:
        out.append(("git", True, git, "", ""))
    from coordinator.push_capabilities import report
    from coordinator.client import clean_environment
    from coordinator import push_guard
    try:
        push_guard.verify({}, clean_environment(), configuration=False)
    except Exception as exc:
        out.append(("Codex launch guard", False, str(getattr(exc, "code", type(exc).__name__)),
                    "The Codex launch environment or refusal helper is unavailable; reinstall the plugin.", ""))
    try:
        capabilities = report(data_dir, quick=quick)
    except Exception as exc:
        capabilities = [{"binary": {"path": "git"}, "cases": [],
                         "error": "unverified: " + str(getattr(exc, "code", type(exc).__name__))}]
    for result in capabilities:
        cases = [row for row in result["cases"] if row["case"] != "control"]
        ordinary = [row for row in cases if row["case"] in ("ordinary", "url", "file_url")]
        limits = [row for row in cases if row not in ordinary]
        # SessionStart reports a measured ordinary failure, never missing/stale
        # evidence or the documented ways unrestricted programs escape a guard.
        if quick and not any(row["status"] != "refused" for row in ordinary):
            continue
        detail = result.get("version", result["binary"]["path"]) + ": " + (result.get("error") or
                 ", ".join(row["case"] + "=" + row["status"] for row in ordinary))
        if limits:
            detail += "; measured limitations: " + ", ".join(row["case"] + "=" + row["status"] for row in limits)
        ready = (len(ordinary) == 3 and not result.get("error")
                 and all(row["status"] == "refused" for row in ordinary))
        out.append(("Codex push guard: " + result["binary"]["path"], ready, detail,
                    "The lead (Claude) pushes. Review measured limitations with /software-workflows:setup; unrestricted descendants are not a security boundary." if not ready else "", ""))
    return out


def _sh(path):
    """A path as commands/setup.md quotes it — in double quotes, the form its allowed-tools rule
    matches (measured on Claude Code 2.1.302 with a space in the plugin root: an unquoted ! step
    breaks, and a quoted step runs only under the quoted rule) — or shell-quoted when double
    quotes would not keep it as it is."""
    if re.search(r'["$`\n]|\\(?=["$`\\])', path):
        return shlex.quote(path)
    return f'"{path}"'


def _check(data_dir):
    items = checks(data_dir)
    me = _sh(sys.argv[0])
    data = f" --data {_sh(data_dir)}" if data_dir else ""
    print(f"software-workflows setup check ({sum(1 for i in items if i[1])} of {len(items)} ready)")
    for item, ready, detail, fix, action in items:
        print(f"  {'OK     ' if ready else 'MISSING'}  {item:<25} {detail}")
        if fix:
            print(f"  {'':<7}  {'':<25} fix: {fix}")
    actions = [i[4] for i in items if i[4] and not i[1]]
    if actions:
        print("Setup can do these itself:")
        for action in actions:
            if action == "agent-teams":
                print(f"  agent-teams (ask the person first): python3 {me} apply agent-teams")
            elif action == "prepare":
                print(f"  prepare (writes only in the plugin's data directory): python3 {me} prepare{data}")
    print(f"Check again: python3 {me} check{data}")
    return 0


def _read_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _flag_rename():
    """The C rename that takes flags, with its (exchange, never-replace) flags: macOS renameatx_np
    (RENAME_SWAP, RENAME_EXCL), Linux renameat2 (RENAME_EXCHANGE, RENAME_NOREPLACE); None where
    there is none (Windows, an older C library). As scripts/codex_src.py `_exclusive_rename`."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except (OSError, TypeError):
        return None
    for name, flags in (("renameatx_np", (0x2, 0x4)), ("renameat2", (0x2, 0x1))):
        fn = getattr(libc, name, None)
        if fn is not None:
            fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            fn.restype = ctypes.c_int
            return fn, flags
    return None


def _with_agent_teams(raw, target):
    """(the settings bytes with agent teams on, or None when they need no change; a refusal, or
    "")."""
    try:
        text = raw.decode("utf-8") if raw is not None else ""
        data = json.loads(text) if text.strip() else {}
    except ValueError as exc:
        return None, f"not changed: {target} is not valid JSON ({exc}); fix the file, then run setup again"
    env = data.get("env", {}) if isinstance(data, dict) else None
    if not isinstance(env, dict):
        return None, f"not changed: {target} does not hold a JSON object with an \"env\" object"
    if env.get(TEAMS) == "1":
        return None, ""
    data["env"] = env
    env[TEAMS] = "1"
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), ""


def _finish(dir_fd, code):
    if dir_fd >= 0:
        try:
            os.close(dir_fd)
        except OSError:
            pass
    return code


def _exclusive(target, prefix, suffix=""):
    """A new, empty file beside `target` under a name nothing held before (created exclusively), so
    a temp or recovery file an earlier run left — even under a reused PID — is never truncated or
    overwritten (review of 2026-10-18, turn 8)."""
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=prefix, suffix=suffix)
    os.close(fd)
    return Path(name)


def apply_agent_teams():
    """Add env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1" to the person's own settings.json, in
    place: every other key stays as it was, the write is atomic, and a symlinked file is written
    through its link. A file that is not a JSON object is refused, never rewritten.

    Claude Code writes this file too (review of 2026-10-18). The merged file is EXCHANGED with the
    one in place, atomically, and when what that displaced is not the text the merge was made from,
    a write landed meanwhile: setup moves the displaced text out to a durable recovery file BEFORE
    anything else, merges it in turn, and exchanges again, until a swap displaces only setup's own
    text — so a concurrent write is always either in the live file or in that recovery file, never
    only in memory, and a crash at any point loses nothing. The invariant holds the HIGH finding of
    the turn-7 review closed. A file that did not exist is created only if it still does not. Where
    the system has no exchange (Windows; a filesystem that refuses it), the file is read again just
    before an ordinary replace instead, and a write landing in that last instant is the one setup
    cannot see (documented, not claimed closed)."""
    path = settings_path()
    target = path.resolve() if path.is_symlink() else path
    tmp = None   # setup's own merge, in a file made for it at each write
    keep = None  # durable home for a concurrent write, made the first time one is displaced
    flagged = _flag_rename()
    done = f'set env.{TEAMS} = "1" in {target}. Agent teams start in your next Claude Code session.'
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        expect = _read_bytes(target)  # the text in place, as far as setup knows
        dir_fd = os.open(target.parent, os.O_RDONLY) if flagged else -1
    except OSError as exc:
        print(f"not changed: cannot read {target} ({exc})")
        return 1
    base, saved = expect, False  # base: the newest text to merge onto; saved: `keep` holds a concurrent write
    tmp_holds_displaced = False  # tmp holds a concurrent write not yet moved to `keep` (one syscall wide)
    try:
        for _ in range(5):
            merged, refusal = _with_agent_teams(base, target)
            if merged is None and base == expect:
                print(refusal or f'already on: env.{TEAMS} is "1" in {target}')
                return _finish(dir_fd, 1 if refusal else 0)
            new = merged if merged is not None else base  # else: a displaced write, merged, going back in
            assert new is not None  # base is None only when expect is None, where _with_agent_teams merges {} to bytes
            tmp = _exclusive(target, f".{target.name}.", ".tmp")
            tmp.write_bytes(new)
            if expect is not None:
                os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))
            else:  # a file setup creates gets the mode a plain write would give it
                um = os.umask(0)
                os.umask(um)
                os.chmod(tmp, 0o666 & ~um)
            if flagged:
                fn, (exchange, exclusive) = flagged
                rc = fn(dir_fd, os.fsencode(tmp.name), dir_fd, os.fsencode(target.name),
                        exclusive if expect is None else exchange)
                err = ctypes.get_errno() if rc != 0 else 0
                if expect is None and err == errno.EEXIST:  # created meanwhile: merge onto that
                    tmp.unlink()
                    base = expect = _read_bytes(target)
                    continue
                if err and err not in (errno.EINVAL, errno.ENOTSUP, errno.ENOSYS):
                    raise OSError(err, os.strerror(err), str(target))
                if not err:
                    if expect is None:  # created where nothing was: the rename moved tmp to target
                        print(refusal or done)
                        return _finish(dir_fd, 1 if refusal else 0)
                    # the swap put the displaced text in tmp; move it to `keep` at once, durably,
                    # before reading or merging it — on disk throughout, in tmp for a few syscalls
                    tmp_holds_displaced = True
                    if keep is None:
                        keep = _exclusive(target, f"{target.name}.displaced-by-setup-")
                    os.replace(tmp, keep)
                    tmp_holds_displaced, saved = False, True
                    displaced = _read_bytes(keep)
                    if displaced == expect:  # setup displaced its own earlier write: all merges are in
                        keep.unlink()
                        print(refusal or done)
                        return _finish(dir_fd, 1 if refusal else 0)
                    base, expect = displaced, new
                    continue
            # no exchange here: read again just before an ordinary replace
            now = _read_bytes(target)
            if now != expect:
                tmp.unlink()
                base = expect = now
                continue
            os.replace(tmp, target)
            print(refusal or done)
            return _finish(dir_fd, 1 if refusal else 0)
    except OSError as exc:
        if tmp_holds_displaced and tmp is not None:  # preserve the concurrent write only in tmp now
            try:
                if keep is None:
                    keep = _exclusive(target, f"{target.name}.displaced-by-setup-")
                os.replace(tmp, keep)
                tmp_holds_displaced, saved = False, True
            except OSError:
                pass  # tmp keeps the bytes on disk under its own name
        elif tmp is not None:
            try:
                tmp.unlink()  # tmp holds only setup's own merge, if it is still there
            except OSError:
                pass
        where = (f"; a concurrent write is preserved in {tmp}" if tmp_holds_displaced
                 else f"; a concurrent write is preserved in {keep}" if saved else "")
        print(f"stopped: cannot write {target} ({exc}){where}")
        return _finish(dir_fd, 1)
    if keep is None:  # every round met a newer file before setup wrote anything
        print(f"stopped: {target} kept changing while setup merged it; setup left it as it is. "
              "Run setup again.")
        return _finish(dir_fd, 1)
    # five swaps, each displacing a newer write: the live file holds setup's merge of an earlier
    # version; `keep` holds the last write it displaced, already merged into nothing live yet
    print(f"stopped: {target} kept changing while setup merged it. It holds setup's merge of an "
          f"earlier version; the newer text it displaced last is in {keep}. Compare the two, then "
          "run setup again.")
    return _finish(dir_fd, 1)


def main(argv):
    data_dir = None
    if "--data" in argv:
        i = argv.index("--data")
        data_dir = argv[i + 1] if i + 1 < len(argv) else ""
        argv = argv[:i] + argv[i + 2:]
    if argv[:1] == ["check"]:
        return _check(data_dir)
    if argv[:2] == ["apply", "agent-teams"]:
        return apply_agent_teams()
    if argv[:1] == ["prepare"]:
        if data_dir:
            os.environ["CLAUDE_PLUGIN_DATA"] = data_dir
        return _launcher().main(prepare=True)
    print(str(__doc__).split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
