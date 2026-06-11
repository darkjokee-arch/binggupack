// U1 — data/packs.json 검증 로더 (GO-HOSTED-REALPACK-LOCAL).
// fail-closed: 위반 1건이면 throw → Workers 기동 자체가 실패해 잘못된 번들이 라이브로 나가지 않는다.
// consume()은 무수정 — 이 로더는 pack 파일 5종(raw)을 Pack 구조로 검증·변환만 한다.

import type { Pack } from "./index";

const FORMAT_VERSION = "opencrab-pack-v1";
const MAX_PACKS = 20;

function fail(msg: string): never {
  throw new Error("LOADPACKS_INVALID: " + msg);
}

function asArr(v: unknown, name: string): any[] {
  if (!Array.isArray(v)) fail(name + " is not an array");
  return v;
}

function asStr(v: unknown, name: string): string {
  if (typeof v !== "string" || !v.trim()) fail(name + " is not a non-empty string");
  return v;
}

export function loadPacks(raw: unknown): Pack[] {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) fail("root is not an object");
  const packsRaw = asArr((raw as any).packs, "packs");
  if (packsRaw.length < 1 || packsRaw.length > MAX_PACKS) fail("packs count out of range (1~" + MAX_PACKS + ")");

  const out: Pack[] = [];
  const seen = new Set<string>();
  for (const p of packsRaw) {
    const manifest = (p ?? {}).manifest;
    if (manifest === null || typeof manifest !== "object") fail("manifest missing");
    if (manifest.format_version !== FORMAT_VERSION) fail("format_version mismatch");
    const packId = asStr(manifest.pack_id, "pack_id");
    if (seen.has(packId)) fail("duplicate pack_id: " + packId);
    seen.add(packId);
    // 불변 게이트: hosted는 candidate pack 전용 — confirmed/승격 경로 원천 차단
    if (manifest.status === "validated") fail("validated pack forbidden (confirmed_allowed must stay false): " + packId);
    if (manifest.promotion_allowed_default) fail("promotion_allowed_default must be false: " + packId);

    const nodes = asArr(p.nodes, packId + ".nodes");
    const edges = asArr(p.edges, packId + ".edges");
    const evIndex = asArr(p.evidence_index, packId + ".evidence_index");
    const evChunk = asArr(p.evidence_chunk, packId + ".evidence_chunk");

    const counts = manifest.counts ?? {};
    if (counts.nodes !== nodes.length || counts.edges !== edges.length || counts.evidence !== evIndex.length) {
      fail("manifest counts mismatch actual rows: " + packId);
    }

    const evIds = new Set<string>();
    for (const ev of evIndex) evIds.add(asStr(ev?.evidence_id, packId + ".evidence_id"));
    for (const c of evChunk) {
      if (!evIds.has(asStr(c?.item_id, packId + ".chunk.item_id"))) {
        fail("chunk item_id not in evidence_index: " + packId);
      }
    }

    for (const [kind, items] of [["node", nodes], ["edge", edges]] as [string, any[]][]) {
      for (const it of items) {
        const itemId = asStr(it?.id, packId + "." + kind + ".id");
        if (it.promotion_allowed) fail(kind + " promotion_allowed must be false: " + itemId);
        if (!(it.properties ?? {}).candidate) fail(kind + " must be candidate: " + itemId);
        const refs = asArr(it.evidence_refs ?? [], itemId + ".evidence_refs");
        if (kind === "node" && refs.length === 0) fail("node without evidence_refs: " + itemId);
        for (const r of refs) {
          if (!evIds.has(r)) fail("evidence_ref not in evidence_index: " + itemId);
        }
      }
    }

    out.push({ manifest, nodes, edges, evIndex, evChunk });
  }
  return out;
}
