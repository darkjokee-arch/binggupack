# BingguPack Hosted MCP — Cloudflare Workers runtime

> **connector 정본(TS)** — 라인 2개로 구성:
> **read 라인**(read-only 6 tool, 2026-06-11 실동작 검증) + **save 라인**(v1.2.0 — 폰 `SAVE n` 적재→PC 러너 pull→로컬 장부 저장, 2026-06-12 라이브 실증).
> read 결과·보안 설계: [`docs/BINGGUPACK_HOSTED_CONNECTOR_PHASE1_RESULT.md`](../../docs/BINGGUPACK_HOSTED_CONNECTOR_PHASE1_RESULT.md) /
> save 설계 정본: [`docs/BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md`](../../docs/BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md) · [`docs/BINGGUPACK_SAVE_INTENT_V2_RFC.md`](../../docs/BINGGUPACK_SAVE_INTENT_V2_RFC.md) · [`docs/BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md`](../../docs/BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md)
> repo 루트의 Python skeleton(`scripts/binggupack_http_mcp_skeleton.py`)은 PoC archive(frozen) — read 라인과 동작 동등(content parity 검증).

## 구성 — wrangler 설정 7종 ↔ 워커 매핑

| wrangler config | `name` | `main` | 용도 |
|---|---|---|---|
| `wrangler.toml` | `binggupack-workers-port-local` | `src/index.ts` | **read 라인** toy 빌드 — read-only 6 tool · synthetic toy pack 임베드 |
| `wrangler.real.toml` | `binggupack-workers-port-local` | `src/index.real.ts` | **read 라인** 실 pack private 빌드 — read 라인과 같은 `name`(같은 워커 새 버전 → `rollback`=비상 셧다운) |
| `wrangler.save.toml` | `binggupack-save-intent-local` | `src/index.save.ts` | save v1(D3) — in-memory 적재, **로컬 dev 한정·deploy 금지** |
| `wrangler.save_v2.toml` | `binggupack-save-intent-v2-local` | `src/index.save_v2.ts` | save v2(V2-1) — Durable Object inbox + HMAC, **로컬 dev 한정·deploy 금지** |
| `wrangler.save_v2.prod.toml` | `binggupack-save-intent-v2` | `src/index.save_v2.ts` | save v2 **운영**(V2-2) — HMAC 전용 라인. owner 명시 GO에서만 deploy |
| `wrangler.save_mcp.toml` | `binggupack-save-intent-mcp-local` | `src/index.save_mcp.ts` | save MCP 어댑터(V2-A) — **로컬 dev 한정·deploy 금지** |
| `wrangler.save_mcp.prod.toml` | `binggupack-save-intent-mcp` | `src/index.save_mcp.ts` | save MCP 어댑터 **운영**(A-2) — 폰 커넥터 라인. owner 명시 GO에서만 deploy |

- read 라인과 save 라인은 **`name` 완전 분리** — 오배포·삭제 모두 격리, read 라인(장부 조회) 무접촉.
- `src/capture_preview.ts` — `conversation_capture_preview` 공용 모듈(대화 텍스트→핵심 문장 후보 미리보기, 저장 0·PII/secret 문장 제외). read 라인 6번째 도구이자 save MCP 라인의 미리보기 도구.
- `src/load_packs.ts` — 실 pack JSON 검증 로더(fail-closed: 위반 1건 = 기동 실패).

## read 라인 배포 (개인용)
```bash
npx wrangler login                          # 본인 Cloudflare 계정
npx wrangler deploy                         # workers.dev 서브도메인 필요(계정 1회 설정)
npx wrangler secret put MCP_PATH_TOKEN      # 비공개 경로 토큰(예: 32자 hex) — 코드/설정 평문 금지
```
- 접속 경로는 `https://<worker>.<subdomain>.workers.dev/mcp/<MCP_PATH_TOKEN>` — **토큰 미설정 시 전 요청 503(fail-closed)**, 무토큰/오토큰 404.
- 로컬 개발: `.dev.vars` 파일에 `MCP_PATH_TOKEN` 한 줄(키와 값)을 작성 후 `npx wrangler dev` (`.dev.vars`는 커밋 금지).

## read 라인 실 pack private 빌드 (코드=public / 데이터=private)
- 실 데이터는 `data/packs.json`에만 — **gitignore + 배포 머신 로컬 전용**(repo에 커밋 금지, 공개 트리 스캐너도 이 경로 존재 자체를 BLOCK).
- **clean clone에서 `wrangler.real.toml` 빌드는 실패한다 — 의도된 fail-closed 동작** (data 부재 = 실 데이터가 repo에 없다는 증명).
- `data/packs.json`은 배포 전 게이트 체인(doctor → tree scan → source pointer → secret/PII → 런타임 leakScan 사전 전수 → 20K 캡) 전건 통과 시에만 생성 허용.
- 비상 셧다운: `npx wrangler rollback`으로 toy-only 구버전 복귀(실데이터를 망에서 즉시 제거).

## save 라인 (v1.2.0 — 폰 저장 save-intent)

흐름: **폰(MCP 커넥터)** `conversation_capture_preview`로 후보 1~10 확인 → 사용자가 `SAVE n,m` 발화 → `save_intent`가 worker inbox(Durable Object)에 **휘발 적재** → **PC 러너**가 HMAC pull → 로컬 게이트(`scripts/openbinggu_save_intent_outbox_runner.py`) 통과분만 장부 저장. worker는 전달 통로일 뿐 — **장부 write 0, 최종 저장 권한 = 로컬 러너 게이트**.

### 배포 + secret 주입 (운영 라인, owner GO 하에만)
```bash
npx wrangler deploy --config wrangler.save_mcp.prod.toml
npx wrangler secret put SAVE_PATH_TOKEN  --config wrangler.save_mcp.prod.toml   # 비공개 경로 토큰(예: 32자 hex)
npx wrangler secret put SAVE_SIGN_SECRET --config wrangler.save_mcp.prod.toml   # PC 러너 HMAC 서명 키(예: 64자 hex)
```
- 값은 **placeholder 예시 길이만 참고해 직접 생성** — 코드·설정·문서 어디에도 평문 금지.
- **secret 미주입 시 전 요청 503 not configured (fail-closed)** — 잠긴 상태 배포가 기본.
- HMAC 전용 라인을 쓰려면 `--config wrangler.save_v2.prod.toml`로 동일 절차(secret 2종 동일).
- 라우트(이중 인증 도메인):
  - 적재(폰, MCP): `POST /mcp2/<SAVE_PATH_TOKEN>` — JSON-RPC + Origin 가드
  - 인출·관리(PC): `POST /save2/<SAVE_PATH_TOKEN>/{pull,admin/enable,admin/disable}` — HMAC 서명(`X-BGP-TS` + `X-BGP-SIG`, 재전송 창 ±300초). **신형 v2 = `ts.METHOD.path.bodyhash`** 바인딩, 검증 단일 출처 `save_common.verifySig`(신형 우선 + `SAVE_SIG_V2_ONLY` 미설정 동안 구형 하위호환)

### PC 러너 페어링 개요
- 키 사본은 배포 머신 로컬 파일 1개로만 보관: `.dev.vars.save_mcp` (v2 라인은 `.dev.vars.save_v2`) — `SAVE_PATH_TOKEN`/`SAVE_SIGN_SECRET`/`WORKER_URL` 한 줄씩.
- **`.dev.vars*`는 .gitignore 등록 — 평문 커밋 절대 금지.** 워커 URL도 계정 식별자라 공개 트리 비노출.
- 러너 측 검증/인출 스크립트는 이 사본에서 키를 읽는다(키 출력 0): `scripts/openbinggu_save_intent_v23_live_e2e.py`(E2E) · `scripts/openbinggu_save_intent_live_runner.py`(단일 흐름 enable→inject→pull→process→finally disable, 실 저장은 `--real-ledger --confirm "LIVE SAVE REHEARSAL"` 명시). 라이브 E2E 결과: `docs/BINGGUPACK_SAVE_INTENT_LIVE_E2E_RESULT.md`.
- 폰 미리보기와 PC 러너의 후보 번호 체계는 **10건 고정으로 동일**(`CANDIDATE_MAX=10`) — 11번 이상 인덱스는 worker가 선제 거부.
- **운영 1명령(권장)**: `python binggu.py hosted pull --confirm "LIVE SAVE REHEARSAL" [--wait 60]` — enable(잠금 해제) → 폰/커넥터에서 `SAVE n` → pull → candidate 저장 → inbox disable(다시 잠금·보장)을 한 번에. `--confirm` 없으면 안내만(live worker 미접촉). 경로는 `--workers-port` 또는 `BINGGU_WORKERS_PORT`.

### inbox 평소 잠금 · non-retention 운영 수칙
- **inbox는 평소 잠금이 기본**(DO storage `enabled` 부재 = 닫힘): 적재 시도는 503 `inbox_disabled`. 저장 작업 직전에만 `admin/enable`(HMAC) → 작업 후 즉시 `admin/disable` 복귀.
- **non-retention**: `pull` = drain(반환 즉시 worker 측 보관 0) · TTL 기본 86400초 만료 시 삭제(마킹 아님) · inbox 상한 32건 초과 적재 거부 503 · 응답에 text echo 0(intent_id만).
- **Workers Logs/observability 켜지 말 것** — URL 경로 토큰·payload 기록 차단.
- 자동/추론 호출 금지: `save_intent`는 사용자가 `SAVE n,m`을 정확히 발화했을 때만(confirm 문구 불일치 = 거부).

## 불변 원칙
read 라인 = read-only 6 tool(장부 write 0) · save 라인 = inbox 휘발 적재만(최종 저장 = PC 러너 로컬 게이트, 자동 적용 0) · toy entry는 synthetic 한정 · real entry는 게이트 전건 통과 데이터만(레포에 실 데이터 0) · candidate-first(승격/병합 금지 룰 동봉) · fail-closed(secret 미주입 503·inbox 기본 잠금·누출 스캔) · non-retention(pull=drain·TTL 삭제·payload 로깅 0) · Workers Logs/observability 끄기 유지 · OAuth 미구현.
**운영 수칙**: stateless 서버라 도구 목록 변경 통지(listChanged)가 없음 — 도구 추가/변경 배포 후에는 각 클라이언트에서 커넥터 **재연결**(Claude) 또는 **제거 후 재추가**(ChatGPT)가 필요.
