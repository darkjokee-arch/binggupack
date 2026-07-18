# BingguPack — 후보 수정 = 기각+신규 transaction 묶음 설계 (2026-06-11 설계 · 구현 완료)

> **(구현 상태) 구현 완료 — `scripts/openbinggu_candidate_replace_ux.py` (`replace_from_list`) · CLI + MCP `replace` 도구 노출.** (이전 '설계만·코드 0' 표기 갱신)


> owner 확정(BINGGUPACK_V1_CANDIDATE_MGMT_UX_GAP.md 결정①): in-place 수정 금지.
> "기존 candidate 기각(deprecated/replaced) + 수정본 신규 candidate 저장"을 하나의 transaction/audit 묶음으로.
> 근거 코드: conversation_candidate_save.py(save 게이트) · deprecate_and_remind_g3.py(deprecate_item) · staging_write_selftest.py(staging_apply/스냅샷/checksum rollback).

## 1. 어휘 — 기각 사유 코드 (권고: 기존 스키마 무수정)
- `deprecations.reason` = `"replaced_by:<신규 node_id>|<사람 사유>"` (기계 파싱 prefix + 사람 사유, 200자 캡 내).
- `counter_evidence_ref` 재사용 **기각**: 의미가 "반증 증거 ref"라 교체 링크와 충돌 — 비워 둠(향후 반증 기각과 구분 유지).
- 신규 컬럼 추가 **불필요**: reason prefix로 충분. **스키마 무수정 확정.**

## 2. 원자성 — staging_apply 자체 transaction 중첩 문제
- 실측: `deprecate_item`·`staging_apply` 모두 자체 `BEGIN~COMMIT` 보유 → 외곽 BEGIN으로 감싸면 SQLite "transaction within a transaction" 에러. 모듈 무수정 원칙상 **단일 SQL transaction 불가 → 스냅샷 보상(compensation) 방식 채택**.
- 순서(고정):
  1. **preflight 게이트 전부 선검사** (confirm·actor·A0·PII/secret·중복·동일hash — DB 무변, 실패 시 BLOCK으로 종료)
  2. **묶음 스냅샷 1회 선확보** (`shutil.copy2(db.path, snap_replace_<hash>)` — staging_apply 스냅샷 방식 재사용)
  3. `deprecate_item(원본, reason="replaced_by:...")` 호출
  4. `staging_apply(신규 mini-pack)` 호출 (+ 성공 시 신규 노드 `supersedes=<원본 node_id>` UPDATE — §6)
  5. 4 실패 시 **스냅샷 원복** = con.close() → copy2(snap, db.path) → 재오픈 (checksum rollback 의미 재사용). 원복은 audit_log도 스냅샷 시점으로 되돌리므로 직후 `candidate_replace ROLLBACK` audit 1건 append로 실패 사실 기록(체인 연속 유지).
  6. 전부 성공 시 `candidate_replace ALLOW` 묶음 audit 1건 (before=묶음 전 checksum, after=묶음 후).

## 3. confirm 문구
- `"REPLACE <n> WITH <수정문장>"` 정확 일치 의무 (n = candidate 목록 뷰 인덱스). 불일치·actor∈{auto,reader} → BLOCK (save_selected G4/confirm 게이트 동형).
- **수정문장도 신규 후보와 동일 게이트 전부 통과 의무**: A0 재판정(FAIL 거부·REVIEW는 allow_review 명시 필요) + PII/secret/bizno 재스캔 + 80자 캡 + 중복 차단. 게이트는 save_selected 3a/3b 로직 재사용.

## 4. 같은 오판 재생성 차단
- 수정문장 `content_hash`(=_sent_hash) == 기각 대상 원본 hash → **BLOCK `replace_same_content`** (preflight 단계, DB 무변).
- 확장: 동일 hash의 deprecated 노드가 이미 존재해도 BLOCK (node_id=`node:CONV:`+hash라 물리 잔존 row가 자동 충돌 — 기존재 skip에 묻히지 않게 명시 reason으로 승격).

## 5. audit 추적성 도식 (단일 hash chain 내 연쇄)
```
[preflight 실패]            candidate_replace BLOCK <reason> (1건, DB 무변)
[성공 경로]   deprecate <원본nid> ALLOW "replaced_by:<신규nid>|사유"
            → insert <pack_id> ALLOW            (staging_apply 내부)
            → candidate_replace <원본nid>><신규nid> ALLOW   ← 묶음 종결 1건
[중간 실패]   (스냅샷 원복으로 내부 audit 소거) → candidate_replace ROLLBACK <reason> 1건
```
- 묶음 audit의 pack_id 필드에 `<원본nid>><신규nid>` 기록 → 단일 행으로 양 끝 추적 가능.

## 6. 역방향 링크 (권고 1개)
- **권고: edge 추가 안 함.** ① 정방향(원본→신규) = `deprecations.reason` prefix 파싱. ② 역방향(신규→원본) = nodes 스키마에 **이미 존재하는 미사용 `supersedes` 컬럼**에 원본 node_id 기입(묶음 내 UPDATE 1줄 — 스키마 무수정).
- edge 방식 기각 사유: edge는 evidence_refs 필수(헌법)인데 교체 관계의 증거는 자기증빙뿐이라 부적합 + deprecated 노드를 가리키는 active edge가 active_view 소비를 오염.

## 7. 구현 게이트 — temp selftest 케이스 (구현은 **별도 GO** 후)
1. 정상 replace: 원본 state=deprecated + reason prefix, 신규 active·candidate=1·promotion=0·supersedes=원본, audit 3연쇄(deprecate→insert→candidate_replace ALLOW)
2. 중간 실패 원복: checksum_mismatch 주입 → 원본 active 복귀·신규 0건·ROLLBACK audit 1건·chain INTACT
3. 동일 hash 차단: 수정문장=원본 → BLOCK replace_same_content, DB 무변
4. confirm 불일치 → BLOCK confirm_phrase_mismatch, DB 무변
5. actor=auto/reader → BLOCK G4_no_auto
6. 수정문장 PII/A0 FAIL → BLOCK (deprecate 미발생 — preflight 선검사 증명)
7. 이미 deprecated 원본 재replace → BLOCK already_deprecated
8. 전 케이스 공통: confirmed 0·promotion 0·운영 store mtime 불변·audit chain verify

## 불변 (공통 금지 준수)
real staging(tmp/real_staging) 접근 0 · hosted/deploy 0 · OpenCrab 호출 0 · confirmed 자동 생성 0 ·
in-place 수정 0 · git commit/push 0 · RC 트리 무수정 · temp SQLite 한정.

---

## 4cli 토론 반영 (r2, 2026-06-11 — 정본: BINGGUPACK_V1_BOUNDARY_DEBATE_CONCLUSION.md)

- **confirm 형식 정렬**: `REPLACE <n> <id8> WITH <수정문장>` — 인덱스 단독 금지(결론 4), id8 = 목록 뷰 id 칼럼. confirm은 transaction 밖에서 받고, 실행 직전 목록 재실행 + id8 재검증(기각 UX 기구현 패턴 재사용).
- **duplicate 검사 정밀화**: canonical_hash(공백·줄끝 정규화) 기준 + **predecessor 자신은 예외** — 다른 active와 동일 시 BLOCK, predecessor와 동일 시 no-op (결론 2-3. 본문 §5 동일 content_hash BLOCK 규정을 이 기준으로 대체).
- **원자성 재확인**: 외곽 단일 SQL transaction 불가(본문 §2) → 묶음 스냅샷 1회 + 중간 실패 시 파일 원복은 결론 2-2(짧은 transaction + hash 재검증)와 양립 — 사람 대기 중 잠금 0 유지.
- **event sourcing 금지** 재확인: replaced_by reason prefix + supersedes 컬럼 = 기존 state 모델 내 해법(결론 2-1 정합).
- 구현 selftest 의무 케이스 추가: stale 목록 오지정(id8 mismatch)·TOCTOU 변경·batch 일부 hash 불일치 (결론 5).
- 구현 = 별도 GO (owner 2026-06-11: D 항목).

## r3 (2026-06-11 적대 검증 반영)
- canonical_hash 정의 확정: NFC + 공백 정규화 + Cf 제거 + casefold (구현 `_canonical_hash` 정본)
- 80자 캡 = 입력 검증 BLOCK(`sentence_too_long_max80`) — silent 절단 금지
- supersedes/종결 audit = 보호 영역(실패 시 묶음 원복)
