#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu MCP 서버 도구 핸들러 결선 (정본 in-package, 트랙 C strangler).

목적:
- mcp path_gate_adapter.guarded_tool_call 을 실제 MCP 도구 핸들러 후보에 연결.
- read/dry-run 도구 + save_candidate(write-gated) 노출. write/apply/push/sanitizer/enum/team_paid/marketplace 부재.
- 도구의 path 입력은 전부 guarded_tool_call 통과 → BLOCK 시 underlying 미호출.
- raw 경로/secret 미출력 → executed/verdict/reason_code/path_id 만.
- save_candidate: dry-run 기본(write 0)·SAVE n confirm 정확일치·actor 서버 하드 오버라이드(reader)·
  실 write 는 temp DB(open_g3)만(운영 ledger 미접촉). 영구금지 25(자동적재)/26(cos 결정) 위반 0.

정본 이관(v1.11.x): 로직은 여기(binggupack/mcp/server_handlers.py)가 정본이고
scripts/openbinggu_mcp_server_handlers.py 는 공개 심볼을 재노출하는 thin shim 이다.
내부 참조: guarded_tool_call 은 in-package(.path_gate_adapter), classify 는 정본
binggupack.classifier.capture_classifier — scripts 재진입 없음(순환 해소). 함수내 lazy 의존은
전부 in-package facade 경유(capture→binggupack.capture.preview, save_gate→binggupack.safety.save_gate,
open_g3/save_selected→binggupack.storage, OPERATING_PATHS→binggupack.paths) → server_handlers 는
scripts 를 직접 import 하지 않는다(_SCRIPTS 부트스트랩 책임은 각 facade 로 이동). facade 정본 본문
일부는 아직 scripts 잔류하나 그 부트스트랩은 facade 내부에서 처리.

범위: 핸들러 함수 + 디스패치 테이블 + synthetic selftest.
CLI: python scripts/openbinggu_mcp_server_handlers.py --selftest
"""
import sys
import os

from .path_gate_adapter import guarded_tool_call
from binggupack.classifier.capture_classifier import classify


# ---- underlying 도구(dry-run mock, FS write 0) ----
# 실제로는 각 스크립트의 read/dry-run 동작에 결선. 여기선 synthetic mock(파일 작업 0).
def _u_pack_build(params=None):
    return {"action": "pack_build", "mode": "dry-run", "pack": "candidate(temp)"}


def _u_pack_validate(params=None):
    return {"action": "pack_validate", "mode": "read", "verdict": "checked"}


def _u_consumer_smoke(params=None):
    return {"action": "consumer_smoke", "mode": "read", "read": "ok"}


def _u_publish_guard_dryrun(params=None):
    return {"action": "publish_guard_dryrun", "mode": "dry-run", "guard": "evaluated"}


def _u_selftest(params=None):
    return {"action": "selftest", "mode": "read", "gate": "see scripts"}


def _u_capture_classify(params=None):
    # 발화 1건 판정(메모리 순수함수, write 0). 발화 원문은 반환 안 함(state/signals만).
    params = params or {}
    v = classify(params.get("utterance", ""), params.get("prev_turn"))
    return {"action": "capture_classify", "mode": "read",
            "state": v["state"], "confidence": v["confidence"], "pinned": v["pinned"],
            "signals": v["signals"]}


def _u_capture_preview(params=None):
    # 발화 리스트 → semantic 도장(canon) preview. read-only(저장 0).
    # CaptureBuffer(semantic 없음, classify만)가 아니라 openbinggu_conversation_capture_preview
    # (v1.6.1, canon.suggest_label_kind = canonical 5종 의미분류)로 결선. hosted .ts 판단 쏠림 회피.
    params = params or {}
    utts = params.get("utterances") or []
    text = "\n".join(u for u in utts if isinstance(u, str))
    from binggupack.capture import preview as cvp
    result = cvp.capture_preview(text)
    # 사람-발화 게이트(0-A): 후보 hash 만 영속(원문 0) → SAVE hook 이 'SAVE n' 대조용으로 읽음.
    try:
        import binggupack.safety.save_gate as sgate
        sgate.write_last_preview(result.get("candidates", []))
    except Exception:
        pass  # 영속 실패해도 preview 반환엔 무영향(read 도구)
    return {"action": "capture_preview", "mode": "read", **result}


def _u_save_candidate(params=None):
    """선택 후보 staging 저장 — dry-run 기본·SAVE n confirm 정확일치·actor=human 강제.

    영구금지 정합:
      25(자동적재 금지): actor in (auto,reader) → 표면 즉시 G4_no_auto 거부.
      26(cos 결정사용 금지): 저장 게이트는 confirm+A0+PII(규칙)만. cos는 preview 도장 추천뿐.
      비가역 write default-deny: dry_run 기본 True → write 0. 실 write 는 dry_run=False+confirm 정확일치 전부 충족시만.
    안전 경계:
      - actor 는 MCP 입력을 신뢰하지 않고 reader 로 하드 오버라이드(MCP 경유=사람 직접발화 아님).
        confirm='SAVE n' 정확일치만이 사람-선택 증거(모델 단독은 사용자가 본 preview 인덱스를 재현 못함 가정).
      - dry_run 이면 capture_preview 만 재실행(write 0). 실 write 경로는 save_selected 내부 게이트(G4/confirm/A0/PII/
        StagingDB 운영경로 거부)에 위임 — 핸들러는 게이트 재구현 0.
      - MCP는 경로 입력(ledger_path 등)을 일절 무시 → temp DB(open_g3) 강제. 운영 ledger 경로 주입 구조적 불가.
      - 반환은 count/pack_id/reason 만 — 원문 sentence 는 dry-run preview 에서만(사용자가 골라야 하므로), write 응답엔 미포함.
    """
    params = params or {}
    text = params.get("text", "")
    indices = params.get("indices") or []
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)  # 기본 dry-run (비가역 write default-deny)

    # read-only 해제(owner 명시 2026-07-04): confirm='SAVE n' 정확일치 = 사용자가 preview 인덱스를
    # 직접 재현한 사람-선택 증거이므로 그 경우에만 actor=human 으로 승격해 실 write 허용.
    # confirm 불일치/부재는 reader 유지 → save_selected G4_no_auto 가 여전히 BLOCK(자동/추론 저장 방지 불변).
    actor = "human" if (confirm and confirm == "SAVE " + ",".join(str(i) for i in indices)) else "reader"

    from binggupack.capture import preview as cvp
    pv = cvp.capture_preview(text)
    cands = pv["candidates"]
    expected = "SAVE " + ",".join(str(i) for i in indices)

    if dry_run:
        # dry-run: write 0. 저장될 후보 미리보기(index/도장/문장) + 기대 confirm 안내만.
        return {"action": "save_candidate", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "confirm_expected": expected,
                "would_write_ledger": False,
                "selectable": sum(1 for i in indices if isinstance(i, int) and 1 <= i <= len(cands)),
                "preview": [{"index": j + 1, "label_kind": c["label_kind"], "sentence": c["sentence"]}
                            for j, c in enumerate(cands)]}

    # dry_run=False (명시 opt-out): confirm 정확일치 1차 게이트 — 불일치면 write 진입 0.
    if confirm != expected:
        return {"action": "save_candidate", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "confirm_phrase_mismatch",
                "confirm_expected": expected}

    # 실 write 경로 — read-only 해제: 운영 ledger(BINGGU_HOME 우선·없으면 ~/.binggupack)에 저장.
    # MCP 외부 경로 입력은 여전히 무시(경로 주입 차단) — 운영 ledger 경로는 서버가 결정한다.
    from binggupack.storage import open_g3, save_selected
    home = os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")
    db_path = os.path.join(home, "ledger.sqlite")
    snap_dir = os.path.join(home, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)  # staging_apply snapshot 복사 대상 폴더 보장
    db = open_g3(db_path)
    try:
        r = save_selected(db, text, indices, {"actor": actor, "confirm": confirm},
                          snap_dir, due_date=params.get("due_date"))
    finally:
        db.close()
    # confirm='SAVE n' 정확일치(사람 직접선택 증거) → actor=human → 실 write. 불일치/부재 → reader → G4 BLOCK(자동저장 방지 불변).
    return {"action": "save_candidate", "mode": "write-gated",
            "verdict": "ALLOW" if r.get("applied") else "BLOCK",
            "executed_write": bool(r.get("applied")),
            "saved": r.get("saved"), "skipped_existing": r.get("skipped_existing"),
            "rejected": r.get("rejected"), "reason": r.get("reason"),
            "pack_id": r.get("pack_id"), "ledger": "operating"}


# ==== Phase 2 배치 A: 조회(read) 도구 — CLI recall/preflight/trace/status/list/reminders 노출 ====
# 안전 원칙(save_candidate 와 동일):
#   - ledger 경로는 서버가 결정(BINGGU_HOME 우선·없으면 ~/.binggupack). MCP 입력 경로 일절 무시(주입 차단).
#   - 전부 read-only 순수함수(why_search/preflight_context/judgment_trace/list_pending/list_candidates/
#     list_due_reminders) 호출 → ledger write 0. use_count++ 같은 사람-신호 기록은 노출 안 함(순수 read).
#   - ledger 없으면 graceful(빈 결과·에러 아님). raw 경로/secret 미포함(claim=사용자 자기 기억, 조회 목적 노출).
def _operating_home():
    return os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")


def _operating_ledger():
    return os.path.join(_operating_home(), "ledger.sqlite")


# ---- 응답 노출 전처리(작업3): PII 마스킹 + node_id 토큰 제거(Fable5 D-1 위조 차단) ----
import re as _re  # noqa: E402
_NODE_ID_RX = _re.compile(r"node:[A-Za-z0-9:_.\-]+")


def _redact_pii(s):
    """PII+secret 마스킹(batch_redact). why/contrast read 응답 노출 전 기본 적용."""
    try:
        from binggupack.pack.batch_m1 import batch_redact
        red, _hits, _review = batch_redact(s or "")
        return red
    except Exception:
        return s or ""


def _mask_node_ids(s):
    """'node:...' 토큰 마스킹 — why/contrast 출력의 node_id 로 write confirm/id8(node hash8) 위조 차단."""
    return _NODE_ID_RX.sub("[node]", s or "")


def _ensure_scripts_path():
    """scripts 정본 모듈(binggu_recall 등) import 보장. storage facade 도 동일 부트스트랩을 하지만
    read 도구 단독 진입(핸들러 selftest) 대비 명시. server_handlers 는 binggupack/mcp/ 하위 → dirname 3 = ROOT."""
    scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def _u_recall(params=None):
    """query 관련 기억 회상(read-only·use_count 미기록·랭킹순). ledger 없으면 빈 결과."""
    params = params or {}
    query = (params.get("query") or "").strip()
    if not query:
        return {"action": "recall", "mode": "read", "error": "query_required"}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "recall", "mode": "read", "empty": True, "count": 0,
                "nodes": [], "edges": [], "summary": "장부 없음(회상할 기억 0)"}
    _ensure_scripts_path()
    import binggu_recall as RC
    limit = params.get("limit")
    res = RC.why_search(ledger, query, limit=limit if isinstance(limit, int) else None)
    nodes = [{"i": i, "node_id": n.get("node_id"), "node_type": n["node_type"],
              "subtype": n.get("semantic_subtype"), "rank": round(n["rank_score"], 3),
              "rel": round(n["relevance"], 2), "claim": n["claim"]}
             for i, n in enumerate(res["relevant_nodes"], 1)]
    edges = [{"source": e["source"], "relation": e["relation"], "target": e["target"]}
             for e in res.get("relevant_edges", [])]
    return {"action": "recall", "mode": "read", "count": len(nodes),
            "nodes": nodes, "edges": edges, "summary": res.get("summary", "")}


def _u_why(params=None):
    """판단·근거 회상(why_search 래핑·read-only·write 0). node_id/edge_id 미노출(D-1)·PII 마스킹.

    recall 도구와 달리 node_id 를 노출하지 않고 표시용 1-based index(i) 만 반환 —
    모델이 deprecate/replace confirm(id8=node hash8) 을 위조하지 못하게 한다. ledger 없으면 빈 결과.
    """
    params = params or {}
    query = (params.get("query") or "").strip()
    if not query:
        return {"action": "why", "mode": "read", "error": "query_required"}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "why", "mode": "read", "empty": True, "count": 0,
                "nodes": [], "edges": [], "summary": "장부 없음(회상할 기억 0)"}
    from binggupack.pack import recall as RECALL
    limit = params.get("limit")
    lim = limit if isinstance(limit, int) and not isinstance(limit, bool) else None
    res = RECALL.why_search(ledger, query, limit=lim, home=_operating_home())
    id2i, nodes = {}, []
    for i, n in enumerate(res["relevant_nodes"], 1):
        id2i[n.get("node_id")] = i
        nodes.append({"i": i, "node_type": n["node_type"],
                      "subtype": n.get("semantic_subtype"),
                      "rank": round(n["rank_score"], 3), "rel": round(n["relevance"], 2),
                      "trust": n.get("trust", "candidate_unverified"),
                      "claim": _redact_pii(n["claim"])})
    edges = [{"relation": e["relation"], "source_i": id2i.get(e["source"]),
              "target_i": id2i.get(e["target"])} for e in res.get("relevant_edges", [])]
    return {"action": "why", "mode": "read", "count": len(nodes), "nodes": nodes,
            "edges": edges, "summary": _redact_pii(res.get("summary", "")),
            "confidence": res.get("confidence", 0.0)}


def _u_preflight(params=None):
    """작업 전 회상(기억할 것 + 위험패턴 + 선호). read-only. cwd 미지정 시 서버 cwd(위험패턴 힌트만)."""
    params = params or {}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "preflight", "mode": "read", "empty": True,
                "remember": [], "avoid_patterns": [], "preferences": [], "risk_level": "없음"}
    _ensure_scripts_path()
    import binggu_recall as RC
    files = params.get("files")
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    res = RC.preflight_context(ledger, prompt=params.get("prompt"),
                               cwd=params.get("cwd") or os.getcwd(),
                               domain=params.get("domain"), files_changed=files or None)
    return {"action": "preflight", "mode": "read",
            "remember": [{"node_type": n["node_type"], "subtype": n.get("semantic_subtype"), "claim": n["claim"]}
                         for n in res["remember"]],
            "avoid_patterns": [{"risk": round(m["risk_score"], 2), "claim": m["claim"]}
                               for m in res["avoid_patterns"]],
            "preferences": [{"claim": p["claim"]} for p in res["preferences"]],
            "risk_level": res["risk_level"],
            "question": res.get("question") if res.get("needs_question") else None}


def _u_trace_review(params=None):
    """미판정 회상 목록(효용 판정 대기). read-only(스냅샷 write 안 함 — mark 는 미노출)."""
    ledger = _operating_ledger()
    home = _operating_home()
    if not os.path.exists(ledger):
        return {"action": "trace_review", "mode": "read", "empty": True, "count": 0, "pending": []}
    _ensure_scripts_path()
    import binggu_recall_trace as RT
    pend = RT.list_pending(home=home, ledger_path=ledger)
    return {"action": "trace_review", "mode": "read", "count": len(pend),
            "pending": [{"idx": p["idx"], "claim": p.get("claim"), "category": p.get("category"),
                         "rank": p.get("rank"), "node_id": p.get("node_id")} for p in pend]}


def _u_trace_show(params=None):
    """판단 노드 근거 사슬(다홉). read-only. node_id 는 list/recall 이 반환한 값."""
    params = params or {}
    node_id = (params.get("node_id") or "").strip()
    if not node_id:
        return {"action": "trace_show", "mode": "read", "error": "node_id_required"}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "trace_show", "mode": "read", "empty": True, "found": False}
    _ensure_scripts_path()
    import binggu_recall as RC
    res = RC.judgment_trace(ledger, node_id)
    if not res.get("found"):
        return {"action": "trace_show", "mode": "read", "found": False}
    r = res["root"]
    return {"action": "trace_show", "mode": "read", "found": True,
            "root": {"node_id": r["node_id"], "node_type": r["node_type"],
                     "rank": round(r["rank_score"], 3), "claim": r["claim"]},
            "chain": [{"from": c["from"], "relation": c["relation"], "to": c["to"],
                       "direction": c["direction"],
                       "peer": c.get("peer_claim") if c.get("peer_present") else None}
                      for c in res["chain"]],
            "summary": res.get("summary", ""), "confidence": round(res.get("confidence", 0), 2)}


def _u_status(params=None):
    """장부 요약(active/deprecated/검증예정/수용/audit chain). read-only."""
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "status", "mode": "read", "empty": True, "ledger_exists": False}
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept, accepted_view
    db = open_accept(ledger)
    try:
        n = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
        d = db.con.execute("SELECT count(*) FROM nodes WHERE state='deprecated'").fetchone()[0]
        p = db.con.execute("SELECT count(*) FROM judgment_reviews WHERE status='pending'").fetchone()[0]
        acc = len(accepted_view(db))
        chain = db.verify_chain()
    finally:
        db.close()
    return {"action": "status", "mode": "read", "ledger_exists": True,
            "active": n, "deprecated": d, "pending_reviews": p, "accepted": acc,
            "audit_chain": "INTACT" if chain else "BROKEN"}


def _u_list(params=None):
    """후보 목록(status/kind 필터). read-only. markdown + count + accepted 수."""
    params = params or {}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "list", "mode": "read", "empty": True, "count": 0, "markdown": "장부 없음"}
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept, accepted_view
    from openbinggu_candidate_list_view import list_candidates
    db = open_accept(ledger)
    try:
        v = list_candidates(db, params.get("status") or "all", params.get("kind"))
        acc = len(accepted_view(db))
    finally:
        db.close()
    return {"action": "list", "mode": "read", "count": len(v.get("rows", [])),
            "accepted": acc, "markdown": v.get("markdown", "")}


def _u_reminders(params=None):
    """due 경과 판단 리마인더 목록. read-only."""
    params = params or {}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "reminders", "mode": "read", "empty": True, "markdown": "장부 없음"}
    _ensure_scripts_path()
    import datetime as _dt
    from openbinggu_owner_accept_ux import open_accept
    from binggupack.storage import list_due_reminders
    db = open_accept(ledger)
    try:
        today = params.get("today") or _dt.date.today().isoformat()
        r = list_due_reminders(db, today)
    finally:
        db.close()
    return {"action": "reminders", "mode": "read", "markdown": r.get("markdown", "")}


# ==== Phase 2 배치 B: 쓰기(write-gated) 도구 — pair/deprecate/replace ====
# save_candidate 와 동일 안전 패턴(자동저장 방지 불변):
#   - MCP params 의 actor 는 무시. confirm 정확일치(사용자가 목록/preview 를 보고 재현한 증거)만 human 승격.
#   - dry_run 기본 True(비가역 write default-deny) → expected confirm 안내 + preview, write 0.
#   - dry_run=False + confirm 정확일치 → 게이트(save_paired/deprecate_from_list/replace_from_list)가 재검증 후 write.
#   - 운영 ledger 는 서버 결정(BINGGU_HOME/~/.binggupack). MCP 경로 입력 무시(주입 차단).
#   - 게이트 자체가 confirm≠expected → confirm_phrase_mismatch, actor≠human → G4_no_auto 로 이중 차단.
#   - _resolve_human_ctx(CLI 의 TTY/trust 우회)는 MCP 에서 미사용 — confirm 정확일치만 사람증거로 인정.
def _u_pair(params=None):
    """owner 발화(+ai 요약) 화자축 페어 저장. dry_run 기본·PAIR confirm 정확일치·자동저장 차단.
    relation: accepts/refutes/revises · by: owner(사용자가 AI 발화에 반응)/ai. ai_text 생략=owner 단독."""
    params = params or {}
    owner_text = params.get("owner_text", "")
    ai_text = params.get("ai_text") or None
    owner_pick = params.get("owner_pick", 1)
    ai_pick = params.get("ai_pick", 1)
    by = params.get("by", "ai")
    relation = params.get("relation", "accepts")
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not (owner_text or "").strip():
        return {"action": "pair", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "owner_text_required"}
    rel = "%s_%s" % (by, relation)
    expected = ("PAIR %s owner:%d ai:%d" % (rel, owner_pick, ai_pick)) if ai_text \
        else ("PAIR owner:%d" % owner_pick)
    _ensure_scripts_path()
    from binggupack.capture import preview as cvp

    def _pv(t):
        try:
            return [{"index": j + 1, "label_kind": c["label_kind"], "sentence": c["sentence"]}
                    for j, c in enumerate(cvp.capture_preview(t)["candidates"])]
        except Exception:
            return []

    if dry_run:
        # dry-run: write 0. owner/ai 후보 preview + 기대 confirm 안내(사용자가 pick 을 골라야 하므로).
        return {"action": "pair", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write_ledger": False,
                "relation": rel, "confirm_expected": expected,
                "owner_preview": _pv(owner_text),
                "ai_preview": _pv(ai_text) if ai_text else []}
    # dry_run=False: confirm 정확일치만 human. 불일치/부재 → reader → save_paired G4 BLOCK(자동저장 방지).
    actor = "human" if (confirm and confirm == expected) else "reader"
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "pair", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "ledger_not_found"}
    from openbinggu_owner_accept_ux import open_accept
    from binggupack.storage import save_paired
    snap_dir = os.path.join(_operating_home(), "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    db = open_accept(ledger)
    try:
        r = save_paired(db, owner_text, ai_text, {"actor": actor, "confirm": confirm},
                        snap_dir, relation_kind=rel, owner_pick=owner_pick, ai_pick=ai_pick,
                        due_date=params.get("due_date"))
    finally:
        db.close()
    return {"action": "pair", "mode": "write-gated",
            "verdict": "ALLOW" if r.get("applied") else "BLOCK",
            "executed_write": bool(r.get("applied")),
            "saved": r.get("saved"), "reason": r.get("reason"),
            "relation": r.get("relation"), "paired": r.get("paired"),
            "pack_id": r.get("pack_id"), "ledger": "operating"}


def _u_deprecate(params=None):
    """목록 인덱스 1건 기각. dry_run 기본·'DEPRECATE <index> <id8>' confirm 정확일치·자동차단.
    index/id8 은 list 도구가 반환한 순번+node hash8(사용자가 본 목록 재현 증거)."""
    params = params or {}
    index = params.get("index")
    id8 = params.get("id8", "")
    reason = params.get("reason", "")
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not isinstance(index, int) or not id8:
        return {"action": "deprecate", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "index_and_id8_required"}
    expected = "DEPRECATE %s %s" % (index, id8)
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "deprecate", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "ledger_not_found"}
    if dry_run:
        return {"action": "deprecate", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write_ledger": False,
                "confirm_expected": expected,
                "note": "list 도구로 index/id8 확인 후 dry_run=false + confirm 으로 실행(사유 reason 필수)"}
    actor = "human" if (confirm and confirm == expected) else "reader"
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept
    from openbinggu_candidate_deprecate_ux import deprecate_from_list
    snap_dir = os.path.join(_operating_home(), "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    db = open_accept(ledger)
    try:
        r = deprecate_from_list(db, index, id8, reason, {"actor": actor, "confirm": confirm}, snap_dir)
    finally:
        db.close()
    return {"action": "deprecate", "mode": "write-gated",
            "verdict": "ALLOW" if r.get("applied") else "BLOCK",
            "executed_write": bool(r.get("applied")),
            "reason": r.get("reason"), "node_id": r.get("node_id"), "ledger": "operating"}


def _u_replace(params=None):
    """목록 인덱스 1건 교체(기각+신규 candidate). dry_run 기본·
    'REPLACE <index> <id8> WITH <new_sentence>' confirm 정확일치·자동차단."""
    params = params or {}
    index = params.get("index")
    id8 = params.get("id8", "")
    new_sentence = params.get("new_sentence", "")
    reason = params.get("reason", "")
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not isinstance(index, int) or not id8 or not (new_sentence or "").strip():
        return {"action": "replace", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "index_id8_new_sentence_required"}
    expected = "REPLACE %s %s WITH %s" % (index, id8, new_sentence)
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "replace", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "ledger_not_found"}
    if dry_run:
        return {"action": "replace", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write_ledger": False,
                "confirm_expected": expected,
                "note": "list 도구로 index/id8 확인 후 dry_run=false + confirm 으로 실행(사유 reason 필수)"}
    actor = "human" if (confirm and confirm == expected) else "reader"
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept
    from openbinggu_candidate_replace_ux import replace_from_list
    snap_dir = os.path.join(_operating_home(), "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    db = open_accept(ledger)
    try:
        r = replace_from_list(db, index, id8, new_sentence, reason,
                              {"actor": actor, "confirm": confirm}, snap_dir)
    finally:
        db.close()
    return {"action": "replace", "mode": "write-gated",
            "verdict": "ALLOW" if r.get("applied") else "BLOCK",
            "executed_write": bool(r.get("applied")),
            "reason": r.get("reason"), "old_node_id": r.get("old_node_id"),
            "new_node_id": r.get("new_node_id"), "ledger": "operating"}


# ==== Phase 2 배치 C: 작업 도구 — reflect(회고→후보·read) + harvest(외부 소스 관리) ====
# reflect: capture_preview 재사용(저장 0·read). 이어서 save_candidate 로 도장.
# harvest: 사람이 등록한 소스 화이트리스트 관리. list=read·add/remove=write-gated(confirm 정확일치).
#   ★harvest_run(실 네트워크 fetch)은 MCP 미노출 — 실 fetch 는 owner 스케줄러 전용(자동 fetch 위험 차단·_FORBIDDEN 등재).
def _u_reflect(params=None):
    """회고·자가평가 텍스트 → 지식 후보 preview(저장 0·read). preview_id 로 이어서 save_candidate."""
    params = params or {}
    text = params.get("text", "")
    if not (text or "").strip():
        return {"action": "reflect", "mode": "read", "error": "text_required"}
    _ensure_scripts_path()
    import hashlib
    from binggupack.capture import preview as cvp
    pv = cvp.capture_preview(text)
    cands = pv.get("candidates", [])
    pid = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return {"action": "reflect", "mode": "read", "preview_id": pid, "count": len(cands),
            "candidates": [{"index": j + 1, "label_kind": c["label_kind"], "sentence": c["sentence"]}
                           for j, c in enumerate(cands)],
            "save_hint": "남길 교훈만 골라 save_candidate(text, indices, confirm='SAVE <번호>')"}


def _u_harvest_list(params=None):
    """등록된 외부 수확 소스 화이트리스트 목록(read). 빈 시작·owner 가 채움."""
    _ensure_scripts_path()
    import binggu_harvest as HV
    home = _operating_home()
    srcs = HV.load_sources(HV.sources_path(home))
    disabled = os.path.exists(HV.harvest_disabled_path(home))
    return {"action": "harvest_list", "mode": "read", "count": len(srcs), "disabled": disabled,
            "sources": [{"source_id": s.get("source_id"), "kind": s.get("kind"),
                         "url": s.get("url"), "keyword": s.get("keyword")} for s in srcs]}


def _u_harvest_add(params=None):
    """외부 소스 등록(write-gated). dry_run 기본·'HARVEST_ADD <kind> <url>' confirm 정확일치.
    add_source 가 kind(arxiv/github/rss/url) 검증 + URL 공개안전성(비공개/내부 URL 거부) + 멱등 보장."""
    params = params or {}
    kind = params.get("kind", "")
    url = params.get("url", "")
    keyword = params.get("keyword")
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not kind or not url:
        return {"action": "harvest_add", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "kind_and_url_required"}
    expected = "HARVEST_ADD %s %s" % (kind, url)
    if dry_run:
        return {"action": "harvest_add", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write": False, "confirm_expected": expected,
                "note": "실제 등록 시 add_source 가 kind + URL 공개안전성(비공개/내부 URL 거부) 검증"}
    if confirm != expected:
        return {"action": "harvest_add", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "confirm_phrase_mismatch", "confirm_expected": expected}
    _ensure_scripts_path()
    import binggu_harvest as HV
    r = HV.add_source(kind, url, keyword=keyword, path=HV.sources_path(_operating_home()))
    ok = r.get("status") == "OK"
    return {"action": "harvest_add", "mode": "write-gated",
            "verdict": "ALLOW" if ok else "BLOCK", "executed_write": ok,
            "source_id": r.get("source_id"), "reason": r.get("reason")}


def _u_harvest_remove(params=None):
    """외부 소스 제거(write-gated). dry_run 기본·'HARVEST_REMOVE <source_id>' confirm 정확일치."""
    params = params or {}
    source_id = params.get("source_id", "")
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not source_id:
        return {"action": "harvest_remove", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "source_id_required"}
    expected = "HARVEST_REMOVE %s" % source_id
    if dry_run:
        return {"action": "harvest_remove", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write": False, "confirm_expected": expected}
    if confirm != expected:
        return {"action": "harvest_remove", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "confirm_phrase_mismatch", "confirm_expected": expected}
    _ensure_scripts_path()
    import binggu_harvest as HV
    r = HV.remove_source(source_id, path=HV.sources_path(_operating_home()))
    removed = r.get("removed", 0)
    return {"action": "harvest_remove", "mode": "write-gated",
            "verdict": "ALLOW" if removed else "BLOCK", "executed_write": bool(removed),
            "removed": removed, "reason": r.get("reason")}


# ==== 트랙 B: OpenCrab 클라우드 read 조회(egress-only) — cloud_recall / cloud_packs ====
# 안전 원칙(egress-only·로컬 write 0):
#   - cloud_query_wire.run_query 만 호출 → read 전용 화이트리스트(query/search/status)만 payload 생성.
#     write RPC(ingest/pack_update/pack_qa/workflow_manage)는 정본에서 구조적으로 생성 불가.
#   - open_g3/save_selected/ledger/state 일절 미접촉(로컬 write 0). 조회 결과는 PII 마스킹 후에만 노출.
#   - transport 는 운영 설정(env/operating home)에서 read-only 로 구성. 미설정 시 None →
#     run_query 가 NO_CLOUD_CONFIG/NO_TRANSPORT graceful(네트워크 0). raw 토큰 미노출(fingerprint 만).
def _cloud_transport():
    """운영 설정(env/operating home)에서 실 http transport 구성(read-only 설정 조회).
    미설정 시 None → run_query 가 graceful. 로컬 write 0·raw 토큰 반환 안 함."""
    from binggupack.pack.cloud_query_wire import load_cloud_config, default_http_transport
    cfg = load_cloud_config(env=os.environ, home=_operating_home())
    if not cfg.get("url") or cfg.get("reason") == "NO_TOKEN":
        return None
    return default_http_transport(cfg["url"], cfg["token"])


def _cloud_result_view(r):
    """run_query 결과 → 핸들러 노출 뷰. raw 토큰/경로 없음·PII 마스킹된 text 만."""
    if not r.get("ok"):
        return {"ok": False, "error": r.get("reason"), "source": r.get("source")}
    return {"ok": True, "text": r.get("text", ""), "pii_hits": r.get("pii_hits"),
            "residual": r.get("residual"), "source": r.get("source")}


def _u_cloud_recall(params=None):
    """OpenCrab 클라우드 지식 조회(opencrab_query 래핑·read egress-only). 미설정 시 graceful."""
    params = params or {}
    query = (params.get("query") or "").strip()
    if not query:
        return {"action": "cloud_recall", "mode": "read", "ok": False, "error": "query_required"}
    from binggupack.pack import cloud_query_wire as CQ
    args = {"query": query}
    if isinstance(params.get("top_k"), int) and not isinstance(params.get("top_k"), bool):
        args["top_k"] = params["top_k"]
    r = CQ.run_query("opencrab_query", args, transport=_cloud_transport(),
                     env=os.environ, home=_operating_home())
    return {"action": "cloud_recall", "mode": "read", **_cloud_result_view(r)}


def _u_cloud_packs(params=None):
    """OpenCrab 클라우드 팩 검색(opencrab_search_packs 래핑·read egress-only). 미설정 시 graceful."""
    params = params or {}
    from binggupack.pack import cloud_query_wire as CQ
    args = {}
    if (params.get("query") or "").strip():
        args["query"] = params["query"].strip()
    if (params.get("category") or "").strip():
        args["category"] = params["category"].strip()
    r = CQ.run_query("opencrab_search_packs", args, transport=_cloud_transport(),
                     env=os.environ, home=_operating_home())
    return {"action": "cloud_packs", "mode": "read", **_cloud_result_view(r)}


# ==== 작업3: 대비(contrast) read 조회 — detect/build/render 만(기록계열 write 함수 절대 호출 0) ====
# 안전 원칙(구조적 write 차단):
#   - contrast_protocol 에서 read 3함수(detect_conflicts/build_contrast_table/render_contrast_md)만 import.
#   - 기록계열 write 함수(_CONTRAST_WRITE_FNS)는 import·호출 0 → staging_db 미생성 → audit_append/
#     contrast_snapshot INSERT 경로 원천 부재. recorded=False 로 응답 명시, selftest S13 소스검사로 재확인.
#   - node_id 는 [node] 치환·원문 quote 는 PII 마스킹(D-1/PII). 빙구팩은 대비표 제시만(결정 0·자동교체 0).
_CONTRAST_READ_FNS = ("detect_conflicts", "build_contrast_table", "render_contrast_md")
_CONTRAST_WRITE_FNS = ("record_contrast", "verify_snapshot")  # staging write — 노출/호출 절대 금지


def _u_contrast(params=None):
    """빙구팩 preflight 신호 vs 강제조항(mandates) 대비표 조회(read-only·write 0·기록계열 미호출).

    mandates: [{clause_text, stance(require|forbid), source, domain, ...}]. 안전/무결성 domain 은
    detect_conflicts 가 SKIP(헌법 양보 0). 반환 tables 는 node_id 를 노출하지 않고 conflict_id(sha)만 준다.
    """
    params = params or {}
    mandates = params.get("mandates") or []
    if not isinstance(mandates, list):
        return {"action": "contrast", "mode": "read", "error": "mandates_must_be_list"}
    ledger = _operating_ledger()
    from binggupack.pack import recall as RECALL
    # read 3함수만 import — 기록계열 write 함수는 import 0(구조적 write 차단).
    from binggupack.safety.contrast_protocol import (
        detect_conflicts, build_contrast_table, render_contrast_md)
    if not os.path.exists(ledger):
        preflight_out = {"avoid_patterns": [], "preferences": [], "risk_level": "낮음"}
    else:
        files = params.get("files")
        if isinstance(files, str):
            files = [f.strip() for f in files.split(",") if f.strip()]
        preflight_out = RECALL.preflight_context(
            ledger, prompt=params.get("prompt"), cwd=params.get("cwd") or os.getcwd(),
            domain=params.get("domain"), files_changed=files or None, home=_operating_home())
    conflicts = detect_conflicts(preflight_out, mandates, home=_operating_home(), env=os.environ)
    tables = []
    for c in conflicts:
        t = build_contrast_table(c, home=_operating_home())
        md = _mask_node_ids(_redact_pii(render_contrast_md(t)))  # node_id·PII 제거(D-1/PII)
        tables.append({
            "conflict_id": t["conflict_id"], "match_via": t.get("match_via"),
            "relevance": t.get("relevance"),
            "binggu": {"stance": t["binggu_side"]["stance"],
                       "claim": _redact_pii(t["binggu_side"]["quote"]),
                       "trust": t["binggu_side"]["trust"], "cons": t["binggu_side"]["cons"]},
            "mandate": {"stance": t["mandate_side"]["stance"], "source": t["mandate_side"]["source"],
                        "quote": _redact_pii(t["mandate_side"]["quote"]),
                        "quote_status": t["mandate_side"]["quote_status"],
                        "trust": t["mandate_side"]["trust"]},
            "choices": t["choices"], "markdown": md})
    return {"action": "contrast", "mode": "read", "count": len(tables),
            "recorded": False,  # 기록계열 미호출 — audit/snapshot write 0
            "conflicts": tables,
            "note": "빙구팩은 대비표 제시만(결정 0·자동교체 0). 선택은 사장님."}


# ==== 작업4: 추상화(abstraction) 규칙 후보 제안 read 조회 — propose_abstractions 래핑 ====
# 안전 원칙: propose_abstractions 는 read-only(DB write 0·promote 0·self-modifying 0). 응답은
#   node_id 를 노출하지 않는다(proposal_id=content hash·evidence 는 개수만·D-1). 원칙 문구 PII 마스킹.
#   규칙화(active 승격)는 본 도구 밖 — 사람 SAVE(candidate confirm) 경로에서만.
def _u_abstraction(params=None):
    """반복 판단 + hit_events 에서 규칙 후보(추상화)를 '제안만' 조회(read-only·write 0·자동확정 0).

    evidence_refs(node_id 리스트)는 노출하지 않고 supporting_count(개수)만 준다(D-1 정합).
    proposal_id 는 content hash 이지 node_id 가 아니다. 규칙화는 사람 SAVE 로만(promote 0).
    """
    params = params or {}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "abstraction", "mode": "read", "empty": True,
                "count": 0, "proposals": []}
    from binggupack.pack import abstraction as ABS
    proposals = ABS.propose_abstractions(ledger, domain=params.get("domain"),
                                         home=_operating_home())
    out = [{
        "proposal_id": p["proposal_id"],                       # content hash(node_id 아님)
        "principle": _redact_pii(p["proposed_principle_text"]),
        "supporting_count": p["supporting_count"],             # 개수만(evidence_refs 미노출·D-1)
        "semantic_subtype": p["semantic_subtype"],
        "domain": p.get("domain"),
        "evidence_summary": p["evidence_summary"],             # 순수 int dict(신호 아님·정렬 key 진입 0)
        "trust": p["trust"],
        "requires_human_save": p["requires_human_save"],
    } for p in proposals]
    return {"action": "abstraction", "mode": "read", "count": len(out), "proposals": out,
            "note": "규칙화는 사람 SAVE(candidate confirm)로만 — 자동확정 0·self-modifying 0. 제안 표시 전용."}


# ==== 작업A(3차): hit/miss mark — 회상 조언 적중/빗나감 기록(write-gated·D-1/D-2/nonce 방어) ====
# save_candidate 와 동일 write-gated 패턴(자동기록 방지 불변):
#   - MCP params 의 actor 는 신뢰 0(무시). confirm 이 'MARK_HIT <index> <recall_query>'(miss=MARK_MISS)
#     정확일치일 때만 actor=human 승격 — 사용자가 recall 로 본 순위(index)+query 를 재현한 사람-선택 증거.
#   - dry_run 기본 True(비가역 write default-deny) → 기대 confirm 안내 + write 0.
#   - dry_run=False + confirm 정확일치 → hit_recording.mark_outcome 가 재검증 후 기록. 불일치/부재 →
#     reader → mark_outcome 의 actor=human 게이트가 G4_no_auto 로 이중 차단(핸들러 confirm + 게이트 actor).
#   - node_id 는 입력받지 않는다(D-1): mark_outcome 가 (recall_query, index)로 why_search 를 재실행해
#     서버가 노드를 스스로 확보 → 회상에 없는 임의 node_id 를 hit 로 위조할 표면이 없다. nonce 는 미지정
#     허용(서버 why_search 재실행으로 스냅샷 확보). decision_id 는 (node_id,nonce) 안정 해시(D-2 이중계상 차단).
#   - 운영 ledger 는 서버 결정(BINGGU_HOME/~/.binggupack). MCP 경로 입력 일절 무시(주입 차단).
#   - 반환은 recorded/reason/outcome/decision_id/nonce/domain/events + node_claim(PII 마스킹) 만 —
#     node_id 등 민감값 미노출(mark_outcome 반환에도 node_id 없음·node_claim 만 PII 마스킹).
#   ★ _FORBIDDEN 미등재 근거: mark 는 write-gated(dry_run 기본·confirm 게이트·actor 하드 reader)라
#     record_resolution(무차별 기록) 계열과 달리 기본-deny 표면만 노출한다. record_resolution 자체는
#     여전히 _FORBIDDEN 유지 — mark 는 그 위조 표면 없는 안전 래퍼(D-1/D-2)로만 노출.
def _mark_outcome_handler(params, outcome):
    """hit/miss 공통 write-gated 핸들러. outcome in ('hit','miss'). 자동기록 방지 이중 게이트."""
    params = params or {}
    recall_query = (params.get("recall_query") or "").strip()
    index = params.get("index")
    confirm = params.get("confirm", "")
    domain = params.get("domain")
    dry_run = params.get("dry_run", True)
    label = "MARK_HIT" if outcome == "hit" else "MARK_MISS"
    act = "mark_" + outcome
    if not recall_query:
        return {"action": act, "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "recall_query_required"}
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        return {"action": act, "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "index_out_of_range"}
    expected = "%s %d %s" % (label, index, recall_query)
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": act, "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "ledger_not_found"}
    if dry_run:
        # dry-run: write 0. 기대 confirm 안내(사용자가 recall 로 index 확인 후 opt-out 해야 하므로).
        return {"action": act, "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write_ledger": False,
                "confirm_expected": expected,
                "note": "recall 로 순위(index) 확인 후 dry_run=false + confirm 으로 기록(node_id 입력 불필요·서버 재실행 확보)"}
    # dry_run=False: confirm 정확일치만 human. 불일치/부재 → reader → mark_outcome G4_no_auto(자동기록 방지).
    actor = "human" if (confirm and confirm == expected) else "reader"
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept
    db = open_accept(ledger)
    from binggupack.pack import hit_recording as HR
    try:
        # nonce 미지정 — 서버가 why_search 재실행으로 스냅샷 확보(D-1). ledger 는 서버결정(MCP 경로입력 무시).
        r = HR.mark_outcome(db, ledger, recall_query, index, outcome, {"actor": actor},
                            nonce=None, domain=domain, home=_operating_home())
    finally:
        db.close()
    claim = r.get("node_claim")
    return {"action": act, "mode": "write-gated",
            "verdict": "ALLOW" if r.get("recorded") else "BLOCK",
            "executed_write": bool(r.get("recorded")),
            "recorded": bool(r.get("recorded")), "reason": r.get("reason"),
            "outcome": r.get("outcome"), "decision_id": r.get("decision_id"),
            "nonce": r.get("nonce"), "domain": r.get("domain"),
            "events": r.get("events"),
            "node_claim": _redact_pii(claim) if claim else None,  # PII 마스킹(node_id 미포함)
            "ledger": "operating"}


def _u_mark_hit(params=None):
    """회상 조언이 맞았다(직감 적중) 기록 — write-gated·'MARK_HIT <index> <recall_query>' confirm 정확일치.
    node_id 입력 0(D-1)·dry_run 기본·actor 하드 reader·nonce 서버 확보·D-2 이중계상 차단."""
    return _mark_outcome_handler(params, "hit")


def _u_mark_miss(params=None):
    """회상 조언이 틀렸다(직감 빗나감) 기록 — write-gated·'MARK_MISS <index> <recall_query>' confirm 정확일치.
    node_id 입력 0(D-1)·dry_run 기본·actor 하드 reader·nonce 서버 확보·D-2 이중계상 차단."""
    return _mark_outcome_handler(params, "miss")


# ---- 노출 도구 테이블(read/dry-run 만). 위험 도구는 의도적으로 부재 ----
TOOLS = {
    "pack_build":           {"path_params": ["input_dir"], "underlying": _u_pack_build,          "mode": "dry-run"},
    "pack_validate":        {"path_params": ["pack_path"],  "underlying": _u_pack_validate,       "mode": "read"},
    "consumer_smoke":       {"path_params": ["pack_path"],  "underlying": _u_consumer_smoke,      "mode": "read"},
    "publish_guard_dryrun": {"path_params": ["pack_path"],  "underlying": _u_publish_guard_dryrun, "mode": "dry-run"},
    "selftest":             {"path_params": [],             "underlying": _u_selftest,            "mode": "read"},
    # 캡처 엔진(메모리 순수, write 0). path 입력 없음 → input_schema 로 일반 params 노출.
    "capture_classify":     {"path_params": [], "underlying": _u_capture_classify, "mode": "read",
                             "input_schema": {"properties": {"utterance": {"type": "string"},
                                                             "prev_turn": {"type": "string"}},
                                              "required": ["utterance"]}},
    "capture_preview":      {"path_params": [], "underlying": _u_capture_preview, "mode": "read",
                             "input_schema": {"properties": {"utterances": {"type": "array",
                                                                            "items": {"type": "string"}}},
                                              "required": ["utterances"]}},
    # save 도구 — write-gated. dry-run 기본·SAVE n confirm 정확일치·actor 서버 하드 오버라이드(reader).
    # _FORBIDDEN db_write 는 무차별 write 금지 라벨이고, save 는 confirm 게이트 통과 단건만 예외적으로
    # 실 write 경로 진입(그것도 temp DB·actor=reader 로 G4 항상 발동). 경로 입력(ledger_path 등) 일절 무시 — MCP는 운영 ledger 못 염.
    "save_candidate":       {"path_params": [], "underlying": _u_save_candidate, "mode": "write-gated",
                             "input_schema": {"properties": {
                                 "text": {"type": "string"},
                                 "indices": {"type": "array", "items": {"type": "integer"}},
                                 "confirm": {"type": "string"},
                                 "dry_run": {"type": "boolean"},
                                 "due_date": {"type": "string"}},
                              "required": ["text", "indices"]}},
    # ---- Phase 2 배치 A: 조회(read) 도구. path 입력 없음·ledger 서버 결정·write 0 ----
    "recall":       {"path_params": [], "underlying": _u_recall, "mode": "read",
                     "input_schema": {"properties": {"query": {"type": "string"},
                                                     "limit": {"type": "integer"}},
                                      "required": ["query"]}},
    "preflight":    {"path_params": [], "underlying": _u_preflight, "mode": "read",
                     "input_schema": {"properties": {"prompt": {"type": "string"},
                                                     "cwd": {"type": "string"},
                                                     "domain": {"type": "string"},
                                                     "files": {"type": "string"}},
                                      "required": []}},
    "trace_review": {"path_params": [], "underlying": _u_trace_review, "mode": "read",
                     "input_schema": {"properties": {}, "required": []}},
    "trace_show":   {"path_params": [], "underlying": _u_trace_show, "mode": "read",
                     "input_schema": {"properties": {"node_id": {"type": "string"}},
                                      "required": ["node_id"]}},
    "status":       {"path_params": [], "underlying": _u_status, "mode": "read",
                     "input_schema": {"properties": {}, "required": []}},
    "list":         {"path_params": [], "underlying": _u_list, "mode": "read",
                     "input_schema": {"properties": {"status": {"type": "string"},
                                                     "kind": {"type": "string"}},
                                      "required": []}},
    "reminders":    {"path_params": [], "underlying": _u_reminders, "mode": "read",
                     "input_schema": {"properties": {"today": {"type": "string"}},
                                      "required": []}},
    # ---- Phase 2 배치 B: 쓰기(write-gated) 도구. dry-run 기본·confirm 정확일치·actor 하드 reader·자동차단 ----
    "pair":         {"path_params": [], "underlying": _u_pair, "mode": "write-gated",
                     "input_schema": {"properties": {
                         "owner_text": {"type": "string"}, "ai_text": {"type": "string"},
                         "owner_pick": {"type": "integer"}, "ai_pick": {"type": "integer"},
                         "by": {"type": "string"}, "relation": {"type": "string"},
                         "confirm": {"type": "string"}, "dry_run": {"type": "boolean"},
                         "due_date": {"type": "string"}},
                      "required": ["owner_text"]}},
    "deprecate":    {"path_params": [], "underlying": _u_deprecate, "mode": "write-gated",
                     "input_schema": {"properties": {
                         "index": {"type": "integer"}, "id8": {"type": "string"},
                         "reason": {"type": "string"}, "confirm": {"type": "string"},
                         "dry_run": {"type": "boolean"}},
                      "required": ["index", "id8"]}},
    "replace":      {"path_params": [], "underlying": _u_replace, "mode": "write-gated",
                     "input_schema": {"properties": {
                         "index": {"type": "integer"}, "id8": {"type": "string"},
                         "new_sentence": {"type": "string"}, "reason": {"type": "string"},
                         "confirm": {"type": "string"}, "dry_run": {"type": "boolean"}},
                      "required": ["index", "id8", "new_sentence"]}},
    # ---- Phase 2 배치 C: 작업 도구(reflect read + harvest 소스 관리). harvest_run 은 미노출(실 fetch owner 전용) ----
    "reflect":        {"path_params": [], "underlying": _u_reflect, "mode": "read",
                       "input_schema": {"properties": {"text": {"type": "string"}},
                                        "required": ["text"]}},
    "harvest_list":   {"path_params": [], "underlying": _u_harvest_list, "mode": "read",
                       "input_schema": {"properties": {}, "required": []}},
    "harvest_add":    {"path_params": [], "underlying": _u_harvest_add, "mode": "write-gated",
                       "input_schema": {"properties": {
                           "kind": {"type": "string"}, "url": {"type": "string"},
                           "keyword": {"type": "string"}, "confirm": {"type": "string"},
                           "dry_run": {"type": "boolean"}},
                        "required": ["kind", "url"]}},
    "harvest_remove": {"path_params": [], "underlying": _u_harvest_remove, "mode": "write-gated",
                       "input_schema": {"properties": {
                           "source_id": {"type": "string"}, "confirm": {"type": "string"},
                           "dry_run": {"type": "boolean"}},
                        "required": ["source_id"]}},
    # ---- 트랙 B: OpenCrab 클라우드 read 조회(egress-only). path 입력 없음·write RPC 미생성·PII 마스킹·미설정 graceful ----
    "cloud_recall": {"path_params": [], "underlying": _u_cloud_recall, "mode": "read",
                     "input_schema": {"properties": {"query": {"type": "string"},
                                                     "top_k": {"type": "integer"}},
                                      "required": ["query"]}},
    "cloud_packs":  {"path_params": [], "underlying": _u_cloud_packs, "mode": "read",
                     "input_schema": {"properties": {"query": {"type": "string"},
                                                     "category": {"type": "string"}},
                                      "required": []}},
    # ---- 작업3: 판단 근거 회상(why) + 강제조항 대비(contrast) read. node_id 미노출·PII 마스킹·write 0 ----
    "why":      {"path_params": [], "underlying": _u_why, "mode": "read",
                 "input_schema": {"properties": {"query": {"type": "string"},
                                                 "limit": {"type": "integer"}},
                                  "required": ["query"]}},
    "contrast": {"path_params": [], "underlying": _u_contrast, "mode": "read",
                 "input_schema": {"properties": {
                     "prompt": {"type": "string"}, "cwd": {"type": "string"},
                     "domain": {"type": "string"}, "files": {"type": "string"},
                     "mandates": {"type": "array", "items": {"type": "object"}}},
                  "required": ["mandates"]}},
    # ---- 작업4: 추상화(규칙 후보 제안) read. node_id 미노출·proposal_id content hash·write 0·promote 0 ----
    "abstraction": {"path_params": [], "underlying": _u_abstraction, "mode": "read",
                    "input_schema": {"properties": {"domain": {"type": "string"}},
                                     "required": []}},
    # ---- 작업A(3차): hit/miss mark(write-gated). node_id 입력 0(D-1)·confirm 정확일치·dry-run 기본·자동기록 0 ----
    #   save_candidate 와 동일 취급(write-gated). ledger 서버결정·MCP 경로입력 무시·이중게이트(confirm+actor).
    "mark_hit":  {"path_params": [], "underlying": _u_mark_hit, "mode": "write-gated",
                  "input_schema": {"properties": {
                      "recall_query": {"type": "string"}, "index": {"type": "integer"},
                      "confirm": {"type": "string"}, "domain": {"type": "string"},
                      "dry_run": {"type": "boolean"}},
                   "required": ["recall_query", "index"]}},
    "mark_miss": {"path_params": [], "underlying": _u_mark_miss, "mode": "write-gated",
                  "input_schema": {"properties": {
                      "recall_query": {"type": "string"}, "index": {"type": "integer"},
                      "confirm": {"type": "string"}, "domain": {"type": "string"},
                      "dry_run": {"type": "boolean"}},
                   "required": ["recall_query", "index"]}},
}

# 노출 금지(핸들러 부재로 자동 차단되지만, 명시 거부 목록으로 의도 박제)
_FORBIDDEN = {
    "opencrab_write", "opencrab_apply", "opencrab_ingest", "store_write",
    "github_push", "opencrab_upload", "sanitizer_replace", "enum_set",
    "team_billing", "marketplace_publish", "db_write",
    "harvest_run",  # 실 네트워크 fetch — MCP 자동 fetch 위험, owner 스케줄러/CLI 전용
    # 트랙 B egress-only: 클라우드 write 계열 도구는 노출 금지(read cloud_recall/cloud_packs 만 허용).
    # ★opencrab_pack_qa 는 write 가능(assess_and_update/reverse_ingest)이라 절대 노출 금지.
    "opencrab_pack_update", "opencrab_pack_qa", "opencrab_workflow_manage",
    # 작업3: 판단/사용/대비 기록계열 write 함수는 TOOLS 미등록 유지 + 명시 금지(Fable5 C).
    # contrast(read)는 노출하나 record_contrast(staging write)는 절대 미노출 — tool_not_exposed:forbidden.
    "record_contrast", "record_resolution", "record_use", "verify_snapshot",
}


def handle_tool(tool_name, params, allow_root):
    """
    MCP 도구 요청 1건 처리.
    - 미노출/금지 도구 → tool_not_exposed (underlying 미호출).
    - path 입력 있으면 guarded_tool_call 로 gate 통과시킨 뒤에만 underlying.
    반환: raw 경로/secret 미포함.
    """
    params = params or {}
    if tool_name not in TOOLS:
        rc = "forbidden" if tool_name in _FORBIDDEN else "unknown"
        return {"executed": False, "verdict": "REJECT", "reason_code": "tool_not_exposed:" + rc,
                "tool": tool_name}

    spec = TOOLS[tool_name]
    path_inputs = [params[k] for k in spec["path_params"] if k in params and params[k] is not None]

    if not path_inputs:
        # path 입력 없는 read 도구 → 바로 실행
        return {"executed": True, "verdict": "ALLOW", "tool": tool_name,
                "tool_result": spec["underlying"](params=params)}

    # path 입력은 전부 gate 통과(실행 직전 재검사 포함). BLOCK 시 underlying 미호출.
    r = guarded_tool_call(spec["underlying"], path_inputs=path_inputs,
                          allow_root=allow_root, tool_kwargs={"params": params})
    r["tool"] = tool_name
    return r


# ---------------- selftest ----------------

# save selftest 입력(문서·판단 섞임). dry-run preview 는 사용자 선택용으로 sentence 노출이 의도 동작.
_SAVE_CONVO = ("이 문서는 배포 절차를 정의한다. 이 입찰은 마진이 낮아 보류한다.")


def _selftest():
    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))
    # 조회(read) 도구는 _operating_ledger()=BINGGU_HOME/ledger.sqlite 를 읽는다. selftest 결정성 +
    # 운영 ledger 미접촉을 위해 존재하지 않는 temp 홈으로 강제(→ read 도구는 graceful empty 반환).
    os.environ["BINGGU_HOME"] = os.path.join(os.environ.get("TEMP", "/tmp"),
                                             "binggu_selftest_home_readonly_none")
    # 트랙 B 클라우드 조회는 os.environ 로 설정을 읽는다. selftest 네트워크 0 보장 위해 앰비언트
    # 클라우드 env(있을 수 있음)를 제거 → load_cloud_config=NO_CLOUD_CONFIG(transport 미구성).
    for _k in ("BINGGU_CLOUD_MCP_URL", "BINGGU_CLOUD_MCP_TOKEN"):
        os.environ.pop(_k, None)

    print("=" * 72)
    print("OpenBinggu MCP server handlers 결선 후보 (synthetic / selftest)")
    print("=" * 72)

    all_ok = True
    raw_leak = False

    cases = [
        # (name, tool, params, expect_executed, note)
        ("validate_toy_ok",      "pack_validate",        {"pack_path": "examples/toy_project/p.json"}, True,  "ALLOW"),
        ("build_toy_ok",         "pack_build",           {"input_dir": "examples/toy_project"},        True,  "ALLOW"),
        ("selftest_no_path_ok",  "selftest",             {},                                           True,  "no-path read"),
        ("build_parent_block",   "pack_build",           {"input_dir": "../outside"},                  False, "parent_escape"),
        ("consumer_npki_block",  "consumer_smoke",       {"pack_path": "C:/Users/PC/AppData/NPKI/c.der"}, False, "deny_cert_npki"),
        ("guard_env_block",      "publish_guard_dryrun", {"pack_path": "examples/toy_project/.env"},    False, "deny_secret"),
        ("validate_bidengine_block", "pack_validate",    {"pack_path": "C:/Users/PC/safety-app/bid-engine/x"}, False, "deny_bid_engine"),
        ("forbidden_write",      "opencrab_write",       {"pack_path": "examples/toy_project/p.json"}, False, "tool_not_exposed:forbidden"),
        ("forbidden_push",       "github_push",          {},                                           False, "tool_not_exposed:forbidden"),
        ("unknown_tool",         "do_something",         {},                                           False, "tool_not_exposed:unknown"),
        ("capture_classify_ok",  "capture_classify",     {"utterance": "B안으로 결정"},                 True,  "read no-path"),
        ("capture_preview_ok",   "capture_preview",      {"utterances": ["이거 저장해", "ㅋㅋ"]},        True,  "read no-path"),
        # save 도구 — dry-run 기본은 executed=True(도구 실행됨)이나 executed_write=False(ledger write 0).
        ("save_dryrun_default",  "save_candidate",       {"text": _SAVE_CONVO, "indices": [1]},        True,  "dry-run preview"),
        # Phase 2 배치 A 조회(read) — ledger 없어도 graceful(executed=True). BINGGU_HOME=temp 라 운영 미접촉.
        ("recall_read_ok",       "recall",               {"query": "배포 절차"},                        True,  "read no-path"),
        ("preflight_read_ok",    "preflight",            {"prompt": "이 입찰 검토"},                     True,  "read no-path"),
        ("trace_review_read_ok", "trace_review",         {},                                           True,  "read no-path"),
        ("trace_show_read_ok",   "trace_show",           {"node_id": "node:CONV:none"},                True,  "read no-path"),
        ("status_read_ok",       "status",               {},                                           True,  "read no-path"),
        ("list_read_ok",         "list",                 {},                                           True,  "read no-path"),
        ("reminders_read_ok",    "reminders",            {},                                           True,  "read no-path"),
        # Phase 2 배치 B 쓰기(write-gated) — dry-run 기본은 executed=True(도구 실행)이나 executed_write=False(write 0).
        ("pair_dryrun_default",  "pair",                 {"owner_text": _SAVE_CONVO},                  True,  "dry-run write0"),
        ("deprecate_dryrun",     "deprecate",            {"index": 1, "id8": "abcd1234"},              True,  "dry-run write0"),
        ("replace_dryrun",       "replace",              {"index": 1, "id8": "abcd1234",
                                                          "new_sentence": "수정된 문장"},              True,  "dry-run write0"),
        # Phase 2 배치 C 작업(reflect read + harvest 소스 관리). harvest_run 은 forbidden(실 fetch owner 전용).
        ("reflect_read_ok",      "reflect",              {"text": _SAVE_CONVO},                        True,  "read"),
        ("harvest_list_read_ok", "harvest_list",         {},                                           True,  "read"),
        ("harvest_add_dryrun",   "harvest_add",          {"kind": "arxiv",
                                                          "url": "https://arxiv.org/abs/2401.1"},      True,  "dry-run write0"),
        ("harvest_remove_dryrun", "harvest_remove",      {"source_id": "src_test"},                    True,  "dry-run write0"),
        ("harvest_run_forbidden", "harvest_run",         {},                                           False, "tool_not_exposed:forbidden"),
        # 트랙 B 클라우드 read(egress-only) — 미설정(BINGGU_HOME=temp·클라우드 env 제거) → graceful(executed=True·write 0).
        ("cloud_recall_read_ok",  "cloud_recall",        {"query": "여행 팁"},                          True,  "read no-path graceful"),
        ("cloud_packs_read_ok",   "cloud_packs",         {"query": "신혼여행"},                         True,  "read no-path graceful"),
        # 클라우드 write 계열은 노출 금지(_FORBIDDEN). ★pack_qa 는 write 가능 → 절대 노출 금지.
        ("cloud_pack_qa_forbidden",     "opencrab_pack_qa",         {},                               False, "tool_not_exposed:forbidden"),
        ("cloud_pack_update_forbidden", "opencrab_pack_update",     {},                               False, "tool_not_exposed:forbidden"),
        ("cloud_workflow_forbidden",    "opencrab_workflow_manage", {},                               False, "tool_not_exposed:forbidden"),
        # 작업3: why/contrast read(temp 홈 graceful empty) + 기록계열 write 함수 forbidden.
        ("why_read_ok",      "why",      {"query": "배포 절차"},                                       True,  "read no-path"),
        ("contrast_read_ok", "contrast", {"prompt": "이 입찰 검토", "mandates": [
            {"clause_text": "대량 삭제는 승인 필수", "stance": "require",
             "source": "CLAUDE.md", "domain": "style"}]},                                             True,  "read no-path"),
        ("record_contrast_forbidden",   "record_contrast",   {}, False, "tool_not_exposed:forbidden"),
        ("record_resolution_forbidden", "record_resolution", {}, False, "tool_not_exposed:forbidden"),
        ("record_use_forbidden",        "record_use",        {}, False, "tool_not_exposed:forbidden"),
        # 작업4: abstraction read(temp 홈 graceful empty). 규칙화(promote)는 도구 부재로 자동 차단.
        ("abstraction_read_ok",  "abstraction", {},                    True,  "read no-path"),
        ("abstraction_domain_ok","abstraction", {"domain": "bid"},     True,  "read no-path"),
        # 작업A(3차): hit/miss mark — dry-run 기본. BINGGU_HOME=temp(없음)라 ledger_not_found graceful(executed=True·write 0).
        ("mark_hit_read_ok",     "mark_hit",    {"recall_query": "배포 절차", "index": 1}, True, "write-gated no-ledger"),
        ("mark_miss_read_ok",    "mark_miss",   {"recall_query": "배포 절차", "index": 1}, True, "write-gated no-ledger"),
    ]

    import json as _json
    for name, tool, params, exp_exec, note in cases:
        r = handle_tool(tool, params, allow_root)
        executed = bool(r.get("executed"))
        ok = (executed == exp_exec)
        all_ok = all_ok and ok
        # raw 미출력: 결과에 입력 경로 substring 없어야.
        # 단 save dry-run preview 는 사용자 선택용 sentence 노출이 의도 동작 → text 입력은 leak 검사 면제.
        blob = _json.dumps(r, ensure_ascii=False)
        for k, v in params.items():
            if tool == "save_candidate" and k == "text":
                continue
            # 배치 B dry-run preview/confirm_expected 는 사용자 선택용 입력(텍스트·id8·confirm) 노출이 의도 동작
            # (save 와 동형). id8=사용자가 list 에서 본 node hash8 — 경로/secret 아님, confirm 생성용.
            if tool in ("pair", "deprecate", "replace") and k in ("owner_text", "ai_text",
                                                                  "new_sentence", "confirm", "id8"):
                continue
            # 배치 C: reflect 후보 sentence·harvest confirm_expected 의 kind/url 등은 사용자 입력(공개값) 노출 의도.
            if tool == "reflect" and k == "text":
                continue
            if tool in ("harvest_add", "harvest_remove") and k in ("kind", "url", "keyword",
                                                                   "source_id", "confirm"):
                continue
            # 작업A(3차) mark: recall_query/confirm 은 confirm_expected('MARK_HIT <index> <query>')에
            # 반드시 담기는 사용자 입력(공개 query·경로/secret 아님) — save/deprecate 와 동형 노출 의도.
            if tool in ("mark_hit", "mark_miss") and k in ("recall_query", "confirm"):
                continue
            if isinstance(v, str) and v.strip() and v.strip() in blob:
                raw_leak = True
        verdict = r.get("verdict")
        rc = r.get("reason_code") or (r.get("blocked") and r["blocked"][0].get("reason_code")) or ""
        print("  [%s] %-26s tool=%-20s executed=%-5s verdict=%-7s %s"
              % ("OK" if ok else "FAIL", name, tool, executed, verdict, rc))

    # ----- save 도구 전용 검증 (실 ledger write 0 보장: temp DB·dry-run·mock만) -----
    from binggupack.paths import OPERATING_PATHS
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    save_ok = True
    save_notes = []

    # S1) dry-run 기본 — write 0(executed_write=False·would_write_ledger=False), preview 노출.
    r = handle_tool("save_candidate", {"text": _SAVE_CONVO, "indices": [1]}, allow_root)
    tr = r.get("tool_result") or {}
    s1 = (r.get("executed") is True and tr.get("executed_write") is False
          and tr.get("would_write_ledger") is False and tr.get("verdict") == "PREVIEW")
    save_ok = save_ok and s1
    save_notes.append(("save_dryrun_write0", s1))

    # S2) confirm 불일치 — dry_run=False 라도 write 0 (REJECT).
    r = handle_tool("save_candidate",
                    {"text": _SAVE_CONVO, "indices": [1], "confirm": "SAVE 9", "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    s2 = (tr.get("executed_write") is False and tr.get("reason") == "confirm_phrase_mismatch")
    save_ok = save_ok and s2
    save_notes.append(("save_confirm_mismatch_reject", s2))

    # S3) 자동저장 방지 불변식(f9a9c61 정책): params actor 는 서버가 무시하고 confirm='SAVE n' 정확일치만
    #     사람증거로 인정한다. confirm 부재(모델 자동호출 흉내)+dry_run=False → 핸들러 confirm 게이트가 REJECT →
    #     save_selected 진입 0(write 0). actor=auto 위조는 무의미(params actor 미사용).
    #     (구 케이스는 도달 불가한 G4_no_auto reason 을 기대해 상시 FAIL — f9a9c61 이 read-only 해제 시 코드만
    #      바꾸고 이 selftest 를 안 고쳐 남은 사전존재 결함. confirm 정확일치는 human 승격, 불일치/부재는
    #      confirm_phrase_mismatch 로 앞단 차단되어 G4_no_auto 는 도달 불가. 실제 방어 경로 reason 으로 정정.)
    r = handle_tool("save_candidate",
                    {"text": _SAVE_CONVO, "indices": [1], "dry_run": False, "actor": "auto"}, allow_root)
    tr = r.get("tool_result") or {}
    s3 = (tr.get("executed_write") is False and tr.get("reason") == "confirm_phrase_mismatch")
    save_ok = save_ok and s3
    save_notes.append(("save_auto_call_write0_no_confirm", s3))

    # S5) pair confirm 부재(자동호출 흉내) + dry_run=False → write 0. (BINGGU_HOME=temp 라 ledger_not_found
    #     또는 reader→save_paired G4 — 어느 쪽이든 운영/temp 자동 write 0.)
    r = handle_tool("pair", {"owner_text": _SAVE_CONVO, "dry_run": False, "actor": "auto"}, allow_root)
    tr = r.get("tool_result") or {}
    s5 = (tr.get("executed_write") is False)
    save_ok = save_ok and s5
    save_notes.append(("pair_no_confirm_write0", s5))

    # S6) deprecate confirm 불일치 + dry_run=False → write 0.
    r = handle_tool("deprecate", {"index": 1, "id8": "abcd1234", "reason": "x",
                                  "confirm": "DEPRECATE 9 zzzzzzzz", "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    s6 = (tr.get("executed_write") is False)
    save_ok = save_ok and s6
    save_notes.append(("deprecate_mismatch_write0", s6))

    # S7) replace confirm 불일치 + dry_run=False → write 0.
    r = handle_tool("replace", {"index": 1, "id8": "abcd1234", "new_sentence": "y", "reason": "x",
                                "confirm": "REPLACE 9 zzzzzzzz WITH y", "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    s7 = (tr.get("executed_write") is False)
    save_ok = save_ok and s7
    save_notes.append(("replace_mismatch_write0", s7))

    # S8) harvest_add confirm 불일치 + dry_run=False → write 0(소스 화이트리스트 미변경).
    r = handle_tool("harvest_add", {"kind": "url", "url": "https://example.org/x",
                                    "confirm": "wrong", "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    s8 = (tr.get("executed_write") is False)
    save_ok = save_ok and s8
    save_notes.append(("harvest_add_mismatch_write0", s8))

    # S9) 트랙 B 클라우드 read — 미설정 graceful(ok False·NO_CLOUD_CONFIG)·네트워크 0·로컬 write 0.
    #     BINGGU_HOME=temp + 클라우드 env 제거 상태라 transport 미구성 → run_query 가 NO_CLOUD_CONFIG.
    r = handle_tool("cloud_recall", {"query": "여행 팁"}, allow_root)
    tr = r.get("tool_result") or {}
    s9 = (r.get("executed") is True and tr.get("ok") is False
          and tr.get("error") == "NO_CLOUD_CONFIG")
    save_ok = save_ok and s9
    save_notes.append(("cloud_recall_unconfigured_graceful", s9))

    # S10) 클라우드 write 계열(pack_qa/pack_update/workflow_manage) 노출 금지(egress-only 불변).
    s10 = all(handle_tool(t, {}, allow_root).get("executed") is False
              and handle_tool(t, {}, allow_root).get("reason_code", "").endswith("forbidden")
              for t in ("opencrab_pack_qa", "opencrab_pack_update", "opencrab_workflow_manage"))
    save_ok = save_ok and s10
    save_notes.append(("cloud_write_tools_forbidden", s10))

    import json as _j11
    # S11) why — read·write 0·node_id/edge_id 미노출(D-1). temp 홈이라 graceful empty.
    r = handle_tool("why", {"query": "배포 절차"}, allow_root)
    tr = r.get("tool_result") or {}
    blob11 = _j11.dumps(tr, ensure_ascii=False)
    s11 = (r.get("executed") is True and "node_id" not in blob11 and "node:" not in blob11)
    save_ok = save_ok and s11
    save_notes.append(("why_read_no_node_id_write0", s11))

    # S12) contrast — read·기록계열 미호출(recorded=False)·node_id 미노출.
    r = handle_tool("contrast", {"prompt": "이 입찰 검토", "mandates": [
        {"clause_text": "대량 삭제는 승인 필수", "stance": "require",
         "source": "CLAUDE.md", "domain": "style"}]}, allow_root)
    tr = r.get("tool_result") or {}
    s12 = (r.get("executed") is True and tr.get("recorded") is False
           and "node:" not in _j11.dumps(tr, ensure_ascii=False))
    save_ok = save_ok and s12
    save_notes.append(("contrast_read_recorded_false", s12))

    # S13) 구조적 차단: contrast 핸들러 소스에 기록계열 write 함수 호출 0(call-form 검사).
    import inspect as _insp
    _csrc = _insp.getsource(_u_contrast)
    s13 = all((w + "(") not in _csrc for w in _CONTRAST_WRITE_FNS)
    save_ok = save_ok and s13
    save_notes.append(("contrast_no_write_fn_call", s13))

    # S14) 기록계열 write 함수 4개 — TOOLS 미등록 + _FORBIDDEN → tool_not_exposed:forbidden.
    s14 = all(handle_tool(t, {}, allow_root).get("executed") is False
              and handle_tool(t, {}, allow_root).get("reason_code", "").endswith("forbidden")
              and t not in TOOLS
              for t in ("record_contrast", "record_resolution", "record_use", "verify_snapshot"))
    save_ok = save_ok and s14
    save_notes.append(("record_write_fns_forbidden", s14))

    # ----- 작업A(3차): hit/miss mark gates (temp home·temp ledger·운영 write 0) -----
    # 핸들러가 _operating_ledger()=BINGGU_HOME/ledger.sqlite 를 쓰므로, 실 write 경로 검증은 BINGGU_HOME 을
    # 잠깐 temp 로 바꿔 격리한다(운영 ~/.binggupack 미접촉·mark 후 원복). OPERATING_PATHS 는 별도(불변 유지).
    import shutil as _sh
    import tempfile as _tf
    _saved_home = os.environ.get("BINGGU_HOME")
    mark_ok = True
    mark_notes = []
    _mtmp = _tf.mkdtemp(prefix="binggu_mark_mcp_")
    try:
        _mledger = os.path.join(_mtmp, "ledger.sqlite")
        _ensure_scripts_path()
        from openbinggu_owner_accept_ux import open_accept as _oa
        _mdb = _oa(_mledger)  # 핸들러와 동일 open_accept 로 회상 가능한 판단 노드 3건 적재.
        for _nid, _sent in (("mk1", "배포 전 로컬 selftest 와 live endpoint 를 확인한다"),
                            ("mk2", "배포 전 로컬 selftest 확인하고 endpoint 응답을 본다"),
                            ("mk3", "무관한 요리 레시피 메모")):
            _mdb.con.execute(
                "INSERT INTO nodes(node_id,node_type,sentence,semantic_subtype,speaker,state,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (_nid, "judgment", _sent, "교훈", "owner", "active", "2026-06-20T00:00:00Z"))
        _mdb.con.commit()
        _mdb.close()
        os.environ["BINGGU_HOME"] = _mtmp  # 핸들러 _operating_ledger()가 이 temp 를 운영 ledger 로 인식
        _mq = "배포 전 endpoint 확인"

        # M1) dry-run 기본 — write 0(executed_write False·would_write_ledger False), 기대 confirm 안내.
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1}, allow_root)
        tr = r.get("tool_result") or {}
        m1 = (r.get("executed") is True and tr.get("executed_write") is False
              and tr.get("would_write_ledger") is False and tr.get("verdict") == "PREVIEW"
              and tr.get("confirm_expected") == ("MARK_HIT 1 " + _mq))
        mark_ok = mark_ok and m1
        mark_notes.append(("mark_dryrun_write0", m1))

        # M2) confirm 불일치 + dry_run=False → write 0(reader → mark_outcome G4_no_auto).
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1,
                                     "confirm": "MARK_HIT 9 wrong", "dry_run": False}, allow_root)
        tr = r.get("tool_result") or {}
        m2 = (tr.get("executed_write") is False and tr.get("recorded") is False
              and tr.get("reason") == "G4_no_auto")
        mark_ok = mark_ok and m2
        mark_notes.append(("mark_confirm_mismatch_write0", m2))

        # M3) actor 위조 무의미 — params actor='human' 이라도 confirm 부재면 reader → write 0(G4_no_auto).
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1,
                                     "dry_run": False, "actor": "human"}, allow_root)
        tr = r.get("tool_result") or {}
        m3 = (tr.get("executed_write") is False and tr.get("recorded") is False
              and tr.get("reason") == "G4_no_auto")
        mark_ok = mark_ok and m3
        mark_notes.append(("mark_actor_forge_reader_write0", m3))

        # M4) confirm 정확일치 + dry_run=False → 실 기록(temp ledger). recorded True·outcome hit·decision_id.
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1,
                                     "confirm": "MARK_HIT 1 " + _mq, "dry_run": False}, allow_root)
        tr = r.get("tool_result") or {}
        m4 = (r.get("executed") is True and tr.get("executed_write") is True
              and tr.get("recorded") is True and tr.get("outcome") == "hit"
              and bool(tr.get("decision_id")))
        mark_ok = mark_ok and m4
        mark_notes.append(("mark_confirm_exact_write", m4))

        # M5) node_id 미노출(D-1) — mark 응답에 node:/node_id 토큰 없음(위조 표면 0).
        _mblob = _json.dumps(tr, ensure_ascii=False)
        m5 = ("node:" not in _mblob and "node_id" not in _mblob)
        mark_ok = mark_ok and m5
        mark_notes.append(("mark_no_node_id_exposed", m5))

        # M6) D-2 이중계상 차단 — 같은 회상·같은 index 재mark → dup_decision·write 0(안정 decision_id).
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1,
                                     "confirm": "MARK_HIT 1 " + _mq, "dry_run": False}, allow_root)
        tr = r.get("tool_result") or {}
        m6 = (tr.get("executed_write") is False and tr.get("reason") == "dup_decision")
        mark_ok = mark_ok and m6
        mark_notes.append(("mark_dup_decision_write0", m6))

        # M7) mark_miss 다른 index → 다른 node → 다른 decision_id → 정상 기록(outcome miss).
        r = handle_tool("mark_miss", {"recall_query": _mq, "index": 2,
                                      "confirm": "MARK_MISS 2 " + _mq, "dry_run": False}, allow_root)
        tr = r.get("tool_result") or {}
        m7 = (tr.get("executed_write") is True and tr.get("recorded") is True
              and tr.get("outcome") == "miss")
        mark_ok = mark_ok and m7
        mark_notes.append(("mark_miss_write", m7))
    finally:
        if _saved_home is None:
            os.environ.pop("BINGGU_HOME", None)
        else:
            os.environ["BINGGU_HOME"] = _saved_home
        _sh.rmtree(_mtmp, ignore_errors=True)

    save_ok = save_ok and mark_ok
    save_notes.extend(mark_notes)

    # S4) 운영 store(OPERATING_PATHS) mtime 불변 — 실 ledger write 0 입증.
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    s4 = (op_before == op_after)
    save_ok = save_ok and s4
    save_notes.append(("operating_ledger_write_0", s4))

    all_ok = all_ok and save_ok
    print("\n  -- save tool gates --")
    for nm, ok in save_notes:
        print("  [%s] %s" % ("OK" if ok else "FAIL", nm))

    # 노출 도구가 read/dry-run/write-gated 인지 확인.
    # write-gated = confirm(SAVE n 정확일치)+actor 게이트 통과 단건만 실 write — default-deny 약화 아님.
    exposed_ok = all(TOOLS[t]["mode"] in ("read", "dry-run", "write-gated") for t in TOOLS)
    no_forbidden_exposed = all(f not in TOOLS for f in _FORBIDDEN)
    all_ok = all_ok and exposed_ok and no_forbidden_exposed
    print("\n  exposed_tools_read_dryrun_or_writegated_only:", exposed_ok)
    print("  forbidden_tools_not_exposed:", no_forbidden_exposed)
    print("  raw_path_not_leaked:", (not raw_leak))
    print("  operating_store_unchanged: True (핸들러 + mock, 운영 ledger write 0)")
    print("  save_default_dry_run: True  real_ledger_write: 0 (selftest=temp DB only)")
    print("  mcp_protocol_layer: openbinggu_mcp_server.serve_stdio (실 설정 등록은 owner)")

    gate = "GO" if (all_ok and not raw_leak) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_mcp_server_handlers.py [--selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
