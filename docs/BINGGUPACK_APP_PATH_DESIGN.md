# BingguPack App Path 설계 — `@BingguPack` 호출 (DESIGN ONLY, planned)

> 최종 UX: 일반 채팅 앱에서 `@BingguPack`처럼 자기 pack context를 호출.
> 이 문서는 설계 초안이며 **코드 구현 0 · hosted 배포 0**. ChatGPT Apps/HTTPS MCP first, Claude/Gemini는 platform adapter later.

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

## 7. 플랫폼별 확인 필요 목록 (실측 전 가정 금지)

| # | 항목 | 플랫폼 |
|---|---|---|
| P1 | Apps 등록 요건(검수·도메인 인증·rate limit 정책) | ChatGPT Apps |
| P2 | HTTPS MCP(streamable) 세션/SSE 요구사항·timeout | ChatGPT/공통 |
| P3 | remote MCP connector 등록 방식·OAuth scope 제약 | Claude |
| P4 | adapter 형태(Gems/Extensions 중 무엇이 tool 호출 지원하는지) | Gemini |
| P5 | tool 응답 크기 상한(handoff_context Markdown이 잘리는 한계) | 전 플랫폼 |
| P6 | 무료 hosted 런타임 후보(서버리스 cold start가 MCP 세션과 호환되는지) | 인프라 |

## 8. 다음 단계 (전부 별도 GO)

① §7 P1~P6 실측 ② hosted 런타임 선택 ③ upload 게이트(publish guard) 연동 설계 ④ 코드 구현(이 문서 범위 밖).
