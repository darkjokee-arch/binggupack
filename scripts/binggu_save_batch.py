# -*- coding: utf-8 -*-
"""binggu_save_batch.py — 세션 마무리 candidate 번호 배치 저장 (발화별 반복 UX 제거).

문제(2026-07-18 owner 지적): 세션 마무리에 여러 발화를 저장하려면 발화마다
pair preview→SAVE→confirm 를 반복해야 해서(N번) 실사용자가 귀찮아서 포기한다.

해결: owner 가 세션 마무리 preview 를 보고 'SAVE 6,11,13'(candidate 번호)를 **한 번**
발화하면, 지정한 candidate 를 각각 화자축 pair 로 배치 저장한다.

승인 경계 (헌법 §C-11·G4_no_auto 불변 — 자동저장 0):
  - owner 의 candidate 번호 SAVE 발화가 유일 앵커. save hook(gate_record_from_prompt)이
    candidate 앵커(write_last_preview)와 대조해 save_gate_log 에 (pref, idxs)를 기록하고,
    배치는 gate_human_for_ref 로 그 앵커를 검증해야만 human 승격(없으면 전체 skip).
  - 각 candidate 는 **기존 save_paired 로 저장**(owner 원문 그대로·문장 분할·pair 엣지·중복검사
    전부 재사용·무변경). confirm 은 각 문장 owner_pick 에 맞춰 배치가 생성 — owner 의 candidate
    SAVE 는 그 candidate '전체 저장' 승인이므로 배치의 문장별 confirm 은 승인 범위 안(위조 아님).
  - save_paired 는 ctx.actor=='human' 이면 게이트 재검증을 건너뛴다(gate_log.py 승격 정본을
    배치가 candidate 앵커로 한 번 대신 수행 — 문장축 앵커 불필요).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _anchor_candidates(buffer_items):
    """capture buffer items([{idx,text,...}]) → write_last_preview 용 candidates([{sentence}]).
    idx 순서 보존(preview 와 배치가 동일 pref 재계산 보장 — 순서 바뀌면 앵커 불일치)."""
    return [{"sentence": it.get("text", "")} for it in (buffer_items or [])]


def stage_batch_anchor(buffer_items, path=None):
    """세션 마무리 candidate 목록을 번호축 앵커(last_preview_candidates.json)로 기록.
    owner 의 'SAVE n' 발화가 이 앵커와 대조돼 human 승격된다(저장 0 · hash 만). 반환 rows 수."""
    from binggupack.safety.gate_log import write_last_preview
    return write_last_preview(_anchor_candidates(buffer_items), path=path, explicit=True)


def render_batch_preview(buffer_items):
    """배치 저장 preview(결정적 마크다운 · 저장 0). 각 candidate 번호 + 원문 발췌 표."""
    lines = ["# 세션 마무리 배치 저장 preview (candidate · 저장 0 · 사람이 SAVE)",
             "| # | owner 발화(원문) |", "|---|---|"]
    for it in (buffer_items or []):
        txt = (it.get("text", "") or "").replace("\n", " ")
        if len(txt) > 90:
            txt = txt[:90] + "…"
        lines.append("| %s | %s |" % (it.get("idx"), txt))
    lines.append("")
    lines.append('저장: 이 채팅에 `SAVE 6,11,13`(원하는 번호) 한 번 → 지정 candidate 전체를 저장.')
    return "\n".join(lines)


def save_candidates_batch(db, snap_dir, buffer_items, indices, gate_log_path=None):
    """indices 의 candidate 를 각각 화자축 pair 로 배치 저장. owner SAVE 앵커로 human 승격.

    반환 {applied, reason, saved, skipped, results[]} — results 각 항목은
    {cand, pick, applied, reason, pack_id}. 앵커 미검증 시 saved=0·reason='no_save_gate_ref'."""
    from binggupack.safety.gate_log import preview_ref_for_candidates, gate_human_for_ref, gate_path
    from binggupack.storage import save_paired
    from openbinggu_conversation_capture_preview import capture_preview

    indices = [int(i) for i in (indices or [])]
    if not indices:
        return {"applied": False, "reason": "no_indices", "saved": 0, "skipped": 0, "results": []}

    pref = preview_ref_for_candidates(_anchor_candidates(buffer_items))
    gp = gate_log_path or gate_path()
    # owner 의 candidate 번호 SAVE 발화가 앵커로 기록됐는지 — all-or-nothing human 승격(fail-closed).
    if not gate_human_for_ref(pref, indices, path=gp):
        return {"applied": False, "reason": "no_save_gate_ref", "saved": 0, "skipped": 0, "results": []}

    by_idx = {it.get("idx"): it.get("text", "") for it in buffer_items}
    human_base = {"actor": "human", "actor_promoted_by": "batch_save_gate_ref"}
    results = []
    total_saved = 0
    skipped = 0
    for idx in indices:
        text = by_idx.get(idx)
        if not text:
            results.append({"cand": idx, "pick": None, "applied": False,
                            "reason": "candidate_missing", "pack_id": None})
            skipped += 1
            continue
        sents = capture_preview(text, explicit=True).get("candidates", [])
        for pick in range(1, len(sents) + 1):
            ctx = dict(human_base)
            ctx["confirm"] = "PAIR owner:%d" % pick   # candidate 전체 저장 승인 범위 내
            r = save_paired(db, text, None, ctx, snap_dir, owner_pick=pick)
            if r.get("applied"):
                total_saved += 1
            results.append({"cand": idx, "pick": pick, "applied": bool(r.get("applied")),
                            "reason": r.get("reason"), "pack_id": r.get("pack_id")})
    return {"applied": total_saved > 0, "reason": None, "saved": total_saved,
            "skipped": skipped, "results": results}
