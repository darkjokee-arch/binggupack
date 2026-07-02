#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu v1.0 — candidate_list 목록 뷰 (owner GO 2026-06-11: UX 1단계).

저장된 candidate를 사람이 보고 고르는 UX의 기반 — **read-only 전용**.
필터: --status all|pending|deprecated|resolved · --kind 문서|증거|개념|상태|판단.
표시: 도장(label_kind 결정론 재분류) · state · review 상태 · evidence 연결 · candidate 플래그.

불변: write 0 (조회 전후 store_checksum 동일을 모드 내 자체 검증) · raw 원문 출력 금지
(저장된 문장 전체, 표시 60자 cap) · 수정/기각/확정 실행 없음(다음 단계 분리).
모드:
  --selftest    temp SQLite — 저장→resolve→기각 시나리오 구성 후 필터 전건 검증
  --real-smoke  real staging read-only 1회 (private 설정 모듈 환경 한정 — lazy import)
"""
import hashlib
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import (  # noqa: E402
    open_g3, deprecate_item, resolve_review)
from openbinggu_conversation_candidate_save import save_selected  # noqa: E402
from openbinggu_label_kind_map import classify_label_kind, KIND_KO, EN2KO  # noqa: E402

STATUSES = ("all", "pending", "deprecated", "resolved")
DISPLAY_CAP = 60


def node_id8(node_id):
    """변경 confirm 문구에 동반할 단축 식별자 — 4cli 결론 4(ACTION <n> <hash8>) 정본."""
    return hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:8]


def stamp_from_node_type(node_type, sentence):
    """저장된 node_type(5종 EN 라벨) → 도장(한글). 저장값을 단일 진실로 EN2KO 역매핑.

    재분류(classify_label_kind) 폐기 — 저장 도장 ↔ 표시 도장 발산 구조 제거.
    legacy 호환: node_type 이 5종 EN 라벨이 아니면(옛 Claim/Document 등) 부득이 문장 재분류
    fallback(신규 저장은 전부 5종 EN 이므로 이 경로 미진입). 빈 ledger 전제."""
    if node_type in EN2KO:
        return EN2KO[node_type]
    kind_ko, _ = classify_label_kind(sentence or "")
    return kind_ko


def list_candidates(db, status="all", kind=None):
    """read-only 목록. 반환 {rows, markdown, non_candidate}. 도장은 저장된 node_type(5종 EN 라벨)을
    EN2KO 로 역매핑해 표시 — 저장값이 단일 진실(표시 때 재분류 폐기로 발산 구조 제거)."""
    rows = []
    non_candidate = 0
    for nid, ntype, sent, cand, promo, state in db.con.execute(
            "SELECT node_id,node_type,sentence,candidate,promotion_allowed,state FROM nodes ORDER BY node_id"):
        rev = db.con.execute(
            "SELECT status,outcome FROM judgment_reviews WHERE node_id=? "
            "ORDER BY review_id DESC LIMIT 1", (nid,)).fetchone()
        review = "-" if not rev else (rev[0] if rev[0] == "pending" else "resolved:%s" % rev[1])
        ev = [r[0] for r in db.con.execute(
            "SELECT source FROM edges WHERE relation='evidence_supports' AND target=?", (nid,))]
        kind_ko = stamp_from_node_type(ntype, sent)
        if cand != 1 or promo != 0:
            non_candidate += 1
        if status == "pending" and review != "pending":
            continue
        if status == "resolved" and not review.startswith("resolved"):
            continue
        if status == "deprecated" and state != "deprecated":
            continue
        if kind and kind_ko != kind:
            continue
        rows.append({"node_id": nid, "id8": node_id8(nid), "kind": kind_ko, "state": state,
                     "review": review, "sentence": (sent or "")[:DISPLAY_CAP], "evidence": ev,
                     "candidate_only": cand == 1 and promo == 0})

    lines = ["# candidate 목록 — %d건 (status=%s kind=%s · read-only · 실행 버튼 없음)"
             % (len(rows), status, kind or "전체"),
             "", "| # | id | 도장 | state | review | 문장(발췌) | evidence | 출구 |",
             "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        # 출구 라벨: candidate=1(staging 미확정) / candidate=0(sealed 공개대상). 저장값 무변경(표시 문자열만).
        exit_label = "미확정(staging)" if r["candidate_only"] else "확정(sealed)"
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
            i, r["id8"], r["kind"], r["state"], r["review"], r["sentence"],
            ",".join(e[:14] for e in r["evidence"]) or "-",
            exit_label))
    lines.append("")
    lines.append("조회 전용입니다 — 아무것도 변경되지 않았습니다. 표시는 저장된 문장 전체의 60자 cap, 원문 전문(대화 전체)은 저장되어 있지 않습니다.")
    lines.append("변경 작업의 confirm 문구에는 # 와 id 를 함께 적습니다 (예: DEPRECATE 3 %s)." % (rows[2]["id8"] if len(rows) >= 3 else "a1b2c3d4"))
    return {"rows": rows, "markdown": "\n".join(lines), "non_candidate": non_candidate}


# ---------------- selftest (temp) ----------------

T1 = ("이 입찰은 마진이 낮아 보류한다. 백필 작업이 진행 중이다. "
      "낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다.")
T2 = "릴리스 전에는 빌드와 테스트를 모두 통과해야 한다."


def main_selftest():
    print("=" * 78)
    print("candidate_list 목록 뷰 — temp selftest (read-only 검증·운영/real 접근 0)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_candlist_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-44s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_g3(os.path.join(tmp, "s.sqlite"))
    # 시나리오: 저장 3건(판단/상태/개념, 판단 due) → 판단 resolve(성공) → 상태 기각 → 추가 판단 1건 pending
    r1 = save_selected(db, T1, [1, 2, 3], {"actor": "human", "confirm": "SAVE 1,2,3"},
                       snap_dir, due_date="2026-06-20")
    j1 = db.con.execute("SELECT node_id FROM judgment_reviews ORDER BY review_id LIMIT 1").fetchone()[0]
    resolve_review(db, j1, "성공", "목록 뷰 검증 시나리오", {"actor": "human"})
    s2 = [r[0] for r in db.con.execute("SELECT node_id,sentence FROM nodes") if "진행 중" in r[1]][0]
    deprecate_item(db, "node", s2, "목록 뷰 검증 — 기각 표본", {"actor": "human"}, snap_dir)
    r2 = save_selected(db, T2, [1], {"actor": "human", "confirm": "SAVE 1"}, snap_dir,
                       due_date="2026-06-30")
    ck("0_시나리오_구성", r1["saved"] == 3 and r2["saved"] == 1)

    cs1 = db.store_checksum()
    all_v = list_candidates(db)
    pend = list_candidates(db, status="pending")
    reso = list_candidates(db, status="resolved")
    depr = list_candidates(db, status="deprecated")
    kindf = list_candidates(db, kind="개념")
    cs2 = db.store_checksum()

    ck("1_전체_4건", len(all_v["rows"]) == 4)
    ck("2_pending_필터", len(pend["rows"]) == 1 and pend["rows"][0]["review"] == "pending")
    ck("3_resolved_필터", len(reso["rows"]) == 1 and reso["rows"][0]["review"] == "resolved:성공")
    ck("4_deprecated_필터", len(depr["rows"]) == 1 and depr["rows"][0]["state"] == "deprecated")
    ck("5_kind_필터(개념)", len(kindf["rows"]) == 1 and kindf["rows"][0]["kind"] == "개념")
    ck("6_read_only(checksum_동일)", cs1 == cs2)
    # raw 가드 = 다문장 원문 전문이 결합 재현되지 않음 + 표시 cap. 단문(T2)은 발췌=원문이 설계 의도.
    ck("7_raw_원문_미출력(전문_비재현)", T1 not in all_v["markdown"]
       and all(len(r["sentence"]) <= DISPLAY_CAP for r in all_v["rows"]))
    ck("8_candidate_only_전건+표시", all_v["non_candidate"] == 0
       and all(r["candidate_only"] for r in all_v["rows"]) and "read-only" in all_v["markdown"])
    ck("9_evidence_표시", all(r["evidence"] for r in all_v["rows"]
                           if r["node_id"].startswith("node:CONV:")))
    ck("10_실행버튼_없음(변경API_미노출)", "실행 버튼 없음" in all_v["markdown"])

    print("\n--- 표시 예 (status=all) ---")
    print(all_v["markdown"])
    print("---")
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("11_운영_store_불변", op_before == op_after)
    shutil.rmtree(tmp, ignore_errors=True)
    ck("12_temp_정리", not os.path.exists(tmp))

    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  write=0 confirmed=0 opencrab=0 deploy=0" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


def main_real_smoke():
    from openbinggu_real_staging_apply_once import REAL_STAGING_DB, _wal_checkpoint  # noqa: E402  private
    print("=" * 78)
    print("candidate_list — real staging read-only smoke (write 0 증명)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    db_mtime_before = os.path.getmtime(REAL_STAGING_DB)
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-44s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_g3(REAL_STAGING_DB)
    _wal_checkpoint(db.con)
    cs1 = db.store_checksum()
    views = {s: list_candidates(db, status=s) for s in STATUSES}
    kind_counts = {k: len(list_candidates(db, kind=k)["rows"]) for k in KIND_KO}
    cs2 = db.store_checksum()
    ck("1_read_only(checksum_동일)", cs1 == cs2, "checksum=%s" % cs1)
    ck("2_전체_조회", len(views["all"]["rows"]) > 0,
       "all=%d pending=%d resolved=%d deprecated=%d kind=%s" % (
           len(views["all"]["rows"]), len(views["pending"]["rows"]),
           len(views["resolved"]["rows"]), len(views["deprecated"]["rows"]), kind_counts))
    ck("3_candidate_only_전건", views["all"]["non_candidate"] == 0)
    ck("4_chain_INTACT", db.verify_chain())
    print("\n--- real staging 목록 (status=all) ---")
    print(views["all"]["markdown"])
    print("---")
    db.close()
    ck("5_DB_파일_mtime_불변", os.path.getmtime(REAL_STAGING_DB) == db_mtime_before)
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("6_운영_store_불변", op_before == op_after)

    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  write=0 confirmed=0 opencrab=0 deploy=0" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        sys.exit(main_selftest())
    if args == ["--real-smoke"]:
        sys.exit(main_real_smoke())
    print("usage: openbinggu_candidate_list_view.py [--selftest | --real-smoke]")
    sys.exit(2)
