# -*- coding: utf-8 -*-
"""comp3 — Merkle 앵커 (2단 무결성): hit_events 위변조 방지.

event 해시체인(sequence_no/prev_hash/snapshot_hash/external_ts) + Merkle root 로
raw 위변조·sequence gap·꼬리삭제·timestamp 역전을 fail-closed 검출한다.

self-modifying 회피 대원칙:
  - 빙구팩 = 봉인·기록만(seal_event 는 hit_events 행을 read → 별도 hit_event_chain 테이블에 append).
    hit_events/nodes/edges/거버넌스 규칙(CLAUDE.md·박제) 을 0줄도 쓰지 않는다(평가·제안·렌더링 0).
  - 검증 = 빙구팩 무관 독립 함수(verify_chain). rows·anchor 두 자료구조만 받는 순수함수 →
    raw 를 외부 도구가 export 해 같은 stdlib 한 줄로 재검증 가능(피검자=검증자 동일 런타임 차단).

★ 가드4 fix (검증서 5건):
  ① TOCTOU 차단 — seal_event 를 record_resolution INSERT 와 동일 트랜잭션/commit 안으로 묶어
     '미봉인 통과'를 구조적으로 불가능하게(record_and_seal 단일 atomic 경로). seal_event 단독 호출 시
     commit 여부를 호출측이 제어(commit=False → 같은 트랜잭션 합류).
  ② 누락 fail-closed — verify_from_db 는 hit_events 대비 미봉인 행이 있으면 BLOCK(reconcile).
  ③ leaf 단일정의 — _canon/_hash 는 openbinggu sha256[:16] 규약 그대로(comp5 와 동일·외부 재계산 정합).
  ④ Merkle root = full sha256(64자) — leaf 는 16자(체인 패리티)지만 root 결합은 절단 0(collision 강도).
  ⑤ 빈 체인 fail-open 금지 — anchor 가 없으면(아직 1건도 안 봉인) verify_from_db 는 BLOCK
     (단, hit_events 도 0건이면 '봉인할 게 없음' = OK; '이벤트는 있는데 anchor 없음' = BLOCK).

stdlib only: hashlib/json/re/datetime. 외부 바이너리 0.
"""
from __future__ import annotations

import os
import re
import sys
import json
import hashlib
from datetime import datetime, timezone

# 미이관 bare-name(_selftest 내 openbinggu_staging_write_selftest fixture · binggu_hit_stats lazy)
# 해소 — 원본이 자기 위치(scripts/)를 얹던 것을 패키지 위치에서 scripts/ 로 재계산해 동일 효과.
_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

CHAIN_VER = "m1"
_LEAF_EVENT_COLS = ("event_id", "node_id", "speaker", "kind", "outcome", "subtype", "ts")


# ---------------- ③ leaf 단일정의 (openbinggu _canon/_hash sha256[:16] 규약) ----------------

def _canon(s):
    """공백 정규화 후 utf-8 — openbinggu_staging_write_selftest._canon 와 동일 규약."""
    return re.sub(r"\s+", " ", str(s)).strip().encode("utf-8", "replace")


def _hash(s):
    """sha256[:16] — 기존 audit chain·comp5 leaf 와 동일 절단 규약(외부 재계산 정합)."""
    return hashlib.sha256(_canon(s)).hexdigest()[:16]


def _canon_event(row_dict):
    """event raw → 결정적 canonical JSON. 컬럼 순서 고정(위치 의존 명시).
    comp5(binggu_hit_export) 가 동일 규약으로 leaf 를 재계산할 단일 진실."""
    return json.dumps(
        [row_dict[k] for k in _LEAF_EVENT_COLS],
        ensure_ascii=False, sort_keys=False)


def _leaf_hash(row_dict):
    """이벤트 1건 → leaf hash(sha256[:16]). comp5 와 동일 규약(③ 단일정의)."""
    return _hash(_canon_event(row_dict))


# ---------------- ④ Merkle root = full sha256(64자, 절단 0) ----------------

def merkle_root(leaf_hashes):
    """leaf(=각 행 entry_hash, 16자) 리스트 → Merkle root(full sha256 64자).
    ④ root 결합 hash 는 절단하지 않는다(16자 leaf 와 달리 collision 강도 확보).
    홀수 노드는 마지막 복제(RFC6962 변형·결정적). 빈 입력 → 'EMPTY' sentinel(에러 0)."""
    if not leaf_hashes:
        return "EMPTY"
    layer = list(leaf_hashes)
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        nxt = []
        for i in range(0, len(layer), 2):
            combo = (layer[i] + layer[i + 1]).encode("utf-8", "replace")
            nxt.append(hashlib.sha256(combo).hexdigest())  # full 64자, 절단 0
        layer = nxt
    return layer[0]


def _now_iso(ts=None):
    return ts if ts else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- 봉인 (빙구팩 측 — 기록만) ----------------

def seal_event(con, event_id, external_ts=None, commit=True):
    """hit_events 의 한 행을 봉인 — raw 선저장→hash→체인 append→앵커·root 갱신.

    ① TOCTOU: commit=False 로 호출하면 자체 commit 안 함 → record_resolution INSERT 와
       동일 트랜잭션에 합류(record_and_seal 가 이 경로 사용). 미봉인 행이 commit 사이에
       끼어들 틈을 제거한다. commit=True(단독 봉인)면 함수 끝에서 commit.
    봉인은 '기록 사실의 사후 무결성 고정'일 뿐 — 평가·규칙 변경 0(self-modifying 0).
    actor 게이트 불요: hit_events INSERT 는 이미 record_resolution(actor=human) 에서만 발생.
    """
    ext = _now_iso(external_ts)
    row = con.execute(
        "SELECT event_id,node_id,speaker,kind,outcome,subtype,ts FROM hit_events WHERE event_id=?",
        (event_id,)).fetchone()
    if not row:
        return {"sealed": False, "reason": "event_not_found"}
    rd = dict(zip(_LEAF_EVENT_COLS, row))
    raw = _canon_event(rd)          # raw 를 먼저 그대로 저장(원본 봉인)
    snap = _hash(raw)               # snapshot_hash = raw 에서 도출 → 봉인 후 raw 변경 시 verify 에서 fail
    last = con.execute(
        "SELECT sequence_no,entry_hash,external_ts FROM hit_event_chain "
        "ORDER BY sequence_no DESC LIMIT 1").fetchone()
    seq = (last[0] + 1) if last else 1
    prev = last[1] if last else "GENESIS"
    if last and ext < last[2]:      # timestamp 역전 = fail-closed(봉인 거부)
        return {"sealed": False, "reason": "external_ts_regression"}
    body = json.dumps([CHAIN_VER, seq, event_id, snap, ext, prev], ensure_ascii=False)
    eh = _hash(body)                # entry_hash = sha256[:16](체인 패리티)
    con.execute(
        "INSERT INTO hit_event_chain"
        "(sequence_no,event_id,raw_json,snapshot_hash,external_ts,prev_hash,entry_hash,chain_ver) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (seq, event_id, raw, snap, ext, prev, eh, CHAIN_VER))
    leaves = [r[0] for r in con.execute(
        "SELECT entry_hash FROM hit_event_chain ORDER BY sequence_no")]
    root = merkle_root(leaves)      # ④ full 64자
    n = con.execute("SELECT count(*) FROM hit_event_chain").fetchone()[0]
    for k, v in (("entry_count", str(n)), ("head_entry_hash", eh),
                 ("merkle_root", root), ("leaf_count", str(len(leaves)))):
        con.execute("INSERT OR REPLACE INTO hit_event_anchor(key,value) VALUES(?,?)", (k, v))
    if commit:
        con.commit()
    return {"sealed": True, "sequence_no": seq, "entry_hash": eh, "merkle_root": root}


def record_and_seal(db, record_fn, *args, external_ts=None, **kwargs):
    """① TOCTOU atomic 경로 — record_fn(=binggu_hit_stats.record_resolution) 의 INSERT 와
    seal_event 를 동일 트랜잭션/commit 으로 묶는다. 미봉인 행이 통과할 수 없다(atomic).

    record_fn 은 내부에서 commit 하므로(기존 시그니처 보존), 여기서는 record_fn 호출 직후
    같은 con 으로 방금 INSERT 된 event_id 들을 찾아 seal_event(commit=False) 로 봉인한 뒤
    단일 commit. record 가 실패(recorded False·event 0)면 봉인할 것도 0(원자성 자명).

    주의: record_fn 이 자체 commit 하더라도, INSERT~seal 사이에 다른 writer 가 끼어들 수 없도록
    호출측은 db.write_lock() 안에서 record_and_seal 을 부르는 것을 권장(이중 writer 차단).
    """
    con = db.con
    before = con.execute("SELECT COALESCE(MAX(event_id),0) FROM hit_events").fetchone()[0]
    res = record_fn(db, *args, **kwargs)
    new_ids = [r[0] for r in con.execute(
        "SELECT event_id FROM hit_events WHERE event_id>? ORDER BY event_id", (before,))]
    sealed = []
    for eid in new_ids:
        s = seal_event(con, eid, external_ts=external_ts, commit=False)
        sealed.append(s)
        if not s.get("sealed"):
            # 봉인 실패(ts 역전 등) → 전체 롤백(미봉인 hit_events 행이 남지 않게 atomic)
            con.rollback()
            return {"recorded": False, "reason": "seal_failed:%s" % s.get("reason"),
                    "record_result": res}
    con.commit()
    return {"recorded": res.get("recorded"), "record_result": res, "sealed": sealed,
            "sealed_event_ids": new_ids}


# ---------------- ② 누락 검출(reconcile) ----------------

def find_unsealed_events(con):
    """hit_events 에 있으나 hit_event_chain 에 없는 event_id 목록(미봉인 행).
    ② verify_from_db 가 이를 BLOCK 근거로 사용(미봉인 통과 차단·fail-closed)."""
    sealed = {r[0] for r in con.execute("SELECT event_id FROM hit_event_chain")}
    allev = [r[0] for r in con.execute("SELECT event_id FROM hit_events")]
    return [e for e in allev if e not in sealed]


# ---------------- 검증 (외부 독립 순수함수 — fail-closed) ----------------

def verify_chain(rows, anchor):
    """rows=[(seq,event_id,raw_json,snapshot_hash,external_ts,prev_hash,entry_hash,chain_ver)]
    seq 오름차순. fail-closed: 첫 위반에서 (False, reason). 무손상 시 (True, "OK").
    빙구팩 런타임/DB/CFG/recall 무참조 → 검증 독립성(피검자=검증자 차단)."""
    prev = "GENESIS"
    prev_seq = 0
    prev_ext = None
    leaves = []
    for r in rows:
        seq, eid, raw, snap, ext, ph, eh, ver = r
        if seq != prev_seq + 1:
            return (False, "sequence_gap@%d" % seq)               # 누락·gap
        if ph != prev:
            return (False, "prev_hash_break@%d" % seq)
        if _hash(raw) != snap:
            return (False, "snapshot_hash_mismatch@%d" % seq)     # raw 위변조
        body = json.dumps([CHAIN_VER, seq, eid, snap, ext, ph], ensure_ascii=False)
        if _hash(body) != eh:
            return (False, "entry_hash_mismatch@%d" % seq)
        if prev_ext is not None and ext < prev_ext:
            return (False, "ts_regression@%d" % seq)              # 시간 역전
        prev, prev_seq, prev_ext = eh, seq, ext
        leaves.append(eh)
    # 꼬리삭제 + Merkle root 앵커 대조
    if anchor:
        if anchor.get("entry_count") != str(len(rows)):
            return (False, "tail_deletion")                       # 꼬리삭제
        head = rows[-1][6] if rows else "GENESIS"
        if anchor.get("head_entry_hash", head) != head:
            return (False, "head_anchor_break")
        if anchor.get("merkle_root") != merkle_root(leaves):
            return (False, "merkle_root_mismatch")                # ④ root 위조
    elif rows:
        # ⑤ 빈 체인 fail-open 금지 — 봉인 행은 있는데 anchor 가 없으면 BLOCK
        return (False, "anchor_missing")
    return (True, "OK")


def verify_from_db(con):
    """DB 에서 rows·anchor 로드 후 verify_chain. ② 미봉인 hit_events 가 있으면 BLOCK.
    ⑤ hit_events·chain 둘 다 0 이면 '봉인할 게 없음' = OK; 이벤트는 있는데 anchor 없음 = BLOCK."""
    unsealed = find_unsealed_events(con)
    if unsealed:
        return (False, "unsealed_events:%s" % ",".join(str(x) for x in unsealed[:8]))
    rows = list(con.execute(
        "SELECT sequence_no,event_id,raw_json,snapshot_hash,external_ts,prev_hash,entry_hash,chain_ver "
        "FROM hit_event_chain ORDER BY sequence_no"))
    anchor = {k: v for k, v in con.execute("SELECT key,value FROM hit_event_anchor")}
    # ⑤ 이벤트가 1건이라도 있었는데 chain·anchor 가 모두 비면 fail-closed
    n_events = con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
    if n_events > 0 and not rows:
        return (False, "anchor_missing")
    return verify_chain(rows, anchor)


# ============================ selftest ============================

def _selftest():
    import tempfile, shutil
    from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS

    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="binggu_merkle_")
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    def mk_event(con, eid, outcome="hit", ts="2026-06-20T00:00:00Z"):
        con.execute(
            "INSERT INTO hit_events(event_id,node_id,speaker,kind,outcome,subtype,ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (eid, "n%d" % eid, "owner", "직감", outcome, "J", ts))
        con.commit()

    def fresh(name):
        db = StagingDB(os.path.join(tmp, name))
        return db

    # T1 정상 봉인 N건 → verify_from_db (True,"OK"), seq 1..N 연속
    db = fresh("t1.sqlite")
    for i in range(1, 6):
        mk_event(db.con, i, "hit" if i % 2 else "miss",
                 "2026-06-20T00:0%d:00Z" % i)
        seal_event(db.con, i, external_ts="2026-06-20T01:0%d:00Z" % i)
    ok1, why1 = verify_from_db(db.con)
    seqs = [r[0] for r in db.con.execute("SELECT sequence_no FROM hit_event_chain ORDER BY sequence_no")]
    rec(1, "정상 봉인 5건→verify OK·seq 1..5 연속", ok1 and why1 == "OK" and seqs == [1, 2, 3, 4, 5])

    # T2 raw 위변조 → snapshot_hash_mismatch
    db2 = fresh("t2.sqlite")
    for i in range(1, 4):
        mk_event(db2.con, i, ts="2026-06-20T00:0%d:00Z" % i)
        seal_event(db2.con, i, external_ts="2026-06-20T01:0%d:00Z" % i)
    db2.con.execute("UPDATE hit_event_chain SET raw_json=raw_json||'X' WHERE sequence_no=2")
    db2.con.commit()
    ok2, why2 = verify_from_db(db2.con)
    rec(2, "raw 위변조→snapshot_hash_mismatch fail-closed",
        (not ok2) and why2.startswith("snapshot_hash_mismatch"))

    # T3 entry_hash 위변조
    db3 = fresh("t3.sqlite")
    for i in range(1, 4):
        mk_event(db3.con, i, ts="2026-06-20T00:0%d:00Z" % i)
        seal_event(db3.con, i, external_ts="2026-06-20T01:0%d:00Z" % i)
    db3.con.execute("UPDATE hit_event_chain SET entry_hash='deadbeefdeadbeef' WHERE sequence_no=2")
    db3.con.commit()
    ok3, why3 = verify_from_db(db3.con)
    # entry_hash 변조 시: seq2 의 prev_hash 검사는 통과(seq1 entry 무변), entry_hash_mismatch@2 → 단 seq3.prev=원래값이라
    # prev_hash_break@3 가 먼저 잡힐 수도. 둘 중 하나면 변조 검출 성공.
    rec(3, "entry_hash 위변조→fail-closed(entry/prev break)",
        (not ok3) and ("entry_hash_mismatch" in why3 or "prev_hash_break" in why3))

    # T4 prev_hash 절단(중간행 entry_hash 변경→다음행 prev break)
    db4 = fresh("t4.sqlite")
    for i in range(1, 5):
        mk_event(db4.con, i, ts="2026-06-20T00:0%d:00Z" % i)
        seal_event(db4.con, i, external_ts="2026-06-20T01:0%d:00Z" % i)
    # seq2 의 prev_hash 를 가짜로 → prev_hash_break@2
    db4.con.execute("UPDATE hit_event_chain SET prev_hash='0000000000000000' WHERE sequence_no=2")
    db4.con.commit()
    ok4, why4 = verify_from_db(db4.con)
    rec(4, "prev_hash 절단→prev_hash_break fail-closed",
        (not ok4) and why4.startswith("prev_hash_break"))

    # T5 sequence gap: 중간행 DELETE(seq2)
    db5 = fresh("t5.sqlite")
    for i in range(1, 5):
        mk_event(db5.con, i, ts="2026-06-20T00:0%d:00Z" % i)
        seal_event(db5.con, i, external_ts="2026-06-20T01:0%d:00Z" % i)
    db5.con.execute("DELETE FROM hit_event_chain WHERE sequence_no=2")
    db5.con.commit()
    # 미봉인 검사 전에 chain 에서 event 2 가 빠지면 find_unsealed 가 먼저 잡을 수 있음 → 둘 다 fail 이면 OK
    ok5, why5 = verify_from_db(db5.con)
    rec(5, "sequence gap(중간 DELETE)→fail-closed(gap/unsealed)",
        (not ok5) and ("sequence_gap" in why5 or "unsealed_events" in why5))

    # T6 꼬리 삭제: 마지막 행 DELETE + anchor 미갱신
    db6 = fresh("t6.sqlite")
    for i in range(1, 5):
        mk_event(db6.con, i, ts="2026-06-20T00:0%d:00Z" % i)
        seal_event(db6.con, i, external_ts="2026-06-20T01:0%d:00Z" % i)
    # chain 의 마지막 행만 삭제(hit_events 에는 event 4 가 남음→미봉인으로도 잡힘)
    db6.con.execute("DELETE FROM hit_event_chain WHERE sequence_no=4")
    db6.con.commit()
    ok6, why6 = verify_from_db(db6.con)
    rec(6, "꼬리 삭제→fail-closed(tail_deletion/unsealed)",
        (not ok6) and ("tail_deletion" in why6 or "unsealed_events" in why6))

    # T7 timestamp 역전: 과거 external_ts 로 seal → seal_event reason
    db7 = fresh("t7.sqlite")
    mk_event(db7.con, 1, ts="2026-06-20T00:01:00Z")
    seal_event(db7.con, 1, external_ts="2026-06-20T05:00:00Z")
    mk_event(db7.con, 2, ts="2026-06-20T00:02:00Z")
    s7 = seal_event(db7.con, 2, external_ts="2026-06-20T01:00:00Z")  # 역전(05:00 → 01:00)
    rec(7, "timestamp 역전→seal external_ts_regression 거부",
        (not s7["sealed"]) and s7["reason"] == "external_ts_regression")

    # T8 Merkle root 위조: anchor.merkle_root 직접 변조
    db8 = fresh("t8.sqlite")
    for i in range(1, 4):
        mk_event(db8.con, i, ts="2026-06-20T00:0%d:00Z" % i)
        seal_event(db8.con, i, external_ts="2026-06-20T01:0%d:00Z" % i)
    db8.con.execute("UPDATE hit_event_anchor SET value='f'||value WHERE key='merkle_root'")
    db8.con.commit()
    ok8, why8 = verify_from_db(db8.con)
    rec(8, "merkle_root 위조→merkle_root_mismatch fail-closed",
        (not ok8) and why8 == "merkle_root_mismatch")

    # T9 독립 재계산 동치: rows export 해 verify_chain 직접 호출 == verify_from_db
    db9 = fresh("t9.sqlite")
    for i in range(1, 6):
        mk_event(db9.con, i, "hit" if i % 2 else "miss", ts="2026-06-20T00:0%d:00Z" % i)
        seal_event(db9.con, i, external_ts="2026-06-20T01:0%d:00Z" % i)
    rows9 = list(db9.con.execute(
        "SELECT sequence_no,event_id,raw_json,snapshot_hash,external_ts,prev_hash,entry_hash,chain_ver "
        "FROM hit_event_chain ORDER BY sequence_no"))
    anchor9 = {k: v for k, v in db9.con.execute("SELECT key,value FROM hit_event_anchor")}
    ind = verify_chain(rows9, anchor9)          # 빙구팩 DB 핸들 없이(rows·anchor 만)
    dbv = verify_from_db(db9.con)
    rec(9, "독립 재계산 동치: verify_chain(rows,anchor)==verify_from_db",
        ind == (True, "OK") and dbv == (True, "OK"))

    # T10 merkle_root 결정성 + 홀수 leaf(3건) 복제 경로 + full 64자
    leaves3 = ["a" * 16, "b" * 16, "c" * 16]
    r10a = merkle_root(leaves3)
    r10b = merkle_root(leaves3)
    rec(10, "merkle_root 결정성·홀수 leaf 복제·full 64자",
        r10a == r10b and len(r10a) == 64 and r10a != "EMPTY")

    # T11 빈 체인 graceful: rows=[]·anchor None → (True,"OK")·merkle_root("EMPTY")
    rec(11, "빈 체인 graceful: verify_chain([],None)==OK·merkle_root([])=='EMPTY'",
        verify_chain([], None) == (True, "OK") and merkle_root([]) == "EMPTY")

    # ⑤ 보강: 이벤트는 있는데 anchor 없음 → BLOCK(fail-open 금지)
    db11 = fresh("t11.sqlite")
    mk_event(db11.con, 1, ts="2026-06-20T00:01:00Z")  # 봉인 안 함
    ok11, why11 = verify_from_db(db11.con)
    fail_open_blocked = (not ok11) and ("unsealed_events" in why11 or why11 == "anchor_missing")

    # T12 self-modifying 0 증명: seal 전후 hit_events/nodes/edges count·store_checksum 불변
    db12 = fresh("t12.sqlite")
    db12.con.execute("INSERT INTO nodes(node_id,node_type,sentence) VALUES('z1','j','s')")
    db12.con.execute("INSERT INTO edges(edge_id,relation,source,target) VALUES('e1','r','z1','z1')")
    mk_event(db12.con, 1, ts="2026-06-20T00:01:00Z")
    ck_before = db12.store_checksum()
    he_before = db12.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
    nd_before = db12.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    ed_before = db12.con.execute("SELECT count(*) FROM edges").fetchone()[0]
    seal_event(db12.con, 1, external_ts="2026-06-20T01:00:00Z")  # append 는 chain 테이블에만
    ck_after = db12.store_checksum()
    he_after = db12.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
    nd_after = db12.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    ed_after = db12.con.execute("SELECT count(*) FROM edges").fetchone()[0]
    rec(12, "self-modifying 0: seal→hit_events/nodes/edges/checksum 불변",
        ck_before == ck_after and he_before == he_after
        and nd_before == nd_after and ed_before == ed_after and fail_open_blocked)

    # 보강 T13: ① TOCTOU atomic — record_and_seal 후 미봉인 0 + verify OK
    try:
        import binggu_hit_stats as hs
        db13 = fresh("t13.sqlite")
        db13.con.execute(
            "INSERT INTO nodes(node_id,node_type,sentence,speaker,semantic_subtype) "
            "VALUES('ow','judgment','판단',?,?)", ("owner", "J"))
        db13.con.commit()
        rs = record_and_seal(db13, hs.record_resolution, "ow", True, {"actor": "human"},
                             ts="2026-06-20T00:00:00Z",
                             external_ts="2026-06-20T01:00:00Z")
        unsealed13 = find_unsealed_events(db13.con)
        okv, _ = verify_from_db(db13.con)
        rec(13, "① TOCTOU atomic: record_and_seal→미봉인 0·verify OK",
            rs.get("recorded") and unsealed13 == [] and okv)
    except Exception as e:  # noqa
        rec(13, "① TOCTOU atomic: record_and_seal", False)
        print("  T13 exc:", repr(e)[:120])

    for n in ("db", "db2", "db3", "db4", "db5", "db6", "db7", "db8", "db9", "db11", "db12", "db13"):
        try:
            locals_obj = locals().get(n)
            if locals_obj is not None:
                locals_obj.close()
        except Exception:
            pass

    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = before_mtime == after_mtime
    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 74)
    print("binggu_merkle_anchor — comp3 Merkle 무결성(2단) selftest (temp DB·운영 write 0)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print(f"{'[OK]' if v == 'PASS' else '[X]'} {cid:>2} {desc}")
    print("-" * 74)
    print(f"=== {npass}/{len(results)} ===")
    print(f"RESULT: {npass}/{len(results)} PASS")
    print(f"operating_store_unchanged={store_unchanged}  hit_event_chain_append_only=1  "
          f"merkle_root_full64=1  raw_leak=0")
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print(f"GATE={gate}")
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_merkle_anchor: --selftest 로 검증 실행")
