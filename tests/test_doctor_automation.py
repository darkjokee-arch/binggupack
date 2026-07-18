# -*- coding: utf-8 -*-
"""추천④ MF10 보강 — doctor automation 플래그가 read-only 로 실상태를 비추고 줄이 파싱된다.

검증 대상:
  - doctor._automation_flags(home): capture/preflight/recall_trace/crab_sync 를 파일플래그·config
    로만 읽는다(파일/DB write 0). capture 는 paused/disabled 가 우선(daily._capture_status 규약).
  - run_doctor 가 내는 `[INFO] automation_flags  automation: capture=.. preflight=.. trace=.. crab_sync=..`
    한 줄이 기계 파싱 가능하고 실플래그를 그대로 반영.

owner 원칙: 이 스위치는 owner 만 켠다 — doctor 는 거울일 뿐(변경 0). automation 라인은 INFO(게이트 무영향).
run_doctor 의 무거운 하위 selftest 6개는 테스트-측 monkeypatch(_CHECKS=[])로 스킵 — 우리가 검증하는
automation-라인 코드경로만 결정적으로 실행(원본 doctor.py 는 미변경).
"""
import json
import os
import re
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO, os.path.join(REPO, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack import doctor           # noqa: E402
import binggu_schema                          # noqa: E402

_LINE_RE = re.compile(
    r"automation_flags\s+automation: capture=(ON|OFF) preflight=(ON|OFF) "
    r"trace=(ON|OFF) crab_sync=(ON|OFF)")


def _snapshot_dir(home):
    """{name: mtime_ns} — _automation_flags 가 파일을 만들거나 건드리지 않았음을 확인용."""
    return {n: os.stat(os.path.join(home, n)).st_mtime_ns for n in os.listdir(home)}


def test_automation_flags_all_off_by_default(tmp_path):
    """빈 home → 4 플래그 전부 OFF(기본 보수)."""
    home = str(tmp_path)
    af = doctor._automation_flags(home)
    assert af == {"capture": False, "preflight": False, "recall_trace": False, "crab_sync": False}


def test_automation_flags_each_on(tmp_path):
    """각 플래그 원천(파일플래그 · config)이 켜지면 True."""
    home = str(tmp_path)
    (tmp_path / "capture_enabled").write_text("1", encoding="utf-8")
    (tmp_path / "preflight_enabled").write_text("1", encoding="utf-8")
    (tmp_path / "recall_trace_enabled").write_text("1", encoding="utf-8")
    (tmp_path / "person_pack.json").write_text(json.dumps({"crab_auto_sync": True}), encoding="utf-8")
    af = doctor._automation_flags(home)
    assert af == {"capture": True, "preflight": True, "recall_trace": True, "crab_sync": True}


def test_automation_flags_capture_paused_or_disabled_overrides(tmp_path):
    """capture 는 enabled 여도 paused/disabled 마커가 있으면 OFF(daily._capture_status 규약)."""
    home = str(tmp_path)
    (tmp_path / "capture_enabled").write_text("1", encoding="utf-8")
    assert doctor._automation_flags(home)["capture"] is True

    (tmp_path / "capture_paused").write_text("1", encoding="utf-8")
    assert doctor._automation_flags(home)["capture"] is False

    os.remove(str(tmp_path / "capture_paused"))
    (tmp_path / "capture_disabled").write_text("1", encoding="utf-8")
    assert doctor._automation_flags(home)["capture"] is False


def test_automation_flags_crab_sync_config_and_corruption_graceful(tmp_path):
    """crab_sync 는 person_pack.json 의 config 값 — false/누락/손상 전부 graceful OFF."""
    home = str(tmp_path)
    pp = tmp_path / "person_pack.json"

    pp.write_text(json.dumps({"crab_auto_sync": False}), encoding="utf-8")
    assert doctor._automation_flags(home)["crab_sync"] is False

    pp.write_text(json.dumps({"other": 1}), encoding="utf-8")     # 키 누락
    assert doctor._automation_flags(home)["crab_sync"] is False

    pp.write_text("{ this is not json", encoding="utf-8")          # 손상
    assert doctor._automation_flags(home)["crab_sync"] is False


def test_automation_flags_is_read_only(tmp_path):
    """_automation_flags 는 파일/DB write 0 — 반복 호출해도 디렉터리 스냅샷 불변, 새 파일 0."""
    home = str(tmp_path)
    (tmp_path / "capture_enabled").write_text("1", encoding="utf-8")
    (tmp_path / "person_pack.json").write_text(json.dumps({"crab_auto_sync": True}), encoding="utf-8")

    before = _snapshot_dir(home)
    for _ in range(5):
        doctor._automation_flags(home)
    assert _snapshot_dir(home) == before                # mtime 불변 + 새 파일 0


def test_doctor_emits_parseable_automation_line(tmp_path, monkeypatch, capsys):
    """run_doctor 가 실플래그(capture ON·preflight OFF·trace ON·crab ON)를 반영한 파싱 가능한 1줄을 출력."""
    monkeypatch.setattr(doctor, "_CHECKS", [])          # 무거운 하위 selftest 6개 스킵(라인 코드만)
    home = tmp_path
    (home / "capture_enabled").write_text("1", encoding="utf-8")
    (home / "recall_trace_enabled").write_text("1", encoding="utf-8")
    (home / "person_pack.json").write_text(json.dumps({"crab_auto_sync": True}), encoding="utf-8")
    # preflight_enabled 는 일부러 안 만든다 → OFF 로 나와야
    ledger = home / "ledger.sqlite"
    con = sqlite3.connect(str(ledger))
    binggu_schema.apply_schema(con)
    con.close()

    doctor.run_doctor(ledger_path=str(ledger))          # tree_root 없음 → 트리 스캔 skip
    out = capsys.readouterr().out

    m = _LINE_RE.search(out)
    assert m, "automation_flags 라인을 찾지 못함:\n%s" % out[-1500:]
    assert m.groups() == ("ON", "OFF", "ON", "ON")      # capture·preflight·trace·crab_sync
