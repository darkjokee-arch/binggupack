"""binggu_local_collect — 항목 C: "주제 → 분류설계(aspect) → aspect 별 수집 → aspect 별 pack".

빙구팩 2차 라인의 LLM 동적 분류 경로 오케스트레이터. topic_to_pack 의 단일 pack 과 달리
**aspect(관점) 별로 별도 pack** 을 만든다(planner 가 주제마다 동적 설계한 분류 체계 기준).

  plan(주제→aspects)  →  각 aspect: discover(aspect.queries, lang) → promote → harvest → pack

핵심 흐름(aspect 마다 독립):
  ① binggu_collection_planner.plan(topic, llm_transport) 로 분류 설계(aspects).
     LLM 주입 시 동적 설계가 주 경로, 미주입/실패 시 무손실 룰 폴백(planner 책임).
  ② 각 aspect.queries 로 binggu_discover.discover(provider, lang=aspect.lang) → promote
     (add_source 게이트 통과분만) → **이번 aspect 승급 source_id 만** harvest(오염격리).
  ③ aspect 별 evidence → binggu_pack_factory.build_pack(완료 기준=validate_pack).

오염격리(필수):
  - aspect 마다 격리 sub-home(<home>/aspects/<i>_<slug>) → discover_candidates/harvest_sources
    물리 분리. 다른 aspect/topic 의 소스가 이 aspect pack 에 섞이지 않음.
  - 그 위에 promoted_sids 필터 — 이번 aspect 에서 승급한 source_id 만 harvest(이중 안전).

정형화 금지(사장님 핵심): 분류 관점은 planner(LLM 동적 설계)가 결정. 이 모듈은 코드에
  관점을 하드코딩하지 않고 plan 결과(aspects)를 그대로 흘려보낸다.

topic_to_pack.py 미접촉(자체 조립). 운영 store/ledger 미접촉(temp home·out_dir 만).
실 네트워크는 provider/fetch_runner 주입 — selftest 는 전부 mock(네트워크 0).

진입점: collect(topic, llm_transport=None, provider=None, fetch_runner=None,
                home=None, out_dir=None) -> {topic, source, aspects:[{name, pack,
                doc_count, queries, lang, ...}], plan}
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_collection_planner as PLAN   # noqa: E402  (항목 A — 주제→aspects 동적 설계)
import binggu_discover as DISC             # noqa: E402  (항목 B — 발견·lang 전파)
import binggu_harvest as HV                # noqa: E402  (수집·source_id·격리)
import binggu_pack_factory as FAC          # noqa: E402  (evidence→pack·완료기준 validate)


def _collect_one_aspect(topic, aspect, idx, base_home, out_dir, provider,
                        fetch_runner, min_score, max_sources):
    """단일 aspect 수집 — 격리 sub-home 에서 discover→promote→harvest→pack.
    promoted_sids 필터로 이번 aspect 승급분만 수확(다른 aspect/topic 소스 미혼입)."""
    name = (aspect.get("name") or ("%s aspect %d" % (topic, idx))).strip()
    lang = aspect.get("lang")
    queries = [q for q in (aspect.get("queries") or []) if str(q or "").strip()]

    # 격리 sub-home — aspect 마다 discover_candidates/harvest_sources 물리 분리(오염격리 ①).
    sub_home = os.path.join(base_home, "aspects", "%02d_%s" % (idx, FAC._slug(name)))
    os.makedirs(sub_home, exist_ok=True)
    sp = HV.sources_path(sub_home)
    dp = DISC.discover_path(sub_home)

    # ① 발견 — aspect.queries 각각 discover(merge=True 로 이 aspect 후보 누적). lang 전파(SearXNG 등).
    for q in queries:
        DISC.discover(q, provider=provider, home=sub_home, lang=lang)

    # ② 승급 — min_score 이상 후보를 add_source 게이트 통과시켜서만. 이번 aspect promoted_sids 수집.
    promoted, promoted_sids = [], set()
    for c in sorted(DISC.load_discoveries(dp), key=lambda x: x.get("score", 0), reverse=True):
        if c.get("score", 0) < min_score:
            continue
        pr = DISC.promote_discovery(c["source_id"], sources_path_=sp, discover_path_=dp)
        if pr.get("status") == "OK":
            promoted.append(pr["promoted_url"])
            promoted_sids.add(c["source_id"])
        if len(promoted) >= max_sources:
            break

    # ③ 수확 — 이번 aspect 승급 source_id 만(오염격리 ② promoted_sids 필터).
    documents, skipped = [], []
    for s in HV.load_sources(sp):
        if s.get("source_id") not in promoted_sids:
            continue
        one = HV.harvest_one(s, runner=fetch_runner, sources_path_=sp, home=sub_home)
        if one["status"] == "OK":
            documents.append({"nodes": one["nodes"],
                              "evidence_index": one["evidence_index"],
                              "evidence_chunks": one.get("evidence_chunks", []),
                              "parse_artifacts": one.get("parse_artifacts", [])})
        else:
            etype = ((one.get("parse_error") or {}).get("type")
                     or one.get("reason") or one["status"])
            skipped.append({"source_id": s.get("source_id"), "status": one["status"],
                            "error": etype, "detail": one.get("detail")})

    # ④ 팩 — aspect 별 별도 pack(완료 기준 = validate_pack PASS·STOP 아님).
    aspect_topic = "%s :: %s" % (topic, name)
    sub_out = os.path.join(out_dir, "aspect_%02d" % idx) if out_dir else None
    res = FAC.build_pack(aspect_topic, documents, out_dir=sub_out)

    return {"name": name, "pack": res.get("pack"), "doc_count": len(documents),
            "queries": queries, "lang": lang, "why": aspect.get("why", ""),
            "status": res["status"], "verdict": res["verdict"]["verdict"],
            "promoted": len(promoted), "skipped": skipped,
            "counts": res["counts"], "written": res.get("written")}


def collect(topic, llm_transport=None, provider=None, fetch_runner=None, home=None,
            out_dir=None, min_score=0.0, max_sources=10, aspects_hint=None, max_aspects=5):
    """주제 → 분류설계(aspects) → aspect 별 독립 수집/pack.

    ① planner.plan(topic, llm_transport) 로 aspects 설계(LLM 주 경로·룰 폴백 무손실).
    ② 각 aspect: discover(aspect.queries, lang) → promote → harvest(promoted_sids 격리) → pack.
    ③ 반환 {status, topic, source, aspects:[{name, pack, doc_count, queries, lang, ...}], plan}.

    EMPTY_TOPIC(None/공백) → status=EMPTY_TOPIC·aspects []. 실 네트워크는 provider/fetch_runner 주입.
    운영 store/ledger 미접촉(home/out_dir 만 사용 — 호출자가 temp 주면 전부 temp 하위)."""
    base_home = home or HV._home()
    plan = PLAN.plan(topic, llm_transport=llm_transport,
                     aspects_hint=aspects_hint, max_aspects=max_aspects)

    if plan.get("status") != "OK" or not plan.get("aspects"):
        return {"status": plan.get("status", "EMPTY"), "topic": plan.get("topic", topic),
                "source": plan.get("source", "none"), "aspects": [], "plan": plan}

    aspects_out = []
    for i, aspect in enumerate(plan["aspects"]):
        aspects_out.append(_collect_one_aspect(
            plan["topic"], aspect, i, base_home, out_dir, provider,
            fetch_runner, min_score, max_sources))

    return {"status": "OK", "topic": plan["topic"], "source": plan.get("source"),
            "aspects": aspects_out, "plan": plan,
            "n_aspects": len(aspects_out),
            "total_docs": sum(a["doc_count"] for a in aspects_out)}


# ── selftest (planner transport + provider/fetch 전부 mock · 실 네트워크 0 · temp) ──
def _aspect_texts(pack):
    """pack 의 evidence_chunk 본문 전체 결합(격리 검증용)."""
    return " ".join(str(c.get("text") or "") for c in (pack or {}).get("evidence_chunk", []))


def _selftest():
    import json
    import tempfile
    os.environ["BINGGU_PARSER_CLI_OFF"] = "1"   # 결정성 — parser 실 CLI 0(txt 는 1:1 경로)
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    # ── 1) mock planner transport — aspect 2개(동선/맛집), 각 distinct query ──
    plan_json = json.dumps([
        {"name": "동선", "why": "이동 효율", "queries": ["발리 동선 코스"],
         "lang": "id", "local_sources": []},
        {"name": "맛집", "why": "현지 음식", "queries": ["발리 맛집 로컬"],
         "lang": "id", "local_sources": []},
    ], ensure_ascii=False)

    plan_calls = {"n": 0, "prompts": []}

    def _plan_t(prompt):
        plan_calls["n"] += 1
        plan_calls["prompts"].append(prompt)
        return plan_json

    # ── 2) query 인지 mock provider(aspect 별 distinct url) + lang 캡처 ──
    ROUTE = "https://route.example.com/a.txt"
    FOOD = "https://food.example.com/b.txt"
    langs_seen = []

    def _mk_provider():
        p = DISC.SearchProvider()
        p.name = "mock"

        def _search(query, limit=10, lang=None):
            langs_seen.append(lang)
            if "동선" in query:
                return [{"url": ROUTE, "title": "발리 동선 가이드", "snippet": "추천 코스"}]
            if "맛집" in query:
                return [{"url": FOOD, "title": "발리 맛집", "snippet": "현지 음식"}]
            return [{"url": "https://generic.example.com/g.txt",
                     "title": "일반", "snippet": "자료"}]
        p.search = _search
        return p

    # ── 3) mock fetch runner — url 별 distinct 마커 본문(txt=1:1 경로) ──
    def _fetch(url, timeout=30):
        if url == ROUTE:
            body = "ROUTEMARK 발리 추천 동선 코스 본문입니다.\n\n둘째 문단 동선 보존."
        elif url == FOOD:
            body = "FOODMARK 발리 현지 맛집 본문입니다.\n\n둘째 문단 맛집 보존."
        else:
            body = "GENERICMARK 일반 자료 본문입니다.\n\n둘째 문단."
        raw = body.encode("utf-8")
        return {"ok": True, "text": body, "url": url, "final_url": url,
                "raw_bytes": raw, "content_type": "text/plain"}

    home = os.path.join(tempfile.mkdtemp(prefix="lc_"), ".binggupack")
    os.makedirs(home)
    out = tempfile.mkdtemp(prefix="lc_pack_")

    r = collect("발리 신혼여행", llm_transport=_plan_t, provider=_mk_provider(),
                fetch_runner=_fetch, home=home, out_dir=out)

    # C1 — 구조/계약
    chk("C1 status OK", r["status"] == "OK")
    chk("C1b planner transport 1회+ 호출(LLM 주 경로)", plan_calls["n"] >= 1)
    chk("C1c source=llm(LLM 설계 채택)", r["source"] == "llm")
    chk("C1d aspects 2개(동선/맛집)", len(r["aspects"]) == 2)
    chk("C1e 반환 plan 동봉", isinstance(r["plan"], dict) and r["plan"]["status"] == "OK")
    chk("C1f 각 aspect 계약키(name/pack/doc_count/queries/lang)",
        all({"name", "pack", "doc_count", "queries", "lang"} <= set(a) for a in r["aspects"]))

    a_route = next(a for a in r["aspects"] if a["name"] == "동선")
    a_food = next(a for a in r["aspects"] if a["name"] == "맛집")

    # C2 — aspect 별 수집 성공
    chk("C2 동선 aspect 수확 1건", a_route["doc_count"] == 1)
    chk("C2b 맛집 aspect 수확 1건", a_food["doc_count"] == 1)
    chk("C2c 동선 pack validate 완료기준 통과", a_route["verdict"] in ("PASS", "REVIEW_ONLY"))
    chk("C2d 맛집 pack validate 완료기준 통과", a_food["verdict"] in ("PASS", "REVIEW_ONLY"))
    chk("C2e 동선 노드>0(근거 기반)", a_route["counts"]["nodes"] > 0)

    # C3 — 오염격리(핵심): aspect pack 은 자기 소스만(다른 aspect 마커 미혼입)
    rt_txt, fd_txt = _aspect_texts(a_route["pack"]), _aspect_texts(a_food["pack"])
    chk("C3 동선 pack 은 ROUTEMARK 만(FOODMARK 미혼입)",
        "ROUTEMARK" in rt_txt and "FOODMARK" not in rt_txt)
    chk("C3b 맛집 pack 은 FOODMARK 만(ROUTEMARK 미혼입)",
        "FOODMARK" in fd_txt and "ROUTEMARK" not in fd_txt)
    chk("C3c 두 aspect pack 의 노드셋 분리(교집합 0)",
        not (set(n["id"] for n in a_route["pack"]["nodes"])
             & set(n["id"] for n in a_food["pack"]["nodes"])))

    # C4 — lang 전파(aspect.lang='id' → provider.search 로 전달)
    chk("C4 aspect.lang='id' provider 로 전파(캡처)", "id" in langs_seen)
    chk("C4b 반환 aspect.lang 보존", a_route["lang"] == "id" and a_food["lang"] == "id")

    # C5 — pack 파일 기록(aspect 별 별도 디렉토리)
    chk("C5 aspect 별 out 디렉토리 분리·manifest 기록",
        a_route["written"] and a_food["written"] and a_route["written"] != a_food["written"]
        and os.path.exists(os.path.join(a_route["written"], "manifest.json")))
    mani = json.load(open(os.path.join(a_route["written"], "manifest.json"), encoding="utf-8"))
    import openbinggu_pack_validate as PV
    chk("C5b 기록 manifest 독립 재검증 PASS", PV.validate_pack(mani)["verdict"] in ("PASS", "REVIEW_ONLY"))
    chk("C5c promotion_allowed_default=false(안전 불변)", mani["promotion_allowed_default"] is False)

    # C6 — 안전 불변식: 노드 candidate=true / promotion_allowed=false
    chk("C6 노드 candidate 불변식(candidate=true·promotion=false)",
        all(n["properties"]["candidate"] is True and n["promotion_allowed"] is False
            for a in r["aspects"] for n in a["pack"]["nodes"]))

    # C7 — 폴백 경로(llm_transport=None) — planner 룰 폴백 aspects 로도 수집(무손실)
    def _generic_provider():
        p = DISC.SearchProvider()
        p.name = "gmock"
        p.search = lambda query, limit=10, lang=None: [
            {"url": "https://generic.example.com/g.txt", "title": "일반 자료", "snippet": "발리 관련"}]
        return p

    home2 = os.path.join(tempfile.mkdtemp(prefix="lc_fb_"), ".binggupack")
    os.makedirs(home2)
    rf = collect("발리 신혼여행", llm_transport=None, provider=_generic_provider(),
                 fetch_runner=_fetch, home=home2)
    chk("C7 폴백(llm_transport=None) status OK", rf["status"] == "OK")
    chk("C7b 폴백 source=fallback", rf["source"] == "fallback")
    chk("C7c 폴백도 aspect>0(planner 무손실)", len(rf["aspects"]) > 0)
    chk("C7d 폴백 aspect 도 pack 생성(완료기준)",
        all(a["verdict"] in ("PASS", "REVIEW_ONLY") for a in rf["aspects"]))

    # C8 — EMPTY_TOPIC(None/공백) → aspects []
    re_ = collect("   ", llm_transport=_plan_t)
    chk("C8 공백 topic → EMPTY_TOPIC·aspects []",
        re_["status"] == "EMPTY_TOPIC" and re_["aspects"] == [])
    chk("C8b None topic → EMPTY_TOPIC", collect(None)["status"] == "EMPTY_TOPIC")

    # C9 — max_aspects cap 전파(planner→collect)
    rcap = collect("발리", llm_transport=_plan_t, provider=_mk_provider(),
                   fetch_runner=_fetch, home=os.path.join(tempfile.mkdtemp(prefix="lc_cap_"), ".bp"),
                   max_aspects=1)
    chk("C9 max_aspects=1 → aspect 1개", len(rcap["aspects"]) == 1)

    # C10 — cross-aspect 격리 재확인: 다른 aspect sub-home 의 소스가 서로 안 보임
    #   (a_route 의 sub-home 과 a_food 의 sub-home 은 물리 분리 → load_sources 교집합 0)
    sub_route = os.path.join(home, "aspects", "00_%s" % FAC._slug("동선"))
    sub_food = os.path.join(home, "aspects", "01_%s" % FAC._slug("맛집"))
    sids_route = {s["source_id"] for s in HV.load_sources(HV.sources_path(sub_route))}
    sids_food = {s["source_id"] for s in HV.load_sources(HV.sources_path(sub_food))}
    chk("C10 aspect sub-home 화이트리스트 물리 분리(교집합 0)",
        bool(sids_route) and bool(sids_food) and not (sids_route & sids_food))

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("=== %d/%d ===" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


def _main(argv):
    import json
    import argparse
    ap = argparse.ArgumentParser(
        description="주제→분류설계(aspect)→aspect 별 수집/pack (LLM 동적 분류 경로)")
    ap.add_argument("--topic", help="수집 주제 (예: '발리 신혼여행')")
    ap.add_argument("--out", default=None, help="pack 출력 루트 디렉토리(없으면 미기록)")
    ap.add_argument("--max-aspects", type=int, default=5, help="설계할 최대 aspect 수")
    ap.add_argument("--max-sources", type=int, default=10, help="aspect 당 승급/수확 최대 소스 수")
    ap.add_argument("--live", action="store_true",
                    help="실 ollama planner transport 사용(네트워크). 미지정 시 룰 폴백")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1
    if not args.topic:
        ap.error("--topic 필요 (또는 --selftest)")

    transport = PLAN.default_ollama_transport() if args.live else None
    r = collect(args.topic, llm_transport=transport, out_dir=args.out,
                max_aspects=args.max_aspects, max_sources=args.max_sources)
    summary = {"status": r["status"], "topic": r.get("topic"), "source": r.get("source"),
               "n_aspects": r.get("n_aspects"), "total_docs": r.get("total_docs"),
               "aspects": [{k: a[k] for k in ("name", "doc_count", "queries", "lang",
                                              "verdict", "promoted", "written")}
                           for a in r.get("aspects", [])]}
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if r["status"] in ("OK", "EMPTY_TOPIC") else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
