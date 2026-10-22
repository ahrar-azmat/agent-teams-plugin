"""File revisions, complete patch scopes and verified operation receipts.

Claims are deliberately absent from the authorship decision. Only the pre-image,
matching native hook lifecycle, computed native diff and observed post-image can
establish an exact patch. UNKNOWN history cannot be erased by matching bytes.
"""
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import unicodedata

from .client import CoordinatorError
from . import gitio
from .store import uid, digest
from .locks import KeyedLocks
from . import runtime


def case_sensitive(root):
    # Test the actual volume, not os.path.normcase (a no-op on macOS).
    directory = Path(tempfile.mkdtemp(prefix="c1-volume-", dir=root))
    name = directory / "CaseProbe"
    try:
        name.write_bytes(b"")
        return not (directory / "caseprobe").exists()
    finally:
        name.unlink(missing_ok=True)
        directory.rmdir()


def normalize(checkout, value):
    if not isinstance(value, str) or not value or "\0" in value or "\n" in value or "\r" in value:
        raise CoordinatorError("PATH_ENCODING", "Path cannot be represented reliably in native patch protocol")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise CoordinatorError("PATH_ENCODING", "Path must be valid UTF-8") from exc
    root = Path(checkout["root"])
    path = Path(os.path.abspath(root / value))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise CoordinatorError("PATH_ESCAPE", "Mutation escapes the registered checkout") from exc
    if not relative.parts or any(x.casefold() == ".git" for x in relative.parts):
        raise CoordinatorError("GIT_STATE", "the lead records; Git metadata is not a writer path")
    current = root
    for component in relative.parts:
        current = current / component
        try:
            info = current.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISLNK(info.st_mode):
            raise CoordinatorError("PATH_SYMLINK", "Mutation traverses a symlink", {"path": str(relative), "checkout": checkout.get("id")})
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise CoordinatorError("PATH_HARDLINK", "Hardlinked mutations cannot be modelled", {"path": str(relative), "checkout": checkout.get("id")})
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
            raise CoordinatorError("PATH_TYPE", "Unsupported filesystem node", {"path": str(relative), "checkout": checkout.get("id")})
    name = relative.as_posix()
    return {"path": name, "key": key(checkout, name)}


def key(checkout, name):
    """A path as the volume tells names apart: Unicode-normalized, case-folded where case is ignored."""
    alias = unicodedata.normalize("NFD", name)
    return alias if checkout["case_sensitive"] else alias.casefold()


def hidden(checkout):
    """Keys of what exists outside git's view right now: ignored files, ignored folders (once, as
    "name/") and embedded repositories ("name/"). Together with git's view this covers the whole
    checkout. Git also lists as "name/" a folder that is not ignored but holds only ignored content;
    such a folder is opened, so a new file made there later is not taken for an old one."""
    root, found, folders = checkout["root"], set(), []
    for name in gitio.git(root, ["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"]).stdout.split(b"\0"):
        if name:
            (folders.append if name.endswith(b"/") else found.add)(os.fsdecode(name))
    for name in gitio.git(root, ["ls-files", "-z", "--others", "--exclude-standard"]).stdout.split(b"\0"):
        if name.endswith(b"/"):
            found.add(os.fsdecode(name))
    while folders:
        ignored = _ignored(root, [folder[:-1] for folder in folders])
        deeper = []
        for folder in folders:
            if folder[:-1] in ignored or os.path.lexists(os.path.join(root, folder, ".git")):
                found.add(folder)
                continue
            try:
                with os.scandir(os.path.join(root, folder)) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            deeper.append(folder + entry.name + "/")
                        else:
                            found.add(folder + entry.name)
            except (FileNotFoundError, NotADirectoryError):
                pass  # gone since git listed it
        folders = deeper
    return {key(checkout, name) for name in found}


def _ignored(root, names):
    """Which of these paths an exclude rule matches, of those the index does not track (`hidden` asks of folders,
    `unwatched` of files). check-ignore reads its input as pathspecs: "./" keeps a name like `:(glob)cache` literal (it
    echoes the input). It fails the whole batch for a path beyond a symlink: none is given to it."""
    if not names:
        return set()
    result = gitio.git(root, ["check-ignore", "-z", "--stdin"], data=b"".join(b"./" + os.fsencode(n) + b"\0" for n in names), check=False)
    if result.returncode not in (0, 1):
        raise CoordinatorError("GIT_FAILED", "Git could not classify ignored paths", {"returncode": result.returncode})
    return {os.fsdecode(name[2:] if name.startswith(b"./") else name) for name in result.stdout.split(b"\0") if name}


def _nested(root, path):
    """What a folder on the way to `path` is when git cannot see past it: "link" (a symlink; git refuses to classify
    the path) or "repository" (it holds a .git: the path is an embedded repository's), else None."""
    current = root
    for part in path.split("/")[:-1]:
        current = os.path.join(current, part)
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return "link"
        except (FileNotFoundError, NotADirectoryError):
            return None
        if os.path.lexists(os.path.join(current, ".git")):
            return "repository"
    return None


def _committed(root, names):
    """Which of these paths the branch commit or the index holds an entry at (a file, a folder, a submodule)."""
    names = sorted(names)
    if not names:
        return set()
    # One object name a line, read whole: "HEAD:<path>" and ":0:<path>" (stage 0 of the index) take <path> literally.
    data = b"".join(prefix + os.fsencode(name) + b"\n" for name in names for prefix in (b"HEAD:", b":0:"))
    lines = gitio.git(root, ["cat-file", "--batch-check=%(objecttype)"], data=data).stdout.split(b"\n")
    if len(lines) < 2 * len(names):
        raise CoordinatorError("GIT_FAILED", "Git did not classify every path", {"command": "cat-file"}, True)
    kinds = (b"blob", b"tree", b"commit")
    return {name for i, name in enumerate(names) if lines[2 * i] in kinds or lines[2 * i + 1] in kinds}


def unwatched(checkout, paths):
    """Of these paths, those outside the ledger: git ignores them, or they lie in an embedded repository, and neither
    the branch commit nor the index holds them. None is commit material, whoever writes it, so the coordinator records
    nothing there and publishes nothing from there: no receipt, claim, UNKNOWN or refusal. A path git tracks stays in the
    ledger though an ignore rule matches it, as does one beyond a symlink (git cannot classify it). Raises GIT_FAILED
    when git cannot tell."""
    root = checkout["root"]
    nested = {path: _nested(root, path) for path in set(paths) if path and path != "*"}
    candidates = {path for path, where in nested.items() if where == "repository"}
    candidates |= _ignored(root, sorted(path for path, where in nested.items() if where is None))
    return candidates - _committed(root, candidates)


# What a scan reads at a path but cannot record bytes of: a symlink, a hardlinked or special file, bytes that kept
# changing while read. Each is that path's UNKNOWN (2.4.0-2.4.3: one such path failed the whole reconcile, and a
# writer's turn end then recorded a checkout-wide UNKNOWN).
UNREADABLE = {"PATH_SYMLINK": "a symlink", "PATH_HARDLINK": "a hardlinked file", "PATH_TYPE": "not an ordinary file",
              "UNSTABLE_READ": "changing while read"}


def covered(checkout, entries, path):
    """Whether `path` is, or lies inside, one of `hidden()`'s entries, compared as the volume names paths."""
    name = key(checkout, path)
    parts = name.split("/")
    return name in entries or any("/".join(parts[:i]) + "/" in entries for i in range(1, len(parts) + 1))


def overlaps(left, right):
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def read_path(store, checkout, name):
    normalized = normalize(checkout, name)
    root = checkout["root"]
    parts = normalized["path"].split("/")
    for attempt in range(3):
        descriptors = []
        try:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(descriptor)
            for component in parts[:-1]:
                descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                descriptors.append(descriptor)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                os.close(fd)  # a tracked file that became a folder is absent; its files are paths of their own
                return {"blob": None, "mode": None}
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise CoordinatorError("PATH_TYPE", "Only ordinary single-link files have receipts", {"path": name, "checkout": checkout.get("id")})
                blob = store.blob(stream)
                after = os.fstat(stream.fileno())
                current = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
                fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_nlink)
                if fields(before) == fields(after) == fields(current):
                    return {"blob": blob, "mode": "100755" if before.st_mode & 0o111 else "100644"}
        except (FileNotFoundError, NotADirectoryError):
            # A tracked path whose parent is now a file is absent like a deleted one.
            return {"blob": None, "mode": None}
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
    raise CoordinatorError("UNSTABLE_READ", "File changed while reading", {"path": name, "checkout": checkout.get("id")})


def _decoded(name):
    try:
        return name.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise CoordinatorError("PATH_ENCODING", "Path must be valid UTF-8", {"path": os.fsdecode(name)}) from exc


def visible(checkout, unreadable=None):
    """Git's own view of the checkout, the set Codex itself diffs: tracked paths, including
    deleted ones, plus untracked paths no ignore rule covers. Ignored trees (node_modules,
    virtualenvs, build output) are never commit material and are never read. A submodule is
    refused, or with `unreadable` (a dict) put there as its path's PATH_TYPE."""
    root, paths = checkout["root"], set()
    for entry in gitio.git(root, ["ls-files", "-z", "--stage"]).stdout.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        if meta.split()[0] == b"160000":
            if unreadable is not None:
                unreadable[_decoded(name)] = "PATH_TYPE"
                continue
            raise CoordinatorError("PATH_TYPE", "Git's view of the checkout includes a submodule; the coordinator records "
                                   "ordinary files only", {"path": _decoded(name), "checkout": checkout.get("id")})
        paths.add(_decoded(name))
    for name in gitio.git(root, ["ls-files", "-z", "--others", "--exclude-standard"]).stdout.split(b"\0"):
        # An embedded repository is listed once, as "name/": its files belong to that checkout.
        if name and not name.endswith(b"/"):
            paths.add(_decoded(name))
    return paths


def _folders(root, paths):
    """Each folder holding a scanned path, with the identity and times that change when an entry
    in it is created, removed or replaced; None for a folder that does not exist."""
    names = {""}
    for path in paths:
        parts = path.split("/")[:-1]
        names.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    found = {}
    for name in names:
        try:
            info = os.lstat(os.path.join(root, name) if name else root)
            found[name] = (info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns, info.st_ctime_ns)
        except (FileNotFoundError, NotADirectoryError):
            found[name] = None
    return found


def _snapshot_once(store, checkout, known, unreadable=None):
    listed = visible(checkout, unreadable)
    # Paths the ledger already holds stay in scope after an ignore rule starts covering them (outside the ledger,
    # reconcile lets them go: Ledger.outside).
    scope = sorted((listed | known) - set(unreadable or ()))
    folders = _folders(checkout["root"], scope)
    found = {}
    for path in scope:
        try:
            found[path] = read_path(store, checkout, path)
        except CoordinatorError as exc:
            if unreadable is not None and exc.code in UNREADABLE:
                unreadable[path] = exc.code
                continue
            if exc.code in ("PATH_SYMLINK", "PATH_HARDLINK", "PATH_TYPE"):
                raise CoordinatorError(exc.code, "Git's view of the checkout includes a path that is not an ordinary file; "
                                       "ignore it in .gitignore or remove it", {"path": path, "checkout": checkout.get("id")}) from exc
            raise
    # Equal listings alone miss a tracked file deleted or replaced after it was read: it stays listed.
    if visible(checkout, None if unreadable is None else {}) != listed or _folders(checkout["root"], scope) != folders:
        raise CoordinatorError("UNSTABLE_SNAPSHOT", "Git's view of the checkout changed during the full snapshot")
    return found, listed


def full_snapshot(store, checkout, known=(), unreadable=None, listed=None):
    """What every path of git's view, and every path in `known`, holds. With `unreadable` (a dict), a path that cannot
    be read as an ordinary file (UNREADABLE) is put there with its code instead of failing the snapshot; `listed` (a
    set) receives git's own view, whose paths are all in the ledger."""
    for _ in range(3):
        found_unreadable = None if unreadable is None else {}
        try:
            found, seen = _snapshot_once(store, checkout, set(known), found_unreadable)
        except CoordinatorError as exc:
            if exc.code != "UNSTABLE_SNAPSHOT":
                raise
            continue
        if unreadable is not None:
            unreadable.update(found_unreadable)
        if listed is not None:
            listed.update(seen)
        return found
    raise CoordinatorError("UNSTABLE_SNAPSHOT", "Git's view of the checkout did not stabilize for reconciliation")


def patch_scope(patch):
    if not isinstance(patch, str):
        raise CoordinatorError("PATCH_INPUT", "Expected complete patch text")
    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise CoordinatorError("PATCH_GRAMMAR", "Expected a complete native apply_patch payload")
    changes, current = [], None
    for line in lines[1:-1]:
        match = re.fullmatch(r"\*\*\* (Add|Update|Delete) File: (.+)", line)
        if match:
            current = {"kind": match[1].lower(), "path": match[2]}
            changes.append(current)
        elif line.startswith("*** Move to: "):
            if not current or current["kind"] != "update" or "move" in current:
                raise CoordinatorError("PATCH_SCOPE", "Invalid move scope")
            current["move"] = line[len("*** Move to: "):]
        elif line.startswith("***") and line != "*** End of File":
            raise CoordinatorError("PATCH_SCOPE", "Unknown patch directive")
        elif current is None:
            raise CoordinatorError("PATCH_SCOPE", "Patch content precedes a file header")
    if not changes:
        raise CoordinatorError("PATCH_SCOPE", "Empty patch scope")
    paths = [path for change in changes for path in (change["path"], change.get("move")) if path]
    if len(set(paths)) != len(paths):
        raise CoordinatorError("PATCH_SCOPE", "Repeated or intersecting patch endpoints require separate operations")
    return changes


def unified_post(before, diff):
    """Apply native *computed* diff strictly. No fuzzy matching, newline normalization or search."""
    old = before.splitlines(keepends=True)
    lines = diff.encode("utf-8").splitlines(keepends=True)
    output, position, i, hunks = [], 0, 0, 0
    pattern = re.compile(rb"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    while i < len(lines):
        line = lines[i]
        if line.startswith((b"--- ", b"+++ ")) and not hunks:
            i += 1
            continue
        match = pattern.match(line)
        if not match:
            if not line.strip():
                i += 1
                continue
            raise CoordinatorError("DIFF_FORMAT", "Unrecognized native unified diff")
        hunks += 1
        start, count, new_start, new_count = (int(match[1]), int(match[2] or 1), int(match[3]), int(match[4] or 1))
        start = start - 1 if count else start
        if start < position or start > len(old):
            raise CoordinatorError("DIFF_POSITION", "Native diff positions do not match saved pre-image")
        output.extend(old[position:start])
        position, removed, added = start, 0, 0
        i += 1
        while i < len(lines) and not lines[i].startswith(b"@@ "):
            line = lines[i]
            if not line or line[:1] not in (b" ", b"+", b"-"):
                break
            prefix, data = line[:1], line[1:]
            i += 1
            if i < len(lines) and lines[i].startswith(b"\\ No newline at end of file"):
                data = data.removesuffix(b"\n")
                i += 1
            if prefix in (b" ", b"-"):
                if position >= len(old) or old[position] != data:
                    raise CoordinatorError("DIFF_PREIMAGE", "Computed diff does not match saved bytes")
                position += 1
                removed += 1
            if prefix in (b" ", b"+"):
                output.append(data)
                added += 1
        if (removed, added) != (count, new_count):
            raise CoordinatorError("DIFF_COUNTS", "Computed diff has inconsistent hunk sizes")
    if not hunks and diff.strip():
        raise CoordinatorError("DIFF_FORMAT", "Computed diff has no hunks")
    output.extend(old[position:])
    return b"".join(output)


class Ledger:
    def __init__(self, store):
        self.store = store
        self.locks = {}
        self.registration = asyncio.Lock()
        self.lifecycle = KeyedLocks()
        self.ownership = {}
        self.messages = None  # the daemon's Messages: how a release reaches the leads (notify_released)
        self.notifying = asyncio.Lock()

    def lock(self, checkout):
        return self.locks.setdefault(checkout, asyncio.Lock())

    async def register(self, root):
        # Serialize before the first async gap, including baseline completion.
        async with self.registration:
            result = await self._register(root)
            from .registry import publish
            publish(self.store)
            return result

    async def _register(self, root):
        await asyncio.to_thread(gitio.calibrate, self.store.root)
        root = str(Path(root).resolve())
        if Path(root) == self.store.root or self.store.root in Path(root).parents or Path(root) in self.store.root.parents:
            raise CoordinatorError("STATE_IN_CHECKOUT", "Checkout and coordinator state must be disjoint")
        metadata = await asyncio.to_thread(self._metadata, root)
        identity = metadata["identity"]
        self.own(identity)
        existing = self.store.db.execute("SELECT * FROM checkout_identities WHERE identity=?", (identity,)).fetchone()
        record = self.store.get(existing["checkout"], required=False) if existing else None
        # A first registration that failed (marked) or was killed mid-scan (unmarked) never became ready,
        # wrote no history and admitted nothing: take it again under the same id.
        retry = (bool(record) and not record.get("ready") and existing["state"] in ("incomplete", "initializing")
                 and record.get("quarantine") in (None, "baseline incomplete") and self._untouched(record["id"]))
        if existing and not retry:
            if existing["state"] != "ready":
                raise CoordinatorError("CHECKOUT_QUARANTINED", "Canonical checkout registration needs reconciliation",
                                       {"identity": identity, "state": existing["state"], "members": existing["members"]})
            record = self.store.get(existing["checkout"])
            self.require_owned(record)
            return record
        ident = record["id"] if retry else uid("checkout")
        metadata.update(root=root, case_sensitive=await asyncio.to_thread(case_sensitive, root))
        with self.store.transaction():
            self.store.put("checkout", ident, metadata)
            self.store.db.execute("INSERT INTO checkout_identities VALUES(?,?,?,?) ON CONFLICT(identity) DO UPDATE SET state=excluded.state",
                                  (identity, ident, "initializing", '[]'))
        checkout = self.store.get(ident)
        try:
            base_entries = await asyncio.to_thread(gitio.entries, root, metadata["base"])
            for path, entry in base_entries.items():
                if len(entry) != 1 or entry[0]["mode"] not in ("100644", "100755"):
                    raise CoordinatorError("BASE_TYPE", "Unsupported base tree entry", {"path": path})
            # The base tree's paths are read too: a file removed from the index but kept on disk is not deleted.
            snapshot = await asyncio.to_thread(full_snapshot, self.store, checkout, set(base_entries))
            blobs_by_oid = await asyncio.to_thread(self._base_blobs, root, {entry[0]["oid"] for entry in base_entries.values()})
            baseline = {}
            for path in set(snapshot) | set(base_entries):
                entry = base_entries.get(path)
                baseline[path] = {"blob": blobs_by_oid[entry[0]["oid"]], "mode": entry[0]["mode"]} if entry else {"blob": None, "mode": None}
            with self.store.transaction():
                for path, known in baseline.items():
                    rev = self.store.revision(ident, path, known["blob"], known["mode"], "baseline")
                    actual = snapshot.get(path, {"blob": None, "mode": None})
                    if actual != known:
                        dirty = self.store.revision(ident, path, actual["blob"], actual["mode"], "UNKNOWN")
                        self.store.unknown(ident, path, "pre-existing dirty bytes", {"known_revision": rev}, dirty)
                checkout["ready"] = True
                self.store.save(checkout)
                self.store.db.execute("UPDATE checkout_identities SET state='ready' WHERE identity=?", (identity,))
            return self.store.get(ident)
        except BaseException:
            checkout["quarantine"] = "baseline incomplete"
            self.store.save(checkout)
            self.store.db.execute("UPDATE checkout_identities SET state='incomplete' WHERE identity=?", (identity,))
            raise

    def own(self, identity):
        if identity in self.ownership:
            return
        path = runtime.runtime_root() / (identity + ".owner")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise CoordinatorError("CHECKOUT_OWNER", "Unsafe checkout ownership anchor")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CoordinatorError("CHECKOUT_OWNED", "Another coordinator owns this canonical checkout", {"identity": identity}) from exc
            previous = os.read(fd, 16384).decode()
            state = str(self.store.root)
            if previous and previous != state:
                raise CoordinatorError("CHECKOUT_OWNED", "Checkout history belongs to another coordinator state root",
                                       {"identity": identity, "state": previous})
            if not previous:
                os.write(fd, state.encode())
                os.fsync(fd)
                runtime.sync_directory(path.parent)
            self.ownership[identity] = fd
        except BaseException:
            os.close(fd)
            raise

    def require_owned(self, checkout):
        if checkout.get("identity") not in self.ownership or not checkout.get("ready") or checkout.get("quarantine"):
            raise CoordinatorError("CHECKOUT_QUARANTINED", "Checkout ownership/baseline must be reconciled before effects",
                                   {"checkout": checkout["id"], "reason": checkout.get("quarantine")})

    async def recover_ownership(self):
        groups = {}
        for checkout in list(self.store.all("checkout")):
            try:
                metadata = await asyncio.to_thread(self._metadata, checkout["root"])
                if checkout.get("identity") and checkout["identity"] != metadata["identity"]:
                    raise CoordinatorError("CHECKOUT_IDENTITY_CHANGED", "Registered directory or Git directory was replaced")
                checkout["identity"] = metadata["identity"]
                self.store.save(checkout)
                groups.setdefault(metadata["identity"], []).append(checkout)
            except (CoordinatorError, OSError) as exc:
                checkout["quarantine"] = getattr(exc, "code", type(exc).__name__)
                self.store.save(checkout)
        for identity, members in groups.items():
            reason = None
            try:
                self.own(identity)
            except CoordinatorError as exc:
                reason = exc.code
            if len(members) != 1:
                reason = "CHECKOUT_IDENTITY_COLLISION"
            with self.store.transaction():
                for checkout in members:
                    if reason:
                        checkout["quarantine"] = reason
                        self.store.save(checkout)
                state = "collision" if len(members) != 1 else ("ready" if members[0].get("ready") and not reason else "incomplete")
                from .store import encoded
                self.store.db.execute("INSERT INTO checkout_identities VALUES(?,?,?,?) ON CONFLICT(identity) "
                                      "DO UPDATE SET state=excluded.state,members=excluded.members",
                                      (identity, members[0]["id"], state, encoded([c["id"] for c in members])))
                if reason:
                    self.store.event("checkout/ownership_refused", identity, {"reason": reason, "members": [c["id"] for c in members]})

    def mutation_scope(self, unitid, checkoutid=None):
        """Every path the unit's mutations in this checkout touched, each of which its seal needs a final exact receipt
        of. A restore to the path's baseline (recovery.restore_unknown) adds none; a path another operation of the unit
        touched stays through that one. A path outside the ledger (unwatched) is never publication material, whoever
        wrote it (2.4.0-2.4.3 force-added an ignored file a patch created into the candidate tree)."""
        checkout = self.store.get(checkoutid or self.store.get(unitid)["checkout"])
        paths = set()
        for operation in self.store.all("operation", unitid):
            if operation["checkout"] != checkout["id"] or operation.get("parts") or operation["kind"] not in ("patch", "lead-edit", "restore"):
                continue
            if operation["state"] == "denied" and operation.get("no_effect") is True or operation.get("resolution") == "baseline":
                continue
            paths.update(operation["paths"])
            for entry in operation.get("intended", {}).get("scope", []):
                paths.add(entry["path"])
                if entry.get("move"):
                    paths.add(entry["move"])
        paths = {normalize(checkout, path)["path"] for path in paths}
        return sorted(paths - unwatched(checkout, paths))

    def _untouched(self, ident):
        db = self.store.db
        return not (db.execute("SELECT 1 FROM revisions WHERE checkout=? LIMIT 1", (ident,)).fetchone()
                    or db.execute("SELECT 1 FROM unknowns WHERE checkout=? LIMIT 1", (ident,)).fetchone()
                    or db.execute("SELECT 1 FROM records WHERE kind != 'checkout' AND json_extract(data, '$.checkout')=? LIMIT 1",
                                  (ident,)).fetchone())

    def _base_blobs(self, root, oids):
        with gitio.blobs(root) as read:
            return {oid: self.store.blob(read(oid)) for oid in sorted(oids)}

    def _metadata(self, root):
        top = gitio.text(root, ["rev-parse", "--show-toplevel"])
        if Path(top).resolve() != Path(root):
            raise CoordinatorError("CHECKOUT_ROOT", "Register the Git checkout root")
        base = gitio.text(root, ["rev-parse", "--verify", "HEAD^{commit}"])
        branch = gitio.text(root, ["symbolic-ref", "HEAD"])
        if not branch.startswith("refs/heads/"):
            raise CoordinatorError("BRANCH", "Publication requires a named branch")
        common = str(Path(gitio.text(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])).resolve())
        info = os.stat(common)
        git_directory = str(Path(gitio.text(root, ["rev-parse", "--absolute-git-dir"])).resolve())
        work, gitdir = os.stat(root), os.stat(git_directory)
        return {"base": base, "branch": branch, "repository": digest([common, info.st_dev, info.st_ino]),
                "identity": digest([[work.st_dev, work.st_ino], [gitdir.st_dev, gitdir.st_ino]]),
                "git_directory": git_directory,
                "git_common": common, "object_format": gitio.text(root, ["rev-parse", "--show-object-format"])}

    async def reconcile(self, checkout, paths=None, reason="unexplained filesystem change", ignore_pending=True, attribute=None):
        """Record what differs from the ledger, path by path (the whole checkout without `paths`). What is outside the
        ledger (unwatched) is let go instead (release), and a path that cannot be read as an ordinary file
        (UNREADABLE) is that path's UNKNOWN."""
        async with self.lock(checkout["id"]):
            unreadable, listed = {}, None
            try:
                if paths is None:
                    known = {r[0] for r in self.store.db.execute("SELECT path FROM heads WHERE checkout=?", (checkout["id"],))}
                    listed = set()
                    actual = await asyncio.to_thread(full_snapshot, self.store, checkout, known, unreadable, listed)
                    paths = set(actual)
                else:
                    actual = {}
                    for path in paths:
                        try:
                            actual[path] = await asyncio.to_thread(read_path, self.store, checkout, path)
                        except CoordinatorError as exc:
                            if exc.code not in UNREADABLE:
                                raise
                            unreadable[path] = exc.code
            except CoordinatorError as exc:
                if exc.code in ("BLOB_MISSING", "BLOB_CORRUPT"):
                    with self.store.transaction():
                        for row in self.store.db.execute("SELECT DISTINCT operation,path FROM revisions WHERE checkout=? AND blob=? AND operation IS NOT NULL",
                                                         (checkout["id"], exc.details.get("blob"))):
                            operation = self.store.get(row["operation"])
                            operation.update(state="quarantined", refusal=exc.code)
                            self.store.save(operation)
                            self.store.unknown(checkout["id"], row["path"], exc.code, {"possible_operation": operation["id"]})
                        checkout["quarantine"] = exc.code
                        self.store.save(checkout)
                raise
            outside = await self.outside(checkout, actual, unreadable, listed)
            result = self.apply_observation(checkout, paths, actual, reason, ignore_pending, attribute, outside=outside, unreadable=unreadable)
        await self.notify_released()
        return result

    async def unwatched(self, checkout, paths):
        """unwatched(), off the event loop. A git failure raises (GIT_FAILED)."""
        paths = set(paths)
        return await asyncio.to_thread(unwatched, checkout, paths) if paths else set()

    async def classify(self, checkout, paths):
        """unwatched(), where a git failure must not stop the caller: none is taken as outside, so each is recorded as
        before 2.4.4 (an event says so), and a later reconcile releases what is outside."""
        try:
            return await self.unwatched(checkout, paths)
        except (CoordinatorError, OSError, subprocess.SubprocessError) as exc:
            self.store.event("ledger/ignore_check_failed", checkout["id"], {"paths": len(paths), "code": getattr(exc, "code", type(exc).__name__)})
            return set()

    async def outside(self, checkout, observed, unreadable=(), listed=None):
        """Of what a reconcile read (`observed` images, `unreadable` paths), the paths outside the ledger (unwatched):
        asked of those whose bytes differ from the ledger's, that it could not read or that have an open UNKNOWN, and in
        a whole-checkout read (`listed`: git's own view, all in the ledger) of every open UNKNOWN's path. What a command
        writes there and a writer then reads with read_file is never UNKNOWN (2.4.0-2.4.3 recorded it, and every later
        run of the tests another), and an UNKNOWN recorded there before is resolved (release). Called holding the
        checkout's lock, before the observation is applied."""
        if listed is not None:  # a whole checkout: one read of its heads and UNKNOWNs, not one per path
            heads = {r["path"]: {"blob": r["blob"], "mode": r["mode"]} for r in self.store.db.execute(
                "SELECT h.path, r.blob, r.mode FROM heads h JOIN revisions r ON r.id=h.revision WHERE h.checkout=?", (checkout["id"],))}
        else:
            heads = {}
            for path in observed:
                row = self.store.head(checkout["id"], path)
                if row:
                    heads[path] = {"blob": row["blob"], "mode": row["mode"]}
        absent = {"blob": None, "mode": None}
        candidates = {path for path, image in observed.items() if image != heads.get(path, absent)} | set(unreadable)
        candidates |= {r[0] for r in self.store.db.execute("SELECT DISTINCT u.path FROM unknowns u LEFT JOIN resolutions s ON "
                       "s.unknown_id=u.id WHERE u.checkout=? AND s.edit IS NULL AND u.path!='*'", (checkout["id"],))
                       if listed is not None or r[0] in observed or r[0] in unreadable}
        candidates -= listed or set()
        return await self.classify(checkout, candidates) if candidates else set()

    def release(self, checkout, paths, reason):
        """Let go of paths outside the ledger (unwatched), in the caller's transaction: the head held of each is let go,
        so it reads as a path the ledger never saw, and each UNKNOWN open there is resolved as "outside-ledger" by one
        resolution record that keeps its reason and time (nothing is deleted). Returns that record's id, or None when no
        UNKNOWN was open there; the leads hear of it once (notify_released)."""
        paths = sorted(set(paths))
        if not paths:
            return None
        heads = {}
        for start in range(0, len(paths), 500):
            chunk = paths[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            heads.update((r["path"], r["revision"]) for r in self.store.db.execute(
                "SELECT path, revision FROM heads WHERE checkout=? AND path IN (" + marks + ")", [checkout["id"], *chunk]))
            self.store.db.execute("DELETE FROM heads WHERE checkout=? AND path IN (" + marks + ")", [checkout["id"], *chunk])
        if heads:
            self.store.event("ledger/left", checkout["id"], {"heads": heads, "reason": "outside the ledger: " + reason})
        wanted = set(paths)
        found = [u for u in self.store.unresolved(checkout["id"]) if u["path"] in wanted]
        if not found:
            return None
        ident, units, unscoped = uid("resolution"), {}, []
        for unknown in found:
            # The units it names: those whose custody it records, or the operation it may be.
            context, owners = json.loads(unknown["context"] or "{}"), set()
            for key in (context.get("custody") or []) + [context.get("possible_operation")]:
                record = self.store.get(key, required=False) if isinstance(key, str) else None
                if record and record.get("unit"):
                    owners.add(record["unit"])
            for unit in owners:
                units.setdefault(unit, []).append(unknown["id"])
            if not owners:
                unscoped.append(unknown["id"])
        self.store.put("resolution", ident, {"kind": "outside-ledger", "checkout": checkout["id"], "reason": reason,
                       "created": time.time(), "units": units, "unscoped": unscoped, "notices": None,
                       "unknowns": [{k: u[k] for k in ("id", "path", "reason", "created")} for u in found]}, checkout["id"])
        for unknown in found:
            self.store.db.execute("INSERT INTO resolutions(unknown_id,restore,edit) VALUES(?,?,?) ON CONFLICT(unknown_id) "
                                  "DO UPDATE SET edit=excluded.edit WHERE resolutions.edit IS NULL", (unknown["id"], ident, ident))
        self.store.event("unknown/outside_ledger", ident, {"checkout": checkout["id"], "unknowns": [u["id"] for u in found],
                         "paths": sorted({u["path"] for u in found}), "reason": reason})
        return ident

    async def notify_released(self):
        """Tell the leads of each release once: one message to the lead mailbox of each unit its UNKNOWNs name, and one
        to the checkout's leads (`lead`, by its folder) for those naming no unit or a unit without one. A release made
        while no messages are wired (self.messages) waits for the next call."""
        if self.messages is None:
            return
        async with self.notifying:
            for record in list(self.store.selected("SELECT * FROM records WHERE kind='resolution' AND "
                                                   "json_extract(data,'$.notices') IS NULL ORDER BY id")):
                try:
                    await self._notify(record)
                except Exception as exc:  # a notice never fails the reconcile that released them; the next call sends it
                    self.store.event("unknown/notice_failed", record["id"], {"code": getattr(exc, "code", type(exc).__name__)})

    async def _notify(self, record):
        checkout = self.store.get(record["checkout"])
        rows = {u["id"]: u for u in record["unknowns"]}
        recipients = {}
        for unit, ids in sorted(record["units"].items()):
            box = (self.store.get(unit, required=False) or {}).get("lead_mailbox")
            recipients.setdefault(box or "lead", set()).update(ids)
        if record["unscoped"]:
            recipients.setdefault("lead", set()).update(record["unscoped"])
        sent = []
        for recipient, ids in sorted(recipients.items()):
            listed = sorted((rows[i] for i in ids), key=lambda u: (u["path"], u["created"]))
            text = (str(len(listed)) + " UNKNOWN" + ("s" if len(listed) != 1 else "") + " in " + checkout["root"] +
                    " resolved as outside the ledger (resolution " + record["id"] + "): git ignores these paths and neither "
                    "the branch commit nor the index holds them, so the coordinator records nothing there and never "
                    "publishes them. Nothing was written or deleted. " + "; ".join(u["path"] + " (" + u["reason"] + ")" for u in listed))
            try:
                message = await self.messages.send("coordinator", recipient, text, "none", cwd=checkout["root"] if recipient == "lead" else "")
            except CoordinatorError:
                if recipient == "lead":
                    raise
                message = await self.messages.send("coordinator", "lead", text, "none", cwd=checkout["root"])
            sent.append(message["id"])
        record["notices"] = sent
        self.store.save(record)

    def apply_observation(self, checkout, paths, actual, reason, ignore_pending=True, attribute=None, transaction=True, outside=(),
                          unreadable=None):
        pending = set()
        if ignore_pending:
            for operation in self.store.live_operations(checkout["id"]):
                if (operation["state"] in ("prepared", "allowed", "running") or operation.get("executing")) and operation["kind"] in ("patch", "lead-edit", "restore"):
                    pending.update(operation["paths"])
        context = [r["id"] for r in self.store.held_custody(checkout["id"])]
        # The caller's proof is checked again after the scan, with nothing awaited before the writes.
        if attribute and attribute.get("still_quiet") and not attribute["still_quiet"]():
            attribute = None
        if attribute:
            from .participants import published_participant
            # Recheck after every asynchronous scan, immediately before receipts.
            # The installed prefix belongs to the old unit; later bytes do not.
            if published_participant(self.store, attribute["unit"], checkout["id"]):
                attribute = None
        unknowns, attributed, released = [], [], None
        unreadable = unreadable or {}
        from contextlib import nullcontext
        with self.store.transaction() if transaction else nullcontext():
            if outside:
                # A head held there (2.4.0-2.4.3 kept test output a writer read) would refuse its next read_file
                # UNSTABLE_READ: it is let go with the UNKNOWNs open there.
                released = self.release(checkout, [path for path in outside if path not in pending], reason)
            opened = None
            for path, code in sorted(unreadable.items()):
                if path in pending or path in outside:
                    continue
                if opened is None:
                    opened = {(u["path"], json.loads(u["context"] or "{}").get("unreadable")) for u in self.store.unresolved(checkout["id"])}
                if (path, code) in opened:
                    continue  # already UNKNOWN for that: one per reason, not one per scan
                unknowns.append(self.store.unknown(checkout["id"], path, "unreadable (" + UNREADABLE[code] + "): " + reason,
                                                   {"custody": context, "label": "custody" if context else "UNKNOWN", "unreadable": code}))
            for path in paths:
                if path in pending or path in outside or path in unreadable:
                    continue
                current = actual.get(path, {"blob": None, "mode": None})
                head = self.store.head(checkout["id"], path)
                known = {"blob": head["blob"], "mode": head["mode"]} if head else {"blob": None, "mode": None}
                if current != known:
                    why = reason
                    # A caller that proved nothing else could have changed the tree names the author.
                    if attribute and not any(overlaps(normalize(checkout, path)["key"], key) for key in attribute["excluded"]):
                        # A path new to the ledger is the caller's only if nothing existed there when it began:
                        # content that was outside git's view then (`git add -f`, an ignore rule removed) is older.
                        if head or (attribute.get("hidden") is not None and not covered(checkout, attribute["hidden"], path)):
                            self.store.revision(checkout["id"], path, current["blob"], current["mode"], attribute["attribution"],
                                                attribute["operation"], attribute["unit"])
                            attributed.append(path)
                            continue
                        why = "older content became visible: " + reason
                    revision = self.store.revision(checkout["id"], path, current["blob"], current["mode"], "UNKNOWN")
                    unknowns.append(self.store.unknown(checkout["id"], path, why,
                                                      {"custody": context, "label": "custody" if context else "UNKNOWN"}, revision))
        result = {"unknowns": unknowns, "paths_scanned": len(paths)}
        if released:
            result["released"] = released
        if attribute:
            result["attributed"] = attributed
        return result

    async def reconcile_many(self, checkouts, reason, target=None, attribute=None):
        from contextlib import AsyncExitStack
        async with AsyncExitStack() as stack:
            for checkout in sorted(checkouts, key=lambda c: c["id"]):
                await stack.enter_async_context(self.lock(checkout["id"]))
            async def collect(checkout):
                known = {r[0] for r in self.store.db.execute("SELECT path FROM heads WHERE checkout=?", (checkout["id"],))}
                unreadable, listed = {}, set()
                actual = await asyncio.to_thread(full_snapshot, self.store, checkout, known, unreadable, listed)
                return actual, unreadable, listed
            observations = await asyncio.gather(*(collect(c) for c in checkouts))
            outsides = [await self.outside(c, *observed) for c, observed in zip(checkouts, observations)]
            if attribute and attribute.get("still_quiet") and not attribute["still_quiet"]():
                attribute = None
            results = {}
            with self.store.transaction():
                for checkout, (actual, unreadable, _), outside in zip(checkouts, observations, outsides):
                    proof = None
                    if attribute:
                        if "checkouts" in attribute:
                            part = attribute["checkouts"].get(checkout["id"])
                            if part:
                                proof = dict(attribute, **part)
                        elif checkout["id"] == target:
                            proof = attribute
                    results[checkout["id"]] = self.apply_observation(checkout, set(actual), actual, reason,
                        attribute=proof, transaction=False, outside=outside, unreadable=unreadable)
            result = dict(results.get(target, {}))
            result["participants"] = [{"checkout": c["id"], "root": c["root"], **results[c["id"]]} for c in checkouts]
        await self.notify_released()
        return result

    async def prepare(self, actor, kind, intended, paths, native=None, deadline=None, outside=None):
        """Admit an operation on these paths. Those outside the ledger (unwatched; `outside` when the caller classified
        them already) are written but never recorded: the operation keeps them apart (`outside`), with no base, claim
        or receipt, and nothing there refuses it. A restore names only paths in the ledger."""
        unit, checkout = self.store.get(actor["unit"]), self.store.get(actor["checkout"])
        self.require_owned(checkout)
        if unit["state"] != "open" or checkout.get("quarantine"):
            raise CoordinatorError("UNIT_FROZEN", "Unit or checkout is not open for operations")
        from .participants import require_writable
        endpoints = sorted({normalize(checkout, path)["path"] for path in paths})
        keys = [normalize(checkout, path)["key"] for path in endpoints]
        if any(overlaps(left, right) for i, left in enumerate(keys) for right in keys[i + 1:]):
            raise CoordinatorError("OPERATION_SCOPE", "A single mutation cannot contain aliased or ancestor/descendant endpoints")
        if kind in ("command", "mcp", "lead-command", "restore") or not endpoints:
            outside = set()
        elif outside is None:
            outside = await self.classify(checkout, endpoints)
        else:
            outside = {normalize(checkout, path)["path"] for path in outside} & set(endpoints)
        paths = [path for path in endpoints if path not in outside]
        keys = [normalize(checkout, path)["key"] for path in paths]
        if paths or kind in ("command", "mcp", "lead-command"):
            require_writable(self.store, unit, checkout["id"])
        from .locks import held
        async with held(self.lock(checkout["id"]), deadline):
            from .custody import interfering
            started = time.time()
            if paths and interfering(self.store, checkout, paths):
                raise CoordinatorError("CUSTODY_CONFLICT", "Command custody overlaps the intended mutation")
            for operation in self.store.live_operations(checkout["id"]):
                if (kind == "restore" and operation["state"] == "quarantined" and not operation.get("executing")
                        and operation["paths"] and set(operation["paths"]) <= set(paths)):
                    continue  # a restore replaces what a quarantined operation left, once native cannot still write it
                existing = [normalize(checkout, path)["key"] for path in operation["paths"]]
                if any(overlaps(a, b) for a in keys for b in existing):
                    raise CoordinatorError("OPERATION_BUSY", "An overlapping operation is unreconciled", {"operation": operation["id"]})
            pre = {}
            for path in endpoints:
                try:
                    pre[path] = await asyncio.to_thread(read_path, self.store, checkout, path)
                except CoordinatorError as exc:
                    if path not in outside or exc.code not in UNREADABLE:
                        raise
                    pre[path] = None  # outside the ledger and no ordinary file: nothing moved from it can be verified
            if self.store.get(actor["unit"])["state"] != "open":
                raise CoordinatorError("UNIT_FROZEN", "Unit froze while operation pre-images were being saved")
            if paths or kind in ("command", "mcp", "lead-command"):
                require_writable(self.store, actor["unit"], checkout["id"])
            if paths and interfering(self.store, checkout, paths, since=started):
                raise CoordinatorError("CUSTODY_CONFLICT", "Command custody changed during pre-image capture")
            bases = {}
            for path in paths:
                image = pre[path]
                head = self.store.head(checkout["id"], path)
                known = {"blob": head["blob"], "mode": head["mode"]} if head else {"blob": None, "mode": None}
                if image != known:
                    with self.store.transaction():
                        rev = self.store.revision(checkout["id"], path, image["blob"], image["mode"], "UNKNOWN")
                        self.store.unknown(checkout["id"], path, "stale pre-image at operation admission", {}, rev)
                    raise CoordinatorError("STALE_BASE", "Path changed outside a verified operation", {"path": path})
                bases[path] = head and head["id"]
            opid = uid("op")
            # An opaque operation reads no base of its own (publish skips it as a dependency): a
            # fingerprint pins the heads it was admitted against. A copy of every head cost 365 KB
            # a command on a 3,679-file checkout, decoded again by every later scan.
            heads = self.store.heads_fingerprint(checkout["id"]) if kind in ("command", "mcp", "lead-command") else None
            reads = [r for r in self.store.all("read", actor["id"])
                     if unit.get("project_revision") or r.get("checkout", actor["checkout"]) == checkout["id"]]
            dependencies = [r["revisions"] for r in reads]
            operation = {"checkout": checkout["id"], "unit": unit["id"], "actor": actor["id"], "kind": kind,
                         "attempt": actor.get("attempt"), "generation": self.store.generation,
                         "state": "prepared", "paths": paths, "pre": pre, "bases": bases,
                         "intended": intended, "native": native or {}, "dependencies": dependencies,
                         "created": started, "hook_valid": False, **({"heads": heads} if heads else {}),
                         **({"outside": sorted(outside)} if outside else {})}
            if unit.get("project_revision"):
                operation["qualified_dependencies"] = [{"checkout": r.get("checkout", actor["checkout"]), "revisions": r["revisions"]} for r in reads]
            with self.store.transaction():
                self.store.put("operation", opid, operation, unit["id"])
                self.store.event("operation/prepared", opid, {"bases": bases, **({"heads": heads} if heads else {}), "native": native or {}})
            return self.store.get(opid)

    async def complete_patch(self, operation, item):
        if operation.get("parts"):
            from .operations import complete_patch
            return await complete_patch(self, operation, item)
        checkout = self.store.get(operation["checkout"])
        try:
            if item.get("status") != "completed" or not operation.get("hook_valid") or not operation.get("post_hook"):
                raise CoordinatorError("PATCH_EVIDENCE", "Patch lacks successful native lifecycle evidence")
            if operation.get("post_payload", {}).get("command") != operation["intended"]["patch"]:
                raise CoordinatorError("PATCH_REWRITTEN", "Effective post-tool patch differs from the durably admitted input")
            expected, seen = {}, set()
            intended = operation["intended"]["scope"]
            native_changes = item.get("changes", [])
            outside = set(operation.get("outside", ()))
            if len(native_changes) != len(intended):
                raise CoordinatorError("PATCH_SCOPE", "Native changes differ from admitted complete scope")
            for change in native_changes:
                path = normalize(checkout, change["path"])["path"]
                kind = change["kind"]["type"]
                move = change["kind"].get("move_path") or change["kind"].get("movePath")
                target = normalize(checkout, move)["path"] if move else path
                match = next((x for x in intended if x["path"] == path), None)
                if not match or match["kind"] != kind or match.get("move", path) != target or path in seen:
                    raise CoordinatorError("PATCH_SCOPE", "Native computed change mismatches original patch")
                seen.add(path)
                if target in outside:
                    # What lands outside the ledger is not recorded; a source in the ledger is gone.
                    if path not in outside:
                        expected[path] = {"blob": None, "mode": None}
                    continue
                pre = operation["pre"][path]
                if pre is None:
                    raise CoordinatorError("PATCH_EVIDENCE", "A move from outside the ledger left no readable pre-image", {"path": path})
                before = self.store.read_blob(pre["blob"]) if pre["blob"] else b""
                diff = change["diff"]
                if kind == "add":
                    if pre["blob"] is not None:
                        raise CoordinatorError("PATCH_ADD", "Add over existing file is not an exact addition")
                    after = diff.encode("utf-8")
                elif kind == "delete":
                    if before != diff.encode("utf-8") or pre["blob"] is None:
                        raise CoordinatorError("PATCH_DELETE", "Delete evidence differs from pre-image")
                    expected[path] = {"blob": None, "mode": None}
                    continue
                elif kind == "update":
                    if pre["blob"] is None:
                        raise CoordinatorError("PATCH_UPDATE", "Update has no saved pre-image")
                    if move:
                        suffix = "\n\nMoved to: " + move
                        if not diff.endswith(suffix):
                            raise CoordinatorError("PATCH_MOVE", "Native move suffix disagrees with structured destination")
                        diff = diff[:-len(suffix)]
                        if path not in outside:
                            expected[path] = {"blob": None, "mode": None}
                    after = unified_post(before, diff)
                else:
                    raise CoordinatorError("PATCH_KIND", "Unknown native patch kind")
                blob = await asyncio.to_thread(self.store.blob, after)
                mode = operation["pre"][target]["mode"] or "100644"
                expected[target] = {"blob": blob, "mode": mode}
            if set(expected) != set(operation["paths"]):
                raise CoordinatorError("PATCH_SCOPE", "Computed output does not cover every endpoint")
            return await self.verify(operation, expected, "exact", item)
        except (CoordinatorError, OSError, MemoryError, UnicodeError, KeyError) as exc:
            await self.quarantine(operation, getattr(exc, "code", type(exc).__name__))
            raise

    async def verify(self, operation, expected, attribution, evidence):
        checkout = self.store.get(operation["checkout"])
        async with self.lock(checkout["id"]):
            actual = await self.verify_images(operation, expected)
            return self.apply_verified(operation, actual, attribution, evidence)

    async def verify_images(self, operation, expected):
        checkout = self.store.get(operation["checkout"])
        outside = set(operation.get("outside", ()))  # written, never recorded (prepare)
        expected = {path: image for path, image in expected.items() if path not in outside}
        actual = {p: await asyncio.to_thread(read_path, self.store, checkout, p) for p in operation["paths"]}
        if actual != expected:
            raise CoordinatorError("POSTIMAGE_MISMATCH", "Observed bytes or modes differ from recorded intended result")
        for path, base in operation["bases"].items():
            head = self.store.head(checkout["id"], path)
            if (head and head["id"]) != base:
                raise CoordinatorError("REVISION_CHANGED", "Ledger base changed during operation")
        from .custody import interfering
        opaque = interfering(self.store, checkout, operation["paths"], operation["id"], operation["created"])
        if opaque:
            raise CoordinatorError("OPAQUE_OVERLAP", "Opaque effects overlap this patch's possible authorship", {"custody": opaque})
        return actual

    def apply_verified(self, operation, actual, attribution, evidence, transaction=True):
        checkout = self.store.get(operation["checkout"])
        revisions = {}
        from contextlib import nullcontext
        with self.store.transaction() if transaction else nullcontext():
            operation.update(self.completable(operation))
            for path, image in actual.items():
                revisions[path] = self.store.revision(checkout["id"], path, image["blob"], image["mode"], attribution,
                                                      operation["id"], operation["unit"])
            operation.update(state="verified", result=actual, revisions=revisions, evidence=evidence, completed=time.time())
            self.store.save(operation)
            self.store.event("operation/verified", operation["id"], {"revisions": revisions, "attribution": attribution})
            if operation["kind"] != "restore":
                for path in operation["paths"]:
                    base = self.store.db.execute("SELECT operation,attribution FROM revisions WHERE id=?", (operation["bases"][path],)).fetchone()
                    if base and base["attribution"] == "restore":
                        self.store.db.execute("UPDATE resolutions SET edit=? WHERE edit IS NULL AND restore=? AND unknown_id IN "
                                              "(SELECT id FROM unknowns WHERE checkout=? AND path=?)",
                                              (operation["id"], base["operation"], checkout["id"], path))
                for resolution in list(self.store.db.execute("SELECT s.unknown_id,s.restore FROM resolutions s JOIN unknowns u "
                        "ON u.id=s.unknown_id WHERE u.checkout=? AND u.path='*' AND s.edit IS NULL", (checkout["id"],))):
                    restore = self.store.get(resolution["restore"])
                    if not set(restore["paths"]) <= set(operation["paths"]):
                        continue
                    bases = [self.store.db.execute("SELECT operation,attribution FROM revisions WHERE id=?",
                             (operation["bases"][p],)).fetchone() for p in restore["paths"]]
                    if all(b and b["attribution"] == "restore" and b["operation"] == restore["id"] for b in bases):
                        self.store.db.execute("UPDATE resolutions SET edit=? WHERE unknown_id=?", (operation["id"], resolution["unknown_id"]))
        return operation

    def completable(self, operation):
        """Read the durable state at the receipt transaction's commit boundary."""
        current = self.store.get(operation["id"])
        records = [current]
        if current.get("parent_operation"):
            records.append(self.store.get(current["parent_operation"]))
        for record in records:
            if record["state"] not in ("prepared", "allowed", "running"):
                raise CoordinatorError("OPERATION_STATE", "Operation cannot receive receipts after quarantine or completion",
                                       {"operation": record["id"], "state": record["state"]})
        return current

    async def settle_unapplied(self, operation, reason, still):
        """A patch native ended without applying it (it never began, or reported failure). With every endpoint
        still at its pre-image it changed nothing: it is settled as no effect and stops excluding its paths.
        Any other bytes may be its effect, so it is quarantined. `still(operation)` is the caller's evidence
        that native is done with it; evidence arriving while the endpoints are read (an item, a Post hook)
        wins, so it is checked again in the transaction that settles it and before any quarantine."""
        operation = self.store.get(operation["id"])
        if operation.get("parent_operation"):
            operation = self.store.get(operation["parent_operation"])
        parts = [self.store.get(key) for key in operation.get("parts", [])] or [operation]
        def current():
            fresh = self.store.get(operation["id"])
            return fresh if fresh["state"] in ("prepared", "allowed", "running") and still(fresh) else None
        from contextlib import AsyncExitStack
        try:
            async with AsyncExitStack() as stack:
                for key in sorted({part["checkout"] for part in parts}):
                    await stack.enter_async_context(self.lock(key))
                if not current():
                    return  # Settled meanwhile, or its own lifecycle evidence arrived: that decides.
                parts = [self.store.get(key) for key in operation.get("parts", [])] or [self.store.get(operation["id"])]
                for part in parts:
                    checkout = self.store.get(part["checkout"])
                    for path in part["paths"]:
                        if await asyncio.to_thread(read_path, self.store, checkout, path) != part["pre"][path]:
                            raise CoordinatorError("PATCH_EFFECT", "An endpoint differs from its pre-image", {"path": path})
                with self.store.transaction():
                    fresh = current()
                    if not fresh:
                        return
                    # Check every record before any changes: a part's check reads its parent's state.
                    records = [self.completable(record) for record in [fresh] + [part for part in parts if part["id"] != fresh["id"]]]
                    for record in records:
                        record.update(state="denied", no_effect=True, refusal=reason, completed=time.time())
                        self.store.save(record)
                    self.store.event("operation/no_effect", operation["id"], {"reason": reason})
        except CoordinatorError as exc:
            if exc.code == "OPERATION_STATE" or not current():
                return
            await self.quarantine(operation, reason + ": " + exc.code)
        except OSError as exc:
            if not current():
                return
            await self.quarantine(operation, reason + ": " + type(exc).__name__)

    async def quarantine(self, operation, reason, executing=False):
        """Its effects are unknown. With `executing`, native may still be writing for it: admission, restoration
        and quiet windows keep treating its paths as busy until `end_execution`."""
        # State changes must win before waiting for filesystem/ledger locks.
        # Reload rather than saving an object captured before a competing await.
        operation = self.store.get(operation["id"])
        if operation.get("parent_operation"):
            operation = self.store.get(operation["parent_operation"])
        parts = [self.store.get(key) for key in operation.get("parts", [])]
        from .participants import checkout_ids
        scopes = {}
        with self.store.transaction():
            for record in [operation, *parts]:
                record.update(state="quarantined", refusal=reason)
                if executing:
                    record["executing"] = True
                self.store.save(record)
            for part in parts or [operation]:
                ids = checkout_ids(self.store, part["unit"]) if part["kind"] in ("command", "mcp", "lead-command") else [part["checkout"]]
                # An operation with paths outside the ledger only could change nothing the ledger records.
                targets = part["paths"] or ([] if part.get("outside") else ["*"])
                for key in ids if targets else []:
                    scopes.setdefault(key, set()).update(targets)
                    for path in targets:
                        self.store.unknown(key, path, reason, {"possible_operation": part["id"]})
        async def reconcile(key, paths):
            checkout = self.store.get(key)
            try:
                await self.reconcile(checkout, paths=None if "*" in paths else sorted(paths), reason=reason, ignore_pending=False)
            except (OSError, CoordinatorError):
                checkout["quarantine"] = "filesystem reconciliation incomplete"
                self.store.save(checkout)
        await asyncio.gather(*(reconcile(key, paths) for key, paths in scopes.items()))

    def end_execution(self, operation, transaction=True):
        """Native can no longer write for this operation: its handler ended, or its app-server exited. A quarantine
        stops holding the paths it kept busy. Its parts carry the flag with it."""
        operation = self.store.get(operation["id"])
        if operation.get("parent_operation"):
            operation = self.store.get(operation["parent_operation"])
        records = [r for r in [operation] + [self.store.get(key) for key in operation.get("parts", [])] if r.get("executing")]
        if not records:
            return
        from contextlib import nullcontext
        with self.store.transaction() if transaction else nullcontext():
            for record in records:
                record.pop("executing")
                self.store.save(record)
            self.store.event("operation/execution_ended", operation["id"], {})
