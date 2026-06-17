# -*- coding: utf-8 -*-
"""BingguPack P1 "3층 구조" 기반 — 안전벨트(헌법) + 설정값 + 가치관 로더.

빙구팩 정체성 = 빈 뼈대 프레임워크. 어떤 사용자가 깔아도 자기 설정/가치관을 꽂는다.
owner 31노드·owner 경로 하드코딩 0 — 기본값 + 사용자별 설정파일(없으면 기본값).

3층 분리(owner 확정):
  1. 🔒 안전벨트(코드 고정·전 사용자 공통, 헌법): SAFETY_BELT 상수 + 헬퍼.
     다른 모듈이 import 해 강제한다. 설정/가치관으로도 못 바꾼다(불변).
       - AI 는 추천만 · 확정은 사람(actor=human)
       - 근거 없는 직감 메모도 검열·자동폐기 금지(보존)
       - AI 자동 가치관 판정 금지
       - 가치관 코드 동결 금지(빙구팩은 경로/로더만, 내용은 각 사용자 것)
  2. ⚙️ 설정값(기본값 제공·사용자별 조정): challenge_threshold · 랭킹 가중치 · 외부소스목록.
     <binggu_home>/binggu_config.json 에서 로드, 없으면 DEFAULT_CONFIG. 코드 하드코딩 고정 아님.
  3. 👤 가치관 로더: user_ontology(또는 사용자 지정 파일)를 "읽는 자리"만 제공.
     내용은 각 사용자 것. 빙구팩은 경로/로더만. 없어도 graceful(예외 0).

경로는 전부 binggu_home() 파생 — BINGGU_HOME opt-in + OS별 홈 폴백을 공짜로 상속.
운영 ledger.sqlite 미접촉(설정파일은 별도 sibling). write 는 설정파일/가치관 경로만.
"""
import json
import os
import warnings
from pathlib import Path

try:  # 패키지/스크립트 양쪽 import 호환(기존 모듈과 동일 관용)
    from binggu_capture_persist import binggu_home
except ImportError:  # pragma: no cover
    import binggu_platform as _plat

    def binggu_home(home=None):
        if home:
            return Path(home)
        return Path(_plat.binggu_home())


# ============================================================
# 1층 🔒 안전벨트 (헌법 · 코드 고정 · 전 사용자 공통 · 불변)
#    설정/가치관 어떤 값으로도 못 바꾼다. 다른 모듈이 import 해 강제.
# ============================================================
SAFETY_BELT = {
    # AI 는 추천만 — 영구화·확정은 사람만(actor 화이트리스트 = human 단일).
    "confirm_actor": "human",
    # 근거 없는 직감/짧은 메모도 검열하거나 자동폐기하지 않는다(보존). discard 는 사람 손에만.
    "preserve_unsupported_notes": True,
    # AI 가 가치관(옳다/그르다)을 자동 판정하지 않는다 — 가치관 적용은 추천·신호까지.
    "no_auto_value_judgment": True,
    # 가치관은 코드에 동결하지 않는다 — 빙구팩은 경로/로더만, 내용은 각 사용자 것.
    "no_frozen_values": True,
}


def confirm_actor():
    """영구화/확정을 통과시키는 단 하나의 actor(=human). allowlist 단일 원천."""
    return SAFETY_BELT["confirm_actor"]


def is_confirm_actor(actor):
    """actor 가 사람(확정 권한)인가. allowlist(== human)만 True — auto/reader/agent/누락 전부 False."""
    return actor == SAFETY_BELT["confirm_actor"]


def assert_human(actor):
    """확정 게이트 — 사람이 아니면 PermissionError(fail-closed). 다른 모듈이 import 해 강제.
    영구금지: actor denylist 우회 취약점 회피 → allowlist(== human)만 통과."""
    if not is_confirm_actor(actor):
        raise PermissionError(
            "AI 는 추천만 — 영구화·확정은 사람(actor='%s')만 가능. 받은 actor=%r"
            % (SAFETY_BELT["confirm_actor"], actor))
    return True


def preserves_unsupported_notes():
    """근거 없는 직감 메모도 보존(자동폐기·검열 금지)하는가. 항상 True(헌법)."""
    return SAFETY_BELT["preserve_unsupported_notes"]


def auto_value_judgment_allowed():
    """AI 자동 가치관 판정 허용 여부. 항상 False(헌법)."""
    return not SAFETY_BELT["no_auto_value_judgment"]


# ============================================================
# 2층 ⚙️ 설정값 (기본값 제공 · 사용자별 조정 · 코드 하드코딩 고정 아님)
#    <binggu_home>/binggu_config.json 로드, 없으면 DEFAULT_CONFIG.
# ============================================================
DEFAULT_CONFIG = {
    # 도전(challenge) 노드를 N회 누적되면 철학 재검토 신호로 본다.
    "challenge_threshold": 3,
    # 후보 랭킹 가중치 — 합 정규화는 소비처에서. 사용자별 조정 가능.
    "ranking_weights": {"freshness": 1.0, "relevance": 1.0, "utility": 1.0},
    # 외부 소스 목록 — 기본 빈 [](각 사용자가 자기 소스를 등록). owner 하드코딩 0.
    "external_sources": [],
    # 가치관 파일 경로 override(None 이면 기본 위치 = <home>/user_ontology.{yaml,md,txt}).
    "ontology_path": None,
    # 회상/반문(L5 preflight · L6 반문 엔진) 설정 — 과잉반문 방지·사용자 조정.
    #   risk_mid_score : 이 점수 이상이면 "중간"(짧게 경고). 미만이면 "낮음"(조용히 참고).
    #   risk_high_score: 이 점수 이상이면 "높음"(반문 후 진행 = needs_question).
    #   preflight_max  : preflight 가 끌어올릴 관련 판단 최대 개수(3~7 권장).
    #   recall_limit   : why_search 기본 결과 개수.
    # 위험도 점수 = subtype 가중(버그패턴>교훈) × 관련성(term-frequency 정규화) — 0~1 정규.
    #   semantic_recall_enabled: 의미(bge-m3 cos) 회상 보강 스위치(기본 False — 어휘 회상만).
    #     실제 활성은 이 값 AND binggu_canonical_semantic.enabled()(opt-in) 둘 다 켜져야 한다.
    "recall_config": {
        "risk_mid_score": 0.30,
        "risk_high_score": 0.55,
        "preflight_max": 5,
        "recall_limit": 5,
        "semantic_recall_enabled": False,
    },
}

_RECALL_KEYS = ("risk_mid_score", "risk_high_score", "preflight_max", "recall_limit",
                "semantic_recall_enabled")

_RANKING_KEYS = ("freshness", "relevance", "utility")


def config_path(home=None):
    """설정파일 경로 = <binggu_home>/binggu_config.json (capture_scope.json sibling)."""
    return binggu_home(home) / "binggu_config.json"


def _coerce_ranking(raw):
    """랭킹 가중치 강제 정규화 — 키 누락은 기본값, 비-숫자는 기본값으로 폴백(방어).

    음수 방어: 가중치는 가중합의 계수다(점수 = Σ w·축). 음수면 해당 축의 부호가
    뒤집혀(예: freshness=-2.0 → 오래된 노드가 상위) 정렬 의미가 깨진다. 따라서
    음수는 0 으로 클램프(max(0, w))하고 경고. 전부 0(또는 음수→0)이 되면 모든 노드
    점수가 동일(무의미 평탄 정렬)이라 기본값으로 폴백(0 나눗셈/무의미 정렬 방지).
    """
    base = dict(DEFAULT_CONFIG["ranking_weights"])
    if isinstance(raw, dict):
        for k in _RANKING_KEYS:
            v = raw.get(k, base[k])
            try:
                w = float(v)
            except (TypeError, ValueError):
                continue  # 기본값 유지
            if w < 0:
                warnings.warn(
                    "ranking_weight '%s'=%r < 0 → 0 으로 클램프(음수 가중치는 정렬 부호를 "
                    "뒤집어 무의미). 가중치는 0 이상이어야 합니다." % (k, w),
                    RuntimeWarning, stacklevel=2)
                w = 0.0
            base[k] = w
    if all(base[k] == 0.0 for k in _RANKING_KEYS):
        warnings.warn(
            "ranking_weights 가 전부 0 → 점수가 평탄해져 정렬이 무의미. 기본값으로 폴백.",
            RuntimeWarning, stacklevel=2)
        return dict(DEFAULT_CONFIG["ranking_weights"])
    return base


def load_user_config(home=None):
    """설정파일 있으면 DEFAULT_CONFIG 위에 병합, 없거나 깨졌으면 DEFAULT_CONFIG 사본.

    CaptureScope._scope() 의 방어적 로드 스타일 그대로 — 어떤 파일 손상도 예외 0.
    """
    cfg = {
        "challenge_threshold": DEFAULT_CONFIG["challenge_threshold"],
        "ranking_weights": dict(DEFAULT_CONFIG["ranking_weights"]),
        "external_sources": list(DEFAULT_CONFIG["external_sources"]),
        "ontology_path": DEFAULT_CONFIG["ontology_path"],
        "recall_config": dict(DEFAULT_CONFIG["recall_config"]),
    }
    p = config_path(home)
    if not p.exists():
        return cfg
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(d, dict):
        return cfg
    # challenge_threshold — 양의 정수만 채택, 아니면 기본값
    try:
        ct = int(d.get("challenge_threshold", cfg["challenge_threshold"]))
        if ct >= 1:
            cfg["challenge_threshold"] = ct
    except (TypeError, ValueError):
        pass
    cfg["ranking_weights"] = _coerce_ranking(d.get("ranking_weights"))
    src = d.get("external_sources", cfg["external_sources"])
    if isinstance(src, list):
        cfg["external_sources"] = [str(s) for s in src]
    op = d.get("ontology_path", cfg["ontology_path"])
    cfg["ontology_path"] = str(op) if op else None
    cfg["recall_config"] = _coerce_recall(d.get("recall_config"))
    return cfg


def _coerce_recall(raw):
    """회상/반문 설정 강제 정규화 — 키 누락/비-숫자는 기본값. 임계 단조성 방어.

    risk_high_score < risk_mid_score 이면(역전) 기본값으로 폴백(중간이 높음보다 빡센
    무의미 상태 방지). preflight_max/recall_limit 는 양의 정수로 클램프(최소 1).
    """
    base = dict(DEFAULT_CONFIG["recall_config"])
    if isinstance(raw, dict):
        for k in ("risk_mid_score", "risk_high_score"):
            try:
                v = float(raw.get(k, base[k]))
            except (TypeError, ValueError):
                continue
            base[k] = min(1.0, max(0.0, v))  # [0,1] 클램프
        for k in ("preflight_max", "recall_limit"):
            try:
                v = int(raw.get(k, base[k]))
            except (TypeError, ValueError):
                continue
            base[k] = max(1, v)
        # semantic_recall_enabled — 불리언만 채택(잘못된 타입은 기본 False 유지).
        sv = raw.get("semantic_recall_enabled", base["semantic_recall_enabled"])
        if isinstance(sv, bool):
            base["semantic_recall_enabled"] = sv
    if base["risk_high_score"] < base["risk_mid_score"]:
        warnings.warn(
            "recall_config: risk_high_score(%r) < risk_mid_score(%r) 역전 → 기본값 폴백."
            % (base["risk_high_score"], base["risk_mid_score"]),
            RuntimeWarning, stacklevel=2)
        return dict(DEFAULT_CONFIG["recall_config"])
    return base


def recall_config(home=None):
    """편의 접근자 — 회상/반문 설정 dict(risk_mid_score/risk_high_score/preflight_max/recall_limit)."""
    return load_user_config(home)["recall_config"]


def save_user_config(cfg, home=None):
    """설정파일 기록 — init_profile 의 scope write 스타일(ensure_ascii=False, indent=2)."""
    p = config_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def challenge_threshold(home=None):
    """편의 접근자 — 도전 재검토 임계 N(기본 3)."""
    return load_user_config(home)["challenge_threshold"]


def ranking_weights(home=None):
    """편의 접근자 — 랭킹 가중치 dict(freshness/relevance/utility)."""
    return load_user_config(home)["ranking_weights"]


def external_sources(home=None):
    """편의 접근자 — 외부 소스 목록(기본 빈 [])."""
    return load_user_config(home)["external_sources"]


# ============================================================
# 3층 👤 가치관 로더 ("읽는 자리"만 제공 · 내용은 각 사용자 것 · 없어도 graceful)
# ============================================================
_ONTOLOGY_DEFAULT_NAMES = ("user_ontology.yaml", "user_ontology.md", "user_ontology.txt")


def user_ontology_path(home=None):
    """가치관 파일 경로 결정. config 의 ontology_path override 우선, 없으면 기본 위치 탐색.

    기본 위치 = <binggu_home>/user_ontology.{yaml,md,txt} (먼저 존재하는 것).
    owner 는 ~/.claude/memory/user_ontology.md 를 config.ontology_path 로 꽂은 예 —
    빙구팩 코드엔 어떤 owner 경로도 하드코딩하지 않는다(설정값으로만 지정).
    반환: Path 또는 None(어느 것도 없으면).
    """
    cfg = load_user_config(home)
    if cfg["ontology_path"]:
        return Path(cfg["ontology_path"])
    h = binggu_home(home)
    for name in _ONTOLOGY_DEFAULT_NAMES:
        cand = h / name
        if cand.exists():
            return cand
    return h / _ONTOLOGY_DEFAULT_NAMES[0]  # 기본 위치(미존재여도 '읽는 자리'로 반환)


def load_user_ontology(home=None):
    """가치관 파일을 read-only 로 읽어 원문 반환. 없거나 못 읽으면 None(절대 예외 0).

    빙구팩은 경로/로더만 — 파싱/판정은 소비처(challenge/ranking)에서. 가치관 코드 동결 0.
    """
    p = user_ontology_path(home)
    try:
        if p and p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        return None
    return None


def ontology_status(home=None):
    """가치관 꽂힘 상태 read-only 요약(doctor/안내용)."""
    p = user_ontology_path(home)
    return {
        "path": str(p) if p else None,
        "present": bool(p and p.exists()),
        "via_config_override": bool(load_user_config(home)["ontology_path"]),
    }


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
