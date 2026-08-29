"""CLI surfaces for the read-only cognitive adapters."""

from __future__ import annotations

import json
import os

from binggupack.cognitive.catchup import collect_catchup, render_catchup


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
