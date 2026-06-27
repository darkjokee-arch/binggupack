# -*- coding: utf-8 -*-
"""Step 0 — capture/preview 분류 일치성 블랙박스 하네스 (Test-First 방어선).

목적: 분류 로직을 **건드리지 않고**, 100문장 fixture로 두 경로의 후보 판정을 비교해
      현재 불일치(disagreement)와 false positive를 숫자로 고정한다. 통합(Step 1) 전후의
      회귀 가드로 쓴다. write 0 (순수 측정).

두 경로:
  - capture : binggupack.classifier.capture_classifier.classify(text).state == "captured_candidate"
  - preview : openbinggu_conversation_capture_preview.capture_preview(text) 의 candidates 길이 >= 1

기준: should_capture (개인 판단/교훈/선호/방향/리스크/영구규칙 = true, 조회/지시/보고/확인/잡담/순수지식 = false)

측정값:
  - disagreement       : 두 경로 후보판정이 다른 문장 수 (1순위 통합의 핵심 타깃 → 목표 0)
  - preview_false_pos  : 기대 false인데 preview가 후보로 올린 수 (노이즈)
  - capture_*          : capture 경로의 정확도/오탐/누락
실행:
  python tests/classifier_consistency_harness.py                 # 측정 리포트
  python tests/classifier_consistency_harness.py --json          # 기계 판독용 JSON
  python tests/classifier_consistency_harness.py --assert-consistent  # 불일치>0 이면 exit 1
  python tests/classifier_consistency_harness.py --semantic-on   # semantic 켜고 측정(비교용)
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_FIXTURE = os.path.join(_HERE, "fixtures", "classifier_consistency", "sentences_100.json")


def _load_paths():
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    sys.path.insert(0, _ROOT)
    from binggupack.classifier.capture_classifier import classify
    import openbinggu_conversation_capture_preview as prev
    return classify, prev


def evaluate(semantic_off=True):
    # 결정적 기준: semantic OFF 고정 (자매 selftest 동일 패턴). 비교 모드에서만 ON.
    if semantic_off:
        os.environ["BINGGU_SEMANTIC_OFF"] = "1"
    else:
        os.environ.pop("BINGGU_SEMANTIC_OFF", None)

    classify, prev = _load_paths()
    with open(_FIXTURE, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    rows = []
    for c in cases:
        text = c["text"]
        cap_state = classify(text)["state"]
        cap_cand = cap_state == "captured_candidate"
        p = prev.capture_preview(text)
        prev_cand = len(p["candidates"]) >= 1
        rows.append({
            "id": c["id"], "category": c["category"], "text": text,
            "expected": c["should_capture"],
            "capture": cap_cand, "cap_state": cap_state,
            "preview": prev_cand,
            "prev_excl": sorted(p["excluded_counts"].keys()),
            "agree": cap_cand == prev_cand,
            "cap_correct": cap_cand == c["should_capture"],
            "prev_correct": prev_cand == c["should_capture"],
        })

    n = len(rows)
    disagree = [r for r in rows if not r["agree"]]
    prev_fp = [r for r in rows if r["preview"] and not r["expected"]]   # 노이즈
    prev_fn = [r for r in rows if not r["preview"] and r["expected"]]
    cap_fp = [r for r in rows if r["capture"] and not r["expected"]]
    cap_fn = [r for r in rows if not r["capture"] and r["expected"]]
    return {
        "n": n,
        "disagreement": len(disagree),
        "preview_false_pos": len(prev_fp),
        "preview_false_neg": len(prev_fn),
        "preview_accuracy": round(sum(r["prev_correct"] for r in rows) / n, 3),
        "capture_false_pos": len(cap_fp),
        "capture_false_neg": len(cap_fn),
        "capture_accuracy": round(sum(r["cap_correct"] for r in rows) / n, 3),
        "rows": rows,
        "_lists": {"disagree": disagree, "prev_fp": prev_fp, "prev_fn": prev_fn,
                   "cap_fp": cap_fp, "cap_fn": cap_fn},
    }


def _print_report(res, semantic_off):
    mode = "semantic=OFF(결정적)" if semantic_off else "semantic=ON"
    print("=" * 78)
    print("분류 일치성 하네스 — Step 0 baseline  (%s, n=%d)" % (mode, res["n"]))
    print("=" * 78)
    print("  두 경로 불일치 (disagreement)   : %d / %d   ← 1순위 통합 목표 = 0" % (res["disagreement"], res["n"]))
    print("  preview false positive (노이즈) : %d        ← 기대 false인데 후보로 올림" % res["preview_false_pos"])
    print("  preview false negative          : %d" % res["preview_false_neg"])
    print("  preview accuracy                : %.1f%%" % (res["preview_accuracy"] * 100))
    print("  capture false positive          : %d" % res["capture_false_pos"])
    print("  capture false negative          : %d" % res["capture_false_neg"])
    print("  capture accuracy                : %.1f%%" % (res["capture_accuracy"] * 100))
    print("-" * 78)

    def _dump(title, lst, limit=40):
        if not lst:
            return
        print("\n[%s] %d건" % (title, len(lst)))
        for r in lst[:limit]:
            print("  #%-3d %-8s exp=%-5s cap=%-5s(%s) prev=%-5s  %r"
                  % (r["id"], r["category"], r["expected"], r["capture"],
                     r["cap_state"], r["preview"], r["text"]))

    _dump("불일치 (capture != preview)", res["_lists"]["disagree"])
    _dump("preview 노이즈 (false positive)", res["_lists"]["prev_fp"])
    _dump("preview 누락 (false negative)", res["_lists"]["prev_fn"])
    _dump("capture 오탐 (false positive)", res["_lists"]["cap_fp"])
    _dump("capture 누락 (false negative)", res["_lists"]["cap_fn"])
    print("\n주: Step 0(통합 전)에서는 불일치/노이즈가 존재하는 게 정상 — 이 숫자를 baseline으로 고정한다.")


def main():
    args = sys.argv[1:]
    semantic_off = "--semantic-on" not in args
    res = evaluate(semantic_off=semantic_off)
    if "--json" in args:
        out = {k: v for k, v in res.items() if k not in ("rows", "_lists")}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _print_report(res, semantic_off)
    if "--assert-consistent" in args:
        sys.exit(0 if res["disagreement"] == 0 else 1)
    sys.exit(0)


if __name__ == "__main__":
    main()
