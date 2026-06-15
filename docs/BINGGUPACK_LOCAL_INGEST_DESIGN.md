# BingguPack 로컬 역인제스트(Local Ingest) 설계 — 출구 교체

> owner 결정(2026-06-15): 빙구팩의 OpenCrab 출구를 **클라우드 업로드 → 로컬 역인제스트**로 전환.
> Cloud 업로드는 미구현/HOLD로 방치돼 있었고(plan·권한·cwd 블로커), 로컬 ingest는 실증으로 작동 확인됨(query score 1.000).

## 1. 핵심 전환

| 구간 | 기존(HOLD) | 신규(구현) |
|---|---|---|
| ZIP 빌더 | `cloud_pack_export` / `publish_p6` | **그대로 유지** (산출물 구조 = OpenCrab ingest 스키마 정합) |
| 출구 | OpenCrab Cloud 업로드 (미구현, `send_staged_bundle` NotImplementedError) | **로컬 `opencrab ingest`** (`localbinggu_ingest_executor.py`) |
| 검증 게이트 | upload preflight G1~G7 | G1~G6 재사용(품질·PII·source pointer는 로컬에도 동일 유효) |

ZIP은 이미 OpenCrab 적재용 산출물(`neo4j/opencrab_ingest.jsonl`, `graph/nodes.jsonl`, `graph/edges.jsonl`)을 담고 있다. "업로드"만 빠져 있었다.

## 2. 흐름

```
ledger active 노드
  → ZIP 빌드 (cloud_pack_export --build / publish_p6)
  → [preflight G1~G6: schema·source pointer·secret/PII·candidate·leak]
  → localbinggu_ingest_executor: ZIP 해제 → 검증 → opencrab ingest
  → 로컬 OpenCrab store(SQLite/ChromaDB/docs)에 적재 → query 검색
```

## 3. 안전 원칙 (4cli C 검토 반영)

- **로컬 ingest도 비가역 write다.** OpenCrab store에 영속 반영되므로 "로컬이라 안전" 금지.
  - `ingest_zip(execute=False)` 기본 = 해제+검증+명령 구성까지만(실행 0).
  - 실제 적재는 `execute=True` 명시 + 호출자(owner) GO 필요.
- **synthetic_fixture pack 실 적재 차단** — manifest `data_class=synthetic_fixture`면 `execute=True`라도 DRYRUN으로 강등(빌더 검증용 dry-run 산출물이 실 store 오염 금지). `--allow-synthetic`로만 우회.
- **cloud_upload=False, db_insert=False, 네트워크 전송 0** 불변. 오직 로컬 CLI 호출.
- **opencrab 실행 파일 비하드코딩** — `OPENCRAB_EXE` env 우선, 없으면 후보 경로/PATH 탐색. 못 찾으면 BLOCK.
- **zip slip 방어** — 절대경로·상위탈출(`..`) 엔트리 거부.

## 4. 모듈

- `scripts/localbinggu_ingest_executor.py` — 실행 엔진 + selftest 7종(`--selftest`).
  - `find_opencrab_exe()` / `extract_zip()` / `validate_extracted()` / `build_ingest_command()` / `ingest_zip()`.
- 회귀 묶음 `binggu_publish_run_all_selftests.py` — 8/8 → **9/9**(local ingest 게이트 추가).

## 5. 사용

```bash
# 1) ZIP 빌드 (synthetic fixture 예시)
python scripts/binggu_cloud_pack_export.py --build

# 2) dry-run (기본 — 명령 구성만, 실행 0)
python scripts/localbinggu_ingest_executor.py <pack.zip>

# 3) 실제 적재 (실 데이터 ZIP + owner GO)
python scripts/localbinggu_ingest_executor.py <real_pack.zip> --execute
```

## 6. OpenCrab plan 안내 (역인제스트 가능 조건)

- **OpenCrab Cloud 역인제스트(ontology_ingest / Cloud 적재)는 Expert plan 전용** — Free/Pro plan에서는 역방향 인제스트 불가([[feedback_opencrab_cloud_plan_gate]] 정합).
- **본 구현(로컬 CLI `opencrab ingest`, LOCAL MODE)은 plan 무관** — 실측(2026-06-15): 실 ledger ZIP(real_active, node 9/edge 8) `--execute` 적재 시 plan 게이트 없이 성공(20/20, query score 1.000).
- 정리: 빙구팩 **로컬** 역인제스트는 plan 제약 없이 동작한다. 향후 **Cloud** 역인제스트로 확장할 때에만 Expert plan이 필요하다.

## 7. HOLD (이번 전환 범위 밖)

- OpenCrab **Cloud** 실 업로드 / Cloud 원본화 / marketplace / 팀·공유·과금 — 계속 HOLD.
- 폰 수집(hosted save-intent v2/mcp) — 별개 축(폰↔PC). 이번 작업 미수정.
- "OpenCrab cloud 제거"와 "모든 cloud 제거"는 다르다 — 폰 동기화 통로는 보존.
