"""BingguPack AGI memory capture profile — init / status / pause / resume / uninstall.

`binggu init` 이 호출하는 capture profile 관리 모듈.
profile = capture_enabled 플래그 + capture_scope.json + (선택) settings.json hook 등록.

핵심 원칙:
  - clone 직후(profile 미생성)에는 아무 수집도 없음 — init 을 실행해야 capture ON
  - init profile 의 scope 기본값 = **현재 cwd(repo/workspace) 1개**. 전역은 global_scope=True 명시해야만
  - 자동 저장 없음: hook 은 candidate 만 수집, 저장은 preview → SAVE n 게이트(save_selected)만
  - settings.json 편집은 백업 후 idempotent — 기존 hook 보존, binggu hook 만 add/remove
  - pause/resume/uninstall(rollback) 전부 제공

settings.json 실편집은 호출자(binggu.py)가 실제 경로를 줄 때만. 셀프테스트는 temp 경로 전용.
"""
import json
from pathlib import Path

from binggu_capture_persist import CaptureScope, PersistentCaptureBuffer, binggu_home

HOOK_MARKER = "binggu_capture_hook"  # settings.json command 식별 토큰


def profile_paths(home=None):
    h = binggu_home(home)
    return {
        "home": h,
        "enabled": h / "capture_enabled",
        "paused": h / "capture_paused",
        "scope": h / "capture_scope.json",
        "buffer": h / "capture_buffer.sqlite",
        "preview": h / "capture_last_preview.json",
    }


def _group_has_marker(group, marker):
    return any(marker in (h.get("command") or "") for h in group.get("hooks", []))


def register_hook(settings_path, command, events=("UserPromptSubmit", "Stop"), marker=HOOK_MARKER):
    """settings.json 의 각 이벤트에 binggu capture hook 그룹 추가(백업·idempotent).
    이미 marker 가 있으면 skip. 반환 = 새로 추가된 이벤트 목록."""
    sp = Path(settings_path)
    data = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    if sp.exists():
        bak = sp.with_name(sp.name + ".bak")
        if not bak.exists():
            bak.write_text(sp.read_text(encoding="utf-8"), encoding="utf-8")
    hooks = data.setdefault("hooks", {})
    added = []
    for ev in events:
        groups = hooks.setdefault(ev, [])
        if any(_group_has_marker(g, marker) for g in groups):
            continue  # idempotent
        groups.append({"hooks": [{"type": "command", "command": command, "async": True}]})
        added.append(ev)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return added


def unregister_hook(settings_path, marker=HOOK_MARKER):
    """marker 든 hook 항목만 제거(다른 hook 보존). 빈 그룹은 정리. 반환 = 제거된 이벤트 목록."""
    sp = Path(settings_path)
    if not sp.exists():
        return []
    data = json.loads(sp.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})
    removed = []
    for ev in list(hooks.keys()):
        new_groups = []
        touched = False
        for g in hooks[ev]:
            inner = g.get("hooks", [])
            kept = [h for h in inner if marker not in (h.get("command") or "")]
            if len(kept) != len(inner):
                touched = True
            if kept:
                g["hooks"] = kept
                new_groups.append(g)
            elif not inner:
                new_groups.append(g)  # 원래 빈 그룹은 보존
            # marker 로만 채워졌던 그룹은 drop
        if touched:
            removed.append(ev)
        if new_groups:
            hooks[ev] = new_groups
        else:
            hooks.pop(ev, None)
    sp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def hook_registered(settings_path, marker=HOOK_MARKER):
    sp = Path(settings_path)
    if not sp.exists():
        return False
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return False
    for groups in data.get("hooks", {}).values():
        if any(_group_has_marker(g, marker) for g in groups):
            return True
    return False


def init_profile(home, cwd, hook_command=None, settings_path=None, global_scope=False):
    """capture profile 생성: 플래그 ON + scope(현재 cwd 또는 global) + (선택) settings hook 등록."""
    p = profile_paths(home)
    p["home"].mkdir(parents=True, exist_ok=True)
    p["enabled"].write_text("1", encoding="utf-8")
    if p["paused"].exists():
        p["paused"].unlink()  # init = 활성 상태
    if global_scope:
        scope = {"global": True, "allowed_cwd_prefixes": [], "denied_cwd_substrings": []}
    else:
        scope = {"global": False,
                 "allowed_cwd_prefixes": [str(Path(cwd).resolve())],
                 "denied_cwd_substrings": []}
    p["scope"].write_text(json.dumps(scope, ensure_ascii=False, indent=2), encoding="utf-8")
    added = register_hook(settings_path, hook_command) if (settings_path and hook_command) else []
    return {"scope": scope, "hook_events": added, "global": global_scope}


def pause(home):
    profile_paths(home)["paused"].write_text("1", encoding="utf-8")


def resume(home):
    p = profile_paths(home)["paused"]
    if p.exists():
        p.unlink()


def uninstall(home, settings_path=None, marker=HOOK_MARKER):
    """완전 rollback: profile 파일 전부 삭제 + settings hook 제거. ledger.sqlite 는 미접촉."""
    p = profile_paths(home)
    removed = []
    for k in ("enabled", "paused", "scope", "buffer", "preview"):
        if p[k].exists():
            p[k].unlink()
            removed.append(k)
    ev = unregister_hook(settings_path, marker) if settings_path else []
    return {"removed_files": removed, "hook_removed_events": ev}


def status(home, cwd, settings_path=None):
    sc = CaptureScope(home=home)
    buf = PersistentCaptureBuffer(home=home)
    return {
        "enabled": sc.enabled(),
        "paused": sc.paused(),
        "global": sc._scope()["global"],
        "in_current_scope": sc.in_scope(cwd),
        "buffer_count": buf.size,
        "hook_registered": hook_registered(settings_path) if settings_path else None,
    }


# ---------------- 셀프테스트 (temp home + temp settings.json, 실 settings 미접촉) ----------------
def _selftest():
    import shutil
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    tmp = Path(tempfile.mkdtemp(prefix="bgp_capture_profile_"))
    try:
        home = tmp / ".binggupack"
        settings = tmp / "settings.json"
        cwd = str(tmp / "myrepo")
        (tmp / "myrepo").mkdir()
        cmd = 'python /path/to/binggupack/hooks/binggu_capture_hook.py'

        # 기존 settings(다른 hook 보존 검증용)
        settings.write_text(json.dumps({
            "hooks": {"UserPromptSubmit": [{"hooks": [
                {"type": "command", "command": "node existing-hook.js"}]}]},
            "language": "korean",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        # T1 clone 직후(init 전) → 수집 OFF
        st0 = status(home, cwd, settings)
        check(not st0["enabled"] and st0["buffer_count"] == 0 and not st0["hook_registered"],
              "T1 init 전 → enabled False · hook 미등록")

        # T2 init → 플래그·scope(현재 cwd)·hook 등록
        r = init_profile(home, cwd, hook_command=cmd, settings_path=settings)
        st1 = status(home, cwd, settings)
        check(st1["enabled"] and st1["in_current_scope"] and st1["hook_registered"]
              and not st1["global"], "T2 init → enabled · 현재 scope ON · hook 등록 · global False")

        # T3 기존 hook 보존 + binggu hook 추가
        data = json.loads(settings.read_text(encoding="utf-8"))
        ups = data["hooks"]["UserPromptSubmit"]
        has_existing = any("existing-hook.js" in h.get("command", "")
                           for g in ups for h in g.get("hooks", []))
        has_binggu = any(HOOK_MARKER in h.get("command", "")
                         for g in ups for h in g.get("hooks", []))
        check(has_existing and has_binggu and "Stop" in data["hooks"] and data["language"] == "korean",
              "T3 기존 hook·설정 보존 + binggu hook UserPromptSubmit/Stop 등록")

        # T4 백업 생성
        check((settings.with_name(settings.name + ".bak")).exists(), "T4 settings.json.bak 백업 생성")

        # T5 idempotent: 재init → 중복 등록 0
        init_profile(home, cwd, hook_command=cmd, settings_path=settings)
        data2 = json.loads(settings.read_text(encoding="utf-8"))
        n_binggu = sum(1 for g in data2["hooks"]["UserPromptSubmit"]
                       for h in g.get("hooks", []) if HOOK_MARKER in h.get("command", ""))
        check(n_binggu == 1, "T5 재init idempotent(UserPromptSubmit binggu hook 1개)")

        # T6 pause → 수집 OFF / resume → ON
        pause(home)
        check(not status(home, cwd, settings)["enabled"] and status(home, cwd, settings)["paused"],
              "T6 pause → enabled False")
        resume(home)
        check(status(home, cwd, settings)["enabled"], "T6b resume → enabled True")

        # T7 global init → scope.global True (타 cwd 도 in_scope)
        init_profile(home, cwd, hook_command=cmd, settings_path=settings, global_scope=True)
        other = "D:/some/other/workspace"
        check(status(home, other, settings)["global"] and status(home, other, settings)["in_current_scope"],
              "T7 --global → 전역 scope(타 cwd in_scope)")

        # T8 uninstall → profile 삭제 + hook 제거 + 기존 hook 보존
        # 먼저 버퍼 1건 생성(삭제 확인용)
        buf = PersistentCaptureBuffer(home=home)
        buf.feed("이거 저장해", cwd)  # global ON 이라 수집됨
        had_buffer = buf.size >= 1
        res = uninstall(home, settings_path=settings)
        st2 = status(home, cwd, settings)
        data3 = json.loads(settings.read_text(encoding="utf-8"))
        existing_kept = any("existing-hook.js" in h.get("command", "")
                            for g in data3.get("hooks", {}).get("UserPromptSubmit", [])
                            for h in g.get("hooks", []))
        binggu_gone = not hook_registered(settings)
        check(had_buffer and not st2["enabled"] and st2["buffer_count"] == 0
              and binggu_gone and existing_kept,
              "T8 uninstall → profile/buffer 삭제 · binggu hook 제거 · 기존 hook 보존")

        # T9 ledger.sqlite 미접촉(애초에 생성 0)
        check(not (home / "ledger.sqlite").exists(), "T9 ledger.sqlite 미생성(write 0)")

        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
