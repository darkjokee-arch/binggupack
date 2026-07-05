# Changelog — BingguPack

## Unreleased

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
