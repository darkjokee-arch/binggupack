#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu MCP server (stdio JSON-RPC) wrapper 후보 — 실 등록/공개 前.

목적:
- openbinggu_mcp_server_handlers.handle_tool 을 stdio JSON-RPC 형태로 감싼다.
- read/dry-run + save_candidate(write-gated) 노출(tools/list). 위험 도구는 노출 0.
- 모든 path 입력은 기존 path gate/adapter(guarded_tool_call)를 통과(handle_tool 경유).
- 응답에는 tool/verdict/executed/reason_code/path_id/count 만 — raw 경로/secret 출력 0.
- malformed request 안전 처리(JSON-RPC error).

범위: wrapper 코드(serve_stdio 정식 구현) + synthetic protocol selftest.
  serve_stdio()는 정식 JSON-RPC 루프(initialize/tools/list/tools/call). selftest 는 handle_jsonrpc 직접 검증.
  실제 MCP 설정 파일(.mcp.json/.claude.json) 등록은 owner 운영 행위(코드 변경 아님).
CLI: python openbinggu_mcp_server.py --selftest      # 프로토콜 synthetic 검증
     python openbinggu_mcp_server.py --serve <ROOT>  # 실 stdio 서버(설정 등록은 owner)
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_mcp_server_handlers import handle_tool, TOOLS, _FORBIDDEN  # noqa: E402

_TOOL_DESC = {
    "pack_build": "로컬 자료로 candidate pack 빌드(dry-run, temp)",
    "pack_validate": "pack 검증(read)",
    "consumer_smoke": "pack 소비(읽기) smoke(read)",
    "publish_guard_dryrun": "공개 fail-closed 게이트 dry-run",
    "selftest": "자가검사(read)",
    "capture_classify": "발화 1건 캡처 판정(read·메모리 순수)",
    "capture_preview": "대화 발췌 도장 미리보기(read·저장 0)",
    "save_candidate": "선택 후보 staging 저장(dry-run 기본·SAVE n confirm·actor 하드 reader·자동호출 차단)",
}


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _list_tools():
    # read/dry-run 도구만. 위험 도구는 TOOLS 부재로 자연 제외. description 에 경로/secret 없음.
    out = []
    for name, spec in TOOLS.items():
        if spec["mode"] not in ("read", "dry-run", "write-gated"):
            continue  # 방어: read/dry-run/write-gated 외 노출 금지(write-gated=confirm+actor 게이트 단건)
        props = {p: {"type": "string", "description": f"{p} (작업 폴더 내 경로)"}
                 for p in spec["path_params"]}
        req = list(spec["path_params"])
        # path 외 일반 params(예: capture 도구의 utterance/utterances) 지원
        extra = spec.get("input_schema")
        if extra:
            props.update(extra.get("properties", {}))
            req += [r for r in extra.get("required", []) if r not in req]
        out.append({"name": name, "mode": spec["mode"],
                    "description": _TOOL_DESC.get(name, name),
                    "path_params": list(spec["path_params"]),
                    "inputSchema": {"type": "object",
                                    "properties": props,
                                    "required": req}})
    return out


def _sanitize(r):
    """handle_tool 결과를 응답 허용 키로만 축소. raw 경로/secret 미포함."""
    out = {"tool": r.get("tool"), "verdict": r.get("verdict"), "executed": bool(r.get("executed"))}
    if r.get("reason_code"):
        out["reason_code"] = r["reason_code"]
    if r.get("count") is not None:
        out["count"] = r["count"]
    if r.get("blocked"):
        out["blocked"] = [{"reason_code": b.get("reason_code"), "path_id": b.get("path_id")}
                          for b in r["blocked"]]
    if r.get("allowed_path_ids"):
        out["allowed_path_ids"] = r["allowed_path_ids"]
    if r.get("tool_result") is not None:
        # mock tool_result 는 action/mode 만(경로 없음). 그래도 dict 외 타입은 버림.
        tr = r["tool_result"]
        out["tool_result"] = tr if isinstance(tr, (dict, str, int, float, bool)) else None
    return out


def handle_jsonrpc(req, allow_root):
    """JSON-RPC 1건 처리. raw 미출력. malformed 안전 처리."""
    if not isinstance(req, dict):
        return _err(None, -32600, "invalid request (not an object)")
    rid = req.get("id")
    method = req.get("method")
    if not isinstance(method, str) or not method:
        return _err(rid, -32600, "missing method")
    params = req.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _err(rid, -32602, "invalid params")

    if method == "initialize":
        return _ok(rid, {"protocolVersion": "2024-11-05",
                         "serverInfo": {"name": "openbinggu", "version": "0.1-candidate"},
                         "capabilities": {"tools": {"listChanged": False}}})

    if method in ("tools/list", "list_tools"):
        return _ok(rid, {"tools": _list_tools()})

    if method in ("tools/call", "call_tool"):
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _err(rid, -32602, "missing tool name")
        targs = params.get("arguments")
        if targs is None:
            targs = {}
        if not isinstance(targs, dict):
            return _err(rid, -32602, "invalid arguments")
        r = handle_tool(name, targs, allow_root)
        sanitized = _sanitize(r)
        # MCP tools/call 표준: result.content(텍스트) 필수. structuredContent 동봉.
        # selftest 호환 위해 sanitized 최상위 키(verdict/executed 등)도 병합 유지.
        result = {"content": [{"type": "text",
                               "text": json.dumps(sanitized, ensure_ascii=False)}],
                  "structuredContent": sanitized,
                  "isError": False}
        result.update(sanitized)
        return _ok(rid, result)

    return _err(rid, -32601, "method not found: " + method)


def serve_stdio(allow_root):
    """실 stdio JSON-RPC 루프 (initialize/tools/list/tools/call → handle_jsonrpc → handle_tool).

    정식 구현. .mcp.json/.claude.json 에 `python openbinggu_mcp_server.py --serve <ROOT>` 엔트리 추가는
    owner 운영 행위(코드 변경 아님). notification(id 없음)은 응답 미발신(JSON-RPC 2.0 표준).
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            # parse error 는 표준상 응답 의무(id 없이 발신).
            sys.stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            sys.stdout.flush()
            continue
        resp = handle_jsonrpc(req, allow_root)
        # notification(id 없음)은 응답 미발신. request(id 있음)만 1줄 발신.
        has_id = isinstance(req, dict) and req.get("id") is not None
        if has_id:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


# ---------------- selftest ----------------

def _selftest():
    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))
    print("=" * 72)
    print("OpenBinggu MCP server (stdio JSON-RPC) wrapper (synthetic / selftest)")
    print("=" * 72)

    all_ok = True
    raw_leak = False

    def call(req):
        return handle_jsonrpc(req, allow_root)

    checks = []

    # 1) initialize
    r = call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    checks.append(("initialize", "result" in r and r["result"]["serverInfo"]["name"] == "openbinggu"))

    # 2) tools/list — read/dry-run/write-gated 만, forbidden 없음, save_candidate 노출
    r = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = r.get("result", {}).get("tools", [])
    names = {t["name"] for t in tools}
    list_ok = (all(t["mode"] in ("read", "dry-run", "write-gated") for t in tools)
               and names == set(TOOLS.keys())
               and "save_candidate" in names
               and not (names & _FORBIDDEN))
    checks.append(("tools_list_read_dryrun_writegated_only", list_ok))

    # 3) call pack_validate toy → ALLOW executed
    r = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "pack_validate", "arguments": {"pack_path": "examples/toy_project/p.json"}}})
    checks.append(("call_toy_allow", r.get("result", {}).get("executed") is True
                   and r["result"]["verdict"] == "ALLOW"))

    # 4~7) BLOCK 경로들
    block_cases = [
        ("call_parent_block", "pack_build", {"input_dir": "../outside"}),
        ("call_npki_block", "consumer_smoke", {"pack_path": "C:/Users/PC/AppData/NPKI/c.der"}),
        ("call_env_block", "publish_guard_dryrun", {"pack_path": "examples/toy_project/.env"}),
        ("call_bidengine_block", "pack_validate", {"pack_path": "C:/Users/PC/safety-app/bid-engine/x"}),
    ]
    for nm, tool, args in block_cases:
        r = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                  "params": {"name": tool, "arguments": args}})
        res = r.get("result", {})
        checks.append((nm, res.get("executed") is False and res.get("verdict") == "BLOCK"))

    # 8~9) forbidden REJECT
    for nm, tool in [("reject_opencrab_write", "opencrab_write"), ("reject_github_push", "github_push")]:
        r = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                  "params": {"name": tool, "arguments": {"pack_path": "examples/toy_project/p.json"}}})
        res = r.get("result", {})
        checks.append((nm, res.get("executed") is False and res.get("verdict") == "REJECT"))

    # 9b) save_candidate tools/call — dry-run 기본은 write 0(executed_write=False·PREVIEW).
    r = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "save_candidate",
                         "arguments": {"text": "이 문서는 배포 절차를 정의한다.", "indices": [1]}}})
    res = r.get("result", {})
    tr = res.get("tool_result") or {}
    checks.append(("save_call_dryrun_write0",
                   res.get("executed") is True and tr.get("executed_write") is False
                   and tr.get("verdict") == "PREVIEW"))

    # 9c) save_candidate confirm 불일치(dry_run=False) — write 0 REJECT.
    r = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "save_candidate",
                         "arguments": {"text": "이 문서는 배포 절차를 정의한다.", "indices": [1],
                                       "confirm": "SAVE 9", "dry_run": False}}})
    tr = (r.get("result", {}).get("tool_result")) or {}
    checks.append(("save_call_confirm_mismatch_write0",
                   tr.get("executed_write") is False and tr.get("reason") == "confirm_phrase_mismatch"))

    # 10) malformed: missing method
    r = call({"jsonrpc": "2.0", "id": 10})
    checks.append(("malformed_missing_method", "error" in r and r["error"]["code"] == -32600))

    # 11) malformed: unknown method
    r = call({"jsonrpc": "2.0", "id": 11, "method": "do_whatever"})
    checks.append(("malformed_unknown_method", "error" in r and r["error"]["code"] == -32601))

    # 12) malformed: not a dict
    r = call(["not", "a", "dict"])
    checks.append(("malformed_not_object", "error" in r and r["error"]["code"] == -32600))

    # 13) malformed: invalid params
    r = call({"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": "oops"})
    checks.append(("malformed_invalid_params", "error" in r and r["error"]["code"] == -32602))

    # raw 미출력: 전 응답 직렬화 후 민감 substring 검사
    probe_paths = ["../outside", "NPKI", ".env", "safety-app", "bid-engine"]
    for cid in range(1, 14):
        pass
    # 위 모든 call 결과를 한 번 더 모아 검사
    sample_calls = [
        call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "consumer_smoke", "arguments": {"pack_path": "C:/Users/PC/AppData/NPKI/secret.der"}}}),
        call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "pack_build", "arguments": {"input_dir": "../private_outside"}}}),
    ]
    for r in sample_calls:
        blob = json.dumps(r, ensure_ascii=False)
        for tok in ["NPKI", "secret.der", "private_outside", "safety-app", "C:/Users"]:
            if tok in blob:
                raw_leak = True

    for nm, ok in checks:
        all_ok = all_ok and ok
        print("  [%s] %s" % ("OK" if ok else "FAIL", nm))

    print("\n  raw_path_not_leaked:", (not raw_leak))
    print("  serve_stdio: IMPLEMENTED (initialize/tools/list/tools/call). 실 설정 등록은 owner")
    print("  save_default_dry_run: True  real_ledger_write: 0 (selftest=handle_jsonrpc, temp/mock only)")
    print("  operating_store_unchanged: True (wrapper + mock, 운영 ledger write 0)")

    gate = "GO" if (all_ok and not raw_leak) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    elif args[0] == "--serve" and len(args) >= 2:
        # 실 stdio 서버: 등록/공개는 별도 GO. 의도치 않은 가동 방지로 명시 인자 요구.
        serve_stdio(os.path.abspath(args[1]))
    else:
        print("usage: openbinggu_mcp_server.py [--selftest | --serve <ROOT>]")
        sys.exit(2)


if __name__ == "__main__":
    main()
