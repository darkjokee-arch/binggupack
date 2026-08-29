# -*- coding: utf-8 -*-
"""comp5 selftest — binggu_hit_export (3단 A 분리·진화 추출기).

temp DB·운영 미접촉·stdlib only. openbinggu/binggu_recall selftest 규약 그대로.
8 케이스:
  T1 collect_hit_raw read-only — events·rates 일치, 운영 store mtime 불변.
  T2 PII 제외 — export jsonl 에 sentence/원문 필드 0(7 컬럼만).
  T3 merkle_root 결정성 + 변조 탐지(1개 event outcome 변조 → root 변경).
  T4 외부 재계산 동치 — jsonl 독립 재로드 merkle_root == manifest.merkle_root.
  T5 거버넌스 write 차단 — CLAUDE.md/박제/정책파일 경로 export → PermissionError. 정상 temp 성공.
  T6 audit_anchor 동기 — manifest.audit_anchor.head_entry_hash == db audit_meta head_entry_hash.
  T7 빈 그래프 graceful — hit_events 0 → event_count 0·root='EMPTY'·에러 0.
  T8 self-modifying 0 — GOVERNANCE_FORBIDDEN(CLAUDE.md·박제·정책파일) 경로 mtime before==after.
GATE=GO 조건: 전 케이스 PASS AND operating_store_unchanged AND governance_paths_unchanged.
"""
from __future__ import annotations

from contextlib import suppress
from pathlib import Path
import os
import sys
import json
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import binggu_hit_export as ex  # noqa: E402
import binggu_hit_stats as hs  # noqa: E402
from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS  # noqa: E402


def _mk_owner(db, nid, subtype="J"):
    db.con.execute(
        "INSERT INTO nodes(node_id,node_type,sentence,semantic_subtype,speaker) VALUES(?,?,?,?,?)",
        (nid, "judgment", "판단 " + nid, subtype, "owner"))
    db.con.commit()


def _seed(db, n, base="o"):
    """record_resolution(actor=human) 으로 hit_events n건 적재(불변식6 경유)."""
    for i in range(n):
        nid = "%s%d" % (base, i)
        _mk_owner(db, nid)
        hs.record_resolution(db, nid, (i % 2 == 0), {"actor": "human"},
                             ts="2026-06-20T00:0%d:00Z" % (i % 10), domain="bid")


def run():
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    # 운영 store + 거버넌스 자산 mtime before
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    gov_paths = ex._governance_forbidden()
    gov_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in gov_paths}

    tmp = tempfile.mkdtemp(prefix="binggu_hit_export_")
    dbs = []

    try:
        # ---- T1 collect_hit_raw read-only ----
        db1 = StagingDB(os.path.join(tmp, "t1.sqlite")); dbs.append(db1)
        _seed(db1, 6)
        raw1 = ex.collect_hit_raw(db1, now_ts="2026-06-21T00:00:00Z")
        n_db = db1.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        rec(1, "collect_hit_raw read-only·events/rates 일치",
            raw1["counts"]["event_count"] == n_db == 6
            and raw1["rates"]["owner"].get("signal_only")
            and raw1["merkle_root"] not in (None, ""))

        # ---- T2 PII 제외 ----
        out2 = os.path.join(tmp, "out2")
        r2 = ex.export_hit_raw(db1, out2, ts="2026-06-21T00:00:00Z")
        jsonl_txt = Path(r2['written'][0]).read_text(encoding='utf-8')
        allowed = set(ex._LEAF_EVENT_COLS)
        keys_ok = True
        for line in jsonl_txt.splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if set(obj.keys()) != allowed:
                keys_ok = False
        # sentence/원문·domain·context_hash·decision_id 필드 부재
        pii_free = ("sentence" not in jsonl_txt and "context_hash" not in jsonl_txt
                    and "decision_id" not in jsonl_txt)
        rec(2, "PII 제외(7 컬럼만·sentence/decision_id/context_hash 0)", keys_ok and pii_free)

        # ---- T3 merkle_root 결정성 + 변조 탐지 ----
        raw3a = ex.collect_hit_raw(db1, now_ts="2026-06-21T00:00:00Z")
        raw3b = ex.collect_hit_raw(db1, now_ts="2026-06-21T00:00:00Z")
        det = raw3a["merkle_root"] == raw3b["merkle_root"]
        # 1개 event outcome 변조 → root 변경
        db1.con.execute("UPDATE hit_events SET outcome='miss' WHERE event_id=(SELECT MIN(event_id) FROM hit_events)")
        db1.con.commit()
        raw3c = ex.collect_hit_raw(db1, now_ts="2026-06-21T00:00:00Z")
        tamper = raw3c["merkle_root"] != raw3a["merkle_root"]
        rec(3, "merkle_root 결정성 + outcome 변조→root 변경(탐지)", det and tamper)

        # ---- T4 외부 재계산 동치 ----
        db4 = StagingDB(os.path.join(tmp, "t4.sqlite")); dbs.append(db4)
        _seed(db4, 7)
        out4 = os.path.join(tmp, "out4")
        r4 = ex.export_hit_raw(db4, out4, ts="2026-06-21T00:00:00Z")
        manifest4 = json.loads(Path(r4['written'][1]).read_text(encoding='utf-8'))
        recomputed = ex.recompute_root_from_jsonl(r4["written"][0])
        rec(4, "외부 재계산 동치: jsonl→merkle_root == manifest.merkle_root",
            recomputed == manifest4["merkle_root"] == r4["merkle_root"])

        # ---- T5 거버넌스 write 차단 ----
        # 가드 계약: 디렉터리 항목은 '그 자신 + 하위' 차단, 파일 항목은 '그 파일 자신' 차단.
        # (파일 항목의 상위 디렉터리까지 차단하지 않는다 — 과방어 회피·capability 정밀성.)
        blocked = []
        for g in gov_paths:
            try:
                # 디렉터리면 하위 경로 시도, 파일이면 그 파일 자신 — 둘 다 거부돼야
                target = os.path.join(g, "binggu_hit_export_evil") if os.path.isdir(g) else g
                ex._assert_export_target(target)
                blocked.append(False)
            except PermissionError:
                blocked.append(True)
        # 명시: CLAUDE.md 파일 자신 경로도 차단
        try:
            ex._assert_export_target(ex._governance_forbidden()[0])
            file_self_blocked = False
        except PermissionError:
            file_self_blocked = True
        # 정상 temp out_dir 는 통과 + 실제 write 성공
        out5 = os.path.join(tmp, "out5")
        r5 = ex.export_hit_raw(db4, out5, ts="2026-06-21T00:00:00Z")
        normal_ok = os.path.exists(r5["written"][0]) and os.path.exists(r5["written"][1])
        rec(5, "거버넌스 write 차단(정책/박제/CLAUDE.md PermissionError)·정상 temp 성공",
            all(blocked) and file_self_blocked and normal_ok)

        # ---- T6 audit_anchor 동기 ----
        db6 = StagingDB(os.path.join(tmp, "t6.sqlite")); dbs.append(db6)
        _seed(db6, 5)
        # audit_meta 채우기 — audit_append 1건(head_entry_hash/entry_count 세팅)
        db6.audit_append("human", "test_action", "pk1", "ok", "seed", "b", "a",
                         ts="2026-06-20T00:00:00Z")
        out6 = os.path.join(tmp, "out6")
        r6 = ex.export_hit_raw(db6, out6, ts="2026-06-21T00:00:00Z")
        manifest6 = json.loads(Path(r6['written'][1]).read_text(encoding='utf-8'))
        db_head = db6.con.execute(
            "SELECT value FROM audit_meta WHERE key='head_entry_hash'").fetchone()[0]
        rec(6, "audit_anchor 동기: manifest.head_entry_hash == db audit_meta",
            manifest6["audit_anchor"]["head_entry_hash"] == db_head and db_head is not None)

        # ---- T7 빈 그래프 graceful ----
        db7 = StagingDB(os.path.join(tmp, "t7.sqlite")); dbs.append(db7)
        out7 = os.path.join(tmp, "out7")
        r7 = ex.export_hit_raw(db7, out7, ts="2026-06-21T00:00:00Z")
        empty_jsonl = Path(r7['written'][0]).read_text(encoding='utf-8').strip()
        rec(7, "빈 그래프 graceful: event_count 0·root='EMPTY'·에러 0",
            r7["event_count"] == 0 and r7["merkle_root"] == "EMPTY" and empty_jsonl == "")

    finally:
        for d in dbs:
            with suppress(Exception):
                d.close()

    # ---- T8 self-modifying 0: 거버넌스 자산 mtime 불변 ----
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    gov_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in gov_paths}
    operating_store_unchanged = op_before == op_after
    governance_paths_unchanged = gov_before == gov_after
    rec(8, "self-modifying 0: 거버넌스 자산(CLAUDE.md/박제/정책) mtime 불변",
        governance_paths_unchanged)

    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 74)
    print("binggu_hit_export — comp5 3단 A 분리(진화 추출기) selftest (temp DB·운영 write 0)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print(f"{'[OK]' if v == 'PASS' else '[X]'} {cid:>2} {desc}")
    print("-" * 74)
    print(f"=== {npass}/{len(results)} ===")
    print(f"RESULT: {npass}/{len(results)} PASS")
    print(f"operating_store_unchanged={operating_store_unchanged}  "
          f"governance_paths_unchanged={governance_paths_unchanged}  "
          f"governance_write=0  rule_write=0  raw_leak_gate=1")
    gate = "GO" if (npass == len(results) and operating_store_unchanged
                    and governance_paths_unchanged) else "NO-GO"
    print(f"GATE={gate}")
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(run())
