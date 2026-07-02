"""binggu_subtopic_decompose — 주제 세분화 자동화(항목 A·2차 라인 발견 앞단 보강).

"넓은 주제 하나 → 검색·수집에 바로 쓸 수 있는 세부 주제(facet) 목록".
예: "해외 신혼여행" → 항공/숙박/패키지/예약/현지음식점/현지명소/여행자후기 ...

binggu_discover(주제→소스 후보)의 **앞단**. discover 가 그물 하나로 넓게 긁는 대신,
세분화된 query 여러 개로 더 정밀·무손실하게 후보를 모으게 한다. 이 모듈은 query 생성까지만
(검색/파싱/적재 안 함). Phase3(topic_to_pack)가 각 subtopic.query 를 discover 에 흘려보냄.

설계(조사결과 계획 반영):
  - 메인 경로 = ③ 도메인 템플릿(결정적 골격·무손실·네트워크0) + ② 검색 빈출어 보강(주입 corpus).
    ① 클라우드 LLM 은 opt-in llm_runner 주입 경로로만(기본 None·selftest 미사용).
  - 무손실: 검색/LLM 0(실패·미주입)이어도 템플릿 facet 으로 **항상 비어있지 않은 결과**.
  - 결정성: 같은 입력 → 완전 동일 출력(템플릿 고정 순서 + 빈출어 안정정렬). 네트워크0 selftest.
  - 기존계약 보존: binggu_discover 의 provider 덕타이핑(.search)만 차용. import 강결합 회피
    (binggu_harvest 무거운 의존 안 끌어옴). _tokens 류는 로컬 재정의.
  - 절대 raise 0(parser_adapter 패턴) — 실패는 generic 템플릿 fail-open + status 필드.

Phase3 통합 진입점: decompose(topic, ...) → [{subtopic, rationale, query}].

v1.16 strangler Phase2: 순수 transform 정본(re stdlib 만·네트워크0). 공개 시그니처는
scripts/binggu_subtopic_decompose.py thin wrapper 가 byte-identical 재노출한다. 기존 호출처
(import binggu_subtopic_decompose as SUB — binggu_topic_to_pack)는 그대로 동작한다.
selftest(mock provider/corpus/transport·실 네트워크0)와 --use-search CLI(binggu_discover
scripts/ 의존)는 wrapper 에 잔류한다.
"""
import re

# ── STOPWORDS — 조사·일반어(빈출어 보강에서 노이즈 제거). 완전 차단 아님(1차 필터). ──
STOPWORDS = {
    # 한국어 조사·일반어
    "그리고", "그러나", "하지만", "또는", "또한", "위해", "대한", "관련", "통해", "있는",
    "있다", "한다", "하는", "되는", "이다", "에서", "으로", "에게", "까지", "부터",
    "보다", "처럼", "같은", "경우", "때문", "정도", "이런", "저런", "그런", "어떤",
    "무엇", "여러", "모든", "각종", "기타", "등의", "들의", "것은", "것을", "수가",
    # 영어 stopword
    "the", "and", "for", "with", "from", "this", "that", "are", "was", "were",
    "have", "has", "had", "not", "but", "you", "your", "our", "their", "its",
    "can", "will", "all", "any", "how", "what", "when", "where", "which", "who",
    "about", "into", "over", "than", "then", "they", "them", "these", "those",
}


# ── 도메인 템플릿 — facet = {label, query_suffix, rationale}. 결정적 골격(고정 순서). ──
#   aliases: topic 토큰과 매칭되면 해당 도메인 채택. 미스 → GENERIC 폴백(무손실).
DOMAIN_TEMPLATES = {
    "travel": {
        "aliases": ["여행", "신혼여행", "관광", "휴가", "trip", "travel", "tour",
                    "호텔", "항공", "패키지", "해외여행", "국내여행"],
        "facets": [
            {"label": "항공", "query_suffix": "항공권 비행기", "rationale": "이동수단(항공편·요금)"},
            {"label": "숙박", "query_suffix": "호텔 숙소 숙박", "rationale": "체류 숙박 정보"},
            {"label": "패키지", "query_suffix": "패키지 상품 일정", "rationale": "여행 상품·일정 구성"},
            {"label": "예약", "query_suffix": "예약 방법 비용", "rationale": "예약 절차·비용"},
            {"label": "현지음식점", "query_suffix": "현지 맛집 음식점 방문", "rationale": "현지 음식점(여행객 방문)"},
            {"label": "현지명소", "query_suffix": "관광지 명소 가볼만한곳", "rationale": "현지 명소·볼거리"},
            {"label": "여행자후기-음식", "query_suffix": "음식점 여행 후기 리뷰", "rationale": "여행객 음식점 후기"},
            {"label": "여행자후기-명소", "query_suffix": "명소 여행 후기 리뷰", "rationale": "여행객 명소 후기"},
        ],
    },
    "procurement": {
        "aliases": ["입찰", "조달", "나라장터", "공고", "낙찰", "투찰", "발주",
                    "용역", "물품", "g2b", "관급", "계약"],
        "facets": [
            {"label": "공고", "query_suffix": "공고 내용 개요", "rationale": "입찰 공고 본문"},
            {"label": "자격요건", "query_suffix": "참가 자격 요건", "rationale": "입찰 참가 자격"},
            {"label": "예정가격", "query_suffix": "예정가격 기초금액 추정가격", "rationale": "가격 기준선"},
            {"label": "낙찰사례", "query_suffix": "낙찰 결과 사례 낙찰가", "rationale": "과거 낙찰 사례"},
            {"label": "규격사양", "query_suffix": "규격 사양 과업지시서", "rationale": "요구 규격·과업"},
            {"label": "일정마감", "query_suffix": "입찰 일정 마감 개찰", "rationale": "일정·마감"},
            {"label": "경쟁사", "query_suffix": "경쟁 업체 참여사", "rationale": "경쟁 구도"},
            {"label": "리스크", "query_suffix": "유의사항 리스크 분쟁", "rationale": "위험·유의사항"},
        ],
    },
    "research": {
        "aliases": ["연구", "기술", "모델", "논문", "알고리즘", "research", "study",
                    "딥러닝", "머신러닝", "ai", "분석", "이론"],
        "facets": [
            {"label": "정의", "query_suffix": "정의 개념 개요", "rationale": "주제 정의·개념"},
            {"label": "선행연구", "query_suffix": "선행 연구 관련 논문", "rationale": "선행 연구"},
            {"label": "방법론", "query_suffix": "방법론 접근법 기법", "rationale": "연구 방법론"},
            {"label": "데이터셋", "query_suffix": "데이터셋 데이터 출처", "rationale": "데이터 출처"},
            {"label": "성능지표", "query_suffix": "성능 평가 지표 벤치마크", "rationale": "성능·평가 지표"},
            {"label": "한계", "query_suffix": "한계 단점 제약", "rationale": "한계·제약"},
            {"label": "응용", "query_suffix": "응용 사례 활용", "rationale": "응용·활용"},
            {"label": "비교", "query_suffix": "비교 대안 차이", "rationale": "대안 비교"},
        ],
    },
}

# GENERIC fallback — 미상 주제(어떤 도메인에도 안 걸림)도 무손실로 세분화.
GENERIC_FACETS = [
    {"label": "정의·개요", "query_suffix": "정의 개요 뜻", "rationale": "기본 정의·개요"},
    {"label": "현황·통계", "query_suffix": "현황 통계 데이터", "rationale": "현황·통계"},
    {"label": "방법·절차", "query_suffix": "방법 절차 과정", "rationale": "방법·절차"},
    {"label": "사례", "query_suffix": "사례 예시 후기", "rationale": "실제 사례"},
    {"label": "비용", "query_suffix": "비용 가격 요금", "rationale": "비용·가격"},
    {"label": "규제·법령", "query_suffix": "규제 법령 기준", "rationale": "규제·법령"},
    {"label": "리스크", "query_suffix": "리스크 주의사항 단점", "rationale": "위험·주의"},
    {"label": "비교·대안", "query_suffix": "비교 대안 차이", "rationale": "대안 비교"},
]


def _tokens(text):
    """binggu_discover 와 동일 정규식 — 로컬 재정의(강결합 회피)."""
    return set(re.findall(r"[0-9a-z가-힣]+", str(text or "").lower()))


def _token_list(text):
    """순서 보존 토큰 리스트(빈출어 카운트용·동일 정규식)."""
    return re.findall(r"[0-9a-z가-힣]+", str(text or "").lower())


def detect_domain(topic):
    """topic 토큰 vs 도메인 aliases 매칭. 미스면 'generic'.
    반환 (domain_key, matched_terms). 결정적(도메인 순회는 dict 삽입 순서)."""
    toks = _tokens(topic)
    for dkey, spec in DOMAIN_TEMPLATES.items():
        matched = [a for a in spec["aliases"] if a in toks]
        if matched:
            return dkey, matched
    return "generic", []


def _make_query(topic, suffix):
    """topic + suffix 결합(결정적). suffix 없으면 topic 만."""
    topic = str(topic or "").strip()
    suffix = str(suffix or "").strip()
    return (topic + " " + suffix).strip() if suffix else topic


def template_subtopics(topic, domain):
    """도메인 템플릿 facet 전개(고정 순서·무손실). [{subtopic, rationale, query}]."""
    facets = (DOMAIN_TEMPLATES.get(domain, {}) or {}).get("facets") if domain != "generic" else None
    if not facets:
        facets = GENERIC_FACETS
    topic = str(topic or "").strip()
    out = []
    for f in facets:
        label = f["label"]
        out.append({
            "subtopic": (topic + " " + label).strip() if topic else label,
            "rationale": "템플릿(%s): %s" % (domain, f["rationale"]),
            "query": _make_query(topic, f["query_suffix"]),
        })
    return out


def frequent_terms(corpus, topic, top_n=5):
    """corpus(제목+snippet 문자열 리스트)에서 빈출 정보토큰 추출.
    STOPWORDS·topic 토큰·1글자 제외 → (count desc, term asc) 안정정렬. [(term, count)]."""
    topic_toks = _tokens(topic)
    counts = {}
    for text in (corpus or []):
        for tok in _token_list(text):
            if len(tok) < 2:
                continue
            if tok in STOPWORDS or tok in topic_toks:
                continue
            counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:top_n]


def _search_corpus(topic, provider, limit=10):
    """provider.search 결과의 title/snippet 수집 → 문자열 리스트. 예외 흡수(→[])."""
    if provider is None:
        return []
    try:
        hits = provider.search(topic, limit=limit) or []
    except TypeError:
        # provider.search(query) 1-인자 시그니처 호환
        try:
            hits = provider.search(topic) or []
        except Exception:
            return []
    except Exception:
        return []
    corpus = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        t = h.get("title") or ""
        s = h.get("snippet") or ""
        joined = (str(t) + " " + str(s)).strip()
        if joined:
            corpus.append(joined)
    return corpus


def _normalize_llm_items(raw, topic):
    """LLM raw items([str] 또는 [{subtopic/label/query,...}])을 출력계약으로 정규화.
    llm_decompose·llm_transport_decompose 공용(중복 제거). 절대 raise 0(방어적 순회)."""
    topic = str(topic or "").strip()
    out = []
    for item in (raw or []):
        if isinstance(item, dict):
            label = item.get("subtopic") or item.get("label") or item.get("query")
            if not label:
                continue
            label = str(label).strip()
            rationale = str(item.get("rationale") or "LLM 제안").strip()
            query = str(item.get("query") or _make_query(topic, label)).strip()
        else:
            label = str(item).strip()
            if not label:
                continue
            rationale = "LLM 제안"
            query = _make_query(topic, label)
        out.append({"subtopic": label, "rationale": rationale, "query": query})
    return out


def llm_decompose(topic, llm_runner):
    """opt-in LLM 세분화(고수준) — llm_runner(topic)->[items] 주입 결과 정규화.
    기본 미호출(llm_runner=None). runner 반환은 [str] 또는 [{subtopic/label,...}] 허용.
    예외/형식오류 흡수(→[])."""
    if llm_runner is None:
        return []
    try:
        raw = llm_runner(topic) or []
    except Exception:
        return []
    return _normalize_llm_items(raw, topic)


# ── A2 — 세분화 LLM 위임 경로(저수준 transport 규약·binggu_cloud_ingest_wire 정합) ──
#   transport(payload)->response. 네트워크는 transport 책임(selftest=mock·실 urllib 는
#   default_llm_transport 에만 격리). 미주입(None)이면 룰기반 폴백 유지(기존 동작 100% 불변).
def _build_llm_payload(topic, max_subtopics=8):
    """결정적 LLM 요청 payload 빌드(순수함수·네트워크0). transport 가 이 dict 를 전송."""
    topic = str(topic or "").strip()
    n = max_subtopics if (isinstance(max_subtopics, int) and max_subtopics > 0) else 8
    return {
        "task": "subtopic_decompose",
        "topic": topic,
        "max_subtopics": n,
        "instruction": (
            "주어진 topic 을 검색·수집에 바로 쓸 수 있는 세부 주제(facet)로 분해하라. "
            "결과는 JSON 배열 [{\"subtopic\":..., \"rationale\":..., \"query\":...}] 형식. "
            "최대 %d개. 각 query 는 topic 을 포함한 검색 질의여야 한다." % n
        ),
    }


def _parse_llm_response(resp, topic):
    """LLM 응답 관용 파서(raise 0) — 다양한 provider 형식을 raw items 리스트로.
    (a) list → 그대로 / (b) dict 의 subtopics|items|results 키 → 그 값 /
    (c) OpenAI 스타일 {choices:[{message:{content}}]} → content 추출 후 JSON|줄단위 /
    (d) str → JSON|줄단위 / 인식불가 → []. 모든 파싱 try/except 로 예외 전파 0."""
    import json as _json

    def _from_text(text):
        text = str(text or "").strip()
        if not text:
            return []
        try:
            parsed = _json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for k in ("subtopics", "items", "results"):
                v = parsed.get(k)
                if isinstance(v, list):
                    return v
            return []
        # JSON 아님 → 줄단위 텍스트 폴백
        lines = [ln.strip(" \t-*•") for ln in text.splitlines()]
        return [ln for ln in lines if ln]

    if resp is None:
        return []
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in ("subtopics", "items", "results"):
            v = resp.get(k)
            if isinstance(v, list):
                return v
        # OpenAI 스타일 choices[].message.content
        try:
            choices = resp.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if content is not None:
                    return _from_text(content)
        except Exception:
            return []
        return []
    if isinstance(resp, str):
        return _from_text(resp)
    return []


def llm_transport_decompose(topic, transport, max_subtopics=8):
    """opt-in LLM 세분화(저수준 transport) — payload 빌드→transport 호출→파싱→정규화.
    transport(payload)->response. 미주입(None)이면 []. 절대 raise 0(transport/파싱 예외 흡수)."""
    if transport is None:
        return []
    payload = _build_llm_payload(topic, max_subtopics=max_subtopics)
    try:
        resp = transport(payload)
    except Exception:
        return []
    raw = _parse_llm_response(resp, topic)
    return _normalize_llm_items(raw, topic)


def default_llm_transport(url, token, *, model=None, timeout=30):
    """실 네트워크 LLM transport 생성기(클로저) — **selftest 미사용·실 endpoint 전용**.
    url/token 은 호출측이 env/config 에서 읽어 주입(하드코딩 0). 토큰 평문 로그 0.
    반환 transport(payload)->dict(JSON 응답). 네트워크 예외는 호출자(llm_transport_decompose)가 흡수."""
    import json as _json
    import urllib.request as _urlreq

    def _transport(payload):
        body = dict(payload or {})
        if model:
            body["model"] = model
        data = _json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + str(token)
        req = _urlreq.Request(url, data=data, headers=headers, method="POST")
        with _urlreq.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            return _json.loads(raw)
        except Exception:
            return raw

    return _transport


def _dedup(items):
    """토큰겹침 dedup — subtopic 토큰셋이 이미 본 것과 동일하면 1건으로 축약(첫 등장 우선)."""
    seen = []
    out = []
    for it in items:
        sig = frozenset(_tokens(it.get("subtopic")))
        if not sig:
            # 토큰 0(빈 subtopic) — query 로 식별
            sig = frozenset(_tokens(it.get("query")))
        if sig and sig in seen:
            continue
        if sig:
            seen.append(sig)
        out.append(it)
    return out


def decompose_detail(topic, provider=None, corpus=None, max_subtopics=8,
                     use_search=False, llm_runner=None, limit=10, transport=None):
    """세분화 오케스트레이션(템플릿 → 검색 빈출어 보강 → LLM 보강 → dedup → cap).

    절대 raise 0 — 실패는 status 필드로. 무손실 — 템플릿 facet 항상 포함.
    반환 {status, topic, domain, source, subtopics}.
      status: OK / EMPTY_TOPIC
      source: 어떤 레이어가 기여했는지(template / +search / +llm)
    LLM 경로 둘 다 opt-in(기본 None):
      llm_runner = 고수준 콜백 llm_runner(topic)->[items]
      transport  = 저수준 콜백 transport(payload)->response (binggu_cloud_ingest_wire 규약)
    """
    topic = "" if topic is None else str(topic).strip()
    if not topic:
        return {"status": "EMPTY_TOPIC", "topic": "", "domain": "generic",
                "source": [], "subtopics": []}

    domain, _matched = detect_domain(topic)
    source = ["template"]

    # ① 템플릿 골격(결정적·무손실·항상 비어있지 않음)
    items = template_subtopics(topic, domain)

    # ② 검색 빈출어 보강(주입 corpus 우선 → use_search 시 provider 수확). 네트워크는 provider 책임.
    eff_corpus = list(corpus or [])
    if use_search and provider is not None:
        eff_corpus += _search_corpus(topic, provider, limit=limit)
    if eff_corpus:
        freq = frequent_terms(eff_corpus, topic, top_n=5)
        if freq:
            source.append("search")
        for term, cnt in freq:
            items.append({
                "subtopic": (topic + " " + term).strip(),
                "rationale": "검색 빈출어 보강(%d회)" % cnt,
                "query": _make_query(topic, term),
            })

    # ③ LLM 보강(opt-in·기본 미호출·고수준 콜백)
    llm_items = llm_decompose(topic, llm_runner)
    if llm_items:
        source.append("llm")
        items.extend(llm_items)

    # ③' LLM transport 보강(opt-in·기본 미호출·저수준 payload 콜백·표준 transport 규약)
    if transport is not None:
        t_items = llm_transport_decompose(topic, transport, max_subtopics=max_subtopics)
        if t_items:
            if "llm" not in source:
                source.append("llm")
            items.extend(t_items)

    # ④ dedup → cap
    items = _dedup(items)
    if max_subtopics and max_subtopics > 0:
        items = items[:max_subtopics]

    return {"status": "OK", "topic": topic, "domain": domain,
            "source": source, "subtopics": items}


def decompose(topic, provider=None, corpus=None, max_subtopics=8,
              use_search=False, llm_runner=None, limit=10, transport=None):
    """계약 진입점(Phase3 통합용) — [{subtopic, rationale, query}] 반환.
    빈/None topic → [] (raise 0). decompose_detail()['subtopics'] 그대로.
    transport: opt-in 저수준 LLM 콜백(기본 None·미주입시 룰기반·기존 동작 불변)."""
    return decompose_detail(topic, provider=provider, corpus=corpus,
                            max_subtopics=max_subtopics, use_search=use_search,
                            llm_runner=llm_runner, limit=limit,
                            transport=transport)["subtopics"]
