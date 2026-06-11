# v0.8 개인용 쓰기 루프 설계 (2026-06-11, GOAL MODE — 설계 단계)

> **루프**: preview → 사용자 선택 → candidate 저장 → 피드백 기록 → real staging 검증.
> **위험 인식**: v0.7=읽기, v0.8=저장. 설계→temp→real staging(별도 gate)→rollback 순서 고정.
> **불변**: candidate-only · promotion_allowed=false · confirmed 0 · OpenCrab apply 0 · 원문 저장 금지 · audit/rollback 필수 · live deploy 금지(채팅 hosted write 노출은 v0.8 범위 외 — 로컬 루프 우선).

## 0. 실행 표면 결정 (중요)
v0.8 쓰기 루프는 **로컬(Claude Code CLI) 우선**. 근거: ① owner 금지선 "live deploy 금지" ② E 설계 체크리스트 4(hosted write 첫 노출 = 인증 상향·원격 staging 전송 경로·audit·rollback 선행)가 미해결 ③ real staging은 로컬 SQLite. 채팅(hosted)에서의 save 버튼은 **이 루프가 로컬에서 검증된 후** 별도 GO.

## 1. save UX 설계 (owner 작업 1)
- **저장 전 미리보기 의무**: capture_preview 결과 표(후보 N건 + 도장 + 헌법판정)를 먼저 표시 — 저장 대상은 이 표의 행 번호로만 지정.
- **사용자 명시 승인**: "3,5,7번 저장해" 식 **명시 선택**만(전체 자동 저장 없음). 승인 단위는 G2-B 묶음 패턴(선택분 1배치 = 1클릭/1발화).
- **피드백 4버튼**(성공/실패/불확실/판정불가): 저장 자체가 아니라 **저장된 판단 노드의 사후 검증**에 쓰임 — G3 `resolve_review` enum과 1:1 (기구현·검증 완료 재사용).
- **원문 저장 금지**: 저장되는 것은 선택된 후보 문장(≤80자 발췌)뿐 — 입력 전문·대화 로그는 어떤 테이블에도 기록 0 (selftest로 증명).

## 2. conversation_candidate_save 설계 (owner 작업 2 — **적대 리뷰 15건 반영판**)
**핵심 결정 2개**:
(A) 신규 쓰기 경로를 만들지 않고 mini-pack → 기존 `staging_apply`(C-2 검사 내장) 경유.
(B) **save는 preview 결과 객체를 받지 않는다 — 원본 text를 받아 capture_preview를 내부 재실행**(deterministic·멱등 기증명) → TOCTOU 문장 변조 주입·전달 매체 문제 동시 해소 (보안 결함2·운영 결함8).
```
save_selected(db, text, indices=[3,5,7], ctx, snap_dir, due_date=None)
 1) actor=human + **confirm 문구 검증**: ctx["confirm"] == "SAVE 3,5,7" (선택 인덱스와 정확 일치
    의무 — 사람 발화 유래 증거의 경량 fail-closed, 불일치=BLOCK) (보안 결함6)
 2) capture_preview(text) 내부 재실행 → 후보 재생성 (변조 불가)
 3) 후보별 게이트 (선택분만):
    - **저장될 문자열(≤80자 발췌) 그대로 A0 재판정**: FAIL=거부 / REVIEW=ctx["allow_review"]=True
      명시 시만 (헌법 결함2·3)
    - PII/secret/bizno 재스캔 (preview와 동일 패턴 — 방어 목적은 재실행 경로 무결성)
    - **기존재 노드(같은 content-hash) skip** — 부분 재선택 시 배치 전멸 방지 (헌법 결함6)
 4) mini-pack 조립: pack_id="conv_<선택내용hash8>"
    - node_id = "node:CONV:" + 문장hash8 (**결정적** — 중복 적재 구조 차단)
    - node_type/space = lkmap.KIND_TO_SPACE_NTYPE[label_kind] 경유 (한글 kind→영문 NTYPE — 어휘
      3종 분열 해소, 헌법 결함1)
    - evidence: source_pointer_id="**conv-self:**<hash8>" — **자기증빙임을 prefix로 명시**(보안 결함4),
      source_hash=captured_hash=capture 시점 동결 hash (**ephemeral 출처라 freshness가 동어반복임을
      숨기지 않음** — audit reason "ephemeral_conv"로 기록, 이런 evidence는 promotion 영구 제외 — 보안 결함3·헌법 결함4)
 5) staging_apply 경유 (duplicate·backup·transaction·checksum rollback·audit chain 재사용)
    + save_selected 자체 audit "conv_save" 1건 추가 (C-2 1클릭과 계층 구분 — 헌법 결함5)
 6) 판단 노드 + due_date 지정 시 G3 judgment_reviews 등록
 반환: {saved, skipped_existing, rejected(사유별), pack_id, snapshot}
```
- 저장 위치: **real staging SQLite 한정** (StagingDB 운영경로 거부 내장)
- **저장 한계 명시**(보안 결함1·7): 저장되는 것은 사용자가 명시 선택+스캔 통과한 문장 발췌뿐이나, 정규식이 못 잡는 민감 한 줄은 사용자 판단 책임 — 사후 구제는 deprecate(보존-제외)/tombstone. selftest로 "입력 전문이 DB 어디에도 substring으로 없음"을 증명.
- **스냅샷 보존 정책**(보안 결함5): snap_dir 누적은 수동 정리 대상 — 민감 노드 tombstone 시 이전 스냅샷 동반 파기 절차를 real gate 문서에 포함. 자동 정리 스크립트는 별도 GO.

### real staging 진입 gate 정의 (헌법 결함7 — 4조건 고정)
① temp selftest 전건 GATE=GO ② 기존 체인 회귀(6도구 live + staging/G2계열/G3/doctor) GO ③ rollback 절차 문서 선갱신 ④ **owner 명시 GO 발화** — 4개 전부 충족 전 real staging write 0.

## 3. feedback 기록 (owner 작업 3)
- **전부 G3 기구현 재사용** (신규 코드 0): `set_review_due`(예정일) → `list_due_reminders`(사람 검토 유도만) → `resolve_review`(4값+사유 필수, **기록만 — 노드 state/candidate 무변**)
- **실패여도 자동 강등 금지**: resolve "실패" ≠ deprecate. 강등은 `deprecate_item`(사유 필수) **별도 사람 행동** — G3 selftest 9·real 실연에서 이미 증명된 분리.

## 4. 검증 계획 (owner 작업 4)
| 단계 | 내용 |
|---|---|
| temp selftest (~12종) | 정상 선택 저장(노드/증거/엣지+audit) · 비선택 미저장 · **원문 미저장 증명**(입력 전문이 DB 어디에도 없음) · PII/bizno 재스캔 차단 · auto 차단 · duplicate 차단 · checksum rollback · confirmed 0 전수 · 운영 mtime 불변 · 멱등 |
| real staging 1사이클 (**별도 gate**) | 스냅샷 선확보 → preview→선택→저장 1배치 → due 1건 → read-back → rollback 절차 문서 사전 갱신 |
| 회귀 | 기존 read-only 6도구 live smoke(12+9) + staging/G2/G2-B/G2-C/G3/doctor 체인 |
| 노출 0 | secret/PII/bizno 노출 0 · raw 원문 저장 0 · confirmed 0 · OpenCrab write/apply 0 |

## 5. HOLD (v0.8에서도 불변)
team paid · marketplace · 결제 · seed scope 정책 구현 · OpenCrab apply/finalize · confirmed 운영 반영 · hosted write 도구 노출(E 체크리스트 4 해결 전) · 자동 관찰 daemon/hook

## 6. 근거 인용 (재토론 불요 항목)
- 4cli R3 지시 6·7: 평가=별도 기록·자동 승격 0 / 자동 관찰 불가·승인 확정 — 본 설계 그대로
- 글로벌 조사 후보 3(Fatebook 리마인드)·1(deprecated 보존-제외): G3로 기구현
- E 설계(피드백 버튼 UX): 4값 enum 1:1 — hosted 노출만 보류
