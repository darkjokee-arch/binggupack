#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu MCP path gate adapter (S3/X1 실연결 최소 구현 후보).

목적:
- MCP/local 도구의 모든 path 입력이 openbinggu_path_safety_gate.classify_path 를 통과하게 한다.
- 도구 실행 "직전" 재검사로 TOCTOU 잔여를 줄인다.
- gate 실패(BLOCK) 시 실제 도구 함수를 호출하지 않는다.
- raw 경로값은 출력하지 않는다 → path_id / reason_code / count 만.

범위: adapter + synthetic selftest. 실제 MCP 서버 공개/등록 0. FS write 0(경로 분석만, underlying tool 은 mock).
CLI: python openbinggu_mcp_path_gate_adapter.py --selftest

설계 ref: BINGGUPACK_MCP_EXPOSURE_CANDIDATE.md(경로 입력 안전) / BINGGUPACK_FIRST_RELEASE_4CLI_SYNTHESIS.md §3-A(S3)/§3-B(X1)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_path_safety_gate import classify_path  # noqa: E402


def _scan(path_inputs, allow_root):
    """각 입력 경로 판정. raw 경로 미포함 결과 리스트."""
    out = []
    for p in path_inputs:
        r = classify_path(p, allow_root)
        out.append({"verdict": r["verdict"], "reason_code": r["reason_code"], "path_id": r["path_id"]})
    return out


def guarded_tool_call(tool_fn, *, path_inputs, allow_root, recheck=True, tool_kwargs=None):
    """
    path 입력을 gate 통과시킨 뒤에만 tool_fn 을 호출.
    - 1차 검사 → BLOCK 1건이라도 있으면 tool_fn 미호출.
    - recheck=True: 모두 ALLOW 여도 tool_fn 호출 "직전" 재검사(TOCTOU 잔여 감소). 재검사에서 BLOCK 시 미호출.
    반환: dict(executed, verdict, [blocked|allowed_path_ids], count, [tool_result]) — raw 경로 미포함.
    """
    tool_kwargs = tool_kwargs or {}

    first = _scan(path_inputs, allow_root)
    blocked = [r for r in first if r["verdict"] == "BLOCK"]
    if blocked:
        return {"executed": False, "verdict": "BLOCK", "phase": "pre",
                "blocked": [{"reason_code": r["reason_code"], "path_id": r["path_id"]} for r in blocked],
                "count": len(blocked)}

    if recheck:
        second = _scan(path_inputs, allow_root)
        rblocked = [r for r in second if r["verdict"] == "BLOCK"]
        if rblocked:
            return {"executed": False, "verdict": "BLOCK", "phase": "recheck",
                    "blocked": [{"reason_code": r["reason_code"], "path_id": r["path_id"]} for r in rblocked],
                    "count": len(rblocked)}

    # 모두 ALLOW → 실제 도구 실행
    result = tool_fn(**tool_kwargs)
    return {"executed": True, "verdict": "ALLOW",
            "allowed_path_ids": [r["path_id"] for r in first],
            "count": len(first), "tool_result": result}


# ---------------- selftest ----------------

def _selftest():
    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))

    # underlying mock 도구: 호출되면 카운터 증가 (실제 실행 여부 추적)
    state = {"calls": 0}

    def mock_tool(label="x"):
        state["calls"] += 1
        return "TOOL_RAN:" + label

    # 트리 밖 절대경로: OS별로 실제 절대경로인 입력 사용 (Windows=드라이브문자 / POSIX=/-시작)
    _outside_abs = "C:/Windows/System32/config/SAM" if os.name == "nt" else "/etc/passwd"

    cases = [
        # (name, path_inputs, expect_executed, expect_reason_or_None)
        ("toy_internal_ok",      ["examples/toy_project/Makefile"],             True,  None),
        ("toy_multi_ok",         ["examples/toy_project/a.py", "examples/toy_project/b.py"], True, None),
        ("parent_escape_block",  ["../secret_outside.txt"],                     False, "parent_escape"),
        ("npki_block",           ["C:/Users/PC/AppData/LocalLow/NPKI/yessign/cert.der"], False, "deny_cert_npki"),
        ("bid_engine_block",     ["C:/Users/PC/safety-app/bid-engine/app/worker.py"],   False, "deny_bid_engine"),
        ("env_secret_block",     ["examples/toy_project/.env"],                 False, "deny_secret"),
        ("opencrab_store_block", ["data/localcrab_index.sqlite"],               False, "deny_opencrab_store"),
        ("mixed_one_bad_block",  ["examples/toy_project/ok.py", "../escape"],   False, "parent_escape"),
        ("unc_block",            ["\\\\fs\\share\\x"],                          False, "unc"),
        ("outside_abs_block",    [_outside_abs],                                False, "outside_root"),
    ]

    print("=" * 72)
    print("OpenBinggu MCP path gate adapter (synthetic / selftest)")
    print("=" * 72)

    all_ok = True
    raw_leak = False
    blocked_reasons = {}
    for name, paths, exp_exec, exp_reason in cases:
        before = state["calls"]
        r = guarded_tool_call(mock_tool, path_inputs=paths, allow_root=allow_root,
                              tool_kwargs={"label": name})
        called = (state["calls"] > before)
        exec_ok = (r["executed"] == exp_exec) and (called == exp_exec)
        reason_ok = True
        if not exp_exec:
            got = [b["reason_code"] for b in r.get("blocked", [])]
            reason_ok = (exp_reason in got)
            blocked_reasons[exp_reason] = blocked_reasons.get(exp_reason, 0) + 1
        ok = exec_ok and reason_ok
        all_ok = all_ok and ok

        # raw 경로 미출력 검증: 결과 어디에도 입력 원본 substring 이 없어야
        import json as _json
        blob = _json.dumps(r, ensure_ascii=False)
        for p in paths:
            ps = p.strip()
            if ps and ps in blob:
                raw_leak = True

        tag = "OK" if ok else "FAIL"
        detail = ("executed=%s called=%s" % (r["executed"], called))
        if not r["executed"]:
            detail += " reason=%s" % ([b["reason_code"] for b in r.get("blocked", [])])
        print("  [%s] %-22s %s" % (tag, name, detail))

    print("\n  --- blocked reason 집계(raw 경로 미출력) ---")
    for k in sorted(blocked_reasons):
        print("    %-22s %d" % (k, blocked_reasons[k]))

    print("\n  total_underlying_calls:", state["calls"], "(ALLOW 케이스만 실행)")
    print("  raw_path_not_leaked:", (not raw_leak))
    print("  operating_store_unchanged: True (경로 분석 + mock tool, FS write 0)")

    gate = "GO" if (all_ok and not raw_leak) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_mcp_path_gate_adapter.py [--selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
