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
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")

# 게이트별 격리 실행 — 2026-07-29.
#   ① 속도: 게이트 56개를 순차 subprocess 로 띄우면 windows 러너에서 222초(ubuntu 24초 · 9배)다.
#      python 인터프리터 기동 비용이 windows 에서 훨씬 크기 때문이고, 게이트 자체는 서로 독립이다.
#   ② 안정성: 종전엔 전 게이트가 **같은 BINGGU_HOME/temp 를 공유**해 파일쓰기 레이스로 1회성
#      transient FAIL 이 났다(본 파일 상단 FLAKY 절차의 원인). 게이트마다 홈을 갈라 원인을 없앤다.
# 순서 보존: 결과는 GATES 순서대로 출력한다(로그 diff 안정).
# BGP_REGRESS_WORKERS=1 로 순차 실행(원인 규명·baseline 측정용).
_MAX_WORKERS = max(1, int(os.environ.get("BGP_REGRESS_WORKERS")
                          or min(8, (os.cpu_count() or 2))))

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
    ("recall run outcome","binggu_outcome_attribution.py",             ["--selftest"], "GATE=GO"),
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
    # 2026-07-28 등록: 둘 다 게이트 밖이라 회귀가 조용히 살아 있었다(replace_ux 는 실제로
    #   StopIteration 으로 죽은 채 방치). 게이트에 없는 selftest = 회귀 감지 0.
    ("candidate list view","openbinggu_candidate_list_view.py",         ["--selftest"], "GATE: GO"),
    ("candidate replace ux","openbinggu_candidate_replace_ux.py",       ["--selftest"], "GATE: GO"),
    ("archive backup/export", os.path.join("..", "binggupack", "workspace", "archive.py"), [], "GATE=GO"),
    ("publish workflow",  "publish_workflow_selftest.py",              ["--selftest"], "GATE: GO"),
    ("private path scan", "private_path_scan.py",                      ["--source"], "GATE: GO"),
    ("tree scan",         "openbinggu_public_tree_scan.py",            ["--tree", REPO, "--public"], "verdict=CLEAN"),
]


def run_one(label, script, extra, ok_marker, home=None):
    path = os.path.join(SCRIPTS, script)
    if not os.path.exists(path):
        return {"label": label, "ok": False, "detail": "script_missing", "rc": None}
    env = os.environ.copy()
    # 게이트는 격리 홈(BINGGU_HOME=temp)에서 도는데 임베딩 영속 캐시도 그 홈에 있어 매번 콜드 →
    # 게이트 1회당 실 Ollama /api/embed 758회(실측 2026-07-30). semantic 자체를 검증하는 게이트는
    # probe/embed_fn 주입(mock)을 쓰므로 이 OFF 에 영향받지 않는다 — 곁다리 실서버 호출만 제거.
    env.setdefault("BINGGU_SEMANTIC_OFF", "1")
    if home:
        # 게이트 전용 홈·temp. 운영 홈(~/.binggupack)은 어느 게이트도 건드리지 않는다.
        # ★ temp 는 홈 **밖**의 형제 디렉터리여야 한다 — 같은 경로로 묶으면 temp 로 내보내는
        #   export 가 "거버넌스 홈에 쓰기"로 오인돼 차단된다(binggu_hit_export._assert_export_target).
        h = os.path.join(home, "home")
        t = os.path.join(home, "tmp")
        os.makedirs(h, exist_ok=True)
        os.makedirs(t, exist_ok=True)
        env["BINGGU_HOME"] = h
        env["TMPDIR"] = env["TEMP"] = env["TMP"] = t
    try:
        p = subprocess.run([sys.executable, path] + extra, cwd=REPO,
                           capture_output=True, text=True, timeout=600, env=env)
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
    root = tempfile.mkdtemp(prefix="bgp_regress_")
    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            # 게이트마다 격리 홈(h0, h1, ...) — 공유 상태 0 이라 순서 무관·병렬 안전.
            futures = [ex.submit(run_one, *g, home=os.path.join(root, "h%d" % i))
                       for i, g in enumerate(GATES)]
            results = [f.result() for f in futures]   # 제출 순서 = GATES 순서
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("(격리 홈 · 병렬 %d)" % _MAX_WORKERS)
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
