# -*- coding: utf-8 -*-
"""OpenBinggu G0 — label_kind 매핑/분류 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 매핑/분류 정본 로직은 binggupack.classifier.label_kind_map 으로
이관됐고, 이 파일은 공개 심볼(KIND_KO/KO2EN/EN2KO/KIND_TO_SPACE_NTYPE/_RULES/FALLBACK/
classify_label_kind/to_a0_node_type)이 byte-identical 한 thin wrapper 다. 기존 호출처
(from openbinggu_label_kind_map import classify_label_kind, KO2EN 등 bare-name import)는
그대로 동작한다. 순수 함수(write 0·LLM 0·멱등).

selftest 의 정합검사(a0_node_dryrun.LABEL_KINDS / merge_adapter.NODE_MAP 비교)는 scripts/
sys.path 의존이므로 이 wrapper 에 잔류한다.

CLI: python scripts/openbinggu_label_kind_map.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.classifier.label_kind_map import (  # noqa: E402,F401  (전체 명시 re-export)
    KIND_KO,
    KO2EN,
    EN2KO,
    KIND_TO_SPACE_NTYPE,
    _RULES,
    FALLBACK,
    classify_label_kind,
    to_a0_node_type,
)

__all__ = (
    'KIND_KO',
    'KO2EN',
    'EN2KO',
    'KIND_TO_SPACE_NTYPE',
    '_RULES',
    'FALLBACK',
    'classify_label_kind',
    'to_a0_node_type',
)


# ---------------- selftest (동적 정합검사는 scripts/ sys.path 의존이라 wrapper 잔류) ----------------

def _selftest():
    cases = [
        # (문장, 기대 kind, 기대 rule prefix)
        ("테스트 로그에 통과 결과가 기록되어 있다.", "증거", "ev_record"),
        ("공고문 캡처된 화면이 첨부되어 있다는 기록이다.", "증거", "ev_record"),
        ("이 문서는 배포 절차를 정의한다.", "문서", "doc_ref"),
        ("본 설계서는 staging 스키마를 규정한다.", "문서", "doc_ref"),
        ("redaction 이란 민감정보를 제거하는 절차이다.", "개념", "concept_def"),
        ("낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다.", "개념", "concept_def"),
        ("현재 테스트 스위트는 전부 통과한 상태이다.", "상태", "state_now"),
        ("백필 작업이 진행 중이다.", "상태", "state_now"),
        ("이 입찰은 마진이 낮아 보류한다.", "판단", "judgment_verdict"),
        ("이 방식은 위험하므로 채택하지 않는 것이 낫다.", "판단", "judgment_verdict"),
        ("릴리스 전에는 빌드와 테스트를 모두 통과해야 한다.", "판단", "judgment_verdict"),
        # fallback (애매 — 현행과 동일하게 판단)
        ("변경 scripts/foo.py (+3/-1): import json", "판단", "fallback_judgment"),
        ("", "판단", "fallback_judgment"),
    ]
    all_ok = True
    print("=" * 72)
    print("OpenBinggu G0 — label_kind 매핑/분류 selftest")
    print("=" * 72)
    for s, exp_kind, exp_rule in cases:
        kind, rule = classify_label_kind(s)
        ok = (kind == exp_kind and rule == exp_rule)
        all_ok = all_ok and ok
        print("  [%s] %-14s rule=%-18s %s" % ("OK" if ok else "FAIL", kind, rule, s[:34]))

    # 매핑 정합: merge_adapter NODE_MAP 과 일치 + 양방향 무손실 + A0 LABEL_KINDS 일치
    # merge_adapter 는 비공개 작업트리 전용 — clean clone(public repo)엔 없으므로 부재 시 skip.
    import importlib
    a0 = importlib.import_module("openbinggu_a0_node_dryrun")
    try:
        ma = importlib.import_module("localbinggu_merge_adapter")
        map_match = all(KIND_TO_SPACE_NTYPE[k] == ma.NODE_MAP[k] for k in KIND_KO)
        map_label = "merge_adapter_NODE_MAP_일치"
    except ImportError:
        map_match = True
        map_label = "merge_adapter_NODE_MAP_일치 (모듈 부재 — public clone, skip)"
    roundtrip = all(EN2KO[KO2EN[k]] == k for k in KIND_KO)
    a0_match = set(KO2EN.values()) == a0.LABEL_KINDS
    for name, ok in [(map_label, map_match),
                     ("한영_왕복_무손실", roundtrip),
                     ("A0_LABEL_KINDS_일치", a0_match)]:
        all_ok = all_ok and ok
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))

    # 멱등(2회 동일)
    idem = all(classify_label_kind(s) == classify_label_kind(s) for s, _, _ in cases)
    all_ok = all_ok and idem
    print("  [%s] idempotent_2회_동일" % ("OK" if idem else "FAIL"))

    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.path.insert(0, HERE)
        _selftest()
    else:
        print("usage: openbinggu_label_kind_map.py [--selftest]")
        sys.exit(2)
