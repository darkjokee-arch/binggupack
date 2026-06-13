#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu MCP 서버 도구 핸들러 결선 후보 (실 서버 등록/공개 前).

목적:
- 이미 만든 mcp_path_gate_adapter.guarded_tool_call 을 실제 MCP 도구 핸들러 후보에 연결.
- read/dry-run 도구만 노출(write/apply/push/sanitizer/enum/team_paid/marketplace 핸들러 부재).
- 도구의 path 입력은 전부 guarded_tool_call 통과 → BLOCK 시 underlying 미호출.
- raw 경로/secret 미출력 → executed/verdict/reason_code/path_id 만.

범위: 핸들러 함수 + 디스패치 테이블 + synthetic selftest.
  ⚠️ MCP 프로토콜(stdio JSON-RPC) 레이어·실 서버 등록/공개는 **미구현(별도 GO)**. 여기선 핸들러 결선 후보만.
CLI: python openbinggu_mcp_server_handlers.py --selftest
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_mcp_path_gate_adapter import guarded_tool_call  # noqa: E402
from binggu_capture_classifier import classify  # noqa: E402
from binggu_capture_buffer import CaptureBuffer  # noqa: E402


# ---- underlying 도구(dry-run mock, FS write 0) ----
# 실제로는 각 스크립트의 read/dry-run 동작에 결선. 여기선 synthetic mock(파일 작업 0).
def _u_pack_build(params=None):
    return {"action": "pack_build", "mode": "dry-run", "pack": "candidate(temp)"}


def _u_pack_validate(params=None):
    return {"action": "pack_validate", "mode": "read", "verdict": "checked"}


def _u_consumer_smoke(params=None):
    return {"action": "consumer_smoke", "mode": "read", "read": "ok"}


def _u_publish_guard_dryrun(params=None):
    return {"action": "publish_guard_dryrun", "mode": "dry-run", "guard": "evaluated"}


def _u_selftest(params=None):
    return {"action": "selftest", "mode": "read", "gate": "see scripts"}


def _u_capture_classify(params=None):
    # 발화 1건 판정(메모리 순수함수, write 0). 발화 원문은 반환 안 함(state/signals만).
    params = params or {}
    v = classify(params.get("utterance", ""), params.get("prev_turn"))
    return {"action": "capture_classify", "mode": "read",
            "state": v["state"], "confidence": v["confidence"], "pinned": v["pinned"],
            "signals": v["signals"]}


def _u_capture_preview(params=None):
    # 발화 리스트 무상태 재구성 → preview 리스트(메모리만, write 0). active 전이 0.
    params = params or {}
    buf = CaptureBuffer()
    for u in (params.get("utterances") or []):
        if isinstance(u, str):
            buf.feed(u)
    return {"action": "capture_preview", "mode": "read", **buf.render_preview()}


# ---- 노출 도구 테이블(read/dry-run 만). 위험 도구는 의도적으로 부재 ----
TOOLS = {
    "pack_build":           {"path_params": ["input_dir"], "underlying": _u_pack_build,          "mode": "dry-run"},
    "pack_validate":        {"path_params": ["pack_path"],  "underlying": _u_pack_validate,       "mode": "read"},
    "consumer_smoke":       {"path_params": ["pack_path"],  "underlying": _u_consumer_smoke,      "mode": "read"},
    "publish_guard_dryrun": {"path_params": ["pack_path"],  "underlying": _u_publish_guard_dryrun, "mode": "dry-run"},
    "selftest":             {"path_params": [],             "underlying": _u_selftest,            "mode": "read"},
    # 캡처 엔진(메모리 순수, write 0). path 입력 없음 → input_schema 로 일반 params 노출.
    "capture_classify":     {"path_params": [], "underlying": _u_capture_classify, "mode": "read",
                             "input_schema": {"properties": {"utterance": {"type": "string"},
                                                             "prev_turn": {"type": "string"}},
                                              "required": ["utterance"]}},
    "capture_preview":      {"path_params": [], "underlying": _u_capture_preview, "mode": "read",
                             "input_schema": {"properties": {"utterances": {"type": "array",
                                                                            "items": {"type": "string"}}},
                                              "required": ["utterances"]}},
}

# 노출 금지(핸들러 부재로 자동 차단되지만, 명시 거부 목록으로 의도 박제)
_FORBIDDEN = {
    "opencrab_write", "opencrab_apply", "opencrab_ingest", "store_write",
    "github_push", "opencrab_upload", "sanitizer_replace", "enum_set",
    "team_billing", "marketplace_publish", "db_write",
}


def handle_tool(tool_name, params, allow_root):
    """
    MCP 도구 요청 1건 처리.
    - 미노출/금지 도구 → tool_not_exposed (underlying 미호출).
    - path 입력 있으면 guarded_tool_call 로 gate 통과시킨 뒤에만 underlying.
    반환: raw 경로/secret 미포함.
    """
    params = params or {}
    if tool_name not in TOOLS:
        rc = "forbidden" if tool_name in _FORBIDDEN else "unknown"
        return {"executed": False, "verdict": "REJECT", "reason_code": "tool_not_exposed:" + rc,
                "tool": tool_name}

    spec = TOOLS[tool_name]
    path_inputs = [params[k] for k in spec["path_params"] if k in params and params[k] is not None]

    if not path_inputs:
        # path 입력 없는 read 도구 → 바로 실행
        return {"executed": True, "verdict": "ALLOW", "tool": tool_name,
                "tool_result": spec["underlying"](params=params)}

    # path 입력은 전부 gate 통과(실행 직전 재검사 포함). BLOCK 시 underlying 미호출.
    r = guarded_tool_call(spec["underlying"], path_inputs=path_inputs,
                          allow_root=allow_root, tool_kwargs={"params": params})
    r["tool"] = tool_name
    return r


# ---------------- selftest ----------------

def _selftest():
    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))

    print("=" * 72)
    print("OpenBinggu MCP server handlers 결선 후보 (synthetic / selftest)")
    print("=" * 72)

    all_ok = True
    raw_leak = False

    cases = [
        # (name, tool, params, expect_executed, note)
        ("validate_toy_ok",      "pack_validate",        {"pack_path": "examples/toy_project/p.json"}, True,  "ALLOW"),
        ("build_toy_ok",         "pack_build",           {"input_dir": "examples/toy_project"},        True,  "ALLOW"),
        ("selftest_no_path_ok",  "selftest",             {},                                           True,  "no-path read"),
        ("build_parent_block",   "pack_build",           {"input_dir": "../outside"},                  False, "parent_escape"),
        ("consumer_npki_block",  "consumer_smoke",       {"pack_path": "C:/Users/PC/AppData/NPKI/c.der"}, False, "deny_cert_npki"),
        ("guard_env_block",      "publish_guard_dryrun", {"pack_path": "examples/toy_project/.env"},    False, "deny_secret"),
        ("validate_bidengine_block", "pack_validate",    {"pack_path": "C:/Users/PC/safety-app/bid-engine/x"}, False, "deny_bid_engine"),
        ("forbidden_write",      "opencrab_write",       {"pack_path": "examples/toy_project/p.json"}, False, "tool_not_exposed:forbidden"),
        ("forbidden_push",       "github_push",          {},                                           False, "tool_not_exposed:forbidden"),
        ("unknown_tool",         "do_something",         {},                                           False, "tool_not_exposed:unknown"),
        ("capture_classify_ok",  "capture_classify",     {"utterance": "B안으로 결정"},                 True,  "read no-path"),
        ("capture_preview_ok",   "capture_preview",      {"utterances": ["이거 저장해", "ㅋㅋ"]},        True,  "read no-path"),
    ]

    import json as _json
    for name, tool, params, exp_exec, note in cases:
        r = handle_tool(tool, params, allow_root)
        executed = bool(r.get("executed"))
        ok = (executed == exp_exec)
        all_ok = all_ok and ok
        # raw 미출력: 결과에 입력 경로 substring 없어야
        blob = _json.dumps(r, ensure_ascii=False)
        for v in params.values():
            if isinstance(v, str) and v.strip() and v.strip() in blob:
                raw_leak = True
        verdict = r.get("verdict")
        rc = r.get("reason_code") or (r.get("blocked") and r["blocked"][0].get("reason_code")) or ""
        print("  [%s] %-26s tool=%-20s executed=%-5s verdict=%-7s %s"
              % ("OK" if ok else "FAIL", name, tool, executed, verdict, rc))

    # 노출 도구가 read/dry-run 만인지(쓰기/업로드 핸들러 부재) 확인
    exposed_ok = all(TOOLS[t]["mode"] in ("read", "dry-run") for t in TOOLS)
    no_forbidden_exposed = all(f not in TOOLS for f in _FORBIDDEN)
    all_ok = all_ok and exposed_ok and no_forbidden_exposed
    print("\n  exposed_tools_read_or_dryrun_only:", exposed_ok)
    print("  forbidden_tools_not_exposed:", no_forbidden_exposed)
    print("  raw_path_not_leaked:", (not raw_leak))
    print("  operating_store_unchanged: True (핸들러 + mock, FS write 0)")
    print("  mcp_protocol_layer: NOT_IMPLEMENTED (실 서버 등록/공개 별도 GO)")

    gate = "GO" if (all_ok and not raw_leak) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_mcp_server_handlers.py [--selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
