# G0 — 생산→헌법 정렬 결과 (2026-06-11, GO-G0)

> 4cli R3 지시 1 + 글로벌 조사 후보 4(generated_by) 동승. dry-run 트리 한정, 운영 store write 0.

## 변경
| 파일 | 내용 |
|---|---|
| `scripts/openbinggu_label_kind_map.py` **신설** | 한영 매핑 단일 정본 (한글 5종 ↔ A0 영문 5종 ↔ space/node_type, merge_adapter NODE_MAP 정합 selftest로 강제) + deterministic 분류기 `classify_label_kind()` (정규식 5규칙 + 판단 fallback, LLM 0·멱등) |
| `scripts/watcher_candidate_mvp2.py` 수정 (백업 `.bak_g0_20260611`) | ① label_kind "판단" 하드코딩 제거 → 분류기 결선 ② space/node_type 매핑 유도 ③ `generated_by {extractor, rule_version}` attribution (timestamp 미포함 = 멱등 유지) ④ `rule_id` 분류 근거 기록 ⑤ **A0 헌법 shadow 판정** (`a0_verdict` PASS/REVIEW/FAIL 기록만, stop은 기존 가드 유지) ⑥ PROP_KEYS whitelist 3키 확장 |

## 검증 (전건 직접 실행)
- 매핑 selftest 17/17 GO (분류 13 + NODE_MAP 정합 + 한영 왕복 + A0 일치 + 멱등)
- 회귀 체인 전건 GO: mvp2 11/11 (loader 7불변식 — 신규 키·5종 분류 통과) · edge_mvp21 · batch_m1 · pack_builder · consumer_smoke · doctor
- 실증: 합성 6문장 → 문서/증거/개념/상태/판단 5종 정확 분류 + diff 요약문은 fallback_judgment + **a0_verdict=FAIL** (캡처 품질 문제 가시화)

## 판정·한계
- **한영 매핑 함정(C 지적) 해소**: 매핑 정본 1곳 + selftest가 3파일(A0/loader/merge_adapter) 정합을 상시 감시
- **A0 full 강제는 보류**: 현 capture가 만드는 diff 요약문은 종결어미가 없어 A0 기준 "핵심 문장" 미달 — full 강제 시 watcher 출력 전멸. shadow 기록으로 비율을 누적 관찰 후, 캡처 문장 품질 개선(별도 GO)과 함께 강제 전환
- deprecated 상태(조사 후보 1)는 staging/pack 스키마 쪽 변경이라 G2(엣지 스키마 확정)와 묶어서 진행 권장

## 다음 (owner GO 대기)
- **GO-G2**: 동사형 엣지 6종 스키마 확정 + deterministic 후보 생산(proposal 분리) — R3 지시 2·4·8 정합
- GO-HOSTED-REALPACK-DEPLOY (deploy+rollback 실증+토큰 회전 한 묶음, 별도)
