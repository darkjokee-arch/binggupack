// capture_preview_semantic.ts — hosted 도장(label_kind) semantic 분류 (4cli 20260615_1715 합의 B'7).
// 로컬(binggu_canonical_semantic.py)과 동일 모델(@cf/baai/bge-m3)·동일 seed centroid 로 일원화.
//
// 합의 B'7 정합:
//   ①(버전핀) centroids JSON 의 model 이 EXPECT_MODEL 과 다르면 semantic 비활성 → RULES fallback (drift 차단).
//   ②(라벨별 임계) band 임계는 centroids JSON 산출물(band_hi/band_lo) 사용 — 코드 하드코딩 아님.
//   ③(fallback no_suggestion) semantic 실패 → RULES → RULES 도 fallback_judgment 면 판단 강제 금지, label_kind=null(no_suggestion).
//   ④(hosted 전용 재산출) centroids 는 Workers AI 로 재계산된 JSON (로컬 복붙 아님 — binggu_hosted_centroid_gen.py --workers-ai).
//   ⑤(cos 격리) 결과는 label_kind_suggestion 필드 전용. should_capture/confirm/저장 경로와 비연결(이 모듈은 분류 제안만, write 0).
//
// 영구금지: opt-in OFF 기본(env.SEMANTIC_LABEL_ENABLED !== "1" 이면 Workers AI 호출 0). cos 는 도장 제안에만.

import { capturePreview } from "./capture_preview";

const EXPECT_MODEL = "@cf/baai/bge-m3";

export interface Centroids {
  version: string;
  model: string;
  dimension: number;
  normalization: string;
  seed_hash: string;
  band_hi: number;
  band_lo: number;
  kinds: string[];
  centroids: Record<string, number[]>;
}

export interface LabelSuggestion {
  label_kind_suggestion: string | null; // null = no_suggestion (판단 강제 금지)
  conf: number | null;
  band: string | null;                  // hi | ambiguous | lo | null
  source: string;                       // "semantic" | "rules" | "no_suggestion"
}

function l2(v: number[]): number[] {
  let n = 0;
  for (const x of v) n += x * x;
  n = Math.sqrt(n) || 1.0;
  return v.map((x) => x / n);
}

function dot(a: number[], b: number[]): number {
  let s = 0;
  const m = Math.min(a.length, b.length);
  for (let i = 0; i < m; i++) s += a[i] * b[i];
  return s;
}

// 순수 함수 — 임베딩 벡터 → centroid 최근접 도장. 테스트 가능(Workers AI 비의존).
export function classifyByEmbedding(
  embedding: number[] | null,
  cent: Centroids,
): { kind: string; conf: number; band: string } | null {
  if (!embedding || !embedding.length) return null;
  if (cent.model !== EXPECT_MODEL) return null;       // ① drift/버전 불일치 → fallback
  const e = l2(embedding);
  let best: string | null = null;
  let bs = -2.0;
  for (const k of cent.kinds) {
    const c = cent.centroids[k];
    if (!c) continue;
    const s = dot(e, c);
    if (s > bs) {
      bs = s;
      best = k;
    }
  }
  if (best === null) return null;
  const band = bs >= cent.band_hi ? "hi" : (bs < cent.band_lo ? "lo" : "ambiguous"); // ② JSON 임계
  return { kind: best, conf: Math.round(bs * 10000) / 10000, band };
}

// Workers AI 임베드 (opt-in ON 시에만 호출). 실패/타임아웃 → null (호출측 RULES fallback).
async function embed(env: any, text: string): Promise<number[] | null> {
  try {
    const r = await env.AI.run(EXPECT_MODEL, { text: [text] });
    const data = r?.data;
    if (Array.isArray(data) && Array.isArray(data[0])) return data[0] as number[];
    return null;
  } catch {
    return null;
  }
}

export function semanticEnabled(env: any): boolean {
  return env && env.SEMANTIC_LABEL_ENABLED === "1"; // 기본 OFF
}

/**
 * 도장 분류 일원화 진입점 (B'7 ③ fallback 사슬).
 * @param ruleResult capture_preview.ts classify() 결과 [kind, ruleId]. fallback_judgment 면 정규식 매치 실패를 뜻함.
 *
 * 사슬: opt-in ON → Workers AI 임베드 → centroid cos
 *   - band hi/ambiguous → semantic 도장(label_kind_suggestion)
 *   - band lo / 임베드 실패 / 버전 불일치 → RULES 결과 사용
 *       · RULES 가 fallback_judgment(정규식 매치 실패) → null = no_suggestion (판단 강제 금지)
 *   opt-in OFF → RULES 그대로 (단 fallback_judgment 는 그대로 둠 — 현 동작 보존, 배포 전이므로)
 */
export async function suggestLabel(
  env: any,
  text: string,
  ruleResult: [string, string],
  cent: Centroids | null,
): Promise<LabelSuggestion> {
  const [ruleKind, ruleId] = ruleResult;
  if (!semanticEnabled(env) || !cent) {
    return { label_kind_suggestion: ruleKind, conf: null, band: null, source: "rules" };
  }
  const e = await embed(env, text);
  const sem = classifyByEmbedding(e, cent);
  if (sem && (sem.band === "hi" || sem.band === "ambiguous")) {
    return { label_kind_suggestion: sem.kind, conf: sem.conf, band: sem.band, source: "semantic" };
  }
  // semantic 미확정 → RULES fallback. RULES 도 매치 실패면 no_suggestion (③ 판단 쏠림 차단).
  if (ruleId === "fallback_judgment") {
    return { label_kind_suggestion: null, conf: sem ? sem.conf : null,
             band: sem ? sem.band : null, source: "no_suggestion" };
  }
  return { label_kind_suggestion: ruleKind, conf: sem ? sem.conf : null,
           band: sem ? sem.band : null, source: "rules" };
}

/**
 * 통합 진입점 (P2) — capture_preview.ts 의 capturePreview 결과에 semantic 도장 제안을 보강.
 * 기존 capturePreview 는 무변경(배포 중 index.ts 안전). opt-in OFF / centroids 없음 → base 그대로 반환(무영향).
 * 보강 필드는 label_kind_suggestion 등 별도 키 — 기존 label_kind/candidate 는 건드리지 않음(B'7 ⑤ 격리).
 */
export async function capturePreviewSemantic(
  text: string,
  env: any,
  cent: Centroids | null,
  maxCandidates?: number,
): Promise<Record<string, any>> {
  const base = capturePreview(text, maxCandidates);
  if (!semanticEnabled(env) || !cent) return base; // 무영향(현 동작 보존)
  for (const c of base.candidates) {
    const sug = await suggestLabel(env, c.sentence, [c.label_kind, c.rule_id], cent);
    c.label_kind_suggestion = sug.label_kind_suggestion;
    c.semantic_conf = sug.conf;
    c.semantic_band = sug.band;
    c.semantic_source = sug.source;
  }
  base.semantic_applied = true;
  return base;
}
