#!/usr/bin/env node
/**
 * pre-enforce-guard.js — 강제 회상 장치 자기훼손 방지 가드 (PreToolUse).
 *
 * 목적 (정직):
 *   AI(지휘자)가 "부주의로" 강제 회상 장치를 끄거나 삭제·훼손하는 것을 감지.
 *   "악의 완전 봉쇄"가 아니라 "부주의 차단"이 목표 (Bash(*) 전허용이라 hard block으로
 *   owner도 못 고치게 만드는 것은 비현실적·부정직 — Fable5 강제 실현성 결론).
 *
 * 판정 (기본 경고, 좁은 hard block):
 *   - 기본: 경고 stderr + 로그 → exit 0 (통과). 부주의를 상기시키되 흐름 막지 않음.
 *   - hard block(exit 2): "enforce hook 파일 삭제(rm/mv/del) 또는 disabled flag 생성을
 *     confirm 문구 없이" 하는 명백한 무력화만 좁게.
 *   - confirm 문구 ("DISABLE ENFORCE" 또는 "GUARD OK")가 명령/입력에 있으면 무조건 통과.
 *
 * 감지 대상 경로 (부분매칭):
 *   stop-enforce-recall / user-prompt-enforce-recall / user-prompt-learn-outcome
 *   / recall_enforce_disabled (disabled flag)
 *   ★ settings.json 은 절대 건드리지 않음 (지휘자가 PreToolUse 등록) — 매칭 제외.
 *
 * 안전:
 *   - 모든 예외 흡수 → exit 0 (가드 자체가 작업 흐름을 깨지 않음).
 *   - 일반 파일/일반 작업엔 완전 무반응 (과탐 0).
 *   - 이 세션에서 지휘자가 hook 개발 중 → 정상 편집(Edit/Write)은 경고만, 절대 block 안 함.
 *
 * 패턴 참조: pre-edit-fan-guard.js (Node stdin 비동기 + processOnce + timer 이중 방어).
 * 박제 [feedback_hook_stdin_node_required]: PreToolUse stdin은 Node.js 필수.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const HOME = os.homedir();
const LOG_PATH = path.join(HOME, '.claude', 'state', 'enforce_guard.log');

// 강제 회상 장치 파일/플래그 (부분매칭). settings.json 은 절대 제외.
const ENFORCE_HOOK_NAMES = [
  'stop-enforce-recall',
  'user-prompt-enforce-recall',
  'user-prompt-learn-outcome',
];
const DISABLED_FLAG_NAME = 'recall_enforce_disabled';

function log(record) {
  try {
    fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true });
    fs.appendFileSync(
      LOG_PATH,
      `[${new Date().toISOString()}] ${JSON.stringify(record)}\n`,
      'utf-8'
    );
  } catch {}
}

function hasConfirm(text) {
  if (!text) return false;
  const up = String(text).toUpperCase();
  // ★단어 경계(Fable5): 'BODYGUARD OKAY' 가 'GUARD OK' 에 substring 매칭되던 오탐 차단.
  return /\bDISABLE ENFORCE\b/.test(up) || /\bGUARD OK\b/.test(up);
}

// 텍스트(명령 or 경로)가 강제장치 파일/플래그를 건드리는지
function touchedTargets(text) {
  if (!text) return [];
  const norm = String(text).replace(/\\/g, '/');
  const hits = [];
  for (const name of ENFORCE_HOOK_NAMES) {
    if (norm.includes(name)) hits.push(name);
  }
  if (norm.includes(DISABLED_FLAG_NAME)) hits.push(DISABLED_FLAG_NAME);
  return hits;
}

// Bash/PowerShell 명령이 "삭제/무력화" 동사를 enforce 대상에 쓰는가
//   삭제: rm / del / erase / Remove-Item / mv(이동=원위치 소멸) / move / ren / rename
//   플래그 생성: touch / New-Item / echo > / Set-Content (disabled flag)
function isDestructiveEnforceCmd(cmd, targets) {
  if (!cmd || targets.length === 0) return { block: false, kind: null };
  // ★과탐 방지(Fable5): 명령을 &&/;/|/개행 세그먼트로 나눠, 삭제 동사와 강제장치 대상이
  //   "같은 세그먼트"에 있을 때만 block. 'rm /tmp/x && grep foo <enforce>' 같은 오탐 차단.
  const segments = String(cmd).split(/&&|\|\||[;\n|]/);
  const delPat = /\b(rm|del|erase|unlink|Remove-Item|rmdir)\b/i;
  const movePat = /\b(mv|move)\b/i;   // ren/rename 제거 — git log --follow 등 오탐 유발
  for (const seg of segments) {
    if (/^\s*git\b/i.test(seg)) continue;   // git 접두 명령(log/show/diff)은 삭제 아님
    const segTargets = touchedTargets(seg);
    if (segTargets.length === 0) continue;
    const segHook = segTargets.some((t) => ENFORCE_HOOK_NAMES.includes(t));
    const segFlag = segTargets.includes(DISABLED_FLAG_NAME);
    if (segHook && (delPat.test(seg) || movePat.test(seg))) {
      return { block: true, kind: 'enforce hook 파일 삭제/이동' };
    }
    const createPat = /\b(touch|New-Item|Set-Content|Add-Content|Out-File)\b|>\s*\S*recall_enforce_disabled/i;
    if (segFlag && createPat.test(seg)) {
      return { block: true, kind: 'disabled flag 생성(강제장치 무력화)' };
    }
  }
  return { block: false, kind: null };
}

let chunks = [];
let processed = false;
let timer = null;

function done(code) {
  process.exit(code);
}

function warn(kind, targets, extra) {
  const msg = `[강제 회상 장치 가드] 강제 회상 장치를 건드립니다 — ${kind}. 대상: ${targets.join(', ')}. `
    + `부주의면 중단, 의도면 confirm("DISABLE ENFORCE"/"GUARD OK").` + (extra ? ` ${extra}` : '');
  process.stderr.write(msg + '\n');
  // ★모델 상기(Fable5): PreToolUse exit0 의 stderr 는 모델에 미전달 → stdout JSON systemMessage 로 전달.
  try { process.stdout.write(JSON.stringify({ systemMessage: msg }) + '\n'); } catch {}
}

function processOnce(src) {
  if (processed) return;
  processed = true;
  if (timer) clearTimeout(timer);

  let data;
  try {
    const stdin = chunks.join('');
    if (!stdin) return done(0);
    data = JSON.parse(stdin);
  } catch {
    return done(0); // 파싱 실패 = 다른 hook에 위임, 무반응
  }

  try {
    const tool = (data && data.tool_name) || '';
    const inp = (data && data.tool_input) || {};

    // 대상 도구만 처리
    if (!['Bash', 'PowerShell', 'Edit', 'Write', 'mcp__shell__shell_execute'].includes(tool)) {
      return done(0);
    }

    // ---- Bash / PowerShell / shell ----
    if (tool === 'Bash' || tool === 'PowerShell' || tool === 'mcp__shell__shell_execute') {
      const cmd = inp.command || '';
      const targets = touchedTargets(cmd);
      if (targets.length === 0) return done(0); // 무관 명령 = 완전 무반응

      if (hasConfirm(cmd)) {
        log({ verdict: 'pass_confirm', tool, targets, preview: cmd.slice(0, 160) });
        return done(0);
      }

      const d = isDestructiveEnforceCmd(cmd, targets);
      if (d.block) {
        log({ verdict: 'block', tool, kind: d.kind, targets, preview: cmd.slice(0, 160) });
        process.stderr.write(
          `[강제 회상 장치 가드 — 차단] confirm 없이 강제장치 무력화 시도 — ${d.kind}\n` +
          `   대상: ${targets.join(', ')}\n` +
          `   이것이 의도된 조치라면 명령에 "DISABLE ENFORCE" 또는 "GUARD OK"를 포함해 재실행하세요.\n` +
          `   부주의라면 중단하세요 — 강제 회상 장치는 과거 실수 반복을 막는 안전 장치입니다.\n`
        );
        return done(2);
      }

      // 삭제/무력화는 아니지만 대상을 언급/편집 → 경고만 통과
      log({ verdict: 'warn', tool, targets, preview: cmd.slice(0, 160) });
      warn('명령이 강제 회상 장치 경로를 참조', targets);
      return done(0);
    }

    // ---- Edit / Write ---- (지휘자 hook 개발 중 → 경고만, block 안 함)
    if (tool === 'Edit' || tool === 'Write') {
      const fp = inp.file_path || '';
      const targets = touchedTargets(fp);
      if (targets.length === 0) return done(0); // 일반 파일 = 완전 무반응

      // disabled flag를 Write로 생성 = 명백한 무력화 → block (confirm 없을 때)
      if (targets.includes(DISABLED_FLAG_NAME) && tool === 'Write') {
        const bodyConfirm = hasConfirm(inp.content) || hasConfirm(fp);
        if (!bodyConfirm) {
          log({ verdict: 'block', tool, kind: 'disabled flag 생성(Write)', targets, preview: fp.slice(0, 160) });
          process.stderr.write(
            `[강제 회상 장치 가드 — 차단] confirm 없이 disabled flag 생성 시도 (Write)\n` +
            `   경로: ${fp}\n` +
            `   의도면 내용 또는 경로에 "DISABLE ENFORCE" / "GUARD OK" 포함 후 재실행.\n`
          );
          return done(2);
        }
        log({ verdict: 'pass_confirm', tool, targets, preview: fp.slice(0, 160) });
        return done(0);
      }

      // enforce hook 파일 Edit/Write = 경고만 (지휘자 정상 개발 존중)
      log({ verdict: 'warn', tool, targets, preview: fp.slice(0, 160) });
      warn(`${tool} 대상이 강제 회상 장치 파일`, targets,
        '정상 개발/개선이면 그대로 진행하세요 (이 가드는 막지 않습니다).');
      return done(0);
    }

    return done(0);
  } catch {
    return done(0); // 모든 예외 흡수
  }
}

try {
  process.stdin.setEncoding('utf-8');
  process.stdin.on('data', (c) => chunks.push(c));
  process.stdin.on('end', () => processOnce('end'));
  process.stdin.on('error', () => processOnce('error'));
  timer = setTimeout(() => processOnce('timeout'), 3000);
} catch {
  process.exit(0);
}
