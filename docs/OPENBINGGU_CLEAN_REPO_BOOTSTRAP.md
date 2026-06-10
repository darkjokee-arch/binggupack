> OpenBinggu is the legacy/internal codename for BingguPack.

marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# OpenBinggu 1차 배포 — clean repo 부트스트랩 절차 (S1)

> **상태: 절차 문서(2026-06-08). docs only · 실 repo 생성/push 0 · production write 0.**
> 4CLI 선결 gate S1(git 히스토리 영속성) 흡수. 상세 [FIRST_RELEASE_4CLI_SYNTHESIS](OPENBINGGU_FIRST_RELEASE_4CLI_SYNTHESIS.md) §3-A.
> 상위: [RELEASE_REPO_LAYOUT](OPENBINGGU_RELEASE_REPO_LAYOUT.md) · [RELEASE_PREFLIGHT_CHECKLIST](OPENBINGGU_RELEASE_PREFLIGHT_CHECKLIST.md).

---

## 0. 한 줄

공개 repo는 **반드시 새 clean repo에서 시작**한다. 기존 작업 repo를 그대로 공개로 전환하지 않는다(과거 커밋에 실데이터가 영속될 수 있으므로). 정상 배포 흐름 = "처음부터 clean repo".

---

## 1. 왜 clean repo인가 (C S1 반박 흡수)

- git은 **1회 커밋이 영속**된다. 한 번 커밋된 dirty 데이터는 force-push·BFG·filter-branch 없이는 제거 불가이고, 그조차 PR·fork·CI 캐시·클론에 잔존한다.
- 따라서 fail-closed를 "공개 push 직전 1회"만 거는 것으로는 부족하다. **repo 자체를 깨끗하게 태어나게** 하는 것이 유일하게 확실한 방법.
- 기존 작업 repo(개인 그래프·DB·실 경로 history 포함)를 "공개로 전환"하는 경로는 **금지**.

---

## 2. clean repo 부트스트랩 절차 (정상 배포 흐름)

> 본 문서는 절차 정의까지. 실제 repo 생성/push는 owner 명시 승인 후 별도 GO.

1. **새 빈 repo 생성** — 기존 작업 repo와 분리된 새 디렉터리/원격(`git init` 새로). 기존 `.git` history 미반입.
2. **공개 대상만 복사** — [REPO_LAYOUT](OPENBINGGU_RELEASE_REPO_LAYOUT.md) §1 트리에 해당하는 파일만 복사(scripts 4개·docs 공개분·examples/toy_project·tests/fixtures/synthetic·README·INSTALL·mcp.example·LICENSE 위치·.gitignore). 실 그래프/DB/reports/reviews/captures/evidence/.env/credential 미복사.
3. **.gitignore 먼저 배치** — 첫 `git add` 전에 [REPO_LAYOUT §3] .gitignore 적용. 추적 누락 검증(`git check-ignore`).
4. **secret/PII scan** — 복사된 트리 전체 rglob + secret/PII 정규식 → **0건**(존재·길이만 보고, raw 미출력). 1건이라도 검출 시 중단.
5. **source pointer 점검** — 공개 pack은 디폴트 source pointer 미포함([SANITIZER_POLICY_BLOCK_ONLY](OPENBINGGU_SANITIZER_POLICY_BLOCK_ONLY.md) §S2). dirty/unknown 잔존 시 BLOCK.
6. **첫 커밋 = clean** — 위 통과분만 단일 초기 커밋. 이 커밋이 repo history의 시작.
7. **owner 명시 승인** — 공개 요약(무엇을·어디로·항목 수) 확인 후 1회 approve.
8. **push** — 승인 후에만. 자동/일괄 금지.

---

## 3. 금지 / 사고 대응

- ❌ 기존 작업 repo를 그대로 공개 전환.
- ❌ `git filter-branch`/BFG로 사후 정리 의존(불완전·잔존 위험). 사후 purge를 정상 흐름으로 삼지 않는다.
- ❌ 과거 커밋 history를 공개 repo로 반입.
- **사고 시(dirty가 커밋됨)**: 사후 history purge에 의존하지 말고 **새 clean repo 재부트스트랩**(2장 재실행)이 기본. 이미 push됐다면 노출 항목을 reason_code/count로만 식별·보고하고(raw 미출력), repo 비공개/삭제 + 재부트스트랩.

## 4. 상태

- S1 = **docs 절차 정의 완료**(GATE 정의). 실제 repo 생성/push·CI clean 검사 구현은 별도 GO.

## 5. 안전

docs only. 실 repo 생성/push·production·OpenCrab/store/DB 자동 write·apply/ingest/merge·enum·team_paid·marketplace·sanitizer·raw 출력 0. operating store mtime 불변.
