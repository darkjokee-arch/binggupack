#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu doctor — 공개 전 필수 검사 단일 진입점 (backward-compatible thin shim).

strangler Phase2: 정본(run_doctor · oi1_stamp_divergence · oi2_evidence_closure ·
oi3_exit_label_count · _run_oi_checks · _run_selftest · _real_tree_scan · _default_ledger ·
main 및 내부 상수/헬퍼)은 binggupack.pack.doctor 로 이관됐고, 이 파일은 공개 심볼이 동일한
thin shim 이다. 기존 CLI(python scripts/openbinggu_doctor.py --selftest | --tree | --ledger |
--oi-selftest)는 그대로 동작한다.

정본 모듈은 _HERE(=<repo>/scripts)·_ROOT(=<repo>) 를 패키지 위치에서 역산해 하위 selftest
subprocess 경로·_OP_STORE·_real_tree_scan 을 원래와 동일하게 유지한다(경로/판정/write0 무변).
subprocess 진입점 호환을 위해 scripts/ 와 repo root 를 sys.path 에 얹어 패키지 import 를 보장한다.

CLI:
  python scripts/openbinggu_doctor.py --selftest
  python scripts/openbinggu_doctor.py --tree <ROOT> [--public]
  python scripts/openbinggu_doctor.py --ledger <PATH>
  python scripts/openbinggu_doctor.py --oi-selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.doctor import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    run_doctor,
    oi1_stamp_divergence,
    oi2_evidence_closure,
    oi3_exit_label_count,
    _run_oi_checks,
    _run_selftest,
    _real_tree_scan,
    _default_ledger,
    _secret_scan_stub,
    _store_snapshot,
    _print_oi,
    _build_temp_ledger,
    _oi_selftest,
    main,
    _HERE,
    _ROOT,
    _OP_STORE,
    _CHECKS,
    _NODE_ID_RE,
    _EVC_ID_RE,
    _GATE_RE,
)

__all__ = (
    'run_doctor',
    'oi1_stamp_divergence',
    'oi2_evidence_closure',
    'oi3_exit_label_count',
    '_run_oi_checks',
    '_run_selftest',
    '_real_tree_scan',
    '_default_ledger',
    '_secret_scan_stub',
    '_store_snapshot',
    '_print_oi',
    '_build_temp_ledger',
    '_oi_selftest',
    'main',
    '_HERE',
    '_ROOT',
    '_OP_STORE',
    '_CHECKS',
    '_NODE_ID_RE',
    '_EVC_ID_RE',
    '_GATE_RE',
)


if __name__ == "__main__":
    main()
