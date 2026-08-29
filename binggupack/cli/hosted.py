#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu CLI `hosted` 명령 분리본 (God-file 분리 → binggu.py 슬림화).

기존 binggu.py 안에 있던 cmd_hosted 를 옮긴 것 — binggu.py 의 wrapper 가 이 모듈의
cmd_hosted(a) 를 그대로 호출한다. 함수 본체는 한 글자도 바꾸지 않은 순수 위치 이동이다
(§3 안전 영역 — 금고 게이트/승인/confirm 정확일치/actor 판정 로직 불변).

백본 심볼(BASE·HINT·_parse_days·_ledger_paths·_open·_resolve_human_ctx)은 binggu.py 에
잔류하고 여기서는 `from binggu import` 로 참조한다(selftest_embed·daily 선례).
함수 본문 안의 지역 import(binggu_hosted_inbox·openbinggu_save_intent_live_runner·
binggu_save_gate)는 함수와 자동 동반 — 그대로 둔다.

★selftest_embed.py 및 지휘자 wrapper 가 `from binggu import cmd_hosted` 후 직접 호출한다 —
본체 시그니처 def cmd_hosted(a) 불변.
"""
import os
import sys

# repo root(= binggu.py 위치)를 path 에 올려 binggu 를 import 가능하게 한다.
# repo: <root>/binggupack/cli/ 에서 3단계 상위 = <root>. wheel: 3단계 상위 = site-packages(이미 path).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 백본 심볼은 binggu.py 에 잔류(로직 불변·재배선만).
from binggu import (  # noqa: E402
    BASE,
    HINT,
    _ledger_paths,
    _open,
    _parse_days,
    _resolve_human_ctx,
)


def cmd_hosted(a):
    """hosted — collect broad, commit narrow. mobile/web 가 모으고 PC 가 검토·확정한다.
      inbox: worker 1회 회수(저장0) + 대기 intent read-only 요약(80자 발췌·sha8·count·PII/secret flag).
      pull --select: inbox 에서 본 번호만 ledger 로 commit. 전량 자동 적용 없음 · 사람 confirm 게이트.
    no daemon · no autopull · no autosave — 두 명령 모두 사람이 직접 실행해야만 동작."""
    sub = getattr(a, "hosted_cmd", None)
    import json as _json
    import time as _t
    from binggu_hosted_inbox import (  # noqa: E402
        staging_dir_for, fetch_to_staging, summarize, render_summary_md, commit_selected)
    home = os.path.dirname(os.path.abspath(a.ledger))
    staging = staging_dir_for(home)

    def _staged_cands(now_ts):
        """staged intent 원문 → save-n preview 후보(1 intent = 1 row · 전체 idx 순서 고정 · 해시만 영속).
        inbox 렌더의 write_last_preview 와 pull 의 pref 재계산이 같은 빌더를 공유(pref 패리티)."""
        cands = []
        for it in summarize(staging, now_ts)["items"]:
            try:
                with open(it["_path"], "r", encoding="utf-8") as f:
                    cands.append({"sentence": _json.load(f).get("text") or ""})
            except Exception:
                cands.append({"sentence": ""})
        return cands

    if sub == "inbox":
        no_fetch = bool(getattr(a, "no_fetch", False))
        if not no_fetch:
            from openbinggu_save_intent_live_runner import (  # noqa: E402
                make_live_admin, make_live_pull, _load_save_env)
            wp = getattr(a, "wp", None) or os.environ.get("BINGGU_WORKERS_PORT") \
                or os.path.abspath(os.path.join(BASE, "..", "workers_port"))
            try:
                b, t, sk = _load_save_env(wp, a.variant)
            except Exception:
                print("workers_port 키 없음 — worker 회수 생략, staging 만 표시"
                      "(--workers-port/BINGGU_WORKERS_PORT 확인)")
                no_fetch = True
            if not no_fetch:
                fr = fetch_to_staging(staging, make_live_pull(b, t, sk), make_live_admin(b, t, sk),
                                      poll_secs=int(getattr(a, "wait", 0) or 0))
                tail = "" if fr["disabled"] else (" ⚠disable_err=%s" % fr["disable_err"])
                print("회수: fetched=%s enabled=%s disabled=%s%s"
                      % (fr["fetched"], fr["enabled"], fr["disabled"], tail))
        _now = int(_t.time())
        summ = summarize(staging, _now, since_days=_parse_days(getattr(a, "since", None)))
        if summ.get("total", summ["count"]) > summ["count"]:
            print("(--since 로 %d건 중 %d건만 표시 · 번호는 전체 기준 고정)"
                  % (summ["total"], summ["count"]))
        print(render_summary_md(summ))
        # save-n 참조 바인딩 앵커 — inbox 렌더 시 staged 원문을 last_preview 에 영속(해시만·원문 미저장).
        # 사람이 'SAVE n'(세이브 n) 발화하면 hook 이 이 preview_ref 로 ref 레코드를 기록한다.
        # 앵커 경로 = ledger 기준 home(데이터와 동일 축) — 전역 home 고정이면 --ledger 격리 실행이
        # 운영 앵커를 오염시킨다(2026-07-13 실측: 테스트/무인 실행이 owner 발화 도장을 계속 덮음).
        # --no-anchor = 무인(auto_pull 등) 렌더 전용: 사람 SAVE 앵커를 건드리지 않는다.
        if not getattr(a, "no_anchor", False):
            try:
                import binggu_save_gate as _sg
                _sg.write_last_preview(_staged_cands(_now),
                                       path=os.path.join(home, "last_preview_candidates.json"))
            except Exception:
                # Preview staging is advisory; the hosted pull result remains authoritative.
                pass
        return 0

    if sub == "pull":
        # 저장 게이트 = 사람 save-n(preview_ref 바인딩) + confirm 정확일치(스펙 ③ — approval 배선 없음).
        sel = getattr(a, "select", None)
        if not sel:
            print("hosted pull = inbox 에서 본 번호를 골라 사람 save-n 으로 ledger 에 저장합니다(전량 자동 없음).")
            print(f"  먼저:  {HINT} hosted inbox                 (대기 intent 번호 확인 · preview 기록)")
            print(f"  저장:  {HINT} hosted pull --select 1,3 --confirm \"SAVE 1,3\"")
            print("  (Claude Code 에선 inbox 확인 후 '세이브 1,3' 발화가 사람 앵커 · 터미널에선 직접 실행이 곧 save n)")
            return 0
        idx = [int(x) for x in sel.split(",") if x.strip()]
        confirm = getattr(a, "confirm", None)
        ledger, snap_dir = _ledger_paths(a.ledger)
        if not os.path.exists(ledger):
            print(f"장부 없음: %s (먼저 {HINT} init)" % ledger)
            return 2
        now_ts = int(_t.time())
        # save-n 참조 바인딩 — inbox 렌더가 영속한 preview 와 동일 빌더로 pref 재계산(1 intent = 1 row).
        _refs = None
        try:
            import binggu_save_gate as _sg
            _cands = _staged_cands(now_ts)
            if _cands:
                _refs = [(_sg.preview_ref_for_candidates(_cands), idx)]
        except Exception:
            _refs = None
        db, _ = _open(ledger)
        before = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
        ctx = _resolve_human_ctx(a.ledger, _refs, confirm)
        res = commit_selected(db, home, staging, idx, ctx, confirm, snap_dir, now_ts)
        after = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
        chain = db.verify_chain()
        db.close()
        # 선택 자체 오류(전량 자동 금지·없는 번호)
        if res.get("reason") in ("select_required", "idx_out_of_range", "intent_id_missing"):
            extra = (" (없는 번호: %s)" % res["bad_idx"]) if res.get("bad_idx") else ""
            print("BLOCK: %s%s" % (res["reason"], extra))
            return 1
        if res.get("write"):
            print("hosted pull 저장: 묶음 %d건 확정(atomic)" % res["applied"])
            print("  candidate(active) %d -> %d (+%d) · audit chain %s"
                  % (before, after, after - before, "INTACT" if chain else "BROKEN"))
            return 0
        # 저장 실패(사람 앵커 없음·confirm 불일치·all-or-nothing 차단 등) — write 0·원문 보존
        rc = res.get("reason") or "not_written"
        print("BLOCK: %s · write 0(원문 보존)" % rc)
        if rc == "human_save_required":
            print("  inbox preview 확인 → Claude Code 에선 '세이브 %s' 발화, 터미널에선 직접 실행."
                  % ",".join(str(i) for i in idx))
        if res.get("guidance"):
            print("  %s" % res["guidance"])
        if res.get("fail"):
            print("  실패 intent: %s (%s)" % (res["fail"].get("intent_id"), res["fail"].get("reason")))
        if res.get("receipt"):
            print("  기존 receipt 반환(재저장 0·멱등)")
        return 1
    return 1
