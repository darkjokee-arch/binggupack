# G2-C — 승인 proposal → 동사 6종 볼펜 확정 주입 결과 (2026-06-11, GO)

> temp/staging 한정. 운영 store write 0 · confirmed 승격 0 · deploy 0 · 자동 관계추론 0 (relation은 사용자 선택값만).

## 변경 (신규 1파일, 기존 무수정)
`scripts/openbinggu_proposal_to_verb_edge_g2c.py` — G2-B(open_staging·묶음승인)·StagingDB·verb_edge_schema·label_kind_map 전부 무수정 재사용.

## 흐름 (연필 → 볼펜)
1. `finalize_proposal(proposal_id, relation, src, tgt)`: 사용자가 동사 6종 중 선택 + 방향 지정
2. 게이트 체인 (fail-closed, 실패=주입 0+audit BLOCK): actor=human → proposal 실재·accepted 상태(미승인/기각/기확정 거부) → **노드쌍 바꿔치기 차단**(proposal 쌍과 동일 의무) → staging 노드 실재 → **validate_verb_edge 풀 검증**(6종·label_kind 매트릭스·방향·증거 승계·promotion false) → 중복 엣지 차단
3. 통과 시: backup→transaction→staging edges insert(**candidate=1 유지**)+proposal `finalized` 마킹→checksum→audit ALLOW
4. `reject_proposal`: 사유 필수 기각(rejected 마킹, edges 불변) — "모든 연필이 볼펜이 되는 건 아님" 경로

## 검증
- selftest **12/12 첫 실행 GO**: 정상 확정(증거 승계·finalized) / 이중 확정·기각 후 확정·미존재·쌍 바꿔치기·auto·매트릭스 위반·6종 외 전부 차단 / checksum rollback(finalized 마킹까지 원자 롤백) / audit 변조 BROKEN / confirmed·promotion 0 전수
- 회귀: G2-B 12/12 · staging_write 11/11 · schema · proposal · mvp2 · doctor 전건 GO · 운영 mtime 불변

## 파이프라인 완성도 (이 시점)
관찰(G0 5종 분류) → 연필 후보(G2 자동, 약한 2종) → 묶음 1클릭 승인(G2-B) → **동사 선택 볼펜 확정(G2-C)** — 전 구간 temp dry-run으로 닫힘. 남은 것 = real staging 1회 실적용 + confirmed 승격 게이트(전부 별도 GO).

## 다음 (owner GO 대기)
- real staging DB 1회 실적용 (현재 전부 temp)
- deprecated(기각 도장) staging 연동 + 검증 리마인드 루프(서베이 후보 3)
- GO-HOSTED-REALPACK-DEPLOY
