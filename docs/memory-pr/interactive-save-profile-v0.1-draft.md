# Interactive Save Profile — v0.1-draft

> **상태 배너 (v0.1-draft):** 본 문서는 **BingguPack reference implementation** 의 현재 동작을 서술한 프로젝트 초안이다. **표준·범용 규격이 아니며**, vendor-neutral / 타 프로젝트 채택 / 독립 구현 / digest 호환을 주장하지 않는다. 여기 적힌 산식·필드·경로는 "현재 BingguPack 이 지원하는 경로를 외부가 재현"하기 위한 참조일 뿐이다. spec 확정·구현·병합은 재-4CLI blocker 0 + owner GO 이후에만 진행한다.
>
> **Profile ID (문서 라벨):** `interactive-save` — 이 문자열은 **문서용 네임스페이스**이며 **해시 재료에 절대 넣지 않는다**. Interactive Save 는 wire version 태그가 없다(순수 함수, §6·§10 참조).

---

## 1. 개요 — 대표적인 Memory PR 흐름

Interactive Save 는 BingguPack 이 기억을 저장하는 **대표 흐름**이자 세 프로필(Interactive / Trusted / Hosted) 중 가장 기본이 되는 경로다. Hosted Relay 프로필의 최종 저장도 이 프로필의 `commit_bundle` 게이트로 수렴한다.

핵심 라이프사이클:

```
candidate (후보 제안·저장 0)
   → preview (사람이 읽을 수 있는 후보 목록·원문 미영속)
      → 사람의 "SAVE n" 발화 (승인)
         → active memory commit (candidate=1·promotion_allowed=0 노드 삽입)
```

공통 불변식(Core Model 과 공유):

1. preview / intent 만으로는 저장이 **0** 이다. 후보 제시는 저장이 아니다.
2. 저장된 노드는 항상 `candidate=1` · `promotion_allowed=0` — **정본으로 자동 승격되지 않는 개인 기억**이다. (승인 전이라는 뜻이 아님. active 상태 노드도 candidate=1 이다. state 축과 candidate 축은 별개다.)
3. fail-closed: 기본 actor 는 `reader` 다. 사람 승인이 없으면 write 로 넘어가지 못한다.
4. 사람 승인(모드별 상이)만이 write 를 발생시킨다.

---

## 2. 신뢰 주체 — 승인 3모드 (정직 서술)

승인 판정은 `_resolve_human_ctx` (`binggu.py:917`) 의 **fail-closed 3분기**로 결정된다. 기본값은 `reader` 이며, 아래 조건을 통과해야만 `human` 으로 승격된다. core 게이트 `c2_check` 는 actor ≠ human 이면 `G4_no_auto` 로 저장을 거부한다.

| 모드 | 조건 | 판정 | 신뢰 강도 |
|---|---|---|---|
| `save_gate_ref` | UserPromptSubmit hook 이 `(preview_ref, idx)` 를 기록 | `human` | 강함 (아래 ★ 참조) |
| `cli_command` | `CLAUDECODE` 미설정 상태의 직접 터미널 입력 (isatty 무관·hook 불필요) | `human` | 중간 (셸 접근 주체 = human 가정) |
| `denied` | `CLAUDECODE` 가 truthy | `reader` / `agent_session_unanchored` (거부 전용) | — (소프트 신호) |

### ★ "AI 위조 불가"의 정직한 범위

"AI 가 위조할 수 없다"는 주장은 **`save_gate_ref` 모드 + `CLAUDECODE` 세션 조합에서만** 성립한다. 이유:

- `save_gate_ref` 모드는 UserPromptSubmit **hook 이벤트**로 승격된다. AI(모델)는 hook 이벤트를 발화할 수 없으므로 **이 모드 한정으로** 위조가 불가능하다.
- `CLAUDECODE` 는 **제거 가능한 소프트 신호(환경변수)** 일 뿐이며, 강한 암호학적 증명이 아니다. 환경변수를 지운 plain 터미널, 또는 Filesystem/Bash MCP 를 동반한 세션은 **위협 모델 밖**이다.
- 따라서 `cli_command` 모드는 "셸 접근 주체가 human"이라는 **가정**에 기댄 판정이며, save_gate_ref 만큼 강하지 않다.

이 프로필은 위 3모드의 신뢰 강도 차이를 숨기지 않는다. 전역적 "AI 위조 불가"는 주장하지 않는다.

---

## 3. 코드 경로 체인 (파일:라인)

```
capture_preview   openbinggu_conversation_capture_preview.py:97   (write 0·순수 함수)
   → cmd_preview            binggu.py:848
   → write_last_preview     gate_log.py:197                       (hash 만 기록·원문 0)
   → [사람] "SAVE n"
   → SAVE n hook            binggu_save_gate_hook.py:28           (UserPromptSubmit)
   → gate_record_from_prompt gate_log.py:273
   → _resolve_human_ctx     binggu.py:917                         (3분기 fail-closed)
   → save_selected          candidate_save.py:163
   → staging_apply
   → apply_pack_in_txn      staging_write_selftest.py:277
                            (INSERT nodes candidate=1, promotion_allowed=0, state='active')
```

- `capture_preview` 와 `write_last_preview` 는 **저장을 하지 않는다** (hash 만). 실제 노드 INSERT 는 사람 SAVE n 이후 `apply_pack_in_txn` 단 한 곳에서 일어난다.
- Hosted Relay 프로필은 `commit_bundle` 을 통해 이 체인의 `_resolve_human_ctx → apply_pack_in_txn` 부분으로 수렴한다(별도 저장 산식 아님).

---

## 4. Request / Event 객체와 필드

### Request (제안 단계·모두 미영속 또는 hash-only)

| 대상 | 내용 | 원문 저장 |
|---|---|---|
| preview 후보 dict | `{sentence, label_kind, rule_id, semantic_subtype, capture_reason, gate, a0_verdict, candidate: True}` | 메모리 상 (미영속) |
| `last_preview_candidates.json` | `{ts, pref, explicit, items:[{idx, sh}]}` | **원문 0** (idx + sh 해시만) |
| `save_gate_log.jsonl` | `{pref, idxs, ts, source}` | 선택 인덱스·pref 만 |

### Event (commit 단계·실제 저장)

- **nodes**: `{node_id, node_type, sentence, candidate=1, promotion_allowed=0, state='active', pack_id, content_hash, speaker}`
- **edges**: `evidence_supports`
- **evidence**: `EVC-CONV-…`, `source_pointer_id = "conv-self:" + h8`, `redaction_policy = "v1"`

핵심: preview 산출물(`last_preview_candidates.json`)은 **원문을 담지 않고 `sh` 해시만** 담는다. 원문은 commit 시점의 `nodes.sentence` 로만 영속된다.

---

## 5. Canonicalization (완전·정확)

Interactive Save 는 **4개의 해시**를 쓰며, 절단 폭이 각각 다르고 **전부 소문자 hex** 다.

### 5.1 정규화 함수 `_norm`

```python
def _norm(s):
    return re.sub(r"\s+", " ", s).strip()
```

- **공백 축소(연속 공백 → 단일 스페이스) + 앞뒤 strip 만** 한다.
- **NFC 정규화 없음. bidi/control 문자 거부 없음.** (이 점이 Trusted Event 프로필의 `_clean_str` = NFC + bidi 9종/Cc/Cf 거부 와 근본적으로 다르다.)
- 해시 입력은 UTF-8 로 encode 한다.

### 5.2 preview_ref (2단계 해시)

```python
# 1단계: 각 후보 문장의 sh (8 byte = 16 hex)
sh = sha256(_norm(sentence).encode())[:16]

# 2단계: idx(1-based) 와 sh 를 결합
#   행 구분자 = "\n", idx-sh 구분자 = ":"
joined = "\n".join(f"{idx}:{sh}")     # idx 는 1-based

preview_ref = sha256(joined.encode())[:16]
```

### 5.3 node_id

```python
node_id = "node:CONV:" + sha256(_norm(sentence))[:8]
# 정규식: ^node:CONV:[0-9a-f]{8}$
```

### 5.4 preview_id (정규화 없음 ★)

```python
preview_id = sha256(raw_text.encode())[:8]   # _norm 을 거치지 않는 raw 원문 해시
```

### 5.5 dedup 키

```python
dedup = sha256(_norm(s))[:12]
```

### 5.6 절단 폭·상수 요약

| 해시 | 입력 | 절단 폭 |
|---|---|---|
| `preview_ref` | 2단계(sh 결합) | `[:16]` |
| `sh` (후보별) | `_norm(sentence)` | `[:16]` |
| `node_id` | `_norm(sentence)` | `[:8]` (접두 `node:CONV:`) |
| `preview_id` | **raw** (정규화 X) | `[:8]` |
| `dedup` | `_norm(s)` | `[:12]` |

- `GATE_WINDOW = 3600s` (env `BINGGU_SAVE_GATE_WINDOW` 로 조정·`0` = 무한).
- freshness `gate_human_for_ref`: `age < 0`(clock 역행) → False · `age > window` → False · **all-or-nothing**.

### 5.7 null / empty 처리

- 빈 sentence 는 후보에서 **제외**한다.
- 후보 rows 가 비어 있으면 `preview_ref = sha256(b"")[:16]`.

### 5.8 mismatch reason 코드 (예외 아님)

불일치는 예외를 던지지 않고 **reason 코드**로 반환한다:

| reason 코드 | 의미 |
|---|---|
| `G4_no_auto` | actor ≠ human (승인 없이 자동 저장 시도) |
| `confirm_phrase_mismatch` | 확인 문구 불일치 |
| `empty_selection` | 선택된 인덱스 없음 |
| `preview_required_mismatch` | preview_ref 불일치 (선행 preview 필요) |

---

## 6. Persisted / Derived 상태

Interactive Save 는 **공통 상태 기계를 갖지 않는다** (프로필별 상태만 존재).

| 구분 | 항목 | 저장 위치 |
|---|---|---|
| **persisted** | `nodes.state` (`active` / `deprecated` / `tombstoned`) | ledger nodes |
| **persisted** | `candidate` (컬럼·항상 1) | ledger nodes |
| **persisted** | `save_gate_log` ref 행 | `save_gate_log.jsonl` |
| **derived** | `actor` / `actor_source` (`_resolve_human_ctx` 런타임 계산) | **미저장** (계산 결과) |

- `state` 축과 `candidate` 축은 **별개**다. active 노드도 candidate=1 이다.
- `actor` / `actor_source` 는 요청 시점에 매번 계산되며 영속되지 않는다.

---

## 7. 공개 CLI 필드 vs 내부 전용

| 구분 | 필드 |
|---|---|
| **공개 (CLI 표면)** | preview 표(도장 / 문장 / 캡처 근거 / 도장 근거 / 헌법 판정) · `preview_id` · save 결과(`applied` / `saved` / `skipped` / `reason` / `pack_id`) |
| **내부 전용** | `preview_ref` · `sh` (json/모듈에서만) · `node_id` · `content_hash` · `candidate` · `state` · `speaker` · `source_pointer_id` · `actor` / `actor_source` |

- `preview_ref` 와 `sh` 는 `last_preview_candidates.json` / 모듈 API 에서만 접근 가능하며 **CLI 로 출력되지 않는다**(§8 UNSUPPORTED).
- Recall / Explain 표면에서의 필드 노출 단위는 Core Model 문서의 Recall 계약표를 따른다(`list` = id8 · `explain` = 전체 node_id · `recall`/`why` Hot = ID 미노출).

---

## 8. UNSUPPORTED (≠ optional)

optional 은 "구현 시 선택 제공해도 적합"이고, UNSUPPORTED 는 "**현재 미제공 검증 표면**"이다. 아래를 optional 로 낮춰 표기하지 않는다.

| UNSUPPORTED 항목 | 사유 |
|---|---|
| `CLAUDECODE` 세션 자동 SAVE | `save_gate_ref` 모드는 hook 이벤트로만 승격 → AI 가 위조 불가. 자동 저장 경로 없음. |
| `preview_ref` CLI 미출력 | json / 모듈 API 로만 노출. CLI 표면에 `preview_ref` 를 출력하는 명령 없음. |
| freshness 만료 결정적 재현 | `GATE_WINDOW = 3600s` 를 결정적으로 재현하려면 **실제 1시간 대기**가 필요(`--now` 주입 경로 없음). |

이 항목들은 문서와 test vector 양쪽에 UNSUPPORTED 로 명시되며, 고정 KAT(digest 순수 함수)로 착각시키지 않는다.

---

## 9. Profile ID 와 wire version

- **문서 라벨(profile ID):** `interactive-save`. **해시 재료가 아니다.** 재현자는 이 문자열을 어떤 digest 계산에도 넣지 않으며, §5 의 실제 코드 재료 문자열(`_norm(sentence)` 등)을 그대로 사용한다.
- **wire version:** Interactive Save 는 **버전 태그가 없다**(순수 함수). 대조 참고:
  - Trusted Event digest = `tae-1` (PROTOCOL_VERSION)
  - Hosted intent = `schema_ver = 1`
  - **Interactive = 버전 태그 없음**

---

## 부록 — test vector 소재 (참고)

PII 없는 합성 데이터로 아래 6종을 재현할 수 있다(정식 vector 파일은 owner GO 후 `vectors/` 에 생성):

1. candidate 생성
2. preview 영속(`last_preview_candidates.json` · 원문 0 확인)
3. 정상 SAVE (plain 터미널 · `CLAUDECODE` unset → `cli_command` human)
4. mismatch (reason 코드 반환)
5. 비승인 부재 (`CLAUDECODE` 세션 → `G4_no_auto`)
6. commit 결과 (nodes candidate=1 · promotion_allowed=0)

고정 KAT 로 검증 가능한 것은 §5 의 canonicalization digest(preview_ref / node_id / preview_id / dedup) 순수 함수 계산뿐이다. 정상 SAVE commit(hook 경유)·freshness 만료는 illustrative-only(재현 가이드만) 로 분류한다.
