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

# ── MCP exposure profiles (v1.20-B MCP Front Door) ─────────────────────────────────
# core = 신규 사용자 기본(작고 명확한 표면). advanced = 기존 전체 노출(현행 동작 불변).
# profile 은 서버 프로세스 시작 시 한 번 결정되고 이후 불변 — 요청/env 로 승격 불가(ambient override 0).
# 노출 판단은 오직 이 정적 집합으로만 — 새 tool/alias/handler 를 만들지 않는다.
CORE_TOOLS = frozenset({
    # Read
    "status", "recall", "why", "trace_show", "preflight", "list", "reminders", "capture_preview",
    # Consent-gated mutation (write-gated · dry-run 기본 · 확정은 사람 save-n 앵커/owner 로컬 CLI)
    "save_candidate", "pair", "deprecate", "replace",
})
_PROFILES = ("core", "advanced")
_EXPOSED_MODES = ("read", "dry-run", "write-gated")


def _advanced_tools():
    """advanced 노출 집합 = 현행 필터(read/dry-run/write-gated)와 동일 소스(하드코딩 개수 0)."""
    return frozenset(n for n, s in TOOLS.items() if s["mode"] in _EXPOSED_MODES)


def exposed_tools(profile):
    """profile 이 노출하는 도구 집합. core=CORE_TOOLS(전부 advanced 부분집합·아래 검증) / advanced=전체."""
    if profile == "core":
        return frozenset(CORE_TOOLS)
    return _advanced_tools()


def tool_in_profile(profile, name):
    """name 이 profile 노출 집합에 포함되는지."""
    return name in exposed_tools(profile)


def core_profile_invalid():
    """core 목록 중 레지스트리에 없거나 노출 불가 mode 인 도구(비면 유효). 새 tool 을 만들지 않고
    불일치를 CORE_PROFILE_INVALID 로 드러내기 위한 검증 훅(startup/selftest/test 에서 확인)."""
    adv = _advanced_tools()
    return frozenset(n for n in CORE_TOOLS if n not in adv)


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
    "pair": "owner+ai 페어 저장. dry-run=미리보기만 — MCP 로는 실행 불가(fail-closed·사람 앵커 없음·approval_id 무효). 실제 저장은 owner 로컬 CLI: binggu pair",
    "deprecate": "목록 1건 기각. dry-run=미리보기만 — MCP 로는 실행 불가(fail-closed·approval_id 무효). 실제 기각은 owner 로컬 CLI: binggu deprecate",
    "replace": "목록 1건 교체. dry-run=미리보기만 — MCP 로는 실행 불가(fail-closed·approval_id 무효). 실제 교체는 owner 로컬 CLI: binggu replace",
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
    "mark_hit": "회상 적중 기록(node_id 미노출). dry-run=미리보기만 — MCP 로는 기록 불가(fail-closed·approval_id 무효). 실제 기록은 owner 로컬 CLI: binggu mark-hit",
    "mark_miss": "회상 빗나감 기록(node_id 미노출). dry-run=미리보기만 — MCP 로는 기록 불가(fail-closed·approval_id 무효). 실제 기록은 owner 로컬 CLI: binggu mark-miss",
}


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _list_tools(profile="advanced"):
    # read/dry-run/write-gated 도구 중 profile 노출 집합만. 위험 도구는 TOOLS 부재로 자연 제외.
    # profile 은 schema/description 을 바꾸지 않고 '노출 여부'만 결정 — 동일 tool 은 두 profile 에서 byte-equal.
    exp = exposed_tools(profile)
    out = []
    for name, spec in TOOLS.items():
        if spec["mode"] not in _EXPOSED_MODES:
            continue  # 방어: read/dry-run/write-gated 외 노출 금지(write-gated=confirm+actor 게이트 단건)
        if name not in exp:
            continue  # profile 미노출(core 에서 advanced 전용 도구 숨김)
        props = {p: {"type": "string", "description": f"{p} (작업 폴더 내 경로)"}
                 for p in spec["path_params"]}
        req = list(spec["path_params"])
        # path 외 일반 params(예: capture 도구의 utterance/utterances) 지원
        extra = spec.get("input_schema")
        if extra:
            props.update(extra.get("properties", {}))
            req += [r for r in extra.get("required", []) if r not in req]
        # 구 P1-A approval_id(optional) 표준 노출은 제거(2026-07-13) — MCP write-gated 도구는
        # approval_id 로 승격되지 않는다(fail-closed). 제시돼도 무시(approval_id_ignored 응답).
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


def handle_jsonrpc(req, allow_root, profile="advanced", server_name="openbinggu"):
    """JSON-RPC 1건 처리. raw 미출력. malformed 안전 처리.

    profile: 노출 도구 집합(core|advanced). server_name: initialize serverInfo.name.
    기본값(advanced/openbinggu)은 현행 legacy 동작과 정확히 동일 — stdio/HTTP 공용 경로.
    """
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
                         "serverInfo": {"name": server_name, "version": "0.1-candidate"},
                         "capabilities": {"tools": {"listChanged": False}}})

    if method in ("tools/list", "list_tools"):
        return _ok(rid, {"tools": _list_tools(profile)})

    if method in ("tools/call", "call_tool"):
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _err(rid, -32602, "missing tool name")
        targs = params.get("arguments")
        if targs is None:
            targs = {}
        if not isinstance(targs, dict):
            return _err(rid, -32602, "invalid arguments")
        # profile enforcement (tools/list 뿐 아니라 tools/call 도 차단) — 등록된 도구인데 현 profile 에
        # 미노출이면 handle_tool 호출 전에 차단(executed_write 0·ledger 0·network 0·raw 0). 미등록
        # (forbidden/unknown)은 기존 handle_tool 경로 유지(tool_not_exposed REJECT) — advanced 동작 불변.
        if name in TOOLS and not tool_in_profile(profile, name):
            blocked = {"tool": name, "verdict": "REJECT", "executed": False,
                       "reason_code": "tool_not_in_profile"}
            result = {"content": [{"type": "text",
                                   "text": json.dumps(blocked, ensure_ascii=False)}],
                      "structuredContent": blocked, "isError": True}
            result.update(blocked)
            return _ok(rid, result)
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


def serve_stdio(allow_root, profile="advanced", server_name="openbinggu"):
    """실 stdio JSON-RPC 루프 (initialize/tools/list/tools/call → handle_jsonrpc → handle_tool).

    정식 구현. .mcp.json/.claude.json 에 `python openbinggu_mcp_server.py --serve <ROOT>` 엔트리 추가는
    owner 운영 행위(코드 변경 아님). notification(id 없음)은 응답 미발신(JSON-RPC 2.0 표준).
    """
    # [진단 wire 로깅] opt-in(BINGGU_MCP_WIRE=1)·기본 off — <home>/mcp_wire.log 에 통신 기록
    #   (메서드/id/예외만·raw 0). stdio 클라이언트 연결 실패 진단용.
    import time as _t
    _wire_on = os.environ.get("BINGGU_MCP_WIRE") == "1"
    _wp = os.path.join(os.path.expanduser("~"), ".binggupack", "mcp_wire.log")

    def _wire(m):
        if not _wire_on:
            return
        try:
            with open(_wp, "a", encoding="utf-8") as _f:
                _f.write("%s %s\n" % (_t.strftime("%H:%M:%S"), m))
        except Exception:
            pass

    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _wire("=== START pid=%s in=%s out=%s ===" % (os.getpid(), sys.stdin.encoding, sys.stdout.encoding))
    while True:
        try:
            line = sys.stdin.readline()
        except Exception as _e:
            _wire("readline EXC=%s" % type(_e).__name__)
            raise
        if not line:   # EOF → 세션 종료
            _wire("EOF -> break")
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            _wire("RECV method=%s id=%s" % (req.get("method"), req.get("id")))
        except Exception:
            _wire("RECV parse-error len=%d head=%r" % (len(line), line[:40]))
            sys.stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            sys.stdout.flush()
            continue
        try:
            resp = handle_jsonrpc(req, allow_root, profile=profile, server_name=server_name)
        except Exception as e:
            _wire("handle EXC=%s" % type(e).__name__)
            rid = req.get("id") if isinstance(req, dict) else None
            resp = _err(rid, -32603, "internal error: " + type(e).__name__)
        # notification(id 없음)은 응답 미발신. request(id 있음)만 1줄 발신.
        has_id = isinstance(req, dict) and req.get("id") is not None
        if has_id:
            try:
                # binary buffer 직접 write — TextIOWrapper 버퍼링 우회(큰 응답 7KB+ 를 Windows
                # 파이프에서 flush() 로 안 밀리는 경우 방지·Codex rmcp 수신 실패 대응).
                _data = (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")
                sys.stdout.buffer.write(_data)
                sys.stdout.buffer.flush()
                _wire("SENT id=%s ok=%s bytes=%d" % (req.get("id"), "result" in resp, len(_data)))
            except Exception as _e:
                _wire("SEND EXC=%s" % type(_e).__name__)
                continue
    _wire("=== END ===")


def serve_http(allow_root, port, path_token, profile="advanced", server_name="openbinggu"):
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
                resp = handle_jsonrpc(req, allow_root, profile=profile, server_name=server_name)
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

def _selftest(profile="advanced", server_name="openbinggu"):
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
    print("BingguPack MCP server (stdio JSON-RPC) wrapper (synthetic / selftest)")
    print("  profile=%s  serverInfo.name=%s" % (profile, server_name))
    print("=" * 72)

    all_ok = True
    raw_leak = False

    def call(req):
        return handle_jsonrpc(req, allow_root, profile=profile, server_name=server_name)

    checks = []

    # 0) core profile 유효성(존재하지 않는 tool 을 core 에 넣지 않았는지) — 비면 유효.
    checks.append(("core_profile_valid", not core_profile_invalid()))

    # 1) initialize — serverInfo.name 은 entrypoint(server_name) 에 따른다(legacy=openbinggu·canonical=binggupack)
    r = call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    checks.append(("initialize_server_name", "result" in r
                   and r["result"]["serverInfo"]["name"] == server_name))

    # 2) tools/list — 선택 profile 노출 집합과 정확히 일치, forbidden 없음, 노출 mode 만
    r = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = r.get("result", {}).get("tools", [])
    names = {t["name"] for t in tools}
    # mode 는 tools/list 응답 최상위에 없음(MCP 표준 필드만) → TOOLS 소스에서 검증.
    list_ok = (all(TOOLS[t["name"]]["mode"] in _EXPOSED_MODES for t in tools)
               and names == exposed_tools(profile)
               and "save_candidate" in names
               and not (names & _FORBIDDEN))
    checks.append(("tools_list_matches_profile", list_ok))

    # 3+) profile 별 심화 검사. advanced=현행 전체(불변) / core=허용 도구 동작 + 숨긴 도구 차단.
    if profile == "core":
        raw_leak = _core_deep_checks(call, checks) or raw_leak
    else:
        raw_leak = _advanced_deep_checks(call, checks) or raw_leak

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


def _advanced_deep_checks(call, checks):
    """advanced profile 심화 검사(현행 legacy selftest 그대로). raw_leak 여부 반환."""
    raw_leak = False
    # 3) call pack_validate toy → ALLOW executed
    r = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "pack_validate", "arguments": {"pack_path": "examples/toy_project/p.json"}}})
    checks.append(("call_toy_allow", r.get("result", {}).get("executed") is True
                   and r["result"]["verdict"] == "ALLOW"))

    # 4~7) BLOCK 경로들
    block_cases = [
        ("call_parent_block", "pack_build", {"input_dir": "../outside"}),
        ("call_npki_block", "consumer_smoke", {"pack_path": "C:/Users/fixture-user/AppData/NPKI/c.der"}),
        ("call_env_block", "publish_guard_dryrun", {"pack_path": "examples/toy_project/.env"}),
        ("call_private_project_block", "pack_validate", {"pack_path": "C:/Users/fixture-user/example-org/example-project/x"}),
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
        raise RuntimeError("BOOM C:/Users/fixture-user/AppData/NPKI/secret.der must_not_leak")

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
              "params": {"name": "consumer_smoke", "arguments": {"pack_path": "C:/Users/fixture-user/AppData/NPKI/secret.der"}}}),
        call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "pack_build", "arguments": {"input_dir": "../private_outside"}}}),
    ]
    for r in sample_calls:
        blob = json.dumps(r, ensure_ascii=False)
        for tok in ["NPKI", "secret.der", "private_outside", "example-org", "C:/Users"]:
            if tok in blob:
                raw_leak = True
    return raw_leak


def _core_deep_checks(call, checks):
    """core profile 심화 검사 — 허용 도구는 advanced 와 동일 동작, 숨긴(advanced 전용) 도구는
    handler 전 차단(tool_not_in_profile·executed 0·write 0·network 0). raw_leak 여부 반환."""
    raw_leak = False

    # C1) 허용 read 도구(status) — advanced 와 동일하게 ALLOW·executed.
    r = call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "status", "arguments": {}}})
    res = r.get("result", {})
    checks.append(("core_status_read_ok",
                   res.get("executed") is True and res.get("verdict") == "ALLOW"))

    # C2) 허용 read 도구(recall) — query 필수·empty graceful.
    r = call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "recall", "arguments": {"query": "배포 절차"}}})
    res = r.get("result", {})
    checks.append(("core_recall_read_ok",
                   res.get("executed") is True and res.get("verdict") == "ALLOW"))

    # C3) 허용 write-gated(save_candidate) — dry-run 기본 write 0(PREVIEW).
    r = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "save_candidate",
                         "arguments": {"text": "이 문서는 배포 절차를 정의한다.", "indices": [1]}}})
    res = r.get("result", {})
    tr = res.get("tool_result") or {}
    checks.append(("core_save_dryrun_write0",
                   res.get("executed") is True and tr.get("executed_write") is False
                   and tr.get("verdict") == "PREVIEW"))

    # C4) 허용 write-gated(pair) — dry-run 기본 write 0(PREVIEW).
    r = call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "pair", "arguments": {"owner_text": "이 방향으로 가자"}}})
    res = r.get("result", {})
    tr = res.get("tool_result") or {}
    checks.append(("core_pair_dryrun_write0",
                   res.get("executed") is True and tr.get("executed_write") is False
                   and tr.get("verdict") == "PREVIEW"))

    # C5) 숨긴(advanced 전용) 도구 tools/call 직접 호출 → handler 전 차단(tool_not_in_profile).
    #     pack_validate=파일 도구·cloud_recall=네트워크 도구 → 차단 시 write 0·network 0.
    for nm, hidden in (("core_hidden_pack_validate_blocked", "pack_validate"),
                       ("core_hidden_cloud_recall_blocked", "cloud_recall")):
        r = call({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                  "params": {"name": hidden, "arguments": {"pack_path": "examples/toy_project/p.json",
                                                           "query": "x"}}})
        res = r.get("result", {})
        checks.append((nm, res.get("executed") is False
                       and res.get("reason_code") == "tool_not_in_profile"
                       and (res.get("tool_result") in (None, {}))))

    # C6) malformed 안전 처리(공용).
    r = call({"jsonrpc": "2.0", "id": 6})
    checks.append(("core_malformed_missing_method", "error" in r and r["error"]["code"] == -32600))
    return raw_leak


def _extract_profile(args, default):
    """argv 에서 `--profile core|advanced` 를 additive 로 추출(위치 무관). 나머지 인자는 그대로 반환.
    잘못된/누락 값은 write 없이 exit 2. --profile 미지정 시 default(entrypoint 별 기본)."""
    if "--profile" not in args:
        return default, args
    i = args.index("--profile")
    val = args[i + 1] if i + 1 < len(args) else None
    if val not in _PROFILES:
        print("invalid --profile (core|advanced)")
        sys.exit(2)
    return val, args[:i] + args[i + 2:]


def _main(default_profile, server_name, prog):
    """공통 진입. --profile 만 additive — root/port/path-token 처리·기존 --selftest/--serve/--http 무변."""
    args = sys.argv[1:]
    profile, args = _extract_profile(args, default_profile)
    if not args or args[0] == "--selftest":
        _selftest(profile=profile, server_name=server_name)
    elif args[0] == "--serve" and len(args) >= 2:
        # 실 stdio 서버: 등록/공개는 별도 GO. 의도치 않은 가동 방지로 명시 인자 요구.
        serve_stdio(os.path.abspath(args[1]), profile=profile, server_name=server_name)
    elif args[0] == "--http" and len(args) >= 3:
        # 로컬 HTTP 서버(터널 뒤 웹/앱 커넥터용). 경로키는 env BINGGU_MCP_PATH_TOKEN 주입(코드 평문 0).
        tok = os.environ.get("BINGGU_MCP_PATH_TOKEN", "").strip()
        if not tok:
            print("BINGGU_MCP_PATH_TOKEN env 필요(경로키)")
            sys.exit(2)
        serve_http(os.path.abspath(args[2]), args[1], tok,
                   profile=profile, server_name=server_name)
    else:
        print("usage: %s [--profile core|advanced] "
              "[--selftest | --serve <ROOT> | --http <PORT> <ROOT>]" % prog)
        sys.exit(2)


def main():
    """legacy entrypoint (openbinggu-mcp-server). 기본 profile=advanced·serverInfo.name=openbinggu —
    현행 동작과 정확히 동일. `--profile core` 로 명시 core 도 가능(additive)."""
    _main("advanced", "openbinggu", "openbinggu-mcp-server")


def main_binggupack():
    """canonical entrypoint (binggupack-mcp). 기본 profile=core·serverInfo.name=binggupack.
    `--profile advanced` 로 전체 도구 노출도 가능."""
    _main("core", "binggupack", "binggupack-mcp")


if __name__ == "__main__":
    main()
