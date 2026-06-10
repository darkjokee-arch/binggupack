> OpenBinggu is the legacy/internal codename for BingguPack.

# OPENBINGGU PACK CONTRACT (v0.10)

> 상태: **GO** (selftest 8/8 일치) · production write **0** · **BLOCKED_BY_V09 유지**
> 생성: 2026-06-03 · scope: project:openbinggu · 선행: v0.9 production write policy

## 1. 목적 (칸 확정 아님)

Pack contract 는 **ontology "칸" 확정이 아니라 "안전한 컨테이너 계약"**이다.
이 단계에서 ontology 자체를 키우지 않는다. session / evidence / candidate pack 이
나중에 **안전하게 쌓일 수 있도록 최소 계약과 validator 만** 만든다.

R2 4-CLI debate 구멍 ②("칸 선확정 부채") 회피 원칙: 경계를 지금 확정하지 않고,
pack 이 지켜야 할 최소 안전 필드만 코드로 고정한다. 주제는 기존 9도메인(D1~D9)에
강제 배정하지 않고 `scope` + `cross_pack_tags` 로만 표현한다.

## 2. 산출물

| 파일 | 역할 |
|---|---|
| `docs/OPENBINGGU_PACK_CONTRACT.md` | 본 설계 문서 |
| `schemas/openbinggu_pack_contract.schema.json` | JSON Schema(draft-07) — 계약 형식 정의 |
| `scripts/openbinggu_pack_validate.py` | validator(자체 구현, 외부 의존 0) — dry-run only |
| `tests/fixtures/openbinggu_pack_contract/*.json` | synthetic fixture 8개(GO 조건 대응) |
| `reports/openbinggu_pack_contract_selftest.json` | selftest 결과(유일한 write) |

## 3. 최소 pack contract 필드

```yaml
required_fields:
  - pack_id            # 고유 식별자
  - pack_type          # 아래 enum
  - scope              # 주제 범위(9도메인 강제배정 X)
  - depends_on         # 선행 pack_id 목록(형식만 확인)
  - evidence_policy    # {source, min_evidence}
  - merge_policy       # {mode, target, cross_pack}
  - promotion_allowed_default   # 반드시 false
  - status             # 아래 enum
  - cross_pack_tags    # cross-pack 연결 태그
  - risk_level         # low|medium|high|unknown
  - created_from       # provenance(박제/handoff/세션)

hard_defaults:         # 존재 시 반드시 false, true 면 STOP
  promotion_allowed_default: false
  production_write_allowed:   false
  opencrab_ingest_allowed:    false
  github_publish_allowed:     false

pack_type_allowed:  [seed, session, evidence, candidate, review, audit, runtime, synthetic_fixture]
status_allowed:     [draft, staged, validated, review_required, archived, rejected]
risk_level_allowed: [low, medium, high, unknown]
merge_policy.mode:       [manual, auto, review]
merge_policy.target:     [staging, candidate, review, production]
merge_policy.cross_pack: [isolated, review_only, fuzzy]
```

## 4. validator 초기 범위 (나쁜 pack 만 막는 최소 gate)

verdict = **PASS / REVIEW_ONLY / STOP**

| # | 규칙 | 결과 |
|---|---|---|
| 1 | required field 누락 | STOP |
| 2 | `promotion_allowed_default` 가 false 아님 | STOP |
| – | hard-default 플래그(production_write/opencrab_ingest/github_publish) true | STOP |
| 3 | `risk_level` ∈ {high, unknown} 이고 `merge_policy.mode=auto` | STOP (자동 merge 금지) |
| 4 | `pack_type` ∈ {session, candidate, evidence} 이고 `merge_policy.target=production` | STOP |
| 5 | `depends_on` 은 **존재(형식)만 확인**, 의미론적 merge 안 함 | note |
| 6 | `cross_pack_tags` 있고 `merge_policy.cross_pack=fuzzy` | REVIEW_ONLY |
| 7 | `forced_domain` 등 9도메인 강제배정 표현 | STOP |
| 8 | enum(pack_type/status/risk_level/merge_*) 비허용값 | STOP |
| – | validation report 는 생성하되 **production graph 는 생성 X** | — |

우선순위: STOP 이 하나라도 있으면 STOP → 없고 REVIEW 있으면 REVIEW_ONLY → 둘 다 없으면 PASS.
PASS 라도 실제 production write 는 v0.9 정책(BLOCKED_BY_V09)으로 별도 차단된다.

## 5. v0.10 GO 조건 (selftest)

| fixture | 기대 | 실측 |
|---|---|---|
| pass_valid_minimal | PASS | PASS ✓ |
| pass_synthetic_fixture | PASS | PASS ✓ |
| stop_missing_required | STOP | STOP ✓ |
| stop_promotion_true | STOP | STOP ✓ |
| stop_high_risk_automerge | STOP | STOP ✓ |
| stop_unknown_risk_automerge | STOP | STOP ✓ |
| stop_production_target | STOP | STOP ✓ |
| review_cross_pack_fuzzy | REVIEW_ONLY | REVIEW_ONLY ✓ |

**GATE: GO** (8/8 일치, mismatch 0).

실행:
```bash
python scripts/openbinggu_pack_validate.py --selftest   # 전수 + report
python scripts/openbinggu_pack_validate.py <pack.json>   # 단일 dry-run
```

## 6. 금지 (BLOCKED_BY_V09 불변)

production write / 운영 store write(`_graph_merge.yaml`·`user_graph.yaml`·`localcrab_index.sqlite`) /
`localbinggu_production_graph.yaml` 생성 / OpenCrab 호출 / GitHub push·repo 생성 /
`reingest_pack_draft` 원본 수정 / scheduler 수정 / `promotion_allowed` 변경 / D9 상태 변경 /
coverage·pattern 승격 — **전부 금지**. validator 의 유일한 write 는 selftest report JSON.

## 7. 다음 (별도 GO)

- v0.11 후보: incoming → staging loader 가 본 contract 를 통과한 pack 만 수용(여전히 dry-run).
- 본평가는 EVALUATION_PROTOCOL 마일스톤(session pack 10 / evidence 100 / CLI 20회 / 고위험 5) 도달 후.
