#!/usr/bin/env node
/**
 * stop-enforce-recall.js — 강제 회상 "검증" (Stop · SYNC · exit 2 물리 차단)
 *
 * 이번 turn 이 결정요청(pending 마커 존재)인데 recall/cloud_recall/why/opencrab 조회를
 * 한 번도 안 불렀으면 exit 2 로 turn 종료를 거부하고 "회상 후 재답변"을 강제한다.
 * exit 2 = 텍스트 잔소리가 아니라 물리 차단(Fable5 강제 실현성 실증 · 공식 문서:
 * Stop exit 2 = "Prevents Claude from stopping, continues the conversation").
 *
 * ★배선 필수: settings.json 에 반드시 SYNC(async 없음)로 등록. async 면 exit 2 차단력 0
 *   (현행 stop-self-eval·stop-pajae-top1 이 async 라 강제력 0 이었던 게 이번 사고의 배선 원인).
 *   wrapper.mjs 는 exit code 를 그대로 전달(stdio inherit + process.exit(r.status)) → 차단 보존.
 *
 * 안전(무한루프·세션 마비 방지):
 *   - 카운터: 이 turn 이미 1회 막았으면 무조건 통과(1회 한정 강제).
 *   - kill switch: ~/.claude/state/recall_enforce_disabled 존재 시 통과.
 *   - 모든 예외 흡수 → exit 0(hook 무방해). 판정 전부 결정적(transcript tool_use 스캔 · 의미판정 0).
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const STATE = path.join(os.homedir(), '.claude', 'state');
const PENDING = path.join(STATE, 'recall_enforce_pending.json');
const COUNTER = path.join(STATE, 'recall_enforce_count.txt');
const DISABLED = path.join(STATE, 'recall_enforce_disabled');
const LOG = path.join(STATE, 'recall_enforce.log');

// 회상으로 인정하는 도구(로컬 recall/why + 오픈크랩 조회). MCP 접두어 포함 부분매칭.
const RECALL_RE = /recall|opencrab_(search|query)|__why(\b|"|$)|preflight/i;

async function readStdin() {
  return new Promise((resolve) => {
    let d = '';
    process.stdin.setEncoding('utf-8');
    process.stdin.on('data', (c) => (d += c));
    process.stdin.on('end', () => resolve(d));
    setTimeout(() => resolve(d), 2000);
  });
}

function log(msg) {
  try { fs.appendFileSync(LOG, `${new Date().toISOString()} ${msg}\n`, 'utf-8'); } catch {}
}
function pass(reason) {
  log(`PASS ${reason}`);
  process.exit(0);
}

(async () => {
  try {
    // kill switch 최우선
    if (fs.existsSync(DISABLED)) pass('disabled');
    // 이번 turn 결정요청 아니면 통과(소음 0)
    if (!fs.existsSync(PENDING)) pass('not_decision');
    // 카운터: 이미 1회 막았으면 통과(무한루프 방지)
    let cnt = 0;
    try { cnt = parseInt(fs.readFileSync(COUNTER, 'utf-8').trim(), 10) || 0; } catch {}
    if (cnt >= 1) {
      try { fs.unlinkSync(PENDING); } catch {}
      pass('already_enforced');
    }

    const raw = await readStdin();
    let tp = '';
    try { tp = JSON.parse(raw).transcript_path || ''; } catch { pass('no_transcript'); }
    if (!tp || !fs.existsSync(tp)) pass('no_transcript_file');

    const lines = fs.readFileSync(tp, 'utf-8').split('\n').filter((l) => l.trim());
    // 이번 turn = 마지막 user message 이후 assistant 구간
    let start = -1;
    for (let i = lines.length - 1; i >= 0; i--) {
      try {
        const o = JSON.parse(lines[i]);
        if (o.type === 'user' || o.role === 'user') { start = i + 1; break; }
      } catch {}
    }
    if (start < 0) start = Math.max(0, lines.length - 60);

    let recalled = false;
    for (let i = start; i < lines.length && !recalled; i++) {
      try {
        const o = JSON.parse(lines[i]);
        const content = (o.message || o).content;
        if (Array.isArray(content)) {
          for (const c of content) {
            if (c.type === 'tool_use' && c.name && RECALL_RE.test(c.name)) { recalled = true; break; }
          }
        }
      } catch {}
    }

    if (recalled) {
      try { fs.unlinkSync(PENDING); } catch {}
      pass('recalled');
    }

    // 결정요청인데 회상 0 → exit 2 로 재답변 강제(1회 한정)
    try { fs.writeFileSync(COUNTER, '1', 'utf-8'); } catch {}
    log('BLOCK no_recall');
    process.stderr.write(
      '[강제 회상 미이행] 결정/검토 답변인데 개인 판단 회상을 안 했습니다. ' +
      'recall(로컬 장부) 또는 cloud_recall(오픈크랩 개인 팩)로 사장님 판단·최근 실수·프로젝트 ' +
      '최신 상태를 먼저 회상한 뒤 답변을 보완하세요. ' +
      '(1회 한정 강제 · 끄기: ~/.claude/state/recall_enforce_disabled 파일 생성)\n'
    );
    process.exit(2);
  } catch {
    process.exit(0); // 모든 예외 흡수 — 어떤 경우에도 세션 방해 0
  }
})();
