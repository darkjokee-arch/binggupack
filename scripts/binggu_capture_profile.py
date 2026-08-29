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
from contextlib import suppress
import json
import re
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
        "disabled": h / "capture_disabled",  # owner sticky OFF 정책 마커(있으면 init 재실행도 안 켬)
    }


def _group_has_marker(group, marker):
    return any(marker in (h.get("command") or "") for h in group.get("hooks", []))


_HOOK_PATH_RE = re.compile(r'"([^"]+\.py)"')


def _hook_target_path(command):
    """command 문자열에서 따옴표로 감싼 첫 .py 경로 추출('py "<path>"' 형식 — binggu.py _hook_command).
    추출 실패 시 None (남의 hook 형식은 건드리지 않기 위한 보수적 파싱)."""
    m = _HOOK_PATH_RE.search(command or "")
    return m.group(1) if m else None


def _hook_entry_dead(entry, marker=HOOK_MARKER):
    """binggu entry 의 대상 .py 파일이 사라졌으면 True(죽은 경로 — pip uninstall/repo 이동 후 잔존).
    marker 없거나 경로 추출 불가면 False — false positive 로 남의 hook 형식을 깨지 않는다."""
    cmd = entry.get("command") or ""
    if marker not in cmd:
        return False
    path = _hook_target_path(cmd)
    if path is None:
        return False
    return not Path(path).exists()


def register_hook(settings_path, command, events=("UserPromptSubmit", "Stop"), marker=HOOK_MARKER, is_async=True):
    """settings.json 의 각 이벤트에 binggu hook 그룹 추가(백업·idempotent).
    이미 marker 가 있으면 skip — 단 그 entry 의 대상 파일이 사라진 죽은 경로면 command 를
    새 것으로 교체(자가치유)하고 "repaired:<이벤트>" 로 표기. 반환 = 새로 추가/수리된 이벤트 목록
    (신규 등록은 "<이벤트>" 그대로 — 기존 호출부 하위호환).
    is_async=False 면 async 키 생략(sync) — save_gate 처럼 저장 전 완료 보장 필요한 hook 용(레이스 회피)."""
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
        marker_groups = [g for g in groups if _group_has_marker(g, marker)]
        if marker_groups:
            # 이미 등록됨 — 죽은 경로만 새 command 로 교체(중복 그룹 생성 0)
            repaired = False
            for g in marker_groups:
                for h in g.get("hooks", []):
                    if _hook_entry_dead(h, marker):
                        h["command"] = command
                        repaired = True
            if repaired:
                added.append("repaired:%s" % ev)
            continue  # idempotent
        entry = {"type": "command", "command": command}
        if is_async:
            entry["async"] = True
        groups.append({"hooks": [entry]})
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


def hook_health(settings_path, marker=HOOK_MARKER):
    """hook 등록 + 대상 파일 실존 여부 진단.
    반환 {"registered": bool, "dead_paths": [죽은 .py 경로들],
          "events": {이벤트: "ok"(살아있음) | "dead"(파일 소실) | "missing"(marker 없음)}}.
    settings 파일이 없거나 파싱 실패면 전부 빈/False."""
    result = {"registered": False, "dead_paths": [], "events": {}}
    sp = Path(settings_path)
    if not sp.exists():
        return result
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return result
    for ev, groups in data.get("hooks", {}).items():
        state = "missing"
        for g in groups:
            for h in g.get("hooks", []):
                cmd = h.get("command") or ""
                if marker not in cmd:
                    continue
                result["registered"] = True
                if _hook_entry_dead(h, marker):
                    state = "dead"
                    path = _hook_target_path(cmd)
                    if path and path not in result["dead_paths"]:
                        result["dead_paths"].append(path)
                elif state != "dead":
                    state = "ok"
        result["events"][ev] = state
    return result


def hook_registered(settings_path, marker=HOOK_MARKER):
    return hook_health(settings_path, marker)["registered"]


def init_profile(home, cwd, hook_command=None, settings_path=None, global_scope=False, force_enable=False):
    """capture profile 생성: 플래그 ON + scope(현재 cwd 또는 global) + (선택) settings hook 등록.

    owner sticky OFF: `capture_disabled` 마커가 있으면 init 재실행이 정책(자동수집 영구 OFF)을
    깨지 않는다(enabled 미생성·OFF 유지). 신규 유저(마커 없음)는 기본 ON 그대로.
    force_enable=True(또는 `capture enable`)면 마커를 지우고 ON 으로 강제 전환한다.
    반환 dict 의 'enabled' = 이번 init 으로 capture 가 켜졌는지."""
    p = profile_paths(home)
    p["home"].mkdir(parents=True, exist_ok=True)
    if force_enable and p["disabled"].exists():
        p["disabled"].unlink()  # 명시 강제 ON → sticky OFF 해제
    if p["disabled"].exists():
        capture_on = False  # owner 가 끈 상태 — init 이 정책을 깨지 않음
    else:
        p["enabled"].write_text("1", encoding="utf-8")
        if p["paused"].exists():
            p["paused"].unlink()  # init = 활성 상태
        capture_on = True
    # ★멱등: 기존 scope 가 있으면 덮어쓰지 않고 병합한다(7/9 회귀 방지 — init 무조건 덮어쓰기가
    #   owner 커스텀 allow[system32]·deny 를 날려 capture 전멸시킨 사고). 병합 전 .json.bak 백업.
    existing_scope = None
    if p["scope"].exists():
        try:
            _es = json.loads(p["scope"].read_text(encoding="utf-8"))
            existing_scope = _es if isinstance(_es, dict) else None
        except Exception:
            existing_scope = None
    if existing_scope is not None:
        with suppress(Exception):  # 병합 전 기존 scope 백업(원복 지점 · 1회)
            bak = p["scope"].with_suffix(".json.bak")
            if not bak.exists():
                bak.write_text(json.dumps(existing_scope, ensure_ascii=False, indent=2), encoding="utf-8")
        prefixes = [str(x) for x in existing_scope.get("allowed_cwd_prefixes", [])]
        cwd_prefix = str(Path(cwd).resolve())
        if not global_scope and cwd_prefix not in prefixes:
            prefixes.append(cwd_prefix)  # 현재 cwd 추가(기존 allow 소실 0)
        scope = {
            "global": bool(existing_scope.get("global", False)) or bool(global_scope),
            "allowed_cwd_prefixes": prefixes,
            "denied_cwd_substrings": [str(x) for x in existing_scope.get("denied_cwd_substrings", [])],
        }
    elif global_scope:
        scope = {"global": True, "allowed_cwd_prefixes": [], "denied_cwd_substrings": []}
    else:
        scope = {"global": False,
                 "allowed_cwd_prefixes": [str(Path(cwd).resolve())],
                 "denied_cwd_substrings": []}
    p["scope"].write_text(json.dumps(scope, ensure_ascii=False, indent=2), encoding="utf-8")
    added = register_hook(settings_path, hook_command) if (settings_path and hook_command) else []
    return {"scope": scope, "hook_events": added, "global": global_scope, "enabled": capture_on}


def disable_capture(home):
    """owner sticky OFF: enabled 플래그 제거 + capture_disabled 마커 생성.
    pause(일시정지)와 달리 init 재실행에도 OFF 가 유지된다(정책 영구 OFF)."""
    p = profile_paths(home)
    p["home"].mkdir(parents=True, exist_ok=True)
    if p["enabled"].exists():
        p["enabled"].unlink()
    p["disabled"].write_text("1", encoding="utf-8")


def enable_capture(home):
    """sticky OFF 해제 + enabled ON. owner 가 명시적으로 다시 켤 때만."""
    p = profile_paths(home)
    p["home"].mkdir(parents=True, exist_ok=True)
    if p["disabled"].exists():
        p["disabled"].unlink()
    if p["paused"].exists():
        p["paused"].unlink()
    p["enabled"].write_text("1", encoding="utf-8")


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
    for k in ("enabled", "paused", "scope", "buffer", "preview", "disabled"):
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
        "disabled": profile_paths(home)["disabled"].exists(),  # owner sticky OFF 정책 마커
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
        init_profile(home, cwd, hook_command=cmd, settings_path=settings)
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
        uninstall(home, settings_path=settings)
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

        # T10 owner sticky OFF — disable 후 재init 해도 OFF 유지(정책 영구 OFF 보존)
        init_profile(home, cwd, hook_command=cmd, settings_path=settings)
        check(status(home, cwd, settings)["enabled"], "T10a 재설치 init → ON")
        disable_capture(home)
        st_d = status(home, cwd, settings)
        check(not st_d["enabled"] and st_d["disabled"], "T10b capture disable → OFF · sticky 마커 생성")
        r_re = init_profile(home, cwd, hook_command=cmd, settings_path=settings)
        check(not status(home, cwd, settings)["enabled"] and not r_re["enabled"],
              "T10c sticky OFF 중 재init → OFF 유지(init 이 정책을 안 깸)")

        # T11 명시 enable / --force-capture → sticky 해제 후 ON
        enable_capture(home)
        st_e = status(home, cwd, settings)
        check(st_e["enabled"] and not st_e["disabled"], "T11a capture enable → ON · 마커 제거")
        disable_capture(home)
        r_f = init_profile(home, cwd, hook_command=cmd, settings_path=settings, force_enable=True)
        check(status(home, cwd, settings)["enabled"] and r_f["enabled"],
              "T11b init --force-capture → sticky 해제 ON")

        # T12 init 멱등 병합: 기존 커스텀 scope(추가 allow[system32]·deny)가 재init 에 보존(7/9 회귀 방지)
        pscope = profile_paths(home)["scope"]
        bakp = pscope.with_suffix(".json.bak")
        if bakp.exists():
            bakp.unlink()  # 이전 백업 정리(격리)
        custom = {"global": False,
                  "allowed_cwd_prefixes": [str(Path(cwd).resolve()), "C:/WINDOWS/system32"],
                  "denied_cwd_substrings": ["example-project", "example-org"]}
        pscope.write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")
        init_profile(home, cwd, hook_command=cmd, settings_path=settings)  # 재init
        merged = json.loads(pscope.read_text(encoding="utf-8"))
        check("C:/WINDOWS/system32" in merged["allowed_cwd_prefixes"]
              and merged["denied_cwd_substrings"] == ["example-project", "example-org"],
              "T12 재init 멱등 병합: 커스텀 allow(system32)·deny 보존(7/9 회귀 방지)")
        check(bakp.exists(), "T12b 병합 전 기존 scope .json.bak 백업 생성")

        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
