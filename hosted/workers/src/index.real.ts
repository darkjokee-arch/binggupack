// U2 — 실 pack entry (GO-HOSTED-REALPACK-LOCAL).
// 데이터 = private: packs 는 KV(env.PACKS)에 owner 가 별도 적재 — 코드 번들에 데이터 0.
//   → CI deploy(코드만)는 데이터를 끌어들이지 않는다(데이터/배포 분리, 미스매치 #1 해소).
// fetch 첫 호출 시 env.PACKS.get(key,"json") 으로 lazy 로드 → loadPacks 검증 → PackStore lazy singleton 캐싱.
//   KV 미적재(raw===null)면 PACKS_EMPTY throw → 첫 요청 503 (fail-closed 유지 — 잘못된 빈 worker 가 답을 내지 않음).
// 게이트(load_packs.ts): KV 값이 load_packs 스펙 위반이면 throw → 503 (라이브 오염 차단).

import { PackStore, makeFetchHandler } from "./index";
import { loadPacks } from "./load_packs";

interface RealEnv {
  MCP_PATH_TOKEN?: string;
  AI?: any;
  SEMANTIC_LABEL_ENABLED?: string;
  PACKS?: KVNamespace;
  PACKS_KEY?: string;
}

// lazy singleton — promise 를 캐싱(동시 첫 요청이 같은 promise 공유 → KV 1회, TOCTOU 차단).
// 실패 시 CACHED 리셋 → 다음 요청 재시도(fail-closed 유지).
let CACHED: Promise<ReturnType<typeof makeFetchHandler>> | null = null;

function getHandler(env: RealEnv): Promise<ReturnType<typeof makeFetchHandler>> {
  if (CACHED) return CACHED;
  CACHED = (async () => {
    if (!env.PACKS) throw new Error("PACKS_EMPTY: KV binding 'PACKS' not configured");
    const key = (env.PACKS_KEY ?? "packs.json").trim() || "packs.json";
    const raw = await env.PACKS.get(key, "json");
    if (raw === null) throw new Error("PACKS_EMPTY: KV key not found: " + key);
    return makeFetchHandler(new PackStore(loadPacks(raw)));
  })().catch((e) => { CACHED = null; throw e; }); // 실패 시 리셋(다음 요청 재시도)
  return CACHED;
}

export default {
  async fetch(request: Request, env: RealEnv): Promise<Response> {
    let handler: ReturnType<typeof makeFetchHandler>;
    try {
      handler = await getHandler(env);
    } catch (e) {
      // fail-closed: KV 미적재/검증 실패 → 503 (내부 메시지 미노출 정적 문구)
      return new Response(JSON.stringify({ error: "pack store not ready" }), {
        status: 503, headers: { "Content-Type": "application/json" },
      });
    }
    return handler.fetch(request, env);
  },
};

export { __test } from "./index"; // S28 절단 경로 검증용 (런타임 미사용)
