# PyPI / Packaging Release Verification — BingguPack v1.13.0

> Lane C 산출물. evidence-first: 아래 모든 결과는 실제 명령 실행으로 검증함. 추측 없음.
> **실제 publish(twine upload / PyPI / gh release / git tag)는 수행하지 않음.** build/check/install 검증만.

- 저장소: `C:\Users\PC\BingguPack`
- baseline: HEAD == origin/main == `407656c`
- 선언 버전(pyproject): `1.13.0`
- 검증 일자: 2026-06-26
- 빌드/검증 Python: **3.14.4** (base 환경)
- 빌드/twine/install은 격리 임시 venv에서 수행(검증 후 정리)

---

## 종합 판정

| 항목 | 결과 |
| :--- | :--- |
| `python -m build` (sdist+wheel) | **PASS** |
| `twine check dist/*` | **PASS** (wheel + sdist 둘 다) |
| clean venv `pip install *.whl` | **PASS** |
| `import binggupack; __version__` | **PASS** → `1.13.0` (pyproject와 일치) |
| console_scripts entry (`binggu-interactive-save --help`) | **PASS** (exit 0) |
| `scripts/smoke_test.py` | **PASS** (10/10) |
| wheel METADATA (name/version/summary) | **PASS** |

**Publish 가능 여부: HOLD**
- 기술적 packaging 게이트(build / twine check / clean-install / import / entry / smoke)는 전부 GREEN — 패키징 자체에 차단 결함 없음.
- HOLD 사유는 **정책**: 실제 PyPI publish는 owner 승인 전 항상 HOLD(상시 규칙). 추가로 base 환경에 build/twine/setuptools/wheel 부재(아래 Known Issues) — 릴리스 머신에서 재현 셋업 필요.

---

## 1) build — sdist + wheel

base 환경에는 `build`/`twine`/`setuptools`/`wheel` 모두 미설치 → `python -m pip show <pkg>` 로 확인됨(Known Issues 참조).
격리 임시 venv에 `build 1.5.0` 설치 후 빌드 실행.

```
$ python -m build
Successfully built binggupack-1.13.0.tar.gz and binggupack-1.13.0-py3-none-any.whl
```

생성 산출물(`dist/`, untracked):

```
binggupack-1.13.0-py3-none-any.whl   56,353 bytes
binggupack-1.13.0.tar.gz             47,173 bytes
```

> `dist/`, `build/`, `*.egg-info` 는 빌드 부산물(untracked). 검증 후 정리함(tracked 파일 무수정).
> `.gitignore` 에 `dist/` 등록됨. `binggupack.egg-info/` 는 미등록(빌드 시 생성·정리 대상).

### sdist 구성(주요)
```
binggupack-1.13.0/pyproject.toml
binggupack-1.13.0/setup.cfg
binggupack-1.13.0/README.md
binggupack-1.13.0/LICENSE
binggupack-1.13.0/PKG-INFO
binggupack-1.13.0/binggupack/__about__.py, __init__.py
binggupack-1.13.0/binggupack/{capture,classifier,cli,mcp,pack,policy,review,safety,schema,workspace}/...
binggupack-1.13.0/binggupack.egg-info/{PKG-INFO,SOURCES.txt,entry_points.txt,top_level.txt,dependency_links.txt}
```

---

## 2) twine check

격리 venv에 `twine 6.2.0` 설치 후 실행.

```
$ python -m twine check dist/*
Checking dist/binggupack-1.13.0-py3-none-any.whl: PASSED
Checking dist/binggupack-1.13.0.tar.gz: PASSED
```

long_description(README, `text/markdown`) 렌더 검증 통과. metadata 결함 없음.

---

## 3) clean venv install + import + version

별도의 깨끗한 임시 venv 생성 → wheel 설치(저장소 의존성 0, stdlib only):

```
$ pip install dist/binggupack-1.13.0-py3-none-any.whl
Successfully installed binggupack-1.13.0

$ python -c "import binggupack; print(binggupack.__version__)"
1.13.0
```

- 외부 의존성 없이 설치 성공(`dependencies = []`).
- `binggupack.__version__` == `1.13.0` == pyproject `version` (일치).

---

## 4) console_scripts entry point / CLI help

> 주의: pyproject `[project.scripts]` 에는 `binggupack` CLI 가 **없다**. 정의된 entry 는
> `binggu-interactive-save = binggupack.cli.interactive_save:main` 하나뿐.
> (저장소 사용법은 `python binggu.py ...` / `python scripts/smoke_test.py` 로 backward-compatible 유지)

설치된 venv 의 `Scripts/` 에 `binggu-interactive-save.exe` 생성 확인. help 실행:

```
$ binggu-interactive-save --help        # exit 0
usage: ... binggu-interactive-save [-h] [--selftest]
BingguPack interactive save gate (보조 UX)
options:
  -h, --help  show this help message and exit
  --selftest  비-TTY 검증(저장 0)
```

wheel `entry_points.txt`:
```
[console_scripts]
binggu-interactive-save = binggupack.cli.interactive_save:main
```

---

## 5) smoke test

```
$ python scripts/smoke_test.py --home ./_binggu_test_home
  [PASS] 1.selftest_ALLOW
  [PASS] 2.capture_classify_ALLOW
  [PASS] 3.capture_preview_ALLOW_nothing_saved
  [PASS] 4.pack_build_dryrun_ALLOW
  [PASS] 5.pack_validate_ALLOW
  [PASS] 6.publish_guard_dryrun_ALLOW
  [PASS] 7.consumer_smoke_ALLOW
  [PASS] 8.save_dryrun_write0
  [PASS] 9.save_actual_G4_no_auto_BLOCK
  [PASS] 10.operating_ledger_write_0
  actual_api_call: 0 | source_fetch: 0 | production_write: 0
  G4_no_auto: confirmed (AI/reader actor cannot durably save)
  real_home_changed: 0 (BINGGU_HOME redirected; OPERATING_PATHS unchanged)
  RESULT: PASS
```

S4 FINAL HOLD / G4_no_auto BLOCK 등 안전 게이트 그대로 PASS(완화/변경 없음).

---

## 6) package metadata inspection (wheel METADATA)

```
Metadata-Version: 2.4
Name: binggupack
Version: 1.13.0
Summary: BingguPack — local-first, evidence-backed memory/context pack framework
         with an installable Claude Code MCP server (stdio JSON-RPC). Python stdlib only.
Author: BingguPack contributors
License: MIT
Requires-Python: >=3.10
Project-URL: Homepage, https://github.com/darkjokee-arch/binggupack
Project-URL: Release, https://github.com/darkjokee-arch/binggupack/releases/tag/v1.10.0
Keywords: mcp,claude-code,local-first,ontology,context-packs,agi-memory
Classifier: License :: OSI Approved :: MIT License
Classifier: Programming Language :: Python :: 3 / 3.10
Classifier: Operating System :: OS Independent
Description-Content-Type: text/markdown
License-File: LICENSE
top_level: binggupack
```

### 관찰(권장 — publish 차단 아님)
1. **Project-URL `Release` 가 `v1.10.0` 고정.** 현재 버전이 v1.13.0 인데 릴리스 링크는 v1.10.0 태그를 가리킴. publish 전 갱신 권장(메타데이터 정확성). *코드 변경 금지 lane 이라 본 lane 에서는 수정하지 않음.*
2. **classifiers 에 Python 3.10 만 명시.** 실제 빌드/검증은 3.14 에서도 정상. 지원 매트릭스 확장 시 classifier 추가 고려(선택).
3. **CLI 명령은 `binggu-interactive-save` 단일.** `binggupack` 실행파일은 제공되지 않음 — 문서/온보딩에서 혼동 주의.

---

## Known Issues (정직 기록 — 완화하지 않음)

1. **base 환경에 packaging 툴체인 부재.** `python -m pip show build|twine|setuptools|wheel` 전부 `Package(s) not found`. 본 검증은 격리 임시 venv 에 build/twine 을 설치해 수행함. **릴리스 실행 머신에서는 build/twine 설치 상태를 먼저 갖춰야 함.**
2. **빌드/검증 Python 이 3.14.4.** pyproject `requires-python >=3.10` 충족하나 classifier 는 3.10 만 선언. 다른 인터프리터(3.10~3.13) 에서의 빌드 재현은 본 lane 범위 밖.
3. **Project-URL Release 링크가 v1.10.0 로 stale**(위 관찰 1). publish 전 정정 필요(코드/메타 수정은 별도 lane/owner).
4. **작업 중 `binggu.py` 가 tracked-modified 상태로 관찰됨**(`--speaker` 인자 추가 diff). 본 lane 이 수정하지 않았으며(첫 `git status` 에는 없었고 세션 중 등장 — v1.12.0 화자 축 관련 병행 작업으로 추정), 본 lane 은 일절 손대지 않음. 릴리스 전 working tree clean 여부 확인 권장.

---

## Publish 절차 메모(실행 금지 — owner 승인 후 별도)

본 lane 은 아래를 **수행하지 않음**(금지):
- `twine upload` / PyPI publish
- `gh release create`
- `git tag` 생성·push, `git push`, `git commit`

owner 승인 시 릴리스 머신에서: 클린 build → `twine check` → (TestPyPI 선검증) → `twine upload`. 본 검증 결과상 packaging 게이트는 통과 상태.
