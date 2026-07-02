#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu — 물리 store 격리(physical store isolation) validator (dry-run only).

user_root별 파일/DB/pack 저장 경로를 물리 분리. user A context 에서 user B 경로 접근 BLOCK.
A1 논리 격리(user_root 필드)를 path/store namespace 격리로 확장. path_safety_gate 연동.

물리 경로 규칙:
  users/<user_root>/evidence/   users/<user_root>/reviews/   users/<user_root>/packs/
  shared_objective/             ← source/evidence 기준 읽기 전용 공유
  operating store(_graph_merge.yaml·user_graph.yaml 등) ← 직접 접근 BLOCK

범위: 판정 + synthetic selftest. operating store write 0. 실제 파일/DB 생성 0. raw 경로 미출력.
CLI: python openbinggu_physical_store_isolation_dryrun.py --selftest
"""
import sys
import re
import hashlib

OPERATING_STORE = re.compile(r"_graph_merge\.yaml|user_graph\.yaml|localcrab_index\.sqlite|operating_store|production_graph")
USERS_RE = re.compile(r"(?:^|/)users/([^/]+)/")
SHARED_RE = re.compile(r"(?:^|/)shared_objective/")


def _path_id(s):
    return "sp_" + hashlib.sha256(s.replace("\\", "/").lower().encode("utf-8", "replace")).hexdigest()[:8]


def check_store_access(req):
    """
    물리 store 접근 판정. req = {access_path, actor_user_root, op(read|write),
        intent(normal|upload|shared), owner_approved(opt), is_objective(opt), shared_objective(opt)}.
    반환 verdict: PASS / BLOCK + reason_code + path_id (raw 경로 미출력).
    """
    p = (req.get("access_path") or "").replace("\\", "/")
    ur = req.get("actor_user_root")
    pid = _path_id(p)

    def out(v, rc):
        return {"verdict": v, "reason_code": rc, "path_id": pid}

    if not p.strip():
        return out("BLOCK", "empty_path")
    if not ur:
        return out("BLOCK", "no_actor_user_root")

    # path traversal(.. / 절대경로 탈출)
    if ".." in p.split("/"):
        return out("BLOCK", "path_traversal")

    # operating store 직접 접근 금지
    if OPERATING_STORE.search(p):
        return out("BLOCK", "operating_store_access")

    # shared_objective: 읽기 전용 + objective 만
    m_shared = SHARED_RE.search(p)
    if m_shared:
        if req.get("op") == "write":
            return out("BLOCK", "shared_objective_write")
        if not (req.get("is_objective") and req.get("shared_objective")):
            return out("BLOCK", "shared_objective_requires_objective_flag")
        return out("PASS", "shared_objective_read")

    # users/<root>/ 경로: actor user_root 와 일치해야
    m = USERS_RE.search(p)
    if m:
        path_root = m.group(1)
        if path_root != ur:
            return out("BLOCK", "deny_cross_user_root")
        # subjective(판단) cross-user 는 위에서 path_root 불일치로 차단됨.
        # upload/shared intent 는 owner 승인 전 BLOCK
        if req.get("intent") in ("upload", "shared") and not req.get("owner_approved"):
            return out("BLOCK", "upload_no_approval")
        return out("PASS", "own_user_root")

    # users/ 도 shared_objective 도 아닌 경로 = 미허용 영역
    return out("BLOCK", "outside_user_namespace")


# ---- path_safety_gate 연동 후보: allow_root = 현재 user_root 경로 ----
def build_allow_root(user_root, base="users"):
    """path_safety_gate 의 allow_root = users/<user_root>. 타 user_root 는 자연 deny(outside_root)."""
    return "%s/%s" % (base, user_root)


# ---------------- selftest ----------------

def _selftest():
    def req(path, ur="user_a", op="read", intent="normal", approved=False, obj=False, shared=False):
        return {"access_path": path, "actor_user_root": ur, "op": op, "intent": intent,
                "owner_approved": approved, "is_objective": obj, "shared_objective": shared}

    cases = [
        ("자기경로_read", req("users/user_a/evidence/e1.json"), "PASS", "own_user_root"),
        ("자기경로_reviews", req("users/user_a/reviews/r1.json", op="write"), "PASS", "own_user_root"),
        ("타user_graph_접근", req("users/user_b/evidence/e9.json"), "BLOCK", "deny_cross_user_root"),
        ("타user_pack_접근", req("users/user_b/packs/p1.json"), "BLOCK", "deny_cross_user_root"),
        ("shared_objective_read(flag)", req("shared_objective/fact1.json", obj=True, shared=True), "PASS", "shared_objective_read"),
        ("shared_objective_write_금지", req("shared_objective/fact1.json", op="write", obj=True, shared=True), "BLOCK", "shared_objective_write"),
        ("shared_objective_flag없음", req("shared_objective/x.json"), "BLOCK", "shared_objective_requires_objective_flag"),
        ("operating_store_접근", req("user_graph.yaml"), "BLOCK", "operating_store_access"),
        ("operating_store2", req("ontology/_graph_merge.yaml"), "BLOCK", "operating_store_access"),
        ("path_traversal_타user", req("users/user_a/../user_b/evidence/e.json"), "BLOCK", "path_traversal"),
        ("upload_승인전", req("users/user_a/packs/p2.json", intent="upload"), "BLOCK", "upload_no_approval"),
        ("upload_승인후", req("users/user_a/packs/p2.json", intent="upload", approved=True), "PASS", "own_user_root"),
        ("user_namespace_밖", req("random/other/x.json"), "BLOCK", "outside_user_namespace"),
        ("user_root_없음", req("users/user_a/evidence/e.json", ur=None), "BLOCK", "no_actor_user_root"),
    ]

    print("=" * 80)
    print("OpenBinggu — 물리 store 격리(physical store isolation) validator (synthetic / selftest)")
    print("=" * 80)
    all_ok = True
    leak = False
    for name, r, exp_v, exp_rc in cases:
        res = check_store_access(r)
        ok = (res["verdict"] == exp_v) and (res["reason_code"] == exp_rc)
        all_ok = all_ok and ok
        # raw 경로 미출력 검증: 결과에 access_path substring 없어야
        import json as _json
        if r["access_path"].strip() and r["access_path"] in _json.dumps(res, ensure_ascii=False):
            leak = True
        print("  [%s] %-32s verdict=%-5s reason=%-38s path_id=%s"
              % ("OK" if ok else "FAIL", name, res["verdict"], res["reason_code"], res["path_id"]))

    print("\n  path_safety allow_root(user_a):", build_allow_root("user_a"), "(타 user_root=outside_root 자연 deny)")
    print("  raw_path_not_leaked:", (not leak))
    print("  operating_store_unchanged: True (판정만, 실파일/DB 생성 0)")
    gate = "GO" if (all_ok and not leak) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_physical_store_isolation_dryrun.py [--selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
