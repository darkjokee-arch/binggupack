# Trusted Event Profile — BingguPack Memory PR Spec

> **상태: v0.1-draft (프로젝트 초안).**
> 이 문서는 BingguPack 저장소의 **현재 코드 실측**을 공개 문서화한 초안이다. 표준·범용·vendor-neutral·타 프로젝트 채택·digest 호환을 주장하지 않는다. 독립 구현이 없으며 표준 단체 산출물이 아니다. 코어/CLI/DB 변경 0 — 서술만 한다.
> 상위 문서: `core-model-and-boundaries-v0.1-draft.md` (공통 의미 어휘). 자매 프로필: `interactive-save-profile-v0.1-draft.md` · `hosted-relay-profile-v0.1-draft.md`.

---

## 1. 개요

Trusted Event Profile 은 BingguPack 에서 **저장(save) 이 아닌 mutation** — 즉 노드 본문을 새로 쓰지 않고 기존 기억의 상태·관계를 바꾸는 owner 승인 이벤트 — 를 다룬다.

실소비되는 mutation operation:

- `accept` / `unaccept` — 후보(candidate) 기억의 accepted 표식 부여/해제
- `due` — 기억에 기한(due_date) 부여
- `resolve` — 기한/미결 항목을 결과(outcome)로 종결
- `import_edges` — 근거 기반 edge 일괄 반입 (HAG 동기화 경로)

이들은 **`approval_requests`(모델이 쓸 수 있는 sqlite)에 제안(Request)** 이 올라오고, **`approvals.jsonl`(owner 만 append 하는 이벤트 로그)에 approve event 가 발행**되어야 소비된다.

> **★ Interactive SAVE n 과 "동일 승인방식"이라 부르지 않는다.**
> Interactive Save Profile 의 SAVE n 은 UserPromptSubmit hook 이 사람의 발화를 기록하는 것으로 승인이 증명되고, 저장 게이트(`commit_bundle`/`apply_pack_in_txn`)로 소비된다. Trusted Event 는 **별도의 approve 이벤트(`approvals.jsonl`)로 승인이 증명되고, `approval_consumptions`(nonce 소비)로 소비된다.** 승인 증명 매체와 소비 경로가 다르므로 "동일 승인방식"이라는 표현은 이 문서에서 금지한다. 공통점은 상위 Core Model 의 불변식(preview/intent 만으로 저장 0 · 사람 승인만 write) 수준에서만 성립한다.

---

## 2. 신뢰주체 — Request ≠ Event

두 저장소를 분리하는 것이 이 프로필의 핵심이다.

| 축 | Request | Event |
|---|---|---|
| 저장소 | `approval_requests` (sqlite) | `approvals.jsonl` (append-only 로그) |
| 쓰기 권한 | **model-writable** (모델 tool 이 upsert 가능) | **owner-only append** |
| 부여하는 것 | 승인 대상 제안(PENDING)만 — **승인 권한 0** | 실제 approve/reject/revoke 결정 |
| 발행 경로 | `upsert_request` (권한 없는 제안) | `mint_approval` ← `cmd_approval approve` |

- `verify_event` 는 소비 판정 시 **`approval_requests` 를 신뢰하지 않는다.** `find_approve` 로 `approvals.jsonl` 의 approve 이벤트만 조회한다. 따라서 모델이 `approval_requests` 를 아무리 조작해도 승인으로 이어지지 않는다.
- approve 발행(`cmd_approval` 의 `approve` 서브커맨드) 하드게이트:
  - **TTY 전용** — 대화형 터미널에서 owner 가 직접 `APPROVE <rid8>` 를 정확히 타이핑해야 한다 (`channel=cli_tty`, `isatty` 확인 후).
  - **비대화형이면 exit 2** — 파이프/스크립트 입력 거부.
  - **env 백도어 제거** — 환경변수로 approve 를 우회하는 경로 없음.

> **정직성 경계**: 이 하드게이트는 "모델의 tool surface 가 approval store 에 write 하지 못하는 배포"에서만 강하다. 같은 호스트에서 Filesystem MCP·Bash MCP 를 동반하면 approve 파일을 직접 만들 수 있으므로 위협모델 밖이다. 서명/HMAC 은 도입돼 있지 않다.

---

## 3. 실소비 operation (5개) 과 프로필 밖

### 3.1 실소비 5 operation

| operation | 경로 | 배선 |
|---|---|---|
| `accept` | CLI `--approval-id` | `binggu.py:1223` → `_mutation_via_approval:966` → `approval_gate.authorize:120` |
| `unaccept` | CLI `--approval-id` | 〃 |
| `due` | CLI `--approval-id` | 〃 |
| `resolve` | CLI `--approval-id` | 〃 |
| `import_edges` | HAG 동기화 | `hag_sync_adapter.py:256` → 직접 core (approval_gate 미경유) |

경로 A(accept/unaccept/due/resolve) 는 `approval_gate.authorize` 를 거친다: `canonical_payload_digest` → `compute_request_id` → (approval_id 없으면) `upsert_request` + `write_review`=approval_required → id≠rid 이면 `binding_mismatch` → `verify_event` → `reserve` → `tombstone` 확인 → `actor=human` → `settle` → `finalize_consumed`(receipt + state=consumed + purge_review).

경로 B(import_edges) 는 `approval_gate` 를 우회하고 core 를 직접 호출한다.

### 3.2 프로필 밖 (binding_fields 정의는 존재하나 현행 미소비 — 명확 제외)

아래 operation 들은 `trusted_approval.py` 에 binding_fields 정의가 살아 있지만 **현재 소비 경로가 없다.** 제외 사유는 두 가지다: (a) CLI `--approval-id` 로 배선되지 않고 save-n 경로로 처리되거나, (b) MCP write 핸들러가 2026-07-13 에 제거되었다.

- `deprecate`, `replace` — binding 정의 있음 · CLI 미배선(save-n 경로)
- `save_candidate`, `pair`, `mark_hit`/`mark_miss`, `harvest_add`, `harvest_remove`, `hosted_bundle` — MCP write 핸들러 2026-07-13 제거
- `confirm_edges` — `import_edges` 의 별칭 · CLI 는 sync 단계까지만 수행

이들의 binding_fields 는 §5 에 함께 열거한다 (정의 존재 사실을 정직하게 남기되, 소비되지 않음을 명시).

---

## 4. operation별 binding_fields

binding_fields 는 payload_digest 계산에 들어가는 정규 필드 집합이다 (`trusted_approval.py:142`).

### 4.1 실소비 5개 — 완전 열거

| operation | binding_fields |
|---|---|
| `accept` / `unaccept` | `{index, id8, reason}` |
| `due` | `{node_id, due_date}` |
| `resolve` | `{node_id, outcome, reason}` |
| `import_edges` | `{edges: [{src, dst, rel, evidence[sorted]}]}` — `edges` 는 `(src, dst, rel)` 로 정렬 |

### 4.2 프로필 밖 (정의 존재 · 현행 미소비)

| operation | binding_fields | 제외 사유 |
|---|---|---|
| `save_candidate` | `{text, indices[sorted], explicit, speaker, due_date}` | MCP write 제거 |
| `pair` | `{owner_text, ai_text, owner_pick, ai_pick, by, relation, due_date}` | MCP write 제거 |
| `deprecate` | `{index, id8, reason}` | CLI 미배선(save-n 경로) |
| `replace` | `{index, id8, new_sentence, reason}` | CLI 미배선(save-n 경로) |
| `mark_hit` / `mark_miss` | `{recall_query, index, outcome, domain, recall_nonce}` | MCP write 제거 |
| `harvest_add` | `{kind, url, keyword}` | MCP write 제거 |
| `harvest_remove` | `{source_id}` | MCP write 제거 |
| `hosted_bundle` | `{items: [{intent_id, digest} sorted]}` | MCP write 제거 |
| `confirm_edges` | (= `import_edges` 별칭) | CLI 는 sync 까지만 |

---

## 5. Canonicalization

Trusted Event 는 다른 두 프로필과 근본적으로 다른 정규화·digest 산식을 쓴다. 통합 대표값(단일 TAE) 은 존재하지 않는다.

### 5.1 상수

```
PROTOCOL_VERSION = "tae-1"
_UNIT            = "\x1f"     # ASCII Unit Separator, 필드 구분자
```

### 5.2 문자열 정규화 `_clean_str`

Interactive Save 의 `_norm`(공백 축소만) 과 달리, Trusted Event 는 문자열을 강하게 정규화·거부한다.

```
_clean_str(s):
    NFC 정규화
    bidi 제어문자 9종 거부
    유니코드 Cc(제어) / Cf(포맷) 카테고리 거부   # 단 "\t", "\n" 은 예외 허용
    → 위반 시 binding_reject: control_char
```

리스트 필드는 정렬되어 순서 변조를 무력화한다 (예: `import_edges` 의 `edges` 는 `(src,dst,rel)` 정렬, `save_candidate.indices` 는 정렬).

### 5.3 payload_digest (전체 64 hex, 절단 없음)

```
fields  = binding_fields(op, payload)                      # 각 문자열 _clean_str 적용, 리스트 정렬
canon   = json.dumps(fields, sort_keys=True,
                     ensure_ascii=False, separators=(",", ":"))
material = "tae-1" + "\x1f" + op + "\x1f" + canon
payload_digest = sha256(material.encode("utf-8")).hexdigest()   # 전체 64 hex (소문자)
```

- `sort_keys=True` + `separators=(",",":")` → 키 순서·공백 비의존 canonical JSON.
- `ensure_ascii=False` → 유니코드를 그대로 UTF-8 인코딩.
- 다른 프로필들의 절단(preview_ref[:16], node_id[:8], intent_id[:16]) 과 달리 **payload_digest 는 절단하지 않고 64 hex 전체** 를 쓴다.

### 5.4 request_id (24 hex)

```
request_id = sha256(
    "\x1f".join(("tae-1", op, payload_digest, str(ledger_id)))
).hexdigest()[:24]
```

> **★ `ledger_id` 는 재현 필수 핀**: request_id 재료에 합성 `ledger_id` 가 포함된다. 고정 KAT(테스트 벡터)에서 이 값을 manifest 에 핀으로 고정하지 않으면 재현이 불가능하다.

### 5.5 nonce (32 hex)

```
nonce = secrets.token_hex(16)      # 16 byte = 32 hex (소문자)
```

소비 시 `approval_consumptions` 의 PK 로 사용되어, IntegrityError 로 **single-winner**(최초 1회만 성공) 를 보장한다.

모든 digest·id·nonce 는 **소문자 hex** 이다.

---

## 6. 상태 (persisted vs derived)

> **★ 공통 상태 전이(공통 상태기계) 없음.** 아래는 이 프로필 고유 상태다. request 의 승인 상태와 consumption 의 소비 상태는 **별개의 축** 이다.

### 6.1 persisted 상태

| 저장소 | 상태 컬럼 | 값 |
|---|---|---|
| `approval_requests` | `state` | `pending` → `consumed` |
| `approval_consumptions` | `state` | `consuming` → `consumed` |
| `approvals.jsonl` | `record_type` | `approve` / `reject` / `revoke` |

레코드 스키마:

```
approval_requests {
  request_id PK, protocol_version, operation, payload_digest,
  ledger_id, summary, state='pending', created_at, expires_at
}
approval_consumptions {
  approval_nonce PK, request_id, state, reserved_at, receipt, consumed_at
}
approve event (jsonl) {
  request_id, protocol_version, operation, payload_digest, ledger_id,
  approval_nonce(128bit), approved_at, expires_at,
  approver_channel, record_type="approve"
}
```

### 6.2 derived 상태 (`verify_event` 런타임 판정)

`verify_event` 는 소비 시점에 아래를 판정한다 (저장하지 않음):

- **freshness** (`DEFAULT_TTL = 900`초):
  - `now < approved_at` → `approval_time_invalid` (승인 시각 이전에 소비 시도 = 시계 역행)
  - `now > expires_at` → `approval_expired`
- **binding mismatch 4종**: `op` / `payload_digest` / `ledger_id` / `protocol` 중 하나라도 불일치 → 거부
- **tombstone 우선**: `revoke` / `reject` 이벤트가 있으면 approve 보다 우선하여 거부
- **replay**: nonce PK 소비 후 `finalize` → `state=consumed`. 재사용 시 `already_consumed`(write 0). 실패/transient 는 `release`(재시도 가능).

---

## 7. Receipt

소비 성공 시 발급되는 영수증. **nonce 는 절대 포함하지 않는다.**

```
# 경로 A (accept/unaccept/due/resolve) — derive_receipt
{ request_id, operation, node_ids, decision_id }        # nonce 절대 0

# 경로 B (import_edges) — hag_sync_adapter.py:388
{ request_id, operation, imported_edge_ids, actor }     # nonce 미포함
```

receipt 는 `approval_consumptions.receipt` 에 저장되고, 소비 응답으로 owner 에게 반환된다.

---

## 8. 공개 CLI vs 내부

| 구분 | 노출 |
|---|---|
| **공개 CLI** | `approvals` (list): `request_id`, `operation`, `summary`(payload-agnostic), `state`, `expires_at` — **raw payload 0** · `approvals show <rid>`: review items (PII 위험 필드는 `_pii_safe` placeholder) · 소비 응답: `write_available`, `request_id`, `reason`, `receipt` |
| **내부 전용 (3중 제거)** | `approval_nonce` · `payload_digest` · 시각 필드(`approved_at`/`reserved_at`/`consumed_at`) · review 파일(소비 후 `purge`) |

`approval_nonce` 와 `payload_digest` 는 공개 CLI·목록·상세 어디에도 노출되지 않는다.

---

## 9. UNSUPPORTED (현재 검증 표면 없음 — optional 아님)

> UNSUPPORTED ≠ optional. optional 은 "구현 시 선택 제공해도 적합", UNSUPPORTED 는 "현재 미제공 검증 표면"이다. PASS 로 위장하지 않고 문서·test vector 양쪽에 명시한다.

- **approve 이벤트 자동생성 경로 전체** — approve 발행은 **대화형 TTY 전용** 이다. 비대화형 입력은 exit 2, Unix 에서는 PTY 로만 테스트되며 Windows 에서는 skip 된다. 테스트용 `test_double` 채널은 wheel 배포에서 제거됐다.
  - 결과: **소비 vector(정상 approve → consume)는 CI 에서 실제로 생성할 수 없다.** PTY approve 는 "사람 증명" 이 아니다. 따라서 소비 경로는 재현 가이드(illustrative-only)로만 문서화하며 고정 KAT 로 착각하면 안 된다.
- **MGB-03** (만료 판정의 결정적 재현) — approve 는 wall-clock 에 의존하고 `--now` 주입 지점이 없어 900초 실대기 없이는 만료를 결정적으로 재현할 수 없다.
- **MGB-10** (변조 탐지 standalone 검증) — 임의 payload/nonce/receipt 를 독립적으로 검증하는 standalone tamper CLI 가 부재하다. 변조 탐지는 소비 시도의 부수효과(binding_mismatch·already_consumed 등)로만 관찰된다.

digest 순수함수(§5) 는 approve/hook 없이 결정적으로 재계산되므로 고정 KAT 로 CI 검증 가능하다. UNSUPPORTED 는 위 "사람 기원 · 실서비스" 영역에 한한다.

---

## 10. Profile ID

- **문서 라벨**: `doc-profile: trusted-event`
- 이 라벨은 **문서용 네임스페이스일 뿐이며 해시 재료에 절대 포함하지 않는다.** 재현자는 실제 코드 재료 문자열(§5 의 `"tae-1"` 등)을 그대로 사용해야 한다.
- **wire version**: 이 프로필의 digest 재료에 실제로 들어가는 버전 문자열은 `tae-1` (`PROTOCOL_VERSION`) 이다. `trusted-event` 와 `tae-1` 를 혼동하지 말 것 — 전자는 문서 라벨, 후자는 산식 상수다.
