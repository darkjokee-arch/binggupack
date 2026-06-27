"""binggu_provider_bench — 검색 그물(SearchProvider) 후보 비교 측정 하니스(PoC).

4-CLI 토론 결정: "본진 단정 X → 같은 주제로 실측 비교 후 데이터로 결정".
같은 query 집합을 여러 provider 에 던져 성공률·결과수·latency·빈결과율·차단/에러를 표로.
provider 계약(B 지적 반영): {url, title, snippet, provider, rank, source, fetched_at, error_type}.

provider 어댑터(키/인프라 준비된 것만 측정):
  - ddgs        : pip ddgs(다중엔진·primp 봇차단우회). 키 0.
  - searxng     : self-host 메타서치 JSON API. 도커 필요(SEARXNG_URL).
  - serper      : Google SERP API(유료·무료체험). SERPER_API_KEY.
  - naver       : 네이버 검색 API(한국어). NAVER_CLIENT_ID/SECRET.
실 네트워크 호출(측정 목적). 키 미설정/인프라 부재 provider 는 자동 skip(NOT_CONFIGURED).
"""
import os
import json
import time
import urllib.request
from urllib.parse import urlencode

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ── provider 어댑터 — 각자 [{url,title,snippet}] 반환(공통 계약) ───────
def p_ddgs(query, limit=10):
    from ddgs import DDGS
    with DDGS() as d:
        res = d.text(query, max_results=limit)
    return [{"url": r.get("href"), "title": r.get("title"), "snippet": r.get("body")} for r in res]


def p_searxng(query, limit=10):
    base = os.environ.get("SEARXNG_URL", "http://localhost:8080")
    url = base.rstrip("/") + "/search?" + urlencode({"q": query, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return [{"url": r.get("url"), "title": r.get("title"), "snippet": r.get("content")}
            for r in data.get("results", [])][:limit]


def p_serper(query, limit=10):
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        raise RuntimeError("NOT_CONFIGURED:SERPER_API_KEY")
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=json.dumps({"q": query, "num": limit}).encode("utf-8"),
        headers={"X-API-KEY": key, "Content-Type": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return [{"url": o.get("link"), "title": o.get("title"), "snippet": o.get("snippet")}
            for o in data.get("organic", [])][:limit]


def p_naver(query, limit=10):
    cid = os.environ.get("NAVER_CLIENT_ID")
    sec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and sec):
        raise RuntimeError("NOT_CONFIGURED:NAVER_CLIENT_ID/SECRET")
    url = "https://openapi.naver.com/v1/search/webkr.json?" + urlencode({"query": query, "display": limit})
    req = urllib.request.Request(url, headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec})
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return [{"url": i.get("link"), "title": i.get("title"), "snippet": i.get("description")}
            for i in data.get("items", [])][:limit]


def p_tavily(query, limit=10):
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("NOT_CONFIGURED:TAVILY_API_KEY")
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps({"query": query, "max_results": limit}).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=25).read())
    return [{"url": r.get("url"), "title": r.get("title"), "snippet": r.get("content")}
            for r in data.get("results", [])][:limit]


PROVIDERS = {"ddgs": p_ddgs, "searxng": p_searxng, "serper": p_serper,
             "naver": p_naver, "tavily": p_tavily}


def _is_korean(q):
    import re
    return bool(re.search(r"[가-힣]", q))


def bench_one(name, fn, queries, limit=10):
    rows = []
    for q in queries:
        t0 = time.time()
        try:
            res = fn(q, limit)
            err = None
        except Exception as e:
            res, err = [], (type(e).__name__ + ":" + str(e))[:100]
        dt = round((time.time() - t0) * 1000)
        # 결과 품질 신호: 비어있지 않은 url 비율, snippet 보유율
        valid = [r for r in res if r.get("url")]
        with_snip = [r for r in valid if (r.get("snippet") or "").strip()]
        rows.append({"query": q, "kr": _is_korean(q), "n": len(valid),
                     "latency_ms": dt, "empty": len(valid) == 0, "error": err,
                     "snippet_rate": round(len(with_snip) / max(1, len(valid)), 2),
                     "sample": [r.get("url") for r in valid[:2]]})
    ok = [r for r in rows if not r["error"]]
    krok = [r for r in ok if r["kr"]]
    not_configured = any(r["error"] and "NOT_CONFIGURED" in r["error"] for r in rows)
    return {
        "provider": name,
        "status": "NOT_CONFIGURED" if not_configured else "MEASURED",
        "queries": len(queries),
        "success_rate": round(len(ok) / len(queries), 2),
        "avg_results": round(sum(r["n"] for r in ok) / max(1, len(ok)), 1),
        "avg_latency_ms": round(sum(r["latency_ms"] for r in ok) / max(1, len(ok))),
        "empty_rate": round(sum(1 for r in rows if r["empty"]) / len(queries), 2),
        "snippet_rate": round(sum(r["snippet_rate"] for r in ok) / max(1, len(ok)), 2),
        "kr_avg_results": round(sum(r["n"] for r in krok) / max(1, len(krok)), 1),
        "error_types": sorted(set(r["error"].split(":")[0] for r in rows if r["error"])),
        "rows": rows,
    }


DEFAULT_QUERIES = [
    "입찰 가격 예측 모델",
    "공동주택 하자보수 절차",
    "조달청 전자입찰 적격심사",
    "산업안전보건 위험성평가",
    "한국 부동산 시장 전망 2026",
    "machine learning model deployment best practices",
    "rust async runtime comparison",
    "climate change adaptation policy",
    "vector database benchmark 2026",
    "open source LLM agent framework",
]


def run_bench(queries=None, providers=None, limit=10):
    queries = queries or DEFAULT_QUERIES
    names = providers or list(PROVIDERS.keys())
    out = []
    for name in names:
        if name not in PROVIDERS:
            continue
        out.append(bench_one(name, PROVIDERS[name], queries, limit))
    return {"n_queries": len(queries), "kr_queries": sum(1 for q in queries if _is_korean(q)),
            "results": out}


def print_table(bench):
    print("\n검색 그물 비교 (질의 %d개·한국어 %d개)\n" % (bench["n_queries"], bench["kr_queries"]))
    hdr = ("provider", "status", "성공률", "평균결과", "한국어결과", "지연ms", "빈결과율", "snippet율")
    print("| %-8s | %-13s | %-5s | %-7s | %-8s | %-6s | %-7s | %-7s |" % hdr)
    print("|" + "-" * 86 + "|")
    for r in bench["results"]:
        print("| %-8s | %-13s | %-5s | %-7s | %-8s | %-6s | %-7s | %-7s |" % (
            r["provider"], r["status"], r["success_rate"], r["avg_results"],
            r["kr_avg_results"], r["avg_latency_ms"], r["empty_rate"], r["snippet_rate"]))
        if r["error_types"]:
            print("    errors: %s" % r["error_types"])


if __name__ == "__main__":
    import sys
    sel = None
    for a in sys.argv[1:]:
        if a.startswith("--providers="):
            sel = a.split("=", 1)[1].split(",")
    b = run_bench(providers=sel)
    print_table(b)
