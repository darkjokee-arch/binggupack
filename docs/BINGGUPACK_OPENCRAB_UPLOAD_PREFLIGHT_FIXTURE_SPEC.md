# BingguPack — OpenCrab private upload preflight FIXTURE SPEC (2026-06-11)

> 상위 설계: `docs/BINGGUPACK_OPENCRAB_PRIVATE_UPLOAD_PREFLIGHT_DESIGN.md` (r2, G1~G7 명세).
> 본 문서 = 음성 22 + 양성 1 fixture를 **구현 가능한 수준**으로 구체화. 설계만 — 코드 0·OpenCrab 도구 호출 0.
> 실 업로드/apply는 본 스펙 범위 밖(별도 GO). 검증은 전부 temp(`tempfile.mkdtemp`) + temp SQLite.

## 0. 실측 근거 (정독 완료)

- `scripts/openbinggu_pack_validate.py` — `validate_pack(pack) -> {verdict, stops, reviews, notes}` (L54). REQUIRED_FIELDS 10종·HARD_FALSE_FLAGS 3종·REVIEW_ONLY 경계(cross_pack_tags+fuzzy, L121-124) 실측.
- `workers_port/realpack_gate.py` — PACK_FILES 5종(L45)·PII_PATTERNS 5종(L29-35)·LEAK_PATTERNS 4종(L38-43)·MAX_VIEW_CHARS=20000(L21)·pointer 수집 규약(L82-98: `evidence_index[].source_path` + `evidence_chunk[].evidence_meta.raw_pointer`).
- `scripts/openbinggu_incoming_to_staging.py` — SECRET_PATTERNS (L43~: vendor prefix `ghp_` 등·AKIA·credential 키워드+값·private key·bearer).
- `scripts/openbinggu_scope_envelope_dryrun.py` — `classify_source_pointer(value) -> 'clean'|'dirty'|'unknown'`(L261)·`publish_decision(items, publish_approved, regression_state)`(L178).
- 기존 fixture 컨벤션: `tests/fixtures/synthetic/seed_bid_domain/` — pack 5파일(JSONL), manifest `format_version: opencrab-pack-v1`, 합성 한국어 입찰 도메인 문장, 상대경로 source pointer(`seed/...`), `promotion_allowed: false` 전건, `candidate: true` 노드.

## 1. fixture 저장 컨벤션 — 디스크엔 위반 미보관 (scanner 자기검출 회피)

과거 교훈(공개 트리 파일은 scanner regex와 자기충돌 금지 — secret 키·값 포맷 출력 문구의 자기검출 2연발) 준수:

- **디스크 보관물 = clean base pack 1세트 + 케이스별 delta 명세 JSON만.** 위반 문자열(절대경로·PII 형식·secret 토큰·`_backup` 마커)은 디스크에 완성형으로 저장하지 않는다.
- **delta 명세의 위반 값은 조각 분할 저장**: `{"join": ["frag1", "frag2", ...]}` 형태. 분할 규칙 — *어떤 단일 조각도* SECRET_PATTERNS·PII_PATTERNS·LEAK_PATTERNS·`classify_source_pointer` dirty regex 에 단독 매치되지 않게 자른다(예: `["C:", "\\Users\\PC\\synthetic\\x.md"]`, `["ghp_", "SYNTHFIXTURE000001"]`, `["_bac", "kup"]`, `["123-45-", "67890"]`).
- **selftest 가 `tempfile.mkdtemp()` 안에 materialize**: base pack 복사 → delta 적용(join 조각 결합 포함) → 그 temp pack 으로 게이트 평가. temp 밖 FS write 0.
- fixture 루트: `tests/fixtures/synthetic/upload_preflight/`
  ```
  upload_preflight/
    base_pok/                  # §3 양성 pack 5파일 (전건 clean — 디스크 그대로 안전)
      manifest.json  nodes.jsonl  edges.jsonl  evidence_index.jsonl  evidence_chunk.jsonl
    cases/                     # 음성 22 delta 명세 (1케이스 1파일)
      N_G1a.json ... N_X1.json
  ```
- delta 명세 스키마(공통):
  ```json
  {
    "case_id": "N-G2a",
    "gate": "G2",
    "expected_reason_code": "G2_SRCPTR_DIRTY",
    "ops": [
      {"op": "set|del|append_line|del_key_all|pad",
       "file": "manifest.json|nodes.jsonl|...",
       "path": "json pointer 또는 line 셀렉터",
       "value": "plain string | {\"join\": [...]} | object"}
    ],
    "approval_input": null,
    "db_setup": null,
    "regression_state": null
  }
  ```
  G5 계열은 `db_setup`(temp SQLite 시드 명세), G7 계열은 `approval_input`/`regression_state` 필드로 표현(파일 조작 없음).

## 2. 음성 22 fixture 상세 표

기대 결과 공통: 해당 reason_code 포함 BLOCK + `fail_open=False` + 나머지 게이트도 평가되어 일괄 보고(설계 r2 판정 순서 규약). base = `base_pok` 복사본(temp).

| # | 게이트 | delta (synthetic 조작 — 실데이터 0) | 기대 reason_code |
|---|---|---|---|
| N-G1a | G1 | manifest에서 `pack_type` 키 `del` | `G1_SCHEMA_STOP` |
| N-G1b | G1 | manifest `promotion_allowed_default` → `true` | `G1_SCHEMA_STOP` |
| N-G1c | G1 | manifest에 `opencrab_ingest_allowed: true` 추가 (HARD_FALSE_FLAGS 위반 — stops 에 들어가나 별도 코드 승격) | `G1_HARD_FLAG_TRUE` |
| N-G1d | G1 | manifest `cross_pack_tags: ["synth_bid","synth_contract"]` + `merge_policy.cross_pack: "fuzzy"` (validate_pack L121-124 REVIEW_ONLY 유도, stops 0 유지) | `G1_REVIEW_ONLY` |
| N-G2a | G2 | evidence_index 1행 `source_path` → `{"join": ["C:", "\\Users\\PC\\synthetic\\x.md"]}` (dirty=_WIN_ABSPATH) | `G2_SRCPTR_DIRTY` |
| N-G2b | G2 | evidence_chunk 1행 `evidence_meta.raw_pointer` → `"MASK_UNDECIDED_TOKEN"` (_UNDECIDED_TOKENS → unknown) | `G2_SRCPTR_UNKNOWN` |
| N-G2c | G2 | `del_key_all`: evidence_index 전행 `source_path` 키 + evidence_chunk 전행 `evidence_meta.raw_pointer` 키 삭제 → 수집 pointer 0건 | `G2_SRCPTR_EMPTY_SET` |
| N-G3a | G3 | nodes.jsonl 1행 `properties.sentence` 에 합성 사업자번호 `{"join": ["123-45-", "67890"]}` 삽입 (pii_bizno_fmt) | `G3_PII_HIT` |
| N-G3b | G3 | evidence_chunk 1행 `text` 에 `{"join": ["synth@", "example.com"]}` 삽입 (pii_email) | `G3_PII_HIT` |
| N-G3c | G3 | nodes.jsonl 1행에 `{"join": ["ghp_", "SYNTHFIXTURE000001"]}` 삽입 — SECRET_PATTERNS vendor prefix 매치, 실키 아님(조각조합 규약) | `G3_SECRET_HIT` |
| N-G4a | G4 | evidence_chunk 에 `append_line`: `evidence_meta.source_kind: "conv_self_ephemeral"` chunk 1건 (text 합성 1문장·EVS-x9) | `G4_EPHEMERAL_INCLUDED` |
| N-G4b | G4 | evidence_chunk 1행 `evidence_meta.source_kind` 키 삭제 (제외판정 불가 = fail-closed) | `G4_EXCLUDE_FLAG_MISSING` |
| N-G5a | G5 | `db_setup`: temp SQLite 에 pack_id 행 3건 중 1건 `status='confirmed'` insert | `G5_NON_CANDIDATE_FOUND` |
| N-G5b | G5 | `db_setup`: temp SQLite 에 해당 pack_id 행 0건 (타 pack_id 행만 1건) | `G5_EMPTY_PACK` |
| N-G6a | G6 | nodes.jsonl 1행 sentence 에 `{"join": ["_bac", "kup"]}` 마커 삽입 — G3(SECRET/PII)엔 안 걸리고 LEAK_PATTERNS `backup_marker` 에만 걸림 | `G6_SERIALIZED_LEAK` |
| N-G6b | G6 | `pad`: nodes.jsonl 에 합성 문장("합성 패딩 문장 N" 반복) 노드 다수 append → `consume()` view 직렬화 > 20,000자 (cap=MAX_VIEW_CHARS) | `G6_VIEW_OVERSIZE` |
| N-G7a | G7 | `approval_input: "upload <pack_id> <hash8>"` (소문자 — strict 불일치) | `G7_CONFIRM_MISMATCH` |
| N-G7b | G7 | `approval_input: null` (미입력) | `NOT_APPROVED` |
| N-G7c | G7 | `approval_input: "UPLOAD other_synth_pack <hash8>"` (타 pack_id) | `G7_CONFIRM_MISMATCH` |
| N-G7d | G7 | 비가역 판정 상태(`irreversible=true` 주입) + `approval_input: "UPLOAD <pack_id> <hash8>"` (IRREVERSIBLE 누락) | `G7_CONFIRM_MISMATCH` |
| N-G7e | G7 | `regression_state: {"marketplace_enabled": true, ...}` 주입 → publish_decision R1~R3 위반 | `REGRESSION_FAIL` |
| N-X1 | 토큰 | P-OK 경로로 approval_token 1회 소모 후 동일 token 으로 2회째 시도 | `APPROVAL_TOKEN_CONSUMED` |

**게이트별 건수**: G1=4 · G2=3 · G3=3 · G4=2 · G5=2 · G6=2 · G7=5 · 토큰(X)=1 → **합 22**.

구현 주의 2건:
- **N-G2c 수집기 스펙**: `realpack_gate.gate_pack` 의 수집은 `.get("source_path", "")` 라 키 부재 시 `""`(=unknown) 이 됨. preflight 수집기는 **키 부재 row 를 skip** 하는 신규 수집기로 명세(어느 쪽이든 fail-closed BLOCK 이지만, `G2_SRCPTR_EMPTY_SET` 과 `G2_SRCPTR_UNKNOWN` reason 구분을 위해 skip 방식 채택).
- **N-G1c 코드 승격**: `validate_pack` 은 hard flag 위반을 stops 문자열로만 반환 → preflight 어댑터가 stops 내 `"hard-default 위반"` prefix 매치 시 `G1_HARD_FLAG_TRUE` 로 승격, 그 외 stops 는 `G1_SCHEMA_STOP`.

## 3. 양성 fixture P-OK 1세트 (`base_pok/`) — private pack 기준 충족 정의

`seed_bid_domain` 컨벤션 계승, 전 파일 디스크 보관 안전(위반 패턴 0).

| 파일 | 내용 정의 |
|---|---|
| `manifest.json` | `format_version: "opencrab-pack-v1"` · `pack_id: "upload_preflight_pok_v1"` · `pack_type: "candidate"` · `status: "validated"` · `visibility: "private"` · `scope: "domain:bid"` · `promotion_allowed_default: false` · `depends_on: []` · `cross_pack_tags: []` · `merge_policy: {mode: "review", target: "staging", cross_pack: "isolated"}` · `evidence_policy: {min_evidence: 1, source: "synthetic_fixture"}` · `risk_level: "low"` · `created_from: "upload_preflight_fixture_spec"` · `blocked_by_v09: true` · `counts: {nodes: 3, edges: 2, evidence: 3}` · HARD_FALSE_FLAGS 3종 미기재(또는 false) |
| `nodes.jsonl` | 합성 노드 3건(개념/판단/문서 — seed_bid_domain 문장 변형 합성 한국어), 전건 `properties.candidate: true`·`promotion_allowed: false`·`evidence_refs` 1+ |
| `edges.jsonl` | 합성 edge 2건 (`supports_judgment`·`depends_on`), 양끝 node id 실재 |
| `evidence_index.jsonl` | 3건, `source_path` 전건 상대경로 합성(`seed/synthetic_a.md` 형) = classify clean·`promotion_allowed: false` |
| `evidence_chunk.jsonl` | 3건, `evidence_meta`: `raw_pointer`(clean 상대경로)·`source_kind: "md"`·`redaction_applied: true`·`redaction_hits: 0` 전건 보유 — ephemeral/conv-self 표식 0건 |

**P-OK 충족 체크리스트(= 각 게이트 PASS 조건)**: ① G1 `validate_pack` verdict==PASS(REVIEW_ONLY 아님) ② G2 pointer ≥1 AND 전건 clean ③ G3 SECRET+PII hit 0 ④ G4 conv-self/ephemeral chunk 0 + `source_kind` 전건 존재 ⑤ G5 temp SQLite 시드(`db_setup`): pack_id 행 3건 전건 `status IN ('candidate','validated')`·confirmed/promotion 플래그 0 ⑥ G6 leak 0 + view ≤ 20,000자 ⑦ G7 정확 문구 + regression_state 정상(R1~R3 유지) → 기대: **전 게이트 PASS·publish ALLOW**.

## 4. owner 승인 문구 최종형 + 재해시 검증 2곳

**문구 최종형 (strict — 대소문자·단일 공백·전후 공백 0, 정확 일치)**

```
UPLOAD <pack_id> <bundle_hash8>                  # 가역 입증 시
UPLOAD <pack_id> <bundle_hash8> IRREVERSIBLE     # 비가역 판정(미입증 포함, fail-closed) 시
```

- 형식 regex(1차 거름) `^UPLOAD [A-Za-z0-9_\-]+ [0-9a-f]{8}( IRREVERSIBLE)?$` → 이후 기대 문자열과 **정확 일치 비교**(regex 만으로 PASS 금지).
- `bundle_hash8` 정의: staged bundle 의 `sha256( Σ (filename + b"\0" + raw bytes) , PACK_FILES 고정 순서 )` 앞 8 hex 소문자. 결정적(타임스탬프 미포함).
- 비가역 연동: read-only 실측으로 삭제/비공개 전환 경로가 **입증되지 않으면 = 비가역 간주** → IRREVERSIBLE 변형 의무 (설계 r2 §비가역성).

**재해시 검증 시점 2곳 (불일치 = 즉시 ABORT)**

1. **승인 직전 — staged bundle 생성 시**: G1~G6 통과 직후 직렬화 bundle 을 staged 로 고정하고 `bundle_hash8` 계산 → 승인 요청 화면에 표시. owner 가 이 hash 를 문구에 타이핑 = "이 바이트들"에 승인했음을 고정.
2. **전송 직전 — 재해시**: UPLOADING 진입 직전 staged bundle 재해시 → 승인 문구의 hash8 과 비교. **불일치 = `BUNDLE_HASH_MISMATCH_ABORT`** → ABORTED_PARTIAL 전이·token 소각·업로드 0 (승인~전송 사이 어떤 변경도 승인 무효).

토큰 1회성(N-X1)·sidecar(`UPLOAD_STATE.json`)·재시도=G1~G7 전체 재실행+새 승인 — 설계 r2 상태 전이 그대로.

## 5. selftest 골격 (구현 시 — 가칭 시그니처)

파일(예정): `scripts/openbinggu_upload_preflight.py` (`--selftest` 내장, realpack_gate/scope_envelope 패턴 계승)

```
GateResult = {"gate": str, "ok": bool, "reason_codes": [str], "counts": dict}   # raw 값 미포함

gate_g1_schema(manifest: dict) -> GateResult                      # validate_pack 어댑터(+코드 승격)
gate_g2_source_pointers(pack_payload: dict) -> GateResult         # 신규 수집기(키 부재 skip) + classify_source_pointer
gate_g3_secret_pii(raw_texts: dict[str,str]) -> GateResult        # SECRET_PATTERNS + PII_PATTERNS 루프 재사용
gate_g4_ephemeral(pack_payload: dict) -> GateResult               # 신규 check_ephemeral_excluded
gate_g5_candidate_only(db_path: str, pack_id: str) -> GateResult  # 신규 check_candidate_only (PRAGMA query_only=ON)
gate_g6_serialized_leak(pack_dir: Path) -> GateResult             # consume() view + LEAK_PATTERNS + 20K cap 재스캔
gate_g7_approval(approval_input, pack_id, bundle_hash8, irreversible, regression_state) -> GateResult
                                                                  # strict 문구 + publish_decision 재사용 + audit append

compute_bundle_hash8(pack_dir: Path) -> str                       # §4 정의
verify_rehash_before_send(staged_dir: Path, approved_hash8: str) -> {"ok", "reason_code"}
materialize_case(case_spec: dict, base_dir: Path, tmp_dir: Path) -> Path   # delta 적용(join 조각 결합)
preflight_run(pack_dir, db_path, approval_input, regression_state, irreversible=False)
    -> {"verdict": "ALLOW"|"BLOCK", "gates": [GateResult x7], "reason_codes": [...]}   # 첫 FAIL 후에도 전 게이트 평가
run_selftest() -> exit 0|1                                        # GATE: GO/STOP 출력 (기존 패턴)
```

**체크 수 추정 ≈ 35**: 음성 22(기대 reason_code 일치) + 양성 1(전 게이트 PASS·ALLOW) + 문구 단위검사 ~5(정상 2형·regex 거름·trailing space·이중 공백) + 재해시 2(일치 통과·변조 후 MISMATCH ABORT) + 토큰 상태전이 1(P-OK→소모→재사용 BLOCK은 N-X1 과 통합 가능) + 무해성 3(temp 외 FS write 0·temp SQLite 외 DB 미접촉·base_pok 5파일 sha 불변) + fail_open 검출 1.

**합격 기준**: 양성 1 ALLOW + 음성 22 전건 의도된 reason_code BLOCK + **fail_open 검출 0**(`run_source_pointer_checks` 패턴 — 의도적 위반이 PASS 로 새는 케이스 0) + temp(`tempfile.mkdtemp`) 외 FS write 0 + 운영 store(real staging DB 포함) mtime/sha 불변 + OpenCrab 도구 실호출 0(전부 mock).

## 6. 범위 재명시

- **실 업로드·apply·finalize·confirmed 생성 = 본 스펙 범위 밖. 별도 owner GO.**
- 본 스펙 단계 산출물 = 문서 1건. 코드·fixture 파일 생성도 다음 단계(구현 GO) 산출물.
- 검증 시 real staging DB(`tmp/real_staging`) 절대 미접촉 — G5 는 temp SQLite 전용. mcp__opencrab__* 호출 0 (read-only 실측조차 별도 GO).

## 7. 구현 시 예상 파일 목록

| 경로 | 성격 |
|---|---|
| `scripts/openbinggu_upload_preflight.py` | G1~G7 체인 + `--selftest` (신규) |
| `tests/fixtures/synthetic/upload_preflight/base_pok/` manifest.json·nodes.jsonl·edges.jsonl·evidence_index.jsonl·evidence_chunk.jsonl | 양성 base pack 5파일 (신규) |
| `tests/fixtures/synthetic/upload_preflight/cases/N_G1a.json` ~ `N_X1.json` | 음성 delta 명세 22파일 (신규) |
| `docs/BINGGUPACK_OPENCRAB_UPLOAD_PREFLIGHT_RESULT.md` | 구현 후 결과 보고 (신규) |
| (무수정 재사용) `scripts/openbinggu_pack_validate.py` · `scripts/openbinggu_scope_envelope_dryrun.py` · `scripts/openbinggu_incoming_to_staging.py` · `scripts/openbinggu_pack_consumer_smoke.py` · `workers_port/realpack_gate.py` 패턴 | 기존 모듈 호출만 |
