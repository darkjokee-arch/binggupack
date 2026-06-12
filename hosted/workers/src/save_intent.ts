// save_intent.ts — D3: hosted save-intent 적재 전용 worker (로컬 wrangler dev 한정).
// 설계 정본: docs/BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md
// 원칙(설계 §0): worker = 전달 통로(적재만). DB write 0 · payload 로깅 0(non-retention) ·
//   판정 게이트 본체는 로컬 러너(openbinggu_save_intent_outbox_runner.py).
// live 노출 = D5 owner 명시 GO 전 금지 — 이 모듈은 wrangler.save.toml(로컬 dev 전용)만 로드.
// 변수명에 token·secret 류 + '=' 조합 금지 — 공개 트리 secret 스캐너 자기검출 회피 (6/10 박제)

const SCHEMA_VER = 1;
const TEXT_CAP = 36000;          // hosted skeleton 크기캡 정합
const INDICES_CAP = 64;
const STORE_CAP = 32;            // 초과 = 적재 거부 503 (fail-closed)
const DEFAULT_TTL_S = 86400;     // 설계 §1
const TTL_CAP_S = 7 * 86400;     // 러너 MARKER_TTL_S 상한 정합

export interface SaveIntent {
  schema_ver: number;
  intent_id: string;
  text: string;
  indices: number[];
  confirm: string;
  created_ts: number;
  ttl_s: number;
  source: string;
}

export class IntentStore {
  private m = new Map<string, SaveIntent>();

  private purge(now: number): void {
    for (const [k, v] of this.m) {
      if (now > v.created_ts + v.ttl_s) this.m.delete(k);
    }
  }

  put(it: SaveIntent, now: number): string | null {
    this.purge(now);
    if (this.m.size >= STORE_CAP && !this.m.has(it.intent_id)) return "store_full";
    this.m.set(it.intent_id, it);
    return null;
  }

  // pull = drain — 반환 즉시 store에서 소거 (worker 측 non-retention)
  drain(now: number): SaveIntent[] {
    this.purge(now);
    const out = Array.from(this.m.values());
    this.m.clear();
    return out;
  }

  size(): number {
    return this.m.size;
  }
}

// 수신 body 형태 검증 — 거부 사유 코드 반환 (통과 = null). 판정 게이트는 러너 몫, 여기는 모양만.
function shapeReject(b: any): string | null {
  if (b === null || typeof b !== "object" || Array.isArray(b)) return "not_object";
  if (b.schema_ver !== SCHEMA_VER) return "schema_mismatch";
  if (typeof b.text !== "string" || b.text.length === 0) return "text_invalid";
  if (b.text.length > TEXT_CAP) return "text_too_large";
  if (!Array.isArray(b.indices) || b.indices.length === 0) return "indices_empty";
  if (b.indices.length > INDICES_CAP) return "indices_too_many";
  if (!b.indices.every((i: any) => Number.isInteger(i) && i >= 1)) return "indices_invalid";
  if (typeof b.confirm !== "string") return "confirm_invalid";
  if (b.confirm !== "SAVE " + b.indices.join(",")) return "confirm_phrase_mismatch";
  if (typeof b.intent_id !== "string" || !/^[0-9a-f]{16}$/.test(b.intent_id)) return "intent_id_invalid";
  if (b.ttl_s !== undefined && (!Number.isInteger(b.ttl_s) || b.ttl_s < 1 || b.ttl_s > TTL_CAP_S)) return "ttl_invalid";
  return null;
}

function denyJson(status: number, msg: string): Response {
  return new Response(JSON.stringify({ error: msg }), {
    status, headers: { "Content-Type": "application/json" },
  });
}

// S2 정합 — absent 허용(서버 발신), 브라우저 Origin 전부 403
function saveOriginOk(request: Request): boolean {
  return request.headers.get("Origin") === null;
}

interface SaveEnv {
  SAVE_PATH_TOKEN?: string;
}

export function makeSaveFetchHandler(store: IntentStore) {
  return {
    async fetch(request: Request, env: SaveEnv): Promise<Response> {
      const pathKey = (env.SAVE_PATH_TOKEN ?? "").trim();
      if (!pathKey) return denyJson(503, "save path token not configured"); // fail-closed
      const base = "/save/" + pathKey;
      const url = new URL(request.url);
      if (request.method !== "POST") return denyJson(405, "POST only");
      if (url.pathname !== base + "/intent" && url.pathname !== base + "/pull") {
        return denyJson(404, "not found"); // 오토큰·무토큰·기타 경로 일괄
      }
      if (!saveOriginOk(request)) return denyJson(403, "origin not allowed");
      const now = Math.floor(Date.now() / 1000);

      if (url.pathname === base + "/pull") {
        // pull-and-drain — 응답에 실어주는 즉시 worker 측 보관 0
        const intents = store.drain(now);
        return new Response(JSON.stringify({ intents }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }

      // /intent — 적재만
      let body: any;
      try {
        body = await request.json();
      } catch {
        return denyJson(400, "invalid json");
      }
      const reason = shapeReject(body);
      if (reason !== null) return denyJson(400, reason);
      const it: SaveIntent = {
        schema_ver: SCHEMA_VER,
        intent_id: body.intent_id,
        text: body.text,
        indices: body.indices,
        confirm: body.confirm,
        created_ts: now,                       // 설계 §1 — worker 수신 시각
        ttl_s: body.ttl_s ?? DEFAULT_TTL_S,
        source: "hosted",                      // 설계 §1 — 고정
      };
      const putErr = store.put(it, now);
      if (putErr !== null) return denyJson(503, putErr);
      // 응답에 text 미반환 (echo 0) — intent_id만
      return new Response(JSON.stringify({ ok: true, intent_id: it.intent_id }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    },
  };
}

export const __testSave = { shapeReject, SCHEMA_VER, TEXT_CAP, STORE_CAP };
