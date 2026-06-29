# -*- coding: utf-8 -*-
"""binggu_semantic_clean — 의미정제(LLM 노이즈 판단) : 청크 리스트 → 본문만 남김.

역할: harvest/parser 가 뽑은 청크 중 **안내문/메뉴/광고/JS경고/쿠키동의/네비** 같은
      주제무관 노이즈를 LLM 판단으로 제거하고 실제 정보 본문만 남긴다.

★ 고정 단어 리스트 금지(brittle·정상 본문 오인):
  - 노이즈 판정을 '자바스크립트/쿠키/로그인' 같은 고정 키워드로 박지 않는다.
  - "이 청크가 실제 정보 본문인가, 안내/메뉴/광고/JS경고/네비 노이즈인가" 는 LLM(transport)이 판단.
  - 코드는 메커니즘만: 배치 묶기 · LLM 호출 · 결과 매핑 · 폴백.
  - 폴백(LLM 없음)도 고정 단어 0 — 주제무관 **구조 신호**(아주 짧은 토막·과도한 링크 밀도)만
    binggu_harvest 의 구조 휴리스틱을 재사용하거나, 그조차 불가하면 전체 통과(무손실·보수적).

조건3(무손실 지향): clean_chunks 는 **절대 raise 하지 않는다**. transport/파싱 예외는 흡수하고
  해당 배치를 보수적으로 kept 로 유지한다(노이즈 제거 실패 < 본문 손실). 의심스러우면 보존.

실 ollama 는 default_ollama_transport() 팩토리(함수 내부 lazy urllib)에만 격리 — selftest 미사용·실 네트워크 전용.
"""
from __future__ import annotations

import json

# 폴백 구조 신호 임계(주제무관·고정 단어 0). 보수적 — 의심스러우면 보존.
_FALLBACK_MIN_LEN = 8        # 이 길이 미만 토막 + 정보신호 부재 → 구조 노이즈(메뉴/네비 잔재)
_FALLBACK_LINK_DENSITY = 0.5  # 블록 문자의 이 비율 이상이 링크 + 다수 링크 + 정보신호 0 → 네비/메뉴
_FALLBACK_LINK_MIN = 3


# ── 청크 텍스트 추출(str / dict 양형 지원 — harvest dict 청크는 'text' 키) ───────
def _chunk_text(chunk):
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, dict):
        return str(chunk.get("text") or chunk.get("sentence") or "")
    return str(chunk or "")


# ── 프롬프트 — 배치 청크 번호 매김 + 본문/노이즈 판단 지시(고정 단어 0) ──────────
def build_clean_prompt(batch, hints=None):
    """배치 청크에 번호를 매겨 제시하고, 각 청크가 '실제 정보 본문(1)'인지
    '안내/메뉴/광고/JS경고/쿠키동의/네비 노이즈(0)'인지 순서대로 판단하도록 지시.

    hints(plan.semantic_hints) 가 있으면 **참고 가이드로만** 덧붙인다(강제 규칙 아님).
    코드가 단어를 매칭하지 않는다 — 판단은 전적으로 LLM 몫. 노이즈 범주 설명은
    LLM 에게 과제를 알려주는 지시문일 뿐(코드 레벨 고정 키워드 필터가 아님)."""
    lines = []
    lines.append("다음은 웹/문서에서 추출한 텍스트 청크들이다. 각 청크가 실제 '정보 본문'인지,")
    lines.append("아니면 안내문·메뉴·광고/클릭베이트·JS/쿠키 안내·네비게이션 같은 '노이즈'인지 판단하라.")
    lines.append("정보 본문이면 1, 노이즈면 0. 청크 번호 순서대로 verdicts 배열에 담아라.")
    lines.append('출력은 JSON 한 줄: {"verdicts": [1, 0, ...]} (청크 개수와 길이 동일).')
    lines.append("애매하면 본문(1)으로 보존하라(정보 손실보다 노이즈 잔존이 낫다).")
    hint_list = _norm_hints(hints)
    if hint_list:
        lines.append("참고(가이드일 뿐·강제 아님) — 이 주제에서 관심 관점: " + ", ".join(hint_list))
    lines.append("")
    lines.append("=== 청크 %d개 ===" % len(batch))
    for i, c in enumerate(batch, start=1):
        t = _chunk_text(c).replace("\n", " ").strip()
        lines.append("[%d] %s" % (i, t))
    return "\n".join(lines)


def _norm_hints(hints):
    if not hints:
        return []
    if isinstance(hints, str):
        hints = [hints]
    out = []
    for h in hints:
        s = str(h or "").strip()
        if s and s not in out:
            out.append(s)
    return out


# ── verdict 파싱(절대 raise 0) — 코드펜스/잡담 섞여도 verdicts 배열 추출 ──────────
def _strip_fences(text):
    s = str(text or "").strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _extract_json(text):
    """첫 {...} 또는 [...] 블록 파싱(raise 0). 실패 → None."""
    for op, cl in (("{", "}"), ("[", "]")):
        i, j = text.find(op), text.rfind(cl)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                continue
    return None


def _coerce_verdict(v):
    """단일 verdict → bool(본문 keep) / None(불명). 1·true·본문류 → True, 0·false → False."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(int(v))
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "keep", "본문", "content"):
            return True
        if s in ("0", "false", "no", "drop", "노이즈", "noise"):
            return False
        return None
    if isinstance(v, dict):
        for k in ("keep", "verdict", "label", "is_content", "본문"):
            if k in v:
                return _coerce_verdict(v[k])
    return None


def _parse_verdicts(resp):
    """transport 응답(str|dict|list) → verdict 리스트(bool|None) 또는 None(파싱 실패). raise 0."""
    if resp is None:
        return None
    if isinstance(resp, (list, dict)):
        data = resp
    elif isinstance(resp, str):
        text = _strip_fences(resp)
        if not text:
            return None
        try:
            data = json.loads(text)
        except Exception:
            data = _extract_json(text)
            if data is None:
                return None
    else:
        return None
    arr = None
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        for k in ("verdicts", "labels", "results", "judgments", "verdict"):
            v = data.get(k)
            if isinstance(v, list):
                arr = v
                break
        if arr is None:
            return None
    if arr is None:
        return None
    return [_coerce_verdict(x) for x in arr]


# ── 폴백 구조 휴리스틱(고정 단어 0 — binggu_harvest 구조 신호 재사용) ─────────────
def _structural_keep(text):
    """LLM 없음 폴백 — 주제무관 구조 신호만으로 본문/노이즈 판정(고정 단어 0).
    반환 (keep:bool, reason:str|None). 보수적: 명백한 구조 노이즈만 drop, 나머지 보존(무손실).
    binggu_harvest 의 _has_info_signal/_link_stats 재사용. import 실패 시 전체 통과(보존)."""
    s = str(text or "").strip()
    if not s:
        return False, "structural: empty"
    try:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import binggu_harvest as _hv
        has_info = _hv._has_info_signal(s)
        n_links, dens = _hv._link_stats(s)
    except Exception:
        return True, None  # 구조 분석 불가 → 보존(무손실·보수적)
    # ① 아주 짧은 토막 + 정보신호 부재 → 메뉴/네비/버튼 라벨 잔재
    if len(s) < _FALLBACK_MIN_LEN and not has_info:
        return False, "structural: trivial fragment(len<%d, no info-signal)" % _FALLBACK_MIN_LEN
    # ② 링크 과밀(블록 대부분 링크) + 다수 링크 + 정보신호 부재 → 네비/메뉴
    if dens >= _FALLBACK_LINK_DENSITY and n_links >= _FALLBACK_LINK_MIN and not has_info:
        return False, "structural: link-dense nav(density=%.2f, links=%d)" % (dens, n_links)
    return True, None


# ── 핵심: clean_chunks ──────────────────────────────────────────────────────
def clean_chunks(chunks, llm_transport=None, batch_size=10, hints=None):
    """청크 리스트 → 본문만 남김. **절대 raise 0**(transport/파싱 예외 흡수·보수적 보존).

    LLM 주 경로: llm_transport(prompt:str)->response 주입 시, batch_size 씩 묶어 LLM 이
      각 청크가 본문(1)/노이즈(0)인지 판단. 결과로 kept/dropped 분리.
    폴백: llm_transport=None 또는 배치 transport/파싱 실패 시 → 구조 휴리스틱(고정 단어 0) 또는
      전체 통과(무손실·보수적). 노이즈 제거 실패 < 본문 손실.

    반환 {
      kept:    [원본 청크 객체...]      # 본문(원형 보존 — dict 면 item_id 등 유지)
      dropped: [{"text":..., "reason":..., "chunk":원본}]
      stats:   {total, kept, dropped, batches, source, llm_batches, fallback_batches, errors:[...]}
    }
    """
    chunks = list(chunks or [])
    result_kept, result_dropped = [], []
    errors = []
    n_batches = 0
    llm_batches = 0
    fallback_batches = 0

    if not chunks:
        return {"kept": [], "dropped": [],
                "stats": {"total": 0, "kept": 0, "dropped": 0, "batches": 0,
                          "source": "none", "llm_batches": 0, "fallback_batches": 0, "errors": []}}

    bs = batch_size if isinstance(batch_size, int) and batch_size > 0 else 10

    for start in range(0, len(chunks), bs):
        batch = chunks[start:start + bs]
        n_batches += 1
        verdicts = None

        if llm_transport is not None:
            prompt = build_clean_prompt(batch, hints=hints)
            try:
                resp = llm_transport(prompt)
            except Exception as e:
                resp = None
                errors.append({"batch": n_batches, "stage": "transport", "detail": str(e)[:150]})
            if resp is not None:
                verdicts = _parse_verdicts(resp)
                if verdicts is None:
                    errors.append({"batch": n_batches, "stage": "parse", "detail": "verdicts unparseable"})

        if verdicts is not None:
            # LLM verdict 매핑 — 길이 불일치 안전(부족분=보존(1), 초과분 무시)
            llm_batches += 1
            for i, c in enumerate(batch):
                keep = verdicts[i] if i < len(verdicts) else None
                if keep is None:          # 불명/누락 → 보수적 보존(본문 손실 방지)
                    keep = True
                if keep:
                    result_kept.append(c)
                else:
                    result_dropped.append({"text": _chunk_text(c), "reason": "llm:noise", "chunk": c})
        else:
            # 폴백 — 구조 휴리스틱(고정 단어 0) 또는 전체 통과(무손실)
            fallback_batches += 1
            for c in batch:
                keep, reason = _structural_keep(_chunk_text(c))
                if keep:
                    result_kept.append(c)
                else:
                    result_dropped.append({"text": _chunk_text(c), "reason": reason or "structural", "chunk": c})

    if llm_batches and not fallback_batches:
        source = "llm"
    elif fallback_batches and not llm_batches:
        source = "fallback"
    elif llm_batches and fallback_batches:
        source = "mixed"
    else:
        source = "none"

    return {
        "kept": result_kept,
        "dropped": result_dropped,
        "stats": {
            "total": len(chunks),
            "kept": len(result_kept),
            "dropped": len(result_dropped),
            "batches": n_batches,
            "source": source,
            "llm_batches": llm_batches,
            "fallback_batches": fallback_batches,
            "errors": errors,
        },
    }


# ── 실 ollama transport 팩토리(selftest 미사용·실 네트워크 전용·lazy) ───────────
def default_ollama_transport(model="qwen2.5:14b-instruct-q4_K_M",
                             url="http://localhost:11434", timeout=120):
    """실 ollama generate transport 생성기(클로저) — **selftest 미사용·실 endpoint 전용**.
    반환 transport(prompt:str)->response:str. urllib 는 함수 내부 lazy(서드파티 0).
    네트워크/파싱 예외는 호출자(clean_chunks)가 흡수 — transport 자체는 실 경로라 raise 가능.
    ollama generate API: POST {url}/api/generate {model, prompt, format:'json', stream:false}."""
    def _transport(prompt):
        import json as _json
        import urllib.request as _u
        endpoint = str(url).rstrip("/") + "/api/generate"
        body = _json.dumps({"model": model, "prompt": str(prompt),
                            "format": "json", "stream": False}).encode("utf-8")
        req = _u.Request(endpoint, data=body,
                         headers={"Content-Type": "application/json"}, method="POST")
        with _u.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            data = _json.loads(raw)
        except Exception:
            return raw
        if isinstance(data, dict) and "response" in data:
            return data.get("response")
        return raw
    return _transport


# ── selftest (transport mock · 실 네트워크 0 · 결정적) ──────────────────────────
def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    BODY1 = "이 보고서는 2026년 1분기 매출이 전년 대비 12% 증가했다고 분석한다."
    BODY2 = "제안한 방법은 세 개의 벤치마크에서 일관되게 향상을 보였다."
    NOISE1 = "로그인 | 회원가입 | 마이페이지 | 장바구니"
    NOISE2 = "자바스크립트를 활성화하세요. 쿠키 동의가 필요합니다."

    # T1 — LLM 경로: 고정 verdict mock 으로 본문/노이즈 분리
    def _t_fixed(prompt):
        return '```json\n{"verdicts": [1, 1, 0, 0]}\n```'  # 코드펜스 견고성 포함
    r = clean_chunks([BODY1, BODY2, NOISE1, NOISE2], llm_transport=_t_fixed, batch_size=10)
    chk("T1 LLM 본문/노이즈 분리(kept=2, dropped=2)",
        r["stats"]["kept"] == 2 and r["stats"]["dropped"] == 2)
    chk("T1b kept 가 본문 청크", BODY1 in r["kept"] and BODY2 in r["kept"])
    chk("T1c dropped reason=llm:noise",
        all(d["reason"] == "llm:noise" for d in r["dropped"]))
    chk("T1d source=llm", r["stats"]["source"] == "llm")

    # T2 — 배치 경계: batch_size=2, 5청크 → 3배치 전부 처리(누락 0)
    calls = {"n": 0}

    def _t_batched(prompt):
        calls["n"] += 1
        # 각 배치 청크 수를 세서 전부 본문(1)으로 — 청크 수에 맞춰 verdicts 생성
        n = prompt.count("\n[")  # 번호 라인 수 ≈ 청크 수
        return json.dumps({"verdicts": [1] * max(1, n)})
    five = [BODY1, BODY2, "세 번째 본문 내용입니다 숫자 100 포함.", "네 번째 본문 문장.", BODY2 + " 끝"]
    r = clean_chunks(five, llm_transport=_t_batched, batch_size=2)
    chk("T2 배치 경계(5청크/bs2 → 3배치)", r["stats"]["batches"] == 3 and calls["n"] == 3)
    chk("T2b 전건 처리(kept=5)", r["stats"]["kept"] == 5)

    # T3 — 폴백(llm_transport=None): 구조 휴리스틱 또는 전체 통과(무손실·본문 보존)
    r = clean_chunks([BODY1, BODY2], llm_transport=None)
    chk("T3 폴백 본문 보존(kept=2)", r["stats"]["kept"] == 2)
    chk("T3b source=fallback", r["stats"]["source"] == "fallback")

    # T4 — verdict 길이 불일치(부족) → 누락분 보수적 보존(crash 0)
    def _t_short(prompt):
        return '{"verdicts": [0]}'  # 4청크인데 1개만
    r = clean_chunks([BODY1, NOISE1, BODY2, NOISE2], llm_transport=_t_short, batch_size=10)
    chk("T4 verdict 부족 → 누락분 보존(kept>=3)", r["stats"]["kept"] >= 3 and r["stats"]["dropped"] == 1)

    # T4b — verdict 초과 → 초과분 무시(crash 0)
    def _t_long(prompt):
        return '{"verdicts": [1, 1, 1, 1, 1, 1, 1]}'  # 2청크인데 7개
    r = clean_chunks([BODY1, BODY2], llm_transport=_t_long)
    chk("T4b verdict 초과 → 무시(kept=2)", r["stats"]["kept"] == 2)

    # T5 — transport 예외 → 배치 보수적 보존(raise 0·본문 손실 0)
    def _t_boom(prompt):
        raise RuntimeError("ollama down")
    r = clean_chunks([BODY1, NOISE1, BODY2], llm_transport=_t_boom)
    chk("T5 transport 예외 → 전건 보존(kept=3, raise 0)", r["stats"]["kept"] == 3)
    chk("T5b 예외가 stats.errors 에 기록", any(e["stage"] == "transport" for e in r["stats"]["errors"]))
    chk("T5c 폴백 경로(source=fallback)", r["stats"]["source"] == "fallback")

    # T6 — 비-JSON garbage 응답 → 파싱 실패 → 폴백 보존(raise 0)
    def _t_garbage(prompt):
        return "죄송합니다 판단할 수 없습니다 (no json here)"
    r = clean_chunks([BODY1, BODY2], llm_transport=_t_garbage)
    chk("T6 garbage 응답 → 폴백 보존(kept=2)", r["stats"]["kept"] == 2)
    chk("T6b parse 실패 기록", any(e["stage"] == "parse" for e in r["stats"]["errors"]))

    # T7 — dict 청크(harvest 형태) 원형 보존(item_id 유지)
    dchunks = [
        {"item_id": "EVC-1", "text": BODY1, "source": "s"},
        {"item_id": "EVC-2", "text": NOISE1, "source": "s"},
    ]
    r = clean_chunks(dchunks, llm_transport=lambda p: '{"verdicts":[1,0]}')
    chk("T7 dict 청크 kept 원형 보존(item_id 유지)",
        len(r["kept"]) == 1 and r["kept"][0].get("item_id") == "EVC-1")
    chk("T7b dropped 에 원본 chunk 동봉", r["dropped"][0]["chunk"].get("item_id") == "EVC-2")

    # T8 — 빈 입력 → 빈 결과(crash 0)
    r = clean_chunks([], llm_transport=_t_fixed)
    chk("T8 빈 입력 → 빈 결과", r["stats"]["total"] == 0 and r["kept"] == [] and r["dropped"] == [])
    r = clean_chunks(None, llm_transport=None)
    chk("T8b None 입력 안전", r["stats"]["total"] == 0)

    # T9 — build_clean_prompt: 번호 매김 + hints 반영(고정 단어 매칭 아님)
    p = build_clean_prompt([BODY1, NOISE1], hints=["가격 동향", "공급사"])
    chk("T9 프롬프트 번호 매김([1]/[2])", "[1]" in p and "[2]" in p)
    chk("T9b hints 가이드 반영", "가격 동향" in p and "공급사" in p)
    chk("T9c verdicts 출력 계약 명시", "verdicts" in p)
    p2 = build_clean_prompt([BODY1], hints=None)
    chk("T9d hints 없어도 안전", "[1]" in p2)

    # T10 — default_ollama_transport 팩토리(callable 생성만·네트워크 0·호출 안 함)
    tr = default_ollama_transport(model="qwen2.5:14b-instruct-q4_K_M")
    chk("T10 default_ollama_transport → callable", callable(tr))
    tr2 = default_ollama_transport(url="http://localhost:11434", timeout=5)
    chk("T10b 인자 커스터마이즈 callable", callable(tr2))

    # T11 — _parse_verdicts 견고성(bare list / 다양한 표기 / 실패)
    chk("T11 bare list 파싱", _parse_verdicts("[1,0,1]") == [True, False, True])
    chk("T11b true/false 문자열", _parse_verdicts('{"verdicts":["true","false"]}') == [True, False])
    chk("T11c 파싱 실패 → None", _parse_verdicts("그냥 텍스트") is None)
    chk("T11d None → None", _parse_verdicts(None) is None)

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_semantic_clean — use --selftest, or import clean_chunks() / default_ollama_transport()")
