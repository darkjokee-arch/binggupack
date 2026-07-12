"""BingguPack — local-first, evidence-backed memory/context pack framework.

v1.11.0 groundwork: 내부 구현을 패키지 모듈로 단계적 이관.
public entrypoint(scripts/smoke_test.py, scripts/install_claude_mcp.py, binggu.py)는
backward-compatible 하게 유지된다.
"""
from binggupack.__about__ import __version__

__all__ = ["__version__"]
