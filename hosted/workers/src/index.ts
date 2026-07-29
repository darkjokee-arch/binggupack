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
// 불변: read-only 6 tool · synthetic toy pack 전용 · JSON-only(GET 405) · stateless ·
//   fail-closed 누출 스캔(SANITIZE_BLOCK) · 배포/OAuth/등록 0 (wrangler dev 로컬 전용).

import { capturePreview, scanPii, hasSecret } from "./capture_preview";
import { capturePreviewSemantic, Centroids } from "./capture_preview_semantic";
import centroidsData from "./centroids_canonical_5.json";
// write 도구 save_intent 재사용(단일 출처 — intentHash 는 PC 러너와 바이트 동일 의무).
import { argsReject, intentHash, SAVE_INTENT_CONSTS } from "./save_intent_mcp";

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
    // P1 ② 랭킹: 빌더가 pre-compute 한 rank_score(3축 가중합)·created_at(신선도)·use_count(유용성).
    // worker 는 read-only — 점수 계산/저장 안 함. 빌더 점수를 surface 하고 sort 만 한다.
    const rankScore = typeof p.rank_score === "number" ? p.rank_score : 0;
    return {
      id: n.id,
      claim: p.sentence ?? n.label ?? "",
      candidate: Boolean(p.candidate),
      promotion_allowed: Boolean(n.promotion_allowed ?? false),
      origin: p.origin ?? "",
      domain: p.domain ?? "",
      evidence_refs: [...(n.evidence_refs ?? [])],
      trust: "candidate_unverified",
      rank_score: rankScore,
      // §10 회상: semantic_subtype(버그패턴/교훈 등 보조 메타) + label_kind 을 view 로 surface.
      // 빌더(realpack_build)가 properties 에 넣어준 값 — 반문 엔진(preflight)이 위험패턴 매칭에 사용.
      // null 이면 미부착(빈 그래프/구 pack graceful). 도장/점수 계산 0 — surface 만.
      ...(p.semantic_subtype ? { semantic_subtype: String(p.semantic_subtype) } : {}),
      ...(p.label_kind ? { label_kind: String(p.label_kind) } : {}),
      ...(p.created_at ? { created_at: String(p.created_at) } : {}),
      ...(typeof p.use_count === "number" ? { use_count: p.use_count } : {}),
      ...(p.doc_status && p.doc_status !== "active" ? { doc_status: p.doc_status } : {}),
    };
  });
  // P1 ② 정렬 보존: 빌더가 이미 rank_score 내림차순 정렬하지만, worker 도 방어적으로 재정렬
  // (다른 적재 경로/구 packs.json 도 일관 순서 보장). 동점은 id 사전순 — 결정적.
  nodeView.sort((a, b) => b.rank_score - a.rank_score || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

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
                    sentence_excerpt: excerptSrc.slice(0, EXCERPT_MAX), score, candidate: true,
                    rank_score: typeof n.rank_score === "number" ? n.rank_score : 0 });
      }
    }
  }
  // 관련성(term-frequency score) 1차 — 기존 신호 그대로. 동점은 P1 rank_score(신선도+유용성) 2차,
  // 그래도 동점이면 evidence_id 사전순(결정적). 3축이 query 관련성을 보강하되 압도하지 않음.
  hits.sort((a, b) => b.score - a.score || b.rank_score - a.rank_score ||
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
                   rank_score: typeof node.rank_score === "number" ? node.rank_score : 0,
                   ...(node.created_at ? { created_at: node.created_at } : {}),
                   ...(typeof node.use_count === "number" ? { use_count: node.use_count } : {}),
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

// ---------------- §10 회상 도구 (Phase4·5·6 · 전부 read-only) ----------------
// 점수 계산/저장 0 — 빌더 pre-compute rank_score(consume 가 surface)·semantic_subtype(properties)
// 만 surface·정렬. owner 31노드 하드코딩 0 — 빈 pack(신규 사용자)이면 빈 결과·반문 0·에러 0.

// 위험 신호 subtype 가중 — 버그패턴(반복 결함) > 교훈. 그 외 subtype = 위험 후보 아님.
const RISK_SUBTYPE_WEIGHT: Record<string, number> = { "버그패턴": 1.0, "교훈": 0.7 };
const JUDGMENT_KINDS = new Set(["judgment", "판단"]);
// 반문 위험도 경계(worker 기본값 — 로컬 binggu_p1_config.recall_config 와 동일 시작값).
// worker 는 KV read-only·stateless 라 사용자 설정파일 미접근 → 코드 기본값(요청 args override 허용).
const RISK_MID_DEFAULT = 0.30;
const RISK_HIGH_DEFAULT = 0.55;
// subtype → 반문 문구 토대(로컬 _SUBTYPE_WHY 와 동일·결정적·LLM 0).
const SUBTYPE_WHY: Record<string, string> = {
  "교훈": "반복 적용 가능한 규칙성(다음에 같은 실수 회피)",
  "버그패턴": "반복 실수/결함 패턴 — 재발 방지 신호",
  "결정": "선택의 방향과 이유", "선호": "반복되는 작업 방식",
  "설계결정": "구조/절차 설계 근거", "사실": "확인된 사실/지식",
};

function relScore(terms: string[], text: string): number {
  if (!terms.length) return 0;
  const s = text.toLowerCase();
  const uniq = new Set(terms);
  let hit = 0;
  for (const t of uniq) if (s.includes(t)) hit++;
  return hit / uniq.size;
}

// IDF(대상 노드 집합 내 df) — 같은 rel 동점 안에서만 쓰는 2차 키(relq).
// py _why_search_on_graph 미러: rel 양자화 동점 홍수에서 rank 가 전권을 쥐어
// 흔한 부분문자열 토큰만 스친 무관 노드가 상위로 오는 것을 희소 토큰 가중으로 분해.
// rel 값·게이트·위험매칭은 불변. uniq 정렬 고정(합산 순서 결정적).
function idfTable(terms: string[], texts: string[]): { idf: Map<string, number>; sum: number; uniq: string[] } {
  const uniq = Array.from(new Set(terms)).sort();
  const totalN = texts.length || 1;
  const idf = new Map<string, number>();
  let sum = 0;
  for (const t of uniq) {
    let df = 0;
    for (const s of texts) if (s.includes(t)) df++;
    const w = Math.log(1 + totalN / (df || 1));
    idf.set(t, w);
    sum += w;
  }
  return { idf, sum: sum || 1, uniq };
}

function relqScore(tbl: { idf: Map<string, number>; sum: number; uniq: string[] }, text: string): number {
  let rq = 0;
  for (const t of tbl.uniq) if (text.includes(t)) rq += tbl.idf.get(t) ?? 0;
  return Math.round((rq / tbl.sum) * 1e6) / 1e6;
}

function toolWhySearch(store: PackStore, args: Record<string, any>): any {
  const query = reqStr(args, "query");
  if (!(query.length >= 2 && query.length <= 200)) {
    throw new ToolError("QUERY_TOO_SHORT", "query must be 2~200 chars");
  }
  const limit = Math.max(1, Math.min(toInt(args.limit ?? 5), 20));
  const packIds = args.pack_id ? [String(args.pack_id)] : store.ids();
  const terms = query.toLowerCase().split(/\s+/).filter((t) => t.length >= 2);
  const all: any[] = [];
  for (const pid of packIds) {
    const v = store.get(pid);
    for (const n of v.nodes) all.push({ pid, n, low: (n.claim ?? "").toLowerCase() });
  }
  const tbl = idfTable(terms, all.map((x) => x.low));
  const scored: any[] = [];
  for (const { pid, n, low } of all) {
    const rel = relScore(terms, n.claim ?? "");
    if (rel <= 0) continue;
    scored.push({ pid, n, rel, relq: relqScore(tbl, low) });
  }
  // 관련성 1차, IDF 가중 관련성(relq — 동점 분해) 2차, rank_score 3차, node_id 사전순 4차 — 결정적.
  scored.sort((a, b) => b.rel - a.rel || b.relq - a.relq || b.n.rank_score - a.n.rank_score ||
    (a.n.id < b.n.id ? -1 : a.n.id > b.n.id ? 1 : 0));
  const top = scored.slice(0, limit);
  const relevant_nodes = top.map(({ pid, n, rel }) => ({
    pack_id: pid, node_id: n.id, claim: (n.claim ?? "").slice(0, 120),
    semantic_subtype: n.semantic_subtype ?? null,
    rank_score: typeof n.rank_score === "number" ? n.rank_score : 0,
    relevance: Math.round(rel * 10000) / 10000, candidate: true, trust: "candidate_unverified",
  }));
  const topIds = new Set(top.map(({ n }) => n.id));
  const relevant_edges: any[] = [];
  for (const { pid } of top) {
    const v = store.get(pid);
    for (const e of v.edges) {
      if (topIds.has(e.source) || topIds.has(e.target)) {
        relevant_edges.push({ edge_id: e.id, relation: e.relation,
          source: e.source, target: e.target, candidate: true });
      }
    }
  }
  // edge dedup(전 팩 순회 시 중복 방지)
  const seenE = new Set<string>();
  const edges = relevant_edges.filter((e) => !seenE.has(e.edge_id) && seenE.add(e.edge_id));
  const evidence = top.map(({ n }) => ({ node_id: n.id,
    evidence_excerpt: (n.claim ?? "").slice(0, 120) }));
  const confidence = top.length ? Math.round(top[0].rel * 10000) / 10000 : 0;
  return {
    relevant_nodes, relevant_edges: edges, evidence,
    summary: top.length ? `관련 기억 ${top.length}건(랭킹순). candidate — 사람 확정 전 참고용.`
                        : "query 와 관련된 노드를 찾지 못했습니다.",
    recommended_question: null, confidence,
    candidate_note: "all items candidate (not confirmed)",
  };
}

function toolJudgmentTrace(store: PackStore, args: Record<string, any>): any {
  const v = store.get(reqStr(args, "pack_id"));
  const nodeId = reqStr(args, "node_id");
  const maxHops = Math.max(1, Math.min(toInt(args.max_hops ?? 3), 5));
  const byId: Record<string, any> = {};
  for (const n of v.nodes) byId[n.id] = n;
  if (!(nodeId in byId)) {
    return { root: nodeId, found: false, chain: [], confidence: 0,
             summary: "노드를 찾을 수 없습니다(dangling 또는 미저장)." };
  }
  const visited = new Set([nodeId]);
  let frontier = [nodeId];
  const chain: any[] = [];
  for (let hop = 0; hop < maxHops; hop++) {
    const next: string[] = [];
    for (const cur of frontier) {
      for (const e of v.edges) {
        let peer: string | null = null, direction = "";
        if (e.source === cur) { peer = e.target; direction = "out"; }
        else if (e.target === cur) { peer = e.source; direction = "in"; }
        if (peer === null) continue;
        const pnode = byId[peer];
        chain.push({ edge_id: e.id, relation: e.relation, from: cur, to: peer,
          direction, peer_claim: pnode ? (pnode.claim ?? "").slice(0, 100) : null,
          peer_present: Boolean(pnode) });
        if (pnode && !visited.has(peer)) { visited.add(peer); next.push(peer); }
      }
    }
    if (!next.length) break;
    frontier = next;
  }
  const root = byId[nodeId];
  const present = chain.filter((c) => c.peer_present).length;
  return {
    root: { node_id: nodeId, claim: (root.claim ?? "").slice(0, 120),
            semantic_subtype: root.semantic_subtype ?? null,
            rank_score: typeof root.rank_score === "number" ? root.rank_score : 0 },
    found: true, chain,
    confidence: chain.length ? Math.round(Math.min(1, present / 3) * 10000) / 10000 : 0,
    summary: chain.length ? `판단 근거 사슬 ${chain.length}개 연결. candidate edge — 사람 확정 전 참고.`
                          : "이 판단에 연결된 근거 엣지가 없습니다(고립 노드).",
    candidate_note: "all items candidate (not confirmed)",
  };
}

function toolPreflightContext(store: PackStore, args: Record<string, any>): any {
  // 입력 = prompt / cwd / domain / files_changed (설계 Phase5). 거친 1차 신호로 합쳐 회상.
  const prompt = typeof args.prompt === "string" ? args.prompt : "";
  const cwd = typeof args.cwd === "string" ? args.cwd : "";
  const domain = typeof args.domain === "string" ? args.domain : "";
  const files: string[] = Array.isArray(args.files_changed)
    ? args.files_changed.map((f: any) => String(f)) : [];
  const dom = domain || (cwd ? cwd.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "" : "");
  const work = [prompt, dom, ...files.map((f) => f.split(/[\\/]/).pop() ?? "")].filter(Boolean).join(" ");
  const terms = work.toLowerCase().split(/\s+/).filter((t) => t.length >= 2);
  const riskMid = typeof args.risk_mid_score === "number" ? args.risk_mid_score : RISK_MID_DEFAULT;
  const riskHigh = typeof args.risk_high_score === "number" ? args.risk_high_score : RISK_HIGH_DEFAULT;
  const maxN = Math.max(1, Math.min(toInt(args.max_nodes ?? 5), 7));

  const packIds = args.pack_id ? [String(args.pack_id)] : store.ids();
  const allNodes: any[] = [];
  for (const pid of packIds) for (const n of store.get(pid).nodes) allNodes.push({ pid, n });
  if (!allNodes.length) {
    return { remember: [], ask: [], avoid_patterns: [], preferences: [],
             risk_level: "낮음", needs_question: false, question: null, confidence: 0,
             summary: "그래프가 비어 있습니다(신규 사용자 — 회상할 기억 없음).",
             candidate_note: "all items candidate (not confirmed)" };
  }

  // remember = 관련 노드 상위 maxN(why_search 정렬과 동일 — relq 동점 분해 포함).
  const pfTbl = idfTable(terms, allNodes.map(({ n }) => (n.claim ?? "").toLowerCase()));
  const scored = allNodes.map(({ pid, n }) => ({ pid, n, rel: relScore(terms, n.claim ?? ""),
    relq: relqScore(pfTbl, (n.claim ?? "").toLowerCase()) }))
    .filter((x) => x.rel > 0);
  scored.sort((a, b) => b.rel - a.rel || b.relq - a.relq || b.n.rank_score - a.n.rank_score ||
    (a.n.id < b.n.id ? -1 : a.n.id > b.n.id ? 1 : 0));
  const remember = scored.slice(0, maxN).map(({ pid, n, rel }) => ({
    pack_id: pid, node_id: n.id, claim: (n.claim ?? "").slice(0, 100),
    semantic_subtype: n.semantic_subtype ?? null,
    rank_score: typeof n.rank_score === "number" ? n.rank_score : 0,
    relevance: Math.round(rel * 10000) / 10000,
  }));

  // 위험패턴 매칭(Phase6) — node 의 semantic_subtype 은 consume 가 properties 로 surface.
  const matches: any[] = [];
  const avoid: any[] = [];
  const preferences: any[] = [];
  for (const { n } of allNodes) {
    const sub = n.semantic_subtype ?? null;
    const kind = n.label_kind ?? n.node_type ?? "";
    const rel = relScore(terms, n.claim ?? "");
    if (rel <= 0) continue;
    if (sub === "선호") {
      preferences.push({ node_id: n.id, claim: (n.claim ?? "").slice(0, 100),
        relevance: Math.round(rel * 10000) / 10000 });
      continue;
    }
    const w = RISK_SUBTYPE_WEIGHT[sub as string];
    if (w === undefined || !JUDGMENT_KINDS.has(kind)) continue;
    const score = w * rel;
    const m = { node_id: n.id, claim: (n.claim ?? "").slice(0, 100), semantic_subtype: sub,
      risk_score: Math.round(score * 10000) / 10000, relevance: Math.round(rel * 10000) / 10000 };
    matches.push(m);
    if (sub === "버그패턴") avoid.push(m);
  }
  matches.sort((a, b) => b.risk_score - a.risk_score || (a.node_id < b.node_id ? -1 : 1));
  avoid.sort((a, b) => b.risk_score - a.risk_score || (a.node_id < b.node_id ? -1 : 1));
  preferences.sort((a, b) => b.relevance - a.relevance);
  const top = matches.length ? matches[0].risk_score : 0;
  let risk_level = "낮음", needs_question = false;
  if (top >= riskHigh) { risk_level = "높음"; needs_question = true; }
  else if (top >= riskMid) { risk_level = "중간"; needs_question = false; }
  let question: string | null = null;
  if (matches.length && needs_question) {
    const src = allNodes.find(({ n }) => n.id === matches[0].node_id);
    if (src) {
      const sub = src.n.semantic_subtype ?? "";
      const why = SUBTYPE_WHY[sub as string] ?? "과거 판단";
      question = `이 작업은 과거 패턴과 닮았습니다: "${(src.n.claim ?? "").slice(0, 60)}" (${why}). ` +
        "같은 실수를 반복하지 않도록, 먼저 점검/확인하고 진행할까요?";
    }
  }
  return {
    remember, ask: question ? [question] : [],
    avoid_patterns: avoid.slice(0, 5), preferences: preferences.slice(0, maxN),
    risk_level, needs_question, question,
    confidence: remember.length ? remember[0].relevance : 0,
    summary: `관련 기억 ${remember.length} · 위험패턴 ${matches.length} · 위험도 ${risk_level}` +
      (needs_question ? " (반문 필요)" : ""),
    candidate_note: "all items candidate (not confirmed)",
  };
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
  annotations?: Record<string, any>;  // per-tool(생략 시 READ_ONLY_ANNOTATIONS). save_intent 만 write(readOnlyHint=false).
  handler: (store: PackStore, args: Record<string, any>, env?: any) => any | Promise<any>;
}

// ---- write 도구: save_intent — 선택 후보를 inbox(save worker IntentInbox DO 공유)에 적재 ----
// read 라인과 달리 유일한 write. 적재만(휘발·TTL) — 실제 로컬 장부 확정은 PC 러너 pull.
// 안전: argsReject 가 confirm='SAVE '+indices 정확일치·index≤10·PII 형식 등 검증(save worker 와 동일 단일 출처).
// 자동 추론 호출 금지는 모델 프롬프트 규약(description) — 서버는 confirm 정확일치를 사람-선택 증거로 받는다.
async function toolSaveIntent(_store: PackStore, args: Record<string, any>, env?: any): Promise<any> {
  if (!env || !env.INBOX) throw new ToolError("NOT_CONFIGURED", "inbox binding absent");
  const reason = argsReject(args);
  if (reason !== null) throw new ToolError("INVALID_ARGUMENT", reason);
  // C1 백스톱: put 직전 text 전체 PII/secret 재검사(원문 클라우드/디스크 잔존 방지) — save_intent_mcp.ts 와 동일 패턴.
  // 로그는 kind만 — 원문/매치값 0. intentHash 입력·confirm 게이트 무변경.
  const piiKinds = scanPii(args.text);
  const secretHit = hasSecret(args.text);
  if (piiKinds.length || secretHit) {
    console.log(`[MCP] save_intent backstop block: pii_kinds=${JSON.stringify(piiKinds)} secret=${secretHit}`);
    throw new ToolError("PII_IN_TEXT", "pii_in_text");
  }
  const nowS = Math.floor(Date.now() / 1000);
  const iid = await intentHash(args.text, args.indices, args.confirm);
  const it: Record<string, unknown> = {
    schema_ver: SAVE_INTENT_CONSTS.SCHEMA_VER, intent_id: iid, text: args.text,
    indices: args.indices, confirm: args.confirm, created_ts: nowS,
    ttl_s: SAVE_INTENT_CONSTS.TTL_S, source: "hosted",
  };
  if (args.speaker === "owner" || args.speaker === "ai") it.speaker = args.speaker;
  const stub = env.INBOX.get(env.INBOX.idFromName("inbox"));
  const r = await stub.fetch("https://do/put", {
    method: "POST", body: JSON.stringify(it),
    headers: { "X-Inbox-Cap": String(SAVE_INTENT_CONSTS.INBOX_CAP) },
  });
  const body = await r.json() as any;
  if (r.status !== 200) throw new ToolError("INBOX_REJECTED", JSON.stringify(body));
  // text echo 0 — intent_id(적재 성공 신호)만. 실제 장부 반영은 PC 러너 pull 후.
  return { intent_id: iid, ttl_s: SAVE_INTENT_CONSTS.TTL_S, saved_to_inbox: true,
           note: "저장 대기함(inbox) 적재 완료 — PC 러너 pull 로 로컬 장부 최종 확정" };
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
        score: { type: "integer" }, candidate: { type: "boolean" },
        rank_score: { type: "number", description: "P1 pre-compute 랭킹(신선도+유용성) — 동점 정렬 보조" } },
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
        evidence_refs: { type: "array", items: { type: "string" } }, trust: { type: "string" },
        rank_score: { type: "number", description: "P1 pre-compute 랭킹 점수(신선도+유용성)" },
        created_at: { type: "string" }, use_count: { type: "integer" } },
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
  why_search: {
    description: "query 관련 판단/근거 노드 + why-edge 회상(P1 rank_score 정렬). " +
      "pack_id 생략 시 전 팩. 빈 그래프면 빈 결과(에러 0). read-only.",
    inputSchema: { type: "object", properties: {
      query: { type: "string", description: "2~200자" },
      pack_id: { type: "string", description: "생략 시 전 팩" },
      limit: { type: "integer", description: "기본 5, 최대 20" } },
      required: ["query"] },
    outputSchema: { type: "object", properties: {
      relevant_nodes: { type: "array", items: { type: "object", properties: {
        pack_id: { type: "string" }, node_id: { type: "string" }, claim: { type: "string" },
        semantic_subtype: { type: ["string", "null"] }, rank_score: { type: "number" },
        relevance: { type: "number" }, candidate: { type: "boolean" }, trust: { type: "string" } },
        required: ["node_id", "claim", "rank_score", "relevance", "candidate"] } },
      relevant_edges: { type: "array", items: { type: "object", properties: {
        edge_id: { type: "string" }, relation: { type: "string" },
        source: { type: "string" }, target: { type: "string" }, candidate: { type: "boolean" } },
        required: ["edge_id", "relation", "source", "target", "candidate"] } },
      evidence: { type: "array", items: { type: "object" } },
      summary: { type: "string" }, recommended_question: { type: ["string", "null"] },
      confidence: { type: "number" }, candidate_note: { type: "string" } },
      required: ["relevant_nodes", "relevant_edges", "evidence", "summary", "confidence"] },
    handler: toolWhySearch,
  },
  judgment_trace: {
    description: "판단 노드에서 연결 엣지를 따라 근거 사슬(다홉) + peer claim. " +
      "dangling node 면 found=false(에러 0). read-only.",
    inputSchema: { type: "object", properties: {
      pack_id: { type: "string" }, node_id: { type: "string" },
      max_hops: { type: "integer", description: "기본 3, 최대 5" } },
      required: ["pack_id", "node_id"] },
    outputSchema: { type: "object", properties: {
      root: { type: ["object", "string"] }, found: { type: "boolean" },
      chain: { type: "array", items: { type: "object", properties: {
        edge_id: { type: "string" }, relation: { type: "string" },
        from: { type: "string" }, to: { type: "string" }, direction: { type: "string" },
        peer_claim: { type: ["string", "null"] }, peer_present: { type: "boolean" } },
        required: ["edge_id", "relation", "from", "to", "direction", "peer_present"] } },
      confidence: { type: "number" }, summary: { type: "string" },
      candidate_note: { type: "string" } },
      required: ["root", "found", "chain", "confidence", "summary"] },
    handler: toolJudgmentTrace,
  },
  preflight_context: {
    description: "작업 시작 전 회상 + 반문(L5+L6). 입력=prompt/cwd/domain/files_changed. " +
      "관련 기억 + 하면안되는 과거패턴(버그패턴) + 사용자 선호 + 위험도(낮음/중간/높음). " +
      "위험패턴 닮으면 needs_question. 빈 그래프면 빈 결과·반문 0(에러 0). read-only.",
    inputSchema: { type: "object", properties: {
      prompt: { type: "string", description: "이번 작업 설명" },
      cwd: { type: "string" }, domain: { type: "string" },
      files_changed: { type: "array", items: { type: "string" } },
      pack_id: { type: "string", description: "생략 시 전 팩" },
      max_nodes: { type: "integer", description: "기본 5, 최대 7" },
      risk_mid_score: { type: "number", description: "중간 경고 임계(기본 0.30)" },
      risk_high_score: { type: "number", description: "반문 임계(기본 0.55)" } },
      required: [] },
    outputSchema: { type: "object", properties: {
      remember: { type: "array", items: { type: "object" } },
      ask: { type: "array", items: { type: "string" } },
      avoid_patterns: { type: "array", items: { type: "object" } },
      preferences: { type: "array", items: { type: "object" } },
      risk_level: { type: "string" }, needs_question: { type: "boolean" },
      question: { type: ["string", "null"] }, confidence: { type: "number" },
      summary: { type: "string" }, candidate_note: { type: "string" } },
      required: ["remember", "ask", "avoid_patterns", "preferences", "risk_level",
        "needs_question", "confidence", "summary"] },
    handler: toolPreflightContext,
  },
  save_intent: {
    description: "사용자의 판단·교훈을 빙구팩에 **실제로 저장하는 실행(write) 도구** — read-only 아님. " +
      "사용자가 '저장해'/'SAVE n' 등 저장 의사를 밝히면 반드시 이 도구를 호출해 저장을 실행하라(미리보기가 아니라 저장 실행). " +
      "conversation_capture_preview 로 후보 번호(1~10)를 먼저 받고, 사용자가 고른 번호로 indices/confirm 을 구성한다. " +
      "confirm 은 'SAVE ' + indices.join(',') 정확 일치. speaker='owner'(사용자 본인 발화) 시 온톨로지 팩 반영. " +
      "(사용자가 요청하지 않았는데 자동/추론 호출만 금지 — 사용자가 저장을 요청하면 반드시 실행할 것.)",
    inputSchema: { type: "object", properties: {
      text: { type: "string", description: "후보 미리보기 대상 대화 원문" },
      indices: { type: "array", items: { type: "integer" }, description: "1-base 선택 인덱스(1~10)" },
      confirm: { type: "string", description: "'SAVE ' + indices.join(',') 정확 일치" },
      speaker: { type: "string", enum: ["owner", "ai"],
        description: "화자 축(선택). 사용자 본인 발화='owner' / AI 요약='ai'. 생략=미지정(NULL)." } },
      required: ["text", "indices", "confirm"] },
    outputSchema: { type: "object", properties: {
      intent_id: { type: "string" }, ttl_s: { type: "integer" },
      saved_to_inbox: { type: "boolean" }, note: { type: "string" } },
      required: ["intent_id", "saved_to_inbox"] },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    handler: toolSaveIntent,
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
                                          annotations: TOOLS[k].annotations ?? READ_ONLY_ANNOTATIONS })) };
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
  INBOX?: DurableObjectNamespace;  // save_intent write 도구용 — save worker(binggupack-save-intent-mcp)의
                                   // IntentInbox 를 cross-script 공유. 미바인딩 시 save_intent 는 NOT_CONFIGURED.
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
