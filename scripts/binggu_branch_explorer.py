"""binggu_branch_explorer — 1주제 → 재귀 분기 지식그래프(항목 A·2차 라인 발견 앞단 보강).

"넓은 주제 하나를 LLM 으로 재귀 분기시켜 깊이 있는 지식 트리(그래프)로 펼친다."
예: "신혼여행팩" → 패키지구성 / 이동수단 / 현지정보 ... → (패키지구성) 항공·숙박·일정 ...

binggu_subtopic_decompose 가 **1단(facet)** 평면 분해라면, 본 모듈은 그 facet 을 다시 분기시키는
**다단 재귀**. 핵심 3가지:
  1) 경로맥락(root > ... > parent > node) 주입 → 드리프트 차단
     ('신혼여행 > 패키지'의 '패키지'를 소프트웨어 패키지로 오해하지 않게 전체 경로를 LLM 에 줌).
  2) 관련성 가지치기(relevance_min) → root 에서 멀어진 가지 prune.
  3) budget/depth 제어(max_nodes/max_depth/breadth) → 폭발 차단.

★ 설계 철학(사장님 지침 — 고정 금지·LLM 유동):
  - 분기 내용·관련성 판단·가지치기 의미는 **전부 LLM(transport)** 이 결정.
    코드에 주제별 분기 리스트/고정 키워드 **하드코딩 0**(DOMAIN_TEMPLATE 류 없음).
  - 코드가 갖는 것은 **메커니즘만**: 재귀 BFS 순회·경로맥락 조립·budget/depth 카운터·
    dedup(라벨 정규화 병합·사이클 차단)·그래프 자료구조.
  - 폭발 제어 파라미터(max_depth/max_nodes/breadth/relevance_min)는 호출인자(메커니즘).

규약:
  - transport(prompt:str) -> json. ollama 등 LLM. 반환은 list/dict/str(JSON 문자열) 관용 처리.
  - 절대 raise 0(공개 함수) — transport/파싱 예외 흡수. 실패 노드는 leaf 로(무손실 부분 그래프).
  - 결정성: transport mock 결정적이면 출력 완전 결정적. dedup/budget/depth 정확.
  - 실 네트워크는 default_ollama_transport 에만 격리(lazy·selftest 미사용).

진입점: explore(root, transport, ...) -> {nodes, edges, pruned, stats}.
"""
import re
import json


# ── 라벨 정규화 / 토큰(메커니즘 — 의미판단 아님) ──────────────────────────
def _normalize_label(label):
    """dedup·사이클 차단용 정규화 키. 소문자·앞뒤 글머리표/따옴표 제거·공백 축약.
    의미 분류 아님(순수 문자열 정규화)."""
    s = str(label or "").strip().strip("\"'`-*•·▪◦●○[](){}").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _tokens(text):
    """binggu_discover/subtopic_decompose 와 동일 정규식 — 로컬 재정의(강결합 회피)."""
    return set(re.findall(r"[0-9a-z가-힣]+", str(text or "").lower()))


# ── transport 응답 관용 파서(raise 0) ─────────────────────────────────────
def _coerce_labels(resp):
    """transport 응답 → child 라벨 리스트. list/dict/str(JSON|줄단위) 관용 처리.
    (a) list[str] → 그대로 / list[dict] → label|branch|subtopic|name|가지 키 추출
    (b) dict → branches|children|items|subtopics|가지 키의 리스트
    (c) str → JSON 시도 후 위 규칙, 실패하면 줄단위 폴백
    인식불가 → []. 모든 파싱 예외 흡수."""
    if resp is None:
        return []

    def _from_list(seq):
        out = []
        for item in seq:
            if isinstance(item, dict):
                v = (item.get("label") or item.get("branch") or item.get("subtopic")
                     or item.get("name") or item.get("가지") or item.get("title"))
                if v:
                    out.append(str(v).strip())
            elif item is not None:
                s = str(item).strip()
                if s:
                    out.append(s)
        return out

    def _from_dict(d):
        for k in ("branches", "children", "items", "subtopics", "가지", "labels", "results"):
            v = d.get(k)
            if isinstance(v, list):
                return _from_list(v)
        # 폴백: 화이트리스트 키 없으면 dict 의 첫 list 값 추출
        #   (LLM 이 format=json 강제 시 'json' 등 임의 키로 배열을 래핑하는 경우 관용)
        for v in d.values():
            if isinstance(v, list):
                return _from_list(v)
        return []

    if isinstance(resp, list):
        return _from_list(resp)
    if isinstance(resp, dict):
        return _from_dict(resp)
    if isinstance(resp, str):
        text = resp.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return _from_list(parsed)
        if isinstance(parsed, dict):
            return _from_dict(parsed)
        # JSON 아님 → 줄단위 폴백(글머리표 제거)
        lines = [ln.strip(" \t-*•·▪◦") for ln in text.splitlines()]
        return [ln for ln in lines if ln]
    return []


# ── 깨진 라벨 필터(구조적·주제무관 — LLM 분기 출력 후처리·고정 단어 0) ───────
#   실증 n10: '{"가지":["브라이덜 무ｎｍ...シャシャシャ' 류 반복토큰/JSON파편 통과 버그.
#   ★ 노이즈 판정을 주제별 고정 단어로 박지 않는다(brittle). 순수 구조 신호만:
#     과길이 / JSON 구조 파편 / 토큰·문자 연속반복 / 제어문자·깨진 유니코드.
_LABEL_MAX_LEN = 40                                   # 정상 라벨=짧은 명사구. 초과=깨진 출력
_JSON_FRAGMENT_RE = re.compile(r'[{}\[\]"]')          # 배열/객체 텍스트 파편(정상 명사구엔 부재)
_REPEAT_RUN_RE = re.compile(r"(.{1,4}?)\1{2,}", re.S)  # 1~4자 단위 3회+ 연속 반복(シャシャシャ)
_CONTROL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f�]")  # 제어문자·U+FFFD(깨진 유니코드)


def _is_broken_label(label):
    """LLM 분기 출력의 깨진 라벨 판정(구조적·주제무관·고정 단어 0).
    신호: 과길이 / JSON 구조 파편 / 토큰·문자 연속반복 / 제어문자·깨진 유니코드.
    정상 라벨(짧은 명사구)은 False. 의미판단 0 — 순수 구조 신호.
    구두점/공백 전용(정규화 후 빈 키)은 깨짐 아님(False) → explore 의 empty prune 에 위임."""
    s = str(label or "")
    if not _normalize_label(s):
        return False  # 빈/구두점전용 — explore empty prune 가 처리(여기서 가로채면 사유 왜곡)
    if len(s) > _LABEL_MAX_LEN:
        return True
    if _JSON_FRAGMENT_RE.search(s):
        return True
    if _CONTROL_RE.search(s):
        return True
    if _REPEAT_RUN_RE.search(s):
        return True
    return False


def _coerce_score(resp):
    """transport 응답 → 0~1 관련성 float. number / {relevance|score|관련성:..} / str 관용.
    파싱 실패 → None(호출자 fail-open). 예외 흡수."""
    if resp is None:
        return None
    if isinstance(resp, bool):  # bool 은 int 하위 — 별도 차단
        return 1.0 if resp else 0.0
    if isinstance(resp, (int, float)):
        return max(0.0, min(1.0, float(resp)))
    if isinstance(resp, dict):
        for k in ("relevance", "score", "관련성", "rel"):
            if k in resp:
                try:
                    return max(0.0, min(1.0, float(resp[k])))
                except (TypeError, ValueError):
                    return None
        return None
    if isinstance(resp, str):
        text = resp.strip()
        try:
            return max(0.0, min(1.0, float(text)))
        except ValueError:
            pass
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return _coerce_score(parsed)
    return None


# ── 경로맥락 조립(드리프트 차단의 핵심·메커니즘) ──────────────────────────
def _context_chain(path, node):
    """root > ... > parent > node 경로 라벨 체인 문자열. path=조상 라벨 리스트(root..parent)."""
    chain = [str(x).strip() for x in (path or []) if str(x).strip()]
    chain.append(str(node).strip())
    return " > ".join(chain)


def build_expand_prompt(node, root, path, breadth=8):
    """expand 프롬프트(전체 경로맥락 주입). 분기 '의미'는 LLM 이 결정 — 코드는 맥락만 조립.
    대상 노드를 별도 라인('대상 노드:')으로 명시 → 응답 파서/테스트 mock 가 식별 가능."""
    chain = _context_chain(path, node)
    return (
        "당신은 지식 탐색 트리를 한 단계 확장한다.\n"
        "루트 주제: %s\n"
        "현재 경로: %s\n"
        "대상 노드: %s\n"
        "위 '현재 경로' 맥락 안에서만 대상 노드의 직접 하위 가지(세부 주제)를 최대 %d개 제안하라.\n"
        "경로 맥락을 벗어난 해석(드리프트)은 금지한다. "
        "예) 경로가 여행 맥락이면 '패키지'를 소프트웨어 패키지로 해석하지 말 것.\n"
        "각 가지는 대상 노드를 한 단계 더 구체화한 것이어야 하며, 조상 노드와 중복되면 안 된다.\n"
        "출력은 JSON 배열 [\"가지1\", \"가지2\", ...] 형식. 라벨만, 설명 없이."
        % (str(root).strip(), chain, str(node).strip(), int(breadth))
    )


def build_relevance_prompt(label, root, path):
    """관련성 평가 프롬프트. 가지가 루트 주제에 얼마나 붙어있는지 LLM 이 0~1 로 판단.
    평가 대상 가지를 별도 라인('평가 대상 가지:')으로 명시(파서/mock 식별용)."""
    chain = _context_chain(path, "")  # root..parent 까지의 경로
    return (
        "지식 탐색 트리에서 한 가지의 주제 관련성을 평가한다.\n"
        "루트 주제: %s\n"
        "현재 경로: %s\n"
        "평가 대상 가지: %s\n"
        "이 가지가 루트 주제 '%s' 에 얼마나 관련 있는지 0.0~1.0 사이로 평가하라. "
        "경로 맥락에서 벗어났으면 낮게, 루트 주제를 구체화하면 높게.\n"
        "출력은 JSON 숫자 또는 {\"relevance\": 0.0~1.0} 형식."
        % (str(root).strip(), chain.rstrip(" >"), str(label).strip(), str(root).strip())
    )


# ── 관련성 경로일관성 휴리스틱(relevance_transport 미주입 시 폴백·메커니즘) ──
def path_consistency(label, root, path):
    """경로 일관성 관련성 — label 토큰이 lineage(root + path 조상)에 얼마나 grounded 인가. 0~1.
    LLM 없이 결정적. 순수 어휘 앵커링(의미판단 아님) — 새 어휘를 도입한 가지는 낮게 나오므로,
    의미적 분기에는 relevance_transport(LLM) 주입을 권장. 드리프트(겹침 0)는 0.0."""
    lt = _tokens(label)
    if not lt:
        return 0.0
    ctx = _tokens(root)
    for p in (path or []):
        ctx |= _tokens(p)
    if not ctx:
        return 1.0  # lineage 어휘 자체가 없으면 판단 보류(fail-open)
    grounded = len(lt & ctx)
    return grounded / len(lt)


# ── 공개: expand_node / score_relevance ───────────────────────────────────
def expand_node(node, root, path, transport, breadth=8):
    """node 의 직접 하위 가지 라벨 리스트 반환. 전체 경로맥락 주입 → LLM 이 맥락 내 분기 생성.
    transport(prompt:str)->json. breadth 로 캡. 절대 raise 0(transport/파싱 예외 → [])."""
    if transport is None:
        return []
    try:
        b = int(breadth)
    except (TypeError, ValueError):
        b = 8
    if b <= 0:
        return []
    prompt = build_expand_prompt(node, root, path, breadth=b)
    try:
        resp = transport(prompt)
    except Exception:
        return []
    labels = _coerce_labels(resp)
    # 빈 라벨 제거 + 깨진 라벨(구조적·주제무관) 필터 + breadth 캡(순서 보존).
    #   구두점전용('---' 등)은 _is_broken_label 이 통과시켜 explore 의 empty prune 가 처리.
    out = []
    for x in labels:
        s = str(x).strip()
        if not s or _is_broken_label(s):
            continue
        out.append(s)
    return out[:b]


def score_relevance(label, root, path, transport=None):
    """가지(label)의 root 주제 관련성 0~1. transport 주입 시 LLM 판단, 아니면 경로일관성 휴리스틱.
    절대 raise 0 — LLM 경로 예외/파싱 실패는 fail-open(1.0, 보존). 휴리스틱은 결정적."""
    if transport is None:
        return path_consistency(label, root, path)
    prompt = build_relevance_prompt(label, root, path)
    try:
        resp = transport(prompt)
    except Exception:
        return 1.0  # LLM 경로 실패 → 보존(가지치기 안 함·fail-open)
    score = _coerce_score(resp)
    if score is None:
        return 1.0  # 파싱 실패 → 보존
    return score


# ── 핵심: explore (재귀 BFS·메커니즘) ─────────────────────────────────────
def explore(root, transport, max_depth=3, max_nodes=300, breadth=8,
            relevance_min=0.5, relevance_transport=None):
    """1주제 → 재귀 분기 지식그래프. BFS 확장 + 경로맥락 드리프트 차단 + 관련성 가지치기 + budget.

    인자(폭발 제어 — 전부 메커니즘):
      max_depth: 최대 깊이(root=0). node.depth < max_depth 인 노드만 확장.
      max_nodes: 노드 총량 budget. 도달 시 신규 노드 생성 중단(나머지 'budget' prune).
      breadth: 노드당 최대 자식 수(expand 캡).
      relevance_min: 관련성 하한. score_relevance < relevance_min → 'low_relevance' prune.
      relevance_transport: 관련성 LLM 판단기(미주입 시 경로일관성 휴리스틱).

    반환:
      {nodes:[{id,label,depth,parent_id,relevance}], edges:[{from,to}],
       pruned:[{label,reason,parent_id}],
       stats:{total, by_depth, pruned_count, llm_calls, max_depth_reached, budget_hit}}

    dedup: 정규화 라벨 동일 → 노드 1개로 병합(자식이 기존 노드면 교차 엣지만 추가·재확장 안 함).
    사이클 차단: 자식 라벨이 조상과 동일 → 'cycle' prune(엣지 미추가·DAG 유지).
    결정성: transport 결정적이면 완전 결정적(BFS·dict 삽입순).
    절대 raise 0.
    """
    # ── 인자 정규화(메커니즘 카운터) ──
    def _int(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    max_depth = max(0, _int(max_depth, 3))
    max_nodes = max(1, _int(max_nodes, 300))
    breadth = max(0, _int(breadth, 8))
    try:
        relevance_min = float(relevance_min)
    except (TypeError, ValueError):
        relevance_min = 0.5

    root_label = "" if root is None else str(root).strip()

    nodes = []
    edges = []
    edge_seen = set()
    pruned = []
    label_to_id = {}     # 정규화 라벨 → node id (dedup·사이클)
    counter = {"n": 0}   # 실제 transport 호출 횟수(llm_calls)

    # transport 호출 카운팅 래퍼(실제 호출 정확 집계)
    def _counted(t):
        if t is None:
            return None
        def w(prompt):
            counter["n"] += 1
            return t(prompt)
        return w
    exp_t = _counted(transport)
    rel_t = _counted(relevance_transport)

    if not root_label:
        return {"nodes": [], "edges": [], "pruned": [],
                "stats": {"total": 0, "by_depth": {}, "pruned_count": 0,
                          "llm_calls": 0, "max_depth_reached": 0, "budget_hit": False}}

    def _add_edge(frm, to):
        key = (frm, to)
        if key in edge_seen:
            return
        edge_seen.add(key)
        edges.append({"from": frm, "to": to})

    def _new_node(label, depth, parent_id, relevance, norm):
        nid = "n%d" % len(nodes)
        nodes.append({"id": nid, "label": label, "depth": depth,
                      "parent_id": parent_id, "relevance": round(float(relevance), 4)})
        label_to_id[norm] = nid
        return nid

    # 루트 노드(관련성 1.0 고정·확장 비용 없음)
    root_norm = _normalize_label(root_label)
    root_id = _new_node(root_label, 0, None, 1.0, root_norm)

    # BFS 큐: (node_id, label, depth, chain_labels[root..node], ancestor_ids set)
    from collections import deque
    queue = deque()
    queue.append((root_id, root_label, 0, [root_label], {root_id}))
    budget_hit = False
    max_depth_reached = 0

    while queue:
        nid, nlabel, depth, chain, ancestors = queue.popleft()
        if depth >= max_depth:
            continue  # leaf — 확장 안 함(노드는 보존)
        if len(nodes) >= max_nodes:
            budget_hit = True
            continue
        # 경로맥락 = chain[:-1](조상) + nlabel → expand_node 가 chain 재구성
        children = expand_node(nlabel, root_label, chain[:-1], exp_t, breadth=breadth)
        for clabel in children:
            cnorm = _normalize_label(clabel)
            if not cnorm:
                pruned.append({"label": clabel, "reason": "empty", "parent_id": nid})
                continue
            # 사이클 차단 — 자식이 조상 라벨과 동일하면 back-edge 금지
            if cnorm in label_to_id and label_to_id[cnorm] in ancestors:
                pruned.append({"label": clabel, "reason": "cycle", "parent_id": nid})
                continue
            # dedup — 이미 존재하는 라벨(조상 아님) → 교차 엣지만(재확장 안 함·병합)
            if cnorm in label_to_id:
                _add_edge(nid, label_to_id[cnorm])
                continue
            # 관련성 가지치기
            rel = score_relevance(clabel, root_label, chain, rel_t)
            if rel < relevance_min:
                pruned.append({"label": clabel, "reason": "low_relevance", "parent_id": nid})
                continue
            # budget — 신규 노드 한도
            if len(nodes) >= max_nodes:
                budget_hit = True
                pruned.append({"label": clabel, "reason": "budget", "parent_id": nid})
                continue
            cdepth = depth + 1
            cid = _new_node(clabel, cdepth, nid, rel, cnorm)
            _add_edge(nid, cid)
            if cdepth > max_depth_reached:
                max_depth_reached = cdepth
            queue.append((cid, clabel, cdepth, chain + [clabel], ancestors | {cid}))

    # stats
    by_depth = {}
    for nd in nodes:
        by_depth[nd["depth"]] = by_depth.get(nd["depth"], 0) + 1
    stats = {"total": len(nodes), "by_depth": by_depth,
             "pruned_count": len(pruned), "llm_calls": counter["n"],
             "max_depth_reached": max_depth_reached, "budget_hit": budget_hit}
    return {"nodes": nodes, "edges": edges, "pruned": pruned, "stats": stats}


# ── 실 ollama transport 팩토리(selftest 미사용·실 네트워크 전용·lazy) ──────
def default_ollama_transport(model="qwen2.5:32b-instruct-q4_K_M", url="http://localhost:11434", timeout=120):
    """실 ollama generate transport 생성기(클로저) — **selftest 미사용·실 endpoint 전용**.
    반환 transport(prompt:str)->json(파싱된 객체 또는 원문 문자열). urllib lazy(서드파티 0).
    ollama generate API: POST {url}/api/generate {model, prompt, format:'json', stream:false}.
    네트워크 예외는 expand_node/score_relevance 가 흡수(transport 자체는 실 경로에서 raise 가능)."""
    def _transport(prompt):
        import json as _json
        import urllib.request as _u
        endpoint = str(url).rstrip("/") + "/api/generate"
        body = _json.dumps({"model": model, "prompt": str(prompt),
                            "format": "json", "stream": False}).encode("utf-8")
        req = _u.Request(endpoint, data=body,
                         headers={"Content-Type": "application/json"}, method="POST")
        with _u.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            data = _json.loads(raw)
        except Exception:
            return raw
        # ollama 응답 {"response": "<생성 JSON 문자열>"} — 생성 텍스트 반환(_coerce_* 가 재파싱)
        if isinstance(data, dict) and "response" in data:
            return data.get("response")
        return raw
    return _transport


# ── selftest (transport mock·실 네트워크 0·결정적) ─────────────────────────
def _tree_transport(tree, record=None):
    """mock expand transport — 프롬프트의 '대상 노드:' 라인을 읽어 고정 자식 반환.
    tree: {node_label: [child labels]}. record(list) 주면 프롬프트 캡처."""
    def t(prompt):
        if record is not None:
            record.append(prompt)
        m = re.search(r"대상 노드:\s*(.+)", prompt)
        node = m.group(1).strip() if m else ""
        return list(tree.get(node, []))
    return t


def _rel_transport(scores, default=1.0):
    """mock relevance transport — '평가 대상 가지:' 라인을 읽어 점수 반환."""
    def t(prompt):
        m = re.search(r"평가 대상 가지:\s*(.+)", prompt)
        label = m.group(1).strip() if m else ""
        return {"relevance": scores.get(label, default)}
    return t


def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    # E1 — 기본 트리 확장(root 포함·노드 다수)
    tree = {"신혼여행팩": ["패키지구성", "이동수단", "현지정보"],
            "패키지구성": ["항공", "숙박", "일정"],
            "이동수단": ["렌터카", "택시"],
            "현지정보": ["맛집", "명소"]}
    # 구조 메커니즘 격리 — relevance_min=0.0(관련성 가지치기는 E6/E11 에서 별도 검증)
    r = explore("신혼여행팩", _tree_transport(tree), max_depth=3, breadth=8, relevance_min=0.0)
    labels = [n["label"] for n in r["nodes"]]
    chk("E1a root 노드 존재(depth 0)", any(n["depth"] == 0 and n["label"] == "신혼여행팩" for n in r["nodes"]))
    chk("E1b 1단 facet 3개 확장", all(x in labels for x in ("패키지구성", "이동수단", "현지정보")))
    chk("E1c 2단 손자 확장(항공/맛집)", "항공" in labels and "맛집" in labels)
    chk("E1d edges = 노드수-1(트리, 병합/사이클 없음)", len(r["edges"]) == len(r["nodes"]) - 1)

    # E2 — max_depth 정지(깊이 한도 초과 노드 없음)
    r2 = explore("신혼여행팩", _tree_transport(tree), max_depth=1, breadth=8, relevance_min=0.0)
    chk("E2a max_depth=1 → 깊이 1 초과 노드 없음", all(n["depth"] <= 1 for n in r2["nodes"]))
    chk("E2b max_depth=1 → 손자(항공) 미생성", "항공" not in [n["label"] for n in r2["nodes"]])
    chk("E2c root+자식만(1+3=4)", len(r2["nodes"]) == 4)

    # E3 — max_nodes budget 정지
    big = {"R": ["a", "b", "c", "d", "e"], "a": ["a1", "a2"], "b": ["b1", "b2"]}
    r3 = explore("R", _tree_transport(big), max_depth=3, max_nodes=3, breadth=8, relevance_min=0.0)
    chk("E3a 노드 총량 <= max_nodes", r3["stats"]["total"] <= 3)
    chk("E3b budget_hit 플래그", r3["stats"]["budget_hit"] is True)
    chk("E3c budget prune 로그 존재", any(p["reason"] == "budget" for p in r3["pruned"]))

    # E4 — breadth 캡(자식 많아도 breadth 개만)
    wide = {"R": ["c%d" % i for i in range(20)]}
    r4 = explore("R", _tree_transport(wide), max_depth=1, breadth=5, relevance_min=0.0)
    chk("E4 breadth=5 → 자식 5개만", len([n for n in r4["nodes"] if n["depth"] == 1]) == 5)

    # E5 — dedup 병합(같은 라벨 두 부모 → 노드 1개·엣지 2개)
    dag = {"R": ["A", "B"], "A": ["C"], "B": ["C"], "C": []}
    r5 = explore("R", _tree_transport(dag), max_depth=3, breadth=8, relevance_min=0.0)
    c_nodes = [n for n in r5["nodes"] if _normalize_label(n["label"]) == "c"]
    chk("E5a 중복 라벨 C 노드 1개(병합)", len(c_nodes) == 1)
    cid = c_nodes[0]["id"]
    chk("E5b C 로 들어오는 엣지 2개(A,B)", len([e for e in r5["edges"] if e["to"] == cid]) == 2)
    chk("E5c 총 노드 4개(R,A,B,C)", r5["stats"]["total"] == 4)

    # E6 — 관련성 가지치기(relevance_transport mock 저점수 → prune)
    drift_tree = {"R": ["관련가지", "드리프트가지"]}
    rel = _rel_transport({"관련가지": 0.9, "드리프트가지": 0.1}, default=0.9)
    r6 = explore("R", _tree_transport(drift_tree), max_depth=1, breadth=8,
                 relevance_min=0.5, relevance_transport=rel)
    r6_labels = [n["label"] for n in r6["nodes"]]
    chk("E6a 고관련 가지 보존", "관련가지" in r6_labels)
    chk("E6b 저관련 가지 prune(노드 미생성)", "드리프트가지" not in r6_labels)
    chk("E6c prune 사유 low_relevance", any(p["reason"] == "low_relevance" for p in r6["pruned"]))
    chk("E6d 보존 가지 relevance 기록", any(abs(n["relevance"] - 0.9) < 1e-6
                                            for n in r6["nodes"] if n["label"] == "관련가지"))

    # E7 — 경로맥락 주입(깊은 노드 프롬프트에 전체 체인 포함)
    rec = []
    explore("신혼여행팩", _tree_transport(tree, record=rec), max_depth=3, breadth=8, relevance_min=0.0)
    # '항공' 의 부모 '패키지구성' 확장 프롬프트엔 'root > 패키지구성' 경로가 있어야
    pkg_prompts = [p for p in rec if "대상 노드: 패키지구성" in p]
    chk("E7a 패키지구성 확장 프롬프트 존재", len(pkg_prompts) >= 1)
    chk("E7b 프롬프트에 root 경로맥락 포함(신혼여행팩 > 패키지구성)",
        any("신혼여행팩 > 패키지구성" in p for p in pkg_prompts))
    chk("E7c 드리프트 차단 지시문 포함", any("드리프트" in p for p in pkg_prompts))

    # E8 — 사이클 차단(자식이 조상과 동일 → cycle prune·DAG 유지)
    cyc = {"R": ["A"], "A": ["R", "B"], "B": []}
    r8 = explore("R", _tree_transport(cyc), max_depth=5, breadth=8, relevance_min=0.0)
    chk("E8a 사이클(R 재등장) prune", any(p["reason"] == "cycle" for p in r8["pruned"]))
    # back-edge(A→R) 없어야 — root 로 들어오는 엣지 0
    chk("E8b root 로 향하는 back-edge 0(DAG)",
        len([e for e in r8["edges"] if e["to"] == r8["nodes"][0]["id"]]) == 0)
    chk("E8c B 는 정상 생성", "B" in [n["label"] for n in r8["nodes"]])

    # E9 — 결정성(동일 mock·동일 인자 2회 완전 동일)
    a = explore("신혼여행팩", _tree_transport(tree), max_depth=3, breadth=8, relevance_min=0.0)
    b = explore("신혼여행팩", _tree_transport(tree), max_depth=3, breadth=8, relevance_min=0.0)
    chk("E9 결정성 — 2회 호출 완전 동일", a == b)

    # E10 — stats 정합
    chk("E10a stats.total == len(nodes)", r["stats"]["total"] == len(r["nodes"]))
    chk("E10b by_depth 합 == total", sum(r["stats"]["by_depth"].values()) == r["stats"]["total"])
    chk("E10c pruned_count == len(pruned)", r["stats"]["pruned_count"] == len(r["pruned"]))
    # llm_calls == 확장된(내부) 노드 수(leaf·max_depth 도달은 미확장). relevance_transport 없음.
    internal = len([n for n in r["nodes"] if n["depth"] < 3
                    and any(e["from"] == n["id"] for e in r["edges"])])
    chk("E10d llm_calls > 0(확장 호출 집계)", r["stats"]["llm_calls"] >= internal and r["stats"]["llm_calls"] > 0)

    # E10e — llm_calls 정확 집계(relevance_transport 포함 시 expand+relevance 합)
    rc = explore("R", _tree_transport({"R": ["x", "y"]}), max_depth=1, breadth=8,
                 relevance_transport=_rel_transport({}, default=0.9))
    # expand 1회(R) + relevance 2회(x,y) = 3
    chk("E10e llm_calls = expand + relevance 합", rc["stats"]["llm_calls"] == 3)

    # E11 — 경로일관성 휴리스틱(relevance_transport 미주입 폴백)
    chk("E11a grounded 라벨 높음", path_consistency("발리 신혼여행", "신혼여행", ["신혼여행"]) >= 0.5)
    chk("E11b 드리프트(겹침 0) 낮음", path_consistency("python pip install", "신혼여행", ["신혼여행"]) == 0.0)
    chk("E11c 빈 라벨 0.0", path_consistency("", "신혼여행", []) == 0.0)
    # explore 휴리스틱 경로 — 드리프트 자식 prune
    heur = {"신혼여행": ["신혼여행 항공", "javascript npm"]}
    r11 = explore("신혼여행", _tree_transport(heur), max_depth=1, breadth=8, relevance_min=0.5)
    r11_labels = [n["label"] for n in r11["nodes"]]
    chk("E11d 휴리스틱 — grounded 가지 보존", "신혼여행 항공" in r11_labels)
    chk("E11e 휴리스틱 — 드리프트 가지 prune", "javascript npm" not in r11_labels)

    # E11f~ — _coerce_labels 관용 파싱(실 LLM format=json 이 'json' 등 임의키로 배열 래핑·이번 실증 버그 회귀방지)
    chk("E11f {json:[...]} 임의키 list 폴백", _coerce_labels({"json": ["a", "b"]}) == ["a", "b"])
    chk("E11g 화이트리스트 키 우선(branches)", _coerce_labels({"branches": ["x"]}) == ["x"])
    chk("E11h str 임의키 래핑 폴백", _coerce_labels('{"json": ["c"]}') == ["c"])
    chk("E11i dict 에 list 값 없으면 []", _coerce_labels({"k": "v"}) == [])

    # E12 — 절대 raise 0(transport 예외 → leaf·부분 그래프)
    def _boom(prompt):
        raise RuntimeError("llm down")
    r12 = explore("R", _boom, max_depth=3, breadth=8)
    chk("E12a transport 예외 흡수 — root 만 반환", r12["stats"]["total"] == 1)
    chk("E12b expand 예외 → []", expand_node("x", "R", [], _boom) == [])
    chk("E12c relevance 예외 → 1.0(fail-open)", score_relevance("x", "R", [], _boom) == 1.0)

    # E13 — 빈/None root
    chk("E13a None root → 빈 그래프", explore(None, _tree_transport(tree))["nodes"] == [])
    chk("E13b 빈 문자열 root → 빈 그래프", explore("   ", _tree_transport(tree))["stats"]["total"] == 0)

    # E14 — _coerce_labels / _coerce_score 관용 파싱
    chk("E14a list[str]", _coerce_labels(["a", "b"]) == ["a", "b"])
    chk("E14b list[dict label]", _coerce_labels([{"label": "x"}, {"branch": "y"}]) == ["x", "y"])
    chk("E14c dict branches", _coerce_labels({"branches": ["m", "n"]}) == ["m", "n"])
    chk("E14d JSON 문자열", _coerce_labels('["p","q"]') == ["p", "q"])
    chk("E14e 줄단위 폴백", _coerce_labels("- 알파\n- 베타") == ["알파", "베타"])
    chk("E14f 인식불가 → []", _coerce_labels(123) == [])
    chk("E14g score number", _coerce_score(0.7) == 0.7)
    chk("E14h score dict", _coerce_score({"relevance": 0.3}) == 0.3)
    chk("E14i score 클램프", _coerce_score(1.8) == 1.0 and _coerce_score(-1) == 0.0)
    chk("E14j score 파싱실패 → None", _coerce_score("주제 무관 텍스트") is None)
    chk("E14k score JSON 문자열", _coerce_score('{"score": 0.6}') == 0.6)

    # E15 — node 스키마 5키 보존
    chk("E15 모든 노드 id/label/depth/parent_id/relevance 5키",
        all(set(["id", "label", "depth", "parent_id", "relevance"]) <= set(n.keys())
            for n in r["nodes"]))

    # E16 — empty 라벨 prune. 공백전용("   ")은 expand_node 에서 걸러지고,
    #   글머리표/구두점만("---")은 strip 통과하나 정규화 후 빈 키 → explore 가 empty prune.
    r16 = explore("R", _tree_transport({"R": ["정상", "   ", "---"]}), max_depth=1, breadth=8, relevance_min=0.0)
    chk("E16a 정규화 빈 라벨('---') prune(empty)", any(p["reason"] == "empty" for p in r16["pruned"]))
    chk("E16b 정상 자식만 노드화", [n["label"] for n in r16["nodes"] if n["depth"] == 1] == ["정상"])

    # E17 — 깨진 라벨 필터(구조적·주제무관). 실증 n10 반복토큰/JSON파편/과길이/제어문자 드롭, 정상 보존.
    broken_tree = {"R": [
        "정상가지",                       # 정상 보존
        '{"가지":["브라이덜 무ｎｍ',         # JSON 구조 파편
        "シャシャシャシャシャ",             # 토큰 연속 반복
        "가" * 50,                        # 과길이
        "정상\x00라벨",                    # 제어문자
        "또다른정상",                      # 정상 보존
    ]}
    r17 = explore("R", _tree_transport(broken_tree), max_depth=1, breadth=8, relevance_min=0.0)
    r17_labels = [n["label"] for n in r17["nodes"] if n["depth"] == 1]
    chk("E17a 정상 라벨 2개 보존", "정상가지" in r17_labels and "또다른정상" in r17_labels)
    chk("E17b JSON 파편 드롭", not any(("{" in l or '"' in l or "[" in l) for l in r17_labels))
    chk("E17c 반복토큰 드롭", "シャシャシャシャシャ" not in r17_labels)
    chk("E17d 과길이 드롭", ("가" * 50) not in r17_labels)
    chk("E17e 제어문자 드롭", "정상\x00라벨" not in r17_labels)
    chk("E17f 깨진 라벨만 제거(정상 2개)", len(r17_labels) == 2)
    # _is_broken_label 단위 검증
    chk("E17g JSON 파편 True", _is_broken_label('{"가지":[') is True)
    chk("E17h 토큰 반복 True(abcabcabc)", _is_broken_label("abcabcabc") is True)
    chk("E17i 단일문자 반복 True(aaaa)", _is_broken_label("aaaa") is True)
    chk("E17j 과길이 True", _is_broken_label("x" * 41) is True)
    chk("E17k 제어문자 True", _is_broken_label("정상\x00라벨") is True)
    chk("E17l 정상 명사구 False", _is_broken_label("신혼여행 항공") is False
        and _is_broken_label("패키지구성") is False)
    chk("E17m 구두점전용은 비깨짐(empty 위임)", _is_broken_label("---") is False)
    chk("E17n 빈 문자열 비깨짐", _is_broken_label("") is False)

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # CLI: --explore '<주제>' [--depth N] [--max-nodes N] [--breadth N] [--rel-min F] [--llm-relevance]
    root = None
    if "--explore" in sys.argv:
        i = sys.argv.index("--explore")
        if i + 1 < len(sys.argv):
            root = sys.argv[i + 1]

    def _arg(flag, default, cast):
        if flag in sys.argv:
            j = sys.argv.index(flag)
            if j + 1 < len(sys.argv):
                try:
                    return cast(sys.argv[j + 1])
                except ValueError:
                    return default
        return default

    if root:
        tr = default_ollama_transport()
        rel_tr = tr if "--llm-relevance" in sys.argv else None
        res = explore(root, tr,
                      max_depth=_arg("--depth", 3, int),
                      max_nodes=_arg("--max-nodes", 300, int),
                      breadth=_arg("--breadth", 8, int),
                      relevance_min=_arg("--rel-min", 0.5, float),
                      relevance_transport=rel_tr)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("binggu_branch_explorer — use --selftest, or "
              "--explore '<주제>' [--depth N] [--max-nodes N] [--breadth N] [--rel-min F] [--llm-relevance]")
        print("import: explore(root, transport) -> {nodes, edges, pruned, stats}")
