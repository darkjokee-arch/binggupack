# -*- coding: utf-8 -*-
"""capture profile hook 죽은 경로 자가치유 회귀 — register_hook repair · hook_health.

전 테스트 tmp_path settings.json 격리 — 실 ~/.claude/settings.json 미접촉.
시나리오: pip uninstall/repo 이동 후 settings 에 죽은 .py 경로가 남으면
재설치 init(register_hook)이 skip 대신 command 를 교체(수리)해야 한다.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggu_capture_profile import (  # noqa: E402
    HOOK_MARKER, _hook_entry_dead, _hook_target_path,
    hook_health, hook_registered, register_hook)


def _cmd_for(path):
    return 'py "%s"' % path


def _make_hook_py(tmp_path, name="binggu_capture_hook.py"):
    d = tmp_path / "hooks"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text("# hook stub\n", encoding="utf-8")
    return f


def _load(settings):
    return json.loads(settings.read_text(encoding="utf-8"))


def _binggu_commands(data, ev):
    return [h["command"] for g in data["hooks"].get(ev, [])
            for h in g.get("hooks", []) if HOOK_MARKER in (h.get("command") or "")]


# ---------- _hook_target_path / _hook_entry_dead ----------

def test_hook_target_path_extracts_quoted_py(tmp_path):
    f = _make_hook_py(tmp_path)
    assert _hook_target_path(_cmd_for(f)) == str(f)
    # 따옴표 없는 형식·.py 아님·빈 문자열 → None (남의 hook 형식 오탐 0)
    assert _hook_target_path("python /x/binggu_capture_hook.py") is None
    assert _hook_target_path('node "existing-hook.js"') is None
    assert _hook_target_path("") is None
    assert _hook_target_path(None) is None


def test_hook_entry_dead_requires_marker_and_missing_file(tmp_path):
    f = _make_hook_py(tmp_path)
    alive = {"type": "command", "command": _cmd_for(f)}
    assert not _hook_entry_dead(alive)
    f.unlink()
    assert _hook_entry_dead(alive)  # marker + 경로 추출 + 파일 소실 → dead
    # marker 없으면 파일이 없어도 dead 아님
    assert not _hook_entry_dead({"command": 'py "%s"' % (tmp_path / "other.py")})
    # 경로 추출 불가(따옴표 없음)면 dead 아님 — false positive 방지
    assert not _hook_entry_dead({"command": "python missing_binggu_capture_hook.py"})


# ---------- ① 살아있는 경로: 재등록 skip ----------

def test_alive_hook_reregister_skips(tmp_path):
    f = _make_hook_py(tmp_path)
    settings = tmp_path / "settings.json"
    cmd = _cmd_for(f)
    added1 = register_hook(settings, cmd)
    assert added1 == ["UserPromptSubmit", "Stop"]
    added2 = register_hook(settings, cmd)
    assert added2 == []  # 살아있으면 그대로 skip(수리 표기도 없음)
    data = _load(settings)
    for ev in ("UserPromptSubmit", "Stop"):
        assert _binggu_commands(data, ev) == [cmd]


# ---------- ② 죽은 경로: repair — 새 경로만·중복 0·.bak 보존 ----------

def test_dead_hook_repaired_with_new_command(tmp_path):
    old = _make_hook_py(tmp_path, "binggu_capture_hook.py")
    settings = tmp_path / "settings.json"
    old_cmd = _cmd_for(old)
    register_hook(settings, old_cmd)
    bak = settings.with_name(settings.name + ".bak")
    assert not bak.exists()  # 최초 생성(settings 부재)엔 백업 없음 — 기존 동작
    pre_repair = settings.read_text(encoding="utf-8")

    old.unlink()  # pip uninstall/이동 시뮬레이션 — 죽은 경로 잔존
    new_dir = tmp_path / "reinstalled" / "hooks"
    new_dir.mkdir(parents=True)
    new = new_dir / "binggu_capture_hook.py"
    new.write_text("# hook stub v2\n", encoding="utf-8")
    new_cmd = _cmd_for(new)

    added = register_hook(settings, new_cmd)
    assert added == ["repaired:UserPromptSubmit", "repaired:Stop"]
    data = _load(settings)
    for ev in ("UserPromptSubmit", "Stop"):
        cmds = _binggu_commands(data, ev)
        assert cmds == [new_cmd]  # 새 경로만 · 중복 0 · 죽은 경로 잔존 0
    assert bak.read_text(encoding="utf-8") == pre_repair  # 수리 전 상태 .bak 백업(원복 지점)

    # 수리 후 재등록 → 다시 idempotent skip
    assert register_hook(settings, new_cmd) == []


# ---------- ③ hook_health dead 감지 ----------

def test_hook_health_detects_dead(tmp_path):
    f = _make_hook_py(tmp_path)
    settings = tmp_path / "settings.json"
    cmd = _cmd_for(f)

    h0 = hook_health(settings)
    assert h0 == {"registered": False, "dead_paths": [], "events": {}}

    register_hook(settings, cmd)
    h1 = hook_health(settings)
    assert h1["registered"] and h1["dead_paths"] == []
    assert h1["events"] == {"UserPromptSubmit": "ok", "Stop": "ok"}

    f.unlink()
    h2 = hook_health(settings)
    assert h2["registered"]  # 문자열 기준 등록은 유지(hook_registered 하위호환)
    assert h2["dead_paths"] == [str(f)]  # 이벤트 2곳이어도 경로 중복 0
    assert h2["events"] == {"UserPromptSubmit": "dead", "Stop": "dead"}
    assert hook_registered(settings)  # 기존 시그니처·의미 불변


# ---------- ④ binggu 외 다른 hook 무손상 ----------

def test_other_hooks_untouched_by_repair(tmp_path):
    f = _make_hook_py(tmp_path)
    settings = tmp_path / "settings.json"
    other = {"type": "command", "command": 'node "existing-hook.js"'}
    settings.write_text(json.dumps({
        "hooks": {"UserPromptSubmit": [{"hooks": [dict(other)]}]},
        "language": "korean",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    register_hook(settings, _cmd_for(f))
    f.unlink()
    new = _make_hook_py(tmp_path, "binggu_capture_hook2.py")
    added = register_hook(settings, _cmd_for(new))
    assert "repaired:UserPromptSubmit" in added and "repaired:Stop" in added

    data = _load(settings)
    ups = data["hooks"]["UserPromptSubmit"]
    kept = [h for g in ups for h in g.get("hooks", [])
            if "existing-hook.js" in (h.get("command") or "")]
    assert kept == [other]  # 타 hook entry 원형 그대로(existing-hook.js 실존 안 해도 미접촉)
    assert data["language"] == "korean"
    h = hook_health(settings)
    assert h["events"]["UserPromptSubmit"] == "ok"  # 같은 이벤트에 타 hook 있어도 binggu 기준 ok
