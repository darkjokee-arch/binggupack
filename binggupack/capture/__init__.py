"""capture — 빙구팩 캡처 파이프라인 모듈. v1.11.0 strangler phase5 이관 시작.

현재: buffer(메모리 내 candidate 누적 + preview 렌더, 영속화 0) + session(buffer 를 감싸는
on_user_prompt/on_session_end entrypoint). scripts/binggu_capture_buffer.py·binggu_capture_session.py
는 backward-compatible thin wrapper 로 유지된다. classify 정본은 binggupack.classifier.
"""
from .buffer import CaptureBuffer  # noqa: F401
from .session import CaptureSession  # noqa: F401
