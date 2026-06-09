# -*- coding: utf-8 -*-
"""OpenBinggu v0.11 — incoming → staging loader (dry-run only).

흐름: incoming pack 읽기 → v0.10 contract validator 선행 통과 → secret 검사
      → risk/cross-pack 정책 적용 → staging plan dry-run report 만 생성.
      **실제 graph write 없음. apply 단계 아님. production graph 생성 아님.**

전제/금지 (BLOCKED_BY_V09 유지):
  production write / 운영 store(_graph_merge.yaml·user_graph.yaml·localcrab_index.sqlite) write /
  localbinggu_production_graph.yaml 생성 / OpenCrab 호출 / GitHub push / reingest_pack_draft 원본 수정 /
  scheduler 수정 / promotion_allowed 변경 / D9 상태 변경 / coverage·pattern 승격 — 전부 금지.
  유일한 write = reports/*.json (staging plan dry-run + selftest). production graph 아님.

verdict (v0.11):
  STOP             — contract STOP(필드누락·promotion≠false·production target 등) 또는 secret-like content.
  REVIEW_REQUIRED  — risk_level ∈ {high, unknown}. 사람이 반드시 검토.
  REVIEW_ONLY      — cross-pack fuzzy (contract REVIEW_ONLY). 검토 권장.
  SAFE_STAGING     — valid low/medium risk, secret 없음 → staging 후보로 정규화.

우선순위: STOP > REVIEW_REQUIRED(risk) > REVIEW_ONLY(fuzzy) > SAFE_STAGING.

CLI:
  python openbinggu_incoming_to_staging.py --selftest      # fixtures 전수 + selftest/plan report
  python openbinggu_incoming_to_staging.py <incoming.json> # 단일 dry-run
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "openbinggu_v011_incoming"
SELFTEST_REPORT = BASE / "reports" / "openbinggu_v011_selftest.json"
STAGING_PLAN_REPORT = BASE / "reports" / "openbinggu_v011_staging_plan.json"

# v0.10 contract validator 재사용 (선행 gate)
sys.path.insert(0, str(SCRIPTS))
import openbinggu_pack_validate as v010  # noqa: E402

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
                snippet = m.group(0)
                masked = snippet[:8] + "…(masked, len=%d)" % len(snippet)
                hits.append({"item_id": it.get("item_id", "?"), "pattern": pat.pattern[:40], "masked": masked})
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


def run_selftest():
    if not FIXTURE_DIR.is_dir():
        print(f"[FAIL] fixture 디렉토리 없음: {FIXTURE_DIR}")
        sys.exit(1)
    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    cases, staging_plan = [], []
    n_match = n_mismatch = 0
    agg = {"production_write": 0, "operating_store_write": 0, "opencrab_call": 0, "github_push": 0}

    for fp in fixtures:
        expected = _expected_from_name(fp.name)
        try:
            incoming = json.loads(fp.read_text(encoding="utf-8"))
            res = assess_incoming(incoming)
        except Exception as e:
            res = {"verdict": "ERROR", "contract_verdict": "N/A", "reasons": [repr(e)],
                   "counters": agg}
        ok = (res["verdict"] == expected)
        n_match += ok
        n_mismatch += (not ok)
        for k in agg:
            agg[k] += res.get("counters", {}).get(k, 0)
        cases.append({
            "fixture": fp.name, "expected": expected, "actual": res["verdict"],
            "match": ok, "contract_verdict": res.get("contract_verdict"),
            "reasons": res["reasons"],
        })
        if res["verdict"] == "SAFE_STAGING" and "normalized_pack" in res:
            staging_plan.append(res["normalized_pack"])

    all_match = (n_mismatch == 0 and n_match > 0)
    selftest = {
        "loader": "openbinggu_incoming_to_staging.py", "version": "v0.11",
        "mode": "dry-run / selftest", "blocked_by_v09": True,
        "fixture_dir": str(FIXTURE_DIR), "n_cases": len(cases),
        "n_match": n_match, "n_mismatch": n_mismatch,
        "counters_total": agg,
        "gate": "GO" if (all_match and agg == {"production_write": 0, "operating_store_write": 0,
                                               "opencrab_call": 0, "github_push": 0}) else "STOP",
        "cases": cases,
    }
    plan = {
        "loader": "openbinggu_incoming_to_staging.py", "version": "v0.11",
        "mode": "staging plan dry-run (NO graph write)", "blocked_by_v09": True,
        "production_graph_written": False,
        "n_safe_staging": len(staging_plan),
        "staging_candidates": staging_plan,
        "counters": agg,
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(selftest, ensure_ascii=False, indent=2), encoding="utf-8")
    STAGING_PLAN_REPORT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 70)
    print("OpenBinggu v0.11 incoming→staging loader  (dry-run / selftest)")
    print("=" * 70)
    for c in cases:
        flag = "[PASS]" if c["match"] else "[FAIL]"
        print(f"  {flag} {c['fixture']:40s} expected={c['expected']:15s} actual={c['actual']}")
        if not c["match"]:
            for r in c["reasons"]:
                print("         !", r)
    print(f"\n  cases={len(cases)} match={n_match} mismatch={n_mismatch}")
    print(f"  counters(전부 0 의무): {agg}")
    print(f"  staging candidates(SAFE_STAGING): {len(staging_plan)}")
    print(f"  selftest → {SELFTEST_REPORT}")
    print(f"  plan     → {STAGING_PLAN_REPORT}")
    print(f"\n  GATE: {selftest['gate']}  (GO = 전 fixture 일치 + write counter 전부 0)")
    sys.exit(0 if selftest["gate"] == "GO" else 1)


def run_single(path):
    incoming = json.loads(Path(path).read_text(encoding="utf-8"))
    res = assess_incoming(incoming)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["verdict"] in {"SAFE_STAGING", "REVIEW_REQUIRED", "REVIEW_ONLY"} else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
