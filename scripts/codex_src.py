#!/usr/bin/env python3
"""Keep a read-only EXPORT of the codex source at the INSTALLED binary's release tag.

Why this exists (the map-vs-territory rule)
-------------------------------------------
Upstream source is the MAP: it names the config keys, event types and
mechanisms worth probing. The INSTALLED binary is the TERRITORY: only it
decides what is true at runtime. Reading the clone's main-branch HEAD has
already produced wrong conclusions twice in this repo's history — main and
the release tags are DIVERGENT (releases are cut on branches), so main
contains code the deployed binary does not have, and vice versa.

This script keeps ONE stable path — ~/Documents/codex-installed by default —
pointing at a read-only export of exactly `rust-v<installed version>`,
re-exported whenever the codex CLI is updated. Run it before any session that
reads upstream source; it is idempotent and safe to run any time.

    python3 scripts/codex_src.py            # export, or re-export after a codex update
    CODEX_SRC_CLONE=...  the reference clone (default ~/Documents/codex)
    CODEX_SRC_DIR=...    the stable path (default ~/Documents/codex-installed;
                         CODEX_SRC_WORKTREE is still read as its old name)

Layout (user rule 2026-10-16: no worktrees, ever; a pristine copy of a commit
is a read-only export):

    <dir>                                symlink → the current export
    .<dir name>.exports/<tag>-<sha12>/   one read-only export per release
    .<dir name>.exports/.lock            serializes runs

An export is the commit's blobs written verbatim from `git cat-file --batch`
— not `git archive`, which drops export-ignore paths and rewrites
export-subst files, and not a checkout, which applies eol and filter
attributes — so it holds every file of the release byte for byte, with its
executable bit, and its symlinks as symlinks. Everything is created through
directory descriptors with O_NOFOLLOW and O_EXCL: nothing is written through
a symlink, and a path the filesystem folds onto another one (case, Unicode)
fails the export instead of overwriting it; so does a symlink that is
absolute or resolves outside the export. Before it is published the export
is checked against the commit on disk — content, type, mode, no extra paths
— and it carries a stamp, `.codex-src.json`, naming the tag and commit. A
rerun re-checks it; a damaged export is rebuilt beside it and swapped in.

Nothing is deleted on the strength of its name. A tree goes only entry by
entry: each entry is first moved to a name only this run knows, then checked
against the commit its name records, git supplying the bytes — a blob the
clone has lost leaves the copy here the only one (the stamp: byte for byte
against what this script writes for that commit) — and deleted only if it is
still the very entry checked; so the script deletes only what git can
recreate, and whatever is saved at an old name meanwhile is never touched.
The one file that need not be whole is the one a build of this run was writing
when it failed, while it holds exactly the bytes that reached it: a file a
killed run cut short cannot be told from an edit afterwards, so it is kept like
one. Git records bytes, a mode and a type and nothing else, so an entry carrying
an extended attribute — a resource fork, a user's tag or note — is kept as well
(on macOS every entry also carries com.apple.provenance, which the system sets
and no process can: that one is not data; on Linux the security and system
namespaces hold labels, capabilities and ACLs — permissions, like modes). An
entry whose attributes cannot be read is kept the same way; on Linux, where the
trusted namespace is listed only to a process privileged in the initial user
namespace, nothing is deleted at all. A link this script made that carries one is kept aside too, as
`.<dir name>.kept-<time>` beside the stable path, never deleted. Whatever git cannot recreate (an
edit, a file someone added, a cut-short write, an extended attribute, a tree
whose commit or blobs the clone no longer has) is moved aside as
`.<export>.kept-<time>` and named, never deleted — and what was kept, and
anything else in the exports directory this script did not make, is named on
every run, an aligned one included, until someone removes it. No rename replaces anything
(renameatx_np with RENAME_EXCL on macOS, renameat2 with RENAME_NOREPLACE on
Linux): a put-back that would is refused instead, naming both entries. The
stable symlink is created exclusively; an existing one is moved aside and
checked to be the very link this run inspected before anything replaces it.
Exports are kept by identity as well as by name: a name the filesystem folds
onto a kept one (case) is that very export. And the link — a new one, or the
one an aligned run found — stays only if a reader following it, after the
cleanup, reaches the export this run verified.

What no script can promise: a process of the same user racing on purpose can
still swap an entry in the instant between the last check and the delete, or
replace the exports after the link is checked — POSIX has no unlink-if-
unchanged. The windows left are that instant, on names only this run knows,
and the path a reader follows after publication. A stamp edited into exactly
the form this script writes (another well-formed time) cannot be told from one
it wrote.

Switching versions keeps the export it replaces, so a session that already
resolved the old path keeps reading it; older exports and what killed runs
left go by the rule above. A lookup racing the switch can fail for a moment:
between the link moving aside and the new one appearing there is no link at
all — and even one rename is not atomic for such lookups on macOS: measured
2026-10-18 on APFS, a reader polling in a tight loop while a link was
replaced 20,000 times saw 2,988 EINVAL failures in 349,736 lookups; swapping
directories (old one renamed away, new one in) failed 887,379 of 1,213,821.

The reference clone is only ever fetched (`git fetch --tags`, which writes
git's own fetch state there), never checked out (other sessions pin its
HEAD). GIT_DIR and the other repository-local git variables (`git rev-parse
--local-env-vars`) are cleared first, so none inherited can point the export
at another repository. Anything at the stable path this script did not make is refused
untouched — including the worktree older versions of this script kept there:
this script never deletes a worktree.
Exit 0 = aligned (or already aligned); exit 1 = refused or failed, with the reason.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable, Iterator, NoReturn

try:
    import fcntl
except ImportError:  # not POSIX: refused in require_platform()
    fcntl = None

STAMP = ".codex-src.json"
TOOL = "scripts/codex_src.py"
# The stamp's fields in the order this script writes them; the 2026-10-18 04:50 version
# (git archive) also wrote the paths export-ignore / export-subst had touched.
STAMP_ORDER = ("tag", "commit", "files", "codex_cli", "exported_at", "tool")
STAMP_ORDER_ARCHIVE = ("tag", "commit", "files", "codex_cli", "export_ignored", "export_substituted",
                       "exported_at", "tool")
EXPORTED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}")
EXPORT_NAME =re.compile(r"rust-v\d+\.\d+\.\d+[0-9A-Za-z.+-]*-(?P<sha12>[0-9a-f]{12})")
# what runs leave beside an export: a half-built one (tmp), a replaced one being
# checked for deletion (old), and what git could not recreate (kept)
BESIDE = re.compile(rf"\.(?P<export>{EXPORT_NAME.pattern})\.(?P<kind>tmp|old|kept)-[0-9A-Za-z-]+")
RO_FILE, RO_EXEC, IN_PROGRESS = 0o444, 0o555, 0o600
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
O_DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW
O_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW
O_READ = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | _NOFOLLOW  # a FIFO planted in the tree never blocks a read

Entries = list[tuple[bytes, str, str]]  # (path, kind, object id); kind: file, exec, link, gitlink


def _exclusive_rename():
    """The C rename that never replaces — macOS renameatx_np(RENAME_EXCL), Linux
    renameat2(RENAME_NOREPLACE) — with its flag, or None. os.rename silently replaces
    whatever holds the destination; this fails EEXIST instead."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None
    for fname, flag in (("renameatx_np", 0x4), ("renameat2", 0x1)):
        fn = getattr(libc, fname, None)
        if fn is not None:
            fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            fn.restype = ctypes.c_int
            return fn, flag
    return None


_RENAME_EXCL = _exclusive_rename()


_OS_LISTXATTR = getattr(os, "listxattr", None)  # Linux; Python's os has no xattr calls on macOS


def _attribute_lister():
    """How to list an open descriptor's extended attributes here: os.listxattr where Python
    has it (Linux), flistxattr through ctypes on macOS, else None."""
    if _OS_LISTXATTR is not None:
        return _OS_LISTXATTR
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None
    fn = getattr(libc, "flistxattr", None)
    if fn is None:
        return None
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
    fn.restype = ctypes.c_ssize_t

    def listing(fd: int) -> list[str]:
        while True:
            size = fn(fd, None, 0, 0)
            if size > 0:
                buf = ctypes.create_string_buffer(size)
                size = fn(fd, buf, size, 0)
                if size >= 0:
                    return [os.fsdecode(n) for n in buf.raw[:size].split(b"\0") if n]
            if size == 0:
                return []
            err = ctypes.get_errno()
            if err != errno.ERANGE:  # ERANGE: the list grew between the two calls; ask again
                raise OSError(err, os.strerror(err))
    return listing


_LIST_ATTRIBUTES = _attribute_lister()


# Can this process list every extended attribute an entry may carry? On Linux the trusted
# namespace is listed only to a process holding CAP_SYS_ADMIN in the INITIAL user namespace
# (ext4 checks capable(), xattr(7)); one holding it in a child user namespace sees CapEff set
# and still gets no trusted names, so no check from inside the process settles it: on Linux,
# no — an entry carrying one cannot be told from an entry carrying none, and nothing is deleted.
_SEES_EVERY_ATTRIBUTE = sys.platform == "darwin"
UNSEEN = "(attributes this process cannot list)"


def foreign_attributes(names: list[str]) -> list[str]:
    """The extended attributes among `names` that git cannot recreate. On macOS that is all
    but com.apple.provenance, which the system attaches to every entry by itself and no
    process can set (measured 2026-10-18, macOS 26.6: a user's write or delete of it exits 0
    and changes nothing); a resource fork is com.apple.ResourceFork. On Linux all but the
    security and system namespaces, which hold what the system assigns — labels,
    capabilities, ACLs: permissions, like modes. user, trusted and any namespace added later
    can hold data (xattr(7)). The trusted namespace is listed only to a process privileged
    in the initial user namespace, so on Linux attributes() always adds UNSEEN and nothing
    is deleted (Linux is not measured here: macOS only)."""
    if sys.platform == "darwin":
        return sorted(n for n in names if n != "com.apple.provenance")
    return sorted(n for n in names if not n.startswith(("security.", "system.")))


def attributes(fd: int) -> list[str]:
    """foreign_attributes() of the open descriptor. When they cannot all be read — this
    process cannot list some namespace, or the listing fails — the answer is UNSEEN, which
    keeps the entry like an attribute does. That includes ENOTSUP: it can mean attributes
    are switched off rather than absent (a CIFS mount returns it without asking the server,
    which may hold some), so an entry is deleted only once its attributes are known."""
    assert _LIST_ATTRIBUTES is not None  # require_platform() refused its absence
    try:
        names = foreign_attributes(_LIST_ATTRIBUTES(fd))
    except OSError:
        return [UNSEEN]
    return names if _SEES_EVERY_ATTRIBUTE else [*names, UNSEEN]


def link_attributes(dir_fd: int, name: str) -> list[str]:
    """attributes() of the symlink `name` itself, never its target; UNSEEN when they
    cannot be read without following it."""
    if hasattr(os, "O_SYMLINK"):  # macOS opens the link itself
        try:
            fd = os.open(name, os.O_RDONLY | os.O_SYMLINK | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
        except OSError:
            return [UNSEEN]
        try:
            return attributes(fd) if stat.S_ISLNK(os.fstat(fd).st_mode) else [UNSEEN]
        finally:
            os.close(fd)
    if _OS_LISTXATTR is None:
        return [UNSEEN]
    try:
        names = foreign_attributes(_OS_LISTXATTR(f"/proc/self/fd/{dir_fd}/{name}", follow_symlinks=False))
    except OSError:
        return [UNSEEN]  # ENOTSUP included, as in attributes()
    return names if _SEES_EVERY_ATTRIBUTE else [*names, UNSEEN]


def rename_exclusive(src: str | bytes, dst: str | bytes, dir_fd: int) -> None:
    """Rename within dir_fd, never over anything: FileExistsError when dst exists. A
    filesystem without the flag fails loudly (ENOTSUP / EINVAL), never by replacing."""
    assert _RENAME_EXCL is not None  # require_platform() refused its absence
    fn, flag = _RENAME_EXCL
    if fn(dir_fd, os.fsencode(src), dir_fd, os.fsencode(dst), flag) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), os.fsdecode(src), None, os.fsdecode(dst))


def run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    return p.returncode, (p.stdout or p.stderr or "").strip()


def die(msg: str) -> NoReturn:
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(1)


def require_platform() -> None:
    """Every write goes through directory descriptors, every rename refuses an
    occupied destination, and runs serialize on flock; a platform without them is
    refused rather than run unprotected."""
    lacking = [n for n in ("O_NOFOLLOW", "O_DIRECTORY") if not hasattr(os, n)]
    if fcntl is None:
        lacking.append("fcntl.flock")
    if _RENAME_EXCL is None:
        lacking.append("an exclusive rename (renameatx_np or renameat2)")
    if _LIST_ATTRIBUTES is None:
        lacking.append("extended-attribute listing (flistxattr)")
    if not {os.open, os.mkdir, os.symlink, os.rename, os.unlink, os.rmdir, os.readlink, os.stat} <= os.supports_dir_fd:
        lacking.append("dir_fd support")
    if lacking:
        die(f"this platform ({sys.platform}) lacks {', '.join(lacking)}, "
            f"which this script needs to stay inside its own directories")


def open_dir(name: str | bytes, dir_fd: int | None = None) -> int:
    return os.open(name, O_DIR, dir_fd=dir_fd)


def git_hash(oid_len: int):
    return hashlib.sha1() if oid_len == 40 else hashlib.sha256()


def tree_entries(clone: Path, sha: str) -> Entries:
    """Every entry of the commit, in tree order."""
    p = subprocess.run(["git", "-C", str(clone), "ls-tree", "-r", "-z", "--full-tree", sha],
                       capture_output=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"`git ls-tree {sha[:9]}` failed: {p.stderr.decode('utf-8', 'replace')[:200]}")
    entries = []
    for entry in p.stdout.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode_s, _type, oid = meta.decode("ascii").split(" ")
        mode = int(mode_s, 8)
        if stat.S_ISLNK(mode):
            kind = "link"
        elif stat.S_ISREG(mode):
            kind = "exec" if mode & 0o100 else "file"
        elif mode == 0o160000:
            kind = "gitlink"
        else:
            raise RuntimeError(f"unexpected tree entry mode {mode_s} at {os.fsdecode(path)!r}")
        if any(c in (b"", b".", b"..") or c.lower() == b".git" for c in path.split(b"/")):
            raise RuntimeError(f"the release has a path no checkout would write: {os.fsdecode(path)!r}")
        entries.append((path, kind, oid))
    return entries


def entries_for(clone: Path, export: str) -> tuple[str, Entries] | None:
    """The commit an export name records and its tree; None if the clone cannot say."""
    m = EXPORT_NAME.fullmatch(export)
    if not m:
        return None
    rc, sha = run(["git", "rev-parse", "--verify", "--quiet", f"{m['sha12']}^{{commit}}"], cwd=clone)
    if rc != 0:
        return None
    try:
        return sha, tree_entries(clone, sha)
    except RuntimeError:
        return None


def parent_dirs(paths) -> set[bytes]:
    dirs: set[bytes] = set()
    for path in paths:
        i = path.rfind(b"/")
        while i > 0 and path[:i] not in dirs:
            dirs.add(path[:i])
            i = path.rfind(b"/", 0, i)
    return dirs


class Blobs:
    """Blob bytes by object id from one `git cat-file --batch`: raw — no
    attribute, filter or eol conversion — and checked against the id."""

    def __init__(self, clone: Path) -> None:
        self._err = tempfile.TemporaryFile()  # a file, not a pipe: an undrained stderr pipe can block git
        self._p = subprocess.Popen(["git", "-C", str(clone), "cat-file", "--batch"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err)

    def __enter__(self) -> Blobs:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def stream(self, oid: str) -> Iterator[bytes]:
        stdin, stdout = self._p.stdin, self._p.stdout
        assert stdin is not None and stdout is not None
        try:
            stdin.write(oid.encode("ascii") + b"\n")
            stdin.flush()
        except BrokenPipeError:
            raise RuntimeError(f"git cat-file exited: {self._stderr()}") from None
        header = stdout.readline().split()
        if len(header) != 3 or header[0] != oid.encode("ascii") or header[1] != b"blob":
            raise RuntimeError(f"git cat-file has no blob {oid}: "
                               f"{b' '.join(header).decode('utf-8', 'replace') or self._stderr()}")
        left = int(header[2])
        h = git_hash(len(oid))
        h.update(b"blob %d\0" % left)
        while left:
            chunk = stdout.read(min(left, 1 << 20))
            if not chunk:
                raise RuntimeError(f"git cat-file stopped inside blob {oid}: {self._stderr()}")
            h.update(chunk)
            left -= len(chunk)
            yield chunk
        if stdout.read(1) != b"\n" or h.hexdigest() != oid:
            raise RuntimeError(f"blob {oid} came back from git cat-file not matching its id")

    def _stderr(self) -> str:
        self._err.seek(0)
        return self._err.read().decode("utf-8", "replace").strip()[:300] or "no error output"

    def close(self) -> None:
        for f in (self._p.stdin, self._p.stdout):
            try:
                if f is not None:
                    f.close()
            except OSError:
                pass
        self._p.wait()
        self._err.close()


def scan(dir_fd: int, prefix: bytes = b"") -> Iterator[tuple[bytes, os.stat_result, int, str]]:
    """(path, lstat, parent fd, name) for everything below dir_fd, depth
    first; a symlink is reported, never followed."""
    with os.scandir(dir_fd) as it:
        found = [(e.name, e.stat(follow_symlinks=False)) for e in it]
    for name, st in found:
        rel = prefix + os.fsencode(name)
        yield rel, st, dir_fd, name
        if stat.S_ISDIR(st.st_mode):
            sub = open_dir(name, dir_fd)
            try:
                yield from scan(sub, rel + b"/")
            finally:
                os.close(sub)


def open_regular(dir_fd: int, name: str):
    """A binary reader for a regular file, or None (missing, or a FIFO, directory or link)."""
    try:
        fd = os.open(name, O_READ, dir_fd=dir_fd)
    except OSError:
        return None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    return os.fdopen(fd, "rb")


def read_id(f, size: int, oid_len: int) -> str:
    """The blob id of what the reader holds; "" when it is not `size` bytes long."""
    h = git_hash(oid_len)
    h.update(b"blob %d\0" % size)
    n = 0
    while chunk := f.read(1 << 20):
        h.update(chunk)
        n += len(chunk)
    return h.hexdigest() if n == size else ""


def file_id(dir_fd: int, name: str, oid_len: int) -> str:
    f = open_regular(dir_fd, name)
    if f is None:
        return ""
    with f:
        return read_id(f, os.fstat(f.fileno()).st_size, oid_len)


def link_id(dir_fd: int, name: str, oid_len: int) -> str:
    data = os.fsencode(os.readlink(name, dir_fd=dir_fd))
    h = git_hash(oid_len)
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def matches_blob(f, blobs: Blobs, oid: str) -> bool:
    """Does the reader hold exactly the blob git supplies for `oid`? The blob is
    read to its end whatever the answer — so the batch stays in step, and git has
    shown it can still supply those very bytes (stream() checks them against the
    id). A blob the clone has lost raises: the copy here may be the only one."""
    same = True
    for chunk in blobs.stream(oid):
        if same:
            same = f.read(len(chunk)) == chunk
    return same and not f.read(1)


class Writing:
    """The one file this run is writing, while it writes it: its path in the
    export, its identity, and the length and digest of exactly the bytes that
    reached it. A build that fails deletes that file only while it holds exactly
    those bytes — its own write, cut short — so no file found later is ever taken
    for one: a cut-short write a killed run left cannot be told from an edit."""

    def __init__(self) -> None:
        self.path: bytes | None = None
        self.ident: tuple[int, int] | None = None
        self.size = 0
        self.digest = hashlib.sha256()

    def write(self, path: bytes, fd: int, chunks: Iterable[bytes]) -> None:
        st = os.fstat(fd)
        self.path, self.ident, self.size, self.digest = path, (st.st_dev, st.st_ino), 0, hashlib.sha256()
        for chunk in chunks:
            view = memoryview(chunk)
            while view:
                n = os.write(fd, view)
                self.digest.update(view[:n])
                self.size += n
                view = view[n:]

    def finished(self) -> None:
        self.path = self.ident = None

    def holds(self, rel: bytes, f, st: os.stat_result) -> bool:
        """Is `f` (at `rel` in the export, fstat `st`) the file this run was writing
        when it stopped, holding exactly what reached it?"""
        if (rel, (st.st_dev, st.st_ino), st.st_size) != (self.path, self.ident, self.size):
            return False
        h = hashlib.sha256()
        while chunk := f.read(1 << 20):
            h.update(chunk)
        return h.digest() == self.digest.digest()


def stamp_is_ours(f, export: str, sha: str, entries: Entries) -> bool:
    """Is this, byte for byte, a stamp this script writes for `export` at commit `sha`?
    Every field is what it would write; the one it cannot know is when, so exported_at
    need only have the form it writes. Anything else in a file of that name may be
    someone's — an edit to a stamp keeps it."""
    data = f.read(1 << 16)
    if f.read(1):
        return False
    try:
        d = json.loads(data.decode("utf-8"))
    except ValueError:
        return False
    m = EXPORT_NAME.fullmatch(export)
    if m is None or not isinstance(d, dict) or tuple(d) not in (STAMP_ORDER, STAMP_ORDER_ARCHIVE):
        return False
    tag, when = export[:m.start("sha12") - 1], d["exported_at"]
    want = {"tag": tag, "commit": sha, "files": sum(k != "gitlink" for _, k, _ in entries),
            "codex_cli": tag.removeprefix("rust-v"), "exported_at": when, "tool": TOOL}
    if tuple(d) == STAMP_ORDER_ARCHIVE:
        want.update(export_ignored=[], export_substituted=[])  # it touched no path, or git cannot recreate them
    if d != want or type(d["files"]) is not int or not isinstance(when, str) or not EXPORTED_AT.fullmatch(when):
        return False
    try:
        time.strptime(when, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return False
    return data == (json.dumps(d, indent=2) + "\n").encode("utf-8")


def identity(dir_fd: int, name: str) -> tuple[int, int] | None:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return st.st_dev, st.st_ino


def checked_entry(dir_fd: int, name: str, rel: bytes, kind: str, oid: str, blobs: Blobs, export: str, sha: str,
                  entries: Entries, writing: Writing | None) -> tuple[int, int] | None:
    """The identity of the entry at `name` (`rel` in the export) when git recreates
    exactly it, else None — git supplying the bytes itself, so a blob the clone has
    lost keeps the copy here, and an entry carrying an extended attribute git cannot
    recreate (a resource fork, a user's attribute) is kept with it. A file is judged by
    the descriptor it is read through, and only if it did not change while it was read.
    Modes are not data. The one file that need not be whole is the one a failed build
    of this run was writing (`writing`), while it holds exactly what reached it."""
    if kind == "link":
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (not stat.S_ISLNK(st.st_mode) or link_attributes(dir_fd, name) != []
                or os.fsencode(os.readlink(name, dir_fd=dir_fd)) != b"".join(blobs.stream(oid))):
            return None
        return st.st_dev, st.st_ino
    if kind not in ("file", "exec", "stamp"):  # an extra path: nothing git recreates
        return None
    f = open_regular(dir_fd, name)
    if f is None:
        return None
    with f:
        before = os.fstat(f.fileno())
        if attributes(f.fileno()):
            ok = False
        elif writing is not None and writing.holds(rel, f, before):
            ok = True
        else:
            f.seek(0)
            ok = stamp_is_ours(f, export, sha, entries) if kind == "stamp" else matches_blob(f, blobs, oid)
        after = os.fstat(f.fileno())
    if not ok or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        return None
    return before.st_dev, before.st_ino


def put_back(dir_fd: int, private: str, name: str) -> None:
    """Return an entry taken out for checking to its name — never over whatever was
    saved there meanwhile: then it stays beside it, as <name>.kept-…"""
    try:
        rename_exclusive(private, name, dir_fd)
    except FileExistsError:
        rename_exclusive(private, f"{name}.kept-{secrets.token_hex(3)}", dir_fd)


def check_export(root_fd: int, entries: Entries) -> list[str]:
    """Where the export differs from the commit: a missing or extra path, or a
    different type, mode or content. The stamp is the one extra path allowed."""
    want = {path: (kind, oid) for path, kind, oid in entries}
    dirs = parent_dirs(want)
    bad = [] if stat.S_IMODE(os.fstat(root_fd).st_mode) == RO_EXEC else [b"."]
    seen = set()
    for rel, st, parent, name in scan(root_fd):
        seen.add(rel)
        m = st.st_mode
        kind, oid = want.get(rel) or ("dir" if rel in dirs else "stamp" if rel == STAMP.encode() else "extra", "")
        if kind == "link":
            ok = stat.S_ISLNK(m) and link_id(parent, name, len(oid)) == oid
        elif kind in ("file", "exec"):
            ok = (stat.S_ISREG(m) and stat.S_IMODE(m) == (RO_EXEC if kind == "exec" else RO_FILE)
                  and file_id(parent, name, len(oid)) == oid)
        elif kind in ("dir", "gitlink"):
            ok = stat.S_ISDIR(m) and stat.S_IMODE(m) == RO_EXEC
        else:
            ok = kind == "stamp" and stat.S_ISREG(m) and stat.S_IMODE(m) == RO_FILE
        if not ok:
            bad.append(rel)
    bad.extend(path for path in want if path not in seen)
    return sorted(os.fsdecode(p) for p in bad)


def remove_reproducible(clone: Path, dir_fd: int, name: str, export: str, sha: str, entries: Entries,
                        writing: Writing | None = None) -> bool:
    """Delete the directory `name` in dir_fd entry by entry, so that only what git
    can recreate is ever deleted: each entry is first moved to a name only this run
    knows, then checked against the commit `entries` lists, with git supplying the
    bytes (the stamp: against what this script writes for `export` at `sha`), and
    deleted only if it is still the very entry checked. `writing` is set only for
    the build this run just failed: the file it was writing may be cut short.
    Whatever is saved at an old name meanwhile is never touched; a directory goes
    only once empty. Returns False at the first entry git could not recreate,
    keeping it and all not yet checked; anything else at `name` is never deleted."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    if not stat.S_ISDIR(st.st_mode):
        return False
    want = {path: (kind, oid) for path, kind, oid in entries}
    dirs = parent_dirs(want)

    def remove_dir(parent_fd: int, dname: str, prefix: bytes, blobs: Blobs) -> bool:
        fd = open_dir(dname, parent_fd)
        try:
            if attributes(fd):  # what the directory itself carries: kept, with all it holds
                return False
            os.fchmod(fd, stat.S_IRWXU)
            with os.scandir(fd) as it:
                found = [(e.name, e.stat(follow_symlinks=False)) for e in it]
            for child, cst in found:
                rel = prefix + os.fsencode(child)
                kind, oid = want.get(rel) or ("dir" if rel in dirs else "stamp" if rel == STAMP.encode() else "extra", "")
                if kind in ("dir", "gitlink"):
                    if not stat.S_ISDIR(cst.st_mode) or not remove_dir(fd, child, rel + b"/", blobs):
                        return False
                    continue
                private = f".rm-{secrets.token_hex(8)}"
                try:
                    rename_exclusive(child, private, fd)
                except FileNotFoundError:
                    continue
                try:
                    checked = checked_entry(fd, private, rel, kind, oid, blobs, export, sha, entries, writing)
                    same = checked is not None and identity(fd, private) == checked
                except BaseException:
                    put_back(fd, private, child)
                    raise
                if not same:
                    put_back(fd, private, child)
                    return False
                os.unlink(private, dir_fd=fd)
        finally:
            os.close(fd)
        try:
            os.rmdir(dname, dir_fd=parent_fd)  # fails if anything appeared meanwhile: nothing unchecked goes
        except OSError:
            return False
        return True

    try:
        with Blobs(clone) as blobs:
            return remove_dir(dir_fd, name, b"", blobs)
    except (RuntimeError, OSError):
        return False


def keep_aside(dir_fd: int, name: str, export: str) -> str:
    kept = f".{export}.kept-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
    rename_exclusive(name, kept, dir_fd)
    return kept


def write_entry(blobs: Blobs, dir_fd: int, name: bytes, kind: str, oid: str, path: bytes, writing: Writing) -> None:
    if kind == "link":
        os.symlink(b"".join(blobs.stream(oid)), name, dir_fd=dir_fd)
    elif kind == "gitlink":  # a submodule: an empty directory, as a checkout without it leaves one
        os.mkdir(name, 0o700, dir_fd=dir_fd)
        fd = open_dir(name, dir_fd)
        try:
            os.fchmod(fd, RO_EXEC)
        finally:
            os.close(fd)
    else:
        fd = os.open(name, O_NEW, IN_PROGRESS, dir_fd=dir_fd)
        try:
            writing.write(path, fd, blobs.stream(oid))
            os.fchmod(fd, RO_EXEC if kind == "exec" else RO_FILE)
        finally:
            os.close(fd)
        writing.finished()


def write_tree(blobs: Blobs, entries: Entries, root_fd: int, writing: Writing) -> None:
    """Create every entry below root_fd in tree order, through directory
    descriptors: nothing is written through a symlink, and a name the
    filesystem folds onto one already written fails with EEXIST."""
    stack: list[tuple[bytes, int]] = []  # the open directories, root first

    def leave(depth: int) -> None:
        while len(stack) > depth:
            _, fd = stack.pop()
            try:
                os.fchmod(fd, RO_EXEC)
            finally:
                os.close(fd)

    try:
        for path, kind, oid in entries:
            *dirs, leaf = path.split(b"/")
            depth = 0
            while depth < min(len(stack), len(dirs)) and stack[depth][0] == dirs[depth]:
                depth += 1
            leave(depth)
            try:
                for comp in dirs[depth:]:
                    parent = stack[-1][1] if stack else root_fd
                    os.mkdir(comp, 0o700, dir_fd=parent)
                    stack.append((comp, open_dir(comp, parent)))
                write_entry(blobs, stack[-1][1] if stack else root_fd, leaf, kind, oid, path, writing)
            except FileExistsError:
                raise RuntimeError(f"{os.fsdecode(path)!r} collides with another path of the release "
                                   f"on this filesystem (case or Unicode folding)") from None
        leave(0)
    finally:
        for _, fd in stack:
            os.close(fd)


def escaping_links(root: Path, entries: Entries) -> list[str]:
    """Symlinks of the export that are absolute or resolve outside it."""
    real = os.path.realpath(root)
    bad = []
    for path, kind, _oid in entries:
        if kind == "link":
            link = os.path.join(root, os.fsdecode(path))
            if os.path.isabs(os.readlink(link)) or os.path.commonpath([real, os.path.realpath(link)]) != real:
                bad.append(os.fsdecode(path))
    return bad


def write_stamp(root_fd: int, data: dict, writing: Writing) -> None:
    # Exclusive and never through a symlink: a release path the filesystem
    # folds onto the stamp's name fails the export instead of being overwritten.
    try:
        fd = os.open(STAMP, O_NEW, IN_PROGRESS, dir_fd=root_fd)
    except FileExistsError:
        raise RuntimeError(f"the release has a path that takes the stamp's name {STAMP} on this filesystem") from None
    try:
        writing.write(STAMP.encode(), fd, [(json.dumps(data, indent=2) + "\n").encode("utf-8")])
        os.fchmod(fd, RO_FILE)
    finally:
        os.close(fd)
    writing.finished()


def read_stamp(root_fd: int) -> dict | None:
    f = open_regular(root_fd, STAMP)
    if f is None:
        return None
    try:
        with f:
            data = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def export_valid(exports_fd: int, name: str, tag: str, sha: str, entries: Entries) -> tuple[int, int] | None:
    """The identity of the export at `name` when it matches the commit, else None."""
    try:
        fd = open_dir(name, exports_fd)
    except OSError:  # missing, or not a plain directory
        return None
    try:
        stamp = read_stamp(fd)
        if stamp is None or stamp.get("tag") != tag or stamp.get("commit") != sha or check_export(fd, entries):
            return None
        st = os.fstat(fd)
        return st.st_dev, st.st_ino
    finally:
        os.close(fd)


def build_export(clone: Path, exports: Path, exports_fd: int, name: str, tag: str, sha: str,
                 version: str, entries: Entries) -> tuple[str, tuple[int, int]]:
    """Write and verify the export under a tmp name; return that name, unpublished,
    and the identity of the directory verified."""
    tmp = f".{name}.tmp-{os.getpid()}-{secrets.token_hex(3)}"
    os.mkdir(tmp, 0o700, dir_fd=exports_fd)
    writing = Writing()
    try:
        root_fd = open_dir(tmp, exports_fd)
        try:
            with Blobs(clone) as blobs:
                write_tree(blobs, entries, root_fd, writing)
            escaping = escaping_links(exports / tmp, entries)
            if escaping:
                raise RuntimeError(f"the release has symlinks that leave the export: "
                                   f"{', '.join(escaping[:5])} — refusing to publish it")
            write_stamp(root_fd, {
                "tag": tag, "commit": sha, "files": sum(k != "gitlink" for _, k, _ in entries),
                "codex_cli": version, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "tool": TOOL,
            }, writing)
            os.fchmod(root_fd, RO_EXEC)
            bad = check_export(root_fd, entries)
            if bad:
                raise RuntimeError(f"the export of {tag} does not match the commit at {len(bad)} "
                                   f"path(s), e.g. {', '.join(bad[:5])} — refusing to publish it")
            st = os.fstat(root_fd)
        finally:
            os.close(root_fd)
    except BaseException:
        # Only what git can recreate, and this run's own cut-short write, go; anything else
        # stays for the next run's sweep to keep aside.
        remove_reproducible(clone, exports_fd, tmp, name, sha, entries, writing)
        raise
    return tmp, (st.st_dev, st.st_ino)


def publish(clone: Path, exports_fd: int, tmp: str, name: str, sha: str, entries: Entries) -> None:
    """Move the verified export into place. Whatever holds the name already (a
    damaged export, or anything else) is moved aside first — so readers lose
    the name only between two renames, and get it back if the second fails —
    then deleted as far as git can recreate it; the rest is kept aside. No rename
    here replaces anything: one that would is refused, naming what is where."""
    aside: str | None = f".{name}.old-{os.getpid()}-{secrets.token_hex(3)}"
    try:
        rename_exclusive(name, aside, exports_fd)
    except FileNotFoundError:
        aside = None
    try:
        rename_exclusive(tmp, name, exports_fd)
    except OSError as e:
        if aside:
            try:
                rename_exclusive(aside, name, exports_fd)
            except FileExistsError:
                raise RuntimeError(f"something was created at {name} while this script published it — left "
                                   f"untouched; what held the name before is at {aside}") from e
        if isinstance(e, FileExistsError):
            raise RuntimeError(f"something was created at {name} while this script published it — left "
                               f"untouched; rerun when it is settled") from e
        raise
    if aside and not remove_reproducible(clone, exports_fd, aside, name, sha, entries):
        keep_aside(exports_fd, aside, name)


def linked_export(parent_fd: int, stable: Path, exports: Path, clone: Path) -> tuple[str | None, os.stat_result | None]:
    """The export the stable path's symlink names and the link's lstat, or
    (None, None) when nothing is there yet. Anything else is refused untouched."""
    try:
        st = os.stat(stable.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None, None
    if stat.S_ISLNK(st.st_mode):
        dest = os.readlink(stable.name, dir_fd=parent_fd)
        head, _, name = dest.rpartition("/")
        if head == str(exports) and EXPORT_NAME.fullmatch(name):
            return name, st
        die(f"{stable} is a symlink to {dest}, which this script did not create — move it aside")
    if stat.S_ISDIR(st.st_mode) and os.path.lexists(stable / ".git"):
        die(f"{stable} is a git checkout — the worktree older versions of this script kept there. "
            f"This script never deletes a worktree: keep what you need from it, remove it "
            f"(`git -C {clone} worktree remove {stable}`), then rerun")
    die(f"{stable} exists and is not this script's symlink — move it aside")


def remove_own_link(parent_fd: int, name: str, exports: Path) -> str | None:
    """Delete the link `name`, if it is what this script writes — a symlink naming
    an export — and still the very entry checked: it is moved to a name only this
    run knows first. Anything else is put back untouched. A link of this script's
    that carries an extended attribute git cannot recreate (or whose attributes
    cannot be read) is kept aside as `.<name>.kept-…`, and that name returned."""
    private = f".{name.lstrip('.')}.rm-{secrets.token_hex(4)}"
    try:
        rename_exclusive(name, private, parent_fd)
    except FileNotFoundError:
        return None
    try:
        before = identity(parent_fd, private)
        try:
            dest = os.readlink(private, dir_fd=parent_fd)
        except OSError:  # not a symlink
            dest = ""
        head, _, export = dest.rpartition("/")
        if head != str(exports) or not EXPORT_NAME.fullmatch(export) or identity(parent_fd, private) != before:
            put_back(parent_fd, private, name)
            return None
        if link_attributes(parent_fd, private) != []:
            kept = f".{name.lstrip('.')}.kept-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
            rename_exclusive(private, kept, parent_fd)
            return kept
        os.unlink(private, dir_fd=parent_fd)
        return None
    except BaseException:
        if identity(parent_fd, private) is not None:  # never left at a name only this run knew
            put_back(parent_fd, private, name)
        raise


def restore_link(parent_fd: int, aside: str, stable: Path, why: str) -> NoReturn:
    """Put what was moved aside back at the stable path — never over something
    created there meanwhile: then both stay, and both are named."""
    try:
        rename_exclusive(aside, stable.name, parent_fd)
    except FileExistsError:
        die(f"{stable} {why}, and something else was created there meanwhile — nothing was replaced: "
            f"what was there first is now {stable.parent / aside}")
    die(f"{stable} {why} — put back untouched; rerun when it is settled")


def point_link(parent_fd: int, stable: Path, exports: Path, target: Path, seen: os.stat_result | None) -> None:
    """Point the stable path at `target` without ever replacing what this run
    did not inspect: a new link is created exclusively; the existing one —
    the link `seen` describes — is first moved aside and checked to be that
    very link. Links killed runs left aside go when they are this script's."""
    leftover = re.compile(rf"\.{re.escape(stable.name)}\.link-[0-9A-Za-z-]+")
    with os.scandir(parent_fd) as it:
        stale = [e.name for e in it if leftover.fullmatch(e.name)]
    for n in stale:
        remove_own_link(parent_fd, n, exports)
    aside = None
    if seen is not None:
        aside = f".{stable.name}.link-{os.getpid()}-{secrets.token_hex(3)}"
        rename_exclusive(stable.name, aside, parent_fd)
        st = os.stat(aside, dir_fd=parent_fd, follow_symlinks=False)
        if (st.st_dev, st.st_ino) != (seen.st_dev, seen.st_ino):
            restore_link(parent_fd, aside, stable, "changed while this script ran")
    try:
        os.symlink(str(target), stable.name, dir_fd=parent_fd)
    except FileExistsError:
        if aside:
            remove_own_link(parent_fd, aside, exports)
        die(f"{stable} appeared while this script ran — left untouched; move it aside and rerun")
    except OSError as e:
        if aside:
            restore_link(parent_fd, aside, stable, f"could not be linked to {target} ({e})")
        die(f"cannot create the symlink {stable} → {target} ({e}); this script needs symlink support")
    if aside:
        remove_own_link(parent_fd, aside, exports)


def reachable(path: Path) -> tuple[int, int] | None:
    """The identity of what a reader opening `path` reaches, links followed."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_dev, st.st_ino


def open_exports(parent_fd: int, exports: Path) -> int:
    """The exports directory, created if missing. Refused when it is a symlink,
    not a directory, or someone else's: every write below is anchored to it."""
    try:
        os.mkdir(exports.name, 0o755, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        fd = open_dir(exports.name, parent_fd)
    except OSError as e:
        die(f"{exports} is not a plain directory ({e.strerror}) — this script keeps its exports there; move it aside")
    if os.fstat(fd).st_uid != os.getuid():
        die(f"{exports} belongs to another user — refusing to write or delete there")
    return fd


def survey(exports_fd: int, keep_ids: set[tuple[int, int]]) -> tuple[list[str], list[str]]:
    """What sweep() would name, deleting nothing: what runs kept aside, and anything
    this script did not make — the export in use, under any name, being neither."""
    kept, strangers = [], []
    for n in sorted(os.listdir(exports_fd)):
        if identity(exports_fd, n) in keep_ids:
            continue
        beside = BESIDE.fullmatch(n)
        if beside and beside["kind"] == "kept":
            kept.append(n)
        elif n != ".lock" and not beside and not EXPORT_NAME.fullmatch(n):
            strangers.append(n)
    return kept, strangers


def sweep(clone: Path, exports_fd: int, keep: set[str], keep_ids: set[tuple[int, int]]) -> tuple[list[str], list[str]]:
    """Exports not kept — by name, or by identity: a name the filesystem folds onto
    a kept one (case) is that very directory — and what runs left beside them go as
    far as git can recreate them (this run holds the lock, so no other run is
    building); the rest is kept aside. A killed build's leftover is judged whole,
    like any tree. Returns (kept, strangers): strangers are anything else here, left alone."""
    kept, strangers = [], []
    with os.scandir(exports_fd) as it:
        names = sorted(e.name for e in it)
    for n in names:
        if n in keep or n == ".lock" or identity(exports_fd, n) in keep_ids:
            continue
        beside = BESIDE.fullmatch(n)
        if beside and beside["kind"] == "kept":
            kept.append(n)
            continue
        export = n if EXPORT_NAME.fullmatch(n) else beside["export"] if beside else None
        if export is None:
            strangers.append(n)
            continue
        known = entries_for(clone, export)
        if known is None or not remove_reproducible(clone, exports_fd, n, export, *known):
            kept.append(keep_aside(exports_fd, n, export))
    return sorted(kept), strangers


def main() -> None:
    require_platform()
    # GIT_DIR, GIT_OBJECT_DIRECTORY and the rest would point every git call below at
    # another repository; git names its own list of such variables.
    rc, out = run(["git", "rev-parse", "--local-env-vars"])
    if rc != 0:
        die(f"`git rev-parse --local-env-vars` failed: {out[:200]}")
    for var in out.split():
        os.environ.pop(var, None)
    os.environ["GIT_NO_REPLACE_OBJECTS"] = "1"  # the commit and blobs as the tag names them, not as refs/replace/ swaps them
    clone = Path(os.environ.get("CODEX_SRC_CLONE", "~/Documents/codex")).expanduser()
    stable = Path(
        os.environ.get("CODEX_SRC_DIR")
        or os.environ.get("CODEX_SRC_WORKTREE")
        or "~/Documents/codex-installed"
    ).expanduser()
    if not stable.name:
        die(f"{stable} names no directory entry")
    stable = stable.parent.resolve() / stable.name  # the stable path itself is this script's symlink
    exports = stable.parent / f".{stable.name}.exports"

    rc, out = run(["codex", "--version"])
    if rc != 0:
        die(f"`codex --version` failed: {out[:200]}")
    m = re.search(r"codex-cli (\d+\.\d+\.\d+[0-9A-Za-z.+-]*)", out)
    if not m:
        die(f"could not parse a version out of {out!r}")
    version = m.group(1)
    tag = f"rust-v{version}"

    if not (clone / ".git").exists():
        die(f"{clone} is not a git clone (set CODEX_SRC_CLONE)")

    # Tags may not be local yet for a freshly-updated binary; fetch is
    # best-effort so the script still works offline when the tag is present.
    rc, out = run(["git", "fetch", "--tags", "--quiet", "origin"], cwd=clone)
    if rc != 0:
        print(f"note: tag fetch failed ({out[:120]}) — trying local tags", file=sys.stderr)

    rc, sha = run(["git", "rev-parse", "--verify", f"{tag}^{{commit}}"], cwd=clone)
    if rc != 0:
        die(
            f"tag {tag} not found in {clone} — the installed binary is newer "
            f"than the clone knows. Run `git -C {clone} fetch --tags` (network "
            f"needed) and retry; if it still fails, check the tag scheme on "
            f"github.com/openai/codex/tags."
        )

    name = f"{tag}-{sha[:12]}"
    parent_fd = open_dir(str(stable.parent))
    exports_fd = open_exports(parent_fd, exports)
    # O_EXCL, then a plain open of the winner's file — not O_CREAT alone: on macOS 26.6
    # (Darwin 25.6) an openat() with O_CREAT racing another process creating the same name
    # fails ENOENT; measured 2026-10-18: 1,180 of 1,600 racing opens, and 0 of 1,600 this way.
    try:
        lock = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=exports_fd)
    except FileExistsError:
        lock = os.open(".lock", os.O_RDWR | _NOFOLLOW, dir_fd=exports_fd)
    assert fcntl is not None  # require_platform() refused its absence
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        entries = tree_entries(clone, sha)
        current, seen = linked_export(parent_fd, stable, exports, clone)
        verified = export_valid(exports_fd, name, tag, sha, entries)
        if current == name and verified:
            built = None
            # nothing is swept (the export the last switch replaced may still be read), but what
            # was kept, and what this script did not make, is named on every run
            kept, strangers = survey(exports_fd, {verified})
        else:
            if verified:
                built = "reused"
            else:
                tmp, verified = build_export(clone, exports, exports_fd, name, tag, sha, version, entries)
                publish(clone, exports_fd, tmp, name, sha, entries)
                built = "exported"
            point_link(parent_fd, stable, exports, exports / name, seen)
            # Keep the export just replaced (a session may still be reading it).
            held = {verified}
            if current is not None and (ident := identity(exports_fd, current)) is not None:
                held.add(ident)
            kept, strangers = sweep(clone, exports_fd, {name, current} - {None}, held)
        if kept:
            print(f"note: kept in {exports} — git cannot recreate what they hold (an edit, an added file, "
                  f"a write a killed run cut short, an extended attribute, or a blob or commit the clone "
                  f"lacks); check them, then delete them yourself: {', '.join(kept)}", file=sys.stderr)
        if strangers:
            print(f"note: left alone in {exports} (not made by this script): {', '.join(strangers)}",
                  file=sys.stderr)
        kept_link = re.compile(rf"\.{re.escape(stable.name)}\.(?:link-[0-9A-Za-z-]+\.)?(?:kept|rm)-[0-9A-Za-z-]+")
        kept_links = sorted(n for n in os.listdir(parent_fd) if kept_link.fullmatch(n))
        if kept_links:
            print(f"note: kept beside {stable} — links this script made and did not delete (an extended "
                  f"attribute git cannot recreate, or a run stopped while removing one); check them, then "
                  f"delete them yourself: {', '.join(kept_links)}", file=sys.stderr)
        if not _SEES_EVERY_ATTRIBUTE:
            print("note: on Linux this script cannot be sure it sees every extended attribute "
                  "(trusted.* is listed only to a process privileged in the initial user namespace), "
                  "so it deletes nothing: replaced exports and leftovers are kept aside and named — "
                  "delete them yourself", file=sys.stderr)
        # The link names a path, not this run's descriptors: it stands only if what a
        # reader following it reaches is the export just verified — aligned or not, and
        # after the sweep.
        if reachable(stable) != verified:
            aside = remove_own_link(parent_fd, stable.name, exports)
            die(f"{exports} or what it holds was replaced while this script ran — the link reached "
                f"something other than the export checked, so it was withdrawn"
                + (f" (it carries an extended attribute: kept as {stable.parent / aside})" if aside else "")
                + "; rerun when it is settled")
        if built is None:
            print(f"already aligned: {stable} → {name} — codex-cli {version}")
            return
    except (RuntimeError, OSError) as e:
        die(str(e))
    files = sum(k != "gitlink" for _, k, _ in entries)
    prev = f" (was {current})" if current is not None and current != name else ""
    print(f"{built}: {stable} → {name}{prev} — {files} files, read-only — codex-cli {version}")


if __name__ == "__main__":
    main()
