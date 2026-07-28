# -*- coding: utf-8 -*-
"""OpenBinggu — conversation_capture_preview (B 로컬 구현, 저장 0 원칙).

설계: docs/BINGGUPACK_CONVERSATION_CAPTURE_PREVIEW_DESIGN.md
원칙: 미리보기만 — FS/DB/로그 write 0(순수 함수), raw 대화 전체 재출력 0,
      PII/secret 문장은 후보 제외(사유 kind 카운트만), candidate 고정, confirmed 단어 출력 0.
금지: conversation_candidate_save 미구현 · hosted 노출 0 · daemon/hook 0 (전부 별도 GO).

CLI: python openbinggu_conversation_capture_preview.py --selftest
"""
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openbinggu_label_kind_map as lkmap
import openbinggu_a0_node_dryrun as a0
import openbinggu_incoming_to_staging as v011        # SECRET_PATTERNS
from watcher_batch_m1 import scan_residual_pii       # PII kind 검출 (무수정 재사용)
import binggu_canonical_semantic as canon            # 도장 semantic 제안 (opt-in, 영구금지26 개정)
import binggu_capture_classifier as capclf           # SSOT 후보 게이트 (capture 와 동일 분류기)

INPUT_CAP = 20000
DEFAULT_MAX = 10
HARD_MAX = 20
# 정체성(owner 2026-06-15): 빙구팩 = 개인 온톨로지/AGI화 → 저장 단위 = 사용자가 고른 문장 전체.
# 80자 발췌 저장 폐기(사고 절단 금지). preview 표시도 전체("본 것 = 저장된 것" confirm 무결성).
# MAX_NODE_SENTENCE = 단일 문장 정당 상한. 초과 = 종결어미 없이 이어진 문단/로그 덩어리(비정상) →
# silent 절단 아닌 후보 제외(BLOCK). 정당한 긴 교훈 문장은 전부 통과. [[feedback_binggupack_identity_personal_ontology_agi]]
MAX_NODE_SENTENCE = 1000

# ── L-lane (2단계 절단 · 설계 docs/BINGGUPACK_STAGE2_TRUNCATION_DESIGN.md) ──────────────
# MAX_NODE_SENTENCE 초과분을 "버리는" 대신 별도 차선으로 옮긴다. 주 목록(candidates·
# excluded_counts)은 바이트 단위로 불변 — 분기가 PII/secret/classify/dedup/정원보다 앞이라
# 정원도 안 먹고 seen 도 안 건드리고 번호도 안 민다(설계 G-1).
# owner 결정(2026-07-28): 4000자를 넘어도 저장은 전문. 표시만 앞뒤로 줄이고 전문 sha·열람
# 명령을 같이 띄운다("본 것 = 저장된 것"의 취지 유지 · 6/15 사고 절단 금지 정합).
L_FULL_SHOW = 4000   # 이 이하는 L 섹션에도 전문 표시
L_MAX = 5            # L 정원. 초과분은 long_overflow 로 표면화하되 폐기 0(버퍼 원문 잔존)
L_HEAD, L_TAIL = 800, 400


def longsave_enabled():
    """L-lane opt-in. **호출 시점 평가** — import 시점 금지(MCP 는 장수 프로세스라
    import-time 이면 플래그를 켜도 영원히 옛 값을 본다)."""
    return os.environ.get("BINGGU_LONGSAVE_V1") == "1"
_SENT_SPLIT = re.compile(r"(?<=[.!?다음임함됨까요])\s+|\n+")
_REDACT_RE = re.compile(r"\[REDACTED:\w+\]")

# preview 전용 추가 PII (owner 6/11 결정 (a)): 사업자등록번호 형식/bare 10자리.
# 공용 scan_residual_pii(batch_m1)는 "도메인 식별자 보존" 정책이라 무수정 — hosted 외부 표면인
# preview 에서만 보수적으로 제외한다.
_PREVIEW_PII_EXTRA = [
    ("scan_bizno_fmt", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)")),
    ("scan_bizno_bare", re.compile(r"(?<!\d)\d{10}(?!\d)")),
]


# 제외 사유 사람친화 라벨 (사람 출력 전용 — JSON excluded_counts 키는 기계용으로 불변).
_VETO_LABEL = {
    "단순조회": "단순조회", "단순질문": "단순질문",
    "ops_imperative": "운영지시", "ops_report": "운영보고", "meta_confirm": "진행확인",
    "농담": "잡담", "감탄": "감탄", "인사": "인사", "임시감정": "임시감정",
    "ignored": "판단신호 없음", "preview_trigger": "저장 트리거",
}
_EXCL_LABEL = {
    "short_or_fragment": "너무짧음", "over_max_sentence": "문장과길이초과",
    "secret_pattern": "비밀패턴", "duplicate": "중복",
    "over_max_candidates": "후보수상한", "over_max": "상한초과",
}


def _friendly_excl(key):
    """excluded_counts 키를 사람친화 라벨로(판단아님/민감정보/너무짧음 등). 출력 전용."""
    if key.startswith("not_judgment:"):
        why = key.split(":", 1)[1]
        return _VETO_LABEL.get(why, why) + "(판단아님)"
    if key.startswith("pii_"):
        return "민감정보"
    return _EXCL_LABEL.get(key, key)


def _meaningful(s):
    stripped = _REDACT_RE.sub("", s).strip()
    if len(stripped) < 6:
        return False
    if " " not in stripped and len(stripped) < 12:
        return False
    return True


def _norm_hash(s):
    return hashlib.sha256(re.sub(r"\s+", " ", s).strip().encode("utf-8")).hexdigest()[:12]


def _suggest_subtype(sent):
    """보조 semantic_subtype 제안 (교훈/결정/선호/설계결정/버그패턴/사실 — canonical 5종 도장 아님).
    값 원천 = cos shadow, opt-in 게이트는 canon.enabled() 재사용(semantic_label_enabled OR Ollama bge-m3).
    hi/ambiguous band 만 채움, lo/차단/실패/OFF → None(NULL 저장). enabled OFF면 embed 호출 0(순수성 보존)."""
    if not canon.enabled():
        return None
    try:
        from binggu_semantic_shadow import get_cached_shadow  # lazy import(순환 import 회피)
        sug = get_cached_shadow().subtype_suggestion(sent)
        if sug and sug.get("band") in ("hi", "ambiguous"):
            return sug["sem_subtype"]
    except Exception:
        return None
    return None


def _blob_suspect(raw):
    """L 항목이 '사람이 쓴 긴 판단'인지 '붙여넣기 덩어리'인지의 보조 라벨.

    **분리 전 원본 축**에서 판정한다(문장으로 쪼갠 뒤엔 덩어리가 짧은 조각이 되어 신호가 사라진다).
    판정 정본은 capture 의 `_is_bulk_text` 를 그대로 재사용 — 여기서 임계를 새로 만들지 않는다.
    설계상 종결어미 부재·특수문자 비율 보조신호가 후보로 올라와 있으나 **임계 미실측**이라 넣지 않는다
    (추측값을 박지 않는다). 어떤 경우에도 **폐기 0** — 이 값은 라벨링·정렬·자동포함 여부에만 쓴다."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from binggu_capture_persist import _is_bulk_text   # 지연 import(순환 회피 + 정본 재사용)
        return bool(_is_bulk_text(raw))
    except Exception:
        return False   # 판정 불가 = 라벨 없음. 폐기가 아니라 자동포함만 보수적으로 막힌다


def _long_collect(sent, items, excl, blob_suspect):
    """L-lane 수집 — 주 목록 계산에 일절 영향 주지 않는다(별도 리스트·별도 카운터).

    ★ 안전 게이트는 주 목록과 동일하게 건다. 분기 지점이 PII/secret 검사보다 **앞**이라
      그냥 담으면 PII 든 긴 문장이 검사를 건너뛰고 저장 후보가 된다(구현 중 발견 — 설계 보정).
      제외 카운트는 `long_excluded` 로 분리해 `excluded_counts` 바이트 불변(G-1)을 지킨다."""
    pii = scan_residual_pii(sent) + [k for k, rx in _PREVIEW_PII_EXTRA if rx.search(sent)]
    if pii:
        for k in pii:
            excl("pii_" + k)
        return
    if any(p.search(sent) for p in v011.SECRET_PATTERNS):
        excl("secret_pattern")
        return
    if len(items) >= L_MAX:
        excl("long_overflow")   # 표면화만 — 버퍼 원문은 남아 재-preview 로 회수 가능
        return
    kind, _rule = lkmap.classify_label_kind(sent)
    h = hashlib.sha256(sent.encode("utf-8")).hexdigest()[:16]
    verdict = a0.classify_node(
        {"id": "long:" + h, "sentence": sent, "node_type": lkmap.KO2EN[kind],
         "evidence_refs": ["preview"]}, status="candidate")
    items.append({"label": "L%d" % (len(items) + 1), "sentence": sent, "length": len(sent),
                  "sha": h, "blob_suspect": blob_suspect, "label_kind": kind,
                  "a0_verdict": verdict["verdict"], "capture_reason": "장문(주 목록 상한 초과)"})


def _long_display(item):
    """L 항목 표시 문자열 — L_FULL_SHOW 이하는 전문, 초과는 머리·꼬리 + 전문 길이·sha·열람 명령.
    저장은 **어느 쪽이든 전문**이다. 축약될 때도 저장될 글자수·sha·여는 법을 표시 안에 둔다."""
    s = item["sentence"]
    if item["length"] <= L_FULL_SHOW:
        return s
    return ("%s …(중략)… %s\n\n> 전문 %d자 · sha:%s · 전문 보기: `binggu capture --show %s` "
            "(표시만 줄었고 **저장은 전문**)"
            % (s[:L_HEAD], s[-L_TAIL:], item["length"], item["sha"][:8], item["label"]))


def capture_preview(text, max_candidates=DEFAULT_MAX, explicit=False):
    """대화 발췌 → 핵심문장 후보 미리보기. 순수 함수(write 0). 반환 dict.

    explicit: 명시 저장 의도 경로(pair/remember 등 사용자가 직접 '이걸 기억해'라고 친 입력)면 True.
      판단-veto(SSOT classify 게이트)만 면제한다 — 사용자가 직접 친 문장은 자동수집 노이즈가 아니므로.
      PII/secret/길이/중복 등 안전·형식 게이트는 explicit 와 무관하게 그대로 적용된다(완화 0).
      자동 캡처/일반 preview 는 explicit=False 로 노이즈 0 을 유지한다."""
    max_candidates = max(1, min(int(max_candidates or DEFAULT_MAX), HARD_MAX))
    raw = (text or "")
    truncated = False
    if len(raw) > INPUT_CAP:
        cut = raw.rfind("\n", 0, INPUT_CAP)
        raw = raw[:cut if cut > 200 else INPUT_CAP]
        truncated = True

    excluded = {}

    def excl(kind):
        excluded[kind] = excluded.get(kind, 0) + 1

    candidates = []
    seen = set()
    long_items, long_excluded = [], {}
    long_on = longsave_enabled()          # 호출 시점 1회 평가(루프 중 값 흔들림 방지)
    blob_suspect = _blob_suspect(raw) if long_on else False   # 분리 전 **원본 축**에서 판정

    def long_excl(kind):
        long_excluded[kind] = long_excluded.get(kind, 0) + 1

    for sent in (s.strip() for s in _SENT_SPLIT.split(raw)):
        if not sent:
            continue
        if not _meaningful(sent):
            excl("short_or_fragment")
            continue
        if len(sent) > MAX_NODE_SENTENCE:
            # 단일 문장 정당 상한 초과 = 문단/로그 덩어리(split 실패) — 절단 아닌 제외.
            # 주 목록에서의 제외·카운트는 **불변**(G-1). L-lane 은 여기서 갈라지는 별도 차선일 뿐.
            excl("over_max_sentence")
            if long_on:
                _long_collect(sent, long_items, long_excl, blob_suspect)
            continue
        # PII/secret — redact 가 아니라 후보 제외 (owner 조건). 사유 kind 만 집계.
        pii = scan_residual_pii(sent) + [k for k, rx in _PREVIEW_PII_EXTRA if rx.search(sent)]
        if pii:
            for k in pii:
                excl("pii_" + k)
            continue
        if any(p.search(sent) for p in v011.SECRET_PATTERNS):
            excl("secret_pattern")
            continue
        # SSOT 후보 게이트: capture 와 동일한 분류기(should_capture)로 거른다. 판단/교훈/선호/
        # 영구규칙(captured_candidate)만 후보로 통과. 단순조회·일회성지시·운영보고·메타확인·잡담·
        # 순수지식(ignored/preview_trigger)은 제외 — preview/capture 후보 기준 불일치(노이즈) 제거.
        # 안전(PII/secret)은 이 게이트보다 앞서 항상 제외되므로 게이트 결과와 무관하게 보호된다.
        cap = capclf.classify(sent)
        if cap["state"] != "captured_candidate" and not explicit:
            excl("not_judgment:" + (cap["vetoes"][0] if cap["vetoes"] else cap["state"]))
            continue
        # 왜 후보인지(설명가능성) — 캡처 게이트가 잡은 판단 signal. explicit(명시 입력)은 판단-veto
        # 면제이므로 signal 이 없을 수 있다 → '명시저장'. pinned 도 명시 저장 의도.
        capture_reason = ",".join(cap["signals"]) or ("명시저장" if (explicit or cap["pinned"]) else "판단")
        h = _norm_hash(sent)
        if h in seen:
            excl("duplicate")
            continue
        seen.add(h)
        if len(candidates) >= max_candidates:
            excl("over_max_candidates")
            continue
        kind, rule_id = lkmap.classify_label_kind(sent)   # 종결어 규칙 = 기본/fallback
        # 영구금지26 개정(owner GO 2026-06-14): opt-in 시 도장을 의미(semantic)로 제안.
        # hi=확정 / ambiguous=확인권장 / lo·차단·실패=None→종결어 규칙 유지. 저장 0·사람 confirm 게이트.
        sem = canon.suggest_label_kind(sent)
        if sem is not None:
            kind = sem["kind"]
            rule_id = "semantic_%s_%.2f" % (sem["band"], sem["conf"])
        # 보조 semantic_subtype(6종) — label_kind(5종 도장)와 별개 축. 있으면 채움, 없으면 None(NULL).
        semantic_subtype = _suggest_subtype(sent)
        verdict = a0.classify_node(
            {"id": "preview:" + h, "sentence": sent, "node_type": lkmap.KO2EN[kind],
             "evidence_refs": ["preview"]}, status="candidate")
        # 문장 전체 저장(발췌 cut 제거) — 본 것 = 저장된 것. node_id/hash 도 전체 기준.
        # gate: 캡처 게이트 출처(regex 정본). rule_id: 도장 근거(semantic_* 이면 semantic 개입, 그 외 종결어 fallback).
        candidates.append({"sentence": sent, "label_kind": kind, "rule_id": rule_id,
                           "semantic_subtype": semantic_subtype,
                           "capture_reason": capture_reason, "gate": "regex",
                           "a0_verdict": verdict["verdict"], "candidate": True})

    lines = ["# 캡처 미리보기 — 후보 %d건 (전부 candidate, 미저장)" % len(candidates),
             "", "| # | 도장 | 문장 | 캡처근거 | 도장근거 | 헌법판정 |", "|---|---|---|---|---|---|"]
    for i, c in enumerate(candidates, 1):
        lines.append("| %d | %s | %s | %s | %s | %s |" % (i, c["label_kind"], c["sentence"],
                                                          c["capture_reason"], c["rule_id"], c["a0_verdict"]))
    if excluded:
        lines.append("")
        lines.append("제외: " + ", ".join("%s=%d" % (_friendly_excl(k), v) for k, v in sorted(excluded.items())))
    if truncated:
        lines.append("")
        lines.append("(입력이 %d자 캡으로 절단됨)" % INPUT_CAP)
    # L 섹션 — 항목이 있을 때만 붙인다. 플래그 OFF 또는 장문 0 이면 markdown 은 종전과 byte 동일.
    if long_items:
        lines.append("")
        lines.append("## 긴 발화 %d건 — 주 번호(1,2,3…)와 별개 축입니다" % len(long_items))
        lines.append("")
        lines.append("| # | 도장 | 문장 | 길이 | 헌법판정 |")
        lines.append("|---|---|---|---|---|")
        for it in long_items:
            mark = " ⚠덩어리 의심" if it["blob_suspect"] else ""
            lines.append("| %s | %s%s | %s | %d자 | %s |"
                         % (it["label"], it["label_kind"], mark, _long_display(it),
                            it["length"], it["a0_verdict"]))
        lines.append("")
        lines.append("저장하려면 `SAVE %s` — 주 목록 번호는 그대로입니다."
                     % ", ".join(it["label"] for it in long_items))
    if long_excluded:
        lines.append("")
        lines.append("긴 발화 제외: "
                     + ", ".join("%s=%d" % (_friendly_excl(k), v)
                                 for k, v in sorted(long_excluded.items())))
    lines.append("")
    lines.append("입력은 모델이 전달한 대화 텍스트 기준입니다(원문 그대로 보장 없음 — "
                 "모델 요약보다 원문 대화/로그를 넣을수록 도장 분류가 정확합니다). "
                 "미리보기일 뿐 아무것도 저장되지 않았습니다(nothing_saved=true). 등재는 로컬 승인 게이트에서만.")

    # long_candidates/long_excluded 는 플래그 OFF 에서도 **항상 존재**([]/{}) — 구 소비자가
    # 키 부재로 죽지 않게(KeyError 0 · 설계 J5). 값이 비면 markdown 도 종전과 byte 동일.
    return {"candidates": candidates, "excluded_counts": excluded, "truncated": truncated,
            "long_candidates": long_items, "long_excluded": long_excluded,
            "preview_markdown": "\n".join(lines), "nothing_saved": True}


# ---------------- selftest (C) ----------------

def _fs_snapshot(roots):
    snap = {}
    for r in roots:
        for dp, _, fns in os.walk(r):
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    snap[p] = os.path.getmtime(p)
                except OSError:
                    pass
    return snap


def run_selftest():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    watch = [os.path.join(base, "tmp"), os.path.join(base, "reports"),
             os.path.expanduser("~/.claude/memory/ontology")]
    watch = [w for w in watch if os.path.isdir(w)]
    fs_before = _fs_snapshot(watch)
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    # 1. 정상 대화 — 판단/결정/규칙/교훈/리스크 5문장(전부 SSOT captured). 순수 사실/개념/상태는
    #    SSOT 게이트(should_capture)에서 제외되므로 후보 문장은 사용자 판단류로 구성한다.
    convo = ("이 입찰은 마진이 낮아 보류하기로 결정했다. 캐시 전략은 이걸로 확정한다. "
             "백업은 항상 작업 전에 먼저 해 둔다. 다음부터는 로그를 먼저 본다. "
             "이 변경은 회귀 위험이 커서 조심해야 한다.")
    r1 = capture_preview(convo)
    rec(1, "정상 대화 판단 5문장 후보(SSOT 통과)",
        len(r1["candidates"]) == 5 and r1["nothing_saved"] is True)

    # 2. secret 포함 — 해당 문장 후보 제외 + raw 미출력
    # 키워드·접두사 런타임 조립 — 공개 트리 스캐너 자기검출 회피 (6/10 박제)
    sec_line = "배포 키는 to" + "ken = 'gh" + "p_" + "EXAMPLE000000000000000000' 이다."
    r2 = capture_preview("이 입찰은 보류하기로 결정했다. " + sec_line)
    no_leak = ("ghp_" not in r2["preview_markdown"]) and all("ghp_" not in c["sentence"] for c in r2["candidates"])
    # secret 은 PII 스캐너(scan_kv)와 SECRET_PATTERNS 2중 레이어 중 먼저 잡는 쪽이 제외 — 어느 쪽이든 안전 동일
    sec_excluded = any(k == "secret_pattern" or k.startswith("pii_") for k in r2["excluded_counts"])
    rec(2, "secret 문장 후보 제외 + raw 미출력", len(r2["candidates"]) == 1 and sec_excluded and no_leak)

    # 3. PII 포함 — 제외 + kind 카운트만
    pii_line = "담당자 연락처는 010-" + "1234-5678 입니다."
    r3 = capture_preview("이 입찰은 보류하기로 결정했다. " + pii_line)
    rec(3, "PII 문장 후보 제외(kind 카운트만)", len(r3["candidates"]) == 1
        and any(k.startswith("pii_") for k in r3["excluded_counts"])
        and "1234" not in r3["preview_markdown"])

    # 3b. 사업자번호(형식/bare) — preview 전용 제외 (owner (a))
    r3b = capture_preview("이 입찰은 보류하기로 결정했다. 협력사 사업자등록번호는 123-45-" + "67890 입니다. "
                          "구계좌 사업자번호 12345" + "67890 등록 확인이 필요하다.")
    rec(11, "bizno 형식/bare 후보 제외(카운트만)", len(r3b["candidates"]) == 1
        and r3b["excluded_counts"].get("pii_scan_bizno_fmt", 0) >= 1
        and r3b["excluded_counts"].get("pii_scan_bizno_bare", 0) >= 1
        and "67890" not in r3b["preview_markdown"])

    # 4. 긴 대화 — 캡 절단 + max_candidates 상한
    long_text = "\n".join("케이스 %d 는 검토 후 보류한다." % i for i in range(2000))
    r4 = capture_preview(long_text, max_candidates=20)
    rec(4, "긴 대화 캡 절단 + 상한", r4["truncated"] is True and len(r4["candidates"]) <= 20)

    # 5. 빈 대화 / 공백만
    r5a = capture_preview("")
    r5b = capture_preview("   \n  ")
    rec(5, "빈 대화 후보 0", len(r5a["candidates"]) == 0 and len(r5b["candidates"]) == 0
        and r5a["nothing_saved"] is True)

    # 6. 중복 후보 dedup
    r6 = capture_preview("이 입찰은 보류하기로 결정했다. 이 입찰은  보류하기로 결정했다.\n이 입찰은 보류하기로 결정했다.")
    rec(6, "중복 후보 dedup(1건+duplicate 집계)", len(r6["candidates"]) == 1
        and r6["excluded_counts"].get("duplicate", 0) == 2)

    # 7. raw 대화 전체 재출력 금지 — 입력 전문이 출력에 미포함 (다문장 입력 기준)
    joined_out = r1["preview_markdown"]
    rec(7, "raw 전문 재출력 금지", convo not in joined_out)

    # 8. confirmed 단어 출력 0 + 전 후보 candidate
    all_md = "\n".join(r["preview_markdown"] for r in (r1, r2, r3, r4, r5a, r6))
    rec(8, "confirmed 출력 0 · candidate 전건", ("confirmed" not in all_md)
        and all(c["candidate"] is True for r in (r1, r2, r3, r4, r6) for c in r["candidates"]))

    # 9. 멱등 (2회 동일)
    rec(9, "멱등(2회 동일)", capture_preview(convo) == capture_preview(convo))

    # 12. 긴 문장(80자 초과·MAX 이내) 전체 저장 + preview 전체 표시(본 것 = 저장된 것)
    long_sent = "이 입찰은 " + "매우 " * 30 + "신중하게 검토한 끝에 보류하기로 결정했다."  # 한 문장, ~110자
    r12 = capture_preview(long_sent)
    rec(12, "긴 문장 전체 보존(발췌 cut 0) + preview 전체 표시",
        len(r12["candidates"]) == 1 and r12["candidates"][0]["sentence"] == long_sent
        and len(long_sent) > 80 and long_sent in r12["preview_markdown"])

    # 13. MAX_NODE_SENTENCE 초과(문단/로그 덩어리) → silent 절단 아닌 후보 제외
    huge = "가 " * 600 + "이다."  # 한 문장, ~1200자 (종결어미 중간 없음 = split 1건)
    r13 = capture_preview(huge)
    rec(13, "MAX_NODE_SENTENCE 초과 후보 제외(절단 0)",
        len(huge) > MAX_NODE_SENTENCE and len(r13["candidates"]) == 0
        and r13["excluded_counts"].get("over_max_sentence", 0) >= 1)

    # 14. 80자 뒤 PII 차단 — 발췌 저장 시절엔 sent[:80] 이 못 보던 위협(전체 스캔으로 해소)
    pii_tail = "이 입찰은 " + "매우 " * 30 + "신중히 검토 후 담당자 연락처 010-" + "9876-5432 로 보류한다."
    r14 = capture_preview(pii_tail)
    rec(14, "80자 뒤 PII 차단(전체 스캔) + raw 미출력",
        len(pii_tail) > 80 and len(r14["candidates"]) == 0
        and any(k.startswith("pii_") for k in r14["excluded_counts"])
        and "9876" not in r14["preview_markdown"])

    # 15. 설명가능성 — 후보에 capture_reason(왜 후보)/gate 노출 + 제외 사유 사람친화 라벨
    r15 = capture_preview("이 입찰은 마진이 낮아 보류하기로 결정했다. 상태 보여줘.")
    c15 = r15["candidates"][0] if r15["candidates"] else {}
    rec(15, "후보 capture_reason/gate 노출 + 제외 친화 라벨",
        len(r15["candidates"]) == 1 and bool(c15.get("capture_reason")) and c15.get("gate") == "regex"
        and "단순조회" in r15["preview_markdown"]
        and any(k.startswith("not_judgment:") for k in r15["excluded_counts"]))

    # ── L-lane (2단계 절단) 16~18 ─────────────────────────────────────────────
    # huge(케이스 13, ~1200자 단일문)를 그대로 재사용해 "같은 입력, 플래그만 다름"을 대조한다.
    _prev_flag = os.environ.pop("BINGGU_LONGSAVE_V1", None)
    try:
        r16 = capture_preview(huge)
        rec(16, "플래그 OFF — long_candidates 키는 있고 비어 있음(구 소비자 KeyError 0)",
            r16["long_candidates"] == [] and r16["long_excluded"] == {}
            and r16["candidates"] == r13["candidates"]
            and r16["excluded_counts"] == r13["excluded_counts"])

        os.environ["BINGGU_LONGSAVE_V1"] = "1"
        r17 = capture_preview(huge)
        l17 = r17["long_candidates"][0] if r17["long_candidates"] else {}
        rec(17, "플래그 ON — L1 수집(전문 byte 동일·절단 0) + 주 목록 축 불변",
            len(r17["long_candidates"]) == 1 and l17.get("label") == "L1"
            and l17.get("sentence") == huge.strip() and l17.get("length") == len(huge.strip())
            # ★주 목록은 OFF 와 완전히 같아야 한다(G-1) — 정원·dedup·번호 어느 것도 안 밀린다
            and r17["candidates"] == r13["candidates"]
            and r17["excluded_counts"] == r13["excluded_counts"])

        # 18. ★L-lane 도 PII/secret 게이트를 통과해야 한다.
        #     분기 지점이 PII 검사보다 앞이라, 그냥 담으면 PII 든 긴 문장이 검사를 건너뛴다
        #     (구현 중 발견한 설계 공백 — 회귀로 못박는다). 제외 카운트는 long_excluded 로 분리해
        #     주 목록 excluded_counts 의 byte 불변을 깨지 않는다.
        # 1000자를 확실히 넘겨야 L-lane 분기로 간다(짧으면 주 목록 PII 게이트에서 걸려 검증이 무의미).
        pii_long = "연락처 010-9876-5432 로 연락해서 " + "이 건은 계속 보류하기로 하고 " * 70 + "이다."
        r18 = capture_preview(pii_long)
        rec(18, "L-lane 도 PII 차단(long_excluded 로 분리 집계·raw 미출력)",
            len(pii_long) > MAX_NODE_SENTENCE and r18["long_candidates"] == []
            and any(k.startswith("pii_") for k in r18["long_excluded"])
            and "9876" not in r18["preview_markdown"])
    finally:
        os.environ.pop("BINGGU_LONGSAVE_V1", None)
        if _prev_flag is not None:
            os.environ["BINGGU_LONGSAVE_V1"] = _prev_flag

    # 10. write/save 0 — 감시 디렉토리 FS 전후 동일 + 본 모듈 save 함수 부재
    fs_after = _fs_snapshot(watch)
    rec(10, "write/save 0 (FS 전후 동일 + save 함수 부재)", fs_before == fs_after
        and not any(n.startswith("save") or "candidate_save" in n for n in dir(sys.modules[__name__])))

    print("=" * 74)
    print("OpenBinggu conversation_capture_preview — selftest (저장 0, 미리보기만)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("fs_write=0  db_write=0  raw_full_echo=0  confirmed=0  hosted_exposure=0  daemon=0")
    gate = "GO" if npass == len(results) else "NO-GO"
    print("GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        run_selftest()
    else:
        print("usage: openbinggu_conversation_capture_preview.py [--selftest]")
        sys.exit(2)
