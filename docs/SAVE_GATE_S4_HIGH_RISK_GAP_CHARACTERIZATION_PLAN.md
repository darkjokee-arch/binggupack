# save-gate S4 — high-risk GAP characterization 관측 설계 (design-only)

**기준 HEAD:** f642fd1
**성격:** **design-only · 구현 0.** 본 문서는 테스트 코드도, production 코드도 작성하지 않는다.
잔여 high-risk GAP 13건(actual write core 인접)을 "구현 없이 어떻게 관측할 것인가"의
**관측 방식만** 정의한다.
**상위 설계:** `docs/SAVE_GATE_S4_CHARACTERIZATION_DESIGN.md` §2(케이스 표) · §4(선행 의무) · §5(원칙)

**HOLD 선언:**
- **S4 implementation HOLD.** 본 문서는 GAP 관측 방식 설계일 뿐, 어떤 코드 이동·변경도 승인하지 않는다.
- **actual write core(`staging_apply` + `save_selected` + `commit_selected` + `deprecate_g3`)는 S4-6 마지막 / 영구 HOLD 후보.** 본 문서는 이 본체를 **수정 대상으로 다루지 않는다.**
- **owner approval / 토큰 미요청.** 본 작성은 토큰 자격을 주장하지 않는다(테스트 추가조차 별도 단계).

---

## 0. 관측의 절대 불변식 (모든 GAP 공통)

본 문서가 설계하는 "관측"은 **외부에서 import 후 호출 + 단언(assert)** 뿐이다. 다음을 절대 위반하지 않는다.

1. **본체 무수정.** `staging_apply` / `save_selected` / `commit_selected` / `deprecate_g3`(deprecate_item·set_review_due·resolve_review·classify_harvest_item) 본체는 **절대 수정·이동 대상 아님.** 관측은 전부 외부 호출+단언으로만 한다 — 함수 안에 hook·플래그·로그를 새로 심지 않는다.
2. **부작용 주입은 입력 측에서만.** 분기를 타게 하려면 (a) 이미 본체가 읽는 `ctx` 플래그(`backup_fail`/`wal_abort`/`checksum_mismatch`/`actor`/`allow_review`/`confirm`), (b) 손상/조작한 pack·DB 행, (c) monkeypatch(외부 의존 모듈을 테스트 측에서 교체) — 이 3가지로만. 본체 코드에 새 분기·새 ctx 키를 추가하지 않는다.
3. **temp DB / 운영 store 불변.** 전 시나리오 temp 파일 SQLite·`BINGGU_HOME` temp 격리. `OPERATING_PATHS` mtime 전후 동일.
4. **새 동작 요구 0.** 관측은 *현재 동작 그대로* 고정(characterization)이다. 단언이 통과하지 않으면 그것은 "버그 발견"이지 "코드를 고칠 신호"가 아니다 — design-only 단계에서는 기록만 한다.

> 관측 패턴 선례: `scripts/openbinggu_s4_gap_characterization_selftest.py`(A2·E1b·B5·F2~F4·D11, 20/20 GREEN). 본 문서의 13건도 동일한 import-호출-단언 패턴을 따른다.

---

## 1. 대상 13건 · 위험 등급 · 관측 가능성 요약

| GAP | 의미 | 대상 함수 | 위험 등급 | 부작용 주입 | core 미접촉 관측 가능? |
|---|---|---|---|---|---|
| A4  | evidence.source_missing → `freshness_source_missing` | `c2_check` | High(게이트 판정) | 손상 pack(evidence.source_missing=True) | **가능(미접촉)** |
| A6  | redaction_policy≠v1 → `freshness_redaction_policy_changed` | `c2_check` | High(게이트 판정) | 손상 pack(redaction_policy="v2") | **가능(미접촉)** |
| A10 | 판정 순서 고정(actor→evidence_refs→freshness→duplicate→backup) | `c2_check` | High(우선순위=방어 골격) | 다중 위반 pack 조합 | **가능(미접촉)** |
| B9  | snapshot 동반(파일 존재) | `staging_apply` | High(actual write core) | 정상 pack(부작용 0) | **가능(미접촉)** |
| B10 | BLOCK 시 before==after checksum 불변 | `staging_apply` | High(actual write core) | c2 reason 유발 pack | **가능(미접촉)** |
| C2  | 미존재 node_id → {state:None, physical_present:False} | `tombstone` | High(state write 인접) | 빈 DB + 없는 node_id | **가능(미접촉)** |
| C3  | write_lock·snapshot·audit ALLOW 동반 | `tombstone` | High(state write 인접) | 정상 노드 + audit 조회 | **가능(미접촉)** |
| D3  | write_lock 같은 pid 재진입 허용 | `StagingDB.write_lock` | High(write 인프라) | 동일 프로세스 2회 진입 | **가능(미접촉)** |
| D9  | snapshot wal_checkpoint 후 copy 파일 생성 | `StagingDB.snapshot` | High(write 인프라) | write 후 snapshot 호출 | **가능(미접촉)** |
| D10 | store_checksum 결정성 | `StagingDB.store_checksum` | High(무결성 기준값) | 동일 상태 2회 호출 | **가능(미접촉)** |
| E3  | indices=[] → `empty_selection` | `save_selected` | High(G4① 인접) | ctx.confirm="SAVE " + indices=[] | **주입 필요(confirm 정합)** |
| E6  | A0 REVIEW & not allow_review → `a0_review_needs_explicit_allow` | `save_selected` | High(G4① 인접) | a0.classify_node monkeypatch(REVIEW 강제) | **주입 필요(monkeypatch)** |
| E7  | PII/secret 재스캔 hit → `pii_or_secret` | `save_selected` | High(G4① 인접) | PII 포함 문장 입력(또는 scan monkeypatch) | **주입 필요(PII fixture)** |

**판정 분포:** core 미접촉 관측 가능 **10** / 주입 필요(본체 무수정, 입력·monkeypatch만) **3** / 관측 곤란 **0**.

> "주입 필요" 3건도 **본체는 무수정**이다. E3는 confirm 문구를 빈 indices에 정합시키는 입력 구성, E6/E7은 외부 의존(`a0.classify_node`, `scan_residual_pii`)을 테스트 측에서 교체하거나 fixture 문장으로 분기를 타게 하는 것이며, `save_selected` 함수 자체는 건드리지 않는다.

---

## 2. GAP별 관측 방식 (구현 0 · 외부 호출+단언만)

각 항목: **호출 대상 / 입력 구성 / 부작용 주입 / 단언 / core 접촉 여부.**

### A4 — c2_check evidence.source_missing → freshness_source_missing
- **호출:** `c2_check(db, pack, ctx)`. db=temp `StagingDB`, ctx=`{"actor":"human"}`.
- **입력 구성:** `base_pack()` 복제 후 `pack["evidence"][0]["source_missing"]=True`(나머지 정상: evidence_refs 채움, source_hash==captured_hash, redaction_policy="v1").
- **주입:** 손상 pack(입력 데이터만 조작). ctx·DB 부작용 0.
- **단언:** 반환값 `== "freshness_source_missing"`. (본체 line 175 `if ev.get("source_missing")` 분기)
- **core 접촉:** 없음. `c2_check`는 순수 판정 함수 — 호출+반환 비교만.

### A6 — c2_check redaction_policy≠v1 → freshness_redaction_policy_changed
- **호출:** `c2_check(db, pack, ctx)`, ctx=`{"actor":"human"}`.
- **입력 구성:** `base_pack()` 복제 후 `pack["evidence"][0]["redaction_policy"]="v2"`. source_missing=False, source_hash==captured_hash로 두어 앞선 freshness 분기(source_missing·hash_mismatch)를 통과시켜 **redaction 분기까지 도달**시킨다.
- **주입:** 손상 pack.
- **단언:** 반환 `== "freshness_redaction_policy_changed"`. (본체 line 177, `CUR_REDACTION_POLICY="v1"` 대조)
- **core 접촉:** 없음.

### A10 — c2_check 판정 순서 고정
- **호출:** `c2_check(db, pack, ctx)`를 **순서를 검증하도록 다중 위반 pack**으로 반복 호출.
- **입력 구성(우선순위 증명 조합):**
  1. `ctx.actor="auto"` + evidence_refs 빈값 + freshness 위반 동시 → 반환 `"G4_no_auto"`(actor가 1순위).
  2. ctx.actor="human" + evidence_refs 빈값 + freshness 위반 동시 → `"evidence_refs_missing"`(actor 통과 후 evidence_refs가 2순위).
  3. human + evidence_refs 정상 + source_missing=True + duplicate 조건 동시 → `"freshness_source_missing"`(freshness가 duplicate보다 앞).
  4. human + evidence_refs 정상 + freshness 정상 + applied_registry에 동일(pack_id,content_hash) 선삽입 + ctx.backup_fail=True → `"duplicate_already_applied"`(duplicate가 backup보다 앞).
- **주입:** ctx 플래그 + 손상 pack + applied_registry 선삽입(temp DB 직접 INSERT — 본체 외부에서). backup은 ctx.backup_fail.
- **단언:** 위 4조합 각각 기대 reason 일치 → 순서 actor→evidence_refs→freshness→duplicate→backup 고정 증명.
- **core 접촉:** 없음. `c2_check` 반환값 비교만. duplicate 조건의 registry 행은 테스트가 외부에서 INSERT(본체 무수정).

### B9 — staging_apply snapshot 동반(파일 존재)
- **호출:** `staging_apply(db, base_pack(), {"actor":"human"}, snap_dir)`.
- **입력 구성:** 정상 pack(부작용 0) → applied=True 경로.
- **주입:** 없음(정상 경로 관측).
- **단언:** 반환 `r["applied"] is True` **그리고** `os.path.exists(r["snapshot"])`(반환된 snapshot 경로의 파일이 실제 존재). 추가로 `r["snapshot"]`이 snap_dir 하위 경로임을 단언.
- **core 접촉:** 없음. `staging_apply` 정상 호출 후 **반환 dict의 snapshot 경로를 외부에서 os.path.exists로 검사**할 뿐. 본체는 이미 line 196에서 snapshot을 생성·반환한다.

### B10 — staging_apply BLOCK 시 before==after checksum 불변
- **호출:** c2 reason을 유발하는 pack으로 `staging_apply` 호출(예: backup_fail=True → backup_create_failed, 또는 evidence_refs 빈값 → evidence_refs_missing).
- **입력 구성:** BLOCK 유발 pack/ctx. write 전에 `db.store_checksum()`을 외부에서 1회 호출해 before 저장.
- **주입:** ctx.backup_fail=True 또는 손상 pack.
- **단언:**
  1. 반환 `applied is False`.
  2. **호출 후** 외부에서 다시 `db.store_checksum()` → before와 동일(write 0).
  3. 최신 audit_log 행의 `before_hash == after_hash`(본체 line 192 `before, before` 동일값 기록). audit 행은 외부 SELECT로 조회.
- **core 접촉:** 없음. store_checksum/audit_log는 외부에서 호출·SELECT.

### C2 — tombstone 미존재 node_id → {state:None, physical_present:False}
- **호출:** `tombstone(db, "nonexistent_node", {"actor":"human"}, snap_dir)`. db=빈 temp StagingDB(노드 0개).
- **입력 구성:** 존재하지 않는 node_id.
- **주입:** 빈 DB(insert 없이 바로 tombstone 호출).
- **단언:** 반환 `== {"state": None, "physical_present": False}`(본체 line 238~240, UPDATE는 0행 영향, SELECT는 None).
- **core 접촉:** 없음. tombstone 반환 dict 비교만.

### C3 — tombstone write_lock·snapshot·audit ALLOW 동반
- **호출:** 먼저 정상 노드를 `staging_apply`로 1건 적재 → 그 node_id로 `tombstone` 호출.
- **입력 구성:** 존재 노드(staging_apply로 선적재) + 동일 node_id tombstone.
- **주입:** 없음(정상 경로).
- **단언:**
  1. 반환 `state=="tombstoned"` 且 `physical_present is True`.
  2. **snapshot 파일 생성:** tombstone 직전 snap_dir 파일 목록 대비 직후 `snap_t_*` 파일 1개 증가(외부 디렉터리 listdir 비교).
  3. **audit ALLOW 동반:** audit_log에서 action="tombstone" 且 result="ALLOW" 행 1개(외부 SELECT). reason은 None.
  4. write_lock: tombstone 완료 후 `db.path + ".lock"` 파일이 잔존하지 않음(정상 해제 확인 — 외부 os.path.exists).
- **core 접촉:** 없음. snapshot 파일·audit 행·lock 파일을 전부 외부에서 관측.

### D3 — write_lock 같은 pid 재진입 허용
- **호출:** `StagingDB.write_lock()` 컨텍스트를 **같은 프로세스에서 연달아 진입**.
- **입력 구성:** 두 가지 관측 경로:
  1. lock 파일에 현재 pid(`str(os.getpid())`)를 외부에서 미리 기록 → `with db.write_lock(): ...` 진입이 `RuntimeError` 없이 통과(line 107 `if pid != str(os.getpid())` 분기에서 같은 pid는 허용).
  2. (보조) 같은 프로세스에서 staging_apply를 2회 연속 호출 → 두 번째도 lock 에러 없이 진행(owner=False 분기로 들어가지만 같은 pid라 통과).
- **주입:** lock 파일을 외부에서 현재 pid로 선기록.
- **단언:** `with db.write_lock():` 블록이 예외 없이 실행됨(에러 0). 대조군으로 타 pid("999999") lock 시 RuntimeError 발생도 함께 단언(D4 기존 커버와 동일 골격, 여기선 같은 pid 허용만 명시).
- **core 접촉:** 없음. write_lock은 외부에서 직접 `with` 진입.

### D9 — snapshot wal_checkpoint 후 copy 파일 생성
- **호출:** `StagingDB.snapshot(snap_dir, name)`를 직접 호출.
- **입력 구성:** staging_apply로 노드 몇 건 적재(WAL에 쓰기 존재) → `db.snapshot(snap_dir, "snap_test")` 직접 호출.
- **주입:** 없음(정상 경로).
- **단언:**
  1. 반환된 경로 `== os.path.join(snap_dir, "snap_test")` 且 `os.path.exists()` True.
  2. 복사된 snapshot 파일을 별도 sqlite3.connect로 열어 nodes count가 원본과 동일(wal_checkpoint TRUNCATE가 WAL 잔존분을 main 파일로 합친 뒤 복사됐는지 확인). 외부에서 새 connection으로 검사.
- **core 접촉:** 없음. snapshot 직접 호출 + 결과 파일 외부 검사.

### D10 — store_checksum 결정성
- **호출:** `StagingDB.store_checksum()`를 동일 상태에서 2회 호출.
- **입력 구성:** staging_apply로 동일 데이터 적재한 DB.
- **주입:** 없음.
- **단언:**
  1. 같은 상태에서 `store_checksum()` 2회 호출 결과 동일(결정성).
  2. (보조) 노드 1건 추가(외부 INSERT) 후 호출 → 값 변경(상태 민감성). 다시 그 행 삭제 후 호출 → 원래 값 복원(정렬 ORDER BY 1로 행 순서 무관 결정성).
- **core 접촉:** 없음. store_checksum 외부 호출. 상태 변경은 테스트가 외부 INSERT/DELETE로.

### E3 — save_selected indices=[] → empty_selection
- **호출:** `save_selected(db, text, [], ctx, snap_dir)`.
- **입력 구성:** `indices=[]`. **부작용 주입(confirm 정합):** 본체 line 70 `expected = "SAVE " + ",".join(str(i) for i in indices)` → indices=[]이면 `expected == "SAVE "`. 따라서 `ctx.confirm = "SAVE "`(끝 공백 포함)로 두어야 confirm_phrase_mismatch를 통과하고 **line 74 `if not indices` 분기**까지 도달한다. actor="human"으로 G4 통과.
- **주입:** ctx 구성(actor="human", confirm="SAVE "), indices=[].
- **단언:** 반환 `applied is False` 且 `reason == "empty_selection"`.
- **core 접촉:** 없음(본체 무수정). 단, **confirm 문구를 빈 indices에 정합**시키는 입력 구성이 필요하므로 "주입 필요"로 분류.

### E6 — save_selected A0 REVIEW & not allow_review → a0_review_needs_explicit_allow
- **호출:** `save_selected(db, text, [i], {"actor":"human","confirm":"SAVE i"}, snap_dir)`(allow_review 미설정).
- **입력 구성/주입:** 본체 line 96~102가 `a0.classify_node(...)["verdict"]`를 읽는다. REVIEW 분기를 결정적으로 타려면 **`a0.classify_node`를 테스트 측에서 monkeypatch**하여 `{"verdict":"REVIEW"}` 반환하도록 교체(import된 모듈 객체 `a0`의 속성 치환). 또는 자연적으로 REVIEW를 내는 fixture 문장을 찾을 수 있으면 그것으로 대체(결정성 우선이면 monkeypatch 권장).
- **단언:** 반환 `applied is False`(saved 0) 且 `rejected.get("a0_review_needs_explicit_allow") == 1`. allow_review=True를 주면 분기를 통과함도 대조 단언 가능.
- **core 접촉:** 없음. `save_selected` 본체 무수정. monkeypatch 대상은 외부 의존 모듈 `a0`이며 함수 호출 전 테스트가 교체하고 finally에서 원복.

### E7 — save_selected PII/secret 재스캔 hit → pii_or_secret
- **호출:** `save_selected(db, text, [i], {"actor":"human","confirm":"SAVE i"}, snap_dir)`.
- **입력 구성/주입:** 본체 line 105~107이 `scan_residual_pii(sent)` + `_PREVIEW_PII_EXTRA` + `v011.SECRET_PATTERNS`로 재스캔한다. 두 경로:
  1. **PII fixture 문장:** 종결형(A0 통과)이면서 PII 패턴(주민번호형/이메일/전화 등 `scan_residual_pii`·`_PREVIEW_PII_EXTRA`가 잡는 형태) 또는 secret 패턴을 포함하는 문장을 입력. 단 capture_preview가 그 문장을 후보로 올려야 하므로 fixture 검증 필요.
  2. (대안) `scan_residual_pii`를 monkeypatch하여 hit 1건 반환하도록 교체(결정성 보장).
- **단언:** 반환 `applied is False`(또는 nothing_to_save) 且 `rejected.get("pii_or_secret") == 1`.
- **core 접촉:** 없음. 본체 무수정. PII fixture 또는 외부 의존 함수 monkeypatch로 분기 유도.

---

## 3. core 미접촉 보증 (재확인)

- `staging_apply` / `save_selected` / `commit_selected` / `deprecate_g3` 4함수 본체는 **본 문서 어디에서도 수정·이동 대상이 아니다.** B9·B10·C2·C3·E3·E6·E7은 전부 이 본체들을 **외부에서 호출하고 반환·부작용을 단언**하는 방식이며, 본체 라인을 한 줄도 바꾸지 않는다.
- 부작용 주입은 (a) 본체가 *이미 읽는* ctx 플래그, (b) 손상 pack/조작 DB 행(테스트 외부 INSERT), (c) 외부 의존 모듈 monkeypatch(`a0`, `scan_residual_pii`) — 셋 중 하나로만. **본체에 새 ctx 키·새 분기·새 hook을 심지 않는다.**
- 전 GAP temp DB · `BINGGU_HOME` temp 격리 · `OPERATING_PATHS` mtime 불변 전제(선례 selftest와 동일).

---

## 4. 결론

- 13건 모두 **본체(actual write core) 무수정**으로 관측 가능. core 직접 접촉(본체 수정) 0건.
- **core 미접촉 관측 가능 10** (A4·A6·A10·B9·B10·C2·C3·D3·D9·D10) / **주입 필요 3** (E3=confirm 정합 입력, E6=a0 monkeypatch, E7=PII fixture/monkeypatch — 모두 본체 무수정) / **관측 곤란 0.**
- 본 문서는 design-only · 구현 0. **S4 implementation HOLD 유지.** actual write core(S4-6)는 마지막/영구 HOLD. 테스트 추가·owner 토큰 요청은 본 문서 범위 밖의 별도 단계다.
