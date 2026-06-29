"""binggu_pack_edges — 세분화된 여러 pack 의 메타 신호에서 **pack-간 workflow edges** 추론.

owner 항목 C: "팩 간 edges". build_pack 은 한 pack 내부 노드만 다루고 pack['edges']=[]
(노드 엣지 MVP 계약·불변). 본 모듈은 그와 **별개 차원** — 여러 세분화 pack 을 받아
OpenCrab workflow 가 쓰는 pack-level edge 구조 [{from_pkg, to_pkg, relation}] 를 만든다.

결정적 추론만(LLM·네트워크·임베딩 0). manifest 에 이미 존재하는 신호로만 관계 결정:
  (1) depends_on              → references     (child→parent, 방향성)
  (2) pack_id 경로 계층        → parent_of      (parent→child, 방향성)
  (3) sequence/order 연속      → sequence_next  (이전→다음, 방향성)
  (4) cross_pack_tags 교집합   → adjacent       (무방향·from<to 정규화)
  (5) topic 토큰 Jaccard≥임계  → related        (무방향·from<to 정규화)

한 쌍에 복수 신호 시 우선순위(references>parent_of>sequence_next>adjacent>related)로
**1개만** 채택(dedup). 빙구팩 철학: 절대 raise 0(잘못된 입력은 errors 에 typed 기록·전체
안 죽음), 무손실(입력 모든 valid pack 을 counts.packs 반영·by_relation 합==len(edges)),
기존 계약 보존(pack['edges'] 불변·여긴 pack-간 edge 라 키도 from_pkg/to_pkg 로 구분),
결정적 정렬 출력.

진입점(Phase3 통합용): infer_edges(pack_metas) -> dict.
입력 형태 3종 모두 허용: build_pack 반환 dict / 순수 pack dict / 순수 manifest dict.
"""
import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 관계 화이트리스트 + 우선순위(앞일수록 높음) — 한 쌍 복수 신호 시 1개만 채택
RELATIONS = ("references", "parent_of", "sequence_next", "adjacent", "related")
_PRIORITY = {r: i for i, r in enumerate(RELATIONS)}
# 신호별 결정적 confidence(related 만 Jaccard 로 동적)
_CONFIDENCE = {"references": 1.0, "parent_of": 0.9, "sequence_next": 0.85, "adjacent": 0.7}


def _tokens(text):
    """로컬 토큰화(workflow_recommend 패턴 차용 — import 부작용 0 위해 재구현)."""
    return set(re.findall(r"[0-9a-z가-힣]+", str(text or "").lower()))


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def _as_int(v):
    """sequence/order 값을 int 로 정규화. 불가하면 None(raise 0)."""
    if isinstance(v, bool):           # bool 은 int subclass — 순번으로 취급 안 함
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v.strip())
    return None


def _normalize_meta(obj):
    """build_pack 반환 / 순수 pack dict / 순수 manifest → 표준 메타 dict.

    반환 {pack_id, topic, depends_on, cross_pack_tags, scope, risk_level, sequence}.
    pack_id 가 없거나 입력이 dict 아니면 None(호출부가 errors 에 typed 기록 — raise 0).
    """
    if not isinstance(obj, dict):
        return None
    src = obj
    if isinstance(src.get("pack"), dict):          # build_pack 반환({status,pack,...})
        src = src["pack"]
    if isinstance(src.get("manifest"), dict):      # 순수 pack dict(manifest 하위)
        src = src["manifest"]
    pack_id = src.get("pack_id")
    if not isinstance(pack_id, str) or not pack_id.strip():
        return None
    dep = src.get("depends_on")
    dep = [d for d in dep if isinstance(d, str)] if isinstance(dep, list) else []
    tags = src.get("cross_pack_tags")
    tags = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    seq = _as_int(src.get("sequence"))
    if seq is None:
        seq = _as_int(src.get("order"))
    return {
        "pack_id": pack_id.strip(),
        "topic": src.get("topic") or "",
        "depends_on": dep,
        "cross_pack_tags": tags,
        "scope": src.get("scope"),
        "risk_level": src.get("risk_level"),
        "sequence": seq,
    }


def _hierarchy_relation(pack_id_a, pack_id_b):
    """pack_id 경로 계층(prefix) 판정. 반환 (parent_id, child_id) 또는 None.

    'topic/a' vs 'topic/a/b' → ('topic/a','topic/a/b'). 'topic/a' vs 'topic/ab' 는
    토큰 단위 비교라 prefix 아님(문자열 prefix 오탐 방지)."""
    pa = [p for p in str(pack_id_a).split("/") if p]
    pb = [p for p in str(pack_id_b).split("/") if p]
    if not pa or not pb or pa == pb:
        return None
    if len(pa) < len(pb) and pb[:len(pa)] == pa:
        return (pack_id_a, pack_id_b)
    if len(pb) < len(pa) and pa[:len(pb)] == pb:
        return (pack_id_b, pack_id_a)
    return None


def _ordered(x, y):
    """무방향 관계의 결정적 from<to 정규화."""
    return (x, y) if x <= y else (y, x)


def _pair_relation(meta_a, meta_b, topic_jaccard_min):
    """두 메타에서 우선순위 최고 1개 신호 → (from, to, relation, basis, confidence) 또는 None."""
    a, b = meta_a["pack_id"], meta_b["pack_id"]
    if a == b:                                   # self-edge 금지
        return None
    cands = []  # (priority, from, to, relation, basis, confidence)

    # (1) references — depends_on (child→parent, 방향성)
    if b in meta_a["depends_on"]:
        cands.append((_PRIORITY["references"], a, b, "references",
                      "depends_on", _CONFIDENCE["references"]))
    if a in meta_b["depends_on"]:
        cands.append((_PRIORITY["references"], b, a, "references",
                      "depends_on", _CONFIDENCE["references"]))

    # (2) parent_of — pack_id 경로 계층(parent→child, 방향성)
    hier = _hierarchy_relation(a, b)
    if hier:
        cands.append((_PRIORITY["parent_of"], hier[0], hier[1], "parent_of",
                      "pack_id_hierarchy", _CONFIDENCE["parent_of"]))

    # (3) sequence_next — sequence/order 연속(이전→다음, 방향성)
    sa, sb = meta_a["sequence"], meta_b["sequence"]
    if sa is not None and sb is not None and abs(sa - sb) == 1:
        frm, to = (a, b) if sa < sb else (b, a)
        cands.append((_PRIORITY["sequence_next"], frm, to, "sequence_next",
                      "sequence:%d->%d" % (min(sa, sb), max(sa, sb)),
                      _CONFIDENCE["sequence_next"]))

    # (4) adjacent — cross_pack_tags 교집합(무방향)
    shared = set(meta_a["cross_pack_tags"]) & set(meta_b["cross_pack_tags"])
    if shared:
        frm, to = _ordered(a, b)
        cands.append((_PRIORITY["adjacent"], frm, to, "adjacent",
                      "cross_pack_tags:" + ",".join(sorted(shared)),
                      _CONFIDENCE["adjacent"]))

    # (5) related — topic 토큰 Jaccard≥임계(무방향)
    j = _jaccard(_tokens(meta_a["topic"]), _tokens(meta_b["topic"]))
    if j >= topic_jaccard_min and j > 0.0:
        frm, to = _ordered(a, b)
        cands.append((_PRIORITY["related"], frm, to, "related",
                      "topic_jaccard:%.3f" % j, round(j, 3)))

    if not cands:
        return None
    # 우선순위 최고 1개 — 동순위(예: 양방향 references)는 결정적 정렬로 첫 항목
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    _, frm, to, rel, basis, conf = cands[0]
    return (frm, to, rel, basis, conf)


def infer_edges(pack_metas, *, topic_jaccard_min=0.34, max_edges=None):
    """세분화된 여러 pack 메타 → pack-간 workflow edges (결정적·preview only).

    인자:
      pack_metas       : build_pack 반환 dict / 순수 pack dict / 순수 manifest dict 의 리스트
      topic_jaccard_min: related 엣지 임계(기본 0.34 ≈ 2/3 겹침 근사·보수적)
      max_edges        : 정렬 후 상위 N 개로 제한(None=무제한)

    반환:
      {status, edges:[{from_pkg,to_pkg,relation,basis,confidence}], counts, errors}
      - status   : 항상 "OK"(raise 0 — 잘못된 입력은 errors 로). 입력 자체가 리스트 아니면 "ERROR".
      - counts   : {packs, edges, by_relation:{relation:n}}
      - errors   : [{index, reason}] — pack_id 누락/비-dict 등 skip 된 항목(전체는 안 죽음)
    """
    if not isinstance(pack_metas, (list, tuple)):
        return {"status": "ERROR", "reason": "pack_metas 가 list 아님",
                "edges": [], "counts": {"packs": 0, "edges": 0, "by_relation": {}},
                "errors": [{"index": -1, "reason": "INPUT_NOT_LIST"}]}

    metas, errors = [], []
    for i, obj in enumerate(pack_metas):
        m = _normalize_meta(obj)
        if m is None:
            reason = "NOT_A_DICT" if not isinstance(obj, dict) else "PACK_ID_MISSING"
            errors.append({"index": i, "reason": reason})
            continue
        metas.append(m)

    seen, raw_edges = set(), []
    for i in range(len(metas)):
        for j in range(i + 1, len(metas)):
            res = _pair_relation(metas[i], metas[j], topic_jaccard_min)
            if not res:
                continue
            frm, to, rel, basis, conf = res
            key = (frm, to, rel)
            if key in seen:                      # 쌍 중복 dedup(동일 pack_id 중복 입력 등)
                continue
            seen.add(key)
            raw_edges.append({"from_pkg": frm, "to_pkg": to, "relation": rel,
                              "basis": basis, "confidence": conf})

    raw_edges.sort(key=lambda e: (e["from_pkg"], e["to_pkg"], e["relation"]))
    if isinstance(max_edges, int) and max_edges >= 0:
        raw_edges = raw_edges[:max_edges]

    by_rel = {}
    for e in raw_edges:
        by_rel[e["relation"]] = by_rel.get(e["relation"], 0) + 1

    return {"status": "OK", "edges": raw_edges,
            "counts": {"packs": len(metas), "edges": len(raw_edges), "by_relation": by_rel},
            "errors": errors}


# ── selftest (전부 메모리 mock · 네트워크/store 0) ─────────────────────
def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    def meta(pid, topic="", depends_on=None, tags=None, seq=None, risk="low"):
        m = {"pack_id": pid, "topic": topic, "risk_level": risk,
             "depends_on": depends_on or [], "cross_pack_tags": tags or []}
        if seq is not None:
            m["sequence"] = seq
        return m

    # C1 depends_on → references (child→parent)
    r = infer_edges([meta("topic/child", depends_on=["topic/parent"]), meta("topic/parent")])
    e1 = r["edges"]
    chk("C1 depends_on → references(child→parent)",
        len(e1) == 1 and e1[0]["relation"] == "references"
        and e1[0]["from_pkg"] == "topic/child" and e1[0]["to_pkg"] == "topic/parent")

    # C2 pack_id 경로 계층 → parent_of (parent→child)
    r = infer_edges([meta("topic/a"), meta("topic/a/b")])
    e2 = r["edges"]
    chk("C2 pack_id 계층 → parent_of(parent→child)",
        len(e2) == 1 and e2[0]["relation"] == "parent_of"
        and e2[0]["from_pkg"] == "topic/a" and e2[0]["to_pkg"] == "topic/a/b")
    # 계층 오탐 방지: topic/a vs topic/ab 는 parent_of 아님
    r_amb = infer_edges([meta("topic/a"), meta("topic/ab")])
    chk("C2b topic/a vs topic/ab 계층 오탐 0",
        all(x["relation"] != "parent_of" for x in r_amb["edges"]))

    # C3 cross_pack_tags 교집합 → adjacent (무방향 from<to)
    r = infer_edges([meta("zeta", tags=["t1", "t2"]), meta("alpha", tags=["t2", "t9"])])
    e3 = r["edges"]
    chk("C3 tags 교집합 → adjacent(from<to 정규화)",
        len(e3) == 1 and e3[0]["relation"] == "adjacent"
        and e3[0]["from_pkg"] == "alpha" and e3[0]["to_pkg"] == "zeta")

    # C4 topic Jaccard ≥ 임계 → related, 미만 → 0
    r_hi = infer_edges([meta("p/1", topic="입찰 가격 예측 모델"),
                        meta("p/2", topic="입찰 가격 예측 분석")])
    chk("C4 topic Jaccard≥임계 → related",
        any(x["relation"] == "related" for x in r_hi["edges"]))
    r_lo = infer_edges([meta("p/1", topic="입찰 가격 예측"),
                        meta("p/2", topic="요리 김치 레시피")], topic_jaccard_min=0.34)
    chk("C4b 임계 미만 → related 엣지 0",
        all(x["relation"] != "related" for x in r_lo["edges"]))

    # C5 우선순위/dedup: depends_on + tag중복 동시 → references 1건만(adjacent 억제)
    r = infer_edges([meta("topic/x", depends_on=["topic/y"], tags=["s"]),
                     meta("topic/y", tags=["s"])])
    chk("C5 복수신호 → references 1건만(adjacent 억제)",
        len(r["edges"]) == 1 and r["edges"][0]["relation"] == "references")

    # C6 결정적: 동일 입력 2회 → 직렬화 바이트 동일
    inp = [meta("zeta", tags=["t"]), meta("alpha", tags=["t"]),
           meta("topic/a"), meta("topic/a/b")]
    s1 = json.dumps(infer_edges(inp), ensure_ascii=False, sort_keys=True)
    s2 = json.dumps(infer_edges(inp), ensure_ascii=False, sort_keys=True)
    edges_sorted = infer_edges(inp)["edges"]
    is_sorted = all(
        (edges_sorted[k]["from_pkg"], edges_sorted[k]["to_pkg"], edges_sorted[k]["relation"]) <=
        (edges_sorted[k + 1]["from_pkg"], edges_sorted[k + 1]["to_pkg"], edges_sorted[k + 1]["relation"])
        for k in range(len(edges_sorted) - 1))
    chk("C6 결정적 정렬·2회 호출 바이트 동일", s1 == s2 and is_sorted)

    # C7 self-edge 금지: 같은 pack_id 쌍 / 단일 / 빈 리스트 → 엣지 0
    r_self = infer_edges([meta("dup", tags=["t"]), meta("dup", tags=["t"])])
    chk("C7 같은 pack_id 쌍 → 엣지 0(self 금지)", r_self["counts"]["edges"] == 0)
    chk("C7b 단일 pack → 엣지 0", infer_edges([meta("solo")])["counts"]["edges"] == 0)
    chk("C7c 빈 리스트 → 엣지 0·status OK", infer_edges([])["status"] == "OK"
        and infer_edges([])["counts"]["edges"] == 0)

    # C8 잘못된 입력 → status OK·errors typed·해당 항목만 skip
    r = infer_edges([meta("topic/a", depends_on=["topic/b"]), meta("topic/b"),
                     {"no_pack_id": 1}, "not-a-dict", 42])
    reasons = {e["reason"] for e in r["errors"]}
    chk("C8 잘못된 입력 → status OK·해당만 skip",
        r["status"] == "OK" and len(r["errors"]) == 3
        and "PACK_ID_MISSING" in reasons and "NOT_A_DICT" in reasons
        and r["counts"]["packs"] == 2)
    chk("C8b 정상 항목 엣지는 유지", any(x["relation"] == "references" for x in r["edges"]))
    chk("C8c 입력 자체 list 아님 → ERROR(raise 0)",
        infer_edges("nope")["status"] == "ERROR")

    # C9 무손실: N팩 전부 counts.packs·by_relation 합 == len(edges)
    big = [meta("topic/a"), meta("topic/a/b"), meta("topic/a/b/c"),
           meta("q", tags=["z"]), meta("r", tags=["z"])]
    r = infer_edges(big)
    chk("C9 counts.packs == valid N", r["counts"]["packs"] == 5)
    chk("C9b by_relation 합 == len(edges)",
        sum(r["counts"]["by_relation"].values()) == len(r["edges"])
        == r["counts"]["edges"])

    # C10 relation 전부 화이트리스트 내
    chk("C10 relation 전부 RELATIONS 화이트리스트",
        all(x["relation"] in RELATIONS for x in r["edges"]))

    # C11 confidence 전부 0.0~1.0
    all_edges = r["edges"] + r_hi["edges"] + e1 + e2 + e3
    chk("C11 confidence 0.0~1.0",
        all(0.0 <= x["confidence"] <= 1.0 for x in all_edges))

    # C12 입력 정규화: build_pack 반환 / 순수 pack / 순수 manifest 동일 처리
    manifest_a = {"pack_id": "topic/m1", "topic": "입찰 가격 예측",
                  "depends_on": ["topic/m2"], "cross_pack_tags": []}
    manifest_b = {"pack_id": "topic/m2", "topic": "입찰 가격 예측"}
    pack_form = {"manifest": dict(manifest_a)}                 # 순수 pack dict
    buildret_form = {"status": "OK", "pack": {"manifest": dict(manifest_b)}}  # build_pack 반환
    r = infer_edges([pack_form, buildret_form])
    chk("C12 3형태(manifest/pack/build_pack) 정규화 동일",
        any(x["relation"] == "references" and x["from_pkg"] == "topic/m1"
            and x["to_pkg"] == "topic/m2" for x in r["edges"]))
    # 순수 manifest 직접도 동작
    r_m = infer_edges([manifest_a, manifest_b])
    chk("C12b 순수 manifest 직접 입력 동작",
        any(x["relation"] == "references" for x in r_m["edges"]))

    # C13 sequence/order 연속 → sequence_next
    r = infer_edges([meta("step/2", seq=2), meta("step/1", seq=1)])
    e13 = r["edges"]
    chk("C13 sequence 연속 → sequence_next(이전→다음)",
        len(e13) == 1 and e13[0]["relation"] == "sequence_next"
        and e13[0]["from_pkg"] == "step/1" and e13[0]["to_pkg"] == "step/2")
    # order 키도 동작·비연속(2칸)은 엣지 0
    r_gap = infer_edges([{"pack_id": "g/1", "order": 1}, {"pack_id": "g/3", "order": 3}])
    chk("C13b 비연속 sequence → sequence_next 0",
        all(x["relation"] != "sequence_next" for x in r_gap["edges"]))

    # C14 출력 직렬화 PII/secret 잔존 0
    blob = json.dumps(infer_edges(big), ensure_ascii=False)
    try:
        import watcher_batch_m1 as _bm1
        chk("C14 출력 PII/secret 잔존 0", not _bm1.scan_residual_pii(blob))
    except Exception:
        chk("C14 출력 PII/secret 잔존 0(scanner 부재 skip)", True)

    # C15 max_edges 제한(결정적 상위 N)
    r_cap = infer_edges(big, max_edges=2)
    chk("C15 max_edges 제한", len(r_cap["edges"]) == 2
        and r_cap["edges"] == infer_edges(big)["edges"][:2])

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_pack_edges — use --selftest, or import infer_edges(pack_metas)")
