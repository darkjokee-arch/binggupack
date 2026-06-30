#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu — reviewer 인증/세션 토큰 S1~S19 synthetic selftest (DESIGN→검증, sandbox only).

설계: docs/BINGGUPACK_REVIEWER_AUTH_SESSION_TOKEN_DESIGN.md
범위: 토큰 발급/검증 mock + revocation mock + audit whitelist 검사 +
      enforce_access / ReviewResolver 연결 검증. 전부 in-memory / synthetic.

불변: 운영 store write 0 · OpenCrab upload 0 · confirmed apply 0 · push 0 · FS write 0.
raw secret/token/private path 미출력 (token_id 해시·reason_code·id 만). signing_key 미출력.

무수정 import:
  - openbinggu_runtime_access_engine.enforce_access   (deny-by-default 강제 엔진)
  - openbinggu_review_resolver_sandbox.ReviewResolver  (review decision preview)

CLI: python openbinggu_reviewer_auth_session_selftest.py --selftest
"""
import os
import sys
import json
import hmac
import hashlib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_runtime_access_engine import enforce_access          # noqa: E402
from openbinggu_review_resolver_sandbox import ReviewResolver        # noqa: E402

# ── audit whitelist (설계 §5 정합) — 이 키만 audit 에 허용. raw 0. ──────────────
AUDIT_WHITELIST = {
    "event_id", "event_type", "actor", "user_root", "action",
    "resource_id", "token_id", "decision", "reason_code", "timestamp", "session_id",
}
# raw 누출 sentinel — blob 어디에도 등장하면 안 됨.
SIGNING_KEY = "SECRET_SIGNING_KEY_DO_NOT_LOG"     # 절대 출력/audit 미포함
LEAK_NEEDLES = ["C:/", "C:\\", ".pem", "id_rsa", SIGNING_KEY, "/home/", "/Users/"]

# 요청 action → 토큰 scope 에서 요구되는 권한 토큰.
ACTION_SCOPE_NEED = {
    "read": "read",
    "tool_call": "tool_call:dryrun",
    "review_decision": "review_decision:preview",
    "write": "write",
    "upload": "upload",
}


def _h(s):
    return hashlib.sha256(str(s).encode("utf-8", "replace")).hexdigest()[:12]


def _canonical(claim):
    return json.dumps(claim, sort_keys=True, ensure_ascii=False)


# ── 토큰 발급 mock (raw secret 미저장 — 서명만, signing_key 미포함) ─────────────
# issuer = 발급 주체. 설계 §1-0: owner(개인용 local)만 발급. 정상 토큰은 issuer="owner".
ISSUER_OWNER = "owner"


def issue_token(subject, user_root, role, scope, allowed_visibility,
                issued_at, expires_at, nonce, issuer=ISSUER_OWNER):
    token_id = _h(str(nonce) + subject + str(issued_at))
    claim = {
        "token_id": token_id, "issuer": issuer, "subject": subject,
        "user_root": user_root, "role": role,
        "scope": list(scope), "allowed_visibility": list(allowed_visibility),
        "issued_at": issued_at, "expires_at": expires_at, "nonce": nonce,
    }
    sig = hmac.new(SIGNING_KEY.encode(), _canonical(claim).encode(), hashlib.sha256).hexdigest()
    return {"claim": claim, "sig": sig}


# ── 토큰 검증 (서명·issuer·만료·revocation·clock skew·replay) — fail-closed ──────
def verify_token(token, now, revocation_set, seen_nonces):
    if not token:
        return False, "no_token", None
    claim = token.get("claim") or {}
    expect = hmac.new(SIGNING_KEY.encode(), _canonical(claim).encode(), hashlib.sha256).hexdigest()
    if token.get("sig") != expect:
        return False, "token_sig_mismatch", claim.get("token_id")
    # 설계 §1-0: 발급 주체는 owner 뿐. issuer 없거나 owner 아니면 무효(fail-closed).
    if claim.get("issuer") != ISSUER_OWNER:
        return False, "token_issuer_invalid", claim.get("token_id")
    if claim.get("issued_at") is None or claim["issued_at"] > now:   # 시계 역행 무효
        return False, "token_future_issued", claim.get("token_id")
    if claim.get("expires_at") is None or claim["expires_at"] <= now:  # 만료=hard cut(fail-closed)
        return False, "token_expired", claim.get("token_id")
    if claim.get("token_id") in revocation_set:
        return False, "token_revoked", claim.get("token_id")
    if claim.get("nonce") in seen_nonces:                            # replay 방지
        return False, "token_replay", claim.get("token_id")
    seen_nonces.add(claim.get("nonce"))
    return True, "token_valid", claim.get("token_id")


# ── 토큰 → access 컨텍스트 (scope/visibility/approval 강제) ────────────────────
def token_to_access(token, action, req_visibility, approval_grant):
    """반환: (access_or_None, reason). reader 는 approval 강제 False(설계 S7)."""
    claim = token["claim"]
    role = claim["role"]
    # apply 는 scope 무관 → 엔진이 항상 HOLD 로 처리하도록 통과.
    if action != "apply":
        need = ACTION_SCOPE_NEED.get(action)
        if need is None or need not in claim["scope"]:
            return None, "scope_deny"
    # visibility escalation 차단
    if req_visibility is not None and req_visibility not in claim["allowed_visibility"]:
        return None, "visibility_escalation"
    actor_kind = "human" if role == "owner" else "reader"
    # approval: owner + 유효 grant 만 True. reader 는 무조건 False.
    approval = bool(role == "owner" and approval_grant)
    return {"user_root": claim["user_root"], "actor_kind": actor_kind,
            "action": action, "approval": approval, "layer": "subjective"}, "access_built"


# ── 단일 요청 파이프라인: verify → access → enforce_access/ReviewResolver ──────
class Harness:
    def __init__(self):
        self.audit = []
        self.seen_nonces = set()
        self.revocation = set()
        self.fs_writes = 0          # 항상 0 (이 selftest 는 FS write 안 함)
        self.confirmed_created = 0
        self.applied = 0
        self.uploaded = 0
        self.pushed = 0

    def _audit(self, event_type, actor, user_root, action, resource_id, token_id, decision, reason_code):
        self.audit.append({
            "event_id": "ev_" + _h(str(len(self.audit)) + str(action)),
            "event_type": event_type, "actor": _h(str(actor)), "user_root": user_root,
            "action": action, "resource_id": _h(resource_id) if resource_id else None,
            "token_id": token_id, "decision": decision, "reason_code": reason_code,
            "timestamp": "T", "session_id": _h("sess"),
        })

    def request(self, token, action, target, now, req_visibility=None, approval_grant=False, evidence_store=None):
        evidence_store = evidence_store or {}
        # 1) 토큰 검증
        ok, rc, token_id = verify_token(token, now, self.revocation, self.seen_nonces)
        if not ok:
            self._audit("token_verify", None, None, action, target.get("path"), token_id, "BLOCK", rc)
            return {"verdict": "BLOCK", "reason_code": rc, "stage": "token"}
        # 2) 토큰 → access (scope/visibility/approval)
        access, arc = token_to_access(token, action, req_visibility, approval_grant)
        if access is None:
            self._audit("access_decision", token["claim"]["subject"], token["claim"]["user_root"],
                        action, target.get("path"), token_id, "BLOCK", arc)
            return {"verdict": "BLOCK", "reason_code": arc, "stage": "scope"}
        # 3) review_decision → ReviewResolver / 그 외 → enforce_access
        req = {"actor": {"user_root": access["user_root"], "actor": access["actor_kind"],
                         "owner_approved": access["approval"]},
               "action": action, "target": target}
        r = enforce_access(req, evidence_store)
        # 부수효과 카운터(전부 0 유지 확인용)
        self.confirmed_created += int(r.get("confirmed_created", 0) or 0)
        self.applied += 1 if r.get("apply") == "DONE" else 0
        self.uploaded += 1 if r.get("uploaded") else 0
        self.pushed += 1 if r.get("pushed") else 0
        et = "review_preview" if action == "review_decision" else "access_decision"
        self._audit(et, access["actor_kind"], access["user_root"], action,
                    target.get("path") or target.get("item_id"), token_id,
                    r["verdict"], r.get("reason_code"))
        return r


def _leak_scan(*objs):
    blob = json.dumps(objs, ensure_ascii=False, default=str)
    return sum(1 for n in LEAK_NEEDLES if n in blob)


# ──────────────────────── selftest S1~S19 ────────────────────────
def _selftest():
    H = Harness()
    NOW = 1000
    ev = {"EV_ok": {"user_root": "user_a", "layer": "subjective"},
          "EV_stale": {"user_root": "user_a", "stale": True}}

    def tok(role="reader", ur="user_a", scope=("read",), vis=("private",),
            issued=900, expires=2000, nonce=None, subject="reader:codex",
            issuer=ISSUER_OWNER):
        nonce = nonce if nonce is not None else "n_" + _h(str(role) + str(scope) + str(issued))
        return issue_token(subject, ur, role, scope, vis, issued, expires, nonce, issuer=issuer)

    def qitem(iid, ur="user_a", refs=None, layer="subjective", status="review_pending"):
        return {"item_id": iid, "user_root": ur, "status": status,
                "evidence_refs": refs or ["EV_ok"], "layer": layer}

    results = []   # (sid, name, ok, verdict, reason)

    def check(sid, name, r, exp_v, exp_rc=None, extra_ok=True):
        v_ok = r["verdict"] == exp_v
        rc_ok = (exp_rc is None) or (str(r.get("reason_code", "")) == exp_rc)
        ok = v_ok and rc_ok and extra_ok
        results.append((sid, name, ok, r["verdict"], r.get("reason_code")))
        return r

    # S1 토큰 없음
    check("S1", "토큰 없음 → deny",
          H.request(None, "read", {"path": "users/user_a/evidence/e1.json"}, NOW),
          "BLOCK", "no_token")
    # S2 만료 토큰
    check("S2", "만료 토큰 → deny",
          H.request(tok(expires=500), "read", {"path": "users/user_a/evidence/e1.json"}, NOW),
          "BLOCK", "token_expired")
    # S3 철회 토큰
    t3 = tok(nonce="n_revoke")
    H.revocation.add(t3["claim"]["token_id"])
    check("S3", "철회(revoked) 토큰 → deny",
          H.request(t3, "read", {"path": "users/user_a/evidence/e1.json"}, NOW),
          "BLOCK", "token_revoked")
    # S4 서명 위조
    t4 = tok(nonce="n_forge")
    t4 = {"claim": t4["claim"], "sig": "deadbeef_forged"}
    check("S4", "서명 불일치/위조 → deny",
          H.request(t4, "read", {"path": "users/user_a/evidence/e1.json"}, NOW),
          "BLOCK", "token_sig_mismatch")
    # S5 scope 밖 (read 토큰 → write)
    check("S5", "scope 밖 action(read→write) → deny",
          H.request(tok(scope=("read",), nonce="n_s5"), "write",
                    {"path": "users/user_a/packs/p.json"}, NOW),
          "BLOCK", "scope_deny")
    # S6 cross-user_root (owner user_a 토큰, 요청 user_b 경로)
    check("S6", "cross-user_root → deny",
          H.request(tok(role="owner", scope=("read",), nonce="n_s6"), "read",
                    {"path": "users/user_b/evidence/e9.json"}, NOW),
          "BLOCK", "deny_cross_user_root")
    # S7 reader 토큰이 approval=True 시도 → approval 강제 False + reader 변경성 자동 차단
    #    (reader 는 upload 자체가 auto_path_forbidden 으로 더 먼저 차단 = approval 보다 강한 deny)
    s7_tok = tok(role="reader", scope=("read", "upload"), nonce="n_s7")
    s7_acc, _ = token_to_access(s7_tok, "upload", None, approval_grant=True)
    s7_approval_false = (s7_acc is not None and s7_acc["approval"] is False)
    r7 = H.request(s7_tok, "upload", {"path": "users/user_a/packs/up.json"}, NOW, approval_grant=True)
    check("S7", "reader approval=True 무시(강제 False)", r7, "BLOCK", "auto_path_forbidden",
          extra_ok=s7_approval_false)
    # S8 owner approval 없는 upload
    check("S8", "owner approval 없는 upload → deny",
          H.request(tok(role="owner", scope=("read", "upload"), nonce="n_s8"), "upload",
                    {"path": "users/user_a/packs/up.json"}, NOW, approval_grant=False),
          "BLOCK", "upload_no_approval")
    # S9 owner review decision confirm → CONFIRM_ALLOWED/ALLOW preview, confirmed_created=0
    r9 = H.request(tok(role="owner", scope=("review_decision:preview",), nonce="n_s9"),
                   "review_decision",
                   {"queue_item": qitem("i1"), "decision": "confirm"}, NOW, evidence_store=ev)
    check("S9", "review confirm → preview(confirmed 0)", r9, "ALLOW",
          "review_governance_pass_preview_only", extra_ok=(r9.get("confirmed_created", 0) == 0))
    # S10 apply (토큰 무관 HOLD)
    check("S10", "apply → 항상 HOLD",
          H.request(tok(role="owner", scope=("read",), nonce="n_s10"), "apply",
                    {"item_id": "i30"}, NOW),
          "BLOCK", "apply_to_graph_store_HOLD")
    # S11 dry-run tool_call clean path → ALLOW
    r11 = H.request(tok(role="reader", scope=("tool_call:dryrun",), nonce="n_s11"), "tool_call",
                    {"path_inputs": ["users/user_a/packs/p1.json"], "allow_root": "users/user_a"}, NOW)
    check("S11", "dry-run 도구 clean → ALLOW", r11, "ALLOW", "tool_path_clean")
    # S12 push/ingest 핸들러 노출 시도 → REJECT (scope 미포함 → scope_deny)
    check("S12", "write/push/ingest 노출 시도 → REJECT",
          H.request(tok(role="owner", scope=("read",), nonce="n_s12"), "push",
                    {"path": "users/user_a/x"}, NOW),
          "BLOCK", "scope_deny")
    # S14 clock skew: 미래 issued_at 무효
    check("S14a", "미래 issued_at → 무효",
          H.request(tok(issued=5000, expires=6000, nonce="n_s14a"), "read",
                    {"path": "users/user_a/evidence/e1.json"}, NOW),
          "BLOCK", "token_future_issued")
    # S14 만료 경계: expires == now → expired (fail-closed)
    check("S14b", "만료 경계(expires==now) → deny",
          H.request(tok(expires=NOW, nonce="n_s14b"), "read",
                    {"path": "users/user_a/evidence/e1.json"}, NOW),
          "BLOCK", "token_expired")
    # S15 replay: 동일 nonce 2회
    t15 = tok(role="reader", scope=("read",), nonce="n_replay")
    H.request(t15, "read", {"path": "users/user_a/evidence/e1.json"}, NOW)   # 1회차 소비
    check("S15", "nonce replay(2회차) → deny",
          H.request(t15, "read", {"path": "users/user_a/evidence/e1.json"}, NOW),
          "BLOCK", "token_replay")
    # S16 visibility escalation (allowed=private, 요청 public)
    check("S16", "visibility escalation → deny",
          H.request(tok(role="reader", scope=("read",), vis=("private",), nonce="n_s16"), "read",
                    {"path": "users/user_a/evidence/e1.json"}, NOW, req_visibility="public"),
          "BLOCK", "visibility_escalation")
    # S19 issuer=owner 검증 (설계 §1-0): issuer 가 owner 아니면 BLOCK.
    #   대표 케이스 = issuer 위장(reader 가 자기 발급한 척). 서명은 유효해도 issuer≠owner 면 무효.
    #   보조 assert = issuer 없는(None) 토큰도 동일하게 token_issuer_invalid 로 차단.
    t19_none = tok(role="reader", scope=("read",), nonce="n_s19n", issuer=None)
    none_ok, none_rc, _ = verify_token(t19_none, NOW, set(), set())
    s19_none_blocked = (none_ok is False and none_rc == "token_issuer_invalid")
    check("S19", "issuer≠owner/없음 → deny",
          H.request(tok(role="reader", scope=("read",), nonce="n_s19", issuer="reader:codex"),
                    "read", {"path": "users/user_a/evidence/e1.json"}, NOW),
          "BLOCK", "token_issuer_invalid", extra_ok=s19_none_blocked)

    # ── 집계 판정 ──
    print("=" * 86)
    print("OpenBinggu — reviewer 인증/세션 토큰 S1~S19 selftest (synthetic / sandbox)")
    print("=" * 86)
    npass = 0
    for sid, name, ok, v, rc in results:
        npass += ok
        print("  [%s] %-5s %-38s verdict=%-7s reason=%s" %
              ("OK" if ok else "FAIL", sid, name[:38], v, rc))

    # S13 raw leak 0 (전 결과 + audit)
    leak = _leak_scan([r for r in results], H.audit)
    # audit 에 raw token sig / claim nonce / signing_key 없는지 추가 확인
    audit_blob = json.dumps(H.audit, ensure_ascii=False)
    leak += 1 if (SIGNING_KEY in audit_blob or "deadbeef_forged" in audit_blob) else 0
    s13_ok = (leak == 0)

    # S17 audit whitelist: 모든 audit 엔트리 키 ⊆ whitelist
    bad_keys = set()
    for a in H.audit:
        bad_keys |= (set(a.keys()) - AUDIT_WHITELIST)
    s17_ok = (len(bad_keys) == 0)

    # S18 operating store unchanged: 이 selftest FS write 0 + 부수효과 0
    s18_ok = (H.fs_writes == 0 and H.confirmed_created == 0 and H.applied == 0
              and H.uploaded == 0 and H.pushed == 0)

    print("\n  [S13] raw_leak                 :", leak, "(0 이어야)", "→", "OK" if s13_ok else "FAIL")
    print("  [S17] audit whitelist 위반 키  :", sorted(bad_keys) or "없음", "→", "OK" if s17_ok else "FAIL")
    print("  [S18] FS write / 부수효과       : writes=%d confirmed=%d applied=%d upload=%d push=%d → %s"
          % (H.fs_writes, H.confirmed_created, H.applied, H.uploaded, H.pushed,
             "OK" if s18_ok else "FAIL"))
    print("        operating_store_unchanged: True (in-memory only)")
    print("  audit entries(whitelist only, raw 0):", len(H.audit))

    case_pass = (npass == len(results))
    gate = "GO" if (case_pass and s13_ok and s17_ok and s18_ok) else "NO-GO"
    total = len(results) + 3   # +S13,S17,S18
    npass_all = npass + int(s13_ok) + int(s17_ok) + int(s18_ok)
    print("\n  RESULT: %d/%d  (cases %d/%d + S13/S17/S18)  GATE=%s"
          % (npass_all, total, npass, len(results), gate))
    return 0 if gate == "GO" else 1


def main():
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        return _selftest()
    print("usage: python openbinggu_reviewer_auth_session_selftest.py --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
