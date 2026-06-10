> OpenBinggu is the legacy/internal codename for BingguPack.

# BingguPack Phase 4 — Reviewer / Confirmed Flow 설계 (DESIGN ONLY)

> 목적: candidate를 **사람이 검토해 confirmed로 올리는** 실제 흐름 설계. **confirmed 생성/apply는 실행하지 않음**(설계만).
> ⚠️ 코드·write·confirmed·apply·promote·push·OpenCrab·Neo4j 0. confirmed_created=0 불변.
> 기준: reviewer auth/token selftest **20/20**(`OPENBINGGU_REVIEWER_AUTH_SESSION_TOKEN_DESIGN`·S1~S19) · review resolver preview · confirmed governance(candidate→review_pending→confirmed/rejected).

## 1. 흐름 (candidate → confirmed, 사람 승인 게이트)
```
candidate(staging) → review_pending(큐 적재) → [사람 검토 + reviewer 인증]
  → CONFIRM_ALLOWED(preview) → [owner/reviewer 명시 승인] → confirmed(생성)  ← Phase 4 실행은 HOLD
                                                          ↘ rejected / defer
```
- **Phase 4 = 흐름·게이트 설계까지.** 실제 confirmed 생성·apply는 별도 중대결정(HOLD). 현재 모든 단계 `confirmed_created=0`.

## 2. reviewer auth/token 연결 (기존 20/20)
- 토큰 모델 = session lease(만료·취소·issuer=owner·scope·nonce·clock skew). selftest S1~S19 PASS(20/20).
- **R2 review decision preview**: reviewer 토큰(scope `review_decision:preview`)으로 confirm/reject/defer **판정 preview만**(CONFIRM_ALLOWED까지). 실제 confirmed 생성 0.
- **R3 owner approval**: confirmed 생성·apply는 owner role + 단발 grant + 명시 승인 후에만(승인≠실행, 실행은 별도 GO).
- enforce_access: reader/auto는 confirm/apply 자동 차단(auto_path_forbidden). apply 항상 `apply_to_graph_store_HOLD`.

## 3. confirmed 전 preview (CLI/UI)
- 사람이 보는 것: candidate 노드/엣지 요약 + evidence_refs + 충돌(contradicts) 표시 + governance 판정(G4/G6). raw 경로/secret 미출력(id·count·hash).
- preview는 read-only. "이걸 confirmed로 올릴지" 결정만 사람에게. 자동 confirmed 0.

## 4. rollback
- confirmed apply(미래 실행 시): apply 전 backup snapshot → 실패 시 rollback(checksum 원복, Phase 2 staging 패턴 재사용).
- WAL checkpoint(TRUNCATE) 후 snapshot + 복원 시 -wal/-shm 삭제(real_staging 교훈).
- rollback도 C-2 2단계 + audit 기록.

## 5. confirmed_created=0 유지 selftest 계획 (구현 시, 전부 synthetic)
| # | 항목 | 기대 |
|---|---|---|
| C1 | reader/auto가 confirm 시도 | BLOCK auto_path_forbidden, confirmed_created=0 |
| C2 | reviewer 토큰 review preview | CONFIRM_ALLOWED preview, confirmed_created=0 |
| C3 | owner approval 없는 confirm | BLOCK, confirmed_created=0 |
| C4 | 만료/철회 토큰 confirm | BLOCK(token_expired/revoked) |
| C5 | confirm→apply 시도 | 항상 apply_to_graph_store_HOLD |
| C6 | rollback(apply 실패 주입) | snapshot 원복, checksum == before |
| C7 | audit chain | confirm/preview/reject 기록, 변조 시 BROKEN |
| C8 | operating_store_unchanged | True (confirmed/apply 0) |
| C9 | raw_leak=0 | id/reason_code/hash만 |
- 공통: confirmed_created=0 · applied(운영)=0 · upload=0 · push=0.

## 6. apply / promote HOLD 경계
- **Phase 4 = preview/판정/흐름 설계까지.** confirmed **생성**·graph store **apply**·staging→운영 **promote**는 전부 별도 중대결정(HOLD).
- 실 reviewer 인증 채널(외부 모델/사람)·세션 토큰 실 발급(서명 키 관리)은 미구현(reviewer 설계 §10).

## 7. HOLD
confirmed 생성·apply·promote · 운영 store write · OpenCrab finalize/upload · Neo4j · 실 reviewer 인증 채널/실 토큰 발급(서명 키) · MCP write 노출 · fix5(enforce_access multi-user 실엔진).

## 8. 다음
- 본 설계 승인 후 Phase 4 synthetic 구현(C1~C9, confirmed_created=0 강제, temp/synthetic) — 별도 GO.
- 실 reviewer 인증/서명 키 관리는 그 다음(별도 중대결정).
