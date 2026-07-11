# -*- coding: utf-8 -*-
"""Binggu Studio Preview 회귀 — loopback 격리 · read-only · Daily Console snapshot 재사용 · packaging.

전 테스트 임시 홈/ledger 격리 · 운영 ~/.binggupack 미접촉. 서버는 인프로세스로 임시 포트(0)에 bind,
요청 후 즉시 shutdown(hang 방지). 브라우저 자동 열기(webbrowser)는 어떤 테스트도 호출하지 않는다.
"""
import inspect
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.studio import server            # noqa: E402
from binggupack.cli import daily                # noqa: E402
from test_daily_console import _full_home, _snapshot   # noqa: E402  (fixture 재사용)

_STATIC_DIR = os.path.join(ROOT, "binggupack", "studio", "static")
_SERVER_SRC = os.path.join(ROOT, "binggupack", "studio", "server.py")


class _Studio:
    """인프로세스 Studio 서버 — 임시 포트 bind + 별도 스레드 serve. 사용 후 close()."""

    def __init__(self, ledger):
        self.httpd, self.session = server.build_server(ledger, port=0)
        self.host_addr = self.httpd.server_address[0]
        self.port = self.httpd.server_address[1]
        self.base = "http://127.0.0.1:%d/s/%s/" % (self.port, self.session)
        self._t = threading.Thread(target=self.httpd.serve_forever)
        self._t.daemon = True
        self._t.start()

    def get(self, path="", method="GET", host=None, raw_url=None):
        url = raw_url if raw_url is not None else (self.base + path)
        r = urllib.request.Request(url, method=method)
        if host is not None:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def studio_full(tmp_path):
    home, ledger = _full_home(tmp_path)
    s = _Studio(ledger)
    try:
        yield s, home, ledger
    finally:
        s.close()


def _cli(args, timeout=60):
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, os.path.join(ROOT, "binggu.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=e, cwd=ROOT, timeout=timeout)


def _read_static(fn):
    with open(os.path.join(_STATIC_DIR, fn), encoding="utf-8") as f:
        return f.read()


# ════════════════════════ CLI / 실행 계약 ════════════════════════
def test_studio_command_exists():
    r = _cli(["studio", "--help"])
    assert r.returncode == 0, r.stderr
    assert "--no-open" in r.stdout and "--port" in r.stdout
    top = _cli(["--help"])
    assert "studio" in top.stdout


def test_studio_default_port_is_ephemeral(studio_full):
    s, _, _ = studio_full
    # OS 가 고른 임시 포트 — 0 이 아니고 유효 범위
    assert 1 <= s.port <= 65535 and s.port != 0
    # 기본 port 인자는 0(OS 임시 포트)
    assert inspect.signature(server.build_server).parameters["port"].default == 0
    assert inspect.signature(server.serve).parameters["port"].default == 0


def test_studio_binds_loopback_only(studio_full):
    s, _, _ = studio_full
    assert s.host_addr == "127.0.0.1"
    with open(_SERVER_SRC, encoding="utf-8") as f:
        code = f.read()
    assert "0.0.0.0" not in code
    assert '"127.0.0.1"' in code


# ════════════════════════ 서버 보안 계약 ════════════════════════
def test_studio_rejects_untrusted_host(studio_full):
    s, _, _ = studio_full
    st, _, _ = s.get("api/home", host="evil.example.com")
    assert st == 403
    assert s.get("api/home", host="127.0.0.1:%d" % s.port)[0] == 200
    assert s.get("api/home", host="localhost:%d" % s.port)[0] == 200


def test_studio_requires_session_token(studio_full):
    s, _, _ = studio_full
    root = "http://127.0.0.1:%d" % s.port
    for suffix in ("/", "/api/home", "/s//api/home", "/s/definitely-not-valid/api/home"):
        st, _, _ = s.get(raw_url=root + suffix)
        assert st == 404, suffix
    assert s.get("api/home")[0] == 200   # 올바른 세션은 통과


def test_studio_unknown_api_404(studio_full):
    s, _, _ = studio_full
    for p in ("api/nope", "api/", "api/home/extra", "random", "static/", "static/notallowed.txt"):
        assert s.get(p)[0] == 404, p


def test_studio_post_put_patch_delete_405(studio_full):
    s, _, _ = studio_full
    for m in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        assert s.get("api/home", method=m)[0] == 405, m


def test_studio_mutation_handler_never_called(studio_full):
    s, home, _ = studio_full
    before = _snapshot(home)
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        s.get("api/home", method=m)
        s.get("api/inbox", method=m)
        s.get("", method=m)
    assert _snapshot(home) == before, "mutation 메서드가 파일을 변경했다"
    # 서버 모듈은 저장/승인/동기화 mutation 심볼을 import/호출하지 않는다
    with open(_SERVER_SRC, encoding="utf-8") as f:
        code = f.read()
    for banned in ("save_selected", "commit_bundle", "upsert_request",
                   "fetch_to_staging", "import_edges", "makedirs"):
        assert banned not in code, banned


def test_studio_no_cors(studio_full):
    s, _, _ = studio_full
    for p, m in (("api/home", "GET"), ("api/home", "OPTIONS"), ("", "GET"), ("static/app.js", "GET")):
        _, h, _ = s.get(p, method=m)
        assert not any(k.lower().startswith("access-control-") for k in h), (p, m, list(h))


def test_studio_security_headers(studio_full):
    s, _, _ = studio_full
    _, h, _ = s.get("api/home")
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("Referrer-Policy") == "no-referrer"
    assert h.get("X-Frame-Options") == "DENY"
    csp = h.get("Content-Security-Policy", "")
    for token in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                  "frame-ancestors 'none'", "base-uri 'none'", "form-action 'none'"):
        assert token in csp, token


def test_studio_cache_disabled(studio_full):
    s, _, _ = studio_full
    for p in ("", "static/app.js", "static/style.css", "api/home", "api/meta"):
        _, h, _ = s.get(p)
        assert h.get("Cache-Control") == "no-store", p
        assert h.get("Pragma") == "no-cache", p


# ════════════════════════ API = Daily Console snapshot ════════════════════════
def test_studio_home_api_matches_daily_snapshot(studio_full):
    s, _, ledger = studio_full
    st, h, b = s.get("api/home")
    assert st == 200 and h.get("Content-Type") == "application/json; charset=utf-8"
    api = json.loads(b)
    direct = daily.collect_home_snapshot(ledger)
    for k in ("schema_version", "ledger", "services", "queues", "next_actions"):
        assert api[k] == direct[k], k
    assert api["queues"] == {"capture": 2, "hosted": 1, "approvals": 1, "due": 1}


def test_studio_inbox_api_matches_daily_snapshot(studio_full):
    s, _, ledger = studio_full
    st, h, b = s.get("api/inbox")
    assert st == 200
    api = json.loads(b)
    direct = daily.collect_inbox_snapshot(ledger)
    assert api["schema_version"] == direct["schema_version"]
    assert set(api["sections"]) == set(direct["sections"])
    for k in direct["sections"]:
        assert api["sections"][k]["count"] == direct["sections"][k]["count"], k
        assert api["sections"][k]["items"] == direct["sections"][k]["items"], k


def test_studio_meta_is_read_only(studio_full):
    s, _, _ = studio_full
    st, h, b = s.get("api/meta")
    assert st == 200
    assert json.loads(b) == {"studio_version": 1, "product": "BingguPack", "mode": "read-only"}
    assert h.get("Content-Type") == "application/json; charset=utf-8"


# ════════════════════════ 프런트엔드 정적 계약 ════════════════════════
def test_studio_dynamic_text_uses_safe_dom_path():
    js = _read_static("app.js")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert banned not in js, banned
    assert "textContent" in js


def test_studio_has_no_external_asset_url():
    for fn in ("index.html", "app.js", "style.css"):
        txt = _read_static(fn)
        assert "http://" not in txt, fn
        assert "https://" not in txt, fn
        assert "@import" not in txt, fn
        for cdn in ("cdnjs", "googleapis", "unpkg", "jsdelivr", "//fonts."):
            assert cdn not in txt, (fn, cdn)


# ════════════════════════ read-only 불변식 ════════════════════════
def test_studio_no_ledger_creates_nothing(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    s = _Studio(ledger)
    try:
        st, _, b = s.get("api/home")
        assert st == 200 and json.loads(b)["ledger"]["exists"] is False
        s.get("api/inbox")
        s.get("api/meta")
        s.get("")
    finally:
        s.close()
    assert not os.path.exists(ledger), "Studio 가 ledger 를 생성했다"
    assert sorted(os.listdir(home)) == [], os.listdir(home)


def test_studio_refresh_is_read_only(studio_full):
    s, home, ledger = studio_full
    before = _snapshot(home)
    led_mt = os.path.getmtime(ledger)
    cap = os.path.join(home, "capture_buffer.sqlite")
    cap_mt = os.path.getmtime(cap)
    for _ in range(5):   # 새로고침(polling) 시뮬
        for p in ("api/home", "api/inbox", "api/meta", "", "static/app.js"):
            s.get(p)
    assert _snapshot(home) == before, "새로고침이 파일을 변경했다"
    assert os.path.getmtime(ledger) == led_mt
    assert os.path.getmtime(cap) == cap_mt


# ════════════════════════ packaging / 설치본 ════════════════════════
def test_studio_assets_present_in_wheel():
    from importlib.resources import files
    base = files("binggupack.studio") / "static"
    for fn in ("index.html", "app.js", "style.css"):
        assert (base / fn).read_bytes(), fn
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        pj = f.read()
    assert "binggupack.studio" in pj
    assert "static/*.html" in pj and "static/*.js" in pj and "static/*.css" in pj


def test_studio_external_cwd_no_open_smoke(tmp_path):
    home, ledger = _full_home(tmp_path)
    ext = str(tmp_path / "external_cwd")
    os.makedirs(ext)
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["BINGGU_HOME"] = home
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "binggu.py"), "--ledger", ledger,
         "studio", "--no-open", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env=e, cwd=ext)
    try:
        url = None
        deadline = time.time() + 20
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            if line.startswith("URL:"):
                url = line.split("URL:", 1)[1].strip()
                break
        assert url and url.startswith("http://127.0.0.1:"), "URL 미출력: %r" % url
        assert url.rstrip("/").endswith("/") is False and "/s/" in url
        for name in ("api/home", "api/inbox", "api/meta"):
            r = urllib.request.Request(url + name)
            with urllib.request.urlopen(r, timeout=5) as resp:
                assert resp.status == 200, name
                data = json.loads(resp.read())
                assert data.get("schema_version") == 1 or data.get("mode") == "read-only", name
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    # 프로세스가 정상적으로 종료됨(hang 없음)
    assert proc.poll() is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
