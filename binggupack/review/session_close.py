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
    """[DEPRECATED 2026-07-16 — §supersede] 세션마무리 지적 후보 표시는 owner 지시로 폐지.
    판정은 `binggu verdict` 즉시 기록(hit_recording.record_verdict)이 정본. 본 함수는
    build_close_summary 에서 분리됐고 호출부 0 — 이력 보존용으로만 남김(신규 배선 금지).

    (구 계약) 당일 owner 지적(learn_outcome_queue 소비 대기) 후보 — read-only(소비 0 · 큐 write 0).

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
                          "stance": LC.stance_of(entry),
                          "ai_answer": (entry.get("ai_answer") or "")[:60],
                          "feedback": fb[:60], "ts": entry.get("ts")})
        return {"available": True, "count": len(items), "items": items,
                "note": "확정은 사람만 — 자동 적재 0 · 번호는 learn-consume dry-run 과 동일"}
    except Exception:
        return {"available": False, "count": 0, "items": [],
                "note": "당일 후보 로드 실패(graceful 생략)"}


# ---------------- 2c. 누적 미판정 회상 정리 후보 (recall_trace · 세션 무관 · read-only · owner 도장만) ----------------

def _build_recall_hits(home=None, ledger_path=None, now_ts=None, top_n=12, session_id=None):
    """누적 미판정 회상 정리 후보(세션 무관) — AI 자동선별 소수(값 확정 0) + owner 도장.

    ★v4(2026-07-25 owner 지적 해결): session_id 주어지면 '이번 세션 실제 회상'을 우선 표시(효용
    판정 대상 — 도움=`히트`/헛다리=`미스`). 이번 세션 회상이 없으면(구세션·session_id NULL) 아래 폴백:
    누적 미판정 중 청소 시급분(오래됨·미편입 우선). recall_traces.session_id(v4)가 이번 세션 필터를 가능케 함.
    (구 한계 2026-07-22: session_id 컬럼 부재로 '이번 세션'을 못 걸러 누적만 표시 → v4로 해소.)
    실데이터 전량이 preflight 자동회상이라 미판정이 수백 건 누적된다(2026-07-21 실측: pending
    425 = 100% preflight). owner 가 전부 도장할 수 없으므로 AI 가 **미스 후보(오래됨·그래프
    미편입 = recall_trace.list_miss_candidates)를 우선 정렬**해 top_n 만 제시한다. 선별=조회+신호
    (자동 OK), 값 확정(recall_outcomes used/ignored)은 owner actor=human 도장만(헌법 정합 · owner
    논증 2026-07-21: 후보 제시 ≠ 판정). 번호(N)는 save_review_snapshot 으로 선별목록에 고정 →
    owner '히트 N'/'미스 N' N-shift 안전. now_ts 미지정 → 미스 선별 생략(최신 미판정 top_n).

    ★축 구분(MF3): '히트'=회상 효용(recall_outcomes used, usefulness) — use_count(ledger)와 다른
    축. trace store 는 ledger.sqlite sibling(운영 ledger 불변). claim 은 read-only join(원문 0).
    반환 {available, count, total_pending, items:[{idx,category,rank,claim,flag,age_hours}], note}."""
    try:
        from binggupack.pack import recall_trace as RT
    except Exception:
        return {"available": False, "count": 0, "total_pending": 0, "items": [],
                "note": "recall_trace 모듈 미사용(회상 후보 생략)"}
    try:
        lp = ledger_path or _ledger_path(home)
        # v4(owner 2026-07-25): 이번 세션 실제 회상 우선 — 도움 판정 대상은 '이번 세션 인출 회상'이다.
        #   session_id 주어지고 이번 세션 회상이 있으면 그것(효용 판정), 없으면(구세션·NULL) 누적 청소분 폴백.
        session_pending = (RT.list_pending(home=home, ledger_path=lp, session_id=session_id)
                           if session_id else [])
        if session_pending:
            sel = []
            for p in reversed(session_pending):  # list_pending 은 ts asc → 뒤가 최신
                sel.append({"trace_id": p["trace_id"], "node_id": p["node_id"],
                            "category": p.get("category"), "rank": p.get("rank"),
                            "claim": p.get("claim"), "flag": "session"})
                if len(sel) >= top_n:
                    break
            for i, s in enumerate(sel, 1):
                s["idx"] = i
            RT.save_review_snapshot(sel, home=home)  # N → (trace_id,node_id) 고정(N-shift 안전)
            items = [{"idx": s["idx"], "category": s.get("category"), "rank": s.get("rank"),
                      "claim": s.get("claim"), "flag": s.get("flag")} for s in sel]
            return {"available": True, "count": len(items), "total_pending": len(session_pending),
                    "items": items, "scope": "session",
                    "note": ("이번 세션 실제 회상 %d건 중 %d건 — 도움됐으면 `히트 N`·안 도움이면 `미스 N`"
                             "(actor=human). 이번 세션 인출 회상의 효용 판정(v4 session_id 필터)."
                             % (len(session_pending), len(items)))}
        pending = RT.list_pending(home=home, ledger_path=lp)
        total = len(pending)
        if not pending:
            return {"available": True, "count": 0, "total_pending": 0, "items": [],
                    "note": "누적 미판정 회상 없음(trace OFF 거나 회상 0 — binggu trace enable 로 켜짐)"}
        # AI 자동선별: 미스 후보(오래됨·미편입) 우선 → 나머지는 최신 회상으로 채움(top_n 컷)
        miss = RT.list_miss_candidates(now_ts, home=home, ledger_path=lp, top_n=top_n) if now_ts else []
        seen = {(m["trace_id"], m["node_id"]) for m in miss}
        selected = [dict(m, flag="miss") for m in miss]
        if len(selected) < top_n:
            for p in reversed(pending):  # list_pending 은 ts asc → 뒤가 최신
                k = (p["trace_id"], p["node_id"])
                if k in seen:
                    continue
                seen.add(k)
                selected.append({"trace_id": p["trace_id"], "node_id": p["node_id"],
                                 "category": p.get("category"), "rank": p.get("rank"),
                                 "claim": p.get("claim"), "flag": "recent"})
                if len(selected) >= top_n:
                    break
        for i, s in enumerate(selected, 1):
            s["idx"] = i
        RT.save_review_snapshot(selected, home=home)  # N → (trace_id,node_id) 고정(N-shift 안전)
        items = [{"idx": s["idx"], "category": s.get("category"), "rank": s.get("rank"),
                  "claim": s.get("claim"), "flag": s.get("flag"),
                  "age_hours": s.get("age_hours")} for s in selected]
        miss_n = sum(1 for s in selected if s.get("flag") == "miss")
        return {"available": True, "count": len(items), "total_pending": total, "items": items,
                "note": ("이번 세션이 아니라 전체 누적 미판정 %d건 중 청소 시급분 %d건 선별"
                         "(⚠미스 후보 %d = 오래됨·그래프 미편입 우선). "
                         "도움=`히트 N`·헛다리=`미스 N`(actor=human) — 선별=조회, 도장=사람(자동 0)"
                         % (total, len(items), miss_n))}
    except Exception:
        return {"available": False, "count": 0, "total_pending": 0, "items": [],
                "note": "회상 후보 로드 실패(graceful 생략)"}


# ---------------- 2d. AI 제안 L1 명제 (hybrid_agi · 승인 대기 · 화자축 분리 · read-only) ----------------

def _build_l1_proposals(home=None):
    """이번 세션 AI 제안 L1 명제(hybrid_agi · 승인 대기 · ai_inferred · owner candidate 와 분리).
    hag_l1_bridge.list_pending 재사용(모듈/스토어 부재 → count 0 graceful). owner 승인(도장) 전
    비영구 — 운영 ledger write 0. 화자축 분리: owner 후보 큐(capture_buffer)와 물리 별도 파일.
    반환 {available, count, items:[{idx,proposition,source}], note}."""
    try:
        import sys as _sys
        _hag = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "scripts", "hybrid_agi")
        if _hag not in _sys.path:
            _sys.path.insert(0, _hag)
        import hag_l1_bridge as HB
    except Exception:
        return {"available": False, "count": 0, "items": [],
                "note": "hag_l1_bridge 모듈 미사용(AI 제안 생략)"}
    try:
        db = HB.l1_db_path(home)
        if not os.path.exists(db):
            return {"available": True, "count": 0, "items": [],
                    "note": "AI 제안 명제 없음(binggu l1-propose 로 적재)"}
        conn = HB.open_l1_db(db)
        try:
            pend = HB.list_pending(conn)
        finally:
            conn.close()
        items = [{"idx": i + 1, "proposition": p["proposition"], "source": p["l0_raw"]}
                 for i, p in enumerate(pend)]
        return {"available": True, "count": len(items), "items": items,
                "note": "AI 제안(ai_inferred) — owner 승인 전 비영구·owner 후보와 화자축 분리(자동 0)"}
    except Exception:
        return {"available": False, "count": 0, "items": [],
                "note": "AI 제안 로드 실패(graceful 생략)"}


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


def build_close_summary(home=None, cwd=None, ledger_path=None, session_id=None,
                        today=None, now_ts=None):
    """세션 마무리 표시용 요약 빌드(저장 0 · read-only). preview + 거버넌스 묶음.
    session_id 지정 시 preview 를 그 세션 발화로 한정(세션 경계). today 파라미터는 하위호환
    유지(구 지적 후보 필터용 — §supersede: 지적 후보 섹션은 2026-07-16 owner 지시로 폐지,
    판정은 binggu verdict 즉시 기록으로 대체 — 세션마무리 배치 확인 의식 0).
    now_ts(ISO · 호출자 주입 · Date.now 미사용): 회상 미스 후보 나이 판정용 — 미지정 시
    미스 자동선별 생략(최신 미판정만 · graceful).
    반환 {preview, recall_hits, governance, save_action}."""
    _ = today  # 하위호환(구 시그니처 호출자 무해)
    return {
        "preview": _build_preview(home, session_id=session_id),
        "recall_hits": _build_recall_hits(home, ledger_path, now_ts=now_ts, session_id=session_id),
        "l1_proposals": _build_l1_proposals(home),
        "governance": _build_governance(home, cwd, ledger_path),
        "save_action": {
            "auto_save": False,
            "how": "저장은 사람이 직접 — preview 번호를 보고 **이 세션 채팅에** `SAVE 1,2`(여러 개 한 번) 발화 시 앵커 생성→저장. 도움된 회상은 `히트 1,2`·헛다리는 `미스 3`(H 접두 금지 — 게이트는 `히트/미스 \\d+` 만 인식). 안 하면 넘어감(강제 0). 로컬 터미널 별도 실행 안내 금지(이 세션에서 완결)·빙구팩 자동저장 0(저장 확정=사람 · T2). 효용 도장은 사람 히트/미스 + T0 그래프편입 자동관측(opt-in · 헌법 v2 · used only).",
        },
    }


# ---------------- 4. 렌더링 (결정적 마크다운 · 저장 0) ----------------

def _build_paste_block(summary):
    """저장·히트 후보를 owner 가 한 번에 복붙할 단일 블록(각 줄 = 게이트 정확 문법).

    게이트 _stamp_chunks(gate_log.py)가 발화의 **줄 단위 fullmatch** 로 각 줄을 개별
    인식하고 SAVE/히트/승격 트리거는 서로소라, owner 가 이 블록을 한 메시지로 붙여넣으면
    통합 파서 없이 각 줄이 자기 종류로 도장된다(4cli+Fable5 수렴 — 통합 파서는 fullmatch
    계약 위반이라 기각). L1 제안(P)은 destination(단계2) 미배선이라 제외(죽은 명령 방지).
    각 줄 문법은 gate_log 정규식 정합: SAVE `\\d+`·히트 `\\d+`(H 접두 금지 — 도장 증발 버그)."""
    block = []
    pv = summary.get("preview", {}) or {}
    if pv.get("available") and pv.get("count"):
        block.append("SAVE %s" % ",".join(str(i) for i in range(1, pv["count"] + 1)))
    # 회상 판정(히트/미스)은 복붙 블록에 자동으로 넣지 않는다 — owner 가 도움/헛다리를
    # 골라 직접 도장(전체 자동 '히트 <전체>' = usefulness 100% 편향의 기계적 근원). SAVE 만 자동.
    return block


def render_close_md(summary):
    """세션 마무리 요약 → 사람이 읽는 마크다운(결정적 · LLM 0 · 저장 0).
    저장 preview(candidate) + 누적 미판정 회상 정리 후보(세션 무관) + 거버넌스(적중률) + 한 줄 도장 안내."""
    lines = ["## 세션 마무리 — 저장 preview + 회상 히트 + 거버넌스 (저장 0 · 사람이 SAVE/도장)"]

    # 1) 저장 후보
    pv = summary.get("preview", {}) or {}
    lines.append("")
    lines.append("### 1) 저장 후보 (candidate · active 아님)")
    if pv.get("available") and pv.get("count"):
        for it in pv.get("items", []):
            lines.append("- " + str(it.get("label", it.get("text", ""))))
            # B-2(대화쌍): 직전 AI말 발췌를 2줄로 노출 — owner 가 SAVE 하면 save_paired 로
            #   owner 발화 ↔ AI 말 pair(노드2+엣지1) 저장(별도 경로 신설 없음 · 재료 표시만).
            ai_ctx = it.get("ai_context")
            if ai_ctx:
                s = " ".join(str(ai_ctx).split())
                lines.append("    ↳ 직전 AI말(대화쌍 재료 · SAVE 시 pair 저장): %s"
                             % (s if len(s) <= 80 else s[:79] + "…"))
        lines.append("> %s" % pv.get("note", "owner 승인 전 candidate"))
    else:
        lines.append("- (수집된 candidate 없음 — 표시할 preview 0)")
    bv = pv.get("bulk_vetoed", 0)
    if bv:
        lines.append("- ⚠️ 긴 발화 %d건 자동 제외(붙여넣기·대화 덩어리·AI 응답문 — 화자축 오염 방지). "
                     "진짜 저장하려면 그 내용에 `이거 저장해` 명시." % bv)

    # 2) 누적 미판정 회상 정리 후보 (recall_trace 통합 · 세션 무관 · MF3 회상효용 축 · owner 도장)
    rh = summary.get("recall_hits", {}) or {}
    lines.append("")
    lines.append("### 2) 누적 미판정 회상 — 정리 (세션 무관 · 오래됨/미편입 우선 · 도움=`히트 N` / 헛다리=`미스 N`)")
    if rh.get("available") and rh.get("count"):
        for it in rh.get("items", []):
            cat = (" [%s]" % it["category"]) if it.get("category") else ""
            rank = (" score=%.2f" % it["rank"]) if isinstance(it.get("rank"), (int, float)) else ""
            claim = it.get("claim") or "(원문 미상)"
            if it.get("flag") == "miss":
                ah = it.get("age_hours")
                age = (" · %.0fh 안 쓰임" % ah) if isinstance(ah, (int, float)) else ""
                mark = " ⚠미스후보"
            else:
                age = mark = ""
            lines.append("- %d.%s %s%s%s%s" % (it["idx"], mark, claim, cat, rank, age))
        lines.append("> %s" % rh.get("note", "도움=히트 N·헛다리=미스 N — 양쪽 다 도장해야 정직·안 치면 pending 유지·자동 0"))
    else:
        lines.append("- (누적 미판정 회상 없음 — trace OFF 거나 회상 0)")

    # 2-b) AI 제안 L1 명제 (hybrid_agi · 승인 대기 · owner 후보와 화자축 분리)
    lp1 = summary.get("l1_proposals", {}) or {}
    lines.append("")
    lines.append("### 2-b) AI 제안 명제 (hybrid_agi · 승인 대기 · owner 후보와 분리)")
    if lp1.get("available") and lp1.get("count"):
        for it in lp1.get("items", []):
            lines.append("- P%d. %s" % (it["idx"], it["proposition"]))
            src = it.get("source")
            if src:
                s = " ".join(str(src).split())
                lines.append("    ↳ 출처: %s" % (s if len(s) <= 60 else s[:59] + "…"))
        lines.append("> %s" % lp1.get("note", "AI 제안 — owner 승인 전 비영구"))
    else:
        lines.append("- (AI 제안 명제 없음)")

    # 3) 거버넌스
    gv = summary.get("governance", {}) or {}
    lines.append("")
    lines.append("### 3) 거버넌스 정리 — 대비 기록·적중률 (신호 · 상관≠인과)")
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

    # 4) 한 줄로 도장 (간결 UX · 안 하면 넘어감 · 강제 0)
    sa = summary.get("save_action", {}) or {}
    lines.append("")
    lines.append("### 4) 한 줄로 도장 (안 하면 넘어감 · 강제 0)")
    lines.append("- 저장: `SAVE n` — 예 `SAVE 1,2`(여러 개 한 번) 또는 `SAVE all`")
    lines.append("- 히트: `히트 1,2` — 도움된 회상 / 미스: `미스 3` — 헛다리·안 도움된 회상(여러 개 한 줄)")
    lines.append("  (양쪽 다 도장해야 usefulness 정직 — 히트만 = 100% 가짜·안 치면 pending 유지)")
    lines.append("- 자동저장: **0** · 자동도장: **0** (헌법 — 사람 앵커·actor=human 만)")
    lines.append("- %s" % sa.get("how", "저장·도장은 사람이 직접."))

    # 5) 한 번에 복사 저장 (복붙 블록 · 게이트 줄단위 인식 · 통합 파서 불요)
    paste = _build_paste_block(summary)
    lines.append("")
    lines.append("### 5) 한 번에 저장 — 아래 블록을 복사해 한 메시지로 붙여넣기")
    if paste:
        lines.append("```")
        lines.extend(paste)
        lines.append("```")
        lines.append("> 각 줄이 게이트에 개별 인식 — 한 번 붙여넣기로 전 종류 저장(원하는 줄만 남겨 부분 저장도 가능). "
                     "회상 판정(히트/미스)은 자동으로 안 넣음 — §2 보고 도움=`히트 N`·헛다리=`미스 N` 직접(편향 방지). "
                     "AI 제안(P)은 저장 경로 준비 중(단계2)이라 블록에서 제외.")
    else:
        lines.append("- (저장·히트 후보 없음 — 복사할 블록 0)")

    # (§supersede 2026-07-16 owner) 구 "당일 owner 지적 후보" 섹션 폐지 — 판정은 논쟁이
    # 실측으로 판가름 난 순간 `binggu verdict` 즉시 기록(개방 기록 트랙·의식 0)으로 대체.

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
    import time
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())  # hook 운영 진입점 — 미스 후보 나이 판정용
    summary = build_close_summary(home, cwd, ledger_path, session_id=sid, now_ts=now_ts)
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

        # T9b B-2: dialectic 발화(prev_turn) → preview 대화쌍 노출 + render 에 직전 AI말 2줄
        pair_ok = True
        try:
            b.feed("아니 그게 아니라 A안이 맞다", "C:/Users/fixture-user/binggupack",
                   prev_turn="B안을 추천합니다")
            pv2 = _build_preview(home=home)
            has_ctx = any(it.get("ai_context") for it in pv2.get("items", []))
            md_pair = render_close_md({"preview": pv2, "save_action": {"auto_save": False}})
            pair_ok = has_ctx and "대화쌍 재료" in md_pair and "B안을 추천합니다" in md_pair
        except Exception:
            pair_ok = False
        check(pair_ok, "T9b dialectic → preview ai_context 노출 + render '대화쌍 재료' 직전 AI말 표시")

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

        # ── T18~T19 §supersede(2026-07-16 owner): 지적 후보 섹션 폐지 — verdict 즉시 기록 대체 ──
        #    큐에 당일 대기 항목이 있어도 세션마무리에 섹션이 안 뜨고 큐 write 0 인지 확인.
        st_dir = home / "state"
        st_dir.mkdir(parents=True, exist_ok=True)
        qp = st_dir / "learn_outcome_queue.jsonl"
        qp.write_text(json.dumps(
            {"ts": "2026-07-13T01:00:00Z", "outcome": "miss", "queries": [],
             "evidence": {"feedback": "너도 제대로 안 읽었는데"}, "consumed": False},
            ensure_ascii=False) + "\n", encoding="utf-8")
        q_mtime = qp.stat().st_mtime_ns
        s18 = build_close_summary(home=home, today="2026-07-13")
        md18 = render_close_md(s18)
        check("outcome_candidates" not in s18
              and "당일 owner 지적 후보" not in md18 and "CONSUME" not in md18
              and qp.stat().st_mtime_ns == q_mtime,
              "T18 지적 후보 섹션 폐지(§supersede) — 당일 큐 존재해도 미표시·큐 write 0")
        check(callable(_build_outcome_candidates)
              and "DEPRECATED" in (_build_outcome_candidates.__doc__ or ""),
              "T19 구 빌더는 호출부 0·DEPRECATED 마킹 보존(이력)")

        # ── T20~T21 이번 세션 회상 히트 후보(recall_trace 통합 · MF3 회상효용 축 · owner 도장) ──
        rh_ok = True
        try:
            from binggu_schema import apply_schema
            from binggupack.pack import recall_trace as RT
            home_rh = tmp / ".binggupack_rh"
            home_rh.mkdir(parents=True)
            RT.set_trace_flag(True, home=str(home_rh))
            led_rh = home_rh / "ledger.sqlite"
            lcon = sqlite3.connect(str(led_rh))
            apply_schema(lcon)
            lcon.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                         "created_at,semantic_subtype,use_count) VALUES"
                         "('node:CONV:h1','judgment','배포 전 live endpoint 확인',0,'active','h',"
                         "'2026-06-27T00:00:00Z','교훈',0)")
            lcon.commit()
            lcon.close()
            recalled = [{"node_id": "node:CONV:h1", "semantic_subtype": "교훈",
                         "rank_score": 0.9, "relevance": 0.8}]
            RT.record_trace("배포 점검", "why_search", recalled, "2026-06-27T00:00:00Z", home=str(home_rh))
            rh = _build_recall_hits(home=str(home_rh), ledger_path=str(led_rh))
            check(rh["available"] and rh["count"] == 1
                  and rh["items"][0]["claim"] == "배포 전 live endpoint 확인"
                  and rh["items"][0]["idx"] == 1,
                  "T20 회상 히트 후보 빌드(recall_trace list_pending 통합·claim join·idx)")
            check((home_rh / "recall_trace_review.json").exists(),
                  "T20b review snapshot 저장(H N → trace/node 고정 · mark N-shift 안전)")
            s_rh = build_close_summary(home=str(home_rh), ledger_path=str(led_rh))
            md_rh = render_close_md(s_rh)
            # 복붙 블록엔 SAVE 만(자동 '히트 <전체>' 제거 = 편향 근원 차단) · §2·§4 는 히트/미스 양자 유도
            paste_rh = _build_paste_block(s_rh)
            check("누적 미판정 회상" in md_rh and "- 1. " in md_rh
                  and "미스 N" in md_rh and "미스 3" in md_rh and "SAVE 1,2" in md_rh
                  and "### 5) 한 번에 저장" in md_rh
                  and "자동저장: **0**" in md_rh
                  and all(not ln.startswith("히트") for ln in paste_rh),
                  "T21 render: 회상 판정 히트/미스 양자 유도 + 복붙 SAVE only(자동 전체히트 제거) + 자동 0")
            rh0 = _build_recall_hits(home=str(tmp / ".binggupack_rh0"))
            check(rh0["count"] == 0,
                  "T21b trace 부재 home → 히트 후보 0(graceful · 운영홈 미접촉)")
            # T21c(v4 session_id): 이번 세션 회상 우선(scope=session) vs 폴백(누적)
            home_sc = tmp / ".binggupack_sc"
            RT.set_trace_flag(True, home=str(home_sc))
            RT.record_trace("이번세션회상", "preflight", [{"node_id": "node:CONV:sc1"}],
                            "2026-07-25T01:00:00Z", session_id="SC_NOW", home=str(home_sc))
            RT.record_trace("구세션회상", "preflight", [{"node_id": "node:CONV:sc2"}],
                            "2026-07-24T01:00:00Z", session_id="SC_OLD", home=str(home_sc))
            rh_now = _build_recall_hits(home=str(home_sc), session_id="SC_NOW")
            rh_fb = _build_recall_hits(home=str(home_sc))
            check(rh_now.get("scope") == "session" and rh_now["count"] == 1
                  and rh_fb.get("scope") != "session" and rh_fb["total_pending"] == 2,
                  "T21c(v4) session_id → 이번 세션 회상 우선(scope=session·1건) · 폴백=누적(2건)")
        except Exception as e:
            rh_ok = False
            check(rh_ok, "T20~T21 회상 히트 통합 예외: %s" % type(e).__name__)

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
