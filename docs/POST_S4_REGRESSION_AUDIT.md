# POST-S4 REGRESSION AUDIT (Lane D)

- 저장소: `C:\Users\PC\BingguPack`
- 검증 시각 기준 HEAD: `3d5363e97797be665d2ab3c92200004f32ae54e0` (== `origin/main`, branch `main`)
  - 주: 작업 지시문의 baseline `407656c` 와 실제 HEAD(`3d5363e`)가 다름. Lane A/B/C 가 이미 docs/ 에 신규 문서를 추가한 상태에서 검증 수행. 이 차이는 코드 변경이 아니라 baseline 라벨 불일치로 간주(아래 forbidden audit 에서 tracked diff = 0 확인).
- 버전 SSOT: `pyproject.toml` `1.13.0` == `binggupack/__about__.__version__` `1.13.0`
- Python: 3.14.4 (base 환경)
- 원칙: evidence-first. 모든 판정은 실제 명령 실행 결과. 실패 테스트는 그대로 기록(삭제/완화 금지).

---

## 1) 회귀 결과 최종표

| # | test | command | result | notes | verdict |
|---|------|---------|--------|-------|---------|
| 1 | smoke | `python scripts/smoke_test.py` | PASS (10/10) | G4_no_auto BLOCK 확인·actual_api_call/source_fetch/production_write=0·real_home_changed=0 | GO |
| 2 | save_gate selftest | `python scripts/binggu_save_gate.py --selftest` | PASS (28/28, GATE: GO) | gate scope==ledger scope·BINGGU_HOME 단독경로 | GO |
| 3 | parse(save indices) characterization | `python scripts/binggu_save_gate_parse_characterization_selftest.py` | PASS (GATE: GO) | pii_email_none·deterministic·output_shape | GO |
| 4 | sent_hash characterization | `python scripts/binggu_save_gate_hash_characterization_selftest.py` | PASS (GATE: GO) | pii_no_leak_in_hash·distinct_inputs_distinct_hash·save_gate_eq_package | GO |
| 5 | path_safety characterization | `python scripts/openbinggu_path_safety_characterization_selftest.py` | PASS (GATE: GO) | raw_path_not_leaked·pure_no_write | GO |
| 6 | platform selftest | `python scripts/binggu_platform_selftest.py` | PASS (40/40, GATE: GO) | 플랫폼 경로 해석 | GO |
| 7 | interactive non-TTY fail-closed | `python -m binggupack.cli.interactive_save --selftest` | PASS (8/8) | tty_fail_closed=ready·ledger_write=0·G4_bypass=0 | GO |
| 8 | classifier selftest | `python scripts/binggu_capture_classifier_selftest.py` | PASS (GATE: GO) | classify_is_pure: write 0 / network 0 / re-only | GO |
| 9 | examples parse | `python scripts/examples_synthetic_guard_selftest.py` | PASS (GATE: GO) | scanned=5 clean=5 violations=0 (synthetic pack/workflow 예제) | GO |
| 10 | version SSOT | `python scripts/version_consistency_selftest.py` | PASS (3/3, GATE: GO) | MATCH version=1.13.0 (about==pyproject)·fs_write=0·network=0 | GO |
| 11 | package import version | `python -c "import binggupack; print(binggupack.__version__)"` | PASS | 출력 `1.13.0` | GO |
| 12 | MCP tool count preservation | `import openbinggu_mcp_server_handlers` 검사 | PASS | TOOLS=8 (capture_classify, capture_preview, consumer_smoke, pack_build, pack_validate, publish_guard_dryrun, **save_candidate**, selftest)·_FORBIDDEN=11·forbidden_not_exposed=True | GO |
| 13 | tree scan (synthetic selftest) | `python scripts/openbinggu_public_tree_scan.py` | PASS (GATE: GO) | clean/dirty/ignore/skip/read_error/neg fixture 6 케이스 모두 OK·raw_value_not_leaked | GO |
| 14 | tree scan (runner mode, full repo) | `python scripts/openbinggu_public_tree_scan.py --tree . --public` | **FAIL / verdict=BLOCK (rc=1)** | 최종 hits=14 (pii_phone 9 + pii_rrn 5) = `scripts/` selftest 합성 fixture만(pre-existing). 작업 중 릴리스 문서에 들어갔던 더미 예시는 마스킹 완료 → docs 기여 0. 실 PII 0. **pre-existing Known Issue** | HOLD |
| 15 | 통합 러너 | `python scripts/binggu_publish_run_all_selftests.py` | **23/24 PASS (REGRESSION=FAIL, rc=1)** | 유일 실패 = `tree scan`(#14 동일). 나머지 23 게이트 전부 PASS | HOLD |
| 16 | build (sdist+wheel) | `python -m build` | NOT RE-RUN by Lane D | base 환경에 `build`/`twine`/`setuptools`/`wheel` 미설치(확인됨). 패키지 설치 미수행(전역 지침 §3 자동설치 금지 + 패키지설치 hook). **Lane A 가 격리 임시 venv 에서 build/twine check/clean-install/import/entry/smoke 전부 PASS** 문서화(`docs/PYPI_RELEASE_VERIFICATION.md`) | HOLD(정책) |
| 17 | twine check | `python -m twine check dist/*` | NOT RE-RUN by Lane D | 위와 동일. Lane A 결과: wheel+sdist 둘 다 PASSED | HOLD(정책) |

요약: 필수 회귀 13종(#1~#13) 전부 GO. 통합 러너는 23/24 PASS, 유일 실패는 tree-scan runner mode(#14) 이며 합성 PII fixture 탐지에 의한 **pre-existing Known Issue**. 신규 회귀 깨짐 없음.

---

## 2) tree-scan runner BLOCK 상세 (Known Issue)

- `--selftest`(합성 fixture 자체 검증)은 PASS(GATE: GO).
- 러너(`--tree . --public`)는 작업트리 전체를 스캔 → 마스킹 후 최종 `verdict=BLOCK hits=14 by_reason={pii_phone:9, pii_rrn:5}`.
- 탐지 위치(값 미출력, 디렉터리+사유+건수만):
  - `scripts/` : pii_phone x9, pii_rrn x5 (characterization selftest 의 합성 패턴 문자열) ← 잔존 전부 여기(pre-existing tracked fixture)
  - `docs/` : 0 (작업 중 릴리스 문서에 들어갔던 더미 예시는 마스킹 처리 → 기여 0)
  - `tests/fixtures/` : 위 scripts 집계에 포함된 의도된 음성 fixture
- 패턴: `pii_rrn = \b\d{6}-\d{7}\b`, `pii_phone = \b01[016789]-?\d{3,4}-?\d{4}\b`. 탐지값은 전부 명백한 더미(`010-****-****` / `######-#######`) — 실 개인정보/시크릿 0.
- 판정: release blocker 아님. 탐지기가 자기 자신의 합성 fixture 를 정상 검출하는 구조적 한계. `docs/RELEASE_NOTES_DRAFT_V1_13_X.md` §, `docs/RELEASE_READINESS_V1_13_X.md` §4 와 일치.

해소 완료(주석): 신규 릴리스/감사 문서가 Known Issue 설명용으로 더미 PII 패턴을 포함해 runner hit 이 한때 19였으나, 통합 단계에서 전부 마스킹(`010-****-****` / `######-#######`) → docs 기여 0, 최종 runner hits=14(scripts 합성 fixture pre-existing 만). forbidden #9(fixture 외 PII 포함 파일 추가) 정합. 잔존 14는 PII 탐지기 검증용 tracked selftest fixture 라 삭제 금지(#10 실패 테스트 완화 금지) → release blocker 아님으로 유지.

---

## 3) FORBIDDEN AUDIT

명령: `git diff` / `git diff --cached` / `git status --porcelain`

| 항목 | 결과 | 판정 |
|------|------|------|
| tracked 코드 파일 수정 (`git diff --stat`) | **빈 출력 (0건)** | GO |
| staged 변경 (`git diff --cached --stat`) | **빈 출력 (0건)** | GO |
| staging_apply / save_selected / commit_selected / gate_log 로직 diff | diff 자체가 0 → 변경 0 | GO |
| S4 FINAL HOLD 해제 / G4 / actor / token 로직 변경 | diff 0 → 변경 0 | GO |
| production write path 변경 / OpenCrab ingest 변경 | diff 0 → 변경 0 | GO |
| promotion_allowed=true / auto-save 동작 변경 | diff 0 → 변경 0 | GO |
| MCP save_candidate 영구저장 경로 전환 | handlers TOOLS 불변(8개, write-gated·temp DB·actor=reader 오버라이드), diff 0 | GO |
| PyPI upload(twine) / gh release create / git tag·push·commit | 미실행 | GO |
| ledger.sqlite 수정(~/.binggupack 포함) | git-tracked 아님·본 검증 모든 selftest 는 temp home(BINGGU_HOME 리다이렉트) 사용·smoke 의 real_home_changed=0 | GO |
| token / secret 추가 | 신규 파일은 docs(.md)뿐·실 secret/실 PII 0 (더미 PII 인용만) | GO |

forbidden_violations: 없음(0건).

### working tree 변경 (untracked 만)
신규 docs (Lane A/B/C + 본 Lane D):
- `docs/PYPI_RELEASE_VERIFICATION.md` (Lane A)
- `docs/RELEASE_NOTES_DRAFT_V1_13_X.md` (Lane B/C)
- `docs/RELEASE_READINESS_V1_13_X.md` (Lane B/C)
- `docs/S4_POST_STRUCTURE_REVIEW.md` (Lane B/C)
- `docs/POST_S4_REGRESSION_AUDIT.md` (Lane D, 본 문서)

기타 untracked(테스트/백업 산출물 — 코드 아님, 정리 후보):
- `_backup/`, `_binggu_health_home/`, `_binggu_test_home_laneB/`, `_fc_home/`, `logs/`, `scripts/_archived/`
- 확인: `git status --porcelain --ignored` 에서 추적 대상 `.py` 소스 수정 0. (gitignore 된 `scripts/hybrid_agi/hag_token_guard.py` 는 본 작업과 무관·기존 항목)
- Lane C 잔여 build 산출물(`dist/` / `build/` / `*.egg-info`) 없음 — 이미 정리됨.

---

## 4) 종합 판정

- **overall_verdict = HOLD**
- 근거:
  1. 필수 회귀 13종 전부 GO, 통합 러너 23/24 PASS.
  2. 유일 실패(tree-scan runner mode)는 합성 PII fixture 에 의한 **pre-existing Known Issue** — 신규 회귀 깨짐 아님.
  3. forbidden audit 전 항목 0 위반(tracked diff = 0).
  4. STOP 아님: 금지 위반·신규 회귀 모두 없음.
  5. GO 아님(HOLD 사유): (a) S4 FINAL HOLD 는 설계상 유지, (b) tree-scan BLOCK 잔존(Known Issue), (c) 실제 PyPI/release publish 는 owner 승인 전 항상 HOLD.
- 권고: 릴리스 실행 머신에서 build/twine/setuptools/wheel 셋업 후 packaging 게이트 재현(Lane A 가 임시 venv 로 이미 PASS 확인). 문서 내 더미 PII 마스킹은 선택 사항.
