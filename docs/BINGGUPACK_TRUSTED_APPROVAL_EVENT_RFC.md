---
status: draft
name: BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC
description: P1-A — Trusted Approval Event. Owner-controlled local approval → exactly-once MCP mutation. Fills the trusted_approval_event_required fail-closed contract reserved in P0/P0.1. (v2 — Wave 2 Fable5 pre-review resolutions incorporated.)
metadata:
  type: reference
scope: project:binggupack
---

# BingguPack Trusted Approval Event — RFC (P1-A · v2)

> "AI가 기억해도, 결정권은 나에게." — 제품 원칙
>
> **정의.** *Trusted approval event* 는, **구성된 위협 모델 안에서**, MCP/model 의 tool
> surface **밖**의 owner-controlled channel 이 **특정 mutation**(exact payload · operation ·
> ledger · protocol version)을 승인했다는 **로컬 증거**다. "human proof" 가 아니다. 이 증거의
> 보안 강도는 **배포 형태에 의존**한다(§4·§5 — 과장하지 않는다).

Base: `feat/p1a-trusted-approval-event` (= PR #2 head `78486e7`, P0/P0.1 포함). V2/V2A RFC 에
**additive**. 기존 문서 삭제 0.

> **v2 개정(Wave 2 Fable5 사전 적대검증 반영).** 3 reviewer 가 0 Critical · 8 High · 7 Medium ·
> 5 Low 를 코드 실측 기반으로 제기했다. 전부 "기능 불가"가 아니라 "RFC 부정확/과장/누락" 이라
> 이 v2 에서 인라인 해소했다. 추적표 = §27.

---

## 1. 문제 정의

P0/P0.1(`e48a842f`/`78486e7`)에서 모든 MCP write 핸들러는 fail-closed 로 봉인됐다. 핸들러가
client `actor` 를 무시하고 `actor="reader"` 로 하드 오버라이드 → 각 storage core 게이트가 write
를 0 으로 막는다. 이유(P0 재현): `confirm` 은 dry-run 이 `confirm_expected` 로 노출해 **모델이
재현** 가능 → 사람 선택의 증거가 못 된다.

**중요(Wave2 TAE-2 실측).** "actor != human → block" 은 6 core 중 **4 core 만** allowlist
(`== "human"`)이고, **2 core(`replace_from_list`·`staging_apply/c2_check`)는 denylist**
(`actor in ('auto','reader')`)다. 즉 현 봉인은 **no-approval sentinel 이 문자열 `"reader"` 라는
사실에 의존**한다. sentinel 이 달라지면 replace/staging 이 fail-OPEN. → P1-A 는 (a) no-approval
actor 를 **정확히 `"reader"`** 로 고정하고, (b) 두 denylist 게이트를 **allowlist(`=="human"`)로
하드닝**해 이 잠재 fail-open 을 제거한다(§18·§27-H2).

P0.1 은 계약 이름을 예약해 뒀다 — `reason: "trusted_approval_event_required"`,
`owner_action: "use_local_cli"`. **P1-A 는 이 placeholder 를 채운다.**

## 2. 현재 P0 fail-closed 상태 (실측)

| handler | write 대상 | core 게이트 유형 | 상태 |
|---|---|---|---|
| `_u_save_candidate` (:93) | nodes/edges/evidence | `save_selected:70` **allowlist** | FAIL-CLOSED |
| `_u_pair` (:418) | 화자축 nodes+edge | `save_paired:248` **allowlist** | FAIL-CLOSED |
| `_u_deprecate` (:482) | nodes.state + deprecations | `deprecate_item:83` **allowlist** | FAIL-CLOSED |
| `_u_replace` (:526) | deprecate + save (composite) | `replace_from_list:113` **denylist**⚠ + `c2_check:183` **denylist**⚠ | FAIL-CLOSED (sentinel='reader' 한정) |
| `_u_mark_hit`/`_miss` (:995) | hit_events | `record_resolution:169` **allowlist** | FAIL-CLOSED |
| `_u_harvest_add`/`_remove` (:606/:635) | harvest_sources.json (**not ledger**) | — | **GAP: NOT fail-closed** (confirm-only) |

⚠ = Wave2 TAE-2 hardening 대상. **GAP** = harvest 는 actor 게이트 없이 재현 가능 confirm 만으로
통과(§18 에서 authorize 편입해 봉인 — canary 창에서 confirm-only 로 남기지 않는다, TAE-7).

**저장 아키텍처(실측).** 모든 write 는 프로세스당 하나의 `StagingDB.con`(WAL, busy_timeout=5000)
공유. core 들은 각자 내부 `BEGIN..COMMIT`(또는 단일 commit)을 연다. **`replace_from_list` 은 단일
SQL tx 불가**(내부 각기 commit 하는 두 sub-op 합성). `audit_append` 는 mutation tx 와 **항상 별도
commit**. → consume+mutate 단일 트랜잭션은 uniform 하게 불가. P1-A 의 uniform primitive =
**reserve + one-time-consume + idempotent replay-receipt**(§14).

`capture_preview`(MCP mode "read")는 `last_preview_candidates.json`(home)을 쓴다 — "read" 가
"파일 write 0" 을 뜻하지 않음(TAE-7). 이 파일은 approval store/gate log 가 아니다.

## 3. Threat actors

1. **Autonomous MCP model (주 위협).** binggupack MCP tool surface **만** 가진 에이전트. dry-run
   재현·임의 param·무한 재시도 가능. **approval event 를 쓰는 도구가 없다**(§5).
2. **Compromised/confused hosted intake.** 폰/웹 save-intent. V2/V2A 가 transport 담당 —
   **untrusted intent** 로만 취급. (hosted runner 자체는 P1-A 범위 밖 — §23.)
3. **Replay/race 유발자.** 응답 유실 재시도, 두 프로세스 동시 consume, 클럭 교란.
4. **범위 밖(명시).** arbitrary local shell/OS 권한 공격자. **그리고(Wave2 TAE-1) 모델에게
   binggupack MCP 외에 임의 파일/셸 도구(Filesystem/Bash MCP)를 부여한 호스트** — 그 호스트에서
   모델은 approval store 파일을 직접 쓸 수 있으므로 이 통제의 방어 범위 밖이다(§4·§5).

## 4. In-scope / Out-of-scope · 신뢰 가정 (정직한 범위)

**In-scope (이 PR).** approval request/event 상태 모델 · local-only trusted provider · MCP
mutation↔approval 연결 · exact payload/operation/ledger/protocol binding · 짧은 TTL ·
reserve+one-time consume + lease · reject/revoke/expire · replay receipt · per-request review
record(owner 검토) · CLI owner UX(비-TTY 하드거부) · fail-closed 재활성화 · 위협 모델/RFC ·
적대 회귀 테스트 · additive/idempotent migration · 기존 RFC 정합 · 문서/SECURITY 갱신.

**Out-of-scope.** hosted/web approval provider(placeholder interface 만) · hosted save-intent
runner 재배선(§23 = STILL-OPEN/P1-B) · live Worker 배포 · 모바일 승인 · 팀/멀티유저 · OAuth ·
원격 동기화 · MCP core/advanced profile · alias · Bridge 리네이밍 · UI 대시보드 ·
release/merge/tag/live · co-loaded FS/shell 호스트에 대한 하드 방어(§5).

**신뢰 가정 (명문화 — 불변식 A, 과장 금지).**
- approval event 는 MCP tool surface **밖**의 owner-controlled channel(CLI/hook)이 만든다.
- 모델에게 approval **생성 도구**·secret 이 노출되지 않는다(binggupack MCP 표면 기준).
- `isatty()` 는 pipe 를 거르는 **보조 신호**일 뿐 사람 증명 경계가 아니다.
- **이 통제의 보안 강도는 "모델의 tool surface 가 approval store 위치에 write 권한을 갖지 못한다"
  가 성립하는 배포에서만 하드하다.** arbitrary local shell/OS, 또는 **모델에 Filesystem/Bash MCP
  를 함께 물린 호스트(Claude Code/Codex 기본)** 는 방어 범위 밖 — 그 경우 P1-A 는 **fail-closed 를
  보존하는 intent-routing UX 이지 하드 보안 통제가 아니다**(§5·§16). 우리는 그 이상을 주장하지
  않는다.

이 정직한 범위 안에서 통제가 성립하는 근거가 §5 다. (선택적 하드닝 = §26 R2 / P1-B.)

## 5. trusted channel 의 정확한 의미 (경계가 성립하는 조건 · 성립하지 않는 조건)

**성립(하드).** binggupack MCP 서버가 노출하는 **고정 도구셋**만 가진 에이전트. 그 도구셋에는
approval 발행 도구·approval store write 도구·임의 파일/셸 도구가 **없다**(실측: `TOOLS` 딕셔너리
= read + dry-run + write-gated 핸들러뿐 · `binggupack/mcp/` 전체에 `open(...,'a'/'w')` 0 ·
`_FORBIDDEN` 로 위험 도구 배제). 따라서 이 표면만 가진 모델은 approval event 를 만들 수 없다.
owner 는 **별도 프로세스**(`binggu approval approve`, 자기 TTY)에서 승인한다. 이 패턴은 P0 의
`save_gate` 로 이미 운영 중이다(hook 이 `save_gate_log.jsonl` 에 앵커, MCP 는 못 씀).

**성립하지 않음(정직).** 모델에게 binggupack MCP **와 함께** Filesystem/Bash MCP 를 물린 호스트
에서는, 같은 에이전트가 `approvals.jsonl` 에 위조 approve 레코드를 직접 append 하고 자기가 만든
승인을 consume 할 수 있다(Wave2 TAE-1 재현). event store 는 평문 append-only 이고 §6 은 서명
토큰을 금지하므로(같은 머신 키 = 보안 연극), 이 배포에서 P1-A 는 **하드 통제가 아니다**. 우리는
이를 §16·SECURITY.md 에 **명시**하고, 회귀 테스트로 "MCP-only 표면엔 write 도구 부재"만 보증하며
FS-병재 호스트 보호를 **주장하지 않는다**(그 주장 시 테스트 실패). 하드닝(store 를 FS-root 밖/
keychain 으로) 은 P1-B 옵션.

**불변(모든 배포 공통).** provider 미구성 시 **fail-closed**(P0 그대로). 즉 이 기능을 provider
없이 ship 해도 운영상 변화 0 — 최악은 "승인해도 안 써짐" 이지 "몰래 써짐" 이 아니다.

## 6. confirmation phrase 가 승인이 아닌 이유

```
confirmation phrase   != human approval   (dry-run 이 confirm_expected 노출 → 재현 가능)
actor field           != human approval   (핸들러가 client actor 무시·reader 하드 오버라이드)
environment variable  != human approval   (모델 설정 가능 → provider 발견 근거 불가)
MCP argument          != human approval   (approval_id 포함 모든 param 모델 생성 가능)
model-generated token != human approval   ("안전토큰 금지" · 같은 머신 서명 = 보안 연극)
isatty()              != human approval   (pipe 필터일 뿐)
dry-run 재현값         != human approval
```

confirm 은 **payload-binding shape check** 로만 남는다(권한 부여 0).

## 7. request/event 상태 머신 (§13/§14 모순 제거 · Wave2 P2-01)

두 store 분리. **모델은 REQUEST(PENDING)만, owner 채널만 EVENT(APPROVED anchor).**

```
   (model, MCP) create request ─▶ PENDING ──approve(owner CLI)─▶ APPROVED
                                    │ reject(owner)  ─▶ REJECTED (tombstone)          [terminal]
                                    │ TTL            ─▶ EXPIRED                        [terminal]
                                    ▼
                                 APPROVED ──revoke(owner)─▶ REVOKED (tombstone)        [terminal]
                                    │ TTL           ─▶ EXPIRED                          [terminal]
                                    ▼
   (model, MCP consume) RESERVE (atomic single-winner INSERT nonce, state=CONSUMING, reserved_at=now)
                                    ▼
                                CONSUMING ──(TRANSIENT block)──▶ RELEASE → APPROVED (재시도)
                                    │  (mutation applied | IDEMPOTENT_DONE)
                                    ▼
                                 CONSUMED (+ receipt)  ──replay──▶ receipt, executed_write=false
```

**PK-collision(reserve 시 nonce 존재) 단일 규칙(§13/§14 통합):**
- row = CONSUMED → replay: receipt 반환, `executed_write=false`, no write.
- row = CONSUMING **AND** `now - reserved_at ≤ LEASE` → `approval_in_progress`(loser bail, **no re-run**).
- row = CONSUMING **AND** lease 만료 → **atomic takeover**: `UPDATE … SET reserved_at=now WHERE
  nonce=? AND state='consuming' AND reserved_at=<seen>` rowcount==1 이면 승계해 §14 recovery, 아니면
  다른 프로세스가 승계 → `approval_in_progress`.

**금지 전이(테스트 고정):** PENDING→CONSUMED · REJECTED/REVOKED/EXPIRED→CONSUMING ·
CONSUMED→CONSUMING · 어떤 model 행위도 →APPROVED.

## 8. 데이터 모델

**(a) `approval_requests` (model-writable PENDING · db.con · additive)**

| col | 용도 |
|---|---|
| request_id TEXT PK | `sha256("%s\x1f%s\x1f%s\x1f%s" % (proto, operation, payload_digest, ledger_id))[:24]` — **§9 와 동일 `\x1f` 구분자**(TAE-P2-09) |
| protocol_version, operation, payload_digest, ledger_id | §9·§10 바인딩 |
| summary TEXT | **payload-agnostic 템플릿, handler 생성**(모델 미제공): `"<op>: <n> items → ledger <id8>"`. payload-derived 텍스트 0(TAE R3-02) |
| state, created_at, expires_at | 'pending' + ISO8601 |

**(b) `approval_review` (per-request 검토 레코드 · owner-facing · TAE R3-01)**
owner 가 `approval show` 로 **실내용**을 보게 하는 유일한 곳. home 내 `approval_review/<request_id>.json`.
- 내용 = 렌더된 exact payload(문장 전체 등). **candidate 와 동일한 PII/개인사 제외 게이트**(capture
  preview 의 exclusion, batch_redact 만이 아님) 통과 후에만 기록. 초과분 = placeholder.
- **cap**(ledger 당 pending 상한) · **TTL** · **approve/reject/expire 시 즉시 purge**.
- §17 의 "raw 0" 에 대한 **명시적·경계된 예외**(§17). digest/consumption store 는 여전히 hash-only.
- approve 시 owner CLI 가 **이 레코드 내용으로 digest 재계산** → 저장하는 EVENT digest = owner 가
  본 내용의 digest. consume 은 모델 payload digest == EVENT digest 를 검증 → **owner-saw == committed**.

**(c) Trusted approval EVENT store (owner-only 앵커 · append-only `approvals.jsonl` · CLI/hook 만 write)**
`{request_id, protocol_version, operation, payload_digest, ledger_id, approval_nonce(≥128-bit CLI
생성), approved_at, expires_at, approver_channel:"cli_tty", record_type:"approve"|"revoke"|"reject"}`.
`save_gate_log.jsonl` 규율(append-only·hash/digest only·MCP 미접근·freshness window).

**(d) `approval_consumptions` (dedup ledger · db.con · one-time gate)**

| col | 용도 |
|---|---|
| approval_nonce TEXT PK | **UNIQUE = single-winner**. reserve INSERT 성공자만 진행 |
| request_id | 역참조 |
| state | 'consuming'\|'consumed' |
| **reserved_at** | reserve 시각 — **lease 판정용**(TAE-P2-01) |
| receipt | node_id/decision_id/result(replay 반환). **approval_nonce 는 절대 미포함**(TAE-6) |
| consumed_at | finalize 시각(monotonic-checked) |

**(e) `audit_meta['ledger_id']`** — 최초 open 시 **무조건 `INSERT OR IGNORE`**(user_version 게이트
_migrate 브랜치 밖 — TAE R3 caveat)로 `uuid4` 발행·영속.

## 9. Canonical payload 규칙 (versioned)

`canonical_payload_digest(operation, payload, protocol_version) -> hexdigest`:

1. operation 별 **고정 binding schema**(누락 optional = explicit null):
   - `save_candidate`: `{text, indices(정렬 int), explicit(bool), speaker|null, due_date|null}`
     — **explicit/speaker/due_date 를 반드시 바인딩**(TAE-P2-04: explicit 이 index→sentence 매핑을
     바꾸므로 미포함 시 owner 가 본 문장≠쓰이는 문장). 렌더러·실행기는 **동일 explicit** 사용.
   - `pair`: `{owner_text, ai_text|null, owner_pick, ai_pick, by, relation}`
   - `deprecate`: `{index, id8, reason}` · `replace`: `{index, id8, new_sentence, reason}`
   - `mark_hit`/`mark_miss`: `{recall_query, index, outcome, domain|null, recall_nonce}`
     — **recall_nonce(결과셋 스냅샷) 바인딩**(TAE-P2-05: ledger 변동 시 index→node 재타겟 방지 ·
     consume 은 재계산 nonce==승인 nonce 요구, 모델의 nonce 생략/재계산 불가).
   - `harvest_add`: `{kind, url, keyword|null}` · `harvest_remove`: `{source_id}`
2. 문자열 값 **NFC 정규화**(save_gate `_norm` 정합).
3. 금지 codepoint 거부: bidi override/isolate(RLO/LRO/RLE/LRE/PDF/LRI/RLI/FSI/PDI)·zero-width/
   control(Cc/Cf, tab·newline 예외) 포함 시 `binding_reject:control_char`. 저장 문장도 sanitize(§17).
4. 직렬화 = `json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",",":"))`.
   JSON quoting 이 필드 경계를 확정 → concat 충돌 구조적 불가 · int/str 구분 · `str/repr` 해시 금지.
5. `digest = sha256(("%s\x1f%s\x1f%s" % (protocol_version, operation, canonical_json)).encode()).hexdigest()`.
6. **versioned**: canonicalization 변경 = protocol_version bump → 기존 approval 은 protocol mismatch
   fail-closed. helper 는 CLI·MCP·렌더러 **공유**(digest 일치 보장).

## 10. Ledger identity

내부 영속 UUID 부재(실측) → `audit_meta['ledger_id']=uuid4()` 최초 open 시 **무조건
INSERT-OR-IGNORE** 발행(§8e). approval 은 `(ledger_id, protocol_version, operation,
payload_digest)` 바인딩 → ledger 간 replay 불가. 경로 보조검사(store 위치)엔 `expanduser→abspath→
realpath→normcase→samefile`(기존 `_same_path`) — symlink/case-alias cross-ledger 차단. **1차
identity = ledger_id UUID**(경로 문자열 아님).

## 11. TTL · clock

- 기본 TTL 짧게(예 900s), owner config 파일 값(env boolean 아님). 유효: `approved_at ≤ now ≤
  approved_at+TTL`. 경계 inclusive, `+ε` = expired.
- **clock 역행 방지**: `now < approved_at` = `approval_time_invalid`(영원-fresh 아님). consume 은
  monotonic-checked receipt ts 를 남겨 rollback 이 CONSUMED 를 되살리지 못함.
- **레거시 홀 동시 폐쇄(TAE-P2-08)**: `binggupack/safety/gate_log.py` `gate_human_for` 도
  `now - ts < 0`(미래 ts) → `False` 로 수정한다(P1-A 가 실제로 이 함수를 고친다 — §18 change surface
  에 포함). 이로써 §11 의 "레거시 음수-age 홀 폐쇄" 주장이 실제 코드 변경과 일치.

## 12. Replay 방지 · receipt · provider

`approval_nonce`(owner CLI ≥128-bit)가 `approval_consumptions.PK`. replay/lost-response 재시도 →
CONSUMED row 의 `receipt` 반환(`already_consumed`, `executed_write=false`). **receipt 필드
whitelist = node_id/decision_id/result 만 · nonce 절대 미노출**(TAE-6). Hosted provider =
`NotImplementedError` stub(실행 adapter 미작성).

## 13. Concurrent consume

단일 승인 두 프로세스: reserve `INSERT nonce PK`(WAL+busy_timeout 하 single-winner). loser 는
§7 PK-collision 규칙(CONSUMED→receipt / CONSUMING&lease내→`approval_in_progress` / lease만료→
takeover). 결과 노드 정확히 1개. **§13↔§14 모순 제거**(§7 이 단일 규칙 정본).

## 14. Transaction / crash semantics (불변식 D · Wave2 P2-01/02/03/07 · TAE-3/4)

단일 SQL tx uniform 불가 → **reserve → mutate → finalize** + **명시적 reason 분할**. "cores 멱등"
전제가 거짓(재실행 시 BLOCK 반환)이라, settle() 은 **core 반환 shape 이 아니라 reason 코드의
enumerated 분할**로 판정한다:

**settle(op, core_result) 분할표:**

| 부류 | reason 코드 | 처리 |
|---|---|---|
| **APPLIED** | `applied=True` | CONSUMED · receipt = `node_ids`/`decision_id` |
| **IDEMPOTENT_DONE** (효과 이미 반영) | `nothing_to_save`(∧ `skipped_existing>0` ∧ `rejected=={}`) · `duplicate_already_applied` · `already_deprecated` · `pair_partial_exists` · `dup_decision` · `replace_same_content` | CONSUMED · receipt = **payload 에서 결정적 산출한 node_id**(`node:CONV:`+sent_hash) — core 반환에 node_ids 없어도 재구성(TAE-P2-07) |
| **TRANSIENT** (재시도 가능) | `backup_create_failed` · `sqlite_wal_incomplete`/`wal_abort` · `sqlite_checksum_mismatch`/`checksum_mismatch` · `staging_apply:<transient>` | **RELEASE**(consuming row 삭제) → APPROVED, no write. audit BLOCK row |
| **HARD_BLOCK** (정당 거부) | `pii_or_secret` · `a0_fail` · `confirm_phrase_mismatch` · `owner_*`/`ai_*` · `empty_selection` · `nothing_to_save`(∧ `skipped_existing==0` ∧ `rejected≠{}`) | **RELEASE** + owner 에게 사유 반환(무음 소각 0) |
| **RECOVER** (replace 반쪽) | `pending_replace_journal` | **RELEASE 아님** — §14-replace recovery(아래) |

> TAE-P2-02/04 정합: 이미-완료 BLOCK(IDEMPOTENT_DONE)과 transient 를 **reason 으로 명확 구분** →
> "block→RELEASE 무한 spin" 과 "transient 를 CONSUMED 로 소각" 둘 다 차단. `nothing_to_save` 는
> `skipped_existing`/`rejected` 로 IDEMPOTENT vs HARD_BLOCK 분기. `duplicate_already_applied` 는
> **registry↔node 존재 재조정**(registry 있고 node 없으면 IDEMPOTENT 로 오판 금지 — node 부재
> 확인 시 RELEASE + 재조정 안내, TAE-4).

**Crash 복구(save/pair/deprecate/mark).** reserve 후 finalize 전 crash → consumptions row =
CONSUMING(고아). lease 만료 시 다음 consume 이 takeover(§7). mutation 재실행 → 이미 있으면
IDEMPOTENT_DONE(node 결정적 산출로 receipt) → CONSUMED. 없으면 지금 생성 → CONSUMED. 최종 노드
정확히 1개.

**Crash 복구(replace · 비-멱등 · TAE-3/P2-03).** replace 는 deprecate(commit)+save(commit) 합성 →
crash 시 원본 deprecated·신규 부재·`pending_replace_journal` 잔존(이후 모든 replace BLOCK).
→ **consume 진입 시 해당 ledger 에 pending_replace_journal 있으면 approval 평가 전에
`recover_pending_replace`(pre-snapshot 복원 = 원본 active·신규 부재·journal 제거) 자동 실행** 후
재시도. 이로써 net effect = all-or-nothing(완전적용 or 완전복원). journal-block 은 RELEASE 가
아니라 RECOVER(승인 소각 0·반쪽변형 관찰 시 재조정). replace 의 이 복잡성이 과하면 **P1-A 에서
replace 만 fail-closed 유지(P1-B 로 이연)** 하고 save/pair/deprecate/mark/harvest 만 활성화하는
것이 §13 중단 규칙상 허용된 후퇴 경로다(구현 중 원자성 증명 실패 시).

## 15. reject / revoke / expire

- reject(PENDING→REJECTED)·revoke(APPROVED→REVOKED): owner CLI 가 EVENT store tombstone append.
- **revoke-vs-commit(TAE-P2-06)**: verify(step2)는 tombstone 을 먼저 본다. 추가로 reserve 성공 후
  **mutate 직전 tombstone 재확인**으로 verify~mutate 사이에 landing 한 revoke 를 차단한다(구현:
  approval_gate.authorize, yield 전). ★**한계(정직)**: mutate **도중** landing 한 revoke 는 잡지
  못한다 — 비-가역 core 는 되돌릴 수 없어 이미 owner 가 승인한 작업이 커밋된다. **주장 하향**:
  "revoke 는 mutate 직전 재확인 이전에 landing 하면 이긴다"(무조건 승리 아님). write-forge 아님
  (사전 승인된 작업의 취소 창 문제일 뿐).
- expire: TTL lazy 판정, 별도 write 0. expired ≠ consumed.
- cleanup: expired request/event/review lazy prune. 운영 ledger 대량 삭제 0.

## 16. Audit / receipt · 정직 보고

- ledger op consume 성공 → `db.audit_append(actor='human', action='approval_consume:<op>',
  result='ALLOW', reason_code=request_id)` hash-chain receipt. **mark 패리티**: mark 는 hit_events
  (audit_log row 없음) → approval consume 이 mark 에도 audit_log row 추가.
- **정직 보고 원칙(§4·§5 정합)**: 문서·CLI help·MCP description·SECURITY 어디서도 "FS/shell 병재
  호스트에서 사람 승인을 보장한다" 고 **주장하지 않는다**. 보장하는 것은 (1) provider 부재 시
  fail-closed, (2) binggupack MCP-only 표면에 approval-store write 도구 부재, (3) exact binding ·
  one-time consume · replay receipt · owner review. Wave4/최종 보고는 이 경계를 과장 없이 기술한다.

## 17. Privacy / non-retention (불변식 F · Wave2 R3-01/02 · TAE-6)

- **digest/consumption/event store = hash + 최소 metadata 만. 원문 대화 저장 0.**
- `summary` = **payload-agnostic 템플릿, handler 생성**(모델 미제공·topic 텍스트 0). batch_redact
  의존 제거(구조 PII regex 만이라 자유형 개인사 누락 — R3-02).
- **명시적·경계된 예외 = `approval_review` 레코드(§8b)**: owner 가 승인 전 실내용을 봐야 하므로
  payload 를 **candidate 와 동일한 PII/개인사 제외 게이트** 통과 후 owner-only·home·**cap+TTL**·
  **approve/reject/expire 시 purge** 로 보관. 이것이 payload 텍스트가 로컬에 잠깐 상주하는 **유일한
  곳**이며, active memory 도 raw conversation store 도 아니다(인정 트랙 검토 surface).
- request persistence(및 review 기록) **전에** PII/secret 게이트 적용.
- **pending cap**: owner-relevant 요청 보호(모델 스팸이 legit 요청 생성을 막지 못하도록 — TAE-6:
  per-operation sub-cap 또는 미조회 모델 스팸 우선 축출, owner-minted approval evict 0).
- 문서상 **candidate/request(=active memory 아님)** 과 **active memory** 구분(UGI 2트랙: 기록/인정,
  approval=인정 트랙 사람 주권).

## 18. MCP 인터페이스 · change surface

핵심 write 핸들러의 `actor="reader"` 사이트를 신규 `binggupack/mcp/approval_gate.py` 단일 호출로:

```python
with approval_gate.authorize(operation, payload, params, home) as auth:  # auth.actor ∈ {"human","reader"}
    r = core_mutation(db, ..., {"actor": auth.actor, "confirm": confirm}, ...)
    auth.settle(r)            # §14 분할표 · CONSUMED/RELEASE/RECOVER
return {..., **auth.response_extra()}   # write_available / receipt(node_id·nonce 제외) / reason
```

- no-approval → `auth.actor="reader"`(**정확히 이 문자열**) → core fail-closed. provider 미구성도 동일.
- `authorize` 는 `params["approval_id"]`(=request_id)로 EVENT store 를 **read-only** 조회.
  approval 생성 0 · secret 반환 0 · **응답에 nonce 0**(TAE-6).
- dry_run: write 0 · preview + `approval_required` + request_id + **review 레코드 기록**(cap/TTL/PII).
- **harvest_add/remove 도 같은 authorize 로 편입**(GAP 봉인) — canary 창에서 confirm-only 로 남기지
  않는다(TAE-7). legacy confirm-only(approval 없음) → `approval_required`(무음 통과 0).
- **change surface 파일**: `approval_gate.py`(신규) · `trusted_approval.py`(core 신규) ·
  `server_handlers.py`(7 사이트) · `binggu_schema.py`(테이블·ledger_id) · `binggu.py`(CLI) ·
  **`openbinggu_candidate_replace_ux.py:113` + `openbinggu_staging_write_selftest.py:183`
  denylist→allowlist 하드닝**(TAE-2) · **`gate_log.py` 음수-age fix**(TAE-P2-08).

## 19. CLI owner UX (approve = 비-TTY 하드거부)

```
binggu approvals                 # PENDING/APPROVED 목록(요약·op·ledger·만료). 조회 only.
binggu approval show <req-id>    # review 레코드의 exact payload + op + ledger + expiry 렌더
binggu approval approve <req-id> # EVENT 발행. 대화형 TTY 필수 — 비대화형 stdin/pipe = 하드 거부(exit≠0)
binggu approval reject <req-id> / revoke <req-id>   # tombstone
```

- `approve` 는 **단순 confirm 비교가 아니다** — review 레코드 실내용을 렌더 → 대화형 확인 → ≥128-bit
  nonce 생성 → owner-only EVENT store append.
- **`_resolve_human_ctx` 의 permissive default 를 재사용하지 않는다**(TAE-5). 비대화형(pipe/redirect)
  이면 warn-and-proceed 가 아니라 **거부·exit≠0**(`BINGGU_STRICT_HUMAN_GATE` 없이도 fail-closed).
  isatty 는 보조 필터로 명시 · 실질 보증은 §4/§5 배포 경계.
- MCP tool 은 approve/reject/revoke 직접 생성 불가 · approval secret 응답 반환 0 · owner 미검토
  payload 는 승인 범위 밖(review 레코드 == committed digest).

## 20. Migration (additive · idempotent · Wave2 R3 SOUND)

- `binggu_schema.py`: `approval_requests`·`approval_consumptions` 를 `_TABLE_COLUMNS` 에 추가
  (CREATE IF NOT EXISTS) · `SCHEMA_VERSION 1→2`. `_migrate` = ALTER-ADD-only 유지(행 손실 0).
- **ledger_id 는 user_version 게이트 밖에서 무조건 `INSERT OR IGNORE`**(downgrade/upgrade churn
  으로 ledger_id 누락 → binding 실패 방지 — R3 caveat).
- 구 ledger(approval 테이블 없음) open → 빈 approval 테이블 + approvals.jsonl 부재 = **auto-grant 0**.
- `user_version` monotonic(현재<목표일 때만 set).

## 21. Rollback

- feature/provider **기본 disabled**. 미구성 = P0 fail-closed(운영 무변).
- 스키마 additive(테이블 추가) — downgrade 시 구 코드는 신규 테이블 무시. 데이터 손실 0.
  destructive migration 0.

## 22. Old client compatibility

- confirm-only(approval/protocol 없음) → `approval_required`(무음 legacy 통과 0).
- protocol vN approval 을 vM 요청에 제시 → `binding_mismatch:protocol`.
- **CLI TTY 경로(owner 직접 `binggu save`/`--confirm`·save_gate 앵커) 무변경** — 유효 사람 승인
  채널 유지. trusted approval event 는 이를 일반화(제거 아님).

## 23. 기존 SAVE_INTENT V2/V2A 와 관계 (Wave2 R3-03/04 확대)

**Additive.** V2/V2A 는 transport 를 풀었고 "hosted/MCP 적재 = weak-auth save-INTENT · 권한은
로컬 게이트" 를 확립. 미완 = 로컬 authorization primitive(러너 게이트가 transported confirm 으로
`actor=human` 승격). P0 봉인 → **P1-A 가 그 자리에 trusted approval event.**

- **SUPERSEDED_IN_PART**: V08 §2 save_selected step1 · HOSTED §3 step5 · SPEAKER_AXIS §7 · CHANGELOG
  "save_candidate read-only 해제" · **V2A §0 의 "…confirm…에만 있음" 권한 절(clause)**(TAE R3-04:
  "적재 강도≠저장 안전" 부분은 KEEP, confirm-권한 절만 supersede 로 분리) · **D4_GATE_TABLE 의
  confirm="사람 발화 유래 증거"**(§23 목록 신규 추가).
- **UNTRUSTED_INTENT_ONLY**: HOSTED §1 confirm 라벨 · V2A save_intent description · README ChatGPT
  `SAVE n=승인` · **README hero/Commit 행 "채팅 중 SAVE n"**(키보드/hook 앵커 vs MCP/chat confirm
  구분 — TAE R3-04) · V2 flow 말미 confirm.
- **NOT_A_TRUSTED_APPROVAL_CHANNEL**: confirm-phrase equality gate.
- **STILL-OPEN → P1-B (신규·정직 라벨, TAE R3-03)**: **hosted save-intent runner**
  (`openbinggu_save_intent_outbox_runner.py:211` 이 `actor='human'`+confirm 으로 save_selected 호출
  = P0 패턴). §18/§25 는 MCP 핸들러만 재배선 → **runner 는 P1-A 가 봉인하지 않는다.** §23 라벨을
  "SUPERSEDED by P1-A" 로 오도하지 않고 "STILL-OPEN, P1-B" 로 명시. (runner 는 MCP 표면 미노출·owner
  CLI `hosted pull` 경유 — 신규 노출 아님.)
- **KEEP**: V2A §0 "적재 강도≠저장 안전" · HOSTED §0 transport≠authority · intent_id rehash
  (integrity, authorization 과 직교·병합 금지) · V2 HMAC/inbox/fail-closed flag/injection 격리 ·
  SPEAKER_AXIS §4 G4_no_auto 불변 · **SECURITY.md 위협모델(이미 trusted approval event 를 P1
  로드맵으로 명명 — 이 RFC 가 구현)** · CONSTITUTION(영구저장=사람 승인만).
- 삭제 0. 각 supersede 문장에 P1-A RFC 를 가리키는 상태 노트. **doc-lint 테스트**(§24)로 잔존
  "confirm=저장 권한/사람 승인" 문장에 상태 노트 없으면 실패.

## 24. Test matrix (§9 적대 · Wave1-D 28벡터 + Wave2 회귀)

TIER 1 (handler `_selftest`, temp home): legacy_confirm/actor_param/env_var/noninteractive/
demo/test actor cannot approve · no_isatty · partial_batch · old_client · no_provider ·
**6-core actor 파라미터 스윕**(TAE-2: {reader,auto,unapproved,Human,HUMAN,agent,system,'',None} →
human 외 전부 write 0).
TIER 2 (`scripts/openbinggu_trusted_approval_boundary_selftest.py`, REGRESSION 등재): id_guessing ·
replay_no_2nd_write · op/ledger/payload/protocol mismatch · batch_extension · raw/secret residue ·
**summary residue(이혼/이름/우울증 → summary 미포함)** · pending_cap(owner 요청 생존) · expired_cleanup ·
**harvest fail-closed(confirm 재현→write 0)** · **nonce-not-in-response** · valid_owner_exactly_one_write ·
**MCP TOOLS 표면에 file/shell write 도구 0**(TAE-1 이 보증 가능한 것).
TIER 3 (`tests/test_trusted_approval_e2e.py`, subprocess): concurrent_double_consume(exactly-one) ·
**crash_between_reserve_and_mutate → 재consume 정확히 1회·CONSUMED·receipt 비어있지 않음** ·
retry_receipt · **per-op crash-recovery(각 op: mutate 후 crash→재consume→CONSUMED·no-spin)** ·
**replace_crash(원본 active 복원·journal 잔존 0·반쪽변형 0)** · revoke_race(verify후·finalize전) ·
clock_rollback · symlink/case cross-ledger · migration_no_loss/idempotent · stdin_pipe(approve 거부) ·
**FS-co-loaded 정직성**(위조 approve 레코드+FS write → 문서가 보호를 주장하면 실패).
TIER 4 (`scripts/binggu_trusted_approval_binding_characterization_selftest.py`, no DB): dict_order ·
one_char · unicode NFC · bidi/control · field_reorder collision · **explicit flip → digest 상이**(TAE-P2-04) ·
**request_id \x1f 무충돌**(TAE-P2-09) · **mark recall_nonce 바인딩**.
**doc-lint**: 레거시 문서 confirm/SAVE-n 권한 문구 전수 → 상태 노트 없으면 실패(§23·R3-04).

**모든 BLOCK 공통 불변식**: `executed_write==False` · target node COUNT/state 불변 ·
hit_events/owner_acceptances/harvest 화이트리스트 불변 · `verify_chain()==True` · audit 실패 기록 ·
approval state 불법 전이 0 · **`OPERATING_PATHS` mtime map before==after(운영 ledger 미접촉)**.

**★실제 배송 vs 후속 (정직 · TA-COV-2/CC-3).** 이 PR 이 배송하는 명시 테스트:
`scripts/openbinggu_trusted_approval_boundary_selftest.py`(TIER-2·run_all 등재),
`scripts/binggu_trusted_approval_binding_characterization_selftest.py`(TIER-4·run_all 등재),
`tests/test_trusted_approval_e2e.py`(TIER-3: 승인흐름·reject 우선·운영 sentinel 3케이스),
`binggupack/mcp/server_handlers.py --selftest`(TIER-1 fail-closed 회귀·PW1~5), `binggupack/pack/smoke.py`
(9b/9c 앵커). **후속(P1-B)**: crash_between_reserve_and_mutate·per-op crash-recovery·replace_crash·
symlink/case cross-ledger·clock_rollback 의 **명시 subprocess-crash e2e**(보호 자체는 코드+boundary
하니스로 검증됨·전용 crash 재현 테스트 미배송)와 **doc-lint 자동화**(레거시 상태 노트는 §23 대로
수동 적용·자동 lint 미배송). 이 문서는 그 이상을 "배송된 테스트"로 주장하지 않는다.

## 25. Rollout 단계

1. schema + approval core(테이블·digest·provider abstraction·gate_log 음수-age fix·denylist 하드닝).
2. feature/provider **기본 disabled**.
3. local owner provider(CLI approve/reject/revoke·review 레코드 E2E, temp home).
4. MCP canary(save_candidate) — **단, harvest 는 canary 와 동시 편입**(confirm-only 창 금지·TAE-7).
5. 전체 mutation(pair/deprecate/replace/mark) 동일 core. **replace 원자성 증명 실패 시 replace 만
   P1-B 이연(§14)** — 나머지는 활성.
6. 전체 적대 테스트(TIER1~4·doc-lint) + full regression.
7. 문서/SECURITY/CHANGELOG 갱신(정직 경계 — §16).
8. commit/push/PR (merge/release/tag/live 금지).

## 26. Unresolved risks

- **R1.** reserve→mutate→finalize 는 단일 SQL tx 아님(replace 제약). 안전성은 reason 분할(§14) +
  lease(§7) + replace journal-recovery 에 의존 — 신규 op 추가 시 reason 분할 회귀 필수.
- **R2 (핵심·정직).** FS/shell 병재 호스트(Claude Code/Codex 기본)에서 P1-A 는 하드 통제가 아니라
  fail-closed 보존 intent-routing(§4·§5). 하드닝(store 를 FS-root 밖/OS keychain 비밀) = P1-B 옵션.
  이 PR 은 이를 **과장 없이 문서화**하고 보증하지 않는 것을 회귀로 고정한다.
- **R3.** `approval_review` 는 payload 를 잠깐 상주(cap/TTL/PII 게이트·purge). owner 검토 필수와
  raw-0 의 최소 타협 — 게이트 우회 시 개인사 상주 위험(테스트로 방어).
- **R4.** clock 로컬 시계 의존(monotonic receipt 로 rollback 완화·신뢰 앵커 아님).
- **R5.** hosted/web provider + hosted runner 봉인 = P1-B(§23 STILL-OPEN).
- **R6.** replace 원자성이 구현 중 과도하면 replace 만 fail-closed 유지·P1-B 이연(§14·§25-5).

## 27. Wave 2 사전 적대검증 해소 추적 (finding → 조치)

| finding | sev | 조치(이 v2) |
|---|---|---|
| TAE-1 | High | §4·§5·§16 정직 재범위화(FS-병재=intent-routing·하드통제 아님)·§24 TIER2/3 정직성 테스트. **보증 범위 축소로 해소**(과장 제거) |
| TAE-2 | High | sentinel 정확히 "reader"(§1·§18) + replace/c2_check denylist→allowlist 하드닝 + 6-core 스윕 테스트 |
| TAE-3 / P2-03 | High | §14 replace journal-recovery consume 배선 · RECOVER 부류 · §25-5 replace 이연 후퇴로 |
| P2-01 | High | §7 reserved_at+lease 단일 규칙 · §13/§14 모순 제거 |
| P2-02 | High | §14 명시적 reason 분할표(IDEMPOTENT_DONE/TRANSIENT/HARD_BLOCK/RECOVER) |
| P2-04 | High | §9 save digest 에 explicit/speaker/due 바인딩 · 렌더러=실행기 동일 explicit |
| R3-01 | High | §8b `approval_review` owner 검토 레코드 · §17 경계된 예외 · owner-saw==committed |
| TAE-4 | Med | §14 분할표 registry↔node 재조정 · nothing_to_save 분기 |
| TAE-5 | Med | §19 approve 비-TTY 하드거부(_resolve_human_ctx permissive 미재사용) |
| P2-05 | Med | §9 mark digest 에 recall_nonce 바인딩 |
| P2-06 | Med | §15 finalize 직전 tombstone 재확인 · 주장 하향 |
| P2-07 | Med | §14 receipt node_id payload 에서 결정적 산출 |
| R3-02 | Med | §17 summary payload-agnostic 템플릿 · review=candidate 제외 게이트 |
| R3-03 | Med | §23 hosted runner STILL-OPEN/P1-B 정직 라벨 |
| TAE-6 | Low | §12·§17·§18 nonce 응답 금지·receipt whitelist·pending cap owner 보호 |
| TAE-7 | Low | §18 harvest 동시 편입(confirm-only 창 0)·§5 capture_preview home-write 명시 |
| P2-08 | Low | §11·§18 gate_human_for 음수-age fix(실제 코드 변경) |
| P2-09 | Low | §8 request_id \x1f 구분자 |
| R3-04 | Low | §23 V2A §0 절 분리·README hero·D4 gate table 추가·doc-lint 테스트 |

---

**요약.** trusted approval event = owner-controlled·로컬·out-of-band 증거가 **하나의 mutation**
(exact payload+op+ledger+protocol)을 **정확히 한 번** 승인. provider 미구성 = P0 fail-closed(불변).
confirm/actor/env/MCP-arg/model-token/isatty 는 승인 증거 아님. 신뢰 경계 = "binggupack MCP tool
surface 는 approval store 에 쓰지 못한다" — **그리고 그 경계는 배포 형태에 의존한다**(FS/shell
병재 호스트엔 intent-routing·하드 통제 아님, 과장하지 않음). owner 는 `approval_review` 로 실내용을
보고 승인하며, owner-saw == committed.
