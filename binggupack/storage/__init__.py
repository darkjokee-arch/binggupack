"""binggupack.storage — 저장 정본 facade (트랙 C strangler, C1).

목적: CLI/MCP 등 호출자가 scripts/ 의 개별 파일을 직접 import 하지 않고 이 facade 하나만 보게 한다.
지금은 **scripts 정본을 재노출만** 한다(동작 변경 0). 정본 코드를 이 패키지로 옮기는 일은 C2~ 에서
한 모듈씩 점진(strangler) — facade 의 공개 이름은 그대로 유지하므로 호출자는 안 바뀐다.

회귀 가드: tests/storage_characterization.py (save_selected 현재 동작 고정) + doctor.

공개 API (시그니처는 정본 구현과 1:1 — 낡으면 소비자가 인자 존재를 모른 채 호출한다):
  - save_selected(db, text, indices, ctx, snap_dir, due_date=None, speaker=None,
                  explicit=False, origin=None)
      origin: 앞막이 출처 dict(source_id|src_id|transcript_path|session_id|turn_uuid|src_sha).
      미지정이면 원문 발화 자신을 좌표계로 삼는 폴백(`utterance:<hash>`)이 되어 등급이 T2 로
      내려간다 — 세션 좌표가 있으면 반드시 넘길 것(정본: openbinggu_conversation_candidate_save).
  - save_paired(db, owner_text, ai_text, ctx, snap_dir, relation_kind="ai_accepts",
                owner_pick=1, ai_pick=1, due_date=None, owner_origin=None, ai_origin=None)
      화자 축 페어 저장(owner/ai) — owner_origin/ai_origin 은 각 화자 발화의 앞막이 출처.
  - commit_selected(db, text, preview_id, picks, confirm, snap_dir, ...)
  - open_g3(path)            장부(ledger) 열기
  - set_review_due(...)      검증 예정일 설정
"""
import os
import sys

# scripts 정본 경로를 import 가능하게(현 단계 한정 — C2~ 이동 시 제거 예정).
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from openbinggu_conversation_candidate_save import save_selected, save_paired
from binggu_capture_to_save import commit_selected
from openbinggu_deprecate_and_remind_g3 import (
    open_g3, set_review_due, resolve_review, list_due_reminders)
from binggupack.paths import OPERATING_PATHS  # 정본(셀프테스트 결합 해소)

__all__ = ["save_selected", "save_paired", "commit_selected", "open_g3",
           "set_review_due", "resolve_review", "list_due_reminders", "OPERATING_PATHS"]
