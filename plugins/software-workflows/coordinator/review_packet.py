"""Bound object manifests and context-budgeted review input, never a full diff RPC."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

from treedigest import git_argv, git_env
from .client import CoordinatorError
from . import gitio
from .store import digest, encoded


# Allocate 50% of the registry's EFFECTIVE window; leave the other 50% for
# instructions, tool schemas, object inspection, reasoning and output. Use one
# UTF-8 byte per allocated token as a deliberately conservative byte budget,
# not an estimated tokenizer ratio. Example measured on 0.163.0: 272000 *
# 95 / 100 * 50 / 100 = 129200 bytes. No model window is hard-coded here.
REVIEW_WINDOW_PERCENT = 50


def context_budget(model, executable):
    cache = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "models_cache.json"
    try:
        version = subprocess.run([*executable, "--version"], capture_output=True, check=True, timeout=15).stdout.decode().strip()
        source = str(cache)
        try:
            if cache.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("registry exceeds cache format limit")
            raw = cache.read_bytes()
            registry = json.loads(raw)
            if registry.get("client_version") != version.removeprefix("codex-cli "):
                raise ValueError("another binary refreshed the shared cache")
        except (OSError, ValueError):
            # 0.163.0 exposes its raw bundled catalog explicitly. A private
            # home prevents config/account access and shared-cache races. This
            # command does no refresh or inference (--bundled).
            with tempfile.TemporaryDirectory(prefix="software-workflows-models-") as home:
                raw = subprocess.run([*executable, "debug", "models", "--bundled"],
                    env=dict(os.environ, CODEX_HOME=home), capture_output=True, check=True, timeout=15).stdout
            registry = json.loads(raw)
            source = "deployed binary: debug models --bundled"
        client = registry.get("client_version", version.removeprefix("codex-cli "))
        # Legacy servers do not report their selected model. Use the smallest
        # measured effective window in this binary's catalog, rather than guess
        # which long-lived server/config dispatched the request.
        entry = (next(e for e in registry["models"] if e.get("slug") == model) if model else
                 min(registry["models"], key=lambda e: int(e.get("context_window") or e["max_context_window"]) *
                     int(e["effective_context_window_percent"])))
        window = int(entry.get("context_window") or entry["max_context_window"])
        effective = int(entry["effective_context_window_percent"])
        if window <= 0 or not 0 < effective <= 100:
            raise ValueError("invalid effective context window")
        return {"model": entry["slug"], "model_selection": "requested" if model else "minimum deployed catalog window (legacy caller omits model)",
                "context_window": window, "effective_context_window_percent": effective,
                "allocation_percent": REVIEW_WINDOW_PERCENT,
                "packet_bytes": window * effective * REVIEW_WINDOW_PERCENT // 10000,
                "registry": source, "registry_sha256": hashlib.sha256(raw).hexdigest(),
                "registry_client_version": client, "registry_fetched_at": registry.get("fetched_at"),
                "executable": list(executable), "cli_version": version}
    except (OSError, ValueError, KeyError, StopIteration, TypeError, subprocess.SubprocessError) as exc:
        raise CoordinatorError("REVIEW_CONTEXT_UNKNOWN", "Cannot budget a sealed review from the deployed model registry",
                               {"model": model, "registry": str(cache), "reason": str(exc)}) from exc


def limited_diff(root, base, candidate, path, limit):
    """Spool, count and hash the complete patch; retain at most limit bytes."""
    env = dict(git_env(), GIT_LITERAL_PATHSPECS="1")
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", base, candidate, "--", path]
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run(git_argv(root, args), env=env, stdout=output, stderr=errors, timeout=120)
        if result.returncode:
            raise CoordinatorError("REVIEW_OBJECTS", "Could not read bound patch", {"root": root, "path": path, "exit_code": result.returncode})
        size = output.tell()
        output.seek(0)
        checksum = hashlib.sha256()
        prefix = bytearray()
        while chunk := output.read(65536):
            checksum.update(chunk)
            if len(prefix) < limit:
                prefix.extend(chunk[:limit - len(prefix)])
        return size, checksum.hexdigest(), bytes(prefix) if size <= limit else None


def manifest(seal, checkout):
    participants = seal.get("vector") or [dict(seal, root=checkout["root"], checkout=checkout["id"], unchanged=False)]
    result = []
    for item in participants:
        root, base = item["root"], item["base"]
        candidate = item.get("candidate") or gitio.text(root, ["rev-parse", base + "^{tree}"])
        part = {k: item[k] for k in ("root", "checkout", "base")}
        part.update(candidate_tree=candidate, unchanged=bool(item.get("unchanged")), paths=[])
        raw = gitio.git(root, ["diff", "--raw", "--no-abbrev", "--no-renames", "-z", base, candidate]).stdout.split(b"\0")
        for offset in range(0, len(raw) - 1, 2):
            old_mode, new_mode, old, new, status = raw[offset].decode("ascii").lstrip(":").split()
            path = raw[offset + 1].decode("utf-8", "strict")
            entry = {"path": path, "status": status, "old_mode": old_mode, "new_mode": new_mode,
                     "old_blob": old, "new_blob": new}
            for side, oid in (("old", old), ("new", new)):
                entry[side + "_bytes"] = int(gitio.text(root, ["cat-file", "-s", oid])) if set(oid) != {"0"} else 0
            part["paths"].append(entry)
        part["diffstat"] = gitio.git(root, ["diff", "--no-ext-diff", "--no-textconv", "--shortstat", base, candidate]).stdout.decode("utf-8", "replace").strip()
        result.append(part)
    return result


def build(store, seal, checkout, job, budget):
    if not isinstance(budget, dict) or not isinstance(budget.get("packet_bytes"), int) or budget["packet_bytes"] <= 0:
        raise CoordinatorError("REVIEW_CONTEXT_UNKNOWN", "Review preparation requires a measured model registry budget")
    parts = manifest(seal, checkout)
    limit = budget["packet_bytes"]
    patches = []
    for part in parts:
        for entry in part["paths"]:
            size, checksum, patch = limited_diff(part["root"], part["base"], part["candidate_tree"], entry["path"], limit)
            entry.update(patch_bytes=size, patch_sha256=checksum, inlined=False)
            patches.append((size, part, entry, patch))
    header = {"packet_version": 1, "seal": seal["seal"], "job": job, "budget": budget}
    instructions = ("Review only these bound Git objects. Candidates are tree objects; bases are commits. "
                    "Every path with inlined=false must be inspected from its bound objects, in manageable chunks. "
                    "Use git --literal-pathspecs -C <root> diff --no-ext-diff --no-textconv <base> <candidate_tree> -- <path>; "
                    "quote paths literally. Blob IDs and byte sizes identify both sides. Working-tree files are not review evidence.\n")
    def render():
        return instructions + encoded(header) + "\nOBJECT MANIFEST\n" + encoded(parts) + "\nINLINE PATCHES\n"
    # Reserve wrapper space within this allocation as well as the explicit 50%
    # context headroom. Metadata is included before choosing small patches.
    remaining = limit - len(render().encode()) - 8192
    inline = []
    for size, part, entry, patch in sorted(patches, key=lambda p: (p[0], p[1]["root"], p[2]["path"])):
        label = "\nRepository " + json.dumps(part["root"]) + "; path " + json.dumps(entry["path"]) + "\n"
        if patch is not None:
            text = patch.decode("utf-8", "replace")
            cost = len((label + text).encode())
            if cost <= remaining:
                inline.append(label + text)
                entry["inlined"] = True
                remaining -= cost
    packet = render() + "".join(inline)
    # An unlimited path count cannot fit a finite context. Preserve every entry
    # in a private complete manifest, with explicit paging rather than truncation.
    path = None
    if len(packet.encode()) > limit - 8192:
        for _, _, entry, _ in patches:
            entry["inlined"] = False
        path = store.root / "review-packets" / (job + ".json")
        payload = encoded(parts).encode()
        packet = (instructions + encoded(header) + "\nComplete object manifest: " + shlex.quote(str(path)) +
                  "\nManifest sha256: " + hashlib.sha256(payload).hexdigest() +
                  "\nThe manifest exceeds the prompt allocation. Read every repository and path entry in bounded pages; "
                  "no paths or repositories have been omitted from that file. No patches are inlined.\n")
    if len(packet.encode()) > limit:
        raise CoordinatorError("REVIEW_CONTEXT_TOO_SMALL", "Registry window cannot hold the review instructions and manifest locator")
    if path is not None:
        path.parent.mkdir(mode=0o700, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(payload)
    return {"packet": packet, "packet_version": 1, "manifest": parts, "manifest_path": str(path) if path else None,
            "manifest_digest": digest(parts), "packet_digest": digest(packet), "packet_bytes": len(packet.encode()), "budget": budget}
