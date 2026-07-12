# Local Fresh Index (LFI) — 정본 설계

BingguPack 1.20.x · 신규 모듈 `binggupack/pack/fresh_index.py` · 1단계(MVP 코어).

## 1. 배경 — 무엇이 느렸나 (실측)

기본 회상이 매 호출마다 전체 active 노드를 로드하고, semantic provider(Ollama bge-m3)가
켜져 있으면 회상마다 임베딩 probe 를 돌린다. 실제 사용자 config 는 `semantic_recall_enabled:
true` + Ollama 가동 상태라 기본 경로가 semantic 이다.

실측(운영 ledger 407 active 노드 복사본, 격리 홈):

| 경로 | p50 | p95 |
|---|---|---|
| `_load_graph` 전체 스캔 | 10ms | 13ms |
| `why_search` 어휘 | 10ms | 13ms |
| `preflight_context` 어휘 | 22ms | 29ms |
| `why_search` **semantic(warm)** | ~450ms | **621ms** |
| `preflight` **semantic(warm)** | ~380ms | **427ms** |
| embed probe 1회(cold) | — | **1584ms** |

어휘 경로는 이미 빠르다. 병목은 semantic ON 기본 경로다 — 회상마다 `embed("점검", timeout=3)`
게이트(≈1.5s) + query embed. Ollama 지연/다운 시 최대 3초 hang → 데모/셀프테스트 timeout.

## 2. 설계 — 원본과 색인 분리

- 원본(ledger·기억·traj·md·config)은 **불변**. LFI 는 ledger 를 `mode=ro` 로만 읽는다.
- 파생 색인 `<BINGGU_HOME>/fresh_index.sqlite` — 독립 SQLite, 재생성 가능(삭제해도 rebuild).
- **ledger 스키마 미변경.** 색인은 승인 권한이 아니라 읽기 성능용 파생 데이터.

### 색인 스키마 (fresh_index.sqlite)
- `index_meta(key,value)` — last_update_ts, ledger_node_count, ledger_path, schema_version
- `hot_items(item_id PK, kind, source_id, project_id, node_type, file_kind, size, mtime,
  content_hash, title, summary, created_at, indexed_at, last_seen_at, state, trust,
  owner_approved, pinned, use_count, rank_score)`
- `embed_vec(item_id PK, model, dim, vec)` — 인덱스 항목만(전 노드 아님)
- `pins(node_id PK, ts)` — owner 핀(영구 규칙 · ledger 불변)

### 증분 갱신 (daemon 0)
`index_update(ledger, home)`: content_hash 서명 diff — 신규/변경 노드만 upsert, 사라진 노드
`deleted` 표시, `deprecated` 반영. title/summary 는 **저장 전 redaction**(§4). 변경 항목만
rank_score 재계산. 후킹 지점(기존 write 경로): `cmd_save`/`cmd_pair`/`cmd_replace`/`cmd_deprecate`.

### Hot 랭킹
`rank_score = w_fresh·freshness + w_trust·trust + w_util·utility + (pin_boost if pinned)`.
- freshness/utility 는 기존 `p1_ranking` 재사용. trust = candidate=0(owner-sealed) → 1.0,
  candidate=1 → 0.5, owner_acceptance 있으면 +0.2. (전 노드 use_count=0 인 실데이터라
  freshness/trust/pin 중심.)

## 3. 3단 조회

- **Hot(기본)** `binggu recall "<q>"` — 색인만 읽는다. `why_search` 와 **동일한** substring
  relevance 1차 + rank_score 2차 → 중복제거 top 5. 전체 ledger 스캔 0 · 노드 전수 embed 0 ·
  provider hang 0. semantic 은 opt-in(top-K 후보만 저장 vec 로 재랭킹 · query 1회 짧은 timeout ·
  circuit breaker · 실패 즉시 어휘 폴백).
- **Warm** `binggu recall "<q>" --project <P>` — project 스코프 확장(색인 내).
- **Deep** `binggu recall "<q>" --deep` — 원본 전체 `why_search`(느리지만 넓음). Hot 이 부족해도
  자동 Deep 승격 0 — 안내만.

## 4. 안전 경계

- 색인 = read-only 파생. mutation 경로 신설 0. owner approval/save/replace/deprecate 불변.
- `title`/`summary` 저장 전 `batch_m1.batch_redact`(시크릿+PII 스팬 → `[REDACTED:N]`) 후
  독립 검증 `leak_guard`(scan_residual_pii+SECRET) — 잔존 시 보수적 blank. 검증자≠피검증자.
- `binggu home` 의 색인 상태 표시는 `peek()`(mode=ro · 생성/write 0)로 원본·색인 미변경.

## 5. CLI

- `binggu index status [--json]` — 마지막 갱신·색인 항목수·변경/제거 대기·상태
- `binggu index update` — 변경분만 증분 반영
- `binggu index rebuild` — 전체 재생성(손상 복구 포함 · 핀 보존)
- `binggu index pin|unpin <node_id>` — 영구 규칙 고정/해제(색인 레벨 · ledger 불변)
- `binggu recall "<q>" [--deep] [--project P] [--limit N] [--record]`
- `binggu home` — 색인 최신/갱신필요 표시

## 6. 성능 (전후 실측, 동일 407-node 운영 ledger 복사본)

| 항목 | 전(기존 semantic 기본) | 후(LFI Hot) |
|---|---|---|
| 기본 회상 p95 | **621ms** | **2.3ms** (≈265×) |
| provider 다운 시 | 최대 3s hang | 2.3ms(무지연) |
| 변경없는 색인 확인 | — | p95 9.3ms (<100ms) |
| 파일 1개 변경 반영 | — | 6.7ms (<300ms) |
| 기본 회상 원본 스캔 | 전체 스캔 | **0**(색인만) |
| 기본 회상 노드 임베딩 | 전수 | **0** |
| 관련성(top5, full-sentence relevance) | mean 0.367 | mean **0.367**(회귀 0) |

## 7. 테스트

`tests/test_fresh_index.py` (pytest, 14 케이스) + 모듈 `--selftest`(GATE=GO):
최초 색인 · 변경없음 · 추가/수정/삭제 · 저장/교체/폐기 · 프로젝트 스코프 · pinned 보존 ·
최근/고신뢰 우선순위 · 중복제거 · Hot/Warm/Deep 경계 · 원본 전체스캔 방지 · query-time 전수
임베딩 방지 · provider timeout+lexical fallback · 색인 손상 후 rebuild · 중간종료 정합성 ·
PII/시크릿 미노출 · owner approval·mutation 무회귀. CI 매트릭스에 **Python 3.14**(windows+ubuntu)
추가 + 기존 3.10/3.12/3.13 무회귀.

## 8. 2단계(분리 · 이 PR 범위 밖)

- 로컬 markdown/traj 파일 포인터 인덱싱 — **명시 허용 경로만**(`fresh_index.allowed_paths` config,
  기본 빈=owner 옵트인). mtime/size/hash 로 이동/삭제 반영. 원문은 Deep 요청 전까지 미로드.
- 기본 회상 경로(preflight hook · MCP 도구)의 Hot cutover — shadow 소킹 후.
- 세션 종료(session_close) 후킹으로 증분 갱신 지점 추가.
- (선택) FTS5/BM25 가속 — 노드 수가 수천을 넘어 substring 스캔이 병목이 될 때.
