# -*- coding: utf-8 -*-
"""binggu_learn_consume — 학습 큐(hit/miss 후보) → hit_recording 안전 소비.

user-prompt-learn-outcome.js 가 owner 자연 피드백("맞네"/"틀렸어")을 감지해 append 하는
learn_outcome_queue.jsonl(append-only)을 **사람 승인**으로 소비해 hit_events 에 적재한다.

★설계 근거(회상으로 확정 · owner 판단 정합):
  - 스케줄러 자동 소비 배제: owner_refutes "안전장치를 Agent 경유로 우회해 자동 반복" ·
    owner "무차별 적재=노이즈 폭증 → 교훈·자가평가만 선별" · "OFF 박제인데 무단 재활성" 경계.
    → 소비는 owner 승인 CLI(dry-run 기본 · CONSUME <n> 정확 confirm)만. 자동 크랭크 0.
  - index 는 큐에 없다(owner 는 "맞네"만 발화 · 어느 순위가 적중인진 안 밝힘). 소비 시점에
    why_search 를 재실행해 top 을 preview 로 보여주고, owner 가 --index 로 지정(기본 top1).

안전 불변(hit_recording 그대로 통과):
  - actor=human 게이트(불변식6) · D-1(node_id 위조 차단=query,index 재실행) ·
    D-2(안정 decision_id 이중계상 차단) · nonce=None(소비 시점 회상이 진실 · stale 검사 skip).
  - 큐는 append-only. 소비는 consumed=true 마킹만(원자 재작성) · 원문 evidence 불변.
  - 운영 write 는 hit_recording(hit_events INSERT) 한 곳. 규칙/박제/nodes/edges 불변.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))      # <repo>/binggupack/pack
ROOT = os.path.dirname(os.path.dirname(HERE))          # <repo>
_SCRIPTS = os.path.join(ROOT, "scripts")
for _p in (ROOT, _SCRIPTS):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack import recall as RECALL           # why_search(read-only)          # noqa: E402
from binggupack.pack import hit_recording as HR         # mark_outcome(안전 write)        # noqa: E402

QUEUE_NAME = "learn_outcome_queue.jsonl"
CONFIRM_RE = re.compile(r"^CONSUME\s+(\d+)$")


# ── 경로: learn-outcome.js 와 동일 규칙(BINGGU_HOME/state 우선, 없으면 ~/.claude/state) ──
def _state_dir(home=None):
    if home and str(home).strip():
        return os.path.join(str(home).strip(), "state")
    bh = os.environ.get("BINGGU_HOME")
    if bh and bh.strip():
        return os.path.join(bh.strip(), "state")
    return os.path.join(os.path.expanduser("~"), ".claude", "state")


def queue_path(home=None):
    return os.path.join(_state_dir(home), QUEUE_NAME)


# ── 큐 파싱(빈 줄 제외 · 라인 인덱스 = 재작성 인덱스와 동일) ────────────────────────
def _load_lines(qpath):
    if not qpath or not os.path.exists(qpath):
        return []
    with open(qpath, encoding="utf-8") as f:
        return [ln for ln in (raw.rstrip("\n") for raw in f) if ln.strip()]


def _parse(qpath):
    out = []
    for i, ln in enumerate(_load_lines(qpath)):
        try:
            out.append((i, json.loads(ln)))
        except Exception:
            out.append((i, None))
    return out


def load_pending(qpath):
    """소비 대기(consumed 미마킹·유효 JSON) 항목만 [(line_idx, entry), ...]."""
    return [(i, o) for (i, o) in _parse(qpath) if o and not o.get("consumed")]


def _mark_consumed(qpath, line_idx):
    """지정 라인만 consumed=true 로 원자 재작성(다른 라인 보존)."""
    lines = _load_lines(qpath)
    if line_idx < 0 or line_idx >= len(lines):
        return False
    obj = json.loads(lines[line_idx])
    obj["consumed"] = True
    obj["consumed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines[line_idx] = json.dumps(obj, ensure_ascii=False)
    tmp = qpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp, qpath)
    return True


# ── preview(dry-run) — 소비 대기 항목 + 각 회상 top preview(read-only) ─────────────
def preview(ledger_path, qpath, home=None, top=3):
    pend = load_pending(qpath)
    items = []
    for qi, (_line_idx, entry) in enumerate(pend):
        queries = entry.get("queries") or []
        recall_top = []
        if queries and ledger_path and os.path.exists(ledger_path):
            try:
                res = RECALL.why_search(ledger_path, queries[0],
                                        home=home or os.path.dirname(ledger_path))
                for j, n in enumerate((res.get("relevant_nodes") or [])[:top], 1):
                    recall_top.append({"index": j, "claim": n.get("claim")})
            except Exception:
                pass
        items.append({
            "qi": qi,
            "outcome": entry.get("outcome"),
            "feedback": (entry.get("evidence") or {}).get("feedback"),
            "queries": queries,
            "ts": entry.get("ts"),
            "recall_top": recall_top,
        })
    return {"pending": len(pend), "items": items}


def render_preview_md(pv):
    n = pv.get("pending", 0)
    if not n:
        return "학습 큐: 소비 대기 0건. (owner 자연 피드백이 쌓이면 여기 표시됩니다.)"
    out = ["학습 큐 소비 대기 %d건 (dry-run · 저장 0)" % n,
           "  소비: python binggu.py learn-consume --confirm \"CONSUME <번호>\" [--index k]", ""]
    for it in pv["items"]:
        tag = "적중(hit)" if it["outcome"] == "hit" else "빗나감(miss)"
        out.append("[%d] %s · 발화: %s" % (it["qi"], tag, (it.get("feedback") or "")[:60]))
        if it.get("queries"):
            out.append("    회상 query: %s" % it["queries"][0][:70])
        for rt in it.get("recall_top") or []:
            out.append("      %d) %s" % (rt["index"], (rt.get("claim") or "")[:70]))
        if not it.get("recall_top"):
            out.append("      (회상 재현 0 — query 로 회상되는 판단 없음/장부 변경)")
    return "\n".join(out)


# ── consume — owner 승인 소비(mark_outcome actor=human) ───────────────────────────
def consume(db, ledger_path, qpath, qi, index=1, home=None):
    """qi 번째 소비 대기 항목을 mark_outcome 으로 적재하고 consumed=true 마킹.

    반환: {consumed, reason?, outcome?, node_claim?, decision_id?, query?, index?, mark?}.
    """
    pend = load_pending(qpath)
    if not isinstance(qi, int) or qi < 0 or qi >= len(pend):
        return {"consumed": False, "reason": "qi_out_of_range", "pending": len(pend)}
    line_idx, entry = pend[qi]
    outcome = entry.get("outcome")
    queries = entry.get("queries") or []
    if not queries:
        # ★A 재설계(2026-07-10): recall 무관 owner 지적(recall_linked=false) — 발화 앵커로 직접
        #   hit/miss 기록. hit_events 는 speaker 별 outcome 개수만 세므로 노드에 안 묶어도 owner
        #   적중률에 반영된다. 위조 차단은 발화 앵커(UserPromptSubmit hook)+owner 승인(human)이 보장.
        fb = (entry.get("evidence") or {}).get("feedback") or ""
        r = HR.mark_outcome_uttered(db, fb, entry.get("ts"), outcome,
                                    {"actor": "human"}, domain=entry.get("domain"))
        if not r.get("recorded"):
            return {"consumed": False, "reason": r.get("reason"), "mark": r}
        _mark_consumed(qpath, line_idx)
        return {"consumed": True, "outcome": outcome, "index": index,
                "node_claim": fb[:70], "query": None, "anchor": "utterance",
                "node_id": r.get("node_id"), "decision_id": r.get("decision_id")}
    query = queries[0]
    # mark_outcome 이 actor=human 게이트·D-1·D-2·nonce(None=stale skip) 을 그대로 강제.
    r = HR.mark_outcome(db, ledger_path, query, index, outcome, {"actor": "human"},
                        nonce=None, domain=entry.get("domain"), home=home)
    if not r.get("recorded"):
        return {"consumed": False, "reason": r.get("reason"), "mark": r}
    _mark_consumed(qpath, line_idx)
    return {"consumed": True, "outcome": outcome, "node_claim": r.get("node_claim"),
            "decision_id": r.get("decision_id"), "query": query, "index": index}


def parse_confirm(confirm):
    """--confirm 문자열 → qi(int) 또는 None(형식 불일치)."""
    if not confirm:
        return None
    m = CONFIRM_RE.match(confirm.strip())
    return int(m.group(1)) if m else None


# ---------------- selftest (temp 큐 + temp DB · 운영 write 0) ----------------

def _selftest():
    import tempfile
    import shutil
    from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="binggu_learncons_")
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if bool(ok) else "FAIL"))

    def mk(db, nid, sent):
        db.con.execute(
            "INSERT INTO nodes(node_id,node_type,sentence,semantic_subtype,speaker,state,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (nid, "judgment", sent, "교훈", "owner", "active", "2026-06-20T00:00:00Z"))
        db.con.commit()

    ledger = os.path.join(tmp, "led.sqlite")
    db = StagingDB(ledger)
    mk(db, "n1", "배포 전 로컬 selftest 와 live endpoint 를 확인한다")
    mk(db, "n2", "배포 전 로컬 selftest 확인하고 endpoint 응답을 본다")

    q = "배포 전 endpoint 확인"
    qpath = os.path.join(tmp, QUEUE_NAME)

    def write_queue(entries):
        with open(qpath, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    try:
        # 큐: hit 1건 + miss 1건 + 이미 consumed 1건(소비 대상 아님).
        write_queue([
            {"ts": "2026-07-10T00:00:00Z", "outcome": "hit", "queries": [q],
             "evidence": {"feedback": "오 맞네"}, "consumed": False},
            {"ts": "2026-07-10T00:01:00Z", "outcome": "miss", "queries": [q],
             "evidence": {"feedback": "아니야 그거 아냐"}, "consumed": False},
            {"ts": "2026-07-09T00:00:00Z", "outcome": "hit", "queries": [q],
             "evidence": {"feedback": "이미소비"}, "consumed": True},
        ])

        # T1 load_pending — consumed=true 는 제외(2건만).
        pend = load_pending(qpath)
        rec(1, "load_pending: consumed 제외(대기 2건)", len(pend) == 2)

        # T2 preview — 회상 top 재현 · 운영/큐 write 0.
        q_mtime_before = os.path.getmtime(qpath)
        pv = preview(ledger, qpath, home=tmp)
        rec(2, "preview: 대기 2건 + 회상 top 재현 · 큐 unchanged",
            pv["pending"] == 2 and pv["items"][0]["recall_top"]
            and os.path.getmtime(qpath) == q_mtime_before)

        # T3 정상 소비(qi=0 hit index=1) → recorded · consumed=true · hit_events 1.
        r3 = consume(db, ledger, qpath, 0, index=1, home=tmp)
        n_ev = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        pend_after = load_pending(qpath)
        rec(3, "소비 qi=0(hit) → recorded·consumed=true·hit_events 1·대기 1건",
            r3.get("consumed") and n_ev == 1 and len(pend_after) == 1)

        # T4 소비 마킹 무결 — 다른 라인(miss)·consumed 라인 보존(총 3라인 유지).
        all_lines = _parse(qpath)
        consumed_flags = [bool(o.get("consumed")) for _, o in all_lines]
        rec(4, "consumed 마킹 무결(3라인 유지·[0][2] consumed·[1] 미소비)",
            len(all_lines) == 3 and consumed_flags == [True, False, True])

        # T5 miss 소비(qi=0 은 이제 원래 [1] miss) index=2 → 다른 node → 정상.
        r5 = consume(db, ledger, qpath, 0, index=2, home=tmp)
        miss_ev = db.con.execute("SELECT count(*) FROM hit_events WHERE outcome='miss'").fetchone()[0]
        rec(5, "소비 qi=0(miss·index=2) → recorded·hit_events miss 1",
            r5.get("consumed") and r5.get("outcome") == "miss" and miss_ev == 1)

        # T6 대기 0 → preview 안내 · consume qi 범위 밖.
        pv6 = preview(ledger, qpath, home=tmp)
        r6 = consume(db, ledger, qpath, 0, index=1, home=tmp)
        rec(6, "대기 0건 → preview pending 0 · consume qi_out_of_range",
            pv6["pending"] == 0 and (not r6.get("consumed"))
            and r6.get("reason") == "qi_out_of_range")

        # T7 parse_confirm — 정확 문구만.
        rec(7, "parse_confirm: 'CONSUME 3'→3 · 'consume 3'/빈값→None",
            parse_confirm("CONSUME 3") == 3 and parse_confirm("consume 3") is None
            and parse_confirm(None) is None)

        # T8 query 빈 + 발화 근거도 없음 → empty_feedback graceful(기록 0).
        write_queue([{"ts": "x", "outcome": "hit", "queries": [], "consumed": False}])
        r8 = consume(db, ledger, qpath, 0, index=1, home=tmp)
        rec(8, "query 빈+발화없음 → empty_feedback graceful(consumed False·에러 0)",
            (not r8.get("consumed")) and r8.get("reason") == "empty_feedback")

        # T9 ★A 재설계: recall 무관 owner 지적(query 빈·feedback 있음) → 발화 앵커로 hit/miss 소비.
        ev_b = db.con.execute("SELECT count(*) FROM hit_events WHERE node_id LIKE 'utter:%'").fetchone()[0]
        write_queue([{"ts": "2026-07-10T09:00:00Z", "outcome": "miss", "queries": [],
                      "recall_linked": False, "evidence": {"feedback": "산으로 간다"}, "consumed": False}])
        r9 = consume(db, ledger, qpath, 0, index=1, home=tmp)
        ev_a = db.con.execute("SELECT count(*) FROM hit_events WHERE node_id LIKE 'utter:%'").fetchone()[0]
        pend9 = load_pending(qpath)
        rec(9, "발화 앵커 소비(recall 무관 owner 지적 → recorded·anchor=utterance·hit_events +1·consumed)",
            r9.get("consumed") and r9.get("anchor") == "utterance"
            and ev_a == ev_b + 1 and len(pend9) == 0)

    finally:
        db.close()
        op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
        shutil.rmtree(tmp, ignore_errors=True)

    store_unchanged = op_before == op_after
    print("=" * 74)
    print("binggu_learn_consume — 학습 큐 owner 승인 소비 selftest")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=%s  actor_human_gate=on  queue_append_only=on"
          % store_unchanged)
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print("GATE=%s" % gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_learn_consume: --selftest 로 검증 실행")
