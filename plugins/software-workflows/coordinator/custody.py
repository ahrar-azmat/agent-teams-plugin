"""One conflict model for claims, path operations and opaque command custody."""
import asyncio
import re
import shlex
import time

from .locks import finish_thread
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


REDIRECTIONS = ("<<<", "<<-", ">>", ">&", ">|", "<<", "<&", "<>", ">", "<")


def shell_words(command):
    """Small conservative lexer; retain quoted punctuation as ordinary arguments.

    Yields (value, kind): kind False for a word, True for a control operator, "redirect" for a
    redirection operator, then what it reads or writes: a "target" (a file or a descriptor), a
    "herestring", or a "heredoc" ({"body", "quoted"}; the body is read from the lines after its
    command line). A descriptor number in front of an operator is no argument. Expansions and
    unsupported grammar raise ValueError naming the construct, rather than pretending this is a
    complete shell parser. No shell text is ever evaluated here.
    """
    tokens, word, present, quoted, quote, i = [], [], False, False, None, 0
    pending, target = [], None  # heredocs whose lines are still to come; the redirection awaiting its word
    def flush():
        nonlocal word, present, quoted, target
        if present:
            value = "".join(word)
            if target in ("<<", "<<-"):
                heredoc = {"delimiter": value, "strip": target == "<<-", "quoted": quoted, "body": None}
                pending.append(heredoc)
                tokens.append((heredoc, "heredoc"))
            elif target:
                tokens.append((value, "herestring" if target == "<<<" else "target"))
            else:
                tokens.append((value, False))
            target = None
        word, present, quoted = [], False, False
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
                raise ValueError("a trailing escape")
            escaped = command[i]
            if escaped != "\n":  # An escaped newline only joins two lines: it quotes nothing and makes no word.
                if quote == '"' and escaped not in '$`"\\':
                    word.append("\\")
                word.append(escaped)
                present = quoted = True
        elif char == quote:
            quote = None
        elif char == "`" or char == "$" and command[i + 1:i + 2] == "(":
            raise ValueError("arithmetic expansion" if command.startswith("$((", i) else "command substitution")
        elif char == "$":
            raise ValueError("parameter expansion")
        elif quote:
            word.append(char)
        elif char in "'\"":
            quote, present, quoted = char, True, True
        elif char == "#" and not present:
            end = command.find("\n", i)
            i = len(command) if end < 0 else end - 1
        elif char in "<>" or command.startswith("&>", i):
            if char in "<>" and command[i + 1:i + 2] == "(":
                raise ValueError("process substitution")
            if target and not present:
                raise ValueError("a redirection without its target")
            if char in "<>" and present and not quoted and not target and re.fullmatch(
                    r"[0-9]+|\{[A-Za-z_][A-Za-z_0-9]*\}", "".join(word)):
                word, present = [], False  # A descriptor: the 2 of 2>&1, or the {name} that is to hold a new one.
            else:
                flush()
            operator = ("&>>" if command.startswith("&>>", i) else "&>") if char == "&" else next(
                o for o in REDIRECTIONS if command.startswith(o, i))
            tokens.append((operator, "redirect"))
            target = operator
            i += len(operator) - 1
        elif char in ";|&()\n":
            flush()
            if target:
                raise ValueError("a redirection without its target")
            operator = ";" if char == "\n" else char
            if i + 1 < len(command) and char in "|&" and command[i + 1] == char:
                operator += char
                i += 1
            elif command.startswith("|&", i):
                i += 1  # |& also pipes standard error.
            tokens.append((operator, True))
            if char == "\n" and pending:
                i = heredoc_bodies(command, i + 1, pending) - 1
                del pending[:]
        elif char.isspace():
            flush()
        else:
            word.append(char)
            present = True
        i += 1
    if quote:
        raise ValueError("an unterminated quote")
    flush()
    if target:
        raise ValueError("a redirection without its target")
    if pending:
        raise ValueError("an unterminated heredoc (no line equal to its delimiter)")
    return tokens


def heredoc_bodies(command, start, pending):
    """Read each pending heredoc's lines from `start` up to its delimiter line; return where the command goes on."""
    for heredoc in pending:
        lines = []
        while True:
            if start >= len(command):
                raise ValueError("an unterminated heredoc (no line equal to its delimiter)")
            end = command.find("\n", start)
            end = len(command) if end < 0 else end
            line = command[start:end].lstrip("\t") if heredoc["strip"] else command[start:end]
            start = end + 1
            # In an unquoted body the shell joins a line that ends in an escaping backslash to the next one before it
            # looks for the delimiter (bash, zsh: measured 11-02), so a delimiter split across lines ends the heredoc
            # and the lines after it run as commands. A quoted body joins nothing.
            if not heredoc["quoted"] and (len(line) - len(line.rstrip("\\"))) % 2:
                raise ValueError("a line continuation in an unquoted heredoc body (quote its delimiter: <<'EOF')")
            if line == heredoc["delimiter"]:
                break
            lines.append(line)
        heredoc["body"] = "\n".join(lines)
    return start


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


READ_GH = {"pr": {"view", "list", "status", "diff", "checks"},
           "issue": {"view", "list", "status"}, "release": {"view", "list", "download"},
           "repo": {"view", "list"}, "run": {"view", "list", "download"},
           "workflow": {"view", "list"}, "gist": {"view", "list"},
           "search": {"issues", "prs", "repos", "code", "commits"},
           "auth": {"status"}, "cache": {"list"}, "ruleset": {"list", "view"}}


def gh_read(words):
    """Only built-in read command paths, never an alias/extension named 'view'."""
    rest = list(words[1:])
    while rest and rest[0].startswith("-"):
        flag = rest.pop(0)
        if flag in ("--version", "--help", "-h"):
            return not rest
        if flag in ("-R", "--repo", "--hostname") and rest:
            rest.pop(0)
        elif flag.startswith(("--repo=", "--hostname=")) or flag.startswith("-R") and len(flag) > 2:
            continue
        else:
            return False
    if not rest:
        return False
    if rest[0] != "api":
        return len(rest) >= 2 and rest[1] in READ_GH.get(rest[0], set())
    rest = rest[1:]
    method, endpoints = "GET", 0
    values = {"--method", "-X", "--header", "-H", "--hostname", "--jq", "-q", "--template", "-t", "--cache", "--preview", "-p"}
    flags = {"--paginate", "--slurp", "--include", "-i", "--silent", "--verbose"}
    while rest:
        arg = rest.pop(0)
        if arg == "--":
            endpoints += len(rest)
            return endpoints == 1 and method in ("GET", "HEAD")
        if not arg.startswith("-"):
            endpoints += 1
            continue
        key, equal, val = arg.partition("=")
        if key in ("-f", "-F", "--field", "--raw-field", "--input") or arg.startswith(("-f", "-F")):
            return False
        if key in flags and not equal:
            continue
        if not equal and arg[:2] in ("-X", "-H", "-q", "-t", "-p") and len(arg) > 2:
            key, val, equal = arg[:2], arg[2:], True
        if key not in values:
            return False
        if not equal:
            if not rest:
                return False
            val = rest.pop(0)
        if key in ("--method", "-X"):
            if val not in ("GET", "HEAD"):
                return False
            method = val
    return endpoints == 1 and method in ("GET", "HEAD")


GIT_COMMAND = "a Git command"
MENTIONS_GIT = "that mentions Git (its semantics are not established)"
# `git` as a word of its own: a path's last part, a shell word, an option's value, a call, or a command inside text
# that an inner shell may run (`sh -c "cd x&&git commit"`, `{git,commit}`); a word naming a dashed Git program
# (git-receive-pack); or Git's own program directory (libexec/git-core/git-commit). In any letter case: on the
# default case-insensitive macOS file system `nice /usr/bin/GIT commit` runs git (measured 11-02).
GIT_WORD = re.compile(r"(?:^|[\s/'\"`;|&(){},=\\!<>])git(?:$|[\s'\"`;|&(){},<>])|(?:^|\s)git-[a-z]|/git-core/", re.IGNORECASE)
# The command word is a glob or a brace expansion: what runs is whatever it expands to.
COMMAND_PATTERN = re.compile(r"[*?]|\[.*\]|\{.*(?:,|\.\.).*\}")
# The system's own tool directories (read-only on macOS): a tool there is the one its name says.
SYSTEM_TOOL = re.compile(r"/(?:usr/)?bin/[^/]+")
# A closed list of read-only tools that never execute their arguments or their input.
DATA_ONLY = {"grep", "rg", "cat", "head", "tail", "wc", "ls", "stat", "file", "diff", "cmp", "sort", "uniq", "cut", "tr",
             "shasum", "sha256sum"}


def data_exec(executable, words):
    """The option of a data-only tool that runs another program: rg --pre, and sort --compress-program, which GNU
    getopt also takes abbreviated."""
    for word in words[1:]:
        if word == "--":
            break
        name = word.split("=", 1)[0]
        if executable == "rg" and name == "--pre":
            return "rg --pre, which runs a program for each file"
        if executable == "sort" and len(name) > 3 and "--compress-program".startswith(name):
            return "sort --compress-program, which runs a program"
    return False


def heredoc_expansion(reader, stdin):
    """The shell expands an unquoted heredoc body before the command that reads it starts, whatever that command is:
    a `$(…)` or a backtick there runs, even in a body given to cat, echo or command -v (measured 11-02)."""
    for body in stdin:
        if not body["quoted"] and re.search(r"[$`]", body["body"] or ""):
            return "expansion in an unquoted heredoc body read by " + reader + " (quote its delimiter: <<'EOF')"
    return False


def stdin_refusal(executable, stdin):
    """An interpreter's standard input is read like its arguments: it may be code."""
    expanded = heredoc_expansion(executable, stdin)
    if expanded:
        return expanded
    for body in stdin:
        if GIT_WORD.search(body["body"] or ""):
            return executable + " reading a heredoc or here-string " + MENTIONS_GIT
    return False


def invocation(words, depth, stdin=(), lookup=True):
    """Why this simple command must be refused (a reason naming it, or LEAD_PUBLISHES); False when admitted.
    lookup is False once something in front of the command (an assignment, an env option) may change what its name
    finds."""
    # A plain `git` refuses whatever its environment, and the one accepted form starts with
    # `/usr/bin/env -i`, which clears it: assignments in front cannot make Git out of another
    # command. Refusing them refused `PYTHONPATH=. python -m …` as Git execution (measured, 2.3.0).
    # Except the ones that make a shell or interpreter run code this reading never sees.
    # Keywords and assignments may follow each other (`if X=1 cmd`): neither is the command.
    while words and (words[0] in ("!", "if", "then", "elif", "else", "while", "until", "do", "{", "coproc")
                     or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", words[0])):
        if "=" in words[0]:
            if words[0].split("=", 1)[0] in STARTUP_CODE:
                return "the startup-code variable " + words[0].split("=", 1)[0]
            lookup = False
        words = words[1:]
    if not words or words in (["}"], ["done"], ["fi"], ["esac"]):
        # A heredoc after `)`, `}`, done, fi or esac is the standard input of every command the group runs.
        return stdin_refusal("a command group", stdin)
    if COMMAND_PATTERN.search(words[0]):
        return "a pattern (glob or brace expansion) in the command position"
    # What is refused matches in any case: on the default case-insensitive macOS file system, GIT finds and runs git.
    # What is trusted must be spelled exactly: on a case-sensitive one, Cat need not be cat.
    name = words[0].rsplit("/", 1)[-1]
    executable = name.lower()
    exact = name == executable
    by_name = exact and lookup and "/" not in words[0]  # The shell's own lookup of this name, nothing in front changing it.
    # Before any answer that admits: the shell runs what a heredoc expands before this command starts.
    expanded = heredoc_expansion(executable, stdin)
    if expanded:
        return expanded
    if executable == "git" or executable.startswith("git-"):
        return (executable != "git" or git_invocation(words)) and GIT_COMMAND
    if executable == "gh":
        return False if exact and gh_read(words) else "LEAD_PUBLISHES"
    if executable in ("eval", "source", ".", "alias", "function", "xargs", "sudo", "time"):
        return executable + ", which runs code this reading never sees"
    if executable in ("sh", "bash", "dash", "zsh", "ksh") and not exact:
        return "a shell (" + name + ") reading a script or its standard input"
    if exact and executable in ("command", "exec", "env", "nohup", "builtin"):
        rest = words[1:]
        if executable == "env":
            isolated = bool(rest) and rest[0] == "-i"
            assignments = []
            rest = rest[1:] if isolated else rest
            while not isolated and rest and (rest[0] in ("-u", "--unset") and len(rest) > 1 or re.fullmatch(
                    r"(?:-u|--unset=)[A-Za-z_][A-Za-z_0-9]*", rest[0])):
                rest = rest[2:] if rest[0] in ("-u", "--unset") else rest[1:]  # Unsetting a variable runs nothing.
                lookup = False
            while rest and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", rest[0]):
                if rest[0].split("=", 1)[0] in STARTUP_CODE:
                    return "the startup-code variable " + rest[0].split("=", 1)[0]
                assignments.append(rest.pop(0))
                lookup = False
            if isolated and rest and rest[0].rsplit("/", 1)[-1] == "git":
                from .gitio import SHELL_GIT_ENV, shell_git_binary, shell_env_binary
                return (words[0] != shell_env_binary() or set(assignments) != set(SHELL_GIT_ENV) or len(assignments) != len(SHELL_GIT_ENV)
                        or rest[0] != shell_git_binary() or git_invocation(rest, isolated=True)) and GIT_COMMAND
            if isolated:
                lookup = False
            if rest and rest[0] == "--":
                rest = rest[1:]
            if not rest:
                return False  # `env` alone prints the environment.
            if rest[0].startswith("-"):
                return "an env option this reading cannot establish"
        else:
            while rest and rest[0] in ("--", "-p"):
                rest = rest[1:]
            if executable == "command" and rest and re.fullmatch(r"-[pvV]+", rest[0]) and set(rest[0]) & set("vV"):
                return False  # command -v/-V only says what a name would run.
            if rest and rest[0].startswith("-"):
                return ("an " if executable == "exec" else "a ") + executable + " option this reading cannot establish"
        # Without a command, each of them runs nothing; exec only applies its redirections, and the commands after it
        # read a heredoc it gives the shell.
        return invocation(rest, depth + 1, stdin, lookup) if rest else stdin_refusal(executable, stdin)
    if executable in ("sh", "bash", "dash", "zsh", "ksh"):
        rest = words[1:]
        if rest == ["--version"]:
            return False
        # Every leading option word, + ones too: another option (+n turns -n off) may run what -n would only read.
        flags = rest[:next((i for i, word in enumerate(rest) if not word.startswith(("-", "+"))), len(rest))]
        if "-n" in flags and set(flags) <= {"-n", "--noprofile", "--norc"}:
            return False  # -n reads the commands and executes none of them: a syntax check.
        for i, flag in enumerate(rest):
            if flag.startswith("-") and "c" in flag[1:] and set(flag[1:]) <= set("celuxv"):
                if i + 1 >= len(rest):
                    return executable + " -c without its command"
                if rest[i + 1].startswith(("-", "+")):  # The shell runs the first word after its options (-c -- cmd).
                    return executable + " -c with an option after it (which word it runs is not established)"
                return _recording_command(rest[i + 1], depth + 1, lookup) or stdin_refusal(executable, stdin)
            if flag not in ("--noprofile", "--norc", "-l"):
                break
        return "a shell (" + executable + ") reading a script or its standard input"
    # A tool trusted by its name is trusted only where that name finds it: a path elsewhere, or an assignment in
    # front, may make it another program. Builtins (echo, printf) ignore PATH.
    system = exact and SYSTEM_TOOL.fullmatch(words[0])
    if exact and executable in ("printf", "echo") and ("/" not in words[0] or system):
        return False  # Literal text is not an executable position.
    if executable in DATA_ONLY and (by_name or system):
        return data_exec(executable, words)
    # Unknown wrappers/interpreters that mention Git have unestablished semantics.
    if any(GIT_WORD.search(arg) for arg in words[1:]):
        return executable + " with an argument " + MENTIONS_GIT
    return stdin_refusal(executable, stdin)


def _recording_command(command, depth=0, lookup=True):
    """Why the command must be refused (a reason naming it, or LEAD_PUBLISHES); False when admitted. lookup is
    False for a shell's command text run with assignments in front of that shell (see invocation)."""
    if not isinstance(command, str) or not command:
        return "an empty command"
    if depth > 16:
        return "commands nested more than 16 deep"
    try:
        tokens = shell_words(command)
    except ValueError as exc:
        return str(exc)
    words, stdin = [], []
    for value, kind in tokens + [(";", True)]:
        if kind is True:
            refused = invocation(words, depth, stdin, lookup)
            if refused:
                return refused
            if value not in (";", "|", "||", "&", "&&", "(", ")"):
                return "the shell operator " + value
            words, stdin = [], []
        elif kind == "heredoc":
            stdin.append(value)
        elif kind == "herestring":
            stdin.append({"body": value, "quoted": True})
        elif kind is False:
            words.append(value)
        # A redirection and its target (a file or a descriptor) are never an executable position.
    return False


def command_refusal_reason(command, depth=0):
    """The named reason the gate refuses a command (LEAD_PUBLISHES for gh publication), or None."""
    return _recording_command(command, depth) or None


def command_refusal(command, depth=0):
    refused = _recording_command(command, depth)
    return "LEAD_PUBLISHES" if refused == "LEAD_PUBLISHES" else "LEAD_RECORDS" if refused else None


def recording_command(command, depth=0):
    # Existing callers consume a Boolean; the admission paths retain the named code.
    return bool(command_refusal(command, depth))


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


def baseline_id(group):
    """The record of a custody group's process baseline: the process table its command is checked against while held
    (Custody.process_baseline). Custody granted through 2.4.22 holds it in each member (process_baseline)."""
    return "custody_baseline:" + group


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

    async def acquire(self, actor, paths, cwd=None, mark=None, lead_call=None, executor=None, effect=None):
        """Claim these paths for the actor's unit. A path outside the ledger (ledger.unwatched) is neither claimed nor
        held against another unit's claim: nothing there is recorded, so nothing there refuses an edit (2.4.0-2.4.3
        claimed it, and another unit's edit of the same ignored file was refused CLAIM_CONFLICT). Those are returned
        as `outside`. A restore passes its `mark`: a claim this call creates carries it until another call acquires
        that claim as well, and the restore lets go only of claims still carrying it (recovery.release_taken)."""
        from .routing import grouped, qualified
        groups = grouped(self.store, actor, paths, cwd)
        normalized = {key: [normalize(self.store.get(key), path) for path in values] for key, values in groups.items()}
        for key in groups:
            self.ledger.require_owned(self.store.get(key))
        outside = {key: await self.ledger.classify(self.store.get(key), [p["path"] for p in values]) for key, values in normalized.items()}
        normalized = {key: [p for p in values if p["path"] not in outside[key]] for key, values in normalized.items()}
        normalized = {key: values for key, values in normalized.items() if values}
        outside = [qualified(key, path) for key, values in sorted(outside.items()) for path in sorted(values)]
        from .locks import held
        async with held(self.condition):
            from . import admissions
            admissions.check("claim reservation")
            admission = admissions.mark()
            from .lead_calls import refuse
            for key, values in groups.items():
                refuse(self.store, key, values, lead_call, "RESTORE_EXECUTING" if mark else "CLAIM_CONFLICT")
            if self.store.get(actor["unit"])["state"] != "open":
                raise CoordinatorError("UNIT_FROZEN", "Cannot claim paths for a sealed unit")
            from .participants import require_writable
            for key in normalized:
                require_writable(self.store, actor["unit"], key)
            conflicts = [dict(conflict, checkout=key, root=self.store.get(key)["root"])
                         for key, values in normalized.items() for conflict in self.conflicts(key, actor["unit"], values)]
            if effect is not None:
                effect()
            if conflicts:
                request = uid("request")
                self.store.put("claim_request", request, {"actor": actor["id"], "unit": actor["unit"],
                               "paths": paths, "conflicts": conflicts, "state": "pending"}, actor["unit"])
                raise CoordinatorError("CLAIM_CONFLICT", "Scope held by " + conflicts[0]["holder"] +
                                       "; send a message to that holder or the lead, then request lead-authorized handback",
                                       {"conflicts": conflicts, "request": request})
            result = []
            transferred = []
            with self.store.transaction():
                for key, values in normalized.items():
                    from . import transfers
                    transferred.extend(transfers.acquire(self.store, actor, key, values))
                    held = {c["key"]: c for c in self.claims(key) if c["unit"] == actor["unit"]}
                    for path in values:
                        existing = held.get(path["key"])
                        if existing:
                            if existing.get("executor") not in (None, executor):
                                existing.pop("executor")
                                self.store.save(existing)
                            if existing.get("admission") not in (None, admission):
                                existing.pop("admission")
                                self.store.save(existing)
                            if existing.get("restore") not in (None, mark):
                                existing.pop("restore")  # this call holds it too: no longer the restore's alone
                                self.store.save(existing)
                            result.append(existing["id"])
                        else:
                            ident = uid("claim")
                            held[path["key"]] = self.store.put("claim", ident, dict(path, checkout=key, root=self.store.get(key)["root"],
                                                               unit=actor["unit"], actor=actor["id"], state="held",
                                                               **({"restore": mark} if mark else {}),
                                                               **({"admission": admission} if admission else {}),
                                                               **({"executor": executor} if executor else {})), actor["unit"])
                            result.append(ident)
                self.store.event("claim/acquired", actor["id"], {"claims": result})
            return {"claims": result, "transfers": transferred, **({"outside": outside} if outside else {})}

    async def acquire_pending(self, unitid):
        """A seal may carry pending grants only for its open unit's held claims."""
        from . import participants, transfers
        from .ledger import key
        async with self.condition:
            unit = self.store.get(unitid)
            if unit["state"] != "open":
                return []
            planned = []
            carried = transfers.publications(self.store)
            for checkoutid in participants.checkout_ids(self.store, unit):
                claims = self.claims(checkoutid)
                if not any(c["unit"] == unitid for c in claims):
                    continue
                checkout = self.store.get(checkoutid)
                for current in self.store.all("publication_right", checkoutid):
                    if current.get("owner") is not None or not transfers.right(self.store, checkoutid, current["path"], carried):
                        continue
                    covering = [c for c in claims if transfers.covers(c["key"], key(checkout, current["path"]))]
                    owners = {c["unit"] for c in covering}
                    if unitid not in owners:
                        continue
                    if owners != {unitid}:
                        raise CoordinatorError("CLAIM_CONFLICT", "Pending publication right has multiple covering units",
                                               {"path": current["path"], "units": sorted(owners)})
                    actor = self.store.get(covering[0]["actor"])
                    if actor["unit"] != unitid:
                        raise CoordinatorError("PUBLICATION_RIGHT_PROOF", "Covering claim and actor do not agree",
                                               {"claim": covering[0]["id"], "actor": actor["id"]})
                    planned.append((actor, current))
            with self.store.transaction():
                return [transfers.grant(self.store, actor, current) for actor, current in planned]

    def release_request(self, actor, paths, cwd=None):
        from .routing import grouped, qualified
        resolved = grouped(self.store, actor, paths, cwd)
        ident = uid("request")
        self.store.put("release_request", ident, {"unit": actor["unit"], "actor": actor["id"],
                       "paths": paths, "qualified_paths": [qualified(key, path) for key, values in resolved.items() for path in values],
                       "state": "pending_lead"}, actor["unit"])
        return {"request": ident, "state": "pending_lead", "message": "Claims stay held until the lead authorizes handback or publication reconciles"}

    async def release(self, unit, paths=None, cwd=None):
        from . import participants, transfers
        paths, authorizer = transfers.authorizer(paths)
        async with self.ledger.lifecycle.hold_many(participants.checkout_ids(self.store, unit)):
            return await self._release(unit, paths, cwd, authorizer)

    async def _release(self, unit, paths, cwd, authorizer):
        from . import transfers
        from .participants import checkout_ids
        if self.unit_active(unit["id"]):
            raise CoordinatorError("CUSTODY_HELD", "Reconcile command custody before releasing claims")
        # Snapshot SQL on its owner thread. Routing/canonicalization touches the
        # filesystem, so run it on a worker using only these immutable records.
        # Heads already cover every verified mutation; this release needs no Git
        # mutation_scope scan (nor one full checkout scan per selected claim).
        claims = list(self.store.all("claim", unit["id"]))
        checkouts = list(self.store.all("checkout"))
        members = list(self.store.all("participant", unit["id"]))
        ids = checkout_ids(self.store, unit)
        rights = transfers.owned(self.store, unit["id"], ids)
        heads = {key: dict(self.store.db.execute("SELECT path,revision FROM heads WHERE checkout=?", (key,))) for key in ids}
        def plan():
            from types import SimpleNamespace
            from .routing import grouped
            records = {r["id"]: r for r in [unit, *checkouts, *members]}
            def get(ident):
                if ident not in records:
                    raise CoordinatorError("PATH_ESCAPE", "Handback root is outside the pinned routing snapshot")
                return records[ident]
            snapshot = SimpleNamespace(get=get, all=lambda kind, parent=None: checkouts if kind == "checkout" else members)
            selected = grouped(snapshot, dict(unit=unit["id"], checkout=unit["checkout"]), paths, cwd) if paths is not None else None
            right_paths = {key: {r["path"] for r in rights if r["checkout"] == key} for key in ids}
            normalized = {key: {p: normalize(get(key), p) for p in set(heads[key]) | right_paths[key] | set((selected or {}).get(key, []))}
                          for key in ids}
            selected_rights = {key: {p for p in values if selected is None or any(
                overlaps(normalized[key][p]["key"], normalized[key][s]["key"]) for s in selected.get(key, []))}
                for key, values in right_paths.items()}
            planned = []
            for claim in claims:
                if claim["state"] == "released":
                    continue
                where = claim["checkout"]
                keys = [claim["key"]] if selected is None else [normalized[where][p]["key"] for p in selected.get(where, [])
                                                               if overlaps(normalized[where][p]["key"], claim["key"])]
                if not keys:
                    continue
                owned = {p for p in heads[where] if overlaps(normalized[where][p]["key"], claim["key"])}
                chosen = {p for p in owned if any(overlaps(normalized[where][p]["key"], key) for key in keys)}
                remainder = [] if any(claim["key"] == key or claim["key"].startswith(key + "/") for key in keys) else [
                    normalized[where][p] for p in sorted(owned - chosen)]
                planned.append((claim, chosen, remainder))
            return selected, planned, selected_rights
        paths, planned, selected_rights = await finish_thread(plan)
        # Recheck the selected claim subtrees, not unrelated checkout traffic.
        # A partial directory release also retains siblings, so its whole claim
        # subtree matters, including paths inserted while plan ran. key() is a
        # pure Unicode/case conversion; filesystem normalization stayed off-loop.
        from .ledger import key as path_key
        roots = {}
        for claim, _, _ in planned:
            roots.setdefault(claim["checkout"], set()).add(claim["key"])
        for where, values in selected_rights.items():
            if values:
                checkout = next(c for c in checkouts if c["id"] == where)
                roots.setdefault(where, set()).update(path_key(checkout, p) for p in values)
        ancestors = {where: {"/".join(k.split("/")[:n]) for k in keys for n in range(1, len(k.split("/")))}
                     for where, keys in roots.items()}
        def selected_heads(where, values):
            checkout = next(c for c in checkouts if c["id"] == where)
            def selected(path):
                canonical = path_key(checkout, path)
                return canonical in ancestors[where] or any("/".join(canonical.split("/")[:n]) in roots[where]
                    for n in range(1, len(canonical.split("/")) + 1))
            return {p: revision for p, revision in values.items() if selected(p)}
        expected = {where: selected_heads(where, heads[where]) for where in roots}
        released, transferred, replacements = [], [], []
        async with self.condition:
            if self.unit_active(unit["id"]):
                raise CoordinatorError("CUSTODY_HELD", "Reconcile command custody before releasing claims")
            if (list(self.store.all("claim", unit["id"])) != claims or transfers.owned(self.store, unit["id"], ids) != rights or
                    any(selected_heads(where, dict(self.store.db.execute("SELECT path,revision FROM heads WHERE checkout=?", (where,))))
                        != expected[where] for where in roots)):
                raise CoordinatorError("HANDBACK_CHANGED", "Claims, rights or revisions changed during handback preparation; retry")
            for publication in (*self.store.all("publication", unit["id"]), *self.store.all("publication_group", unit["id"])):
                if publication["phase"] not in ("complete", "abandoned", "superseded"):
                    raise CoordinatorError("HANDBACK_PUBLICATION", "Finish or abandon publication before handing back its paths", {"publication": publication["id"]})
            from .lead_calls import refuse
            for key in checkout_ids(self.store, unit["id"]):
                if paths is None or key in paths:
                    refuse(self.store, key, None if paths is None else paths[key], code="UNVERIFIED_OPERATION")
            # Admission could have begun while this release waited for the condition.
            for operation in self.store.all("operation", unit["id"]):
                if operation["state"] not in ("verified", "reconciled", "denied", "resolved"):
                    raise CoordinatorError("UNVERIFIED_OPERATION", "Cannot release an unreconciled operation's claim",
                                           {"operation": operation["id"]})
            carried = transfers.publications(self.store)
            with self.store.transaction():
                granted = {key: set(values) for key, values in selected_rights.items() if values}
                for claim, chosen, remainder in planned:
                    checkout = self.store.get(claim["checkout"])
                    granted.setdefault(checkout["id"], set()).update(chosen)
                    released.append({"id": claim["id"], "path": claim["path"], "checkout": checkout["id"], "root": checkout["root"]})
                    claim["state"] = "released"
                    self.store.save(claim)
                    # A directory-minus-path is not a claim shape. Preserve its
                    # known siblings as exact claims, using the prepared keys.
                    for path in remainder:
                        ident = uid("claim")
                        self.store.put("claim", ident, dict(path, checkout=checkout["id"], root=checkout["root"],
                            unit=unit["id"], actor=claim["actor"], state="held", split_from=claim["id"]), unit["id"])
                        replacements.append({"id": ident, "path": path["path"], "checkout": checkout["id"]})
                for key, chosen in granted.items():
                    transferred.extend(transfers.release(self.store, unit["id"], key, chosen, authorizer, carried))
                self.store.event("claim/released", unit["id"], {"paths": paths, "authorizer": authorizer, "transfers": transferred, "replacement_claims": replacements})
            self.condition.notify_all()
        return {"released": bool(released or transferred), "count": len(released), "rights_count": len(transferred),
                "claims": released, "transfers": transferred, "replacement_claims": replacements,
                "message": "Claims released" if released else "Publication rights released" if transferred else "No claims selected or eligible for release"}

    @staticmethod
    def git_guidance():
        from .gitio import safe_shell_git
        return "Supported helper-free example: " + safe_shell_git(["diff", "--no-ext-diff", "--no-textconv"])

    def refuse_recording(self, command):
        reason = command_refusal_reason(command)
        if reason == "LEAD_PUBLISHES":
            raise CoordinatorError(reason, "LEAD_PUBLISHES: the lead (Claude) publishes; Codex cannot perform this operation. Use a built-in gh read command or ask the lead.")
        if reason and (reason == GIT_COMMAND or reason.endswith(MENTIONS_GIT)):
            raise CoordinatorError("LEAD_RECORDS", "the lead records; unestablished Git execution refused (" + reason + "). The lead (Claude) pushes. "
                                   + self.git_guidance(), {"reason": reason})
        if reason:
            raise CoordinatorError("LEAD_RECORDS", "LEAD_RECORDS: the command gate refused " + reason + ": it admits only what it can read "
                                   "as never running Git. Rewrite the command without it, for example as a script file run by its interpreter.",
                                   {"reason": reason})

    def declare(self, actor, command, reason):
        if not isinstance(command, str) or not command or not isinstance(reason, str) or not reason.strip():
            raise CoordinatorError("DECLARATION", "Shared declaration needs an exact command and a reason")
        self.refuse_recording(command)
        ident = uid("declaration")
        self.store.put("declaration", ident, {"actor": actor["id"], "command": command, "reason": reason,
                       "state": "available", "level": "shared"}, actor["id"])
        return {"declaration": ident, "invocations": 1}

    async def reserve(self, actor, command, operation=None, timeout=110):
        if self.store.get(actor["unit"])["state"] != "open":
            raise CoordinatorError("UNIT_FROZEN", "Cannot start a command for a sealed or published unit")
        self.refuse_recording(command)
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
                    from .lead_calls import active, describe
                    lead_pending = [r for item in proposed for r in active(self.store, item["checkout"])
                                    if not r.get("paths") or effect_conflict(item, path_effect(
                                        self.store.get(item["checkout"]), r["paths"], r["id"]))]
                    if not conflicts and not pending and not lead_pending:
                        if process_baseline is None:
                            process_baseline = await finish_thread(baseline)
                            continue  # Recheck every checkout after the asynchronous collection.
                        break
                    process_baseline = None
                    left = deadline - asyncio.get_running_loop().time()
                    if left <= 0:
                        request.update(state="denied", holders=[r["unit"] for r in conflicts])
                        self.store.save(request)
                        raise CoordinatorError("CUSTODY_WAIT", "Custody wait reached the safe hook deadline; retry or message the holder",
                                               {"holders": [r["unit"] for r in conflicts],
                                                "operations": [o["id"] for o in pending],
                                                "lead_edits": [describe(r) for r in lead_pending]})
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
                    # The process table the command is checked against is its own record, written once for the group
                    # (process_baseline). Kept in each member (89 KB on a probe), it was written again with each of
                    # their saves while held: 5.8 of the 7.6 MB a run of ten commands wrote.
                    self.store.put("custody_baseline", baseline_id(ident), {"group": ident, "processes": process_baseline},
                                   actor["unit"])
                    for index, item in enumerate(proposed):
                        key = ident if index == 0 else uid("custody")
                        ids.append(key)
                        self.store.put("custody", key, dict(item, unit=actor["unit"], actor=actor["id"], command=command,
                                       group=ident, state="held", created=time.time(), terminals=[], processes=[],
                                       generation=self.store.generation), actor["unit"])
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
        group = reservation.get("group", reservation["id"])
        root = self.store.get(group, required=False) or reservation
        if all(self.store.get(key)["state"] == "released" for key in root.get("members", [root["id"]])):
            baseline = self.store.get(baseline_id(group), required=False)
            if baseline and baseline["processes"]:
                baseline["processes"] = []  # the group's last member released: no window is checked against it
                self.store.save(baseline)
        self.store.event("custody/released", reservation["id"], {})

    def process_baseline(self, reservation):
        """The process table a held command is checked against: its group's record (baseline_id), or the member's own
        process_baseline for custody granted through 2.4.22."""
        if "process_baseline" in reservation:
            return reservation["process_baseline"] or []
        record = self.store.get(baseline_id(reservation.get("group", reservation["id"])), required=False)
        return (record or {}).get("processes") or []
