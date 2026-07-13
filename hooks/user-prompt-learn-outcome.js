#!/usr/bin/env node
/**
 * user-prompt-learn-outcome.js — 빙구팩 학습 채널 "결과 기록" (UserPromptSubmit · SYNC)
 *
 * owner 자연 발화("맞네"/"정확해"/"아니야"/"틀렸어" 등 긍정·부정 피드백)를 감지해,
 * 직전 AI 답변 발췌와 함께 교환(exchange) 후보로 append-only 큐
 * (~/.claude/state/learn_outcome_queue.jsonl)에 남긴다.
 * ★축(2026-07-13): 극성 = 결과가 아니라 입장(stance: refutes/accepts). 누가 맞았는지는
 *   소비 시점(learn-consume)에 사람이 확정 — "사용자 대화 → AI 답변 → 확인" 순서.
 *
 * ★설계 근거(위조 불가 앵커):
 *   - UserPromptSubmit = 사람만 발생하는 이벤트(AI 는 이 이벤트를 못 거침 → 위조 불가).
 *     MCP confirm(AI 가 문자열 조립 가능)과 달리 사람 발화가 hit/miss 의 앵커가 된다.
 *   - use_count 0·hit_events 1건으로 학습이 안 돌던 근본 원인 = "사람이 크랭크를 안 돌려서".
 *     마찰을 없애 자연 발화만으로 표본이 쌓이게 하는 게 목적(마감은 owner SAVE/스케줄러가).
 *
 * ★안전 불변(헌법 §3 정합):
 *   - 운영 ledger(sqlite) 직접 write 0. 여기선 append-only 큐만 남긴다(영구 적재=사람 SAVE).
 *     큐는 나중에 owner 승인 경로/스케줄러(hit_recording.mark_hit/miss actor=human)가 소비.
 *   - stdout 침묵 · 항상 exit 0 · 예외 전부 흡수(세션 무방해 — 감지·기록만, 차단 0).
 *   - 과대포착 금지: 명확한 피드백 발화만(짧은 발화 or 앞머리 매칭). 긴 문장 중 우연 매칭 방지.
 *   - 하드코딩 owner 경로/UUID 0 — os.homedir()/BINGGU_HOME 상대 경로만(신규 사용자 대응).
 *   - 큐 상한(폭주 방지) · PII 최소(query 추정은 짧게 절단).
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

// ── 경로: BINGGU_HOME 우선, 없으면 ~/.claude/state (신규 사용자 대응·하드코딩 0) ─────────
function stateDir() {
  const bh = process.env.BINGGU_HOME;
  if (bh && bh.trim()) return path.join(bh.trim(), 'state');
  return path.join(os.homedir(), '.claude', 'state');
}
const STATE = stateDir();
const QUEUE = path.join(STATE, 'learn_outcome_queue.jsonl');
const LOG = path.join(STATE, 'learn_outcome.log');
const DISABLED = path.join(STATE, 'learn_outcome_disabled');  // kill switch(존재 시 즉시 무동작·짝 hook 패턴)

const QUEUE_MAX = 500;     // 큐 라인 상한(폭주 방지 — 초과 시 무기록)
const QUERY_MAX = 120;     // query 추정 절단(PII 최소)
const SCAN_TAIL = 400;     // transcript 꼬리 스캔 상한(성능)
const AI_ANSWER_MAX = 200; // 직전 AI 답변 발췌 절단(교환 축 근거·PII 최소)

// 회상으로 인정하는 도구(로컬 recall/why + 오픈크랩 cloud_recall/search/query). 부분매칭.
const RECALL_RE = /recall|cloud_recall|opencrab_(search|query)|__why(\b|"|$)/i;

// ── 피드백 정규식(과대포착 금지) ─────────────────────────────────────────────
// 긍정: "맞네/맞아/정확해/그래 맞아/딩동/오 맞다/바로 그거/굿" 등.
// 부정: "아니야/아닌데/틀렸어/그거 아냐/아니 그게 아니라" 등.
// 둘 다 어미·감탄 위주로 좁게 잡고, 별도로 '발화가 짧거나 앞머리 매칭'일 때만 채택(우연 매칭 차단).
// ★한글에는 \b(word boundary)를 쓰지 않는다 — 한글 음절은 \w 밖이라 "맞네"에서 경계 매칭 실패.
//   우연 매칭은 아래 SHORT_LEN/HEAD_WINDOW 게이트가 방어(어미까지 그룹으로 좁게).
const POS_RE = /(정확해|정확하(네|다)|맞(네|아|았|다|어|지)|맞았(어|다|네)|딩동|바로\s*그거|그래\s*맞|오\s*맞|좋아\s*맞|훌륭|잘\s*했|잘했|맞음|맞다|굳(?=[\s!.,]|$)|굿)/;
// ★A 재설계(2026-07-10): owner 실제 지적/정정 스타일 확장 — "산으로 간다"·"그대로다"·"다시 봐"·
//   "안 고쳐졌"·"왜 안" 등. 기존은 "아니야/틀렸어" 명시 리액션만 잡아 owner 실사용 지적 0건 매칭이었다.
//   SHORT_LEN/HEAD_WINDOW 게이트가 긴 문장 중간 우연 매칭을 방어(과대포착은 큐→owner 승인 소비가 최종 차단).
const NEG_RE = /(아니(야|다|네|지|에요|예요|었어|었다|긴)|아닌(데|가|것)|틀렸|틀린|그게\s*아니|그거\s*아니|잘못(됐|짚|알)|아냐|노노|반대야|산으로|다시\s*(봐|보자|확인)|안\s*(고쳐|됐|돼|바뀌)|그대로(다|네|잖|인데)|왜\s*안)/;

// 우연 매칭 방지: 발화가 짧거나(대략 피드백성) 앞머리에서 매칭돼야 채택.
// 긴 서술문 중간에 "맞아" 가 우연히 섞인 경우(예: "일정 맞춰서 진행해줘")를 배제.
const SHORT_LEN = 24;      // 이 길이 이하면 피드백 발화로 간주(짧은 리액션)
const HEAD_WINDOW = 12;    // 앞머리 N자 내 매칭이면 채택

function log(msg) {
  try { fs.appendFileSync(LOG, `${new Date().toISOString()} ${msg}\n`, 'utf-8'); } catch {}
}

async function readStdin() {
  return new Promise((resolve) => {
    let d = '';
    process.stdin.setEncoding('utf-8');
    process.stdin.on('data', (c) => (d += c));
    process.stdin.on('end', () => resolve(d));
    setTimeout(() => resolve(d), 1500);
  });
}

// 피드백 발화 여부 판정 — outcome('hit'|'miss') 또는 null.
// 규칙: (POS|NEG) 매칭 AND (짧은 발화 OR 앞머리 매칭). 부정 우선(더 명확한 신호).
function classifyFeedback(prompt) {
  const p = (prompt || '').trim();
  if (!p) return null;
  // ★질문형 배제 — 반문·확인 질문은 피드백 아님. 음절 기반(자모 리터럴 ㄴ가/ㄹ까 는
  //   조합형 한글에 절대 매칭 안 되던 死코드였음 · Fable5). 물음표 or 의문 어미면 제외.
  if (/[?？]\s*$/.test(p)) return null;
  if (/(냐$|나요$|는가$|은가$|인가$|아닌가$|을까$|는지$|되는거\s*아|하면\s*되|어때$|어떨까$)/.test(p)) return null;
  // ★극성 반전 방어(Fable5): "안 맞", "못 맞", "맞지/맞는 않" = 부정(miss) — POS 매칭보다 우선.
  const NEG_POS = /(안\s*맞|못\s*맞|맞(지|는)\s*않)/;
  const negated = NEG_POS.test(p);
  const neg = p.match(NEG_RE);
  const pos = negated ? null : p.match(POS_RE);   // 부정부사 있으면 POS 채택 안 함
  if (!neg && !negated && !pos) return null;
  const isMiss = !!(neg || negated);
  const m = neg || (negated ? p.match(NEG_POS) : pos);
  const idx = (m && m.index) || 0;
  const short = p.length <= SHORT_LEN;
  const head = idx <= HEAD_WINDOW;
  if (!short && !head) return null;   // 긴 문장 중간 우연 매칭 배제
  return isMiss ? 'miss' : 'hit';
}

// transcript 에서 "직전 assistant turn"의 recall tool_use 추출.
//   경계: 마지막 '사람 user 발화'(toolUseResult 없음 + text/string) 이후 ~ 끝.
//   UserPromptSubmit 시점엔 이번 발화가 아직 transcript 에 없을 수 있으므로,
//   기록된 마지막 사람 발화(=직전 turn 시작) 이후 구간의 recall 을 본다.
// 반환: {found:bool, queries:[...]} — queries 는 recall input.query 목록(짧게 절단).
function extractText(c) {
  if (typeof c === 'string') return c;
  if (Array.isArray(c)) {
    return c.map((x) => (typeof x === 'string' ? x : (x && x.type === 'text' ? (x.text || '') : ''))).join('');
  }
  return '';
}

function scanRecall(transcriptPath, currentPrompt) {
  const out = { found: false, queries: [], aiAnswer: '' };
  if (!transcriptPath || !fs.existsSync(transcriptPath)) return out;
  let lines;
  try {
    lines = fs.readFileSync(transcriptPath, 'utf-8').split('\n').filter((l) => l.trim());
  } catch { return out; }
  if (!lines.length) return out;

  const scanFrom = Math.max(0, lines.length - SCAN_TAIL);
  // 마지막 '사람 user 발화' 인덱스 탐색(tool_result 는 role=user 지만 toolUseResult 키로 구분).
  // ★배선 강건성(Fable5): UserPromptSubmit 시점 이번 발화가 이미 transcript 끝에 기록돼 있으면
  //   그걸 skip 하고 '직전 turn' 의 human 을 경계로(안 그러면 스캔 0→영구 무기록 침묵사).
  let lastHuman = -1;
  let skippedCurrent = false;
  for (let i = lines.length - 1; i >= scanFrom; i--) {
    let o;
    try { o = JSON.parse(lines[i]); } catch { continue; }
    const role = o.type || o.role;
    if (role !== 'user') continue;
    if (o.toolUseResult !== undefined) continue;   // 도구 결과 turn — 사람 발화 아님
    const c = (o.message || o).content;
    const isText = typeof c === 'string'
      || (Array.isArray(c) && c.some((x) => x && (x.type === 'text' || typeof x === 'string')));
    const isToolResult = Array.isArray(c) && c.some((x) => x && x.type === 'tool_result');
    if (isText && !isToolResult) {
      if (!skippedCurrent && currentPrompt && extractText(c).trim() === String(currentPrompt).trim()) {
        skippedCurrent = true;   // 이번 발화 skip → 그 이전 human 을 직전 turn 경계로
        continue;
      }
      lastHuman = i;
      break;
    }
  }
  const start = lastHuman >= 0 ? lastHuman + 1 : scanFrom;

  for (let i = start; i < lines.length; i++) {
    let o;
    try { o = JSON.parse(lines[i]); } catch { continue; }
    const role = o.type || o.role;
    const c = (o.message || o).content;
    if (!Array.isArray(c)) continue;
    // ★교환 축(2026-07-13 owner): 사용자가 반응한 대상 = 직전 turn 의 마지막 assistant text.
    //   창 안의 assistant text 를 갱신 유지 → 루프 종료 시 마지막 것이 남는다(발췌 절단).
    if (role === 'assistant') {
      const t = c
        .map((x) => (x && x.type === 'text' ? (x.text || '') : ''))
        .join('').trim();
      if (t) out.aiAnswer = t.slice(0, AI_ANSWER_MAX);
    }
    for (const it of c) {
      if (it && it.type === 'tool_use' && it.name && RECALL_RE.test(it.name)) {
        out.found = true;
        const q = it.input && (it.input.query || it.input.q || it.input.text);
        if (q && typeof q === 'string') {
          out.queries.push(q.slice(0, QUERY_MAX));
        }
      }
    }
  }
  // 중복 query 제거
  out.queries = Array.from(new Set(out.queries)).slice(0, 5);
  return out;
}

function queueLineCount() {
  try {
    return fs.readFileSync(QUEUE, 'utf-8').split('\n').filter((l) => l.trim()).length;
  } catch { return 0; }
}

function main(rawData) {
  let data;
  try { data = JSON.parse(rawData); } catch { return; }
  if ((data.hook_event_name || '') !== 'UserPromptSubmit') return;
  try { if (fs.existsSync(DISABLED)) return; } catch {}   // kill switch(짝 hook 패턴 · 즉시 무동작)

  const prompt = data.prompt || '';
  const outcome = classifyFeedback(prompt);
  if (!outcome) return;   // 피드백 발화 아님 → 무기록(과대포착 방지)

  // transcript_path: stdin JSON 에 오면 사용(없으면 무기록=안전 실패, 표본만 덜 쌓임).
  let tp = data.transcript_path || '';
  const scan = scanRecall(tp, prompt);
  // ★A 재설계(2026-07-10): recall 커플링 제거. owner 지적/정정 대부분은 recall 무관(AI 작업
  //   피드백)이라 scan.found 필수 게이트가 owner 실사용 피드백을 구조적으로 0건 만들었다
  //   (hit_events n=1 근본원인). recall 있으면 query 연결, 없으면 recall_linked=false 로 큐에 남긴다.
  //   자동 확정 0 유지 — 큐는 owner 승인 소비(learn-consume)/세션마무리 SAVE 로만 hit_events 적재.
  const recallLinked = scan.found;

  fs.mkdirSync(STATE, { recursive: true });
  if (queueLineCount() >= QUEUE_MAX) {
    log(`SKIP queue_full max=${QUEUE_MAX}`);
    return;
  }

  // ★교환 축(2026-07-13 owner "사용자 대화 - ai답변 - 맞는지 틀리는지 확인"):
  //   발화 극성은 결과(hit/miss)가 아니라 입장(stance)이다 — 부정어("아니지")=AI 답변 반박(refutes),
  //   긍정어("맞다")=AI 답변 인정(accepts). 누가 맞았는지(확인)는 소비 시점에 사람이 확정한다.
  //   outcome 필드는 구독자 호환용 legacy alias 로만 유지(신규 소비자는 stance 사용).
  const stance = outcome === 'miss' ? 'refutes' : 'accepts';
  const entry = {
    ts: new Date().toISOString(),
    outcome,                                   // legacy alias('hit'|'miss') — 신규 축은 stance
    stance,                                    // 'refutes'(반박) | 'accepts'(인정) — 교환 축
    ai_answer: scan.aiAnswer || '',            // 사용자가 반응한 직전 AI 답변 발췌(절단)
    recall_linked: recallLinked,               // ★recall 연결 여부(true=회상결과 피드백 / false=일반 작업 지적)
    queries: scan.queries,                     // recall input.query 추정(recall_linked 시만 채워짐·중복 제거)
    evidence: {
      feedback: prompt.slice(0, QUERY_MAX),    // 사람 발화 근거(PII 최소 절단)
      recall_count: scan.queries.length,
      transcript: tp ? path.basename(tp) : '', // 파일명만(경로 PII 회피)
    },
    consumed: false,                           // 스케줄러/owner 승인 경로 소비 마커(미소비)
  };
  try {
    fs.appendFileSync(QUEUE, JSON.stringify(entry) + '\n', 'utf-8');
    log(`QUEUED outcome=${outcome} linked=${recallLinked} queries=${scan.queries.length}`);
  } catch (e) {
    log(`ERR append ${e && e.message}`);
  }
}

(async () => {
  try {
    const raw = await readStdin();
    if (raw && raw.trim()) main(raw);
  } catch { /* 예외 전부 흡수 */ }
  process.exit(0);   // 항상 통과 · stdout 침묵 — 감지·기록만, 차단 0
})();
