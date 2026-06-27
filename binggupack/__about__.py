"""Single source of version truth for BingguPack.

v1.13.0(자기진화 거버넌스) 위 v1.14.0 — 화자 페어 양방향(owner_*/ai_* — 누가 먼저
말하고 누가 반응했는지 시간 순서·방향) + speaker CLI(save --speaker) + MCP
save_candidate 크래시 수정(snap_dir) + owner 발화 a0 형식게이트 면제(구어체 원문
보존·안전게이트 불변) + post-S4 release readiness docs. main 반영 완료.
(GitHub release tag 는 owner 결정 — version 파일은 main 코드 상태를 반영한다.)
"""
__version__ = "1.14.0"
