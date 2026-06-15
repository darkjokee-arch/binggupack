// embed_probe.ts — centroid 생성 전용 임시 임베드 endpoint (P2, 4cli B'7 ④).
// wrangler dev --remote 로만 띄워 seed 를 @cf/baai/bge-m3 로 실임베드. 배포(deploy) 안 함.
// 로컬 centroid 생성 후 폐기 — 라이브 라우트/secret 없음.
export interface Env { AI: any; }

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method !== "POST") return new Response("POST only", { status: 405 });
    try {
      const body = await req.json() as { text?: string };
      const text = (body.text || "").toString();
      if (!text) return Response.json({ embedding: null, dim: 0, error: "empty" });
      const r = await env.AI.run("@cf/baai/bge-m3", { text: [text] });
      const vec = (r && r.data && Array.isArray(r.data[0])) ? r.data[0] : null;
      return Response.json({ embedding: vec, dim: vec ? vec.length : 0 });
    } catch (e: any) {
      return Response.json({ embedding: null, dim: 0, error: String(e?.message || e) }, { status: 500 });
    }
  },
};
