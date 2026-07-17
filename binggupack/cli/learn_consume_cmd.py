"""cmd_learn_consume — 학습 큐 owner 승인 소비 CLI (금고 §3 안전 영역).

binggu.py 에서 정확 복사 이관(순수 위치 이동). 게이트/승인/CONSUME <n> 정확 confirm·
_resolve_human_ctx human 승격·LC.consume/consume_many(actor=human) 로직·문구 byte 불변.
백본 심볼은 binggu.py 잔류 → top-level from import 로 참조.
(모듈명 _cmd 접미사 = binggupack.pack.learn_consume 그림자 충돌 회피.)
"""
import os

from binggu import _ledger_paths, _open, _resolve_human_ctx, HINT


def cmd_learn_consume(a):
    """학습 큐(hit/miss 후보) owner 승인 소비 — dry-run 기본 · CONSUME <n> 정확 confirm(작업C).

    user-prompt-learn-outcome.js 가 owner 자연 피드백("맞네"/"틀렸어")을 append 한 큐를 사람이
    확인하고 승인 소비한다. 스케줄러 자동 소비 배제(owner_refutes '안전장치 우회 자동반복' ·
    owner '무차별 적재=노이즈'). mark_outcome 의 actor=human·D-1·D-2·nonce 방어 그대로 통과."""
    from binggupack.pack import learn_consume as LC
    ledger, _ = _ledger_paths(a.ledger)
    qpath = LC.queue_path()
    if a.confirm:
        qis = LC.parse_confirm(a.confirm)
        if qis is None:
            print('BLOCK: --confirm 는 정확히 "CONSUME <번호>" 또는 "CONSUME 0,1,4" 여야 합니다'
                  '(자동확정 0).')
            return 1
        if not os.path.exists(ledger):
            print(f"장부가 없습니다: %s · 먼저 {HINT} init" % ledger)
            return 2
        db, _ = _open(ledger)
        # 소비 승인 actor 판정 = _resolve_human_ctx(save-n 바인딩/cli_command · 에이전트 세션 deny).
        #   env fail-open 없음. CONSUME <n> 문구 단독으로 human 승격 금지(AOB-3 동종).
        #   dry-run 이 스테이징한 큐 preview(발화 원문 축·번호=qi+1)에 사람이 '세이브 qi+1'
        #   (일괄은 '세이브 1-5'/줄 도장) 도장을 찍었으면 ref 바인딩으로 human 승격.
        _refs = None
        try:
            import binggu_save_gate as _sg
            _cands = [{"sentence": ((e.get("evidence") or {}).get("feedback") or "")}
                      for _li, e in LC.load_pending(qpath)]
            if _cands:
                _refs = [(_sg.preview_ref_for_candidates(_cands), [q + 1 for q in qis])]
        except Exception:
            _refs = None
        _ctx = _resolve_human_ctx(a.ledger, _refs)

        def _print_ok(r):
            if r.get("rows"):
                attribution = " · ".join(
                    "%s %s" % (row["speaker"], "적중" if row["outcome"] == "hit" else "빗나감")
                    for row in r["rows"])
                print('OK: 교환 소비(%s·%s) — "%s" → %s'
                      % (r.get("stance"), r.get("verdict"), r.get("node_claim") or "", attribution))
            else:
                print('OK: 회상 조언 %s 소비 — [%d] "%s"'
                      % (r.get("outcome"), r.get("index") or 0, r.get("node_claim") or ""))
            print("    decision=%s · 큐 consumed=true · 사람 확정(actor=human·자동 0)"
                  % r.get("decision_id"))

        if len(qis) > 1:
            # ★일괄 소비(도장 1회·단일 스냅샷 — 번호 재편 재도장 불필요)
            mr = LC.consume_many(db, ledger, qpath, qis, index=a.index,
                                 home=os.path.dirname(ledger), ctx=_ctx, verdict=a.verdict)
            db.close()
            if not mr.get("results"):
                print("BLOCK: %s" % mr.get("reason"))
                if mr.get("reason") == "qi_out_of_range":
                    print("  소비 대기 %s건. dry-run 으로 번호 확인(하나라도 범위 밖이면 전체 거부)."
                          % mr.get("pending", "?"))
                return 1
            for r in mr["results"]:
                if r.get("consumed"):
                    _print_ok(r)
                else:
                    print("BLOCK: [%s] %s" % (r.get("qi"), r.get("reason")))
            return 0 if mr.get("all_consumed") else 1
        r = LC.consume(db, ledger, qpath, qis[0], index=a.index, home=os.path.dirname(ledger),
                       ctx=_ctx, verdict=a.verdict)
        db.close()
        if r.get("consumed"):
            _print_ok(r)
            return 0
        reason = r.get("reason")
        print("BLOCK: %s" % reason)
        if reason == "qi_out_of_range":
            print(f"  소비 대기 %s건. dry-run({HINT} learn-consume) 으로 번호 확인."
                  % r.get("pending", "?"))
        elif reason == "index_out_of_range":
            mark = r.get("mark") or {}
            print("  --index 가 회상 건수를 벗어남(회상 %s건). dry-run 으로 top 확인."
                  % mark.get("recall_count", "?"))
        elif reason == "no_recall":
            print("  이 query 로 회상되는 판단이 없습니다(장부 변경 가능).")
        elif reason == "no_query":
            print("  큐 항목에 회상 query 가 없습니다(소비 불가).")
        elif reason == "empty_feedback":
            print("  큐 항목에 발화 근거(feedback)가 없습니다(소비 불가).")
        elif reason == "dup_decision":
            print("  같은 발화가 이미 적중률에 반영됨(이중계상 차단).")
        elif reason == "invalid_stance":
            print("  큐 항목에 stance/outcome 이 없어 입장을 판정할 수 없습니다(소비 불가).")
        elif reason == "invalid_verdict":
            print("  --verdict 는 upheld(발화대로·기본) 또는 overturned(뒤집힘)만 가능합니다.")
        return 1
    # dry-run(기본): 소비 대기 목록 + 회상 top preview(read-only · 저장 0)
    pv = LC.preview(ledger, qpath, home=os.path.dirname(ledger))
    print(LC.render_preview_md(pv))
    # 도장 스테이징 — 대기 발화 원문을 preview 축(번호=qi+1)으로 영속(hash만·ledger write 0).
    # 사람이 '세이브 <qi+1>' 도장 후 --confirm "CONSUME <qi>" 재실행 = 에이전트 세션에서도 소비.
    try:
        import binggu_save_gate as _sg
        _cands = [{"sentence": ((e.get("evidence") or {}).get("feedback") or "")}
                  for _li, e in LC.load_pending(qpath)]
        if _cands:
            _sg.write_last_preview(_cands, explicit=True,
                                   path=os.path.join(os.path.dirname(ledger),
                                                     "last_preview_candidates.json"))
            print('\n도장(사람 키보드): 표의 [N]번 소비 = "세이브 N+1" 입력 → '
                  '--confirm "CONSUME N" 재실행'
                  '\n  일괄: "세이브 1-6" 또는 "세이브 1,3,5" 한 줄 → --confirm "CONSUME 0,2,4"'
                  ' (도장 1회·재도장 불필요)'
                  '\n  도장은 메시지 전체 또는 **한 줄 전체**가 도장뿐이어야 인식(문장 속 언급 무시).'
                  '\n  확인(교환 축): 기본 = 발화대로(upheld). 나중에 뒤집힌 건이면 '
                  '--verdict overturned 추가.')
    except Exception:
        pass
    return 0
