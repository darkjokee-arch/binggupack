# Seed Pack 개념 (기술 문서)

BingguPack의 pack은 용도에 따라 두 계층으로 나뉠 수 있다. 이 문서는 그 기술 개념만 정의한다.

## 두 계층
| | seed pack | user pack |
|---|---|---|
| 내용 | 도메인 골격: 개념 정의·문서·판단 **템플릿** (예: 용어 정의, 빈칸이 있는 판단 규칙 틀) | 사용자 작업에서 생성된 데이터 (현 watcher→승인 파이프라인 산출물) |
| pack_type | `seed` — validator의 기존 허용값 (`PACK_TYPE_ALLOWED`, 변경 불요) | `candidate` 등 기존 허용값 |
| 데이터 | 합성·일반 지식만 — **사용자 데이터 미포함 원칙** | 사용자 비공개 (로컬) |
| 승격 | `promotion_allowed_default=false` 동일 — seed도 후보일 뿐, 수용 여부는 사용자 승인 게이트가 결정 | 동일 (candidate-first) |

## 연결
- user pack의 manifest 기존 필드 `depends_on`에 seed pack id를 기록할 수 있다: `"depends_on": ["seed_bid_domain_v1"]`
- 노드 레벨 연결(예: 사용자 판단이 seed 템플릿 판단을 `refines`)은 다른 모든 엣지와 동일하게 **후보 생성 → 사용자 승인** 경로만 따른다. 자동 병합 없음 (`merge_policy.cross_pack: isolated` 유지).

## 헌법 동일 적용
seed pack도 일반 pack과 같은 규칙을 따른다: 핵심 문장 노드 · evidence 필수 · candidate-first · 승인 게이트 경유. 검증도 동일 (`openbinggu_pack_validate.py` — seed는 기존 허용 타입이므로 그대로 PASS).

## 미정 (구현 전 결정 필요)
- **scope 정책**: consumer는 scope 일치만 소비하는데 seed는 도메인 단위(`domain:*`) scope가 자연스러움 — 허용 목록 확장 / 설치 시 rewrite / 전용 경로 중 미정. 결정 전 구현하지 않는다.
