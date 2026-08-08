#!/usr/bin/env node
/**
 * stop-enforce-recall-stamp.js — 회상 **도장** 강제 (Stop · SYNC · exit 2 물리 차단)
 *
 * ★ 왜 만들었나 (2026-08-08 사장님 지적)
 *   회상 효용 도장은 **AI 가 쓰는 순간 자동 기입**하는 것이 정본이다(CLAUDE.md §C-11-1 ·
 *   2026-07-27 사장님 명시 지시 — "그 회상이 도움됐는지는 쓰는 순간의 AI 가 가장 잘 알고,
 *   세션 끝 목록만 보는 owner 보다 판정이 정확하다"). 사장님이 헷갈릴 수 있어 **일부러 만든 예외**다.
 *
 *   그런데 실측(2026-08-08 · recall_trace.sqlite): 회상 1,454회 중 판정된 회차 **109회(7%)**.
 *   93% 가 채점 없이 지나갔다. 도장이 랭킹(use_count)으로 이어지므로, 안 찍으면 **어떤 회상이
 *   쓸모 있었는지 시스템이 영영 학습하지 못한다**(장부 [G-06] 과 같은 뿌리).
 *   같은 날 세션이 전형이었다 — 회상 두 번을 판단에 쓰고 도장 0, 사장님이 물으신 뒤에야 찍었다.
 *
 *   규율로는 안 된다는 게 7% 라는 숫자로 증명됐다. 그래서 stop-enforce-recall 과 **같은 방식**으로
 *   막는다: 이번 turn 에 회상을 인출했는데 trace_stamp 를 한 번도 안 불렀으면 exit 2.
 *
 * 안전(무한루프·세션 마비 방지) — 회상 강제 hook 과 동일 규약:
 *   - 회상 자체를 안 불렀으면 통과(소음 0). 도장은 인출한 턴에만 의무다.
 *   - 카운터: 이 turn 이미 1회 막았으면 무조건 통과(1회 한정 강제).
 *   - kill switch: ~/.claude/state/recall_stamp_enforce_disabled 존재 시 통과.
 *   - 모든 예외 흡수 → exit 0. 판정은 transcript tool_use 스캔뿐(의미판정 0).
 *
 * ★배선: settings.json 에 반드시 SYNC 로 등록. async 면 exit 2 차단력이 0 이다.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const STATE = path.join(os.homedir(), '.claude', 'state');
const COUNTER = path.join(STATE, 'recall_stamp_enforce_count.txt');
const DISABLED = path.join(STATE, 'recall_stamp_enforce_disabled');
const LOG = path.join(STATE, 'recall_stamp_enforce.log');

/** 도장 의무를 지우는 인출 — 사람이 보는 목록을 만드는 recall/why/cloud_recall. */
const RECALL_RE = /__(recall|cloud_recall|why)(\b|"|$)/i;
/** 도장 — 이걸 부르면 의무 해제. */
const STAMP_RE = /trace_stamp|mark_hit|mark_miss/i;

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
    if (fs.existsSync(DISABLED)) pass('disabled');

    let cnt = 0;
    try { cnt = parseInt(fs.readFileSync(COUNTER, 'utf-8').trim(), 10) || 0; } catch {}
    if (cnt >= 1) { try { fs.unlinkSync(COUNTER); } catch {} pass('already_enforced'); }

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
    let stamped = false;
    for (let i = start; i < lines.length; i++) {
      try {
        const o = JSON.parse(lines[i]);
        const content = (o.message || o).content;
        if (!Array.isArray(content)) continue;
        for (const c of content) {
          if (c.type !== 'tool_use' || !c.name) continue;
          if (STAMP_RE.test(c.name)) stamped = true;
          else if (RECALL_RE.test(c.name)) recalled = true;
        }
      } catch {}
    }

    if (!recalled) pass('no_recall_this_turn');   // 인출 안 했으면 도장 의무 없음
    if (stamped) pass('stamped');

    try { fs.writeFileSync(COUNTER, '1', 'utf-8'); } catch {}
    log('BLOCK recall_without_stamp');
    process.stderr.write(
      '[회상 도장 누락] 이번 턴에 회상을 인출했는데 효용 도장을 안 찍었습니다. ' +
      '도장은 사장님이 아니라 **쓰는 순간의 네가** 찍는 것이 정본입니다(CLAUDE.md §C-11-1 · ' +
      '실측 2026-08-08: 회상 1,454회 중 판정 109회 = 7%). ' +
      'trace_stamp(trace_id + i + used/ignored/corrected)로 인출분을 전부 판정한 뒤 답변을 마치세요. ' +
      '(1회 한정 강제 · 끄기: ~/.claude/state/recall_stamp_enforce_disabled 파일 생성)\n'
    );
    process.exit(2);
  } catch {
    process.exit(0); // 어떤 경우에도 세션 방해 0
  }
})();
