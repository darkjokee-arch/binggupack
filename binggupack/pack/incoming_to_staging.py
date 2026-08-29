# -*- coding: utf-8 -*-
"""OpenBinggu v0.11 — incoming → staging assess (정본 impl · dry-run only).

v1.16 strangler Phase2: 정본 transform 을 scripts/openbinggu_incoming_to_staging.py 에서 이관.
scripts/openbinggu_incoming_to_staging.py 는 이 모듈을 re-export 하는 backward-compatible
thin wrapper(__file__ 경로상수 + run_selftest/run_single/CLI 오케스트레이션 잔류)다.

흐름: incoming pack 읽기 → v0.10 contract validator 선행 통과 → secret 검사 → risk/cross-pack
      정책 적용 → SAFE_STAGING/REVIEW_*/STOP 판정. 실제 graph write 없음(dry-run).
v0.10 contract validator(validate_pack)는 binggupack.pack.contract_validate 정본을 재사용한다.
"""
import json
import re
import tempfile
from pathlib import Path

# v0.10 contract validator 재사용 (선행 gate) — 정본 패키지 모듈.
from binggupack.pack import contract_validate as v010

__all__ = [
    "json", "re", "tempfile", "Path", "v010", "SECRET_PATTERNS", "scan_secrets",
    "assess_incoming", "_expected_from_name", "_base_pack", "_content", "synthesize_fixtures",
]

# --- secret-like content 검사 (박제: 단어≠값, 값 패턴 기반 과탐 회피) ---
# key=value 형태에서 value 가 실제 시크릿처럼 긴 경우만 STOP. 단어 자체("key","token")는 무죄.
SECRET_PATTERNS = [
    # TF-A: vendor 토큰(prefix 기반) — sk-live-/sk-proj-/ghp_/github_pat_/xox[baprs]-
    re.compile(r"(?i)\b(?:sk-live-|sk-proj-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_\-]{6,}"),
    # TF-A: AWS access key id
    re.compile(r"\bAKIA[0-9A-Z]{8,}"),
    # TF-A: credential 키워드 + 값(짧은 값 포함, 16자 미만도 감지) — password/secret/token/api_key 키워드 등
    re.compile(r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|client[_-]?secret|"
               r"access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*['\"]?\S{3,}"),
    # TF-A: private key 키워드
    re.compile(r"(?i)\bprivate[_-]?key\b"),
    re.compile(r"(?i)\b(service_?key|api_?key|secret_?key|client_?secret|access_?token|"
               r"refresh_?token|password|passwd|cookie|authorization)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-+/=.]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.=]{20,}"),
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(aws_secret_access_key|aws_access_key_id)\b\s*[:=]\s*[A-Za-z0-9/+=]{16,}"),
    # 단독 긴 토큰형(공백 없는 40+ 영숫자 혼합, 16진/base64 시크릿 추정) — 보수적 STOP
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9])"),
]


def scan_secrets(content):
    hits = []
    if not isinstance(content, dict):
        return hits
    for it in content.get("items", []) or []:
        text = it.get("text", "") if isinstance(it, dict) else ""
        if not isinstance(text, str):
            continue
        for pat in SECRET_PATTERNS:
            m = pat.search(text)
            if m:
                hits.append({"item_id": it.get("item_id", "?"), "pattern": pat.pattern[:40],
                             "masked": "[redacted]"})
                break
    return hits


def assess_incoming(incoming):
    """incoming dict → {verdict, contract_verdict, reasons, normalized_pack?, counters}."""
    counters = {"production_write": 0, "operating_store_write": 0, "opencrab_call": 0, "github_push": 0}
    reasons = []

    if not isinstance(incoming, dict):
        return {"verdict": "STOP", "contract_verdict": "N/A",
                "reasons": ["incoming 이 object 아님"], "counters": counters}

    pack = incoming.get("pack")
    content = incoming.get("content")

    # 1. v0.10 contract gate 선행
    cres = v010.validate_pack(pack if isinstance(pack, dict) else {})
    contract_verdict = cres["verdict"]
    if contract_verdict == "STOP":
        return {"verdict": "STOP", "contract_verdict": contract_verdict,
                "reasons": ["[contract] " + s for s in cres["stops"]], "counters": counters}

    # 2. secret-like content → STOP
    sec = scan_secrets(content)
    if sec:
        return {"verdict": "STOP", "contract_verdict": contract_verdict,
                "reasons": ["[secret] %s @ %s" % (h["masked"], h["item_id"]) for h in sec],
                "counters": counters}

    # 3. risk high/unknown → REVIEW_REQUIRED (cross-pack fuzzy 보다 우선)
    risk = pack.get("risk_level")
    if risk in {"high", "unknown"}:
        reasons.append("risk_level=%s → REVIEW_REQUIRED" % risk)
        return {"verdict": "REVIEW_REQUIRED", "contract_verdict": contract_verdict,
                "reasons": reasons, "counters": counters}

    # 4. cross-pack fuzzy → REVIEW_ONLY (contract 가 이미 표시)
    if contract_verdict == "REVIEW_ONLY":
        reasons.append("cross-pack fuzzy → REVIEW_ONLY")
        reasons += ["[contract] " + r for r in cres["reviews"]]
        return {"verdict": "REVIEW_ONLY", "contract_verdict": contract_verdict,
                "reasons": reasons, "counters": counters}

    # 5. SAFE_STAGING — staging 후보로 정규화(dry-run, write 없음)
    normalized = {
        "pack_id": pack.get("pack_id"),
        "pack_type": pack.get("pack_type"),
        "scope": pack.get("scope"),
        "risk_level": risk,
        "staging_status": "staged",            # 후보 표시(정규화), 실제 store write 아님
        "promotion_allowed_default": False,    # 강제 false 재확인
        "staging_target": "staging",
        "n_content_items": len(content.get("items", [])) if isinstance(content, dict) else 0,
        "source_incoming_id": incoming.get("incoming_id"),
    }
    reasons.append("valid %s-risk, secret 없음 → SAFE_STAGING" % risk)
    return {"verdict": "SAFE_STAGING", "contract_verdict": contract_verdict,
            "reasons": reasons, "normalized_pack": normalized, "counters": counters}


def _expected_from_name(name):
    low = name.lower()
    if low.startswith("safe_"):
        return "SAFE_STAGING"
    if low.startswith("reviewreq_"):
        return "REVIEW_REQUIRED"
    if low.startswith("reviewonly_"):
        return "REVIEW_ONLY"
    if low.startswith("stop_"):
        return "STOP"
    return None


def _base_pack(pack_id, risk="low", cross_pack="isolated", tags=None):
    return {
        "pack_id": pack_id, "pack_type": "session", "scope": "synthetic_selftest",
        "depends_on": [], "evidence_policy": "required",
        "merge_policy": {"mode": "manual", "target": "staging", "cross_pack": cross_pack},
        "promotion_allowed_default": False, "status": "draft",
        "cross_pack_tags": list(tags or []), "risk_level": risk,
        "created_from": "selftest_synthetic",
    }


def _content(texts):
    return {"items": [{"item_id": "it_%02d" % i, "text": t} for i, t in enumerate(texts, 1)]}


def synthesize_fixtures():
    """selftest fixture 를 temp 디렉토리에 합성 생성(synthetic, 개인 데이터 0, repo fixture 불필요)."""
    tmp = Path(tempfile.mkdtemp(prefix="obg_v011_fixtures_"))
    # 시크릿 키워드는 public tree scanner 자기검출 회피를 위해 런타임 조립(합성값)
    fake_secret_text = "api" + "_key = '" + ("Ab1" * 8) + "'"
    fixtures = {
        "safe_low_risk.json": {
            "incoming_id": "in_safe_low",
            "pack": _base_pack("pk_safe_low", risk="low"),
            "content": _content(["저위험 합성 메모 항목", "secondary synthetic note"]),
        },
        "safe_medium_risk.json": {
            "incoming_id": "in_safe_med",
            "pack": _base_pack("pk_safe_med", risk="medium"),
            "content": _content(["중위험 합성 메모 항목"]),
        },
        "reviewreq_high_risk.json": {
            "incoming_id": "in_rr_high",
            "pack": _base_pack("pk_rr_high", risk="high"),
            "content": _content(["고위험 합성 항목"]),
        },
        "reviewreq_unknown_risk.json": {
            "incoming_id": "in_rr_unknown",
            "pack": _base_pack("pk_rr_unknown", risk="unknown"),
            "content": _content(["위험도 미상 합성 항목"]),
        },
        "reviewonly_cross_pack_fuzzy.json": {
            "incoming_id": "in_ro_fuzzy",
            "pack": _base_pack("pk_ro_fuzzy", risk="low", cross_pack="fuzzy",
                               tags=["topic_overlap"]),
            "content": _content(["cross-pack fuzzy 합성 항목"]),
        },
        "stop_contract_missing_fields.json": {
            "incoming_id": "in_stop_missing",
            "pack": {"pack_id": "pk_stop_missing"},
            "content": _content(["계약 필드 누락 합성 항목"]),
        },
        "stop_contract_promotion_true.json": {
            "incoming_id": "in_stop_promo",
            "pack": dict(_base_pack("pk_stop_promo"), promotion_allowed_default=True),
            "content": _content(["promotion true 합성 항목"]),
        },
        "stop_secret_in_content.json": {
            "incoming_id": "in_stop_secret",
            "pack": _base_pack("pk_stop_secret"),
            "content": _content(["정상 합성 문장", fake_secret_text]),
        },
    }
    for name, obj in fixtures.items():
        (tmp / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp
