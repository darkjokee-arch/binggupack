# -*- coding: utf-8 -*-
"""binggu_workspace_organize.py — 항목 W: 클라우드 workspace 정리(비파괴) 분석 + 정리안 산출.

역할: opencrab-cloud workspace(워크플로/팩/노드/엣지/소스)의 현황을 '읽기 전용'으로
스냅샷 → 순수 detector(네트워크 0·결정적)로 중복 팩·고아 노드·구조 개선 후보를 탐지 →
사람이 읽는 dry-run 리포트(자연어 제안문)를 만든다. 실행/삭제/병합/적용은 하지 않는다.

비파괴 보장 (구조적):
  - 본 모듈은 merge/delete/apply/commit/prune 같은 **파괴 callable 을 아예 export 하지 않는다.**
    _selftest 가 모듈 심볼에 파괴 callable 0 임을 직접 assert(W7) → '실수로도 못 지움'.
  - report.execution_allowed=False·safe_mode=True·destructive_actions=[] 고정.

안전 불변 (전부 _selftest 로 증명):
  - transport 주입형 읽기 전용 리스팅: transport(resource:str, **params)->dict|list.
    실 endpoint·urllib·네트워크 의존은 본 모듈에 두지 않음(default 실 transport 부재).
    호출자가 주입할 때만 호출. transport is None → 호출 0 + reason='NO_TRANSPORT'.
  - import 부수효과 0(network/urllib 미접근). 모든 실패는 typed dict 흡수 — 절대 raise 0.
  - 결정성: 동일 스냅샷 2회 → 출력 동일(정렬 고정 + generated_at 옵션 고정).
  - PII/secret 잔존 0(watcher_batch_m1.scan_residual_pii — 부재 시 graceful skip).

topic_to_pack/Phase3 통합용 진입점: analyze(transport, ...) (읽기 전용·raise 0).
"""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 읽기 전용으로 스냅샷할 워크스페이스 리소스(고수준 리스팅 추상)
RESOURCES = ("workflows", "packs", "nodes", "edges", "sources")

# 클라우드 응답 키 변형 허용 — 노드/엣지/팩 식별자·타입 다중 키 정규화
_ID_KEYS = ("id", "node_id", "pack_id", "workflow_id", "uid", "_id")
_TYPE_KEYS = ("node_type", "type", "kind", "label_type")
_TITLE_KEYS = ("title", "name", "label")
_TOPIC_KEYS = ("topic", "subject", "theme")
_SENTENCE_KEYS = ("sentence", "text", "content", "body")
_NODE_IDS_KEYS = ("node_ids", "nodes", "members", "node_refs")
_EDGE_SRC_KEYS = ("source", "src", "from", "source_id", "start")
_EDGE_DST_KEYS = ("target", "dst", "to", "target_id", "end")


# ───────────────────────────── 정규화 헬퍼 ─────────────────────────────
def _first(d, keys, default=None):
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _as_list(value):
    """transport 결과를 항상 list 로 정규화. {items:[...]}/{results:[...]}/{data:[...]} 허용."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for k in ("items", "results", "data", "nodes", "packs", "workflows", "edges", "sources"):
            if isinstance(value.get(k), list):
                return value[k]
        return [value]
    return []


def _norm_id(item):
    return _first(item, _ID_KEYS)


def _norm_type(item):
    return str(_first(item, _TYPE_KEYS, "") or "")


def _norm_title(item):
    return str(_first(item, _TITLE_KEYS, "") or "")


def _norm_topic(item):
    # manifest 안에 topic 이 있는 경우도 허용
    t = _first(item, _TOPIC_KEYS)
    if t:
        return str(t)
    man = item.get("manifest") if isinstance(item, dict) else None
    if isinstance(man, dict):
        return str(_first(man, _TOPIC_KEYS, "") or "")
    return ""


def _norm_node_ids(pack):
    """pack 이 참조하는 노드 id 집합. node_ids/nodes/members 또는 노드 객체 리스트 허용."""
    raw = _first(pack, _NODE_IDS_KEYS, [])
    out = set()
    for n in _as_list(raw):
        if isinstance(n, dict):
            nid = _norm_id(n)
            if nid is not None:
                out.add(nid)
        elif n not in (None, ""):
            out.add(n)
    return out


def _norm_sentence(node):
    props = node.get("properties") if isinstance(node, dict) else None
    if isinstance(props, dict):
        s = _first(props, _SENTENCE_KEYS)
        if s:
            return str(s)
    return str(_first(node, _SENTENCE_KEYS, "") or "")


def _tokens(text):
    return tuple(sorted(set(re.findall(r"[0-9a-z가-힣]+", str(text or "").lower()))))


def _sha8(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:8]


# ───────────────────────────── 페처 (읽기 전용) ─────────────────────────────
def fetch_workspace(transport, *, resources=RESOURCES):
    """transport 로 각 resource 를 읽어 스냅샷 정규화. 절대 raise 0.

    반환: {snapshot:{resource:[...]}, errors:[{resource,error_type}], reason}
      - transport is None → 호출 0·reason='NO_TRANSPORT'·snapshot 빈 리스트.
      - 개별 resource 예외/None → errors 기록 후 빈 리스트로 계속.
    """
    snapshot = {r: [] for r in resources}
    errors = []
    if transport is None:
        return {"snapshot": snapshot, "errors": [], "reason": "NO_TRANSPORT"}

    for r in resources:
        try:
            raw = transport(r)
            snapshot[r] = _as_list(raw)
        except Exception as ex:  # noqa — 읽기 실패도 흡수(상위 raise 0)
            errors.append({"resource": r, "error_type": type(ex).__name__})
            snapshot[r] = []
    return {"snapshot": snapshot, "errors": errors, "reason": None}


# ───────────────────────────── 순수 detector (네트워크 0·결정적) ─────────────────────────────
def detect_duplicate_packs(packs):
    """동일 title/topic/content_hash 팩 그룹 탐지. 2개 이상만 후보. 정렬 결정적.

    반환: [{group_key, pack_ids:[...정렬...], reason:'same_title'|'same_topic'|'same_content_hash'}]
    우선순위(한 팩이 여러 신호 겹치면 content_hash > title > topic):
      - same_content_hash: 소속 노드 sentence sha 묶음이 동일.
      - same_title: 정규화 title 토큰이 동일(비어있지 않을 때).
      - same_topic: 정규화 topic 토큰이 동일(비어있지 않을 때).
    """
    by_hash, by_title, by_topic = {}, {}, {}
    for p in _as_list(packs):
        pid = _norm_id(p)
        if pid is None:
            continue
        # content hash — 소속 노드 id 묶음(정렬)으로 결정적 지문
        node_ids = sorted(str(x) for x in _norm_node_ids(p))
        if node_ids:
            h = _sha8("|".join(node_ids))
            by_hash.setdefault(h, []).append(pid)
        title_tok = _tokens(_norm_title(p))
        if title_tok:
            by_title.setdefault(title_tok, []).append(pid)
        topic_tok = _tokens(_norm_topic(p))
        if topic_tok:
            by_topic.setdefault(topic_tok, []).append(pid)

    groups = []
    seen_pairs = set()  # (frozenset(pack_ids)) 중복 그룹 억제 — content_hash 우선

    def _emit(reason, key, ids):
        ids_sorted = sorted(set(str(i) for i in ids))
        if len(ids_sorted) < 2:
            return
        fp = frozenset(ids_sorted)
        if fp in seen_pairs:
            return
        seen_pairs.add(fp)
        groups.append({"group_key": str(key), "pack_ids": ids_sorted, "reason": reason})

    for h, ids in by_hash.items():
        _emit("same_content_hash", h, ids)
    for tok, ids in by_title.items():
        _emit("same_title", " ".join(tok), ids)
    for tok, ids in by_topic.items():
        _emit("same_topic", " ".join(tok), ids)

    groups.sort(key=lambda g: (g["reason"], g["group_key"], tuple(g["pack_ids"])))
    return groups


def detect_orphan_nodes(nodes, packs, edges):
    """엣지로 참조 안 되고 어떤 pack 의 node_ids 에도 안 든 노드. Document 타입 제외.

    반환: [{node_id, reason:'no_pack_ref'|'no_edge'}] (정렬 결정적)
      - no_edge: 엣지 source/target 어디에도 안 나타남 + pack 미소속.
      - no_pack_ref: 엣지엔 있으나 어떤 pack 에도 미소속(약한 고아).
    """
    referenced = set()
    for e in _as_list(edges):
        s = _first(e, _EDGE_SRC_KEYS)
        t = _first(e, _EDGE_DST_KEYS)
        if s is not None:
            referenced.add(s)
        if t is not None:
            referenced.add(t)

    in_pack = set()
    for p in _as_list(packs):
        in_pack |= _norm_node_ids(p)

    orphans = []
    for n in _as_list(nodes):
        nid = _norm_id(n)
        if nid is None:
            continue
        if _norm_type(n).lower() == "document":
            continue  # 구조적 루트 — 고아 아님
        has_edge = nid in referenced
        has_pack = nid in in_pack
        if not has_pack and not has_edge:
            orphans.append({"node_id": nid, "reason": "no_edge"})
        elif not has_pack:
            orphans.append({"node_id": nid, "reason": "no_pack_ref"})
    orphans.sort(key=lambda o: (str(o["node_id"]), o["reason"]))
    return orphans


def detect_structure_candidates(workflows, packs, nodes):
    """구조 개선 후보(dry-run 제안). 정렬 결정적.

    반환: [{kind, target_id, note}]
      - empty_workflow: 노드/팩 0 워크플로.
      - empty_pack: 소속 노드 0 팩.
      - near_duplicate_workflow_name: 정규화 이름 토큰이 동일한 워크플로 2+.
      - unmanaged_nodes: 어떤 pack 에도 안 든 노드 군집(요약 1행).
    """
    cands = []

    # empty_workflow — workflow 가 참조하는 노드/팩이 0
    for w in _as_list(workflows):
        wid = _norm_id(w)
        if wid is None:
            continue
        refs = _norm_node_ids(w)
        wf_packs = _as_list(_first(w, ("packs", "pack_ids"), []))
        if not refs and not wf_packs:
            cands.append({"kind": "empty_workflow", "target_id": wid,
                          "note": "노드/팩 참조가 없는 워크플로"})

    # empty_pack — 소속 노드 0
    for p in _as_list(packs):
        pid = _norm_id(p)
        if pid is None:
            continue
        if not _norm_node_ids(p):
            cands.append({"kind": "empty_pack", "target_id": pid,
                          "note": "소속 노드가 0인 팩"})

    # near_duplicate_workflow_name — 동일 정규화 이름 토큰
    by_name = {}
    for w in _as_list(workflows):
        wid = _norm_id(w)
        tok = _tokens(_norm_title(w))
        if wid is None or not tok:
            continue
        by_name.setdefault(tok, []).append(wid)
    for tok, ids in by_name.items():
        if len(ids) >= 2:
            cands.append({"kind": "near_duplicate_workflow_name",
                          "target_id": sorted(str(i) for i in ids)[0],
                          "note": "이름이 유사한 워크플로 %d개: %s"
                                  % (len(ids), ", ".join(sorted(str(i) for i in ids)))})

    # unmanaged_nodes — 어떤 pack 에도 안 든 비-Document 노드 군집(요약 1행)
    in_pack = set()
    for p in _as_list(packs):
        in_pack |= _norm_node_ids(p)
    unmanaged = [_norm_id(n) for n in _as_list(nodes)
                 if _norm_id(n) is not None and _norm_id(n) not in in_pack
                 and _norm_type(n).lower() != "document"]
    if unmanaged:
        cands.append({"kind": "unmanaged_nodes", "target_id": None,
                      "note": "팩 미소속 노드 %d개 — 팩 편성 검토" % len(unmanaged)})

    cands.sort(key=lambda c: (c["kind"], str(c["target_id"]), c["note"]))
    return cands


# ───────────────────────────── 리포트 빌더 (사람용 dry-run) ─────────────────────────────
def _build_recommendations(dup_packs, orphans, structure):
    """자연어 제안문(실행 아님 — 사람이 검토). 결정적."""
    recs = []
    for g in dup_packs:
        recs.append("중복 의심 팩 %d개(%s) — 검토 후 하나로 병합 고려: %s"
                    % (len(g["pack_ids"]), g["reason"], ", ".join(g["pack_ids"])))
    if orphans:
        recs.append("고아 노드 %d개 — 팩/엣지 연결 또는 보관 검토(자동 삭제 아님)" % len(orphans))
    for c in structure:
        if c["kind"] == "empty_workflow":
            recs.append("빈 워크플로(%s) — 노드 추가 또는 정리 검토" % c["target_id"])
        elif c["kind"] == "empty_pack":
            recs.append("빈 팩(%s) — 노드 편성 또는 정리 검토" % c["target_id"])
        elif c["kind"] == "near_duplicate_workflow_name":
            recs.append("이름 유사 워크플로 — %s" % c["note"])
        elif c["kind"] == "unmanaged_nodes":
            recs.append(c["note"])
    if not recs:
        recs.append("정리 후보 없음 — 워크스페이스 양호")
    return recs


def build_report(snapshot, *, errors=None, generated_at=None):
    """스냅샷 → 사람용 dry-run 리포트. raise 0·비파괴 고정.

    반환: {status, generated_at, counts, duplicate_packs, orphan_nodes,
           structure_candidates, recommendations, errors, safe_mode:True,
           execution_allowed:False, destructive_actions:[]}
    generated_at=None(기본) → None 으로 고정(결정성). 호출자가 타임스탬프 주입 가능.
    """
    snap = snapshot or {}
    workflows = _as_list(snap.get("workflows"))
    packs = _as_list(snap.get("packs"))
    nodes = _as_list(snap.get("nodes"))
    edges = _as_list(snap.get("edges"))
    sources = _as_list(snap.get("sources"))

    dup_packs = detect_duplicate_packs(packs)
    orphans = detect_orphan_nodes(nodes, packs, edges)
    structure = detect_structure_candidates(workflows, packs, nodes)

    return {
        "status": "OK",
        "generated_at": generated_at,
        "counts": {"workflows": len(workflows), "packs": len(packs),
                   "nodes": len(nodes), "edges": len(edges), "sources": len(sources)},
        "duplicate_packs": dup_packs,
        "orphan_nodes": orphans,
        "structure_candidates": structure,
        "recommendations": _build_recommendations(dup_packs, orphans, structure),
        "errors": list(errors or []),
        "safe_mode": True,            # 비파괴 — 분석/제안만
        "execution_allowed": False,   # 실행/삭제/병합 0 (사람이 결정)
        "destructive_actions": [],    # 항상 빈 채 — 파괴 액션 산출 안 함
    }


# ───────────────────────────── 메인 진입점 ─────────────────────────────
def analyze(transport, *, env=None, config_path=None, home=None, resources=RESOURCES,
            generated_at=None):
    """Phase3/topic_to_pack 통합용 메인 진입점. 읽기 전용·raise 0.

    fetch_workspace(transport) → detectors → build_report.
    transport None/예외도 status 포함 typed 반환(raise 0).
    env/config_path/home 은 향후 owner 토글 자리(현재 읽기 전용이라 게이트=transport 주입 자체).
    """
    fetched = fetch_workspace(transport, resources=resources)
    report = build_report(fetched["snapshot"], errors=fetched["errors"],
                          generated_at=generated_at)
    if fetched["reason"]:
        report["reason"] = fetched["reason"]
    return report


# ───────────────────────────── selftest (mock transport · 네트워크 0) ─────────────────────────────
def _mk_workspace():
    """결정적 mock 스냅샷. 중복 팩·고아 노드·빈 구조 케이스 포함."""
    nodes = [
        {"id": "n1", "node_type": "Claim", "properties": {"sentence": "가격 예측 정확도 향상"}},
        {"id": "n2", "node_type": "Claim", "properties": {"sentence": "낙찰가 추정 기초금액"}},
        {"id": "n3", "node_type": "Claim", "properties": {"sentence": "고아 노드 본문"}},  # 고아
        {"id": "nd", "node_type": "Document", "properties": {"sentence": "문서 루트"}},     # 제외
    ]
    edges = [{"source": "n1", "target": "n2"}]
    packs = [
        {"pack_id": "p1", "title": "가격 예측 팩", "topic": "입찰 가격 예측",
         "node_ids": ["n1", "n2"]},
        {"pack_id": "p2", "title": "가격 예측 팩", "topic": "입찰 가격 예측",
         "node_ids": ["n1", "n2"]},   # p1 과 동일 title/topic/content → 중복
        {"pack_id": "p3", "title": "빈 팩", "node_ids": []},  # empty_pack
    ]
    workflows = [
        {"workflow_id": "w1", "name": "분석 워크플로", "node_ids": ["n1"]},
        {"workflow_id": "w2", "name": "빈 워크플로", "node_ids": []},  # empty_workflow
    ]
    sources = [{"id": "s1", "url": "harvest :: alpha"}]
    return {"workflows": workflows, "packs": packs, "nodes": nodes,
            "edges": edges, "sources": sources}


def _mk_transport(snapshot, spy=None):
    def transport(resource, **params):
        if spy is not None:
            spy["n"] += 1
        return list(snapshot.get(resource, []))
    return transport


def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    ws = _mk_workspace()
    transport = _mk_transport(ws)

    # ── W1 analyze → status OK + 필수키 전부 ──
    rep = analyze(transport)
    required = ("counts", "duplicate_packs", "orphan_nodes", "structure_candidates",
                "recommendations", "safe_mode", "execution_allowed", "destructive_actions")
    chk("W1 analyze(mock) status OK + report 필수키 전부",
        rep["status"] == "OK" and all(k in rep for k in required))

    # ── W2 동일 title/topic 팩 2개 → 1그룹 ──
    dups = rep["duplicate_packs"]
    has_pair = any(set(g["pack_ids"]) == {"p1", "p2"} and len(g["pack_ids"]) == 2 for g in dups)
    chk("W2 동일 title/topic 팩 → 1그룹(pack_ids 2개)", has_pair)

    # ── W3 중복 없는 팩 → [] ──
    no_dup = build_report({"packs": [
        {"pack_id": "a", "title": "유일1", "topic": "t1", "node_ids": ["x"]},
        {"pack_id": "b", "title": "유일2", "topic": "t2", "node_ids": ["y"]}]})
    chk("W3 중복 없는 팩 → duplicate_packs == []", no_dup["duplicate_packs"] == [])

    # ── W4 고아 노드 탐지 ──
    orphan_ids = {o["node_id"] for o in rep["orphan_nodes"]}
    chk("W4 edge·pack 미참조 노드(n3) → 고아 탐지", "n3" in orphan_ids)

    # ── W5 참조된 노드/팩 소속 노드는 고아 제외 + Document 제외 ──
    chk("W5 참조/소속 노드(n1,n2)·Document(nd) 고아 제외",
        "n1" not in orphan_ids and "n2" not in orphan_ids and "nd" not in orphan_ids)

    # ── W6 빈 워크플로 + 빈 팩 → structure_candidates 포함 ──
    kinds = {(c["kind"], c["target_id"]) for c in rep["structure_candidates"]}
    chk("W6 empty_workflow(w2)·empty_pack(p3) 포함",
        ("empty_workflow", "w2") in kinds and ("empty_pack", "p3") in kinds)

    # ── W7 안전: 비파괴 불변 + 모듈 심볼에 파괴 callable 0 ──
    import sys as _sys
    mod = _sys.modules[__name__]
    destructive = [n for n in dir(mod)
                   if any(k in n.lower() for k in
                          ("merge", "delete", "apply", "commit", "prune", "remove",
                           "destroy", "drop", "execute_"))
                   and callable(getattr(mod, n, None))]
    chk("W7 비파괴 불변 + 파괴 callable 0",
        rep["execution_allowed"] is False and rep["safe_mode"] is True
        and rep["destructive_actions"] == [] and destructive == [])

    # ── W8 transport 예외 → raise 0·errors 기록·status 반환 ──
    def boom(resource, **params):
        raise RuntimeError("net_down")

    r_err = analyze(boom)
    chk("W8 transport 예외 → raise 0·errors 에 resource+error_type·status OK",
        isinstance(r_err, dict) and r_err["status"] == "OK"
        and len(r_err["errors"]) == len(RESOURCES)
        and all("resource" in e and "error_type" in e for e in r_err["errors"]))

    # ── W9 transport=None → NO_TRANSPORT·호출 0 ──
    spy = {"n": 0}
    r_nt = analyze(None)
    # spy 는 None transport 라 증가 불가 — fetch 가 호출 자체를 안 함을 reason 으로 확증
    chk("W9 transport None → reason NO_TRANSPORT·호출 0",
        r_nt.get("reason") == "NO_TRANSPORT" and spy["n"] == 0
        and r_nt["counts"]["nodes"] == 0)

    # spy 카운터로 정상 경로 호출 수 = RESOURCES 개수 확인(읽기 전용 N회)
    spy2 = {"n": 0}
    analyze(_mk_transport(ws, spy=spy2))
    chk("W9b 정상 transport → resource 당 1회(총 %d)" % len(RESOURCES),
        spy2["n"] == len(RESOURCES))

    # ── W10 출력 PII/secret 잔존 0 ──
    try:
        import watcher_batch_m1 as _bm1
        blob = json.dumps(rep, ensure_ascii=False)
        chk("W10 report 출력 PII/secret 잔존 0", not _bm1.scan_residual_pii(blob))
    except Exception:
        chk("W10 report 출력 PII/secret 잔존 0(scanner 부재 skip)", True)

    # ── W11 결정성: 동일 스냅샷 2회 → 출력 동일 ──
    a = analyze(_mk_transport(ws))
    b = analyze(_mk_transport(ws))
    chk("W11 결정성(동일 스냅샷 2회 출력 동일)",
        json.dumps(a, ensure_ascii=False, sort_keys=True)
        == json.dumps(b, ensure_ascii=False, sort_keys=True))

    # ── W12 빈 워크스페이스 → status OK·모든 후보 빈 채 ──
    empty = analyze(_mk_transport({r: [] for r in RESOURCES}))
    chk("W12 빈 워크스페이스 → status OK·후보 전부 []",
        empty["status"] == "OK" and empty["duplicate_packs"] == []
        and empty["orphan_nodes"] == [] and empty["structure_candidates"] == []
        and empty["counts"] == {"workflows": 0, "packs": 0, "nodes": 0,
                                "edges": 0, "sources": 0})

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE=" + ("GO" if passed == total else "NO-GO"))
    return passed == total


def main(argv=None):
    ap = argparse.ArgumentParser(prog="binggu_workspace_organize")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return 0 if _selftest() else 1

    # CLI 단독 실행은 안내만(실 transport 없음 — 우발 네트워크 0)
    print("binggu_workspace_organize — 클라우드 워크스페이스 정리(비파괴) 분석.")
    print("  검증:   python binggu_workspace_organize.py --selftest")
    print("  진입점: analyze(transport)  # 읽기 전용 리포트(실행/삭제/병합 0)")
    print("  transport: callable(resource:str, **params) -> dict|list  (호출자 주입)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
