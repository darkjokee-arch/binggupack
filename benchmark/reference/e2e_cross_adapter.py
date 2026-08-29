# -*- coding: utf-8 -*-
"""Cross-adapter E2E — '모델 A 저장 → 모델 B recall/explain' 로컬 참조구현.

두 개의 어댑터가 **하나의 공유 격리 홈(ledger.sqlite 단일 버스)** 을 공유한다:
  · Writer A = BingguPackAdapter  — preview→save(사람 승인·CLAUDECODE unset)로 활성 기억을 만든다.
  · Reader B = ReaderOnlyAdapter — 같은 공유 홈을 읽기 바인딩해 새 프로세스 recall/explain 만 한다.

이 흐름은 이 repo 최초의 '2-proc-shared-home' 패턴이라, 동작을 가정하지 않고 로컬+CI green 으로만
증명한다(함수형 진입점 run_e2e 를 test 가 import 해 단언).

경계(정직 표기):
  · 결정성 — 공유 홈은 semantic_recall 기본 OFF(부재→False) 라 회상은 순수 lexical. sleep/random/
    wall-clock 단언 0. freshness 만료·tamper·실 worker 는 단언 대상 아님(UNSUPPORTED/illustrative).
  · canonicalization digest 결정성은 core 심볼을 재-import 하지 않고 check_vectors.py 를 subprocess
    로 위임(exit0=GO). 네트워크 egress 0(requests/urllib/socket import 없음).
  · Hosted '최종저장=commit_bundle 수렴' 은 로컬 커밋경로(save→ledger)로만 단언하고, 실 worker
    HMAC/DO 는 이 로컬 참조구현에서 실행하지 않는다(UNSUPPORTED/illustrative).
"""
from __future__ import annotations

import os
import subprocess
import sys

from benchmark.adapters.binggupack import BingguPackAdapter
from benchmark.contracts import Cap, fp_content_equal
from benchmark.reference.reader_adapter import ReaderOnlyAdapter
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict, summarize
from benchmark.runner import _assert_home_isolated, _make_safe_root
from benchmark.scenarios import mgb_08

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))                 # repo root
_CHECK_VECTORS = os.path.join(_ROOT, "docs", "memory-pr", "tools", "check_vectors.py")

# reader 가 절대 선언하면 안 되는 write/mutation cap 집합(단일-버스 단언용).
_WRITE_CAPS = frozenset({
    Cap.INIT, Cap.PREVIEW, Cap.SAVE, Cap.LIST_ACTIVE, Cap.SUPERSEDE, Cap.PAIR,
    Cap.REMOTE_INTENT, Cap.CAPTURE_CANDIDATE, Cap.UNAUTHORIZED_WRITE,
    Cap.EXACT_BINDING, Cap.STALE_FRESHNESS, Cap.REPLAY_APPROVAL, Cap.INTEGRITY_PUBLIC,
})

# 비승인 대조군 — fixture 와 겹치지 않는 합성 문장(AI 가 승인 없이 남기려는 시도).
_AI_UNAPPROVED = "에이전트가 사람 승인 없이 임의로 남기려 한 메모라고 정했어요."


def _ok(cond: bool) -> Verdict:
    return Verdict.PASS if cond else Verdict.FAIL


def _run_check_vectors() -> tuple[int, str]:
    """canonicalization KAT drift 검증을 check_vectors.py subprocess 로 위임(core 재-import 금지)."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.pop("CLAUDECODE", None)
    p = subprocess.run([sys.executable, _CHECK_VECTORS], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=_ROOT, env=env, timeout=120)
    return p.returncode, (p.stdout or "")[-400:]


def run_e2e(root: str | None = None) -> dict:
    """모델 A 저장 → 모델 B recall/explain 로컬 cross-adapter E2E. receipt(dict) 반환.

    root=None 이면 runner._make_safe_root() 로 안전 임시 root 를 만든다(운영 홈 상하위/symlink 거부).
    """
    writer = BingguPackAdapter()
    op_fp_before = writer.operating_fingerprint()

    created_root = root is None
    safe_root: str = _make_safe_root() if root is None else root
    results: list[ScenarioResult] = []
    try:
        # (1)(2) 공유 격리 홈 1개 생성 + 격리 단언(운영 홈 밖·symlink 아님) + init.
        home = writer.new_home(safe_root)
        _assert_home_isolated(home, safe_root)
        writer.observe(home, Cap.INIT)

        # (3) Writer A: preview→save(CLAUDECODE unset=사람 승인)로 target+distractor2+hard-neg 저장.
        fixtures = (mgb_08._TARGET, mgb_08._DIST1, mgb_08._DIST2, mgb_08._HARD)
        target_nid = None
        active_after = None
        save_ok = True
        for t in fixtures:
            s = writer.observe(home, Cap.SAVE, text=t)
            save_ok = save_ok and s.exit_code == 0 and (s.state.get("saved") or 0) >= 1
            active_after = s.state.get("active_count")
            if t == mgb_08._TARGET:
                target_nid = (s.state.get("node_ids") or [None])[0]
        results.append(ScenarioResult(
            "E2E-A-SAVE", "writer-approved-save", ExecutionStatus.OK,
            _ok(save_ok and active_after == len(fixtures) and bool(target_nid)),
            reason="사람 승인 저장 %d건·active=%s·target_nid=%s"
            % (len(fixtures), active_after, bool(target_nid))))

        # (4) 비승인 대조: CLAUDECODE=1 save → 거부·active 불변(AI 는 제안까지만).
        ua = writer.observe(home, Cap.UNAUTHORIZED_WRITE, text=_AI_UNAPPROVED)
        ua_before = ua.state.get("active_before")
        ua_after = ua.state.get("active_after")
        results.append(ScenarioResult(
            "E2E-NOAUTH", "ai-write-blocked", ExecutionStatus.OK,
            _ok(ua.exit_code != 0 and ua_after == ua_before == len(fixtures)),
            reason="CLAUDECODE=1 save exit=%s·active %s→%s(거부+불변 기대)"
            % (ua.exit_code, ua_before, ua_after)))

        # (5a) Reader B: 같은 공유 홈 읽기 바인딩 → 새 프로세스 recall(mgb_08 특이성).
        reader = ReaderOnlyAdapter()
        rhome = reader.bind_home(home.root)
        rec = reader.observe(rhome, Cap.RECALL_FRESH, query="결제 배포 스테이징 검증")
        out = rec.stdout or ""
        has_target = mgb_08._TARGET in out
        dist_absent = all(x not in out for x in (mgb_08._DIST1, mgb_08._DIST2, mgb_08._HARD))
        results.append(ScenarioResult(
            "E2E-B-RECALL", "cross-recall-specificity", ExecutionStatus.OK,
            _ok(rec.exit_code == 0 and has_target and dist_absent),
            reason="fresh recall exit=%s·target=%s·distractor/hard-neg 배제=%s"
            % (rec.exit_code, has_target, dist_absent),
            evidence={"recall": rec.to_dict()}))

        # (5b) Reader B: explain(A 의 node_id) 근거 연결 + 존재않는 id 실패(negative control, mgb_06).
        if target_nid:
            ex = reader.observe(rhome, Cap.EXPLAIN, node_id=target_nid)
            tail = target_nid.rsplit(":", 1)[-1]
            exo = ex.stdout or ""
            id_linked = (target_nid in exo) or (tail in exo)
            has_evidence = ("근거" in exo) or ("evidence" in exo.lower()) or ("->" in exo)
            neg = reader.observe(rhome, Cap.EXPLAIN, node_id="node:CONV:00000000")
            nego = neg.stdout or ""
            neg_ok = (target_nid not in nego) and (
                "찾을 수 없" in nego or neg.exit_code != 0 or not nego.strip())
            results.append(ScenarioResult(
                "E2E-B-EXPLAIN", "cross-explain-provenance", ExecutionStatus.OK,
                _ok(ex.exit_code == 0 and id_linked and has_evidence and neg_ok),
                reason="explain id연결=%s·근거=%s·negative제어=%s"
                % (id_linked, has_evidence, neg_ok),
                evidence={"explain": ex.to_dict(), "negative": neg.to_dict()}))
        else:
            results.append(ScenarioResult(
                "E2E-B-EXPLAIN", "cross-explain-provenance", ExecutionStatus.ERROR,
                Verdict.FAIL, reason="target node_id 미획득"))

        # (6) 단일 버스 단언: 공유물=ledger.sqlite 하나 · reader 는 write cap 미선언.
        ledger = os.path.join(home.root, "ledger.sqlite")
        rcaps = reader.capabilities()
        single_bus = (os.path.isfile(ledger)
                      and rcaps == set(ReaderOnlyAdapter._READER_CAPS)
                      and not (rcaps & _WRITE_CAPS))
        results.append(ScenarioResult(
            "E2E-BUS", "single-shared-ledger", ExecutionStatus.OK, _ok(single_bus),
            reason="ledger.sqlite 존재=%s·reader caps=%s·write cap 교집합=%s"
            % (os.path.isfile(ledger), sorted(rcaps), sorted(rcaps & _WRITE_CAPS))))

        # (2') canonicalization KAT drift — core 재-import 아닌 check_vectors subprocess 위임.
        kat_rc, kat_tail = _run_check_vectors()
        results.append(ScenarioResult(
            "E2E-KAT", "canonicalization-drift", ExecutionStatus.OK, _ok(kat_rc == 0),
            reason="check_vectors exit=%s(0=GO)" % kat_rc,
            evidence={"tail": kat_tail}))

        # Hosted 최종저장 수렴 — 로컬 commit 경로(save→ledger)로만 단언. 실 worker HMAC/DO 는
        # 이 로컬 참조구현에서 실행하지 않는다(정직: UNSUPPORTED/illustrative).
        results.append(ScenarioResult(
            "E2E-HOSTED", "hosted-final-store-commit_bundle", ExecutionStatus.UNSUPPORTED,
            Verdict.UNSUPPORTED,
            reason="Hosted 최종저장=로컬 commit_bundle 수렴(로컬 save→ledger 로 예시)·실 worker "
                   "HMAC/DO 미실행(illustrative)"))
    finally:
        # writer 소유 홈은 root rmtree 로 정리(reader.cleanup 은 no-op). 우리가 만든 root 만 지운다.
        if created_root:
            import shutil
            shutil.rmtree(safe_root, ignore_errors=True)

    # (7) 사후 운영홈 sentinel 불변(content 기준 · mtime 오탐 제외) — hard gate.
    op_fp_after = writer.operating_fingerprint()
    op_equal = fp_content_equal(op_fp_before, op_fp_after)

    summary = summarize(results, expected_total=len(results))
    summary["operating_fingerprint_equal"] = op_equal
    go = (summary["FAIL"] == 0 and op_equal and summary["total_matches_expected"])
    return {
        "e2e": "cross_adapter",
        "spec": "memory-pr-reference-v0.1",
        "decision": "GO" if go else "FAIL",
        "summary": summary,
        "operating_fingerprint_equal": op_equal,
        "kat_vectors_exit": kat_rc,
        "hosted_final_store": "UNSUPPORTED/illustrative (commit_bundle 로컬 수렴만 단언)",
        "results": [r.to_dict() for r in results],
    }


def main(argv=None) -> int:
    receipt = run_e2e()
    print("Memory PR cross-adapter E2E — decision=%s" % receipt["decision"])
    for r in receipt["results"]:
        print("  %-14s %-32s %s / %s"
              % (r["id"], r["title"], r["execution_status"], r["verdict"]))
    s = receipt["summary"]
    print("SUMMARY PASS=%(PASS)d FAIL=%(FAIL)d UNSUPPORTED=%(UNSUPPORTED)d TOTAL=%(TOTAL)d"
          % s)
    print("  operating_fingerprint_equal=%s · kat_exit=%s"
          % (receipt["operating_fingerprint_equal"], receipt["kat_vectors_exit"]))
    return 0 if receipt["decision"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
