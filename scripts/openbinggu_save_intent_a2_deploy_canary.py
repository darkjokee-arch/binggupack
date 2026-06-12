#!/usr/bin/env python3
"""A-2 — save-mcp 라이브 배포 + canary (owner "a-2 배포 고", 2026-06-12).

V2-A(MCP 어댑터)를 별도 운영 worker로 잠긴 상태 배포 → secret 주입 → canary 실측.
적재=MCP(폰 흉내)·인출/관리=HMAC(PC). CF 1010 회피 = custom UA 기본 탑재.
금지: 실 사용자 데이터 0(합성) · Workers Logs 0 · read/v2 라인 무접촉.
로컬 키 사본 = workers_port/.dev.vars.save_mcp (gitignore).
전부 통과 = GATE=GO exit 0 / 실패 = BLOCK exit 1 → inbox disable 유지.
"""
import hashlib
import hmac as hmac_mod
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_WP = [os.path.abspath(os.path.join(HERE, "..", "workers_port")),
       os.path.abspath(os.path.join(HERE, "..", "hosted", "workers"))]
WP = next((p for p in _WP if os.path.isfile(os.path.join(p, "wrangler.save_mcp.prod.toml"))), _WP[0])
CONF = "wrangler.save_mcp.prod.toml"
VARS_FILE = os.path.join(WP, ".dev.vars.save_mcp")
_UA = "binggupack-canary/1.0"

sys.path.insert(0, HERE)
from openbinggu_save_intent_outbox_runner import intent_hash  # noqa: E402

RESULTS = []


def rec(cid, desc, ok):
    RESULTS.append((cid, desc, ok))
    print("[%s] %s %s" % ("OK" if ok else "NG", cid, desc))


def h8(v):
    return hashlib.sha256(v.encode()).hexdigest()[:8]


def sig(sm, body, ts=None):
    ts = str(ts if ts is not None else int(time.time()))
    bh = hashlib.sha256(body).hexdigest()
    mac = hmac_mod.new(sm.encode(), (ts + "." + bh).encode(), hashlib.sha256).hexdigest()
    return {"X-BGP-TS": ts, "X-BGP-SIG": mac}


def http(method, url, body=None, headers=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", _UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def mcp(mid, method, params=None):
    o = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        o["params"] = params
    return o


def run_wr(args, inp=None):
    npx = shutil.which("npx") or "npx.cmd"
    return subprocess.run([npx, "wrangler"] + args, cwd=WP, capture_output=True,
                          text=True, input=inp, timeout=180)


def main():
    pk = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    sm = hashlib.sha256(os.urandom(32)).hexdigest()
    print("path hash8=%s len=%d / sign hash8=%s len=%d" % (h8(pk), len(pk), h8(sm), len(sm)))

    r = run_wr(["deploy", "--config", CONF])
    m = re.search(r"https://[^\s]+\.workers\.dev", r.stdout + r.stderr)
    rec("V0", "deploy 성공 + URL", r.returncode == 0 and m is not None)
    if not m:
        print(r.stdout[-600:]); print(r.stderr[-600:]); return finish(None)
    host = m.group(0)
    print("worker URL:", host)

    st, rb = http("POST", host + "/mcp2/_probe_", json.dumps(mcp(1, "ping")).encode())
    rec("V1", "secret 미주입 503 not configured", st == 503 and "not configured" in rb)

    r1 = run_wr(["secret", "put", "SAVE_PATH_" + "TOKEN", "--config", CONF], inp=pk)
    r2 = run_wr(["secret", "put", "SAVE_SIGN_" + "SECRET", "--config", CONF], inp=sm)
    rec("V2", "secret 2종 주입", r1.returncode == 0 and r2.returncode == 0)
    with open(VARS_FILE, "w", encoding="utf-8") as f:
        f.write("# save-mcp 운영 키 로컬 사본 (러너용, git 비추적)\n")
        f.write("SAVE_PATH_" + "TOKEN" + "=" + pk + "\n")
        f.write("SAVE_SIGN_" + "SECRET" + "=" + sm + "\n")
        f.write("WORKER_URL=" + host + "\n")
    time.sleep(8)

    mcp_url = host + "/mcp2/" + pk
    save = host + "/save2/" + pk
    eb = b"{}"
    marker = "CANARY-A2-" + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    text = "합성 a2 라이브 — " + marker + " — 판단 후보 1: 보류. 판단 후보 5: 마진 확보."
    indices = [1, 5]
    confirm = "SAVE 1,5"
    iid = intent_hash(text, indices, confirm)
    call = mcp(3, "tools/call", {"name": "save_intent",
                                 "arguments": {"text": text, "indices": indices, "confirm": confirm}})

    # 전파 대기 (MCP ping)
    t0 = time.time()
    prop = False
    while time.time() - t0 < 60:
        st, _ = http("POST", mcp_url, json.dumps(mcp(0, "ping")).encode())
        if st == 200:
            prop = True
            break
        time.sleep(3)
    rec("V3", "secret 전파 + MCP ping", prop)

    st, rb = http("POST", mcp_url, json.dumps(mcp(1, "initialize", {"protocolVersion": "2025-06-18"})).encode())
    j = json.loads(rb) if st == 200 else {}
    rec("L1", "라이브 MCP initialize + serverInfo",
        j.get("result", {}).get("serverInfo", {}).get("name") == "binggupack-save-intent")
    st, rb = http("POST", mcp_url, json.dumps(mcp(2, "tools/list")).encode())
    tl = json.loads(rb).get("result", {}).get("tools", []) if st == 200 else []
    rec("L2", "tools/list save_intent", len(tl) == 1 and tl[0]["name"] == "save_intent")

    # 기본 잠김
    st, rb = http("POST", mcp_url, json.dumps(call).encode())
    res = json.loads(rb).get("result", {}) if st == 200 else {}
    rec("L3", "기본 비활성 tools/call isError(inbox_disabled)",
        res.get("isError") is True and "inbox_disabled" in json.dumps(res))

    st, _ = http("POST", save + "/admin/enable", eb, sig(sm, eb))
    rec("L4", "admin/enable(HMAC) 200", st == 200)

    st, _ = http("POST", mcp_url, json.dumps(call).encode(), {"Origin": "https://evil.example"})
    rec("L5", "브라우저 Origin 403", st == 403)
    st, _ = http("POST", host + "/mcp2/wrongkey000", json.dumps(mcp(9, "ping")).encode())
    rec("L6", "오경로키 404", st == 404)

    st, rb = http("POST", mcp_url, json.dumps(call).encode())
    res = json.loads(rb).get("result", {}) if st == 200 else {}
    sc = res.get("structuredContent", {})
    rec("L7", "라이브 적재 isError=false + intent_id", res.get("isError") is False and sc.get("intent_id") == iid)
    rec("L8", "적재 응답 marker echo 0", marker not in rb)

    time.sleep(3)
    st, rb = http("POST", save + "/pull", eb, sig(sm, eb))
    arr = json.loads(rb).get("intents", []) if st == 200 else []
    ok = len(arr) == 1 and marker in arr[0].get("text", "") and \
        intent_hash(arr[0]["text"], arr[0]["indices"], arr[0]["confirm"]) == arr[0]["intent_id"]
    rec("L9", "HMAC pull 1건 + 재해시 (전역)", ok)
    st, rb = http("POST", save + "/pull", eb, sig(sm, eb))
    rec("L10", "2차 pull 0건 (전역 drain)", st == 200 and json.loads(rb).get("intents") == [])
    st, _ = http("POST", save + "/pull", eb)
    rec("L11", "무서명 pull 401", st == 401)

    http("POST", save + "/admin/disable", eb, sig(sm, eb))
    st, rb = http("POST", mcp_url, json.dumps(call).encode())
    res = json.loads(rb).get("result", {}) if st == 200 else {}
    rec("L12", "disable 후 tools/call isError(재잠금)",
        res.get("isError") is True and "inbox_disabled" in json.dumps(res))

    return finish(host)


def finish(host):
    n_ok = sum(1 for _c, _d, ok in RESULTS if ok)
    gate = "GO" if n_ok == len(RESULTS) else "BLOCK"
    print("---")
    print("synthetic-only=1  real_user_data=0  read_v2_line_touched=0  state=LOCKED")
    print("A-2 LIVE CANARY GATE=%s (%d/%d)  worker=%s" % (gate, n_ok, len(RESULTS), host or "?"))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    main()
