# BingguPack "PC-mediated read 공유" — 반자동 publish 파이프라인 설계

> 상태: **P1~P8 구현 완료 · 라이브 (v1.9.0)** — autopush 스케줄러가 SAVE→자동 KV 전송(이중게이트: 확정 변화 AND 사람 SAVE 기록), `setup-cloud --apply` 한 방 설치. 폰/claude.ai/ChatGPT 양쪽 실측 조회 확인.
> 결정: owner 명시 (2026-06-14) + 4cli 토론 REFINE(session 20260614_1330_publish_pipeline). 이하 본문은 설계 시점(P1) 기록 — 현행 운영 요약은 `../README.md` PC-mediated read 공유 섹션 참조.
> 트랙명: **PC-mediated read 공유** ("실시간 공유" 표현 금지)
> 박제: `feedback_binggupack_crossdevice_publish_pipeline`

## 0. 목적과 갭

**목적(owner)**: 로컬·모바일·웹앱 셋 + 모델 간 공유. 예) Claude Code 작업을 폰 ChatGPT에서 확인.

**현재 갭**: 읽기 공유는 됨(클라우드 read-only pack) / 쓰기 공유 안 됨 — 폰 SAVE는 PC 로컬 ledger에만 들어가고, 다른 기기에 보이려면 PC가 빌드+승인 후 올려야 함. 따라서 이 트랙은 **PC-mediated read 공유**: 원본은 PC, 다른 기기는 PC가 승인·게시한 read-only pack을 읽는다.

## 1. owner 명시 설계 단서 (절대 준수)

1. 원본은 계속 PC 로컬 `~/.binggupack` 유지.
2. 폰/Claude/ChatGPT는 클라우드 **read-only** pack을 본다.
3. PC `SAVE n` 확정분만 **publish queue**에 넣는다.
4. 자동 publish = **"빌드+검증+업로드 후보 생성"까지만**.
5. 실 cloud 갱신 = **owner 승인**만. 안전토큰 금지.
6. Cloud 원본화 = 별도 4cli/영구금지 재검토 전까지 **HOLD**.

## 2. 파이프라인 (5단계)

```
[원본] 로컬 ledger (~/.binggupack/ledger.sqlite)   ← 단일 진실, 쓰기 O
   │  ① SAVE n 확정 (사람 선택)
   ▼
[큐]   publish_queue (로컬 SQLite, 포인터만 + 멱등 잠금)
   │  ② 자동 빌드 (watcher_pack_builder_m0)   [P2]
   ▼
[빌드] pack 후보 (nodes/edges/evidence jsonl)
   │  ③ 자동 검증 (pack_validate + leak scan + 영구금지 22~27 hard fail)  [P2]
   ▼
[후보] 업로드 후보 ZIP (staging, opencrab-cloud-pack-v1)  ← 자동 멈춤 (단서4)
   │  ④ owner 승인: APPROVE <queue_id> <bundle_full_hash>  (안전토큰 금지, 단서5)
   ▼
[배포] cloud worker pack 갱신 (read-only)   [P2 — rollback 보존/live 확인 조건]
```

## 3. publish_queue 스키마 (로컬 SQLite, 신규)

| 컬럼 | 의미 |
|---|---|
| `queue_id` | PK (결정적 hash) |
| `node_id` | ledger 확정 노드 참조 (포인터 — 원본은 ledger) |
| `status` | 상태머신(§5) |
| `node_hash` | enqueue 시점 ledger node content hash |
| `evidence_hash` | 연결 evidence content hash (없으면 BLOCK) |
| `bundle_hash` | 후보 ZIP full sha256 (candidate_ready 이후) |
| `lock_owner` | 멱등/동시성 단일 잠금 토큰 (watcher 중복 차단) |
| `enqueued_at`/`built_at`/`approved_at` | 단계 타임스탬프 |
| `approved_by` | `owner_explicit` / null (안전토큰 컬럼 없음) |

- 큐는 **포인터만** — 원문은 ledger. 큐 삭제해도 원본 무손상.

## 4. hash 3중 검증 (변조 차단)

1. **node_hash**: enqueue 시 ledger node content hash 고정.
2. **evidence_hash**: 연결 evidence content hash. evidence 없으면 BLOCK(영구금지 27).
3. **bundle_hash**: 후보 ZIP full sha256. APPROVE에 묶고, 배포 직전 재계산 불일치 시 ABORT(P2).

- hash8은 사람 표시용만. pin·승인·비교는 전부 full sha256.
- 3중 비교: enqueue node_hash → build 재읽기 hash → deploy 직전 bundle hash.

## 5. 상태머신 (불법 전이 ABORT)

```
queued → building → candidate_ready → approved → deploying → deployed
                                                          ↘ aborted
       ↘ failed (검증 실패·미실행·증거 없음 = 전부 BLOCK→failed)
```

- **queue_id 단일 잠금**(lock_owner): watcher 중복 빌드·이중 deploy 차단(멱등성·동시성).
- 정의되지 않은 전이 = ABORT. 검증기 fail-closed: leak scan/validate가 에러·타임아웃·빈입력·미실행·증거파일 없음 → candidate_ready 금지, **무조건 failed/BLOCK**.

## 6. 자동/수동 경계 (단서 4·5)

| 단계 | 주체 | 게이트 |
|---|---|---|
| ① 큐 적재 | 사람 (SAVE n) | — |
| ② 빌드 | 자동 [P2] | 멱등 잠금 |
| ③ 검증+후보 ZIP | 자동 [P2] | 검증 실패/미실행/증거없음 → BLOCK |
| ④ 승인 | **owner 명시** | `APPROVE <queue_id> <bundle_full_hash>` (안전토큰 금지) |
| ⑤ cloud push | owner 별도 GO [P2] | 라이브 재배포 = 영구금지 4cli 선행 |

## 7. 영구금지 정합 ([[feedback_binggupack_permanent_guards]])

- 4(자동화): 자동은 후보까지만, 실 push owner 명시 게이트.
- 16(Cloud 우회 default-deny): ④ 통과 없이 cloud 무변경. **안전토큰 없음**.
- 1(비가역): cloud 배포 전 owner IRREVERSIBLE 유지.
- 22~27(빙구팩 고유): **pack builder가 hard fail로 강제** — supports_judgment 외 node-to-node verb·synthetic 실팩표시(24)·evidence 미연결(27) → 후보생성 단계 fail.

## 8. P1 확정 스펙 (이번 구현 범위 — owner 명시)

**범위 = 대기열 + 중복(멱등) 잠금 + selftest 까지만.**

포함:
- publish_queue 스키마 생성(temp SQLite) + enqueue(포인터·node_hash·evidence_hash).
- 상태머신 전이 함수 + 불법전이 ABORT + queue_id 단일 잠금(멱등/동시성).
- APPROVE 파싱: `APPROVE <queue_id> <bundle_full_hash>`만 허용(안전토큰 토큰 경로 부재 검증).
- hash 3중 자리 검증 로직(node_hash/evidence_hash/bundle_hash) — 순수함수.
- 검증기 실패·미실행·증거파일 없음 = 전부 BLOCK/failed로 귀결됨을 selftest로 입증.
- selftest GATE.

**제외(절대 금지)**:
- 실 ledger write 0 · cloud upload 0 · DB insert(운영) 0 · capture_enabled 재활성 0.
- 빌드/검증기 실제 연결(②③)·배포 로직(⑤) = **P1에서 구현 안 함**(스텁/인터페이스만).
- rollback 보존 / 배포 후 live 확인 = **P2 조건으로 문서에만 명시**(아래).

## 9. P2 조건 (문서 명시만 — P1 구현 금지)

- ② watcher_pack_builder_m0 연결 + ③ pack_validate/leak scan/영구금지 22~27 hard fail 실연결.
- ⑤ 배포: 배포 직전 manifest full hash 재계산 ABORT · **직전 pack 보존+복원 검증(rollback)** · 배포 후 **Cloudflare live selftest**(로컬 GO≠live GO) · 실패 시 이전 worker/pack rollback.
- 트랙 분리: "PC-mediated read 공유"(현 트랙) vs "양방향 sync"(Cloud 원본화, HOLD 조건부 재검토) — 별도 트랙.

## 10. 구현 완료 현황 (2026-06-14, P1~P8 · 기준 09a35a4)

| 단계 | 커밋 | 모듈 | 내용 |
|---|---|---|---|
| P1 | 7d1b875 | `binggu_publish_queue_p1.py` | publish_queue·멱등 단일잠금·상태머신(불법전이 ABORT)·hash 3중(full sha256)·APPROVE(안전토큰 금지) |
| P2 | 2410272 | `binggu_publish_p2_pipeline.py` | 실 빌더(build_cloud_pack)·검증기 연결·영구금지 22~27 hard fail·배포 plan(deploy/rollback/live-check, executed=False)·fail-closed BLOCK |
| P3 | 3a14ff6 | `binggu_publish_p3_real_ledger.py` | 실 ledger read-only build 재검증. active 0 → NO_REAL_LEDGER_DATA(실데이터 안 꾸밈)·candidate 실데이터 취급 금지 |
| P4 | 24fab4d | `binggu_publish_p4_label.py` + `binggu_cloud_pack_export.py` | data_class 인자화(synthetic_fixture/real_candidate/real_active). release 자격 = real_active만 |
| P5/P7 | 42c6ae8 | `binggu_publish_p5_promote.py` | candidate→active 승격 정식 모듈. owner 명시만·idempotent(이미 active=skip)·evidence 1:1 정합 BLOCK·백업 필수·checksum+audit ALLOW |
| P6 | caf5181 | `binggu_publish_p6_opencrab_pack.py` | OpenCrab Desktop 기대 구조(OC12 스키마: space/canonical node_type/linkage closure/retrieval)로 pack 수리 + validator(6결함 검출) |
| P8 | 09a35a4 | `binggu_publish_run_all_selftests.py` | 회귀 묶음 러너(8 게이트, summary-fail) |

## 11. 운영 (Operations)

### 회귀 실행 (한 줄)
```
py scripts/binggu_publish_run_all_selftests.py
```
- 8 게이트(P1·P2·P3·P4·P5 promote·P6·cloud_pack export·tree scan) **summary-fail** 방식.
- 전부 실행 후 요약. `REGRESSION=GO` + exit 0 이면 통과. 하나라도 실패 시 `REGRESSION=FAIL` + exit 1.

### 금지선 (영구)
- **cloud upload / DB insert / tag·release / OpenCrab ingest / Cloud 원본화 금지.**
- 실 ledger write = owner 명시 `SAVE n`(저장) / `PROMOTE`(승격)만. 그 외 자동 write 0.
- 배포 실행 = owner **"이 ZIP 업로드 실행"** 문구 + OpenCrab plan/권한 확인 전까지 HOLD.

### OpenCrab Cloud 상태 (보류, 2026-06-14)
- Desktop ZIP validation = **PASS**
- local ingest = **NOT_FOUND** (opencrab_data 전수 — BingguPack 흔적 0)
- cloud ingest = **UNKNOWN** (AI 접근 경로 없음 — MCP cwd 블로커·CLI LOCAL MODE·Cloud=Desktop 로그인+Supabase 세션 전용)
- **Cloud 건 = 보류** (업로드/재인제스트/owned 확인/MCP·CLI 업로드 전부 중단)

### 기준 ZIP (보존)
```
C:\Users\PC\.binggupack\_opencrab_repaired\binggupack_opencrab_pack_v1.zip
```
- 구조: documents=1 · nodes=9 · edges=8 · format=opencrab-cloud-pack-v1 · release_ready
- bundle_hash: `5f9158cd3b4a9d4bffe0eac36d3eeaf0fa1895ce43b1b867f0f754f1d9b0470a`
- Cloud 업로드는 owner가 OpenCrab Desktop 화면에서 직접(AI/MCP 자동 업로드 금지).
