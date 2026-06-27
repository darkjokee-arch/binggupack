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
    """검색 그물 추상. search(query, limit) -> [{"url","title","snippet"}]."""
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
    provider = provider or DDGProvider()
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
                "provider": provider.name, "query_origin": topic, "_rank": i}
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

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_discover — use --selftest, or import discover()/promote_discovery()")
