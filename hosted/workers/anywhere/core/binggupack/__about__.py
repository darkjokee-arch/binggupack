"""Single source of version truth for BingguPack.

v1.15.0(owner 발화 a0 형식게이트 면제) 위 v1.16.0 — 외부 리뷰 5건 정리: 공개 트리 PII
scan 이 빌드 산출물(dist/·build/·*.egg-info/)을 read_error 로 잡던 개발 UX 버그 수정,
표준 개발 툴체인(ruff/mypy/pytest 점진 도입·binggupack/ 한정), 사용자 대면 문서 브랜드
통일(OpenBinggu→BingguPack)이 핵심. 외부 인터페이스(MCP 서버명·스키마 $id·OPENBINGGU_*
환경변수·scripts/openbinggu_*.py 파일명)는 호환 유지. main 반영 완료.
(GitHub release tag 는 owner 결정 — version 파일은 main 코드 상태를 반영한다.)

v1.17.0 — MCP 24도구 전면 노출·웹/앱 커넥터(HTTP 모드)·ChatGPT 저장 채널(inbox→PC pull)·
원클릭 온보딩(binggu onboard: setup-cloud+저장채널+auto-pull)·backup/export/restore
데이터 주권·CI ruff 정적 게이트. 저장은 여전히 사람 confirm(도구별 문구 정확 일치)만.

v1.18.0 — CrabAgent 스키마 팩 경로(crab_pack_wire: 개념/주장/증거 계층 빌드+업로드·
leak fail-closed·기본 dry_run)·사용자 온톨로지 자동 동기화(person_crab_sync:
crab_auto_sync 옵트인·5분 무인 갱신·보조 문서 세척 병합·묶음·chunk_cap)·
CI 타입검사(tsc strict) 게이트. 오픈크랩 Expert 요금제 전제(crab-agent 업로드).

v1.18.1 — MCP tools/list 표준 준수(Codex/Rust rmcp 호환·비표준 최상위 필드 mode·
path_params 제거)·stdio MCP 진입점(openbinggu-mcp-server: pip 설치 후 clone 없이 등록)·
cloud_search/cloud_recall 개인 온톨로지 자동 스코프·MCP 30도구(24→30)·개인 온톨로지 팩
파이프라인 편입(person_pack_*)·신규 사용자 온보딩 문서 정비(Codex 등록·named tunnel·URL 2종).

v1.18.2 — 자동수집 0 부활(init_profile scope 무조건 덮어쓰기 2026-07-09 회귀 봉합·멱등 병합
재발방지·명시저장 중립 cwd 우회)·세션 마무리 감지 정규화(N3: NFKC+casefold 유한폐포 membership·
변형 흡수·부정계/선행텍스트 오발동 구조 차단)·대화 덩어리/붙여넣기/AI 응답문 자동 제외(bulk veto:
길이+줄바꿈 밀도·줄바꿈 없는 장문 보존·명시저장 우회·preview "n건 제외" 노출·화자축 오염 방지).

v1.18.3 — 문서/문구 정밀화(pyproject Release 링크 stale[v1.16.0]→releases/latest·SECURITY.md
위협모델 명시[로컬 개인도구·전권 접근자 out of scope·우발/부분 변조 감지]·README 저장 문구를
기록/인정 2트랙 정합["임시 후보 자동 / 장부 확정은 내 승인만"]·"변조 감지"→"손상·변조 감지")·
적중률 학습 재설계(owner 실시간 지적을 hit_events 로 흐르게: learn-outcome hook recall 커플링
제거 + 지적/정정 패턴 확장["산으로"·"그대로다"·"다시 봐"·"안 고쳐"·"왜 안"], hit_recording.
mark_outcome_uttered 발화 앵커[utter:<sha16>] hit/miss 직접 기록[nodes 불필요·both_sides 는
speaker 별 개수 집계], learn_consume 발화 앵커 소비 — hit_events n=1 영구 갭 해소, 위조차단은
UserPromptSubmit hook[사람만·AI 위조 불가]+owner 승인[actor=human]).

v1.19.0rc1 (Release Candidate · Consent-first, exact-bound AI memory) — P0~P1-B.1 누적을
첫 정식 릴리스 후보로 봉인. 핵심: (1) binggu demo(60초 오프라인 체험), (2) trusted approval
event(owner 로컬 승인으로 MCP mutation 정확히 1회 · exact operation/payload/ledger/version
바인딩 · one-time consume), (3) hosted intent→local approval→exactly-once crash-atomic bundle
commit(원문 자동삭제 0 · direct hosted write 0), (4) 승인 기원 계약(env·비대화형·confirm 문구·
actor 라벨은 권한 아님). 정직 경계: 로컬 TTY 는 L1 routing · shell/filesystem 병재 에이전트에는
하드 승인 권한 아님 · protected writer/verifier/trust root/detached signer 는 RFC only(미구현) ·
root/admin compromise 방어 주장 없음. PEP440 pre-release("rc1"): 안정 설치는 이 RC 를 받지
않는다(pip install --pre 필요). 향후 tag = v1.19.0rc1(하이픈 없음).

v1.21.0 — Codex 재감사(main 77/REFINE · PyPI 1.20.0 52/BLOCK) 6트랙 수정. 핵심:
(1) 의미분류 seed 를 binggupack/data/ 에 패키지 내장 — wheel 에 seed 가 빠져 설치본이
semantic enabled 로 표시하고도 규칙분류로 조용히 후퇴하던 결함 해소(silent fallback→경고),
(2) 설치본/소스 명령 자동 구분(invocation_prefix: 설치본 "binggu" / clone "python binggu.py"),
(3) binggu start 부작용 분리 — 기본 start=장부 생성만, capture hook/Claude settings 편집은
capture install(+--force)/--with-capture 옵트인 · owner sticky OFF 존중,
(4) mypy 3건+ruff invalid-noqa 정리 · path_safety 벤더 재동기, (5) help 일상/고급 그룹,
(6) CI 확대(typecheck-python mypy 게이트 · npm audit 비게이팅 잡 · dependabot 주간).
저장소 보안 강화 병행(secret scanning · push protection · branch protection 필수검사 13 ·
PyPI env admin bypass 차단). production 로직/스키마/의존성 변경 0(설치본 결함 수정·문구·게이트).

v1.23.0 — 회상 효용 축 재설계 + 긴 발화 저장(L-lane) + 저장 정직성. 핵심:
(1) **use-time AI 도장**(trace_stamp) — 회상이 도움됐는지는 쓰는 순간의 AI 가 가장 잘 안다는
owner 판단(2026-07-27)을 코드로. actor=ai_stamp 로 사람 도장과 분리 집계하고 owner 가 다르게
찍으면 덮어쓴다. 실측 이행률 7% → 67.6%(hook 물리 차단 + 세 화면 숫자 통일).
(2) **화면 통일**(2026-08-25) — `trace review` 가 include_ai_stamped 기본값 탓에 AI 도장분을
아예 안 보여줘, CLI(748건 미판정)와 세션 preview(73% 도장)와 status(표시 없음)가 같은 저장소를
보고도 다른 그림을 냈다. 요약 도장률 상시 표기 · `--all` · status 동일 줄로 봉합.
(3) **L-lane 긴 발화 저장** — 잘라야만 저장되던 것을 전문으로. 배치 SAVE 한 번에 긴 발화까지.
(4) **저장 정직성** — save-batch 기저장 재등장 차단(B-08) · 전건 실패 사유 표면화(조용한 실패 0) ·
owner 교정 캡처 수율 3중 수리(전문 저장 · 구조 신호 relation · TTL 소멸 계수기).
(5) **회상 잡음 억제** — 자동주입 판정 대상 상위 N + 관련도 하한(owner B안) · 판정 TTL(B-02) ·
정렬 동점 IDF 2차 키 · preflight_rel_min=0.25.
(6) py↔ts 골든 왕복 parity(divergence 6건 검출·4건 수리) · Claim label 80자 절단 해소.
저장 확정은 여전히 사람 SAVE 앵커만(자동저장 0 · 헌법 불변).

v1.24.0 — Paperthin 7개 선택 패턴을 별도 런타임 없이 BingguPack식 cognitive adapter로
통합. 읽기 전용 catchup, readchk/hate/sip/nba/factchk 순수 판단 계층, eval-only mandela,
recall→decision→action→outcome→next recall 폐루프 fixture가 핵심. SIP는 ephemeral 후보만
제안하고 factual evidence exact binding이 없으면 fail-closed한다. G4_no_auto·human approval·
immutable bundle·provenance·rollback은 불변이며 Paperthin runtime/hook/별도 SSOT는 도입하지 않았다.
"""
__version__ = "1.24.0"
