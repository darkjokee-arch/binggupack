# -*- coding: utf-8 -*-
"""outcome_attribution.py — Recall→Outcome Attribution v0.1 (회상→작업결과 귀속).

목적(owner 확정 스펙): 회상된 기억(recall_trace)이 실제 작업에 '적용됐고'(application) 그
작업 '결과가 어땠는지'(result)를 관찰 telemetry 로 기록한다. use_count 0(잠김)·verdict(회수측
적중률)로는 닫히지 않던 폐루프의 한 조각 — 측정 축을 '이 기억이 맞았나'(회수측)에서 '기억이
행동을 바꿔 결과를 개선했나'(결과-귀속)로 옮긴다.

차원 구분(중복 아님):
  - recall_trace.recall_outcomes : 회상 자체의 효용(used/ignored/corrected). 다른 축.
  - hit_stats / verdict          : 사람 판단 적중률(owner/ai 가 맞았나). 다른 축.
  - 이 모듈 recall_run_outcomes  : 적용 여부(application) + 작업 결과(result). 결과-귀속 축.

설계 제약(owner 스펙 · 헌법 §1·§6 정합):
  - 새 엔진/DB 0 — 기존 recall_trace store(<home>/recall_trace.sqlite)에 테이블 하나만 추가.
    운영 ledger.sqlite(nodes/edges/기억문장/use_count/hit_events) 절대 미접촉(합격기준6).
  - append-only — 삭제/UPDATE 0. 정정(overturn)도 원본 보존 + reversal 행 append(합격기준7).
  - evidence-gated 자동 append — evidence_digest 없으면 거부(fail-closed). 정본 기억·정책 승격이
    아니라 '관찰 로그'라 SAVE 승인 불요(헌법 §6 '안전=영수증 자동'). record_verdict 선례(3중 방어:
    evidence 필수 + 신뢰등급 라벨 + overturn)를 본뜨되 store 는 ledger 아닌 sibling(더 안전).
  - 신뢰등급 라벨 — trust_tier='ai_observation'(자동 관찰) literal. actor='human' 위장 0(§8-1-⑥).
  - 인과 단정 금지 — memory_improved_result 류 컬럼 스키마 원천 배제. application·result 두 관찰
    사실만. 집계는 signal_only(랭킹/use_count/golden 자동수정 진입 0). 상관은 나중에 사람이.
  - overturn 은 사람만 — CLI(binggu outcome --overturn N) 전용. 자동 경로 없음(owner 게이트).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

# recall_trace 와 동일한 bare-name dep 해소(binggu_platform 등) — scripts/ 를 sys.path 에.
HERE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import binggu_platform as _plat  # binggu_home(BINGGU_HOME 존중 · 격리 폴백)  # noqa: E402
from binggupack.pack import recall_trace as RT  # store 헬퍼(_open_store/trace_store_path) 공유  # noqa: E402

# application: 회상 기억이 작업에 어떻게 반영됐나 (관찰 사실).
VALID_APPLICATION = ("applied", "ignored", "corrected")
# result: 그 작업의 결과 (관찰 사실 — 인과 단정 아님).
VALID_RESULT = ("success", "failure", "mixed", "unknown")
# evidence_kind: 결과 증거의 유형 화이트리스트(스펙 §사용자표면). 원문은 저장 안 함(digest만).
VALID_EVIDENCE_KIND = ("pytest", "ci", "file", "user")

TRUST_AUTO = "ai_observation"      # evidence-gated 자동 관찰 record
TRUST_OVERTURN = "owner_overturn"  # 사람 1-발화 정정 reversal

# signal_only 라벨 — 집계 반환이 랭킹/use_count/golden 자동수정 입력으로 못 쓰이게 명시(헌법·스펙 §3).
_SIGNAL_NOTE = ("이 수치는 표시 신호일 뿐 — 랭킹/use_count/정책 자동수정 근거 아님(인과 단정 0). "
                "적용·결과 두 관찰 사실만이며 상관은 나중에 사람이 표본으로 본다.")


# ---------------- 결정적 id ----------------

def _outcome_id(trace_id, evidence_digest, supersedes):
    """결정적 outcome_id — 원본(supersedes='')은 (trace,digest)로 고정 → dup 과 일관.
    reversal(supersedes=원본oid)은 원본과 다른 id."""
    raw = "%s|%s|%s" % (trace_id, evidence_digest, supersedes)
    return "rro-" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


# ---------------- 기록: evidence-gated 자동 관찰 append ----------------

def record_run_outcome(trace_id, applied_node_ids, application, result,
                       evidence_kind, evidence_digest, ts, *, home=None):
    """회상 trace 1건의 결과-귀속 관찰을 append(evidence-gated 자동 · SAVE 불요).

    trace_id       : recall_trace 가 발급한 회상 trace(존재해야 함 · 합격기준3).
    applied_node_ids: 실제 적용/참조한 node_id 목록 — 그 trace 가 회상한 노드의 부분집합만(합격기준2).
    application    : applied | ignored | corrected.
    result         : success | failure | mixed | unknown.
    evidence_kind  : pytest | ci | file | user (화이트리스트).
    evidence_digest: 증거의 digest(sha256 등) — 없으면 거부(fail-closed). 원문은 저장 안 함.
    반환 {recorded, reason?, outcome_id?, trust_tier?}."""
    application = (application or "").strip().lower()
    result = (result or "").strip().lower()
    evidence_kind = (evidence_kind or "").strip().lower()
    evidence_digest = (evidence_digest or "").strip()
    if application not in VALID_APPLICATION:
        return {"recorded": False, "reason": "invalid_application"}
    if result not in VALID_RESULT:
        return {"recorded": False, "reason": "invalid_result"}
    if evidence_kind not in VALID_EVIDENCE_KIND:
        return {"recorded": False, "reason": "invalid_evidence_kind"}
    if not evidence_digest:
        return {"recorded": False, "reason": "evidence_required"}  # fail-closed(합격기준4)
    ts = RT._ts_iso(ts)  # epoch 호출자 방어 — (ts, outcome_id) 순번(overturn N) 오염 차단
    applied = list(dict.fromkeys(applied_node_ids or []))  # 순서보존 dedup
    con = RT._open_store(home)  # sibling store(recall_trace.sqlite) — ledger 미접촉
    try:
        row = con.execute("SELECT recalled_json FROM recall_traces WHERE trace_id=?",
                          (trace_id,)).fetchone()
        if not row:
            return {"recorded": False, "reason": "trace_not_found"}  # 합격기준3
        try:
            recalled = json.loads(row[0]) if row[0] else []
        except Exception:
            recalled = []
        recalled_ids = {n.get("node_id") for n in recalled if isinstance(n, dict) and n.get("node_id")}
        bad = [nid for nid in applied if nid not in recalled_ids]
        if bad:
            return {"recorded": False, "reason": "node_not_in_trace", "bad": bad}  # 합격기준2
        # dup(합격기준5): 같은 (trace, 증거) 원본 1건만
        if con.execute("SELECT 1 FROM recall_run_outcomes WHERE trace_id=? AND evidence_digest=?"
                       " AND supersedes=''", (trace_id, evidence_digest)).fetchone():
            return {"recorded": False, "reason": "dup_outcome"}
        oid = _outcome_id(trace_id, evidence_digest, "")
        con.execute(
            "INSERT INTO recall_run_outcomes(outcome_id,trace_id,applied_node_ids_json,"
            "application,result,evidence_digest,evidence_kind,trust_tier,supersedes,ts)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (oid, trace_id, json.dumps(applied, ensure_ascii=False, sort_keys=True),
             application, result, evidence_digest, evidence_kind, TRUST_AUTO, "", ts))
        con.commit()
    finally:
        con.close()
    return {"recorded": True, "outcome_id": oid, "trust_tier": TRUST_AUTO,
            "application": application, "result": result}


# ---------------- 조회 / 정정 ----------------

def list_run_outcomes(home=None, limit=10):
    """최근 결과-귀속 목록(원본 행만 · reversal 은 overturned 플래그로 반영). seq=1-base 안정 순번.

    삭제가 없으므로 (ts, outcome_id) 정렬의 원본 순번은 새 기록에도 앞번호 불변(overturn N 안전)."""
    if not os.path.exists(RT.trace_store_path(home)):
        return []
    con = RT._open_store(home)
    try:
        rows = con.execute(
            "SELECT outcome_id,trace_id,applied_node_ids_json,application,result,"
            "evidence_digest,evidence_kind,trust_tier,supersedes,ts FROM recall_run_outcomes"
            " ORDER BY ts, outcome_id").fetchall()
    finally:
        con.close()
    superseded = {r[8] for r in rows if r[8]}  # reversal 이 가리키는 원본 oid = overturned
    out = []
    seq = 0
    for r in rows:
        if r[8]:  # reversal 행은 목록에서 스킵(원본에 overturned 로 표시)
            continue
        seq += 1
        try:
            nodes = json.loads(r[2]) if r[2] else []
        except Exception:
            nodes = []
        out.append({"seq": seq, "outcome_id": r[0], "trace_id": r[1],
                    "applied_node_ids": nodes, "application": r[3], "result": r[4],
                    "evidence_digest": r[5], "evidence_kind": r[6],
                    "trust_tier": r[7], "ts": r[9], "overturned": r[0] in superseded})
    return out[-limit:] if limit else out


def overturn_run_outcome(seq, ts, home=None):
    """owner 1-발화 정정(binggu outcome --overturn N) — 원본 보존 + reversal 행 append(합격기준7).

    삭제/UPDATE 0. 이미 정정된 원본은 재정정 거부(already_overturned)."""
    ts = RT._ts_iso(ts)  # epoch 호출자 방어 — record_run_outcome 와 동일 규약
    rows = list_run_outcomes(home, limit=0)
    hit = next((r for r in rows if r["seq"] == seq), None)
    if not hit:
        return {"overturned": False, "reason": "seq_out_of_range", "count": len(rows)}
    if hit["overturned"]:
        return {"overturned": False, "reason": "already_overturned"}
    oid = hit["outcome_id"]
    rev_id = _outcome_id(hit["trace_id"], hit["evidence_digest"], oid)
    con = RT._open_store(home)
    try:
        con.execute(
            "INSERT INTO recall_run_outcomes(outcome_id,trace_id,applied_node_ids_json,"
            "application,result,evidence_digest,evidence_kind,trust_tier,supersedes,ts)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (rev_id, hit["trace_id"],
             json.dumps(hit["applied_node_ids"], ensure_ascii=False, sort_keys=True),
             "overturned", "unknown", hit["evidence_digest"], "overturn",
             TRUST_OVERTURN, oid, ts))
        con.commit()
    finally:
        con.close()
    return {"overturned": True, "seq": seq, "outcome_id": oid, "reversal_id": rev_id}


# ---------------- 집계 (signal_only — 랭킹/자동수정 진입 0) ----------------

def aggregate_run_outcomes(home=None):
    """결과-귀속 집계(합격기준8) — 전부 signal_only. overturned 원본은 유효 집계 제외.

    반환 overall: traces(회상 trace 수) · outcomes(유효 결과 수) · applied(적용 trace 수) ·
      applied_success/applied_failure/applied_mixed · ignored · corrected · overturned ·
      pending_traces(결과 미연결 회상 trace 수 = 미결)."""
    empty = {"traces": 0, "outcomes": 0, "applied": 0, "applied_success": 0,
             "applied_failure": 0, "applied_mixed": 0, "ignored": 0, "corrected": 0,
             "overturned": 0, "pending_traces": 0}
    if not os.path.exists(RT.trace_store_path(home)):
        return {"overall": empty, "signal_only": True, "note": _SIGNAL_NOTE}
    con = RT._open_store_ro(home)  # read-only 집계 — apply_schema/makedirs 미경유(sibling store write 0)
    try:
        n_traces = con.execute("SELECT COUNT(*) FROM recall_traces").fetchone()[0]
        rows = con.execute("SELECT outcome_id,trace_id,application,result,supersedes"
                           " FROM recall_run_outcomes").fetchall()
    finally:
        con.close()
    superseded = {r[4] for r in rows if r[4]}  # overturned 원본 oid
    o = dict(empty)
    o["traces"] = n_traces
    linked = set()
    for oid, tid, application, result, supersedes in rows:
        if supersedes:            # reversal 행 → 원본 1건 overturned 로 카운트
            o["overturned"] += 1
            continue
        if oid in superseded:     # 정정된 원본 → 유효 집계 제외
            continue
        o["outcomes"] += 1
        linked.add(tid)
        if application == "applied":
            o["applied"] += 1
            if result == "success":
                o["applied_success"] += 1
            elif result == "failure":
                o["applied_failure"] += 1
            elif result == "mixed":
                o["applied_mixed"] += 1
        elif application == "ignored":
            o["ignored"] += 1
        elif application == "corrected":
            o["corrected"] += 1
    o["pending_traces"] = max(0, n_traces - len(linked))
    return {"overall": o, "signal_only": True, "note": _SIGNAL_NOTE}


# ---------------- trace_id staging (MF2: preflight 가 버리던 trace_id 보존) ----------------

def last_trace_path(home=None):
    """직전 회상 trace 포인터 경로 — <home>/last_trace.json. AI 자동 outcome record 시 --trace 기본."""
    base = home or _plat.binggu_home()
    return os.path.join(base, "last_trace.json")


def stage_last_trace(trace_id, node_ids, kind, ts, *, home=None):
    """직전 trace_id + 회상 node_ids 를 staging(원문 0 · node_id 는 식별자일 뿐)."""
    if not trace_id:
        return None
    p = last_trace_path(home)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"trace_id": trace_id, "node_ids": list(node_ids or []),
                   "kind": kind, "ts": ts}, f, ensure_ascii=False)
    os.replace(tmp, p)
    return p


def last_staged_trace(home=None):
    p = last_trace_path(home)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------- selftest (temp home · 운영 미접촉 · write 0) ----------------

def _selftest():
    import tempfile
    import shutil

    sys.path.insert(0, HERE)
    from openbinggu_staging_write_selftest import OPERATING_PATHS
    import binggu_p1_config as CFG

    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_outcome_")
    TS = "2026-07-18T00:00:00Z"
    try:
        home = os.path.join(tmp, ".binggupack")
        os.makedirs(home, exist_ok=True)
        # 운영 ledger 미접촉 sentinel
        ledger = os.path.join(home, "ledger.sqlite")
        with open(ledger, "wb") as f:
            f.write(b"LEDGER-SENTINEL")
        ledger_mt0 = os.path.getmtime(ledger)

        # trace opt-in ON + 회상 trace 1건 생성(원문 claim 포함 — scrub 되어야)
        CFG.save_user_config({"recall_config": {"trace_enabled": True}}, home=home)
        SECRET_CLAIM = "결과원문비밀SHOULDNOTPERSIST"
        recalled = [
            {"node_id": "node:CONV:aa01", "claim": SECRET_CLAIM, "semantic_subtype": "교훈",
             "rank_score": 0.9, "relevance": 0.8},
            {"node_id": "node:CONV:bb02", "claim": "또다른원문", "semantic_subtype": "버그패턴",
             "rank_score": 0.7, "relevance": 0.6},
        ]
        res_pf = {"remember": recalled, "risk_level": "높음", "needs_question": False}
        rt = RT.trace_from_preflight("배포 점검 " + SECRET_CLAIM, res_pf, TS, domain="proj", home=home)
        tid = rt["trace_id"]
        ck(rt["recorded"] and "node_ids" in rt and rt["node_ids"] == ["node:CONV:aa01", "node:CONV:bb02"],
           "record_trace 가 node_ids 반환(staging 재사용)")

        # 합격기준4: evidence 없으면 거부(fail-closed)
        r0 = record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "pytest", "", TS, home=home)
        ck(not r0["recorded"] and r0["reason"] == "evidence_required", "합격기준4: evidence 없으면 거부")

        # enum 화이트리스트 거부
        ck(record_run_outcome(tid, ["node:CONV:aa01"], "zzz", "success", "pytest", "d1", TS, home=home)["reason"]
           == "invalid_application", "invalid application 거부")
        ck(record_run_outcome(tid, ["node:CONV:aa01"], "applied", "great", "pytest", "d1", TS, home=home)["reason"]
           == "invalid_result", "invalid result 거부")
        ck(record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "freeform", "d1", TS, home=home)["reason"]
           == "invalid_evidence_kind", "invalid evidence_kind 거부(화이트리스트)")

        # 합격기준3: trace 없으면 거부
        ck(record_run_outcome("rtr-deadbeefdeadbeef", ["node:CONV:aa01"], "applied", "success",
                              "pytest", "d1", TS, home=home)["reason"] == "trace_not_found",
           "합격기준3: dangling trace → 거부")

        # 합격기준2: 회상 안 된 node → 거부
        ck(record_run_outcome(tid, ["node:CONV:zz99"], "applied", "success", "pytest", "d1", TS, home=home)["reason"]
           == "node_not_in_trace", "합격기준2: 회상 안 된 node → 거부")

        # 정상 기록(applied/success/pytest)
        r1 = record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "pytest",
                                "sha-abc123", TS, home=home)
        ck(r1["recorded"] and r1["trust_tier"] == "ai_observation",
           "정상 기록 + trust_tier=ai_observation(자동 관찰 라벨)")

        # 합격기준5: 같은 (trace, 증거) 반복 → dup 1회로 끝
        r_dup = record_run_outcome(tid, ["node:CONV:aa01"], "applied", "failure", "pytest",
                                   "sha-abc123", TS, home=home)
        ck(not r_dup["recorded"] and r_dup["reason"] == "dup_outcome", "합격기준5: 같은 증거 반복 → dup")

        # PII 0: store 바이트에 회상 claim/query 원문 없어야
        with open(RT.trace_store_path(home), "rb") as f:
            blob = f.read()
        ck(SECRET_CLAIM.encode("utf-8") not in blob and "배포".encode("utf-8") not in blob,
           "PII 0: 회상 원문/query 가 store 에 미저장(scrub)")

        # 합격기준6: 운영 ledger sentinel 미접촉(별도 store)
        ck(os.path.exists(ledger) and os.path.getmtime(ledger) == ledger_mt0,
           "합격기준6: 운영 ledger sentinel 미접촉(node/edge/기억 불변)")

        # 집계(합격기준8) — 두 번째 trace 로 ignored 도 추가
        rt2 = RT.record_trace("다른 작업", "why_search",
                              [{"node_id": "node:CONV:cc03", "semantic_subtype": "결정",
                                "rank_score": 0.5, "relevance": 0.4}], TS, home=home)
        record_run_outcome(rt2["trace_id"], ["node:CONV:cc03"], "ignored", "unknown", "user",
                           "sha-ign1", TS, home=home)
        # 세 번째 trace 는 결과 미연결(pending)
        RT.record_trace("미결 작업", "why_search",
                        [{"node_id": "node:CONV:dd04", "semantic_subtype": "교훈",
                          "rank_score": 0.5, "relevance": 0.4}], TS, home=home)
        agg = aggregate_run_outcomes(home=home)["overall"]
        ck(agg["traces"] == 3 and agg["applied"] == 1 and agg["applied_success"] == 1
           and agg["ignored"] == 1 and agg["pending_traces"] == 1,
           "합격기준8: 집계(traces3·applied1·success1·ignored1·pending1)")

        # 합격기준7: overturn → 원본 보존 + reversal append(삭제 0), 재정정 거부
        lst = list_run_outcomes(home, limit=0)
        n_before = len(lst)
        # applied/success 원본을 내용으로 겨냥(seq 정렬은 outcome_id 해시순이라 위치 비결정적)
        target = next(r["seq"] for r in lst if r["application"] == "applied" and r["result"] == "success")
        con = RT._open_store(home)
        try:
            rows_pre = con.execute("SELECT COUNT(*) FROM recall_run_outcomes").fetchone()[0]
        finally:
            con.close()
        ov = overturn_run_outcome(target, TS, home=home)
        ck(ov["overturned"], "합격기준7: overturn 성공")
        con = RT._open_store(home)
        try:
            rows_post = con.execute("SELECT COUNT(*) FROM recall_run_outcomes").fetchone()[0]
            orig_still = con.execute("SELECT 1 FROM recall_run_outcomes WHERE outcome_id=?",
                                     (ov["outcome_id"],)).fetchone()
        finally:
            con.close()
        ck(orig_still is not None and rows_post == rows_pre + 1,
           "합격기준7: 원본 삭제 0·reversal 1행만 append(rows_pre+1)")
        lst2 = list_run_outcomes(home, limit=0)
        ck(len(lst2) == n_before and any(r["overturned"] for r in lst2),
           "합격기준7: 목록에 overturned 이력 반영(원본 유지)")
        ck(overturn_run_outcome(target, TS, home=home)["reason"] == "already_overturned",
           "재정정 거부(already_overturned)")
        agg2 = aggregate_run_outcomes(home=home)["overall"]
        ck(agg2["overturned"] == 1 and agg2["applied_success"] == 0,
           "overturned 원본은 유효 집계 제외(applied_success 0 으로 감산)")

        # signal_only 라벨
        ck(aggregate_run_outcomes(home=home)["signal_only"] is True, "집계 signal_only=True(랭킹 진입 0)")

        # ── readonly 집계: store mtime 불변(mode=ro · apply_schema/makedirs 미경유 · write 0) ──
        sp_ro = RT.trace_store_path(home)
        mt_ro = os.path.getmtime(sp_ro)
        aggregate_run_outcomes(home=home)
        ck(os.path.getmtime(sp_ro) == mt_ro,
           "readonly 집계: aggregate_run_outcomes 후 store mtime 불변(mode=ro · write 0)")

        # staging: trace_id 보존 라운드트립
        stage_last_trace(tid, ["node:CONV:aa01", "node:CONV:bb02"], "preflight", TS, home=home)
        st = last_staged_trace(home=home)
        ck(st and st["trace_id"] == tid and st["node_ids"] == ["node:CONV:aa01", "node:CONV:bb02"],
           "staging: last_trace 라운드트립(preflight 가 버리던 trace_id 보존)")

        # 빈 store graceful
        agg_empty = aggregate_run_outcomes(home=os.path.join(tmp, ".empty"))
        ck(agg_empty["overall"]["traces"] == 0 and agg_empty["overall"]["applied"] == 0,
           "빈 store → 집계 0(에러 0)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "운영 store 불변(OPERATING_PATHS mtime 전후 동일)")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    if sys.argv[1] == "--aggregate":
        print(json.dumps(aggregate_run_outcomes(), ensure_ascii=False, indent=2))
        sys.exit(0)
    print("usage: outcome_attribution.py [--selftest | --aggregate]")
    sys.exit(2)
