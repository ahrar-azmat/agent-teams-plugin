"""Durable hook routing hints. Claims and identity checks stay in the ledger."""
import os

from .runtime import atomic_json


def publish(store):
    roots = []
    for checkout in store.all("checkout"):
        if not checkout.get("ready"):
            continue
        try:
            info = os.stat(checkout["root"])
            filesystem = [info.st_dev, info.st_ino]
        except OSError:
            filesystem = None
        roots.append({"root": checkout["root"], "filesystem": filesystem,
                      "identity": checkout["identity"]})
    atomic_json(store.root / "checkouts.json", {"version": 1, "roots": roots})
