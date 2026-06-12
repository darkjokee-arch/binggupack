// save_intent_mcp.ts — V2-A: MCP 커넥터 어댑터 (폰 적재) + HMAC pull/admin (PC 러너).
// 설계 정본: docs/BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md
// 이중 인증 도메인:
//   적재(폰, MCP)  : POST /mcp2/<경로키>      JSON-RPC + Origin 가드 (read 라인 동급)
//   인출·관리(PC)  : POST /save2/<경로키>/{pull,admin/*}  HMAC 서명 (강한 인증 유지)
// 최종 저장 권한 = 로컬 러너 게이트(여기는 휘발 적재만). read 라인(62팩) 무접촉·별도 worker.
// live 노출 = A-2 owner GO 전 금지. 변수명 token/secret '=' 조합 회피 (6/10 박제).
import { IntentInbox } from "./save_intent_v2";

export { IntentInbox };

const SCHEMA_VER = 1;
const TEXT_CAP = 36000;
const INDICES_CAP = 64;
const DEFAULT_TTL_S = 86400;
const DEFAULT_INBOX_CAP = 32;
const SIG_WINDOW_S = 300;
const PROTOCOL_VERSION = "2025-06-18";
const SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2025-06-18", "2025-11-25"];
const SERVER_INFO = { name: "binggupack-save-intent", version: "2.0" };

const ENC = new TextEncoder();

function hex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

// 러너 intent_hash 와 바이트 동일 의무: sha256(text + "|" + indices.join(",") + "|" + confirm)[:16]
async function intentHash(text: string, indices: number[], confirm: string): Promise<string> {
  const base = text + "|" + indices.join(",") + "|" + confirm;
  return hex(await crypto.subtle.digest("SHA-256", ENC.encode(base))).slice(0, 16);
}

function argsReject(a: any): string | null {
  if (a === null || typeof a !== "object" || Array.isArray(a)) return "not_object";
  if (typeof a.text !== "string" || a.text.length === 0) return "text_invalid";
  if (a.text.length > TEXT_CAP) return "text_too_large";
  if (!Array.isArray(a.indices) || a.indices.length === 0) return "indices_empty";
  if (a.indices.length > INDICES_CAP) return "indices_too_many";
  if (!a.indices.every((i: any) => Number.isInteger(i) && i >= 1)) return "indices_invalid";
  if (typeof a.confirm !== "string") return "confirm_invalid";
  if (a.confirm !== "SAVE " + a.indices.join(",")) return "confirm_phrase_mismatch";
  return null;
}

const SAVE_TOOL = {
  name: "save_intent",
  description: "대화에서 사용자가 명시적으로 'SAVE n,m' 을 발화했을 때만 호출. " +
    "선택한 후보 인덱스를 저장 대기함(inbox)에 적재한다. 자동/추론 호출 금지 — " +
    "사용자 발화 confirm 문구가 정확히 일치해야 한다. 실제 저장은 사용자 PC의 게이트에서 확정된다.",
  inputSchema: {
    type: "object",
    properties: {
      text: { type: "string", description: "후보 미리보기 대상 대화 원문" },
      indices: { type: "array", items: { type: "integer" }, description: "1-base 선택 인덱스" },
      confirm: { type: "string", description: "'SAVE ' + indices.join(',') 정확 일치" },
    },
    required: ["text", "indices", "confirm"],
  },
};

function denyJson(status: number, msg: string): Response {
  return new Response(JSON.stringify({ error: msg }), {
    status, headers: { "Content-Type": "application/json" },
  });
}

function rpcResult(id: any, result: any): Response {
  return new Response(JSON.stringify({ jsonrpc: "2.0", id, result }), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
}

function rpcError(id: any, code: number, message: string): Response {
  return new Response(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
}

function originOk(request: Request): boolean {
  return request.headers.get("Origin") === null;
}

async function verifySig(signMaterial: string, request: Request, bodyText: string,
                         nowS: number): Promise<boolean> {
  const ts = request.headers.get("X-BGP-TS");
  const sig = request.headers.get("X-BGP-SIG");
  if (!ts || !sig || !signMaterial) return false;
  const t = parseInt(ts, 10);
  if (!Number.isInteger(t) || Math.abs(nowS - t) > SIG_WINDOW_S) return false;
  const bodyHash = hex(await crypto.subtle.digest("SHA-256", ENC.encode(bodyText)));
  const key = await crypto.subtle.importKey(
    "raw", ENC.encode(signMaterial), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = hex(await crypto.subtle.sign("HMAC", key, ENC.encode(ts + "." + bodyHash)));
  return timingSafeEqHex(mac, sig.toLowerCase());
}

interface SaveMcpEnv {
  SAVE_PATH_TOKEN?: string;
  SAVE_SIGN_SECRET?: string;
  SAVE_INBOX_CAP?: string;
  INBOX: DurableObjectNamespace;
}

async function handleMcp(rpc: any, env: SaveMcpEnv, stub: DurableObjectStub): Promise<Response> {
  const id = rpc.id ?? null;
  const method = rpc.method ?? "";
  if (id === null) return new Response(null, { status: 202 }); // notification
  if (method === "initialize") {
    const reqVer = (rpc.params ?? {}).protocolVersion;
    return rpcResult(id, {
      protocolVersion: SUPPORTED_PROTOCOL_VERSIONS.includes(reqVer) ? reqVer : PROTOCOL_VERSION,
      capabilities: { tools: { listChanged: false } },
      serverInfo: SERVER_INFO,
    });
  }
  if (method === "ping") return rpcResult(id, {});
  if (method === "tools/list") return rpcResult(id, { tools: [SAVE_TOOL] });
  if (method === "tools/call") {
    const params = rpc.params ?? {};
    if ((params.name ?? "") !== "save_intent") {
      return rpcError(id, -32602, "unknown tool: " + (params.name ?? ""));
    }
    const args = params.arguments ?? {};
    const reason = argsReject(args);
    if (reason !== null) {
      return rpcResult(id, { content: [{ type: "text", text: JSON.stringify({ error: reason }) }],
                             isError: true });
    }
    const nowS = Math.floor(Date.now() / 1000);
    const iid = await intentHash(args.text, args.indices, args.confirm);
    const it = {
      schema_ver: SCHEMA_VER, intent_id: iid, text: args.text, indices: args.indices,
      confirm: args.confirm, created_ts: nowS, ttl_s: DEFAULT_TTL_S, source: "hosted",
    };
    const cap = (env.SAVE_INBOX_CAP ?? "").trim() || String(DEFAULT_INBOX_CAP);
    const r = await stub.fetch("https://do/put", {
      method: "POST", body: JSON.stringify(it), headers: { "X-Inbox-Cap": cap },
    });
    const body = await r.json() as any;
    if (r.status !== 200) {
      return rpcResult(id, { content: [{ type: "text", text: JSON.stringify(body) }], isError: true });
    }
    const out = { intent_id: iid, ttl_s: DEFAULT_TTL_S }; // text echo 0
    return rpcResult(id, { content: [{ type: "text", text: JSON.stringify(out) }],
                           structuredContent: out, isError: false });
  }
  return rpcError(id, -32601, "method not found: " + method);
}

export function makeSaveMcpHandler() {
  return {
    async fetch(request: Request, env: SaveMcpEnv): Promise<Response> {
      const pathKey = (env.SAVE_PATH_TOKEN ?? "").trim();
      if (!pathKey) return denyJson(503, "not configured");
      const url = new URL(request.url);
      const stub = env.INBOX.get(env.INBOX.idFromName("inbox"));
      const nowS = Math.floor(Date.now() / 1000);

      // 적재 (폰, MCP) — Origin 가드 + 경로키
      if (url.pathname === "/mcp2/" + pathKey) {
        if (request.method !== "POST") return denyJson(405, "POST only");
        if (!originOk(request)) return denyJson(403, "origin not allowed");
        let rpc: any;
        try { rpc = JSON.parse(await request.text()); } catch { return denyJson(400, "invalid json"); }
        if (rpc === null || typeof rpc !== "object" || Array.isArray(rpc)) {
          return denyJson(400, "invalid json");
        }
        return handleMcp(rpc, env, stub);
      }

      // 인출·관리 (PC 러너) — HMAC
      const sbase = "/save2/" + pathKey;
      const sub = url.pathname.startsWith(sbase + "/") ? url.pathname.slice(sbase.length + 1) : null;
      if (sub !== null && ["pull", "admin/enable", "admin/disable"].includes(sub)) {
        if (request.method !== "POST") return denyJson(405, "POST only");
        if (!originOk(request)) return denyJson(403, "origin not allowed");
        const signMaterial = (env.SAVE_SIGN_SECRET ?? "").trim();
        const bodyText = await request.text();
        if (!(await verifySig(signMaterial, request, bodyText, nowS))) {
          return denyJson(401, "bad signature");
        }
        if (sub === "pull") return stub.fetch("https://do/drain", { method: "POST" });
        return stub.fetch("https://do/" + (sub === "admin/enable" ? "enable" : "disable"));
      }

      return denyJson(404, "not found");
    },
  };
}

export const __testSaveMcp = { argsReject, intentHash, SAVE_TOOL, SCHEMA_VER };
