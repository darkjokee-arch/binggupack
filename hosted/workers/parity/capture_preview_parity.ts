// S2-6 — py↔ts 골든 대조 하네스. py 가 SSOT, ts 는 같은 파일을 읽어 대조만 한다.
// 숫자 상수 parity 는 "값이 같은 채 분기 로직만 갈린" 경우를 통과시켰다(설계 §7) → 형태로 대조한다.
// 실행: npm run test:parity  (tsc -p tsconfig.parity.json && node dist-parity/parity/capture_preview_parity.js)

import { capturePreview } from "../src/capture_preview";

declare const require: any;
declare const process: any;
declare const __dirname: string;

const fs = require("fs");
const path = require("path");

// 컴파일 출력 위치(dist-parity/parity)와 소스 위치(parity) 양쪽에서 같은 파일을 찾도록 위로 올라가며 탐색.
// ★ build/lib 등 낡은 사본을 집지 않게 `hosted/parity` 고정 경로만 후보로 둔다(설계 §7 경고).
function findGolden(): string {
  let dir = __dirname;
  for (let i = 0; i < 6; i++) {
    const cand = path.join(dir, "parity", "capture_preview_golden.json");
    if (fs.existsSync(cand) && path.basename(dir) === "hosted") return cand;
    dir = path.dirname(dir);
  }
  return path.resolve(__dirname, "..", "..", "..", "parity", "capture_preview_golden.json");
}

const GOLDEN = findGolden();

interface Projected {
  candidates: { sentence: string; label_kind: string; a0_verdict: string }[];
  excluded_counts: Record<string, number>;
  long_candidates: { label: string; length: number; sha: string; blob_suspect: boolean }[];
  truncated: boolean;
}

/** py `_project` 와 동일한 교집합 projection. ts 전용 필드(rule_id·preview_markdown 등)는 제외. */
function project(r: Record<string, any>): Projected {
  return {
    candidates: (r.candidates || []).map((c: any) => ({
      sentence: c.sentence, label_kind: c.label_kind, a0_verdict: c.a0_verdict,
    })),
    excluded_counts: r.excluded_counts || {},
    long_candidates: (r.long_candidates || []).map((x: any) => ({
      label: x.label, length: x.length, sha: x.sha, blob_suspect: x.blob_suspect,
    })),
    truncated: r.truncated,
  };
}

function canon(v: any): string {
  // 키 순서 무관 비교를 위한 결정적 직렬화
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
  return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";
}

function main(): number {
  if (!fs.existsSync(GOLDEN)) {
    console.error("golden not found: " + GOLDEN +
                  "\n  먼저 py 로 생성: python scripts/openbinggu_conversation_capture_preview.py --write-golden");
    return 2;
  }
  const golden = JSON.parse(fs.readFileSync(GOLDEN, "utf-8"));
  const corpus: { case_id: string; input: string }[] = golden.corpus;
  const byId = new Map(corpus.map((c) => [c.case_id, c.input]));

  let checked = 0;
  const diffs: string[] = [];

  for (const run of golden.runs) {
    const longSave: boolean = run.longsave;
    for (const want of run.results) {
      const input = byId.get(want.case_id);
      if (input === undefined) {
        diffs.push(`[${want.case_id}] corpus 에 input 없음`);
        continue;
      }
      const got = project(capturePreview(input, undefined, { longSave }));
      const expected: Projected = {
        candidates: want.candidates, excluded_counts: want.excluded_counts,
        long_candidates: want.long_candidates, truncated: want.truncated,
      };
      checked++;
      if (canon(got) !== canon(expected)) {
        const fields = (["candidates", "excluded_counts", "long_candidates", "truncated"] as const)
          .filter((f) => canon((got as any)[f]) !== canon((expected as any)[f]));
        diffs.push(`[longsave=${longSave} ${want.case_id}] 불일치 필드: ${fields.join(",")}` +
                   `\n    py : ${canon((expected as any)[fields[0]]).slice(0, 240)}` +
                   `\n    ts : ${canon((got as any)[fields[0]]).slice(0, 240)}`);
      }
    }
  }

  // 상수 대조(종전 축 유지 — 형태 대조와 별개로 값도 같아야 한다)
  const constDiff: string[] = [];
  const gc = golden.constants || {};
  const probe = capturePreview("가".repeat(gc.INPUT_CAP + 1), undefined, { longSave: false });
  if (probe.truncated !== true) constDiff.push("INPUT_CAP 경계에서 truncated 미설정");

  console.log(`[parity] cases=${corpus.length} checked=${checked} diffs=${diffs.length}`);
  for (const d of diffs.slice(0, 20)) console.log("  " + d);
  if (diffs.length > 20) console.log(`  ... 외 ${diffs.length - 20}건`);
  for (const d of constDiff) console.log("  [const] " + d);

  const ok = diffs.length === 0 && constDiff.length === 0;
  console.log("PARITY=" + (ok ? "GO" : "NO-GO"));
  return ok ? 0 : 1;
}

process.exit(main());
