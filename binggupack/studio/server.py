# -*- coding: utf-8 -*-
"""Binggu Studio Preview 서버 — 로컬 read-only 웹 UI (Python stdlib only).

loopback(127.0.0.1) 전용 · 실행마다 새 ephemeral session token 경로 스코프 · GET/HEAD 만 허용.
Daily Console read model(collect_home_snapshot / collect_inbox_snapshot)을 매 요청 그대로 재사용한다.

읽기 전용 계약:
  · mutation / approval 발행 / hosted fetch / ledger write 0 · directory 생성 0 · 외부 asset/network 0.
  · session token 은 메모리에만 유지(파일/config/로그 0) · URL 이외 JSON 응답 미포함.
  · Host 는 127.0.0.1 / localhost 만(그 외 403) · POST/PUT/PATCH/DELETE/OPTIONS → 405(핸들러 미호출).
  · CSP · no-store · nosniff 등 보안 헤더 · CORS 헤더 0.
"""
import json
import os
import secrets
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from binggupack.cli import daily
from binggupack.studio import read_model

try:
    from importlib.resources import files as _res_files
except Exception:   # pragma: no cover — 방어(패키지 최소 3.10)
    _res_files = None

STUDIO_VERSION = 1
_LOOPBACK = "127.0.0.1"
_ALLOWED_HOSTNAMES = ("127.0.0.1", "localhost")

# 서빙 허용 정적 asset 화이트리스트(경로 순회·임의 파일 read 차단).
_STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

_SECURITY_HEADERS = (
    ("Cache-Control", "no-store"),
    ("Pragma", "no-cache"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy",
     "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
     "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
)


def _read_static(name):
    """화이트리스트 정적 asset 을 bytes 로 읽는다(설치본=importlib.resources · clone=상대경로 폴백)."""
    if name not in _STATIC_FILES:
        return None
    if _res_files is not None:
        try:
            return (_res_files("binggupack.studio") / "static" / name).read_bytes()
        except Exception:
            pass
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", name)
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def _meta():
    return {"studio_version": STUDIO_VERSION, "product": "BingguPack", "mode": "read-only"}


def _query(raw_path):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(raw_path).query)


def _qget(query, key, default):
    v = query.get(key)
    return v[0] if v else default


def _make_handler(ledger, session):
    class StudioHandler(BaseHTTPRequestHandler):
        server_version = "BingguStudio"
        sys_version = ""

        # ── 응답 헬퍼 ────────────────────────────────────────────────────────────
        def _emit(self, code, body, ctype, head_only):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in _SECURITY_HEADERS:
                self.send_header(k, v)
            self.end_headers()
            if not head_only and body:
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

        def _text(self, code, msg, head_only=False):
            self._emit(code, msg.encode("utf-8"), "text/plain; charset=utf-8", head_only)

        def _json(self, obj, code=200, head_only=False):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._emit(code, body, "application/json; charset=utf-8", head_only)

        # ── 검증 ─────────────────────────────────────────────────────────────────
        def _host_ok(self):
            host = self.headers.get("Host", "") or ""
            hostname = host.rsplit(":", 1)[0] if host else ""
            return hostname in _ALLOWED_HOSTNAMES

        def _session_ok(self, given):
            try:
                return secrets.compare_digest(given, session)
            except Exception:
                return False

        # ── 라우팅(GET/HEAD 공용) ──────────────────────────────────────────────────
        def _route(self, head_only):
            if not self._host_ok():
                self._text(403, "forbidden", head_only)
                return
            raw = self.path
            path = raw.split("?", 1)[0].split("#", 1)[0]
            parts = [p for p in path.split("/") if p]
            if len(parts) < 2 or parts[0] != "s" or not self._session_ok(parts[1]):
                self._text(404, "not found", head_only)
                return
            rest = parts[2:]
            if not rest:
                self._serve_static("index.html", head_only)
            elif rest[0] == "static" and len(rest) == 2:
                self._serve_static(rest[1], head_only)
            elif rest[0] == "api" and len(rest) == 3 and rest[1] == "memory":
                self._serve_memory_detail(rest[2], head_only)
            elif rest[0] == "api" and len(rest) == 2:
                self._serve_api(rest[1], _query(raw), head_only)
            else:
                self._text(404, "not found", head_only)

        def _serve_static(self, name, head_only):
            ctype = _STATIC_FILES.get(name)
            if ctype is None:
                self._text(404, "not found", head_only)
                return
            body = _read_static(name)
            if body is None:
                self._text(404, "not found", head_only)
                return
            self._emit(200, body, ctype, head_only)

        def _serve_api(self, name, query, head_only):
            if name == "home":
                self._json(daily.collect_home_snapshot(ledger), head_only=head_only)
            elif name == "inbox":
                self._json(daily.collect_inbox_snapshot(ledger), head_only=head_only)
            elif name == "meta":
                self._json(_meta(), head_only=head_only)
            elif name == "memories":
                self._serve_memories(query, head_only)
            elif name == "recall":
                self._serve_recall(query, head_only)
            else:
                self._json({"error": "not_found"}, code=404, head_only=head_only)

        def _serve_memories(self, query, head_only):
            state = _qget(query, "state", "active")
            node_type = _qget(query, "type", None)
            subtype = _qget(query, "subtype", None)
            q = _qget(query, "q", None)
            try:
                limit = read_model.parse_int(_qget(query, "limit", str(read_model.LIST_LIMIT_DEFAULT)), "limit")
                offset = read_model.parse_int(_qget(query, "offset", "0"), "offset")
                read_model.validate_list_params(state, limit, offset)
                if q is not None:
                    q = read_model.normalize_text(q)
                    if len(q) > read_model.QUERY_MAX:
                        raise read_model.ValidationError("q", "q too long")
                    q = q or None
            except read_model.ValidationError as e:
                self._json({"error": "invalid_request", "field": e.field}, code=400, head_only=head_only)
                return
            snap = read_model.collect_memory_list_snapshot(
                ledger, state=state, node_type=node_type, subtype=subtype, q=q, limit=limit, offset=offset)
            self._json(snap, head_only=head_only)

        def _serve_recall(self, query, head_only):
            try:
                q = read_model.validate_query(_qget(query, "q", None))
                limit = read_model.parse_int(_qget(query, "limit", str(read_model.RECALL_LIMIT_DEFAULT)), "limit")
                if not (1 <= limit <= read_model.RECALL_LIMIT_MAX):
                    raise read_model.ValidationError("limit", "limit must be 1..%d" % read_model.RECALL_LIMIT_MAX)
            except read_model.ValidationError as e:
                self._json({"error": "invalid_request", "field": e.field}, code=400, head_only=head_only)
                return
            self._json(read_model.collect_recall_snapshot(ledger, q, limit=limit), head_only=head_only)

        def _serve_memory_detail(self, raw_id, head_only):
            try:
                node_id = read_model.validate_node_id(urllib.parse.unquote(raw_id))
            except read_model.ValidationError as e:
                self._json({"error": "invalid_request", "field": e.field}, code=400, head_only=head_only)
                return
            snap = read_model.collect_memory_detail_snapshot(ledger, node_id)
            if snap is None:
                self._json({"error": "not_found"}, code=404, head_only=head_only)
                return
            self._json(snap, head_only=head_only)

        # ── 메서드 ──────────────────────────────────────────────────────────────
        def do_GET(self):
            self._route(head_only=False)

        def do_HEAD(self):
            self._route(head_only=True)

        def _reject(self):
            # mutation 계열은 Host/token 검증 이전에 즉시 거부 — 어떤 핸들러/CLI mutation 도 호출하지 않는다.
            self._text(405, "method not allowed")

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _reject

        def log_message(self, fmt, *args):   # token/경로/payload 노출 0 — 침묵.
            return

    return StudioHandler


def build_server(ledger, port=0):
    """(httpd, session_token) 반환. 127.0.0.1 에만 bind. port=0 → OS 임시 포트."""
    if not isinstance(port, int) or isinstance(port, bool) or not (0 <= port <= 65535):
        raise ValueError("port must be within 0..65535")
    session = secrets.token_urlsafe(32)
    httpd = ThreadingHTTPServer((_LOOPBACK, port), _make_handler(ledger, session))
    httpd.daemon_threads = True
    return httpd, session


def studio_url(httpd, session):
    return "http://%s:%d/s/%s/" % (_LOOPBACK, httpd.server_address[1], session)


def serve(ledger, port=0, open_browser=True):
    """Studio 실행(Ctrl+C 까지 blocking). read-only · loopback only. 정상 종료 시 0."""
    try:
        httpd, session = build_server(ledger, port)
    except ValueError:
        print("port 는 0..65535 범위여야 합니다.", file=sys.stderr)
        return 2
    except OSError as e:
        print("Studio 서버를 시작할 수 없습니다: %s" % e, file=sys.stderr)
        return 1
    url = studio_url(httpd, session)
    print("Binggu Studio Preview")
    print("Read-only local session")
    print("URL: %s" % url)
    print("Press Ctrl+C to stop.")
    sys.stdout.flush()
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0
