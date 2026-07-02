"""binggupack.safety.pii — PII redaction 정본(canonical) 지정 facade.

문제: redact 진입 함수가 코드 전반 8곳(redact_text·_redact_token·_redact_all·
_scrub_node·_pii_present·batch_redact·scan_residual_pii·redact_and_validate)에 산재해
어느 것이 정본인지 코드상 불분명했다. 정규식 정본은 이미
scripts/watcher_batch_m1.py 의 batch_redact / scan_residual_pii 이고 harvest·t3_filter
가 이를 재사용한다(복붙 아님). 이 모듈은 **그 정본을 한 곳에서 재노출(re-export)만**
하여 canonical 위치를 명확히 한다.

원칙(이 트랙 한정):
  - 정규식/판정 로직 **이동·수정 0**. import 재노출만(회귀 위험 차단).
  - 방금 보안수정으로 watcher_batch_m1 에 한국 주소/이름 shape 이 추가된 상태 →
    로직을 옮기면 그 수정이 깨질 수 있으므로 참조 통일만 수행한다.
  - 나머지 6개 진입 함수(redact_text·_redact_token·_redact_all·_scrub_node·
    _pii_present·redact_and_validate)는 이 트랙에서 건드리지 않는다(다음 이관 대상).

공개 API (정본 위치: scripts/watcher_batch_m1.py):
  - batch_redact(text, field_name="") -> (redacted:str, hits:int, review_flag:bool)
        secret(mvp1.redact_text 무수정) + PII 복합판단(shape+문맥+whitelist/denylist).
  - scan_residual_pii(text) -> list[str]
        산출물 잔존 PII/secret 형태 탐지. kind 이름만 반환(raw 값 미반환).

주의: 정본 watcher_batch_m1 import 는 sibling 스크립트(mvp1/mvp2/edge/op_m0 등)를
불러오므로 상대적으로 무거운 import 다. 그래서 이 모듈은 서브모듈 import 전용으로 두고,
binggupack.safety.__init__ 에는 노출하지 않는다(패키지 import 비용 회귀 방지).
"""
import os
import sys

# scripts 정본 경로를 import 가능하게(strangler 현 단계 한정 — 로직 이관 시 제거 예정).
_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from watcher_batch_m1 import batch_redact, scan_residual_pii  # noqa: E402,F401

#: 정본(canonical) 모듈 위치 — 문서/디버깅용 상수.
CANONICAL_MODULE = "scripts/watcher_batch_m1.py"

__all__ = ["batch_redact", "scan_residual_pii", "CANONICAL_MODULE"]
