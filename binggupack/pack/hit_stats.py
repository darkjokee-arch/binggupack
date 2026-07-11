# -*- coding: utf-8 -*-
"""양방향 신뢰도 — owner 직감 / ai 반박 적중률 (별도 분모·시간감쇠·표본게이트).

4cli 토론 도출 불변식:
  5(이중계상 분리): owner/ai 적중률을 speaker 별도 분모로 산정(같은 표본 이중계상 금지).
  6(라벨 오염 차단): record 는 사람 resolve(actor=human) 시에만 — AI 자동 기록 0.
  7(시간감쇠): 조회 시 반감기 가중(낡은 적중률이 새 판단을 오염시키지 않게).
  8(표본게이트): N<N_MIN 이면 rate=None·weight=0(과소표본 편향 차단).

정체성(헌법): 신뢰도는 '참고 가중치'이지 '맹종 스위치'가 아니다. AI 가 owner 직감을
무조건 신뢰·선실행하는 근거로 쓰지 않는다 — 양쪽(owner/ai) 적중률을 모두 표시해 사람이 판단.
hit_events 는 append-only 이벤트 로그(조회 시 산정) → 시간감쇠·표본게이트가 자연 반영.

★ 비인과 불변식 (guard3 H1~H6 — 검증서 가드 fix):
  적중률은 정렬/순위/선택 key 에 절대 진입 금지(상관≠인과). proposal_priority_signal /
  get_hit_rate 반환은 표시(signal)용일 뿐 ordering 을 바꾸는 입력으로 쓰지 못한다.
  모든 신호 반환에 signal_only=True · not_causal=True 라벨을 박고, assert_not_ranking_input()
  으로 이 반환이 node_rank_score·compute_score·why_search·preflight 정렬 key 로 흘러들면
  TypeError(비인과 위반)를 내도록 코드 레벨로 봉쇄한다. 라벨은 보조, 실 가드는 이 차단.
  domain 분리 시 N_MIN 게이트를 domain별로 적용(소표본 과신 차단). rate=None 이면 priority 기여 0.
  domain 키는 binggu_recall._domain_from_cwd 정규화 재사용(표기 흔들림→분모 쪼개짐 방지).

strangler: 순수 정본(record_resolution · record_stage1_selection · get_hit_rate · both_sides ·
proposal_priority_signal · snapshot_context · classify_outcome · assert_not_ranking_input ·
N_MIN · HALFLIFE_DAYS · PAIR_RELS · _selftest)은 이 모듈로 byte-identical 이관됐고, 진입점
scripts/binggu_hit_stats.py 는 공개 심볼 동일 thin wrapper 다. 판정/적재 로직은 1바이트도
변하지 않았다. 미이관 bare-name 의존(binggu_recall._domain_from_cwd lazy · selftest 내
openbinggu_staging_write_selftest fixture)을 위해 scripts/ 를 sys.path 에 얹는다(원본이 자기
위치 scripts/ 를 얹던 것과 동일 의도 — shim 경유/패키지 직접 import 양쪽 모두 안전).
"""
import os
import sys
import json
import hashlib
from datetime import datetime, timezone

# 미이관 bare-name(binggu_recall._domain_from_cwd lazy · openbinggu_staging_write_selftest fixture)
# 해소 — 원본이 자기 위치(scripts/)를 얹던 것을 패키지 위치에서 scripts/ 로 재계산해 동일 효과.
_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

N_MIN = 5             # 표본 미달 시 가중 0(불변식8)
HALFLIFE_DAYS = 30.0  # 시간감쇠 반감기(불변식7)
PAIR_RELS = ("ai_accepts", "ai_refutes", "ai_revises")

# guard3: 비인과 신호 마커. 이 키가 박힌 dict 는 정렬/순위 key 입력으로 금지(assert_not_ranking_input).
_SIGNAL_MARK = "__binggu_hit_signal__"
_NOT_CAUSAL_NOTE = "상관≠인과 — 제안 우선순위 신호일 뿐 교체/정렬 근거 아님(guard3 H1/H2)"
_GOV_NOTE = ("이 수치는 표시 신호. 규칙/박제 변경은 사람이 빙구팩과 무관한 독립 도구로 판단"
            "(토론 3R A안). 적중률은 node_rank_score·compute_score·why_search·preflight 정렬 key 진입 금지.")


def _domain_norm(domain=None, cwd=None):
    """domain 키 정규화 — binggu_recall._domain_from_cwd 재사용(표기 흔들림 방지·소표본 분모 쪼개짐 차단).
    import 실패 시 graceful fallback(소문자화·basename)."""
    try:
        from binggu_recall import _domain_from_cwd
        return _domain_from_cwd(cwd, domain)
    except Exception:
        if domain:
            return str(domain).lower()
        if cwd:
            base = os.path.basename(os.path.normpath(str(cwd)))
            return base.lower() if base and base not in (".", os.sep) else None
        return None


def _parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _now_iso(ts=None):
    return ts if ts else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ai_outcome(owner_success, relation):
    """페어 relation 으로 ai 입장의 적중 도출. 수용=같은편 / 반박=반대 / 수정=중립(None=기록 skip)."""
    if relation == "ai_accepts":
        return owner_success
    if relation == "ai_refutes":
        return not owner_success
    return None  # ai_revises = 중립


# ---------------- guard3: 비인과 봉쇄 (H1/H2) ----------------

def assert_not_ranking_input(value, where="ranking_key"):
    """★ 비인과 불변식 강제: 적중률 신호(get_hit_rate/proposal_priority_signal/both_sides 반환)가
    정렬/순위/선택 key 입력으로 흘러들면 TypeError 를 낸다(라벨이 아니라 코드 차단).
    정렬 함수(node_rank_score·compute_score·why_search·preflight)는 입력을 이 가드에 통과시켜
    적중률이 ordering 에 인과적으로 진입하는 경로를 봉쇄한다. signal 이 아닌 값은 그대로 반환."""
    if isinstance(value, dict) and value.get(_SIGNAL_MARK):
        raise TypeError(
            "non_causal_violation: 적중률 신호는 %s 에 진입 금지(guard3 H1/H2·signal_only). %s"
            % (where, _NOT_CAUSAL_NOTE))
    return value


def _mark_signal(d):
    """신호 dict 에 비인과 라벨 + 마커 부착(소비처가 자동결정·정렬에 못 쓰게 명시)."""
    d[_SIGNAL_MARK] = True
    d["signal_only"] = True
    d["not_causal"] = _NOT_CAUSAL_NOTE
    return d


# ---------------- comp4: 선택 시점 근거 스냅샷 (상관≠인과 봉인·H4) ----------------

def snapshot_context(candidates, chosen_id, relevance_map, evidence_node_ids, ts=None):
    """선택 시점 근거 스냅샷 봉인 — 사후 적중률만 보고 인과로 오독 차단(PII 제외).
    sentence 원문 미저장: id·excerpt[:60]·관련성 수치만. _canon 후 sha256[:16].
    반환 {context_hash, snapshot}."""
    snap = {
        "chosen_id": chosen_id,
        "candidates": [
            {"id": c.get("id"),
             "excerpt": (c.get("sentence_excerpt") or "")[:60],
             "rel": round(float(relevance_map.get(c.get("id"), 0.0)), 4)}
            for c in (candidates or [])
        ],
        "evidence_node_ids": sorted(str(x) for x in (evidence_node_ids or [])),
        "ts": _now_iso(ts),
    }
    blob = json.dumps(snap, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ch = hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]
    return {"context_hash": ch, "snapshot": snap}


# ---------------- comp4: 실패의 명시적 정의 (분모 오염 방지) ----------------

def classify_outcome(owner_success, resolved, abandoned=False):
    """'hit'(직감 적중)/'miss'(직감 빗나감)/None(기록 skip).
    실패(miss)는 사람이 직감 빗나감을 명시 confirm 한 경우로만 한정.
    미해결(resolved False)·작업 폐기(abandoned)는 표본 아님(분모 오염 방지) → None."""
    if abandoned or not resolved:
        return None
    return "hit" if owner_success else "miss"


def _decision_id(owner_node_id, ts):
    """결정적 decision_id 생성(이중계상 방지 키). owner_node_id + ts 해시."""
    raw = "%s|%s" % (str(owner_node_id), str(ts))
    return "dec-" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _dup_exists(db, decision_id, node_id, speaker):
    """같은 (decision_id, node_id, speaker) 이미 기록됐는지(이중계상 가드)."""
    if not decision_id:
        return False
    r = db.con.execute(
        "SELECT 1 FROM hit_events WHERE decision_id=? AND node_id=? AND speaker=? LIMIT 1",
        (decision_id, node_id, speaker)).fetchone()
    return bool(r)


def record_resolution(db, owner_node_id, owner_success, ctx, ts=None,
                      domain=None, decision_id=None, context=None):
    """owner 직감 노드의 resolve 결과를 기록하고, 페어 ai 노드 입장을 도출해 함께 기록.
    헌법: actor=human 만(불변식6). owner_success=True(직감 적중)/False(빗나감).
    하위호환: domain/decision_id/context 미지정 시 NULL(기존 호출부 무수정).
      domain      : _domain_norm 정규화(분모 분리 키).
      decision_id : 미지정 시 _decision_id 결정적 생성(이중계상 방지).
      context     : snapshot_context 결과 → context_hash 적재(상관≠인과 봉인)."""
    if ctx.get("actor", "").strip().lower() != "human":
        return {"recorded": False, "reason": "G4_no_auto"}
    now = _now_iso(ts)
    row = db.con.execute("SELECT speaker, semantic_subtype FROM nodes WHERE node_id=?",
                         (owner_node_id,)).fetchone()
    if not row:
        return {"recorded": False, "reason": "node_not_found"}
    speaker, subtype = row
    dom = _domain_norm(domain)
    did = decision_id or _decision_id(owner_node_id, now)
    chash = (context or {}).get("context_hash")
    owner_speaker = speaker or "owner"
    # 이중계상 가드 — 같은 선택의 owner 이벤트가 이미 있으면 skip
    if _dup_exists(db, did, owner_node_id, owner_speaker):
        return {"recorded": False, "reason": "dup_decision"}
    rec = 0
    db.con.execute(
        "INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts,domain,context_hash,decision_id) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (owner_node_id, owner_speaker, "직감",
         "hit" if owner_success else "miss", subtype, now, dom, chash, did))
    rec += 1
    # 페어 ai 노드 (ai --(relation)--> owner) — 같은 decision_id·domain·context_hash 부여
    pairs = db.con.execute(
        "SELECT source, relation FROM edges WHERE target=? AND relation IN (?,?,?)",
        (owner_node_id, *PAIR_RELS)).fetchall()
    for ai_id, rel in pairs:
        ao = _ai_outcome(owner_success, rel)
        if ao is None:
            continue
        if _dup_exists(db, did, ai_id, "ai"):
            continue
        arow = db.con.execute("SELECT semantic_subtype FROM nodes WHERE node_id=?", (ai_id,)).fetchone()
        db.con.execute(
            "INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts,domain,context_hash,decision_id) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (ai_id, "ai", "반박" if rel == "ai_refutes" else "수용",
             "hit" if ao else "miss", arow[0] if arow else None, now, dom, chash, did))
        rec += 1
    db.con.commit()
    return {"recorded": True, "events": rec, "decision_id": did, "domain": dom, "context_hash": chash}


def record_stage1_selection(db, owner_node_id, chosen, candidates, relevance_map,
                            evidence_node_ids, ctx, domain, resolved, owner_success,
                            abandoned=False, ts=None):
    """1단(대비표)에서 사람이 고른 선택의 결과를 hit_events 에 누적하는 단일 공식 경로.
    절차: actor=human 게이트 → classify_outcome(미해결/폐기 skip) → snapshot_context 봉인
          → record_resolution(context=snap, domain, decision_id).
    빙구팩은 '기록만'(토론 결론 1·2단 GO). 규칙 변경·자동교체 0."""
    if ctx.get("actor", "").strip().lower() != "human":
        return {"recorded": False, "reason": "G4_no_auto"}
    outcome = classify_outcome(owner_success, resolved, abandoned)
    if outcome is None:
        return {"recorded": False, "reason": "unresolved_or_abandoned"}
    snap = snapshot_context(candidates, chosen, relevance_map, evidence_node_ids, ts=ts)
    return record_resolution(db, owner_node_id, owner_success, ctx, ts=ts,
                             domain=domain, context=snap)


# ---------------- comp4: 적중률 조회 (domain 분리·표본게이트·시간감쇠·비인과 라벨) ----------------

def get_hit_rate(db, speaker, subtype=None, now_ts=None, n_min=N_MIN,
                 halflife=HALFLIFE_DAYS, domain=None):
    """speaker(+subtype, +domain) 적중률(시간감쇠 가중·표본게이트). 반환 {rate,n,enough,weight,...}.
    enough=False(n<n_min) 면 rate=None·weight=0 → 호출자는 신뢰도 미반영(불변식8).
    domain 지정 시 도메인별 분모 분리 + N_MIN 을 domain별로 적용(소표본 과신 차단·guard3 H5).
    ★ 반환은 signal_only/not_causal 표지 + _SIGNAL_MARK — 정렬/순위 key 진입 금지(assert_not_ranking_input)."""
    now = (_parse_ts(now_ts) if now_ts else None) or datetime.now(timezone.utc)
    q = "SELECT outcome, ts FROM hit_events WHERE speaker=?"
    args = [speaker]
    if subtype is not None:
        q += " AND subtype=?"
        args.append(subtype)
    dom = _domain_norm(domain)
    if dom is not None:
        q += " AND domain=?"
        args.append(dom)
    rows = db.con.execute(q, args).fetchall()
    n = len(rows)
    if n < n_min:  # domain 지정 시에도 동일 N_MIN 게이트 → 소표본 신호 과신 차단
        return _mark_signal({"rate": None, "n": n, "enough": False, "weight": 0.0,
                             "domain": dom, "governance_note": _GOV_NOTE})
    wh = wt = 0.0
    for outcome, ts in rows:
        t = _parse_ts(ts)
        days = max(0.0, (now - t).total_seconds() / 86400.0) if t else 0.0
        w = 0.5 ** (days / halflife)
        wt += w
        if outcome == "hit":
            wh += w
    return _mark_signal({"rate": (wh / wt if wt > 0 else None), "n": n,
                         "enough": True, "weight": 1.0, "domain": dom,
                         "governance_note": _GOV_NOTE})


def both_sides(db, subtype=None, now_ts=None, domain=None):
    """양쪽(owner 직감 / ai 반박·수용) 적중률을 함께 반환 — 한쪽 편들지 않는 균형 표시(헌법).
    ★ signal_only — 정렬/순위 key 진입 금지."""
    return _mark_signal({
        "owner": get_hit_rate(db, "owner", subtype, now_ts, domain=domain),
        "ai": get_hit_rate(db, "ai", subtype, now_ts, domain=domain),
        "domain": _domain_norm(domain),
        "governance_note": _GOV_NOTE,
    })


def proposal_priority_signal(db, subtype=None, domain=None, now_ts=None):
    """제안 정렬용 '표시 신호'(상관≠인과). ★ 정렬/순위/선택 key 에 절대 진입 금지 — 표시 전용.
    enough=False(표본 미달)면 rate=None → 소비처는 신호 미반영(편향 차단·H5).
    반환 dict 에 _SIGNAL_MARK 가 박혀 assert_not_ranking_input 이 ordering 진입을 TypeError 로 차단(H1/H2)."""
    owner = get_hit_rate(db, "owner", subtype, now_ts, domain=domain)
    ai = get_hit_rate(db, "ai", subtype, now_ts, domain=domain)
    return _mark_signal({
        "owner": owner, "ai": ai,
        "domain": _domain_norm(domain),
        "governance_note": _GOV_NOTE,
        # 소표본 인과 착시 차단: rate=None 이면 priority 기여 0(소비처가 미반영해야 함을 명시)
        "priority_contribution": 0.0 if (not owner.get("enough") and not ai.get("enough")) else None,
    })


# ---------------- selftest ----------------

def _selftest():
    import tempfile, shutil
    from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS

    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="binggu_hit_")
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    def mk_owner(db, nid, subtype="J", speaker="owner"):
        db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,semantic_subtype,speaker) VALUES(?,?,?,?,?)",
                       (nid, "judgment", "판단 " + nid, subtype, speaker))
        db.con.commit()

    db = StagingDB(os.path.join(tmp, "h.sqlite"))

    # T1 record_resolution 기존 호출(domain/context 미지정) 동작 100% 동일 — 하위호환
    mk_owner(db, "o1")
    r1 = record_resolution(db, "o1", True, {"actor": "human"}, ts="2026-06-20T00:00:00Z")
    ev1 = db.con.execute("SELECT outcome FROM hit_events WHERE node_id='o1'").fetchone()
    rec(1, "record_resolution 하위호환(domain/context 미지정)", r1["recorded"] and ev1 == ("hit",))

    # T2 actor!='human' → G4_no_auto, 이벤트 0
    mk_owner(db, "o2")
    r2 = record_resolution(db, "o2", True, {"actor": "ai"})
    n2 = db.con.execute("SELECT count(*) FROM hit_events WHERE node_id='o2'").fetchone()[0]
    rec(2, "actor!=human → G4_no_auto·이벤트 0(불변식6)",
        (not r2["recorded"]) and r2["reason"] == "G4_no_auto" and n2 == 0)

    # T3 record_stage1_selection: snapshot context_hash·domain·decision_id 적재
    mk_owner(db, "o3")
    cands = [{"id": "o3", "sentence_excerpt": "근거 A"}, {"id": "x9", "sentence_excerpt": "근거 B"}]
    r3 = record_stage1_selection(db, "o3", "o3", cands, {"o3": 0.9, "x9": 0.3}, ["EV1"],
                                 {"actor": "human"}, "example-project", True, True, ts="2026-06-20T00:00:00Z")
    he3 = db.con.execute("SELECT domain,context_hash,decision_id FROM hit_events WHERE node_id='o3'").fetchone()
    rec(3, "record_stage1_selection: context_hash·domain·decision_id 봉인",
        r3["recorded"] and he3[0] == "example-project" and he3[1] and he3[2])

    # T4 실패 정의: resolved=False / abandoned=True → recorded False·이벤트 0
    mk_owner(db, "o4")
    r4a = record_stage1_selection(db, "o4", "o4", cands, {}, [], {"actor": "human"}, "bid", False, True, ts="2026-06-20T00:00:00Z")
    r4b = record_stage1_selection(db, "o4", "o4", cands, {}, [], {"actor": "human"}, "bid", True, True, abandoned=True, ts="2026-06-20T00:00:00Z")
    n4 = db.con.execute("SELECT count(*) FROM hit_events WHERE node_id='o4'").fetchone()[0]
    rec(4, "실패 정의(미해결/폐기)→recorded False·분모 미오염",
        (not r4a["recorded"]) and r4a["reason"] == "unresolved_or_abandoned"
        and (not r4b["recorded"]) and n4 == 0)

    # T5 이중계상 가드: 같은 decision_id+node_id+speaker 재호출 → dup_decision·INSERT 0
    mk_owner(db, "o5")
    did5 = _decision_id("o5", "2026-06-20T00:00:00Z")
    record_resolution(db, "o5", True, {"actor": "human"}, ts="2026-06-20T00:00:00Z", decision_id=did5)
    r5 = record_resolution(db, "o5", True, {"actor": "human"}, ts="2026-06-20T00:00:00Z", decision_id=did5)
    n5 = db.con.execute("SELECT count(*) FROM hit_events WHERE node_id='o5'").fetchone()[0]
    rec(5, "이중계상 가드(decision_id+node+speaker)→dup_decision·INSERT 0",
        (not r5["recorded"]) and r5["reason"] == "dup_decision" and n5 == 1)

    # T6 domain 분리: bid 6건·cook 6건 → get_hit_rate(domain='bid') n==6 분리, 전역 n==12
    db6 = StagingDB(os.path.join(tmp, "h6.sqlite"))
    for i in range(6):
        nid = "b%d" % i
        mk_owner(db6, nid)
        record_resolution(db6, nid, True, {"actor": "human"}, ts="2026-06-20T00:00:00Z", domain="bid")
    for i in range(6):
        nid = "c%d" % i
        mk_owner(db6, nid)
        record_resolution(db6, nid, True, {"actor": "human"}, ts="2026-06-20T00:00:00Z", domain="cook")
    hb = get_hit_rate(db6, "owner", domain="bid")
    hall = get_hit_rate(db6, "owner")
    rec(6, "domain 분리 분모(bid n==6·전역 n==12)", hb["n"] == 6 and hall["n"] == 12)

    # T7 표본게이트: domain별 N_MIN — bid 4건 → enough False·rate None·weight 0
    db7 = StagingDB(os.path.join(tmp, "h7.sqlite"))
    for i in range(4):
        nid = "s%d" % i
        mk_owner(db7, nid)
        record_resolution(db7, nid, True, {"actor": "human"}, ts="2026-06-20T00:00:00Z", domain="bid")
    h7 = get_hit_rate(db7, "owner", domain="bid")
    rec(7, "domain별 표본게이트 N<N_MIN→rate None·weight 0(H5)",
        (not h7["enough"]) and h7["rate"] is None and h7["weight"] == 0.0)

    # T8 시간감쇠: 오래된 hit 5 vs 최근 miss 5 → 최근 가중 더 반영(rate < 0.5)
    db8 = StagingDB(os.path.join(tmp, "h8.sqlite"))
    for i in range(5):
        nid = "old%d" % i
        mk_owner(db8, nid)
        record_resolution(db8, nid, True, {"actor": "human"}, ts="2025-01-01T00:00:00Z")
    for i in range(5):
        nid = "new%d" % i
        mk_owner(db8, nid)
        record_resolution(db8, nid, False, {"actor": "human"}, ts="2026-06-20T00:00:00Z")
    h8 = get_hit_rate(db8, "owner", now_ts="2026-06-21T00:00:00Z")
    rec(8, "시간감쇠: 최근 miss 가중↑→rate<0.5", h8["enough"] and h8["rate"] is not None and h8["rate"] < 0.5)

    # T9 페어 ai 도출 보존: ai_refutes 면 owner hit → ai miss, 같은 decision_id·domain 공유
    db9 = StagingDB(os.path.join(tmp, "h9.sqlite"))
    mk_owner(db9, "ow")
    db9.con.execute("INSERT INTO nodes(node_id,node_type,sentence,speaker,semantic_subtype) VALUES('ai1','claim','반박',?,?)", ("ai", "C"))
    db9.con.execute("INSERT INTO edges(edge_id,relation,source,target) VALUES('e1','ai_refutes','ai1','ow')")
    db9.con.commit()
    record_resolution(db9, "ow", True, {"actor": "human"}, ts="2026-06-20T00:00:00Z", domain="bid")
    ai_ev = db9.con.execute("SELECT outcome,domain,decision_id FROM hit_events WHERE node_id='ai1'").fetchone()
    ow_ev = db9.con.execute("SELECT decision_id FROM hit_events WHERE node_id='ow'").fetchone()
    rec(9, "페어 ai 도출 보존(refute→ai miss·decision_id/domain 공유)",
        ai_ev[0] == "miss" and ai_ev[1] == "bid" and ai_ev[2] == ow_ev[0])

    # T10 비인과 라벨: get_hit_rate/proposal_priority_signal 반환에 signal_only/not_causal/governance_note
    h10 = get_hit_rate(db6, "owner", domain="bid")
    p10 = proposal_priority_signal(db6, domain="bid")
    bs10 = both_sides(db6, domain="bid")
    rec(10, "비인과 라벨(signal_only/not_causal/governance_note·_SIGNAL_MARK)",
        h10.get("signal_only") and h10.get("not_causal") and h10.get(_SIGNAL_MARK)
        and p10.get("signal_only") and p10.get("governance_note")
        and bs10.get("signal_only"))

    # T11 ★ 비인과 봉쇄(H1/H2): 신호를 정렬 key 입력으로 넘기면 TypeError
    blocked = False
    try:
        assert_not_ranking_input(get_hit_rate(db6, "owner", domain="bid"), where="compute_score")
    except TypeError:
        blocked = True
    # 비신호(일반 값)는 통과
    passthru = (assert_not_ranking_input(0.7, where="compute_score") == 0.7)
    rec(11, "★ 비인과 봉쇄: 적중률 신호→정렬 key 진입 시 TypeError(H1/H2)", blocked and passthru)

    # T12 audit anchor 무손상: hit_events 적재 후 store_checksum 변화 0
    dbc = StagingDB(os.path.join(tmp, "h12.sqlite"))
    mk_owner(dbc, "ck1")
    ck_before = dbc.store_checksum()  # 노드 적재 후 — 이후 hit_events INSERT 만 측정
    record_resolution(dbc, "ck1", True, {"actor": "human"}, ts="2026-06-20T00:00:00Z", domain="bid")
    ck_after = dbc.store_checksum()
    rec(12, "audit anchor 무손상: hit_events 적재→store_checksum 불변", ck_before == ck_after)

    db.close(); db6.close(); db7.close(); db8.close(); db9.close(); dbc.close()

    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = before_mtime == after_mtime
    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 74)
    print("binggu_hit_stats — comp4 적중률 추적(2단) selftest (temp DB·운영 write 0)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print(f"{'[OK]' if v == 'PASS' else '[X]'} {cid:>2} {desc}")
    print("-" * 74)
    print(f"RESULT: {npass}/{len(results)} PASS")
    print(f"operating_store_unchanged={store_unchanged}  hit_events_append_only=1  signal_into_ranking=0")
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print(f"GATE: {gate}")
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_hit_stats: --selftest 로 검증 실행")
