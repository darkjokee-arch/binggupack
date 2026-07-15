# Memory PR Core Model — v0.1-draft

> BingguPack reference implementation 서술 · 표준 아님. 세 프로필이 **같은 바이트 산식을 쓴다고 주장하지 않는다** — Core Model 은 공통 *의미* 만 정의한다.

## 1. 공통 어휘 (semantic vocabulary)

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

## 2. 공통 불변식 (invariants · 전 프로필)

1. **preview / intent 만으로는 저장 0.**
2. **저장 노드 = `candidate=1, promotion_allowed=0`.** 이 `candidate` 는 "승인 전"이 아니라 "정본(공개/마켓)으로 승격되지 않은 개인 기억"을 뜻한다. 승인·커밋된 `state='active'` 노드도 `candidate=1` 이다. 승인 lifecycle 축(`state`)과 promotion 축(`candidate` 컬럼)은 명칭만 겹치는 별개 개념이다.
3. **fail-closed 기본값 `actor=reader`** — 승인 증명이 없으면 write 하지 않는다.
4. **사람 승인(모드별 상이)만이 write 를 만든다.** AI 는 제안까지만 가능하다(단 그 "위조 불가"의 강도는 프로필/배포에 따라 다르다 — [security-limitations.md](./security-limitations.md) 참조).

## 3. Request 와 Event 는 별개다

Core Model 은 **공통 선형 상태기계를 강제하지 않는다.** 아래는 의미상 흐름이며, 실제 persisted/derived 상태는 각 프로필 문서의 상태표를 따른다.

```
requested → reviewed → approved → consuming → committed / consumed
                                   ↘ rejected · revoked · expired · replayed · failed
```

- **Request** (미실행 의도): preview 후보 · save intent · approval request · replace/resolve request.
- **Event** (검증·소비된 결과): committed memory · approval consumed · superseded · retired · resolved · receipt.
- ⚠️ **approval 만으로 write 완료를 표현하지 않는다.** 예: Trusted Event 는 approve 이벤트(승인) 와 consumption(소비 완료) 가 별개 축이다 — approve 되었어도 `reserve → finalize` 소비가 끝나야 반영이다.

## 4. 각 프로필이 독립적으로 정의하는 것

각 프로필 문서는 다음을 자기 값으로 명시한다(공유 가정 금지):

신뢰 주체 · 입력 객체 · 승인 증명 · canonicalization · digest · freshness · replay 방지 · commit/consumption 결과 · 공개적으로 검증 가능한 범위 · 현재 지원되지 않는 범위(UNSUPPORTED).

## 5. canonicalization 은 프로필별 필수 계약

외부 클라이언트가 BingguPack 과 실제로 연동/재현하려면 해당 프로필의 canonical bytes 와 digest 를 재현할 수 있어야 한다. 따라서 canonicalization 은 각주가 아니라 **필수 규범**이며, 각 프로필이 산식을 완전 공개한다.

세 프로필 산식은 실제로 다르다(요약, 정확한 값은 각 프로필 문서):

| 항목 | Interactive Save | Trusted Event | Hosted Relay |
| :-- | :-- | :-- | :-- |
| 정규화 | 공백축소+strip (NFC/bidi **없음**) | NFC + bidi/Cc/Cf 거부 | (intent 원문 · commit 은 Interactive 수렴) |
| digest 입력 | `_norm(sentence)` | `tae-1 \x1f op \x1f json(sort_keys,compact,NFC)` | `text\|indices\|confirm` |
| 절단폭 | preview_ref[:16]·node_id[:8]·preview_id[:8] | payload_digest **전체 64**·request_id[:24]·nonce 32hex | intent_id[:16]·bundle_id[:16] |
| hex | 소문자 | 소문자 | 소문자 |

## 6. profile ID = 문서 네임스페이스 (wire version 아님)

- 문서 라벨: `doc-profile: interactive-save` / `trusted-event` / `hosted-relay`.
- ⚠️ **이 라벨은 절대 해시 재료에 넣지 않는다.** 재현자는 실제 코드의 재료 문자열을 그대로 사용해야 한다.
- wire version 매핑: Trusted digest = `tae-1`(PROTOCOL_VERSION) · Hosted intent = `schema_ver=1` · Interactive = 버전 태그 없음(순수 함수).

## 7. Recall / Explain 는 logical model

BingguPack 의 정본은 로컬 CLI 와 로컬 장부다. Recall/Explain 문서는 "이상적 JSON 응답"을 현재 공개 CLI 의 실제 응답처럼 표현하지 않는다. 세 층을 구분한다:

| 구분 | 의미 |
| :-- | :-- |
| Logical model | recall/explain 이 의미상 제공해야 하는 정보 |
| Current CLI surface | 실제 명령으로 볼 수 있는 출력 (surface 별 상이) |
| Internal representation | SQLite/Python 내부 · 외부 계약 아님 |

필드별 지위표는 [interactive-save-profile](./interactive-save-profile-v0.1-draft.md) §Recall 및 각 프로필 문서 참조. **Logical model ≠ public response schema.**
