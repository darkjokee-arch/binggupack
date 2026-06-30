# -*- coding: utf-8 -*-
"""LocalBinggu incoming graph loader v0.7 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 정본 로직은 binggupack.pack.incoming_loader 로 이관됐고, 이 파일은
공개 심볼(VALID_SPACE/VALID_NTYPE/VALID_KIND/VALID_REL/_jl/load_incoming)이 byte-identical 한
thin wrapper 다. 기존 호출처(import localbinggu_incoming_loader as v07loader → v07loader.load_incoming
등 bare-name import)는 그대로 동작한다. 순수 read-only(운영 store write 0).

(storage facade __init__ 의 무거운 import 로 인한 순환을 피해 pack/ 에 정본을 둔다.)

CLI: python scripts/localbinggu_incoming_loader.py --incoming-dir <dir> [--seed-evidence <file>]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.pack.incoming_loader import *  # noqa: E402,F401,F403
from binggupack.pack.incoming_loader import (  # noqa: E402,F401  (전체 명시 re-export)
    VALID_SPACE,
    VALID_NTYPE,
    VALID_KIND,
    VALID_REL,
    _jl,
    load_incoming,
)


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("--incoming-dir", required=True)
    ap.add_argument("--seed-evidence", default=None,
                    help="seed evidence_index.jsonl (기존 evidence_id 참조 허용용)")
    args = ap.parse_args()
    known = set()
    seed_ev = args.seed_evidence or str(Path(__file__).resolve().parent.parent / "reingest_pack_draft" / "evidence_index.jsonl")
    if Path(seed_ev).exists():
        known = {json.loads(l)["evidence_id"] for l in Path(seed_ev).read_text(encoding="utf-8").splitlines() if l.strip()}
    r = load_incoming(args.incoming_dir, known_evidence_ids=known)
    out = {k: v for k, v in r.items() if k not in ("accepted_nodes", "accepted_edges", "evidence")}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if r["schema_valid"] else 1)
