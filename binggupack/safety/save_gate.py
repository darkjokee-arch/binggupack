# -*- coding: utf-8 -*-
"""binggupack.safety.save_gate — SAVE 게이트 facade (트랙 C strangler).

write/판정 4함수(gate_record·gate_human_for·write_last_preview·gate_record_from_prompt)
및 save-n 참조 바인딩 3함수(gate_record_ref·gate_human_for_ref·preview_ref_for_candidates)의
정본은 binggupack.safety.gate_log 다 → 여기서 그대로 재노출(동일 객체).
scripts/binggu_save_gate 는 이 정본을 re-export 하는 shim 이므로 identity 는 동일하다.

미이관 심볼(트리거 토큰 has_trigger_token·TRIGGER_TOKENS, PEP562 lazy 속성 GATE_PATH 등)은
아직 scripts/binggu_save_gate 잔류 → __getattr__ 로 접근 시점 lazy 위임(부트스트랩 지연).
star import 금지(binggu_save_gate 는 PEP562 __getattr__ 모듈) — 필요 이름만 명시.

server_handlers 는 write_last_preview 만 사용. 게이트 로직 미접촉(byte-identical).
"""
import os
import sys

from binggupack.safety.gate_log import (  # noqa: F401
    gate_record, gate_human_for, write_last_preview, gate_record_from_prompt,
    gate_record_ref, gate_human_for_ref, preview_ref_for_candidates,
    gate_home, gate_path, last_preview_path, GATE_WINDOW_SEC)

# 미이관 심볼(has_trigger_token·TRIGGER_TOKENS·GATE_PATH 등) 위임용 — 접근 시점 lazy 부트스트랩.
_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")


def __getattr__(name):  # noqa: D401 - PEP 562: 미이관 심볼은 scripts 정본에 위임
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    import binggu_save_gate as _sg
    return getattr(_sg, name)


# has_trigger_token·TRIGGER_TOKENS 는 위 __getattr__(PEP 562)로 scripts/binggu_save_gate 에
# 런타임 위임된다(정본 존재 확인: TRIGGER_TOKENS/def has_trigger_token). ruff 정적분석은 이
# lazy 위임을 못 봐 F822(undefined-export)로 오탐하므로 noqa 로 억제한다(실제 접근 정상).
__all__ = ["gate_record", "gate_human_for", "write_last_preview", "gate_record_from_prompt",
           "gate_record_ref", "gate_human_for_ref", "preview_ref_for_candidates",
           "gate_home", "gate_path", "last_preview_path", "GATE_WINDOW_SEC",
           "has_trigger_token", "TRIGGER_TOKENS"]  # noqa: F822
