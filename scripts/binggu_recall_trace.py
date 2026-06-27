# -*- coding: utf-8 -*-
"""binggu_recall_trace.py — 회상 효용 trace (Phase 2: real handoff trace usefulness).

목적: 실제 세션에서 recall/preflight 가 회상한 노드가 실제로 쓰였는지(used) ·
      무시됐는지(ignored) · 틀려서 교정됐는지(corrected) 를 기록·집계해
      offline golden set(recall_golden.json) 보정 신호로 쓴다.

Phase 1(binggu_recall.py + recall_consistency_harness.py)은 합성 corpus 의 정적 정확도를
잰다. Phase 2 는 그 정확도가 "현실에서 쓸만한가"를 사후 라벨로 검증한다(둘은 보완 관계).

설계 제약(사장님 명시 · 헌법 정합):
  - 원문/PII 저장 금지 — query 는 sha256[:16] 만, 회상 노드는 node_id/category/rank/relevance 만.
    claim·sentence 원문은 store 에 한 바이트도 들어가지 않는다(selftest 가 바이트 검증).
  - opt-in 기록 — recall_config["trace_enabled"](기본 False) 또는 env BINGGU_RECALL_TRACE=1
    이 켜졌을 때만 record_trace 가 write. 안 켜면 no-op(status='disabled', write 0).
  - 사람 판정 게이트 — record_outcome(used/ignored/corrected)은 actor=human 만(헌법 §1 안전벨트).
  - 운영 ledger 불변 — trace 는 별도 store(<home>/recall_trace.sqlite). ledger.sqlite 미접촉.
  - 자동결정 0 — 집계(golden_drift_candidates)는 signal_only. golden set 을 자동수정하지 않고
    "사람이 재검토할 후보"만 표시한다(Phase 1 교훈: 사람 승인 SSOT).

차원 구분(중복 아님):
  - binggu_hit_stats : owner 직감 / ai 반박 '판단 적중률'(사람의 판단이 맞았나).
  - 이 모듈          : recall 시스템의 '회상 효용'(회상된 노드가 쓸모 있었나). 다른 축.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import binggu_p1_config as CFG  # recall_config["trace_enabled"] opt-in  # noqa: E402

VALID_VERDICTS = ("used", "ignored", "corrected")

# signal_only 라벨 — 집계 반환이 golden set 을 자동수정하는 입력으로 쓰이지 못하게 명시(헌법).
_SIGNAL_NOTE = ("이 수치는 표시 신호일 뿐 — golden set/fixture 자동수정 근거 아님. "
                "사람이 후보를 보고 recall_golden.json 을 직접 보정한다(자동결정 0).")

# golden_drift 후보 임계: 표본 N≥min 이고 (ignored+corrected)/total ≥ ratio 인 노드만 후보.
_DRIFT_N_MIN = 3
_DRIFT_RATIO = 0.5


# ---------------- store 경로 (운영 ledger 와 분리된 sibling) ----------------

def trace_store_path(home=None):
    """trace store 경로 = <binggu_home>/recall_trace.sqlite (ledger.sqlite sibling · 운영 불변)."""
    base = home or os.path.join(os.path.expanduser("~"), ".binggupack")
    return os.path.join(base, "recall_trace.sqlite")


def _open_store(home=None):
    p = trace_store_path(home)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS recall_traces("
        " trace_id TEXT PRIMARY KEY, kind TEXT, query_sha TEXT, domain TEXT,"
        " recalled_json TEXT, top1_node_id TEXT, risk_level TEXT,"
        " needs_question INTEGER, ts TEXT);"
        "CREATE TABLE IF NOT EXISTS recall_outcomes("
        " outcome_id TEXT PRIMARY KEY, trace_id TEXT, node_id TEXT, verdict TEXT,"
        " actor TEXT, ts TEXT, UNIQUE(trace_id, node_id));")
    return con


# ---------------- opt-in 판정 ----------------

def trace_enabled(home=None):
    """기록 opt-in 여부 — config trace_enabled(영구) OR env BINGGU_RECALL_TRACE=1(세션). 기본 False."""
    if os.environ.get("BINGGU_RECALL_TRACE") == "1":
        return True
    try:
        return bool(CFG.recall_config(home).get("trace_enabled", False))
    except Exception:
        return False  # config 손상도 graceful — 기본 미기록(보수)


# ---------------- PII 차단 정규화 ----------------

def _sha16(text):
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]


def _scrub_node(n):
    """회상 노드 dict → 저장 가능한 메타만(원문 0). node_id/category(subtype)/rank/relevance.

    claim·sentence·evidence_excerpt 등 원문 키는 의도적으로 버린다(PII 차단). node_id 는
    빙구팩 정본 스키마(node:CONV:<h8>) — 식별자일 뿐 원문 아님."""
    return {
        "node_id": n.get("node_id"),
        "category": n.get("semantic_subtype"),
        "rank": n.get("rank_score"),
        "relevance": n.get("relevance"),
    }


def _trace_id(kind, query_sha, node_ids, ts):
    raw = "%s|%s|%s|%s" % (kind, query_sha, ",".join(node_ids), ts)
    return "rtr-" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


# ---------------- 기록: recall/preflight 결과 → trace (opt-in) ----------------

def record_trace(query, kind, recalled_nodes, ts, *, domain=None,
                 risk_level=None, needs_question=None, home=None):
    """회상 결과 1건을 trace store 에 적재(opt-in). PII 0 — query=sha16, 노드=메타만.

    kind        : 'why_search' | 'preflight'.
    recalled_nodes: why_search.relevant_nodes / preflight.remember 형식 리스트.
    ts          : 호출자 제공 ISO 타임스탬프(결정성 · Date.now 미사용 정책).
    반환 {status, trace_id?, recorded, n_nodes} — status='disabled' 면 write 0(no-op)."""
    if not trace_enabled(home):
        return {"status": "disabled", "recorded": False, "trace_id": None,
                "reason": "trace opt-in OFF(recall_config.trace_enabled / BINGGU_RECALL_TRACE)"}
    qsha = _sha16(query)
    scrubbed = [_scrub_node(n) for n in (recalled_nodes or [])]
    node_ids = [s["node_id"] for s in scrubbed if s["node_id"]]
    tid = _trace_id(kind, qsha, node_ids, ts)
    con = _open_store(home)
    try:
        con.execute(
            "INSERT OR IGNORE INTO recall_traces"
            "(trace_id,kind,query_sha,domain,recalled_json,top1_node_id,risk_level,needs_question,ts)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (tid, kind, qsha, domain,
             json.dumps(scrubbed, ensure_ascii=False, sort_keys=True),
             node_ids[0] if node_ids else None, risk_level,
             None if needs_question is None else int(bool(needs_question)), ts))
        con.commit()
    finally:
        con.close()
    return {"status": "ok", "recorded": True, "trace_id": tid, "n_nodes": len(node_ids)}


def trace_from_why_search(query, result, ts, *, domain=None, home=None):
    """why_search 반환 dict 를 그대로 받아 trace 기록(호출측 편의 · recall.py 비침습)."""
    return record_trace(query, "why_search", result.get("relevant_nodes", []), ts,
                        domain=domain, home=home)


def trace_from_preflight(query, result, ts, *, domain=None, home=None):
    """preflight_context 반환 dict 를 받아 trace 기록(remember + 위험도/반문 메타)."""
    return record_trace(query, "preflight", result.get("remember", []), ts,
                        domain=domain, risk_level=result.get("risk_level"),
                        needs_question=result.get("needs_question"), home=home)


# ---------------- 기록: 사후 효용 판정 (actor=human 게이트) ----------------

def record_outcome(trace_id, node_id, verdict, ctx, ts, *, home=None):
    """회상 노드 1개에 대한 사후 효용 판정. actor=human 만(헌법). verdict∈used/ignored/corrected.

    node_id='*' 는 trace 전체에 대한 판정(개별 노드 미지정). 같은 (trace_id,node_id)는
    UNIQUE — 재판정은 갱신(REPLACE)이 아니라 무시(이중계상 차단 · 첫 판정 보존)."""
    if (ctx or {}).get("actor", "").strip().lower() != "human":
        return {"recorded": False, "reason": "G4_no_auto"}
    if verdict not in VALID_VERDICTS:
        return {"recorded": False, "reason": "invalid_verdict"}
    oid = "rto-" + hashlib.sha256(
        ("%s|%s|%s" % (trace_id, node_id, ts)).encode("utf-8", "replace")).hexdigest()[:16]
    con = _open_store(home)
    try:
        # trace 존재 확인(dangling outcome 방지 — 없는 trace 판정은 분모 오염)
        if not con.execute("SELECT 1 FROM recall_traces WHERE trace_id=?", (trace_id,)).fetchone():
            return {"recorded": False, "reason": "trace_not_found"}
        if con.execute("SELECT 1 FROM recall_outcomes WHERE trace_id=? AND node_id=?",
                       (trace_id, node_id)).fetchone():
            return {"recorded": False, "reason": "dup_outcome"}
        con.execute(
            "INSERT INTO recall_outcomes(outcome_id,trace_id,node_id,verdict,actor,ts)"
            " VALUES(?,?,?,?,?,?)", (oid, trace_id, node_id, verdict, "human", ts))
        con.commit()
    finally:
        con.close()
    return {"recorded": True, "outcome_id": oid}


# ---------------- 집계 (signal_only — golden set 자동수정 0) ----------------

def aggregate(home=None):
    """node별/전체 회상 효용 집계 + golden_drift 후보. 전부 signal_only(자동수정 0).

    반환 {overall, per_node, golden_drift_candidates, signal_only, note}.
      overall: 전체 trace/outcome 수, used/ignored/corrected 합, usefulness_rate(used/total).
      per_node[node_id]: {used,ignored,corrected,total,usefulness_rate}.
      golden_drift_candidates: 표본 N≥3 · (ignored+corrected)/total≥0.5 인 node — 재검토 후보.
    빈 store → 전부 0(에러 0)."""
    if not os.path.exists(trace_store_path(home)):
        return {"overall": {"traces": 0, "outcomes": 0, "used": 0, "ignored": 0,
                            "corrected": 0, "usefulness_rate": None},
                "per_node": {}, "golden_drift_candidates": [],
                "signal_only": True, "note": _SIGNAL_NOTE}
    con = _open_store(home)
    try:
        n_traces = con.execute("SELECT COUNT(*) FROM recall_traces").fetchone()[0]
        rows = con.execute("SELECT node_id, verdict FROM recall_outcomes").fetchall()
    finally:
        con.close()

    per = {}
    tot = {"used": 0, "ignored": 0, "corrected": 0}
    for node_id, verdict in rows:
        if verdict not in VALID_VERDICTS:
            continue
        tot[verdict] += 1
        d = per.setdefault(node_id, {"used": 0, "ignored": 0, "corrected": 0})
        d[verdict] += 1

    per_node = {}
    drift = []
    for node_id, d in per.items():
        total = d["used"] + d["ignored"] + d["corrected"]
        rate = round(d["used"] / total, 4) if total else None
        per_node[node_id] = {**d, "total": total, "usefulness_rate": rate}
        bad = d["ignored"] + d["corrected"]
        if total >= _DRIFT_N_MIN and total and (bad / total) >= _DRIFT_RATIO:
            drift.append({"node_id": node_id, "total": total,
                          "ignored": d["ignored"], "corrected": d["corrected"],
                          "bad_ratio": round(bad / total, 4)})
    drift.sort(key=lambda x: (-x["bad_ratio"], -x["total"], x["node_id"]))

    n_out = tot["used"] + tot["ignored"] + tot["corrected"]
    overall = {"traces": n_traces, "outcomes": n_out, **tot,
               "usefulness_rate": round(tot["used"] / n_out, 4) if n_out else None}
    return {"overall": overall, "per_node": per_node,
            "golden_drift_candidates": drift, "signal_only": True, "note": _SIGNAL_NOTE}


# ---------------- selftest (temp home · 운영 미접촉 · write 0) ----------------

def _selftest():
    import tempfile
    import shutil

    sys.path.insert(0, HERE)
    from openbinggu_staging_write_selftest import OPERATING_PATHS

    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_rtrace_")
    TS = "2026-06-27T00:00:00Z"
    try:
        home = os.path.join(tmp, ".binggupack")
        os.makedirs(home, exist_ok=True)
        # 운영 ledger 미접촉 sentinel
        ledger = os.path.join(home, "ledger.sqlite")
        with open(ledger, "wb") as f:
            f.write(b"LEDGER-SENTINEL")
        ledger_mt0 = os.path.getmtime(ledger)

        # 회상 결과(원문 claim 포함 — scrub 으로 떨궈져야)
        SECRET_CLAIM = "검증없이배포비밀원문문장SHOULDNOTPERSIST"
        recalled = [
            {"node_id": "node:CONV:aa01", "claim": SECRET_CLAIM,
             "semantic_subtype": "버그패턴", "rank_score": 0.9, "relevance": 0.8},
            {"node_id": "node:CONV:bb02", "claim": "또다른원문",
             "semantic_subtype": "교훈", "rank_score": 0.7, "relevance": 0.6},
        ]

        # ── opt-in OFF(기본) → no-op, store 파일조차 안 생김 ──
        CFG.save_user_config({"recall_config": {"trace_enabled": False}}, home=home)
        os.environ.pop("BINGGU_RECALL_TRACE", None)
        r_off = record_trace("배포 " + SECRET_CLAIM, "preflight", recalled, TS, home=home)
        ck(r_off["status"] == "disabled" and r_off["recorded"] is False,
           "opt-in OFF → record_trace no-op(status=disabled)")
        ck(not os.path.exists(trace_store_path(home)),
           "opt-in OFF → store 파일 미생성(write 0)")

        # ── opt-in ON → 기록 ──
        CFG.save_user_config({"recall_config": {"trace_enabled": True}}, home=home)
        QUERY = "검증없이 바로 배포 " + SECRET_CLAIM
        res_pf = {"remember": recalled, "risk_level": "높음", "needs_question": True}
        r_on = trace_from_preflight(QUERY, res_pf, TS, domain="bid-engine", home=home)
        ck(r_on["status"] == "ok" and r_on["recorded"] and r_on["n_nodes"] == 2,
           "opt-in ON → trace 기록(n_nodes=2)")
        tid = r_on["trace_id"]

        # ── PII 0: store 바이트에 query/claim 원문이 한 글자도 없어야 ──
        with open(trace_store_path(home), "rb") as f:
            blob = f.read()
        ck(SECRET_CLAIM.encode("utf-8") not in blob,
           "PII 0: 회상 노드 claim 원문이 store 에 미저장(scrub)")
        ck("배포".encode("utf-8") not in blob,
           "PII 0: query 원문이 store 에 미저장(sha16 만)")
        # 저장된 건 node_id/category/rank/relevance 만
        con = _open_store(home)
        rj = con.execute("SELECT recalled_json FROM recall_traces WHERE trace_id=?", (tid,)).fetchone()[0]
        con.close()
        parsed = json.loads(rj)
        ck(all(set(p.keys()) == {"node_id", "category", "rank", "relevance"} for p in parsed)
           and parsed[0]["node_id"] == "node:CONV:aa01" and parsed[0]["category"] == "버그패턴",
           "저장 메타 = node_id/category/rank/relevance 만(원문 키 0)")

        # ── 멱등: 같은 입력 재기록 → trace_id 동일·중복 INSERT 0 ──
        r_dup = trace_from_preflight(QUERY, res_pf, TS, domain="bid-engine", home=home)
        con = _open_store(home)
        n_tr = con.execute("SELECT COUNT(*) FROM recall_traces").fetchone()[0]
        con.close()
        ck(r_dup["trace_id"] == tid and n_tr == 1, "멱등: 동일 입력 → trace 1건(INSERT OR IGNORE)")

        # ── outcome actor=ai → G4_no_auto ──
        o_ai = record_outcome(tid, "node:CONV:aa01", "used", {"actor": "ai"}, TS, home=home)
        ck(not o_ai["recorded"] and o_ai["reason"] == "G4_no_auto",
           "outcome actor=ai → G4_no_auto(사람 판정 게이트)")

        # ── invalid verdict ──
        o_bad = record_outcome(tid, "node:CONV:aa01", "maybe", {"actor": "human"}, TS, home=home)
        ck(not o_bad["recorded"] and o_bad["reason"] == "invalid_verdict",
           "invalid verdict → 거부")

        # ── dangling trace_id → 거부(분모 오염 방지) ──
        o_dang = record_outcome("rtr-deadbeefdeadbeef", "node:CONV:aa01", "used",
                                {"actor": "human"}, TS, home=home)
        ck(not o_dang["recorded"] and o_dang["reason"] == "trace_not_found",
           "dangling trace_id → trace_not_found")

        # ── 정상 outcome: aa01 used, bb02 ignored ──
        ck(record_outcome(tid, "node:CONV:aa01", "used", {"actor": "human"}, TS, home=home)["recorded"],
           "outcome used 기록(actor=human)")
        ck(record_outcome(tid, "node:CONV:bb02", "ignored", {"actor": "human"}, TS, home=home)["recorded"],
           "outcome ignored 기록")
        # 이중계상: 같은 (trace,node) 재판정 → dup
        o_d2 = record_outcome(tid, "node:CONV:aa01", "corrected", {"actor": "human"}, TS, home=home)
        ck(not o_d2["recorded"] and o_d2["reason"] == "dup_outcome",
           "이중계상 가드: 같은 (trace,node) 재판정 → dup_outcome(첫 판정 보존)")

        # ── 집계: used 1 / ignored 1 → usefulness 0.5 ──
        agg = aggregate(home=home)
        ck(agg["overall"]["used"] == 1 and agg["overall"]["ignored"] == 1
           and agg["overall"]["usefulness_rate"] == 0.5,
           "집계: used 1·ignored 1 → usefulness_rate 0.5")
        ck(agg["per_node"]["node:CONV:aa01"]["usefulness_rate"] == 1.0
           and agg["per_node"]["node:CONV:bb02"]["usefulness_rate"] == 0.0,
           "per_node usefulness(aa01=1.0 · bb02=0.0)")
        ck(agg["signal_only"] is True, "집계 signal_only=True(golden 자동수정 0)")

        # ── golden_drift 후보: bb02 를 ignored/corrected 3회 채워 후보화 ──
        #   (서로 다른 trace 가 필요 — 같은 trace 는 node UNIQUE). 추가 trace 2건 생성.
        for i, verdict in enumerate(["ignored", "corrected"]):
            q = "다른 작업 %d" % i
            rr = record_trace(q, "why_search",
                              [{"node_id": "node:CONV:bb02", "semantic_subtype": "교훈",
                                "rank_score": 0.5, "relevance": 0.4}], TS, home=home)
            record_outcome(rr["trace_id"], "node:CONV:bb02", verdict, {"actor": "human"}, TS, home=home)
        agg2 = aggregate(home=home)
        drift_ids = [d["node_id"] for d in agg2["golden_drift_candidates"]]
        bb = agg2["per_node"]["node:CONV:bb02"]
        ck(bb["total"] == 3 and bb["ignored"] == 2 and bb["corrected"] == 1
           and "node:CONV:bb02" in drift_ids,
           "golden_drift: bb02(ignored2+corrected1/3=1.0≥0.5,N≥3) → 재검토 후보")
        ck("node:CONV:aa01" not in drift_ids,
           "golden_drift: aa01(used 1·N<3) → 후보 아님(표본게이트)")

        # ── env opt-in: config OFF 라도 BINGGU_RECALL_TRACE=1 이면 기록 ──
        home2 = os.path.join(tmp, ".binggupack2")
        os.makedirs(home2, exist_ok=True)
        CFG.save_user_config({"recall_config": {"trace_enabled": False}}, home=home2)
        os.environ["BINGGU_RECALL_TRACE"] = "1"
        r_env = record_trace("env 작업", "why_search", recalled, TS, home=home2)
        os.environ.pop("BINGGU_RECALL_TRACE", None)
        ck(r_env["recorded"], "env BINGGU_RECALL_TRACE=1 → config OFF 라도 세션 기록")

        # ── 빈 store graceful ──
        home3 = os.path.join(tmp, ".binggupack3")
        agg_empty = aggregate(home=home3)
        ck(agg_empty["overall"]["traces"] == 0 and agg_empty["golden_drift_candidates"] == []
           and agg_empty["overall"]["usefulness_rate"] is None,
           "빈 store → 집계 0·후보 0(에러 0)")

        # ── 운영 ledger sentinel 미접촉 ──
        ck(os.path.exists(ledger) and os.path.getmtime(ledger) == ledger_mt0,
           "운영 ledger.sqlite sentinel 미접촉(별도 store · write 0)")
    finally:
        CFG_home = None  # noqa: F841
        shutil.rmtree(tmp, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "운영 store 불변(OPERATING_PATHS mtime 전후 동일)")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    if sys.argv[1] == "--aggregate":
        import json as _json
        print(_json.dumps(aggregate(), ensure_ascii=False, indent=2))
        sys.exit(0)
    print("usage: binggu_recall_trace.py [--selftest | --aggregate]")
    sys.exit(2)
