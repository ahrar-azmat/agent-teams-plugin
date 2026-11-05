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


class ShellWord(str):
    """A decoded word, with the characters the shell may still interpret as pattern syntax."""
    def __new__(cls, value, active, qualifier=False, regrouped=False):
        word = str.__new__(cls, value)
        word.active = tuple(active)
        word.qualifier = qualifier
        word.regrouped = regrouped
        return word


ZSH_PATTERN = "a zsh glob qualifier or pattern group"
NUMERIC_GLOB = re.compile(r"<[0-9]*-[0-9]*>")
SAFE_QUALIFIER = re.compile(r"\([ND./@-]+\)(?=$|[\s;|&<>)])")
SHELL_LITERAL = re.compile(r"[^\s'\"\\`$#<>;|&()]+")
SHELL_PATTERN_WORD = re.compile(r"(?:[^\s'\"\\`$#<>;|&()]+|<[0-9]*-[0-9]*>)+")


def shell_words(command, patterns=True):
    """Small conservative lexer; retain quoted punctuation as ordinary arguments.

    Yields (value, kind): kind False for a word, True for a control operator, "redirect" for a
    redirection operator, then what it reads or writes: a "target" (a file or a descriptor), a
    "herestring", or a "heredoc" ({"body", "quoted"}; the body is read from the lines after its
    command line). A descriptor number in front of an operator is no argument. Expansions and
    unsupported grammar raise ValueError naming the construct, rather than pretending this is a
    complete shell parser. No shell text is ever evaluated here.
    """
    tokens, word, active, present, quoted, quote, i = [], [], [], False, False, None, 0
    qualifier, argument, regrouped = False, False, False
    pending, target = [], None  # heredocs whose lines are still to come; the redirection awaiting its word
    def flush():
        nonlocal word, active, present, quoted, target, qualifier, argument, regrouped
        if present:
            value = ShellWord("".join(word), active, qualifier, regrouped)
            if target in ("<<", "<<-"):
                heredoc = {"delimiter": value, "strip": target == "<<-", "quoted": quoted, "body": None}
                pending.append(heredoc)
                tokens.append((heredoc, "heredoc"))
            elif target:
                tokens.append((value, "herestring" if target == "<<<" else "target"))
            else:
                tokens.append((value, False))
                if value not in ("!", "if", "then", "elif", "else", "while", "until", "do", "{", "coproc") and not re.fullmatch(
                        r"[A-Za-z_][A-Za-z_0-9]*=.*", value):
                    argument = True
            target = None
        word, active, present, quoted, qualifier = [], [], False, False, False
        regrouped = False
    while i < len(command):
        char = command[i]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                word.append(char)
                active.append(False)
        elif char == "\\" and quote != "'":
            i += 1
            if i == len(command):
                raise ValueError("a trailing escape")
            escaped = command[i]
            if escaped != "\n":  # An escaped newline only joins two lines: it quotes nothing and makes no word.
                if quote == '"' and escaped not in '$`"\\':
                    word.append("\\")
                    active.append(False)
                word.append(escaped)
                active.append(False)
                present = quoted = True
        elif char == quote:
            quote = None
        elif char == "`" or char == "$" and command[i + 1:i + 2] == "(":
            raise ValueError("arithmetic expansion" if command.startswith("$((", i) else "command substitution")
        elif char == "$":
            raise ValueError("parameter expansion")
        elif quote:
            word.append(char)
            active.append(False)
        elif char in "'\"":
            quote, present, quoted = char, True, True
        elif char == "#" and not present:
            end = command.find("\n", i)
            i = len(command) if end < 0 else end - 1
        elif patterns and char == "<" and NUMERIC_GLOB.match(command, i):
            # zsh's numeric glob is one word, not two redirections (including at its start).
            numeric = SHELL_PATTERN_WORD.match(command, i)[0]
            word.extend(numeric)
            active.extend([True] * len(numeric))
            present, regrouped = True, True
            i += len(numeric) - 1
        elif char in "<>" or command.startswith("&>", i):
            if char in "<>" and command[i + 1:i + 2] == "(":
                raise ValueError("process substitution")
            if target and not present:
                raise ValueError("a redirection without its target")
            if char in "<>" and present and not quoted and not target and re.fullmatch(
                    r"[0-9]+|\{[A-Za-z_][A-Za-z_0-9]*\}", "".join(word)):
                word, active, present = [], [], False  # A descriptor: the 2 of 2>&1, or the {name} that is to hold a new one.
            else:
                flush()
            operator = ("&>>" if command.startswith("&>>", i) else "&>") if char == "&" else next(
                o for o in REDIRECTIONS if command.startswith(o, i))
            tokens.append((operator, "redirect"))
            target = operator
            i += len(operator) - 1
        elif patterns and target not in ("<<<", "<<", "<<-") and char == "(" and (present or argument or target):
            # A leading group in an argument is a pattern too: `printf %s (cat|ls)(e:code:)`.
            # Only one trailing, unquoted, selection-only qualifier is understood. Strip it for
            # the name check, but retain that this word expands even without a `*` or a `?`.
            safe = SAFE_QUALIFIER.match(command, i)
            if not present or qualifier or not safe or word and word[-1] in "@+!*?":
                raise ValueError(ZSH_PATTERN)
            qualifier = True
            i += len(safe[0]) - 1
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
            argument = False
            if char == "\n" and pending:
                i = heredoc_bodies(command, i + 1, pending) - 1
                del pending[:]
        elif char.isspace():
            flush()
        else:
            # Consume ordinary runs together: metadata stays linear even for a huge word.
            run = (SHELL_PATTERN_WORD if patterns else SHELL_LITERAL).match(command, i)
            text = run[0] if run else char
            word.extend(text)
            active.extend([True] * len(text))
            regrouped = regrouped or "<" in text
            i += len(text) - 1
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
NESTED = "commands nested more than 16 deep"
SHELLS = ("sh", "bash", "dash", "zsh", "ksh")
SHELL_OPTIONS = ("--noprofile", "--norc", "-l")
# `git` as a word of its own: a path's last part, a shell word, an option's value, a call, or a command inside text
# that an inner shell may run (`sh -c "cd x&&git commit"`, `{git,commit}`); a word naming a dashed Git program
# (git-receive-pack); or Git's own program directory (libexec/git-core/git-commit). In any letter case: on the
# default case-insensitive macOS file system `nice /usr/bin/GIT commit` runs git (measured 11-02).
GIT_WORD = re.compile(r"(?:^|[\s/'\"`;|&(){},=\\!<>])git(?:$|[\s'\"`;|&(){},<>])|(?:^|\s)git-[a-z]|/git-core/", re.IGNORECASE)
# The system's own tool directories (read-only on macOS): a tool there is the one its name says.
SYSTEM_TOOL = re.compile(r"/(?:usr/)?bin/[^/]+")
# A closed list of read-only tools that never execute their arguments or their input.
DATA_ONLY = {"grep", "rg", "cat", "head", "tail", "wc", "ls", "stat", "file", "diff", "cmp", "sort", "uniq", "cut", "tr",
             "shasum", "sha256sum"}


def command_pattern(word):
    """The old command-position pattern rule, without a failing regex retry at every opener."""
    if "*" in word or "?" in word:
        return True
    for line in word.split("\n"):
        start = line.find("[")
        if start >= 0 and line.rfind("]") > start:
            return True
        start, end = line.find("{"), line.rfind("}")
        if start >= 0 and end > start and (line.find(",", start, end) >= 0 or line.find("..", start, end) >= 0):
            return True
    return False


def active_pattern(word):
    """Whether an unquoted character of word may make the shell expand it (zsh's operators, a numeric glob, a
    qualifier): what the word then names is not established by its letters."""
    active = getattr(word, "active", ())
    return bool(getattr(word, "qualifier", False)) or any(
        on and (char in "*?[{#^<" or char == "~" and i) for i, (char, on) in enumerate(zip(word, active)))


POSIX_CLASS = re.compile(r"\[:[a-z]+:\]")
BRACE_SEQUENCE = re.compile(r"(-?[0-9]+|[A-Za-z])\.\.(-?[0-9]+|[A-Za-z])(?:\.\.(-?[0-9]+))?")


def pattern_class_ends(word, active):
    """Read disjoint classes once; an unfinished '[' never rescans the rest of the word. None when a bracket holds an
    equivalence class or a collating symbol (`[[=g=]]`, `[[.g.]]`): bash 3.2 reads them and zsh 5.9 does not, so the two
    shells close that bracket at different places (measured: bash matches `[[=g=]]it` to git, zsh `[[=g=]it`)."""
    ends, i = {}, 0
    if "]" not in word:
        return ends
    while i < len(word):
        start = word.find("[", i)
        if start < 0:
            break
        i = start + 1
        if not active[start]:
            continue
        if i < len(word) and active[i] and word[i] in "!^":
            i += 1
        if i < len(word) and word[i] == "]":
            i += 1
        while i < len(word):
            if active[i] and word[i] == "]":
                ends[start] = i
                i += 1
                break
            if active[i] and word.startswith("[:", i):
                match = POSIX_CLASS.match(word, i)
                if match and all(active[i:match.end()]):
                    i = match.end()
                    continue
            if active[i] and word.startswith(("[=", "[."), i) and active[i + 1]:
                return None
            i += 1
    return ends


# git_pattern's fixed relations, built once: a relation maps each of its seven states to the set of states (bits) that
# one fragment reaches. Every character outside ASCII reads as `.`: the only one whose lower case is an ASCII name
# character is the Kelvin sign, as `k`, which is no Git letter (measured over every code point, 3.9 and 3.15).
NAME_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
RELATION_IDENTITY = (1, 2, 4, 8, 16, 32, 64)
RELATION_EMPTY = (0,) * 7


def literal_relation(char):
    lower = char.lower()
    return (1,) * 7 if char == "/" else (
        2 if lower == "g" else 64, 4 if lower == "i" else 64,
        8 if lower == "t" else 64, 16 if char == "-" else 64,
        32 if char in NAME_ALPHABET else 64, 32 if char in NAME_ALPHABET else 64, 64)


def relation_union(left, right):
    return tuple(a | b for a, b in zip(left, right))


def relation_closure(part):
    result = relation_union(RELATION_IDENTITY, part)
    # Reachability in a seven-vertex graph; always exactly seven bounded passes.
    for k in range(7):
        result = tuple(row | result[k] if row & (1 << k) else row for row in result)
    return result


def any_char_relation():
    result = RELATION_LITERALS["."]
    for char in NAME_ALPHABET:
        result = relation_union(result, RELATION_LITERALS[char])
    return result


RELATION_LITERALS = {chr(code): literal_relation(chr(code)) for code in range(128)}
RELATION_OTHER = RELATION_LITERALS["."]
RELATION_ANY_CHAR = any_char_relation()
RELATION_STAR = relation_closure(RELATION_ANY_CHAR)
CLASS_PREDICATES = {"alnum": str.isalnum, "alpha": str.isalpha, "ascii": str.isascii,
                    "blank": lambda c: c in " \t", "cntrl": lambda c: ord(c) < 32 or ord(c) == 127,
                    "digit": str.isdigit, "graph": lambda c: c.isprintable() and not c.isspace(),
                    "lower": str.islower, "print": str.isprintable,
                    "punct": lambda c: not c.isalnum() and not c.isspace(), "space": str.isspace,
                    "upper": str.isupper, "word": lambda c: c.isalnum() or c == "_",
                    "xdigit": lambda c: c in "0123456789abcdefABCDEF"}


def git_pattern(word):
    """Read a pattern through a fixed seven-state Git-basename recognizer.

    Each fragment is a relation between the states. Composition, union and repetition cost a
    fixed amount; neither brace products nor regex derivative states are enumerated. Quoting
    remains per character. A slash resets the basename, even when supplied by a brace branch.
    Exclusions never remove a name here; a complement may select any component. This deliberate
    over-approximation keeps admission linear and independent of filenames and login locales.
    """
    active = getattr(word, "active", None)
    if active is None:
        return False
    patterned = bool(getattr(word, "qualifier", False))
    possible = ("*" in word or "?" in word or "[" in word and "]" in word or
                "{" in word and "}" in word or "#" in word or "^" in word or
                "~" in word[1:] or "<" in word)
    if not patterned and not possible:
        return False
    if not patterned and not any(active):
        return False
    classes = pattern_class_ends(word, active)
    if classes is None:
        return True  # Which bracket the shell closes is not established (pattern_class_ends).
    braces, stack = {}, []
    if "}" in word:
        for i, char in enumerate(word):
            if active[i] and char == "{":
                stack.append(i)
            elif active[i] and char == "}" and stack:
                braces[stack.pop()] = i
    identity, empty, alphabet = RELATION_IDENTITY, RELATION_EMPTY, NAME_ALPHABET
    products = {}
    def literal(char):
        return RELATION_LITERALS.get(char, RELATION_OTHER)
    def compose(left, right):
        if left == identity:
            return right
        if right == identity:
            return left
        key = (left, right)
        if key not in products:
            result = []
            for states in left:
                dest = 0
                while states:
                    bit = states & -states
                    dest |= right[bit.bit_length() - 1]
                    states -= bit
                result.append(dest)
            products[key] = tuple(result)
        return products[key]
    union, closure = relation_union, relation_closure
    any_char, star, predicates = RELATION_ANY_CHAR, RELATION_STAR, CLASS_PREDICATES
    def character_class(start, end):
        i = start + 1
        negate = active[i] and word[i] in "!^"
        i += bool(negate)
        selected = set()
        while i < end:
            match = POSIX_CLASS.match(word, i) if active[i] and word.startswith("[:", i) else None
            predicate = predicates.get(match[0][2:-2]) if match else None
            if predicate and all(active[i:match.end()]):
                selected.update(c for c in alphabet if predicate(c))
                i = match.end()
            elif i + 2 < end and active[i + 1] and word[i + 1] == "-":
                selected.update(c for c in alphabet if word[i] <= c <= word[i + 2])
                i += 3
            else:
                selected.add(word[i])
                i += 1
        # Other characters may occur in directories, including Unicode. No wildcard matches /.
        result = literal(".")
        for c in alphabet:
            if (c not in selected) if negate else (c in selected):
                result = union(result, literal(c))
        return result
    def sequence(match):
        first, last, stride = match.groups()
        if first.isalpha() and last.isalpha():
            # Only 58 possible ASCII endpoints: a long stride need not become a huge integer.
            digits = (stride or "1").lstrip("-0")
            step = int(digits) if len(digits) <= 2 and digits else 1
            step = step if len(digits) <= 2 else 128
            a, b = ord(first), ord(last)
            values = range(a, b + (1 if a <= b else -1), step if a <= b else -step)
            result = empty
            for value in values:
                char = chr(value)
                if char in "[]^\\":  # Brace expansion can synthesize glob grammar.
                    return None
                result = union(result, literal(char))
            return result
        if first.isalpha() or last.isalpha():
            return False
        # All nonnegative decimal strings have the same relation, as do all negative ones.
        # Do not convert arbitrary-size integers or enumerate a sequence/stride product.
        result = literal("0") if not first.startswith("-") or not last.startswith("-") else empty
        if first.startswith("-") or last.startswith("-"):
            result = union(result, compose(literal("-"), literal("0")))
        return result
    # Frame: concatenation before its last atom, last atom, alternatives, opening offset,
    # whether it has a nested brace. Keeping the last atom separate makes #/## constant-time.
    frames = [[identity, identity, empty, -1, False]]
    i = 0
    while i < len(word):
        char, frame = word[i], frames[-1]
        part = None
        if active[i] and char == "{" and i in braces:
            frame[4] = True
            frames.append([identity, identity, empty, i, False])
        elif active[i] and char == "," and len(frames) > 1:
            frame[2] = union(frame[2], compose(frame[0], frame[1]))
            frame[0], frame[1] = identity, identity
            patterned = True
        elif active[i] and char == "}" and len(frames) > 1 and braces[frame[3]] == i:
            before, last, branches, start, nested = frames.pop()
            inner = compose(before, last)
            if branches != empty:
                part = union(branches, inner)
            else:
                match = BRACE_SEQUENCE.fullmatch(word, start + 1, i) if not nested else None
                if match and all(active[start:i + 1]):
                    part = sequence(match)
                    if part is None:
                        return True
                    patterned = patterned or part is not False
                if part is None or part is False:
                    part = compose(compose(literal("{"), inner), literal("}"))
            frame = frames[-1]
        elif active[i] and char in "#^~" and (char != "~" or i):
            if braces:
                return True  # An expansion can splice operators across its branch boundaries.
            patterned = True
            if char == "~":
                break  # An exclusion can only remove filenames. Never use it to prove safety.
            if char == "^":
                part = star
                end = word.find("/", i + 1)
                i = len(word) - 1 if end < 0 else end - 1
            else:
                repeat = closure(frame[1])
                if i + 1 < len(word) and active[i + 1] and word[i + 1] == "#":
                    repeat = compose(frame[1], repeat)
                    i += 1
                frame[1] = repeat
        elif active[i] and char in "*?":
            part = star if char == "*" else any_char
            patterned = True
        elif active[i] and i in classes:
            end = classes[i]
            if braces and any(active[j] and word[j] in "{}" for j in range(i, end + 1)):
                return True  # E.g. g{[,x}i{],y}t expands to the active class g[i]t.
            part = character_class(i, end)
            i, patterned = end, True
        elif active[i] and char == "<":
            match = NUMERIC_GLOB.match(word, i)
            if match and all(active[i:match.end()]):
                if braces:
                    return True
                low, high = match[0][1:-1].split("-")
                a, b = low.lstrip("0") or "0", high.lstrip("0") or "0"
                part = empty if high and (len(a), a) > (len(b), b) else literal("0")
                i, patterned = match.end() - 1, True
            else:
                part = literal(char)
        else:
            part = literal(char)
        if part is not None:
            frame[0], frame[1] = compose(frame[0], frame[1]), part
        i += 1
    result = compose(frames[0][0], frames[0][1])
    return patterned and bool(result[0] & (8 | 32))


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


def invocation(words, depth, stdin=(), lookup=True, patterns=True, git_only=False):
    """Why this simple command must be refused (a reason naming it, or LEAD_PUBLISHES); False when admitted.
    lookup is False once something in front of the command (an assignment, an env option) may change what its name
    finds."""
    if depth > 16:  # Wrappers nest here as shells do in _recording_command (5,000 `command` raised RecursionError).
        return NESTED
    # A plain `git` refuses whatever its environment, and the one accepted form starts with
    # `/usr/bin/env -i`, which clears it: assignments in front cannot make Git out of another
    # command. Refusing them refused `PYTHONPATH=. python -m …` as Git execution (measured, 2.3.0).
    # Except the ones that make a shell or interpreter run code this reading never sees.
    # Keywords and assignments may follow each other (`if X=1 cmd`): neither is the command.
    # An index, not a slice per word: 25,000 leading assignments took 0.71 s on the event loop.
    start = 0
    while start < len(words) and (words[start] in ("!", "if", "then", "elif", "else", "while", "until", "do", "{", "coproc")
                                  or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", words[start])):
        if "=" in words[start]:
            if words[start].split("=", 1)[0] in STARTUP_CODE:
                return "the startup-code variable " + words[start].split("=", 1)[0]
            lookup = False
        start += 1
    words = words[start:]
    if not words or words in (["}"], ["done"], ["fi"], ["esac"]):
        # A heredoc after `)`, `}`, done, fi or esac is the standard input of every command the group runs.
        return stdin_refusal("a command group", stdin)
    if (command_pattern(words[0]) or getattr(words[0], "qualifier", False) or patterns and git_pattern(words[0])) and (not git_only or git_pattern(words[0])):
        return "a pattern (glob or brace expansion) in the command position"
    # Where only a pattern naming Git refuses here (git_only), the word may still name a shell: perl … /bin/s[h] -c ….
    if patterns and active_pattern(words[0]) and shell_text(words, 0, depth, lookup):
        return "a pattern (glob or brace expansion) in the command position"
    # What is refused matches in any case: on the default case-insensitive macOS file system, GIT finds and runs git.
    # What is trusted must be spelled exactly: on a case-sensitive one, Cat need not be cat.
    # zsh EQUALS performs path lookup before invocation. Only an active leading '=' expands;
    # a quoted/escaped one remains a literal program name. The resolved path gains no trust.
    equals = patterns and words[0].startswith("=") and bool(getattr(words[0], "active", (False,))[0])
    name = (words[0][1:] if equals else words[0]).rsplit("/", 1)[-1]
    executable = name.lower()
    exact = name == executable and not equals
    by_name = exact and lookup and "/" not in words[0]  # The shell's own lookup of this name, nothing in front changing it.
    # Before any answer that admits: the shell runs what a heredoc expands before this command starts.
    expanded = heredoc_expansion(executable, stdin)
    if expanded:
        return expanded
    if executable == "git" or executable.startswith("git-"):
        return (equals or executable != "git" or git_invocation(words)) and GIT_COMMAND
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
            at = 1 if isolated else 0  # An index, not a slice per word (see above).
            while not isolated and at < len(rest) and (rest[at] in ("-u", "--unset") and at + 1 < len(rest) or re.fullmatch(
                    r"(?:-u|--unset=)[A-Za-z_][A-Za-z_0-9]*", rest[at])):
                at += 2 if rest[at] in ("-u", "--unset") else 1  # Unsetting a variable runs nothing.
                lookup = False
            while at < len(rest) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", rest[at]):
                if rest[at].split("=", 1)[0] in STARTUP_CODE:
                    return "the startup-code variable " + rest[at].split("=", 1)[0]
                assignments.append(rest[at])
                at += 1
                lookup = False
            rest = rest[at:]
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
            at = 0
            while at < len(rest) and rest[at] in ("--", "-p"):
                at += 1
            rest = rest[at:]
            if executable == "command" and rest and re.fullmatch(r"-[pvV]+", rest[0]) and set(rest[0]) & set("vV"):
                return False  # command -v/-V only says what a name would run.
            if rest and rest[0].startswith("-"):
                return ("an " if executable == "exec" else "a ") + executable + " option this reading cannot establish"
        # Without a command, each of them runs nothing; exec only applies its redirections, and the commands after it
        # read a heredoc it gives the shell.
        return invocation(rest, depth + 1, stdin, lookup, patterns, git_only) if rest else stdin_refusal(executable, stdin)
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
                return _recording_command(rest[i + 1], depth + 1, lookup, patterns, git_only) or stdin_refusal(executable, stdin)
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
    # The census uses precisely this timeout wrapper. With more than one ARGV element Perl
    # calls execvp directly; a single element can instead become shell text, so is not trusted.
    # Keep the old literal-mention check above this narrow contract (no old refusal is relaxed).
    if patterns and executable == "perl" and (by_name or system) and len(words) >= 5 and words[1] == "-e" and re.fullmatch(
            r"alarm [0-9]+; exec @ARGV", words[2]) and not words[3].startswith(("-", "+")):
        reason = invocation(words[3:], depth + 1, stdin, lookup, patterns, True)
        if forwards(reason):
            return reason
        # An old refusal may precede the new argument check (xargs, a shell script, rg
        # --pre, sort --compress-program). Ignoring that old refusal must not trust its
        # pattern arguments, nor -c text it may give a shell (=sh, SH). A False result,
        # e.g. printf, command -v or sh -n, really is data-only: it forwards nothing.
        if reason and any(git_pattern(arg) for arg in words[4:]):
            return words[3].rsplit("/", 1)[-1].lower() + " with a pattern argument that may name a Git program"
        return reason and forwarded_shell(executable, words, 3, depth, lookup) or stdin_refusal(executable, stdin)
    if patterns and any(git_pattern(arg) for arg in words[1:]):
        return executable + " with a pattern argument that may name a Git program"
    if patterns:
        return forwarded_shell(executable, words, 1, depth, lookup) or stdin_refusal(executable, stdin)
    return stdin_refusal(executable, stdin)


def forwards(reason):
    """Whether refusing text that another program hands on refuses that program too: the text may run Git or
    publish, names Git by a pattern, or was not read to its end (nested too deep). A refusal only of code this
    reading never sees (a script, eval, an unestablished option) stays one of the documented limits."""
    return reason in (ZSH_PATTERN, NESTED, GIT_COMMAND, "LEAD_PUBLISHES", "a pattern (glob or brace expansion) in the command position") or isinstance(
        reason, str) and reason.endswith((" with a pattern argument that may name a Git program", MENTIONS_GIT))


def brace_alternatives(word):
    """The alternatives of word's first unquoted brace expansion with commas (`{-c,'…'}`), without what surrounds
    it: each becomes a word of its own."""
    if "{" not in word or "," not in word:
        return []
    active, depth, start, parts = getattr(word, "active", ()), 0, 0, []
    for i, (char, on) in enumerate(zip(word, active)):
        if on and char == "{":
            depth += 1
            if depth == 1:
                start, parts = i + 1, []
        elif on and char == "," and depth == 1:
            parts.append(word[start:i])
            start = i + 1
        elif on and char == "}" and depth:
            depth -= 1
            if not depth and parts:
                return parts + [word[start:i]]
    return []


def shell_text(words, i, depth, lookup, scan=None):
    """The refusal (forwards) of the -c text that words[i] runs if it is a shell, else False.

    The outer shell does not expand quoted -c text, but the inner shell does. A shell is found by its name's last part
    in any letter case, after an active zsh `=`: `nice =sh -c …` and `nice SH -c …` run sh on the default
    case-insensitive file system (measured). A word with an active pattern may name a shell, or its -c, too
    (`nice /bin/s[h] -c …` runs /bin/sh), so it counts as either. A brace expansion may supply an option among its
    alternatives, and then a shell's -c and its text too (`nice sh {-c,'…'}`, `nice {/bin/sh,-c} '…'`), so the
    alternatives after the first that may be an option, and the word after the brace, are read as text. Among another
    program's arguments (scan: where each run of shell options ends, and what was read), each word is read as sh's
    text at most once, and each option position is judged once."""
    ends, read = scan or (None, {})
    def text(k):
        if k not in read:
            reason = invocation(["sh", "-c", words[k]], depth + 1, (), lookup, True, True)
            read[k] = reason if forwards(reason) else False
        return read[k]
    parts = brace_alternatives(words[i])
    cut = next((j for j, part in enumerate(parts) if part.startswith("-") and (set(part[1:]) & set("*?[") or "c" in part[1:]
                                                                              and set(part[1:]) <= set("celuxv"))), None)
    if cut is not None:
        for part in parts[cut + 1:]:
            reason = invocation(["sh", "-c", part], depth + 1, (), lookup, True, True)
            if forwards(reason):
                return reason
        if i + 1 < len(words) and text(i + 1):
            return text(i + 1)
    word = words[i]
    if word.startswith("=") and getattr(word, "active", (False,))[0]:
        word = word[1:]
    if word.rsplit("/", 1)[-1].lower() not in SHELLS and not active_pattern(words[i]):
        return False
    end = ends[i + 1] if ends else next((k for k in range(i + 1, len(words)) if words[k] not in SHELL_OPTIONS), len(words))
    if ("option", end) not in read:
        option = words[end] if end < len(words) else ""
        read[("option", end)] = end + 1 < len(words) and (active_pattern(option) or option.startswith("-") and "c" in option[1:]
                                                           and set(option[1:]) <= set("celuxv"))
    # Only -c text is read: copying every remaining argument for each possible shell would make a wrapper quadratic.
    return read[("option", end)] and text(end + 1)


def forwarded_shell(executable, words, start, depth, lookup):
    """The refusal for -c text that a shell among words[start:] would run (shell_text), else False: an unknown wrapper,
    or perl's exec @ARGV, can hand a shell quoted -c text. Script files and other opaque code remain the existing
    documented limits."""
    ends = list(range(len(words) + 1))  # where the run of shell options from each word ends, found once
    for k in range(len(words) - 1, -1, -1):
        if words[k] in SHELL_OPTIONS:
            ends[k] = ends[k + 1]
    scan = (ends, {})
    for i in range(start, len(words)):
        reason = shell_text(words, i, depth, lookup, scan)
        if reason == "a pattern (glob or brace expansion) in the command position" or isinstance(reason, str) and reason.endswith(
                " with a pattern argument that may name a Git program"):
            return executable + " with a pattern argument that may name a Git program"
        if reason:
            return reason
    return False


def _recording_command(command, depth=0, lookup=True, patterns=True, git_only=False):
    """Why the command must be refused (a reason naming it, or LEAD_PUBLISHES); False when admitted. lookup is
    False for a shell's command text run with assignments in front of that shell (see invocation)."""
    if not isinstance(command, str) or not command:
        return "an empty command"
    if depth > 16:
        return NESTED
    try:
        tokens = shell_words(command, patterns)
    except ValueError as exc:
        return str(exc)
    words, stdin = [], []
    for value, kind in tokens + [(";", True)]:
        if kind is True:
            refused = invocation(words, depth, stdin, lookup, patterns, git_only)
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
    # Recognizing a selection-only qualifier joins what the old lexer split into commands.
    # Keep any old refusal as well: e.g. `echo x(N) git` must not become newly admitted.
    if patterns and any(getattr(value, "qualifier", False) or getattr(value, "regrouped", False) for value, _ in tokens):
        return _recording_command(command, depth, lookup, False, git_only)
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
            # Admission could have begun while this release waited for the condition. Every one not settled is named.
            from .publish import SETTLED, unsettled
            blocking = [operation for operation in self.store.all("operation", unit["id"]) if operation["state"] not in SETTLED]
            if blocking:
                raise unsettled(blocking, "Cannot release an unreconciled operation's claim")
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
