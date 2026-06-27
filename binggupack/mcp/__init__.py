"""binggupack.mcp — MCP 핸들러 정본 facade (트랙 C strangler, C4).

목적: MCP 서버 wrapper 등 호출자가 scripts/openbinggu_mcp_server_handlers.py 를 직접 이름으로
import 하지 않고 이 facade 하나만 보게 한다. 지금은 **scripts 정본을 재노출만** 한다(동작 변경 0).
정본 코드를 이 패키지로 옮기는 일은 이후 단계 — facade 공개 이름은 그대로 유지한다.

불변(재노출이므로 그대로 보존): write-gated save_candidate · dry-run · _FORBIDDEN(위험 도구 노출 0)
· path gate/adapter 경유. facade 는 같은 객체를 노출할 뿐 게이트를 우회/완화하지 않는다.

공개 API:
  - TOOLS        노출 도구 레지스트리(read/dry-run + write-gated)
  - handle_tool(tool_name, params, allow_root)  게이트 경유 도구 실행
  - _FORBIDDEN   노출 금지 도구 집합(가시성 유지 — 서버가 노출 0 강제에 사용)
"""
import os
import sys

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from openbinggu_mcp_server_handlers import handle_tool, TOOLS, _FORBIDDEN

__all__ = ["handle_tool", "TOOLS", "_FORBIDDEN"]
