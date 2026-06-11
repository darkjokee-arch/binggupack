# Real staging 1-pack 실적용 결과 (2026-06-11, owner GO)

> staging 한정 (`tmp/real_staging/openbinggu_real_staging.sqlite` — 운영과 물리 분리, StagingDB 운영경로 거부). 운영 DB write 0 · confirmed 승격 0 · deploy 0.

## 2단계 실행
**1단계 — 검증 (apply_once, 기존 스크립트 무수정)**: GATE=GO
- 선행 게이트 4종 실측 재확인(doctor·c2_guard 21/21·staging_write 11/11·reviewer 20/20) → personal_apply_allowed ON(GO 문구 해시만 기록) → backup+before_checksum → 마스킹 실 pack apply(9/9/9, pre-apply PII 재스캔 0) → read-back PASS → **rollback 원복 검증(checksum 일치·nodes 0)** → 플래그 OFF → 운영 mtime 불변

**2단계 — 남기는 적용 (persist_apply 신설)**: GATE=GO
- 동일 게이트·플래그·backup 체인 후 apply → DB·스냅샷 보존(rollback 안 함) → 플래그 OFF
- 적용 pack = `workers_port/packs_src/m1_external_demo` (bizno 마스킹 + G1~G6 게이트 통과본)

## 독립 read-back (read-only 별도 확인)
nodes 9 · edges 9 · evidence 9 · audit_log 1(insert ALLOW) · pack_id=batch_m1_external_demo · candidate 위반 0 · promotion 위반 0 · 스냅샷 3개 보존

## 복구 수단
`tmp/real_staging/snapshots/snap_before_persist_e3b0c44298fc1c14.sqlite` → DB 위에 copy 1줄 = 적용 전 원복.

## 의미
실 traj 기반 pack이 **영속 staging DB에 처음으로 적재**됨 — G0~G2-C 파이프라인 산출물이 살아있는 DB 위에서 돌 수 있는 기반. confirmed 승격·운영 반영은 여전히 HOLD.

## 다음 (owner GO 대기)
- real staging 위에서 연필 후보→묶음 승인→볼펜 확정 1사이클 실연 (G2~G2-C를 temp가 아닌 real staging에)
- deprecated 도장·검증 리마인드 연동 / GO-HOSTED-REALPACK-DEPLOY
