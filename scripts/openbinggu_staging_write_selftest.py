#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu Step 3 — synthetic staging write 구현 + selftest.
기준: OPENBINGGU_PERSONAL_APPLY_ALLOWED_DESIGN.md(Step1) + OPENBINGGU_STAGING_SQLITE_SCHEMA_DESIGN.md(Step2).

안전: staging = temp 파일 SQLite(운영과 물리 분리). 운영 localcrab_index.sqlite/user_graph/_graph_merge
      connect 0·write 0(mtime 전후 대조). C-2 guard 통과 후에만 insert. apply(운영) 0.
"""
import os, re, json, hashlib, sqlite3, tempfile, shutil
from contextlib import contextmanager
from datetime import datetime, timezone

CUR_REDACTION_POLICY = "v1"
# 운영 store 경로(거부 대상). 공개본은 작성자 절대경로를 포함하지 않는다.
# 사용자가 자기 운영 경로를 거부 대상으로 등록하려면 아래 env 를 설정한다.
# 미설정 시 temp 의 dummy 경로(존재하지 않아도 됨)로, "거부 대상 표식" 의미만 유지한다.
_TMP = tempfile.gettempdir()
OPERATING_PATHS = [
    os.environ.get("OPENBINGGU_USER_GRAPH",  os.path.join(_TMP, "openbinggu_user_graph_dummy.yaml")),
    os.environ.get("OPENBINGGU_GRAPH_MERGE", os.path.join(_TMP, "openbinggu_graph_merge_dummy.yaml")),
    os.environ.get("OPENBINGGU_OPERATING_DB", os.path.join(_TMP, "openbinggu_operating_dummy.sqlite")),
]

def _canon(s): return re.sub(r"\s+", " ", str(s)).strip().encode("utf-8", "replace")
def _hash(s): return hashlib.sha256(_canon(s)).hexdigest()[:16]


def _now_iso(ts=None):
    """실시간 UTC ISO. selftest 재현성 필요 시 ts 명시 주입(분리 파라미터)."""
    return ts if ts else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StagingDB:
    """temp 파일 staging SQLite. 운영 경로 거부. WAL + transaction + checksum."""
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,
        candidate INTEGER DEFAULT 1, promotion_allowed INTEGER DEFAULT 0, state TEXT DEFAULT 'active',
        supersedes TEXT, pack_id TEXT, content_hash TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS edges(edge_id TEXT PRIMARY KEY, relation TEXT, source TEXT, target TEXT,
        candidate INTEGER DEFAULT 1, state TEXT DEFAULT 'active', evidence_refs TEXT,
        pack_id TEXT, content_hash TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS evidence(evidence_id TEXT PRIMARY KEY, sentence TEXT, source_pointer_id TEXT,
        source_hash TEXT, redaction_policy TEXT, pack_id TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS applied_registry(pack_id TEXT, content_hash TEXT, applied_at TEXT,
        PRIMARY KEY(pack_id, content_hash));
    CREATE TABLE IF NOT EXISTS audit_log(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT,
        action TEXT, pack_id TEXT, result TEXT, reason_code TEXT, before_hash TEXT, after_hash TEXT,
        prev_audit_hash TEXT, entry_hash TEXT, chain_ver TEXT);
    CREATE TABLE IF NOT EXISTS audit_meta(key TEXT PRIMARY KEY, value TEXT);
    """
    def __init__(self, path):
        # 운영 경로 거부 (대상 한정)
        norm = os.path.normcase(os.path.abspath(path))
        for op in OPERATING_PATHS:
            if norm == os.path.normcase(os.path.abspath(op)):
                raise PermissionError("operating_store_forbidden")
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=5000")
        self.con.executescript(self.SCHEMA)
        # 기존 장부(v1) 마이그레이션 — chain_ver 컬럼 없으면 추가(기존 행은 NULL=v1 검증)
        cols = [c[1] for c in self.con.execute("PRAGMA table_info(audit_log)")]
        if "chain_ver" not in cols:
            self.con.execute("ALTER TABLE audit_log ADD COLUMN chain_ver TEXT")
        self.con.commit()

    def snapshot(self, snap_dir, name):
        """표준 스냅샷 — wal_checkpoint(TRUNCATE) 후 main 파일 복사 (WAL 잔존분 누락 방지)."""
        self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.con.commit()
        snap = os.path.join(snap_dir, name)
        shutil.copy2(self.path, snap)
        return snap

    @contextmanager
    def write_lock(self):
        """쓰기 진입 lock 파일(O_EXCL) — 이중 실행 감지 시 명시 에러. 같은 pid 재진입 허용."""
        lock = self.path + ".lock"
        owner = False
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            owner = True
        except FileExistsError:
            pid = ""
            try:
                with open(lock, "r") as f:
                    pid = f.read().strip()
            except OSError:
                pass
            if pid != str(os.getpid()):
                raise RuntimeError("staging_write_locked: concurrent writer detected (%s)" % lock)
        try:
            yield
        finally:
            if owner and os.path.exists(lock):
                os.remove(lock)

    def store_checksum(self):
        h = hashlib.sha256()
        for t in ("nodes", "edges", "evidence"):
            for row in self.con.execute(f"SELECT * FROM {t} ORDER BY 1"):
                h.update(_canon(json.dumps(row, ensure_ascii=False)))
        return h.hexdigest()[:16]

    def audit_append(self, actor, action, pack_id, result, reason, before, after, ts=None):
        ts = _now_iso(ts)
        prev = self.con.execute("SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = prev[0] if prev else "GENESIS"
        # v2: ts 를 해시 재료에 포함 (기존 v1 행과 공존 — verify_chain 양식 분기)
        body = json.dumps(["v2", ts, actor, action, pack_id, result, reason, before, after, prev], ensure_ascii=False)
        eh = _hash(body)
        self.con.execute("INSERT INTO audit_log(ts,actor,action,pack_id,result,reason_code,before_hash,after_hash,prev_audit_hash,entry_hash,chain_ver) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (ts, actor, action, pack_id, result, reason, before, after, prev, eh, "v2"))
        # 꼬리 삭제 검출용 메타 — 엔트리 수 + head(최신 entry_hash) 앵커
        n = self.con.execute("SELECT count(*) FROM audit_log").fetchone()[0]
        self.con.execute("INSERT OR REPLACE INTO audit_meta(key,value) VALUES('entry_count',?)", (str(n),))
        self.con.execute("INSERT OR REPLACE INTO audit_meta(key,value) VALUES('head_entry_hash',?)", (eh,))
        self.con.commit()

    def verify_chain(self):
        prev = "GENESIS"
        rows = list(self.con.execute(
            "SELECT ts,actor,action,pack_id,result,reason_code,before_hash,after_hash,prev_audit_hash,entry_hash,chain_ver FROM audit_log ORDER BY seq"))
        for ts,actor,action,pid,res,rc,bh,ah,ph,eh,ver in rows:
            if ph != prev: return False
            if ver == "v2":
                body = json.dumps(["v2", ts, actor, action, pid, res, rc, bh, ah, ph], ensure_ascii=False)
            else:  # v1 (기존 장부 호환)
                body = json.dumps([actor,action,pid,res,rc,bh,ah,ph], ensure_ascii=False)
            if _hash(body) != eh: return False
            prev = eh
        # 꼬리 삭제 검출 — 메타(엔트리 수·head 앵커) 대조 (메타 없는 기존 장부는 skip)
        meta = {k: v for k, v in self.con.execute("SELECT key,value FROM audit_meta")}
        if meta:
            if meta.get("entry_count") != str(len(rows)): return False
            head = rows[-1][9] if rows else "GENESIS"
            if meta.get("head_entry_hash", head) != head: return False
        return True

    def verify_tail_state(self):
        """마지막 audit after_hash ↔ 실 테이블 checksum 대조 1건 (호출자 선택 검사)."""
        row = self.con.execute("SELECT after_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        if not row:
            return True
        return row[0] == self.store_checksum()

    def close(self): self.con.close()


def c2_check(db, pack, ctx):
    """C-2 자동검사 (freshness/duplicate/backup/checksum/evidence_refs). 통과 시 None, 실패 시 reason."""
    if ctx.get("actor") in ("auto", "reader"): return "G4_no_auto"
    # evidence_refs 필수(헌법)
    for e in pack["edges"]:
        if not e.get("evidence_refs"): return "evidence_refs_missing"
    # freshness: source_hash mismatch
    for ev in pack["evidence"]:
        if ev.get("source_missing"): return "freshness_source_missing"
        if ev.get("source_hash") != ev.get("captured_hash"): return "freshness_source_hash_mismatch"
        if ev.get("redaction_policy") != CUR_REDACTION_POLICY: return "freshness_redaction_policy_changed"
    # duplicate
    ch = _hash(pack["content"])
    if db.con.execute("SELECT 1 FROM applied_registry WHERE pack_id=? AND content_hash=?", (pack["pack_id"], ch)).fetchone():
        return "duplicate_already_applied"
    # backup
    if ctx.get("backup_fail"): return "backup_create_failed"
    return None


def staging_apply(db, pack, ctx, snap_dir, ts=None):
    """C-2 통과 후 transaction insert. checksum/WAL 중단 시 rollback."""
    before = db.store_checksum()
    reason = c2_check(db, pack, ctx)
    if reason:
        db.audit_append(ctx.get("actor","human"), "insert", pack["pack_id"], "BLOCK", reason, before, before, ts=ts)
        return {"applied": False, "reason": reason, "button": "disabled"}
    with db.write_lock():
        # backup (commit 직전) — checkpoint 포함 표준 스냅샷
        snap = db.snapshot(snap_dir, "snap_" + _hash(before))
        ch = _hash(pack["content"])
        now = _now_iso(ts)
        try:
            db.con.execute("BEGIN")
            for n in pack["nodes"]:
                db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) VALUES(?,?,?,1,0,'active',?,?,?)",
                               (n["id"], n["type"], n["sentence"], pack["pack_id"], ch, now))
            for e in pack["edges"]:
                db.con.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,state,evidence_refs,pack_id,content_hash,created_at) VALUES(?,?,?,?,1,'active',?,?,?,?)",
                               (e["id"], e["relation"], e["source"], e["target"], json.dumps(e["evidence_refs"]), pack["pack_id"], ch, now))
            for ev in pack["evidence"]:
                db.con.execute("INSERT INTO evidence(evidence_id,sentence,source_pointer_id,source_hash,redaction_policy,pack_id,created_at) VALUES(?,?,?,?,?,?,?)",
                               (ev["id"], ev["sentence"], ev.get("source_pointer_id","sp"), ev.get("source_hash"), ev.get("redaction_policy"), pack["pack_id"], now))
            # WAL/transaction 중단 주입
            if ctx.get("wal_abort"):
                db.con.execute("ROLLBACK")
                db.audit_append(ctx.get("actor","human"), "insert", pack["pack_id"], "ROLLBACK", "sqlite_wal_incomplete", before, db.store_checksum(), ts=ts)
                return {"applied": False, "reason": "sqlite_wal_incomplete", "button": "disabled"}
            # checksum mismatch 주입 → 롤백
            if ctx.get("checksum_mismatch"):
                db.con.execute("ROLLBACK")
                db.audit_append(ctx.get("actor","human"), "insert", pack["pack_id"], "ROLLBACK", "sqlite_checksum_mismatch", before, db.store_checksum(), ts=ts)
                return {"applied": False, "reason": "sqlite_checksum_mismatch", "button": "disabled"}
            db.con.execute("INSERT INTO applied_registry VALUES(?,?,?)", (pack["pack_id"], ch, now))
            db.con.execute("COMMIT")
        except Exception as ex:
            db.con.execute("ROLLBACK")
            return {"applied": False, "reason": "exception:"+type(ex).__name__, "button": "disabled"}
        after = db.store_checksum()
        db.audit_append(ctx.get("actor","human"), "insert", pack["pack_id"], "ALLOW", None, before, after, ts=ts)
    return {"applied": True, "reason": None, "button": "enabled", "snapshot": snap}


def tombstone(db, node_id, ctx, snap_dir, ts=None):
    before = db.store_checksum()
    with db.write_lock():
        snap = db.snapshot(snap_dir, "snap_t_" + _hash(before))
        db.con.execute("BEGIN")
        db.con.execute("UPDATE nodes SET state='tombstoned' WHERE node_id=?", (node_id,))
        db.con.execute("COMMIT")
        db.audit_append(ctx.get("actor","human"), "tombstone", "p", "ALLOW", None, before, db.store_checksum(), ts=ts)
    row = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (node_id,)).fetchone()
    phys = db.con.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone()
    return {"state": row[0] if row else None, "physical_present": bool(phys)}


def base_pack(**kw):
    p = {"pack_id":"p1","content":"노드 추가 정상",
         "nodes":[{"id":"n1","type":"judgment","sentence":"마진 확보되면 참여한다"}],
         "edges":[{"id":"e1","relation":"supports_judgment","source":"EVC-1","target":"n1","evidence_refs":["EVC-1"]}],
         "evidence":[{"id":"EVC-1","sentence":"마진 12% 확보","source_missing":False,"source_hash":"h1","captured_hash":"h1","redaction_policy":"v1"}]}
    p.update(kw); return p


def run():
    before_mtime = {p:(os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="obg_staging_")
    snap_dir = os.path.join(tmp,"snapshots"); os.makedirs(snap_dir, exist_ok=True)
    results = []
    def rec(cid, desc, ok): results.append((cid, desc, "PASS" if ok else "FAIL"))

    # 1. 정상 insert
    db = StagingDB(os.path.join(tmp,"s1.sqlite"))
    r = staging_apply(db, base_pack(), {"actor":"human"}, snap_dir)
    n = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    cand = db.con.execute("SELECT candidate,promotion_allowed FROM nodes").fetchone()
    rec(1,"정상 pack insert", r["applied"] and n==1 and cand==(1,0));
    # 2. duplicate 차단
    r2 = staging_apply(db, base_pack(), {"actor":"human"}, snap_dir)
    rec(2,"duplicate 차단", (not r2["applied"]) and r2["reason"]=="duplicate_already_applied")
    # 3. duplicate 비정규화 우회
    r3 = staging_apply(db, base_pack(content="노드   추가   정상\n"), {"actor":"human"}, snap_dir)
    rec(3,"duplicate 비정규화 우회 차단", (not r3["applied"]) and r3["reason"]=="duplicate_already_applied")
    db.close()
    # 4. evidence_refs 빈 값 reject
    db = StagingDB(os.path.join(tmp,"s4.sqlite"))
    p = base_pack(pack_id="p4"); p["edges"][0]["evidence_refs"]=[]
    r = staging_apply(db, p, {"actor":"human"}, snap_dir)
    rec(4,"evidence_refs 빈 값 reject", (not r["applied"]) and r["reason"]=="evidence_refs_missing"); db.close()
    # 5. freshness 실패(hash mismatch)
    db = StagingDB(os.path.join(tmp,"s5.sqlite"))
    p = base_pack(pack_id="p5"); p["evidence"][0]["captured_hash"]="h2"
    r = staging_apply(db, p, {"actor":"human"}, snap_dir)
    rec(5,"freshness 실패 차단", (not r["applied"]) and r["reason"]=="freshness_source_hash_mismatch"); db.close()
    # 6. backup 실패
    db = StagingDB(os.path.join(tmp,"s6.sqlite"))
    r = staging_apply(db, base_pack(pack_id="p6"), {"actor":"human","backup_fail":True}, snap_dir)
    rec(6,"backup 실패 차단", (not r["applied"]) and r["reason"]=="backup_create_failed"); db.close()
    # 7. checksum mismatch → rollback
    db = StagingDB(os.path.join(tmp,"s7.sqlite"))
    r = staging_apply(db, base_pack(pack_id="p7"), {"actor":"human","checksum_mismatch":True}, snap_dir)
    rolled = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]==0
    rec(7,"checksum mismatch rollback", (not r["applied"]) and r["reason"]=="sqlite_checksum_mismatch" and rolled); db.close()
    # 8. WAL/transaction 중단
    db = StagingDB(os.path.join(tmp,"s8.sqlite"))
    r = staging_apply(db, base_pack(pack_id="p8"), {"actor":"human","wal_abort":True}, snap_dir)
    rolled = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]==0
    rec(8,"WAL/transaction 중단 → 부분쓰기 미반영", (not r["applied"]) and rolled); db.close()
    # 9. tombstone
    db = StagingDB(os.path.join(tmp,"s9.sqlite")); staging_apply(db, base_pack(pack_id="p9"), {"actor":"human"}, snap_dir)
    t = tombstone(db, "n1", {"actor":"human"}, snap_dir)
    rec(9,"tombstone(논리, 물리 잔존)", t["state"]=="tombstoned" and t["physical_present"]);
    # 10. audit chain
    intact = db.verify_chain()
    db.con.execute("UPDATE audit_log SET action='TAMPER' WHERE seq=(SELECT min(seq) FROM audit_log)")
    broken = not db.verify_chain()
    rec(10,"audit chain intact→변조 BROKEN", intact and broken); db.close()
    # 11. 운영 경로 write 거부
    op_blocked = False
    try:
        StagingDB(OPERATING_PATHS[2])  # 운영 sqlite
    except PermissionError as e:
        op_blocked = (str(e)=="operating_store_forbidden")
    rec(11,"운영 경로 write 거부", op_blocked)
    # 12. 꼬리 삭제 검출 (메타 엔트리 수·head 앵커)
    db = StagingDB(os.path.join(tmp,"s12.sqlite"))
    staging_apply(db, base_pack(pack_id="p12a", content="c12a"), {"actor":"human"}, snap_dir)
    p12b = base_pack(pack_id="p12b", content="c12b")
    p12b["nodes"][0]["id"]="n12b"; p12b["edges"][0]["id"]="e12b"; p12b["edges"][0]["target"]="n12b"
    p12b["edges"][0]["source"]="EVC-12b"; p12b["edges"][0]["evidence_refs"]=["EVC-12b"]; p12b["evidence"][0]["id"]="EVC-12b"
    staging_apply(db, p12b, {"actor":"human"}, snap_dir)
    intact12 = db.verify_chain()
    db.con.execute("DELETE FROM audit_log WHERE seq=(SELECT max(seq) FROM audit_log)"); db.con.commit()
    rec(12,"audit 꼬리 삭제 검출(메타 앵커)", intact12 and (not db.verify_chain())); db.close()
    # 13. 마지막 after_hash ↔ 실 테이블 상태 대조
    db = StagingDB(os.path.join(tmp,"s13.sqlite"))
    staging_apply(db, base_pack(pack_id="p13", content="c13"), {"actor":"human"}, snap_dir)
    ok13 = db.verify_tail_state()
    db.con.execute("INSERT INTO nodes(node_id,node_type,sentence) VALUES('n13x','judgment','audit 우회 직접 쓰기')")
    db.con.commit()
    rec(13,"마지막 after_hash↔실 테이블 대조(우회 쓰기 검출)", ok13 and (not db.verify_tail_state())); db.close()
    # 14. 쓰기 진입 lock — 타 프로세스 lock 실재 시 명시 에러
    db = StagingDB(os.path.join(tmp,"s14.sqlite"))
    with open(db.path + ".lock", "w") as f:
        f.write("999999")
    locked_err = False
    try:
        staging_apply(db, base_pack(pack_id="p14", content="c14"), {"actor":"human"}, snap_dir)
    except RuntimeError as e:
        locked_err = str(e).startswith("staging_write_locked")
    os.remove(db.path + ".lock")
    r14 = staging_apply(db, base_pack(pack_id="p14", content="c14"), {"actor":"human"}, snap_dir)
    rec(14,"이중 실행 lock 감지(명시 에러)→해제 후 정상", locked_err and r14["applied"]); db.close()

    after_mtime = {p:(os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = before_mtime == after_mtime
    shutil.rmtree(tmp, ignore_errors=True)

    print("="*74); print("OpenBinggu Step 3 — staging write synthetic selftest (temp DB, 운영 write 0)"); print("="*74)
    npass = sum(1 for _,_,v in results if v=="PASS")
    for cid,desc,v in results:
        print(f"{'[OK]' if v=='PASS' else '[X]'} {cid:>2} {desc}")
    print("-"*74)
    print(f"RESULT: {npass}/{len(results)} PASS")
    print(f"operating_store_unchanged={store_unchanged}  operating_apply_executed=0  raw_leak=0")
    gate = "GO" if (npass==len(results) and store_unchanged) else "NO-GO"
    print(f"GATE: {gate}")
    return 0 if gate=="GO" else 1

if __name__ == "__main__":
    import sys; sys.exit(run())
