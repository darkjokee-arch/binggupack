# G2 — 동사형 엣지 6종 스키마 + 기각 도장 + proposal 생산기 결과 (2026-06-11, GO-G2)

> private/dry-run 한정. 운영 store write 0 · confirmed 승격 0 · deploy 0 (owner 금지선 준수, mtime 불변 확인).

## 변경 (신규 2파일, 기존 무수정)
| 파일 | 내용 |
|---|---|
| `scripts/openbinggu_verb_edge_schema.py` **신설** | ① 6종 동사형 relation 정본 (supports_judgment/contradicts/depends_on/blocks/enables/refines + 한글 동사) ② **허용 source/target label_kind 매트릭스** (R3 지시 3 — 예: supports_judgment는 증거·상태·개념→판단만) ③ `validate_verb_edge()` (매트릭스·dangling·self-loop·evidence 필수·promotion false·candidate true) ④ **기각 도장**: status enum {candidate, confirmed, deprecated} + deprecated_reason 필수 + `default_view_filter()` (보존하되 기본 조회 제외 — Wikidata rank 차용) |
| `scripts/watcher_edge_proposal_g2.py` **신설** | node→node **약한 후보 2종만 자동 생산**: nearby_candidate(co_evidence/same_file 구조 신호)·stance_candidate(판단쌍 상반 어조, 확정 아님). **6종 강한 라벨 자동 생산 0** (R3 지시 4). 산출 = `edge_proposals.jsonl` (incoming_edges 아님 → **v0.7 loader가 구조적으로 미입력**, R3 지시 8 proposal graph 한정). 쌍당 1 + run당 32 cap. generated_by attribution. 멱등 |

## 검증 (전건 직접 실행)
- schema selftest **21/21 GO**: 6종 정상 7 + FAIL 8(매트릭스 위반·역방향·6종외·dangling·self-loop·무증거·promotion·candidate) + deprecated 4 + view 필터 + 약한 라벨 본그래프 거부
- proposal selftest **12/12 GO**: co_evidence·stance·same_file 생성 / cap 절단 / 쌍 dedup / **강한 라벨 0** / **loader edges_in=0 증명** / promotion false / evidence 필수 / 멱등 / schema 검증기가 proposal 위장 투입 거부
- 회귀 전건 GO: G0 map 17/17 · mvp2 11/11 · edge_mvp21 · batch_m1 · pack_builder · consumer_smoke · doctor 11/11
- 운영 store(localcrab_index.sqlite) mtime 6/10 그대로 — write 0 확증

## 안전선 매핑 (R3 최종 지시 대비)
- 지시 2(반박슬롯 폐기→contradicts는 문장 성립 시만): contradicts는 자동 생산 0, 스키마 검증만 ✅
- 지시 4(시간 인접≠인과·stance는 후보만): nearby/stance_candidate만 생산 ✅
- 지시 8(proposal graph 한정·본 그래프 C-2 후만): 파일 분리 + loader 미입력 증명 + 약한 라벨 본그래프 거부 ✅
- 조사 후보 1(기각 도장): deprecated 스키마+검증+view 필터 완료 (staging 적용은 C-2 연동 시) ✅

## 다음 (owner GO 대기)
- proposal → C-2 승인 UX 연동 (batch approval, R3 지시 7) — staging write 게이트 필요라 별도 GO
- 6종 강한 엣지의 승인 주입 경로 (사용자가 proposal을 6종으로 확정하는 흐름)
- GO-HOSTED-REALPACK-DEPLOY (별도)
