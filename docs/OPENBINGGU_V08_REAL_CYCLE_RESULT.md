# v0.8 쓰기 루프 — real staging 1사이클 실연 결과 (2026-06-11, owner GO)

> gate 4조건 전부 충족 후 진입: ①temp 12/12 ②회귀 전건 ③rollback 문서 선갱신 ④owner GO 발화("real staging go"). 진입 직전 4cli 토론 1회(B 조건부→C·D 불승인→B 종합 지시 6: 수동 2회 한정·confirm 원본 발화 증빙·전 테이블 무결·allow_review 금지·rollback 재실연 폐기(기실증 참조)·러너 출력물 단일화 — 전부 러너에 반영).

## 실연 결과 — 9/9 (검증식 결함 1건 즉시 정정 후 단독 재검증 통과)
| 증빙 | 실측 |
|---|---|
| 실행 실체 고정 | save 모듈 sha256=a02f8519… / 러너 sha256=6806b4e7… |
| 스냅샷 선확보 | `snap_v08_before_520e2b22f8b8b4d4.sqlite` + 전 8테이블 before (nodes9·edges10·evidence9·registry2·audit10·reviews1·proposals9·deprecations1) |
| 저장 전 미리보기 | 후보 3건 — 판단/상태/개념 정확 분류·전건 PASS |
| 저장 | **3건 + 판단 due 1건** (pack=conv_97474ce2, rejected 0, allow_review 미사용) |
| 전 테이블 무결 | 기존 row 전건 보존(append-only audit 포함) — inserted: nodes3·edges3·evidence3·registry1·audit3·reviews1 |
| 원문 무누설 | 입력 전문이 DB 전체·audit 어디에도 없음 (문장 단위만) |
| audit | insert→conv_save(ALLOW)→review_due 순서 정상·chain INTACT |
| read-back | active 11노드·pending review 2·candidate/promotion 위반 0·운영 store 불변 |

- 검증식 결함 1건: "마지막 audit=conv_save" 가정이 review_due append 순서와 충돌 — 존재 검사로 정정(데이터는 처음부터 정상)
- rollback: 스냅샷 copy 1줄(checksum 원복 6/11 기실증 절차 참조 — 재실연 폐기는 4cli 합의)

## v0.8 루프 상태
**preview → 사용자 선택(confirm) → candidate 저장 → 피드백(due/리마인드) → real staging 검증 — 전 구간 영속 DB에서 완주.** confirmed 0 · OpenCrab 0 · deploy 0 · 원문 저장 0 유지.

## 다음 (owner 결정): 검증예정일(6-25) 도래 시 리마인드→4값 resolve 실사용 / RC 동기화·commit(v0.8 산출 4파일) / hosted save 노출은 E 체크리스트 4 선행(별도 GO)
