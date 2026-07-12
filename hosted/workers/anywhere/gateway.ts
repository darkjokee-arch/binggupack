// Binggu Anywhere — PUBLIC HTTPS MCP gateway.
//
// Responsibilities (owner §6): HTTP/MCP envelope, authentication, tenant derivation,
// request validation, admin-upload orchestration, and Service Binding calls into the
// PRIVATE Python core. It does NOT reimplement any of the 5 read tools — every tool
// call is forwarded to the core, which runs the v1.21-A PackService.
//
// Data plane (/mcp)  : exactly five READ tools. No cloud write is reachable here.
// Admin plane (/admin/packs): owner-only immutable upload (separate route + scope).
// tenant_id comes ONLY from the auth context — never from body/query/tool args.

const SERVER_INFO = { name: "binggupack-anywhere", version: "1.21.0" };
const PROTOCOL_VERSION = "2025-06-18";
const SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2025-06-18", "2025-11-25"];

const MCP_BODY_CAP = 64 * 1024; // read requests are tiny
const ADMIN_BODY_CAP = 3 * 1024 * 1024; // 2 MiB snapshot + base64 overhead
const SCOPE_READ = "read:packs";
const SCOPE_WRITE = "write:packs";

const READ_TOOLS = [
  {
    name: "pack_list",
    description: "List the packs available to you (candidate-only, evidence-backed).",
    inputSchema: {
      type: "object",
      properties: {
        cursor: { type: "string", description: "opaque pagination cursor" },
        limit: { type: "integer", minimum: 1, maximum: 100 },
      },
      additionalProperties: false,
    },
  },
  {
    name: "pack_summary",
    description: "Summary of one pack by exact pack_id.",
    inputSchema: {
      type: "object",
      properties: { pack_id: { type: "string" } },
      required: ["pack_id"],
      additionalProperties: false,
    },
  },
  {
    name: "evidence_search",
    description: "Deterministic lexical evidence search within one pack.",
    inputSchema: {
      type: "object",
      properties: {
        pack_id: { type: "string" },
        query: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: 20 },
      },
      required: ["pack_id", "query"],
      additionalProperties: false,
    },
  },
  {
    name: "node_edge_lookup",
    description: "Look up a node and its edges by exact node_id or by keyword.",
    inputSchema: {
      type: "object",
      properties: {
        pack_id: { type: "string" },
        node_id: { type: "string" },
        keyword: { type: "string" },
      },
      required: ["pack_id"],
      additionalProperties: false,
    },
  },
  {
    name: "handoff_context",
    description: "Build a candidate-only handoff context (Markdown) for a pack.",
    inputSchema: {
      type: "object",
      properties: {
        pack_id: { type: "string" },
        topic: { type: "string" },
        max_nodes: { type: "integer", minimum: 1, maximum: 50 },
      },
      required: ["pack_id"],
      additionalProperties: false,
    },
  },
];
const READ_TOOL_NAMES = new Set(READ_TOOLS.map((t) => t.name));

type AuthContext = { subject: string; tenant_id: string; scopes: string[] };

const SECURITY_HEADERS: Record<string, string> = {
  "content-type": "application/json",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: SECURITY_HEADERS });
}

// 401 with WWW-Authenticate so MCP clients know to attach a bearer token.
function unauthorized(msg: string): Response {
  return new Response(JSON.stringify({ error: msg }), {
    status: 401,
    headers: { ...SECURITY_HEADERS, "www-authenticate": 'Bearer realm="binggupack-anywhere"' },
  });
}

async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Bearer token -> AuthContext, resolved SERVER-SIDE only. The raw token is never
// stored/logged; we look up by its SHA-256 hash in the AUTH KV (owner-provisioned).
// Tokens must be high-entropy random bearer secrets (256-bit), so an unsalted hash
// is not rainbow-table-exposed; low-entropy tokens must not be provisioned.
async function authenticate(request: Request, env: Env): Promise<AuthContext | null> {
  const h = request.headers.get("authorization") || "";
  const m = /^Bearer\s+(.+)$/i.exec(h.trim());
  if (!m) return null;
  const token = m[1].trim();
  if (!token) return null;
  const hash = await sha256Hex(token);
  const raw = await env.AUTH.get("cred:" + hash, "json");
  if (!raw) return null;
  const rec = raw as Partial<AuthContext> & { disabled?: boolean };
  if (rec.disabled) return null;
  if (!rec.tenant_id || !rec.subject || !Array.isArray(rec.scopes)) return null;
  return { subject: rec.subject, tenant_id: rec.tenant_id, scopes: rec.scopes };
}

function hasScope(auth: AuthContext, scope: string): boolean {
  return auth.scopes.includes(scope);
}

// Read the body with a hard size cap (fail-closed, no partial parse).
async function readCapped(request: Request, cap: number): Promise<string | null> {
  const cl = request.headers.get("content-length");
  if (cl && Number(cl) > cap) return null;
  const text = await request.text();
  if (text.length > cap) return null;
  return text;
}

async function callCore(env: Env, payload: unknown): Promise<{ status: number; body: any }> {
  const res = await env.CORE.fetch(
    new Request("https://core.internal/", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
  let body: any;
  try {
    body = await res.json();
  } catch {
    body = { error_code: "CORE_UNAVAILABLE" };
  }
  return { status: res.status, body };
}

// ── MCP JSON-RPC ────────────────────────────────────────────────────────────
async function handleRpc(rpc: any, env: Env, auth: AuthContext): Promise<any | null> {
  const id = rpc?.id ?? null;
  const method = rpc?.method;
  const err = (code: number, message: string) => ({ jsonrpc: "2.0", id, error: { code, message } });

  if (id === null || id === undefined) return null; // notification -> 202 (no body)
  if (typeof method !== "string") return err(-32600, "invalid request");

  if (method === "initialize") {
    const reqVer = (rpc.params ?? {}).protocolVersion;
    return {
      jsonrpc: "2.0",
      id,
      result: {
        protocolVersion: SUPPORTED_PROTOCOL_VERSIONS.includes(reqVer) ? reqVer : PROTOCOL_VERSION,
        capabilities: { tools: {} },
        serverInfo: SERVER_INFO,
      },
    };
  }
  if (method === "ping") return { jsonrpc: "2.0", id, result: {} };
  if (method === "tools/list") {
    return { jsonrpc: "2.0", id, result: { tools: READ_TOOLS } };
  }
  if (method === "tools/call") {
    const params = rpc.params ?? {};
    const name = params.name;
    if (!READ_TOOL_NAMES.has(name)) {
      // upload/write tools are NOT part of the data plane surface (owner §4).
      return err(-32602, "unknown tool: " + String(name));
    }
    // tenant comes ONLY from auth; any tenant_id in args is ignored.
    const args = { ...(params.arguments ?? {}) };
    delete (args as any).tenant_id;
    delete (args as any).tenant;
    const { status, body } = await callCore(env, {
      op: "invoke",
      tenant: auth.tenant_id,
      tool: name,
      args,
    });
    if (status >= 500) {
      return { jsonrpc: "2.0", id, result: { content: [{ type: "text", text: JSON.stringify({ error_code: "CORE_UNAVAILABLE" }) }], isError: true } };
    }
    const isError = !!(body && body.error_code);
    return {
      jsonrpc: "2.0",
      id,
      result: { content: [{ type: "text", text: JSON.stringify(body) }], isError },
    };
  }
  return err(-32601, "method not found: " + method);
}

async function handleMcp(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
  const auth = await authenticate(request, env);
  if (!auth) return unauthorized("authentication required");
  if (!hasScope(auth, SCOPE_READ)) return json({ error: "read:packs scope required" }, 403);

  const text = await readCapped(request, MCP_BODY_CAP);
  if (text === null) return json({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "request too large" } }, 413);
  let rpc: any;
  try {
    rpc = JSON.parse(text);
  } catch {
    return json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "parse error" } }, 400);
  }
  if (Array.isArray(rpc) || typeof rpc !== "object" || rpc === null) {
    return json({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "invalid request" } }, 400);
  }
  let resp: any;
  try {
    resp = await handleRpc(rpc, env, auth);
  } catch {
    resp = { jsonrpc: "2.0", id: rpc.id ?? null, error: { code: -32603, message: "internal error" } };
  }
  if (resp === null) return new Response(null, { status: 202, headers: SECURITY_HEADERS });
  return json(resp);
}

// ── Admin upload plane (owner-only, write:packs) ─────────────────────────────
async function handleAdminPacks(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
  const auth = await authenticate(request, env);
  if (!auth) return unauthorized("authentication required");
  // read credentials cannot reach the admin plane.
  if (!hasScope(auth, SCOPE_WRITE)) return json({ error: "write:packs scope required" }, 403);

  const text = await readCapped(request, ADMIN_BODY_CAP);
  if (text === null) return json({ error: "request too large" }, 413);
  let payload: any;
  try {
    payload = JSON.parse(text);
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const tarB64 = payload?.pack_tar_b64;
  if (typeof tarB64 !== "string" || !tarB64) return json({ error: "pack_tar_b64 required" }, 400);
  // tenant from auth ONLY — ignore/reject any tenant in the body.
  const { status, body } = await callCore(env, {
    op: "publish",
    tenant: auth.tenant_id,
    pack_tar_b64: tarB64,
  });
  if (status >= 500) return json({ publish_status: "error", reason: "core_unavailable" }, 502);
  // never surface raw storage paths — core already returns a whitelisted projection.
  return json(body, body?.publish_status === "ok" ? 200 : 422);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (path === "/health") {
      if (request.method !== "GET" && request.method !== "HEAD") return json({ error: "method not allowed" }, 405);
      return json({ ok: true, service: "binggupack-anywhere", mode: "read-only" });
    }
    if (path === "/mcp") return handleMcp(request, env);
    if (path === "/admin/packs") return handleAdminPacks(request, env);
    return json({ error: "not found" }, 404);
  },
};

interface Env {
  CORE: Fetcher;
  AUTH: KVNamespace;
}
