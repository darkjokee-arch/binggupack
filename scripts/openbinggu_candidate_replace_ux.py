# -*- coding: utf-8 -*-
"""OpenBinggu v1.0 — replace transaction (후보 수정 = 기각+신규 저장 묶음, in-place 수정 0).

설계 정본: docs/BINGGUPACK_CANDIDATE_REPLACE_TRANSACTION_DESIGN.md (r2 우선):
  - confirm = "REPLACE <n> <id8> WITH <수정문장>" 정확 일치 — confirm 은 transaction 밖,
    실행 직전 목록 재실행 + id8 재검증(기각 UX 기구현 패턴 재사용).
  - 원자성 = 외곽 단일 SQL transaction 불가(내부 BEGIN 중첩) → 묶음 스냅샷 1회 선확보 +
    중간 실패 시 파일 원복(compensation) + "rolled_back:" audit 1건으로 실패 사실 기록.
  - 같은 오판 재생성 차단 = canonical_hash(공백·줄끝 정규화) 기준,
    predecessor 동일 → replace_same_content / 다른 active 동일 → duplicate_active_content.
  - 역링크 = deprecations.reason "replaced_by:<신규nid>|<사유>" prefix +
    nodes.supersedes 컬럼(PRAGMA 실측, 실재 시에만 기입 — ALTER 금지).
  - 신규 문장은 저장 게이트 전부 통과 의무(A0 재판정 + PII/secret 재스캔, 우회 금지).

불변: real staging(tmp/real_staging) 접근 0 · confirmed 0 · promotion 0 · in-place 수정 0 ·
      OpenCrab 호출 0 · deploy 0 · 운영 store write 0 · temp 는 tempfile.mkdtemp 만.
CLI: python openbinggu_candidate_replace_ux.py --selftest
     (real staging 적용은 별도 GO 필요 — 본 단계는 temp selftest만)
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import staging_apply, OPERATING_PATHS, _hash, _now_iso  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3, deprecate_item, active_view  # noqa: E402
from openbinggu_candidate_list_view import list_candidates, node_id8, T1  # noqa: E402
from openbinggu_conversation_candidate_save import save_selected, _sent_hash  # noqa: E402
from openbinggu_conversation_capture_preview import _PREVIEW_PII_EXTRA  # noqa: E402
from watcher_batch_m1 import scan_residual_pii  # noqa: E402
import openbinggu_label_kind_map as lkmap  # noqa: E402
import openbinggu_a0_node_dryrun as a0  # noqa: E402
import openbinggu_incoming_to_staging as v011  # noqa: E402  (SECRET_PATTERNS)


# 정본 위임: 저장 단위 상한 = capture_preview.MAX_NODE_SENTENCE(1000). 2026-06-15 owner 결정
# "저장 단위 = 문장 전체(80자 발췌 cut 폐기)"가 save/preview 에 반영될 때 replace 경로만 80 에
# 남아 있었다(2026-07-30 실측 — 오절단 원문 복원 replace 가 sentence_too_long_max80 로 차단됨).
# 초과는 silent 절단 아닌 BLOCK(불변). import 실패 시에도 정본과 같은 값으로 폴백(drift 방지 명시).
try:
    from openbinggu_conversation_capture_preview import MAX_NODE_SENTENCE as MAX_SENTENCE
except Exception:
    MAX_SENTENCE = 1000  # 정본: openbinggu_conversation_capture_preview.MAX_NODE_SENTENCE


def _canonical_hash(s):
    """같은 오판 재생성 차단 기준 — 적대 검증 결함 1 반영:
    NFC 정규화(한글 IME/복붙 NFD 변형) + 공백 연속 정규화 + format 문자(zero-width 등 Cf) 제거
    + strip + casefold 후 sha256. 우회 입력(U+200B 삽입·NFD·대소문자)이 동일 hash 로 수렴한다."""
    s = unicodedata.normalize("NFC", s or "")
    s = re.sub(r"\s+", " ", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    return hashlib.sha256(s.strip().casefold().encode("utf-8")).hexdigest()


def _journal_path(snap_dir, before):
    return os.path.join(snap_dir, "journal_replace_" + _hash(before) + ".json")


def pending_replace_journals(snap_dir):
    """잔존 journal 마커 목록 (L-2) — 묶음(기각+신규+audit) 중 crash 시 마커가 남아
    "기각만 되고 신규 미생성" 상태를 다음 실행이 감지·복구 안내할 수 있게 한다."""
    if not os.path.isdir(snap_dir):
        return []
    return sorted(os.path.join(snap_dir, f) for f in os.listdir(snap_dir)
                  if f.startswith("journal_replace_") and f.endswith(".json"))


def recover_pending_replace(db, journal_path):
    """잔존 journal 복구 — 묶음 시작 직전 스냅샷으로 파일 원복(부분쓰기 제거) + audit + 마커 제거."""
    with open(journal_path, "r", encoding="utf-8") as f:
        j = json.load(f)
    snap = j["snapshot"]
    if not os.path.exists(snap):
        return {"recovered": False, "reason": "snapshot_missing"}
    db.con.close()
    for ext in ("-wal", "-shm"):
        p = db.path + ext
        if os.path.exists(p):
            os.remove(p)
    shutil.copy2(snap, db.path)
    db.con = sqlite3.connect(db.path)
    db.con.execute("PRAGMA journal_mode=WAL")
    db.con.execute("PRAGMA busy_timeout=5000")
    db.audit_append(j.get("actor", "human"), "replace_ux",
                    "%s>%s" % (j["old_node_id"], j["new_node_id"]),
                    "BLOCK", "recovered_from_journal", j["before_checksum"], db.store_checksum())
    os.remove(journal_path)
    return {"recovered": True, "snapshot": snap}


def replace_from_list(db, index, node_hash8, new_sentence, reason, ctx, snap_dir,
                      status="all", kind=None):
    """목록 인덱스 1건 교체 = 기각(replaced_by)+신규 candidate 저장 묶음.
    confirm="REPLACE <index> <node_hash8> WITH <new_sentence>" 정확 일치 의무.
    반환 {applied, reason | old_node_id, new_node_id, snapshot, supersedes_written}."""
    # L-2: 잔존 journal = 직전 묶음이 crash 로 중단(기각만 되고 신규 미생성 가능) —
    # DB 상태 불확정이므로 audit append 없이 즉시 차단 + 복구 안내.
    resid = pending_replace_journals(snap_dir)
    if resid:
        return {"applied": False, "reason": "pending_replace_journal", "journals": resid,
                "recovery": "recover_pending_replace(db, journals[0]) — 스냅샷 원복 후 재시도"}
    before = db.store_checksum()

    def block(rc):
        db.audit_append(ctx.get("actor", "human"), "replace_ux", "idx:%s" % index,
                        "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    # ---- preflight (DB 무변 — 전부 통과해야 스냅샷·묶음 진입) ----
    # 1) 사람만 — P1-A TAE-2 hardening: allowlist(== 'human'). 기존 denylist(auto/reader)는
    #    non-'reader' sentinel(agent/system/'unapproved'/대문자)에 fail-OPEN 이었다. human 만 허용.
    if ctx.get("actor") != "human":
        return block("G4_no_auto")
    # 2) confirm 문구 정확 일치 — 인덱스+id8+수정문장 삼중 바인딩 (transaction 밖, 잠금 0)
    if ctx.get("confirm") != "REPLACE %s %s WITH %s" % (index, node_hash8, new_sentence):
        return block("confirm_phrase_mismatch")
    # 3) 사유·수정문장 필수
    if not (reason or "").strip():
        return block("replace_reason_required")
    if not (new_sentence or "").strip():
        return block("new_sentence_required")
    # 3b) 80자 캡 — silent 절단 금지, 초과는 명시 BLOCK (적대 검증 결함 2 반영·발췌 규율 정합)
    if len(new_sentence.strip()) > MAX_SENTENCE:
        return block("sentence_too_long_max80")
    # 4) 목록 재실행 + 범위 + id8 재검증 (stale 목록 오지정 방어 — 기각 UX 패턴)
    rows = list_candidates(db, status, kind)["rows"]
    if not isinstance(index, int) or index < 1 or index > len(rows):
        return block("index_out_of_range")
    row = rows[index - 1]
    if node_id8(row["node_id"]) != node_hash8:
        return block("node_hash_mismatch")
    old_nid = row["node_id"]
    # 5) 대상 active 한정 (deprecated wins — 이미 기각된 노드 재교체 금지)
    old_db = db.con.execute("SELECT state, sentence FROM nodes WHERE node_id=?", (old_nid,)).fetchone()
    if not old_db or old_db[0] != "active":
        return block("target_not_active")
    # 6) 신규 문장 게이트 — 저장(save_selected 3a/3b)과 동일, 우회 금지
    kind_ko, _rule = lkmap.classify_label_kind(new_sentence)
    verdict = a0.classify_node(
        {"id": "pre:" + _sent_hash(new_sentence), "sentence": new_sentence,
         "node_type": lkmap.KO2EN[kind_ko], "evidence_refs": ["pre"]}, status="candidate")
    if verdict["verdict"] == "FAIL":
        return block("a0_fail")
    if verdict["verdict"] == "REVIEW" and not ctx.get("allow_review"):
        return block("a0_review_needs_explicit_allow")
    pii = scan_residual_pii(new_sentence) + [k for k, rx in _PREVIEW_PII_EXTRA if rx.search(new_sentence)]
    if pii or any(p.search(new_sentence) for p in v011.SECRET_PATTERNS):
        return block("pii_or_secret")
    # 7) canonical_hash 비교 — predecessor 동일 = no-op 재생성 / 다른 active 동일 = 중복
    new_ch = _canonical_hash(new_sentence)
    if _canonical_hash(old_db[1]) == new_ch:
        return block("replace_same_content")
    for nid2, sent2 in db.con.execute(
            "SELECT node_id, sentence FROM nodes WHERE state='active' AND node_id<>?", (old_nid,)):
        if _canonical_hash(sent2) == new_ch:
            return block("duplicate_active_content")

    new_nid = "node:CONV:" + _sent_hash(new_sentence)

    # ---- 묶음 전체 lock (이중 실행 감지) — 내부 모듈은 같은 pid 재진입 허용 ----
    with db.write_lock():
        # ---- 묶음 스냅샷 1회 선확보 — StagingDB.snapshot 표준 (sqlite Online Backup API · MF1.1) ----
        # 사본을 원본과 상대 대조(테이블집합·행수·audit_meta·user_version·quick_check)까지 하므로
        # 검증 실패 시 BackupVerifyError 를 던진다 → staging_apply 와 동일한 backup_create_failed
        # BLOCK 계약으로 받는다(D5). 아직 DB 변경 0 지점이라 그대로 반환해도 원복 대상이 없다.
        try:
            snap = db.snapshot(snap_dir, "snap_replace_" + _hash(before))
        except Exception as ex:      # noqa: BLE001
            r = block("backup_create_failed")
            r["backup_error"] = "%s: %s" % (type(ex).__name__, ex)
            return r

        # ---- journal 마커 (L-2) — 묶음 시작 기록. 완료/원복 끝에서 제거, crash 잔존 시 다음 실행 차단.
        # 내부 모듈(deprecate_item·staging_apply·audit_append)이 각자 commit 하므로 외곽 단일
        # BEGIN IMMEDIATE 불가 — 스냅샷+compensation 에 journal 로 crash 구간을 닫는다.
        jpath = _journal_path(snap_dir, before)
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump({"old_node_id": old_nid, "new_node_id": new_nid, "snapshot": snap,
                       "before_checksum": before, "actor": ctx.get("actor", "human")}, f)

        def rollback(rc):
            """파일 원복 — 잔존 WAL/SHM 제거(재적용 방지) 후 스냅샷 copy back + ROLLBACK audit."""
            db.con.close()
            for ext in ("-wal", "-shm"):
                p = db.path + ext
                if os.path.exists(p):
                    os.remove(p)
            shutil.copy2(snap, db.path)
            db.con = sqlite3.connect(db.path)
            db.con.execute("PRAGMA journal_mode=WAL")
            db.con.execute("PRAGMA busy_timeout=5000")
            db.audit_append(ctx.get("actor", "human"), "replace_ux", "%s>%s" % (old_nid, new_nid),
                            "BLOCK", "rolled_back:" + rc, before, db.store_checksum())
            if os.path.exists(jpath):  # 원복 완료 후에만 마커 해제 (중간 crash 시 잔존 → 복구 안내)
                os.remove(jpath)
            return {"applied": False, "reason": "rolled_back:" + rc}

        # ---- (a) 원본 기각 — replaced_by prefix (정방향 링크 = 단일 소스) ----
        rd = deprecate_item(db, "node", old_nid,
                            "replaced_by:%s|%s" % (new_nid, reason[:80]), ctx, snap_dir)
        if not rd.get("applied"):
            return rollback("deprecate:" + str(rd.get("reason")))
        if ctx.get("crash_after_deprecate"):  # selftest 전용 — 프로세스 중단 시뮬(rollback 미수행)
            raise RuntimeError("crash_sim_after_deprecate")

        # ---- (b) 신규 candidate 저장 — save 모듈과 동일 mini-pack 구조 + staging_apply 경유 ----
        # 도장 단일 원천 정합(conversation_candidate_save 와 동일): node_type = 5종 EN 라벨.
        ntype = lkmap.KO2EN[kind_ko]
        eid = "EVC-CONV-" + _sent_hash(new_sentence)
        th = _hash(new_sentence)  # capture 시점 동결 (자기증빙 동어반복 — conv-self prefix 명시)
        pack = {"pack_id": "convr_" + _hash(new_sentence)[:8], "content": new_sentence,
                "nodes": [{"id": new_nid, "type": ntype, "sentence": new_sentence}],
                "edges": [{"id": "edge:CONV:" + _sent_hash(new_sentence),
                           "relation": "evidence_supports", "source": eid, "target": new_nid,
                           "evidence_refs": [eid]}],
                "evidence": [{"id": eid, "sentence": new_sentence,
                              "source_pointer_id": "conv-self:" + _sent_hash(new_sentence),
                              "source_missing": False, "source_hash": th, "captured_hash": th,
                              "redaction_policy": "v1"}]}
        ra = staging_apply(db, pack,
                           {"actor": ctx.get("actor", "human"),
                            **{k: v for k, v in ctx.items()
                               if k in ("backup_fail", "wal_abort", "checksum_mismatch")}},
                           snap_dir)
        if not ra.get("applied"):
            return rollback("staging_apply:" + str(ra.get("reason")))

        # ---- 역링크 + 종결 audit — 보호 영역 안 (적대 검증 관찰 반영: 비원자 tail 제거) ----
        try:
            cols = [c[1] for c in db.con.execute("PRAGMA table_info(nodes)")]
            supersedes_written = False
            if "supersedes" in cols:
                db.con.execute("UPDATE nodes SET supersedes=? WHERE node_id=?", (old_nid, new_nid))
                db.con.commit()
                supersedes_written = True
            # 묶음 종결 audit 1건 — pack_id 필드에 원본>신규 (단일 행 양끝 추적)
            db.audit_append(ctx.get("actor", "human"), "candidate_replace",
                            "%s>%s" % (old_nid, new_nid), "ALLOW", "replaced|" + reason[:60],
                            before, db.store_checksum())
            os.remove(jpath)  # 묶음 완료 — 마커 해제
        except Exception as e:
            return rollback("finalize:" + type(e).__name__)
    return {"applied": True, "old_node_id": old_nid, "new_node_id": new_nid,
            "snapshot": snap, "supersedes_written": supersedes_written}


# ---------------- selftest (temp) ----------------

def _insert_shift_node(db):
    """정렬상 맨 앞에 오는 노드 삽입 — stale 목록 인덱스 시프트 재현용(기각 UX 패턴)."""
    db.con.execute(
        "INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
        "VALUES('node:AAA:shift','judgment','[검증픽스처] 목록 시프트 유발용 판단이다.',1,0,'active','repux_fix',?,?)",
        (_hash("node:AAA:shift"), _now_iso()))
    db.con.commit()


def _idx_of(rows, node_id):
    for i, r in enumerate(rows, 1):
        if r["node_id"] == node_id:
            return i, r["id8"]
    return None, None


def _ctx(index, h8, sent, **kw):
    c = {"actor": "human", "confirm": "REPLACE %s %s WITH %s" % (index, h8, sent)}
    c.update(kw)
    return c


def main_selftest():
    print("=" * 78)
    print("replace transaction — temp selftest (기각+신규 묶음·스냅샷 원복·운영/real 접근 0)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_repux_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-52s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_g3(os.path.join(tmp, "s.sqlite"))

    # 0. 시나리오 — 저장 3건(판단/상태/개념)
    # ★explicit=True 필수: 명시 저장 시나리오다. 자동수집(explicit=False)은 판단/개념문을
    #   candidate 로 뽑지 않아(1인칭 주관문만) saved 0 → 아래 next() 가 StopIteration 으로 죽는다.
    #   형제 selftest(openbinggu_candidate_list_view)는 같은 함정을 이미 피해 뒀는데 여기만 누락됐었다.
    r0 = save_selected(db, T1, [1, 2, 3], {"actor": "human", "confirm": "SAVE 1,2,3"}, snap_dir,
                       explicit=True)
    ck("0_시나리오_구성(후보3건)", r0["applied"] and r0["saved"] == 3)
    rows0 = list_candidates(db)["rows"]
    j_nid = next(r["node_id"] for r in rows0 if r["kind"] == "판단")
    s_nid = next(r["node_id"] for r in rows0 if r["kind"] == "상태")
    c_nid = next(r["node_id"] for r in rows0 if r["kind"] == "개념")
    c_sent_full = db.con.execute("SELECT sentence FROM nodes WHERE node_id=?", (c_nid,)).fetchone()[0]

    # 1. 정상 replace — 판단 노드를 수정 판단으로 교체
    NEW1 = "이 입찰은 마진을 확보했으므로 진행한다."
    i1, h1 = _idx_of(rows0, j_nid)
    r1 = replace_from_list(db, i1, h1, NEW1, "재검토 결과 판단 교체", _ctx(i1, h1, NEW1), snap_dir)
    new_nid = r1.get("new_node_id")
    old_st = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (j_nid,)).fetchone()
    dep = db.con.execute("SELECT reason FROM deprecations WHERE item_id=? AND kind='node'", (j_nid,)).fetchone()
    nrow = db.con.execute("SELECT state,candidate,promotion_allowed,supersedes FROM nodes WHERE node_id=?",
                          (new_nid,)).fetchone() if new_nid else None
    ev = db.con.execute("SELECT count(*) FROM evidence WHERE evidence_id=?",
                        ("EVC-CONV-" + _sent_hash(NEW1),)).fetchone()[0]
    eg = db.con.execute("SELECT count(*) FROM edges WHERE relation='evidence_supports' AND target=?",
                        (new_nid,)).fetchone()[0] if new_nid else 0
    aud1 = db.con.execute("SELECT count(*) FROM audit_log WHERE action='candidate_replace' AND result='ALLOW' "
                          "AND pack_id=?", ("%s>%s" % (j_nid, new_nid),)).fetchone()[0] if new_nid else 0
    act = active_view(db)
    ck("1_정상_replace(기각+신규+역링크+audit)",
       r1["applied"] and old_st == ("deprecated",)
       and dep is not None and dep[0].startswith("replaced_by:%s|" % new_nid)
       and nrow == ("active", 1, 0, j_nid) and r1["supersedes_written"]
       and ev == 1 and eg == 1 and aud1 == 1
       and j_nid not in act["nodes"] and new_nid in act["nodes"])
    ck("1b_성공후_journal_잔존0", pending_replace_journals(snap_dir) == [])

    # 2. stale 목록 오지정 BLOCK — 사용자가 본 목록이 시프트 노드 삽입으로 어긋남
    rows2 = list_candidates(db)["rows"]
    stale_i, stale_h = 2, rows2[1]["id8"]   # 사용자가 본 index 2 의 id8
    _insert_shift_node(db)                  # 'node:AAA:shift' 가 맨 앞 → 전 항목 +1
    NEW2 = "이 절차는 검증을 먼저 통과해야 한다."
    r2 = replace_from_list(db, stale_i, stale_h, NEW2, "사유", _ctx(stale_i, stale_h, NEW2), snap_dir)
    ck("2_stale_목록_오지정_BLOCK(hash_mismatch)",
       (not r2["applied"]) and r2["reason"] == "node_hash_mismatch")

    # 3. predecessor 동일 내용 BLOCK — 공백 변형으로 canonical 동일성 확인
    rows3 = list_candidates(db)["rows"]
    i3, h3 = _idx_of(rows3, s_nid)
    s_sent_full = db.con.execute("SELECT sentence FROM nodes WHERE node_id=?", (s_nid,)).fetchone()[0]
    NEW3 = "  " + s_sent_full.replace(" ", "   ") + "  "   # 공백만 변형 → canonical 동일
    r3 = replace_from_list(db, i3, h3, NEW3, "사유", _ctx(i3, h3, NEW3), snap_dir)
    s_st3 = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (s_nid,)).fetchone()[0]
    ck("3_predecessor_동일내용_BLOCK(canonical)",
       (not r3["applied"]) and r3["reason"] == "replace_same_content" and s_st3 == "active")

    # 3b. 유니코드 변형 우회 BLOCK — zero-width 삽입 + NFD 분해형도 canonical 동일로 수렴 (결함 1 회귀)
    NEW3Z = s_sent_full[:3] + "​" + unicodedata.normalize("NFD", s_sent_full[3:])
    r3z = replace_from_list(db, i3, h3, NEW3Z, "사유", _ctx(i3, h3, NEW3Z), snap_dir)
    ck("3b_유니코드_변형_BLOCK(zerowidth+NFD)",
       (not r3z["applied"]) and r3z["reason"] == "replace_same_content")

    # 4. 다른 active 노드와 동일 내용 BLOCK (+ zero-width 변형도 동일 차단)
    r4 = replace_from_list(db, i3, h3, c_sent_full, "사유", _ctx(i3, h3, c_sent_full), snap_dir)
    c_zw = c_sent_full[:2] + "​" + c_sent_full[2:]
    r4z = replace_from_list(db, i3, h3, c_zw, "사유", _ctx(i3, h3, c_zw), snap_dir)
    ck("4_다른_active_동일내용_BLOCK(+zerowidth)",
       (not r4["applied"]) and r4["reason"] == "duplicate_active_content"
       and (not r4z["applied"]) and r4z["reason"] == "duplicate_active_content")

    # 4b. 상한(MAX_SENTENCE=정본 1000) 초과 BLOCK — silent 절단 없이 명시 거부 + DB 무변 (결함 2 회귀)
    #     2026-07-30: 80 은 6/15 "문장 전체 저장" 개정에서 빠진 잔재 — 정본(MAX_NODE_SENTENCE) 위임.
    NEW4L = "이 판단은 " + "매우 " * 330 + "길어서 문단 덩어리로 판정되어 그대로 저장해서는 안 된다."
    cs4a = db.store_checksum()
    r4l = replace_from_list(db, i3, h3, NEW4L, "사유", _ctx(i3, h3, NEW4L), snap_dir)
    cs4b = db.store_checksum()
    ck("4b_상한초과_BLOCK(silent절단_없음)", len(NEW4L) > MAX_SENTENCE
       and (not r4l["applied"]) and r4l["reason"] == "sentence_too_long_max80" and cs4a == cs4b)

    # 5. confirm 불일치 BLOCK (문장은 같은데 인덱스 갈림)
    NEW5 = "이 항목은 기준 미달이라 기각한다."
    r5 = replace_from_list(db, i3, h3, NEW5, "사유", _ctx(99, h3, NEW5), snap_dir)
    ck("5_confirm_불일치_BLOCK", (not r5["applied"]) and r5["reason"] == "confirm_phrase_mismatch")

    # 6. actor=auto BLOCK (입구 차단 + BLOCK audit)
    a_before = db.con.execute("SELECT count(*) FROM audit_log WHERE action='replace_ux' AND result='BLOCK'").fetchone()[0]
    r6 = replace_from_list(db, i3, h3, NEW5, "사유", _ctx(i3, h3, NEW5, actor="auto"), snap_dir)
    a_after = db.con.execute("SELECT count(*) FROM audit_log WHERE action='replace_ux' AND result='BLOCK'").fetchone()[0]
    ck("6_actor_auto_BLOCK(+audit)", (not r6["applied"]) and r6["reason"] == "G4_no_auto"
       and a_after == a_before + 1)

    # 7. PII 신규 문장 거부 — 전화번호 포함 (deprecate 미발생 = preflight 선검사 증명)
    # 전화번호 fixture 는 조각 결합으로 구성 — 소스 자체가 scanner regex 에 자기검출되지 않게 (6/10 교훈)
    NEW7 = "담당자 번호 010-" + "1234-" + "5678 로 연락해야 한다."
    cs7a = db.store_checksum()
    r7 = replace_from_list(db, i3, h3, NEW7, "사유", _ctx(i3, h3, NEW7), snap_dir)
    cs7b = db.store_checksum()
    s_st7 = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (s_nid,)).fetchone()[0]
    ck("7_PII_신규문장_거부(DB무변)", (not r7["applied"]) and r7["reason"] == "pii_or_secret"
       and s_st7 == "active" and cs7a == cs7b)

    # 8. deprecated 대상 replace BLOCK — 1에서 기각된 노드를 status=all 목록으로 지정
    rows8 = list_candidates(db)["rows"]
    i8, h8 = _idx_of(rows8, j_nid)
    NEW8 = "이 판단은 다시 세워야 한다."
    r8 = replace_from_list(db, i8, h8, NEW8, "사유", _ctx(i8, h8, NEW8), snap_dir)
    ck("8_deprecated_대상_BLOCK(target_not_active)",
       (not r8["applied"]) and r8["reason"] == "target_not_active")

    # 9. 중간 실패 원복 — 신규 저장 단계 checksum_mismatch 주입 → 원본 미기각 + 부분쓰기 0
    NEW9 = "이 검증은 회귀 위험이 있어 보류한다."
    cs9a = db.store_checksum()
    n9a = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    r9 = replace_from_list(db, i3, h3, NEW9, "사유",
                           _ctx(i3, h3, NEW9, checksum_mismatch=True), snap_dir)
    cs9b = db.store_checksum()
    n9b = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    s_st9 = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (s_nid,)).fetchone()[0]
    ghost9 = db.con.execute("SELECT count(*) FROM nodes WHERE node_id=?",
                            ("node:CONV:" + _sent_hash(NEW9),)).fetchone()[0]
    rb_aud = db.con.execute("SELECT count(*) FROM audit_log WHERE action='replace_ux' AND result='BLOCK' "
                            "AND reason_code LIKE 'rolled_back:%'").fetchone()[0]
    ck("9_중간실패_원복(원본active+부분쓰기0+ROLLBACK_audit)",
       (not r9["applied"]) and r9["reason"].startswith("rolled_back:staging_apply:")
       and s_st9 == "active" and cs9a == cs9b and n9a == n9b and ghost9 == 0 and rb_aud == 1)
    ck("9b_원복후_journal_잔존0", pending_replace_journals(snap_dir) == [])

    # 9c. crash 시뮬 (L-2) — deprecate 직후 중단(rollback 미수행) → journal 잔존이
    #     다음 실행을 차단(기각만 되고 신규 미생성 상태 진입 금지) + 복구로 DB 원상태.
    NEW9C = "이 항목은 재공고 이후에 다시 판단한다."
    cs9c = db.store_checksum()
    try:
        replace_from_list(db, i3, h3, NEW9C, "사유",
                          _ctx(i3, h3, NEW9C, crash_after_deprecate=True), snap_dir)
        crashed = False
    except RuntimeError:
        crashed = True
    s_mid = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (s_nid,)).fetchone()[0]
    ghost_mid = db.con.execute("SELECT count(*) FROM nodes WHERE node_id=?",
                               ("node:CONV:" + _sent_hash(NEW9C),)).fetchone()[0]
    resid = pending_replace_journals(snap_dir)
    rblock = replace_from_list(db, i3, h3, NEW9C, "사유", _ctx(i3, h3, NEW9C), snap_dir)
    rec = recover_pending_replace(db, resid[0]) if resid else {"recovered": False}
    cs9d = db.store_checksum()
    s_rec = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (s_nid,)).fetchone()[0]
    rec_aud = db.con.execute("SELECT count(*) FROM audit_log WHERE action='replace_ux' "
                             "AND reason_code='recovered_from_journal'").fetchone()[0]
    ck("9c_crash잔존_차단+복구_원상태(기각만되고_신규미생성_불가)",
       crashed and s_mid == "deprecated" and ghost_mid == 0 and len(resid) == 1
       and (not rblock["applied"]) and rblock["reason"] == "pending_replace_journal"
       and rec["recovered"] and cs9d == cs9c and s_rec == "active" and rec_aud == 1
       and pending_replace_journals(snap_dir) == [])

    # 9d. 80~1000자 정당 문장 통과 — 오절단 원문 복원(2026-07-30 실측 차단 사례)이 이 밴드.
    #     6/15 "문장 전체 저장" 정체성 정합(80 잔재 제거 회귀 못박기). 마지막 mutation 케이스라
    #     i3/h3 픽스처 소모 무방(10~13 은 i3/h3 미사용).
    NEW9D = ("이 판단은 여러 부가 조건과 배경 설명을 모두 포함해 80자를 넘지만 정당한 한 "
             "문장이므로 전체 저장 정체성에 따라 교체가 허용되어야 한다고 판단한다.")
    r9d = replace_from_list(db, i3, h3, NEW9D, "사유", _ctx(i3, h3, NEW9D), snap_dir)
    s9d = db.con.execute("SELECT count(*) FROM nodes WHERE node_id=? AND state='active'",
                         ("node:CONV:" + _sent_hash(NEW9D),)).fetchone()[0]
    ck("9d_80자초과_1000이하_정당문장_통과(전체저장_정체성)",
       80 < len(NEW9D) <= MAX_SENTENCE and r9d["applied"] and s9d == 1)

    # 10. raw 원문(긴 원본 텍스트 전문) 미저장
    blob = "\n".join(str(row) for t in ("nodes", "edges", "evidence", "deprecations", "audit_log")
                     for row in db.con.execute("SELECT * FROM " + t))
    ck("10_raw_원문_미저장(전문_비재현)", T1 not in blob)

    # 11. audit chain INTACT (원복 후 append 포함 전체)
    ck("11_audit_chain_INTACT", db.verify_chain())

    # 12. confirmed 0 · promotion 0 전수
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    ck("12_confirmed0_promotion0_전수", bad == 0)
    db.close()

    # 13. 운영 store 불변 + temp 정리
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    shutil.rmtree(tmp, ignore_errors=True)
    ck("13_운영store_불변+temp_정리", op_before == op_after and not os.path.exists(tmp))

    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  in-place수정=0 confirmed=0 promotion=0 opencrab=0 deploy=0" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(main_selftest())
    print("usage: openbinggu_candidate_replace_ux.py [--selftest]")
    print("real staging 적용은 별도 GO 필요 — 본 단계는 temp selftest만")
    sys.exit(2)
