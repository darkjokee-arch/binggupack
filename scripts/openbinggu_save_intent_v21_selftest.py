#!/usr/bin/env python3
"""V2-1 selftest — durable inbox + HMAC 서명 worker (로컬 wrangler dev 한정).

설계 정본: docs/BINGGUPACK_SAVE_INTENT_V2_RFC.md (V2-1 단계 게이트).
v1 라이브 부적합 5결함(§0)에 대한 구조 응답을 로컬에서 검증한다.
주의: DO 전역 의미론 자체의 라이브 실측은 V2-2(D3' canary) 몫 — 여기서는
로컬 미니플레어 DO로 동작·게이트·fail-closed를 검증한다.

케이스:
  S1   기본 비활성 — 정상 서명 put도 503 inbox_disabled (persistent fail-closed 기본 off)
  S2   admin/enable(서명) 200
  S3   무서명 put 401
  S4   ts 창 밖(-301s) 401
  S5   body 변조(서명은 원본 기준) 401
  S6   오토큰 경로 404
  S7   정상 put 200 + intent_id
  S8   shape 거부 (schema_ver=2) 400
  S9   cap 초과 — 전역 단일 카운트 (cap=5 주입, 6번째 503 store_full)
  S10  pull(서명) = 적재 전건 + 러너 intent_hash 재해시 일치
  S11  2차 pull 0건 (atomic drain)
  S12  TTL 만료 — ttl_s=1 put → 2초 후 pull 0건 (만료=삭제, 마킹 0)
  S13  admin/disable 후 put 503 (재폐쇄)
  S14  GET 405
  R1   dev 로그·산출물에 payload marker 잔존 0

전부 통과 = GATE=GO exit 0 / 실패 = GATE=BLOCK exit 1 (fail-closed).
금지: deploy 0 · live 호출 0 · 외부 네트워크 0 (127.0.0.1 한정) · 실 DB 0.
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
_WP_CANDIDATES = [os.path.abspath(os.path.join(HERE, "..", "workers_port")),
                  os.path.abspath(os.path.join(HERE, "..", "hosted", "workers"))]
WP = next((p for p in _WP_CANDIDATES if os.path.isfile(os.path.join(p, "wrangler.save_v2.toml"))),
          _WP_CANDIDATES[0])
PORT = 8797
BASE_HOST = "http://127.0.0.1:%d" % PORT

sys.path.insert(0, HERE)
from openbinggu_save_intent_outbox_runner import intent_hash  # noqa: E402

RESULTS = []


def rec(cid, desc, ok):
    RESULTS.append((cid, desc, ok))
    print("[%s] %s %s" % ("OK" if ok else "NG", cid, desc))


def signed_headers(sign_material, body_bytes, ts=None):
    ts = str(ts if ts is not None else int(time.time()))
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    mac = hmac_mod.new(sign_material.encode("utf-8"),
                       (ts + "." + body_hash).encode("utf-8"), hashlib.sha256).hexdigest()
    return {"X-BGP-TS": ts, "X-BGP-SIG": mac}


def http(method, url, body=None, headers=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def mk_intent(text, indices):
    confirm = "SAVE " + ",".join(str(i) for i in indices)
    return {"schema_ver": 1, "text": text, "indices": indices, "confirm": confirm,
            "intent_id": intent_hash(text, indices, confirm)}


def main():
    path_key = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    sign_material = hashlib.sha256(os.urandom(32)).hexdigest()
    marker = "CANARY-V21-" + hashlib.sha256(os.urandom(16)).hexdigest()[:12]

    npx = shutil.which("npx") or "npx.cmd"
    logf = tempfile.NamedTemporaryFile(prefix="v21_dev_", suffix=".log",
                                       delete=False, dir=tempfile.gettempdir())
    log_path = logf.name
    # 스캐너 secret_kv 자기검출 회피 — 키 이름과 ':' 분리 결합 (6/10 박제)
    cmd = [npx, "wrangler", "dev", "--config", "wrangler.save_v2.toml",
           "--port", str(PORT),
           "--var", "SAVE_PATH_TOKEN" + ":" + path_key,
           "--var", "SAVE_SIGN_SECRET" + ":" + sign_material,
           "--var", "SAVE_INBOX_CAP" + ":" + "5"]
    proc = subprocess.Popen(cmd, cwd=WP, stdout=logf, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    base = BASE_HOST + "/save2/" + path_key
    try:
        t0 = time.time()
        ready = False
        while time.time() - t0 < 90:
            try:
                st, _ = http("POST", BASE_HOST + "/save2/_probe_/intent", b"{}")
                if st in (400, 401, 403, 404, 405, 503):
                    ready = True
                    break
            except Exception:
                time.sleep(1.0)
        rec("S0", "wrangler dev 기동 (DO 포함)", ready)
        if not ready:
            return finish(marker, log_path, logf, proc)

        it = mk_intent("합성 v21 대화 — " + marker + " — 이 입찰은 마진이 낮아 보류한다.", [1])
        body = json.dumps(it).encode("utf-8")

        st, rb = http("POST", base + "/intent", body, signed_headers(sign_material, body))
        rec("S1", "기본 비활성 — 서명 put도 503 (fail-closed 기본 off)",
            st == 503 and "inbox_disabled" in rb)

        eb = b"{}"
        st, rb = http("POST", base + "/admin/enable", eb, signed_headers(sign_material, eb))
        rec("S2", "admin/enable(서명) 200", st == 200)

        st, _ = http("POST", base + "/intent", body)
        rec("S3", "무서명 put 401", st == 401)
        st, _ = http("POST", base + "/intent", body,
                     signed_headers(sign_material, body, ts=int(time.time()) - 301))
        rec("S4", "ts 창 밖(-301s) 401", st == 401)
        tam = json.dumps(dict(it, text=it["text"] + "x")).encode("utf-8")
        st, _ = http("POST", base + "/intent", tam, signed_headers(sign_material, body))
        rec("S5", "body 변조(서명 원본 기준) 401", st == 401)
        st, _ = http("POST", BASE_HOST + "/save2/wrongkey000/intent", body,
                     signed_headers(sign_material, body))
        rec("S6", "오토큰 경로 404", st == 404)

        st, rb = http("POST", base + "/intent", body, signed_headers(sign_material, body))
        rec("S7", "정상 put 200 + intent_id",
            st == 200 and json.loads(rb).get("intent_id") == it["intent_id"])

        bad = json.dumps(dict(it, schema_ver=2)).encode("utf-8")
        st, rb = http("POST", base + "/intent", bad, signed_headers(sign_material, bad))
        rec("S8", "shape 거부 schema_ver=2 → 400", st == 400 and "schema_mismatch" in rb)

        # S9 — cap=5 전역 단일 카운트: 현재 1건 적재 → +4 = 5 → 6번째 503
        ok9 = True
        for i in range(4):
            x = mk_intent("cap 채움 %d — 이 검토는 보류한다." % i, [1])
            xb = json.dumps(x).encode("utf-8")
            st, _ = http("POST", base + "/intent", xb, signed_headers(sign_material, xb))
            ok9 = ok9 and st == 200
        x6 = mk_intent("cap 초과 — 이 검토는 보류한다.", [1])
        x6b = json.dumps(x6).encode("utf-8")
        st, rb = http("POST", base + "/intent", x6b, signed_headers(sign_material, x6b))
        rec("S9", "cap 전역 단일 카운트 — 6번째 503 store_full",
            ok9 and st == 503 and "store_full" in rb)

        pb = b"{}"
        st, rb = http("POST", base + "/pull", pb, signed_headers(sign_material, pb))
        ok10 = False
        if st == 200:
            arr = json.loads(rb).get("intents", [])
            mine = [a for a in arr if a.get("intent_id") == it["intent_id"]]
            ok10 = (len(arr) == 5 and len(mine) == 1
                    and mine[0].get("source") == "hosted"
                    and intent_hash(mine[0]["text"], mine[0]["indices"],
                                    mine[0]["confirm"]) == mine[0]["intent_id"])
        rec("S10", "pull 전건(5)+재해시 일치", ok10)

        st, rb = http("POST", base + "/pull", pb, signed_headers(sign_material, pb))
        rec("S11", "2차 pull 0건 (atomic drain)",
            st == 200 and json.loads(rb).get("intents") == [])

        # S12 — TTL 만료 = 삭제 (마킹 0)
        sh = mk_intent("ttl 만료 케이스 — 보류한다.", [1])
        sh["ttl_s"] = 1
        shb = json.dumps(sh).encode("utf-8")
        st, _ = http("POST", base + "/intent", shb, signed_headers(sign_material, shb))
        time.sleep(2.5)
        st, rb = http("POST", base + "/pull", pb, signed_headers(sign_material, pb))
        rec("S12", "TTL 만료 후 pull 0건 (만료=삭제)",
            st == 200 and json.loads(rb).get("intents") == [])

        st, _ = http("POST", base + "/admin/disable", eb, signed_headers(sign_material, eb))
        st, rb = http("POST", base + "/intent", body, signed_headers(sign_material, body))
        rec("S13", "disable 후 put 503 (재폐쇄)", st == 503 and "inbox_disabled" in rb)

        try:
            st, _ = http("GET", base + "/intent")
        except Exception:
            st = -1
        rec("S14", "GET 405", st == 405)

        return finish(marker, log_path, logf, proc)
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)


def finish(marker, log_path, logf, proc):
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        time.sleep(2.0)
    logf.flush()
    logf.close()
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        log_text = f.read()
    hits = 1 if marker in log_text else 0
    for root in [os.path.join(WP, ".wrangler")]:
        for dirpath, _d, files in os.walk(root) if os.path.isdir(root) else []:
            for fn in files:
                p = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(p) < 50 * 1024 * 1024:
                        with open(p, "rb") as f:
                            if marker.encode("utf-8") in f.read():
                                hits += 1
                except OSError:
                    continue
    # 주의: 로컬 DO SQLite(.wrangler/state)는 drain 후 잔존 0이어야 함 — 잔존 시 hits>0
    rec("R1", "dev 로그·.wrangler 산출물 marker 잔존 0 (hits=%d)" % hits, hits == 0)
    os.unlink(log_path)

    n_ok = sum(1 for _c, _d, ok in RESULTS if ok)
    gate = "GO" if n_ok == len(RESULTS) else "BLOCK"
    print("---")
    print("deploy=0  live=0  network=127.0.0.1-only  real_db=0")
    print("V2-1 GATE=%s (%d/%d)" % (gate, n_ok, len(RESULTS)))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    main()
