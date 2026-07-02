# -*- coding: utf-8 -*-
"""OpenBinggu G3 — 기각 도장(deprecated) staging 연동 + 판단 검증 리마인드 (staging 한정).

owner 조건 고정:
  - deprecated = **삭제가 아니라 보존 + 기본조회 제외** (Wikidata rank 차용, 글로벌 조사 후보 1)
  - deprecated_reason 필수 (없으면 차단)
  - 기각된 proposal 재확정 차단 유지 (G2-C 기존 게이트 + deprecated 엣지 물리 잔존 = 재확정 자동 차단)
  - 리마인드 = **자동 승격 0, 사람 검토 유도까지만** (목록 생성이 전부 — 상태 변경 없음)

write = staging SQLite 한정 (StagingDB 운영경로 거부 재사용). confirmed 0 · deploy 0.
CLI: python openbinggu_deprecate_and_remind_g3.py --selftest
"""
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_staging_write_selftest import OPERATING_PATHS, _hash, _now_iso  # 무수정 재사용
from openbinggu_proposal_batch_approval_g2b import open_staging  # edge_proposals 테이블 보장
try:  # 설정값(challenge_threshold) — base 안전벨트/설정 로더 재사용
    from binggu_p1_config import challenge_threshold as _cfg_challenge_threshold
except Exception:  # pragma: no cover — base 부재 시 기본값
    def _cfg_challenge_threshold(home=None):
        return 3
try:  # 🔒 안전벨트 — actor allowlist(==human). 영구금지25 denylist 우회 회피.
    from binggu_p1_config import is_confirm_actor
except Exception:  # pragma: no cover — base 부재 시에도 allowlist(==human)
    def is_confirm_actor(actor):
        return actor == "human"


def _audit_actor(ctx):
    """감사 기록용 실제 actor — 누락/None 도 'human' 으로 위장하지 않는다(거짓 출처 금지)."""
    a = ctx.get("actor")
    return a if a else "unknown"

G3_SCHEMA = """
CREATE TABLE IF NOT EXISTS deprecations(
    item_id TEXT, kind TEXT, reason TEXT, counter_evidence_ref TEXT, ts TEXT,
    PRIMARY KEY(item_id, kind));
CREATE TABLE IF NOT EXISTS judgment_reviews(
    review_id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT, due_date TEXT,
    status TEXT DEFAULT 'pending', outcome TEXT, resolved_reason TEXT, ts TEXT);
-- 철학필터 열린 분류 (keep / challenge / discard) — 닫힌 필터=에코챔버 차단(헌법 §2 line 39~41).
--   keep: 내 가치관과 맞음(우선순위↑) / challenge: 다르지만 근거 있음(보존+주기적 반문) /
--   discard: 무근거(이유 남기고 버림, 물리 보존). 확정은 사람(actor=human). 자동 가치판정 0.
CREATE TABLE IF NOT EXISTS harvest_classifications(
    item_id TEXT NOT NULL,               -- 분류 대상(수확물/노드/판단 id)
    klass TEXT NOT NULL,                 -- keep | challenge | discard (열린 3분류)
    reason TEXT,                         -- 분류 근거(discard 는 필수)
    actor TEXT,                          -- 분류한 사람(human 만)
    ts TEXT,
    PRIMARY KEY(item_id));
"""

OUTCOMES = {"성공", "실패", "불확실", "판정불가", "옳음", "그름"}
# 철학필터 challenge — '옳음' 누적이 철학 재검토 신호의 카운터(judgment_reviews 단일 진실원천)
CHALLENGE_OUTCOME = "옳음"
# 열린 분류 3종 — keep(활용) / challenge(도전 보관·주기 반문) / discard(무근거만, 이유 남김).
#   닫힌 필터(keep/discard 2분류)는 에코챔버 = "고집=무능" 위배 → challenge 가 발전의 핵심.
HARVEST_CLASSES = {"keep", "challenge", "discard"}


def open_g3(path):
    db = open_staging(path)
    db.con.executescript(G3_SCHEMA)
    db.con.commit()
    return db


# ---------------- 기각 도장 (deprecated) ----------------

def deprecate_item(db, kind, item_id, reason, ctx, snap_dir, counter_evidence_ref=None, ts=None):
    """node/edge 1건 deprecated — 보존(물리 잔존) + state 변경 + 사유 기록. 사람만."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "deprecate", item_id, "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    if not is_confirm_actor(ctx.get("actor")):  # allowlist(==human) — auto/agent/None/누락 전부 차단
        return block("G4_no_auto")
    if not (reason or "").strip():
        return block("deprecated_reason_required")
    table, col = ("nodes", "node_id") if kind == "node" else ("edges", "edge_id")
    if kind not in ("node", "edge"):
        return block("kind_invalid")
    row = db.con.execute("SELECT state FROM %s WHERE %s=?" % (table, col), (item_id,)).fetchone()
    if not row:
        return block("item_not_found")
    if row[0] == "deprecated":
        return block("already_deprecated")
    if row[0] == "tombstoned":
        return block("tombstoned_item")

    with db.write_lock():
        snap = db.snapshot(snap_dir, "snap_g3_" + _hash(before))
        db.con.execute("BEGIN")
        db.con.execute("UPDATE %s SET state='deprecated' WHERE %s=?" % (table, col), (item_id,))
        db.con.execute("INSERT INTO deprecations(item_id,kind,reason,counter_evidence_ref,ts) VALUES(?,?,?,?,?)",
                       (item_id, kind, reason[:200], counter_evidence_ref, _now_iso(ts)))
        db.con.execute("COMMIT")
        db.audit_append(_audit_actor(ctx), "deprecate", item_id, "ALLOW", reason[:80],
                        before, db.store_checksum(), ts=ts)
    return {"applied": True, "reason": None, "snapshot": snap}


def active_view(db):
    """기본 소비 view — deprecated/tombstoned 제외 (물리 보존은 그대로)."""
    nodes = [r[0] for r in db.con.execute("SELECT node_id FROM nodes WHERE state='active' ORDER BY node_id")]
    edges = [r[0] for r in db.con.execute("SELECT edge_id FROM edges WHERE state='active' ORDER BY edge_id")]
    return {"nodes": nodes, "edges": edges}


# ---------------- 판단 검증 리마인드 (자동 승격 0 — 목록 생성까지만) ----------------

def set_review_due(db, node_id, due_date, ctx, ts=None):
    """판단 노드에 검증예정일 등록. 사람만 · 노드 active 실재 · pending 중복 차단."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "review_due", node_id, "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    if not is_confirm_actor(ctx.get("actor")):  # allowlist(==human)
        return block("G4_no_auto")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date or ""):
        return block("due_date_invalid")
    row = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (node_id,)).fetchone()
    if not row or row[0] != "active":
        return block("node_not_active")
    if db.con.execute("SELECT 1 FROM judgment_reviews WHERE node_id=? AND status='pending'",
                      (node_id,)).fetchone():
        return block("pending_review_exists")
    with db.write_lock():
        db.con.execute("INSERT INTO judgment_reviews(node_id,due_date,status,ts) VALUES(?,?,'pending',?)",
                       (node_id, due_date, _now_iso(ts)))
        db.con.commit()
        db.audit_append(_audit_actor(ctx), "review_due", node_id, "ALLOW", due_date,
                        before, db.store_checksum(), ts=ts)
    return {"applied": True, "reason": None}


def list_due_reminders(db, today):
    """due 경과 + pending → 사람 검토 유도 목록(마크다운). 상태 변경·승격 0 (read-only)."""
    rows = db.con.execute(
        "SELECT r.node_id, r.due_date, n.sentence FROM judgment_reviews r "
        "JOIN nodes n ON n.node_id=r.node_id "
        "WHERE r.status='pending' AND r.due_date<=? ORDER BY r.due_date", (today,)).fetchall()
    lines = ["# 판단 검증 리마인드 — %s 기준 %d건 (사람 검토 유도, 자동 변경 없음)" % (today, len(rows))]
    for nid, due, sent in rows:
        lines.append("- [ ] (%s 예정) %s — `%s` → 결과 입력: 성공/실패/불확실/판정불가" % (due, (sent or "")[:50], nid))
    return {"count": len(rows), "items": [r[0] for r in rows], "markdown": "\n".join(lines)}


def resolve_review(db, node_id, outcome, reason, ctx, ts=None):
    """사람이 결과 입력. 기록만 — 노드 자체(state·candidate) 무변. 강등 원하면 별도 deprecate(사람 행동)."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "review_resolve", node_id, "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    if not is_confirm_actor(ctx.get("actor")):  # allowlist(==human)
        return block("G4_no_auto")
    if outcome not in OUTCOMES:
        return block("outcome_invalid")
    if not (reason or "").strip():
        return block("resolve_reason_required")
    with db.write_lock():
        cur = db.con.execute(
            "UPDATE judgment_reviews SET status='resolved', outcome=?, resolved_reason=? "
            "WHERE node_id=? AND status='pending'", (outcome, reason[:200], node_id))
        db.con.commit()
        if cur.rowcount == 0:
            return block("no_pending_review")
        db.audit_append(_audit_actor(ctx), "review_resolve", node_id, "ALLOW", outcome,
                        before, db.store_checksum(), ts=ts)
    return {"applied": True, "reason": None}


# ---------------- 철학 재검토 신호 (challenge outcome 누적, read-only) ----------------

def challenge_outcome_count(db, node_id, outcome=CHALLENGE_OUTCOME):
    """node 의 resolved outcome 누적 횟수. judgment_reviews 단일 진실원천(신규 테이블 0)."""
    return db.con.execute(
        "SELECT count(*) FROM judgment_reviews WHERE node_id=? AND status='resolved' AND outcome=?",
        (node_id, outcome)).fetchone()[0]


def philosophy_review_signals(db, threshold=None, home=None):
    """challenge 노드의 '옳음' 누적 ≥ threshold → '철학 재검토?' 신호 목록(read-only).

    threshold 미지정 시 설정값(challenge_threshold, 기본 3). 상태 변경·자동 승격 0.
    judgment_reviews 만 집계 — 신규 카운터 테이블 없음(단일 진실원천).
    """
    n = int(threshold) if threshold is not None else int(_cfg_challenge_threshold(home))
    if n < 1:
        n = 1
    rows = db.con.execute(
        "SELECT r.node_id, count(*) AS c, n.sentence "
        "FROM judgment_reviews r JOIN nodes n ON n.node_id=r.node_id "
        "WHERE r.status='resolved' AND r.outcome=? "
        "GROUP BY r.node_id HAVING c>=? ORDER BY c DESC, r.node_id",
        (CHALLENGE_OUTCOME, n)).fetchall()
    lines = ["# 철학 재검토 신호 — '%s' %d회 이상 누적 %d건 (read-only · 자동 변경 없음)"
             % (CHALLENGE_OUTCOME, n, len(rows))]
    for nid, c, sent in rows:
        lines.append("- [ ] (%s %d회) %s — `%s` → 철학 재검토? (확정은 사람)"
                     % (CHALLENGE_OUTCOME, c, (sent or "")[:50], nid))
    return {"threshold": n, "count": len(rows),
            "items": [{"node_id": r[0], "count": r[1]} for r in rows],
            "markdown": "\n".join(lines)}


# ---------------- 열린 분류 (keep / challenge / discard) — 에코챔버 차단 ----------------

def classify_harvest_item(db, item_id, klass, reason, ctx, ts=None):
    """수확물/판단 1건을 열린 3분류(keep/challenge/discard)로 분류 기록. 사람만(actor=human).

    헌법 §2(line 39~41): 닫힌 필터(맞으면 keep·다르면 discard)는 에코챔버 = 발전 정지.
      열린 분류 = 다르지만 근거 있으면 challenge 로 '보존'(주기적 반문 대상). 무근거만 discard.
    자동 가치 판정 0 — AI 는 분류값을 못 정한다(is_confirm_actor 강제). discard 는 이유 필수.
    물리 삭제 0 — discard 도 분류 기록일 뿐, 노드/엣지는 보존(deprecate 와 동일 철학).
    같은 item_id 재분류는 갱신(사람이 challenge→keep 등 재판단 가능). 분류 자체는 기록만.
    """
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "harvest_classify", item_id, "BLOCK", rc, before, before, ts=ts)
        return {"applied": False, "reason": rc}

    if not is_confirm_actor(ctx.get("actor")):  # allowlist(==human) — AI 자동 가치판정 차단
        return block("G4_no_auto")
    if klass not in HARVEST_CLASSES:
        return block("klass_invalid")
    if klass == "discard" and not (reason or "").strip():
        return block("discard_reason_required")  # 무근거 버림 금지 — 이유 남겨야 함
    if not (item_id or "").strip():
        return block("item_id_required")
    with db.write_lock():
        db.con.execute(
            "INSERT INTO harvest_classifications(item_id,klass,reason,actor,ts) VALUES(?,?,?,?,?) "
            "ON CONFLICT(item_id) DO UPDATE SET klass=excluded.klass, reason=excluded.reason, "
            "actor=excluded.actor, ts=excluded.ts",
            (item_id, klass, (reason or "")[:200], "human", _now_iso(ts)))
        db.con.commit()
        db.audit_append(_audit_actor(ctx), "harvest_classify", item_id, "ALLOW", klass,
                        before, db.store_checksum(), ts=ts)
    return {"applied": True, "reason": None, "klass": klass}


def classification_distribution(db):
    """열린 분류 분포 read-only — keep/challenge/discard 각 건수(에코챔버 진단용)."""
    rows = db.con.execute(
        "SELECT klass, count(*) FROM harvest_classifications GROUP BY klass").fetchall()
    dist = {k: 0 for k in HARVEST_CLASSES}
    for k, c in rows:
        if k in dist:
            dist[k] = c
    dist["total"] = sum(dist[k] for k in HARVEST_CLASSES)
    return dist


def philosophy_diversity_signals(db, min_total=5, challenge_floor=0.10):
    """에코챔버 진단 신호(read-only) — 분류가 한쪽으로 쏠렸는지 경보.

    헌법 §2: 닫힌 필터 = 에코챔버 = 발전 정지. challenge 비율이 바닥(challenge_floor)
      미만이면 "다른 관점을 전부 keep/discard 로만 처리 = 도전 보관 안 함 = 발전 정지 위험"
      신호. 자동 변경 0 — 사람에게 "열린 분류 하고 있나?" 반문만(신호).
    total < min_total 이면 표본 부족(신호 없음 — 섣부른 경보 방지).
    """
    dist = classification_distribution(db)
    total = dist["total"]
    echo_risk = False
    note = ""
    challenge_ratio = (dist["challenge"] / total) if total else 0.0
    if total >= min_total and challenge_ratio < challenge_floor:
        echo_risk = True
        note = ("challenge 비율 %.0f%% < %.0f%% — 다른 관점을 '도전 보관' 안 하고 "
                "keep/discard 로만 처리 = 에코챔버 위험(발전 정지). 열린 분류 점검 권장."
                % (challenge_ratio * 100, challenge_floor * 100))
    lines = ["# 철학 다양성 신호 — keep %d / challenge %d / discard %d (총 %d · read-only)"
             % (dist["keep"], dist["challenge"], dist["discard"], total)]
    if echo_risk:
        lines.append("- [ ] ⚠️ 에코챔버 위험: %s (확정·조정은 사람)" % note)
    elif total < min_total:
        lines.append("- 표본 부족(총 %d < %d) — 신호 보류" % (total, min_total))
    else:
        lines.append("- 분류 다양성 양호(challenge %.0f%% ≥ %.0f%%)"
                     % (challenge_ratio * 100, challenge_floor * 100))
    return {"echo_chamber_risk": echo_risk, "challenge_ratio": round(challenge_ratio, 4),
            "distribution": dist, "note": note, "markdown": "\n".join(lines)}


# ---------------- selftest ----------------

def run():
    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="obg_g3_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    db = open_g3(os.path.join(tmp, "s.sqlite"))
    for nid, s in [("n1", "이 입찰은 보류한다."), ("n2", "마진 확보로 참여한다."), ("n3", "절차가 진행 중이다.")]:
        db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
                       "VALUES(?,?,?,1,0,'active','g3test',?,?)", (nid, "judgment", s, _hash(nid), _now_iso()))
    db.con.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,state,evidence_refs,pack_id,content_hash,created_at) "
                   "VALUES('e1','refines','n2','n1',1,'active','[\"EVC-1\"]','g3test','h',?)", (_now_iso(),))
    db.con.commit()

    # 1. 정상 deprecate — 보존 + 기본 view 제외
    r1 = deprecate_item(db, "node", "n1", "낙찰가 공개로 반증됨", {"actor": "human"}, snap_dir, "EVC-9")
    phys = db.con.execute("SELECT count(*) FROM nodes WHERE node_id='n1'").fetchone()[0]
    view = active_view(db)
    dep = db.con.execute("SELECT reason FROM deprecations WHERE item_id='n1'").fetchone()
    rec(1, "deprecate 보존+기본조회 제외+사유 기록", r1["applied"] and phys == 1
        and "n1" not in view["nodes"] and "n2" in view["nodes"] and dep is not None)

    # 2. 사유 없음 차단
    r2 = deprecate_item(db, "node", "n2", "  ", {"actor": "human"}, snap_dir)
    rec(2, "deprecated_reason 필수", (not r2["applied"]) and r2["reason"] == "deprecated_reason_required")

    # 3. 이중 deprecate 차단
    r3 = deprecate_item(db, "node", "n1", "또 기각", {"actor": "human"}, snap_dir)
    rec(3, "이중 deprecate 차단", (not r3["applied"]) and r3["reason"] == "already_deprecated")

    # 4. auto 차단 + 미존재 차단
    r4a = deprecate_item(db, "node", "n2", "사유", {"actor": "auto"}, snap_dir)
    r4b = deprecate_item(db, "edge", "e_nope", "사유", {"actor": "human"}, snap_dir)
    rec(4, "auto/미존재 차단", (not r4a["applied"]) and r4a["reason"] == "G4_no_auto"
        and (not r4b["applied"]) and r4b["reason"] == "item_not_found")

    # 4b. 비human actor 전수 차단 (allowlist 회귀 — None/agent/system/AUTO/빈/누락/reader)
    bypass_actors = [None, "", "agent", "system", "AUTO", "auto", "reader", "ai", "claude"]
    dep_blocked = all(
        not deprecate_item(db, "node", "n2", "사유", {"actor": a}, snap_dir)["applied"]
        for a in bypass_actors)
    due_blocked = all(
        not set_review_due(db, "n2", "2026-06-10", {"actor": a})["applied"]
        for a in bypass_actors)
    # actor 키 자체 누락도 차단(KeyError/위장 없이 BLOCK)
    nokey_dep = not deprecate_item(db, "node", "n2", "사유", {}, snap_dir)["applied"]
    nokey_due = not set_review_due(db, "n2", "2026-06-10", {})["applied"]
    res_blocked = all(
        not resolve_review(db, "n2", "성공", "x", {"actor": a})["applied"]
        for a in bypass_actors)
    # 감사 위장 차단: 위에서 시도한 비human BLOCK 들이 'human' 으로 기록되지 않음.
    #   actor 누락({}) 은 'unknown', 비human 은 해당 문자열(또는 빈→'unknown') 로 기록.
    forged_audit = db.con.execute(
        "SELECT count(*) FROM audit_log WHERE actor='human' AND result='BLOCK' "
        "AND reason_code='G4_no_auto'").fetchone()[0]
    rec(40, "비human actor(None/agent/system/AUTO/누락) 3경로 전수 BLOCK + 감사 위장 0",
        dep_blocked and due_blocked and res_blocked and nokey_dep and nokey_due and forged_audit == 0)

    # 5. 엣지 deprecate — 보존 + view 제외
    r5 = deprecate_item(db, "edge", "e1", "관계 근거 철회", {"actor": "human"}, snap_dir)
    view5 = active_view(db)
    phys5 = db.con.execute("SELECT count(*) FROM edges WHERE edge_id='e1'").fetchone()[0]
    rec(5, "엣지 deprecate 보존+제외", r5["applied"] and phys5 == 1 and "e1" not in view5["edges"])

    # 6. set_due 정상 + pending 중복 차단
    r6a = set_review_due(db, "n2", "2026-06-10", {"actor": "human"})
    r6b = set_review_due(db, "n2", "2026-07-01", {"actor": "human"})
    rec(6, "검증예정일 등록 + pending 중복 차단", r6a["applied"]
        and (not r6b["applied"]) and r6b["reason"] == "pending_review_exists")

    # 7. due 형식·비active 노드·auto 차단
    r7a = set_review_due(db, "n3", "06/10", {"actor": "human"})
    r7b = set_review_due(db, "n1", "2026-06-10", {"actor": "human"})  # deprecated 노드
    r7c = set_review_due(db, "n3", "2026-06-10", {"actor": "auto"})
    rec(7, "형식/비active/auto 차단", all(not r["applied"] for r in (r7a, r7b, r7c)))

    # 8. 리마인드 — 과거 due 1건 목록, 미래 due 0건, 상태 무변(자동 승격 0)
    before8 = db.store_checksum()
    rem_today = list_due_reminders(db, "2026-06-11")
    rem_past = list_due_reminders(db, "2026-06-09")
    after8 = db.store_checksum()
    rec(8, "리마인드 목록(검토 유도만, 상태 무변)", rem_today["count"] == 1 and "n2" in rem_today["items"]
        and rem_past["count"] == 0 and before8 == after8 and "자동 변경 없음" in rem_today["markdown"])

    # 9. resolve 정상 — 기록만, 노드 무변
    node_before = db.con.execute("SELECT state,candidate FROM nodes WHERE node_id='n2'").fetchone()
    r9 = resolve_review(db, "n2", "실패", "실제 낙찰가가 예상과 달랐음", {"actor": "human"})
    node_after = db.con.execute("SELECT state,candidate FROM nodes WHERE node_id='n2'").fetchone()
    st9 = db.con.execute("SELECT status,outcome FROM judgment_reviews WHERE node_id='n2'").fetchone()
    rec(9, "resolve 기록만(노드 state/candidate 무변)", r9["applied"] and node_before == node_after
        and st9 == ("resolved", "실패"))

    # 10. resolve 가드 — outcome enum·사유 필수·pending 없음·auto
    r10a = resolve_review(db, "n2", "성공", "재차", {"actor": "human"})       # pending 없음
    r10b = resolve_review(db, "n3", "애매", "x", {"actor": "human"})          # enum 외
    rec(10, "resolve 가드 (pending 없음/enum 외)", (not r10a["applied"]) and r10a["reason"] == "no_pending_review"
        and (not r10b["applied"]) and r10b["reason"] == "outcome_invalid")

    # --- 철학필터 challenge — '옳음' 누적 → 철학 재검토 신호 ---
    # challenge 노드 n3 에 사이클 3회: set_review_due → resolve('옳음') 반복(단일 진실원천 누적)
    def challenge_cycle(node_id, due, ts):
        set_review_due(db, node_id, due, {"actor": "human"}, ts=ts)
        return resolve_review(db, node_id, "옳음", "검증 결과 옳았음", {"actor": "human"}, ts=ts)

    cyc_ok = True
    for i, (due, ts) in enumerate([("2026-06-01", "2026-06-02T00:00:00"),
                                   ("2026-06-08", "2026-06-09T00:00:00"),
                                   ("2026-06-15", "2026-06-16T00:00:00")]):
        r = challenge_cycle("n3", due, ts)
        cyc_ok = cyc_ok and r["applied"]

    # 13. '옳음' 누적 카운트 == 3
    cnt = challenge_outcome_count(db, "n3")
    rec(13, "challenge '옳음' 누적 3회 (judgment_reviews 단일원천)", cyc_ok and cnt == 3)

    # 14. threshold=3 → 재검토 신호 뜸 (read-only, 상태 무변)
    before14 = db.store_checksum()
    sig3 = philosophy_review_signals(db, threshold=3)
    after14 = db.store_checksum()
    rec(14, "threshold=3 → 철학 재검토 신호(상태 무변)",
        sig3["count"] == 1 and sig3["items"][0]["node_id"] == "n3"
        and sig3["items"][0]["count"] == 3 and before14 == after14
        and "자동 변경 없음" in sig3["markdown"])

    # 15. threshold=4 → 2/3회 부족, 신호 안 뜸 (override 작동)
    sig4 = philosophy_review_signals(db, threshold=4)
    rec(15, "threshold override(=4) → 신호 안 뜸", sig4["count"] == 0 and sig4["threshold"] == 4)

    # 16. '옳음' 2회만(n2) → threshold=3 신호 미발생 (경계)
    set_review_due(db, "n2", "2026-06-20", {"actor": "human"}, ts="2026-06-21T00:00:00")
    resolve_review(db, "n2", "옳음", "1회", {"actor": "human"}, ts="2026-06-21T00:00:00")
    set_review_due(db, "n2", "2026-06-22", {"actor": "human"}, ts="2026-06-23T00:00:00")
    resolve_review(db, "n2", "옳음", "2회", {"actor": "human"}, ts="2026-06-23T00:00:00")
    sig_n2 = philosophy_review_signals(db, threshold=3)
    rec(16, "'옳음' 2회(n2) → threshold=3 신호 미발생",
        challenge_outcome_count(db, "n2") == 2
        and all(it["node_id"] != "n2" for it in sig_n2["items"]))

    # --- 열린 분류 (keep / challenge / discard) — 에코챔버 차단 ---
    before_cls = db.store_checksum()
    rk = classify_harvest_item(db, "h1", "keep", "내 가치관과 일치", {"actor": "human"}, ts="2026-06-01T00:00:00")
    rc_ = classify_harvest_item(db, "h2", "challenge", "다르지만 근거 있음 — 도전 보관", {"actor": "human"}, ts="2026-06-01T00:00:00")
    rd = classify_harvest_item(db, "h3", "discard", "무근거", {"actor": "human"}, ts="2026-06-01T00:00:00")
    rec(17, "열린 3분류(keep/challenge/discard) 기록 — 사람만",
        rk["applied"] and rc_["applied"] and rd["applied"])

    # discard 이유 필수 / 잘못된 분류값 차단 / auto 차단
    rd_noreason = classify_harvest_item(db, "h4", "discard", "  ", {"actor": "human"})
    r_badklass = classify_harvest_item(db, "h5", "delete", "x", {"actor": "human"})
    cls_auto = all(not classify_harvest_item(db, "h6", "keep", "x", {"actor": a})["applied"]
                   for a in [None, "", "auto", "agent", "system", "ai"])
    cls_nokey = not classify_harvest_item(db, "h6", "keep", "x", {})["applied"]
    rec(18, "discard 이유 필수 / 잘못된 klass 차단 / 비human 자동 가치판정 0",
        (not rd_noreason["applied"]) and rd_noreason["reason"] == "discard_reason_required"
        and (not r_badklass["applied"]) and r_badklass["reason"] == "klass_invalid"
        and cls_auto and cls_nokey)

    # 재분류(challenge→keep) 갱신 — 같은 item_id 덮어쓰기
    classify_harvest_item(db, "h2", "keep", "재판단: 결국 맞았음", {"actor": "human"}, ts="2026-06-02T00:00:00")
    k_h2 = db.con.execute("SELECT klass FROM harvest_classifications WHERE item_id='h2'").fetchone()[0]
    rec(19, "재분류(challenge→keep) 갱신 — item_id 단일 유지", k_h2 == "keep")

    # 다양성 신호: 표본 부족 → 신호 보류
    div_small = philosophy_diversity_signals(db, min_total=5)
    # h2 가 keep 됐으니 현재 keep=2, challenge=0, discard=1 (총 3 < 5) → 표본 부족
    rec(20, "철학 다양성 신호 표본 부족(총<min_total) → 신호 보류",
        (not div_small["echo_chamber_risk"]) and "표본 부족" in div_small["markdown"])

    # 에코챔버 위험: keep 만 잔뜩 → challenge 0% → 위험 신호
    for i in range(5):
        classify_harvest_item(db, "echo%d" % i, "keep", "전부 keep", {"actor": "human"},
                              ts="2026-06-03T0%d:00:00" % i)
    div_echo = philosophy_diversity_signals(db, min_total=5, challenge_floor=0.10)
    rec(21, "challenge 0% (전부 keep/discard) → 에코챔버 위험 신호(자동 변경 0)",
        div_echo["echo_chamber_risk"] and div_echo["challenge_ratio"] == 0.0
        and "에코챔버 위험" in div_echo["markdown"])

    # challenge 충분 → 다양성 양호 (분류 자체는 read-only 신호, 상태 무변)
    for i in range(3):
        classify_harvest_item(db, "ch%d" % i, "challenge", "도전 보관", {"actor": "human"},
                              ts="2026-06-04T0%d:00:00" % i)
    div_ok = philosophy_diversity_signals(db, min_total=5, challenge_floor=0.10)
    after_cls = db.store_checksum()  # 신호 자체(diversity_signals)는 read-only
    div_ok2 = philosophy_diversity_signals(db, min_total=5, challenge_floor=0.10)
    after_cls2 = db.store_checksum()
    rec(22, "challenge 비율 충분 → 다양성 양호 + 신호 read-only(상태 무변)",
        (not div_ok["echo_chamber_risk"]) and div_ok["challenge_ratio"] >= 0.10
        and after_cls == after_cls2 and "다양성 양호" in div_ok2["markdown"])

    # 11. audit chain
    intact = db.verify_chain()
    db.con.execute("UPDATE audit_log SET action='TAMPER' WHERE seq=(SELECT min(seq) FROM audit_log)")
    db.con.commit()
    rec(11, "audit chain intact→변조 BROKEN", intact and (not db.verify_chain()))

    # 12. confirmed 0 · promotion 0 전수
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    rec(12, "confirmed 0 · promotion 0 전수", bad == 0)
    db.close()

    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    unchanged = before_mtime == after_mtime
    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 74)
    print("OpenBinggu G3 — deprecated 도장 + 판단 검증 리마인드 selftest (temp, 운영 write 0)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=%s  auto_promotion=0  confirmed=0  deploy=0" % unchanged)
    gate = "GO" if (npass == len(results) and unchanged) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(run())
