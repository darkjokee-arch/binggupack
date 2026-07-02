# -*- coding: utf-8 -*-
"""binggu_subtopic_decompose — 주제 세분화 자동화 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 순수 transform 정본(STOPWORDS/DOMAIN_TEMPLATES/GENERIC_FACETS ·
detect_domain/template_subtopics/frequent_terms · llm_decompose/llm_transport_decompose/
default_llm_transport · decompose/decompose_detail + 헬퍼)은 binggupack.pack.subtopic_decompose
로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin wrapper 다. 기존 호출처
(import binggu_subtopic_decompose as SUB — binggu_topic_to_pack)는 그대로 동작한다.
순수 함수(re stdlib 만·네트워크0·부수효과0).

selftest 는 provider/corpus/llm/transport 를 전부 mock 하는 자기완결형(실 네트워크0)이라
정본 심볼을 재노출해 그대로 검증한다. --topic --use-search CLI 만 binggu_discover(scripts/
형제 모듈) 의존이라 이 wrapper 의 sys.path 부트스트랩으로 해소한다.

CLI: python scripts/binggu_subtopic_decompose.py [--selftest] | --topic '<주제>' [--use-search] [--max N]
import: decompose(topic) -> [{subtopic, rationale, query}]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import(binggu_discover) 경로

from binggupack.pack.subtopic_decompose import *  # noqa: E402,F401,F403
from binggupack.pack.subtopic_decompose import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    STOPWORDS,
    DOMAIN_TEMPLATES,
    GENERIC_FACETS,
    _tokens,
    _token_list,
    detect_domain,
    _make_query,
    template_subtopics,
    frequent_terms,
    _search_corpus,
    _normalize_llm_items,
    llm_decompose,
    _build_llm_payload,
    _parse_llm_response,
    llm_transport_decompose,
    default_llm_transport,
    _dedup,
    decompose_detail,
    decompose,
)


# ── selftest (provider/corpus/llm 전부 mock — 실 네트워크 0·결정적) ──────
def _mock_provider(hits):
    class _P:
        name = "mock"
        def search(self, query, limit=10):
            return list(hits)[:limit]
    return _P()


def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    # S1 — 알려진 도메인(여행) 비어있지 않음
    r = decompose("해외 신혼여행")
    chk("S1 여행 도메인 비어있지 않음", len(r) > 0)

    # S2 — 출력 계약 3키
    chk("S2 모든 항목 subtopic/rationale/query 3키",
        all(set(["subtopic", "rationale", "query"]) <= set(x.keys()) for x in r))

    # S3 — 결정성(같은 입력 2회 동일)
    r2 = decompose("해외 신혼여행")
    chk("S3 결정성 — 2회 호출 완전 동일", r == r2)

    # S4 — travel facet(숙박/항공/예약/현지) 재현(수동8 회귀)
    labels = " ".join(x["subtopic"] for x in r)
    rat = " ".join(x["rationale"] for x in r)
    chk("S4 travel 숙박/항공/예약/현지 facet 포함",
        all(k in (labels + rat) for k in ("숙박", "항공", "예약", "현지")))

    # S5 — 미상 주제 → generic 폴백(무손실)
    det_g = decompose_detail("zxqv 임의주제 12345")
    chk("S5 미상 주제 generic 폴백·비어있지 않음",
        det_g["domain"] == "generic" and len(det_g["subtopics"]) > 0)

    # S6 — query 에 topic 문자열 포함(검색 주입 가능)
    chk("S6 query 에 topic 포함",
        all("신혼여행" in x["query"] for x in decompose("신혼여행")))

    # S7 — max_subtopics cap
    capped = decompose("입찰 조달 공고", max_subtopics=3)
    chk("S7 max_subtopics cap 준수(<=3)", len(capped) <= 3)

    # S8 — 검색 보강(주입 corpus 빈출어 병합·네트워크0)
    corpus = ["발리 신혼여행 리조트 추천", "발리 리조트 가격 발리 후기",
              "발리 신혼여행 일정 발리"]
    det_s = decompose_detail("신혼여행", corpus=corpus, max_subtopics=20)
    sub_join = " ".join(x["subtopic"] for x in det_s["subtopics"])
    chk("S8 빈출어(발리) subtopic 병합", "발리" in sub_join and "search" in det_s["source"])

    # S9 — dedup(중복 facet/토큰겹침 1건)
    dup_corpus = ["호텔 숙박 호텔", "숙박 호텔 숙박"]  # '숙박' 은 travel facet 과 겹침
    det_d = decompose_detail("해외여행", corpus=dup_corpus, max_subtopics=30)
    sigs = [frozenset(_tokens(x["subtopic"])) for x in det_d["subtopics"]]
    chk("S9 dedup — 토큰셋 중복 0", len(sigs) == len(set(sigs)))

    # S10 — 절대 raise 0(None·빈 문자열)
    chk("S10a None topic → [] (raise 0)", decompose(None) == [])
    chk("S10b 빈 문자열 topic → []", decompose("   ") == [])
    chk("S10c decompose_detail None → EMPTY_TOPIC",
        decompose_detail(None)["status"] == "EMPTY_TOPIC")

    # S11 — provider.search 예외 흡수 → 템플릿만으로 정상(폴백)
    class _Boom:
        def search(self, q, limit=10):
            raise RuntimeError("provider down")
    det_b = decompose_detail("입찰 공고", provider=_Boom(), use_search=True)
    chk("S11 provider 예외 흡수·템플릿 결과 유지",
        det_b["status"] == "OK" and len(det_b["subtopics"]) > 0)

    # S12 — llm_runner 주입 병합 / None 이면 미호출
    def _llm(topic):
        return ["맞춤 신혼여행 예산", {"subtopic": "신혼여행 보험", "rationale": "안전",
                                       "query": "신혼여행 여행자보험"}]
    det_l = decompose_detail("신혼여행", llm_runner=_llm, max_subtopics=30)
    sub_l = " ".join(x["subtopic"] for x in det_l["subtopics"])
    chk("S12a llm 병합(예산/보험)", "예산" in sub_l and "보험" in sub_l and "llm" in det_l["source"])
    det_n = decompose_detail("신혼여행", llm_runner=None)
    chk("S12b llm_runner=None → llm 미호출", "llm" not in det_n["source"])

    # S13 — frequent_terms STOPWORDS·topic 토큰·1글자 제외
    ft = dict(frequent_terms(["발리 그리고 a 신혼여행 발리 위해 발리"], "신혼여행", top_n=10))
    chk("S13a 빈출어 발리 카운트", ft.get("발리") == 3)
    chk("S13b STOPWORD '그리고'/'위해' 제외", "그리고" not in ft and "위해" not in ft)
    chk("S13c 1글자 'a' 제외", "a" not in ft)
    chk("S13d topic 토큰 '신혼여행' 제외", "신혼여행" not in ft)

    # S14 — decompose_detail status OK + 도메인 정확 탐지
    chk("S14a travel 탐지", decompose_detail("해외 신혼여행")["domain"] == "travel")
    chk("S14b procurement 탐지", decompose_detail("나라장터 입찰 공고")["domain"] == "procurement")
    chk("S14c research 탐지", decompose_detail("딥러닝 모델 연구")["domain"] == "research")
    chk("S14d generic 폴백", decompose_detail("점심 메뉴 고민")["domain"] == "generic")

    # S15 — provider use_search 경로(mock·네트워크0)로 빈출어 보강
    prov = _mock_provider([
        {"url": "https://x", "title": "코타키나발루 신혼여행", "snippet": "코타키나발루 리조트 코타키나발루"},
    ])
    det_p = decompose_detail("신혼여행", provider=prov, use_search=True, max_subtopics=20)
    chk("S15 provider use_search 빈출어(코타키나발루) 병합",
        "코타키나발루" in " ".join(x["subtopic"] for x in det_p["subtopics"]))

    # ── A2 — 저수준 transport LLM 경로(mock transport·실 네트워크 0) ──
    import json as _json

    # S16 — transport 주입 병합(OpenAI 스타일 응답·spy 호출횟수)
    calls = {"n": 0, "payloads": []}

    def _t_openai(payload):
        calls["n"] += 1
        calls["payloads"].append(payload)
        content = _json.dumps([
            {"subtopic": "신혼여행 환율", "rationale": "예산", "query": "신혼여행 환율 정보"},
            {"subtopic": "신혼여행 통신", "rationale": "현지", "query": "신혼여행 현지 유심"},
        ], ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}]}

    det_t = decompose_detail("신혼여행", transport=_t_openai, max_subtopics=30)
    sub_t = " ".join(x["subtopic"] for x in det_t["subtopics"])
    chk("S16a transport 항목(환율/통신) 병합", "환율" in sub_t and "통신" in sub_t)
    chk("S16b source 에 'llm' 포함", "llm" in det_t["source"])
    chk("S16c transport 1회 호출(spy)", calls["n"] == 1)
    chk("S16d payload task=subtopic_decompose·topic 전달",
        calls["payloads"][0].get("task") == "subtopic_decompose"
        and calls["payloads"][0].get("topic") == "신혼여행")

    # S17 — transport=None → transport 경로 미호출(룰기반만)
    sentinel = {"n": 0}

    def _t_spy(payload):
        sentinel["n"] += 1
        return []

    det_n2 = decompose_detail("신혼여행", transport=None, max_subtopics=30)
    chk("S17a transport=None → spy 미호출", sentinel["n"] == 0)
    chk("S17b transport=None → 룰기반 source(llm 기여 없음·llm_runner도 None)",
        "llm" not in det_n2["source"])

    # S18 — transport 예외 흡수(raise 0·폴백)
    def _t_boom(payload):
        raise RuntimeError("llm down")

    det_tb = decompose_detail("입찰 공고", transport=_t_boom, max_subtopics=20)
    chk("S18 transport 예외 흡수·템플릿 유지",
        det_tb["status"] == "OK" and len(det_tb["subtopics"]) > 0
        and "llm" not in det_tb["source"])

    # S19 — 응답 형식 변형 관용 파싱(예외 0)
    chk("S19a list[str] 파싱", _parse_llm_response(["a", "b"], "t") == ["a", "b"])
    chk("S19b dict subtopics 키 파싱",
        _parse_llm_response({"subtopics": ["x"]}, "t") == ["x"])
    chk("S19c JSON 문자열 파싱",
        _parse_llm_response('[{"subtopic":"y"}]', "t") == [{"subtopic": "y"}])
    chk("S19d 인식불가(int) → []", _parse_llm_response(123, "t") == [])
    chk("S19e None → []", _parse_llm_response(None, "t") == [])
    chk("S19f OpenAI content 줄단위 폴백(비JSON)",
        _parse_llm_response({"choices": [{"message": {"content": "알파\n베타"}}]}, "t")
        == ["알파", "베타"])

    # S20 — 출력 계약 보존(transport 항목도 3키·query 에 topic·cap 준수)
    def _t_strlist(payload):
        return ["견적", "환전"]  # str 항목 → query 자동생성

    det_c = decompose_detail("신혼여행", transport=_t_strlist, max_subtopics=4)
    chk("S20a transport 항목 3키 보존",
        all(set(["subtopic", "rationale", "query"]) <= set(x.keys())
            for x in det_c["subtopics"]))
    chk("S20b transport 항목 query 에 topic 포함",
        all("신혼여행" in x["query"] for x in det_c["subtopics"]))
    chk("S20c max_subtopics cap 준수", len(det_c["subtopics"]) <= 4)

    # S21 — 결정성(동일 mock transport·동일 입력 2회 완전 동일)
    d1 = decompose("신혼여행", transport=_t_openai, max_subtopics=30)
    d2 = decompose("신혼여행", transport=_t_openai, max_subtopics=30)
    chk("S21 transport 경로 결정성(2회 동일)", d1 == d2)

    # S22 — _build_llm_payload 결정적·max cap 반영
    p1 = _build_llm_payload("주제", max_subtopics=5)
    p2 = _build_llm_payload("주제", max_subtopics=5)
    chk("S22 payload 결정적·max_subtopics 반영",
        p1 == p2 and p1["max_subtopics"] == 5)

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    import json
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # CLI: --topic '<주제>' [--use-search] [--max N]
    topic = None
    use_search = "--use-search" in sys.argv
    max_n = 8
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
                pass
    if topic:
        prov = None
        if use_search:
            try:
                import binggu_discover as _DISC
                prov = _DISC.default_provider()
            except Exception:
                prov = None
        res = decompose_detail(topic, provider=prov, use_search=use_search, max_subtopics=max_n)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("binggu_subtopic_decompose — use --selftest, or --topic '<주제>' [--use-search] [--max N]")
        print("import: decompose(topic) -> [{subtopic, rationale, query}]")
