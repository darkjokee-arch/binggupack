# -*- coding: utf-8 -*-
"""binggupack.pack.rationale_suggest — 2층 근거 사슬 추천 정본 (PoC · read-only · 추천만).

설계: BINGGUPACK_RATIONALE_EDGE_DESIGN.md (AIF 동형 · 신규 predicate 0).
1층(node 후보 선별) 위에 2층으로 "왜 저장 가치(rationale)" + "무엇과 연결(suggested_edges)"을 추천한다.

strangler: scripts/binggu_rationale_suggest.py 의 순수 로직(suggest_rationale·_rationale_text·
상수·_selftest)을 byte-identical 로 이관한 정본이다. 판정 로직은 1바이트도 변하지 않았다.
변경된 것은 import 바인딩뿐:
  scripts 원본  `sys.path.insert(0, HERE); from openbinggu_verb_edge_schema import ...`
  이관본        `from binggupack.schema.verb_edge import validate_verb_edge, VERB_EDGES`
lazy semantic pre-filter(canonical_semantic/recall)는 canonical_semantic 미이관이라 recall 선례대로
scripts/ 를 sys.path 에 임시 삽입해 bare-name 접근(canonical_semantic 이관 시 이 블록 정리).

불변 (전부 selftest 증명):
  - 신규 predicate 0 — `supports_judgment`(기존, "근거가_된다")만. validate_verb_edge 위임으로 매트릭스 강제.
  - 자동 저장 0 · ledger/candidate/DB/file write 0 · 추천 list 만 반환.
  - evidence 없는 src → edge 추천 0 (rationale만). 가짜 evidence/node 양산 금지(hallucination 0 — 입력 노드만 연결).
  - 판단→판단 직접 근거 = 매트릭스 보류(중간 근거노드는 실제 evidence 있을 때만 — 본 PoC는 기존 후보만 연결).
  - cos/임베딩 미사용(설계 §8: 유사도만으론 근거 판정 약함) — 구조 신호(label_kind 역할+evidence)만, 결정적·멱등.
  - canonical 5종 계층 불변 · semantic_subtype 은 표시 보조.
"""
import hashlib
import importlib
import os
import sys

from binggupack.schema.verb_edge import validate_verb_edge, VERB_EDGES

SUPPORTS = "supports_judgment"          # 기존 predicate (신규 0)

# 데카르트곱(src×판단 전수) 축소용 semantic pre-filter. cos는 근거 판정이 아니라
# "후보 노출 폭"일 뿐 — 통과=도장 아님. 최종 근거/인과는 사람 도장(4cli 20260618 both_reject 결론).
# opt-in(canon.enabled)일 때만 적용, OFF면 기존 전수 후보(하위호환).
_RATIONALE_FLOOR = 0.60   # 검토 큐 진입선(넓게·놓침 방지). 0.55는 노이즈 과다라 제외.
_DUP_CEIL = 0.85          # 이상은 거의 중복 문장(동어반복=인과 아님·진짜 인과는 0.7대) → near_duplicate 격리(도장 큐 제외).
SUPPORTS_SRC = VERB_EDGES[SUPPORTS]["src"]   # {증거, 상태, 개념}
CAVEAT_NODE = "candidate · unverified · 사람 SAVE/승인 전 저장 0"
CAVEAT_EDGE = "candidate edge · 매트릭스 검증 통과 · evidence 원문 필수 · 사람 승인 전 저장 0"

# rationale 문구 = 결정적 템플릿(LLM 0 · hallucination 0). semantic_subtype 보조.
_SUBTYPE_WHY = {
    "교훈": "반복 적용 가능한 규칙성(다음에 같은 실수 회피)",
    "결정": "선택의 방향과 이유 — 이후 판단의 기준",
    "선호": "반복되는 작업 방식 — 일관성 근거",
    "설계결정": "구조/절차 설계 근거 — 변경 시 참조점",
    "버그패턴": "반복 실수/결함 패턴 — 재발 방지 신호",
    "사실": "확인된 사실/지식 — 판단의 근거 자료",
}


def _rationale_text(cand):
    sub = cand.get("semantic_subtype")
    base = _SUBTYPE_WHY.get(sub, "저장 후보 — 판단/근거 성격")
    lk = cand.get("label_kind") or "?"
    ev = "evidence 1건+ 뒷받침" if cand.get("evidence_refs") else "evidence 미첨부(저장 시 원문 필요)"
    tail = ("/subtype=%s" % sub) if sub else ""
    return "%s · canonical=%s%s · %s" % (base, lk, tail, ev)


def suggest_rationale(candidates, semantic=None):
    """입력: captured 후보 목록 [{text, label_kind, semantic_subtype?, confidence?, evidence_refs?}].
    출력: {rationale[], suggested_edges[], note}. read-only · write 0.
    ignored 문장은 호출측(preview)이 미포함(buffer 미저장) — 본 함수는 받은 후보만 처리.
    semantic: None=opt-in 자동(canon.enabled 시 cos pre-filter로 데카르트곱 축소) / False=강제 OFF(전수)
              / callable=scorer 주입(테스트). cos 미달 쌍은 후보 제외(근거 판정 아님 — 사람 도장 최종)."""
    # opt-in semantic pre-filter 준비 (lazy import — 순환 회피). 무관 src-판단 쌍을 후보에서 뺀다.
    _scorer = None
    if semantic is False:
        _scorer = None
    elif callable(semantic):
        _scorer = semantic
    else:
        try:
            # canonical_semantic 미이관(scripts 잔류) — recall 선례대로 scripts/ 를 sys.path 에 임시 삽입.
            # canonical_semantic 이관 시 이 블록 제거하고 패키지 import 로 정리.
            _scr = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "scripts")
            if _scr not in sys.path:
                sys.path.insert(0, _scr)
            import binggu_canonical_semantic as _CS
            if _CS.enabled():
                _R = importlib.import_module("binggu_recall")
                _scorer = _R._semantic_scorer()  # 노드 임베딩 캐시 재사용
        except Exception:
            _scorer = None
    nodes, items = {}, []
    for i, c in enumerate(candidates):
        nid = c.get("id") or ("cand_%d" % i)   # 실제 node id 우선(3층 graph화용), 없으면 임시
        nodes[nid] = {"id": nid, "properties": {"label_kind": c.get("label_kind"), "candidate": True}}
        items.append((nid, c))

    rationale = [{
        "node": c["text"][:60], "label_kind": c.get("label_kind"),
        "semantic_subtype": c.get("semantic_subtype"),
        "confidence": c.get("confidence"),
        "rationale": _rationale_text(c), "caveat": CAVEAT_NODE,
    } for _, c in items]

    tgts = [(nid, c) for nid, c in items if c.get("label_kind") == "판단"]
    # 콜드 캐시 배치 선채움 — 아래 src×판단 쌍 루프가 텍스트마다 단건 HTTP embed 를 왕복하던 것
    # 제거(_why_search_on_graph 와 동일 관례 · 실측 2026-07-30). prefill 없는 커스텀 scorer 는
    # getattr 미스로 통과, 실패해도 단건 경로가 그대로 이어받는다(graceful).
    if _scorer is not None and tgts:
        _pf = getattr(_scorer, "prefill", None)
        if _pf is not None:
            try:
                _pf([c.get("text", "") for _, c in items if c.get("text")])
            except Exception:
                # Optional scorer prefill may fail without changing deterministic fallback scoring.
                pass
    edges, dup_edges, seen = [], [], set()
    for nid, c in items:
        if c.get("label_kind") not in SUPPORTS_SRC:   # 증거/상태/개념만 src
            continue
        if not c.get("evidence_refs"):                # evidence 없으면 edge 보류(rationale만)
            continue
        for tnid, t in tgts:
            if tnid == nid:
                continue
            # semantic pre-filter — 의미 무관 src→판단 쌍은 데카르트곱 후보에서 제외(opt-in).
            cs = None
            if _scorer is not None:
                cs = _scorer(c.get("text", ""), t.get("text", ""))
                if cs is not None and cs < _RATIONALE_FLOOR:
                    continue
            edge = {"id": "%s->%s" % (nid, tnid), "source": nid, "target": tnid,
                    "properties": {"relation": SUPPORTS, "candidate": True},
                    "evidence_refs": list(c["evidence_refs"]), "promotion_allowed": False}
            if validate_verb_edge(edge, nodes)["verdict"] != "PASS":
                continue                              # 매트릭스 위반/신규 predicate 자동 폐기
            ev_key = hashlib.sha256(("|".join(map(str, c["evidence_refs"])) + tnid).encode()).hexdigest()[:12]
            if ev_key in seen:                        # evidence dedup(설계 §5)
                continue
            seen.add(ev_key)
            rec = {
                "source": c["text"][:40], "target": t["text"][:40],
                "source_id": nid, "target_id": tnid,   # 3층 graph화용 id(없으면 임시 cand_i)
                "relation": SUPPORTS, "verb": "근거가_된다",
                "evidence_refs": list(c["evidence_refs"]),
                "status": "candidate", "promotion_allowed": False, "caveat": CAVEAT_EDGE,
                "cos": round(cs, 3) if cs is not None else None,
            }
            if cs is not None and cs >= _DUP_CEIL:
                dup_edges.append(rec)   # 거의 중복(동어반복=인과 아님) → 도장 큐 제외(near_duplicate)
            else:
                edges.append(rec)
    return {"rationale": rationale, "suggested_edges": edges, "near_duplicates": dup_edges,
            "note": "2층 PoC 추천만 — 자동 저장 0 · 신규 predicate 0 · evidence 없으면 edge 보류 · target(판단) 없으면 edge 0"}


# ---------------- selftest (순수 함수 · write 0) ----------------
def _selftest():
    # selftest 결정성/속도: semantic pre-filter OFF 강제(전수 후보 검증 · Ollama 비의존).
    os.environ["BINGGU_SEMANTIC_OFF"] = "1"
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    # 1) 증거(evidence 有) + 판단 → supports_judgment edge 추천
    cands = [
        {"text": "로그에 오타가 3번 찍혔다", "label_kind": "증거", "semantic_subtype": "버그패턴", "evidence_refs": ["EVC-1"]},
        {"text": "보내기 전 한 번 더 확인하자", "label_kind": "판단", "semantic_subtype": "교훈"},
    ]
    r = suggest_rationale(cands)
    ck(len(r["rationale"]) == 2, "rationale = 후보 2건 전부")
    ck(len(r["suggested_edges"]) == 1 and r["suggested_edges"][0]["relation"] == SUPPORTS,
       "증거(evidence)→판단 = supports_judgment edge 1건 추천")
    ck(r["suggested_edges"][0]["verb"] == "근거가_된다" and r["suggested_edges"][0]["status"] == "candidate",
       "기존 verb + candidate status (확정 아님)")

    # 2) 신규 predicate 0 — 모든 추천 relation ∈ 기존 6종
    ck(all(e["relation"] in VERB_EDGES for e in r["suggested_edges"]), "추천 relation 전부 기존 6종(신규 predicate 0)")

    # 3) evidence 없는 증거 → edge 0 (rationale만)
    r2 = suggest_rationale([
        {"text": "로그에 오류가 보인다", "label_kind": "증거", "evidence_refs": []},
        {"text": "이건 위험하니 보류한다", "label_kind": "판단"},
    ])
    ck(len(r2["suggested_edges"]) == 0 and len(r2["rationale"]) == 2,
       "evidence 없는 src → edge 0, rationale은 표시")

    # 4) 판단→판단 직접 근거 = 매트릭스 보류(edge 0)
    r3 = suggest_rationale([
        {"text": "B안으로 결정한다", "label_kind": "판단", "semantic_subtype": "결정", "evidence_refs": ["EVC-2"]},
        {"text": "다음부터 먼저 확인하자", "label_kind": "판단", "semantic_subtype": "교훈"},
    ])
    ck(len(r3["suggested_edges"]) == 0, "판단→판단 직접 근거 = 매트릭스 보류(중간 근거노드 없으면 edge 0)")

    # 5) 문서 src 불가 (매트릭스 위반 폐기)
    r4 = suggest_rationale([
        {"text": "이 설계서는 절차를 규정한다", "label_kind": "문서", "evidence_refs": ["EVC-3"]},
        {"text": "이대로 진행한다", "label_kind": "판단"},
    ])
    ck(len(r4["suggested_edges"]) == 0, "문서→판단 supports = 매트릭스 위반 폐기")

    # 6) target(판단) 없음 → edge 0
    r5 = suggest_rationale([{"text": "로그 기록", "label_kind": "증거", "evidence_refs": ["EVC-4"]}])
    ck(len(r5["suggested_edges"]) == 0, "판단 target 없음 → edge 0")

    # 7) hallucination 0 — 추천 source/target 은 입력 text 에서만
    ck(all(e["source"] in {t[:40] for t in [cands[0]["text"]]} or e["source"][:40] in [c["text"][:40] for c in cands]
           for e in r["suggested_edges"]), "edge source/target = 입력 노드만(새 노드 생성 0)")

    # 8) evidence dedup — 동일 evidence+target 중복 추천 0
    dup = suggest_rationale([
        {"text": "증거A", "label_kind": "증거", "evidence_refs": ["EVC-9"]},
        {"text": "증거A 복제", "label_kind": "증거", "evidence_refs": ["EVC-9"]},
        {"text": "판단X", "label_kind": "판단"},
    ])
    ck(len(dup["suggested_edges"]) == 1, "동일 evidence→동일 target dedup(노드 폭증 차단)")

    # 9) caveat 명시(candidate/unverified 확정처럼 표현 금지)
    ck(all("candidate" in it["caveat"] for it in r["rationale"]) and
       all("candidate" in e["caveat"] for e in r["suggested_edges"]),
       "rationale/edge 전부 candidate·unverified caveat 명시")

    # 10) id 보존 — 입력 candidate id → edge source_id/target_id (3층 graph화용)
    rid = suggest_rationale([
        {"id": "node:ev1", "text": "증거문", "label_kind": "증거", "evidence_refs": ["EVC-7"]},
        {"id": "node:j1", "text": "판단문", "label_kind": "판단"},
    ])
    ck(len(rid["suggested_edges"]) == 1 and rid["suggested_edges"][0]["source_id"] == "node:ev1"
       and rid["suggested_edges"][0]["target_id"] == "node:j1",
       "id 보존 → edge source_id/target_id (3층 graph화용)")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
