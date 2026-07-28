# 2단계(절단) 설계 — 긴 발화를 버리지 않고 저장한다

**상태**: 설계 확정 (owner 결정 4건 반영 · 2026-07-28) · **구현 착수는 별도 GO**
**계보**: Unit J 설계서(v2 후속) → 적대검토 3건 → 실측 판정 → 본 문서가 정본. 이전 초안은 본 문서가 supersede.
**정본 참조**: `docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md` §0(지식은 안 버린다)·§2·§5(hosted payload)·§6 ·
`scripts/openbinggu_conversation_capture_preview.py:27-31`(owner 2026-06-15 정체성 박제)

---

## 0. 문제와 결론

**문제**: 지금은 마침표 없이 길게 쓴 판단(1000자 초과)이 **통째로 버려진다**. 100자를 잘라내야만 저장이 된다(실측 F1).

**결론 4줄**

1. 절단을 없애는 방법은 "상한을 떼는 것"이 아니라 **제외분을 별도 차선(L-lane)으로 옮기는 것**이다. 주 후보 목록의 계산은 한 줄도 바꾸지 않는다 → owner 2026-06-15 방어(`over_max_sentence` 제외)와 SAVE 번호축이 구조적으로 불변.
2. **실측으로 우선순위가 뒤집혔다.** `TEXT_CAP` 실피해 **0건**, `AI_CONTEXT_CAP` 실피해 **20/21건(95.2%)**. "맥락 소실"의 1위 원인은 `TEXT_CAP` 이 아니었다.
3. **`over_max_sentence` 는 덩어리 방어가 아니다** — 덩어리 방어의 실체는 capture 단계 `_is_bulk_text`. 따라서 v2 의 "줄바꿈 밀도 결합" 개정(dead branch)은 **폐기**.
4. **차단·거부 신설 0.** 유일한 build 실패는 팩 축(스펙 §5:64 가 명령한 것)뿐.

### owner 결정 (2026-07-28)

| # | 질문 | 결정 | 설계 반영 |
|---|---|---|---|
| 1 | 4000자 초과 L 항목 표시 | **통째로 살린다** (앞뒤 표시 + 전문 저장) | §2 확정 |
| 2 | 붙여넣기 덩어리 원문 보관 | **보관하지 않는다 — 현행대로 버린다** | **S2-4 삭제** · §4 `BULK_*` 처분 = 현행 유지 |
| 3 | 보관 TTL | **7일 유지** | L-lane 전문에 적용 |
| 4 | 덩어리 꺼내는 방법 | (2번 부결로 **무효**) | — |

> **결정 2의 파급**: `bulk_deferred` 단계 전체가 없어진다. 적대검토 지적③("복귀 경로 없음")은 **단계 소멸로 자동 해소**. 덩어리는 지금처럼 capture 단계에서 걸러지고 길이만 기록된다(`bulk_vetoes`).

---

## 1. 적대검토 지적 3건 — 실측 판정

| # | 지적 | 판정 | 근거(실측) |
|---|---|---|---|
| ① | `gate_record_from_prompt` 가 파일표에 없어 기능 전체 dead | **✅ 유효(치명)** | `gate_log.py:315` 가 `pv.get("items")` 만 조회 |
| ② | py↔ts 골든 전제 거짓 | **❌ 오탐 — 기각** | ci.yml·package.json·ts 파일 전부 전제대로 실재 |
| ③ | `bulk_deferred` 복귀 경로 없음 | **✅ 유효 → owner 결정 2로 단계 소멸** | `bulk_vetoes` 에 원문 컬럼 부재 · 저장 토큰 없음 |

**②를 실측 없이 반영했으면 성립하는 전제를 뜯어고칠 뻔했다.** 적대검토도 틀린다 — 지적은 실측으로 검증한 뒤 반영한다.

### ① 상세 — 왜 치명인가

```python
# binggupack/safety/gate_log.py:315
by_idx = {r.get("idx"): r.get("sh") for r in pv.get("items", [])}
matched = [i for i in idxs if by_idx.get(i)]
if not hashes: return 0
```

L 항목은 G-3 에 따라 앵커 JSON 의 **신규 키 `long_items`** 에 들어간다. 이 함수는 `items` 만 본다 →
`matched=[]` → **`return 0`**. `parse_save_indices` 를 확장해 `["L1"]` 을 반환시켜도 **앵커가 한 줄도 안 써진다.**
저장측은 없는 앵커를 조회하므로 항상 False → `SAVE L1` **전체가 dead**. `return 0` 은 조용한 실패라 **무증상** —
7/19 "히트 H1" 사고(렌더는 되는데 파서/기록이 갈려 화면대로 쳐도 증발)와 같은 계열.

### ② 상세 — 전제는 전부 참

| 전제 | 실측 | 위치 |
|---|---|---|
| CI 가 node 세팅 | ✅ `actions/setup-node@v7` · `node-version: "22"` | `.github/workflows/ci.yml:21,23` |
| CI 가 npm ci | ✅ | `ci.yml:26,30` |
| typescript devDependency | ✅ `"typescript": "^7.0.2"` | `hosted/workers/package.json:14` |
| ts 가 node 단독 실행 가능 | ✅ **import 0 · Workers API 참조 0 · 순수 함수** `capturePreview(text, maxCandidates)` | `hosted/workers/src/capture_preview.ts:125` |

착수 시 실무 확인 2건(전제 붕괴 아님): `tsconfig.parity.json` 신설 · 모듈 형식(ESM/CJS)은 **실행해 확정**하고 추측으로 박지 않는다.

---

## 2. 설계 원칙 — "본 것 = 저장된 것" 을 깨지 않고 긴 발화를 살린다

`capture_preview` 의 `over_max_sentence` 분기를 **제외 → 라우팅**으로 바꾼다. 제외 자체는 유지한다.

```
현행:  len(sent) > 1000  →  excl("over_max_sentence");  continue
설계:  len(sent) > 1000  →  excl("over_max_sentence");  long.append(sent);  continue
```

분기 지점(`:125`)이 PII(`:130`)·secret(`:135`)·classify(`:142`)·dedup(`:149`)·정원(`:154`) **전부보다 앞**이라,
여기서 갈라내면 주 목록 계산은 **바이트 단위로 불변**이다(정원도 안 먹고, `seen` 도 안 건드리고, 번호도 안 민다).

**표시 규칙 (owner 결정 1 = 통째로)**

| 문장 길이 | preview 표시 | 저장 |
|---|---|---|
| ≤ 1000 (주 목록) | 전문 (현행 그대로) | 전문 |
| 1000 < n ≤ `L_FULL_SHOW`(4000) | **전문** (L 섹션) | 전문 |
| n > 4000 | 머리 800 + 꼬리 400 + `전문 N자 · sha:xxxxxxxx · 전문 보기: binggu capture --show L1` | **전문** |

**어떤 길이에서도 저장은 전문이다 — 자르지 않는다.** 4000자 초과에서만 표시가 축약되는데, 이때도 ① 저장될 정확한 글자수
② 전문 sha ③ 전문 열람 1명령을 **표시 안에** 둔다. "몰래 다른 걸 저장"이 아니라 "명시된 축약 + 즉시 검증 수단"이라
2026-06-15 원칙(표시↔저장 무음 괴리 금지)의 취지가 유지되고, hosted 응답 캡(20000/36000)과도 충돌하지 않는다.

> **owner 판단 근거(2026-07-28)**: 한 호흡에 쓴 판단은 하나의 생각이므로 잘라 여러 노드로 만들지 않는다
> (= 6/15 "사고 절단 금지" 박제와 정합).

---

## 3. SAVE 번호축 불변 — 4중 구조 보장

| # | 보장 | 근거 | 강제 방법 |
|---|---|---|---|
| **G-1** | 주 후보 목록 **바이트 불변** | 라우팅 분기가 정원/dedup/PII 검사보다 앞 | 골든 코퍼스(200건) 스냅샷 대조 — `candidates` + `excluded_counts` 완전 일치 |
| **G-2** | `INPUT_CAP` 제거는 **prefix-extension 만** | 20000자 컷이 `\n`(split 경계)에서 잘렸으므로 머리 부분 문장 분해가 동일 | `old == new[:len(old)]` assert. 예외(첫 20000자에 개행 사실상 없음 = `rfind("\n") <= 200`)는 이미 L-lane 행이라 주 목록 무영향 — 경계 케이스를 코퍼스에 명시 포함 |
| **G-3** | `preview_ref` 산식 **무변경** | `_preview_rows`/`preview_ref_for_rows` 는 주 후보만 본다 | L-lane 은 앵커 JSON 의 **신규 키** `long_items` + `lref` 로 분리. L 이 없으면 앵커 파일이 구버전과 byte 동일 |
| **G-4** | 축이 어긋나면 **오저장이 아니라 거절** | `pref` 는 후보 집합+순서의 sha256 | 플래그 ON preview → OFF 저장 시나리오 → `no_save_gate_ref`·saved 0 (7/20 idx축 오저장 계열 재발 차단) |

**L-lane 번호 네임스페이스**: `L1, L2, …` — 정수 축과 문자열 축이라 충돌 불가. 정원 `L_MAX = 5`,
초과분은 `long_overflow` 카운트로 표면화하되 **폐기 0**(버퍼 원문이 남아 재-preview 로 회수).

**7/19 "히트 H1" 사고 재발 차단**: 렌더 문자열과 파서 정규식이 갈리면 화면대로 쳐도 조용히 증발한다 →
`SAVE_TRIGGER_RE` 확장과 L 섹션 렌더를 **같은 상수**에서 파생시키고 왕복 테스트로 못박는다.

---

## 4. 캡 분류 + 처분

**분류 기준**: 잘려서 **노드로 영속되거나 후보 집합을 좁히면 저장측**, 사람이 읽는 문자열만 줄이면 표시측.

| 상수 | 위치 | 축 | 처분 | 실피해 |
|---|---|---|---|---|
| `AI_CONTEXT_CAP=400` | `binggu_capture_persist.py:25`·적용 `:332` | **저장측** | **제거**(전문 보관) | ★ **20/21 (95.2%)** |
| hook `cap=1500` | `hooks/binggu_capture_hook.py:37,63` | **저장측**(같은 사슬 상류) | **슬라이스 제거**. `tail=400`(스캔 줄 수)은 절단 아님 → 유지·상수화 | 상동 |
| `TEXT_CAP=1000` | `:24`·적용 `:323-325` | **저장측** | **제거**(L-lane 선행 필수) | 창 내 0건(예방) |
| `INPUT_CAP=20000` | `capture_preview.py:24`·적용 `:107-110` | **저장측** (v2 의 '표시측' 분류는 **오분류**) | **truncation 제거**(전 구간 스캔). 상수는 `LONG_INPUT_NOTE_AT` 로 표시 알림 임계만 담당 | 창 내 0건 |
| `MAX_NODE_SENTENCE=1000` | `capture_preview.py:31`·적용 `:125-128` | **선별측(라우팅)** — 절단 아님 | **값·주 목록 동작 완전 불변**. 제외분의 **행선지만** 폐기→L-lane | (F1) |
| `BULK_*` veto | `:89-91`·`:134`·`:313-315` | 수집측 | **현행 유지 — 원문 보관하지 않는다** (owner 결정 2) | 3건 소실(수용) |
| `binggu_save_batch.py:53` `txt[:90]` | 배치 preview 표 | 표시측 | 유지 + `(전문 N자)` 마커 | — |
| `session_close.py:550/:596` | 마무리 표시 | 표시측 | 유지 + `…(전문 N자 저장)` 마커 1줄 | — |
| pack export `[:80]` ×2 | `binggu_cloud_pack_export.py:106,115` | 산출물측(팩) | **진짜 §5 위반** → §6 | — |

---

## 5. 파일별 변경표 (파일 소유 무중복)

전부 **`BINGGU_LONGSAVE_V1` 플래그 뒤**. OFF 가 기본이고, OFF 면 `long_candidates: []`(키는 **항상 존재** — 소비자 KeyError 회피).
※ 플래그 판정은 **호출 시점 평가**(import 시점 금지 — MCP 는 장수 프로세스라 import-time 이면 영원히 옛 값).

### S2-1 — L-lane 골격 (표시·라우팅만)

| 파일 | 변경 |
|---|---|
| `scripts/openbinggu_conversation_capture_preview.py` | `:125-128` 분기에 `long.append(...)` 1줄(제외·카운트 유지) · `L_FULL_SHOW=4000`·`L_MAX=5` 신설 · 반환에 `long_candidates` · `preview_markdown` 에 L 섹션 · `--write-golden` 서브커맨드 · selftest 신규 3 (**기존 15케이스 무수정**) |
| `hosted/workers/src/capture_preview.ts` | `:146` 동일 이식 (이 파일을 소유하지 않으면 cross-client 경로가 갈린다) |

**L 후보 행 스키마**: `{label:"L1", sentence(전문), length, sha, blob_suspect, label_kind, a0_verdict, capture_reason}`.
`blob_suspect` = **분리 전 원본 축**에서 계산(`_is_bulk_text(raw)` + 종결어미 부재·특수문자 비율 보조신호).
**임계는 S2-0 표본으로 실측 후 확정하고 추측값을 박지 않는다.** 어떤 경우에도 **폐기 0** — 라벨링·정렬 전용.

### S2-2 — 번호축 배선 (저장 가능화)

| 파일 | 변경 |
|---|---|
| `binggupack/safety/gate_log.py` | `SAVE_TRIGGER_RE`/`_expand_indices` 에 `L\d+` 토큰 추가(범위 `L1-L3` **미지원** — 오지정 위험) · `write_last_preview` 조건부 `long_items`/`lref` · `gate_human_for_long_ref()` 신설 · `_preview_rows`/`preview_ref_for_rows` **무수정**(G-3) |
| **★ `gate_log.py` `gate_record_from_prompt:299-330`** | **(적대검토 ① 해소 — 누락분)** ① 조회원을 `items` + `long_items` 통합으로 확장 ② 주 idx 는 기존 `{"pref","idxs"}`, L 라벨은 **별도 레코드** `{"lref","lidxs","ts","source"}` 로 append ③ 반환값 계약 유지 |
| `scripts/openbinggu_conversation_candidate_save.py` | `_pick_one_node` 가 `pick="L1"` 수용 · `prepare_selected` 가 L 토큰 수용 · `_gate_ref_ok` 의 `isinstance(i,int)` 필터를 int∪L 로 확장 · `PAIR_RELATIONS`/evidence 규약 불변 |
| `scripts/binggu_save_batch.py` | `save_candidates_batch` 가 main 순회 후 **long 순회**(`blob_suspect=False` 만 자동 포함 · True 는 명시 `SAVE L1` 전용) · `ai_ctx` 경로 `owner_pick` 을 "main #1 없으면 L1" 로 · `confirm` 문구 `PAIR owner:L1` 수용 · `render_batch_preview` 에 "SAVE 시 문장 n건(주 n·긴 m) 저장" 사전 고지 |

> **축 분리를 강제하는 이유**: `pref` 는 주 후보 집합+순서의 sha256 이다. L 을 같은 레코드에 섞으면 주 목록이 안 변했는데도
> pref 해석이 갈릴 여지가 생겨 G-1/G-3 이 흔들린다. `lref` 는 `long_items` 만의 독립 sha 로 두고 소비자도 분리한다.

### S2-3 — 저장측 캡 제거 (**S2-1·S2-2 통과가 선행 조건 · 동일 커밋**)

| 파일 | 변경 |
|---|---|
| `scripts/binggu_capture_persist.py` | `AI_CONTEXT_CAP` 제거(①) → `TEXT_CAP` 제거(②) · `render_preview` 장문 마커 · 인라인 T7/T7b/T30 재작성(**회귀 아니라 계약 변경**임을 커밋 메시지에 명시) |
| `hooks/binggu_capture_hook.py` | `_prev_assistant_text` 의 `t[:cap]` 슬라이스 제거, `tail` 유지 |
| `scripts/openbinggu_conversation_capture_preview.py` (S2-1 소유) | `:107-110` truncation 제거, `truncated` 키는 **유지하되 의미 전환**(절단됨 → 장문 알림) |
| `binggupack/review/session_close.py` | `:550` 마커 1줄 |

**내부 실행 순서는 실피해 순**: ① `AI_CONTEXT_CAP`+hook cap (20/21) → ② `TEXT_CAP` → ③ `INPUT_CAP`. 하위 단계마다 게이트 통과 후 다음으로.

### ~~S2-4 — bulk_deferred~~ **삭제 (owner 결정 2 — 덩어리는 현행대로 버린다)**

### S2-5 — 팩 export §5 정합 (독립·병렬) → §6
### S2-6 — py↔ts 골든 (S2-1 형태 확정 후) → §7

---

## 6. 팩 export 80자 절단 — 진짜 §5 위반 처리

현행 `:106` Evidence `text: txt[:80]`, `:115` Claim `text: p["sentence"][:80]`.
**비대칭이 핵심**: Evidence 는 전문이 `chunks[].text` 에 있어 회수 가능하지만, Claim 은 어떤 chunk 에도 없어
**80자 뒤가 팩에서 영구 소실**된다. 진짜 위반은 Claim 쪽 1곳.

1. `SHORT_LABEL_LEN = 80` 상수화. 두 노드 모두 `text`(현행 80 — 소비자 호환 위해 키·값 유지) + `short_label` + `label_truncated: true` + `full_ref: <chunk_id>` 추가. **필드 추가만, 기존 키 변경 0.**
2. Claim 노드마다 `chunks` 에 `{chunk_id:"CHUNK-CLAIM-<id>", doc_id:"DOC-CLAIM-1", claim_id, text:<전문>, candidate:true}` emit. **`ev_index` 에는 넣지 않는다**(§1 — Claim 은 증거가 아니다).
3. 빌드타임 게이트 `assert_no_lossy_labels(pack)`: `label_truncated` 전 노드에 대해 `full_ref` 가 실제 chunk 로 해소되고 `sha256(chunk.text)` 가 원본 문장 sha 와 일치 → 불일치 시 **빌드 실패**(§5:64 가 명령한 유일한 실패 경로).
4. `reports/pack_payload_budget.md` 산출(노드/엣지 수·평균 label 길이·예상 payload·20K view 캡 대비 30% 여유) — 게이트 포함.
5. **선행 필수 grep**: `hosted/workers/src/load_packs.ts` · `binggupack/studio/read_model.py` · conformance 소비자가 `chunks[].evidence_id` 부재를 견디는지 전수 확인. 못 견디면 별도 배열 `claim_chunks` 로 분리하는 대안으로 전환.

---

## 7. py↔ts 동기화 — 숫자 parity 로는 못 잡는다

현행 parity selftest 는 `INPUT_CAP`/`MAX_NODE_SENTENCE` **숫자만** 본다. 값이 같은 채 분기 로직만 갈리면 GO 로 통과한다.

| 층 | 내용 | 신규 파일 |
|---|---|---|
| ① **골든 왕복**(본체) | py 가 SSOT. `--write-golden` 이 코퍼스 N건 → `{input, candidates[sentence,label_kind,a0_verdict], excluded_counts, long_candidates[label,length,sha,blob_suspect], truncated}` **교집합 projection** 산출(py 전용 필드는 projection 제외, 그 사실을 골든의 `projection` 키에 명시). py·ts 테스트가 **같은 파일**을 읽어 대조 | `hosted/parity/capture_preview_golden.json` |
| ② **ts 실행 하네스** | `tsc -p tsconfig.parity.json` → `node` 로 `capturePreview()` 실행 후 골든 대조 | `hosted/workers/parity/capture_preview_parity.ts` · `tsconfig.parity.json` · `package.json` `"test:parity"` · `ci.yml` 스텝 1 |
| ③ **구조 backstop** | `over_max_sentence` 분기가 **양쪽 모두 L-lane append 를 동반**함을 regex assert + 골든 sha 를 두 소비자가 동일 참조함을 assert | `scripts/binggupack_constants_parity_selftest.py` rec 추가 |

**골든 코퍼스(≈200건)**: 합성 + 경계 케이스 — 1000자 정확·1001자·개행0 1110자·개행19 로그덩어리·20000자 경계(개행 有/無)·40000자·PII·secret·중복·정원 초과. **운영 원문은 넣지 않는다**(골든은 저장소에 커밋된다).
⚠ `build/lib/scripts/` 낡은 사본이 골든/parity 경로에 섞이지 않도록 BASE 경로 명시.

---

## 8. 단계 순서 + 게이트

```
S2-0  baseline 실측 (완료)                                    [운영 write 0]
   ↓
S2-1  L-lane 골격 (표시·라우팅)      ── 게이트 A
   ↓                                    ★ 여기 통과 전에는 어떤 캡도 떼지 않는다
S2-2  번호축 배선 (SAVE L1 가능)     ── 게이트 B   ★ gate_record_from_prompt 포함
   ↓
S2-3  저장측 캡 제거 ①AI_CONTEXT+hook ②TEXT_CAP ③INPUT_CAP  ── 게이트 C (동일 커밋)
```
독립·병렬: **S2-5**(팩 export) · **S2-6**(py↔ts 골든 — 골든 재생성은 S2-1 형태 확정 후).
**순서 이유**: S2-1/2 → S2-3 을 뒤집으면 (F1) 실측대로 1110자 발화가 **70% 보존 → 100% 소실**로 악화한다.

| 게이트 | 테스트 | 핵심 단언 |
|---|---|---|
| **A** | `test_preview_axis_invariance.py` | 코퍼스 200건에 대해 **OFF 출력 == 골든 == HEAD 출력**(byte). ON 에서도 `candidates`·`excluded_counts` 완전 일치, `long_candidates` 만 증가. 기존 preview selftest **15/15 무수정 PASS** |
| **A** | `test_long_lane_display.py` | 1110자 → L 섹션 **전문 표시**(byte 동일) / 5000자 → 머리·꼬리 + `전문 N자` + sha + `--show L1` 안내 **전부** 표시 |
| **B** ★앵커 | `test_long_utterance_savable.py` | (a) 1110자·개행0 → main 후보 **0** + `over_max_sentence==1`(**owner 6/15 방어 불변**) + `long_candidates` 1건이 입력과 **byte 동일** (b) 렌더 문자열 → `parse_save_indices` → `["L1"]` **(c) ★ 그 문자열 그대로 `gate_record_from_prompt` → 반환>0 AND gate 파일에 `lref` 레코드 실재 AND `gate_human_for_long_ref` True** (d) `save_paired(owner_pick="L1")` applied True (e) ledger `nodes.sentence` **== 입력 1110자 전문**(왕복 byte 동일·절단 0) |
| **B** | `test_save_number_axis_failclosed.py` | ON preview 앵커 → OFF 저장 → `no_save_gate_ref`·saved 0·**오저장 0**. 반대 방향 동일. 주 목록 `pref` 는 두 프로세스에서 동일값 |
| **C** | `test_input_cap_removed.py` | 40000자 입력 → 20000 이후 문장이 후보 또는 L-lane 에서 회수 가능. `old == new[:len(old)]` |
| **C** | `test_ai_context_full.py` | `prev_turn` 3000자 → `ai_context` **3000자 전문** · pair 저장 시 ai 노드 절단 0 · `ai_pick=1` 지시 대상이 캡 제거 전후 동일 |
| **C** | `test_text_cap_removed.py` | 1110자 feed → buffer `text` 1110자 · `truncated=False` · `src_sha` == 원문 sha |
| **S2-5** | `test_pack_no_lossy_label.py` | 전 Claim 전문이 chunk 로 sha 일치 회수 가능 · 회수 불가 주입 시 **빌드 실패** · `ev_index` 에 Claim chunk 미등장 |
| **S2-6** | `test_capture_preview_parity_golden.py` + `npm run test:parity` | py·ts 가 같은 골든에 동일 projection · ts 분기 형태 regex assert |

> **게이트 B (c) 가 이번 라운드의 핵심 신설**이다. 종전 설계는 `parse_save_indices` 까지만 단언했는데 그건 파서 단위테스트지
> 경로 테스트가 아니다. **파서→기록→판독→저장 전 사슬을 한 테스트가 관통**해야 GO — 2026-07-27 "E2E 가 실제 호출 경로를
> 안 타서 실사용 12건 전량 미반영" 재발 방지.

기존 배터리 병행: `binggupack-verify-battery`. selftest temp 홈 이름은 신설하고 **삽입 전 grep** 필수(home5·home7 충돌 2회 전례).

---

## 9. 위험 / 완화

| # | 위험 | 완화 |
|---|---|---|
| **J1** | L-lane 이 온톨로지 오염 통로 | 배치 자동 저장은 `blob_suspect=False` 만, True 는 **명시 `SAVE L1` 전용**. `_is_bulk_text` 실측 방어(43/43)를 **원본 축에서** 재사용 |
| **J2** | `blob_suspect` 임계 추측 오탐 | S2-0 표본으로 **실측 후 확정**. 확정 전에는 라벨만 달고 자동 포함 비활성 |
| **J3** | 전문 저장으로 buffer PII 잔존 표면 확대 | PII 게이트는 정규식이라 길이 무관(오히려 검출 표면 증가 = 안전 방향). 잔존 방어는 **TTL 7일 purge**(owner 결정 3 — 유지) |
| **J4** | hosted 응답 캡 초과 | 표시는 `L_FULL_SHOW` 축약 + 주 목록 현행 유지 → preview 총량 증가에 상한. 팩 축은 S2-5 budget 문서로 관리 |
| **J5** | 구 설치본이 `long_candidates` 를 모름 | **키 추가만**(구 소비자는 무시). OFF 에서도 키 **항상 존재**(`[]`) → KeyError 0. ts 는 S2-6 동시 반영 |
| **J6** | 세션 중간 배포로 기존 preview 앵커 무효화 | **오저장이 아니라 거절**(G-4). 배포는 세션 경계에서. 거절 문구에 "preview 를 다시 띄우세요" 명시 |
| **J7** | `build/lib/scripts/` 낡은 사본 혼입 | BASE 경로 명시 + `build/` 제외 assert |
| **J8** | 전역 §3-2 "원문 통째 출력 금지" ↔ 로컬 전문 저장 | ⚠ **규칙-상황 불일치 명시**(§12-8): §3-2 는 *출력* 규칙, 본 설계는 *로컬 저장*. 따르되 보고에 1줄 남김 |

### owner 저장 UX 가 바뀌는 지점 (4개 — 전부 사전 고지 대상)

1. preview 하단에 **"긴 발화 n건 — L1, L2"** 섹션이 새로 보인다. **주 번호(1,2,3…)는 그대로.**
2. **`SAVE L1`** 토큰이 생긴다. 화면 렌더 문구와 파서를 왕복 테스트로 못박는다.
3. 세션 중간 배포 시 이미 본 preview 의 `SAVE n` 이 **거절**될 수 있다(다른 문장이 저장되는 일은 없다).
4. 배치 저장 시 "문장 **n건(주 n · 긴 m)** 저장" 이 미리 뜬다 — 저장 건수가 늘어나는 경우가 있다.

---

## 10. 보고 (§12-4)

- **따른 정본**: `BINGGUPACK_GRAPH_GRAMMAR_SPEC.md` §0:14 · §2:26-29 · §5:61-64(**팩 축에만** 인용) · §6:71-78 · `capture_preview.py:27-31` owner 2026-06-15 정체성 박제 · owner 결정 4건(2026-07-28).
- **신구 충돌 / 정리 3건**
  1. v2 §3-2 개정 규칙(`sent.count("\n") >= BULK_NL_MIN`)과 §8 대조군 단언을 **본 문서가 supersede** — 실측(문장조각 개행 최대치 0 · 로그덩어리는 `over_max_sentence` 에 애초에 안 걸림) 근거. 규칙으로 채택 금지.
  2. v2 §4-4 의 `INPUT_CAP` '표시측' 분류를 **'저장측'으로 정정**.
  3. **적대검토 지적② 기각** — 실측으로 전제가 전부 참임을 확인. 무비판 수용했으면 성립하는 전제를 뜯어고칠 뻔했다.
- **owner 결정으로 삭제된 단계 1건**: S2-4(`bulk_deferred`) — 지적③도 함께 소멸.
- **⚠ 규칙-상황 불일치 1건**: 전역 §3-2(출력 규칙) ↔ 로컬 전문 저장 — 따르되 명시(J8).
- **산출**: 설계뿐. **코드 편집 0 · 운영 write 0 · DDL 0.**
