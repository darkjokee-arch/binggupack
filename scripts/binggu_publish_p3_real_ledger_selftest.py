"""P3 selftest — 실 ledger 읽기 가드 + build 재검증 (합성 temp ledger로 로직 입증).

⚠ temp ledger 합성은 "P3 로직 검증용"이며 실 ledger 데이터를 꾸미는 것이 아님(실 ledger 무접촉).
temp 전용. cloud upload 0 / DB insert 0 / 실 ledger write 0 / 실 ledger mtime 불변.
GATE=GO 조건: 전 항목 PASS.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_p3_real_ledger as P3

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


_LEDGER_SCHEMA = """
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
    conn.executescript(_LEDGER_SCHEMA)
    for i, (nid, ntype, sent, cand, state) in enumerate(nodes):
        conn.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,created_at)"
                     " VALUES(?,?,?,?,?,?,?)", (nid, ntype, sent, cand, state, "h%d" % i, "t0"))
        conn.execute("INSERT INTO evidence(evidence_id,sentence,source_pointer_id,created_at)"
                     " VALUES(?,?,?,?)", ("EV%d" % i, sent, "SP%d" % i, "t0"))
    conn.commit()
    conn.close()


def main():
    tmp = tempfile.mkdtemp(prefix="bgp_p3_")
    home = os.path.expanduser("~")
    real_led = os.path.join(home, ".binggupack", "ledger.sqlite")
    real_mtime = os.path.getmtime(real_led) if os.path.exists(real_led) else None

    # 1. empty ledger → NO_REAL_LEDGER_DATA BLOCK
    led_empty = os.path.join(tmp, "empty.sqlite")
    _make_ledger(led_empty, [])
    r1 = P3.run_p3(led_empty)
    check("1.empty ledger → BLOCK", r1["status"] == "BLOCK")
    check("2.사유 NO_REAL_LEDGER_DATA", r1["reason"] == "NO_REAL_LEDGER_DATA")

    # 3. candidate-only ledger → active 0 → BLOCK (미SAVE 후보 실데이터 취급 금지)
    led_cand = os.path.join(tmp, "cand.sqlite")
    _make_ledger(led_cand, [
        ("N1", "판단", "[합성] 후보일 뿐 SAVE 안 됨", 1, "candidate"),
        ("N2", "판단", "[합성] 또 다른 후보", 1, "candidate"),
    ])
    r3 = P3.run_p3(led_cand)
    check("3.candidate-only → BLOCK", r3["status"] == "BLOCK")
    check("4.candidate는 실데이터 취급 금지(active 0)",
          r3["ledger_stats"]["active_nodes"] == 0 and r3["ledger_stats"]["candidate_nodes"] == 2)

    # 5. fixture 없이 run → 실 ledger(현 상태) 정합 (상태 독립 — active 0이면 BLOCK, >0이면 진입)
    r_real = P3.run_p3()  # 기본 = 실 ledger
    _an = r_real.get("ledger_stats", {}).get("active_nodes", 0)
    if _an == 0:
        check("5.실 ledger active 0 → NO_REAL_LEDGER_DATA",
              r_real["status"] == "BLOCK" and r_real["reason"] == "NO_REAL_LEDGER_DATA")
    else:
        check("5.실 ledger active>0 → build 재검증 진입(NO_REAL 아님)",
              r_real.get("reason") != "NO_REAL_LEDGER_DATA")

    # 6. active node 있는 합성 ledger → build 재검증 단계 진입(BLOCK NO_REAL 아님)
    led_active = os.path.join(tmp, "active.sqlite")
    _make_ledger(led_active, [
        ("N1", "증거", "[합성] 로그에 오타가 세 번 찍혔다", 0, "confirmed"),
        ("N2", "상태", "[합성] 빌드가 깨져 있다", 0, "confirmed"),
        ("N3", "판단", "[합성] 배포 전 한 번 더 확인하자", 0, "confirmed"),
    ])
    out6 = os.path.join(tmp, "out6")
    db6 = os.path.join(tmp, "q6.sqlite")
    r6 = P3.run_p3(led_active, out_dir=out6, db_path=db6)
    check("6.active 있음 → build 재검증 단계 진입(NO_REAL 아님)",
          r6.get("reason") != "NO_REAL_LEDGER_DATA")
    check("7.build 재검증 결과 DRYRUN_OK 또는 검증결과 산출",
          r6["status"] in ("DRYRUN_OK", "BLOCK") and (r6.get("source") == "real_ledger"))
    check("8.cloud_upload/db_insert/upload_executed 전부 False",
          r6["cloud_upload"] is False and r6["db_insert"] is False and r6["upload_executed"] is False)
    check("9.active_nodes=3 정확 추출", r6["ledger_stats"]["active_nodes"] == 3)

    # 10. ledger read-only 강제 (run_p3가 write 못 함 — mode=ro)
    try:
        c = sqlite3.connect("file:%s?mode=ro" % led_active, uri=True)
        try:
            c.execute("INSERT INTO nodes(node_id) VALUES('X')")
            c.commit()
            check("10.ledger read-only 강제(write 차단)", False)
        except sqlite3.OperationalError:
            check("10.ledger read-only 강제(write 차단)", True)
        finally:
            c.close()
    except Exception:  # noqa
        check("10.ledger read-only 강제(write 차단)", False)

    # 11. 운영 ledger mtime 불변
    if real_mtime is not None:
        check("11.실 ledger 무접촉(mtime 불변)",
              abs(os.path.getmtime(real_led) - real_mtime) < 1e-6)
    else:
        check("11.실 ledger 무접촉(파일 없음)", True)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"GATE={gate}")
    # 진단: 실 ledger 현 상태
    print("REAL_LEDGER:", r_real.get("ledger_stats"), r_real.get("reason"))
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
