// save_intent_v2.ts — V2-1: durable inbox + HMAC 서명 (로컬 wrangler dev 한정).
// 설계 정본: docs/BINGGUPACK_SAVE_INTENT_V2_RFC.md (4cli 20260612_1025 both_reject 반영)
// v1 대비 구조 변경: in-memory Map → Durable Object 단일 inbox(전역 의미론) /
//   경로 키 단독 → HMAC-SHA256 요청 서명(재전송 창 ±300s) /
//   휘발 상태 → persistent fail-closed 플래그(기본 off, DO storage).
// 불변: 장부(로컬 SQLite) write 0 · 판정 게이트 = 로컬 러너 · 자동 적용 0 · payload 로깅 0.
// live 노출 = V2-4 owner 명시 GO 전 금지. deploy 금지 — wrangler.save_v2.toml 참조.
// 변수명에 token·secret 류 + '=' 조합 금지 — 공개 트리 스캐너 자기검출 회피 (6/10 박제)

import { SIG_WINDOW_S, sigV2Only, verifySig } from "./save_common";

const SCHEMA_VER = 1;
const TEXT_CAP = 36000;
const INDICES_CAP = 64;
const DEFAULT_TTL_S = 86400;
const TTL_CAP_S = 7 * 86400;
const DEFAULT_INBOX_CAP = 32;      // DO 전역 단일 카운트 — v1의 isolate별 cap 결함 해소

// ---------- 공통 유틸 ----------

function denyJson(status: number, msg: string): Response {
  return new Response(JSON.stringify({ error: msg }), {
    status, headers: { "Content-Type": "application/json" },
  });
}

function okJson(obj: unknown): Response {
  return new Response(JSON.stringify(obj), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
}

// v1과 동일한 모양 검증 — 거부 사유 코드 반환 (통과 = null)
function shapeReject(b: any): string | null {
  if (b === null || typeof b !== "object" || Array.isArray(b)) return "not_object";
  if (b.schema_ver !== SCHEMA_VER) return "schema_mismatch";
  if (typeof b.text !== "string" || b.text.length === 0) return "text_invalid";
  if (b.text.length > TEXT_CAP) return "text_too_large";
  if (!Array.isArray(b.indices) || b.indices.length === 0) return "indices_empty";
  if (b.indices.length > INDICES_CAP) return "indices_too_many";
  if (!b.indices.every((i: any) => Number.isInteger(i) && i >= 1)) return "indices_invalid";
  if (typeof b.confirm !== "string") return "confirm_invalid";
  if (b.confirm !== "SAVE " + b.indices.join(",")) return "confirm_phrase_mismatch";
  if (typeof b.intent_id !== "string" || !/^[0-9a-f]{16}$/.test(b.intent_id)) return "intent_id_invalid";
  if (b.ttl_s !== undefined && (!Number.isInteger(b.ttl_s) || b.ttl_s < 1 || b.ttl_s > TTL_CAP_S)) return "ttl_invalid";
  return null;
}

// ---------- HMAC 서명 검증 (조건 1) ----------
// 단일 출처 = save_common.verifySig — 신형(ts.METHOD.path.bodyhash) 우선,
// SAVE_SIG_V2_ONLY 미설정(기본) 동안 구형(ts.bodyhash)도 수용 (L-4 전환 플래그).

// ---------- Durable Object — 단일 inbox (조건 2·3·4) ----------
// storage 키: "enabled"(bool, 기본 부재=닫힘) / "intent:<id>"(intent)
// 내부 전용 fetch — 외부 노출 0 (worker 핸들러만 stub 호출)

export class IntentInbox {
  state: DurableObjectState;

  constructor(state: DurableObjectState) {
    this.state = state;
  }

  private async purgeExpired(nowS: number): Promise<void> {
    const m = await this.state.storage.list({ prefix: "intent:" });
    for (const [k, v] of m) {
      const it = v as any;
      if (nowS > (it.created_ts ?? 0) + (it.ttl_s ?? DEFAULT_TTL_S)) {
        await this.state.storage.delete(k); // 만료 = 삭제 (마킹 아님 — hosted 잔존 0)
      }
    }
  }

  private async rescheduleAlarm(nowS: number): Promise<void> {
    const m = await this.state.storage.list({ prefix: "intent:" });
    let earliest: number | null = null;
    for (const [, v] of m) {
      const it = v as any;
      const exp = (it.created_ts ?? 0) + (it.ttl_s ?? DEFAULT_TTL_S);
      if (earliest === null || exp < earliest) earliest = exp;
    }
    if (earliest !== null) await this.state.storage.setAlarm((earliest + 1) * 1000);
    else await this.state.storage.deleteAlarm();
  }

  async alarm(): Promise<void> {
    const nowS = Math.floor(Date.now() / 1000);
    await this.purgeExpired(nowS);
    await this.rescheduleAlarm(nowS);
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const nowS = Math.floor(Date.now() / 1000);
    await this.purgeExpired(nowS); // lazy purge — alarm과 2중

    if (url.pathname === "/enable" || url.pathname === "/disable") {
      await this.state.storage.put("enabled", url.pathname === "/enable");
      return okJson({ ok: true, enabled: url.pathname === "/enable" });
    }

    if (url.pathname === "/put") {
      // owner GO(2026-07-04): 채팅(ChatGPT/claude.ai) 상시 적재 위해 inbox enable 게이트 제거.
      // 경로키(/mcp2·/save2 <경로키>) 인증이 앞단 방어 — 그것 없으면 아무도 put 에 도달 못 함.
      // storage 에 남은 enabled=false 무시(HMAC enable 수단 부재 대응). 잠그려면 이 게이트 복원 후 재배포.
      const it = await request.json() as any;
      const cap = parseInt(request.headers.get("X-Inbox-Cap") ?? "", 10) || DEFAULT_INBOX_CAP;
      const m = await this.state.storage.list({ prefix: "intent:" });
      if (m.size >= cap && !m.has("intent:" + it.intent_id)) {
        return denyJson(503, "store_full"); // 전역 단일 카운트 — 진짜 상한
      }
      await this.state.storage.put("intent:" + it.intent_id, it);
      await this.rescheduleAlarm(nowS);
      return okJson({ ok: true, intent_id: it.intent_id });
    }

    if (url.pathname === "/drain") {
      // 트랜잭션 read+delete = 전역 atomic drain (조건 3)
      const out: unknown[] = [];
      await this.state.storage.transaction(async (txn) => {
        const m = await txn.list({ prefix: "intent:" });
        for (const [k, v] of m) {
          out.push(v);
          await txn.delete(k);
        }
      });
      await this.rescheduleAlarm(nowS);
      return okJson({ intents: out });
    }

    return denyJson(404, "not found");
  }
}

// ---------- worker 핸들러 ----------
// 라우트: POST /save2/<경로키>/{intent|pull|admin/enable|admin/disable}
// 순서: 경로키 404 → 메서드 405 → Origin 403 → 서명 401 → 모양 400 → DO

interface SaveV2Env {
  SAVE_PATH_TOKEN?: string;
  SAVE_SIGN_SECRET?: string;
  SAVE_INBOX_CAP?: string;
  SAVE_SIG_V2_ONLY?: string;       // "1"/"true" = 신형 서명 전용 (기본 false = 구형 수용)
  INBOX: DurableObjectNamespace;
}

function saveOriginOk(request: Request): boolean {
  return request.headers.get("Origin") === null;
}

export function makeSaveV2Handler() {
  return {
    async fetch(request: Request, env: SaveV2Env): Promise<Response> {
      const pathKey = (env.SAVE_PATH_TOKEN ?? "").trim();
      const signMaterial = (env.SAVE_SIGN_SECRET ?? "").trim();
      if (!pathKey || !signMaterial) return denyJson(503, "not configured"); // fail-closed
      const base = "/save2/" + pathKey;
      const url = new URL(request.url);
      const sub = url.pathname.startsWith(base + "/") ? url.pathname.slice(base.length + 1) : null;
      if (request.method !== "POST") return denyJson(405, "POST only");
      if (sub === null || !["intent", "pull", "admin/enable", "admin/disable"].includes(sub)) {
        return denyJson(404, "not found");
      }
      if (!saveOriginOk(request)) return denyJson(403, "origin not allowed");

      const nowS = Math.floor(Date.now() / 1000);
      const bodyText = await request.text();
      if (!(await verifySig(signMaterial, request, bodyText, nowS, sigV2Only(env.SAVE_SIG_V2_ONLY)))) {
        return denyJson(401, "bad signature"); // 무서명·창 밖·변조 일괄
      }

      const stub = env.INBOX.get(env.INBOX.idFromName("inbox")); // 단일 좌표

      if (sub === "admin/enable" || sub === "admin/disable") {
        return stub.fetch("https://do/" + (sub === "admin/enable" ? "enable" : "disable"));
      }
      if (sub === "pull") {
        return stub.fetch("https://do/drain", { method: "POST" });
      }

      // intent
      let body: any;
      try {
        body = JSON.parse(bodyText);
      } catch {
        return denyJson(400, "invalid json");
      }
      const reason = shapeReject(body);
      if (reason !== null) return denyJson(400, reason);
      const it = {
        schema_ver: SCHEMA_VER, intent_id: body.intent_id, text: body.text,
        indices: body.indices, confirm: body.confirm,
        created_ts: nowS, ttl_s: body.ttl_s ?? DEFAULT_TTL_S, source: "hosted",
      };
      const capHdr = (env.SAVE_INBOX_CAP ?? "").trim() || String(DEFAULT_INBOX_CAP);
      return stub.fetch("https://do/put", {
        method: "POST", body: JSON.stringify(it),
        headers: { "X-Inbox-Cap": capHdr },
      });
    },
  };
}

export const __testSaveV2 = { shapeReject, verifySig, SIG_WINDOW_S, DEFAULT_INBOX_CAP };
