> OpenBinggu is the legacy/internal codename for BingguPack.

marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# OpenBinggu 1차 배포 — 사용자 주도 OpenCrab 업로드 흐름 (FLOW)

> **상태: 흐름 정의 문서(2026-06-08). docs only · 실 업로드 0 · 업로드 도구 미구현(별도 GO).**
> 상위: [FIRST_RELEASE_GITHUB_MCP_DESIGN](OPENBINGGU_FIRST_RELEASE_GITHUB_MCP_DESIGN.md) §2-4 · [RELEASE_PREFLIGHT_CHECKLIST](OPENBINGGU_RELEASE_PREFLIGHT_CHECKLIST.md) §3.

---

## 0. 한 줄

OpenCrab은 **각 사용자가 가입해서 자기 pack을 자기 의지로 올리는 곳**이다(우리가 자동으로 쌓는 중앙 store 아님). 사용자가 자기 계정에 올리는 흐름은 **GitHub 공개와 완전히 동일한 fail-closed gate**를 거치며, **우리 시스템의 자동 store write/apply/ingest와는 명확히 구분**된다.

---

## 1. 두 가지를 절대 섞지 않는다

| 구분 | 사용자 주도 OpenCrab 업로드 | 우리 시스템/운영자의 자동 store write |
|---|---|---|
| 누가 | 가입한 사용자 본인 | OpenBinggu 시스템/운영자 |
| 트리거 | 사용자가 수동 승인 후 직접 실행 | 자동(파이프라인) |
| 데이터 방향 | 사용자 → 자기 OpenCrab 계정 | 시스템 → 운영 그래프/store |
| 판정 | **1차 포함 가능**(gate 통과 시) | **HOLD** |
| 위험 | 사용자가 게이트 통과분만 올림 | 운영 그래프 자동 오염 |

> 본 문서는 **왼쪽(사용자 주도 업로드)** 만 다룬다. 오른쪽(자동 store write)은 계속 HOLD.

---

## 2. 사용자 주도 업로드 흐름

```
[1] 사용자가 로컬에서 candidate pack 생성 (자기 자료, 로컬 고정)
[2] fail-closed gate 통과 검사 (GitHub 공개와 동일)
    - source pointer dirty/unknown      → 업로드 BLOCK
    - raw PII/secret/private path 잔존   → 업로드 BLOCK
    - redaction residual 0 확인
    - 공개 pack은 source pointer 미포함 디폴트(SANITIZER §S2)
[3] 업로드 요약 표시 (raw 없이 count/reason_code/source_pointer_id 만)
[4] user 1회 명시 승인 (무엇을·어느 계정에·항목 수)
    - 승인 전 업로드 금지
    - 자동/일괄 업로드 금지, 이전 승인 재사용 금지
[5] 승인 후에만 사용자가 자기 OpenCrab 계정에 업로드
    - 사용자 본인 행위 (우리 시스템 자동 write 아님)
```

- gate(2)는 [PREFLIGHT §3] 공개 게이트와 동일 기준. GitHub push와 OpenCrab 업로드는 같은 문을 통과.
- 업로드 대상은 **사용자 자기 계정**. 다른 사용자/운영 그래프 강제 반영 0.

---

## 3. 경계 / 금지

- ❌ 우리 시스템/운영자가 OpenCrab store에 **자동** write/apply/ingest (HOLD).
- ❌ user 승인 없는 업로드 / 자동·일괄 업로드.
- ❌ dirty/unknown source pointer·raw PII/secret/private path 포함 pack 업로드.
- ❌ 업로드 결과로 raw 값 출력.
- ⚠️ 실제 업로드 도구(MCP/CLI)는 **미구현**([MCP_EXPOSURE](OPENBINGGU_MCP_EXPOSURE_CANDIDATE.md): 자동화 금지·수동 승인 후 사용자 실행 형태로만, 별도 GO).

## 4. 상태

- 사용자 주도 OpenCrab 업로드 = **docs 기준 정리 완료**(GitHub 공개와 동일 gate 기준 확정). **⚠️ 구현 완료 아님**: 실제 OpenCrab 업로드 기능/API/MCP 연결은 **HOLD/미구현**. 업로드 도구 구현 + owner/user 승인 후에만 실 업로드.

## 5. 안전

docs only. 실 OpenCrab 업로드·자동 store write/apply/ingest·GitHub push·production·enum·team_paid·marketplace·sanitizer·raw 출력 0. operating store mtime 불변.
