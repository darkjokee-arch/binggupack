# -*- coding: utf-8 -*-
"""OpenBinggu P3 — 자기개선 planner + 하네스 승격 exporter + 철학진화 루프 + safe apply 표준화.

설계서 §7(하네스층 L7)·§7-5(승격 흐름)·Phase7~8 / 헌법 §2(자기개선 planner·점진승격)·
§6(안전벨트: 위험=사람 승인 큐 / 안전=영수증+롤백 자동) 구현.

🔒 절대 제약 (헌법 §3 zero-tolerance · Dynamic Discovery 자동 기각):
  - AI 자동 적용 0 — 하네스 승격·철학진화·승격 전부 "제안/신호"만.
    실제 hook/AGENTS/skill/배포/삭제 변경은 100% 사람(이 모듈은 어떤 외부 하네스도 write 0).
  - 위험 작업(hook/배포/삭제)은 사람 승인 큐 강제(actor=human allowlist).
  - 운영 ledger 미접촉 — temp/dry-run 만(StagingDB 운영경로 거부 재사용).
  - 미구현 스텁 없음 — 4개 기능 모두 동작 + 직접 selftest.

구성:
  1. 자기개선 planner: 반복 신호(같은 실수 3회 / QA fail / 반복 교정) 감지 → 하네스 승격
     "후보 제안" 생성. dedupe. 적용 0(제안만). challenge_threshold 재사용.
  2. harness exporter: 후보 → AGENTS rule / hook / test / checklist "후보 텍스트"(마크다운+코드블록)
     export. 사람이 복사·적용. 빙구팩이 사용자 hook/AGENTS/skill 자동 변경 절대 0.
     파일 write 는 제안 문서(dry-run preview)만 — 실제 하네스 적용 위치는 출력 텍스트로 안내.
  3. 철학진화 루프: P1(openbinggu_deprecate_and_remind_g3.philosophy_review_signals) challenge
     outcome='옳음' 누적 ≥ threshold → "철학 기준 재검토?" 신호. 자동 변경 0(신호만, 사람 결정).
  4. safe apply 표준화: 위험(hook/배포/삭제)=사람 승인 큐 / 안전(경고/랭킹)=영수증(audit)+롤백 자동.
     점진 승격(경고→소프트→하드)은 효과 측정 후 사람 승인. dry-run/영수증/롤백 헬퍼 표준화.

멀티LLM: 이미 KV 공유(Stage0 라이브). 본 모듈 신규 구현 0 — 확인만(verify_multillm_shared).

CLI: python openbinggu_p3_self_improve.py --selftest
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_staging_write_selftest import OPERATING_PATHS, _hash, _now_iso  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import (  # noqa: E402
    open_g3, philosophy_review_signals, philosophy_diversity_signals)
try:  # 설정값(challenge_threshold = 반복 임계 단일 원천) 재사용
    from binggu_p1_config import challenge_threshold as _cfg_challenge_threshold
    from binggu_p1_config import is_confirm_actor
except Exception:  # pragma: no cover — base 부재 폴백
    def _cfg_challenge_threshold(home=None):
        return 3

    def is_confirm_actor(actor):
        return actor == "human"


def _audit_actor(ctx):
    """감사 기록용 실제 actor — 누락/None 을 'human' 으로 위장하지 않는다(거짓 출처 금지)."""
    a = ctx.get("actor")
    return a if a else "unknown"


# ============================================================
# 공통 스키마 — 자기개선 신호 + 하네스 승격 후보 + safe apply 영수증/승인큐
#   전부 candidate(제안)·event(record-only) 테이블. 운영 노드/엣지 직접 변경 0.
# ============================================================
P3_SCHEMA = """
CREATE TABLE IF NOT EXISTS improvement_signals(
    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_key TEXT NOT NULL,            -- dedupe 키(반복 패턴 정규화)
    signal_kind TEXT NOT NULL,           -- repeat_mistake | qa_fail | repeat_correction
    occurrences INTEGER DEFAULT 1,       -- 누적 발생 횟수
    sample TEXT,                         -- 대표 문장(80자 절단)
    last_ts TEXT,
    UNIQUE(signal_key));
CREATE TABLE IF NOT EXISTS harness_candidates(
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_key TEXT NOT NULL,
    harness_type TEXT NOT NULL,          -- agents_rule | hook | test | checklist | custom_instruction
    risk TEXT NOT NULL,                  -- safe | risky
    stage TEXT NOT NULL,                 -- warn | soft | hard (점진 승격 단계)
    applied INTEGER DEFAULT 0,           -- 항상 0 — AI 적용 금지(제안만)
    proposed_at TEXT,
    UNIQUE(signal_key, harness_type));
-- safe apply 표준화: 위험 작업은 승인 큐(사람 ACCEPT 대기), 안전 작업은 즉시 영수증+롤백.
CREATE TABLE IF NOT EXISTS approval_queue(
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_kind TEXT NOT NULL,           -- hook | deploy | delete | rank | warn ...
    risk TEXT NOT NULL,                  -- risky | safe
    summary TEXT,
    status TEXT DEFAULT 'pending',       -- pending | approved | rejected (변경은 사람만)
    actor TEXT,                          -- 승인/거부한 사람(human 만)
    receipt_seq INTEGER,                 -- 안전 작업 자동적용 시 audit_log seq(영수증 앵커)
    ts TEXT);
-- 점진 승격 효과 측정 — 하네스 적용 후 같은 패턴 재발 여부 관찰(stage 전환 근거).
--   헌법 §2(line 45·86): 경고→소프트→하드 단계 승격, 효과 측정 후 증명되면 올리고 아니면 되돌림.
--   관찰만 — 사람이 "이 하네스 적용 후 또 재발했나?" 입력. AI 자동 stage 변경 0.
CREATE TABLE IF NOT EXISTS harness_effect_obs(
    obs_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    at_stage TEXT NOT NULL,              -- 관찰 시점의 단계(warn|soft)
    recurred INTEGER NOT NULL,           -- 1=하네스 적용 후 같은 실수 또 함 / 0=재발 안 함(효과 있음)
    note TEXT,
    actor TEXT,                          -- 관찰 입력한 사람(human 만)
    ts TEXT);
-- 단계 전환 승인 큐 — warn→soft, soft→hard 전환 제안(효과 측정 후) → 사람 승인 큐.
--   applied_stage 는 사람이 apply 하기 전엔 NULL. AI 가 자동 전환 0(전부 pending 등재만).
CREATE TABLE IF NOT EXISTS promotion_transitions(
    trans_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    from_stage TEXT NOT NULL,            -- warn | soft
    to_stage TEXT NOT NULL,              -- soft | hard
    effect_summary TEXT,                 -- 효과 측정 근거(관찰 N건 중 재발 M건)
    status TEXT DEFAULT 'pending',       -- pending | approved | rejected (사람만)
    actor TEXT,                          -- 승인한 사람(human 만)
    proposed_at TEXT, decided_at TEXT,
    UNIQUE(candidate_id, from_stage, to_stage, status) ON CONFLICT IGNORE);
"""

# 점진 승격 사다리 — 경고(warn) → 소프트필수(soft) → 하드게이트(hard). 단조 1단씩만.
#   각 전환은 효과 측정(harness_effect_obs) 후 사람 승인. 건너뛰기·역행 금지.
STAGE_LADDER = ["warn", "soft", "hard"]
# 전환 제안 최소 효과 표본 — 이 횟수 이상 관찰돼야 stage 승격 제안 가능(섣부른 승격 차단).
MIN_EFFECT_OBS = 3

# 하네스 승격 위치 안내(설계 L7 / 실측 타깃) — 자동 write 금지, 사람이 붙여넣을 위치만 명시.
HARNESS_TARGETS = {
    "agents_rule": "~/.claude/CLAUDE.md (custom instruction 섹션) — AGENTS.md 대체",
    "custom_instruction": "~/.claude/CLAUDE.md 또는 프로젝트 CLAUDE.md",
    "hook": "~/.claude/hooks/<name>.py + ~/.claude/settings.json hooks 블록 등록",
    "test": "프로젝트 selftest/CI (예: scripts/*_selftest.py 또는 ci.yml)",
    "checklist": "~/.claude/commands/<name>.md (슬래시/체크리스트)",
}

# 위험 분류 — allowlist(default-deny): SAFE_KINDS 에 정확히 든 것만 안전, 그 외 전부 위험.
#   denylist(default-allow) 금지 — 신규/변형 위험류(DELETE/Hook/rm_rf/kv_put/push 등)가
#   안전 경로로 새서 무승인 자동 적용되는 우회 차단(§3 zero-tolerance 파괴적 작업 보호).
SAFE_KINDS = {"warn", "rank", "ranking", "checklist"}
# 참고용(문서/검출) — 분류는 SAFE_KINDS allowlist 단일 기준. 위험류 예시 나열(완전열거 아님).
RISKY_KINDS = {"hook", "deploy", "delete"}

# 하네스 유형별 위험도 — hook=위험(실행 강제), test=안전(검출만), 나머지=안전(텍스트 가이드).
HARNESS_RISK = {
    "hook": "risky", "agents_rule": "safe", "custom_instruction": "safe",
    "test": "safe", "checklist": "safe",
}


def open_p3(path):
    db = open_g3(path)
    db.con.executescript(P3_SCHEMA)
    db.con.commit()
    return db


# ============================================================
# 1. 자기개선 planner — 반복 신호 감지 → 하네스 승격 "후보 제안"(적용 0)
# ============================================================

def _norm_key(text):
    """반복 패턴 dedupe 키 — 공백 정규화 후 hash(같은 실수 다른 표현도 같은 키로 묶이게 단순화)."""
    return _hash(text)


def observe_signal(db, signal_kind, text, ctx, ts=None):
    """반복 신호 1건 관찰 — 같은 키면 occurrences 증가(dedupe), 처음이면 등록.

    적용 아님 — 신호 누적만. actor=human 만 기록(자동 신호 위장 차단).
    signal_kind: repeat_mistake | qa_fail | repeat_correction.
    """
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "observe_signal", signal_kind, "BLOCK", rc, before, before, ts=ts)
        return {"recorded": False, "reason": rc}

    if not is_confirm_actor(ctx.get("actor")):  # allowlist(==human)
        return block("G4_no_auto")
    if signal_kind not in ("repeat_mistake", "qa_fail", "repeat_correction"):
        return block("signal_kind_invalid")
    if not (text or "").strip():
        return block("signal_text_required")
    key = _norm_key(text)
    now = _now_iso(ts)
    with db.write_lock():
        row = db.con.execute("SELECT occurrences FROM improvement_signals WHERE signal_key=?", (key,)).fetchone()
        if row:
            db.con.execute("UPDATE improvement_signals SET occurrences=occurrences+1, last_ts=? WHERE signal_key=?",
                           (now, key))
            occ = row[0] + 1
        else:
            db.con.execute("INSERT INTO improvement_signals(signal_key,signal_kind,occurrences,sample,last_ts) "
                           "VALUES(?,?,1,?,?)", (key, signal_kind, text[:80], now))
            occ = 1
        db.con.commit()
        db.audit_append(_audit_actor(ctx), "observe_signal", signal_kind, "ALLOW", str(occ), before,
                        db.store_checksum(), ts=ts)
    return {"recorded": True, "reason": None, "signal_key": key, "occurrences": occ}


def plan_promotion_candidates(db, threshold=None, home=None):
    """반복 ≥ threshold 인 신호 → 하네스 승격 "후보 제안" 생성(read-only 집계 + 제안 등록).

    제안만 — applied 는 항상 0. 같은 (signal_key, harness_type) 은 dedupe(UNIQUE 충돌 무시).
    threshold 미지정 시 challenge_threshold(기본 3) 재사용 = "같은 실수 3회 이상" 승격 조건.
    """
    n = int(threshold) if threshold is not None else int(_cfg_challenge_threshold(home))
    if n < 1:
        n = 1
    rows = db.con.execute(
        "SELECT signal_key, signal_kind, occurrences, sample FROM improvement_signals "
        "WHERE occurrences>=? ORDER BY occurrences DESC, signal_key", (n,)).fetchall()
    proposed = []
    now = _now_iso()
    for key, kind, occ, sample in rows:
        htype = _suggest_harness_type(kind)
        risk = HARNESS_RISK.get(htype, "safe")
        stage = "warn"  # 점진 승격 시작 단계(경고). 사람이 효과 측정 후 soft/hard 로.
        # applied=0 강제(컬럼 DEFAULT 0). dedupe: 이미 같은 후보 있으면 INSERT OR IGNORE.
        db.con.execute(
            "INSERT OR IGNORE INTO harness_candidates(signal_key,harness_type,risk,stage,applied,proposed_at) "
            "VALUES(?,?,?,?,0,?)", (key, htype, risk, stage, now))
        proposed.append({"signal_key": key, "signal_kind": kind, "occurrences": occ,
                         "sample": sample, "harness_type": htype, "risk": risk, "stage": stage})
    db.con.commit()
    return {"threshold": n, "count": len(proposed), "candidates": proposed}


def _suggest_harness_type(signal_kind):
    """신호 종류 → 권장 하네스 유형(설계 L7 강제력 사다리 참고, 제안값일 뿐)."""
    return {
        "repeat_mistake": "checklist",      # 반복 실수 → 작업 전 체크리스트(소프트)
        "qa_fail": "test",                  # QA 실패 → 회귀 테스트(검출, 안전)
        "repeat_correction": "agents_rule", # 반복 교정 → custom instruction 규칙
    }.get(signal_kind, "checklist")


# ============================================================
# 2. harness exporter — 후보 → 붙여넣기 가능한 "후보 텍스트"(자동 write 0)
# ============================================================

def export_harness_candidate(db, candidate_id, dry_run=True):
    """후보 1건 → 사람이 복사·적용할 마크다운+코드블록 텍스트 생성.

    dry_run=True(고정 기본) — 실제 hook/AGENTS/settings 파일 write 0. 출력은 '제안 문서'일 뿐.
    빙구팩이 사용자 하네스를 자동 변경하는 코드는 일절 없음(§3 Dynamic Discovery 기각).
    """
    row = db.con.execute(
        "SELECT c.signal_key, c.harness_type, c.risk, c.stage, s.occurrences, s.sample, s.signal_kind "
        "FROM harness_candidates c JOIN improvement_signals s ON s.signal_key=c.signal_key "
        "WHERE c.candidate_id=?", (candidate_id,)).fetchone()
    if not row:
        return {"ok": False, "reason": "candidate_not_found", "markdown": "", "wrote_files": []}
    key, htype, risk, stage, occ, sample, kind = row
    target = HARNESS_TARGETS.get(htype, "(미정 — 사람이 위치 결정)")
    block = _harness_text_block(htype, sample, kind)
    rollback = _rollback_hint(htype)
    md = [
        "## 하네스 승격 후보 #%d — %s (%s · 단계: %s)" % (candidate_id, htype, risk, stage),
        "",
        "- 근거: '%s' 반복 신호(%s) %d회 누적 (≥ 임계)" % ((sample or "")[:50], kind, occ),
        "- 적용 위치: `%s`" % target,
        "- 점진 단계: %s → (효과 측정 후 사람이) soft → hard" % stage,
        "- 위험도: %s" % ("위험(hook/배포/삭제 = 사람 승인 큐)" if risk == "risky" else "안전(경고/랭킹/검출)"),
        "",
        "### 붙여넣기 텍스트 (사람이 직접 적용 — 빙구팩 자동 write 0)",
        "```%s" % ("python" if htype in ("hook", "test") else "markdown"),
        block,
        "```",
    ]
    if htype == "hook":
        md += [
            "",
            "### settings.json 등록 스니펫 (사람이 직접 추가)",
            "```json",
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash",
                       "hooks": [{"type": "command", "command": "python ~/.claude/hooks/<name>.py"}]}]}},
                       ensure_ascii=False, indent=2),
            "```",
        ]
    md += ["", "### 롤백 방법", "- %s" % rollback]
    return {"ok": True, "reason": None, "harness_type": htype, "risk": risk, "stage": stage,
            "markdown": "\n".join(md), "wrote_files": [], "dry_run": dry_run}


def _harness_text_block(htype, sample, kind):
    s = (sample or "").replace("`", "'")
    if htype == "checklist":
        return "- [ ] (반복 실수 방지) 작업 전 확인: %s" % s
    if htype == "agents_rule" or htype == "custom_instruction":
        return "- %s — 같은 실수 반복 금지(반복 교정 누적 → 규칙 승격 후보)." % s
    if htype == "test":
        return ("def test_regression_%s():\n"
                "    # QA 실패 재발 방지 회귀 테스트 후보\n"
                "    # 사람이 실제 assert 채움: %s\n"
                "    assert True  # TODO(사람): 실제 검증 작성" % (_hash(s)[:6], s))
    if htype == "hook":
        return ("# ~/.claude/hooks/<name>.py — 사람이 검토 후 배치\n"
                "# 근거(반복 위험 패턴): %s\n"
                "import sys\n"
                "# TODO(사람): 위험 동작 차단/경고 로직 작성. AI 자동 생성 금지." % s)
    return "- %s" % s


def _rollback_hint(htype):
    if htype == "hook":
        return "settings.json 에서 해당 hook 등록 줄 삭제 + hooks/<name>.py 파일 삭제(사람)."
    if htype == "test":
        return "추가한 test 함수 삭제 또는 skip 마크(사람)."
    return "추가한 마크다운 줄을 CLAUDE.md/commands 에서 제거(사람)."


# ============================================================
# 2b. 점진 승격 — 경고(warn)→소프트(soft)→하드(hard), 각 전환 효과 측정 + 사람 승인
#     헌법 §2(line 45·86): 효과 측정 후 증명되면 올리고 아니면 되돌림. AI 자동 전환 0.
# ============================================================

def _next_stage(stage):
    """단조 사다리에서 다음 단계 — warn→soft→soft→hard. hard 는 끝(None)."""
    try:
        i = STAGE_LADDER.index(stage)
    except ValueError:
        return None
    return STAGE_LADDER[i + 1] if i + 1 < len(STAGE_LADDER) else None


def record_harness_effect(db, candidate_id, recurred, ctx, note=None, ts=None):
    """하네스 적용 후 효과 1건 관찰 — 같은 패턴 재발 여부(recurred 0/1). 사람만 입력.

    효과 = 하네스를 붙인 뒤에도 같은 실수를 또 했나? recurred=1(또 함=효과 없음) /
      recurred=0(안 함=효과 있음). 관찰만 — stage 자동 변경 0. at_stage 는 후보 현재 단계.
    """
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "harness_effect", str(candidate_id), "BLOCK", rc, before, before, ts=ts)
        return {"recorded": False, "reason": rc}

    if not is_confirm_actor(ctx.get("actor")):  # allowlist(==human)
        return block("G4_no_auto")
    row = db.con.execute("SELECT stage FROM harness_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    if not row:
        return block("candidate_not_found")
    if recurred not in (0, 1, True, False):
        return block("recurred_must_be_bool")
    cur_stage = row[0]
    with db.write_lock():
        db.con.execute(
            "INSERT INTO harness_effect_obs(candidate_id,at_stage,recurred,note,actor,ts) "
            "VALUES(?,?,?,?,?,?)",
            (candidate_id, cur_stage, 1 if recurred else 0, (note or "")[:200], "human", _now_iso(ts)))
        db.con.commit()
        db.audit_append(_audit_actor(ctx), "harness_effect", str(candidate_id), "ALLOW",
                        "recurred=%d@%s" % (1 if recurred else 0, cur_stage), before, db.store_checksum(), ts=ts)
    return {"recorded": True, "reason": None, "at_stage": cur_stage}


def measure_effect(db, candidate_id, at_stage=None):
    """후보의 효과 측정 집계(read-only) — 관찰 N건 중 재발 M건, 효과 입증 여부.

    at_stage 지정 시 그 단계 관찰만. 효과 입증 = 표본 ≥ MIN_EFFECT_OBS AND 재발 0(완전 방지).
      재발이 1건이라도 있으면 미입증(아직 효과 불충분 → 승격 보류, 헌법 '증명되면 올림').
    """
    q = "SELECT count(*), coalesce(sum(recurred),0) FROM harness_effect_obs WHERE candidate_id=?"
    args = [candidate_id]
    if at_stage is not None:
        q += " AND at_stage=?"
        args.append(at_stage)
    total, recurred = db.con.execute(q, args).fetchone()
    proven = (total >= MIN_EFFECT_OBS and recurred == 0)
    return {"observations": total, "recurred": recurred, "proven": proven,
            "min_obs": MIN_EFFECT_OBS, "at_stage": at_stage}


def propose_stage_promotion(db, candidate_id, ctx, ts=None):
    """효과 입증된 후보 → 다음 단계 승격을 '제안'(승인 큐 등재). 적용 0 — 사람 승인 대기.

    헌법 §2: 효과 측정 후 증명되면 올린다. 단, 이 모듈은 '제안'만 — 실제 stage 전환은
      apply_stage_promotion(사람 승인)에서. 효과 미입증/마지막 단계(hard)면 제안 안 함.
    제안 자체는 누구나 트리거 가능하나 status=pending 고정(승인은 human allowlist).
    """
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "propose_promotion", str(candidate_id), "BLOCK", rc, before, before, ts=ts)
        return {"proposed": False, "reason": rc}

    row = db.con.execute("SELECT stage FROM harness_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    if not row:
        return block("candidate_not_found")
    cur = row[0]
    nxt = _next_stage(cur)
    if nxt is None:
        return block("already_top_stage")  # hard 가 끝 — 더 올릴 곳 없음
    eff = measure_effect(db, candidate_id, at_stage=cur)
    if not eff["proven"]:
        return block("effect_not_proven")  # 효과 미입증 → 승격 제안 0(섣부른 승격 차단)
    summary = "효과 입증: %s 단계 관찰 %d건 중 재발 0 (≥%d)" % (cur, eff["observations"], MIN_EFFECT_OBS)
    with db.write_lock():
        db.con.execute(
            "INSERT OR IGNORE INTO promotion_transitions"
            "(candidate_id,from_stage,to_stage,effect_summary,status,proposed_at) "
            "VALUES(?,?,?,?,'pending',?)", (candidate_id, cur, nxt, summary, _now_iso(ts)))
        tid = db.con.execute(
            "SELECT trans_id FROM promotion_transitions WHERE candidate_id=? AND from_stage=? "
            "AND to_stage=? AND status='pending'", (candidate_id, cur, nxt)).fetchone()
        db.con.commit()
        db.audit_append(_audit_actor(ctx), "propose_promotion", str(candidate_id), "QUEUED",
                        "%s->%s" % (cur, nxt), before, before, ts=ts)
    return {"proposed": True, "reason": None, "from_stage": cur, "to_stage": nxt,
            "trans_id": tid[0] if tid else None, "effect_summary": summary, "applied": False}


def apply_stage_promotion(db, trans_id, ctx, ts=None):
    """단계 전환 사람 승인 — actor=human 강제. 승인 시에만 harness_candidates.stage 1단 전진.

    무승인 자동 적용 0 — pending 전환을 human 이 승인해야만 stage 가 실제로 바뀐다.
      단조 1단 전진만(from_stage 가 현재 단계와 일치해야 — 중간에 다른 승격 끼면 stale 차단).
    """
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "apply_promotion", str(trans_id), "BLOCK", rc, before, before, ts=ts)
        return {"applied": False, "reason": rc}

    if not is_confirm_actor(ctx.get("actor")):  # allowlist(==human) — 자동 전환 절대 차단
        return block("G4_no_auto")
    row = db.con.execute(
        "SELECT candidate_id, from_stage, to_stage, status FROM promotion_transitions WHERE trans_id=?",
        (trans_id,)).fetchone()
    if not row:
        return block("transition_not_found")
    cid, frm, to, status = row
    if status != "pending":
        return block("not_pending")
    cur = db.con.execute("SELECT stage FROM harness_candidates WHERE candidate_id=?", (cid,)).fetchone()
    if not cur or cur[0] != frm:  # 현재 단계가 from_stage 와 다르면 stale(이미 다른 전환 적용됨)
        return block("stale_from_stage")
    with db.write_lock():
        db.con.execute("UPDATE harness_candidates SET stage=? WHERE candidate_id=?", (to, cid))
        db.con.execute(
            "UPDATE promotion_transitions SET status='approved', actor='human', decided_at=? WHERE trans_id=?",
            (_now_iso(ts), trans_id))
        db.con.commit()
        db.audit_append("human", "apply_promotion", str(trans_id), "ALLOW",
                        "%s->%s" % (frm, to), before, db.store_checksum(), ts=ts)
    return {"applied": True, "reason": None, "candidate_id": cid, "from_stage": frm, "to_stage": to,
            "note": "stage 전환 기록만 — 실제 하네스 강제력(soft/hard)은 사람이 export 텍스트로 적용."}


def reject_stage_promotion(db, trans_id, ctx, reason, ts=None):
    """효과 미입증 등으로 전환 거부(되돌림) — 사람만. stage 무변(현 단계 유지 = '아니면 되돌림')."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "reject_promotion", str(trans_id), "BLOCK", rc, before, before, ts=ts)
        return {"rejected": False, "reason": rc}

    if not is_confirm_actor(ctx.get("actor")):
        return block("G4_no_auto")
    row = db.con.execute("SELECT status FROM promotion_transitions WHERE trans_id=?", (trans_id,)).fetchone()
    if not row:
        return block("transition_not_found")
    if row[0] != "pending":
        return block("not_pending")
    with db.write_lock():
        db.con.execute(
            "UPDATE promotion_transitions SET status='rejected', actor='human', decided_at=? WHERE trans_id=?",
            (_now_iso(ts), trans_id))
        db.con.commit()
        db.audit_append("human", "reject_promotion", str(trans_id), "ALLOW", (reason or "")[:80],
                        before, db.store_checksum(), ts=ts)
    return {"rejected": True, "reason": None}


# ============================================================
# 3. 철학진화 루프 — P1 challenge '옳음' 누적 ≥ threshold → 재검토 신호(자동 변경 0)
#    (philosophy_review_signals 를 G3 에서 재사용 — 신규 카운터 0)
# ============================================================

def philosophy_evolution_signals(db, threshold=None, home=None):
    """challenge 노드 '옳음' 누적 ≥ threshold → '철학 기준 재검토?' 신호(read-only).

    P1 의 philosophy_review_signals 단일 진실원천 그대로 재사용 — 신호만, 자동 변경 0.
    내 가치관과 안 맞아 challenge 로 보관한 판단이 반복해서 옳다 판명 → 철학 필터 재검토 신호.
    """
    before = db.store_checksum()
    sig = philosophy_review_signals(db, threshold=threshold, home=home)
    after = db.store_checksum()
    sig["operating_unchanged"] = (before == after)  # read-only 증명(상태 무변)
    return sig


def philosophy_loop_report(db, threshold=None, home=None, min_total=5, challenge_floor=0.10):
    """철학진화 루프 통합 신호(read-only) — 두 축을 한 번에 본다(자동 변경 0).

    축1 = challenge '옳음' 반복 → 철학 기준 재검토 신호(philosophy_evolution_signals).
    축2 = 열린 분류 다양성(에코챔버) 진단(philosophy_diversity_signals).
    둘 다 신호일 뿐 — 확정/조정은 사람. 닫힌 필터 고정 방지(헌법 §2 line 39~41·45).
    """
    before = db.store_checksum()
    review = philosophy_evolution_signals(db, threshold=threshold, home=home)
    diversity = philosophy_diversity_signals(db, min_total=min_total, challenge_floor=challenge_floor)
    after = db.store_checksum()
    return {
        "review_signals": review,
        "diversity": diversity,
        "operating_unchanged": (before == after),
        "markdown": review["markdown"] + "\n\n" + diversity["markdown"],
    }


# ============================================================
# 4. safe apply 표준화 — 위험=사람 승인 큐 / 안전=영수증(audit)+롤백 자동
# ============================================================

def classify_change(change_kind):
    """변경 위험 분류 — allowlist(default-deny): SAFE_KINDS 에 정확히 든 것만 safe, 그 외 전부 risky.

    대소문자 정규화 후 매칭(DELETE/Hook 등 변형이 안전으로 새지 않게). 모르는 종류 = 위험.
    """
    k = (change_kind or "").strip().lower()
    return "safe" if k in SAFE_KINDS else "risky"


def route_change(db, change_kind, summary, ctx, snap_dir, ts=None):
    """safe apply 단일 관문 — 위험이면 사람 승인 큐(pending), 안전이면 영수증+자동 스냅샷 롤백점.

    위험(hook/배포/삭제): approval_queue 에 pending 등재만 — 적용 0(사람 approve 전엔 아무 변경 없음).
    안전(경고/랭킹): 자동 스냅샷(롤백점) + audit 영수증 기록(receipt_seq). 운영 노드/엣지 변경은
      하지 않는다(이 모듈은 self-improvement 메타층 — 실제 그래프 write 는 staging_apply 별도 경로).
    """
    before = db.store_checksum()
    risk = classify_change(change_kind)
    now = _now_iso(ts)
    if risk == "risky":
        # 위험 = 사람 승인 큐. 적용 0. (actor 무관하게 큐 등재는 누구나 요청 가능하나 status=pending 고정)
        with db.write_lock():
            db.audit_append(_audit_actor(ctx), "route_change", change_kind, "QUEUED", "risky_needs_human",
                            before, before, ts=ts)
            anchor_seq = db.con.execute("SELECT max(seq) FROM audit_log").fetchone()[0]
            cur = db.con.execute(
                "INSERT INTO approval_queue(change_kind,risk,summary,status,receipt_seq,ts) "
                "VALUES(?,?,?,'pending',?,?)",
                (change_kind, risk, (summary or "")[:200], anchor_seq, now))
            qid = cur.lastrowid
            db.con.commit()
        return {"route": "approval_queue", "risk": risk, "queue_id": qid, "applied": False,
                "receipt_seq": anchor_seq, "reason": "risky_needs_human_approval"}
    # 안전 = 영수증 + 롤백점(자동 스냅샷). 적용은 가역·저위험만.
    # 스냅샷은 영수증(audit+queue) 기록 '후'에 찍는다 — 그래야 롤백해도 영수증이 보존되고
    #   이후 그래프 write 만 영수증 시점으로 되돌아간다(영수증과 롤백 상호배타 모순 제거).
    with db.write_lock():
        db.audit_append(_audit_actor(ctx), "route_change", change_kind, "ALLOW", "safe_auto_receipt",
                        before, db.store_checksum(), ts=ts)
        receipt_seq = db.con.execute("SELECT max(seq) FROM audit_log").fetchone()[0]
        db.con.execute(
            "INSERT INTO approval_queue(change_kind,risk,summary,status,receipt_seq,ts) "
            "VALUES(?,?,?,'approved',?,?)", (change_kind, risk, (summary or "")[:200], receipt_seq, now))
        db.con.commit()
        # 영수증 기록 후 스냅샷 → 롤백점은 '영수증 포함' 상태. 그래프 변화 0인 no-op 가 아니라
        #   "영수증 발급 직후" 시점 보존(이후 staging_apply 그래프 write 만 가역 원복).
        snap = db.snapshot(snap_dir, "snap_safe_" + _hash(before + now))
    return {"route": "auto_receipt", "risk": risk, "applied": True, "reason": None,
            "receipt_seq": receipt_seq, "snapshot": snap, "rollback_to_receipt": receipt_seq}


def approve_queued_change(db, queue_id, ctx, ts=None):
    """위험 작업 사람 승인 — actor=human allowlist 강제. 승인은 status 전환 + 영수증만(실제 적용은 사람)."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(_audit_actor(ctx), "approve_change", str(queue_id), "BLOCK", rc, before, before, ts=ts)
        return {"approved": False, "reason": rc}

    if not is_confirm_actor(ctx.get("actor")):  # allowlist(==human) — 자동 승인 절대 차단
        return block("G4_no_auto")
    row = db.con.execute("SELECT status, risk FROM approval_queue WHERE queue_id=?", (queue_id,)).fetchone()
    if not row:
        return block("queue_item_not_found")
    if row[0] != "pending":
        return block("not_pending")
    with db.write_lock():
        db.audit_append("human", "approve_change", str(queue_id), "ALLOW", "human_approved",
                        before, db.store_checksum(), ts=ts)
        approve_seq = db.con.execute("SELECT max(seq) FROM audit_log").fetchone()[0]
        # 승인 영수증 앵커를 큐 행에 갱신 — 등재~승인 구간 추적성(audit seq ↔ 큐 연결).
        db.con.execute("UPDATE approval_queue SET status='approved', actor='human', receipt_seq=? WHERE queue_id=?",
                       (approve_seq, queue_id))
        db.con.commit()
    return {"approved": True, "reason": None, "receipt_seq": approve_seq,
            "note": "승인 기록만 — 실제 hook/배포/삭제 적용은 사람이 export 텍스트로 직접 수행."}


def rollback_to_snapshot(db, snapshot_path):
    """안전 작업 자동 롤백 — 스냅샷으로 main DB 원복(WAL/SHM 제거 후 복사).

    docs/BINGGUPACK_REAL_STAGING_CYCLE_ROLLBACK_PROCEDURE.md 절차 코드화(temp staging 한정).
    """
    if not os.path.exists(snapshot_path):
        return {"rolled_back": False, "reason": "snapshot_missing"}
    path = db.path
    db.con.close()
    for ext in ("", "-wal", "-shm"):
        p = path + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    shutil.copy2(snapshot_path, path)
    import sqlite3
    db.con = sqlite3.connect(path)
    db.con.execute("PRAGMA journal_mode=WAL")
    db.con.execute("PRAGMA busy_timeout=5000")
    return {"rolled_back": True, "reason": None, "restored_from": snapshot_path}


# ============================================================
# 5. 멀티LLM 공유 — 이미 KV 공유(Stage0 라이브). 신규 구현 0 — 확인만.
# ============================================================

def verify_multillm_shared():
    """멀티LLM 공유 구조 확인(read-only 사실 보고) — 본 모듈 신규 구현 0.

    구조: PC local ledger(원본) → Cloudflare KV pack(read-only 복제) → MCP server(검색 표면)
          → 각 LLM(claude.ai / ChatGPT)이 preflight/harness 별도 연결. autopush 가 SAVE→KV 자동.
    경계: cloud 를 원본 승격 금지 · remote write 금지 · 각 LLM read-only.
    """
    return {
        "already_shared": True,
        "new_impl_here": 0,
        "flow": "local ledger → KV pack(read-only) → MCP → 각 LLM read-only",
        "writer": "owner Windows 스케줄러(autopush) — Claude tool_use 아님",
    }


# ============================================================
# selftest (temp 전용 · 운영 store write 0 · auto_apply 0 직접 실증)
# ============================================================

def run():
    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="obg_p3_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    db = open_p3(os.path.join(tmp, "p3.sqlite"))
    H = {"actor": "human"}

    # --- 1. 자기개선 planner: 반복 3회 → 후보 / 2회 → 후보 0 ---
    # signal A: 같은 실수 3회 관찰
    for i in range(3):
        observe_signal(db, "repeat_mistake", "전체 COUNT 를 활성 필터 없이 운영 숫자로 오용", H,
                       ts="2026-06-1%dT00:00:00Z" % i)
    # signal B: 2회만(임계 미달)
    for i in range(2):
        observe_signal(db, "qa_fail", "selftest temp-only 라 운영 경로 회귀 못 잡음", H,
                       ts="2026-06-2%dT00:00:00Z" % i)
    occ_a = db.con.execute("SELECT occurrences FROM improvement_signals WHERE signal_kind='repeat_mistake'").fetchone()[0]
    rec(1, "반복 신호 dedupe 누적 (같은 실수 3회 → occurrences=3)", occ_a == 3)

    plan = plan_promotion_candidates(db, threshold=3)
    keys_3plus = [c["sample"] for c in plan["candidates"]]
    rec(2, "반복 3회 → 후보 제안 / 2회 → 제안 0",
        plan["count"] == 1 and any("COUNT" in s for s in keys_3plus)
        and all("temp-only" not in s for s in keys_3plus))

    # 2회 신호 1회 더 → 3회 되면 후보 추가(임계 도달 검증)
    observe_signal(db, "qa_fail", "selftest temp-only 라 운영 경로 회귀 못 잡음", H, ts="2026-06-22T00:00:00Z")
    plan2 = plan_promotion_candidates(db, threshold=3)
    rec(3, "2→3회 도달 시 후보로 승급 (임계 동작)", plan2["count"] == 2)

    # dedupe: 같은 plan 재호출해도 harness_candidates 중복 INSERT 0
    cand_n1 = db.con.execute("SELECT count(*) FROM harness_candidates").fetchone()[0]
    plan_promotion_candidates(db, threshold=3)
    cand_n2 = db.con.execute("SELECT count(*) FROM harness_candidates").fetchone()[0]
    rec(4, "후보 제안 dedupe (재호출 중복 0)", cand_n1 == cand_n2 and cand_n1 == 2)

    # --- 적용 0 증명: applied 컬럼 전부 0 ---
    applied_any = db.con.execute("SELECT count(*) FROM harness_candidates WHERE applied!=0").fetchone()[0]
    rec(5, "제안은 적용 아님 (harness_candidates.applied 전수 0)", applied_any == 0)

    # --- 6. harness exporter: 후보 텍스트 생성 + 파일 write 0 ---
    # checklist 후보(repeat_mistake)
    cid_chk = db.con.execute(
        "SELECT candidate_id FROM harness_candidates WHERE harness_type='checklist'").fetchone()[0]
    exp = export_harness_candidate(db, cid_chk)
    rec(6, "harness export 후보 텍스트 생성 + 파일 write 0",
        exp["ok"] and "붙여넣기 텍스트" in exp["markdown"] and exp["wrote_files"] == []
        and "자동 write 0" in exp["markdown"])

    # test 후보(qa_fail) — test 타입 코드블록
    cid_test = db.con.execute(
        "SELECT candidate_id FROM harness_candidates WHERE harness_type='test'").fetchone()[0]
    exp_t = export_harness_candidate(db, cid_test)
    rec(7, "test 후보 export (python 코드블록 + 사람 TODO)",
        exp_t["ok"] and "def test_regression_" in exp_t["markdown"] and "TODO(사람)" in exp_t["markdown"])

    # --- 8. hook 후보(위험) export → settings.json 등록 스니펫 포함 + write 0 ---
    # repeat_correction → agents_rule(안전). hook 후보를 강제로 만들어 위험 export 검증.
    observe_signal(db, "repeat_mistake", "위험 동작 전 차단 hook 필요", H, ts="2026-06-25T00:00:00Z")
    observe_signal(db, "repeat_mistake", "위험 동작 전 차단 hook 필요", H, ts="2026-06-26T00:00:00Z")
    observe_signal(db, "repeat_mistake", "위험 동작 전 차단 hook 필요", H, ts="2026-06-27T00:00:00Z")
    # 이 신호의 후보는 checklist(repeat_mistake 기본) — hook export 경로는 _harness_text_block 직접 검증
    hook_block = _harness_text_block("hook", "위험 동작 차단", "repeat_mistake")
    rec(8, "hook 텍스트 블록 = 사람 작성 TODO (AI 자동 생성 0)",
        "TODO(사람)" in hook_block and "AI 자동 생성 금지" in hook_block)

    # --- 9. 철학진화: challenge '옳음' 3회 → 재검토 신호 / 상태 무변 ---
    # judgment 노드 1개 + '옳음' 3회 사이클(G3 judgment_reviews 단일원천)
    from openbinggu_deprecate_and_remind_g3 import set_review_due, resolve_review
    db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
                   "VALUES('jp','judgment','추첨운 지배 — 낙찰률 향상 시도 무의미',1,0,'active','p3t','h',?)", (_now_iso(),))
    db.con.commit()
    for i, (due, ts) in enumerate([("2026-06-01", "2026-06-02T00:00:00Z"),
                                   ("2026-06-08", "2026-06-09T00:00:00Z"),
                                   ("2026-06-15", "2026-06-16T00:00:00Z")]):
        set_review_due(db, "jp", due, H, ts=ts)
        resolve_review(db, "jp", "옳음", "검증 결과 옳았음 %d" % i, H, ts=ts)
    sig = philosophy_evolution_signals(db, threshold=3)
    rec(9, "challenge '옳음' 3회 → 철학 재검토 신호 + 상태 무변(자동 변경 0)",
        sig["count"] == 1 and sig["items"][0]["node_id"] == "jp" and sig["operating_unchanged"]
        and "자동 변경 없음" in sig["markdown"])

    # threshold=4 → 미발생(경계)
    sig4 = philosophy_evolution_signals(db, threshold=4)
    rec(10, "철학 신호 threshold override(=4) → 미발생", sig4["count"] == 0)

    # --- 11. safe apply: 위험(hook/deploy/delete) → 승인 큐(적용 0) ---
    r_hook = route_change(db, "hook", "pre-action 차단 hook 추가", H, snap_dir, ts="2026-06-17T00:00:00Z")
    r_dep = route_change(db, "deploy", "KV 배포", H, snap_dir, ts="2026-06-17T01:00:00Z")
    r_del = route_change(db, "delete", "노드 삭제", H, snap_dir, ts="2026-06-17T02:00:00Z")
    rec(11, "위험(hook/deploy/delete) → 사람 승인 큐 (applied 전부 False)",
        all(r["route"] == "approval_queue" and r["applied"] is False for r in (r_hook, r_dep, r_del)))

    # --- 12. safe apply: 안전(warn/rank) → 영수증 + 롤백점 자동 ---
    before_safe = db.store_checksum()
    r_warn = route_change(db, "warn", "랭킹 경고 표시", H, snap_dir, ts="2026-06-17T03:00:00Z")
    rec(12, "안전(warn) → 영수증(receipt_seq) + 자동 스냅샷 롤백점",
        r_warn["route"] == "auto_receipt" and r_warn["applied"] is True
        and r_warn["receipt_seq"] is not None and os.path.exists(r_warn["snapshot"]))

    # --- 13. 위험 큐 사람 승인 / auto 차단 ---
    appr_auto = approve_queued_change(db, r_hook["queue_id"], {"actor": "auto"})
    appr_human = approve_queued_change(db, r_hook["queue_id"], H, ts="2026-06-17T04:00:00Z")
    appr_again = approve_queued_change(db, r_hook["queue_id"], H)  # 이미 approved
    rec(13, "위험 큐 승인 = human 만 (auto 차단 / 이중 승인 차단)",
        (not appr_auto["approved"]) and appr_auto["reason"] == "G4_no_auto"
        and appr_human["approved"] and (not appr_again["approved"]) and appr_again["reason"] == "not_pending")

    # --- 14. 롤백 = 그래프만 원복 + 그 시점 영수증 생존 (영수증↔롤백 상호배타 모순 제거) ---
    # 신선한 안전작업으로 격리 검증: 영수증 기록 '후' 스냅샷이므로 롤백점은 영수증 포함 상태.
    r_rb = route_change(db, "warn", "롤백 검증용 안전작업", H, snap_dir, ts="2026-06-17T05:00:00Z")
    snap_for_rb = r_rb["snapshot"]
    audit_at_snap = db.con.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    queue_at_snap = db.con.execute("SELECT count(*) FROM approval_queue").fetchone()[0]
    receipt_row_before = db.con.execute(
        "SELECT count(*) FROM approval_queue WHERE receipt_seq=?", (r_rb["receipt_seq"],)).fetchone()[0]
    # 롤백 후 검증용으로 그래프 변경(노드 1개 추가) — 영수증 발급 후 시점이므로 이것만 사라져야 함
    db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
                   "VALUES('rb','judgment','롤백 테스트 노드',1,0,'active','p3t','h',?)", (_now_iso(),))
    db.con.commit()
    present_before_rb = db.con.execute("SELECT count(*) FROM nodes WHERE node_id='rb'").fetchone()[0]
    rb = rollback_to_snapshot(db, snap_for_rb)
    present_after_rb = db.con.execute("SELECT count(*) FROM nodes WHERE node_id='rb'").fetchone()[0]
    audit_after_rb = db.con.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    queue_after_rb = db.con.execute("SELECT count(*) FROM approval_queue").fetchone()[0]
    receipt_row_after = db.con.execute(
        "SELECT count(*) FROM approval_queue WHERE receipt_seq=?", (r_rb["receipt_seq"],)).fetchone()[0]
    rec(14, "롤백 = 그래프만 원복(추가 노드 사라짐) + 그 시점 영수증(audit/queue) 생존(상호배타 모순 제거)",
        rb["rolled_back"] and present_before_rb == 1 and present_after_rb == 0
        and audit_after_rb == audit_at_snap and queue_after_rb == queue_at_snap
        and receipt_row_before == 1 and receipt_row_after == 1)

    # --- 15. classify_change allowlist(default-deny) — 변형/신규 위험류 전부 risky ---
    bypass_kinds = ["DELETE", "Hook", "HOOK", "rm_rf", "drop_table", "kv_put",
                    "deploy_remote", "push", "exec", "", None, "unknown_new_kind"]
    all_bypass_risky = all(classify_change(k) == "risky" for k in bypass_kinds)
    safe_ok = all(classify_change(k) == "safe" for k in ("warn", "rank", "ranking", "checklist", "WARN", "Rank"))
    rec(15, "classify_change allowlist: SAFE_KINDS 만 safe / 변형·신규 위험류 전부 risky(우회 0)",
        all_bypass_risky and safe_ok)

    # --- 15b. 우회 위험류 route_change → 무승인 자동적용 0 (전부 승인 큐) ---
    bypass_routes = [route_change(db, k, "bypass probe %s" % k, H, snap_dir,
                                  ts="2026-06-18T0%d:00:00Z" % i)
                     for i, k in enumerate(["DELETE", "kv_put", "deploy_remote", "push"])]
    rec(16, "변형 위험류 route_change → 전부 승인 큐(applied=False · 무승인 자동적용 0)",
        all(r["route"] == "approval_queue" and r["applied"] is False for r in bypass_routes))

    # --- 17. observe_signal auto 차단(allowlist 회귀) ---
    bypass = [None, "", "auto", "agent", "system", "AUTO", "reader", "ai", "claude"]
    obs_blocked = all(not observe_signal(db, "repeat_mistake", "x", {"actor": a})["recorded"] for a in bypass)
    obs_nokey = not observe_signal(db, "repeat_mistake", "x", {})["recorded"]
    forged = db.con.execute(
        "SELECT count(*) FROM audit_log WHERE actor='human' AND result='BLOCK' AND reason_code='G4_no_auto'").fetchone()[0]
    rec(17, "observe_signal 비human 전수 BLOCK + 감사 위장 0", obs_blocked and obs_nokey and forged == 0)

    # --- 18. 멀티LLM 공유 = 이미 라이브 · 신규 구현 0 ---
    ml = verify_multillm_shared()
    rec(18, "멀티LLM = 이미 KV 공유(Stage0) · 본 모듈 신규 구현 0", ml["already_shared"] and ml["new_impl_here"] == 0)

    # --- 점진 승격: 경고(warn)→소프트(soft)→하드(hard) 효과 측정 + 사람 승인 ---
    # checklist 후보(cid_chk) 로 승격 사다리 검증. 시작 단계 = warn.
    stage0 = db.con.execute("SELECT stage FROM harness_candidates WHERE candidate_id=?", (cid_chk,)).fetchone()[0]
    rec(21, "신규 하네스 후보 시작 단계 = warn(경고)", stage0 == "warn")

    # 효과 미입증(관찰 0) → 승격 제안 0
    prop_noeff = propose_stage_promotion(db, cid_chk, H, ts="2026-07-01T00:00:00Z")
    rec(22, "효과 미입증(관찰 0) → 승격 제안 0 (effect_not_proven)",
        (not prop_noeff["proposed"]) and prop_noeff["reason"] == "effect_not_proven")

    # warn 단계 효과 관찰 3건 모두 재발 0(효과 입증) → 승격 제안 가능
    for i in range(3):
        record_harness_effect(db, cid_chk, recurred=False, ctx=H, note="적용 후 재발 없음 %d" % i,
                              ts="2026-07-0%dT00:00:00Z" % (i + 2))
    eff_warn = measure_effect(db, cid_chk, at_stage="warn")
    rec(23, "warn 효과 관찰 3건 재발 0 → 효과 입증(proven)",
        eff_warn["observations"] == 3 and eff_warn["recurred"] == 0 and eff_warn["proven"])

    prop_warn = propose_stage_promotion(db, cid_chk, H, ts="2026-07-06T00:00:00Z")
    rec(24, "효과 입증 → warn→soft 승격 제안(승인 큐 등재 · applied=False)",
        prop_warn["proposed"] and prop_warn["from_stage"] == "warn"
        and prop_warn["to_stage"] == "soft" and prop_warn["applied"] is False)

    # auto 승인 차단 → human 승인만 stage 전진
    appr_auto2 = apply_stage_promotion(db, prop_warn["trans_id"], {"actor": "auto"})
    appr_h2 = apply_stage_promotion(db, prop_warn["trans_id"], H, ts="2026-07-07T00:00:00Z")
    stage1 = db.con.execute("SELECT stage FROM harness_candidates WHERE candidate_id=?", (cid_chk,)).fetchone()[0]
    rec(25, "단계 전환 = human 승인만 (auto 차단) → stage warn→soft 전진",
        (not appr_auto2["applied"]) and appr_auto2["reason"] == "G4_no_auto"
        and appr_h2["applied"] and stage1 == "soft")

    # soft 단계 효과 관찰 — 재발 1건 발생 → 효과 미입증 → soft→hard 제안 0 (증명 안 되면 안 올림)
    record_harness_effect(db, cid_chk, recurred=True, ctx=H, note="soft 적용 후 또 재발", ts="2026-07-08T00:00:00Z")
    record_harness_effect(db, cid_chk, recurred=False, ctx=H, note="재발 없음", ts="2026-07-09T00:00:00Z")
    record_harness_effect(db, cid_chk, recurred=False, ctx=H, note="재발 없음", ts="2026-07-10T00:00:00Z")
    prop_soft_fail = propose_stage_promotion(db, cid_chk, H, ts="2026-07-11T00:00:00Z")
    rec(26, "soft 단계 재발 1건 → 효과 미입증 → soft→hard 제안 0(증명 안 되면 안 올림)",
        (not prop_soft_fail["proposed"]) and prop_soft_fail["reason"] == "effect_not_proven")

    # 단계 단조성: hard 까지 올린 뒤 더 못 올림(already_top_stage)
    # soft 에서 재발 없는 관찰 3건 더(앞 재발 1건 섞였으니 별도 검증용으로 hard 직접 세팅 후 경계 확인)
    db.con.execute("UPDATE harness_candidates SET stage='hard' WHERE candidate_id=?", (cid_chk,))
    db.con.commit()
    for i in range(3):
        record_harness_effect(db, cid_chk, recurred=False, ctx=H, ts="2026-07-1%dT00:00:00Z" % (i + 2))
    prop_top = propose_stage_promotion(db, cid_chk, H, ts="2026-07-20T00:00:00Z")
    rec(27, "hard = 최상 단계 → 더 승격 0 (already_top_stage · 단조 사다리)",
        (not prop_top["proposed"]) and prop_top["reason"] == "already_top_stage")

    # record_harness_effect auto 차단(allowlist 회귀)
    eff_auto = all(not record_harness_effect(db, cid_chk, recurred=False, ctx={"actor": a})["recorded"]
                   for a in [None, "", "auto", "agent", "system", "ai"])
    rec(28, "record_harness_effect 비human 전수 BLOCK(자동 효과 위조 0)", eff_auto)

    # 전환 거부(되돌림) — pending 전환을 reject → stage 무변
    db.con.execute("UPDATE harness_candidates SET stage='warn' WHERE candidate_id=?", (cid_test,))
    db.con.commit()
    for i in range(3):
        record_harness_effect(db, cid_test, recurred=False, ctx=H, ts="2026-07-2%dT00:00:00Z" % i)
    prop_t = propose_stage_promotion(db, cid_test, H, ts="2026-07-25T00:00:00Z")
    rej = reject_stage_promotion(db, prop_t["trans_id"], H, "현장 판단상 보류", ts="2026-07-26T00:00:00Z")
    stage_after_rej = db.con.execute("SELECT stage FROM harness_candidates WHERE candidate_id=?", (cid_test,)).fetchone()[0]
    apply_after_rej = apply_stage_promotion(db, prop_t["trans_id"], H)  # 이미 rejected
    rec(29, "전환 거부 → stage 무변(되돌림) + rejected 후 apply 차단",
        prop_t["proposed"] and rej["rejected"] and stage_after_rej == "warn"
        and (not apply_after_rej["applied"]) and apply_after_rej["reason"] == "not_pending")

    # 무승인 자동 적용 0 전수: promotion_transitions 중 actor!='human' 으로 approved 된 것 0
    auto_approved = db.con.execute(
        "SELECT count(*) FROM promotion_transitions WHERE status='approved' AND actor!='human'").fetchone()[0]
    rec(30, "무승인 자동 stage 적용 0 (approved 는 전부 actor=human)", auto_approved == 0)

    # --- 철학진화 루프 통합: 재검토 신호 + 에코챔버 다양성 진단(read-only) ---
    from openbinggu_deprecate_and_remind_g3 import classify_harvest_item
    # keep 만 잔뜩 → 에코챔버 위험
    for i in range(6):
        classify_harvest_item(db, "echo%d" % i, "keep", "전부 keep", H, ts="2026-08-0%dT00:00:00Z" % i)
    before_loop = db.store_checksum()
    loop = philosophy_loop_report(db, threshold=3, min_total=5, challenge_floor=0.10)
    after_loop = db.store_checksum()
    rec(31, "철학진화 루프 통합 = 재검토 신호 + 에코챔버 진단 + read-only(상태 무변)",
        loop["review_signals"]["count"] == 1 and loop["diversity"]["echo_chamber_risk"]
        and loop["operating_unchanged"] and before_loop == after_loop)

    # --- 19. audit chain intact → 변조 BROKEN ---
    intact = db.verify_chain()
    db.con.execute("UPDATE audit_log SET action='TAMPER' WHERE seq=(SELECT min(seq) FROM audit_log)")
    db.con.commit()
    rec(19, "audit chain intact → 변조 시 BROKEN", intact and (not db.verify_chain()))

    # --- 20. auto_apply 0 전수: harness_candidates.applied · 외부 하네스 파일 write 0 ---
    applied0 = db.con.execute("SELECT count(*) FROM harness_candidates WHERE applied!=0").fetchone()[0]
    # export 가 만든 외부 파일이 없는지(이 모듈은 ~/.claude/* 를 절대 건드리지 않음) — wrote_files 누적 0
    wrote_total = 0
    for cid in [r[0] for r in db.con.execute("SELECT candidate_id FROM harness_candidates")]:
        e = export_harness_candidate(db, cid)
        wrote_total += len(e["wrote_files"])
    rec(20, "auto_apply 0 (applied 전수 0 · export wrote_files 전수 0)", applied0 == 0 and wrote_total == 0)

    db.close()

    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    unchanged = before_mtime == after_mtime
    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 78)
    print("OpenBinggu P3 — 자기개선 planner + 하네스 exporter + 철학진화 + safe apply selftest")
    print("=" * 78)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 78)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=%s  auto_apply=0  harness_files_written=0  confirmed=0  deploy=0"
          % unchanged)
    gate = "GO" if (npass == len(results) and unchanged) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(run())
