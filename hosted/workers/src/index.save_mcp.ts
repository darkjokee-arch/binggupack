// index.save_mcp.ts — V2-A MCP 어댑터 worker entry (로컬 wrangler dev 한정).
// live 노출 = A-2 owner GO 전 금지. deploy 금지 — wrangler.save_mcp.toml 참조.
import { makeSaveMcpHandler } from "./save_intent_mcp";

export { IntentInbox } from "./save_intent_mcp";
export default makeSaveMcpHandler();
export { __testSaveMcp } from "./save_intent_mcp";
