# -*- coding: utf-8 -*-
"""binggu_session_close.py — 세션 마무리 트리거 (backward-compatible thin wrapper).

strangler phase3: 순수 정본(detect_session_close · build_close_summary · render_close_md ·
register_close_phrase · process · _RoLedger · _home · _load_close_phrases · _build_preview ·
_build_governance · _ledger_path · _fmt_rate · _selftest)은 binggupack.review.session_close 로
byte-identical 이관됐고, 이 파일은 공개 심볼 동일한 thin wrapper 다. read-only·저장 0 불변은
1바이트도 변하지 않았다. 기존 호출처(import binggu_session_close · scripts/ 직접 실행)는 그대로
동작한다.

미이관 bare-name lazy(binggu_capture_persist)·shim 경유 sibling(binggu_hit_stats·binggu_recall
— migrated)은 정본 모듈이 scripts/ 를 sys.path 에 얹어 해소한다(이 wrapper 도 동일하게 얹는다 —
selftest 의 capture 버퍼 흐름이 HERE(scripts) 경유라 HERE 유지 필수).

CLI: python scripts/binggu_session_close.py [--selftest] | (stdin JSON 신호 처리)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.review.session_close import *  # noqa: E402,F401,F403
from binggupack.review.session_close import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    _home,
    _load_close_phrases,
    register_close_phrase,
    detect_session_close,
    _build_preview,
    _build_outcome_candidates,
    _RoLedger,
    _ledger_path,
    _build_governance,
    build_close_summary,
    render_close_md,
    _fmt_rate,
    process,
    _selftest,
)


if __name__ == "__main__":
    import json
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # 비-selftest 직접 실행: stdin JSON 신호 처리(저장 0 · stdout=rendered or 침묵)
    try:
        raw = sys.stdin.read()
        sig = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        sys.exit(0)
    out = process(sig, cwd=(sig.get("cwd") if isinstance(sig, dict) else None))
    if out.get("rendered"):
        print(out["rendered"])
    sys.exit(0)
