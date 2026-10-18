#!/usr/bin/env python3
"""Cross-platform MCP server launcher: finds a Python >= 3.11, keeps the server's venv in the
plugin's data directory, builds it once under a lock, and runs the server from it.

It runs under any Python 3 — 3.9 is what macOS ships as python3 — because it only looks for the
interpreter the server needs (3.11+, for tomllib): CODEX_ORACLE_PYTHON when set, else the newest
python3.N on PATH or in the usual install directories, else this interpreter if it is new enough.

The venv lives in ${CLAUDE_PLUGIN_DATA} (Claude Code keeps that directory across plugin updates;
without it, beside this file as before), at venv/<key>: the key is the requirements, the
interpreter the venv links to and its version, and the platform, so a change to any of them builds
a new generation instead of running against a stale one. Builds are serialized by an exclusive
lock, and a generation counts only once it holds `.ready`, written after the server's own imports
load in it — so two sessions starting at once build once, and a build killed half-way is rebuilt,
never used. A running server holds a shared lock on its generation's `.in-use` file for its whole
life (the descriptor survives the exec) and hands it to each detached watchdog it starts, which
runs this venv's python after the server may be gone; an old generation is deleted only when no
process holds it.

`--prepare` makes the venv ready and exits without starting the server (/software-workflows:setup
runs it, through plugin_setup.py).

stdout is the MCP JSON-RPC channel — ALL bootstrap output goes to stderr.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = ROOT.name
IS_WINDOWS = os.name == "nt"
MIN = (3, 11)
IMPORT_CHECK = "import mcp.server.fastmcp, tomllib"

try:
    import fcntl
except ImportError:  # Windows: builds lock through msvcrt below
    fcntl = None


def _err(msg):
    print(f"[{NAME}] {msg}", file=sys.stderr, flush=True)


def _probe(python):
    """((major, minor), sys.executable) of an interpreter, or None when it does not run."""
    try:
        out = subprocess.run([python, "-c", "import sys; print(*sys.version_info[:2], sys.executable)"],
                             capture_output=True, text=True, timeout=20).stdout.split(None, 2)
        return (int(out[0]), int(out[1])), out[2].strip()
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _find_python():
    """The interpreter for the server's venv as (path, ((major, minor), sys.executable)), or (None, why)."""
    chosen = os.environ.get("CODEX_ORACLE_PYTHON", "")
    if chosen:  # the user's explicit choice is used as it is, or refused
        found = shutil.which(chosen) or chosen
        info = _probe(found)
        if info is None or info[0] < MIN:
            got = "%d.%d" % info[0] if info else "not runnable"
            return None, f"CODEX_ORACLE_PYTHON={chosen} is {got}; the server needs >= 3.11"
        return found, info
    seen, best = set(), (None, None)
    suffix = ".exe" if IS_WINDOWS else ""
    dirs = os.environ.get("PATH", "").split(os.pathsep) + [
        "/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")]
    for d in dirs:
        for minor in range(30, MIN[1] - 1, -1):
            if best[1] is not None and (3, minor) <= best[1][0]:
                break  # newest first: nothing newer is left in this directory
            p = Path(d) / f"python3.{minor}{suffix}"
            try:
                real = str(p.resolve()) if p.is_file() else ""
            except OSError:
                real = ""
            if not real or real in seen:
                continue
            seen.add(real)
            info = _probe(str(p))
            if info is not None and info[0] >= MIN:
                best = (str(p), info)
    here = sys.version_info[:2]
    if here >= MIN and (best[1] is None or here > best[1][0]):
        best = (sys.executable, (here, sys.executable))
    if best[0] is None:
        return None, (f"no Python >= 3.11 found (this launcher runs under {sys.version.split()[0]}; looked "
                      f"for python3.11 … python3.30 on PATH, /opt/homebrew/bin, /usr/local/bin); install one, "
                      f"or set CODEX_ORACLE_PYTHON to its path")
    return best


def _venv_python(venv):
    return venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def _lock(path, shared=False, wait=True):
    """An open descriptor holding a lock on `path`, or None when wait=False and it is taken."""
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(fd, mode if wait else mode | fcntl.LOCK_NB)
            return fd
        import msvcrt
        while True:  # msvcrt's own blocking mode gives up after 10 s, and a build takes longer
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return fd
            except OSError:
                if not wait:
                    raise
                time.sleep(1)
    except OSError:
        os.close(fd)
        if wait:
            raise
        return None


def _ready(venv):
    return (venv / ".ready").is_file() and _venv_python(venv).exists()


def _build(venv, python, build_lock):
    """Build the venv at its own path (a venv's scripts name the path they were made at)."""
    if venv.exists():
        _err(f"removing an unfinished venv at {venv}")
        shutil.rmtree(venv)
    _err(f"building the server venv at {venv} with {python}")
    py = _venv_python(venv)
    # Claude Code kills a server slow to start (MCP_TIMEOUT) but not its children: a pip cut off
    # that way runs on, so it holds the build lock too, and the next start waits for it to stop
    # instead of building over it.
    hold = () if IS_WINDOWS else (build_lock,)
    for cmd in ([python, "-m", "venv", str(venv)],
                [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                 "-r", str(ROOT / "requirements.txt")],
                [str(py), "-c", IMPORT_CHECK]):
        proc = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr, pass_fds=hold)
        if proc.returncode != 0:
            return f"{' '.join(cmd)} -> exit {proc.returncode}"
    (venv / ".ready").write_text(f"{python}\n")
    return ""


def _prune(generations, keep):
    """Delete other generations no running process holds."""
    if fcntl is None:
        return
    for old in generations.iterdir():
        if old.name == keep or not old.is_dir() or old.name.startswith("."):
            continue
        fd = _lock(old / ".in-use", wait=False) if (old / ".in-use").exists() else None
        if (old / ".in-use").exists() and fd is None:
            continue  # a server or its watchdog still runs from it
        try:
            shutil.rmtree(old)
            _err(f"removed an old server venv: {old}")
        except OSError as exc:
            _err(f"could not remove an old server venv {old}: {exc}")
        finally:
            if fd is not None:
                os.close(fd)


def _generation(info, data):
    """The venv directory for the interpreter `info` describes (raises OSError without requirements)."""
    (major, minor), exe = info
    requirements = (ROOT / "requirements.txt").read_bytes()
    # the venv links to the interpreter's own sys.executable (Homebrew's stays put across patch
    # releases), so that path, not the symlink's target, is what a generation depends on
    key = hashlib.sha256(requirements + f"\0{exe}\0{major}.{minor}\0{sys.platform}".encode()).hexdigest()[:16]
    # without a data directory (older Claude Code): beside this file, never in the .venv an older
    # launcher may still be serving from
    return (Path(data) / "venv" if data else ROOT / ".venvs") / key


def main(prepare=False):
    """Run the server; with prepare=True only make its venv ready (setup does this) and exit."""
    python, info = _find_python()
    if python is None:
        _err(f"bootstrap failed: {info}")
        return 1
    try:
        venv = _generation(info, os.environ.get("CLAUDE_PLUGIN_DATA"))
    except OSError as exc:
        _err(f"bootstrap failed: cannot read requirements.txt ({exc})")
        return 1
    generations, key = venv.parent, venv.name
    generations.mkdir(parents=True, exist_ok=True)

    build_lock = _lock(generations / ".lock")
    in_use = None
    try:
        if not _ready(venv):
            failed = _build(venv, python, build_lock)
            if failed:
                _err(f"bootstrap failed: {failed}")
                return 1
        if fcntl is not None and not prepare:
            in_use = _lock(venv / ".in-use", shared=True)  # held by the server until it exits
            _prune(generations, keep=key)
    finally:
        os.close(build_lock)
    if prepare:
        _err(f"the server venv is ready: {venv}")
        return 0

    py = _venv_python(venv)
    argv = [str(py), str(ROOT / "server.py")]
    if IS_WINDOWS:
        # No true exec() on Windows — run the server as a child; stdio handles are inherited, so the
        # JSON-RPC channel passes straight through (old generations are not pruned there).
        return subprocess.run(argv).returncode
    if in_use is not None:
        os.set_inheritable(in_use, True)
        os.environ["CODEX_ORACLE_VENV_LOCK"] = str(in_use)  # server.py passes it to its watchdogs
    os.execv(str(py), argv)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main(prepare="--prepare" in sys.argv[1:]))
