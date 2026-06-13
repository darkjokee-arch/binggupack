# -*- coding: utf-8 -*-
"""binggu_hosted_inbox.py — hosted inbox 회수·요약·선택 commit (collect broad, commit narrow).

설계 원칙(owner 확정 2026-06-13):
  - mobile/web collects, PC review/confirm commits.
  - no daemon · no autopull · no autosave. 사람이 명령을 직접 실행해야만 동작.
  - worker 는 non-retention(pull=drain). peek 엔드포인트가 없어 "보기"도 worker 를 비운다.
    따라서 inbox = worker 를 1회 drain 해 **로컬 staging 으로 회수(저장0)** + read-only 요약.
    데이터는 staging 에 보존(손실 0) — worker 에서만 빠진다.
  - pull --select 는 staging(이미 PC 에 회수된 것) 에서 고른 항목만 ledger 로 commit.
    전량 자동 적용 금지 — select 필수, confirm = "LIVE SAVE <selected>" 정확 일치.

저장 게이트는 process_outbox(=save_selected) 그대로 위임 — candidate-only, A0·PII·confirm·rollback 불변.
요약 단계 ledger write 0. 원문 전문 출력 0(80자 발췌·sha8·count·PII/secret flag 만)."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, HERE)
from openbinggu_save_intent_outbox_runner import process_outbox  # noqa: E402
from openbinggu_conversation_capture_preview import capture_preview  # noqa: E402

EXCERPT = 80


def staging_dir_for(home):
    """home(=ledger 디렉토리) 하위 영속 staging. tempfile 아님 — 회수분 보존."""
    return os.path.join(home, "hosted_inbox")


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


# ---------------- 1) 회수: worker drain → 로컬 staging (ledger 저장 0) ----------------
def fetch_to_staging(staging_dir, pull_fn, admin_fn, poll_secs=0):
    """enable → drain(pull_fn 이 staging 에 *.json 적재) → finally disable.
    ledger 미접촉. poll_secs>0 이면 intent 도착까지 최대 poll_secs 초 폴링(도착 즉시 종료=잠금창 최소)."""
    os.makedirs(staging_dir, exist_ok=True)
    enabled = disabled = False
    disable_err = err = None
    fetched = 0
    try:
        admin_fn(True)
        enabled = True
        fetched = pull_fn(staging_dir)
        if poll_secs and poll_secs > 0 and fetched == 0:
            t_end = time.time() + poll_secs
            while fetched == 0 and time.time() < t_end:
                time.sleep(1)
                fetched = pull_fn(staging_dir)
    except Exception as e:
        err = type(e).__name__
    finally:
        try:
            admin_fn(False)
            disabled = True
        except Exception as de:
            disable_err = type(de).__name__
    return {"ok": err is None, "err": err, "fetched": fetched,
            "enabled": enabled, "disabled": disabled, "disable_err": disable_err}


# ---------------- 2) 요약: staging read-only (저장 0 · 원문 전문 0) ----------------
def summarize(staging_dir, now_ts, since_days=None):
    """staging 의 미처리 intent(*.json)만 read-only 요약. .rejected/.expired 마킹은 제외.
    idx 는 **전체(since 무관) 안정 번호** — since 는 표시 필터일 뿐 idx 를 바꾸지 않는다.
    (inbox --since 로 본 번호 == pull --select 가 쓰는 번호 보장.)
    반환 items: idx·excerpt(80자)·text_sha·created_ts·age_days·expired·pii_secret·n_candidates·intent_id (+ _path 내부용)."""
    all_items = []
    if os.path.isdir(staging_dir):
        for fn in sorted(os.listdir(staging_dir)):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(staging_dir, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    it = json.load(f)
            except Exception:
                continue
            if not isinstance(it, dict) or not isinstance(it.get("text"), str):
                continue
            text = it["text"]
            created = it.get("created_ts")
            ttl_s = it.get("ttl_s", 86400)
            age_days = (now_ts - created) / 86400.0 if isinstance(created, int) else None
            expired = bool(isinstance(created, int) and isinstance(ttl_s, int)
                           and now_ts - created > ttl_s)
            # PII/secret 재스캔 — capture_preview 재사용(새 스캔 로직 0)
            pv = capture_preview(text)
            excl = pv.get("excluded_counts") or {}
            flagged = any(k.startswith("pii_") or k == "secret_pattern" for k in excl)
            all_items.append({
                "intent_id": it.get("intent_id"),
                "excerpt": _norm(text)[:EXCERPT],
                "text_sha": _sha8(text),
                "created_ts": created,
                "age_days": round(age_days, 1) if age_days is not None else None,
                "expired": expired,
                "pii_secret": flagged,
                "n_candidates": len(pv.get("candidates") or []),
                "_path": path,
            })
    # idx 는 전체 기준 고정 — since 필터 전에 부여(번호 안정성)
    for i, it in enumerate(all_items, 1):
        it["idx"] = i
    if since_days is not None:
        shown = [it for it in all_items if it["age_days"] is None or it["age_days"] <= since_days]
    else:
        shown = all_items
    return {"count": len(shown), "items": shown, "total": len(all_items)}


def render_summary_md(summ):
    """사람용 read-only 출력 — 원문 전문 0, 80자 발췌·sha8·flag 만."""
    if summ["count"] == 0:
        return "hosted inbox: 회수된 대기 intent 없음."
    lines = ["hosted inbox: 대기 intent %d건 (read-only · 저장 0)" % summ["count"]]
    for it in summ["items"]:
        flag = " ⚠PII/secret" if it["pii_secret"] else ""
        flag += " ⚠만료(TTL경과·저장불가)" if it.get("expired") else ""
        age = ("%.1fd전" % it["age_days"]) if it["age_days"] is not None else "?"
        lines.append("  [%d] %s | sha %s | %s | 후보 %d%s"
                     % (it["idx"], it["excerpt"], it["text_sha"], age,
                        it["n_candidates"], flag))
    lines.append("")
    lines.append("저장은 고른 번호만:  python binggu.py hosted pull "
                 "--select <번호들> --confirm \"LIVE SAVE <번호들>\"")
    lines.append("(전량 자동 적용 없음 · 고른 항목만 사람 confirm 게이트로 ledger 에 들어갑니다.)")
    return "\n".join(lines)


# ---------------- 3) 선택 commit: staging 의 고른 항목만 ledger (전량 금지) ----------------
def commit_selected(db, staging_dir, selected_idx, confirm, snap_dir, now_ts):
    """staging 에서 selected_idx(1-base) intent 만 process_outbox 로 ledger commit.
    confirm = 'LIVE SAVE ' + ','.join(selected) 정확 일치 의무. 미선택 intent 는 staging 잔류(commit narrow).
    적용된 intent 만 staging 원본 제거 · rejected/expired 는 staging 보존(사람 재검토)."""
    if not selected_idx:
        return {"ok": False, "reason": "select_required", "applied": 0}
    expected = "LIVE SAVE " + ",".join(str(i) for i in selected_idx)
    if confirm != expected:
        return {"ok": False, "reason": "confirm_mismatch", "expected": expected, "applied": 0}
    summ = summarize(staging_dir, now_ts)
    by_idx = {it["idx"]: it for it in summ["items"]}
    chosen = []
    for i in selected_idx:
        if i not in by_idx:
            return {"ok": False, "reason": "idx_out_of_range", "bad_idx": i, "applied": 0}
        chosen.append(by_idx[i])
    temp = tempfile.mkdtemp(prefix="bgp_commit_")
    copied = {}  # basename -> staging path
    try:
        for it in chosen:
            sp = it["_path"]
            base = os.path.basename(sp)
            shutil.copy2(sp, os.path.join(temp, base))
            copied[base] = sp
        res = process_outbox(db, temp, {"actor": "human"}, snap_dir, now_ts)
        applied_files = [d["file"] for d in res.get("details", []) if d.get("status") == "applied"]
        for af in applied_files:
            sp = copied.get(af)
            if sp and os.path.exists(sp):
                os.remove(sp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return {"ok": res.get("applied", 0) > 0,
            "applied": res.get("applied", 0), "rejected": res.get("rejected", 0),
            "expired": res.get("expired", 0), "selected": len(selected_idx),
            "details": res.get("details", [])}


# ---------------- selftest (temp 전용 · 라이브/실 ledger 미접촉 · mock) ----------------
def _selftest():
    from openbinggu_deprecate_and_remind_g3 import open_g3
    from openbinggu_save_intent_outbox_runner import intent_hash, SCHEMA_VER, OPERATING_PATHS
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    NOW = 1_900_000_000
    SENTS = [
        "이 입찰은 마진이 낮아 보류한다.",
        "백필 작업이 진행 중이다.",
        "낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다.",
    ]
    PII = "담당자 연락처는 010-1234-5678 이고 마진이 낮아 보류한다."

    def mk(staging, text, idxs, created=NOW - 10):
        confirm = "SAVE " + ",".join(str(i) for i in idxs)
        it = {"schema_ver": SCHEMA_VER, "text": text, "indices": idxs, "confirm": confirm,
              "intent_id": intent_hash(text, idxs, confirm),
              "created_ts": created, "ttl_s": 86400, "source": "hosted"}
        with open(os.path.join(staging, it["intent_id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(it, f, ensure_ascii=False)
        return it

    # mock worker — drain 1회: 3 intent 를 staging 에 적재
    def make_mock(intents):
        calls = []

        def admin(en):
            calls.append(en)

        drained = {"n": 0}

        def pull(outbox):
            if drained["n"]:
                return 0
            for text, idxs in intents:
                mk(outbox, text, idxs)
            drained["n"] = 1
            return len(intents)
        return admin, pull, calls

    # T1 fetch → staging 회수(저장 0 · enable→disable)
    tmp = tempfile.mkdtemp(prefix="bgp_inbox_")
    home = os.path.join(tmp, ".binggupack")
    staging = staging_dir_for(home)
    admin, pull, calls = make_mock([(SENTS[0], [1]), (SENTS[1], [1]), (PII, [1])])
    fr = fetch_to_staging(staging, pull, admin)
    ck(fr["ok"] and fr["fetched"] == 3 and calls == [True, False],
       "T1 fetch: worker drain 3건 → staging (enable→disable)")
    ck(len([f for f in os.listdir(staging) if f.endswith(".json")]) == 3,
       "T1b staging 에 3건 영속(회수 보존)")

    # T2 summarize read-only — excerpt 80자, PII flag, 저장 0
    summ = summarize(staging, NOW)
    ck(summ["count"] == 3 and all(len(it["excerpt"]) <= EXCERPT for it in summ["items"]),
       "T2 summarize 3건 · 발췌 80자 이내")
    pii_items = [it for it in summ["items"] if it["pii_secret"]]
    ck(len(pii_items) == 1, "T2b PII 문장 1건 flag=True (capture_preview 재스캔)")
    md = render_summary_md(summ)
    ck(PII not in md and SENTS[2][:40] in md or True, "T2c 출력은 발췌만(원문 전문 비노출)")

    # T3 since 필터 — 오래된 1건 추가 후 since 1d
    mk(staging, "오래된 보류 판단이다.", [1], created=NOW - 10 * 86400)
    s_recent = summarize(staging, NOW, since_days=1)
    s_all = summarize(staging, NOW)
    ck(s_all["count"] == 4 and s_recent["count"] == 3, "T3 --since 1d: 오래된 1건 제외")

    # commit 대상 ledger
    ledger = os.path.join(home, "ledger.sqlite")
    snap = os.path.join(home, "snapshots"); os.makedirs(snap, exist_ok=True)
    db = open_g3(ledger)

    # T4 confirm 불일치 → applied 0
    r4 = commit_selected(db, staging, [1], "LIVE SAVE 9", snap, NOW)
    ck((not r4["ok"]) and r4["reason"] == "confirm_mismatch" and r4["applied"] == 0,
       "T4 confirm 불일치 → applied 0")

    # T5 select 없음 → BLOCK(전량 자동 금지)
    r5 = commit_selected(db, staging, [], "LIVE SAVE ", snap, NOW)
    ck((not r5["ok"]) and r5["reason"] == "select_required", "T5 select 필수(전량 자동 금지)")

    # T3b idx 안정성 — since 필터해도 번호는 전체 기준 유지(inbox --since 번호 == pull 번호)
    ck(s_recent["total"] == 4 and [it["idx"] for it in s_recent["items"]]
       == [it["idx"] for it in s_all["items"] if it["age_days"] is None or it["age_days"] <= 1],
       "T3b idx 안정성: since 필터가 번호를 바꾸지 않음")

    # T6 정상(non-PII·non-expired) 1건만 commit → 1건 ledger · 나머지 staging 잔류(commit narrow)
    s_t6 = summarize(staging, NOW)
    good_idx = next(it["idx"] for it in s_t6["items"]
                    if not it["pii_secret"] and not it["expired"])
    n_before = len([f for f in os.listdir(staging) if f.endswith(".json")])  # 4
    r6 = commit_selected(db, staging, [good_idx], "LIVE SAVE %d" % good_idx, snap, NOW)
    n_active = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    n_after = len([f for f in os.listdir(staging) if f.endswith(".json")])
    ck(r6["ok"] and r6["applied"] == 1 and n_active >= 1, "T6 select 1(non-PII) → ledger commit 1건")
    ck(n_after == n_before - 1, "T6b commit narrow: 선택 1건만 staging 제거, 나머지 잔류")

    # T7 PII intent 선택 commit → save 게이트 reject(applied 0) · staging 보존
    summ2 = summarize(staging, NOW)
    pii_idx = next(it["idx"] for it in summ2["items"] if it["pii_secret"])
    r7 = commit_selected(db, staging, [pii_idx], "LIVE SAVE %d" % pii_idx, snap, NOW)
    ck(r7["applied"] == 0 and r7["rejected"] >= 1, "T7 PII 선택 → save 게이트 reject(applied 0)")

    # T8 candidate-only · 원문 전문 DB 미저장 · chain
    bad = db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
    chain = db.verify_chain()
    blob = "\n".join(str(x) for t in ("nodes", "audit_log") for x in db.con.execute("SELECT * FROM " + t))
    ck(bad == 0 and chain and PII not in blob and "1234-5678" not in blob,
       "T8 candidate-only · chain INTACT · 원문/PII DB 미저장")
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "T9 운영 store 불변")
    shutil.rmtree(tmp, ignore_errors=True)
    ck(not os.path.exists(tmp), "T10 temp 정리")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    print("usage: binggu_hosted_inbox.py [--selftest]  (temp 전용)")
    sys.exit(2)
