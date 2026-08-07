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


def stage_batch_anchor(buffer_items, path=None, session_id=None):
    """세션 마무리 candidate 목록을 번호축 앵커(last_preview_candidates.json)로 기록.
    owner 의 'SAVE n' 발화가 이 앵커와 대조돼 human 승격된다(저장 0 · hash 만). 반환 rows 수.
    session_id 지정 시 앵커에 심어 저장 경로(cmd_save_batch)가 동일 세션 목록을 재현하도록 한다
    (마무리 preview·앵커·저장 3자 idx/pref 통일 — 이원화 오저장 방지)."""
    from binggupack.safety.gate_log import write_last_preview
    return write_last_preview(_anchor_candidates(buffer_items), path=path,
                              explicit=True, session_id=session_id)


def render_batch_preview(buffer_items):
    """배치 저장 preview(결정적 마크다운 · 저장 0). 각 candidate 번호 + 원문 발췌 표."""
    lines = ["# 세션 마무리 배치 저장 preview (candidate · 저장 0 · 사람이 SAVE)",
             "| # | owner 발화(원문) |", "|---|---|"]
    for it in (buffer_items or []):
        raw = it.get("text", "") or ""
        txt = raw.replace("\n", " ")
        if len(txt) > 90:
            # 표시만 줄인다(저장은 전문) — 실제 길이를 같이 적어 표시↔저장 괴리를 없앤다.
            txt = txt[:90] + "… (전문 %d자)" % len(raw)
        lines.append("| %s | %s |" % (it.get("idx"), txt))
    lines.append("")
    # SAVE 시 실제로 몇 문장이 저장되는지 사전 고지 — 장문 차선이 붙으면 건수가 늘 수 있다.
    n_main, n_long = _batch_sentence_counts(buffer_items)
    if n_long:
        lines.append("SAVE 하면 문장 **%d건(주 %d · 긴 %d)** 이 저장됩니다."
                     % (n_main + n_long, n_main, n_long))
    lines.append('저장: 이 채팅에 `SAVE 6,11,13`(원하는 번호) 한 번 → 지정 candidate 전체를 저장.')
    return "\n".join(lines)


def _batch_sentence_counts(buffer_items):
    """preview 표의 각 candidate 가 SAVE 시 만들 문장 수 (주 목록 / 장문 차선).
    표시 전용 집계라 실패는 조용히 0 (preview 가 죽으면 owner 가 아무것도 못 본다)."""
    n_main = n_long = 0
    try:
        from openbinggu_conversation_capture_preview import capture_preview
        for it in (buffer_items or []):
            txt = it.get("text", "") or ""
            if not txt or it.get("ai_context"):
                continue          # 대화쌍 경로는 대표문 1 pair 고정 — 이 집계 대상 아님
            pv = capture_preview(txt, explicit=True)
            n_main += len(pv.get("candidates", []))
            n_long += sum(1 for x in pv.get("long_candidates", [])
                          if not x.get("blob_suspect"))
    except Exception:
        return n_main, n_long
    return n_main, n_long


def _origin_of(it):
    """buffer item → 앞막이 출처 dict. capture buffer 가 채운 좌표(src_id/src_sha)를 저장까지
    실어 나른다. 이 배선이 없으면 hook 이 모은 세션 좌표가 마지막 홉에서 버려지고
    locator 의 source_id 가 `utterance:<hash>` 자기해시로만 남는다(앞막이 무력화)."""
    if not isinstance(it, dict):
        return None
    o = {k: it.get(k) for k in ("src_id", "src_sha") if it.get(k)}
    return o or None


def save_candidates_batch(db, snap_dir, buffer_items, indices, gate_log_path=None):
    """indices 의 candidate 를 각각 화자축 pair 로 배치 저장. owner SAVE 앵커로 human 승격.

    반환 {applied, reason, saved, skipped, results[]} — results 각 항목은
    {cand, pick, applied, reason, pack_id}. 앵커 미검증 시 saved=0·reason='no_save_gate_ref'."""
    from binggupack.safety.gate_log import preview_ref_for_candidates, gate_human_for_ref, gate_path
    from binggupack.storage import save_paired
    from openbinggu_conversation_capture_preview import capture_preview
    from binggu_capture_persist import DEFAULT_PAIR_RELATION

    indices = [int(i) for i in (indices or [])]
    if not indices:
        return {"applied": False, "reason": "no_indices", "saved": 0, "skipped": 0, "results": []}

    pref = preview_ref_for_candidates(_anchor_candidates(buffer_items))
    gp = gate_log_path or gate_path()
    # owner 의 candidate 번호 SAVE 발화가 앵커로 기록됐는지 — all-or-nothing human 승격(fail-closed).
    if not gate_human_for_ref(pref, indices, path=gp):
        return {"applied": False, "reason": "no_save_gate_ref", "saved": 0, "skipped": 0, "results": []}

    by_idx = {it.get("idx"): it for it in buffer_items}
    human_base = {"actor": "human", "actor_promoted_by": "batch_save_gate_ref"}
    results = []
    total_saved = 0
    skipped = 0
    for idx in indices:
        it = by_idx.get(idx) or {}
        text = it.get("text", "")
        if not text:
            results.append({"cand": idx, "pick": None, "applied": False,
                            "reason": "candidate_missing", "pack_id": None})
            skipped += 1
            continue
        ai_ctx = it.get("ai_context")
        if ai_ctx:
            # B(대화쌍): owner 발화 ↔ 직전 AI말 pair(노드2+엣지1) 저장. relation=신호 제안값(owner
            #   SAVE 앵커가 승인 · §8-1 정체성 축). 대표문 1 pair — 같은 ai 노드가
            #   여러 pick 에 재등장하면 save_paired 가 pair_partial_exists 로 막으므로 분할 안 함.
            # ★owner_whole=True(2026-08-04): 종전 owner_pick=1 은 _SENT_SPLIT 오분리 시 첫 조각만
            #   저장해 owner 교정 원문의 나머지를 조용히 버렸다("사전 자체를 다" 류 절단 노드).
            #   owner 발화는 원문 그대로(§C-13) — 전문 1노드. 불가 시 save_paired 가 폴백+사유.
            relation = it.get("pair_relation") or DEFAULT_PAIR_RELATION
            ctx = dict(human_base)
            ctx["confirm"] = "PAIR %s owner:1 ai:1" % relation
            r = save_paired(db, text, ai_ctx, ctx, snap_dir,
                            relation_kind=relation, owner_pick=1, ai_pick=1,
                            owner_origin=_origin_of(it), owner_whole=True)
            if r.get("applied"):
                total_saved += 1
            results.append({"cand": idx, "pick": 1, "applied": bool(r.get("applied")),
                            "reason": r.get("reason"), "pack_id": r.get("pack_id"),
                            "paired": True, "relation": relation,
                            "owner_whole": r.get("owner_whole"),
                            "owner_whole_fallback": r.get("owner_whole_fallback")})
            continue
        pv = capture_preview(text, explicit=True)
        sents = pv.get("candidates", [])
        for pick in range(1, len(sents) + 1):
            ctx = dict(human_base)
            ctx["confirm"] = "PAIR owner:%d" % pick   # candidate 전체 저장 승인 범위 내
            r = save_paired(db, text, None, ctx, snap_dir, owner_pick=pick,
                            owner_origin=_origin_of(it))
            if r.get("applied"):
                total_saved += 1
            results.append({"cand": idx, "pick": pick, "applied": bool(r.get("applied")),
                            "reason": r.get("reason"), "pack_id": r.get("pack_id"),
                            "paired": False, "relation": None})
        # L-lane(2단계 절단): 주 목록 순회 뒤 장문 차선. 이 candidate 는 owner 가 이미 SAVE 로
        #   승인한 범위이므로 그 안의 장문도 같은 승인에 포함된다.
        #   단 **덩어리 의심(blob_suspect)은 자동 포함하지 않는다** — 로그·붙여넣기가 온톨로지에
        #   섞이는 통로가 되면 안 되므로 명시 `SAVE L1` 경로로만 들어온다(설계 J1).
        for lit in pv.get("long_candidates", []):
            if lit.get("blob_suspect"):
                results.append({"cand": idx, "pick": lit.get("label"), "applied": False,
                                "reason": "blob_suspect_needs_explicit", "pack_id": None,
                                "paired": False, "relation": None})
                skipped += 1
                continue
            ctx = dict(human_base)
            ctx["confirm"] = "PAIR owner:%s" % lit.get("label")
            r = save_paired(db, text, None, ctx, snap_dir, owner_pick=lit.get("label"),
                            owner_origin=_origin_of(it))
            if r.get("applied"):
                total_saved += 1
            results.append({"cand": idx, "pick": lit.get("label"),
                            "applied": bool(r.get("applied")), "reason": r.get("reason"),
                            "pack_id": r.get("pack_id"), "paired": False, "relation": None})
    reason = None
    if not total_saved and results:
        # B-08 ②(2026-08-07): 전건 실패 배치의 최상위 reason 을 개별 사유 집계로 채운다.
        # 종전엔 상시 None 이라 CLI 가 'BLOCK: None' 만 찍고 results[].reason 이 증발했다
        # (조용한 실패 — 2026-08-04 심야 전건 pair_partial_exists 배치에서 실측).
        cnt = {}
        for res in results:
            k = str(res.get("reason"))
            cnt[k] = cnt.get(k, 0) + 1
        reason = "all_failed(%s)" % ", ".join(
            "%s×%d" % (k, n) for k, n in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0])))
    return {"applied": total_saved > 0, "reason": reason, "saved": total_saved,
            "skipped": skipped, "results": results}


def transition_targets(results):
    """버퍼 상태 전이(saved_to_ledger) 대상 candidate 번호 집합 — 저장 성공 + '이미 원장에
    존재(pair_partial_exists)' 둘 다. all-fail 배치에서도 전이해야 기저장 발화가 다음 preview
    에 재등장하는 루프가 끊긴다(B-08 ① · 2026-08-07)."""
    return {res.get("cand") for res in (results or [])
            if res.get("applied") or res.get("reason") == "pair_partial_exists"}


def stale_ledger_ids(db, buffer_items):
    """이미 원장에 있어 SAVE 해도 새 노드가 0인 candidate 의 buffer_id 목록(read-only).

    왜(B-08 ① 2026-08-07): 세션 귀속 필터가 못 거르는 경로(구형 앵커=전체 목록·이전 세션
    잔존·배치 밖 경로로 저장된 발화)로 기저장 후보가 preview 에 재등장했다. 판정은 저장
    경로와 **같은 함수**(_whole_utterance_node/_pick_one_node → node id)로 계산해 패리티를
    보장한다. 보수 원칙: 게이트 에러코드 등 판정 불확실은 유지 — 잘못 제외해 저장 기회를
    잃는 쪽이 재등장 소음보다 큰 손실이다."""
    from openbinggu_conversation_candidate_save import _pick_one_node, _whole_utterance_node
    from openbinggu_conversation_capture_preview import capture_preview

    def _exists(nid):
        return bool(db.con.execute("SELECT 1 FROM nodes WHERE node_id=?", (nid,)).fetchone())

    out = []
    for it in (buffer_items or []):
        if not isinstance(it, dict) or it.get("buffer_id") is None:
            continue
        text = it.get("text") or ""
        if not text.strip():
            continue
        try:
            if it.get("ai_context"):
                # pair 경로: owner 노드는 전문(owner_whole) 우선 → 불가 시 pick1 폴백(저장과 동일).
                # owner 든 ai 든 한쪽이라도 기존재면 save_paired 가 pair_partial_exists 로 전건
                # 차단하므로 이 candidate 에서 새로 저장될 노드는 없다.
                own = _whole_utterance_node(text, "owner")
                if isinstance(own, str):
                    own = _pick_one_node(text, 1, "owner", explicit=True)
                if isinstance(own, str):
                    continue
                stale = _exists(own["id"])
                if not stale:
                    ain = _pick_one_node(it["ai_context"], 1, "ai", explicit=True)
                    stale = (not isinstance(ain, str)) and _exists(ain["id"])
            else:
                # 단독 경로: 주 목록 전 pick + L-lane(비 blob) 이 전부 기존재일 때만 stale —
                # 일부만 있으면 나머지가 새로 저장되므로 유지.
                pv = capture_preview(text, explicit=True)
                picks = list(range(1, len(pv.get("candidates") or []) + 1))
                picks += [x.get("label") for x in (pv.get("long_candidates") or [])
                          if not x.get("blob_suspect")]
                if not picks:
                    continue
                nodes = [_pick_one_node(text, p, "owner", explicit=True) for p in picks]
                if any(isinstance(n, str) for n in nodes):
                    continue
                stale = all(_exists(n["id"]) for n in nodes)
            if stale:
                out.append(it["buffer_id"])
        except Exception:
            continue      # 대조 실패 = 유지(preview 는 죽지 않는다)
    return out


def _selftest():
    """환경 비의존·결정적 게이트 — 격리 tempdir 에서 순수 함수 + 승인 경계(fail-closed·
    owner SAVE 앵커 승격)를 검증한다. 실 ledger/운영홈 미접근(save_paired 실저장은
    데모/E2E 가 커버 — 여기선 db=None 으로 fail-closed 조기반환 경로만 확인)."""
    import tempfile
    from binggupack.safety.gate_log import (
        preview_ref_for_candidates, gate_human_for_ref, gate_record_from_prompt,
    )
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    items = [
        {"idx": 1, "text": "빠른 결정이 느린 완벽보다 낫다."},
        {"idx": 2, "text": "성공은 크기가 아니라 자유다."},
        {"idx": 3, "text": "안 됩니다는 답이 아니다."},
    ]

    # 1) _anchor_candidates: idx 순서·원문 보존, sentence 키(앵커 재계산 패리티의 근거)
    anc = _anchor_candidates(items)
    check("anchor_len", len(anc) == 3)
    check("anchor_order", [a["sentence"] for a in anc] == [it["text"] for it in items])

    # 2) render_batch_preview: 번호 + 원문 발췌 + SAVE 안내(무음 폐기 방지)
    pv = render_batch_preview(items)
    check("preview_nums", ("| 1 |" in pv) and ("| 2 |" in pv) and ("| 3 |" in pv))
    check("preview_save_hint", "SAVE" in pv)
    check("preview_excerpt", "성공은 크기가 아니라 자유다." in pv)

    # 3) preview_ref 안정성 — 같은 buffer 는 어디서 재계산해도 동일 pref(순서보존 계약)
    p1 = preview_ref_for_candidates(anc)
    p2 = preview_ref_for_candidates(_anchor_candidates(items))
    check("pref_stable", bool(p1) and p1 == p2)

    with tempfile.TemporaryDirectory() as td:
        anchor_path = os.path.join(td, "last_preview_candidates.json")
        gate_log = os.path.join(td, "save_gate_log.jsonl")

        # 4) stage_batch_anchor: rows 수 == items 수 + 앵커 파일 생성(저장 0)
        n = stage_batch_anchor(items, path=anchor_path)
        check("stage_rows", n == 3)
        check("stage_file", os.path.exists(anchor_path))

        # 5) fail-closed A: indices 없음 → applied False(db 접근 전 조기반환)
        r0 = save_candidates_batch(None, None, items, [], gate_log_path=gate_log)
        check("fc_no_indices", (r0.get("applied") is False) and (r0.get("reason") == "no_indices"))

        # 6) fail-closed B(★핵심 안전): owner SAVE 앵커 미기록 → no_save_gate_ref·saved 0
        r1 = save_candidates_batch(None, None, items, [1], gate_log_path=gate_log)
        check("fc_no_gate_ref",
              (r1.get("applied") is False)
              and (r1.get("reason") == "no_save_gate_ref")
              and (r1.get("saved") == 0))

        # 7) 승격 계약: owner 'SAVE n' 발화가 앵커와 대조돼 human 승격되는지(저장 직전 게이트)
        rec = gate_record_from_prompt("SAVE 1,2", preview_path=anchor_path, gate_path=gate_log)
        check("gate_record", rec > 0)
        pref = preview_ref_for_candidates(anc)
        check("promote_true", gate_human_for_ref(pref, [1, 2], path=gate_log) is True)
        # 미발화 idx 는 승격 안 됨(부분 우회 차단·all-or-nothing)
        check("promote_partial_false", gate_human_for_ref(pref, [3], path=gate_log) is False)

    # 8) transition_targets(B-08 ①): 저장 성공 + 기존재(pair_partial_exists) = 전이 대상,
    #    그 외 실패(pii 등)는 제외 — 전이가 과하면 미저장 발화가 preview 에서 사라진다(유실).
    tt = transition_targets([
        {"cand": 1, "applied": True, "reason": None},
        {"cand": 2, "applied": False, "reason": "pair_partial_exists"},
        {"cand": 3, "applied": False, "reason": "pii_or_secret"},
    ])
    check("transition_targets", tt == {1, 2})

    if fails:
        print("save_batch selftest FAIL: %s" % ", ".join(fails))
        print("GATE=NO-GO")
        return 1
    print("save_batch selftest: 8 checks pass (순수함수 3 + 앵커/게이트 fail-closed·승격 4 + 전이판정 1)")
    print("GATE=GO")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_save_batch: --selftest 로 검증 실행")
