# -*- coding: utf-8 -*-
"""binggupack.capture.preview — capture_preview facade (트랙 C strangler).

목적: server_handlers 등 호출자가 scripts/openbinggu_conversation_capture_preview 를
직접 import 하지 않고 이 facade 만 보게 한다. 정본 본문은 아직 scripts 잔류
(의존: label_kind_map·a0·incoming_to_staging·watcher_batch_m1·canon·capclf) →
여기서는 절대경로 _SCRIPTS 부트스트랩 후 동일 함수 객체를 재노출한다(identity 불변).

주의: 이 모듈은 CaptureBuffer(classify 전용, semantic 없음)가 아니라 semantic 도장(canon)
preview 인 openbinggu_conversation_capture_preview 를 노출한다. PII 로직 미접촉(byte-identical).
"""
import os
import sys

# 정본 본문이 아직 scripts 잔류 → <repo>/scripts 를 sys.path 에 보장(부트스트랩 책임 흡수).
_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from openbinggu_conversation_capture_preview import (  # noqa: E402,F401
    capture_preview, _PREVIEW_PII_EXTRA)

__all__ = ["capture_preview", "_PREVIEW_PII_EXTRA"]
