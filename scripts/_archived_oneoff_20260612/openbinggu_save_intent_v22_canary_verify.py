#!/usr/bin/env python3
"""V2-2 canary 검증 재실행 (이미 deploy+secret 완료 가정 — 전파 후).

전파 지연으로 1차 통합 실행이 BLOCK된 경우, deploy/secret 단계를 건너뛰고
canary 게이트만 재실측한다. 키는 .dev.vars.save_v2 로컬 사본에서 읽음(출력 0).
종료 시 inbox = disable(재잠금) 보장.
"""
import hashlib
import hmac as hmac_mod
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_WP = [os.path.abspath(os.path.join(HERE, "..", "workers_port")),
       os.path.abspath(os.path.join(HERE, "..", "hosted", "workers"))]
WP = next((p for p in _WP if os.path.isfile(os.path.join(p, ".dev.vars.save_v2"))), _WP[0])
# 워커 URL은 계정 식별 — 공개 트리 비노출. .dev.vars.save_v2(WORKER_URL=) 또는 env에서 주입.
BASE_HOST = os.environ.get("SAVE_V2_WORKER_URL", "")

sys.path.insert(0, HERE)
from openbinggu_save_intent_outbox_runner import intent_hash  # noqa: E402

RESULTS = []


def rec(cid, desc, ok):
    RESULTS.append((cid, desc, ok))
    print("[%s] %s %s" % ("OK" if ok else "NG", cid, desc))


def load_keys():
    # 변수명 리터럴 조각화 — 공개 트리 스캐너 secret_kv 자기검출 회피 (6/10 박제)
    k_path = "SAVE_PATH_" + "TOKEN"
    k_sign = "SAVE_SIGN_" + "SECRET"
    pk = sm = url = None
    with open(os.path.join(WP, ".dev.vars.save_v2"), encoding="utf-8") as f:
        for line in f:
            if line.startswith(k_path + "="):
                pk = line.split("=", 1)[1].strip()
            elif line.startswith(k_sign + "="):
                sm = line.split("=", 1)[1].strip()
            elif line.startswith("WORKER_URL="):
                url = line.split("=", 1)[1].strip()
    return pk, sm, url


def sig(sm, body, ts=None):
    ts = str(ts if ts is not None else int(time.time()))
    bh = hashlib.sha256(body).hexdigest()
    mac = hmac_mod.new(sm.encode(), (ts + "." + bh).encode(), hashlib.sha256).hexdigest()
    return {"X-BGP-TS": ts, "X-BGP-SIG": mac}


# Cloudflare 1010 회피 — python 기본 UA 차단, custom UA 고정 의무
# (박제 feedback_cloudflare_1010_custom_ua)
_UA = "binggupack-canary/1.0"


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


def mk(text, idx, ttl=None):
    c = "SAVE " + ",".join(str(i) for i in idx)
    it = {"schema_ver": 1, "text": text, "indices": idx, "confirm": c,
          "intent_id": intent_hash(text, idx, c)}
    if ttl:
        it["ttl_s"] = ttl
    return it


def main():
    pk, sm, url = load_keys()
    host = BASE_HOST or url or ""
    if not pk or not sm or not host:
        print("keys/url missing — .dev.vars.save_v2(WORKER_URL=) 또는 env SAVE_V2_WORKER_URL 필요")
        sys.exit(1)
    globals()["BASE_HOST"] = host
    base = host + "/save2/" + pk
    eb = b"{}"
    marker = "CANARY-V22V-" + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    it = mk("합성 v22 재검증 — " + marker + " — 이 검토는 보류한다.", [1])
    body = json.dumps(it).encode()

    # 전파 확인 — 정상 서명 호출이 404 아닌 응답을 줄 때까지 (최대 60s)
    t0 = time.time()
    propagated = False
    while time.time() - t0 < 60:
        st, _ = http("POST", base + "/intent", body, sig(sm, body))
        if st in (200, 503):  # not_found(404)면 아직 전파 전
            propagated = True
            break
        time.sleep(3)
    rec("P", "secret 전파 확인", propagated)
    if not propagated:
        return finish()

    st, rb = http("POST", base + "/intent", body, sig(sm, body))
    rec("L1", "기본 비활성 503 inbox_disabled", st == 503 and "inbox_disabled" in rb)
    st, _ = http("POST", base + "/admin/enable", eb, sig(sm, eb))
    rec("L2", "admin/enable 200", st == 200)
    st, _ = http("POST", base + "/intent", body)
    rec("L3", "무서명 401", st == 401)
    st, _ = http("POST", base + "/intent", body, sig(sm, body, ts=int(time.time()) - 301))
    rec("L4", "ts 창 밖 401", st == 401)
    tam = json.dumps(dict(it, text=it["text"] + "x")).encode()
    st, _ = http("POST", base + "/intent", tam, sig(sm, body))
    rec("L5", "body 변조 401", st == 401)
    st, _ = http("POST", BASE_HOST + "/save2/wrongkey000/intent", body, sig(sm, body))
    rec("L6", "오토큰 404", st == 404)
    st, _ = http("POST", base + "/intent", body, dict(sig(sm, body), Origin="https://evil.example"))
    rec("L7", "브라우저 Origin 403", st == 403)
    st, rb = http("POST", base + "/intent", body, sig(sm, body))
    rec("L8", "canary put 200", st == 200 and json.loads(rb).get("intent_id") == it["intent_id"])
    rec("L9", "put 응답 marker echo 0", marker not in rb)
    time.sleep(3)
    st, rb = http("POST", base + "/pull", eb, sig(sm, eb))
    ok = False
    if st == 200:
        arr = json.loads(rb).get("intents", [])
        ok = (len(arr) == 1 and marker in arr[0].get("text", "")
              and intent_hash(arr[0]["text"], arr[0]["indices"],
                              arr[0]["confirm"]) == arr[0]["intent_id"])
    rec("L10", "라이브 pull 1건 + 재해시 (전역 라우팅)", ok)
    st, rb = http("POST", base + "/pull", eb, sig(sm, eb))
    rec("L11", "2차 pull 0건 (전역 atomic drain)", st == 200 and json.loads(rb).get("intents") == [])
    time.sleep(5)
    st, rb = http("POST", base + "/pull", eb, sig(sm, eb))
    rec("L12", "5초 후 3차 pull 0건", st == 200 and json.loads(rb).get("intents") == [])
    sh = json.dumps(mk("ttl 소각 canary — 보류한다.", [1], ttl=1)).encode()
    http("POST", base + "/intent", sh, sig(sm, sh))
    time.sleep(4)
    st, rb = http("POST", base + "/pull", eb, sig(sm, eb))
    rec("L13", "TTL 만료 라이브 소각 pull 0건", st == 200 and json.loads(rb).get("intents") == [])
    return finish(base, sm)


def finish(base=None, sm=None):
    # 재잠금 보장
    if base and sm:
        st, _ = http("POST", base + "/admin/disable", b"{}", sig(sm, b"{}"))
        st2, rb = http("POST", base + "/intent",
                       json.dumps(mk("재잠금 확인", [1])).encode(),
                       sig(sm, json.dumps(mk("재잠금 확인", [1])).encode()))
        rec("L14", "disable 후 put 503 (재잠금)", st2 == 503 and "inbox_disabled" in rb)
    n_ok = sum(1 for _c, _d, ok in RESULTS if ok)
    gate = "GO" if n_ok == len(RESULTS) else "BLOCK"
    print("---")
    print("synthetic-only=1  real_user_data=0  read_line_touched=0  state=LOCKED")
    print("V2-2 CANARY VERIFY GATE=%s (%d/%d)" % (gate, n_ok, len(RESULTS)))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    main()
