# 분류 일치성 하네스 — 증분 운영 루프

capture(자동 판정)와 preview(미리보기) 두 경로가 **같은 SSOT 분류기**로 후보를 고르는지,
그리고 그 판정이 **사람이 정한 기준**과 맞는지 지키는 회귀 가드.

## 파일
| 파일 | 역할 |
|---|---|
| `sentences_100.json` | 문장 + 사람 라벨(`should_capture`, `category`) — **진실의 출처** |
| `golden_100.json` | 사람 기준에서 파생한 기대치(capture/label/signal). 구현 출력 스냅샷 아님 |
| `../../build_golden.py` | fixture → golden 재생성(분류기 호출 0, 규칙만) |
| `../../classifier_consistency_harness.py` | 두 경로 비교 + golden 대조 |

## 명령
```bash
python tests/classifier_consistency_harness.py                 # 일치/정확도 리포트
python tests/classifier_consistency_harness.py --assert-consistent  # 두 경로 불일치>0 → exit 1
python tests/classifier_consistency_harness.py --golden        # 사람 기준과 대조, 다르면 문장·필드 diff
python tests/classifier_consistency_harness.py --semantic-on   # semantic 켜고 비교(기본은 OFF 결정적)
python tests/build_golden.py                                   # golden 재생성
```

## 증분 루프 (실사용에서 틀린 문장이 나오면)
1. **fixture에 문장 추가** — `sentences_100.json`에 `{id, category, should_capture, text}` 1줄. 라벨은 사람이 정한다(판단/교훈/선호/규칙이면 true, 조회/지시/보고/확인/잡담/순수지식이면 false).
2. **golden 재생성** — `python tests/build_golden.py`.
3. **하네스 실행** — `--assert-consistent` 와 `--golden`. 새 문장에서 불일치/diff가 나오면 **구현이 사람 기준과 어긋난 것**.
4. **분류기 수정** — `binggupack/classifier/capture_classifier.py`의 SSOT regex(또는 게이트)를 고친다. preview/capture 별도 수정 금지(SSOT 한 곳).
5. **5종 재확인** — 하네스 + capture/preview/binggu/doctor selftest 전부 GO 후 커밋.

## 원칙
- **golden 은 무비판 덤프 금지.** `expected_*` 는 사람 라벨(`should_capture`, `category`)에서 파생한다. 구현이 내는 값을 그대로 정답으로 박으면 회귀 가드가 아니라 현재 버그를 고정하는 셈이 된다.
- **분류는 SSOT 한 곳에서만.** capture/preview 가 각자 분류하면 다시 갈라진다. 분류기는 `capture_classifier.classify()` 하나.
- **새 카테고리**를 추가하면 `build_golden.py`의 `LABEL_BY_CATEGORY`에 도장 기준을 사람이 승인해 등록한다.
