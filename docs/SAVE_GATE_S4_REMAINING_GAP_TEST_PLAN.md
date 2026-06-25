# save-gate S4 — 잔여 GAP characterization 테스트 설계 (design-only)

**기준 HEAD:** `f642fd1`
**성격:** **design-only.** 구현 0 · 코드 이동 0 · 테스트 코드 0 · semantic change 0.
본 문서는 `docs/SAVE_GATE_S4_CHARACTERIZATION_DESIGN.md` §2/§3 의 **잔여 GAP 21건**을
위험도·함수군으로 재분류하고, 각 GAP 별 "현재 동작 pin" 케이스 1줄을 **현행 코드 실제 동작 그대로**
기술한 설계서다. **새 동작·새 분기 요구 0** — 전부 읽은 코드의 현재 반환/부작용을 스냅샷한다.

**판정/HOLD:**
- **S4 implementation HOLD 유지.** 본 문서는 테스트 케이스 설계만 — 어떤 코드 이동·변경도 승인하지 않는다.
- **actual write core**(`staging_apply` + `save_selected` + `commit_selected`)는 **S4-6 마지막 / 영구 HOLD 후보** — 본 plan 의 케이스가 GREEN 이어도 write core 이동 승인이 아니다.
- 본 plan 의 테스트는 **production/gate-critical 코드 touch 0** 의 tests-only characterization 으로만 추가될 수 있으며, owner approval 토큰 전까지 구현 진입 0.

**선행 문서:** `docs/SAVE_GATE_S4_CHARACTERIZATION_DESIGN.md`(§2 케이스 표·§3 매핑·잔여 GAP 목록).
본 문서는 그 §2 잔여 GAP 21건(**A4 A6 A10 B9 B10 C2 C3 D3 D9 D10 E3 E6 E7 H4 H5 J3 K4 L2 N2 O3 O4**)만 다룬다.

---

## 0. 잔여 GAP 21건 — Worker 분담 표기

설계 문서 §2 잔여 GAP 중, 본 plan(Worker A)은 **저위험·관찰분류 설계**까지만 담당한다.
다음 8건은 **Worker B 가 별도로 테스트를 추가**하므로, 본 plan 에서는 "B 담당" 표기 + 현재 동작 pin 1줄만 기술하고 **테스트 코드는 작성하지 않는다.**

| Worker | GAP |
|---|---|
| **B 담당**(별도 테스트 추가) | H4 · H5 · J3 · K4 · L2 · N2 · O3 · O4 |
| 본 plan 설계 대상(분류+케이스 설계) | A4 · A6 · A10 · B9 · B10 · C2 · C3 · D3 · D9 · D10 · E3 · E6 · E7 (+ B 담당 8건도 분류·pin 1줄 기술) |

> 본 plan 은 **설계 문서 1개**만 산출한다. production 코드/테스트 파일 생성·수정 0.

---

## 1. 위험도 × 함수군 재분류 표 (잔여 21건)

**위험도 기준:**
- **저위험(read/validation)** = 순수 판정·read·인프라 보조. write core 본체 미경유 또는 BLOCK 가드(write 0 경로).
- **고위험(write-core 인접)** = actual ledger/sqlite write 본체에 직접 인접하거나, write 부작용 동반(snapshot/audit/transaction). 이동 시 byte-identical 증명 부담 큼.

**함수군:** c2_check · staging_apply · tombstone · StagingDB · save_selected · deprecate_g3 · save_gate.

| GAP | 함수군 | 대상 함수:줄 | 위험도 | 분류 사유 | Worker |
|---|---|---|---|---|---|
| **A4** | c2_check | `c2_check`:167 | 저위험 | 순수 판정(reason 반환·write 0) | A |
| **A6** | c2_check | `c2_check`:167 | 저위험 | 순수 판정(reason 반환·write 0) | A |
| **A10** | c2_check | `c2_check`:167 | 저위험 | 판정 순서 고정(분기 순서·write 0) | A |
| **B9** | staging_apply | `staging_apply`:187 + `snapshot`:82 | **고위험** | actual write 본체 ALLOW 경로·snapshot 파일 부작용 동반 | A |
| **B10** | staging_apply | `staging_apply`:187 | **고위험** | write 본체 BLOCK 경로·audit before==after 부작용 단언 | A |
| **C2** | tombstone | `tombstone`:230 | **고위험** | state write 본체(UPDATE)·미존재 경로 반환 | A |
| **C3** | tombstone | `tombstone`:230 | **고위험** | write_lock+snapshot+audit ALLOW 부작용 동반 | A |
| **D3** | StagingDB | `write_lock`:91 | 저위험 | write 인프라 보조(lock 재진입 허용 판정·INSERT 미동반) | A |
| **D9** | StagingDB | `snapshot`:82 | **고위험** | write 본체가 호출하는 영속 부작용(파일 생성+checkpoint) | A |
| **D10** | StagingDB | `store_checksum`:115 | 저위험 | 순수 read 해시(결정성·write 0) | A |
| **E3** | save_selected | `save_selected`:54 | **고위험** | G4① write core 게이트(L73 BLOCK 가드) | A |
| **E6** | save_selected | `save_selected`:54 | **고위험** | write core 내부 A0 거부 분기(rejected 누적) | A |
| **E7** | save_selected | `save_selected`:54 | **고위험** | write core 내부 PII 재스캔 거부 분기 | A |
| **H4** | deprecate_g3 | `deprecate_item`:76 | 저위험 | sqlite write 가드(BLOCK 경로·write 0) | **B** |
| **H5** | deprecate_g3 | `deprecate_item`:76 | 저위험 | sqlite write 가드(BLOCK 경로·write 0) | **B** |
| **J3** | deprecate_g3 | `resolve_review`:159 | 저위험 | sqlite write 가드(BLOCK 경로·write 0) | **B** |
| **K4** | deprecate_g3 | `classify_harvest_item`:221 | 저위험 | sqlite write 가드(BLOCK 경로·write 0) | **B** |
| **L2** | save_gate | `gate_record`:121 | 저위험 | gate-log append(빈문장 skip·운영 ledger 별개 파일) | **B** |
| **N2** | save_gate | `write_last_preview`:175 | 저위험 | preview 영속(빈문장 skip·hash-only·atomic) | **B** |
| **O3** | save_gate | `gate_record_from_prompt`:189 | 저위험 | hook 진입점 가드(파일부재/파싱실패→0·write 0) | **B** |
| **O4** | save_gate | `gate_record_from_prompt`:189 | 저위험 | hook 진입점 가드(idx 매칭 0→0·write 0) | **B** |

**위험도 집계:** 저위험 **13** / 고위험 **8** (= B9·B10·C2·C3·D9·E3·E6·E7).

---

## 2. GAP 별 "현재 동작 pin" 케이스 (현행 코드 실제 동작 그대로)

표기: **입력 조건 → 기대 반환/부작용(현재 동작)**. 모든 케이스 **temp DB / 운영 store 불변** 전제.
인용 줄번호는 기준 HEAD `f642fd1` 기준.

### A. c2_check (`openbinggu_staging_write_selftest.py`)

- **A4** — `pack.evidence[0].source_missing=True`(actor=human·evidence_refs 정상) → `c2_check` 반환 `"freshness_source_missing"` (L175: `if ev.get("source_missing"): return "freshness_source_missing"` — hash/redaction 검사보다 먼저).
- **A6** — `pack.evidence[0].redaction_policy="v0"`(source_missing=False·source_hash==captured_hash) → 반환 `"freshness_redaction_policy_changed"` (L177: `CUR_REDACTION_POLICY="v1"` 불일치).
- **A10** — 한 pack 에 actor=auto + evidence_refs=[] + source_missing=True + duplicate + backup_fail 모두 동시 위반 시 → 반환은 **`"G4_no_auto"`**(우선순위: actor L169 → evidence_refs L171~172 → freshness(source_missing L175 → hash L176 → redaction L177) → duplicate L180 → backup L183 순서로 첫 매칭만 반환). 단계별 케이스: actor 제거 시 `evidence_refs_missing`, 그다음 `freshness_source_missing`, … 순으로 노출되는지 순차 pin.

### B. staging_apply (`openbinggu_staging_write_selftest.py`) — **고위험: actual write 본체**

- **B9** — 정상 pack·actor=human → 반환 `{applied:True, ..., snapshot:<path>}` 이고 **`os.path.exists(r["snapshot"])` True** (L196 `db.snapshot(...)` 생성, L227 반환에 `"snapshot": snap` 포함). snapshot 파일명은 `snap_<hash(before)>` 형식.
- **B10** — c2_check reason 존재(예: backup_fail=True) BLOCK 경로 → 반환 `{applied:False, reason, button:"disabled"}` 이고 **audit_log 마지막 행 before_hash == after_hash**(L192 `audit_append(..., before, before, ...)` — BLOCK 시 before/after 동일 store_checksum). 노드 count 불변(write 0).

### C. tombstone (`openbinggu_staging_write_selftest.py`) — **고위험: state write**

- **C2** — DB 에 없는 `node_id`("nope")로 `tombstone` 호출 → 반환 `{state:None, physical_present:False}` (L238 `SELECT state` → row None → `state:None`; L239 `SELECT 1` → None → `physical_present:bool(None)=False`). UPDATE 는 0 rows 영향(에러 0).
- **C3** — 존재 노드 tombstone 호출 → write_lock 진입·`snapshot` 파일(`snap_t_<hash>`) 생성·**audit_log 에 action="tombstone" result="ALLOW" 행 1개 추가**(L233 snapshot, L237 audit_append ALLOW), 반환 `{state:"tombstoned", physical_present:True}`.

### D. StagingDB 인프라 (`openbinggu_staging_write_selftest.py`)

- **D3** — 같은 프로세스(동일 pid)에서 `write_lock()` 진입 중 lock 파일에 자기 pid 가 적혀 있으면 재진입 시 **RuntimeError 없이 통과**(L107 `if pid != str(os.getpid())` → 같으면 raise 안 함). pin: 동일 pid pre-write lock 존재 상태에서 staging_apply 가 에러 없이 진행.
- **D9** — `db.snapshot(snap_dir, name)` 호출 → `PRAGMA wal_checkpoint(TRUNCATE)` 실행 후 main 파일 `shutil.copy2` → **반환 경로 파일 존재**(`os.path.exists(snap)` True, L84~88). pin: snapshot 반환값이 실재 파일.
- **D10** — 동일 nodes/edges/evidence 내용 두 DB → `store_checksum()` 동일 값(결정성). 내용 1개 변경 시 값 변경. (L115~120: 세 테이블 `ORDER BY 1` 정렬 후 sha256[:16] — 삽입 순서 무관·결정적).

### E. save_selected (`openbinggu_conversation_candidate_save.py`) — **고위험: G4① write core**

- **E3** — actor=human·confirm 일치하되 `indices=[]` → `block("empty_selection")` 반환 `{applied:False, reason:"empty_selection", ...}` (L70~73: confirm 검사 통과 후 `if not indices` 가 empty_selection BLOCK). 단, confirm 기댓값은 `"SAVE "`(빈 인덱스이므로 빈 리스트 join) — confirm 도 `"SAVE "` 정확형이어야 empty_selection 도달.
- **E6** — A0 판정이 REVIEW 인 후보 선택 + `ctx.allow_review` 미설정 → 해당 인덱스 `rejected["a0_review_needs_explicit_allow"]` 증가, 저장 0(L101~103). 다른 인덱스 없으면 최종 `nothing_to_save`.
- **E7** — 선택 문장에 PII/secret/bizno 패턴 hit → 해당 인덱스 `rejected["pii_or_secret"]` 증가, 저장 0(L105~107: `scan_residual_pii` + `_PREVIEW_PII_EXTRA` + `SECRET_PATTERNS` 재스캔). 재실행 경로(capture_preview 내부 재호출)에서도 동일 차단.

### H~K. deprecate_g3 write 가드 (`openbinggu_deprecate_and_remind_g3.py`) — **B 담당**

- **H4**(B) — 존재하되 `state="tombstoned"` 노드 deprecate → `block("tombstoned_item")`(L96~97). actor=human·reason 정상·미존재 아님 전제.
- **H5**(B) — `kind` 가 `"node"`/`"edge"` 아님(예 `"x"`) → `block("kind_invalid")`(L89~90). **주의(현행 코드 순서):** L88 `table,col` 은 `kind=="node"` else edges 로 먼저 배정되나, L89 의 `if kind not in ("node","edge")` 가 row 조회(L91) **이전**에 return 하므로 invalid kind 는 edges 조회 도달 전 `kind_invalid` 로 차단됨. pin: kind="x" → 반드시 `"kind_invalid"`(item_not_found 아님).
- **J3**(B) — pending review 존재·outcome∈OUTCOMES 이되 `reason="  "`(공백) → `block("resolve_reason_required")`(L171~172). actor=human 전제.
- **K4**(B) — klass∈HARVEST_CLASSES·(discard 아니거나 discard+reason 있음)이되 `item_id="  "`(공백) → `block("item_id_required")`(L242~243). actor=human 전제. **순서 주의:** klass 검사(L238)·discard reason 검사(L240) 통과 후 item_id 검사이므로, 케이스는 klass="keep"+item_id 공백으로 구성해야 item_id_required 도달.

### L~O. save_gate (`binggu_save_gate.py`) — **B 담당**

- **L2**(B) — `gate_record(["  ", "", "정상문장"], path=tmp)` → 빈/공백 문장은 `_norm` 빈 → skip(L130~131), 반환 건수 = 1(정상문장만 기록). 파일에 빈문장 행 0.
- **N2**(B) — `write_last_preview([{"sentence":"  "},{"sentence":"가나다"}], path=tmp)` → 빈 sentence 후보 제외, rows 1개·반환 1(L180~181 `if _norm(c.get("sentence",""))`). idx 는 enumerate 기준(빈문장도 j 소비) — pin: 남는 row 의 idx 값 현행 그대로.
- **O3**(B) — `gate_record_from_prompt("SAVE 1", preview_path=<없는파일>)` → preview 파일 부재 시 0 반환(L196~197). 파일 존재하나 JSON 파싱 실패 시도 0(L198~202 except → 0).
- **O4**(B) — `gate_record_from_prompt("SAVE 9", preview_path=<idx 1만 있는 preview>)` → idx 9 매칭 0 → `hashes=[]` → 0 반환(L203~205). gate-log write 0.

---

## 3. 관찰 가능 vs 관찰 불가능 분리

테스트(외부 호출 + 반환값/DB 상태/파일 단언)로 **관찰·주입 가능**한가 기준.

### 3-1. 관찰 가능(테스트로 직접 pin 가능) — 19건

A4 · A6 · A10 · B9 · B10 · C2 · C3 · D9 · D10 · E3 · E6 · E7 · H4 · H5 · J3 · K4 · L2 · N2 · O3 · O4
(= O3 를 2 케이스로 보면 실질 전부; 아래 부분 관찰 2건 제외 시 순수 관찰 가능 19건).

**관찰 수단:**
- 반환 dict 의 `reason`/`applied`/`state`/`physical_present`/건수 직접 단언(A4·A6·A10·B10·C2·E3·E6·E7·H4·H5·J3·K4·L2·N2·O3·O4).
- 파일시스템 단언: `os.path.exists(snapshot)`(B9·C3·D9).
- DB 상태 단언: audit_log 행/노드 count/store_checksum 동일성(B10·C3·D10).

### 3-2. 관찰 어려움(주입·격리 난점) — 2건

- **D3**(같은 pid write_lock 재진입 허용) — *부분 관찰*. `write_lock()` 자체는 contextmanager 라 직접 재진입 테스트 가능하나, **"같은 pid 재진입이 staging_apply 실 경로에서 발생하는 시나리오"** 는 자연 발생 0(staging_apply 는 단일 write_lock 블록만 사용). 사유: 현행 코드에 같은 pid 중첩 write_lock 호출 경로가 없음 → 인위적으로 lock 파일에 자기 pid 를 미리 써두는 합성 주입으로만 관찰. **관찰 가능하나 주입 합성 필요**.
- **A10 전체 순서**(판정 우선순위 5단 전수) — *부분 관찰*. 첫 매칭만 반환하므로 "동시 다중 위반 시 순서" 는 **단계적 제거**(위반 조건을 하나씩 풀며 다음 reason 노출 확인)로만 간접 증명 가능. 단일 호출 1회로는 한 reason 만 관찰 → 5회 케이스 누적 필요. **관찰 가능하나 단일 단언 불가(누적 케이스)**.

### 3-3. 관찰 불가능(테스트 부적합) — 0건

본 21건 GAP 중 **테스트로 전혀 관찰 불가능한 항목은 없음**. 전부 (a) 반환값 또는 (b) 파일/DB 부작용으로 확인 가능. D3·A10 만 "주입 합성/누적 케이스" 의 절차적 난점이 있을 뿐 관찰 자체는 가능.

> **관찰 분류 요약:** 순수 관찰 가능 **19** / 부분 관찰(주입·누적 필요) **2**(D3·A10) / 완전 관찰 불가 **0**.

---

## 4. 최종 판정

**S4 implementation HOLD 유지.** 본 문서는 잔여 GAP 21건의 위험도·함수군 재분류 + 현재 동작 pin 케이스 설계 + 관찰 가능성 분리만 명문화한 **design-only** 산출물이다.
구현 0 · 코드 이동 0 · 테스트 코드 0 · 대상 코드 touch 0.

- **actual write core**(`staging_apply` / `save_selected` / `commit_selected`)는 **S4-6 마지막 / 영구 HOLD 후보** — 본 plan 의 고위험 8건(B9·B10·C2·C3·D9·E3·E6·E7)이 GREEN 이어도 write core 이동 승인이 아니다.
- 본 plan 의 케이스는 **tests-only characterization**(production/gate-critical touch 0)으로만 추가될 수 있으며, owner approval 토큰 전까지 구현 진입 0.
- **다음 단계(승인 전제):** 본 설계의 21건을 tests-only characterization 으로 추가 → 전종 GREEN → owner 토큰 요청(§ S4 진입 게이트).
