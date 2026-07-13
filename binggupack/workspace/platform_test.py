# -*- coding: utf-8 -*-
"""binggupack.workspace.platform.invocation_prefix 단위테스트.

계약:
  - 설치본(argv[0] basename 이 ".py" 로 안 끝남) → "binggu"
  - 소스(argv[0] basename 이 ".py" 로 끝남)     → "python binggu.py"
  - argv[0] 빈문자열/판정불가                    → 소스 폴백("python binggu.py")
  - raise 절대 없음
"""
import binggupack.workspace.platform as plat
from binggupack.workspace.platform import invocation_prefix


def test_installed_console_script(monkeypatch):
    monkeypatch.setattr(plat.sys, "argv", ["binggu"])
    assert invocation_prefix() == "binggu"


def test_installed_with_path(monkeypatch):
    monkeypatch.setattr(plat.sys, "argv", ["/usr/local/bin/binggu"])
    assert invocation_prefix() == "binggu"


def test_source_script_py(monkeypatch):
    monkeypatch.setattr(plat.sys, "argv", ["/x/binggu.py"])
    assert invocation_prefix() == "python binggu.py"


def test_source_script_bare_py(monkeypatch):
    monkeypatch.setattr(plat.sys, "argv", ["binggu.py"])
    assert invocation_prefix() == "python binggu.py"


def test_source_uppercase_py_extension(monkeypatch):
    monkeypatch.setattr(plat.sys, "argv", ["/x/BINGGU.PY"])
    assert invocation_prefix() == "python binggu.py"


def test_empty_argv0_falls_back_to_source(monkeypatch):
    monkeypatch.setattr(plat.sys, "argv", [""])
    assert invocation_prefix() == "python binggu.py"


def test_missing_argv_falls_back_to_source(monkeypatch):
    monkeypatch.setattr(plat.sys, "argv", [])
    assert invocation_prefix() == "python binggu.py"


def test_explicit_argv0_argument_overrides_sys_argv(monkeypatch):
    monkeypatch.setattr(plat.sys, "argv", ["binggu"])
    assert invocation_prefix("binggu.py") == "python binggu.py"
    assert invocation_prefix("binggu") == "binggu"


def test_never_raises_on_odd_input():
    # 어떤 입력에도 raise 없이 계약된 두 문자열 중 하나만 반환.
    assert invocation_prefix("") == "python binggu.py"
    for val in ("/", "\\", "binggu", "x.py"):
        assert invocation_prefix(val) in ("binggu", "python binggu.py")
