# save-gate 라인 — S3 종료 + S4 gate-critical HOLD 문서 (docs only, 코드 변경 0)

**기준:** main/origin = 12284b9
**목적:** save_gate 라인 모듈화의 S3 종료 기준과 S4 HOLD 기준을 코드 변경 없이 명문화.
**판정:** **S3 CLOSED · S4 HOLD(owner approval 전 금지).**

---

## 1. S1~S3 완료 목록 (정본화 완료, main 확정)
| 단계 | 이관 | 착지 | 정본화 |
|---|---|---|---|
| stage0 | save_gate 경로해석 단일화(split-brain 차단) | save_gate `gate_home`/`gate_path` | resolver 경유 |
| S1 | storage resolver `binggu_platform` | `binggupack/workspace/platform.py` | ✅ |
| S2 | 경로 안전 게이트 `path_safety_gate` | `binggupack/safety/path_safety.py` | ✅ |
| S3-B | `parse_save_indices` + `SAVE_TRIGGER_RE` | `binggupack/safety/gate_text.py` | ✅ |
| S3-C | `sent_hash` + `_norm` | `binggupack/safety/gate_text.py` | ✅ |

**확립 패턴:** characterization-first → 정본 이관 → scripts wrapper/import 이동(폴백 byte-identical) → **게이트 로직(G4/actor/confirm/write) diff 0** → 외부 호출처는 re-export 경유 무변경(예: autopush `SGATE.sent_hash`) → 회귀 전종 + ledger integrity_check + 좁은 구간 mtime.

## 2. S3 종료 — 미이관 확정 항목
### 2-1. `has_trigger_token` — 미이관 (종료)
- 외부 호출처 **0**(save_gate 내부 hook 빠른차단 substring 체크 전용).
- 순수하나 이관 이득 낮음(고립 self-use). save_gate 파일 추가 touch 대비 효익 낮음.
- 결정: **save_gate 잔류**. 정본화 불필요.

### 2-2. `build_save_commands` — HOLD (phase8a 유지)
- to_save 인접(`scripts/binggu_capture_to_save.py`). confirm 문자열 **생성** 포함.
- phase8a 안전검토에서 to_save 전체 HOLD 확정(commit_selected가 save_selected 직결).
- 결정: **to_save와 함께 S4 영역 또는 영구 HOLD**. 단독 split 안 함.

## 3. S4 gate-critical 범위 (구현 금지·HOLD)
| 대상 | 파일 | 위험 |
|---|---|---|
| `staging_apply` / `tombstone` | openbinggu_staging_write_selftest.py | **유일 actual ledger write 본체**(INSERT/UPDATE·write_lock·트랜잭션) |
| `save_selected` | openbinggu_conversation_candidate_save.py | actor/confirm/preview 게이트 + staging 위임 + G4 |
| `c2_check` | openbinggu_staging_write_selftest.py | G4 3중 방어 ② |
| `deprecate_g3` write 4함수 | openbinggu_deprecate_and_remind_g3.py | G4 3중 방어 ③ + sqlite write |
| `commit_selected` | binggu_capture_to_save.py | save_selected 위임·actual write |
| `gate_record`/`gate_human_for`/`write_last_preview` | binggu_save_gate.py | gate log append write + actor 승격 판정 |

## 4. S4 진입 전 필요한 대량 characterization (선행 의무)
- **actual write path**: staging_apply INSERT(nodes/edges/evidence/applied_registry) 정상/롤백.
- **dry_run path**: MCP dry_run=True(기본) write 0 PREVIEW.
- **G4 block path (3중)**: save_selected L68(human-allowlist)·c2_check(auto/reader)·deprecate_g3 4함수 각 actor=auto/reader/none/누락 → BLOCK.
- **actor/confirm/token path**: actor=human 외 BLOCK·confirm 정확일치/불일치/누락·preview_id 일치/불일치.
- **non-TTY fail-closed**: interactive_save sys.exit(2).
- **ledger write transaction**: BEGIN/COMMIT/ROLLBACK·wal_abort/checksum_mismatch/backup_fail 주입 시 ROLLBACK.
- **write_lock**: O_EXCL 경합·WAL busy_timeout fail-closed.
- **snapshot/preview 동반성**: save 시 snapshots/ + last_preview_candidates.json 동반(정상 SAVE 시그니처).
- **sqlite integrity_check=ok** 보존.
- **operating home no unintended side effect**: 운영 ~/.binggupack 의도 외 변경 0(temp 격리 검증).
- audit chain INTACT·candidate-only·promotion 0·원문 전문 미저장.

## 5. S4 원칙 (절대)
1. **actual write 본체(`staging_apply`)는 마지막 또는 영구 HOLD** — 이동 이득 < 위험.
2. **G4_no_auto 3중 방어(save_selected L68 + c2_check + deprecate_g3 4함수)는 분리·약화 절대 금지.** 한 층이라도 비활성/우회 0.
3. **`save_selected`/`staging_apply` 변경은 별도 owner approval 전 금지.** S1~S3 같은 helper 이관 자율 진행과 달리, S4는 owner의 명시 승인 토큰이 전제.
4. characterization(§4 전 항목) GREEN 없이는 S4 진입 불가.
5. S4 진입 시에도 actor/confirm/token/dry_run/ledger write 경로의 의미 변경 0(순수 위치 이동만, byte-identical).

## 6. 최종 판정
**S3 CLOSED** — resolver·path_safety·pure helper 3종(parse_save_indices/sent_hash/_norm) 정본화 완료. has_trigger_token·build_save_commands는 미이관 확정(사유 §2).
**S4 HOLD** — gate-critical(actual write core + G4 3중)은 owner approval + 대량 characterization 선행 전까지 구현 금지. save_gate 라인 모듈화는 저위험 영역 종료·고위험 영역 동결로 일단락.
