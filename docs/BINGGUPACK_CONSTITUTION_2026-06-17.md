# 빙구팩 헌법 (CONSTITUTION) — 최상위 우선
> 2026-06-17 확정. **이 문서가 빙구팩의 최상위 설계 기준이다.** 기존 docs의 어떤 조항이든 이 헌법과 충돌하면 **이 헌법이 우선**한다. (빙구팩母 + Loopy 장점 흡수)
>
> ## 이 헌법이 대체·우선하는 기존 조항 (stale)
> - `BINGGUPACK_USER_AGI_FULL_DESIGN.md`: "자동 후보 수집 = 꺼져 있음", 금지목록의 `dynamic_registry_sync`, "자동 저장 0" 표현 → 본 헌법 §1·§3·§6으로 대체.
> - `BINGGUPACK_V08_PERSONAL_WRITE_LOOP_DESIGN.md`: "자동 승격 0", "자동 관찰 daemon/hook HOLD" → daemon은 HOLD 유지, trigger-based capture(후보)는 §2 허용. 점진 승격은 §2·§6(사람 승인 게이트).
> - `BINGGUPACK_PUBLIC_RELEASE_POLICY.md` / `..._CHECKLIST.md` / `BINGGUPACK_SANITIZER_POLICY_BLOCK_ONLY.md`: 외부수확·철학필터·점진승격 미반영 → 본 헌법 §2·§3·§6 적용.
> - `BINGGUPACK_PACK_CONTRACT.md`: `opencrab_ingest_allowed=false`, 자동 merge STOP → "자동 **영구** ingest 금지"로 해석. dry-run·후보 제안·사람 --execute는 허용.
> - `BINGGUPACK_GRAPH_GRAMMAR_SPEC.md` / `BINGGUPACK_RATIONALE_EDGE_DESIGN.md`: edge actor/owner 모델·외부수확·철학필터 미반영 → 본 헌법으로 보완(각 사용자=자기 데이터 owner, actor=human 자기확정).
> - `README.md` / `INSTALL.md`: P0·우선순위·철학필터 미반영 → 본 헌법 §5 우선순위로 갱신 예정.
>
> ## 충돌 없음(그대로 유지): BINGGUPACK_OVERALL_GOAL, BINGGUPACK_PRODUCT_DIRECTION_TWO_TRACK, BINGGUPACK_PERSONAL_ACCEPTED_STATE_DESIGN_R2, BINGGUPACK_SEMANTIC_CLASSIFIER_DESIGN, BINGGUPACK_CAPTURE_HOOK_SETUP — 핵심 원칙 이미 일치.

---

## 0. 대원칙 (한 줄)
**빙구팩이 본체(母)다. Loopy는 빙구팩 위에 얹는 기능(子)이다.** Loopy의 자동화 장점은 가져오되, 빙구팩의 정체성·안전벨트가 그 전부를 감싼다. 부딪히면 빙구팩이 이긴다.

---

## 1. 빙구팩 정체성 (불변 = 母의 뼈대)
- 빈 뼈대 프레임워크. 각 사용자가 자기 노드·엣지·그래프를 채운다. 공개 배포.
- **영구 저장은 사람 SAVE/승인만.** AI는 추천만.
- 모든 관계엔 원문 근거 필수. 원문 변형 0.
- 로컬 장부가 원본. cloud는 읽기전용 복제. 사용자별 데이터 격리.
- 자동수집=후보 모으기(보조) / SAVE=사람 확정(핵심). 영구는 사람만.

→ 이 5개는 Loopy 무엇을 얹어도 안 바뀐다.

---

## 2. Loopy에서 흡수할 "장점만" (단점은 버림)

| Loopy 장점 | 빙구팩에 얹는 형태 | 빙구팩 토대 |
|---|---|---|
| pack 우선순위 랭킹 | 자주 쓰이고 신선한 판단을 먼저 회상 | pack/노드 메타데이터 |
| need-based 자동 생성 | **기존 기록 폴더 → 점·근거·관계 자동 채움** (P0) | watcher_pack_builder |
| 시간 자동 수확 | **내 기록(박제·traj·handoff·md) + 외부 소스(arXiv·GitHub·RSS) 주기 수확** — 단 사람이 등록한 소스만, 긁은 건 후보로만 | autopush 스케줄러 |
| **철학 필터** (Loopy Philosophy Filter, 열린 버전) | 수확물을 내 가치관으로 **거르되 배제 아님** → 맞으면 우선순위↑ / **다르지만 근거 있으면 "도전" 보관** / 무근거만 버림. (닫힌 필터 = 에코챔버 = 발전 정지 = "고집=무능" 가치관 위배) | 사용자 온톨로지·박제 |
| keep / challenge / discard | 보관(활용) / **도전(다른 관점, 주기적 반문으로 들이댐)** / 버림(이유 남김). 확정은 사람 | reflect·candidate |
| **철학 진화 루프** (신규) | "도전" 항목이 반복해서 옳다고 판명 → **내 철학 기준 자체를 재검토**하라는 신호. 필터 고정 금지 | 자기개선 신호 |
| 자기개선 planner | 반복 실수 신호 → 하네스 승격 "후보 제안"(적용은 사람) | reflect |
| dry-run 검증 | 적용 전 안전 검사 표준화 | selftest·publish_guard |
| safe apply | 영수증 + 롤백 | audit chain·백업 |
| 점진 승격 + 효과 측정 | 반복 패턴 → **경고→소프트필수→하드게이트** 단계 승격, 효과 측정 후 증명되면 올리고 아니면 되돌림(사람 승인) | 하네스 문화 |

---

## 3. 버리는 것 (양쪽 단점 = 흡수 안 함)

**Loopy에서 버림:**
- 외부 수확의 "무분별·자동 영구화"만 버림 → ① 소스는 사람이 등록한 것만(화이트리스트) ② 긁은 건 후보로만 ③ 영구는 사람 SAVE. (외부 수확 자체는 흡수)
- 규칙·hook·skill 자동 변경(통제불가 자기수정) → 사람 승인 게이트로 강제 변형.

**빙구팩에서 메움(Loopy 자동화로 보완):**
- 수동·정적(자동 채움 없음) → need-based 자동 생성으로
- 입력 좁음(git diff만) → 기록 폴더 범용 수확으로
- 자기개선 회고까지만 → planner+승격으로

---

## 4. 통합 구조 (빙구팩 골격 + Loopy 기능)

```
[입력]  내 기록(박제·traj·handoff·md·hook·스킬) + 외부 소스(사람 등록분) + 일하면서 자동 capture
   │ (시간 수확 = Loopy harvest 흡수)
   ▼
[철학 필터(열린)]  내 가치관으로 거르되 배제 아님  ← Loopy Philosophy Filter 흡수
   │
   ▼
[분류]  keep(활용) / challenge(다른 관점·도전 보관, 주기적 반문) / discard(무근거만, 이유 남김)
   │ keep + challenge 둘 다 살림 (challenge가 자꾸 옳으면 → 철학 재검토 신호)
   ▼
[자동 채움]  자료 → 점(노드) → 근거 → 관계(선) 자동 생성   ← Loopy need-based 흡수 (P0)
   │
   ▼
[사람 게이트]  preview → 사람 SAVE/승인 (영구는 여기서만)   ← 빙구팩 母 원칙 (모든 자동물의 최종 관문)
   │
   ▼
[그래프]  랭킹으로 우선순위(신선도+관련성+유용성)        ← Loopy 랭킹 흡수
   │
   ▼
[회상]  작업 전 관련 판단·관계 꺼내 쓰기 + 위험하면 반문
   │
   ▼
[자기개선]  신호(QA fail·수정·교정·낮은 답변력) → dedupe → 후보 → dry-run → 사람 승인 → 영수증/롤백 → 효과 측정 → 경고→소프트→하드 점진 승격   ← Loopy 자기개선 흡수 + 빙구팩 게이트
```

---

## 5. 우선순위

| 순위 | 할 일 | 출처 |
|---|---|---|
| **P0** | 기존 기록 폴더 → 점·근거·관계 자동 채움 (입력 범용화 + 관계 자동저장 MVP2.1) | 빙구팩 P0 = Loopy need-based |
| **P1** | ① 시간 자동 수확(내 기록 + 사람이 등록한 외부 소스 arXiv·GitHub·RSS, 후보로만) ② pack 우선순위 랭킹 ③ README "빈 뼈대" 정직화 | Loopy harvest+랭킹 흡수 |
| **P2** | 꺼내 쓰는 회상 + 자기개선 후보 planner + dry-run/safe apply/롤백 표준화 | 빙구팩 회상 + Loopy 자기개선 |
| **P3** | hard gate 승격 + 폰/웹 멀티 LLM 공유 | Loopy 승격 + 빙구팩 공유 |

---

## 6. 안전벨트 (母가 子를 감싼다)
- 모든 자동 흡수물(수확·생성·랭킹) → **영구는 사람 SAVE/승인** 통과해야만.
- 관계 자동 생성 → 근거 없으면 보류, 키에 근거·작성자 포함(조용한 덮어쓰기 방지).
- 자기개선 자동 적용 → **위험(hook/배포/삭제)은 사람 승인 큐**. 안전(경고/랭킹)만 영수증+롤백 자동.
- 외부 수확 → **사람이 등록한 소스만**(무분별 크롤링 X) + 후보로만 + 영구는 사람 SAVE. 전역 Dynamic Discovery 규칙은 "통제 없는 자동 영구화"를 막는 것이므로, 이 3중 게이트로 양립.

---

## 7. 결론
**빙구팩 = Loopy를 실제로 구현할 토대(母).** Loopy의 자동 채움·수확·랭킹·자기개선을 빙구팩에 흡수하되, 빙구팩의 "사람 승인·근거 필수·로컬 원본" 안전벨트가 전부를 감싼다. 두 단점(Loopy 통제불가 자동 / 빙구팩 수동·정적)은 서로를 메워 사라진다.
첫 삽 = **P0(기존 기록 → 점·근거·관계 자동 채움).** 이게 빙구팩 P0이자 Loopy need-based 흡수의 출발점.
