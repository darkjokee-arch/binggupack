# 프로젝트 스킬 — ponytail (2026-09-03 실험 후 도입)

## 무엇이 들어왔나

`ponytail-review` · `ponytail-audit` **두 개뿐**. 둘 다 **읽고 목록만 내는** 스킬이다.
빙구팩 코드에는 아무것도 안 닿는다 — 운영홈(`~/.binggupack/`)도 건드리지 않는다.

- 원본: https://github.com/DietrichGebert/ponytail **v4.9.0** (커밋 `2ed6c52`, MIT)
- 각 `SKILL.md` = upstream 원문 + 맨 아래 「프로젝트 로컬 보호 규칙」 절만 덧붙임

## 무엇이 **안** 들어왔나 (일부러)

- **플러그인 설치 안 함** — 훅이 모든 세션·모든 subagent 에 규칙 5.2KB 를 상시 주입하고
  `~/.claude/settings.json` 수정을 권하는 지시문을 1회 주입한다. 전역 §3-3·§8-1 과 걸린다.
- **`ponytail` 상시 모드·`ultra` 안 씀** — 2026-09-03 실측에서 상시 모드는
  창구 작업에 타입 오류를 만들었고, ultra 는 자기가 쓴 시험이 틀린 채 「끝」 선언까지 갔다.

## 이 저장소에서 특히 조심할 것

2026-09-03 실측(`ba9e581` diff · `binggupack/` 감사)에서 **가장 큰 제안 두 개가 둘 다 위험**했다.

- `binggupack/eval/paperthin.py` 226줄 **통째 삭제** 제안 — 근거가 사실과 다르다
  (「항상 BLOCK」이라 했지만 `tests/test_cognitive_patterns.py:222` 는 통과 경로를 돈다).
  이건 벤치마크 오염·정답 유출·채점자 결합을 잡는 **감사 게이트**다.
- 모듈 안 `_selftest()` **~1,700줄 삭제** 제안 — 「pytest 가 이미 있다」는 근거인데,
  wheel 에는 `tests/` 가 안 실려서 설치본 `binggu --selftest` 가 바로 이걸로 돈다
  (`binggu.py:1776-1786` 이 그 사고를 적어 뒀다).

반대로 **값어치가 확실했던 것**: `binggupack/policy/match.py` 의 `rapidfuzz` 옵셔널 의존.
없으면 `_sim()` 이 100/0 이진값이라 `SIM_T2`/`SIM_T3` Tier2·Tier3 이 기본 설치에서 통째로
죽는다 → `difflib.SequenceMatcher` 로 바꾸면 **stdlib-only 헌법에도 맞고 기능도 살아난다**.

## 쓰는 법

```
/ponytail-review   <diff 나 바꾼 파일을 가리키며>
/ponytail-audit    <디렉터리를 반드시 지정>
```

2026-09-03 실측 정확도(해강·빙구팩 실제 diff 3건 35항목):
**바로 반영 26% · 참고할 만함 34% · 헛것 31% · 위험 9%.** 목록을 그대로 믿지 말 것.

## 되돌리기

`.claude/skills/ponytail-review` 와 `.claude/skills/ponytail-audit` 두 디렉터리를 지우면 끝.
git 에 커밋돼 있지 않다(untracked).
