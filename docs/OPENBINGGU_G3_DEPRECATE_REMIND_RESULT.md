# G3 — 기각 도장(deprecated) + 판단 검증 리마인드 결과 (2026-06-11, owner GO)

> staging 한정. 운영 write 0 · confirmed 0 · deploy 0 · **자동 승격 0** (리마인드는 사람 검토 유도까지만).

## 변경
- `scripts/openbinggu_deprecate_and_remind_g3.py` 신설 — StagingDB 본체 무수정, 추가 테이블 2종(deprecations·judgment_reviews)
  - `deprecate_item`: **삭제 아닌 보존**(물리 잔존) + state='deprecated' + **사유 필수** + 반박 증거 ref 선택 + audit·스냅샷. 이중 기각·tombstone·auto 차단
  - `active_view`: 기본 소비에서 deprecated/tombstoned 제외 (Wikidata rank 차용 — 글로벌 조사 후보 1 staging 연동 완료)
  - `set_review_due` / `list_due_reminders` / `resolve_review`: 검증예정일 → 경과 시 **목록 생성까지만**(상태 무변 checksum 증명) → 사람이 성공/실패/불확실/판정불가 입력(기록만, 노드 무변 — Fatebook UX 차용, 조사 후보 3)
- `scripts/openbinggu_real_staging_g3_once.py` — real staging 실연 러너

## 검증
- selftest **12/12 첫 실행 GO** (보존+제외·사유 필수·이중/auto/형식 차단·리마인드 상태 무변·resolve 노드 무변·audit 변조 BROKEN·confirmed 0)
- **real staging 실연 8/8 GO**: 스냅샷 선확보 → 오분류 노드 1건 기각(active 9→8, 물리 보존) → 사유 없는 기각 차단 → **기각 proposal 재확정 차단 유지 재검증** → 검증예정일 등록 → 리마인드 1건 목록(상태 무변) → read-back(active 8·deprecated 1·edges 10·pending 1·audit 10·위반 0·chain INTACT) → 운영 불변
- 복구: `snap_g3_before_2f1d200f04e4ecb8.sqlite` copy 1줄

## 의미
글로벌 조사 흡수 후보 1(기각 도장)·3(검증 리마인드)이 staging에 실제 연동 — "틀린 판단의 보존-제외"와 "미검증 판단 방치 차단"이 헌법(승인 게이트·자동 변경 금지) 안에서 작동.

## deploy 선행 3요건 현황
staging 실사이클 ✅ · rollback 실증 ✅ · commit 정리 ✅(3784384 + 본 건) → **push + 배포 rollback 절차 확인 후 별도 GO 판단** (HOLD 유지)
