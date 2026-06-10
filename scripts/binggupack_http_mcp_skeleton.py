# -*- coding: utf-8 -*-
"""BingguPack hosted MCP skeleton — 로컬 한정 PoC (GO-HOSTED-MCP-SKELETON-LOCAL).

목적: App path 첫 기술 검증 — "read-only HTTP MCP(streamable HTTP, JSON-only)가
  실제로 응답하는가"만 확인한다. P2 실측 근거(docs/BINGGUPACK_APP_P1P6_FINDINGS.md):
  SSE/세션은 선택사항 → stateless + JSON-only 최소 구현으로 스펙 적합.

범위(불변):
  - bind = 127.0.0.1 하드코딩(옵션 없음). 외부 배포/도메인/OAuth 0.
  - read-only tool 5종만 노출(write/apply/upload/finalize/confirm 류 0).
  - synthetic/toy pack 전용(--make-packs 가 생성). 실 데이터/운영 store 접근 0.
  - 응답 JSON-only(SSE 미구현 — GET 은 405, 스펙 허용). 세션 미발급(stateless 합법).
  - raw 경로/secret/PII 미출력: pack view 는 openbinggu_pack_consumer_smoke.consume()
    (sanitize 기존 게이트) 재사용 + 직렬화 후 fail-closed 누출 스캔.
  - 응답 크기: MAX_RESPONSE_CHARS(≈1만 토큰 보수 기준) 초과 시 목록 절단 + truncated 표시.

CLI:
  python binggupack_http_mcp_skeleton.py --make-packs            # toy pack 2개 생성(tmp/)
  python binggupack_http_mcp_skeleton.py --serve [--port 8841]   # 로컬 서버
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import openbinggu_pack_consumer_smoke as smoke  # consume() — sanitize된 read-only view

HOST = "127.0.0.1"                 # 로컬 한정 — 변경 옵션 자체를 두지 않음
DEFAULT_PORT = 8841
MCP_PATH = "/mcp"
PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "binggupack-http-mcp-skeleton", "version": "0.1.0-local-poc"}
DEFAULT_PACKS_ROOT = BASE / "tmp" / "http_mcp_skeleton_packs"
MAX_RESPONSE_CHARS = 36000         # ≈1만 토큰 보수 기준(P5 실측 정합)
EXCERPT_MAX = 200

# fail-closed 누출 스캔: 절대경로/홈경로/내부 작업트리 흔적이 응답에 보이면 차단
LEAK_PATTERNS = [
    re.compile(r"[A-Za-z]:\\\\?"),            # Windows 드라이브 절대경로
    re.compile(r"/(?:Users|home)/[A-Za-z0-9_]+"),
    re.compile(r"_backup"),
    re.compile(r"cloud_reset_\d+"),
]

CONSUMER_RULES_MD = (
    "## consumer rules (불변)\n"
    "1. evidence_refs 기반으로만 답한다 — 근거 없으면 \"pack에 근거 없음\".\n"
    "2. 추측 생성 금지 — 출처는 node_id/evidence_id로 표기(id만, raw 경로/secret 금지).\n"
    "3. 모든 노드/엣지는 candidate(confirmed 아님) — 승격하지 않는다.\n"
    "4. 자동 병합/저장 금지 — 받은 pack을 그래프/메모리에 자동 반영하지 않는다.\n"
)


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------- toy pack 생성 (synthetic only) ----------------

def _w_jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def make_toy_packs(root):
    """결정적 synthetic toy pack 2개 생성. 실 데이터/실 경로 0."""
    root = Path(root)
    specs = [
        ("toy_build_notes", "synthetic toy: 빌드/테스트 절차 메모", [
            ("n1", "Toy 프로젝트의 빌드는 make build 로 실행한다 (합성 예시).", "EV-A1", "examples/toy/Makefile"),
            ("n2", "Toy 프로젝트의 테스트는 make test 로 실행한다 (합성 예시).", "EV-A2", "examples/toy/Makefile"),
            ("n3", "릴리스 전에는 빌드와 테스트를 모두 통과해야 한다 (합성 예시).", "EV-A3", "examples/toy/RELEASE.md"),
        ], [("e1", "n3", "depends_on", "n1"), ("e2", "n3", "depends_on", "n2")]),
        ("toy_recipe_notes", "synthetic toy: 요리 레시피 메모", [
            ("n1", "토마토 수프는 토마토를 먼저 볶은 뒤 끓인다 (합성 예시).", "EV-B1", "examples/recipe/soup.md"),
            ("n2", "수프 간은 마지막 단계에서 맞춘다 (합성 예시).", "EV-B2", "examples/recipe/soup.md"),
        ], [("e1", "n2", "refines", "n1")]),
    ]
    made = []
    for pack_name, scope_desc, node_rows, edge_rows in specs:
        d = root / pack_name
        d.mkdir(parents=True, exist_ok=True)
        pid = "toy/" + pack_name
        nodes, evidx, evchunk = [], [], []
        for nid, sentence, eid, rel_src in node_rows:
            nodes.append({
                "id": "node:%s:%s" % (pack_name, nid),
                "label": sentence[:40],
                "properties": {"sentence": sentence, "candidate": True,
                               "origin": "synthetic", "domain": "toy"},
                "evidence_refs": [eid], "promotion_allowed": False,
            })
            evidx.append({"evidence_id": eid, "source_path": rel_src})  # 상대경로만(clean)
            evchunk.append({"item_id": eid, "text": sentence})
        edges = [{
            "id": "edge:%s:%s" % (pack_name, eid_),
            "source": "node:%s:%s" % (pack_name, s),
            "target": "node:%s:%s" % (pack_name, t),
            "properties": {"relation": rel, "candidate": True, "origin": "synthetic"},
            "evidence_refs": [nodes[0]["evidence_refs"][0]], "promotion_allowed": False,
        } for eid_, s, rel, t in edge_rows]
        (d / "manifest.json").write_text(json.dumps({
            "format_version": "opencrab-pack-v1", "pack_id": pid,
            "scope": scope_desc, "visibility": "private", "status": "staged",
            "pack_type": "candidate", "promotion_allowed_default": False,
            "counts": {"nodes": len(nodes), "edges": len(edges), "evidence": len(evidx)},
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        _w_jsonl(d / "nodes.jsonl", nodes)
        _w_jsonl(d / "edges.jsonl", edges)
        _w_jsonl(d / "evidence_index.jsonl", evidx)
        _w_jsonl(d / "evidence_chunk.jsonl", evchunk)
        made.append(pid)
    return made


# ---------------- pack store (read-only) ----------------

class PackStore:
    def __init__(self, root):
        self.root = Path(root)
        self._views = {}
        self.reload()

    def reload(self):
        self._views = {}
        if not self.root.exists():
            return
        for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if not (d / "manifest.json").exists():
                continue
            view = smoke.consume(d)        # 기존 sanitize 게이트 재사용 — raw 복원/경로 dump 없음
            if view.get("pack_id"):
                self._views[view["pack_id"]] = view

    def ids(self):
        return sorted(self._views.keys())

    def get(self, pack_id):
        if pack_id not in self._views:
            raise ToolError("PACK_NOT_FOUND", "pack_id not found: %s" % pack_id)
        return self._views[pack_id]


# ---------------- tools (전부 read-only) ----------------

def tool_pack_list(store, args):
    limit = max(1, min(int(args.get("limit", 20)), 50))
    packs = []
    for pid in store.ids()[:limit]:
        v = store.get(pid)
        packs.append({"pack_id": pid, "title": v.get("scope", ""),
                      "counts": v["counts"], "candidate_note": "all items candidate (not confirmed)"})
    return {"packs": packs, "total": len(store.ids())}


def tool_pack_summary(store, args):
    v = store.get(_req_str(args, "pack_id"))
    topics = [n["claim"][:40] for n in v["nodes"][:10]]
    return {
        "pack_id": v["pack_id"],
        "manifest_summary": {"visibility": v["visibility"], "status": v["status"],
                             "pack_type": "candidate", "counts": v["counts"]},
        "topics": topics,
        "candidate_note": "all items candidate (not confirmed); promotion_allowed=false",
    }


def tool_evidence_search(store, args):
    v = store.get(_req_str(args, "pack_id"))
    query = _req_str(args, "query")
    if not (2 <= len(query) <= 200):
        raise ToolError("QUERY_TOO_SHORT", "query must be 2~200 chars")
    limit = max(1, min(int(args.get("limit", 5)), 20))
    terms = [t for t in query.lower().split() if t]
    hits = []
    chunk_text = {}
    for ev in v["evidence"]:
        chunk_text[ev["evidence_id"]] = ""
    # evidence 본문은 노드 claim(이미 sanitize됨)과 chunk 동일 — claim 기준 매칭
    for n in v["nodes"]:
        text = n["claim"].lower()
        score = sum(text.count(t) for t in terms)
        if score > 0:
            for eid in n["evidence_refs"]:
                hits.append({"evidence_id": eid,
                             "sentence_excerpt": n["claim"][:EXCERPT_MAX],
                             "score": score, "candidate": True})
    hits.sort(key=lambda h: (-h["score"], h["evidence_id"]))
    return {"hits": hits[:limit], "total_hits": len(hits), "candidate_note": "excerpts are candidate evidence"}


def tool_node_edge_lookup(store, args):
    v = store.get(_req_str(args, "pack_id"))
    node_id = args.get("node_id")
    keyword = args.get("keyword")
    if not node_id and not keyword:
        raise ToolError("NODE_NOT_FOUND", "node_id or keyword required")
    nodes_by_id = {n["id"]: n for n in v["nodes"]}
    if node_id:
        if node_id not in nodes_by_id:
            raise ToolError("NODE_NOT_FOUND", "node_id not found: %s" % node_id)
        node = nodes_by_id[node_id]
    else:
        kw = str(keyword).lower()
        cands = [n for n in v["nodes"] if kw in n["claim"].lower()]
        if not cands:
            raise ToolError("NODE_NOT_FOUND", "no node matches keyword")
        if len(cands) > 1:
            raise ToolError("AMBIGUOUS_KEYWORD",
                            "candidates: " + ", ".join(sorted(n["id"] for n in cands)[:5]))
        node = cands[0]
    edges = [{"id": e["id"], "relation": e["relation"], "direction":
              ("out" if e["source"] == node["id"] else "in"),
              "peer_id": (e["target"] if e["source"] == node["id"] else e["source"]),
              "evidence_refs": e["evidence_refs"], "candidate": e["candidate"]}
             for e in v["edges"] if node["id"] in (e["source"], e["target"])]
    return {"node": {"id": node["id"], "claim": node["claim"], "candidate": node["candidate"],
                     "evidence_refs": node["evidence_refs"], "trust": node["trust"]},
            "edges": edges}


def tool_handoff_context(store, args):
    v = store.get(_req_str(args, "pack_id"))
    max_nodes = max(1, min(int(args.get("max_nodes", 15)), 30))
    topic = str(args.get("topic", "")).strip().lower()
    nodes = v["nodes"]
    if topic:
        picked = [n for n in nodes if topic in n["claim"].lower()] or nodes
    else:
        picked = nodes
    picked = picked[:max_nodes]
    picked_ids = {n["id"] for n in picked}
    lines = ["# BingguPack handoff context — %s" % v["pack_id"],
             "(candidate pack — not confirmed / counts: nodes=%d edges=%d evidence=%d)" % (
                 v["counts"]["nodes"], v["counts"]["edges"], v["counts"]["evidence"]),
             "", CONSUMER_RULES_MD, "## nodes (candidate)"]
    for n in picked:
        lines.append("- [%s] %s (evidence: %s)" % (n["id"], n["claim"], ", ".join(n["evidence_refs"])))
    lines.append("")
    lines.append("## edges (candidate)")
    for e in v["edges"]:
        if e["source"] in picked_ids or e["target"] in picked_ids:
            lines.append("- %s -%s-> %s (evidence: %s)" % (
                e["source"], e["relation"], e["target"], ", ".join(e["evidence_refs"])))
    md = "\n".join(lines)
    return {"context_markdown": md, "nodes_included": len(picked),
            "truncated": len(picked) < len(nodes)}


def _req_str(args, key):
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ToolError("INVALID_ARGUMENT", "missing required string: %s" % key)
    return val.strip()


TOOLS = {
    "pack_list": {
        "description": "내 synthetic toy pack 목록(요약만, raw 경로 0). read-only.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "최대 개수(기본 20)"}}, "required": []},
        "handler": tool_pack_list,
    },
    "pack_summary": {
        "description": "pack manifest 요약 + counts + 주제 라벨. read-only.",
        "inputSchema": {"type": "object", "properties": {
            "pack_id": {"type": "string"}}, "required": ["pack_id"]},
        "handler": tool_pack_summary,
    },
    "evidence_search": {
        "description": "pack 내 evidence 발췌 검색(상위 N, candidate 표시 유지). read-only.",
        "inputSchema": {"type": "object", "properties": {
            "pack_id": {"type": "string"}, "query": {"type": "string", "description": "2~200자"},
            "limit": {"type": "integer", "description": "기본 5, 최대 20"}},
            "required": ["pack_id", "query"]},
        "handler": tool_evidence_search,
    },
    "node_edge_lookup": {
        "description": "노드 + 연결 엣지(relation·evidence_refs) 조회. read-only.",
        "inputSchema": {"type": "object", "properties": {
            "pack_id": {"type": "string"}, "node_id": {"type": "string"},
            "keyword": {"type": "string"}}, "required": ["pack_id"]},
        "handler": tool_node_edge_lookup,
    },
    "handoff_context": {
        "description": "모델 투입용 context Markdown(mobile fallback과 동일 형식). read-only.",
        "inputSchema": {"type": "object", "properties": {
            "pack_id": {"type": "string"}, "topic": {"type": "string"},
            "max_nodes": {"type": "integer", "description": "기본 15, 최대 30"}},
            "required": ["pack_id"]},
        "handler": tool_handoff_context,
    },
}


# ---------------- JSON-RPC / 크기·누출 가드 ----------------

def _leak_scan(text):
    return [p.pattern for p in LEAK_PATTERNS if p.search(text)]


def _fit_result(result):
    """직렬화 크기 가드 — 초과 시 목록 필드 절단 + truncated 표시."""
    for _ in range(8):
        s = json.dumps(result, ensure_ascii=False)
        if len(s) <= MAX_RESPONSE_CHARS:
            return result
        cut = False
        for key in ("packs", "hits", "edges", "topics"):
            seq = result.get(key)
            if isinstance(seq, list) and len(seq) > 1:
                result[key] = seq[: max(1, len(seq) // 2)]
                cut = True
        if "context_markdown" in result and len(result["context_markdown"]) > 1000:
            result["context_markdown"] = result["context_markdown"][: len(result["context_markdown"]) // 2]
            cut = True
        result["truncated"] = True
        if not cut:
            return {"error_code": "RESPONSE_TOO_LARGE", "message": "result exceeds size cap"}
    return result


def handle_rpc(store, req):
    """JSON-RPC 1건 처리 → (응답 dict | None=notification)."""
    rpc_id = req.get("id")
    method = req.get("method", "")
    if rpc_id is None:
        return None  # notification (initialized 등) — 202
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION,
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": SERVER_INFO}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": k, "description": t["description"],
                             "inputSchema": t["inputSchema"]} for k, t in sorted(TOOLS.items())]}
    elif method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": rpc_id,
                    "error": {"code": -32602, "message": "unknown tool: %s" % name}}
        try:
            out = _fit_result(TOOLS[name]["handler"](store, args))
            is_err = "error_code" in out
        except ToolError as te:
            out = {"error_code": te.code, "message": te.message}
            is_err = True
        text = json.dumps(out, ensure_ascii=False)
        leaks = _leak_scan(text)
        if leaks:  # fail-closed: 내부 흔적 검출 시 결과 자체를 내보내지 않음
            out = {"error_code": "SANITIZE_BLOCK", "message": "internal trace detected; blocked"}
            text = json.dumps(out, ensure_ascii=False)
            is_err = True
        result = {"content": [{"type": "text", "text": text}],
                  "structuredContent": out, "isError": is_err}
    else:
        return {"jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32601, "message": "method not found: %s" % method}}
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


class McpHandler(BaseHTTPRequestHandler):
    store = None  # serve()에서 주입
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # 요청 메타만 stderr (pack 내용 미기록)
        sys.stderr.write("[mcp] %s %s\n" % (self.command, self.path))

    def _deny(self, code, msg):
        body = json.dumps({"error": msg}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", origin) is not None

    def do_GET(self):
        if self.path != MCP_PATH:
            return self._deny(404, "not found")
        return self._deny(405, "SSE not offered (JSON-only server)")

    def do_DELETE(self):
        return self._deny(405, "stateless server (no session)")

    def do_POST(self):
        if self.path != MCP_PATH:
            return self._deny(404, "not found")
        if not self._origin_ok():
            return self._deny(403, "origin not allowed")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return self._deny(400, "invalid json")
        resp = handle_rpc(self.store, req)
        if resp is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port, packs_root):
    store = PackStore(packs_root)
    McpHandler.store = store
    httpd = ThreadingHTTPServer((HOST, port), McpHandler)
    print("binggupack http mcp skeleton: http://%s:%d%s  packs=%d (local-only, read-only)"
          % (HOST, httpd.server_address[1], MCP_PATH, len(store.ids())))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main():
    args = sys.argv[1:]
    packs_root = DEFAULT_PACKS_ROOT
    if "--packs-root" in args:
        packs_root = Path(args[args.index("--packs-root") + 1])
    if "--make-packs" in args:
        made = make_toy_packs(packs_root)
        print("toy packs created: %s (root=tmp/http_mcp_skeleton_packs)" % ", ".join(made))
        return
    if "--serve" in args:
        port = DEFAULT_PORT
        if "--port" in args:
            port = int(args[args.index("--port") + 1])
        serve(port, packs_root)
        return
    print("usage: binggupack_http_mcp_skeleton.py [--make-packs | --serve [--port N]] [--packs-root DIR]")


if __name__ == "__main__":
    main()
