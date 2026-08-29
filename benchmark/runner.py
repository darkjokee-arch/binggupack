# -*- coding: utf-8 -*-
"""MGB v0.1 runner — 시나리오를 로드해 adapter 로 실행하고, verdict 를 독립 판정한다.

원칙(owner 확정):
  · black-box: adapter 는 공개 인터페이스만 실행하고 관찰 자료(exit·state)만 반환한다.
  · runner 가 시나리오 계약으로 verdict 를 계산한다(adapter 의 자기신고 미신뢰).
  · REQUIRES(공개 인터페이스) 미충족 → UNSUPPORTED/UNSUPPORTED.
  · 매 시나리오 새 격리 홈. 홈은 허용 임시 root 하위 realpath 여야 하고 symlink 를 거부한다.
  · 임시 root 와 운영 홈이 같거나 상하위 관계면 중단.
  · 운영 정본 fingerprint 는 매 시나리오 전후로 재검사(사후 sentinel). 오염되면 hard FAIL.
"""
from __future__ import annotations

from contextlib import suppress
import argparse
import importlib
import json
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmark.contracts import fp_content_equal  # noqa: E402
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict, summarize  # noqa: E402
from benchmark.scenarios import ORDER  # noqa: E402


def _resolve_adapter(name: str):
    if name == "binggupack":
        from benchmark.adapters.binggupack import BingguPackAdapter
        return BingguPackAdapter()
    if name == "toy_conforming":
        from benchmark.adapters.toy_conforming import ToyConformingAdapter
        return ToyConformingAdapter()
    if name == "toy_failing":
        from benchmark.adapters.toy_failing import ToyFailingAdapter
        return ToyFailingAdapter()
    raise SystemExit("알 수 없는 adapter: %s (binggupack|toy_conforming|toy_failing)" % name)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def _is_within(child: str, parent: str) -> bool:
    child, parent = _norm(child), _norm(parent)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def _operating_home_real() -> str:
    return _norm(os.path.join(os.path.expanduser("~"), ".binggupack"))


def _make_safe_root() -> str:
    root = _norm(tempfile.mkdtemp(prefix="mgb_run_"))
    op = _operating_home_real()
    if root == op or _is_within(root, op) or _is_within(op, root):
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError("임시 root 가 운영 홈과 같거나 상하위 관계 — 중단: %s vs %s" % (root, op))
    return root


def _assert_home_isolated(home, root: str) -> None:
    if os.path.islink(home.root):
        raise RuntimeError("격리 홈이 symlink/junction — 거부: %s" % home.root)
    if not _is_within(home.root, root):
        raise RuntimeError("격리 홈이 허용 root 밖 — 거부: %s" % home.root)


def _run_one(mod, adapter, root: str, op_fp_before) -> ScenarioResult:
    sid, title = mod.ID, mod.TITLE
    requires = set(getattr(mod, "REQUIRES", set()))
    caps = set(adapter.capabilities())
    if not requires <= caps:
        return ScenarioResult(
            sid, title, ExecutionStatus.UNSUPPORTED, Verdict.UNSUPPORTED,
            reason="공개 인터페이스 미지원: %s" % sorted(requires - caps),
            operating_state_invariant=True)

    home = None
    try:
        home = adapter.new_home(root)
        _assert_home_isolated(home, root)
        res = mod.run(adapter, home, {})
    except Exception as e:  # 실행 자체 오류 → ERROR/FAIL(제품 계약 실패와 구분)
        res = ScenarioResult(sid, title, ExecutionStatus.ERROR, Verdict.FAIL,
                             reason="실행 오류: %r" % e)
    finally:
        if home is not None:
            with suppress(Exception):
                adapter.cleanup(home)

    # 사후 sentinel: 운영 정본 fingerprint 불변 재검사(content 기준 · mtime 외부활동 오탐 제외).
    op_fp_after = adapter.operating_fingerprint()
    inv = fp_content_equal(op_fp_before, op_fp_after)
    if res.operating_state_invariant is None:
        res.operating_state_invariant = inv
    if not inv:
        res.operating_state_invariant = False
        res.reason = (res.reason + " | ★운영 정본 오염 감지(hard FAIL)").strip()
    return res


def run_benchmark(adapter):
    op_fp_before = adapter.operating_fingerprint()
    root = _make_safe_root()
    results: list[ScenarioResult] = []
    try:
        for name in ORDER:
            mod = importlib.import_module("benchmark.scenarios.%s" % name)
            results.append(_run_one(mod, adapter, root, op_fp_before))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    op_fp_after = adapter.operating_fingerprint()
    summary = summarize(results)
    summary["operating_fingerprint_equal"] = fp_content_equal(op_fp_before, op_fp_after)
    return results, summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Memory Governance Benchmark (MGB) v0.1 runner")
    ap.add_argument("--adapter", default="binggupack")
    ap.add_argument("--out", default=None, help="결과 JSON 경로(기본 results/<adapter>.json)")
    a = ap.parse_args(argv)

    adapter = _resolve_adapter(a.adapter)
    results, summary = run_benchmark(adapter)
    payload = {"adapter": adapter.name, "spec": "mgb-v0.1",
               "summary": summary, "results": [r.to_dict() for r in results]}

    out = a.out or os.path.join(_HERE, "results", "%s.json" % adapter.name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("MGB v0.1 — adapter=%s" % adapter.name)
    for r in results:
        print("  %-7s %-32s %s / %s"
              % (r.id, r.title, ExecutionStatus(r.execution_status).value,
                 Verdict(r.verdict).value))
    print("SUMMARY  PASS=%(PASS)d FAIL=%(FAIL)d UNSUPPORTED=%(UNSUPPORTED)d "
          "NOT_RUN=%(NOT_RUN)d TOTAL=%(TOTAL)d" % summary)
    print("  total_matches_expected=%s · operating_state_ok=%s · result→%s"
          % (summary["total_matches_expected"], summary["operating_state_ok"], out))

    bad = (summary["FAIL"] > 0 or not summary["operating_state_ok"]
           or not summary["total_matches_expected"])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
