#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""로컬 CI 프리플라이트 — .github/workflows 의 모든 게이트를 커밋 전에 로컬에서 그대로 재현한다.

동기(P1-A.1 §7): run_all 만 돌려선 CI 전용 스텝(version SSOT·ruff·platform·doctor·autopush·
setup_cloud·e2e·demo·tree·MCP install smoke)을 놓쳐 CI red 를 뒤늦게 만난다. 이 래퍼는 새 프레임워크가
아니라 **기존 스텝을 순서대로 호출**하는 얇은 러너다(summary-fail — 전부 끝까지 → 하나라도 실패면 exit 1).

커버:
  · ci.yml(selftest job): version SSOT · ruff F · platform · binggu --selftest · doctor · autopush ·
    setup_cloud · run_all 회귀 · e2e lifecycle · pytest(전체 tests/ · CI 는 demo 만이나 여기선 상위집합) · tree scan
  · mcp-cross-platform-install.yml: smoke_test(8도구+게이트) · installer dry-run · 운영명 거부

전부 temp BINGGU_HOME 격리 · 운영 ~/.binggupack 미접촉. CLI: python scripts/ci_local_preflight.py
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TMP_HOME = tempfile.mkdtemp(prefix="bgp_preflight_home_")

# (label, argv, verdict)
#   verdict:
#     "rc0"           → exit 0 이어야 통과
#     "rc_nonzero"    → exit != 0 이어야 통과(운영명 거부 등 '실패가 정답'인 스텝)
#     "contains:<str>" → 출력에 <str> 포함 + 'verdict=BLOCK' 부재(tree scan)
#       (prefix 는 'token:' 을 피한다 — 이 문자열이 public_tree_scan 의 secret_kv 규칙에 자가 오탐됨)
STEPS = [
    ("version SSOT",      [PY, "scripts/version_consistency_selftest.py"], "rc0"),
    ("ruff F-gate",       [PY, "-m", "ruff", "check", "binggupack/", "scripts/", "--select", "F"], "rc0"),
    ("anywhere vendor sync", [PY, "scripts/sync_anywhere_vendor.py", "--check"], "rc0"),
    ("platform policy",   [PY, "scripts/binggu_platform_selftest.py"], "rc0"),
    ("binggu --selftest", [PY, "binggu.py", "--selftest"], "rc0"),
    ("doctor",            [PY, "scripts/openbinggu_doctor.py", "--selftest"], "rc0"),
    ("autopush",          [PY, "scripts/binggu_publish_autopush.py", "--selftest"], "rc0"),
    ("setup_cloud",       [PY, "scripts/binggu_setup_cloud.py", "--selftest"], "rc0"),
    ("run_all 회귀",       [PY, "scripts/binggu_publish_run_all_selftests.py"], "rc0"),
    ("e2e lifecycle",     [PY, "scripts/binggu_e2e_lifecycle_selftest.py"], "rc0"),
    ("pytest tests/",     [PY, "-m", "pytest", "tests/", "-q"], "rc0"),
    ("semantic seed SSOT", [PY, "scripts/sync_semantic_seed.py", "--check", "--check-hosted"], "rc0"),
    ("publish workflow",  [PY, "scripts/publish_workflow_selftest.py"], "rc0"),
    ("private path scan", [PY, "scripts/private_path_scan.py", "--source"], "rc0"),
    ("tree scan",         [PY, "scripts/openbinggu_public_tree_scan.py", "--tree", ".", "--public"], "contains:verdict=CLEAN"),
    ("smoke_test",        [PY, "scripts/smoke_test.py", "--home", os.path.join(TMP_HOME, "smoke")], "rc0"),
    ("installer dry-run", [PY, "scripts/install_claude_mcp.py", "--sandbox",
                           "--home", os.path.join(TMP_HOME, "install"), "--dry-run"], "rc0"),
    ("운영명 거부(refuse)", [PY, "scripts/install_claude_mcp.py", "--name", "openbinggu-local", "--apply"], "rc_nonzero"),
]


def run_step(label, argv, verdict):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    # BINGGU_HOME 은 전역 설정 안 함 — CI(ci.yml)와 동일 조건 재현. 각 selftest 는 자체 temp 로
    # 격리되고 운영 sentinel 을 스스로 검증한다. smoke/install 스텝만 --home 인자로 격리한다.
    try:
        p = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
    except Exception as e:  # noqa
        return {"label": label, "ok": False, "detail": "run_error:%s" % str(e)[:80], "rc": None}
    out = (p.stdout or "") + (p.stderr or "")
    if verdict == "rc0":
        ok = p.returncode == 0
        # ruff 미설치는 게이트 검증 불가 — 명확히 구분(하드 실패 대신 표식).
        if not ok and "No module named ruff" in out:
            return {"label": label, "ok": False, "rc": p.returncode, "detail": "ruff 미설치 → pip install ruff==0.15.20"}
    elif verdict == "rc_nonzero":
        ok = p.returncode != 0
    elif verdict.startswith("contains:"):
        tok = verdict.split(":", 1)[1]
        ok = tok in out and "verdict=BLOCK" not in out
    else:
        ok = False
    return {"label": label, "ok": ok, "rc": p.returncode,
            "detail": "" if ok else out.strip().splitlines()[-1][:100] if out.strip() else "(no output)"}


def main():
    print("=" * 64)
    print("BingguPack 로컬 CI 프리플라이트 — ci.yml + mcp-install.yml 전 게이트")
    print("  (temp BINGGU_HOME=%s)" % TMP_HOME)
    print("=" * 64)
    results = []
    for label, argv, verdict in STEPS:
        print("\n▶ %s ..." % label, flush=True)
        r = run_step(label, argv, verdict)
        results.append(r)
        mark = "PASS" if r["ok"] else "FAIL"
        print("  [%s] %s  rc=%s%s" % (mark, label, r.get("rc"),
                                      ("  · " + r["detail"]) if r.get("detail") else ""))
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print("\n" + "-" * 64)
    print("PREFLIGHT: %d/%d PASS" % (passed, total))
    verdict = "GO" if passed == total else "FAIL"
    print("VERDICT=%s" % verdict)
    if verdict != "GO":
        print("FAILED:", ", ".join(r["label"] for r in results if not r["ok"]))
    import shutil
    shutil.rmtree(TMP_HOME, ignore_errors=True)
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
