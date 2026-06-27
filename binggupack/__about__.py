"""Single source of version truth for BingguPack.

v1.14.0(화자 페어 양방향 + MCP save_candidate 크래시 수정 + speaker CLI + post-S4
release docs) 위 v1.15.0 — owner 발화 a0 형식게이트 면제: speaker=owner 이고 a0 형식
FAIL(node_1_word/node_1_meaning = 단어·비종결·짧음)일 때만 면제해 구어체·짧은 직감을
원문 그대로 보존. PII/secret·G4_no_auto·actor/confirm 안전게이트는 불변. main 반영 완료.
(GitHub release tag 는 owner 결정 — version 파일은 main 코드 상태를 반영한다.)
"""
__version__ = "1.15.0"
