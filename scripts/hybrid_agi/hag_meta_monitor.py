# -*- coding: utf-8 -*-
"""hag_meta_monitor.py — 메타감시망 (인간 발화 정형화 추이 · 경고만).

목적: 사람 발화가 점점 "정형화"(어휘 다양성 하락 · 구조 비정형성 하락)되는지
      시간축으로 관찰해 하락 추이일 때만 warn 신호를 낸다.

영구 원칙 (전부 selftest 증명):
  - 경고만 한다. 자동 행동 0 · 차단 0 · write 0 · DB/ledger 미접촉(읽지도 않음).
  - 판단은 사람 몫. warn은 "사람이 봐달라"는 신호일 뿐 — 어떤 부작용도 없다.
  - 오탐 무시권: 단발 하락/노이즈는 사람이 무시해도 됨(threshold·연속성으로 완충).
  - 결정론적: 실시간 시각/난수 미사용. window·baseline은 주입값만.
  - 순수 함수: 입력(텍스트/시계열) → 지표/신호. 외부 상태 의존 0.

지표:
  - diversity(texts): 어휘 다양성(type-token ratio, TTR) + 구조 비정형성.
      TTR = 고유토큰수 / 전체토큰수. 1.0 = 모두 다른 단어(비정형), 낮을수록 정형/반복.
      구조 비정형성 = 발화 길이(토큰수) 분산의 정규화 — 길이가 다양할수록 비정형.
  - track_trend(window, baseline): 최근 window 평균을 baseline과 비교.
      하락(평균 < baseline - margin)이고 연속 하락 추세면 warn.
"""
import math
import re
import sys


_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+", re.UNICODE)


def _tokenize(text):
    """언어중립 토큰화: 한글 음절 블록 + 영숫자. 구두점/공백 무시."""
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def type_token_ratio(texts):
    """어휘 다양성(TTR). 전체 토큰 대비 고유 토큰 비율. 토큰 0이면 0.0."""
    toks = []
    for t in texts:
        toks.extend(_tokenize(t))
    if not toks:
        return 0.0
    return len(set(toks)) / len(toks)


def structural_irregularity(texts):
    """구조 비정형성: 발화별 토큰 길이의 변동(정규화 표준편차, CV).
    길이가 들쭉날쭉(비정형)할수록 큼, 모두 같은 길이(정형)면 0.
    값 범위는 0.0~1.0으로 포화(saturate)시켜 안정화."""
    lens = [len(_tokenize(t)) for t in texts]
    lens = [n for n in lens if n > 0]
    if len(lens) < 2:
        return 0.0
    mean = sum(lens) / len(lens)
    if mean == 0:
        return 0.0
    var = sum((n - mean) ** 2 for n in lens) / len(lens)
    cv = math.sqrt(var) / mean   # 변동계수
    return min(cv, 1.0)


def diversity(texts):
    """종합 다양성 점수 = TTR · 구조 비정형성 가중 결합. 범위 0.0~1.0.
    높을수록 비정형(다양), 낮을수록 정형화(반복·획일).
    반환: {ttr, structural, score, n_texts, n_tokens}."""
    texts = [t for t in (texts or []) if isinstance(t, str)]
    ttr = type_token_ratio(texts)
    struct = structural_irregularity(texts)
    # 어휘 다양성이 주, 구조 비정형성이 보조(0.7/0.3).
    score = round(0.7 * ttr + 0.3 * struct, 6)
    n_tokens = sum(len(_tokenize(t)) for t in texts)
    return {
        "ttr": round(ttr, 6),
        "structural": round(struct, 6),
        "score": score,
        "n_texts": len(texts),
        "n_tokens": n_tokens,
    }


def track_trend(series, window=3, baseline=None, margin=0.05):
    """시간축 다양성 추이 관찰 — 하락 시 warn(경고만, 행동 0).

    입력:
      series   : 시간순 다양성 점수 리스트(과거→현재). 각 diversity()의 score 등.
      window   : 최근 몇 개로 현재 수준을 평균낼지(>=1).
      baseline : 기준선. None이면 series 앞부분(window 이전 전체) 평균을 자동 기준.
      margin   : baseline 대비 이만큼 이상 낮아야 하락으로 본다(오탐 완충).

    판정:
      - 최근 window 평균 < (baseline - margin)  → 정형화(다양성 하락) 의심
      - AND 연속 하락 추세(최근 window가 우하향)   → warn=True
      - 둘 중 하나라도 아니면 warn=False (정상 추이는 무경고)

    반환: {warn, level, recent_avg, baseline, drop, monotonic_down, note}
          level: "ok" | "watch" | "warn". 자동 행동 0 — 사람 판단 신호일 뿐.
    """
    vals = [float(v) for v in (series or [])]
    window = max(1, int(window))

    if len(vals) < window + 1:
        return {
            "warn": False, "level": "ok",
            "recent_avg": None, "baseline": baseline, "drop": None,
            "monotonic_down": False,
            "note": "표본 부족(시계열 %d < window+1=%d) — 관찰만, 행동 0" % (len(vals), window + 1),
        }

    recent = vals[-window:]
    recent_avg = sum(recent) / len(recent)

    if baseline is None:
        base_part = vals[:-window]
        baseline = sum(base_part) / len(base_part) if base_part else recent_avg
    baseline = float(baseline)

    drop = baseline - recent_avg
    below = recent_avg < (baseline - margin)
    # 연속 하락: 최근 window 구간이 비증가(우하향) — 단발 노이즈 배제.
    monotonic_down = all(recent[i] >= recent[i + 1] for i in range(len(recent) - 1)) and len(recent) >= 2

    if below and monotonic_down:
        level, warn = "warn", True
        note = "정형화 의심: 최근 다양성 하락(연속) — 사람 확인 권고(자동 행동 0)"
    elif below or (monotonic_down and drop > 0):
        level, warn = "watch", False
        note = "약한 하락 신호 — 오탐 무시 가능, 경고 아님"
    else:
        level, warn = "ok", False
        note = "정상 추이 — 무경고"

    return {
        "warn": warn, "level": level,
        "recent_avg": round(recent_avg, 6),
        "baseline": round(baseline, 6),
        "drop": round(drop, 6),
        "monotonic_down": monotonic_down,
        "note": note,
    }


# ---------------------------------------------------------------------------
# selftest — 결정론적(시각/난수 0). `python hag_meta_monitor.py --selftest`
# ---------------------------------------------------------------------------
def _selftest():
    ok = [True]

    def ck(cond, label):
        mark = "PASS" if cond else "FAIL"
        if not cond:
            ok[0] = False
        print("  [%s] %s" % (mark, label))

    # 1) 다양성 계산 — 모두 다른 단어 = 높은 TTR, 같은 단어 반복 = 낮은 TTR
    div_varied = diversity(["alpha beta gamma", "delta epsilon zeta"])
    div_repeat = diversity(["same same same", "same same same"])
    ck(div_varied["ttr"] == 1.0, "다양성: 모든 토큰 고유 → TTR 1.0")
    ck(div_repeat["ttr"] < 0.3, "다양성: 반복 발화 → 낮은 TTR(정형화)")
    ck(div_varied["score"] > div_repeat["score"], "다양성: 다양 발화 score > 정형 발화 score")

    # 2) 빈 입력/비문자 안전
    ck(diversity([])["score"] == 0.0 and diversity([])["n_tokens"] == 0, "빈 입력 → score 0, 토큰 0")
    ck(diversity([None, 123, "real word"])["n_texts"] == 1, "비문자 입력 무시(문자열만 집계)")

    # 3) 구조 비정형성 — 길이 동일=0, 들쭉날쭉=양수
    ck(structural_irregularity(["a b", "c d", "e f"]) == 0.0, "구조: 동일 길이 발화 → 비정형성 0(정형)")
    ck(structural_irregularity(["a", "a b c d e f g h"]) > 0.0, "구조: 길이 편차 → 비정형성 > 0")

    # 4) 하락 추이 → warn (연속 우하향 + baseline 미달)
    down = track_trend([0.9, 0.85, 0.7, 0.5, 0.3], window=3, baseline=0.85, margin=0.05)
    ck(down["warn"] is True and down["level"] == "warn", "추이: 연속 하락 → warn=True")

    # 5) 정상(상승/평탄) 추이 → 무경고
    up = track_trend([0.4, 0.5, 0.6, 0.7, 0.8], window=3, baseline=0.5, margin=0.05)
    ck(up["warn"] is False and up["level"] == "ok", "추이: 상승 추이 → 무경고")
    flat = track_trend([0.7, 0.7, 0.7, 0.7, 0.7], window=3, baseline=0.7, margin=0.05)
    ck(flat["warn"] is False, "추이: 평탄 → 무경고")

    # 6) 단발 노이즈(비연속 하락) → warn 아님 (오탐 무시권)
    noisy = track_trend([0.8, 0.6, 0.8, 0.6, 0.7], window=3, baseline=0.75, margin=0.05)
    ck(noisy["warn"] is False, "추이: 단발 노이즈(비연속) → warn 아님(오탐 완충)")

    # 7) 표본 부족 → 관찰만, warn 아님
    few = track_trend([0.5, 0.4], window=3)
    ck(few["warn"] is False and "표본 부족" in few["note"], "추이: 표본 부족 → 관찰만")

    # 8) baseline 자동 산출(None) — 앞부분 평균 기준
    auto = track_trend([0.9, 0.9, 0.9, 0.4, 0.3, 0.2], window=3, baseline=None, margin=0.05)
    ck(auto["warn"] is True and auto["baseline"] == 0.9, "추이: baseline 자동(앞부분 평균=0.9) → 하락 warn")

    # 9) 경고만 — 반환에 행동/부작용 키 0 (action/exec/write/block 등 없음)
    forbidden = {"action", "exec", "write", "block", "kill", "delete", "command"}
    ck(not (set(down.keys()) & forbidden), "경고만: 반환에 행동/부작용 키 0(사람 판단 신호일 뿐)")

    # 10) 결정론 — 동일 입력 2회 동일 출력(난수/시각 미사용)
    a = track_trend([0.9, 0.8, 0.7, 0.6], window=2, baseline=0.85)
    b = track_trend([0.9, 0.8, 0.7, 0.6], window=2, baseline=0.85)
    ck(a == b, "결정론: 동일 입력 → 동일 출력")

    print("\nGATE: %s" % ("GO" if ok[0] else "STOP"))
    return 0 if ok[0] else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("hag_meta_monitor — 메타감시망(경고만). 사용: python hag_meta_monitor.py --selftest")
    sys.exit(0)
