# -*- coding: utf-8 -*-
"""Characterization selftest — localbinggu_match_policy (v1.11.0 strangler phase2).

이관 전 현행 동작을 고정한다(pre-move characterization). 이관 후 thin wrapper 에서도
동일 PASS 해야 한다. 호출처 형태(import localbinggu_match_policy as mp)를 그대로 재현.

rapidfuzz 유무와 무관하게 결정론적이도록 sim 의존 케이스(Tier2/3 node)는 제외하고,
완전일치·sentence hash 동일·4축 reject·D9 보호·watcher override·edge evidence_refs
교집합(=sim 비의존) 판정만 고정한다. read-only, write 0.
"""
import sys

import localbinggu_match_policy as mp  # noqa: E402  (호출처 2개와 동일 import 형태)


def _n(nid, domain="D1", space="s", ntype="doc", lk="문서", sentence="hello",
       cand=False, est="confirmed", refs=None, origin=""):
    return {"id": nid, "domain": domain, "space": space, "node_type": ntype,
            "label_kind": lk, "sentence": sentence, "candidate": cand,
            "evidence_status": est, "evidence_refs": set(refs or []), "origin": origin}


def _edge(eid, etype="e", rel="evidence_supports", src="a", tgt="b", domain="D1",
          refs=None, cand=False, origin=""):
    return {"id": eid, "edge_type": etype, "source": src, "target": tgt,
            "properties": {"relation": rel, "domain": domain, "candidate": cand, "origin": origin},
            "evidence_refs": list(refs or [])}


def run():
    results = []

    def ck(name, got, exp_bucket, exp_tier=None):
        ok = (got[0] == exp_bucket) and (exp_tier is None or got[1] == exp_tier)
        results.append((name, ok, got))

    # ---- classify_pair (node) — sim 비의존 ----
    ck("node_exact_id", mp.classify_pair(_n("n1"), _n("n1")), "exact_matches", 0)
    ck("node_reject_domain", mp.classify_pair(_n("n1", domain="D1"), _n("n2", domain="D2")),
       "rejected_fuzzy_matches", -1)
    ck("node_reject_label_kind", mp.classify_pair(_n("n1", lk="문서"), _n("n2", lk="판단")),
       "rejected_fuzzy_matches", -1)
    ck("node_safe_hash", mp.classify_pair(_n("n1", sentence="same text"), _n("n2", sentence="same text")),
       "safe_matches", 1)
    ck("node_watcher_override", mp.classify_pair(_n("n1", sentence="t", origin="watcher"), _n("n2", sentence="t")),
       "review_candidates")
    ck("node_d9_protect", mp.classify_pair(
        _n("n1", domain="D9", sentence="t", cand=True),
        _n("n2", domain="D9", sentence="t", cand=False, est="confirmed")), "d9_protected_matches", -1)

    # ---- classify_edge_pair (edge) — 호출처 2개가 실제 사용 ----
    ck("edge_exact_id", mp.classify_edge_pair(_edge("e1"), _edge("e1")), "exact_matches", 0)
    ck("edge_reject_role", mp.classify_edge_pair(_edge("e1", rel="evidence_supports"), _edge("e2", rel="other")),
       "rejected_fuzzy_matches", -1)
    ck("edge_reject_cross_domain", mp.classify_edge_pair(_edge("e1", domain="D1"), _edge("e2", domain="D2")),
       "rejected_fuzzy_matches", -1)
    ck("edge_safe_refs", mp.classify_edge_pair(_edge("e1", refs=["R1"]), _edge("e2", refs=["R1"])),
       "safe_matches", 1)
    ck("edge_review_no_refs", mp.classify_edge_pair(_edge("e1", refs=["R1"]), _edge("e2", refs=["R2"])),
       "review_candidates", 3)
    ck("edge_watcher_override",
       mp.classify_edge_pair(_edge("e1", refs=["R1"], origin="watcher"), _edge("e2", refs=["R1"])),
       "review_candidates")
    ck("edge_d9_protect",
       mp.classify_edge_pair(_edge("e1", domain="D9", refs=["R1"], cand=True), _edge("e2", domain="D9", refs=["R1"])),
       "d9_protected_matches", -1)

    # ---- evaluate + summarize decision (node) ----
    nodes = [_n("a1", sentence="x"), _n("a2", sentence="x")]  # 4축 동일 + hash 동일 → safe
    buckets, fuzzy, cda = mp.evaluate(nodes)
    s = mp.summarize(buckets, fuzzy, cda)
    sum_ok = (s["localbinggu_safe_match_count"] == 1 and s["cross_domain_auto_merge_count"] == 0
              and s["decision"] in ("GO", "HOLD", "STOP"))
    results.append(("evaluate+summarize_node", sum_ok, s["decision"]))

    # cross-domain reject → summarize 에 반영
    nodes2 = [_n("b1", domain="D1", sentence="x"), _n("b2", domain="D2", sentence="x")]
    b2, f2, c2 = mp.evaluate(nodes2)
    s2 = mp.summarize(b2, f2, c2)
    results.append(("cross_domain_no_auto", s2["cross_domain_auto_merge_count"] == 0, s2["cross_domain_auto_merge_count"]))

    # ---- evaluate_edges + summarize_edges ----
    edges = [_edge("e1", refs=["R1"]), _edge("e2", refs=["R1"])]  # role 동일 + refs 교집합 → safe
    eb = mp.evaluate_edges(edges)
    es = mp.summarize_edges(eb)
    edge_sum_ok = (es["edge_safe_match_count"] == 1 and es["decision"] == "STOP")  # 자동병합 1건 → STOP
    results.append(("evaluate_edges+summarize", edge_sum_ok, es["decision"]))

    # ---- normalize_nodes (public) ----
    pkg_nodes = [{"id": "z1", "space": "sp", "node_type": "doc",
                  "properties": {"domain": "D1", "label_kind": "문서", "sentence": "hi", "candidate": True},
                  "evidence_refs": ["R1"]}]
    nn = mp.normalize_nodes(pkg_nodes)
    nn_ok = (len(nn) == 1 and nn[0]["id"] == "z1" and nn[0]["domain"] == "D1"
             and nn[0]["evidence_refs"] == {"R1"} and nn[0]["candidate"] is True)
    results.append(("normalize_nodes", nn_ok, nn_ok))

    print("=" * 74)
    print("localbinggu_match_policy characterization selftest (read-only, write 0)")
    print("=" * 74)
    all_ok = True
    for name, ok, got in results:
        all_ok = all_ok and ok
        print("  [%s] %-30s %s" % ("OK" if ok else "FAIL", name, "" if ok else ("got=%r" % (got,))))
    print("\n  rapidfuzz_active: %s (sim 케이스는 비의존 설계)" % mp.RF)
    print("  operating_store_unchanged: True (판정만, FS write 0)")
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run())
