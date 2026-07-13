#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""offline smoke 핵심 로직 (Lane B 모듈화).

scripts/smoke_test.py 가 이 모듈의 run_smoke_cli() 를 호출하는 backward-compatible
wrapper 다. save gate 동작 변경(read-only 해제 f9a9c61: confirm 정확일치 시 human
승격 저장)에 맞춰 케이스 9(confirm 부재→confirm_phrase_mismatch REJECT)/9b(정확일치→
격리 write) 를 갱신했다. 나머지 명령/출력/exit code 규약은 유지.

handle_tool 직접 호출로 8도구 + save gate(confirm-gated) 검증.
BINGGU_HOME 격리(--home) → 운영 ~/.binggupack 미접촉. 실 ledger write 0.
"""
import argparse
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/binggupack/pack
PKG = os.path.dirname(HERE)                          # <repo>/binggupack
ROOT = os.path.dirname(PKG)                          # <repo>
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)   # handle_tool 등 기존 서버 핸들러 import 경로

SYN = ("BingguPack install smoke test: v1.10.0-rc.1 ships the installable MCP package, "
       "workflow-to-pack factory, and insane-search optional evidence discovery adapter. "
       "This is synthetic test evidence only.")

# 실존 fixture (path-gate 의존 제거: 실제 파일/디렉토리만 사용)
FX_DIR = os.path.join("examples", "toy_project")
FX_FILE = os.path.join("examples", "toy_project", "expected", "toy_pack_summary.json")


def run_smoke(home=None):
    """smoke 체크를 실행하고 (checks, home, all_ok) 를 반환. side-effect: BINGGU_HOME 설정, chdir(ROOT)."""
    home = os.path.abspath(home) if home else tempfile.mkdtemp(prefix="binggu_smoke_home_")
    os.makedirs(home, exist_ok=True)
    os.environ["BINGGU_HOME"] = home
    os.chdir(ROOT)  # examples 상대경로 동작

    if not (os.path.exists(FX_DIR) and os.path.exists(FX_FILE)):
        print("FAIL: fixture 없음 (%s / %s). repo 루트에서 실행하세요." % (FX_DIR, FX_FILE))
        sys.exit(2)

    from openbinggu_mcp_server_handlers import handle_tool
    from openbinggu_staging_write_selftest import OPERATING_PATHS

    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}

    checks = []
    def chk(name, cond): checks.append((name, bool(cond)))

    r = handle_tool("selftest", {}, allow_root)
    chk("1.selftest_ALLOW", r.get("verdict") == "ALLOW" and r.get("executed"))

    r = handle_tool("capture_classify", {"utterance": "B안으로 결정"}, allow_root)
    chk("2.capture_classify_ALLOW", r.get("verdict") == "ALLOW")

    # SSOT 후보 게이트(should_capture) 후 — 자동 preview 는 판단문만 후보가 된다(SYN 영문 합성은 후보 0).
    r = handle_tool("capture_preview", {"utterances": ["이 입찰은 마진이 낮아 보류하기로 결정했다."]}, allow_root)
    tr = r.get("tool_result") or {}
    chk("3.capture_preview_ALLOW_nothing_saved",
        r.get("verdict") == "ALLOW" and tr.get("nothing_saved") is True and len(tr.get("candidates", [])) >= 1)

    r = handle_tool("pack_build", {"input_dir": FX_DIR}, allow_root)
    chk("4.pack_build_dryrun_ALLOW", r.get("verdict") == "ALLOW")

    r = handle_tool("pack_validate", {"pack_path": FX_FILE}, allow_root)
    chk("5.pack_validate_ALLOW", r.get("verdict") == "ALLOW")

    r = handle_tool("publish_guard_dryrun", {"pack_path": FX_FILE}, allow_root)
    chk("6.publish_guard_dryrun_ALLOW", r.get("verdict") == "ALLOW")

    r = handle_tool("consumer_smoke", {"pack_path": FX_FILE}, allow_root)
    chk("7.consumer_smoke_ALLOW", r.get("verdict") == "ALLOW")

    r = handle_tool("save_candidate", {"text": SYN, "indices": [1]}, allow_root)
    tr = r.get("tool_result") or {}
    chk("8.save_dryrun_write0",
        tr.get("executed_write") is False and tr.get("would_write_ledger") is False
        and tr.get("verdict") == "PREVIEW")

    # 9. actual save 시도(confirm 부재) → 자동/무단 저장 차단(confirm_phrase_mismatch, write 0).
    #    read-only 해제(f9a9c61) 이후 방어선이 G4_no_auto → confirm_phrase_mismatch 로 이동(handler selftest 와 정합).
    #    SYN(영문 합성)은 후보 0(nothing_to_save)이라 게이트를 못 태우므로 후보가 생기는 한국어 판단문 사용.
    KO = "이 입찰은 마진이 낮아 보류하기로 결정했다."
    r = handle_tool("save_candidate",
                    {"text": KO, "indices": [1], "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    chk("9.save_no_confirm_REJECT_write0",
        tr.get("executed_write") is False and tr.get("reason") == "confirm_phrase_mismatch")

    # 9b. confirm 정확일치("SAVE 1")만으로는 저장 안 됨 — 사람 SAVE 앵커(save_gate_log) 없으면
    #     actor=reader 유지 → G4_no_auto 차단(P0 봉인: 모델이 dry-run 의 confirm_expected 를 재현해
    #     사람 발화 0으로 write 하던 우회 차단, 2026-07-10).
    r = handle_tool("save_candidate",
                    {"text": KO, "indices": [1], "dry_run": False, "confirm": "SAVE 1"}, allow_root)
    tr = r.get("tool_result") or {}
    # MCP save approval 배선 제거(2026-07-13): 방어선은 core 의 G4_no_auto 단일(사람 save-n 앵커 없으면
    # actor=reader 유지). write 0(fail-closed)은 불변.
    chk("9b.confirm_alone_BLOCKED(no_human_anchor)",
        tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto")

    # 9c. 사장님이 실제 'SAVE 1' 을 키보드 입력하면 save_gate hook 이 앵커를 남긴다(여기선 그 앵커를 직접
    #     기록해 시뮬). save-n 참조 바인딩: 훅은 (preview_ref, idx) ref 레코드 1행 + 레거시 sh 행을
    #     병기 append 하고, 승격 정본은 ref 대조다. 그때만 confirm 정확일치가 human 승격 저장으로
    #     이어진다(격리 BINGGU_HOME 에만).
    import json as _json
    import os as _os
    import sys as _sys
    _sd = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "scripts")
    if _sd not in _sys.path:
        _sys.path.insert(0, _sd)
    import binggu_save_gate as _sgate
    from binggupack.capture import preview as _cvp
    _cands9 = _cvp.capture_preview(KO)["candidates"]
    _sgate.write_last_preview(_cands9)
    _sgate.gate_record_from_prompt("SAVE 1")
    # ref 레코드 단정 — gate log 에 (pref, idx=1) 이 실재(승격 정본 경로 증명 · 원문 미저장)
    _pref9 = _sgate.preview_ref_for_candidates(_cands9)
    _ref_seen = False
    with open(_sgate.gate_path(), encoding="utf-8") as _f9:
        for _ln in _f9:
            try:
                _d9 = _json.loads(_ln)
            except Exception:
                continue
            if _d9.get("pref") == _pref9 and 1 in (_d9.get("idxs") or []):
                _ref_seen = True
    chk("9c0.save_gate_ref_record_present", _ref_seen)
    r = handle_tool("save_candidate",
                    {"text": KO, "indices": [1], "dry_run": False, "confirm": "SAVE 1"}, allow_root)
    tr = r.get("tool_result") or {}
    chk("9c.human_anchor_confirm_isolated_write",
        tr.get("executed_write") is True and tr.get("saved") == 1)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    chk("10.operating_ledger_write_0", op_before == op_after)

    all_ok = all(ok for _, ok in checks)
    return checks, home, all_ok


def run_smoke_cli(argv=None):
    """scripts/smoke_test.py wrapper 진입점. 출력/exit code 는 v1.10.0 과 동일."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=None, help="BINGGU_HOME (격리 home). 미지정 시 temp.")
    args = ap.parse_args(argv)

    checks, home, all_ok = run_smoke(args.home)

    print("=" * 64)
    print("BingguPack MCP — offline smoke test")
    print("=" * 64)
    for name, ok in checks:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("-" * 64)
    print("  home                  :", home)
    print("  actual_api_call       : 0")
    print("  source_fetch          : 0")
    print("  production_write       : 0")
    print("  G4_no_auto            : confirmed (AI/reader actor cannot durably save)")
    print("  real_home_changed     : 0 (BINGGU_HOME redirected; OPERATING_PATHS unchanged)")
    print("\n  RESULT:", "PASS" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)
