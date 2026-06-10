# BingguPack Phase 5 — enum 결정 브리프 (license / release_mode / entitlement)

> 목적: OpenCrab Pack v1 finalize를 막아온 enum HOLD를 풀기 위한 선택지 정리. **DESIGN ONLY — finalize/upload/apply 실행 0.**
> 근거: OpenCrab 백엔드 소스 정적 실측(2026-06-08, read-only) + pack v1 spec.

## 0. 실측 핵심 (결정의 전제)

| enum | 소스 실측 결과 |
|---|---|
| license | spec 예시 필드로만 존재 — `license = {scope, name}` **자유 문자열**(허용값 enum 강제 없음, validator 부재) |
| release_mode | 백엔드 소스에 **코드 키 자체가 0건** (과거 목격된 "4모드"는 별도 데스크탑 앱 UI 개념, 소스 미보유) |
| entitlement | 동일하게 **0건** (purchased/subscription enum 부재) |

→ **결론: pack v1 manifest가 실제로 요구하는 것은 license 필드 하나뿐이고, 그것도 자유 문자열.** release_mode/entitlement는 pack v1 finalize의 필수 필드가 아니다.

## 1. 선택지

### license
- **L1 (추천)**: `{scope:"personal", name:"MIT"}` — RC 코드 라이선스(MIT)·개인용 트랙과 정합, spec 예시값 그대로
- L2: `{scope:"personal", name:"CC-BY-4.0"}` — pack 콘텐츠를 코드와 구분하고 싶을 때
- L3: 보류 지속 — finalize 자체가 계속 막힘

### release_mode
- **R1 (추천)**: **pack v1 manifest 비필드로 확정** — 값을 정하지 않고 "BingguPack 배포 옵션은 approval_path(3-path)가 담당, release_mode는 외부 앱 개념"으로 경계 정리. HOLD 해소를 "결정"이 아니라 "비의존 선언"으로
- R2: 자체 enum(draft/private/public) 신설 — 외부 spec에 없는 필드 발명 = 비추천

### entitlement
- **E1 (추천)**: R1과 동일 — 비필드 확정, 유료/구독은 트랙2(DEFER)에서만 재론
- E2: placeholder("none") 기입 — spec에 없는 필드 추가라 비추천

## 2. v0.6 finalize dry-run 최소 확정값 (추천 조합)

**L1 + R1 + E1** — 즉, 실제로 "정해야 하는 값"은 license 하나. 이 조합이면 enum HOLD가 finalize dry-run을 더 이상 막지 않는다.

## 3. v0.6 dry-run 범위 (upload/apply와 분리)

**포함 (전부 로컬 파일 생성, 외부 전송 0)**:
1. pack v1 필수 레이아웃 생성기 — 현재 gap 6종: `neo4j/import.cypher`(파일 생성만, Neo4j 실행 0) · `neo4j/opencrab_ingest.jsonl` · `neo4j/export_status.json`(생성 스텁 + "미실행" 상태 기록) · `quality/report.json` · `sample_queries.json` · `community_reports.json`
2. manifest license=L1 기입 + 레이아웃 완전성 validator (synthetic pack 대상 selftest)
3. publish guard(기존) 통과 확인

**제외 (별도 GO-OC2)**: OpenCrab 실제 upload/apply · Neo4j start/add(export_status 실측은 이때만) · 계정/대상 결정.

## 4. 결정 확정 (2026-06-10 owner 승인)

✅ **L1 + R1 + E1 확정** — license `{"scope":"personal","name":"MIT"}` / release_mode·entitlement는 manifest **비필드**(키 자체 미기입).
구현: `scripts/openbinggu_finalize_dryrun.py` (selftest 10/10 — 레이아웃 11종 생성·L1 검증·비필드 검증·Neo4j NOT_RUN·PII fail-closed·결정적 재생성). upload/apply는 계속 별도 단계(GO-OC2).
