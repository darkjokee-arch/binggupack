# SAVE Gate S4 — Parallel Audit Checklist

> **기준 HEAD:** `f642fd1` (`f642fd1509474dd41f06cf76ef4e253aea5eaa7c`, branch `main`)
> **문서 성격:** audit / **docs-only**. 코드·테스트 무수정.
> **상태:** **S4 implementation HOLD** — 본 문서는 gate-critical 안전망 회귀를 *재현·검증* 하기 위한 체크리스트일 뿐, 어떤 구현/저장/푸시도 트리거하지 않는다.
> **작성:** Worker D (S4 병렬 작업). 명령은 *문서화만* 했으며 본 작성 과정에서 실제 selftest를 실행하지 않았다(파일 존재만 Glob/Read로 확인).

---

## 0. 목적과 범위

이 문서는 누구나 HEAD `f642fd1` 에서 **gate-critical 안전망의 전체 회귀**를 동일하게 재현하고, merge 전후를 비교해 "docs/tests-only 인지, production·gate-critical을 건드렸는지" 를 기계적으로 판정하도록 한다.

- **검증 대상 selftest 6종** (§1)
- **merge 전후 비교 체크리스트** (§2)
- **STOP 조건** (§3)
- **최종 판정 라벨 템플릿** (§4)

전제: 모든 selftest 는 **temp/synthetic DB** 위에서 동작하며 운영 store(`~/.binggupack`)를 **불변** 으로 유지한다. 회귀 실행은 production write 0 이어야 한다.

---

## 1. 회귀 실행 — 명령 + 기대 카운트

### 1-1. 일괄 실행 블록 (bash)

리포 루트(`C:\Users\PC\BingguPack`)에서 실행:

```bash
cd /c/Users/PC/BingguPack/scripts

python openbinggu_staging_write_selftest.py --selftest
python openbinggu_conversation_candidate_save.py --selftest
python openbinggu_deprecate_and_remind_g3.py --selftest
python binggu_capture_to_save.py
python binggu_save_gate.py --selftest
python openbinggu_s4_gap_characterization_selftest.py --selftest
```

한 줄(연쇄, 하나라도 실패 시 즉시 중단·비0 종료):

```bash
cd /c/Users/PC/BingguPack/scripts && \
python openbinggu_staging_write_selftest.py --selftest && \
python openbinggu_conversation_candidate_save.py --selftest && \
python openbinggu_deprecate_and_remind_g3.py --selftest && \
python binggu_capture_to_save.py && \
python binggu_save_gate.py --selftest && \
python openbinggu_s4_gap_characterization_selftest.py --selftest && \
echo "ALL_SELFTESTS_GREEN"
```

> 비고: `binggu_capture_to_save.py` 는 인자 없이 실행하면 selftest 가 기본 동작(`--selftest` 불필요). 나머지 5종은 `--selftest` 플래그 사용. 모든 selftest 는 PASS=GO 시 exit 0, 실패 시 비0.

### 1-2. selftest별 기대 카운트 표

| # | selftest 스크립트 | 실행 명령 | 기대 RESULT | 기대 GATE | exit |
|---|---|---|---|---|---|
| 1 | `openbinggu_staging_write_selftest.py` | `python openbinggu_staging_write_selftest.py --selftest` | **16/16 PASS** | **GO** | 0 |
| 2 | `openbinggu_conversation_candidate_save.py` | `python openbinggu_conversation_candidate_save.py --selftest` | **13/13 PASS** | **GO** | 0 |
| 3 | `openbinggu_deprecate_and_remind_g3.py` | `python openbinggu_deprecate_and_remind_g3.py --selftest` | **23/23 PASS** | **GO** | 0 |
| 4 | `binggu_capture_to_save.py` | `python binggu_capture_to_save.py` | (orchestrator) | **GATE=GO** | 0 |
| 5 | `binggu_save_gate.py` | `python binggu_save_gate.py --selftest` | **28/28 PASS** | **GO** | 0 |
| 6 | `openbinggu_s4_gap_characterization_selftest.py` | `python openbinggu_s4_gap_characterization_selftest.py --selftest` | **≥20/20 PASS** (확장 시 갱신) | **GO** | 0 |

> **#6 카운트 주의:** 현재 HEAD `f642fd1` 기준 **20/20**. Worker B 가 저위험 GAP(예: A2·E1b·B5·F2~F4·D11 계열)을 tests-only 로 추가 pin 하면 분모/분자가 함께 증가한다. 판정 기준은 **"≥20/20 이고 GATE GO"** 로 보고, 실제 숫자는 확장 시 본 표를 **갱신**한다(예: 20→27).

> **GATE 출력 표기 차이:** #1·#3·#6 은 실패 시 `GATE: NO-GO`, #2 도 `NO-GO`, **#5(`binggu_save_gate.py`)는 실패 시 `GATE: BLOCK`** 으로 출력한다(성공은 모두 `GO`). 카운트 불일치든 store 변경이든 GATE 가 `GO` 가 아니면 회귀 실패로 본다.

### 1-3. 전체 회귀 GREEN 기준선 (한 줄 요약)

```
staging 16/16 · candidate 13/13 · deprecate 23/23 · capture GATE=GO · save_gate 28/28 · S4 GAP ≥20/20  →  ALL GREEN · GATE GO · operating_store_unchanged=True · production/gate-critical touch 0
```

---

## 2. merge 전후 비교 체크리스트

merge(또는 PR 반영) **직전/직후** 동일하게 수행. 모든 항목이 기대값과 일치해야 통과.

### 2-1. 작업 트리 상태

```bash
cd /c/Users/PC/BingguPack
git status --short
```

- [ ] 스테이징/변경된 tracked 파일이 **docs 또는 tests-only** 범위 안에만 존재.
- [ ] 의도하지 않은 untracked 산출물(예: `logs/`, `_backup/`, `__pycache__/`, temp DB) 이 커밋 대상에 끼지 않음.

### 2-2. tracked diff stat

```bash
git diff --stat HEAD          # 워킹트리 vs HEAD
git diff --stat --staged      # 스테이징 vs HEAD
# merge 검토 시: git diff --stat <base>..<head>
```

- [ ] 변경 파일 경로가 전부 `docs/**` 또는 `tests/**` (또는 명시적으로 합의된 tests-only selftest) 이내.
- [ ] `scripts/**`, `binggupack/**`, `hosted/**`, `hooks/**`, `binggu.py`, `pyproject.toml` 등 **production/패키지 경로 변경 0**.

### 2-3. whitespace / conflict marker 검사

```bash
git diff --check
```

- [ ] 출력 없음 (trailing whitespace·conflict marker `<<<<<<<` 등 0).

### 2-4. 변경 파일이 docs 또는 tests-only 인지 (자동 판정)

```bash
git diff --name-only HEAD | grep -vE '^(docs/|tests/)' || echo "DOCS_OR_TESTS_ONLY"
```

- [ ] 출력이 `DOCS_OR_TESTS_ONLY` (= docs/tests 밖 변경 0). 다른 경로가 한 줄이라도 나오면 **즉시 STOP**(§3).

### 2-5. production · gate-critical touch 0

다음 gate-critical 함수/심볼이 정의된 production 파일이 **변경되지 않았는지** 확인. 이들은 SAVE 게이트의 핵심 의사결정 경로다.

| gate-critical 항목 | 위치(소유 모듈) | 검증 |
|---|---|---|
| `to_save` (저장 후보 추출) | capture→save 경로 | diff 0 |
| `build_save_commands` | save 명령 빌더 | diff 0 |
| `has_trigger_token` | trigger 토큰 게이트 | diff 0 |
| `save_selected` | 선택 저장 | diff 0 |
| `staging_apply` | staging write | `openbinggu_staging_write_selftest.py` (production 본체) diff 0 |
| `commit_selected` | 선택 커밋 | diff 0 |
| `deprecate_g3` | G3 deprecate 도장 | `openbinggu_deprecate_and_remind_g3.py` diff 0 |
| **G4_no_auto 3중** | 자동 저장 차단 3중 게이트 | diff 0 (약화 금지) |

```bash
# gate-critical / production 경로가 diff 에 등장하면 그 줄을 출력 (없어야 정상)
git diff --name-only HEAD | grep -E \
  'binggu_capture_to_save\.py|binggu_save_gate\.py|openbinggu_staging_write_selftest\.py|openbinggu_deprecate_and_remind_g3\.py|openbinggu_conversation_candidate_save\.py|^binggupack/|^binggu\.py$' \
  && echo "!!! GATE_CRITICAL_TOUCHED — STOP" || echo "GATE_CRITICAL_UNTOUCHED"
```

- [ ] 출력이 `GATE_CRITICAL_UNTOUCHED`.
- [ ] G4_no_auto 3중 차단 로직(자동 저장 금지)이 **약화/우회 변경 0**.

> 비고: 위 grep 의 `openbinggu_*selftest.py` / `binggu_capture_to_save.py` / `binggu_save_gate.py` 는 동시에 selftest 진입점이자 **production gate 로직 본체**다. tests-only 작업이라면 이 파일들은 **건드리지 않고** 별도 `tests/**` 에서 호출만 해야 한다.

### 2-6. binggupack/ 패키지 변경 0

```bash
git diff --name-only HEAD | grep '^binggupack/' && echo "!!! PACKAGE_TOUCHED — STOP" || echo "BINGGUPACK_UNCHANGED"
```

- [ ] 출력이 `BINGGUPACK_UNCHANGED` (= 설치 패키지 `binggupack/` 변경 0).

### 2-7. operating store(`~/.binggupack`) 미변경

selftest 는 temp/synthetic store 만 사용해야 하며, 회귀 실행 전후로 운영 store 가 불변이어야 한다.

```bash
ls -la ~/.binggupack 2>/dev/null || echo "no operating store present"
# 회귀 실행 전후 mtime / 라인 수 비교 (예: ledger·save_gate_log)
```

- [ ] `~/.binggupack` 의 ledger / `save_gate_log.jsonl` 등 핵심 파일 **mtime·라인 수 불변**.
- [ ] 각 selftest 의 `operating_store_unchanged=True` / `store_unchanged` 단언이 PASS.

---

## 3. STOP 조건 (하나라도 해당 시 즉시 중단·보고)

아래 중 **하나라도** 발생하면 merge·docs 작업을 멈추고 사장님께 보고. S4 implementation 으로 넘어가지 않는다.

1. **production 변경** — `scripts/**` 의 gate 본체, `binggupack/**`, `binggu.py`, `hosted/**`, `hooks/**`, `pyproject.toml` 등 production 경로에 tracked diff 발생.
2. **gate-critical 변경** — `to_save` · `build_save_commands` · `has_trigger_token` · `save_selected` · `staging_apply` · `commit_selected` · `deprecate_g3` · **G4_no_auto 3중** 중 어느 하나라도 touch.
3. **write 경로 변경** — staging/commit/save write 경로, store 경로 해석(resolver), 또는 운영 store 로의 실제 write 가 발생/변경.
4. **G4 약화** — 자동 저장 차단(G4_no_auto) 3중 게이트의 우회·완화·삭제·조건 약화.
5. **새 동작 요구** — docs/tests-only 범위를 벗어나 새로운 런타임 동작(기능 추가·로직 변경)이 필요해짐.
6. **DB · ledger 변화** — 운영 DB 접근, `~/.binggupack` ledger / `save_gate_log` 라인 증감, real DB write.
7. **push · tag · release 시도** — `git push`, tag 생성, GitHub release/publish 등 외부 반영 시도.

> 본 작업(Worker D)은 docs 1개 신규 작성만 허용. 위 1~7 은 전부 범위 밖이며, 발생 시 작업 무효·롤백 검토.

---

## 4. 최종 판정 라벨 템플릿

회귀(§1) + 비교 체크리스트(§2) 결과를 아래 라벨로 마킹한다. STOP 조건(§3) 미발생이 전제.

| 라벨 | 의미 | 부여 조건 |
|---|---|---|
| **PARALLEL_DOCS_GO** | docs-only 병렬 작업 통과 | 변경이 `docs/**` 한정 · §1 전체 GREEN · §2 전 항목 통과 · §3 미발생 |
| **PARALLEL_TESTS_ONLY_GO** | tests-only 병렬 작업 통과 | 변경이 `tests/**` 한정(gate 본체 무수정) · §1 전체 GREEN(해당 카운트 갱신 포함) · §2 전 항목 통과 · §3 미발생 |
| **S4_IMPLEMENTATION_HOLD** | S4 구현 보류(기본 상태) | gate-critical/production 구현 변경은 별도 승인 전까지 금지. 본 문서 작성 시점의 기본 라벨 |
| **PUSH_HOLD** | push 보류 | 부모(상위 작업자)가 커밋·푸시 담당. Worker 단계에서 `git push` 금지 |
| **TAG_RELEASE_HOLD** | tag/release 보류 | tag 생성·GitHub release·publish 는 별도 owner 승인 전까지 금지 |

### 4-1. 본 작업(Worker D, docs 신규 1개) 적용 라벨

```
PARALLEL_DOCS_GO · S4_IMPLEMENTATION_HOLD · PUSH_HOLD · TAG_RELEASE_HOLD
```

- 산출물: `docs/SAVE_GATE_S4_PARALLEL_AUDIT_CHECKLIST.md` (이 파일 하나)
- 변경 범위: docs-only. production·gate-critical·`binggupack/`·operating store touch 0.
- 커밋/푸시: 부모가 수행(Worker 는 git 미실행).

---

## 5. 부록 — 한눈 재현 절차

```bash
# 1) 기준 HEAD 확인
cd /c/Users/PC/BingguPack && git rev-parse HEAD   # 기대: f642fd1...

# 2) 전체 회귀
cd scripts && \
python openbinggu_staging_write_selftest.py --selftest && \
python openbinggu_conversation_candidate_save.py --selftest && \
python openbinggu_deprecate_and_remind_g3.py --selftest && \
python binggu_capture_to_save.py && \
python binggu_save_gate.py --selftest && \
python openbinggu_s4_gap_characterization_selftest.py --selftest && \
echo "ALL_SELFTESTS_GREEN"

# 3) merge 전후 비교
cd /c/Users/PC/BingguPack
git status --short
git diff --stat HEAD
git diff --check
git diff --name-only HEAD | grep -vE '^(docs/|tests/)' || echo "DOCS_OR_TESTS_ONLY"
git diff --name-only HEAD | grep '^binggupack/' && echo "!!! PACKAGE_TOUCHED" || echo "BINGGUPACK_UNCHANGED"

# 4) 라벨 부여 (§4)
```
