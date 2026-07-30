# -*- coding: utf-8 -*-
"""MCP Front Door 회귀 — canonical entrypoint(binggupack-mcp) + core/advanced profile.

profile 은 tools/list 뿐 아니라 tools/call 에서도 강제(숨긴 도구는 handler 전 차단). stdio/HTTP
동일 handle_jsonrpc 경로. legacy(openbinggu-mcp-server) 기본 동작·serverInfo.name 불변. 전 테스트
temp BINGGU_HOME/root 격리 · 운영 ~/.binggupack 미접촉 · mutation/approval/schema 변경 0.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
for _p in (ROOT, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import openbinggu_mcp_server as srv  # noqa: E402

_TMP = os.environ.get("TEMP") or "/tmp"
_ALLOW_ROOT = os.path.join(_TMP, "mcp_front_door_allow_root")


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    # 조회(read) 도구가 운영 ledger 미접촉·결정성 갖도록 — selftest 와 동일 격리.
    monkeypatch.setenv("BINGGU_HOME", os.path.join(_TMP, "mcp_fd_home_none"))
    monkeypatch.setenv("BINGGU_CLOUD_MCP_NO_FALLBACK", "1")
    for k in ("BINGGU_CLOUD_MCP_URL", "BINGGU_CLOUD_MCP_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def _init(profile="advanced", server_name="openbinggu"):
    return srv.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                              _ALLOW_ROOT, profile=profile, server_name=server_name)


def _list(profile):
    r = srv.handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                           _ALLOW_ROOT, profile=profile)
    return r["result"]["tools"]


def _call(name, args, profile, server_name="openbinggu"):
    return srv.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": name, "arguments": args}},
        _ALLOW_ROOT, profile=profile, server_name=server_name)


def _run_entry(entry, args, cwd=None, env_extra=None):
    """entry 함수(main|main_binggupack)를 지정 argv 로 subprocess 실행(설치본 console_script 대리)."""
    driver = ("import sys; sys.argv=['x']+%r; sys.path.insert(0, %r); "
              "import openbinggu_mcp_server as s; s.%s()" % (list(args), SCRIPTS, entry))
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONPATH"] = ROOT + os.pathsep + SCRIPTS + os.pathsep + e.get("PYTHONPATH", "")
    if env_extra:
        e.update(env_extra)
    return subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=e, cwd=cwd or ROOT, timeout=120)


# ════════════ entrypoints ════════════
def test_canonical_entrypoint_exists():
    assert callable(getattr(srv, "main_binggupack", None))
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        py = f.read()
    assert 'binggupack-mcp = "scripts.openbinggu_mcp_server:main_binggupack"' in py


def test_legacy_entrypoint_still_exists():
    assert callable(getattr(srv, "main", None))
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        py = f.read()
    assert 'openbinggu-mcp-server = "scripts.openbinggu_mcp_server:main"' in py


def test_wheel_has_both_entrypoints():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        py = f.read()
    assert 'binggupack-mcp = "scripts.openbinggu_mcp_server:main_binggupack"' in py
    assert 'openbinggu-mcp-server = "scripts.openbinggu_mcp_server:main"' in py


# ════════════ default / explicit profiles ════════════
def test_canonical_default_profile_is_core():
    r = _run_entry("main_binggupack", ["--selftest"])
    assert r.returncode == 0, r.stderr
    assert "profile=core" in r.stdout and "serverInfo.name=binggupack" in r.stdout
    assert "GATE: GO" in r.stdout


def test_legacy_default_profile_is_advanced():
    r = _run_entry("main", ["--selftest"])
    assert r.returncode == 0, r.stderr
    assert "profile=advanced" in r.stdout and "serverInfo.name=openbinggu" in r.stdout
    assert "GATE: GO" in r.stdout


def test_explicit_core_on_legacy_works():
    r = _run_entry("main", ["--selftest", "--profile", "core"])
    assert r.returncode == 0, r.stderr
    assert "profile=core" in r.stdout and "serverInfo.name=openbinggu" in r.stdout
    assert "GATE: GO" in r.stdout


def test_explicit_advanced_on_canonical_works():
    r = _run_entry("main_binggupack", ["--selftest", "--profile", "advanced"])
    assert r.returncode == 0, r.stderr
    assert "profile=advanced" in r.stdout and "serverInfo.name=binggupack" in r.stdout
    assert "GATE: GO" in r.stdout


def test_invalid_profile_exits_two():
    for entry in ("main", "main_binggupack"):
        r = _run_entry(entry, ["--selftest", "--profile", "bogus"])
        assert r.returncode == 2, (entry, r.stdout, r.stderr)
        assert "GATE: GO" not in r.stdout  # selftest 미실행(write 0)
    # --serve 조합에서도 잘못된 profile 은 서버 미가동으로 exit 2
    r = _run_entry("main_binggupack", ["--profile", "nope", "--serve", _TMP])
    assert r.returncode == 2


# ════════════ profile exposure sets ════════════
def test_core_profile_exact_allowlist():
    assert srv.core_profile_invalid() == frozenset(), srv.core_profile_invalid()
    expected = {"status", "recall", "why", "trace_show", "preflight", "list", "reminders",
                "capture_preview", "save_candidate", "pair", "deprecate", "replace",
                # 2026-07-30 use-time AI 도장 — recall/why 가 core 라 도장도 core(루프 폐합).
                "trace_stamp"}
    assert srv.exposed_tools("core") == expected
    assert len(expected) == 13
    # trace_show 가 실제 정식 tool 인지 코드로 확인
    assert "trace_show" in srv.TOOLS and srv.TOOLS["trace_show"]["mode"] == "read"
    assert "trace_stamp" in srv.TOOLS and srv.TOOLS["trace_stamp"]["mode"] == "write-gated"


def test_trace_stamp_surface_carries_reason_codes():
    """도장 유효값이 tools/list 표면(설명+enum)에 정본 그대로 실린다.

    2026-07-30 결함: AI 가 찍는 도구인데 유효값이 표면 어디에도 없어 첫 실사용 도장 6건이
    전량 invalid_reason_code 거부. 하드카피가 아니라 recall_trace 정본 위임임을 함께 잠근다.
    """
    from binggupack.pack.recall_trace import REASON_CODES, VALID_VERDICTS
    t = [x for x in srv._list_tools("core") if x["name"] == "trace_stamp"][0]
    for verdict, codes in REASON_CODES.items():
        assert verdict in t["description"]
        for c in codes:
            assert c in t["description"], (verdict, c)
    ip = t["inputSchema"]["properties"]["items"]["items"]["properties"]
    assert ip["verdict"]["enum"] == list(REASON_CODES)
    assert set(ip["verdict"]["enum"]) == set(VALID_VERDICTS)
    assert ip["reason_code"]["enum"] == [c for codes in REASON_CODES.values() for c in codes]
    # registry 원본은 훼손 0 — 주입은 tools/list 사본에만(다른 소비자 계약 불변).
    reg = srv.TOOLS["trace_stamp"]["input_schema"]["properties"]["items"]["items"]["properties"]
    assert "enum" not in reg["verdict"] and "enum" not in reg["reason_code"]


def test_advanced_profile_matches_previous_exposure():
    # advanced = 현행 필터(read/dry-run/write-gated)와 동일 소스 · 하드코딩 개수 0
    expected = {n for n, s in srv.TOOLS.items() if s["mode"] in srv._EXPOSED_MODES}
    assert srv.exposed_tools("advanced") == expected
    assert srv.exposed_tools("advanced") == set(srv.TOOLS.keys())


def test_forbidden_tools_absent_all_profiles():
    for profile in ("core", "advanced"):
        names = {t["name"] for t in _list(profile)}
        assert not (names & srv._FORBIDDEN), (profile, names & srv._FORBIDDEN)


def test_core_tools_list_only_allowed():
    names = {t["name"] for t in _list("core")}
    assert names == srv.exposed_tools("core")


def test_advanced_tools_list_unchanged():
    tools = _list("advanced")
    names = {t["name"] for t in tools}
    assert names == set(srv.TOOLS.keys())
    # 표준 필드만 — 비표준 최상위 필드(mode/profile/path_params/internal_flags) 없음
    for t in tools:
        assert set(t.keys()) == {"name", "description", "inputSchema"}


def test_same_tool_schema_identical_across_profiles():
    core = {t["name"]: t for t in _list("core")}
    adv = {t["name"]: t for t in _list("advanced")}
    for name in srv.exposed_tools("core"):
        assert name in adv
        assert json.dumps(core[name], ensure_ascii=False, sort_keys=True) == \
               json.dumps(adv[name], ensure_ascii=False, sort_keys=True)


# ════════════ tools/call enforcement ════════════
def test_hidden_tool_call_blocked_before_handler(monkeypatch):
    # core 에서 advanced 전용 도구 직접 호출 → handler 호출되기 전에 차단(핸들러가 raise 해도 도달 0)
    def _boom(name, targs, allow_root):
        raise AssertionError("handle_tool 이 호출됨 — profile 차단 실패")

    monkeypatch.setattr(srv, "handle_tool", _boom)
    r = _call("pack_validate", {"pack_path": "examples/toy_project/p.json"}, profile="core")
    res = r["result"]
    assert res["executed"] is False
    assert res["reason_code"] == "tool_not_in_profile"
    assert res.get("tool_result") in (None, {})


def test_hidden_tool_call_executed_write_zero():
    r = _call("save_candidate", {"text": "x", "indices": [1]}, profile="core")  # save 는 core 에 있음(대조군)
    assert r["result"]["executed"] is True  # 허용 도구는 실행
    # 반대로 숨긴 write 계열 없음 — advanced 전용 write-gated 예: harvest_add
    r2 = _call("harvest_add", {"kind": "url", "url": "https://x", "confirm": "y", "dry_run": False},
               profile="core")
    res = r2["result"]
    assert res["executed"] is False and res["reason_code"] == "tool_not_in_profile"
    assert res.get("tool_result") in (None, {})  # executed_write 필드 자체가 없음


def test_hidden_tool_call_network_zero(monkeypatch):
    # cloud_recall(네트워크 계열) 은 core 에 없음 → handler 전 차단 → 네트워크 0
    def _boom(name, targs, allow_root):
        raise AssertionError("cloud handler 도달 — 네트워크 위험")

    monkeypatch.setattr(srv, "handle_tool", _boom)
    r = _call("cloud_recall", {"query": "여행"}, profile="core")
    assert r["result"]["reason_code"] == "tool_not_in_profile"
    assert r["result"]["executed"] is False


def test_allowed_core_read_tool_behaves_same_as_advanced():
    rc = _call("status", {}, profile="core")["result"]
    ra = _call("status", {}, profile="advanced")["result"]
    assert rc["executed"] is True and rc["verdict"] == "ALLOW"
    assert rc["structuredContent"] == ra["structuredContent"]


def test_allowed_core_write_gated_preview_behaves_same_as_advanced():
    args = {"text": "이 문서는 배포 절차를 정의한다.", "indices": [1]}
    rc = _call("save_candidate", dict(args), profile="core")["result"]
    ra = _call("save_candidate", dict(args), profile="advanced")["result"]
    for res in (rc, ra):
        tr = res.get("tool_result") or {}
        assert res["executed"] is True and tr.get("executed_write") is False
        assert tr.get("verdict") == "PREVIEW"
    assert rc["structuredContent"] == ra["structuredContent"]


# ════════════ escalation protection ════════════
def test_profile_immutable_after_initialize():
    # initialize 이후 어떤 요청도 profile 을 못 바꾼다(handle_jsonrpc 는 request 에서 profile 을 읽지 않음).
    _init(profile="core", server_name="binggupack")
    r = _call("pack_validate", {"pack_path": "examples/toy_project/p.json"}, profile="core")
    assert r["result"]["reason_code"] == "tool_not_in_profile"


def test_request_argument_cannot_escalate_profile():
    # arguments/params 에 profile·mode 를 넣어도 승격 불가 — 여전히 tool_not_in_profile.
    r = srv.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "pack_validate", "profile": "advanced",
                    "arguments": {"pack_path": "examples/toy_project/p.json",
                                  "profile": "advanced", "mode": "advanced"}}},
        _ALLOW_ROOT, profile="core")
    assert r["result"]["reason_code"] == "tool_not_in_profile"
    assert r["result"]["executed"] is False


# ════════════ serverInfo names ════════════
def test_legacy_initialize_server_name_unchanged():
    assert _init(profile="advanced", server_name="openbinggu")["result"]["serverInfo"]["name"] == "openbinggu"
    # legacy entrypoint 기본
    r = _run_entry("main", ["--selftest"])
    assert "serverInfo.name=openbinggu" in r.stdout


def test_canonical_initialize_server_name_binggupack():
    assert _init(profile="core", server_name="binggupack")["result"]["serverInfo"]["name"] == "binggupack"
    r = _run_entry("main_binggupack", ["--selftest"])
    assert "serverInfo.name=binggupack" in r.stdout


# ════════════ stdio / HTTP transports ════════════
def _stdio_roundtrip(profile, reqs):
    """--serve <ROOT> --profile <p> 서브프로세스에 JSON-RPC 라인들을 stdin 으로 주고 응답 수집."""
    driver = ("import sys; sys.argv=['x','--profile',%r,'--serve',%r]; sys.path.insert(0, %r); "
              "import openbinggu_mcp_server as s; s.main()" % (profile, _TMP, SCRIPTS))
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONPATH"] = ROOT + os.pathsep + SCRIPTS
    inp = "".join(json.dumps(q) + "\n" for q in reqs)
    p = subprocess.run([sys.executable, "-c", driver], input=inp, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=e, cwd=ROOT, timeout=120)
    out = [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip().startswith("{")]
    return out


def test_stdio_profile_enforcement():
    out = _stdio_roundtrip("core", [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "pack_validate", "arguments": {"pack_path": "examples/toy_project/p.json"}}},
    ])
    by_id = {o.get("id"): o for o in out}
    assert by_id[1]["result"]["serverInfo"]["name"] == "openbinggu"  # legacy entry, but core profile
    blocked = by_id[2]["result"]
    assert blocked["executed"] is False and blocked["reason_code"] == "tool_not_in_profile"


def _http_server(profile):
    port = _free_port()
    path_seg = "mcpunit"  # URL 경로 세그먼트(비밀 아님) — secret_kv 오탐 회피 위해 'token' 이름/값 미사용
    root = _TMP
    th = threading.Thread(target=srv.serve_http,
                          args=(os.path.abspath(root), port, path_seg),
                          kwargs={"profile": profile, "server_name": "binggupack"}, daemon=True)
    th.start()
    _wait_port(port)
    return port, path_seg


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port, tries=50):
    for _ in range(tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                s.close()
                return
        finally:
            s.close()
        time.sleep(0.05)


def _http_post(port, path_seg, req):
    data = json.dumps(req).encode("utf-8")
    r = urllib.request.Request("http://127.0.0.1:%d/mcp/%s" % (port, path_seg),
                               data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_http_profile_enforcement():
    port, path_seg = _http_server("core")
    r = _http_post(port, path_seg, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "pack_validate",
                                            "arguments": {"pack_path": "examples/toy_project/p.json"}}})
    res = r["result"]
    assert res["executed"] is False and res["reason_code"] == "tool_not_in_profile"


def test_stdio_http_toolset_parity():
    # stdio(core) tools/list 이름집합 == http(core) tools/list 이름집합
    out = _stdio_roundtrip("core", [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    stdio_names = {t["name"] for t in out[0]["result"]["tools"]}
    port, path_seg = _http_server("core")
    r = _http_post(port, path_seg, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    http_names = {t["name"] for t in r["result"]["tools"]}
    assert stdio_names == http_names == srv.exposed_tools("core")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
