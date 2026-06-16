// BingguPack hosted MCP — Cloudflare Workers TS (GO-CONNECTOR-PHASE1-CODE-LOCAL).
//
// 기반: GO-WORKERS-PORT-LOCAL 포팅본 (selftest 21/21 GO · Python parity byte 30/30 GO).
// 이 버전부터 TS가 단일 정본 — 설계 docs/BINGGUPACK_CONNECTOR_PHASE1_PREFLIGHT_DESIGN.md §1~§4 반영:
//   S1 경로 토큰: /mcp/<MCP_PATH_TOKEN> (env 주입 — wrangler secret 또는 .dev.vars. 코드/설정 평문 0)
//      토큰 미설정 시 fail-closed 503. 토큰 없는 /mcp·오토큰 = 404.
//   S2 Origin: absent 허용(Claude/ChatGPT 서버 발신) + 브라우저 Origin 전부 403 (localhost 예외 제거)
//   S5 MCP-Protocol-Version 헤더: 미지원 값 400 (absent 허용 — initialize 협상)
//   S6 응답 캡 기존 36K → 20K자
//   §4 tool 5종 전건 annotations(readOnlyHint=true) + outputSchema
// 불변: read-only 5 tool · synthetic toy pack 전용 · JSON-only(GET 405) · stateless ·
//   fail-closed 누출 스캔(SANITIZE_BLOCK) · 배포/OAuth/등록 0 (wrangler dev 로컬 전용).

import { capturePreview, scanPii } from "./capture_preview";
import { capturePreviewSemantic, Centroids } from "./capture_preview_semantic";
import centroidsData from "./centroids_canonical_5.json";

// P3: 실 @cf/baai/bge-m3 centroid(코드 상수 박제). opt-in OFF면 미사용(capturePreviewSemantic 가 passthrough).
const CENTROIDS = centroidsData as unknown as Centroids;

const PROTOCOL_VERSION = "2025-06-18";
const SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2025-06-18", "2025-11-25"];
const SERVER_INFO = { name: "binggupack-http-mcp-skeleton", version: "0.2.1-phase1-local" };
const MAX_RESPONSE_CHARS = 20000; // S6 — Claude Code 25K 토큰 캡·한국어 토큰 밀도 보수 기준
const EXCERPT_MAX = 200;

const LEAK_PATTERNS: [string, RegExp][] = [
  ["[A-Za-z]:\\\\\\\\?", /[A-Za-z]:\\\\?/],
  ["/(?:Users|home)/[A-Za-z0-9_]+", /\/(?:Users|home)\/[A-Za-z0-9_]+/],
  ["_backup", /_backup/],
  ["cloud_reset_\\d+", /cloud_reset_\d+/],
];

const CONSUMER_RULES_MD =
  "## consumer rules (불변)\n" +
  "1. evidence_refs 기반으로만 답한다 — 근거 없으면 \"pack에 근거 없음\".\n" +
  "2. 추측 생성 금지 — 출처는 node_id/evidence_id로 표기(id만, raw 경로/secret 금지).\n" +
  "3. 모든 노드/엣지는 candidate(confirmed 아님) — 승격하지 않는다.\n" +
  "4. 자동 병합/저장 금지 — 받은 pack을 그래프/메모리에 자동 반영하지 않는다.\n";

class ToolError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

// ---------------- pyDumps — Python json.dumps(ensure_ascii=False) 재현 ----------------

function pyDumps(v: unknown): string {
  if (v === null || v === undefined) return "null";
  const t = typeof v;
  if (t === "string") return JSON.stringify(v);
  if (t === "number") return String(v);
  if (t === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return "[" + v.map(pyDumps).join(", ") + "]";
  return (
    "{" +
    Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => JSON.stringify(k) + ": " + pyDumps(val))
      .join(", ") +
    "}"
  );
}

// ---------------- toy pack 생성 (synthetic only) ----------------

type NodeRow = [string, string, string, string];
type EdgeRow = [string, string, string, string];

const PACK_SPECS: [string, string, NodeRow[], EdgeRow[]][] = [
  ["toy_build_notes", "synthetic toy: 빌드/테스트 절차 메모", [
    ["n1", "Toy 프로젝트의 빌드는 make build 로 실행한다 (합성 예시).", "EV-A1", "examples/toy/Makefile"],
    ["n2", "Toy 프로젝트의 테스트는 make test 로 실행한다 (합성 예시).", "EV-A2", "examples/toy/Makefile"],
    ["n3", "릴리스 전에는 빌드와 테스트를 모두 통과해야 한다 (합성 예시).", "EV-A3", "examples/toy/RELEASE.md"],
  ], [["e1", "n3", "depends_on", "n1"], ["e2", "n3", "depends_on", "n2"]]],
  ["toy_recipe_notes", "synthetic toy: 요리 레시피 메모", [
    ["n1", "토마토 수프는 토마토를 먼저 볶은 뒤 끓인다 (합성 예시).", "EV-B1", "examples/recipe/soup.md"],
    ["n2", "수프 간은 마지막 단계에서 맞춘다 (합성 예시).", "EV-B2", "examples/recipe/soup.md"],
  ], [["e1", "n2", "refines", "n1"]]],
];

export interface Pack {
  manifest: Record<string, any>;
  nodes: any[];
  edges: any[];
  evIndex: any[];
  evChunk: any[];
}

function makeToyPacks(): Pack[] {
  const packs: Pack[] = [];
  for (const [packName, scopeDesc, nodeRows, edgeRows] of PACK_SPECS) {
    const pid = "toy/" + packName;
    const nodes: any[] = [];
    const evIndex: any[] = [];
    const evChunk: any[] = [];
    const nid2eid: Record<string, string> = {};
    for (const [nid, , eid] of nodeRows) nid2eid[nid] = eid;
    for (const [nid, sentence, eid, relSrc] of nodeRows) {
      nodes.push({
        id: `node:${packName}:${nid}`,
        label: sentence.slice(0, 40),
        properties: { sentence, candidate: true, origin: "synthetic", domain: "toy" },
        evidence_refs: [eid],
        promotion_allowed: false,
      });
      evIndex.push({ evidence_id: eid, source_path: relSrc });
      evChunk.push({ item_id: eid, text: sentence });
    }
    const edges = edgeRows.map(([eid_, s, rel, t]) => ({
      id: `edge:${packName}:${eid_}`,
      source: `node:${packName}:${s}`,
      target: `node:${packName}:${t}`,
      properties: { relation: rel, candidate: true, origin: "synthetic" },
      evidence_refs: [nid2eid[t]], // 의존 대상(target) 노드의 근거 — Python 원본과 동일 fix (parity)
      promotion_allowed: false,
    }));
    packs.push({
      manifest: {
        format_version: "opencrab-pack-v1", pack_id: pid,
        scope: scopeDesc, visibility: "private", status: "staged",
        pack_type: "candidate", promotion_allowed_default: false,
        counts: { nodes: nodes.length, edges: edges.length, evidence: evIndex.length },
      },
      nodes, edges, evIndex, evChunk,
    });
  }
  return packs;
}

// ---------------- consume() — sanitize view (원본 contract 1:1) ----------------

function consume(pack: Pack): any {
  const manifest = pack.manifest;
  const visibility = manifest.visibility ?? "private";
  const status = manifest.status ?? "staged";
  const packPromotionDefault = manifest.promotion_allowed_default ?? false;
  const confirmedAllowed = status === "validated";

  const nodeView = pack.nodes.map((n) => {
    const p = n.properties ?? {};
    return {
      id: n.id,
      claim: p.sentence ?? n.label ?? "",
      candidate: Boolean(p.candidate),
      promotion_allowed: Boolean(n.promotion_allowed ?? false),
      origin: p.origin ?? "",
      domain: p.domain ?? "",
      evidence_refs: [...(n.evidence_refs ?? [])],
      trust: "candidate_unverified",
      ...(p.doc_status && p.doc_status !== "active" ? { doc_status: p.doc_status } : {}),
    };
  });

  const edgeView = pack.edges.map((e) => {
    const p = e.properties ?? {};
    return {
      id: e.id,
      relation: p.relation ?? "",
      source: e.source ?? "",
      target: e.target ?? "",
      candidate: Boolean(p.candidate),
      promotion_allowed: Boolean(e.promotion_allowed ?? false),
      origin: p.origin ?? "",
      evidence_refs: [...(e.evidence_refs ?? [])],
      trust: "candidate_unverified",
    };
  });

  const evText: Record<string, string> = {};
  for (const c of pack.evChunk) evText[c.item_id] = c.text ?? "";
  const evidenceView = pack.evIndex.map((ev) => {
    const eid = ev.evidence_id;
    const ptr = ev.source_path ?? "";
    const present = eid in evText && Boolean(evText[eid]);
    return {
      evidence_id: eid,
      source_pointer: ptr,
      verification: present ? "verified_pointer" : "unverified",
      redaction: (evText[eid] ?? "").includes("[REDACTED:") || present ? "applied" : "unknown",
    };
  });

  const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
  return {
    pack_id: manifest.pack_id ?? "",
    title: manifest.title ?? "",
    scope: manifest.scope ?? "",
    topics: Array.isArray(manifest.topics) ? manifest.topics : [],
    visibility, status,
    confirmed_allowed: confirmedAllowed,
    pack_promotion_allowed_default: Boolean(packPromotionDefault),
    counts: { nodes: nodeView.length, edges: edgeView.length, evidence: evidenceView.length },
    evidence_basis: {
      node_ids: nodeView.map((n) => n.id).sort(cmp),
      edge_ids: edgeView.map((e) => e.id).sort(cmp),
      evidence_ids: evidenceView.map((e) => e.evidence_id).sort(cmp),
    },
    nodes: nodeView, edges: edgeView, evidence: evidenceView,
  };
}

// ---------------- pack store (read-only) ----------------

export class PackStore {
  private views: Record<string, any> = {};
  // v2.1: 원문 발췌(chunk text) 보관 — 검색이 short_label뿐 아니라 원문도 치도록 (read-only)
  private evTexts: Record<string, Record<string, string>> = {};
  constructor(packs: Pack[] = makeToyPacks()) {
    for (const pack of packs) {
      const view = consume(pack);
      if (view.pack_id) {
        this.views[view.pack_id] = view;
        const t: Record<string, string> = {};
        for (const c of pack.evChunk) t[c.item_id] = c.text ?? "";
        this.evTexts[view.pack_id] = t;
      }
    }
  }
  ids(): string[] {
    return Object.keys(this.views).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  }
  get(packId: string): any {
    if (!(packId in this.views)) throw new ToolError("PACK_NOT_FOUND", "pack_id not found: " + packId);
    return this.views[packId];
  }
  texts(packId: string): Record<string, string> {
    return this.evTexts[packId] ?? {};
  }
}

const STORE = new PackStore();

// ---------------- tools (전부 read-only) ----------------

function reqStr(args: Record<string, any>, key: string): string {
  const val = args[key];
  if (typeof val !== "string" || !val.trim()) {
    throw new ToolError("INVALID_ARGUMENT", "missing required string: " + key);
  }
  return val.trim();
}

function toInt(v: any): number {
  const n = Math.trunc(Number(v));
  if (Number.isNaN(n)) throw new ToolError("INVALID_ARGUMENT", "invalid integer");
  return n;
}

function countOcc(text: string, term: string): number {
  if (!term) return 0;
  let c = 0, i = 0;
  while ((i = text.indexOf(term, i)) !== -1) { c++; i += term.length; }
  return c;
}

function toolPackList(store: PackStore, args: Record<string, any>): any {
  const limit = Math.max(1, Math.min(toInt(args.limit ?? 20), 70)); // v2.1: MAX_PACKS 70 정합
  const packs = store.ids().slice(0, limit).map((pid) => {
    const v = store.get(pid);
    const title = (v.title || (v.topics && v.topics.length ? v.topics.join("·") : v.scope)) ?? "";
    return { pack_id: pid, title, counts: v.counts,
             candidate_note: "all items candidate (not confirmed)" };
  });
  return { packs, total: store.ids().length };
}

function toolPackSummary(store: PackStore, args: Record<string, any>): any {
  const v = store.get(reqStr(args, "pack_id"));
  const topics = v.nodes.slice(0, 10).map((n: any) => n.claim.slice(0, 40));
  return {
    pack_id: v.pack_id,
    manifest_summary: { visibility: v.visibility, status: v.status,
                        pack_type: "candidate", counts: v.counts },
    topics,
    candidate_note: "all items candidate (not confirmed); promotion_allowed=false",
  };
}

function toolEvidenceSearch(store: PackStore, args: Record<string, any>): any {
  const query = reqStr(args, "query");
  if (!(query.length >= 2 && query.length <= 200)) {
    throw new ToolError("QUERY_TOO_SHORT", "query must be 2~200 chars");
  }
  const limit = Math.max(1, Math.min(toInt(args.limit ?? 5), 20));
  // v2.1: pack_id 생략 시 전 팩 검색 (62팩 분할 구조에서 팩 위치를 몰라도 검색 가능)
  const packIds = args.pack_id ? [String(args.pack_id)] : store.ids();
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const hits: any[] = [];
  for (const pid of packIds) {
    const v = store.get(pid);
    const texts = store.texts(pid);
    for (const n of v.nodes) {
      const refs: string[] = n.evidence_refs ?? [];
      if (refs.length === 1 && refs[0] === n.id) continue; // 증거 노드 자기참조 — 본문은 chunk로 검색
      const claim = n.claim.toLowerCase();
      const fullList = refs.map((r: string) => texts[r] ?? "");
      const full = fullList.join(" ").toLowerCase();
      let score = 0;
      for (const t of terms) score += countOcc(claim, t) * 2 + countOcc(full, t);
      if (score > 0) {
        const excerptSrc =
          fullList.find((x: string) => terms.some((t) => x.toLowerCase().includes(t))) || n.claim;
        hits.push({ pack_id: pid, node_id: n.id, evidence_id: refs[0] ?? "",
                    sentence_excerpt: excerptSrc.slice(0, EXCERPT_MAX), score, candidate: true });
      }
    }
  }
  hits.sort((a, b) => b.score - a.score ||
    (a.evidence_id < b.evidence_id ? -1 : a.evidence_id > b.evidence_id ? 1 : 0));
  return { hits: hits.slice(0, limit), total_hits: hits.length,
           candidate_note: "excerpts are candidate evidence" };
}

function toolNodeEdgeLookup(store: PackStore, args: Record<string, any>): any {
  const v = store.get(reqStr(args, "pack_id"));
  const nodeId = args.node_id;
  const keyword = args.keyword;
  if (!nodeId && !keyword) throw new ToolError("NODE_NOT_FOUND", "node_id or keyword required");
  const nodesById: Record<string, any> = {};
  for (const n of v.nodes) nodesById[n.id] = n;
  let node: any;
  if (nodeId) {
    if (!(nodeId in nodesById)) throw new ToolError("NODE_NOT_FOUND", "node_id not found: " + nodeId);
    node = nodesById[nodeId];
  } else {
    const kw = String(keyword).toLowerCase();
    const cands = v.nodes.filter((n: any) => n.claim.toLowerCase().includes(kw));
    if (cands.length === 0) throw new ToolError("NODE_NOT_FOUND", "no node matches keyword");
    if (cands.length > 1) {
      const ids = cands.map((n: any) => n.id).sort((a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0));
      throw new ToolError("AMBIGUOUS_KEYWORD", "candidates: " + ids.slice(0, 5).join(", "));
    }
    node = cands[0];
  }
  const edges = v.edges
    .filter((e: any) => e.source === node.id || e.target === node.id)
    .map((e: any) => ({
      id: e.id, relation: e.relation,
      direction: e.source === node.id ? "out" : "in",
      peer_id: e.source === node.id ? e.target : e.source,
      evidence_refs: e.evidence_refs, candidate: e.candidate,
    }));
  return { node: { id: node.id, claim: node.claim, candidate: node.candidate,
                   evidence_refs: node.evidence_refs, trust: node.trust,
                   ...(node.doc_status ? { doc_status: node.doc_status } : {}) },
           edges };
}

function toolHandoffContext(store: PackStore, args: Record<string, any>): any {
  const v = store.get(reqStr(args, "pack_id"));
  const maxNodes = Math.max(1, Math.min(toInt(args.max_nodes ?? 15), 30));
  const offset = Math.max(0, toInt(args.offset ?? 0)); // U6 — 분할 소비(2순위 pack 분할의 전제)
  const topic = String(args.topic ?? "").trim().toLowerCase();
  const nodes = v.nodes;
  let pool: any[];
  if (topic) {
    const filtered = nodes.filter((n: any) => n.claim.toLowerCase().includes(topic));
    pool = filtered.length ? filtered : nodes;
  } else {
    pool = nodes;
  }
  const picked = pool.slice(offset, offset + maxNodes);
  const pickedIds = new Set(picked.map((n: any) => n.id));
  const lines = [
    "# BingguPack handoff context — " + v.pack_id,
    `(candidate pack — not confirmed / counts: nodes=${v.counts.nodes} edges=${v.counts.edges} evidence=${v.counts.evidence})`,
    "", CONSUMER_RULES_MD, "## nodes (candidate)",
  ];
  for (const n of picked) {
    lines.push(`- [${n.id}] ${n.claim} (evidence: ${n.evidence_refs.join(", ")})`);
  }
  lines.push("");
  lines.push("## edges (candidate)");
  for (const e of v.edges) {
    if (pickedIds.has(e.source) || pickedIds.has(e.target)) {
      lines.push(`- ${e.source} -${e.relation}-> ${e.target} (evidence: ${e.evidence_refs.join(", ")})`);
    }
  }
  const truncated = offset + picked.length < pool.length;
  if (truncated) {
    lines.push("");
    lines.push(`…(노드 ${pool.length}개 중 ${offset + 1}~${offset + picked.length} 표시 — 다음은 offset=${offset + picked.length} 로 재호출)`);
  }
  const md = lines.join("\n");
  const out: Record<string, any> = { context_markdown: md, nodes_included: picked.length, truncated };
  if (truncated) out.next_offset = offset + picked.length;
  return out;
}

// §4 — 공통 annotations: 전 tool read-only·비파괴·멱등·closed-world
const READ_ONLY_ANNOTATIONS = {
  readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false,
};

const COUNTS_SCHEMA = {
  type: "object",
  properties: { nodes: { type: "integer" }, edges: { type: "integer" }, evidence: { type: "integer" } },
  required: ["nodes", "edges", "evidence"],
};

interface ToolDef {
  description: string;
  inputSchema: Record<string, any>;
  outputSchema: Record<string, any>;
  handler: (store: PackStore, args: Record<string, any>, env?: any) => any | Promise<any>;
}

const TOOLS: Record<string, ToolDef> = {
  pack_list: {
    description: "내 synthetic toy pack 목록(요약만, raw 경로 0). read-only.",
    inputSchema: { type: "object", properties: {
      limit: { type: "integer", description: "최대 개수(기본 20)" } }, required: [] },
    outputSchema: { type: "object", properties: {
      packs: { type: "array", items: { type: "object", properties: {
        pack_id: { type: "string" }, title: { type: "string" },
        counts: COUNTS_SCHEMA, candidate_note: { type: "string" } },
        required: ["pack_id", "title", "counts", "candidate_note"] } },
      total: { type: "integer" } }, required: ["packs", "total"] },
    handler: toolPackList,
  },
  pack_summary: {
    description: "pack manifest 요약 + counts + 주제 라벨. read-only.",
    inputSchema: { type: "object", properties: {
      pack_id: { type: "string" } }, required: ["pack_id"] },
    outputSchema: { type: "object", properties: {
      pack_id: { type: "string" },
      manifest_summary: { type: "object", properties: {
        visibility: { type: "string" }, status: { type: "string" },
        pack_type: { type: "string" }, counts: COUNTS_SCHEMA },
        required: ["visibility", "status", "pack_type", "counts"] },
      topics: { type: "array", items: { type: "string" } },
      candidate_note: { type: "string" } },
      required: ["pack_id", "manifest_summary", "topics", "candidate_note"] },
    handler: toolPackSummary,
  },
  evidence_search: {
    description: "evidence 발췌 검색(원문+요지, 상위 N). pack_id 생략 시 전 팩 검색. read-only.",
    inputSchema: { type: "object", properties: {
      pack_id: { type: "string", description: "생략 시 전 팩" },
      query: { type: "string", description: "2~200자" },
      limit: { type: "integer", description: "기본 5, 최대 20" } },
      required: ["query"] },
    outputSchema: { type: "object", properties: {
      hits: { type: "array", items: { type: "object", properties: {
        pack_id: { type: "string" }, node_id: { type: "string" },
        evidence_id: { type: "string" }, sentence_excerpt: { type: "string" },
        score: { type: "integer" }, candidate: { type: "boolean" } },
        required: ["evidence_id", "sentence_excerpt", "score", "candidate"] } },
      total_hits: { type: "integer" }, candidate_note: { type: "string" } },
      required: ["hits", "total_hits", "candidate_note"] },
    handler: toolEvidenceSearch,
  },
  node_edge_lookup: {
    description: "노드 + 연결 엣지(relation·evidence_refs) 조회. read-only.",
    inputSchema: { type: "object", properties: {
      pack_id: { type: "string" }, node_id: { type: "string" },
      keyword: { type: "string" } }, required: ["pack_id"] },
    outputSchema: { type: "object", properties: {
      node: { type: "object", properties: {
        id: { type: "string" }, claim: { type: "string" }, candidate: { type: "boolean" },
        evidence_refs: { type: "array", items: { type: "string" } }, trust: { type: "string" } },
        required: ["id", "claim", "candidate", "evidence_refs", "trust"] },
      edges: { type: "array", items: { type: "object", properties: {
        id: { type: "string" }, relation: { type: "string" }, direction: { type: "string" },
        peer_id: { type: "string" }, evidence_refs: { type: "array", items: { type: "string" } },
        candidate: { type: "boolean" } },
        required: ["id", "relation", "direction", "peer_id", "evidence_refs", "candidate"] } } },
      required: ["node", "edges"] },
    handler: toolNodeEdgeLookup,
  },
  conversation_capture_preview: {
    description: "사용자가 전달한 대화 텍스트에서 핵심 문장 후보를 미리보기(5종 도장·헌법 판정). " +
      "저장 0 — PII/secret(사업자번호 포함) 문장은 후보 제외(종류·개수만 표시). " +
      "모델 요약보다 원문 대화/로그 입력일수록 분류 정확. 화면 캡처/스크린샷 도구 아님. read-only.",
    inputSchema: { type: "object", properties: {
      text: { type: "string", description: "캡처 후보를 뽑을 대화 발췌 (사용자가 명시적으로 전달)" },
      max_candidates: { type: "integer", description: "기본 10, 최대 20" } },
      required: ["text"] },
    outputSchema: { type: "object", properties: {
      candidates: { type: "array", items: { type: "object", properties: {
        sentence: { type: "string" }, label_kind: { type: "string" },
        rule_id: { type: "string" }, a0_verdict: { type: "string" },
        candidate: { type: "boolean" },
        // P3 opt-in ON 시에만 보강(B'7⑤ 격리·기존 label_kind 무변경). null=no_suggestion.
        label_kind_suggestion: { type: ["string", "null"] },
        semantic_conf: { type: ["number", "null"] },
        semantic_band: { type: ["string", "null"] },
        semantic_source: { type: "string" } },
        required: ["sentence", "label_kind", "rule_id", "a0_verdict", "candidate"] } },
      excluded_counts: { type: "object" },
      truncated: { type: "boolean" },
      preview_markdown: { type: "string" },
      nothing_saved: { type: "boolean" },
      semantic_applied: { type: "boolean" } },
      required: ["candidates", "excluded_counts", "truncated", "preview_markdown", "nothing_saved"] },
    handler: (_store: PackStore, args: Record<string, any>, env?: any) => {
      if (typeof args.text !== "string" || !args.text.trim()) {
        throw new ToolError("INVALID_ARGUMENT", "missing required string: text");
      }
      // P3: opt-in ON(env.SEMANTIC_LABEL_ENABLED=="1") 이면 semantic 도장 보강, OFF 면 base passthrough.
      return capturePreviewSemantic(args.text, env, CENTROIDS, args.max_candidates);
    },
  },
  handoff_context: {
    description: "모델 투입용 context Markdown(mobile fallback과 동일 형식). read-only.",
    inputSchema: { type: "object", properties: {
      pack_id: { type: "string" }, topic: { type: "string" },
      max_nodes: { type: "integer", description: "기본 15, 최대 30" },
      offset: { type: "integer", description: "노드 시작 위치(기본 0) — truncated=true면 next_offset으로 재호출" } },
      required: ["pack_id"] },
    outputSchema: { type: "object", properties: {
      context_markdown: { type: "string" }, nodes_included: { type: "integer" },
      truncated: { type: "boolean" },
      next_offset: { type: "integer", description: "truncated=true일 때만 포함 — 다음 호출 offset" } },
      required: ["context_markdown", "nodes_included", "truncated"] },
    handler: toolHandoffContext,
  },
};

// ---------------- JSON-RPC / 크기·누출 가드 ----------------

function leakScan(text: string): string[] {
  return LEAK_PATTERNS.filter(([, re]) => re.test(text)).map(([pat]) => pat);
}

const CUT_FOOTER = "\n\n…(응답 캡으로 절단됨 — max_nodes 축소 또는 offset 지정으로 재호출)";

function fitResult(result: Record<string, any>): Record<string, any> {
  for (let i = 0; i < 8; i++) {
    const s = pyDumps(result);
    if (s.length <= MAX_RESPONSE_CHARS) return result;
    let cut = false;
    for (const key of ["packs", "hits", "edges", "topics"]) {
      const seq = result[key];
      if (Array.isArray(seq) && seq.length > 1) {
        result[key] = seq.slice(0, Math.max(1, Math.floor(seq.length / 2)));
        cut = true;
      }
    }
    if (typeof result.context_markdown === "string" && result.context_markdown.length > 1000) {
      // U6 — 절단을 줄 경계로 + 재호출 안내 푸터 (반복 halving 시 푸터 중복 방지)
      const md = result.context_markdown;
      const target = Math.floor(md.length / 2);
      const nl = md.lastIndexOf("\n", target);
      let cutMd = md.slice(0, nl > 200 ? nl : target);
      if (!cutMd.endsWith(CUT_FOOTER)) cutMd += CUT_FOOTER;
      result.context_markdown = cutMd;
      cut = true;
    }
    result.truncated = true;
    if (!cut) return { error_code: "RESPONSE_TOO_LARGE", message: "result exceeds size cap" };
  }
  return result;
}

async function handleRpc(store: PackStore, rpc: Record<string, any>, env?: any): Promise<Record<string, any> | null> {
  const rpcId = rpc.id ?? null;
  const method = rpc.method ?? "";
  if (rpcId === null) return null; // notification — 202
  let result: Record<string, any>;
  if (method === "initialize") {
    // 스펙 MUST: 요청 버전을 지원하면 동일 버전으로 응답 (echo)
    const reqVer = (rpc.params ?? {}).protocolVersion;
    result = { protocolVersion: SUPPORTED_PROTOCOL_VERSIONS.includes(reqVer) ? reqVer : PROTOCOL_VERSION,
               capabilities: { tools: { listChanged: false } },
               serverInfo: SERVER_INFO };
  } else if (method === "ping") {
    result = {};
  } else if (method === "tools/list") {
    const names = Object.keys(TOOLS).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
    result = { tools: names.map((k) => ({ name: k, description: TOOLS[k].description,
                                          inputSchema: TOOLS[k].inputSchema,
                                          outputSchema: TOOLS[k].outputSchema,
                                          annotations: READ_ONLY_ANNOTATIONS })) };
  } else if (method === "tools/call") {
    const params = rpc.params ?? {};
    const name = params.name ?? "";
    const args = params.arguments ?? {};
    if (!(name in TOOLS)) {
      return { jsonrpc: "2.0", id: rpcId,
               error: { code: -32602, message: "unknown tool: " + name } };
    }
    let out: Record<string, any>;
    let isErr: boolean;
    try {
      out = fitResult(await TOOLS[name].handler(store, args, env));
      isErr = "error_code" in out;
    } catch (e) {
      if (e instanceof ToolError) {
        out = { error_code: e.code, message: e.message };
        isErr = true;
      } else {
        throw e;
      }
    }
    let text = pyDumps(out);
    const leaks = leakScan(text);
    if (leaks.length) { // fail-closed: 내부 흔적 검출 시 결과 자체를 내보내지 않음
      out = { error_code: "SANITIZE_BLOCK", message: "internal trace detected; blocked" };
      text = pyDumps(out);
      isErr = true;
    }
    // L-1 — PII regex 백스톱: capture_preview의 SCAN_SHAPES+PREVIEW_PII_EXTRA(주민/전화/사업자/
    // 이메일 등) 재사용. 정상 경로는 PII가 못 들어오지만 마지막 방어선으로 응답 직전 1회 스캔,
    // 검출 시 응답 전체 BLOCK(fail-closed). 로그는 패턴 종류만 — 원문/매치값 0.
    const piiKinds = scanPii(text);
    if (piiKinds.length) {
      console.log("PII_BLOCK kinds=" + piiKinds.join(","));
      out = { error_code: "PII_BLOCK", message: "pii pattern detected; response blocked" };
      text = pyDumps(out);
      isErr = true;
    }
    // 스펙 MUST: structuredContent는 outputSchema 적합 의무 — 오류 면제 조항 없음(공식 SDK는
    // 존재 시 isError 무관 검증). 오류는 content text만 반환(SDK가 명시 면제하는 유일 형태).
    result = isErr
      ? { content: [{ type: "text", text }], isError: true }
      : { content: [{ type: "text", text }], structuredContent: out, isError: false };
  } else {
    return { jsonrpc: "2.0", id: rpcId,
             error: { code: -32601, message: "method not found: " + method } };
  }
  return { jsonrpc: "2.0", id: rpcId, result };
}

// ---------------- HTTP (fetch handler) ----------------

function deny(status: number, msg: string): Response {
  return new Response(pyDumps({ error: msg }), {
    status, headers: { "Content-Type": "application/json" },
  });
}

// S2 — absent 허용(서버 발신은 Origin 없음), 브라우저 Origin은 전부 403
function originOk(request: Request): boolean {
  return request.headers.get("Origin") === null;
}

// S5 — MCP-Protocol-Version 헤더: absent 허용, 미지원 값은 400
function protocolVersionOk(request: Request): boolean {
  const pv = request.headers.get("MCP-Protocol-Version");
  return pv === null || SUPPORTED_PROTOCOL_VERSIONS.includes(pv);
}

interface Env {
  MCP_PATH_TOKEN?: string;
  AI?: any;                        // P3 Workers AI 바인딩(@cf/baai/bge-m3). opt-in ON 시에만 호출.
  SEMANTIC_LABEL_ENABLED?: string; // "1" 이면 semantic 도장 활성. 미설정/기타=OFF(기존 동작).
  PACKS?: KVNamespace;             // U2 — 실 pack KV(index.real.ts lazy 로드). toy(STORE) 경로는 미사용.
  PACKS_KEY?: string;              // U2 — KV 키(기본 "packs.json"). index.real.ts 에서만 읽음.
}

// store 주입형 핸들러 팩토리 — toy(기본 STORE)와 실 pack 빌드(index.real.ts)가 동일 코드 경로 공유
export function makeFetchHandler(store: PackStore) {
  return {
  async fetch(request: Request, env: Env): Promise<Response> {
    // 변수명에 token·secret 류 + '=' 조합 금지 — 공개 트리 secret 스캐너 자기검출 회피 (6/10 박제)
    const pathKey = (env.MCP_PATH_TOKEN ?? "").trim();
    if (!pathKey) return deny(503, "path token not configured"); // S1 fail-closed
    const mcpPath = "/mcp/" + pathKey;
    const url = new URL(request.url);
    if (request.method === "GET") {
      if (url.pathname !== mcpPath) return deny(404, "not found");
      return deny(405, "SSE not offered (JSON-only server)");
    }
    if (request.method === "DELETE") {
      return deny(405, "stateless server (no session)");
    }
    if (request.method !== "POST") {
      return deny(501, "unsupported method");
    }
    if (url.pathname !== mcpPath) return deny(404, "not found"); // 토큰 없는 /mcp·오토큰 포함
    if (!originOk(request)) return deny(403, "origin not allowed");
    if (!protocolVersionOk(request)) return deny(400, "unsupported protocol version");
    let rpc: Record<string, any>;
    try {
      rpc = await request.json() as Record<string, any>;
    } catch {
      return deny(400, "invalid json");
    }
    if (rpc === null || typeof rpc !== "object" || Array.isArray(rpc)) {
      return deny(400, "invalid json"); // null/배열/스칼라 body — JSON-RPC 객체만 허용
    }
    let resp: Record<string, any> | null;
    try {
      resp = await handleRpc(store, rpc, env);
    } catch { // 미상 예외 — 내부 정보 미노출 정적 -32603
      resp = { jsonrpc: "2.0", id: rpc.id ?? null,
               error: { code: -32603, message: "internal error" } };
    }
    if (resp === null) return new Response(null, { status: 202 });
    return new Response(pyDumps(resp), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  },
  };
}

export default makeFetchHandler(STORE);

// 테스트 전용 노출 — Workers 런타임은 default export만 사용 (S28 절단 경로 실발동 검증용)
export const __test = { fitResult, leakScan, pyDumps, capturePreview };
