# S4 이후 구조 정리 리뷰 — Lane A (S4_POST_STRUCTURE_REVIEW)

**기준선:** HEAD = origin/main = `407656c` · version `1.13.0`
**성격:** 읽기/검증 전용 · **코드 변경 0** · 산출물 = 본 문서뿐
**판정(전체):** **GO** — S4-1 정본화 착지 정상, S4-2~S4-6 HOLD/FINAL HOLD 경계 무손상, crash fix(407656c)는 handler-only 재검증 완료.

> 모든 판정은 실제 실행 결과 기반(evidence-first). 추측 0.

---

## 0. 검증 실행 결과 (evidence)

| 명령 | 핵심 결과 |
|---|---|
| `git rev-parse HEAD` | `407656c…` = origin/main = baseline (일치) |
| `git show 407656c --stat` | `scripts/openbinggu_mcp_server_handlers.py` **1 file, 1 insertion** (handler-only) |
| `git diff --stat HEAD -- scripts/ binggupack/` | 빈 출력 = tracked 코드 미변경(작업 중 코드 0 변경) |
| `python scripts/openbinggu_mcp_server_handlers.py --selftest` | **GATE: GO** (save gate 4종 OK: dryrun_write0·confirm_mismatch_reject·auto_blocked_G4·operating_ledger_write_0) |
| `python scripts/openbinggu_conversation_candidate_save.py --selftest` | **19/19 PASS · GATE: GO** (G4_no_auto·confirm·A0·PII·rollback·speaker 전수) |
| `python scripts/binggu_capture_to_save.py` | **GATE=GO** (T1~T13 전부 PASS: actor=auto BLOCK·confirm 누락 BLOCK·preview_id 불일치 BLOCK·운영 store 불변) |

crash fix 위치(`scripts/openbinggu_mcp_server_handlers.py:128`):
```
+    os.makedirs(snap_dir, exist_ok=True)  # staging_apply snapshot 복사 대상 폴더 보장(없으면 FileNotFoundError로 stdio 루프 사망)
```
→ `_u_save_candidate` 핸들러 내부 1줄 추가뿐. write core(`save_selected`/`staging_apply`/`commit_selected`)·gate_log·G4·actor/confirm/token·ledger write path **전부 미접촉**(git show 로 diff 단일 파일·단일 라인 재확인).

---

## 1. 점검 항목별 구조 정리표

| 항목 | 현재 위치 | 정본화 가능 여부 | S4 HOLD 영향 | 위험도 | 다음 액션 | 결론 |
|---|---|---|---|---|---|---|
| **save_candidate temp_only 경계** | `scripts/openbinggu_mcp_server_handlers.py` `_u_save_candidate` | 불필요(이미 handler 격리) — MCP는 경로 입력 일절 무시·`tempfile.mkdtemp` 강제·운영 ledger 주입 구조적 불가 | 영향 0(write core 미접촉) | 낮음 | 없음(현행 유지) | **GO** |
| **commit_selected real-save 경계** | `scripts/binggu_capture_to_save.py` `commit_selected` (G) | HOLD — `save_selected` 직위임·actual write core 결합. S4-6 영역 | FINAL HOLD 대상(S4-6 G) | 높음 | 이관 금지(영구/마지막) | **HOLD** |
| **G4_no_auto BLOCK 경계** | 3중: `save_selected`(L70 human-allowlist) + `c2_check`(staging_write_selftest) + `deprecate_g3` 4함수 | 분리·약화 절대 금지(S3 §5-2) | S4-3/4/5/6 핵심 | 높음 | 3층 동시·byte-identical 외 이동 금지 | **HOLD** |
| **actor=human 저장 경계** | `save_selected`(L70 정확매칭+정규화) + `_maybe_promote_actor_by_gate`(F) | F는 HOLD(`capture_preview` scripts 역의존=strangler 위반) | S4-2 대상 | 중 | `capture_preview` package 화 선결 후 재진입 | **HOLD** |
| **"owner token보다 기술적 STOP 우선" 원칙** | `SAVE_GATE_S4_FINAL_CLOSURE.md` §1(S4-6) + governance `self-modifying 0` | 문서·코드로 박제됨 | S4-6 "어떤 owner token 으로도 미접촉" 명문화 | n/a | 원칙 유지·문서 보존 | **GO** |
| **S4-1~S4-6 중 FINAL HOLD 잔존** | S4-1 ✅DONE / S4-2~S4-5 HOLD / **S4-6 FINAL HOLD** | S4-1만 이관됨 | S4-6(B `staging_apply`+E `save_selected`+G `commit_selected`) = actual write core 본체 | 높음 | S4-6 영구 HOLD 후보·접촉 금지 | **STOP**(접촉 금지) |
| **write core ↔ handler 책임분리** | write core = scripts(`save_selected`/`staging_apply`/`commit_selected`) · handler = `_u_save_candidate`(게이트 재구현 0·위임만) | 책임분리 명확(handler는 temp DB·actor=reader 하드오버라이드만) | 양호 — handler 변경이 write core 의미 변경 0 | 낮음 | 현행 유지 | **GO** |
| **crash fix(407656c) handler-only 재검증** | `_u_save_candidate` snap_dir mkdir 1줄 | git show = 1 file/1 insertion·write core diff 0 | 영향 0 | 낮음 | 없음(검증 완료) | **GO** |

---

## 2. S4-1 착지 상태 (정본화 완료분)

- 정본: `binggupack/safety/gate_log.py` — `gate_record`/`gate_human_for`/`write_last_preview`/`gate_record_from_prompt` + resolver/`_load`/`GATE_WINDOW_SEC` byte-identical relocation.
- home resolver 는 `binggupack.workspace.platform`(S1) 경유, 파싱 helper 는 `binggupack.safety.gate_text`(S3) 경유 — 각 import 실패 시 동일 정책 폴백(byte-identical).
- **actual write core 와 별개 파일**: gate_log 는 gate-log append + 사람 발화 판정 read 만(운영 ledger 와 무관). 따라서 S4-1 이관이 G4/actor/confirm/token/dry_run/ledger write 경로를 건드리지 않음.
- 마지막 touch 커밋: `de8672e refactor: canonicalize S4 gate log helpers` — 이후 gate_log.py 변경 0.

---

## 3. FINAL HOLD 잔존 확인 (S4-6 = actual write core 본체)

`SAVE_GATE_S4_FINAL_CLOSURE.md` §1 S4-6 행 그대로:
- 대상: B `staging_apply` + E `save_selected` + G `commit_selected`.
- 사유: ledger INSERT/UPDATE 트랜잭션(BEGIN/COMMIT/ROLLBACK) + G4①(save_selected L70) + 위임 진입점.
- 명문: **"어떤 owner token 으로도 이번 라인에서 미접촉."** → 기술적 STOP이 owner token보다 우선하는 원칙의 코드 영역 적용.

본 리뷰는 이 경계를 읽기만 했으며 접촉/변경 0. S4-2~S4-5 HOLD 사유(strangler 단방향 위반 / StagingDB write 인프라 결합)도 변동 없음.

---

## 4. 다음단계 후보 분류 (GO / HOLD / STOP)

### GO (즉시 안전 — 코드 write core 미접촉)
- 본 구조 리뷰 문서화(완료) 및 release-readiness 문서 정리.
- S4-1 정본 경로 회귀 모니터링(selftest 주기 실행) — 이미 전건 GO.
- crash fix 회귀 가드 유지(MCP handlers selftest 의 `operating_ledger_write_0` 항목).

### HOLD (선결 대형 phase + 별도 owner token 필요)
- **S4-2** (`_maybe_promote_actor_by_gate` F): `capture_preview` 및 의존 체인(a0/v011/watcher/label_kind_map/canonical_semantic) package 정본화 선결.
- **S4-3~S4-5** (`deprecate_g3` 4함수 H·I·J·K / `tombstone`+`StagingDB` C·D / `c2_check` A): `StagingDB`/write infrastructure(write_lock/snapshot/audit/store_checksum) 별도 대형 phase package 화 + scripts helper(`is_confirm_actor`/`open_staging`) 이관 + semantic lock 테스트 선행.

### STOP (이번 라인 접촉 금지 — 영구/마지막 HOLD 후보)
- **S4-6** (`staging_apply` B + `save_selected` E + `commit_selected` G): actual write core 본체. **어떤 owner token 으로도 미접촉.** 이동 이득 < 위험(gate-critical 파손).
- **G4_no_auto 3중 방어 분리/약화**: 한 층이라도 비활성·우회 = STOP.
- production write / OpenCrab ingest / PyPI publish / gh release / git tag·push = STOP(본 작업 범위 밖).

---

## 5. 결론

S4 이후 구조는 **안정 착지** 상태다. S4-1(gate_log) 정본화는 write core 와 분리된 안전 영역에서 완료·회귀 GREEN, 나머지 S4-2~S4-6 은 명확한 사유로 scripts 잔류·HOLD, 특히 S4-6(actual write core)은 FINAL HOLD 로 owner token 무관 미접촉 원칙이 코드·문서 양쪽에 박제되어 있다. 407656c crash fix 는 handler 1줄로 write core 경계를 넘지 않음을 git show + 3종 selftest 로 재검증했다. **전체 GO.**
