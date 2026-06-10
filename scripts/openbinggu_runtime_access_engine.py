#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu — 런타임 접근제어 강제 엔진 (local/sandbox enforce, deny-by-default).

기존 dry-run 판정 규칙을 단일 enforce 진입점(enforce_access)으로 통합. 도구 실행 "전" gate.
권한 미충족=BLOCK. raw PII/secret/private path 미출력(reason_code/path_id/item_id 만).

통합 모듈:
  - openbinggu_path_safety_gate.classify_path        (경로 denylist·traversal·symlink)
  - openbinggu_physical_store_isolation_dryrun        (user_root namespace·shared_objective·operating store)
  - openbinggu_confirmed_governance_dryrun            (confirm G4/G6·자동 confirmed 금지)
  - openbinggu_mcp_path_gate_adapter.guarded_tool_call(도구 실행 직전 path gate + 미호출)
  - openbinggu_review_resolver_sandbox.ReviewResolver (reviewer 권한·decision)

엔진 규칙: deny-by-default / user_root 없으면 BLOCK / cross-user BLOCK / shared_objective는
objective+read-only만 ALLOW / subjective user_root 격리 / upload·shared는 승인 전 BLOCK /
reviewer 권한 없으면 review BLOCK / operating store 직접 접근 BLOCK / write·confirm·upload 자동
경로 BLOCK / apply 항상 HOLD. confirmed 자동 생성 0(preview only).

범위: local/sandbox + synthetic selftest. production/OpenCrab/store/DB write·apply/ingest/merge·
실 업로드·confirmed 자동 생성 금지. CLI: python openbinggu_runtime_access_engine.py --selftest
"""
import os
import sys
import json
import hashlib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

import openbinggu_path_safety_gate as psg
import openbinggu_physical_store_isolation_dryrun as phys
import openbinggu_confirmed_governance_dryrun as gov
import openbinggu_mcp_path_gate_adapter as mcpgate
from openbinggu_review_resolver_sandbox import ReviewResolver

# 실행 허용(도구 호출 가능) verdict. 그 외 전부 deny(도구 미호출).
SUPPORTED_ACTIONS = {"read", "write", "upload", "tool_call", "review_decision", "confirm",
                     "operating_store", "apply"}
# 자동 경로(시스템/reader)가 절대 못 하는 변경성 action.
AUTO_FORBIDDEN_ACTIONS = {"write", "upload", "confirm", "apply"}


def _id(s):
    return "ax_" + hashlib.sha256(str(s).replace("\\", "/").lower().encode("utf-8", "replace")).hexdigest()[:8]


def enforce_access(req, evidence_store=None):
    """
    deny-by-default 단일 강제 진입점. 도구 실행 전 호출.
    req = {actor:{user_root, actor(human|reader|auto), owner_approved}, action, target:{...}}.
    반환: {verdict: ALLOW|BLOCK|REVIEW, reason_code, path_id?/item_id?, executed?}.
    raw 경로/PII 미출력 — id/reason_code 만.
    """
    evidence_store = evidence_store or {}
    actor = req.get("actor") or {}
    user_root = actor.get("user_root")
    actor_kind = actor.get("actor", "human")
    action = req.get("action")
    target = req.get("target") or {}

    def block(rc, **kw):
        d = {"verdict": "BLOCK", "reason_code": rc}
        d.update(kw)
        return d

    def allow(rc=None, **kw):
        d = {"verdict": "ALLOW", "reason_code": rc}
        d.update(kw)
        return d

    # 1) deny-by-default: user_root 없으면 BLOCK
    if not user_root:
        return block("no_user_root")
    # 2) 미지원 action BLOCK
    if action not in SUPPORTED_ACTIONS:
        return block("unsupported_action")
    # 3) 자동 경로(reader/auto)는 변경성 action 금지
    if action in AUTO_FORBIDDEN_ACTIONS and actor_kind in ("auto", "reader"):
        return block("auto_path_forbidden", item_id=target.get("item_id"))
    # 4) apply 항상 HOLD (graph/store 반영 금지)
    if action == "apply":
        return block("apply_to_graph_store_HOLD", item_id=target.get("item_id"))
    # 5) operating store 직접 접근 BLOCK (명시 action)
    if action == "operating_store":
        return block("operating_store_access", path_id=_id(target.get("path", "operating")))

    # ----- 경로 기반 action: read / write / upload -----
    if action in ("read", "write", "upload"):
        path = target.get("path", "")
        op = "write" if action in ("write", "upload") else "read"
        intent = {"read": "normal", "write": "normal", "upload": "upload"}[action]
        store_req = {
            "access_path": path, "actor_user_root": user_root, "op": op, "intent": intent,
            "owner_approved": actor.get("owner_approved", False),
            "is_objective": target.get("is_objective", False),
            "shared_objective": target.get("shared_objective", False),
        }
        s = phys.check_store_access(store_req)
        if s["verdict"] != "PASS":
            return block(s["reason_code"], path_id=s["path_id"])
        # 경로 denylist/traversal/symlink 2차 (allow_root = users/<user_root>)
        allow_root = phys.build_allow_root(user_root)
        p = psg.classify_path(path, allow_root, symlink_detected=target.get("symlink_detected", False))
        if p["verdict"] != "ALLOW":
            return block(p["reason_code"], path_id=p["path_id"])
        return allow("authorized_" + action, path_id=s["path_id"])

    # ----- 도구 호출: MCP path gate(실행 직전) -----
    if action == "tool_call":
        allow_root = target.get("allow_root") or phys.build_allow_root(user_root)
        # tool_fn 은 sandbox noop — 실제 도구/외부 호출 0. gate BLOCK 시 미호출.
        r = mcpgate.guarded_tool_call(
            lambda: {"sandbox_noop": True},
            path_inputs=target.get("path_inputs", []),
            allow_root=allow_root, recheck=True,
        )
        if not r.get("executed"):
            blk = r.get("blocked") or []
            rc = blk[0].get("reason_code", "tool_path_block") if blk else "tool_path_block"
            return block(rc, executed=False)
        return allow("tool_path_clean", executed=True)

    # ----- review decision: reviewer 권한 + governance -----
    if action == "review_decision":
        resolver = ReviewResolver()
        res = resolver.resolve(
            target.get("queue_item", {}), target.get("decision", "confirm"),
            {"user_root": user_root, "actor": actor_kind,
             "owner_approved": actor.get("owner_approved", False)},
            evidence_store,
        )
        v = res["verdict"]
        if v in ("CONFIRM_ALLOWED", "PREVIEW"):
            # decision 처리 허용(preview). 실제 confirmed/apply 0.
            return allow("review_" + res["reason_code"], item_id=target.get("queue_item", {}).get("item_id"),
                         confirmed_created=0)
        if v == "REVIEW":
            return {"verdict": "REVIEW", "reason_code": res["reason_code"],
                    "item_id": target.get("queue_item", {}).get("item_id")}
        return block(res["reason_code"], item_id=target.get("queue_item", {}).get("item_id"))

    # ----- confirm: governance 직접(자동은 위에서 차단됨) -----
    if action == "confirm":
        gov_req = dict(target.get("gov_req", {}))
        gov_req.setdefault("user_root", user_root)
        gov_req.setdefault("actor", actor_kind)
        g = gov.evaluate_confirm(gov_req, evidence_store)
        if g["verdict"] == "ALLOW":
            # 강제 엔진은 confirmed 를 "생성"하지 않음 — would_confirm preview only.
            return allow("confirm_preview_only", item_id=g.get("item_id"),
                         confirmed_created=0, apply="HOLD")
        if g["verdict"] == "REVIEW":
            return {"verdict": "REVIEW", "reason_code": g.get("guard"), "item_id": g.get("item_id")}
        return block(g.get("guard") or "governance_fail", item_id=g.get("item_id"))

    # deny-by-default 최종 백스톱
    return block("deny_by_default")


# ---------------- selftest ----------------

def _selftest():
    ev = {
        "EV_ok": {"user_root": "user_a", "layer": "subjective"},
        "EV_stale": {"user_root": "user_a", "stale": True},
    }

    def actor(ur="user_a", kind="human", approved=False):
        return {"user_root": ur, "actor": kind, "owner_approved": approved}

    def qitem(iid, ur="user_a", refs=None, layer="subjective", status="review_pending"):
        return {"item_id": iid, "user_root": ur, "status": status,
                "evidence_refs": refs or ["EV_ok"], "layer": layer}

    cases = [
        # name, req, expect_verdict, expect_reason(prefix or None)
        ("authorized own read PASS",
         {"actor": actor(), "action": "read", "target": {"path": "users/user_a/evidence/e1.json"}},
         "ALLOW", "authorized_read"),
        ("own write to sandbox candidate PASS",
         {"actor": actor(), "action": "write", "target": {"path": "users/user_a/packs/candidate/p1.json"}},
         "ALLOW", "authorized_write"),
        ("cross-user read BLOCK",
         {"actor": actor("user_a"), "action": "read", "target": {"path": "users/user_b/evidence/e9.json"}},
         "BLOCK", "deny_cross_user_root"),
        ("cross-user write BLOCK",
         {"actor": actor("user_a"), "action": "write", "target": {"path": "users/user_b/packs/p1.json"}},
         "BLOCK", "deny_cross_user_root"),
        ("shared_objective read PASS",
         {"actor": actor(), "action": "read",
          "target": {"path": "shared_objective/fact1.json", "is_objective": True, "shared_objective": True}},
         "ALLOW", "authorized_read"),
        ("shared_objective write BLOCK",
         {"actor": actor(), "action": "write",
          "target": {"path": "shared_objective/fact1.json", "is_objective": True, "shared_objective": True}},
         "BLOCK", "shared_objective_write"),
        ("upload no approval BLOCK",
         {"actor": actor(approved=False), "action": "upload", "target": {"path": "users/user_a/packs/up.json"}},
         "BLOCK", "upload_no_approval"),
        ("upload approval PASS",
         {"actor": actor(approved=True), "action": "upload", "target": {"path": "users/user_a/packs/up.json"}},
         "ALLOW", "authorized_upload"),
        ("unauthorized reviewer BLOCK",
         {"actor": actor("user_a"), "action": "review_decision",
          "target": {"queue_item": qitem("i7", ur="user_b"), "decision": "confirm"}},
         "BLOCK", "unauthorized_reviewer_decision"),
        ("authorized reviewer confirm preview ALLOW",
         {"actor": actor("user_a"), "action": "review_decision",
          "target": {"queue_item": qitem("i1"), "decision": "confirm"}},
         "ALLOW", "review_governance_pass_preview_only"),
        ("MCP tool path escape BLOCK",
         {"actor": actor(), "action": "tool_call",
          "target": {"path_inputs": ["C:/Users/PC/safety-app/bid-engine/app/worker.py"]}},
         "BLOCK", "deny_bid_engine"),
        ("MCP tool clean path ALLOW",
         {"actor": actor(), "action": "tool_call",
          "target": {"path_inputs": ["users/user_a/packs/p1.json"], "allow_root": "users/user_a"}},
         "ALLOW", "tool_path_clean"),
        ("operating store access BLOCK",
         {"actor": actor(), "action": "operating_store", "target": {"path": "user_graph.yaml"}},
         "BLOCK", "operating_store_access"),
        ("operating store via read path BLOCK",
         {"actor": actor(), "action": "read", "target": {"path": "users/user_a/_graph_merge.yaml"}},
         "BLOCK", "operating_store_access"),
        ("confirm auto path BLOCK",
         {"actor": actor(kind="auto"), "action": "confirm",
          "target": {"gov_req": {"item_id": "i20", "from_status": "review_pending",
                                 "to_status": "confirmed", "evidence_refs": ["EV_ok"]}}},
         "BLOCK", "auto_path_forbidden"),
        ("write auto path BLOCK",
         {"actor": actor(kind="auto"), "action": "write", "target": {"path": "users/user_a/packs/x.json"}},
         "BLOCK", "auto_path_forbidden"),
        ("apply always HOLD BLOCK",
         {"actor": actor(), "action": "apply", "target": {"item_id": "i30"}},
         "BLOCK", "apply_to_graph_store_HOLD"),
        ("no user_root BLOCK",
         {"actor": {"actor": "human"}, "action": "read", "target": {"path": "users/user_a/e1.json"}},
         "BLOCK", "no_user_root"),
        ("unsupported action BLOCK",
         {"actor": actor(), "action": "delete_all", "target": {}},
         "BLOCK", "unsupported_action"),
        ("path traversal BLOCK",
         {"actor": actor(), "action": "read", "target": {"path": "users/user_a/../user_b/e.json"}},
         "BLOCK", "path_traversal"),
        ("confirm human stale evidence BLOCK",
         {"actor": actor(), "action": "confirm",
          "target": {"gov_req": {"item_id": "i40", "from_status": "review_pending",
                                 "to_status": "confirmed", "evidence_refs": ["EV_stale"]}}},
         "BLOCK", "G4_stale"),
    ]

    print("=" * 80)
    print("OpenBinggu — 런타임 접근제어 강제 엔진 selftest (deny-by-default)")
    print("=" * 80)
    npass = 0
    leak = 0
    for name, req, exp_v, exp_rc in cases:
        r = enforce_access(req, ev)
        v_ok = r["verdict"] == exp_v
        rc_ok = (exp_rc is None) or (str(r.get("reason_code", "")) == exp_rc)
        # raw 경로/PII 누출 검사: 값에 절대경로·.json 풀path·secret 흔적 없어야(id/reason만)
        blob = json.dumps(r, ensure_ascii=False)
        if "C:/" in blob or "C:\\" in blob or ".pem" in blob or "id_rsa" in blob:
            leak += 1
        ok = v_ok and rc_ok
        npass += ok
        print("  [%s] %-46s verdict=%-6s reason=%s" %
              ("OK" if ok else "FAIL", name[:46], r["verdict"], r.get("reason_code")))

    print("\n  raw_leak:", leak, "(0 이어야)")
    # 강제 엔진은 confirmed 를 만들지 않음
    conf_zero = all(enforce_access(c[1], ev).get("confirmed_created", 0) == 0
                    for c in cases if c[1].get("action") in ("confirm", "review_decision"))
    print("  confirmed_created_always_0:", conf_zero)
    gate = "GO" if (npass == len(cases) and leak == 0 and conf_zero) else "NO-GO"
    print("\n  RESULT: %d/%d  GATE=%s" % (npass, len(cases), gate))
    return 0 if gate == "GO" else 1


def main():
    if "--selftest" in sys.argv:
        return _selftest()
    print("usage: python openbinggu_runtime_access_engine.py --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
