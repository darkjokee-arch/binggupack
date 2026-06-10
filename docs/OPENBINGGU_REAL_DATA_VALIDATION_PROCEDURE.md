> OpenBinggu is the legacy/internal codename for BingguPack.

marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# OpenBinggu 1차 배포 — 실데이터 검증 절차 (S4)

> **상태: 절차 문서(2026-06-08). docs only · 실 업로드/push 0 · 실 트리 secret/PII scan 구현(`scripts/openbinggu_public_tree_scan.py` + `doctor --tree <ROOT>`, read-only·raw 미출력).**
> 4CLI 선결 gate S4(합성 fixture 통과 ≠ 실 로컬데이터 안전) 흡수. 상세 `OPENBINGGU_FIRST_RELEASE_4CLI_SYNTHESIS.md`(internal design doc — not included in public repo) §3-A.
> 상위: [CLEAN_REPO_BOOTSTRAP](OPENBINGGU_CLEAN_REPO_BOOTSTRAP.md) · [RELEASE_PREFLIGHT_CHECKLIST](OPENBINGGU_RELEASE_PREFLIGHT_CHECKLIST.md) · [SANITIZER_POLICY_BLOCK_ONLY](OPENBINGGU_SANITIZER_POLICY_BLOCK_ONLY.md).

---

## 0. 한 줄

**합성 fixture가 GATE=GO여도 그것은 "규칙·로직이 맞다"는 뜻이지 "당신의 실제 로컬 데이터가 안전하다"는 뜻이 아니다.** 공개/업로드 전, 사용자는 **자기 로컬 데이터(공개 후보 트리)** 를 대상으로 검증을 한 번 더 돌려야 하며, 그 결과는 **raw 값 없이 count/reason_code/path_id 요약만** 본다. dirty/unknown·source pointer 문제·secret/PII가 1건이라도 있으면 **GitHub 공개와 OpenCrab 업로드 모두 BLOCK**이고, 사용자가 요약을 보고 **수동 승인하기 전에는 push/upload 금지**다.

---

## 1. 합성 통과 ≠ 실데이터 안전 (왜 별도 검증인가)

| 구분 | 합성 fixture selftest | 실데이터 검증(본 절차) |
|---|---|---|
| 대상 | synthetic/temp 고정 입력 | 사용자 **자기 로컬** 공개 후보 트리 |
| 보장 | gate/규칙/로직이 의도대로 작동 | 이 사용자의 실제 파일에 노출이 없는지 |
| GATE=GO 의미 | "검사기가 맞다" | "이 트리 기준 추가 노출 미검출" |
| 한계 | 사용자 실 데이터 미검증 | 검사 범위·시점 한정(완전 보장 아님) |

> 표현 규율: "안전 완성"·"유출 0" 금지 → **"현재 트리/시점 기준 추가 노출 미검출"** 만.

---

## 2. 실데이터 검증 절차 (공개/업로드 전, 사용자 로컬에서만)

> 이 절차는 **사용자 자기 머신에서만** 수행한다. 작성자/운영자가 사용자 데이터를 대신 스캔하지 않는다.

1. **clean repo 후보 트리 선택** — [CLEAN_REPO_BOOTSTRAP](OPENBINGGU_CLEAN_REPO_BOOTSTRAP.md)에 따라 새 clean 트리에 공개 대상만 복사(실 그래프/DB/.env/credential 미복사).
2. **doctor 실행** — `python scripts/openbinggu_doctor.py --selftest` → 6 selftest + secret/PII scan + operating store 가드. 요약만(PASS/FAIL·reason_code·count), raw 미출력.
3. **public tree secret/PII scan 실행** — 공개 후보 트리 전체 대상(현재 doctor는 **stub**, 실 트리 스캔은 §3 참조). 검출 시 **count·reason_code·path_id만**, raw 값 0.
4. **source pointer dirty/unknown count 확인** — 공개 후보 pack의 source pointer 판정([path_safety_gate]/[scope_envelope publish guard]). dirty/unknown count 집계.
5. **요약만 확인** — raw 경로/원문/secret 없이 `{reason_code: count}` + path_id 목록만.
6. **BLOCK 판정** — dirty·unknown·secret/PII·source pointer 문제 **count > 0이면 BLOCK**(GitHub 공개·OpenCrab 업로드 둘 다).
7. **수동 승인 게이트** — 모두 0(clean)일 때만, 사용자가 요약을 보고 **1회 명시 승인**. 승인 전 push/upload 금지·자동/일괄 금지·이전 승인 재사용 금지.

> GitHub 공개와 OpenCrab 업로드는 **동일 BLOCK 기준·동일 수동 승인 게이트**. 한쪽만 통과시키지 않는다.

---

## 3. secret_pii_scan_stub(빠른 내장) ↔ real public tree scan(실 트리)

| 항목 | `secret_pii_scan_stub`(내장 빠른 검사) | **real public tree scan**(실 트리) |
|---|---|---|
| 도구 | doctor 내장 합성 샘플 | `scripts/openbinggu_public_tree_scan.py` + `doctor --tree <ROOT>` |
| 입력 | 합성 clean/dirty 샘플(고정) | 사용자 공개 후보 **트리 실제 파일** |
| 목적 | 검출 로직 생존 확인(clean fp 0 + dirty 검출>0) | 실 트리에 secret/PII/private path 잔존 0 확인 |
| 검출 | secret_kv·aws_key·phone·dotenv | secret_kv·aws_key·github_token·private_key_block·pii_rrn·pii_phone + 경로(path_dotenv·credential·private_key·cert_npki) |
| 출력 | reason_code/count(raw 0) | reason_code/count/**file_id**/where(L번호·path), raw 경로/내용 0 |
| 제외 연동 | — | `.gitignore` 계열 ignore_globs(공개 제외 경로 skip) |
| 상태 | ✅ 구현(내장) | ✅ **구현**(scanner selftest 3/3 GATE=GO, doctor --tree clean→GATE=GO / dirty→BLOCK NO-GO) |
| 한계 | 사용자 실데이터 미검증 | 스캔 범위·시점·패턴 한정(완전 보장 아님) |

> 사용 방법: `python scripts/openbinggu_doctor.py --tree <공개_후보_트리>` → real_tree_scan 항목이 추가되어, 검출 1건 이상이면 **verdict=BLOCK / GATE=NO-GO / exit 1**. 요약(count·reason_code·file_id)만 출력, raw 경로/내용/secret 미출력.

---

## 4. 상태 / 판정

- S4 = **절차 docs 정의 완료 + 실 트리 secret/PII 스캐너 결선 완료**(`openbinggu_public_tree_scan.py` + `doctor --tree`, read-only·raw 미출력). scanner selftest 3/3 GATE=GO, doctor 9/9 GATE=GO.
- GitHub 공개·OpenCrab 업로드 = dirty/unknown/secret/PII count>0 → BLOCK, 수동 승인 전 금지(공통).

## 5. 안전

docs only. 실 OpenCrab 업로드·GitHub push·production/OpenCrab store 자동 write/apply/ingest·DB/bid-engine write·enum·team_paid·marketplace·sanitizer 자동치환·raw PII/secret/private path 출력 0. operating store mtime 불변.
