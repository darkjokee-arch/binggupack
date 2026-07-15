# Memory PR — Core Model & Boundaries — v0.1-draft

> 이 문서는 **공통 어휘(Core Model) + 정직한 한계(Security & Limitations) + MGB Crosswalk** 3층을 한 곳에 담는다. 세 원본을 각각 독립 최상위 섹션으로 그대로 이관했다(재작성 0). Security(한계 렌즈) 와 MGB Crosswalk(검증유형 렌즈) 는 같은 사실을 다른 프레이밍으로 서술하므로 병렬 존치한다 — 하나로 합치지 않는다.

## Memory PR Core Model — v0.1-draft

> BingguPack reference implementation 서술 · 표준 아님. 세 프로필이 **같은 바이트 산식을 쓴다고 주장하지 않는다** — Core Model 은 공통 *의미* 만 정의한다.

### 1. 공통 어휘 (semantic vocabulary)

| 어휘 | 의미 |
| :-- | :-- |
| Proposal / Candidate | AI 또는 시스템이 만든 미확정 제안 |
| Reviewable Preview | 사람이 확정 전에 실제로 보는 미리보기 |
| Approval / Human Decision | 사람의 확정 행위 (프로필마다 증명 방식 다름) |
| Mutation Request | 아직 실행되지 않은 변경 의도 |
| Commit / Event Consumption | 검증 후 실제 반영된 결과 |
| Receipt | 소비 결과 영수증 (재현 식별자 포함, 비밀값 제외) |
| Recall / Explain | 저장된 기억의 조회·근거 추적 |
| Supersede / Retire | 물리 삭제 없는 상태 전이(이력 보존) |
| Rejected / Expired / Replayed / Failed | 실패·거부 종결 상태 |

### 2. 공통 불변식 (invariants · 전 프로필)

1. **preview / intent 만으로는 저장 0.**
2. **저장 노드 = `candidate=1, promotion_allowed=0`.** 이 `candidate` 는 "승인 전"이 아니라 "정본(공개/마켓)으로 승격되지 않은 개인 기억"을 뜻한다. 승인·커밋된 `state='active'` 노드도 `candidate=1` 이다. 승인 lifecycle 축(`state`)과 promotion 축(`candidate` 컬럼)은 명칭만 겹치는 별개 개념이다.
3. **fail-closed 기본값 `actor=reader`** — 승인 증명이 없으면 write 하지 않는다.
4. **사람 승인(모드별 상이)만이 write 를 만든다.** AI 는 제안까지만 가능하다(단 그 "위조 불가"의 강도는 프로필/배포에 따라 다르다 — [security-limitations.md](./security-limitations.md) 참조).

### 3. Request 와 Event 는 별개다

Core Model 은 **공통 선형 상태기계를 강제하지 않는다.** 아래는 의미상 흐름이며, 실제 persisted/derived 상태는 각 프로필 문서의 상태표를 따른다.

```
requested → reviewed → approved → consuming → committed / consumed
                                   ↘ rejected · revoked · expired · replayed · failed
```

- **Request** (미실행 의도): preview 후보 · save intent · approval request · replace/resolve request.
- **Event** (검증·소비된 결과): committed memory · approval consumed · superseded · retired · resolved · receipt.
- ⚠️ **approval 만으로 write 완료를 표현하지 않는다.** 예: Trusted Event 는 approve 이벤트(승인) 와 consumption(소비 완료) 가 별개 축이다 — approve 되었어도 `reserve → finalize` 소비가 끝나야 반영이다.

### 4. 각 프로필이 독립적으로 정의하는 것

각 프로필 문서는 다음을 자기 값으로 명시한다(공유 가정 금지):

신뢰 주체 · 입력 객체 · 승인 증명 · canonicalization · digest · freshness · replay 방지 · commit/consumption 결과 · 공개적으로 검증 가능한 범위 · 현재 지원되지 않는 범위(UNSUPPORTED).

### 5. canonicalization 은 프로필별 필수 계약

외부 클라이언트가 BingguPack 과 실제로 연동/재현하려면 해당 프로필의 canonical bytes 와 digest 를 재현할 수 있어야 한다. 따라서 canonicalization 은 각주가 아니라 **필수 규범**이며, 각 프로필이 산식을 완전 공개한다.

세 프로필 산식은 실제로 다르다(요약, 정확한 값은 각 프로필 문서):

| 항목 | Interactive Save | Trusted Event | Hosted Relay |
| :-- | :-- | :-- | :-- |
| 정규화 | 공백축소+strip (NFC/bidi **없음**) | NFC + bidi/Cc/Cf 거부 | (intent 원문 · commit 은 Interactive 수렴) |
| digest 입력 | `_norm(sentence)` | `tae-1 \x1f op \x1f json(sort_keys,compact,NFC)` | `text\|indices\|confirm` |
| 절단폭 | preview_ref[:16]·node_id[:8]·preview_id[:8] | payload_digest **전체 64**·request_id[:24]·nonce 32hex | intent_id[:16]·bundle_id[:16] |
| hex | 소문자 | 소문자 | 소문자 |

### 6. profile ID = 문서 네임스페이스 (wire version 아님)

- 문서 라벨: `doc-profile: interactive-save` / `trusted-event` / `hosted-relay`.
- ⚠️ **이 라벨은 절대 해시 재료에 넣지 않는다.** 재현자는 실제 코드의 재료 문자열을 그대로 사용해야 한다.
- wire version 매핑: Trusted digest = `tae-1`(PROTOCOL_VERSION) · Hosted intent = `schema_ver=1` · Interactive = 버전 태그 없음(순수 함수).

### 7. Recall / Explain 는 logical model

BingguPack 의 정본은 로컬 CLI 와 로컬 장부다. Recall/Explain 문서는 "이상적 JSON 응답"을 현재 공개 CLI 의 실제 응답처럼 표현하지 않는다. 세 층을 구분한다:

| 구분 | 의미 |
| :-- | :-- |
| Logical model | recall/explain 이 의미상 제공해야 하는 정보 |
| Current CLI surface | 실제 명령으로 볼 수 있는 출력 (surface 별 상이) |
| Internal representation | SQLite/Python 내부 · 외부 계약 아님 |

필드별 지위표는 [interactive-save-profile](./interactive-save-profile-v0.1-draft.md) §Recall 및 각 프로필 문서 참조. **Logical model ≠ public response schema.**

## Memory PR — Security & Limitations — v0.1-draft

> 정직한 한계 명세. 이 문서는 BingguPack 이 **보장하지 않는 것**을 분명히 한다.

### 1. 위협 모델 경계

- 이 통제(모델은 제안만·사람만 확정)는 **"모델 tool surface 가 approval store 에 write 하지 못한다"** 가 성립하는 배포에서만 하드하다 — 웹/앱 커넥터, 잠긴 에이전트 등.
- **Filesystem / Bash MCP 를 동반한 호스트는 위협 모델 밖이다.** same-host attacker 는 로컬 파일(ledger.sqlite·approvals.jsonl·save_gate_log.jsonl)을 직접 조작할 수 있다.

### 2. "AI 위조 불가" 의 범위 (과장 금지)

Interactive Save 의 승인은 3모드다:

| 모드 | 위조 난이도 |
| :-- | :-- |
| `save_gate_ref` | AI 는 UserPromptSubmit hook 을 발화할 수 없어 이 모드로 승격 불가 — **이 모드 한정 위조 불가** |
| `cli_command` | CLAUDECODE 없는 직접 터미널 입력 → **hook 없이 human 판정**(isatty 무관). 셸 접근 주체를 human 으로 가정 |
| `denied` | `CLAUDECODE` truthy → reader(거부 전용) |

- ⚠️ `CLAUDECODE` 는 **제거 가능한 소프트 신호**(환경변수)지 암호 증명이 아니다.
- 따라서 **"AI 위조 불가" 는 `save_gate_ref` + `CLAUDECODE` 세션 조합에서만 성립**한다. 전역 주장이 아니다.

### 3. tamper-proof 아님

- `trusted_approval` 은 서명/HMAC 을 도입하지 않았다. **같은 머신의 키 = 보안 연극**이라는 판단(정직).
- 변조 탐지(`binding_mismatch:payload/operation/ledger/protocol`)는 **소비 시도의 부수효과로만** 발생한다. 임의 payload/nonce/receipt 를 받아 독립 검증하는 **standalone tamper-verify CLI 는 없다**(→ MGB-10 UNSUPPORTED).

### 4. Hosted 본문 평문 잔존 (중요)

Hosted Relay 경로에서 본문 평문은 **세 곳**에 존재한다:

1. hosted DO storage 체류 (drain 시 delete).
2. 로컬 staging 원문.
3. **commit 후 원문 전체가 `_archive/<intent_id>.processed.json` 으로 이동·영구 보존** (hosted 계약 15 · 삭제가 아니라 이동 · 별도 owner purge 만 삭제).

- ledger(nodes) 는 전문을 저장하지 않지만(발췌 + 해시), **로컬 평문은 영구 잔존**한다.
- **본문 암호화 없음** — 전송 TLS + 인출 HMAC 무결성만. 체류 중 평문. 보완책은 PII 를 preview 단계에서 제외 + 짧은 TTL + pull 후 inbox 잔존 0 뿐이다.

### 5. approve 이벤트 자동생성은 UNSUPPORTED

- Trusted approve 발행은 **대화형 TTY 전용**(비대화형 exit 2 · Unix PTY 테스트만 · Windows PTY 미지원으로 skip · test_double 채널은 배포 wheel 에서 제거).
- 따라서 **공개 CLI / CI 로 approve 이벤트를 스크립트로 만들 수 없다.** Trusted Event 소비 vector 는 `illustrative-only` 로만 제공한다.

### 6. freshness / 만료

- Interactive `GATE_WINDOW` 기본 1시간 · Trusted `DEFAULT_TTL` 900초.
- 만료(stale) 관찰은 **wall-clock 실대기**가 필요하고 `--now` 주입 CLI 플래그가 없어, 공개 CLI 로 결정적 재현이 비현실적이다(→ MGB-03 UNSUPPORTED).

### 7. 요약: UNSUPPORTED 목록

approve 이벤트 자동생성 · MGB-03(stale approval 결정적 재현) · MGB-10(public tamper verification) · Hosted 본문 암호화 · preview_ref 의 CLI 출력 · 실 worker HMAC 왕복/DO drain.

## Memory PR — MGB Crosswalk — v0.1-draft

> Spec(필드·산식 정적 계약) 과 MGB(Memory Governance Benchmark · 런타임 행위 검증) 의 대응. **중복 구현이 아니다** — Spec 은 새 검증 코드를 만들지 않고 기존 MGB/selftest 를 인용한다.

### 1. 책임 분리

| | 역할 | 산출 |
| :-- | :-- | :-- |
| **MGB** | 런타임 행위 검증 | PASS / FAIL / UNSUPPORTED / NOT_RUN |
| **Spec** (이 문서군) | 정적 필드·산식·불변식 정의 | 재현용 계약 + 고정 KAT |

두 자산은 상호 참조한다. Spec 의 불변식이 실제로 지켜지는지는 MGB 가 런타임에 검증하고, MGB 가 무엇을 검증하는지의 필드 정의는 Spec 이 제공한다.

### 2. 직접 검증 범위 (과장 금지)

- **Interactive Save 공개 CLI 경로 + MGB-09** = MGB 가 **런타임 직접 검증**.
- **Trusted approve · Hosted 수렴** = MGB 의 공개 CLI(black-box) 프로필이 커버하지 못한다 → crosswalk 에서 **"정적 문서 계약 only"** 로 표기.

### 3. 매핑표

| Spec 불변식 / 필드 | MGB 시나리오 | 검증 유형 |
| :-- | :-- | :-- |
| preview/intent 만으로 저장 0 · candidate=1 | MGB-09 (등 공개 CLI 저장 게이트) | **직접 (런타임)** |
| Interactive preview_ref 바인딩 | 고정 KAT (`vectors/kat/`) | 직접 (순수함수) |
| Trusted payload_digest / request_id 산식 | 고정 KAT | 직접 (순수함수) |
| Hosted intent_id 산식 | 고정 KAT | 직접 (순수함수) |
| Trusted approve 이벤트 소비·replay | (approve TTY 전용) | 정적 문서 계약 only |
| approve stale 만료 | MGB-03 | **UNSUPPORTED** |
| public tamper verification | MGB-10 | **UNSUPPORTED** |
| Hosted 최종 저장 = commit_bundle 수렴 | (로컬 selftest 인용) | 정적 문서 계약 + selftest |

### 4. UNSUPPORTED ≠ optional

- MGB 의 `UNSUPPORTED`(MGB-03·MGB-10) 는 Spec 에서도 **UNSUPPORTED** 로 유지한다.
- `UNSUPPORTED` 를 `optional` 로 낮춰 "적합한 것처럼" 보이게 하지 않는다. 문서와 test vector 양쪽에 명시한다. (**PASS 위장 0** 원칙을 MGB 와 공유.)

### 5. check_vectors.py 의 책임

`tools/check_vectors.py` 는 **고정 KAT 비교 + 기존 MGB/selftest 호출**만 담당한다. 상태기계를 재구현하지 않는다. 즉 Spec 의 CI 는 canonicalization digest 순수함수의 drift 만 강제하고, 행위 검증은 MGB 에 위임한다.
