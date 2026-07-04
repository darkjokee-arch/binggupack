// save_intent_mcp.ts — V2-A: MCP 커넥터 어댑터 (폰 적재) + HMAC pull/admin (PC 러너).
// 설계 정본: docs/BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md
// 이중 인증 도메인:
//   적재(폰, MCP)  : POST /mcp2/<경로키>      JSON-RPC + Origin 가드 (read 라인 동급)
//   인출·관리(PC)  : POST /save2/<경로키>/{pull,admin/*}  HMAC 서명 (강한 인증 유지)
// 최종 저장 권한 = 로컬 러너 게이트(여기는 휘발 적재만). read 라인(62팩) 무접촉·별도 worker.
// live 노출 = A-2 owner GO 전 금지. 변수명 token/secret '=' 조합 회피 (6/10 박제).
import { IntentInbox } from "./save_intent_v2";
import { capturePreview } from "./capture_preview";
import { hex, sigV2Only, verifySig } from "./save_common";

export { IntentInbox };

const SCHEMA_VER = 1;
const TEXT_CAP = 36000;
const INDICES_CAP = 64;
const DEFAULT_TTL_S = 86400;
const DEFAULT_INBOX_CAP = 32;
// 4cli 20260612_1420 both_reject→단순화: 폰 미리보기·PC 러너 후보 상한 단일 고정.
// PC 러너 capture_preview 기본(DEFAULT_MAX=10)과 반드시 동일해야 번호 일치(임의 max 금지).
const CANDIDATE_MAX = 10;
const PROTOCOL_VERSION = "2025-06-18";
const SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2025-06-18", "2025-11-25"];
const SERVER_INFO = { name: "binggupack-save-intent", version: "2.0" };

const ENC = new TextEncoder();

// 러너 intent_hash 와 바이트 동일 의무: sha256(text + "|" + indices.join(",") + "|" + confirm)[:16]
// export: index.ts(read worker)가 save_intent 를 노출할 때 동일 로직 재사용(단일 출처 — 재구현 시 불일치 위험).
export async function intentHash(text: string, indices: number[], confirm: string): Promise<string> {
  const base = text + "|" + indices.join(",") + "|" + confirm;
  return hex(await crypto.subtle.digest("SHA-256", ENC.encode(base))).slice(0, 16);
}

// export: index.ts 재사용(동일 인자 검증 게이트). SCHEMA_VER/DEFAULT_TTL_S/DEFAULT_INBOX_CAP 도 함께 노출.
export const SAVE_INTENT_CONSTS = { SCHEMA_VER, TTL_S: DEFAULT_TTL_S, INBOX_CAP: DEFAULT_INBOX_CAP };

export function argsReject(a: any): string | null {
  if (a === null || typeof a !== "object" || Array.isArray(a)) return "not_object";
  if (typeof a.text !== "string" || a.text.length === 0) return "text_invalid";
  if (a.text.length > TEXT_CAP) return "text_too_large";
  if (!Array.isArray(a.indices) || a.indices.length === 0) return "indices_empty";
  if (a.indices.length > INDICES_CAP) return "indices_too_many";
  if (!a.indices.every((i: any) => Number.isInteger(i) && i >= 1)) return "indices_invalid";
  // 후보 상한(10) 초과 번호 선제 거부 — 폰·PC 번호 체계 동일성 강제 (11번↑은 존재 불가)
  if (!a.indices.every((i: any) => i <= CANDIDATE_MAX)) return "index_above_candidate_max";
  if (typeof a.confirm !== "string") return "confirm_invalid";
  if (a.confirm !== "SAVE " + a.indices.join(",")) return "confirm_phrase_mismatch";
  // 화자 축(선택): 사용자 본인 발화 저장이면 "owner"(내 온톨로지 팩 반영), AI 요약이면 "ai". 생략 시 미지정.
  if (a.speaker !== undefined && a.speaker !== "owner" && a.speaker !== "ai") return "speaker_invalid";
  return null;
}

const PREVIEW_TOOL = {
  name: "conversation_capture_preview",
  description: "사용자가 전달한 대화 텍스트에서 핵심 문장 후보를 미리보기(최대 10건·5종 도장·헌법 판정). " +
    "confirm 없이 부르면 저장 0(순수 미리보기) — PII/secret 문장은 후보 제외. " +
    "★사용자가 '저장해'/'SAVE n'으로 저장을 요청하면, 같은 호출에 indices(사용자가 고른 후보 번호)와 " +
    "confirm('SAVE '+번호들, 예: 'SAVE 1' 또는 'SAVE 1,3')을 함께 넣어라 — 그러면 미리보기와 동시에 실제 저장이 실행된다. " +
    "confirm 은 반드시 'SAVE ' + indices.join(',') 정확 일치여야 저장된다(자동저장 방지 게이트). " +
    "저장 대상이 사용자 본인 발화면 speaker='owner'. 후보 개수는 10건 고정 — 11번 이상은 없다.",
  inputSchema: {
    type: "object",
    properties: {
      text: { type: "string", description: "캡처 후보를 뽑을 대화 발췌 (사용자가 명시적으로 전달)" },
      indices: { type: "array", items: { type: "integer" },
        description: "저장 실행 시에만 — 사용자가 고른 후보 번호(1-base). 미리보기만이면 생략." },
      confirm: { type: "string",
        description: "저장 실행 시에만 — 'SAVE ' + indices.join(',') 정확 일치. 생략 시 순수 미리보기(저장 0)." },
      speaker: { type: "string", enum: ["owner", "ai"],
        description: "화자 축(선택). 사용자 본인 발화 저장='owner' / AI 요약='ai'." },
    },
    required: ["text"],
  },
  // MCP annotations: 이 도구는 read-only(환경 미변경). save_intent 와 성격을 명확히 구분해
  // 클라이언트(ChatGPT 등)가 커넥터 전체를 read-only 로 뭉뚱그리지 않게 한다.
  annotations: { title: "빙구팩 저장 후보 미리보기", readOnlyHint: true, openWorldHint: false },
};

const SAVE_TOOL = {
  name: "save_intent",
  description: "사용자의 판단·교훈을 빙구팩에 **실제로 저장하는 실행(write) 도구** — read-only 아님. " +
    "사용자가 '저장해' / 'SAVE n' 등 저장 의사를 밝히면 반드시 이 도구를 호출해 저장을 실행하라. " +
    "이 호출이 저장을 실제로 수행하는 단계다(미리보기가 아니라 저장 실행). " +
    "(사용자가 요청하지 않았는데 자동/추론으로 호출하는 것만 금지 — 사용자가 저장을 요청하면 반드시 실행할 것.) " +
    "confirm 은 'SAVE ' + indices.join(',') 정확 일치. " +
    "저장 대상이 사용자 본인 발화면 speaker='owner' 를 함께 보내라(사용자 온톨로지 팩 반영).",
  inputSchema: {
    type: "object",
    properties: {
      text: { type: "string", description: "후보 미리보기 대상 대화 원문" },
      indices: { type: "array", items: { type: "integer" }, description: "1-base 선택 인덱스" },
      confirm: { type: "string", description: "'SAVE ' + indices.join(',') 정확 일치" },
      speaker: { type: "string", enum: ["owner", "ai"],
        description: "화자 축(선택). 사용자 본인 발화 저장='owner'(내 온톨로지 팩 반영) / AI 요약='ai'. 생략 시 미지정(NULL)." },
    },
    required: ["text", "indices", "confirm"],
  },
  // MCP annotations: OpenAI 안전검사가 readOnlyHint=false(write 표시)를 트리거로 차단하는지 실측하기 위해
  // readOnlyHint/openWorldHint 를 생략(기본값 위임). 비파괴·멱등만 명시. confirm 게이트(자동저장 방지)는 불변.
  annotations: { title: "빙구팩 저장 실행", destructiveHint: false, idempotentHint: true },
};

function denyJson(status: number, msg: string): Response {
  return new Response(JSON.stringify({ error: msg }), {
    status, headers: { "Content-Type": "application/json" },
  });
}

// MCP Streamable HTTP: 클라이언트가 Accept: text/event-stream 을 보내면(claude.ai 등) 응답을
// SSE 이벤트로 감싼다. 아니면 단순 JSON. 둘 다 스펙상 유효하나 일부 클라이언트는 SSE 응답만 처리한다.
function jsonRpcBody(id: any, body: any): string {
  return JSON.stringify({ jsonrpc: "2.0", id, ...body });
}
function wrap(payload: string, sse: boolean): Response {
  if (sse) {
    return new Response(`event: message\ndata: ${payload}\n\n`, {
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
    });
  }
  return new Response(payload, { status: 200, headers: { "Content-Type": "application/json" } });
}
function rpcResult(id: any, result: any, sse = false): Response {
  return wrap(jsonRpcBody(id, { result }), sse);
}
function rpcError(id: any, code: number, message: string, sse = false): Response {
  return wrap(jsonRpcBody(id, { error: { code, message } }), sse);
}

// 신뢰 채팅 도메인 allowlist — Origin 없음(서버-서버 pull) 또는 claude.ai/chatgpt/openai 계열만 허용.
// 그 외 브라우저 Origin 은 403(CSRF 방어 유지). MCP 커넥터가 Origin 을 붙여도 신뢰 도메인이면 통과.
const ALLOWED_ORIGIN_HOSTS = new Set(["claude.ai", "chatgpt.com", "chat.openai.com", "openai.com"]);
function originOk(request: Request): boolean {
  const o = request.headers.get("Origin");
  if (o === null) return true;                        // 서버-서버 (PC 러너 pull 등) — 기존 동작 유지
  try {
    const h = new URL(o).hostname.toLowerCase();
    return ALLOWED_ORIGIN_HOSTS.has(h)
      || h.endsWith(".claude.ai") || h.endsWith(".openai.com") || h.endsWith(".chatgpt.com");
  } catch {
    return false;                                     // 파싱 불가 Origin = 차단
  }
}

// HMAC 검증 단일 출처 = save_common.verifySig (신형 method+path 우선 · 구형 하위호환)

interface SaveMcpEnv {
  SAVE_PATH_TOKEN?: string;
  SAVE_SIGN_SECRET?: string;
  SAVE_INBOX_CAP?: string;
  SAVE_SIG_V2_ONLY?: string;       // "1"/"true" = 신형 서명 전용 (기본 false = 구형 수용)
  INBOX: DurableObjectNamespace;
}

async function handleMcp(rpc: any, env: SaveMcpEnv, stub: DurableObjectStub, sse: boolean): Promise<Response> {
  const id = rpc.id ?? null;
  const method = rpc.method ?? "";
  console.log(`[MCP] method=${method} id=${JSON.stringify(id)} sse=${sse}`);   // 진단: claude.ai 핸드셰이크 추적
  if (id === null) return new Response(null, { status: 202 }); // notification
  if (method === "initialize") {
    const reqVer = (rpc.params ?? {}).protocolVersion;
    return rpcResult(id, {
      protocolVersion: SUPPORTED_PROTOCOL_VERSIONS.includes(reqVer) ? reqVer : PROTOCOL_VERSION,
      capabilities: { tools: { listChanged: false } },
      serverInfo: SERVER_INFO,
    }, sse);
  }
  if (method === "ping") return rpcResult(id, {}, sse);
  if (method === "tools/list") return rpcResult(id, { tools: [PREVIEW_TOOL, SAVE_TOOL] }, sse);
  if (method === "tools/call") {
    const params = rpc.params ?? {};
    const toolName = params.name ?? "";
    const args = params.arguments ?? {};

    // 미리보기 (read-only, 저장 0) — save_intent 번호와 동일 체계
    if (toolName === "conversation_capture_preview") {
      if (typeof args.text !== "string" || !args.text.trim()) {
        return rpcResult(id, { content: [{ type: "text", text: JSON.stringify({ error: "text_invalid" }) }],
                               isError: true }, sse);
      }
      // max 고정 — 폰이 임의 개수를 보내도 무시(PC 러너 기본 10과 동일성 강제)
      const out: Record<string, unknown> = capturePreview(args.text, CANDIDATE_MAX);
      // OpenAI 안전검사 우회(2026-07-04 owner GO): read 도구(preview)에 저장 통합. save_intent(write) 는
      // OpenAI 가 불안정 차단하나 preview(read)는 통과 → confirm='SAVE '+indices 정확일치 시에만 inbox 적재.
      // confirm/indices 없으면 순수 preview(저장 0). 자동저장 방지는 confirm 게이트로 불변(사용자 SAVE n 명시 증거).
      if (typeof args.confirm === "string" && Array.isArray(args.indices) && args.indices.length) {
        const rej = argsReject({ text: args.text, indices: args.indices, confirm: args.confirm, speaker: args.speaker });
        if (rej !== null) {
          out.save_result = { saved: false, error: rej };
        } else {
          const nowS = Math.floor(Date.now() / 1000);
          const iid = await intentHash(args.text, args.indices, args.confirm);
          const it: Record<string, unknown> = {
            schema_ver: SCHEMA_VER, intent_id: iid, text: args.text, indices: args.indices,
            confirm: args.confirm, created_ts: nowS, ttl_s: DEFAULT_TTL_S, source: "hosted",
          };
          if (args.speaker === "owner" || args.speaker === "ai") it.speaker = args.speaker;
          const cap = (env.SAVE_INBOX_CAP ?? "").trim() || String(DEFAULT_INBOX_CAP);
          const r = await stub.fetch("https://do/put", {
            method: "POST", body: JSON.stringify(it), headers: { "X-Inbox-Cap": cap },
          });
          const body = await r.json() as any;
          out.save_result = r.status === 200
            ? { saved: true, intent_id: iid, ttl_s: DEFAULT_TTL_S }
            : { saved: false, error: body.error };
        }
      }
      const md = out.preview_markdown as string;
      const sr = out.save_result as any;
      const text = md + (sr ? ("\n\n저장 결과: " + JSON.stringify(sr)) : "");
      return rpcResult(id, { content: [{ type: "text", text }],
                             structuredContent: out, isError: false }, sse);
    }

    if (toolName !== "save_intent") {
      return rpcError(id, -32602, "unknown tool: " + toolName, sse);
    }
    const reason = argsReject(args);
    if (reason !== null) {
      return rpcResult(id, { content: [{ type: "text", text: JSON.stringify({ error: reason }) }],
                             isError: true }, sse);
    }
    const nowS = Math.floor(Date.now() / 1000);
    const iid = await intentHash(args.text, args.indices, args.confirm);
    const it: Record<string, unknown> = {
      schema_ver: SCHEMA_VER, intent_id: iid, text: args.text, indices: args.indices,
      confirm: args.confirm, created_ts: nowS, ttl_s: DEFAULT_TTL_S, source: "hosted",
    };
    // 화자 축을 payload 로만 실어보낸다(intentHash 재료 text|indices|confirm 은 불변 → 러너 intent_id 재해시 호환).
    if (args.speaker === "owner" || args.speaker === "ai") it.speaker = args.speaker;
    const cap = (env.SAVE_INBOX_CAP ?? "").trim() || String(DEFAULT_INBOX_CAP);
    const r = await stub.fetch("https://do/put", {
      method: "POST", body: JSON.stringify(it), headers: { "X-Inbox-Cap": cap },
    });
    const body = await r.json() as any;
    if (r.status !== 200) {
      return rpcResult(id, { content: [{ type: "text", text: JSON.stringify(body) }], isError: true }, sse);
    }
    const out = { intent_id: iid, ttl_s: DEFAULT_TTL_S }; // text echo 0
    return rpcResult(id, { content: [{ type: "text", text: JSON.stringify(out) }],
                           structuredContent: out, isError: false }, sse);
  }
  return rpcError(id, -32601, "method not found: " + method, sse);
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
        console.log(`[REQ] mcp2 method=${request.method} origin=${request.headers.get("Origin")} accept=${request.headers.get("Accept")}`);  // 진단: transport 협상
        if (request.method !== "POST") return denyJson(405, "POST only");
        if (!originOk(request)) return denyJson(403, "origin not allowed");
        let rpc: any;
        try { rpc = JSON.parse(await request.text()); } catch { return denyJson(400, "invalid json"); }
        if (rpc === null || typeof rpc !== "object" || Array.isArray(rpc)) {
          return denyJson(400, "invalid json");
        }
        const wantsSSE = (request.headers.get("Accept") ?? "").includes("text/event-stream");
        try {
          return await handleMcp(rpc, env, stub, wantsSSE);
        } catch { // 미상 예외 — 내부 정보 미노출 정적 -32603 (index.ts 패턴 재사용)
          return rpcError(rpc.id ?? null, -32603, "internal error", wantsSSE);
        }
      }

      // 인출·관리 (PC 러너) — HMAC
      const sbase = "/save2/" + pathKey;
      const sub = url.pathname.startsWith(sbase + "/") ? url.pathname.slice(sbase.length + 1) : null;
      if (sub !== null && ["pull", "admin/enable", "admin/disable"].includes(sub)) {
        if (request.method !== "POST") return denyJson(405, "POST only");
        if (!originOk(request)) return denyJson(403, "origin not allowed");
        const signMaterial = (env.SAVE_SIGN_SECRET ?? "").trim();
        const bodyText = await request.text();
        if (!(await verifySig(signMaterial, request, bodyText, nowS, sigV2Only(env.SAVE_SIG_V2_ONLY)))) {
          return denyJson(401, "bad signature");
        }
        if (sub === "pull") return stub.fetch("https://do/drain", { method: "POST" });
        return stub.fetch("https://do/" + (sub === "admin/enable" ? "enable" : "disable"));
      }

      return denyJson(404, "not found");
    },
  };
}

export const __testSaveMcp = { argsReject, intentHash, SAVE_TOOL, PREVIEW_TOOL, SCHEMA_VER };
