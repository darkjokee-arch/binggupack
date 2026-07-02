# -*- coding: utf-8 -*-
"""OpenBinggu Watcher M1 — 수동 다중 source batch 운영모드 (dry-run only).

M0 단일 git diff → 다중 source(git_diff + transcript_summary + md) 1회 수동 batch 확장.
hook/daemon 없음. temp/staging only. 운영 store/OpenCrab/DB write 0.

재사용(무수정): watcher_capture_mvp1(capture/to_evidence/redact_text) · watcher_candidate_mvp2(to_nodes/_meaningful)
  · watcher_edge_mvp21(build_edges, fan-out cap) · watcher_op_m0(_store_snapshot) ·
  openbinggu_pack_review_e2e(pack_to_staging_plan/bridge/resolver) · openbinggu_pack_consumer_smoke(consume).
신규: batch manifest 처리 · transcript/md 어댑터 · PII redaction 확장(batch 전용, 기존 redact_text 무수정).

강제: candidate=true / promotion_allowed=false / origin=watcher / node→node edge 0 / redaction_required=true.
실패: 변환·접근 실패=per-source HOLD(skipped/failed, batch 계속) / secret·PII 잔존(FN)=전체 STOP.
STOP: hook/daemon · raw session jsonl · 실/private 외부전송 · secret/PII raw 잔존 · temp 외 write ·
  OpenCrab/store/DB write · v09/ARMED/apply · push · bid-engine · candidate→confirmed · redaction 복원 · node→node edge.

CLI:
  python watcher_batch_m1.py --selftest
  python watcher_batch_m1.py <batch_manifest.json>
"""
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_m1_batch"
TMP_OUT = BASE / "tmp" / "watcher_batch_m1"
SELFTEST_REPORT = BASE / "reports" / "watcher_batch_m1_selftest.json"

sys.path.insert(0, str(SCRIPTS))
import watcher_capture_mvp1 as mvp1
import watcher_candidate_mvp2 as mvp2
import watcher_edge_mvp21 as edgemod
import watcher_op_m0 as m0
import openbinggu_pack_review_e2e as reviewe2e
import openbinggu_pack_consumer_smoke as consumer

SCOPE = "project:openbinggu"
SOURCE_TYPES = {"git_diff", "transcript", "md"}

def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


# ---------- PII redaction (복합 판단: shape + field/context + whitelist/denylist) ----------
# 기존 secret 공용함수(mvp1.redact_text/_has_secret) 무수정. PII 판단은 이 모듈에서 독립 수행.
# 한글-숫자 인접 미매칭(\b 결함) 회피: word boundary 대신 숫자 lookaround 경계 사용.
_NL = r"(?<![0-9])"   # 좌 숫자 경계
_NR = r"(?![0-9])"    # 우 숫자 경계

# 명확한 PII shape (무하이픈/공백/점/+82 변형 포함). 형태가 명확하므로 도메인 문맥과 무관하게 마스킹.
# 이메일 TLD: ASCII(2+) | punycode(xn--..) | 한글 TLD(.한국 등). 신용카드/AKIA/vendor 토큰은 secret 계열.
_EMAIL_TLD = r"(?:[A-Za-z]{2,}|xn--[A-Za-z0-9-]+|[가-힣]{2,})"

# ---- 한국 주소/이름 PII (Fix B) ----
# 시/도 화이트리스트(고정 17 + 전체명). bare("서울")·suffixed("서울시") 둘 다. 문법어 오탐 억제 위해 화이트리스트로 앵커.
_SIDO = (r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주"
         r"|충청북|충청남|전라북|전라남|경상북|경상남|강원특별자치)"
         r"(?:특별자치시|특별자치도|특별시|광역시|자치시|자치도|도|시)?")
# 행정/도로 토큰: …구/군/시/읍/면/동/로/길, 뒤가 한글이 아니어야 함(우리집·처리·활동 부분매칭 차단). '리/가' 제외(우리/거리·조사 오탐).
_ADDR_TOKEN = r"(?:\s*[가-힣A-Za-z0-9]{1,12}(?:구|군|시|읍|면|동|로|길)(?![가-힣]))"
# 계층 주소: 시/도 + 토큰1+ + 지번/도로번호(필수, 오탐 억제 핵심) [+ 동/호]. 번호 없으면 주소로 보지 않음.
_KR_ADDRESS = re.compile(
    _SIDO + _ADDR_TOKEN + r"+"
    + r"\s*\d{1,5}(?:-\d{1,4})?(?:번지)?"
    + r"(?:\s*\d{1,4}동)?(?:\s*\d{1,4}호)?")
# 아파트/건물 동·호 쌍 — 시/도 없이도 거주지 식별(숫자+동+숫자+호). "101동 202호".
_KR_DONGHO = re.compile(r"(?<![0-9])\d{1,4}동\s*\d{1,4}호(?![0-9])")
# 이름: 강한 라벨(이름/성명/성함) + 구분자(콜론/조사+공백/공백) + 2~4자 한글. group(1)만 마스킹(보수적, 라벨 오탐 최소).
_KR_NAME = re.compile(r"(?:이름|성명|성함)(?:\s*[:：=]\s*|\s*(?:은|는|이|가)\s+|\s+)([가-힣]{2,4})(?![가-힣])")

PII_SHAPES = [
    ("rrn",            re.compile(_NL + r"\d{6}[-\s.]?\d{7}" + _NR)),                                  # 주민(13)
    ("phone_mobile",   re.compile(_NL + r"(?:\+?82[-\s.]?)?0?1[016789][-\s.]?\d{3,4}[-\s.]?\d{4}" + _NR)),
    ("phone_landline", re.compile(_NL + r"(?:\+?82[-\s.]?)?0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}" + _NR)),
    # 신용카드: 4-4-4-4 (구분자 -/공백/점 또는 무구분 16자리). Luhn 불요 형태매칭. 사업자번호(3-2-5)와 자릿수 상이.
    ("credit_card",    re.compile(_NL + r"\d{4}[-\s.]?\d{4}[-\s.]?\d{4}[-\s.]?\d{4}" + _NR)),
    ("email",          re.compile(r"[A-Za-z0-9._%+\-가-힣]+@[A-Za-z0-9.\-가-힣]+\." + _EMAIL_TLD)),
    # AWS access key: AKIA + 7자 이상(짧은 변형까지 공격적으로).
    ("aws_akia",       re.compile(r"\bAKIA[0-9A-Z]{7,}")),
    # vendor 토큰: sk-live-/sk-/ghp_/gho_/ghs_ 등 prefix + 충분 길이.
    ("vendor_token",   re.compile(r"\b(?:sk-live-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{16,}|gh[oprsu]_[A-Za-z0-9]{20,})")),
    # bearer 토큰: 'bearer ' + 긴 토큰.
    ("bearer_token",   re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    # 긴 base64-ish secret (>=32, 영숫자+/=_-). 카드/주민 등 숫자열은 먼저 매칭되어 제외됨.
    ("b64_secret",     re.compile(r"\b[A-Za-z0-9+/=_-]{32,}\b")),
    # 한국 주소(계층/동호). full-span 마스킹 — 기존 겹침정리 로직이 서브스팬 흡수.
    ("kr_address",     _KR_ADDRESS),
    ("kr_dongho",      _KR_DONGHO),
]

# 도메인 식별자 형태 — 문맥이 명확히 일치할 때만 보존(아니면 마스킹+review_flag).
BIZNO_SHAPE = re.compile(_NL + r"\d{3}-\d{2}-\d{5}" + _NR)   # 사업자등록번호 10자리

# 보존(whitelist) 문맥 — 필드명 또는 주변 문맥에 존재해야 보존.
WHITELIST_CONTEXT = ("사업자등록번호", "사업자번호", "등록번호", "공고번호", "입찰번호", "계약번호",
                     "공고", "입찰공고", "계약", "biz", "bizno", "notice_no", "bid_no", "contract_no")
# denylist — 존재하면 whitelist 무효화(무조건 마스킹). 인증/토큰/키 계열.
DENYLIST_CONTEXT = ("인증", "토큰", "비밀", "비번", "패스워드", "credential", "cert", "token",
                    "api", "apikey", "api_key", "key", "session", "cookie", "fingerprint",
                    "secret", "password", "passwd", "private")

# substring 오매칭 차단 — ASCII 토큰은 경계(\b 또는 _ 구분자), 한글 토큰은 인접 한글 미존재로 경계 판정.
# 'key' in turkey/monkey, '공고' in 공고문 등 부분일치 제거. 정규식 캐시(모듈 1회 컴파일).
def _compile_kw(kw):
    if re.fullmatch(r"[A-Za-z0-9_]+", kw):
        # ASCII 식별자: 영숫자/언더스코어 경계. notice_no, api_key 등 _ 포함 토큰도 단일 토큰 취급.
        return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(kw) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
    # 한글(또는 혼합) 키워드: 좌우 한글 인접 시 부분일치로 간주, 경계 요구.
    return re.compile(r"(?<![가-힣])" + re.escape(kw) + r"(?![가-힣])")

_WHITELIST_RX = [_compile_kw(k) for k in WHITELIST_CONTEXT]
_DENYLIST_RX = [_compile_kw(k) for k in DENYLIST_CONTEXT]


def _ctx_window(text, start, end, w=20):
    return text[max(0, start - w):min(len(text), end + w)]


def _domain_preserve(ctx, field):
    """문맥/필드명이 도메인 식별자로 명확하고 denylist 아니면 True(보존). 토큰 경계 매칭."""
    joined = (field or "") + " " + ctx
    if any(rx.search(joined) for rx in _DENYLIST_RX):
        return False
    return any(rx.search(joined) for rx in _WHITELIST_RX)


def batch_redact(text, field_name=""):
    """secret(mvp1, 무수정) + PII 복합 판단. 정규식 단독 X — shape+문맥+whitelist/denylist.
    returns (redacted, hits, review_flag). 애매한 도메인 숫자열은 raw 보존 금지 → 마스킹+review."""
    red, hits = mvp1.redact_text(text)   # secret 동작 그대로(회귀 0)
    field = field_name or ""
    review = False

    # 1단계: 보존할 도메인 식별자 span 확정 (사업자번호 형태 + 문맥 일치 + not denylist)
    preserve = []
    for m in BIZNO_SHAPE.finditer(red):
        ctx = _ctx_window(red, m.start(), m.end())
        if _domain_preserve(ctx, field):
            preserve.append((m.start(), m.end()))
        else:
            review = True   # 사업자번호 형태인데 문맥 불충분 = 애매 → 마스킹 대상 + review

    def _in_preserve(s, e):
        return any(not (e <= ps or s >= pe) for ps, pe in preserve)

    # 2단계: PII shape 후보 수집 (보존 span 제외)
    cands = []
    for kind, pat in PII_SHAPES:
        for m in pat.finditer(red):
            if not _in_preserve(m.start(), m.end()):
                cands.append((m.start(), m.end(), kind))
    # 애매 사업자번호(보존 실패)도 마스킹 후보로 편입
    for m in BIZNO_SHAPE.finditer(red):
        if not _in_preserve(m.start(), m.end()):
            cands.append((m.start(), m.end(), "bizno_ambiguous"))
    # 한국 이름(라벨 문맥): 라벨 제외 이름 group(1)만 마스킹
    for m in _KR_NAME.finditer(red):
        if not _in_preserve(m.start(1), m.end(1)):
            cands.append((m.start(1), m.end(1), "kr_name"))

    # 3단계: 겹침 정리(좌→우, 긴 것 우선) 후 우→좌 치환
    cands.sort(key=lambda c: (c[0], -(c[1] - c[0])))
    merged = []
    for s, e, k in cands:
        if merged and s < merged[-1][1]:
            continue
        merged.append((s, e, k))
    out = red
    for s, e, k in sorted(merged, key=lambda c: -c[0]):
        out = out[:s] + ("[REDACTED:%d]" % (e - s)) + out[e:]
        hits += 1
    return out, hits, review


# ---------- 독립 잔존 scanner (redactor 로직 import 안 함 — 별도 shape, 더 공격적) ----------
# redactor가 놓친 PII/secret 형태를 다른 로직으로 잡는다(검증자≠피검증자).
# boundary 없이 광범위 탐지. 도메인 식별자(사업자번호 10자리)는 패턴에 안 걸려 오탐 0.
_SCAN_SHAPES = [
    ("scan_rrn",      re.compile(r"\d{6}[-\s.]\d{7}")),
    ("scan_rrn_nohp", re.compile(r"(?<![0-9])\d{13}(?![0-9])")),
    ("scan_mobile",   re.compile(r"(?<![0-9])0?1[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?![0-9])")),
    ("scan_landline", re.compile(r"(?<![0-9])0\d{1,2}[-\s.]\d{3,4}[-\s.]\d{4}(?![0-9])")),
    # 신용카드 4-4-4-4 (구분자 또는 무구분 16자리). 사업자번호 10자리(3-2-5)는 미매칭.
    ("scan_credit_card", re.compile(r"(?<![0-9])\d{4}[-\s.]?\d{4}[-\s.]?\d{4}[-\s.]?\d{4}(?![0-9])")),
    # 이메일: ASCII | punycode | 한글 TLD.
    ("scan_email",    re.compile(r"[A-Za-z0-9._%+\-가-힣]+@[A-Za-z0-9.\-가-힣]+\.(?:[A-Za-z]{2,}|xn--[A-Za-z0-9-]+|[가-힣]{2,})")),
    # AKIA: 7자 이상(짧은 변형 포함, redactor와 동일 임계).
    ("scan_aws",      re.compile(r"\bAKIA[0-9A-Z]{7,}")),
    # vendor 토큰: sk-live/sk-/ghp_ 등.
    ("scan_vendor",   re.compile(r"\b(?:sk-live-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{16,}|gh[oprsu]_[A-Za-z0-9]{20,})")),
    # bearer 토큰.
    ("scan_bearer",   re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    # 값이 영숫자/특수 토큰일 때만 secret (한글 서술 "token: 서명방식…"=단어≠값 오탐 제외). 박제 feedback scan_kv 오탐.
    ("scan_kv",       re.compile(r"(?i)(?:api[_-]?key|token|secret|password|passwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-+/=.]{4,}")),
    # 긴 base64-ish secret.
    ("scan_b64",      re.compile(r"\b[A-Za-z0-9+/=_-]{32,}\b")),
    # 한국 주소/이름 잔존 검증(redactor와 동일 shape 공유 — 신규 PII 타입은 형태 자체가 판정근거).
    ("scan_kr_address", _KR_ADDRESS),
    ("scan_kr_dongho",  _KR_DONGHO),
    ("scan_kr_name",    _KR_NAME),
]


def scan_residual_pii(text):
    """산출물 텍스트에서 PII/secret 형태 잔존 탐지. returns kind list (raw 값 미반환)."""
    if not isinstance(text, str):
        return []
    found = []
    for kind, pat in _SCAN_SHAPES:
        if pat.search(text):
            found.append(kind)
    return found


# ---------- source 어댑터 ----------
def adapt_git_diff(diff_text, source_id):
    """git_diff: MVP1 재사용(secret) + PII 복합판단 2차 + 독립 scanner 잔존검증."""
    events = mvp1.capture(diff_text, "m1::" + source_id)
    chunks, _ = mvp1.to_evidence(events)
    out, stops = [], []
    for c in chunks:
        t2, _, review = batch_redact(c["text"])
        residual = scan_residual_pii(t2)   # 독립 scanner (redactor와 별도 로직)
        if residual:
            stops.append({"item": c["item_id"], "reason": "secret/PII residual", "kinds": residual})
            continue
        c["text"] = t2
        c.setdefault("evidence_meta", {})
        c["evidence_meta"]["review_flag"] = review
        out.append(c)
    return out, stops


def adapt_text(raw_text, source_id, kind):
    """transcript/md: 문단 분할 → evidence_chunk 정규화 + redaction(복합) + 독립 scanner."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw_text) if p.strip()]
    out, stops = [], []
    for i, p in enumerate(paras):
        red, hits, review = batch_redact(p)
        if not mvp2._meaningful(red):
            continue
        residual = scan_residual_pii(red)   # 독립 scanner
        if residual:
            stops.append({"item": source_id + "#" + str(i), "reason": "secret/PII residual", "kinds": residual})
            continue
        item_id = "EVC-" + _sha8(red)   # 내용 기반 → cross-source 동일 문단 dedup
        out.append({
            "item_id": item_id, "text": red, "source": source_id,
            "evidence_meta": {
                "confidence": 0.5, "source_kind": kind,
                "timestamp": "(deterministic-m1)", "scope": SCOPE,
                "raw_pointer": source_id, "redaction_applied": True, "redaction_hits": hits,
                "review_flag": review,
            },
        })
    return out, stops


def _resolve_path(manifest_path, p):
    pp = Path(p)
    return pp if pp.is_absolute() else (Path(manifest_path).parent / p)


def process_batch(manifest_path):
    """batch manifest → per-source + 통합 nodes/edges + pack + review + consumer summary."""
    store_before = m0._store_snapshot()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    batch_id = manifest.get("batch_id", "m1_" + _sha8(str(manifest_path)))
    run_dir = TMP_OUT / batch_id
    (run_dir / "per_source").mkdir(parents=True, exist_ok=True)

    per_source, all_chunks, seen_items = [], [], set()
    residual_stop = False

    for src in manifest.get("sources", []):
        sid = src.get("source_id", "src_" + _sha8(str(src)))
        stype = src.get("source_type")
        rec = {"source_id": sid, "source_type": stype, "n_chunks": 0, "n_fresh": 0, "status": "ok", "stops": []}

        if src.get("redaction_required") is False:
            rec["status"] = "stop_redaction_required_false"; residual_stop = True; per_source.append(rec); break
        us = src.get("user_scope", "")
        if us and us != SCOPE:
            rec["status"] = "dropped_scope"; per_source.append(rec); continue
        if stype not in SOURCE_TYPES:
            rec["status"] = "failed_unknown_type"; per_source.append(rec); continue
        path = _resolve_path(manifest_path, src.get("path", ""))
        if not path.exists():
            rec["status"] = "skipped_dangling"; per_source.append(rec); continue

        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            if stype == "git_diff":
                chunks, stops = adapt_git_diff(text, sid)
            else:  # transcript | md
                chunks, stops = adapt_text(text, sid, stype)
        except Exception as e:
            rec["status"] = "failed_convert"; rec["stops"] = [repr(e)[:80]]; per_source.append(rec); continue

        if stops:  # redaction 잔존 → 전체 STOP
            rec["status"] = "stop_residual"; rec["stops"] = stops; residual_stop = True
            per_source.append(rec); break

        fresh = [c for c in chunks if c["item_id"] not in seen_items]
        for c in fresh:
            seen_items.add(c["item_id"])
        rec["n_chunks"] = len(chunks); rec["n_fresh"] = len(fresh)
        per_source.append(rec)
        all_chunks.extend(fresh)

    if residual_stop:
        report = {"batch_id": batch_id, "gate": "STOP", "reason": "secret/PII residual 또는 redaction_required=false",
                  "per_source": per_source, "operating_store_unchanged": (store_before == m0._store_snapshot())}
        (run_dir / "batch_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return report, run_dir

    # 통합 nodes/edges (dedup 된 all_chunks)
    nodes, ev_index, _ = mvp2.to_nodes(all_chunks)
    fresh_map = {c["item_id"]: c["evidence_meta"]["timestamp"] for c in all_chunks}
    edges, edge_stops = edgemod.build_edges(nodes, ev_index, fresh_map)   # fan-out cap batch 통합 후 재검사

    # combined pack candidate write (temp)
    pack_dir = run_dir / "batch_pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest_pack = {
        "format_version": "opencrab-pack-v1", "pack_id": "batch_" + batch_id,
        "pack_type": "candidate", "scope": SCOPE, "depends_on": [],
        "evidence_policy": {"source": "watcher", "min_evidence": 0},
        "merge_policy": {"mode": "review", "target": "staging", "cross_pack": "isolated"},
        "promotion_allowed_default": False, "status": "staged", "cross_pack_tags": [],
        "risk_level": "low", "created_from": "watcher_batch_m1", "blocked_by_v09": True,
        "counts": {"nodes": len(nodes), "edges": len(edges), "evidence": len(all_chunks)},
        "files": ["manifest.json", "nodes.jsonl", "edges.jsonl", "evidence_index.jsonl", "evidence_chunk.jsonl"],
    }
    (pack_dir / "manifest.json").write_text(json.dumps(manifest_pack, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _wj = lambda p, rows: p.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    _wj(pack_dir / "nodes.jsonl", nodes)
    _wj(pack_dir / "edges.jsonl", edges)
    _wj(pack_dir / "evidence_index.jsonl", ev_index)
    _wj(pack_dir / "evidence_chunk.jsonl", all_chunks)

    # consumer view summary (재사용)
    view, _ = consumer.run_on_pack(pack_dir)
    # review queue candidate (review_e2e 어댑터 재사용, resolve() 순수함수)
    plan, _, _ = reviewe2e.pack_to_staging_plan(pack_dir, "batch_" + batch_id)
    bres = reviewe2e.bridgemod.bridge(plan)
    preview = bres["v08_review_workflow_preview"]["items"]
    decisions = reviewe2e._synthetic_decisions(preview)
    audit, buckets, rdecision, _ = reviewe2e.resolvermod.resolve(preview, decisions)

    store_after = m0._store_snapshot()
    # 독립 scanner를 산출물 전 경로(node 원본·evidence·edge·ev_index 직렬화)에 적용
    scan_targets = []
    scan_targets.extend(c["text"] for c in all_chunks)
    scan_targets.extend(n["properties"]["sentence"] for n in nodes)
    scan_targets.append(json.dumps(nodes, ensure_ascii=False))
    scan_targets.append(json.dumps(edges, ensure_ascii=False))
    scan_targets.append(json.dumps(ev_index, ensure_ascii=False))
    scan_targets.append(json.dumps(all_chunks, ensure_ascii=False))
    residual_kinds = sorted({k for t in scan_targets for k in scan_residual_pii(t)})
    secret_anywhere = bool(residual_kinds)
    review_flagged = sum(1 for c in all_chunks if c.get("evidence_meta", {}).get("review_flag"))

    report = {
        "batch_id": batch_id, "run_dir": str(run_dir),
        "per_source": per_source,
        "combined": {"n_nodes": len(nodes), "n_edges": len(edges), "n_evidence": len(all_chunks),
                     "n_edge_stops": len(edge_stops)},
        "consumer_view_summary": {"evidence_basis": view["evidence_basis"], "counts": view["counts"]},
        "review_queue_candidate": {"queue": len(bres["review_queue"]),
                                   "buckets": buckets, "resolver_decision": rdecision},
        "candidate_all_true": all(n["properties"]["candidate"] is True for n in nodes),
        "promotion_all_false": (all(n["promotion_allowed"] is False for n in nodes)
                                and all(e["promotion_allowed"] is False for e in edges)),
        "node_to_node_edges": sum(1 for e in edges if not (e["source"].startswith("EVC-") and e["target"].startswith("node:"))),
        "any_secret_or_pii_residual": secret_anywhere,
        "residual_scanner_kinds": residual_kinds,
        "review_flagged_chunks": review_flagged,
        "edge_fanout_ok": len(edge_stops) == 0,
        "operating_store_unchanged": (store_before == store_after),
        "production_write": 0, "store_write": 0, "db_write": 0, "opencrab_call": 0,
        "github_push": 0, "apply": 0, "hook_daemon": 0,
    }
    checks = {
        "no_secret_or_pii_residual": not report["any_secret_or_pii_residual"],
        "candidate_all_true": report["candidate_all_true"],
        "promotion_all_false": report["promotion_all_false"],
        "no_node_to_node": report["node_to_node_edges"] == 0,
        "edge_fanout_ok": report["edge_fanout_ok"],
        "operating_store_unchanged": report["operating_store_unchanged"],
    }
    report["per_run_checks"] = checks
    report["gate"] = "GO" if all(checks.values()) else "STOP"
    (run_dir / "batch_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return report, run_dir


# ---------- fixture 자동 생성 (idempotent, 결정적) ----------
def ensure_fixtures():
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    def wr(rel, content):
        p = FIXTURE_DIR / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    diff_normal = ("diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n"
                   "@@ -1 +1,2 @@\n+    return 2\n+    # added clarifying comment\n")
    transcript_normal = "사용자가 M1 배치 설계를 검토했다.\n\n다중 source 입력을 evidence 로 정규화하기로 결정했다.\n"
    md_normal = "# Handoff\n\n노드와 엣지를 review queue 로 넘기는 흐름을 닫았다.\n\ncandidate 는 confirmed 로 격상하지 않는다.\n"
    # 1) normal_3source
    wr("normal_3source/a.diff", diff_normal)
    wr("normal_3source/b.transcript.txt", transcript_normal)
    wr("normal_3source/c.handoff.md", md_normal)
    wr("normal_3source/batch.json", json.dumps({"batch_id": "normal_3source", "sources": [
        {"source_id": "n_diff", "source_type": "git_diff", "path": "a.diff", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
        {"source_id": "n_tr", "source_type": "transcript", "path": "b.transcript.txt", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
        {"source_id": "n_md", "source_type": "md", "path": "c.handoff.md", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
    ]}, ensure_ascii=False, indent=2))
    # 2) secret_source (secret + PII → 마스킹, 잔존 0 기대)
    wr("secret_source/s.txt", "토큰 " + "AKIA" + "IOSFODNN7EXAMPLE 노출.\n\n담당자 연락처 010-" + "1234-5678 이메일 hong@example.com 주민 " + "901010" + "-1234567.\n")
    wr("secret_source/batch.json", json.dumps({"batch_id": "secret_source", "sources": [
        {"source_id": "s1", "source_type": "transcript", "path": "s.txt", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
    ]}, ensure_ascii=False, indent=2))
    # 3) empty_source
    wr("empty_source/e.diff", "")
    wr("empty_source/batch.json", json.dumps({"batch_id": "empty_source", "sources": [
        {"source_id": "e1", "source_type": "git_diff", "path": "e.diff", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
    ]}, ensure_ascii=False, indent=2))
    # 4) duplicate_source (동일 내용 2 md → dedup)
    wr("duplicate_source/d1.md", md_normal)
    wr("duplicate_source/d2.md", md_normal)
    wr("duplicate_source/batch.json", json.dumps({"batch_id": "duplicate_source", "sources": [
        {"source_id": "dup_a", "source_type": "md", "path": "d1.md", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
        {"source_id": "dup_b", "source_type": "md", "path": "d2.md", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
    ]}, ensure_ascii=False, indent=2))
    # 5) dangling_path
    wr("dangling_path/batch.json", json.dumps({"batch_id": "dangling_path", "sources": [
        {"source_id": "dg", "source_type": "md", "path": "no_such_file.md", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
    ]}, ensure_ascii=False, indent=2))
    # 6) pii_boundary — positive(PII 변형, 한글 인접) 마스킹 + negative(도메인 식별자) 보존
    wr("pii_boundary/positive.txt",
       "담당자 주민" + "901010" + "-1234567 확인 바람.\n\n"
       "전화010 1234 5678 또는 국제 +82-10-9876-5432 로 연락.\n\n"
       "무하이픈 " + "010" + "55556666 와 이메일 hong@test.co.kr 도 본문에 노출됨.\n\n"
       "거주지 주소는 서울시 강남구 테헤란로 123 456동 789호 이고 이름은 홍길동 이라고 적혀 있다.\n")
    wr("pii_boundary/negative.txt",
       "이 건의 공고번호 20240101234 를 참조하라.\n\n"
       "사업자등록번호 123-45-67890 으로 등록된 업체가 낙찰했다.\n\n"
       "입찰번호 R26BK01498882 와 계약번호 C2024-0001 은 그대로 보존되어야 한다.\n")
    wr("pii_boundary/batch.json", json.dumps({"batch_id": "pii_boundary", "sources": [
        {"source_id": "pos", "source_type": "transcript", "path": "positive.txt", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
        {"source_id": "neg", "source_type": "transcript", "path": "negative.txt", "visibility": "private", "user_scope": SCOPE, "redaction_required": True},
    ]}, ensure_ascii=False, indent=2))


def _fanout_inline_check():
    """fanout_exceed: batch 통합 구조상 1:1이라 자연발생 X → edge producer build_edges 직접 합성으로 cap STOP 확인."""
    nodes = [edgemod.mvp2.to_nodes.__self__ if False else None]  # noop
    # 합성: 동일 ev_id 를 9 node 가 참조 → fan-out 9 > 8 cap
    synth_nodes = []
    for i in range(9):
        synth_nodes.append({"id": "node:STAGING:wch:fan%02d" % i, "properties": {"domain": "STAGING_UNASSIGNED", "sentence": "fanout %d" % i},
                            "label": "fanout %d" % i, "evidence_refs": ["EVC-fanone"]})
    ev_index = [{"evidence_id": "EVC-fanone"}]
    _, stops = edgemod.build_edges(synth_nodes, ev_index, {"EVC-fanone": "t"})
    return len(stops) > 0  # cap 초과 STOP 발생해야 True


def _boundary_inline_check():
    """redaction 복합 판단 + 독립 scanner 단위 검증 (한글 인접 / 도메인 보존 / denylist / 독립성 / 애매값)."""
    res = {}
    # positive: 한글 인접 PII 변형(무하이픈/공백/+82) 전부 마스킹 + scanner 잔존 0
    pos = "주민" + "901010" + "-1234567 전화010 1234 5678 국제+82-10-9876-5432 무하이픈" + "010" + "55556666 메일hong@test.co.kr"
    red, hits, _ = batch_redact(pos)
    res["positive_no_residual"] = (len(scan_residual_pii(red)) == 0) and (hits >= 4)
    # negative: 도메인 문맥 사업자번호 보존
    redn, _, _ = batch_redact("사업자등록번호 123-45-67890 으로 등록된 업체")
    res["negative_preserved"] = "123-45-67890" in redn
    # denylist override: 같은 형태라도 인증 문맥이면 마스킹
    redd, _, _ = batch_redact("api_key 123-45-67890 노출됨")
    res["denylist_masked"] = "123-45-67890" not in redd
    # scanner 독립성: redactor 거치지 않은 raw PII를 scanner가 잡음
    res["scanner_catches_raw"] = len(scan_residual_pii("연락처 010-" + "1234-5678 입니다")) > 0
    # 애매 숫자열(도메인 문맥 없음): raw 보존 금지 → 마스킹 + review_flag
    reda, _, reva = batch_redact("그 번호는 123-45-67890 였다")
    res["ambiguous_masked_review"] = ("123-45-67890" not in reda) and bool(reva)
    # 한국 주소(시/도+구/군+로+지번+동/호): 전부 마스킹 + scanner 잔존 0 (Fix B 실증 케이스)
    addr = "집 주소는 서울시 강남구 테헤란로 123 456동 789호"
    reda2, ha, _ = batch_redact(addr)
    res["kr_address_masked"] = (all(x not in reda2 for x in ("강남구", "테헤란로", "789호"))
                                and len(scan_residual_pii(reda2)) == 0 and ha >= 1)
    # 아파트 동/호 쌍(시/도 없이)도 마스킹
    redap, _, _ = batch_redact("아파트 101동 202호로 이사했다")
    res["kr_dongho_masked"] = "101동 202호" not in redap
    # scanner 독립성: raw 주소/동호를 scanner가 직접 탐지
    res["kr_scanner_catches_raw"] = (len(scan_residual_pii("서울시 강남구 테헤란로 123")) > 0
                                     and len(scan_residual_pii("101동 202호")) > 0)
    # 이름(강한 라벨 문맥) 마스킹 — 라벨은 남고 이름만 제거
    redn2, _, _ = batch_redact("이름은 홍길동 이고 연락 바람")
    res["kr_name_masked"] = ("홍길동" not in redn2) and ("이름" in redn2)
    # 과탐 억제: 시/도 유사어·문법 '로'·'이름값' 등 정상 문장은 원문 보존(변경 0)
    fps = ["그러므로 123번을 눌렀고 회의는 정해진 대로 진행됐다",
           "서울 도시 문제를 연구했고 경기 침체 대응을 논의했다",
           "이름값을 계산하는 함수 3개를 추가했다",
           "부산 국제시장 3곳을 둘러봤다"]
    res["kr_no_false_positive"] = all(batch_redact(t)[0] == t and not scan_residual_pii(t) for t in fps)
    return res


def run_selftest():
    ensure_fixtures()
    cases = []
    for batch_json in sorted(FIXTURE_DIR.glob("*/batch.json")):
        name = batch_json.parent.name
        r1, _ = process_batch(batch_json)
        r2, _ = process_batch(batch_json)
        # 멱등: batch_report 핵심부 동일
        idem = (json.dumps(r1.get("combined", {}), sort_keys=True) == json.dumps(r2.get("combined", {}), sort_keys=True)
                and r1.get("gate") == r2.get("gate"))
        cases.append({"name": name, "gate": r1.get("gate"), "report": r1, "idempotent": idem})

    by = {c["name"]: c for c in cases}
    fanout_ok = _fanout_inline_check()
    bnd = _boundary_inline_check()

    def comb(c): return c["report"].get("combined", {})
    checks = {
        "normal_3source_GO": "normal_3source" in by and by["normal_3source"]["gate"] == "GO"
                             and comb(by["normal_3source"]).get("n_nodes", 0) > 0
                             and comb(by["normal_3source"]).get("n_edges", 0) > 0,
        "secret_source_no_residual": "secret_source" in by
            and not by["secret_source"]["report"].get("any_secret_or_pii_residual", True)
            and by["secret_source"]["gate"] == "GO",
        "empty_source_continues": "empty_source" in by and by["empty_source"]["gate"] == "GO"
                                  and comb(by["empty_source"]).get("n_nodes", -1) == 0,
        "duplicate_dedup": "duplicate_source" in by
            and comb(by["duplicate_source"]).get("n_nodes", 0) == comb(by["normal_3source"]).get("n_md_unit", comb(by["duplicate_source"]).get("n_nodes", 0)),
        "dangling_skipped": "dangling_path" in by and by["dangling_path"]["gate"] == "GO"
            and any(s["status"] == "skipped_dangling" for s in by["dangling_path"]["report"]["per_source"]),
        "fanout_cap_stops": fanout_ok,
        "all_idempotent": all(c["idempotent"] for c in cases),
        "candidate_all_true": all(c["report"].get("candidate_all_true", True) for c in cases),
        "promotion_all_false": all(c["report"].get("promotion_all_false", True) for c in cases),
        "no_node_to_node": all(c["report"].get("node_to_node_edges", 0) == 0 for c in cases),
        "no_secret_pii_residual_all": all(not c["report"].get("any_secret_or_pii_residual", False) for c in cases),
        "operating_store_unchanged": all(c["report"].get("operating_store_unchanged", True) for c in cases),
        "boundary_positive_no_residual": bnd["positive_no_residual"],
        "boundary_negative_preserved": bnd["negative_preserved"],
        "boundary_denylist_masked": bnd["denylist_masked"],
        "boundary_scanner_catches_raw": bnd["scanner_catches_raw"],
        "boundary_ambiguous_masked_review": bnd["ambiguous_masked_review"],
        "boundary_kr_address_masked": bnd["kr_address_masked"],
        "boundary_kr_dongho_masked": bnd["kr_dongho_masked"],
        "boundary_kr_scanner_catches_raw": bnd["kr_scanner_catches_raw"],
        "boundary_kr_name_masked": bnd["kr_name_masked"],
        "boundary_kr_no_false_positive": bnd["kr_no_false_positive"],
        "pii_boundary_fixture_GO": "pii_boundary" in by and by["pii_boundary"]["gate"] == "GO",
    }
    # duplicate dedup 정확 판정: dup 2 source 동일 내용 → md 문단 수만큼 node, 2배 안 됨
    dup_nodes = comb(by["duplicate_source"]).get("n_nodes", 0)
    single_md_nodes = sum(1 for _ in re.split(r"\n\s*\n", (FIXTURE_DIR / "duplicate_source" / "d1.md").read_text(encoding="utf-8")) if _.strip())
    # meaningful 통과 문단만 — 느슨히 dup_nodes <= single_md_nodes (2배 아님) 확인
    checks["duplicate_dedup"] = dup_nodes <= single_md_nodes and dup_nodes > 0

    gate = "GO" if all(checks.values()) else "STOP"
    report = {
        "tool": "watcher_batch_m1.py", "phase": "M1 수동 다중 source batch", "mode": "dry-run / selftest",
        "blocked_by_v09": True, "operating_store_write": 0, "production_write": 0, "opencrab_call": 0,
        "db_write": 0, "github_push": 0, "hook_daemon": 0, "checks": checks, "gate": gate,
        "cases": [{"name": c["name"], "gate": c["gate"], "combined": comb(c), "idempotent": c["idempotent"],
                   "per_source": c["report"].get("per_source", [])} for c in cases],
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 76)
    print("OpenBinggu Watcher M1 — 수동 다중 source batch (dry-run / selftest)")
    print("=" * 76)
    for c in cases:
        cb = comb(c)
        print("  [%s] gate=%s nodes=%s edges=%s evidence=%s idem=%s"
              % (c["name"], c["gate"], cb.get("n_nodes"), cb.get("n_edges"), cb.get("n_evidence"), c["idempotent"]))
        for s in c["report"].get("per_source", []):
            print("        - %s/%s: %s (chunks=%d fresh=%d)" % (s["source_id"], s["source_type"], s["status"], s.get("n_chunks", 0), s.get("n_fresh", 0)))
    print("\n  checks:")
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  temp:", TMP_OUT, "\n  report:", SELFTEST_REPORT)
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def run_single(path):
    report, run_dir = process_batch(path)
    print(json.dumps({"batch_id": report.get("batch_id"), "gate": report.get("gate"),
                      "combined": report.get("combined"), "per_source": report.get("per_source"),
                      "run_dir": str(run_dir)}, ensure_ascii=False, indent=2))
    sys.exit(0 if report.get("gate") == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
