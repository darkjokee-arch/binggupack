# save-gate S4 canonicalization line — FINAL CLOSURE

**기준선:** main/origin = `ccc13d2` (S4-3 closure 후)
**판정:** **BINGGUPACK_S4_CANONICALIZATION_LINE_FINAL_CLOSURE_GO**
**성격:** docs-only · 코드 변경 0 · actual write core 미접촉.

S4 gate-critical write core 모듈화 라인을 마무리한다. 실측 누적 결론:
**정본화 가능한 깨끗한 케이스는 S4-1(gate_log) 하나뿐이고, S4-2~S4-6 은 전부 scripts 의존 체인 또는
actual write core/StagingDB write 인프라 결합으로 byte-identical 이관 불가 → HOLD.**

---

## 1. S4-1 ~ S4-6 최종 상태표

| 단계 | 대상 | 상태 | 사유 (실측) | 정본 |
|---|---|---|---|---|
| **S4-1** | L·M·N·O (`gate_record`/`gate_human_for`/`write_last_preview`/`gate_record_from_prompt` + resolver/_load) | **✅ DONE** | scripts 의존 0 — 순수 file I/O + `binggupack.workspace.platform`(S1)·`binggupack.safety.gate_text`(S3) 정본만 경유. byte-identical·semantic change 0. | `binggupack/safety/gate_log.py` |
| **S4-2** | F (`_maybe_promote_actor_by_gate`) | **HOLD** | `capture_preview()`(scripts 전용·package 등가물 0인 모듈 5개 의존) 호출 → binggupack→scripts 역의존 = strangler 단방향 위반. | scripts 잔류 |
| **S4-3** | H·I·J·K (deprecate_g3 4함수) | **HOLD** | `StagingDB`(write 인프라)+`write_lock`/`BEGIN`/`UPDATE`/`INSERT`/`audit_append`/`store_checksum` = actual sqlite write 직접 + scripts 의존(`_hash`/`_now_iso`/`is_confirm_actor`/`open_staging`). | scripts 잔류 |
| **S4-4** | C·D (`tombstone` + `StagingDB` write_lock/snapshot/audit_append/verify_chain/store_checksum/__init__) | **HOLD** | **actual write 인프라 본체** — `write_lock`(O_EXCL)·`audit_append`(INSERT audit_log)·`store_checksum`(anchor)·`snapshot`(file copy)·`tombstone`(UPDATE state). candidate_save/deprecate_g3/capture_to_save 전부 의존하는 토대. | scripts 잔류 |
| **S4-5** | A (`c2_check`) | **HOLD** | `staging_apply`(B·line 200)와 **같은 파일**(`openbinggu_staging_write_selftest.py`)·강결합 — B가 c2_check 호출. scripts 의존(`_hash`). actual write core 진입 게이트. | scripts 잔류 |
| **S4-6** | B `staging_apply` + E `save_selected` + G `commit_selected` | **FINAL HOLD (영구/마지막)** | **actual write core 본체** — ledger INSERT/UPDATE 트랜잭션(`BEGIN`/`COMMIT`/`ROLLBACK`) + G4①(L68) + 위임 진입점. **어떤 owner token 으로도 이번 라인에서 미접촉.** | scripts 잔류 |

## 2. 메타 결론 — S4 라인 종료

- **S4-1 만 이관 완료.** 나머지 5단계는 전부 HOLD. 이유의 본질:
  - S4-2 = scripts 핵심 파이프라인(`capture_preview`) 결합.
  - S4-3~S4-5 = `StagingDB` write 인프라/sqlite write 결합.
  - S4-6 = actual write core 본체.
- **characterization 은 전건 GREEN**(s4gap 41/41: A2·E1b·B5·F1~F4·D11·H4·H5·J3·K4·L2·N2·O3·O4·A4·A6·A10·B9·B10·C2·C3·D3·D9·D10·E3·E6·E7). 즉 **이관하지 않아도 동작은 전부 고정·검증**되어 있다. 안전망은 완비, 모듈 이동만 보류.
- **이관 이득(중복 제거) < 위험(strangler 위반/actual write core 접촉).** 무리한 이관은 gate-critical 파손 위험.

## 3. future re-entry 조건 (선결 대형 phase)

| 재진입 대상 | 선결 조건 |
|---|---|
| **S4-2** | `capture_preview`(및 scripts 의존 체인 a0/v011/watcher/label_kind_map/canonical_semantic)를 먼저 `binggupack` package 로 정본화 |
| **S4-3 ~ S4-5** | `StagingDB`/write infrastructure(write_lock/snapshot/audit/store_checksum)를 **별도 대형 phase** 로 먼저 package 화 + `is_confirm_actor`/`open_staging` 등 scripts helper 이관 + dry_run/actual save/ledger/G4_no_auto/confirm/token/actor gate 의 **semantic lock 테스트** 선행 |
| **S4-6** | **마지막 또는 영구 HOLD.** 이번 지시로 절대 구현 안 함. actual write core 는 이동 이득 < 위험(S3_CLOSURE §5-1)이라 영구 HOLD 후보. |

## 4. 금지선 준수 (S4 final closure 작업)

- 코드 변경 **0** — docs-only. actual write core 미접촉.
- staging_apply/save_selected/commit_selected/deprecate_g3 semantic 변경 0 · G4_no_auto/G4②/G4③·actor/confirm/token 판정 약화 0 · dry_run/actual save·ledger write 경로 변경 0.
- production write 0 · OpenCrab ingest 0 · PyPI 0 · tag/release 0 · pyproject/version 변경 0.

## 5. 다음 phase 후보 (S4 라인 밖)

S4 모듈화를 더 진행하려면 다음이 전제다(전부 별도 대형 phase·별도 owner token):
1. **capture_preview package 화** — S4-2 잠금 해제 선결.
2. **StagingDB/write infrastructure package 화** — S4-3~S4-5 잠금 해제 선결. actual write core 토대라 대량 characterization + semantic lock 테스트 필수.
3. **actual write core(S4-6)** — 영구 HOLD 후보. 이동하지 않는 것이 기본 결정.

**S4 canonicalization 라인은 본 문서로 종료한다(FINAL CLOSURE). 안전망(characterization 41/41)은 완비, S4-1 정본화 완료, 잔여는 선결 phase 전까지 scripts 잔류·HOLD.**
