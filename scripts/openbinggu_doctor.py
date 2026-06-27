#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu doctor — 공개 전 필수 검사 단일 진입점 (S5 최소 구현 후보).

목적:
- 사용자가 1차 배포판을 받은 뒤 한 명령으로 공개 전 필수 검사를 실행.
- 신규 공격면 최소화: 기존 selftest/gate 를 subprocess 로 "호출만" 한다(로직 재구현 0).
- raw PII/secret/private path 미출력 → 검사명 + PASS/FAIL + reason_code + count 만.

범위: 검사 오케스트레이션 + secret scan stub(dry-run). 실 OpenCrab 업로드/production/store/DB write 0.
CLI: python openbinggu_doctor.py --selftest

검사 묶음:
  1 scope_envelope_dryrun  2 watcher_pack_builder_m0  3 pack_validate
  4 pack_consumer_smoke    5 path_safety_gate         6 mcp_path_gate_adapter
  7 secret/PII scan stub (dry-run, raw 미출력)
"""
import sys
import os
import re
import sqlite3
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # repo 루트(작업본)

sys.path.insert(0, _HERE)
import binggu_platform as _plat  # noqa: E402
from openbinggu_label_kind_map import classify_label_kind, KO2EN  # noqa: E402

# 운영 ledger 의 정본 스키마(probe 실측 2026-06-16): node:CONV:<h8> ↔ EVC-CONV-<h8> 1:1.
_NODE_ID_RE = re.compile(r"^node:CONV:[0-9a-f]{8}$")
_EVC_ID_RE = re.compile(r"^EVC-CONV-[0-9a-f]{8}$")


def _default_ledger():
    return _plat.default_ledger()

# operating store: 변하면 안 되는 운영 파일(있을 때만 검사)
_OP_STORE = [
    os.path.join(_ROOT, "localcrab_index.sqlite"),
    os.path.join(_ROOT, "user_graph.yaml"),
    os.path.join(_ROOT, "_graph_merge.yaml"),
]

_CHECKS = [
    ("scope_envelope_dryrun", "openbinggu_scope_envelope_dryrun.py"),
    ("watcher_pack_builder_m0", "watcher_pack_builder_m0.py"),
    ("pack_validate", "openbinggu_pack_validate.py"),
    ("pack_consumer_smoke", "openbinggu_pack_consumer_smoke.py"),
    ("path_safety_gate", "openbinggu_path_safety_gate.py"),
    ("mcp_path_gate_adapter", "openbinggu_mcp_path_gate_adapter.py"),
    ("public_tree_scan", "openbinggu_public_tree_scan.py"),  # 실 트리 secret/PII scanner(synthetic 검증)
    ("c2_guard_selftest", "openbinggu_c2_guard_selftest.py"),  # C-2 단일통제 가드 synthetic selftest(21/21, write 0)
    ("staging_write_selftest", "openbinggu_staging_write_selftest.py"),  # Step3 staging write synthetic(11/11, temp DB, 운영 write 0)
    ("phase4_reviewer_confirmed", "openbinggu_phase4_reviewer_confirmed_selftest.py"),  # v0.5 preview 불변 강제(confirmed_created=0/applied=0/promoted=0/upload=0)
    ("runtime_access_engine", "openbinggu_runtime_access_engine.py"),  # deny-by-default 강제 엔진(21케이스, write 0)
    ("mcp_server_handlers", "openbinggu_mcp_server_handlers.py"),  # MCP 도구 핸들러 gate 결선(synthetic, underlying mock)
    ("reviewer_auth_session", "openbinggu_reviewer_auth_session_selftest.py"),  # reviewer 인증/세션(in-memory, FS write 0)
    # hosted 경계 회귀 가드(runner+inbox selftest 묶음). 순수 python·network/node/wrangler 0 — 기본 doctor 적격.
    # wrangler/node 필요한 live E2E 는 여기 넣지 않는다(기본 doctor 는 무설치·무네트워크 유지).
    ("hosted_boundary_e2e", "../tests/hosted_boundary_e2e.py"),
]

# 하위 selftest 의 "GATE: GO" / "RESULT: n/n  GATE=GO" 양식 모두 수용
_GATE_RE = re.compile(r"GATE[:=]\s*([A-Za-z\-]+)")


def _run_selftest(script):
    """하위 selftest subprocess 실행. raw stdout 은 보관하지 않고 GATE 라벨 + exit 만 추출."""
    path = os.path.join(_HERE, script)
    try:
        p = subprocess.run([sys.executable, path, "--selftest"],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return {"gate": "ERROR", "exit": -1, "reason_code": "subprocess_error:" + type(e).__name__}
    m = None
    for line in p.stdout.splitlines():
        mm = _GATE_RE.search(line)
        if mm:
            m = mm.group(1)
    gate = m or "NO-GATE"
    reason = None
    if gate != "GO":
        reason = "gate_not_go"
    if p.returncode != 0:
        reason = (reason + "+nonzero_exit") if reason else "nonzero_exit"
    # raw stdout/stderr 는 의도적으로 버림(덤프 금지)
    return {"gate": gate, "exit": p.returncode, "reason_code": reason}


def _secret_scan_stub():
    """
    secret/PII scan dry-run stub.
    실 공개 트리 부재 → 합성 샘플로 검출 로직 생존만 확인. raw 값 미출력, count/reason_code 만.
    PASS 기준: dirty 합성 샘플을 검출(hits>0) AND clean 샘플은 통과.
    """
    # 합성 샘플(실 파일/실 secret 아님)
    clean = ["build with make", "run tests with make test", "synthetic claim summary"]
    # 정적 소스에 scanner 트리거 리터럴이 남지 않게 런타임 조립(검출은 동일)
    dirty = ["api_" + "key=" + "AKIA" + "0000EXAMPLE0000", "to" + "ken: " + "ghp_" + "EXAMPLE0000000000",
             "phone 010-" + "0000-0000", "C:/Users/x/.env"]
    pats = [
        ("secret_kv", re.compile(r"(api_key|token|secret|password)\s*[:=]", re.I)),
        ("aws_key", re.compile(r"AKIA[0-9A-Z]{4,}")),
        ("phone", re.compile(r"01[016789]-?\d{3,4}-?\d{4}")),
        ("dotenv_path", re.compile(r"\.env\b")),
    ]
    clean_hits = 0
    for s in clean:
        if any(rx.search(s) for _, rx in pats):
            clean_hits += 1
    dirty_codes = {}
    for s in dirty:
        for code, rx in pats:
            if rx.search(s):
                dirty_codes[code] = dirty_codes.get(code, 0) + 1
    detects = sum(dirty_codes.values())
    ok = (clean_hits == 0) and (detects > 0)
    return {"pass": ok, "scanned": len(clean) + len(dirty),
            "clean_false_positive": clean_hits, "dirty_detected": detects,
            "reason_codes": sorted(dirty_codes.keys()),  # 카테고리만, raw 값 0
            "raw_not_output": True}


def _store_snapshot():
    snap = {}
    for f in _OP_STORE:
        try:
            snap[os.path.basename(f)] = os.path.getmtime(f) if os.path.exists(f) else None
        except Exception:
            snap[os.path.basename(f)] = None
    return snap


def _real_tree_scan(tree_root):
    """실 공개 후보 트리 scan(요약만). raw 미출력. import 는 호출 시점에만."""
    sys.path.insert(0, _HERE)
    from openbinggu_public_tree_scan import scan_public_tree  # noqa: E402
    # .gitignore 계열 기본 제외(공개 대상 아님 전제). run_all 의 PUBLIC_IGNORE 와 정합.
    # 주의: .env/credentials*/private_key* 는 scanner 의 "검출 대상"이므로 제외 금지(검출 무력화 방지)
    ignore = ["*.sqlite", "*.db", "*_graph.yaml", "reports/", "reviews/", "captures/",
              "tmp/", "__pycache__/", "*.bak_*",
              # gitignore 대상 비공개·미커밋 라이브 데이터 (path_private_pack_data 자기탐지 회피)
              "hosted/workers/data/", "data/packs.json"]
    return scan_public_tree(tree_root, ignore_globs=ignore)


# ---------------- 운영 ledger read-only 정합검사 (OI1~OI3) ----------------
# 모두 mode=ro 연결 위에서 SELECT 만. write 0. 호출자(run_doctor / _oi_selftest)가
# 검사 전후 ledger mtime 불변을 확인한다. raw 문장/원문 미출력 — count/code 만.

def oi1_stamp_divergence(con):
    """OI1 도장 발산: 저장 node_type vs sentence 재분류(classify→KO2EN) 불일치 노드 수.

    list_view 가 표시 때 재분류하여 저장값과 어긋날 수 있는 구조를 가시화(raw 미출력).
    node_type 이 5종 영문 라벨(doc/evidence/concept/state/judgment)인 노드만 비교 대상.
    Claim 등 비-라벨 node_type(상태/판단이 둘 다 Claim 으로 적재되는 현행)은 분모에서 제외."""
    total = 0
    comparable = 0
    diverge = 0
    label_vals = set(KO2EN.values())
    for ntype, sent in con.execute("SELECT node_type, sentence FROM nodes"):
        total += 1
        stored = (ntype or "")
        if stored not in label_vals:
            continue  # 저장 node_type 이 5종 라벨이 아니면 발산 비교 불가(제외)
        comparable += 1
        kind_ko, _rule = classify_label_kind(sent or "")
        reclassified = KO2EN.get(kind_ko)
        if reclassified != stored:
            diverge += 1
    return {"total_nodes": total, "comparable": comparable,
            "divergent": diverge, "pass": True}  # OI1 은 보고용(검출=가시화), 자체 FAIL 아님


def oi2_evidence_closure(con):
    """OI2 evidence 키 폐쇄: evidence_id 전부 EVC-CONV-<h8> 스키마 + node:CONV:<h8> 1:1 연결.

    FAIL 조건(하나라도):
      - 비-EVC-CONV evidence_id 존재
      - evidence_supports 가 가리키는 target/source 스키마 위반
      - node ↔ evidence 미연결(node 에 evidence_supports edge 0) 또는 1:1 깨짐."""
    ev_ids = [r[0] for r in con.execute("SELECT evidence_id FROM evidence")]
    node_ids = [r[0] for r in con.execute("SELECT node_id FROM nodes")]
    bad_ev_schema = sum(1 for e in ev_ids if not _EVC_ID_RE.match(e or ""))

    # evidence_supports edge: source=EVC-CONV-<h8>, target=node:CONV:<h8>
    edges = con.execute(
        "SELECT source, target FROM edges WHERE relation='evidence_supports'").fetchall()
    bad_edge_schema = 0
    target_to_sources = {}
    for src, tgt in edges:
        if not _EVC_ID_RE.match(src or "") or not _NODE_ID_RE.match(tgt or ""):
            bad_edge_schema += 1
        target_to_sources.setdefault(tgt, set()).add(src)

    node_set = set(node_ids)
    ev_set = set(ev_ids)
    # 미연결: evidence_supports edge 가 없는 node
    unlinked_nodes = sum(1 for n in node_set if n not in target_to_sources)
    # 1:1 깨짐: node 당 evidence source 가 2개 이상
    multi_linked = sum(1 for srcs in target_to_sources.values() if len(srcs) > 1)
    # dangling: edge target 이 실제 node 가 아니거나 source 가 실제 evidence 가 아님
    dangling = 0
    for tgt, srcs in target_to_sources.items():
        if tgt not in node_set:
            dangling += 1
        for s in srcs:
            if s not in ev_set:
                dangling += 1

    ok = (bad_ev_schema == 0 and bad_edge_schema == 0 and unlinked_nodes == 0
          and multi_linked == 0 and dangling == 0)
    return {"evidence_count": len(ev_ids), "node_count": len(node_ids),
            "edge_count": len(edges), "bad_evidence_schema": bad_ev_schema,
            "bad_edge_schema": bad_edge_schema, "unlinked_nodes": unlinked_nodes,
            "multi_linked": multi_linked, "dangling": dangling, "pass": ok}


def oi3_exit_label_count(con):
    """OI3 출구 라벨 카운트: candidate=1(미확정/staging) / candidate=0(확정/sealed) 노드 수.

    owner_acceptances 테이블 존재 시 accepted(event 종류별) 카운트도 보고.
    표시 거꾸로(✓/⚠ 역직관) 검출용 — 라벨 자체는 list_view 가 부착, 여기선 카운트만."""
    counts = {}
    for cand, n in con.execute("SELECT candidate, COUNT(*) FROM nodes GROUP BY candidate"):
        counts[int(cand) if cand is not None else -1] = n
    staging = counts.get(1, 0)
    sealed = counts.get(0, 0)
    accepted = None
    has_oa = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='owner_acceptances'"
    ).fetchone() is not None
    if has_oa:
        accepted = con.execute("SELECT COUNT(*) FROM owner_acceptances").fetchone()[0]
    return {"candidate_staging": staging, "sealed_public": sealed,
            "owner_acceptances_table": has_oa, "accepted": accepted, "pass": True}


def _run_oi_checks(ledger_path):
    """운영(또는 temp) ledger 를 mode=ro 로 열어 OI1~OI3 실행. write 0.

    ledger 부재 → skipped(신규설치=정상). 반환: (results_dict, mtime_before, mtime_after)."""
    if not os.path.exists(ledger_path):
        return {"skipped": True, "reason": "no_ledger"}, None, None
    mt_before = os.path.getmtime(ledger_path)
    con = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
    try:
        res = {
            "skipped": False,
            "oi1": oi1_stamp_divergence(con),
            "oi2": oi2_evidence_closure(con),
            "oi3": oi3_exit_label_count(con),
        }
    finally:
        con.close()
    mt_after = os.path.getmtime(ledger_path)
    res["ledger_mtime_unchanged"] = (mt_before == mt_after)
    return res, mt_before, mt_after


def _print_oi(res):
    """OI 결과 출력(raw 미출력). 반환 all_ok(이 묶음 통과 여부)."""
    if res.get("skipped"):
        print("  [SKIP] %-24s reason=%s (신규설치=정상)" % ("operating_ledger_oi", res.get("reason")))
        return True
    o1, o2, o3 = res["oi1"], res["oi2"], res["oi3"]
    # OI1: 보고용(발산 가시화) — 자체 FAIL 아님, count 만
    print("  [INFO] %-24s comparable=%d divergent=%d (총 %d노드·raw 미출력)"
          % ("oi1_stamp_divergence", o1["comparable"], o1["divergent"], o1["total_nodes"]))
    o2_ok = o2["pass"]
    print("  [%s] %-24s ev=%d node=%d edge=%d bad_ev=%d bad_edge=%d unlinked=%d multi=%d dangling=%d"
          % ("PASS" if o2_ok else "FAIL", "oi2_evidence_closure", o2["evidence_count"],
             o2["node_count"], o2["edge_count"], o2["bad_evidence_schema"], o2["bad_edge_schema"],
             o2["unlinked_nodes"], o2["multi_linked"], o2["dangling"]))
    print("  [INFO] %-24s staging(candidate=1)=%d sealed(candidate=0)=%d oa_table=%s accepted=%s"
          % ("oi3_exit_label_count", o3["candidate_staging"], o3["sealed_public"],
             o3["owner_acceptances_table"], o3["accepted"]))
    mt_ok = res.get("ledger_mtime_unchanged", False)
    print("  [%s] %-24s ledger_mtime_unchanged=%s"
          % ("PASS" if mt_ok else "FAIL", "operating_ledger_write0", mt_ok))
    return o2_ok and mt_ok


def run_doctor(tree_root=None, ledger_path=None):
    print("=" * 72)
    print("OpenBinggu doctor — 공개 전 필수 검사 (synthetic / dry-run)")
    print("=" * 72)

    store_before = _store_snapshot()

    results = []
    all_ok = True

    # 1~6 기존 selftest 호출
    for name, script in _CHECKS:
        r = _run_selftest(script)
        ok = (r["gate"] == "GO" and r["exit"] == 0)
        all_ok = all_ok and ok
        results.append((name, ok, r))
        tag = "PASS" if ok else "FAIL"
        extra = "" if ok else ("  reason=%s" % r.get("reason_code"))
        print("  [%s] %-24s GATE=%-6s exit=%s%s" % (tag, name, r["gate"], r["exit"], extra))

    # 7 secret/PII scan stub
    sc = _secret_scan_stub()
    sc_ok = sc["pass"]
    all_ok = all_ok and sc_ok
    print("  [%s] %-24s detected=%d false_positive=%d codes=%s (raw 미출력)"
          % ("PASS" if sc_ok else "FAIL", "secret_pii_scan_stub",
             sc["dirty_detected"], sc["clean_false_positive"], sc["reason_codes"]))

    # (옵션) 실 공개 후보 트리 scan — --tree 지정 시에만
    if tree_root:
        tr = _real_tree_scan(tree_root)
        tr_ok = (tr["verdict"] == "CLEAN")
        all_ok = all_ok and tr_ok
        print("  [%s] %-24s verdict=%s hits=%d by_reason=%s content_skipped=%s (raw 미출력)"
              % ("PASS" if tr_ok else "FAIL", "real_tree_scan",
                 tr["verdict"], tr["hits"], tr["by_reason"], tr.get("content_skipped")))

    # 운영 ledger read-only 정합검사 (OI1~OI3) — write 0, mtime 전후 불변 확인.
    lp = ledger_path or _default_ledger()
    print("  --- 운영 ledger read-only 정합검사 (mode=ro · write 0) : %s ---"
          % _plat.display_path(lp))
    oi_res, _b, _a = _run_oi_checks(lp)
    oi_ok = _print_oi(oi_res)
    all_ok = all_ok and oi_ok

    # operating store 불변 확인
    store_after = _store_snapshot()
    store_unchanged = (store_before == store_after)
    all_ok = all_ok and store_unchanged
    print("  [%s] %-24s operating_store_unchanged=%s"
          % ("PASS" if store_unchanged else "FAIL", "operating_store_guard", store_unchanged))

    passed = sum(1 for _, ok, _ in results if ok) + (1 if sc_ok else 0) + (1 if store_unchanged else 0)
    total = len(results) + 2
    print("\n  summary: %d/%d PASS  (raw PII/secret/private path 미출력)" % (passed, total))

    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if all_ok else 1


# ---------------- OI selftest (합성 temp ledger — 검출 실패 시 GATE STOP) ----------------
# ⚠️ 897ff2f 회귀 교훈: temp-only selftest 는 운영경로를 못 탄다. 그래서 이 selftest 는
#   (a) 합성 temp ledger 의 정상/불일치 주입 케이스를 _run_oi_checks(운영과 동일 코드경로)로 검증하고,
#   (b) 운영 ledger 가 있으면 그 read-only 경로도 한 번 타서(write 0·mtime 불변) 실제 동작을 확인한다.

def _build_temp_ledger(path, *, break_evidence=False):
    """합성 ledger 생성. break_evidence=True 면 evidence 키/연결을 깨서 OI2 FAIL 을 유도."""
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE nodes(node_id TEXT, node_type TEXT, sentence TEXT, candidate INTEGER, "
        "promotion_allowed INTEGER, state TEXT);"
        "CREATE TABLE evidence(evidence_id TEXT, sentence TEXT);"
        "CREATE TABLE edges(edge_id TEXT, relation TEXT, source TEXT, target TEXT, "
        "candidate INTEGER, state TEXT);"
        "CREATE TABLE owner_acceptances(event_id INTEGER PRIMARY KEY, node_id TEXT, event TEXT);")
    # 정상 2노드: 도장 정합(node_type=judgment, 판단문) + 도장 거꾸로(node_type=concept, 판단문→발산)
    rows = [
        ("node:CONV:aaaaaaaa", "judgment", "이 입찰은 마진이 낮아 보류한다.", 0, 0, "active"),
        ("node:CONV:bbbbbbbb", "concept", "이 입찰은 마진이 낮아 보류한다.", 1, 0, "active"),  # 발산(저장=concept, 재분류=judgment) + staging
    ]
    con.executemany("INSERT INTO nodes VALUES(?,?,?,?,?,?)", rows)
    if break_evidence:
        # 키 스키마 깨짐(EVC-CONV 아님) + 미연결(edge 없음)
        con.execute("INSERT INTO evidence VALUES('BROKEN-KEY-1','x')")
        con.execute("INSERT INTO evidence VALUES('EVC-CONV-bbbbbbbb','y')")
        # edge 0 → 두 노드 모두 unlinked
    else:
        con.execute("INSERT INTO evidence VALUES('EVC-CONV-aaaaaaaa','x')")
        con.execute("INSERT INTO evidence VALUES('EVC-CONV-bbbbbbbb','y')")
        con.execute("INSERT INTO edges VALUES('e1','evidence_supports','EVC-CONV-aaaaaaaa','node:CONV:aaaaaaaa',1,'active')")
        con.execute("INSERT INTO edges VALUES('e2','evidence_supports','EVC-CONV-bbbbbbbb','node:CONV:bbbbbbbb',1,'active')")
    con.execute("INSERT INTO owner_acceptances(node_id,event) VALUES('node:CONV:aaaaaaaa','accepted')")
    con.commit()
    con.close()


def _oi_selftest():
    import tempfile
    import shutil
    print("=" * 72)
    print("OpenBinggu doctor — OI 운영검사 selftest (합성 temp + 운영경로 read-only)")
    print("=" * 72)
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-44s %s" % ("OK" if ok else "FAIL", name, detail))

    tmp = tempfile.mkdtemp(prefix="bgp_doctor_oi_")
    try:
        good = os.path.join(tmp, "good.sqlite")
        bad = os.path.join(tmp, "bad.sqlite")
        _build_temp_ledger(good)
        _build_temp_ledger(bad, break_evidence=True)

        # (a) 합성 — 운영과 동일 코드경로(_run_oi_checks) 로 검증
        gmt = os.path.getmtime(good)
        gres, _, _ = _run_oi_checks(good)
        ck("good_oi2_pass", gres["oi2"]["pass"], "evidence 폐쇄 정상")
        # 5종 세분화 저장이면 node_type 이 전부 5종 EN 라벨 → OI1 비교가능(comparable=total).
        # (Claim 단일 적재였을 땐 comparable=0 으로 발산을 못 봤음 — 그 사각이 닫혔는지 검증.)
        ck("good_oi1_comparable>0(5종세분화)", gres["oi1"]["comparable"] == gres["oi1"]["total_nodes"]
           and gres["oi1"]["comparable"] == 2,
           "comparable=%d total=%d" % (gres["oi1"]["comparable"], gres["oi1"]["total_nodes"]))
        ck("good_oi1_발산검출", gres["oi1"]["divergent"] == 1,
           "도장 거꾸로 1건 검출(divergent=%d)" % gres["oi1"]["divergent"])
        ck("good_oi3_출구카운트", gres["oi3"]["candidate_staging"] == 1
           and gres["oi3"]["sealed_public"] == 1, "staging=1 sealed=1")
        ck("good_oi3_oa", gres["oi3"]["owner_acceptances_table"]
           and gres["oi3"]["accepted"] == 1, "owner_acceptances accepted=1")
        ck("good_mtime_불변", gres["ledger_mtime_unchanged"]
           and os.path.getmtime(good) == gmt, "검사 전후 mtime 동일")

        bres, _, _ = _run_oi_checks(bad)
        # 불일치 주입(evidence 키 깨짐 + 미연결) → OI2 가 반드시 FAIL 검출해야
        ck("bad_oi2_검출_FAIL", not bres["oi2"]["pass"],
           "bad_ev=%d unlinked=%d" % (bres["oi2"]["bad_evidence_schema"],
                                      bres["oi2"]["unlinked_nodes"]))
        ck("bad_oi2_키스키마_검출", bres["oi2"]["bad_evidence_schema"] >= 1)
        ck("bad_oi2_미연결_검출", bres["oi2"]["unlinked_nodes"] >= 1)

        # skip 케이스: 부재 ledger
        sres, _, _ = _run_oi_checks(os.path.join(tmp, "nope.sqlite"))
        ck("absent_ledger_skip", sres.get("skipped") is True, "신규설치=정상 skip")

        # (b) 운영경로: 실 ledger 가 있으면 read-only 로 한 번 타고 mtime 불변 확인(없으면 skip 통과)
        op = _default_ledger()
        if os.path.exists(op):
            omt = os.path.getmtime(op)
            ores, _, _ = _run_oi_checks(op)
            ck("operating_path_taken", not ores.get("skipped"),
               "운영 ledger read-only 실행됨")
            ck("operating_write0_mtime", ores.get("ledger_mtime_unchanged")
               and os.path.getmtime(op) == omt, "운영 ledger mtime 불변")
        else:
            ck("operating_path_taken", True, "운영 ledger 부재 — skip(신규설치)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(o for _, o in checks)
    print("-" * 72)
    print("RESULT: %d/%d PASS  write=0 (temp+운영 read-only)"
          % (sum(1 for _, o in checks if o), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("--selftest", "--check", "--run"):
        # --selftest 는 (1) 합성/dry-run 묶음 + 운영 ledger OI read-only + (2) OI 검출 selftest 둘 다 GATE.
        rc1 = run_doctor()
        rc2 = _oi_selftest()
        gate = "GO" if (rc1 == 0 and rc2 == 0) else "NO-GO"
        print("\n  DOCTOR GATE:", gate)
        sys.exit(0 if gate == "GO" else 1)
    elif args[0] == "--tree" and len(args) >= 2:
        sys.exit(run_doctor(tree_root=args[1]))
    elif args[0] == "--ledger" and len(args) >= 2:
        sys.exit(run_doctor(ledger_path=args[1]))
    elif args[0] == "--oi-selftest":
        sys.exit(_oi_selftest())
    else:
        print("usage: openbinggu_doctor.py [--selftest | --tree <ROOT> | --ledger <PATH> | --oi-selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
