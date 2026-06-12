// index.save_v2.ts — V2-1 durable inbox worker entry (로컬 wrangler dev 한정).
// live 노출 = V2-4 owner 명시 GO 전 금지. deploy 금지 — wrangler.save_v2.toml 참조.
import { makeSaveV2Handler } from "./save_intent_v2";

export { IntentInbox } from "./save_intent_v2";
export default makeSaveV2Handler();
export { __testSaveV2 } from "./save_intent_v2";
