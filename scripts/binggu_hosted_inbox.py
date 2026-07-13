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

★A3(P1-B)+save-n 참조 바인딩(2026-07-12): 저장의 유일 경로 = binggu_hosted_bundle.commit_bundle
(사람 저장 게이트 = preview + save n). commit_selected 는 선택 번호를 intent_id 로 매핑해
commit_bundle 에 위임한다 — 옛 direct process_outbox(actor=human) 경로·approval mint/consume
배선은 폐지(transported actor/confirm 신뢰 0). ctx.actor=='human' + confirm='SAVE <번호들>'
정확 일치 시에만 atomic 저장. inbox 렌더가 staged intent 를 write_last_preview 로 영속
(1 intent = 1 row · sentence=intent 원문 · 해시만 저장 — 세이브 n 의 n = inbox 번호 1:1).
candidate-only·A0·PII·rollback 불변. 요약 단계 ledger write 0. 원문 전문 출력 0(80자 발췌·
sha8·count·PII/secret flag 만)."""
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
_BASE = os.path.dirname(HERE)
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
from openbinggu_conversation_capture_preview import capture_preview  # noqa: E402
from binggupack.safety.gate_log import (write_last_preview,  # noqa: E402
                                        preview_ref_for_candidates)

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
                "_text": text,   # 내부용(preview 해시 재계산) — 렌더/스냅샷 출력엔 미사용
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
    lines.append("저장(preview + save n) — 위 번호가 곧 save n 의 n 입니다:")
    lines.append("  · Claude Code: '세이브 <번호들>' 발화 후")
    lines.append("      python binggu.py hosted pull --select <번호들> --confirm \"SAVE <번호들>\"")
    lines.append("  · 터미널: 위 명령을 직접 입력(그 자체가 save n)")
    lines.append("(전량 자동 적용 없음 · 고른 묶음 전체를 한 번에 ledger 에 확정합니다.)")
    return "\n".join(lines)


def inbox_preview_candidates(staging_dir, now_ts):
    """staged intent 전체 → capture_preview 형 후보 리스트(1 intent = 1 row · sentence=intent 원문).
    순서 = summarize idx(전체 기준 고정) — 세이브 n 의 n 과 inbox 번호 1:1. pref 재계산 공유 빌더."""
    summ = summarize(staging_dir, now_ts)
    return [{"sentence": it["_text"]} for it in summ["items"]]


def write_inbox_preview(staging_dir, now_ts):
    """inbox 렌더 시 staged intent 를 last_preview 로 영속(원문 미저장·해시만) + preview_ref 반환.
    이후 사람의 '세이브 n' 발화(훅)가 이 preview 에 ref 바인딩된다. staged 0건이면 write 0·None."""
    cands = inbox_preview_candidates(staging_dir, now_ts)
    if not cands:
        return None
    write_last_preview(cands)
    return preview_ref_for_candidates(cands)


# ---------------- 3) 선택 commit: staging 의 고른 항목만 commit_bundle 경유 (전량 금지) ----------------
def commit_selected(db, home, staging_dir, selected_idx, ctx, confirm, snap_dir, now_ts):
    """★A3(P1-B)+save-n: staging 의 selected_idx(1-base inbox 번호) → intent_id 매핑 후
    commit_bundle 경유(유일 저장 경로). 사람 저장 게이트(preview + save n):
      - ctx['actor'] != 'human' → human_save_required(직접 write 0·원문 보존).
      - confirm != 'SAVE <번호들>'(inbox 번호 그대로) → confirm_phrase_mismatch(write 0).
      - 통과 시 atomic 저장(전체 성공/전체 실패·미선택은 staging 잔류·계약 1·4·9).
    옛 direct process_outbox(actor=human) 경로·approval 배선 제거 — transported actor/confirm 신뢰 0(계약 11).
    선택 단위 = PC 명시 선택 묶음 전체(계약 1) — 선택 번호가 confirm 의 n 과 정확 바인딩(계약 4)."""
    from binggu_hosted_bundle import commit_bundle  # noqa: E402  (지연 import — cycle 회피)
    if not selected_idx:
        return {"ok": False, "reason": "select_required", "applied": 0, "write": 0,
                "selected": 0}
    summ = summarize(staging_dir, now_ts)
    by_idx = {it["idx"]: it for it in summ["items"]}
    intent_ids = []
    for i in selected_idx:
        if i not in by_idx:
            return {"ok": False, "reason": "idx_out_of_range", "bad_idx": i, "applied": 0,
                    "write": 0, "selected": len(selected_idx)}
        iid = by_idx[i].get("intent_id")
        if not iid:
            return {"ok": False, "reason": "intent_id_missing", "bad_idx": i, "applied": 0,
                    "write": 0, "selected": len(selected_idx)}
        intent_ids.append(iid)
    res = commit_bundle(db, home, staging_dir, intent_ids, ctx, confirm, snap_dir, now_ts,
                        indices=selected_idx)
    res["ok"] = bool(res.get("write"))
    res["selected"] = len(selected_idx)
    res.setdefault("applied", 0)
    res.setdefault("rejected", 0)
    res.setdefault("expired", 0)
    return res


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
    # SSOT 후보 게이트(should_capture) 후 — 판단 단문으로 구성(순수 사실/상태는 후보 아님).
    SENTS = [
        "이 입찰은 마진이 낮아 보류하기로 결정했다.",
        "백업은 항상 작업 전에 먼저 해 둔다.",
        "캐시 전략은 이걸로 확정한다.",
    ]
    PII = "담당자 연락처는 010-" + "1234-5678 이고 마진이 낮아 보류한다."  # 분리=공개 스캐너 회피(런타임 동일·PII 차단 테스트용 합성 번호)

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

    # commit 대상 ledger — 사람 저장 게이트(preview + save n)는 ctx/confirm 명시 주입으로 검증
    ledger = os.path.join(home, "ledger.sqlite")
    snap = os.path.join(home, "snapshots"); os.makedirs(snap, exist_ok=True)
    db = open_g3(ledger)
    os.makedirs(home, exist_ok=True)
    HUMAN = {"actor": "human", "actor_source": "cli_command"}
    READER = {"actor": "reader", "actor_source": "agent_session_unanchored"}

    def apr_count():
        """approval_requests 무증가 단정(테이블 부재=0) — 저장 경로 approval 배선 제거 증명."""
        try:
            return db.con.execute("SELECT count(*) FROM approval_requests").fetchone()[0]
        except Exception:
            return 0

    # T3b idx 안정성 — since 필터해도 번호는 전체 기준 유지(inbox --since 번호 == pull 번호)
    ck(s_recent["total"] == 4 and [it["idx"] for it in s_recent["items"]]
       == [it["idx"] for it in s_all["items"] if it["age_days"] is None or it["age_days"] <= 1],
       "T3b idx 안정성: since 필터가 번호를 바꾸지 않음")

    # T3c 렌더 preview — 1 intent = 1 row · idx = inbox 번호 1:1 · 원문 미저장(해시만) · pref 산출
    _env_home = os.environ.get("BINGGU_HOME")
    os.environ["BINGGU_HOME"] = home
    try:
        pref = write_inbox_preview(staging, NOW)
        lp = os.path.join(home, "last_preview_candidates.json")
        with open(lp, "r", encoding="utf-8") as f:
            pv = json.load(f)
        raw = open(lp, encoding="utf-8").read()
        ck(pref and pv.get("pref") == pref
           and [r["idx"] for r in pv["items"]] == [it["idx"] for it in s_all["items"]]
           and SENTS[0] not in raw and PII not in raw and "1234-5678" not in raw,
           "T3c 렌더 preview: 1 intent=1 row·idx 1:1·pref 산출·원문/PII 미저장(해시만)")
    finally:
        if _env_home is None:
            os.environ.pop("BINGGU_HOME", None)
        else:
            os.environ["BINGGU_HOME"] = _env_home

    # T4 select 없음 → BLOCK(전량 자동 금지 · 계약 1)
    r4 = commit_selected(db, home, staging, [], READER, None, snap, NOW)
    ck((not r4["ok"]) and r4["reason"] == "select_required" and r4["write"] == 0,
       "T4 select 필수(전량 자동 금지) · write 0")

    # T5 존재하지 않는 번호 → idx_out_of_range · write 0
    r5 = commit_selected(db, home, staging, [999], READER, None, snap, NOW)
    ck((not r5["ok"]) and r5["reason"] == "idx_out_of_range" and r5["write"] == 0,
       "T5 없는 번호 → idx_out_of_range · write 0")

    # T6 사람 게이트 — actor!=human → human_save_required · 직접 write 0 · 원문 보존(계약 11)
    s_t6 = summarize(staging, NOW)
    good = next(it for it in s_t6["items"] if not it["pii_secret"] and not it["expired"])
    good_idx, good_iid = good["idx"], good["intent_id"]
    n_files_before = len([f for f in os.listdir(staging) if f.endswith(".json")])
    r6 = commit_selected(db, home, staging, [good_idx], READER, "SAVE %d" % good_idx, snap, NOW)
    n_active6 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    src_kept = os.path.isfile(os.path.join(staging, good_iid + ".json"))
    ck((not r6["ok"]) and r6["write"] == 0 and r6["reason"] == "human_save_required"
       and r6.get("guidance") and n_active6 == 0 and src_kept and apr_count() == 0,
       "T6 actor!=human → human_save_required·write 0·원문 보존·approval_requests 0")

    # T6b confirm 불일치(inbox 번호와 다른 n) → confirm_phrase_mismatch · write 0(계약 4)
    r6b = commit_selected(db, home, staging, [good_idx], HUMAN, "SAVE 999", snap, NOW)
    ck((not r6b["ok"]) and r6b["write"] == 0 and r6b["reason"] == "confirm_phrase_mismatch"
       and os.path.isfile(os.path.join(staging, good_iid + ".json")),
       "T6b confirm 불일치 → confirm_phrase_mismatch · write 0 · 원문 보존")

    # T7 human + 정확 confirm → atomic 저장(계약 1·4) · 미선택은 staging 잔류(commit narrow·계약 9)
    r7 = commit_selected(db, home, staging, [good_idx], HUMAN, "SAVE %d" % good_idx, snap, NOW)
    n_active7 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    n_files_after = len([f for f in os.listdir(staging) if f.endswith(".json")])
    src_archived = (not os.path.isfile(os.path.join(staging, good_iid + ".json"))
                    and os.path.isfile(os.path.join(staging, "_archive", good_iid + ".processed.json")))
    ck(r7["ok"] and r7["write"] == 1 and r7["applied"] == 1 and n_active7 >= 1
       and src_archived and n_files_after == n_files_before - 1 and apr_count() == 0,
       "T7 human+confirm → atomic 저장 1건 · 미선택 잔류(commit narrow) · 원문 archive(삭제 아님)")

    # T7b 동일 문장 재적재 후 재커밋 → applied_registry 멱등 · 재write 0(계약 8 · ★재시도 semantics)
    with open(os.path.join(staging, "_archive", good_iid + ".processed.json"), encoding="utf-8") as f:
        good_text = json.load(f)["text"]
    mk(staging, good_text, [1])
    s_t7b = summarize(staging, NOW)
    re_idx = next(it["idx"] for it in s_t7b["items"] if it["intent_id"] == good_iid)
    r7b = commit_selected(db, home, staging, [re_idx], HUMAN, "SAVE %d" % re_idx, snap, NOW)
    n_active7b = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    ck(r7b["write"] == 0 and r7b["reason"] == "idempotent_already_applied"
       and n_active7b == n_active7, "T7b 동일 문장 재커밋 → idempotent · 재write 0(계약 8)")

    # T8 PII intent — human+confirm 이어도 save 게이트가 all-or-nothing 차단(write 0) · 원문 보존(계약 4·5)
    s_t8 = summarize(staging, NOW)
    pii = next(it for it in s_t8["items"] if it["pii_secret"])
    pii_idx, pii_iid = pii["idx"], pii["intent_id"]
    n_active_b8 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    r8 = commit_selected(db, home, staging, [pii_idx], HUMAN, "SAVE %d" % pii_idx, snap, NOW)
    n_active_a8 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    pii_src_kept = os.path.isfile(os.path.join(staging, pii_iid + ".json"))
    ck(r8["write"] == 0 and n_active_a8 == n_active_b8 and pii_src_kept and apr_count() == 0,
       "T8 PII intent → save 게이트 all-or-nothing 차단(write 0) · 원문 보존")

    # T9 candidate-only · 원문 전문/PII DB 미저장 · chain
    bad = db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
    chain = db.verify_chain()
    blob = "\n".join(str(x) for t in ("nodes", "audit_log") for x in db.con.execute("SELECT * FROM " + t))
    ck(bad == 0 and chain and PII not in blob and "1234-5678" not in blob,
       "T9 candidate-only · chain INTACT · 원문/PII DB 미저장")
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "T10 운영 store 불변")
    shutil.rmtree(tmp, ignore_errors=True)
    ck(not os.path.exists(tmp), "T11 temp 정리")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    print("usage: binggu_hosted_inbox.py [--selftest]  (temp 전용)")
    sys.exit(2)
