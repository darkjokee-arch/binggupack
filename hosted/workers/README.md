# BingguPack Hosted MCP — Cloudflare Workers runtime (Phase 1)

> **connector 정본(TS)** — Claude·ChatGPT custom connector에서 read-only **6 tool** 실동작 검증 완료(2026-06-11).
> 결과·보안 설계·운영 수칙: [`docs/BINGGUPACK_HOSTED_CONNECTOR_PHASE1_RESULT.md`](../../docs/BINGGUPACK_HOSTED_CONNECTOR_PHASE1_RESULT.md)
> repo 루트의 Python skeleton(`scripts/binggupack_http_mcp_skeleton.py`)은 PoC archive(frozen) — 동작 동등(content parity 검증).

## 구성
- `src/index.ts` — 전체 런타임 (streamable HTTP JSON-only · stateless · read-only **6 tool** · synthetic toy pack 임베드)
- `src/capture_preview.ts` — 6번째 도구 `conversation_capture_preview` (대화 텍스트→핵심 문장 후보 미리보기, 저장 0·PII/secret 문장 제외)
- `wrangler.toml` — 최소 설정 (routes/KV/observability 미설정. `name`은 사용자 환경에 맞게 변경 가능)
- `src/load_packs.ts` — 실 pack JSON 검증 로더 (fail-closed: 위반 1건 = 기동 실패)
- `src/index.real.ts` + `wrangler.real.toml` — 실 pack private 빌드 entry (아래 참조)

## 배포 (개인용)
```bash
npx wrangler login                          # 본인 Cloudflare 계정
npx wrangler deploy                         # workers.dev 서브도메인 필요(계정 1회 설정)
npx wrangler secret put MCP_PATH_TOKEN      # 비공개 경로 토큰(예: 32자 hex) — 코드/설정 평문 금지
```
- 접속 경로는 `https://<worker>.<subdomain>.workers.dev/mcp/<MCP_PATH_TOKEN>` — **토큰 미설정 시 전 요청 503(fail-closed)**, 무토큰/오토큰 404.
- 로컬 개발: `.dev.vars` 파일에 `MCP_PATH_TOKEN` 한 줄(키와 값)을 작성 후 `npx wrangler dev` (`.dev.vars`는 커밋 금지).

## 실 pack private 빌드 (코드=public / 데이터=private)
- 실 데이터는 `data/packs.json`에만 — **gitignore + 배포 머신 로컬 전용**(repo에 커밋 금지, 공개 트리 스캐너도 이 경로 존재 자체를 BLOCK).
- **clean clone에서 `wrangler.real.toml` 빌드는 실패한다 — 의도된 fail-closed 동작** (data 부재 = 실 데이터가 repo에 없다는 증명).
- `data/packs.json`은 배포 전 게이트 체인(doctor → tree scan → source pointer → secret/PII → 런타임 leakScan 사전 전수 → 20K 캡) 전건 통과 시에만 생성 허용.
- 비상 셧다운: `npx wrangler rollback`으로 toy-only 구버전 복귀(실데이터를 망에서 즉시 제거).

## 불변 원칙
read-only 6 tool만(저장/write 0) · toy entry는 synthetic 한정 · real entry는 게이트 전건 통과 데이터만(레포에 실 데이터 0) · candidate-first(승격/병합 금지 룰 동봉) · fail-closed 누출 스캔 · Workers Logs/observability 끄기 유지(URL 토큰 기록 방지) · OAuth/write 미구현.
**운영 수칙**: stateless 서버라 도구 목록 변경 통지(listChanged)가 없음 — 도구 추가/변경 배포 후에는 각 클라이언트에서 커넥터 **재연결**(Claude) 또는 **제거 후 재추가**(ChatGPT)가 필요.
