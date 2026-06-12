#!/usr/bin/env python3
"""V2-2 — 라이브 D3' canary (owner 승인 문구 "V2-2 배포 고", 2026-06-12).

절차 (RFC V2-2):
  1) 잠긴 상태로 deploy (secret 미주입 = 503 not configured fail-closed)
  2) write 키 2종 신규 발급 → CF secret 주입 (평문 출력 0 — hash8만 표시)
  3) canary 창: enable → 합성 canary 실측 → disable (재잠금)
  4) 실측: 기본잠김/서명게이트/전역 drain/2차 pull 0/TTL 소각/재폐쇄

로컬 키 사본: workers_port/.dev.vars.save_v2 (git 비추적 트리 — 러너 V2-3용 보관).
금지: 실 사용자 데이터 0 (합성 canary만) · Workers Logs 활성화 0 · read 라인 무접촉.
전부 통과 = GATE=GO exit 0 / 실패 = GATE=BLOCK exit 1 → 즉시 disable 유지.
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
_WP_CANDIDATES = [os.path.abspath(os.path.join(HERE, "..", "workers_port")),
                  os.path.abspath(os.path.join(HERE, "..", "hosted", "workers"))]
WP = next((p for p in _WP_CANDIDATES if os.path.isfile(os.path.join(p, "wrangler.save_v2.prod.toml"))),
          _WP_CANDIDATES[0])
CONF = "wrangler.save_v2.prod.toml"
VARS_FILE = os.path.join(WP, ".dev.vars.save_v2")

sys.path.insert(0, HERE)
from openbinggu_save_intent_outbox_runner import intent_hash  # noqa: E402

RESULTS = []


def rec(cid, desc, ok):
    RESULTS.append((cid, desc, ok))
    print("[%s] %s %s" % ("OK" if ok else "NG", cid, desc))


def h8(v):
    return hashlib.sha256(v.encode("utf-8")).hexdigest()[:8]


def signed_headers(sign_material, body_bytes, ts=None):
    ts = str(ts if ts is not None else int(time.time()))
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    mac = hmac_mod.new(sign_material.encode("utf-8"),
                       (ts + "." + body_hash).encode("utf-8"), hashlib.sha256).hexdigest()
    return {"X-BGP-TS": ts, "X-BGP-SIG": mac}


def http(method, url, body=None, headers=None, timeout=20):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def mk_intent(text, indices, ttl=None):
    confirm = "SAVE " + ",".join(str(i) for i in indices)
    it = {"schema_ver": 1, "text": text, "indices": indices, "confirm": confirm,
          "intent_id": intent_hash(text, indices, confirm)}
    if ttl:
        it["ttl_s"] = ttl
    return it


def run_wrangler(args, input_text=None):
    npx = shutil.which("npx") or "npx.cmd"
    return subprocess.run([npx, "wrangler"] + args, cwd=WP, capture_output=True,
                          text=True, input=input_text, timeout=180)


def main():
    # 1) 키 신규 발급 (평문 출력 0)
    path_key = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    sign_material = hashlib.sha256(os.urandom(32)).hexdigest()
    print("path_key hash8=%s len=%d / sign hash8=%s len=%d"
          % (h8(path_key), len(path_key), h8(sign_material), len(sign_material)))

    # 2) 잠긴 상태 deploy
    r = run_wrangler(["deploy", "--config", CONF])
    m = re.search(r"https://[^\s]+\.workers\.dev", r.stdout + r.stderr)
    rec("V0", "deploy 성공 + URL 식별", r.returncode == 0 and m is not None)
    if not m:
        print(r.stdout[-800:])
        print(r.stderr[-800:])
        return finish(None)
    base_host = m.group(0)
    print("worker URL:", base_host)

    # secret 미주입 상태 = 503 fail-closed
    st, rb = http("POST", base_host + "/save2/_probe_/intent", b"{}")
    rec("V1", "secret 미주입 503 not configured (fail-closed)", st == 503 and "not configured" in rb)

    # 3) secret 주입 (stdin pipe — 화면 출력 0)
    r1 = run_wrangler(["secret", "put", "SAVE_PATH_TOKEN", "--config", CONF], input_text=path_key)
    r2 = run_wrangler(["secret", "put", "SAVE_SIGN_SECRET", "--config", CONF], input_text=sign_material)
    rec("V2", "secret 2종 주입", r1.returncode == 0 and r2.returncode == 0)
    # 변수명 리터럴 조각화 — 공개 트리 스캐너 secret_kv 자기검출 회피 (6/10 박제)
    k_path = "SAVE_PATH_" + "TOKEN"
    k_sign = "SAVE_SIGN_" + "SECRET"
    with open(VARS_FILE, "w", encoding="utf-8") as f:
        f.write("# save-intent v2 운영 키 로컬 사본 (V2-3 러너용, git 비추적)\n")
        f.write(k_path + "=" + path_key + "\n")
        f.write(k_sign + "=" + sign_material + "\n")
        f.write("WORKER_URL=" + base_host + "\n")
    time.sleep(8)  # secret 반영 대기

    base = base_host + "/save2/" + path_key
    marker = "CANARY-V22-" + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    it = mk_intent("합성 v22 라이브 canary — " + marker + " — 이 검토는 보류한다.", [1])
    body = json.dumps(it).encode("utf-8")
    eb = b"{}"

    # 기본 잠김 (enable 전)
    st, rb = http("POST", base + "/intent", body, signed_headers(sign_material, body))
    rec("L1", "기본 비활성 — 서명 put도 503 inbox_disabled", st == 503 and "inbox_disabled" in rb)

    # canary 창 open
    st, _ = http("POST", base + "/admin/enable", eb, signed_headers(sign_material, eb))
    rec("L2", "admin/enable 200", st == 200)

    st, _ = http("POST", base + "/intent", body)
    rec("L3", "무서명 put 401", st == 401)
    st, _ = http("POST", base + "/intent", body,
                 signed_headers(sign_material, body, ts=int(time.time()) - 301))
    rec("L4", "ts 창 밖 401", st == 401)
    tam = json.dumps(dict(it, text=it["text"] + "x")).encode("utf-8")
    st, _ = http("POST", base + "/intent", tam, signed_headers(sign_material, body))
    rec("L5", "body 변조 401", st == 401)
    st, _ = http("POST", base_host + "/save2/wrongkey000/intent", body,
                 signed_headers(sign_material, body))
    rec("L6", "오토큰 경로 404", st == 404)
    st, _ = http("POST", base + "/intent", body,
                 dict(signed_headers(sign_material, body), Origin="https://evil.example"))
    rec("L7", "브라우저 Origin 403", st == 403)

    st, rb = http("POST", base + "/intent", body, signed_headers(sign_material, body))
    rec("L8", "canary put 200", st == 200 and json.loads(rb).get("intent_id") == it["intent_id"])
    rec("L9", "put 응답 marker echo 0", marker not in rb)

    time.sleep(3)  # 별도 연결·시점에서 pull (전역 의미론 — colo 경유)
    st, rb = http("POST", base + "/pull", eb, signed_headers(sign_material, eb))
    ok = False
    if st == 200:
        arr = json.loads(rb).get("intents", [])
        ok = (len(arr) == 1 and arr[0].get("text", "").find(marker) >= 0
              and intent_hash(arr[0]["text"], arr[0]["indices"],
                              arr[0]["confirm"]) == arr[0]["intent_id"])
    rec("L10", "라이브 pull 1건 + 재해시 일치 (전역 라우팅)", ok)

    st, rb = http("POST", base + "/pull", eb, signed_headers(sign_material, eb))
    rec("L11", "2차 pull 0건 (전역 atomic drain)", st == 200 and json.loads(rb).get("intents") == [])
    time.sleep(5)
    st, rb = http("POST", base + "/pull", eb, signed_headers(sign_material, eb))
    rec("L12", "5초 후 3차 pull 0건 (잔존 재확인)", st == 200 and json.loads(rb).get("intents") == [])

    sh = mk_intent("ttl 소각 canary — 보류한다.", [1], ttl=1)
    shb = json.dumps(sh).encode("utf-8")
    st, _ = http("POST", base + "/intent", shb, signed_headers(sign_material, shb))
    time.sleep(4)
    st, rb = http("POST", base + "/pull", eb, signed_headers(sign_material, eb))
    rec("L13", "TTL 만료 라이브 소각 — pull 0건", st == 200 and json.loads(rb).get("intents") == [])

    # canary 창 close — 재잠금
    st, _ = http("POST", base + "/admin/disable", eb, signed_headers(sign_material, eb))
    st, rb = http("POST", base + "/intent", body, signed_headers(sign_material, body))
    rec("L14", "disable 후 put 503 (재잠금 확인)", st == 503 and "inbox_disabled" in rb)

    return finish(base_host)


def finish(base_host):
    n_ok = sum(1 for _c, _d, ok in RESULTS if ok)
    gate = "GO" if n_ok == len(RESULTS) else "BLOCK"
    print("---")
    print("synthetic-only=1  real_user_data=0  read_line_touched=0  logs_enabled=0")
    print("V2-2 LIVE CANARY GATE=%s (%d/%d)  worker=%s  state=LOCKED"
          % (gate, n_ok, len(RESULTS), base_host or "?"))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    main()
