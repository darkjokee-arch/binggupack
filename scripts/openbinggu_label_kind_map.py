# -*- coding: utf-8 -*-
"""OpenBinggu G0 — label_kind 한영 매핑 단일 소스 + deterministic 5종 분류기.

배경 (4cli R3 지시 1): A0 validator=영문 5종(doc/evidence/concept/state/judgment),
loader=한글 5종(문서/증거/개념/상태/판단), 생산기=한글 "판단" 하드코딩 — 매핑표 없이
정렬하면 적재 전건 거부. 이 모듈이 유일한 매핑 정본이다 (다른 곳에 매핑 중복 정의 금지).

분류 규칙: 전부 정규식(LLM 0·멱등). 명확히 매칭될 때만 해당 종, 아니면 "판단" fallback
(현행 하드코딩과 동일값 → 회귀 0). rule_id로 분류 근거 추적.

CLI: python openbinggu_label_kind_map.py --selftest
"""
import re
import sys

# ---------------- 매핑 정본 ----------------

KIND_KO = ["문서", "증거", "개념", "상태", "판단"]
KO2EN = {"문서": "doc", "증거": "evidence", "개념": "concept", "상태": "state", "판단": "judgment"}
EN2KO = {v: k for k, v in KO2EN.items()}
# localbinggu_merge_adapter.NODE_MAP 과 동일 (정합 검증은 selftest에서)
KIND_TO_SPACE_NTYPE = {
    "문서": ("resource", "Document"), "증거": ("evidence", "Evidence"),
    "개념": ("concept", "Concept"), "상태": ("claim", "Claim"), "판단": ("claim", "Claim"),
}

# ---------------- deterministic 분류 규칙 (우선순위 순) ----------------
# 보수 원칙: 패턴이 명확할 때만 해당 종. 애매하면 판단 fallback (오분류보다 안전).

_RULES = [
    ("증거", "ev_record", re.compile(
        r"(기록되어 있|기록돼 있|로그에 .{0,12}(남|찍|있)|적혀 있|첨부되|캡처(했|된|되)|스크린샷|출력에 .{0,8}(나타|찍|보))")),
    ("문서", "doc_ref", re.compile(
        r"^(이|본|해당) ?(문서|보고서|설계서|가이드|튜토리얼|README|runbook)|(문서|보고서|설계서)(는|가) .{0,20}(정의|기술|설명|규정)한다")),
    ("개념", "concept_def", re.compile(
        r"((이|란|라)는 .{0,16}(절차|개념|규칙|원칙|방식|용어)(이다|다)\.?$|[을를] (말한다|의미한다|뜻한다)\.?$|(이란|란) )")),
    ("상태", "state_now", re.compile(
        r"((상태|중)(이다|다)\.?$|진행 중|가동 중|완료(된 상태|되어 있)|남아 ?있다|되어 ?있다\.?$|^현재 )")),
    ("판단", "judgment_verdict", re.compile(
        r"(해야 (한다|함)|하는 (것이|게) (낫|좋)|보류(한다|함)|진행(한다|함)|채택(한다|함)|기각(한다|함)|권고(한다|함)|금지(한다|함)|(이|가) (낫다|위험하다|안전하다)|않는 것이 (낫|좋))")),
]

FALLBACK = ("판단", "fallback_judgment")


def classify_label_kind(sentence):
    """핵심 문장 → (label_kind_ko, rule_id). 100% deterministic·멱등."""
    s = (sentence or "").strip()
    if not s:
        return FALLBACK
    for kind, rule_id, rx in _RULES:
        if rx.search(s):
            return kind, rule_id
    return FALLBACK


def to_a0_node_type(kind_ko):
    """A0 validator(영문 5종) 호출용 변환. 미지 값은 None (호출측 fail-closed)."""
    return KO2EN.get(kind_ko)


# ---------------- selftest ----------------

def _selftest():
    import json
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
        import os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        _selftest()
    else:
        print("usage: openbinggu_label_kind_map.py [--selftest]")
        sys.exit(2)
