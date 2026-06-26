# S4-2 — _maybe_promote_actor_by_gate canonicalization 판정 기록 (HOLD)

**owner token:** ✅ OWNER_TOKEN_APPROVED_FOR_S4_2_LOW_RISK_ENTRY (2026-06-26) — 발급됨.
**S4-2 entry baseline:** `c925656` (S4-1 closure 후, 코드 = v1.12.0 speaker axis 반영)
**판정:** **BINGGUPACK_S4_2_CANONICALIZATION_HOLD** — owner token 발급됐으나, 조사 결과
S4-1식 byte-identical 정본 이관은 **기술적 STOP 조건**(strangler 단방향 위반·import cycle 위험)에 해당.
코드 이관 보류, **characterization 은 이미 GREEN**(s4gap F1~F4), 본 문서는 docs-only 경계 기록.

---

## 1. 대상

`scripts/openbinggu_conversation_candidate_save.py:35` `_maybe_promote_actor_by_gate`
— 비human actor 가 사람 SAVE 발화로 기록된 문장을 고른 경우에만 `actor=human` 승격(G4① 분기·fail-closed).

## 2. 왜 HOLD인가 — S4-1 패턴 부적용 (Lane B 조사)

S4-1(`gate_log.py`)이 깨끗한 byte-identical 이관에 성공한 이유는 **gate-log append/판정이 scripts 의존 0**
(순수 file I/O + `binggupack.workspace.platform` 정본 + `binggupack.safety.gate_text` 정본만 경유)이었기 때문이다.
`_maybe_promote_actor_by_gate` 는 정반대다:

| 항목 | 내용 | 문제 |
|---|---|---|
| `capture_preview(text)` 호출 | 함수 내부에서 `scripts/openbinggu_conversation_capture_preview` 호출 | 그 모듈은 scripts 전용 5개(`openbinggu_label_kind_map`·`openbinggu_a0_node_dryrun`·`openbinggu_incoming_to_staging`·`watcher_batch_m1`·`binggu_canonical_semantic`)에 의존 — **binggupack 등가물 0** |
| `binggu_save_gate.gate_human_for` | 함수 내부 lazy import | S4-1 정본 re-export 라 이 부분만은 안전 |

→ 정본을 `binggupack/safety/` 에 두려면 **`binggupack → scripts` 역방향 의존**이 강제된다.
이 리포의 strangler 원칙은 코드 주석에 **명시적으로 단방향**(`binggupack/capture/buffer.py:8`·`cli.py:8` "scripts 역참조 0 — strangler 단방향")으로 못박혀 있다.

**걸린 STOP 조건:** ① binggupack→scripts 역의존 발생 · ② strangler 단방향 위반 · ③ byte-identical
semantic change ≠ 0 위험(capture_preview 경로 재배선) · ④ actual write core 결합(actor 승격 = G4① 게이트 직결).

## 3. fallback 으로도 회피 불가

S4-1 패턴(try import 정본 / except byte-identical 폴백)을 써도, 폴백 본문이 `capture_preview` + scripts 모듈
전부를 다시 참조해야 하므로 **폴백 자체가 scripts 의존을 해소하지 못한다.** 패턴 부적용.

## 4. 현재 안전 상태 (이관 없이도 확보됨)

- **characterization F1~F4 = s4gap GREEN** (41/41): F1(이미 human 무승격)·F2(기록 존재 승격)·F3(미기록 승격0)·F4(sgate 예외 fail-closed). default-deny/fail-closed 4분기 전수 pin 완료.
- `_maybe_promote` 는 actual write/save/ledger 를 **직접 하지 않는다**(ctx 변형만). write 는 후단 `save_selected`→`staging_apply`.
- 즉 **이동하지 않아도 동작은 이미 고정·검증**되어 있다. 이관 이득(중복 제거) < 위험(strangler 위반·cycle).

## 5. 재진입 조건

S4-2 canonicalization 은 다음 **선결 조건** 충족 후에만 재검토:
- `capture_preview` (및 그 scripts 의존 체인)를 먼저 `binggupack` 으로 정본화(별도 대형 phase — S4 범위 밖).
- 그 후에야 `_maybe_promote` 가 scripts 역참조 없이 binggupack 정본으로 이동 가능.
- 재진입 시에도 fail-closed 4분기 전수 pin 재확인 + byte-identical + owner token 범위 재확인.

## 6. 금지선 준수 (본 작업)

- 코드 변경 **0** — docs-only. actual write core(`staging_apply`+`save_selected`+`commit_selected`) 미접촉.
- G4_no_auto 3중·actor/confirm/token 흐름·dry_run/actual save·ledger write 경로 변경 0.
- production write 0 · OpenCrab ingest 0 · PyPI 0 · tag/release 0 · pyproject/version 변경 0.

---

## 7. S4-3 ~ S4-6 경계 정리 (actual write core 도달 · 별도 token)

| 단계 | 대상 | write core 도달 | 별도 owner token | 비고 |
|---|---|---|---|---|
| **S4-3** | H·I·J·K (`deprecate_item`/`set_review_due`/`resolve_review`/`classify_harvest_item`) | 간접(sqlite write·G4③) | **필요** | 4함수 동시(③ 분리 금지). **단 S4-2와 동일 결합 리스크 선검토 필수** — deprecate_g3 도 scripts 의존 체인 확인 후 판정 |
| **S4-4** | C·D (`tombstone`+`StagingDB` write_lock/snapshot/audit/verify/store_checksum) | 강의존(write 인프라) | **필요** | staging_apply 트랜잭션 토대. store_checksum anchor `210e04611a157877`·integrity_check=ok 보존 필수 |
| **S4-5** | A (`c2_check`) | 강결합(G4②·B 게이트) | **필요** | B(staging_apply)와 함께 검토. 판정 순서 byte-identical 고정 전제 |
| **S4-6** (마지막/**영구 HOLD**) | B `staging_apply` + E `save_selected` + G `commit_selected` | **= actual write core 본체** | 필요 + **영구 HOLD 후보** | ledger INSERT 본체 + G4① + 위임. 이번 지시로 **절대 구현 안 함** |

**경계 원칙:**
- S4-2~S4-5 = low-risk helper/canonicalization 범위지만, **각 단계 strangler 결합도 선검토 필수**(S4-2가 보여줬듯, "순수 함수"여도 scripts 의존 체인으로 이관 불가일 수 있음).
- 각 단계 **별도 owner token** — 한 token 이 다음 단계로 전이 안 됨.
- **S4-6 만이 actual write core 본체** — 마지막 또는 영구 HOLD.
- 전 단계 공통 불변식: byte-identical 위치 이동만 · semantic change 0 · G4_no_auto 3중 한 층도 약화 0 · re-export 무변경.

## 8. 다음 단계

- **S4-3** 진입 전 — deprecate_g3 4함수의 scripts 의존 체인을 **S4-2처럼 먼저 조사**(strangler 결합도 판정) → 이관 가능하면 별도 owner token 요청, 불가하면 본 문서처럼 HOLD 기록.
- **actual write core(S4-6)는 마지막 또는 영구 HOLD.**
- owner token 없이 S4-3 이상 진입 금지.
