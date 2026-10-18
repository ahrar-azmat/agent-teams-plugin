"""File revisions, complete patch scopes and verified operation receipts.

Claims are deliberately absent from the authorship decision. Only the pre-image,
matching native hook lifecycle, computed native diff and observed post-image can
establish an exact patch. UNKNOWN history cannot be erased by matching bytes.
"""
import asyncio
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
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
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise CoordinatorError("PATH_SYMLINK", "Mutation traverses a symlink", {"path": str(relative)})
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise CoordinatorError("PATH_HARDLINK", "Hardlinked mutations cannot be modelled", {"path": str(relative)})
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
            raise CoordinatorError("PATH_TYPE", "Unsupported filesystem node", {"path": str(relative)})
    name = relative.as_posix()
    alias = unicodedata.normalize("NFD", name)
    if not checkout["case_sensitive"]:
        alias = alias.casefold()
    return {"path": name, "key": alias}


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
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise CoordinatorError("PATH_TYPE", "Only ordinary single-link files have receipts", {"path": name})
                blob = store.blob(stream)
                after = os.fstat(stream.fileno())
                current = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
                fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_nlink)
                if fields(before) == fields(after) == fields(current):
                    return {"blob": blob, "mode": "100755" if before.st_mode & 0o111 else "100644"}
        except FileNotFoundError:
            return {"blob": None, "mode": None}
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
    raise CoordinatorError("UNSTABLE_READ", "File changed while reading", {"path": name})


def _snapshot_once(store, checkout):
    found, directories = {}, []
    def failed(error):
        raise error
    for directory, dirs, files in os.walk(checkout["root"], followlinks=False, onerror=failed):
        dirs[:] = [name for name in dirs if name != ".git"]
        info = os.stat(directory, follow_symlinks=False)
        directories.append((directory, info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns))
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise CoordinatorError("PATH_SYMLINK", "Full reconciliation encountered a directory symlink")
        for name in files:
            if name == ".git":
                continue
            path = (Path(directory) / name).relative_to(checkout["root"]).as_posix()
            found[path] = read_path(store, checkout, path)
    for directory, device, inode, modified, changed in directories:
        info = os.stat(directory, follow_symlinks=False)
        if (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns) != (device, inode, modified, changed):
            raise CoordinatorError("UNSTABLE_SNAPSHOT", "Directory entries changed during the full snapshot")
    return found


def full_snapshot(store, checkout):
    for _ in range(3):
        try:
            return _snapshot_once(store, checkout)
        except CoordinatorError as exc:
            if exc.code != "UNSTABLE_SNAPSHOT":
                raise
    raise CoordinatorError("UNSTABLE_SNAPSHOT", "Directory entries did not stabilize for reconciliation")


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
        if existing:
            if existing["state"] != "ready":
                raise CoordinatorError("CHECKOUT_QUARANTINED", "Canonical checkout registration needs reconciliation",
                                       {"identity": identity, "state": existing["state"], "members": existing["members"]})
            record = self.store.get(existing["checkout"])
            self.require_owned(record)
            return record
        ident = uid("checkout")
        metadata.update(root=root, case_sensitive=await asyncio.to_thread(case_sensitive, root))
        with self.store.transaction():
            self.store.put("checkout", ident, metadata)
            self.store.db.execute("INSERT INTO checkout_identities VALUES(?,?,?,?)", (identity, ident, "initializing", '[]'))
        checkout = self.store.get(ident)
        try:
            snapshot = await asyncio.to_thread(full_snapshot, self.store, checkout)
            base_entries = await asyncio.to_thread(gitio.entries, root, metadata["base"])
            baseline, blobs_by_oid = {}, {}
            for path in set(snapshot) | set(base_entries):
                entry = base_entries.get(path, [])
                if entry and (len(entry) != 1 or entry[0]["mode"] not in ("100644", "100755")):
                    raise CoordinatorError("BASE_TYPE", "Unsupported base tree entry", {"path": path})
                oid = entry[0]["oid"] if entry else None
                if oid and oid not in blobs_by_oid:
                    data = await asyncio.to_thread(gitio.git, root, ["cat-file", "blob", oid])
                    blobs_by_oid[oid] = await asyncio.to_thread(self.store.blob, data.stdout)
                baseline[path] = {"blob": blobs_by_oid.get(oid),
                                  "mode": entry[0]["mode"] if entry else None}
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

    def mutation_scope(self, unitid):
        checkout = self.store.get(self.store.get(unitid)["checkout"])
        paths = set()
        for operation in self.store.all("operation", unitid):
            if operation["kind"] not in ("patch", "lead-edit", "restore"):
                continue
            if operation["state"] == "denied" and operation.get("no_effect") is True:
                continue
            paths.update(operation["paths"])
            for entry in operation.get("intended", {}).get("scope", []):
                paths.add(entry["path"])
                if entry.get("move"):
                    paths.add(entry["move"])
        return sorted({normalize(checkout, path)["path"] for path in paths})

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
        async with self.lock(checkout["id"]):
            try:
                if paths is None:
                    actual = await asyncio.to_thread(full_snapshot, self.store, checkout)
                    paths = set(actual) | {r[0] for r in self.store.db.execute("SELECT path FROM heads WHERE checkout=?", (checkout["id"],))}
                else:
                    actual = {path: await asyncio.to_thread(read_path, self.store, checkout, path) for path in paths}
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
            pending = set()
            if ignore_pending:
                for operation in self.store.all("operation"):
                    if operation["checkout"] == checkout["id"] and operation["state"] in ("prepared", "allowed", "running") and operation["kind"] in ("patch", "lead-edit", "restore"):
                        pending.update(operation["paths"])
            context = [r["id"] for r in self.store.all("custody") if r["checkout"] == checkout["id"] and r["state"] != "released"]
            # The caller's proof is checked again after the scan, with nothing awaited before the writes.
            if attribute and attribute.get("still_quiet") and not attribute["still_quiet"]():
                attribute = None
            unknowns, attributed = [], []
            with self.store.transaction():
                for path in paths:
                    if path in pending:
                        continue
                    current = actual.get(path, {"blob": None, "mode": None})
                    head = self.store.head(checkout["id"], path)
                    known = {"blob": head["blob"], "mode": head["mode"]} if head else {"blob": None, "mode": None}
                    if current != known:
                        # A caller that proved nothing else could have changed the tree names the author.
                        if attribute and not any(overlaps(normalize(checkout, path)["key"], key) for key in attribute["excluded"]):
                            self.store.revision(checkout["id"], path, current["blob"], current["mode"], attribute["attribution"],
                                                attribute["operation"], attribute["unit"])
                            attributed.append(path)
                            continue
                        revision = self.store.revision(checkout["id"], path, current["blob"], current["mode"], "UNKNOWN")
                        unknowns.append(self.store.unknown(checkout["id"], path, reason,
                                                          {"custody": context, "label": "custody" if context else "UNKNOWN"}, revision))
            result = {"unknowns": unknowns, "paths_scanned": len(paths)}
            if attribute:
                result["attributed"] = attributed
            return result

    async def prepare(self, actor, kind, intended, paths, native=None):
        unit, checkout = self.store.get(actor["unit"]), self.store.get(actor["checkout"])
        self.require_owned(checkout)
        if unit["state"] != "open" or checkout.get("quarantine"):
            raise CoordinatorError("UNIT_FROZEN", "Unit or checkout is not open for operations")
        paths = sorted({normalize(checkout, path)["path"] for path in paths})
        keys = [normalize(checkout, path)["key"] for path in paths]
        if any(overlaps(left, right) for i, left in enumerate(keys) for right in keys[i + 1:]):
            raise CoordinatorError("OPERATION_SCOPE", "A single mutation cannot contain aliased or ancestor/descendant endpoints")
        async with self.lock(checkout["id"]):
            from .custody import interfering
            started = time.time()
            if paths and interfering(self.store, checkout, paths):
                raise CoordinatorError("CUSTODY_CONFLICT", "Command custody overlaps the intended mutation")
            for operation in self.store.all("operation"):
                if operation["checkout"] == checkout["id"] and operation["state"] in ("prepared", "allowed", "running", "quarantined"):
                    if kind == "restore" and operation["state"] == "quarantined" and operation["paths"] and set(operation["paths"]) <= set(paths):
                        continue
                    existing = [normalize(checkout, path)["key"] for path in operation["paths"]]
                    if any(overlaps(a, b) for a in keys for b in existing):
                        raise CoordinatorError("OPERATION_BUSY", "An overlapping operation is unreconciled", {"operation": operation["id"]})
            pre = {path: await asyncio.to_thread(read_path, self.store, checkout, path) for path in paths}
            if self.store.get(actor["unit"])["state"] != "open":
                raise CoordinatorError("UNIT_FROZEN", "Unit froze while operation pre-images were being saved")
            if paths and interfering(self.store, checkout, paths, since=started):
                raise CoordinatorError("CUSTODY_CONFLICT", "Command custody changed during pre-image capture")
            bases = {}
            for path, image in pre.items():
                head = self.store.head(checkout["id"], path)
                known = {"blob": head["blob"], "mode": head["mode"]} if head else {"blob": None, "mode": None}
                if image != known:
                    with self.store.transaction():
                        rev = self.store.revision(checkout["id"], path, image["blob"], image["mode"], "UNKNOWN")
                        self.store.unknown(checkout["id"], path, "stale pre-image at operation admission", {}, rev)
                    raise CoordinatorError("STALE_BASE", "Path changed outside a verified operation", {"path": path})
                bases[path] = head and head["id"]
            opid = uid("op")
            if kind in ("command", "mcp", "lead-command"):
                bases = {row["path"]: row["revision"] for row in self.store.db.execute(
                         "SELECT path,revision FROM heads WHERE checkout=?", (checkout["id"],))}
            dependencies = [r["revisions"] for r in self.store.all("read", actor["id"])]
            operation = {"checkout": checkout["id"], "unit": unit["id"], "actor": actor["id"], "kind": kind,
                         "attempt": actor.get("attempt"), "generation": self.store.generation,
                         "state": "prepared", "paths": paths, "pre": pre, "bases": bases,
                         "intended": intended, "native": native or {}, "dependencies": dependencies,
                         "created": started, "hook_valid": False}
            with self.store.transaction():
                self.store.put("operation", opid, operation, unit["id"])
                self.store.event("operation/prepared", opid, {"bases": bases, "native": native or {}})
            return self.store.get(opid)

    async def complete_patch(self, operation, item):
        checkout = self.store.get(operation["checkout"])
        try:
            if item.get("status") != "completed" or not operation.get("hook_valid") or not operation.get("post_hook"):
                raise CoordinatorError("PATCH_EVIDENCE", "Patch lacks successful native lifecycle evidence")
            if operation.get("post_payload", {}).get("command") != operation["intended"]["patch"]:
                raise CoordinatorError("PATCH_REWRITTEN", "Effective post-tool patch differs from the durably admitted input")
            expected, seen = {}, set()
            intended = operation["intended"]["scope"]
            native_changes = item.get("changes", [])
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
                pre = operation["pre"][path]
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
            revisions = {}
            with self.store.transaction():
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

    async def quarantine(self, operation, reason):
        checkout = self.store.get(operation["checkout"])
        operation.update(state="quarantined", refusal=reason)
        with self.store.transaction():
            self.store.save(operation)
            for path in operation["paths"] or ["*"]:
                self.store.unknown(checkout["id"], path, reason, {"possible_operation": operation["id"]})
        try:
            await self.reconcile(checkout, paths=operation["paths"] or None, reason=reason, ignore_pending=False)
        except (OSError, CoordinatorError):
            checkout["quarantine"] = "filesystem reconciliation incomplete"
            self.store.save(checkout)
