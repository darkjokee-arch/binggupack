# scripts/ 인덱스 (2026-06-30 v1.5.0 — strangler Phase2 이관·2차 라인(파서/discover/harvest)·branch/knowledge_graph 반영)

> **신규 기여자 길잡이 (5줄)**
> 1. 시작점은 `openbinggu_doctor.py --selftest` — 공개 전 필수 검사 단일 진입점(기존 selftest들을 subprocess로 호출만).
> 2. 모든 스크립트는 fail-closed·read-only/dry-run·candidate-only가 기본 — 운영 store write 0, confirmed/promotion 자동 생성 0.
> 3. "selftest" 열이 `--selftest`면 해당 플래그로, "자체"면 파일 실행 자체가 selftest/게이트(인자 불필요), `별도(↓_selftest)`면 짝 `*_selftest.py` 파일로 검증, `✗(...)`면 순수 유틸/import-only.
> 4. 분류: **현행**=현재 라인에서 import되거나 게이트로 사용(strangler thin wrapper 포함) / **1회성**=owner GO 1회 실연·canary(현재 직속 0 — 전부 `_archived_oneoff_20260612/` 이동 완료) / **레거시**=참조 0 실측·설계만·이전 버전.
> 5. raw 경로·secret·PII는 어떤 스크립트도 출력하지 않는다(id/hash/count/reason_code만) — 새 스크립트도 이 규약을 따를 것.

총 142개 직속 .py(= 141 운영 모듈 + 패키징 marker `__init__.py` 1개). 6/13 v1.4.0(73) 이후 strangler Phase2 thin wrapper 이관·2차 라인(discover/harvest/parser/topic_to_pack)·branch_explorer/knowledge_graph·publish 파이프라인(P1~P8)·거버넌스(hit/contrast/policy/merkle) 신규 반영. archived 하위폴더는 표 제외(별도 집계): `_archived/` 14개 · `_archived_oneoff_20260612/` 4개. 분류 근거 = 각 파일 머리 docstring + import 참조 관계 실측(`grep "import <모듈>"` 교차).

| 파일명 | 분류 | 한 줄 역할 | selftest |
| :--- | :--- | :--- | :--- |
| __init__.py | 현행 | 패키징 marker — scripts 모듈 bare-name import 후방호환(strangler 경로) | ✗(marker) |
| binggu_branch_explorer.py | 현행 | 1주제→재귀 분기 지식그래프(경로맥락 드리프트 차단·budget/depth·LLM 유동) | --selftest |
| binggu_canonical_semantic.py | 현행 | 도장(canonical 5종) semantic 분류 제안(opt-in·기본 OFF·PII 선차단) | --selftest |
| binggu_capture_buffer_selftest.py | 현행 | characterization selftest — capture_buffer.CaptureBuffer 동작 고정 | 자체 |
| binggu_capture_buffer.py | 현행 | 메모리 candidate 버퍼(thin wrapper→binggupack.capture.buffer) | 자체 |
| binggu_capture_classifier_selftest.py | 현행 | characterization selftest — capture_classifier.classify 판정 고정 | 자체 |
| binggu_capture_classifier.py | 현행 | 자동 캡처 판정기(thin wrapper→binggupack.classifier.capture_classifier) | 자체 |
| binggu_capture_cli_selftest.py | 현행 | characterization selftest — capture_cli run_batch 경로 고정 | --selftest |
| binggu_capture_cli.py | 현행 | capture 수동 호출 CLI(thin wrapper→binggupack.capture.cli·leaf) | 별도(↓_selftest) |
| binggu_capture_persist.py | 현행 | 영속 candidate 버퍼 — 기본 OFF·scope 게이트·TTL·rollback | 자체 |
| binggu_capture_profile.py | 현행 | AGI memory capture profile init/status/pause/resume/uninstall(idempotent) | 자체 |
| binggu_capture_session_selftest.py | 현행 | characterization selftest — capture_session.CaptureSession 고정 | 자체 |
| binggu_capture_session.py | 현행 | 세션 capture entrypoint(thin wrapper→binggupack.capture.session) | 자체 |
| binggu_capture_to_save.py | 현행 | capture→저장 게이트 어댑터(save_selected 위임·자동저장 구조적 불가) | 자체 |
| binggu_cloud_ingest_wire.py | 현행 | topic_to_pack→opencrab-cloud 자동 ingest 래퍼(삼중 게이트·기본 OFF) | --selftest |
| binggu_cloud_pack_export.py | 현행 | OpenCrab Cloud Pack v1 ZIP export(dry-run·fixture·업로드 0) | --selftest |
| binggu_collection_planner.py | 현행 | 주제→LLM 동적 분류설계(aspect)+무손실 룰 폴백(plan 생성까지만) | --selftest |
| binggu_contrast_protocol.py | 현행 | 대비 규약 — 빙구팩 신호↔강제조항 대비표(read-only·결정 0) | --selftest |
| binggu_created_at_backfill.py | 현행 | active 노드 created_at(P1 신선도) 소급 backfill(dry-run 기본·멱등) | --selftest |
| binggu_discover.py | 현행 | 주제→소스 자동발견+랭킹(fetch/파싱 0·provider 추상화·vet 재사용) | --selftest |
| binggu_e2e_lifecycle_selftest.py | 현행 | 실 CLI init→save→list→deprecate 라이프사이클 E2E(temp HOME) | 자체 |
| binggu_env_check.py | 현행 | 첫 설치/init 환경 점검+설치 안내(자동설치 0·점검만) | --selftest |
| binggu_graph_confirm.py | 현행 | 5층 사람 승인 confirm(read-only·자동 approve 0·재검증) | 자체 |
| binggu_graph_preview.py | 현행 | 3층 graph화 preview+graph validation(candidate/unverified·predicate 0) | 자체 |
| binggu_harvest.py | 현행 | P1 외부 수확 — 등록 소스→후보만(3중 게이트·deny-by-default) | --selftest |
| binggu_hit_export_selftest.py | 현행 | comp5 selftest — hit_export(merkle·PII 제외·거버넌스 write 차단) | 자체 |
| binggu_hit_export.py | 현행 | 적중률 raw 단방향 export(merkle root·거버넌스 read-only·self-modifying 0) | --selftest |
| binggu_hit_stats.py | 현행 | 양방향 신뢰도(owner 직감/ai 반박 적중률·비인과·signal_only) | --selftest |
| binggu_hosted_centroid_gen.py | 현행 | hosted 도장 분류용 centroid 생성(Workers AI bge-m3·기본 주입) | --selftest |
| binggu_hosted_inbox.py | 현행 | hosted inbox 회수·요약·선택 commit(collect broad·commit narrow) | --selftest |
| binggu_hosted_semantic_parity_selftest.py | 현행 | hosted semantic 도장 분류 py↔ts parity(regex 추출·model 핀) | 자체 |
| binggu_knowledge_graph.py | 현행 | explore 그래프→OpenCrab workflow 페이로드+노드별 수집 훅 | --selftest |
| binggu_local_collect.py | 현행 | 주제→aspect별 수집→aspect별 pack 오케스트레이터(오염격리) | --selftest |
| binggu_merkle_anchor.py | 현행 | comp3 Merkle 앵커 — hit_events 위변조 방지(해시체인·fail-closed) | --selftest |
| binggu_p1_config.py | 현행 | P1 안전벨트(헌법)+설정+가치관 로더(thin wrapper→binggupack.safety.p1_config) | 자체 |
| binggu_p1_ranking.py | 현행 | P1 pack 우선순위 랭킹(thin wrapper→binggupack.pack.p1_ranking·순수) | --selftest |
| binggu_pack_edges.py | 현행 | 세분화 pack 간 workflow edges 추론(결정적·5종 관계·절대 raise 0) | --selftest |
| binggu_pack_factory.py | 현행 | parsed docs+evidence→OpenBinggu pack(완료 기준=validate_pack) | --selftest |
| binggu_parser_adapter.py | 현행 | 전방위 파싱 어댑터(HTML/PDF/HWP/XLSX/DOCX/PPTX·절대 raise 0) | --selftest |
| binggu_platform_resolver_characterization_selftest.py | 현행 | characterization selftest — binggu_platform resolver 동작 고정 | 자체 |
| binggu_platform_selftest.py | 현행 | binggu_platform helper selftest(Win/WSL/macOS 경로정책·lock fail-closed) | 자체 |
| binggu_platform.py | 현행 | cross-platform 경로/플랫폼 helper(thin wrapper→binggupack.workspace.platform) | 별도(↓_selftest) |
| binggu_policy.py | 현행 | 자기진화 거버넌스 2단 선언형 정책 read-only 평가기(가드 5종·hash pin) | --selftest |
| binggu_provider_bench.py | 현행 | 검색 그물(provider) 후보 비교 측정 하니스(PoC·실 네트워크·키 없으면 skip) | ✗(측정 하니스) |
| binggu_publish_autopush.py | 현행 | SAVE 확정분→자동 KV 업로드 orchestrator(이중게이트 fail-closed·기본 mock) | --selftest |
| binggu_publish_p2_pipeline_selftest.py | 현행 | P2 selftest — 실 빌더·검증기 연결+영구금지 hard fail+BLOCK 케이스 | 자체 |
| binggu_publish_p2_pipeline.py | 현행 | 크로스디바이스 P2 실 빌더·검증기 연결+배포 plan(실행 0) | 별도(↓_selftest) |
| binggu_publish_p3_real_ledger_selftest.py | 현행 | P3 selftest — 실 ledger 읽기 가드+build 재검증(temp 합성) | 자체 |
| binggu_publish_p3_real_ledger.py | 현행 | P3 실 ledger fixture build 재검증(report-only·ledger ro) | 별도(↓_selftest) |
| binggu_publish_p4_label_selftest.py | 현행 | P4 selftest — data_class 인자화+candidate/active 라벨 분리 | 자체 |
| binggu_publish_p4_label.py | 현행 | P4 실 ledger build 라벨 정정(candidate/active 구분·ledger ro) | 별도(↓_selftest) |
| binggu_publish_p5_promote_selftest.py | 현행 | P7 selftest — candidate→active promote(idempotent·auto BLOCK) | 자체 |
| binggu_publish_p5_promote.py | 현행 | candidate→active 승격 정식 모듈(owner 명시·백업·auto 금지) | 별도(↓_selftest) |
| binggu_publish_p6_opencrab_pack_selftest.py | 현행 | P6 selftest — OpenCrab OC12 구조 수리+6결함 재현 검출 | 자체 |
| binggu_publish_p6_opencrab_pack.py | 현행 | P6 OpenCrab Desktop OC12 스키마 ZIP 수리(local-ingest·업로드 0) | 별도(↓_selftest) |
| binggu_publish_queue_p1_selftest.py | 현행 | P1 selftest — publish_queue+멱등 잠금+상태머신+APPROVE | 자체 |
| binggu_publish_queue_p1.py | 현행 | 크로스디바이스 P1 publish_queue+멱등 잠금+상태머신 | 별도(↓_selftest) |
| binggu_publish_run_all_selftests.py | 현행 | P8 회귀 묶음 러너(P1~P7 selftest+cloud_pack+tree scan·summary-fail) | 자체 |
| binggu_rationale_suggest.py | 현행 | 2층 근거 사슬 추천(PoC·read-only·신규 predicate 0·hallucination 0) | 자체 |
| binggu_realpack_build.py | 현행 | active 확정 노드→worker packs.json(serve·ledger ro·active 0이면 BLOCK) | --selftest |
| binggu_recall_trace.py | 현행 | 회상 효용 trace(used/ignored/corrected·opt-in·별도 store·ledger 불변) | --selftest |
| binggu_recall.py | 현행 | 회상 API+반문 엔진(thin wrapper→binggupack.pack.recall·read-only) | --selftest |
| binggu_save_gate_hash_characterization_selftest.py | 현행 | characterization selftest — save_gate.sent_hash/_norm 고정 | 자체 |
| binggu_save_gate_parse_characterization_selftest.py | 현행 | characterization selftest — save_gate.parse_save_indices 고정 | 자체 |
| binggu_save_gate.py | 현행 | 사람-발화 저장 게이트 기록장(0-A·append-only·위조 차단) | --selftest |
| binggu_semantic_clean.py | 현행 | 의미정제(LLM 노이즈 판단)→본문만(고정단어 0·절대 raise 0·보수적 보존) | --selftest |
| binggu_semantic_shadow.py | 현행 | L2 semantic_subtype shadow 분류(PoC·추천만·저장 0·기본 OFF) | --selftest |
| binggu_semantic_subtype_backfill.py | 현행 | active 노드 semantic_subtype 소급 backfill(dry-run 기본·멱등) | --selftest |
| binggu_session_close.py | 현행 | 세션 마무리 트리거 preview+거버넌스 요약(read-only·저장 0·모델 의미감지) | --selftest |
| binggu_setup_cloud.py | 현행 | 흩어진 cloud 셋업 명령 1진입점 오케스트레이터(멱등·실패정지·대행 0) | --selftest |
| binggu_speaker_owner_a0_exempt_characterization_selftest.py | 현행 | characterization selftest — owner 발화 a0 형식게이트 면제 고정 | 자체 |
| binggu_subtopic_decompose.py | 현행 | 주제 세분화(facet)→검색 query(템플릿 골격+LLM opt-in·무손실·결정성) | --selftest |
| binggu_topic_to_pack.py | 현행 | 2차 라인 통합 오케스트레이터(주제→발견→크롤→파싱→evidence→pack) | --selftest |
| binggu_worker_recall_selftest.py | 현행 | hosted worker §10 회상 도구(why_search/judgment_trace/preflight) selftest | 자체 |
| binggu_workflow_recommend.py | 현행 | pack(nodes/evidence)→실행 가능 workflow spec 추천(execution 0) | --selftest |
| binggu_workspace_organize.py | 현행 | 클라우드 workspace 비파괴 정리 분석+dry-run 리포트(파괴 callable 0) | --selftest |
| binggupack_constants_parity_selftest.py | 현행 | py↔ts 매직넘버 동기 검증 selftest(후보 상한 10·INPUT_CAP 20000·fail-closed) | 자체 |
| binggupack_http_mcp_skeleton_selftest.py | 현행 | skeleton 실 HTTP E2E(initialize/tools/Origin 가드/누출 0) | 자체 |
| binggupack_http_mcp_skeleton.py | 현행 | hosted MCP 로컬 PoC(127.0.0.1 read-only 5도구·JSON-only stateless) | 별도(↓_selftest) |
| binggupack_sign_util.py | 현행 | save-intent HMAC 서명 단일 출처(method+path 바인딩·ts 바이트 동일) | --selftest |
| examples_synthetic_guard_selftest.py | 현행 | examples/ sample json synthetic-only 가드 selftest(실URL/PII fail-closed) | 자체 |
| install_claude_mcp.py | 현행 | BingguPack MCP Claude Code 등록 헬퍼(claude mcp add·dry-run/apply) | ✗(설치 헬퍼) |
| localbinggu_incoming_loader.py | 현행 | incoming jsonl 불변식 검증 loader(thin wrapper→binggupack.pack.incoming_loader) | ✗(CLI 검증기, watcher 재사용) |
| localbinggu_ingest_executor.py | 현행 | 빌더 ZIP→로컬 OpenCrab 역인제스트 실행기(dry-run 기본·execute 명시) | --selftest |
| localbinggu_match_policy_selftest.py | 현행 | characterization selftest — match_policy(완전일치·4축 reject 고정) | 자체 |
| localbinggu_match_policy.py | 현행 | read-only match policy(thin wrapper→binggupack.policy.match·Tier 0~3) | ✗(__main__=draft 평가 출력) |
| localbinggu_review_resolver.py | 현행 | review decision→reviewed plan+audit 생성(production write 금지) | fixture 모드(--fixture-dir) |
| openbinggu_a0_node_dryrun.py | 현행 | 노드 정본(헌법 1조) validator(thin wrapper→binggupack.safety.a0_node) | --selftest |
| openbinggu_batch_pack_loader.py | 현행 | batch pack→staging apply→read-back→rollback 일괄 검증 실행기 | --selftest |
| openbinggu_c2_guard_selftest.py | 현행 | C-2 단일통제 guard in-memory selftest(자동검사 4종·rate limit) | 자체 |
| openbinggu_candidate_deprecate_ux.py | 현행 | 기각 UX("DEPRECATE n id8" confirm+실행 직전 재검증·node 한정) | --selftest |
| openbinggu_candidate_list_view.py | 현행 | candidate 목록 뷰(read-only·status/kind 필터·도장 표시) | --selftest |
| openbinggu_candidate_replace_ux.py | 현행 | replace transaction(기각+신규 저장 묶음·in-place 0·보상 원복) | --selftest |
| openbinggu_confirmed_governance_dryrun.py | 현행 | confirmed governance validator(G4 status 전이/G6 멀티유저 충돌) | --selftest |
| openbinggu_conversation_candidate_save.py | 현행 | 대화→candidate 저장(preview 재실행+"SAVE i,j" confirm 게이트) | --selftest |
| openbinggu_conversation_capture_preview.py | 현행 | 대화 capture 미리보기(순수 함수·write 0·PII 문장 후보 제외) | --selftest |
| openbinggu_deprecate_and_remind_g3.py | 현행 | G3 기각 도장(보존+기본조회 제외) staging 연동+검증 리마인드 | --selftest |
| openbinggu_doctor.py | 현행 | 공개 전 필수 검사 단일 진입점(기존 selftest subprocess 오케스트레이션) | --selftest |
| openbinggu_incoming_to_staging.py | 현행 | incoming→staging loader dry-run(thin wrapper→binggupack.pack.incoming_to_staging) | --selftest |
| openbinggu_label_kind_map.py | 현행 | label_kind 매핑/분류 정본(thin wrapper→binggupack.classifier.label_kind_map) | --selftest |
| openbinggu_mcp_path_gate_adapter.py | 현행 | MCP 도구 path 입력 가드(실행 직전 classify_path·BLOCK 시 미호출) | --selftest |
| openbinggu_mcp_server_handlers.py | 현행 | MCP 도구 핸들러 결선(path gate+access 2중 통과 시만 underlying 호출) | --selftest |
| openbinggu_mcp_server.py | 현행 | BingguPack local MCP 서버(stdio JSON-RPC·read/dry-run+save_candidate) | --selftest |
| openbinggu_owner_accept_ux.py | 현행 | owner_accepted UX(record-only event·"ACCEPT/UNACCEPT n id8") | --selftest |
| openbinggu_p3_self_improve.py | 현행 | 자기개선 planner+하네스 승격 exporter(제안/신호만·자동 적용 0) | --selftest |
| openbinggu_pack_consumer_smoke.py | 현행 | 로컬 pack consumer smoke(모델 중립 contract sanitize view·모델 호출 0) | --selftest |
| openbinggu_pack_review_e2e.py | 현행 | pack→review queue e2e dry-run(builder→bridge→resolver 연결) | --selftest |
| openbinggu_pack_validate.py | 현행 | pack contract validator v0.10(thin wrapper→binggupack.pack.contract_validate) | --selftest |
| openbinggu_path_safety_characterization_selftest.py | 현행 | characterization selftest — path_safety_gate.classify_path 판정 고정 | 자체 |
| openbinggu_path_safety_gate.py | 현행 | 경로 안전 gate(thin wrapper→binggupack.safety.path_safety·traversal/symlink) | --selftest |
| openbinggu_phase2_local_persistence_selftest.py | 현행 | Phase 2-A 로컬 영속 selftest(temp HOME candidate 저장·write OFF) | --selftest |
| openbinggu_phase4_reviewer_confirmed_selftest.py | 현행 | Phase 4 reviewer/confirmed flow selftest(token+resolver+governance) | --selftest |
| openbinggu_phase6_manual_capture_selftest.py | 현행 | Phase 6 수동 1회 capture selftest(allowlist·rate limit·fail-closed) | --selftest |
| openbinggu_physical_store_isolation_dryrun.py | 현행 | 물리 store 격리 validator(user_root namespace·cross-user BLOCK) | --selftest |
| openbinggu_proposal_batch_approval_g2b.py | 현행 | G2-B 연필 후보(edge proposal) 묶음 1클릭 승인(staging 한정 write) | --selftest |
| openbinggu_proposal_to_verb_edge_g2c.py | 현행 | G2-C 승인 proposal→동사 선택→볼펜 6종 엣지 staging 주입 | --selftest |
| openbinggu_public_tree_scan.py | 현행 | 공개 트리 secret/PII scanner(검출 1건이면 BLOCK·size 초과 fail-closed) | --selftest |
| openbinggu_review_queue_bridge.py | 현행 | review queue bridge(staging_plan→resolver 입력 preview 변환) | --selftest |
| openbinggu_review_resolver_sandbox.py | 현행 | review resolver 결선(decision→governance 라우팅·apply HOLD) | --selftest |
| openbinggu_reviewed_plan_preview_selftest.py | 현행 | characterization selftest — reviewed_plan_preview 이관 동작 고정 | 자체 |
| openbinggu_reviewed_plan_preview.py | 레거시 | v0.15 reviewed-plan PREVIEW(thin wrapper→binggupack.review·설계만·참조 0) | --selftest |
| openbinggu_reviewer_auth_session_selftest.py | 현행 | reviewer 인증/세션 토큰 S1~S19 selftest(발급/검증/revocation mock) | --selftest |
| openbinggu_runtime_access_engine.py | 현행 | 런타임 접근제어 강제 엔진(deny-by-default·단일 enforce_access 진입점) | --selftest |
| openbinggu_s4_gap_characterization_selftest.py | 현행 | S4 GAP characterization(gate-critical write core 미커버 분기 pin) | --selftest |
| openbinggu_save_intent_live_runner.py | 현행 | 라이브 worker HMAC pull→로컬 outbox→process_outbox(dry-run 기본·confirm) | --selftest |
| openbinggu_save_intent_outbox_runner.py | 현행 | hosted save-intent 로컬 outbox 러너(D2·게이트 본체·수동 실행) | --selftest |
| openbinggu_save_intent_v21_selftest.py | 현행 | V2-1 durable inbox+HMAC worker selftest(로컬 wrangler dev 한정) | 자체 |
| openbinggu_save_intent_v2a_selftest.py | 현행 | V2-A MCP 어댑터(폰 적재)+HMAC pull selftest(로컬 wrangler dev 한정) | 자체 |
| openbinggu_scope_envelope_dryrun.py | 현행 | scope envelope 통합 dry-run(reader contract+visibility+access 묶음) | --selftest |
| openbinggu_staging_write_selftest.py | 현행 | staging SQLite 엔진 정본(StagingDB/staging_apply)+selftest(다수 import) | 자체 |
| openbinggu_verb_edge_schema.py | 현행 | 동사형 엣지 6종 스키마 검증기(thin wrapper→binggupack.schema.verb_edge) | --selftest |
| smoke_test.py | 현행 | clone 직후 offline smoke test(thin wrapper→binggupack.pack.smoke·등록 불필요) | 자체 |
| strangler_wrapper_compat_selftest.py | 현행 | strangler 이관 wrapper 호환 회귀 하니스(package↔scripts byte-identical) | 자체 |
| version_consistency_selftest.py | 현행 | version SSOT(__about__↔pyproject) 일치 검증 selftest(fail-closed) | --selftest |
| watcher_batch_m1.py | 현행 | Watcher M1 다중 source(git diff+transcript+md) 수동 batch dry-run | --selftest |
| watcher_candidate_mvp2.py | 현행 | Watcher MVP2 evidence_chunk→incoming_nodes(thin wrapper→binggupack.pack.candidate_mvp2) | --selftest |
| watcher_capture_mvp1.py | 현행 | Watcher MVP1 git diff capture+evidence(thin wrapper→binggupack.pack.capture_mvp1) | --selftest |
| watcher_edge_mvp21.py | 현행 | Watcher MVP2.1 evidence_supports edge 1종 생산(의미추론 금지) | --selftest |
| watcher_edge_proposal_g2.py | 현행 | G2 약한 후보 엣지 proposal 생산기(nearby/stance 2종·강한 라벨 0) | --selftest |
| watcher_incoming_folder_adapter.py | 현행 | 기존 기록 폴더(박제·traj·md)→MVP2 evidence_chunk 입력 어댑터 | --selftest |
| watcher_op_m0.py | 현행 | Watcher 운영모드 M0 수동 1회(thin wrapper→binggupack.pack.op_m0) | --selftest |
| watcher_pack_builder_m0.py | 현행 | M0 산출→temp pack 조립+pack_validate 계약 검증(pack 빌더 정본) | --selftest |

## 집계
- 총 142 = 현행 141 · 1회성 0 · 레거시 1 (직속 .py 142 = 141 운영 모듈 + 패키징 marker `__init__.py` 1)
- 레거시 1 = `openbinggu_reviewed_plan_preview.py`(v0.15 설계만·production 참조 0 실측 — strangler thin wrapper 로만 유지).
- 1회성 0 = 6/13 v1.4.0 의 1회성 5종(real_staging_cycle/g3·v08_real_cycle·v1_candidate_cycle·save_intent_v23_live 등)은 전부 `_archived_oneoff_20260612/` 이동 완료. 직속에 잔존 0.
- archived 하위폴더(표 제외): `_archived/` 14개 · `_archived_oneoff_20260612/` 4개. (6/13 대비 직속에서 빠진 18개 = finalize_dryrun·phase2_staging_reread_e2e·promotion_preview·reviewed_apply_plan_validate·upload_preflight·v08_review_resolve_4values·real_staging 3종·v08_real_cycle·v1_candidate_cycle·save_intent canary/deploy/e2e 다수 → archived 이동분과 정합.)
- strangler Phase2 thin wrapper(정본 = binggupack/ 패키지, scripts/ 는 byte-identical 호환 shim): label_kind_map·verb_edge·match_policy·path_safety_gate·a0_node_dryrun·p1_config·p1_ranking·recall·incoming_to_staging·incoming_loader·pack_validate·capture_buffer/classifier/session/cli·platform·smoke_test·candidate_mvp2·capture_mvp1·op_m0 등.
