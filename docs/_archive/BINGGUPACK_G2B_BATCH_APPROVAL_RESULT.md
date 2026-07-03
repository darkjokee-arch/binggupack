# G2-B — 연필 후보 묶음 승인(batch approval) 결과 (2026-06-11, GO)

> staging write = temp SQLite 한정 (owner GO 범위). 운영 store write 0 · confirmed 생성 0 · 본 그래프 edges insert 0 · deploy 0 — 전부 selftest로 증명.

## 변경 (신규 1파일, 기존 무수정)
`scripts/openbinggu_proposal_batch_approval_g2b.py` — StagingDB(운영경로 거부·WAL·checksum·audit chain) 무수정 재사용 + `edge_proposals` 전용 테이블 추가(본 그래프 edges와 물리 분리, R3 지시 8).

## 동작 (R3 지시 7 — 태깅 노동 없는 승인)
1. `build_batch()`: 후보 묶음 → 1화면 마크다운 요약 (라벨 카운트 + 항목별 양끝 문장 발췌 + 신호=추천 사유 + 증거 id). raw 경로 미출력·batch_id 멱등.
2. 자동검사 (실패=버튼 비활성): 결정된 묶음 재결정 차단 → 약한 라벨 2종만 → evidence 필수 → self-loop → dangling → 쌍 중복 → actor=auto/reader 거부(G4).
3. `decide_batch()` 1클릭: **전체 승인**(backup→transaction insert→checksum→audit ALLOW, 승인돼도 candidate 유지) / **전체 거부**(insert 0 + audit REJECT + 재제출 차단).

## 검증
- selftest **12/12 GO**: 정상 승인(proposals=2·edges=0·candidate 유지) / 묶음·쌍 중복 차단 / 거부+번복 차단 / 강한 라벨·dangling·무증거·auto 차단 / checksum rollback / audit 변조 BROKEN / 요약 raw 0
- 1차 실행 11/12 → 검사 순서 결함 fix(결정된 묶음 판정을 쌍 중복보다 우선) → 12/12
- 회귀: staging_write 11/11 · G2 schema · proposal · doctor 전건 GO · 운영 store mtime 불변

## 다음 (owner GO 대기)
- 승인된 proposal → 6종 강한 엣지 확정(사용자 라벨 선택) 주입 경로
- real staging DB 1회 적용 (현재는 temp 한정)
- GO-HOSTED-REALPACK-DEPLOY
