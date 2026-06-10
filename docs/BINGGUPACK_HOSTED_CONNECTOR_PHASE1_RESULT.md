# BingguPack Hosted Connector Phase 1 — Claude 실동작 검증 결과 (2026-06-10)

> hosted MCP 서버(Cloudflare Workers, TS)가 **Claude custom connector에서 read-only 5 tool 실동작**까지 검증된 기록.
> 본 문서는 결과 기록이며 배포 URL·토큰·계정 정보는 포함하지 않는다(비공개 운영 정보).
> "100% 완성판" 아님 — synthetic toy pack 한정, 실 pack 탑재·타 플랫폼 등록은 별도 단계.

## 1. 구성 (Phase 1)

| 항목 | 값 |
|---|---|
| 런타임 | Cloudflare Workers (무료 티어), **TS 포팅본 = connector 정본** / 이 repo의 Python skeleton = PoC archive(frozen) |
| transport | streamable HTTP, JSON-only(GET 405), stateless(세션 미발급) |
| auth | no-auth + **비공개 경로 토큰**(`/mcp/<token>`, env 주입 — 코드·설정 평문 0, 미설정 시 전요청 503 fail-closed) |
| 데이터 | synthetic toy pack 2개(모듈 임베드) — 실 데이터·운영 store 접근 0 |
| tool | read-only 5종 (pack_list / pack_summary / evidence_search / node_edge_lookup / handoff_context), 전건 `readOnlyHint:true` + outputSchema |
| 가드 | Origin(서버 발신만 허용)·MCP-Protocol-Version 검증·응답 캡 20K자·fail-closed 누출 스캔(SANITIZE_BLOCK)·오류 응답 structuredContent 생략(스펙 정합) |

## 2. 검증 결과

### 2-1. 로컬 게이트 (배포 전)
- selftest 32/32 GATE=GO — 크기 캡 절단 경로 실발동 검증 포함
- 번들 leak scan GATE=GO (개인 경로·secret 패턴·토큰 평문 0)
- Python 원본과 content parity 10/10 (structuredContent + isError)

### 2-2. 배포 검증 (9항목)
무토큰/오토큰 404 · smoke(ping/tools/list/pack_list) 전건 200 · GET 405 · 응답 중앙값 수백 ms(엣지 RTT 포함) · CPU 캡 초과 오류 0 · 배포 로그 토큰 평문 0.

### 2-3. Claude custom connector 실동작 (owner 실대화)
개인 계정 Settings→Connectors에 no-auth 등록 → **5 tool 전건 성공**:
pack 목록(2)·summary(counts 정확)·evidence 검색(hit+candidate 표기)·node/edge 조회(depends_on 2)·handoff context(consumer rules 4항·truncated=false). readOnlyHint로 write 확인 프롬프트 없이 동작.

## 3. 운영 수칙 (불변)
1. Workers Logs/observability **끄기 유지** — URL 경로 토큰이 평문 기록되는 표면.
2. 토큰 노출 의심 시 회전: `wrangler secret put MCP_PATH_TOKEN` 1줄(신규 값) — URL 교체로 즉시 무효화.
3. 등록은 개인 계정으로(조직 관리형 connector는 no-auth 미지원 사례 있음).
4. 실 pack 탑재는 별도 결정 — tree scan(`--tree` 명시)·source pointer fail-closed 게이트 선행 의무.

## 4. 이번 수정 (v0.7.0 후보 묶음)
- toy 생성기 결함 fix: 전 엣지에 첫 근거(EV-A1)를 일괄 부여하던 단순화 → **의존 대상(target) 노드의 근거로 매핑** (owner가 Claude 실대화 검증 중 발견). Python skeleton·TS Workers 양쪽 동일 적용(parity 유지).
- INSTALL stale 2건 정정(Phase 4 자기모순 문장·"v0.6 후보" 표기).

## 5. HOLD (변경 없음)
ChatGPT/Gemini 등록 · OAuth · 실 pack 탑재 · OpenCrab upload/apply/finalize · 운영 store write · tag/release(별도 GO).
