"""One conflict model for claims, path operations and opaque command custody."""
import asyncio
import re
import shlex
import time

from .client import CoordinatorError
from .ledger import normalize, overlaps
from .store import uid


RECORDING_COMMANDS = {"commit", "push", "tag", "update-ref", "symbolic-ref", "reset", "checkout", "switch",
                      "restore", "stash", "worktree", "clean", "rebase", "merge", "cherry-pick", "revert",
                      "branch", "commit-tree", "update-index", "read-tree", "am", "apply"}


# A closed set: aliases and newly installed Git subcommands never inherit trust.
READ_GIT = {"status", "diff", "show", "log", "rev-parse", "ls-files", "ls-tree", "cat-file",
            "diff-tree", "diff-index", "diff-files", "show-ref", "for-each-ref", "describe",
            "merge-base", "rev-list", "check-attr", "check-ignore", "grep", "blame", "version"}


def shell_words(command):
    """Small conservative lexer; retain quoted punctuation as ordinary arguments.

    Expansions, heredocs and unsupported grammar refuse, rather than pretending
    shlex is a complete shell parser. No shell text is ever evaluated here.
    """
    tokens, word, present, quote, i = [], [], False, None, 0
    def flush():
        nonlocal word, present
        if present:
            tokens.append(("".join(word), False))
        word, present = [], False
    while i < len(command):
        char = command[i]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                word.append(char)
        elif char == "\\" and quote != "'":
            i += 1
            if i == len(command):
                raise ValueError("trailing escape")
            escaped = command[i]
            if escaped != "\n":
                if quote == '"' and escaped not in '$`"\\':
                    word.append("\\")
                word.append(escaped)
            present = True
        elif char == quote:
            quote = None
        elif char in "$`":
            raise ValueError("executable or parameter expansion")
        elif quote:
            word.append(char)
        elif char in "'\"":
            quote, present = char, True
        elif char == "#" and not present:
            end = command.find("\n", i)
            i = len(command) if end < 0 else end - 1
        elif char in ";|&()<>\n":
            flush()
            operator = ";" if char == "\n" else char
            if i + 1 < len(command) and char in "|&<>" and command[i + 1] == char:
                operator += char
                i += 1
            if operator in ("<<",) or char in "<>" and i + 1 < len(command) and command[i + 1] == "(":
                raise ValueError("unsupported shell input")
            tokens.append((operator, True))
        elif char.isspace():
            flush()
        else:
            word.append(char)
            present = True
        i += 1
    if quote:
        raise ValueError("unterminated quote")
    flush()
    return tokens


# Explicit read-option contracts. Unknown/abbreviated/helper options refuse.
READ_FLAGS = {
    "status": {"--short", "-s", "--branch", "-b", "--porcelain", "--porcelain=v1", "--porcelain=v2", "-z", "--untracked-files", "--untracked-files=all", "--untracked-files=no", "--ignored", "--no-renames"},
    "diff": {"--cached", "--staged", "--stat", "--numstat", "--shortstat", "--name-only", "--name-status", "--raw", "--patch", "-p", "-s", "--quiet", "--exit-code", "--check", "--binary", "--no-ext-diff", "--no-textconv", "--no-renames", "--relative", "--no-color", "--color=never", "-z", "--no-index"},
    "log": {"--oneline", "--all", "--branches", "--tags", "--remotes", "--graph", "--decorate", "--no-decorate", "--stat", "--name-only", "--name-status", "--no-merges", "--merges", "--reverse", "--first-parent", "--no-color", "--color=never", "--no-ext-diff", "--no-textconv"},
    "show": {"--stat", "--name-only", "--name-status", "--raw", "--patch", "-p", "-s", "--no-patch", "--no-ext-diff", "--no-textconv", "--no-color", "--color=never"},
    "rev-parse": {"--show-toplevel", "--show-prefix", "--show-cdup", "--git-dir", "--absolute-git-dir", "--git-common-dir", "--show-object-format", "--verify", "--short", "--is-inside-work-tree", "--is-bare-repository", "--abbrev-ref", "--symbolic-full-name", "--quiet", "-q"},
    "ls-files": {"--stage", "-s", "--cached", "-c", "--modified", "-m", "--others", "-o", "--deleted", "-d", "--unmerged", "-u", "--exclude-standard", "--error-unmatch", "--full-name", "-z"},
    "ls-tree": {"-r", "-t", "-d", "-l", "-z", "--name-only", "--name-status", "--full-tree", "--full-name", "--long"},
    "cat-file": {"-p", "-t", "-s", "-e"},
    "show-ref": {"--head", "--heads", "--tags", "--verify", "--quiet", "-q", "--hash", "--dereference", "-d"},
    "for-each-ref": {"--count=1"},
    "describe": {"--always", "--tags", "--all", "--long", "--exact-match", "--contains", "--dirty"},
    "merge-base": {"--all", "-a", "--is-ancestor", "--octopus", "--independent", "--fork-point"},
    "rev-list": {"--all", "--count", "--left-right", "--boundary", "--objects", "--parents", "--reverse", "--first-parent", "--no-merges", "--merges"},
    "check-attr": {"--all", "-a", "--cached", "-z"},
    "check-ignore": {"--verbose", "-v", "--quiet", "-q", "--no-index", "-z"},
    "grep": {"-n", "--line-number", "-i", "--ignore-case", "-I", "-l", "--files-with-matches", "-L", "--files-without-match", "-c", "--count", "-v", "--invert-match", "-w", "--word-regexp", "-F", "--fixed-strings", "-E", "--extended-regexp", "-G", "--basic-regexp", "--cached", "--no-index", "--untracked", "--no-recurse-submodules", "--full-name", "--no-textconv", "--no-color", "--color=never", "-z", "--null", "-q", "--quiet"},
    "blame": {"--line-porcelain", "--porcelain", "-p", "--incremental", "--root", "-w", "--no-textconv", "--no-progress"},
    "version": set(),
}
for _command in ("diff-tree", "diff-index", "diff-files"):
    READ_FLAGS[_command] = READ_FLAGS["diff"] | {"-r", "--root", "--no-commit-id"}
READ_VALUES = {
    "diff": {"-U", "--unified"}, "log": {"-n", "--max-count", "--format", "--pretty", "--since", "--until", "--author", "--grep"},
    "show": {"--format", "--pretty"}, "rev-parse": {"--short"}, "ls-files": {"--exclude", "-x"},
    "for-each-ref": {"--format", "--sort", "--count"}, "describe": {"--match", "--exclude", "--abbrev"},
    "rev-list": {"--max-count", "-n"}, "grep": {"-e", "-A", "-B", "-C", "--after-context", "--before-context", "--context", "--max-count"},
    "blame": {"-L"},
}


def git_invocation(words, isolated=False):
    if not isolated:
        return words[1:] != ["--version"]
    if not {"--no-pager", "--no-optional-locks", "--no-replace-objects"} <= set(words[1:]):
        return True
    safe_config = set()
    i = 1
    while i < len(words) and words[i].startswith("-"):
        option = words[i]
        if option == "-c" and i + 1 < len(words) and words[i + 1] in ("core.fsmonitor=false", "core.hooksPath=/dev/null", "submodule.recurse=false"):
            safe_config.add(words[i + 1])
            i += 2
        elif option == "-C" and i + 1 < len(words) and words[i + 1] and not words[i + 1].startswith("-"):
            i += 2
        elif option.startswith("-C") and len(option) > 2:
            i += 1
        elif option in ("--no-pager", "--no-optional-locks", "--literal-pathspecs", "--no-replace-objects"):
            i += 1
        elif option == "--version" and i == len(words) - 1:
            return False
        else:
            return True
    if i >= len(words) or words[i] not in READ_FLAGS:
        return True
    command = words[i]
    if safe_config != {"core.fsmonitor=false", "core.hooksPath=/dev/null", "submodule.recurse=false"}:
        return True
    arguments = words[i + 1:]
    options = arguments[:arguments.index("--")] if "--" in arguments else arguments
    if command in {"diff", "show", "log", "diff-tree", "diff-index", "diff-files"} and not {"--no-ext-diff", "--no-textconv"} <= set(options):
        return True
    if command in {"grep", "blame"} and "--no-textconv" not in options:
        return True
    i += 1
    while i < len(words):
        arg = words[i]
        if arg == "--":
            return False  # Remaining words are literal pathspecs/patterns.
        if not arg.startswith("-") or arg == "-":
            i += 1
            continue
        if arg in READ_FLAGS[command]:
            i += 1
            continue
        values = READ_VALUES.get(command, set())
        if arg in values and i + 1 < len(words):
            i += 2
            continue
        if any(arg.startswith(key + "=") and len(arg) > len(key) + 1 for key in values if key.startswith("--")):
            i += 1
            continue
        if any(arg.startswith(key) and len(arg) > len(key) for key in values if len(key) == 2):
            i += 1
            continue
        if command in ("log", "rev-list") and re.fullmatch(r"-[0-9]+", arg):
            i += 1
            continue
        return True
    return False


# Variables that load code at startup: a shell's startup files, interpreter preloads.
STARTUP_CODE = {"BASH_ENV", "ENV", "ZDOTDIR", "PROMPT_COMMAND", "PS4", "SHELLOPTS", "BASHOPTS",
                "PERL5OPT", "NODE_OPTIONS", "RUBYOPT", "PYTHONSTARTUP", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}


def invocation(words, depth):
    # A plain `git` refuses whatever its environment, and the one accepted form starts with
    # `/usr/bin/env -i`, which clears it: assignments in front cannot make Git out of another
    # command. Refusing them refused `PYTHONPATH=. python -m …` as Git execution (measured, 2.3.0).
    # Except the ones that make a shell or interpreter run code this reading never sees.
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", words[0]):
        if words[0].split("=", 1)[0] in STARTUP_CODE:
            return True
        words = words[1:]
    while words and words[0] in ("!", "if", "then", "elif", "else", "while", "until", "do", "{"):
        words = words[1:]
    if not words:
        return False
    executable = words[0].rsplit("/", 1)[-1]
    if executable == "git":
        return git_invocation(words)
    if executable in ("command", "exec", "env", "nohup"):
        rest = words[1:]
        if executable == "env":
            isolated = bool(rest) and rest[0] == "-i"
            assignments = []
            rest = rest[1:] if isolated else rest
            while rest and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", rest[0]):
                if rest[0].split("=", 1)[0] in STARTUP_CODE:
                    return True
                assignments.append(rest.pop(0))
            if isolated and rest and rest[0].rsplit("/", 1)[-1] == "git":
                from .gitio import SHELL_GIT_ENV, shell_git_binary, shell_env_binary
                return (words[0] != shell_env_binary() or set(assignments) != set(SHELL_GIT_ENV) or len(assignments) != len(SHELL_GIT_ENV)
                        or rest[0] != shell_git_binary() or git_invocation(rest, isolated=True))
            if rest and rest[0] == "--":
                rest = rest[1:]
            if not rest:
                return False  # `env` alone prints the environment.
            if rest[0].startswith("-"):
                return True
        else:
            while rest and rest[0] in ("--", "-p"):
                rest = rest[1:]
            if rest and rest[0].startswith("-"):
                return True
        return not rest or invocation(rest, depth + 1)
    if executable in ("sh", "bash", "dash", "zsh", "ksh"):
        rest = words[1:]
        for i, flag in enumerate(rest):
            if flag.startswith("-") and "c" in flag[1:] and set(flag[1:]) <= set("celuxv"):
                return i + 1 >= len(rest) or recording_command(rest[i + 1], depth + 1)
            if flag not in ("--noprofile", "--norc", "-l"):
                break
        return True
    if executable in ("eval", "source", ".", "alias", "function", "xargs", "sudo", "time"):
        return True
    if executable in ("printf", "echo"):
        return False  # Literal text is not an executable position.
    # Unknown wrappers/interpreters that mention Git have unestablished semantics.
    return any(re.search(r"(?:^|[\s/'\"])git(?:$|[\s'\"])", arg) for arg in words[1:])


def recording_command(command, depth=0):
    if not isinstance(command, str) or not command or depth > 16:
        return True
    try:
        tokens = shell_words(command)
    except ValueError:
        return True
    words, redirect = [], False
    for value, operator in tokens + [(";", True)]:
        if operator and value in (">", ">>", "<"):
            if redirect:
                return True
            redirect = True
        elif operator:
            if redirect or invocation(words, depth):
                return True
            if value not in (";", "|", "||", "&", "&&", "(", ")"):
                return True
            words = []
        elif redirect:
            redirect = False
        else:
            words.append(value)
    return False


def covered(path, exclusion):
    return path == exclusion or path.startswith(exclusion + "/")


def effect_conflict(left, right):
    """One symmetric predicate for normalized path effects and command windows."""
    if left.get("checkout") != right.get("checkout"):
        return False
    if left.get("operation") and left.get("operation") == right.get("operation"):
        return False
    a, b = left.get("level", "paths"), right.get("level", "paths")
    if a == "shared" and not left.get("unmediated") or b == "shared" and not right.get("unmediated"):
        other = right if a == "shared" and not left.get("unmediated") else left
        return other.get("unmediated", False) or other.get("level") == "exclusive"
    if a == b == "paths":
        return any(overlaps(x, y) for x in left["keys"] for y in right["keys"])
    if a == "paths":
        left, right = right, left
    if right.get("level", "paths") == "paths":
        return any(not any(covered(path, excluded) for excluded in left.get("excludes", [])) for path in right["keys"])
    return True


def path_effect(checkout, paths, operation=None):
    return {"checkout": checkout["id"], "level": "paths", "operation": operation,
            "keys": [normalize(checkout, path)["key"] for path in paths]}


def interfering(store, checkout, paths, operation=None, since=None):
    effect = path_effect(checkout, paths, operation)
    # Only this checkout's custody can conflict: held, or released since `since`.
    found = {r["id"]: r for r in store.held_custody(checkout["id"])}
    if since is not None:
        found.update((r["id"], r) for r in store.released_custody(checkout["id"], since))
    return [ident for ident, r in sorted(found.items()) if effect_conflict(r, effect)]


class Custody:
    def __init__(self, store, ledger):
        self.store = store
        self.ledger = ledger
        self.condition = asyncio.Condition()

    def claims(self, checkout, unit=None):
        return list(self.store.selected("SELECT * FROM records WHERE kind='claim' AND json_extract(data,'$.state')='held' AND "
                    "json_extract(data,'$.checkout')=?" + (" AND parent=?" if unit else "") + " ORDER BY id",
                    (checkout, unit) if unit else (checkout,)))

    def active(self, checkout):
        return list(self.store.held_custody(checkout))

    def conflicts(self, checkout, unit, paths):
        conflicts = []
        for claim in self.claims(checkout):
            if claim["unit"] != unit and any(overlaps(p["key"], claim["key"]) for p in paths):
                conflicts.append({"holder": claim["unit"], "scope": claim["path"], "kind": "claim"})
        for reservation in self.active(checkout):
            if effect_conflict(reservation, {"checkout": checkout, "level": "paths", "keys": [p["key"] for p in paths]}):
                if paths:
                    conflicts.append({"holder": reservation["unit"], "scope": "checkout minus existing other claims",
                                      "kind": "custody", "reservation": reservation["id"]})
        return conflicts

    def unit_active(self, unitid):
        return list(self.store.held_custody(unit=unitid))

    async def acquire(self, actor, paths, cwd=None):
        from .routing import grouped
        groups = grouped(self.store, actor, paths, cwd)
        normalized = {key: [normalize(self.store.get(key), path) for path in values] for key, values in groups.items()}
        for key in groups:
            self.ledger.require_owned(self.store.get(key))
        async with self.condition:
            if self.store.get(actor["unit"])["state"] != "open":
                raise CoordinatorError("UNIT_FROZEN", "Cannot claim paths for a sealed unit")
            from .participants import require_writable
            for key in groups:
                require_writable(self.store, actor["unit"], key)
            conflicts = [dict(conflict, checkout=key, root=self.store.get(key)["root"])
                         for key, values in normalized.items() for conflict in self.conflicts(key, actor["unit"], values)]
            if conflicts:
                request = uid("request")
                self.store.put("claim_request", request, {"actor": actor["id"], "unit": actor["unit"],
                               "paths": paths, "conflicts": conflicts, "state": "pending"}, actor["unit"])
                raise CoordinatorError("CLAIM_CONFLICT", "Scope held by " + conflicts[0]["holder"] +
                                       "; send a message to that holder or the lead, then request lead-authorized handback",
                                       {"conflicts": conflicts, "request": request})
            result = []
            with self.store.transaction():
                for key, values in normalized.items():
                    held = {c["key"]: c for c in self.claims(key) if c["unit"] == actor["unit"]}
                    for path in values:
                        existing = held.get(path["key"])
                        if existing:
                            result.append(existing["id"])
                        else:
                            ident = uid("claim")
                            held[path["key"]] = self.store.put("claim", ident, dict(path, checkout=key, root=self.store.get(key)["root"],
                                                               unit=actor["unit"], actor=actor["id"], state="held"), actor["unit"])
                            result.append(ident)
                self.store.event("claim/acquired", actor["id"], {"claims": result})
            return {"claims": result}

    def release_request(self, actor, paths, cwd=None):
        from .routing import grouped, qualified
        resolved = grouped(self.store, actor, paths, cwd)
        ident = uid("request")
        self.store.put("release_request", ident, {"unit": actor["unit"], "actor": actor["id"],
                       "paths": paths, "qualified_paths": [qualified(key, path) for key, values in resolved.items() for path in values],
                       "state": "pending_lead"}, actor["unit"])
        return {"request": ident, "state": "pending_lead", "message": "Claims stay held until the lead authorizes handback or publication reconciles"}

    async def release(self, unit, paths=None, cwd=None):
        if self.unit_active(unit["id"]):
            raise CoordinatorError("CUSTODY_HELD", "Reconcile command custody before releasing claims")
        for operation in self.store.all("operation", unit["id"]):
            if operation["state"] not in ("verified", "reconciled", "denied", "resolved"):
                raise CoordinatorError("UNVERIFIED_OPERATION", "Cannot release an unreconciled operation's claim")
        if paths is not None:
            if not isinstance(paths, list):
                raise CoordinatorError("CLAIM_PATHS", "Handback paths must be a list or omitted for all claims")
            from .routing import grouped
            actor = dict(unit=unit["id"], checkout=unit["checkout"])
            paths = grouped(self.store, actor, paths, cwd)
        released = []
        async with self.condition:
            with self.store.transaction():
                for claim in self.store.all("claim", unit["id"]):
                    if claim["state"] != "released" and (paths is None or claim["path"] in paths.get(claim["checkout"], [])):
                        released.append({"id": claim["id"], "path": claim["path"], "checkout": claim["checkout"], "root": self.store.get(claim["checkout"])["root"]})
                        claim["state"] = "released"
                        self.store.save(claim)
                self.store.event("claim/released", unit["id"], {"paths": paths})
            self.condition.notify_all()
        return {"released": bool(released), "count": len(released), "claims": released,
                "message": "Claims released" if released else "No claims selected or eligible for release"}

    @staticmethod
    def git_guidance():
        from .gitio import safe_shell_git
        return "Supported helper-free example: " + safe_shell_git(["diff", "--no-ext-diff", "--no-textconv"])

    def declare(self, actor, command, reason):
        if not isinstance(command, str) or not command or not isinstance(reason, str) or not reason.strip():
            raise CoordinatorError("DECLARATION", "Shared declaration needs an exact command and a reason")
        if recording_command(command):
            raise CoordinatorError("LEAD_RECORDS", "the lead records; unestablished Git execution refused. " + self.git_guidance())
        ident = uid("declaration")
        self.store.put("declaration", ident, {"actor": actor["id"], "command": command, "reason": reason,
                       "state": "available", "level": "shared"}, actor["id"])
        return {"declaration": ident, "invocations": 1}

    async def reserve(self, actor, command, operation=None, timeout=110):
        if self.store.get(actor["unit"])["state"] != "open":
            raise CoordinatorError("UNIT_FROZEN", "Cannot start a command for a sealed or published unit")
        if recording_command(command):
            raise CoordinatorError("LEAD_RECORDS", "the lead records; unestablished Git execution refused. " + self.git_guidance())
        declaration = next((d for d in self.store.all("declaration", actor["id"])
                            if d["state"] == "available" and d["command"] == command), None)
        level = "shared" if declaration else "exclusive"
        ident = uid("custody")
        self.store.put("custody_request", ident + "_request", {"actor": actor["id"], "unit": actor["unit"],
                       "level": level, "state": "waiting", "created": time.time(), "generation": self.store.generation}, actor["unit"])
        request = self.store.get(ident + "_request")
        try:
            deadline = asyncio.get_running_loop().time() + timeout
            from .participants import checkout_ids, require_writable
            from .processes import baseline
            checkouts = [self.store.get(key) for key in checkout_ids(self.store, actor["unit"])]
            async with self.condition:
                process_baseline = None
                while True:
                    for checkout in checkouts:
                        self.ledger.require_owned(checkout)
                        require_writable(self.store, actor["unit"], checkout["id"])
                    if self.store.get(actor["unit"])["state"] != "open" or operation and self.store.get(operation)["state"] != "prepared":
                        request.update(state="interrupted")
                        self.store.save(request)
                        raise CoordinatorError("CUSTODY_INTENT_CHANGED", "Unit or invocation changed while waiting for custody")
                    if declaration and self.store.get(declaration["id"])["state"] != "available":
                        declaration, level = None, "exclusive"
                    proposed = [{"checkout": c["id"], "level": level, "operation": operation,
                                 "excludes": [claim["key"] for claim in self.claims(c["id"]) if claim["unit"] != actor["unit"]]}
                                for c in checkouts]
                    conflicts = [r for item in proposed for r in self.active(item["checkout"]) if effect_conflict(item, r)]
                    pending = [o for item in proposed for o in self.store.live_operations(item["checkout"])
                               if o["kind"] in ("patch", "lead-edit", "restore") and (o["state"] in ("prepared", "allowed", "running") or o.get("executing"))
                               and effect_conflict(item, path_effect(self.store.get(item["checkout"]), o["paths"], o["id"]))]
                    if not conflicts and not pending:
                        if process_baseline is None:
                            process_baseline = await asyncio.to_thread(baseline)
                            continue  # Recheck every checkout after the asynchronous collection.
                        break
                    process_baseline = None
                    left = deadline - asyncio.get_running_loop().time()
                    if left <= 0:
                        request.update(state="denied", holders=[r["unit"] for r in conflicts])
                        self.store.save(request)
                        raise CoordinatorError("CUSTODY_WAIT", "Custody wait reached the safe hook deadline; retry or message the holder",
                                               {"holders": [r["unit"] for r in conflicts]})
                    try:
                        await asyncio.wait_for(self.condition.wait(), min(left, 1))
                    except asyncio.TimeoutError:
                        pass
                with self.store.transaction():
                    if declaration:
                        declaration.update(state="consumed", invocation=ident)
                        self.store.save(declaration)
                    request["state"] = "granted"
                    self.store.save(request)
                    ids = []
                    for index, item in enumerate(proposed):
                        key = ident if index == 0 else uid("custody")
                        ids.append(key)
                        self.store.put("custody", key, dict(item, unit=actor["unit"], actor=actor["id"], command=command,
                                       group=ident, state="held", created=time.time(), terminals=[], processes=[],
                                       process_baseline=process_baseline, generation=self.store.generation), actor["unit"])
                    root = self.store.get(ident)
                    root["members"] = ids
                    self.store.save(root)
                return self.store.get(ident)
        finally:
            # A failed process snapshot or cancelled hook never granted custody.
            # Close its durable wait record so settlement cannot wait forever
            # for an acquisition that has already returned a refusal.
            current = self.store.get(request["id"])
            if current["state"] == "waiting":
                current.update(state="interrupted", reason="custody acquisition ended before grant")
                self.store.save(current)

    async def relinquish(self, reservation):
        async with self.condition:
            self.release_held(reservation)
            self.condition.notify_all()

    def release_held(self, reservation):
        """Under self.condition, inside the caller's transaction when it has one."""
        reservation["state"] = "released"
        reservation["completed"] = time.time()
        # Only a held window is checked against its baseline; kept, every command's process table
        # (80 KB on a busy machine) is decoded again by each later custody scan.
        reservation.pop("process_baseline", None)
        self.store.save(reservation)
        self.store.event("custody/released", reservation["id"], {})
