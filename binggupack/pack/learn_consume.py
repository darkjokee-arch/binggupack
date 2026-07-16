# -*- coding: utf-8 -*-
"""binggu_learn_consume — 학습 큐(교환 후보) → hit_recording 안전 소비.

user-prompt-learn-outcome.js 가 owner 자연 피드백("맞네"/"틀렸어")을 감지해 append 하는
learn_outcome_queue.jsonl(append-only)을 **사람 승인**으로 소비해 hit_events 에 적재한다.

★교환 축(2026-07-13 owner "사용자 대화 - ai답변 - 맞는지 틀리는지 확인"):
  발화 극성은 결과가 아니라 입장(stance: refutes 반박 / accepts 인정)이다. 누가 맞았는지는
  소비 시점에 사람이 확인(verdict: upheld 발화대로 / overturned 뒤집힘)한다. 구축(발화 극성
  → speaker=owner 직결)은 옳은 지적을 owner miss 로 계상하는 축 뒤집힘이라 폐기 —
  mark_exchange_uttered 로 귀속(반박 적중 = owner hit + ai miss).

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

try:
    from binggupack.workspace.platform import invocation_prefix
except Exception:  # pragma: no cover — 구버전/부분설치 폴백
    def invocation_prefix(argv0=None):
        return "python binggu.py"

HERE = os.path.dirname(os.path.abspath(__file__))      # <repo>/binggupack/pack
ROOT = os.path.dirname(os.path.dirname(HERE))          # <repo>
_SCRIPTS = os.path.join(ROOT, "scripts")
for _p in (ROOT, _SCRIPTS):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack import recall as RECALL           # why_search(read-only)          # noqa: E402
from binggupack.pack import hit_recording as HR         # mark_outcome(안전 write)        # noqa: E402

QUEUE_NAME = "learn_outcome_queue.jsonl"
# 'CONSUME 0' 단건 + 'CONSUME 0,1,4' 일괄(2026-07-13 owner GO — 도장 1회 다건 소비)
CONFIRM_RE = re.compile(r"^CONSUME\s+(\d+(?:\s*,\s*\d+)*)$")


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


def stance_of(entry):
    """교환 축 입장 — 신큐는 stance 필드, 구큐(2026-07-13 이전)는 outcome 극성에서 유도.

    구큐 유도 근거: 훅의 구 outcome 은 발화 극성 그대로였다(부정어→'miss'·긍정어→'hit')
    → 부정어 발화 = AI 답변 반박(refutes) · 긍정어 발화 = AI 답변 인정(accepts)."""
    s = (entry or {}).get("stance")
    if s in ("refutes", "accepts"):
        return s
    oc = (entry or {}).get("outcome")
    if oc == "miss":
        return "refutes"
    if oc == "hit":
        return "accepts"
    return None


def _node_outcome(stance, verdict):
    """recall 연결 항목의 회상 조언 노드 귀속 — '그 조언이 맞았나'.

    반박이 유지(upheld)됐거나 인정이 뒤집혔으면(overturned) 조언은 빗나간 것(miss)."""
    advice_wrong = (stance == "refutes") == (verdict == "upheld")
    return "miss" if advice_wrong else "hit"


def load_pending_today(qpath, today=None):
    """당일(ts 날짜 prefix 일치) 소비 대기 항목 — [(qi, entry), ...] (read-only · 소비 0).

    ★qi = load_pending 상 인덱스 = learn-consume dry-run 번호 = consume(qi)/CONSUME <번호>
    와 동일 번호를 보존한다(당일 필터로 재번호 매기면 owner 확정이 다른 항목을 소비 — 금지).
    ts 는 학습 훅(user-prompt-learn-outcome.js)의 UTC ISO(toISOString) — today 는
    'YYYY-MM-DD'(UTC) prefix. 미지정 시 현재 UTC 날짜(테스트는 today 주입식 · wall-clock 배제)."""
    if today is None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
    today = str(today).strip()
    if not today:
        return []
    return [(qi, entry) for qi, (_line_idx, entry) in enumerate(load_pending(qpath))
            if str(entry.get("ts") or "").startswith(today)]


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
            "outcome": entry.get("outcome"),           # legacy 표시용(신규 축은 stance)
            "stance": stance_of(entry),
            "ai_answer": (entry.get("ai_answer") or "")[:70],
            "feedback": (entry.get("evidence") or {}).get("feedback"),
            "queries": queries,
            "ts": entry.get("ts"),
            "recall_top": recall_top,
        })
    return {"pending": len(pend), "items": items}


_STANCE_TAG = {"refutes": "반박(사용자가 AI 를 정정)", "accepts": "인정(AI 답변 수긍)"}


def render_preview_md(pv):
    n = pv.get("pending", 0)
    if not n:
        return "학습 큐: 소비 대기 0건. (owner 자연 피드백이 쌓이면 여기 표시됩니다.)"
    out = ["학습 큐 소비 대기 %d건 (dry-run · 저장 0 · 축: 사용자 발화 → AI 답변 → 확인)" % n,
           "  소비(에이전트 세션): 채팅에 \"컨슘 <번호>\" 한 줄 도장 → "
           "%s learn-consume --confirm \"CONSUME <번호>\""
           " [--verdict overturned] [--index k]" % invocation_prefix(), ""]
    for it in pv["items"]:
        tag = _STANCE_TAG.get(it.get("stance"), "미상(stance 없음)")
        out.append("[%d] %s · 사용자: %s" % (it["qi"], tag, (it.get("feedback") or "")[:60]))
        out.append("    AI 답변: %s" % (it["ai_answer"] if it.get("ai_answer")
                                        else "(미기록 — 구큐 항목)"))
        if it.get("queries"):
            out.append("    회상 query: %s" % it["queries"][0][:70])
        for rt in it.get("recall_top") or []:
            out.append("      %d) %s" % (rt["index"], (rt.get("claim") or "")[:70]))
        if it.get("queries") and not it.get("recall_top"):
            out.append("      (회상 재현 0 — 소비 시 발화 교환 축으로 폴백)")
    out.append("")
    out.append("확인(verdict) 기본 = 발화대로(upheld): 반박이 옳았음 → owner 적중 + ai 빗나감 · "
               "인정 → ai 적중. 나중에 뒤집힌 건이면 --verdict overturned.")
    return "\n".join(out)


# ── consume — owner 승인 소비(교환 축 · actor=human) ───────────────────────────
def consume(db, ledger_path, qpath, qi, index=1, home=None, ctx=None, verdict="upheld"):
    """qi 번째 소비 대기 항목을 교환 축으로 적재하고 consumed=true 마킹.

    ★교환 축: stance(반박/인정) × verdict(사람 확인 upheld/overturned) 로 귀속 —
      반박+upheld → owner hit + ai miss · 반박+overturned → owner miss + ai hit ·
      인정 → ai 행만(hit/miss). recall 연결 항목은 회상 조언 노드에도 귀속(mark_outcome ·
      '그 조언이 맞았나' 축은 원래 정합)하고, 회상 재현 0 이면 발화 교환 축으로 폴백.

    actor 는 하드코딩 'human' 이 아니라 호출자가 넘긴 `ctx`. binggu CLI 는 `_resolve_human_ctx`
    (판정 정본은 그 docstring — save-n 참조 바인딩/cli_command · 에이전트 세션 deny) 를 넘긴다. ctx
    미지정 시 fail-closed 기본(reader) → mark_outcome/mark_exchange_uttered 의 actor=human 게이트가 BLOCK.

    반환: {consumed, reason?, stance?, verdict?, rows?, outcome?, node_claim?, decision_id?,
           query?, index?, mark?}.
    """
    ctx = ctx if ctx is not None else {"actor": "reader"}   # fail-closed 기본
    if verdict not in ("upheld", "overturned"):
        return {"consumed": False, "reason": "invalid_verdict"}
    pend = load_pending(qpath)
    if not isinstance(qi, int) or qi < 0 or qi >= len(pend):
        return {"consumed": False, "reason": "qi_out_of_range", "pending": len(pend)}
    line_idx, entry = pend[qi]
    return _consume_entry(db, ledger_path, qpath, line_idx, entry,
                          index=index, home=home, ctx=ctx, verdict=verdict)


def consume_many(db, ledger_path, qpath, qis, index=1, home=None, ctx=None, verdict="upheld"):
    """★일괄 소비(2026-07-13 owner GO) — 도장 1회로 여러 건. 단일 pending 스냅샷의 line_idx
    로 소비하므로 항목 간 번호 재편 문제가 없다(재도장 불필요). 범위 검증은 all-or-nothing
    (하나라도 벗어나면 전체 거부 — 부분 오소비 방지). 항목별 결과 리스트 반환."""
    ctx = ctx if ctx is not None else {"actor": "reader"}   # fail-closed 기본
    if verdict not in ("upheld", "overturned"):
        return {"consumed_any": False, "reason": "invalid_verdict", "results": []}
    pend = load_pending(qpath)
    qis = sorted({int(q) for q in (qis or [])})
    if not qis or any(q < 0 or q >= len(pend) for q in qis):
        return {"consumed_any": False, "reason": "qi_out_of_range",
                "pending": len(pend), "results": []}
    results = []
    for qi in qis:
        line_idx, entry = pend[qi]   # 스냅샷 line_idx — 소비 순서와 무관하게 안정
        r = _consume_entry(db, ledger_path, qpath, line_idx, entry,
                           index=index, home=home, ctx=ctx, verdict=verdict)
        r["qi"] = qi
        results.append(r)
    return {"consumed_any": any(r.get("consumed") for r in results),
            "all_consumed": all(r.get("consumed") for r in results),
            "results": results}


def _consume_entry(db, ledger_path, qpath, line_idx, entry, index, home, ctx, verdict):
    """단일 큐 항목 소비(교환 축) — consume/consume_many 공용 본체."""
    stance = stance_of(entry)
    if stance is None:
        return {"consumed": False, "reason": "invalid_stance"}
    queries = entry.get("queries") or []
    fb = (entry.get("evidence") or {}).get("feedback") or ""

    def _exchange():
        # 발화 앵커 교환 기록 — 위조 차단은 발화 앵커(UserPromptSubmit hook)+owner 승인(human).
        r = HR.mark_exchange_uttered(db, fb, entry.get("ts"), stance, verdict, ctx,
                                     domain=entry.get("domain"),
                                     ai_answer=entry.get("ai_answer"))
        if not r.get("recorded"):
            return {"consumed": False, "reason": r.get("reason"), "mark": r}
        _mark_consumed(qpath, line_idx)
        return {"consumed": True, "stance": stance, "verdict": verdict,
                "rows": r.get("rows"), "index": index,
                "node_claim": fb[:70], "query": None, "anchor": "utterance",
                "node_id": r.get("node_id"), "decision_id": r.get("decision_id")}

    if not queries:
        return _exchange()
    query = queries[0]
    # recall 연결 — 회상 조언 노드 귀속. mark_outcome 이 actor=human·D-1·D-2 를 그대로 강제.
    node_outcome = _node_outcome(stance, verdict)
    r = HR.mark_outcome(db, ledger_path, query, index, node_outcome, ctx,
                        nonce=None, domain=entry.get("domain"), home=home)
    if not r.get("recorded"):
        if r.get("reason") in ("no_recall", "no_ledger") and fb.strip():
            return _exchange()   # 장부 변경으로 회상 재현 0 → 발화 교환 축 폴백(소비 가능 유지)
        return {"consumed": False, "reason": r.get("reason"), "mark": r}
    _mark_consumed(qpath, line_idx)
    return {"consumed": True, "outcome": node_outcome, "stance": stance, "verdict": verdict,
            "node_claim": r.get("node_claim"), "decision_id": r.get("decision_id"),
            "query": query, "index": index}


def parse_confirm(confirm):
    """--confirm 문자열 → qi 리스트 또는 None(형식 불일치).

    'CONSUME 0' → [0] · 'CONSUME 0,1,4' → [0,1,4](중복 제거·정렬). 정확형만(자동확정 0)."""
    if not confirm:
        return None
    m = CONFIRM_RE.match(confirm.strip())
    if not m:
        return None
    return sorted({int(x) for x in re.findall(r"\d+", m.group(1))})


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
        r3 = consume(db, ledger, qpath, 0, index=1, home=tmp, ctx={"actor": "human"})
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
        r5 = consume(db, ledger, qpath, 0, index=2, home=tmp, ctx={"actor": "human"})
        miss_ev = db.con.execute("SELECT count(*) FROM hit_events WHERE outcome='miss'").fetchone()[0]
        rec(5, "소비 qi=0(miss·index=2) → recorded·hit_events miss 1",
            r5.get("consumed") and r5.get("outcome") == "miss" and miss_ev == 1)

        # T6 대기 0 → preview 안내 · consume qi 범위 밖.
        pv6 = preview(ledger, qpath, home=tmp)
        r6 = consume(db, ledger, qpath, 0, index=1, home=tmp, ctx={"actor": "human"})
        rec(6, "대기 0건 → preview pending 0 · consume qi_out_of_range",
            pv6["pending"] == 0 and (not r6.get("consumed"))
            and r6.get("reason") == "qi_out_of_range")

        # T7 parse_confirm — 정확 문구만(단건 리스트 + 일괄 콤마).
        rec(7, "parse_confirm: 'CONSUME 3'→[3] · 'CONSUME 0,2,1'→[0,1,2] · 비정확형→None",
            parse_confirm("CONSUME 3") == [3] and parse_confirm("CONSUME 0,2,1") == [0, 1, 2]
            and parse_confirm("consume 3") is None and parse_confirm(None) is None)

        # T8 query 빈 + 발화 근거도 없음 → empty_feedback graceful(기록 0).
        write_queue([{"ts": "x", "outcome": "hit", "queries": [], "consumed": False}])
        r8 = consume(db, ledger, qpath, 0, index=1, home=tmp, ctx={"actor": "human"})
        rec(8, "query 빈+발화없음 → empty_feedback graceful(consumed False·에러 0)",
            (not r8.get("consumed")) and r8.get("reason") == "empty_feedback")

        # T9 ★교환 축: recall 무관 owner 지적(구큐 outcome=miss → stance=refutes 유도) →
        #    upheld 기본으로 owner(지적,hit)+ai(답변,miss) 2행 — 옳은 지적이 owner 적중으로.
        ev_b = db.con.execute("SELECT count(*) FROM hit_events WHERE node_id LIKE 'utter:%'").fetchone()[0]
        write_queue([{"ts": "2026-07-10T09:00:00Z", "outcome": "miss", "queries": [],
                      "recall_linked": False, "evidence": {"feedback": "산으로 간다"}, "consumed": False}])
        r9 = consume(db, ledger, qpath, 0, index=1, home=tmp, ctx={"actor": "human"})
        ev_a = db.con.execute("SELECT count(*) FROM hit_events WHERE node_id LIKE 'utter:%'").fetchone()[0]
        rows9 = {(r["speaker"], r["outcome"]) for r in (r9.get("rows") or [])}
        pend9 = load_pending(qpath)
        rec(9, "교환 소비(구큐 지적 → refutes+upheld → owner hit+ai miss 2행·anchor=utterance·consumed)",
            r9.get("consumed") and r9.get("anchor") == "utterance"
            and r9.get("stance") == "refutes" and rows9 == {("owner", "hit"), ("ai", "miss")}
            and ev_a == ev_b + 2 and len(pend9) == 0)

        # T10 ★P1-A.1 fail-closed: ctx 미지정(기본 reader)·recall 큐 → mark_outcome G4_no_auto·소비 0.
        write_queue([{"ts": "z", "outcome": "hit", "queries": ["이 입찰 보류"], "consumed": False}])
        ev10b = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        r10 = consume(db, ledger, qpath, 0, index=1, home=tmp)   # ctx 미지정 → reader(fail-closed)
        ev10a = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        rec(10, "ctx 기본(reader)·비대화형 → G4_no_auto·hit_events 불변·미소비(fail-closed)",
            (not r10.get("consumed")) and ev10a == ev10b
            and (r10.get("mark") or {}).get("reason") == "G4_no_auto")

        # T11 ★P1-A.1 발화 앵커도 reader → G4_no_auto(fail-closed · 발화 경로 동일 게이트).
        write_queue([{"ts": "z2", "outcome": "miss", "queries": [], "recall_linked": False,
                      "evidence": {"feedback": "다시 봐"}, "consumed": False}])
        ev11b = db.con.execute("SELECT count(*) FROM hit_events WHERE node_id LIKE 'utter:%'").fetchone()[0]
        r11 = consume(db, ledger, qpath, 0, index=1, home=tmp)   # ctx 미지정 → reader
        ev11a = db.con.execute("SELECT count(*) FROM hit_events WHERE node_id LIKE 'utter:%'").fetchone()[0]
        rec(11, "발화 앵커도 reader → G4_no_auto·utter hit_events 불변(fail-closed)",
            (not r11.get("consumed")) and ev11a == ev11b
            and (r11.get("mark") or {}).get("reason") == "G4_no_auto")

        # T12 ★B안(사람 확정): load_pending_today — 당일 prefix 만·consumed 제외·qi(소비 번호) 보존·
        #     read-only(큐 mtime 불변)·다른 날짜 주입 → 0건. today 주입식(wall-clock 의존 0).
        write_queue([
            {"ts": "2026-07-12T22:00:00Z", "outcome": "hit", "queries": [],
             "evidence": {"feedback": "전일 지적"}, "consumed": False},
            {"ts": "2026-07-13T01:00:00Z", "outcome": "miss", "queries": [],
             "evidence": {"feedback": "너도 제대로 안 읽었는데"}, "consumed": False},
            {"ts": "2026-07-13T02:00:00Z", "outcome": "hit", "queries": [],
             "evidence": {"feedback": "이미소비(당일)"}, "consumed": True},
            {"ts": "2026-07-13T03:00:00Z", "outcome": "hit", "queries": [q],
             "evidence": {"feedback": "오 맞네"}, "consumed": False},
        ])
        q12_mtime = os.path.getmtime(qpath)
        td = load_pending_today(qpath, today="2026-07-13")
        rec(12, "load_pending_today: 당일 2건(전일·consumed 제외)·qi=소비 번호(1,2) 보존·큐 write 0·타일자 0건",
            [qi for qi, _ in td] == [1, 2]
            and td[0][1]["outcome"] == "miss" and td[1][1]["outcome"] == "hit"
            and all(str(e.get("ts") or "").startswith("2026-07-13") for _, e in td)
            and load_pending_today(qpath, today="2026-07-14") == []
            and os.path.getmtime(qpath) == q12_mtime)

        # T13 ★교환 축: 신큐 stance=accepts(인정) 소비 → ai 행만(hit) · owner 표본 0.
        write_queue([{"ts": "2026-07-13T10:00:00Z", "stance": "accepts", "queries": [],
                      "ai_answer": "그건 클로드가 맞습니다", "evidence": {"feedback": "클로드맞다."},
                      "consumed": False}])
        r13 = consume(db, ledger, qpath, 0, index=1, home=tmp, ctx={"actor": "human"})
        rows13 = [(r["speaker"], r["outcome"]) for r in (r13.get("rows") or [])]
        rec(13, "교환 소비(accepts+upheld → ai hit 1행만·owner 표본 0)",
            r13.get("consumed") and r13.get("stance") == "accepts"
            and rows13 == [("ai", "hit")])

        # T14 ★교환 축: verdict=overturned(뒤집힌 지적) → owner miss + ai hit ·
        #     invalid verdict → 소비 0(fail-closed).
        write_queue([{"ts": "2026-07-13T11:00:00Z", "stance": "refutes", "queries": [],
                      "evidence": {"feedback": "아니지 그건 다르다"}, "consumed": False}])
        r14bad = consume(db, ledger, qpath, 0, index=1, home=tmp,
                         ctx={"actor": "human"}, verdict="maybe")
        r14 = consume(db, ledger, qpath, 0, index=1, home=tmp,
                      ctx={"actor": "human"}, verdict="overturned")
        rows14 = {(r["speaker"], r["outcome"]) for r in (r14.get("rows") or [])}
        rec(14, "교환 소비(refutes+overturned → owner miss+ai hit · invalid verdict → 소비 0)",
            (not r14bad.get("consumed")) and r14bad.get("reason") == "invalid_verdict"
            and r14.get("consumed") and rows14 == {("owner", "miss"), ("ai", "hit")})

        # T15 ★일괄 소비(도장 1회) — 단일 스냅샷 line_idx 로 번호 재편 없이 3건 소비 ·
        #     범위 밖 포함 시 all-or-nothing 전체 거부.
        write_queue([
            {"ts": "2026-07-13T20:00:00Z", "stance": "refutes", "queries": [],
             "evidence": {"feedback": "일괄 지적 A"}, "consumed": False},
            {"ts": "2026-07-13T20:01:00Z", "stance": "accepts", "queries": [],
             "evidence": {"feedback": "일괄 인정 B"}, "consumed": False},
            {"ts": "2026-07-13T20:02:00Z", "stance": "refutes", "queries": [],
             "evidence": {"feedback": "일괄 지적 C"}, "consumed": False},
        ])
        bad = consume_many(db, ledger, qpath, [0, 1, 9], home=tmp, ctx={"actor": "human"})
        mr = consume_many(db, ledger, qpath, [0, 1, 2], home=tmp, ctx={"actor": "human"})
        pend15 = load_pending(qpath)
        rec(15, "일괄 소비(3건 1도장·all_consumed·대기 0) · 범위 밖 혼입 → 전체 거부",
            (not bad.get("consumed_any")) and bad.get("reason") == "qi_out_of_range"
            and mr.get("all_consumed") and len(mr.get("results")) == 3
            and len(pend15) == 0)

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
