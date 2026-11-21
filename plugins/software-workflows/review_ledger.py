"""Dispatch snapshots and completed code reviews for the Git pre-push hook.

A manifest describes the raw worktree as a committed tree plus blob overrides.
The tree ID can be used in another clone. Only a successful code_review publishes
a manifest. The 12-character digest in Oracle answers is informational.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time


OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
MAX_LISTING = 64 << 20
MAX_FILES = 100_000
DAY = 86400
# Only inherit Git's repository routing for the checkout that actually invoked
# pre-push. A review may have been dispatched in an entirely different checkout.
REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
            "GIT_SHALLOW_FILE", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM")


class TooLarge(Exception):
    """A check exceeded its work or time budget."""


class GitError(Exception):
    """A plumbing read failed, without including its argv or environment."""


def log_dir():
    return Path.home() / ".claude" / "logs" / "codex-oracle"


def nonnegative_setting(name, default):
    raw = os.environ.get(name, str(default)).strip()
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"{name} must be a non-negative integer")
    return int(raw)


def nul_records(stream):
    """Iterate NUL-terminated Git records without buffering a large history."""
    tail = b""
    while chunk := stream.read1(65536):
        records = (tail + chunk).split(b"\0")
        tail = records.pop()
        yield from records
        if len(tail) > MAX_LISTING:
            raise TooLarge("oversized Git record")
    if tail:
        raise GitError("incomplete NUL-terminated Git output")


class Git:
    """Read plumbing under the caller's hard process-group deadline."""

    def __init__(self, cwd, executable, *, inherit_repo=False):
        self.cwd = os.path.abspath(cwd)
        self.executable = executable
        self.env = {k: v for k, v in os.environ.items()
                    if k in ("HOME", "PATH", "SystemRoot", "WINDIR", "XDG_CONFIG_HOME",
                             "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM")
                    or (inherit_repo and k in REPO_ENV)}
        self.env.update(LC_ALL="C", GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1",
                        GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="")

    @contextlib.contextmanager
    def output(self, command, *args, feed=None, allowed=(0,)):
        argv = [self.executable, "--no-pager", "--no-replace-objects", "--no-lazy-fetch",
                "--literal-pathspecs", "-c", "core.fsmonitor=", "-c", "core.hooksPath=" + os.devnull,
                command, *args]
        # Stay in the snapshot/check worker's group. Its watchdog can interrupt
        # even a blocked filesystem read and reap Git and the worker together.
        proc = subprocess.Popen(argv, cwd=self.cwd, env=self.env, stdout=subprocess.PIPE,
                                stdin=subprocess.PIPE if feed is not None else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        failures = []

        def write_input():
            try:
                with proc.stdin:
                    proc.stdin.writelines(feed)
            except Exception as exc:
                failures.append(exc)
            finally:
                close = getattr(feed, "close", None)
                if close is not None:
                    close()

        writer = None
        try:
            if feed is not None:
                writer = threading.Thread(target=write_input, daemon=True)
                writer.start()
            with proc.stdout:
                yield proc.stdout
            if proc.wait(timeout=30) not in allowed:
                raise GitError(f"git {command} exited {proc.returncode}")
            if writer is not None:
                writer.join(timeout=2)
                if writer.is_alive():
                    raise GitError("Git stopped reading before its input completed")
            if failures:
                raise failures[0]
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
            if writer is not None:
                writer.join(timeout=1)

    def read(self, command, *args, allowed=(0,), cap=MAX_LISTING):
        with self.output(command, *args, allowed=allowed) as stream:
            data = stream.read(cap + 1)
            if len(data) > cap:
                raise TooLarge(f"git {command} listing exceeds {cap} bytes")
            return data

    def objects(self, queries):
        """One batch for any number of trees and paths, including whitespace and LF."""
        feed = (query + b"\0" for query in queries())
        with self.output("cat-file", "--batch-check=%(objectname) %(objecttype)", "-Z", feed=feed) as stream:
            rows = nul_records(stream)
            for query in queries():
                row = next(rows, None)
                if row == query + b" missing":
                    yield query, None
                    continue
                oid, _, kind = (row or b"").partition(b" ")
                if not OID.fullmatch(oid) or kind not in (b"blob", b"tree", b"commit", b"tag"):
                    raise GitError("invalid cat-file response")
                yield query, (oid, kind)
            if next(rows, None) is not None:
                raise GitError("extra cat-file response")

    def roots(self, *tips):
        out = self.read("rev-list", "--max-parents=0", "--all", *tips)
        roots = set(out.splitlines())
        if not all(OID.fullmatch(root) for root in roots):
            raise GitError("invalid root commit ID")
        return sorted(root.decode("ascii") for root in roots)

    def tree_entries(self, tree):
        with self.output("ls-tree", "-rz", "--full-tree", tree) as stream:
            for record in nul_records(stream):
                meta, path = record.split(b"\t", 1)
                mode, kind, oid = meta.split()
                yield path, kind, oid


def _worktree_file(path, algo):
    """Return (digest component, blob ID or None). Never run a Git filter."""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return b"missing", None
    if stat.S_ISLNK(st.st_mode):
        target = os.fsencode(os.readlink(path))
        blob = algo(b"blob %d\0" % len(target) + target).hexdigest()
        return b"l\0" + target, blob
    if stat.S_ISDIR(st.st_mode):
        return b"d", None
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("special file in the worktree")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("worktree file type changed while being opened")
        blob = algo(b"blob %d\0" % st.st_size)
        content = hashlib.sha256()
        size = 0
        while chunk := fh.read(1 << 20):
            size += len(chunk)
            if size > st.st_size:
                raise ValueError("worktree file grew while being read")
            blob.update(chunk)
            content.update(chunk)
        after = os.fstat(fh.fileno())
        if (size, after.st_mtime_ns, after.st_ctime_ns) != (st.st_size, st.st_mtime_ns, st.st_ctime_ns):
            raise ValueError("worktree file changed while being read")
    mode = b"x" if st.st_mode & 0o100 else b"-"
    return b"f" + mode + b"\0" + content.digest(), blob.hexdigest()


def worktree_snapshot(cwd, git_exe, review=True):
    """Read the informational digest and (for code_review) the effective dispatch tree."""
    git = Git(cwd, git_exe)
    top = git.read("rev-parse", "--show-toplevel", allowed=(0, 128)).removesuffix(b"\n")
    if not top:
        # Reviews also work outside Git. Their empty root set cannot match a push.
        manifest = {"toplevel": None, "head": None, "tree": None, "roots": [], "dirty": {}}
        return {"digest": "nogit", "manifest": manifest if review else None}
    top = os.path.realpath(os.fsdecode(top))
    git = Git(top, git_exe)
    tracked = git.read("ls-files", "--stage", "-z")
    paths, gitlinks = set(), {}
    for entry in tracked.split(b"\0"):
        if entry:
            meta, rel = entry.split(b"\t", 1)
            mode, oid, _stage = meta.split()
            paths.add(rel)
            if mode == b"160000":
                gitlinks[rel] = oid
    paths.update(filter(None, git.read("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")))
    manifest, base = None, {}
    algo = hashlib.sha1
    if review:
        head = git.read("rev-parse", "--verify", "-q", "HEAD^{commit}", allowed=(0, 1)).strip()
        tree = git.read("rev-parse", head + b"^{tree}").strip() if head else b""
        fmt = git.read("rev-parse", "--show-object-format").strip()
        algo = {b"sha1": hashlib.sha1, b"sha256": hashlib.sha256}[fmt]
        if tree:
            for rel, kind, oid in git.tree_entries(tree):
                if kind == b"blob":
                    base[rel] = oid.decode("ascii")
                if len(base) > MAX_FILES:
                    raise TooLarge("too many paths in HEAD")
        manifest = {"toplevel": top, "head": head.decode("ascii") or None,
                    "tree": tree.decode("ascii") or None,
                    "roots": git.roots(*([head] if head else [])), "dirty": {}}

    entries = paths | base.keys()
    if len(entries) > MAX_FILES:
        raise TooLarge("too many worktree paths")
    digest, first = hashlib.sha256(), True
    for rel in sorted(entries):
        parts = rel.split(b"/")
        if (os.path.isabs(rel) or os.path.splitdrive(rel)[0]
                or any(part in (b".", b"..", b".git") for part in parts)
                or (os.name == "nt" and b"\\" in rel)):
            raise GitError("Git listed a path outside the worktree")
        file = os.path.join(top, os.fsdecode(rel))
        # A tracked directory can now be a symlink or a gitlink. Its former
        # descendants are absent from this tree, even if the target has files.
        parent = os.path.dirname(file)
        inside_link = any(b"/".join(parts[:end]) in gitlinks for end in range(1, len(parts)))
        blob = None
        if inside_link or os.path.realpath(parent) != parent:
            part = b"missing"
        elif rel in gitlinks:
            subhead = b"absent"
            if os.path.isdir(file) and not os.path.islink(file) and os.path.lexists(os.path.join(file, ".git")):
                subhead = Git(file, git_exe).read("rev-parse", "--verify", "HEAD").strip()
            part = b"g\0" + gitlinks[rel] + b"\0" + subhead
        else:
            part, blob = _worktree_file(file, algo)
        if rel in paths:
            if not first:
                digest.update(b"\0")
            digest.update(rel + b"\0" + part)
            first = False
        if manifest is not None and blob != base.get(rel):
            manifest["dirty"][os.fsdecode(rel)] = blob
    if review and git.read("rev-parse", "--verify", "-q", "HEAD^{commit}", allowed=(0, 1)).strip() != head:
        raise ValueError("HEAD moved while capturing the review")
    return {"digest": digest.hexdigest()[:12], "manifest": manifest}


def dispatch_snapshot(cwd, git_exe, review, run_contained):
    """Capture in one bounded child so an inaccessible mount cannot stall the Oracle."""
    argv = [sys.executable, "-I", os.path.abspath(__file__), os.path.abspath(cwd),
            git_exe, "review" if review else "digest"]
    rc, out, why = run_contained(argv, 23, cap=MAX_LISTING, cwd=os.path.dirname(git_exe))
    if rc or why:
        raise RuntimeError(f"dispatch snapshot failed ({why or f'exit {rc}'})")
    result = json.loads(out)
    if "error" in result:
        raise ValueError(result["error"])
    return result


def write_review(run_id, manifest, *, tool, status):
    """Atomically publish the dispatch snapshot only after the review completed."""
    if tool != "code_review" or status != "ok":
        return
    if not run_id or run_id in (".", "..") or "/" in run_id or "\\" in run_id:
        raise ValueError("invalid review run ID")
    directory = log_dir() / "reviews"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    now = time.time()
    entry = {"time": now, "run_id": run_id, "tool": tool, "status": status, "manifest": manifest}
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".review-")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            json.dump(entry, fh, ensure_ascii=True, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        # A second collector of the same result cannot refresh its review window.
        with contextlib.suppress(FileExistsError):
            os.link(tmp, directory / (run_id + ".json"))
    finally:
        Path(tmp).unlink(missing_ok=True)
    for path in directory.glob("*.json"):
        with contextlib.suppress(FileNotFoundError):
            if path.stat().st_mtime < now - 30 * DAY:
                path.unlink()


def load_reviews(roots):
    window = nonnegative_setting("CODEX_REVIEW_WINDOW_DAYS", 14)
    now = time.time()
    cutoff = now - window * DAY
    reviews = []
    for path in (log_dir() / "reviews").glob("*.json"):
        try:
            with path.open("rb") as fh:
                data = fh.read(MAX_LISTING + 1)
            if len(data) > MAX_LISTING:
                raise TooLarge("oversized review ledger entry")
            entry = json.loads(data)
        except FileNotFoundError:  # concurrent pruning
            continue
        if (entry["tool"] == "code_review" and entry["status"] == "ok" and entry["manifest"] is not None
                and cutoff <= entry["time"] <= now and roots.intersection(entry["manifest"]["roots"])):
            reviews.append(entry)
    return sorted(reviews, key=lambda entry: entry["time"], reverse=True)


def coverage(git, pairs, reviews):
    """Return unreviewed (path, blob) versions and the IDs of reviews that supplied matches."""
    missing, used = set(pairs), set()
    groups = {}
    for review in reviews:
        manifest = review["manifest"]
        for path, blob in manifest["dirty"].items():
            pair = (path, blob)
            if pair in missing:
                missing.remove(pair)
                used.add(review["run_id"])
        if manifest["tree"]:
            groups.setdefault(manifest["tree"].encode("ascii"), []).append(review)

    def check_trees(repo, trees):
        paths = sorted({path for path, _blob in missing})
        unavailable = set()

        def queries():
            for tree in trees:
                if not OID.fullmatch(tree):
                    raise ValueError("invalid manifest tree ID")
                yield tree
                for path in paths:
                    yield tree + b":" + os.fsencode(path)

        def credit(path, blob, tree):
            pair = (path, blob)
            if pair in missing:
                for review in groups[tree]:
                    if path not in review["manifest"]["dirty"]:
                        missing.remove(pair)
                        used.add(review["run_id"])
                        break

        # 'tree:path missing' can also mean a missing subtree or blob in a
        # partial clone. Before crediting an absence, verify the tree listing.
        absences = {}
        for query, obj in repo.objects(queries):
            tree, sep, raw = query.partition(b":")
            if not sep:
                if obj is None:
                    unavailable.add(tree)
                elif obj != (tree, b"tree"):
                    raise GitError("manifest HEAD tree is not a tree")
            elif tree not in unavailable:
                path = os.fsdecode(raw)
                if obj is None:
                    if (path, None) in missing:
                        absences.setdefault(tree, set()).add(path)
                else:
                    credit(path, obj[0].decode("ascii") if obj[1] == b"blob" else None, tree)
        for tree, absent in absences.items():
            for raw, kind, _oid in repo.tree_entries(tree):
                if kind == b"blob":
                    absent.discard(os.fsdecode(raw))
            for path in absent:
                credit(path, None, tree)
        return unavailable

    if missing and groups:
        unavailable = check_trees(git, groups)
        origins = {}
        for tree in unavailable:
            for review in groups[tree]:
                origins.setdefault(review["manifest"]["toplevel"], set()).add(tree)
        for top, trees in origins.items():
            candidates = trees & unavailable
            if missing and candidates and os.path.isdir(top):
                still_missing = check_trees(Git(top, git.executable), candidates)
                unavailable.difference_update(candidates - still_missing)
    return missing, used


if __name__ == "__main__":
    try:
        snapshot = worktree_snapshot(sys.argv[1], sys.argv[2], sys.argv[3] == "review")
    except Exception as exc:
        snapshot = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(snapshot, ensure_ascii=True))
