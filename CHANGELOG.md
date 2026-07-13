# Changelog — BingguPack

## [Unreleased]

### Fixed
- `backup_ledger` 가 손상 ledger.sqlite 에서 raw traceback 으로 죽던 갭 — `CORRUPT_LEDGER` status 반환 + 빈 산출물 정리 + CLI 복구 안내(restore 의 `INVALID_BACKUP` 방어와 대칭).

### Fixed — learn-outcome 축 교정: 발화 극성 → 교환(사용자 발화·AI 답변·확인) (2026-07-13)
owner 지적("사용자 대화 - ai답변 - 맞는지 틀리는지 확인 이렇게 가야 축이 맞지") — 구축은 발화 극성(hit/miss)을 speaker=owner 로 직결해 **옳은 지적("아니지…")이 owner 빗나감으로 계상되는 축 뒤집힘**이었다.
- **훅**(`hooks/user-prompt-learn-outcome.js`): 극성 = 결과가 아니라 입장 — `stance`(refutes 반박/accepts 인정) + 직전 AI 답변 발췌(`ai_answer`, 200자 절단) 큐 기록. `outcome` 은 legacy alias 유지. 구큐 항목은 outcome 극성에서 stance 유도(하위호환).
- **`hit_recording.mark_exchange_uttered` 신설**: stance × verdict(사람 확인: upheld 발화대로/overturned 뒤집힘) 귀속 — 반박+upheld → **owner hit + ai miss**(2행) · 반박+overturned → owner miss + ai hit · 인정 → ai 행만. dup 는 all-or-nothing(부분 삽입 0). 기존 `mark_outcome_uttered` 는 SUPERSEDED 표기(호환용 유지).
- **`learn-consume`**: `--verdict {upheld,overturned}`(기본 upheld). recall 연결 항목은 회상 조언 노드 귀속(`_node_outcome` — 그 조언이 맞았나) 유지 + 재현 0 이면 발화 교환 축 폴백(소비 가능 유지). preview/세션 마무리 표시를 교환 축(반박/인정 + AI 답변)으로 전환.
- 안전 불변: actor=human 게이트·발화 앵커(UserPromptSubmit hook 만 append)·안정 decision_id dup 차단·자동 확정 0 전부 유지. ai_answer 는 DB 미저장(표시 전용·PII 최소).
- 검증: hit_recording selftest 15/15 · learn_consume 14/14 · session_close GO · 실큐 6건 dry-run 축 정확 분류.

### Changed — pair 결합 번호축(도장 1회) + learn-consume 도장 소비 (2026-07-13)
owner 지적("같이 프리뷰 주면 해결") — 축별 preview+도장 2회 마찰 제거. 도장=사람 키보드만 원칙·fail-closed 완화 0.
- **`binggu pair` `--confirm` 생략 = 결합 미리보기 스테이징(저장 0)** — owner+ai 후보를 한 preview(연속 번호: owner 1..N · ai N+1..)로 `write_last_preview`(explicit·ledger 기준 home). 사람 도장 1회(`세이브 o,a`)로 양축이 함께 기록되고, confirm 재실행 시 결합 1-튜플 ref(`(pref(owner+ai), [o, N+a])`)를 우선 대조(기존 축별 2-튜플 폴백 유지 — 구 흐름 무손상).
- **`binggu learn-consume` 에이전트 세션 소비** — dry-run 이 큐 발화 원문을 preview 로 스테이징(번호=qi+1)하고, 사람 도장(`세이브 qi+1`) 후 `--confirm "CONSUME <qi>"` 재실행이 save-n ref 바인딩으로 human 승격. 도장 없으면 기존대로 G4 차단(fail-closed 불변).
- 회귀망: `tests/test_pair_single_stamp.py` 5건(결합 도장 승격·무도장 차단·부분 도장 all-or-nothing 차단·learn-consume 도장 소비·무도장 차단).

### Removed — MCP save approval 제거: MCP 도구 표면의 approval 요청/소비 배선 삭제 (2026-07-13)
저장 게이트 단일 원칙("preview + 사람의 `SAVE n` 입력") 후속 — owner 결정. 스코프는 **모델-facing MCP tool surface 만**.
- **MCP 핸들러의 `approval_gate.authorize` 배선 7곳 제거**(`binggupack/mcp/server_handlers.py` — save_candidate·pair·deprecate·replace·harvest_add/remove·mark_hit/miss). MCP write 시도는 `actor=reader` 고정으로 core 게이트(G4 fail-closed)에 위임 — 핸들러 게이트 재구현 0. `save_candidate` 만 core 의 사람 save-n 앵커(owner 키보드 `SAVE n` → hook 기록)로 확정 가능(기존 동작 유지).
- **`approval_id` 는 MCP write 를 승격하지 않는다** — tools/list 스키마 노출 제거·제시 시 무시(`approval_id_ignored` 응답 명시). MCP 는 PENDING 승인 요청도 만들지 않는다. dry-run fail-closed 안내는 `reason=human_save_required`(구 `trusted_approval_event_required`) + owner CLI 안내로 수렴.
- **보존(1바이트도 무변인 자산)**: approval core `binggupack/safety/trusted_approval.py` · `binggupack/mcp/approval_gate.py` 모듈(CLI `_mutation_via_approval` 가 공유) · owner CLI `binggu approval`/`approvals` 채널과 비-저장 mutation `--approval-id` 경로(accept/unaccept/due/resolve) · hag `--import-edges` · Studio Approval Center.
- **테스트 정렬**: `tests/test_trusted_approval_e2e.py` 의 MCP consume 단언을 "MCP 는 owner 가 mint 한 approval 조차 소비/승격 불가(fail-closed)" 보안 회귀로 반전 + CLI `--approval-id` 정확 1회/replay 차단 생존 케이스 신설. `openbinggu_trusted_approval_boundary_selftest.py` 동일 반전. handlers selftest 에 `approval_id_no_longer_promotes_BLOCKED` 회귀 추가.

### Changed — 저장 게이트 개정: preview + 사람의 `SAVE n` 입력 단일 원칙 (2026-07-12)
저장 경로(save/pair·hosted 커밋)의 사람 증명을 단일 원칙으로 개정. approval core(비-저장 mutation·`binggu approval` 채널·Approval Center·trusted_approval verifier·MCP fail-closed)는 무손상.
- **① Claude Code 환경** — UserPromptSubmit 훅이 기록한 "세이브 n" 발화가 유일한 사람 앵커. AI 는 훅을 거치지 못하므로 confirm 문구를 재현해도 승격 0(위조 불가 성질 유지) · `actor_source=save_gate_ref`.
- **② 터미널** — 사용자가 명령을 직접 입력한 것 자체가 save n. `sys.stdin.isatty()` 를 사람 앵커에서 삭제(isatty 는 사람 증명과 무관 · 파이프/리다이렉트 무관) · `actor_source=cli_command`.
- **③ 저장 경로의 approval 배선 삭제** — hosted pull → `commit_bundle` 의 approval mint/consume 기계 제거(CLI save/pair 는 원래 approval 배선 0). hosted 확정 = inbox preview + 사람의 `SAVE n`(confirm `SAVE <n[,n]>` 정확일치). crash-atomic 단일 COMMIT all-or-nothing·사전검증 전체차단·post-commit archive 계약은 보존. approval core 자산(accept/unaccept/due/resolve `--approval-id`·hag import-edges·MCP mutation fail-closed)은 1바이트도 무변.
- **④ 내용문장 hash 대조 → save-n 참조 바인딩** — 앵커 대조를 `preview_ref`(후보 집합+순서에서 결정론 파생) + 선택 idx 로 교체. 같은 정규화 문장이 **다른 preview** 에서 신선도 창 내 재사용되던 replay 면적 축소. gate log 는 ref 레코드 + 레거시 sh 행 이중기록(구 소비자 무수정 호환 · 마이그레이션 없음).
- **에이전트 세션 가드** — `CLAUDECODE` env 존재 + 훅 앵커 부재 → `reader`(`actor_source=agent_session_unanchored`). 이 env 는 승인을 부여하지 않고 **거부만** 합니다(env 로 fail-open 불가). 정직 한계: deny 전용이라 env 를 제어하는 에이전트·`CLAUDECODE` 부재 환경의 스크립트에는 하드 통제가 아님 — write 성사는 여전히 confirm 정확일치·preview·PII/secret 게이트 통과 필요.

#### Breaking / behavior changes
- **비대화형 스크립트 저장의 완화(loosening)** — 구 isatty 게이트가 차단하던 터미널 비대화형(pipe/redirect/cron) 실행이 이제 `CLAUDECODE` 부재 시 터미널 명령 경로(human · `cli_command`)로 통과한다. 자동화 스크립트가 정확한 confirm 문구를 제시하면 저장이 성사될 수 있다(사후 감사는 `actor_source` 로).
- hosted `pull` 의 `--approval-id` 인자 삭제 · `--confirm` 활성화 — 기존 approval 3단(요청→`approval approve`→`--approval-id`) hosted 자동화는 preview + `SAVE n` 흐름으로 변경 필요. 미소진 hosted approval request 는 owner 가 `binggu approval reject <rid>` 로 정리(자동 reject 없음 · 방치 시 expired 표시 무해).
- `BINGGU_STRICT_HUMAN_GATE` deprecated no-op 안내·`BINGGU_TRUSTED_CLI` 무시는 그대로.

#### Fixed
- `scripts/binggu_save_gate.py` 폴백 `gate_human_for` 에 정본의 미래-ts(age<0) 거부가 누락돼 있던 drift 를 정본과 재동기(ride-along 버그픽스 1건).

### Added
- **Transport-independent read-only Pack Service Core (v1.21-A)** — 미래 HTTPS MCP / `@BingguPack` 앱이 호출할 순수 core(`binggupack/app/read_core.py`). HTTP/MCP/OAuth/cloud/ledger 미의존.
- **Five App Path read tools** — `list_packs`·`get_pack_summary`·`search_evidence`(deterministic lexical-only)·`lookup_node_edges`(exact node_id / ambiguous keyword)·`build_handoff_context`(Phase 3 handoff guide 단일 정본). 기존 pack contract(`validate_pack`)·canonical layout(manifest + graph/nodes.jsonl + graph/edges.jsonl + evidence/index.jsonl) 재사용(신규 schema 0).
- **Deterministic conformance harness** — `python -m binggupack.app.conformance --selftest` (synthetic pack 10종 · 운영 ledger/네트워크 0).

### Security
- **pack root traversal/symlink 차단** — repository 밖 파일 미접근(realpath 격리) · symlink/비정규 파일 거부 · pack_id 정규식.
- **raw path/secret/PII 출력 0** — source_pointer 원문·source hash·절대경로 미노출 · pack .jsonl content 의 secret/PII scan(CLEAN 만 서비스) · 오류 message 에 내부 경로/stack 미노출.
- **write/network/cache 0** — 파일 read 만 · index/cache/log/temp/SQLite WAL 생성 0 · embedding/remote model/query log/usage trace 0.
- **candidate/evidence semantics 유지** — 모든 node/edge candidate(confirmed 자동 승격 0) · 없는 evidence_ref 미생성 · malformed/unsafe pack fail-closed(부분 서비스 0).

## [1.20.0] - 2026-07-12 — Your AI memory, visible and understandable

v1.20 은 consent-first memory 엔진을 CLI·MCP·로컬 Studio 에서 들여다볼 수 있는 일상 제품으로 만든다.
**schema migration 0 · dependency 추가 0 · 기존 mutation/approval semantics 변경 0 · production
approval verifier 변경 0.** 아래는 v1.20-A~E 누적 요약이며, 각 기능의 전체 근거·회귀 테스트는 이어지는
항목 그대로다.

### Added
- **읽기 전용 `binggu` 데일리 홈** — 인자 없이 `binggu`(또는 `binggu home`)를 치면 지금 상태(활성 기억·자동 수집 의도·원격 저장 의도·대기 승인·검토 예정·장부 무결성·capture/provider 상태)와 다음 할 일을 한 화면에서 본다. 장부가 없으면 오류 대신 온보딩 안내(생성 0).
- **통합 로컬 `binggu inbox`** — 자동 수집 후보·원격 저장 의도·대기 승인 요청·검토 예정을 한 화면에 모으는 read-only aggregator. `--capture/--hosted/--approvals/--due` 섹션 필터. 기존 큐 명령(`capture preview`·`hosted inbox`·`approval`·`reminders`)은 그대로 유지된다.
- **JSON 스냅샷** — `binggu home --json` / `binggu inbox --json` (schema_version=1). automation·향후 Binggu Studio 가 재사용할 안정적 read model.
- **로컬 read-only Binggu Studio Preview** — `binggu studio` 로 브라우저 기반 로컬 UI 를 띄운다. 실행마다 loopback(127.0.0.1) 임시 포트 + ephemeral session URL(`/s/<token>/`)로만 열리고, 기본 브라우저를 자동으로 연다(`--no-open` 으로 생략, `--port` 로 고정). Home + 통합 Inbox 를 시각화하며 모든 화면은 읽기 전용이다.
- **Studio = Daily Console schema v1 재사용** — `/api/home`·`/api/inbox` 는 `collect_home_snapshot`/`collect_inbox_snapshot` 을 그대로 호출한다(스냅샷 복제 0). ledger count·hosted index·approval request ID·due item·redacted preview 가 `binggu home/inbox --json` 과 동일하다. Python stdlib 만 사용(외부 의존성 0).
- **Studio Memory Explorer** — Studio 에 Memories 화면 추가. 저장된 ledger 를 브라우저에서 탐색한다: active/deprecated·type·subtype 필터 + 문장 검색으로 **페이지네이션된 목록**(`GET /api/memories` · 기본 30 · 최대 100 · 무제한 전량 반환 없음), **exact full-node-ID 기반 상세**(`GET /api/memory/<id>` · id8/suffix fuzzy 없음 · deprecated 도 조회 · evidence 발췌·관계·owner 승인·explain 요약), **lexical-only 회상**(`GET /api/recall`). 상세/카드 버튼은 `binggu explain <id>`·`binggu recall "<질문>"` 을 클립보드 복사만 한다(mutation endpoint/action 0). read model 은 `binggupack/studio/read_model.py`(mode=ro · schema 미적용).
- **Studio Approval Center** — Studio 에 Approvals 화면 추가. 승인 요청의 **exact request 상세**(`GET /api/approval/<full-request-id>` · id8/prefix/suffix fuzzy 없음 · 없으면 404)와 **페이지네이션 목록**(`GET /api/approvals` · state/operation 필터). effective 상태(pending/approved/consuming/consumed/rejected/revoked/expired)를 기존 verifier·`approval_consumptions` 로 read-only 해석(신규 semantics 0 · consumed 는 expiry 로 덮지 않음). owner review/timeline/receipt 를 안전하게 표시하고, `binggu approval show/approve/reject/revoke <request-id>` **로컬 터미널 명령을 복사만** 제공한다(Studio 자체 approval/mutation 0). read model 은 `binggupack/studio/approval_view.py`(mode=ro · now 주입 가능).
- **Canonical MCP 진입점 `binggupack-mcp`** — 신규 사용자용. 기본 **core profile**(12 도구: status·recall·why·trace_show·preflight·list·reminders·capture_preview + 승인 기반 save_candidate·pair·deprecate·replace). `--profile advanced` 로 전체 도구. serverInfo.name=`binggupack`.
- **MCP exposure profiles (core/advanced)** — profile 은 tools/list **및** tools/call 양쪽에서 강제(숨긴 도구는 handler 호출 전 차단·`tool_not_in_profile`·write/network 0). profile 은 서버 시작 시 1회 결정되고 이후 불변(요청/env 로 승격 불가). stdio·HTTP 동일 경로.

정직한 문구:
- 기존 진입점 `openbinggu-mcp-server` 는 그대로 동작하며 **기본이 전체(advanced) 도구**다(하위호환·serverInfo.name=`openbinggu` 불변). Legacy-compatible entry point. No removal date is currently scheduled.
- 새 `install_claude_mcp.py` 등록은 core profile 로 노출한다. 기존 등록은 자동 교체·삭제하지 않는다(신규만).
- MCP handler 로직·approval/mutation 경계·schema 변경 0 — 이번 변경은 진입점·startup profile·노출 필터뿐이다.
- `binggu inbox` 는 기본적으로 네트워크 fetch 를 하지 않는다(로컬 스냅샷만). 원격을 새로 가져오려면 `binggu hosted inbox`.
- mutation(저장·승인·교체·폐기·동기화)은 기존 명령과 owner approval 경계를 그대로 사용한다 — daily console 은 표현 계층일 뿐이다.
- `binggu home`/`binggu inbox` 실행만으로 저장되는 데이터 0(ledger·capture·staging·config·provider 상태·use_count 불변). SQLite 조회는 mode=ro URI 로만 한다.

### Security
- **Studio mutation endpoint 0** — GET/HEAD 만 허용(POST/PUT/PATCH/DELETE/OPTIONS → 405, 어떤 handler/CLI mutation 도 호출하지 않음). approve/저장/hosted fetch/ledger write/디렉토리 생성 0.
- **loopback only** — 127.0.0.1 에만 bind(`--host` 미제공·0.0.0.0/LAN/외부 인터페이스 불가). Host 헤더는 127.0.0.1/localhost 만 허용(그 외 403).
- **session-scoped URL** — 실행마다 새 ephemeral token(메모리에만·파일/config/로그 0). 잘못된/없는 token → 404. token 은 URL 이외 응답 본문에 미포함.
- **external asset/network 0** — HTML/CSS/JS 는 self-hosted(CDN/외부 폰트/이미지 0). CSP `default-src 'self'`·CORS 헤더 0·`Cache-Control: no-store`.
- **Memory Explorer: semantic cache/network 0** — Studio recall 은 LEXICAL_ONLY_SCORER 를 주입해 `why_search` 가 semantic scorer 초기화·`recall_embed_cache` open·Ollama/embed/network 를 하지 않고 term-frequency ranking 만 쓴다. use_count 증가·recall_trace 기록 0. mode=ro 조회만(schema apply/migration/makedirs 0).
- **Memory Explorer: exact full-ID detail** — 상세 조회는 `WHERE node_id=?` 정확 일치만(id8/suffix fuzzy 없음 · 없으면 404 · 다른 node 로 자동 보정 0).
- **Memory Explorer: mutation endpoint/action 0** — GET/HEAD 만. UI 에 forget/deprecate/replace/accept/delete 버튼 0(mutation handoff 는 이후 범위).
- **Memory Explorer: sensitive provenance redaction** — source_pointer_id/source_hash/raw conversation/nonce/provider config 미노출. evidence·peer 는 `safe_excerpt`(≤160자) · id 는 sha256[:8] display_id. 입력 검증: state/limit/offset/node_id/query 범위·NUL·control/bidi(위반 400) · SQL 은 전부 parameter binding.
- **Approval Center: approval nonce 미노출** — approve event 의 `approval_nonce`·raw approvals.jsonl record·event store path·review file path·ledger 절대경로·`trusted_approval.json`·raw provider config·source pointer·credential 은 응답에 절대 포함하지 않는다. timeline 은 허용 필드(record_type/at/channel)만, receipt 는 whitelist projection(request_id/operation/node_ids/decision_id/consumed_at · nonce/actor/unknown 제거).
- **Approval Center: exact full request ID lookup** — 상세는 `approval_requests.request_id=?` 정확 일치만(id8/prefix/suffix/fuzzy 없음 · 없으면 404). review 파일은 DB canonical request_id 로만 접근(URL id 직접 path 결합 0).
- **Approval Center: review integrity + symlink/oversize 거부** — review 파일은 request_id/operation/payload_digest == DB 무결성 검증(불일치 → integrity=mismatch·items 미반환) · symlink/비정규 파일/256KB 초과 거부 · 경로 순회 방어(정규식 + realpath). 결정 후 purge 되면 integrity=unavailable_after_decision(재생성 0).
- **Approval Center: mutation endpoint/call 0** — GET/HEAD 만. UI 는 `binggu approval` CLI 명령 클립보드 복사만(승인/거절/취소 실행 버튼·POST fetch·terminal launch·approval phrase 자동생성·mutation 추측 0). append_event/mint_approval/tombstone/purge_review/reserve/finalize_consumed/release 호출 0.

### Honest boundary
- Studio 는 **read-only handoff UI** 다 — Studio 에서 owner approval 을 실행하지 않는다(복사한 명령을 owner 가 별도 로컬 터미널에서 직접 실행).
- Local TTY 는 **L1 owner-routing** 이다(암호학적 보증 아님 · UX 경계).
- 같은 shell/filesystem 을 이미 제어하는 에이전트에 대한 **hard approval authority 가 아니다**(fail-closed routing + 비대화형 owner 경로 + intent-routing).
- Protected writer/verifier/trust root/detached signer 는 **RFC only**(미구현 · production 코드 0).

## [1.19.0] - 2026-07-11 — Stable promotion of v1.19.0rc1

v1.19.0rc1 을 검증 완료 후 stable 로 승격. **RC 이후 production code 변경 0**(version-only promotion:
pyproject / `binggupack/__about__.py` version literal · CHANGELOG · release notes 만 변경). RC artifact 와
production 모듈은 version literal 제외 byte-equivalent. 실경로 mobile/web→PC canary(hosted intent →
owner 로컬 대화형 승인[cli_tty] → exact-bound one-time commit · direct_write_before_approval=0 · write 1 ·
retry write 0 · original receipt · source 보존 · integrity INTACT) 통과. 상세 근거는 아래 [1.19.0rc1] 및
P1-* 섹션 그대로.

## [1.19.0rc1] - 2026-07-11 — Consent-first, exact-bound AI memory (Release Candidate)

P0~P1-B.1 누적을 첫 정식 릴리스 후보로 봉인. 아래는 릴리스 분류 요약이며, 이어지는 상세
섹션(P1-B / P1-B.1 / P1-A / P1-A.1 / P0.1 / P0)에 전체 근거·회귀 테스트가 그대로 있다.

#### Added
- `binggu demo` — 60초 오프라인 체험(네트워크·API 키 0)
- Trusted approval request/event — owner 로컬 승인으로 MCP mutation 정확히 1회
- Local approval CLI (`binggu approvals` / `approval show|approve|reject|revoke`)
- Exact-bound mutation — accept/unaccept/due/resolve/confirm_edges + hosted 3파일 + hag 를 exact binding or fail-closed
- Hosted bundle flow — 폰/웹 intent → PC 로컬 승인 → exactly-once crash-atomic bundle commit

#### Changed
- Remote confirm 은 승인이 아니라 **intent** 다(저장하지 않음)
- 폰/웹 저장은 PC 로컬 승인이 필요하다
- 비대화형 mutation 은 `--approval-id` 가 필요하다
- `actor` 라벨은 권한(authority)이 아니다

#### Security
- Autonomous preview→confirm 자동 저장 차단(fail-closed `G4_no_auto`)
- Exact operation/payload/ledger/version binding + one-time consume
- Crash-atomic hosted bundle(단일 COMMIT · 부분 bundle 0)
- Source 자동 삭제 0 · direct hosted write 0
- 사설 경로/소유자 메타데이터 스캐너(`private_path_scan`)를 CI 프리플라이트·publish 업로드 게이트에 등록(배포물에 owner 절대경로/사설 프로젝트 토큰 0)

#### Known boundary (정직)
- Local TTY 는 L1 routing 이다(암호학적 보증 아님 · UX 경계)
- Shell/filesystem 병재 에이전트에는 **하드 승인 권한이 아니다**(fail-closed routing + 비대화형 owner 경로 + intent-routing)
- Protected writer/verifier/trust root/detached signer 는 **RFC only**(미구현 · production 코드 0)
- root/admin compromise 방어는 주장하지 않는다

#### Breaking / behavior changes
- confirm 문구만으로 비대화형 write 불가 · `actor=human` 으로 권한 승격 불가
- 폰/웹 confirm 은 pending approval request 만 생성(commit 은 PC 승인 필요)
- 기존 confirm-only 자동화는 `approval_id` 흐름으로 변경 필요 · direct hosted save 제거
- `BINGGU_TRUSTED_CLI` 백도어 제거 · `BINGGU_STRICT_HUMAN_GATE` deprecated no-op
- 1.19.0 미만으로 downgrade 시 trusted-approval-event 강제 계층이 사라진다(데이터는 보존되나 구 confirm-only 게이트만 남음 — 보안 회귀)

---

### Security / Hardening (P1-B · Track A) — Mutation Surface Closure
- **P1-A 가 "STILL-OPEN, P1-B" 로 미룬 mutation 표면을 exact-bound approval 로 봉인** — 승인이 필요한 write 경로에서 남아 있던 `{"actor":"human"}` transported/literal 승격을 전부 제거하고, 비대화형 owner 는 `--approval-id` exact-bound 승인으로만 통과. 설계 정본 `docs/BINGGUPACK_P1B_MUTATION_CLOSURE_DESIGN.md`. RFC §19.1①②④·§23·§26 R2/R5 상태노트 갱신.
  - **CLI 5개(accept/unaccept/due/resolve/confirm_edges) 봉인**: `trusted_approval.binding_fields` 에 5 operation 스키마 추가(evidence_refs 까지 바인딩). `binggu.py cmd_*` 에 `--approval-id` 비대화형 owner 경로(`_mutation_via_approval` → `approval_gate.authorize` verify + one-time consume). 미승인/바인딩 불일치 → `reader` → core fail-closed(G4). **코어 함수 시그니처·동작 불변**(게이트는 CLI 층에만 · owner_accept/g3/graph_confirm/hag 37+ 체크 무회귀). `binggu.py confirm-edges` 자체는 staging(sync_edges) write 만이라 TTY/앵커 게이트 유지, 운영 ledger edge write 는 hag import-edges 로 봉인(아래).
  - **hosted save-intent 3파일 봉인**: `binggu_hosted_inbox.py`·`openbinggu_save_intent_live_runner.py`·`openbinggu_save_intent_outbox_runner.py` 의 direct `save_selected(actor=human)` 경로를 **전부 제거·도달불가**로 만들고, 유일 저장 경로를 로컬 exact-bound **`hosted_bundle`** 승인(`commit_bundle`)으로 통일. 폰/웹은 저장 **의도만** 전달 → 정상 intent 는 `.pending_approval`(원문 보존 · 삭제/스트립 0) → PC 가 선택 묶음 전체를 `--approval-id` 로 **atomic all-or-nothing** 커밋(membership 변경→request_id 변화→기존 승인 무효). live_runner 는 1회용 tempdir 소실 방지로 승인 대기 intent 를 영속 staging 으로 비파괴 이전. **데이터 손실 0**.
  - **`hybrid_agi/hag_sync_adapter.py` 봉인**: `import_confirmed_edges` 재작성 — positional `actor` 제거 → **actor 는 keyword-only 감사 메타**(권한 효과 0). 운영 ledger edges INSERT 는 exact-bound `import_edges` 승인(post-filter 정규화 edge set · evidence 바인딩) + one-time reserve/finalize 필수. `--actor human` CLI arg 단독의 사람 근거 효과 제거(미승인 → fail-closed `approval_required`+request_id · replay→0 second write).
  - **정직 경계(변함없음)**: 위는 "**승인이 없으면 안 써진다**"(mutation 표면)를 봉인한다. **셸/파일 도구를 가진 같은 에이전트가 승인 store 를 직접 write 해 승인 자체를 위조**하는 축(§19.1 ③)은 여전히 열려 있다. 그 store 격리(OS keychain·별도 프로세스·별도 디바이스) 설계는 `docs/BINGGUPACK_PROTECTED_WRITER_RFC.md`(**Track B · L0~L4 보증 등급 · 설계 RFC · 미구현**). owner dev(FS/Shell 병재) 머신에서 Track A 전체는 하드 통제가 아니라 "자동저장 방지 + 비대화형 owner 경로 + intent-routing". 원문 자동 삭제 없음 · purge 는 별도 owner action.
  - **회귀**: `scripts/binggu_approval_origin_selftest.py`(AST 인벤토리 — CLI 진입점 리터럴 human write 0) · `hag_sync_adapter --selftest`(37/37 · 12 필수 HAG 체크: actor/confirm/env cannot approve · provider absent fail-closed · op/payload/ledger mismatch · expired · replay no-2nd-import · actor=audit-metadata-only) · hosted inbox/outbox/live_runner `--selftest` · `binggu.py --selftest`(hosted 13~16 승인 흐름).
- **P1-B.1 — Exact Membership + Crash-Atomic Commit (외부 owner review 봉인)**: hosted `commit_bundle` 의 두 merge blocker + 인접 receipt 계약을 닫는 하드닝.
  - **H1 exact membership**: 선택 intent 중 **하나라도** 사전검증 실패(누락·만료·malformed·schema·intent_id·confirm·**중복**)면 `bundle_prevalidation_failed` 로 **전체 차단** — 승인 요청/mint/reserve/ledger write/원문 이동 0. 이전엔 유효 부분집합으로 **암묵 축소**(silent shrink)됐음. 중복 intent_id 는 암묵 dedupe 폐기 → `duplicate_selection` 으로 표면화(명시 계약).
  - **H2 crash-atomic 단일 COMMIT**: `save_selected` 반복(intent별 독립 COMMIT + snapshot 파일 rollback)을 **Phase 1 prepare(DB write 0) → Phase 2 reserve → Phase 3 단일 `BEGIN IMMEDIATE`(전 intent 노드/근거/`applied_registry` + `finalize_consumed` + `approval_requests` consumed + 성공 audit → COMMIT 정확히 1회)** 로 재구성. 프로세스 kill: COMMIT **이전** → ledger write 0(예약 lease 회복) · COMMIT **이후** → 전체 write + consume receipt. **부분 bundle 은 어떤 재오픈 시점에도 없음**(subprocess `os._exit` hard-crash 테스트로 실증). `audit_append(commit=False)`·`finalize_consumed(commit=False)`·`apply_pack_in_txn` 추가. **단건 `save_selected` 동작 불변**(prepare 공유 · staging_apply 리팩터).
  - **M1 Contract-8 receipt + archive 분리**: `approval_id` 제시 시 source load 전 `get_consumption(request_id)` 먼저 조회 → 이미 consumed 면 `already_consumed` + **original receipt** 반환(이전엔 원문 archive 이동 후 재시도가 `no_valid_intent` 오반환). 원문 archive 는 DB 트랜잭션과 **분리된 멱등 post-commit reconciliation** — archive 실패는 ledger 성공을 뒤집지 않고 `archive_pending` 로 원문 보존 → 다음 실행이 원 receipt 로 재정리(자동 삭제 없음).
  - **회귀**: `scripts/binggu_p1b1_bundle_atomicity_selftest.py` 신설(membership 7 · crash 5[os._exit] · receipt/archive 6) · run_all 등재 · `binggu_hosted_bundle --selftest` 12/12 · p1b mutation closure 28/28 무회귀.
- **Design (Track B · 별도 PR #5 · 미구현)**: `docs/BINGGUPACK_PROTECTED_WRITER_RFC.md` + `scripts/binggu_protected_writer_attack_demo.py` 는 **Track B(PR #5)에서 신설**된다(이 Track A PR #4 트리에는 미포함 — 두 PR 함께 병합/릴리스). approval store 를 모델 tool surface 밖(keychain/별도 프로세스/별도 디바이스)에 두는 Protected Writer + Detached Signer 설계. 보증 등급 L0~L4 · Provider Matrix · same-user daemon 한계 · user presence/verification/trusted display 구분 · trust-root 보호 · software-key fallback 을 hard 라 안 함 · root/admin out-of-scope · shell/fs agent in-scope · 공격 프로토타입(`scripts/binggu_protected_writer_attack_demo.py`). **production 코드 0.**

### Added (P1-A) — Trusted Approval Event
- **owner-controlled 승인으로 MCP mutation 정확히 1회 실행**: P0/P0.1 에서 fail-closed(`trusted_approval_event_required`) 로 봉인된 MCP 저장/기각/교체/기록(`save_candidate`·`pair`·`deprecate`·`replace`·`mark_hit`/`mark_miss`·`harvest_add`/`harvest_remove`)을, owner 가 로컬에서 승인하면 안전하게 실행하는 경로. 설계 정본 `docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md`(v2).
  - **신뢰 분리**: MCP/모델은 승인 **요청**(PENDING)만 만들고, 승인 **발행**은 모델 도구 표면 밖 owner 채널(`binggu approval approve`, 대화형 TTY — 비대화형 stdin 하드 거부)만 한다. `binggupack/safety/trusted_approval.py`(core) + `binggupack/mcp/approval_gate.py`(핸들러 결선).
  - **정확 바인딩**: 승인은 `(protocol_version, operation, canonical payload digest, ledger_id)` 에 묶인다. canonical digest = NFC 정규화 + bidi/control 거부 + sort_keys JSON(concat 충돌 불가) + versioned. `save_candidate` 는 `explicit`/`speaker`/`due_date` 까지 바인딩(렌더러=실행기 정합), `mark` 는 recall 스냅샷(nonce) 바인딩.
  - **1회용 consume + lease**: `approval_consumptions.approval_nonce` UNIQUE PK single-winner + `reserved_at` lease. 재생(replay)→`already_consumed`+receipt·2차 write 0 / 동시 consume→정확히 1 winner / payload 한 글자 변경·다른 operation·다른 ledger·다른 protocol·만료·거절/회수→전부 차단. reason 분할표(IDEMPOTENT_DONE/TRANSIENT/HARD_BLOCK/RECOVER)로 crash-후-재시도 정확히 1회 완성. `replace`(비-멱등)는 consume 진입 전 pending journal all-or-nothing 복원.
  - **privacy**: 승인 store 는 digest/hash + payload-agnostic summary 만(원문 저장 0). owner 실내용 검토는 별도 `approval_review` 레코드(PII/개인사 게이트·cap·TTL·결정 시 purge). 응답에 approval nonce 미노출.
  - **CLI**: `binggu approvals` / `binggu approval show|approve|reject|revoke <req-id>`.
  - **한계(정직)**: 하드 통제는 모델 도구 표면이 승인 저장 위치에 쓰지 못하는 배포(웹/앱 커넥터·잠긴 에이전트)에서만 성립. Filesystem/Bash MCP 를 함께 물린 호스트에서는 fail-closed 보존 intent-routing(하드 통제 아님). provider 미구성 시 fail-closed 는 모든 배포 공통. SECURITY.md 위협모델 참조.
  - **schema v2**(additive·비파괴): `approval_requests`·`approval_consumptions` 테이블 + `audit_meta['ledger_id']`(최초 open 시 무조건 발행). 구 ledger 는 auto-grant 0 으로 마이그레이트.
  - **하드닝(TAE-2)**: `replace_from_list`·`c2_check` 의 actor 게이트를 denylist→allowlist(`== "human"`)로 전환(non-`reader` sentinel fail-open 제거). `gate_human_for` 음수-age(미래 ts) 무효화(TAE-P2-08).
  - **적대 회귀**: `scripts/openbinggu_trusted_approval_boundary_selftest.py`(24체크·run_all 등재) + `scripts/binggu_trusted_approval_binding_characterization_selftest.py`(TIER-4 canonical 19체크·run_all 등재) + `tests/test_trusted_approval_e2e.py`(CLI subprocess). Fable5 사전 3-reviewer(8 High) RFC v2 해소 후 구현 → 사후 3-reviewer(1 High=pair due_date 미바인딩·1 High=smoke 9b reason 회귀·3 Medium·2 Low) 전부 수정.

### Security / Hardening (P1-A.1) — 승인 기원 계약(Approval-Origin Contract) 봉인
- **RFC 가 선언(§6/§19/TAE-5)했으나 코드가 강제하지 않던 승인 기원 격차 봉인** — "환경변수·비대화형·actor 문자열·confirm 문구·bare isatty 는 사람 승인이 아니다"를 실제 코드로 강제. 변경은 계약 강화만(새 도구 표면 0).
  - **`approval approve` 의 `BINGGU_TRUSTED_CLI` 백도어 제거(AOB-1)**: env truthy 로 비대화형 mint 하던 경로 삭제 → 비대화형(pipe/redirect)·환경변수 approve 는 항상 `no-write · exit≠0`. mint 는 isatty 검증 + typed `APPROVE <rid8>` 후에만 · `channel="cli_tty"` 로만(정직한 라벨).
  - **`_resolve_human_ctx` fail-closed 기본(AOB-2)**: 기본 actor=`reader`(모든 core 게이트 BLOCK). `human` 승격은 대역 외 사람 근거 2가지(키보드 `SAVE n` 의 `save_gate` 앵커 · 대화형 TTY)만. `BINGGU_STRICT_HUMAN_GATE` 는 **deprecated no-op**(0/false 로 fail-open 불가). `mint_approval` 기본 channel `cli_tty`→`unverified_direct`(직접 import 가 TTY 를 자칭 못 함).
  - **레거시 CLI mutation 12곳 재배선(AOB-3)**: `deprecate`/`replace`/`accept`/`unaccept`/`due`/`resolve`/`mark`/`trace mark`/`confirm-edges` + `save --accept`/`pair --accept` 가 하드코딩 `{"actor":"human"}` 대신 전부 `_resolve_human_ctx` 경유(검증된 ctx 재사용). binggu.py 잔존 리터럴 human write = **0**(AST 인벤토리 강제).
  - **회귀**: `scripts/binggu_approval_origin_selftest.py`(10체크·run_all 등재) — env_var_cannot_approve(1/true/TRUE/yes/on/random) · noninteractive_always_blocked · strict_false_no_fail_open · noninteractive save/pair blocked · **ship-guard**(production wheel 에 `test_double` 채널·env 승인 read 0) · **AST 인벤토리**. 대화형 성공경로는 PTY(Unix)로 검증(`test_interactive_approve_pty`). 테스트는 env 백도어 대신 **owner-채널 test double**(`ta.mint_approval(..., channel="test_double")`)·save_gate 앵커·PTY 로 성공경로 구성(production 백도어 0).
  - **CI 미스 방지**: `scripts/ci_local_preflight.py` — `.github/workflows` 전 게이트(version SSOT·ruff·platform·selftest·doctor·autopush·setup_cloud·run_all·e2e·pytest·tree·MCP install smoke)를 커밋 전 로컬에서 그대로 재현하는 얇은 러너.
  - **사후 적대검증(Fable5 3-reviewer: black-box env/pipe/PTY 공격·source actor-origin 감사·doc-claim 정합) 반영**: (1) `learn-consume` 도 `_resolve_human_ctx` 경유로 재배선 — `CONSUME <n>` 문구 단독의 비대화형 fail-open 봉인(`learn_consume.consume(..., ctx)` · mark_outcome 의 actor=human 게이트가 reader 를 BLOCK · fail-closed 회귀 2케이스 추가). (2) 인벤토리 주장 정밀화 — binggu.py CLI 진입점 리터럴 0 은 유지하되 "전체 잔존 0"의 과대해석을 제거하고 CLI-도달 hosted 커밋 3파일을 **매 실행 명시 출력**(§D explicit-exclude · 거짓 전역주장 0). (3) `binggu_e2e_lifecycle_selftest` subprocess `input=""` 강제 — 상속 콘솔 isatty True(Windows)로 인한 비결정성 제거, 비대화형 fail-closed 를 전 OS 결정적 검증. (4) `tombstone` 기본 channel `cli_tty`→`unverified_direct`(reject/revoke 는 isatty 미검증 → 정직 라벨). 계약 핵심(env·비대화형 approval 이벤트 0)은 31+ 공격에도 불변 확인.
  - **여전히 P1-B(비대화형 owner 승인 스키마 필요)**: `accept`/`unaccept`/`due`/`resolve`/`confirm-edges`(binding_fields 스키마 부재 → 현재 TTY/앵커로만) · hosted save-intent 커밋 3파일(`binggu_hosted_inbox`·`openbinggu_save_intent_live_runner`·`openbinggu_save_intent_outbox_runner`) · FS/Bash 병재 호스트 직접 import mint 및 대화형 TTY PTY 위조 · `hybrid_agi/hag_sync_adapter.py --actor human`(owner 셸 전용). RFC §19.1·§23 STILL-OPEN 참조.

### Security / Hardening (P0.1)
- **demo 격리 경로 하드닝**: `--home` 비교를 `expanduser→abspath→realpath→normcase` + `os.path.samefile` 로 강화. (1) 운영 홈을 가리키는 **symlink**, (2) **대소문자만 다른** 동일 경로, (3) `--home` 아래 **기존 `ledger.sqlite`** 는 전부 BLOCK(기존 장부 재사용/오염 금지 — 재사용은 향후 `--reuse-demo-home` 로 명시 설계). `try/finally` 로 **subprocess·예외·조기 return 어디로 빠지든 BINGGU_HOME 복구 + 자동 생성 임시 홈 정리**. `--keep` 은 새 데이터 보존일 뿐 기존 장부 재사용 수단이 아님.
- **MCP fail-closed 안내 정합**: `pair`/`deprecate`/`replace`/`mark` dry-run·실행 응답에 `write_available: false` · `reason: trusted_approval_event_required` · `owner_action: use_local_cli` · `guidance`(로컬 CLI 안내) 추가. "dry_run=false + confirm 으로 실행" 류 오해 문구 제거(`confirm_expected` 는 호환 유지, 단 그것만으로 실행된다는 안내는 삭제). 도구 설명·README MCP 도구 분류(조회 / 미리보기 / 사람 앵커 저장 / 일시 fail-closed mutation)를 실제 상태로 수정. 보안 동작(actor=reader fail-closed)은 그대로.

### Added
- **`binggu demo` — 60초 체험**: 설치 직후 격리 임시 장부에서 후보 발견 → 승인 → 확정 → **새 프로세스 회상** → 근거(provenance) 전 과정을 오프라인(네트워크·API 키 0)으로 보여준다. `--non-interactive`(CI/자동화·데모 격리 홈에서만 승인 시뮬), `--home`/`--keep`. 운영 장부는 구조적으로 미접촉(운영 홈과 같으면 거부). `tests/test_demo.py` 회귀 + CI 스텝.
- **기본 사용자 흐름 UX**: 인자 없이 `binggu` → 홈 화면(장부 상태 + 다음 행동 안내). 별칭 `explain <id>`(=trace show)·`forget <id>`(deprecate 안내·확인 문구 유지)·`inbox`(=hosted inbox).
- **영문 진입점** `README.en.md` + README 상단 재편(60초 체험·Candidate→Review→Commit→Recall→Explain/Replace 멘탈모델·Core/Bridge 구분·Consent-first/Local-first/Auditable/Model-agnostic).

### Security
- **MCP 승인 경계 봉인(P0)**: `pair`/`deprecate`/`replace`/`mark_hit`/`mark_miss` 핸들러가 `confirm` 정확일치만으로 `actor="human"` 을 부여하던 우회를 제거하고 **`actor="reader"` 하드 오버라이드**(2026-07-10 `save_candidate` 봉인과 동일 패턴). dry_run 응답이 노출하는 `confirm_expected` 를 모델이 재현해도 사람 승격이 되지 않으며, 사람 앵커(키보드 `SAVE n`·CLI TTY) 없는 MCP write 는 **fail-closed(`G4_no_auto`)**. owner 정당 저장/기각/교체는 CLI 경로(TTY 신뢰) 그대로. `autonomous_agent_preview_then_confirm` 재현 회귀 테스트 추가(`*_preview_then_confirm_BLOCKED`), 오해 소지 주석·SECURITY.md 정합.

### Changed
- **CI**: `selftest` 잡에 Python 버전 매트릭스(Linux 3.10/3.12/3.13) + macOS/Windows 대표버전 smoke 추가(`requires-python>=3.10` 증명). `typecheck-workers` `npm install`→lockfile 있으면 `npm ci`. 데모 회귀 스텝 추가.
- **문서**: `SECURITY.md` 승인 경계 설명을 대역 외 앵커·MCP fail-closed 로 정밀화 + "검증되는 불변식(테스트로 강제)" 추가. `INSTALL.md` 버전 하드코딩 제거(releases/latest 참조).

## v1.18.3 — 문서/문구 정밀화 + 적중률 학습 재설계(발화 앵커) (2026-07-10)

### Fixed
- **적중률 학습 갭 해소(hit_events n=1)**: owner 실시간 지적("산으로 간다"·"그대로다"·"틀렸어")이 적중률 장부로 자동으로 흐르지 않던 근본 갭 해소. 원인 = `user-prompt-learn-outcome.js` 가 (1)"맞네/틀렸어" 명시 리액션만 잡고 (2)직전 turn 에 `recall` tool_use 가 있어야만 큐잉(`scan.found`) → owner 지적 대부분(recall 무관 작업 정정)이 0건 매칭.
  - **recall 커플링 제거** + owner 지적/정정 패턴 확장("산으로"·"다시 봐"·"안 고쳐"·"그대로다"·"왜 안") + `recall_linked` 구분. 과대포착은 `SHORT_LEN`/`HEAD_WINDOW` 게이트 + 큐→owner 승인 소비가 방어.
  - `hit_recording.mark_outcome_uttered`: **발화 앵커**(`utter:<sha16>`)로 hit/miss 직접 기록 — `hit_events.node_id` 는 nodes FK 가 아닌 자유 TEXT 이고 `both_sides` 는 speaker 별 outcome 개수만 세므로 노드 생성 없이 owner 적중률에 반영. 위조차단 = UserPromptSubmit hook(사람만·AI 위조 불가) + owner 승인(actor=human). dup 가드로 이중계상 0.
  - `learn_consume`: `recall_linked=false`(recall 무관) 항목을 발화 앵커로 소비. selftest hit_recording 11/11 · learn_consume 9/9 · e2e(지적 3건 → owner 적중률 분모 반영).

### Changed
- **pyproject Release 링크 최신화**: stale `v1.16.0` → `releases/latest`(자동 최신) + `Changelog` 링크 추가.
- **README 저장 문구 정밀화**: "자동 저장 절대 없음"(절대문구) → 기록/인정 2트랙 정합("기억할 만한 말은 임시 후보에 자동 / 장부 **확정**은 내 승인만"). "변조 감지" → "손상·변조 감지"(우발/부분 손상 탐지·장부 직접 접근자는 위협모델 밖).

### Added
- **SECURITY.md**: 취약점 신고 채널 + 위협모델 명시 — in scope(민감정보 차단·승인 없는 확정 방지·우발/부분 변조 감지·클라우드 읽기 마스킹) · out of scope(장부 직접 쓰기 권한 주체·다중 사용자 서버).

## v1.18.2 — 자동수집 부활(scope 회귀 봉합)·세션 마무리 정규화·대화 덩어리 veto (2026-07-10)

### Fixed
- **자동수집 0 부활**: `init_profile` 이 `capture_scope.json` 을 무조건 덮어써(2026-07-09 회귀) owner 커스텀 allow/deny 가 소실 → 자동수집이 조용히 멈추던 문제 봉합. init 멱등 병합(기존 scope 보존 + `.json.bak` 백업)으로 재발 방지. 명시 저장 신호("빙구팩 저장해"·"이거 저장해")는 중립 cwd allow 미스에서도 우회 통과(deny·비활성은 존중).
- **세션 마무리 감지 정규화(N3)**: `detect_session_close` 를 NFKC+casefold+isalnum 정규화 후 등록 표현 × 접미 유한폐포 membership 으로 교체 — 구두점·전각·공백·붙여쓰기 변형 흡수, 부정계("세션 마무리 안 해")·선행 자유텍스트 오발동은 구조적 차단.

### Added
- **대화 덩어리/붙여넣기/AI 응답문 자동 제외(bulk veto)**: 자동수집 candidate 가 긴 붙여넣기·AI 응답문으로 오염되던 문제(실측 55%) 차단. 길이 단독이 아닌 **길이(>300자) + 줄바꿈 밀도(≥3)**, 또는 초장문(>2000자) 게이트 — 줄바꿈 없는 장문 판단은 보존(문장 절단 회귀 없음). 명시 저장은 우회(owner 의도). 제외 건수는 세션 마무리 preview 에 "긴 발화 n건 제외"로 노출(무음 폐기 방지 — 명시 저장으로 회수). C(문장 발췌)는 AI 응답문을 owner 판단으로 둔갑시키는 화자축 오염이라 기각. `binggupack/capture/buffer.py`(메모리)·`binggu_capture_persist.py`(영속) 대칭.

## v1.18.1 — MCP 표준 호환(Codex)·자동 스코프·신규 사용자 온보딩 정비 (2026-07-09)

### Fixed
- **MCP tools/list 표준 준수**: tool 응답의 비표준 최상위 필드(`mode`·`path_params`) 제거 → `name`/`description`/`inputSchema` 만. Codex 등 엄격한 Rust(rmcp) 클라이언트가 unknown field 로 도구 캐시 생성 실패(`has_cached_tools=false`)하던 문제 해결(Claude Code 는 관대해 기존에도 동작).
- **scripts selftest 클라우드 격리**: `cloud_recall` selftest 가 `~/.claude.json` opencrab-cloud 폴백을 타 실 클라우드를 치던 격리 결함 봉합(`BINGGU_CLOUD_MCP_NO_FALLBACK` 가드 — server_handlers selftest 와 대칭·GATE NO-GO→GO).

### Added
- **stdio MCP 진입점 `openbinggu-mcp-server`**: pip 설치 후 clone 없이 stdio MCP 등록 가능(`openbinggu-mcp-server --serve <ROOT>`).
- **`cloud_search`·`cloud_recall` 개인 온톨로지 자동 스코프**: 미지정 호출이 config 접두어("Binggu Person")로 자동 스코프 — 세션 중 사용자 온톨로지가 검색에 자동으로 붙음(id churn 면역·telemetry 노출).
- **개인 온톨로지 팩 파이프라인 편입**: `scripts/person_pack_{assemble,split_upload,daily_sync}.py`(`~/.claude/memory` → 90문서 sticky 분할·`owner_label` 개인화). 통합 팩 chunk 유실 복구(4파트 분할).

### Changed
- **MCP 30도구**(24→30): `cloud_search`·`why`·`contrast`·`abstraction`·`mark_hit`·`mark_miss` 노출·`_TOOL_DESC` 설명 보완(read 20 · dry-run 2 · write-gated 8).
- **`cloud_search` 하이브리드 의미검색**: OpenCrab 서버가 저장된 openai-1536 임베딩을 낮은 가중 fusion 으로 배선(2026-07-08 · `vector_candidates` 0→32). 빙구팩 `cloud_search` 는 로직 변경 0 으로 hybrid evidence 를 그대로 수신. 질의확장(원 질문 3~6 동의어)은 현 fusion 벡터 가중이 낮아 lexical recall 보강용으로 여전히 권장 — 벡터 가중 상향은 서버측 튜닝 사안.
- **신규 사용자 온보딩 문서 정비**: Codex(`~/.codex/config.toml`) 등록 예시 · named tunnel 고정 주소 절차 · cloudflared 설치 · 커넥터 URL 2종 구분표(save_mcp `/mcp2` vs webmcp `/mcp`) · `binggu onboard`=clone 필요(hosted 는 pip 미번들) 명시.

## v1.18.0 — CrabAgent 스키마 팩 · 사용자 온톨로지 자동 동기화 (2026-07-06)

- **CrabAgent 스키마 팩 wire `crab_pack_wire`**: 데이터 폴더 → 개념/주장/증거 계층 Cloud Pack v1 ZIP 빌드(stdlib·PII leak fail-closed·원본 문서 미포함) + crab-agent 업로드(기본 dry_run·`BINGGU_CRAB_UPLOAD=1`+confirm 게이트·statement timeout 세션 재발급 재시도·한글 pack_name ASCII 자동 변환). 서버 재추출(문서당 13노드 상한) 경로를 대체. selftest 15케이스. *(오픈크랩 Expert 요금제 필요)*
- **사용자 온톨로지 자동 동기화 `person_crab_sync`**: owner 확정 판단(T3 통과분)을 스키마 팩으로 빌드해 같은 pack_name 제자리 교체 동기화. `person_pack.json "crab_auto_sync": true` 옵트인 시 auto-pull(5분)에 편승해 무인 갱신(변화 없으면 NO_CHANGE·네트워크 0). 보조 문서 계층(`<home>/person_pack_sources/` — 경로 마스킹·PII 잔존 문서 통째 제외) + 문서 예산 초과 시 무손실 묶음 병합 + `crab_chunk_cap` 설정. selftest 11케이스.
- **CI 타입검사 게이트**: hosted/workers TS 11파일 `tsc --noEmit`(strict) — tsconfig + typescript/@cloudflare/workers-types 도입, 로컬 0건 확인 후 게이트화.
- **v2 save worker 폐기 확정**: `binggupack-save-intent-v2`(6/12 배포·7/3 save_mcp로 대체) — 시크릿만 남은 방치 표면 제거. CF 삭제는 owner 직접 실행(`wrangler delete --config wrangler.save_v2.prod.toml`). v1(binggupack-save-intent-local)은 CF에 이미 없음. 코드 파일은 이력·DO 클래스(라이브 import) 보존.
- **README 기능 표 재구성**: 기능명 중심 카드 표(주요 기능 12종 + 안전장치 5종) — 비전문가가 한눈에.

## v1.17.0 — MCP 24도구 · 웹/앱 커넥터 · ChatGPT 저장 채널 · 원클릭 온보딩 (2026-07-05)

로컬 MCP를 8→**24도구**로 전면 확장하고, 웹/앱 커넥터(HTTP 모드)와 ChatGPT 채팅 저장 채널을 열고, 신규 사용자 원클릭 온보딩과 backup/export/restore 데이터 주권을 붙였다. 저장은 여전히 사람 confirm(도구별 문구 정확 일치)만.

### Added
- **원클릭 온보딩 `binggu onboard`**: setup-cloud(읽기 worker) + 저장 채널(save_mcp — 키 48hex 자동생성·repo 밖 보관·secret stdin 주입·커넥터 URL 기본 마스킹 `--show-url` 옵트인) + auto-pull 스케줄러 + 웹 MCP 안내를 한 진입점으로(멱등·dry-run 기본·login/deploy 는 본인 손). `scripts/binggu_setup_save.py` selftest 36케이스.
- **`binggu restore`**: 백업 스냅샷으로 장부 교체 — `RESTORE <백업파일명>` confirm 정확 일치 게이트 + 교체 직전 `pre_restore_<ts>.sqlite` 자동 스냅샷(복구의 복구) + 원자 교체. backup/export 와 함께 데이터 주권 3종 완성.
- **웹 MCP 상시가동 스크립트 일반화 편입**: `scripts/start_binggu_web.py`(HTTP+quick tunnel·경로토큰 자동생성·로그에 토큰 0) + `scripts/register_webmcp.ps1`(경로 자동탐지) — 공개 터널 노출 결정은 사람 직접 실행.
- **CI ruff 정적 게이트**: `binggupack/` F-rules 0 을 3-OS CI 에서 강제(7/3 달성 상태 고정).
- **MCP 24도구 전면 노출** (기존 8 → 24): 조회 7종(`recall`/`preflight`/`trace_review`/`trace_show`/`status`/`list`/`reminders`) + 쓰기 3종(`pair`/`deprecate`/`replace` — dry-run 기본·confirm 정확 일치 시에만 실행) + 작업 4종(`reflect`/`harvest_list`/`harvest_add`/`harvest_remove`) + 클라우드 read 2종(아래). `harvest_run`(실 네트워크 수확) 등 위험 도구 15종은 MCP 노출 금지 유지.
- **클라우드 read 도구**: `cloud_recall`/`cloud_packs` — OpenCrab 클라우드 지식·팩 조회(read 전용·PII 마스킹·미설정 시 graceful 통과).
- **로컬 MCP HTTP 모드**: `openbinggu_mcp_server.py --http <PORT> <ROOT>` — Cloudflare Tunnel 뒤에서 Claude 웹/앱 커넥터에 로컬 24도구를 그대로 노출. 경로 토큰은 `BINGGU_MCP_PATH_TOKEN` env 주입(코드 평문 0).
- **ChatGPT 채팅 저장 채널**: ChatGPT 대화 중 `SAVE n` 승인분만 hosted inbox에 적재 → PC가 서명키로 pull → 로컬 장부 반영. 자동 pull 스케줄러 스크립트(`scripts/auto_pull_hosted.py`·`scripts/register_autopull.ps1`) 동반. confirm 없으면 저장 0.
- **채팅(MCP) 저장 화자 축**: hosted 저장 경로에 `speaker`(owner/ai) 전달 — 채팅 저장분도 사용자 온톨로지 팩 대상.
- `save --accept` / `pair --accept` — 저장+owner 확정을 한 번에. preflight에 양방향 신뢰도(hit_stats) 블록·owner 원칙 상시 노출 섹션 편입.
- 수확 확장: 탐색 깊이 기본 3→4, 현지어 수집 `lang` 파라미터(discover→오케스트레이터→CLI), 수집→적재 정제기+관련성 게이트 연결.

### Changed
- **`save_candidate` read-only 해제**: MCP 쓰기 방어선이 무조건 BLOCK(`G4_no_auto`) → confirm 게이트로 이동. `dry_run=false`라도 confirm 부재/불일치면 REJECT(`confirm_phrase_mismatch`)·write 0, `SAVE n` **정확 일치**(사람 승인 증거) 시에만 human 승격 실 저장. 자동 저장 0 불변.
- **smoke test 10→11 케이스**: 케이스 9(confirm 부재→REJECT)/9b(정확 confirm→격리 홈 실 저장) 분리 — confirm-gated 방어선과 정합.
- 코어 이관(strangler) 대규모 진행: 배치 1~6으로 30+ 모듈 `binggupack/` 정본화(스키마 단일화·경로 중앙화·PII 정본·pytest 래퍼), `scripts/`는 thin shim 유지. 전체 회귀 selftest 30/30 유지.
- 문서 재구성: ARCHITECTURE 신설, 결과보고 44건 `docs/_archive/` 이동, README 비전문가용 재구성(온톨로지 6단계 파이프라인 설명 포함).

### Fixed
- CI `MCP Cross-Platform Install`(ubuntu/macos/windows 3-OS) 초록화 — smoke 케이스 9를 confirm-gated 방어선에 정합.
- hosted `save_intent`: SSE transport 지원 + Origin 가드 allowlist 완화(claude.ai/chatgpt 커넥터 허용) + 쓰기 도구 설명 명확화.
- 자동 pull/push 스케줄러 무인 실행 시 cmd 창 깜빡임 제거.
- P4 label tie-break 허위실패, gate21/24 격리 오탐·잠재 write 봉합, 파서 temp 디렉토리 누수 차단, 수확 탐색 픽스(branch_explorer 모델명·재귀 다방향·`--rel-min` 기본 0.0·ollama timeout 확대).

### Security
- 적대적 검증(Fable5) findings 6건 수정: T3 필터 다국어 우회(영어·한자·NFKC 정규화) 차단, cloud wire 전 필드 검사+content 추출 실패 fail-closed, actor 게이트 실소비, 한국 주소/이름 마스킹 보강, 델타 업로드 `uploaded_hashes` 보존.
- hosted `save_intent` **PII 백스톱 reject** + 무인 pull PII skip — 채팅 저장 채널에 PII 이중 방어. legacy `save_intent_v2` 서명 채널(intent)에도 동일 백스톱 패리티 적용.
- `person_pack_sync` 팩 지정 일반화: env > `<home>/person_pack.json` > 기본값 — 사용자별 온톨로지 팩 지정 경로 확장.
- T3 하드제외 필터 + ingest 파이프 게이트 — 온톨로지 팩 업로드에서 PII·개인사 반출 절대금지.
- tree-scan 위생: selftest fixture PII 리터럴을 런타임 조립로 바꿔 정적 스캔 CLEAN.

### Verified
- `smoke_test.py` **11/11 PASS** 실측(temp home·`operating_ledger_write_0` PASS·운영 ledger 불변).
- MCP 노출 도구 **24종** — `binggupack.mcp` TOOLS 레지스트리 실측(read 16 · dry-run 2 · write-gated 6), 위험 도구 15종 노출 금지 확인.
- CI 3-OS clean-install green(케이스 9 정합 후).

## v1.16.0 — 외부 리뷰 5건 정리: PII scan 버그·툴체인·브랜드 통일 (2026-06-30)

preview/capture 분류를 단일 SSOT 게이트로 통합(자동 preview는 판단/교훈/선호/규칙만 후보), 명시 입력(`remember`/`pair`)은 판단-veto 면제(안전 게이트 유지), 100문장 golden 하네스 + doctor 회귀망 확장, `binggupack.storage`/`binggupack.mcp` facade(strangler 1단계). 더해 외부 리뷰 5건(PII scan 버그·툴체인 점진 도입·브랜드 통일)을 정리했다.

### Fixed
- 공개 트리 PII scan이 빌드 산출물(dist/ build/ *.egg-info/)을 read_error 로 잡아 package_cli_selftest 후 같은 트리에서 BLOCK 되던 개발 UX 버그 수정 (scan ignore 패턴 추가)

### Added
- 표준 개발 툴체인 점진 도입: ruff/mypy/pytest 최소 설정(binggupack/ 패키지 한정) + requirements/dev.txt. 기존 자체 selftest 게이트와 공존.

### Changed
- 브랜드 통일: 사용자 대면 docs(OPENBINGGU_*.md → BINGGUPACK_*.md) 및 문서 본문 OpenBinggu→BingguPack. 외부 인터페이스(MCP 서버명 openbinggu-local, 스키마 $id, OPENBINGGU_* 환경변수)는 호환 유지.

### Safety
- scripts/openbinggu_*.py 내부 파일명·MCP 진입점·공개 스키마 $id URL 미변경 (기존 설치 호환성 보존).

### Verified
- 전체 회귀 selftest 30/30 PASS (REGRESSION=GO) — 브랜드 docs rename(31)·코드주석/메타 참조 정리(20)·툴체인·v1.16.0 bump·PII scan 수정 후 회귀 0.
- `openbinggu_doctor --selftest` GATE GO (21/21 + 12/12, 운영 ledger write=0·mtime 불변).
- PII tree-scan 재현 검증: build 산출물(dist/build/*.egg-info) ignore 실증 + 일반 소스 PII 탐지 능력 유지(BLOCK).
- `version_consistency_selftest` 3/3 (pyproject == __about__ == 1.16.0).
- 외부 인터페이스 보존 실측: MCP 서버명 `openbinggu-local`·`OPENBINGGU_*` 환경변수(10건)·`openbinggu_pack_contract.schema` $id(9건) 미변경.

### Known follow-up
The current release introduces `binggupack.storage` and `binggupack.mcp` facades as the first strangler step. Implementation logic still remains in `scripts/` for compatibility and release stability. Phase 2 will migrate storage/MCP logic into package modules incrementally while keeping script shims. (마일스톤 blocker: `docs/BINGGUPACK_NEXT_IMPLEMENTATION_CANDIDATES.md`)

## v1.15.0 — owner 발화 a0 형식게이트 면제 (2026-06-27)

owner(사용자 본인) 발화는 구어체·짧은 직감이어도 자연어 원문 그대로 보존한다. a0 형식 게이트(`node_1_word`/`node_1_meaning` = 단어·비종결·짧음)는 "owner 직감 검열·자동폐기 금지" 원칙으로 면제한다.

### Changed
- `_pick_one_node`: a0 형식 FAIL(`node_1_word`/`node_1_meaning`)을 **speaker=owner일 때만** 면제(요약/번역 없이 원문 저장). ai/reader/None은 기존대로 `a0_fail`.

### Safety (불변 — 회귀로 고정)
- PII/secret 거부, `G4_no_auto` BLOCK, actor/confirm 게이트, temp_only/ledger 경계 전부 미변경.
- 신규 characterization test `binggu_speaker_owner_a0_exempt_characterization_selftest.py`(7/7): owner 면제 발동 · owner-scoped(ai/reader/None 비면제) · 정상문장 무관 고정.

## v1.14.0 — 화자 페어 양방향 + MCP 크래시 수정 (2026-06-26)

화자 축을 **대화 주고받음**으로 완성 — 누가 먼저 말했고 누가 반응했는지(시간 순서·관계 방향)를 페어 엣지에 담는다. `save --speaker` CLI로 "빙구팩 저장해" 흐름에 화자 칸 연동.

### Added
- **양방향 페어 엣지**: 기존 `ai_*`(AI가 사용자 발화에 반응)에 더해 `owner_*`(사용자가 AI 발화에 반응) 추가 — `owner_accepts`/`owner_refutes`/`owner_revises`. `binggu pair … --by <ai|owner>`로 선택. relation prefix가 **반응 주체(source)** = [먼저 말한 사람]→[반응한 사람] 시간 순서·방향이 엣지에 보존된다.
- `binggu save … --speaker <owner|ai>` — 단건 저장에도 화자 칸 연동. owner는 **사용자 자연어 원문 그대로**(요약·번역 금지), ai는 AI 요약.

### Fixed
- MCP `save_candidate` 실 write 크래시 — `snap_dir` 미생성으로 `snapshot→copy2` FileNotFoundError가 stdio 루프를 죽이던 문제(`os.makedirs` 1줄). dry-run/조기 BLOCK 경로는 영향 없었음.
- **owner 발화 a0 형식게이트 면제** — owner 노드는 자연어 원문 그대로 담아야 하는데(화자축 본질) a0 정본 게이트가 명제형만 통과시켜 구어체·의문문 사장님 발화가 `node_1_meaning`/`node_1_word`로 막히던 문제. `speaker=="owner"` 한정으로 형식 게이트만 면제("owner 직감 검열·자동폐기 금지" 원칙 확장). PII/secret/`G4_no_auto` 안전 게이트는 그대로 강제, ai 발화는 명제형 강제 유지.

### Safety
- 양방향이어도 헌법 불변: `G4_no_auto`(actor=human) 게이트·candidate-only·PII 제외·원자성(단일 pack)·dangling 방지. 자동 저장 0.

### Verified
- selftest 전수 GO: binggu 40/40·candidate_save 양방향 페어 e2e(`owner_revises` 실저장 PASS)·운영 ledger 무손상.

## v1.13.0 — 자기진화 거버넌스 (2026-06-26)

빙구팩 학습과 기존 규칙(강제조항) 충돌 시 — 빙구팩은 제안·기록만, 규칙 변경은 사람. **self-modifying 0**(거버넌스 자산 write 0).

### Added
- 1단 대비(`binggu_contrast_protocol.py`): 학습↔규칙 충돌→중립 대비표·원문 본문 봉인(contrast_snapshot)·사람 선택·자동결정 0.
- 2단 적중률(`binggu_hit_stats.py` 확장): 비인과 불변식(assert_not_ranking_input)·domain 분리·signal_only.
- 2단 무결성(`binggu_merkle_anchor.py`): atomic 봉인·full64·외부 raw 재계산·누락 fail-closed.
- 2단 정책(`binggu_policy.py`+`policies/binggu_policy.json`): REQUIRED_IMMUTABLE 안전 화이트리스트·pin fail-closed·style-disguise 차단.
- 3단 분리(`binggu_hit_export.py`): capability-removal·realpath 물리가드·raw export만(규칙 write 0).
- 세션 마무리(`binggu_session_close.py`): 모델 의미감지(키워드X)·사용자 등록 opt-in·저장 preview(저장 0).
- `docs/BINGGUPACK_GOVERNANCE_DESIGN.md` 신규 — 거버넌스 설계.

### Safety
- 5개 가드 코드 강제·통과: self-modifying write 0·대비표 원문봉인·적중률 비인과·Merkle atomic·정책 REQUIRED_IMMUTABLE.
- 3라운드 4cli 토론 결론(빙구팩이 거버넌스 진화에 같은 런타임 관여 시 메모리 격리 없이 무결성 불가) → self-modifying 회피.

### Verified
- selftest 전수 GO: hit_stats 12/12·merkle 13/13·hit_export 8/8·staging 17/17·binggu 40/40·contrast·policy·session_close.
- 운영 ledger 마이그레이션 무손상(291노드·verify_tail_state/chain True·백업)·governance_write_zero.

## v1.12.0 — Personal speaker axis (2026-06-26)

사용자 발화(owner)와 AI 요약(ai)을 따로 저장하고 연결하는 화자 축. 빙구팩이 "AI 작업일지"가 아니라 **사용자 본체**를 쌓게 하는 핵심.

### Added
- `nodes.speaker`(owner/ai/NULL) 비파괴 `ALTER` — 사용자 발화와 AI 요약을 독립 노드로 구분 저장. 기존 노드 NULL 보존.
- `save_paired(owner_text, ai_text, relation_kind)` — owner/ai 페어 저장 + 동사형 엣지(`ai_accepts`/`ai_refutes`/`ai_revises`). owner 단독(순수 직감) 허용. 단일 pack `staging_apply`(원자성), 페어 dangling 방지(`pair_partial_exists`).
- `binggu_hit_stats.py` + `hit_events` 테이블 — 양방향 신뢰도(owner 직감/ai 반박 적중률 별도 분모·시간감쇠 반감기 30일·표본게이트 N<5·`both_sides` 균형). 맹종 아닌 참고 가중치.
- CLI: `binggu pair` / `binggu trust`(read-only) / `binggu route`(저장 의도 신규/수정/결과 안내) + `binggu resolve` 신뢰도 연동.
- `docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md` — 화자 축 설계 문서.

### Fixed
- `store_checksum` 위치 비의존화(speaker 컬럼 제외 명시 projection) — `ALTER` 후 기존 운영 ledger audit anchor와 어긋나 `verify_tail_state`가 정상 노드를 변조로 오판하던 함정 해소. `verify_chain`만으론 미검출.

### Verified
- selftest 전수 GO: binggu 40/40 · candidate_save 19/19 · staging 16/16 · deprecate 23/23 · replace 19/19 · promote 17/17.
- 운영 ledger 마이그레이션: speaker/hit_events 추가 후 **291노드 무손상** · `verify_tail_state`/`verify_chain` True · 백업 동반.
- 헌법 5항(candidate-only/`G4_no_auto`/PII/사람 confirm/전 엣지 evidence) **위반 0** — 최종 검증 워크플로우 확인.

### Notes
- 4cli 토론(방향 채택 + 9불변식) + 6축 충돌분석(STOP→선결과제 해소) 거쳐 구현.
- publish 게이트(SUPPORTS→`ALLOWED_RELATIONS`·cloud export 단계)·기존 노드 speaker backfill은 선택 사항으로 남김.

## v1.10.0 — Installable MCP Package (stable) (2026-06-25)

`v1.10.0-rc.1` 을 stable 로 승격. RC 기능은 그대로이며 cross-platform 검증과 MCP tool exposure 검증을 통과해 stable line(main)으로 병합했다.

### Added (RC 대비)
- `.github/workflows/mcp-cross-platform-install.yml` — ubuntu/macos/windows 3-OS clean-install CI. 각 job: tag clone → `smoke_test.py` 10/10 → installer dry-run → operating-name protection refusal. **전 OS PASS** (run 28138244904).
- `docs/BINGGUPACK_V1_10_0_RC1_CROSS_PLATFORM_VALIDATION.md` — cross-platform 검증 기록.

### Verified (stable gate)
- `MCP_TOOL_EXPOSURE_PASS` — 재시작 세션에서 sandbox MCP(`openbinggu-local-sandbox`, `BINGGU_HOME` 격리) 8도구 live 노출 + 전부 `ALLOW`.
- `save_candidate(dry_run=false)` actual save → `G4_no_auto BLOCK` (`executed_write=false`, `ledger=temp_only`).
- 운영 home `ledger.sqlite`/`-wal` 불변, production write 0, OpenCrab ingest 0, G4 우회 0.

RC 의 전체 기능 목록은 아래 `v1.10.0-rc.1` 항목 참조.

## v1.10.0-rc.1 — Installable MCP Package and Workflow Factory (2026-06-24)

신규 사용자가 **clone만으로 BingguPack MCP를 설치**할 수 있게 설치 경험을 완성.

### Added
- `scripts/install_claude_mcp.py` — `claude mcp add` 헬퍼. repo root 자동 감지, server.py 경로 계산, `BINGGU_HOME`/`OPENCRAB_HOME`/`XDG_CACHE_HOME` 주입, `--dry-run`/`--apply`/`--name`/`--home`/`--sandbox`/`--force`, 동일이름 가드, **운영 엔트리(`openbinggu-local`) 보호**(거부), Windows `claude.cmd` shim 처리(`shutil.which`).
- `scripts/smoke_test.py` — clone 직후 오프라인 검증. 8도구 + save gate(G4_no_auto) + 운영 home 불변 = 10 checks. 실존 fixture(`examples/toy_project/`) 사용.
- `pyproject.toml` — 패키지 메타(version, stdlib-only).
- `docs/BINGGUPACK_MCP_CLEAN_INSTALL_E2E_TEST_REPORT.md` — clean install E2E 결과.

### Notes
- BingguPack MCP 서버는 **이 본체 repo(`darkjokee-arch/binggupack`)** 에서 설치된다(OpenCrab repo 아님).
- `BINGGU_HOME` 으로 sandbox/운영 home 분리. 미설정 시 OS별 `~/.binggupack`.
- AI/reader actor 의 실저장은 `G4_no_auto` 로 차단. 저장은 사람 actor 의 `SAVE n` 승인 게이트에서만.
- actual API collection 은 release requirement 가 아니다. insane-search 는 optional evidence discovery adapter. production write 는 기본 0.
- OpenCrab repo 의 `v1.8.1-rc.1` 작업은 임시 구현/검증본 → 본체 repo 로 정렬·이관 완료.

## v1.9.0 — 확정→폰/웹 자동 공유 + setup-cloud (2026-06-16)
## v1.8.0 — 똑똑한 뜻 분류 자동 켜짐 + 첫 설치 환경 점검 (2026-06-15)
## v1.7.0 / v1.6.1 / v1.6.0 / v1.5.x / v1.4.x — 이전 릴리스 (GitHub Releases 참조)
