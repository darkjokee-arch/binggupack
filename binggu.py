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
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "scripts"))

# 트랙 C(C3): storage 진입점은 facade 경유로 정리(호출 경계). scripts 직접 import 도 호환 유지.
from binggupack.storage import (  # noqa: E402
    OPERATING_PATHS, set_review_due, resolve_review, list_due_reminders)
from openbinggu_candidate_list_view import list_candidates  # noqa: E402
from binggupack.storage import save_selected  # noqa: E402  (트랙 C: scripts 직접 import → storage facade)
from openbinggu_conversation_capture_preview import capture_preview  # noqa: E402
from openbinggu_candidate_deprecate_ux import deprecate_from_list  # noqa: E402
from openbinggu_candidate_replace_ux import replace_from_list  # noqa: E402
from openbinggu_owner_accept_ux import (  # noqa: E402
    open_accept, accept_from_list, unaccept_from_list, accepted_view, accept_by_node_id)
from binggu_capture_profile import (  # noqa: E402
    init_profile, pause as cap_pause, resume as cap_resume,
    disable_capture as cap_disable, enable_capture as cap_enable,
    uninstall as cap_uninstall, status as cap_status,
    register_hook, unregister_hook, hook_registered)

SAVE_GATE_MARKER = "binggu_save_gate_hook"  # 사람-발화 저장 게이트 hook 식별 토큰
PREFLIGHT_MARKER = "binggu_preflight_hook"  # preflight 자동주입 hook 식별 토큰
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


def _preflight_hook_command():
    """settings.json 에 등록할 preflight 자동주입 hook 실행 명령(UserPromptSubmit)."""
    return '%s "%s"' % (_plat.python_cmd(), os.path.join(BASE, "hooks", "binggu_preflight_hook.py"))


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
    res = RC.why_search(ledger, a.query, limit=getattr(a, "limit", None),
                        home=os.path.dirname(ledger))
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
    # 회상 조언 적중 기록 안내(작업A) — nonce 로 위조(D-1)·이중계상(D-2) 방어. 사람 확정만 기록.
    from binggupack.pack import hit_recording as _HR
    _nonce = _HR.recall_nonce(a.query, [n["node_id"] for n in res["relevant_nodes"]])
    print("  → 맞았으면:  python binggu.py mark-hit \"%s\" --index N --nonce %s" % (a.query, _nonce))
    print("     틀렸으면 mark-miss (N=위 번호). 자동 기록 0 · 사람 확정만.")
    # P1-② use_count++ — --record 명시 시에만(사람의 '유용했다' 신호). 기본 회상은 read-only.
    # 작업B: use_key(회상 스냅샷) 로 채택 멱등 — 같은 회상 반복 --record 는 정렬에 재기여 0.
    if getattr(a, "record", False):
        db, _ = _open(ledger)
        use_key = RANK.adoption_key(a.query, getattr(a, "domain", None))
        for n in res["relevant_nodes"]:
            RANK.record_use(db, n["node_id"], use_key=use_key)
        db.close()
        print("\n(use_count 기록됨 · 유용성 신호 · 채택멱등[같은 회상 재기여 0] · 도장/문장 불변)")
    return 0


def _reason_hint(verdict):
    import binggu_recall_trace as RT
    return ", ".join(RT.REASON_CODES.get(verdict, ())) or "(없음)"


def _judgment_trace_show(ledger, node_id):
    """기존 judgment_trace — 판단 노드 근거 사슬(다홉). read-only."""
    import binggu_recall as RC
    if not os.path.exists(ledger):
        print("장부가 없습니다: %s · 먼저 python binggu.py init" % ledger)
        return 0
    res = RC.judgment_trace(ledger, node_id, home=os.path.dirname(ledger))
    if not res["found"]:
        print("노드를 찾을 수 없습니다: %s (binggu.py list 로 node_id 확인)" % node_id)
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


def _trace_review(RT, ledger, home):
    """미판정 회상 목록 + 번호→(trace,node) 스냅샷 저장(원문 0). 효용 판정 대기."""
    pend = RT.list_pending(home=home, ledger_path=ledger)
    if not pend:
        print("미판정 회상이 없습니다.")
        print("(preflight 자동주입이 일어나고 opt-in 이 켜져 있어야 쌓입니다 — binggu trace enable)")
        return 0
    RT.save_review_snapshot(pend, home=home)
    print("# 미판정 회상 %d건 — 효용 판정 대기 (candidate · 사람 판정만)" % len(pend))
    for p in pend:
        cat = (" [%s]" % p["category"]) if p["category"] else ""
        rank = (" score=%.2f" % p["rank"]) if isinstance(p["rank"], (int, float)) else ""
        claim = p["claim"] or ("(원문 미상 · node_id %s)" % p["node_id"])
        print("  %d. %s%s%s" % (p["idx"], claim, cat, rank))
        print("     판정: binggu trace mark %d used|ignored|corrected [--note <code>]" % p["idx"])
    print("\nreason_code(--note): ignored→%s · corrected→%s"
          % (_reason_hint("ignored"), _reason_hint("corrected")))
    return 0


def cmd_trace(a):
    """trace — 회상 효용 trace(review/mark/enable/disable) + 근거 사슬(show/<node_id>).

    binggu trace [review]          : 미판정 회상 목록(효용 판정 대기)
    binggu trace mark N <verdict>  : N 번 회상 판정 used|ignored|corrected (--note <reason_code>)
    binggu trace enable | disable  : 효용 trace 기록 opt-in 파일플래그(preflight 패턴 통일)
    binggu trace show <node_id>    : (기존) 판단 노드 근거 사슬
    binggu trace <node:CONV:...>   : (하위호환) show 와 동일
    """
    import binggu_recall_trace as RT
    from datetime import datetime, timezone
    ledger, _ = _ledger_paths(a.ledger)
    home = os.path.dirname(os.path.abspath(ledger))
    a1 = getattr(a, "a1", None)

    if a1 == "enable":
        r = RT.set_trace_flag(True, home=home)
        print("회상 효용 trace 기록 ON (opt-in): %s" % r["flag_path"])
        print("preflight 자동주입 시 회상 메타가 기록됩니다(원문 0). 끄기: binggu trace disable")
        return 0
    if a1 == "disable":
        RT.set_trace_flag(False, home=home)
        print("회상 효용 trace 기록 OFF (기록 0)")
        return 0
    if a1 == "mark":
        try:
            n = int(a.a2)
        except (TypeError, ValueError):
            print("사용법: binggu trace mark <N> <used|ignored|corrected> [--note <reason_code>]")
            return 2
        verdict = a.a3
        if verdict not in RT.VALID_VERDICTS:
            print("verdict 는 used|ignored|corrected (받음: %r)" % verdict)
            return 2
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = RT.mark_by_index(n, verdict, {"actor": "human"}, ts,
                               reason_code=getattr(a, "note", None), home=home)
        if res["recorded"]:
            note = (" · note=%s" % res["reason_code"]) if res.get("reason_code") else ""
            print("판정 기록: #%d → %s%s (actor=human)" % (n, verdict, note))
            return 0
        hint = {"need_review": "먼저 binggu trace review 로 목록을 보세요.",
                "bad_index": "그 번호의 회상이 없습니다(review 재실행).",
                "dup_outcome": "이미 판정된 회상(첫 판정 보존).",
                "invalid_reason_code": "note 는 정해진 코드만: %s" % _reason_hint(verdict),
                "trace_not_found": "trace 를 찾을 수 없습니다.",
                "G4_no_auto": "actor=human 만 판정 가능(헌법)."}.get(res["reason"], res["reason"])
        print("판정 안 됨(%s): %s" % (res["reason"], hint))
        return 0
    if a1 in (None, "review"):
        return _trace_review(RT, ledger, home)
    # show <node_id> | <node:CONV:...> (judgment_trace · 하위호환)
    node_id = a.a2 if a1 == "show" else a1
    return _judgment_trace_show(ledger, node_id)


def cmd_preflight(a):
    """preflight — 작업 시작 전 관련 기억 + 위험패턴 반문(L5+L6). read-only.
    cwd 미지정 시 현재 디렉토리(capture 와 동일 패턴). 위험패턴 닮으면 반문 표시.

    자동주입(UserPromptSubmit hook) 설치/토글:
      --install   : settings.json 에 preflight hook 등록(대화 상단 자동주입 · async)
      --uninstall : hook 제거
      --enable    : 자동주입 ON(~/.binggupack/preflight_enabled 플래그 · 기본 OFF)
      --disable   : 자동주입 OFF(플래그 삭제)
      --auto-status : 등록/활성 상태 표시
    설치+활성 둘 다여야 자동주입이 동작한다(기본 OFF — 타 세션 무부담)."""
    home = os.path.dirname(os.path.abspath(a.ledger))
    settings = getattr(a, "settings", None) or DEFAULT_SETTINGS
    flag = os.path.join(home, "preflight_enabled")
    if getattr(a, "install", False):
        added = register_hook(settings, _preflight_hook_command(),
                              events=("UserPromptSubmit",), marker=PREFLIGHT_MARKER, is_async=True)
        print("preflight 자동주입 hook 등록 완료(settings.json 백업됨): %s" % ", ".join(added)
              if added else "preflight hook 이미 등록됨 — 그대로 사용")
        print("활성화는 별도:  python binggu.py preflight --enable   (기본 OFF)")
        return 0
    if getattr(a, "uninstall", False):
        removed = unregister_hook(settings, marker=PREFLIGHT_MARKER)
        print("preflight 자동주입 hook 제거: %s" % (", ".join(removed) or "없음(미등록)"))
        return 0
    if getattr(a, "enable", False):
        os.makedirs(home, exist_ok=True)
        with open(flag, "w", encoding="utf-8") as f:
            f.write("1")
        print("preflight 자동주입 ON. 작업 발화 시 관련 기억이 상단에 표시됩니다(정보만 · 저장 0 · 차단 0).")
        print("끄기:  python binggu.py preflight --disable")
        return 0
    if getattr(a, "disable", False):
        if os.path.exists(flag):
            os.remove(flag)
        print("preflight 자동주입 OFF(플래그 삭제). 수동 회상은 `python binggu.py preflight --prompt ...` 로 가능.")
        return 0
    if getattr(a, "auto_status", False):
        reg = hook_registered(settings, marker=PREFLIGHT_MARKER)
        print("preflight 자동주입 — hook 등록: %s · 활성(플래그): %s"
              % ({True: "예", False: "아니오", None: "미확인"}[reg],
                 "예(ON)" if os.path.exists(flag) else "아니오(OFF)"))
        print("자동주입은 '등록 AND 활성' 둘 다여야 동작합니다.")
        return 0
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
                               domain=getattr(a, "domain", None), files_changed=files,
                               home=os.path.dirname(ledger))
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
    # P1-② use_count++ — --record 명시 시에만(사람의 '이 회상 유용했다' 신호). 기본 preflight 는 read-only.
    if getattr(a, "record", False):
        import binggu_p1_ranking as RANK
        db, _ = _open(ledger)
        # 작업B: preflight 회상도 채택 멱등(같은 prompt+domain 반복 --record 재기여 0).
        use_key = RANK.adoption_key(getattr(a, "prompt", None) or "preflight", getattr(a, "domain", None))
        seen = set()
        for group in (res["remember"], res["avoid_patterns"], res["preferences"]):
            for n in group:
                nid = n.get("node_id")
                if nid and nid not in seen:
                    seen.add(nid)
                    RANK.record_use(db, nid, use_key=use_key)
        db.close()
        print("\n(use_count 기록됨 %d건 · 유용성 신호 · 채택멱등 · 도장/문장 불변)" % len(seen))
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


def cmd_preview(a, explicit=False):
    # explicit: remember(명시 저장 의도) 경로면 True — 판단-veto 면제(안전 게이트는 유지).
    pv = capture_preview(a.text, explicit=explicit)
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
        _x = " --explicit" if explicit else ""
        print("저장은 번호를 직접 골라서:  python binggu.py save \"<같은 텍스트>\" --preview-id %s "
              "--pick <고른 번호들> --confirm \"SAVE <고른 번호들>\"%s" % (pid, _x))
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


def _gate_log_for_ledger(ledger):
    """gate scope 를 ledger scope 에 고정(split-brain 차단·hermetic). 운영에선 ledger dir==home 이라
    gate_path() 와 동일 경로. --ledger 격리/selftest 에선 gate 도 그 dir 를 따른다. 파일명은
    save_gate 모듈에서 취해 하드코딩 회피."""
    import binggu_save_gate as _sg
    return os.path.join(os.path.dirname(os.path.abspath(ledger)),
                        os.path.basename(_sg.gate_path()))


def _resolve_human_ctx(ledger, sents, confirm):
    """운영 ledger write 의 'human' 주장을 신뢰 신호로 검증해 실제 actor ctx 를 만든다.

    Fix E: 종전엔 CLI 가 actor="human" 을 하드코딩해 위조방지 기록장(save_gate.gate_human_for)이
    write 경로에서 미소비였다(텍스트만 알면 preview_id·confirm 을 AI 도 합성 가능 → 사람 확인 없이
    운영 저장). 이제 write 직전 게이트를 실제로 소비한다. ★오버블록 회피(회귀0):
      1) 게이트 기록 존재(사람 SAVE 발화를 hook 이 append — AI 위조 불가) → human 확정(save_gate)
      2) 대화형 TTY(사장님 키보드) 또는 명시 신뢰 플래그(BINGGU_TRUSTED_CLI) → human 유지 + 감사표식
      3) 비대화형(자동화/파이프) + 게이트 기록 없음 = 사람 확인 미검증 → 기본은 경고+감사(강등 안 함,
         정당한 대화형 저장·pair 를 안 깨기 위함), BINGGU_STRICT_HUMAN_GATE=1 이면 비-human 으로
         강등해 기존 G4_no_auto BLOCK 으로 **코드 강제**(opt-in).
    """
    ctx = {"actor": "human", "confirm": confirm}
    # 1) 위조방지 기록장 대조 (hook 이 쓴 사람 SAVE 발화 hash — AI 는 UserPromptSubmit 를 못 거쳐 위조 불가)
    try:
        import binggu_save_gate as _sg
        if sents and _sg.gate_human_for(sents, path=_gate_log_for_ledger(ledger)):
            ctx["actor_source"] = "save_gate"
            return ctx
    except Exception:
        pass  # 게이트 부재/오류 → 아래 신뢰 신호로 판단(default 는 오버블록 회피)
    # 2) 대화형 터미널(사장님이 직접 타이핑) / 명시 신뢰 플래그
    trusted = os.environ.get("BINGGU_TRUSTED_CLI", "").strip().lower() in ("1", "true", "yes", "on")
    try:
        interactive = sys.stdin.isatty()
    except Exception:
        interactive = False
    if interactive or trusted:
        ctx["actor_source"] = "tty" if interactive else "trust_flag"
        return ctx
    # 3) 비대화형 + 게이트 기록 없음 = 사람 확인 미검증
    if os.environ.get("BINGGU_STRICT_HUMAN_GATE", "").strip().lower() in ("1", "true", "yes", "on"):
        ctx["actor"] = "cli_unverified"          # → save_selected/save_paired 의 G4_no_auto BLOCK
        ctx["actor_source"] = "strict_block"
    else:
        ctx["actor_source"] = "unverified_noninteractive"
        print("WARN: 사람 확인 미검증(비대화형·게이트 기록 없음) — actor_source=unverified_noninteractive "
              "로 진행합니다. 코드 강제 차단은 BINGGU_STRICT_HUMAN_GATE=1.")
    return ctx


def cmd_save(a):
    # 화자 페어 가드: owner 발화를 평면 save 로 저장하면 ai 발화와의 페어 연결(엣지)이 빠져
    # 노드가 흩어진다(화자축 본질 상실). owner 화자 저장은 pair 로만 — pair 가 페어/단독 둘 다 커버.
    # (capture 자동수집과 무관 · explicit 우회로 정합 · false positive 0: 단독 직감도 pair 로 가능)
    if getattr(a, "speaker", None) == "owner":
        print(
            "BLOCK: owner_flat_save_forbidden — owner 화자 저장은 pair 를 쓰세요(노드2+엣지1 연결 보존).\n"
            "  · AI 발화에 대한 반응(수용/반박/수정):\n"
            "      python binggu.py pair \"<owner 원문 그대로>\" \"<ai 발화>\" "
            "--by owner --relation {accepts|refutes|revises} --confirm \"PAIR owner_<relation> owner:1 ai:1\"\n"
            "  · 순수 단독 직감(ai 없음):\n"
            "      python binggu.py pair \"<owner 원문 그대로>\" --confirm \"PAIR owner:1\"\n"
            "  · save --speaker owner 는 화자 페어 엣지가 빠져 노드가 흩어집니다(2026-06-28 재발 차단)."
        )
        return 1
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
    _explicit = getattr(a, "explicit", False)
    # 저장될 문장(선택 후보) = 위조방지 게이트 대조 대상. save_selected 내부 재실행과 동일 인덱싱.
    try:
        _cands = capture_preview(a.text, explicit=_explicit)["candidates"]
        _sents = [_cands[i - 1]["sentence"] for i in idx
                  if isinstance(i, int) and 1 <= i <= len(_cands)]
    except Exception:
        _sents = []
    ctx = _resolve_human_ctx(a.ledger, _sents, a.confirm)
    r = save_selected(db, a.text, idx, ctx,
                      snap_dir, due_date=a.due, speaker=getattr(a, "speaker", None),
                      explicit=_explicit)
    # --accept: 저장과 동시에 owner_accepted 확정 — 별도 ACCEPT 문구 면제(SAVE confirm 이 이미 사람 확인).
    # 인정=SAVE 통합(2트랙 설계) — candidate→accept 한 명령. pair --accept 동형(accept_by_node_id).
    if r.get("applied") and getattr(a, "accept", False):
        accepted = 0
        for nid in r.get("node_ids", []):
            ar = accept_by_node_id(db, nid, "save --accept 통합 확정(SAVE confirm 편승)",
                                   {"actor": "human"})
            if ar.get("applied"):
                accepted += 1
        r["accepted"] = accepted
    db.close()
    return _show(r)


def cmd_pair(a):
    """owner 발화 + ai 요약을 각각 독립 노드(speaker=owner/ai)로 저장하고 연결 엣지로 묶는다.
    ai_text 생략 = owner 단독(순수 직감·억지 ai 금지). relation: accepts/refutes/revises."""
    from binggupack.storage import save_paired   # 트랙 C: storage facade 경유
    db, snap_dir = _open(a.ledger)
    rel = getattr(a, "by", "ai") + "_" + a.relation  # 반응 주체: ai(AI가 사용자 발화를) / owner(사용자가 AI 발화를)
    # 저장될 문장(owner/ai pick) = 위조방지 게이트 대조 대상(_pick_one_node 와 동일 explicit 인덱싱).
    _psents = []
    try:
        _oc = capture_preview(a.owner_text, explicit=True)["candidates"]
        if isinstance(a.owner_pick, int) and 1 <= a.owner_pick <= len(_oc):
            _psents.append(_oc[a.owner_pick - 1]["sentence"])
        if a.ai_text:
            _ac = capture_preview(a.ai_text, explicit=True)["candidates"]
            if isinstance(a.ai_pick, int) and 1 <= a.ai_pick <= len(_ac):
                _psents.append(_ac[a.ai_pick - 1]["sentence"])
    except Exception:
        _psents = []
    ctx = _resolve_human_ctx(a.ledger, _psents, a.confirm)
    r = save_paired(db, a.owner_text, a.ai_text, ctx,
                    snap_dir, relation_kind=rel, owner_pick=a.owner_pick, ai_pick=a.ai_pick, due_date=a.due)
    acc_note = ""
    if r.get("applied") and getattr(a, "accept", False):
        # 저장과 동시에 owner_accepted 확정 — 별도 ACCEPT 문구 면제(PAIR confirm 이 이미 사람 확인)
        ar = accept_by_node_id(db, r["owner_node_id"],
                               "pair --accept 통합 확정(PAIR confirm 편승)", {"actor": "human"})
        acc_note = " · 확정 OK" if ar.get("applied") else (" · 확정 실패(%s)" % ar.get("reason"))
    db.close()
    if r.get("applied"):
        tail = (" (%s 연결)" % r["relation"]) if r.get("paired") else " (owner 단독)"
        print("OK: 저장 %d건%s · pack=%s%s" % (r["saved"], tail, r.get("pack_id"), acc_note))
        return 0
    return _show(r)


def cmd_trust(a):
    """양방향 신뢰도 표시(read-only) — 내 직감 적중률 + AI 반박·수용 적중률.
    참고 가중치이지 맹종 스위치가 아니다(헌법). 최종 판단은 사람+근거."""
    import binggu_hit_stats as HS
    db, _ = _open(a.ledger)
    bs = HS.both_sides(db, subtype=a.subtype)
    db.close()
    print("# 양방향 신뢰도%s (참고 가중치 · 맹종 아님 · 최종 판단은 사람+근거)"
          % ((" [%s]" % a.subtype) if a.subtype else ""))
    for side, label in (("owner", "내 직감(owner)"), ("ai", "AI 반박·수용(ai)")):
        s = bs[side]
        if s["enough"]:
            print("  %s: 적중률 %.0f%% (표본 %d · 시간감쇠 반영)" % (label, s["rate"] * 100, s["n"]))
        else:
            print("  %s: 표본 부족 (%d/%d) — 신뢰도 미산정" % (label, s["n"], HS.N_MIN))
    return 0


def cmd_route(a):
    """저장 의도 라우팅 — 발화를 신규/수정/결과로 추정해 해당 명령을 안내(read-only).
    추정일 뿐 실행·번호선택·confirm 합성은 하지 않는다(owner 손에 잔류·게이트 무수정).
    자기수정(replace)·결과확정(resolve)을 '저장해' 흐름에 자연스럽게 잇는 안내 계층."""
    import re as _re
    text = a.text or ""
    revise = _re.compile(r"틀렸|틀린|바꿔|바꾸|수정|아니야|아니라|다시|고쳐|잘못|정정")
    result = _re.compile(r"결과|낙찰|유찰|성공했|실패했|맞았|판명|드러났|됐다|밝혀")
    if revise.search(text):
        kind = "수정(자기수정)"
        guide = ['기존 판단을 고치려면:',
                 '  binggu list                                   # 번호·id8 확인',
                 '  binggu replace <n> <id8> --with "<수정문장>" --reason "..." --confirm "REPLACE <n> <id8> WITH <수정문장>"']
    elif result.search(text):
        kind = "결과확정(예측→실측)"
        guide = ['예측 결과를 기록하면 양방향 신뢰도에 누적됩니다:',
                 '  binggu list                                   # 번호·id8 확인',
                 '  binggu resolve <n> <id8> --outcome 성공/실패 --reason "..."',
                 '  binggu trust                                  # 누적된 적중률 보기']
    else:
        kind = "신규 저장"
        guide = ['새로 남기려면:',
                 '  binggu preview "<텍스트>"  →  binggu save ... (단일)',
                 '  binggu pair "<내 발화/직감>" "<AI 요약>" --relation accepts/refutes/revises --confirm "PAIR ..."  (페어)',
                 '  binggu pair "<내 직감만>" --confirm "PAIR owner:1"  (순수 직감 단독)']
    print("# 저장 라우팅 — 추정: %s  (추정일 뿐, 최종 선택은 직접)" % kind)
    for g in guide:
        print(g)
    print("(의도가 다르면 위 안내를 무시하고 맞는 명령을 직접 쓰세요 — 이 명령은 아무것도 실행하지 않습니다.)")
    return 0


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
    # 양방향 신뢰도 연동 — 성공/실패만 hit_events 기록(불확실/판정불가 skip). 사람 resolve 한정(불변식6).
    if r.get("applied") and a.outcome in ("성공", "실패"):
        import binggu_hit_stats as _HS
        _HS.record_resolution(db, nid, a.outcome == "성공", {"actor": "human"})
    db.close()
    return _show(r)


def cmd_abstraction(a):
    """반복 판단 + hit_events 에서 규칙 후보(추상화)를 '제안만' 조회 — read-only·자동확정 0.

    규칙화(active 승격)는 절대 하지 않는다 — 제안 문구만 표시. 규칙으로 만들려면 사람이 SAVE 로
    명시 승인(candidate confirm 경로). DB write 0 · self-modifying 0."""
    from binggupack.pack import abstraction as ABS
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print("장부가 없습니다: %s · 먼저 python binggu.py init" % ledger)
        return 2
    proposals = ABS.propose_abstractions(ledger, domain=getattr(a, "domain", None),
                                         home=os.path.dirname(ledger))
    print(ABS.render_proposals_md(proposals))
    return 0


def cmd_mark(a):
    """회상(recall/why) 조언의 적중/빗나감 기록 — mark-hit / mark-miss.

    node_id 를 직접 받지 않고 (query, index)로 받아 why_search 를 재실행해 서버가 노드를 확보한다
    (D-1 위조 차단). nonce 로 회상 스냅샷을 고정하고(stale 차단), decision_id 를 (node_id,nonce)
    안정 해시로 만들어 반복 mark 를 dup_decision 으로 막는다(D-2 이중계상 차단). 사람 확정만(actor=human)."""
    from binggupack.pack import hit_recording as HR
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print("장부가 없습니다(회상 기억 없음): %s · 먼저 python binggu.py init" % ledger)
        return 2
    outcome = "hit" if a.cmd == "mark-hit" else "miss"
    db, _ = _open(ledger)
    r = HR.mark_outcome(db, ledger, a.query, a.index, outcome, {"actor": "human"},
                        nonce=a.nonce, domain=a.domain, home=os.path.dirname(ledger))
    db.close()
    if r.get("recorded"):
        print("OK: %s 기록 — [%d] \"%s\"" % (outcome, a.index, r.get("node_claim") or ""))
        print("    decision=%s · domain=%s · 사람 확정(자동 0)"
              % (r.get("decision_id"), r.get("domain")))
        return 0
    reason = r.get("reason")
    print("BLOCK: %s" % reason)
    if reason == "stale_recall":
        print("  회상 결과가 바뀌었습니다. python binggu.py recall \"%s\" 로 nonce 를 다시 받으세요"
              " (기대 nonce=%s)." % (a.query, r.get("expected_nonce")))
    elif reason in ("index_out_of_range",):
        print("  index 가 회상 건수를 벗어났습니다(회상 %s건). recall 로 번호를 확인하세요."
              % r.get("recall_count", "?"))
    elif reason == "no_recall":
        print("  이 query 로 회상되는 판단이 없습니다. recall 로 먼저 확인하세요.")
    elif reason == "dup_decision":
        print("  이미 같은 회상에서 기록됨(이중계상 방지). 중복 아님.")
    return 1


def cmd_reminders(a):
    db, _ = _open(a.ledger)
    today = a.today or datetime.date.today().isoformat()
    r = list_due_reminders(db, today)
    db.close()
    print(r["markdown"])
    return 0


def selftest():
    """임베드 selftest 는 tests/test_binggu_cli_selftest.py 로 분리(God-file #4).

    CLI 동작 불변 — 분리한 케이스(GATE=GO)를 그대로 import 해서 실행한다.
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from tests.test_binggu_cli_selftest import selftest as _impl
    return _impl()


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
        disabled = os.path.exists(HV.harvest_disabled_path(home))
        if disabled:
            print("[일시중지] harvest_disabled 플래그 ON — 수확은 0(긴급 정지 중).")
            print("  재개:  Remove-Item \"%s\"" % HV.harvest_disabled_path(home))
        if not srcs:
            print("등록된 외부 소스가 없습니다(빈 화이트리스트). 수확은 0입니다.")
            print('  등록:  python binggu.py harvest add --kind arxiv --url "https://arxiv.org/..."')
            return 0
        print("등록된 외부 소스 %d개:" % len(srcs))
        for i, s in enumerate(srcs, 1):
            print("  %d. [%s] %s%s" % (i, s.get("kind"), s.get("url"),
                                       (" (keyword=%s)" % s["keyword"]) if s.get("keyword") else ""))
            print("       제거:  python binggu.py harvest remove %s" % s.get("source_id"))
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
        for sr in (res.get("sources") or []):
            print("  - %s: %s%s · 노드=%s"
                  % (sr.get("source_id"), sr.get("status"),
                     (" (%s)" % sr["reason"]) if sr.get("reason") else "", sr.get("n_nodes")))
        if res["reason"] == "NO_REGISTERED_SOURCES":
            print("  등록된 소스가 없습니다 — 먼저 harvest add 로 소스를 등록하세요.")
        elif res["reason"] == "HARVEST_DISABLED":
            print("  긴급 정지 중(harvest_disabled). 플래그를 지우면 재개됩니다.")
        if res.get("candidates"):
            print("  후보만 적재됨(candidate=1 · 영구화 0). 영구는 preview→SAVE n 게이트로만.")
        return 0 if res["status"] in ("OK", "NOOP") else 1
    return 1


def cmd_confirm_edges(a):
    """confirm-edges — 관계 후보(graph_preview) → 사람 도장 → sync_edges 적재(일반 사용자 경로).

    흐름: 운영 ledger(read-only) 스냅샷 → graph_preview(2층 후보) → graph_confirm(--approve/--reject)
          → apply_confirm_to_sync(actor='human' 도장 → sync_edges 'confirmed').
    영구(운영 edges) 등재는 owner-only 별도 단계(hag_sync_adapter --import-edges)로 안내만 한다.

    헌법 준수:
      - 신규 EDGE SAVE 경로 신설 0 — graph_preview/graph_confirm/hag_sync_adapter 전부 재사용.
      - node→node 강한관계(supports_judgment) 자동생성 0 — --approve 로 사람이 고른 idx 만, actor='human'.
      - --confirm 게이트 필수(raw 실행 차단) · 운영 ledger write 0(read-only 스냅샷만).
      - apply 가 매트릭스/evidence/secret/dangling 재검증 + 멱등.

      python binggu.py confirm-edges                                   # 후보 목록(report only · 적재 0)
      python binggu.py confirm-edges --approve 1,3 --confirm "CONFIRM EDGES 1,3"
    """
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print("장부가 없습니다: %s\n먼저 만드세요:  python binggu.py init" % ledger)
        return 2
    # 운영 ledger 는 점검(read-only)만 — write 0. OPERATING_PATHS 신원(owner ledger 원본) 확인.
    # confirm-edges 는 운영 ledger 를 read-only 스냅샷만 한다(write 0) — 경로 무관 안전.
    # (옛 OPERATING_PATHS 분기는 pass 뿐인 죽은 가드라 제거 — 오해 소지.)
    sys.path.insert(0, os.path.join(BASE, "scripts", "hybrid_agi"))
    import hag_sync_adapter as SA
    import binggu_graph_preview as GP
    import binggu_graph_confirm as GC

    # 1) 운영 노드 read-only 스냅샷 → graph_preview 입력(EN2KO label_kind 변환·자기증빙 evidence)
    snap = SA.snapshot_operating_nodes(ledger)
    cands = SA.to_candidates(snap)
    nodes_in = [{"id": c["id"],
                 "properties": {"label_kind": c["label_kind"], "sentence": c["text"]},
                 "evidence_refs": c.get("evidence_refs") or []} for c in cands]
    preview = GP.build_graph_preview(nodes_in)
    confirm = GC.build_graph_confirm(
        preview,
        approve=[int(x) for x in a.approve.split(",") if x.strip()] if a.approve else None,
        reject=[int(x) for x in a.reject.split(",") if x.strip()] if a.reject else None)

    # 후보 목록 항상 표시(사람이 보고 고르는 행위) — report only.
    edges = preview.get("edges", [])
    sent = {n["id"]: n.get("text", "") for n in preview.get("nodes", [])}
    print("# 관계 후보 (supports_judgment · candidate · 적재 0 — 사람 도장 전)")
    if not edges:
        print("관계 후보 0건 — 운영 노드(증거/상태/개념 → 판단)가 쌓여야 후보가 생깁니다.")
        print("  먼저 preview→SAVE n 으로 노드를 도장(확정)하세요.")
        return 0
    for i, e in enumerate(edges, 1):
        print("  [%d] %s\n        --(%s)--> %s"
              % (i, sent.get(e["source_id"], e["source_id"])[:50],
                 e["relation"], sent.get(e["target_id"], e["target_id"])[:50]))
    print("\n승인 대기: %d · approved: %d · rejected: %d"
          % (confirm["summary"]["deferred"], confirm["summary"]["approved"],
             confirm["summary"]["rejected"]))

    # 2) --approve 없으면 안내만(적재 0)
    if not a.approve:
        print("\n관계를 도장하려면(사람이 고른 것만):")
        print('  python binggu.py confirm-edges --approve <번호들> --confirm "CONFIRM EDGES <번호들>"')
        return 0

    # 3) --confirm 게이트(raw 실행 차단) — "CONFIRM EDGES <approve>" 정확히 타이핑
    expected = "CONFIRM EDGES %s" % a.approve
    if a.confirm != expected:
        print('\nBLOCK: confirm_required_mismatch — 정확히 입력하세요:  --confirm "%s"' % expected)
        return 1

    # 4) 사람 도장 → sync_edges 'confirmed' 적재(actor='human'·운영 write 0)
    # full sentence(untruncated) — preview node text 는 [:60] 잘려 secret 스캔이 60자 이후 PII 를
    # 놓치므로, 원본 노드 문장(nodes_in)을 _has_secret 대상으로 넘긴다(truncation 우회 차단).
    _full_sent = {nd["id"]: nd["properties"]["sentence"] for nd in nodes_in}
    nbi = {n["id"]: {"id": n["id"],
                     "properties": {"label_kind": n.get("label_kind"), "candidate": True},
                     "sentence": _full_sent.get(n["id"], n.get("text", ""))}
           for n in preview.get("nodes", [])}
    sync_db = os.path.join(os.path.dirname(ledger), "sync_edges.sqlite")
    try:
        r = GC.apply_confirm_to_sync(confirm["approved"], sync_db, nodes_by_id=nbi,
                                     actor="human", now=int(__import__("time").time()))
    except SA.SyncError as e:
        print("BLOCK:", e)
        return 2
    print("\nOK: 사람 도장 %d건 → sync_edges 적재(%s)" % (r["applied"], sync_db))
    if r["rejected"]:
        print("  적재 제외: %s" % ", ".join("%s→%s(%s)" % (x["source_id"][:12], x["target_id"][:12], x["reason"])
                                          for x in r["rejected"]))
    if r["dangling"]:
        print("  dangling skip: %d건" % len(r["dangling"]))
    print("  %s" % r["caveat"])
    print("\n운영 ledger 등재(영구·owner-only·운영 write):")
    print('  python scripts/hybrid_agi/hag_sync_adapter.py --import-edges --actor human '
          '--ledger "%s" --sync-db "%s"' % (ledger, sync_db))
    return 0


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


def cmd_onboard(a):
    """onboard — 신규 사용자 원클릭 셋업: 읽기(setup-cloud) + 저장채널(save_mcp) + auto-pull.
    얇은 래퍼 — 실 오케스트레이션은 binggu_setup_cloud/binggu_setup_save(순수함수+selftest).
      python binggu.py onboard                  # 점검만(dry-run · 변경 0)
      python binggu.py onboard --apply          # 키 생성/kv/toml/스케줄러(배포 제외)
      python binggu.py onboard --apply --deploy # 위 + worker 2종 deploy(비가역)
    login(브라우저 OAuth)·deploy 결정·ChatGPT 커넥터 등록은 본인 손 — 대행 0."""
    import binggu_setup_cloud as SC
    import binggu_setup_save as SS
    apply_, deploy = bool(getattr(a, "apply", False)), bool(getattr(a, "deploy", False))
    res1 = SC.run_setup(apply=apply_, deploy=deploy)
    print(SC.render_report(res1))
    if res1["halted_at"] is not None:
        return 2
    res2 = SS.run_save_setup(apply=apply_, deploy=deploy,
                             show_url=bool(getattr(a, "show_url", False)),
                             webmcp=bool(getattr(a, "webmcp", False)))
    print(SS.render_report(res2))
    return 0 if res2["halted_at"] is None else 2


def cmd_restore(a):
    """restore — 백업 스냅샷으로 장부 교체(파괴적 · confirm 정확 일치 필수).
    confirm 없이 실행하면 검증 결과 + 기대 confirm 문구만 안내(write 0).
    교체 직전 현 장부를 _backup/pre_restore_<ts>.sqlite 로 자동 스냅샷(복구의 복구)."""
    from binggupack.workspace import archive as AR
    ledger, _ = _ledger_paths(a.ledger)
    res = AR.restore_ledger(a.backup, ledger, home=os.path.dirname(ledger),
                            confirm=getattr(a, "confirm", None))
    st = res["status"]
    if st == "NO_BACKUP":
        print("백업 파일이 없습니다: %s" % res.get("backup"))
        return 1
    if st == "INVALID_BACKUP":
        print("백업 파일이 유효한 장부가 아닙니다: %s (%s)" % (res.get("backup"), res.get("reason", "not_sqlite")))
        return 1
    if st in ("DRY_RUN", "CONFIRM_MISMATCH"):
        if st == "CONFIRM_MISMATCH":
            print("confirm 불일치 — 교체하지 않았습니다(write 0).")
        print("백업: 노드 %d · 엣지 %d  ↔  현재: 노드 %d · 엣지 %d"
              % (res["backup_nodes"], res["backup_edges"], res["current_nodes"], res["current_edges"]))
        print('교체하려면:  python binggu.py restore "%s" --confirm "%s"'
              % (res["backup"], res["expected_confirm"]))
        return 0 if st == "DRY_RUN" else 1
    if st == "BUSY":
        print("장부를 다른 프로세스가 사용 중이라 교체 실패(원본 무손상): %s" % res.get("error"))
        print("Claude 앱/Code·auto-pull 을 잠시 멈춘 뒤 재시도하세요.")
        return 1
    if st == "PRE_SNAPSHOT_FAIL":
        print("교체 직전 자동 스냅샷 실패 — 안전을 위해 교체하지 않았습니다.")
        return 1
    print("복원 완료: 노드 %d · 엣지 %d ← %s" % (res["nodes"], res["edges"], res["backup"]))
    print("직전 상태 스냅샷(되돌리기용): %s" % res.get("pre_snapshot"))
    return 0


def cmd_backup(a):
    """backup — 장부를 일관 스냅샷으로 복사(운영 write 0). 기본 <home>/_backup/ledger_<ts>.sqlite."""
    from binggupack.workspace import archive as AR
    ledger, _ = _ledger_paths(a.ledger)
    res = AR.backup_ledger(ledger, out_path=getattr(a, "out", None), home=os.path.dirname(ledger))
    if res["status"] == "NO_LEDGER":
        print("장부가 없습니다: %s · 먼저 python binggu.py init" % ledger)
        return 2
    print("# 백업 완료 → %s" % res["out_path"])
    print("  노드 %d · 엣지 %d · %d bytes · sha256 %s…"
          % (res["nodes"], res["edges"], res["size"], res["sha256"][:16]))
    return 0


def cmd_export(a):
    """export — 장부를 markdown/json 으로 내보낸다(데이터 주권 · read-only). --out 없으면 stdout."""
    from binggupack.workspace import archive as AR
    ledger, _ = _ledger_paths(a.ledger)
    res = AR.export_ledger(ledger, fmt=getattr(a, "fmt", "md"), out=getattr(a, "out", None))
    if res.get("out_path"):
        print("# 내보내기 완료(%s) → %s · 노드 %d · 엣지 %d"
              % (res["format"], res["out_path"], res["nodes"], res["edges"]))
    else:
        try:
            sys.stdout.write(res["text"])
        except BrokenPipeError:
            pass  # `| head` 등 파이프 조기 종료 — 정상(traceback 억제)
    return 0


def main():
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    p = argparse.ArgumentParser(prog="binggu", description="BingguPack 개인 장부 CLI")
    p.add_argument("--ledger", default=DEFAULT_LEDGER)
    sub = p.add_subparsers(dest="cmd", required=True)
    # 쉬운 별칭(UX): start=init · doctor=status · remember=preview · ask=recall.
    # 동작/안전 게이트는 본명령과 동일(별칭은 이름만 짧게). aliases= 와 dispatch dict 양쪽에 매핑.
    ip = sub.add_parser("init", aliases=["start"])
    ip.add_argument("--agi-memory", action="store_true", dest="agi_memory")  # 명시 별칭(동작 동일)
    ip.add_argument("--global", action="store_true", dest="global_scope")     # 전역 수집(미지정 시 현재 위치만)
    ip.add_argument("--no-capture", action="store_true", dest="no_capture")   # 장부만, capture profile 생략
    ip.add_argument("--force-capture", action="store_true", dest="force_capture")  # owner sticky OFF 해제하고 강제 ON
    sub.add_parser("status", aliases=["doctor"])
    sp = sub.add_parser("preview", aliases=["remember"]); sp.add_argument("text")
    rp = sub.add_parser("reflect")          # 회고·자가평가 → 지식 후보(반성이 지식으로 · 저장 0)
    rp.add_argument("text", nargs="?", default=None)
    rp.add_argument("--from-file", dest="from_file", default=None)  # 쌓인 회고 파일 일괄 후보화
    sp = sub.add_parser("save"); sp.add_argument("text", nargs="?", default=None)
    sp.add_argument("--from-file", dest="from_file", default=None)   # reflect --from-file 과 동일 text 로 저장(preview_id 일치)
    sp.add_argument("--preview-id", required=True, dest="preview_id")
    sp.add_argument("--pick", required=True); sp.add_argument("--confirm", required=True)
    sp.add_argument("--due", default=None)
    sp.add_argument("--speaker", choices=["owner", "ai"], default=None)  # 화자 칸(owner=사용자 발화/ai=AI 요약)
    sp.add_argument("--explicit", action="store_true")  # remember(명시 입력) preview 로 본 후보 저장 — 판단-veto 면제
    sp.add_argument("--accept", action="store_true")  # 저장과 동시에 owner_accepted 확정(별도 ACCEPT 문구 면제·2트랙 인정=SAVE 통합)
    sp = sub.add_parser("list"); sp.add_argument("--status", default=None)
    sp.add_argument("--kind", default=None)
    # 회상(L4~L6 · read-only) — recall(why_search) / trace(judgment_trace) / preflight
    rcp = sub.add_parser("recall", aliases=["ask"]); rcp.add_argument("query")
    rcp.add_argument("--limit", type=int, default=None)
    rcp.add_argument("--record", action="store_true", dest="record")  # use_count++ (기본 read-only)
    wp_ = sub.add_parser("why"); wp_.add_argument("query")                  # recall 별칭
    wp_.add_argument("--limit", type=int, default=None)
    wp_.add_argument("--record", action="store_true", dest="record")  # use_count++ (기본 read-only)
    # trace: 효용 trace(review/mark/enable/disable) + judgment_trace(show/<node_id> 하위호환)
    tp = sub.add_parser("trace")
    tp.add_argument("a1", nargs="?", default=None)   # review|mark|enable|disable|show|<node_id>
    tp.add_argument("a2", nargs="?", default=None)   # mark:N | show:node_id
    tp.add_argument("a3", nargs="?", default=None)   # mark:verdict
    tp.add_argument("--note", default=None)          # reason_code(화이트리스트)
    pfp = sub.add_parser("preflight")
    pfp.add_argument("--prompt", default=None)
    pfp.add_argument("--cwd", default=None)
    pfp.add_argument("--domain", default=None)
    pfp.add_argument("--files", default=None)  # 콤마 구분 변경 파일명
    pfp.add_argument("--record", action="store_true", dest="record")  # use_count++ (기본 read-only)
    pfp.add_argument("--settings", default=None)  # hook 등록 대상 settings.json (기본 OS별)
    pfp.add_argument("--install", action="store_true")    # 자동주입 hook 등록
    pfp.add_argument("--uninstall", action="store_true")  # 자동주입 hook 제거
    pfp.add_argument("--enable", action="store_true")     # 자동주입 ON(플래그)
    pfp.add_argument("--disable", action="store_true")    # 자동주입 OFF(플래그 삭제)
    pfp.add_argument("--auto-status", dest="auto_status", action="store_true")  # 등록/활성 상태
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
    # 회상 조언 적중 기록(작업A) — mark-hit / mark-miss (query+index, node_id 미노출·nonce 방어)
    for _mk in ("mark-hit", "mark-miss"):
        mkp = sub.add_parser(_mk); mkp.add_argument("query")
        mkp.add_argument("--index", type=int, default=1)   # recall 표시 번호(1-based)
        mkp.add_argument("--nonce", default=None)           # recall 이 발급한 회상 봉인(stale 방어)
        mkp.add_argument("--domain", default=None)          # 분모 분리 키(선택)
    # 추상화 규칙 후보 제안(작업4·C) — read-only·자동확정 0
    abp = sub.add_parser("abstraction"); abp.add_argument("--domain", default=None)
    pp = sub.add_parser("pair")              # owner 발화 + ai 요약 페어 저장(화자 축)
    pp.add_argument("owner_text"); pp.add_argument("ai_text", nargs="?", default=None)
    pp.add_argument("--relation", choices=["accepts", "refutes", "revises"], default="accepts")
    pp.add_argument("--by", choices=["ai", "owner"], default="ai")  # 반응 주체(누가 누구를 수용/반박/수정)
    pp.add_argument("--owner-pick", type=int, default=1, dest="owner_pick")
    pp.add_argument("--ai-pick", type=int, default=1, dest="ai_pick")
    pp.add_argument("--confirm", required=True); pp.add_argument("--due", default=None)
    pp.add_argument("--accept", action="store_true")  # 저장과 동시에 owner_accepted 확정(별도 ACCEPT 문구 면제)
    tp = sub.add_parser("trust"); tp.add_argument("--subtype", default=None)  # 양방향 신뢰도(read-only)
    rtp = sub.add_parser("route"); rtp.add_argument("text")  # 저장 의도 라우팅(신규/수정/결과 read-only 안내)
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
    cep = sub.add_parser("confirm-edges")   # 관계 후보→사람 도장→sync_edges(일반 사용자 경로)
    cep.add_argument("--approve", default=None)          # 도장할 관계 번호(1,3) — 미지정=후보 목록만
    cep.add_argument("--reject", default=None)
    cep.add_argument("--confirm", default=None)          # "CONFIRM EDGES <approve>" 게이트
    scp = sub.add_parser("setup-cloud")     # cloud 셋업 1개 진입점(멱등·실패정지·dry-run 기본)
    scp.add_argument("--apply", action="store_true")     # 실제 변경(미지정=점검만)
    scp.add_argument("--deploy", action="store_true")    # (--apply 와) wrangler deploy 까지 — 비가역
    obp = sub.add_parser("onboard")  # 신규 사용자 원클릭: setup-cloud + 저장채널 + auto-pull
    obp.add_argument("--apply", action="store_true")
    obp.add_argument("--deploy", action="store_true")
    obp.add_argument("--show-url", dest="show_url", action="store_true")  # 커넥터 전체 URL(본인 화면)
    obp.add_argument("--webmcp", action="store_true")   # 웹 MCP 자동가동 등록 옵트인(공개 터널=본인 결정)
    bkp = sub.add_parser("backup")   # 장부 백업(일관 스냅샷 복사 · 운영 write 0)
    bkp.add_argument("--out", default=None)
    exp = sub.add_parser("export")   # 장부 내보내기(md/json · 데이터 주권)
    exp.add_argument("--format", dest="fmt", choices=["md", "json"], default="md")
    exp.add_argument("--out", default=None)
    rsp = sub.add_parser("restore")  # 백업 → 장부 교체(파괴적 · confirm 정확 일치 게이트)
    rsp.add_argument("backup")                           # 백업 sqlite 경로
    rsp.add_argument("--confirm", default=None)          # "RESTORE <백업파일명>" 정확 일치
    a = p.parse_args()
    fn = {"init": cmd_init, "start": cmd_init, "status": cmd_status, "doctor": cmd_status,
          "preview": cmd_preview, "remember": lambda a: cmd_preview(a, explicit=True),  # remember=명시 입력
          "reflect": cmd_reflect, "save": cmd_save,
          "list": cmd_list, "deprecate": cmd_deprecate, "replace": cmd_replace,
          "accept": cmd_accept, "unaccept": cmd_unaccept, "due": cmd_due,
          "resolve": cmd_resolve, "mark-hit": cmd_mark, "mark-miss": cmd_mark,
          "abstraction": cmd_abstraction,
          "reminders": cmd_reminders, "capture": cmd_capture,
          "recall": cmd_recall, "why": cmd_recall, "ask": cmd_recall, "trace": cmd_trace, "preflight": cmd_preflight,
          "hosted": cmd_hosted, "harvest": cmd_harvest, "setup-cloud": cmd_setup_cloud,
          "onboard": cmd_onboard,
          "confirm-edges": cmd_confirm_edges, "pair": cmd_pair, "trust": cmd_trust,
          "route": cmd_route, "backup": cmd_backup, "export": cmd_export,
          "restore": cmd_restore}[a.cmd]
    sys.exit(fn(a))


if __name__ == "__main__":
    main()
