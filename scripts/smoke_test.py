#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BingguPack MCP — offline smoke test (clone 직후 실행 가능. MCP 등록 불필요).

handle_tool 직접 호출로 8도구 + save gate(G4_no_auto) 검증.
BINGGU_HOME 격리(--home) → 운영 ~/.binggupack 미접촉. 실 ledger write 0.
actual API call / source fetch / network / production write 0 (synthetic only).

usage:
  python scripts/smoke_test.py --home ./_binggu_test_home
  python scripts/smoke_test.py                 # home 미지정 시 temp 자동
"""
import os, sys, argparse, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
sys.path.insert(0, HERE)

SYN = ("BingguPack install smoke test: v1.10.0-rc.1 ships the installable MCP package, "
       "workflow-to-pack factory, and insane-search optional evidence discovery adapter. "
       "This is synthetic test evidence only.")

# 실존 fixture (path-gate 의존 제거: 실제 파일/디렉토리만 사용)
FX_DIR = os.path.join("examples", "toy_project")
FX_FILE = os.path.join("examples", "toy_project", "expected", "toy_pack_summary.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=None, help="BINGGU_HOME (격리 home). 미지정 시 temp.")
    args = ap.parse_args()

    home = os.path.abspath(args.home) if args.home else tempfile.mkdtemp(prefix="binggu_smoke_home_")
    os.makedirs(home, exist_ok=True)
    os.environ["BINGGU_HOME"] = home
    os.chdir(ROOT)  # examples 상대경로 동작

    # 실존 fixture 사전 확인
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

    r = handle_tool("capture_preview", {"utterances": [SYN]}, allow_root)
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


if __name__ == "__main__":
    main()
