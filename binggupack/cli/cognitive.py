"""CLI surfaces for the read-only cognitive adapters."""

from __future__ import annotations

import json
import os
import sys

from binggupack.cognitive.catchup import collect_catchup, render_catchup
from binggupack.cognitive.workloop import run_cognitive_workloop


def cmd_catchup(a):
    repo = getattr(a, "repo", None) or os.getcwd()
    query = getattr(a, "query", None) or os.path.basename(os.path.abspath(repo)) or "current work"
    ledger = os.path.abspath(a.ledger) if getattr(a, "ledger", None) else None
    result = collect_catchup(
        repo, query=query, ledger_path=ledger,
        max_chars=max(500, int(getattr(a, "max_chars", 8000) or 8000)),
        test_state=getattr(a, "test_state", None),
    )
    if getattr(a, "json", False):
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(render_catchup(result), end="")
    return 0


def cmd_workloop(a):
    path = getattr(a, "input", None)
    try:
        if path == "-":
            spec = json.load(sys.stdin)
        else:
            with open(path, encoding="utf-8") as handle:
                spec = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        print("BLOCK: invalid workloop input: %s" % exc)
        return 1
    if not isinstance(spec, dict):
        print("BLOCK: workloop input must be a JSON object")
        return 1
    result = run_cognitive_workloop(spec)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0
