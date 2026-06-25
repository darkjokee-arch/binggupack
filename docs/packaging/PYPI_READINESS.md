# PyPI / Distribution Readiness (Lane E)

> **상태: NOT READY.** 준비 상태만 점검합니다. PyPI publish·token 사용·external secret = 0 (금지 준수).

## 점검 결과

| 항목 | 상태 | 비고 |
|---|---|---|
| `pyproject.toml` 존재 | ✅ | `[project]` 메타 있음 |
| `name` | ✅ | `binggupack` |
| `version` | ⚠️ **수정 필요** | 현재 `1.10.0rc1` — stable `v1.10.0`과 불일치. `1.10.0`(또는 v1.11.0 작업 시 `1.11.0`)로 정정 필요 |
| `description` / `requires-python` | ✅ | `>=3.10`, stdlib only(dependencies=[]) |
| `license` metadata | ⚠️ | `[project]`에 license 필드 없음(LICENSE 파일은 MIT 존재) → `license = {text="MIT"}` 또는 SPDX 표기 권장 |
| `[build-system]` 섹션 | ❌ **없음** | PEP517 build 불가의 핵심 요인. `requires=["setuptools>=61"]` + `build-backend` 필요 |
| packages 정의 | ❌ | 평면 구조(top-level `binggu.py` + `scripts/`). `binggupack/` 패키지 없어 자동 검출 대상 모호 → Lane B 모듈화 선행 필요 |
| console_scripts | ❌ | `[project.scripts]` 없음. 모듈화 후 `binggu = binggupack.cli:main` 형태 가능 |
| README long_description | ⚠️ | `[project]`에 `readme="README.md"` 미지정 → 추가 권장 |
| build 도구 | ❌ | 로컬에 `build`/`setuptools`/`wheel` 미설치 → wheel build **실행 불가** |
| local install 가능성 | ⚠️ | 현재는 `git clone` + 스크립트 직접 실행 방식. `pip install .`는 build-system·packages 정의 후 가능 |

## wheel build 시도 결과

- `python -m build` → 도구 없음
- `import setuptools, wheel` → 실패
- `[build-system]` 섹션 부재 → PEP517 빌드 자체가 불가
- **결론: 실제 build 실행 불가. readiness 점검만 수행.**

## NOT READY 사유 (blocker)

1. `[build-system]` 섹션 없음 — PEP517 build 불가.
2. `version`이 `1.10.0rc1`로 stable과 불일치.
3. `binggupack/` 패키지 미존재 — packages 검출/모듈화 선행 필요(Lane B).
4. build 도구 미설치.

## READY로 가기 위한 단계 (승인 후)

1. Lane B 모듈화로 `binggupack/` 패키지 구성.
2. `pyproject.toml` 보강: `[build-system]`, `version` 정정, `license`, `readme`, `[project.scripts]`.
3. 격리 venv에 `build`·`setuptools`·`wheel` 설치 후 `python -m build`로 sdist/wheel 생성 검증.
4. `pip install dist/*.whl` 격리 환경 설치 + `binggu --help` smoke.
5. (실제 PyPI publish는 그 이후 별도 owner 승인 — 이번 범위 아님.)

## 보강 적용 (v1.11.0 groundwork)

`pyproject.toml` **실제 보강 완료** (TOML 파싱 검증 OK):
- `[build-system]` 추가 — `requires=["setuptools>=61"]`, `build-backend="setuptools.build_meta"` (PEP517 build 가능 상태).
- `version` `1.10.0rc1` → **`1.11.0.dev0`** (`binggupack/__about__.py`와 일치).
- `license = {text="MIT"}`, `readme="README.md"`, `authors`, `keywords`, `classifiers` 추가.
- `[project.scripts]` `binggu-interactive-save = "binggupack.cli.interactive_save:main"` (entry import 검증 OK).
- `[tool.setuptools.packages.find]` `include=["binggupack*"]` — flat-layout(top-level `binggu.py`/`scripts`) 충돌 회피.

남은 blocker:
- 로컬에 `build`/`setuptools`/`wheel` 미설치 → **실 build NOT RUN**(설치 금지 준수). 격리 venv에서 `python -m build` 검증은 다음 단계.
- Lane B 모듈화 완료도(전 스크립트 이관)에 따라 packages 범위 확정 필요.

## 판정

**Lane E = pyproject 보강 적용, build readiness 크게 개선 / 실 build NOT RUN.** publish/token/secret 0. 격리 venv build 검증은 다음 단계.
