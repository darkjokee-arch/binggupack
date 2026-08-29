"""P7 selftest — candidate→active promote 정식 모듈.

temp ledger(open_g3) 전용. 실 ledger write 0 / mtime 불변.
검증: 명시 승격 · idempotent(이미 active) · 정합 BLOCK · node_not_found BLOCK · auto BLOCK · 백업 · 승격전후 정합.
GATE=GO 조건: 전 항목 PASS.
"""
from contextlib import suppress
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_p5_promote as PR
from openbinggu_deprecate_and_remind_g3 import open_g3

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def add_node(db, nid, sent, cand, with_evidence=True):
    db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,content_hash,created_at)"
                   " VALUES(?,?,?,?,?,?)", (nid, "Claim", sent, cand, "h" + nid, "t0"))
    if with_evidence:
        eid = "EVC-" + nid
        db.con.execute("INSERT INTO evidence(evidence_id,sentence,source_pointer_id,source_hash,created_at)"
                       " VALUES(?,?,?,?,?)", (eid, sent, "sp" + nid, "sh", "t0"))
        db.con.execute("INSERT INTO edges(edge_id,relation,source,target,evidence_refs,created_at)"
                       " VALUES(?,?,?,?,?,?)", ("e-" + nid, "evidence_supports", eid, nid,
                                                '["' + eid + '"]', "t0"))
    db.con.commit()


def fresh_db(tmp, label):
    return open_g3(os.path.join(tmp, label + ".sqlite"))


def main():
    tmp = tempfile.mkdtemp(prefix="bgp_p7_")
    home = os.path.expanduser("~")
    real_led = os.path.join(home, ".binggupack", "ledger.sqlite")
    real_mtime = os.path.getmtime(real_led) if os.path.exists(real_led) else None

    # ── 1. 명시 승격 (candidate→active, checksum 변화, audit ALLOW) ──
    db = fresh_db(tmp, "ok")
    add_node(db, "N1", "문장1", 1)
    add_node(db, "N2", "문장2", 1)
    r = PR.promote(db, ["N1"], {"actor": "human"})
    check("1.명시 승격 applied", r["applied"] and r["promoted"] == ["N1"])
    check("2.checksum 변화", r["checksum_changed"] is True)
    check("3.audit ALLOW 기록", r["audit_ok"] is True)
    n1 = db.con.execute("SELECT candidate FROM nodes WHERE node_id='N1'").fetchone()[0]
    n2 = db.con.execute("SELECT candidate FROM nodes WHERE node_id='N2'").fetchone()[0]
    check("4.N1 active(0) / N2 미접촉(candidate 1)", n1 == 0 and n2 == 1)
    aud = db.con.execute("SELECT count(*) FROM audit_log WHERE action='candidate_promote' AND result='ALLOW'").fetchone()[0]
    check("5.audit candidate_promote ALLOW 존재", aud >= 1)

    # ── 6. idempotent — 이미 active 재승격 (skip, checksum 불변) ──
    cs_before = db.store_checksum()
    r2 = PR.promote(db, ["N1"], {"actor": "human"})
    check("6.이미 active 재승격 idempotent", r2["applied"] and r2.get("idempotent") is True
          and r2["promoted"] == [] and r2["skipped_already_active"] == ["N1"])
    check("7.idempotent checksum 불변", r2["checksum_after"] == cs_before)

    # ── 8. 정합 깨진 node (evidence edge 없음) → BLOCK ──
    db2 = fresh_db(tmp, "broken")
    add_node(db2, "B1", "근거없음", 1, with_evidence=False)
    rb = PR.promote(db2, ["B1"], {"actor": "human"})
    check("8.정합 깨짐(evidence 없음) → BLOCK", (not rb["applied"]) and rb["reason"] == "linkage_broken_pre")
    bc = db2.con.execute("SELECT candidate FROM nodes WHERE node_id='B1'").fetchone()[0]
    check("9.BLOCK 시 승격 0(candidate 유지)", bc == 1)

    # ── 10. node_not_found → BLOCK ──
    db3 = fresh_db(tmp, "nf")
    add_node(db3, "X1", "문장", 1)
    rn = PR.promote(db3, ["X1", "GHOST"], {"actor": "human"})
    check("10.node_not_found → BLOCK", (not rn["applied"]) and rn["reason"] == "node_not_found")
    xc = db3.con.execute("SELECT candidate FROM nodes WHERE node_id='X1'").fetchone()[0]
    check("11.not_found BLOCK 시 X1 미변경", xc == 1)

    # ── 12. auto actor → BLOCK ──
    db4 = fresh_db(tmp, "auto")
    add_node(db4, "A1", "문장", 1)
    ra = PR.promote(db4, ["A1"], {"actor": "auto"})
    check("12.actor=auto → BLOCK", (not ra["applied"]) and ra["reason"] == "G4_no_auto")

    # ── 13. verify_evidence_linkage 단위 ──
    db5 = fresh_db(tmp, "verify")
    add_node(db5, "V1", "문장V", 1)
    check("13.정합 검증 ok", PR.verify_evidence_linkage(db5, ["V1"])["ok"] is True)
    # sentence mismatch 주입
    db5.con.execute("UPDATE evidence SET sentence='다른문장' WHERE evidence_id='EVC-V1'"); db5.con.commit()
    vm = PR.verify_evidence_linkage(db5, ["V1"])
    check("14.sentence mismatch 검출", (not vm["ok"]) and any(i["issue"] == "sentence_mismatch" for i in vm["issues"]))

    # ── 15. run_promote 백업 필수 + 승격 ──
    db6 = fresh_db(tmp, "runp"); db6.con.close()
    led6 = os.path.join(tmp, "runp.sqlite")
    db6b = open_g3(led6); add_node(db6b, "R1", "문장R", 1); db6b.con.close()
    bdir = os.path.join(tmp, "backups")
    rr = PR.run_promote(led6, ["R1"], bdir, {"actor": "human"}, tag="t1")
    check("15.run_promote 백업 생성", os.path.exists(rr["backup"]))
    check("16.run_promote 승격 applied", rr["applied"] and rr["promoted"] == ["R1"])

    # ── 17. 실 ledger mtime 불변 ──
    if real_mtime is not None:
        check("17.실 ledger 무접촉(mtime 불변)", abs(os.path.getmtime(real_led) - real_mtime) < 1e-6)
    else:
        check("17.실 ledger 무접촉(파일 없음)", True)

    for d in (db, db2, db3, db4, db5):
        with suppress(Exception):
            d.con.close()

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"GATE={gate}")
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
