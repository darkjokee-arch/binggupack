# save-gate S4 — gate-critical write core characterization 설계 (design-only)

**기준(원 설계):** main/origin = 978394f
**S4-1 entry baseline (재기준선 2026-06-26):** **`807dc7d`** (코드 = `10df67c`와 동일, 807dc7d는 README docs만 추가) — v1.12.0 speaker axis 반영. 이전 characterization closure baseline 은 `919e2ae`.
**선행 문서:** `docs/SAVE_GATE_S3_CLOSURE_S4_HOLD.md` §4(선행 의무) · §5(원칙)
**성격:** **design-only.** 구현 0 · 코드 이동 0 · semantic change 0. 본 문서는 S4 진입 전
"무엇을 pin 해야 byte-identical 이동이 안전한가"의 characterization 케이스 목록만 정리한다.
**판정:** **S4 HOLD 유지.** 본 설계는 owner approval 토큰의 전제 조건(§4 GREEN 정의)을 명문화할 뿐,
어떤 gate-critical 코드의 이동·변경도 승인하지 않는다.

**갱신 이력(2026-06-25, docs-only):** 본 문서 §2 GAP 전건을 tests-only characterization 으로 커버.
- 1차: **A2·E1b·B5·F1~F4·D11** / 2차 저위험: **H4·H5·J3·K4·L2·N2·O3·O4** /
  3차 고위험: **A4·A6·A10·B9·B10·C2·C3·D3·D9·D10·E3·E6·E7** (C-high, 본체 무수정 외부 호출+단언/monkeypatch)
- 추가 테스트(production touch 0): `scripts/openbinggu_s4_gap_characterization_selftest.py`
- 결과: **41/41 PASS · GATE GO · operating_store_unchanged=True · production/gate-critical touch 0**
- 전체 회귀 GREEN: staging 16/16 · candidate 13/13 · deprecate 23/23 · capture GO · save_gate 28/28 · S4 GAP 41/41
- **잔여 GAP 0 — S4 characterization coverage complete.** 그러나 **S4 implementation 은 여전히 HOLD.**
  본 갱신은 결과 반영(docs-only)일 뿐, actual write core
  (§4 S4-6: `staging_apply`+`save_selected`+`commit_selected`)는 **마지막/영구 HOLD 유지**.

**재기준선(2026-06-26, docs-only) — v1.12.0 speaker axis 반영:**
- **이전 closure baseline** `919e2ae` → **현재 S4-1 entry baseline** `807dc7d`(코드 = `10df67c`).
  그 사이 머지된 v1.12.0 speaker axis(`285f006`~`378f560`)가 gate-critical 본체 파일을 **additive 변경**했다.
- **speaker = 화자 축 metadata(owner/ai/None).** actor/confirm/token 흐름을 **대체·우회하지 않는다**(독립 메타 축).
  speaker audit 판정: **SPEAKER_AXIS_REBASELINE_GO · G4_no_auto 약화 0.**
- **변경 성격(전부 additive, gate/write 로직 무변):**
  - `save_selected(... , speaker=None)` 파라미터 추가 — actor/confirm/A0/PII 게이트 흐름 무변.
  - `staging_apply` INSERT 에 `speaker` 컬럼 1개 추가(`n.get("speaker")`) — BEGIN/COMMIT/ROLLBACK·c2_check·write_lock·audit 무변.
  - **base SCHEMA `nodes` 에 `speaker TEXT` 컬럼 추가** + ALTER migration(기존 ledger 비파괴·기존 행 NULL) + 신뢰도 테이블 신설.
  - `store_checksum` 은 **`speaker` 컬럼을 projection 에서 제외**(anchor `210e04611a157877` 유지) → B5·D10·D11 무결성 호환.
  - `commit_selected` / `deprecate_g3` 변경 **0**.
- **신규 6케이스(candidate_save selftest 13/13 → 19/19):**
  14 speaker 적재(owner) · 15 paired save(owner/ai) · 16 owner-only invariant ·
  17 pair duplicate block(`pair_partial_exists`) · 18 trust record(사람만·`actor=auto → G4_no_auto`) · 19 audit chain/tail anchor 무손상.
- **회귀 GREEN(`807dc7d` 기준):** staging 16/16 · candidate **19/19** · deprecate 23/23 · capture GO · save_gate 28/28 · **S4 GAP 41/41** · version SSOT 3/3 v1.12.0. **잔여 GAP 0.**
- **owner token request: ELIGIBLE(807dc7d 기준 재확정).** 단 **S4 implementation HOLD 유지**,
  actual write core(§4 S4-6: `staging_apply`+`save_selected`+`commit_selected`)는 **마지막/영구 HOLD**.
- **S4-1 착수 시:** byte-identical 이동 기준선은 **`807dc7d`**(919e2ae 아님). 대상(save_gate write/판정 L·M·N·O)은 speaker axis 미접촉.

---

## 0. 원칙 재확인 (S3_CLOSURE §5)

1. actual write 본체(`staging_apply`)는 **마지막 또는 영구 HOLD** — 이동 이득 < 위험.
2. **G4_no_auto 3중 방어**(save_selected L68 + c2_check + deprecate_g3 4함수)는 분리·약화 절대 금지.
3. `save_selected`/`staging_apply` 변경은 **별도 owner approval 토큰** 전제.
4. characterization(본 문서 전 케이스) GREEN 없이는 S4 진입 불가.
5. 진입해도 의미 변경 0 — **순수 위치 이동(byte-identical)만** 허용.

> 본 설계의 목표는 "테스트를 늘리는 것"이 아니라 **현재 동작의 완전한 스냅샷을 고정**해
> 이동 후 동일성을 기계적으로 증명할 수 있게 하는 것이다. 새 동작·새 분기 추가 금지.

---

## 1. S4 대상 인벤토리 (현 위치 = 모두 scripts/ 잔류)

| # | 대상 | 파일:정의줄 | 종류 | G4 방어층 |
|---|---|---|---|---|
| A | `c2_check` | openbinggu_staging_write_selftest.py:167 | 게이트(판정) | **② (actor auto/reader → G4_no_auto)** |
| B | `staging_apply` | openbinggu_staging_write_selftest.py:187 | **actual ledger write 본체** | — (c2 경유) |
| C | `tombstone` | openbinggu_staging_write_selftest.py:230 | state write | — |
| D | `StagingDB` (write_lock/snapshot/audit_append/verify_chain/verify_tail_state/store_checksum/__init__) | openbinggu_staging_write_selftest.py:37 | write 인프라 | 운영경로 거부 |
| E | `save_selected` | openbinggu_conversation_candidate_save.py:54 | 게이트+위임 | **① (actor!=human → G4_no_auto, L68)** |
| F | `_maybe_promote_actor_by_gate` | openbinggu_conversation_candidate_save.py:35 | actor 승격 | fail-closed |
| G | `commit_selected` | binggu_capture_to_save.py:55 | 위임 | preview_id 게이트 |
| H | `deprecate_item` | openbinggu_deprecate_and_remind_g3.py:76 | sqlite write | **③-a (is_confirm_actor)** |
| I | `set_review_due` | openbinggu_deprecate_and_remind_g3.py:120 | sqlite write | **③-b** |
| J | `resolve_review` | openbinggu_deprecate_and_remind_g3.py:159 | sqlite write | **③-c** |
| K | `classify_harvest_item` | openbinggu_deprecate_and_remind_g3.py:221 | sqlite write | **③-d** |
| L | `gate_record` | binggu_save_gate.py:121 | gate-log append | append-only |
| M | `gate_human_for` | binggu_save_gate.py:158 | 승격 판정(read) | 신선도 창 |
| N | `write_last_preview` | binggu_save_gate.py:175 | preview 영속(atomic) | hash-only |
| O | `gate_record_from_prompt` | binggu_save_gate.py:189 | hook 진입점 write | hash-only |

> **build_save_commands / has_trigger_token** 은 S3_CLOSURE §2에서 미이관 확정(저장 0/순수 self-use).
> S4 범위 밖. 본 문서 대상 아님.

---

## 2. 함수별 characterization 케이스 목록

표기: **EXP** = 고정해야 할 현재 반환/부작용. **EX-st** = 기존 selftest 가 이미 cover(케이스 id).
**GAP** = 이동 전 추가 pin 권장(현재 미커버 분기). 모든 케이스는 **temp DB / 운영 store 불변** 전제.

### A. `c2_check(db, pack, ctx)` → reason|None (B의 게이트)
| id | 입력 조건 | EXP(반환) | 기존 |
|---|---|---|---|
| A1 | actor=auto | `"G4_no_auto"` | EX staging#(간접) / candidate#3 |
| A2 | actor=reader | `"G4_no_auto"` | **s4gap A2-1** (auto=A2-2·human정상=A2-3) |
| A3 | edge.evidence_refs 빈/누락 | `"evidence_refs_missing"` | EX staging#4 |
| A4 | evidence.source_missing=True | `"freshness_source_missing"` | **s4gap A4** |
| A5 | source_hash≠captured_hash | `"freshness_source_hash_mismatch"` | EX staging#5 |
| A6 | redaction_policy≠"v1" | `"freshness_redaction_policy_changed"` | **s4gap A6** |
| A7 | applied_registry 중복 | `"duplicate_already_applied"` | EX staging#2,3 |
| A8 | ctx.backup_fail=True | `"backup_create_failed"` | EX staging#6 |
| A9 | 정상 | `None` | EX staging#1 |
| A10 | **판정 순서 고정**: actor→evidence_refs→freshness→duplicate→backup | 위 우선순위 그대로 | **s4gap A10** (4조합) |

### B. `staging_apply(db, pack, ctx, snap_dir, ts=None)` — **actual write 본체**
| id | 입력 | EXP | 기존 |
|---|---|---|---|
| B1 | 정상 pack | `{applied:True, button:"enabled", snapshot}` + nodes/edges/evidence/applied_registry INSERT + audit ALLOW | EX staging#1 |
| B2 | c2_check reason 존재 | `{applied:False, reason, button:"disabled"}` + audit BLOCK + **write 0** | EX staging#2,4,5,6 |
| B3 | ctx.wal_abort=True | ROLLBACK·`reason:"sqlite_wal_incomplete"`·nodes count 불변 | EX staging#8 |
| B4 | ctx.checksum_mismatch=True | ROLLBACK·`reason:"sqlite_checksum_mismatch"`·count 0 | EX staging#7 |
| B5 | INSERT 중 예외 | ROLLBACK·`reason:"exception:<Type>"` | **s4gap B5-1~5** (partial write 0·checksum 불변·integrity ok·재투입 정상) |
| B6 | write_lock 경합(타 pid lock) | `RuntimeError("staging_write_locked…")` raise | EX staging#14 |
| B7 | candidate=1·promotion_allowed=0 강제 | INSERT 값 고정 | EX staging#1(cand==(1,0)) |
| B8 | created_at=ts·use_count=0 | 값 고정 | EX staging#15 |
| B9 | snapshot 동반(snap_dir 생성) | 반환 snapshot 경로 존재 | **s4gap B9** |
| B10 | before==after(BLOCK 시 checksum 불변) | audit before==after | **s4gap B10** |

### C. `tombstone(db, node_id, ctx, snap_dir, ts=None)`
| id | 입력 | EXP | 기존 |
|---|---|---|---|
| C1 | 존재 노드 | `{state:"tombstoned", physical_present:True}`(논리만, 물리 잔존) | EX staging#9 |
| C2 | 미존재 node_id | `{state:None, physical_present:False}` | **s4gap C2** |
| C3 | write_lock·snapshot·audit ALLOW 동반 | 부작용 고정 | **s4gap C3** |

### D. `StagingDB` 인프라
| id | 메서드/조건 | EXP | 기존 |
|---|---|---|---|
| D1 | `__init__` 운영경로(OPERATING_PATHS) | `PermissionError("operating_store_forbidden")` | EX staging#11 |
| D2 | `__init__` 구 ledger 마이그레이션(chain_ver/semantic_subtype/use_count/**speaker** 없음) | ALTER 추가·기존 행 보존·NULL/0 | EX staging#16 |
| D3 | `write_lock` 같은 pid 재진입 | 허용(에러 0) | **s4gap D3** (타 pid RuntimeError 대조) |
| D4 | `write_lock` 타 pid lock 잔존 | `RuntimeError` | EX staging#14 |
| D5 | `audit_append` v2 체인(prev_hash 연결·entry_hash) | 체인 무결 | EX staging#10 |
| D6 | `verify_chain` 변조 시 False | 변조 BROKEN | EX staging#10 |
| D7 | `verify_chain` 꼬리 삭제(메타 앵커) | False | EX staging#12 |
| D8 | `verify_tail_state` 우회 직접쓰기 | False | EX staging#13 |
| D9 | `snapshot` wal_checkpoint(TRUNCATE) 후 copy | 파일 생성 | **s4gap D9** (복사본 count 일치) |
| D10 | `store_checksum` nodes/edges/evidence 정렬 해시(**speaker 컬럼 projection 제외·anchor 유지**) | 결정성 | **s4gap D10** (변경 감지·복원) |
| D11 | **sqlite integrity_check=ok** 보존(이동 전후) | "ok" | **s4gap D11-1~5** (candidate/차단/deprecate/staging 후 ok·save_gate jsonl 무결) |

### E. `save_selected(db, text, indices, ctx, snap_dir, due_date=None)` — **G4 ① / L68**
| id | 입력 | EXP | 기존 |
|---|---|---|---|
| E1 | actor≠"human"(정규화 후) | `block("G4_no_auto")` | EX cand#3 |
| E1b | actor=" Human "/"AUTO"/누락/agent/system | 정규화 lower 후 human 외 전부 G4_no_auto | **s4gap E1b-1,2,3** (human변형=통과·human외 전수 BLOCK·차단군 write 0) |
| E2 | confirm≠"SAVE i,j" 정확형 | `block("confirm_phrase_mismatch")` | EX cand#2 |
| E3 | indices=[] | `block("empty_selection")` | **s4gap E3** (confirm="SAVE " 정합) |
| E4 | index 범위 밖 | rejected.index_out_of_range++ | EX cand#7 |
| E5 | A0 verdict=FAIL | rejected.a0_fail++ | EX cand#8 |
| E6 | A0 REVIEW & not allow_review | rejected.a0_review_needs_explicit_allow++ | **s4gap E6** (a0 monkeypatch) |
| E7 | PII/secret 재스캔 hit | rejected.pii_or_secret++ | **s4gap E7** (scan monkeypatch) |
| E8 | 기존재 노드 | skipped_existing++ | EX cand#6 |
| E9 | saved_items 비면 | `nothing_to_save`·skipped 보존 | EX cand#6a |
| E10 | 정상 | `{applied:True, saved, skipped, due_set}`·node_type∈5종EN·문장전체 저장 | EX cand#1,13 |
| E11 | due_date & kind=판단 | set_review_due 위임·due_set++ | EX cand#1 |
| E12 | staging_apply BLOCK 위임 | reason 전달·audit BLOCK | EX cand#9,10 |
| E13 | 원문 전문 미저장·confirmed0·promotion0 | 전수 0 | EX cand#5,11,12 |

### F. `_maybe_promote_actor_by_gate(text, indices, ctx)` — actor 승격(fail-closed)
| id | 입력 | EXP | 기존 |
|---|---|---|---|
| F1 | actor 이미 human | ctx 그대로 반환 | **s4gap F1** |
| F2 | 비human + gate_human_for True | actor→human·actor_promoted_by="save_gate" | **s4gap F2** |
| F3 | 비human + 게이트 미기록/stale | 승격 0(원 ctx 유지) | **s4gap F3** |
| F4 | sgate import 실패/예외 | except pass·원 ctx(default-deny) | **s4gap F4** |

> **F는 G4 ① 의 승격 분기 — 약화 절대 금지(§5-2).** 이동 시 fail-closed 4분기 전수 pin 필수.

### G. `commit_selected(db, text, preview_id, picks, confirm, snap_dir, due=None, actor="human")`
| id | 입력 | EXP | 기존 |
|---|---|---|---|
| G1 | preview_id≠sha256[:8] | `{applied:False, reason:"preview_required_mismatch"}` | EX c2s#9 |
| G2 | preview_id 일치+confirm 일치 | save_selected 위임·applied | EX c2s#5 |
| G3 | confirm 불일치/누락 | save_selected→confirm_phrase_mismatch | EX c2s#6,7 |
| G4 | actor=auto | save_selected→G4_no_auto | EX c2s#8 |
| G5 | BLOCK 다발 중 write 0 | 노드 count 불변 | EX c2s#10 |

### H~K. deprecate_g3 write 4함수 — **G4 ③ (is_confirm_actor allowlist)**
공통 G4: actor∈{None,"",auto,reader,agent,system,AUTO,ai,claude,키누락} → `"G4_no_auto"`,
감사 위장 0(`_audit_actor`: 누락→"unknown", 위장 human 0). EX g3#40(전수 회귀).

| id | 함수/입력 | EXP | 기존 |
|---|---|---|---|
| H1 | `deprecate_item` 정상 | state→deprecated·물리잔존·deprecations 기록·ALLOW | EX g3#1 |
| H2 | reason 공백 | `"deprecated_reason_required"` | EX g3#2 |
| H3 | 이미 deprecated | `"already_deprecated"` | EX g3#3 |
| H4 | tombstoned 노드 | `"tombstoned_item"` | **s4gap H4** |
| H5 | kind∉{node,edge} | `"kind_invalid"` | **s4gap H5** |
| H6 | 미존재 | `"item_not_found"` | EX g3#4 |
| H7 | edge deprecate | view 제외·물리잔존 | EX g3#5 |
| I1 | `set_review_due` 정상 | judgment_reviews pending INSERT·ALLOW | EX g3#6 |
| I2 | due 형식≠YYYY-MM-DD | `"due_date_invalid"` | EX g3#7a |
| I3 | 노드 비active | `"node_not_active"` | EX g3#7b |
| I4 | pending 중복 | `"pending_review_exists"` | EX g3#6 |
| J1 | `resolve_review` 정상 | status→resolved·노드 state/candidate 무변 | EX g3#9 |
| J2 | outcome∉OUTCOMES | `"outcome_invalid"` | EX g3#10b |
| J3 | reason 공백 | `"resolve_reason_required"` | **s4gap J3** |
| J4 | pending 없음 | `"no_pending_review"` | EX g3#10a |
| K1 | `classify_harvest_item` 정상 3종 | keep/challenge/discard 기록·ALLOW | EX g3#17 |
| K2 | klass∉HARVEST_CLASSES | `"klass_invalid"` | EX g3#18 |
| K3 | discard & reason 공백 | `"discard_reason_required"` | EX g3#18 |
| K4 | item_id 공백 | `"item_id_required"` | **s4gap K4** |
| K5 | 재분류(같은 item_id) | UPSERT 갱신 | EX g3#19 |
| HK-audit | 4함수 BLOCK 중 audit 위장 human 0 | forged_audit==0 | EX g3#40 |
| HK-store | confirmed0·promotion0·운영 store 불변 | 전수 0 | EX g3#12, mtime |

### L~O. save_gate (binggu_save_gate.py) — gate-log write/판정
| id | 함수/입력 | EXP | 기존 |
|---|---|---|---|
| L1 | `gate_record` 정상 | append-only 기록·건수 반환·파일 생성 | EX gate T3 |
| L2 | `gate_record` 빈/공백 문장 skip | `_norm` 빈 → 미기록 | **s4gap L2** |
| M1 | `gate_human_for` 전부 기록+신선 | True | EX gate T3,T11 |
| M2 | 일부 미기록 | False(all 요구) | EX gate T5 |
| M3 | stale(창 밖) | False | EX gate T6 |
| M4 | 빈 입력/공백 | False | EX gate T8 |
| M5 | append-only 재대조 | 여전히 True | EX gate T7 |
| N1 | `write_last_preview` atomic(.tmp→replace) | hash-only·원문 미저장·건수 | EX gate T9 |
| N2 | 빈 sentence 후보 skip | rows 제외 | **s4gap N2** |
| O1 | `gate_record_from_prompt` 'SAVE n' | 해당 idx hash 기록·건수 | EX gate T10 |
| O2 | 비SAVE 발화 | 0 | EX gate T13 |
| O3 | preview 파일 부재/파싱실패 | 0 | **s4gap O3** |
| O4 | idx 매칭 0 | 0 | **s4gap O4** |
| LO-home | gate_path/last_preview_path == gate_home 단일 resolver | split-brain 0 | EX gate T14~T18 |

---

## 3. §4 선행 의무 ↔ 케이스 매핑 (GREEN 정의)

| §4 선행 의무 | 충족 케이스 | 현 상태 |
|---|---|---|
| actual write path(staging INSERT 정상/롤백) | B1·B3·B4·B5 | **B5 s4gap 커버** |
| dry_run path(MCP dry_run write 0 PREVIEW) | **별도(MCP 핸들러 계층)** | 본 문서 범위 밖 — §4-주 참조 |
| G4 block 3중(L68/c2/deprecate 4) | E1·E1b · A1·A2 · HK(H~K) | **A2·E1b·H4 s4gap 커버** |
| actor/confirm/token path | E1~E3·G3·B7 | **E1b·E3 s4gap 커버** |
| non-TTY fail-closed(exit2) | **interactive_save**(별도, 인수인계 8/8 GREEN) | 기존 GREEN |
| ledger write transaction(BEGIN/COMMIT/ROLLBACK) | B3·B4·B5 | **B5 s4gap 커버** |
| write_lock(O_EXCL/busy_timeout) | B6·D3·D4 | **D3 s4gap 커버** |
| snapshot/preview 동반성 | B9·N1 | **B9 s4gap 커버** |
| sqlite integrity_check=ok | D11 | **s4gap D11-1~5 커버** |
| actor 승격 fail-closed(F2~F4) | F1·F2·F3·F4 | **s4gap F1~F4 커버** |
| operating home no side effect | 전 selftest mtime 불변 | EX(전 파일) |
| audit chain/candidate-only/promotion0/원문 미저장 | D5~D8·E13·HK-store | EX |

> **§4 dry_run path 주:** MCP `dry_run=True` PREVIEW 경로는 핸들러(`_u_save_candidate` 등) 계층에
> 있고 gate-critical write core(본 6대상) 밖이다. S4 진입 시 별도 characterization 항목으로 분리하되,
> write core 이동의 전제 조건은 아니다(write 0 경로라 본체 미경유).

**GREEN 판정 기준(S4 진입 게이트):**
1. 본 §2 전 케이스 중 **EX 표기 전부 현행 GREEN 재확인**(5 selftest + interactive + 회귀 harness).
2. **GAP 표기 전부 characterization 추가 후 GREEN** — 단 *테스트 추가만*, 대상 코드 touch 0.
3. D11(integrity_check=ok)을 5 대상 selftest 공통 사후단언으로 신설.
4. 위 1~3 달성 = §4 "대량 characterization GREEN" 충족 → owner approval 토큰 요청 자격.

---

## 4. S4 이동 권장 순서 (진입 승인 후에만 — 본 문서는 순서만 설계)

저위험→고위험. **actual write 본체(B `staging_apply`)는 마지막 또는 영구 HOLD(§5-1).**

| 순번 | 대상 | 사유 | 비고 |
|---|---|---|---|
| S4-1 | L·M·N·O (save_gate write/판정) | gate-log·hash-only·운영 ledger 별개 파일 | **✅ DONE(2026-06-26)** — `binggupack/safety/gate_log.py` 정본화·byte-identical·semantic change 0. 상세 = `SAVE_GATE_S4_1_GATE_LOG_CANONICALIZATION.md` |
| S4-2 | F (`_maybe_promote_actor_by_gate`) | 순수 함수·G4① 승격 | **HOLD(2026-06-26)** — characterization F1~F4 GREEN(s4gap)이나 `capture_preview`(scripts 전용·package 등가물 0) 의존으로 binggupack 정본 이관 시 **strangler 단방향 위반·import cycle 위험** → byte-identical 이관 불가. owner token 발급됐으나 기술적 STOP. 상세 = `SAVE_GATE_S4_2_ACTOR_PROMOTION_CANONICALIZATION.md` |
| S4-3 | H·I·J·K (deprecate_g3 4함수) | sqlite write·G4③ | 4함수 동시(③ 분리 금지) |
| S4-4 | C·D 인프라(write_lock/snapshot/audit/verify) | write 본체 의존 | store_checksum 결정성 보존 |
| S4-5 | A (`c2_check`) | G4② 판정·순수 | B와 강결합 — 함께 검토 |
| **S4-6(마지막/HOLD)** | **B `staging_apply` + E `save_selected` + G `commit_selected`** | **actual write core + G4①** | **owner 명시 승인 필수·영구 HOLD 후보** |

**이동 불변식(전 순번 공통):**
- semantic change 0 / byte-identical 위치 이동만.
- G4_no_auto 3중(①L68 ②c2 ③deprecate4) 한 층도 분리·약화·우회 0.
- actor/confirm/token/dry_run/ledger-write 분기 의미 0 변경.
- 외부 호출처는 re-export 경유 무변경(S1~S3 확립 패턴).
- 이동 후: 5 selftest + interactive + 회귀 harness + ledger integrity_check + 좁은 구간 mtime 전종 GREEN.

---

## 5. 최종 판정

**S4 HOLD 유지.** 본 문서는 design-only — characterization 케이스 목록·GREEN 정의·이동 순서만 명문화했다.
구현 0·코드 이동 0·대상 코드 touch 0. GAP 케이스의 테스트 추가와 owner approval 토큰 없이는
어떤 gate-critical write core 도 이동·변경하지 않는다.

**진척(2026-06-25): S4 characterization coverage COMPLETE.** §2 전 GAP을 tests-only 로 커버 —
1차(A2·E1b·B5·F1~F4·D11) + 2차 저위험(H4·H5·J3·K4·L2·N2·O3·O4) +
3차 고위험(A4·A6·A10·B9·B10·C2·C3·D3·D9·D10·E3·E6·E7)
= `openbinggu_s4_gap_characterization_selftest.py` **41/41 GREEN**. **잔여 GAP: 0.**
그러나 **S4 implementation 은 HOLD** — 테스트 커버는 안전망일 뿐 구현 진입 승인이 아니다.
actual write core(§4 S4-6: `staging_apply`+`save_selected`+`commit_selected`)는 **마지막/영구 HOLD 유지**.

**다음 단계(승인 전제):** characterization 전종 GREEN 충족 → **owner approval/token 요청 자격 발생**
(요청은 별도 단계 — 본 문서는 미요청). 토큰 후에야 S4-1(저위험 save_gate write) 구현 진입 판단.
