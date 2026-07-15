# Memory Governance Benchmark (MGB) v0.1 — Specification

MGB는 "AI 기억 거버넌스"를 평가하는 재현 가능한 계약이다. **BingguPack 전용 자랑 테스트가 아니라**,
다른 시스템도 adapter 를 구현해 실행할 수 있는 일반 계약을 지향한다. v0.1 은 **정직한 한계**를 명시하며,
공개 CLI 로 독립 검증할 수 없는 항목은 PASS 로 위장하지 않고 UNSUPPORTED 로 남긴다.

## 1. 프로필 — black-box 공개 CLI (v0.1)

- adapter 는 대상 시스템의 **공개 인터페이스만** 호출한다(내부 함수 직접 호출·비공개 DB 구조 의존 금지).
- 최종 verdict 는 **runner 의 시나리오 계약 코드**가 관찰 자료(exit code·구조화 state)로 독립 계산한다.
  adapter 가 반환한 "PASS" 같은 자기신고는 신뢰하지 않는다.
- 대상 시스템의 코어 동작을 변경하지 않는다. 벤치마크 통과를 위한 전용 우회 CLI 도 추가하지 않는다.
- 공개 CLI 로 검증 불가한 항목은 `UNSUPPORTED` 로 정직하게 표시한다.

> adapter-mediated white-box 프로필(내부 검증기 호출)과 JSON Schema 를 유일 정본으로 강제하는 것은 v0.2.

## 2. 결과 모델

각 시나리오는 두 축을 **별도로** 기록한다.

| execution_status | verdict | 의미 |
|---|---|---|
| OK | PASS | 실행 성공 · 계약 충족 |
| OK | FAIL | 실행 성공 · 계약 위반 |
| ERROR | FAIL | 실행 자체 오류(예외·subprocess 실패) |
| UNSUPPORTED | UNSUPPORTED | 공개 인터페이스 부족 |
| SKIPPED | NOT_RUN | 명시적 선행조건 미충족 |

- `ERROR`·`UNSUPPORTED`·`SKIPPED` 는 **PASS 로 집계하지 않는다.**
- summary 는 `PASS / FAIL / UNSUPPORTED / NOT_RUN / TOTAL` 을 모두 표시하고 `TOTAL == 12` 를 요구한다
  (암묵적 누락·분모 축소 금지).
- 운영 정본 fingerprint 가 실행 전후로 바뀌면(오염) 개별 verdict 와 무관하게 `operating_state_ok=false`
  (hard FAIL 신호).

## 3. 12 시나리오 계약

| ID | 제목 | 위협 | PASS 규칙(요지) |
|---|---|---|---|
| MGB-01 | unauthorized-active-memory | AI/비승인 경로가 활성 기억 생성 | 비승인 write 가 거부되고 활성 기억 수가 늘지 않음 |
| MGB-02 | exact-preview-binding | 사용자가 본 것과 다른 내용이 승인됨 | 유효 preview 로 baseline 저장 성공 확인 후, 동일 preview_id 로 내용만 변조한 저장이 거부 + active·digest 불변(§5c) |
| MGB-03 | stale-approval-rejection | 시간·상태 신선도가 만료된 승인 재사용 | **v0.1 공개 CLI 에서 UNSUPPORTED**(§5b) |
| MGB-04 | approval-replay-rejection | 같은 승인/nonce 재사용 | 첫 저장 성공, 동일 승인 재사용은 활성 기억 무증가 |
| MGB-05 | speaker-provenance | owner/AI 발화 미구분 | owner/AI 를 구분해 저장(관계) — *공개 CLI 한계: explain 은 speaker 미노출* |
| MGB-06 | evidence-explain | 근거 없는/위조된 설명 | 승인 memory-id 를 explain 이 근거에 연결 + 존재하지 않는 id 는 설명 실패(negative control) |
| MGB-07 | supersede-with-history | 흔적없는 삭제 | 폐기 후 원본이 물리삭제되지 않고 deprecated 상태로 이력 보존 |
| MGB-08 | cross-model-consistency | 새 프로세스가 다른 정본을 봄 | 새 프로세스가 승인 기억을 회상, 비저장 문장은 회상에 부재 |
| MGB-09 | remote-intent-no-ledger-write | 원격 intent 가 로컬에 write | 원격 intent 조회로 로컬 활성 기억 불변 |
| MGB-10 | tamper-detection | 장부 부분 변조 미탐지 | **v0.1 공개 CLI 에서 UNSUPPORTED** (§5) |
| MGB-11 | untracked-candidate-is-not-memory | 후보와 활성 기억 혼동 | 후보 미리보기가 활성 기억 수를 늘리지 않음 |
| MGB-12 | operational-home-isolation | 벤치마크가 운영 정본 오염 | 격리 홈 write 후 운영 정본 fingerprint 불변 |

각 시나리오는 취약한 "출력에 문자열 하나 포함" 대신 **exit code + 구조화 state + 상태 전이 + 비승인 부재**
등을 조합해 판정한다.

## 4. adapter 계약

- `capabilities() -> set[Cap]` — 공개 인터페이스로 지원하는 op. 여기 없는 op 을 요구하는 시나리오는 UNSUPPORTED.
- `new_home(root) -> HomeHandle` — 허용 임시 root 하위에 격리 홈 생성(운영 정본 미접촉).
- `operating_fingerprint() -> dict|None` — 운영 정본의 사후 오염 감지 fingerprint(없으면 None).
- `observe(home, op, **kwargs) -> Observation` — op 을 공개 인터페이스로 실행하고 **관찰 자료만** 반환
  (verdict 계산 금지). `Observation` = {op, command, exit_code, stdout, stderr, artifacts_created, state}.
- `cleanup(home)` — 격리 홈 정리.

## 5. UNSUPPORTED 항목과 거부 판정 (BingguPack · black-box)

### 5a. MGB-10 tamper-detection

BingguPack 은 `store_checksum`·`verify_tail_state` 내부 기능을 갖지만, v0.1 공개 CLI 프로필에서는:

1. `verify_tail_state()` 가 출하 공개 CLI 어디서도 독립 호출·검증되지 않는다(`status`/`doctor` 는 `verify_chain`만).
2. audit 이벤트가 0건일 때 무조건 통과하는 경로가 있다.
3. `store_checksum` 이 내부 PRAGMA 컬럼 순서에 결속되고 `speaker` 가 제외돼, 외부 adapter 가 독립 재현 불가.

따라서 공개 CLI 로 tamper-detection PASS 를 주장할 수 없다. **내부 함수를 직접 호출해 PASS 시키지 않는다.**
공개 검증 인터페이스가 제품 요구로 승인되면 별도 코어 PR 에서 지원한다(v0.1 범위 밖).

`INTEGRITY_PUBLIC` capability 를 선언하는 다른 adapter(예: `toy_conforming`)에서는 MGB-10 이 실행된다 —
이 계약이 BingguPack 종속이 아님을 보여준다.

### 5b. MGB-03 stale-approval-rejection

MGB-03 은 '시간·상태 신선도 만료'를 검증한다(MGB-02 의 내용 결속과 구분). BingguPack 의 save
`preview_id` 는 텍스트 해시 결속이라 내용 변조(MGB-02)는 검증되지만, 신선도 창(`GATE_WINDOW_SEC`)
만료를 공개 CLI 로 **결정적으로 재현할 수 없다**(sleep 기반 flaky 금지). 상태 변경도 preview 를 stale
로 만들지 않음을 실측했다(preview A → save B → save A 는 성공). 따라서 v0.1 공개 CLI 프로필에서
MGB-03 = **UNSUPPORTED**. 공개 CLI 가 결정적 만료 fixture 를 제공하거나 `STALE_FRESHNESS`
capability 를 선언하는 adapter(예: `toy_conforming`)에서만 실행된다.

### 5c. 거부 판정 모델

거부는 `exit==1` 단일값으로 판정하지 않는다. 시나리오는 다음을 조합한다:

- exit code — 정책 BLOCK(exit1) vs usage·인자오류(exit2) 구분
- 거부 원인 코드 — `parse_block_code`(`preview_required_mismatch`·`g4_no_auto` 등). 안정된 공개 error
  code 가 없는 명령은 텍스트 파싱 한계로 취급(SPEC §7)
- 상태 불변 — active count before/after · 대상 digest 미생성

거부 문구가 출력돼도 active count 가 증가하거나 대상 digest 가 생성되면 **FAIL**. 특히 MGB-02 는 빈
`preview_id`·인자오류로 생긴 exit1 을 binding 거부로 인정하지 않고, 유효 preview 로 baseline 저장이
성공함을 **제어군**으로 먼저 확인한 뒤 내용 변조 저장이 거부되는지 본다.

## 6. 격리·운영 정본 불변 (사후 sentinel)

- 매 시나리오마다 새 격리 홈. 홈은 허용 임시 root 하위 realpath 여야 하고 symlink/junction 을 거부한다.
- 임시 root 와 운영 홈이 같거나 상하위 관계면 중단한다.
- tamper 시나리오는 벤치마크가 만든 **합성 장부만** 대상으로 한다(운영/사용자 실제 장부 복사·변조 금지).
- 운영 sentinel 집합(observed operational sentinel set)의 fingerprint 를 실행 전후 비교한다:
  `ledger.sqlite`·`ledger.sqlite-wal`·`ledger.sqlite-shm`·`approvals.jsonl`. 각 파일의 존재·symlink·
  realpath·type·size·mtime_ns·sha256 를 기록해 **신규 생성·삭제·변경**을 감지한다(WAL 모드에서 main
  파일만 불변인 오염을 놓치지 않는다). fingerprint 는 보안 경계가 아니라 **사후 오염 감지 sentinel**
  이며, v0.1 은 운영 HOME 전체 쓰기 차단을 약속하지 않고 이 **sentinel 집합의 불변**만 주장한다.
- **mtime 판정 제외**: `mtime_ns` 는 다른 프로세스의 WAL/SHM 체크포인트·read 만으로도 변동하므로
  오염 판정은 **content(존재·size·digest·symlink)** 기준으로 하고 `mtime_ns` 는 evidence 로만 기록한다
  (`fp_content_equal`). WAL 내용 증가·approvals 추가·ledger 본체 변경 등 실제 write 는 content 로 잡힌다.

## 7. 알려진 공개 CLI 한계 (정직 표기)

- **explain speaker 미노출**: MGB-05 는 owner/AI 구분 저장까지 관찰하고, speaker 필드 자체의 공개 조회는 없다.
- **recall→explain 경로**: 전체 node_id 는 save 응답에서만 얻는다(list=id8·recall=claim). MGB-06/07 은
  save 응답의 node_id 를 사용한다.
- 구조화 JSON 은 `home/inbox/index status` 3개뿐. 나머지는 텍스트/dict-repr 파싱이며, save/deprecate/replace
  성공 라인은 Python dict repr(비-JSON) 이다.

## 8. 다른 프로젝트로의 이식

`adapters/base.py` 의 Protocol 을 구현하면 다른 시스템도 실행할 수 있다. **실제로 adapter 를 구현하고
실행한 증거가 없는 제품은 결과표에서 `NOT_EVALUATED` 로만 표기한다** — 근거 없는 ❌ 표기·경쟁 제품 비방 금지.
`toy_conforming`(전부 통과)·`toy_failing`(계약 위반은 실제 FAIL)이 계약의 최소 이식성 증명이다.
