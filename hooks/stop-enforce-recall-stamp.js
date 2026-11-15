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

/**
 * ★ 2026-08-08 — **판정 소스를 transcript 가 아니라 trace store 로 옮긴다.**
 *
 * 종전에는 transcript 의 tool_use 이름만 훑었다. 그런데 회상의 92%는 **자동주입**
 * (UserPromptSubmit hook 이 프롬프트에 끼워넣는 것)이라 도구 호출 기록이 아예 없다 —
 * 이 hook 은 구조적으로 그것을 볼 수 없었고, 만든 날 로그 27줄이 전부 `no_recall_this_turn`
 * 이었다. **같은 날 아침 traj 에 "자발 회상 0 · 전부 hook 강제" 라고 적어 놓고 자발 회상만
 * 잡는 장치를 만든 것**이다(실측: 자동주입 1,373건 중 판정 16건 = 1.2% · AI 도장 0건).
 *
 * 이제 recall_trace.sqlite 를 직접 본다 — 이번 세션에 기록됐는데 판정이 없는 trace 가
 * 있으면 도장 의무가 있다. 읽기 전용이고(RO), 실패는 전부 흡수해 통과시킨다.
 */
const TRACE_DB = path.join(os.homedir(), '.binggupack', 'recall_trace.sqlite');
const REPO = path.join(os.homedir(), 'binggupack');

/**
 * ★ 기준은 **정본 함수 하나**로만 잰다 — `recall_trace.list_pending`.
 *   자동주입은 전부가 도장 대상이 아니다: 상위 N(2) + 관련도 하한(0.6)을 통과한 것만 찍을 수
 *   있다(2026-08-01 owner B안 · `_autoinject_judgeable`). 이 hook 이 "미판정이면 무조건 막는다"
 *   로 세면 **찍을 수 없는 것 때문에 영영 막힌다** — 실측으로 확인했다(같은 세션 5건 중 3건이
 *   `not_judgeable` 거부). 그래서 SQL 로 흉내내지 않고 정본을 그대로 부른다.
 *   실패(파이썬 부재·경로 변경·타임아웃)는 전부 0 = 통과. 막는 쪽이 아니라 여는 쪽으로 실패한다.
 */
const PY = `
import sys, json
sys.path.insert(0, r"${REPO.replace(/\\/g, '\\\\')}")
sys.path.insert(0, r"${path.join(REPO, 'scripts').replace(/\\/g, '\\\\')}")
try:
    import os, sqlite3
    import binggu_recall_trace as RT
    rows = RT.list_pending(session_id=sys.argv[1]) or []
    # ★ 2026-11-14 — list_pending 의 idx 는 화면용 일련번호라 trace_stamp 의 i(그 회상 안 자리)와 다르다.
    #   일련번호로 찍으면 bad_index 로 거절돼 아무리 찍어도 안 풀렸다(8/8 꺼짐의 유력 원인 · 실측 26건 중 22건 거절).
    #   그래서 회상별 «자리 번호»를 직접 계산해 차단 문구에 실어 준다.
    by = {}
    try:
        con = sqlite3.connect("file:" + os.path.join(os.path.expanduser("~"), ".binggupack", "recall_trace.sqlite") + "?mode=ro", uri=True)
        for r in rows:
            tid, nid = r.get("trace_id"), r.get("node_id")
            if tid not in by:
                row = con.execute("SELECT recalled_json FROM recall_traces WHERE trace_id=?", (tid,)).fetchone()
                rj = json.loads(row[0]) if row and row[0] else []
                order = [x.get("node_id") for x in (rj.get("nodes", rj) if isinstance(rj, dict) else rj) if isinstance(x, dict)]
                by[tid] = {"order": order, "pos": []}
            if nid in by[tid]["order"]:
                by[tid]["pos"].append(by[tid]["order"].index(nid) + 1)
        con.close()
    except Exception:
        by = {}
    # 찍을 기억의 앞머리도 실어 준다 — 내용을 모르면 판정하려고 매번 따로 조회해야 했다(2026-11-14).
    heads = {}
    try:
        led = sqlite3.connect("file:" + os.path.join(os.path.expanduser("~"), ".binggupack", "ledger.sqlite") + "?mode=ro", uri=True)
        for t, v in by.items():
            for p in v["pos"]:
                s = led.execute("SELECT sentence FROM nodes WHERE node_id=?", (v["order"][p - 1],)).fetchone()
                heads.setdefault(t, {})[str(p)] = " ".join(((s[0] if s else "") or "").split())[:70]
        led.close()
    except Exception:
        heads = {}
    print(json.dumps({"n": len(rows), "items": {t: sorted(v["pos"]) for t, v in by.items() if v["pos"]}, "heads": heads}))
except Exception:
    print(json.dumps({"n": 0, "items": {}}))
`;

function unstampedInSession(sessionId) {
  try {
    if (!sessionId || !fs.existsSync(TRACE_DB)) return 0;
    const { execFileSync } = require('child_process');
    const out = execFileSync('python', ['-c', PY, String(sessionId)],
      { timeout: 8000, encoding: 'utf-8', windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    const j = JSON.parse(String(out).trim().split('\n').pop());
    PENDING_ITEMS = (j && j.items) || {};
    PENDING_HEADS = (j && j.heads) || {};
    return parseInt(j && j.n, 10) || 0;
  } catch { return 0; }   // 파이썬 부재·타임아웃 등 — 세션 방해 0
}

/** 차단 문구에 실을 «회상별 자리 번호» — unstampedInSession 이 채운다. */
let PENDING_ITEMS = {};
let PENDING_HEADS = {};

function pendingHint() {
  const keys = Object.keys(PENDING_ITEMS);
  if (!keys.length) return '';
  const one = (t) => PENDING_ITEMS[t].map((p) => {
    const h = (PENDING_HEADS[t] || {})[String(p)];
    return h ? `i=${p} «${h}»` : `i=${p}`;
  }).join(' / ');
  const shown = keys.slice(0, 8).map((t) => `${t} → ${one(t)}`).join(' · ');
  return ` ★찍을 자리(trace_id → i): ${shown}${keys.length > 8 ? ` 외 ${keys.length - 8}회` : ''}.`;
}

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
    let sessionId = '';
    try {
      const j = JSON.parse(raw);
      tp = j.transcript_path || '';
      sessionId = j.session_id || (tp ? path.basename(tp, '.jsonl') : '');
    } catch { pass('no_transcript'); }
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

    if (stamped) pass('stamped');   // 이 턴에 한 번이라도 찍었으면 통과(도구 호출이 증거)

    // 자동주입 회상은 transcript 에 tool_use 로 안 남는다 — 정본 list_pending 으로 센다.
    // 직접 인출을 한 턴에도 자리 번호 안내를 위해 정본을 부른다(실패하면 0 — 그때는 인출 사실만으로 막는다).
    const counted = unstampedInSession(sessionId);
    const pending = recalled ? Math.max(1, counted) : counted;
    if (!pending) pass('no_recall_this_turn');   // 찍을 게 없으면 도장 의무 없음
    log(`pending=${pending}`);

    try { fs.writeFileSync(COUNTER, '1', 'utf-8'); } catch {}
    log('BLOCK recall_without_stamp');
    process.stderr.write(
      '[회상 도장 누락] 이번 세션 회상(자동주입 포함)에 효용 도장이 없습니다. ' +
      '쓴 순간의 판단이 가장 정확하므로 도장은 답변하는 쪽이 찍습니다(CLAUDE.md §C-11-1). ' +
      'trace_stamp(trace_id + i + used/ignored/corrected)로 판정한 뒤 답변을 마치세요 — ' +
      'trace_id 는 회상 블록 푸터에 실려 있고, 없으면 trace_review 로 미판정 목록을 받으세요. ' +
      '판단에 안 썼으면 ignored(회상은 맞았는데 안 썼으면 reason_code="not_applied")로 찍으면 됩니다. ' +
      'i 는 그 회상 안에서의 자리 번호(1부터)입니다 — 미판정 목록의 일련번호가 아닙니다.' + pendingHint() + ' ' +
      '(1회 한정 강제 · 끄기: ~/.claude/state/recall_stamp_enforce_disabled 파일 생성)\n'
    );
    process.exit(2);
  } catch {
    process.exit(0); // 어떤 경우에도 세션 방해 0
  }
})();
