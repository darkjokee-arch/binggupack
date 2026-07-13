"""빙구팩 캡처 → 저장 게이트 연결 어댑터.

설계: BINGGUPACK_USER_ONTOLOGY_EVENT_SCHEMA_DESIGN.md §4 + 저장 승인 정책
- 자동 캡처 엔진(binggu_capture_*)이 고른 후보 preview를, 기존 저장 게이트
  (preview_id + --pick + --confirm "SAVE n")로 넘기는 다리.
- 두 진입점:
    build_save_commands(preview)      → 사람이 직접 실행할 `binggu.py save` 명령 문자열만 생성(저장 0)
    commit_selected(db, text, ...)    → 기존 save_selected 게이트에 위임(저장 실행, 게이트 통과 시만)
- ★ 자동저장 구조적 불가는 commit_selected 에서도 유지된다:
    · 어댑터는 confirm 문구를 **생성하지 않고** 인자로 받은 값을 그대로 전달한다.
    · save_selected 의 actor 게이트(auto/reader → G4_no_auto BLOCK) + confirm 정확일치 게이트가
      방아쇠를 사람에게 고정한다. 어댑터는 게이트를 새로 만들거나 우회하지 않는다.
    · preview_id 게이트(text 의 sha256[:8] 일치)로 "사람이 preview 를 본 텍스트"만 저장 가능.
- write 대상 = save_selected 가 받는 staging ledger(temp 또는 owner 운영본). OpenCrab 0 / confirmed 0.
"""

import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_conversation_candidate_save import save_selected  # noqa: E402
try:
    import binggu_platform as _plat  # noqa: E402
except Exception:  # pragma: no cover — 폴백
    _plat = None


def _invocation_prefix():
    try:
        return _plat.invocation_prefix() if _plat else "python binggu.py"
    except Exception:
        return "python binggu.py"


def _preview_id(text):
    # binggu.py._preview_id 와 동일(sha256[:8]) — 저장 시 preview_required 일치 보장
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def build_save_commands(preview, ledger=None):
    """capture preview(buffer.render_preview 결과) → 후보별 저장 명령 안내.
    저장 실행 0 — 명령 문자열만. 사용자가 실행해야 기존 게이트로 저장됨."""
    ledger_opt = f' --ledger "{ledger}"' if ledger else ""
    prefix = _invocation_prefix()
    rows = []
    for it in preview.get("items", []):
        text = it["text"]
        pid = _preview_id(text)
        # 발화 1건 = 후보. 기존 capture_preview 재실행 시 보통 1번 후보 → --pick 1
        cmd = (f'{prefix} save "{text}" --preview-id {pid} '
               f'--pick 1 --confirm "SAVE 1"{ledger_opt}')
        rows.append({
            "idx": it["idx"],
            "pinned": it.get("pinned", False),
            "confidence": it.get("confidence", "normal"),
            "preview_id": pid,
            "save_command": cmd,
            "note": "사용자가 직접 실행해야 저장 (자동저장 불가)",
        })
    return {"count": len(rows), "commands": rows,
            "guard": "어댑터는 게이트를 만들지 않음. preview_id+--pick+--confirm 사람 게이트로만 저장."}


def commit_selected(db, text, preview_id, picks, confirm, snap_dir,
                    due=None, actor="human"):
    """capture 후보(발화 text)를 실제 장부에 저장 — 기존 save_selected 게이트에 위임.

    자동저장 구조적 불가 유지(아래 셋 다 save_selected/여기 게이트로 강제):
      1. preview 게이트   : preview_id 가 text 의 sha256[:8] 과 일치해야 진행
      2. actor 게이트     : actor in (auto,reader) → save_selected 가 G4_no_auto BLOCK
      3. confirm 게이트   : confirm 이 "SAVE <picks>" 와 정확 일치해야 통과
                            (★ confirm 은 인자로 받은 값을 그대로 전달 — 어댑터 생성 0)
    반환: save_selected 결과({applied, saved, skipped_existing, rejected, reason, ...})
          또는 preview_id 불일치 시 {applied:False, reason:"preview_required_mismatch"}.
    """
    if preview_id != _preview_id(text):
        return {"applied": False, "saved": 0, "skipped_existing": 0,
                "rejected": {}, "reason": "preview_required_mismatch"}
    return save_selected(db, text, list(picks),
                         {"actor": actor, "confirm": confirm}, snap_dir, due_date=due)


# ---------------- 셀프테스트 (temp ledger 전용, 운영 store 미접촉) ----------------
def _selftest():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from binggu_capture_buffer import CaptureBuffer
    from openbinggu_deprecate_and_remind_g3 import open_g3
    from openbinggu_staging_write_selftest import OPERATING_PATHS

    ok = True

    def check(c, m):
        nonlocal ok
        ok = ok and c
        print(f"  [{'PASS' if c else 'FAIL'}] {m}")

    # ---- A. 명령 생성기(저장 0) ----
    buf = CaptureBuffer()
    buf.feed("이거 저장해")            # pinned
    buf.feed("로컬 정본으로 가자")      # normal
    buf.feed("ㅋㅋ")                    # ignored
    pv = buf.render_preview()
    out = build_save_commands(pv)
    check(out["count"] == 2, "T1 명령 생성 2개(ignored 제외)")
    check(out["commands"][0]["pinned"], "T2 pinned 우선(맨 위)")
    c0 = out["commands"][0]
    check(c0["preview_id"] == _preview_id("이거 저장해"), "T3 preview_id 정확(게이트 통과 보장)")
    check('--confirm "SAVE 1"' in c0["save_command"] and "--pick 1" in c0["save_command"],
          "T4 명령에 --pick + --confirm 사람 게이트 포함")

    # ---- B. commit_selected 게이트 (temp ledger, 운영 store 불변) ----
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_c2s_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    # 종결 완결 문장 = capture_preview 후보 1번이 되도록(어댑터 흐름과 동일)
    TEXT = "이 입찰은 마진이 낮아 보류한다."
    PID = _preview_id(TEXT)

    db = open_g3(os.path.join(tmp, "ledger.sqlite"))

    # T5 confirm 일치 → 저장됨 (방아쇠=사람이 넘긴 confirm)
    r_ok = commit_selected(db, TEXT, PID, [1], "SAVE 1", snap_dir)
    check(r_ok["applied"] and r_ok["saved"] == 1, "T5 confirm 일치 → 저장(applied·saved=1)")

    # T6 confirm 불일치 → BLOCK (어댑터가 confirm 못 만든다는 증명)
    TEXT2 = "백필 작업이 지금 진행 중이다."
    r_bad = commit_selected(db, TEXT2, _preview_id(TEXT2), [1], "SAVE 9", snap_dir)
    check((not r_bad["applied"]) and r_bad["reason"] == "confirm_phrase_mismatch",
          "T6 confirm 불일치 → BLOCK(confirm_phrase_mismatch)")

    # T7 confirm 누락(빈 문자열) → BLOCK = '자동저장 차단' 핵심
    r_none = commit_selected(db, TEXT2, _preview_id(TEXT2), [1], "", snap_dir)
    check((not r_none["applied"]) and r_none["reason"] == "confirm_phrase_mismatch",
          "T7 confirm 누락 → BLOCK (confirm 없으면 저장 0)")

    # T8 actor=auto → BLOCK = 자동 호출 구조적 차단
    r_auto = commit_selected(db, TEXT2, _preview_id(TEXT2), [1], "SAVE 1", snap_dir, actor="auto")
    check((not r_auto["applied"]) and r_auto["reason"] == "G4_no_auto",
          "T8 actor=auto → BLOCK (자동저장 구조적 불가)")

    # T9 preview 안 본 텍스트(preview_id 불일치) → BLOCK
    r_pv = commit_selected(db, TEXT2, "deadbeef", [1], "SAVE 1", snap_dir)
    check((not r_pv["applied"]) and r_pv["reason"] == "preview_required_mismatch",
          "T9 preview 미확인 → BLOCK (preview_required_mismatch)")

    # T10 위 BLOCK 4건 동안 추가 저장 0 (T5의 1건만 존재)
    n_nodes = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    check(n_nodes == 1, "T10 BLOCK 4건 동안 저장 0 (노드=T5의 1건만)")

    # T11 confirmed 0 · promotion 0 · audit chain INTACT · 원문 전문 미저장
    bad = db.con.execute(
        "SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
    chain = db.verify_chain()
    blob = "\n".join(str(r) for t in ("nodes", "edges", "evidence", "audit_log")
                     for r in db.con.execute("SELECT * FROM " + t))
    check(bad == 0 and chain and (TEXT + TEXT2) not in blob,
          "T11 candidate-only·promotion0·chain INTACT·원문전문 미저장")
    db.close()

    # T12 운영 store(~/.binggupack 등) mtime 불변
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    check(op_before == op_after, "T12 운영 store 불변(temp 전용)")

    shutil.rmtree(tmp, ignore_errors=True)
    check(not os.path.exists(tmp), "T13 temp 정리")

    print(f"\nGATE={'GO' if ok else 'NO-GO'}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if _selftest() else 1)
