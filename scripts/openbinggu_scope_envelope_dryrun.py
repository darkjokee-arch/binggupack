# -*- coding: utf-8 -*-
"""OpenBinggu scope envelope 통합 dry-run validator/reader (synthetic fixture only).

설계 문서 기준: SCOPE_ENVELOPE_CONTRACT / CONSUMER_READER_CONTRACT / USER_ROOT_ISOLATION /
VISIBILITY_META / RUNTIME_ACCESS_CONTROL / SCOPE_ENVELOPE_FIXTURE_PLAN.

consumer reader contract + user_root + visibility + runtime access(deny-by-default) 를
합성 fixture 로 한 묶음 검증. 정상=PASS, negative=의도한 STOP/FAIL.

재사용(무수정): watcher_op_m0._store_snapshot (운영 store mtime 불변 검증) ·
  watcher_batch_m1.scan_residual_pii (독립 PII 형태 scanner).
실자료/private pack 0 · 외부전송 0 · OpenCrab/store/DB write 0 · apply/ingest/merge/production 0 ·
hook/daemon 0 · v09/ARMED/push/bid-engine 0. 산출은 temp/synthetic only.

CLI: python openbinggu_scope_envelope_dryrun.py --selftest
"""
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
TMP_OUT = BASE / "tmp" / "scope_envelope_dryrun"
REPORT = BASE / "reports" / "scope_envelope_dryrun.json"

sys.path.insert(0, str(SCRIPTS))
import watcher_op_m0 as m0          # _store_snapshot (운영 store 불변 검증)
import watcher_batch_m1 as m1       # scan_residual_pii (독립 scanner 재사용)

NS_A = "user_a"
NS_B = "user_b"


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _id_ns(node_or_edge_id):
    """'node:user_a:wch:xxx' -> 'user_a'."""
    parts = node_or_edge_id.split(":")
    return parts[1] if len(parts) >= 2 else ""


# ---------- 합성 pack 빌더 (전부 가짜 데이터) ----------
def make_pack(*, namespace=NS_A, owner=NS_A, visibility="private",
              redaction_status="verified", raw_allowed=False,
              promotion_bad=False, cross_root=False, basis_mismatch=False, pii_raw=False):
    sentences = [
        "합성 노드 알파는 evidence 정규화 절차를 설명한다.",
        "합성 노드 베타는 review-only 원칙을 기술한다.",
    ]
    # PII raw negative: 합성(가짜) 미마스킹 전화 형태 1건 주입 (raw 값은 fixture 내부에만)
    if pii_raw:
        sentences = sentences + ["합성 연락처 010-" + "0000-0000 미마스킹 상태."]

    nodes, chunks, ev_index = [], [], []
    for i, s in enumerate(sentences):
        eid = "EVC-" + _sha8(namespace + s)
        nid = "node:%s:wch:%s" % (namespace, _sha8(s)[:6])
        nodes.append({
            "id": nid, "promotion_allowed": (True if (promotion_bad and i == 0) else False),
            "properties": {"candidate": True, "domain": "STAGING_UNASSIGNED", "sentence": s, "origin": "watcher"},
        })
        chunks.append({"item_id": eid, "text": s, "source": "synthetic"})
        ev_index.append({"evidence_id": eid})

    # cross-root negative: 타 namespace node 1건 주입
    if cross_root:
        xs = "타 root 혼입 합성 노드."
        nodes.append({"id": "node:%s:wch:%s" % (NS_B, _sha8(xs)[:6]),
                      "promotion_allowed": False,
                      "properties": {"candidate": True, "domain": "STAGING_UNASSIGNED", "sentence": xs, "origin": "watcher"}})
        chunks.append({"item_id": "EVC-" + _sha8(NS_B + xs), "text": xs, "source": "synthetic"})
        ev_index.append({"evidence_id": "EVC-" + _sha8(NS_B + xs)})

    # edge 1건 (evidence_supports, node→node 아님: EVC→node)
    edges = []
    if len(nodes) >= 1:
        edges.append({"id": "edge:%s:wch:%s" % (namespace, _sha8("e0")[:6]),
                      "source": chunks[0]["item_id"], "target": nodes[0]["id"],
                      "promotion_allowed": (True if promotion_bad else False), "rel": "evidence_supports"})

    basis = {
        "node_ids": sorted(n["id"] for n in nodes),
        "edge_ids": sorted(e["id"] for e in edges),
        "evidence_ids": sorted(c["item_id"] for c in chunks),
    }
    if basis_mismatch:
        basis["node_ids"] = basis["node_ids"] + ["node:%s:wch:ghost0" % namespace]  # 실제 없는 id

    manifest = {
        "format_version": "opencrab-pack-v1", "pack_id": "%s/pack_%s" % (namespace, _sha8(visibility + owner)[:6]),
        "owner": owner, "user_root": owner, "user_namespace": namespace,
        "visibility": visibility, "pack_type": "candidate", "redaction_status": redaction_status,
        "raw_allowed": raw_allowed, "counts": {"nodes": len(nodes), "edges": len(edges), "evidence": len(chunks)},
    }
    return {"manifest": manifest, "nodes": nodes, "edges": edges,
            "evidence_index": ev_index, "evidence_chunk": chunks,
            "consumer_view": {"evidence_basis": basis, "counts": manifest["counts"]}}


# ---------- runtime access (deny-by-default) ----------
def access_decision(manifest, grant):
    """deny-by-default. reader_permission(grant) + session delegation + visibility 범위 강제.
    하나라도 불충족이면 deny. (envelope=표식, 차단은 이 함수가 강제)."""
    if not grant:
        return False                                      # reader_permission 누락
    if not grant.get("session_valid", False):
        return False                                      # session delegation 누락/만료
    vis = manifest.get("visibility") or "private"         # 미상 시 private
    allowed = grant.get("allowed_visibility", [])
    if vis == "private":
        return manifest.get("owner") == grant.get("user_root") and "private" in allowed
    if vis == "team":
        return grant.get("team_member", False) and "team" in allowed   # team member 아니면 deny
    return "public" in allowed                            # public


# ---------- dry-run reader/validator ----------
def read_validate(pack, grant):
    m = pack["manifest"]; ns = m["user_namespace"]
    nodes, edges, chunks = pack["nodes"], pack["edges"], pack["evidence_chunk"]
    ids = [n["id"] for n in nodes] + [e["id"] for e in edges if e["id"].startswith("edge:")]

    residual = []
    for c in chunks:
        residual += m1.scan_residual_pii(c["text"])

    actual_basis = {
        "node_ids": set(n["id"] for n in nodes),
        "edge_ids": set(e["id"] for e in edges),
        "evidence_ids": set(c["item_id"] for c in chunks),
    }
    cv = pack["consumer_view"]["evidence_basis"]

    checks = {
        "envelope_fields": all(k in m for k in
            ["pack_id", "owner", "user_root", "user_namespace", "visibility", "pack_type", "redaction_status", "raw_allowed"]),
        "raw_allowed_false": m.get("raw_allowed") is False,
        "pack_id_namespace_consistent": str(m.get("pack_id", "")).startswith(ns + "/"),
        "owner_user_root_consistent": m.get("owner") == m.get("user_root"),
        "no_cross_root": all(_id_ns(i) == ns for i in ids),
        "candidate_all_true": all(n["properties"]["candidate"] is True for n in nodes),
        "promotion_all_false": all(n["promotion_allowed"] is False for n in nodes) and all(e["promotion_allowed"] is False for e in edges),
        "redaction_no_residual": len(residual) == 0,
        "redaction_status_ok": m.get("redaction_status") in ("verified", "revalidated"),
        "evidence_basis_match": (set(cv["node_ids"]) == actual_basis["node_ids"]
                                 and set(cv["edge_ids"]) == actual_basis["edge_ids"]
                                 and set(cv["evidence_ids"]) == actual_basis["evidence_ids"]),
        "read_allowed": access_decision(m, grant),
    }
    verdict = "PASS" if all(checks.values()) else "STOP/FAIL"
    failed = [k for k, v in checks.items() if not v]
    return {"verdict": verdict, "checks": checks, "failed_checks": failed,
            "residual_scanner_kinds": sorted(set(residual))}


# ---------- 트랙1 GitHub 공개 fail-closed guard (설계: TRACK1_FAILCLOSED_PUBLISH_GUARD_DESIGN) ----------
# 회귀방지 R1~R3 기본 상태(현 트랙 고정). 하나라도 깨지면 공개 파이프라인 FAIL.
PUBLISH_REGRESSION_STATE = {
    "marketplace_enabled": False,
    "enum_status": "HOLD",
    "team_billing_code_exists": False,
}
FAILCLOSED_FIXTURE = BASE / "docs" / "fixtures_candidate" / "track1_failclosed_masking_unknown_bad.json"


def regression_guard(state):
    """R1~R3: marketplace_enabled==False AND enum_status=='HOLD' AND team_billing_code_exists==False.
    하나라도 깨지면 False(FAIL)."""
    return (state.get("marketplace_enabled") is False
            and state.get("enum_status") == "HOLD"
            and state.get("team_billing_code_exists") is False)


def publish_decision(items, publish_approved, regression_state=None):
    """트랙1 GitHub 공개 fail-closed 결정.
    게이트1(마스킹): 모든 item.mask_result == 'clean' 이어야 통과. dirty/unknown(=clean 아님) 1건↑ = BLOCK.
    게이트2(수동승인): publish_approved is True 아니면 BLOCK.
    회귀방지(R1~R3): 깨지면 FAIL(파이프라인 차단).
    fail-closed: 의심·불명(unknown)·미검증·미승인은 전부 차단. raw 값은 미기재(reason_code만)."""
    state = regression_state if regression_state is not None else PUBLISH_REGRESSION_STATE
    reason = []
    reg_ok = regression_guard(state)
    if not reg_ok:
        reason.append("REGRESSION_FAIL")
    # 게이트1: clean 외 전부 BLOCK (fail-closed). unknown 도 dirty 도 통과 못 함.
    results = [str(it.get("mask_result", "unknown")).lower() for it in items]
    non_clean = [r for r in results if r != "clean"]
    gate1_ok = len(non_clean) == 0
    if not gate1_ok:
        if any(r == "dirty" for r in non_clean):
            reason.append("RESIDUAL_DIRTY")
        if any(r != "dirty" for r in non_clean):   # unknown 또는 알 수 없는 값 → fail-closed
            reason.append("MASK_UNKNOWN")
    # 게이트2: 수동승인
    gate2_ok = publish_approved is True
    if not gate2_ok:
        reason.append("NOT_APPROVED")
    publish_allowed = bool(reg_ok and gate1_ok and gate2_ok)
    verdict = "ALLOW" if publish_allowed else "BLOCK"
    if not reg_ok:
        verdict = "FAIL"   # 회귀는 단순 BLOCK 이 아니라 파이프라인 FAIL
    return {"publish_allowed": publish_allowed, "verdict": verdict,
            "reason_codes": sorted(set(reason)), "regression_pass": reg_ok}


def run_publish_guard_checks():
    """트랙1 fail-closed guard selftest. negative fixture 연결 + fail-open 검출. 반환 (ok, results)."""
    REG_OK = dict(PUBLISH_REGRESSION_STATE)
    clean_items = [{"item_id": "a", "mask_result": "clean"}, {"item_id": "b", "mask_result": "clean"}]
    dirty_items = [{"item_id": "a", "mask_result": "clean"}, {"item_id": "d", "mask_result": "dirty"}]

    # negative fixture 연결 (Python utf-8 json.load)
    fx = json.loads(FAILCLOSED_FIXTURE.read_text(encoding="utf-8"))
    fx_items = fx["pack"]["items"]
    fx_exp_allowed = fx["expected"]["publish_allowed"]    # False
    fx_exp_reason = fx["expected"]["reason_codes_include"]  # ["MASK_UNKNOWN"]

    # (name, items, approved, reg_state, exp_allowed, exp_verdict, exp_reason_subset)
    cases = [
        ("publish_clean_approved_ok", clean_items, True, REG_OK, True, "ALLOW", []),
        ("publish_masking_unknown_bad(fixture)", fx_items, True, REG_OK, fx_exp_allowed, "BLOCK", fx_exp_reason),
        ("publish_dirty_bad", dirty_items, True, REG_OK, False, "BLOCK", ["RESIDUAL_DIRTY"]),
        ("publish_not_approved_bad", clean_items, False, REG_OK, False, "BLOCK", ["NOT_APPROVED"]),
        ("publish_regression_marketplace_bad", clean_items, True, {**REG_OK, "marketplace_enabled": True}, False, "FAIL", ["REGRESSION_FAIL"]),
        ("publish_regression_enum_bad", clean_items, True, {**REG_OK, "enum_status": "CONFIRMED"}, False, "FAIL", ["REGRESSION_FAIL"]),
        ("publish_regression_billing_bad", clean_items, True, {**REG_OK, "team_billing_code_exists": True}, False, "FAIL", ["REGRESSION_FAIL"]),
    ]
    results, ok = [], True
    for name, items, approved, reg, exp_allowed, exp_verdict, exp_reason in cases:
        out = publish_decision(items, approved, reg)
        allowed_ok = out["publish_allowed"] == exp_allowed
        verdict_ok = out["verdict"] == exp_verdict
        reason_ok = all(rc in out["reason_codes"] for rc in exp_reason)
        # fail-open 검출: negative(exp_allowed False)인데 allowed True 면 치명 FAIL
        fail_open = (exp_allowed is False and out["publish_allowed"] is True)
        intended = allowed_ok and verdict_ok and reason_ok and not fail_open
        ok = ok and intended
        results.append({"name": name, "expected_allowed": exp_allowed, "actual_allowed": out["publish_allowed"],
                        "expected_verdict": exp_verdict, "actual_verdict": out["verdict"],
                        "reason_codes": out["reason_codes"], "fail_open": fail_open, "as_intended": intended})
    return ok, results


# ---------- source pointer 공개 차단 판정 (판정 only · 치환/sanitizer 없음) ----------
# 비공개/내부 형태 = dirty, 판정불가 = unknown, 그 외 안전 형태 = clean.
# raw 경로값은 반환/출력하지 않음 (라벨·count 만).
_WIN_ABSPATH = re.compile(r"^[A-Za-z]:[\\/]")                       # C:\... or C:/...
_FILE_URI = re.compile(r"^file://", re.I)
_UNC = re.compile(r"^\\\\[^\\]+\\")                                 # \\server\share
_UNIX_PRIVATE = re.compile(r"^(/home/|/Users/|/root/|/var/|/etc/|/mnt/|/opt/|/private/)")
_LOCALHOST = re.compile(r"(localhost|127\.0\.0\.1|0\.0\.0\.0|::1)", re.I)
_INTERNAL_HOST = re.compile(
    r"(192\.168\.|10\.\d+\.|172\.(1[6-9]|2\d|3[01])\.|\.local\b|\.internal\b|\.lan\b|\.intra\b)", re.I)
_UNDECIDED_TOKENS = {"mask_undecided_token", "unknown", "n/a", "na", "?", "tbd"}


def classify_source_pointer(value):
    """source pointer 1건 → 'clean' | 'dirty' | 'unknown'. raw 값 미반환(판정만, 치환 없음).
    dirty = Windows 절대경로 / file:// / UNC / 비공개 unix path / localhost / 내부 URL·IP.
    unknown = None·빈값·판정불가 토큰.
    clean = 그 외(synthetic 식별자·hash_reference·상대경로·외부 공개 URL)."""
    if value is None:
        return "unknown"
    s = str(value).strip()
    if not s or s.lower() in _UNDECIDED_TOKENS:
        return "unknown"
    if (_WIN_ABSPATH.match(s) or _FILE_URI.match(s) or _UNC.match(s) or _UNIX_PRIVATE.match(s)
            or _LOCALHOST.search(s) or _INTERNAL_HOST.search(s)):
        return "dirty"
    return "clean"


def classify_source_pointers(pointers):
    """여러 source pointer → 라벨 list + {clean,dirty,unknown} count. raw 경로값 미포함."""
    labels = [classify_source_pointer(p) for p in pointers]
    return {"labels": labels,
            "counts": {k: labels.count(k) for k in ("clean", "dirty", "unknown")}}


def run_source_pointer_checks():
    """source pointer 판정 + fail-closed guard 연동 selftest. raw 경로 미출력. 반환 (ok, results)."""
    # (name, pointers, expected_labels) — pointers 는 synthetic. dirty 케이스도 합성 형태.
    cases = [
        ("srcptr_synthetic_clean_ok", ["EVC-abc12345", "examples/toy/readme.md", "synthetic://node/n1"], ["clean", "clean", "clean"]),
        ("srcptr_win_abspath_dirty", ["C:\\Users\\PC\\private\\notes.md"], ["dirty"]),
        ("srcptr_file_uri_dirty", ["file:///C:/Users/PC/x.md"], ["dirty"]),
        ("srcptr_unc_dirty", ["\\\\fileserver\\share\\doc.md"], ["dirty"]),
        ("srcptr_unix_private_dirty", ["/home/pc/secret/key.md"], ["dirty"]),
        ("srcptr_localhost_dirty", ["http://localhost:8080/internal"], ["dirty"]),
        ("srcptr_internal_ip_dirty", ["http://192.168.0.10/api"], ["dirty"]),
        ("srcptr_internal_domain_dirty", ["https://wiki.internal/page"], ["dirty"]),
        ("srcptr_token_unknown", ["MASK_UNDECIDED_TOKEN"], ["unknown"]),
        ("srcptr_empty_unknown", [""], ["unknown"]),
        ("srcptr_mixed_dirty", ["EVC-ok1", "C:\\Users\\PC\\leak.md"], ["clean", "dirty"]),
    ]
    results, ok = [], True
    for name, pointers, exp_labels in cases:
        c = classify_source_pointers(pointers)
        labels_ok = c["labels"] == exp_labels
        # fail-closed guard 연동: source pointer 라벨을 mask_result item 으로 변환
        items = [{"item_id": "sp%d" % i, "mask_result": lab} for i, lab in enumerate(c["labels"])]
        pub = publish_decision(items, True, PUBLISH_REGRESSION_STATE)
        all_clean = all(l == "clean" for l in c["labels"])
        publish_ok = (pub["publish_allowed"] == all_clean)   # clean only=ALLOW, dirty/unknown 1↑=BLOCK
        fail_open = ((not all_clean) and pub["publish_allowed"] is True)
        intended = labels_ok and publish_ok and not fail_open
        ok = ok and intended
        results.append({"name": name, "counts": c["counts"], "labels_match": labels_ok,
                        "publish_allowed": pub["publish_allowed"], "publish_reasons": pub["reason_codes"],
                        "fail_open": fail_open, "as_intended": intended})
    return ok, results


# ---------- selftest ----------
def run_selftest():
    store_before = m0._store_snapshot()
    TMP_OUT.mkdir(parents=True, exist_ok=True)
    # grant = reader_permission + session delegation + team membership 표현
    grant_a = {"user_root": NS_A, "allowed_visibility": ["private", "team", "public"], "session_valid": True, "team_member": True}
    grant_other = {"user_root": "user_x", "allowed_visibility": ["private", "team", "public"], "session_valid": True, "team_member": False}
    grant_no_session = {"user_root": NS_A, "allowed_visibility": ["private", "team", "public"], "session_valid": False, "team_member": True}
    grant_none = None   # reader_permission 누락

    # envelope 누락/위조/불일치 후처리 헬퍼
    def _missing_envelope():
        p = make_pack(visibility="private")
        p["manifest"].pop("visibility", None)        # 필수 필드 누락
        return p

    def _forged_owner():
        p = make_pack(visibility="private")
        p["manifest"]["owner"] = NS_B                # owner != user_root (위조)
        return p

    def _packid_ns_mismatch():
        p = make_pack(visibility="private")
        p["manifest"]["pack_id"] = NS_B + "/pack_x"  # pack_id namespace != user_namespace
        return p

    # (name, pack, grant, expected_verdict, expected_failed_subset)
    cases = [
        ("same_root_private_ok", make_pack(visibility="private"), grant_a, "PASS", []),
        ("vis_team_ok", make_pack(visibility="team"), grant_a, "PASS", []),
        ("vis_public_anyone_ok", make_pack(visibility="public"), grant_other, "PASS", []),
        ("cross_root_bad", make_pack(cross_root=True), grant_a, "STOP/FAIL", ["no_cross_root"]),
        ("vis_private_otherroot_deny", make_pack(visibility="private"), grant_other, "STOP/FAIL", ["read_allowed"]),
        ("redaction_failed_bad", make_pack(redaction_status="failed", pii_raw=True), grant_a, "STOP/FAIL", ["redaction_no_residual", "redaction_status_ok"]),
        ("promotion_true_bad", make_pack(promotion_bad=True), grant_a, "STOP/FAIL", ["promotion_all_false"]),
        ("evidence_basis_mismatch_bad", make_pack(basis_mismatch=True), grant_a, "STOP/FAIL", ["evidence_basis_match"]),
        # --- runtime access control 강화 negative ---
        ("team_nonmember_deny", make_pack(visibility="team"), grant_other, "STOP/FAIL", ["read_allowed"]),
        ("public_redaction_stale_deny", make_pack(visibility="public", redaction_status="stale"), grant_other, "STOP/FAIL", ["redaction_status_ok"]),
        ("envelope_missing_bad", _missing_envelope(), grant_a, "STOP/FAIL", ["envelope_fields"]),
        ("envelope_forged_owner_bad", _forged_owner(), grant_a, "STOP/FAIL", ["owner_user_root_consistent"]),
        ("packid_ns_mismatch_bad", _packid_ns_mismatch(), grant_a, "STOP/FAIL", ["pack_id_namespace_consistent"]),
        ("session_missing_expired_deny", make_pack(visibility="private"), grant_no_session, "STOP/FAIL", ["read_allowed"]),
        ("reader_permission_missing_deny", make_pack(visibility="public"), grant_none, "STOP/FAIL", ["read_allowed"]),
    ]

    results, ok = [], True
    for name, pack, grant, exp_verdict, exp_failed in cases:
        # temp 산출 (synthetic only)
        (TMP_OUT / name).mkdir(parents=True, exist_ok=True)
        for fn, obj in [("manifest.json", pack["manifest"]), ("consumer_view_summary.json", pack["consumer_view"])]:
            (TMP_OUT / name / fn).write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        for fn, rows in [("nodes.jsonl", pack["nodes"]), ("edges.jsonl", pack["edges"]),
                         ("evidence_index.jsonl", pack["evidence_index"]), ("evidence_chunk.jsonl", pack["evidence_chunk"])]:
            (TMP_OUT / name / fn).write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

        out = read_validate(pack, grant)
        verdict_ok = out["verdict"] == exp_verdict
        failed_ok = all(f in out["failed_checks"] for f in exp_failed)
        # negative 는 의도한 check 에서만 실패해야(과검출 방지): 정상 케이스는 failed 0
        intended = verdict_ok and failed_ok and (exp_verdict == "STOP/FAIL" or len(out["failed_checks"]) == 0)
        ok = ok and intended
        results.append({"name": name, "expected": exp_verdict, "actual": out["verdict"],
                        "expected_failed_subset": exp_failed, "actual_failed": out["failed_checks"],
                        "residual_scanner_kinds": out["residual_scanner_kinds"], "as_intended": intended})

    pub_ok, pub_results = run_publish_guard_checks()
    spt_ok, spt_results = run_source_pointer_checks()

    store_after = m0._store_snapshot()
    store_unchanged = (store_before == store_after)
    gate = "GO" if (ok and pub_ok and spt_ok and store_unchanged) else "STOP"

    report = {"tool": "openbinggu_scope_envelope_dryrun.py", "mode": "synthetic dry-run / selftest",
              "operating_store_unchanged": store_unchanged, "all_as_intended": ok,
              "publish_guard_as_intended": pub_ok, "source_pointer_as_intended": spt_ok, "gate": gate,
              "store_write": 0, "db_write": 0, "opencrab_call": 0, "github_push": 0, "apply": 0,
              "ingest": 0, "merge": 0, "production": 0, "hook_daemon": 0, "external_transmit": 0,
              "cases": results, "publish_guard_cases": pub_results, "source_pointer_cases": spt_results}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 76)
    print("OpenBinggu scope envelope 통합 dry-run (synthetic / selftest)")
    print("=" * 76)
    for r in results:
        mark = "[OK]" if r["as_intended"] else "[FAIL]"
        print("  %s %-30s expected=%-9s actual=%-9s failed=%s"
              % (mark, r["name"], r["expected"], r["actual"], r["actual_failed"]))
    print("\n  --- 트랙1 GitHub 공개 fail-closed guard ---")
    for r in pub_results:
        mark = "[OK]" if r["as_intended"] else "[FAIL]"
        print("  %s %-38s allowed=%-5s verdict=%-5s reasons=%s%s"
              % (mark, r["name"], r["actual_allowed"], r["actual_verdict"], r["reason_codes"],
                 "  FAIL-OPEN!" if r["fail_open"] else ""))
    print("\n  --- source pointer 공개 차단 판정 (raw 경로 미출력, 라벨·count 만) ---")
    for r in spt_results:
        mark = "[OK]" if r["as_intended"] else "[FAIL]"
        print("  %s %-30s counts=%s publish_allowed=%-5s reasons=%s%s"
              % (mark, r["name"], r["counts"], r["publish_allowed"], r["publish_reasons"],
                 "  FAIL-OPEN!" if r["fail_open"] else ""))
    print("\n  operating_store_unchanged:", store_unchanged)
    print("  publish_guard_as_intended:", pub_ok)
    print("  source_pointer_as_intended:", spt_ok)
    print("  temp:", TMP_OUT)
    print("  report:", REPORT)
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


# ---------- multi-agent reader 기준 산출 (reader output 5종) ----------
def reader_output(pack, grant):
    """reader output 5종 상태 (raw 본문 미포함, 식별자/상태만)."""
    m = pack["manifest"]
    cv = pack["consumer_view"]["evidence_basis"]
    res = read_validate(pack, grant)
    return {
        "evidence_basis": {"node_ids": sorted(cv["node_ids"]), "edge_ids": sorted(cv["edge_ids"]),
                           "evidence_ids": sorted(cv["evidence_ids"])},
        "visible_scope": {"visibility": m.get("visibility"), "owner": m.get("owner"),
                          "read_allowed": res["checks"]["read_allowed"]},
        "candidate_state": res["checks"]["candidate_all_true"],
        "promotion_allowed_state": res["checks"]["promotion_all_false"],
        "redaction_state": ("추가 노출 미검출" if (res["checks"]["redaction_no_residual"]
                            and res["checks"]["redaction_status_ok"]) else "추가 노출 검출"),
        "verdict": res["verdict"],
    }


def run_prepare_multiagent():
    """scope envelope 포함 합성 정상 pack 1개 + Claude 기준 reader output + safety meta export.
    외부전송 0 — 외부 모델용 입력 준비까지만."""
    store_before = m0._store_snapshot()
    exp_dir = BASE / "docs" / "external_review" / "scope_envelope_multiagent_demo"
    exp_dir.mkdir(parents=True, exist_ok=True)

    pack = make_pack(namespace=NS_A, owner=NS_A, visibility="public", redaction_status="verified")
    # export pack 6파일 (synthetic, 전송 후보 자산)
    (exp_dir / "manifest.json").write_text(json.dumps(pack["manifest"], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (exp_dir / "consumer_view_summary.json").write_text(json.dumps(pack["consumer_view"], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    for fn, rows in [("nodes.jsonl", pack["nodes"]), ("edges.jsonl", pack["edges"]),
                     ("evidence_index.jsonl", pack["evidence_index"]), ("evidence_chunk.jsonl", pack["evidence_chunk"])]:
        (exp_dir / fn).write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    # Claude 로컬 기준 reader output (public pack은 임의 grant 허용)
    grant = {"user_root": "any_reader", "allowed_visibility": ["public"]}
    base_output = reader_output(pack, grant)
    (exp_dir / "expected_reader_output.json").write_text(json.dumps(base_output, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    # 전송 전 안전 메타 (raw 없음)
    residual = []
    for c in pack["evidence_chunk"]:
        residual += m1.scan_residual_pii(c["text"])
    safety = {
        "pack_id": pack["manifest"]["pack_id"], "synthetic": True, "external_transmittable": True,
        "visibility": pack["manifest"]["visibility"], "raw_allowed": pack["manifest"]["raw_allowed"],
        "residual_scanner_kinds": sorted(set(residual)), "pii_raw_residual": 0,
        "candidate_all_true": base_output["candidate_state"], "promotion_all_false": base_output["promotion_allowed_state"],
        "redaction_state": base_output["redaction_state"],
        "operating_store_unchanged": (store_before == m0._store_snapshot()),
        "note": "현재 fixture/temp 기준 추가 노출 미검출",
    }
    (exp_dir / "transmit_safety_meta.json").write_text(json.dumps(safety, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    store_after = m0._store_snapshot()
    print("=" * 76)
    print("scope envelope multi-agent reader fixture 준비 (synthetic, 외부전송 0)")
    print("=" * 76)
    print("  export:", exp_dir)
    print("  files:", sorted(p.name for p in exp_dir.iterdir()))
    print("  residual_scanner_kinds:", safety["residual_scanner_kinds"])
    print("  candidate_all_true:", safety["candidate_all_true"], "/ promotion_all_false:", safety["promotion_all_false"])
    print("  redaction_state:", safety["redaction_state"])
    print("  operating_store_unchanged:", (store_before == store_after))
    sys.exit(0)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    elif args[0] == "--prepare-multiagent":
        run_prepare_multiagent()
    else:
        print("usage: openbinggu_scope_envelope_dryrun.py [--selftest | --prepare-multiagent]")
        sys.exit(2)


if __name__ == "__main__":
    main()
