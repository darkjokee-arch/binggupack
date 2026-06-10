#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu — review queue resolver 결선 (dry-run / sandbox only).

review_pending item 의 reviewer decision(confirm/reject/defer)을
confirmed governance(G4/G6)로 라우팅. confirm 가능 여부는 governance validator 판정.
실제 graph/store apply 는 계속 HOLD. 사람 review 전 자동 confirmed 0.

범위: in-memory + sandbox. operating store/DB write 0. apply/ingest/merge 0. raw 출력 0.
CLI: python openbinggu_review_resolver_sandbox.py --selftest
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_confirmed_governance_dryrun import evaluate_confirm  # noqa: E402


class ReviewResolver:
    """reviewer decision → governance 라우팅. apply HOLD. operating store 미접근."""

    def __init__(self):
        self.audit = []          # sandbox audit (event/actor/decision/reason_code, raw 0)
        self.confirmed_created = 0
        self.applied = 0

    def _log(self, event, actor, decision, reason_code, item_id=None):
        self.audit.append({"event": event, "actor": actor, "decision": decision,
                           "reason_code": reason_code, "item_id": item_id})

    def resolve(self, queue_item, decision, reviewer, evidence_store):
        """
        queue_item = {item_id, user_root, status(review_pending), evidence_refs, layer,
                      conflicting_user(opt)}.
        decision = confirm | reject | defer.
        reviewer = {user_root, actor(human|reader|auto), owner_approved(opt)}.
        반환: verdict + reason_code (preview only, 실 apply 0).
        """
        def out(v, rc, preview=None):
            self._log("resolve", reviewer.get("actor"), decision, rc, queue_item.get("item_id"))
            r = {"verdict": v, "reason_code": rc, "decision": decision,
                 "confirmed_created": 0, "applied": 0}
            if preview:
                r["preview"] = preview
            return r

        # 권한: reviewer user_root == item user_root (unauthorized decision 차단)
        if reviewer.get("user_root") != queue_item.get("user_root"):
            if not reviewer.get("owner_approved"):
                return out("BLOCK", "unauthorized_reviewer_decision")

        # 상태: review_pending 만 resolve 가능
        if queue_item.get("status") not in ("review_pending", "review_required"):
            return out("FAIL", "not_in_review_status")

        if decision == "reject":
            # reject: queue status preview 만(실 graph 변경 0)
            return out("PREVIEW", "reject_preview_no_apply", preview={"next_status": "rejected"})
        if decision == "defer":
            return out("PREVIEW", "defer_preview_no_apply", preview={"next_status": "review_pending"})
        if decision != "confirm":
            return out("FAIL", "unknown_decision")

        # confirm → confirmed governance(G4/G6)로 라우팅
        gov_req = {
            "item_id": queue_item.get("item_id"), "from_status": "review_pending", "to_status": "confirmed",
            "actor": reviewer.get("actor"), "user_root": reviewer.get("user_root"),
            "item_user_root": queue_item.get("user_root"),
            "evidence_refs": queue_item.get("evidence_refs"), "layer": queue_item.get("layer"),
            "conflicting_user": queue_item.get("conflicting_user"),
            "owner_approved": reviewer.get("owner_approved"),
        }
        g = evaluate_confirm(gov_req, evidence_store)
        if g["verdict"] == "ALLOW":
            # confirm_allowed preview 만 — 실제 confirmed/apply 0
            return out("CONFIRM_ALLOWED", "governance_pass_preview_only",
                       preview={"would_confirm": True, "apply": "HOLD"})
        if g["verdict"] == "REVIEW":
            return out("REVIEW", "governance_review:" + str(g.get("guard")))
        if g["verdict"] == "BLOCK":
            return out("BLOCK", "governance_block:" + str(g.get("guard")))
        # FAIL
        return out("FAIL", "governance_fail:" + str(g.get("guard")))

    def try_apply(self, item_id, reviewer):
        """confirm_allowed 여도 실제 graph/store apply 는 HOLD."""
        self._log("apply_blocked", reviewer.get("actor"), "apply", "apply_hold", item_id)
        return {"verdict": "BLOCK", "reason_code": "apply_to_graph_store_HOLD", "applied": 0}


# ---------------- selftest ----------------

def _selftest():
    ev = {"EV1": {"user_root": "user_a"}, "EV_stale": {"user_root": "user_a", "stale": True}}

    def qitem(iid, ur="user_a", refs=None, layer="subjective", conflict=None, status="review_pending"):
        return {"item_id": iid, "user_root": ur, "status": status,
                "evidence_refs": refs if refs is not None else ["EV1"], "layer": layer,
                "conflicting_user": conflict}

    def rv(ur="user_a", actor="human", approved=False):
        return {"user_root": ur, "actor": actor, "owner_approved": approved}

    rs = ReviewResolver()
    print("=" * 82)
    print("OpenBinggu — review queue resolver 결선 (synthetic / dry-run)")
    print("=" * 82)
    cases = []

    def run(name, q, dec, reviewer, ev_store, exp_v, exp_rc_prefix):
        r = rs.resolve(q, dec, reviewer, ev_store)
        ok = (r["verdict"] == exp_v) and r["reason_code"].startswith(exp_rc_prefix)
        cases.append(ok)
        print("  [%s] %-40s verdict=%-15s reason=%s" % ("OK" if ok else "FAIL", name, r["verdict"], r["reason_code"]))
        return r

    run("valid human confirm → PASS", qitem("i1"), "confirm", rv(), ev, "CONFIRM_ALLOWED", "governance_pass")
    run("auto confirm → FAIL", qitem("i2"), "confirm", rv(actor="auto"), ev, "FAIL", "governance_fail:G4_no_auto")
    run("reader confirm → FAIL", qitem("i3"), "confirm", rv(actor="reader"), ev, "FAIL", "governance_fail:G4_no_auto")
    run("stale evidence confirm → FAIL", qitem("i4", refs=["EV_stale"]), "confirm", rv(), ev, "FAIL", "governance_fail:G4_stale")
    run("cross-user confirm → BLOCK", qitem("i5", ur="user_b"), "confirm", rv(ur="user_a"), ev, "BLOCK", "unauthorized_reviewer_decision")
    run("subjective conflict confirm → REVIEW", qitem("i6", conflict="user_b"), "confirm", rv(), ev, "REVIEW", "governance_review:G6_subjective")
    run("unauthorized reviewer decision → BLOCK", qitem("i7"), "confirm", rv(ur="user_b"), ev, "BLOCK", "unauthorized_reviewer_decision")
    run("reject → preview", qitem("i8"), "reject", rv(), ev, "PREVIEW", "reject_preview")
    run("defer → preview", qitem("i9"), "defer", rv(), ev, "PREVIEW", "defer_preview")
    run("not review_pending → FAIL", qitem("i10", status="confirmed"), "confirm", rv(), ev, "FAIL", "not_in_review_status")

    # apply HOLD
    ap = rs.try_apply("i1", rv())
    ap_ok = ap["verdict"] == "BLOCK" and ap["applied"] == 0
    cases.append(ap_ok)
    print("  [%s] %-40s verdict=%s (apply HOLD)" % ("OK" if ap_ok else "FAIL", "confirm_allowed여도 apply BLOCK", ap["verdict"]))

    print("\n  confirmed_created:", rs.confirmed_created, "(0)")
    print("  applied:", rs.applied, "(0)")
    print("  audit entries(sandbox only, raw 0):", len(rs.audit))
    print("  operating_store_unchanged: True (in-memory + sandbox, FS/DB write 0)")
    gate = "GO" if (all(cases) and rs.confirmed_created == 0 and rs.applied == 0) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_review_resolver_sandbox.py [--selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
