# Real staging 1사이클 실연 결과 — 연필→묶음 승인→볼펜 확정 (2026-06-11, owner GO)

> 조건 8개 고정 전부 이행. GATE=GO (13/13, 첫 실행). 운영 write 0 · confirmed 0 · deploy 0 · 자동 관계추론 0.

## 실행 기록 (조건 → 실측)
| owner 조건 | 실측 |
|---|---|
| ① 스냅샷 선확보 | `snap_cycle_before_83fe97a4cd2593d1.sqlite` (persist 직후 상태) |
| ② 최소 1사이클 | batch 1건(연필 9) · 볼펜 확정 1건 · 기각 1건 |
| ③ 승인 후 candidate 유지 | edge_proposals 9건 전수 candidate=1·promotion=0 위반 0 |
| ④ 6종 매트릭스 통과 | `refines` 감마(40b7ec64)→베타(f2bc46b2), 판단→판단 허용 매트릭스 PASS + evidence 승계 |
| ⑤ confirmed 0 | nodes·edges·proposals 전 테이블 위반 0 |
| ⑥ rejected + 중복 차단 | PII 쌍 기각(사유 기록) / 동일 proposal 재확정 → already_finalized / 기각건 확정 시도 → proposal_rejected |
| ⑦ read-back 수량 | nodes 9 · edges 10(EvidenceSupports 9 + refines 1) · evidence 9 · proposals 9(확정1/기각1/대기7) · audit 6 · chain INTACT |
| ⑧ rollback 사전 문서화 | `BINGGUPACK_REAL_STAGING_CYCLE_ROLLBACK_PROCEDURE.md` (실행 전 작성) |

## 변경
- `scripts/openbinggu_real_staging_cycle_once.py` 신설 (G2·G2-B·G2-C 함수 재사용)
- `scripts/openbinggu_proposal_to_verb_edge_g2c.py` 1함수 보완(백업 `.bak_cycle_20260611`): 구형 노드(G0 이전, node_type=Claim) → view 계층에서 G0 분류기 재분류 fallback (DB 무수정·deterministic). selftest 12/12 회귀 GO.

## 의미
**빙구팩 전 파이프라인이 영속 DB 위에서 처음으로 완주**: 실 pack 적재(9노드) → 연필 후보 자동 9건 → 묶음 1클릭 승인 → 사람이 동사 선택해 볼펜 확정(refines) → 기각·중복 차단 — 헌법(candidate-first·증거 필수·승인 게이트) 전 구간 유지.

## 다음 (owner 결정 대기)
- 2번: deprecated(기각 도장) staging 연동 + 검증 리마인드
- 3번: GO-HOSTED-REALPACK-DEPLOY — HOLD (staging 실사이클 ✅ + rollback 실증 ✅ + commit/push 정리 후 별도 GO)
- (선택) 사이클 산출 신규 스크립트들 RC 트리 동기화·commit
