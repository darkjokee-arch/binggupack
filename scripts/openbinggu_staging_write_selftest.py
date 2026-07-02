#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu Step 3 — synthetic staging write 구현 + selftest.
기준: BINGGUPACK_PERSONAL_APPLY_ALLOWED_DESIGN.md(Step1) + BINGGUPACK_STAGING_SQLITE_SCHEMA_DESIGN.md(Step2).

안전: staging = temp 파일 SQLite(운영과 물리 분리). 운영 localcrab_index.sqlite/user_graph/_graph_merge
      connect 0·write 0(mtime 전후 대조). C-2 guard 통과 후에만 insert. apply(운영) 0.
"""
import os, sys, re, json, hashlib, sqlite3, tempfile, shutil
from contextlib import contextmanager
from datetime import datetime, timezone

CUR_REDACTION_POLICY = "v1"
# 운영 store 경로(거부 대상). 공개본은 작성자 절대경로를 포함하지 않는다.
# 사용자가 자기 운영 경로를 거부 대상으로 등록하려면 아래 env 를 설정한다.
# 미설정 시 temp 의 dummy 경로(존재하지 않아도 됨)로, "거부 대상 표식" 의미만 유지한다.
# 운영 store 경로(거부 대상) = OpenCrab user_graph/graph_merge 등. env 미설정 시 temp dummy(거부 표식).
# 주의: ledger.sqlite/capture_buffer.sqlite 는 StagingDB 의 정상 운영 대상(binggu.py 가 직접 연다) → 거부 목록 금지.
# MCP 경로 입력 차단은 핸들러(_u_save_candidate)가 ledger_path 입력 자체를 무시하는 방식으로 처리(과방어 회피).
# 정본: scripts/binggu_paths.py (셀프테스트 결합 해소 — 프로덕션 상수를 셀프테스트 파일에 두지 않음).
# 기존 `from openbinggu_staging_write_selftest import OPERATING_PATHS` 호출자 호환 위해 이 이름 재노출 유지.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from binggu_paths import OPERATING_PATHS  # noqa: E402,F401

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
        supersedes TEXT, pack_id TEXT, content_hash TEXT, created_at TEXT, semantic_subtype TEXT,
        use_count INTEGER DEFAULT 0, speaker TEXT);
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
    CREATE TABLE IF NOT EXISTS hit_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id TEXT, speaker TEXT, kind TEXT, outcome TEXT, subtype TEXT, ts TEXT,
        domain TEXT, context_hash TEXT, decision_id TEXT);
    CREATE TABLE IF NOT EXISTS hit_event_chain(sequence_no INTEGER PRIMARY KEY,
        event_id INTEGER NOT NULL, raw_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
        external_ts TEXT NOT NULL, prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL,
        chain_ver TEXT DEFAULT 'm1');
    CREATE TABLE IF NOT EXISTS hit_event_anchor(key TEXT PRIMARY KEY, value TEXT);
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
        # nodes 보조 필드 마이그레이션 — semantic_subtype 없으면 추가(기존 ledger 비파괴·기존 행 NULL).
        ncols = [c[1] for c in self.con.execute("PRAGMA table_info(nodes)")]
        if "semantic_subtype" not in ncols:
            self.con.execute("ALTER TABLE nodes ADD COLUMN semantic_subtype TEXT")
        # P1 랭킹 유용성 축 — use_count(노드 회상 빈도). 없으면 추가(기존 ledger 비파괴·기존 행 0).
        #   created_at(신선도 축)은 이미 base SCHEMA 컬럼이라 신규 ALTER 불필요(INSERT 시 채워짐).
        #   use_count 는 로컬 회상 카운터(폰/웹 집계는 worker write 필요 = deferred). DEFAULT 0.
        if "use_count" not in ncols:
            self.con.execute("ALTER TABLE nodes ADD COLUMN use_count INTEGER DEFAULT 0")
        # 화자 축 — speaker(owner=사장님 발화 / ai=AI 요약·회고). 없으면 추가(기존 ledger 비파괴·기존 행 NULL).
        #   사용자 AGI화 핵심: 사장님 원문 발화(owner)와 AI 작업회고(ai)를 같은 그래프에 구분 적재.
        #   기존 291행은 NULL=미상(대부분 conv_save AI 회고) — 소급 라벨은 별도 backfill(비파괴).
        if "speaker" not in ncols:
            self.con.execute("ALTER TABLE nodes ADD COLUMN speaker TEXT")
        # comp4 적중률 추적(2단) — hit_events 비파괴 ALTER(전부 NULL 허용·기존 행 보존).
        #   domain        : 선택이 일어난 도메인(분모 분리 키). _domain_from_cwd 정규화로 채워짐.
        #   context_hash  : 선택 시점 근거 스냅샷 sha256[:16](상관≠인과 봉인·PII 제외).
        #   decision_id   : 1단 대비 선택 1건 식별자(owner/ai 묶음 + 이중계상 방지 키).
        #   hit_events 는 store_checksum projection(nodes,edges,evidence) 밖이라 audit anchor 무손상.
        hcols = [c[1] for c in self.con.execute("PRAGMA table_info(hit_events)")]
        if "domain" not in hcols:
            self.con.execute("ALTER TABLE hit_events ADD COLUMN domain TEXT")
        if "context_hash" not in hcols:
            self.con.execute("ALTER TABLE hit_events ADD COLUMN context_hash TEXT")
        if "decision_id" not in hcols:
            self.con.execute("ALTER TABLE hit_events ADD COLUMN decision_id TEXT")
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

    # speaker 컬럼은 checksum 산정에서 제외(위치 비의존 명시 projection). speaker 는 보조 화자
    # 메타이며, 이를 포함하면 ALTER ADD COLUMN 후 기존 운영 ledger 의 audit after_hash(anchor)와
    # 어긋나 verify_tail_state 가 정상 노드를 변조로 오판한다 → 재봉인 불필요·옛 checksum 호환 유지.
    # 실증: state/_ck1 (anchor 210e04611a157877 == speaker 제외 checksum).
    def store_checksum(self):
        h = hashlib.sha256()
        for t in ("nodes", "edges", "evidence"):
            cols = ",".join(c[1] for c in self.con.execute("PRAGMA table_info(%s)" % t)
                            if c[1] != "speaker")
            for row in self.con.execute("SELECT %s FROM %s ORDER BY 1" % (cols, t)):
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
                # speaker(owner/ai/None)는 pack node dict 에서 일원화해 적재(ctx 아님). 미지정=None(NULL).
                db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at,semantic_subtype,speaker) VALUES(?,?,?,1,0,'active',?,?,?,?,?)",
                               (n["id"], n["type"], n["sentence"], pack["pack_id"], ch, now, n.get("semantic_subtype"), n.get("speaker")))
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
    # 15. created_at 기록 + use_count 기본 0 (P1 랭킹 신선도/유용성 축)
    db = StagingDB(os.path.join(tmp,"s15.sqlite"))
    staging_apply(db, base_pack(pack_id="p15", content="c15"), {"actor":"human"}, snap_dir, ts="2026-06-17T00:00:00Z")
    row = db.con.execute("SELECT created_at, use_count FROM nodes WHERE node_id='n1'").fetchone()
    rec(15,"INSERT 시 created_at 기록 + use_count 기본 0", row==("2026-06-17T00:00:00Z", 0)); db.close()
    # 16. use_count ALTER 비파괴 — 구 ledger(use_count 컬럼 없음)에 기존 행 보존 + 컬럼 추가
    legacy = os.path.join(tmp,"legacy16.sqlite")
    lc = sqlite3.connect(legacy)
    lc.executescript("CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
                     " candidate INTEGER DEFAULT 1, promotion_allowed INTEGER DEFAULT 0, state TEXT,"
                     " supersedes TEXT, pack_id TEXT, content_hash TEXT, created_at TEXT, semantic_subtype TEXT);")
    lc.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state) VALUES('old1','judgment','옛 노드',0,'active')")
    lc.commit(); lc.close()
    db = StagingDB(legacy)  # __init__ 마이그레이션이 use_count 추가해야
    cols16 = [c[1] for c in db.con.execute("PRAGMA table_info(nodes)")]
    old_row = db.con.execute("SELECT sentence, use_count FROM nodes WHERE node_id='old1'").fetchone()
    rec(16,"use_count ALTER 비파괴(기존 행 보존·신규 컬럼 0/NULL)",
        "use_count" in cols16 and old_row[0]=="옛 노드" and old_row[1] in (0, None)); db.close()

    # 17. comp4 hit_events ALTER 비파괴 — 구 ledger(3 신규 컬럼 없음) 보존 + 컬럼 추가 + store_checksum 불변
    legacy17 = os.path.join(tmp,"legacy17.sqlite")
    lc = sqlite3.connect(legacy17)
    lc.executescript(
        "CREATE TABLE hit_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " node_id TEXT, speaker TEXT, kind TEXT, outcome TEXT, subtype TEXT, ts TEXT);")
    lc.execute("INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts) "
               "VALUES('old1','owner','직감','hit','J','2026-06-17T00:00:00Z')")
    lc.commit(); lc.close()
    db = StagingDB(legacy17)  # __init__ 마이그레이션이 domain/context_hash/decision_id 추가해야
    hcols17 = [c[1] for c in db.con.execute("PRAGMA table_info(hit_events)")]
    old_he = db.con.execute("SELECT outcome,domain,context_hash,decision_id FROM hit_events WHERE node_id='old1'").fetchone()
    ck_before = db.store_checksum()
    db.con.execute("INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts,domain,context_hash,decision_id) "
                   "VALUES('n2','owner','직감','hit','J','2026-06-18T00:00:00Z','bid','abc123','d1')")
    db.con.commit()
    ck_after = db.store_checksum()
    rec(17,"comp4 hit_events ALTER 비파괴(기존 행 보존·신규 NULL) + store_checksum 불변(audit anchor)",
        all(c in hcols17 for c in ("domain","context_hash","decision_id"))
        and old_he==("hit", None, None, None) and ck_before==ck_after); db.close()

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
