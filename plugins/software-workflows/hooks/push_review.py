#!/usr/bin/env python3
"""Git pre-push hook for the Claude session's Bash tool on POSIX.

This is a guardrail against forgetting a review, not a security boundary.
Git's --no-verify skips pre-push hooks. Git before 2.54 ignores config hooks.
Windows and PowerShell have no push review gate. Commits do not invoke it.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import sys
import time


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
TTL = 600
SAMPLE = 10
BUDGET = 30


def _peel(ledger, git, lines):
    """Parse Git's ref updates and peel every tip to its commit."""
    updates = []
    for line in lines:
        local_ref, local, remote_ref, old = line.rsplit(b" ", 3)
        if (not local_ref or not remote_ref or not ledger.OID.fullmatch(local)
                or not ledger.OID.fullmatch(old) or len(local) != len(old)):
            raise ValueError("invalid pre-push ref update")
        if local.strip(b"0"):
            updates.append((local, old))
    if not updates:
        return [], {}
    # Batch-peel the tips. A non-commit tag (or an existing non-commit old
    # object) cannot prove a commit range and is refused explicitly.
    oids = {oid for update in updates for oid in update if oid.strip(b"0")}
    peeled = {}

    def queries():
        for oid in sorted(oids):
            yield oid
            yield oid + b"^{commit}"

    objects = dict(git.objects(queries))
    for oid in oids:
        if objects[oid] is not None:
            commit = objects[oid + b"^{commit}"]
            if commit is None or commit[1] != b"commit":
                raise ValueError("a pushed ref does not peel to a commit")
            peeled[oid] = commit[0]
    for local, _old in updates:
        if local not in peeled:
            raise ValueError("local ref object is missing")
    return updates, peeled


def _reviewed_heads(ledger, git, reviews):
    """The HEAD commits of the considered reviews that exist in this repository. A commit in the
    history of a reviewed HEAD was in front of that review, as HEAD's own content always was: the
    lead may commit first and review the committed range (SmartPay, 2026-11-21)."""
    heads = sorted({r["manifest"]["head"].encode("ascii") for r in reviews if r["manifest"].get("head")})
    if not heads:
        return []
    return sorted(h for h, obj in git.objects(lambda: iter(heads)) if obj is not None and obj[1] == b"commit")


def _pairs(ledger, git, name, url, updates, peeled, heads=()):
    """Walk each updated range, minus the history of reviewed HEADs, then ask Git for all its raw
    diffs in one process. Returns (pairs, commits checked, commits skipped as reviewed history, whether
    that skipped count is exact)."""
    ranges = set(updates)
    def revs_for(local, old):
        # A new ref, or an old tip this repository lacks: nothing is known about what the destination
        # already has. Remote-tracking refs are not evidence of it (they describe the fetch side, can
        # outlive a deletion on the server, and a push URL can differ), so only the reviewed HEADs'
        # history is subtracted (review of 2.5.1, turns 9-11).
        if old in peeled:
            return [peeled[old] + b".." + peeled[local]]
        return [peeled[local]]

    # How many commits of this push were in a reviewed HEAD's history, for the report only: Git counts
    # each range with and without that history, so nothing is held in memory however long the history
    # (review of 2.5.1, turn 13: a new ref of 1.6 M commits overflowed the listing cap). One range gives
    # the exact count; across several, overlapping ranges would be counted twice, so the largest single
    # count is reported as a lower bound.
    skipped, exact = 0, True
    if heads:
        per_range = []
        for local, old in ranges:
            revs = revs_for(local, old)
            whole = int(git.read("rev-list", "--count", *revs, "--"))
            rest = int(git.read("rev-list", "--count", *revs, "--not", *heads, "--"))
            per_range.append(max(0, whole - rest))
        skipped, exact = max(per_range, default=0), len(per_range) <= 1
    checked = 0

    def commit_ids():
        nonlocal checked
        seen = set()
        for local, old in ranges:
            revs = revs_for(local, old)
            if heads:
                revs = revs + ["--not"] + list(heads)
            with git.output("rev-list", *revs, "--") as stream:
                for row in stream:
                    commit = row.rstrip(b"\n")
                    if not ledger.OID.fullmatch(commit):
                        raise ValueError("invalid rev-list output")
                    if commit in seen:
                        continue
                    seen.add(commit)
                    checked += 1
                    yield row

    limit = ledger.nonnegative_setting("CODEX_PUSH_GATE_MAX_PAIRS", 50000)
    pairs = set()
    count = 0
    with git.output("diff-tree", "--stdin", "--root", "-r", "-c", "--raw", "-z",
                    "--no-commit-id", "--no-abbrev", "--no-textconv", "--no-ext-diff",
                    "--no-renames", "--ignore-submodules=none", feed=commit_ids()) as stream:
        fields = ledger.nul_records(stream)
        for header in fields:
            path = next(fields, None)
            meta = header.lstrip(b":")
            parents = len(header) - len(meta)
            parts = meta.split()
            if not parents or len(parts) != 2 * (parents + 1) + 1 or not path:
                raise ValueError("invalid raw diff-tree record")
            modes = parts[:parents + 1]
            oid = parts[-2]
            if not ledger.OID.fullmatch(oid):
                raise ValueError("invalid blob ID in diff-tree output")
            if modes[-1] == b"160000" or (modes[-1] == b"000000" and set(modes[:-1]) == {b"160000"}):
                if modes[-1] == b"160000" and any(m not in (b"160000", b"000000") for m in modes[:-1]):
                    # a file or symlink replaced by a submodule: the submodule is not a file of the
                    # reviewed checkout, but the file's removal is (review of 2.5.1, turn 15)
                    count += 1
                    if count > limit:
                        raise ledger.TooLarge(f"more than {limit} changed (path, blob) pairs")
                    pairs.add((os.fsdecode(path), None))
                continue  # gitlink, not a file in the reviewed checkout
            if parents > 1 and oid in parts[parents + 1:-2]:
                continue  # merge content already present in a parent (e.g. a mode-only resolution)
            count += 1
            if count > limit:
                raise ledger.TooLarge(f"more than {limit} changed (path, blob) pairs")
            pairs.add((os.fsdecode(path), oid.decode("ascii") if oid.strip(b"0") else None))
    return pairs, checked, skipped, exact


def _check(git_exe, name, url, lines):
    result = {"repo": os.getcwd(), "uncovered_count": None, "uncovered_sample": [],
              "reviews_considered": 0}
    ledger = _module("sw_review_ledger", HERE.parent / "review_ledger.py")
    try:
        git = ledger.Git(os.getcwd(), git_exe, inherit_repo=True)
        top = git.read("rev-parse", "--show-toplevel", allowed=(0, 128)).removesuffix(b"\n")
        if not top:  # a bare repository can push too
            top = git.read("rev-parse", "--absolute-git-dir").removesuffix(b"\n")
        result["repo"] = os.fsdecode(top)
        updates, peeled = _peel(ledger, git, lines)
        tips = {peeled[local] for local, _old in updates}
        reviews = ledger.load_reviews(set(git.roots(*tips))) if tips else []
        heads = _reviewed_heads(ledger, git, reviews)
        pairs, checked, skipped, exact = _pairs(ledger, git, name, url, updates, peeled, heads)
        result["reviews_considered"] = len(reviews)
        # `used` counts the reviews whose content covered a changed path; reviewed history is reported
        # by its commit count only (crediting every reviewed HEAD overstated it; review of 2.5.1, turn 14)
        missing, used = ledger.coverage(git, pairs, reviews)
        uncovered = sorted({path for path, _blob in missing})
        result.update(uncovered_count=len(uncovered), uncovered_sample=uncovered[:SAMPLE],
                      changed_paths=len({path for path, _blob in pairs}), used_reviews=len(used),
                      checked_commits=checked, reviewed_history_commits=skipped,
                      reviewed_history_exact=exact)
    except Exception as exc:
        if isinstance(exc, (TimeoutError, ledger.TooLarge)):
            result["too_large"] = True
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _ack_dir():
    return Path(os.environ.get("CODEX_PUSH_ACK_DIR") or
                Path.home() / ".claude" / "logs" / "codex-oracle" / "push-ack")


def _mint(binding):
    directory = _ack_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = time.time()
    for path in directory.glob("*.json"):
        if re.fullmatch(r"[0-9a-f]{16}\.json", path.name):
            with contextlib.suppress(FileNotFoundError):
                if now - path.lstat().st_mtime > TTL:
                    path.unlink()
    nonce = secrets.token_hex(8)
    fd = os.open(directory / (nonce + ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        json.dump({"binding": binding, "ts": now}, fh)
    return nonce


def _consume(nonce, binding):
    if binding is None or not re.fullmatch(r"[0-9a-f]{16}", nonce):
        return False
    token = _ack_dir() / (nonce + ".json")
    try:
        fd = os.open(token, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
                return False
            record = json.loads(fh.read(4096))
        if record["binding"] != binding or not 0 <= time.time() - record["ts"] <= TTL:
            return False
        # Unlink is the claim. Of concurrent readers of this valid token,
        # only the one that actually removes its name may pass.
        token.unlink()
        return True
    except FileNotFoundError:
        return False


@contextlib.contextmanager
def _io_deadline(seconds):
    """Bound the hook's own stdin and state I/O, including the notice file lock."""
    def expired(*_):
        raise TimeoutError("I/O deadline reached")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _audit_url(url):
    # Preserve the destination, never an inline transport command, URL password
    # or signed query string. The token binding still uses every original byte.
    if url.startswith("ext::"):
        return "ext::<command omitted>"
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    base, query, _ = rest.partition("?")
    authority, slash, path = base.partition("/")
    userinfo, at, host = authority.rpartition("@")
    if at and (scheme.lower() in ("http", "https") or ":" in userinfo):
        authority = host
    return scheme + sep + authority + slash + path + ("?<redacted>" if query else "")


def _audit(record):
    directory = Path.home() / ".claude" / "logs" / "codex-oracle"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
    fd = os.open(directory / "push-log.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short write to push-log.jsonl")
    finally:
        os.close(fd)


def _decision(check, nonce, reason, binding):
    if reason.strip():
        return "unreviewed-with-reason", "push gate: proceeding with the stated reason"
    if nonce:
        if _consume(nonce, binding):
            return "acknowledged", "push gate: acknowledged these ref updates"
        return "refused", "push gate: invalid, used, expired, or mismatched acknowledgement"
    if check.get("error"):
        if check.get("too_large"):
            return "refused", "push gate: too large to check (" + check["error"] + ")"
        return "error", "push gate: review check failed (" + check["error"] + ")"
    if check["uncovered_count"]:
        return "refused", "push gate: review coverage incomplete"
    count, reviews = check["changed_paths"], check["used_reviews"]
    history = check.get("reviewed_history_commits") or 0
    checked = check.get("checked_commits") or 0
    at_least = "" if check.get("reviewed_history_exact", True) else "at least "
    in_reviews = f"({reviews} review{'s' if reviews != 1 else ''})"
    if not count and not history:
        if checked:
            return "covered", f"push gate: the {checked} commit{'s' if checked != 1 else ''} in this push change no file"
        return "covered", "push gate: nothing to check (no commits the remote lacks)"
    if not count:
        rest = (f"; the other {checked} commit{'s' if checked != 1 else ''} change{'' if checked != 1 else 's'} "
                "no file") if checked else ""
        return "covered", (f"push gate: {at_least}{history} commit{'s' if history != 1 else ''} in this push were in "
                           f"the history of a completed Codex review's HEAD{rest}")
    note = (f"; {at_least}{history} more commit{'s' if history != 1 else ''} were in a reviewed HEAD's history"
            if history else "")
    return "covered", (f"push gate: all {count} changed path{'s' if count != 1 else ''} in this push were in "
                       f"completed Codex reviews {in_reviews}{note}")


def _stderr(message):
    # A closed or redirected stderr must not change the hook's exit status.
    with contextlib.suppress(OSError):
        print(message, file=sys.stderr, flush=True)


def main(git_exe, name, url):
    # Bind the raw protocol bytes, including update order. No command text,
    # ambient config, checkout path or other writer's worktree enters it.
    started = time.monotonic()
    binding, lines = None, []
    reason = os.environ.get("CODEX_PUSH_UNREVIEWED", "")
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    repo = None
    with contextlib.suppress(OSError):
        repo = os.getcwd()
    check = {"repo": repo, "reviews_considered": 0, "uncovered_count": None,
             "uncovered_sample": []}
    try:
        with _io_deadline(BUDGET):
            raw = sys.stdin.buffer.read()
            lines = raw.split(b"\n")
            if lines and not lines[-1]:
                lines.pop()
            binding = hashlib.sha256(os.fsencode(url) + b"\0" + raw).hexdigest()
        td = _module("sw_process_group", HERE.parent / "treedigest.py")
        argv = [sys.executable, "-I", str(HERE / "push_review.py"), "--check", git_exe, name, url]
        remaining = max(0.0, BUDGET - (time.monotonic() - started))
        rc, out, why = td.run_contained(argv, remaining, cap=1 << 20, payload=raw, grace_s=0.1)
        if why == "timeout":
            check.update(error=f"{BUDGET}s deadline reached", too_large=True)
        elif rc or why:
            check["error"] = f"check worker failed ({why or f'exit {rc}'})"
        else:
            check.update(json.loads(out))
    except Exception as exc:
        check.update(error=f"{type(exc).__name__}: {exc}", too_large=isinstance(exc, TimeoutError))

    try:
        with _io_deadline(2):
            decision, message = _decision(check, os.environ.get("CODEX_PUSH_ACK", ""), reason, binding)
    except Exception as exc:
        decision, message = "error", f"push gate: acknowledgement check failed ({exc})"

    try:
        with _io_deadline(2):
            _audit({"time": time.time(), "session_id": session, "repo": check["repo"], "remote_url": _audit_url(url),
                    "refs": [os.fsdecode(line) for line in lines], "decision": decision,
                    "uncovered_count": check["uncovered_count"], "uncovered_sample": check["uncovered_sample"],
                    "reviews_considered": check["reviews_considered"], "reason": reason})
    except Exception as exc:
        decision, message = "error", f"push gate: could not append push-log.jsonl ({exc})"
    if decision in ("covered", "acknowledged", "unreviewed-with-reason"):
        _stderr(message)
        return 0

    count = check["uncovered_count"]
    if count:
        sample = ", ".join(ascii(path) for path in check["uncovered_sample"])
        message += f"\n{count} uncovered path{'s' if count != 1 else ''}: {sample}"
    reviews = check["reviews_considered"]
    message += f"\nConsidered {reviews} completed Codex review{'s' if reviews != 1 else ''}."
    if not check.get("error") and decision != "error":
        message += "\nRun code_review on the change and push again."
    token = ""
    if binding is not None:
        try:
            with _io_deadline(2):
                token = _mint(binding)
            message += f"\nOr re-run the same push with CODEX_PUSH_ACK={token} git push …"
        except Exception as exc:
            message += f"\nCould not issue an acknowledgement ({exc})."
    message += '\nOr give the reason up front: CODEX_PUSH_UNREVIEWED="<reason>" git push …'
    try:
        with _io_deadline(2):
            notices = _module("sw_gate_notice", HERE / "gate_notice.py")
            notices.write_notice(session, token, message)
    except Exception as exc:
        message += f"\nCould not store the Bash notice ({exc})."
    _stderr(message)
    return 1


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--check":
        data = sys.stdin.buffer.read().split(b"\n")
        if data and not data[-1]:
            data.pop()
        print(json.dumps(_check(*sys.argv[2:], data), ensure_ascii=True))
    elif len(sys.argv) == 5 and sys.argv[1] == "--git":
        sys.exit(main(*sys.argv[2:]))
    elif len(sys.argv) == 3:
        sys.exit(main(shutil.which("git", path=os.defpath) or "git", *sys.argv[1:]))
    else:
        sys.exit("push gate: expected [--git <executable>] <remote name> <remote url>")
