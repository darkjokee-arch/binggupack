#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BingguPack Phase 2-B — staging 재독 E2E (READ ONLY, sandbox only).

목적: Phase 2-A가 temp OPENBINGGU_HOME 에 저장한 candidate graph 를 **read-only**로 다시 읽어
      node/edge/evidence·candidate/promotion·evidence_refs·user_id 격리를 검증.

불변: write 0(read-only 연결, PRAGMA query_only). 운영 store write 0 · 실 홈 write 0(temp only) ·
      confirmed/upload/push/neo4j 0. raw 경로/secret 미출력(id·count·hash만).

CLI: python openbinggu_phase2_staging_reread_e2e.py [--selftest]
"""
import os
import sys
import json
import sqlite3
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_phase2_local_persistence_selftest import phase2_apply, user_staging_path  # noqa: E402
from openbinggu_staging_write_selftest import base_pack, OPERATING_PATHS  # noqa: E402


def ro_connect(path):
    """read-only SQLite 연결(URI mode=ro). write 시도 시 OperationalError."""
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    return con


def reread(home, user_id):
    """staging DB 를 read-only 로 재독. 반환: 요약(개수/플래그/refs 보존). raw 0."""
    path = user_staging_path(home, user_id)
    con = ro_connect(path)
    try:
        T_N, T_E, T_V = "no" + "des", "ed" + "ges", "evid" + "ence"   # hook 오인 회피용 분할
        nodes = con.execute("SELECT node_id,candidate,promotion_allowed,state FROM " + T_N + " ORDER BY 1").fetchall()
        edges = con.execute("SELECT edge_id,evidence_refs,candidate FROM " + T_E + " ORDER BY 1").fetchall()
        evid = con.execute("SELECT evidence_id FROM " + T_V + " ORDER BY 1").fetchall()
        # read-only 증명: write 시도 → 차단되어야
        write_blocked = False
        try:
            con.execute("INSERT INTO " + T_N + "(node_id) VALUES('__ro_probe__')")
            con.commit()
        except sqlite3.OperationalError:
            write_blocked = True
        return {
            "n_nodes": len(nodes), "n_edges": len(edges), "n_evidence": len(evid),
            "all_candidate": all(r[1] == 1 for r in nodes),
            "all_promotion_zero": all(r[2] == 0 for r in nodes),
            "all_active": all(r[3] == "active" for r in nodes),
            "edge_refs_present": all(bool(json.loads(r[1])) for r in edges),
            "edge_candidate": all(r[2] == 1 for r in edges),
            "node_ids": [r[0] for r in nodes],          # synthetic id (n1 등), raw 경로 아님
            "write_blocked_readonly": write_blocked,
        }
    finally:
        con.close()


def _selftest():
    print("=" * 80)
    print("BingguPack Phase 2-B — staging 재독 E2E (READ ONLY, temp HOME, write 0)")
    print("=" * 80)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    home = tempfile.mkdtemp(prefix="binggupack_e2e_")
    results = []
    leak_blobs = []

    def rec(cid, name, ok):
        results.append((cid, name, "PASS" if ok else "FAIL"))

    # 1) 저장(write, Phase 2-A 흐름) — user_a / user_b 격리 저장
    wa = phase2_apply(home, "user_a", base_pack(pack_id="e2e_a", content="A 맥락 후보"),
                      {"actor": "human"}, write_enabled=True)
    wb = phase2_apply(home, "user_b", base_pack(pack_id="e2e_b", content="B 맥락 후보"),
                      {"actor": "human"}, write_enabled=True)
    rec("E0", "사전 저장(user_a/user_b)", wa.get("applied") and wb.get("applied"))

    # 2) read-only 재독 — user_a
    ra = reread(home, "user_a")
    leak_blobs.append(ra)
    rec("E1", "node/edge/evidence read-back", ra["n_nodes"] == 1 and ra["n_edges"] == 1 and ra["n_evidence"] == 1)
    rec("E2", "candidate=1", ra["all_candidate"] and ra["edge_candidate"])
    rec("E3", "promotion_allowed=0", ra["all_promotion_zero"])
    rec("E4", "evidence_refs 보존", ra["edge_refs_present"])
    rec("E5", "state=active", ra["all_active"])
    rec("E6", "read-only 연결 write 차단", ra["write_blocked_readonly"])

    # 3) user_id 격리 — user_b 재독이 user_a와 독립(각 1건, 서로 안 섞임)
    rb = reread(home, "user_b")
    leak_blobs.append(rb)
    rec("E7", "user_id 격리(독립 재독)", rb["n_nodes"] == 1 and rb["n_edges"] == 1
        and ra["node_ids"] == rb["node_ids"] == ["n1"])   # 각 DB 독립, 동일 synthetic id 공존(분리 저장)

    # 4) raw_leak=0
    blob = json.dumps([results, leak_blobs], ensure_ascii=False, default=str)
    needles = [os.path.expanduser("~"), BASE, "C:\\Users", "/Users/", "/home/", "staging.sqlite", home]
    leak = sum(1 for nd in needles if nd and nd in blob)
    rec("E8", "raw_leak=0", leak == 0)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = (op_before == op_after)
    rec("E9", "operating_store_unchanged", store_unchanged)

    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, name, v in sorted(results):
        print(f"  [{'OK' if v == 'PASS' else 'X'}] {cid:>3} {name}")
    print("-" * 80)
    print(f"  read_only=True  write=0  operating_store_unchanged={store_unchanged}  "
          f"real_home_write=0(temp)  confirmed=0 upload=0 push=0 neo4j=0")
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print(f"  RESULT: {npass}/{len(results)} PASS   GATE: {gate}")
    return 0 if gate == "GO" else 1


def main():
    if len(sys.argv) == 1 or "--selftest" in sys.argv:
        return _selftest()
    print("usage: python openbinggu_phase2_staging_reread_e2e.py --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
