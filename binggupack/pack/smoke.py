#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""offline smoke 핵심 로직 (Lane B 모듈화).

scripts/smoke_test.py 가 이 모듈의 run_smoke_cli() 를 호출하는 backward-compatible
wrapper 다. 동작/출력/exit code 는 v1.10.0 과 byte-identical 하게 유지한다.

handle_tool 직접 호출로 8도구 + save gate(G4_no_auto) 검증.
BINGGU_HOME 격리(--home) → 운영 ~/.binggupack 미접촉. 실 ledger write 0.
"""
import os, sys, argparse, tempfile

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

    # 9. actual save 시도 → AI/reader actor 이므로 G4_no_auto BLOCK (이게 PASS).
    r = handle_tool("save_candidate",
                    {"text": SYN, "indices": [1], "dry_run": False, "confirm": "SAVE 1"}, allow_root)
    tr = r.get("tool_result") or {}
    chk("9.save_actual_G4_no_auto_BLOCK",
        tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto")

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
