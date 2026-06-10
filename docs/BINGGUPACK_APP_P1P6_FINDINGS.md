# BingguPack App P1~P6 실측 결과 (2026-06-10, 공식 문서 기준)

> `BINGGUPACK_APP_PATH_DESIGN.md` §7의 "플랫폼별 확인 필요 목록" P1~P6을 공식 문서로 실측한 결과.
> 조사만 수행 — **코드 구현 0 · hosted 배포 0 · OAuth 구현 0** (전부 기존 HOLD 유지).

## 종합 판정 — `@BingguPack` 최종 UX 가능 여부

| 플랫폼 | 판정 | 근거 |
|---|---|---|
| **ChatGPT** | **가능** (2단계) | Developer Mode로 본인 계정 즉시 연결(심사 0) → 디렉토리 공개는 개인 신원확인+심사. 모바일은 웹에서 연결 후 사용 가능 |
| **Claude** | **가능** (최저 장벽) | 전 플랜 custom connector + **no-auth 공식 허용** + 모바일(기추가 서버) 사용 가능 |
| **Gemini** | **조건부** | 소비자 Gemini 앱은 custom MCP 미개방(파트너 전용) — fallback은 Gemini CLI(`httpUrl`로 동일 서버 재사용) |

**핵심 결론 3줄:**
1. **HTTPS streamable HTTP MCP 서버 1개**로 ChatGPT(Developer Mode/Apps SDK) + Claude(custom connector) + Gemini CLI 3곳 커버 — adapter 불필요. 소비자 Gemini 앱만 진입로 자체가 닫혀 있음(CLI fallback).
2. **3 경로 모두 OAuth 없이 시작 가능** (Claude no-auth 공식 / ChatGPT Developer Mode / Gemini CLI headers 키). OAuth는 공개 확장 시점에 추가.
3. 권장 1순위 경로: stateless·JSON-only·**read-only** MCP 서버 → **Claude custom connector(no-auth)로 먼저 검증**(모바일 포함) → 같은 URL을 ChatGPT Developer Mode·Gemini CLI에 등록 → 반응 확인 후 OAuth + ChatGPT 디렉토리 심사.

---

## P1. ChatGPT Apps 등록 요건

**확인된 사실**
- Apps SDK는 **MCP 기반**("MCP server + UI components") — https://developers.openai.com/apps-sdk
- 개인 개발자 제출 가능, 단 **개인 신원확인(Individual verification) 필수** + `api.apps.write` 권한 — https://developers.openai.com/apps-sdk/deploy/submission
- MCP 서버는 **공개 도메인 호스팅 필수**(로컬 endpoint 불가), CSP 정의 요구. 품질·안전·프라이버시 정책 필수 — https://developers.openai.com/apps-sdk/app-submission-guidelines
- 디렉토리 외 **Developer Mode**: Plus/Pro/Business 등에서 본인 계정에 custom MCP connector 직접 추가, 심사 불필요 — https://help.openai.com/en/articles/12584461

**불확실**: 심사 소요 기간 미공표. 개인 플랜 Developer Mode의 write 도구 제한은 시점별 정책 변동 가능성.

**시사점**: read-only pack 조회 도구라면 Developer Mode만으로 즉시 사용 가능(심사 0). 디렉토리 공개를 원할 때만 신원확인+심사.

## P2. HTTPS MCP (streamable HTTP) 요건

**확인된 사실** (https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- 단일 MCP endpoint가 POST/GET 모두 지원(MUST). 클라이언트 POST는 `Accept: application/json, text/event-stream`.
- 서버는 단일 JSON 응답 또는 SSE 중 택1 — **SSE는 선택사항** (JSON-only 서버도 스펙 적합).
- 세션(`MCP-Session-Id`)은 서버 선택(MAY) — **stateless 서버 합법**. `MCP-Protocol-Version` 헤더, `Origin` 검증(DNS rebinding 방지) 의무.
- 장기 연결 유지 의무 없음(SSE `retry`/`Last-Event-ID` 재개는 MAY).

**불확실**: 스펙에 응답 크기·timeout 수치 규정 없음(클라이언트별 상이).

**시사점**: stateless + JSON-only로 최소 구현해도 적합 — 기존 stdio JSON-RPC 서버에 POST 핸들러 한 겹 추가가 핵심. 세션 관리 생략 가능이 1인 운영에 유리.

## P3. Claude remote connector

**확인된 사실**
- custom connector(remote MCP)는 **전 플랜** 지원(Free 1개 제한), 설정→Connectors에 URL 입력 — https://support.claude.com/en/articles/11175166
- 인증: OAuth DCR(RFC 7591)·PKCE S256 외에 **No authentication(authless) 허용**. 정적 bearer 토큰 미지원 — https://claude.com/docs/connectors/building/authentication
- **모바일**: 웹에서 추가한 서버는 iOS/Android에서 도구 사용 가능(모바일에서 신규 추가는 불가) — https://support.claude.com/en/articles/11503834
- 연결 발신은 Anthropic 서버 — MCP 서버가 공인 인터넷 접근 가능해야 함.

**불확실**: authless UI 플로우가 케이스별 마찰 보고 있음(미세).

**시사점**: 3사 중 진입장벽 최저. 단 authless면 URL을 아는 누구나 접근 가능 → **read-only 도구만 노출 + URL 비공개 운영 전제** (공개 확장 시 OAuth 필수).

## P4. Gemini 측 대응

**확인된 사실**
- 소비자 Gemini 앱: custom MCP connector 추가 기능 **없음** (Spark 런타임 MCP 채택 발표, 사전 파트너 3개만 — 일반 개방 미공개).
- Gemini Enterprise: custom MCP 지원(preview) — Streamable HTTP 전용·공개 IP·**OAuth 강제** — https://docs.cloud.google.com/gemini/enterprise/docs/connectors/custom-mcp-server/set-up-custom-mcp-server
- **Gemini CLI**: `mcpServers`에 `httpUrl`(streamable HTTP)/`headers` 지원 — **동일 서버 그대로 재사용 가능** — https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html

**불확실**: 소비자 앱 MCP 일반 개방 시점.

**시사점**: Gemini는 adapter가 아니라 **진입로 문제** — CLI는 동일 서버 재사용, 소비자 앱은 개방 공지 모니터링.

## P5. tool 응답 크기 상한

**확인된 사실**
- MCP 스펙: 하드 제한 없음(클라이언트 truncation 논의 중 — modelcontextprotocol discussions #2211).
- Claude: tool 결과 ~**25K 토큰** 캡, 초과 truncate(claude.ai 웹은 조정 불가).
- ChatGPT: tool 정의 합계 **5,000 토큰 미만** 요구, 응답 truncation 보고 다수(정확 상한 미공표). Apps SDK 가이드: "structuredContent는 작게, outputSchema 선언, 응답 내 시크릿 금지" — https://developers.openai.com/apps-sdk/build/mcp-server
- truncation 발생 시 서버에 통보 없음(프로토콜 한계).

**시사점**: 안전 기준 = **응답 1만 토큰 이하 + 요약/ID 반환 후 상세는 후속 호출(pagination)**. 기존 "summary/count/id만 반환" 원칙이 그대로 정답 — `handoff_context`는 `max_nodes` 기본값을 보수적으로.

## P6. 무료 hosted 런타임 후보

| 후보 | 무료 티어 | cold start | MCP 적합성 |
|---|---|---|---|
| **Cloudflare Workers** ⭐1순위 | 10만 req/일 영구 무료 | ~0ms | 공식 remote MCP 템플릿, streamable HTTP OK |
| Render | 750시간/월 | 15분 유휴 후 sleep, 첫 요청 ~1분 | OK(sleep이 UX 해침) — Python 그대로면 차선 |
| Railway | 소액 크레딧(24/7 불가) | 없음 | OK, 무료론 상시 불가 |
| Vercel Functions | Hobby 무료 | 1~3초 | 장시간 SSE 제약(JSON-only면 무관) |
| Fly.io | 무료 티어 폐지(카드 필수) | 빠름 | 저비용 가능, 무료 아님 |

**불확실**: Cloudflare 무료 티어 CPU 제한(10ms)이 pack 검증 로직에 충분한지 실측 필요. Workers는 Python 서버 재사용이 아니라 JS/TS 포팅 + 데이터 저장(KV/D1) 변환 검토 필요.

---

## 다음 단계 연결

- 구현 후보 정리: `BINGGUPACK_NEXT_IMPLEMENTATION_CANDIDATES.md`
- 설계 본문: `BINGGUPACK_APP_PATH_DESIGN.md` (§7에 본 결과 요약 반영)
- 모든 구현(hosted server·OAuth·배포)은 **별도 GO 전까지 HOLD** 불변.
