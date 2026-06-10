# BingguPack 다음 구현 후보 3종 — 위험도/선행조건/추천순위 (2026-06-10)

> P1~P6 실측(`BINGGUPACK_APP_P1P6_FINDINGS.md`) 직후 작성. **본 문서는 정리만 — 구현 착수는 후보별 별도 owner GO.**

## 추천순위 요약

| 순위 | 후보 | 위험도 | 한 줄 근거 |
|---|---|---|---|
| **1** | hosted MCP skeleton (로컬 실행 한정) | 중 | P2 실측으로 최소 구현 범위 확정(stateless·JSON-only) — 배포 0이면 HOLD 미침범, app 경로의 유일한 기술 미검증 구간 해소 |
| **2** | mobile handoff export | 하 | 기존 Phase 3 template 재사용 read-only 로컬 기능 — 즉시 사용 가치, hosted 없이도 모바일 UX 일부 충족 |
| **3** | OpenCrab upload GO-OC2 preflight | 중(설계만) / 실행은 고위험 | 외부 전송 첫 진입의 사전 체크리스트·dry-run 설계 — 실행 자체는 계속 고위험 보류 |

---

## 후보 1 — hosted MCP skeleton (1순위 추천)

**범위**: streamable HTTP MCP 서버 최소 골격 — 단일 `/mcp` endpoint(POST/GET), JSON-only(SSE 생략), stateless(세션 생략), read-only tool 5종(`pack_list`/`pack_summary`/`evidence_search`/`node_edge_lookup`/`handoff_context`) 중 1~2종부터. **로컬 실행(localhost) 한정 — 공개 배포·도메인·OAuth는 범위 밖.**

- **위험도: 중** — 코드 신설이지만 read-only·로컬 한정이면 외부 노출 0. 위험은 "skeleton이 슬금슬금 배포로 번지는 scope creep" — 배포는 별도 GO로 명문화하여 차단.
- **선행조건**:
  1. owner GO (코드 구현 자체가 신규 착수)
  2. tool 응답 크기 가드(P5 기준: 1만 토큰 이하 + pagination) 구현 포함
  3. 기존 게이트 재사용: publish guard·path gate·enforce_access 호출 (신규 판정 로직 발명 금지)
  4. selftest 동반 (도구별 정상/거부 케이스)
- **검증 경로**: 로컬 skeleton → (별도 GO) 무료 런타임 배포(P6: Cloudflare Workers 1순위, Python 유지 시 Render) → (별도 GO) Claude custom connector no-auth 등록 → 모바일 실측.
- **추천 이유**: P1~P6 실측으로 불확실성이 걷힌 지금, app 경로에서 남은 유일한 기술 검증이 "우리 서버가 streamable HTTP로 실제 응답하는가". 로컬 skeleton이 그 질문에 최소 비용으로 답함.

## 후보 2 — mobile handoff export (2순위)

**범위**: pack에서 `handoff_context` 형식 Markdown(Phase 3 prompt template과 동일 포맷)을 파일/클립보드로 내보내는 CLI 1개 — 사용자가 모바일 채팅 앱에 복붙하는 fallback 경로(`BINGGUPACK_APP_PATH_DESIGN.md` §5)의 공식 도구화.

- **위험도: 하** — read-only·로컬 파일 출력만. 서버 0·네트워크 0·운영 store write 0.
- **선행조건**:
  1. owner GO
  2. 출력 형식 = APP_PATH_DESIGN §6-5 `handoff_context` 출력과 **단일 포맷 공유** (이중 유지보수 금지 — 추후 hosted tool이 같은 함수 호출)
  3. PII/secret scan 통과분만 export (기존 스캐너 재사용)
- **추천 이유**: hosted 없이도 "모바일에서 내 pack context 쓰기"를 오늘 충족. 후보 1의 `handoff_context` 구현을 선행 공유 모듈로 깔아주는 효과.

## 후보 3 — OpenCrab upload GO-OC2 preflight (3순위)

**범위**: 실 upload **실행이 아니라** 사전 점검 설계+dry-run — 업로드 대상 선별 기준(publish guard CLEAN 통과분만), Neo4j 이벤트 절차(add+start→upload→remove+stop), 실패/중단 시 복구 절차, owner 승인 양식. 산출물 = preflight 체크리스트 문서 + dry-run 리포트.

- **위험도: 설계만이면 중 / 실행은 고위험 보류 불변** — 외부 전송 첫 진입 + Neo4j 이벤트 실행 동반.
- **선행조건**:
  1. owner의 GO-OC2 방향 결정 자체가 선행 (preflight를 만들어도 실행 GO는 별개)
  2. upload 대상 pack 확정 (publish guard CLEAN + PII 0 실측)
  3. Neo4j 이벤트 절차 리허설 범위 합의 (컨테이너 start/stop만 — 데이터 write 0)
- **추천 이유(3순위인 이유)**: 가치는 크지만 owner가 "외부 전송을 시작할지" 자체를 결정하기 전에는 preflight 산출물이 대기 상태가 됨. 후보 1·2가 끝나 app 경로 가치가 입증된 뒤가 자연스러운 순서.

---

## 공통 불변 (모든 후보)

- MCP **write 도구 공개 금지** · OpenCrab finalize/upload/apply 실행 금지 · Neo4j 상시 실행 금지 · 운영 store write 금지 · confirmed 생성 0 · candidate-first 유지.
- 각 후보 착수·범위 확장·배포 전환은 전부 **별도 owner GO**.
