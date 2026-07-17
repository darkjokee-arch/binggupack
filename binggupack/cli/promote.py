# -*- coding: utf-8 -*-
"""binggu promote 명령 — binggu.py 에서 이관(구조 정리·동작 불변).

승격 write 게이트 정본(§3 안전 영역 — AI 무단저장 차단). confirm 정확일치 문구·id8/index
fail-closed·GL.gate_human_for_promote·_resolve_human_ctx(stamp_ctx)·P5.run_promote 의 ctx
항상 명시전달({"actor":"human"} fail-open 배제) 계약을 한 글자도 바꾸지 않는다. 순수 위치 이동.
백본 심볼(_ledger_paths·_open·_gate_log_for_ledger·_resolve_human_ctx·_reindex_after_write·HINT)은
binggu.py 에 물리적으로 잔류하고 top-level `from binggu import` 로 참조한다. 순환은 binggu.py 가
이 모듈을 wrapper 함수 본문에서만 lazy import 하는 구조로 차단된다(selftest_embed·daily·preflight 선례).
헬퍼 3종(_promote_staging_for_ledger·_promote_candidates·_stage_promote)은 외부 참조 0이라 co-move.
"""
import datetime
import os

from binggu import (  # noqa: E402  (binggu 완전 로드 후 lazy 진입 — 순환 차단)
    HINT,
    _ledger_paths,
    _open,
    _gate_log_for_ledger,
    _resolve_human_ctx,
    _reindex_after_write,
)


def _promote_staging_for_ledger(ledger):
    """승격 도장 staging 을 ledger scope 에 고정(_recall_staging_for_ledger 와 동일 원칙).
    운영에선 dirname(ledger)==home 이라 gate_log.last_promote_candidates_path() 와 동일 경로."""
    from binggupack.safety import gate_log as _gl
    return os.path.join(os.path.dirname(os.path.abspath(ledger)),
                        os.path.basename(_gl.last_promote_candidates_path()))


def _promote_candidates(db):
    """승격 후보(candidate=1·active) 정렬 조회 — hit수↓ > use_count↓ > 최근 저장↓(동률 node_id).

    hit_events 테이블/use_count·created_at·state 컬럼은 구 ledger 에 없을 수 있어 PRAGMA 로
    존재 확인 후 0/'' 폴백(부재 시 candidate+저장순 정렬만 남고 크래시 0). read-only."""
    ncols = {r[1] for r in db.con.execute("PRAGMA table_info(nodes)")}
    use_expr = "COALESCE(use_count,0)" if "use_count" in ncols else "0"
    created_expr = "COALESCE(created_at,'')" if "created_at" in ncols else "''"
    state_pred = " AND COALESCE(state,'active')='active'" if "state" in ncols else ""
    rows = db.con.execute(
        "SELECT node_id, sentence, %s, %s FROM nodes WHERE candidate=1%s"
        % (use_expr, created_expr, state_pred)).fetchall()
    hits = {}
    if list(db.con.execute("PRAGMA table_info(hit_events)")):
        hits = dict(db.con.execute(
            "SELECT node_id, COUNT(*) FROM hit_events WHERE outcome='hit' GROUP BY node_id"))
    out = [{"node_id": r[0], "id8": str(r[0] or "")[:8], "claim": r[1] or "",
            "hits": int(hits.get(r[0], 0)), "use": int(r[2] or 0), "created_at": r[3] or ""}
           for r in rows]
    out.sort(key=lambda c: (c["hits"], c["use"], c["created_at"], c["node_id"]), reverse=True)
    return out


def _stage_promote(ledger, cands):
    """승격 staging 기록(도장 소비용 idx→node_id 바인딩 · ledger write 0 · 실패 침묵 False).

    표시 번호(1-base) = staging idx — owner 채팅 1-발화("승격 N")가 이 바인딩에 도장을 찍는다.
    표시 --limit 과 무관하게 항상 전체 후보 기록(번호는 전체 리스트 위치 — 어느 번호든 도장 가능).
    같은 후보 집합이면 재기록해도 promote_gate_ref 불변(기존 도장 유지)."""
    try:
        from binggupack.safety import gate_log as GL
        GL.write_last_promote(
            [{"node_id": c["node_id"], "id8": c["id8"], "claim": c["claim"]} for c in cands],
            path=_promote_staging_for_ledger(ledger))
        return True
    except Exception:
        return False


def cmd_promote(a):
    """candidate→active 봉인 승격 — 인자없음=후보 리스트 · <n> <id8>=dry-run(기본) · --confirm=실행.

    abstraction --promote(규칙 제안→candidate '등록')와 다른 단계 — 여기는 이미 저장된 candidate
    노드의 active 봉인 승격이며 write 는 봉인 모듈(scripts/binggu_publish_p5_promote.run_promote ·
    백업 강제·evidence 1:1 전후 검증·G4_no_auto·idempotent)로만 간다. 자동 승격 0.

    사람 증명 = owner 채팅 1-발화("승격 N" — UserPromptSubmit hook 이 promote 스탬프 기록) +
    --confirm 정확 문구. 도장 강도는 SAVE 패리티(hook 기록 + gate 파일 존재/신선도 의존 ·
    CLAUDECODE env 는 deny 전용 소프트 신호). ctx 는 도장 판정 결과로 **항상 명시** 전달 —
    도장 없음(에이전트 세션)=actor reader → core G4_no_auto BLOCK. run_promote 의 ctx 기본값
    {"actor":"human"} fail-open 경로를 호출부에서 배제한다."""
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print(f"장부가 없습니다: %s · 먼저 {HINT} init" % ledger)
        return 2
    from binggupack.safety import gate_log as GL

    # ── --confirm 실행: staging 정본 + 도장 대조 → run_promote(자체 open · read 연결 없음) ──
    if getattr(a, "confirm", None):
        if a.n is None or not a.id8:
            print("BLOCK: usage — --confirm 은 번호+id8 과 함께: "
                  f'{HINT} promote <n> <id8> --confirm "PROMOTE <n> <id8>"')
            return 2
        st = GL.load_last_promote(_promote_staging_for_ledger(ledger)) or {}
        rows = st.get("items") or []
        if not rows:
            print("BLOCK: no_promote_staging — 승격 staging 이 없습니다. "
                  f"먼저 {HINT} promote <n> <id8> (dry-run) 을 실행하세요.")
            return 1
        row = {r.get("idx"): r for r in rows}.get(a.n)
        if not row:
            print("BLOCK: index_out_of_range — staging 후보는 %d건(번호 1~%d)." % (len(rows), len(rows)))
            return 1
        if (row.get("id8") or "") != a.id8:
            print("BLOCK: id8_mismatch — 번호 %d 의 id8 은 %s 입니다(리스트 변경/오지정 방지)."
                  % (a.n, row.get("id8")))
            print(f"  {HINT} promote <n> <id8> (dry-run) 으로 번호를 다시 확인하세요.")
            return 1
        expect = "PROMOTE %d %s" % (a.n, a.id8)
        if a.confirm != expect:
            print('BLOCK: confirm_mismatch — 정확히 "%s" 를 입력해야 실행됩니다(자동확정 0).' % expect)
            return 1
        # 사람 도장 판정 → actor 명시 결정(fail-closed). 판정 규약은 _resolve_human_ctx 3분기와
        # 동일 — ① promote 스탬프(소비시점 promote_gate_ref **재계산** 대조 · 미도장/stale/
        # staging 변조 전부 False) ② CLAUDECODE = deny 전용 ③ 터미널 직접 입력 = 사람.
        gate_p = _gate_log_for_ledger(ledger)
        if GL.gate_human_for_promote(rows, [a.n], path=gate_p):
            ctx = _resolve_human_ctx(ledger, stamp_ctx="promote_stamp_ref")
        else:
            # 미도장 → _resolve_human_ctx 2·3분기(CLAUDECODE deny / 터미널 cli_command)와 동일.
            ctx = _resolve_human_ctx(ledger)
        import binggu_publish_p5_promote as P5
        backup_dir = os.path.join(os.path.dirname(ledger), "_backup")
        # tag=timestamp — 백업 파일명 유니크(연속 승격 덮어씀 방지). .sqlite 확장자로 restore 목록 노출.
        tag = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".sqlite"
        r = P5.run_promote(ledger, [row["node_id"]], backup_dir, ctx=ctx, tag=tag)
        if r.get("applied"):
            GL.stamp_mark_consumed(GL.promote_gate_ref(rows), [a.n], path=gate_p)  # 감사 마킹(판정 미반영)
            if r.get("promoted"):
                _reindex_after_write(a.ledger)   # candidate 플립 → fresh_index trust 반영
                print("OK: 승격 완료 — [%d/%s] \"%s\"" % (a.n, a.id8, row.get("claim") or ""))
            else:
                print("이미 active 입니다(idempotent · 변경 0) — [%d/%s]" % (a.n, a.id8))
            print("    백업=%s · checksum %s→%s · 사람 증명=%s(자동 0)"
                  % (r.get("backup"), (r.get("checksum_before") or "")[:8],
                     (r.get("checksum_after") or "")[:8], ctx["actor_source"]))
            return 0
        reason = r.get("reason")
        print("BLOCK: %s" % reason)
        if reason == "G4_no_auto":
            print('  사람 도장이 없습니다(에이전트 세션 · fail-closed). '
                  '채팅 정확형 1줄 "승격 %d" 후 재실행하세요.' % a.n)
        elif reason in ("linkage_broken_pre", "linkage_broken_post"):
            print("  evidence↔node 정합이 깨져 승격 0 (issues=%d)." % len(r.get("issues") or []))
            if r.get("need_restore"):
                print(f'  백업 복원 검토: {HINT} restore "%s"' % r.get("backup"))
        elif reason == "node_not_found":
            print(f"  staging 노드가 ledger 에 없습니다(변경/삭제됨). {HINT} promote 로 리스트를 다시 확인하세요.")
        return 1

    # ── 리스트 / dry-run (read-only · 승격 write 0) ──
    db, _ = _open(a.ledger)
    try:
        cands = _promote_candidates(db)
        if a.n is None:
            # 리스트(추천 표시만 · 자동 승격 0) — claim 원문 전문(요약·말줄임 금지)
            if not cands:
                print("승격 후보(candidate=1)가 없습니다 — 전부 봉인(active)이거나 장부가 비었습니다.")
                return 0
            staged = _stage_promote(ledger, cands)
            shown = cands[:a.limit] if getattr(a, "limit", 0) else cands
            print("# 승격 후보 %d건 — hit↓ · use↓ · 최근저장↓ (추천 표시만 · 자동 승격 0 · read-only)"
                  % len(cands))
            for i, c in enumerate(shown, 1):
                print("  %d. [%s] (hit %d · use %d · %s) %s"
                      % (i, c["id8"], c["hits"], c["use"], c["created_at"] or "-", c["claim"]))
            if len(shown) < len(cands):
                print("  … 외 %d건(번호 %d~%d) — 전체 표시: --limit 0"
                      % (len(cands) - len(shown), len(shown) + 1, len(cands)))
            print(f"\n다음(dry-run · 아직 승격 안 함):  {HINT} promote <번호> <id8>")
            if not staged:
                print("  (주의: staging 기록 실패 — 채팅 도장은 dry-run 재실행 후 사용하세요)")
            return 0
        # dry-run: 사전 점검(evidence 1:1) + staging 기록 + 다음 단계 안내
        if not a.id8:
            print(f"BLOCK: usage — 번호와 id8 을 함께 지정하세요: {HINT} promote <n> <id8> "
                  f"(리스트: {HINT} promote)")
            return 2
        if a.n < 1 or a.n > len(cands):
            print("BLOCK: index_out_of_range — 승격 후보는 %d건(번호 1~%d)." % (len(cands), len(cands)))
            return 1
        c = cands[a.n - 1]
        if c["id8"] != a.id8:
            print("BLOCK: id8_mismatch — 번호 %d 의 id8 은 %s 입니다(리스트 변경/오지정 방지)."
                  % (a.n, c["id8"]))
            print(f"  {HINT} promote 로 최신 리스트를 다시 확인하세요.")
            return 1
        staged = _stage_promote(ledger, cands)
        import binggu_publish_p5_promote as P5
        chk = P5.verify_evidence_linkage(db, [c["node_id"]])
        if not chk["ok"]:
            print("BLOCK: linkage_broken_pre — evidence↔node 정합이 깨져 있어 실행해도 BLOCK 됩니다(승격 0).")
            for iss in chk["issues"]:
                print("  - %s: %s" % (iss.get("node"), iss.get("issue")))
            return 1
        print("# 승격 미리보기(dry-run · 아직 승격 안 함 · ledger write 0)")
        print("대상          : %d. [%s] %s" % (a.n, c["id8"], c["claim"]))
        print("evidence 정합 : OK(1:1) — 실행 시 전후 재검증")
        print("백업 예고     : %s (실행 시 자동 생성 · timestamp 유니크)"
              % os.path.join(os.path.dirname(ledger), "_backup", "ledger.bak_promote_<ts>.sqlite"))
        print("다음 단계:")
        print('  1) 채팅 정확형 1줄로 "승격 %d"  (한 줄 전체가 도장이어야 인식 · 문장 속 언급 무시)' % a.n)
        print(f'  2) {HINT} promote %d %s --confirm "PROMOTE %d %s"'
              % (a.n, c["id8"], a.n, c["id8"]))
        if not staged:
            print("  (주의: staging 기록 실패 — 채팅 도장이 이 후보에 바인딩되지 않습니다)")
        return 0
    finally:
        db.close()
