#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""openbinggu_save_intent_live_runner.py — 라이브 worker HMAC pull → 로컬 outbox → process_outbox.

기본은 **dry-run/temp**. 실 장부 write 는 반드시 명시 옵션이 있어야만 한다:
    --real-ledger <ledger.sqlite> --confirm "LIVE SAVE REHEARSAL"
그 외에는 실 ledger 에 일절 접근하지 않는다(--selftest 는 temp 전용).

안전 불변 (전부 --selftest 로 증명):
  - --real-ledger 없으면 실 ledger 접근 0 / backup 0 / enable 0
  - 실 모드는 confirm 정확 일치 없으면 enable·pull·write 0
  - 실행 전 ledger 백업, finally 로 inbox disable 보장(중간 실패에도)
  - 게이트는 process_outbox(=save_selected) 그대로 위임 — candidate-only, promotion_allowed=0,
    confirmed/active 자동 전이 0, A0·PII·confirm·rollback 불변
  - secret/token/URL/원문 전문 출력 0 — hash8/len/count/reason_code 만
  - live admin/pull 은 owner 별도 GO 하에서만(본 모듈은 호출 수단 제공, --selftest 는 mock)

CLI:
  python openbinggu_save_intent_live_runner.py --selftest
  python openbinggu_save_intent_live_runner.py            # dry-run 안내(실행 0)
  python openbinggu_save_intent_live_runner.py --real-ledger <p> --confirm "LIVE SAVE REHEARSAL" [--variant save_mcp|save_v2]
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from openbinggu_save_intent_outbox_runner import process_outbox, _mk_intent  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402
from binggupack_sign_util import signed_headers  # noqa: E402

UA = "BingguPack-live-rehearsal/1.0"            # CF 1010 회피용 custom UA
REAL_CONFIRM = "LIVE SAVE REHEARSAL"


def _h8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _backup_ledger(ledger_path):
    if not os.path.exists(ledger_path):
        return None
    bak = ledger_path + ".bak_rehearsal_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(ledger_path, bak)
    return bak


# ---------------- live 호출 수단 (owner 별도 GO 하에서만 사용) ----------------
def make_live_admin(base, token, sk):
    def f(enable):
        path = "/save2/%s/admin/%s" % (token, "enable" if enable else "disable")
        body = b"{}"
        h = signed_headers(sk, body, path)
        h.update({"User-Agent": UA, "Content-Type": "application/json"})
        req = urllib.request.Request(base + path, data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    return f


def make_live_pull(base, token, sk):
    def f(outbox_dir):
        path = "/save2/%s/pull" % token
        body = b"{}"
        h = signed_headers(sk, body, path)
        h.update({"User-Agent": UA, "Content-Type": "application/json"})
        req = urllib.request.Request(base + path, data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        intents = data.get("intents") or data.get("items") or []
        for it in intents:
            iid = it.get("intent_id") or ("intent_" + _h8(json.dumps(it, sort_keys=True)))
            with open(os.path.join(outbox_dir, iid + ".json"), "w", encoding="utf-8") as fp:
                json.dump(it, fp, ensure_ascii=False)
        return len(intents)
    return f


def _load_save_env(wp, variant):
    f = os.path.join(wp, ".dev.vars." + variant)
    d = {}
    with open(f, encoding="utf-8") as fp:
        for line in fp:
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    return d["WORKER_URL"], d["SAVE_PATH_TOKEN"], d["SAVE_SIGN_SECRET"]


# ---------------- core run (게이트·백업·finally disable) ----------------
def run(*, ledger_path, outbox_dir, snap_dir, pull_fn, admin_fn, now, real=False, confirm=None):
    """enable → pull → process_outbox → finally disable. real=True 일 때만 백업 + confirm 게이트."""
    if real and confirm != REAL_CONFIRM:
        return {"ok": False, "reason": "confirm_required", "ledger_write": 0,
                "enabled": False, "disabled": False, "backup": False, "real": True}
    backup = _backup_ledger(ledger_path) if real else None
    enabled = False
    disabled = False
    err = None
    pull_count = 0
    res = None
    admin_fn(True)
    enabled = True
    try:
        pull_count = pull_fn(outbox_dir)
        db = open_g3(ledger_path)
        try:
            res = process_outbox(db, outbox_dir, {"actor": "human"}, snap_dir, now)
        finally:
            db.close()
    except Exception as e:
        err = type(e).__name__
    finally:
        try:
            admin_fn(False)
        finally:
            disabled = True
    return {"ok": err is None, "err": err, "reason": None, "enabled": enabled, "disabled": disabled,
            "pull_count": pull_count, "applied": (res or {}).get("applied"),
            "rejected": (res or {}).get("rejected"), "backup": bool(backup), "real": real}


# ---------------- 셀프테스트 (temp 전용 · 라이브/실 ledger 미접촉 · mock) ----------------
def _selftest():
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    SYN = "빙구팩 실저장 리허설용 합성 판단 문장이다. 자동 저장은 금지된다."
    NOW = 1_900_000_000

    def fresh(pull="ok"):
        tmp = tempfile.mkdtemp(prefix="obg_live_")
        ob = os.path.join(tmp, "outbox"); os.makedirs(ob)
        snap = os.path.join(tmp, "snap"); os.makedirs(snap)
        ledger = os.path.join(tmp, "ledger.sqlite")
        calls = []

        def admin(en):
            calls.append(en)

        def pull_ok(o):
            _mk_intent(o, SYN, [1], confirm="SAVE 1", created_ts=NOW); return 1

        def pull_bad(o):
            _mk_intent(o, SYN, [1], confirm="SAVE 1", schema_ver=9, created_ts=NOW); return 1

        def pull_raise(o):
            raise RuntimeError("pull_fail")

        pf = {"ok": pull_ok, "bad": pull_bad, "raise": pull_raise}[pull]
        return tmp, ob, snap, ledger, calls, admin, pf

    # T1 기본 temp 정상 1건
    tmp, ob, snap, ledger, calls, admin, pf = fresh()
    r1 = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin, now=NOW)
    ck(r1["ok"] and r1["applied"] == 1 and not r1["real"], "T1 기본 temp → applied 1")
    db = open_g3(ledger)
    bad = db.con.execute("select count(*) from nodes where candidate!=1 or promotion_allowed!=0").fetchone()[0]
    chain = db.verify_chain()
    blob = "\n".join(str(x) for t in ("nodes", "audit_log") for x in db.con.execute("select * from " + t))
    db.close()
    ck(bad == 0, "T5/T6 candidate-only + promotion_allowed=0 (confirmed/active 자동전이 0)")
    ck(SYN not in blob, "T7/T10 원문 전문 DB 미저장(발췌만)")
    ck(chain, "T8 audit chain INTACT")
    ck(calls == [True, False], "T4a enable→disable 정상 순서")
    ck(not r1["backup"], "T2 real 아님 → backup/실ledger 접근 0")
    shutil.rmtree(tmp, ignore_errors=True)

    # T3 malformed(schema_ver=9) → ledger write 0
    tmp, ob, snap, ledger, calls, admin, pf = fresh(pull="bad")
    r3 = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin, now=NOW)
    db = open_g3(ledger); n = db.con.execute("select count(*) from nodes").fetchone()[0]; db.close()
    ck(r3["applied"] == 0 and n == 0, "T3 malformed pull → applied 0, ledger write 0")
    shutil.rmtree(tmp, ignore_errors=True)

    # T4 pull 예외 → disable 보장
    tmp, ob, snap, ledger, calls, admin, pf = fresh(pull="raise")
    r4 = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin, now=NOW)
    ck((not r4["ok"]) and calls == [True, False] and r4["disabled"], "T4 pull 예외에도 disable 보장")
    shutil.rmtree(tmp, ignore_errors=True)

    # T9 real 모드 게이트 + 백업/rollback
    tmp, ob, snap, ledger, calls, admin, pf = fresh(pull="ok")
    open_g3(ledger).close()  # 백업 대상 파일 생성
    rW = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin,
             now=NOW, real=True, confirm="WRONG")
    ck(rW["reason"] == "confirm_required" and not rW["enabled"] and calls == [],
       "T9a real + confirm 불일치 → enable/pull/write 0")
    rO = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin,
             now=NOW + 1, real=True, confirm=REAL_CONFIRM)
    ck(rO["backup"] and rO["applied"] == 1 and calls == [True, False],
       "T9b real + confirm 일치 → backup + 저장 + disable")
    bak_exists = any(x.startswith("ledger.sqlite.bak_rehearsal_") for x in os.listdir(tmp))
    ck(bak_exists, "T9c rollback 백업 파일 생성")
    shutil.rmtree(tmp, ignore_errors=True)

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="save_intent_live_runner")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--real-ledger", dest="real_ledger", default=None)
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--variant", choices=["save_mcp", "save_v2"], default="save_mcp")
    ap.add_argument("--workers-port", dest="wp", default=None)
    a = ap.parse_args()

    if a.selftest:
        sys.exit(_selftest())

    if not a.real_ledger:
        print("dry-run 안내: 기본 실행은 temp 전용입니다.")
        print("  검증:      python openbinggu_save_intent_live_runner.py --selftest")
        print('  실 저장:   --real-ledger <ledger.sqlite> --confirm "%s" (owner 별도 GO)' % REAL_CONFIRM)
        sys.exit(0)

    # ---- 실 장부 저장 (owner 별도 GO 하에서만 도달) ----
    wp = a.wp or os.path.abspath(os.path.join(HERE, "..", "workers_port"))
    base, token, sk = _load_save_env(wp, a.variant)
    outbox = tempfile.mkdtemp(prefix="obg_live_pull_")
    snap = os.path.join(os.path.dirname(os.path.abspath(a.real_ledger)), "snapshots")
    os.makedirs(snap, exist_ok=True)
    admin_fn = make_live_admin(base, token, sk)
    pull_fn = make_live_pull(base, token, sk)
    res = run(ledger_path=a.real_ledger, outbox_dir=outbox, snap_dir=snap,
              pull_fn=pull_fn, admin_fn=admin_fn, now=int(time.time()),
              real=True, confirm=a.confirm)
    shutil.rmtree(outbox, ignore_errors=True)
    # secret/URL/원문 미출력 — count/flag/reason 만
    print(json.dumps({k: res.get(k) for k in
                      ("ok", "reason", "enabled", "disabled", "pull_count", "applied", "rejected", "backup")},
                     ensure_ascii=False))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
