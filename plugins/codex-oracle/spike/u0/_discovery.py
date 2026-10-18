"""Uncapped metadata-only discovery prototype and measured synthetic workload."""
import sys
sys.dont_write_bytecode = True

import os
from pathlib import Path
import statistics
import tempfile
import time

from _common import git, init_repo, require


def discover(workspace):
    start = time.monotonic()
    visited, repositories, metadata_dirs = {}, {}, set()
    directory_versions = {}
    aliases, errors, boundaries = [], [], []
    entries = directories = git_queries = 0
    pending = [Path(workspace)]
    while pending:
        path = pending.pop()
        try:
            physical = path.resolve(strict=True)
            st = physical.stat()
            identity = (st.st_dev, st.st_ino)
            if path.is_symlink():
                aliases.append({"path": str(path), "canonical": str(physical), "kind": "directory_symlink"})
            if physical in metadata_dirs:
                continue
            # A separate git-dir may be encountered before its checkout's .git
            # file. Recognize metadata, not directory names, before descending.
            if ((physical / "HEAD").is_file() and
                    ((physical / "objects").is_dir() or (physical / "commondir").is_file())):
                git_queries += 1
                metadata = git(physical, "rev-parse", "--resolve-git-dir", str(physical))
                if metadata.returncode == 0:
                    metadata_dirs.add(physical)
                    continue
            if identity in visited:
                aliases.append({"path": str(path), "canonical": str(physical), "first": visited[identity]})
                continue
            visited[identity] = str(path)
            directory_versions[str(path)] = (st.st_dev, st.st_ino, st.st_mtime_ns)
            directories += 1
            marker = physical / ".git"
            if os.path.lexists(marker):
                git_queries += 1
                result = git(physical, "rev-parse", "--show-toplevel", "--absolute-git-dir",
                             "--path-format=absolute", "--git-common-dir", "--is-bare-repository")
                lines = result.stdout.splitlines()
                if result.returncode or len(lines) != 4 or lines[3] != "false" or Path(lines[0]).resolve() != physical:
                    errors.append({"path": str(physical), "reason": "invalid/unreadable Git boundary", "stderr": result.stderr})
                    boundaries.append({"path": str(physical), "ownership": "unknown"})
                else:
                    git_dir = Path(lines[1]).resolve(strict=True)
                    gs = git_dir.stat()
                    common = Path(lines[2]).resolve(strict=True)
                    metadata_dirs.update((git_dir, common))
                    key = f"{st.st_dev}:{st.st_ino}:{gs.st_dev}:{gs.st_ino}"
                    repositories[key] = {"checkout": str(physical), "checkout_identity": list(identity),
                        "git_dir": str(git_dir), "git_dir_identity": [gs.st_dev, gs.st_ino],
                        "common_dir": str(common), "marker_type": "directory" if marker.is_dir() else "file",
                        "discovered_via": str(path)}
                    boundaries.append({"path": str(physical), "ownership": key})
            with os.scandir(physical) as iterator:
                children = list(iterator)
            entries += len(children)
            # Sorting is for repeatable evidence, not a priority that drops later entries.
            for child in sorted(children, key=lambda e: e.name, reverse=True):
                if child.name == ".git":
                    continue
                try:
                    if child.is_dir(follow_symlinks=True):
                        pending.append(Path(child.path))
                    elif child.is_symlink() and not Path(child.path).exists():
                        errors.append({"path": child.path, "reason": "dangling symlink; target type unknown"})
                except OSError as exc:
                    errors.append({"path": child.path, "reason": str(exc)})
        except OSError as exc:
            errors.append({"path": str(path), "reason": str(exc)})
    for path, version in directory_versions.items():
        try:
            current = Path(path).stat()
            if (current.st_dev, current.st_ino, current.st_mtime_ns) != version:
                errors.append({"path": path, "reason": "directory changed during traversal"})
        except OSError as exc:
            errors.append({"path": path, "reason": str(exc)})
    return {"entries_visited": entries, "directories_visited": directories,
            "git_metadata_subprocess_count": git_queries, "repositories_found": len(repositories),
            "repositories": list(repositories.values()), "aliases_or_cycles": aliases,
            "boundaries": boundaries, "errors": errors, "complete": not errors,
            "elapsed_seconds": time.monotonic() - start}


def benchmark(scratch):
    results = []
    for count in (1000, 10000, 100000):
        tmp = tempfile.TemporaryDirectory(prefix=f"discovery-{count}-", dir=scratch)
        start = time.monotonic()
        base = Path(tmp.name)
        workspace = init_repo(base / "workspace")
        (workspace / ".gitignore").write_text("*\n")
        planted = [workspace]
        for relative in ("ignored/repo", "ignored/repo/deeper", "node_modules/package/repository", "CaseRepo"):
            planted.append(init_repo(workspace / relative))
        planted.append(init_repo(workspace / "gitfile", separate=base / "separate-metadata"))
        outside = init_repo(base / "external-through-symlink")
        planted.append(outside)
        (workspace / "outside-alias").symlink_to(outside, target_is_directory=True)
        (workspace / "nested-alias").symlink_to(workspace / "ignored/repo", target_is_directory=True)
        (workspace / "cycle").symlink_to(workspace, target_is_directory=True)
        case_alias = workspace / "caserepo"
        case_insensitive = case_alias.exists() and case_alias.stat().st_ino == (workspace / "CaseRepo").stat().st_ino
        # A second spelling is a filesystem alias on insensitive volumes. On a
        # sensitive volume plant a distinct repository and require both identities.
        if not case_insensitive:
            planted.append(init_repo(case_alias))
        for index in range(count):
            folder = workspace / "node_modules" / f"pkg-{index // 100:05d}"
            if index % 100 == 0:
                folder.mkdir(parents=True)
            (folder / f"file-{index % 100:03d}.js").write_bytes(b"// synthetic\n")
        construction = time.monotonic() - start
        traversals = []
        try:
            for _ in range(4):
                measurement = discover(workspace)
                require(measurement["complete"], "Synthetic discovery incomplete")
                found = {r["checkout"] for r in measurement["repositories"]}
                require(found == {str(p.resolve()) for p in planted}, "Discovery missed or invented a planted repository")
                traversals.append(measurement)
        finally:
            cleanup_start = time.monotonic()
            tmp.cleanup()
            cleanup = time.monotonic() - cleanup_start
        later = [t["elapsed_seconds"] for t in traversals[1:]]
        results.append({"ordinary_files": count, "planted_repositories": len(planted),
            "construction_seconds": construction, "first_traversal_seconds": traversals[0]["elapsed_seconds"],
            "subsequent_seconds": {"min": min(later), "median": statistics.median(later), "max": max(later)},
            "case_insensitive_volume": case_insensitive, "case_alias_identity_equal": case_insensitive,
            "traversals": traversals, "cleanup_seconds": cleanup, "scratch_root": str(base),
            "cleanup_verified": not base.exists(), "cache_control": "none; first traversal is not claimed cold"})
    return results
