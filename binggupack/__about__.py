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
"""
__version__ = "1.18.1"
