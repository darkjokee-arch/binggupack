# -*- coding: utf-8 -*-
"""binggu_harvest — P1 ③ 외부 수확(inbound) : 사람 등록 소스 → 후보(candidate)만.

헌법(docs/BINGGUPACK_CONSTITUTION_2026-06-17.md §6) 3중 게이트를 코드 불변식으로 박는다:
  ① 사람이 등록한 소스만 fetch  — harvest_sources.json 화이트리스트(기본 빈 []).
       화이트리스트에 없는 source_id 는 fetch 거부(deny-by-default · fail-closed).
       추가 방어: 소스 URL 자체가 비공개/내부 위치(로컬경로·내부 IP·localhost)면 dirty 로 거부.
  ② 긁은 건 후보로만   — fetch 텍스트 → 원문 1:1 분할(redaction 만) → to_nodes →
       candidate=true / promotion_allowed=false / origin=watcher / domain=STAGING_UNASSIGNED.
       active 승격 0 · 영구화 0(코드에 active write 경로 없음).
       주의: git-diff 전용 capture()(added[:3] 잘림·접두·' | ' 치환)는 §1 위배라 쓰지 않는다.
  ③ 영구는 사람 SAVE   — 이 모듈은 영구화 안 함. 후보를 사람이 preview→SAVE n 해야만 active
       (기존 0-A save_gate 체인 그대로). 본 모듈엔 그 경로 없음.

방향(autopush 의 거울상):
  autopush = SAVE 확정 → KV 출력(outbound). 게이트 = '사람 SAVE 기록 존재 → 전송 허용'.
  harvest  = 외부 소스 → 로컬 후보 적재(inbound). 게이트 = '사람 등록 소스 → 수확 허용 +
             수확물은 candidate 만(영구화 0)'. 의미가 반전된다.

원문 변형 0(헌법 §1): fetch 한 외부 텍스트는 redaction(secret/PII 마스킹)만 거치고 가공 0.
  redaction 은 누출 차단 안전벨트일 뿐 의미 변형이 아니며, 마스킹 후에도 secret 잔존이면 STOP.
  분할은 문단/줄 경계만 잡고(_split_segments) 각 segment 의 raw 문자열을 1:1 보존한다(잘림/요약/리포맷 0).

주체 분리(CLAUDE.md §3 규칙4 — 외부 자동 동기화 기각과 양립):
  실 네트워크 fetch(_real_fetch_runner)는 **owner 의 작업 스케줄러 프로세스** 에서만 일어난다
  (Claude tool_use 아님). 코드(어댑터+게이트+selftest)는 이 파일이, 스케줄러 등록·실 fetch 는
  owner 1회. selftest 는 fetch 를 mock 으로(실 네트워크 0) + temp 경로만(실 ledger/소스장 미접촉).

긴급 스위치: ~/.binggupack/harvest_disabled 플래그가 있으면 즉시 no-op(owner 통제).

불변/제약:
  - 운영 ledger active 노드 미접촉 — candidate=1 INSERT 만(영구화 0).
  - 표준 라이브러리 urllib 만 사용 — feedparser/requests 추가 금지(자동 업데이트 정책).
  - 화이트리스트 없는 소스 fetch 0. fetch 텍스트 raw 무가공(redaction 만).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_platform as _plat
import watcher_capture_mvp1 as mvp1                  # redact_text/_has_secret/SCOPE 재사용(capture 미사용)
import watcher_candidate_mvp2 as mvp2                # to_nodes (candidate 불변식 단일 글루)
import openbinggu_scope_envelope_dryrun as SP        # classify_source_pointer (게이트 마스킹)
import watcher_batch_m1 as bm1                       # B3: secret+PII 복합 redaction(batch_redact)+잔존 scanner


def _redact_all(text):
    """secret(mvp1) + PII(전화/이메일/주민/카드 — batch_redact) 통합 마스킹 + 독립 잔존 scan.
    반환 (redacted, residual_kinds). residual 비어 있어야 안전(있으면 호출부가 STOP).
    import 게이트(batch_pack_loader.residual_scan)와 동일 scanner 라 수확 단계서 미리 차단."""
    red, _hits, _review = bm1.batch_redact(text)
    return red, bm1.scan_residual_pii(red)


# ── 경로 (BINGGU_HOME 우선 — cross-platform 정합) ──────────────────
def _home():
    return _plat.binggu_home()


def sources_path(home=None):
    """사람이 등록한 외부 소스 화이트리스트(append/remove 가능한 JSON 목록)."""
    return os.path.join(home or _home(), "harvest_sources.json")


def harvest_log_path(home=None):
    return os.path.join(home or _home(), "harvest_log.jsonl")


def harvest_cursor_path(home=None):
    """이미 수확한 외부 항목 재적재 방지용 dedup 커서(content_hash 집합)."""
    return os.path.join(home or _home(), "harvested_cursor.json")


def harvest_disabled_path(home=None):
    return os.path.join(home or _home(), "harvest_disabled")


# ── ① source_registry — 사람이 등록한 소스만(화이트리스트, 기본 빈) ───────────
# 등록 단위: kind(arxiv|github|rss|url) + url + 선택 keyword. owner 가 직접 등록(빈 시작).
# 빙구팩 코드엔 owner 소스 하드코딩 0 — 아래 목록은 항상 owner 가 채운다.
VALID_KINDS = ("arxiv", "github", "rss", "url")


def _norm_url(u):
    return str(u or "").strip()


def source_id_for(url):
    """소스 식별자 = url 의 결정적 sha(12). 같은 url 재등록은 멱등."""
    return "src:" + hashlib.sha256(_norm_url(url).encode("utf-8")).hexdigest()[:12]


def load_sources(path=None):
    """화이트리스트 로드 → list[dict]. 부재/손상 = 빈 목록(fail-closed: 수확 0)."""
    path = path or sources_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []  # 손상 = 빈 목록 가정 → 미등록 = 수확 0
    src = d.get("sources") if isinstance(d, dict) else d
    return [s for s in (src or []) if isinstance(s, dict) and s.get("url")]


def _write_sources(sources, path=None):
    path = path or sources_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"sources": sources, "ts": time.time()}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # atomic
    return path


def add_source(kind, url, keyword=None, path=None):
    """소스 등록(사람 행위). kind 검증 + URL 공개안전성(dirty 거부) + 멱등(같은 url 1건만).
    반환 dict(status/source_id/reason). 비공개/내부 위치를 가리키는 URL 은 등록 거부."""
    kind = str(kind or "").strip().lower()
    url = _norm_url(url)
    if kind not in VALID_KINDS:
        return {"status": "BLOCK", "reason": "BAD_KIND", "valid": list(VALID_KINDS)}
    if not url:
        return {"status": "BLOCK", "reason": "EMPTY_URL"}
    # URL 공개안전성 — 로컬경로/내부 IP/localhost/UNC/file:// 면 dirty → 등록 거부(누출 차단).
    label = SP.classify_source_pointer(url)
    if label != "clean":
        return {"status": "BLOCK", "reason": "SOURCE_NOT_PUBLIC", "label": label}
    sid = source_id_for(url)
    sources = load_sources(path)
    if any(s.get("source_id") == sid for s in sources):
        return {"status": "OK", "source_id": sid, "reason": "ALREADY_REGISTERED"}
    sources.append({"source_id": sid, "kind": kind, "url": url,
                    "keyword": (keyword or None), "registered_ts": time.time()})
    _write_sources(sources, path)
    return {"status": "OK", "source_id": sid, "reason": "REGISTERED"}


def remove_source(source_id, path=None):
    sources = load_sources(path)
    kept = [s for s in sources if s.get("source_id") != source_id]
    if len(kept) == len(sources):
        return {"status": "OK", "reason": "NOT_FOUND", "removed": 0}
    _write_sources(kept, path)
    return {"status": "OK", "reason": "REMOVED", "removed": len(sources) - len(kept)}


def is_registered(source_id, path=None):
    """deny-by-default — 등록 목록에 있을 때만 True. fetch 직전 게이트①."""
    return any(s.get("source_id") == source_id for s in load_sources(path))


# ── ② urllib fetch 어댑터 — 표준 라이브러리만(requests/feedparser 금지) ───────
class _NoRedirect(__import__("urllib.request", fromlist=["HTTPRedirectHandler"]).HTTPRedirectHandler):
    """3xx 자동 추종 차단 — redirect 된 최종 URL(127.0.0.1·169.254.169.254 등)이 게이트를
    우회해 fetch 되는 SSRF(A4) 봉쇄. redirect 발견 시 예외로 STOP(추종 0)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        raise __import__("urllib.error").error.HTTPError(
            req.full_url, code, "redirect blocked (harvest gate — no auto-redirect)", headers, fp)


def _real_fetch_runner(url, timeout=30):
    """실제 외부 fetch — owner ScheduledTask 에서만 실행됨. selftest 는 이 함수를 호출하지 않음.

    표준 urllib 만 사용(자동 업데이트 정책 — 서드파티 추가 금지). 텍스트 raw 그대로 반환(무가공).
    redirect 자동 추종 금지(A4) — 3xx 응답이면 추종하지 않고 ERROR(게이트 우회 차단).
    """
    import urllib.request
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "binggupack-harvest/1.0"})
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310 (owner 등록 소스만·redirect 차단)
        final_url = resp.geturl()
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    # raw_bytes/content_type 동봉 — 전방위 파싱(parser adapter)용. text(decode)는 기존 호환 유지.
    return {"ok": True, "text": raw.decode("utf-8", errors="replace"),
            "url": url, "final_url": final_url,
            "raw_bytes": raw, "content_type": ctype}


def fetch_source(source, runner=None, sources_path_=None):
    """게이트① — 등록된 소스만 fetch. 미등록이면 거부(fail-closed). runner 기본 실 fetch.

    selftest 는 runner 를 mock 으로 주입(실 네트워크 0). 반환 dict(status/text/source_id).

    A2 차단: source_id 는 호출자 입력을 신뢰하지 않고 **항상 url 에서 재산출**한다.
      (등록된 source_id + 미등록 url 디커플링 공격 봉쇄 — 게이트는 fetch 대상 url 의 정체성으로만 판정.)
    A4 차단: runner 가 돌려준 final_url(리다이렉트 최종 도착지)을 다시 공개안전성 재검증한다.
    """
    url = _norm_url(source.get("url"))
    if not url:
        return {"status": "BLOCK", "reason": "EMPTY_URL", "source_id": None}
    # A2 — source_id 를 url 에서 재산출(입력 source_id 무시). 게이트는 fetch 할 url 의 정체성으로 판정.
    sid = source_id_for(url)
    # 게이트① 런타임 재검증 — 호출자가 임의 source dict 를 줘도 url 의 sid 가 등록 목록에 없으면 거부.
    if not is_registered(sid, sources_path_):
        return {"status": "BLOCK", "reason": "SOURCE_NOT_REGISTERED", "source_id": sid}
    # URL 공개안전성 재확인(등록 후 목록이 손상/변조됐을 가능성 — fail-closed).
    if SP.classify_source_pointer(url) != "clean":
        return {"status": "BLOCK", "reason": "SOURCE_NOT_PUBLIC", "source_id": sid}
    run = runner or _real_fetch_runner
    try:
        r = run(url)
    except Exception as e:
        return {"status": "ERROR", "reason": "FETCH_ERROR", "source_id": sid, "detail": str(e)[:200]}
    if not isinstance(r, dict) or not r.get("ok") or not r.get("text"):
        return {"status": "ERROR", "reason": "FETCH_EMPTY", "source_id": sid}
    # A4 — 리다이렉트 최종 도착지 재검증(runner 가 final_url 제공 시). 내부/비공개로 튀었으면 거부.
    final_url = _norm_url(r.get("final_url") or url)
    if SP.classify_source_pointer(final_url) != "clean":
        return {"status": "BLOCK", "reason": "REDIRECT_NOT_PUBLIC",
                "source_id": sid, "final_label": SP.classify_source_pointer(final_url)}
    return {"status": "OK", "source_id": sid, "url": url,
            "kind": source.get("kind"), "text": r["text"],
            "raw_bytes": r.get("raw_bytes"), "content_type": r.get("content_type")}


# ── ③ harvest 글루 — fetch 텍스트 → 후보(candidate only). 원문 변형 0 ──────────
def _split_segments(text):
    """fetch 텍스트를 의미 단위 segment 로 분할 — **원문 변형 0**(잘림/요약/리포맷 절대 0).

    분할 규칙: 빈 줄(\\n\\n+) 경계로 문단을 나눈다. 문단이 없으면(단일 블록) 줄 단위로 폴백.
    각 segment 는 strip() 으로 앞뒤 공백만 제거(내부 문자/순서/내용은 1:1 보존). 빈 segment 제외.
    git-diff 전용 capture()(added[:3] 잘림·'변경..' 접두·' | ' 치환)를 쓰지 않는다 — 그게 §1 위배 원인.
    """
    raw = str(text or "")
    # 1차: 빈 줄로 문단 분할(원문 그대로). 마스킹 전 raw 기준으로 경계만 잡는다.
    import re as _re
    paras = [p.strip() for p in _re.split(r"\n[ \t]*\n+", raw) if p.strip()]
    if len(paras) >= 2:
        return paras
    # 폴백: 문단 경계가 없으면 줄 단위(빈 줄 제외). 단일 라인이면 그 1건.
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return lines if lines else ([raw.strip()] if raw.strip() else [])


def _content_chunks(text, source_ref):
    """fetch 텍스트 → evidence_chunk[]. **원문 1:1 보존**(redaction 만, 잘림/요약/포맷변형 0).

    §1(원문 변형 0): 각 segment 의 raw 문자열을 그대로 chunk.text 에 넣는다(secret/PII 마스킹만 적용).
      마스킹 후에도 secret 잔존이면 해당 segment STOP(후보 미생성·누출 차단). git-diff 포맷이 아닌
      arxiv/RSS/HTML/평문 본문도 동일하게 보존된다(capture() 의 diff-only 전량누락 결함 제거).
    반환: (chunks, stops). chunk 는 to_nodes 호환(item_id·text 필수)."""
    chunks, stops = [], []
    for i, seg in enumerate(_split_segments(text)):
        red, resid = _redact_all(seg)                 # secret+PII 마스킹 — 의미 변형 아님(§1 안전벨트)
        if resid:                                      # 마스킹 후 secret/PII 잔존 → STOP(누출 차단·후보 0)
            stops.append({"seg": i, "reason": "secret/PII residual after redaction", "kinds": resid})
            continue
        # item_id = source_ref + 본문 해시(멱등·결정적). text = 마스킹된 원문(잘림/요약 0).
        item_id = "EVC-HV-" + hashlib.sha256(
            (source_ref + "::" + red).encode("utf-8")).hexdigest()[:12]
        chunks.append({
            "item_id": item_id,
            "text": red,
            "source": source_ref,
            "evidence_meta": {
                "confidence": 0.5,
                "source_kind": "harvest",
                "timestamp": "(deterministic-harvest)",
                "scope": mvp1.SCOPE,
                "raw_pointer": source_ref,
                "redaction_applied": True,
            },
        })
    return chunks, stops


# ── 전방위 파싱 2층(원문보존 + 파생) — owner 조건부 GO 반영 ───────────────
#   §1(원문 1:1)은 text/plain·미상 소스에 그대로 유지(기존 _content_chunks 경로).
#   바이너리/HTML 은 parser adapter 로 derived_text(가공본) 생성 — 원문(raw)은 그대로 보관 +
#   fingerprint(raw_sha256) 동봉. 즉 §1 을 '원문(raw) 보존 + 파생(parsed) 명시' 2층으로 확장.
def harvest_raw_dir(home=None):
    return os.path.join(home or _home(), "harvest_raw")


def _store_raw(raw_bytes, home):
    """raw 원문을 그대로 보관(조건1) — <home>/harvest_raw/<sha256>.bin (멱등). sha256 반환.
    home 미지정(selftest 등)이면 보관 skip 하고 fingerprint 만 반환."""
    if not raw_bytes:
        return None
    sha = hashlib.sha256(raw_bytes).hexdigest()
    if home:
        d = harvest_raw_dir(home)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, sha + ".bin")
        if not os.path.exists(p):
            tmp = p + ".tmp"
            with open(tmp, "wb") as f:
                f.write(raw_bytes)
            os.replace(tmp, p)
    return sha


def _maybe_parse(fr):
    """파싱 필요 판정 + parser adapter 호출. 텍스트류/미상이거나 raw_bytes 없으면 None
    (→ 기존 §1 원문보존 경로 유지). mock fetch(raw_bytes 미제공)는 항상 None = 기존 동작 불변."""
    raw = fr.get("raw_bytes")
    if not raw:
        return None
    import binggu_parser_adapter as PA  # lazy(서드파티 미노출·부재해도 harvest 로드 OK)
    fmt = PA.detect_format(fr.get("content_type"), fr.get("url"))
    if fmt in ("text", "unknown"):
        return None  # 평문/미상 → 기존 1:1 경로
    return PA.parse_document(raw, content_type=fr.get("content_type"), filename=fr.get("url"))


def _derived_chunks(derived_text, source_ref, pr):
    """파생 텍스트(가공본) → evidence_chunk[]. text 는 derived 임을 evidence_meta 로 명시
    (derivative=True / parser / raw_sha256). 원문 raw 는 _store_raw 가 별도 보관."""
    chunks, stops = [], []
    for i, seg in enumerate(_split_segments(derived_text)):
        red, resid = _redact_all(seg)                 # secret+PII 마스킹(파생 텍스트도 동일 게이트)
        if resid:
            stops.append({"seg": i, "reason": "secret/PII residual after redaction", "kinds": resid})
            continue
        item_id = "EVC-HVP-" + hashlib.sha256(
            (source_ref + "::" + red).encode("utf-8")).hexdigest()[:12]
        chunks.append({
            "item_id": item_id,
            "text": red,
            "source": source_ref,
            "evidence_meta": {
                "confidence": 0.5,
                "source_kind": "harvest_parsed",
                "timestamp": "(deterministic-harvest)",
                "scope": mvp1.SCOPE,
                "raw_pointer": source_ref,
                "redaction_applied": True,
                "derivative": True,                      # 파생(원문 아님)
                "parser": pr.get("parser"),
                "raw_sha256": pr.get("raw_sha256"),       # 원문 fingerprint
            },
        })
    return chunks, stops


def harvest_one(source, runner=None, sources_path_=None, parse=True, home=None):
    """단일 소스 1건 수확 → 후보 노드(candidate=true). 운영 ledger 미접촉(dict 반환만).

    글루: fetch(게이트①) → [2층: 파싱 필요시 parser adapter / 아니면 §1 원문보존] →
          to_nodes(게이트② candidate 불변식). 파싱 실패는 이 소스만 PARSE_SKIP(전체 안 죽음·조건3).
    """
    fr = fetch_source(source, runner=runner, sources_path_=sources_path_)
    if fr["status"] != "OK":
        return fr
    src_ref = "harvest :: %s :: %s" % (fr.get("kind"), fr.get("source_id"))
    parse_artifacts = []
    pr = _maybe_parse(fr) if parse else None
    if pr is not None:
        raw_sha = _store_raw(fr.get("raw_bytes"), home)  # 조건1 — raw 보관(성패 무관)
        if not pr["ok"]:
            # 조건3 — 이 소스만 typed error 로 skip. run_harvest 가 다음 소스 계속 진행.
            return {"status": "PARSE_SKIP", "source_id": fr["source_id"], "url": fr.get("url"),
                    "kind": fr.get("kind"), "parse_error": pr["error"],
                    "raw_sha256": raw_sha, "nodes": []}
        chunks, ev_stops = _derived_chunks(pr["derived_text"], src_ref, pr)
        parse_artifacts.append({"parser": pr.get("parser"), "fmt": pr.get("fmt"),
                                "raw_sha256": pr.get("raw_sha256"), "derivative": True,
                                "n_chunks": len(chunks)})
    else:
        chunks, ev_stops = _content_chunks(fr["text"], src_ref)  # 기존 §1 1:1 경로(불변)
    nodes, ev_index, node_stops = mvp2.to_nodes(chunks)
    # 게이트② 코드 불변식 — to_nodes 가 candidate=true/promotion_allowed=false 를 강제하지만,
    # 본 모듈에서도 명시 검증(헌법 §6 ②). 위반 시 STOP(적재 0).
    cand_ok = all(n["properties"]["candidate"] is True for n in nodes)
    promo_ok = all(n["promotion_allowed"] is False for n in nodes)
    if nodes and not (cand_ok and promo_ok):
        return {"status": "BLOCK", "reason": "CANDIDATE_INVARIANT_VIOLATION",
                "source_id": fr["source_id"]}
    return {"status": "OK", "source_id": fr["source_id"], "url": fr.get("url"),
            "kind": fr.get("kind"), "nodes": nodes, "evidence_index": ev_index,
            "evidence_chunks": chunks,  # 원본 chunk(evidence_meta: source/raw_pointer/raw_sha256/parser/derivative)
            "stops": ev_stops + node_stops, "parse_artifacts": parse_artifacts,
            "candidate_all_true": cand_ok if nodes else True,
            "promotion_all_false": promo_ok if nodes else True}


# ── dedup 커서 (이미 수확한 항목 재적재 방지) ──────────────────────────
def _node_content_hash(node):
    """후보 문장의 결정적 content_hash(16) — 커서/ledger dedup 양쪽이 같은 키를 쓴다."""
    return hashlib.sha256(node["properties"]["sentence"].encode("utf-8")).hexdigest()[:16]


def read_cursor(path=None):
    """harvested_cursor.json → 이미 적재된 content_hash 집합. 부재/손상 = 빈 집합(전부 신규로 취급)."""
    path = path or harvest_cursor_path()
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return set()
    return set(d.get("hashes", []))


def write_cursor(hashes, path=None):
    path = path or harvest_cursor_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"hashes": sorted(hashes), "ts": time.time()}, f, ensure_ascii=False)
    os.replace(tmp, path)  # atomic
    return path


def append_harvest_log(entry, path=None):
    path = path or harvest_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


# ── ④ orchestrator — 등록 소스 전부 1회 수확 → 후보 적재(영구화 0) ────────────
def _insert_candidates(ledger_path, nodes):
    """수확 노드를 운영 ledger 에 candidate=1, state=NULL 로만 INSERT. active 승격 절대 0.

    헌법 §6② — 후보로만. 기존 active 노드 미접촉(이 함수는 candidate INSERT 만 함).
    content_hash 중복(이미 candidate/active) 은 ledger 내에서도 skip(dedup 양쪽 체크).
    """
    import sqlite3
    inserted, skipped = 0, 0
    con = sqlite3.connect(ledger_path)
    _plat.apply_ledger_pragmas(con)
    try:
        # nodes 테이블이 없으면(빈 환경) 적재 0 — init 으로 장부 생성이 선행돼야 함.
        cols = {r[1] for r in con.execute("PRAGMA table_info(nodes)")}
        if not cols:
            return {"inserted": 0, "skipped": 0, "reason": "NO_NODES_TABLE"}
        for n in nodes:
            sent = n["properties"]["sentence"]
            chash = _node_content_hash(n)
            # ledger 내 동일 content_hash 존재(candidate 든 active 든) → skip(중복 적재 방지).
            if "content_hash" in cols:
                exists = con.execute(
                    "SELECT 1 FROM nodes WHERE content_hash=? LIMIT 1", (chash,)).fetchone()
                if exists:
                    skipped += 1
                    continue
            node_id = "node:HARVEST:" + chash
            ntype = n.get("node_type", "concept")
            # candidate=1, state=NULL(미확정) — 영구화 0. promotion_allowed 컬럼 있으면 0.
            field_vals = {"node_id": node_id, "node_type": ntype, "sentence": sent,
                          "candidate": 1, "state": None, "content_hash": chash,
                          "promotion_allowed": 0}
            usable = [c for c in field_vals if c in cols]
            con.execute("INSERT INTO nodes (%s) VALUES (%s)"
                        % (",".join(usable), ",".join("?" for _ in usable)),
                        tuple(field_vals[c] for c in usable))
            inserted += 1
        con.commit()
    finally:
        con.close()
    return {"inserted": inserted, "skipped": skipped}


def run_harvest(ledger_path=None, home=None, runner=None, sources_path_=None, persist=True):
    """등록 소스 전부 1회 수확 → 후보(candidate=1) 적재. active 미접촉. 반환 dict.

    매개변수 전부 selftest 주입용(기본은 실 경로). runner 기본 실 fetch — owner 스케줄러 전용.
    selftest 는 runner mock + 모든 경로 temp(실 네트워크/ledger/소스장 미접촉).
    """
    home = home or _home()
    ledger_path = ledger_path or _plat.default_ledger()
    sources_path_ = sources_path_ or sources_path(home)
    base = {"ledger": ledger_path, "ts": time.time(), "active_write": 0}

    # 긴급 스위치 — 최우선 no-op
    if os.path.exists(harvest_disabled_path(home)):
        return dict(base, status="NOOP", reason="HARVEST_DISABLED",
                    fetched=0, candidates=0, skipped=0)

    # 게이트① — 등록 소스 목록. 빈 목록(미등록) → 수확 0(fail-closed).
    sources = load_sources(sources_path_)
    if not sources:
        append_harvest_log({"ts": base["ts"], "result": "NO_SOURCES", "fetched": 0}, harvest_log_path(home))
        return dict(base, status="NOOP", reason="NO_REGISTERED_SOURCES",
                    fetched=0, candidates=0, skipped=0)

    cursor = read_cursor(harvest_cursor_path(home))
    all_new_nodes, fetched, src_results = [], 0, []
    for s in sources:
        one = harvest_one(s, runner=runner, sources_path_=sources_path_, home=home)
        src_results.append({"source_id": s.get("source_id"), "status": one["status"],
                            "reason": one.get("reason"),
                            "parse_error": (one.get("parse_error") or {}).get("type"),
                            "n_nodes": len(one.get("nodes", []))})
        if one["status"] != "OK":
            continue
        fetched += 1
        # 커서 dedup — 이미 수확한 content_hash 는 제외(재적재 방지).
        for n in one["nodes"]:
            ch = _node_content_hash(n)
            if ch in cursor:
                continue
            all_new_nodes.append(n)
            cursor.add(ch)

    candidates, skipped, ins_reason = 0, 0, None
    if all_new_nodes and persist and os.path.exists(ledger_path):
        ins = _insert_candidates(ledger_path, all_new_nodes)
        candidates, skipped, ins_reason = ins["inserted"], ins["skipped"], ins.get("reason")
        if candidates:
            write_cursor(cursor, harvest_cursor_path(home))
    elif all_new_nodes and persist and not os.path.exists(ledger_path):
        ins_reason = "LEDGER_ABSENT"

    entry = {"ts": base["ts"], "result": "HARVESTED", "fetched": fetched,
             "candidates": candidates, "skipped": skipped, "sources": src_results,
             "insert_reason": ins_reason}
    append_harvest_log(entry, harvest_log_path(home))
    return dict(base, status="OK", reason="HARVESTED", fetched=fetched,
                candidates=candidates, skipped=skipped, sources=src_results,
                insert_reason=ins_reason)


# ---------------- 셀프테스트 (fetch mock · temp 경로만 — 실 네트워크/ledger/소스장 0) -------
def _selftest():
    import sqlite3
    import tempfile
    os.environ["BINGGU_PARSER_CLI_OFF"] = "1"   # selftest 결정성 — parser 실 CLI(uvx/npx) 0, plain 폴백
    ok = 0
    tot = 0

    def chk(name, cond):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("  PASS " if cond else "  FAIL ") + name)

    work = tempfile.mkdtemp(prefix="harvest_st_")
    home = os.path.join(work, ".binggupack")
    os.makedirs(home)
    sp = sources_path(home)
    lp = os.path.join(work, "ledger.sqlite")

    # 빈 환경 ledger(nodes 테이블 — 운영 스키마 모사)
    def make_ledger(p):
        c = sqlite3.connect(p)
        c.executescript(
            "CREATE TABLE nodes(node_id TEXT,node_type TEXT,sentence TEXT,candidate INT,"
            "state TEXT,content_hash TEXT,promotion_allowed INT);")
        c.commit()
        c.close()
    make_ledger(lp)

    # 실세계 수확물(비 git-diff 평문/abstract). 문단 4개 — 4번째까지 전량 보존돼야 함(잘림 0 검증).
    SEG1 = "이 논문은 그래프 기반 메모리 구조의 새 접근을 제안한다."
    SEG2 = "실험 결과 회상 정확도가 향상되었다고 보고한다."
    SEG3 = "제안 방법은 세 개의 벤치마크 데이터셋에서 일반화된다."
    SEG4 = "네 번째 문단은 절대 누락되면 안 되는 본문 내용이다."
    SAMPLE = "\n\n".join([SEG1, SEG2, SEG3, SEG4])
    SAMPLE_SEGS = [SEG1, SEG2, SEG3, SEG4]
    SECRET_SAMPLE = ("api_key=" + "AKIA" + "IOSFODNN7" + "EXAMPLE 가 노출된 사례를 다룬다.")

    fetch_calls = []

    def mock_fetch(text, final_url=None):
        def _run(url, timeout=30):
            fetch_calls.append(url)
            return {"ok": True, "text": text, "url": url,
                    "final_url": final_url or url}
        return _run

    def run(**kw):
        return run_harvest(ledger_path=lp, home=home, sources_path_=sp, **kw)

    # T1 빈 화이트리스트 → fetch 시도 0(소스 없음)
    fetch_calls.clear()
    r = run(runner=mock_fetch(SAMPLE))
    chk("T1 빈 화이트리스트 → NOOP(fetch 0)",
        r["status"] == "NOOP" and r["reason"] == "NO_REGISTERED_SOURCES"
        and r["fetched"] == 0 and len(fetch_calls) == 0)

    # T2 소스 등록 — 공개 URL 만 통과(내부/로컬은 거부)
    PUB = "https://arxiv.org/abs/2601.00001"
    chk("T2a 공개 URL 등록 OK", add_source("arxiv", PUB, path=sp)["status"] == "OK")
    chk("T2b 로컬경로 소스 등록 거부",
        add_source("url", "C:\\Users\\PC\\private\\notes.md", path=sp)["reason"] == "SOURCE_NOT_PUBLIC")
    chk("T2c 내부 IP 소스 등록 거부",
        add_source("url", "http://192.168.0.10/api", path=sp)["reason"] == "SOURCE_NOT_PUBLIC")
    chk("T2d 잘못된 kind 거부", add_source("ftp", PUB + "/x", path=sp)["reason"] == "BAD_KIND")
    chk("T2e 멱등 재등록(같은 url 1건)",
        add_source("arxiv", PUB, path=sp)["reason"] == "ALREADY_REGISTERED"
        and len(load_sources(sp)) == 1)

    # T3 mock fetch 텍스트 → candidate=true·promotion_allowed=false 후보만 생성(영구 0)
    fetch_calls.clear()
    mt_before = os.path.getmtime(lp)
    before_active = sqlite3.connect(lp).execute(
        "SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    r = run(runner=mock_fetch(SAMPLE))
    chk("T3 등록 소스 → 수확 진행(fetch 1)",
        r["status"] == "OK" and r["fetched"] == 1 and len(fetch_calls) == 1)
    chk("T3b 후보 적재(candidate>0)", r["candidates"] > 0)
    con = sqlite3.connect(lp)
    cand_rows = con.execute("SELECT candidate,state,promotion_allowed FROM nodes").fetchall()
    con.close()
    chk("T3c 적재 전건 candidate=1 · state=NULL · promotion_allowed=0",
        all(row[0] == 1 and row[1] is None and row[2] == 0 for row in cand_rows) and len(cand_rows) > 0)
    chk("T3d active 노드 0(영구화 0)",
        sqlite3.connect(lp).execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0] == 0)

    # T4 원문 변형 0 — fetch 원문 segment 전건이 후보 sentence 에 **글자 단위 1:1**(잘림/요약/접두/구분자 0).
    con = sqlite3.connect(lp)
    sents = set(r2[0] for r2 in con.execute("SELECT sentence FROM nodes").fetchall())
    con.close()
    # (a) 4개 segment 전부 정확히 동일 문자열로 존재(부분일치 아님 — 집합 동일성)
    chk("T4 원문 1:1 보존(4 segment 전건 글자단위 동일)",
        all(seg in sents for seg in SAMPLE_SEGS))
    # (b) 4번째 문단 누락 0(과거 added[:3] 잘림 결함 회귀 차단)
    chk("T4b 4번째 segment 누락 0", SEG4 in sents)
    # (c) git-diff 접두/구분자 변형 0 — '변경 '·' | ' 같은 capture() 리포맷 흔적이 전혀 없어야 함
    chk("T4c 리포맷 흔적 0(변경 접두·' | ' 치환 없음)",
        not any(("변경 " in s or " | " in s or "(+" in s) for s in sents))

    # T4d 비 git-diff 평문(arxiv abstract 모사) → 후보 생성(과거 capture() 는 events=0 전량소실).
    PLAIN_URL = "https://export.arxiv.org/abs/2601.09999"
    add_source("arxiv", PLAIN_URL, path=sp)
    plain = ("Abstract: We introduce a retrieval scheme.\n\n"
             "It improves accuracy on long-context tasks.")
    r = run(runner=mock_fetch(plain))
    chk("T4d 비-diff 평문 → 후보 생성(전량소실 결함 제거)", r["candidates"] >= 2)
    con = sqlite3.connect(lp)
    has_plain = con.execute(
        "SELECT count(*) FROM nodes WHERE sentence=?",
        ("Abstract: We introduce a retrieval scheme.",)).fetchone()[0]
    con.close()
    chk("T4e 비-diff 평문 segment 1:1 보존", has_plain == 1)
    remove_source(source_id_for(PLAIN_URL), path=sp)

    # T5 운영(=현 temp) ledger active 미접촉 — active count 불변
    after_active = sqlite3.connect(lp).execute(
        "SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    chk("T5 active 미접촉(수확 전후 active count 동일=0)", before_active == after_active == 0)

    # T6 멱등 dedup — 같은 소스 재수확 시 신규 후보 0(커서가 막음)
    r = run(runner=mock_fetch(SAMPLE))
    chk("T6 재수확 → 신규 후보 0(커서 dedup)", r["candidates"] == 0)

    # T7 secret 잔존 → STOP(후보 미생성). secret 문장은 to_evidence 단계 STOP.
    SECRET_URL = "https://github.com/owner/repo"
    add_source("github", SECRET_URL, path=sp)
    r = run(runner=mock_fetch(SECRET_SAMPLE))
    con = sqlite3.connect(lp)
    leaked = con.execute("SELECT count(*) FROM nodes WHERE sentence LIKE '%AKIA%'").fetchone()[0]
    con.close()
    chk("T7 secret 원문 잔존 0(STOP — ledger 에 raw secret 0)", leaked == 0)

    # T8 미등록 소스 직접 fetch 시도 → 거부(게이트① 런타임 deny-by-default)
    bogus = {"source_id": "src:deadbeefdead", "kind": "url",
             "url": "https://evil.example.com/x"}
    fetch_calls.clear()
    fr = fetch_source(bogus, runner=mock_fetch(SAMPLE), sources_path_=sp)
    chk("T8 미등록 소스 fetch 거부", fr["status"] == "BLOCK" and fr["reason"] == "SOURCE_NOT_REGISTERED")

    # T8b A2 — 등록 source_id + 미등록 url 디커플링 공격: source_id 를 신뢰하면 통과하는 케이스.
    #   게이트는 url 에서 sid 재산출 → 미등록 url 이므로 BLOCK, fetch runner 호출 0(긁기 자체 0).
    PUB2 = "https://arxiv.org/abs/2601.00002"
    add_source("arxiv", PUB2, path=sp)                       # 정당 등록 소스(그 source_id 만 도용)
    decoupled = {"source_id": source_id_for(PUB2),            # ← 등록된 sid
                 "kind": "url", "url": "https://evil.example.com/inject"}  # ← 미등록 url
    fetch_calls.clear()
    fr = fetch_source(decoupled, runner=mock_fetch(SAMPLE), sources_path_=sp)
    chk("T8b A2 디커플링(등록 sid + 미등록 url) → BLOCK",
        fr["status"] == "BLOCK" and fr["reason"] == "SOURCE_NOT_REGISTERED"
        and fr["source_id"] == source_id_for("https://evil.example.com/inject"))
    chk("T8b2 A2 — fetch runner 미호출(긁기 0)", len(fetch_calls) == 0)
    # 전체 파이프라인으로도 후보 0 확인(운영 ledger 미오염)
    one = harvest_one(decoupled, runner=mock_fetch(SAMPLE), sources_path_=sp)
    chk("T8b3 A2 — harvest_one 도 BLOCK(후보 노드 0)",
        one["status"] == "BLOCK" and not one.get("nodes"))
    remove_source(source_id_for(PUB2), path=sp)

    # T8c A4 — redirect 최종 도착지 재검증: 등록 clean 소스가 내부(169.254.169.254)로 302 → BLOCK.
    REDIR_URL = "https://feed.example.com/rss"
    add_source("rss", REDIR_URL, path=sp)
    redir_source = {"kind": "rss", "url": REDIR_URL}          # source_id 는 url 에서 재산출
    fr = fetch_source(redir_source,
                      runner=mock_fetch(SAMPLE, final_url="http://169.254.169.254/latest/meta-data"),
                      sources_path_=sp)
    chk("T8c A4 redirect→내부 IP 재검증 BLOCK",
        fr["status"] == "BLOCK" and fr["reason"] == "REDIRECT_NOT_PUBLIC")
    # clean→clean redirect 는 정상 통과
    fr2 = fetch_source(redir_source,
                       runner=mock_fetch(SAMPLE, final_url="https://feed.example.com/rss/final"),
                       sources_path_=sp)
    chk("T8c2 A4 clean→clean redirect 정상 통과", fr2["status"] == "OK")
    remove_source(source_id_for(REDIR_URL), path=sp)

    # T8d A7 — SSRF 우회 IP/이름 표기는 등록 단계에서 전부 거부(공개안전성 게이트).
    EVASIONS = ["http://2130706433", "http://0x7f000001", "http://127.1",
                "http://169.254.169.254/latest/meta-data", "http://0177.0.0.1",
                "http://[::1]/x", "http://0xA9FEA9FE/meta"]
    all_blocked = all(add_source("url", u, path=sp)["reason"] == "SOURCE_NOT_PUBLIC" for u in EVASIONS)
    chk("T8d A7 SSRF 우회 표기 전건 등록 거부(SOURCE_NOT_PUBLIC)", all_blocked)

    # T9 긴급 스위치 → 즉시 NOOP(fetch 0)
    flag = harvest_disabled_path(home)
    with open(flag, "w") as f:
        f.write("off")
    fetch_calls.clear()
    r = run(runner=mock_fetch(SAMPLE))
    chk("T9 harvest_disabled → NOOP(fetch 0)",
        r["status"] == "NOOP" and r["reason"] == "HARVEST_DISABLED" and len(fetch_calls) == 0)
    os.remove(flag)

    # T10 ledger 파일 write 0 검증(persist=False 면 운영 ledger 미접촉)
    lp2 = os.path.join(work, "ledger2.sqlite")
    make_ledger(lp2)
    mt2 = os.path.getmtime(lp2)
    run_harvest(ledger_path=lp2, home=home, sources_path_=sp, runner=mock_fetch(SAMPLE), persist=False)
    chk("T10 persist=False → ledger write 0(mtime 불변)", os.path.getmtime(lp2) == mt2)

    # T11 소스 제거 → 화이트리스트에서 빠지면 다시 수확 0
    sid = source_id_for(PUB)
    chk("T11a 소스 제거", remove_source(sid, path=sp)["reason"] == "REMOVED")
    chk("T11b 제거 후 미등록", not is_registered(sid, sp))

    # T12 fetch 어댑터가 urllib(표준) 만 — 실제 import 문에 서드파티 0.
    #   (이 selftest 문자열 자체가 오탐되지 않게 실제 import 행만 골라 검사.)
    import_lines = [ln.strip() for ln in open(os.path.abspath(__file__), encoding="utf-8")
                    if ln.strip().startswith(("import ", "from "))]
    third = ("requests", "feedparser", "bs4", "lxml", "aiohttp", "httpx")
    chk("T12 서드파티 import 0 + urllib 사용",
        not any(any(t in ln for t in third) for ln in import_lines)
        and "urllib.request" in open(os.path.abspath(__file__), encoding="utf-8").read())

    # ── T13~T15 전방위 파싱 2층(원문보존 + 파생) — raw_bytes 주는 mock 으로 harvest_one 직접 검증 ──
    def raw_runner(raw_bytes, ctype):
        def _run(url, timeout=30):
            return {"ok": True, "text": raw_bytes.decode("utf-8", "replace"),
                    "url": url, "final_url": url, "raw_bytes": raw_bytes, "content_type": ctype}
        return _run

    add_source("url", "https://example.org/doc.html", path=sp)
    src = {"url": "https://example.org/doc.html", "kind": "url",
           "source_id": source_id_for("https://example.org/doc.html")}
    html_raw = (b"<html><body><p>" + SEG1.encode("utf-8") + b"</p><p>"
                + SEG4.encode("utf-8") + b"</p></body></html>")
    one = harvest_one(src, runner=raw_runner(html_raw, "text/html"), sources_path_=sp, home=home)
    chk("T13 HTML derived 수확 OK", one["status"] == "OK" and len(one["nodes"]) > 0)
    chk("T13b parse_artifacts derivative=True",
        bool(one.get("parse_artifacts")) and one["parse_artifacts"][0]["derivative"] is True)
    raw_sha = hashlib.sha256(html_raw).hexdigest()
    raw_p = os.path.join(harvest_raw_dir(home), raw_sha + ".bin")
    chk("T13c raw 원문 그대로 보관(조건1)", os.path.exists(raw_p))
    chk("T13d 보관 raw == 원본 bytes(무변형)",
        os.path.exists(raw_p) and open(raw_p, "rb").read() == html_raw)
    chk("T13e derived 노드도 candidate=1/promotion=0",
        all(n["properties"]["candidate"] is True and n["promotion_allowed"] is False
            for n in one["nodes"]))

    add_source("url", "https://example.org/report.pdf", path=sp)
    src_pdf = {"url": "https://example.org/report.pdf", "kind": "url",
               "source_id": source_id_for("https://example.org/report.pdf")}
    pdf_raw = b"%PDF-1.4 broken-not-a-real-pdf"
    one2 = harvest_one(src_pdf, runner=raw_runner(pdf_raw, "application/pdf"), sources_path_=sp, home=home)
    chk("T14 파싱 실패 → 이 소스만 PARSE_SKIP(전체 안 죽음·조건3)", one2["status"] == "PARSE_SKIP")
    chk("T14b PARSE_SKIP 도 raw 보관(fingerprint)",
        one2.get("raw_sha256") == hashlib.sha256(pdf_raw).hexdigest())
    chk("T14c typed error 동봉",
        (one2.get("parse_error") or {}).get("type") in
        ("BACKEND_NOT_WIRED", "BACKEND_CALL_FAILED", "PARSER_MISSING",
         "UNSUPPORTED_FORMAT", "PARSER_FAILED", "EMPTY_RESULT", "CORRUPT_DOCUMENT"))

    add_source("url", "https://example.org/feed.txt", path=sp)
    src_txt = {"url": "https://example.org/feed.txt", "kind": "url",
               "source_id": source_id_for("https://example.org/feed.txt")}
    plain_raw = SAMPLE.encode("utf-8")
    one3 = harvest_one(src_txt, runner=raw_runner(plain_raw, "text/plain"), sources_path_=sp, home=home)
    chk("T15 text/plain → §1 원문보존 경로(파생 아님)",
        one3["status"] == "OK" and not one3.get("parse_artifacts"))
    chk("T15b 평문 segment 1:1 보존(파생 경로 미적용)",
        set(SAMPLE_SEGS) <= set(n["properties"]["sentence"] for n in one3["nodes"]))

    # T16 — B3 PII redaction(전화/이메일). PII 포함 텍스트 수확 → 노드/chunk 잔존 0.
    add_source("url", "https://example.org/pii.txt", path=sp)
    src_pii = {"url": "https://example.org/pii.txt", "kind": "url",
               "source_id": source_id_for("https://example.org/pii.txt")}
    pii_text = "문의는 010-1234-5678 또는 hong@example.com 으로 연락 바랍니다 자세한 본문 내용."
    onep = harvest_one(src_pii, runner=raw_runner(pii_text.encode("utf-8"), "text/plain"),
                       sources_path_=sp, home=home)
    chk("T16 PII 포함 수확 OK(STOP 아님)", onep["status"] == "OK" and len(onep["nodes"]) > 0)
    _allsent = " ".join(n["properties"]["sentence"] for n in onep["nodes"])
    _allchunk = " ".join(c["text"] for c in onep.get("evidence_chunks", []))
    chk("T16b 노드 sentence PII/secret 잔존 0", not bm1.scan_residual_pii(_allsent))
    chk("T16c evidence_chunk PII/secret 잔존 0", not bm1.scan_residual_pii(_allchunk))
    chk("T16d 전화/이메일 원문 미노출",
        "010-1234-5678" not in _allsent and "hong@example.com" not in _allsent)

    print("\nRESULT: %d/%d %s" % (ok, tot, "PASS" if ok == tot else "FAIL"))
    print("GATE: %s" % ("GO" if ok == tot else "BLOCK"))
    return ok == tot


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # 운영 실행(owner ScheduledTask 전용 — 실 네트워크 fetch). 평소 owner 만 실행.
    print(json.dumps(run_harvest(), ensure_ascii=False, indent=2, default=str))
