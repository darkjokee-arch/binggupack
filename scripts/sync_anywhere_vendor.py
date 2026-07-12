#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sync the v1.21-A read core into the Anywhere Python Worker vendor tree.

The Cloudflare Python Worker bundles only files under its ``main`` directory, so
the read core must be vendored next to the worker entry. To honour the "do not
reimplement the read core" boundary, the vendored copy MUST be byte-identical to
the canonical source. This script copies the exact module set and can verify it.

    python scripts/sync_anywhere_vendor.py           # copy source -> vendor
    python scripts/sync_anywhere_vendor.py --check    # fail if vendor drifts

The module set is the closure actually loaded by ``binggupack.app.conformance``
(measured), i.e. read_core + its lazy imports (models, cli.daily, pack.contract_validate,
safety.{gate_text,path_safety,public_tree_scan}) plus package __init__/__about__.
"""
import argparse
import filecmp
import hashlib
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "binggupack")
VENDOR = os.path.join(REPO, "hosted", "workers", "anywhere", "core", "binggupack")

# Exact module set (relative to binggupack/). Keep in sync with the measured
# import closure of the read core; conformance.py is included for the self-check.
MODULES = [
    "__init__.py",
    "__about__.py",
    "app/__init__.py",
    "app/models.py",
    "app/read_core.py",
    "app/snapshot.py",
    "app/service.py",
    "app/conformance.py",
    "cli/__init__.py",
    "cli/daily.py",
    "pack/__init__.py",
    "pack/contract_validate.py",
    "safety/__init__.py",
    "safety/gate_text.py",
    "safety/path_safety.py",
    "safety/public_tree_scan.py",
]


def _digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def do_sync():
    copied = []
    for rel in MODULES:
        s = os.path.join(SRC, rel)
        d = os.path.join(VENDOR, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(s, d)
        copied.append(rel)
    print("[sync] copied %d modules -> %s" % (len(copied), VENDOR))
    return 0


def do_check():
    missing, drift = [], []
    for rel in MODULES:
        s = os.path.join(SRC, rel)
        d = os.path.join(VENDOR, rel)
        if not os.path.exists(d):
            missing.append(rel)
        elif not filecmp.cmp(s, d, shallow=False):
            drift.append(rel)
    # extra vendored files (not in the allowed set) are also drift
    extra = []
    allowed = set(MODULES)
    if os.path.isdir(VENDOR):
        for r, _, fns in os.walk(VENDOR):
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(r, fn), VENDOR).replace(os.sep, "/")
                if rel not in allowed:
                    extra.append(rel)
    if missing or drift or extra:
        if missing:
            print("[check] MISSING: %s" % ", ".join(missing))
        if drift:
            print("[check] DRIFT: %s" % ", ".join(drift))
        if extra:
            print("[check] EXTRA (not allowed): %s" % ", ".join(extra))
        print("[check] FAIL — run: python scripts/sync_anywhere_vendor.py")
        return 1
    print("[check] OK — %d vendored modules byte-identical to source" % len(MODULES))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify vendor is byte-identical (CI gate)")
    a = ap.parse_args(argv)
    return do_check() if a.check else do_sync()


if __name__ == "__main__":
    sys.exit(main())
