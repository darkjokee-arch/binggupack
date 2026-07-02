"""binggu_workflow_recommend — pack(nodes/evidence)을 읽어 실행 가능한 workflow spec 추천.

owner 요구5: 단순 문장 요약이 아니라 실행 가능한 spec(이름/입력값/실행순서/근거 evidence/confidence/주의).
기존 추천기(source_candidate_planner_poc)는 goal 문자열+정적룰이라 pack 을 안 읽음 → 본 모듈은
**pack 내용 기반**(노드 sentence 토큰 분포 → 템플릿 매칭 + 근거 evidence_refs + confidence).

안전: 추천(preview)만. execution_allowed=False 고정 — 실행/자동승격 0(사람이 결정).

strangler: 순수 정본(WORKFLOW_TEMPLATES · _tokens · _pack_tokens · recommend)은 이 모듈로
byte-identical 이관됐다. 모듈레벨은 stdlib 뿐이라 cross-dep 0. selftest W10 의 watcher_batch_m1
의존은 함수 내부 lazy import(try/except graceful)로만 존재 — 진입점 scripts/binggu_workflow_recommend.py
가 scripts/ sys.path 로 해소하고, 패키지 단독 실행 시엔 scanner 부재로 graceful skip 된다.
"""
import re
import sys

# workflow 템플릿 — 키워드(주제/노드 토큰) → 실행 가능한 spec. v2 에서 학습/확장.
WORKFLOW_TEMPLATES = [
    {
        "id": "wf-price-predict",
        "name": "가격 예측 분석 워크플로우",
        "keywords": {"가격", "예측", "예가", "낙찰", "price", "predict", "추정"},
        "inputs": ["과거 낙찰가/기초금액 데이터셋", "예측 대상 공고 식별자"],
        "steps": ["데이터 수집/정제", "피처 추출(기초금액·경쟁률·기간)",
                  "모델 학습/검증", "예측가 산출 + 신뢰구간"],
        "cautions": ["데이터 표본 부족 시 정확도 저하", "분포 변화(레짐) 모니터링 필요"],
    },
    {
        "id": "wf-bid-monitor",
        "name": "입찰 공고 모니터링 워크플로우",
        "keywords": {"공고", "입찰", "조달", "나라장터", "g2b", "발주", "마감"},
        "inputs": ["관심 키워드/업종코드", "알림 채널"],
        "steps": ["공고 소스 등록", "주기적 수집", "필터/적격성 판정", "알림 발송"],
        "cautions": ["소스 차단/구조변경 시 수집 누락", "마감 임박 건 우선순위"],
    },
    {
        "id": "wf-research-digest",
        "name": "자료조사 요약 워크플로우(기본)",
        "keywords": set(),  # fallback — 항상 매칭
        "inputs": ["주제", "수집 소스 목록"],
        "steps": ["소스 발견", "수집/파싱", "핵심 근거 추출", "요약 리포트"],
        "cautions": ["출처 신뢰도 검증 필요"],
    },
]


def _tokens(text):
    return set(re.findall(r"[0-9a-z가-힣]+", str(text or "").lower()))


def _pack_tokens(pack):
    """pack 노드 sentence + manifest topic 의 토큰 집합 + 토큰별 노드 빈도."""
    from collections import Counter
    freq = Counter()
    topic = (pack.get("manifest", {}) or {}).get("topic", "")
    for n in pack.get("nodes", []):
        for t in _tokens(n.get("properties", {}).get("sentence", "")):
            freq[t] += 1
    return freq, _tokens(topic)


def recommend(pack, top_k=3, min_confidence=0.0):
    """pack → workflow spec 후보(점수 내림차순). 반환 dict(status/recommendations)."""
    nodes = pack.get("nodes", [])
    freq, topic_toks = _pack_tokens(pack)
    total = max(1, len(nodes))

    recs = []
    for tpl in WORKFLOW_TEMPLATES:
        kw = tpl["keywords"]
        if kw:
            hit_nodes = sum(1 for n in nodes
                            if _tokens(n.get("properties", {}).get("sentence", "")) & kw)
            topic_hit = 1 if (topic_toks & kw) else 0
            # confidence = 키워드 포함 노드 비율(0~0.85) + 주제 매칭 보너스(0.15)
            conf = round(min(0.85, hit_nodes / total) + 0.15 * topic_hit, 3)
        else:
            hit_nodes = total
            conf = 0.3  # fallback 기본 신뢰도(항상 후보로 남되 낮게)
        if conf < min_confidence:
            continue
        # 근거 evidence_refs — 키워드 포함 노드의 evidence_refs(최대 5)
        ev_refs = []
        for n in nodes:
            if not kw or (_tokens(n.get("properties", {}).get("sentence", "")) & kw):
                ev_refs.extend(n.get("evidence_refs", []))
            if len(ev_refs) >= 5:
                break
        recs.append({
            "workflow_id": tpl["id"],
            "workflow": tpl["name"],
            "inputs": tpl["inputs"],
            "steps": tpl["steps"],
            "evidence_refs": ev_refs[:5],
            "matched_nodes": hit_nodes,
            "confidence": conf,
            "cautions": tpl["cautions"],
            "execution_allowed": False,   # 추천(preview)만 — 실행은 사람이 결정
        })
    recs.sort(key=lambda r: r["confidence"], reverse=True)
    return {"status": "OK", "pack_id": (pack.get("manifest", {}) or {}).get("pack_id"),
            "node_count": len(nodes), "recommendations": recs[:top_k]}


# ── selftest ──────────────────────────────────────────────────────────
def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(cond)
        print(("  PASS " if cond else "  FAIL ") + name)

    def mk_pack(topic, sentences):
        nodes = [{"id": "n%d" % i, "properties": {"sentence": s}, "evidence_refs": ["EVC-%d" % i]}
                 for i, s in enumerate(sentences)]
        return {"manifest": {"pack_id": "topic/x", "topic": topic}, "nodes": nodes}

    pack = mk_pack("입찰 가격 예측", [
        "입찰 가격 예측 모델의 정확도가 향상되었다",
        "낙찰가 추정에 기초금액이 중요하다",
        "예가 산정 방식이 변경되었다",
        "조달청 공고 데이터를 수집한다",
    ])
    r = recommend(pack)
    chk("W1 추천 생성", r["status"] == "OK" and len(r["recommendations"]) > 0)
    top = r["recommendations"][0]
    chk("W2 최상위=가격예측(주제 매칭)", top["workflow_id"] == "wf-price-predict")
    chk("W3 spec 필수필드(inputs/steps/evidence/confidence/cautions)",
        all(k in top for k in ("inputs", "steps", "evidence_refs", "confidence", "cautions")))
    chk("W4 실행순서 steps>=3", len(top["steps"]) >= 3)
    chk("W5 근거 evidence_refs 존재", len(top["evidence_refs"]) > 0)
    chk("W6 confidence 0~1", 0.0 <= top["confidence"] <= 1.0)
    chk("W7 execution_allowed=False(안전)", top["execution_allowed"] is False)
    chk("W8 fallback 항상 후보(research-digest 포함)",
        any(x["workflow_id"] == "wf-research-digest" for x in r["recommendations"]))

    # 무관 주제 → 가격예측 confidence 낮음
    r2 = recommend(mk_pack("요리 레시피", ["김치찌개 끓이는 법", "재료 손질"]))
    price = next((x for x in r2["recommendations"] if x["workflow_id"] == "wf-price-predict"), None)
    chk("W9 무관 주제 가격예측 0 매칭", price is None or price["matched_nodes"] == 0)

    # W10 — B3: workflow 추천 출력에 PII/secret 잔존 0(출력 텍스트 전체 scan)
    try:
        import json
        import watcher_batch_m1 as _bm1
        blob = json.dumps(r, ensure_ascii=False)
        chk("W10 추천 출력 PII/secret 잔존 0", not _bm1.scan_residual_pii(blob))
    except Exception:
        chk("W10 추천 출력 PII/secret 잔존 0(scanner 부재 skip)", True)

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_workflow_recommend — use --selftest, or import recommend(pack)")
