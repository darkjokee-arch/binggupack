"""safety — 안전 불변식 모듈. v1.11.0 save-gate 라인 S2 이관 시작.

현재: path_safety(MCP/local 경로 안전 게이트 — classify_path, write 0·raw 경로 미노출).
scripts/openbinggu_path_safety_gate.py 는 backward-compatible thin wrapper 로 유지된다.
G4_no_auto / PII / confirm gate 등 gate-critical 영역은 S4(별도 고위험 phase)에서 다룬다.
"""
from .path_safety import (  # noqa: F401
    classify_path,
    _path_id,
    _DENY,
)

