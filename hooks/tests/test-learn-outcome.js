#!/usr/bin/env node
/**
 * test-learn-outcome.js — user-prompt-learn-outcome.js 블랙박스 통합 테스트 (2026-07-14 4cli)
 * 실제 hook 을 child_process 로 spawn, stdin JSON + temp transcript 주입, BINGGU_HOME 으로 큐 격리.
 * 검증: 반문질책 캡처 복구 · 진짜질문 배제 · aiAnswer 게이트 · silent-death(미소비 카운트) 해소.
 * 운영 홈(~/.binggupack, ~/.claude/state) 미접촉 — temp home 만 사용.
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOOK = path.join(__dirname, '..', 'user-prompt-learn-outcome.js');
let pass = 0, fail = 0;

function mktemp() {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'lo-test-'));
  fs.mkdirSync(path.join(d, 'state'), { recursive: true });
  return d;
}
function writeTranscript(dir, lines) {
  const tp = path.join(dir, 'transcript.jsonl');
  fs.writeFileSync(tp, lines.map((o) => JSON.stringify(o)).join('\n') + '\n', 'utf-8');
  return tp;
}
function runHook(home, prompt, transcriptPath) {
  const input = JSON.stringify({ hook_event_name: 'UserPromptSubmit', prompt, transcript_path: transcriptPath });
  return spawnSync('node', [HOOK], { input, env: { ...process.env, BINGGU_HOME: home }, encoding: 'utf-8' });
}
function queueEntries(home) {
  try {
    return fs.readFileSync(path.join(home, 'state', 'learn_outcome_queue.jsonl'), 'utf-8')
      .split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
  } catch { return []; }
}
function check(name, cond) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name); }
}

// 공통: 직전 user 발화 + assistant text turn(=aiAnswer 존재)
const convo = [
  { type: 'user', message: { content: '이전 질문이야' } },
  { type: 'assistant', message: { content: [{ type: 'text', text: '제가 드린 답변입니다' }] } },
];

// T1 반문질책 "너 안 읽었지?" + aiAnswer 존재 → 캡처(stdout 침묵·exit0·miss)
{
  const home = mktemp();
  const r = runHook(home, '너 안 읽었지?', writeTranscript(home, convo));
  const q = queueEntries(home);
  check('T1 반문질책("너 안 읽었지?") 캡처·stdout침묵·exit0', r.stdout === '' && r.status === 0 && q.length === 1 && q[0].outcome === 'miss');
}
// T2 순수질문 "이거 어디 있어?" → 무캡처(POS/NEG 미매칭)
{
  const home = mktemp();
  const r = runHook(home, '이거 어디 있어?', writeTranscript(home, convo));
  check('T2 순수질문("이거 어디 있어?") 무캡처', queueEntries(home).length === 0 && r.status === 0);
}
// T3 순수의문 "가능한거 아닌가?" → 무캡처(의문어미 아닌가$ 배제)
{
  const home = mktemp();
  runHook(home, '가능한거 아닌가?', writeTranscript(home, convo));
  check('T3 순수의문("가능한거 아닌가?") 무캡처', queueEntries(home).length === 0);
}
// T4 반문질책이나 직전 AI text 없음 → aiAnswer 게이트 무캡처
{
  const home = mktemp();
  const r = runHook(home, '너 안 읽었지?', writeTranscript(home, [{ type: 'user', message: { content: '첫 발화' } }]));
  check('T4 aiAnswer 게이트(직전 AI답변 없음) 무캡처', queueEntries(home).length === 0 && r.status === 0);
}
// T5 consumed=true 500줄 선주입 + 신규 완곡지적 → 캡처(미소비<500·silent-death 해소)
{
  const home = mktemp();
  const q = path.join(home, 'state', 'learn_outcome_queue.jsonl');
  const filler = Array.from({ length: 500 }, () => JSON.stringify({ ts: 'x', outcome: 'miss', consumed: true })).join('\n') + '\n';
  fs.writeFileSync(q, filler, 'utf-8');
  runHook(home, '제대로 안 읽었네', writeTranscript(home, convo));
  const unconsumed = queueEntries(home).filter((e) => !e.consumed);
  check('T5 소비완료 500줄+신규 완곡지적 캡처(silent-death 해소)', unconsumed.length === 1);
}

// T6 긍정 피드백 "맞네 정확해" → 캡처(hit) — POS 경로 무회귀(구두점 제거 개정이 긍정 안 깨뜨림)
{
  const home = mktemp();
  const r = runHook(home, '맞네 정확해', writeTranscript(home, convo));
  const q = queueEntries(home);
  check('T6 긍정("맞네 정확해") 캡처·hit — POS 무회귀', r.status === 0 && q.length === 1 && q[0].outcome === 'hit');
}
// T7 긴 서술문 중간 우연매칭 → 무캡처(SHORT/HEAD 게이트 무회귀)
{
  const home = mktemp();
  runHook(home, '오늘 안에 반드시 처리해야 하는 그 일정을 다시 확인해서 오후까지 완료해줘', writeTranscript(home, convo));
  check('T7 긴 서술문 앞머리밖 우연매칭 무캡처 — 길이/앞머리 게이트 무회귀', queueEntries(home).length === 0);
}

console.log('\n결과: ' + pass + ' PASS / ' + fail + ' FAIL');
process.exit(fail === 0 ? 0 : 1);
