# -*- coding: utf-8 -*-
"""binggu_p1_config — P1 안전벨트(헌법) + 설정값 + 가치관 로더 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 순수 정본(🔒 SAFETY_BELT 안전벨트 상수 + 헬퍼 confirm_actor/
is_confirm_actor/assert_human/preserves_unsupported_notes/auto_value_judgment_allowed
+ v2 자율성 티어 auto_fact_observation_allowed/auto_signal_ranking_direct_forbidden/t1_negative_gate_enabled,
⚙️ DEFAULT_CONFIG + _coerce_*/load_user_config/접근자, 👤 가치관 로더)은
binggupack.safety.p1_config 로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin
wrapper 다. 기존 호출처(import binggu_p1_config — recall/contrast/realpack/a0_node/p1_ranking
등)는 그대로 동작한다.

🔒 안전벨트(헌법)는 1바이트도 변하지 않았다 — AI 는 추천만, 영구화·확정은 사람(actor=human)
allowlist 단일 원천. assert_human 은 fail-closed(사람 아니면 PermissionError). 설정/가치관 어떤
값으로도 못 바꾼다.

home resolver(binggu_capture_persist.binggu_home · 폴백 binggu_platform)와 temp-home 셀프테스트는
scripts/ sys.path 의존이라 이 wrapper 에 잔류한다(원본 import 소스 그대로 유지). 정본 모듈의 동일
import 도 진입점 scripts/ sys.path 로 해소된다.

CLI: python scripts/binggu_p1_config.py
"""
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.safety.p1_config import *  # noqa: E402,F401,F403
from binggupack.safety.p1_config import (  # noqa: E402,F401  (전체 명시 re-export)
    SAFETY_BELT,
    confirm_actor,
    is_confirm_actor,
    assert_human,
    preserves_unsupported_notes,
    auto_value_judgment_allowed,
    auto_fact_observation_allowed,
    auto_signal_ranking_direct_forbidden,
    t1_negative_gate_enabled,
    DEFAULT_CONFIG,
    _RECALL_KEYS,
    _RANKING_KEYS,
    config_path,
    _coerce_ranking,
    load_user_config,
    _coerce_contrast,
    _coerce_recall,
    recall_config,
    contrast_config,
    save_user_config,
    challenge_threshold,
    ranking_weights,
    external_sources,
    _ONTOLOGY_DEFAULT_NAMES,
    user_ontology_path,
    load_user_ontology,
    ontology_status,
)

try:  # 패키지/스크립트 양쪽 import 호환(기존 모듈과 동일 관용)
    from binggu_capture_persist import binggu_home
except ImportError:  # pragma: no cover
    import binggu_platform as _plat

    def binggu_home(home=None):
        if home:
            return Path(home)
        return Path(_plat.binggu_home())


# ---------------- 셀프테스트 (temp home 전용, 운영 ~/.binggupack 미접촉) ----------------
def _selftest():
    import shutil
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
        return cond

    tmp = Path(tempfile.mkdtemp(prefix="bgp_p1_config_"))
    try:
        home = tmp / ".binggupack"
        home.mkdir(parents=True)
        # 운영 ledger 미접촉 검증용 더미
        ledger = home / "ledger.sqlite"
        ledger.write_bytes(b"LEDGER-SENTINEL")
        ledger_mtime0 = ledger.stat().st_mtime_ns

        # --- 1층 안전벨트 불변 ---
        check(confirm_actor() == "human", "T1a confirm_actor == human")
        check(is_confirm_actor("human") and not is_confirm_actor("auto")
              and not is_confirm_actor("reader") and not is_confirm_actor("agent")
              and not is_confirm_actor("") and not is_confirm_actor(None),
              "T1b allowlist(==human)만 통과 · auto/reader/agent/빈/None 차단")
        try:
            assert_human("auto")
            check(False, "T1c assert_human(auto) → PermissionError")
        except PermissionError:
            check(True, "T1c assert_human(auto) → PermissionError(fail-closed)")
        check(assert_human("human") is True, "T1d assert_human(human) → True")
        check(preserves_unsupported_notes() is True, "T1e 무근거 메모 보존(자동폐기 금지)")
        check(auto_value_judgment_allowed() is False, "T1f AI 자동 가치관 판정 금지")
        # 안전벨트 상수는 설정으로 못 바꾼다 — 별도 변수, 설정 로드와 무관
        save_user_config({"confirm_actor": "auto"}, home=home)  # 설정에 넣어도
        check(confirm_actor() == "human", "T1g 설정에 confirm_actor 넣어도 안전벨트 불변")
        config_path(home).unlink()  # 정리

        # --- 1층 v2 자율성 티어(T0/T1) + T0↔T2 격리 불변식(2026-07-20 owner GO) ---
        check(auto_fact_observation_allowed() is True,
              "T1h T0 효용사실 자동관측 허용(v2)")
        check(auto_signal_ranking_direct_forbidden() is True,
              "T1i T0 자동신호 랭킹 직접반영 금지(인기편향 방어)")
        check(t1_negative_gate_enabled() is True,
              "T1j T1 국소판단 자율+사후부정 게이트(v2)")
        # ★격리 핵심: T0/T1 이 열려도 T2 확정 게이트는 불변 — 자율이 저장확정을 못 연다
        check(confirm_actor() == "human" and not is_confirm_actor("auto"),
              "T1k [격리] T0/T1 열려도 confirm_actor==human 불변")
        try:
            assert_human("auto")
            check(False, "T1l [격리] 자율 티어에도 assert_human(auto) → PermissionError")
        except PermissionError:
            check(True, "T1l [격리] 자율 티어에도 assert_human(auto) → PermissionError(fail-closed)")
        # ★가치/사실 축 분리: 효용 '사실' 관측은 열려도 '가치' 자동판정은 여전히 금지
        check(auto_fact_observation_allowed() is True and auto_value_judgment_allowed() is False,
              "T1m [축분리] 사실관측 허용 ∧ 가치판정 금지 동시 성립")
        # 안전벨트 신규 키도 설정으로 못 끈다(불변)
        save_user_config({"auto_fact_observation": False, "t1_negative_gate": False}, home=home)
        check(auto_fact_observation_allowed() is True and t1_negative_gate_enabled() is True,
              "T1n 설정에 자율 키 False 넣어도 안전벨트 불변")
        config_path(home).unlink()

        # --- 2층 설정값: 없을 때 기본값 ---
        check(not config_path(home).exists(), "T2a 설정파일 없음(초기)")
        c0 = load_user_config(home)
        check(c0["challenge_threshold"] == 3, "T2b 기본 challenge_threshold == 3")
        check(c0["ranking_weights"] == {"freshness": 1.0, "relevance": 1.0, "utility": 1.0},
              "T2c 기본 ranking_weights")
        check(c0["external_sources"] == [] and c0["ontology_path"] is None,
              "T2d 기본 external_sources [] · ontology_path None")
        check(c0["recall_config"]["risk_high_score"] == 0.55
              and c0["recall_config"]["preflight_max"] == 5
              and c0["recall_config"]["semantic_recall_enabled"] is False,
              "T2d2 기본 recall_config(risk_high 0.55 · preflight_max 5 · semantic OFF)")
        # semantic_recall_enabled override(불리언만 채택)
        save_user_config({"recall_config": {"semantic_recall_enabled": True}}, home=home)
        check(load_user_config(home)["recall_config"]["semantic_recall_enabled"] is True,
              "T2d2b semantic_recall_enabled True override 반영")
        save_user_config({"recall_config": {"semantic_recall_enabled": "yes"}}, home=home)
        check(load_user_config(home)["recall_config"]["semantic_recall_enabled"] is False,
              "T2d2c 잘못된 타입(문자열) → 기본 False 유지")
        config_path(home).unlink()
        # trace_enabled(Phase 2 opt-in) — 기본 False · bool override · 잘못된 타입 폴백
        check(load_user_config(home)["recall_config"]["trace_enabled"] is False,
              "T2d2d 기본 trace_enabled False(opt-in 미통과 → 기록 0)")
        save_user_config({"recall_config": {"trace_enabled": True}}, home=home)
        check(load_user_config(home)["recall_config"]["trace_enabled"] is True,
              "T2d2e trace_enabled True override 반영")
        save_user_config({"recall_config": {"trace_enabled": 1}}, home=home)
        check(load_user_config(home)["recall_config"]["trace_enabled"] is False,
              "T2d2f 잘못된 타입(int) → 기본 False 유지")
        config_path(home).unlink()
        # recall_config override + 역전 방어
        save_user_config({"recall_config": {"risk_mid_score": 0.4, "risk_high_score": 0.7,
                                            "preflight_max": 3, "recall_limit": 10}}, home=home)
        cr = load_user_config(home)["recall_config"]
        check(cr["risk_mid_score"] == 0.4 and cr["risk_high_score"] == 0.7
              and cr["preflight_max"] == 3 and cr["recall_limit"] == 10,
              "T2d3 recall_config override 반영")
        import warnings as _w0
        save_user_config({"recall_config": {"risk_mid_score": 0.8, "risk_high_score": 0.2}}, home=home)
        with _w0.catch_warnings(record=True) as wlog0:
            _w0.simplefilter("always")
            cr2 = load_user_config(home)["recall_config"]
        check(cr2 == DEFAULT_CONFIG["recall_config"]
              and any(issubclass(w.category, RuntimeWarning) for w in wlog0),
              "T2d4 임계 역전(high<mid) → 기본값 폴백 + 경고")
        config_path(home).unlink()
        check(challenge_threshold(home) == 3 and ranking_weights(home)["utility"] == 1.0,
              "T2e 편의 접근자 기본값")

        # --- 2층 contrast_config(대비 규약 1단) ---
        cc0 = load_user_config(home)["contrast_config"]
        check(cc0 == {"match_relevance_min": 0.5, "max_rows": 5},
              "T2e2 기본 contrast_config(match_relevance_min 0.5 · max_rows 5)")
        save_user_config({"contrast_config": {"match_relevance_min": 0.7, "max_rows": 3}}, home=home)
        cc1 = contrast_config(home)
        check(cc1["match_relevance_min"] == 0.7 and cc1["max_rows"] == 3,
              "T2e3 contrast_config override 반영")
        # 범위 밖/잘못된 타입 클램프·폴백
        save_user_config({"contrast_config": {"match_relevance_min": 5.0, "max_rows": 0}}, home=home)
        cc2 = contrast_config(home)
        check(cc2["match_relevance_min"] == 1.0 and cc2["max_rows"] == 1,
              "T2e4 contrast_config 클램프(rel>1→1 · max_rows<1→1)")
        save_user_config({"contrast_config": {"match_relevance_min": "x", "max_rows": "y"}}, home=home)
        cc3 = contrast_config(home)
        check(cc3 == {"match_relevance_min": 0.5, "max_rows": 5},
              "T2e5 잘못된 타입 → 기본값 유지(예외 0)")
        config_path(home).unlink()

        # --- 2층 설정값: 있을 때 override ---
        save_user_config({
            "challenge_threshold": 5,
            "ranking_weights": {"freshness": 2.0, "relevance": 0.5, "utility": 1.5},
            "external_sources": ["rss://example", "file:///notes"],
            "ontology_path": str(home / "my_values.md"),
        }, home=home)
        c1 = load_user_config(home)
        check(c1["challenge_threshold"] == 5, "T2f override challenge_threshold == 5")
        check(c1["ranking_weights"]["freshness"] == 2.0 and c1["ranking_weights"]["relevance"] == 0.5,
              "T2g override ranking_weights")
        check(c1["external_sources"] == ["rss://example", "file:///notes"],
              "T2h override external_sources")

        # --- 2층 방어적 로드: 깨진 파일 → 기본값 폴백(예외 0) ---
        config_path(home).write_text("{ this is not json", encoding="utf-8")
        c2 = load_user_config(home)
        check(c2["challenge_threshold"] == 3 and c2["ranking_weights"]["utility"] == 1.0,
              "T2i 깨진 설정파일 → 기본값 폴백(예외 0)")
        # 부분 손상 / 잘못된 타입 → 해당 키만 기본값
        config_path(home).write_text(json.dumps({
            "challenge_threshold": "NaN", "ranking_weights": {"freshness": "x"},
            "external_sources": "not-a-list",
        }, ensure_ascii=False), encoding="utf-8")
        c3 = load_user_config(home)
        check(c3["challenge_threshold"] == 3, "T2j 잘못된 threshold 타입 → 기본 3")
        check(c3["ranking_weights"]["freshness"] == 1.0, "T2k 잘못된 weight 타입 → 기본 1.0")
        check(c3["external_sources"] == [], "T2l 잘못된 external_sources 타입 → 기본 []")
        config_path(home).unlink()

        # --- 2층 음수/0 가중치 방어 (정렬 부호 반전 차단) ---
        import warnings as _warnings
        # 음수 가중치 → 0 으로 클램프, 양수는 보존(경고 발생)
        config_path(home).write_text(json.dumps({
            "ranking_weights": {"freshness": -2.0, "relevance": 0.5, "utility": 1.0},
        }, ensure_ascii=False), encoding="utf-8")
        with _warnings.catch_warnings(record=True) as wlog:
            _warnings.simplefilter("always")
            c_neg = load_user_config(home)
        check(c_neg["ranking_weights"] == {"freshness": 0.0, "relevance": 0.5, "utility": 1.0},
              "T2m 음수 가중치 → 0 클램프(양수는 보존)")
        check(any(issubclass(w.category, RuntimeWarning) for w in wlog),
              "T2n 음수 가중치 → RuntimeWarning 경고")
        # 전부 0(또는 음수→0) → 기본값 폴백(0 나눗셈/평탄 정렬 방지)
        config_path(home).write_text(json.dumps({
            "ranking_weights": {"freshness": 0.0, "relevance": -1.0, "utility": 0.0},
        }, ensure_ascii=False), encoding="utf-8")
        with _warnings.catch_warnings(record=True) as wlog2:
            _warnings.simplefilter("always")
            c_zero = load_user_config(home)
        check(c_zero["ranking_weights"] == {"freshness": 1.0, "relevance": 1.0, "utility": 1.0},
              "T2o 전부 0(음수→0 포함) → 기본값 폴백")
        check(any(issubclass(w.category, RuntimeWarning) for w in wlog2),
              "T2p 전부 0 폴백 → RuntimeWarning 경고")
        config_path(home).unlink()

        # --- 3층 가치관 로더: 파일 없어도 graceful ---
        check(load_user_ontology(home) is None, "T3a 가치관 파일 없음 → None(예외 0)")
        st0 = ontology_status(home)
        check(not st0["present"] and not st0["via_config_override"],
              "T3b 가치관 미존재 status")

        # 기본 위치(.yaml) 에 꽂으면 읽힘
        (home / "user_ontology.yaml").write_text("핵심가치:\n  - 자유\n", encoding="utf-8")
        txt = load_user_ontology(home)
        check(txt is not None and "자유" in txt, "T3c 기본 위치 가치관 파일 읽힘")
        check(ontology_status(home)["present"], "T3d 가치관 present True")

        # config override 경로 우선(owner 처럼 외부 경로 지정)
        ext = tmp / "external_values.md"
        ext.write_text("# 내 가치관\n수평적 관계\n", encoding="utf-8")
        save_user_config({"ontology_path": str(ext)}, home=home)
        txt2 = load_user_ontology(home)
        check(txt2 is not None and "수평적" in txt2, "T3e config override 가치관 경로 우선")
        check(ontology_status(home)["via_config_override"], "T3f override 플래그 True")
        # override 경로가 깨져도 graceful
        save_user_config({"ontology_path": str(tmp / "does_not_exist.md")}, home=home)
        check(load_user_ontology(home) is None, "T3g override 경로 부재 → None(예외 0)")
        config_path(home).unlink()
        (home / "user_ontology.yaml").unlink()

        # --- 운영 ledger 미접촉 ---
        check(ledger.exists() and ledger.stat().st_mtime_ns == ledger_mtime0,
              "T4 운영 ledger.sqlite 미접촉(write 0)")

        print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
