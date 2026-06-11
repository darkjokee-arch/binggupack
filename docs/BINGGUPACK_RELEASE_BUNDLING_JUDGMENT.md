# BingguPack 릴리스 번들링 판단 (2026-06-11, 판단만 — release/tag/commit/push 없음)

## 1. v0.8.1-rc1 이후 main 누적 변경 (실측, RC 트리 read-only git 조회)

- HEAD = origin/main = `4d796ae` (전부 push 완료, working tree clean, 태그만 미부착)
- 커밋 3건 / 4 files, +668 insertions:
  | 커밋 | 내용 | 구분 |
  |---|---|---|
  | `0b496f4` | v0.8: feedback resolve loop — 4-outcome verification runner (`openbinggu_v08_review_resolve_4values.py`) + 결과 문서(`OPENBINGGU_V08_RESOLVE_4VALUES_RESULT.md`) | 코드+문서 |
  | `a6e59da` | v1.0: candidate_list read-only view (filters, evidence links, no actions) | 코드 |
  | `4d796ae` | v1.0: deprecate UX — id8-bound confirm + list view id column | 코드(신규 write UX) |
- 이번 라운드 예정(미커밋): replace(수정) 구현=**코드** / accepted 재설계 r2=**코드+문서** / OpenCrab preflight fixture 스펙=**문서**

## 2. 기준 제안

**(a) v0.8.2-rc1 — "지금 push 된 것까지" 조기 동결 (resolve 러너+목록 뷰+기각 UX), replace는 다음**
- 장: 태그-main drift 즉시 해소(이미 공개된 커밋에 라벨), 릴리스 단위 작고 검증 부담 최소.
- 단: deprecate confirm = **신규 write 능력**이라 patch 번호는 semver 관행 부적합. UX 반쪽(기각만, 수정/확정 없음) 릴리스 노트·검증 2회 중복.

**(b) v0.9.0-rc1 — "후보 관리 UX 완성"(replace+accepted 구현까지) 한 번에**
- 장: 보기/기각/수정/확정이 한 기능 단위(완료기준표 조건 2)로 묶여 노트·clean clone E2E 1회. 새 write 능력 묶음 = **minor bump가 semver 관행에 정합**.
- 단: 동결 지연 — replace/accepted r2가 늘어지면 drift 누적·릴리스 비대.

**추천: (b) v0.9.0-rc1.**
근거: ① 이미 push된 기각 UX부터 신규 write 능력이라 patch(0.8.2)로는 semver 부적합, minor가 맞음. ② 조건 2 UX는 보기→기각→수정→확정이 하나의 사용자 절단면 — 반쪽 공개보다 완성 1회 묶음이 노트/검증/튜토리얼 비용 절반. ③ main은 이미 원격 공개 상태(드러난 위험 없음)라 조기 태그의 실익 낮음.
**Fallback**: replace+accepted r2가 2세션 내 미완이면 (a)로 전환해 v0.8.2-rc1 선동결(기준: drift 5커밋 초과 또는 외부 사용자 이슈 발생 시).

## 3. 릴리스 전 의무 체크리스트 (기존 절차 재사용 — 완료기준표 §검증 의무 5단)

1. 신규 selftest 전건 GATE=GO (resolve/list/deprecate + replace/accepted) — temp, clean clone 호환
2. real staging 실연(스냅샷 선확보) + rollback 실증 — 이번 판단 라운드는 접근 금지, 릴리스 직전 owner GO 하 별도 수행
3. clean clone E2E (%TEMP% 격리): doctor 12/12 포함 기존 17종 + 신규 — **문서가 약속한 기대값 문자열까지 대조** (6/10 교훈)
4. `public_tree_scan --tree <ROOT>` 명시 실행 CLEAN (무인자=selftest 모드 함정 회피) + 금지표현/개인경로/실키 0
5. 릴리스 노트: 변경 목록=커밋 실측 일치, 검증 수치=실측 일치, HOLD 명시(hosted live·OpenCrab apply·confirmed 자동·marketplace), 버전룰 라인(v0.7.0=hosted 실구현 예약과 충돌 없음 확인)
6. 화이트리스트 stage(`git add <명시파일>` + `diff --cached --name-only` 카운트 검증, `git add .` 금지) → tag·release = owner GO 시점에만

> 본 문서 = 판단 전용. 실행(commit/tag/release)은 owner 명시 GO 후 별도 라운드.
