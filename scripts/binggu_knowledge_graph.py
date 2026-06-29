"""binggu_knowledge_graph — 항목 B: explore 그래프 → OpenCrab workflow 페이로드 + 노드별 수집 훅.

binggu_branch_explorer.explore() 가 만든 1주제 재귀 분기 지식그래프(graph{nodes,edges,...})를
받아서:
  (1) OpenCrab opencrab_workflow_manage 페이로드로 변환 — 분기 트리를 워크플로우 그래프로.
  (2) (옵션·기본 OFF) 선택 노드를 binggu_local_collect.collect 로 실제 수집(비용 큼·owner 토글).
  (3) graph 직렬화(json) + 통계.

★ 설계 철학(사장님 지침 — 고정 금지·LLM 유동):
  - 분기 의미·관련성·가지치기는 전부 explore 단계의 LLM(transport) 이 이미 결정함.
  - 본 모듈은 **메커니즘만**: 그래프 자료구조 변환·node id 매핑·source/target 엣지 조립·
    selector(leaf/all/top_relevance)·직렬화·통계. 주제별 분기 리스트/고정 키워드 하드코딩 0.
  - category 는 구조적 역할(root/branch/leaf — 그래프 구조에서 파생)일 뿐 도메인 분류 아님.

★ OpenCrab workflow_manage 계약(빙구팩 박제 — 반드시 준수):
  - workflow edges 키는 from/to 가 **아니라 source/target**.
  - create 는 edges 를 **무시**(노드만 생성) → edges 는 **update 에서 반영**.
    그래서 분기 트리를 그대로 올리려면 create(노드) → update(노드+엣지) 2단계 plan 이 필요.
  - relation='branches_to' (부모→자식 분기 방향).

★ kind 구분: package_id 없는 순수 개념노드(explore 산출의 기본) = kind 'concept'.
  노드가 package_id 를 가지면(예: 수집되어 pack 으로 승급된 노드) kind 'package'.

진입점:
  - graph_to_workflow_nodes_edges(graph) -> {nodes, edges, node_map, counts}
  - build_workflow_payload(graph, action='create'|'update', ...) -> JSON-RPC payload(dict)
  - build_workflow_sync_plan(graph, ...) -> [create payload, update payload]  (박제: edges는 update)
  - collect_nodes(graph, selector='leaf', enabled=False, ...) -> {selected, executed, collections}
  - serialize_graph(graph) -> {json, stats, node_count, edge_count}

local_collect 는 import 만(실행은 collect_nodes(enabled=True) 옵션). topic_to_pack 미접촉.
실 네트워크 0 — collect 실행은 호출자가 provider/fetch_runner 를 명시 주입할 때만(selftest 는 mock).
"""
import os
import sys
import json
import hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 항목 C 수집 경로 — import 만(실행은 collect_nodes(enabled=True) 옵션·기본 OFF).
# import 부작용 0 보존: 실패해도 모듈 로드는 계속(수집 미가용으로 degrade).
try:
    import binggu_local_collect as LOCAL_COLLECT  # noqa: E402
except Exception:  # noqa — 의존 체인 부재 시에도 그래프 변환/직렬화는 동작
    LOCAL_COLLECT = None

WORKFLOW_TOOL = "opencrab_workflow_manage"
BRANCH_RELATION = "branches_to"          # 부모→자식 분기 방향(고정 메커니즘 — 도메인 의미 아님)
DEFAULT_WORKFLOW_STATUS = "draft"


# ── node id (결정적·ascii-safe·원문 미변형) ───────────────────────────────
def _node_id(graph_node_id, label):
    """explore 그래프 노드 → 결정적 workflow node id. label slug + sha1 접미사(충돌 0).

    원문 라벨/id 는 절대 변형하지 않음(별 필드 title/package_id 로 보존). 순수 문자열 메커니즘."""
    import re
    base = "%s|%s" % (str(graph_node_id), str(label))
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", str(label)).strip("_")
    if len(slug) > 40:
        slug = slug[:40].strip("_")
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
    return (slug + "_" + h) if slug else ("node_" + h)


def _child_ids(graph):
    """그래프 엣지에서 '자식을 가진(=내부) 노드 id' 집합(구조적 leaf 판정용)."""
    parents = set()
    for e in graph.get("edges", []) or []:
        frm = e.get("from")
        if frm is not None:
            parents.add(frm)
    return parents


def _category(node, has_children):
    """구조적 역할 — 그래프 구조에서만 파생(도메인 분류 아님).
    depth 0 = root / 자식 있음 = branch / 자식 없음 = leaf."""
    if node.get("depth", 0) == 0:
        return "root"
    return "branch" if has_children else "leaf"


# ── 그래프 → workflow nodes/edges (source/target) ─────────────────────────
def graph_to_workflow_nodes_edges(graph):
    """explore graph{nodes,edges} → workflow nodes/edges (referential integrity·결정적·raise 0).

    nodes : {id, title, label, category, depth, kind, relevance, parent, (package_id)}
      - kind='concept'(package_id 없음·explore 기본) / 'package'(package_id 보유 노드).
      - category=root/branch/leaf (구조적).
    edges : {source, target, relation}   ※ 빙구팩 박제: from/to 아니라 source/target.
      - relation='branches_to' (부모→자식). graph edges(from/to)를 그대로 방향 보존 변환.

    반환: {nodes, edges, node_map:{graph_id:workflow_id}, counts:{nodes,edges}}.
    """
    if not isinstance(graph, dict):
        return {"nodes": [], "edges": [], "node_map": {}, "counts": {"nodes": 0, "edges": 0}}

    g_nodes = graph.get("nodes", []) or []
    g_edges = graph.get("edges", []) or []

    # graph node id → workflow node id (정렬 불필요·explore 삽입순 보존 = 결정적)
    node_map = {}
    by_gid = {}
    for n in g_nodes:
        if not isinstance(n, dict):
            continue
        gid = n.get("id")
        if gid is None or gid in node_map:
            continue
        node_map[gid] = _node_id(gid, n.get("label", ""))
        by_gid[gid] = n

    parents = _child_ids(graph)

    nodes = []
    for gid, n in by_gid.items():
        pkg = n.get("package_id")
        kind = "package" if pkg else "concept"
        parent_gid = n.get("parent_id")
        node = {
            "id": node_map[gid],
            "title": n.get("label", ""),
            "label": n.get("label", ""),
            "category": _category(n, gid in parents),
            "depth": n.get("depth", 0),
            "kind": kind,
            "relevance": n.get("relevance"),
            "parent": node_map.get(parent_gid),  # None for root
        }
        if pkg:
            node["package_id"] = pkg
        nodes.append(node)

    edges = []
    for e in g_edges:
        if not isinstance(e, dict):
            continue
        frm, to = e.get("from"), e.get("to")
        if frm not in node_map or to not in node_map:   # referential integrity
            continue
        edges.append({"source": node_map[frm], "target": node_map[to],
                      "relation": BRANCH_RELATION})

    return {"nodes": nodes, "edges": edges, "node_map": node_map,
            "counts": {"nodes": len(nodes), "edges": len(edges)}}


# ── workflow_manage JSON-RPC 페이로드 ─────────────────────────────────────
def build_workflow_payload(graph, *, action="create", workflow_name=None, workflow_id=None,
                           description=None, status=DEFAULT_WORKFLOW_STATUS, rpc_id=1,
                           include_edges=None):
    """explore graph → opencrab_workflow_manage tools/call JSON-RPC payload (순수·raise 0).

    빙구팩 박제 준수:
      - edges 키는 source/target.
      - create 는 edges 무시(서버) → 기본 include_edges: action!='create' 일 때만 True.
        (명시적으로 include_edges 를 주면 그 값을 따름.)
    create면 arguments.name=workflow_name. update면 workflow_id/workflow_name 패스스루.
    """
    ne = graph_to_workflow_nodes_edges(graph)
    if include_edges is None:
        # 박제: create 는 edges 무시 → 기본은 update 계열에서만 edges 동봉
        include_edges = (action != "create")
    args = {"action": action, "nodes": ne["nodes"]}
    args["edges"] = ne["edges"] if include_edges else []
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


def build_workflow_sync_plan(graph, *, workflow_name=None, workflow_id=None,
                             description=None, status=DEFAULT_WORKFLOW_STATUS):
    """분기 트리를 OpenCrab 에 그대로 올리는 2단계 plan (빙구팩 박제: edges 는 update 에서).

    반환: [create_payload, update_payload].
      - create_payload: 노드만(edges=[]·서버가 create 시 edges 무시하는 계약 반영).
      - update_payload: 노드+엣지(source/target·branches_to) — 실제 분기 연결은 여기서 반영.
    workflow_id 없으면 update 페이로드는 'workflow_id': None(호출자가 create 응답 id 주입).
    순수·raise 0(네트워크 0 — 페이로드만).
    """
    create = build_workflow_payload(
        graph, action="create", workflow_name=workflow_name, description=description,
        status=status, rpc_id=1, include_edges=False)
    update = build_workflow_payload(
        graph, action="update", workflow_id=workflow_id, workflow_name=workflow_name,
        description=description, status=status, rpc_id=2, include_edges=True)
    return [create, update]


# ── 노드 selector (구조적 메커니즘) ───────────────────────────────────────
def select_nodes(graph, selector="leaf", top_k=10, include_root=False):
    """수집 대상 노드 선택 — 구조적 selector(도메인 의미 아님). raise 0.

      leaf           : 자식 없는 노드(트리 말단 — 가장 구체적 주제).
      all            : 전체 노드.
      top_relevance  : relevance 내림차순 top_k.
    include_root=False(기본) → root(depth 0)는 제외(수집 대상 아님·너무 광범위).
    반환: [{id, label, depth, relevance}, ...] (graph node id 기준).
    """
    g_nodes = [n for n in (graph.get("nodes", []) or []) if isinstance(n, dict)]
    if not include_root:
        g_nodes = [n for n in g_nodes if n.get("depth", 0) != 0]

    sel = str(selector or "leaf")
    if sel == "all":
        picked = g_nodes
    elif sel == "top_relevance":
        picked = sorted(g_nodes, key=lambda n: (n.get("relevance") or 0.0), reverse=True)
        try:
            k = int(top_k)
        except (TypeError, ValueError):
            k = 10
        picked = picked[:max(0, k)]
    else:  # leaf (기본)
        parents = _child_ids(graph)
        picked = [n for n in g_nodes if n.get("id") not in parents]

    return [{"id": n.get("id"), "label": n.get("label", ""),
             "depth": n.get("depth", 0), "relevance": n.get("relevance")}
            for n in picked]


# ── 노드별 수집 훅 (옵션·기본 OFF·비용 큼) ────────────────────────────────
def collect_nodes(graph, selector="leaf", *, enabled=False, llm_transport=None,
                  provider=None, fetch_runner=None, home=None, out_dir=None,
                  top_k=10, include_root=False, collect_fn=None, max_nodes=None,
                  **collect_kwargs):
    """선택 노드(label)를 binggu_local_collect.collect 로 수집 — **기본 OFF**(enabled=False).

    기본(enabled=False): 그래프 구조만 — collect 미실행. selected 목록 + executed=False 반환.
      (비용 큼: aspect 별 discover/harvest/pack. owner 가 명시 토글해야만 실행.)
    enabled=True: 각 선택 노드 label 을 collect 의 topic 으로 전달해 수집.
      실 네트워크는 provider/fetch_runner 를 명시 주입할 때만(미주입이면 collect 내부에서
      provider=None → 발견 0 으로 degrade). selftest 는 collect_fn(mock) 주입으로 네트워크 0.

    collect_fn: 테스트/대체용 주입(기본 = binggu_local_collect.collect). 시그니처
      collect_fn(topic, llm_transport=, provider=, fetch_runner=, home=, out_dir=, **kw).
    max_nodes: 수집 실행 상한(비용 안전 캡). None=제한 없음.

    반환: {enabled, selector, selected:[{id,label,...}], executed:bool,
           collections:[{node_id, label, result|error}], skipped_reason?}.
    raise 0 — 노드별 collect 예외는 흡수(해당 노드 error 기록·나머지 진행).
    """
    selected = select_nodes(graph, selector=selector, top_k=top_k, include_root=include_root)

    if not enabled:
        # 기본 경로 — 구조만. collect 절대 미호출(비용 0·네트워크 0).
        return {"enabled": False, "selector": selector, "selected": selected,
                "executed": False, "collections": [],
                "skipped_reason": "COLLECT_DISABLED (옵션·owner 명시 enable 필요)"}

    fn = collect_fn or (LOCAL_COLLECT.collect if LOCAL_COLLECT is not None else None)
    if fn is None:
        return {"enabled": True, "selector": selector, "selected": selected,
                "executed": False, "collections": [],
                "skipped_reason": "LOCAL_COLLECT_UNAVAILABLE (import 실패)"}

    targets = selected
    if max_nodes is not None:
        try:
            targets = selected[:max(0, int(max_nodes))]
        except (TypeError, ValueError):
            targets = selected

    collections = []
    for s in targets:
        label = s.get("label") or ""
        if not str(label).strip():
            continue
        # 노드별 격리 out_dir(섞임 방지)
        node_out = os.path.join(out_dir, _node_id(s.get("id"), label)) if out_dir else None
        try:
            res = fn(label, llm_transport=llm_transport, provider=provider,
                     fetch_runner=fetch_runner, home=home, out_dir=node_out, **collect_kwargs)
            collections.append({"node_id": s.get("id"), "label": label, "result": res})
        except Exception as ex:  # noqa — 노드 1개 실패가 전체를 막지 않음
            collections.append({"node_id": s.get("id"), "label": label,
                                "error": type(ex).__name__ + ":" + str(ex)[:80]})

    return {"enabled": True, "selector": selector, "selected": selected,
            "executed": True, "collections": collections,
            "collected_count": sum(1 for c in collections if "result" in c)}


# ── 직렬화 + 통계 ─────────────────────────────────────────────────────────
def graph_stats(graph):
    """그래프 + workflow 변환 통계(순수·결정적). raise 0."""
    g_nodes = [n for n in (graph.get("nodes", []) or []) if isinstance(n, dict)]
    g_edges = [e for e in (graph.get("edges", []) or []) if isinstance(e, dict)]
    parents = _child_ids(graph)
    by_depth = {}
    by_kind = {"concept": 0, "package": 0}
    leaf = 0
    for n in g_nodes:
        d = n.get("depth", 0)
        by_depth[d] = by_depth.get(d, 0) + 1
        by_kind["package" if n.get("package_id") else "concept"] += 1
        if n.get("id") not in parents and d != 0:
            leaf += 1
    return {
        "nodes": len(g_nodes), "edges": len(g_edges),
        "by_depth": by_depth, "by_kind": by_kind, "leaf_nodes": leaf,
        "max_depth": max(by_depth.keys()) if by_depth else 0,
        # explore 원본 stats 동봉(있으면)
        "explore_stats": graph.get("stats", {}),
        "pruned_count": len(graph.get("pruned", []) or []),
    }


def serialize_graph(graph, *, indent=None):
    """그래프 직렬화(json 문자열) + 통계. 네트워크 0·파일쓰기 0(순수). raise 0.

    반환: {json, stats, node_count, edge_count}. json 은 ensure_ascii=False(한글 보존).
    """
    stats = graph_stats(graph)
    try:
        js = json.dumps(graph, ensure_ascii=False, indent=indent, default=str)
    except Exception:
        js = json.dumps({"error": "serialize_failed"}, ensure_ascii=False)
    return {"json": js, "stats": stats,
            "node_count": stats["nodes"], "edge_count": stats["edges"]}


# ══════════════════════════════════════════════════════════════════════════
# selftest — mock graph·실 네트워크 0·collect 훅 OFF 검증·source/target 검증
# ══════════════════════════════════════════════════════════════════════════
def _mock_graph():
    """explore 출력 형태의 결정적 mock 그래프(신혼여행팩 → 분기 트리)."""
    nodes = [
        {"id": "n0", "label": "신혼여행팩", "depth": 0, "parent_id": None, "relevance": 1.0},
        {"id": "n1", "label": "패키지구성", "depth": 1, "parent_id": "n0", "relevance": 0.92},
        {"id": "n2", "label": "이동수단", "depth": 1, "parent_id": "n0", "relevance": 0.81},
        {"id": "n3", "label": "항공", "depth": 2, "parent_id": "n1", "relevance": 0.77},
        {"id": "n4", "label": "숙박", "depth": 2, "parent_id": "n1", "relevance": 0.74},
        {"id": "n5", "label": "렌터카", "depth": 2, "parent_id": "n2", "relevance": 0.66},
    ]
    edges = [
        {"from": "n0", "to": "n1"}, {"from": "n0", "to": "n2"},
        {"from": "n1", "to": "n3"}, {"from": "n1", "to": "n4"},
        {"from": "n2", "to": "n5"},
    ]
    return {"nodes": nodes, "edges": edges, "pruned": [{"label": "x", "reason": "low_relevance"}],
            "stats": {"total": 6, "by_depth": {0: 1, 1: 2, 2: 3}, "pruned_count": 1,
                      "llm_calls": 3, "max_depth_reached": 2, "budget_hit": False}}


def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    g = _mock_graph()

    # ── K1: nodes/edges 변환 기본 ──
    ne = graph_to_workflow_nodes_edges(g)
    chk("K1a 노드 6개 변환", ne["counts"]["nodes"] == 6)
    chk("K1b 엣지 5개 변환", ne["counts"]["edges"] == 5)
    chk("K1c node_map referential integrity(모든 엣지 endpoint 가 node)",
        all(e["source"] in {n["id"] for n in ne["nodes"]} and
            e["target"] in {n["id"] for n in ne["nodes"]} for e in ne["edges"]))

    # ── K2: 빙구팩 박제 — edges 키는 source/target (from/to 아님) ──
    chk("K2a 엣지 키 source/target", all({"source", "target"} <= set(e) for e in ne["edges"]))
    chk("K2b 엣지에 from/to 키 없음(박제)",
        all("from" not in e and "to" not in e for e in ne["edges"]))
    chk("K2c relation='branches_to'", all(e["relation"] == "branches_to" for e in ne["edges"]))
    # 방향 보존: n0→n1 (부모→자식)
    nm = ne["node_map"]
    chk("K2d 방향 보존(부모→자식)",
        any(e["source"] == nm["n0"] and e["target"] == nm["n1"] for e in ne["edges"]))

    # ── K3: kind 구분 — 개념노드(package_id 없음)=concept ──
    chk("K3a 전부 concept(explore 기본·package_id 없음)",
        all(n["kind"] == "concept" for n in ne["nodes"]))
    chk("K3b concept 노드엔 package_id 키 없음",
        all("package_id" not in n for n in ne["nodes"]))
    # package_id 있는 노드는 kind='package'
    g_pkg = _mock_graph()
    g_pkg["nodes"][3]["package_id"] = "topic/항공-pack"
    ne_pkg = graph_to_workflow_nodes_edges(g_pkg)
    pkg_node = next(n for n in ne_pkg["nodes"] if n.get("package_id") == "topic/항공-pack")
    chk("K3c package_id 노드 → kind='package'", pkg_node["kind"] == "package")

    # ── K4: category 구조적(root/branch/leaf) ──
    cat = {n["title"]: n["category"] for n in ne["nodes"]}
    chk("K4a root(depth0)=root", cat["신혼여행팩"] == "root")
    chk("K4b 자식 있는 노드=branch", cat["패키지구성"] == "branch" and cat["이동수단"] == "branch")
    chk("K4c 말단 노드=leaf", cat["항공"] == "leaf" and cat["숙박"] == "leaf" and cat["렌터카"] == "leaf")

    # ── K5: node 필수 필드(title/category/depth/kind) ──
    chk("K5 노드 필드 title/category/depth/kind/relevance",
        all({"title", "category", "depth", "kind", "relevance"} <= set(n) for n in ne["nodes"]))

    # ── K6: workflow_manage 페이로드 구조 ──
    p_create = build_workflow_payload(g, action="create", workflow_name="신혼여행 탐색")
    chk("K6a JSON-RPC 봉투", p_create["jsonrpc"] == "2.0" and p_create["method"] == "tools/call")
    chk("K6b tool name=opencrab_workflow_manage",
        p_create["params"]["name"] == "opencrab_workflow_manage")
    chk("K6c create action + name", p_create["params"]["arguments"]["action"] == "create"
        and p_create["params"]["arguments"]["name"] == "신혼여행 탐색")
    chk("K6d create 는 edges 무시(빈 배열·박제)",
        p_create["params"]["arguments"]["edges"] == [])
    chk("K6e create 도 노드는 포함", len(p_create["params"]["arguments"]["nodes"]) == 6)

    # ── K7: update 페이로드는 edges 반영(source/target) ──
    p_update = build_workflow_payload(g, action="update", workflow_id="wf-123")
    chk("K7a update action + workflow_id",
        p_update["params"]["arguments"]["action"] == "update"
        and p_update["params"]["arguments"]["workflow_id"] == "wf-123")
    chk("K7b update 는 edges 반영(5개)", len(p_update["params"]["arguments"]["edges"]) == 5)
    chk("K7c update edges 도 source/target",
        all({"source", "target"} <= set(e) for e in p_update["params"]["arguments"]["edges"]))

    # ── K8: sync plan (박제: create→update 2단계, edges는 update) ──
    plan = build_workflow_sync_plan(g, workflow_name="발리팩")
    chk("K8a plan 2단계(create→update)",
        len(plan) == 2 and plan[0]["params"]["arguments"]["action"] == "create"
        and plan[1]["params"]["arguments"]["action"] == "update")
    chk("K8b plan[0](create) edges 비움", plan[0]["params"]["arguments"]["edges"] == [])
    chk("K8c plan[1](update) edges 반영", len(plan[1]["params"]["arguments"]["edges"]) == 5)
    chk("K8d rpc_id 분리(1,2)", plan[0]["id"] == 1 and plan[1]["id"] == 2)

    # ── K9: selector ──
    leaf = select_nodes(g, "leaf")
    chk("K9a leaf selector — 말단 3개(항공/숙박/렌터카)",
        {s["label"] for s in leaf} == {"항공", "숙박", "렌터카"})
    alln = select_nodes(g, "all")
    chk("K9b all selector — root 제외 5개", len(alln) == 5)
    alln_r = select_nodes(g, "all", include_root=True)
    chk("K9c all+include_root — 6개", len(alln_r) == 6)
    topr = select_nodes(g, "top_relevance", top_k=2)
    chk("K9d top_relevance top_k=2 — 최고 relevance(패키지구성/이동수단)",
        {s["label"] for s in topr} == {"패키지구성", "이동수단"})

    # ── K10: collect_nodes 기본 OFF → 미실행(핵심 안전) ──
    called = {"n": 0}

    def _mock_collect(topic, **kw):
        called["n"] += 1
        return {"status": "OK", "topic": topic, "aspects": []}

    off = collect_nodes(g, "leaf", collect_fn=_mock_collect)  # enabled 기본 False
    chk("K10a 기본 OFF — executed=False", off["executed"] is False)
    chk("K10b 기본 OFF — collect 호출 0(비용 0)", called["n"] == 0)
    chk("K10c 기본 OFF — collections 빈 배열", off["collections"] == [])
    chk("K10d 기본 OFF — selected 는 채워짐(구조만)", len(off["selected"]) == 3)
    chk("K10e 기본 OFF — skipped_reason 표기", "DISABLED" in off.get("skipped_reason", ""))

    # ── K11: collect_nodes enabled=True → collect_fn 호출(mock·네트워크 0) ──
    called["n"] = 0
    on = collect_nodes(g, "leaf", enabled=True, collect_fn=_mock_collect)
    chk("K11a enabled — executed=True", on["executed"] is True)
    chk("K11b enabled — leaf 3개 수집 호출", called["n"] == 3)
    chk("K11c enabled — collections 3개 result", len(on["collections"]) == 3
        and all("result" in c for c in on["collections"]))
    chk("K11d enabled — collected_count=3", on.get("collected_count") == 3)
    # 노드별 label 이 collect topic 으로 전달됐는지
    topics = {c["result"]["topic"] for c in on["collections"]}
    chk("K11e 노드 label 이 collect topic 으로 전달", topics == {"항공", "숙박", "렌터카"})

    # ── K12: collect 예외 흡수(노드 1개 실패가 전체 막지 않음·raise 0) ──
    def _boom_collect(topic, **kw):
        if topic == "숙박":
            raise RuntimeError("collect down")
        return {"status": "OK", "topic": topic, "aspects": []}

    on_err = collect_nodes(g, "leaf", enabled=True, collect_fn=_boom_collect)
    chk("K12a 예외 노드 error 기록", any("error" in c for c in on_err["collections"]))
    chk("K12b 나머지 노드는 정상 수집", on_err.get("collected_count") == 2)
    chk("K12c executed=True 유지(부분 성공)", on_err["executed"] is True)

    # ── K13: max_nodes 안전 캡 ──
    called["n"] = 0
    capped = collect_nodes(g, "all", enabled=True, collect_fn=_mock_collect, max_nodes=2)
    chk("K13 max_nodes=2 → collect 2회만", called["n"] == 2)

    # ── K14: 직렬화 + 통계 ──
    ser = serialize_graph(g)
    chk("K14a json 직렬화 문자열", isinstance(ser["json"], str) and len(ser["json"]) > 0)
    chk("K14b json round-trip 동일", json.loads(ser["json"])["stats"]["total"] == 6)
    chk("K14c 한글 보존(ensure_ascii=False)", "신혼여행팩" in ser["json"])
    chk("K14d stats node/edge count", ser["node_count"] == 6 and ser["edge_count"] == 5)
    chk("K14e by_kind concept 카운트", ser["stats"]["by_kind"]["concept"] == 6)
    chk("K14f leaf_nodes=3", ser["stats"]["leaf_nodes"] == 3)
    chk("K14g explore_stats 동봉", ser["stats"]["explore_stats"]["llm_calls"] == 3)

    # ── K15: 빈/이상 입력 raise 0 ──
    chk("K15a 빈 그래프 변환", graph_to_workflow_nodes_edges({"nodes": [], "edges": []})
        == {"nodes": [], "edges": [], "node_map": {}, "counts": {"nodes": 0, "edges": 0}})
    chk("K15b None 그래프 변환 raise 0",
        graph_to_workflow_nodes_edges(None)["counts"]["nodes"] == 0)
    chk("K15c 빈 그래프 페이로드 raise 0",
        build_workflow_payload({"nodes": [], "edges": []})["params"]["name"]
        == "opencrab_workflow_manage")
    chk("K15d 빈 그래프 직렬화 raise 0", serialize_graph({})["node_count"] == 0)
    chk("K15e 댕글링 엣지(없는 노드) 제외",
        graph_to_workflow_nodes_edges(
            {"nodes": [{"id": "a", "label": "A", "depth": 0}],
             "edges": [{"from": "a", "to": "ghost"}]})["counts"]["edges"] == 0)

    # ── K16: 결정성(2회 동일) ──
    chk("K16 변환 결정성 — 2회 동일",
        graph_to_workflow_nodes_edges(g) == graph_to_workflow_nodes_edges(g))

    # ── K17: node id 결정적·ascii-safe ──
    ids = [n["id"] for n in ne["nodes"]]
    chk("K17a node id 유일", len(set(ids)) == len(ids))
    chk("K17b node id ascii-safe", all(all(ord(ch) < 128 for ch in i) for i in ids))

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("=== %d/%d ===" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(
        description="explore 지식그래프 → OpenCrab workflow 페이로드 + 노드별 수집 훅(옵션)")
    ap.add_argument("--explore", help="실 ollama 로 주제 탐색 후 workflow plan 출력(네트워크)")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--breadth", type=int, default=8)
    ap.add_argument("--max-nodes", type=int, default=300)
    ap.add_argument("--workflow-name", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1
    if not args.explore:
        ap.error("--explore '<주제>' 또는 --selftest 필요")

    import binggu_branch_explorer as BE
    graph = BE.explore(args.explore, BE.default_ollama_transport(),
                       max_depth=args.depth, breadth=args.breadth, max_nodes=args.max_nodes)
    plan = build_workflow_sync_plan(graph, workflow_name=args.workflow_name or args.explore)
    ser = serialize_graph(graph)
    out = {"stats": ser["stats"], "workflow_sync_plan": plan,
           "selected_leaf": select_nodes(graph, "leaf")}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
