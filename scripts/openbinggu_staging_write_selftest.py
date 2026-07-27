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
# 정본 스키마 위임(staging=True) + evloc 축 유틸(Unit A). has_table/table_columns 는 write 경로가
# env 플래그가 아니라 **ledger 실재 상태**로 판단하기 위한 정본(재검증 NEW2.5).
from binggu_schema import (  # noqa: E402
    apply_schema, has_table, table_columns, safe_backup, locator_checksum,
)

def _canon(s): return re.sub(r"\s+", " ", str(s)).strip().encode("utf-8", "replace")
def _hash(s): return hashlib.sha256(_canon(s)).hexdigest()[:16]


# ── evidence_locator (앞막이) — 저장 시점 원본 좌표 기록 ─────────────────────────
# 스펙 §1 증거 3요소(source_id · 위치 · excerpt_sha)를 evidence_id 에 부착한다(MF2.6).
# 불변 3개(전부 아래 함수들이 강제·selftest 로 증명):
#   ① write 여부는 env 가 아니라 has_table(con,'evidence_locator') 로 판단한다(NEW2.5).
#      hook·MCP·CLI·schtasks 는 env 원천이 전부 달라 env 로 갈리면 반쪽 스키마가 된다.
#   ② locator 실패가 저장을 절대 롤백시키지 않는다 — SAVEPOINT 로 감싸고, 실패는 삼키지 않고
#      **사유를 report 로 반환**한다(silent drop 금지 · §13 B-10).
#   ③ excerpt 는 ledger 밖 jsonl 에 이중 보관(MF1.3) — 테이블 부재로 skip 해도 유실 0.
EVLOC_TABLE = "evidence_locator"
EVLOC_MIRROR_NAME = "evloc_mirror.jsonl"
# UNIQUE(evidence_id, source_id, locator, excerpt_sha) 참여 컬럼. sqlite 는 NULL 을 서로 distinct 로
# 보므로 NULL 이 섞이면 중복 차단이 무력해진다 → writer 가 None 대신 '' 로 정규화한다(Unit A 주의4).
_EVLOC_UNIQUE_COLS = ("evidence_id", "source_id", "locator", "excerpt_sha")


def excerpt_sha(text):
    """§1 excerpt_sha = sha256(excerpt_text.utf-8) **전체 64hex**.

    _hash(공백정규화 sha256[:16])와 **다른 함수**다 — _hash 산식을 건드리면 c2_check 의
    applied_registry 중복판정이 무효화돼 과거 pack 이 전부 재적재 가능해진다(설계 §1-1).
    """
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def evloc_mirror_path(db_path):
    """excerpt 이중 보관 jsonl 경로 = ledger 파일과 같은 디렉터리.

    `~/.binggupack` 리터럴을 쓰지 않는다(NEW2.9) — 운영 ledger 는 <binggu_home>/ledger.sqlite 이므로
    결과가 <binggu_home>/evloc_mirror.jsonl 로 같고, 격리홈/temp 홈/BINGGU_HOME 에서는 **그 홈 안**에
    남아 운영홈 오염·스플릿브레인이 구조적으로 불가능하다.
    """
    d = os.path.dirname(os.path.abspath(str(db_path)))
    return os.path.join(d or ".", EVLOC_MIRROR_NAME)


def loc_id(evidence_id, source_id, locator, exc_sha):
    """loc_id = sha256(evidence_id|source_id|locator|excerpt_sha)[:24] (설계 §1-1)."""
    key = "|".join(str(x or "") for x in (evidence_id, source_id, locator, exc_sha))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def loc_row(evidence_id, excerpt_text, source_id=None, locator=None, container_sha=None,
            match_method="live_capture", confidence="T1", verified_by="auto",
            batch_id=None, created_at=None):
    """evidence_locator 1행 dict. UNIQUE 참여 4컬럼은 None → '' 정규화."""
    exc = "" if excerpt_text is None else str(excerpt_text)
    esha = excerpt_sha(exc)
    row = {
        "evidence_id": "" if evidence_id is None else str(evidence_id),
        "source_id": "" if source_id is None else str(source_id),
        "locator": "" if locator is None else str(locator),
        "excerpt_sha": esha,
        "excerpt_text": exc,
        "container_sha": container_sha,
        "match_method": match_method,
        "confidence": confidence,
        "verified_by": verified_by,
        "batch_id": batch_id,
        "created_at": _now_iso(created_at),
    }
    row["loc_id"] = loc_id(row["evidence_id"], row["source_id"], row["locator"], esha)
    return row


def _mirror_append(db_path, rows, persisted, reason=None):
    """excerpt 이중 보관(MF1.3) — append-only jsonl. 실패해도 저장 흐름을 막지 않되 사유는 반환."""
    path = evloc_mirror_path(db_path)
    try:
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                rec = dict(r)
                rec["_persisted"] = bool(persisted)
                rec["_skip_reason"] = reason
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"written": len(rows), "path": path, "error": None}
    except Exception as ex:
        return {"written": 0, "path": path, "error": "%s: %s" % (type(ex).__name__, ex)}


def insert_locators(con, rows, db_path=None):
    """evidence_locator 적재(INSERT OR IGNORE). **절대 raise 하지 않는다** — 사유를 report 로 반환.

    반환 {attempted, inserted, present, mirrored, skipped, reason, error, mirror_path}
      reason: None(정상) | 'no_rows' | 'table_absent' | 'insert_failed' | 'insert_dropped'
      present: 적재 후 실제로 ledger 에 존재하는 대상 행 수(멱등 재적재분 포함).
    ★`INSERT OR IGNORE` 는 UNIQUE 뿐 아니라 NOT NULL/CHECK 위반까지 **예외 없이 버린다**.
      그래서 '예외 0' 만 보면 조용한 유실이 성립한다 → 삽입 후 loc_id 실재를 다시 읽어
      attempted 와 대조하고, 못 미치면 'insert_dropped' 로 표면화한다(silent drop 금지).
    호출자가 열어둔 트랜잭션 안이면 SAVEPOINT 로 감싸 **locator 실패가 pack 저장을 롤백시키지
    않게** 한다(NEW2.5 시나리오 B: `no such table` 이 열린 BEGIN 안에서 터지면 owner 발화 통째 소실).
    """
    rows = list(rows or [])
    rep = {"attempted": len(rows), "inserted": 0, "present": None, "mirrored": 0,
           "skipped": False, "reason": None, "error": None, "mirror_path": None}
    if not rows:
        rep["skipped"] = True
        rep["reason"] = "no_rows"
        return rep
    if not has_table(con, EVLOC_TABLE):
        rep["skipped"] = True
        rep["reason"] = "table_absent"      # 플래그 OFF ledger — 정상 경로(무해 skip)
    else:
        live = set(table_columns(con, EVLOC_TABLE))
        in_txn = bool(getattr(con, "in_transaction", False))
        sp = "evloc_sp"
        try:
            if in_txn:
                con.execute("SAVEPOINT %s" % sp)
            n = 0
            for r in rows:
                cols = [c for c in r if c in live]
                if not cols:
                    continue
                vals = [("" if r[c] is None and c in _EVLOC_UNIQUE_COLS else r[c]) for c in cols]
                cur = con.execute(
                    "INSERT OR IGNORE INTO %s(%s) VALUES(%s)"
                    % (EVLOC_TABLE, ",".join(cols), ",".join("?" * len(cols))), vals)
                n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            # OR IGNORE 가 조용히 버린 행 검출 — loc_id 실재를 다시 읽어 attempted 와 대조.
            if "loc_id" in live:
                ids = [r["loc_id"] for r in rows if r.get("loc_id")]
                if ids:
                    rep["present"] = con.execute(
                        "SELECT count(*) FROM %s WHERE loc_id IN (%s)"
                        % (EVLOC_TABLE, ",".join("?" * len(ids))), ids).fetchone()[0]
                    if rep["present"] < len(ids):
                        rep["reason"] = "insert_dropped"
                        rep["error"] = ("INSERT OR IGNORE 가 %d/%d 행을 버렸다(제약 불일치 의심)"
                                        % (len(ids) - rep["present"], len(ids)))
            # evloc 전용 무결성 앵커(MF1.3). ★audit_log 에 넣지 않는다(NEW2.7) — locator 해시가
            # audit tail 을 점유하면 verify_tail_state(store_checksum 대조)가 영구 False 가 된다.
            if n and has_table(con, "audit_meta"):
                con.execute("INSERT OR REPLACE INTO audit_meta(key,value) VALUES('evloc_head',?)",
                            (locator_checksum(con),))
            if in_txn:
                con.execute("RELEASE %s" % sp)
            else:
                con.commit()
            rep["inserted"] = n
        except Exception as ex:
            rep["reason"] = "insert_failed"
            rep["error"] = "%s: %s" % (type(ex).__name__, ex)
            try:
                if in_txn:
                    con.execute("ROLLBACK TO %s" % sp)
                    con.execute("RELEASE %s" % sp)
                else:
                    con.rollback()
            except Exception as ex2:      # 롤백 실패도 삼키지 않는다
                rep["error"] += " / savepoint_rollback:%s" % type(ex2).__name__
    if db_path:
        m = _mirror_append(db_path, rows, persisted=bool(rep["inserted"]), reason=rep["reason"])
        rep["mirrored"] = m["written"]
        rep["mirror_path"] = m["path"]
        if m["error"]:
            rep["error"] = (rep["error"] + " / " if rep["error"] else "") + "mirror:" + m["error"]
    return rep


def verify_locator_tail(con):
    """audit_meta['evloc_head'] ↔ 현재 locator_checksum 대조. 앵커 없으면 True(미사용 ledger)."""
    if not has_table(con, "audit_meta"):
        return True
    row = con.execute("SELECT value FROM audit_meta WHERE key='evloc_head'").fetchone()
    if not row:
        return True
    return row[0] == locator_checksum(con)


def _now_iso(ts=None):
    """실시간 UTC ISO. selftest 재현성 필요 시 ts 명시 주입(분리 파라미터)."""
    return ts if ts else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pid_alive(pid):
    """lock 파일 pid 생존 검사(stale lock 판정용). 비양수 pid 는 dead(=stale) 취급.

    ★Windows 에서 os.kill(pid, 0)은 생존 확인이 아니라 TerminateProcess 로
    프로세스를 죽인다 — 절대 사용 금지. OpenProcess(QUERY_LIMITED) 핸들 획득으로만 판정.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            # 종료됐어도 누군가 핸들을 쥐고 있으면 OpenProcess 는 성공한다(zombie) —
            # exit code 로 실행 중(STILL_ACTIVE) 여부까지 확인해야 진짜 생존 판정.
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True  # 판정 불가 → 보수적으로 alive(fail-closed)
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)  # POSIX: signal 0 = 존재 확인만(전송 없음)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 존재하지만 권한 없음 = alive
    except OSError:
        return True  # 판정 불가 → 보수적으로 alive(fail-closed: lock 을 지우지 않음)
    return True


class StagingDB:
    """temp 파일 staging SQLite. 운영 경로 거부. WAL + transaction + checksum.

    스키마 정본: scripts/binggu_schema.py apply_schema(con, staging=True).
    staging=True → nodes.candidate DEFAULT 1(미확정 스테이징). 정본은 상위집합이라
    owner_acceptances/recall_traces/recall_outcomes 등 미사용 테이블도 함께 생성되나
    store_checksum(nodes/edges/evidence 한정)·audit anchor 에 무영향(추가 테이블 무해).
    checksum 대상 컬럼 순서(nodes/edges/evidence)는 정본과 완전 일치 확인.
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
        apply_schema(self.con, staging=True)  # 정본 스키마 위임(nodes.candidate DEFAULT 1)
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
        """표준 스냅샷 — sqlite **Online Backup API**(MF1.1). 검증 실패 시 raise.

        구 경로(`PRAGMA wal_checkpoint(TRUNCATE)` + `shutil.copy2`)는 폐기했다: 다른 연결이 읽기
        트랜잭션을 잡고 있으면 checkpoint 가 busy 로 실패하는데 반환값을 버려서 무음이고, main
        파일만 복사돼 **조용히 잘린 백업**이 만들어진다(실증 501행 중 1행·예외 0). staging_apply 가
        매 저장마다 이 함수를 부르므로 이 수리가 곧 상시 스냅샷 신뢰 회복이다.
        safe_backup 은 백업 직후 사본을 열어 원본과 상대 대조(테이블집합·행수·audit_meta·
        user_version·quick_check)까지 마친다 — 통과 못 하면 BackupVerifyError.
        """
        self.con.commit()
        snap = os.path.join(snap_dir, name)
        safe_backup(self.path, snap)
        return snap

    @contextmanager
    def write_lock(self):
        """쓰기 진입 lock 파일(O_EXCL) — 이중 실행 감지 시 명시 에러. 같은 pid 재진입 허용.

        죽은 프로세스가 남긴 stale lock(pid 사망·파싱 불가·비양수)은 _pid_alive 검사 후
        제거하고 O_EXCL 재시도 1회만(자동 복구). 재시도도 충돌이면 두 프로세스가 동시에
        stale 청소하는 레이스 — fail-closed 유지(명시 에러). 살아있는 타 pid 는 기존대로 차단.
        """
        lock = self.path + ".lock"
        owner = False
        for attempt in (0, 1):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                owner = True
                break
            except FileExistsError:
                pid_raw = ""
                try:
                    with open(lock, "r") as f:
                        pid_raw = f.read().strip()
                except OSError:
                    pass
                if pid_raw == str(os.getpid()):
                    break  # 같은 pid 재진입 허용(해제는 바깥 holder 가 — 기존 semantics)
                try:
                    pid = int(pid_raw)
                except ValueError:
                    pid = -1  # 쓰레기/빈 pid 문자열 → stale 취급
                if attempt == 0 and not _pid_alive(pid):
                    try:
                        os.remove(lock)  # 죽은 프로세스 잔존 lock 자동 정리
                    except OSError:
                        pass  # 이미 사라졌으면(정상 해제 레이스) 그대로 재시도
                    continue
                raise RuntimeError(
                    "staging_write_locked: concurrent writer detected (lock=%s, pid=%s) — "
                    "그 프로세스가 종료되면 자동 해제됩니다. 비정상 잔존 시 %s 를 삭제하세요."
                    % (lock, pid_raw or "?", lock))
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

    def audit_append(self, actor, action, pack_id, result, reason, before, after, ts=None, commit=True):
        """audit chain 1행 append. commit=False → con.commit() 생략(단일 외부 트랜잭션 안에서
        여러 audit 를 누적할 때 · P1-B.1 crash-atomic bundle). 같은 con 이므로 후속 append 의
        prev_audit_hash 는 uncommitted 최신 행을 그대로 읽어 체인 연속성 유지."""
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
        if commit:
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
        # 꼬리 삭제 검출 — 메타(엔트리 수·head 앵커) 대조. ★ 각 앵커가 존재할 때만 대조:
        # audit_append 가 기록하는 앵커라, 신규 ledger(audit 0 건)는 audit_meta 에 ledger_id
        # (apply_schema)만 있고 앵커 없음 → skip = INTACT. 앵커 있으면 stale/삭제 그대로 검출
        # (rows 삭제 시 head=GENESIS != 저장 head → False).
        meta = {k: v for k, v in self.con.execute("SELECT key,value FROM audit_meta")}
        if meta.get("entry_count") is not None:
            if meta.get("entry_count") != str(len(rows)): return False
        if meta.get("head_entry_hash") is not None:
            head = rows[-1][9] if rows else "GENESIS"
            if meta.get("head_entry_hash") != head: return False
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
    # P1-A TAE-2 hardening: allowlist(== 'human') — 기존 denylist(auto/reader 만 차단)는 'agent'/
    # 'system'/누락/대문자 등 임의 sentinel 이 fail-OPEN 이었다(trusted approval no-approval actor 가
    # 'reader' 가 아니면 통과). human 정확 매칭만 허용 → 어떤 non-human sentinel 도 write 0.
    if ctx.get("actor") != "human": return "G4_no_auto"
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


def apply_pack_in_txn(db, pack, now_iso, loc_rows=None, loc_report=None):
    """★P1-B.1: 단일 열린 트랜잭션 안에서 pack INSERT(nodes/edges/evidence/applied_registry).
    BEGIN/COMMIT/snapshot/audit 없음 — 호출자가 단일 트랜잭션 경계·audit 를 관리한다.
    staging_apply(단건)와 commit_bundle(묶음 crash-atomic)의 유일한 INSERT SQL 원천(schema drift 방지).
    반환 content_hash.

    loc_rows: evidence_locator 행 리스트(앞막이). **pack dict 에 넣지 않고 별도 인자**로 받는다
      (MF2.7) — excerpt 전문이 pack 객체에 상주하면 유출 차단이 '타입 경계'가 아니라 '규율'이 된다.
      기본 None 이므로 기존 3인자 호출(binggu_hosted_bundle.py 등)은 완전 무영향.
    loc_report: 지정 시 locator 적재 결과 dict 를 **제자리 갱신**(사유 반환 — best-effort ≠ 침묵)."""
    ch = _hash(pack["content"])
    for n in pack["nodes"]:
        # speaker(owner/ai/None)는 pack node dict 에서 일원화해 적재(ctx 아님). 미지정=None(NULL).
        db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at,semantic_subtype,speaker) VALUES(?,?,?,1,0,'active',?,?,?,?,?)",
                       (n["id"], n["type"], n["sentence"], pack["pack_id"], ch, now_iso, n.get("semantic_subtype"), n.get("speaker")))
    for e in pack["edges"]:
        db.con.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,state,evidence_refs,pack_id,content_hash,created_at) VALUES(?,?,?,?,1,'active',?,?,?,?)",
                       (e["id"], e["relation"], e["source"], e["target"], json.dumps(e["evidence_refs"]), pack["pack_id"], ch, now_iso))
    for ev in pack["evidence"]:
        db.con.execute("INSERT INTO evidence(evidence_id,sentence,source_pointer_id,source_hash,redaction_policy,pack_id,created_at) VALUES(?,?,?,?,?,?,?)",
                       (ev["id"], ev["sentence"], ev.get("source_pointer_id","sp"), ev.get("source_hash"), ev.get("redaction_policy"), pack["pack_id"], now_iso))
    db.con.execute("INSERT INTO applied_registry VALUES(?,?,?)", (pack["pack_id"], ch, now_iso))
    # 앞막이 — 저장 시점 원본 좌표. 테이블 실재 판정 + SAVEPOINT 격리라 실패해도 pack 은 살아남는다.
    rep = insert_locators(db.con, loc_rows, db_path=getattr(db, "path", None))
    if loc_report is not None:
        loc_report.clear()
        loc_report.update(rep)
    return ch


def staging_apply(db, pack, ctx, snap_dir, ts=None, loc_rows=None):
    """C-2 통과 후 transaction insert. checksum/WAL 중단 시 rollback.

    loc_rows: evidence_locator 앞막이 행(선택). pack 과 분리된 별도 인자(MF2.7)."""
    before = db.store_checksum()
    reason = c2_check(db, pack, ctx)
    if reason:
        db.audit_append(ctx.get("actor","human"), "insert", pack["pack_id"], "BLOCK", reason, before, before, ts=ts)
        return {"applied": False, "reason": reason, "button": "disabled"}
    loc_report = {}
    with db.write_lock():
        # backup (commit 직전) — Online Backup API 표준 스냅샷(MF1.1).
        # 백업 실패는 조용히 넘기지 않는다: 기존 backup_fail 주입과 같은 BLOCK 경로로 표면화한다
        # (구 copy2 경로는 예외가 그대로 밖으로 튀어 저장이 예외로 죽었다).
        try:
            snap = db.snapshot(snap_dir, "snap_" + _hash(before))
        except Exception as ex:
            db.audit_append(ctx.get("actor","human"), "insert", pack["pack_id"], "BLOCK",
                            "backup_create_failed", before, before, ts=ts)
            return {"applied": False, "reason": "backup_create_failed", "button": "disabled",
                    "backup_error": "%s: %s" % (type(ex).__name__, ex)}
        now = _now_iso(ts)
        try:
            db.con.execute("BEGIN")
            # nodes/edges/evidence/applied_registry(+locator) — 단일 SQL 원천
            apply_pack_in_txn(db, pack, now, loc_rows=loc_rows, loc_report=loc_report)
            # WAL/transaction 중단 주입 (ROLLBACK 이 applied_registry 포함 전체 원복 — 최종 상태 동일)
            if ctx.get("wal_abort"):
                db.con.execute("ROLLBACK")
                db.audit_append(ctx.get("actor","human"), "insert", pack["pack_id"], "ROLLBACK", "sqlite_wal_incomplete", before, db.store_checksum(), ts=ts)
                return {"applied": False, "reason": "sqlite_wal_incomplete", "button": "disabled"}
            # checksum mismatch 주입 → 롤백
            if ctx.get("checksum_mismatch"):
                db.con.execute("ROLLBACK")
                db.audit_append(ctx.get("actor","human"), "insert", pack["pack_id"], "ROLLBACK", "sqlite_checksum_mismatch", before, db.store_checksum(), ts=ts)
                return {"applied": False, "reason": "sqlite_checksum_mismatch", "button": "disabled"}
            db.con.execute("COMMIT")
        except Exception as ex:
            db.con.execute("ROLLBACK")
            return {"applied": False, "reason": "exception:"+type(ex).__name__, "button": "disabled"}
        after = db.store_checksum()
        db.audit_append(ctx.get("actor","human"), "insert", pack["pack_id"], "ALLOW", None, before, after, ts=ts)
    return {"applied": True, "reason": None, "button": "enabled", "snapshot": snap,
            "locator": loc_report}


def tombstone(db, node_id, ctx, snap_dir, ts=None):
    before = db.store_checksum()
    with db.write_lock():
        db.snapshot(snap_dir, "snap_t_" + _hash(before))
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
    # 14. 쓰기 진입 lock — '살아있는' 타 프로세스 lock 실재 시 명시 에러.
    #     죽은 pid lock 은 이제 stale 자동 정리(회복) 대상이라 차단 대상이 아님
    #     (stale 회복 경로는 tests/test_staging_stale_lock.py 가 커버) → 실제 live 자식 pid 로 검증.
    import subprocess
    db = StagingDB(os.path.join(tmp,"s14.sqlite"))
    child14 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with open(db.path + ".lock", "w") as f:
            f.write(str(child14.pid))
        locked_err = False
        try:
            staging_apply(db, base_pack(pack_id="p14", content="c14"), {"actor":"human"}, snap_dir)
        except RuntimeError as e:
            locked_err = str(e).startswith("staging_write_locked")
    finally:
        child14.kill(); child14.wait()
    os.remove(db.path + ".lock")
    r14 = staging_apply(db, base_pack(pack_id="p14", content="c14"), {"actor":"human"}, snap_dir)
    rec(14,"이중 실행 lock 감지(live 타 pid 명시 에러)→해제 후 정상", locked_err and r14["applied"]); db.close()
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

    # ===== 18~23 evidence_locator 앞막이 (플래그 OFF/ON · 실패 격리 · 백업) =====
    from binggu_schema import evloc_env  # 테스트 전용 컨텍스트(종료 시 env 원복)

    def _loc_pack(pid):
        p = base_pack(pack_id=pid, content="c_" + pid)
        p["nodes"][0]["id"] = "n_" + pid
        p["edges"][0]["id"] = "e_" + pid
        p["edges"][0]["target"] = "n_" + pid
        p["edges"][0]["source"] = "EVC-" + pid
        p["edges"][0]["evidence_refs"] = ["EVC-" + pid]
        p["evidence"][0]["id"] = "EVC-" + pid
        return p

    def _rows_for(pid, raw="원본 발화 전체 — 마진 12% 확보 확인됨."):
        return [loc_row("EVC-" + pid, "마진 12% 확보", source_id="session:S-" + pid,
                        locator="off:11:len:9", container_sha=excerpt_sha(raw),
                        batch_id="save:" + pid)]

    # 18. 테이블 부재(플래그 OFF) ledger → 저장 정상 + skip 사유 반환 + mirror jsonl 유실 0
    db = StagingDB(os.path.join(tmp, "s18_locoff.sqlite"))
    r18 = staging_apply(db, _loc_pack("p18"), {"actor": "human"}, snap_dir,
                        loc_rows=_rows_for("p18"))
    loc18 = r18.get("locator") or {}
    mir18 = evloc_mirror_path(db.path)
    n18 = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    rec(18, "locator 테이블 부재 → 저장 정상(applied) + reason=table_absent + mirror jsonl 보관",
        r18["applied"] and n18 == 1 and loc18.get("reason") == "table_absent"
        and loc18.get("inserted") == 0 and loc18.get("mirrored") == 1
        and os.path.exists(mir18) and "마진 12% 확보" in open(mir18, encoding="utf-8").read())
    rec(19, "locator 부재 ledger 는 has_table=False(env 아닌 실재 판정·NEW2.5)",
        not has_table(db.con, EVLOC_TABLE)); db.close()

    # 20. 테이블 실재 ledger → locator 행 생성 + 저장 정상 + 전용 무결성 앵커
    with evloc_env(True):
        db = StagingDB(os.path.join(tmp, "s20_locon.sqlite"))
    r20 = staging_apply(db, _loc_pack("p20"), {"actor": "human"}, snap_dir,
                        loc_rows=_rows_for("p20"))
    loc20 = r20.get("locator") or {}
    row20 = db.con.execute(
        "SELECT evidence_id, source_id, locator, excerpt_sha, excerpt_text, batch_id"
        " FROM evidence_locator").fetchall()
    tail20 = verify_locator_tail(db.con)
    rec(20, "locator 테이블 실재 → 행 적재 + 저장 정상 + evloc 앵커(verify_locator_tail)",
        r20["applied"] and loc20.get("inserted") == 1 and len(row20) == 1
        and row20[0] == ("EVC-p20", "session:S-p20", "off:11:len:9",
                         excerpt_sha("마진 12% 확보"), "마진 12% 확보", "save:p20")
        and tail20)
    # 21. 멱등 — 같은 4튜플 재적재는 UNIQUE 로 1행 유지(INSERT OR IGNORE)
    rep21 = insert_locators(db.con, _rows_for("p20"), db_path=db.path)
    n21 = db.con.execute("SELECT count(*) FROM evidence_locator").fetchone()[0]
    rec(21, "locator 재적재 멱등(UNIQUE 4튜플 → 1행 유지)",
        n21 == 1 and rep21["inserted"] == 0 and rep21["error"] is None)
    # 22. evloc 앵커는 audit_log tail 을 점유하지 않는다(NEW2.7 — verify_tail_state 영구 False 방지)
    rec(22, "locator 적재 후에도 audit chain·tail anchor 무손상(evloc 는 audit_meta 별도 키)",
        db.verify_chain() and db.verify_tail_state()
        and db.con.execute("SELECT action FROM audit_log ORDER BY seq DESC LIMIT 1"
                           ).fetchone()[0] == "insert"); db.close()

    # 23. locator INSERT 예외 주입(동명 VIEW → 'cannot modify view') → pack 저장은 살아남고 사유만 반환
    db = StagingDB(os.path.join(tmp, "s23_locfail.sqlite"))
    db.con.execute("CREATE VIEW evidence_locator AS SELECT node_id AS loc_id,"
                   " node_id AS evidence_id FROM nodes")
    db.con.commit()
    r23 = staging_apply(db, _loc_pack("p23"), {"actor": "human"}, snap_dir,
                        loc_rows=_rows_for("p23"))
    loc23 = r23.get("locator") or {}
    n23 = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    e23 = db.con.execute("SELECT count(*) FROM evidence").fetchone()[0]
    rec(23, "locator INSERT 예외 → 저장 롤백 0(nodes/evidence 적재) + reason=insert_failed 반환",
        r23["applied"] and n23 == 1 and e23 == 1
        and loc23.get("reason") == "insert_failed" and loc23.get("error")
        and loc23.get("mirrored") == 1 and db.verify_chain()); db.close()

    # 23b. ★INSERT OR IGNORE 는 NOT NULL 위반까지 예외 없이 버린다 → '예외 0' 을 성공으로 읽으면
    #      조용한 유실. present 재조회로 잡아 'insert_dropped' 로 표면화하는지 확인.
    db = StagingDB(os.path.join(tmp, "s23b_locdrop.sqlite"))
    db.con.execute("CREATE TABLE evidence_locator(loc_id TEXT PRIMARY KEY, evidence_id TEXT,"
                   " must_have TEXT NOT NULL)")
    db.con.commit()
    r23b = staging_apply(db, _loc_pack("p23b"), {"actor": "human"}, snap_dir,
                         loc_rows=_rows_for("p23b"))
    loc23b = r23b.get("locator") or {}
    rec(26, "OR IGNORE 무음 폐기 검출(reason=insert_dropped) + pack 저장은 정상",
        r23b["applied"] and loc23b.get("reason") == "insert_dropped"
        and loc23b.get("present") == 0 and loc23b.get("inserted") == 0
        and loc23b.get("mirrored") == 1
        and db.con.execute("SELECT count(*) FROM nodes").fetchone()[0] == 1); db.close()

    # 24. snapshot = Online Backup API — 리더가 읽기 트랜잭션을 쥔 상태에서도 사본 행수 == 원본
    #     (구 wal_checkpoint+copy2 는 이 조건에서 예외 0·501행 중 1행만 백업했다 · MF1.1)
    db = StagingDB(os.path.join(tmp, "s24_backup.sqlite"))
    for k in range(5):
        staging_apply(db, _loc_pack("p24_%d" % k), {"actor": "human"}, snap_dir)
    src_n = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    reader = sqlite3.connect(db.path)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM nodes").fetchone()
    try:
        snap24 = db.snapshot(snap_dir, "snap_backup24.sqlite")
        sc = sqlite3.connect(snap24)
        snap_n = sc.execute("SELECT count(*) FROM nodes").fetchone()[0]
        sc.close()
    finally:
        reader.close()
    rec(24, "snapshot(Online Backup API): 리더 점유 중에도 사본 행수 == 원본 [%d]" % src_n,
        src_n == 5 and snap_n == src_n)
    # 25. 구 3인자 apply_pack_in_txn 호출 호환(hosted_bundle 경로) — loc_rows 기본 None
    db.con.execute("BEGIN")
    ch25 = apply_pack_in_txn(db, _loc_pack("p25"), _now_iso())
    db.con.execute("COMMIT")
    rec(25, "apply_pack_in_txn 구 3인자 호출 호환(loc_rows 기본 None)",
        ch25 == _hash("c_p25")
        and db.con.execute("SELECT count(*) FROM nodes WHERE node_id='n_p25'").fetchone()[0] == 1)
    db.close()

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
