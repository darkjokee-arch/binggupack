# -*- coding: utf-8 -*-
"""binggupack.pack.cloud_query_wire — 트랙 B: OpenCrab 클라우드 read 조회 래퍼(egress-only).

역할: 로컬 MCP(openbinggu-local)가 OpenCrab 클라우드에 read 전용 조회(query/search/status)를
JSON-RPC tools/call 로 넘기는 얇은 래퍼. cloud_ingest_wire 의 설정/전송/세션/분류 로직을
import 재사용(중복 0). write RPC 는 이 모듈에서 **구조적으로 생성되지 않는다**.

안전 불변 (전부 _selftest 로 증명):
  - read 화이트리스트: _READ_TOOLS = {opencrab_query, opencrab_search_packs,
    opencrab_search_nodes, opencrab_status}. build_query_payload 이 이 집합 밖 tool
    (ingest_text/pack_update/pack_qa/workflow_manage 등)은 REJECT — write payload 미생성.
  - 응답 PII 마스킹: 반환 텍스트는 binggupack.safety.pii.batch_redact 로 마스킹한 뒤에만 노출
    (raw 클라우드 응답 원문 노출 0). scan_residual_pii 로 잔존 kind 보고. 마스킹 실패 =
    안전 판정 불가 → fail-closed(text 비움).
  - graceful/raise 0: NO_CLOUD_CONFIG/NO_TOKEN/NO_TRANSPORT/TRANSPORT_ERROR 를 typed dict 로
    흡수. transport 주입형(_selftest 는 mock transport — 실 네트워크 0).
  - URL/토큰 하드코딩 0·평문 0: load_cloud_config(env/config) 재사용. 토큰은 fingerprint(sha8/len)만.
  - top_k/limit 범위 클램프(1..50)·비정수 제거로 구조 오염/과대 요청 방지.

진입점: run_query(tool, args, transport=..., env=...)  # transport None 이면 NO_TRANSPORT(네트워크 0)
"""
import argparse
import json
import sys

# cloud_ingest_wire 의 순수 정본을 import 재사용(중복 0). 이 import 는 정본이 자기 위치에서
# scripts/ 를 sys.path 에 얹어(bare-name 의존) 부수효과가 있으나 이미 확립된 경로다.
from binggupack.pack.cloud_ingest_wire import (
    load_cloud_config,
    default_http_transport,
    run_mcp_session,
)

# read 전용 화이트리스트 — 이 집합 밖은 build_query_payload 이 REJECT(write RPC payload 미생성).
_READ_TOOLS = frozenset({
    "opencrab_query",
    "opencrab_search_packs",
    "opencrab_search_nodes",
    "opencrab_search_documents",   # 질의확장 lexical 팩검색(evidence chunk 원문 top-k·score 반환)
    "opencrab_status",
})

# 명시 write 도구(문서/디버깅용 — build_query_payload 은 화이트리스트 방식이라 이 목록에 의존 안 함).
# egress-only 원칙: 아래 도구는 어떤 경로로도 payload 로 생성되지 않는다.
_WRITE_TOOLS = frozenset({
    "opencrab_ingest_text",
    "opencrab_pack_update",
    "opencrab_pack_qa",       # ★write 가능(assess_and_update/reverse_ingest) — 절대 노출 금지
    "opencrab_workflow_manage",
})

DEFAULT_CLIENT = "binggupack-cloud-query"
_MIN_K, _MAX_K = 1, 50


def _clamp_args(args):
    """top_k/limit 범위 클램프(1..50)·비정수/bool 제거. 나머지 인자는 보존. raise 0."""
    out = dict(args or {})
    for k in ("top_k", "limit"):
        if k in out:
            v = out[k]
            if isinstance(v, bool) or not isinstance(v, int):
                out.pop(k, None)   # 비정수/bool 은 구조 오염 방지 위해 제거
            else:
                out[k] = max(_MIN_K, min(_MAX_K, v))
    return out


def build_query_payload(tool, args=None, *, rpc_id=1):
    """read 전용 tools/call payload 빌더(핵심 안전 게이트).

    tool in _READ_TOOLS  → ({jsonrpc,id,method:'tools/call',params:{name,arguments}}, None)
    tool 화이트리스트 밖 → (None, 'TOOL_NOT_ALLOWED:<tool>')  ← write payload 구조적 미생성.
    raise 0.
    """
    if tool not in _READ_TOOLS:
        return None, "TOOL_NOT_ALLOWED:" + str(tool)
    clean = _clamp_args(args)
    return ({"jsonrpc": "2.0", "id": int(rpc_id), "method": "tools/call",
             "params": {"name": tool, "arguments": clean}}, None)


def _extract_and_mask(resp):
    """JSON-RPC tools/call 응답 → PII 마스킹 텍스트(raw 노출 0). raise 0.

    result.content[].text 를 모아 batch_redact 로 마스킹. scan_residual_pii 로 잔존 kind 보고.
    마스킹 자체 실패(import/런타임) = 안전 판정 불가 → fail-closed(text 비움).
    반환: {text, pii_hits, residual, masked}
    """
    texts = []
    try:
        result = resp.get("result") if isinstance(resp, dict) else None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    texts.append(c["text"])
        elif isinstance(result, str):
            texts.append(result)
    except Exception:   # noqa — 응답 구조 손상도 흡수(빈 텍스트로)
        texts = []
    combined = "\n".join(texts)
    try:
        from binggupack.safety.pii import batch_redact, scan_residual_pii
        redacted, hits, _review = batch_redact(combined, field_name="cloud_response")
        residual = scan_residual_pii(redacted)
        return {"text": redacted, "pii_hits": hits, "residual": residual, "masked": True}
    except Exception as ex:  # noqa — 마스킹 실패 시 raw 노출 금지(fail-closed: 텍스트 비움)
        return {"text": "", "pii_hits": None,
                "residual": ["MASK_FAILED:" + type(ex).__name__], "masked": False}


def _extract_search_evidence(resp, min_score=0.0):
    """opencrab_search_documents JSON-RPC 응답 → score 하한 필터 + PII 마스킹된 evidence 리스트.

    content[].text 의 JSON(evidence 배열)을 파싱 → 각 evidence 의 score(retrieval.score 폴백)를
    보고 min_score 미만은 버림. 남은 chunk 원문은 batch_redact 로 마스킹한 뒤에만 노출(raw 0).
    마스킹 import/런타임 실패 = 안전 판정 불가 → fail-closed(빈 evidence). raise 0.
    반환: {evidence:[{text,score,chunk_id,pack_title}], count, filtered_out, min_score, residual, masked}
    """
    try:
        result = resp.get("result") if isinstance(resp, dict) else None
        content = result.get("content") if isinstance(result, dict) else None
        texts = []
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    texts.append(c["text"])
        raw = "\n".join(texts)
        doc = json.loads(raw) if raw.strip() else {}
    except Exception:   # noqa — 응답/JSON 손상도 흡수(빈 문서로)
        doc = {}
    ev_in = doc.get("evidence") if isinstance(doc, dict) else None
    if not isinstance(ev_in, list):
        ev_in = []
    # ── telemetry 보존(Fable5 C2): 벡터 timeout(warnings)·스캔 규모·적용 스코프를 버리지 않고 노출 →
    #    stale 스코프/벡터 소거를 호출자(Claude)가 관측 가능. 숫자/메타라 PII 아님(마스킹 불필요). ──
    _retr = doc.get("retrieval") if isinstance(doc, dict) and isinstance(doc.get("retrieval"), dict) else {}
    _tel = {"scanned": (doc.get("scanned") if isinstance(doc, dict) and isinstance(doc.get("scanned"), int)
                        else _retr.get("scanned")),
            "vector_candidates": _retr.get("vector_candidates"),
            "lexical_candidates": _retr.get("lexical_candidates"),
            "warnings": _retr.get("warnings"),
            "pack_scope": (doc.get("pack_scope") if isinstance(doc, dict)
                           and isinstance(doc.get("pack_scope"), dict) else None)}
    try:
        from binggupack.safety.pii import batch_redact, scan_residual_pii
    except Exception as ex:  # noqa — 마스킹 불가 → raw 노출 금지(fail-closed)
        return {"evidence": [], "count": 0, "filtered_out": 0, "min_score": min_score,
                "residual": ["MASK_FAILED:" + type(ex).__name__], "masked": False, **_tel}
    out, filtered, residual_all = [], 0, []
    for e in ev_in:
        if not isinstance(e, dict):
            continue
        score = e.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            r = e.get("retrieval") if isinstance(e.get("retrieval"), dict) else {}
            sc = r.get("score")
            score = sc if isinstance(sc, (int, float)) and not isinstance(sc, bool) else 0.0
        if float(score) < float(min_score):
            filtered += 1
            continue
        text = e.get("text") if isinstance(e.get("text"), str) else ""
        red, _hits, _review = batch_redact(text, field_name="cloud_search_evidence")
        residual_all.extend(scan_residual_pii(red))
        meta = e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
        out.append({"text": red, "score": round(float(score), 4),
                    "chunk_id": meta.get("chunk_id"),
                    "pack_title": meta.get("cloud_pack_title")})
    out.sort(key=lambda x: -x["score"])
    return {"evidence": out, "count": len(out), "filtered_out": filtered,
            "min_score": min_score, "residual": residual_all, "masked": True, **_tel}


def run_query(tool, args=None, *, transport=None, env=None, config_path=None, home=None,
              extractor=None):
    """OpenCrab 클라우드 read 조회. initialize + 단일 tools/call. raise 0·네트워크 게이트.

    게이트(전부 통과해야 실 네트워크 1건):
      - 화이트리스트 밖 tool  → reason='TOOL_NOT_ALLOWED:<tool>' (payload 미생성·네트워크 0)
      - url 부재             → 'NO_CLOUD_CONFIG'
      - token 부재           → 'NO_TOKEN'
      - transport None       → 'NO_TRANSPORT'  ← 주입 없으면 네트워크 0(자동연결 안 함)
      - transport 예외/세션오류 → 'TRANSPORT_ERROR:*' / 세션 카테고리(RPC_ERROR/TOOL_ERROR 등)
    반환(typed·raise 0): {ok, tool, mode:'read', reason, text?, pii_hits?, residual?, masked?,
                         token_fingerprint, source}. 응답 text 는 PII 마스킹 후에만 노출.
    """
    try:
        payload, reject = build_query_payload(tool, args)
        cfg = load_cloud_config(env=env, config_path=config_path, home=home)
        base = {"tool": tool, "mode": "read",
                "token_fingerprint": cfg["token_fingerprint"], "source": cfg["source"]}
        if payload is None:                          # 화이트리스트 밖 → write payload 미생성·네트워크 0
            base.update({"ok": False, "reason": reject})
            return base
        if not cfg["url"]:
            base.update({"ok": False, "reason": "NO_CLOUD_CONFIG"})
            return base
        if cfg.get("reason") == "NO_TOKEN":
            base.update({"ok": False, "reason": "NO_TOKEN"})
            return base
        if transport is None:
            base.update({"ok": False, "reason": "NO_TRANSPORT"})
            return base

        session = run_mcp_session(transport, [payload], client_name=DEFAULT_CLIENT)
        call_results = [r for r in session.get("results", []) if r.get("phase") == "tools/call"]
        if not session.get("ok") or not call_results:
            cats = session.get("error_categories") or []
            reason = cats[0] if len(cats) == 1 else ("SESSION_ERROR" if cats else "EMPTY_RESPONSE")
            base.update({"ok": False, "reason": reason})
            return base
        ext = extractor or _extract_and_mask
        extracted = ext(call_results[0].get("response"))
        base.update({"ok": True, "reason": None, **extracted})
        return base
    except Exception as ex:  # noqa — 방어적 최상위 흡수(상위 raise 0 보장)
        return {"ok": False, "tool": tool, "mode": "read",
                "reason": "TRANSPORT_ERROR:" + type(ex).__name__,
                "token_fingerprint": "none", "source": "none"}


def run_search(query, *, top_k=5, min_score=0.0, package_id=None, package_ids=None,
               pack_query=None, transport=None, env=None, config_path=None, home=None):
    """하이브리드 의미검색: opencrab_search_documents 래핑 + score 하한 필터.

    ★서버 retrieval = lexical(BM25) + vector fusion + graph. 2026-07-08 서버가 벡터 다리를 배선
    (vector_candidates 0→32 실측·evidence sources 에 "vector" 등장·저장 openai-1536 임베딩을 낮은
    가중으로 fusion). 빙구팩은 코드 로직 변경 0 으로 hybrid evidence 를 그대로 수신한다.
    query 는 여전히 Claude 가 동의어 확장한 문자열을 권장한다 — 현 fusion 은 벡터 가중이 낮아(≈0.3)
    lexical 기여가 최종 순위를 지배하므로 확장이 lexical recall 을 보강한다(실측: raw "빠른 결정" 은
    놓치나 "빠른 의사결정 신속 판단 직감" 확장은 정답 top-1). 벡터 가중 상향은 서버측 튜닝 사안.
    min_score 미만 evidence 는 근거에서 배제(off-topic 오공급 방지·Fable5 #3). read egress-only, evidence
    텍스트는 PII 마스킹 후에만 노출. 게이트(url/token/transport)는 run_query 재사용. raise 0.
    반환: run_query typed dict + {evidence:[{text,score,chunk_id,pack_title}], count, filtered_out,
          min_score, residual, masked}.
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "tool": "opencrab_search_documents", "mode": "read",
                "reason": "query_required", "token_fingerprint": "none", "source": "none"}
    args = {"query": q, "limit": top_k}
    # 스코프 우선순위: package_ids(복수) > package_id(단수) > pack_query(접두어). 셋 다 없으면 무필터.
    # ★package_id 단수는 서버가 다중 pack_key 통합 팩을 scanned=0 처리 → 항상 package_ids(복수 배열)로
    # 전송(2026-07-08 실측: 단수 scanned 0 vs 복수 scanned 1227). pack_query 는 팩 title 접두어 스코프
    # 축소(2026-07-09 실측: "Binggu Person" 접두어 하나로 4파트+판단팩 5팩 scanned 1214·vector 32·id churn 면역).
    _ids = None
    if isinstance(package_ids, (list, tuple)):
        _ids = [str(p).strip() for p in package_ids if isinstance(p, str) and p.strip()]
    if not _ids and isinstance(package_id, str) and package_id.strip():
        _ids = [package_id.strip()]
    if _ids:
        args["package_ids"] = _ids[:32]   # 개수 상한(구조 오염 방지·Fable5 R4)
    if isinstance(pack_query, str) and pack_query.strip():
        args["pack_query"] = pack_query.strip()

    def _ext(resp):
        return _extract_search_evidence(resp, min_score=min_score)

    return run_query("opencrab_search_documents", args, transport=transport,
                     env=env, config_path=config_path, home=home, extractor=_ext)


# __all__ 에 재노출 심볼 명시 → 핸들러가 cloud_query_wire.{load_cloud_config,default_http_transport}
# 로 read-only 설정/transport 를 구성할 수 있게 하고, ruff F401(미사용 import) 오탐도 억제.
__all__ = ["run_query", "run_search", "build_query_payload", "load_cloud_config",
           "default_http_transport", "run_mcp_session", "DEFAULT_CLIENT",
           "_READ_TOOLS", "_WRITE_TOOLS", "_clamp_args", "_extract_and_mask",
           "_extract_search_evidence"]


# ───────────────────────────── selftest (mock transport · 네트워크 0) ─────────────────────────────
def _selftest():
    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    live_env = {"BINGGU_CLOUD_MCP_URL": "https://x.example/mcp",
                "BINGGU_CLOUD_MCP_TOKEN": "tok-querysecret-abcdef123456"}

    # ── build_query_payload: read 화이트리스트 정확 ──
    p_q, r_q = build_query_payload("opencrab_query", {"query": "여행 팁", "top_k": 5})
    chk("B1 opencrab_query → tools/call·name·arguments.query/top_k",
        r_q is None and p_q["method"] == "tools/call"
        and p_q["params"]["name"] == "opencrab_query"
        and p_q["params"]["arguments"]["query"] == "여행 팁"
        and p_q["params"]["arguments"]["top_k"] == 5)
    for t in ("opencrab_search_packs", "opencrab_search_nodes", "opencrab_status"):
        pp, rr = build_query_payload(t, {})
        chk("B2 read 화이트리스트 통과: " + t, rr is None and pp["params"]["name"] == t)

    # ── build_query_payload: write/기타 tool REJECT(핵심 안전) — payload 구조적 미생성 ──
    for t in ("opencrab_ingest_text", "opencrab_pack_update", "opencrab_pack_qa",
              "opencrab_workflow_manage", "opencrab_run_workflow", "opencrab_project_run"):
        pp, rr = build_query_payload(t, {"title": "x", "content": "y"})
        chk("B3 write/기타 tool REJECT(payload=None): " + t,
            pp is None and rr == "TOOL_NOT_ALLOWED:" + t)

    # ── top_k/limit 범위 클램프 ──
    p_hi, _ = build_query_payload("opencrab_query", {"query": "x", "top_k": 9999})
    p_lo, _ = build_query_payload("opencrab_search_packs", {"query": "x", "limit": -5})
    p_bad, _ = build_query_payload("opencrab_query", {"query": "x", "top_k": "big"})
    chk("B4 top_k/limit 클램프(1..50)·비정수 제거",
        p_hi["params"]["arguments"]["top_k"] == _MAX_K
        and p_lo["params"]["arguments"]["limit"] == _MIN_K
        and "top_k" not in p_bad["params"]["arguments"])

    # ── run_query graceful 게이트 (네트워크 0) ──
    # 격리: 운영 home 에 실제 config 가 있어도(정상 사용자 상태) 부재 경로를 명시 주입.
    import os
    import tempfile
    _absent = os.path.join(tempfile.gettempdir(), "binggu_selftest_absent", "cloud_ingest.json")
    r_nc = run_query("opencrab_query", {"query": "x"}, transport=None, env={}, config_path=_absent)
    chk("G1 config 없음 → NO_CLOUD_CONFIG·ok False",
        r_nc["reason"] == "NO_CLOUD_CONFIG" and r_nc["ok"] is False)
    r_ntk = run_query("opencrab_query", {"query": "x"},
                      transport=lambda p: {}, env={"BINGGU_CLOUD_MCP_URL": "https://x.example/mcp"})
    chk("G2 token 없음 → NO_TOKEN", r_ntk["reason"] == "NO_TOKEN")
    r_ntr = run_query("opencrab_query", {"query": "x"}, transport=None, env=live_env)
    chk("G3 transport None + config 有 → NO_TRANSPORT(자동연결 안 함·네트워크 0)",
        r_ntr["reason"] == "NO_TRANSPORT")
    calls = {"n": 0}

    def _spy(p):
        calls["n"] += 1
        return {}

    r_rej = run_query("opencrab_ingest_text", {"content": "x"}, transport=_spy, env=live_env)
    chk("G4 write tool → TOOL_NOT_ALLOWED·transport 호출 0(네트워크 진입 0)",
        r_rej["reason"] == "TOOL_NOT_ALLOWED:opencrab_ingest_text"
        and r_rej["ok"] is False and calls["n"] == 0)

    # ── run_query live (mock transport) → initialize + tools/call, 응답 PII 마스킹 ──
    seq = []

    def mock_transport(payload):
        seq.append(payload.get("method"))
        if payload.get("method") == "initialize":
            return {"result": {}}
        return {"result": {"content": [{"type": "text",
                "text": "추천 팩: 연락처 " + "010-" "1234-5678" + ", 담당 test" "@example.com 참고"}]}}

    r_live = run_query("opencrab_query", {"query": "여행"}, transport=mock_transport, env=live_env)
    chk("L1 live → initialize 1회 + tools/call 1회·ok True·reason None",
        seq == ["initialize", "tools/call"] and r_live["ok"] and r_live["reason"] is None)
    chk("L2 응답 PII 마스킹(raw 전화/이메일 노출 0·[REDACTED] 치환)",
        ("010-" "1234-5678") not in r_live["text"] and ("test" "@example.com") not in r_live["text"]
        and r_live["masked"] is True and "[REDACTED" in r_live["text"])
    chk("L3 마스킹 후 잔존 PII scan 0", r_live["residual"] == [])

    # ── 토큰 평문 미노출(fingerprint 만) ──
    chk("T1 결과 어디에도 raw 토큰 없음·fingerprint sha8",
        "tok-querysecret-abcdef123456" not in json.dumps(r_live, ensure_ascii=False)
        and r_live["token_fingerprint"].startswith("sha8:") and "len=" in r_live["token_fingerprint"])

    # ── transport 예외 흡수(raise 0) ──
    def boom(payload):
        raise RuntimeError("net_down")

    r_err = run_query("opencrab_query", {"query": "x"}, transport=boom, env=live_env)
    chk("E1 transport 예외 → typed 흡수(raise 0)·ok False",
        isinstance(r_err, dict) and r_err["ok"] is False
        and r_err["reason"].startswith(("NETWORK_ERROR", "TRANSPORT_ERROR", "SESSION_ERROR")))

    # ── RPC error 응답 흡수 ──
    def rpc_err(payload):
        if payload.get("method") == "initialize":
            return {"result": {}}
        return {"error": {"code": -32000, "message": "boom"}}

    r_rpc = run_query("opencrab_query", {"query": "x"}, transport=rpc_err, env=live_env)
    chk("E2 RPC error 응답 → typed 흡수·ok False", r_rpc["ok"] is False and r_rpc["reason"] == "RPC_ERROR")

    # ── 응답 구조 손상 → fail-closed(빈 텍스트·raise 0) ──
    def junk(payload):
        if payload.get("method") == "initialize":
            return {"result": {}}
        return {"result": {"content": "not-a-list"}}

    r_junk = run_query("opencrab_query", {"query": "x"}, transport=junk, env=live_env)
    chk("E3 응답 구조 손상 → text 빈 문자열·ok True·raise 0",
        r_junk["ok"] is True and r_junk["text"] == "")

    # ── S: 질의확장 lexical 팩검색(opencrab_search_documents + score 하한 필터) ──
    p_sd, r_sd = build_query_payload("opencrab_search_documents", {"query": "직감", "limit": 5})
    chk("S1 search_documents read 화이트리스트 통과",
        r_sd is None and p_sd["params"]["name"] == "opencrab_search_documents")

    _mock_sd = {"result": {"content": [{"type": "text", "text": json.dumps({"evidence": [
        {"text": "높은 점수 chunk", "score": 0.40,
         "metadata": {"chunk_id": "chunk:01:01", "cloud_pack_title": "P"}},
        {"text": "낮은 점수 chunk", "score": 0.05, "metadata": {"chunk_id": "chunk:02:01"}},
        {"text": "연락처 " + "010-" "1234-5678", "retrieval": {"score": 0.20},
         "metadata": {"chunk_id": "chunk:03:01"}},
    ]})}]}}
    ex_hi = _extract_search_evidence(_mock_sd, min_score=0.10)
    chk("S2 score<min_score(0.10) 필터 — 0.05 배제·2건·filtered 1",
        ex_hi["count"] == 2 and ex_hi["filtered_out"] == 1)
    chk("S3 score 내림차순 정렬(0.40 먼저)·chunk_id 보존",
        ex_hi["evidence"][0]["score"] == 0.40 and ex_hi["evidence"][0]["chunk_id"] == "chunk:01:01")
    chk("S4 retrieval.score 폴백(0.20) 인식·evidence 텍스트 PII 마스킹",
        any(e["score"] == 0.20 for e in ex_hi["evidence"])
        and all(("010-" "1234-5678") not in e["text"] for e in ex_hi["evidence"]))

    def mock_search_transport(payload):
        if payload.get("method") == "initialize":
            return {"result": {}}
        return {"result": {"content": [{"type": "text", "text": json.dumps({"evidence": [
            {"text": "결론부터 짧게", "score": 0.19, "metadata": {"chunk_id": "chunk:12:01"}},
            {"text": "노이즈", "score": 0.02, "metadata": {"chunk_id": "chunk:41:01"}},
        ]})}]}}

    r_search = run_search("보고 분량 간결 결론 요약", top_k=5, min_score=0.10,
                          transport=mock_search_transport, env=live_env)
    chk("S5 run_search live → ok·min_score 필터(0.02 배제·1건)·chunk:12 남음",
        r_search["ok"] and r_search["count"] == 1
        and r_search["evidence"][0]["chunk_id"] == "chunk:12:01")

    r_snoq = run_search("  ", transport=mock_search_transport, env=live_env)
    chk("S6 run_search query 공백 → query_required(네트워크 0)",
        r_snoq["ok"] is False and r_snoq["reason"] == "query_required")

    # S7 — pack_query/package_ids 가 payload arguments 에 실림(스코프 전송·빈 원소 제거)
    _cap = {}

    def cap_transport(payload):
        if payload.get("method") == "initialize":
            return {"result": {}}
        _cap["args"] = payload.get("params", {}).get("arguments", {})
        return {"result": {"content": [{"type": "text", "text": json.dumps({"evidence": []})}]}}

    run_search("빠른 결정", pack_query="Binggu Person", package_ids=["u1", " ", "u2"],
               transport=cap_transport, env=live_env)
    chk("S7 pack_query·package_ids arguments 전송(빈 원소 제거)",
        _cap["args"].get("pack_query") == "Binggu Person"
        and _cap["args"].get("package_ids") == ["u1", "u2"])

    # S8 — telemetry(scanned/vector_candidates/warnings/pack_scope) 보존(Fable5 C2 관측성)
    _mock_tel = {"result": {"content": [{"type": "text", "text": json.dumps({
        "scanned": 1214,
        "evidence": [{"text": "x", "score": 0.3, "metadata": {"chunk_id": "c:1"}}],
        "retrieval": {"vector_candidates": 32, "lexical_candidates": 468,
                      "warnings": ["vector: canceling statement due to statement timeout"]},
        "pack_scope": {"packages": 5}})}]}}
    ex_tel = _extract_search_evidence(_mock_tel, min_score=0.0)
    chk("S8 telemetry 보존: scanned/vector_candidates/warnings/pack_scope",
        ex_tel["scanned"] == 1214 and ex_tel["vector_candidates"] == 32
        and bool(ex_tel["warnings"]) and ex_tel["pack_scope"] == {"packages": 5})

    total, passed = len(ok), sum(ok)
    print("\n=== %d/%d ===" % (passed, total))
    print("RESULT: %d/%d PASS" % (passed, total))
    print("GATE=" + ("GO" if passed == total else "NO-GO"))
    return passed == total


def main(argv=None):
    ap = argparse.ArgumentParser(prog="binggu_cloud_query_wire")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if _selftest() else 1
    print("binggu_cloud_query_wire — OpenCrab 클라우드 read 조회 래퍼(egress-only).")
    print("  검증:   python -m binggupack.pack.cloud_query_wire --selftest")
    print("  진입점: run_query(tool, args, transport=..., env=...)  # read 화이트리스트만·transport None=NO_TRANSPORT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
