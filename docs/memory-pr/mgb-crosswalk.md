# Memory PR — MGB Crosswalk — v0.1-draft

> Spec(필드·산식 정적 계약) 과 MGB(Memory Governance Benchmark · 런타임 행위 검증) 의 대응. **중복 구현이 아니다** — Spec 은 새 검증 코드를 만들지 않고 기존 MGB/selftest 를 인용한다.

## 1. 책임 분리

| | 역할 | 산출 |
| :-- | :-- | :-- |
| **MGB** | 런타임 행위 검증 | PASS / FAIL / UNSUPPORTED / NOT_RUN |
| **Spec** (이 문서군) | 정적 필드·산식·불변식 정의 | 재현용 계약 + 고정 KAT |

두 자산은 상호 참조한다. Spec 의 불변식이 실제로 지켜지는지는 MGB 가 런타임에 검증하고, MGB 가 무엇을 검증하는지의 필드 정의는 Spec 이 제공한다.

## 2. 직접 검증 범위 (과장 금지)

- **Interactive Save 공개 CLI 경로 + MGB-09** = MGB 가 **런타임 직접 검증**.
- **Trusted approve · Hosted 수렴** = MGB 의 공개 CLI(black-box) 프로필이 커버하지 못한다 → crosswalk 에서 **"정적 문서 계약 only"** 로 표기.

## 3. 매핑표

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

## 4. UNSUPPORTED ≠ optional

- MGB 의 `UNSUPPORTED`(MGB-03·MGB-10) 는 Spec 에서도 **UNSUPPORTED** 로 유지한다.
- `UNSUPPORTED` 를 `optional` 로 낮춰 "적합한 것처럼" 보이게 하지 않는다. 문서와 test vector 양쪽에 명시한다. (**PASS 위장 0** 원칙을 MGB 와 공유.)

## 5. check_vectors.py 의 책임

`tools/check_vectors.py` 는 **고정 KAT 비교 + 기존 MGB/selftest 호출**만 담당한다. 상태기계를 재구현하지 않는다. 즉 Spec 의 CI 는 canonicalization digest 순수함수의 drift 만 강제하고, 행위 검증은 MGB 에 위임한다.
