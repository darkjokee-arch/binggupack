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
  - provider = 일반검색 그물 + 공식 커넥터(data.go.kr/KIPRIS·키 우대·없으면 폴백)·topic 상황적응 routing.
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

# 도메인 신뢰 seed(bootstrap) — "최상품 자료" 티어 랭킹. 공식/학술/1차자료를 상위로.
#   Tier1(1.0) 공식기관·학술·1차데이터·국제기구  /  Tier2(0.7) 백과·.org·공식기업·신뢰미디어
#   Tier3 감점(0.15)은 아래 _PENALTY_DOMAINS 로 별도 강제(명시 매칭 우선).
TRUST_SEED = {
    # ── Tier1(1.0) 최상품 — 공식기관·학술·1차데이터·국제기구 ──
    ".gov": 1.0, ".gov.": 1.0, ".go.kr": 1.0, ".ac.kr": 1.0, ".edu": 1.0,
    ".re.kr": 1.0, ".or.kr": 1.0,
    "arxiv.org": 1.0, "data.go.kr": 1.0, "kostat.go.kr": 1.0,
    "visitkorea.or.kr": 1.0, "scholar.google": 1.0, "who.int": 1.0,
    "oecd.org": 1.0, "g2b.go.kr": 1.0, "nps.or.kr": 1.0, "law.go.kr": 1.0,
    "apis.data.go.kr": 1.0, "plus.kipris.or.kr": 1.0, "kipris.or.kr": 1.0,
    # ── Tier2(0.7) — 백과·.org·신뢰 미디어·공식 기업 도메인 ──
    "wikipedia.org": 0.7, "namu.wiki": 0.7, ".org": 0.7, "github.com": 0.7,
}
# 감점 도메인(Tier3·0.15) — 개인블로그·제휴/쇼핑/광고성·SEO 클릭베이트. 명시 매칭 시 강제(Tier 우선 X).
_PENALTY_DOMAINS = {
    "blog.naver.com", "tistory.com", "blog.", "brunch.co.kr", "velog.io", ".shop",
}
_PENALTY_TRUST = 0.15
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

    def search(self, query, limit=10, lang=None):
        # lang(=현지어 타겟) 주어지면 SearXNG 'language' 파라미터로 전달(id/en/ko 등).
        #   lang=None 이면 기존 동작 그대로(language 미전송 = 회귀 0).
        from urllib.parse import urlencode
        params = {"q": query, "format": "json"}
        if lang:
            params["language"] = lang
        url = self.base + "/search?" + urlencode(params)
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


class TavilyProvider(SearchProvider):
    """Tavily Search API(LLM 정제·빠름·측정 최速). TAVILY_API_KEY 필요(무료 월 1,000 credits).
    키 미설정/오류면 빈 결과 → 자동 스킵(살아나면 자동 활성). runner 주입 시 selftest mock."""
    name = "tavily"

    def __init__(self, runner=None):
        self._runner = runner

    def search(self, query, limit=10):
        if self._runner is not None:
            return list(self._runner(query, limit) or [])[:limit]
        key = os.environ.get("TAVILY_API_KEY")
        if not key:
            return []  # 미설정 → 빈 결과(키 살아나면 자동 활성)
        import urllib.request
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=json.dumps({"query": query, "max_results": limit}).encode("utf-8"),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=25).read())
        except Exception:
            return []
        return [{"url": r.get("url"), "title": r.get("title"),
                 "snippet": r.get("content"), "source": "tavily"}
                for r in (data.get("results") or [])][:limit]


class DataGoKrProvider(SearchProvider):
    """공공데이터포털(data.go.kr) 데이터셋 검색 공식 커넥터. DATA_GO_KR_SERVICE_KEY 필요.
    JSON OpenAPI → SearchProvider 1:1(stdlib json·서드파티 0). 키 미설정/오류면 빈 결과(자동 스킵).
    runner 주입 시 selftest mock. serviceKey 는 secret — env/인자 참조만(하드코딩 0)."""
    name = "data_go_kr"
    ENDPOINT = "https://api.odcloud.kr/api/15077093/v1/centers"  # env DATA_GO_KR_ENDPOINT 로 교체 가능

    def __init__(self, runner=None, service_key=None):
        self._runner = runner
        self._service_key = service_key

    def search(self, query, limit=10):
        if self._runner is not None:
            return list(self._runner(query, limit) or [])[:limit]
        key = self._service_key or os.environ.get("DATA_GO_KR_SERVICE_KEY")
        if not key:
            return []  # 미설정 → 빈 결과(키 살아나면 자동 활성)
        from urllib.parse import urlencode
        import urllib.request
        endpoint = os.environ.get("DATA_GO_KR_ENDPOINT") or self.ENDPOINT
        url = endpoint + "?" + urlencode({
            "serviceKey": key, "page": 1, "perPage": limit,
            "returnType": "JSON", "cond[title::LIKE]": query})
        req = urllib.request.Request(url, headers={"User-Agent": DDGProvider.UA})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=25).read())
        except Exception:
            return []
        records = (data.get("data") or data.get("items")
                   or data.get("response", {}).get("body", {}).get("items") or [])
        out = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            u = (rec.get("url") or rec.get("link") or rec.get("detailUrl")
                 or rec.get("fileUrl"))
            if not u and rec.get("id"):
                u = "https://www.data.go.kr/data/%s/fileData.do" % rec.get("id")
            if not u:
                continue
            title = rec.get("title") or rec.get("datasetNm") or rec.get("name") or ""
            snippet = (rec.get("description") or rec.get("desc")
                       or rec.get("snippet") or "")
            out.append({"url": u, "title": title, "snippet": snippet,
                        "source": "data_go_kr"})
        return out[:limit]


class KiprisProvider(SearchProvider):
    """키프리스(KIPRIS Plus) 특허/실용/상표/디자인 검색 공식 커넥터. KIPRIS_SERVICE_KEY 필요.
    응답 XML → xml.etree.ElementTree(stdlib·bs4 금지). 키 미설정/오류면 빈 결과(자동 스킵).
    runner 주입 시 selftest mock. ServiceKey 는 secret — env/인자 참조만(하드코딩 0)."""
    name = "kipris"
    ENDPOINT = ("http://plus.kipris.or.kr/openapi/rest/"
                "patUtiModInfoSearchSevice/freeSearchInfo")  # env KIPRIS_ENDPOINT 로 교체 가능

    def __init__(self, runner=None, service_key=None):
        self._runner = runner
        self._service_key = service_key

    def search(self, query, limit=10):
        if self._runner is not None:
            return list(self._runner(query, limit) or [])[:limit]
        key = self._service_key or os.environ.get("KIPRIS_SERVICE_KEY")
        if not key:
            return []  # 미설정 → 빈 결과(키 살아나면 자동 활성)
        from urllib.parse import urlencode
        import urllib.request
        import xml.etree.ElementTree as ET  # stdlib(bs4 금지)
        endpoint = os.environ.get("KIPRIS_ENDPOINT") or self.ENDPOINT
        url = endpoint + "?" + urlencode(
            {"word": query, "ServiceKey": key, "numOfRows": limit})
        req = urllib.request.Request(url, headers={"User-Agent": DDGProvider.UA})
        try:
            root = ET.fromstring(urllib.request.urlopen(req, timeout=25).read())
        except Exception:
            return []
        out = []
        for item in root.iter("item"):
            def _t(*tags):
                for tag in tags:
                    el = item.find(tag)
                    if el is not None and el.text:
                        return el.text
                return None
            appno = _t("applicationNumber", "ApplicationNumber")
            title = _t("inventionTitle", "InventionName", "inventionName") or ""
            snippet = _t("astrtCont", "abstract") or ""
            u = _t("url", "link")
            if not u and appno:
                u = ("https://www.kipris.or.kr/khome/search/searchResult.do?query="
                     + str(appno))
            if not u:
                continue
            out.append({"url": u, "title": title, "snippet": snippet,
                        "source": "kipris"})
            if len(out) >= limit:
                break
        return out[:limit]


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


class CompositeProvider(SearchProvider):
    """공식 커넥터 + 일반검색을 **합산**(merge·url dedup)하는 그물. FallbackProvider(first-non-empty)와
    달리 breadth 무손실 — 공식 결과가 일반검색을 억누르지 않음. 공식을 앞에 두어 자연히 상위 랭킹.
    child 예외/빈결과는 건너뜀(절대 raise 0). 채택 child 명으로 name 라벨 갱신."""
    name = "composite"

    def __init__(self, providers):
        self.providers = [p for p in providers if p is not None]

    def search(self, query, limit=10):
        merged, seen, used = [], set(), []
        for p in self.providers:
            try:
                res = p.search(query, limit) or []
            except Exception:
                continue  # child 실패는 건너뜀(예외 전파 0)
            got = False
            for hit in res:
                if not isinstance(hit, dict):
                    continue
                u = hit.get("url")
                if not u or u in seen:
                    continue
                seen.add(u)
                merged.append(hit)
                got = True
            if got:
                used.append(p.name)
        self.name = "composite:" + ("+".join(used) if used else "none")
        return merged


# ── 공식 커넥터 레지스트리 + intent routing(상황 적응 선택) ─────────────
#   topic 토큰이 keywords 와 겹치고 AND 필요 env 키가 모두 set 인 커넥터만 합류.
#   둘 중 하나라도 불충족이면 제외 → 일반검색만(폴백 보장).
OFFICIAL_CONNECTORS = (
    {"name": "kipris",
     "keywords": ("특허", "실용신안", "patent", "상표", "디자인권", "지식재산", "특허권"),
     "env": ("KIPRIS_SERVICE_KEY",),
     "factory": lambda: KiprisProvider()},
    {"name": "data_go_kr",
     "keywords": ("공공데이터", "통계", "조달", "입찰", "행정", "데이터셋", "공공"),
     "env": ("DATA_GO_KR_SERVICE_KEY",),
     "factory": lambda: DataGoKrProvider()},
)


def select_official_providers(topic):
    """topic 매칭 + 키 set 인 공식 커넥터만 인스턴스화. 매칭 0 또는 키 부족이면 [](일반검색만)."""
    toks = _tokens(topic)
    if not toks:
        return []
    out = []
    for c in OFFICIAL_CONNECTORS:
        kw = set()
        for k in c["keywords"]:
            kw |= _tokens(k)
        if not (toks & kw):
            continue  # topic 불일치 → 제외
        if not all(os.environ.get(e) for e in c["env"]):
            continue  # 키 부족 → 제외(자동 폴백)
        out.append(c["factory"]())
    return out


def default_provider(topic=None):
    """그물 우선순위 — SearXNG(본진·측정 결정) → serper/naver/tavily(키 있으면 보조 폴백) → ddgs → DDG(최후).
    URL/키 미설정 그물은 자동 제외(unset 이면 chain 미합류, 살아나면 자동 활성).
    항상 DDGProvider 최후 폴백(키·인프라 0).

    topic 주어지고 매칭 공식 커넥터(키 set)가 있으면 CompositeProvider([*공식, 일반]) 로 합산
    (공식 우대·일반검색 무손실 병행). topic=None(무인자)이면 기존 일반 그물 그대로(회귀 0)."""
    chain = []
    if os.environ.get("SEARXNG_URL"):
        chain.append(SearxngProvider())
    if os.environ.get("SERPER_API_KEY"):
        chain.append(SerperProvider())
    if os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET"):
        chain.append(NaverProvider())
    if os.environ.get("TAVILY_API_KEY"):
        chain.append(TavilyProvider())
    try:
        import ddgs  # noqa: F401
        chain.append(DdgsProvider())
    except Exception:
        pass
    chain.append(DDGProvider())  # 최후 폴백(키/인프라 0)
    general = FallbackProvider(chain) if len(chain) > 1 else chain[0]
    off = select_official_providers(topic) if topic else []
    if off:
        return CompositeProvider([*off, general])  # 공식+일반 합산(무손실)
    return general  # 무인자/매칭 0 → 기존 동작 그대로


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
    # 감점 우선 — blog.naver.com 같은 명백한 개인블로그/광고성은 Tier 키워드 동시매칭이어도 0.15 강제.
    for bad in _PENALTY_DOMAINS:
        if bad in u:
            return _PENALTY_TRUST
    best = _DEFAULT_TRUST
    for key, val in TRUST_SEED.items():
        if key in u and val > best:
            best = val
    return best


# 콘텐츠 품질 신호(선택) — 정보성 +, 광고성/클릭베이트 -. 가중 작게(±0.05·과조정 금지).
#   정보신호: 숫자/% (예: 2023년·12%·5000원은 digit 으로 포착) + 통계/공식/발표 류.
_INFO_TERMS = ("통계", "공식", "발표", "보고서", "백서", "연구", "지표")
_AD_TERMS = ("할인", "최저가", "예약", "지금바로", "지금 바로", "클릭", "회원가입",
             "이벤트", "특가", "쿠폰", "무료체험", "광고", "제휴")


def _content_quality(title, snippet):
    """title+snippet 콘텐츠 품질 신호 → ±0.05. 정보성(숫자·%·통계·공식·발표) +, 광고성 -."""
    text = (str(title or "") + " " + str(snippet or "")).lower()
    delta = 0.0
    if re.search(r"\d|%", text) or any(t in text for t in _INFO_TERMS):
        delta += 0.05
    if any(t in text for t in _AD_TERMS):
        delta -= 0.05
    return delta


def score_candidate(topic, cand):
    """score = 0.35·relevance + 0.55·domain_trust + 0.1·rank_bonus + content_quality(±0.05).
    출처 신뢰(최상품)를 핵심 가중으로(0.55). 컴포넌트 분해 동봉(B 지적). 결과는 [0,1] 클램프."""
    rel = _relevance(topic, cand.get("title"), cand.get("snippet"))
    trust = _domain_trust(cand["url"])
    rank_bonus = max(0.0, 1.0 - cand.get("_rank", 0) * 0.1)  # 검색순위 보너스
    cq = _content_quality(cand.get("title"), cand.get("snippet"))
    comp = {"relevance": round(rel, 3), "domain_trust": round(trust, 3),
            "rank_bonus": round(rank_bonus, 3), "content_quality": round(cq, 3)}
    base = 0.35 * rel + 0.55 * trust + 0.1 * rank_bonus + cq
    score = round(max(0.0, min(1.0, base)), 4)
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
def _provider_search(provider, topic, limit, lang):
    """provider.search 호출 — provider 가 lang 인자를 받으면 전파, 아니면 무시(시그니처 호환).
    SearXNG 만 lang 을 쓰고 Ddgs/Fallback/Composite 등은 lang 미수용이어도 회귀 0."""
    if lang is not None:
        import inspect
        try:
            if "lang" in inspect.signature(provider.search).parameters:
                return provider.search(topic, limit=limit, lang=lang) or []
        except (TypeError, ValueError):
            pass
    return provider.search(topic, limit=limit) or []


def discover(topic, provider=None, limit=10, home=None, persist=True, merge=True, lang=None):
    """주제→소스 후보 발견. provider 검색 → vet → 랭킹 → discover_candidates.json.

    lang(=현지어 타겟·SearXNG 'language' id/en/ko 등) 주어지면 provider.search 에 전파.
      lang=None 이면 기존 동작 그대로(회귀 0). lang 미수용 provider 는 무시(시그니처 호환).

    반환 dict(status/topic/candidates/rejected/provider). 후보는 vet 통과(clean)분만.
    """
    provider = provider or default_provider(topic)
    raw = _provider_search(provider, topic, limit, lang)
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
        cand["score_version"] = "v2"
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

    # D18~ — Tavily provider(키 미설정 빈결과 + runner 계약 + chain 합류). env save/restore.
    _tk = os.environ.pop("TAVILY_API_KEY", None)
    chk("D18 Tavily 키 미설정 → 빈결과(자동 스킵)", TavilyProvider().search("q") == [])
    if _tk is not None:
        os.environ["TAVILY_API_KEY"] = _tk
    tv_m = TavilyProvider(runner=lambda q, n: [
        {"url": "https://t.com", "title": "t", "snippet": "x", "source": "tavily"}])
    chk("D18b Tavily runner→계약 매핑", tv_m.search("q")[0]["source"] == "tavily")
    _save2 = {k: os.environ.get(k) for k in ("SEARXNG_URL", "TAVILY_API_KEY")}
    os.environ["SEARXNG_URL"] = "http://localhost:8888"
    os.environ["TAVILY_API_KEY"] = "dummy"
    chk("D18c tavily 키 set 시 chain 합류",
        "tavily" in [p.name for p in default_provider().providers])
    for _k, _v in _save2.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

    # D19~ — 출처 티어 랭킹 v2(최상품 우선·감점 도메인). 결정적·네트워크 0.
    chk("D19 Tier1 .go.kr/통계청 trust=1.0", _domain_trust("https://www.kostat.go.kr/x") == 1.0)
    chk("D19b Tier1 arxiv 학술 trust=1.0", _domain_trust("https://arxiv.org/abs/2401.1") == 1.0)
    chk("D19c Tier1 .re.kr 연구기관 trust=1.0", _domain_trust("https://www.kdi.re.kr/r/1") == 1.0)
    chk("D19d Tier1 visitkorea.or.kr 관광공사 trust=1.0",
        _domain_trust("https://english.visitkorea.or.kr/x") == 1.0)
    chk("D20 Tier2 wikipedia trust=0.7", _domain_trust("https://ko.wikipedia.org/wiki/x") == 0.7)
    chk("D20b Tier2 namu.wiki trust=0.7", _domain_trust("https://namu.wiki/w/x") == 0.7)
    chk("D21 감점 blog.naver.com=0.15", _domain_trust("https://blog.naver.com/abc/123") == 0.15)
    chk("D21b 감점 tistory=0.15", _domain_trust("https://abc.tistory.com/1") == 0.15)
    chk("D21c 감점 velog/.shop=0.15",
        _domain_trust("https://velog.io/@x") == 0.15 and _domain_trust("https://deal.shop/x") == 0.15)
    chk("D21d 감점 우선 — blog.naver.com 은 Tier 키워드 동시매칭이어도 0.15",
        _domain_trust("https://blog.naver.com/post.org") == 0.15)
    chk("D22 미분류 default=0.4", _domain_trust("https://random-site.com/x") == 0.4)
    # 티어 랭킹 — 공식 통계청 > 상업 개인블로그(신혼여행 검색 회귀 케이스)
    _official = {"url": "https://www.kostat.go.kr/stat", "title": "통계청 신혼부부 통계",
                 "snippet": "2023년 신혼부부 통계 발표", "_rank": 2}
    _blogc = {"url": "https://blog.naver.com/wedding", "title": "신혼여행 추천",
              "snippet": "신혼여행 best 예약 할인 지금바로 클릭", "_rank": 0}
    _so, _soc = score_candidate("신혼부부 신혼여행 통계", _official)
    _sb, _ = score_candidate("신혼부부 신혼여행 통계", _blogc)
    chk("D23 공식 통계청 score > 상업블로그 score(상위 랭킹)", _so > _sb)
    chk("D23b score_components 에 content_quality 동봉", "content_quality" in _soc)
    # 콘텐츠 품질 신호 — 정보성 + / 광고성 -
    chk("D24 정보신호(숫자·통계·발표) +", _content_quality("2023년 통계 발표", "조사 결과 12% 상승") > 0)
    chk("D24b 광고성(최저가·예약·클릭·이벤트) -",
        _content_quality("최저가 예약", "지금바로 클릭 할인 이벤트 회원가입") < 0)
    chk("D24c 신호 없음 → 0", _content_quality("일반 제목", "평범한 본문") == 0.0)
    chk("D25 가중 0.55 trust 지배 — 동일 relevance 면 Tier1 > default",
        score_candidate("x", {"url": "https://a.go.kr", "title": "t", "snippet": "s", "_rank": 0})[0]
        > score_candidate("x", {"url": "https://a.com", "title": "t", "snippet": "s", "_rank": 0})[0])

    # D26~ — 공식 커넥터(data.go.kr/KIPRIS) 키 미설정 빈결과 + runner 계약 매핑(네트워크 0)
    _dk = os.environ.pop("DATA_GO_KR_SERVICE_KEY", None)
    chk("D26 DataGoKr 키 미설정 → 빈결과(자동 스킵)", DataGoKrProvider().search("q") == [])
    if _dk is not None:
        os.environ["DATA_GO_KR_SERVICE_KEY"] = _dk
    dgk_m = DataGoKrProvider(runner=lambda q, n: [
        {"url": "https://www.data.go.kr/data/1/fileData.do", "title": "조달 통계",
         "snippet": "공공 입찰 데이터", "source": "data_go_kr"}])
    _dgk = dgk_m.search("q")
    chk("D26b DataGoKr runner→계약 매핑(source=='data_go_kr')",
        _dgk[0]["url"].startswith("https://www.data.go.kr/") and _dgk[0]["source"] == "data_go_kr")
    _kk = os.environ.pop("KIPRIS_SERVICE_KEY", None)
    chk("D27 KIPRIS 키 미설정 → 빈결과(자동 스킵)", KiprisProvider().search("q") == [])
    if _kk is not None:
        os.environ["KIPRIS_SERVICE_KEY"] = _kk
    kp_m = KiprisProvider(runner=lambda q, n: [
        {"url": "https://www.kipris.or.kr/khome/x", "title": "발명명칭",
         "snippet": "요약", "source": "kipris"}])
    chk("D27b KIPRIS runner→계약 매핑(source=='kipris')", kp_m.search("q")[0]["source"] == "kipris")

    # D28~ — select_official_providers: topic 매칭 AND 키 set 인 커넥터만. env save/restore.
    _ek = os.environ.get("KIPRIS_SERVICE_KEY")
    _ed = os.environ.get("DATA_GO_KR_SERVICE_KEY")
    os.environ["KIPRIS_SERVICE_KEY"] = "dummy"
    os.environ.pop("DATA_GO_KR_SERVICE_KEY", None)
    _names_patent = [p.name for p in select_official_providers("특허 침해 분석")]
    chk("D28 특허 topic + KIPRIS키 → kipris 포함", "kipris" in _names_patent)
    chk("D28a 비특허 topic('신혼부부 통계') → kipris 미포함",
        "kipris" not in [p.name for p in select_official_providers("신혼부부 통계")])
    # data_go_kr 키 미설정이면 통계/조달 topic 매칭이어도 제외
    chk("D28a2 통계 topic 인데 data.go.kr 키 미설정 → 미포함",
        "data_go_kr" not in [p.name for p in select_official_providers("조달 통계 데이터셋")])
    # 키 전무 → []
    os.environ.pop("KIPRIS_SERVICE_KEY", None)
    chk("D28b 키 전무 → [](일반검색만)", select_official_providers("특허 통계") == [])

    # D29 default_provider(topic)+키 → CompositeProvider & 공식이 chain 앞
    os.environ["KIPRIS_SERVICE_KEY"] = "dummy"
    _dpt = default_provider("특허 침해 분석")
    chk("D29 default_provider(topic)+키 → CompositeProvider", isinstance(_dpt, CompositeProvider))
    chk("D29a 공식(kipris)이 chain 맨 앞", _dpt.providers[0].name == "kipris")
    os.environ.pop("KIPRIS_SERVICE_KEY", None)
    # D29b 회귀가드 — 무인자 default_provider() 는 기존과 동일(FallbackProvider/단일, Composite 아님)
    _dp0 = default_provider()
    chk("D29b 무인자 default_provider() 는 Composite 아님(회귀 0)",
        not isinstance(_dp0, CompositeProvider))
    # 키/매칭 0 인 topic 도 일반검색 그대로(Composite 아님)
    chk("D29c topic 있어도 공식키 0 → 일반검색 그대로(Composite 아님)",
        not isinstance(default_provider("특허 통계"), CompositeProvider))
    # env restore
    if _ek is not None:
        os.environ["KIPRIS_SERVICE_KEY"] = _ek
    else:
        os.environ.pop("KIPRIS_SERVICE_KEY", None)
    if _ed is not None:
        os.environ["DATA_GO_KR_SERVICE_KEY"] = _ed

    # D30 CompositeProvider 병합·dedup·공식 상위·child 예외 건너뜀
    _offp = _mock_provider([{"url": "https://data.go.kr/d/1", "title": "공식", "snippet": "조달"},
                            {"url": "https://dup.com/x", "title": "중복src", "snippet": "s"}])
    _genp = _mock_provider([{"url": "https://dup.com/x", "title": "일반중복", "snippet": "s"},
                            {"url": "https://general.com/y", "title": "일반", "snippet": "s"}])

    class _Boom2(SearchProvider):
        name = "boom2"
        def search(self, q, limit=10): raise RuntimeError("down")
    comp = CompositeProvider([_offp, _Boom2(), _genp])
    _cres = comp.search("q")
    chk("D30 Composite 공식 결과가 맨 위", _cres[0]["url"] == "https://data.go.kr/d/1")
    chk("D30a url dedup(중복 1건)",
        len([h for h in _cres if h["url"] == "https://dup.com/x"]) == 1)
    chk("D30b 일반 hit 보존(무손실)",
        any(h["url"] == "https://general.com/y" for h in _cres))
    chk("D30c child 예외 건너뜀(예외 전파 0)", isinstance(_cres, list))
    chk("D30d 전부 빈결과 → [](raise 0)",
        CompositeProvider([_mock_provider([]), _mock_provider([])]).search("q") == [])

    # D31 discover(topic, composite-mock): 공식 .go.kr vet 통과·trust 1.0 최상위 + 일반 보존(무손실)
    _comp_disc = CompositeProvider([
        _mock_provider([{"url": "https://www.data.go.kr/data/1", "title": "조달 데이터",
                         "snippet": "공공 입찰 통계"}]),
        _mock_provider([{"url": "https://general.com/y", "title": "일반 자료",
                         "snippet": "조달 입찰 통계"}])])
    _rc = discover("조달 입찰 통계", provider=_comp_disc, home=home, persist=False)
    chk("D31 공식 .go.kr 후보가 최상위(domain_trust 1.0)",
        "data.go.kr" in _rc["candidates"][0]["url"])
    chk("D31a 일반 hit 도 후보 보존(무손실)",
        any("general.com" in c["url"] for c in _rc["candidates"]))

    # D32 공식 커넥터 dirty url 도 vet_url 로 rejected(공식 신뢰가 SSRF 게이트 우회 못 함)
    _dirty = CompositeProvider([
        _mock_provider([{"url": "http://127.0.0.1/secret", "title": "internal", "snippet": "x"},
                        {"url": "file:///c:/x.txt", "title": "local", "snippet": "x"}])])
    _rd = discover("공공데이터", provider=_dirty, home=home, persist=False)
    chk("D32 공식 커넥터 dirty url(127.0.0.1/file:) 도 vet 차단",
        _rd["n_found"] == 0 and _rd["n_rejected"] >= 2)

    # D33 default_provider(topic) selftest 경로 네트워크 0 — 공식키 미설정이면 connector 미인스턴스화
    _g0 = os.environ.get("KIPRIS_SERVICE_KEY")
    os.environ.pop("KIPRIS_SERVICE_KEY", None)
    os.environ.pop("DATA_GO_KR_SERVICE_KEY", None)
    chk("D33 공식키 미설정 topic → connector 미인스턴스화(네트워크 0·일반검색만)",
        not isinstance(default_provider("특허 조달 통계"), CompositeProvider))
    if _g0 is not None:
        os.environ["KIPRIS_SERVICE_KEY"] = _g0
    if _ed is not None:
        os.environ["DATA_GO_KR_SERVICE_KEY"] = _ed

    # D34~ — SearXNG 현지어/언어 검색(lang → 'language' 파라미터). 실 네트워크 0(runner 가 url 캡처).
    _captured = {}

    def _lang_runner(url):
        _captured["url"] = url
        return {"results": [{"url": "https://pantai.id/x", "title": "Pantai Bali",
                             "content": "pantai tersembunyi"}]}
    sx_lang = SearxngProvider(runner=_lang_runner)
    _rl = sx_lang.search("pantai tersembunyi bali", lang="id")
    chk("D34 lang='id' → 요청 url 에 language=id 실림", "language=id" in _captured["url"])
    chk("D34a lang 결과 계약 매핑 보존", _rl[0]["url"] == "https://pantai.id/x")
    _captured.clear()
    sx_lang.search("q")  # lang 미지정(회귀)
    chk("D34b lang=None → 요청 url 에 language 미전송(회귀 0)", "language" not in _captured["url"])
    # discover 가 lang 을 SearXNG provider 로 전파
    _captured.clear()
    _rd_lang = discover("pantai bali", provider=SearxngProvider(runner=_lang_runner),
                        home=home, persist=False, lang="id")
    chk("D34c discover(lang) → provider.search 로 language=id 전파",
        "language=id" in _captured.get("url", "") and _rd_lang["n_found"] == 1)
    # discover lang=None 회귀 — language 미전송
    _captured.clear()
    discover("pantai bali", provider=SearxngProvider(runner=_lang_runner),
             home=home, persist=False)
    chk("D34d discover lang=None → language 미전송(회귀 0)",
        "language" not in _captured.get("url", ""))
    # lang 미수용 provider(ddgs runner)에 lang 줘도 안전(시그니처 호환·예외 0)
    _dg_lang = DdgsProvider(runner=lambda q, n: [{"url": "https://a.com", "title": "t", "snippet": "s"}])
    _rd_ddgs = discover("q", provider=_dg_lang, home=home, persist=False, lang="id")
    chk("D34e lang 미수용 provider(ddgs)에 lang 전달해도 안전(무시·예외 0)",
        _rd_ddgs["n_found"] == 1)

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_discover — use --selftest, or import discover()/promote_discovery()")
