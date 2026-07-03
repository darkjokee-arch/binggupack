# BingguPack — save-intent A-2 라이브 canary 결과 (2026-06-12)

> owner 승인 문구 "a-2 배포 고" 하에 집행. 설계 정본 `BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md` A-2.
> **합성만 — 실 사용자 데이터 0. read/v2 라인 무접촉. inbox 최종 = 잠김.**

## 결과 — 16/16 GATE=GO

worker = `binggupack-save-intent-mcp.<account>.workers.dev` (별도 name — read·v2 라인과 분리)

- V0 deploy / V1 secret 미주입 503 / V2 secret 2종 주입 / V3 전파+MCP ping
- **L1 라이브 MCP initialize + serverInfo** / **L2 tools/list save_intent** / L3 기본 비활성 isError(inbox_disabled) / L4 enable(HMAC) / L5 Origin 403 / L6 오경로키 404
- **L7 라이브 MCP 적재 isError=false + intent_id 일치** / L8 echo 0 / **L9 HMAC pull 1건 + 재해시(전역)** / L10 2차 0건 / L11 무서명 pull 401 / L12 disable 후 재잠금

→ 폰 claude.ai 방식(MCP)으로 라이브 적재가 실증됨. 적재(MCP·경로키+Origin) / 인출(HMAC) 이중 인증 라이브 동작 + DO 전역 drain.

## 운영 상태

- save-mcp worker **배포됨 + inbox 잠김(inbox_disabled)** — A-3 전 기본 off 유지
- secret 2종(SAVE_PATH_·SAVE_SIGN_) CF only / 로컬 사본 `workers_port/.dev.vars.save_mcp`(gitignore, WORKER_URL 포함) / 평문 출력 0
- read 라인(62팩)·v2 라인 응답 정상 — 무영향
- Workers Logs 미활성

## 비상 셧다운

1. `admin/disable`(HMAC) — 현재 상태
2. `npx wrangler delete --config wrangler.save_mcp.prod.toml`

## 단계 상태

- A-0 설계 ✅ / A-1 로컬 15/15 ✅ / **A-2 라이브 canary 16/16 ✅**
- **A-3 = 폰 실사용 (owner GO + 폰 발화)**:
  1. (claude/PC) inbox enable
  2. (owner 폰) claude.ai 커넥터에 save-mcp URL `https://binggupack-save-intent-mcp.<account>.workers.dev/mcp2/<경로키>` 등록 (읽기 커넥터와 별개)
  3. (owner 폰) 채팅에서 후보 preview → `SAVE n,m` 발화 → save_intent 도구 호출
  4. (claude/PC) 러너 pull → 게이트 → preview → 번호 선택 → confirm → **실 로컬 장부** 80자 발췌
  5. inbox 정책: 사용 후 disable(추천)
  > 경로키·URL은 owner에게만 직접 전달(평문 채팅/공개 기록 금지). 실 데이터 첫 유입 — owner 직접 발화.
