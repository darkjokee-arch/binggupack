# scripts/ 인덱스 (2026-06-13 v1.4.0 — AGI memory capture 스크립트 반영)

> **신규 기여자 길잡이 (5줄)**
> 1. 시작점은 `openbinggu_doctor.py --selftest` — 공개 전 필수 검사 단일 진입점(기존 selftest들을 subprocess로 호출만).
> 2. 모든 스크립트는 fail-closed·read-only/dry-run·candidate-only가 기본 — 운영 store write 0, confirmed/promotion 자동 생성 0.
> 3. "selftest" 열이 `--selftest`면 해당 플래그로, "자체"면 파일 실행 자체가 selftest/게이트(인자 불필요 또는 자체 절차).
> 4. 분류: **현행**=현재 라인에서 import되거나 게이트로 사용 / **1회성**=owner GO 1회 실연·canary(재실행은 별도 GO) / **레거시**=참조 0 실측, 이전 버전 라인 / **archived**=`_archived_oneoff_20260612/` 이동 대상.
> 5. raw 경로·secret·PII는 어떤 스크립트도 출력하지 않는다(id/hash/count/reason_code만) — 새 스크립트도 이 규약을 따를 것.

총 77개 (.py — scripts/ 직속 73 + archived 이동분 4 표기). 분류 근거 = 각 파일 머리 docstring + import 참조 관계 실측(`grep "import <모듈>"` 교차).

| 파일명 | 분류 | 한 줄 역할 | selftest |
| :--- | :--- | :--- | :--- |
| binggu_capture_buffer.py | 현행 | 메모리 candidate 버퍼 + preview 렌더(분류 호출, 영속 0) | 자체 |
| binggu_capture_classifier.py | 현행 | 발화→capture 판정(captured/preview_trigger/ignored·pinned·confidence) | 자체 |
| binggu_capture_cli.py | 현행 | capture 수동 호출 CLI(로컬, write 0) | --selftest |
| binggu_capture_persist.py | 현행 | 영속 candidate 버퍼 — 기본 OFF 플래그·scope 2중 게이트(global/deny)·TTL·rollback | 자체 |
| binggu_capture_profile.py | 현행 | AGI memory profile — init/status/pause/resume/uninstall + settings.json hook 등록(백업·idempotent) | 자체 |
| binggu_capture_session.py | 현행 | 세션 capture entrypoint(on_user_prompt/on_session_end, 메모리) | 자체 |
| binggu_capture_to_save.py | 현행 | capture→저장 게이트 어댑터 — commit_selected(save_selected 위임)·build_save_commands | --selftest |
| binggupack_constants_parity_selftest.py | 현행 | py↔ts 매직넘버 동기 검증(후보 상한 10·INPUT_CAP 20000 등 양쪽 regex 추출 대조) | 자체 |
| binggupack_http_mcp_skeleton.py | 현행 | hosted MCP 로컬 한정 PoC — 127.0.0.1 read-only 5 tool, JSON-only stateless | 별도(↓selftest 파일) |
| binggupack_http_mcp_skeleton_selftest.py | 현행 | skeleton 실 HTTP E2E 21케이스(initialize/tools/Origin 가드/누출 0) | 자체 |
| binggupack_sign_util.py | 현행 | save-intent HMAC 서명 단일 출처(method+path 바인딩, hosted save_common.ts 와 바이트 동일) | ✗(유틸 모듈) |
| localbinggu_incoming_loader.py | 현행 | incoming jsonl 읽어 schema/evidence/promotion 불변식 검증(read-only loader) | ✗(CLI 검증기, watcher가 재사용) |
| localbinggu_match_policy.py | 현행 | read-only match policy — Tier 0~3 분류, cross-domain fuzzy 오탐 차단 | ✗(__main__=draft 평가 출력) |
| localbinggu_review_resolver.py | 현행 | review decision → reviewed plan + audit 생성(production write 금지) | fixture 모드(--fixture-dir) |
| openbinggu_a0_node_dryrun.py | 현행 | 노드 정본(헌법 1조) validator — 핵심 문장·5종 label_kind 판정 | --selftest |
| openbinggu_batch_pack_loader.py | 현행 | batch pack → staging apply→read-back→rollback 일괄 검증 실행기 | --selftest |
| openbinggu_c2_guard_selftest.py | 현행 | C-2 단일통제 guard in-memory selftest 21케이스(자동검사 4종·rate limit) | 자체 |
| openbinggu_candidate_deprecate_ux.py | 현행 | 기각 UX — "DEPRECATE <n> <id8>" confirm + 실행 직전 재검증(node 한정) | --selftest |
| openbinggu_candidate_list_view.py | 현행 | candidate 목록 뷰(read-only) — status/kind 필터·도장·review 상태 표시 | --selftest |
| openbinggu_candidate_replace_ux.py | 현행 | replace transaction — 기각+신규 저장 묶음(in-place 수정 0, 보상 원복) | --selftest |
| openbinggu_confirmed_governance_dryrun.py | 현행 | confirmed governance validator — G4 status 전이/G6 멀티유저 충돌 판정 | --selftest |
| openbinggu_conversation_candidate_save.py | 현행 | 대화→candidate 저장 — preview 내부 재실행 + "SAVE i,j" confirm 게이트 | --selftest |
| openbinggu_conversation_capture_preview.py | 현행 | 대화 capture 미리보기(순수 함수, write 0·PII 문장 후보 제외) | --selftest |
| openbinggu_deprecate_and_remind_g3.py | 현행 | G3 기각 도장(보존+기본조회 제외) staging 연동 + 검증 리마인드 | --selftest |
| openbinggu_doctor.py | 현행 | 공개 전 필수 검사 단일 진입점 — 기존 selftest 묶음 subprocess 오케스트레이션 | --selftest |
| openbinggu_finalize_dryrun.py | 현행 | OpenCrab Pack v1 finalize dry-run 생성기(로컬 조립만, 업로드 0) | --selftest |
| openbinggu_incoming_to_staging.py | 현행 | incoming→staging loader dry-run — contract+secret+risk 정책, plan만 생성 | --selftest |
| openbinggu_label_kind_map.py | 현행 | label_kind 한영 매핑 단일 정본 + deterministic 5종 분류기(정규식) | --selftest |
| openbinggu_mcp_path_gate_adapter.py | 현행 | MCP 도구 path 입력 가드 — 실행 직전 classify_path, BLOCK 시 미호출 | --selftest |
| openbinggu_mcp_server.py | 현행 | OpenBinggu local MCP 서버(stdio JSON-RPC) — read/dry-run 5 tool만 노출 | --selftest |
| openbinggu_mcp_server_handlers.py | 현행 | MCP 도구 핸들러 결선 — enforce_access+path gate 2중 통과 시만 underlying 호출 | --selftest |
| openbinggu_owner_accept_ux.py | 현행 | owner_accepted UX — record-only event 테이블 append("ACCEPT/UNACCEPT <n> <id8>") | --selftest |
| openbinggu_pack_consumer_smoke.py | 현행 | 로컬 pack consumer smoke — 모델 중립 소비 contract 기준 sanitize view 생성 | --selftest |
| openbinggu_pack_review_e2e.py | 현행 | pack→review queue e2e dry-run — builder→bridge→resolver 연결 검증 | --selftest |
| openbinggu_pack_validate.py | 현행 | 최소 pack contract validator v0.10 — 나쁜 pack 차단 gate(verdict만 판정) | --selftest |
| openbinggu_path_safety_gate.py | 현행 | 경로 안전 gate — allow_root 밖·symlink·NPKI·.env·운영 store deny | --selftest |
| openbinggu_phase2_local_persistence_selftest.py | 현행 | Phase 2-A 로컬 영속 selftest — temp HOME candidate 저장 흐름(write OFF 기본) | --selftest |
| openbinggu_phase2_staging_reread_e2e.py | 현행 | Phase 2-B staging 재독 E2E(read-only, PRAGMA query_only) | --selftest |
| openbinggu_phase4_reviewer_confirmed_selftest.py | 현행 | Phase 4 reviewer/confirmed flow selftest — token+resolver+governance 연결 | --selftest |
| openbinggu_phase6_manual_capture_selftest.py | 현행 | Phase 6 수동 1회 capture selftest — allowlist·rate limit·fail-closed | --selftest |
| openbinggu_physical_store_isolation_dryrun.py | 현행 | 물리 store 격리 validator — user_root namespace·cross-user BLOCK | --selftest |
| openbinggu_promotion_preview.py | 현행 | promotion preview — 승격 전 변환·write 계획만 표시(target DB mode=ro) | --selftest |
| openbinggu_proposal_batch_approval_g2b.py | 현행 | G2-B 연필 후보(edge proposal) 묶음 1클릭 승인(staging 한정 write) | --selftest |
| openbinggu_proposal_to_verb_edge_g2c.py | 현행 | G2-C 승인 proposal→사용자 동사 선택→볼펜 6종 엣지 staging 주입 | --selftest |
| openbinggu_public_tree_scan.py | 현행 | 공개 트리 secret/PII scanner — 검출 1건이면 BLOCK(실 트리는 --tree 명시) | --selftest |
| openbinggu_real_staging_cycle_once.py | 1회성 | real staging 연필→묶음 승인→볼펜 1사이클 실연(owner GO, 조건 8개) | ✗(실행 자체가 검증 절차) |
| openbinggu_real_staging_g3_once.py | 1회성 | real staging G3 실연 1회 — deprecated 도장 1건+리마인드 1건(owner GO) | ✗(실행 자체가 검증 절차) |
| openbinggu_real_staging_persist_apply.py | 현행 | real staging 남기는 1-pack 적용 실행기(--go 문구 필수, 스냅샷 보존) | ✗(선행 apply_once GATE 의무) |
| openbinggu_review_queue_bridge.py | 현행 | v0.12 review queue bridge — staging_plan→resolver 입력 preview 변환 | --selftest |
| openbinggu_review_resolver_sandbox.py | 현행 | review resolver 결선(sandbox) — decision→governance 라우팅, apply HOLD | --selftest |
| openbinggu_reviewed_apply_plan_validate.py | 레거시 | v0.16 reviewed_apply_plan validator 설계판(전 item executable=false, 참조 0 실측) | --selftest |
| openbinggu_reviewed_plan_preview.py | 레거시 | v0.15 decision→reviewed-plan preview 정규화(설계만, 참조 0 실측) | --selftest |
| openbinggu_reviewer_auth_session_selftest.py | 현행 | reviewer 인증/세션 토큰 S1~S19 selftest — 발급/검증/revocation mock | --selftest |
| openbinggu_runtime_access_engine.py | 현행 | 런타임 접근제어 강제 엔진 — deny-by-default 단일 enforce_access 진입점 | --selftest |
| openbinggu_save_intent_a2_deploy_canary.py | archived (1회성) | A-2 save-mcp 라이브 배포+canary(owner GO 2026-06-12) — `_archived_oneoff_20260612/` 이동 대상 | 자체(canary 게이트) |
| openbinggu_save_intent_d3_canary.py | archived (1회성) | D3 non-retention canary — 로컬 wrangler dev 실측 게이트 — `_archived_oneoff_20260612/` 이동 대상 | 자체(canary 게이트) |
| openbinggu_save_intent_d4_e2e.py | 현행 | D4 4조건 게이트 E2E — worker→pull→outbox→러너→temp DB(로컬, 재실행 가능) | 자체(E2E 게이트) |
| openbinggu_save_intent_outbox_runner.py | 현행 | hosted save-intent 로컬 outbox 러너(D2) — 게이트 본체, 수동 실행만 | --selftest |
| openbinggu_save_intent_v21_selftest.py | 현행 | V2-1 durable inbox+HMAC worker selftest(로컬 wrangler dev 한정) | 자체 |
| openbinggu_save_intent_v22_canary_verify.py | archived (1회성) | V2-2 canary 검증 재실행판(전파 지연 시 deploy 생략 재실측) — `_archived_oneoff_20260612/` 이동 대상 | 자체(canary 게이트) |
| openbinggu_save_intent_v22_deploy_canary.py | archived (1회성) | V2-2 라이브 D3' deploy+canary(owner GO 2026-06-12) — `_archived_oneoff_20260612/` 이동 대상 | 자체(canary 게이트) |
| openbinggu_save_intent_v23_live_e2e.py | 1회성 | V2-3 라이브 worker+로컬 러너 결합 4조건 E2E(owner 승인 1회 실측) | 자체(E2E 게이트) |
| openbinggu_save_intent_v2a_selftest.py | 현행 | V2-A MCP 어댑터(폰 적재)+HMAC pull selftest(로컬 wrangler dev 한정) | 자체 |
| openbinggu_scope_envelope_dryrun.py | 현행 | scope envelope 통합 dry-run — reader contract+visibility+access 묶음 검증 | --selftest |
| openbinggu_staging_write_selftest.py | 현행 | staging SQLite 엔진 정본(StagingDB/staging_apply) + selftest — 20개 스크립트가 import | 자체 |
| openbinggu_upload_preflight.py | 현행 | OpenCrab 업로드 preflight — G1~G7 fail-closed 체인(전송 미구현) | --selftest |
| openbinggu_v08_real_cycle_once.py | 1회성 | v0.8 real staging 쓰기 루프 1사이클 실연(owner GO 2026-06-11) | --dry-run-temp(공개 재현) |
| openbinggu_v08_review_resolve_4values.py | 현행 | 피드백 루프 4값(성공/실패/불확실/판정불가) resolve — 기록만, 자동 강등 0 | --selftest |
| openbinggu_v1_candidate_cycle_real_once.py | 1회성 | v1.0 후보 관리 1사이클 실연 — 보기→기각→수정→수용→철회→피드백(owner GO) | --dry-run-temp(공개 재현) |
| openbinggu_verb_edge_schema.py | 현행 | 동사형 엣지 6종 스키마 정본 + deprecated 검증기(검증만, 생산 0) | --selftest |
| watcher_batch_m1.py | 현행 | Watcher M1 — 다중 source(git diff+transcript+md) 수동 batch dry-run | --selftest |
| watcher_candidate_mvp2.py | 현행 | Watcher MVP2 — evidence_chunk→incoming_nodes 변환(candidate 강제) | --selftest |
| watcher_capture_mvp1.py | 현행 | Watcher MVP1 — git diff capture+evidence(2중 redaction, temp only) | --selftest |
| watcher_edge_mvp21.py | 현행 | Watcher MVP2.1 — evidence_supports edge 1종 생산(의미추론 금지) | --selftest |
| watcher_edge_proposal_g2.py | 현행 | G2 약한 후보 엣지 proposal 생산기 — nearby/stance 2종만(강한 라벨 자동 생산 0) | --selftest |
| watcher_op_m0.py | 현행 | Watcher 운영모드 M0 — 수동 1회 capture→evidence→nodes→report | --selftest |
| watcher_pack_builder_m0.py | 현행 | M0 산출→temp pack 조립 + pack_validate 계약 검증(pack 빌더 정본) | --selftest |

## 집계
- 총 77 = 현행 66 · 1회성 5 · 레거시 2 · archived(1회성, J그룹 이동) 4 (scripts/ 직속 실측 73 = 77 − archived 이동 4)
- archived 4종 파일 이동은 J그룹 담당 — 본 문서는 표기만. 이동 완료 후 위치 = `scripts/_archived_oneoff_20260612/`
