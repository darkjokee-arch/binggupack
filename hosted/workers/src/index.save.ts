// index.save.ts — D3 save-intent 적재 worker entry (로컬 wrangler dev 한정).
// live 노출 = D5 owner 명시 GO 전 금지. deploy 금지 — wrangler.save.toml 참조.
import { IntentStore, makeSaveFetchHandler } from "./save_intent";

export default makeSaveFetchHandler(new IntentStore());
export { __testSave } from "./save_intent";
