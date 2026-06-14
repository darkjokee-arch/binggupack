#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu — BingguPack 개인 장부 CLI (v1.0 사용자 진입점).

설치 후 "내 영속 장부"를 만들고 후보 관리 전 과정을 실행한다.
새 게이트 로직 0 — 기존 검증 모듈(save/list/deprecate/replace/accept/resolve)을 그대로 호출.
모든 변경은 confirm 문구를 사용자가 직접 타이핑해야 통과한다(자동 0·confirmed 0·raw 원문 0).

  python binggu.py init                            내 장부 생성 (~/.binggupack/ledger.sqlite)
  python binggu.py status                          장부 요약
  python binggu.py preview "<대화/메모 텍스트>"      저장 후보 미리보기 (저장 0)
  python binggu.py save "<텍스트>" --preview-id <preview가 표시한 id> \
                   --pick 1,3 --confirm "SAVE 1,3" [--due 2026-07-01]
                   (preview 없이 raw text 직행 저장은 BLOCK — 사람이 보고 고른 것만 저장)
  python binggu.py list [--status pending|deprecated|resolved] [--kind 판단|상태|개념|문서|증거]
  python binggu.py deprecate <n> <id8> --reason "..." --confirm "DEPRECATE <n> <id8>"
  python binggu.py replace <n> <id8> --with "<수정문장>" --reason "..." \
                   --confirm "REPLACE <n> <id8> WITH <수정문장>"
  python binggu.py accept <n> <id8> --reason "..." --confirm "ACCEPT <n> <id8>"
  python binggu.py unaccept <n> <id8> --reason "..." --confirm "UNACCEPT <n> <id8>"
  python binggu.py due <n> <id8> --date 2026-07-01      판단 검증 예정일 등록
  python binggu.py resolve <n> <id8> --outcome 성공|실패|불확실|판정불가 --reason "..."
  python binggu.py reminders [--today YYYY-MM-DD]       due 경과 판단 목록
  python binggu.py --selftest                            temp 장부 풀 사이클 자가검증

장부 위치 변경: --ledger <sqlite 경로> (기본 ~/.binggupack/ledger.sqlite)
"""
import argparse
import datetime
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import (  # noqa: E402
    set_review_due, resolve_review, list_due_reminders)
from openbinggu_candidate_list_view import list_candidates  # noqa: E402
from openbinggu_conversation_candidate_save import save_selected  # noqa: E402
from openbinggu_conversation_capture_preview import capture_preview  # noqa: E402
from openbinggu_candidate_deprecate_ux import deprecate_from_list  # noqa: E402
from openbinggu_candidate_replace_ux import replace_from_list  # noqa: E402
from openbinggu_owner_accept_ux import (  # noqa: E402
    open_accept, accept_from_list, unaccept_from_list, accepted_view)
from binggu_capture_profile import (  # noqa: E402
    init_profile, pause as cap_pause, resume as cap_resume,
    uninstall as cap_uninstall, status as cap_status)
from binggu_capture_persist import PersistentCaptureBuffer  # noqa: E402
from binggu_capture_to_save import build_save_commands  # noqa: E402
import binggu_platform as _plat  # noqa: E402

# cross-platform: BINGGU_HOME 우선(opt-in) · 없으면 OS별 홈/.binggupack (Windows 동작 보존).
DEFAULT_LEDGER = _plat.default_ledger()
DEFAULT_SETTINGS = _plat.default_settings()
OUTCOMES = ("성공", "실패", "불확실", "판정불가")


def _hook_command():
    """settings.json 에 등록할 capture hook 실행 명령(repo hooks 절대경로).
    런처는 OS별: Windows=py, WSL/macOS/Linux=python3."""
    return '%s "%s"' % (_plat.python_cmd(), os.path.join(BASE, "hooks", "binggu_capture_hook.py"))


def _ledger_paths(ledger):
    ledger = os.path.abspath(ledger)
    snap_dir = os.path.join(os.path.dirname(ledger), "snapshots")
    return ledger, snap_dir


def _open(ledger, must_exist=True):
    ledger, snap_dir = _ledger_paths(ledger)
    if must_exist and not os.path.exists(ledger):
        print("장부가 없습니다: %s" % ledger)
        print("먼저 만드세요:  python binggu.py init")
        sys.exit(2)
    os.makedirs(snap_dir, exist_ok=True)
    return open_accept(ledger), snap_dir


def _node_id_of(db, index, status="all", kind=None):
    rows = list_candidates(db, status, kind)["rows"]
    if index < 1 or index > len(rows):
        return None
    return rows[index - 1]["node_id"]


def _show(r):
    if r.get("applied"):
        print("OK:", {k: v for k, v in r.items() if k != "applied"} or "적용됨")
        return 0
    print("BLOCK:", r.get("reason"))
    return 1


def cmd_init(a):
    ledger, snap_dir = _ledger_paths(a.ledger)
    if os.path.exists(ledger):
        print("이미 장부가 있습니다: %s (그대로 사용하면 됩니다)" % ledger)
    else:
        os.makedirs(os.path.dirname(ledger), exist_ok=True)
        os.makedirs(snap_dir, exist_ok=True)
        db = open_accept(ledger)
        db.close()
        print("장부 생성 완료: %s" % ledger)
    # AGI memory capture profile — 이 profile 이 있어야 자동 후보 수집이 동작(기본 ON).
    # clone 직후(init 전)에는 profile 이 없어 어떤 세션에서도 수집되지 않는다.
    if getattr(a, "no_capture", False):
        print("(capture profile 생략: --no-capture)")
    else:
        home = os.path.dirname(os.path.abspath(ledger))
        settings = getattr(a, "capture_settings", None) or DEFAULT_SETTINGS
        cwd = getattr(a, "capture_cwd", None) or os.getcwd()
        # AGI memory mode = 전역 후보수집이 기본 경험(--agi-memory 또는 --global). 플래그 없는 init = 현재 위치만(privacy).
        global_scope = bool(getattr(a, "global_scope", False) or getattr(a, "agi_memory", False))
        r = init_profile(home, cwd, hook_command=_hook_command(),
                         settings_path=settings, global_scope=global_scope)
        scope_desc = "전역(AGI memory — 모든 작업 세션)" if r["global"] else ("현재 위치만 %s (privacy)" % cwd)
        print("AGI memory capture ON — scope: %s" % scope_desc)
        if r["hook_events"]:
            print("hook 등록(settings.json 백업됨): %s" % ", ".join(r["hook_events"]))
        else:
            print("hook 이미 등록됨 — 그대로 사용")
        print("자동 후보 수집만 켜집니다. 저장은 preview 후 SAVE n 게이트로만(자동 저장 없음).")
        print("상태:  python binggu.py capture status   ·   끄기:  capture pause   ·   제거:  capture uninstall")
    print("다음:  python binggu.py preview \"오늘 정리하고 싶은 문장들\"")
    return 0


def cmd_capture(a):
    home = os.path.dirname(os.path.abspath(a.ledger))
    settings = getattr(a, "settings", None) or DEFAULT_SETTINGS
    cwd = getattr(a, "capture_cwd", None) or os.getcwd()
    sub = a.capture_cmd
    if sub == "status":
        st = cap_status(home, cwd, settings)
        print("capture: %s%s" % ("ON" if st["enabled"] else "OFF",
                                  " (paused)" if st["paused"] else ""))
        print("scope: %s · 현재 위치 수집 대상: %s"
              % ("전역" if st["global"] else "지정 위치", "예" if st["in_current_scope"] else "아니오"))
        print("버퍼 후보: %d건 · hook 등록: %s"
              % (st["buffer_count"], {True: "예", False: "아니오", None: "미확인"}[st["hook_registered"]]))
        return 0
    if sub == "pause":
        cap_pause(home)
        print("capture 일시중지(pause). 재개:  python binggu.py capture resume")
        return 0
    if sub == "resume":
        cap_resume(home)
        print("capture 재개(resume).")
        return 0
    if sub == "preview":
        pv = PersistentCaptureBuffer(home=home).render_preview()
        if pv["count"] == 0:
            print("수집된 후보가 없습니다.")
            return 0
        print("자동 수집된 후보 %d건:" % pv["count"])
        for it in pv["items"]:
            print("  " + it["label"])
        ledger_opt = a.ledger if a.ledger != DEFAULT_LEDGER else None
        cmds = build_save_commands(pv, ledger=ledger_opt)
        print("\n저장하려면 후보별로 직접 실행하세요(사람 confirm 게이트로만 저장):")
        for c in cmds["commands"]:
            print("  [%d] %s" % (c["idx"], c["save_command"]))
        print("\n자동 저장은 없습니다 — 위 명령을 실행해야 기존 게이트로 저장됩니다.")
        return 0
    if sub == "uninstall":
        res = cap_uninstall(home, settings_path=settings)
        print("capture 제거 완료. 삭제 파일: %s · hook 제거: %s"
              % (", ".join(res["removed_files"]) or "없음",
                 ", ".join(res["hook_removed_events"]) or "없음"))
        print("(장부 ledger.sqlite 는 그대로 보존됩니다.)")
        return 0
    return 1


def _parse_days(s):
    """'7d' / '7' → 7.0 (일수). None → None."""
    if s is None:
        return None
    return float(str(s).strip().lower().rstrip("d"))


def cmd_hosted(a):
    """hosted — collect broad, commit narrow. mobile/web 가 모으고 PC 가 검토·확정한다.
      inbox: worker 1회 회수(저장0) + 대기 intent read-only 요약(80자 발췌·sha8·count·PII/secret flag).
      pull --select: inbox 에서 본 번호만 ledger 로 commit. 전량 자동 적용 없음 · 사람 confirm 게이트.
    no daemon · no autopull · no autosave — 두 명령 모두 사람이 직접 실행해야만 동작."""
    sub = getattr(a, "hosted_cmd", None)
    import time as _t
    from binggu_hosted_inbox import (  # noqa: E402
        staging_dir_for, fetch_to_staging, summarize, render_summary_md, commit_selected)
    home = os.path.dirname(os.path.abspath(a.ledger))
    staging = staging_dir_for(home)

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
        summ = summarize(staging, int(_t.time()), since_days=_parse_days(getattr(a, "since", None)))
        if summ.get("total", summ["count"]) > summ["count"]:
            print("(--since 로 %d건 중 %d건만 표시 · 번호는 전체 기준 고정)"
                  % (summ["total"], summ["count"]))
        print(render_summary_md(summ))
        return 0

    if sub == "pull":
        sel = getattr(a, "select", None)
        if not sel:
            print("hosted pull = inbox 에서 본 번호만 골라 ledger 에 저장합니다(전량 자동 적용 없음).")
            print("  먼저:  python binggu.py hosted inbox            (대기 intent 번호 확인)")
            print('  저장:  python binggu.py hosted pull --select 1,3 --confirm "LIVE SAVE 1,3"')
            return 0
        idx = [int(x) for x in sel.split(",") if x.strip()]
        if not getattr(a, "confirm", None):
            print('confirm 필요:  --confirm "LIVE SAVE %s"' % ",".join(str(i) for i in idx))
            return 1
        ledger, snap_dir = _ledger_paths(a.ledger)
        if not os.path.exists(ledger):
            print("장부 없음: %s (먼저 python binggu.py init)" % ledger)
            return 2
        db, _ = _open(ledger)
        before = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
        res = commit_selected(db, staging, idx, a.confirm, snap_dir, int(_t.time()))
        after = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
        chain = db.verify_chain()
        db.close()
        if not res["ok"] and res.get("reason"):
            extra = (" (기대 confirm: %s)" % res["expected"]) if res.get("expected") else ""
            extra += (" (없는 번호: %s)" % res["bad_idx"]) if res.get("bad_idx") else ""
            print("BLOCK: %s%s" % (res["reason"], extra))
            return 1
        print("hosted pull 결과: 선택 %d · applied=%s rejected=%s expired=%s"
              % (res["selected"], res["applied"], res["rejected"], res["expired"]))
        print("  candidate(active) %d -> %d (+%d) · audit chain %s"
              % (before, after, after - before, "INTACT" if chain else "BROKEN"))
        return 0 if res["applied"] > 0 else 1
    return 1


def cmd_status(a):
    db, _ = _open(a.ledger)
    n = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    d = db.con.execute("SELECT count(*) FROM nodes WHERE state='deprecated'").fetchone()[0]
    p = db.con.execute("SELECT count(*) FROM judgment_reviews WHERE status='pending'").fetchone()[0]
    acc = len(accepted_view(db))
    chain = db.verify_chain()
    db.close()
    print("장부: %s" % os.path.abspath(a.ledger))
    print("플랫폼: %s · python: %s · 공유장부(BINGGU_HOME): %s"
          % (_plat.detect_os(), _plat.python_cmd(),
             "예(opt-in)" if _plat.shared_opt_in() else "아니오(OS별 로컬)"))
    print("active 후보 %d · 기각 %d · 검증 예정 %d · 수용 %d · audit chain %s"
          % (n, d, p, acc, "INTACT" if chain else "BROKEN!"))
    return 0


def _preview_id(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def cmd_preview(a):
    pv = capture_preview(a.text)
    print(pv["preview_markdown"])
    pid = _preview_id(a.text)
    print("\npreview_id: %s" % pid)
    if pv["candidates"]:
        print("⚠ 외부 사실(릴리스 상태·업로드 여부·등급 등)은 실측 확인 전에 저장하지 마세요.")
        print("저장은 번호를 직접 골라서:  python binggu.py save \"<같은 텍스트>\" --preview-id %s "
              "--pick <고른 번호들> --confirm \"SAVE <고른 번호들>\"" % pid)
    return 0


def cmd_save(a):
    # 승인 정책: preview 를 실제로 본 텍스트만 저장 가능 — raw text 직행 저장 차단
    if a.preview_id != _preview_id(a.text):
        print("BLOCK: preview_required_mismatch — 먼저 preview 를 실행하고, "
              "거기 표시된 preview_id 와 같은 텍스트로 저장하세요.")
        return 1
    db, snap_dir = _open(a.ledger)
    idx = [int(x) for x in a.pick.split(",") if x.strip()]
    r = save_selected(db, a.text, idx, {"actor": "human", "confirm": a.confirm},
                      snap_dir, due_date=a.due)
    db.close()
    return _show(r)


def cmd_list(a):
    db, _ = _open(a.ledger)
    v = list_candidates(db, a.status or "all", a.kind)
    acc = accepted_view(db)
    db.close()
    print(v["markdown"])
    if acc:
        print("수용(owner_accepted) 중: %d건" % len(acc))
    return 0


def cmd_deprecate(a):
    db, snap_dir = _open(a.ledger)
    r = deprecate_from_list(db, a.n, a.id8, a.reason,
                            {"actor": "human", "confirm": a.confirm}, snap_dir)
    db.close()
    return _show(r)


def cmd_replace(a):
    db, snap_dir = _open(a.ledger)
    r = replace_from_list(db, a.n, a.id8, getattr(a, "with"), a.reason,
                          {"actor": "human", "confirm": a.confirm}, snap_dir)
    db.close()
    return _show(r)


def cmd_accept(a):
    db, _ = _open(a.ledger)
    r = accept_from_list(db, a.n, a.id8, a.reason, {"actor": "human", "confirm": a.confirm})
    db.close()
    return _show(r)


def cmd_unaccept(a):
    db, _ = _open(a.ledger)
    r = unaccept_from_list(db, a.n, a.id8, a.reason, {"actor": "human", "confirm": a.confirm})
    db.close()
    return _show(r)


def cmd_due(a):
    db, _ = _open(a.ledger)
    nid = _node_id_of(db, a.n)
    from openbinggu_candidate_list_view import node_id8
    if not nid or node_id8(nid) != a.id8:
        db.close()
        print("BLOCK: node_hash_mismatch (목록을 다시 확인하세요: binggu.py list)")
        return 1
    r = set_review_due(db, nid, a.date, {"actor": "human"})
    db.close()
    return _show(r)


def cmd_resolve(a):
    db, _ = _open(a.ledger)
    nid = _node_id_of(db, a.n)
    from openbinggu_candidate_list_view import node_id8
    if not nid or node_id8(nid) != a.id8:
        db.close()
        print("BLOCK: node_hash_mismatch (목록을 다시 확인하세요: binggu.py list)")
        return 1
    r = resolve_review(db, nid, a.outcome, a.reason, {"actor": "human"})
    db.close()
    return _show(r)


def cmd_reminders(a):
    db, _ = _open(a.ledger)
    today = a.today or datetime.date.today().isoformat()
    r = list_due_reminders(db, today)
    db.close()
    print(r["markdown"])
    return 0


def selftest():
    print("=" * 74)
    print("binggu CLI — temp 장부 풀 사이클 selftest (영속 장부·운영 store 접근 0)")
    print("=" * 74)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_cli_")
    ledger = os.path.join(tmp, "ledger.sqlite")
    checks = []

    def ck(name, ok):
        checks.append(ok)
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))

    class A:  # argparse 흉내 — CLI 함수를 그대로 검증
        pass

    def args(**kw):
        a = A()
        a.ledger = ledger
        a.no_capture = True  # 기본: 장부 사이클만(실 settings.json 미접촉)
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    ck("1_init", cmd_init(args()) == 0 and os.path.exists(ledger))
    ck("1b_init_멱등", cmd_init(args()) == 0)
    # capture profile (AGI memory) — temp settings 전용, 실 ~/.claude/settings.json 미접촉
    cap_settings = os.path.join(tmp, "settings.json")
    cap_cwd = os.path.realpath(tmp)
    cap_home = os.path.dirname(ledger)
    ck("1c_capture_init", cmd_init(args(no_capture=False, capture_settings=cap_settings,
                                        capture_cwd=cap_cwd)) == 0)
    _cst = cap_status(cap_home, cap_cwd, cap_settings)
    ck("1d_capture_ON+hook+scope", _cst["enabled"] and _cst["hook_registered"]
       and _cst["in_current_scope"] and not _cst["global"])
    # --agi-memory = 전역(AGI memory mode) — 임의 cwd 도 수집 대상
    ck("1d2_agi_memory→전역",
       cmd_init(args(no_capture=False, capture_settings=cap_settings, capture_cwd=cap_cwd, agi_memory=True)) == 0
       and cap_status(cap_home, "D:/anywhere/else", cap_settings)["global"]
       and cap_status(cap_home, "D:/anywhere/else", cap_settings)["in_current_scope"])
    ck("1e_pause→OFF", cmd_capture(args(capture_cmd="pause", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1f_resume→ON", cmd_capture(args(capture_cmd="resume", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1g_preview(저장0)", cmd_capture(args(capture_cmd="preview", settings=cap_settings, capture_cwd=cap_cwd)) == 0)
    ck("1h_uninstall", cmd_capture(args(capture_cmd="uninstall", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    TEXT = ("이 입찰은 마진이 낮아 보류한다. 백필 작업이 진행 중이다. "
            "낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다.")
    ck("2_preview(저장0)", cmd_preview(args(text=TEXT)) == 0)
    ck("2b_preview없는_save_BLOCK", cmd_save(args(text=TEXT, preview_id="deadbeef",
                                                  pick="1,2,3", confirm="SAVE 1,2,3",
                                                  due=None)) == 1)
    ck("3_save", cmd_save(args(text=TEXT, preview_id=_preview_id(TEXT),
                               pick="1,2,3", confirm="SAVE 1,2,3",
                               due="2099-12-31")) == 0)
    db, _ = _open(ledger)
    rows = list_candidates(db)["rows"]
    db.close()
    ck("4_list_3건", len(rows) == 3 and cmd_list(args(status=None, kind=None)) == 0)
    i_state = next(i for i, r in enumerate(rows, 1) if r["kind"] == "상태")
    h_state = rows[i_state - 1]["id8"]
    ck("5_deprecate", cmd_deprecate(args(n=i_state, id8=h_state, reason="셀프테스트 기각",
                                         confirm="DEPRECATE %s %s" % (i_state, h_state))) == 0)
    db, _ = _open(ledger)
    rows2 = list_candidates(db)["rows"]
    db.close()
    i_j = next(i for i, r in enumerate(rows2, 1) if r["kind"] == "판단" and r["state"] == "active")
    h_j = rows2[i_j - 1]["id8"]
    NEW = "재검토 결과 이 입찰은 조건부로 진행한다."
    ck("6_replace", cmd_replace(args(n=i_j, id8=h_j, reason="셀프테스트 수정",
                                     confirm="REPLACE %s %s WITH %s" % (i_j, h_j, NEW),
                                     **{"with": NEW})) == 0)
    db, _ = _open(ledger)
    rows3 = list_candidates(db)["rows"]
    db.close()
    i_n = next(i for i, r in enumerate(rows3, 1) if NEW[:10] in r["sentence"])
    h_n = rows3[i_n - 1]["id8"]
    ck("7_accept", cmd_accept(args(n=i_n, id8=h_n, reason="유지",
                                   confirm="ACCEPT %s %s" % (i_n, h_n))) == 0)
    ck("7b_unaccept", cmd_unaccept(args(n=i_n, id8=h_n, reason="재검토",
                                        confirm="UNACCEPT %s %s" % (i_n, h_n))) == 0)
    ck("8_due+resolve", cmd_due(args(n=i_n, id8=h_n, date="2000-01-01")) == 0
       and cmd_reminders(args(today="2000-01-02")) == 0
       and cmd_resolve(args(n=i_n, id8=h_n, outcome="성공", reason="셀프테스트")) == 0)
    ck("9_잘못된_confirm_BLOCK", cmd_deprecate(args(n=1, id8="deadbeef", reason="x",
                                                   confirm="DEPRECATE 1 deadbeef")) == 1)
    db, _ = _open(ledger)
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0])
    chain = db.verify_chain()
    blob = "\n".join(str(r) for t in ("nodes", "audit_log")
                     for r in db.con.execute("SELECT * FROM " + t))
    db.close()
    ck("10_candidate-only+chain+raw0", bad == 0 and chain and TEXT not in blob)

    # ---- hosted: collect broad, commit narrow (worker 미접촉 · 별도 temp · staging 직접) ----
    import time as _time
    import json as _json
    from binggu_hosted_inbox import staging_dir_for as _sdir
    from openbinggu_save_intent_outbox_runner import intent_hash as _ih, SCHEMA_VER as _SV
    h_tmp = tempfile.mkdtemp(prefix="bgp_cli_hosted_")
    h_home = os.path.join(h_tmp, ".binggupack")
    h_staging = _sdir(h_home)
    os.makedirs(h_staging)
    h_ledger = os.path.join(h_home, "ledger.sqlite")
    os.makedirs(os.path.join(h_home, "snapshots"))
    open_accept(h_ledger).close()

    def _mk(text, idxs):
        c = "SAVE " + ",".join(str(i) for i in idxs)
        it = {"schema_ver": _SV, "text": text, "indices": idxs, "confirm": c,
              "intent_id": _ih(text, idxs, c), "created_ts": int(_time.time()) - 10,
              "ttl_s": 86400, "source": "hosted"}
        with open(os.path.join(h_staging, it["intent_id"] + ".json"), "w", encoding="utf-8") as f:
            _json.dump(it, f, ensure_ascii=False)

    _mk("이 입찰은 마진이 낮아 보류한다.", [1])
    _mk("백필 작업이 진행 중이다.", [1])
    ck("13_hosted_inbox_요약(저장0·worker미접촉)",
       cmd_hosted(args(ledger=h_ledger, hosted_cmd="inbox", no_fetch=True, since=None)) == 0)
    ck("14_hosted_pull_select없음_안내(실행0)",
       cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select=None, confirm=None)) == 0)
    n_stg_before = len([f for f in os.listdir(h_staging) if f.endswith(".json")])
    rc15 = cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select="1", confirm="LIVE SAVE 1"))
    db_h = open_accept(h_ledger)
    n_act_h = db_h.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    db_h.close()
    n_stg_after = len([f for f in os.listdir(h_staging) if f.endswith(".json")])
    ck("15_hosted_pull_commit_narrow(선택1건만·나머지잔류)",
       rc15 == 0 and n_act_h == 1 and n_stg_after == n_stg_before - 1)
    ck("16_hosted_pull_confirm불일치_BLOCK(전량자동 차단)",
       cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select="1", confirm="LIVE SAVE 9")) == 1)
    shutil.rmtree(h_tmp, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("11_운영_store_불변", op_before == op_after)
    shutil.rmtree(tmp, ignore_errors=True)
    ck("12_temp_정리", not os.path.exists(tmp))

    ok = all(checks)
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (sum(checks), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    p = argparse.ArgumentParser(prog="binggu", description="BingguPack 개인 장부 CLI")
    p.add_argument("--ledger", default=DEFAULT_LEDGER)
    sub = p.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("init")
    ip.add_argument("--agi-memory", action="store_true", dest="agi_memory")  # 명시 별칭(동작 동일)
    ip.add_argument("--global", action="store_true", dest="global_scope")     # 전역 수집(미지정 시 현재 위치만)
    ip.add_argument("--no-capture", action="store_true", dest="no_capture")   # 장부만, capture profile 생략
    sub.add_parser("status")
    sp = sub.add_parser("preview"); sp.add_argument("text")
    sp = sub.add_parser("save"); sp.add_argument("text")
    sp.add_argument("--preview-id", required=True, dest="preview_id")
    sp.add_argument("--pick", required=True); sp.add_argument("--confirm", required=True)
    sp.add_argument("--due", default=None)
    sp = sub.add_parser("list"); sp.add_argument("--status", default=None)
    sp.add_argument("--kind", default=None)
    for name in ("deprecate", "accept", "unaccept"):
        sp = sub.add_parser(name); sp.add_argument("n", type=int); sp.add_argument("id8")
        sp.add_argument("--reason", required=True); sp.add_argument("--confirm", required=True)
    sp = sub.add_parser("replace"); sp.add_argument("n", type=int); sp.add_argument("id8")
    sp.add_argument("--with", required=True, dest="with")
    sp.add_argument("--reason", required=True); sp.add_argument("--confirm", required=True)
    sp = sub.add_parser("due"); sp.add_argument("n", type=int); sp.add_argument("id8")
    sp.add_argument("--date", required=True)
    sp = sub.add_parser("resolve"); sp.add_argument("n", type=int); sp.add_argument("id8")
    sp.add_argument("--outcome", required=True, choices=OUTCOMES)
    sp.add_argument("--reason", required=True)
    sp = sub.add_parser("reminders"); sp.add_argument("--today", default=None)
    cp = sub.add_parser("capture"); cp.add_argument("--settings", default=None)
    csub = cp.add_subparsers(dest="capture_cmd", required=True)
    for cs in ("status", "pause", "resume", "preview", "uninstall"):
        csub.add_parser(cs)
    hp = sub.add_parser("hosted")
    hsub = hp.add_subparsers(dest="hosted_cmd", required=True)
    ibp = hsub.add_parser("inbox")          # 회수(저장0) + read-only 요약
    ibp.add_argument("--since", default=None)            # '7d' 또는 '7' (표시 필터·번호 고정)
    ibp.add_argument("--no-fetch", dest="no_fetch", action="store_true")  # worker 미접촉, staging 만
    ibp.add_argument("--wait", type=int, default=0)
    ibp.add_argument("--variant", choices=["save_mcp", "save_v2"], default="save_mcp")
    ibp.add_argument("--workers-port", dest="wp", default=None)
    pp = hsub.add_parser("pull")            # 선택 항목만 ledger commit (전량 자동 없음)
    pp.add_argument("--select", default=None)            # 'inbox' 에서 본 번호들 (예: 1,3)
    pp.add_argument("--confirm", default=None)           # "LIVE SAVE <select>" 정확 일치
    a = p.parse_args()
    fn = {"init": cmd_init, "status": cmd_status, "preview": cmd_preview, "save": cmd_save,
          "list": cmd_list, "deprecate": cmd_deprecate, "replace": cmd_replace,
          "accept": cmd_accept, "unaccept": cmd_unaccept, "due": cmd_due,
          "resolve": cmd_resolve, "reminders": cmd_reminders, "capture": cmd_capture,
          "hosted": cmd_hosted}[a.cmd]
    sys.exit(fn(a))


if __name__ == "__main__":
    main()
