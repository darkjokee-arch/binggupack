# -*- coding: utf-8 -*-
"""BingguPack 자기진화 거버넌스 2단 — 선언형 정책 파일 read-only 평가기.

OPA "policy-as-data" 개념만 차용(외부 바이너리·rego 엔진 0). 정책=JSON 문서,
질의=순수 파이썬 함수(결정적·LLM 0). hashlib(sha256)로 bundle digest pin.

빙구팩 코드는 정책/pin 을 **read-only** 로만 읽는다(write 0). 정책 진화(3단)는
사람이 빙구팩과 무관한 독립 도구(git diff·외부 에디터)로만 binggu_policy.json +
binggu_policy.sha256 을 함께 수정한다. 본 모듈에는 정책/pin 에 대한 write 경로가 0.

[가드 5종 — 검증서 필수 fix, 코드로 강제]
  ① 화이트리스트 완전성 불변식: 필수 안전 카테고리(SAFE-DB/SECRET/DESTROY/VERIFY)를
     코드 하드코딩 상수 REQUIRED_IMMUTABLE 로 두고 _validate_shape 에서
     'REQUIRED_IMMUTABLE ⊆ immutable_whitelist' 강제(하나 빠지면 BLOCK).
  ② clause 재분류 우회 차단: 안전 카테고리(category=='안전')는 mutable clauses 로
     이동 불가 — clauses 에 '안전' category 가 보이면 schema_invalid(코드 검증).
  ③ hash pin full fail-closed: pin 부재/불일치 시 BLOCK(미봉인 통과 금지).
  ④ 빙구팩 코드는 정책/pin read-only(write 0) — open(mode 'w'/'a') 호출 0.

CLI: python scripts/binggu_policy.py --selftest
"""
import hashlib
import json
import os
from pathlib import Path

# ============================================================
# 상수 (코드 고정 · 헌법급 · 데이터로 못 바꿈)
# ============================================================
SCHEMA_VERSION = "1.0"
CATEGORIES = ("안전", "스타일", "방법론", "판단")
SAFETY_CATEGORY = "안전"

# 가드① — 필수 안전 카테고리(코드 하드코딩 단일 원천). 정책이 이 중 하나라도
# immutable_whitelist 에서 빠뜨리면 _validate_shape 가 fail-closed(BLOCK).
# 안전조항을 정책에서 지워 교체가능으로 만드는 변조를 코드가 막는다.
REQUIRED_IMMUTABLE = ("SAFE-DB", "SAFE-SECRET", "SAFE-DESTROY", "SAFE-VERIFY")

# constitution 에 반드시 존재해야 하는 헌법 플래그(7종).
_CONSTITUTION_KEYS = (
    "candidate_only", "human_approval_gate", "pii_excluded", "audit_chain",
    "ai_recommend_only", "auto_decision", "stdlib_only",
)

_POLICY_FILENAME = "binggu_policy.json"
_PIN_FILENAME = "binggu_policy.sha256"


def _repo_root():
    """이 파일(scripts/) 기준 repo root. binggu_platform.ROOT 와 동일 파생."""
    here = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
    return os.path.dirname(here)                         # <repo>


def policy_path(env=None):
    """정책 파일 경로. BINGGU_POLICY_PATH override(opt-in) → 아니면 패키지 기본."""
    env = os.environ if env is None else env
    override = env.get("BINGGU_POLICY_PATH")
    if override:
        return str(override)
    return os.path.join(_repo_root(), "policies", _POLICY_FILENAME)


def pin_path(env=None):
    """pin 파일 경로 — 정책 파일과 동일 디렉토리의 binggu_policy.sha256."""
    return os.path.join(os.path.dirname(policy_path(env)), _PIN_FILENAME)


# ============================================================
# 정규화 / digest (stdlib hashlib · 외부 바이너리 0)
# ============================================================
def _canon_bytes(obj):
    """결정적 정규화 — 키순서/공백 비의존 digest 입력.

    json.dumps(sort_keys=True, separators=(",",":")) → utf-8. 전체 hash 라 [:16] 절단 안 함.
    immutable_whitelist 등 배열 순서는 보존(list 순서 = digest 영향), dict 키 순서는 무관.
    """
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def compute_digest(doc):
    """정책 문서의 full sha256 hexdigest(64자). pin 대조 기준."""
    return hashlib.sha256(_canon_bytes(doc)).hexdigest()


# ============================================================
# 형상 검증 (가드①②③ 핵심 — fail-closed)
# ============================================================
def _validate_shape(doc):
    """필수키·카테고리·안전조항 무결성 강제. (ok, reason) 반환.

    가드① REQUIRED_IMMUTABLE ⊆ immutable_whitelist(하나 빠지면 BLOCK).
    가드② 안전 category 는 mutable clauses 로 이동 불가(clauses 에 '안전' 보이면 BLOCK).
    + immutable_whitelist 각 항목 category=='안전' / clauses category ∈ CATEGORIES /
      constitution 플래그 7종 존재 강제. 안전조항을 스타일로 위장하는 변조 차단.
    """
    if not isinstance(doc, dict):
        return False, "not_a_dict"

    # 필수 최상위 키
    for k in ("schema_version", "policy_id", "immutable_whitelist", "clauses",
              "categories", "constitution"):
        if k not in doc:
            return False, "missing_key:%s" % k

    iw = doc.get("immutable_whitelist")
    if not isinstance(iw, list) or not iw:
        return False, "immutable_whitelist_invalid"

    iw_ids = []
    for item in iw:
        if not isinstance(item, dict):
            return False, "immutable_item_not_dict"
        cid = item.get("id")
        if not cid:
            return False, "immutable_item_missing_id"
        # 모든 immutable 항목은 반드시 안전 카테고리(안전조항 위장 차단).
        if item.get("category") != SAFETY_CATEGORY:
            return False, "immutable_category_not_safety:%s" % cid
        if "rule" not in item:
            return False, "immutable_item_missing_rule:%s" % cid
        iw_ids.append(cid)

    iw_idset = set(iw_ids)
    # 가드① — 필수 안전 카테고리 완전성 불변식(코드 상수 ⊆ 정책).
    for req in REQUIRED_IMMUTABLE:
        if req not in iw_idset:
            return False, "required_immutable_missing:%s" % req

    clauses = doc.get("clauses")
    if not isinstance(clauses, list):
        return False, "clauses_invalid"
    seen_clause_ids = set()
    for c in clauses:
        if not isinstance(c, dict):
            return False, "clause_not_dict"
        cid = c.get("id")
        if not cid:
            return False, "clause_missing_id"
        cat = c.get("category")
        if cat not in CATEGORIES:
            return False, "clause_category_unknown:%s" % cid
        # 가드② — 안전 카테고리는 mutable clauses 로 이동 불가(재분류 우회 차단).
        if cat == SAFETY_CATEGORY:
            return False, "safety_clause_in_mutable_list:%s" % cid
        # clause id 가 immutable_whitelist 와 겹치면 안전조항 강등 시도로 간주(차단).
        if cid in iw_idset:
            return False, "clause_id_collides_immutable:%s" % cid
        if cid in seen_clause_ids:
            return False, "duplicate_clause_id:%s" % cid
        seen_clause_ids.add(cid)

    cats = doc.get("categories")
    if not isinstance(cats, list) or SAFETY_CATEGORY not in cats:
        return False, "categories_invalid"

    const = doc.get("constitution")
    if not isinstance(const, dict):
        return False, "constitution_invalid"
    for k in _CONSTITUTION_KEYS:
        if k not in const:
            return False, "constitution_missing_flag:%s" % k

    return True, None


# ============================================================
# 로더 (read-only · fail-closed · 예외 누수 0)
# ============================================================
def _read_text_ro(path):
    """read-only 텍스트 로드. 없거나 못 읽으면 None(예외 0)."""
    try:
        p = Path(path)
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def _read_pin(env=None, expected_digest=None):
    """기대 digest 조회 — 명시 expected_digest 우선, 없으면 pin 파일에서.

    반환: (digest|None). pin 파일은 첫 비주석 토큰(64자 hex)만 채택.
    """
    if expected_digest:
        return expected_digest.strip()
    raw = _read_text_ro(pin_path(env))
    if raw is None:
        return None
    for line in raw.splitlines():
        tok = line.strip()
        if not tok or tok.startswith("#"):
            continue
        # 'sha256:xxxx' 또는 'xxxx' 또는 'xxxx  filename' 형태 모두 첫 토큰 채택
        tok = tok.split()[0]
        if tok.lower().startswith("sha256:"):
            tok = tok.split(":", 1)[1]
        return tok.strip()
    return None


def load_policy(env=None, expected_digest=None):
    """정책 read-only 로드 + hash pin 대조.

    반환 dict: {ok, policy|None, digest, reason, fail_closed}.
    모든 실패 경로 = fail-closed(빈/None 정책 + ok=False). '통과' default 0.

    가드③ — pin 부재/불일치 시 BLOCK(미봉인 통과 금지, full fail-closed).
    """
    out = {"ok": False, "policy": None, "digest": None,
           "reason": None, "fail_closed": True}

    path = policy_path(env)
    raw = _read_text_ro(path)
    if raw is None:
        out["reason"] = "policy_missing"
        return out

    try:
        doc = json.loads(raw)
    except Exception:
        out["reason"] = "policy_corrupt"
        return out
    if not isinstance(doc, dict):
        out["reason"] = "policy_corrupt"
        return out

    if doc.get("schema_version") != SCHEMA_VERSION:
        out["reason"] = "schema_version_mismatch"
        return out

    shape_ok, shape_reason = _validate_shape(doc)
    if not shape_ok:
        out["reason"] = "schema_invalid:%s" % shape_reason
        return out

    digest = compute_digest(doc)
    out["digest"] = digest

    expected = _read_pin(env, expected_digest)
    # 가드③ — pin full fail-closed. 부재/불일치 모두 BLOCK.
    if expected is None:
        out["reason"] = "pin_absent"
        return out
    if digest != expected:
        out["reason"] = "policy_hash_mismatch"
        return out

    out["ok"] = True
    out["fail_closed"] = False
    out["policy"] = doc
    out["reason"] = None
    return out


# ============================================================
# 조회 헬퍼 (정책 dict 주입 또는 로드)
# ============================================================
def _resolve_policy(env=None, policy=None, expected_digest=None):
    """policy dict 직접 주입 시 그대로(검증된 것으로 신뢰), 아니면 load_policy.

    반환 (policy_dict|None, load_result|None).
    """
    if policy is not None:
        return policy, None
    res = load_policy(env=env, expected_digest=expected_digest)
    return (res["policy"] if res["ok"] else None), res


def _immutable_index(policy):
    return {item["id"]: item for item in policy.get("immutable_whitelist", [])
            if isinstance(item, dict) and item.get("id")}


def _clause_index(policy):
    return {c["id"]: c for c in policy.get("clauses", [])
            if isinstance(c, dict) and c.get("id")}


def classify_clause(clause_id, env=None, policy=None, expected_digest=None):
    """조항 id → {category, mutable, is_safety, found}. 미존재 → found=False.

    immutable_whitelist 매칭이 우선(안전조항). 그 다음 mutable clauses.
    """
    pol, res = _resolve_policy(env, policy, expected_digest)
    if pol is None:
        return {"category": None, "mutable": None, "is_safety": None,
                "found": False, "reason": (res or {}).get("reason", "load_failed")}
    imm = _immutable_index(pol)
    if clause_id in imm:
        return {"category": imm[clause_id].get("category", SAFETY_CATEGORY),
                "mutable": False, "is_safety": True, "found": True}
    cl = _clause_index(pol)
    if clause_id in cl:
        cat = cl[clause_id].get("category")
        return {"category": cat, "mutable": bool(cl[clause_id].get("mutable", True)),
                "is_safety": (cat == SAFETY_CATEGORY), "found": True}
    return {"category": None, "mutable": None, "is_safety": None, "found": False}


def is_immutable(clause_id, env=None, policy=None, expected_digest=None):
    """immutable_whitelist 에 있으면 True(교체불가). 평가 전용 — 변경 차단이 아니라
    '추천에서 제외' 신호. 로드 실패 시 fail-closed True(보수적·건드리지 말 것으로 간주)."""
    pol, res = _resolve_policy(env, policy, expected_digest)
    if pol is None:
        return True  # fail-closed: 모르면 교체불가로 취급(안전 우선)
    return clause_id in _immutable_index(pol)


# ============================================================
# 평가기 (OPA query(policy,input) 등가 · 결정적 · read-only · write 0)
# ============================================================
def evaluate(input_ctx, env=None, policy=None, expected_digest=None):
    """정책 평가 — candidate 추천 신호만. 어떤 decision 도 자동 적용 0(헌법).

    input_ctx = {action?, target_clause_id?, proposed_change?, work_text?}
    반환 {decision, fail_closed, digest_verified, matched_immutable[], category,
          advice, reason}

      - load 실패/hash mismatch/pin 부재 → decision='BLOCK' fail_closed=True
        (가드③ — 제안조차 안 함).
      - target 이 immutable_whitelist 매칭 → decision='RECOMMEND_BLOCK'.
      - mutable clause → decision='RECOMMEND_REVIEW'.
      - 미존재 target → decision='RECOMMEND_REVIEW' (보수적 검토 권고).
      - 항상 candidate·ai_recommend_only — 자동 적용 아님.
    """
    out = {
        "decision": "BLOCK", "fail_closed": True, "digest_verified": False,
        "matched_immutable": [], "category": None, "advice": None, "reason": None,
    }
    input_ctx = input_ctx if isinstance(input_ctx, dict) else {}

    # 정책 로드(주입 시 검증된 것으로 간주, 아니면 load_policy 게이트 통과 필요).
    if policy is not None:
        pol = policy
        out["digest_verified"] = bool(expected_digest)  # 주입은 외부 검증 책임
    else:
        res = load_policy(env=env, expected_digest=expected_digest)
        if not res["ok"]:
            out["reason"] = res["reason"]
            out["advice"] = ("정책 봉인 검증 실패 — 평가 BLOCK(fail-closed). "
                             "정책/pin 을 사람이 git 으로 함께 갱신해야 함. 빙구팩 자동변경 0.")
            return out
        pol = res["policy"]
        out["digest_verified"] = True

    out["fail_closed"] = False
    target = input_ctx.get("target_clause_id")

    if target is not None:
        cls = classify_clause(target, env=env, policy=pol)
        if cls["found"] and cls["is_safety"]:
            out["decision"] = "RECOMMEND_BLOCK"
            out["matched_immutable"] = [target]
            out["category"] = cls["category"]
            out["advice"] = ("이 조항은 교체불가(안전: DB·시크릿·파괴·검증). 변경은 "
                             "빙구팩 무관 독립 도구(git diff·외부 에디터)로 사람이. "
                             "빙구팩 자동변경 0.")
            out["reason"] = "target_immutable"
            return out
        if cls["found"]:
            out["decision"] = "RECOMMEND_REVIEW"
            out["category"] = cls["category"]
            out["advice"] = ("candidate 제안만 — 사람이 git diff 로 확정. "
                             "빙구팩 자동변경 0(ai_recommend_only).")
            out["reason"] = "target_mutable"
            return out
        # 미존재 target — 보수적 검토 권고(자동 변경 절대 0).
        out["decision"] = "RECOMMEND_REVIEW"
        out["advice"] = ("조항 미존재 — candidate 검토만. 빙구팩 자동변경 0.")
        out["reason"] = "target_not_found"
        return out

    # target 없음(work_text 등 일반 신호) — 안전 화이트리스트 근접 안내만.
    out["decision"] = "RECOMMEND_REVIEW"
    out["matched_immutable"] = list(_immutable_index(pol).keys())
    out["advice"] = ("정책은 read-only 평가만 제공 — candidate 신호. 진화는 사람이 "
                     "독립 도구로. 빙구팩 자동결정 0.")
    out["reason"] = "no_target"
    return out


# ============================================================
# 셀프테스트 (temp dir · 운영/패키지 정책 미접촉 · write 0 게이트)
# ============================================================
def _selftest():
    import shutil
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
        return cond

    def _valid_doc():
        return {
            "schema_version": "1.0",
            "policy_id": "binggu.governance.clauses",
            "revision": "test.1",
            "immutable_whitelist": [
                {"id": "SAFE-DB", "rule": "ro", "category": "안전"},
                {"id": "SAFE-SECRET", "rule": "secret", "category": "안전"},
                {"id": "SAFE-DESTROY", "rule": "destroy", "category": "안전"},
                {"id": "SAFE-VERIFY", "rule": "verify", "category": "안전"},
            ],
            "clauses": [
                {"id": "STY-01", "text": "결론부터", "category": "스타일", "mutable": True},
                {"id": "MET-01", "text": "50% 직감", "category": "방법론", "mutable": True},
                {"id": "JDG-01", "text": "마진", "category": "판단", "mutable": True},
            ],
            "categories": ["안전", "스타일", "방법론", "판단"],
            "constitution": {
                "candidate_only": True, "human_approval_gate": True,
                "pii_excluded": True, "audit_chain": True,
                "ai_recommend_only": True, "auto_decision": False, "stdlib_only": True,
            },
        }

    tmp = Path(tempfile.mkdtemp(prefix="bgp_policy_"))
    try:
        pol_dir = tmp / "policies"
        pol_dir.mkdir(parents=True)
        pf = pol_dir / _POLICY_FILENAME
        pin = pol_dir / _PIN_FILENAME

        def write_policy(doc):
            pf.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

        def write_pin(dg):
            pin.write_text(dg + "\n", encoding="utf-8")

        env = dict(os.environ)
        env["BINGGU_POLICY_PATH"] = str(pf)

        doc = _valid_doc()
        write_policy(doc)
        dg = compute_digest(doc)
        write_pin(dg)

        # T1 정상 로드
        r = load_policy(env=env)
        check(r["ok"] and not r["fail_closed"] and r["digest"] == dg and r["reason"] is None,
              "T1 정상 로드(ok·digest 일치·reason None)")

        # T2 hash mismatch fail-closed (정책 1바이트 변조, pin 그대로)
        bad = _valid_doc()
        bad["revision"] = "tampered.X"
        write_policy(bad)
        r2 = load_policy(env=env)
        check(not r2["ok"] and r2["fail_closed"] and r2["reason"] == "policy_hash_mismatch",
              "T2 hash mismatch → fail-closed BLOCK")
        ev2 = evaluate({"target_clause_id": "STY-01"}, env=env)
        check(ev2["decision"] == "BLOCK" and ev2["fail_closed"],
              "T2b mismatch 시 evaluate decision=BLOCK")
        write_policy(doc)  # 복원
        write_pin(dg)

        # T3 pin 부재 fail-closed (가드③ — 미봉인 통과 금지)
        pin.unlink()
        r3 = load_policy(env=env)
        check(not r3["ok"] and r3["fail_closed"] and r3["reason"] == "pin_absent",
              "T3 pin 부재 → fail-closed BLOCK(미봉인 통과 금지)")
        write_pin(dg)

        # T4 schema_version 불일치
        bad_sv = _valid_doc()
        bad_sv["schema_version"] = "9.9"
        write_policy(bad_sv)
        write_pin(compute_digest(bad_sv))
        r4 = load_policy(env=env)
        check(not r4["ok"] and r4["reason"] == "schema_version_mismatch",
              "T4 schema_version 불일치 → fail-closed")
        write_policy(doc)
        write_pin(dg)

        # T5 corrupt json
        pf.write_text("{ not json", encoding="utf-8")
        r5 = load_policy(env=env)
        check(not r5["ok"] and r5["reason"] == "policy_corrupt",
              "T5 corrupt json → fail-closed(예외 0)")
        write_policy(doc)
        write_pin(dg)

        # T6 가드② — 안전조항 위장(immutable category 를 '스타일'로 위조)
        forged = _valid_doc()
        forged["immutable_whitelist"][0]["category"] = "스타일"
        write_policy(forged)
        write_pin(compute_digest(forged))
        r6 = load_policy(env=env)
        check(not r6["ok"] and r6["reason"].startswith("schema_invalid")
              and "immutable_category_not_safety" in r6["reason"],
              "T6 안전조항 category 위장 → schema_invalid(가드②)")
        write_policy(doc)
        write_pin(dg)

        # T6b 가드① — 필수 안전 카테고리 누락(SAFE-VERIFY 제거)
        missing = _valid_doc()
        missing["immutable_whitelist"] = [
            x for x in missing["immutable_whitelist"] if x["id"] != "SAFE-VERIFY"]
        write_policy(missing)
        write_pin(compute_digest(missing))
        r6b = load_policy(env=env)
        check(not r6b["ok"] and "required_immutable_missing:SAFE-VERIFY" in r6b["reason"],
              "T6b 필수 안전 카테고리 누락 → BLOCK(가드① 완전성 불변식)")
        write_policy(doc)
        write_pin(dg)

        # T6c 가드② — 안전 category clause 를 mutable clauses 로 이동 시도
        moved = _valid_doc()
        moved["clauses"].append({"id": "SAFE-X", "text": "위장", "category": "안전", "mutable": True})
        write_policy(moved)
        write_pin(compute_digest(moved))
        r6c = load_policy(env=env)
        check(not r6c["ok"] and "safety_clause_in_mutable_list" in r6c["reason"],
              "T6c 안전 category clause → mutable 리스트 이동 차단(가드②)")
        write_policy(doc)
        write_pin(dg)

        # T7 classify_clause
        c_sty = classify_clause("STY-01", env=env)
        c_met = classify_clause("MET-01", env=env)
        c_jdg = classify_clause("JDG-01", env=env)
        c_none = classify_clause("NOPE-99", env=env)
        check(c_sty["category"] == "스타일" and not c_sty["is_safety"] and c_sty["found"],
              "T7a classify 스타일")
        check(c_met["category"] == "방법론" and c_jdg["category"] == "판단",
              "T7b classify 방법론/판단")
        check(not c_none["found"], "T7c 미존재 id → found=False")

        # T8 is_immutable
        check(all(is_immutable(i, env=env) for i in REQUIRED_IMMUTABLE),
              "T8a SAFE-DB/SECRET/DESTROY/VERIFY 전부 immutable")
        check(not is_immutable("STY-01", env=env), "T8b 일반 조항 not immutable")

        # T9 evaluate 안전조항 vs mutable
        ev_safe = evaluate({"target_clause_id": "SAFE-DB"}, env=env)
        check(ev_safe["decision"] == "RECOMMEND_BLOCK"
              and "독립 도구" in (ev_safe["advice"] or "")
              and ev_safe["matched_immutable"] == ["SAFE-DB"],
              "T9a 안전조항 → RECOMMEND_BLOCK + 독립도구 advice")
        ev_mut = evaluate({"target_clause_id": "STY-01"}, env=env)
        check(ev_mut["decision"] == "RECOMMEND_REVIEW"
              and "candidate" in (ev_mut["advice"] or "")
              and ev_mut["category"] == "스타일",
              "T9b mutable 조항 → RECOMMEND_REVIEW + candidate advice")

        # T10 candidate/ai_recommend_only 불변 — 어떤 decision 도 자동적용 0
        for ev in (ev_safe, ev_mut, evaluate({"work_text": "테스트"}, env=env)):
            adv = (ev["advice"] or "")
            check(ev["decision"] in ("RECOMMEND_BLOCK", "RECOMMEND_REVIEW", "BLOCK")
                  and ("자동변경 0" in adv or "자동결정 0" in adv or "자동 적용" not in adv),
                  "T10 decision=%s candidate/자동적용 0 명시" % ev["decision"])

        # T11 정책/pin write 0 게이트 — module 코드(load/classify/is_immutable/evaluate)는
        # 절대 정책/pin 을 쓰지 않는다. baseline 은 모든 테스트 셋업 write 가 끝나고
        # doc/dg 정상 복원된 직후 측정 → 이후 module read-only 호출 배치 후 mtime 불변 검증.
        # (selftest 의 write_policy/write_pin 은 테스트 셋업이며 module 코드 아님.)
        pf_mtime0 = pf.stat().st_mtime_ns
        pin_mtime0 = pin.stat().st_mtime_ns
        for _ in range(3):
            load_policy(env=env)
            classify_clause("STY-01", env=env)
            is_immutable("SAFE-DB", env=env)
            evaluate({"target_clause_id": "SAFE-DB"}, env=env)
            evaluate({"target_clause_id": "STY-01"}, env=env)
            evaluate({"work_text": "x"}, env=env)
            compute_digest(doc)
        check(pf.stat().st_mtime_ns == pf_mtime0,
              "T11a 정책 파일 mtime 불변(module read-only · write 0)")
        check(pin.stat().st_mtime_ns == pin_mtime0,
              "T11b pin 파일 mtime 불변(module read-only · write 0)")

        # T12 BINGGU_POLICY_PATH override — 패키지 기본 미접촉
        pkg_default = os.path.join(_repo_root(), "policies", _POLICY_FILENAME)
        pkg_mtime0 = Path(pkg_default).stat().st_mtime_ns if Path(pkg_default).exists() else None
        _ = load_policy(env=env)  # temp override 로만 동작
        if pkg_mtime0 is not None:
            check(Path(pkg_default).stat().st_mtime_ns == pkg_mtime0,
                  "T12 override 사용 시 패키지 기본 정책 미접촉")
        else:
            check(True, "T12 패키지 기본 정책 부재(미접촉 자명)")

        # T12b — 패키지 기본 정책+pin 정합(실제 봉인 검증)
        if Path(pkg_default).exists():
            r_pkg = load_policy()  # env=None → 패키지 기본 사용
            check(r_pkg["ok"] and not r_pkg["fail_closed"],
                  "T12b 패키지 기본 정책 봉인 일치(pin 정합)")
        else:
            check(True, "T12b 패키지 기본 정책 부재(skip)")

        print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    # 기본: 패키지 정책 로드 요약(read-only)
    res = load_policy()
    print(json.dumps({
        "ok": res["ok"], "reason": res["reason"],
        "digest": res["digest"], "fail_closed": res["fail_closed"],
    }, ensure_ascii=False, indent=2))
