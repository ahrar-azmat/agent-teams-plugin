#!/usr/bin/env python3
"""Align a codex SOURCE worktree to the INSTALLED codex binary's release tag.

Why this exists (the map-vs-territory rule)
-------------------------------------------
Upstream source is the MAP: it names the config keys, event types and
mechanisms worth probing. The INSTALLED binary is the TERRITORY: only it
decides what is true at runtime. Reading the clone's main-branch HEAD has
already produced wrong conclusions twice in this repo's history — main and
the release tags are DIVERGENT (releases are cut on branches), so main
contains code the deployed binary does not have, and vice versa.

This script keeps ONE stable path — ~/Documents/codex-installed by default —
checked out at exactly `rust-v<installed version>`, re-aligning itself
whenever the codex CLI is updated. Run it before any session that reads
upstream source; it is idempotent and safe to run any time.

    python3 scripts/codex_src.py            # align (creates worktree on first run)
    CODEX_SRC_CLONE=...     override the reference clone (default ~/Documents/codex)
    CODEX_SRC_WORKTREE=...  override the aligned path  (default ~/Documents/codex-installed)

The reference clone itself is NEVER touched (other sessions pin its HEAD);
the aligned tree is a linked `git worktree` sharing the same object store.
Exit 0 = aligned (or already aligned); exit 1 = refused, with the reason.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn


def run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    return p.returncode, (p.stdout or p.stderr or "").strip()


def die(msg: str) -> NoReturn:
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    clone = Path(os.environ.get("CODEX_SRC_CLONE", "~/Documents/codex")).expanduser()
    wt = Path(os.environ.get("CODEX_SRC_WORKTREE", "~/Documents/codex-installed")).expanduser()

    rc, out = run(["codex", "--version"])
    if rc != 0:
        die(f"`codex --version` failed: {out[:200]}")
    m = re.search(r"codex-cli (\d+\.\d+\.\d+\S*)", out)
    if not m:
        die(f"could not parse a version out of {out!r}")
    version = m.group(1)
    tag = f"rust-v{version}"

    if not (clone / ".git").exists():
        die(f"{clone} is not a git clone (set CODEX_SRC_CLONE)")

    # Self-heal stale worktree metadata (e.g. the aligned dir was rm -rf'd).
    run(["git", "worktree", "prune"], cwd=clone)

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

    if wt.exists():
        rc, common = run(["git", "rev-parse", "--path-format=absolute",
                          "--git-common-dir"], cwd=wt)
        if rc != 0 or Path(common).resolve() != (clone / ".git").resolve():
            die(f"{wt} exists but is not a worktree of {clone} — move it aside")
        rc, dirty = run(["git", "status", "--porcelain"], cwd=wt)
        if rc == 0 and dirty:
            die(
                f"{wt} has local modifications — the reference tree must stay "
                f"pristine (it is read-only documentation of the installed "
                f"binary). Revert or move your changes, then rerun."
            )
        _, cur = run(["git", "rev-parse", "HEAD"], cwd=wt)
        if cur == sha:
            print(f"already aligned: {wt} @ {tag} ({sha[:9]}) — codex-cli {version}")
            return
        rc, out = run(["git", "checkout", "--detach", tag], cwd=wt)
        if rc != 0:
            die(f"checkout {tag} failed in {wt}: {out[:300]}")
        print(f"re-aligned: {wt} {cur[:9]} → {tag} ({sha[:9]}) — codex-cli {version}")
        return

    rc, out = run(["git", "worktree", "add", "--detach", str(wt), tag], cwd=clone)
    if rc != 0:
        die(f"worktree add failed: {out[:300]}")
    print(f"created: {wt} @ {tag} ({sha[:9]}) — codex-cli {version}")


if __name__ == "__main__":
    main()
