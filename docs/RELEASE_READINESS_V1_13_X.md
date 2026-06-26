# BingguPack v1.13.x — Release Readiness (Lane B)

> 작성일: 2026-06-26 · baseline `HEAD == origin/main == 407656c` · version `1.13.0`
> 본 문서는 **검증 기록 + drift 점검 + 릴리스 문안 초안**이다. 코드/문서 본체는 수정하지 않았다(신규 문서 2개만 생성).
> 모든 판정은 실제 명령 실행 결과로만 기록한다(evidence-first).

---

## 1. Version SSOT (단일 진실 출처) — 실측

| 위치 | 값 | 비고 |
|---|---|---|
| `binggupack/__about__.py` `__version__` | `1.13.0` | SSOT |
| `pyproject.toml` `[project].version` | `1.13.0` | 일치 |
| `README.md` (현재 stable 표기) | `v1.13.0` | 일치 |
| `CHANGELOG.md` (최상단 항목) | `v1.13.0 — 자기진화 거버넌스 (2026-06-26)` | 일치 |
| 빌드 산출물 메타데이터 | `binggupack-1.13.0` (wheel/sdist) | 일치 |

**version_consistency_selftest 결과:**
```
RESULT: 3/3 PASS
fs_write=0  network=0  parser=tomllib
MATCH: version=1.13.0
GATE: GO
```

판정: 코드 SSOT(version 파일·README·CHANGELOG·빌드 메타) **전부 1.13.0 일치**.

---

## 2. Drift 목록 (수정 제안만 — 본체 미수정)

### DRIFT-1 (실질) — `INSTALL.md` 가 v1.12.0 에 멈춰 있음

실측(`grep -n -i version INSTALL.md`):
```
1:# Install BingguPack v1.12.0
3:> 최신: `v1.12.0` (화자 축 — 내 말/AI 요약 따로 쌓기 + 양방향 신뢰도). ...
124:## 화자 축 사용 (v1.12.0)
```
README/CHANGELOG/version 파일은 1.13.0 인데 INSTALL.md 만 1.12.0. 사용자가 설치 문서를 먼저 볼 경우 구버전으로 오인 가능.

**제안 patch (최소 diff, 이번엔 적용하지 않음):**
```diff
-# Install BingguPack v1.12.0
+# Install BingguPack v1.13.0
@@ line 3
-> 최신: `v1.12.0` (화자 축 — 내 말/AI 요약 따로 쌓기 + 양방향 신뢰도). 아래 명령 그대로 사용하세요. PyPI publish는 아직 하지 않으므로 `git clone` 설치만 지원합니다.
+> 최신: `v1.13.0` (자기진화 거버넌스 — 학습↔규칙 충돌 시 양쪽 제시·규칙 변경은 사람·안전 규칙 불변). 아래 명령 그대로 사용하세요. PyPI publish는 아직 하지 않으므로 `git clone` 설치만 지원합니다.
```
주: line 124 "화자 축 사용 (v1.12.0)" 섹션은 **기능 섹션 제목**이므로 그대로 두거나 "(v1.12.0 도입)"으로 명확화하는 정도면 충분(거버넌스 섹션 추가는 owner 판단).

### DRIFT-2 (경미) — `pyproject.toml` Release URL 이 v1.10.0 고정

실측(`pyproject.toml` 24행):
```toml
[project.urls]
Release = "https://github.com/darkjokee-arch/binggupack/releases/tag/v1.10.0"
```
README 의 Release 링크는 `tag/v1.13.0` 인데 패키지 메타데이터는 `tag/v1.10.0`. 빌드/배포 시 PyPI 메타에 구 태그가 박힘.

**제안 patch (최소 diff, 이번엔 적용하지 않음):**
```diff
-Release = "https://github.com/darkjokee-arch/binggupack/releases/tag/v1.10.0"
+Release = "https://github.com/darkjokee-arch/binggupack/releases/tag/v1.13.0"
```

### Drift 없음 확인
- README current stable 표기 = v1.13.0 (line 5·8) — OK
- CHANGELOG 최상단 = v1.13.0 — OK
- `__about__.__version__` == `pyproject [project].version` — OK

---

## 3. 사용자 관점 변경 설명

### 3-1. 크래시 수정 (commit `407656c`)

**무엇이 바뀌나(일상 언어):** 빙구팩을 Claude Code 에 MCP 로 붙여 쓸 때, "후보 저장(save_candidate)" 도구가 **실제 저장 시도** 단계에서 빙구팩 서버가 갑자기 죽어 "Connection closed" 가 뜨던 문제를 고쳤다. 이제 같은 입력에서도 서버가 죽지 않고 정상 응답한다.

**기술 요약(근거 `git show 407656c`):**
- 원인: `_u_save_candidate` 가 snapshot 대상 임시 폴더(`snap_dir`)를 만들지 않아, 실 write(`dry_run=False`) 경로의 `staging_apply → db.snapshot → shutil.copy2` 에서 `FileNotFoundError`. `handle_jsonrpc` 에 try/except 가 없어 예외가 `serve_stdio` 루프를 종료 → 프로세스 사망.
- dry-run / 조기 BLOCK 경로는 snapshot 도달 전에 반환되므로 영향 없었고, **실 write 경로만** 죽던 패턴과 정확히 일치.
- 수정: 핸들러 1줄. `scripts/openbinggu_mcp_server_handlers.py:128`
  ```python
  os.makedirs(snap_dir, exist_ok=True)  # snapshot 복사 대상 폴더 보장(없으면 FileNotFoundError)
  ```
- write core(`save_selected`/`staging_apply`/`commit_selected`)·gate_log·G4·actor/confirm/token·ledger write path **전부 미접촉**. version 불변(1.13.0).
- 변경 규모: `git show --stat` = 1 file changed, 1 insertion(+).

### 3-2. 저장 경로 명확화 (MCP save_candidate = 임시 전용 / 영구저장 = 사람)

**무엇이 바뀌나(일상 언어):** "AI 가 MCP 로 내 노트에 마음대로 저장하는 일"은 구조적으로 불가능하다. Claude Code 의 MCP `save_candidate` 도구는 **임시(temp)** 까지만 닿고, **영구 저장은 사람이 직접 고를 때만** 일어난다.

**근거(코드 실측 `scripts/openbinggu_mcp_server_handlers.py`):**
- MCP 입력의 `actor` 를 신뢰하지 않고 서버가 `reader` 로 **하드 오버라이드**(96~98행): "MCP 경유 호출은 정의상 사람 직접발화가 아님".
- 따라서 `dry_run=False` + `confirm` 정확일치까지 통과해도, `save_selected` 내부 게이트가 `actor=reader` 를 보고 **항상 `G4_no_auto` 로 BLOCK**(135~136행).
- 실 write 도 temp DB 대상이며 결과의 `ledger` 필드는 `"temp_only"`(142행). 경로 입력(`ledger_path` 등)은 일절 무시 — **MCP 는 운영 ledger 를 열 수 없음**(163행).
- 즉 영구 저장(운영 ledger 반영)은 사람이 직접 선택(`actor=human`)으로 `commit_selected`/`save_selected` 를 통과시킬 때만. **MCP 단독으로는 영구 저장 0.**

이는 README 의 헌법적 약속("자동 저장 없음 · 내가 고른 것만 저장 · AI는 저장 못 함")과 코드가 일치함을 보여준다.

---

## 4. Known Issue — master runner 23/24 (tree-scan FAIL)

**판정: pre-existing(기존 이슈) · 실 secret/PII 유출 아님 · 릴리스 blocker 아님.**

**실측:**
- 마스터 러너 `scripts/binggu_publish_run_all_selftests.py` 실행 → `SUMMARY: 23/24 PASS`, 유일 실패 = `tree scan` (rc=1). 나머지 23 게이트 전부 PASS.
- 24번째 게이트 정의(러너 GATES 마지막 항목):
  ```python
  ("tree scan", "openbinggu_public_tree_scan.py", ["--tree", REPO, "--public"], "verdict=CLEAN"),
  ```
- tree scan 단독 실행:
  ```
  python scripts/openbinggu_public_tree_scan.py --tree . --public
  scanned=1108 skipped_ignored=1841 hits=14 verdict=BLOCK by_reason={'pii_phone': 9, 'pii_rrn': 5}
  ```

**근거 — 14건은 전부 tracked selftest 의 합성(synthetic) PII fixture:**
- 패턴 출처 파일(`grep` 으로 합성 패턴 검출, 전부 `git ls-files` 추적됨):
  - `scripts/binggu_capture_classifier_selftest.py`
  - `scripts/binggu_capture_buffer_selftest.py` / `..._session_selftest.py` / `..._cli_selftest.py`
  - `scripts/binggu_save_gate_parse_characterization_selftest.py` / `..._hash_..._selftest.py`
  - `scripts/examples_synthetic_guard_selftest.py`
  - `scripts/watcher_batch_m1.py`
- 검출된 실제 값(합성 placeholder):
  ```
  010-****-****       (pii_phone — 더미 형태, 본 문서에선 마스킹)
  ######-#######      (pii_rrn  — 더미 형태, 본 문서에선 마스킹)
  ```
  (실제 fixture의 합성 패턴은 위 형태이며, 이 문서에는 PII-shape 패턴을 넣지 않기 위해 마스킹함)
- 즉 PII 탐지기(`openbinggu_public_tree_scan.py`)가 **자기 자신의 테스트 fixture**(탐지기가 잡으라고 일부러 넣은 합성 PII)를 정상 검출 → `verdict=BLOCK`. 이는 탐지기가 **의도대로 동작**하는 것이며, 실제 개인정보/시크릿 유출이 아니다.

**왜 release blocker 아님:**
1. 검출값이 전부 전화/주민번호 형태의 명백한 합성 더미 패턴 — 실 PII 0.
2. 출처가 PII 탐지기 검증용 selftest fixture(코드 정상 자산) — 삭제하면 오히려 탐지기 테스트 커버리지가 깨짐(금지 §9: 실패 테스트 완화 금지).
3. tree-scan 게이트는 "공개 트리(공개 대상 파일)"가 깨끗한지 보는 용도인데, 마스터 러너가 `--tree REPO` 로 **selftest fixture 포함 전체 repo** 를 스캔하기 때문에 발생하는 구조적 false-positive.
4. baseline(`407656c`)에서 변경 없이도 재현 — crash fix 와 무관한 pre-existing 상태.

**개선 제안(owner 판단, 이번엔 미적용):** 마스터 러너의 tree-scan 게이트가 selftest fixture 디렉터리(또는 `*_selftest.py` 합성 fixture)를 `ignore_globs` 로 제외하도록 인자를 조정하거나, 별도 "공개 번들 트리"만 스캔 대상으로 좁히면 24/24 가 된다. 단 이는 코드/러너 변경이므로 본 lane 범위 밖.

---

## 5. 빌드/패키징 검증 (격리 venv, 실행 결과)

| 검증 | 명령 | 결과 |
|---|---|---|
| sdist+wheel 빌드 | `python -m build` (temp venv) | `Successfully built binggupack-1.13.0.tar.gz and binggupack-1.13.0-py3-none-any.whl` |
| 메타데이터 점검 | `twine check dist/*` | wheel **PASSED** · sdist **PASSED** (업로드 안 함) |
| 설치+import | `pip install <wheel>` → `import binggupack` | `import OK version= 1.13.0` |
| smoke (격리 home) | `smoke_test.py --home ./_binggu_test_home_laneB` | `RESULT: PASS` (10/10) · actual_api_call 0 · production_write 0 · `G4_no_auto confirmed` · `real_home_changed 0` |
| version SSOT | `version_consistency_selftest.py` | `3/3 PASS · GATE: GO` |
| 회귀 묶음 | `binggu_publish_run_all_selftests.py` | 23/24 PASS (tree-scan만 §4 사유로 FAIL) |

주: `twine check` 는 메타데이터 정합성 점검일 뿐 **업로드(twine upload) 미수행**. PyPI publish/태그/릴리스 생성 모두 미수행(owner 영역).

---

## 6. 릴리스 준비 종합 판정

- version SSOT 1.13.0 정합(코드 자산 기준) — **GO**
- 빌드/twine check/설치-import — **GO**
- crash fix(407656c) 검증·범위 한정 확인 — **GO**
- 저장 경로(MCP=temp_only / 영구=사람) 코드 정합 — **GO**
- 잔여 정리(릴리스 전 owner 적용 권장): DRIFT-1(INSTALL.md), DRIFT-2(pyproject Release URL)
- 정보용 known issue: master runner 23/24 tree-scan(synthetic fixture false-positive, blocker 아님)

**HOLD/STOP 사유 없음.** 단, 본 lane 은 문서만 생성했고 커밋/태그/푸시/publish 는 수행하지 않았다. DRIFT-1/2 patch 적용 및 릴리스 실행은 owner 결정.

---

## 부록 A. 재현 명령 모음

```bash
git rev-parse HEAD                                   # 407656c
git show 407656c --stat                              # 1 file, 1 insertion
python scripts/version_consistency_selftest.py       # 3/3 GATE: GO
python scripts/binggu_publish_run_all_selftests.py   # 23/24
python scripts/openbinggu_public_tree_scan.py --tree . --public   # hits=14 BLOCK
python scripts/smoke_test.py --home ./_binggu_test_home_laneB     # PASS

# 격리 venv 빌드/검증 (업로드 안 함)
python -m venv <tmp>/venv
<tmp>/venv/Scripts/python -m pip install build twine
<tmp>/venv/Scripts/python -m build --outdir <tmp>/dist
<tmp>/venv/Scripts/python -m twine check <tmp>/dist/*
<tmp>/venv/Scripts/python -m pip install <tmp>/dist/binggupack-1.13.0-py3-none-any.whl
<tmp>/venv/Scripts/python -c "import binggupack, binggupack.__about__ as a; print(a.__version__)"
```
