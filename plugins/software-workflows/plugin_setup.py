#!/usr/bin/env python3
"""What software-workflows needs on a machine that a plugin cannot switch on by itself: checked,
and fixed with the person's OK where it can be. /software-workflows:setup runs it; the
SessionStart hook runs the quick checks and points to setup when one fails.

  plugin_setup.py check [--data DIR]    every item, with the exact fix for each one not ready
  plugin_setup.py apply agent-teams     turn agent teams on in the person's own settings.json
  plugin_setup.py prepare [--data DIR]  build the Codex Oracle server's venv now (DIR is the
                                        plugin's data directory, ${CLAUDE_PLUGIN_DATA})

It runs under any python3 from 3.9 on (what macOS ships). Nothing changes without `apply` or
`prepare`; `apply` adds one key to one file and leaves everything else in it as it was."""
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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
    return out


def _check(data_dir):
    items = checks(data_dir)
    me = sys.argv[0]
    print(f"software-workflows setup check ({sum(1 for i in items if i[1])} of {len(items)} ready)")
    for item, ready, detail, fix, action in items:
        print(f"  {'OK     ' if ready else 'MISSING'}  {item:<19} {detail}")
        if fix:
            print(f"  {'':<7}  {'':<19} fix: {fix}")
    actions = [i[4] for i in items if i[4] and not i[1]]
    if actions:
        print("Setup can do these itself:")
        for action in actions:
            if action == "agent-teams":
                print(f"  agent-teams (ask the person first): python3 {me} apply agent-teams")
            elif action == "prepare":
                print(f"  prepare (writes only in the plugin's data directory): "
                      f"python3 {me} prepare --data {data_dir or ''}".rstrip())
    print(f"Check again: python3 {me} check --data {data_dir or ''}".rstrip())
    return 0


def apply_agent_teams():
    """Add env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1" to the person's own settings.json, in
    place: every other key stays as it was, the write is atomic, and a symlinked file is written
    through its link. A file that is not a JSON object is refused, never rewritten."""
    path = settings_path()
    target = path.resolve() if path.is_symlink() else path
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except OSError as exc:
        print(f"not changed: cannot read {target} ({exc})")
        return 1
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as exc:
        print(f"not changed: {target} is not valid JSON ({exc}); fix the file, then run setup again")
        return 1
    env = data.get("env", {}) if isinstance(data, dict) else None
    if not isinstance(env, dict):
        print(f"not changed: {target} does not hold a JSON object with an \"env\" object")
        return 1
    if env.get(TEAMS) == "1":
        print(f'already on: env.{TEAMS} is "1" in {target}')
        return 0
    data["env"] = env
    env[TEAMS] = "1"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if target.exists():
            os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))
        os.replace(tmp, target)
    except OSError as exc:
        print(f"not changed: cannot write {target} ({exc})")
        try:
            tmp.unlink()
        except OSError:
            pass
        return 1
    print(f'set env.{TEAMS} = "1" in {target}. Agent teams start in your next Claude Code session.')
    return 0


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
