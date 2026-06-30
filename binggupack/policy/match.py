# -*- coding: utf-8 -*-
"""LocalBinggu 전용 read-only match policy (v0.3.1).

v1.11.0 strangler phase2: 핵심 로직을 scripts/localbinggu_match_policy.py 에서 이 모듈로
이관했다. scripts 파일은 backward-compatible thin wrapper(sys.path bootstrap + 전체 심볼
re-export + __main__ demo CLI)로 유지되며 공개 심볼/동작은 byte-identical 하다(기능 변경 0).

graph_merge.py 의 rapidfuzz 임계 88(자연어 라벨용)이 구조적 node id 에 과민해
cross-domain false positive(node:D1:doc ↔ node:D2:doc)를 만드는 문제를 막는다.

apply 판단에는 이 모듈 결과만 사용. graph_merge.match_node 는 비교 참고용(fuzzy_dup_count)으로만 카운트.
read-only. write/merge/apply 없음.

분류:
  Tier 0  node_id 완전일치                                            -> exact_matches (auto)
  Tier 1  domain·space·node_type·label_kind 동일 + sentence hash 동일  -> safe_matches (auto)
  Tier 2  위 4축 동일 + sentence sim>=95 + evidence_refs 교집합 有       -> safe_matches (auto)
  Tier 3  domain·role 동일 + sentence sim>=90 + evidence_refs 교집합 無  -> review_candidates (자동 merge 금지)
  Reject  domain/space/node_type/label_kind 중 하나라도 다름           -> rejected_fuzzy_matches
  D9      D9 candidate/partial ↔ confirmed 병합 시도                    -> d9_protected_matches (자동 merge 금지)
"""
import hashlib
import re

try:
    from rapidfuzz import fuzz
    RF = True
except Exception:
    RF = False

FUZZY_THRESHOLD = 88   # graph_merge.py 와 동일 (fuzzy 후보 재현용)
SIM_T2 = 95
SIM_T3 = 90


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _shash(s):
    return hashlib.sha256(_norm(s).encode("utf-8")).hexdigest()[:16]


def _sim(a, b):
    if RF:
        return fuzz.ratio(_norm(a), _norm(b))
    # fallback: 동일하면 100, 아니면 0 (보수적)
    return 100 if _norm(a) == _norm(b) else 0


def _role(n):
    return (n["domain"], n["space"], n["node_type"], n["label_kind"])


def normalize_nodes(pkg_nodes):
    """import_package graph.nodes -> match policy 입력 형태."""
    out = []
    for n in pkg_nodes:
        p = n.get("properties", {})
        out.append({
            "id": n["id"],
            "domain": p.get("domain", ""),
            "space": n.get("space", ""),
            "node_type": n.get("node_type", ""),
            "label_kind": p.get("label_kind", ""),
            "sentence": p.get("sentence", n.get("label", "")),
            "candidate": bool(p.get("candidate")),
            "evidence_status": p.get("evidence_status", ""),
            "evidence_refs": set(n.get("evidence_refs", [])),
            "origin": p.get("origin", ""),
        })
    return out


def _is_d9_protect(a, b):
    """D9 candidate/partial ↔ confirmed 조합이면 보호."""
    if a["domain"] != "D9" and b["domain"] != "D9":
        return False
    a_part = a["candidate"] or a["evidence_status"] != "confirmed"
    b_part = b["candidate"] or b["evidence_status"] != "confirmed"
    # 한쪽 partial + 다른쪽 confirmed
    return (a_part and not b_part) or (b_part and not a_part)


def _classify_pair_core(a, b):
    """두 노드 페어를 Tier 분류. 반환: (bucket, tier, reason)"""
    if a["id"] == b["id"]:
        return ("exact_matches", 0, "node_id 완전일치")

    # Reject: 4축 중 하나라도 다름
    if a["domain"] != b["domain"]:
        return ("rejected_fuzzy_matches", -1, f"domain 다름({a['domain']}≠{b['domain']})")
    if a["space"] != b["space"]:
        return ("rejected_fuzzy_matches", -1, "space 다름")
    if a["node_type"] != b["node_type"]:
        return ("rejected_fuzzy_matches", -1, "node_type 다름")
    if a["label_kind"] != b["label_kind"]:
        return ("rejected_fuzzy_matches", -1, "label_kind 다름")

    # 여기부터 domain+role 동일. D9 보호 먼저.
    if _is_d9_protect(a, b):
        return ("d9_protected_matches", -1, "D9 candidate/partial ↔ confirmed 자동병합 금지")

    # Tier 1: sentence hash 동일
    if _shash(a["sentence"]) == _shash(b["sentence"]):
        return ("safe_matches", 1, "4축 동일 + sentence hash 동일")
    sim = _sim(a["sentence"], b["sentence"])
    inter = a["evidence_refs"] & b["evidence_refs"]
    # Tier 2
    if sim >= SIM_T2 and inter:
        return ("safe_matches", 2, f"4축 동일 + sim {sim} + evidence 교집합 {sorted(inter)}")
    # Tier 3
    if sim >= SIM_T3 and not inter:
        return ("review_candidates", 3, f"domain·role 동일 + sim {sim} + evidence 교집합 없음 → review")
    # 그 외(같은 role 이나 유사도 낮음): 별개 노드로 본다(병합 후보 아님)
    return (None, None, f"같은 role 이나 sim {sim} 낮음 → 병합 후보 아님")


def classify_pair(a, b):
    """Step3 watcher 안전필터 wrapper. origin=watcher 관여 페어는 자동병합(exact/safe)
    자격을 박탈해 review_candidates 로 강제 강등한다(match_policy.py 무수정 시 차단 불가
    하던 Step2 watcher candidate 자동병합을 코드로 막음). reject/d9/review/None 은 그대로.
    호출부(evaluate) 무수정."""
    bucket, tier, reason = _classify_pair_core(a, b)
    if (a.get("origin") == "watcher" or b.get("origin") == "watcher") \
            and bucket in ("exact_matches", "safe_matches"):
        return ("review_candidates", tier, "watcher_override: " + reason)
    return (bucket, tier, reason)


def evaluate(nodes, existing=None):
    """fuzzy 후보(graph_merge 와 동일 임계)에 대해서만 Tier 분류.
    existing=None 이면 nodes 내부 페어와이즈(seed 자기검사)."""
    buckets = {
        "exact_matches": [], "safe_matches": [], "review_candidates": [],
        "rejected_fuzzy_matches": [], "cross_domain_rejections": [], "d9_protected_matches": [],
    }
    graph_merge_fuzzy_pairs = []
    cross_domain_auto_merge = []

    pool = existing if existing is not None else []
    seen = list(pool)
    sequence = nodes  # 신규로 들어오는 노드 순서

    for n in sequence:
        # 후보 생성: (a) id rapidfuzz>=88 (graph_merge 호환) OR
        #            (b) 같은 domain+space+node_type+label_kind + (sentence hash 동일 or sentence sim>=90)
        # → id 가 달라도 의미가 같은 노드를 잡아 D9 보호/safe-merge 우회를 막는다.
        best, best_s = None, 0
        best_id_match, best_id_s = None, 0
        for m in seen:
            s_id = _sim_id(n["id"], m["id"])
            id_hit = s_id >= FUZZY_THRESHOLD
            sem_hit = (_role(n) == _role(m) and
                       (_shash(n["sentence"]) == _shash(m["sentence"]) or _sim(n["sentence"], m["sentence"]) >= SIM_T3))
            if id_hit and s_id > best_id_s:
                best_id_match, best_id_s = m, s_id   # graph_merge match_node 재현(노드당 best id 1개)
            if not (id_hit or sem_hit):
                continue
            score = max(s_id, (_sim(n["sentence"], m["sentence"]) if sem_hit else 0))
            if score > best_s:
                best, best_s = m, score
        if best_id_match is not None:
            graph_merge_fuzzy_pairs.append((n["id"], best_id_match["id"], best_id_s))
        if best is not None:
            bucket, tier, reason = classify_pair(n, best)
            if bucket:
                buckets[bucket].append({"a": n["id"], "b": best["id"],
                                        "id_ratio": best_s, "tier": tier, "reason": reason})
                if bucket == "rejected_fuzzy_matches" and n["domain"] != best["domain"]:
                    buckets["cross_domain_rejections"].append({"a": n["id"], "b": best["id"], "reason": reason})
                if bucket in ("exact_matches", "safe_matches") and n["domain"] != best["domain"]:
                    cross_domain_auto_merge.append({"a": n["id"], "b": best["id"]})
        seen.append(n)

    return buckets, graph_merge_fuzzy_pairs, cross_domain_auto_merge


def _sim_id(a, b):
    if RF:
        return fuzz.ratio(a, b)
    return 100 if a == b else 0


# ========== MVP2.1 edge filter (신규, 노드 로직 무수정 / 2차 소비처 차단) ==========
# 설계: docs/BINGGUPACK_MVP21_EDGE_SAFETY_FILTER_DESIGN.md §4. node classify_pair wrapper 사상 복제.
EDGE_RELATION_ALLOWED = {"evidence_supports"}


def _edge_role(e):
    """edge 비교축: edge_type·relation·source·target."""
    p = e.get("properties", {})
    return (e.get("edge_type", ""), p.get("relation", ""), e.get("source", ""), e.get("target", ""))


def classify_edge_pair(e1, e2):
    """edge 페어 분류(read-only). origin=watcher 관여 exact/safe 자격 박탈 → review 강등.
    반환: (bucket, tier, reason). 노드 classify_pair 와 동일 버킷 구조."""
    p1, p2 = e1.get("properties", {}), e2.get("properties", {})
    d1, d2 = p1.get("domain", ""), p2.get("domain", "")

    if e1.get("id") == e2.get("id"):
        bucket, tier, reason = ("exact_matches", 0, "edge id 완전일치")
    else:
        r1, r2 = _edge_role(e1), _edge_role(e2)
        if r1 != r2:
            return ("rejected_fuzzy_matches", -1, "edge role 다름(type/relation/source/target)")
        # cross-domain edge 격리 (endpoint domain 상속값 비교) — 더 안전한 쪽
        if d1 != d2:
            return ("rejected_fuzzy_matches", -1, f"cross-domain edge({d1}≠{d2})")
        refs1, refs2 = set(e1.get("evidence_refs", [])), set(e2.get("evidence_refs", []))
        if refs1 & refs2:
            bucket, tier, reason = ("safe_matches", 1, "edge role 동일 + evidence_refs 교집합")
        else:
            bucket, tier, reason = ("review_candidates", 3, "edge role 동일·evidence_refs 교집합 없음 → review")

    # D9 보호: endpoint domain 상속이 D9 + candidate/partial 이면 자동병합 금지
    d9_involved = ("D9" in (d1, d2))
    if d9_involved and bucket in ("exact_matches", "safe_matches"):
        cand = bool(p1.get("candidate")) or bool(p2.get("candidate"))
        if cand:
            return ("d9_protected_matches", -1, "D9 edge candidate/partial 자동병합 금지")

    # watcher override (생산자 플래그만 안 믿음 — 소비처 강제 강등)
    if (p1.get("origin") == "watcher" or p2.get("origin") == "watcher") \
            and bucket in ("exact_matches", "safe_matches"):
        return ("review_candidates", tier, "watcher_edge_override: " + reason)
    return (bucket, tier, reason)


def evaluate_edges(edges, existing=None):
    """edge 페어와이즈 분류. existing=None 이면 edges 내부 페어와이즈."""
    buckets = {
        "exact_matches": [], "safe_matches": [], "review_candidates": [],
        "rejected_fuzzy_matches": [], "d9_protected_matches": [],
    }
    seen = list(existing) if existing is not None else []
    for e in edges:
        for m in seen:
            bucket, tier, reason = classify_edge_pair(e, m)
            if bucket:
                buckets[bucket].append({"a": e.get("id"), "b": m.get("id"), "tier": tier, "reason": reason})
        seen.append(e)
    return buckets


def summarize_edges(buckets):
    auto = len(buckets["exact_matches"]) + len(buckets["safe_matches"])
    if auto > 0:
        decision, reason = "STOP", f"edge 자동병합 {auto}건 (금지)"
    elif buckets["review_candidates"] or buckets["d9_protected_matches"]:
        decision, reason = "HOLD", "review/d9_protected edge 후보 — 사람 검토 필요"
    else:
        decision, reason = "GO", "edge 자동병합 후보 0"
    return {
        "edge_exact_match_count": len(buckets["exact_matches"]),
        "edge_safe_match_count": len(buckets["safe_matches"]),
        "edge_review_candidate_count": len(buckets["review_candidates"]),
        "edge_rejected_count": len(buckets["rejected_fuzzy_matches"]),
        "edge_d9_protected_count": len(buckets["d9_protected_matches"]),
        "edge_auto_merge_allowed_count": auto,
        "decision": decision, "reason": reason,
    }


def summarize(buckets, fuzzy_pairs, cross_domain_auto):
    auto_allowed = len(buckets["exact_matches"]) + len(buckets["safe_matches"])
    auto_blocked = (len(buckets["review_candidates"]) + len(buckets["rejected_fuzzy_matches"])
                    + len(buckets["d9_protected_matches"]))
    if cross_domain_auto:
        decision, reason = "STOP", f"cross-domain 자동 merge {len(cross_domain_auto)}건 (금지)"
    elif buckets["review_candidates"] or buckets["d9_protected_matches"]:
        decision, reason = "HOLD", "review/d9_protected 후보 존재 — 사람 검토 필요(자동 merge 금지)"
    else:
        decision, reason = "GO", "auto_merge 후보는 exact/strict same-domain-role 뿐, cross-domain 0"
    return {
        "graph_merge_fuzzy_dup_count": len(fuzzy_pairs),
        "localbinggu_safe_match_count": len(buckets["safe_matches"]),
        "localbinggu_exact_match_count": len(buckets["exact_matches"]),
        "localbinggu_review_candidate_count": len(buckets["review_candidates"]),
        "cross_domain_rejection_count": len(buckets["cross_domain_rejections"]),
        "rejected_fuzzy_count": len(buckets["rejected_fuzzy_matches"]),
        "d9_protected_count": len(buckets["d9_protected_matches"]),
        "auto_merge_allowed_count": auto_allowed,
        "auto_merge_blocked_count": auto_blocked,
        "cross_domain_auto_merge_count": len(cross_domain_auto),
        "decision": decision, "reason": reason,
    }
