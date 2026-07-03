# save-intent 라이브 E2E 결과 (폰/커넥터 → PC 러너 → candidate 저장)

자동 캡처가 아니라 **사람 승인 저장 경로**의 라이브 검증 기록입니다. 저장은 미리보기 후 `SAVE n`(정확한 confirm)을 사람이 발화했을 때만, candidate-only로 진행됩니다.

---

## 흐름 (정본)

```
폰/커넥터(save_mcp MCP)  : conversation_capture_preview → 후보 1~10 확인 → "SAVE n" → save_intent
  → worker inbox(Durable Object)에 휘발 적재 (inbox 평소 잠김, SAVE 시에만 열림)
PC 러너(HMAC)            : inbox pull(drain) → 로컬 게이트(process_outbox = save_selected)
  → 로컬 장부(ledger.sqlite)에 candidate 저장
```

- worker는 **전달 통로**일 뿐 — 장부 write 0, 최종 저장 권한 = PC 러너의 사람 confirm 게이트
- `actor=auto`·confirm 누락/불일치·preview 미확인은 전부 BLOCK (자동 저장 구조적 불가)

## live 러너 (`scripts/openbinggu_save_intent_live_runner.py`)

라이브 worker HMAC pull → 로컬 outbox(temp) → `process_outbox`(실/지정 ledger).

- **기본 dry-run/temp.** 실 장부 저장은 `--real-ledger <p> --confirm "LIVE SAVE REHEARSAL"` 명시 필요
- 단일 흐름 `enable → (inject) → pull → process → finally disable` — **disable이 finally로 보장**
- **enable try/finally 안전 보강**: enable 호출을 try 블록 안으로 옮겨, enable이 실패(예: 서명 거부)해도 finally 경로로 disable을 일관 시도. enable 실패=inbox 미개방이므로 disable 실패는 비치명(보고만)
- selftest 18/18 GATE=GO (기본 temp·real 게이트·malformed write 0·rollback·inject 흐름·enable 예외 finally 정리)
- 출력은 count/flag/reason만 — secret/token/URL 평문 출력 0

## save_mcp worker 신형 v2 서명 배포

서명 검증을 구형(`ts.bodyhash`)에서 **신형 v2(`ts.METHOD.path.bodyhash`)**로 정합.

- 서버 검증 단일 출처 = `save_common.verifySig` — **신형 v2 우선 검증. 현재 `SAVE_SIG_V2_ONLY=1` 적용 → 구형(legacy) 서명 거부(신형 v2 전용)**
- repo 정본이 이미 신형 → 배포 소스(workers_port)를 repo와 동기화(save_common + save_intent_mcp/v2 + capture_preview) 후 save_mcp worker만 재배포
- 배포 후 smoke: 신형 admin/disable **200**(구형 서명 시절의 401 해소) · tools/list(conversation_capture_preview, save_intent) 유지 · capture_preview read-only(nothing_saved) · save_intent **inbox_disabled**. (배포 직후엔 하위호환이었고, 이후 `SAVE_SIG_V2_ONLY=1` 전환으로 **구형 legacy disable = 401** 실측)

## 실 저장 E2E (1회 완주)

단일 흐름으로 폰 저장 경로 전 구간을 실 장부까지 완주:

- candidate **+1** (실행 전 → 실행 후, `binggu status`로 확인)
- 저장 결과 = **판단** 도장 candidate 1건(80자 발췌, 증빙 evidence 포함), `promotion_allowed=0`·`confirmed=0`·active 자동 전이 0
- **audit chain INTACT** · **inbox disabled 복귀** · rollback 불필요
- 리허설용 합성 candidate는 검증 후 **`deprecate`(보존형 제외)로 정리 완료** — 물리 삭제 아님(스냅샷 보존, deprecated 목록 잔존)

## Hardening 적용 (SAVE_SIG_V2_ONLY=1)

- **`SAVE_SIG_V2_ONLY=1` 적용됨** — save_mcp worker가 **신형 v2 서명 전용**. 구형(legacy) 서명은 거부됩니다(legacy admin/disable **401** 실측, 신형 disable 200). 운영 경로(live_runner / `binggu hosted pull`)는 전부 신형 v2라 영향 없음.

## 안전 요약

자동 저장 아님(사람 `SAVE n` 게이트) · candidate-only(promotion 0·confirmed 0) · 원문 전문 미저장(80자 발췌) · OpenCrab export/push 0 · inbox 평소 잠김(fail-closed) · secret/token/URL 평문 노출 0.
