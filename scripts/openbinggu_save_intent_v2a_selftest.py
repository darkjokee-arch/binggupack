#!/usr/bin/env python3
"""V2-A selftest — MCP 어댑터(폰 적재) + HMAC pull/admin(PC 러너) (로컬 wrangler dev 한정).

설계: docs/BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md
폰 claude.ai 커넥터가 보낼 MCP JSON-RPC를 흉내내 적재 경로를, HMAC으로 인출 경로를 검증한다.
금지: deploy 0 · live 0 · 외부 네트워크 0(127.0.0.1) · 실 DB 0. CF 1010 무관(로컬).

케이스:
  S0  dev 기동
  M1  MCP initialize 200 + protocolVersion/serverInfo
  M2  tools/list = save_intent 1개 + inputSchema(object)
  M3  적재 전 기본 비활성 — tools/call save_intent → isError(inbox_disabled)
  A1  admin/enable (HMAC) 200
  M4  tools/call save_intent 정상 → intent_id 반환 + text echo 0
  M5  worker intent_id == 러너 intent_hash (재해시 일치)
  M6  confirm 불일치 → isError(confirm_phrase_mismatch)
  M7  브라우저 Origin → 403 (적재 경로)
  M8  오경로키 /mcp2/<wrong> → 404
  P1  pull (HMAC) → 적재분 drain + 재해시 일치
  P2  무서명 pull → 401
  P3  2차 pull 0건 (atomic drain)
  A2  admin/disable (HMAC) → 이후 tools/call isError(inbox_disabled)
  R1  dev 로그·산출물 marker 잔존 0

전부 통과 = GATE=GO exit 0 / 실패 = BLOCK exit 1.
"""
import hashlib
import hmac as hmac_mod
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_WP = [os.path.abspath(os.path.join(HERE, "..", "workers_port")),
       os.path.abspath(os.path.join(HERE, "..", "hosted", "workers"))]
WP = next((p for p in _WP if os.path.isfile(os.path.join(p, "wrangler.save_mcp.toml"))), _WP[0])
PORT = 8796
BASE = "http://127.0.0.1:%d" % PORT

sys.path.insert(0, HERE)
from openbinggu_save_intent_outbox_runner import intent_hash  # noqa: E402

RESULTS = []


def rec(cid, desc, ok):
    RESULTS.append((cid, desc, ok))
    print("[%s] %s %s" % ("OK" if ok else "NG", cid, desc))


def sig(sm, body, ts=None):
    ts = str(ts if ts is not None else int(time.time()))
    bh = hashlib.sha256(body).hexdigest()
    mac = hmac_mod.new(sm.encode(), (ts + "." + bh).encode(), hashlib.sha256).hexdigest()
    return {"X-BGP-TS": ts, "X-BGP-SIG": mac}


def http(method, url, body=None, headers=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def mcp(mid, method, params=None):
    o = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        o["params"] = params
    return o


def main():
    pk = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    sm = hashlib.sha256(os.urandom(32)).hexdigest()
    marker = "CANARY-V2A-" + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    text = "합성 v2a — " + marker + " — 판단 후보 1: 보류. 판단 후보 5: 마진 확보."
    indices = [1, 5]
    confirm = "SAVE 1,5"
    iid_expect = intent_hash(text, indices, confirm)

    npx = shutil.which("npx") or "npx.cmd"
    logf = tempfile.NamedTemporaryFile(prefix="v2a_dev_", suffix=".log",
                                       delete=False, dir=tempfile.gettempdir())
    log_path = logf.name
    cmd = [npx, "wrangler", "dev", "--config", "wrangler.save_mcp.toml", "--port", str(PORT),
           "--var", "SAVE_PATH_" + "TOKEN" + ":" + pk,
           "--var", "SAVE_SIGN_" + "SECRET" + ":" + sm]
    proc = subprocess.Popen(cmd, cwd=WP, stdout=logf, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    mcp_url = BASE + "/mcp2/" + pk
    save = BASE + "/save2/" + pk
    eb = b"{}"
    try:
        t0 = time.time()
        ready = False
        while time.time() - t0 < 90:
            st, _ = http("POST", BASE + "/mcp2/_probe_", json.dumps(mcp(1, "ping")).encode())
            if st in (200, 400, 403, 404, 503):
                ready = True
                break
            time.sleep(1.0)
        rec("S0", "dev 기동", ready)
        if not ready:
            return finish(marker, log_path, logf, proc)

        st, rb = http("POST", mcp_url, json.dumps(mcp(1, "initialize", {"protocolVersion": "2025-06-18"})).encode())
        j = json.loads(rb) if st == 200 else {}
        rec("M1", "MCP initialize 200 + serverInfo",
            st == 200 and j.get("result", {}).get("serverInfo", {}).get("name") == "binggupack-save-intent")

        st, rb = http("POST", mcp_url, json.dumps(mcp(2, "tools/list")).encode())
        j = json.loads(rb) if st == 200 else {}
        tools = j.get("result", {}).get("tools", [])
        names = sorted(t["name"] for t in tools)
        rec("M2", "tools/list = preview + save_intent (2개)",
            names == ["conversation_capture_preview", "save_intent"])

        # 미리보기 도구 — save_intent 번호와 동일 체계 (read-only, 저장 0)
        prev_text = ("이 입찰은 마진이 낮아 보류한다. 낙찰하한율은 기초금액 대비 최저 투찰 비율이다. "
                     "백필 작업이 진행 중이다.")
        pcall = mcp(20, "tools/call", {"name": "conversation_capture_preview",
                                       "arguments": {"text": prev_text}})
        st, rb = http("POST", mcp_url, json.dumps(pcall).encode())
        j = json.loads(rb) if st == 200 else {}
        sc = j.get("result", {}).get("structuredContent", {})
        rec("M2b", "preview 도구 → candidates + nothing_saved",
            j.get("result", {}).get("isError") is False
            and isinstance(sc.get("candidates"), list) and sc.get("nothing_saved") is True)

        call = mcp(3, "tools/call", {"name": "save_intent",
                                     "arguments": {"text": text, "indices": indices, "confirm": confirm}})
        st, rb = http("POST", mcp_url, json.dumps(call).encode())
        j = json.loads(rb) if st == 200 else {}
        res = j.get("result", {})
        rec("M3", "기본 비활성 → tools/call isError(inbox_disabled)",
            res.get("isError") is True and "inbox_disabled" in json.dumps(res))

        st, _ = http("POST", save + "/admin/enable", eb, sig(sm, eb))
        rec("A1", "admin/enable (HMAC) 200", st == 200)

        st, rb = http("POST", mcp_url, json.dumps(call).encode())
        j = json.loads(rb) if st == 200 else {}
        res = j.get("result", {})
        sc = res.get("structuredContent", {})
        rec("M4", "tools/call 정상 → intent_id + text echo 0",
            res.get("isError") is False and sc.get("intent_id") and marker not in json.dumps(res))
        rec("M5", "worker intent_id == 러너 intent_hash", sc.get("intent_id") == iid_expect)

        bad = mcp(4, "tools/call", {"name": "save_intent",
                                    "arguments": {"text": text, "indices": indices, "confirm": "SAVE 9"}})
        st, rb = http("POST", mcp_url, json.dumps(bad).encode())
        rec("M6", "confirm 불일치 isError", '"isError": true' in rb or '"isError":true' in rb)

        st, _ = http("POST", mcp_url, json.dumps(call).encode(), {"Origin": "https://evil.example"})
        rec("M7", "브라우저 Origin 403 (적재)", st == 403)
        st, _ = http("POST", BASE + "/mcp2/wrongkey000", json.dumps(mcp(5, "ping")).encode())
        rec("M8", "오경로키 404", st == 404)

        st, rb = http("POST", save + "/pull", eb, sig(sm, eb))
        arr = json.loads(rb).get("intents", []) if st == 200 else []
        ok = len(arr) == 1 and intent_hash(arr[0]["text"], arr[0]["indices"],
                                           arr[0]["confirm"]) == arr[0]["intent_id"]
        rec("P1", "pull(HMAC) drain + 재해시 일치", ok)
        st, _ = http("POST", save + "/pull", eb)
        rec("P2", "무서명 pull 401", st == 401)
        st, rb = http("POST", save + "/pull", eb, sig(sm, eb))
        rec("P3", "2차 pull 0건 (atomic drain)", st == 200 and json.loads(rb).get("intents") == [])

        http("POST", save + "/admin/disable", eb, sig(sm, eb))
        st, rb = http("POST", mcp_url, json.dumps(call).encode())
        res = json.loads(rb).get("result", {}) if st == 200 else {}
        rec("A2", "disable 후 tools/call isError(inbox_disabled)",
            res.get("isError") is True and "inbox_disabled" in json.dumps(res))

        return finish(marker, log_path, logf, proc)
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def finish(marker, log_path, logf, proc):
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        time.sleep(2.0)
    logf.flush(); logf.close()
    log_text = open(log_path, "r", encoding="utf-8", errors="replace").read()
    hits = 1 if marker in log_text else 0
    wlog = os.path.join(WP, ".wrangler")
    for dp, _d, fs in (os.walk(wlog) if os.path.isdir(wlog) else []):
        for fn in fs:
            p = os.path.join(dp, fn)
            try:
                if os.path.getsize(p) < 50 * 1024 * 1024 and marker.encode() in open(p, "rb").read():
                    hits += 1
            except OSError:
                pass
    rec("R1", "dev 로그·산출물 marker 잔존 0 (hits=%d)" % hits, hits == 0)
    os.unlink(log_path)
    n_ok = sum(1 for _c, _d, ok in RESULTS if ok)
    gate = "GO" if n_ok == len(RESULTS) else "BLOCK"
    print("---")
    print("deploy=0  live=0  network=127.0.0.1-only  real_db=0  read_line_touched=0")
    print("V2-A GATE=%s (%d/%d)" % (gate, n_ok, len(RESULTS)))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    main()
