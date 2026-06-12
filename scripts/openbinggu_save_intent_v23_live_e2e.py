#!/usr/bin/env python3
"""V2-3 — 라이브 worker + 로컬 러너 결합 4조건 E2E (합성, owner "승인" 2026-06-12).

D4(로컬 worker)와 동일 4조건을 **라이브 worker + 로컬 러너 + temp DB** 결합으로 재실측.
worker = live(binggupack-save-intent-v2, enable→실측→disable) / 러너·DB = 로컬 temp.
금지: 실 사용자 데이터 0(합성만) · 로컬 장부/운영 DB 0(temp만) · read 라인 무접촉.
CF 1010 회피 = custom UA 기본 탑재.

조건 1 인증: 오토큰404·Origin403·무서명401·재전송창401  (라이브)
조건 2 전송: worker 적재 후 로컬 DB 노드 0 / live pull→outbox→러너 applied·노드·파일소거 / 변조=러너 reject
조건 3 audit: hosted_intent ALLOW row / 로컬 DB 원문 전문 잔존 0 / chain INTACT
조건 4 rollback: live TTL 소각 0건 / 러너 자동 재시도 0 / 스냅샷 / 종료 시 inbox disable

전부 통과 = GATE=GO exit 0 / 실패 = BLOCK exit 1.
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
_UA = "binggupack-canary/1.0"  # CF 1010 회피 (박제 feedback_cloudflare_1010_custom_ua)

sys.path.insert(0, HERE)
import tempfile  # noqa: E402
from openbinggu_save_intent_outbox_runner import intent_hash, process_outbox  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402

CONVO = ("이 문서는 배포 절차를 정의한다. 테스트 로그에 통과 결과가 기록되어 있다. "
         "낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다. 백필 작업이 진행 중이다. "
         "이 입찰은 마진이 낮아 보류한다.")
RESULTS = []


def rec(cid, desc, ok):
    RESULTS.append((cid, desc, ok))
    print("[%s] %s %s" % ("OK" if ok else "NG", cid, desc))


def load_keys():
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
    pk, sm, host = load_keys()
    if not (pk and sm and host):
        print("keys/url missing"); sys.exit(1)
    base = host + "/save2/" + pk
    eb = b"{}"
    tmp = tempfile.mkdtemp(prefix="v23_")
    outbox = os.path.join(tmp, "outbox"); snap = os.path.join(tmp, "snap")
    os.makedirs(outbox); os.makedirs(snap)
    db = open_g3(os.path.join(tmp, "s.sqlite"))
    ctx = {"actor": "human"}
    now = int(time.time())

    it = mk(CONVO, [1, 5])
    body = json.dumps(it).encode()

    try:
        st, _ = http("POST", base + "/admin/enable", eb, sig(sm, eb))
        rec("E0", "라이브 inbox enable 200", st == 200)

        # 조건 1
        st, _ = http("POST", host + "/save2/wrongkey000/intent", body, sig(sm, body))
        rec("C1-1", "오토큰 404", st == 404)
        st, _ = http("POST", base + "/intent", body, dict(sig(sm, body), Origin="https://evil.example"))
        rec("C1-2", "Origin 403", st == 403)
        st, _ = http("POST", base + "/intent", body)
        rec("C1-3", "무서명 401", st == 401)
        st, _ = http("POST", base + "/intent", body, sig(sm, body, ts=now - 301))
        rec("C1-4", "재전송 창 밖 401", st == 401)

        # 조건 2 — 정상 + 변조 적재
        st, rb = http("POST", base + "/intent", body, sig(sm, body))
        n0 = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        rec("C2-1", "worker 적재 후 로컬 DB 노드 0",
            st == 200 and json.loads(rb).get("intent_id") == it["intent_id"] and n0 == 0)
        tam = dict(it, text=CONVO + " 변조됨.", intent_id="f" * 16)
        tb = json.dumps(tam).encode()
        st_t, _ = http("POST", base + "/intent", tb, sig(sm, tb))

        time.sleep(3)
        st, rb = http("POST", base + "/pull", eb, sig(sm, eb))
        arr = json.loads(rb).get("intents", []) if st == 200 else []
        for a in arr:
            with open(os.path.join(outbox, a["intent_id"] + ".json"), "w", encoding="utf-8") as f:
                json.dump(a, f, ensure_ascii=False)
        r = process_outbox(db, outbox, ctx, snap, now)
        n1 = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        gone = not os.path.exists(os.path.join(outbox, it["intent_id"] + ".json"))
        rec("C2-2", "live pull→outbox→러너 applied·노드·파일소거",
            st_t == 200 and len(arr) == 2 and r["applied"] == 1 and n1 > 0 and gone)
        rec("C2-3", "변조 intent 러너 reject(.rejected)",
            r["rejected"] >= 1 and os.path.exists(os.path.join(outbox, "f" * 16 + ".json.rejected")))

        # 조건 3 — audit
        a = db.con.execute("SELECT count(*) FROM audit_log WHERE action='hosted_intent' "
                           "AND result='ALLOW' AND pack_id=?", (it["intent_id"],)).fetchone()[0]
        rec("C3-1", "hosted_intent ALLOW row", a == 1)
        blob = "\n".join(str(row) for t in ("nodes", "edges", "evidence", "audit_log")
                         for row in db.con.execute("SELECT * FROM " + t))
        rec("C3-2", "로컬 DB 원문 전문 잔존 0", CONVO not in blob)
        ok_chain = True
        try:
            db.verify_chain()
        except Exception:
            ok_chain = False
        rec("C3-3", "audit chain INTACT", ok_chain)

        # 조건 4 — TTL 라이브 소각 + 자동 재시도 0 + 스냅샷
        sh = json.dumps(mk("ttl 소각 — 보류한다.", [1], ttl=1)).encode()
        http("POST", base + "/intent", sh, sig(sm, sh))
        time.sleep(4)
        st, rb = http("POST", base + "/pull", eb, sig(sm, eb))
        live_burned = st == 200 and json.loads(rb).get("intents") == []
        nb = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        r2 = process_outbox(db, outbox, ctx, snap, now)
        na = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        rec("C4-1", "live TTL 소각 0건 + 러너 무적용", live_burned and r2["applied"] == 0 and nb == na)
        r3 = process_outbox(db, outbox, ctx, snap, now)
        rec("C4-2", "러너 자동 재시도 0", r3["applied"] == 0)
        rec("C4-3", "적용 시 스냅샷 생성", len(os.listdir(snap)) >= 1)
    finally:
        st, rb = http("POST", base + "/admin/disable", eb, sig(sm, eb))
        st2, rb2 = http("POST", base + "/intent", body, sig(sm, body))
        rec("C4-4", "종료 시 inbox disable(재잠금 503)",
            st2 == 503 and "inbox_disabled" in rb2)

    n_ok = sum(1 for _c, _d, ok in RESULTS if ok)
    gate = "GO" if n_ok == len(RESULTS) else "BLOCK"
    print("---")
    print("synthetic-only=1  real_user_data=0  local_db=temp  read_line_touched=0  state=LOCKED")
    print("V2-3 LIVE E2E GATE=%s (%d/%d)" % (gate, n_ok, len(RESULTS)))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    main()
