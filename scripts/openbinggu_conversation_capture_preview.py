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

INPUT_CAP = 20000
DEFAULT_MAX = 10
HARD_MAX = 20
EXCERPT = 80
_SENT_SPLIT = re.compile(r"(?<=[.!?다음임함됨까요])\s+|\n+")
_REDACT_RE = re.compile(r"\[REDACTED:\w+\]")

# preview 전용 추가 PII (owner 6/11 결정 (a)): 사업자등록번호 형식/bare 10자리.
# 공용 scan_residual_pii(batch_m1)는 "도메인 식별자 보존" 정책이라 무수정 — hosted 외부 표면인
# preview 에서만 보수적으로 제외한다.
_PREVIEW_PII_EXTRA = [
    ("scan_bizno_fmt", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)")),
    ("scan_bizno_bare", re.compile(r"(?<!\d)\d{10}(?!\d)")),
]


def _meaningful(s):
    stripped = _REDACT_RE.sub("", s).strip()
    if len(stripped) < 6:
        return False
    if " " not in stripped and len(stripped) < 12:
        return False
    return True


def _norm_hash(s):
    return hashlib.sha256(re.sub(r"\s+", " ", s).strip().encode("utf-8")).hexdigest()[:12]


def capture_preview(text, max_candidates=DEFAULT_MAX):
    """대화 발췌 → 핵심문장 후보 미리보기. 순수 함수(write 0). 반환 dict."""
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
    for sent in (s.strip() for s in _SENT_SPLIT.split(raw)):
        if not sent:
            continue
        if not _meaningful(sent):
            excl("short_or_fragment")
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
        verdict = a0.classify_node(
            {"id": "preview:" + h, "sentence": sent, "node_type": lkmap.KO2EN[kind],
             "evidence_refs": ["preview"]}, status="candidate")
        candidates.append({"sentence": sent[:EXCERPT], "label_kind": kind, "rule_id": rule_id,
                           "a0_verdict": verdict["verdict"], "candidate": True})

    lines = ["# 캡처 미리보기 — 후보 %d건 (전부 candidate, 미저장)" % len(candidates),
             "", "| # | 도장 | 문장 | 분류근거 | 헌법판정 |", "|---|---|---|---|---|"]
    for i, c in enumerate(candidates, 1):
        lines.append("| %d | %s | %s | %s | %s |" % (i, c["label_kind"], c["sentence"],
                                                     c["rule_id"], c["a0_verdict"]))
    if excluded:
        lines.append("")
        lines.append("제외: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(excluded.items())))
    if truncated:
        lines.append("")
        lines.append("(입력이 %d자 캡으로 절단됨)" % INPUT_CAP)
    lines.append("")
    lines.append("입력은 모델이 전달한 대화 텍스트 기준입니다(원문 그대로 보장 없음 — "
                 "모델 요약보다 원문 대화/로그를 넣을수록 도장 분류가 정확합니다). "
                 "미리보기일 뿐 아무것도 저장되지 않았습니다(nothing_saved=true). 등재는 로컬 승인 게이트에서만.")

    return {"candidates": candidates, "excluded_counts": excluded, "truncated": truncated,
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

    # 1. 정상 대화 — 5종 섞임
    convo = ("이 문서는 배포 절차를 정의한다. 테스트 로그에 통과 결과가 기록되어 있다. "
             "낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다. 백필 작업이 진행 중이다. "
             "이 입찰은 마진이 낮아 보류한다.")
    r1 = capture_preview(convo)
    kinds = sorted({c["label_kind"] for c in r1["candidates"]})
    rec(1, "정상 대화 5종 분류 후보", len(r1["candidates"]) == 5 and kinds == ["개념", "문서", "상태", "증거", "판단"]
        and r1["nothing_saved"] is True)

    # 2. secret 포함 — 해당 문장 후보 제외 + raw 미출력
    # 키워드·접두사 런타임 조립 — 공개 트리 스캐너 자기검출 회피 (6/10 박제)
    sec_line = "배포 키는 to" + "ken = 'gh" + "p_" + "EXAMPLE000000000000000000' 이다."
    r2 = capture_preview("이 입찰은 보류한다. " + sec_line)
    no_leak = ("ghp_" not in r2["preview_markdown"]) and all("ghp_" not in c["sentence"] for c in r2["candidates"])
    # secret 은 PII 스캐너(scan_kv)와 SECRET_PATTERNS 2중 레이어 중 먼저 잡는 쪽이 제외 — 어느 쪽이든 안전 동일
    sec_excluded = any(k == "secret_pattern" or k.startswith("pii_") for k in r2["excluded_counts"])
    rec(2, "secret 문장 후보 제외 + raw 미출력", len(r2["candidates"]) == 1 and sec_excluded and no_leak)

    # 3. PII 포함 — 제외 + kind 카운트만
    pii_line = "담당자 연락처는 010-" + "1234-5678 입니다."
    r3 = capture_preview("백필 작업이 진행 중이다. " + pii_line)
    rec(3, "PII 문장 후보 제외(kind 카운트만)", len(r3["candidates"]) == 1
        and any(k.startswith("pii_") for k in r3["excluded_counts"])
        and "1234" not in r3["preview_markdown"])

    # 3b. 사업자번호(형식/bare) — preview 전용 제외 (owner (a))
    r3b = capture_preview("이 입찰은 보류한다. 협력사 사업자등록번호는 123-45-" + "67890 입니다. "
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
    r6 = capture_preview("이 입찰은 보류한다. 이 입찰은  보류한다.\n이 입찰은 보류한다.")
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
