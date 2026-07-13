"""BingguPack PC-mediated read 공유 — P8: 회귀 묶음 명령 (P1~P7 selftest + cloud_pack + tree scan).

기준 커밋: 42c6ae8 위.
owner 지시(2026-06-14 GO-P8): 회귀 묶음 명령 추가만.
방식 = **summary-fail** (전부 끝까지 실행 → PASS/FAIL 요약 → 하나라도 FAIL이면 exit 1).
                       (fail-fast 아님 — 전 게이트 한 번에 보기 위함, 명확히 고정)

금지: 실 ledger write 0 / cloud upload·DB insert·tag/release·ingest 0 / OpenCrab Cloud 확인 0.
  (각 selftest는 temp 전용이거나 실 ledger read-only — 이 러너는 호출만)

★ FLAKY(transient) 항목 판정 절차 (회귀 단정 전 필수 3중 확증):
  이 러너는 selftest 들을 한 프로세스에서 잇따라 호출하므로, 일부 항목이 파일쓰기·
  tree-scan·임시 home 공유 등으로 1회성 transient FAIL 을 낼 수 있다(예: P4 label,
  tree scan — 백그라운드 에이전트 파일쓰기 레이스 선례). FAIL 을 곧바로 "내 변경의 회귀"로
  단정하지 말고 다음 3개를 모두 확인한 뒤에만 회귀로 결론낸다:
    1) 단독 실행 GO  : python scripts/<해당>.py --selftest  → 단독으로 GATE=GO 인가
    2) 무관 grep     : 해당 selftest 가 내 변경 심볼을 0 참조(grep)하는가
    3) 1회 재실행 GO : 이 러너를 한 번 더 돌려 그 항목이 PASS 로 돌아오는가
  셋 다 통과면 transient(무관) — 통과 못 하면 진짜 회귀로 조사한다.
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
    ("save-ref binding",  "binggu_save_ref_binding_selftest.py",       ["--selftest"], "GATE=GO"),
    ("realpack build",    "binggu_realpack_build.py",                  ["--selftest"], "GATE: GO"),
    ("p1 ranking",        "binggu_p1_ranking.py",                      ["--selftest"], "GATE: GO"),
    ("p1 config",         "binggu_p1_config.py",                       [], "GATE=GO"),
    ("recall engine",     "binggu_recall.py",                          ["--selftest"], "GATE=GO"),
    ("recall trace P2",   "binggu_recall_trace.py",                    ["--selftest"], "GATE=GO"),
    ("answer_rules",      os.path.join("..", "binggupack", "pack", "answer_rules.py"), ["--selftest"], "GATE=GO"),
    ("abstraction",       os.path.join("..", "binggupack", "pack", "abstraction.py"), ["--selftest"], "GATE=GO"),
    ("preflight hook",    os.path.join("..", "hooks", "binggu_preflight_hook.py"), ["--selftest"], "GATE=GO"),
    ("hit_stats comp4",   "binggu_hit_stats.py",                       ["--selftest"], "GATE: GO"),
    ("hit_recording A",   os.path.join("..", "binggupack", "pack", "hit_recording.py"), ["--selftest"], "GATE=GO"),
    ("learn_consume C",   os.path.join("..", "binggupack", "pack", "learn_consume.py"), ["--selftest"], "GATE=GO"),
    ("merkle anchor comp3","binggu_merkle_anchor.py",                  ["--selftest"], "GATE=GO"),
    ("hit_export comp5",  "binggu_hit_export_selftest.py",            [], "GATE=GO"),
    ("session_close",     "binggu_session_close.py",                   ["--selftest"], "GATE=GO"),
    ("worker recall (node)","binggu_worker_recall_selftest.py",        ["--selftest"], "GATE=GO"),
    ("created_at backfill","binggu_created_at_backfill.py",            ["--selftest"], "GATE: GO"),
    ("cloud_pack export", "binggu_cloud_pack_export.py",               ["--selftest"], "GATE=GO"),
    ("cloud_ingest_wire", "binggu_cloud_ingest_wire.py",               ["--selftest"], "GATE=GO"),
    ("cloud_query_wire",  "binggu_cloud_query_wire.py",                ["--selftest"], "GATE=GO"),
    ("crab_pack_wire",    "binggu_crab_pack_wire.py",                  ["--selftest"], "GATE=GO"),
    ("person_crab_sync",  "binggu_person_crab_sync.py",                ["--selftest"], "GATE=GO"),
    ("workspace organize","binggu_workspace_organize.py",              ["--selftest"], "GATE=GO"),
    ("local ingest",      "localbinggu_ingest_executor.py",            ["--selftest"], "GATE=GO"),
    ("harvest inbound",   "binggu_harvest.py",                         ["--selftest"], "GATE: GO"),
    ("local collect",     "binggu_local_collect.py",                   ["--selftest"], "GATE: GO"),
    ("knowledge graph",   "binggu_knowledge_graph.py",                 ["--selftest"], "GATE: GO"),
    ("P3 self-improve",   "openbinggu_p3_self_improve.py",             ["--selftest"], "GATE: GO"),
    ("mcp handlers",      "openbinggu_mcp_server_handlers.py",         ["--selftest"], "GATE: GO"),
    ("trusted approval",  "openbinggu_trusted_approval_boundary_selftest.py", ["--selftest"], "GATE=GO"),
    ("trusted appr bind",  "binggu_trusted_approval_binding_characterization_selftest.py", ["--selftest"], "GATE=GO"),
    ("approval origin",   "binggu_approval_origin_selftest.py",        ["--selftest"], "GATE=GO"),
    # ── P1-B mutation surface closure (hosted 저장 · HAG import = exact-bound approval only) ──
    ("hosted inbox P1-B", "binggu_hosted_inbox.py",                    ["--selftest"], "GATE=GO"),
    ("hosted bundle P1-B","binggu_hosted_bundle.py",                   ["--selftest"], "GATE=GO"),
    ("save_intent outbox","openbinggu_save_intent_outbox_runner.py",   ["--selftest"], "GATE: GO"),
    ("save_intent live",  "openbinggu_save_intent_live_runner.py",     ["--selftest"], "GATE=GO"),
    ("hag sync adapter",  os.path.join("hybrid_agi", "hag_sync_adapter.py"), ["--selftest"], "GATE: GO"),
    ("p1b mutation closure","binggu_p1b_mutation_closure_selftest.py", ["--selftest"], "GATE=GO"),
    ("p1b1 bundle atomicity","binggu_p1b1_bundle_atomicity_selftest.py", ["--selftest"], "GATE=GO"),
    ("setup-save onboard","binggu_setup_save.py",                      ["--selftest"], "GATE: GO"),
    ("archive backup/export", os.path.join("..", "binggupack", "workspace", "archive.py"), [], "GATE=GO"),
    ("publish workflow",  "publish_workflow_selftest.py",              ["--selftest"], "GATE: GO"),
    ("private path scan", "private_path_scan.py",                      ["--source"], "GATE: GO"),
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
    tail = "" if ok else "\n".join(out.strip().splitlines()[-25:])
    return {"label": label, "ok": ok, "rc": p.returncode, "count": cnt, "tail": tail}


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
        # 실패 gate 의 subprocess 출력 tail 을 노출(회귀 원인 규명 — CI 로그에서 바로 확인).
        for r in results:
            if not r["ok"] and r.get("tail"):
                print("\n----- FAILED gate 출력 tail: %s (rc=%s) -----" % (r["label"], r.get("rc")))
                print(r["tail"])
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
