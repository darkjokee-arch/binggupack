"""BingguPack PC-mediated read 공유 — P8: 회귀 묶음 명령 (P1~P7 selftest + cloud_pack + tree scan).

기준 커밋: 42c6ae8 위.
owner 지시(2026-06-14 GO-P8): 회귀 묶음 명령 추가만.
방식 = **summary-fail** (전부 끝까지 실행 → PASS/FAIL 요약 → 하나라도 FAIL이면 exit 1).
                       (fail-fast 아님 — 전 게이트 한 번에 보기 위함, 명확히 고정)

금지: 실 ledger write 0 / cloud upload·DB insert·tag/release·ingest 0 / OpenCrab Cloud 확인 0.
  (각 selftest는 temp 전용이거나 실 ledger read-only — 이 러너는 호출만)
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")

# (라벨, 스크립트, 추가인자, 성공판정문구)
GATES = [
    ("P1 queue",          "binggu_publish_queue_p1_selftest.py",       [], "GATE=GO"),
    ("P2 pipeline",       "binggu_publish_p2_pipeline_selftest.py",    [], "GATE=GO"),
    ("P3 real ledger",    "binggu_publish_p3_real_ledger_selftest.py", [], "GATE=GO"),
    ("P4 label",          "binggu_publish_p4_label_selftest.py",       [], "GATE=GO"),
    ("P5 promote",        "binggu_publish_p5_promote_selftest.py",     [], "GATE=GO"),
    ("P6 OpenCrab repair","binggu_publish_p6_opencrab_pack_selftest.py",[], "GATE=GO"),
    ("autopush",          "binggu_publish_autopush.py",                ["--selftest"], "GATE: GO"),
    ("save_gate",         "binggu_save_gate.py",                       ["--selftest"], "GATE: GO"),
    ("save_gate hook",    os.path.join("..", "hooks", "binggu_save_gate_hook.py"), ["--selftest"], "GATE=GO"),
    ("realpack build",    "binggu_realpack_build.py",                  ["--selftest"], "GATE: GO"),
    ("p1 ranking",        "binggu_p1_ranking.py",                      ["--selftest"], "GATE: GO"),
    ("p1 config",         "binggu_p1_config.py",                       [], "GATE=GO"),
    ("recall engine",     "binggu_recall.py",                          ["--selftest"], "GATE=GO"),
    ("worker recall (node)","binggu_worker_recall_selftest.py",        ["--selftest"], "GATE=GO"),
    ("created_at backfill","binggu_created_at_backfill.py",            ["--selftest"], "GATE: GO"),
    ("cloud_pack export", "binggu_cloud_pack_export.py",               ["--selftest"], "GATE=GO"),
    ("local ingest",      "localbinggu_ingest_executor.py",            ["--selftest"], "GATE=GO"),
    ("harvest inbound",   "binggu_harvest.py",                         ["--selftest"], "GATE: GO"),
    ("P3 self-improve",   "openbinggu_p3_self_improve.py",             ["--selftest"], "GATE: GO"),
    ("tree scan",         "openbinggu_public_tree_scan.py",            ["--tree", REPO, "--public"], "verdict=CLEAN"),
]


def run_one(label, script, extra, ok_marker):
    path = os.path.join(SCRIPTS, script)
    if not os.path.exists(path):
        return {"label": label, "ok": False, "detail": "script_missing", "rc": None}
    try:
        p = subprocess.run([sys.executable, path] + extra, cwd=REPO,
                           capture_output=True, text=True, timeout=600)
    except Exception as e:  # noqa
        return {"label": label, "ok": False, "detail": "run_error:%s" % str(e)[:60], "rc": None}
    out = (p.stdout or "") + (p.stderr or "")
    token_ok = ok_marker in out
    # selftest 계열은 rc==0 + GATE=GO 동시 / tree scan 은 verdict=CLEAN (rc 무관)
    if ok_marker == "verdict=CLEAN":
        ok = token_ok and "verdict=BLOCK" not in out
    else:
        ok = (p.returncode == 0) and token_ok
    # 게이트 카운트 추출 (예: === 17/17 ===)
    import re
    m = re.search(r"===\s*(\d+)/(\d+)\s*===", out)
    cnt = "%s/%s" % (m.group(1), m.group(2)) if m else "-"
    return {"label": label, "ok": ok, "rc": p.returncode, "count": cnt}


def main():
    print("=" * 56)
    print("BingguPack 회귀 묶음 (summary-fail) — P1~P7 + cloud_pack + tree scan")
    print("=" * 56)
    results = [run_one(*g) for g in GATES]
    print()
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print("[%s] %-20s rc=%s gates=%s" % (mark, r["label"], r.get("rc"), r.get("count", "-")))
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print("\n" + "-" * 56)
    print("SUMMARY: %d/%d PASS" % (passed, total))
    verdict = "GO" if passed == total else "FAIL"
    print("REGRESSION=%s" % verdict)
    if verdict != "GO":
        print("FAILED:", ", ".join(r["label"] for r in results if not r["ok"]))
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
