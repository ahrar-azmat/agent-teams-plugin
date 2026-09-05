#!/usr/bin/env python3
"""PreToolUse gate on Bash git push/commit. Reads the hook payload from
stdin; every detected git push/commit is DENIED with the review state in
the reason — ALWAYS (design ruling 2026-09-02: static classification of
arbitrary shell text cannot be complete, so nothing auto-opens) — and the
reason carries a ONE-SHOT ACKNOWLEDGEMENT TOKEN bound to this exact
command and tree digest. Re-running the same command with
`CODEX_PUSH_ACK=<token>` in front consumes the token and lets the command
proceed under the session's normal permissions. "deny" rather than "ask"
(round 30): a hook "ask" is honoured only in some host permission modes
(upstream reproduced auto-mode auto-approval, claude-code#51255; measured
here on 2.1.257: `claude -p` manual mode blocks on both), while "deny" is
authoritative in every mode — the gate's effect no longer depends on the
mode the session runs in. The transcript's review state only chooses the
WORDING — "REVIEW VERIFIED" for a completed digest-matching review of a
LONE push/commit of the reviewed HEAD, stale/pending/absent/compound
otherwise — so a parsing gap can cost a sentence, never a silent bypass.
Fail-open only for a malformed payload (never block the call machinery
itself). An unusable token store is a DENY with the reason (round 32: a
downgrade to "ask" would vanish silently on hosts that auto-approve it).
The evaluation runs in a WORKER process group under a hard deadline held
by the parent hook process, which does nothing blocking itself and DENIES
on timeout — a hook that outlives the host's timeout fails OPEN, so the
only safe place for the bound is a process that cannot be blocked;
hooks.json declares a 90 s hook timeout above the worker's 60 s.

Answer verification is a SIGNATURE, not shape-sniffing: server.py stamps
every answer header with `tool:<name> | status:<ok|timeout> | tree:<digest>`
(see `_answer_sig`). The VERIFIED wording appears only for a header
carrying `tool:code_review | status:ok` whose tree digest matches the
workspace at push time. Consequences, each pinned by tests/test_push_gate.py:
  - another tool's answer (codex_query etc.) carries its own tool name and
    never yields the VERIFIED wording, even if its text mentions code_review;
  - a TIMEOUT partial carries status:timeout and never opens the gate;
  - a review of an OLDER tree fails the digest compare — edits after the
    answer mean the review no longer vouches for this push (stale → ask);
  - reading this source file (or any file) into the transcript cannot forge
    a passing header for the current tree;
  - pre-signature answers (older server still running before /mcp
    Reconnect) fail verification, so the gate asks — the safe direction.

Command detection is deliberately over-broad (`git … push|commit` anywhere
in one shell segment): a false positive costs one extra permission prompt,
a false negative silently skips the gate.

TRUST MODEL — a guardrail against FORGETTING, not a security boundary.
This hook protects a cooperative agent from the accident of pushing
without a review, on a stale review, or on a failed/timed-out one. It
does NOT defend against a deliberately evasive agent: any Bash-text hook
is bypassable by construction (write a script and execute it, shell
functions, aliases), so a keyed signature would add ceremony, not
security — the header check is honesty-against-accident. Two disclosed
residuals: the digest is taken at DISPATCH time (server `_answer_sig`),
so edits made while the review runs are NOT in it — the review vouches
for the dispatched tree, and a push of the edited tree reads STALE; and
the acknowledgement token is minted by this hook and shown to the agent,
so it is an explicit "I read the gate's decision" — a speed bump for a
cooperative agent, not a secret (the same trust model as the rest of the
hook). Token store: ~/.claude/logs/codex-oracle/push-ack (override:
CODEX_PUSH_ACK_DIR), 0600 files, 10-minute TTL, consumed on first read.
"""
import hashlib
import os
import stat as stat_mod
import json
import re
import secrets
import subprocess
import sys
import threading
import time

GATE_REASON = (
    "PUSH GATE: no completed Codex review found for the current tree. Run "
    "Codex `code_review` on the diff first — dispatched BLIND (send the "
    "DIFF, keep context/focus factual, put any belief of your own in "
    "caller_hypothesis for an explicit CONFIRMED/REFUTED/UNPROVEN verdict) "
    "— then push. Acknowledge (below) only to explicitly skip the review."
)

CODEX_PENDING_REASON = (
    "PUSH GATE: Codex's review was dispatched but has NOT RETURNED. Codex "
    "runs at max reasoning, so long calls are backgrounded and come back "
    "later as a task notification; that is normal, not a reason to proceed. "
    "Wait for the result and fold its findings in before pushing. "
    "Acknowledge (below) only to explicitly skip waiting for the review."
)

STALE_REASON = (
    "PUSH GATE: the completed Codex review is STALE — the workspace changed "
    "after Codex answered (tree digest mismatch), so that review no longer "
    "vouches for what is being pushed. Re-run `code_review` on the current "
    "diff. Acknowledge (below) only to explicitly push unreviewed changes."
)

VERIFIED_REASON = (
    "PUSH GATE: REVIEW VERIFIED — a completed Codex code_review matches "
    "this exact tree (digest {digest}), the index and HEAD carry that same "
    "content, and this is a lone push/commit of it. Acknowledge (below) to "
    "proceed."
)

VERIFIED_BUT_REASON = (
    "PUSH GATE: a completed Codex review matches this tree (digest "
    "{digest}), BUT a review can only vouch for a lone `git push` of the "
    "reviewed HEAD or a lone `git commit` of this workspace whose index and "
    "HEAD carry the reviewed bytes."
)

INDEX_NOTE_PUSH = (
    " (Note: a push carries HEAD's tree, and the objects it would record are "
    "not provably the reviewed bytes at {n} path(s): {sample} — the review "
    "vouches for the worktree bytes it saw.)"
)

INDEX_NOTE_COMMIT = (
    " (Note: a commit records the INDEX, which is not provably the reviewed "
    "worktree at {n} path(s): {sample} — those bytes are not what Codex "
    "reviewed.)"
)

INDEX_UNKNOWN_NOTE = (
    " (Note: whether the index and HEAD carry the reviewed content could not "
    "be established ({why}); the review vouches for the worktree bytes only.)"
)

RANGE_NOTE_AHEAD = (
    " (Note: the push carries {n} commits on top of {tracking}; the review "
    "vouches for the FINAL tree only — not for what the intermediate commits "
    "added and removed along the way.)"
)

RANGE_NOTE_BEHIND = (
    " (Note: {tracking} has {n} commit(s) that HEAD lacks — a plain push would "
    "be rejected, a forced push would discard them.)"
)

RANGE_UNKNOWN_NOTE = (
    " (Note: the commit range this push transfers could not be established "
    "({why}); the review vouches for the final tree only.)"
)

HOOK_NOTE = (
    " (Note: a {event} hook is active ({names}) and runs during this command "
    "— {effect}; {fix}.)"
)

LEASE_NOTE = (
    " (Note: the transferred range is measured against {tracking} as of the "
    "LAST FETCH ({oid}); the remote's current tip is unknown to this hook, so "
    "a push that is not bound to that tip cannot carry the full wording — "
    "`git push --force-with-lease=refs/heads/{dst}:{oid} {remote} {dst}` — the "
    "lease spelled exactly like the destination and first on the command line, "
    "so it reaches every ref git could resolve the destination to — makes git "
    "refuse the push if the remote moved, and earns it.)"
)

SIGNING_NOTE = (
    " (Note: {key} is set, so git runs the signing program ({program}) during "
    "this command — code this inspection cannot vouch for.)"
)

PAYLOAD_REASON = (
    "PUSH GATE: the hook's input could not be read safely ({why}), so the "
    "command was NOT run and no acknowledgement token exists for it."
)

REPO_PROGRAM_NOTE = (
    " (Note: the repository's own configuration names a program or rewrites "
    "an endpoint ({keys}) — code the command would run that this inspection "
    "cannot vouch for; move it to your global configuration or acknowledge "
    "knowingly.)"
)

ENV_ROUTED_NOTE = (
    " (Note: the environment redirects git — {names} is set — so the shell "
    "that runs this command may address a different repository, index or "
    "configuration than the one inspected here; unset it or acknowledge "
    "knowingly.)"
)

NOT_DIRECT_NOTE = (
    " (Note: this command is not a plain git push/commit of this workspace "
    "— it redirects to another repository, moves the cwd, wraps another "
    "shell, uses expansion/escapes, or runs under a non-Bash tool — "
    "acknowledge only if you understand what it does.)"
)

NOT_LONE_NOTE = (
    " (Note: this command is compound — earlier segments can change the "
    "tree before the push — or pushes refs/tags beyond the reviewed HEAD "
    "(--all/--mirror/--tags, another branch, a refspec not rooted at HEAD); "
    "the review vouches for the reviewed HEAD only.)"
)

DIGEST_UNKNOWN_NOTE = (
    " (The workspace digest could not be computed within its budget — an "
    "unreadable entry, a file over 64 MiB, more than 1 GiB or 100,000 entries "
    "of content, or git slower than 20 s — so no review can be matched to "
    "this tree.)"
)

# ONE-SHOT ACKNOWLEDGEMENT (round 30). The gate DENIES rather than asks —
# a hook "ask" is auto-approved in some host permission modes, "deny" is
# authoritative in all of them — and hands the agent a token bound to this
# exact command and tree digest. Re-running the same command with the
# token in front consumes it and proceeds under the session's own
# permissions: the gate's whole job is to make the review state impossible
# to miss, not to hold the push hostage.
ACK_ENV = "CODEX_PUSH_ACK"
ACK_TTL_S = 600  # acknowledges ONE decision read moments ago; ten minutes
#                  covers a slow re-run and nothing else

ACK_HOWTO_POSIX = (
    " This command was NOT run. To proceed after reading the state above, "
    "re-run the SAME command with the one-shot acknowledgement in front: "
    "`" + ACK_ENV + "={nonce} <your command>` (valid 10 minutes, {scope})."
)
ACK_HOWTO_PS = (
    " This command was NOT run. To proceed after reading the state above, "
    "re-run the SAME command with the one-shot acknowledgement in front: "
    "`$env:" + ACK_ENV + "='{nonce}'; <your command>` (valid 10 minutes, {scope})."
)
ACK_SCOPE_TREE = "for this command and tree only"
ACK_SCOPE_COMMAND = (
    "for this command ONLY — the tree could not be digested, so this "
    "acknowledgement does not bind to it"
)
SCAN_BUDGET_NOTE = (
    " (The transcript scan exceeded its 10 s budget, so the review evidence is "
    "unknown — treated as no review.)"
)
ACK_INVALID_NOTE = (
    " (The acknowledgement token supplied was expired, already used, or "
    "minted for a different command or tree; a fresh one follows.)"
)
STORE_UNUSABLE_NOTE = (
    " (No acknowledgement token could be minted: the token store {path} is "
    "unusable — {why}. Fix it — a private directory you own, mode 0700, or "
    "point CODEX_PUSH_ACK_DIR at one — and re-run; on Windows the store is "
    "UNVERIFIED and the gate denies until it works. A broken store is a "
    "configuration defect the gate reports, never a reason to open — round 32.)"
)

# The hook's WORKER runs the whole evaluation (transcript scan, git lookups,
# digest, token) under a HARD deadline held by the hook's parent process:
# a PreToolUse hook that outlives the host's timeout fails OPEN, so the
# parent — which does nothing blocking itself — kills the worker's process
# group and emits DENY when the worker does not answer in time (round 32).
# Adjustable (validated: unparsable / out of band → default, loudly in the
# reason) so a calibration run can drive the timeout path.
PAYLOAD_MAX_BYTES = 16 << 20   # the host's tool payload; a Bash heredoc of a few hundred KB is the measured worst case
COMMAND_MAX_BYTES = 1 << 20    # the command text the classifier must parse under the deadline
EVAL_DEADLINE_DEFAULT_S = 60.0
EVAL_DEADLINE_MIN_S = 2.0
EVAL_MIN_REMAINING_S = 1.0  # less than this left after reading the payload: deny, do not spawn
NOT_DETECTED = "{}"  # the worker's answer for "examined, not a push/commit" — the parent prints nothing
EVAL_DEADLINE_MAX_S = 80.0  # hooks.json declares 90 s; 10 s of margin for the parent


def _eval_deadline_s():
    raw = os.environ.get("CODEX_PUSH_GATE_EVAL_DEADLINE_S")
    if not raw:
        return EVAL_DEADLINE_DEFAULT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return EVAL_DEADLINE_DEFAULT_S
    if not (EVAL_DEADLINE_MIN_S <= value <= EVAL_DEADLINE_MAX_S):
        return EVAL_DEADLINE_DEFAULT_S
    return value


EVAL_TIMEOUT_REASON = (
    "PUSH GATE: the gate could not finish evaluating this command within its "
    "{deadline:.0f} s budget (transcript scan, git lookups, tree digest or the "
    "token store stalled), so the command was NOT run and no acknowledgement "
    "token exists for it. Investigate what stalls (a huge transcript, a "
    "hung git or mount, an unreachable token store) and re-run; a hook that "
    "silently outlived the host's timeout would have opened the gate instead."
)
EVAL_FAILED_REASON = (
    "PUSH GATE: the gate's evaluation failed ({why}), so the command was NOT "
    "run and no acknowledgement token exists for it. Re-run after checking "
    "the hook's environment (python3, git, ~/.claude/logs/codex-oracle)."
)
ACK_ACCEPTED_NOTE = (
    "PUSH GATE: acknowledgement accepted — the gate's decision was read; the "
    "command proceeds under the session's normal permissions."
)

# git followed by push/commit within one shell segment (no ; | & between) —
# catches `npm test && git push`, `git -C /repo push`, `FOO=1 git commit`.
# CASE-INSENSITIVE: PowerShell commands are (`Git Push` runs git push).
_SEGMENT_SPLIT_RE = re.compile(r"[;|&\n]")
_LINE_SPLIT_RE = re.compile(r"[\r\n]")
_GIT_WORD_RE = re.compile(r"(?i)\bgit\b")
_PUSH_COMMIT_WORD_RE = re.compile(r"(?i)\b(?:push|commit)\b")
_PWSH_WORD_RE = re.compile(r"(?i)\b(?:pwsh|powershell)(?:\.exe)?\b")
_PWSH_ENCODED_RE = re.compile(r"(?i)\s[\"']?-(?:e|ec|en|enc[a-z]*)\b")


class _PushCommitMatcher:
    """LINEAR replacement for the former `\\bgit\\b[^;|&\\n]*\\b(push|commit)\\b`
    regex (round 40: with N `git` words in one segment the regex rescanned
    the rest of the segment from EVERY `git` — quadratic; 32,768 words in a
    131 KB command stalled the parent past its deadline, and a timed-out
    hook does not block). Per segment only the FIRST `git` matters: if any
    `git` is followed by push/commit, the first one is too. The second
    alternative — an ENCODED PowerShell command is an opaque payload the
    gate cannot read (round 20): treat it as a potential push so it always
    asks — is bounded the same way per line. `.search` keeps the regex
    call shape; it returns True or None."""

    def search(self, text):
        for seg in _SEGMENT_SPLIT_RE.split(text):
            m = _GIT_WORD_RE.search(seg)
            if m and _PUSH_COMMIT_WORD_RE.search(seg, m.end()):
                return True
        for line in _LINE_SPLIT_RE.split(text):
            m = _PWSH_WORD_RE.search(line)
            if m and _PWSH_ENCODED_RE.search(line, m.end()):
                return True
        return None


GIT_PUSH_COMMIT_RE = _PushCommitMatcher()
_PREFILTER_RE = re.compile(r"(?i)git|push|commit|pwsh|powershell")


def _maybe_git(command):
    """The PARENT's only look at the command (round 40): a LINEAR pre-filter
    that is a SUPERSET of every `_detected` channel — each of them needs one
    of these words to survive de-escaping and quote/dollar stripping, or
    ANSI-C quoting (`$'`) to be present — so the full detection and the
    decision both run in the WORKER under the one deadline. Whatever passes
    here is examined there; whatever fails here cannot be a push/commit to
    any channel."""
    if "$'" in command:
        return True
    return _PREFILTER_RE.search(re.sub(r"[\"'$]", "", _deescaped(command))) is not None

# Repository-redirection forms: the digest binds the review to the hook's
# cwd, so a push aimed at ANOTHER repository — or one whose cwd moved inside
# the command — must never be auto-opened by a matching cwd digest; those
# always ask (over-broad by design: a false positive costs one prompt).
# Covers POSIX (`GIT_DIR=… git push`, `cd /x && git push`) and PowerShell
# (`$env:GIT_DIR = '…'; git push`, `Set-Location X; git push`).
# `-C` is CASE-SENSITIVE (git -c key=val is a config flag, not a cwd
# change — round 22): it lives in its own case-sensitive belt.
GIT_REPO_FLAG_RE = re.compile(
    r"\bgit\b[^;|&\n]*\s[\"']?(?:-C[\"']?\s|--git-dir\b|--work-tree\b)")
GIT_OTHER_REPO_RE = re.compile(
    r"\bGIT_(?:DIR|WORK_TREE)\s*="
    r"|\$env:GIT_(?:DIR|WORK_TREE)\b"
    r"|(?:^|[;&|\r\n({]|\bthen\b|\bdo\b)\s*"
    r"(?:cd|chdir|pushd|popd|Set-Location|Push-Location|sl)\b"
    # A NESTED SHELL hides its own separators inside a quoted command
    # string (`sh -c "cd /x; git push"`, `bash -lc …`, quoted/clustered
    # options, PowerShell prefix-abbreviated options) — no flat regex can
    # see into it, so the mere PRESENCE of a shell-launcher name in a
    # push-matching command fails closed (rounds 19-20; over-broad by
    # design: one extra prompt, never a silent skip).
    # …only at a SEGMENT START (round 22: `git commit -m fix-bash-hook`
    # matched "bash" inside an argument); the positive parser handles the
    # quoted-launcher shapes.
    r"|(?:^|[;&|\r\n({]\s*|\bthen\s+|\bdo\s+)"
    r"(?:sh|bash|zsh|dash|ksh|fish|csh|tcsh|pwsh|powershell|cmd)(?:\.exe)?\b",
    re.IGNORECASE | re.MULTILINE)

# The server-stamped answer signature (order pinned by server.py
# `_answer_sig`); only an ok-status code_review answer selects the VERIFIED wording.
ANSWER_SIG_RE = re.compile(
    r"\[Codex model: [^\]\n]*\| tool:code_review \| status:ok"
    r" \| tree:([0-9a-fA-F]{4,64}|nogit|unknown)\]"
)

_HEX12_RE = re.compile(r"[0-9a-f]{12}")


class ScanBudgetExceeded(Exception):
    """The transcript scan ran past its budget (NOT an OSError subclass:
    TimeoutError is one, and an `except OSError` swallowed it — round 32)."""

# `CODEX_PUSH_ACK=<nonce> <command>` (POSIX) or
# `$env:CODEX_PUSH_ACK='<nonce>'; <command>` (PowerShell), nonce = 16 hex.
_ACK_PREFIX_RE = re.compile(
    r"^\s*(?:" + ACK_ENV + r"=([0-9a-f]{16})\s+"
    r"|\$env:" + ACK_ENV + r"\s*=\s*['\"]?([0-9a-f]{16})['\"]?\s*;\s*)(.*)\Z",
    re.S | re.I)


_ACK_NAME_RE = re.compile(r"[0-9a-f]{16}(?:\.claim-\d+-[0-9a-f]{8})?\.json")
_ACK_SWEEP_MAX = 4096  # entries examined per mint — beyond it minting is REFUSED loudly (round 33)


def _ack_dir():
    return os.environ.get("CODEX_PUSH_ACK_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "logs", "codex-oracle", "push-ack")


def _ack_dir_fd():
    """The token store as a validated DIRECTORY DESCRIPTOR: (fd, "") on
    success, (None, why) on any doubt — a real directory reached without
    following a symlink, owned by this user, no group/other access (round
    31). A failure is reported to the agent as a DENY with `why` (round 32:
    it used to degrade to "ask", which some host modes auto-approve).
    Windows (no O_DIRECTORY, `os.open` on a directory fails with EACCES):
    a path-based validation returns the sentinel -1 and every file
    operation goes by path — UNVERIFIED on Windows; a failure denies."""
    d = _ack_dir()
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
    except Exception as exc:
        return None, f"cannot create it ({exc.__class__.__name__}: {exc})"
    if os.name == "nt":
        # UNVERIFIED on Windows (round 33): no directory handle, no reparse-
        # point check, no ACL validation, and Python < 3.13 does not make a
        # 0o700 directory private there — the gate DENIES until a native
        # Windows known-green/known-red walk passes.
        return None, ("the token store is UNVERIFIED on Windows in this release (no "
                      "directory handle or ACL validation); the gate denies until a "
                      "native Windows walk passes")
    try:
        fd = os.open(d, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except Exception as exc:
        return None, f"cannot open it as a directory without following symlinks ({exc.__class__.__name__}: {exc})"
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISDIR(st.st_mode):
            raise OSError("not a directory")
        if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
            raise OSError(f"owned by uid {st.st_uid}, not you")
        if st.st_mode & 0o077:
            raise OSError(f"mode {oct(st.st_mode & 0o777)} grants group/other access (need 0700)")
        return fd, ""
    except Exception as exc:
        os.close(fd)
        return None, str(exc)


def _close_store(fd):
    if isinstance(fd, int) and fd >= 0:
        os.close(fd)


_USE_DIR_FD = (os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
               and os.unlink in os.supports_dir_fd)


def _ack_binding(command, tree, state=""):
    """The token's binding: content digest + exact command + STATE (round
    37: HEAD, the raw index listing, the toplevel and the git-routing
    environment — a token minted for one index cannot be consumed after the
    index, HEAD or the environment changed under it)."""
    return hashlib.sha256(
        tree.encode() + b"\0" + command.strip().encode("utf-8", "surrogateescape")
        + b"\0" + (state or "").encode()
    ).hexdigest()


def _env_binding():
    td = _load_treedigest()
    names = td.routing_env_names()
    return hashlib.sha256("\0".join(f"{k}={os.environ.get(k, '')}" for k in names)
                          .encode("utf-8", "surrogateescape")).hexdigest()[:16]


def _state_binding(insp, extra=()):
    """16 hex over the inspection's index/HEAD binding, the environment and
    every other input of the decision (`extra`, round 37)."""
    material = "\0".join([insp.get("binding") or "", _env_binding(), *[str(x) for x in extra]])
    return hashlib.sha256(material.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def _mint_ack(command, tree, state=""):
    """Write a one-shot token bound to (tree digest, exact command, state); returns
    (nonce, "") or ("", why) when the token store is unusable — the caller
    DENIES with `why` (round 32). The opportunistic sweep walks the store
    with scandir under a count cap and removes ONLY expired `<16hex>.json`
    regular files."""
    fd, why = _ack_dir_fd()
    if fd is None:
        return "", why
    d = _ack_dir()
    try:
        now = time.time()
        seen = 0
        with os.scandir(fd if (_USE_DIR_FD and fd >= 0) else d) as it:
            for entry in it:
                seen += 1
                if seen > _ACK_SWEEP_MAX:
                    # MANAGED LIMIT (round 33): a store past the sweep cap is a
                    # misconfiguration reported loudly, never a silent partial
                    # sweep that keeps minting
                    return "", (f"it holds more than {_ACK_SWEEP_MAX} entries — remove stale "
                                "tokens; the sweep is bounded and the store is not a queue")
                if not _ACK_NAME_RE.fullmatch(entry.name):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                    if stat_mod.S_ISREG(st.st_mode) and now - st.st_mtime > ACK_TTL_S:
                        if _USE_DIR_FD and fd >= 0:
                            os.unlink(entry.name, dir_fd=fd)
                        else:
                            os.unlink(os.path.join(d, entry.name))
                except OSError:
                    pass
        nonce = secrets.token_hex(8)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        if _USE_DIR_FD and fd >= 0:
            tfd = os.open(nonce + ".json", flags, 0o600, dir_fd=fd)
        else:
            tfd = os.open(os.path.join(d, nonce + ".json"), flags, 0o600)
        with os.fdopen(tfd, "w", encoding="utf-8") as fh:
            json.dump({"binding": _ack_binding(command, tree, state), "ts": now}, fh)
        return nonce, ""
    except Exception as exc:
        return "", f"cannot write a token ({exc.__class__.__name__}: {exc})"
    finally:
        _close_store(fd)



def _consume_ack(nonce, command, tree, state=""):
    """ONE-SHOT and ATOMIC (round 38): the token is first RENAMED to a
    per-consumer claim — rename is atomic within the store, so of two
    concurrent consumers exactly one holds the claim and the other sees no
    token — then read and removed (in `finally`, so a malformed token is
    consumed too). True only when the claim is a regular file of ours,
    unexpired, and bound to this exact command, tree digest and decision
    state."""
    if not re.fullmatch(r"[0-9a-f]{16}", nonce or ""):
        return False
    fd, _ = _ack_dir_fd()
    if fd is None:
        return False
    name = nonce + ".json"
    claim = f"{nonce}.claim-{os.getpid()}-{secrets.token_hex(4)}.json"
    by_fd = _USE_DIR_FD and fd >= 0 and os.rename in os.supports_dir_fd
    d = _ack_dir()
    try:
        try:
            if by_fd:
                os.rename(name, claim, src_dir_fd=fd, dst_dir_fd=fd)
            else:
                os.rename(os.path.join(d, name), os.path.join(d, claim))
        except OSError:
            return False  # no such token, or another consumer claimed it first
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            tfd = os.open(claim, flags, dir_fd=fd) if by_fd else os.open(os.path.join(d, claim), flags)
        except OSError:
            return False
        try:
            try:
                st = os.fstat(tfd)
                if not stat_mod.S_ISREG(st.st_mode) or (
                        hasattr(os, "geteuid") and st.st_uid != os.geteuid()):
                    return False
                rec = json.loads(os.read(tfd, 4096).decode("utf-8"))
                return (time.time() - float(rec.get("ts") or 0) <= ACK_TTL_S
                        and rec.get("binding") == _ack_binding(command, tree, state))
            except Exception:
                return False
        finally:
            os.close(tfd)
            try:
                if by_fd:
                    os.unlink(claim, dir_fd=fd)
                else:
                    os.unlink(os.path.join(d, claim))
            except OSError:
                pass
    finally:
        _close_store(fd)


def _load_treedigest():
    """The digest lives in ../treedigest.py — ONE implementation shared with
    server.py, loaded by path (this hook is a standalone script). A missing
    or broken module reads "unknown", which the gate reports loudly."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "treedigest.py")
    spec = importlib.util.spec_from_file_location("codex_oracle_treedigest", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _inspect_tree(cwd):
    """ONE walk of the worktree — the digest AND the filter-free status
    (index vs HEAD vs worktree bytes) — by the ONE implementation in
    ../treedigest.py (shared with server.py). In the hook's worker it runs
    in-process (the parent's group kill is the hard bound); it reads bytes
    itself and runs no `git diff`/`git status`, so no configured helper
    command can run through it. {"digest", "repo", "lines", "head",
    "reason"}; digest "nogit" outside a repository, "unknown" on any budget
    breach or failure (never a match), lines None when the status could not
    be established."""
    try:
        # IN-PROCESS in the worker (round 33): digest_hard() would open a
        # second session outside the parent's kill domain; here the git
        # children share the worker's group, so the parent's group kill is
        # the hard bound for all of them.
        return _load_treedigest().inspect(cwd, want_status=True)
    except Exception as exc:
        return {"digest": "unknown", "repo": False, "lines": None, "head": "",
                "reason": f"{type(exc).__name__}: {exc}"[:200]}


def _routing_env_names():
    """Repository-routing / config-injecting variables present in the HOOK's
    environment (the executing shell inherits the same environment)."""
    try:
        return _load_treedigest().routing_env_names()
    except Exception:
        return ["<environment unreadable>"]


def _fault(where):
    """FAULT INJECTION for the crash guard's calibration (round 37): with
    CODEX_PUSH_GATE_FAULT=<where> the named stage raises; the guard must
    still print a structured deny. A no-op otherwise."""
    if os.environ.get("CODEX_PUSH_GATE_FAULT") == where:
        raise RuntimeError(f"fault injection at {where}")


def _stall(where):
    """FAULT INJECTION (round 40): CODEX_PUSH_GATE_STALL=<where>:<seconds>
    sleeps at the named stage — the deadline tests need a worker that
    provably outlives the budget. A no-op otherwise."""
    name, _, secs = os.environ.get("CODEX_PUSH_GATE_STALL", "").partition(":")
    if name == where:
        try:
            time.sleep(float(secs))
        except ValueError:
            pass


def _workspace_digest(cwd):
    """12-hex CONTENT digest of the worktree (see _inspect_tree). It ignores
    HEAD and the index, so committing reviewed content keeps the review
    VERIFIED; whether the index/HEAD carry that content is judged
    separately by _consistent()."""
    return _inspect_tree(cwd)["digest"]


def _consistent(kind, insp):
    """Do the OBJECTS a lone `kind` ("push" | "commit") records equal the
    reviewed worktree bytes? Judged by the STRICT verifier, not the display
    status (round 37): a push carries HEAD's tree, so every path must be
    identical across HEAD, index and worktree, nothing untracked may exist,
    and no entry may be one the byte comparison could not vouch for
    (skip-worktree, a mode the filesystem cannot represent, a symlink
    materialised as a file, a conversion attribute); a commit records the
    index, so every index blob must equal its worktree bytes under the same
    caveats (staged-vs-HEAD differences are what a commit is for). None =
    unknown."""
    if insp.get("lines") is None or insp.get("strict") is None:
        return None
    if kind == "push":
        return not insp["lines"] and not insp["strict"]
    if kind == "commit":
        return not insp.get("strict_commit")
    return False


def _strict_sample(findings, n=4):
    items = sorted(findings.items())[:n]
    more = f" … +{len(findings) - n}" if len(findings) > n else ""
    return "; ".join(f"{p} — {r}" for p, r in items) + more


def _sample(lines, n=4):
    shown = sorted(lines)[:n]
    more = f" … +{len(lines) - n}" if len(lines) > n else ""
    return "; ".join(shown) + more

_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_CWD_VERB_RE = re.compile(
    r"(?i)^(?:cd|chdir|pushd|popd|set-location|push-location|sl|env)$")
# Shell words that are TRANSPARENT to what follows — they run the next word
# AS-IS. NOT here: `eval` REPARSES its arguments, `source`/`.` EXECUTE a
# file, `trap` schedules execution — opaque, fail closed.
_TRANSPARENT_RE = re.compile(r"(?i)^(?:builtin|command|exec|time|nohup)$")
_OPAQUE_RE = re.compile(r"(?i)^(?:eval|source|\.|trap)$")
# Environment names whose assignment changes WHAT executes or WHERE.
_DANGEROUS_ENV_RE = re.compile(
    r"(?i)^(?:PATH|IFS|ENV|BASH_ENV|SHELL|CDPATH|LD_[A-Z_]+|DYLD_[A-Z_]+)=")
# Presentation/diagnostics-only `git -c` keys; every other key is opaque
# (alias.*, include*, core.hooksPath, core.sshCommand, credential.helper,
# commit.gpgsign, core.autocrlf, push.default, diff/merge drivers, …).
_INERT_CONFIG_RE = re.compile(
    r"(?i)^(?:user\.(?:name|email)|color\.[a-z]+|advice\.[a-z]+|i18n\.[a-z]+)=")
# `git` GLOBAL options the direct parser accepts before the subcommand — each
# changes presentation or locking only, never which repository, refs,
# attributes, objects or programs are involved (round 37).
_GIT_GLOBAL_INERT = {"--no-pager", "-P", "--no-optional-locks", "--no-replace-objects",
                     "--literal-pathspecs"}  # round 38: -p/--paginate run core.pager, a shell command
_GROUPING = {"(", ")", "{", "}"}


def _deescaped(command):
    """A conservative de-escaped, line-joined copy for DETECTION only:
    `g\\it push` and `gi\\<newline>t push` are `git push` to the shell,
    so the gate must see what the shell sees. POSIX removes a
    backslash-newline pair WITHOUT inserting whitespace (round 25: a space
    turned `gi\\<nl>t` into `gi t`). PowerShell's escape and continuation
    character is the BACKTICK (`g`it`, "git `<newline>push"); both dialects
    are normalized regardless of the hook's tool name — normalizing a
    dialect the command is not in can only over-detect, never under-detect."""
    joined = re.sub(r"\\\r?\n", "", command)       # POSIX continuation: pair removed
    joined = re.sub(r"`\r?\n", "", joined)          # PowerShell continuation
    joined = re.sub(r"\\(.)", r"\1", joined)         # POSIX escape
    return re.sub(r"`(.)", r"\1", joined)            # PowerShell escape


def _tokenize(command):
    """ONE quote-aware token stream shared by detection and classification
    (round 26). Unquoted newlines become `;` separators first (shlex drops
    LF/CR as whitespace); shlex's `#` COMMENT handling is disabled — a
    comment would otherwise swallow the inserted separator and every later
    command (`git status # ok<nl>source ./x && git push` read as one
    segment). Returns None when the shell would not accept the text
    (unbalanced quotes) — callers fail closed."""
    import shlex
    text = command.replace("\r", "\n").replace("\n", " ; ")
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return None


def _segments(toks):
    """Split a token stream into command segments; None if unquoted
    grouping appears (hides structure)."""
    segments, cur = [], []
    for tok in toks:
        # shlex COALESCES punctuation runs (`&&(`, `)&&`, `;;(`): classify
        # any all-punctuation token by its characters (round 27) — a
        # grouping char anywhere in it hides structure.
        if tok and set(tok) <= set(";&|()"):
            if "(" in tok or ")" in tok:
                return None
            if cur:
                segments.append(cur)
            cur = []
            continue
        if tok in _GROUPING:
            return None
        cur.append(tok)
    if cur:
        segments.append(cur)
    return segments


def _detected(command):
    """Is this a git push/commit (or an opaque encoded PowerShell payload)?
    Checked on the RAW text, on a DE-ESCAPED copy, and on the QUOTE-REMOVED
    token stream (round 26): escape PARITY (`\\\\<nl>git push` keeps one
    backslash and a real newline — the raw text matches), and quote removal
    (`g""it push`, `git pu""sh` are `git push` — the tokens match). Any one
    channel seeing a push is enough: detection only decides whether the
    gate LOOKS; classification decides what it does."""
    if GIT_PUSH_COMMIT_RE.search(command) or GIT_PUSH_COMMIT_RE.search(_deescaped(command)):
        return True
    # QUOTE/DOLLAR-STRIPPED channel (round 27): Bash dollar quoting
    # (`g$''it`, `g$'i't`) and quote characters inside inert regions
    # (comments, here-doc bodies) defeat both the regex and a POSIX
    # tokenizer. Stripping every quote character and `$` sequence yields
    # what the shell would run in the worst case — over-detection only,
    # because detection merely decides whether the gate looks.
    stripped = re.sub(r"[\"']|\$", "", _deescaped(command))
    if GIT_PUSH_COMMIT_RE.search(stripped):
        return True
    # EXPANSION-BEARING push shapes (round 28): quote/dollar stripping is
    # not an upper bound on Bash — ANSI-C quoting decodes escapes
    # (`g$'\\x69't` is `git`) and a variable can BE the command word
    # (`G=git; "$G" push`). Static analysis cannot evaluate those, so the
    # presence of expansion machinery ($ \\ `) alongside a push/commit word
    # is itself detection; classification then fails closed and the gate
    # asks instead of staying silent.
    if (re.search(r"[$\\`]", command)
            and re.search(r"(?i)\b(?:push|commit)\b", stripped)):
        return True
    if "$'" in command:
        # ANSI-C quoting can BUILD the verb itself (`git $'\\x70\\x75\\x73\\x68'`,
        # `g$'\\x69't p$'\\x75'sh`) — nothing literal survives stripping, so
        # its mere presence is detection (round 29). A cooperative agent has
        # no reason to hex-assemble a command; the rare legitimate use costs
        # one prompt. Full evasion-proofing is impossible for a text hook
        # (documented trust model); execution-boundary enforcement is the
        # 1.18 daemon's job.
        return True
    toks = _tokenize(command)
    if toks is None:
        return False
    segs = _segments(toks) or [toks]
    for seg in segs:
        low = [t.lower() for t in seg]
        if "git" in low:
            after = low[low.index("git") + 1:]
            if "push" in after or "commit" in after:
                return True
    return False


def _direct_git_only(command):
    """POSITIVE parse on a QUOTE-AWARE token stream (round 24): the gate
    auto-opens only when every git-touching segment is a DIRECT `git …`
    invocation — first token git (after plain non-GIT_* VAR=val prefixes
    and transparent verbs), no -C/--git-dir/--work-tree — and no segment
    is led by a cwd verb, an env wrapper, or a reparsing verb. shlex does
    the quote removal the shell does (`"-C"` IS -C; `"fix(parser)"` is one
    argument, its parenthesis is not grouping), so quoted text no longer
    forces a prompt. Anything the parser cannot classify — unquoted
    grouping, ANY `$` (runtime expansion), ANY backslash (escapes can hide
    a verb or a flag: `\\cd`, `\\-C`), unbalanced quotes — fails closed.
    A denylist of launchers recurs forever; a whitelist of one shape ends it."""
    if "\\" in command or "$" in command or "`" in command:
        return False
    toks = _tokenize(command)
    if toks is None:
        return False  # unbalanced quotes: unclassifiable
    segments = _segments(toks)
    if segments is None:
        return False  # unquoted grouping hides structure
    for seg in segments:
        i = 0
        while i < len(seg) and (
                _TRANSPARENT_RE.match(seg[i])
                or (_ASSIGN_RE.fullmatch(seg[i])
                    and not seg[i].upper().startswith("GIT_")
                    and not _DANGEROUS_ENV_RE.match(seg[i]))):
            i += 1
        if i < len(seg) and _DANGEROUS_ENV_RE.match(seg[i]):
            return False  # PATH=/LD_*/IFS/… assignments change what executes
        if any(">" in t or "<" in t for t in seg):
            # REDIRECTION anywhere is a MUTATION indicator (round 28:
            # `printf changed > reviewed.txt; git add; git commit; git push`
            # rewrote a file after the digest was taken, in one command).
            return False
        if i >= len(seg):
            continue
        head = seg[i]
        if _CWD_VERB_RE.match(head) or _OPAQUE_RE.match(head):
            return False  # a cwd move / env wrapper / reparsing verb taints the command
        if not any(re.search(r"(?i)\bgit\b", t) for t in seg):
            continue
        if head.lower() != "git":
            return False  # something other than git fronts the git text
        args = seg[i + 1:]
        # GLOBAL options live BEFORE the subcommand (round 28:
        # `git commit -c HEAD` is commit's reuse-message flag, not config);
        # exec-capable transport flags are checked everywhere.
        k = 0
        while k < len(args) and args[k].startswith("-"):
            t = args[k]
            # GLOBAL options are an explicit INERT allowlist (round 37: the
            # denylist let --bare, --namespace=, --attr-source= through —
            # each re-aims the repository, its refs or its attributes).
            if t == "-c":
                kv = args[k + 1] if k + 1 < len(args) else ""
                if not _INERT_CONFIG_RE.match(kv):
                    return False
                k += 1
            elif t.startswith("-c") and len(t) > 2:
                if not _INERT_CONFIG_RE.match(t[2:]):
                    return False
            elif t not in _GIT_GLOBAL_INERT:
                return False
            k += 1
        for t in args[k:]:
            if t.startswith(("--receive-pack", "--upload-pack", "--exec")):
                return False  # transport/exec overrides run a program (--exec = --receive-pack)
    return True


# `git push` options the classifier KNOWS to be inert for "which objects
# get pushed". Everything else — broad flags (--all/--mirror/--tags/
# --follow-tags/--delete/--prune/--repo), git's abbreviated long options
# (`--fol`), clustered short flags (`-fd`), unknown flags — is NOT proven
# inert and reads VERIFIED-BUT (round 31: the classifier is not git's option
# parser and must not pretend to be).
_PUSH_INERT_FLAGS = {
    "-n", "--dry-run", "-u", "--set-upstream", "--no-verify", "-v", "--verbose", "-q", "--quiet",
    "--porcelain", "--progress", "--no-progress", "-4", "--ipv4", "-6", "--ipv6", "--atomic",
    "--no-atomic", "--no-signed", "--no-follow-tags", "--no-thin", "--thin",
}
# FORCE forms are NOT inert (round 38): the range is measured against the
# remote-tracking ref as of the LAST FETCH; a plain push that is behind is
# merely rejected by the remote, a forced one overwrites commits nobody here
# has seen. --force, -f, --force-with-lease (any form), --force-if-includes
# and a `+refspec` all read VERIFIED-BUT.
_PUSH_FORCE_FLAGS = {"-f", "--force", "--force-with-lease", "--force-if-includes"}
_PUSH_INERT_PREFIXES = ("--push-option=",)
_PUSH_ARG_FLAGS = {"-o", "--push-option"}  # take a separate argument


def _git_capped(cwd, args, cap):
    """One git call through treedigest's streamed, capped, deadline-bound
    reader (round 32: `subprocess.run(capture_output=True)` buffered
    unbounded output). (returncode, bytes); (-1, b"") on any breach."""
    try:
        td = _load_treedigest()
        return td._git_output(cwd, tuple(args), time.monotonic() + 10.0, 10.0, cap)
    except Exception:
        return -1, b""


def _current_branch(cwd):
    rc, out = _git_capped(cwd, ("symbolic-ref", "--short", "-q", "HEAD"), 4096)
    if rc == 0:
        return out.decode("utf-8", "surrogateescape").strip()
    return ""  # detached / unknown: only HEAD itself can name the reviewed commit


def _cfg_key(key):
    """git lowercases a key's section and variable, never its subsection."""
    parts = key.split(".")
    if len(parts) >= 3:
        return ".".join([parts[0].lower(), *parts[1:-1], parts[-1].lower()])
    return key.lower()


def _git_bool(value):
    """git's boolean grammar (config.c git_parse_maybe_bool + git_parse_int):
    true/yes/on and ANY non-zero integer are true, false/no/off/0 false.
    A `config --list` dump shows both `[s] key` (true to git) and `key =`
    (false to git) as an empty value, so "" is None (unknown) — as is any
    text git would reject or scale (`2k`, `maybe`, `if-asked`)."""
    v = value.strip().lower()
    if v in ("true", "yes", "on"):
        return True
    if v in ("false", "no", "off"):
        return False
    if re.fullmatch(r"[+-]?\d+", v):
        return int(v) != 0
    return None


def _truthy(value):
    """Not provably OFF: git would enable the feature, or the value cannot be
    read as a git boolean (round 40, calibrated: `commit.gpgSign=2` is true
    to `git config --type=bool`; `1` was the only integer accepted before)."""
    return _git_bool(value) is not False


def _git_config(cwd, overrides=()):
    """git's effective configuration as {key: [values…]} plus the command's
    own `-c key=val` overrides (later wins). {} when git fails — a bare push
    is then configuration-DEPENDENT and reads VERIFIED-BUT."""
    cfg = {}
    try:
        rc, out = _git_capped(cwd, ("config", "-z", "--list"), 1 << 20)
        if rc != 0:
            return None  # UNKNOWN configuration (round 33): never "defaults"
        for entry in out.split(b"\0"):
            if not entry:
                continue
            key, _, val = entry.partition(b"\n")
            cfg.setdefault(_cfg_key(key.decode("utf-8", "surrogateescape")), []).append(
                val.decode("utf-8", "surrogateescape"))
    except Exception:
        return None
    for kv in overrides:
        key, _, val = kv.partition("=")
        cfg.setdefault(_cfg_key(key), []).append(val)
    return cfg


def _lone_reviewed_git(command, cwd):
    """True when `_lone_kind` classifies the command as a lone push or commit."""
    return bool(_lone_kind(command, cwd)[0])


# `git commit` forms that record the COMPLETE index (or, with -a, the
# worktree of every tracked path) — the only forms whose recorded tree the
# strict verifier can vouch for. A pathspec, --only/--include, --patch/
# --interactive, --pathspec-from-file, --fixup/--squash, a reused message
# (-c/-C) or any unknown/abbreviated option constructs a DIFFERENT commit
# from a subset or another source and reads VERIFIED-BUT (round 37).
_COMMIT_INERT_FLAGS = {
    "--amend", "--no-edit", "-q", "--quiet", "-v", "--verbose",
    "-s", "--signoff", "--no-signoff", "-n", "--no-verify", "--verify", "--allow-empty",
    "--allow-empty-message", "--reset-author", "--no-status", "--status", "--no-post-rewrite",
    "--branch", "--porcelain", "--long", "--short", "-z", "--null", "--no-gpg-sign",
    "--dry-run", "-u", "--untracked-files",
}
_COMMIT_ARG_FLAGS = {"-m", "--message", "-F", "--file", "--author", "--date", "--cleanup"}
_COMMIT_INERT_PREFIXES = ("--message=", "--file=", "--author=", "--date=", "--cleanup=",
                          "--untracked-files=")
# EXECUTION SURFACES a commit can open (round 38): -e/--edit and -t/--template
# launch the editor (core.editor / GIT_EDITOR — a program), -S/--gpg-sign
# runs gpg.program, --trailer runs trailer.<token>.cmd. None keeps the
# strong wording.
_COMMIT_PROGRAM_FLAGS = {"-e", "--edit", "-t", "--template", "-S", "--gpg-sign", "--trailer"}
_COMMIT_PROGRAM_PREFIXES = ("--template=", "--gpg-sign=", "--trailer=")
_COMMIT_CLUSTER_LETTERS = "qvsn"     # short flags that may be clustered (`-qm`); n = --no-verify
# `-a`/`--all` (round 39): re-adds every modified tracked file THROUGH the
# clean filters — a same-byte file under a newly enabled conversion attribute
# is checked in transformed, so the recorded blob is not the reviewed bytes.
_COMMIT_WORKTREE_FLAGS = {"-a", "--all"}
_COMMIT_CLUSTER_ARG = "mF"           # a trailing letter that takes the rest / the next token


def _commit_form(args):
    """("", no_verify) when `git commit <args>` records the complete INDEX
    with an explicit message source and opens no program; otherwise
    (why, _). `no_verify` is derived while the arguments are CONSUMED in
    order (round 38: rescanning raw tokens read `-m -n` — a message — and
    `-Fnotes` as --no-verify, and ignored a later `--verify`). Round 39:
    `-a` records the worktree through the clean filters (refused); a commit
    without `-m`/`-F` and without `--no-edit` opens the EDITOR (a program)."""
    j = 0
    no_verify = False
    has_message = False
    no_edit = False
    while j < len(args):
        tok = args[j]
        if tok.startswith("#"):
            # round 37: quote removal erased the difference between a shell
            # comment (`# note`) and a quoted OPERAND (`"#file"` is a pathspec)
            return "a `#` word (a comment to the shell, an operand when quoted) cannot be classified", False
        if tok == "--":
            if j + 1 < len(args):
                return "a pathspec follows `--`", False
            j += 1
            continue
        if tok in _COMMIT_PROGRAM_FLAGS or tok.startswith(_COMMIT_PROGRAM_PREFIXES):
            return f"option {tok.split('=', 1)[0]} runs a program (editor, signer or trailer command)", False
        if tok in _COMMIT_WORKTREE_FLAGS:
            return f"option {tok} records the worktree through the clean filters, not the reviewed index", False
        if tok in ("-n", "--no-verify"):
            no_verify = True
            j += 1
            continue
        if tok == "--verify":
            no_verify = False
            j += 1
            continue
        if tok == "--no-edit":
            no_edit = True
            j += 1
            continue
        if tok in ("-m", "--message", "-F", "--file"):
            has_message = True
            j += 2
            continue
        if tok.startswith(("--message=", "--file=")):
            has_message = True
            j += 1
            continue
        if tok in _COMMIT_ARG_FLAGS:
            j += 2
            continue
        if tok in _COMMIT_INERT_FLAGS or tok.startswith(_COMMIT_INERT_PREFIXES):
            j += 1
            continue
        if tok.startswith("--"):
            return f"option {tok.split('=', 1)[0]} may record something other than the index", False
        if tok.startswith("-") and len(tok) > 1:
            body = tok[1:]
            k = 0
            takes_next = False
            while k < len(body):
                ch = body[k]
                if ch in _COMMIT_CLUSTER_LETTERS:
                    if ch == "n":
                        no_verify = True
                    k += 1
                    continue
                if ch in _COMMIT_CLUSTER_ARG:
                    has_message = True
                    takes_next = (k == len(body) - 1)
                    k = len(body)
                    break
                if ch == "a":
                    return "option -a records the worktree through the clean filters, not the reviewed index", False
                if ch in "etS":
                    return f"option -{ch} runs a program (editor, signer or trailer command)", False
                return f"option -{ch} may record something other than the index", False
            j += 2 if takes_next else 1
            continue
        return f"a pathspec ({tok}) records a subset of the index", False
    if not has_message and not no_edit:
        return "no -m/-F message source and no --no-edit: git opens the editor (a program)", False
    return "", no_verify


def _lone_kind(command, cwd):
    """("push" | "commit" | "", info) — full VERIFIED wording only for ONE
    segment that is a `git push` whose effective update set is exactly the
    reviewed HEAD — one refspec whose source is `HEAD` or the current branch
    and whose destination is not a tag, or no refspec under a configuration
    that pushes the current branch alone (push.default simple/current/
    upstream, no remote.<r>.push, no mirror, no push.followTags) — with only
    KNOWN-inert options; or ONE `git commit` in a form that records the
    complete index (`_commit_form`). Everything else — compound commands
    whose earlier segments can mutate the tree, other refs, tags,
    abbreviated/clustered/unknown options, configuration the classifier
    cannot vouch for — reads VERIFIED_BUT (rounds 30-31, 37). info carries
    the effective remote / destination for the push-range check, or `why`
    when a commit form was refused. Runs only after `_direct_git_only`
    accepted the command."""
    toks = _tokenize(command)
    segs = _segments(toks) if toks is not None else None
    if not segs or len(segs) != 1:
        return "", {}
    seg = segs[0]
    i = 0
    while i < len(seg) and (_TRANSPARENT_RE.match(seg[i]) or _ASSIGN_RE.fullmatch(seg[i])):
        if _ASSIGN_RE.fullmatch(seg[i]):
            # round 38: `HOME=. git push` / `XDG_CONFIG_HOME=/tmp git push` run
            # under a different GLOBAL configuration than the one inspected
            # here — any command-local assignment voids the strong wording
            return "", {"why": f"a command-local environment assignment ({seg[i].split('=', 1)[0]}=…) "
                                "changes the configuration git runs under; run it without the assignment"}
        i += 1
    if i >= len(seg) or seg[i].lower() != "git":
        return "", {}
    args = seg[i + 1:]
    k = 0
    overrides = []
    while k < len(args) and args[k].startswith("-"):  # global options (already vetted)
        if args[k] == "-c" and k + 1 < len(args):
            overrides.append(args[k + 1])
            k += 1
        elif args[k].startswith("-c") and len(args[k]) > 2:
            overrides.append(args[k][2:])
        k += 1
    if k >= len(args):
        return "", {}
    sub = args[k].lower()
    if sub == "commit":
        why, no_verify = _commit_form(args[k + 1:])
        if why:
            return "", {"why": why}
        return "commit", {"no_verify": no_verify, "amend": "--amend" in args[k + 1:]}
    if sub != "push":
        return "", {}
    positional = []
    leases = []  # (ref, oid) in COMMAND-LINE ORDER: git applies the FIRST entry matching a ref
    rest = args[k + 1:]
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok.startswith("#"):
            return "", {}  # a comment to the shell, or a quoted refspec (`"#evil"` is a valid ref): unclassifiable
        if tok in _PUSH_ARG_FLAGS:
            j += 2
            continue
        if tok.startswith("-"):
            if tok == "--no-force-with-lease":
                # cancels EVERY lease given before it (git-push(1)); a later
                # lease re-arms (round 40, calibrated: `main:<B>
                # --no-force-with-lease` was accepted by a remote whose tip
                # had moved — the lease was gone)
                leases = []
                j += 1
                continue
            if tok in _PUSH_INERT_FLAGS or tok.startswith(_PUSH_INERT_PREFIXES):
                j += 1
                continue
            if tok.startswith("--force-with-lease="):
                # `--force-with-lease=<ref>:<oid>` with an EXPLICIT object id is
                # a compare-and-swap, not a force: git refuses the push unless
                # the remote tip equals <oid> (round 39: the only way a hook
                # without network access can bind a push to the tip it
                # measured). The bare/implicit forms stay forced.
                ref, sep, expect = tok[len("--force-with-lease="):].partition(":")
                if not sep or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expect):
                    return "", {"form": "push", "why": "a forced push can overwrite commits the last fetch never saw "
                                                       "(--force-with-lease without an explicit expected object id)"}
                leases.append((ref, expect))
                j += 1
                continue
            if tok in _PUSH_FORCE_FLAGS:
                return "", {"form": "push", "why": "a forced push can overwrite commits the last fetch never saw"}
            return "", {}  # broad, abbreviated, clustered or unknown: not proven inert
        if tok.startswith("+"):
            return "", {"form": "push", "why": "a `+refspec` is a forced push"}
        positional.append(tok)
        j += 1
    if len(positional) > 2:
        return "", {}  # remote + at most ONE refspec
    cfg = _git_config(cwd, overrides)
    if cfg is None:
        return "", {}  # configuration UNKNOWN: a bare push cannot be proven (round 33)
    if _truthy((cfg.get("push.followtags") or ["false"])[-1]):
        return "", {}  # tags ride along with any push
    # push.recurseSubmodules, else git's documented fallback submodule.recurse
    recurse = (cfg.get("push.recursesubmodules") or [""])[-1].strip().lower()
    if not recurse and _truthy((cfg.get("submodule.recurse") or ["false"])[-1]):
        recurse = "on-demand"
    if recurse not in ("", "no", "false", "off", "0", "check"):
        return "", {}  # on-demand / only: submodule pushes the review never saw
    branch = _current_branch(cwd)
    if not branch and (len(positional) < 2 or positional[1].lstrip("+").split(":", 1)[0] != "HEAD"):
        return "", {}  # detached HEAD: only `HEAD` itself can name the reviewed commit
    # EFFECTIVE REMOTE (round 32): git's precedence is branch.<b>.pushRemote,
    # then remote.pushDefault, then branch.<b>.remote, then "origin"; a target
    # that names a remote GROUP (remotes.<group>) fans out to every member.
    if positional:
        remote = positional[0]
    else:
        remote = ((cfg.get(f"branch.{branch}.pushremote") or cfg.get("remote.pushdefault")
                   or cfg.get(f"branch.{branch}.remote") or ["origin"])[-1])
    if cfg.get(f"remotes.{remote}"):
        return "", {}  # a remote group: many remotes, each with its own config
    if _truthy((cfg.get(f"remote.{remote}.mirror") or ["false"])[-1]):
        return "", {}
    if len(cfg.get(f"remote.{remote}.pushurl") or []) > 1 or len(cfg.get(f"remote.{remote}.url") or []) > 1:
        return "", {}  # several URLs: one push fans out to every one (round 33)
    info = {"remote": remote, "cfg": cfg, "dst": "", "leases": leases}
    if cfg.get(f"remote.{remote}.push"):
        # configured push refspecs decide what is pushed: a bare push uses
        # them, and a SOURCE-ONLY refspec is mapped through them (round 42,
        # calibrated: `remote.origin.push=refs/heads/main:refs/tags/main`
        # sent `git push origin main` to the TAG as a forced update)
        return "", {"form": "push", "why": f"remote.{remote}.push maps the destination of a bare push and "
                                           "of a source-only refspec"}
    raw_mode = (cfg.get("push.default") or ["simple"])[-1].lower()
    mode = "upstream" if raw_mode == "tracking" else raw_mode  # `tracking`: git's deprecated synonym (environment.c)
    if len(positional) == 2:
        src, _, dst = positional[1].partition(":")
        if not src or src not in ("HEAD", branch):
            return "", {}  # deletion, another branch, or a ref not rooted at HEAD
        if not dst and src != "HEAD" and mode == "upstream":
            # builtin/push.c refspec_append_mapped: under push.default=upstream
            # (or its synonym `tracking`) a source-only BRANCH name is mapped
            # to branch.<b>.merge (round 42, calibrated: merge=refs/tags/main
            # sent `git push origin main` to the tag; `HEAD` is not mapped) —
            # but only when git KEEPS that configuration: remote.c set_merge
            # drops it unless branch.<b>.remote is set and exactly one merge
            # value exists (round 43, calibrated: without branch.main.remote,
            # merge=refs/heads/release still pushed `main -> main`; with it,
            # `main -> release`).
            merges = cfg.get(f"branch.{branch}.merge") or []
            # PRESENCE of the key, whatever its value (round 44, calibrated: remote.c
            # set_merge keeps the merge configuration for `branch.main.remote = " "`)
            has_remote = bool(cfg.get(f"branch.{branch}.remote"))
            if has_remote and len(merges) == 1:
                if not merges[0].startswith("refs/heads/"):
                    return "", {"form": "push", "why": f"push.default={raw_mode} maps `{src}` to `{merges[0]}`"}
                dst = merges[0]
        # the destination must be UNAMBIGUOUS: omitted (git then uses the
        # SOURCE's full name — measured: `main` and `HEAD` alone create
        # refs/heads/main even when only refs/tags/main exists remotely) or
        # fully qualified under refs/heads/. An EXPLICIT unqualified name —
        # even the branch's own — is resolved against the remote's refs at
        # push time (round 41, calibrated: with only refs/tags/main on the
        # remote, `HEAD:main` updated the TAG). Even the full name is EXPANDED
        # against the advertised refs (round 42: a tag or remote-tracking ref
        # NAMED refs/heads/main becomes the target when the branch is absent)
        # — which is why the lease must be spelled exactly like the
        # destination (_lease_binds).
        if dst and not dst.startswith("refs/heads/"):
            return "", {"form": "push", "why": f"the explicit destination `{dst}` is not fully qualified — git "
                                               f"resolves it against the remote's refs at push time; write "
                                               f"`{src}:refs/heads/{dst}`"}
        info["dst"] = (dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst) or branch
        return "push", info
    if mode not in ("simple", "current", "upstream"):
        return "", {}
    if mode == "upstream":
        # setup_push_upstream: exactly one merge value, branch.<b>.remote set
        # (else git refuses to push at all — no destination to vouch for)
        merges = cfg.get(f"branch.{branch}.merge") or []
        has_remote = bool(cfg.get(f"branch.{branch}.remote"))  # presence, not a stripped value (round 44)
        merge = merges[0] if (has_remote and len(merges) == 1) else ""
        info["dst"] = merge[len("refs/heads/"):] if merge.startswith("refs/heads/") else ""
    else:
        info["dst"] = branch
    return "push", info


def _push_range(cwd, remote, dst, cfg):
    """(ahead, behind, why) — the commit range a lone push of HEAD to
    <remote>/<dst> transfers, measured against the remote-tracking ref
    (round 37: a push transfers HISTORY, not a tree — a commit that added a
    secret and a later one that removed it leave the reviewed tree intact
    and still ship the secret). why != "" when the range cannot be
    established: an unconfigured remote (a URL), a legacy
    $GIT_DIR/remotes|branches definition (its own Push: lines), a fetch
    refspec that does not map refs/heads/* to refs/remotes/<remote>/*, or a
    missing tracking ref (fetch first)."""
    if not dst:
        return None, None, "no destination branch could be named", ""
    if not cfg.get(f"remote.{remote}.url"):
        return None, None, f"'{remote}' is not a configured remote", ""
    # PUSH-ONLY ENDPOINTS (round 37): git pushes to pushurl (and rewrites the
    # push endpoint with url.<base>.pushInsteadOf) while the remote-tracking
    # ref reflects the FETCH endpoint — the history measured here would be
    # another server's; a configured receive-pack is a program the push runs.
    if cfg.get(f"remote.{remote}.pushurl"):
        return None, None, f"remote.{remote}.pushurl sends the push to an endpoint the tracking ref does not reflect", ""
    if any(k.startswith("url.") and k.endswith(".pushinsteadof") for k in cfg):
        return None, None, "url.<base>.pushInsteadOf rewrites the push endpoint", ""
    if cfg.get(f"remote.{remote}.receivepack"):
        return None, None, f"remote.{remote}.receivepack names a program for this push", ""
    if cfg.get(f"remote.{remote}.vcs"):
        return None, None, f"remote {remote} is served by a remote helper program", ""
    # EFFECTIVE endpoints (round 39): `remote get-url` applies url.<base>.insteadOf
    # (any scope) and executes nothing; git runs `git-remote-<transport>` for
    # every scheme it does not implement natively, so only the native
    # transports (ssh, git, http, https, file, scp-like, local paths) pass.
    urls = []
    for extra in ((), ("--push",)):
        rc, out = _git_capped(cwd, ("remote", "get-url", *extra, remote), 4096)
        if rc != 0:
            return None, None, f"the effective URL of {remote} could not be resolved", ""
        urls.append(out.decode("utf-8", "surrogateescape").strip())
    if urls[0] != urls[1]:
        return None, None, f"{remote} pushes to a different endpoint than it fetches from", ""
    if not _native_transport(urls[0]):
        return None, None, f"remote {remote} is served by a remote helper program ({urls[0][:40]})", ""
    for legacy in ("remotes", "branches"):
        rc, out = _git_capped(cwd, ("rev-parse", "--git-path", f"{legacy}/{remote}"), 4096)
        if rc != 0:
            return None, None, "git path lookup failed", ""
        p = out.decode("utf-8", "surrogateescape").strip()
        if p and os.path.lexists(p if os.path.isabs(p) else os.path.join(cwd, p)):
            return None, None, f"a legacy $GIT_DIR/{legacy}/{remote} definition exists", ""
    fetch = cfg.get(f"remote.{remote}.fetch") or []
    if fetch not in ([f"+refs/heads/*:refs/remotes/{remote}/*"], [f"refs/heads/*:refs/remotes/{remote}/*"]):
        return None, None, f"remote.{remote}.fetch is not the standard refs/heads/* mapping", ""
    tracking = f"refs/remotes/{remote}/{dst}"
    rc, out = _git_capped(cwd, ("rev-parse", "--verify", "--quiet", tracking), 4096)
    if rc != 0:
        return None, None, f"no remote-tracking ref {tracking} (fetch first)", ""
    tracking_oid = out.decode("ascii", "replace").strip()
    counts = []
    for spec in (f"{tracking}..HEAD", f"HEAD..{tracking}"):
        rc, out = _git_capped(cwd, ("rev-list", "--count", spec), 4096)
        if rc != 0 or not out.strip().isdigit():
            return None, None, f"rev-list failed for {spec}", ""
        counts.append(int(out.strip()))
    return counts[0], counts[1], "", tracking_oid


# case-SENSITIVE like transport.c's starts_with(): `SSH://` and `GIT://` run
# `git-remote-SSH` / `git-remote-GIT` helper programs (round 41, traced)
_NATIVE_SCHEME_RE = re.compile(r"^(?:ssh|git|https?|file|git\+ssh|ssh\+git)://")  # http(s): git's bundled remote-curl
_HELPER_PREFIX_RE = re.compile(r"^[A-Za-z0-9+.-]*::")
_SCP_LIKE_RE = re.compile(r"^(?:[^/@:]+@)?[^/:]+:(?!//)")


def _native_transport(url):
    """True for URLs git serves ITSELF: ssh/git/http/https/file schemes, the
    scp-like `[user@]host:path` form, and local paths. Everything else —
    `<transport>::…`, `ext::`, an unknown `scheme://` — invokes a
    `git-remote-<transport>` helper program."""
    if not url or _HELPER_PREFIX_RE.match(url):
        # transport.c: URL-scheme characters immediately followed by "::"
        # name a `git-remote-<transport>` helper; "::" anywhere ELSE is an
        # address or a path (round 40: `ssh://[::1]/repo`, `file:///tmp/a::b`)
        return False
    if _NATIVE_SCHEME_RE.match(url):
        return True
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*://", url):
        return False  # a scheme git does not implement: a helper
    if _SCP_LIKE_RE.match(url):
        return True
    return url.startswith(("/", "./", "../", "~")) or ":" not in url


def _active_hooks(cwd, event):
    """The hooks git would RUN for `event`, from `git hook list -z <event>`
    under the command's effective configuration (round 38: Git 2.55 also
    runs CONFIGURED hooks — hook.<name>.command/event — that no file scan
    can see; empty core.hooksPath and %(prefix) forms resolve as git does).
    The listing runs with the fsmonitor override but WITHOUT the hooksPath
    override, and executes nothing (measured). A git without `hook list`
    (rc != 0) reads as "hooks unknown" — assume one runs."""
    try:
        td = _load_treedigest()
        rc, out = td._git_output(cwd, ("hook", "list", "-z", event), time.monotonic() + 10.0, 10.0, 1 << 16,
                                 config=("-c", "core.fsmonitor=false"))
    except Exception:
        return ["<hooks unknown>"]
    names = [n.decode("utf-8", "backslashreplace") for n in out.split(b"\0") if n.strip()]
    if rc == 0 and names:
        return names
    if rc == 1 and not names:
        return []  # measured: "warning: no hooks found for event" + exit 1
    return ["<hooks unknown>"]  # a git without `hook list`, or an unexpected shape: assume one runs


# Repository-scoped configuration that names a PROGRAM or rewrites an
# endpoint (round 38): a `.git/config` the sealed writer or a clone can
# carry. Global/system values are the user's own and are not counted.
# Configuration keys that name a PROGRAM or rewrite an endpoint, matched by
# SECTION and terminal VARIABLE so a subsection may contain dots or anything
# else (round 39: `credential.https://example.com.helper`,
# `filter.my.driver.clean`, `includeif.gitdir:/tmp/a.b/.path` escaped a
# dot-free regex). (requires-subsection, variables): True = only with a
# subsection, False = only without, None = either; variables None = any.
_PROGRAM_RULES = {
    "core": (False, {"sshcommand", "askpass", "editor", "pager", "gitproxy", "fsmonitor", "hookspath"}),
    "credential": (None, {"helper"}),
    "gpg": (None, {"program"}),
    "trailer": (True, {"cmd"}),
    "remote": (True, {"vcs", "proxy", "receivepack", "uploadpack"}),
    "sequence": (False, {"editor"}),
    "filter": (True, {"clean", "smudge", "process"}),
    "diff": (True, {"command", "textconv"}),
    "merge": (True, {"driver"}),
    "url": (True, {"insteadof", "pushinsteadof"}),
    "include": (False, {"path"}),
    "includeif": (True, {"path"}),
    "alias": (False, None),
    "hook": (True, {"command"}),
}


def _is_program_key(key):
    section, _, rest = key.partition(".")
    if not rest:
        return False
    rule = _PROGRAM_RULES.get(section.lower())
    if rule is None:
        return False
    needs_sub, variables = rule
    var = rest.rsplit(".", 1)[-1].lower()
    has_sub = "." in rest
    if needs_sub is True and not has_sub:
        return False
    if needs_sub is False and has_sub:
        return False
    return variables is None or var in variables


def _repo_scoped_programs(cwd):
    """Keys at the LOCAL or WORKTREE scope that name a program or rewrite an
    endpoint (`git config -z --list --show-scope`, entries shaped
    `<scope>\0<key>\n<value>\0`). None when the listing failed (unknown)."""
    rc, out = _git_capped(cwd, ("config", "-z", "--list", "--show-scope"), 1 << 20)
    if rc != 0:
        return None
    parts = out.split(b"\0")
    found = []
    for i in range(0, len(parts) - 1, 2):
        scope = parts[i].strip().decode("ascii", "replace")
        key = parts[i + 1].partition(b"\n")[0].decode("utf-8", "surrogateescape")
        if scope in ("local", "worktree") and _is_program_key(key):
            found.append(key)
    return sorted(set(found))


def _unwrap(text):
    """Undo known MCP result wrappers: {'result': '...'} or ['...']."""
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            obj = json.loads(stripped)
        except ValueError:
            return text
        if isinstance(obj, dict) and isinstance(obj.get("result"), str):
            return obj["result"]
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, str) and "[Codex model:" in item:
                    return item
    return text


def _texts(block):
    """Every text payload inside a tool_result content field."""
    content = block.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                yield part
            elif isinstance(part, dict) and part.get("type") == "text":
                yield str(part.get("text") or "")


_RESULT_JSON_RE = re.compile(r'\{"result":\s*"((?:[^"\\]|\\.)*)"')


def _signed_digest(result_text):
    """The tree digest of a VERIFIED answer: the result's FIRST LINE must be
    exactly the server's ok header, with a nonempty body after it (round
    25: a substring search accepted an ok marker quoted inside a
    status:error body — the same forgery the abraham gate had)."""
    text = _unwrap(result_text).lstrip()
    first = text.partition("\n")[0].rstrip("\r")
    match = ANSWER_SIG_RE.fullmatch(first)
    # The signed FIRST LINE is the evidence; a body is not required here
    # (abraham's gate needs one because it consumes the body as a brief).
    return match.group(1).lower() if match else None


def _review_state(transcript_path, deadline_s=10.0):
    """Return (dispatched, answered_digests) from transcript evidence.

    dispatched: an assistant tool_use whose name is an MCP code_review tool
    (structural — file contents in the transcript cannot fabricate it).
    answered_digests: tree digests from ANCHORED answer signatures — the
    first line of a tool_result BOUND to a dispatched tool_use_id
    (foreground, possibly JSON-wrapped), or of the `{"result": …}` payload
    inside a queue-operation entry (backgrounded task notification).
    """
    dispatched_ids = set()
    answered_digests = set()
    end = time.monotonic() + deadline_s
    for n, line in enumerate(open(transcript_path, encoding="utf-8", errors="ignore")):
        if time.monotonic() > end:  # every line (round 32: one slow line is enough)
            raise ScanBudgetExceeded("transcript scan budget")  # loud: the gate says so
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        etype = entry.get("type")
        if etype == "queue-operation":
            content = str(entry.get("content") or "")
            for m in _RESULT_JSON_RE.finditer(content):
                # TOOL BINDING (round 29): the host-generated summary BEFORE
                # the result payload names the tool that ran — a copied
                # review header inside another tool's result must not count.
                if "code_review" not in content[:m.start()]:
                    continue
                try:
                    payload = json.loads('"' + m.group(1) + '"')
                except ValueError:
                    continue
                digest = _signed_digest(payload)
                if digest:
                    answered_digests.add(digest)
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if etype == "assistant" and block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                if name.startswith("mcp__") and name.endswith("__code_review"):
                    dispatched_ids.add(str(block.get("id") or ""))
            elif block.get("type") == "tool_result":
                if str(block.get("tool_use_id") or "") not in dispatched_ids:
                    continue  # a result not bound to a code_review dispatch is not evidence
                for text in _texts(block):
                    digest = _signed_digest(text)
                    if digest:
                        answered_digests.add(digest)
    if time.monotonic() > end:
        raise ScanBudgetExceeded("transcript scan budget")  # never return evidence late
    return bool(dispatched_ids), answered_digests


def _lease_binds(leases, dst, tracking_oid):
    """Does the command's lease bind THIS push to the measured tracking tip
    for EVERY ref git could resolve the destination to? git expands a
    destination — even a fully qualified one — against the advertised refs
    with the rev-parse rules (remote.c count_refspec_match → refname_match:
    `refs/heads/main` also names refs/tags/refs/heads/main and
    refs/remotes/refs/heads/main when they exist and the branch does not;
    round 42, calibrated: a nested remote-tracking ref at A took a plain
    fast-forward past a `main:<B>` lease, which does not match that name,
    and a nested tag with its own lease first took a forced update). apply_cas
    uses the SAME predicate for the lease entries and takes the first match,
    so a lease spelled EXACTLY like the destination and FIRST on the command
    line applies to every possible target and pins it to the measured tip
    (calibrated: "stale info" on every crafted remote); any other spelling
    or order leaves some target unleased. `--no-force-with-lease` empties the
    list (round 40)."""
    return bool(leases) and bool(dst) and bool(tracking_oid) and leases[0] == (f"refs/heads/{dst}", tracking_oid)


def _evaluate(data):
    """The whole gate decision for a DETECTED push/commit, as the JSON the
    hook prints. Runs in the WORKER process under the parent's hard deadline."""
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    tool_name = str(data.get("tool_name") or "")
    cwd = data.get("cwd") or "."
    if os.name == "nt":
        # UNVERIFIED platform (round 34): deny BEFORE any git or digest work
        return _deny(GATE_REASON + STORE_UNUSABLE_NOTE.format(
            path=_ack_dir(), why=_ack_dir_fd()[1] or "unverified on Windows"))
    _fault("worker")
    _stall("worker")
    insp = _inspect_tree(cwd)
    current = insp["digest"]
    notes = ""
    # ONE-SHOT ACKNOWLEDGEMENT (round 30): a token minted by an earlier
    # decision lets the command proceed under the session's normal
    # permissions (no decision emitted). It is bound to the COMPLETE decision
    # (round 37): the command, the content digest, and every input the
    # decision below is computed from — so it is consumed only AFTER that
    # decision has been recomputed here for the inner command.
    ack = _ACK_PREFIX_RE.match(command)
    nonce = ""
    if ack:
        nonce = ack.group(1) or ack.group(2)
        command = ack.group(3)  # decide (and mint) for the command itself
    # DENY, ALWAYS (design ruling 2026-09-02): five review rounds of shell-
    # semantics counterexamples proved static classification of arbitrary
    # shell text cannot be complete, so the DECISION surface is gone — a
    # detected push/commit is always stopped with the review state in the
    # reason, and parsing only informs the WORDING below. "deny" rather than
    # "ask" (round 30): "ask" is auto-approved in some host permission modes;
    # "deny" holds in all of them. A parsing error can cost a sentence,
    # never a silent auto-open.
    deesc = _deescaped(command)
    direct = (tool_name.lower() == "bash"
              and not GIT_REPO_FLAG_RE.search(deesc)
              and not GIT_OTHER_REPO_RE.search(deesc)
              and "\\" not in command and "`" not in command
              and _direct_git_only(command))
    kind, info = _lone_kind(command, cwd) if direct else ("", {})
    # ENVIRONMENT ROUTING (round 37): inspection scrubs GIT_DIR-style
    # variables, the shell that runs the command inherits them — with one
    # present, inspection and execution may address different repositories.
    routed = _routing_env_names()
    lone = bool(kind) and not routed
    # INDEX/HEAD CONSISTENCY (round 36-37): the digest binds worktree BYTES,
    # git commits the INDEX and pushes HEAD's TREE — a staged blob that
    # differs from the reviewed file kept the digest (and the VERIFIED
    # wording) while unreviewed content shipped. Full VERIFIED wording needs
    # the objects the command records to equal the reviewed worktree under
    # the STRICT verifier, and — for a push — a transferred range of at most
    # ONE commit on top of the remote-tracking ref (a push carries history).
    consistent = _consistent(kind, insp) if lone else False
    ahead = behind = None
    range_why = ""
    tracking_oid = ""
    if lone and kind == "push" and consistent:
        ahead, behind, range_why, tracking_oid = _push_range(cwd, info.get("remote", ""), info.get("dst", ""),
                                                             info.get("cfg") or {})
    range_ok = kind == "commit" or (not range_why and ahead is not None and ahead <= 1 and behind == 0)
    # HOOKS (round 37-38): a pre-commit hook runs BEFORE the commit is created
    # and can re-stage content (measured: the committed tree gained a file
    # the hook added); --no-verify skips it. A pre-push hook runs during the
    # push. Either voids the strong wording; prepare-commit-msg cannot change
    # the recorded tree (measured) and is not counted. Listed by git itself
    # (`hook list`), so configured hooks count too.
    hooks = []
    if lone and kind == "commit":
        # every hook a commit reaches (githooks): --no-verify skips pre-commit
        # and commit-msg only; prepare-commit-msg, post-commit, post-index-
        # change and reference-transaction always run (measured: prepare-
        # commit-msg cannot change the recorded tree, but it runs code).
        events = [] if info.get("no_verify") else ["pre-commit", "commit-msg"]
        events += ["prepare-commit-msg", "post-commit", "post-index-change", "reference-transaction"]
        if info.get("amend"):
            events.append("post-rewrite")
    elif lone and kind == "push":
        events = ["pre-push", "reference-transaction"]
    else:
        events = []
    for ev in events:
        names = _active_hooks(cwd, ev)
        if names:
            hooks.append(f"{ev} ({', '.join(names)})")
    # IMPLICIT SIGNING (round 39): commit.gpgSign / push.gpgSign run the
    # signing program without any flag on the command line.
    signing = []
    if lone:
        scfg = info.get("cfg") if kind == "push" else _git_config(cwd)
        scfg = scfg or {}
        if kind == "commit" and _truthy((scfg.get("commit.gpgsign") or ["false"])[-1]):
            signing.append("commit.gpgSign")
        if kind == "push" and _truthy((scfg.get("push.gpgsign") or ["false"])[-1]):  # if-asked counts
            signing.append("push.gpgSign")
    # REPOSITORY-SCOPED PROGRAMS (round 38): local/worktree configuration
    # that names a program or rewrites an endpoint is code the command
    # would run that this inspection cannot vouch for.
    programs = _repo_scoped_programs(cwd) if lone else []
    if programs is None:
        programs = ["<configuration scopes unreadable>"]
    # LEASE (round 39): the range is measured against the tracking ref AS OF
    # THE LAST FETCH; a hook without network access cannot know the remote's
    # current tip, so a push earns the full wording only when the command
    # binds itself to that tip with `--force-with-lease=<dst>:<oid>` (git
    # then refuses the push if the remote moved).
    lease_ok = kind != "push" or _lease_binds(info.get("leases") or [], info.get("dst", ""), tracking_oid)
    verified = bool(lone and consistent and range_ok and lease_ok and not hooks and not programs and not signing)
    tracking = f"refs/remotes/{info.get('remote', '')}/{info.get('dst', '')}" if kind == "push" else ""
    # REVIEW EVIDENCE FIRST (round 38): the wording class and the evidence
    # it rests on are inputs of the decision the token binds.
    reason = GATE_REASON
    review_evidence = "no-transcript"
    transcript = data.get("transcript_path") or ""
    dispatched, answered_digests, scan_note = False, set(), ""
    if transcript:
        try:
            dispatched, answered_digests = _review_state(transcript)
            review_evidence = f"{int(dispatched)}:{','.join(sorted(answered_digests))}"
        except ScanBudgetExceeded:
            scan_note = SCAN_BUDGET_NOTE
            review_evidence = "scan-budget"
        except OSError:
            review_evidence = "transcript-unreadable"
    matched = bool(dispatched and _HEX12_RE.fullmatch(current) and current in answered_digests)
    if matched:
        reason = (VERIFIED_REASON if verified else VERIFIED_BUT_REASON).format(digest=current)
    elif answered_digests:
        reason = STALE_REASON
    elif dispatched:
        reason = CODEX_PENDING_REASON
    # THE TOKEN'S STATE: every input of this decision AND its outcome, not only
    # the content (round 37-38: an old token survived a config change, a
    # same-commit branch switch, a moved tracking ref and a changed review).
    rc_cfg, cfg_raw = _git_capped(cwd, ("config", "-z", "--list"), 1 << 20)
    state = _state_binding(insp, [
        _current_branch(cwd), hashlib.sha256(cfg_raw).hexdigest() if rc_cfg == 0 else "config-unreadable",
        kind, info.get("remote", ""), info.get("dst", ""), tracking_oid, str(ahead), str(behind),
        range_why, ",".join(hooks), ",".join(programs), ",".join(signing), str(lease_ok), str(verified),
        str(consistent), ",".join(routed), review_evidence, reason.split(" — ", 1)[0][:60]])
    if nonce:
        if _consume_ack(nonce, command, current, state):
            return json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": ACK_ACCEPTED_NOTE,
            }})
        notes += ACK_INVALID_NOTE
    notes += scan_note
    if transcript:
        try:
            if matched:
                reason = (VERIFIED_REASON if verified else VERIFIED_BUT_REASON
                          ).format(digest=current)
                if lone and consistent is None:
                    notes += INDEX_UNKNOWN_NOTE.format(why=insp["reason"] or "status unreadable")
                elif lone and not consistent:
                    findings = (insp.get("strict") if kind == "push" else insp.get("strict_commit")) or {}
                    if kind == "push":
                        notes += INDEX_NOTE_PUSH.format(n=len(findings), sample=_strict_sample(findings))
                    else:
                        notes += INDEX_NOTE_COMMIT.format(n=len(findings), sample=_strict_sample(findings))
                elif lone and kind == "push" and not range_ok:
                    if range_why:
                        notes += RANGE_UNKNOWN_NOTE.format(why=range_why)
                    else:
                        if ahead is not None and ahead > 1:
                            notes += RANGE_NOTE_AHEAD.format(n=ahead, tracking=tracking)
                        if behind:
                            notes += RANGE_NOTE_BEHIND.format(n=behind, tracking=tracking)
                elif lone and kind == "push" and not lease_ok:
                    notes += LEASE_NOTE.format(tracking=tracking, oid=tracking_oid, dst=info.get("dst", ""),
                                               remote=info.get("remote", ""))
                elif lone and hooks:
                    if kind == "commit":
                        notes += HOOK_NOTE.format(event="commit-time", names="; ".join(hooks),
                                                  effect="a pre-commit hook can re-stage content after this "
                                                         "inspection and every hook runs code",
                                                  fix="pass --no-verify to skip pre-commit/commit-msg, remove or "
                                                      "disable the rest, or acknowledge knowingly")
                    else:
                        notes += HOOK_NOTE.format(event="push-time", names="; ".join(hooks),
                                                  effect="it runs arbitrary code during the push",
                                                  fix="acknowledge knowingly")
                elif lone and signing:
                    notes += SIGNING_NOTE.format(key=", ".join(signing), program="gpg.program or gpg.<format>.program")
                elif lone and programs:
                    notes += REPO_PROGRAM_NOTE.format(keys=", ".join(programs[:6])
                                                      + (f" … +{len(programs) - 6}" if len(programs) > 6 else ""))
        except Exception:
            pass  # the wording notes are best effort; the decision above is already fixed
    if not direct:
        notes += NOT_DIRECT_NOTE
    elif routed:
        notes += ENV_ROUTED_NOTE.format(names=", ".join(routed))
    elif not lone:
        notes += NOT_LONE_NOTE
        if info.get("why"):
            notes += f" ({'Push' if info.get('form') == 'push' else 'Commit'} form: {info['why']}.)"
    if not _HEX12_RE.fullmatch(current):
        notes += DIGEST_UNKNOWN_NOTE
    nonce, why = _mint_ack(command, current, state)
    if nonce:
        howto = ACK_HOWTO_POSIX if tool_name.lower() in ("", "bash") else ACK_HOWTO_PS
        scope = ACK_SCOPE_TREE if _HEX12_RE.fullmatch(current) else ACK_SCOPE_COMMAND
        notes += howto.format(nonce=nonce, scope=scope)
    else:
        # STORE FAILURE IS A DENY (round 32): a downgrade to "ask" would be
        # auto-approved in some host modes — the guardrail would vanish
        # silently on exactly the misconfigured machines that need it.
        notes += STORE_UNUSABLE_NOTE.format(path=_ack_dir(), why=why or "unknown")
    reason += notes
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
            "additionalContext": reason,
        }
    })


def _deny(reason):
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
        "additionalContext": reason,
    }})


def _read_fd_select(fd, cap, end):
    """POSIX: select() observes the deadline on a pipe or a file."""
    import select as _select
    chunks, total = [], 0
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return b"", "payload stalled past the deadline"
        ready, _, _ = _select.select([fd], [], [], remaining)
        if not ready:
            return b"", "payload stalled past the deadline"
        chunk = os.read(fd, 1 << 16)
        if not chunk:
            return b"".join(chunks), ""
        total += len(chunk)
        if total > cap:
            return b"", f"payload larger than {cap} bytes"
        chunks.append(chunk)


def _read_fd_threaded(fd, cap, end):
    """Windows (round 40): select() does not accept pipe descriptors there,
    and a bare os.read cannot observe a deadline on an open, stalled pipe —
    so a daemon thread does the blocking reads and the caller waits only
    until the deadline. A stalled pipe is reported, never waited on."""
    box = {}

    def pump():
        chunks, total = [], 0
        try:
            while True:
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    box["data"] = b"".join(chunks)
                    return
                total += len(chunk)
                if total > cap:
                    box["why"] = f"payload larger than {cap} bytes"
                    return
                chunks.append(chunk)
        except OSError as exc:
            box["why"] = f"payload unreadable: {type(exc).__name__}"

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t.join(max(0.0, end - time.monotonic()))
    if t.is_alive():
        return b"", "payload stalled past the deadline"
    return box.get("data", b""), box.get("why", "")


def _read_stdin_bounded(cap, deadline_s):
    """The host's payload, read under a CAP and a DEADLINE (round 39: an
    unbounded `json.load(sys.stdin)` ran before any deadline existed, so an
    oversized or stalled payload could exhaust the host's hook timeout — a
    fail-OPEN — before the fail-closed worker existed). Returns (bytes, why)."""
    fd = sys.stdin.fileno()
    end = time.monotonic() + deadline_s
    if os.name == "nt":
        return _read_fd_threaded(fd, cap, end)
    return _read_fd_select(fd, cap, end)


def main() -> int:
    # ONE absolute deadline for the whole hook (round 40: the payload read
    # and the worker each had the full budget — 59 s of reading plus 60 s of
    # evaluation outlived the host's 90 s hook timeout, which fails OPEN).
    budget = _eval_deadline_s()
    end = time.monotonic() + budget
    raw, why = _read_stdin_bounded(PAYLOAD_MAX_BYTES, budget)
    if why:
        print(_deny(PAYLOAD_REASON.format(why=why)))
        return 0
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (ValueError, UnicodeDecodeError) as exc:
        # a payload the hook cannot read is not a plain command it may let
        # through silently (round 39): fail closed, say why
        print(_deny(PAYLOAD_REASON.format(why=f"not a JSON object: {type(exc).__name__}")))
        return 0
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not isinstance(command, str):
        print(_deny(PAYLOAD_REASON.format(why="tool_input.command is not a string")))
        return 0
    if len(command.encode("utf-8", "surrogateescape")) > COMMAND_MAX_BYTES:
        # MANAGED LIMIT: the classifier must finish under the deadline
        print(_deny(PAYLOAD_REASON.format(why=f"command longer than {COMMAND_MAX_BYTES} bytes cannot be classified")))
        return 0
    if not _maybe_git(command):
        return 0  # no push/commit vocabulary survives de-escaping: silent, and nothing below runs
    if "--evaluate" in sys.argv[1:]:
        # the WORKER: full detection AND the decision run here, under the
        # parent's deadline (round 40: detection ran in the parent, outside
        # any bound, and one regex was quadratic)
        try:
            print(_evaluate(data) if _detected(command) else NOT_DETECTED)
        except Exception as exc:
            print(_deny(EVAL_FAILED_REASON.format(why=f"worker crashed: {type(exc).__name__}: {exc}")))
        return 0
    _fault("parent")
    # The PARENT does nothing that can block: it hands the payload to a
    # worker in its own process group, waits at most what is LEFT of the
    # deadline, and on timeout kills the whole group and DENIES (round 32:
    # a hook that outlives the host's timeout fails OPEN, so the only safe
    # place for a hard bound is a process that is itself never blocked).
    remaining = end - time.monotonic()
    if remaining < EVAL_MIN_REMAINING_S:
        print(_deny(EVAL_TIMEOUT_REASON.format(deadline=budget)))
        return 0
    try:
        td = _load_treedigest()
    except Exception as exc:
        print(_deny(EVAL_FAILED_REASON.format(why=f"treedigest unavailable: {exc}")))
        return 0
    # run_contained (round 36): the worker's group is swept by a group id
    # that is STILL RESERVED — the leader is reaped only after the sweep —
    # so a helper it started cannot outlive the hook and a reused pid is
    # never signalled; the payload rides in on a temporary file, never a
    # pipe that could block the parent.
    rc, out, why = td.run_contained(
        [sys.executable, os.path.abspath(__file__), "--evaluate"],
        remaining, cap=1 << 20, payload=json.dumps(data).encode("utf-8"))
    if why == "timeout":
        print(_deny(EVAL_TIMEOUT_REASON.format(deadline=budget)))
        return 0
    if why:
        print(_deny(EVAL_FAILED_REASON.format(why=why)))
        return 0
    text = out.decode("utf-8", "replace").strip()
    if rc != 0 or not text:
        print(_deny(EVAL_FAILED_REASON.format(why=f"worker exit {rc}")))
        return 0
    if text == NOT_DETECTED:
        return 0  # examined under the deadline: not a push/commit — silent
    try:
        json.loads(text)
    except ValueError:
        print(_deny(EVAL_FAILED_REASON.format(why="worker produced no decision")))
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    # NEVER CRASH (round 37): the host treats a hook that exits non-zero
    # with a traceback as a NON-BLOCKING error and runs the command — so
    # every failure past stdin parsing becomes a structured deny on stdout
    # with exit 0 (measured by the review: no writable temporary directory
    # → traceback → push proceeded).
    try:
        _rc = main()
    except BaseException as _exc:  # noqa: BLE001 — the guard exists for the unforeseen
        _rc = 0
        try:
            print(_deny(EVAL_FAILED_REASON.format(why=f"hook crashed: {type(_exc).__name__}: {_exc}")))
        except Exception:
            pass
    sys.exit(_rc)
