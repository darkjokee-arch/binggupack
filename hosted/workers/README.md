# BingguPack Hosted MCP — Cloudflare Workers runtime (Phase 1)

> **connector 정본(TS)** — Claude custom connector에서 read-only 5 tool 실동작 검증 완료(2026-06-10).
> 결과·보안 설계·운영 수칙: [`docs/BINGGUPACK_HOSTED_CONNECTOR_PHASE1_RESULT.md`](../../docs/BINGGUPACK_HOSTED_CONNECTOR_PHASE1_RESULT.md)
> repo 루트의 Python skeleton(`scripts/binggupack_http_mcp_skeleton.py`)은 PoC archive(frozen) — 동작 동등(content parity 검증).

## 구성
- `src/index.ts` — 전체 런타임 (streamable HTTP JSON-only · stateless · read-only 5 tool · synthetic toy pack 임베드)
- `wrangler.toml` — 최소 설정 (routes/KV/observability 미설정. `name`은 사용자 환경에 맞게 변경 가능)

## 배포 (개인용)
```bash
npx wrangler login                          # 본인 Cloudflare 계정
npx wrangler deploy                         # workers.dev 서브도메인 필요(계정 1회 설정)
npx wrangler secret put MCP_PATH_TOKEN      # 비공개 경로 토큰(예: 32자 hex) — 코드/설정 평문 금지
```
- 접속 경로는 `https://<worker>.<subdomain>.workers.dev/mcp/<MCP_PATH_TOKEN>` — **토큰 미설정 시 전 요청 503(fail-closed)**, 무토큰/오토큰 404.
- 로컬 개발: `.dev.vars` 파일에 `MCP_PATH_TOKEN` 한 줄(키와 값)을 작성 후 `npx wrangler dev` (`.dev.vars`는 커밋 금지).

## 불변 원칙
read-only 5 tool만 · synthetic pack 한정(실 데이터 0) · candidate-first(승격/병합 금지 룰 동봉) · fail-closed 누출 스캔 · Workers Logs/observability 끄기 유지(URL 토큰 기록 방지) · OAuth/write 미구현.
