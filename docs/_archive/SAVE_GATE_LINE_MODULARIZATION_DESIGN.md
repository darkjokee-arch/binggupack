# save_gate 라인 모듈화 설계 (design-only, 구현 0)

**기준:** main/origin = fc25e4f
**목적:** save_gate 라인 전체를 설계·위험분해·이관순서로만 정리. **코드 이동/변경 0.**
**최종 판정:** **SPLIT_REQUIRED** (순수 helper는 단계적 migrate 가능, actual-write/gate-critical은 HOLD 또는 최후).

---

## 1. save_gate 라인 전체 구조
```
[hook] binggu_save_gate_hook → binggu_save_gate.gate_record_from_prompt  ──append── save_gate_log.jsonl
[CLI]  binggu.py cmd_save → save_selected(actor="human")
            ├─ binggu_save_gate.gate_human_for         (gate log READ → actor 승격 증거)
            ├─ openbinggu_staging_write_selftest.staging_apply ──WRITE── staging sqlite  ★유일 write 본체
            └─ openbinggu_deprecate_and_remind_g3.set_review_due ──WRITE── judgment_reviews
[adapter] binggu_capture_to_save.commit_selected → save_selected (HOLD, phase8a)
[MCP]  handlers._u_save_candidate → (dry_run 기본 True) / False 시 open_g3 temp + save_selected(actor=reader) → 항상 G4_no_auto BLOCK
[UX]   binggupack/cli/interactive_save (이미 이관됨, write 0, non-TTY fail-closed)
```

## 2. 파일별 역할
| 파일 | LOC | 역할 | write |
|---|---|---|---|
| binggu_save_gate.py | 303 | 게이트 기록장(사람 SAVE 발화 hash) + preview cache | append jsonl/json (ledger 무관) |
| openbinggu_conversation_candidate_save.py | 303 | save_selected — actor/confirm/preview 게이트 → staging 위임 | 0(위임) |
| binggu_capture_to_save.py | 164 | capture→저장 어댑터(commit_selected) | 0(위임) |
| binggu_platform.py | 209 | **storage resolver**(home/ledger 경로 계산) | **0** |
| openbinggu_path_safety_gate.py | 179 | 경로 분석 게이트(MCP path 입력) | **0** |
| openbinggu_staging_write_selftest.py | 375 | **StagingDB + staging_apply/tombstone — 유일 ledger write 본체** + c2_check(G4) | **★실제 INSERT/UPDATE** |
| openbinggu_deprecate_and_remind_g3.py | 525 | open_g3 + deprecate/review write(4함수 G4) | sqlite write |
| openbinggu_mcp_server_handlers.py | — | MCP 디스패처(dry_run 기본 True·actor=reader 하드오버라이드) | 0(위임) |
| binggupack/cli/interactive_save.py | 127 | 대화형 UX(confirm phrase 구성·non-TTY fail-closed) | **0**(이미 이관됨) |

## 3. 호출 graph (요약)
- staging_write_selftest = 32+ 모듈이 import(write 본체·OPERATING_PATHS). 라인 최하단 코어.
- binggu_platform = 27 모듈 import(resolver). 라인 최하단 코어.
- save_selected = binggu.py·to_save·MCP·hosted_inbox 등 다수.
- save_gate = hook·binggu.py·candidate_save(lazy)·MCP.

## 4. actual write path (단일 집중)
**`staging_apply()` L202~220(INSERT nodes/edges/evidence/applied_registry), `audit_append` L129, `tombstone` L235(UPDATE)** — 전부 `openbinggu_staging_write_selftest.py`. write_lock(O_EXCL)+BEGIN/COMMIT/ROLLBACK. 다른 모든 파일은 이리로 위임. 보조 write = save_gate의 append-only jsonl/json(ledger 무관).

## 5. dry_run / preview path
- MCP handlers: `dry_run` 기본 **True**(write 0 PREVIEW). False(opt-out) 시에도 actor=reader 하드오버라이드 → G4 BLOCK.
- build_save_commands/interactive_save: 명령 문자열/안내만(저장 0).
- save_selected/staging_apply: dry_run 플래그 없음 — 게이트(c2/actor/confirm) 통과해야만 write.

## 6. actor / confirm / token 흐름
- **actor 생성 0**: binggu.py CLI만 `{"actor":"human"}` 하드코딩(키보드 발화 경유). MCP=reader 하드오버라이드. save_gate log 증거 있을 때만 `_maybe_promote_actor_by_gate` 비human→human 승격(fail-closed).
- **confirm 생성 0**: 전부 인자 전달 + `"SAVE <indices>"` 정확일치 대조. interactive의 build_confirm_phrase는 *구성*만(사람이 재타이핑).
- token/phrase: 인자 또는 구성, 생성/자동제출 0.

## 7. resolver 의존성
- binggu_platform(resolver 본체, write 0)을 **binggu_save_gate만 직접 의존**(home 단일화·stage0 split-brain 차단). staging/g3/handlers는 OPERATING_PATHS(env 거부목록)만. path_safety_gate·interactive_save 무의존.

## 8. non-TTY fail-closed 경로
- **interactive_save `_require_tty()`→sys.exit(2)** (유일). save_gate hook은 반대로 항상 exit 0·stdout 침묵(세션 무방해). save_selected/staging은 db 인자형 라이브러리(TTY 무관).

## 9. G4_no_auto block 경로 (3중 방어)
1. `save_selected` L68 — human-allowlist (actor != human → block)
2. `staging_write_selftest.c2_check` L169 — auto/reader 차단(staging 층)
3. `deprecate_and_remind_g3` 4 write 함수 — is_confirm_actor allowlist
→ 어느 한 층이 뚫려도 다음 층이 차단. **이 3중 구조는 모듈 이동 시 절대 분리/약화 금지.**

## 10. pure helper 후보 (migrate 가능, write 0)
- **binggu_platform.py 전체** → `binggupack/workspace/`(placeholder 존재). resolver 정본·write 0. 단 호출처 27 → wrapper re-export 필수, stage0 save_gate 의존 정합 확인.
- **openbinggu_path_safety_gate.py 전체** → `binggupack/safety/`(placeholder 존재). 순수 판정·write 0.
- save_gate의 `parse_save_indices`/`sent_hash`/`has_trigger_token`/`_norm`(순수 부분만).
- to_save `build_save_commands`/`_preview_id`(순수, phase8a SPLIT 후보).
- interactive `build_confirm_phrase`(이미 binggupack 내).

## 11. gate-critical HOLD 후보 (이관 최후 또는 영구 잔류)
- **staging_apply/tombstone/c2_check** (write 본체 + G4) — 최고위험.
- **save_selected** (actor/confirm 게이트 + G4 + staging 위임).
- **deprecate_g3 write 4함수** (G4).
- **commit_selected** (save_selected 위임·actual write, phase8a HOLD).
- save_gate `gate_record`/`gate_human_for`/`write_last_preview` (gate log/preview cache write).

## 12. 권장 phase 분할안
- **phase S1 (저위험·순수 resolver)**: binggu_platform → binggupack/workspace/. characterization(경로 계산 결정성·BINGGU_HOME opt-in·split-brain 정합) 선행. 호출처 27 wrapper.
- **phase S2 (저위험·순수 게이트판정)**: path_safety_gate → binggupack/safety/. characterization(verdict/reason_code) 선행.
- **phase S3 (순수 helper 분리)**: save_gate·to_save·interactive의 pure helper만 binggupack로(gate write는 잔류). split 정밀.
- **phase S4+ (gate-critical, 별도 최고위험 트랙)**: staging_apply·save_selected·G4·commit_selected. **대량 characterization(G4 3중·confirm 대조·운영 store 거부·audit chain·write_lock) 선행 필수.** 또는 영구 scripts 잔류 + binggupack 얇은 facade만.
- **actual write path(staging_apply)는 마지막 또는 HOLD** — 이동 이득 < 위험.

## 13. 필요한 회귀테스트 목록 (이관 전 characterization 필수)
- G4_no_auto 3중 방어 각 지점(save_selected L68 / c2_check L169 / deprecate_g3 4함수) actor=auto/reader/none → BLOCK.
- confirm 정확일치/불일치/누락 → save/BLOCK.
- preview_id 일치/불일치 게이트.
- 운영 store(OPERATING_PATHS) mtime 불변(temp 전용).
- audit chain INTACT·candidate-only·promotion 0·원문 전문 미저장.
- write_lock(O_EXCL) 경합·WAL busy_timeout fail-closed.
- binggu_platform 경로 계산 결정성·BINGGU_HOME opt-in·gate scope==ledger scope(stage0 불변).
- non-TTY fail-closed(interactive sys.exit 2).
- MCP dry_run 기본 True·actor=reader 하드오버라이드→항상 G4 BLOCK.

## 14. 최종 판정
**SPLIT_REQUIRED → DESIGN_READY.** 라인은 (a)순수 resolver/판정(binggu_platform·path_safety_gate, write 0)과 (b)gate-critical write 코어(staging_apply·save_selected·G4 3중·deprecate_g3)로 명확히 분리됨. 저위험 (a)는 단계적 migrate(S1~S3) 가능, 고위험 (b)는 대량 characterization 선행 후 S4 또는 영구 HOLD. **actual write 본체(staging_apply)와 G4 3중 방어는 분리·약화 절대 금지.** 다음 실행 phase는 S1(binggu_platform→workspace)부터 단일 lane characterization-first 권장.
