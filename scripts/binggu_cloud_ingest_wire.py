# -*- coding: utf-8 -*-
"""binggu_cloud_ingest_wire.py — 항목 D: topic_to_pack → opencrab-cloud 자동 ingest 래퍼.

역할분담(A) 정합: 온톨로지화는 OpenCrab Cloud 담당. 빙구팩은 binggu_pack_factory.export_cloud_text
로 만든 '출처별 정제 텍스트 번들'을 opencrab_ingest_text(title/content) JSON-RPC tools/call 로
자동 넘기는 얇은 래퍼만 제공한다(노드/온톨로지 생성 0 — 클라우드가 함).

안전 불변 (전부 _selftest 로 증명):
  - 삼중 게이트: dry_run 기본 True + confirm 명시 확정 + 토글 BINGGU_CLOUD_INGEST=1 기본 OFF.
    셋 다 풀려야(AND) live 네트워크 1건이라도 발생. 하나라도 닫혀 있으면 transport 호출 0.
    (confirm tri-state: None=후방호환 not dry_run / True=확정 / False=명시 거부.)
    인증불가(NO_TOKEN)는 호출 전 사전차단. 예외/응답은 typed 카테고리(AUTH_FAILED/
    NETWORK_ERROR/EMPTY_RESPONSE/RPC_ERROR/TOOL_ERROR)로 분류·흡수.
  - URL/토큰 하드코딩 0 · 평문 출력 0: env(BINGGU_CLOUD_MCP_URL/BINGGU_CLOUD_MCP_TOKEN) 우선,
    없으면 binggu_home()/cloud_ingest.json 폴백. 토큰은 _redact_token(sha8/len)만 노출.
  - transport 주입형: default_http_transport 만 실 urllib. _selftest 는 mock transport 주입
    → urllib 미진입(실 네트워크 0). import 시 부수효과 0.
  - 절대 raise 0: 모든 실패는 typed dict(reason code) 반환 — 상위 topic_to_pack 흐름 보존.
  - build_pack/export_cloud_text/topic_to_pack 본체 미수정 — import 재사용(읽기 전용 의존)만.

topic_to_pack 통합용 진입점: ingest_pack(...) (기본 dry_run=True → 계획만, 네트워크 0).
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import binggu_pack_factory as PF  # noqa: E402  (export_cloud_text 재사용 — 미수정)

INGEST_TOOL = "opencrab_ingest_text"
WORKFLOW_TOOL = "opencrab_workflow_manage"
DEFAULT_CLIENT = "binggupack-cloud-ingest"
CONFIG_FILENAME = "cloud_ingest.json"
ENABLE_ENV = "BINGGU_CLOUD_INGEST"      # owner 토글 — '1' 이어야 live 가능(기본 OFF)


# ───────────────────────────── 설정/토큰 ─────────────────────────────
def _binggu_home(home=None):
    """config 폴백 루트. 테스트는 home 인자/BINGGU_HOME 으로 운영 ~/.binggupack 미접촉."""
    if home:
        return home
    env = os.environ.get("BINGGU_HOME")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".binggupack")


def _redact_token(token):
    """토큰 지문 — 평문 0. 'sha8:xxxxxxxx len=NN' / None → 'none'."""
    if not token:
        return "none"
    s = str(token)
    return "sha8:%s len=%d" % (hashlib.sha256(s.encode("utf-8")).hexdigest()[:8], len(s))


# ───────────────────────────── confirm 게이트 / 분류 헬퍼 ─────────────────────────────
def _is_confirmed(dry_run, confirm):
    """명시 confirm 게이트 판정(tri-state·raise 0).

    - dry_run True            → 무조건 False(계획만).
    - confirm is False(명시거부) → False(dry_run=False 여도 live 차단).
    - confirm is True(명시확정)  → True.
    - confirm is None(미전달)   → not dry_run (기존 의미 보존·후방호환).
    """
    if dry_run:
        return False
    if confirm is False:
        return False
    if confirm is True:
        return True
    return not dry_run


def _classify_exception(ex):
    """transport 예외 → 카테고리 문자열(실 urllib 하드 import 0·평문 토큰/URL 노출 0).

    ex.__class__.__name__ + getattr(ex,'code',None) 만으로 판정:
      - HTTPError code in (401,403) → 'AUTH_FAILED'
      - code 존재 기타             → 'HTTP_ERROR:<code>'
      - 클래스명에 URLError/timeout/TimeoutError/socket 포함 → 'NETWORK_ERROR'
      - 그 외                      → 'TRANSPORT_ERROR:<클래스명>'
    """
    name = ex.__class__.__name__
    code = getattr(ex, "code", None)
    if isinstance(code, int):
        if code in (401, 403):
            return "AUTH_FAILED"
        return "HTTP_ERROR:%d" % code
    low = name.lower()
    if "urlerror" in low or "timeout" in low or "socket" in low:
        return "NETWORK_ERROR"
    return "TRANSPORT_ERROR:" + name


def _classify_response(resp):
    """JSON-RPC 응답 → (ok:bool, outcome:str)·raise 0.

      - None / {} / 'result'·'error' 키 모두 부재 → (False, 'EMPTY_RESPONSE')
      - resp.get('error')                         → (False, 'RPC_ERROR')
      - resp['result'].get('isError') True        → (False, 'TOOL_ERROR')
      - 그 외                                      → (True, 'OK')
    """
    if not isinstance(resp, dict) or not resp:
        return (False, "EMPTY_RESPONSE")
    if "result" not in resp and "error" not in resp:
        return (False, "EMPTY_RESPONSE")
    if resp.get("error"):
        return (False, "RPC_ERROR")
    result = resp.get("result")
    if isinstance(result, dict) and result.get("isError"):
        return (False, "TOOL_ERROR")
    return (True, "OK")


def load_cloud_config(env=None, config_path=None, home=None):
    """클라우드 MCP 설정 로드. 우선순위: env > config(<home>/cloud_ingest.json) > none.

    반환(절대 raise 0): {enabled, url, token_present, token(내부전용·외부 미노출),
                        token_fingerprint, source:'env'|'config'|'none', reason}
    enabled = 토글 ENABLE_ENV=='1'. url/token 부재는 reason='NO_CLOUD_CONFIG'.
    """
    e = os.environ if env is None else env
    enabled = str(e.get(ENABLE_ENV, "")).strip() == "1"
    out = {"enabled": enabled, "url": None, "token_present": False, "token": None,
           "token_fingerprint": "none", "source": "none", "reason": None}

    url = (e.get("BINGGU_CLOUD_MCP_URL") or "").strip() or None
    token = (e.get("BINGGU_CLOUD_MCP_TOKEN") or "").strip() or None
    if url:
        out.update({"url": url, "token": token, "token_present": bool(token),
                    "token_fingerprint": _redact_token(token), "source": "env"})
        out["reason"] = None if token else "NO_TOKEN"
        return out

    # config 폴백 — 운영 home 미접촉(테스트는 home/config_path 주입)
    cpath = config_path or os.path.join(_binggu_home(home), CONFIG_FILENAME)
    if cpath and os.path.exists(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                cfg = json.load(f) or {}
            url = (cfg.get("url") or cfg.get("mcp_url") or "").strip() or None
            token = (cfg.get("token") or cfg.get("mcp_token") or "").strip() or None
            if url:
                out.update({"url": url, "token": token, "token_present": bool(token),
                            "token_fingerprint": _redact_token(token), "source": "config"})
                out["reason"] = None if token else "NO_TOKEN"
                return out
        except Exception as ex:  # noqa — 손상 config 도 raise 0
            out["reason"] = "CONFIG_READ_ERROR:" + type(ex).__name__
            return out

    out["reason"] = "NO_CLOUD_CONFIG"
    return out


# ───────────────────────────── 페이로드 빌더 ─────────────────────────────
def build_ingest_payloads(pack_or_documents, *, create_pack=True, pack_visibility="private",
                          pack_category="mcp", pack_title=None, pack_description=None,
                          workspace_label=None, min_chars=1, start_id=1):
    """export_cloud_text 결과(출처별 번들) → opencrab_ingest_text tools/call payload 리스트.

    각 원소: {title, content, chars, source,
              payload: {jsonrpc:'2.0', id:int, method:'tools/call',
                        params:{name:'opencrab_ingest_text', arguments:{...}}}}
    빈 입력 → []. content == export_cloud_text 텍스트(무손실). raise 0.
    """
    try:
        bundles = PF.export_cloud_text(pack_or_documents, min_chars=min_chars)
    except Exception:  # noqa — export 실패도 빈 계획으로 흡수(상위 raise 0)
        bundles = []

    payloads = []
    rpc_id = int(start_id)
    for b in bundles:
        args = {"title": b["title"], "content": b["text"],
                "create_pack": bool(create_pack),
                "pack_visibility": pack_visibility,
                "pack_category": pack_category}
        if pack_title:
            args["pack_title"] = pack_title
        if pack_description:
            args["pack_description"] = pack_description
        if workspace_label:
            args["workspace_label"] = workspace_label
        payloads.append({
            "title": b["title"], "content": b["text"], "chars": b["chars"], "source": b["source"],
            "payload": {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
                        "params": {"name": INGEST_TOOL, "arguments": args}},
        })
        rpc_id += 1
    return payloads


def build_workflow_payload(action="create", *, name=None, description=None, nodes=None,
                           edges=None, workflow_id=None, workflow_name=None,
                           status="draft", rpc_id=1):
    """opencrab_workflow_manage tools/call payload. raise 0.

    반환: {jsonrpc,id,method:'tools/call',
           params:{name:'opencrab_workflow_manage', arguments:{action, ...}}}
    """
    args = {"action": action}
    if name is not None:
        args["name"] = name
    if description is not None:
        args["description"] = description
    if nodes is not None:
        args["nodes"] = nodes
    if edges is not None:
        args["edges"] = edges
    if workflow_id is not None:
        args["workflow_id"] = workflow_id
    if workflow_name is not None:
        args["workflow_name"] = workflow_name
    if status is not None:
        args["status"] = status
    return {"jsonrpc": "2.0", "id": int(rpc_id), "method": "tools/call",
            "params": {"name": WORKFLOW_TOOL, "arguments": args}}


# ───────────────────────────── transport (실 네트워크 전용) ─────────────────────────────
def default_http_transport(url, token, *, timeout=30):
    """callable(payload)->dict 반환. 실 urllib POST(Bearer) — live 경로에서만 호출.

    import 부수효과 0: urllib 은 반환 callable 이 실제 호출될 때만 진입.
    """
    import urllib.request  # 지연 import — 호출 시점에만 네트워크 의존 로드

    def transport(payload):
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "User-Agent": DEFAULT_CLIENT + "/1.0"}
        if token:
            headers["Authorization"] = "Bearer " + str(token)
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    return transport


def run_mcp_session(transport, payloads, *, client_name=DEFAULT_CLIENT):
    """initialize 1회 후 tools/call 순차 실행. stateless HTTP JSON-RPC.

    반환(typed·raise 0): {ok, initialized, results:[...], errors:[...], calls:int,
                         error_categories:[...]}
      - 기존 키(ok/initialized/results/errors/calls) 불변 — 상위 계약 보존.
      - results[].outcome(OK/EMPTY_RESPONSE/RPC_ERROR/TOOL_ERROR) 추가.
      - errors[].category(_classify_exception/응답분류) 추가.
      - error_categories: 중복제거 카테고리 리스트(가시성).
    transport 예외/비정상 응답은 errors 로 흡수(category 부여).
    """
    out = {"ok": False, "initialized": False, "results": [], "errors": [], "calls": 0,
           "error_categories": []}
    init_payload = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05",
                               "clientInfo": {"name": client_name, "version": "1.0"},
                               "capabilities": {}}}
    try:
        init_resp = transport(init_payload)
        out["initialized"] = True
        init_ok, init_outcome = _classify_response(init_resp)
        out["results"].append({"phase": "initialize", "response": init_resp,
                               "outcome": init_outcome})
        if not init_ok:
            out["errors"].append({"phase": "initialize", "id": 0,
                                  "error": init_outcome, "category": init_outcome})
    except Exception as ex:  # noqa
        cat = _classify_exception(ex)
        out["errors"].append({"phase": "initialize", "id": 0,
                              "error": type(ex).__name__, "category": cat})
        out["error_categories"] = _dedupe([cat])
        return out  # initialize 실패면 tools/call 미진행

    for p in payloads:
        call = p.get("payload") if isinstance(p, dict) and "payload" in p else p
        try:
            resp = transport(call)
            out["calls"] += 1
            resp_ok, outcome = _classify_response(resp)
            out["results"].append({"phase": "tools/call", "id": call.get("id"),
                                   "response": resp, "outcome": outcome})
            if not resp_ok:
                out["errors"].append({"phase": "tools/call", "id": call.get("id"),
                                      "error": outcome, "category": outcome})
        except Exception as ex:  # noqa — 개별 호출 실패도 흡수, 나머지 계속
            cat = _classify_exception(ex)
            out["errors"].append({"phase": "tools/call", "id": call.get("id"),
                                  "error": type(ex).__name__, "category": cat})
    out["ok"] = out["initialized"] and not out["errors"]
    out["error_categories"] = _dedupe([e.get("category") for e in out["errors"]])
    return out


def _dedupe(seq):
    """순서 보존 중복 제거(None 제거)."""
    seen, res = set(), []
    for x in seq:
        if x is None or x in seen:
            continue
        seen.add(x)
        res.append(x)
    return res


# ───────────────────────────── T3 하드제외 게이트 (owner 양보 불가·최우선) ─────────────────────────────
def _apply_t3_gate(payloads):
    """T3(PII · 민감 과거사) 반출 절대금지 — payload content 검사 후 차단분 제거.

    owner 결정([[feedback-binggupack-identity-personal-ontology-agi]] §부분개정): 사용자 온톨로지는
    헌법 §3 제외로 완전자동 업로드하되 **T3 만은 코드로 하드 차단**(양보 불가). dry-run/live 공통
    최우선 게이트(삼중 게이트보다 앞). fail-closed: 필터 로드 실패 시 전량 차단(안전).
    반환 (kept, blocked[{source,title,report}])."""
    try:
        from binggu_t3_filter import is_t3_blocked, t3_report
    except Exception:
        # 안전 판정 자체 불가 → 전량 차단(반출 열어두지 않음)
        return [], [{"source": p.get("source"), "title": p.get("title"),
                     "report": {"blocked": True, "error": "T3_FILTER_UNAVAILABLE"}}
                    for p in payloads]
    kept, blocked = [], []
    for p in payloads:
        try:
            content = p["payload"]["params"]["arguments"]["content"]
        except Exception:
            content = ""
        if is_t3_blocked(content):
            blocked.append({"source": p.get("source"), "title": p.get("title"),
                            "report": t3_report(content)})
        else:
            kept.append(p)
    return kept, blocked


# ───────────────────────────── 오케스트레이터 (메인 진입점) ─────────────────────────────
def ingest_pack(pack_or_documents, *, transport=None, env=None, config_path=None, home=None,
                dry_run=True, confirm=None, create_workflow=False, workflow_spec=None,
                **ingest_opts):
    """topic_to_pack 통합용 메인 진입점.

    삼중 게이트(전부 충족해야 live 네트워크 1건):
      - dry_run=True(기본)         → 계획(payloads)만 반환, 네트워크 0.
      - confirm(명시 확정 게이트)   → tri-state. None=후방호환(not dry_run),
        True=명시 확정, False=명시 거부(dry_run=False 여도 'NOT_CONFIRMED'·호출 0).
      - 토글 BINGGU_CLOUD_INGEST=1 → OFF 면 'CLOUD_INGEST_DISABLED'·호출 0.
      - transport 주입            → None 이면 'NO_TRANSPORT'.
      - url/token 존재            → 'NO_CLOUD_CONFIG' / 'NO_TOKEN'(인증불가 사전차단).

    반환(typed·raise 0): {mode:'dry-run'|'live', enabled, confirmed, planned_calls,
                         payloads|results, workflow, reason, token_fingerprint, source}
    """
    cfg = load_cloud_config(env=env, config_path=config_path, home=home)
    payloads = build_ingest_payloads(pack_or_documents, **ingest_opts)
    # T3 하드제외 게이트(owner 양보 불가·최우선) — PII/과거사 반출 절대금지. dry-run/live 공통.
    payloads, t3_blocked = _apply_t3_gate(payloads)
    confirmed = _is_confirmed(dry_run, confirm)

    wf_payload = None
    if create_workflow:
        spec = dict(workflow_spec or {})
        spec.setdefault("rpc_id", len(payloads) + 1)
        wf_payload = build_workflow_payload(spec.pop("action", "create"), **spec)

    base = {"enabled": cfg["enabled"], "confirmed": confirmed,
            "planned_calls": len(payloads),
            "token_fingerprint": cfg["token_fingerprint"], "source": cfg["source"],
            "t3_blocked": t3_blocked}

    # dry-run: 계획만(네트워크 0) — transport 가 주입돼 있어도 호출 안 함
    if dry_run:
        plan = list(payloads)
        if wf_payload is not None:
            plan.append({"title": "(workflow)", "source": "workflow_manage",
                         "payload": wf_payload})
        base.update({"mode": "dry-run", "payloads": plan,
                     "workflow": wf_payload, "reason": "DRY_RUN"})
        return base

    # live 게이트 1: 명시 confirm 거부(dry_run=False 라도 차단)
    if not confirmed:
        base.update({"mode": "live", "results": None, "workflow": wf_payload,
                     "reason": "NOT_CONFIRMED"})
        return base
    # live 게이트 2: owner 토글
    if not cfg["enabled"]:
        base.update({"mode": "live", "results": None, "workflow": wf_payload,
                     "reason": "CLOUD_INGEST_DISABLED"})
        return base
    # live 게이트 3: transport
    if transport is None:
        base.update({"mode": "live", "results": None, "workflow": wf_payload,
                     "reason": "NO_TRANSPORT"})
        return base
    # live 게이트 4: url
    if not cfg["url"]:
        base.update({"mode": "live", "results": None, "workflow": wf_payload,
                     "reason": "NO_CLOUD_CONFIG"})
        return base
    # live 게이트 5: token(인증불가 사전차단 — transport 호출 0)
    if cfg.get("reason") == "NO_TOKEN":
        base.update({"mode": "live", "results": None, "workflow": wf_payload,
                     "reason": "NO_TOKEN"})
        return base

    call_payloads = list(payloads)
    if wf_payload is not None:
        call_payloads.append({"payload": wf_payload})

    try:
        session = run_mcp_session(transport, call_payloads)
        base.update({"mode": "live", "results": session, "workflow": wf_payload,
                     "reason": _session_reason(session)})
    except Exception as ex:  # noqa — 상위 raise 0 보장(이론상 run_mcp_session 가 이미 흡수)
        base.update({"mode": "live", "results": None, "workflow": wf_payload,
                     "reason": "TRANSPORT_ERROR:" + type(ex).__name__})
    return base


def _session_reason(session):
    """run_mcp_session 결과 → ingest_pack reason 승격(상위호환).

    ok → None. 단일 dominant 카테고리 있으면 그 카테고리, 복합이면 'SESSION_ERROR'.
    """
    if session.get("ok"):
        return None
    cats = session.get("error_categories") or []
    if len(cats) == 1:
        return cats[0]
    return "SESSION_ERROR"


# ───────────────────────────── selftest (mock transport · 네트워크 0) ─────────────────────────────
def _mk_docs():
    def mk(src, texts):
        chunks = [{"item_id": "EVX-%s-%d" % (src, i), "text": t,
                   "source": "harvest :: url :: %s" % src,
                   "evidence_meta": {"raw_pointer": "p"}} for i, t in enumerate(texts)]
        return {"nodes": [], "evidence_index": [], "evidence_chunks": chunks}
    return [mk("alpha", ["가나다 본문 하나 입니다.", "라마바 본문 둘 더 길게 추가 텍스트입니다."]),
            mk("beta", ["짧은 한 줄 베타 본문."])]


def _selftest():
    import tempfile
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    docs = _mk_docs()

    # ── load_cloud_config ──
    c1 = load_cloud_config(env={"BINGGU_CLOUD_MCP_URL": "https://x.example/mcp",
                                "BINGGU_CLOUD_MCP_TOKEN": "tok-abcdef123456", ENABLE_ENV: "1"})
    chk("C1 env URL+TOKEN+toggle → enabled/url/source=env",
        c1["enabled"] and c1["url"] == "https://x.example/mcp"
        and c1["token_present"] and c1["source"] == "env")
    c2 = load_cloud_config(env={})
    chk("C2 설정 전무 → enabled False·reason=NO_CLOUD_CONFIG·url None",
        (not c2["enabled"]) and c2["reason"] == "NO_CLOUD_CONFIG" and c2["url"] is None)
    pub = json.dumps({k: v for k, v in c1.items() if k != "token"}, ensure_ascii=False)
    chk("C3 공개 표현에 원문 토큰 미포함(_redact_token 형식만)",
        "tok-abcdef123456" not in pub and c1["token_fingerprint"].startswith("sha8:")
        and "len=" in c1["token_fingerprint"])

    # ── build_ingest_payloads ──
    pls = build_ingest_payloads(docs)
    chk("B1 번들 2개 → payload 2개", len(pls) == 2)
    chk("B2 method=tools/call·params.name=opencrab_ingest_text",
        all(p["payload"]["method"] == "tools/call"
            and p["payload"]["params"]["name"] == INGEST_TOOL for p in pls))
    chk("B3 arguments.title/content 채워짐(content=export 텍스트)",
        all(p["payload"]["params"]["arguments"]["title"]
            and p["payload"]["params"]["arguments"]["content"] == p["content"] for p in pls))
    # content == export_cloud_text 텍스트 무손실 교차검증
    bundles = PF.export_cloud_text(docs)
    bmap = {b["source"]: b["text"] for b in bundles}
    chk("B3b content == export_cloud_text(무손실)",
        all(p["payload"]["params"]["arguments"]["content"] == bmap[p["source"]] for p in pls))
    chk("B4 빈 입력 → [] (호출 0)",
        build_ingest_payloads([]) == [] and build_ingest_payloads({}) == [])
    chk("B5 기본 pack_visibility=private·create_pack=True·id 순차",
        all(p["payload"]["params"]["arguments"]["pack_visibility"] == "private"
            and p["payload"]["params"]["arguments"]["create_pack"] is True for p in pls)
        and [p["payload"]["id"] for p in pls] == [1, 2])

    # ── build_workflow_payload ──
    wf = build_workflow_payload(action="create", name="WF1", description="d",
                                nodes=[{"package_id": "p"}], edges=[{"from": "a", "to": "b"}])
    chk("W1 params.name=opencrab_workflow_manage·action=create·nodes/edges 전달",
        wf["params"]["name"] == WORKFLOW_TOOL and wf["params"]["arguments"]["action"] == "create"
        and wf["params"]["arguments"]["nodes"] == [{"package_id": "p"}]
        and wf["params"]["arguments"]["edges"] == [{"from": "a", "to": "b"}])

    # ── JSON-RPC 계약 ──
    all_payloads = [p["payload"] for p in pls] + [wf]
    chk("J1 jsonrpc=2.0·id int·params.arguments dict",
        all(p["jsonrpc"] == "2.0" and isinstance(p["id"], int)
            and isinstance(p["params"]["arguments"], dict) for p in all_payloads))

    # ── ingest_pack dry-run (mock-spy transport 호출 0) ──
    spy = {"n": 0}

    def spy_transport(payload):
        spy["n"] += 1
        return {"result": {"isError": False}}

    r_dry = ingest_pack(docs, transport=spy_transport, env={})
    chk("D1 dry_run 기본 → transport 호출 0·mode=dry-run·planned_calls=번들수",
        spy["n"] == 0 and r_dry["mode"] == "dry-run"
        and r_dry["planned_calls"] == 2 and len(r_dry["payloads"]) == 2)

    # ── ingest_pack live + 토글 OFF → 게이트 차단(transport 호출 0) ──
    spy["n"] = 0
    r_off = ingest_pack(docs, transport=spy_transport, env={}, dry_run=False)
    chk("D2 live + 토글 미설정 → CLOUD_INGEST_DISABLED·transport 호출 0",
        r_off["reason"] == "CLOUD_INGEST_DISABLED" and spy["n"] == 0)

    # ── ingest_pack live + 토글 ON + mock transport → initialize 1 + tools/call N ──
    seq = []

    def seq_transport(payload):
        seq.append(payload.get("method"))
        return {"result": {"isError": False}}

    live_env = {ENABLE_ENV: "1", "BINGGU_CLOUD_MCP_URL": "https://x.example/mcp",
                "BINGGU_CLOUD_MCP_TOKEN": "tok-xyz789abcdef"}
    r_live = ingest_pack(docs, transport=seq_transport, env=live_env, dry_run=False)
    chk("D3 live ON → initialize 1회 then tools/call N회 순서",
        seq[0] == "initialize" and seq.count("tools/call") == 2
        and r_live["results"]["calls"] == 2 and r_live["results"]["ok"]
        and r_live["reason"] is None)
    chk("D3b 토큰 평문 미노출(fingerprint 만)",
        "tok-xyz789abcdef" not in json.dumps({k: v for k, v in r_live.items()
                                              if k != "results"})
        and r_live["token_fingerprint"].startswith("sha8:"))

    # ── ingest_pack live + transport 예외 → TRANSPORT/SESSION 흡수(raise 0) ──
    def boom_transport(payload):
        raise RuntimeError("net_down")

    r_err = ingest_pack(docs, transport=boom_transport, env=live_env, dry_run=False)
    chk("D4 transport 예외 → typed 흡수(raise 0)·errors 기록·reason 카테고리 승격",
        isinstance(r_err, dict) and r_err["reason"] == "TRANSPORT_ERROR:RuntimeError"
        and r_err["results"]["errors"]
        and r_err["results"]["error_categories"] == ["TRANSPORT_ERROR:RuntimeError"])

    # ── ingest_pack live + transport None → NO_TRANSPORT ──
    r_nt = ingest_pack(docs, transport=None, env=live_env, dry_run=False)
    chk("D5 live + transport None → NO_TRANSPORT", r_nt["reason"] == "NO_TRANSPORT")

    # ── create_workflow → ingest payload 뒤 workflow_manage append(순서) ──
    seq2 = []

    def seq2_transport(payload):
        nm = payload.get("params", {}).get("name") if payload.get("method") == "tools/call" else payload.get("method")
        seq2.append(nm)
        return {"result": {}}

    r_wf = ingest_pack(docs, transport=seq2_transport, env=live_env, dry_run=False,
                       create_workflow=True,
                       workflow_spec={"action": "create", "name": "WF", "nodes": [{"package_id": "x"}]})
    chk("D6 create_workflow → ingest 뒤 workflow_manage append(순서)",
        seq2 == ["initialize", INGEST_TOOL, INGEST_TOOL, WORKFLOW_TOOL]
        and r_wf["workflow"]["params"]["name"] == WORKFLOW_TOOL)
    r_wf_dry = ingest_pack(docs, env={}, create_workflow=True,
                           workflow_spec={"action": "create", "name": "WF"})
    chk("D6b dry-run workflow 계획 포함(맨끝)",
        r_wf_dry["payloads"][-1]["payload"]["params"]["name"] == WORKFLOW_TOOL)

    # ── T3 하드제외 게이트: PII/과거사 번들 반출 차단(owner 양보불가·최우선) ──
    def _mk_one(src, texts):
        chunks = [{"item_id": "EVX-%s-%d" % (src, i), "text": t,
                   "source": "harvest :: url :: %s" % src, "evidence_meta": {"raw_pointer": "p"}}
                  for i, t in enumerate(texts)]
        return {"nodes": [], "evidence_index": [], "evidence_chunks": chunks}

    t3_docs = [_mk_one("safe", ["결론부터 짧게 답한다는 원칙을 지킨다"]),
               _mk_one("piix", ["연락처는 010-1234-5678 이다"]),
               _mk_one("pastx", ["작년에 빚 때문에 파산 신청했다"])]
    r_t3d = ingest_pack(t3_docs, env={})   # dry-run
    t3_srcs = [p.get("source") for p in r_t3d["payloads"]]
    chk("T3G-1 dry-run: PII/과거사 번들 제외·안전 번들만 계획(planned=1·blocked=2)",
        r_t3d["planned_calls"] == 1 and len(r_t3d["t3_blocked"]) == 2
        and all("safe" in (s or "") for s in t3_srcs))
    spyt = {"n": 0}

    def spyt_transport(payload):
        spyt["n"] += 1
        return {"result": {"isError": False}}

    r_t3l = ingest_pack(t3_docs, transport=spyt_transport, env=live_env,
                        dry_run=False, confirm=True)
    chk("T3G-2 live: T3 차단분 전송 0(안전 1건만 tools/call)·t3_blocked 보존",
        r_t3l["results"]["calls"] == 1 and len(r_t3l["t3_blocked"]) == 2)
    chk("T3G-3 안전 텍스트만이면 T3 게이트 통과(전량 계획)",
        ingest_pack([_mk_one("ok", ["짧게 결론부터", "유연함이 능력이다"])],
                    env={})["planned_calls"] == 1)

    # ── run_mcp_session 직접: initialize protocolVersion/clientInfo 존재 ──
    captured = []

    def cap_transport(payload):
        captured.append(payload)
        return {"ok": True}

    sess = run_mcp_session(cap_transport, [p["payload"] for p in pls])
    init = captured[0]
    chk("S1 initialize params.protocolVersion/clientInfo 존재·tools/call N 카운트",
        init["method"] == "initialize" and "protocolVersion" in init["params"]
        and "clientInfo" in init["params"] and sess["calls"] == 2)

    # ── BINGGU_HOME 임시 격리: config 폴백이 운영 ~/.binggupack 미접촉 ──
    tmp = tempfile.mkdtemp(prefix="cloud_ingest_")
    cpath = os.path.join(tmp, CONFIG_FILENAME)
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump({"url": "https://cfg.example/mcp", "token": "cfgtok-1234567890"}, f)
    c_cfg = load_cloud_config(env={ENABLE_ENV: "1"}, home=tmp)
    chk("H1 config 폴백(home 격리) → source=config·url 로드·운영경로 미접촉",
        c_cfg["source"] == "config" and c_cfg["url"] == "https://cfg.example/mcp"
        and c_cfg["enabled"])
    chk("H2 config 부재(빈 home) → NO_CLOUD_CONFIG(write 0)",
        load_cloud_config(env={}, home=tempfile.mkdtemp(prefix="cloud_empty_"))["reason"]
        == "NO_CLOUD_CONFIG")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    # ── confirm 게이트(명시 분리) ──
    spy["n"] = 0
    r_g1 = ingest_pack(docs, transport=spy_transport, env=live_env, dry_run=False, confirm=False)
    chk("G1 confirm=False 명시거부 → NOT_CONFIRMED·transport 호출 0",
        r_g1["reason"] == "NOT_CONFIRMED" and spy["n"] == 0
        and r_g1["confirmed"] is False)

    seqg2 = []

    def seqg2_transport(payload):
        seqg2.append(payload.get("method"))
        return {"result": {"isError": False}}

    r_g2 = ingest_pack(docs, transport=seqg2_transport, env=live_env, dry_run=False)
    chk("G2 confirm=None 후방호환 → live(initialize+tools/call)·reason None·confirmed True",
        seqg2[0] == "initialize" and seqg2.count("tools/call") == 2
        and r_g2["reason"] is None and r_g2["confirmed"] is True)

    seqg3 = []

    def seqg3_transport(payload):
        seqg3.append(payload.get("method"))
        return {"result": {"isError": False}}

    r_g3 = ingest_pack(docs, transport=seqg3_transport, env=live_env, dry_run=False, confirm=True)
    chk("G3 confirm=True 명시확정 → live 정상·reason None",
        seqg3[0] == "initialize" and r_g3["reason"] is None
        and r_g3["confirmed"] is True and r_g3["results"]["ok"])

    spy["n"] = 0
    r_g4 = ingest_pack(docs, transport=spy_transport, env={}, dry_run=False, confirm=True)
    chk("G4 confirm=True 라도 토글 OFF → CLOUD_INGEST_DISABLED·transport 호출 0",
        r_g4["reason"] == "CLOUD_INGEST_DISABLED" and spy["n"] == 0)

    spy["n"] = 0
    notoken_env = {ENABLE_ENV: "1", "BINGGU_CLOUD_MCP_URL": "https://x.example/mcp"}
    r_g5 = ingest_pack(docs, transport=spy_transport, env=notoken_env, dry_run=False, confirm=True)
    chk("G5 NO_TOKEN 사전차단 → reason=NO_TOKEN·transport 호출 0",
        r_g5["reason"] == "NO_TOKEN" and spy["n"] == 0)

    # ── typed error 분류(인증실패·네트워크·빈응답) ──
    class _HTTPLike(Exception):
        def __init__(self, code):
            super().__init__("http")
            self.code = code

    def auth_transport(payload):
        if payload.get("method") == "initialize":
            return {"result": {}}
        raise _HTTPLike(401)

    r_e1 = ingest_pack(docs, transport=auth_transport, env=live_env, dry_run=False, confirm=True)
    chk("E1 AUTH_FAILED(code=401) → category·reason 승격·raise 0",
        isinstance(r_e1, dict) and r_e1["reason"] == "AUTH_FAILED"
        and any(e.get("category") == "AUTH_FAILED" for e in r_e1["results"]["errors"]))

    class _URLError(Exception):
        pass

    def net_transport(payload):
        if payload.get("method") == "initialize":
            return {"result": {}}
        raise _URLError("conn reset")

    r_e2 = ingest_pack(docs, transport=net_transport, env=live_env, dry_run=False, confirm=True)
    chk("E2 NETWORK_ERROR(URLError) → category·typed 흡수",
        r_e2["reason"] == "NETWORK_ERROR"
        and any(e.get("category") == "NETWORK_ERROR" for e in r_e2["results"]["errors"]))

    def empty_transport(payload):
        if payload.get("method") == "initialize":
            return {"result": {}}
        return {}

    r_e3 = ingest_pack(docs, transport=empty_transport, env=live_env, dry_run=False, confirm=True)
    chk("E3 EMPTY_RESPONSE(tools/call {}) → outcome·errors 적재·reason 승격",
        r_e3["reason"] == "EMPTY_RESPONSE"
        and any(e.get("category") == "EMPTY_RESPONSE" for e in r_e3["results"]["errors"])
        and any(x.get("outcome") == "EMPTY_RESPONSE"
                for x in r_e3["results"]["results"] if x.get("phase") == "tools/call"))

    def rpc_transport(payload):
        if payload.get("method") == "initialize":
            return {"result": {}}
        return {"error": {"code": -32000, "message": "boom"}}

    r_e4 = ingest_pack(docs, transport=rpc_transport, env=live_env, dry_run=False, confirm=True)
    chk("E4 RPC_ERROR(error 키) → outcome=RPC_ERROR·ok False",
        r_e4["reason"] == "RPC_ERROR" and not r_e4["results"]["ok"]
        and any(x.get("outcome") == "RPC_ERROR"
                for x in r_e4["results"]["results"] if x.get("phase") == "tools/call"))

    chk("E5 _classify_exception 단위: 403/500/RuntimeError",
        _classify_exception(_HTTPLike(403)) == "AUTH_FAILED"
        and _classify_exception(_HTTPLike(500)) == "HTTP_ERROR:500"
        and _classify_exception(RuntimeError("x")) == "TRANSPORT_ERROR:RuntimeError")

    chk("E6 _classify_response 단위: None/{}/error/isError True/False",
        _classify_response(None) == (False, "EMPTY_RESPONSE")
        and _classify_response({}) == (False, "EMPTY_RESPONSE")
        and _classify_response({"error": {"code": -1}}) == (False, "RPC_ERROR")
        and _classify_response({"result": {"isError": True}}) == (False, "TOOL_ERROR")
        and _classify_response({"result": {"isError": False}}) == (True, "OK"))

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE=" + ("GO" if passed == total else "NO-GO"))
    return passed == total


def main(argv=None):
    ap = argparse.ArgumentParser(prog="binggu_cloud_ingest_wire")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="(기본) 계획만 — 네트워크 0")
    ap.add_argument("--live", action="store_true",
                    help="실 호출(BINGGU_CLOUD_INGEST=1 + transport 둘 다 충족시에만)")
    ap.add_argument("--workflow", action="store_true", help="ingest 후 workflow_manage create append")
    a = ap.parse_args(argv)

    if a.selftest:
        return 0 if _selftest() else 1

    # CLI 단독 실행은 안내만(실 입력 번들·transport 없음 — 우발 네트워크 0)
    print("binggu_cloud_ingest_wire — topic_to_pack 통합용 래퍼.")
    print("  검증:    python binggu_cloud_ingest_wire.py --selftest")
    print("  진입점:  ingest_pack(pack_or_documents, dry_run=True)  # 기본 계획만(네트워크 0)")
    print("  live:    dry_run=False AND confirm=True AND env BINGGU_CLOUD_INGEST=1 AND transport 주입 (owner GO)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
