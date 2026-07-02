"""binggupack.mcp — MCP 핸들러 정본 패키지 (트랙 C strangler, 묶음 이관 완료).

정본 이관(v1.11.x): server_handlers + path_gate_adapter 를 이 패키지로 옮겨 scripts 재진입
경로를 제거했다. handle_tool/TOOLS/_FORBIDDEN 정본은 이제 .server_handlers 이고,
scripts/openbinggu_mcp_server_handlers.py·openbinggu_mcp_path_gate_adapter.py 는 공개 심볼을
재노출하는 thin shim 이다. eager import 는 순환 없이 완결된다(.server_handlers →
.path_gate_adapter → binggupack.safety / binggupack.classifier 로 전부 outward).

불변: write-gated save_candidate · dry-run · _FORBIDDEN(위험 도구 노출 0) · path gate/adapter 경유.
facade 는 같은 객체를 노출할 뿐 게이트를 우회/완화하지 않는다.

공개 API:
  - TOOLS        노출 도구 레지스트리(read/dry-run + write-gated)
  - handle_tool(tool_name, params, allow_root)  게이트 경유 도구 실행
  - _FORBIDDEN   노출 금지 도구 집합(가시성 유지 — 서버가 노출 0 강제에 사용)
"""
from .server_handlers import handle_tool, TOOLS, _FORBIDDEN

__all__ = ["handle_tool", "TOOLS", "_FORBIDDEN"]
