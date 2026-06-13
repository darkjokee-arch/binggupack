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


def make_live_inject(base, token, text, indices, save_confirm):
    """save_mcp MCP 경로(/mcp2/<token>)로 save_intent tools/call — synthetic 적재. Origin 헤더 없음(폰 방식)."""
    def f():
        ep = base + "/mcp2/" + token
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "save_intent",
                              "arguments": {"text": text, "indices": indices, "confirm": save_confirm}}}
        body = json.dumps(payload).encode("utf-8")
        h = {"User-Agent": UA, "Content-Type": "application/json"}  # Origin 없음
        req = urllib.request.Request(ep, data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        result = data.get("result", {}) or {}
        return {"isError": bool(result.get("isError")), "id_h8": _h8(text + "|" + ",".join(map(str, indices)) + "|" + save_confirm)}
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
def run(*, ledger_path, outbox_dir, snap_dir, pull_fn, admin_fn, now,
        real=False, confirm=None, inject_fn=None):
    """enable → (inject) → pull → process_outbox → finally disable. 단일 try/finally 로 disable 보장.
    real=True 일 때만 백업 + confirm 게이트."""
    if real and confirm != REAL_CONFIRM:
        return {"ok": False, "reason": "confirm_required", "ledger_write": 0, "enabled": False,
                "disabled": False, "disable_err": None, "backup": False, "real": True, "injected": None}
    backup = _backup_ledger(ledger_path) if real else None
    enabled = False
    disabled = False
    disable_err = None
    err = None
    pull_count = 0
    res = None
    injected = None
    try:
        admin_fn(True)                      # enable — try 안: 실패해도 finally 로 정리
        enabled = True
        if inject_fn is not None:
            injected = inject_fn()          # synthetic save_intent 적재
        pull_count = pull_fn(outbox_dir)
        db = open_g3(ledger_path)
        try:
            res = process_outbox(db, outbox_dir, {"actor": "human"}, snap_dir, now)
        finally:
            db.close()
    except Exception as e:
        err = type(e).__name__
    finally:
        # disable 시도 — enable 성공 여부와 무관하게 일관 실행.
        # enable 이 401/예외로 실패한 경우 inbox 는 미개방이므로 disable 실패는 비치명(보고만).
        try:
            admin_fn(False)
            disabled = True
        except Exception as de:
            disable_err = type(de).__name__
    return {"ok": err is None, "err": err, "reason": None, "enabled": enabled, "disabled": disabled,
            "disable_err": disable_err, "injected": injected, "pull_count": pull_count,
            "applied": (res or {}).get("applied"), "rejected": (res or {}).get("rejected"),
            "backup": bool(backup), "real": real}


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

    # ---- inject 흐름 (enable→inject→pull→process→finally disable) · mock 만(real network/ledger 0) ----
    def _inj_ok():
        return {"isError": False, "id_h8": "synthh8x"}

    def _inj_raise():
        raise RuntimeError("inject_fail")

    # TI1 정상 순서
    tmp, ob, snap, ledger, calls, admin, pf = fresh(pull="ok")
    r = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin,
            now=NOW, inject_fn=_inj_ok)
    ck(r["ok"] and r["injected"] and r["applied"] == 1 and calls == [True, False],
       "TI1 enable→inject→pull→process→disable 정상 순서")
    ck(set((r["injected"] or {}).keys()) <= {"isError", "id_h8"},
       "TI6 inject 반환에 text/secret 미포함(id_h8/isError 만)")
    shutil.rmtree(tmp, ignore_errors=True)

    # TI2 inject 실패 → disable 보장 + write 0
    tmp, ob, snap, ledger, calls, admin, pf = fresh(pull="ok")
    r = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin,
            now=NOW, inject_fn=_inj_raise)
    ck((not r["ok"]) and calls == [True, False] and (r["applied"] in (None, 0)),
       "TI2 inject 실패에도 disable 보장 + write 0")
    shutil.rmtree(tmp, ignore_errors=True)

    # TI3 inject 후 pull 실패 → disable 보장
    tmp, ob, snap, ledger, calls, admin, pf = fresh(pull="raise")
    r = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin,
            now=NOW, inject_fn=_inj_ok)
    ck((not r["ok"]) and calls == [True, False], "TI3 inject 후 pull 실패에도 disable 보장")
    shutil.rmtree(tmp, ignore_errors=True)

    # TI4 process reject(malformed) → write 0 + disable
    tmp, ob, snap, ledger, calls, admin, pf = fresh(pull="bad")
    r = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin,
            now=NOW, inject_fn=_inj_ok)
    db = open_g3(ledger); n = db.con.execute("select count(*) from nodes").fetchone()[0]; db.close()
    ck(r["applied"] == 0 and n == 0 and calls == [True, False],
       "TI4 process reject → write 0 + disable 보장")
    shutil.rmtree(tmp, ignore_errors=True)

    # ---- enable 실패(401 등) → finally 경로로 정리 보고 ----
    # TF1 enable 예외 → finally disable 시도(정상) · enabled=False · disabled=True
    tf = []
    def admin_enable_fail(en):
        tf.append(en)
        if en:
            raise RuntimeError("enable_401")

    tmp, ob, snap, ledger, _calls, _admin, pf = fresh(pull="ok")
    r = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin_enable_fail,
            now=NOW, inject_fn=_inj_ok)
    ck((not r["ok"]) and (not r["enabled"]) and r["disabled"] and tf == [True, False]
       and r["applied"] in (None, 0),
       "TF1 enable 예외 → finally disable 시도(정리 보고) · write 0")
    shutil.rmtree(tmp, ignore_errors=True)

    # TF2 enable·disable 둘 다 예외 → 비치명 반환(crash 0) · disable_err 보고 · disabled=False
    tf2 = []
    def admin_both_fail(en):
        tf2.append(en)
        raise RuntimeError("admin_fail")

    tmp, ob, snap, ledger, _calls, _admin, pf = fresh(pull="ok")
    r = run(ledger_path=ledger, outbox_dir=ob, snap_dir=snap, pull_fn=pf, admin_fn=admin_both_fail,
            now=NOW, inject_fn=_inj_ok)
    ck(isinstance(r, dict) and (not r["ok"]) and r["disable_err"] and (not r["disabled"])
       and tf2 == [True, False],
       "TF2 enable·disable 모두 예외 → 비치명 보고(disable_err)")
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
    ap.add_argument("--inject-synthetic", dest="inject_synthetic", action="store_true",
                    help="enable 후 동일 흐름 내에서 synthetic save_intent 1건 적재(save_mcp)")
    ap.add_argument("--synthetic-text", dest="synthetic_text", default=None)
    ap.add_argument("--indices", default="1", help="쉼표 구분 1-base 인덱스")
    ap.add_argument("--save-confirm", dest="save_confirm", default=None,
                    help="'SAVE ' + indices 정확 일치")
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
    inject_fn = None
    if a.inject_synthetic:
        idxs = [int(x) for x in str(a.indices).split(",") if x.strip()]
        inject_fn = make_live_inject(base, token, a.synthetic_text, idxs, a.save_confirm)
    res = run(ledger_path=a.real_ledger, outbox_dir=outbox, snap_dir=snap,
              pull_fn=pull_fn, admin_fn=admin_fn, now=int(time.time()),
              real=True, confirm=a.confirm, inject_fn=inject_fn)
    shutil.rmtree(outbox, ignore_errors=True)
    # secret/URL/원문 미출력 — count/flag/reason 만
    print(json.dumps({k: res.get(k) for k in
                      ("ok", "reason", "enabled", "disabled", "pull_count", "applied", "rejected", "backup")},
                     ensure_ascii=False))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
