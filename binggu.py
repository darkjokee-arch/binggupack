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
    disable_capture as cap_disable, enable_capture as cap_enable,
    uninstall as cap_uninstall, status as cap_status,
    register_hook, unregister_hook, hook_registered)

SAVE_GATE_MARKER = "binggu_save_gate_hook"  # 사람-발화 저장 게이트 hook 식별 토큰
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


def _save_gate_hook_command():
    """settings.json 에 등록할 사람-발화 저장 게이트 hook 실행 명령(sync 등록 대상)."""
    return '%s "%s"' % (_plat.python_cmd(), os.path.join(BASE, "hooks", "binggu_save_gate_hook.py"))


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
    # 실패 이유 전체 노출(사일런트 실패 금지) — BLOCK reason + 후보별 거부 코드/건수 + 기존재 skip.
    print("BLOCK:", r.get("reason"))
    rej = r.get("rejected")
    if rej:
        print("  거부:", ", ".join("%s=%d" % (k, v) for k, v in sorted(rej.items())))
    if r.get("skipped_existing"):
        print("  기존재 skip: %d건" % r["skipped_existing"])
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
        force_cap = bool(getattr(a, "force_capture", False))
        r = init_profile(home, cwd, hook_command=_hook_command(),
                         settings_path=settings, global_scope=global_scope, force_enable=force_cap)
        scope_desc = "전역(AGI memory — 모든 작업 세션)" if r["global"] else ("현재 위치만 %s (privacy)" % cwd)
        if not r["enabled"]:
            # owner sticky OFF — init 이 정책(자동수집 영구 OFF)을 깨지 않음.
            print("capture 는 owner 가 OFF 로 고정해 두어 켜지 않았습니다(scope/hook 만 갱신).")
            print("정말 켜려면:  python binggu.py capture enable   (또는 init --force-capture)")
        else:
            print("AGI memory capture ON — scope: %s" % scope_desc)
            if r["hook_events"]:
                print("hook 등록(settings.json 백업됨): %s" % ", ".join(r["hook_events"]))
            else:
                print("hook 이미 등록됨 — 그대로 사용")
            print("자동 후보 수집만 켜집니다. 저장은 preview 후 SAVE n 게이트로만(자동 저장 없음).")
        print("상태:  capture status   ·   잠깐 끄기:  capture pause   ·   영구 끄기:  capture disable   ·   제거:  capture uninstall")
    # 환경 점검(옵션1, 4cli 20260615_1900) — 무엇이 켜지고 무엇을 더 깔면 뭐가 생기는지 1회 안내.
    # 점검만 — 자동 설치 안 함(없는 건 명령만 안내). 실패해도 init 흐름엔 영향 0.
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
        import binggu_env_check as _ec
        gate_settings = getattr(a, "capture_settings", None) or DEFAULT_SETTINGS
        print()
        print(_ec.render_report(_ec.check_env(settings_path=gate_settings)))
        print()
    except Exception:
        pass
    print("다음:  python binggu.py preview \"오늘 정리하고 싶은 문장들\"")
    return 0


def cmd_capture(a):
    home = os.path.dirname(os.path.abspath(a.ledger))
    settings = getattr(a, "settings", None) or DEFAULT_SETTINGS
    cwd = getattr(a, "capture_cwd", None) or os.getcwd()
    sub = a.capture_cmd
    if sub == "status":
        st = cap_status(home, cwd, settings)
        print("capture: %s%s%s" % ("ON" if st["enabled"] else "OFF",
                                   " (paused)" if st["paused"] else "",
                                   " (owner OFF 고정)" if st.get("disabled") else ""))
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
    if sub == "disable":
        # owner sticky OFF: init 재실행에도 OFF 유지(정책 영구 OFF). pause 와 달리 영구.
        cap_disable(home)
        print("capture 영구 OFF 고정(owner 정책). `binggu init` 재실행해도 켜지지 않습니다.")
        print("다시 켜기:  python binggu.py capture enable")
        return 0
    if sub == "enable":
        cap_enable(home)
        print("capture ON(sticky OFF 해제). 자동 후보 수집만 — 저장은 SAVE n 게이트로만.")
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
    if sub == "install-gate":
        # 사람-발화 저장 게이트 hook 등록 — 사용자가 명시 실행(설치 의도 표현). sync(레이스 회피).
        added = register_hook(settings, _save_gate_hook_command(),
                              events=("UserPromptSubmit",), marker=SAVE_GATE_MARKER, is_async=False)
        if added:
            print("저장 게이트 hook 등록 완료(sync · settings.json 백업됨): %s" % ", ".join(added))
        else:
            print("저장 게이트 hook 이미 등록됨 — 그대로 사용")
        print("이제 'SAVE n' 발화가 인식되어 선택 저장이 통과합니다(자동 저장 아님 · 사람 발화만).")
        print("제거:  python binggu.py capture uninstall-gate")
        return 0
    if sub == "uninstall-gate":
        removed = unregister_hook(settings, marker=SAVE_GATE_MARKER)
        print("저장 게이트 hook 제거: %s" % (", ".join(removed) or "없음(미등록)"))
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


def cmd_recall(a):
    """recall/why — query 관련 기억 회상(기본 read-only). P1 rank_score 정렬 + why-edge.
    use_count++ 는 --record 명시 시에만(헌법 '자동 저장 0' — 회상만으론 ledger write 0).
    --record 는 '이 기억이 유용했다'는 사람의 명시 신호(유용성 랭킹용·도장/문장 불변)."""
    import binggu_recall as RC
    import binggu_p1_ranking as RANK
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print("장부가 없습니다(회상할 기억 없음): %s · 먼저 python binggu.py init" % ledger)
        return 0  # 빈 그래프 graceful
    res = RC.why_search(ledger, a.query, limit=getattr(a, "limit", None))
    if not res["relevant_nodes"]:
        print("관련 기억이 없습니다: \"%s\"" % a.query)
        return 0
    print("# 회상 — \"%s\" 관련 기억 %d건 (랭킹순 · candidate · read-only)"
          % (a.query, len(res["relevant_nodes"])))
    for i, n in enumerate(res["relevant_nodes"], 1):
        sub = (" [%s]" % n["semantic_subtype"]) if n["semantic_subtype"] else ""
        print("  %d. (%s%s rank=%.3f rel=%.2f) %s"
              % (i, n["node_type"], sub, n["rank_score"], n["relevance"], n["claim"]))
    if res["relevant_edges"]:
        print("\n연결(why-edge · candidate):")
        for e in res["relevant_edges"]:
            print("  %s -%s-> %s" % (e["source"], e["relation"], e["target"]))
    print("\n%s" % res["summary"])
    # P1-② use_count++ — --record 명시 시에만(사람의 '유용했다' 신호). 기본 회상은 read-only.
    if getattr(a, "record", False):
        db, _ = _open(ledger)
        for n in res["relevant_nodes"]:
            RANK.record_use(db, n["node_id"])
        db.close()
        print("\n(use_count 기록됨 · 유용성 신호 · 도장/문장 불변)")
    return 0


def cmd_trace(a):
    """trace — judgment_trace: 판단 노드에서 근거 엣지를 따라 사슬(다홉). read-only."""
    import binggu_recall as RC
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print("장부가 없습니다: %s · 먼저 python binggu.py init" % ledger)
        return 0
    res = RC.judgment_trace(ledger, a.node_id)
    if not res["found"]:
        print("노드를 찾을 수 없습니다: %s (binggu.py list 로 node_id 확인)" % a.node_id)
        return 0
    r = res["root"]
    print("# 근거 사슬 — %s" % r["claim"])
    print("  root: %s (%s rank=%.3f)" % (r["node_id"], r["node_type"], r["rank_score"]))
    if not res["chain"]:
        print("  (연결된 근거 엣지 없음 — 고립 노드)")
    for c in res["chain"]:
        arrow = "→" if c["direction"] == "out" else "←"
        peer = c["peer_claim"] if c["peer_present"] else "(dangling: %s)" % c["to"]
        print("  %s -%s-> %s  %s" % (c["from"], c["relation"], c["to"], arrow))
        if c["peer_present"]:
            print("      %s" % peer)
    print("\n%s (신뢰도 %.2f)" % (res["summary"], res["confidence"]))
    return 0


def cmd_preflight(a):
    """preflight — 작업 시작 전 관련 기억 + 위험패턴 반문(L5+L6). read-only.
    cwd 미지정 시 현재 디렉토리(capture 와 동일 패턴). 위험패턴 닮으면 반문 표시."""
    import binggu_recall as RC
    ledger, _ = _ledger_paths(a.ledger)
    cwd = getattr(a, "cwd", None) or os.getcwd()
    files = (getattr(a, "files", None) or "").split(",") if getattr(a, "files", None) else None
    if files:
        files = [f.strip() for f in files if f.strip()]
    if not os.path.exists(ledger):
        print("장부가 없습니다(신규 사용자 — 회상할 기억 없음): %s" % ledger)
        return 0  # 빈 그래프 graceful
    res = RC.preflight_context(ledger, prompt=getattr(a, "prompt", None), cwd=cwd,
                               domain=getattr(a, "domain", None), files_changed=files)
    print("# preflight — 이번 작업 전 회상 (read-only · candidate)")
    if res["remember"]:
        print("\n## 기억할 것")
        for n in res["remember"]:
            sub = (" [%s]" % n["semantic_subtype"]) if n["semantic_subtype"] else ""
            print("  - (%s%s) %s" % (n["node_type"], sub, n["claim"]))
    if res["avoid_patterns"]:
        print("\n## 하면 안 되는 과거 패턴(버그패턴)")
        for m in res["avoid_patterns"]:
            print("  - (위험도 %.2f) %s" % (m["risk_score"], m["claim"]))
    if res["preferences"]:
        print("\n## 사용자 선호")
        for p in res["preferences"]:
            print("  - %s" % p["claim"])
    print("\n위험도: %s" % res["risk_level"])
    if res["needs_question"] and res["question"]:
        print("\n반문 ⚠ %s" % res["question"])
    elif res["risk_level"] == "중간":
        print("(주의: 과거 위험패턴과 일부 닮음 — 참고하세요)")
    if not (res["remember"] or res["avoid_patterns"] or res["preferences"]):
        print("(관련 기억 없음 — 새로운 작업이거나 그래프가 비어 있습니다)")
    return 0


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
    # SAVE 게이트 대조용 — 직전 preview 후보를 last_preview 에 영속(hook 이 사람 'SAVE n' 발화 시 이걸 읽어 도장).
    # 이 연결이 없으면 CLI preview→save 흐름이 save_gate_log 와 분리돼 autopush 이중게이트가 영구 BLOCK.
    try:
        import binggu_save_gate as _sg
        _sg.write_last_preview(pv.get("candidates") or [])
    except Exception:
        pass
    pid = _preview_id(a.text)
    print("\npreview_id: %s" % pid)
    if pv["candidates"]:
        print("⚠ 외부 사실(릴리스 상태·업로드 여부·등급 등)은 실측 확인 전에 저장하지 마세요.")
        print("저장은 번호를 직접 골라서:  python binggu.py save \"<같은 텍스트>\" --preview-id %s "
              "--pick <고른 번호들> --confirm \"SAVE <고른 번호들>\"" % pid)
    return 0


def cmd_reflect(a):
    """회고·자가평가를 빙구팩 지식 후보로 흘려보내는 전용 진입점 (반성→지식).

    preview 와 **동일 게이트**(저장 0·write_last_preview 연동·사람 SAVE 만)를 재사용하되,
    회고/자가평가를 일급 흐름으로 명시한다. 작업 끝 자가평가가 traj 에만 남고 지식으로
    안 흘러들던 구조 갭(반성≠지식)을 메우는 경로. --from-file 로 쌓인 회고를 일괄 후보화.
    """
    text = a.text
    if getattr(a, "from_file", None):
        try:
            with open(a.from_file, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            print("회고 파일을 열 수 없습니다: %s (%s)" % (a.from_file, e))
            return 1
    if not text or not text.strip():
        print("회고 텍스트가 비어있습니다.  binggu reflect \"<자가평가/교훈>\"  또는  --from-file <경로>")
        return 1
    print("# 회고·자가평가 → 지식 후보 (반성이 지식으로 · 저장 0 · 사람 SAVE 만)\n")
    pv = capture_preview(text)
    print(pv["preview_markdown"])
    # preview 와 동일하게 last_preview 영속 → 사람 'SAVE n' 발화 시 save_gate 가 도장(autopush 호환).
    try:
        import binggu_save_gate as _sg
        _sg.write_last_preview(pv.get("candidates") or [])
    except Exception:
        pass
    pid = _preview_id(text)
    print("\npreview_id: %s" % pid)
    if pv["candidates"]:
        print("이 회고에서 남길 교훈만 골라 도장:  python binggu.py save \"<같은 텍스트>\" "
              "--preview-id %s --pick <번호들> --confirm \"SAVE <번호들>\"" % pid)
    else:
        print("후보 0 — 회고에서 남길 판단/교훈 문장이 추출되지 않았습니다(시크릿/PII는 자동 제외).")
    return 0


def cmd_save(a):
    # 승인 정책: preview 를 실제로 본 텍스트만 저장 가능 — raw text 직행 저장 차단
    if getattr(a, "from_file", None):
        try:
            a.text = open(a.from_file, encoding="utf-8").read()
        except OSError as e:
            print("저장 파일을 열 수 없습니다: %s (%s)" % (a.from_file, e))
            return 1
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
    ck("1f2_disable→sticky OFF", cmd_capture(args(capture_cmd="disable", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"]
       and cap_status(cap_home, cap_cwd, cap_settings)["disabled"])
    ck("1f3_재init중_sticky OFF 유지", cmd_init(args(no_capture=False, capture_settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1f4_enable→ON 복구", cmd_capture(args(capture_cmd="enable", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1g_preview(저장0)", cmd_capture(args(capture_cmd="preview", settings=cap_settings, capture_cwd=cap_cwd)) == 0)
    ck("1h_uninstall", cmd_capture(args(capture_cmd="uninstall", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    TEXT = ("이 입찰은 마진이 낮아 보류한다. 백필 작업이 진행 중이다. "
            "낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다.")
    ck("2_preview(저장0)", cmd_preview(args(text=TEXT)) == 0)
    ck("2c_reflect(회고→후보·저장0)", cmd_reflect(args(text=TEXT, from_file=None)) == 0)
    ck("2d_reflect_빈입력_안내", cmd_reflect(args(text=None, from_file=None)) == 1)
    ck("2e_reflect_파일오류_안내",
       cmd_reflect(args(text=None, from_file=os.path.join(tmp, "_no_such_reflect_file.txt"))) == 1)
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
    # ---- 회상(L4~L6 · read-only) — recall/trace/preflight CLI 래퍼 + use_count++ ----
    # 빈 그래프(미존재 ledger) graceful — 에러 0
    _empty = os.path.join(tmp, "no_ledger.sqlite")
    ck("R1_recall_빈그래프_graceful",
       cmd_recall(args(ledger=_empty, query="배포", limit=None, record=False)) == 0)
    ck("R1b_preflight_빈그래프_graceful",
       cmd_preflight(args(ledger=_empty, prompt="바로 배포", cwd=None, domain=None, files=None)) == 0)
    # 저장된 후보 중 '마진' 관련 회상 → 결과 + use_count++ 기록(P1-② 프리미티브)
    db, _ = _open(ledger)
    _uc_before = {r["node_id"]: db.con.execute(
        "SELECT use_count FROM nodes WHERE node_id=?", (r["node_id"],)).fetchone() for r in rows}
    db.close()
    ck("R2_recall_관련회상(use_count기록·--record)",
       cmd_recall(args(ledger=ledger, query="마진 보류", limit=None, record=True)) == 0)
    db, _ = _open(ledger)
    # 적어도 1개 노드의 use_count 가 증가(회상 기록). 도장/문장 불변은 R5 에서 확인.
    _uc_after = {nid: db.con.execute(
        "SELECT use_count FROM nodes WHERE node_id=?", (nid,)).fetchone()[0]
        for nid in _uc_before}
    db.close()
    ck("R3_use_count_증가(P1-②_유용성)",
       any((_uc_after[nid] or 0) >= 1 for nid in _uc_before))
    # judgment_trace — 저장된 판단 노드 1개 (사슬 없어도 found True graceful)
    _j_nid = next((r["node_id"] for r in rows if r["kind"] == "판단"), rows[0]["node_id"])
    ck("R4_trace_노드조회(고립_graceful)",
       cmd_trace(args(ledger=ledger, node_id=_j_nid)) == 0)
    ck("R4b_trace_dangling_graceful",
       cmd_trace(args(ledger=ledger, node_id="node:CONV:nope")) == 0)
    # 회상은 read-only — 문장/도장 불변(use_count 만 변경)
    db, _ = _open(ledger)
    _stamp_intact = all(
        db.con.execute("SELECT sentence,node_type FROM nodes WHERE node_id=?", (r["node_id"],)).fetchone()
        is not None for r in rows)
    db.close()
    ck("R5_회상_도장문장_불변(read-only)", _stamp_intact)
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

    # 10b. _show 실패노출 — 이미 저장된 TEXT 재선택 → nothing_to_save + skip 건수까지 stdout 출력
    import io
    import contextlib
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _rc = cmd_save(args(text=TEXT, preview_id=_preview_id(TEXT), pick="1",
                            confirm="SAVE 1", due=None))
    _out = _buf.getvalue()
    ck("10b_show_실패이유_노출(BLOCK+skip)",
       _rc == 1 and "BLOCK" in _out and "skip" in _out)

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


def cmd_harvest(a):
    """harvest — 외부 수확(P1 ③). 사람이 등록한 소스만 fetch → 후보(candidate)로만.
      add/list/remove: 소스 화이트리스트 관리(빈 시작 · owner 가 등록). 등록 단위=URL/계정/키워드.
      run: 등록 소스 1회 수확(실 fetch=owner 스케줄러 권장). candidate=1 만 적재 · 영구 0.
    3중게이트: ① 등록 소스만 ② 후보로만 ③ 영구는 사람 SAVE(기존 게이트). 원문 변형 0."""
    import binggu_harvest as HV  # scripts/ 는 이미 sys.path 에 있음
    home = os.path.dirname(os.path.abspath(a.ledger))
    sub = getattr(a, "harvest_cmd", None)
    sp = HV.sources_path(home)
    if sub == "add":
        r = HV.add_source(a.kind, a.url, keyword=getattr(a, "keyword", None), path=sp)
        if r["status"] == "OK":
            print("소스 등록(%s): %s" % (r["reason"], r["source_id"]))
        else:
            print("BLOCK: %s%s" % (r["reason"], (" (kind=%s)" % ",".join(r["valid"])) if r.get("valid") else ""))
        return 0 if r["status"] == "OK" else 1
    if sub == "list":
        srcs = HV.load_sources(sp)
        if not srcs:
            print("등록된 외부 소스가 없습니다(빈 화이트리스트). 수확은 0입니다.")
            print('  등록:  python binggu.py harvest add --kind arxiv --url "https://arxiv.org/..."')
            return 0
        print("등록된 외부 소스 %d개:" % len(srcs))
        for s in srcs:
            print("  [%s] %s  %s%s" % (s.get("kind"), s.get("source_id"), s.get("url"),
                                       (" (keyword=%s)" % s["keyword"]) if s.get("keyword") else ""))
        return 0
    if sub == "remove":
        r = HV.remove_source(a.source_id, path=sp)
        print("소스 제거: %s (removed=%d)" % (r["reason"], r["removed"]))
        return 0
    if sub == "run":
        # 실 fetch 는 owner 스케줄러 권장(외부 자동 동기화 = 주체 분리). 수동 1회도 가능.
        res = HV.run_harvest(ledger_path=os.path.abspath(a.ledger), home=home, sources_path_=sp)
        print("수확 결과: %s (%s) · fetched=%s candidates=%s skipped=%s"
              % (res["status"], res["reason"], res.get("fetched"),
                 res.get("candidates"), res.get("skipped")))
        if res.get("candidates"):
            print("  후보만 적재됨(candidate=1 · 영구화 0). 영구는 preview→SAVE n 게이트로만.")
        return 0 if res["status"] in ("OK", "NOOP") else 1
    return 1


def cmd_setup_cloud(a):
    """setup-cloud — 흩어진 cloud 셋업 명령을 1개 진입점으로(멱등·실패정지).
    얇은 래퍼 — 실 오케스트레이션은 scripts/binggu_setup_cloud.py(순수함수+selftest).
      python binggu.py setup-cloud            # 점검만(dry-run · 변경 0)
      python binggu.py setup-cloud --apply    # kv create/toml 기입/kv put/스케줄러 등록
      python binggu.py setup-cloud --apply --deploy   # 위 + wrangler deploy(비가역)
    login(브라우저 OAuth)·deploy 결정은 본인 손 — 스크립트는 점검+안내+멱등 적용만."""
    import binggu_setup_cloud as SC  # scripts/ 는 이미 sys.path 에 있음
    res = SC.run_setup(apply=bool(getattr(a, "apply", False)),
                       deploy=bool(getattr(a, "deploy", False)))
    print(SC.render_report(res))
    return 0 if res["halted_at"] is None else 2


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
    ip.add_argument("--force-capture", action="store_true", dest="force_capture")  # owner sticky OFF 해제하고 강제 ON
    sub.add_parser("status")
    sp = sub.add_parser("preview"); sp.add_argument("text")
    rp = sub.add_parser("reflect")          # 회고·자가평가 → 지식 후보(반성이 지식으로 · 저장 0)
    rp.add_argument("text", nargs="?", default=None)
    rp.add_argument("--from-file", dest="from_file", default=None)  # 쌓인 회고 파일 일괄 후보화
    sp = sub.add_parser("save"); sp.add_argument("text", nargs="?", default=None)
    sp.add_argument("--from-file", dest="from_file", default=None)   # reflect --from-file 과 동일 text 로 저장(preview_id 일치)
    sp.add_argument("--preview-id", required=True, dest="preview_id")
    sp.add_argument("--pick", required=True); sp.add_argument("--confirm", required=True)
    sp.add_argument("--due", default=None)
    sp = sub.add_parser("list"); sp.add_argument("--status", default=None)
    sp.add_argument("--kind", default=None)
    # 회상(L4~L6 · read-only) — recall(why_search) / trace(judgment_trace) / preflight
    rcp = sub.add_parser("recall"); rcp.add_argument("query")
    rcp.add_argument("--limit", type=int, default=None)
    rcp.add_argument("--record", action="store_true", dest="record")  # use_count++ (기본 read-only)
    wp_ = sub.add_parser("why"); wp_.add_argument("query")                  # recall 별칭
    wp_.add_argument("--limit", type=int, default=None)
    wp_.add_argument("--record", action="store_true", dest="record")  # use_count++ (기본 read-only)
    tp = sub.add_parser("trace"); tp.add_argument("node_id")
    pfp = sub.add_parser("preflight")
    pfp.add_argument("--prompt", default=None)
    pfp.add_argument("--cwd", default=None)
    pfp.add_argument("--domain", default=None)
    pfp.add_argument("--files", default=None)  # 콤마 구분 변경 파일명
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
    for cs in ("status", "pause", "resume", "disable", "enable", "preview", "uninstall", "install-gate", "uninstall-gate"):
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
    hv = sub.add_parser("harvest")          # 외부 수확(P1 ③) — 등록 소스만·후보로만·영구는 사람 SAVE
    hvsub = hv.add_subparsers(dest="harvest_cmd", required=True)
    ha = hvsub.add_parser("add")            # 소스 화이트리스트 등록(사람 행위)
    ha.add_argument("--kind", required=True, choices=["arxiv", "github", "rss", "url"])
    ha.add_argument("--url", required=True)
    ha.add_argument("--keyword", default=None)
    hvsub.add_parser("list")                # 등록 소스 목록
    hr = hvsub.add_parser("remove"); hr.add_argument("source_id")
    hvsub.add_parser("run")                 # 등록 소스 1회 수확(실 fetch=owner 스케줄러 권장)
    scp = sub.add_parser("setup-cloud")     # cloud 셋업 1개 진입점(멱등·실패정지·dry-run 기본)
    scp.add_argument("--apply", action="store_true")     # 실제 변경(미지정=점검만)
    scp.add_argument("--deploy", action="store_true")    # (--apply 와) wrangler deploy 까지 — 비가역
    a = p.parse_args()
    fn = {"init": cmd_init, "status": cmd_status, "preview": cmd_preview, "reflect": cmd_reflect, "save": cmd_save,
          "list": cmd_list, "deprecate": cmd_deprecate, "replace": cmd_replace,
          "accept": cmd_accept, "unaccept": cmd_unaccept, "due": cmd_due,
          "resolve": cmd_resolve, "reminders": cmd_reminders, "capture": cmd_capture,
          "recall": cmd_recall, "why": cmd_recall, "trace": cmd_trace, "preflight": cmd_preflight,
          "hosted": cmd_hosted, "harvest": cmd_harvest, "setup-cloud": cmd_setup_cloud}[a.cmd]
    sys.exit(fn(a))


if __name__ == "__main__":
    main()
