"""binggu_collection_planner — 주제 → LLM 이 분류 체계(관점)를 **동적 설계**(항목 A·2차 라인 발견 앞단).

"주제 하나 → 그 주제를 가장 잘 조사하기 위한 관점(aspect) 목록을 LLM 이 직접 설계".
예: "발리 신혼여행" → [동선/루트, 숨은명소, 로컬맛집, 대표관광지, 현지팁] 식이 아니라
    **주제마다 LLM 이 새로 짠** aspect 들(각 aspect = 검색 질의 묶음).

binggu_subtopic_decompose(템플릿 골격이 주 경로)와 **상보**:
  - decompose = 결정적 도메인 템플릿이 주 경로, LLM 은 opt-in 보강.
  - planner  = **LLM 동적 설계가 주 경로**, 룰 폴백은 무손실 안전망(미주입/실패 시).
discover/parser/적재는 전혀 안 함 — 이 모듈은 plan(관점+질의) 생성까지만.
Phase3(topic_to_pack)가 각 aspect.queries 를 discover 로 흘려보낸다.

설계 원칙(사장님 핵심 — 정형화 금지):
  - 분류 관점을 코드에 하드코딩하지 않는다. LLM 이 주제별로 동적 설계하는 게 **주 경로**.
  - 폴백(llm_transport=None / LLM 실패)조차 **가이드 관점**(동선/숨은명소/로컬맛집/대표관광지/현지팁)을
    **주제 토큰과 결합**해 질의를 만든다 — 고정 리스트를 그대로 박지 않음(주제별로 질의가 달라짐).
  - llm_transport = callable(prompt:str)->response:str **주입형**. PoC 검증: ollama generate API
    (POST /api/generate {model, prompt, format:"json", stream:false}) 로 동적 aspect 생성 성공.
  - 실 ollama 는 default_ollama_transport() 팩토리(함수 내부 lazy urllib)에만 격리 — selftest 미사용.
  - LLM 출력 JSON 파싱 견고: 코드펜스 제거·블록 추출·검증·aspect 필수키 보정·typed error(raise 0 지향).
  - 무손실: LLM 0(미주입/실패/빈결과)이어도 폴백 aspect 로 **항상 비어있지 않은 결과**.

진입점: plan(topic, llm_transport=None, aspects_hint=None) -> {topic, aspects:[...]}
        default_ollama_transport(model=..., url=...) -> callable(prompt)->response
"""
import re
import json

_DEFAULT_MAX_ASPECTS = 5
_ASPECT_KEYS = ("name", "why", "queries", "lang", "local_sources")


# ── 언어 추론(검색 언어 코드 기본값) — 분류 관점이 아니라 표기용. ───────────
def _infer_lang(text):
    """주 검색 언어 코드 추론 — 한글 포함이면 'ko', 아니면 'en'. (LLM 이 lang 주면 그게 우선)"""
    return "ko" if re.search(r"[가-힣]", str(text or "")) else "en"


def _combine(topic, *parts):
    """topic + 추가 토큰 결합(결정적). 빈 조각 제거."""
    bits = [str(topic or "").strip()]
    for p in parts:
        p = str(p or "").strip()
        if p:
            bits.append(p)
    return " ".join(b for b in bits if b).strip()


def _norm_str_list(v):
    """임의 값 → 문자열 리스트(중복 제거·순서 보존). str 단일 → [str]. 그 외 → []."""
    out, seen = [], set()
    if isinstance(v, str):
        v = [v] if v.strip() else []
    if isinstance(v, (list, tuple)):
        for x in v:
            s = str(x).strip()
            if not s or s.lower() in seen:
                continue
            seen.add(s.lower())
            out.append(s)
    return out


# ── LLM 프롬프트 빌드(순수함수·네트워크0·결정적) ──────────────────────────
def _build_prompt(topic, aspects_hint=None, max_aspects=_DEFAULT_MAX_ASPECTS):
    """주제 → 동적 분류 설계 요청 프롬프트. transport 가 이 문자열을 LLM 에 보냄.
    고정 분류틀을 주지 않는다(LLM 이 주제별 직접 설계). aspects_hint 는 '참고'로만 전달."""
    topic = str(topic or "").strip()
    n = max_aspects if (isinstance(max_aspects, int) and max_aspects > 0) else _DEFAULT_MAX_ASPECTS
    hint_line = ""
    hints = _norm_str_list(aspects_hint)
    if hints:
        hint_line = "\n참고 관점(필수 아님·그대로 쓰지 말 것): " + ", ".join(hints)
    return (
        "너는 정보 수집 설계 전문가다. 주어진 주제를 가장 잘 조사하기 위한 "
        "분류 체계(관점·aspect)를 주제에 맞춰 **직접 설계**하라(고정 틀·일반 템플릿 금지).\n"
        "주제: %s%s\n"
        "요구사항:\n"
        "- 이 주제에 가장 적합한 aspect 를 최대 %d개 설계.\n"
        "- 각 aspect 필드: name(관점명·간결), why(왜 중요한지 한 문장), "
        "queries(검색 질의 2~3개·현지어/영어 포함 권장), "
        "lang(주 검색 언어코드 예: ko/en/id/ja), "
        "local_sources(현지/공식 출처 힌트 배열·없으면 []).\n"
        "출력은 반드시 JSON 배열만(설명·코드펜스 없이): "
        "[{\"name\":\"\",\"why\":\"\",\"queries\":[\"\"],\"lang\":\"\","
        "\"local_sources\":[]}]\n"
        % (topic, hint_line, n)
    )


# ── LLM 응답 파싱(견고·raise 0) ──────────────────────────────────────────
def _strip_code_fences(text):
    """```json ... ``` / ``` ... ``` 코드펜스 제거. 펜스 없으면 원문."""
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_]*\s*", "", text)
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _extract_json_block(text):
    """본문에 잡담이 섞여도 첫 [...] 또는 {...} 블록을 추출해 파싱(raise 0). 실패 → None."""
    for op, cl in (("[", "]"), ("{", "}")):
        i, j = text.find(op), text.rfind(cl)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                continue
    return None


def _parse_aspects(resp):
    """LLM 응답(str|dict|list) → aspect dict 리스트 또는 None(파싱 실패). 절대 raise 0.
    list → 그대로 / dict 의 aspects|items|results|categories → 그 값 / 단일 aspect dict → [dict] /
    str → 코드펜스 제거 후 JSON, 실패 시 블록 추출."""
    if resp is None:
        return None
    if isinstance(resp, (list, dict)):
        data = resp
    elif isinstance(resp, str):
        text = _strip_code_fences(resp)
        if not text:
            return None
        try:
            data = json.loads(text)
        except Exception:
            data = _extract_json_block(text)
            if data is None:
                return None
    else:
        return None
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("aspects", "items", "results", "categories"):
            v = data.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        if "name" in data or "aspect" in data:
            return [data]
        return None
    return None


def _norm_queries(q, topic, name):
    """queries 정규화 — 리스트/문자열 → 문자열 리스트(중복제거). 비면 topic+name 결합으로 생성(무손실)."""
    out = _norm_str_list(q)
    if not out:
        out = [_combine(topic, name)]
    return out


def _normalize_aspects(raw, topic):
    """raw aspect dict 리스트 → 출력계약 [{name, why, queries, lang, local_sources}].
    필수키 보정·name 중복 제거·name 없으면 스킵. 절대 raise 0(방어적 순회)."""
    topic = str(topic or "").strip()
    out, seen = [], set()
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or item.get("aspect")
                or item.get("label") or item.get("category"))
        name = str(name or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        why = str(item.get("why") or item.get("rationale")
                  or item.get("reason") or "").strip()
        queries = _norm_queries(item.get("queries"), topic, name)
        lang = str(item.get("lang") or item.get("language")
                   or _infer_lang(topic)).strip() or _infer_lang(topic)
        local_sources = _norm_str_list(item.get("local_sources") or item.get("sources"))
        out.append({"name": name, "why": why, "queries": queries,
                    "lang": lang, "local_sources": local_sources})
    return out


# ── 폴백 — 가이드 관점 × 주제 토큰(고정 리스트 그대로 박지 않음·무손실) ──────
#   아래는 '특정 주제 분류'가 아니라 **어떤 주제에도 적용되는 일반 가이드 관점(lens)**.
#   질의는 lens × topic 결합으로 매번 주제에 맞게 생성된다(같은 lens 라도 topic 별로 질의가 달라짐).
#   LLM 동적 설계가 주 경로이고, 이건 LLM 미주입/실패 시의 안전망일 뿐.
_GUIDE_LENSES = (
    {"key": "route", "label": "동선·루트", "why": "효율적 이동 동선과 추천 코스",
     "ko": "추천 코스 동선 일정", "en": "itinerary route guide"},
    {"key": "hidden", "label": "숨은 명소", "why": "덜 알려진 현지 추천 장소",
     "ko": "숨은 명소 로컬 추천", "en": "hidden gems local"},
    {"key": "food", "label": "로컬 맛집", "why": "현지 음식과 추천 맛집",
     "ko": "현지 맛집 로컬 음식", "en": "local food best restaurants"},
    {"key": "landmark", "label": "대표 관광지", "why": "대표 명소·필수 코스",
     "ko": "대표 관광지 명소 가볼만한곳", "en": "top attractions must see"},
    {"key": "tips", "label": "현지 팁", "why": "현지 실용 정보·주의사항",
     "ko": "현지 팁 교통 주의사항", "en": "local tips transport advice"},
)


def _fallback_aspects(topic, aspects_hint=None, max_aspects=_DEFAULT_MAX_ASPECTS):
    """LLM 미주입/실패 시 무손실 폴백 — 가이드 관점(lens) × 주제 토큰으로 aspect 생성.
    aspects_hint(있으면) 를 우선 lens 로 채택(주제 토큰과 결합). 결정적(같은 입력 동일 출력)."""
    topic = str(topic or "").strip()
    lang = _infer_lang(topic)
    out, seen = [], set()

    def _add(name, why, ko, en):
        if not name or name.lower() in seen:
            return
        seen.add(name.lower())
        queries = _norm_queries(
            [_combine(topic, ko), _combine(topic, en)], topic, name)
        out.append({"name": name, "why": why, "queries": queries,
                    "lang": lang, "local_sources": []})

    # ① hint 우선(있으면) — 힌트 관점도 주제 토큰과 결합해 질의화(그대로 박지 않음)
    for h in _norm_str_list(aspects_hint):
        _add("%s %s" % (topic, h) if topic else h,
             "참고 관점(%s) 기반 수집" % h, h, h)

    # ② 일반 가이드 관점 lens × 주제 토큰
    for lens in _GUIDE_LENSES:
        name = ("%s %s" % (topic, lens["label"])).strip() if topic else lens["label"]
        _add(name, lens["why"], lens["ko"], lens["en"])

    if max_aspects and max_aspects > 0:
        out = out[:max_aspects]
    return out


# ── 실 ollama transport 팩토리(selftest 미사용·실 네트워크 전용) ───────────
def default_ollama_transport(model="qwen2.5:32b-instruct-q4_K_M",
                             url="http://localhost:11434", timeout=600):
    """실 ollama generate transport 생성기(클로저) — **selftest 미사용·실 endpoint 전용**.
    반환 transport(prompt:str)->response:str. urllib 는 함수 내부 lazy(서드파티 0).
    네트워크/파싱 예외는 호출자(plan)가 흡수 — transport 자체는 호출되면 raise 가능(실 경로).
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
        # ollama 응답은 {"response": "<생성 텍스트>", ...} — 생성 텍스트(JSON 문자열) 반환
        if isinstance(data, dict) and "response" in data:
            return data.get("response")
        return raw
    return _transport


# ── 핵심: plan ────────────────────────────────────────────────────────────
def plan(topic, llm_transport=None, aspects_hint=None, max_aspects=_DEFAULT_MAX_ASPECTS):
    """주제 → 분류 체계(관점) 동적 설계.

    LLM 주 경로: llm_transport(prompt:str)->response:str 주입 시 LLM 이 aspect 설계.
    폴백: llm_transport=None 또는 LLM 실패/빈결과 시 가이드 관점×주제 토큰(무손실).

    반환 {status, topic, source, aspects:[{name, why, queries, lang, local_sources}]}.
      status: OK / EMPTY_TOPIC
      source: llm / fallback
      llm_error(선택): TRANSPORT_FAILED / PARSE_FAILED / EMPTY_PARSE (LLM 시도했으나 폴백된 경우)
    절대 raise 0 — transport/파싱 예외는 흡수하고 폴백으로 무손실 반환."""
    topic = "" if topic is None else str(topic).strip()
    if not topic:
        return {"status": "EMPTY_TOPIC", "topic": "", "source": "none", "aspects": []}

    aspects, source, error = [], "fallback", None

    if llm_transport is not None:
        prompt = _build_prompt(topic, aspects_hint=aspects_hint, max_aspects=max_aspects)
        try:
            resp = llm_transport(prompt)
        except Exception:
            resp, error = None, "TRANSPORT_FAILED"
        if error is None:
            raw = _parse_aspects(resp)
            if raw is None:
                error = "PARSE_FAILED"
            else:
                aspects = _normalize_aspects(raw, topic)
                if aspects:
                    source = "llm"
                else:
                    error = "EMPTY_PARSE"

    # 폴백(LLM 미주입/실패/빈결과) — 항상 비어있지 않은 결과(무손실)
    if not aspects:
        aspects = _fallback_aspects(topic, aspects_hint=aspects_hint, max_aspects=max_aspects)
        source = "fallback"

    if max_aspects and max_aspects > 0:
        aspects = aspects[:max_aspects]

    out = {"status": "OK", "topic": topic, "source": source, "aspects": aspects}
    if error:
        out["llm_error"] = error
    return out


# ── selftest (transport mock · 실 네트워크 0 · 결정적) ───────────────────────
def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    # 고정 JSON 반환 mock transport(PoC qwen 출력 형태 모사) + 코드펜스 포함
    fixed_json = json.dumps([
        {"name": "동선·루트", "why": "이동 효율",
         "queries": ["발리 추천 동선", "bali itinerary"], "lang": "id",
         "local_sources": ["balitourismboard.org"]},
        {"name": "로컬 맛집", "why": "현지 음식",
         "queries": ["발리 현지 맛집", "bali local food"], "lang": "id",
         "local_sources": []},
        {"name": "숨은 명소", "why": "덜 알려진 장소",
         "queries": ["발리 숨은 명소"], "lang": "id", "local_sources": []},
    ], ensure_ascii=False)

    calls = {"n": 0, "prompts": []}

    def _t_ok(prompt):
        calls["n"] += 1
        calls["prompts"].append(prompt)
        return "```json\n" + fixed_json + "\n```"  # 코드펜스 포함(파서 견고성)

    # P1 — LLM 경로 구조/계약
    r = plan("발리 신혼여행", llm_transport=_t_ok)
    chk("P1a 반환 topic/aspects 키", "topic" in r and "aspects" in r)
    chk("P1b status OK·source llm", r["status"] == "OK" and r["source"] == "llm")
    chk("P1c aspects 비어있지 않음", len(r["aspects"]) > 0)
    chk("P1d 모든 aspect 5키(name/why/queries/lang/local_sources)",
        all(set(_ASPECT_KEYS) <= set(a.keys()) for a in r["aspects"]))
    chk("P1e queries 는 리스트·비어있지 않음",
        all(isinstance(a["queries"], list) and a["queries"] for a in r["aspects"]))
    chk("P1f local_sources 리스트", all(isinstance(a["local_sources"], list) for a in r["aspects"]))

    # P2 — 코드펜스 제거 후 파싱(견고성)
    chk("P2 코드펜스(```json)감싼 응답도 파싱", r["aspects"][0]["name"] == "동선·루트")

    # P3 — transport 호출 검증(spy·프롬프트에 topic 포함)
    chk("P3a transport 1회 호출", calls["n"] == 1)
    chk("P3b 프롬프트에 topic 포함", "발리 신혼여행" in calls["prompts"][0])

    # P4 — 필수키 보정(queries 누락 → topic+name 으로 생성)
    def _t_missing(prompt):
        return json.dumps([{"name": "예산", "why": "비용 계획"}], ensure_ascii=False)  # queries/lang 없음
    rm = plan("발리 신혼여행", llm_transport=_t_missing)
    a0 = rm["aspects"][0]
    chk("P4a queries 누락 → 생성(비어있지 않음)", len(a0["queries"]) > 0)
    chk("P4b 생성 query 에 topic 포함", "발리" in a0["queries"][0])
    chk("P4c lang 누락 → 추론(ko)", a0["lang"] == "ko")
    chk("P4d local_sources 누락 → []", a0["local_sources"] == [])

    # P5 — dict 래핑 응답({"aspects":[...]}) 파싱
    def _t_wrapped(prompt):
        return json.dumps({"aspects": [{"name": "교통", "queries": ["발리 교통"]}]}, ensure_ascii=False)
    rw = plan("발리", llm_transport=_t_wrapped)
    chk("P5 dict.aspects 래핑 파싱", rw["source"] == "llm" and rw["aspects"][0]["name"] == "교통")

    # P6 — 잡담 섞인 응답 → JSON 블록 추출
    def _t_chatty(prompt):
        return "여기 결과입니다:\n[{\"name\": \"날씨\", \"queries\": [\"발리 날씨\"]}]\n참고하세요."
    rc = plan("발리", llm_transport=_t_chatty)
    chk("P6 잡담 섞인 응답에서 JSON 블록 추출", rc["aspects"][0]["name"] == "날씨")

    # P7 — 폴백 경로(llm_transport=None) 무손실·source fallback
    rf = plan("발리 신혼여행")
    chk("P7a llm_transport=None → source fallback", rf["source"] == "fallback")
    chk("P7b 폴백 aspects 비어있지 않음(무손실)", len(rf["aspects"]) > 0)
    chk("P7c 폴백도 5키 계약 유지",
        all(set(_ASPECT_KEYS) <= set(a.keys()) for a in rf["aspects"]))
    chk("P7d 폴백 query 에 주제 토큰 결합(고정 리스트 아님)",
        all("발리" in " ".join(a["queries"]) for a in rf["aspects"]))

    # P8 — 폴백 결정성(같은 입력 2회 동일)
    chk("P8 폴백 결정성(2회 동일)", plan("발리 신혼여행") == plan("발리 신혼여행"))

    # P9 — 폴백이 주제별로 달라짐(정형화 회피 — 다른 topic → 다른 query)
    q_bali = " ".join(q for a in plan("발리")["aspects"] for q in a["queries"])
    q_jeju = " ".join(q for a in plan("제주도")["aspects"] for q in a["queries"])
    chk("P9 폴백 query 가 주제별로 다름", ("발리" in q_bali and "발리" not in q_jeju
                                            and "제주도" in q_jeju))

    # P10 — transport 예외 흡수(raise 0) → 폴백·llm_error 표기
    def _t_boom(prompt):
        raise RuntimeError("ollama down")
    rb = plan("발리", llm_transport=_t_boom)
    chk("P10a transport 예외 흡수·폴백",
        rb["status"] == "OK" and rb["source"] == "fallback" and len(rb["aspects"]) > 0)
    chk("P10b llm_error=TRANSPORT_FAILED", rb.get("llm_error") == "TRANSPORT_FAILED")

    # P11 — 파싱 불가 응답 → 폴백·PARSE_FAILED
    def _t_garbage(prompt):
        return "이건 JSON 이 전혀 아닙니다 그냥 텍스트"
    rg = plan("발리", llm_transport=_t_garbage)
    chk("P11a 파싱 불가 → 폴백", rg["source"] == "fallback" and len(rg["aspects"]) > 0)
    chk("P11b llm_error=PARSE_FAILED", rg.get("llm_error") == "PARSE_FAILED")

    # P12 — 빈 JSON 배열 → 폴백·EMPTY_PARSE
    def _t_empty(prompt):
        return "[]"
    re_ = plan("발리", llm_transport=_t_empty)
    chk("P12 빈 배열 → 폴백·EMPTY_PARSE",
        re_["source"] == "fallback" and re_.get("llm_error") == "EMPTY_PARSE")

    # P13 — EMPTY_TOPIC(None·공백)
    chk("P13a None topic → EMPTY_TOPIC", plan(None)["status"] == "EMPTY_TOPIC")
    chk("P13b 공백 topic → EMPTY_TOPIC·aspects []",
        plan("   ")["status"] == "EMPTY_TOPIC" and plan("   ")["aspects"] == [])

    # P14 — max_aspects cap(LLM·폴백 양쪽)
    chk("P14a LLM 경로 cap(<=2)", len(plan("발리", llm_transport=_t_ok, max_aspects=2)["aspects"]) <= 2)
    chk("P14b 폴백 경로 cap(<=2)", len(plan("발리", max_aspects=2)["aspects"]) <= 2)

    # P15 — aspects_hint: 프롬프트 반영 + 폴백 lens 채택
    def _t_echo_hint(prompt):
        calls["prompts"].append(prompt)
        return fixed_json
    plan("발리", llm_transport=_t_echo_hint, aspects_hint=["예산", "안전"])
    chk("P15a aspects_hint 프롬프트 반영", "예산" in calls["prompts"][-1] and "안전" in calls["prompts"][-1])
    rh = plan("발리", aspects_hint=["예산", "안전"])
    hint_join = " ".join(a["name"] for a in rh["aspects"])
    chk("P15b 폴백에 hint 관점 반영(예산/안전·주제 결합)",
        "예산" in hint_join and "안전" in hint_join and "발리" in hint_join)

    # P16 — name 중복 제거
    def _t_dup(prompt):
        return json.dumps([{"name": "맛집", "queries": ["a"]},
                           {"name": "맛집", "queries": ["b"]},
                           {"name": "명소", "queries": ["c"]}], ensure_ascii=False)
    rd = plan("발리", llm_transport=_t_dup, max_aspects=10)
    names = [a["name"] for a in rd["aspects"]]
    chk("P16 name 중복 제거", len(names) == len(set(n.lower() for n in names)))

    # P17 — 파서 단위(raise 0)
    chk("P17a _parse_aspects(None) → None", _parse_aspects(None) is None)
    chk("P17b _parse_aspects(int) → None(raise 0)", _parse_aspects(123) is None)
    chk("P17c list 직접 전달 파싱", _parse_aspects([{"name": "x"}]) == [{"name": "x"}])
    chk("P17d 단일 aspect dict → [dict]", _parse_aspects({"name": "y", "queries": []}) == [{"name": "y", "queries": []}])

    # P18 — default_ollama_transport 팩토리(callable 생성만·네트워크 0·호출 안 함)
    tr = default_ollama_transport(model="qwen2.5:32b-instruct-q4_K_M")
    chk("P18a default_ollama_transport → callable 반환", callable(tr))
    tr2 = default_ollama_transport(url="http://localhost:11434", timeout=5)
    chk("P18b 인자 커스터마이즈 가능(callable)", callable(tr2))

    # P19 — 출력 안정(LLM 경로 결정성·같은 mock 2회 동일)
    chk("P19 LLM 경로 결정성(2회 동일)",
        plan("발리", llm_transport=_t_ok) == plan("발리", llm_transport=_t_ok))

    # P20 — lang 보존(LLM 이 준 lang 우선·추론 덮어쓰지 않음)
    chk("P20 LLM 제공 lang(id) 보존", plan("발리", llm_transport=_t_ok)["aspects"][0]["lang"] == "id")

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # CLI: --topic '<주제>' [--max N] [--live]  (--live = 실 ollama 사용·네트워크)
    topic = None
    max_n = _DEFAULT_MAX_ASPECTS
    live = "--live" in sys.argv
    if "--topic" in sys.argv:
        i = sys.argv.index("--topic")
        if i + 1 < len(sys.argv):
            topic = sys.argv[i + 1]
    if "--max" in sys.argv:
        i = sys.argv.index("--max")
        if i + 1 < len(sys.argv):
            try:
                max_n = int(sys.argv[i + 1])
            except ValueError:
                # Invalid CLI limits retain the bounded default.
                pass
    if topic:
        tr = default_ollama_transport() if live else None
        print(json.dumps(plan(topic, llm_transport=tr, max_aspects=max_n),
                         ensure_ascii=False, indent=2))
    else:
        print("binggu_collection_planner — use --selftest, "
              "or --topic '<주제>' [--max N] [--live]")
        print("import: plan(topic, llm_transport=None, aspects_hint=None) -> {topic, aspects:[...]}")
