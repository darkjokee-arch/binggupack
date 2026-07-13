# -*- coding: utf-8 -*-
"""session_close — 세션 마무리 트리거(감지 신호 처리 + preview/거버넌스 요약) · read-only.

세션 마무리 자연어(사용자마다 다름)를 **모델이 의미로 감지**한 신호를 받아, 저장 preview +
거버넌스 정리(대비 기록·적중률)를 빌드해 사람에게 표시한다. 빙구팩은 "감지 신호 처리·요약
렌더링"만 하고 **저장 0**(SAVE는 사람이 직접 타이핑·기존 save_gate). 헌법 자동저장 0 유지.

설계 출처: CLAUDE.md §9 Layer1 의도분류 정합(키워드 매칭 X / 모델 의미감지). 본 모듈은
스스로 키워드를 매칭하지 않는다 — 호출측(Claude 모델 행동 규약)이 의미로 판정한 close 신호를
넘기고, 모듈은 그 신호 + opt-in 사용자 등록 표현만 처리한다(결정론·LLM 0).

[흐름 — 단방향 read-only]
  detect_session_close(signal, home)            → {is_close, source, confidence}
    → build_close_summary(home, cwd, ledger_path) → {preview, governance, ...}
    → render_close_md(summary)                     → str(사람이 읽는 마크다운, 저장 0)
  사람이 표를 보고 `SAVE n` 타이핑 → 저장 실행은 본 모듈 범위 밖(기존 save_gate). 여기서 멈춘다.

★감지 = 모델 의미감지(Layer1):
  ① is_close 의 1차 원천 = 호출측이 준 의미감지 결과(signal['model_detected_close']).
  ② 보조 = 사용자별 등록 표현(close_phrases.json, opt-in) — 정규화 후 유한폐포 정확 membership
     (부분 키워드 매칭 아님). 정규화(_norm)=NFKC+casefold+isalnum 필터로 공백·구두점·전각·이모지
     변형을 흡수하고, 등록 표현 × 등록 접미(suffixes)의 유한 후보집합에 대한 == 검사라 좌/우
     부분매칭이 구조적으로 불가능하다(T5 오발동 차단 증명 가능). "정확 일치" 계약 불변.
  ③ 둘 다 없으면 is_close=False(graceful, 표시 0). 빙구팩이 자유문자열 키워드로 추정 0.

불변(헌법):
  - 저장 0 — SAVE 실행·헌법 자동저장 0. 출력 = preview 텍스트 + 거버넌스 요약뿐.
  - candidate-only — preview 는 capture 버퍼 candidate(active/confirmed/ledger write 0).
  - PII 제외 — preview/거버넌스에 원문 전문·시크릿 재출력 0(버퍼/통계 산출물만).
  - 거버넌스 자산(박제/CLAUDE.md/정책파일) write 0(read-only). ledger 는 mode=ro 로만.
  - 적중률은 '신호'(상관≠인과) — 자동결정/자동교체 0. signal_only 표지 그대로 노출.
  - AI 추천만 — 자동결정 0. graceful(빈 버퍼/무 ledger/OFF → 침묵, 에러 0).
  - stdlib only(hashlib/json·외부 바이너리 0).

strangler phase3: 순수 정본(detect_session_close · build_close_summary · render_close_md ·
register_close_phrase · process · _RoLedger · _home · _load_close_phrases · _build_preview ·
_build_governance · _ledger_path · _fmt_rate · _selftest)이 이 모듈로 byte-identical 이관됐다.
binggupack.config 는 정식 패키지 import(migrated)로 재배선됐고, 미이관 bare-name lazy
(binggu_capture_persist)·shim 경유 sibling(binggu_hit_stats·binggu_recall — migrated)은 scripts/
를 sys.path 에 얹어 해소한다. 진입점 scripts/binggu_session_close.py 는 공개 심볼 동일한 thin
wrapper(부트스트랩 + 재-export + selftest/stdin CLI 위임).
"""
from __future__ import annotations

import json
import os
import sys
import unicodedata
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))          # <repo>/binggupack/review
ROOT = os.path.dirname(os.path.dirname(HERE))              # <repo> (binggupack.config import 경로)
_SCRIPTS = os.path.join(ROOT, "scripts")                   # 미이관 sibling(capture_persist bare-name lazy)
for _p in (ROOT, _SCRIPTS):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def _home(home=None):
    """빙구팩 홈. 테스트는 home 인자 / BINGGU_HOME 으로 운영 경로 미접촉."""
    if home:
        return Path(home)
    env = os.environ.get("BINGGU_HOME")
    if env:
        return Path(env)
    return Path.home() / ".binggupack"


# ---------------- 1. 마무리 감지 (모델 의미감지 + opt-in 사용자 등록) ----------------

def _load_close_phrases(home=None):
    """사용자별 세션 마무리 표현 등록(opt-in). close_phrases.json 부재 → 빈 리스트(graceful).
    형식: {"phrases": ["오늘 여기까지", "마무리하자", ...]}. 정확 일치(부분 키워드 매칭 아님).

    로드는 binggupack.config 단일 로더 경유(부재/손상 방어·기본값 병합 재사용). 항상 fresh
    (use_cache=False)로 기존 매 호출 파일 재읽기 동작을 그대로 보존한다."""
    try:
        from binggupack.config import load_config
        data = load_config("close_phrases", home, use_cache=False)
        phrases = data.get("phrases", []) if isinstance(data, dict) else []
        return [str(x).strip() for x in phrases if str(x).strip()]
    except Exception:
        return []


def _norm(s):
    """세션 마무리 매칭용 정규화 — NFKC(전각→반각·호환자모) + casefold(라틴 대소문자) +
    isalnum 필터(공백·구두점·기호·이모지·제어문자 제거). 한글/영숫자는 alnum=True 로 보존되고,
    부정어(안/못/말/않/마)는 '글자'라 살아남는다 → 구두점 제거가 의미반전을 만들지 못한다.
    두 문자열의 _norm 이 같으면 공백·구두점·전각 변형만 다른 동일 표현이다."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    return "".join(c for c in s.casefold() if c.isalnum())


def _load_close_suffixes(home=None, max_norm_len=8):
    """세션 마무리 표현 뒤에 붙는 짧은 종결/조사 접미(opt-in). close_phrases.json 의 "suffixes" 키.
    등록 표현과 유한폐포 합성(phrase+suffix)에만 쓰이며, 다음 접미는 무시한다(부분매칭 재현 봉쇄 — G-c):
      - 공백 포함(= 2어절 이상): 접미는 '단일 종결어'만. "그리고 한가지 더" 같은 다어절은 배제
        (norm 후 길이만으로는 못 막는다 — "그리고한가지더"=7자 < 8. 어절 제약이 정본 방어).
      - _norm 길이 max_norm_len(기본 8) 초과: 단일 어절이라도 과도하게 긴 접미 보조 차단.
    부재→[](graceful). ★부정계(안/못/말/마/않)는 접미로 등록 금지(의미반전 오발동) — onboard seed 주석에 명기."""
    try:
        from binggupack.config import load_config
        data = load_config("close_phrases", home, use_cache=False)
        sfx = data.get("suffixes", []) if isinstance(data, dict) else []
        out = []
        for x in sfx:
            s = str(x).strip()
            if not s or any(c.isspace() for c in s):
                continue  # 빈/다어절(공백 포함) 접미 배제 — 단일 종결어만
            if 0 < len(_norm(s)) <= max_norm_len:
                out.append(s)
        return out
    except Exception:
        return []


def _load_close_config(home=None):
    """close_phrases.json 전체({phrases, suffixes}) 로드 — writer 가 상대 키를 보존하도록.
    부재/손상 → {"phrases": [], "suffixes": []}(config 로더 기본값)."""
    try:
        from binggupack.config import load_config
        data = load_config("close_phrases", home, use_cache=False)
        if not isinstance(data, dict):
            return {"phrases": [], "suffixes": []}
        return data
    except Exception:
        return {"phrases": [], "suffixes": []}


def register_close_phrase(phrase, home=None):
    """사용자 세션 마무리 표현 등록(opt-in 옵션). close_phrases.json 에 append(중복 무시).
    빙구팩 거버넌스 자산이 아닌 사용자 설정 파일만 write — 박제/CLAUDE.md/ledger 미접촉.
    ★suffixes 키 보존(phrase 등록이 접미 목록을 날리지 않도록 전체 config 로드 후 write).
    반환 {registered, phrases}. 빈/공백 phrase → registered=False(graceful)."""
    phrase = str(phrase or "").strip()
    home_dir = _home(home)
    data = _load_close_config(home)
    existing = [str(x).strip() for x in data.get("phrases", []) if str(x).strip()]
    suffixes = data.get("suffixes", [])
    if not phrase:
        return {"registered": False, "reason": "empty_phrase", "phrases": existing}
    if phrase in existing:
        return {"registered": False, "reason": "duplicate", "phrases": existing}
    try:
        home_dir.mkdir(parents=True, exist_ok=True)
        phrases = existing + [phrase]
        (home_dir / "close_phrases.json").write_text(
            json.dumps({"phrases": phrases, "suffixes": suffixes}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return {"registered": True, "phrases": phrases}
    except Exception as e:
        return {"registered": False, "reason": "write_error:%s" % type(e).__name__,
                "phrases": existing}


def register_close_suffix(suffix, home=None):
    """세션 마무리 접미 등록(opt-in). close_phrases.json "suffixes" 에 append(phrases 보존).
    등록 표현 × 접미 유한폐포로 종결/조사 변형을 흡수한다. 단일 종결어만(공백 포함=다어절 거부)·
    빈/구두점-only 거부. ★부정계(안/못/말/마/않)는 등록 금지 — 의미반전 오발동(호출측·onboard 책임).
    반환 {registered, suffixes}. 잘못된 접미 → registered=False(graceful)."""
    suffix = str(suffix or "").strip()
    home_dir = _home(home)
    data = _load_close_config(home)
    phrases = data.get("phrases", [])
    existing = [str(x).strip() for x in data.get("suffixes", []) if str(x).strip()]
    if not suffix or any(c.isspace() for c in suffix) or not _norm(suffix):
        return {"registered": False, "reason": "invalid_suffix", "suffixes": existing}
    if suffix in existing:
        return {"registered": False, "reason": "duplicate", "suffixes": existing}
    try:
        home_dir.mkdir(parents=True, exist_ok=True)
        suffixes = existing + [suffix]
        (home_dir / "close_phrases.json").write_text(
            json.dumps({"phrases": phrases, "suffixes": suffixes}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return {"registered": True, "suffixes": suffixes}
    except Exception as e:
        return {"registered": False, "reason": "write_error:%s" % type(e).__name__,
                "suffixes": existing}


def detect_session_close(signal, home=None):
    """세션 마무리 발화를 의미로 감지한 신호를 처리(키워드 매칭 X · Layer1 정합).

    signal: dict 또는 str.
      dict 권장: {
        "model_detected_close": bool,   # 호출측(Claude)이 의미로 판정한 결과(1차 원천)
        "utterance": str,               # 사용자 발화(opt-in 등록 표현 정확 일치 보조용)
        "confidence": float?,           # 호출측 신뢰도(선택)
      }
      str: 발화 텍스트만 — model_detected_close 없이 opt-in 등록 표현 정확 일치만 검사.

    반환 {is_close, source, confidence}.
      source: 'model'(의미감지) | 'registered_phrase'(사용자 등록) | None.
    빙구팩은 자유문자열 키워드로 추정 0 — 모델 신호/등록 표현 외엔 is_close=False(graceful)."""
    if isinstance(signal, str):
        signal = {"utterance": signal}
    if not isinstance(signal, dict):
        return {"is_close": False, "source": None, "confidence": 0.0}

    # ① 1차: 모델 의미감지 결과(Layer1) — 자유문자열 매칭 아님.
    if bool(signal.get("model_detected_close")):
        try:
            conf = float(signal.get("confidence", 1.0))
        except Exception:
            conf = 1.0
        return {"is_close": True, "source": "model", "confidence": max(0.0, min(1.0, conf))}

    # ② 보조: 사용자 등록 표현 정규화 후 유한폐포 정확 membership(부분 키워드 매칭 금지).
    #   후보집합 = {norm(ph)} ∪ {norm(ph)+norm(sfx)} — 유한·열거가능이라 좌/우 부분매칭 구조적 불가.
    #   가드: G-a(순 구두점 등록 skip)·G-b(순 구두점 발화 skip)·G-c(접미 8자 초과 무시=_load)·G-d(1회 후미 합성만).
    utter = str(signal.get("utterance", "")).strip()
    nu = _norm(utter)
    if nu:  # G-b
        suffixes = _load_close_suffixes(home)
        for ph in _load_close_phrases(home):
            np = _norm(ph)
            if not np:
                continue  # G-a: 등록 표현이 순 구두점 → skip("!!" 오발동 방지)
            if nu == np:
                return {"is_close": True, "source": "registered_phrase", "confidence": 1.0}
            for sfx in suffixes:  # G-d: 등록 접미 1회·후미 합성만(재귀·prefix 0)
                if nu == np + _norm(sfx):
                    return {"is_close": True, "source": "registered_phrase", "confidence": 1.0}

    # ③ 그 외 → 표시 0(빙구팩 추정 0).
    return {"is_close": False, "source": None, "confidence": 0.0}


# ---------------- 2. 저장 preview 빌드 (candidate-only · read-only) ----------------

def _build_preview(home=None, session_id=None):
    """capture 버퍼 candidate 목록(저장 0 · candidate-only). session_id 지정 시 그 세션 발화만
    (세션 경계 — 이전 세션 잔존 배제·2026-07-10). 버퍼 모듈 부재/빈 버퍼 → graceful."""
    try:
        from binggu_capture_persist import PersistentCaptureBuffer
    except Exception:
        return {"available": False, "count": 0, "items": [],
                "note": "capture 버퍼 모듈 미사용(preview 생략)"}
    try:
        pv = PersistentCaptureBuffer(home=_home(home)).render_preview(session_id=session_id)
        return {"available": True, "count": pv.get("count", 0),
                "items": pv.get("items", []),
                "bulk_vetoed": pv.get("bulk_vetoed", 0),
                "note": pv.get("note", "owner 승인 전 candidate (active 아님)")}
    except Exception:
        return {"available": False, "count": 0, "items": [],
                "note": "preview 빌드 실패(graceful 생략)"}


# ---------------- 2b. 당일 owner 지적 후보 (learn 큐 · read-only · 사람 확정만) ----------------

def _build_outcome_candidates(home=None, today=None):
    """당일 owner 지적(learn_outcome_queue 소비 대기) 후보 — read-only(소비 0 · 큐 write 0).

    ★B안(사람 확정): 세션 마무리 preview 에 "오늘 이런 지적이 있었는데 적중한 것 골라주세요"만
    표시하고, 확정(hit_events 적재)은 owner 가 learn-consume --confirm "CONSUME <번호>" 로만.
    자동 확정 0 — 소비 경로(actor=human 게이트·dup_decision 차단)는 기존 learn_consume 재사용.
    ★번호(qi)는 learn_consume.load_pending 인덱스 그대로 — dry-run/CONSUME 번호와 동일 보장.
    today='YYYY-MM-DD'(UTC · 큐 ts 와 동일 기준) 주입식 — 미지정 시 현재 UTC 날짜.
    모듈/큐 부재 → graceful(available=False · 에러 0)."""
    try:
        from binggupack.pack import learn_consume as LC
    except Exception:
        return {"available": False, "count": 0, "items": [],
                "note": "learn_consume 모듈 미사용(후보 표시 생략)"}
    try:
        qpath = LC.queue_path(home)
        items = []
        for qi, entry in LC.load_pending_today(qpath, today=today):
            fb = (entry.get("evidence") or {}).get("feedback") or ""
            items.append({"qi": qi, "outcome": entry.get("outcome"),
                          "feedback": fb[:60], "ts": entry.get("ts")})
        return {"available": True, "count": len(items), "items": items,
                "note": "확정은 사람만 — 자동 적재 0 · 번호는 learn-consume dry-run 과 동일"}
    except Exception:
        return {"available": False, "count": 0, "items": [],
                "note": "당일 후보 로드 실패(graceful 생략)"}


# ---------------- 3. 거버넌스 요약 빌드 (대비 기록·적중률 · read-only · 신호 전용) ----------------

class _RoLedger:
    """hit_stats 가 기대하는 .con(read-only sqlite) 어댑터. write 0(mode=ro). 미존재 → con=None."""

    def __init__(self, ledger_path):
        self.con = None
        try:
            import sqlite3
            if ledger_path and os.path.exists(ledger_path):
                self.con = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
        except Exception:
            self.con = None

    def close(self):
        try:
            if self.con is not None:
                self.con.close()
        except Exception:
            pass


def _ledger_path(home=None):
    return str(_home(home) / "ledger.sqlite")


def _build_governance(home=None, cwd=None, ledger_path=None):
    """거버넌스 정리 = 적중률(대비 기록 양쪽 owner/ai · 도메인 분리) 신호 요약.
    hit_stats.both_sides / proposal_priority_signal 재사용(read-only · signal_only 표지 유지).
    ledger 부재/hit_events 0/모듈 부재 → graceful(available=False · 에러 0). 자동결정/자동교체 0."""
    lp = ledger_path or _ledger_path(home)
    ro = _RoLedger(lp)
    if ro.con is None:
        ro.close()
        return {"available": False, "note": "ledger 없음(거버넌스 요약 생략 · 신규 사용자)"}
    try:
        import binggu_hit_stats as HS
    except Exception:
        ro.close()
        return {"available": False, "note": "hit_stats 모듈 미사용(거버넌스 요약 생략)"}
    try:
        # hit_events 테이블 부재 → graceful(빈 통계 취급).
        try:
            ro.con.execute("SELECT 1 FROM hit_events LIMIT 1")
        except Exception:
            ro.close()
            return {"available": False, "note": "적중률 기록 없음(대비 선택 누적 전)"}
        dom = None
        try:
            from binggu_recall import _domain_from_cwd
            dom = _domain_from_cwd(cwd) if cwd else None
        except Exception:
            dom = None
        # both_sides/proposal_priority_signal(db,...) 는 db.con 을 쓴다 → con 보유 어댑터를 넘긴다.
        overall = HS.both_sides(ro)
        priority = HS.proposal_priority_signal(ro, domain=dom) if dom else \
            HS.proposal_priority_signal(ro)
        return {
            "available": True,
            "overall": overall,        # owner/ai 적중률(전역) — signal_only 표지 포함
            "priority": priority,      # 제안 정렬 신호(도메인 분리) — 상관≠인과
            "domain": dom,
            "note": "적중률 = 제안 우선순위 '신호'(상관≠인과) · 규칙 자동교체 근거 아님",
        }
    except Exception:
        return {"available": False, "note": "거버넌스 요약 빌드 실패(graceful 생략)"}
    finally:
        ro.close()


def build_close_summary(home=None, cwd=None, ledger_path=None, session_id=None, today=None):
    """세션 마무리 표시용 요약 빌드(저장 0 · read-only). preview + 당일 지적 후보 + 거버넌스 묶음.
    session_id 지정 시 preview 를 그 세션 발화로 한정(세션 경계). today('YYYY-MM-DD' UTC)는
    당일 지적 후보 필터 주입용(테스트 결정성 · 미지정 시 현재 UTC 날짜).
    반환 {preview, outcome_candidates, governance, save_action}."""
    return {
        "preview": _build_preview(home, session_id=session_id),
        "outcome_candidates": _build_outcome_candidates(home, today=today),
        "governance": _build_governance(home, cwd, ledger_path),
        "save_action": {
            "auto_save": False,
            "how": "저장은 사람이 직접 — preview 를 보고 `SAVE n`(정확한 번호) 타이핑 시 기존 save_gate 로만 진행. 빙구팩 자동저장 0.",
        },
    }


# ---------------- 4. 렌더링 (결정적 마크다운 · 저장 0) ----------------

def render_close_md(summary):
    """세션 마무리 요약 → 사람이 읽는 마크다운(결정적 · LLM 0 · 저장 0).
    저장 preview(candidate-only) + 거버넌스 정리(적중률 신호) + '저장은 사람' 안내."""
    lines = ["## 세션 마무리 — 저장 preview + 거버넌스 정리 (저장 0 · 사람이 SAVE)"]

    pv = summary.get("preview", {}) or {}
    lines.append("")
    lines.append("### 1) 저장 preview (candidate · active 아님)")
    if pv.get("available") and pv.get("count"):
        for it in pv.get("items", []):
            lines.append("- " + str(it.get("label", it.get("text", ""))))
        lines.append("> %s" % pv.get("note", "owner 승인 전 candidate"))
    else:
        lines.append("- (수집된 candidate 없음 — 표시할 preview 0)")
    bv = pv.get("bulk_vetoed", 0)
    if bv:
        lines.append("- ⚠️ 긴 발화 %d건 자동 제외(붙여넣기·대화 덩어리·AI 응답문 — 화자축 오염 방지). "
                     "진짜 저장하려면 그 내용에 `이거 저장해` 명시." % bv)

    gv = summary.get("governance", {}) or {}
    lines.append("")
    lines.append("### 2) 거버넌스 정리 — 대비 기록·적중률 (신호 · 상관≠인과)")
    if gv.get("available"):
        ov = gv.get("overall", {}) or {}
        owner = (ov.get("owner") or {})
        ai = (ov.get("ai") or {})
        lines.append("- owner 직감 적중률: %s" % _fmt_rate(owner))
        lines.append("- ai 반박/수용 적중률: %s" % _fmt_rate(ai))
        if gv.get("domain"):
            lines.append("- 도메인: %s" % gv["domain"])
        lines.append("> %s" % gv.get("note", "적중률 = 신호(자동교체 근거 아님)"))
    else:
        lines.append("- %s" % gv.get("note", "거버넌스 요약 없음"))

    sa = summary.get("save_action", {}) or {}
    lines.append("")
    lines.append("### 3) 저장")
    lines.append("- 자동저장: **0** (헌법 — candidate-only · 사람 승인 게이트)")
    lines.append("- %s" % sa.get("how", "저장은 사람이 직접 `SAVE n` 타이핑."))

    # ★B안(사람 확정): 당일 owner 지적 후보 — 0건이면 섹션 자체 생략(노이즈 금지).
    oc = summary.get("outcome_candidates", {}) or {}
    if oc.get("available") and oc.get("count"):
        lines.append("")
        lines.append("### 4) 당일 owner 지적 후보 — 적중한 것 골라주세요 (자동 확정 0)")
        for it in oc.get("items", []):
            tag = "적중(hit)" if it.get("outcome") == "hit" else "빗나감(miss)"
            lines.append("- [%s] %s · 발화: %s" % (it.get("qi"), tag, it.get("feedback") or ""))
        lines.append('- 확정: `binggu learn-consume --confirm "CONSUME <번호>"` '
                     "(사람 확정만 · 번호는 dry-run 과 동일)")
        lines.append("> %s" % oc.get("note", "확정은 사람만 — 자동 적재 0"))

    return "\n".join(lines)


def _fmt_rate(d):
    """적중률 dict → 사람이 읽는 표현. 표본 미달(enough=False) → '표본 부족(보류)'."""
    if not isinstance(d, dict):
        return "기록 없음"
    if not d.get("enough"):
        n = d.get("n", 0)
        return "표본 부족(n=%s · 보류)" % n
    rate = d.get("rate")
    n = d.get("n", 0)
    if rate is None:
        return "표본 부족(보류)"
    return "%.0f%% (n=%s · 시간감쇠 가중)" % (float(rate) * 100, n)


# ---------------- 5. 통합 진입점 (감지 → 요약 → 렌더, 저장 0) ----------------

def process(signal, home=None, cwd=None, ledger_path=None):
    """세션 마무리 신호 1건 처리(저장 0). 감지→(close면)요약 빌드+렌더.
    반환 {is_close, source, confidence, summary?, rendered?}. 저장·자동결정 0."""
    det = detect_session_close(signal, home)
    if not det["is_close"]:
        return {**det, "summary": None, "rendered": None}
    sid = signal.get("session_id") if isinstance(signal, dict) else None
    summary = build_close_summary(home, cwd, ledger_path, session_id=sid)
    return {**det, "summary": summary, "rendered": render_close_md(summary)}


# ---------------- 셀프테스트 (temp home 전용 · 저장 0 · 운영 미접촉) ----------------

def _selftest():
    import shutil
    import sqlite3
    import tempfile
    import time

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))

    tmp = Path(tempfile.mkdtemp(prefix="bgp_session_close_"))
    try:
        home = tmp / ".binggupack"
        home.mkdir(parents=True)

        # --- 감지 ---
        # T1 모델 의미감지 → is_close · source=model
        d = detect_session_close({"model_detected_close": True, "confidence": 0.9}, home=home)
        check(d["is_close"] and d["source"] == "model" and abs(d["confidence"] - 0.9) < 1e-6,
              "T1 모델 의미감지 → is_close · source=model · conf 보존")

        # T2 비-마무리 발화 → is_close False(빙구팩 키워드 추정 0)
        d = detect_session_close({"utterance": "이 버그 고쳐줘", "model_detected_close": False},
                                 home=home)
        check(not d["is_close"] and d["source"] is None,
              "T2 비-마무리 발화 → is_close False(추정 0)")

        # T3 str 입력(모델신호 없음) + 등록 표현 없음 → is_close False
        d = detect_session_close("오늘 여기까지 하자", home=home)
        check(not d["is_close"], "T3 등록 전 표현(str) → is_close False(키워드 추정 0)")

        # T4 사용자 등록 표현(opt-in) 정확 일치 → is_close · source=registered_phrase
        reg = register_close_phrase("오늘 여기까지 하자", home=home)
        check(reg["registered"] and "오늘 여기까지 하자" in reg["phrases"],
              "T4a 마무리 표현 등록(opt-in)")
        d = detect_session_close("오늘 여기까지 하자", home=home)
        check(d["is_close"] and d["source"] == "registered_phrase",
              "T4b 등록 표현 정확 일치 → is_close · source=registered_phrase")

        # T5 등록 표현 부분 포함(정확 일치 아님) → is_close False(부분 키워드 매칭 금지)
        d = detect_session_close("오늘 여기까지 하자 그리고 한가지 더", home=home)
        check(not d["is_close"], "T5 부분 포함(정확 일치 아님) → is_close False")

        # T6 중복 등록 무시 · 빈 표현 거부
        check(not register_close_phrase("오늘 여기까지 하자", home=home)["registered"],
              "T6a 중복 등록 무시")
        check(not register_close_phrase("   ", home=home)["registered"],
              "T6b 빈/공백 표현 거부")

        # T6c register_close_suffix + phrase↔suffix 상호 보존(writer 가 상대 키를 안 날림)
        register_close_suffix("해줘", home=home)
        register_close_phrase("빙구팩 마무리", home=home)  # suffix 등록 후 phrase 등록
        _cfg = _load_close_config(home)
        check("해줘" in _cfg.get("suffixes", []) and "빙구팩 마무리" in _cfg.get("phrases", []),
              "T6c suffix+phrase 등록 상호 보존(상대 키 안 날림)")
        # T6d 잘못된 접미 거부(다어절=공백 포함 · 구두점-only)
        check(not register_close_suffix("세션 마무리 후", home=home)["registered"]
              and not register_close_suffix("...", home=home)["registered"],
              "T6d 다어절/구두점-only 접미 거부(graceful)")

        # --- preview / 거버넌스 (빈 상태 graceful) ---
        # T7 빈 버퍼 + 무 ledger → 요약 graceful(에러 0 · 저장 0)
        s = build_close_summary(home=home)
        check(s["preview"]["count"] == 0 and not s["governance"]["available"]
              and s["save_action"]["auto_save"] is False,
              "T7 빈 상태 → preview 0 · 거버넌스 미가용 · auto_save False(graceful)")

        # T8 렌더 결정성 + 저장 토큰 0
        md1 = render_close_md(s)
        md2 = render_close_md(s)
        check(md1 == md2 and "자동저장: **0**" in md1 and "SAVE n" in md1,
              "T8 렌더 결정성 + '자동저장 0'·'SAVE n'(사람) 명시")

        # T9 capture 버퍼 candidate 적재 후 preview 표시(candidate-only)
        buf_ok = True
        try:
            (home / "capture_enabled").write_text("1", encoding="utf-8")
            (home / "capture_scope.json").write_text(json.dumps({
                "allowed_cwd_prefixes": ["C:/Users/fixture-user/binggupack"],
                "denied_cwd_substrings": ["example-project"],
            }, ensure_ascii=False), encoding="utf-8")
            from binggu_capture_persist import PersistentCaptureBuffer
            b = PersistentCaptureBuffer(home=home)
            b.feed("B안으로 결정한다", "C:/Users/fixture-user/binggupack")
            b.feed("이 패턴은 항상 버그를 유발한다는 교훈", "C:/Users/fixture-user/binggupack")
            pv = _build_preview(home=home)
            buf_ok = pv["available"] and pv["count"] >= 1
            # candidate-only: ledger 미생성(저장 0)
            buf_ok = buf_ok and not (home / "ledger.sqlite").exists()
        except Exception:
            buf_ok = False  # 버퍼 모듈 흐름 변경 시 graceful 실패 표시
        check(buf_ok, "T9 candidate 적재 후 preview 표시 · ledger 미생성(candidate-only · 저장 0)")

        # T10 거버넌스 요약(hit_events 있는 ledger) — signal_only 표지 + 저장 0
        gov_ok = True
        try:
            lp = home / "gov_ledger.sqlite"
            con = sqlite3.connect(str(lp))
            con.execute("CREATE TABLE hit_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "node_id TEXT, speaker TEXT, kind TEXT, outcome TEXT, subtype TEXT,"
                        "ts TEXT, domain TEXT, context_hash TEXT, decision_id TEXT)")
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for i in range(6):
                con.execute("INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts) "
                            "VALUES(?,?,?,?,?,?)",
                            ("n%d" % i, "owner", "judgment", "hit", "교훈", now))
            con.commit()
            mtime_before = lp.stat().st_mtime_ns
            gv = _build_governance(home=home, ledger_path=str(lp))
            mtime_after = lp.stat().st_mtime_ns
            gov_ok = gv["available"] and ("overall" in gv) and (mtime_before == mtime_after)
            md = render_close_md({"preview": _build_preview(home),
                                  "governance": gv, "save_action": {"auto_save": False}})
            gov_ok = gov_ok and "적중률" in md and "상관≠인과" in md
        except Exception:
            gov_ok = False
        check(gov_ok, "T10 거버넌스 요약(적중률 신호) · ledger write 0(mtime 불변) · 상관≠인과 표기")

        # T11 process 통합: 비-close → summary None / close → summary+rendered
        p_no = process({"model_detected_close": False, "utterance": "고쳐줘"}, home=home)
        p_yes = process({"model_detected_close": True}, home=home, cwd="C:/Users/fixture-user/binggupack")
        check(p_no["summary"] is None and p_yes["summary"] is not None
              and isinstance(p_yes["rendered"], str),
              "T11 process: 비-close→summary None · close→summary+rendered")

        # T12 거버넌스 자산 write 0: 박제/CLAUDE.md/정책파일 미생성 + 잘못된 입력 graceful
        check(detect_session_close(None, home=home)["is_close"] is False
              and detect_session_close(12345, home=home)["is_close"] is False,
              "T12a 잘못된 입력(None/int) → graceful False")
        gov_assets_clean = not (home / "CLAUDE.md").exists() and not (home / "박제").exists() \
            and not (home / "binggu_policy.json").exists()
        check(gov_assets_clean, "T12b 거버넌스 자산(박제/CLAUDE.md/정책) write 0")

        # ── T13~T17 정규화 유한폐포(N3): 공백·구두점·전각·접미 변형 흡수 + T5류 오발동 구조적 차단 ──
        cp_path = home / "close_phrases.json"
        cp_path.write_text(json.dumps(
            {"phrases": ["오늘 여기까지 하자", "세션 마무리"],
             "suffixes": ["요", "그리고 한가지 더"]}, ensure_ascii=False), encoding="utf-8")
        # T13 정발동: 구두점/전각/공백/붙여쓰기 변형 → True (norm 동치)
        for utt, note in (("오늘 여기까지 하자!", "구두점"),
                          ("  오늘 여기까지 하자...  ", "구두점+공백"),
                          ("오늘여기까지하자", "공백 제거"),
                          ("오늘 여기까지 하자！", "전각 구두점(NFKC)")):
            check(detect_session_close(utt, home=home)["is_close"], "T13 정발동(%s)→True" % note)
        # T14 접미 유한폐포: "세션 마무리"+"요" → True (구성성). 긴 접미는 무시(길이 가드).
        check(detect_session_close("세션 마무리요", home=home)["is_close"],
              "T14 접미 폐포(세션 마무리+요)→True")
        # T15 오발동 차단(전부 False): 부정계·선행어·긴 접미(길이가드로 T5 여전 False)
        for utt, note in (("세션 마무리 안해", "부정계(부정어 보존)"),
                          ("세션 마무리 말고", "부정계(말고)"),
                          ("이제 세션 마무리", "선행 자유텍스트=좌측 부분매칭 금지"),
                          ("오늘 여기까지 하자 그리고 한가지 더", "T5 원본(긴 접미 8자↑ 무시)")):
            check(not detect_session_close(utt, home=home)["is_close"],
                  "T15 오발동 차단(%s)→False" % note)
        # T16 빈 norm 가드: 순 구두점 등록 + 순 구두점 발화 → False
        cp_path.write_text(json.dumps({"phrases": ["..."], "suffixes": []}, ensure_ascii=False),
                           encoding="utf-8")
        check(not detect_session_close("!!", home=home)["is_close"],
              "T16 빈 norm 가드(순 구두점)→False")
        # T17 casefold(비한국어 신규 사용자): "Wrap Up!" ≡ 등록 "wrap up" → True
        cp_path.write_text(json.dumps({"phrases": ["wrap up"], "suffixes": []}, ensure_ascii=False),
                           encoding="utf-8")
        check(detect_session_close("Wrap Up!", home=home)["is_close"],
              "T17 casefold(Wrap Up!≡wrap up)→True")

        # ── T18~T20 ★B안(사람 확정): 당일 owner 지적 후보 — read-only 표시·번호 보존·0건 생략 ──
        #    today 는 주입식(wall-clock 의존 0). 큐 = learn_consume 규칙(home/state/learn_outcome_queue.jsonl).
        st_dir = home / "state"
        st_dir.mkdir(parents=True, exist_ok=True)
        qp = st_dir / "learn_outcome_queue.jsonl"
        q_entries = [
            {"ts": "2026-07-12T22:00:00Z", "outcome": "hit", "queries": [],
             "evidence": {"feedback": "전일 지적 항목"}, "consumed": False},
            {"ts": "2026-07-13T01:00:00Z", "outcome": "miss", "queries": [],
             "evidence": {"feedback": "너도 제대로 안 읽었는데"}, "consumed": False},
            {"ts": "2026-07-13T02:00:00Z", "outcome": "hit", "queries": [],
             "evidence": {"feedback": "이미 소비된 당일 항목"}, "consumed": True},
        ]
        qp.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in q_entries) + "\n",
                      encoding="utf-8")
        q_mtime = qp.stat().st_mtime_ns
        s18 = build_close_summary(home=home, today="2026-07-13")
        oc = s18.get("outcome_candidates", {}) or {}
        md18 = render_close_md(s18)
        check(oc.get("available") and oc.get("count") == 1
              and oc["items"][0]["qi"] == 1 and oc["items"][0]["outcome"] == "miss"
              and qp.stat().st_mtime_ns == q_mtime,
              "T18 당일 후보만(전일·consumed 제외)·qi=learn-consume 소비 번호 보존·큐 write 0")
        check("### 4) 당일 owner 지적 후보" in md18
              and "[1] 빗나감(miss)" in md18 and "너도 제대로 안 읽었는데" in md18
              and 'learn-consume --confirm "CONSUME' in md18
              and "전일 지적 항목" not in md18 and "이미 소비된 당일 항목" not in md18,
              "T19 섹션4 렌더: 번호+outcome+발화 발췌+CONSUME 안내 · 전일/consumed 미표시")
        # T20 후보 0건(타 일자) → 섹션 생략(노이즈 금지) · summary 키는 존재(count 0)
        s20 = build_close_summary(home=home, today="2026-07-14")
        md20 = render_close_md(s20)
        check((s20.get("outcome_candidates", {}) or {}).get("count") == 0
              and "당일 owner 지적 후보" not in md20,
              "T20 당일 후보 0건 → 섹션 생략(노이즈 0)")

        print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
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
