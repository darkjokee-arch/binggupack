# -*- coding: utf-8 -*-
"""binggu preflight 명령 — binggu.py 에서 이관(구조 정리·동작 불변).

게이트 미접촉: read-only 회상 표시 + UserPromptSubmit hook 등록/토글 + --record use_count++.
백본 심볼(_ledger_paths·_open·_stage_recall·_reindex_after_write·hook 등록/토글·전역 상수)은
binggu.py 에 물리적으로 잔류하고 top-level `from binggu import` 로 참조한다. 순환은 binggu.py 가
이 모듈을 wrapper 함수 본문에서만 lazy import 하는 구조로 차단된다(selftest_embed·daily 선례).
"""
import os

from binggu import (  # noqa: E402  (binggu 완전 로드 후 lazy 진입 — 순환 차단)
    DEFAULT_SETTINGS,
    PREFLIGHT_MARKER,
    HINT,
    register_hook,
    unregister_hook,
    hook_registered,
    _preflight_hook_command,
    _ledger_paths,
    _open,
    _stage_recall,
    _reindex_after_write,
)


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
        print(f"활성화는 별도:  {HINT} preflight --enable   (기본 OFF)")
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
        print(f"끄기:  {HINT} preflight --disable")
        return 0
    if getattr(a, "disable", False):
        if os.path.exists(flag):
            os.remove(flag)
        print(f"preflight 자동주입 OFF(플래그 삭제). 수동 회상은 `{HINT} preflight --prompt ...` 로 가능.")
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
    # 작업A2(intel loop): 표시 번호(1-base) = 도장 staging idx — 중복 노드는 첫 번호 재사용.
    _stamp_ids = []

    def _stamp_no(n):
        nid = n.get("node_id")
        if not nid:
            return "-"
        if nid not in _stamp_ids:
            _stamp_ids.append(nid)
        return "%d." % (_stamp_ids.index(nid) + 1)

    if res["remember"]:
        print("\n## 기억할 것")
        for n in res["remember"]:
            sub = (" [%s]" % n["semantic_subtype"]) if n["semantic_subtype"] else ""
            print("  %s (%s%s) %s" % (_stamp_no(n), n["node_type"], sub, n["claim"]))
    if res["avoid_patterns"]:
        print("\n## 하면 안 되는 과거 패턴(버그패턴)")
        for m in res["avoid_patterns"]:
            print("  %s (위험도 %.2f) %s" % (_stamp_no(m), m["risk_score"], m["claim"]))
    if res["preferences"]:
        print("\n## 사용자 선호")
        for p in res["preferences"]:
            print("  %s %s" % (_stamp_no(p), p["claim"]))
    print("\n위험도: %s" % res["risk_level"])
    if res["needs_question"] and res["question"]:
        print("\n반문 ⚠ %s" % res["question"])
    elif res["risk_level"] == "중간":
        print("(주의: 과거 위험패턴과 일부 닮음 — 참고하세요)")
    if not (res["remember"] or res["avoid_patterns"] or res["preferences"]):
        print("(관련 기억 없음 — 새로운 작업이거나 그래프가 비어 있습니다)")
    # 작업A2(intel loop): 도장 소비용 staging(idx=위 번호·raw node_id 화면 미노출) + 푸터 1줄.
    #   query 는 --record 의 adoption_key 파생과 동일 원문(prompt or "preflight") — 교차 경로 멱등.
    if _stamp_ids and _stage_recall(ledger, _stamp_ids,
                                    getattr(a, "prompt", None) or "preflight",
                                    getattr(a, "domain", None), "cli_preflight"):
        print(f'\n  → 유용했으면 채팅 정확형 1줄 "히트 N"(아니면 "미스 N" · N=위 번호) 후: '
              f'{HINT} mark-hit --from-recall --index N')
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
        _reindex_after_write(a.ledger)   # use_count 변화 → fresh_index rank 반영
        print("\n(use_count 기록됨 %d건 · 유용성 신호 · 채택멱등 · 도장/문장 불변)" % len(seen))
    return 0
