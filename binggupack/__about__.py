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
"""
__version__ = "1.17.0"
