[결함 2건]

# replace transaction 적대 검증 — openbinggu_candidate_replace_ux.py (2026-06-11)

대상: `scripts/openbinggu_candidate_replace_ux.py`
설계 정본: `docs/BINGGUPACK_CANDIDATE_REPLACE_TRANSACTION_DESIGN.md` (r2)
검증 방식: 기존 selftest 14/14 재확인 + tempfile.mkdtemp 격리 환경 적대 재현(실행). 운영 store/real staging 미접근(전후 mtime 불변 실측).

## 종합 판정
원자성·롤백·audit chain·confirmed0·운영store 불변 등 **안전 핵심 속성은 전부 견고**(주입 테스트 통과). 단, 설계가 명시한 **중복/재생성 차단(§4·r2 결론2-3)과 80자 캡(§3)** 두 보증이 깨진다. 안전(데이터 파괴) 결함은 아니나, 설계 보증 위반 2건이므로 통과 불가.

---

## 결함 1 — canonical_hash 정규화 불완전: replace_same_content / duplicate_active_content 우회 (심각도: 중)

`_canonical_hash`(line 42-44)는 `re.sub(r"\s+"," ",s).strip()` 후 sha256만 한다. 공백만 정규화하고 **zero-width(U+200B)·유니코드 분해형(NFD)·대소문자**는 정규화하지 않아, 의미상 동일한 문장이 다른 canonical을 갖는다. 이 함수는 §4 "같은 오판 재생성 차단"과 r2 결론2-3 "다른 active와 동일 시 중복 BLOCK"의 **유일 판정 기준**이라, 두 게이트가 동시에 뚫린다.

재현(실측 출력):
```
판단 원본 문장: '이 입찰은 마진이 낮아 보류한다.'
  canon(원본)==canon(zw삽입)? False        ← zero-width 삽입본은 "다른 내용"으로 판정
  원본==NFD? False  canon 동일? False       ← 한글 분해형도 "다른 내용"
  [재현] zero-width 삽입 replace 결과 applied=True reason=None
  >>> 결함: 의미상 동일 판단이 zero-width로 재생성됨 (new_nid=node:CONV:3a78a55c)
캐논 case: canon('Deploy ABORT now')==canon('deploy abort now')? False  ← 대소문자도 우회
```
- 정상 경로(selftest 3번)는 "공백만 변형"이라 막히지만, **공백 외 변형(U+200B / NFD / case)은 전부 통과**해 방금 기각한 판단을 그대로 재생성하거나 다른 active와 사실상 동일한 문장을 중복 저장한다.
- 현실 트리거: IME·복붙이 NFD를 만들거나, 사용자가 보이지 않는 zero-width를 끼운 수정문장을 confirm. (Korean 환경에서 NFD가 가장 현실적.)

수정 권고: `_canonical_hash`에서 sha256 전에 `unicodedata.normalize("NFC", s)` → zero-width/제어문자 제거(`​-‍﻿` 등) → 필요 시 `casefold()`까지 적용. duplicate_active_content 스캔도 같은 함수를 쓰므로 함수 한 곳 수정으로 양쪽 닫힘. selftest에 NFD/zero-width 변형 케이스 추가 권고.

---

## 결함 2 — 80자 캡 미적용: 긴 new_sentence 전문 저장 + selftest 허위 안심 (심각도: 중하)

설계 §3은 "수정문장도 ... **80자 캡** + 중복 차단"을 게이트로 명시한다. 구현은 new_sentence를 절단 없이 node.sentence·evidence.sentence·pack.content에 **전문 그대로** 저장한다(line 86-143). 형제 모듈(list_view DISPLAY_CAP, capture_preview ≤80 발췌, save_selected의 `c["sentence"]`)이 전부 80자 규율을 지키는 것과 불일치.

재현(실측 출력):
```
  new_sentence 길이: 134
  [재현] 긴 문장 replace applied=True
  저장된 node.sentence 길이: 134  == 입력 전문? True
  저장된 evidence.sentence 길이: 134
```
- selftest 체크 10("raw_원문_미저장")은 `T1 not in blob`(원본 대화문 부재)만 본다. **신규 문장 길이는 검사하지 않아** 134자 전문 저장을 잡지 못한다 → 캡 부재가 테스트로 가려진다(허위 안심).
- 구현자 보고는 "confirm 문구가 수정문장 전문 포함이라 캡 미적용"을 사유로 들지만, 이는 **설계 r2와의 명시적 deviation**이며 문서·코드 어디에도 "캡 면제" 합의가 박제돼 있지 않다.

수정 권고: (a) 캡 적용 시 confirm은 전문 대신 `_sent_hash(new_sentence)`로 바인딩하도록 confirm 규약 변경, 또는 (b) 캡 면제를 설계 문서에 정식 결정으로 승격하고 selftest에 "신규 문장 길이 상한/저장 정책" 케이스를 추가해 회귀 고정. 현 상태(설계는 캡, 구현은 무캡, 테스트는 무검사)는 셋이 어긋나 있어 그대로 두면 안 됨.

---

## 관찰 (재현 미수행 — 코드 검토) : 묶음 종결 tail 비보호 구간 (심각도: 하)

staging_apply 성공 이후 `supersedes` UPDATE(line 156-157)와 종결 audit_append(line 161-163)는 try/except·rollback 밖에 있다. 이 둘 중 하나가 예외를 던지면 deprecate(commit)+신규 insert(commit)는 살아남고 역링크/종결 audit만 누락된 부분 상태가 남으며 rollback도 안 된다. 정상 경로에선 supersedes 컬럼 실재(검증됨)·정상 audit이라 발생 가능성은 낮아 fault-injection 없이는 재현 불가. 권고: tail 두 연산을 deprecate+insert와 함께 보상 범위(실패 시 rollback)로 감싸거나, 최소한 supersedes 실패를 audit에 기록.

---

## 견고 확인 (공격 실패 = 정상)
- 원자성/롤백: staging 단계 `checksum_mismatch` 주입 → `rolled_back:staging_apply:sqlite_checksum_mismatch`, 원본 state active 유지(before=after=active), 부분쓰기 0. (실측)
- audit chain: 성공 누적 후 `verify_chain()=True`, **롤백 후에도 True**(스냅샷 복원으로 deprecate/insert 내부 entry 소거 → rolled_back 1건 append, prev_hash 연속). 체인정합 vs 기록소실 trade-off는 의도대로 체인정합 우선 처리됨. (실측)
- confirmed0/promotion0: 정상/롱센텐스/롤백 3개 DB 전수 bad=0. (실측)
- TOCTOU/stale: confirm은 인자 삼중 바인딩 + 실행 직전 list 재실행·id8(sha256 node_id 32bit) 재검증. 시프트/필터 변경 시 index→id8 불일치로 BLOCK(selftest 2 + 단일스레드 CLI라 동시변경 경로 없음). 우회 미발견.
- target_not_active: deprecated 노드 재replace BLOCK(selftest 8). 단 설계 §7 케이스7 명칭은 `already_deprecated`인데 구현 reason은 `target_not_active` — 동작 동일, 명칭만 deviation(비결함).
- 운영 store / real staging: 전 재현 후 OPERATING_PATHS mtime 불변=True, real_staging 미접근.

## 재현 환경
- baseline: `python scripts/openbinggu_candidate_replace_ux.py --selftest` → 14/14 PASS GATE GO.
- adversarial: tempfile.mkdtemp 격리 스크립트 1회 실행(temp만 write, 종료 시 rmtree). 운영/RC 트리 신규 파일 0(본 문서 1개 예외).

---

## 수정 반영 (지휘자, 2026-06-11 같은 세션)

- 결함 1 fix: `_canonical_hash` = NFC 정규화 + 공백 정규화 + Cf(format) 문자 제거 + casefold — zero-width·NFD·대소문자 우회 전부 동일 hash 수렴. 회귀 케이스 3b(zerowidth+NFD predecessor)·4 확장(zerowidth duplicate_active) 추가.
- 결함 2 fix: 80자 초과 = silent 절단이 아니라 명시 BLOCK(`sentence_too_long_max80`) — silent drop 금지 원칙 정합. 회귀 케이스 4b(134자 BLOCK+DB 무변) 추가.
- 관찰 fix: supersedes UPDATE + 종결 audit 를 try/except 보호 영역으로 이동 — 예외 시 스냅샷 원복(`rolled_back:finalize:*`).
- 재실행: selftest **16/16 GATE GO** · 회귀 depux 15/15·list view 13/13·save 12/12 · real DB sha 불변(37557F2A09ECF06D).
