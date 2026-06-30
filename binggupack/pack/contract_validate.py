# -*- coding: utf-8 -*-
"""OpenBinggu pack contract validator — 정본 순수 로직(canonical pure logic).

v1.16 strangler Phase2: pack 계약 validator 의 enum 상수 + validate_pack/_expected_from_name
정본 transform 로직을 scripts/openbinggu_pack_validate.py 에서 binggupack.pack 패키지로 이관.
scripts/openbinggu_pack_validate.py 는 이 모듈을 re-export 하는 backward-compatible thin
wrapper 다(기존 bare-name import 호환). **계약/transform 정본은 이 모듈이다.**

read-only: pack dict 를 읽어 verdict(PASS/REVIEW_ONLY/STOP) 만 판정한다. production write 0·
graph 생성 0·LLM 0·멱등. __file__ 경로상수(BASE/FIXTURE_DIR/REPORT_PATH)·run_selftest·CLI·
__main__ 은 scripts wrapper 에 잔류한다(scripts sys.path / report write 의존).

verdict: PASS / REVIEW_ONLY / STOP
  - STOP : 안전 위반(하나라도). 자동 merge·승격 금지.
  - REVIEW_ONLY : 형식은 통과하나 cross-pack fuzzy 등 사람이 봐야 하는 경우.
  - PASS : 최소 계약 충족(그래도 production write 는 v0.9 정책으로 별도 차단).
"""

REQUIRED_FIELDS = [
    "pack_id", "pack_type", "scope", "depends_on", "evidence_policy",
    "merge_policy", "promotion_allowed_default", "status",
    "cross_pack_tags", "risk_level", "created_from",
]
PACK_TYPE_ALLOWED = {
    "seed", "session", "evidence", "candidate", "review", "audit", "runtime", "synthetic_fixture",
}
STATUS_ALLOWED = {"draft", "staged", "validated", "review_required", "archived", "rejected"}
RISK_ALLOWED = {"low", "medium", "high", "unknown"}
MERGE_MODE_ALLOWED = {"manual", "auto", "review"}
MERGE_TARGET_ALLOWED = {"staging", "candidate", "review", "production"}
CROSS_PACK_ALLOWED = {"isolated", "review_only", "fuzzy"}

# pack_type 중 production target 으로 직접 가면 안 되는 타입 (rule 4)
NON_PRODUCTION_PACK_TYPES = {"session", "candidate", "evidence"}
# hard-default 플래그 — 존재하면 반드시 false (rule: hard_defaults)
HARD_FALSE_FLAGS = ["production_write_allowed", "opencrab_ingest_allowed", "github_publish_allowed"]


def validate_pack(pack):
    """단일 pack dict → {verdict, stops:[...], reviews:[...], notes:[...]} (read-only)."""
    stops, reviews, notes = [], [], []

    if not isinstance(pack, dict):
        return {"verdict": "STOP", "stops": ["pack 이 object(dict) 가 아님"], "reviews": [], "notes": []}

    # rule 1: required field 누락
    missing = [f for f in REQUIRED_FIELDS if f not in pack]
    if missing:
        stops.append(f"required field 누락: {missing}")

    # enum 검증 (rule 6 — 허용값 밖이면 STOP)
    pt = pack.get("pack_type")
    if pt is not None and pt not in PACK_TYPE_ALLOWED:
        stops.append(f"pack_type 비허용값: {pt!r}")
    st = pack.get("status")
    if st is not None and st not in STATUS_ALLOWED:
        stops.append(f"status 비허용값: {st!r}")
    rl = pack.get("risk_level")
    if rl is not None and rl not in RISK_ALLOWED:
        stops.append(f"risk_level 비허용값: {rl!r}")

    mp = pack.get("merge_policy")
    mode = target = cross = None
    if not isinstance(mp, dict):
        if "merge_policy" not in missing:
            stops.append("merge_policy 가 object 아님")
    else:
        mode, target, cross = mp.get("mode"), mp.get("target"), mp.get("cross_pack")
        if mode not in MERGE_MODE_ALLOWED:
            stops.append(f"merge_policy.mode 비허용값: {mode!r}")
        if target not in MERGE_TARGET_ALLOWED:
            stops.append(f"merge_policy.target 비허용값: {target!r}")
        if cross not in CROSS_PACK_ALLOWED:
            stops.append(f"merge_policy.cross_pack 비허용값: {cross!r}")

    # rule 2: promotion_allowed_default 는 반드시 false
    pad = pack.get("promotion_allowed_default")
    if "promotion_allowed_default" not in missing and pad is not False:
        stops.append(f"promotion_allowed_default 는 false 강제(현재 {pad!r})")

    # hard_defaults: production_write/opencrab_ingest/github_publish 는 true 면 STOP
    for flag in HARD_FALSE_FLAGS:
        if pack.get(flag) is True:
            stops.append(f"hard-default 위반: {flag}=true (BLOCKED_BY_V09)")

    # rule 7: 새 주제를 9도메인에 강제배정하는 forced_domain 금지
    if "forced_domain" in pack:
        stops.append("forced_domain 금지(9도메인 강제배정) — scope/cross_pack_tags 로만 표현")

    # rule 3: risk_level high|unknown 이면 자동 merge 금지
    if rl in {"high", "unknown"} and mode == "auto":
        stops.append(f"risk_level={rl} + merge_policy.mode=auto → 자동 merge 금지")

    # rule 4: session|candidate|evidence pack 은 production target 금지
    if pt in NON_PRODUCTION_PACK_TYPES and target == "production":
        stops.append(f"pack_type={pt} 는 production target 금지(merge_policy.target=production)")

    # rule 5: depends_on 은 형식(존재)만 확인, 의미론적 merge 안 함
    dep = pack.get("depends_on")
    if "depends_on" not in missing:
        if not isinstance(dep, list) or any(not isinstance(d, str) for d in dep):
            stops.append("depends_on 은 string 배열이어야 함")
        elif dep:
            notes.append(f"depends_on={dep} — 존재 형식만 확인(의미론적 merge 미실행)")

    # rule 6 / cross-pack: cross_pack_tags 가 있고 fuzzy merge 면 review-only
    cpt = pack.get("cross_pack_tags")
    if isinstance(cpt, list) and cpt and cross == "fuzzy":
        reviews.append(f"cross_pack_tags={cpt} + cross_pack=fuzzy → cross-pack fuzzy merge 는 REVIEW_ONLY")

    if stops:
        verdict = "STOP"
    elif reviews:
        verdict = "REVIEW_ONLY"
    else:
        verdict = "PASS"
    return {"verdict": verdict, "stops": stops, "reviews": reviews, "notes": notes}


def _expected_from_name(name):
    """fixture 파일명 prefix → 기대 verdict."""
    low = name.lower()
    if low.startswith("pass_"):
        return "PASS"
    if low.startswith("stop_"):
        return "STOP"
    if low.startswith("review_"):
        return "REVIEW_ONLY"
    return None
