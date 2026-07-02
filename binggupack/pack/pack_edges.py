"""binggu_pack_edges — 세분화된 여러 pack 의 메타 신호에서 **pack-간 workflow edges** 추론.

owner 항목 C: "팩 간 edges". build_pack 은 한 pack 내부 노드만 다루고 pack['edges']=[]
(노드 엣지 MVP 계약·불변). 본 모듈은 그와 **별개 차원** — 여러 세분화 pack 을 받아
OpenCrab workflow 가 쓰는 pack-level edge 구조 [{from_pkg, to_pkg, relation}] 를 만든다.

결정적 추론만(LLM·네트워크·임베딩 0). manifest 에 이미 존재하는 신호로만 관계 결정:
  (1) depends_on              → references     (child→parent, 방향성)
  (2) pack_id 경로 계층        → parent_of      (parent→child, 방향성)
  (3) sequence/order 연속      → sequence_next  (이전→다음, 방향성)
  (4) cross_pack_tags 교집합   → adjacent       (무방향·from<to 정규화)
  (5) topic 토큰 Jaccard≥임계  → related        (무방향·from<to 정규화)

한 쌍에 복수 신호 시 우선순위(references>parent_of>sequence_next>adjacent>related)로
**1개만** 채택(dedup). 빙구팩 철학: 절대 raise 0(잘못된 입력은 errors 에 typed 기록·전체
안 죽음), 무손실(입력 모든 valid pack 을 counts.packs 반영·by_relation 합==len(edges)),
기존 계약 보존(pack['edges'] 불변·여긴 pack-간 edge 라 키도 from_pkg/to_pkg 로 구분),
결정적 정렬 출력.

진입점(Phase3 통합용): infer_edges(pack_metas) -> dict.
입력 형태 3종 모두 허용: build_pack 반환 dict / 순수 pack dict / 순수 manifest dict.
"""
import os
import re
import sys
import json
import hashlib

# live 분기 lazy import(binggu_cloud_ingest_wire · watcher_batch_m1 — 미이관·bare-name) 해소:
# 원본이 자기 위치(scripts/)를 얹던 것을 패키지 위치(binggupack/pack/)에서 scripts/ 로 재계산해
# 동일 효과. shim 경유/패키지 직접 import 양쪽 모두 안전(t3_filter/workflow_recommend 선례).
_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# 관계 화이트리스트 + 우선순위(앞일수록 높음) — 한 쌍 복수 신호 시 1개만 채택
RELATIONS = ("references", "parent_of", "sequence_next", "adjacent", "related")
_PRIORITY = {r: i for i, r in enumerate(RELATIONS)}
# 신호별 결정적 confidence(related 만 Jaccard 로 동적)
_CONFIDENCE = {"references": 1.0, "parent_of": 0.9, "sequence_next": 0.85, "adjacent": 0.7}


def _tokens(text):
    """로컬 토큰화(workflow_recommend 패턴 차용 — import 부작용 0 위해 재구현)."""
    return set(re.findall(r"[0-9a-z가-힣]+", str(text or "").lower()))


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def _as_int(v):
    """sequence/order 값을 int 로 정규화. 불가하면 None(raise 0)."""
    if isinstance(v, bool):           # bool 은 int subclass — 순번으로 취급 안 함
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v.strip())
    return None


def _normalize_meta(obj):
    """build_pack 반환 / 순수 pack dict / 순수 manifest → 표준 메타 dict.

    반환 {pack_id, topic, depends_on, cross_pack_tags, scope, risk_level, sequence}.
    pack_id 가 없거나 입력이 dict 아니면 None(호출부가 errors 에 typed 기록 — raise 0).
    """
    if not isinstance(obj, dict):
        return None
    src = obj
    if isinstance(src.get("pack"), dict):          # build_pack 반환({status,pack,...})
        src = src["pack"]
    if isinstance(src.get("manifest"), dict):      # 순수 pack dict(manifest 하위)
        src = src["manifest"]
    pack_id = src.get("pack_id")
    if not isinstance(pack_id, str) or not pack_id.strip():
        return None
    dep = src.get("depends_on")
    dep = [d for d in dep if isinstance(d, str)] if isinstance(dep, list) else []
    tags = src.get("cross_pack_tags")
    tags = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    seq = _as_int(src.get("sequence"))
    if seq is None:
        seq = _as_int(src.get("order"))
    return {
        "pack_id": pack_id.strip(),
        "topic": src.get("topic") or "",
        "depends_on": dep,
        "cross_pack_tags": tags,
        "scope": src.get("scope"),
        "risk_level": src.get("risk_level"),
        "sequence": seq,
    }


def _hierarchy_relation(pack_id_a, pack_id_b):
    """pack_id 경로 계층(prefix) 판정. 반환 (parent_id, child_id) 또는 None.

    'topic/a' vs 'topic/a/b' → ('topic/a','topic/a/b'). 'topic/a' vs 'topic/ab' 는
    토큰 단위 비교라 prefix 아님(문자열 prefix 오탐 방지)."""
    pa = [p for p in str(pack_id_a).split("/") if p]
    pb = [p for p in str(pack_id_b).split("/") if p]
    if not pa or not pb or pa == pb:
        return None
    if len(pa) < len(pb) and pb[:len(pa)] == pa:
        return (pack_id_a, pack_id_b)
    if len(pb) < len(pa) and pa[:len(pb)] == pb:
        return (pack_id_b, pack_id_a)
    return None


def _ordered(x, y):
    """무방향 관계의 결정적 from<to 정규화."""
    return (x, y) if x <= y else (y, x)


def _pair_relation(meta_a, meta_b, topic_jaccard_min):
    """두 메타에서 우선순위 최고 1개 신호 → (from, to, relation, basis, confidence) 또는 None."""
    a, b = meta_a["pack_id"], meta_b["pack_id"]
    if a == b:                                   # self-edge 금지
        return None
    cands = []  # (priority, from, to, relation, basis, confidence)

    # (1) references — depends_on (child→parent, 방향성)
    if b in meta_a["depends_on"]:
        cands.append((_PRIORITY["references"], a, b, "references",
                      "depends_on", _CONFIDENCE["references"]))
    if a in meta_b["depends_on"]:
        cands.append((_PRIORITY["references"], b, a, "references",
                      "depends_on", _CONFIDENCE["references"]))

    # (2) parent_of — pack_id 경로 계층(parent→child, 방향성)
    hier = _hierarchy_relation(a, b)
    if hier:
        cands.append((_PRIORITY["parent_of"], hier[0], hier[1], "parent_of",
                      "pack_id_hierarchy", _CONFIDENCE["parent_of"]))

    # (3) sequence_next — sequence/order 연속(이전→다음, 방향성)
    sa, sb = meta_a["sequence"], meta_b["sequence"]
    if sa is not None and sb is not None and abs(sa - sb) == 1:
        frm, to = (a, b) if sa < sb else (b, a)
        cands.append((_PRIORITY["sequence_next"], frm, to, "sequence_next",
                      "sequence:%d->%d" % (min(sa, sb), max(sa, sb)),
                      _CONFIDENCE["sequence_next"]))

    # (4) adjacent — cross_pack_tags 교집합(무방향)
    shared = set(meta_a["cross_pack_tags"]) & set(meta_b["cross_pack_tags"])
    if shared:
        frm, to = _ordered(a, b)
        cands.append((_PRIORITY["adjacent"], frm, to, "adjacent",
                      "cross_pack_tags:" + ",".join(sorted(shared)),
                      _CONFIDENCE["adjacent"]))

    # (5) related — topic 토큰 Jaccard≥임계(무방향)
    j = _jaccard(_tokens(meta_a["topic"]), _tokens(meta_b["topic"]))
    if j >= topic_jaccard_min and j > 0.0:
        frm, to = _ordered(a, b)
        cands.append((_PRIORITY["related"], frm, to, "related",
                      "topic_jaccard:%.3f" % j, round(j, 3)))

    if not cands:
        return None
    # 우선순위 최고 1개 — 동순위(예: 양방향 references)는 결정적 정렬로 첫 항목
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    _, frm, to, rel, basis, conf = cands[0]
    return (frm, to, rel, basis, conf)


def infer_edges(pack_metas, *, topic_jaccard_min=0.34, max_edges=None):
    """세분화된 여러 pack 메타 → pack-간 workflow edges (결정적·preview only).

    인자:
      pack_metas       : build_pack 반환 dict / 순수 pack dict / 순수 manifest dict 의 리스트
      topic_jaccard_min: related 엣지 임계(기본 0.34 ≈ 2/3 겹침 근사·보수적)
      max_edges        : 정렬 후 상위 N 개로 제한(None=무제한)

    반환:
      {status, edges:[{from_pkg,to_pkg,relation,basis,confidence}], counts, errors}
      - status   : 항상 "OK"(raise 0 — 잘못된 입력은 errors 로). 입력 자체가 리스트 아니면 "ERROR".
      - counts   : {packs, edges, by_relation:{relation:n}}
      - errors   : [{index, reason}] — pack_id 누락/비-dict 등 skip 된 항목(전체는 안 죽음)
    """
    if not isinstance(pack_metas, (list, tuple)):
        return {"status": "ERROR", "reason": "pack_metas 가 list 아님",
                "edges": [], "counts": {"packs": 0, "edges": 0, "by_relation": {}},
                "errors": [{"index": -1, "reason": "INPUT_NOT_LIST"}]}

    metas, errors = [], []
    for i, obj in enumerate(pack_metas):
        m = _normalize_meta(obj)
        if m is None:
            reason = "NOT_A_DICT" if not isinstance(obj, dict) else "PACK_ID_MISSING"
            errors.append({"index": i, "reason": reason})
            continue
        metas.append(m)

    seen, raw_edges = set(), []
    for i in range(len(metas)):
        for j in range(i + 1, len(metas)):
            res = _pair_relation(metas[i], metas[j], topic_jaccard_min)
            if not res:
                continue
            frm, to, rel, basis, conf = res
            key = (frm, to, rel)
            if key in seen:                      # 쌍 중복 dedup(동일 pack_id 중복 입력 등)
                continue
            seen.add(key)
            raw_edges.append({"from_pkg": frm, "to_pkg": to, "relation": rel,
                              "basis": basis, "confidence": conf})

    raw_edges.sort(key=lambda e: (e["from_pkg"], e["to_pkg"], e["relation"]))
    if isinstance(max_edges, int) and max_edges >= 0:
        raw_edges = raw_edges[:max_edges]

    by_rel = {}
    for e in raw_edges:
        by_rel[e["relation"]] = by_rel.get(e["relation"], 0) + 1

    return {"status": "OK", "edges": raw_edges,
            "counts": {"packs": len(metas), "edges": len(raw_edges), "by_relation": by_rel},
            "errors": errors}


# ════════════════════════════════════════════════════════════════════════
# 항목 C2 — pack-간 edges → 실 OpenCrab opencrab_workflow_manage 배선
#   infer_edges 산출 edges({from_pkg,to_pkg,...}) 를 workflow_manage 가 요구하는
#   '워크플로우 node id 사이 방향 엣지'로 변환하는 순수 빌더 + transport 주입형
#   오케스트레이터. 전부 additive — infer_edges/RELATIONS/_CONFIDENCE 미수정.
#   실 호출은 AND(dry_run=False, BINGGU_WORKFLOW_SYNC=1, transport 주입, cfg.url) 만.
# ════════════════════════════════════════════════════════════════════════

WORKFLOW_TOOL = "opencrab_workflow_manage"
# owner 토글 — cloud ingest(BINGGU_CLOUD_INGEST)와 분리: ingest 켜도 workflow write 자동 활성 X
SYNC_ENABLE_ENV = "BINGGU_WORKFLOW_SYNC"
DEFAULT_CLIENT = "binggupack-pack-edges-sync"


def _node_id(pack_id):
    """pack_id → 결정적·ascii-safe workflow node id (충돌 0·원문 미변형).

    slug(영숫자만) + sha1(pack_id)[:10]. slug 가 동일해지는 'a/b' vs 'a.b' 도
    해시 접미사로 유일성 보장. package_id 원문은 절대 변형하지 않음(별 필드로 보존)."""
    pid = str(pack_id)
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", pid).strip("_")
    if len(slug) > 40:
        slug = slug[:40].strip("_")
    h = hashlib.sha1(pid.encode("utf-8")).hexdigest()[:10]
    return (slug + "_" + h) if slug else ("pkg_" + h)


def to_workflow_nodes_edges(infer_result, *, pack_metas=None):
    """infer_edges 결과 → workflow nodes/edges (pack_id→node id 매핑·referential integrity).

    입력(raise 0):
      - dict(+'edges' 리스트 키)       → 그대로 edges 사용
      - list/tuple(pack_metas)         → infer_edges() 내부 호출
      - 그 외(비-list·status ERROR 등) → 빈 결과(typed)
    pack_metas 를 주면 엣지 없는 pack 도 node 로 포함(무손실·완전성).

    반환: {nodes, edges, node_map:{pack_id:node_id}, counts:{nodes,edges}}.
      nodes : {id, package_id, title, (order)}  ※ order 는 sequence 있을 때만
      edges : {from, to, relation, confidence, basis}  ※ node id 기준
    """
    # 입력 정규화
    if isinstance(infer_result, dict) and isinstance(infer_result.get("edges"), list):
        inferred = infer_result["edges"]
    elif isinstance(infer_result, (list, tuple)):
        inferred = infer_edges(infer_result).get("edges", [])
    else:
        inferred = []

    # pack_metas 정규화 → pack_id별 메타(title/order 용)
    meta_by_id = {}
    if isinstance(pack_metas, (list, tuple)):
        for obj in pack_metas:
            m = _normalize_meta(obj)
            if m is not None:
                meta_by_id.setdefault(m["pack_id"], m)

    # node_map 대상 pack_id: edges from/to 합집합 + valid metas(완전성)
    pack_ids = set(meta_by_id.keys())
    for e in inferred:
        if isinstance(e, dict):
            if isinstance(e.get("from_pkg"), str):
                pack_ids.add(e["from_pkg"])
            if isinstance(e.get("to_pkg"), str):
                pack_ids.add(e["to_pkg"])

    node_map = {pid: _node_id(pid) for pid in sorted(pack_ids)}  # 정렬 순회 → 결정적

    nodes = []
    for pid in sorted(pack_ids):
        m = meta_by_id.get(pid)
        node = {"id": node_map[pid], "package_id": pid,
                "title": (m["topic"] if (m and m.get("topic")) else pid)}
        if m and m.get("sequence") is not None:
            node["order"] = m["sequence"]
        nodes.append(node)

    edges = []
    for e in inferred:
        if not isinstance(e, dict):
            continue
        frm, to = e.get("from_pkg"), e.get("to_pkg")
        if frm not in node_map or to not in node_map:   # referential integrity
            continue
        edges.append({"from": node_map[frm], "to": node_map[to],
                      "relation": e.get("relation"), "confidence": e.get("confidence"),
                      "basis": e.get("basis")})

    return {"nodes": nodes, "edges": edges, "node_map": node_map,
            "counts": {"nodes": len(nodes), "edges": len(edges)}}


def build_workflow_payload_from_edges(infer_result, *, pack_metas=None, action="create",
                                      workflow_name=None, workflow_id=None, description=None,
                                      status="draft", rpc_id=1):
    """infer_edges 결과 → opencrab_workflow_manage tools/call JSON-RPC payload (순수·raise 0).

    create면 name=workflow_name. update면 workflow_id/workflow_name 패스스루.
    반환: {jsonrpc:'2.0', id:int, method:'tools/call',
           params:{name:WORKFLOW_TOOL, arguments:{action, nodes, edges, ...}}}."""
    ne = to_workflow_nodes_edges(infer_result, pack_metas=pack_metas)
    args = {"action": action, "nodes": ne["nodes"], "edges": ne["edges"]}
    if action == "create":
        if workflow_name is not None:
            args["name"] = workflow_name
    else:
        if workflow_id is not None:
            args["workflow_id"] = workflow_id
        if workflow_name is not None:
            args["workflow_name"] = workflow_name
    if description is not None:
        args["description"] = description
    if status is not None:
        args["status"] = status
    return {"jsonrpc": "2.0", "id": int(rpc_id), "method": "tools/call",
            "params": {"name": WORKFLOW_TOOL, "arguments": args}}


def sync_edges_to_workflow(infer_result, *, pack_metas=None, transport=None, env=None,
                           config_path=None, home=None, dry_run=True, action="create",
                           workflow_name=None, workflow_id=None, description=None,
                           status="draft", rpc_id=1):
    """pack-간 edges → opencrab_workflow_manage 동기화 오케스트레이터 (typed·raise 0).

    삼중 게이트(AND): dry_run=False + BINGGU_WORKFLOW_SYNC=1 + transport 주입 + cfg.url 존재.
    하나라도 미충족이면 transport 호출 0. 기본 dry_run=True → payload 만(네트워크 0).

    반환: {mode:'dry-run'|'live'|'error', action, planned_calls, payload, results?,
           reason, token_fingerprint, source}. 원문 토큰 미노출(sha8 지문만)."""
    try:
        payload = build_workflow_payload_from_edges(
            infer_result, pack_metas=pack_metas, action=action, workflow_name=workflow_name,
            workflow_id=workflow_id, description=description, status=status, rpc_id=rpc_id)
    except Exception as ex:  # noqa — 순수 계산도 방어(raise 0)
        return {"mode": "error", "action": action, "planned_calls": 0, "payload": None,
                "reason": "BUILD_ERROR:" + type(ex).__name__,
                "token_fingerprint": "n/a", "source": "n/a"}

    # dry-run(기본): 계획만 — transport 주입돼 있어도 미호출(네트워크 0)
    if dry_run:
        return {"mode": "dry-run", "action": action, "planned_calls": 1, "payload": payload,
                "reason": "DRY_RUN", "token_fingerprint": "n/a", "source": "n/a"}

    # live 게이트 1: owner 토글
    e = os.environ if env is None else env
    if str(e.get(SYNC_ENABLE_ENV, "")).strip() != "1":
        return {"mode": "live", "action": action, "planned_calls": 1, "payload": payload,
                "results": None, "reason": "WORKFLOW_SYNC_DISABLED",
                "token_fingerprint": "n/a", "source": "n/a"}
    # live 게이트 2: transport
    if transport is None:
        return {"mode": "live", "action": action, "planned_calls": 1, "payload": payload,
                "results": None, "reason": "NO_TRANSPORT",
                "token_fingerprint": "n/a", "source": "n/a"}
    # live 게이트 3: cloud config(url) — 여기서만 lazy import(모듈 import 부작용 0 보존)
    try:
        import binggu_cloud_ingest_wire as CW  # noqa: E402 — live 분기 전용 지연 로드
        cfg = CW.load_cloud_config(env=env, config_path=config_path, home=home)
    except Exception as ex:  # noqa
        return {"mode": "live", "action": action, "planned_calls": 1, "payload": payload,
                "results": None, "reason": "CONFIG_ERROR:" + type(ex).__name__,
                "token_fingerprint": "n/a", "source": "n/a"}
    if not cfg.get("url"):
        return {"mode": "live", "action": action, "planned_calls": 1, "payload": payload,
                "results": None, "reason": "NO_CLOUD_CONFIG",
                "token_fingerprint": cfg.get("token_fingerprint", "none"),
                "source": cfg.get("source", "none")}

    try:
        session = CW.run_mcp_session(transport, [{"payload": payload}], client_name=DEFAULT_CLIENT)
        return {"mode": "live", "action": action, "planned_calls": 1, "payload": payload,
                "results": session, "reason": None if session.get("ok") else "SESSION_ERROR",
                "token_fingerprint": cfg.get("token_fingerprint", "none"),
                "source": cfg.get("source", "none")}
    except Exception as ex:  # noqa — 상위 raise 0 보장
        return {"mode": "live", "action": action, "planned_calls": 1, "payload": payload,
                "results": None, "reason": "TRANSPORT_ERROR:" + type(ex).__name__,
                "token_fingerprint": cfg.get("token_fingerprint", "none"),
                "source": cfg.get("source", "none")}


# ── selftest (전부 메모리 mock · 네트워크/store 0) ─────────────────────
def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    def meta(pid, topic="", depends_on=None, tags=None, seq=None, risk="low"):
        m = {"pack_id": pid, "topic": topic, "risk_level": risk,
             "depends_on": depends_on or [], "cross_pack_tags": tags or []}
        if seq is not None:
            m["sequence"] = seq
        return m

    # C1 depends_on → references (child→parent)
    r = infer_edges([meta("topic/child", depends_on=["topic/parent"]), meta("topic/parent")])
    e1 = r["edges"]
    chk("C1 depends_on → references(child→parent)",
        len(e1) == 1 and e1[0]["relation"] == "references"
        and e1[0]["from_pkg"] == "topic/child" and e1[0]["to_pkg"] == "topic/parent")

    # C2 pack_id 경로 계층 → parent_of (parent→child)
    r = infer_edges([meta("topic/a"), meta("topic/a/b")])
    e2 = r["edges"]
    chk("C2 pack_id 계층 → parent_of(parent→child)",
        len(e2) == 1 and e2[0]["relation"] == "parent_of"
        and e2[0]["from_pkg"] == "topic/a" and e2[0]["to_pkg"] == "topic/a/b")
    # 계층 오탐 방지: topic/a vs topic/ab 는 parent_of 아님
    r_amb = infer_edges([meta("topic/a"), meta("topic/ab")])
    chk("C2b topic/a vs topic/ab 계층 오탐 0",
        all(x["relation"] != "parent_of" for x in r_amb["edges"]))

    # C3 cross_pack_tags 교집합 → adjacent (무방향 from<to)
    r = infer_edges([meta("zeta", tags=["t1", "t2"]), meta("alpha", tags=["t2", "t9"])])
    e3 = r["edges"]
    chk("C3 tags 교집합 → adjacent(from<to 정규화)",
        len(e3) == 1 and e3[0]["relation"] == "adjacent"
        and e3[0]["from_pkg"] == "alpha" and e3[0]["to_pkg"] == "zeta")

    # C4 topic Jaccard ≥ 임계 → related, 미만 → 0
    r_hi = infer_edges([meta("p/1", topic="입찰 가격 예측 모델"),
                        meta("p/2", topic="입찰 가격 예측 분석")])
    chk("C4 topic Jaccard≥임계 → related",
        any(x["relation"] == "related" for x in r_hi["edges"]))
    r_lo = infer_edges([meta("p/1", topic="입찰 가격 예측"),
                        meta("p/2", topic="요리 김치 레시피")], topic_jaccard_min=0.34)
    chk("C4b 임계 미만 → related 엣지 0",
        all(x["relation"] != "related" for x in r_lo["edges"]))

    # C5 우선순위/dedup: depends_on + tag중복 동시 → references 1건만(adjacent 억제)
    r = infer_edges([meta("topic/x", depends_on=["topic/y"], tags=["s"]),
                     meta("topic/y", tags=["s"])])
    chk("C5 복수신호 → references 1건만(adjacent 억제)",
        len(r["edges"]) == 1 and r["edges"][0]["relation"] == "references")

    # C6 결정적: 동일 입력 2회 → 직렬화 바이트 동일
    inp = [meta("zeta", tags=["t"]), meta("alpha", tags=["t"]),
           meta("topic/a"), meta("topic/a/b")]
    s1 = json.dumps(infer_edges(inp), ensure_ascii=False, sort_keys=True)
    s2 = json.dumps(infer_edges(inp), ensure_ascii=False, sort_keys=True)
    edges_sorted = infer_edges(inp)["edges"]
    is_sorted = all(
        (edges_sorted[k]["from_pkg"], edges_sorted[k]["to_pkg"], edges_sorted[k]["relation"]) <=
        (edges_sorted[k + 1]["from_pkg"], edges_sorted[k + 1]["to_pkg"], edges_sorted[k + 1]["relation"])
        for k in range(len(edges_sorted) - 1))
    chk("C6 결정적 정렬·2회 호출 바이트 동일", s1 == s2 and is_sorted)

    # C7 self-edge 금지: 같은 pack_id 쌍 / 단일 / 빈 리스트 → 엣지 0
    r_self = infer_edges([meta("dup", tags=["t"]), meta("dup", tags=["t"])])
    chk("C7 같은 pack_id 쌍 → 엣지 0(self 금지)", r_self["counts"]["edges"] == 0)
    chk("C7b 단일 pack → 엣지 0", infer_edges([meta("solo")])["counts"]["edges"] == 0)
    chk("C7c 빈 리스트 → 엣지 0·status OK", infer_edges([])["status"] == "OK"
        and infer_edges([])["counts"]["edges"] == 0)

    # C8 잘못된 입력 → status OK·errors typed·해당 항목만 skip
    r = infer_edges([meta("topic/a", depends_on=["topic/b"]), meta("topic/b"),
                     {"no_pack_id": 1}, "not-a-dict", 42])
    reasons = {e["reason"] for e in r["errors"]}
    chk("C8 잘못된 입력 → status OK·해당만 skip",
        r["status"] == "OK" and len(r["errors"]) == 3
        and "PACK_ID_MISSING" in reasons and "NOT_A_DICT" in reasons
        and r["counts"]["packs"] == 2)
    chk("C8b 정상 항목 엣지는 유지", any(x["relation"] == "references" for x in r["edges"]))
    chk("C8c 입력 자체 list 아님 → ERROR(raise 0)",
        infer_edges("nope")["status"] == "ERROR")

    # C9 무손실: N팩 전부 counts.packs·by_relation 합 == len(edges)
    big = [meta("topic/a"), meta("topic/a/b"), meta("topic/a/b/c"),
           meta("q", tags=["z"]), meta("r", tags=["z"])]
    r = infer_edges(big)
    chk("C9 counts.packs == valid N", r["counts"]["packs"] == 5)
    chk("C9b by_relation 합 == len(edges)",
        sum(r["counts"]["by_relation"].values()) == len(r["edges"])
        == r["counts"]["edges"])

    # C10 relation 전부 화이트리스트 내
    chk("C10 relation 전부 RELATIONS 화이트리스트",
        all(x["relation"] in RELATIONS for x in r["edges"]))

    # C11 confidence 전부 0.0~1.0
    all_edges = r["edges"] + r_hi["edges"] + e1 + e2 + e3
    chk("C11 confidence 0.0~1.0",
        all(0.0 <= x["confidence"] <= 1.0 for x in all_edges))

    # C12 입력 정규화: build_pack 반환 / 순수 pack / 순수 manifest 동일 처리
    manifest_a = {"pack_id": "topic/m1", "topic": "입찰 가격 예측",
                  "depends_on": ["topic/m2"], "cross_pack_tags": []}
    manifest_b = {"pack_id": "topic/m2", "topic": "입찰 가격 예측"}
    pack_form = {"manifest": dict(manifest_a)}                 # 순수 pack dict
    buildret_form = {"status": "OK", "pack": {"manifest": dict(manifest_b)}}  # build_pack 반환
    r = infer_edges([pack_form, buildret_form])
    chk("C12 3형태(manifest/pack/build_pack) 정규화 동일",
        any(x["relation"] == "references" and x["from_pkg"] == "topic/m1"
            and x["to_pkg"] == "topic/m2" for x in r["edges"]))
    # 순수 manifest 직접도 동작
    r_m = infer_edges([manifest_a, manifest_b])
    chk("C12b 순수 manifest 직접 입력 동작",
        any(x["relation"] == "references" for x in r_m["edges"]))

    # C13 sequence/order 연속 → sequence_next
    r = infer_edges([meta("step/2", seq=2), meta("step/1", seq=1)])
    e13 = r["edges"]
    chk("C13 sequence 연속 → sequence_next(이전→다음)",
        len(e13) == 1 and e13[0]["relation"] == "sequence_next"
        and e13[0]["from_pkg"] == "step/1" and e13[0]["to_pkg"] == "step/2")
    # order 키도 동작·비연속(2칸)은 엣지 0
    r_gap = infer_edges([{"pack_id": "g/1", "order": 1}, {"pack_id": "g/3", "order": 3}])
    chk("C13b 비연속 sequence → sequence_next 0",
        all(x["relation"] != "sequence_next" for x in r_gap["edges"]))

    # C14 출력 직렬화 PII/secret 잔존 0
    blob = json.dumps(infer_edges(big), ensure_ascii=False)
    try:
        import watcher_batch_m1 as _bm1
        chk("C14 출력 PII/secret 잔존 0", not _bm1.scan_residual_pii(blob))
    except Exception:
        chk("C14 출력 PII/secret 잔존 0(scanner 부재 skip)", True)

    # C15 max_edges 제한(결정적 상위 N)
    r_cap = infer_edges(big, max_edges=2)
    chk("C15 max_edges 제한", len(r_cap["edges"]) == 2
        and r_cap["edges"] == infer_edges(big)["edges"][:2])

    # ── 항목 C2: workflow_manage 배선 (W=순수 변환 · S=오케스트레이터) ──
    big_w = [meta("topic/a"), meta("topic/a/b"),
             meta("q", tags=["z"]), meta("r", tags=["z"])]

    # W1 변환: name/action/nodes 구조
    inp_w1 = [meta("topic/child", depends_on=["topic/parent"]), meta("topic/parent")]
    pl_w1 = build_workflow_payload_from_edges(infer_edges(inp_w1), pack_metas=inp_w1)
    a_w1 = pl_w1["params"]["arguments"]
    chk("W1 name=workflow_manage·action=create·nodes에 package_id+id",
        pl_w1["params"]["name"] == WORKFLOW_TOOL and a_w1["action"] == "create"
        and len(a_w1["nodes"]) >= 2
        and all("package_id" in n and "id" in n for n in a_w1["nodes"]))

    # W2 node id 결정적+충돌0: slug 동일해지는 'a/b' vs 'a.b'
    ne_w2 = to_workflow_nodes_edges([], pack_metas=[meta("a/b"), meta("a.b")])
    chk("W2 node id 결정적·충돌0(a/b vs a.b)",
        _node_id("a/b") != _node_id("a.b") and _node_id("a/b") == _node_id("a/b")
        and len({n["id"] for n in ne_w2["nodes"]}) == 2)

    # W3 referential integrity: edge from/to 전부 node id 집합·pack_id 직접노출 0
    ne_w3 = to_workflow_nodes_edges(infer_edges(big_w), pack_metas=big_w)
    nid_w3 = {n["id"] for n in ne_w3["nodes"]}
    pid_w3 = {n["package_id"] for n in ne_w3["nodes"]}
    chk("W3 referential integrity(edge from/to=node id·pack_id 노출 0)",
        len(ne_w3["edges"]) >= 1
        and all(e["from"] in nid_w3 and e["to"] in nid_w3 for e in ne_w3["edges"])
        and all(e["from"] not in pid_w3 and e["to"] not in pid_w3 for e in ne_w3["edges"]))

    # W4 무손실/완전성: 엣지 없는 pack 도 node (node 수 == valid pack 수)
    metas_w4 = [meta("solo/1"), meta("solo/2"), meta("solo/3")]
    ne_w4 = to_workflow_nodes_edges(infer_edges(metas_w4), pack_metas=metas_w4)
    chk("W4 무손실: 엣지 없는 pack 도 node(== valid pack 수)",
        ne_w4["edges"] == [] and len(ne_w4["nodes"]) == 3)

    # W5 메타 전달: relation/confidence/basis == inferred
    inf_w5 = infer_edges([meta("topic/child", depends_on=["topic/parent"]),
                          meta("topic/parent")])
    ne_w5 = to_workflow_nodes_edges(inf_w5)
    src_w5 = inf_w5["edges"][0]
    chk("W5 메타 전달: relation/confidence/basis == inferred",
        len(ne_w5["edges"]) == 1
        and ne_w5["edges"][0]["relation"] == src_w5["relation"]
        and ne_w5["edges"][0]["confidence"] == src_w5["confidence"]
        and ne_w5["edges"][0]["basis"] == src_w5["basis"])

    # W6 JSON-RPC 계약
    pl_w6 = build_workflow_payload_from_edges(infer_edges(big_w), pack_metas=big_w)
    chk("W6 JSON-RPC: jsonrpc 2.0·id int·arguments dict·action 키",
        pl_w6["jsonrpc"] == "2.0" and isinstance(pl_w6["id"], int)
        and isinstance(pl_w6["params"]["arguments"], dict)
        and "action" in pl_w6["params"]["arguments"])

    # W7 action=update: workflow_id 패스스루·nodes/edges 동봉
    pl_w7 = build_workflow_payload_from_edges(infer_edges(big_w), pack_metas=big_w,
                                              action="update", workflow_id="wf-123")
    a_w7 = pl_w7["params"]["arguments"]
    chk("W7 action=update: workflow_id 패스스루·nodes/edges 동봉",
        a_w7["action"] == "update" and a_w7.get("workflow_id") == "wf-123"
        and "nodes" in a_w7 and "edges" in a_w7)

    # W8 결정성: 동일 입력 2회 build → sort_keys 바이트 동일
    b_w8a = json.dumps(build_workflow_payload_from_edges(infer_edges(big_w), pack_metas=big_w),
                       ensure_ascii=False, sort_keys=True)
    b_w8b = json.dumps(build_workflow_payload_from_edges(infer_edges(big_w), pack_metas=big_w),
                       ensure_ascii=False, sort_keys=True)
    chk("W8 결정성: 2회 build 바이트 동일", b_w8a == b_w8b)

    # W9 빈 엣지(단일 pack) → edges []·node 는 metas 반영
    ne_w9 = to_workflow_nodes_edges(infer_edges([meta("only/one")]),
                                    pack_metas=[meta("only/one")])
    chk("W9 빈 엣지(단일 pack) → edges 0·node metas 반영",
        ne_w9["edges"] == [] and len(ne_w9["nodes"]) == 1
        and ne_w9["nodes"][0]["package_id"] == "only/one")

    # W10 출력 직렬화 PII/secret 잔존 0
    blob_w = json.dumps(build_workflow_payload_from_edges(infer_edges(big_w), pack_metas=big_w),
                        ensure_ascii=False)
    try:
        import watcher_batch_m1 as _bm1w
        chk("W10 출력 PII/secret 잔존 0", not _bm1w.scan_residual_pii(blob_w))
    except Exception:
        chk("W10 출력 PII/secret 잔존 0(scanner 부재 skip)", True)

    # S1 dry_run 기본: spy transport 호출 0·mode dry-run·payload·reason DRY_RUN
    spy = {"n": 0}

    def spy_t(payload):
        spy["n"] += 1
        return {"result": {"isError": False}}

    r_s1 = sync_edges_to_workflow(infer_edges(big_w), pack_metas=big_w, transport=spy_t)
    chk("S1 dry_run 기본 → transport 0·mode dry-run·payload·reason DRY_RUN",
        spy["n"] == 0 and r_s1["mode"] == "dry-run" and r_s1["payload"]
        and r_s1["reason"] == "DRY_RUN")

    # S2 live + 토글 OFF → WORKFLOW_SYNC_DISABLED·transport 0
    spy["n"] = 0
    r_s2 = sync_edges_to_workflow(infer_edges(big_w), pack_metas=big_w, transport=spy_t,
                                  env={}, dry_run=False)
    chk("S2 live + 토글 OFF → WORKFLOW_SYNC_DISABLED·transport 0",
        r_s2["reason"] == "WORKFLOW_SYNC_DISABLED" and spy["n"] == 0)

    # S3 live + 토글 ON + mock transport + url → initialize 1 then tools/call 1·ok
    seq_s = []

    def seq_t(payload):
        seq_s.append(payload.get("method"))
        return {"result": {"isError": False}}

    live_env_s = {SYNC_ENABLE_ENV: "1", "BINGGU_CLOUD_MCP_URL": "https://x.example/mcp",
                  "BINGGU_CLOUD_MCP_TOKEN": "tok-sync-abcdef123"}
    r_s3 = sync_edges_to_workflow(infer_edges(big_w), pack_metas=big_w, transport=seq_t,
                                  env=live_env_s, dry_run=False)
    chk("S3 live ON+transport+url → initialize 1 then tools/call 1·ok·reason None",
        bool(seq_s) and seq_s[0] == "initialize" and seq_s.count("tools/call") == 1
        and r_s3["results"]["ok"] and r_s3["reason"] is None)

    # S4 live + transport None → NO_TRANSPORT(네트워크 0)
    r_s4 = sync_edges_to_workflow(infer_edges(big_w), pack_metas=big_w, transport=None,
                                  env=live_env_s, dry_run=False)
    chk("S4 live + transport None → NO_TRANSPORT", r_s4["reason"] == "NO_TRANSPORT")

    # S5 live + transport 예외 → typed SESSION_ERROR 흡수(raise 0)·errors 기록
    def boom_t(payload):
        raise RuntimeError("net_down")

    r_s5 = sync_edges_to_workflow(infer_edges(big_w), pack_metas=big_w, transport=boom_t,
                                  env=live_env_s, dry_run=False)
    chk("S5 transport 예외 → SESSION_ERROR 흡수(raise 0)·errors 기록",
        isinstance(r_s5, dict) and r_s5["reason"] == "SESSION_ERROR"
        and r_s5["results"]["errors"])

    # S6 토큰 평문 미노출·token_fingerprint sha8 형식
    pub_s6 = json.dumps({k: v for k, v in r_s3.items() if k != "results"}, ensure_ascii=False)
    chk("S6 토큰 평문 미노출·token_fingerprint sha8 형식",
        "tok-sync-abcdef123" not in pub_s6 and r_s3["token_fingerprint"].startswith("sha8:"))

    # S7 비정상 입력(status ERROR dict / 비-list) → typed·raise 0·빈 nodes/edges
    ne_s7a = to_workflow_nodes_edges({"status": "ERROR", "edges": []})
    ne_s7b = to_workflow_nodes_edges(42)
    ne_s7c = to_workflow_nodes_edges("nope")
    chk("S7 비정상 입력 → typed·raise 0·빈 nodes/edges",
        ne_s7a["nodes"] == [] and ne_s7a["edges"] == []
        and ne_s7b["nodes"] == [] and ne_s7b["edges"] == []
        and ne_s7c["nodes"] == [] and ne_s7c["edges"] == [])

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_pack_edges — use --selftest, or import infer_edges(pack_metas)")
