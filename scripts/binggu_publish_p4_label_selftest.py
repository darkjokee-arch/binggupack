"""P4 selftest — data_class 인자화 + candidate/active 라벨 분리 + 실 ledger 라벨 정정.

temp 전용(합성 ledger는 로직 검증용·실 ledger 무접촉). cloud/DB 0 / 실 ledger write 0 / mtime 불변.
GATE=GO 조건: 전 항목 PASS.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_p4_label as P4
import binggu_cloud_pack_export as EXP

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


_SCHEMA = """
CREATE TABLE nodes (node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT, candidate INTEGER,
                    promotion_allowed INTEGER, state TEXT, supersedes TEXT, pack_id TEXT,
                    content_hash TEXT, created_at TEXT);
CREATE TABLE edges (edge_id TEXT PRIMARY KEY, relation TEXT, source TEXT, target TEXT, candidate INTEGER,
                    state TEXT, evidence_refs TEXT, pack_id TEXT, content_hash TEXT, created_at TEXT);
CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, sentence TEXT, source_pointer_id TEXT,
                       source_hash TEXT, redaction_policy TEXT, pack_id TEXT, created_at TEXT);
"""


def _make_ledger(path, nodes):
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    for i, (nid, ntype, sent, cand, state) in enumerate(nodes):
        conn.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,created_at)"
                     " VALUES(?,?,?,?,?,?,?)", (nid, ntype, sent, cand, state, "h%d" % i, "t0"))
    conn.commit(); conn.close()


def main():
    tmp = tempfile.mkdtemp(prefix="bgp_p4_")
    home = os.path.expanduser("~")
    real_led = os.path.join(home, ".binggupack", "ledger.sqlite")
    real_mtime = os.path.getmtime(real_led) if os.path.exists(real_led) else None

    # ── 1. data_class 인자화 4종 (build_cloud_pack) ──
    n, ev, g, c = EXP.synthetic_approved()
    def _dc(dc):
        out = tempfile.mkdtemp(prefix="p4dc_")
        return EXP.build_cloud_pack(out, n, ev, g, c, data_class=dc)["manifest"]
    check("1.synthetic_fixture release_ready False", _dc("synthetic_fixture")["release_ready"] is False)
    check("2.real_candidate release_ready False", _dc("real_candidate")["release_ready"] is False)
    check("3.real_active release_ready True(통과시)", _dc("real_active")["release_ready"] is True)
    try:
        _dc("real_release"); check("4.비허용 data_class 거부", False)
    except ValueError:
        check("4.비허용 data_class 거부", True)

    # ── 5. candidate ledger → real_candidate 빌드, release 금지 ──
    led_c = os.path.join(tmp, "cand.sqlite")
    _make_ledger(led_c, [
        ("N1", "증거", "[합성] 로그에 오타가 세 번 찍혔다", 1, "candidate"),
        ("N2", "상태", "[합성] 빌드가 깨져 있다", 1, "candidate"),
        ("N3", "판단", "[합성] 배포 전 한 번 더 확인하자", 1, "candidate"),
    ])
    out5 = os.path.join(tmp, "out5"); db5 = os.path.join(tmp, "q5.sqlite")
    r5 = P4.build_real_pack(led_c, out_dir=out5, db_path=db5, state="candidate")
    check("5.candidate → DRYRUN_OK", r5["status"] == "DRYRUN_OK")
    check("6.data_class=real_candidate", r5.get("data_class") == "real_candidate")
    check("7.candidate release_ready False(release 금지)", r5.get("release_ready") is False)
    check("8.real_active/real_release 라벨 아님", r5.get("data_class") not in ("real_active", "real_release"))
    check("9.cloud/db/upload False",
          r5["cloud_upload"] is False and r5["db_insert"] is False and r5["upload_executed"] is False)
    check("10.ZIP/hash/plan 보고 존재",
          bool(r5.get("bundle_hash")) and "deploy_plan" in r5.get("deploy", {})
          and r5["deploy"]["deploy_plan"]["executed"] is False)

    # ── 11. 같은 ledger state=active → active 0 → NO_REAL_LEDGER_DATA ──
    r11 = P4.build_real_pack(led_c, out_dir=os.path.join(tmp, "o11"), db_path=os.path.join(tmp, "q11.sqlite"),
                             state="active")
    check("11.candidate-only에서 active 요청 → NO_REAL_LEDGER_DATA",
          r11["status"] == "BLOCK" and r11["reason"] == "NO_REAL_LEDGER_DATA")

    # ── 12. active ledger → real_active 빌드 ──
    led_a = os.path.join(tmp, "active.sqlite")
    _make_ledger(led_a, [
        ("A1", "증거", "[합성] 로그에 오타가 세 번 찍혔다", 0, "confirmed"),
        ("A2", "상태", "[합성] 빌드가 깨져 있다", 0, "confirmed"),
        ("A3", "판단", "[합성] 배포 전 한 번 더 확인하자", 0, "confirmed"),
    ])
    out12 = os.path.join(tmp, "out12"); db12 = os.path.join(tmp, "q12.sqlite")
    r12 = P4.build_real_pack(led_a, out_dir=out12, db_path=db12, state="active")
    check("12.active → DRYRUN_OK + data_class=real_active",
          r12["status"] == "DRYRUN_OK" and r12.get("data_class") == "real_active")

    # ── 13. empty ledger → NO_REAL_LEDGER_DATA ──
    led_e = os.path.join(tmp, "empty.sqlite"); _make_ledger(led_e, [])
    r13 = P4.build_real_pack(led_e, out_dir=os.path.join(tmp, "o13"), db_path=os.path.join(tmp, "q13.sqlite"),
                             state="candidate")
    check("13.empty → NO_REAL_LEDGER_DATA", r13["status"] == "BLOCK" and r13["reason"] == "NO_REAL_LEDGER_DATA")

    # ── 14. 실 ledger 라벨 정정 (현 상태 candidate 4) ──
    out14 = os.path.join(tmp, "out14"); db14 = os.path.join(tmp, "q14.sqlite")
    r_real = P4.build_real_pack(out_dir=out14, db_path=db14, state="candidate")
    check("14.실 ledger candidate → real_candidate DRYRUN_OK",
          r_real["status"] == "DRYRUN_OK" and r_real.get("data_class") == "real_candidate"
          and r_real.get("release_ready") is False)

    # ── 15. 실 ledger 무접촉 mtime ──
    if real_mtime is not None:
        check("15.실 ledger 무접촉(mtime 불변)", abs(os.path.getmtime(real_led) - real_mtime) < 1e-6)
    else:
        check("15.실 ledger 무접촉(파일 없음)", True)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"GATE={gate}")
    print("REAL_LEDGER_P4:", r_real.get("ledger_stats"), "->", r_real.get("data_class"), r_real.get("release_status"))
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
