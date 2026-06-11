#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu — BingguPack 개인 장부 CLI (v1.0 사용자 진입점).

설치 후 "내 영속 장부"를 만들고 후보 관리 전 과정을 실행한다.
새 게이트 로직 0 — 기존 검증 모듈(save/list/deprecate/replace/accept/resolve)을 그대로 호출.
모든 변경은 confirm 문구를 사용자가 직접 타이핑해야 통과한다(자동 0·confirmed 0·raw 원문 0).

  python binggu.py init                            내 장부 생성 (~/.binggupack/ledger.sqlite)
  python binggu.py status                          장부 요약
  python binggu.py preview "<대화/메모 텍스트>"      저장 후보 미리보기 (저장 0)
  python binggu.py save "<텍스트>" --pick 1,3 --confirm "SAVE 1,3" [--due 2026-07-01]
  python binggu.py list [--status pending|deprecated|resolved] [--kind 판단|상태|개념|문서|증거]
  python binggu.py deprecate <n> <id8> --reason "..." --confirm "DEPRECATE <n> <id8>"
  python binggu.py replace <n> <id8> --with "<수정문장>" --reason "..." \
                   --confirm "REPLACE <n> <id8> WITH <수정문장>"
  python binggu.py accept <n> <id8> --reason "..." --confirm "ACCEPT <n> <id8>"
  python binggu.py unaccept <n> <id8> --reason "..." --confirm "UNACCEPT <n> <id8>"
  python binggu.py due <n> <id8> --date 2026-07-01      판단 검증 예정일 등록
  python binggu.py resolve <n> <id8> --outcome 성공|실패|불확실|판정불가 --reason "..."
  python binggu.py reminders [--today YYYY-MM-DD]       due 경과 판단 목록
  python binggu.py --selftest                            temp 장부 풀 사이클 자가검증

장부 위치 변경: --ledger <sqlite 경로> (기본 ~/.binggupack/ledger.sqlite)
"""
import argparse
import datetime
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import (  # noqa: E402
    set_review_due, resolve_review, list_due_reminders)
from openbinggu_candidate_list_view import list_candidates  # noqa: E402
from openbinggu_conversation_candidate_save import save_selected  # noqa: E402
from openbinggu_conversation_capture_preview import capture_preview  # noqa: E402
from openbinggu_candidate_deprecate_ux import deprecate_from_list  # noqa: E402
from openbinggu_candidate_replace_ux import replace_from_list  # noqa: E402
from openbinggu_owner_accept_ux import (  # noqa: E402
    open_accept, accept_from_list, unaccept_from_list, accepted_view)

DEFAULT_LEDGER = os.path.join(os.path.expanduser("~"), ".binggupack", "ledger.sqlite")
OUTCOMES = ("성공", "실패", "불확실", "판정불가")


def _ledger_paths(ledger):
    ledger = os.path.abspath(ledger)
    snap_dir = os.path.join(os.path.dirname(ledger), "snapshots")
    return ledger, snap_dir


def _open(ledger, must_exist=True):
    ledger, snap_dir = _ledger_paths(ledger)
    if must_exist and not os.path.exists(ledger):
        print("장부가 없습니다: %s" % ledger)
        print("먼저 만드세요:  python binggu.py init")
        sys.exit(2)
    os.makedirs(snap_dir, exist_ok=True)
    return open_accept(ledger), snap_dir


def _node_id_of(db, index, status="all", kind=None):
    rows = list_candidates(db, status, kind)["rows"]
    if index < 1 or index > len(rows):
        return None
    return rows[index - 1]["node_id"]


def _show(r):
    if r.get("applied"):
        print("OK:", {k: v for k, v in r.items() if k != "applied"} or "적용됨")
        return 0
    print("BLOCK:", r.get("reason"))
    return 1


def cmd_init(a):
    ledger, snap_dir = _ledger_paths(a.ledger)
    if os.path.exists(ledger):
        print("이미 장부가 있습니다: %s (그대로 사용하면 됩니다)" % ledger)
        return 0
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    os.makedirs(snap_dir, exist_ok=True)
    db = open_accept(ledger)
    db.close()
    print("장부 생성 완료: %s" % ledger)
    print("다음:  python binggu.py preview \"오늘 정리하고 싶은 문장들\"")
    return 0


def cmd_status(a):
    db, _ = _open(a.ledger)
    n = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    d = db.con.execute("SELECT count(*) FROM nodes WHERE state='deprecated'").fetchone()[0]
    p = db.con.execute("SELECT count(*) FROM judgment_reviews WHERE status='pending'").fetchone()[0]
    acc = len(accepted_view(db))
    chain = db.verify_chain()
    db.close()
    print("장부: %s" % os.path.abspath(a.ledger))
    print("active 후보 %d · 기각 %d · 검증 예정 %d · 수용 %d · audit chain %s"
          % (n, d, p, acc, "INTACT" if chain else "BROKEN!"))
    return 0


def cmd_preview(a):
    pv = capture_preview(a.text)
    print(pv["preview_markdown"])
    if pv["candidates"]:
        ex = ",".join(str(i) for i in range(1, len(pv["candidates"]) + 1))
        print("\n저장하려면:  python binggu.py save \"<같은 텍스트>\" --pick %s --confirm \"SAVE %s\"" % (ex, ex))
    return 0


def cmd_save(a):
    db, snap_dir = _open(a.ledger)
    idx = [int(x) for x in a.pick.split(",") if x.strip()]
    r = save_selected(db, a.text, idx, {"actor": "human", "confirm": a.confirm},
                      snap_dir, due_date=a.due)
    db.close()
    return _show(r)


def cmd_list(a):
    db, _ = _open(a.ledger)
    v = list_candidates(db, a.status or "all", a.kind)
    acc = accepted_view(db)
    db.close()
    print(v["markdown"])
    if acc:
        print("수용(owner_accepted) 중: %d건" % len(acc))
    return 0


def cmd_deprecate(a):
    db, snap_dir = _open(a.ledger)
    r = deprecate_from_list(db, a.n, a.id8, a.reason,
                            {"actor": "human", "confirm": a.confirm}, snap_dir)
    db.close()
    return _show(r)


def cmd_replace(a):
    db, snap_dir = _open(a.ledger)
    r = replace_from_list(db, a.n, a.id8, getattr(a, "with"), a.reason,
                          {"actor": "human", "confirm": a.confirm}, snap_dir)
    db.close()
    return _show(r)


def cmd_accept(a):
    db, _ = _open(a.ledger)
    r = accept_from_list(db, a.n, a.id8, a.reason, {"actor": "human", "confirm": a.confirm})
    db.close()
    return _show(r)


def cmd_unaccept(a):
    db, _ = _open(a.ledger)
    r = unaccept_from_list(db, a.n, a.id8, a.reason, {"actor": "human", "confirm": a.confirm})
    db.close()
    return _show(r)


def cmd_due(a):
    db, _ = _open(a.ledger)
    nid = _node_id_of(db, a.n)
    from openbinggu_candidate_list_view import node_id8
    if not nid or node_id8(nid) != a.id8:
        db.close()
        print("BLOCK: node_hash_mismatch (목록을 다시 확인하세요: binggu.py list)")
        return 1
    r = set_review_due(db, nid, a.date, {"actor": "human"})
    db.close()
    return _show(r)


def cmd_resolve(a):
    db, _ = _open(a.ledger)
    nid = _node_id_of(db, a.n)
    from openbinggu_candidate_list_view import node_id8
    if not nid or node_id8(nid) != a.id8:
        db.close()
        print("BLOCK: node_hash_mismatch (목록을 다시 확인하세요: binggu.py list)")
        return 1
    r = resolve_review(db, nid, a.outcome, a.reason, {"actor": "human"})
    db.close()
    return _show(r)


def cmd_reminders(a):
    db, _ = _open(a.ledger)
    today = a.today or datetime.date.today().isoformat()
    r = list_due_reminders(db, today)
    db.close()
    print(r["markdown"])
    return 0


def selftest():
    print("=" * 74)
    print("binggu CLI — temp 장부 풀 사이클 selftest (영속 장부·운영 store 접근 0)")
    print("=" * 74)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_cli_")
    ledger = os.path.join(tmp, "ledger.sqlite")
    checks = []

    def ck(name, ok):
        checks.append(ok)
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))

    class A:  # argparse 흉내 — CLI 함수를 그대로 검증
        pass

    def args(**kw):
        a = A()
        a.ledger = ledger
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    ck("1_init", cmd_init(args()) == 0 and os.path.exists(ledger))
    ck("1b_init_멱등", cmd_init(args()) == 0)
    TEXT = ("이 입찰은 마진이 낮아 보류한다. 백필 작업이 진행 중이다. "
            "낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다.")
    ck("2_preview(저장0)", cmd_preview(args(text=TEXT)) == 0)
    ck("3_save", cmd_save(args(text=TEXT, pick="1,2,3", confirm="SAVE 1,2,3",
                               due="2099-12-31")) == 0)
    db, _ = _open(ledger)
    rows = list_candidates(db)["rows"]
    db.close()
    ck("4_list_3건", len(rows) == 3 and cmd_list(args(status=None, kind=None)) == 0)
    i_state = next(i for i, r in enumerate(rows, 1) if r["kind"] == "상태")
    h_state = rows[i_state - 1]["id8"]
    ck("5_deprecate", cmd_deprecate(args(n=i_state, id8=h_state, reason="셀프테스트 기각",
                                         confirm="DEPRECATE %s %s" % (i_state, h_state))) == 0)
    db, _ = _open(ledger)
    rows2 = list_candidates(db)["rows"]
    db.close()
    i_j = next(i for i, r in enumerate(rows2, 1) if r["kind"] == "판단" and r["state"] == "active")
    h_j = rows2[i_j - 1]["id8"]
    NEW = "재검토 결과 이 입찰은 조건부로 진행한다."
    ck("6_replace", cmd_replace(args(n=i_j, id8=h_j, reason="셀프테스트 수정",
                                     confirm="REPLACE %s %s WITH %s" % (i_j, h_j, NEW),
                                     **{"with": NEW})) == 0)
    db, _ = _open(ledger)
    rows3 = list_candidates(db)["rows"]
    db.close()
    i_n = next(i for i, r in enumerate(rows3, 1) if NEW[:10] in r["sentence"])
    h_n = rows3[i_n - 1]["id8"]
    ck("7_accept", cmd_accept(args(n=i_n, id8=h_n, reason="유지",
                                   confirm="ACCEPT %s %s" % (i_n, h_n))) == 0)
    ck("7b_unaccept", cmd_unaccept(args(n=i_n, id8=h_n, reason="재검토",
                                        confirm="UNACCEPT %s %s" % (i_n, h_n))) == 0)
    ck("8_due+resolve", cmd_due(args(n=i_n, id8=h_n, date="2000-01-01")) == 0
       and cmd_reminders(args(today="2000-01-02")) == 0
       and cmd_resolve(args(n=i_n, id8=h_n, outcome="성공", reason="셀프테스트")) == 0)
    ck("9_잘못된_confirm_BLOCK", cmd_deprecate(args(n=1, id8="deadbeef", reason="x",
                                                   confirm="DEPRECATE 1 deadbeef")) == 1)
    db, _ = _open(ledger)
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0])
    chain = db.verify_chain()
    blob = "\n".join(str(r) for t in ("nodes", "audit_log")
                     for r in db.con.execute("SELECT * FROM " + t))
    db.close()
    ck("10_candidate-only+chain+raw0", bad == 0 and chain and TEXT not in blob)
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("11_운영_store_불변", op_before == op_after)
    shutil.rmtree(tmp, ignore_errors=True)
    ck("12_temp_정리", not os.path.exists(tmp))

    ok = all(checks)
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (sum(checks), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    p = argparse.ArgumentParser(prog="binggu", description="BingguPack 개인 장부 CLI")
    p.add_argument("--ledger", default=DEFAULT_LEDGER)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sp = sub.add_parser("preview"); sp.add_argument("text")
    sp = sub.add_parser("save"); sp.add_argument("text")
    sp.add_argument("--pick", required=True); sp.add_argument("--confirm", required=True)
    sp.add_argument("--due", default=None)
    sp = sub.add_parser("list"); sp.add_argument("--status", default=None)
    sp.add_argument("--kind", default=None)
    for name in ("deprecate", "accept", "unaccept"):
        sp = sub.add_parser(name); sp.add_argument("n", type=int); sp.add_argument("id8")
        sp.add_argument("--reason", required=True); sp.add_argument("--confirm", required=True)
    sp = sub.add_parser("replace"); sp.add_argument("n", type=int); sp.add_argument("id8")
    sp.add_argument("--with", required=True, dest="with")
    sp.add_argument("--reason", required=True); sp.add_argument("--confirm", required=True)
    sp = sub.add_parser("due"); sp.add_argument("n", type=int); sp.add_argument("id8")
    sp.add_argument("--date", required=True)
    sp = sub.add_parser("resolve"); sp.add_argument("n", type=int); sp.add_argument("id8")
    sp.add_argument("--outcome", required=True, choices=OUTCOMES)
    sp.add_argument("--reason", required=True)
    sp = sub.add_parser("reminders"); sp.add_argument("--today", default=None)
    a = p.parse_args()
    fn = {"init": cmd_init, "status": cmd_status, "preview": cmd_preview, "save": cmd_save,
          "list": cmd_list, "deprecate": cmd_deprecate, "replace": cmd_replace,
          "accept": cmd_accept, "unaccept": cmd_unaccept, "due": cmd_due,
          "resolve": cmd_resolve, "reminders": cmd_reminders}[a.cmd]
    sys.exit(fn(a))


if __name__ == "__main__":
    main()
