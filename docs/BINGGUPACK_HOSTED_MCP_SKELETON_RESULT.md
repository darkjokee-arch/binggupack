# BingguPack hosted MCP skeleton — 로컬 PoC 결과 (2026-06-10, GO-HOSTED-MCP-SKELETON-LOCAL)

> App path 첫 기술 검증: **read-only HTTP MCP(streamable HTTP, JSON-only)가 실제로 응답하는가** — 로컬 한정으로 확인 완료.
> 근거 설계: `BINGGUPACK_APP_PATH_DESIGN.md` §1~§6 / 실측: `BINGGUPACK_APP_P1P6_FINDINGS.md` (P2: SSE·세션 선택사항 → stateless·JSON-only 적합).

## 1. 결과 요약

- **GATE: GO** — selftest **21/21 PASS** (실 HTTP E2E) + 실 서버(127.0.0.1:8841) curl 검증 + public tree scan CLEAN + doctor 12/12.
- streamable HTTP 단일 endpoint `/mcp`: POST(JSON-RPC) 정상 응답 · GET 405(JSON-only, 스펙 허용) · stateless(세션 헤더 미발급, 스펙 합법).
- tool 5종 전부 실호출 검증: `pack_list` / `pack_summary` / `evidence_search` / `node_edge_lookup` / `handoff_context`.

## 2. 구현 범위 (불변 준수)

| 항목 | 상태 |
|---|---|
| bind | **127.0.0.1 하드코딩** — 변경 옵션 자체 없음 (로컬 한정) |
| auth | 없음 (no-auth 로컬 only — 배포·OAuth는 범위 밖) |
| tool | read-only 5종만. write/apply/upload/finalize/confirm/promote 류 **0** (selftest S8 검증) |
| 데이터 | synthetic toy pack 2개(`--make-packs` 생성, tmp/) — 실 데이터·운영 store 접근 0 |
| view | 기존 `openbinggu_pack_consumer_smoke.consume()` 재사용 — candidate/confirmed 구분·redaction 보존 |
| 응답 | JSON-only · `content`+`structuredContent` 동시 반환(MCP 표준) · 크기 캡 36,000자(≈1만 토큰 보수) + 절단 시 truncated 표시 |
| 입력 | pagination/limit 파라미터: `limit`(pack_list·evidence_search) / `max_nodes`(handoff_context) |
| 가드 | Origin 검증(localhost 외 403) · fail-closed 누출 스캔(절대경로/내부 흔적 검출 시 SANITIZE_BLOCK) |
| 오류 | `{error_code, message}` — PACK_NOT_FOUND / QUERY_TOO_SHORT / NODE_NOT_FOUND / AMBIGUOUS_KEYWORD / INVALID_ARGUMENT |

## 3. 검증 상세

### selftest (`binggupack_http_mcp_skeleton_selftest.py`) — 21/21 PASS

S1 initialize(protocolVersion·capabilities.tools·serverInfo) / S2 stateless(세션 헤더 0) / S3 notification 202 / S4 GET 405 / S5 잘못된 path 404 / S6 tools/list 5종 / S7 inputSchema 전건 object / S8 write tool 미노출 / S9 pack_list 2팩 / S10 pack_summary / S11 PACK_NOT_FOUND / S12 handoff_context(consumer rules 4줄+candidate 표기) / S13 크기 캡 / S14 evidence_search(발췌 ≤200자·candidate) / S15 QUERY_TOO_SHORT / S16 node_edge_lookup(edge evidence_refs 전건) / S17 NODE_NOT_FOUND / S18 unknown tool 거부 / S19 evil Origin 403 / S20 전 응답 누출 스캔 0 / S21 운영 store 불변.

### 실 서버 curl 검증 (port 8841, 검증 후 정확 PID로 즉시 종료)

- initialize → `protocolVersion 2025-06-18` + serverInfo 정상
- `pack_list` → toy pack 2개(counts 포함, raw 경로 0)
- `handoff_context` → mobile fallback 동일 형식 Markdown(consumer rules 4줄 포함)
- GET /mcp → **405** (JSON-only 정합)

### 공개 안전 검증

- public tree scan `--tree .` : **620 파일 CLEAN hits=0**
- doctor: **12/12 GATE=GO** (기존 회귀 0)
- 내부요소 grep: 신규 파일에 절대경로/`_backup`/이메일 0 (스캐너 패턴 정의부 제외)
- ⭐ 교훈: selftest 출력 문구("secret" 키워드 바로 뒤에 등호가 오는 형태)가 tree scan의 secret_kv regex에 자기검출 → 키워드 뒤에 `_pat_hits`를 붙인 표기로 수정(스캐너 무수정). 공개 트리에 들어가는 파일은 출력 문구·문서 서술도 scanner regex와 충돌하지 않게 작성.

## 4. 사용법 (로컬)

```
python scripts/binggupack_http_mcp_skeleton.py --make-packs          # toy pack 생성
python scripts/binggupack_http_mcp_skeleton.py --serve --port 8841   # 127.0.0.1 전용
python scripts/binggupack_http_mcp_skeleton_selftest.py              # E2E 21 체크
```

## 5. 다음 단계 (전부 별도 GO — 현재 HOLD 불변)

① 무료 런타임 배포(P6: Cloudflare Workers 1순위 — JS/TS 포팅 또는 Render Python) ② Claude custom connector(no-auth) 등록 → 모바일 실측 ③ OAuth 추가 ④ ChatGPT Developer Mode/디렉토리. hosted 배포·OAuth·MCP write·OpenCrab upload/apply/finalize·Neo4j·운영 store write 전부 미실행.
