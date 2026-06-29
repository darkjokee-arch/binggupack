"""binggu_discover — 주제→소스 자동발견(source discovery).

빙구팩 2차 라인의 빠진 앞단. "주제 입력 → 관련 소스 후보 발견 → 랭킹 → discover_candidates.json".
fetch/파싱/적재는 절대 안 함(harvest 책임). 이 모듈은 **후보 목록 생성까지만**.

설계 원칙(4-CLI 토론 + owner 조건부 GO 반영):
  - provider interface 로 검색 그물 추상화(DDG 첫 그물, Brave/공식API/RSS 교체 가능). 검색은 중심 아님.
  - discover_candidates.json(후보) 과 harvest_sources.json(승인 화이트리스트)을 **물리 분리**.
  - 보안 vet 비협상: harvest 의 classify_source_pointer 재사용(SSRF/사설IP/우회표기 차단)
    + IDNA(punycode/homograph) 정규화. dirty/unknown 후보는 제외(fail-closed).
  - 등록(promote)은 harvest.add_source 게이트를 통과시켜서만 — discover 가 화이트리스트를 직접 못 씀.
  - 실 네트워크는 provider runner 주입(기본 실 fetch). selftest 는 mock runner — 실 네트워크 0.
"""
import os
import re
import sys
import json
import time
import hashlib

# harvest 재사용(생산자/소비자 분리) — source_id·등록·공개안전성 게이트를 그대로 빌려 씀.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_harvest as HV  # noqa: E402

DISCOVER_FILE = "discover_candidates.json"

# 도메인 신뢰 seed(bootstrap) — 화이트리스트 0개 상태에서 거친 랭킹 시작점. v2에서 학습/확장.
TRUST_SEED = {
    ".gov": 1.0, ".go.kr": 1.0, ".edu": 0.9, ".ac.kr": 0.9,
    "arxiv.org": 0.9, "github.com": 0.8, "wikipedia.org": 0.8,
    "g2b.go.kr": 1.0, "data.go.kr": 1.0, "nps.or.kr": 0.85,
}
_DEFAULT_TRUST = 0.4


# ── 경로 ──────────────────────────────────────────────────────────────
def discover_path(home=None):
    home = home or HV._home()
    return os.path.join(home, DISCOVER_FILE)


# ── provider interface (조건4 — 교체 가능한 검색 그물) ─────────────────
class SearchProvider:
    """검색 그물 추상. search(query, limit) -> [{"url","title","snippet", (opt)"source"}].
    source = 실제 결과를 낸 엔진명(선택). 미제공 시 discover 가 provider.name 으로 채운다."""
    name = "base"

    def search(self, query, limit=10):
        raise NotImplementedError


class DDGProvider(SearchProvider):
    """DuckDuckGo 무키 HTML endpoint. urllib 표준만(서드파티 0). MVP 첫 그물.

    검색엔진은 봇 UA(harvest 의 binggupack-harvest/1.0)를 anomaly 페이지로 차단하므로
    discover 전용 브라우저 UA 로 GET 한다(harvest fetch 와 분리). runner 주입 시 selftest mock.
    참고: DDG 무키 endpoint 는 비공식·차단/구조변경 위험 → 운영은 공식 API/Brave 권장(provider 교체).
    """
    name = "ddg"
    ENDPOINT = "https://html.duckduckgo.com/html/"
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

    def __init__(self, runner=None):
        self._runner = runner  # runner(url)->{"ok","text"} 주입(selftest mock)

    def search(self, query, limit=10):
        from urllib.parse import urlencode
        url = self.ENDPOINT + "?" + urlencode({"q": query})
        if self._runner is not None:
            r = self._runner(url)
            html = r.get("text") if isinstance(r, dict) and r.get("ok") else None
        else:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": self.UA})
            try:
                html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
            except Exception:
                return []
        if not html:
            return []
        return _parse_ddg_html(html, limit)


class SearxngProvider(SearchProvider):
    """SearXNG self-host 메타서치(70+ 엔진 집계·봇차단 헤지). JSON API → SearchProvider 1:1.
    의존은 외부 도커(SEARXNG_URL). 빙구팩 코드는 순수 urllib GET(서드파티 0). 4-CLI 측정 본진."""
    name = "searxng"

    def __init__(self, base=None, runner=None):
        self.base = (base or os.environ.get("SEARXNG_URL") or "http://localhost:8888").rstrip("/")
        self._runner = runner

    def search(self, query, limit=10):
        from urllib.parse import urlencode
        url = self.base + "/search?" + urlencode({"q": query, "format": "json"})
        if self._runner is not None:
            r = self._runner(url)
            data = r if isinstance(r, dict) else {}
        else:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": DDGProvider.UA})
            try:
                data = json.loads(urllib.request.urlopen(req, timeout=20).read())
            except Exception:
                return []
        return [{"url": x.get("url"), "title": x.get("title"), "snippet": x.get("content"),
                 "source": x.get("engine") or "searxng"}
                for x in (data.get("results") or [])][:limit]


class DdgsProvider(SearchProvider):
    """ddgs 라이브러리(다중엔진·primp 봇차단우회). 현 DDG 스크래핑 MVP 의 무료 후계(폴백)."""
    name = "ddgs"

    def __init__(self, runner=None):
        self._runner = runner

    def search(self, query, limit=10):
        if self._runner is not None:
            return list(self._runner(query, limit) or [])[:limit]
        from ddgs import DDGS  # lazy — 미설치여도 모듈 로드 OK
        with DDGS() as d:
            res = d.text(query, max_results=limit)
        return [{"url": r.get("href"), "title": r.get("title"), "snippet": r.get("body"),
                 "source": "ddgs"} for r in res]


class SerperProvider(SearchProvider):
    """Serper.dev Google SERP API(유료·정확한 글로벌). SERPER_API_KEY 필요.
    키 미설정/오류면 빈 결과 → FallbackProvider 가 다음 그물로(자동 스킵). runner 주입 시 selftest mock."""
    name = "serper"

    def __init__(self, runner=None):
        self._runner = runner

    def search(self, query, limit=10):
        if self._runner is not None:
            return list(self._runner(query, limit) or [])[:limit]
        key = os.environ.get("SERPER_API_KEY")
        if not key:
            return []  # 미설정 → 빈 결과(키 살아나면 자동 활성)
        import urllib.request
        req = urllib.request.Request(
            "https://google.serper.dev/search",
            data=json.dumps({"q": query, "num": limit}).encode("utf-8"),
            headers={"X-API-KEY": key, "Content-Type": "application/json"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception:
            return []
        return [{"url": o.get("link"), "title": o.get("title"),
                 "snippet": o.get("snippet"), "source": "serper"}
                for o in (data.get("organic") or [])][:limit]


class NaverProvider(SearchProvider):
    """네이버 검색 API(한국어 강점). NAVER_CLIENT_ID/SECRET 필요.
    키 미설정/오류면 빈 결과 → 자동 스킵. runner 주입 시 selftest mock."""
    name = "naver"

    def __init__(self, runner=None):
        self._runner = runner

    def search(self, query, limit=10):
        if self._runner is not None:
            return list(self._runner(query, limit) or [])[:limit]
        cid = os.environ.get("NAVER_CLIENT_ID")
        sec = os.environ.get("NAVER_CLIENT_SECRET")
        if not (cid and sec):
            return []  # 미설정 → 빈 결과(키 살아나면 자동 활성)
        from urllib.parse import urlencode
        import urllib.request
        url = ("https://openapi.naver.com/v1/search/webkr.json?"
               + urlencode({"query": query, "display": limit}))
        req = urllib.request.Request(url, headers={
            "X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception:
            return []
        # 네이버 title/description 은 <b> 강조태그 포함 — 정규식만으로 제거(bs4 금지).
        def _strip(s):
            return re.sub(r"<[^>]+>", "", s or "")
        return [{"url": i.get("link"), "title": _strip(i.get("title")),
                 "snippet": _strip(i.get("description")), "source": "naver"}
                for i in (data.get("items") or [])][:limit]


class FallbackProvider(SearchProvider):
    """provider 체인 — 앞에서부터 시도, 결과 있으면 채택(빈 결과/예외는 다음으로). 본진 다운 시 폴백."""
    name = "fallback"

    def __init__(self, providers):
        self.providers = [p for p in providers if p is not None]

    def search(self, query, limit=10):
        for p in self.providers:
            try:
                res = p.search(query, limit)
            except Exception:
                continue
            if res:
                self.name = "fallback:" + p.name
                return res
        self.name = "fallback:none"
        return []


def default_provider():
    """그물 우선순위 — SearXNG(본진·측정 결정) → serper/naver(키 있으면 보조 폴백) → ddgs → DDG(최후).
    URL/키 미설정 그물은 자동 제외(unset 이면 chain 미합류, 살아나면 자동 활성).
    항상 DDGProvider 최후 폴백(키·인프라 0)."""
    chain = []
    if os.environ.get("SEARXNG_URL"):
        chain.append(SearxngProvider())
    if os.environ.get("SERPER_API_KEY"):
        chain.append(SerperProvider())
    if os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET"):
        chain.append(NaverProvider())
    try:
        import ddgs  # noqa: F401
        chain.append(DdgsProvider())
    except Exception:
        pass
    chain.append(DDGProvider())  # 최후 폴백(키/인프라 0)
    return FallbackProvider(chain) if len(chain) > 1 else chain[0]


def _parse_ddg_html(html, limit=10):
    """DDG HTML → [{"url","title","snippet"}]. 정규식만(bs4 금지). uddg redirect 디코드."""
    from urllib.parse import unquote, urlparse, parse_qs
    out = []
    # result__a 링크 + 뒤따르는 result__snippet 를 느슨하게 페어링.
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href, title_html = m.group(1), m.group(2)
        # DDG 리다이렉트 래퍼(//duckduckgo.com/l/?uddg=...) → 실제 url 추출
        if "uddg=" in href:
            try:
                href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
            except Exception:
                pass
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        out.append({"url": href, "title": title, "snippet": ""})
        if len(out) >= limit:
            break
    # snippet 보강(있으면)
    snips = [re.sub(r"<[^>]+>", "", s).strip()
             for s in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)]
    for i, sn in enumerate(snips[:len(out)]):
        out[i]["snippet"] = sn
    return out


# ── 정규화 / vet (보안 비협상) ────────────────────────────────────────
def _idna_normalize(url):
    """host 를 IDNA(punycode)로 정규화 — homograph/유니코드 도메인 위장 차단(C 지적).
    정규화 실패(불법 host)면 None 반환 → 후보 제외."""
    from urllib.parse import urlsplit, urlunsplit
    try:
        sp = urlsplit(url.strip())
        if not sp.scheme or not sp.hostname:
            return None
        host_ascii = sp.hostname.encode("idna").decode("ascii")  # 유니코드→xn-- 정규화
        netloc = host_ascii + (":%d" % sp.port if sp.port else "")
        return urlunsplit((sp.scheme, netloc, sp.path, sp.query, ""))
    except Exception:
        return None


def vet_url(url):
    """보안 vet — 공개 http(s) + classify_source_pointer=clean 만 통과. 반환 (ok, norm_url|reason)."""
    norm = _idna_normalize(url)
    if not norm:
        return False, "IDNA_FAIL"
    if not norm.lower().startswith(("http://", "https://")):
        return False, "NON_HTTP"
    # harvest 의 공개안전성 게이트 재사용(사설IP/localhost/우회표기/내부 차단)
    if HV.SP.classify_source_pointer(norm) != "clean":
        return False, "NOT_PUBLIC"
    return True, norm


# ── kind 추론 / 랭킹 ──────────────────────────────────────────────────
def infer_kind(url):
    u = url.lower()
    if "arxiv.org" in u:
        return "arxiv"
    if "github.com" in u:
        return "github"
    if u.endswith((".rss", ".xml", "/feed", "/rss")) or "/rss" in u or "/feed" in u:
        return "rss"
    return "url"


def _tokens(text):
    return set(re.findall(r"[0-9a-z가-힣]+", str(text or "").lower()))


def _relevance(topic, title, snippet):
    """주제 관련도 = 주제 토큰이 제목+요약에 얼마나 겹치나(결정적·semantic OFF). 0~1."""
    t = _tokens(topic)
    if not t:
        return 0.0
    hay = _tokens(title) | _tokens(snippet)
    return len(t & hay) / len(t)


def _domain_trust(url):
    u = url.lower()
    best = _DEFAULT_TRUST
    for key, val in TRUST_SEED.items():
        if key in u and val > best:
            best = val
    return best


def score_candidate(topic, cand):
    """score = 0.5·relevance + 0.4·domain_trust + 0.1·rank_bonus. 컴포넌트 분해 동봉(B 지적)."""
    rel = _relevance(topic, cand.get("title"), cand.get("snippet"))
    trust = _domain_trust(cand["url"])
    rank_bonus = max(0.0, 1.0 - cand.get("_rank", 0) * 0.1)  # 검색순위 보너스
    comp = {"relevance": round(rel, 3), "domain_trust": round(trust, 3),
            "rank_bonus": round(rank_bonus, 3)}
    score = round(0.5 * rel + 0.4 * trust + 0.1 * rank_bonus, 4)
    return score, comp


# ── discover_candidates.json read/write ───────────────────────────────
def load_discoveries(path=None):
    path = path or discover_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    c = d.get("candidates") if isinstance(d, dict) else d
    return [x for x in (c or []) if isinstance(x, dict) and x.get("url")]


def _write_discoveries(cands, path=None):
    path = path or discover_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"candidates": cands, "ts": time.time()}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


# ── 핵심: discover ────────────────────────────────────────────────────
def discover(topic, provider=None, limit=10, home=None, persist=True, merge=True):
    """주제→소스 후보 발견. provider 검색 → vet → 랭킹 → discover_candidates.json.

    반환 dict(status/topic/candidates/rejected/provider). 후보는 vet 통과(clean)분만.
    """
    provider = provider or default_provider()
    raw = provider.search(topic, limit=limit) or []
    cands, rejected, seen = [], [], set()
    for i, hit in enumerate(raw):
        ok, res = vet_url(hit.get("url", ""))
        if not ok:
            rejected.append({"url": hit.get("url"), "reason": res})
            continue
        norm = res
        sid = HV.source_id_for(norm)
        if sid in seen:  # dedup(같은 정규화 url 1건)
            continue
        seen.add(sid)
        cand = {"source_id": sid, "url": norm, "kind": infer_kind(norm),
                "title": hit.get("title", ""), "snippet": hit.get("snippet", ""),
                "provider": provider.name, "source": hit.get("source") or provider.name,
                "query_origin": topic, "rank": i + 1, "fetched_at": time.time(),
                "_rank": i}
        score, comp = score_candidate(topic, cand)
        cand["score"], cand["score_components"] = score, comp
        cand["score_version"] = "v1"
        cand["rationale"] = "rel=%.2f trust=%.2f via %s" % (
            comp["relevance"], comp["domain_trust"], provider.name)
        cand.pop("_rank", None)
        cands.append(cand)
    cands.sort(key=lambda c: c["score"], reverse=True)

    if persist:
        if merge:
            existing = {c["source_id"]: c for c in load_discoveries(discover_path(home))}
            for c in cands:
                existing[c["source_id"]] = c
            merged = sorted(existing.values(), key=lambda c: c.get("score", 0), reverse=True)
            _write_discoveries(merged, discover_path(home))
        else:
            _write_discoveries(cands, discover_path(home))
    return {"status": "OK", "topic": topic, "provider": provider.name,
            "candidates": cands, "rejected": rejected,
            "n_found": len(cands), "n_rejected": len(rejected)}


def promote_discovery(source_id, sources_path_=None, discover_path_=None):
    """후보 1건을 harvest 화이트리스트로 승급 — harvest.add_source 게이트 통과 시에만.
    성공하면 discover_candidates 에서 제거(이동). discover 가 화이트리스트를 직접 쓰지 않음."""
    cands = load_discoveries(discover_path_)
    target = next((c for c in cands if c.get("source_id") == source_id), None)
    if not target:
        return {"status": "BLOCK", "reason": "CANDIDATE_NOT_FOUND", "source_id": source_id}
    res = HV.add_source(target["kind"], target["url"],
                        keyword=target.get("query_origin"), path=sources_path_)
    if res.get("status") != "OK":
        return {"status": "BLOCK", "reason": "ADD_SOURCE_REJECTED", "detail": res}
    kept = [c for c in cands if c.get("source_id") != source_id]
    _write_discoveries(kept, discover_path_ or discover_path())
    return {"status": "OK", "source_id": source_id, "add_reason": res.get("reason"),
            "promoted_url": target["url"]}


# ── selftest (provider mock · temp 경로 · 실 네트워크 0) ───────────────
def _mock_provider(hits):
    p = SearchProvider()
    p.name = "mock"
    p.search = lambda query, limit=10: list(hits)[:limit]
    return p


def _selftest():
    import tempfile
    ok = []

    def chk(name, cond):
        ok.append(cond)
        print(("  PASS " if cond else "  FAIL ") + name)

    home = tempfile.mkdtemp(prefix="discover_st_")
    dp = os.path.join(home, DISCOVER_FILE)
    sp = os.path.join(home, "harvest_sources.json")

    hits = [
        {"url": "https://arxiv.org/abs/2401.00001", "title": "딥러닝 입찰 예측", "snippet": "입찰 가격 예측 모델"},
        {"url": "https://www.g2b.go.kr/notice/123", "title": "나라장터 공고", "snippet": "조달 입찰 공고"},
        {"url": "http://127.0.0.1/secret", "title": "internal", "snippet": "사설"},          # SSRF
        {"url": "https://example.com/page", "title": "잡담", "snippet": "관련 없음"},
        {"url": "https://www.g2b.go.kr/notice/123", "title": "중복", "snippet": "dup"},          # dedup
        {"url": "file:///c:/secret.txt", "title": "local", "snippet": "x"},                    # NON_HTTP/dirty
    ]
    r = discover("입찰 가격 예측", provider=_mock_provider(hits), home=home)
    chk("D1 후보 생성(>0)", r["n_found"] > 0)
    chk("D2 SSRF 사설IP 후보 제외", all("127.0.0.1" not in c["url"] for c in r["candidates"]))
    chk("D2b file:// 제외", all(not c["url"].startswith("file:") for c in r["candidates"]))
    chk("D2c 거부에 사설IP·file 포함", len(r["rejected"]) >= 2)
    chk("D3 dedup(같은 url 1건)",
        len([c for c in r["candidates"] if "g2b.go.kr/notice/123" in c["url"]]) == 1)
    chk("D4 랭킹 — .go.kr/arxiv 가 example.com 보다 위",
        r["candidates"][0]["url"] != "https://example.com/page")
    chk("D5 score_components 동봉", all("score_components" in c for c in r["candidates"]))
    # roundtrip
    loaded = load_discoveries(dp)
    chk("D6 discover_candidates.json roundtrip", len(loaded) == r["n_found"])
    # promote → harvest_sources(별도 파일) 로 이동
    sid = r["candidates"][0]["source_id"]
    pr = promote_discovery(sid, sources_path_=sp, discover_path_=dp)
    chk("D7 promote OK", pr["status"] == "OK")
    chk("D7b harvest_sources 에 등록됨", HV.is_registered(sid, sp))
    chk("D7c discover 에서 제거됨(이동)",
        all(c["source_id"] != sid for c in load_discoveries(dp)))
    chk("D8 분리 — discover_candidates 와 harvest_sources 는 다른 파일", dp != sp)
    # promote 멱등/게이트 — 이미 이동된 sid 재promote 는 NOT_FOUND
    pr2 = promote_discovery(sid, sources_path_=sp, discover_path_=dp)
    chk("D9 이동된 후보 재promote 차단", pr2["status"] == "BLOCK")
    # IDNA homograph
    okv, _ = vet_url("https://аррӏе.com")  # 키릴 위장(non-ascii) → IDNA 정규화는 되나 공개 도메인
    chk("D10 IDNA 정규화 동작(예외 없이 bool)", isinstance(okv, bool))

    # D11~ — provider 추가(SearXNG/ddgs/Fallback) mock 검증(실 네트워크 0)
    sx = SearxngProvider(runner=lambda url: {"results": [
        {"url": "https://law.go.kr/x", "title": "법령", "content": "공식 출처"}]})
    chk("D11 SearXNG json→계약 매핑", sx.search("q")[0]["url"] == "https://law.go.kr/x")
    dg = DdgsProvider(runner=lambda q, n: [{"url": "https://a.com", "title": "t", "snippet": "s"}])
    chk("D11b ddgs→계약 매핑", dg.search("q")[0]["snippet"] == "s")
    # FallbackProvider — 첫 provider 빈결과/예외 → 다음으로
    class _Empty(SearchProvider):
        name = "empty"
        def search(self, q, limit=10): return []
    class _Boom(SearchProvider):
        name = "boom"
        def search(self, q, limit=10): raise RuntimeError("down")
    fb = FallbackProvider([_Boom(), _Empty(), dg])
    chk("D12 폴백 체인 — 예외/빈결과 건너뛰고 ddgs 채택", fb.search("q")[0]["url"] == "https://a.com")
    chk("D12b 채택 provider 라벨", fb.name == "fallback:ddgs")
    fb2 = FallbackProvider([_Empty(), _Boom()])
    chk("D12c 전부 실패 → 빈 결과(예외 전파 0)", fb2.search("q") == [] and fb2.name == "fallback:none")
    # discover 가 mock provider 로 SearXNG 결과를 후보화
    rsx = discover("공동주택 하자보수", provider=sx, home=home, persist=False)
    chk("D13 SearXNG provider 로 discover 후보 생성",
        rsx["n_found"] == 1 and rsx["candidates"][0]["provider"] == "searxng")

    # D14~ — provider 계약 확장(rank/source/fetched_at)
    chk("D14 candidate 에 rank/source/fetched_at 동봉",
        all(("rank" in c and "source" in c and "fetched_at" in c) for c in rsx["candidates"]))
    chk("D14b source 기본 = engine 없으면 provider 명", rsx["candidates"][0]["source"] == "searxng")
    sx2 = SearxngProvider(runner=lambda url: {"results": [
        {"url": "https://law.go.kr/y", "title": "법", "content": "x", "engine": "google"}]})
    rsx2 = discover("법령 공식", provider=sx2, home=home, persist=False)
    chk("D14c hit.engine → candidate.source", rsx2["candidates"][0]["source"] == "google")
    chk("D14d rank 1-based 보존", rsx2["candidates"][0]["rank"] == 1)

    # D15~ — serper/naver 키 미설정 → 빈결과(자동 스킵). env save/restore.
    _sk = os.environ.pop("SERPER_API_KEY", None)
    chk("D15 Serper 키 미설정 → 빈결과(자동 스킵)", SerperProvider().search("q") == [])
    if _sk is not None:
        os.environ["SERPER_API_KEY"] = _sk
    _ni = os.environ.pop("NAVER_CLIENT_ID", None)
    _ns = os.environ.pop("NAVER_CLIENT_SECRET", None)
    chk("D15b Naver 키 미설정 → 빈결과(자동 스킵)", NaverProvider().search("q") == [])
    if _ni is not None:
        os.environ["NAVER_CLIENT_ID"] = _ni
    if _ns is not None:
        os.environ["NAVER_CLIENT_SECRET"] = _ns

    # D16~ — runner mock 계약 매핑(실 네트워크 0)
    sp_m = SerperProvider(runner=lambda q, n: [
        {"url": "https://s.com", "title": "t", "snippet": "x", "source": "serper"}])
    chk("D16 Serper runner→계약 매핑", sp_m.search("q")[0]["url"] == "https://s.com")
    nv_m = NaverProvider(runner=lambda q, n: [
        {"url": "https://n.com", "title": "t", "snippet": "x", "source": "naver"}])
    chk("D16b Naver runner→계약 매핑", nv_m.search("q")[0]["source"] == "naver")

    # D17~ — default_provider 키 가드(키 set 시 chain 합류, 본진 우선). env save/restore.
    _save = {k: os.environ.get(k) for k in ("SEARXNG_URL", "SERPER_API_KEY")}
    os.environ["SEARXNG_URL"] = "http://localhost:8888"
    os.environ["SERPER_API_KEY"] = "dummy"
    dpv = default_provider()
    names = [p.name for p in dpv.providers]
    chk("D17 serper 키 set 시 chain 합류", "serper" in names)
    chk("D17b searxng 본진이 serper 앞(우선)", names.index("searxng") < names.index("serper"))
    for _k, _v in _save.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_discover — use --selftest, or import discover()/promote_discovery()")
