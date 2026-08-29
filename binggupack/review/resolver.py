# -*- coding: utf-8 -*-
"""binggupack.review.resolver — LocalBinggu review decision 라우팅 순수 로직 정본 (read-only).

review decision 을 apply_plan 에 반영해 reviewed plan + audit 을 생성하는 파이프라인 중,
파일 I/O 무관한 순수 라우팅(resolve·items_from_plan + APPROVE/HOLD_DECISIONS 상수)만 담는다.

strangler: scripts/localbinggu_review_resolver.py 에서 순수 라우팅을 byte-identical 이관한
정본이다. 판정 로직은 1바이트도 변하지 않았다. 파일 I/O(load_decisions·write_reports·
run_fixtures·main·BASE/reports 경로)는 __file__/fixture 경로에 의존하므로 scripts 에 잔류한다.

기존엔 --fixture-dir 로만 검증됐고 --selftest 가 없었다(검증수단 부재). 이관하며 순수 라우팅
_selftest 를 신설(인메모리 · 파일 I/O 0)해 검증수단을 부여한다.

공개 API:
  - resolve(items, decisions) -> (audit, buckets, decision, why)
  - items_from_plan(plan) -> items
  - APPROVE / HOLD_DECISIONS
"""

APPROVE = {"approve_safe_merge", "approve_insert"}
HOLD_DECISIONS = {"defer", "edit_required", ""}


def resolve(items, decisions):
    """items: [{review_id, kind, node}] (kind: review|d9_protected|cross_domain|insert)
    decisions: {review_id: decision}
    반환: audit(list), buckets, decision"""
    audit = []
    applied, excluded, held, stopped = [], [], [], []

    for it in items:
        rid = it["review_id"]
        kind = it.get("kind", "review")
        dec = decisions.get(rid, "")
        if dec in APPROVE:
            if kind == "d9_protected":
                result, reason = "STOPPED", "D9 protected 는 approve 불가 (보호 상태 변경 금지)"
                stopped.append(rid)
            elif kind == "cross_domain":
                result, reason = "STOPPED", "cross-domain rejected 는 approve 불가"
                stopped.append(rid)
            else:
                # safety: promotion_allowed=false 유지, evidence_refs 유지 가정(노드 자체 미변경)
                result, reason = "APPLIED", f"{dec} 승인 → apply candidate"
                applied.append(rid)
        elif dec == "reject":
            result, reason = "EXCLUDED", "reject → 적용 제외"
            excluded.append(rid)
        elif dec in HOLD_DECISIONS:
            result, reason = "HELD", ("미결정 → HOLD 유지" if dec == "" else f"{dec} → HOLD 유지")
            held.append(rid)
        else:
            result, reason = "HELD", f"알 수 없는 decision '{dec}' → HOLD 유지"
            held.append(rid)

        audit.append({"review_id": rid, "kind": kind, "node": it.get("node"),
                      "decision": dec, "result": result, "reason": reason,
                      "promotion_allowed": False})

    if stopped:
        decision, why = "STOP", f"approve 불가 항목 approve 시도: {stopped}"
    elif held:
        decision, why = "HOLD", f"미해결(defer/edit_required/미결정) 항목 잔존: {held}"
    else:
        decision, why = "GO", "review-only 전부 approve/reject 처리, 잔여 HOLD 없음, STOP 신호 없음"

    return audit, {"applied": applied, "excluded": excluded, "held": held, "stopped": stopped}, decision, why


def items_from_plan(plan):
    rw = plan.get("review_workflow", {})
    items = []
    for it in rw.get("review_items", []):
        items.append({"review_id": None, "kind": "review", "node": it.get("a"), "raw": it})
    for it in rw.get("d9_protected_items", []):
        items.append({"review_id": None, "kind": "d9_protected", "node": it.get("a"), "raw": it})
    for it in rw.get("cross_domain_items", []):
        items.append({"review_id": None, "kind": "cross_domain", "node": it.get("a"), "raw": it})
    # review_id 부여 (review-only 만 REV-, 나머지는 참고용 id)
    rev = 1
    for it in items:
        if it["kind"] == "review":
            it["review_id"] = f"REV-{rev:03d}"; rev += 1
        else:
            it["review_id"] = f"{it['kind'].upper()}-{it['node']}"
    return items


# ---------------- selftest (순수 라우팅 · 인메모리 · write 0 — 이관 시 신설) ----------------
def _selftest():
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    def it(rid, kind="review", node="n"):
        return {"review_id": rid, "kind": kind, "node": node}

    # 1) review + approve_safe_merge → APPLIED, 전체 GO
    audit, buckets, decision, why = resolve([it("REV-001")], {"REV-001": "approve_safe_merge"})
    ck(audit[0]["result"] == "APPLIED" and decision == "GO" and buckets["applied"] == ["REV-001"],
       "review approve_safe_merge → APPLIED · decision GO")

    # 2) d9_protected + approve → STOPPED, 전체 STOP (보호 상태 변경 금지)
    audit, buckets, decision, why = resolve([it("D9_PROTECTED-n", kind="d9_protected")],
                                            {"D9_PROTECTED-n": "approve_insert"})
    ck(audit[0]["result"] == "STOPPED" and decision == "STOP" and buckets["stopped"],
       "d9_protected approve → STOPPED · decision STOP")

    # 3) cross_domain + approve → STOPPED, 전체 STOP
    audit, buckets, decision, why = resolve([it("CROSS_DOMAIN-n", kind="cross_domain")],
                                            {"CROSS_DOMAIN-n": "approve_safe_merge"})
    ck(audit[0]["result"] == "STOPPED" and decision == "STOP",
       "cross_domain approve → STOPPED · decision STOP")

    # 4) review + reject → EXCLUDED, 잔여 HOLD 없으면 GO
    audit, buckets, decision, why = resolve([it("REV-001")], {"REV-001": "reject"})
    ck(audit[0]["result"] == "EXCLUDED" and decision == "GO" and buckets["excluded"] == ["REV-001"],
       "review reject → EXCLUDED · decision GO")

    # 5) review + defer → HELD, 전체 HOLD
    audit, buckets, decision, why = resolve([it("REV-001")], {"REV-001": "defer"})
    ck(audit[0]["result"] == "HELD" and decision == "HOLD" and buckets["held"] == ["REV-001"],
       "review defer → HELD · decision HOLD")

    # 6) review + 미결정("") → HELD, 전체 HOLD
    audit, buckets, decision, why = resolve([it("REV-001")], {})
    ck(audit[0]["result"] == "HELD" and audit[0]["decision"] == "" and decision == "HOLD",
       "미결정(decision 없음) → HELD · decision HOLD")

    # 7) review + 알 수 없는 decision → HELD (fail-safe, apply 안 함)
    audit, buckets, decision, why = resolve([it("REV-001")], {"REV-001": "weird_unknown"})
    ck(audit[0]["result"] == "HELD" and decision == "HOLD",
       "알 수 없는 decision → HELD (fail-safe)")

    # 8) 혼합 — approve + reject 만, held/stopped 없음 → GO
    audit, buckets, decision, why = resolve(
        [it("REV-001"), it("REV-002")],
        {"REV-001": "approve_insert", "REV-002": "reject"})
    ck(decision == "GO" and buckets["applied"] == ["REV-001"] and buckets["excluded"] == ["REV-002"],
       "approve+reject 혼합, 잔여 HOLD 없음 → GO")

    # 9) STOP 우선순위 — stopped 있으면 held 있어도 STOP
    audit, buckets, decision, why = resolve(
        [it("REV-001"), it("D9_PROTECTED-n", kind="d9_protected")],
        {"REV-001": "defer", "D9_PROTECTED-n": "approve_safe_merge"})
    ck(decision == "STOP", "stopped 존재 시 held 있어도 decision STOP (우선순위)")

    # 10) promotion_allowed 항상 False (승격 금지 불변)
    audit, _, _, _ = resolve([it("REV-001")], {"REV-001": "approve_safe_merge"})
    ck(all(a["promotion_allowed"] is False for a in audit), "audit 전건 promotion_allowed=False")

    # 11) items_from_plan — kind별 review_id 부여 (review=REV-, 나머지=KIND-node)
    plan = {"review_workflow": {
        "review_items": [{"a": "x"}, {"a": "y"}],
        "d9_protected_items": [{"a": "p1"}],
        "cross_domain_items": [{"a": "c1"}]}}
    items = items_from_plan(plan)
    rev_ids = [i["review_id"] for i in items if i["kind"] == "review"]
    other_ids = [i["review_id"] for i in items if i["kind"] != "review"]
    ck(rev_ids == ["REV-001", "REV-002"] and "D9_PROTECTED-p1" in other_ids
       and "CROSS_DOMAIN-c1" in other_ids,
       "items_from_plan → review=REV-순번 · 보호/교차=KIND-node id")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
