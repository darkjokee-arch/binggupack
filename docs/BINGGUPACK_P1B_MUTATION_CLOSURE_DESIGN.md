# BingguPack P1-B Track A — Mutation Surface Closure 설계 명세 (v2 · Fable5 사전검증 반영)

> 정본 상위 RFC: `docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md` (P1-A · v2). §19.1/§23 STILL-OPEN을 봉인.
> base: main `39d8c74`. worktree: `feat/p1b0-mutation-surface-closure`.
> **v2 개정**: Fable5 사전 4-reviewer(바인딩·정직·공격·회귀) Critical 2·High 6 인라인 해소.

## 0. owner 결정 (2026-07-11)
> "휴대폰→PC 워크플로는 유지. 휴대폰은 저장 승인 안 함·저장 의도만 전달. 실제 영구 저장은 PC의 exact-bound 로컬 승인 이벤트 이후에만 정확히 한 번."

→ transport(전달·회수)는 유지. **영구 write만** exact-bound approval 경유. provider 미구성 시 fail-closed. **승인 대기 intent는 원문 보존**(데이터 손실 0 — 사전검증 H1).

## 1. 위협 모델 (정직 경계 — 과장 0, 사전검증 C1·M1·M2 반영)

**하드니스는 배포 형태에 조건부다. 어느 행도 "무조건 하드"가 아니다.**

exact binding이 하드가 되는 **3조건(전부 성립해야 함)**:
1. intent 생산자(폰/웹 모델)에 로컬 파일/셸 도구 없음,
2. `hosted pull`/mutation이 도는 **PC의 모델 tool surface가 approval store(`~/.binggupack/{approvals.jsonl, trusted_approval.json}`)에 write 불가**,
3. provider 구성은 owner만.

| 대상 | 표면 | 봉인 성격 (정직) |
|---|---|---|
| hosted 3파일 | 웹/앱 커넥터 유입 → PC 로컬 커밋 | **웹/앱 커넥터 배포에서만** 하드 — 위 3조건 성립 시. transport 분리는 원격 생산자만 제거하며 store 격리는 조건 2에 의존. **FS/Bash 병재 PC에서 pull 실행 시 하드 아님** |
| CLI 5개 | MCP 미노출·CLI | intent-routing + payload 바인딩(비대화형) + 비대화형 owner 경로 신설. **셸 병재 시 하드 아님** |
| hag_sync_adapter | owner 셸 전용·운영 write | `--actor human` 리터럴 제거·fail-closed 강화. **셸 병재 시 하드 아님(어차피 도달=셸 보유=비하드)** |

**fail-closed 보증의 정직한 한계**(사전검증 M1): "provider 미구성 → 닫힘"은 *구성 파일 부재* 상태에 대한 조건부 보증이다. FS/Bash 병재 호스트에서는 모델이 그 상태(config·event store 둘 다)를 만들 수 있다 — 이 보증이 의미를 갖는 표면은 웹/앱 커넥터(MCP-only)뿐.

**owner 실환경 경고**: owner의 실제 dev 머신은 FS+Shell MCP 병재 = 위 out-of-scope 배포. **이 환경에서 Track A 전체는 하드 통제가 아니라 "자동저장 방지 + 비대화형 owner 경로 + intent-routing"이다.** README/SECURITY에 프로미넌트 표기.

## 2. A1 — `binding_fields` 확장 (5 operation, 사전검증 C1·M4 반영)

`trusted_approval.py:binding_fields`에 5분기 추가. **write되는 값 전부 바인딩**:
```
accept        : {index, id8, reason}                       # event 는 operation 이 구분(중복 제거)
unaccept      : {index, id8, reason}
due           : {node_id, due_date}
resolve       : {node_id, outcome, reason}
confirm_edges : {edges: sorted([{src,dst,rel,evidence:sorted}…])}   # ★evidence_refs 포함(C1)
```
- **C1 해소**: confirm_edges/import는 edge_key(src|dst|rel|kmap)만으론 evidence_refs 미바인딩 → 승인 후 evidence 위조 가능. binding에 각 edge의 `evidence_refs` 정렬 포함 → 위조 시 digest 불일치.
- **M4 해소**: 바인딩·render·receipt를 **실제 적재될 post-filter subset**(supports_judgment+evidence+matrix+non-secret+non-dangling 통과분) 기준으로 통일. consume 시 재필터 결과가 승인 집합과 다르면 fail-closed.
- `summary_for`·`render_review`에 5분기 추가. due/resolve는 **node_id 기반**으로 `nodes.sentence WHERE node_id=?` 조회(index 패턴 재사용 금지·M2), `_pii_safe` 적용. `_target_sentence`는 binding_fields 무시(digest 불변).

## 3. A2 — CLI 5개 (게이트는 CLI층에만·코어 불변, 사전검증 B-1·B-2·B-4 반영)

**★코어 함수(accept_from_list/set_review_due/resolve_review/apply_confirm_to_sync/confirm_edge) 시그니처·동작 절대 불변** — B-1: 코어에 approval 넣으면 owner_accept(15)+g3(22)+graph_confirm(T20~25)+hag(T9~16) 37+체크 대참사. 게이트는 **CLI 진입점(binggu.py cmd_*)에만**.

`_resolve_human_ctx` 확장(keyword-only·하위호환):
```python
def _resolve_human_ctx(ledger, sents, confirm=None, *, operation=None, bind=None, home=None, db=None):
    # 1) save_gate 앵커 → ctx["actor"]="human" (기존)
    # 2) 대화형 TTY → ctx["actor"]="human" (기존·UX 경계·비암호학)
    # 3) 비대화형 + operation+bind+bind.approval_id + home+db →
    #      approval_gate 로직 재사용(verify_event+reserve/finalize) 통과 시 human + one-time consume
    # 4) 그 외 → reader (fail-closed)
```
- B-4: 반드시 `ctx["actor"]="human"` **서브스크립트 대입**(AST 인벤토리 `binggu_approval_origin_selftest`가 `return {"actor":"human"}` 리터럴을 잡음). approval 분기는 **TTY(2) 뒤·reader(4) 앞** 삽입(D1/D2/FE1~4 불변).
- B-2: cmd_*에서 새 인자는 `getattr(a, "approval_id", None)`(fake-args AttributeError 회피 — `test_binggu_cli_selftest`의 `class A` 네임스페이스).
- **정직**: 대화형 경로(1·2)는 payload 미바인딩·isatty 신뢰 한계(Windows 상속콘솔/PTY). due/resolve는 대화형에서 confirm 게이트조차 없음(isatty-only 권한) → SECURITY.md 명시.
- one-time consume은 `approval_gate.authorize`의 reserve/settle 재사용(중복 0). settle의 §14 분할표(idempotent/transient/hard-block)는 accept/due/resolve의 BLOCK reason(`already_accepted`/`no_pending_review`/`pending_review_exists`)이 else→RELEASE(승인 소각 0·재사용 가능)로 안전 처리됨(사전검증 확인).

### confirm_edges 특수 (H1·M3)
- 게이트는 cmd_confirm_edges에만. `apply_confirm_to_sync`/`confirm_edge` 코어 불변.
- **H1**: 코어가 `applied:int`/reason無/SyncError raise라 settle 분할표와 안 맞물림 → CLI 래퍼에서 `{"applied":bool, "reason":<code>, "count":int}`로 정규화 후 settle.
- **M3**: reserve/finalize(approval_consumptions)는 **운영 ledger con**에서. sync_edges db엔 그 테이블 없음 → ledger_id는 운영 ledger 기준, edge write만 sync db.

## 4. A3 — hosted 3파일 (원문 보존·explicit 고정, 사전검증 C1정직·H1·H4·B-3·B-5 반영)

정본 게이트 = `openbinggu_save_intent_outbox_runner.py:process_outbox`. inbox/live_runner가 위임.

### 설계
`process_outbox(db, outbox_dir, ctx, snap_dir, now_ts, *, home=None)`:
- 게이트 1~4(schema/TTL/intent_id/confirm) 후, **save_selected 직전** `approval_gate.authorize("save_candidate", {text, indices, explicit:False, speaker, approval_id}, home, db)` 경유.
- **H4**: digest·render_review·save_selected 3곳 모두 `explicit=False` 고정(hosted 스키마에 explicit 필드 없음·현행 실행과 동일). 넣을 거면 protocol bump.
- intent에 owner가 부여한 `approval_id` 있으면 verify → 저장 1회. 없거나 provider 미구성 → reader → save_selected G4 BLOCK.
- **H1(fail-destructive) 해소**: approval 미제시/미승인 intent는 `.rejected`(원문 파괴) **금지** → 원문 보존 `.pending_approval` 상태(재처리 대상·TTL만 적용). `.rejected`는 정당 거부(schema/tamper/confirm/PII)에만. **데이터 손실 0**.
- `actor = ctx.get("actor")` — 기본값 "human" 제거(B-5: home=None → provider 없음 → fail-closed, 운영홈 폴백 금지). ctx.actor는 감사 표기용(신뢰 안 함).
- inbox(`:168`)·live_runner(`:145`)의 `{"actor":"human"}` 리터럴 제거 → home 전달, 정본 게이트 경유.
- **live_runner 특수**(H1): 1회용 tempdir+worker non-retention이라 원문 소실 위험 → live_runner는 승인 대기 intent를 **영속 staging의 `.pending_approval`로 이전**(tempdir 삭제 전 보존). approval_id 부여 전까지 원문 유지.
- **transport 유지**: intent 수신·staging 회수·outbox 순회 불변. 오직 영구 save_selected만 approval 게이트.

### owner 실행 흐름 (H1 반영·데이터 손실 0)
1. 폰/웹 → intent 전달(승인 아님) → outbox/staging.
2. `binggu hosted pull` → 미승인 intent = PENDING approval request 생성 + 원문 `.pending_approval` 보존.
3. owner `binggu approval show/approve <rid>`(TTY) → mint → intent에 approval_id 기입(러너가 매핑).
4. 다음 `binggu hosted pull` → verify → 저장 1회(idempotent replay-receipt).

## 5. A4 — hag_sync_adapter (actor 인자 제거·멱등 semantics, 사전검증 M3·A-5·B-6 반영)

- `import_confirmed_edges`/`confirm_edge`의 **positional `actor` 인자 자체 제거**(default "human" 제거만으론 직접 import 시 cosmetic). approval-only 강제.
- `import_confirmed_edges(sync_conn, ledger_path, now, *, home)`: 운영 ledger write → exact-bound approval 요구(operation=`import_edges`, binding=confirmed edge_keys+evidence 정렬·§2). ledger_id·reserve/finalize는 **운영 ledger con**(M3). provider 미구성/미승인 → SyncError(fail-closed).
- **A-5(T13 멱등) 해소**: 1회 import 후 confirmed→imported 전이 → 2회차 confirmed 집합=∅ → **빈 집합 import = 승인 불요 no-op**(`{"imported":0}`)으로 정의. exact binding과 충돌 회피.
- 기존 `actor != "human"` 이중 게이트는 confirm_edge(sync 전용)에 유지(A2 confirm_edges CLI 래퍼가 human ctx 전달).
- **B-6**: `binggu.py:1229` owner 안내문(`--import-edges --actor human`) 갱신.
- **정직**: owner 셸 전용·셸 병재 시 하드 아님(§1). fail-closed 강화·과장 금지.

## 6. selftest / 회귀 (사전검증 회귀 4-reviewer 전수 반영)

### 파손 확실 → 갱신 필요 (5파일 + 조건부)
1. **`tests/test_binggu_cli_selftest.py:313` 체크 15** ★CI 직접 실행(binggu.py --selftest, 5 매트릭스) — h_home provider enable→1차 pull PENDING(원문 보존 확인)→mint→approval_id→2차 pull applied 1로 갱신.
2. `openbinggu_save_intent_outbox_runner.py` run() 체크 1·6·7·11 — provider enable+intent별 save_candidate binding digest→upsert→mint→approval_id. 신규: fail-closed·replay 무2차write·approval_id mismatch.
3. `openbinggu_save_intent_live_runner.py` T1·T9b·TI1 — 동일 패턴 + `.pending_approval` 원문 보존 체크.
4. `binggu_hosted_inbox.py` T6(·T7 의미 보존) — mint 후 save 게이트 도달.
5. `hag_sync_adapter.py` T12·T12b·T13·T16 — import approval mint 시나리오 + T13 빈집합 no-op.
- 조건부: `binggu_graph_confirm.py`(코어 불변이면 T20~25 무사·B-1), `binggu_approval_origin_selftest.py`(KNOWN_P1B_HOSTED 문구·스코프 정직성·B-7).

### 신규
`scripts/binggu_p1b_mutation_closure_selftest.py`(run_all 등재) — 5-op binding digest·비대화형 approval 경로·hosted approval 게이트·hag import fail-closed·우회(env/문자열/미승인/evidence위조/집합팽창) 전수 BLOCK.
- **B-3 지뢰 회피**: `channel="test_double"` 리터럴 금지(ship-guard `_ship_guard` 정규식 즉사) → boundary 패턴 `ta.mint_approval(home, req, 900, time.time())` 기본 채널. 합성 PII는 `"010-"+"1234..."` 문자열 분리(tree scan BLOCK 회피).

### 커밋 전 로컬 선행 (P1-A 3회 실패 교훈 — 전수)
1. `python scripts/ci_local_preflight.py` 14 STEP 전부.
2. run_all 밖 수동 8종: outbox(16)·hosted_inbox(T1~10)·live_runner·hag(T1~16)·graph_confirm(T20~25)·owner_accept_ux(15)·g3(22) + `tests/hosted_boundary_e2e.py`.
3. 신규 selftest run_all 등재 후 재실행.
4. 운영 `~/.binggupack` mtime 전 구간 불변 별도 확인.

## 7. 문서 갱신 (declared→enforced 정합, 사전검증 H2·H3 필수)
- `SECURITY.md:35` 사람 근거 "2가지"→3가지(+비대화형 exact-bound approval event). `:20` **store 하드닝은 Track B에서 RFC 설계 검토만·미구현**(H3 — store는 여전히 home 평문 jsonl) 명시.
- `binggu.py:658` `_resolve_human_ctx` docstring 2→3.
- RFC §19.1①②④·§23·§26 R2/R5 상태 노트(삭제 0·§23 규율).
- `binggu_hosted_inbox.py:11,139`·`binggu.py:252,292` confirm="저장 게이트" 어휘 정정(RFC §6: confirm≠승인).
- `README.md:65,230` "휴대폰에서 승인한 걸"→"의도만 전달·PC에서 승인".
- `binggu_approval_origin_selftest` §D KNOWN_P1B_HOSTED 문구(리터럴 제거 후 stale 방지).

## 8. 미결 → 구현 시 확정
- Q1: hosted approval_id 부여 기전 = 러너가 mint 후 intent 파일에 자동 기입(owner는 approve만). "approval_id 존재 ≠ verify_event 대체"(공격 M3) 못박기.
- resolve의 record_resolution(hit_events pair-ai fan-out·바인딩 M1): resolve receipt에 fan-out node_id 집합 포함하거나 render에 표기.
