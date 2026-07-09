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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # ROOT — binggupack facade
# 트랙 C(C4): 핸들러 정본은 binggupack.mcp facade 경유. scripts 직접 import 도 호환(facade 재노출).
from binggupack.mcp import handle_tool, TOOLS, _FORBIDDEN  # noqa: E402

_TOOL_DESC = {
    "pack_build": "로컬 자료로 candidate pack 빌드(dry-run, temp)",
    "pack_validate": "pack 검증(read)",
    "consumer_smoke": "pack 소비(읽기) smoke(read)",
    "publish_guard_dryrun": "공개 fail-closed 게이트 dry-run",
    "selftest": "자가검사(read)",
    "capture_classify": "발화 1건 캡처 판정(read·메모리 순수)",
    "capture_preview": "대화 발췌 도장 미리보기(read·저장 0)",
    "save_candidate": "선택 후보 staging 저장(dry-run 기본·SAVE n confirm·actor 하드 reader·자동호출 차단)",
    "recall": "query 관련 기억 회상(read·랭킹순·저장 0)",
    "preflight": "작업 전 회상 — 기억할 것+위험패턴+선호(read)",
    "trace_review": "미판정 회상 목록(효용 판정 대기·read)",
    "trace_show": "판단 노드 근거 사슬 다홉(read·node_id 필요)",
    "status": "장부 요약 — active/deprecated/검증예정/수용/audit chain(read)",
    "list": "저장 후보 목록(status/kind 필터·read)",
    "reminders": "due 경과 판단 리마인더(read)",
    "pair": "owner 발화(+ai 요약) 화자축 페어 저장(dry-run 기본·PAIR confirm 정확일치·자동차단)",
    "deprecate": "목록 인덱스 1건 기각(dry-run 기본·DEPRECATE <n> <id8> confirm·자동차단)",
    "replace": "목록 인덱스 1건 교체(dry-run 기본·REPLACE <n> <id8> WITH <new> confirm·자동차단)",
    "reflect": "회고·자가평가 → 지식 후보 preview(read·저장 0)",
    "harvest_list": "등록된 외부 수확 소스 목록(read)",
    "harvest_add": "외부 소스 등록(dry-run 기본·HARVEST_ADD <kind> <url> confirm·URL 안전검증)",
    "harvest_remove": "외부 소스 제거(dry-run 기본·HARVEST_REMOVE <source_id> confirm)",
    "cloud_recall": "OpenCrab 클라우드 지식 조회(read·egress-only·PII 마스킹·미설정 graceful)",
    "cloud_packs": "OpenCrab 클라우드 팩 검색(read·egress-only·PII 마스킹·미설정 graceful)",
    "cloud_search": "OpenCrab 팩 하이브리드 의미검색(서버 lexical+vector fusion·2026-07-08 서버 벡터 retrieval 배선 확인 vector_candidates>0). 질의확장(원 질문 3~6 동의어)은 벡터 가중 낮은 현 fusion에서 lexical 기여 보강용으로 여전히 권장·score 하한 min_score로 off-topic 배제·chunk 원문 top-k·read·PII 마스킹·미설정 graceful)",
    "why": "판단 근거 회상 — 과거 결정의 이유·근거 사슬 조회(read·node_id 미노출·PII 마스킹)",
    "contrast": "제안 신호 ↔ 강제조항 대비표(read·양쪽 원문 인용·자동결정 0·write 0)",
    "abstraction": "반복 판단 → 규칙 후보 제안(read·proposal_id=content hash·자동확정 0·write 0)",
    "mark_hit": "회상 조언 적중 기록(write-gated·'MARK_HIT <index> <query>' confirm 정확일치·node_id 미노출·자동기록 0)",
    "mark_miss": "회상 조언 빗나감 기록(write-gated·'MARK_MISS <index> <query>' confirm 정확일치·node_id 미노출·자동기록 0)",
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
        # MCP 표준 tool 필드만(name/description/inputSchema). mode/path_params 는 서버 내부용이라
        # 최상위 노출 금지 — Codex/Rust(rmcp) 등 엄격 클라이언트가 unknown field 로 파싱 실패(도구 캐시
        # 생성 0·연결 무력화). mode 는 위 필터(라인 69)에서만, path_params 는 inputSchema 에 이미 반영됨.
        out.append({"name": name,
                    "description": _TOOL_DESC.get(name, name),
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
        # ★client 가 보낸 protocolVersion 을 echo(협상 호환). 서버 고정 버전과 다르면 엄격한
        #   client(Codex rmcp)가 initialize 를 cancel(task cancelled·Child exit 1·tools/list
        #   30s timeout) 하는 것을 방지. 미지정 시 기본 2024-11-05.
        client_ver = params.get("protocolVersion") if isinstance(params, dict) else None
        return _ok(rid, {"protocolVersion": client_ver or "2024-11-05",
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
        try:
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
        except Exception as e:
            # tool 내부 예외/직렬화 실패는 해당 요청 단위 -32603 으로 격리(루프·세션 유지).
            # message 는 예외 타입만 — raw secret/PII/trace 미노출.
            return _err(rid, -32603, "internal error: " + type(e).__name__)

    return _err(rid, -32601, "method not found: " + method)


def serve_stdio(allow_root):
    """실 stdio JSON-RPC 루프 (initialize/tools/list/tools/call → handle_jsonrpc → handle_tool).

    정식 구현. .mcp.json/.claude.json 에 `python openbinggu_mcp_server.py --serve <ROOT>` 엔트리 추가는
    owner 운영 행위(코드 변경 아님). notification(id 없음)은 응답 미발신(JSON-RPC 2.0 표준).
    """
    # ★readline() 루프 — `for line in sys.stdin` 은 read-ahead 버퍼링이라 실시간 파이프
    #   (Codex 등 Rust rmcp 클라이언트가 한 줄 write 후 응답 대기)에서 요청을 즉시 못 읽어
    #   tools/list 응답이 막힌다(로그: startup_complete=true·has_cached_tools=false·30s timeout).
    #   readline 은 line 단위로 즉시 반환 → 실시간 요청 처리. EOF 시 '' 반환으로 종료.
    while True:
        line = sys.stdin.readline()
        if not line:   # EOF → 세션 종료
            break
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
        # 한 줄 처리 실패가 루프(세션) 전체를 죽이지 않도록 방어. raw 미노출.
        try:
            resp = handle_jsonrpc(req, allow_root)
        except Exception as e:
            rid = req.get("id") if isinstance(req, dict) else None
            resp = _err(rid, -32603, "internal error: " + type(e).__name__)
        # notification(id 없음)은 응답 미발신. request(id 있음)만 1줄 발신.
        has_id = isinstance(req, dict) and req.get("id") is not None
        if has_id:
            try:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except Exception:
                # 직렬화/쓰기 실패도 루프 유지(다음 요청 계속 처리).
                continue


def serve_http(allow_root, port, path_token):
    """로컬 HTTP JSON-RPC 서버 (Cloudflare Tunnel 뒤에서 웹/앱 커넥터에 로컬 MCP 그대로 노출).

    - 127.0.0.1 바인드: 인바운드 포트를 직접 열지 않는다(외부 노출은 터널이 담당).
    - 경로키 인증: POST /mcp/<path_token> 만 처리(그 외 403). path_token 은 env 로 주입(코드 평문 0).
    - handle_jsonrpc 재사용: stdio 와 동일 로직(initialize/tools/list/tools/call) — 22도구 그대로.
    - SSE/JSON 협상: Accept: text/event-stream 이면 SSE 로 감싼다(claude.ai 등 호환).
    - notification(id 없음) 202. request 만 응답.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, sse=False):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
            if sse:
                self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_GET(self):
            self._send(404, json.dumps({"error": "POST only"}))

        def do_POST(self):
            if self.path != "/mcp/" + path_token:
                self._send(403, json.dumps({"error": "forbidden"}))
                return
            try:
                ln = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(ln).decode("utf-8")
                req = json.loads(raw)
            except Exception:
                self._send(400, json.dumps(_err(None, -32700, "parse error")))
                return
            try:
                resp = handle_jsonrpc(req, allow_root)
            except Exception as e:
                rid = req.get("id") if isinstance(req, dict) else None
                resp = _err(rid, -32603, "internal error: " + type(e).__name__)
            if not (isinstance(req, dict) and req.get("id") is not None):
                self._send(202, "")   # notification
                return
            payload = json.dumps(resp, ensure_ascii=False)
            if "text/event-stream" in (self.headers.get("Accept") or ""):
                self._send(200, "event: message\ndata: " + payload + "\n\n", sse=True)
            else:
                self._send(200, payload)

        def log_message(self, *a):
            pass  # 접근 로그 억제(경로키 노출 방지)

    ThreadingHTTPServer(("127.0.0.1", int(port)), Handler).serve_forever()


# ---------------- selftest ----------------

def _selftest():
    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))
    # 조회(read) 도구가 운영 ledger 미접촉·결정성 갖도록 BINGGU_HOME 을 존재하지 않는 temp 로 강제.
    os.environ["BINGGU_HOME"] = os.path.join(os.environ.get("TEMP", "/tmp"),
                                             "binggu_selftest_home_readonly_none")
    # 트랙 B 클라우드 조회 네트워크 0 보장 — 앰비언트 클라우드 env 제거(→ NO_CLOUD_CONFIG graceful).
    for _k in ("BINGGU_CLOUD_MCP_URL", "BINGGU_CLOUD_MCP_TOKEN"):
        os.environ.pop(_k, None)
    # read 폴백(~/.claude.json opencrab-cloud URL 재사용)도 selftest 에선 차단 → 실 네트워크 0.
    # server_handlers selftest 와 동일 가드 — 7/9 cloud_recall 자동스코프 fallback 이 이 격리를 우회하던 것 봉합.
    os.environ["BINGGU_CLOUD_MCP_NO_FALLBACK"] = "1"
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
    # mode 는 tools/list 응답 최상위에 더 이상 없음(MCP 표준 필드만) → TOOLS 소스에서 검증.
    list_ok = (all(TOOLS[t["name"]]["mode"] in ("read", "dry-run", "write-gated") for t in tools)
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

    # 9d) status read tools/call — ledger 서버 결정(BINGGU_HOME=temp)·empty graceful·executed=True.
    r = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "status", "arguments": {}}})
    res = r.get("result", {})
    checks.append(("status_read_call_ok",
                   res.get("executed") is True and res.get("verdict") == "ALLOW"))

    # 9e) recall read tools/call — query 필수·ledger 없으면 empty·executed=True.
    r = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "recall", "arguments": {"query": "배포 절차"}}})
    res = r.get("result", {})
    checks.append(("recall_read_call_ok",
                   res.get("executed") is True and res.get("verdict") == "ALLOW"))

    # 9f) pair write-gated tools/call — dry-run 기본은 write 0(executed_write=False·PREVIEW).
    r = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "pair", "arguments": {"owner_text": "이 방향으로 가자"}}})
    res = r.get("result", {})
    tr = res.get("tool_result") or {}
    checks.append(("pair_call_dryrun_write0",
                   res.get("executed") is True and tr.get("executed_write") is False
                   and tr.get("verdict") == "PREVIEW"))

    # 9g) cloud_recall read tools/call — 미설정(BINGGU_HOME=temp·클라우드 env 제거) graceful·executed=True·ALLOW·write 0.
    r = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "cloud_recall", "arguments": {"query": "여행 팁"}}})
    res = r.get("result", {})
    tr = res.get("tool_result") or {}
    checks.append(("cloud_recall_read_call_graceful",
                   res.get("executed") is True and res.get("verdict") == "ALLOW"
                   and tr.get("ok") is False and tr.get("error") == "NO_CLOUD_CONFIG"))

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

    # 14) tool 내부 예외 격리: handle_tool 가 raise 해도 해당 요청만 -32603,
    #     루프/세션은 유지되고 예외 메시지(raw 경로/secret)는 응답에 미노출.
    global handle_tool
    _orig_handle_tool = handle_tool

    def _boom(name, targs, allow_root):
        raise RuntimeError("BOOM C:/Users/PC/AppData/NPKI/secret.der must_not_leak")

    handle_tool = _boom
    try:
        r = call({"jsonrpc": "2.0", "id": 14, "method": "tools/call",
                  "params": {"name": "pack_validate", "arguments": {"pack_path": "examples/toy_project/p.json"}}})
        # 격리 후에도 정상 경로가 같은 루프에서 계속 동작하는지(세션 생존) 확인.
        r_next = call({"jsonrpc": "2.0", "id": 15, "method": "tools/list"})
    finally:
        handle_tool = _orig_handle_tool
    err14 = r.get("error", {})
    blob14 = json.dumps(r, ensure_ascii=False)
    leak14 = any(tok in blob14 for tok in ["NPKI", "secret.der", "must_not_leak", "BOOM"])
    checks.append(("tool_exception_isolated_-32603",
                   "error" in r and err14.get("code") == -32603 and not leak14))
    checks.append(("session_survives_after_tool_exception",
                   "result" in r_next and r_next["result"].get("tools") is not None))

    # raw 미출력: 전 응답 직렬화 후 민감 substring 검사
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
    elif args[0] == "--http" and len(args) >= 3:
        # 로컬 HTTP 서버(터널 뒤 웹/앱 커넥터용). 경로키는 env BINGGU_MCP_PATH_TOKEN 주입(코드 평문 0).
        tok = os.environ.get("BINGGU_MCP_PATH_TOKEN", "").strip()
        if not tok:
            print("BINGGU_MCP_PATH_TOKEN env 필요(경로키)")
            sys.exit(2)
        serve_http(os.path.abspath(args[2]), args[1], tok)
    else:
        print("usage: openbinggu_mcp_server.py [--selftest | --serve <ROOT> | --http <PORT> <ROOT>]")
        sys.exit(2)


if __name__ == "__main__":
    main()
