#!/usr/bin/env node
/**
 * user-prompt-enforce-recall.js — 강제 회상 "감지" (UserPromptSubmit · SYNC)
 *
 * owner 발화가 결정/검토/의견 요청이면 pending 마커를 남기고 카운터를 리셋한다.
 * 짝 hook stop-enforce-recall.js(Stop·sync)가 이 마커를 읽어, 이번 turn 에서
 * recall/cloud_recall/why 를 한 번도 안 불렀으면 exit 2 로 재답변을 강제한다.
 *
 * 여기서는 감지·기록만(차단 0 · 항상 exit 0). 판정은 결정적 정규식(과대포착 금지 —
 * 과소포착 허용). 근거: core_behavior_master A-1 "결정/검토 전 능동 회상 의무" 강제 배선.
 * (Fable5 강제 실현성: 의미판정 배제·결정적 신호만. #1 강제 회상이 4종 중 유일한 진짜 강제.)
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const STATE = path.join(os.homedir(), '.claude', 'state');
const PENDING = path.join(STATE, 'recall_enforce_pending.json');
const COUNTER = path.join(STATE, 'recall_enforce_count.txt');

// 결정/검토/의견 요청만(좁게). 단순 조회("보여줘"·"뭐야"·"확인")는 제외 → 과대포착 방지.
const DECISION_RE =
  /(할까|해도\s*(될까|되나|돼\??)|검토|의견|추천|어때|어떻게\s*생각|낫(나|을까|겠)|골라|고를까|결정(해|할|하는게|난)|판단해|선택할|방향(성|\s|을|이)|맞(나|아\?|어\?)|괜찮(나|아\?|을까|겠))/;

async function readStdin() {
  return new Promise((resolve) => {
    let d = '';
    process.stdin.setEncoding('utf-8');
    process.stdin.on('data', (c) => (d += c));
    process.stdin.on('end', () => resolve(d));
    setTimeout(() => resolve(d), 1500);
  });
}

(async () => {
  try {
    const raw = await readStdin();
    let prompt = '';
    try {
      prompt = JSON.parse(raw).prompt || '';
    } catch {
      process.exit(0);
    }
    fs.mkdirSync(STATE, { recursive: true });
    if (prompt && DECISION_RE.test(prompt)) {
      // 결정요청 turn → pending 기록 + 카운터 리셋(새 turn 은 다시 1회 강제 가능)
      fs.writeFileSync(PENDING, JSON.stringify({ ts: Date.now(), len: prompt.length }), 'utf-8');
      try { fs.unlinkSync(COUNTER); } catch {}
    } else {
      // 비결정 turn → pending 해제(소음 0 · 조회/일반대화엔 강제 안 함)
      try { fs.unlinkSync(PENDING); } catch {}
      try { fs.unlinkSync(COUNTER); } catch {}
    }
  } catch {}
  process.exit(0); // 항상 통과 — 감지·기록만, 차단 0
})();
