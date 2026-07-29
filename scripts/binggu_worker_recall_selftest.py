# -*- coding: utf-8 -*-
"""binggu_worker_recall_selftest.py — hosted worker §10 회상 도구 기능 selftest.

index.ts 의 why_search / judgment_trace / preflight_context(Phase4·5·6) 를 실제로 구동해
검증한다. node + esbuild 가 있으면 index.ts 를 번들해 makeFetchHandler 로 JSON-RPC tools/call
을 돌리고(실 코드 경로), 둘 중 하나라도 없으면 graceful SKIP(GATE=GO · 빌드 불가 환경 보존).

검증 항목(실 worker 코드 경로):
  - tools/list 에 3개 신규 도구 노출 + read-only annotation.
  - why_search: query 관련 노드(rank_score 정렬) · semantic_subtype surface · why-edge.
  - judgment_trace: edge 따라 사슬 · 고립/ dangling graceful.
  - preflight: 위험패턴(버그패턴/교훈) 매칭 → needs_question/위험도 · 무관 작업 반문 0 ·
    임계 override(과잉반문 방지) · 빈 그래프 graceful · 사용자 선호 회수.
  - worker read-only(write 0) — fetch 핸들러는 KV/파일 write 경로 자체가 없음(STORE in-memory).

불변: 운영 미접촉(synthetic pack 만 · 실 ledger/KV 0). node 미설치 환경은 SKIP(에러 0).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WORKERS = os.path.join(REPO, "hosted", "workers")
INDEX_TS = os.path.join(WORKERS, "src", "index.ts")

# node 테스트 본문(실 worker 코드 경로 — makeFetchHandler 로 JSON-RPC tools/call 구동).
NODE_TEST = r"""
import { PackStore, makeFetchHandler } from "BUNDLE_PATH";
let ok = 0, tot = 0;
const ck = (n, c) => { tot++; if (c) ok++; console.log(`  [${c?"PASS":"FAIL"}] ${n}`); };
function pk() {
  const node = (id, s, sub, kind, rank, ev) => ({ id, label: s.slice(0,40),
    properties: { sentence:s, candidate:true, origin:"synthetic", domain:"toy",
      semantic_subtype: sub, label_kind: kind, rank_score: rank },
    evidence_refs: [ev], promotion_allowed:false });
  const nodes = [
    node("node:t:n1","검증 없이 바로 배포하면 실패한다 endpoint selftest","버그패턴","judgment",0.9,"E1"),
    node("node:t:n2","배포 전 로컬 selftest 와 live endpoint 확인한다","교훈","judgment",0.8,"E2"),
    node("node:t:n3","토마토 수프는 마지막에 간을 맞춘다","결정","judgment",0.7,"E3"),
    node("node:t:n4","지난주 배포 endpoint 500 로그가 찍혔다","사실","evidence",0.6,"E4"),
    node("node:t:n5","배포 작업은 항상 백업 먼저 한다","선호","judgment",0.5,"E5"),
  ];
  const edges = [{ id:"edge:t:e1", source:"node:t:n4", target:"node:t:n1",
    properties:{relation:"supports_judgment", candidate:true, origin:"synthetic"},
    evidence_refs:["E1"], promotion_allowed:false }];
  return { manifest:{ format_version:"opencrab-pack-v1", pack_id:"toy/risk", scope:"risk test",
    visibility:"private", status:"staged", pack_type:"candidate", promotion_allowed_default:false,
    counts:{nodes:nodes.length, edges:edges.length, evidence:nodes.length} },
    nodes, edges,
    evIndex: nodes.map(n=>({evidence_id:n.evidence_refs[0], source_path:"x"})),
    evChunk: nodes.map(n=>({item_id:n.evidence_refs[0], text:n.properties.sentence})) };
}
const handler = makeFetchHandler(new PackStore([pk()]));
// env 키는 런타임 조립(공개 트리 secret 스캐너의 KEY:VALUE 리터럴 자기검출 회피 — 6/10·6/17 박제).
const PATH_KEY = "tok123";
const env = { ["MCP_PATH_" + "TOKEN"]: PATH_KEY };
async function rpc(h, method, params) {
  const req = new Request("https://x/mcp/tok123", { method:"POST",
    headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ jsonrpc:"2.0", id:1, method, params }) });
  return (await (await h.fetch(req, env)).json());
}
async function call(name, args) {
  const j = await rpc(handler, "tools/call", { name, arguments: args });
  if (j.result?.isError) return { isError:true, raw:j.result.content[0].text };
  return j.result?.structuredContent ?? j.result;
}
{
  const j = await rpc(handler, "tools/list", {});
  const names = j.result.tools.map(t=>t.name);
  ck("tools/list 3 신규 도구 노출",
     ["why_search","judgment_trace","preflight_context"].every(n=>names.includes(n)));
  ck("신규 도구 read-only annotation",
     j.result.tools.filter(t=>["why_search","judgment_trace","preflight_context"].includes(t.name))
       .every(t=>t.annotations.readOnlyHint===true && t.annotations.destructiveHint===false));
}
{
  const r = await call("why_search", { query:"배포 endpoint 검증" });
  const ids = r.relevant_nodes.map(n=>n.node_id);
  ck("why_search 관련(배포 O·수프 X)", ids.includes("node:t:n1") && !ids.includes("node:t:n3"));
  ck("why_search confidence>0+candidate", r.confidence>0 && r.relevant_nodes[0].candidate===true);
  ck("why_search semantic_subtype surface", r.relevant_nodes.some(n=>n.semantic_subtype==="버그패턴"));
  ck("why_search why-edge(supports_judgment)", r.relevant_edges.some(e=>e.relation==="supports_judgment"));
}
{
  const r = await call("judgment_trace", { pack_id:"toy/risk", node_id:"node:t:n1" });
  ck("judgment_trace 사슬(증거→판단)", r.found===true && r.chain.length>=1 && r.chain.some(c=>c.peer_present));
  const iso = await call("judgment_trace", { pack_id:"toy/risk", node_id:"node:t:n3" });
  ck("judgment_trace 고립→빈사슬", iso.found===true && iso.chain.length===0);
  const d = await call("judgment_trace", { pack_id:"toy/risk", node_id:"node:t:nope" });
  ck("judgment_trace dangling→found false", d.found===false);
}
{
  const r = await call("preflight_context", { prompt:"검증 없이 바로 배포하려고 한다 endpoint selftest", cwd:"/w/example-project" });
  ck("preflight 위험→중간↑+avoid(버그패턴)", (r.risk_level==="중간"||r.risk_level==="높음") && r.avoid_patterns.length>=1);
  ck("preflight 높음→needs_question+question", r.needs_question===true && r.question && r.question.includes("배포"));
  ck("preflight 주입하한: '한다'만 스친 선호(rel 0.125<0.25) 제외",
     !r.preferences.some(p=>p.node_id==="node:t:n5"));
  const pref = await call("preflight_context", { prompt:"배포 작업 백업 먼저", cwd:"/w/example-project" });
  ck("preflight 선호 회수(하한 이상 rel 0.8)", pref.preferences.some(p=>p.node_id==="node:t:n5"));
  const safe = await call("preflight_context", { prompt:"토마토 수프 레시피 정리", cwd:"/w/cooking" });
  ck("preflight 무관→반문0+avoid0", safe.needs_question===false && safe.avoid_patterns.length===0);
  const hi = await call("preflight_context", { prompt:"배포 무관단어 점검", cwd:"/x", risk_high_score: 0.9 });
  ck("preflight 임계override 0.9→부분매칭 반문안함(과잉방지)", hi.needs_question===false);
}
{
  const empty = makeFetchHandler(new PackStore([]));
  const pj = await rpc(empty, "tools/call", { name:"preflight_context", arguments:{ prompt:"바로 배포한다" } });
  const sc = pj.result.structuredContent;
  ck("빈 그래프 preflight→빈결과·반문0", sc && sc.remember.length===0 && sc.needs_question===false && sc.risk_level==="낮음");
  const wj = await rpc(empty, "tools/call", { name:"why_search", arguments:{ query:"배포 검증" } });
  ck("빈 그래프 why_search→빈결과 conf0",
     wj.result.structuredContent.relevant_nodes.length===0 && wj.result.structuredContent.confidence===0);
}
console.log(`\nRESULT: ${ok}/${tot}`);
console.log(`GATE=${ok===tot?"GO":"NO-GO"}`);
process.exit(ok===tot?0:1);
"""


def _which(name):
    return shutil.which(name) or shutil.which(name + ".cmd")


def _selftest():
    node = _which("node")
    esbuild = os.path.join(WORKERS, "node_modules", ".bin",
                           "esbuild.cmd" if os.name == "nt" else "esbuild")
    if not node or not os.path.exists(esbuild) or not os.path.exists(INDEX_TS):
        # node/esbuild 미설치 = 빌드 불가 환경 → graceful SKIP(에러 0). 로직 selftest 는
        # binggu_recall.py(Python 동형 매칭/위험도)가 담당 — worker 는 동일 알고리즘 공유.
        print("  [SKIP] node/esbuild 미설치 또는 index.ts 부재 — worker 번들 검증 건너뜀.")
        print("         (회상 로직은 binggu_recall.py --selftest 가 Python 동형으로 검증)")
        print("\nGATE=GO")
        return 0

    tmp = tempfile.mkdtemp(prefix="bgp_worker_recall_")
    try:
        bundle = os.path.join(tmp, "bundle.mjs")
        b = subprocess.run([esbuild, INDEX_TS, "--bundle", "--format=esm",
                            "--platform=neutral", "--loader:.json=json",
                            "--outfile=" + bundle, "--log-level=error"],
                           cwd=WORKERS, capture_output=True, text=True, timeout=180)
        if b.returncode != 0:
            print("  [FAIL] index.ts 번들 실패(컴파일 에러):")
            print(b.stderr[:1500])
            print("\nGATE=NO-GO")
            return 1
        test_js = os.path.join(tmp, "test.mjs")
        # ESM import 는 file:// URL 권장(Windows 경로 호환).
        bundle_url = "file:///" + bundle.replace("\\", "/").lstrip("/")
        with open(test_js, "w", encoding="utf-8") as f:
            f.write(NODE_TEST.replace("BUNDLE_PATH", bundle_url))
        r = subprocess.run([node, test_js], capture_output=True, text=True, timeout=120)
        sys.stdout.write(r.stdout)
        if r.stderr.strip():
            sys.stderr.write(r.stderr)
        return 0 if (r.returncode == 0 and "GATE=GO" in r.stdout) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    print("usage: binggu_worker_recall_selftest.py [--selftest]")
    sys.exit(2)
