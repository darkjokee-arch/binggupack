# BingguPack App Path 설계 — `@BingguPack` 호출 (read core IMPLEMENTED, transport DESIGN ONLY)

> 최종 UX: 일반 채팅 앱에서 `@BingguPack`처럼 자기 pack context를 호출.
> **상태 (v1.21-A, 2026-07-12):**
> - ✅ **read core IMPLEMENTED** — `binggupack/app/read_core.py` 에 transport-independent 5-tool 순수 core(pack_list·pack_summary·evidence_search·node_edge_lookup·handoff_context) 구현. read-only · write/network/cache 0 · conformance harness GO.
> - ❌ **HTTPS transport NOT implemented** · ❌ **auth/user isolation NOT implemented** · ❌ **upload/deploy 0** · ❌ hosted 배포 0.
> ChatGPT Apps/HTTPS MCP first, Claude/Gemini는 platform adapter later. §1~§6 spec 은 read core 가 그대로 구현했고, transport/auth/upload 는 v1.21-B/C/D 로 남는다.

## 1. hosted MCP 최소 v1 범위

- **read-only 전용** — 로컬 RC와 동일 원칙: write/apply/confirmed/upload 도구 미노출.
- 전송: **HTTPS MCP** (streamable HTTP). ChatGPT Apps가 1차 타깃(HTTPS MCP 네이티브), Claude는 remote MCP connector, Gemini는 별도 adapter — 플랫폼별 차이는 adapter 층에서 흡수.
- 데이터: 사용자가 자기 pack을 자기 hosted 공간에 올린 것만 서빙(사용자별 격리). 서버가 사용자 로컬을 읽지 않음.
- v1에서 안 하는 것: pack 생성/편집·팀 공유·결제·marketplace (전부 기존 DEFER/BLOCK/HOLD 유지).

## 2. tool 5종 (v1 제안)

| tool | 입력 | 출력 | 비고 |
|---|---|---|---|
| `pack_list` | — | 내 pack id/제목/counts 목록 | 요약만, raw 경로 0 |
| `pack_summary` | pack_id | manifest 요약 + 노드/엣지/evidence counts + 주제 라벨 | |
| `evidence_search` | pack_id, query | evidence id+문장 발췌 상위 N | FTS 기반, candidate 표시 유지 |
| `node_edge_lookup` | pack_id, node_id 또는 키워드 | 노드+연결 엣지(verb·relation·evidence_refs) | evidence_refs 기반 답변 규칙 전제 |
| `handoff_context` | pack_id, (옵션) 주제 | 모델 투입용 context 블록(Phase 3 prompt template 형식) | mobile fallback과 동일 포맷 = 경로 간 일관성 |

## 3. auth / data boundary

- **auth**: 플랫폼 OAuth(ChatGPT Apps 표준) → 사용자별 token → 자기 pack namespace만 접근. 토큰은 pack read 권한만(쓰기 scope 자체 미정의).
- **boundary**: ① 서빙 대상 = 사용자가 명시 업로드한 pack만(fail-closed: publish guard CLEAN 통과분만 업로드 가능) ② secret/PII는 업로드 전 로컬 scan에서 차단(기존 게이트 재사용) ③ 서버 로그에 pack 내용 미기록(요청 메타만) ④ cross-user 접근 deny-by-default(기존 enforce_access 모델 동일).
- candidate-first 유지: 앱 응답에도 candidate/confirmed 구분 표시, 앱이 승격을 수행하지 않음.

## 4. 모바일 UX 흐름 (v1)

1. 사용자가 채팅 앱에서 `@BingguPack` 호출 (또는 앱 디렉터리에서 연결)
2. 최초 1회 OAuth 연결 → `pack_list`로 자기 pack 선택
3. 대화 중 "그 프로젝트 맥락으로" → 모델이 `handoff_context`/`evidence_search` 호출
4. 모델 답변은 evidence_refs 기반(근거 없으면 "pack에 근거 없음") — Phase 3 consumer 규칙 그대로

## 5. fallback — mobile handoff Markdown 유지

- hosted 미사용/미지원 플랫폼: **Phase 3 handoff guide의 prompt template + pack 요약 Markdown 붙여넣기**가 공식 fallback으로 영구 유지.
- `handoff_context` tool 출력 == fallback Markdown 형식 동일화 → 앱 경로/수동 경로 간 학습 비용 0.

## 6. tool 5종 상세 schema (v1 spec)

공통: 모든 tool은 read-only · 입력에 raw 경로 불가(id 기반) · 출력에 raw 경로/secret 미포함 · 오류는 `{error_code, message}` (내부 경로 미노출). pagination은 `cursor`(opaque string) 통일.

### 6-1. pack_list
| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| (입력) cursor | string | ✗ | 페이지 토큰 |
| (출력) packs[] | array | — | `{pack_id, title, counts:{nodes,edges,evidence}, updated_at}` |
오류: `AUTH_REQUIRED`. limit 기본 20.

### 6-2. pack_summary
| 입력 | pack_id(string, 필수) |
| 출력 | `{pack_id, manifest_summary:{license, counts, pack_type}, topics[](라벨 상위 10), candidate_note}` |
오류: `PACK_NOT_FOUND`, `ACCESS_DENIED`(cross-user는 deny-by-default).

### 6-3. evidence_search
| 입력 | pack_id(필수), query(string 필수, 2~200자), limit(int ✗ 기본 5, 최대 20) |
| 출력 | `hits[]: {evidence_id, sentence_excerpt(≤200자), score}` — candidate 표시 포함 |
오류: `QUERY_TOO_SHORT`, `PACK_NOT_FOUND`. FTS(contentless) 기반 — 서버는 count+MATCH+rowid join으로 조회.

### 6-4. node_edge_lookup
| 입력 | pack_id(필수), node_id 또는 keyword 중 1 (둘 다 주면 node_id 우선) |
| 출력 | `{node:{id, label, sentence, candidate, evidence_status}, edges[]:{id, relation, verb, direction, peer_id, evidence_refs[]}}` |
오류: `NODE_NOT_FOUND`, `AMBIGUOUS_KEYWORD`(상위 5 후보 id 반환).

### 6-5. handoff_context
| 입력 | pack_id(필수), topic(string ✗), max_nodes(int ✗ 기본 15) |
| 출력 | `{context_markdown}` — **Phase 3 handoff guide의 prompt template과 동일 형식** (pack 요약 + 노드/엣지 발췌 + consumer 규칙 4줄: evidence_refs 기반·추측 금지·candidate 표시·자동 병합 금지) |
설계 불변: 이 출력 == mobile fallback에서 사람이 복붙하는 Markdown — 단일 포맷 정의를 양쪽이 공유(이중 유지보수 금지).

## 7. 플랫폼별 확인 필요 목록 — ✅ 실측 완료 (2026-06-10, 상세: `BINGGUPACK_APP_P1P6_FINDINGS.md`)

| # | 항목 | 플랫폼 | 실측 결과 요약 |
|---|---|---|---|
| P1 | Apps 등록 요건 | ChatGPT Apps | MCP 기반. Developer Mode로 본인 계정 즉시 연결(심사 0) / 디렉토리 공개는 개인 신원확인+심사. 서버는 공개 도메인 필수 |
| P2 | HTTPS MCP(streamable) 세션/timeout | ChatGPT/공통 | 단일 endpoint POST/GET. **SSE 선택사항·stateless 합법** → JSON-only 최소 구현으로 스펙 적합 |
| P3 | remote connector·OAuth | Claude | 전 플랜 지원 + **no-auth 공식 허용** + 모바일 사용 가능(웹에서 추가 후) — 3사 중 최저 장벽 |
| P4 | adapter 형태 | Gemini | 소비자 앱은 custom MCP 미개방(조건부) — **Gemini CLI가 동일 서버 그대로 재사용**(adapter 불필요) |
| P5 | tool 응답 크기 상한 | 전 플랫폼 | Claude ~25K 토큰 캡 / ChatGPT tool 정의 5K 토큰 + truncation 보고 → **응답 1만 토큰 이하 + pagination** 설계 기준 |
| P6 | 무료 hosted 런타임 | 인프라 | **Cloudflare Workers 1순위**(영구 무료·cold start 0·공식 MCP 템플릿, 단 JS/TS 포팅 필요) / Python 유지 시 Render 차선(sleep 감수) |

**§7 결론**: 서버 1개(stateless·JSON-only·read-only)로 ChatGPT+Claude+Gemini CLI 3곳 커버, 3 경로 모두 OAuth 없이 시작 가능. 권장 검증 순서 = Claude no-auth(모바일 포함) → ChatGPT Developer Mode → Gemini CLI → 이후 OAuth+디렉토리 심사. 구현은 전부 별도 GO.

## 8. 역방향 round-trip roadmap — 대화 → candidate capture (planned, v1 범위 밖)

현재 설계(§1~§6)는 **BingguPack → chat app** 방향의 read-only context 제공이다.
역방향 — **chat app 대화 → 사용자 승인 기반 BingguPack candidate capture** — 를 roadmap으로 명시한다.
예: 사용자가 대화 중 `@BingguPack 이 대화를 pack 후보로 저장해줘`.

### tool 후보 2종 (단계 분리)

| tool | 단계 | 동작 |
|---|---|---|
| `conversation_capture_preview` | **먼저** (v1.5 후보) | 대화에서 핵심 문장/판단 후보를 추출해 **미리보기만** 반환 — 저장 0. PII/secret scan 결과(kind만)와 candidate 목록 표시 |
| `conversation_candidate_save` | **나중** (별도 GO) | 사용자가 preview를 보고 **명시 승인한 항목만** candidate로 저장. v1에서는 제외 가능(preview만 planned로 두는 옵션 유효) |

### 안전 원칙 (불변)

1. **자동 저장 금지** — 어떤 경우에도 대화가 사용자 명시 요청·승인 없이 저장되지 않는다 (preview→사용자 승인→save 2단, capture 도구의 manual one-shot 원칙과 동일).
2. **raw 대화 전체 저장 금지** — 핵심 문장/evidence chunk만 candidate화 (M1 capture와 동일 추출 모델).
3. secret/PII scan **필수** — 검출 시 해당 항목 저장 거부(kind만 표시).
4. **candidate-only** — confirmed 아님, promotion_allowed=false. 승격/확정은 기존 로컬 review 경로.
5. **source pointer 기록** — source app·conversation 식별자(원문 아님)를 evidence 출처로 보존.
6. delete/export 권한(저장된 capture의 삭제·반출)은 **추후 별도 설계**.

### fallback

앱/플랫폼이 역방향 저장을 지원하지 않으면: 사용자가 모바일 대화를 **export/copy → BingguPack CLI capture**(manual one-shot, v0.3 경로)로 넣는 방식이 공식 fallback.

## 9. 다음 단계 (전부 별도 GO)

① §7 P1~P6 실측 ② hosted 런타임 선택 ③ upload 게이트(publish guard) 연동 설계 ④ §8 conversation_capture_preview 상세 설계 ⑤ 코드 구현(이 문서 범위 밖).
