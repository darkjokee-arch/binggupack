"""P2 selftest — 실 빌더·검증기 연결 + 영구금지 hard fail + 배포 plan + BLOCK 케이스.

temp 전용. cloud upload 0 / DB insert 0 / 운영 mtime 불변.
GATE=GO 조건: 전 항목 PASS.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_p2_pipeline as P2
import binggu_cloud_pack_export as EXP

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def expect_block(name, fn):
    try:
        fn()
        check(name, False)
    except P2.BlockError:
        check(name, True)
    except Exception as e:  # noqa
        print(f"   (wrong exc: {type(e).__name__}: {e})")
        check(name, False)


def _fresh_out(tmp, label):
    out = os.path.join(tmp, label, "pack")
    os.makedirs(out, exist_ok=True)
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="bgp_p2_")

    # ── 정상 파이프라인 ──
    out1 = _fresh_out(tmp, "ok")
    db1 = os.path.join(tmp, "ok", "q.sqlite")
    rep = P2.run_pipeline(out1, db1, queue_id="q_ok")
    check("1.정상 → approved 도달", rep["status"] == "approved")
    check("2.guards PASS", rep.get("guards") == "PASS")
    check("3.cloud_upload=False", rep["cloud_upload"] is False)
    check("4.db_insert=False", rep["db_insert"] is False)
    check("5.upload_executed=False", rep.get("upload_executed") is False)
    dp = rep.get("deploy", {})
    check("6.deploy_plan executed=False", dp.get("deploy_plan", {}).get("executed") is False)
    check("7.rollback_plan 존재+미실행", dp.get("rollback_plan", {}).get("executed") is False
          and dp.get("rollback_plan", {}).get("prev_pack_preserved") is True)
    check("8.live_check command 준비+미실행", "command" in dp.get("live_check", {})
          and dp.get("live_check", {}).get("executed") is False)
    check("9.synthetic → release_status degraded", rep["release_status"] == "degraded")
    # manifest 파일도 cloud_upload/db_insert False
    man = json.load(open(os.path.join(out1, "manifest.json"), encoding="utf-8"))
    check("10.manifest cloud_upload/db_insert False",
          man.get("cloud_upload") is False and man.get("db_insert") is False)

    # ── BLOCK: synthetic release_ready=true (G24) ──
    out2 = _fresh_out(tmp, "rr")
    db2 = os.path.join(tmp, "rr", "q.sqlite")
    rep2 = P2.run_pipeline(out2, db2, queue_id="q_rr", force_release_ready=True)
    check("11.synthetic release_ready=true → failed", rep2["status"] == "failed")
    check("12.BLOCK 사유 G24", "G24" in (rep2.get("blocked_reason") or ""))

    # ── BLOCK: hash 불일치 ──
    out3 = _fresh_out(tmp, "th")
    db3 = os.path.join(tmp, "th", "q.sqlite")
    rep3 = P2.run_pipeline(out3, db3, queue_id="q_th", tamper_bundle=True)
    check("13.hash 불일치 → failed", rep3["status"] == "failed")
    check("14.BLOCK 사유 hash", "hash" in (rep3.get("blocked_reason") or "").lower())

    # ── validate_gate 단위: 미실행/leak/required_failures ──
    expect_block("15.검증기 미실행(None) → BLOCK", lambda: P2.validate_gate(None))
    expect_block("16.leak_count!=0 → BLOCK",
                 lambda: P2.validate_gate({"quality": {"leak_count": 1, "required_failures": [],
                                                        "edges_have_endpoints": True, "nodes_with_id": True}}))
    expect_block("17.required_failures → BLOCK",
                 lambda: P2.validate_gate({"quality": {"leak_count": 0, "required_failures": ["x"],
                                                       "edges_have_endpoints": True, "nodes_with_id": True}}))
    expect_block("18.required_failures 미산출(None) → BLOCK",
                 lambda: P2.validate_gate({"quality": {"leak_count": 0, "required_failures": None,
                                                       "edges_have_endpoints": True, "nodes_with_id": True}}))

    # ── check_permanent_guards 단위: 정상 산출물 변조로 hard fail ──
    out4 = _fresh_out(tmp, "guard")
    nodes, evidence, g, conf = EXP.synthetic_approved()
    build4 = EXP.build_cloud_pack(out4, nodes, evidence, g, conf)
    check("19.정상 산출물 guards PASS",
          P2.check_permanent_guards(out4, build4).get("guards") == "PASS")

    # 19b. new_predicates 주입 (G22)
    gr_path = os.path.join(out4, "reports", "graphrag.json")
    gr = json.load(open(gr_path, encoding="utf-8")); gr["new_predicates"] = 2
    json.dump(gr, open(gr_path, "w", encoding="utf-8"))
    expect_block("20.new_predicates!=0 → G22 BLOCK",
                 lambda: P2.check_permanent_guards(out4, build4))
    gr["new_predicates"] = 0; json.dump(gr, open(gr_path, "w", encoding="utf-8"))  # 복구

    # 20b. verb edge evidence 제거 (G27)
    ed_path = os.path.join(out4, "graph", "edges.jsonl")
    edges = [json.loads(l) for l in open(ed_path, encoding="utf-8") if l.strip()]
    for e in edges:
        if e.get("edge_kind") == "verb":
            e["evidence_refs"] = []
    with open(ed_path, "w", encoding="utf-8") as f:
        for e in edges:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    expect_block("21.verb edge evidence 누락 → G27 BLOCK",
                 lambda: P2.check_permanent_guards(out4, build4))

    # 21b. verb relation != supports (G22)
    for e in edges:
        if e.get("edge_kind") == "verb":
            e["evidence_refs"] = ["EVC-s1"]; e["relation"] = "causes"
    with open(ed_path, "w", encoding="utf-8") as f:
        for e in edges:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    expect_block("22.verb relation!=supports → G22 BLOCK",
                 lambda: P2.check_permanent_guards(out4, build4))

    # 22b. subtype canonical 승격 (G23) — nodes 변조
    out5 = _fresh_out(tmp, "g23")
    build5 = EXP.build_cloud_pack(out5, nodes, evidence, g, conf)
    nd_path = os.path.join(out5, "graph", "nodes.jsonl")
    nrows = [json.loads(l) for l in open(nd_path, encoding="utf-8") if l.strip()]
    for n in nrows:
        if n.get("node_type") == "Claim":
            n["semantic_subtype"] = n.get("label_kind")  # subtype을 canonical로 승격
            break
    with open(nd_path, "w", encoding="utf-8") as f:
        for n in nrows:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")
    expect_block("23.subtype canonical 승격 → G23 BLOCK",
                 lambda: P2.check_permanent_guards(out5, build5))

    # ── 운영 ledger 경로 거부 ──
    op = os.path.join(os.path.expanduser("~"), ".binggupack", "ledger.sqlite")
    expect_block_qerr("24.운영 ledger 경로 거부",
                      lambda: P2.run_pipeline(_fresh_out(tmp, "opx"), op, queue_id="qx"))

    # ── 운영 store mtime 불변 ──
    import binggu_publish_queue_p1 as Q  # noqa
    home = os.path.expanduser("~")
    led = os.path.join(home, ".binggupack", "ledger.sqlite")
    buf = os.path.join(home, ".binggupack", "capture_buffer.sqlite")
    check("25.운영 ledger 무접촉(mtime 불변)", _mtime_unchanged(led))
    check("26.capture_buffer 무접촉(mtime 불변)", _mtime_unchanged(buf))

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"GATE={gate}")
    return 0 if gate == "GO" else 1


# mtime baseline (모듈 import 시점)
_MTIME_BASELINE = {}


def _mtime_unchanged(path):
    if not os.path.exists(path):
        return True
    cur = os.path.getmtime(path)
    base = _MTIME_BASELINE.get(path)
    return base is None or abs(cur - base) < 1e-6


def expect_block_qerr(name, fn):
    import binggu_publish_queue_p1 as Q
    try:
        fn()
        check(name, False)
    except (Q.QueueError, P2.BlockError):
        check(name, True)
    except Exception as e:  # noqa
        print(f"   (wrong exc: {type(e).__name__}: {e})")
        check(name, False)


if __name__ == "__main__":
    # baseline 먼저 기록
    home = os.path.expanduser("~")
    for p in (os.path.join(home, ".binggupack", "ledger.sqlite"),
              os.path.join(home, ".binggupack", "capture_buffer.sqlite")):
        if os.path.exists(p):
            _MTIME_BASELINE[p] = os.path.getmtime(p)
    sys.exit(main())
