# PyPI / Distribution Readiness (Lane E)

> **상태: READY FOR LOCAL PACKAGE INSTALL TEST.** PyPI publish·token 사용·external secret = 0 (금지 준수).

## 점검 결과

| 항목 | 상태 | 비고 |
|---|---|---|
| `pyproject.toml` 존재 | ✅ | `[project]` 메타 있음 |
| `name` | ✅ | `binggupack` |
| `version` | ✅ | `1.16.0` — `binggupack.__about__` / `pyproject.toml` 일치 |
| `description` / `requires-python` | ✅ | `>=3.10`, stdlib only(dependencies=[]) |
| `license` metadata | ⚠️ | `[project]`에 license 필드 없음(LICENSE 파일은 MIT 존재) → `license = {text="MIT"}` 또는 SPDX 표기 권장 |
| `[build-system]` 섹션 | ✅ | `setuptools>=61` + `setuptools.build_meta` |
| packages 정의 | ✅ | `binggupack*` + CLI 호환용 `scripts`/`hooks` 번들 |
| console_scripts | ✅ | `binggu = "binggu:main"`, `binggupack = "binggu:main"` |
| README long_description | ✅ | `readme = "README.md"` |
| build 도구 | ✅ | 격리 venv `pip install .`로 build/install 검증 |
| local install 가능성 | ✅ | `tests/package_cli_selftest.py` 10/10 PASS |

## package install 검증 결과

- `python tests/package_cli_selftest.py` → 격리 venv에서 `pip install .`
- `binggu --help` PASS
- `python -m binggupack --help` PASS
- `binggu start --no-capture` PASS
- `binggu remember ...` PASS
- `binggu doctor` PASS
- `ledger.sqlite` 생성 확인 PASS

## 남은 주의점

1. 실제 PyPI publish는 별도 owner 승인 전까지 미수행.
2. `scripts/` 정본을 wheel에 함께 싣는 호환 단계다. 장기적으로는 scripts 로직을 `binggupack.*` 패키지 내부로 더 이관한다.

## READY로 가기 위한 단계 (승인 후)

1. Lane B 모듈화로 `binggupack/` 패키지 구성.
2. `pyproject.toml` 보강: `[build-system]`, `version` 정정, `license`, `readme`, `[project.scripts]`.
3. 격리 venv에 `build`·`setuptools`·`wheel` 설치 후 `python -m build`로 sdist/wheel 생성 검증.
4. `pip install dist/*.whl` 격리 환경 설치 + `binggu --help` smoke.
5. (실제 PyPI publish는 그 이후 별도 owner 승인 — 이번 범위 아님.)

## 보강 적용 (v1.11.0 groundwork)

`pyproject.toml` **실제 보강 완료** (TOML 파싱 검증 OK):
- `[build-system]` 추가 — `requires=["setuptools>=61"]`, `build-backend="setuptools.build_meta"` (PEP517 build 가능 상태).
- `version` `1.10.0rc1` → **`1.11.0`** (`binggupack/__about__.py`와 일치. pre-release 검증 당시에는 `1.11.0.dev0`, release 시 `1.11.0` 확정).
- `license = {text="MIT"}`, `readme="README.md"`, `authors`, `keywords`, `classifiers` 추가.
- `[project.scripts]` `binggu-interactive-save = "binggupack.cli.interactive_save:main"` (entry import 검증 OK).
- `[tool.setuptools.packages.find]` `include=["binggupack*"]` — flat-layout(top-level `binggu.py`/`scripts`) 충돌 회피.

남은 blocker:
- 로컬에 `build`/`setuptools`/`wheel` 미설치 → **실 build NOT RUN**(설치 금지 준수). 격리 venv에서 `python -m build` 검증은 다음 단계.
- Lane B 모듈화 완료도(전 스크립트 이관)에 따라 packages 범위 확정 필요.

## 판정

**Lane E = 로컬 패키지 설치·CLI 진입점 READY.** publish/token/secret 0. 실제 PyPI 배포는 별도 승인 단계.
