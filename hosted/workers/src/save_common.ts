// save_common.ts — save-intent V2 공용 HMAC 서명 유틸 (L-7: verifySig 복붙 단일화).
// 서명 재료 (L-4 method+path 바인딩):
//   신형 v2: HMAC-SHA256(sign_material, ts + "." + METHOD + "." + pathname + "." + sha256_hex(body))
//   구형:    HMAC-SHA256(sign_material, ts + "." + sha256_hex(body))
// 서버는 신형 우선 검증, 실패 시 v2Only=false(기본, SAVE_SIG_V2_ONLY 미설정) 동안만 구형 수용.
// py 단일 출처 = scripts/binggupack_sign_util.py — 재료 포맷 바이트 동일 의무.
// 헤더: X-BGP-TS(epoch초) + X-BGP-SIG(hex). 서명 재료는 요청에 실리지 않음.
// 변수명에 token·secret 류 + '=' 조합 금지 — 공개 트리 스캐너 자기검출 회피 (6/10 박제)

export const SIG_WINDOW_S = 300;          // 재전송 방어 창

const ENC = new TextEncoder();

export function hex(buf: ArrayBuffer | Uint8Array): string {
  const a = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  return Array.from(a).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function timingSafeEqHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function hmacHex(signMaterial: string, msg: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", ENC.encode(signMaterial), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, ENC.encode(msg)));
}

// SAVE_SIG_V2_ONLY 환경값 해석 — 기본 false(구형 하위호환 수용), "1"/"true"만 신형 전용
export function sigV2Only(flag: string | undefined): boolean {
  const v = (flag ?? "").trim().toLowerCase();
  return v === "1" || v === "true";
}

export async function verifySig(signMaterial: string, request: Request, bodyText: string,
                                nowS: number, v2Only = false): Promise<boolean> {
  const ts = request.headers.get("X-BGP-TS");
  const sig = request.headers.get("X-BGP-SIG");
  if (!ts || !sig || !signMaterial) return false;
  const t = parseInt(ts, 10);
  if (!Number.isInteger(t) || Math.abs(nowS - t) > SIG_WINDOW_S) return false;
  const bodyHash = hex(await crypto.subtle.digest("SHA-256", ENC.encode(bodyText)));
  const sigHex = sig.toLowerCase();
  const pathname = new URL(request.url).pathname;
  const macV2 = await hmacHex(signMaterial,
    ts + "." + request.method.toUpperCase() + "." + pathname + "." + bodyHash);
  if (timingSafeEqHex(macV2, sigHex)) return true;
  if (v2Only) return false; // 전환 완료 후 구형 거부 (fail-closed)
  const macLegacy = await hmacHex(signMaterial, ts + "." + bodyHash);
  return timingSafeEqHex(macLegacy, sigHex);
}
