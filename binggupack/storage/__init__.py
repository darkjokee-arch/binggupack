"""binggupack.storage — 저장 정본 facade (트랙 C strangler, C1).

목적: CLI/MCP 등 호출자가 scripts/ 의 개별 파일을 직접 import 하지 않고 이 facade 하나만 보게 한다.
지금은 **scripts 정본을 재노출만** 한다(동작 변경 0). 정본 코드를 이 패키지로 옮기는 일은 C2~ 에서
한 모듈씩 점진(strangler) — facade 의 공개 이름은 그대로 유지하므로 호출자는 안 바뀐다.

회귀 가드: tests/storage_characterization.py (save_selected 현재 동작 고정) + doctor.

공개 API:
  - save_selected(db, text, indices, ctx, snap_dir, due_date=None, speaker=None)
  - save_paired(...)         화자 축 페어 저장(owner/ai)
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
from openbinggu_deprecate_and_remind_g3 import open_g3, set_review_due

__all__ = ["save_selected", "save_paired", "commit_selected", "open_g3", "set_review_due"]
