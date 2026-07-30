#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu — BingguPack 개인 장부 CLI (v1.0 사용자 진입점).

설치 후 "내 영속 장부"를 만들고 후보 관리 전 과정을 실행한다.
새 게이트 로직 0 — 기존 검증 모듈(save/list/deprecate/replace/accept/resolve)을 그대로 호출.
모든 변경은 confirm 문구를 사용자가 직접 타이핑해야 통과한다(자동 0·confirmed 0·raw 원문 0).

  binggu init                            내 장부 생성 (~/.binggupack/ledger.sqlite)
  binggu status                          장부 요약
  binggu preview "<대화/메모 텍스트>"      저장 후보 미리보기 (저장 0)
  binggu save "<텍스트>" --preview-id <preview가 표시한 id> \
                   --pick 1,3 --confirm "SAVE 1,3" [--due 2026-07-01]
                   (preview 없이 raw text 직행 저장은 BLOCK — 사람이 보고 고른 것만 저장)
  binggu list [--status pending|deprecated|resolved] [--kind 판단|상태|개념|문서|증거]
  binggu deprecate <n> <id8> --reason "..." --confirm "DEPRECATE <n> <id8>"
  binggu replace <n> <id8> --with "<수정문장>" --reason "..." \
                   --confirm "REPLACE <n> <id8> WITH <수정문장>"
  binggu accept <n> <id8> --reason "..." --confirm "ACCEPT <n> <id8>"
  binggu unaccept <n> <id8> --reason "..." --confirm "UNACCEPT <n> <id8>"
  binggu due <n> <id8> --date 2026-07-01      판단 검증 예정일 등록
  binggu resolve <n> <id8> --outcome 성공|실패|불확실|판정불가 --reason "..."
  binggu reminders [--today YYYY-MM-DD]       due 경과 판단 목록
  binggu --selftest                            temp 장부 풀 사이클 자가검증

장부 위치 변경: --ledger <sqlite 경로> (기본 ~/.binggupack/ledger.sqlite)
"""
import argparse
import datetime
import os
import sqlite3
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
    register_hook, unregister_hook, hook_registered, hook_health)

SAVE_GATE_MARKER = "binggu_save_gate_hook"  # 사람-발화 저장 게이트 hook 식별 토큰
PREFLIGHT_MARKER = "binggu_preflight_hook"  # preflight 자동주입 hook 식별 토큰
from binggu_capture_persist import PersistentCaptureBuffer  # noqa: E402
from binggu_capture_to_save import build_save_commands  # noqa: E402
import binggu_platform as _plat  # noqa: E402

# cross-platform: BINGGU_HOME 우선(opt-in) · 없으면 OS별 홈/.binggupack (Windows 동작 보존).
DEFAULT_LEDGER = _plat.default_ledger()
DEFAULT_SETTINGS = _plat.default_settings()
OUTCOMES = ("성공", "실패", "불확실", "판정불가")

# 사용자 안내에 쓰는 CLI 실행 접두(설치본="binggu" / 소스="python binggu.py"). 모듈 로드 시 1회 계산.
# 헬퍼 import/판정 실패해도 CLI 가 죽지 않게 폴백(안전 우선).
try:
    HINT = _plat.invocation_prefix()
except Exception:
    HINT = "python binggu.py"


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
        print(f"먼저 만드세요:  {HINT} init")
        sys.exit(2)
    os.makedirs(snap_dir, exist_ok=True)
    # 손상 장부 내성: sqlite 로 못 여는 장부는 traceback 대신 restore 안내(exit 2 = 장부없음과 일관).
    try:
        return open_accept(ledger), snap_dir
    except sqlite3.DatabaseError:
        print("장부 손상 감지: %s (sqlite 로 열 수 없습니다)" % ledger)
        bdir = os.path.join(os.path.dirname(ledger), "_backup")
        backs = []
        if os.path.isdir(bdir):
            backs = sorted((f for f in os.listdir(bdir) if f.endswith(".sqlite")),
                           key=lambda f: os.path.getmtime(os.path.join(bdir, f)),
                           reverse=True)
        if backs:
            print("백업으로 복구하세요 (최신순 · %s):" % bdir)
            for f in backs[:5]:
                print(f'  {HINT} restore "%s"' % os.path.join(bdir, f))
            print("  (confirm 없이 실행하면 dry-run 검증 + 교체용 --confirm 문구를 안내합니다)")
        else:
            print(f"백업 폴더가 비어 있습니다: %s — ledger_*.sqlite 백업을 찾아 {HINT} restore <백업> 하세요." % bdir)
        sys.exit(2)


def _node_id_of(db, index, status="all", kind=None):
    rows = list_candidates(db, status, kind)["rows"]
    if index < 1 or index > len(rows):
        return None
    return rows[index - 1]["node_id"]


def _show(r):
    if r.get("applied"):
        print("OK:", {k: v for k, v in r.items() if k != "applied"} or "적용됨")
        return 0
    # 실패 이유 전체 노출(사일런트 실패 금지) — BLOCK reason + approval 요청/안내 + 거부 코드 + 기존재 skip.
    print("BLOCK:", r.get("reason"))
    if r.get("request_id"):                                   # 비대화형 owner approval 요청 ID(Fable5 R4-1)
        print("  요청ID:", r["request_id"])
    if r.get("guidance"):                                     # owner 승인 안내(binggu approval approve …)
        print("  " + r["guidance"])
    rej = r.get("rejected")
    if rej:
        print("  거부:", ", ".join("%s=%d" % (k, v) for k, v in sorted(rej.items())))
    if r.get("skipped_existing"):
        print("  기존재 skip: %d건" % r["skipped_existing"])
    return 1


def _install_capture_profile(a, ledger, *, force=False):
    """capture profile 설치(hook 등록 + scope 생성) — init_profile 재사용 단일 경로.

    force=False(기본) = owner sticky OFF 존중: 사장님이 영구 OFF 해둔 자동수집을 무단
    재활성하지 않는다(§C-12). scope/hook 만 갱신하고 ON 은 하지 않음. force=True 일 때만
    sticky OFF 를 해제하고 강제 ON. start(기본)/capture install 양쪽이 이 함수로 위임한다.
    """
    home = os.path.dirname(os.path.abspath(ledger))
    settings = (getattr(a, "capture_settings", None) or getattr(a, "settings", None)
                or DEFAULT_SETTINGS)
    cwd = getattr(a, "capture_cwd", None) or os.getcwd()
    # AGI memory mode = 전역 후보수집(--agi-memory 또는 --global). 플래그 없으면 현재 위치만(privacy).
    global_scope = bool(getattr(a, "global_scope", False) or getattr(a, "agi_memory", False))
    r = init_profile(home, cwd, hook_command=_hook_command(),
                     settings_path=settings, global_scope=global_scope, force_enable=force)
    scope_desc = "전역(AGI memory — 모든 작업 세션)" if r["global"] else ("현재 위치만 %s (privacy)" % cwd)
    if not r["enabled"]:
        # owner sticky OFF — install 이 정책(자동수집 영구 OFF)을 깨지 않음.
        print("capture 는 owner 가 OFF 로 고정해 두어 켜지 않았습니다(scope/hook 만 갱신).")
        print(f"정말 켜려면:  {HINT} capture enable   (또는 capture install --force)")
    else:
        print("AGI memory capture ON — scope: %s" % scope_desc)
        # "repaired:<ev>" = 죽은 hook 경로(파일 소실)를 산 경로로 자동 교체한 이벤트 — 신규 등록과 분리 표시.
        new_ev = [e for e in r["hook_events"] if not e.startswith("repaired:")]
        rep_ev = [e.split(":", 1)[1] for e in r["hook_events"] if e.startswith("repaired:")]
        if new_ev:
            print("hook 등록(settings.json 백업됨): %s" % ", ".join(new_ev))
        if rep_ev:
            print("죽은 hook 경로 수리됨: %s (settings.json 백업됨)" % ", ".join(rep_ev))
        if not r["hook_events"]:
            print("hook 이미 등록됨 — 그대로 사용")
        print("자동 후보 수집만 켜집니다. 저장은 preview 후 SAVE n 게이트로만(자동 저장 없음).")
    print("상태:  capture status   ·   잠깐 끄기:  capture pause   ·   영구 끄기:  capture disable   ·   제거:  capture uninstall")
    return r


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
    # 기본 start = 장부 생성만(settings.json hook 미접촉 — 부작용 분리). 자동 후보 수집은
    # 명시 옵트인(--with-capture 또는 별도 `capture install`)일 때만 hook 을 건드린다.
    # --no-capture 는 하위호환 no-op(이제 기본이 곧 no-capture).
    if getattr(a, "with_capture", False):
        _install_capture_profile(a, ledger, force=bool(getattr(a, "force_capture", False)))
    else:
        print(f"자동 후보 수집은 꺼져 있습니다. 켜려면:  {HINT} capture install")
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
    print(f"다음:  {HINT} preview \"오늘 정리하고 싶은 문장들\"")
    return 0


def cmd_capture(a):
    home = os.path.dirname(os.path.abspath(a.ledger))
    settings = getattr(a, "settings", None) or DEFAULT_SETTINGS
    cwd = getattr(a, "capture_cwd", None) or os.getcwd()
    sub = a.capture_cmd
    if sub == "install":
        # capture profile 설치(hook 등록 + scope 생성) — start(기본)에서 분리된 명시 옵트인.
        # owner sticky OFF 존중(무단 재활성 금지). --force 일 때만 강제 ON(force_capture).
        _install_capture_profile(a, a.ledger, force=bool(getattr(a, "force_capture", False)))
        return 0
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
        print(f"capture 일시중지(pause). 재개:  {HINT} capture resume")
        return 0
    if sub == "resume":
        cap_resume(home)
        print("capture 재개(resume).")
        return 0
    if sub == "disable":
        # owner sticky OFF: init 재실행에도 OFF 유지(정책 영구 OFF). pause 와 달리 영구.
        cap_disable(home)
        print("capture 영구 OFF 고정(owner 정책). `binggu init` 재실행해도 켜지지 않습니다.")
        print(f"다시 켜기:  {HINT} capture enable")
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
        print(f"제거:  {HINT} capture uninstall-gate")
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


def cmd_app(a):
    """app — Binggu Anywhere owner tooling (admin plane client).
      upload: validate + public-scan + snapshot a canonical pack directory, preview (dry-run
              default), then owner-TTY-confirmed upload to the Anywhere admin endpoint.
    Read-only MCP data plane never exposes upload — this is a separate owner action."""
    from binggupack.app import upload as _U
    sub = getattr(a, "app_cmd", None)
    if sub == "upload":
        try:
            _U.run(a.pack, a.endpoint, dry_run=not a.confirm)
            return 0
        except _U.UploadError as e:
            print("[app upload] BLOCK: %s" % str(e))
            return 2
    print("usage: binggu app upload --pack <dir> [--endpoint <url>] [--confirm]")
    return 2


def cmd_hosted(a):
    # 본체는 binggupack/cli/hosted.py 로 이관(금고 §3 — 이동만·게이트 로직/문구 불변). lazy import 순환차단.
    from binggupack.cli import hosted as _m
    return _m.cmd_hosted(a)


def _reindex_after_write(ledger_arg):
    """저장/교체/폐기 후 Local Fresh Index 증분 갱신(best-effort · 실패해도 명령 안 깨짐).

    색인은 순수 파생(ledger mode=ro read 만) — write 는 색인 sqlite 만. 실패는 침묵(회상은
    stale 색인도 graceful, 다음 `binggu index update`/자동 빌드로 자기치유)."""
    try:
        from binggupack.pack import fresh_index as FI
        ledger, _ = _ledger_paths(ledger_arg)
        home = os.path.dirname(ledger)
        FI.index_update(ledger, home=home)
    except Exception:
        pass
    # Deep 임베드 캐시 선워밍(감사 #6) — owner 가 semantic 회상을 켠 경우에만(이중 게이트는
    # precompute 내부 CS.enabled() + 여기 recall_config 스위치). 변경분만 배치 왕복·실패 침묵.
    try:
        from binggupack.pack import recall as RC
        from binggupack.safety.p1_config import recall_config as _rcfg
        if _rcfg(home).get("semantic_recall_enabled", False):
            RC.precompute_embeddings(ledger, home=home)
    except Exception:
        pass


def _recall_staging_for_ledger(ledger):
    """회수 도장 staging 을 ledger scope 에 고정(_gate_log_for_ledger 와 동일 원칙·split-brain 차단).
    운영에선 dirname(ledger)==home 이라 gate_log.last_recall_candidates_path() 와 동일 경로."""
    from binggupack.safety import gate_log as _gl
    return os.path.join(os.path.dirname(os.path.abspath(ledger)),
                        os.path.basename(_gl.last_recall_candidates_path()))


def _stage_recall(ledger, node_ids, query, domain, surface):
    """회수 staging 기록(도장 소비용 idx→node_id 포인터 · ledger write 0 · 실패 침묵 False).

    owner 채팅 1-발화("히트 N"/"미스 N")가 유일한 사람 도장이 되는 intel loop 의 대상 고정 —
    표시 순서(1-base) = staging idx. query 는 fresh_index._leak_safe(redact+독립검증) 정화 후
    영속(gate_log 호출측 계약: 평문 시크릿/PII 미영속). 정화로 query 가 비워지는 병리 케이스는
    --record 경로와 adoption_key 가 갈릴 수 있으나(staging 내부 멱등은 유지) 미영속 우선."""
    try:
        from binggupack.pack.fresh_index import _leak_safe
        from binggupack.safety import gate_log as GL
        safe_q, _ok = _leak_safe(str(query or "")[:1000])
        GL.write_last_recall(node_ids, query=safe_q, domain=domain or "", surface=surface,
                             path=_recall_staging_for_ledger(ledger))
        return True
    except Exception:
        return False


def _recall_deep(a):
    """Deep 회상(명시 --deep) — 원본 전체 탐색(기존 why_search full scan). read-only."""
    import binggu_recall as RC
    ledger, _ = _ledger_paths(a.ledger)
    res = RC.why_search(ledger, a.query, limit=getattr(a, "limit", None),
                        home=os.path.dirname(ledger))
    if not res["relevant_nodes"]:
        print("관련 기억이 없습니다(Deep 전체 탐색): \"%s\"" % a.query)
        return 0, []
    print("# 회상(Deep · 원본 전체 탐색) — \"%s\" 관련 기억 %d건 (랭킹순 · candidate · read-only)"
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
    return 0, [n["node_id"] for n in res["relevant_nodes"]]


def cmd_recall(a):
    """recall/why — query 관련 기억 회상(기본 Hot 색인 · read-only). --deep 시 원본 전체 탐색.

    Hot(기본): Local Fresh Index 색인만 읽어 상위 5개 반환(전체 ledger 스캔 0 · provider hang 0).
    Deep(--deep): 원본 전체 why_search(느리지만 넓음). Hot 이 부족해도 자동 Deep 승격 0 — 안내만.
    use_count++ 는 --record 명시 시에만(헌법 '자동 저장 0'). --record='유용했다' 사람 신호."""
    import binggu_p1_ranking as RANK
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print(f"장부가 없습니다(회상할 기억 없음): %s · 먼저 {HINT} init" % ledger)
        return 0  # 빈 그래프 graceful
    home = os.path.dirname(ledger)

    if getattr(a, "deep", False):
        rc, node_ids = _recall_deep(a)
    else:
        from binggupack.pack import fresh_index as FI
        # 최초(색인 없음) 또는 ledger 전환(--ledger 로 다른 장부) 시에만 빌드 →
        # 이후 같은 ledger recall 은 색인만 read(원본 스캔 0). 신선도는 save/replace/폐기 hook 담당.
        if FI.indexed_ledger_path(home) != os.path.abspath(ledger):
            FI.index_update(ledger, home=home)
        limit = getattr(a, "limit", None) or 5
        hr = FI.hot_recall(a.query, home=home, limit=limit,
                           project=getattr(a, "project", None))
        nodes = hr["relevant_nodes"]
        if not nodes:
            print("Hot 색인에 관련 기억이 없습니다: \"%s\"" % a.query)
            print(f"  → 더 넓게 찾기(원본 전체): {HINT} recall \"%s\" --deep" % a.query)
            return 0  # 자동 Deep 승격 금지 — 사람이 선택
        print("# 회상(Hot 색인 · 상위 %d) — \"%s\" (랭킹순 · candidate · read-only · 원본 스캔 0)"
              % (len(nodes), a.query))
        for i, n in enumerate(nodes, 1):
            sub = (" [%s]" % n["semantic_subtype"]) if n.get("semantic_subtype") else ""
            flags = ("📌" if n.get("pinned") else "") + ("✓" if n.get("owner_approved") else "")
            print("  %d. (%s rank=%.3f rel=%.2f trust=%.1f%s) %s"
                  % (i, (n.get("node_type") or "judgment") + sub, n["rank_score"],
                     n["relevance"], n.get("trust", 0.0), (" " + flags) if flags else "", n["claim"]))
        print("\n%s" % hr["summary"])
        node_ids = [n["node_id"] for n in nodes]

    # 회상 조언 적중 기록 안내(작업A) — nonce 로 위조(D-1)·이중계상(D-2) 방어. 사람 확정만.
    if node_ids:
        from binggupack.pack import hit_recording as _HR
        _nonce = _HR.recall_nonce(a.query, node_ids)
        print(f"  → 맞았으면:  {HINT} mark-hit \"%s\" --index N --nonce %s" % (a.query, _nonce))
        print("     틀렸으면 mark-miss (N=위 번호). 자동 기록 0 · 사람 확정만.")
        # 작업A2(intel loop): 도장 소비용 staging(idx=위 번호·raw node_id 화면 미노출) + 푸터 1줄.
        if _stage_recall(ledger, node_ids, a.query, getattr(a, "domain", None), "cli_recall"):
            print(f'  → 유용했으면 채팅 정확형 1줄 "히트 N"(아니면 "미스 N") 후: '
                  f'{HINT} mark-hit --from-recall --index N')
    # P1-② use_count++ — --record 명시 시에만(사람의 '유용했다' 신호). 기본 회상은 read-only.
    if getattr(a, "record", False) and node_ids:
        db, _ = _open(ledger)
        use_key = RANK.adoption_key(a.query, getattr(a, "domain", None))
        for nid in node_ids:
            RANK.record_use(db, nid, use_key=use_key)
        db.close()
        _reindex_after_write(a.ledger)   # use_count 변화 → fresh_index rank 반영
        print("\n(use_count 기록됨 · 유용성 신호 · 채택멱등[같은 회상 재기여 0] · 도장/문장 불변)")
    return 0


def cmd_index(a):
    """Local Fresh Index 관리 — status(상태) / update(증분) / rebuild(전체 재생성) / pin·unpin.

    색인은 원본(ledger)에서 파생된 read-only 캐시. 삭제해도 rebuild 로 복원. ledger write 0."""
    from binggupack.pack import fresh_index as FI
    ledger, _ = _ledger_paths(a.ledger)
    home = os.path.dirname(ledger)
    cmd = a.index_cmd
    if cmd == "status":
        st = FI.index_status(ledger, home=home)
        if getattr(a, "json", False):
            import json as _j
            print(_j.dumps(st, ensure_ascii=False))
            return 0
        if st["status"] == "MISSING":
            print(f"# Local Fresh Index — 없음. 생성: {HINT} index update")
            return 0
        stale = st["status"] == "STALE"
        print("# Local Fresh Index — %s" % ("갱신 필요(STALE)" if stale else "최신(OK)"))
        print("  경로: %s" % st["index_path"])
        print("  마지막 갱신: %s" % (st.get("last_update_ts") or "-"))
        print("  색인 항목: active %d · deprecated %d · pinned %d · 파일 %d"
              % (st["active"], st["deprecated"], st["pinned"], st.get("files", 0)))
        print("  ledger 노드: %d · 변경 대기: %d · 제거 대기: %d (확인 %sms)"
              % (st["ledger_nodes"], st["pending_changes"], st["pending_removals"], st["ms"]))
        _paths = FI.allowed_paths(home)
        print("  허용 로컬 경로: %s" % (", ".join(_paths) if _paths else "(없음 · index add-path 로 옵트인)"))
        if stale:
            print(f"  → 반영: {HINT} index update")
        return 0
    if cmd == "update":
        r = FI.index_update(ledger, home=home)
        f = r.get("files", {})
        print("OK: 색인 증분 갱신 — 노드 신규 %d·수정 %d·유지 %d·제거 %d·폐기 %d | 파일 신규 %d·수정 %d·제거 %d (%sms · ledger write 0)"
              % (r["added"], r["updated"], r["unchanged"], r["removed"], r["deprecated"],
                 f.get("added", 0), f.get("updated", 0), f.get("removed", 0), r["ms"]))
        return 0
    if cmd == "rebuild":
        r = FI.index_rebuild(ledger, home=home)
        print("OK: 색인 전체 재생성 — 항목 %d (%sms · 핀 보존 · 원본 불변)" % (r["scanned"], r["ms"]))
        return 0
    if cmd in ("pin", "unpin"):
        r = FI.set_pin(a.node_id, home=home, pinned=(cmd == "pin"))
        print("OK: %s %s (영구 규칙 %s · 색인 레벨 · ledger 불변)"
              % (cmd, r["node_id"], "고정" if cmd == "pin" else "해제"))
        return 0
    if cmd == "add-path":
        if not os.path.isdir(a.path):
            print("BLOCK: 디렉토리가 아닙니다: %s" % a.path)
            return 1
        paths = FI.add_allowed_path(a.path, home=home)
        print("OK: 허용 경로 추가 — %s (총 %d개 · 다음 index update 부터 md/traj 인덱싱)"
              % (os.path.abspath(a.path), len(paths)))
        return 0
    if cmd == "remove-path":
        paths = FI.remove_allowed_path(a.path, home=home)
        print("OK: 허용 경로 제거 — %s (남은 %d개 · 해당 파일 항목은 다음 update 에서 제거)"
              % (os.path.abspath(a.path), len(paths)))
        return 0
    if cmd == "list-paths":
        paths = FI.allowed_paths(home)
        if not paths:
            print(f"허용 로컬 경로 없음 (기본 빈 · owner 옵트인). 추가: {HINT} index add-path <dir>")
        else:
            print("# 허용 로컬 경로 (md/traj 인덱싱 대상)")
            for p in paths:
                print("  · %s" % p)
        return 0
    print("BLOCK: unknown index subcommand: %s" % cmd)
    return 1


def _reason_hint(verdict):
    import binggu_recall_trace as RT
    return ", ".join(RT.REASON_CODES.get(verdict, ())) or "(없음)"


def _judgment_trace_show(ledger, node_id):
    """기존 judgment_trace — 판단 노드 근거 사슬(다홉). read-only."""
    import binggu_recall as RC
    if not os.path.exists(ledger):
        print(f"장부가 없습니다: %s · 먼저 {HINT} init" % ledger)
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


def _ai_stamp_use_count(ledger, res, verdict, use_ai):
    """AI 자기신고 도장의 랭킹(use_count) 반영 + owner 덮어쓰기 시 회수.

    정본은 binggupack.pack.p1_ranking.ai_stamp_use_count 로 승격(2026-07-30) —
    MCP use-time 도장(trace_stamp)과 CLI 가 같은 반영/회수 대칭을 공유한다.
    반환 계약 불변: (use_count, action) — action ∈ {"record","revoke","error(...)",None}."""
    try:
        # ★ RANK 는 이 모듈의 전역이 아니다 — 반드시 지역 import(2026-07-27 NameError 12건 사고).
        import binggu_p1_ranking as RANK
        return RANK.ai_stamp_use_count(ledger, res, verdict, use_ai)
    except Exception as e:
        return None, "error(%s)" % type(e).__name__


def _trace_review(RT, ledger, home):
    """미판정 회상 목록 + 번호→(trace,node) 스냅샷 저장(원문 0). 효용 판정 대기."""
    pend = RT.list_pending(home=home, ledger_path=ledger)
    if not pend:
        print("미판정 회상이 없습니다.")
        print("(preflight 자동주입이 일어나고 opt-in 이 켜져 있어야 쌓입니다 — binggu trace enable)")
        return 0
    # scope="all" 명시 — 마무리 preview(scope="session")와 같은 파일을 쓰므로, 이 목록으로
    # 덮은 뒤 세션 번호로 도장하면 오도장이 난다. mark 가 expect_scope 로 대조해 막는다.
    RT.save_review_snapshot(pend, home=home, scope="all")
    print("# 미판정 회상 %d건 — 효용 판정 대기 (candidate · 사람 판정만)" % len(pend))
    for p in pend:
        cat = (" [%s]" % p["category"]) if p["category"] else ""
        rank = (" score=%.2f" % p["rank"]) if isinstance(p["rank"], (int, float)) else ""
        claim = p["claim"] or ("(원문 미상 · node_id %s)" % p["node_id"])
        print("  %d. %s%s%s" % (p["idx"], claim, cat, rank))
        print("     판정: binggu trace mark %d used|ignored|corrected [--note <code>]" % p["idx"])
    print("\nreason_code(--note 또는 --reason): ignored→%s · corrected→%s"
          % (_reason_hint("ignored"), _reason_hint("corrected")))
    return 0


def cmd_trace(a):
    """trace — 회상 효용 trace(review/mark/enable/disable) + 근거 사슬(show/<node_id>).

    binggu trace [review]          : 미판정 회상 목록(효용 판정 대기)
    binggu trace mark N <verdict>  : N 번 회상 판정 used|ignored|corrected (--note|--reason <reason_code>)
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
        # 배치 도장(간결 UX): "1,2,3" 콤마 여러 개 → 각각 판정(도움된 회상 한 줄 도장).
        touched_use = False
        # 단일 "N" 은 이전과 동일. owner '히트 H1,H2' 발화가 이 배치 경로로 소비된다.
        verdict = a.a3
        if verdict not in RT.VALID_VERDICTS:
            print("verdict 는 used|ignored|corrected (받음: %r)" % verdict)
            return 2
        try:
            ns = [int(x) for x in str(a.a2).split(",") if str(x).strip()]
        except (TypeError, ValueError):
            ns = []
        if not ns:
            print("사용법: binggu trace mark <N[,N...]> <used|ignored|corrected> "
                  "[--note|--reason <reason_code>]")
            return 2
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # --ai: AI 자기신고 도장(owner 지시 · 히트/미스 한정). 그 외는 기존 fail-closed 경로.
        use_ai = bool(getattr(a, "ai", False))
        ctx = ({"actor": RT.AI_STAMP_ACTOR} if use_ai else _resolve_human_ctx(a.ledger, None))
        actor_label = RT.AI_STAMP_ACTOR if use_ai else "human"
        hints = {"need_review": "먼저 binggu trace review 로 목록을 보세요.",
                 "bad_index": "그 번호의 회상이 없습니다(review 재실행).",
                 "dup_outcome": "이미 판정된 회상(첫 판정 보존).",
                 "invalid_reason_code": "note 는 정해진 코드만: %s" % _reason_hint(verdict),
                 "trace_not_found": "trace 를 찾을 수 없습니다.",
                 "stale_snapshot": "다른 목록이 스냅샷을 덮었습니다 — 대상 목록을 다시 띄우세요(오도장 차단).",
                 "G4_no_auto": "actor=human 만 판정 가능(헌법 · AI 는 --ai 로 ai_stamp 기록)."}
        ok_cnt = 0
        for n in ns:
            res = RT.mark_by_index(n, verdict, ctx, ts,
                                   reason_code=getattr(a, "note", None), home=home,
                                   expect_scope=getattr(a, "expect_scope", None),
                                   expect_session=getattr(a, "expect_session", None))
            if res["recorded"]:
                ok_cnt += 1
                note = (" · note=%s" % res["reason_code"]) if res.get("reason_code") else ""
                over = (" · %s 도장 덮어씀" % res["overwrote"]) if res.get("overwrote") else ""
                uc, act = _ai_stamp_use_count(ledger, res, verdict, use_ai)
                if act in ("record", "revoke"):
                    touched_use = True
                    rank = " · use_count=%s(%s)" % (
                        uc, "AI 반영" if act == "record" else "AI 몫 회수")
                elif act:
                    rank = " · ⚠ 랭킹 반영 실패(%s)" % act   # silent drop 금지(§13 B10)
                else:
                    rank = ""
                print("판정 기록: #%d → %s%s (actor=%s)%s%s"
                      % (n, verdict, note, actor_label, over, rank))
            else:
                print("판정 안 됨 #%d(%s): %s"
                      % (n, res["reason"], hints.get(res["reason"], res["reason"])))
        if len(ns) > 1:
            print("→ %d/%d 건 판정 기록(actor=%s)" % (ok_cnt, len(ns), actor_label))
        if touched_use:
            _reindex_after_write(ledger)   # use_count 변화 → fresh_index rank 반영(기존 규약)
        return 0
    if a1 in (None, "review"):
        return _trace_review(RT, ledger, home)
    # show <node_id> | <node:CONV:...> (judgment_trace · 하위호환)
    node_id = a.a2 if a1 == "show" else a1
    return _judgment_trace_show(ledger, node_id)


def cmd_preflight(a):
    # 본체는 binggupack/cli/preflight.py 로 이관(구조 정리·동작 불변). lazy import 로 순환 차단.
    from binggupack.cli import preflight as _m
    return _m.cmd_preflight(a)


def cmd_status(a):
    db, _ = _open(a.ledger)
    home = os.path.dirname(os.path.abspath(a.ledger))
    # active 후보 수 + 히트(use_count) 롤업을 한 SELECT 로 통합(nodes 재스캔 0 · MF2). use_count 컬럼이
    # 없는 구 ledger 는 PRAGMA 폴백(promote.py 패턴)으로 0 처리 — status 크래시 0.
    _has_uc = "use_count" in {r[1] for r in db.con.execute("PRAGMA table_info(nodes)")}
    _uc_sum = "COALESCE(SUM(use_count),0)" if _has_uc else "0"
    _uc_hit = "SUM(CASE WHEN use_count>0 THEN 1 ELSE 0 END)" if _has_uc else "0"
    n, hit_sum, hit_nodes = db.con.execute(
        "SELECT count(*), %s, %s FROM nodes WHERE state='active'" % (_uc_sum, _uc_hit)).fetchone()
    hit_sum = int(hit_sum or 0)
    hit_nodes = int(hit_nodes or 0)
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
    # capture hook 건강 진단 — 등록돼 있어도 대상 .py 소실이면 수집이 조용히 죽는 결함 표면화.
    hh = hook_health(DEFAULT_SETTINGS)
    if hh["dead_paths"]:
        print("⚠ capture hook 죽은 경로 %d건: %s" % (len(hh["dead_paths"]), ", ".join(hh["dead_paths"])))
        print(f"  수리: {HINT} capture install 실행하면 자동 교체됩니다.")
    elif hh["registered"]:
        print("capture hook: 정상 등록(경로 실존)")
    else:
        print(f"capture hook: 미등록 ({HINT} capture install 로 등록)")
    # ── 지능 루프 롤업(read-only 표시일 뿐 · 운영홈 write 0 · 자동저장 0 · 규칙 자동변경 0) ──
    # 두 축 정직 분리(MF3): (1) 히트 = use_count(수동 recall --record 신호) · (2) 사람판정 = 회상 효용(used).
    # golden_drift 는 재검토 '후보'만 표시 — 사람이 raw 확인 후 도장(빙구팩이 규칙을 자기가 바꾸지 않음).
    print("지능 루프(read-only · 표시일 뿐 · 규칙 자동변경 0):")
    try:
        # automation 스위치 실상태(발견성 — binggu status/doctor 별칭에서 바로 보이게). read-only.
        from binggupack.pack.doctor import _automation_flags
        _af = _automation_flags(home)
        print("  automation: capture=%s preflight=%s trace=%s crab_sync=%s (owner 만 켬 · doctor 거울)"
              % ("ON" if _af["capture"] else "OFF", "ON" if _af["preflight"] else "OFF",
                 "ON" if _af["recall_trace"] else "OFF", "ON" if _af["crab_sync"] else "OFF"))
    except Exception:
        pass  # MF5: automation 줄 실패해도 status 불사
    print("  히트(수동 recall --record 신호): use_count 합 %d · 히트 노드 %d개" % (hit_sum, hit_nodes))
    try:
        # Core read-only 헬퍼만 import(집계 자체가 mode=ro · store write 0). MF5: 실패해도 status 불사.
        from binggupack.pack import recall_trace as RT
        from binggupack.pack import outcome_attribution as OA
        if not RT.trace_enabled(home):
            # 잠김 가시화(MF3) — opt-in 게이트가 꺼져 있으면 사람판정 축은 애초에 축적 0.
            print("  사람판정(used): 회상 trace OFF(§25 owner 게이트 · 잠김) — 켜기: %s trace enable" % HINT)
        else:
            _ag = RT.aggregate(home)
            _ov = _ag["overall"]
            _oa = OA.aggregate_run_outcomes(home)["overall"]
            _tot = _ov["outcomes"]
            if _tot == 0:
                # N=0 = 고장 아님(켜졌지만 도장 대기). 히트→사람판정 전환 액션 힌트(MF3).
                print("  사람판정(used): 0건 (고장 아님 — 도장: %s recall --record · 판정: %s trace mark)"
                      % (HINT, HINT))
            else:
                _rate = _ov["usefulness_rate"]
                _rate_s = ("%.0f%%" % (_rate * 100)) if _rate is not None else "-"
                print("  사람판정(used): used %d/%d(%s) · ignored %d · corrected %d"
                      % (_ov["used"], _tot, _rate_s, _ov["ignored"], _ov["corrected"]))
            # 결과-귀속(별도 축 · signal_only) — RT 판정 유무와 무관하게 표시(신호 누락 0).
            print("  결과-귀속(signal_only · 인과 아님): trace %d · 적용 %d(성공 %d/실패 %d) · 미결 %d"
                  % (_oa["traces"], _oa["applied"], _oa["applied_success"],
                     _oa["applied_failure"], _oa["pending_traces"]))
            _drift = len(_ag["golden_drift_candidates"])
            if _drift:
                print("  재검토 후보 %d건(golden_drift · 사람 raw 확인 후 도장 · 자동변경 0): %s trace review"
                      % (_drift, HINT))
    except Exception:
        # MF5: RT/OA 미탑재·스토어 손상도 status 전체를 죽이지 않음(read-only 표시일 뿐).
        print("  사람판정(used): 집계 불가(회상 trace 모듈 미탑재) — %s trace enable 후 축적" % HINT)
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
    # explicit 모드도 함께 기록 — save/pair/core 재승격이 동일 모드로 pref 재계산(MUST_FIX 2).
    try:
        import binggu_save_gate as _sg
        _sg.write_last_preview(pv.get("candidates") or [], explicit=explicit)
    except Exception:
        pass
    pid = _preview_id(a.text)
    print("\npreview_id: %s" % pid)
    if pv["candidates"]:
        print("⚠ 외부 사실(릴리스 상태·업로드 여부·등급 등)은 실측 확인 전에 저장하지 마세요.")
        _x = " --explicit" if explicit else ""
        print(f"저장은 번호를 직접 골라서:  {HINT} save \"<같은 텍스트>\" --preview-id %s "
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
    # reflect 후보 재도출은 기본 모드 — explicit=False 기록(save pref 재계산 패리티 · MUST_FIX 2).
    try:
        import binggu_save_gate as _sg
        _sg.write_last_preview(pv.get("candidates") or [], explicit=False)
    except Exception:
        pass
    pid = _preview_id(text)
    print("\npreview_id: %s" % pid)
    if pv["candidates"]:
        print(f"이 회고에서 남길 교훈만 골라 도장:  {HINT} save \"<같은 텍스트>\" "
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


def _resolve_human_ctx(ledger, save_refs=None, confirm=None, stamp_ctx=None):
    """운영 write 의 'human' 승격 — 사람 증명 = "preview + 사람의 save n 입력" 단일 원칙.

    save_refs: list[(preview_ref: str, indices: list[int])] | None — save-n 참조 바인딩 대조 대상.
    confirm 은 검증 없는 passthrough(정확문구 검증은 core 몫). 판정 3분기(기본 reader · fail-closed):
      1) save-n 참조 바인딩 — 사람이 preview 를 보고 발화한 "세이브 n" 을 UserPromptSubmit hook 이
         (preview_ref, idx) 로 기록(AI 는 UserPromptSubmit 를 못 거쳐 위조 불가). save_refs 전 튜플이
         gate_human_for_ref 를 통과(all-or-nothing·신선도 창·미래ts 무효)할 때만 human/'save_gate_ref'.
      2) 에이전트 세션 가드 — CLAUDECODE env truthy → reader/'agent_session_unanchored'. Claude Code
         세션 안에서 명령 실행 주체는 AI 일 수 있으므로 훅 기록(1)만이 사람 증명. 이 env 는 승인을
         부여하지 않고 **거부만** 한다(deny 전용 — "env 는 승인 아님" 원칙과 정합·fail-open 없음).
         ★한계 정직 명시: CLAUDECODE 는 프로세스가 env unset 으로 지울 수 있는 소프트 신호다(위조
         =제거 가능) — 하드 통제가 아니라 행동규칙 + audit(actor_source 저널)이 잔여 방어이고,
         write 성사는 여전히 core 의 confirm 정확일치·preview 게이트를 통과해야 한다.
      3) 터미널 = 명령 직접 입력 — 그 외 → human/'cli_command'. 사용자가 명령(번호+confirm)을 직접
         타이핑한 것이 곧 save n 입력(스펙 ②). isatty 검사는 삭제 — pipe/redirect 여부는 사람 증명과
         무관하다.
    BINGGU_TRUSTED_CLI 는 무시(env fail-open 불가) · BINGGU_STRICT_HUMAN_GATE 는 **deprecated no-op**
    (strict 가 기본 · 0/false 로 fail-open 안 됨).

    (비-저장 mutation(accept/unaccept/due/resolve)의 exact-bound approval event 경로는 별도 자산 —
     approval_id 제시 시 cmd_* 가 _mutation_via_approval 로 분기하며 이 함수와 배타.)
    """
    ctx = {"actor": "reader"}                    # 기본 fail-closed (reader 는 denylist·allowlist 게이트 모두 BLOCK)
    if confirm is not None:
        ctx["confirm"] = confirm
    if os.environ.get("BINGGU_STRICT_HUMAN_GATE", "").strip():
        print("NOTE: BINGGU_STRICT_HUMAN_GATE 는 deprecated no-op 입니다(strict 가 기본 · fail-open 불가).")
    # 0) intel-loop 도장(recall/promote) — 호출측이 gate_human_for_recall/promote 로 사전 검증한
    #    사람 도장. save_gate_ref 와 동일 규약(재계산 ref 대조 통과)의 recall/promote 도메인 승격.
    #    subscript 할당(dict 리터럴 아님) — approval-origin 인벤토리 계약(binggu.py CLI 진입점
    #    {"actor":"human"} 리터럴 0)을 지키며 human 승격을 이 단일 해소 지점으로 집중한다.
    if stamp_ctx is not None:
        ctx["actor"] = "human"
        ctx["actor_source"] = stamp_ctx
        return ctx
    # 1) save-n 참조 바인딩 (hook 이 쓴 (preview_ref, idx) — AI 는 UserPromptSubmit 를 못 거쳐 위조 불가)
    try:
        import binggu_save_gate as _sg
        if save_refs and all(
                _sg.gate_human_for_ref(pref, idxs, path=_gate_log_for_ledger(ledger))
                for pref, idxs in save_refs):
            ctx["actor"] = "human"
            ctx["actor_source"] = "save_gate_ref"
            return ctx
    except Exception:
        pass  # 게이트 부재/오류 → 승격 0(fail-closed 유지)
    # 2) 에이전트 세션 가드 — Claude Code 세션 내부는 훅 앵커(1)만 사람 증명(deny 전용·승격 없음).
    if os.environ.get("CLAUDECODE"):
        ctx["actor_source"] = "agent_session_unanchored"
        return ctx
    # 3) 터미널 — 사용자가 명령을 직접 입력한 것이 곧 save n(스펙 ②·isatty 무관).
    ctx["actor"] = "human"
    ctx["actor_source"] = "cli_command"
    return ctx


def _mutation_via_approval(a, db, operation, bind, core_call):
    """비대화형 owner 의 exact-bound approval 경로(P1-B A2). approval_id 제시 시에만 이 경로.

    approval_gate.authorize 컨텍스트로 (protocol,operation,payload digest,ledger) 바인딩 검증 +
    one-time consume(reserve/finalize). provider 미구성/미승인/바인딩 불일치 → auth.actor='reader'
    → core 게이트 fail-closed(G4). save-n 바인딩/cli_command 경로(_resolve_human_ctx)와 배타.
    core_call(ctx) 은 ctx={"actor","confirm"} 를 받아 core mutation 을 실행하고 결과 dict 를 반환한다.
    """
    from binggupack.mcp import approval_gate
    # _approval_home 은 binggupack/cli/approval.py 로 co-move 됨 — 이 백본 호출부에 lazy 재수입(순환 차단).
    from binggupack.cli.approval import _approval_home
    home = _approval_home(a)
    os.makedirs(home, exist_ok=True)
    with approval_gate.authorize(operation, bind, home, db) as auth:
        r = core_call({"actor": auth.actor, "confirm": getattr(a, "confirm", None)})
        auth.settle(r)
    extra = auth.response_extra()
    if not r.get("applied"):
        r = dict(r)
        # approval 게이트 사유(approval_required/provider_not_configured/binding_mismatch)를 core G4 보다
        # 우선 표기(Fable5 R4-1: core 의 G4_no_auto 가 approval_required·owner 승인 여정을 가리던 문제).
        if extra.get("reason"):
            r["reason"] = extra["reason"]
        if extra.get("request_id"):
            r["request_id"] = extra["request_id"]   # owner 가 approve 할 요청 ID 노출
        if extra.get("guidance"):
            r["guidance"] = extra["guidance"]        # owner 안내(binggu approval show/approve <rid>)
    return r


def cmd_save(a):
    # 화자 페어 가드: owner 발화를 평면 save 로 저장하면 ai 발화와의 페어 연결(엣지)이 빠져
    # 노드가 흩어진다(화자축 본질 상실). owner 화자 저장은 pair 로만 — pair 가 페어/단독 둘 다 커버.
    # (capture 자동수집과 무관 · explicit 우회로 정합 · false positive 0: 단독 직감도 pair 로 가능)
    if getattr(a, "speaker", None) == "owner":
        print(
            "BLOCK: owner_flat_save_forbidden — owner 화자 저장은 pair 를 쓰세요(노드2+엣지1 연결 보존).\n"
            "  · AI 발화에 대한 반응(수용/반박/수정):\n"
            f"      {HINT} pair \"<owner 원문 그대로>\" \"<ai 발화>\" "
            "--by owner --relation {accepts|refutes|revises} --confirm \"PAIR owner_<relation> owner:1 ai:1\"\n"
            "  · 순수 단독 직감(ai 없음):\n"
            f"      {HINT} pair \"<owner 원문 그대로>\" --confirm \"PAIR owner:1\"\n"
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
    # save-n 참조 바인딩 — last_preview 에 기록된 explicit 모드로 후보를 재도출해 preview_ref 를
    # 재계산(pref 패리티 · MUST_FIX 2). hook 이 기록한 (pref, idx) 와 전 튜플 대조된다.
    _refs = None
    try:
        import binggu_save_gate as _sg
        import json as _json
        _mode = _explicit
        try:
            with open(_sg.last_preview_path(), "r", encoding="utf-8") as _f:
                _mode = bool(_json.load(_f).get("explicit", _mode))
        except Exception:
            pass
        _cands = capture_preview(a.text, explicit=_mode)["candidates"]
        _refs = [(_sg.preview_ref_for_candidates(_cands), idx)]
    except Exception:
        _refs = None
    ctx = _resolve_human_ctx(a.ledger, _refs, a.confirm)
    r = save_selected(db, a.text, idx, ctx,
                      snap_dir, due_date=a.due, speaker=getattr(a, "speaker", None),
                      explicit=_explicit)
    # --accept: 저장과 동시에 owner_accepted 확정 — 별도 ACCEPT 문구 면제(SAVE confirm 이 이미 사람 확인).
    # 인정=SAVE 통합(2트랙 설계) — candidate→accept 한 명령. pair --accept 동형(accept_by_node_id).
    if r.get("applied") and getattr(a, "accept", False):
        accepted = 0
        for nid in r.get("node_ids", []):
            ar = accept_by_node_id(db, nid, "save --accept 통합 확정(SAVE confirm 편승)",
                                   {"actor": ctx["actor"]})   # P1-A.1: 저장에 쓴 검증된 actor 재사용(fresh human 위조 금지)
            if ar.get("applied"):
                accepted += 1
        r["accepted"] = accepted
    db.close()
    _reindex_after_write(a.ledger)   # LFI 증분 갱신(best-effort · ledger write 0)
    return _show(r)


def cmd_pair(a):
    """owner 발화 + ai 요약을 각각 독립 노드(speaker=owner/ai)로 저장하고 연결 엣지로 묶는다.
    ai_text 생략 = owner 단독(순수 직감·억지 ai 금지). relation: accepts/refutes/revises.
    --confirm 생략 = 결합 미리보기 스테이징(저장 0) — 양축 후보를 한 preview(연속 번호:
    owner 1..N · ai N+1..)에 담아 사람 도장 1회('세이브 o,a')로 양축 ref 가 함께 기록되게
    한다(축별 preview+도장 2회 마찰 제거 · 도장=사람 키보드만 원칙 불변).
    저장 성공 시 owner 반응 노드는 owner_accepted 자동 편승(2트랙 통합·별도 ACCEPT 도장 면제,
    2026-07-20) — --tentative 로 수용 보류. ai/중립 노드는 미수용(단일 게이지 오염 방지)."""
    from binggupack.storage import save_paired   # 트랙 C: storage facade 경유
    rel = getattr(a, "by", "ai") + "_" + a.relation  # 반응 주체: ai(AI가 사용자 발화를) / owner(사용자가 AI 발화를)
    if a.confirm is None:
        # 결합 미리보기/스테이징(ledger 미접촉) — pair 는 명시 입력이므로 explicit 고정
        # (판단-veto 면제 · PII/secret/길이 안전 게이트는 그대로). 앵커 경로 = ledger 기준
        # home(PR#19 정합 — --ledger 격리 실행이 운영 앵커를 오염시키지 않게).
        try:
            import binggu_save_gate as _sg
            _oc = capture_preview(a.owner_text, explicit=True)["candidates"]
            _ac = capture_preview(a.ai_text, explicit=True)["candidates"] if a.ai_text else []
        except Exception as e:
            print("BLOCK: preview_unavailable — %s" % e)
            return 1
        if not _oc or (a.ai_text and not _ac):
            print("BLOCK: no_candidates — 후보 0건(빈 입력/안전 게이트 제외). 입력 문장을 확인하세요.")
            return 1
        home = os.path.dirname(os.path.abspath(a.ledger))
        _sg.write_last_preview(_oc + _ac, explicit=True,
                               path=os.path.join(home, "last_preview_candidates.json"))
        print("# pair 미리보기 — 결합 번호축(owner 1..%d · ai %s) · 도장 1회 · 미저장"
              % (len(_oc), ("%d.." % (len(_oc) + 1)) if _ac else "없음"))
        print("| # | 축 | 문장 |")
        print("|---|---|---|")
        for i, c in enumerate(_oc + _ac, 1):
            print("| %d | %s | %s |" % (i, "owner" if i <= len(_oc) else "ai",
                                        c.get("sentence", "")))
        stamp = ("세이브 %d,%d" % (a.owner_pick, len(_oc) + a.ai_pick)) if a.ai_text \
            else ("세이브 %d" % a.owner_pick)
        conf = ("PAIR %s owner:%d ai:%d" % (rel, a.owner_pick, a.ai_pick)) if a.ai_text \
            else ("PAIR owner:%d" % a.owner_pick)
        print('\n도장(사람 키보드): "%s" 입력 → 이후 같은 pair 명령에 --confirm "%s" 로 저장' % (stamp, conf))
        return 0
    db, snap_dir = _open(a.ledger)
    # save-n 참조 바인딩 — owner/ai 각 preview 의 (pref, pick) 2-튜플 대조. explicit 모드는
    # last_preview 기록 모드로 동일 재계산(pref 패리티 · MUST_FIX 2 — 기본은 _pick_one_node 와 동일 True).
    _refs = None
    _refs_combined = None
    try:
        import binggu_save_gate as _sg
        import json as _json
        _mode = True
        try:
            with open(_sg.last_preview_path(), "r", encoding="utf-8") as _f:
                _mode = bool(_json.load(_f).get("explicit", _mode))
        except Exception:
            pass
        _oc = capture_preview(a.owner_text, explicit=_mode)["candidates"]
        _refs = [(_sg.preview_ref_for_candidates(_oc), [a.owner_pick])]
        _ac = []
        if a.ai_text:
            _ac = capture_preview(a.ai_text, explicit=_mode)["candidates"]
            _refs.append((_sg.preview_ref_for_candidates(_ac), [a.ai_pick]))
        # 결합 번호축(도장 1회) 대안 — 위 preview 모드가 스테이징한 owner+ai 연속 번호 preview 에
        # 사람이 '세이브 o,a' 를 찍은 경우. 축별 2-튜플과 결합 1-튜플 중 어느 쪽이든 전부 통과면 human.
        _cidx = [a.owner_pick] + ([len(_oc) + a.ai_pick] if a.ai_text else [])
        _refs_combined = [(_sg.preview_ref_for_candidates(_oc + _ac), _cidx)]
    except Exception:
        _refs = None
        _refs_combined = None
    ctx = _resolve_human_ctx(a.ledger, _refs_combined, a.confirm)
    if ctx.get("actor") != "human" and _refs is not None:
        ctx = _resolve_human_ctx(a.ledger, _refs, a.confirm)
    r = save_paired(db, a.owner_text, a.ai_text, ctx,
                    snap_dir, relation_kind=rel, owner_pick=a.owner_pick, ai_pick=a.ai_pick, due_date=a.due)
    acc_note = ""
    if r.get("applied") and not getattr(a, "tentative", False):
        # 저장 도장과 동시에 owner_accepted 확정 — 별도 ACCEPT 문구 면제(SAVE 도장이 이미 사람 확인).
        # owner 반응 노드(owner_node_id)만 수용 — ai/중립 노드는 미수용(단일 게이지 오염 방지·R1-1).
        # --tentative 면 보류(기록만). ctx["actor"]=검증된 저장 actor 재사용(fresh human 위조 금지·P1-A.1).
        ar = accept_by_node_id(db, r["owner_node_id"],
                               "pair 저장 편승 확정(SAVE 도장=수용)", {"actor": ctx["actor"]})
        acc_note = " · 확정 OK" if ar.get("applied") else (" · 확정 실패(%s)" % ar.get("reason"))
    db.close()
    _reindex_after_write(a.ledger)   # LFI 증분 갱신(best-effort · ledger write 0)
    if r.get("applied"):
        tail = (" (%s 연결)" % r["relation"]) if r.get("paired") else " (owner 단독)"
        print("OK: 저장 %d건%s · pack=%s%s" % (r["saved"], tail, r.get("pack_id"), acc_note))
        return 0
    return _show(r)


def _anchor_session_id(anchor_path):
    """저장 앵커(last_preview_candidates.json)에 마무리 hook 이 심은 session_id(있으면).
    save-batch 가 마무리 preview 와 동일 세션 목록을 재현하기 위한 힌트 — 부재/손상/구형(필드
    없음) → None(전체 목록·하위호환). read-only(원문 미접근)."""
    try:
        import json as _json
        with open(anchor_path, "r", encoding="utf-8") as f:
            return _json.load(f).get("session_id")
    except Exception:
        return None


def cmd_save_batch(a):
    """세션 마무리 candidate 번호 배치 저장 — preview(앵커) / --confirm 저장 2단계.

    발화별 pair preview→SAVE 반복(N번) UX 제거(2026-07-18 owner 지적). owner 는 세션 마무리
    preview 를 보고 'SAVE 6,11,13' 한 번만 발화. 승인 경계: owner SAVE 앵커가 유일 근거
    (gate_human_for_ref 검증) · 각 candidate 는 기존 save_paired 로 저장(무변경) · 자동저장 0."""
    from binggu_save_batch import stage_batch_anchor, render_batch_preview, save_candidates_batch
    from binggu_capture_persist import PersistentCaptureBuffer
    home = os.path.dirname(os.path.abspath(a.ledger))
    anchor_path = os.path.join(home, "last_preview_candidates.json")
    # ★소스 단일화: 마무리 hook 이 앵커에 심은 session_id 가 있으면 그 세션 발화로 필터
    #   (마무리 preview 와 동일 목록·idx·pref) → owner 'SAVE n' 앵커와 일치·오저장 방지.
    #   앵커 부재/구형(session_id 없음) → 전체 목록(하위호환).
    _sid = _anchor_session_id(anchor_path)
    buf = PersistentCaptureBuffer(home=home)
    items = (buf.render_preview(session_id=_sid) if _sid is not None
             else buf.render_preview()).get("items", [])
    if not items:
        print("배치 저장 후보 0건 — 세션 마무리 candidate 가 없습니다.")
        return 0
    if a.confirm is None:
        # preview + 번호축 앵커 생성(owner 'SAVE n' 발화 대조용 · 저장 0 · session_id 보존)
        stage_batch_anchor(items, path=anchor_path, session_id=_sid)
        print(render_batch_preview(items))
        return 0
    # confirm: 'SAVE 6,11,13' → 저장
    from binggupack.safety.gate_log import parse_save_indices
    indices = parse_save_indices(a.confirm)
    if not indices:
        print("BLOCK: confirm 형식 오류 — 'SAVE 6,11,13' 형식이어야 합니다.")
        return 1
    db, snap_dir = _open(a.ledger)
    r = save_candidates_batch(db, snap_dir, items, indices,
                              gate_log_path=_gate_log_for_ledger(a.ledger))
    if not r.get("applied"):
        print("BLOCK: %s — 저장 0건." % r.get("reason"))
        if r.get("reason") == "no_save_gate_ref":
            print("  owner 의 'SAVE %s' 발화 앵커가 없습니다 — preview(save-batch) 먼저 → "
                  "SAVE 발화 → 재실행." % ",".join(str(i) for i in indices))
        return 1
    by_cand = {}
    for res in r["results"]:
        by_cand.setdefault(res["cand"], []).append(res)
    print("OK: 배치 저장 %d건 (candidate %d개%s)"
          % (r["saved"], len([c for c in by_cand]), (" · skip %d" % r["skipped"]) if r["skipped"] else ""))
    for cand, ress in by_cand.items():
        ok = sum(1 for x in ress if x.get("applied"))
        dup = sum(1 for x in ress if x.get("reason") == "pair_partial_exists")
        note = (" (중복 %d)" % dup) if dup else ""
        print("  candidate %s → %d건 저장%s" % (cand, ok, note))
    return 0


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
    # actor 판정 = _resolve_human_ctx(save-n 참조 바인딩/cli_command · 에이전트 세션은 deny). env fail-open 없음.
    ctx = _resolve_human_ctx(a.ledger, None, a.confirm)
    r = deprecate_from_list(db, a.n, a.id8, a.reason, ctx, snap_dir)
    db.close()
    _reindex_after_write(a.ledger)   # LFI 증분 갱신(폐기 반영 · ledger write 0)
    return _show(r)


def cmd_replace(a):
    db, snap_dir = _open(a.ledger)
    ctx = _resolve_human_ctx(a.ledger, None, a.confirm)   # P1-A.1 fail-closed
    r = replace_from_list(db, a.n, a.id8, getattr(a, "with"), a.reason, ctx, snap_dir)
    db.close()
    _reindex_after_write(a.ledger)   # LFI 증분 갱신(교체 반영 · ledger write 0)
    return _show(r)


def cmd_accept(a):
    db, _ = _open(a.ledger)
    _aid = getattr(a, "approval_id", None)
    if _aid is not None:  # 비대화형 owner exact-bound approval 경로
        r = _mutation_via_approval(
            a, db, "accept",
            {"index": a.n, "id8": a.id8, "reason": a.reason, "approval_id": _aid},
            lambda ctx: accept_from_list(db, a.n, a.id8, a.reason, ctx))
    else:                 # save-n 참조 바인딩 / cli_command (_resolve_human_ctx 판정)
        r = accept_from_list(db, a.n, a.id8, a.reason, _resolve_human_ctx(a.ledger, None, a.confirm))
    db.close()
    return _show(r)


def cmd_unaccept(a):
    db, _ = _open(a.ledger)
    _aid = getattr(a, "approval_id", None)
    if _aid is not None:
        r = _mutation_via_approval(
            a, db, "unaccept",
            {"index": a.n, "id8": a.id8, "reason": a.reason, "approval_id": _aid},
            lambda ctx: unaccept_from_list(db, a.n, a.id8, a.reason, ctx))
    else:
        r = unaccept_from_list(db, a.n, a.id8, a.reason, _resolve_human_ctx(a.ledger, None, a.confirm))
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
    _aid = getattr(a, "approval_id", None)
    if _aid is not None:  # 비대화형 owner exact-bound approval 경로
        r = _mutation_via_approval(
            a, db, "due",
            {"node_id": nid, "due_date": a.date, "approval_id": _aid},
            lambda ctx: set_review_due(db, nid, a.date, ctx))
    else:
        r = set_review_due(db, nid, a.date, _resolve_human_ctx(a.ledger, None))   # P1-A.1 fail-closed
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

    def _resolve_core(ctx):
        rr = resolve_review(db, nid, a.outcome, a.reason, ctx)
        # 양방향 신뢰도 연동 — 성공/실패만 hit_events(불확실/판정불가 skip). 사람 resolve 한정(불변식6).
        # M1(사전검증): record_resolution 은 pair-ai fan-out 을 포함 — ctx.actor 재사용(human 위조 없음).
        if rr.get("applied") and a.outcome in ("성공", "실패"):
            import binggu_hit_stats as _HS
            _HS.record_resolution(db, nid, a.outcome == "성공", ctx)
        return rr

    _aid = getattr(a, "approval_id", None)
    if _aid is not None:  # 비대화형 owner exact-bound approval 경로
        r = _mutation_via_approval(
            a, db, "resolve",
            {"node_id": nid, "outcome": a.outcome, "reason": a.reason, "approval_id": _aid},
            _resolve_core)
    else:
        r = _resolve_core(_resolve_human_ctx(a.ledger, None))   # P1-A.1: 비대화형/env → reader → fail-closed
    db.close()
    return _show(r)


def cmd_abstraction(a):
    """반복 판단 + hit_events 에서 규칙 후보(추상화)를 '제안만' 조회 — read-only·자동확정 0.

    조회(기본): 제안 문구만 표시(DB write 0 · self-modifying 0).
    등록(--promote <proposal_id>): 그 제안을 candidate 로 등록(승격 '연결'). active 승격은 여전히
      별도 promote 단계(evidence 1:1 검증)로 남는다. 정확 문구 "PROMOTE <proposal_id>" 일치만 write,
      dry-run 기본(confirm 없으면 미리보기만), 문장 분할로 원문이 잘리면 자동 등록 차단(수동 저장 안내).
      실제 등록은 기존 save 경로(save_selected)를 재사용(candidate=1·conv-self 자기증빙 1:1·감사/체크섬)."""
    from binggupack.pack import abstraction as ABS
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print(f"장부가 없습니다: %s · 먼저 {HINT} init" % ledger)
        return 2
    domain = getattr(a, "domain", None)
    home = os.path.dirname(ledger)
    promote_id = getattr(a, "promote", None)
    if not promote_id:
        proposals = ABS.propose_abstractions(ledger, domain=domain, home=home)
        print(ABS.render_proposals_md(proposals))
        return 0

    # ── candidate 등록(승격 연결) — 서버 재확보(D-1) → 사람 confirm 정확일치만 write ──
    spec = ABS.build_promotion_candidate_spec(ledger, promote_id, domain=domain, home=home)
    if spec is None:
        print("BLOCK: proposal_not_found — 재확보 목록에 없는 proposal_id 입니다.")
        print("  먼저 `binggu abstraction` 으로 현재 제안을 확인하세요(stale/위조 차단).")
        return 1
    confirm = getattr(a, "confirm", None)
    if not confirm:
        # dry-run 기본: 무엇이 등록될지 미리보기만(write 0)
        print("# 승격 미리보기(dry-run · 아직 등록 안 함 · write 0)")
        print("proposal_id : %s" % spec["proposal_id"])
        print("등록 문장    : %s" % spec["text"])
        print("근거(provenance·그래프 엣지 아님): %s" % ", ".join(spec["evidence_refs"]))
        print("자동 등록 안전(문장 분할 없음): %s" % spec["write_safe"])
        print('등록하려면   : binggu abstraction --promote %s --confirm "%s"'
              % (spec["proposal_id"], spec["confirm_phrase"]))
        return 0
    if confirm != spec["confirm_phrase"]:
        print('BLOCK: confirm_mismatch — 정확히 "%s" 를 입력해야 등록됩니다(자동확정 0).'
              % spec["confirm_phrase"])
        return 1
    if not spec["write_safe"]:
        # 억지 write 회피(안전 > 기능): 문장 분할로 원문이 잘리면 자동 등록하지 않고 수동 저장을 안내.
        print("BLOCK: unsafe_segmentation — 이 제안 문구는 문장 분할로 원문 그대로 저장되지 않습니다.")
        print("수동 저장(안전 경로)으로 등록하세요:")
        print('  binggu preview "%s"' % spec["text"])
        print('  binggu save "%s" --preview-id <표시된 id> --pick <원문 후보 번호> '
              '--confirm "SAVE <번호>" --explicit' % spec["text"])
        return 1

    # write: 기존 save 경로 재사용 — actor 판정은 _resolve_human_ctx(save-n 참조 바인딩/터미널) 그대로.
    db, snap_dir = _open(a.ledger)
    _refs = None
    try:
        import binggu_save_gate as _sg
        _cands = capture_preview(spec["text"], explicit=True)["candidates"]
        _refs = [(_sg.preview_ref_for_candidates(_cands), spec["save_indices"])]
    except Exception:
        _refs = None
    ctx = _resolve_human_ctx(a.ledger, _refs, spec["save_confirm"])
    r = save_selected(db, spec["text"], spec["save_indices"], ctx, snap_dir, explicit=True)
    db.close()
    if r.get("applied") and r.get("saved") == 1:
        print("OK: candidate 등록 완료 · pack=%s · node=%s"
              % (r.get("pack_id"), (r.get("node_ids") or ["?"])[0]))
        print("  active 승격은 별도 단계(기존 promote·evidence 1:1 검증) — 자동확정 0.")
        return 0
    if (not r.get("applied")) and r.get("skipped_existing"):
        print("이미 등록된 후보입니다(idempotent·중복 등록 0): skipped=%d" % r["skipped_existing"])
        return 0
    return _show(r)


def _mark_from_recall(a, ledger, outcome):
    """--from-recall — staging(직전 회상) idx→node_id 정본 + 사람 도장('히트 N'/'미스 N') 소비.

    마찰 해소 축: 사람 증명 = 별도 터미널 재입력이 아니라 owner 채팅 1-발화(UserPromptSubmit
    hook 이 save_gate_log.jsonl 에 기록한 recall 스탬프). 도장 강도는 SAVE 패리티(hook 기록 +
    gate 파일 존재/신선도 의존 · CLAUDECODE env 는 소프트 신호).

    절차: staging 로드 → gate_human_for_recall(소비시점 recall_gate_ref **재계산** 대조 —
    미도장/verdict 불일치/stale/staging 변조 전부 BLOCK·fail-closed) → human ctx 승격
    (actor_source=recall_stamp_ref) → mark_outcome(expected_node_id=staging 정본 —
    why_search 재확보 집합 실재 확인·없으면 stale_recall·MF1/MF4) → recorded=True 이고
    outcome=hit 일 때만 record_use(MF6 · miss 는 유용성 신호가 아니라 use 기여 0 —
    랭킹/승격 오염 차단 · adoption_key 는 --record 경로와 동일하게 staging 의
    query·domain 원문으로 파생 — 교차 경로 멱등).

    record_resolution(hit_events)과 record_use(use_count)는 각각 내부 commit 하는 별개
    write 다(원자 아님) — 중간 중단의 부분 실패는 decision_id/use_key 멱등으로 재실행 시
    이중계상 없이 수렴한다(정직 서술)."""
    import binggu_p1_ranking as RANK

    from binggupack.pack import hit_recording as HR
    from binggupack.safety import gate_log as GL
    st = GL.load_last_recall(_recall_staging_for_ledger(ledger)) or {}
    rows = st.get("items") or []
    if not rows:
        print(f"BLOCK: no_recall_staging — 직전 회상 staging 이 없습니다. 먼저 {HINT} recall 을 실행하세요.")
        return 1
    idx = a.index
    node_id = {r.get("idx"): r.get("node_id") for r in rows}.get(idx)
    if not node_id:
        print("BLOCK: index_out_of_range — staging 회상은 %d건(번호 1~%d)." % (len(rows), len(rows)))
        return 1
    gate_p = _gate_log_for_ledger(ledger)
    if not GL.gate_human_for_recall(rows, [idx], outcome, path=gate_p):
        print("BLOCK: stamp_not_found — 이 번호의 사람 도장이 없습니다"
              "(미도장/verdict 불일치/신선도 창 초과/staging 변조 전부 거부 · fail-closed).")
        print('  채팅에 정확형 1줄로 "%s %d" 를 입력한 뒤 다시 실행하세요.'
              % ("히트" if outcome == "hit" else "미스", idx))
        return 1
    # 사람 증명 = 도장(재계산 ref 대조 통과) — _resolve_human_ctx 경유(인벤토리 계약: 리터럴 0).
    ctx = _resolve_human_ctx(ledger, stamp_ctx="recall_stamp_ref")
    query = st.get("query") or ""
    domain = st.get("domain") or None
    db, _ = _open(ledger)
    used = None
    try:
        r = HR.mark_outcome(db, ledger, query, idx, outcome, ctx, nonce=None,
                            domain=domain or getattr(a, "domain", None),
                            home=os.path.dirname(ledger), expected_node_id=node_id)
        if r.get("recorded") and outcome == "hit":
            used = RANK.record_use(db, node_id, use_key=RANK.adoption_key(query, domain))
    finally:
        db.close()
    if r.get("recorded"):
        GL.stamp_mark_consumed(GL.recall_gate_ref(rows), [idx], path=gate_p)  # 감사 마킹(판정 미반영)
        _reindex_after_write(a.ledger)   # use_count 변화 → fresh_index rank 반영
        print("OK: %s 기록 — [%d] \"%s\"" % (outcome, idx, r.get("node_claim") or ""))
        print("    decision=%s · domain=%s · 사람 도장(recall_stamp_ref · 자동 0) · use_count=%s"
              % (r.get("decision_id"), r.get("domain"), used if used is not None else "-"))
        return 0
    reason = r.get("reason")
    print("BLOCK: %s" % reason)
    if reason == "stale_recall":
        print(f"  staging 노드가 재확보(why_search) 집합에 없습니다 — ledger 변경/회상 낡음."
              f" {HINT} recall 재실행 후 새 번호로 다시 도장하세요.")
    elif reason == "dup_decision":
        print("  이미 같은 회상에서 기록됨(이중계상 방지). 중복 아님.")
    elif reason == "no_recall":
        print("  staging query 로 재확보되는 판단이 없습니다. recall 재실행 후 다시 도장하세요.")
    return 1


def cmd_mark(a):
    """회상(recall/why) 조언의 적중/빗나감 기록 — mark-hit / mark-miss.

    node_id 를 직접 받지 않고 (query, index)로 받아 why_search 를 재실행해 서버가 노드를 확보한다
    (D-1 위조 차단). nonce 로 회상 스냅샷을 고정하고(stale 차단), decision_id 를 (node_id,nonce)
    안정 해시로 만들어 반복 mark 를 dup_decision 으로 막는다(D-2 이중계상 차단). 사람 확정만(actor=human).
    --from-recall 은 staging+채팅 도장 소비 경로(_mark_from_recall) — 별도 터미널 증명 대체."""
    from binggupack.pack import hit_recording as HR
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print(f"장부가 없습니다(회상 기억 없음): %s · 먼저 {HINT} init" % ledger)
        return 2
    outcome = "hit" if a.cmd == "mark-hit" else "miss"
    if getattr(a, "from_recall", False):
        return _mark_from_recall(a, ledger, outcome)
    if not a.query:
        print("BLOCK: query_required — --from-recall 없이는 query 인자가 필요합니다.")
        return 2
    db, _ = _open(ledger)
    r = HR.mark_outcome(db, ledger, a.query, a.index, outcome, _resolve_human_ctx(a.ledger, None),
                        nonce=a.nonce, domain=a.domain, home=os.path.dirname(ledger))   # P1-A.1 fail-closed
    db.close()
    if r.get("recorded"):
        print("OK: %s 기록 — [%d] \"%s\"" % (outcome, a.index, r.get("node_claim") or ""))
        print("    decision=%s · domain=%s · 사람 확정(자동 0)"
              % (r.get("decision_id"), r.get("domain")))
        return 0
    reason = r.get("reason")
    print("BLOCK: %s" % reason)
    if reason == "stale_recall":
        print(f"  회상 결과가 바뀌었습니다. {HINT} recall \"%s\" 로 nonce 를 다시 받으세요"
              " (기대 nonce=%s)." % (a.query, r.get("expected_nonce")))
    elif reason in ("index_out_of_range",):
        print("  index 가 회상 건수를 벗어났습니다(회상 %s건). recall 로 번호를 확인하세요."
              % r.get("recall_count", "?"))
    elif reason == "no_recall":
        print("  이 query 로 회상되는 판단이 없습니다. recall 로 먼저 확인하세요.")
    elif reason == "dup_decision":
        print("  이미 같은 회상에서 기록됨(이중계상 방지). 중복 아님.")
    return 1


def cmd_learn_consume(a):
    # 본체는 binggupack/cli/learn_consume_cmd.py 로 이관(금고 §3 — 이동만·게이트 로직/문구 불변). lazy import 순환차단.
    from binggupack.cli import learn_consume_cmd as _m
    return _m.cmd_learn_consume(a)


def cmd_verdict(a):
    """논쟁 판정 즉시 기록(단순판·2026-07-16 owner "누가 맞았는지만 체크") — 개방 기록 트랙.

    사장님 주장 ↔ AI 주장이 실측으로 판가름 난 순간 AI 가 즉시 기록한다 — 큐·세션마무리 표·
    컨슘 도장 의식 전부 대체(의식 0). 사람 도장 없는 유일한 hit_events 경로: 방어는
    evidence 필수 + subtype='ai판정' 라벨(신뢰서열 구분) + owner 1-발화 정정(--overturn N).
    actor/confirm 미사용 — 승인 경로가 아니라 기록 트랙(§approval-origin 인벤토리 무관)."""
    from binggupack.pack import hit_recording as HR
    ledger, _ = _ledger_paths(a.ledger)
    home = os.path.dirname(ledger)
    if getattr(a, "overturn", None) is not None:
        if not os.path.exists(ledger):
            print(f"장부가 없습니다: %s · 먼저 {HINT} init" % ledger)
            return 2
        db, _ = _open(ledger)
        try:
            r = HR.overturn_verdict(db, home, a.overturn)
        finally:
            db.close()
        if r.get("overturned"):
            print("OK: 판정 뒤집음 — [%d] 이제 %s 맞음 (기록 트랙 정정·감사 로그 보존)"
                  % (r["seq"], "사장님" if r["who_right_now"] == "owner" else "AI"))
            return 0
        print("BLOCK: %s" % r.get("reason"))
        return 1
    who = "owner" if getattr(a, "owner_right", False) else ("ai" if getattr(a, "ai_right", False) else None)
    if who is None:
        # 인자 없음 = 최근 판정 목록(원문 전문 — 요약 금지)
        rows = HR.list_verdicts(home, limit=10)
        if not rows:
            print("판정 기록 0건. (논쟁이 실측으로 판가름 나면 즉시 기록됩니다 — 의식 0)")
            return 0
        print("최근 판정 %d건 (뒤집기: %s verdict --overturn <번호>)" % (len(rows), HINT))
        for seq, e in rows:
            mark = "뒤집힘→" if e.get("overturned") else ""
            print("[%d] %s%s 맞음 · %s" % (seq, mark,
                  "사장님" if e.get("who_right") == "owner" else "AI", e.get("ts") or ""))
            print("    사장님: %s" % e.get("owner_claim"))
            print("    AI: %s" % e.get("ai_claim"))
            print("    증거: %s" % e.get("evidence"))
        return 0
    if not os.path.exists(ledger):
        print(f"장부가 없습니다: %s · 먼저 {HINT} init" % ledger)
        return 2
    db, _ = _open(ledger)
    try:
        r = HR.record_verdict(db, home, a.owner, a.ai_claim, who, a.evidence,
                              domain=getattr(a, "domain", None))
    finally:
        db.close()
    if r.get("recorded"):
        print("OK: 판정 기록 — %s 맞음 (ai판정 라벨·증거 첨부·자동확정 아님 — 기록 트랙)"
              % ("사장님" if who == "owner" else "AI"))
        print("    decision=%s · 뒤집기: %s verdict --overturn <번호> (verdict 로 번호 확인)"
              % (r.get("decision_id"), HINT))
        return 0
    reason = r.get("reason")
    print("BLOCK: %s" % reason)
    if reason == "evidence_required":
        print("  실측 증거 1줄(--evidence)이 없으면 기록하지 않습니다(자기채점 방어).")
    elif reason == "dup_decision":
        print("  같은 논쟁이 이미 기록됨(이중계상 차단). 정정은 --overturn.")
    elif reason == "empty_claim":
        print("  --owner(사장님 주장)·--ai(AI 주장) 원문이 둘 다 필요합니다.")
    return 1


def cmd_outcome(a):
    """회상→작업결과 귀속 기록/조회/정정(Recall→Outcome Attribution v0.1).

    측정 축을 '이 기억이 맞았나'(회수측)에서 '기억이 행동을 바꿔 결과를 개선했나'(결과-귀속)로 옮긴다.
    정상 경로 = AI 가 테스트·CI·실측 결과를 확인한 직후 자동 기록(evidence-gated · 관찰 로그라 SAVE
    불요). 별도 store(recall_trace.sqlite sibling) · 운영 ledger 미접촉 · append-only. 정정만 owner
    --overturn(사람 게이트). 인과 단정 0 — application·result 두 관찰 사실만."""
    from datetime import datetime, timezone
    from binggupack.pack import outcome_attribution as OA
    ledger, _ = _ledger_paths(a.ledger)
    home = os.path.dirname(ledger)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 정정: binggu outcome --overturn N (owner 전용 · 원본 보존 + reversal append)
    if getattr(a, "overturn", None) is not None:
        r = OA.overturn_run_outcome(a.overturn, ts, home=home)
        if r.get("overturned"):
            print("OK: 결과 판정 [%d] 정정(overturn) — 원본 보존·reversal 이력 append(삭제 0)" % r["seq"])
            return 0
        print("BLOCK: %s" % r.get("reason"))
        return 1
    # 기록: binggu outcome record --trace ... --application ... --result ... --evidence-kind ... --evidence-digest ...
    if getattr(a, "outcome_cmd", None) == "record":
        trace = getattr(a, "trace", None)
        node_ids = ([n.strip() for n in a.nodes.split(",") if n.strip()]
                    if getattr(a, "nodes", None) else None)
        if not trace or not node_ids:  # staging 폴백(정상 자동 경로 — 직전 회상 trace 재사용)
            st = OA.last_staged_trace(home=home)
            if st:
                trace = trace or st.get("trace_id")
                node_ids = node_ids or st.get("node_ids")
        if not trace:
            print("BLOCK: --trace 없음(직전 회상 staging 도 없음). preflight/recall 회상 후 기록하세요.")
            return 1
        r = OA.record_run_outcome(trace, node_ids or [], a.application, a.result,
                                  a.evidence_kind, a.evidence_digest, ts, home=home)
        if r.get("recorded"):
            print("OK: 결과-귀속 기록 — 적용=%s 결과=%s (ai_observation 관찰 로그·자동확정 아님)"
                  % (r["application"], r["result"]))
            print("    outcome=%s · 정정: %s outcome --overturn <번호>" % (r["outcome_id"], HINT))
            return 0
        reason = r.get("reason")
        print("BLOCK: %s" % reason)
        hints = {
            "evidence_required": "  결과 증거 digest(--evidence-digest)가 없으면 기록 안 함(fail-closed).",
            "trace_not_found": "  --trace 가 실제 회상 trace 가 아닙니다. 회상 후 그 trace_id 로.",
            "node_not_in_trace": "  --nodes 는 그 trace 가 회상한 node_id 부분집합만 가능(%s)." % r.get("bad"),
            "dup_outcome": "  같은 (trace, 증거)는 이미 기록됨(1회). 정정은 --overturn.",
            "invalid_application": "  --application 은 applied|ignored|corrected.",
            "invalid_result": "  --result 는 success|failure|mixed|unknown.",
            "invalid_evidence_kind": "  --evidence-kind 는 pytest|ci|file|user.",
        }
        if reason in hints:
            print(hints[reason])
        return 1
    # 인자 없음 = 최근 결과 목록(원문 전문 — 요약 금지) + signal_only 집계
    rows = OA.list_run_outcomes(home, limit=getattr(a, "limit", 10) or 10)
    agg = OA.aggregate_run_outcomes(home)["overall"]
    if not rows:
        print("결과-귀속 기록 0건. (회상→작업→결과를 evidence 와 함께 기록 — 자동 관찰·SAVE 불요)")
        print("  회상 trace %d건 중 결과 미연결 %d건 — 회상 후 outcome record 로 결과를 이으세요."
              % (agg["traces"], agg["pending_traces"]))
        return 0
    print("최근 결과-귀속 %d건 (정정: %s outcome --overturn <번호>)" % (len(rows), HINT))
    for r in rows:
        mark = "정정됨→" if r["overturned"] else ""
        print("[%d] %s적용=%s · 결과=%s · 증거=%s(%s) · %s"
              % (r["seq"], mark, r["application"], r["result"],
                 r["evidence_kind"], (r["evidence_digest"] or "")[:12], r["ts"] or ""))
        print("    기억: %s" % ", ".join(r["applied_node_ids"]))
    print("  집계(signal_only·인과 아님): trace %d · 적용 %d(성공 %d/실패 %d) · 무시 %d · 교정 %d · 미결 %d"
          % (agg["traces"], agg["applied"], agg["applied_success"], agg["applied_failure"],
             agg["ignored"], agg["corrected"], agg["pending_traces"]))
    return 0


def cmd_promote(a):
    # 본체는 binggupack/cli/promote.py 로 이관(금고 §3 — 이동만·게이트 로직/문구 불변). lazy import 순환차단.
    from binggupack.cli import promote as _m
    return _m.cmd_promote(a)


def cmd_reminders(a):
    db, _ = _open(a.ledger)
    today = a.today or datetime.date.today().isoformat()
    r = list_due_reminders(db, today)
    db.close()
    print(r["markdown"])
    return 0


def selftest():
    """임베드 selftest 는 binggupack/cli/selftest_embed.py 로 분리(God-file #4 → wheel 자족성).

    CLI 동작 불변 — 분리한 케이스(GATE=GO)를 그대로 import 해서 실행한다.
    (pytest 수집은 tests/test_binggu_cli_selftest.py thin shim — wheel 에 tests/ 미포함이라
    설치본 `binggu --selftest` ModuleNotFoundError 나던 결함 수정.)
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from binggupack.cli.selftest_embed import selftest as _impl
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
            print(f'  등록:  {HINT} harvest add --kind arxiv --url "https://arxiv.org/..."')
            return 0
        print("등록된 외부 소스 %d개:" % len(srcs))
        for i, s in enumerate(srcs, 1):
            print("  %d. [%s] %s%s" % (i, s.get("kind"), s.get("url"),
                                       (" (keyword=%s)" % s["keyword"]) if s.get("keyword") else ""))
            print(f"       제거:  {HINT} harvest remove %s" % s.get("source_id"))
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

      binggu confirm-edges                                   # 후보 목록(report only · 적재 0)
      binggu confirm-edges --approve 1,3 --confirm "CONFIRM EDGES 1,3"
    """
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print(f"장부가 없습니다: %s\n먼저 만드세요:  {HINT} init" % ledger)
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
        print(f'  {HINT} confirm-edges --approve <번호들> --confirm "CONFIRM EDGES <번호들>"')
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
                                     actor=_resolve_human_ctx(a.ledger, None)["actor"],   # P1-A.1 fail-closed
                                     now=int(__import__("time").time()))
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
    print("\n운영 ledger 등재(영구·owner-only·운영 write) — owner 로컬 승인 이벤트 경유:")
    print("  ① 승인 요청:  python scripts/hybrid_agi/hag_sync_adapter.py --import-edges "
          '--ledger "%s" --sync-db "%s"' % (ledger, sync_db))
    print("  ② 로컬 승인:  binggu approval approve <요청ID>")
    print("  ③ 등재 확정:  위 명령에 --approval-id <요청ID> 추가(승인 이벤트 없이는 write 0)")
    return 0


def cmd_setup_cloud(a):
    """setup-cloud — 흩어진 cloud 셋업 명령을 1개 진입점으로(멱등·실패정지).
    얇은 래퍼 — 실 오케스트레이션은 scripts/binggu_setup_cloud.py(순수함수+selftest).
      binggu setup-cloud            # 점검만(dry-run · 변경 0)
      binggu setup-cloud --apply    # kv create/toml 기입/kv put/스케줄러 등록
      binggu setup-cloud --apply --deploy   # 위 + wrangler deploy(비가역)
    login(브라우저 OAuth)·deploy 결정은 본인 손 — 스크립트는 점검+안내+멱등 적용만."""
    import binggu_setup_cloud as SC  # scripts/ 는 이미 sys.path 에 있음
    res = SC.run_setup(apply=bool(getattr(a, "apply", False)),
                       deploy=bool(getattr(a, "deploy", False)))
    print(SC.render_report(res))
    return 0 if res["halted_at"] is None else 2


def cmd_onboard(a):
    """onboard — 신규 사용자 원클릭 셋업: 읽기(setup-cloud) + 저장채널(save_mcp) + auto-pull.
    얇은 래퍼 — 실 오케스트레이션은 binggu_setup_cloud/binggu_setup_save(순수함수+selftest).
      binggu onboard                  # 점검만(dry-run · 변경 0)
      binggu onboard --apply          # 키 생성/kv/toml/스케줄러(배포 제외)
      binggu onboard --apply --deploy # 위 + worker 2종 deploy(비가역)
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
                             webmcp=bool(getattr(a, "webmcp", False)),
                             opencrab_url=getattr(a, "opencrab_url", None))
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
        if res.get("current_ledger_corrupt"):
            print("현재 장부는 손상 상태(카운트 -1 표기) — 교체 시 손상본은 _backup/pre_restore_corrupt_*.sqlite 로 byte 보존됩니다")
        print("백업: 노드 %d · 엣지 %d  ↔  현재: 노드 %d · 엣지 %d"
              % (res["backup_nodes"], res["backup_edges"], res["current_nodes"], res["current_edges"]))
        print(f'교체하려면:  {HINT} restore "%s" --confirm "%s"'
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
        print(f"장부가 없습니다: %s · 먼저 {HINT} init" % ledger)
        return 2
    if res["status"] == "CORRUPT_LEDGER":
        print("장부 파일이 손상됨: %s" % res.get("ledger"))
        print(f"{HINT} doctor 로 확인 후 {HINT} restore <백업> 으로 복구하세요.")
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


# ==================== 60초 데모 (binggu demo) ====================
# 결정론 예제(오프라인·네트워크 0·API 키 0). 격리 임시 장부에서 후보→검토→승인→저장→회상→근거
# 전 과정을 한 번에 보여준다. 운영 장부는 절대 건드리지 않는다.
# 데모 시나리오 SSOT — 데모와 tests/test_demo.py 가 같은 상수를 읽는다(승인 대상·질의 단일 진실).
# 분류기 실측(결정적 3후보 · 3회 동일): '선호합니다 / 하기로 정했어요 / 하기로 했어요' 어미가 후보로 포착.
#   후보1(판단)=결론 우선 선호 · 후보2(증거)=금요일 회고 · 후보3(판단)=배포 전 스테이징 검증.
# 승인은 번호가 아니라 approve_marker(내용)로 선택 → 분류기 순서 변동에 강건(취약한 '정확히 N개' 전제 없음).
DEMO_SCENARIO = {
    "input": (
        "저는 앞으로 답변을 결론부터 짧게 받는 걸 선호합니다. "
        "매주 금요일 오후에는 주간 회고를 하기로 정했어요. "
        "결제 배포 전에는 스테이징에서 먼저 검증하기로 했어요."
    ),
    "approve_marker": "스테이징",          # 승인할 후보를 식별하는 고유 부분문자열
    "query": "결제 배포 스테이징 검증",     # 승인한 기억을 새 프로세스에서 회상할 질의
}


def _operating_home():
    """데모 격리 가드용 — 운영 장부(DEFAULT_LEDGER)의 홈 디렉터리(오버라이드 전 기준)."""
    return os.path.dirname(os.path.abspath(DEFAULT_LEDGER))


def _canonical_path(p):
    """경로 표준화 — expanduser → abspath → realpath → normcase.
    심링크·상대경로·대소문자 별칭을 전부 해소해 '같은 실제 대상'을 문자열로 비교 가능하게 한다."""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(str(p)))))


def _same_path(x, y):
    """두 경로가 같은 실제 대상인지. 둘 다 존재하면 os.path.samefile(장치/inode 동일성)으로,
    아니면 canonical 문자열로 비교(심링크·대소문자 별칭 포함). 존재/미존재 어느 쪽이든 안전."""
    try:
        if os.path.exists(x) and os.path.exists(y):
            return os.path.samefile(x, y)
    except OSError:
        pass
    return _canonical_path(x) == _canonical_path(y)


def cmd_demo(a):
    """60초 데모 — 격리 임시 장부에서 후보→검토→승인→저장→회상→근거 전 과정을 보여준다.

    안전 불변식(P0.1 하드닝):
      · 운영 장부 접근 0 — 격리 데모 홈(임시 폴더 또는 --home)만 사용.
        · demo 홈이 운영 홈과 같은 실제 디렉터리면 BLOCK(심링크·대소문자 별칭·samefile 포함).
        · demo 장부가 운영 장부와 같은 실제 파일이면 BLOCK.
        · --home 아래에 기존 ledger.sqlite 가 있으면 BLOCK(기존 장부 재사용/오염 금지). 재사용은
          향후 별도 --reuse-demo-home 로 명시 설계 — 이번엔 안전하게 거부.
      · subprocess·예외·조기 return 어디로 빠지든 기존 BINGGU_HOME 복구 + 자동생성 임시 홈 정리(finally).
      · --keep 은 '새로 만든 데모 데이터를 남기는' 기능일 뿐, 기존 장부 재사용 수단이 아니다.
      · 승인 전 활성 기억 0 · 승인한 후보만 저장 · 거절 후보 저장 안 함.
      · 비대화형(--non-interactive)은 CI/자동화용. 승인은 데모 격리 홈에서만 시뮬레이션(source="demo"
        앵커), 운영 승인 절차를 우회하는 범용 수단이 아니다(운영 홈 write 0).
    """
    import shutil
    import tempfile

    keep = bool(getattr(a, "keep", False))
    user_home = getattr(a, "home", None)

    op_home = _operating_home()
    op_ledger = os.path.abspath(DEFAULT_LEDGER)
    created_tmp = False
    if user_home:
        demo_home = os.path.abspath(os.path.expanduser(user_home))
        demo_ledger = os.path.join(demo_home, "ledger.sqlite")
        # 운영 장부 오염 차단 — 심링크/대소문자 별칭/실파일까지 해소해 비교(가드는 env 변경 전에 수행).
        if _same_path(demo_home, op_home):
            print("BLOCK: --home 이 운영 장부 홈과 같은 실제 폴더입니다 — 다른 경로를 지정하세요.")
            print("       (운영 홈: %s)" % op_home)
            return 1
        if _same_path(demo_ledger, op_ledger):
            print("BLOCK: --home 아래 장부가 운영 장부와 같은 실제 파일입니다 — 다른 경로를 지정하세요.")
            return 1
        # 기존 ledger.sqlite 재사용 금지 — 기존 장부에 노드 추가·덮어쓰기 방지.
        if os.path.exists(demo_ledger):
            print("BLOCK: --home 아래에 이미 ledger.sqlite 가 있습니다 — 기존 장부 재사용은 지원하지 않습니다.")
            print("       빈 폴더를 지정하세요(재사용은 향후 --reuse-demo-home 로 명시 설계 예정).")
            return 1
    else:
        demo_home = tempfile.mkdtemp(prefix="binggu-demo-")
        created_tmp = True

    _saved_home = os.environ.get("BINGGU_HOME")
    try:
        os.makedirs(demo_home, exist_ok=True)
        # 이 프로세스의 홈을 데모 홈으로 고정 → gate/snapshot/ledger 전부 격리 정렬(운영 홈 미접촉).
        os.environ["BINGGU_HOME"] = demo_home
        # 본체는 binggupack/cli/demo.py 로 이관(구조 정리·동작 불변). lazy import 로 순환 차단.
        from binggupack.cli.demo import _demo_body
        return _demo_body(a, demo_home, keep, created_tmp, op_home)
    finally:
        # subprocess/예외/조기 return 무관하게 BINGGU_HOME 복구 + 자동생성 임시 홈 정리(예외에도).
        if _saved_home is None:
            os.environ.pop("BINGGU_HOME", None)
        else:
            os.environ["BINGGU_HOME"] = _saved_home
        if created_tmp and not keep:
            shutil.rmtree(demo_home, ignore_errors=True)


def cmd_explain(a):
    """explain <memory-id> — 그 기억의 근거 사슬·provenance(= trace show 별칭·read-only)."""
    ledger, _ = _ledger_paths(a.ledger)
    return _judgment_trace_show(ledger, a.memory_id)


def cmd_forget(a):
    """forget <memory-id> — 오래된 기억 폐기 안내. 확인 문구 게이트를 존중해 자동 삭제하지 않고,
    바로 실행할 deprecate 명령을 만들어 보여준다(사람 confirm 유지)."""
    ledger, _ = _ledger_paths(a.ledger)
    if not os.path.exists(ledger):
        print("장부가 없습니다: %s · 먼저 binggu init" % ledger)
        return 2
    db = open_accept(ledger)
    try:
        rows = list_candidates(db, "all", None)["rows"]
    finally:
        db.close()
    mid = a.memory_id
    match = None
    for j, row in enumerate(rows, 1):
        nid = row["node_id"]
        if nid == mid or nid.endswith(mid) or _node_id8(nid) == mid:
            match = (j, row)
            break
    if not match:
        print("기억을 찾을 수 없습니다: %s" % mid)
        print("  목록 확인:  binggu list")
        return 1
    n, row = match
    id8 = _node_id8(row["node_id"])
    print("# 폐기 대상 (아직 삭제 안 함):")
    print("  %s" % row["sentence"])
    print("\n다음 명령으로 확정 폐기하세요(확인 문구를 직접 입력해야 통과):")
    print('  binggu deprecate %d %s --reason "<사유>" --confirm "DEPRECATE %d %s"' % (n, id8, n, id8))
    return 0


def cmd_home(a):
    """home — 데일리 콘솔(상태 + 다음 할 일). local-only · read-only · 저장 0.
    인자 없는 `binggu` 와 동일 화면(이쪽은 --ledger/--json 존중)."""
    from binggupack.cli import daily
    return daily.print_home(a.ledger, as_json=getattr(a, "json", False))


def cmd_inbox(a):
    """inbox — 로컬 통합 검토함(자동 수집 후보 · 원격 저장 의도 · 승인 요청 · 검토 예정).
    read-only · 저장 0 · 네트워크 fetch 0(로컬 스냅샷만). 원격을 새로 가져오려면 `binggu hosted inbox`."""
    from binggupack.cli import daily
    sections = [s for s in ("capture", "hosted", "approvals", "due") if getattr(a, s, False)]
    return daily.print_inbox(a.ledger, sections or None, as_json=getattr(a, "json", False))


def cmd_studio(a):
    """studio — 로컬 read-only 웹 UI(loopback only · 실행마다 새 ephemeral session · Daily Console
    snapshot 재사용). 브라우저 자동 열기(--no-open 으로 생략) · Ctrl+C 종료. mutation/approve/fetch 0."""
    from binggupack.studio import server
    return server.serve(a.ledger, port=getattr(a, "port", 0), open_browser=not getattr(a, "no_open", False))


def _node_id8(node_id):
    """node_id → 표시용 hash8(deprecate/replace confirm 의 id8 규약과 동일)."""
    from openbinggu_candidate_list_view import node_id8 as _n8
    return _n8(node_id)


def _home_screen():
    """인자 없이 `binggu` 실행 시 데일리 콘솔(상태 + 다음 할 일). local-only · read-only · 저장 0.
    (mode=ro 조회만 — 기존 open_accept 경로의 snapshot 디렉토리 생성 등 write 부작용 제거.)"""
    from binggupack.cli import daily
    return daily.print_home(DEFAULT_LEDGER, as_json=False)


def cmd_approvals(a):
    # 본체는 binggupack/cli/approval.py 로 이관(금고 §3 — 이동만·게이트 로직/문구 불변). lazy import 순환차단.
    from binggupack.cli import approval as _m
    return _m.cmd_approvals(a)


def cmd_approval(a):
    # 본체는 binggupack/cli/approval.py 로 이관(금고 §3 — 이동만·게이트 로직/문구 불변). lazy import 순환차단.
    from binggupack.cli import approval as _m
    return _m.cmd_approval(a)


_HELP_EPILOG = """\
일상 명령 (자주 쓰는 것부터):
  start        내 장부 생성(장부만 · 자동수집은 capture install 로 별도)
  home         오늘 할 일 + 상태 한눈에 (인자 없이 binggu 와 동일)
  inbox        검토함 — 자동수집·원격·승인대기·검토예정 모아보기
  preview      저장 후보 미리보기 (저장 0)
  save         preview 후 고른 후보만 도장 (SAVE n 게이트)
  pair         owner 발화 + ai 요약 페어 저장
  recall       회상 — 왜 이렇게 판단했나 (read-only)
  list         후보 목록
  status       장부 요약 · 건강 진단 (= doctor)
  reflect      회고 → 지식 후보
  demo         60초 격리 체험 (운영 장부 미접촉)

고급 명령:
  studio · capture · explain · forget · preflight · index · deprecate · replace ·
  accept · unaccept · due · resolve · reminders · mark-hit · mark-miss ·
  learn-consume · abstraction · promote · trace · hosted · harvest · trust · route ·
  confirm-edges · setup-cloud · onboard · backup · export · restore ·
  approvals · approval · app
  (promote = candidate→active 봉인 승격 · abstraction --promote = 규칙 제안의 candidate 등록 — 별 단계)

각 명령 상세:  binggu <command> -h
"""


# rank12 SSOT — 순수 별칭(동작 100% 동일) 정의 1곳. argparse aliases= 와 dispatch 정규화가 여기서 파생.
# remember(preview+explicit)·why(독립 파서)·mark-hit/miss(독립 명령)는 별칭이 아니므로 제외.
_PURE_ALIASES = {"init": ["start"], "status": ["doctor"], "recall": ["ask"]}
_ALIAS_TO_CANON = {al: canon for canon, als in _PURE_ALIASES.items() for al in als}


def main():
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    if not sys.argv[1:]:  # 인자 없이 실행 → 친절한 홈 화면(argparse 에러 대신)
        sys.exit(_home_screen())
    p = argparse.ArgumentParser(prog="binggu", description="BingguPack 개인 장부 CLI",
                                epilog=_HELP_EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ledger", default=DEFAULT_LEDGER)
    try:
        from binggupack.__about__ import __version__ as _bgp_ver
    except Exception:
        _bgp_ver = "unknown"
    p.add_argument("--version", action="version", version="binggupack %s" % _bgp_ver)
    # metavar 로 usage 의 거대한 {demo,init,...} 브레이스를 <command> 로 축약(서브파서 로직 불변).
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")
    # 60초 데모(설치 직후 체험) — 격리 임시 장부·오프라인·운영 장부 미접촉.
    dmp = sub.add_parser("demo")
    dmp.add_argument("--non-interactive", action="store_true", dest="non_interactive")
    dmp.add_argument("--home", default=None)   # 데모 격리 홈(미지정=임시폴더·종료 시 자동정리)
    dmp.add_argument("--keep", action="store_true")   # 데모 데이터 보존(정리 안 함)
    # 쉬운 별칭(UX): start=init · doctor=status · ask=recall — 순수 별칭은 _PURE_ALIASES SSOT 1곳.
    # remember=preview+explicit · why=recall 은 동작이 달라 별칭이 아니며 dispatch dict 에 별도 유지.
    ip = sub.add_parser("init", aliases=_PURE_ALIASES["init"])
    ip.add_argument("--agi-memory", action="store_true", dest="agi_memory")  # 명시 별칭(동작 동일)
    ip.add_argument("--global", action="store_true", dest="global_scope")     # 전역 수집(미지정 시 현재 위치만)
    ip.add_argument("--with-capture", action="store_true", dest="with_capture")  # 자동 후보 수집 옵트인(hook 등록)
    ip.add_argument("--no-capture", action="store_true", dest="no_capture")   # 하위호환 no-op(기본이 곧 no-capture)
    ip.add_argument("--force-capture", action="store_true", dest="force_capture")  # (--with-capture 와) owner sticky OFF 해제 강제 ON
    sub.add_parser("status", aliases=_PURE_ALIASES["status"])
    hmp = sub.add_parser("home")            # 데일리 콘솔(상태+다음 할 일 · read-only · 인자없는 binggu 와 동일)
    hmp.add_argument("--json", action="store_true")
    stp = sub.add_parser("studio")          # 로컬 read-only 웹 UI(loopback · ephemeral session · 저장 0)
    stp.add_argument("--no-open", action="store_true", dest="no_open")   # 브라우저 자동 열기 생략(headless/CI)
    stp.add_argument("--port", type=int, default=0)                       # 0=OS 임시 포트(권장) · 고정 필요 시 지정
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
    rcp = sub.add_parser("recall", aliases=_PURE_ALIASES["recall"]); rcp.add_argument("query")
    rcp.add_argument("--limit", type=int, default=None)
    rcp.add_argument("--record", action="store_true", dest="record")  # use_count++ (기본 read-only)
    rcp.add_argument("--deep", action="store_true")   # 원본 전체 탐색(기본=Hot 색인)
    rcp.add_argument("--project", default=None)        # 프로젝트 스코프(Warm)
    wp_ = sub.add_parser("why"); wp_.add_argument("query")                  # recall 별칭
    wp_.add_argument("--limit", type=int, default=None)
    wp_.add_argument("--record", action="store_true", dest="record")  # use_count++ (기본 read-only)
    wp_.add_argument("--deep", action="store_true")
    wp_.add_argument("--project", default=None)
    # Local Fresh Index 관리(파생 색인 · read-only · ledger write 0)
    ixp = sub.add_parser("index")
    ixsub = ixp.add_subparsers(dest="index_cmd", required=True)
    ixsub.add_parser("status").add_argument("--json", action="store_true")
    ixsub.add_parser("update")
    ixsub.add_parser("rebuild")
    ixsub.add_parser("pin").add_argument("node_id")
    ixsub.add_parser("unpin").add_argument("node_id")
    ixsub.add_parser("add-path").add_argument("path")       # 로컬 md/traj 인덱싱 경로 옵트인
    ixsub.add_parser("remove-path").add_argument("path")
    ixsub.add_parser("list-paths")
    # 기본 사용자 흐름 별칭(직관 명령) — 기존 명령 위임, 안전 게이트 동일.
    exp = sub.add_parser("explain"); exp.add_argument("memory_id")   # = trace show <id>(근거·이력)
    fgp = sub.add_parser("forget"); fgp.add_argument("memory_id")    # deprecate 안내(확인 문구 유지)
    # 로컬 통합 검토함(read-only aggregator · 저장 0 · fetch 0). 원격 회수는 `hosted inbox` 가 담당.
    ibx = sub.add_parser("inbox")
    ibx.add_argument("--capture", action="store_true")      # 자동 수집 후보만
    ibx.add_argument("--hosted", action="store_true")       # 원격 저장 의도(로컬 staging)만
    ibx.add_argument("--approvals", action="store_true")    # 대기 승인 요청만
    ibx.add_argument("--due", action="store_true")          # 검토 예정만
    ibx.add_argument("--json", action="store_true")         # 안정 read model(automation/Studio)
    # trace: 효용 trace(review/mark/enable/disable) + judgment_trace(show/<node_id> 하위호환)
    tp = sub.add_parser("trace")
    tp.add_argument("a1", nargs="?", default=None)   # review|mark|enable|disable|show|<node_id>
    tp.add_argument("a2", nargs="?", default=None)   # mark:N | show:node_id
    tp.add_argument("a3", nargs="?", default=None)   # mark:verdict
    tp.add_argument("--note", default=None)          # reason_code(화이트리스트)
    # ★2026-07-30: MCP trace_stamp 는 인자명이 `reason_code` 인데 CLI 는 `--note` 뿐이어서
    #   같은 도장을 두 이름으로 쳐야 했다(실사용 시 --reason 로 치고 무시됨 → 사유 유실).
    #   같은 dest 별칭으로 흡수 — 기존 --note 계약 불변(둘 다 주면 뒤에 온 값).
    tp.add_argument("--reason", dest="note", default=None)
    # AI 자기신고 도장(2026-07-27 owner 지시 — 히트/미스만 열외). actor=ai_stamp 로 원문 보존,
    # human 을 참칭하지 않는다. owner 가 나중에 같은 항목을 찍으면 사람 판정이 덮어쓴다.
    tp.add_argument("--ai", action="store_true")
    # 스냅샷 출처 대조(오도장 차단) — 세션 목록 기준으로 찍을 때 지정
    tp.add_argument("--expect-scope", default=None, dest="expect_scope")
    tp.add_argument("--expect-session", default=None, dest="expect_session")
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
        if name in ("accept", "unaccept"):   # P1-B A2 — 비대화형 owner exact-bound approval 경로
            sp.add_argument("--approval-id", dest="approval_id", default=None)
    sp = sub.add_parser("replace"); sp.add_argument("n", type=int); sp.add_argument("id8")
    sp.add_argument("--with", required=True, dest="with")
    sp.add_argument("--reason", required=True); sp.add_argument("--confirm", required=True)
    sp = sub.add_parser("due"); sp.add_argument("n", type=int); sp.add_argument("id8")
    sp.add_argument("--date", required=True)
    sp.add_argument("--approval-id", dest="approval_id", default=None)   # P1-B A2 비대화형 owner 경로
    sp = sub.add_parser("resolve"); sp.add_argument("n", type=int); sp.add_argument("id8")
    sp.add_argument("--outcome", required=True, choices=OUTCOMES)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--approval-id", dest="approval_id", default=None)   # P1-B A2 비대화형 owner 경로
    # 회상 조언 적중 기록(작업A) — mark-hit / mark-miss (query+index, node_id 미노출·nonce 방어)
    for _mk in ("mark-hit", "mark-miss"):
        mkp = sub.add_parser(_mk); mkp.add_argument("query", nargs="?", default=None)
        mkp.add_argument("--index", type=int, default=1)   # recall 표시 번호(1-based)
        mkp.add_argument("--nonce", default=None)           # recall 이 발급한 회상 봉인(stale 방어)
        mkp.add_argument("--domain", default=None)          # 분모 분리 키(선택)
        mkp.add_argument("--from-recall", action="store_true", dest="from_recall")
        #   ↑ 작업A2: staging(직전 회상)+채팅 도장("히트 N"/"미스 N") 소비 — query/nonce 불필요
    # 학습 큐(교환 후보) owner 승인 소비(작업C) — dry-run 기본 · CONSUME <n> 정확 confirm(자동 0)
    lcp = sub.add_parser("learn-consume")
    lcp.add_argument("--confirm", default=None)        # "CONSUME <n>" 정확 일치(자동확정 0)
    lcp.add_argument("--index", type=int, default=1)   # 회상 top 중 적중 노드(1-based·dry-run 확인)
    lcp.add_argument("--verdict", choices=("upheld", "overturned"), default="upheld")
    #   ↑ 사람 확인(교환 축): upheld=발화 판단이 결과적으로 옳았음(기본) · overturned=뒤집힘
    # 논쟁 판정 즉시 기록(단순판·2026-07-16 owner) — 개방 기록 트랙·의식 0. 인자없음=최근 목록
    vdp = sub.add_parser("verdict")
    vdg = vdp.add_mutually_exclusive_group()
    vdg.add_argument("--owner-right", action="store_true", dest="owner_right")
    vdg.add_argument("--ai-right", action="store_true", dest="ai_right")
    vdg.add_argument("--overturn", type=int, default=None)   # 목록 순번 정정("뒤집어 N")
    vdp.add_argument("--owner", default=None, help="사장님 주장(원문)")
    vdp.add_argument("--ai", dest="ai_claim", default=None, help="AI 주장(원문)")
    vdp.add_argument("--evidence", default=None, help="실측 증거 1줄(필수 — 없으면 기록 거부)")
    vdp.add_argument("--domain", default=None)
    # 회상→작업결과 귀속(Recall→Outcome Attribution v0.1) — 인자없음=목록 · record=기록 · --overturn=정정
    ocp = sub.add_parser("outcome")
    ocsub = ocp.add_subparsers(dest="outcome_cmd", required=False)
    ocr = ocsub.add_parser("record")
    ocr.add_argument("--trace", default=None, help="회상 trace_id(생략 시 직전 회상 staging 사용)")
    ocr.add_argument("--nodes", default=None, help="적용한 node_id 목록(쉼표구분·생략 시 staging)")
    ocr.add_argument("--application", default="applied", choices=("applied", "ignored", "corrected"))
    ocr.add_argument("--result", default="unknown", choices=("success", "failure", "mixed", "unknown"))
    ocr.add_argument("--evidence-kind", dest="evidence_kind", default=None,
                     choices=("pytest", "ci", "file", "user"), help="결과 증거 유형")
    ocr.add_argument("--evidence-digest", dest="evidence_digest", default=None,
                     help="결과 증거 digest(sha256 등·필수·원문 저장 0)")
    ocp.add_argument("--overturn", type=int, default=None, help="목록 순번 정정(owner 게이트)")
    ocp.add_argument("--limit", type=int, default=10)
    # 추상화 규칙 후보 제안(작업4·C) — read-only·자동확정 0. --promote 로 candidate 등록(승격 연결)
    abp = sub.add_parser("abstraction"); abp.add_argument("--domain", default=None)
    abp.add_argument("--promote", default=None,
                     help="proposal_id 를 candidate 로 등록(승격 연결). dry-run 기본")
    abp.add_argument("--confirm", default=None,
                     help='정확 문구 "PROMOTE <proposal_id>" 일치 시에만 등록(자동확정 0)')
    # candidate→active 봉인 승격(intel loop) — 인자없음=후보 리스트 · <n> <id8>=dry-run · --confirm=실행
    prp = sub.add_parser("promote")
    prp.add_argument("n", type=int, nargs="?", default=None)   # 후보 리스트 번호(1-based)
    prp.add_argument("id8", nargs="?", default=None)           # node_id 앞 8자(오지정 방지 이중 키)
    prp.add_argument("--confirm", default=None)   # 정확 문구 "PROMOTE <n> <id8>"(자동확정 0)
    prp.add_argument("--limit", type=int, default=0)   # 리스트 표시 상한(0=전체) — staging/번호는 항상 전체
    pp = sub.add_parser("pair")              # owner 발화 + ai 요약 페어 저장(화자 축)
    pp.add_argument("owner_text"); pp.add_argument("ai_text", nargs="?", default=None)
    pp.add_argument("--relation", choices=["accepts", "refutes", "revises"], default="accepts")
    pp.add_argument("--by", choices=["ai", "owner"], default="ai")  # 반응 주체(누가 누구를 수용/반박/수정)
    pp.add_argument("--owner-pick", type=int, default=1, dest="owner_pick")
    pp.add_argument("--ai-pick", type=int, default=1, dest="ai_pick")
    # --confirm 생략 = 결합 미리보기 스테이징(저장 0·도장 1회 흐름). 저장은 confirm 정확문구+사람 도장.
    pp.add_argument("--confirm", default=None); pp.add_argument("--due", default=None)
    # 2트랙 통합(2026-07-20): SAVE 도장 = 저장 + owner 반응 노드 수용(기본 ON). 별도 ACCEPT 문구 면제.
    # --tentative = 수용 보류(기록만). ai/중립 노드는 애초 미수용(게이지 오염 방지).
    pp.add_argument("--tentative", action="store_true")  # owner 반응 노드 수용 보류(기본은 편승 확정)
    sbp = sub.add_parser("save-batch")   # 세션 마무리 candidate 번호 배치 저장(발화별 반복 UX 제거)
    sbp.add_argument("--confirm", default=None)   # "SAVE 6,11,13" 정확형(owner SAVE 앵커 검증)
    tp = sub.add_parser("trust"); tp.add_argument("--subtype", default=None)  # 양방향 신뢰도(read-only)
    rtp = sub.add_parser("route"); rtp.add_argument("text")  # 저장 의도 라우팅(신규/수정/결과 read-only 안내)
    sp = sub.add_parser("reminders"); sp.add_argument("--today", default=None)
    cp = sub.add_parser("capture"); cp.add_argument("--settings", default=None)
    csub = cp.add_subparsers(dest="capture_cmd", required=True)
    for cs in ("status", "pause", "resume", "disable", "enable", "preview", "uninstall", "install-gate", "uninstall-gate"):
        csub.add_parser(cs)
    # capture install — start(기본)에서 분리한 명시 옵트인 설치(hook+scope). install-gate 와 별개 choice.
    cap_install = csub.add_parser("install")
    cap_install.add_argument("--force", action="store_true", dest="force_capture")  # owner sticky OFF 해제 강제 ON
    hp = sub.add_parser("hosted")
    hsub = hp.add_subparsers(dest="hosted_cmd", required=True)
    ibp = hsub.add_parser("inbox")          # 회수(저장0) + read-only 요약
    ibp.add_argument("--since", default=None)            # '7d' 또는 '7' (표시 필터·번호 고정)
    ibp.add_argument("--no-fetch", dest="no_fetch", action="store_true")  # worker 미접촉, staging 만
    ibp.add_argument("--no-anchor", dest="no_anchor", action="store_true")  # 무인 렌더: 사람 SAVE 앵커(last_preview) 미기록
    ibp.add_argument("--wait", type=int, default=0)
    ibp.add_argument("--variant", choices=["save_mcp", "save_v2"], default="save_mcp")
    ibp.add_argument("--workers-port", dest="wp", default=None)
    pp = hsub.add_parser("pull")            # 선택 묶음 → 사람 save-n + confirm 저장 (전량 자동 없음)
    pp.add_argument("--select", default=None)            # 'inbox' 에서 본 번호들 (예: 1,3)
    pp.add_argument("--confirm", default=None)           # "SAVE <n[,n]>" 정확 일치(사람 save-n 게이트)
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
    obp.add_argument("--opencrab-url", dest="opencrab_url", default=None)  # OpenCrab Expert 전용 MCP URL 등록(팩 자동생성 채널)
    bkp = sub.add_parser("backup")   # 장부 백업(일관 스냅샷 복사 · 운영 write 0)
    bkp.add_argument("--out", default=None)
    exp = sub.add_parser("export")   # 장부 내보내기(md/json · 데이터 주권)
    exp.add_argument("--format", dest="fmt", choices=["md", "json"], default="md")
    exp.add_argument("--out", default=None)
    rsp = sub.add_parser("restore")  # 백업 → 장부 교체(파괴적 · confirm 정확 일치 게이트)
    rsp.add_argument("backup")                           # 백업 sqlite 경로
    rsp.add_argument("--confirm", default=None)          # "RESTORE <백업파일명>" 정확 일치
    # P1-A trusted approval event — owner 검토·승인 채널(MCP tool surface 밖).
    sub.add_parser("approvals")                          # 대기 승인 요청 목록(조회)
    apv = sub.add_parser("approval")
    apv.add_argument("action", choices=["show", "approve", "reject", "revoke", "keychain-init"])
    apv.add_argument("request_id", nargs="?", default=None)   # keychain-init 은 request_id 불요
    # Binggu Anywhere — owner-only pack upload (admin plane client; MCP data plane never uploads).
    appp = sub.add_parser("app")
    appsub = appp.add_subparsers(dest="app_cmd", required=True)
    appup = appsub.add_parser("upload")
    appup.add_argument("--pack", required=True)          # explicit canonical pack directory only
    appup.add_argument("--endpoint", default=None)        # https gateway base url
    appup.add_argument("--confirm", action="store_true")  # perform upload (default: dry-run preview)
    a = p.parse_args()
    canon = _ALIAS_TO_CANON.get(a.cmd, a.cmd)   # rank12 SSOT — 순수 별칭 정규화(start→init·doctor→status·ask→recall)
    fn = {"init": cmd_init, "status": cmd_status,
          "home": cmd_home, "studio": cmd_studio,
          "preview": cmd_preview, "remember": lambda a: cmd_preview(a, explicit=True),  # remember=명시 입력
          "reflect": cmd_reflect, "save": cmd_save,
          "list": cmd_list, "deprecate": cmd_deprecate, "replace": cmd_replace,
          "accept": cmd_accept, "unaccept": cmd_unaccept, "due": cmd_due,
          "resolve": cmd_resolve, "mark-hit": cmd_mark, "mark-miss": cmd_mark,
          "learn-consume": cmd_learn_consume, "verdict": cmd_verdict,
          "abstraction": cmd_abstraction, "promote": cmd_promote,
          "reminders": cmd_reminders, "capture": cmd_capture,
          "recall": cmd_recall, "why": cmd_recall, "trace": cmd_trace, "preflight": cmd_preflight,
          "hosted": cmd_hosted, "harvest": cmd_harvest, "setup-cloud": cmd_setup_cloud,
          "onboard": cmd_onboard,
          "confirm-edges": cmd_confirm_edges, "pair": cmd_pair, "trust": cmd_trust,
          "route": cmd_route, "save-batch": cmd_save_batch, "backup": cmd_backup, "export": cmd_export,
          "restore": cmd_restore, "demo": cmd_demo, "explain": cmd_explain,
          "forget": cmd_forget, "inbox": cmd_inbox, "index": cmd_index,
          "approvals": cmd_approvals, "approval": cmd_approval, "app": cmd_app,
          "outcome": cmd_outcome}[canon]
    sys.exit(fn(a))


if __name__ == "__main__":
    main()
