# 전 세계 사용자 온톨로지화 동향 조사 → 빙구팩 흡수 후보 (2026-06-11)

> 조사 에이전트 3개 병렬 (①LLM 그래프 메모리 ②지식표현 표준·논증이론 ③개인 지식그래프·판단 캡처). 조건: 억지 금지·헌법 정합만. 원문 보고서 3건은 본 문서 하단 출처 참조.

## 전략적 발견 (가장 중요)
- **시장 공백 확인**: mem0(48k★)·Zep Graphiti(27k★)·cognee·A-MEM 등 전부 "LLM이 자동 추출·자동 갱신" — **"사용자 승인 게이트 + deterministic 추출 + 판단/직관 특화" 조합은 전 세계 공백 지대**. 빙구팩 차별점 실재.
- 업계 2025~26 합의: "벡터 단독 불충분 → 그래프+벡터", "삭제 대신 무효화", "fact 단위 provenance" — 빙구팩의 evidence 필수·후보only는 이 흐름보다 한 발 앞. **유일하게 빈약한 축 = 시간성**.

## 흡수 후보 (수렴도 순, 전부 헌법 충돌 없음)

| # | 메커니즘 | 출처(수렴) | 빙구팩 적용 |
|---|---|---|---|
| 1 | **삭제 금지·무효화 마킹** — candidate/confirmed에 `deprecated` 제3 상태 + 엣지 bi-temporal(`valid_at/invalid_at/created_at/expired_at`). 기본 조회 제외하되 "왜 틀렸나"+반박 증거 보존 → **같은 오판 재생성 차단** | Wikidata rank + Zep/Graphiti + TMS **(3/3 보고서 독립 수렴)** | 기존 tombstone·supersede 규칙에 자연 결합. 무효화도 1클릭 승인 |
| 2 | **증거 역인덱스 + 흔들림 전파** — evidence→인용 엣지 역참조 빌드, 증거 무효화/파일 소실 시 의존 confirmed 판단·패턴 규칙에 `needs_review` 플래그 자동 부착 (순수 그래프 순회, LLM 0) | TMS(JTMS) + KAG mutual index (2/3) | doctor/freshness 검사에 1단계 추가. 자동=플래그만, 강등=1클릭 |
| 3 | **판단 검증 리마인드 루프** — 판단 노드에 `검증예정일` + 경과 시 미검증 노드를 승인 큐에 올려 결과 입력 요구. 상태 enum `미검증/적중/빗나감/판정불가` + 유효기간. 패턴 승격 전 **반례 0건 자동 검사** | Fatebook + GraphRAG claim status + Voyager skill-debt 교훈 (2/3) | R3 토론 지시6(평가 노드)·G7과 직결. "미검증 영구 방치" 차단의 검증된 UX |
| 4 | **생성 주체 기록** — 모든 candidate에 `generated_by {extractor_ver, session_id, ts}` 3필드 | W3C PROV-O (1/3, 저비용) | 추출기 버그 발견 시 해당 버전 산출분 일괄 식별·회수. 사람 vs 자동 구분 |
| 5 | **PPR 연상 검색** — evidence 검색 히트를 시드로 Personalized PageRank 1회 → 연결된 판단·개념까지 multi-hop 인출. 100% deterministic·읽기전용·LLM 0 | HippoRAG (1/3, 저비용 고효과) | SQLite 그래프 위 수십 줄. "검색 중심=evidence index" 철학의 확장 |

차순위(여유 시): Toulmin **warrant 슬롯**(왜 이 증거가 이 판단을 지지하나 — 같은 warrant 3회+ = 패턴 승격 트리거의 deterministic화) / Tana식 **판단 노드 필수 필드**(대상·근거·예상결과 강제) / Wikidata qualifier(판단 유효 조건 별도 필드) / LightRAG 엣지 검색 키.

## 기각 (전 보고서 공통 — 헌법 충돌)
- LLM 자동 추출·자동 UPDATE/DELETE (mem0)·LLM importance 점수(Stanford)·LLM fact rating(Zep)·메모리 자동 진화(A-MEM) — deterministic·승인게이트 위반
- AIF S-node 전면 reification·ATMS 다중 세계·nanopub trusty URI·Leiden 커뮤니티 — 다자·분산·대규모용, 1인 로컬 과복잡
- nanopublications 3분할 — pack 구조가 이미 동형(재발명 완료 상태)
- Letta/LangMem — 그래프 스키마 자체가 없어 흡수 대상 아님

## R3 토론 결론과의 정합
- 후보1·2(무효화+전파)는 R3 지시6(outcome 별도 노드)·지시8(폐기 목록)과 충돌 없이 보강
- 후보3(리마인드)은 G7 피드백 루프의 실행 UX
- 후보4(주체 기록)는 G0 선행 게이트에 끼워넣기 최적(스키마 확정 시점)
- 후보5(PPR)는 독립 — 언제든 추가 가능

## 출처
- 에이전트 보고서 3건 원문: 세션 traj 참조 (Graphiti/Mem0/HippoRAG/KAG/GraphRAG/LightRAG/Cognee/Letta/LangMem/A-MEM/Wikidata/PROV-O/Toulmin/AIF/TMS/nanopub/Fatebook/Stanford GA/Voyager/Tana 등 40+ 1차 출처 링크 포함)
