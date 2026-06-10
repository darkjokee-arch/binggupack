#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu — confirmed governance validator (G4 status 전이 / G6 멀티유저 충돌) (dry-run only).

candidate node/edge를 confirmed로 올리는 기준. 자동 confirmed 금지·review 전 confirmed 0.
헌법 제8조 G4/G6 + A1 user_root 격리 + A3 reader(confirmed 불가) 전제.

G4 status: candidate → review_pending → confirmed/rejected. stale evidence면 confirmed 금지.
G6 충돌: A subjective 판단은 A graph에만 confirmed. A/B 충돌 시 global confirmed 금지.
         objective fact는 evidence 직접성+source 기준 통과 시 공유. cross-user confirmed는 승인 전 금지.

범위: 판정 + synthetic selftest. operating store write 0. apply/ingest/merge 0.
CLI: python openbinggu_confirmed_governance_dryrun.py --selftest
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_a0_node_dryrun import LAYER  # noqa: E402

ALLOWED_STATUS = {"candidate", "review_pending", "confirmed", "rejected"}
# 허용 전이
TRANSITIONS = {
    ("candidate", "review_pending"),
    ("review_pending", "confirmed"),
    ("review_pending", "rejected"),
    ("confirmed", "review_required"),   # evidence invalidated 후 강등
}


def evaluate_confirm(req, evidence_store):
    """
    confirm 요청 평가. req = {item_id, kind(node|edge), from_status, to_status,
        actor(human|reader|auto), user_root, item_user_root, evidence_refs,
        layer(objective|subjective), conflicting_user(opt), owner_approved(opt)}.
    반환 verdict: ALLOW / REVIEW / FAIL / BLOCK + reason + guard.
    """
    def out(v, reason, guard):
        return {"verdict": v, "reason": reason, "guard": guard, "item_id": req.get("item_id")}

    frm, to = req.get("from_status"), req.get("to_status")

    # 자동 confirmed / reader confirmed 금지
    if to == "confirmed" and req.get("actor") in ("auto", "reader"):
        return out("FAIL", "자동/reader confirmed 금지(사람 review 필수)", "G4_no_auto_confirm")

    # 전이 유효성
    if to not in ALLOWED_STATUS and to != "review_required":
        return out("FAIL", "비허용 status", "G4_status")
    if (frm, to) not in TRANSITIONS:
        return out("FAIL", "비허용 status 전이", "G4_transition")

    # confirmed 로 가는 모든 경우: evidence_refs 필수 + A1 user_root 격리
    if to == "confirmed":
        if not req.get("evidence_refs"):
            return out("FAIL", "evidence_refs 없는 confirmed", "G4_no_evidence")
        # A1: item user_root == actor user_root (cross-user 금지)
        if req.get("item_user_root") and req.get("user_root") != req["item_user_root"]:
            if not req.get("owner_approved"):
                return out("BLOCK", "cross-user confirmed 승인 전 차단", "G6_cross_user_approval")
        # stale evidence: 변경/삭제/오염 시 confirmed 금지
        for ref in req.get("evidence_refs", []):
            ev = evidence_store.get(ref)
            if ev is None:
                return out("FAIL", "evidence 삭제됨(stale) → confirmed 금지", "G4_stale_deleted")
            if ev.get("stale") or ev.get("invalidated"):
                return out("FAIL", "evidence stale/오염 → 재검증 필요", "G4_stale")
        # G6: subjective 판단 충돌
        if req.get("layer") == "subjective" and req.get("conflicting_user"):
            return out("REVIEW", "A/B subjective 판단 충돌 → global confirmed 금지(review)", "G6_subjective_conflict")
        # G6: objective fact 충돌은 subjective 쪽을 review로 — objective 자체는 source/직접성 통과 시 PASS
        # (objective confirmed PASS 조건: evidence_refs + 직접성은 호출부 G1에서 확인됨)
        return out("ALLOW", "confirmed 허용(사람 review + evidence + 격리 통과)", None)

    # confirmed 후 evidence invalidated → review_required 강등
    if frm == "confirmed" and to == "review_required":
        for ref in req.get("evidence_refs", []):
            ev = evidence_store.get(ref)
            if ev is None or ev.get("invalidated") or ev.get("stale"):
                return out("REVIEW", "confirmed 후 evidence invalidated → review_required", "G4_post_invalidate")
        return out("ALLOW", "강등 사유 없음(전이만)", None)

    # candidate → review_pending 등 중간 전이
    return out("ALLOW", "중간 status 전이 허용", None)


# ---------------- selftest ----------------

def _selftest():
    ev = {
        "EV_ok": {"user_root": "user_a", "layer": "subjective"},
        "EV_obj": {"user_root": "user_a", "layer": "objective", "shared_objective": True},
        "EV_stale": {"user_root": "user_a", "stale": True},
        "EV_inval": {"user_root": "user_a", "invalidated": True},
    }

    def req(iid, frm, to, actor="human", refs=None, ur="user_a", item_ur="user_a",
            layer="subjective", conflict=None, approved=False):
        return {"item_id": iid, "kind": "edge", "from_status": frm, "to_status": to,
                "actor": actor, "user_root": ur, "item_user_root": item_ur,
                "evidence_refs": refs or [], "layer": layer,
                "conflicting_user": conflict, "owner_approved": approved}

    cases = [
        ("candidate→review_pending", req("i1", "candidate", "review_pending", refs=["EV_ok"]), "ALLOW", None),
        ("review_pending→confirmed(정상)", req("i2", "review_pending", "confirmed", refs=["EV_ok"]), "ALLOW", None),
        ("자동_confirmed_시도", req("i3", "review_pending", "confirmed", actor="auto", refs=["EV_ok"]), "FAIL", "G4_no_auto_confirm"),
        ("reader_confirmed_시도", req("i4", "review_pending", "confirmed", actor="reader", refs=["EV_ok"]), "FAIL", "G4_no_auto_confirm"),
        ("evidence없는_confirmed", req("i5", "review_pending", "confirmed", refs=[]), "FAIL", "G4_no_evidence"),
        ("stale_evidence_confirmed", req("i6", "review_pending", "confirmed", refs=["EV_stale"]), "FAIL", "G4_stale"),
        ("삭제된_evidence_confirmed", req("i7", "review_pending", "confirmed", refs=["EV_gone"]), "FAIL", "G4_stale_deleted"),
        ("AB_subjective_충돌", req("i8", "review_pending", "confirmed", refs=["EV_ok"], conflict="user_b"), "REVIEW", "G6_subjective_conflict"),
        ("objective_fact_confirmed", req("i9", "review_pending", "confirmed", refs=["EV_obj"], layer="objective"), "ALLOW", None),
        ("cross_user_confirmed_승인전", req("i10", "review_pending", "confirmed", refs=["EV_ok"], item_ur="user_b"), "BLOCK", "G6_cross_user_approval"),
        ("cross_user_confirmed_승인후", req("i11", "review_pending", "confirmed", refs=["EV_ok"], item_ur="user_b", approved=True), "ALLOW", None),
        ("비허용_전이(candidate→confirmed)", req("i12", "candidate", "confirmed", refs=["EV_ok"]), "FAIL", "G4_transition"),
        ("confirmed후_evidence_invalidated", req("i13", "confirmed", "review_required", refs=["EV_inval"]), "REVIEW", "G4_post_invalidate"),
    ]

    print("=" * 78)
    print("OpenBinggu — confirmed governance (G4/G6) validator (synthetic / selftest)")
    print("=" * 78)
    all_ok = True
    confirmed_auto = 0
    for name, r, exp_v, exp_g in cases:
        res = evaluate_confirm(r, ev)
        ok = (res["verdict"] == exp_v) and ((exp_g is None) or (res["guard"] == exp_g))
        all_ok = all_ok and ok
        if res["verdict"] == "ALLOW" and r.get("to_status") == "confirmed" and r.get("actor") in ("auto", "reader"):
            confirmed_auto += 1
        print("  [%s] %-34s verdict=%-7s guard=%s" % ("OK" if ok else "FAIL", name, res["verdict"], res["guard"]))

    print("\n  auto/reader_confirmed_allowed:", confirmed_auto, "(0 이어야)")
    print("  operating_store_unchanged: True (판정만, FS write 0)")
    gate = "GO" if (all_ok and confirmed_auto == 0) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_confirmed_governance_dryrun.py [--selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
