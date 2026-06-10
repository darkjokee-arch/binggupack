# -*- coding: utf-8 -*-
"""binggupack_http_mcp_skeleton selftest — 실 HTTP E2E (로컬 한정, read-only).

검증: initialize/tools 노출/5 tool 실호출/오류코드/Origin 가드/JSON-only(GET 405)/
  stateless(세션 헤더 0)/write tool 미노출/응답크기/raw 경로·secret·PII 누출 0/
  operating store 불변. 전부 temp toy pack — 실 데이터 0.

유일한 write = tmp/ toy pack + reports/binggupack_http_mcp_skeleton_selftest.json.
"""
import json
import re
import sys
import tempfile
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import binggupack_http_mcp_skeleton as skel
import watcher_op_m0 as m0                       # _store_snapshot (운영 store 불변 검증)
import openbinggu_incoming_to_staging as v011    # SECRET_PATTERNS (누출 스캔)

REPORT = BASE / "reports" / "binggupack_http_mcp_skeleton_selftest.json"
CAPTURED = []   # (이름, status, headers, body_text) — 마지막에 일괄 누출 스캔


def _post(url, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8")
            return r.status, dict(r.headers), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8")


def _get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8")


def _call(url, name, args, rid):
    st, hd, body = _post(url, {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                               "params": {"name": name, "arguments": args}})
    CAPTURED.append(("call:" + name, st, hd, body))
    out = json.loads(body)
    res = out.get("result") or {}
    return st, res, res.get("structuredContent") or {}


def run():
    checks = []

    def ck(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:160]})
        print("  [%s] %-34s %s" % ("OK" if ok else "FAIL", name, str(detail)[:90]))

    store_before = m0._store_snapshot()

    tmp_root = Path(tempfile.mkdtemp(prefix="bgp_mcp_selftest_"))
    skel.make_toy_packs(tmp_root)
    store = skel.PackStore(tmp_root)
    skel.McpHandler.store = store
    httpd = ThreadingHTTPServer((skel.HOST, 0), skel.McpHandler)   # ephemeral port
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = "http://%s:%d%s" % (skel.HOST, port, skel.MCP_PATH)

    try:
        # S1 initialize
        st, hd, body = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": skel.PROTOCOL_VERSION,
                                              "capabilities": {}, "clientInfo": {"name": "selftest"}}})
        CAPTURED.append(("initialize", st, hd, body))
        init = json.loads(body).get("result") or {}
        ck("S1_initialize", st == 200 and init.get("protocolVersion") and "tools" in init.get("capabilities", {}),
           init.get("serverInfo", {}).get("name"))
        # S2 stateless — 세션 헤더 미발급
        ck("S2_stateless_no_session", not any(k.lower() == "mcp-session-id" for k in hd))
        # S3 notification → 202
        st3, hd3, body3 = _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        ck("S3_notification_202", st3 == 202)
        # S4 GET → 405 (JSON-only) / S5 잘못된 path → 404
        st4, _, b4 = _get(url)
        CAPTURED.append(("get_mcp", st4, {}, b4))
        ck("S4_get_405_json_only", st4 == 405)
        st5, _, b5 = _get("http://%s:%d/other" % (skel.HOST, port))
        ck("S5_wrong_path_404", st5 == 404)
        # S6 tools/list — 5 tool + inputSchema object 전건
        st6, hd6, body6 = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        CAPTURED.append(("tools_list", st6, hd6, body6))
        tools = (json.loads(body6).get("result") or {}).get("tools") or []
        names = sorted(tl["name"] for tl in tools)
        ck("S6_tools_list_5", len(tools) == 5, ",".join(names))
        ck("S7_input_schema_all_object",
           all(isinstance(tl.get("inputSchema"), dict) and tl["inputSchema"].get("type") == "object"
               for tl in tools))
        # S8 write 류 tool 미노출
        bad = [n for n in names if re.search(r"write|apply|upload|finalize|confirm|promote|save|delete|merge", n)]
        ck("S8_no_write_tools", not bad, bad)
        # S9 pack_list
        st9, res9, out9 = _call(url, "pack_list", {}, 3)
        pids = [p["pack_id"] for p in out9.get("packs", [])]
        ck("S9_pack_list_2", st9 == 200 and len(pids) == 2 and not res9.get("isError"), ",".join(pids))
        pid = pids[0] if pids else ""
        # S10 pack_summary 정상 + S11 PACK_NOT_FOUND
        _, res10, out10 = _call(url, "pack_summary", {"pack_id": pid}, 4)
        ck("S10_pack_summary", (not res10.get("isError")) and "candidate" in out10.get("candidate_note", "")
           and out10.get("manifest_summary", {}).get("counts"))
        _, res11, out11 = _call(url, "pack_summary", {"pack_id": "toy/nope"}, 5)
        ck("S11_pack_not_found", res11.get("isError") and out11.get("error_code") == "PACK_NOT_FOUND")
        # S12 handoff_context — consumer rules 4줄 + candidate 표기 + 크기 캡
        _, res12, out12 = _call(url, "handoff_context", {"pack_id": pid, "max_nodes": 2}, 6)
        md = out12.get("context_markdown", "")
        rules_ok = all(s in md for s in ("evidence_refs", "추측 생성 금지", "candidate", "자동 병합"))
        ck("S12_handoff_context", (not res12.get("isError")) and rules_ok and md.startswith("# BingguPack"),
           "len=%d" % len(md))
        ck("S13_size_cap", len(json.dumps(out12, ensure_ascii=False)) <= skel.MAX_RESPONSE_CHARS)
        # S14 evidence_search 정상 + S15 QUERY_TOO_SHORT
        _, res14, out14 = _call(url, "evidence_search", {"pack_id": pid, "query": "빌드"}, 7)
        hits = out14.get("hits", [])
        ck("S14_evidence_search", (not res14.get("isError")) and len(hits) >= 1
           and all(len(h["sentence_excerpt"]) <= skel.EXCERPT_MAX and h.get("candidate") for h in hits),
           "hits=%d" % len(hits))
        _, res15, out15 = _call(url, "evidence_search", {"pack_id": pid, "query": "x"}, 8)
        ck("S15_query_too_short", res15.get("isError") and out15.get("error_code") == "QUERY_TOO_SHORT")
        # S16 node_edge_lookup keyword + S17 NOT_FOUND
        _, res16, out16 = _call(url, "node_edge_lookup", {"pack_id": pid, "keyword": "릴리스"}, 9)
        edges = out16.get("edges", [])
        ck("S16_node_edge_lookup", (not res16.get("isError")) and out16.get("node", {}).get("candidate")
           and len(edges) >= 1 and all(e.get("evidence_refs") for e in edges), "edges=%d" % len(edges))
        _, res17, out17 = _call(url, "node_edge_lookup", {"pack_id": pid, "keyword": "없는키워드zz"}, 10)
        ck("S17_node_not_found", res17.get("isError") and out17.get("error_code") == "NODE_NOT_FOUND")
        # S18 unknown tool 거부 + evil Origin 403
        st18, hd18, body18 = _post(url, {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                                         "params": {"name": "pack_write", "arguments": {}}})
        CAPTURED.append(("unknown_tool", st18, hd18, body18))
        ck("S18_unknown_tool_reject", "error" in json.loads(body18))
        st18b, _, _ = _post(url, {"jsonrpc": "2.0", "id": 12, "method": "ping"},
                            headers={"Origin": "https://evil.example.com"})
        ck("S19_evil_origin_403", st18b == 403)
        # S20 누출 스캔 — 캡처 전 응답에서 절대경로/_backup/secret 패턴 0
        joined = "\n".join(c[3] for c in CAPTURED)
        leak_hits = skel._leak_scan(joined)
        secret_hits = [p.pattern for p in v011.SECRET_PATTERNS if p.search(joined)]
        ck("S20_no_raw_leak", not leak_hits and not secret_hits,
           "leak_pat_hits=%d secret_pat_hits=%d" % (len(leak_hits), len(secret_hits)))
        # S21 운영 store 불변
        ck("S21_operating_store_unchanged", store_before == m0._store_snapshot())
    finally:
        httpd.shutdown()
        httpd.server_close()

    n_ok = sum(1 for c in checks if c["ok"])
    gate = "GO" if n_ok == len(checks) else "STOP"
    report = {
        "tool": "binggupack_http_mcp_skeleton_selftest.py",
        "scope": "GO-HOSTED-MCP-SKELETON-LOCAL (로컬 한정 PoC)",
        "bind": skel.HOST, "auth": "none(local-only)", "transport": "streamable-http json-only",
        "tools_exposed": 5, "write_tools": 0, "oauth": 0, "hosted_deploy": 0,
        "opencrab_call": 0, "neo4j_run": 0, "operating_store_write": 0, "realdata": 0,
        "checks": checks, "passed": n_ok, "total": len(checks), "gate": gate,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n  RESULT: %d/%d PASS   write_tools=0 oauth=0 deploy=0 realdata=0" % (n_ok, len(checks)))
    print("  report:", REPORT)
    print("  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    print("=" * 74)
    print("BingguPack HTTP MCP skeleton selftest — 실 HTTP E2E (127.0.0.1, read-only)")
    print("=" * 74)
    run()
