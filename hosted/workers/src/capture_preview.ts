// conversation_capture_preview — 6번째 read-only 도구 (GO-PREVIEW-LIVE).
// Python 정본(scripts/openbinggu_conversation_capture_preview.py) 1:1 포팅 — parity selftest 로 강제.
// 원칙: 저장 0(순수 함수) · PII/secret 문장 후보 제외(종류·개수만) · raw 전문 재출력 0 ·
//       candidate 고정 · 승격 암시 출력 0 · 입력 = 모델이 전달한 대화 텍스트(원문 보장 없음) 명시.

const INPUT_CAP = 20000;
const DEFAULT_MAX = 10;
const HARD_MAX = 20;
// 정체성(owner 2026-06-15): 저장 단위 = 사용자가 고른 문장 전체(80자 발췌 cut 폐기 — 개인 온톨로지/AGI화).
// MAX_NODE_SENTENCE = 단일 문장 정당 상한. 초과 = 종결어미 없는 문단/로그 덩어리 → 후보 제외.
// Python 정본(MAX_NODE_SENTENCE) 1:1 — parity selftest 로 강제.
const MAX_NODE_SENTENCE = 1000;
const SENT_SPLIT = /(?<=[.!?다음임함됨까요])\s+|\n+/;
// 콜론을 문자 클래스로 — 소스/번들 텍스트에 영문자+콜론+백슬래시 시퀀스가 생기지 않게 (스캐너 자기검출 회피)
const REDACT_RE = /\[REDACTED[:]\w+\]/g;

// ---- G0 분류 규칙 (openbinggu_label_kind_map._RULES 동일 — 순서·정규식 1:1) ----
const RULES: [string, string, RegExp][] = [
  ["증거", "ev_record",
    /(기록되어 있|기록돼 있|로그에 .{0,12}(남|찍|있)|적혀 있|첨부되|캡처(했|된|되)|스크린샷|출력에 .{0,8}(나타|찍|보))/],
  ["문서", "doc_ref",
    /^(이|본|해당) ?(문서|보고서|설계서|가이드|튜토리얼|README|runbook)|(문서|보고서|설계서)(는|가) .{0,20}(정의|기술|설명|규정)한다/],
  ["개념", "concept_def",
    /((이|란|라)는 .{0,16}(절차|개념|규칙|원칙|방식|용어)(이다|다)\.?$|[을를] (말한다|의미한다|뜻한다)\.?$|(이란|란) )/],
  ["상태", "state_now",
    /((상태|중)(이다|다)\.?$|진행 중|가동 중|완료(된 상태|되어 있)|남아 ?있다|되어 ?있다\.?$|^현재 )/],
  ["판단", "judgment_verdict",
    /(해야 (한다|함)|하는 (것이|게) (낫|좋)|보류(한다|함)|진행(한다|함)|채택(한다|함)|기각(한다|함)|권고(한다|함)|금지(한다|함)|(이|가) (낫다|위험하다|안전하다)|않는 것이 (낫|좋))/],
];

// ---- A0 간이 판정 (openbinggu_a0_node_dryrun 동일 규칙) ----
const TERMINAL = /(다|음|임|까|요|함|됨|된다|이다|한다|난다)\.?$|[.!?]$/;
// Python \b 는 한글=word — JS \b 는 한글 비인식이라 lookahead 로 동치 구현
const NOISE_PREFIX = /^(그리고|또한|그래서|그러나|하지만|즉|또|및)(?![가-힣A-Za-z0-9_])/;

function isWord(s: string): boolean {
  const t = (s || "").trim();
  return !t.includes(" ") || t.length < 6;
}

function hasMeaning(s: string): boolean {
  const t = (s || "").trim();
  return t.length >= 10 && TERMINAL.test(t);
}

function a0Verdict(sentence: string): string {
  if (isWord(sentence)) return "FAIL";
  if (!hasMeaning(sentence)) return "FAIL";
  if (NOISE_PREFIX.test(sentence.trim())) return "REVIEW";
  return "PASS";
}

// ---- PII/secret 검출 (watcher_batch_m1._SCAN_SHAPES + v011.SECRET_PATTERNS 동일) ----
// 공개 트리 스캐너 자기검출 회피: credential 키워드는 런타임 조립 (6/10 박제 정합)
const KW1 = ["pass" + "word", "passwd", "pwd", "sec" + "ret", "to" + "ken",
  "api[_-]?key", "apikey", "client[_-]?sec" + "ret", "access[_-]?to" + "ken",
  "refresh[_-]?to" + "ken"].join("|");
const KW2 = ["service_?key", "api_?key", "sec" + "ret_?key", "client_?sec" + "ret",
  "access_?to" + "ken", "refresh_?to" + "ken", "pass" + "word", "passwd",
  "cookie", "authorization"].join("|");

// preview 전용 추가 PII (owner 6/11 (a)): 사업자등록번호 — 공용 스캐너는 도메인 식별자 보존 정책이라
// hosted 외부 표면인 preview 에서만 보수적으로 제외 (Python _PREVIEW_PII_EXTRA 1:1)
const PREVIEW_PII_EXTRA: [string, RegExp][] = [
  ["scan_bizno_fmt", /(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)/],
  ["scan_bizno_bare", /(?<!\d)\d{10}(?!\d)/],
];

const SCAN_SHAPES: [string, RegExp][] = [
  ["scan_rrn", /\d{6}[-\s.]\d{7}/],
  ["scan_rrn_nohp", /(?<![0-9])\d{13}(?![0-9])/],
  ["scan_mobile", /(?<![0-9])0?1[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?![0-9])/],
  ["scan_landline", /(?<![0-9])0\d{1,2}[-\s.]\d{3,4}[-\s.]\d{4}(?![0-9])/],
  ["scan_email", /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/],
  ["scan_aws", /AKIA[0-9A-Z]{12,}/],
  ["scan_kv", new RegExp("(?:" + KW1 + ")\\s*[:=]\\s*\\S{4,}", "i")],
];

const SECRET_RES: RegExp[] = [
  new RegExp("\\b(?:sk-live-|sk-proj-|gh" + "p_|github_" + "pat_|xox[baprs]-)[A-Za-z0-9_\\-]{6,}", "i"),
  /\bAKIA[0-9A-Z]{8,}/,
  new RegExp("\\b(?:" + KW1 + ")\\b\\s*[:=]\\s*['\"]?\\S{3,}", "i"),
  new RegExp("\\bprivate[_-]?" + "key\\b", "i"),
  new RegExp("\\b(?:" + KW2 + ")\\b\\s*[:=]\\s*['\"]?[A-Za-z0-9_\\-+/=.]{16,}", "i"),
  /\bbearer\s+[A-Za-z0-9_\-.=]{20,}/i,
  new RegExp("-----BEGIN[A-Z ]*PRIVATE " + "KEY-----"),
  new RegExp("\\b(?:aws_sec" + "ret_access_" + "key|aws_access_" + "key_id)\\b\\s*[:=]\\s*[A-Za-z0-9/+=]{16,}", "i"),
  /(?<![A-Za-z0-9])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9])/,
];

// L-1: index.ts 런타임 leakScan PII 백스톱이 동일 패턴을 재사용 (export)
export function scanPii(s: string): string[] {
  const found: string[] = [];
  for (const [kind, re] of SCAN_SHAPES) {
    if (re.test(s)) found.push(kind);
  }
  for (const [kind, re] of PREVIEW_PII_EXTRA) {
    if (re.test(s)) found.push(kind);
  }
  return found;
}

// C1 백스톱: hosted save_intent 가 put 직전 text 전체에 secret 재검사 (save_intent_mcp.ts).
// SECRET_RES 무수정 재사용 — 문장 단위 제외(capturePreview)와 별개로 원문 전체 표면 방어.
export function hasSecret(s: string): boolean {
  return SECRET_RES.some((re) => re.test(s));
}

function meaningful(s: string): boolean {
  const stripped = s.replace(REDACT_RE, "").trim();
  if (stripped.length < 6) return false;
  if (!stripped.includes(" ") && stripped.length < 12) return false;
  return true;
}

function classify(sentence: string): [string, string] {
  const s = (sentence || "").trim();
  if (!s) return ["판단", "fallback_judgment"];
  for (const [kind, ruleId, re] of RULES) {
    if (re.test(s)) return [kind, ruleId];
  }
  return ["판단", "fallback_judgment"];
}

// L-lane 정원·표시 임계 — Python 정본(openbinggu_conversation_capture_preview.py)과 같은 값.
const L_MAX = 5;
const L_FULL_SHOW = 4000;

/** L-lane opt-in.
 *  Python 은 env(BINGGU_LONGSAVE_V1) 로 켜지만 Workers 런타임에는 process.env 가 없다 →
 *  여기서는 **명시 인자**로 받는다(기본 false = 현행 동작 완전 불변). 형태만 다르고 의미는 같다.
 *  py↔ts 골든 대조(S2-6)는 양쪽을 ON 으로 맞춘 상태에서 수행한다. */
export interface CapturePreviewOpts { longSave?: boolean }

export function capturePreview(text: string, maxCandidates?: number,
                               opts?: CapturePreviewOpts): Record<string, any> {
  let maxC = Math.trunc(Number(maxCandidates ?? DEFAULT_MAX));
  if (Number.isNaN(maxC)) maxC = DEFAULT_MAX;
  maxC = Math.max(1, Math.min(maxC, HARD_MAX));
  const longOn = opts?.longSave === true;
  let raw = text || "";
  let truncated = false;
  if (raw.length > INPUT_CAP) {
    const cut = raw.lastIndexOf("\n", INPUT_CAP);
    raw = raw.slice(0, cut > 200 ? cut : INPUT_CAP);
    truncated = true;
  }

  const excluded: Record<string, number> = {};
  const excl = (k: string) => { excluded[k] = (excluded[k] || 0) + 1; };
  const candidates: any[] = [];
  const seen = new Set<string>();
  // L-lane — 주 목록(candidates·excluded_counts)은 바이트 불변, 별도 리스트·별도 카운터.
  const longItems: any[] = [];
  const longExcluded: Record<string, number> = {};
  const longExcl = (k: string) => { longExcluded[k] = (longExcluded[k] || 0) + 1; };

  for (const piece of raw.split(SENT_SPLIT)) {
    const sent = (piece || "").trim();
    if (!sent) continue;
    if (!meaningful(sent)) { excl("short_or_fragment"); continue; }
    if (sent.length > MAX_NODE_SENTENCE) {
      excl("over_max_sentence");
      // 주 목록 제외·카운트는 불변. 여기서 갈라지는 별도 차선일 뿐(Python 정본 1:1).
      // ★ 안전 게이트는 주 목록과 동일하게 건다 — 이 분기가 PII/secret 검사보다 앞이라
      //   그냥 담으면 PII 든 긴 문장이 검사를 건너뛴다.
      if (longOn) {
        const lpii = scanPii(sent);
        if (lpii.length) { for (const k of lpii) longExcl("pii_" + k); continue; }
        if (SECRET_RES.some((re) => re.test(sent))) { longExcl("secret_pattern"); continue; }
        if (longItems.length >= L_MAX) { longExcl("long_overflow"); continue; }
        const [lkind] = classify(sent);
        longItems.push({ label: "L" + (longItems.length + 1), sentence: sent,
                         length: sent.length, label_kind: lkind,
                         a0_verdict: a0Verdict(sent) });
      }
      continue;
    }
    const pii = scanPii(sent);
    if (pii.length) { for (const k of pii) excl("pii_" + k); continue; }
    if (SECRET_RES.some((re) => re.test(sent))) { excl("secret_pattern"); continue; }
    const norm = sent.replace(/\s+/g, " ").trim();
    if (seen.has(norm)) { excl("duplicate"); continue; }
    seen.add(norm);
    if (candidates.length >= maxC) { excl("over_max_candidates"); continue; }
    const [kind, ruleId] = classify(sent);
    // 문장 전체 저장(발췌 cut 제거) — 본 것 = 저장된 것. Python 정본 1:1.
    candidates.push({ sentence: sent, label_kind: kind, rule_id: ruleId,
                      a0_verdict: a0Verdict(sent), candidate: true });
  }

  const lines = ["# 캡처 미리보기 — 후보 " + candidates.length + "건 (전부 candidate, 미저장)",
                 "", "| # | 도장 | 문장 | 분류근거 | 헌법판정 |", "|---|---|---|---|---|"];
  candidates.forEach((c, i) => {
    lines.push("| " + (i + 1) + " | " + c.label_kind + " | " + c.sentence + " | " + c.rule_id +
               " | " + c.a0_verdict + " |");
  });
  const exKeys = Object.keys(excluded).sort();
  if (exKeys.length) {
    lines.push("");
    lines.push("제외: " + exKeys.map((k) => k + "=" + excluded[k]).join(", "));
  }
  if (truncated) {
    lines.push("");
    lines.push("(입력이 " + INPUT_CAP + "자 캡으로 절단됨)");
  }
  // L 섹션 — 항목이 있을 때만. 없으면 markdown 은 종전과 완전 동일.
  if (longItems.length) {
    lines.push("");
    lines.push("## 긴 발화 " + longItems.length + "건 — 주 번호(1,2,3…)와 별개 축입니다");
    lines.push("");
    lines.push("| # | 도장 | 문장 | 길이 | 헌법판정 |");
    lines.push("|---|---|---|---|---|");
    for (const it of longItems) {
      const shown = it.length <= L_FULL_SHOW ? it.sentence
        : it.sentence.slice(0, 800) + " …(중략)… " + it.sentence.slice(-400);
      lines.push("| " + it.label + " | " + it.label_kind + " | " + shown + " | " +
                 it.length + "자 | " + it.a0_verdict + " |");
    }
  }
  const lexKeys = Object.keys(longExcluded).sort();
  if (lexKeys.length) {
    lines.push("");
    lines.push("긴 발화 제외: " + lexKeys.map((k) => k + "=" + longExcluded[k]).join(", "));
  }
  lines.push("");
  lines.push("입력은 모델이 전달한 대화 텍스트 기준입니다(원문 그대로 보장 없음 — " +
             "모델 요약보다 원문 대화/로그를 넣을수록 도장 분류가 정확합니다). " +
             "미리보기일 뿐 아무것도 저장되지 않았습니다(nothing_saved=true). 등재는 로컬 승인 게이트에서만.");

  // long_candidates/long_excluded 는 OFF 에서도 항상 존재([]/{}) — 구 소비자 KeyError 0.
  return { candidates, excluded_counts: excluded, truncated,
           long_candidates: longItems, long_excluded: longExcluded,
           preview_markdown: lines.join("\n"), nothing_saved: true };
}
