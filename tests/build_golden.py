# -*- coding: utf-8 -*-
"""golden 기대치 생성 — fixture 의 사람 라벨에서 규칙으로 파생(구현 출력을 베끼지 않음).

사장님 지침: golden 은 "현재 구현 스냅샷"이면 안 된다. expected_* 는 사람이 승인한 기준이어야
진짜 회귀 가드다. 따라서 이 스크립트는 분류기/preview 를 **호출하지 않는다.** fixture 의
should_capture(사람 판정)와 category(사람 의도 reason)에서만 파생한다.

규칙(사람 검토 승인):
- expected_capture = fixture.should_capture
- expected_label   = "판단"  (should_capture=true 44문장은 전부 사용자 판단/교훈/선호류 → '판단' 도장.
                              2026-06-27 검토에서 44/44 확인. 새 카테고리 추가 시 LABEL_BY_CATEGORY 갱신)
- expected_signal  = fixture.category  (classify signals 가 이 사람 의도 신호를 '포함'해야 함)
- should_capture=false → label/signal 은 null (후보가 아니므로 도장/근거 없음)

실행: python tests/build_golden.py   → tests/fixtures/classifier_consistency/golden_100.json 갱신
"""
from pathlib import Path
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIX = os.path.join(_HERE, "fixtures", "classifier_consistency", "sentences_100.json")
_OUT = os.path.join(_HERE, "fixtures", "classifier_consistency", "golden_100.json")

# 사람 승인: 모든 판단류 카테고리는 '판단' 도장으로 고정. (도장은 5종이나 개인 판단/교훈/선호/규칙은
# 전부 '판단' 축에 든다 — 개념/문서/증거/상태는 should_capture=false 라 후보가 아님)
LABEL_BY_CATEGORY = {
    "방향결정": "판단", "선택판단": "판단", "리스크감지": "판단", "선호스타일": "판단",
    "반복기준": "판단", "교훈규범": "판단", "장기의도": "판단", "AI교정": "판단",
}


def build():
    cases = json.loads(Path(_FIX).read_text(encoding='utf-8'))["cases"]
    out = []
    for c in cases:
        cap = bool(c["should_capture"])
        if cap:
            label = LABEL_BY_CATEGORY.get(c["category"])
            if label is None:
                raise SystemExit("승인되지 않은 캡처 카테고리: %s (id=%s) — LABEL_BY_CATEGORY 검토 필요"
                                 % (c["category"], c["id"]))
            out.append({"id": c["id"], "expected_capture": True,
                        "expected_label": label, "expected_signal": c["category"]})
        else:
            out.append({"id": c["id"], "expected_capture": False,
                        "expected_label": None, "expected_signal": None})
    doc = {
        "_meta": {
            "basis": "fixture 의 사람 라벨(should_capture, category)에서 규칙 파생 — 구현 출력 스냅샷 아님",
            "expected_label_rule": "판단류 카테고리는 전부 '판단' 도장(2026-06-27 44/44 검토 승인)",
            "expected_signal_rule": "classify signals 가 fixture.category(사람 의도)를 포함해야 함",
            "regenerate": "python tests/build_golden.py",
        },
        "cases": out,
    }
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("golden 생성:", _OUT, "(", len(out), "cases )")


if __name__ == "__main__":
    build()
