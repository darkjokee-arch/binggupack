#!/usr/bin/env python
"""BingguPack preflight 자동주입 hook (opt-in) — UserPromptSubmit.

작업 시작 전(=사람이 프롬프트를 보내는 순간) 관련 판단·과거 위험패턴을 회상해
**대화 상단에 자동 주입**한다(설계 §7-4 L5 Preflight / 헌법 §4·§6 안전벨트).

회수 결과는 answer_rules(build_answer_rules → render_answer_rules_md)로 변환해
"이렇게 하세요" 행동지시(조언) 형식으로 주입한다 — '저장→회수→조언' 루프의 조언 단계.
(구 render_block 나열 형식은 대체됐고, 함수만 단위 재사용 위해 잔류. 접근 가능한 구조화
mandates(옵션 <home>/preflight_mandates.json)가 있으면 detect_conflicts 로 충돌 조언까지.)

이 hook 이 하는 일은 "정보(조언) 표시 + 도장 소비용 staging 포인터 기록" — 강제·차단·ledger 저장은 0이다.
  - read-only 회상(binggu_recall.preflight_context) 만 호출 → ledger write 0.
  - stdout 으로 출력하면 Claude Code 가 그 텍스트를 컨텍스트로 주입한다(=상단 자동주입).
  - 차단 0: 항상 exit 0. 사람이 읽고 판단·진행한다(무승인 자동적용 0).
  - 작업A2(intel loop): 회상이 표시될 때만 <home>/last_recall_candidates.json(도장 소비용
    idx→node_id staging · ledger 아님·redact 정화 query)을 덮어쓰고 도장 번호 푸터를 병기한다.
    owner 채팅 1-발화("히트 N"/"미스 N")가 사람 도장 — 기록은 save_gate hook, 소비는
    binggu mark-hit/miss --from-recall. 도장 정확형 발화 자체는 회상/staging 전체 skip(MF3 —
    도장 발화 위에 staging 이 덮이면 도장 대상 ref 가 어긋나는 레이스 차단).

안전 불변 (전부 --selftest 로 증명):
  - 기본 OFF: ~/.binggupack/preflight_enabled 플래그가 없으면 즉시 종료(타 세션 무부담, import 전 차단).
  - read-only ledger: preflight_context 는 ledger 를 mode=ro 로만 연다(ledger write 0).
    write 는 도장 staging 파일(last_recall_candidates.json) 덮어쓰기 한 곳뿐(회상 표시 시에만).
  - 빈 그래프 graceful: 신규 사용자(장부 없음/노드 0) → 출력 0 · 에러 0.
  - 무관 작업: 관련 기억 0 → 출력 0(소음 0). 관련 있을 때만 상단 블록 주입.
  - scope 게이트: 기본 content_only(관련성 게이트된 content block 만 노출) · 전역 trust·owner 원칙은
    positively in-scope(BINGGU_SCOPE env=all/domain 또는 preflight_scope.json allowlist 매칭 → full)일
    때만 노출(무관 세션 전역 누출 차단 · Fable5 E). kill switch=preflight_disabled 파일 또는 BINGGU_SCOPE=off
    → 3블록 전부 즉시 OFF. 게이트는 '억제'지 '차단' 아님(MCP 명시 preflight 도구는 게이트 미적용).
  - PII / 시크릿: 노드 문장은 이미 capture_classifier 마스킹을 거쳐 저장된 것만 회상.
  - AI 위조 불가: UserPromptSubmit 은 사람 발화 이벤트 — 회상은 사람 입력(prompt/cwd)에서만 시작.
  - 차단 0 + 모든 예외 흡수(항상 exit 0) → 어떤 경우에도 세션 방해 0.

헌법 절대제약 준수: 영구=사람 SAVE 만(여기 저장 0) · AI 추천만(정보 표시) · 무승인 자동적용 0 ·
  직감검열 0(subtype 필터 없이 why_search) · 외부수확 없음(local ledger 만) ·
  node→node 강한관계 자동생성 0(읽기만) · 운영 ledger 무단 write 금지(mode=ro) ·
  cloud 무관(PC local ledger 원본만 읽음).
"""
import json
import os
import sys
from pathlib import Path


def _scripts_dir():
    """binggu_recall 가 있는 scripts/ 경로.
    1) BINGGU_SCRIPTS env 우선  2) 이 파일이 <repo>/hooks 에 있을 때 ../scripts."""
    env = os.environ.get("BINGGU_SCRIPTS")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "scripts"


def _repo_root():
    """binggupack 패키지(answer_rules/contrast_protocol)를 import 하기 위한 <repo> 경로.
    이 파일은 <repo>/hooks 에 있으므로 부모의 부모 = <repo>."""
    return Path(__file__).resolve().parent.parent


def _home():
    env = os.environ.get("BINGGU_HOME")
    return Path(env) if env else (Path.home() / ".binggupack")


def _ledger_path():
    """기본 장부 = <home>/ledger.sqlite (BINGGU_LEDGER override 허용 · 자동주입 대상 동일 경로)."""
    env = os.environ.get("BINGGU_LEDGER")
    if env:
        return Path(env)
    return _home() / "ledger.sqlite"


def render_block(res, max_remember=5, max_avoid=5, claim_cap=100):
    """preflight_context 결과 → 상단 주입용 마크다운 블록(정보 표시만).

    관련 기억/위험패턴/선호/반문이 하나도 없으면 None(소음 0 — 주입 안 함).
    헌법 안전벨트 명시: '정보 제공 · 강제 아님 · 영구 저장은 사람 SAVE 만'.
    """
    remember = res.get("remember") or []
    avoid = res.get("avoid_patterns") or []
    prefs = res.get("preferences") or []
    needs_q = res.get("needs_question")
    question = res.get("question")
    if not (remember or avoid or prefs or (needs_q and question)):
        return None

    out = ["# 빙구팩 preflight — 작업 전 관련 기억 (정보 제공 · 강제 아님 · 저장 0)"]
    if remember:
        out.append("\n## 기억할 것 (참고용 · rank 는 신선도×활용도)")
        for n in remember[:max_remember]:
            sub = n.get("semantic_subtype")
            subtxt = (" [%s]" % sub) if sub else ""
            claim = (n.get("claim") or n.get("sentence") or "")[:claim_cap]
            rank = n.get("rank_score")
            ranktxt = (" · rank %.2f" % rank) if isinstance(rank, (int, float)) else ""
            out.append("  - (%s%s) %s%s" % (n.get("node_type", "?"), subtxt, claim, ranktxt))
    if avoid:
        out.append("\n## 하면 안 되는 과거 패턴 (버그패턴 · 위험도 내림차순)")
        for m in avoid[:max_avoid]:
            claim = (m.get("claim") or "")[:claim_cap]
            out.append("  - (위험도 %.2f) %s" % (m.get("risk_score", 0.0), claim))
    if prefs:
        out.append("\n## 사용자 선호 (참고)")
        for p in prefs[:max_remember]:
            out.append("  - %s" % (p.get("claim") or "")[:claim_cap])
    if needs_q and question:
        out.append("\n반문 (참고) " + str(question)[:300])
    out.append("\n(위 내용은 과거 기억의 *추천·참고*입니다 — 자동 적용 0. "
               "영구 저장은 사람이 직접 `SAVE n` 을 타이핑할 때만.)")
    return "\n".join(out)


def render_advice_block(res, ledger_path, conflicts=None, scope=None):
    """preflight_context 결과(res) → answer_rules '이렇게 하세요' 조언 블록(content-tier 주 출력).

    '저장→회수→조언' 루프의 조언 단계. render_block(remember/avoid/preferences 나열)를 대체하는
    주 content 블록 — answer_rules 는 같은 회상 신호(avoid/prefer/remember/ask)를 행동지시로
    재구성한 superset 이므로 render_block 과 병기하면 중복 노출이 된다 → render_advice_block 을
    content-tier 주 블록으로 쓰고 render_block 은 (단위 재사용 위해) 함수만 잔류시킨다.

    사람 노출 표면은 answer_rules.render_answer_rules_md() 단독(D-1 · raw node_id 미노출 ·
    evidence_ref sha8 만). 관련 규칙이 하나도 없으면 None(관련성 게이트 유지 · 소음 0 — 무관
    작업/빈 그래프에서 render_block 과 동일하게 침묵). read-only(ledger mode=ro) · write 0.
    모든 예외 흡수 → None(hook 무방해)."""
    try:
        rr = str(_repo_root())
        if rr not in sys.path:
            sys.path.insert(0, rr)
        from binggupack.pack.answer_rules import (
            build_answer_rules, render_answer_rules_md)
        rules = build_answer_rules(res or {}, conflicts=conflicts, scope=scope,
                                   ledger_path=ledger_path)
        md = render_answer_rules_md(rules)
    except Exception:
        return None
    if not md:
        return None
    # 안전벨트 푸터(헌법 명시 · render_block 과 동일 보증 문구 보존): 강제 아님·자동 적용 0·저장 0.
    return (md + "\n\n(위 규칙은 과거 기억의 *추천·참고* — 강제 아님 · 자동 적용 0. "
            "영구 저장은 사람이 직접 `SAVE n` 을 타이핑할 때만 · 저장 0 · 빙구팩 결정 0.)")


def _load_mandates():
    """옵션 구조화 mandates 소스 — <home>/preflight_mandates.json (있으면 read-only 로드).

    조사 결과(요구④): preflight hook 이 상시 접근 가능한 mandates 저장소는 없다 — 유일 소비처인
    MCP contrast 도구(server_handlers._u_contrast)는 호출측이 mandates 를 param 으로 넘겨받으며,
    파일/config/store 로 상주하는 mandates 는 없다. 헌법상 mandates 하드코딩 금지 →
    기본은 파일 부재 → [] → conflicts=None(avoid/prefer/ask/remember 규칙만 · 정당).
    사용자가 구조화 mandates(JSON list of {clause_text, stance(require|forbid), domain, ...})를
    이 파일에 두면 그때만 대비(conflict) 조언을 켠다(데이터 주도 · 하드코딩 0).
    파일 부재/파싱 실패/list 아님/예외 → [](graceful · 소음 0 · write 0)."""
    try:
        p = _home() / "preflight_mandates.json"
        if not p.exists():
            return []
        spec = json.loads(p.read_text(encoding="utf-8"))
        return spec if isinstance(spec, list) else []
    except Exception:
        return []


def _detect_conflicts_if_any(res):
    """구조화 mandates 파일이 있을 때만 detect_conflicts 연결 → 충돌 조언 입력(read-only).

    없으면 None(요구④ 정당). contrast_protocol/detect_conflicts import 는 mandates 가 실제로
    있을 때만 지연 수행(owner 상시 경로 부담 0). detect_conflicts 는 안전/무결성 mandate 를
    이미 SKIP(헌법 양보 0). 모든 예외 흡수 → None."""
    mandates = _load_mandates()
    if not mandates:
        return None
    try:
        rr = str(_repo_root())
        if rr not in sys.path:
            sys.path.insert(0, rr)
        from binggupack.safety.contrast_protocol import detect_conflicts
        conflicts = detect_conflicts(res, mandates, home=str(_home()), env=os.environ)
        return conflicts or None
    except Exception:
        return None


def _render_trust(ledger_path):
    """양방향 신뢰도(owner 직감 / ai 반박·수용 적중률)를 상단 블록에 표시(참고·강제 0).

    회수 3단 회로의 상시(1단) 신호 편입 — preflight 자동주입에 hit_stats 를 연결한다.
    read-only(sqlite mode=ro · SELECT 만) — hit_events 가 없거나 표본 부족(N<N_MIN)이면
    None(소음 0). guard3: 적중률은 '표시 신호'일 뿐 정렬/자동결정 입력 아님(맹종 아님·헌법)."""
    try:
        import sqlite3
        sd = str(_scripts_dir())
        if sd not in sys.path:
            sys.path.insert(0, sd)
        import binggu_hit_stats as HS

        class _RO:  # hit_stats 는 db.con.execute 만 사용 → ro connection wrapper 로 충분
            pass

        uri = "file:" + str(ledger_path).replace("\\", "/") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            db = _RO()
            db.con = con
            bs = HS.both_sides(db)   # 전역(domain 무관) — 도메인 분리는 표본이 더 쪼개짐
        finally:
            con.close()
    except Exception:
        return None  # hit_events 테이블 부재(신규)·조회 실패 → 소음 0
    lines = []
    for side, label in (("owner", "내 직감(owner)"), ("ai", "AI 반박·수용(ai)")):
        s = bs.get(side) or {}
        if s.get("enough") and isinstance(s.get("rate"), (int, float)):
            lines.append("  - %s 적중률 %.0f%% (표본 %d · 시간감쇠 반영)"
                         % (label, s["rate"] * 100, s["n"]))
    if not lines:
        return None  # 양쪽 다 표본 부족 → 소음 0
    return ("## 양방향 신뢰도 (참고 가중치 · 맹종 아님 · 최종 판단은 사람+근거)\n"
            + "\n".join(lines))


def _render_person_principles(ledger_path, prompt, cwd, RC, max_n=4):
    """사람축(speaker=owner) 원칙·판단을 작업 관련도 순으로 표시(회수 1단·상시).

    preflight remember 는 rank(신선도×활용도) 정렬이라 오래된 owner 원칙/가치관이 상위에서
    밀린다 → 사람축 전용 섹션으로 상시 노출(UGI 회수 1단: 원칙을 답변 맥락에 주입).
    read-only(sqlite mode=ro). speaker 컬럼 부재(구 ledger)·관련 owner 노드 0 → None(소음 0)."""
    try:
        import sqlite3
        uri = "file:" + str(ledger_path).replace("\\", "/") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            ncols = [c[1] for c in con.execute("PRAGMA table_info(nodes)")]
            if "speaker" not in ncols:
                return None
            rows = con.execute(
                "SELECT sentence FROM nodes WHERE speaker='owner' AND state='active'"
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return None
    # Fable5 E: owner 원칙 매칭은 prompt 만 사용 — cwd 도메인 가산 제거(무관 세션 broad owner 원칙 오매칭 차단).
    #   (cwd 파라미터는 시그니처 유지 — 호출부 무변경. 세션 도메인 억제는 _resolve_scope 게이트가 담당.)
    work_text = prompt or ""
    try:
        qtok = RC._tokens(work_text)
        scored = []
        for (sent,) in rows:
            if not sent:
                continue
            rel = RC._relevance(qtok, sent)
            if rel > 0.0:
                scored.append((rel, sent))
    except Exception:
        return None
    if not scored:
        return None  # 관련 owner 원칙 없음 → 소음 0
    scored.sort(key=lambda x: -x[0])
    lines = ["## 사용자 원칙·판단 (owner 화자 · 상시 회수 · 참고 · 강제 아님)"]
    for _rel, sent in scored[:max_n]:
        lines.append("  - %s" % sent[:100])
    return "\n".join(lines)


def _is_stamp_prompt(prompt):
    """도장 정확형 발화(HIT/히트/MISS/미스/PROMOTE/승격/SAVE 계열 fullmatch) 판별 — MF3 자기 트리거 가드.

    도장 발화 위에 회상이 다시 돌면 staging 이 덮여 도장 대상(ref)이 어긋난다 → 해당 발화는
    회상 주입·staging write 전체 skip. 판정은 gate_log 파서(발화 전체/줄단위 fullmatch)에 위임 —
    문장 속 언급("그거 히트 3 어쩌고")은 도장이 아니므로 통과. 판별 실패는 비도장 취급(예외 흡수)."""
    try:
        rr = str(_repo_root())
        if rr not in sys.path:
            sys.path.insert(0, rr)
        from binggupack.safety import gate_log as GL
        p = str(prompt or "")
        return bool(GL.parse_save_indices(p) or GL.parse_hit_stamps(p)
                    or GL.parse_promote_indices(p))
    except Exception:
        return False


def _render_stamp_footer(res, prompt, trace_id=None):
    """회상 도장 번호 블록 + staging 기록(작업A2 — 사람 도장 = owner 채팅 1-발화 "히트 N").

    표시 순서(1-base)→node_id 를 <home>/last_recall_candidates.json 에 영속(gate_log.
    write_last_recall)하고 같은 번호를 화면에 보여준다 — raw node_id 미노출(claim 발췌만).
    query 는 fresh_index._leak_safe(redact+독립검증) 정화 후 영속(평문 시크릿/PII 미영속).
    ledger 는 여전히 mode=ro(위 read-only 불변). staging write 실패 시 번호 블록도 미표시
    (번호↔staging 불일치 차단). 모든 예외 흡수 → None(hook 무방해)."""
    try:
        rr = str(_repo_root())
        if rr not in sys.path:
            sys.path.insert(0, rr)
        from binggupack.safety import gate_log as GL
        ordered, seen = [], set()
        for group in ((res or {}).get("remember") or [], (res or {}).get("avoid_patterns") or [],
                      (res or {}).get("preferences") or []):
            for n in group:
                nid = n.get("node_id")
                if nid and nid not in seen:
                    seen.add(nid)
                    ordered.append((nid, (n.get("claim") or n.get("sentence") or "")[:60]))
        if not ordered:
            return None
        try:
            from binggupack.pack.fresh_index import _leak_safe
            safe_q, _ok = _leak_safe(str(prompt or "")[:1000])
        except Exception:
            safe_q = ""  # 정화기 부재 → 원문 미영속(보수적 blank · 멱등키만 약화)
        GL.write_last_recall([nid for nid, _c in ordered], query=safe_q,
                             surface="preflight_hook")
        # ★Claude 렌더 강제(2026-07-25 owner 지적): 이 블록은 컨텍스트 주입일 뿐 owner 화면에 안 뜬다.
        #   AI 가 옮기지 않으면 owner 는 무엇이 회상됐는지 못 보고 히트/미스 도장도 못 한다
        #   (실측: 한 세션 회상 17회 전부 미노출). PR#105 가 마무리 preview 만 고치고 남긴 절반.
        lines = ['[Claude 필수 — 회상 노출] 아래 회상을 **답변 첫머리**에 '
                 '`🧠 회상 N. <요지>` 한 줄씩 그대로 옮겨 owner 에게 보여라. '
                 '이 블록은 owner 화면에 안 뜬다(요약·생략·"위 참조" 금지). '
                 '안 옮기면 owner 가 히트/미스 도장을 못 해 회상 효용 측정이 멈춘다.',
                 '## 회상 도장 번호 (유용했으면 채팅 정확형 1줄 "히트 N" · 아니면 "미스 N")']
        for i, (_nid, claim) in enumerate(ordered, 1):
            lines.append("  %d. %s" % (i, claim))
        # ★ 2026-08-08 — **AI 도장 손잡이(trace_id)를 함께 준다.**
        #   종전 안내는 "사람 도장만" 이었다(2026-07-25 축). 그 뒤 owner 가 두 번 뒤집었다 —
        #   07-27 "쓰는 순간의 AI 가 가장 잘 안다"(§C-11-1 예외 신설) · 08-01 "실제로 썼냐 안
        #   썼냐가 히트/미스 아니야?"(자동주입도 판정 대상). 그런데 이 푸터는 번호만 주고
        #   trace_id 를 안 줘서 **AI 가 찍고 싶어도 찍을 수가 없었다**(실측 자동주입 1,373건 중
        #   AI 도장 0건). 낡은 안내가 남아 개정된 축을 막고 있던 자리다.
        if trace_id:
            lines.append('[AI 도장 — 이 턴 안에 찍어라] trace_stamp(trace_id="%s", '
                         'items=[{i:번호, verdict:"used"|"ignored"|"corrected", '
                         'reason_code:...}]). 판단에 썼으면 used · 안 썼으면 ignored'
                         '(회상은 맞았는데 안 썼으면 reason_code="not_applied"). '
                         '도장은 owner 가 아니라 **쓰는 순간의 네가** 찍는 것이 정본이다'
                         '(CLAUDE.md §C-11-1). owner 가 나중에 다르게 찍으면 사람 판정이 덮어쓴다.'
                         % trace_id)
        lines.append("(사람 도장: owner 채팅 정확형 1줄 \"히트 N\" / \"미스 N\" — AI 도장을 덮어쓴다)")
        return "\n".join(lines)
    except Exception:
        return None


def _resolve_scope(home, dom=None):
    """세션 scope 판정 → (level, domain, reason). level ∈ {'off','content_only','full'}.

    신뢰 우선순위(cwd 비의존·위조저항 명시 선언 우선):
      1) BINGGU_SCOPE env — off/all/특정 도메인(권위·세션 kill switch 포함).
      2) preflight_scope.json allowlist — 닫힌 도메인 목록(dom 매칭 시 full · 밖이면 off).
      3) 기본 — content_only(owner 확정): 관련성 게이트된 content block 만 하위호환 노출,
         전역 trust/person 은 억제(Fable5 E 전역 누출 차단).
    off = 3블록 전부 억제(세션 kill switch) · full = 3블록 모두 노출(positively in-scope).
    read-only(env 조회 + json 읽기만 · write 0)."""
    raw = (os.environ.get("BINGGU_SCOPE") or "").strip().lower()
    if raw:
        if raw in ("off", "0", "none", "false", "no"):
            return ("off", None, "env_off")
        if raw in ("all", "on", "1", "true", "yes", "*"):
            return ("full", None, "env_all")
        return ("full", raw, "env_domain")  # 특정 도메인 명시 선언 = positively in-scope
    allow = home / "preflight_scope.json"
    if allow.exists():
        try:
            spec = json.loads(allow.read_text(encoding="utf-8"))
            domains = [str(x).strip().lower() for x in (spec.get("domains") or [])]
        except Exception:
            domains = []
        if domains:
            if dom and dom in domains:
                return ("full", dom, "allow_match")
            return ("off", dom, "allow_miss")  # 닫힌 목록 밖 = 억제
    return ("content_only", None, "default")


def _run(data):
    # 1) 기본 OFF 빠른 차단 (import 전 — 플래그 없으면 타 세션에 부담 0)
    try:
        if (data.get("hook_event_name") or "") != "UserPromptSubmit":
            return None
        # kill switch(즉시 OFF · 최우선): preflight_disabled 파일이 있으면 enabled 여도 무조건 억제.
        # (BINGGU_SCOPE=off 세션 kill switch 는 _resolve_scope 에서 level='off' 로 처리)
        if (_home() / "preflight_disabled").exists():
            return None
        if not (_home() / "preflight_enabled").exists():
            return None
    except Exception:
        return None
    # 2) 장부 없으면(신규 사용자) graceful 종료
    try:
        ledger = _ledger_path()
        if not ledger.exists():
            return None
    except Exception:
        return None
    # 3) 플래그 ON + 장부 존재 → read-only 회상 모듈 로드
    try:
        sd = str(_scripts_dir())
        if sd not in sys.path:
            sys.path.insert(0, sd)
        import binggu_recall as RC
    except Exception:
        return None
    # 4) 사람 입력(prompt/cwd)에서만 회상 — read-only · write 0
    try:
        prompt = data.get("prompt", "") or ""
        # MF3 자기 트리거 가드: 도장/SAVE 정확형 발화 → 회상 주입·staging write 전체 skip
        #   (save_gate hook 이 이 발화를 직전 staging 에 도장하는 중 — 덮어쓰기 레이스 차단).
        if _is_stamp_prompt(prompt):
            return None
        cwd = data.get("cwd") or os.getcwd()
        # scope 게이트(단일 지점 · 별경로 누출 0): 세션 in-scope 판정 → off/content_only/full.
        try:
            dom = RC._domain_from_cwd(cwd, None)  # allowlist 도메인 매칭 힌트(권위 부여 안 함)
        except Exception:
            dom = None
        level, _scope_dom, _reason = _resolve_scope(_home(), dom)
        if level == "off":
            return None  # 무관/비활성 세션 → block+person+trust 전부 억제
        res = RC.preflight_context(str(ledger), prompt=prompt, cwd=cwd)
        # 다리c: 이미 계산된 dom(회상 시점 프로젝트)과 situation(의도 상황)을 trace 에 함께 기록.
        #   dom 은 종전 scope 게이트용으로만 쓰고 record 경로로 안 넘겨 recall_traces.domain 이 전부 NULL 이었음.
        trace_id = _maybe_record_trace(prompt, res, domain=dom, session_id=data.get("session_id"))  # Phase 2: opt-in 일 때만 회상 메타 기록(원문 0·실패 흡수)
        # 회수→조언(loop 완성): 회상 결과(res)를 answer_rules '이렇게 하세요' 조언으로 변환해
        #   content-tier 주 블록으로 출력(render_block 재구성 superset · 중복 노출 0 · content_only 노출).
        #   detect_conflicts 는 접근 가능한 구조화 mandates 가 있을 때만(옵션 파일) 연결 — 없으면
        #   conflicts=None(avoid/prefer/ask/remember 규칙만 · 요구④ 정당 · 하드코딩 0).
        conflicts = _detect_conflicts_if_any(res)
        block = render_advice_block(res, str(ledger), conflicts=conflicts)
        # 회수 1단: 사람축 원칙(owner) + 양방향 신뢰도(hit_stats)는 전역(domain-agnostic) 블록 —
        #   positively in-scope(full)일 때만 노출(Fable5 E 전역 누출 차단). read-only·관련 없으면 소음 0.
        person = _render_person_principles(str(ledger), prompt, cwd, RC) if level == "full" else None
        trust = _render_trust(str(ledger)) if level == "full" else None
        # 작업A2: 조언이 실제 표시될 때만 도장 staging+번호 푸터(표시 0 이면 staging 도 0 — stale 덮어쓰기 방지).
        stamp = _render_stamp_footer(res, prompt, trace_id=trace_id) if block else None
        parts = [b for b in (block, person, trust, stamp) if b]
        return "\n\n".join(parts) if parts else None
    except Exception:
        return None


def _maybe_record_trace(prompt, res, domain=None, session_id=None):
    """회상 효용 trace 기록(Phase 2) — opt-in(binggu trace enable / env / config)일 때만.

    ledger 회상은 read-only 그대로 — trace 는 별도 store(recall_trace.sqlite)에만 write.
    어떤 예외도 흡수(hook 무방해 · 항상 정상 진행). 기본 OFF 면 즉시 반환(부담 0).
    domain/situation(다리c): 회상 시점의 프로젝트(domain·인자)와 의도 상황(situation·prompt 분류)을
      함께 기록 — domain 은 종전 미배선(전부 NULL)이었고 situation 은 v3 신규 축(§9 Layer1)."""
    try:
        import binggu_recall_trace as RT
        h = str(_home())
        if not RT.trace_enabled(home=h):
            return
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        situation = RT.classify_situation(prompt)  # 의도 상황(lookup/decision/change/ambiguous)
        r = RT.trace_from_preflight(prompt, res, ts, domain=domain, situation=situation, session_id=session_id, home=h)
        # MF2: 발급된 trace_id + 회상 node_ids 를 staging 보존(outcome record 자동 경로 — 종전엔 버려짐).
        #   원문 0(node_id 는 식별자) · 실패 흡수(hook 무방해) · trace 미기록이면 no-op.
        if isinstance(r, dict) and r.get("recorded") and r.get("trace_id"):
            try:
                import binggu_outcome_attribution as OA
                OA.stage_last_trace(r["trace_id"], r.get("node_ids"), "preflight", ts, home=h)
            except Exception:
                pass
            # ★ 2026-08-08 — **도장 푸터가 쓸 trace_id 를 돌려준다.** 종전엔 여기서 버려서
            #   AI 가 자동주입 회상의 trace_id 를 알 방법이 없었고, 그래서 판정이 영영 안 남았다
            #   (실측: 자동주입 1,373건 중 판정 16건 = 1.2% · 그마저 전부 사람 도장 · AI 도장 0건).
            return r.get("trace_id")
    except Exception:
        return


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0  # stdin 파싱 실패 = 조용히 통과
    block = _run(data)
    if block:
        # UserPromptSubmit stdout = 컨텍스트 주입(상단). 차단 0(exit 0).
        sys.stdout.write(block + "\n")
    return 0  # 항상 0 · 관련 기억 없으면 stdout 침묵


# ---------------- 셀프테스트 (subprocess end-to-end, temp home 전용 · 운영 미접촉) ----------------
def _selftest():
    import shutil
    import sqlite3
    import subprocess
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    tmp = Path(tempfile.mkdtemp(prefix="bgp_preflight_hook_"))
    try:
        home = tmp / ".binggupack"
        home.mkdir(parents=True)
        scripts = str(_scripts_dir())
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from binggu_schema import apply_schema  # 정본 스키마 위임(fixture 인라인 CREATE TABLE 제거)
        self_path = str(Path(__file__).resolve())
        ledger = home / "ledger.sqlite"
        base_env = {**os.environ, "BINGGU_HOME": str(home),
                    "BINGGU_SCRIPTS": scripts, "PYTHONUTF8": "1"}
        base_env.pop("BINGGU_SCOPE", None)  # 상속 env 격리 → 기본(default=content_only) 테스트 결정성

        def call(payload, raw=None, extra_env=None):
            env = {**base_env, **extra_env} if extra_env else base_env
            return subprocess.run(
                [sys.executable, self_path],
                input=(raw if raw is not None else json.dumps(payload)),
                capture_output=True, text=True, env=env)

        repo_cwd = "C:/Users/fixture-user/binggupack"

        # ---- temp ledger 구성 (운영 미접촉 · binggu_recall._load_graph 스키마와 동일) ----
        #   스키마/ node_type 값은 binggu_recall._selftest 와 정확히 일치해야 _load_graph 가 읽는다
        #   (evidence=evidence_id/sentence/source_pointer_id/source_hash · node_type='judgment').
        def build_ledger():
            con = sqlite3.connect(str(ledger))
            apply_schema(con)  # 정본 스키마 위임(인라인 CREATE TABLE 제거·superset)

            def add(nid, ntype, sent, sub, used=0):
                con.execute(
                    "INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                    "created_at,semantic_subtype,use_count) VALUES(?,?,?,?,?,?,?,?,?)",
                    (nid, ntype, sent, 0, "active", "h", "2026-06-01T00:00:00Z", sub, used))
                con.execute(
                    "INSERT INTO evidence(evidence_id,sentence,source_pointer_id,source_hash)"
                    " VALUES(?,?,?,?)",
                    ("EVC-" + nid.split(":")[-1], sent, "ptr", "sh"))
            # 버그패턴(위험) · 교훈 · 선호 · 무관 노드 (node_type='judgment' = JUDGMENT_KINDS)
            add("node:CONV:aa01", "judgment",
                "검증 없이 바로 배포하면 실패한다 selftest live endpoint 확인 누락", "버그패턴", used=5)
            add("node:CONV:bb02", "judgment",
                "배포 전 반드시 live endpoint 를 확인한다", "교훈", used=2)
            add("node:CONV:ee05", "judgment",
                "배포 작업은 항상 백업 먼저 하는 것을 선호한다", "선호", used=1)
            add("node:CONV:cc03", "judgment", "토마토 수프는 마지막에 간을 맞춘다", "결정")
            con.commit()
            con.close()

        # T1 기본 OFF(플래그 없음) → stdout 침묵 (장부 있어도 미작동)
        build_ledger()
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "검증 없이 바로 배포한다", "cwd": repo_cwd})
        check(r.returncode == 0 and r.stdout.strip() == "",
              "T1 기본 OFF(플래그 없음) → stdout 침묵 · exit 0")

        # 활성화
        (home / "preflight_enabled").write_text("1", encoding="utf-8")

        # T2 위험작업 → answer_rules 조언 블록 주입(avoid=하지 마라 · 회수→조언 loop 실작동)
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "검증 없이 바로 배포하려고 한다 endpoint", "cwd": repo_cwd})
        out = r.stdout
        check(r.returncode == 0 and "빙구팩 실행 규칙" in out and "하지 마라" in out,
              "T2 위험작업 → answer_rules 조언 블록(하지 마라=avoid 규칙) 주입")
        check("강제 아님" in out and "저장 0" in out and "자동 적용 0" in out,
              "T2b 안전벨트 문구(강제 아님·저장 0·자동 적용 0) 명시")

        # T3 무관 작업(요리) → 소음 0 (관련 기억 없으면 주입 안 함)
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "오늘 점심 뭐 먹을지 고민이다", "cwd": "C:/tmp"})
        check(r.returncode == 0 and r.stdout.strip() == "",
              "T3 무관 작업 → 주입 0(소음 0)")

        # T4 read-only: 호출 전후 ledger mtime/size 불변(write 0)
        m0 = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        call({"hook_event_name": "UserPromptSubmit",
              "prompt": "검증 없이 배포 endpoint", "cwd": repo_cwd})
        m1 = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        check(m0 == m1, "T4 회상 후 ledger mtime/size 불변(read-only · write 0)")

        # T5 차단 0: 어떤 출력이 있어도 항상 exit 0
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "검증 없이 배포", "cwd": repo_cwd})
        check(r.returncode == 0, "T5 차단 0(항상 exit 0)")

        # T6 비 UserPromptSubmit 이벤트(Stop) → 무동작
        r = call({"hook_event_name": "Stop"})
        check(r.returncode == 0 and r.stdout.strip() == "", "T6 Stop 이벤트 무시(stdout 침묵)")

        # T7 깨진/빈 stdin 방어
        check(call(None, raw="{ broken").returncode == 0, "T7 깨진 stdin → exit 0")
        check(call(None, raw="").returncode == 0, "T8 빈 stdin → exit 0")

        # T9 신규 사용자(장부 없음) → graceful (플래그는 있어도 장부 부재)
        ledger.unlink()
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "검증 없이 배포", "cwd": repo_cwd})
        check(r.returncode == 0 and r.stdout.strip() == "",
              "T9 신규 사용자(장부 없음) → graceful · stdout 침묵")

        # T10 render_block 단위: 빈 결과 → None(주입 안 함)
        empty = {"remember": [], "avoid_patterns": [], "preferences": [],
                 "needs_question": False, "question": None}
        check(render_block(empty) is None, "T10 render_block 빈 결과 → None(소음 0)")

        # T11 render_block 단위: 위험 결과 → 안전벨트 문구 포함
        rich = {"remember": [{"node_type": "판단", "semantic_subtype": "교훈",
                              "claim": "배포 전 확인", "rank_score": 0.8}],
                "avoid_patterns": [{"risk_score": 0.7, "claim": "검증 없이 배포"}],
                "preferences": [{"claim": "백업 선호"}],
                "needs_question": True, "question": "같은 실수 반복 막을까요?"}
        blk = render_block(rich)
        check(blk and "자동 적용 0" in blk and "사람" in blk and "위험도 0.70" in blk,
              "T11 render_block 위험 결과 → 안전벨트 + 위험도 표기")

        # ── T12/T13 Phase 2: record_trace 배선(opt-in 일 때만 · read-only 원칙 유지) ──
        build_ledger()  # T9 에서 지웠으니 재생성
        sys.path.insert(0, scripts)
        import binggu_recall_trace as RT
        store = RT.trace_store_path(str(home))
        if os.path.exists(store):
            os.remove(store)
        # T12 opt-in OFF(기본) → preflight 호출해도 trace store 미생성(no-op)
        call({"hook_event_name": "UserPromptSubmit",
              "prompt": "검증 없이 바로 배포 endpoint", "cwd": repo_cwd})
        check(not os.path.exists(store),
              "T12 trace opt-in OFF(기본) → preflight 호출해도 trace store 미생성")
        # ledger 는 여전히 read-only(회상만) — preflight 회상이 ledger 를 건드리지 않음
        mled = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        # T13 opt-in ON(파일플래그) → 호출 후 trace store 에 회상 메타 기록(원문 0)
        RT.set_trace_flag(True, home=str(home))
        call({"hook_event_name": "UserPromptSubmit",
              "prompt": "검증 없이 바로 배포 endpoint", "cwd": repo_cwd})
        pend = RT.list_pending(home=str(home), ledger_path=str(ledger))
        check(os.path.exists(store) and len(pend) >= 1,
              "T13 trace opt-in ON → preflight 후 미판정 회상 기록(record_trace 배선)")
        check((ledger.stat().st_mtime_ns, ledger.stat().st_size) == mled,
              "T13b trace 기록돼도 ledger 는 불변(별도 store · 회상 read-only)")
        with open(store, "rb") as f:
            tb = f.read()
        check("검증 없이 바로 배포하면 실패한다".encode("utf-8") not in tb,
              "T13c 회상 노드 원문이 trace store 에 미저장(PII 0)")
        RT.set_trace_flag(False, home=str(home))

        # ── T14/T15 회수 1단: 양방향 신뢰도 trust 블록(hit_events 표본 충분 시만·read-only) ──
        # owner 직감 hit 5건(N_MIN=5) → trust 블록 주입. build_ledger 에 hit_events 테이블 존재.
        con = sqlite3.connect(str(ledger))
        for i in range(5):
            con.execute(
                "INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts,"
                "domain,context_hash,decision_id) VALUES(?,?,?,?,?,?,?,?,?)",
                ("node:HIT:%d" % i, "owner", "직감", "hit", "결정",
                 "2026-06-20T00:00:00Z", None, None, "dec-%d" % i))
        con.commit()
        con.close()
        # trust 는 전역 블록 — 신규 scope 게이트상 full 에서만 노출 → BINGGU_SCOPE=all 로 강제(보정).
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "검증 없이 바로 배포 endpoint", "cwd": repo_cwd},
                 extra_env={"BINGGU_SCOPE": "all"})
        check("양방향 신뢰도" in r.stdout and "내 직감(owner) 적중률" in r.stdout,
              "T14 hit_events 표본 충분(N>=5) + full → 양방향 신뢰도 trust 블록 주입(회수 1단)")
        # T15 trust 조회도 read-only(sqlite mode=ro) — ledger mtime/size 불변(full 로 실제 경로 구동)
        mt0 = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        call({"hook_event_name": "UserPromptSubmit",
              "prompt": "검증 없이 배포 endpoint", "cwd": repo_cwd},
             extra_env={"BINGGU_SCOPE": "all"})
        mt1 = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        check(mt0 == mt1, "T15 trust(both_sides) 조회 후 ledger 불변(read-only · write 0)")

        # ── T16 회수 1단: 사람축(speaker=owner) 원칙 상시 노출(관련도 순·소음 0) ──
        con = sqlite3.connect(str(ledger))
        con.execute(
            "INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
            "created_at,semantic_subtype,use_count,speaker) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("node:OWN:p1", "judgment", "검증 없이 배포하지 않고 백업을 먼저 하는 것을 선호한다",
             0, "active", "h", "2026-06-01T00:00:00Z", "선호", 0, "owner"))
        con.commit()
        con.close()
        # person 은 전역 블록 — full 에서만 노출 → BINGGU_SCOPE=all 로 강제(보정). 관련성 게이트는 유지.
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "검증 없이 배포 백업 먼저", "cwd": repo_cwd},
                 extra_env={"BINGGU_SCOPE": "all"})
        check("사용자 원칙" in r.stdout and "owner 화자" in r.stdout,
              "T16 사람축(speaker=owner) 원칙 + full → 상시 회수 블록 주입(회수 1단)")
        # T16b 무관 작업엔 사람축 블록 없음(소음 0) — full 이어도 관련도 0 이면 미노출(관련성 게이트)
        r2 = call({"hook_event_name": "UserPromptSubmit",
                   "prompt": "오늘 점심 메뉴 추천", "cwd": "C:/tmp"},
                  extra_env={"BINGGU_SCOPE": "all"})
        check("사용자 원칙" not in r2.stdout,
              "T16b 무관 작업(full) → 사람축 블록 미노출(관련도 0·소음 0)")

        # ── T17~T23 scope 게이트(Fable5 E 전역 누출 차단 · owner 확정 기본=content_only) ──
        # (rich ledger: 위험/교훈/선호 노드 + hit_events 5 + owner 원칙 노드 p1 세팅 상태 재사용)
        MATCH = "검증 없이 바로 배포 endpoint"  # seeded 위험/owner 노드와 매칭

        # T17 BINGGU_SCOPE=off → enabled + rich ledger + 매칭 이어도 3블록 전부 억제(stdout 침묵)
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH, "cwd": repo_cwd},
                 extra_env={"BINGGU_SCOPE": "off"})
        check(r.returncode == 0 and r.stdout.strip() == "",
              "T17 BINGGU_SCOPE=off → block+person+trust 전부 억제(세션 kill switch · 침묵)")

        # T18 BINGGU_SCOPE=all → level=full → block+person+trust 3블록 모두 노출
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH, "cwd": repo_cwd},
                 extra_env={"BINGGU_SCOPE": "all"})
        check("빙구팩 실행 규칙" in r.stdout and "사용자 원칙" in r.stdout
              and "양방향 신뢰도" in r.stdout,
              "T18 BINGGU_SCOPE=all → 조언블록+person+trust 3블록 모두 노출(full)")

        # T19 기본(env·allowlist 없음 · content_only) → block 노출·전역 trust/person 억제(Fable5 E)
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH, "cwd": repo_cwd})
        check("빙구팩 실행 규칙" in r.stdout
              and "양방향 신뢰도" not in r.stdout and "사용자 원칙" not in r.stdout,
              "T19 기본(content_only) → 조언블록 노출·trust/person 억제(전역 누출 차단)")

        # T20(요구⑤) example-project 무관 세션 owner 일반질문 → stdout 완전 침묵(오매칭 0)
        #   prompt 가 seeded 노드와 토큰 0중복 + cwd basename 'example-project' qtok 가산 안 됨(de-broadening)
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "이 프로젝트의 빌드 명령을 알려줘", "cwd": "C:/Users/fixture-user/example-project"})
        check(r.returncode == 0 and r.stdout.strip() == "",
              "T20(요구⑤) example-project 무관 세션 일반질문 → stdout 침묵(content block 매칭 0)")

        # T21 allowlist 닫힌 목록: domains=['binggupack'] → binggupack cwd 만 full, 밖은 off
        (home / "preflight_scope.json").write_text(
            json.dumps({"domains": ["binggupack"]}), encoding="utf-8")
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH, "cwd": repo_cwd})
        check("빙구팩 실행 규칙" in r.stdout and "양방향 신뢰도" in r.stdout
              and "사용자 원칙" in r.stdout,
              "T21a allowlist 매칭(cwd basename=binggupack) → level=full(3블록)")
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH,
                  "cwd": "C:/Users/fixture-user/example-project"})
        check(r.stdout.strip() == "",
              "T21b allowlist 미매칭(cwd basename=example-project) → level=off(침묵)")
        (home / "preflight_scope.json").unlink()

        # T22 kill switch(preflight_disabled 파일) → BINGGU_SCOPE=all + 매칭 이어도 침묵(최우선)
        (home / "preflight_disabled").write_text("1", encoding="utf-8")
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH, "cwd": repo_cwd},
                 extra_env={"BINGGU_SCOPE": "all"})
        check(r.stdout.strip() == "",
              "T22 kill switch(preflight_disabled) → env=all+매칭 이어도 침묵(즉시 OFF 최우선)")
        (home / "preflight_disabled").unlink()
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH, "cwd": repo_cwd},
                 extra_env={"BINGGU_SCOPE": "all"})
        check("빙구팩 실행 규칙" in r.stdout,
              "T22b kill switch 파일 제거 후 회상 재개(원복 확인)")

        # T23 read-only 불변: 신규 scope 게이트 경로(full/off) 전후 ledger mtime/size 불변(write 0)
        mz0 = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH, "cwd": repo_cwd},
             extra_env={"BINGGU_SCOPE": "all"})
        call({"hook_event_name": "UserPromptSubmit", "prompt": "이 프로젝트의 빌드 명령을 알려줘",
              "cwd": "C:/Users/fixture-user/example-project"}, extra_env={"BINGGU_SCOPE": "off"})
        mz1 = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        check(mz0 == mz1,
              "T23 scope 게이트 경로(full/off) 전후 ledger 불변(scope 파일·env read 만 · write 0)")

        # ── T24 회수→조언(loop 완성): answer_rules 조언 블록이 content_only 에서 노출 ──
        #   avoid(하지 마라)·prefer(이렇게 하라)·remember(기억하라) 행동지시 + evidence_ref(sha8).
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "검증 없이 바로 배포 백업 먼저 endpoint", "cwd": repo_cwd})
        out = r.stdout
        check("빙구팩 실행 규칙" in out and "하지 마라" in out and "이렇게 하라" in out
              and "기억하라" in out and "ev:" in out,
              "T24 조언 블록: avoid/prefer/remember 행동지시 + ev:sha8 노출(content_only)")
        check("node:CONV:aa01" not in out and "node:CONV:ee05" not in out,
              "T24b 조언 블록에 raw node_id 0(evidence_ref sha8 만 · D-1)")

        # ── T25 conflicts: 옵션 mandates 파일 있을 때만 detect_conflicts 연결(요구④ '있으면' 경로) ──
        #   preference(ee05·require) vs 비안전 forbid mandate(style) → '충돌:' 조언 규칙 생성.
        mand_path = home / "preflight_mandates.json"
        conflict_clause = "배포 작업은 백업 먼저 하지 말고 항상 바로 배포하라"  # ee05 토큰 5/8 ≥ 0.5
        mand_prompt = "배포 작업 백업 먼저 할까"
        mand_before = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        mand_path.write_text(json.dumps([
            {"clause_text": conflict_clause, "stance": "forbid",
             "source": "CLAUDE.md", "ref": "CLAUDE.md §X", "domain": "style"}]),
            encoding="utf-8")
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": mand_prompt, "cwd": repo_cwd})
        check("충돌:" in r.stdout,
              "T25 옵션 mandates 파일 → detect_conflicts 연결 → '충돌:' 조언 노출(있으면 경로)")
        # T25b mandates 파일 제거 → 충돌 조언 0(기본 conflicts=None · 요구④ 정당 · 소음 0)
        mand_path.unlink()
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": mand_prompt, "cwd": repo_cwd})
        check("충돌:" not in r.stdout,
              "T25b mandates 파일 부재 → 충돌 조언 0(기본 conflicts=None)")
        # T25c 안전 mandate(domain=safety) → detect_conflicts SKIP(헌법 양보 0 · 대비표 안 만듦)
        mand_path.write_text(json.dumps([
            {"clause_text": conflict_clause, "stance": "forbid",
             "source": "CLAUDE.md", "ref": "CLAUDE.md §3", "domain": "safety"}]),
            encoding="utf-8")
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": mand_prompt, "cwd": repo_cwd})
        check("충돌:" not in r.stdout,
              "T25c 안전 mandate(domain=safety) → detect_conflicts SKIP(충돌 조언 0·헌법 양보 0)")
        mand_path.unlink()
        # T25d mandates 로드·detect_conflicts 는 read-only — ledger mtime/size 불변(write 0)
        check((ledger.stat().st_mtime_ns, ledger.stat().st_size) == mand_before,
              "T25d conflicts 경로(파일 read + detect_conflicts) 전후 ledger 불변(write 0)")

        # ── T26/T27 작업A2 intel loop: 도장 번호 푸터 + staging write · MF3 자기 트리거 가드 ──
        # T26 조언 블록 표시 시 → 번호 푸터 병기 + staging(idx→node_id) 기록 · raw node_id 미노출
        stg = home / "last_recall_candidates.json"
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH, "cwd": repo_cwd})
        stg_items = []
        if stg.exists():
            stg_items = (json.loads(stg.read_text(encoding="utf-8")) or {}).get("items") or []
        check("회상 도장 번호" in r.stdout and '"히트 N"' in r.stdout
              and len(stg_items) >= 1
              and all(i.get("idx") == n + 1 for n, i in enumerate(stg_items))
              and "node:CONV:aa01" not in r.stdout,
              "T26 조언 표시 → 도장 번호 푸터 + staging 기록(idx=번호·raw node_id 미노출)")
        # T26c(2026-07-25 owner 지적 회귀방지): 회상 블록은 owner 화면에 안 뜨므로 AI 렌더 지시가
        #   반드시 동봉돼야 한다. 지시가 빠지면 owner 는 회상을 못 보고 히트/미스 도장도 못 한다
        #   (실측: 한 세션 회상 17회 전부 미노출 → 효용 측정 정지).
        check("[Claude 필수 — 회상 노출]" in r.stdout and "답변 첫머리" in r.stdout,
              "T26c 회상 푸터에 Claude 렌더 지시 동봉(owner 미노출 재발 차단)")
        # T26b 회상 표면의 write 는 staging 뿐 — ledger 는 여전히 불변(read-only 불변 유지)
        mt26 = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        call({"hook_event_name": "UserPromptSubmit", "prompt": MATCH, "cwd": repo_cwd})
        check((ledger.stat().st_mtime_ns, ledger.stat().st_size) == mt26,
              "T26b staging write 경로에서도 ledger 불변(write 는 staging 파일 한 곳뿐)")

        # T27 MF3: 도장 정확형 발화("히트 1"/"SAVE 1"/"승격 2") → 회상 주입·staging 덮어쓰기 전체 skip
        #   (도장 발화 위에 staging 이 덮이면 도장 대상 ref 가 어긋나는 레이스 차단)
        before_stg = stg.read_bytes()
        for _p in ("히트 1", "미스 2", "SAVE 1", "승격 2"):
            r = call({"hook_event_name": "UserPromptSubmit", "prompt": _p, "cwd": repo_cwd})
            check(r.returncode == 0 and r.stdout.strip() == "",
                  "T27 도장 발화 '%s' → 주입 skip(침묵)" % _p)
        check(stg.read_bytes() == before_stg,
              "T27e 도장 발화 4종 후 staging 불변(덮어쓰기 레이스 차단)")
        # T27f 문장 속 언급("그거 히트 3 ...")은 도장이 아님 — 가드 통과(단위 판별로 확인)
        check(_is_stamp_prompt("그거 히트 3 어쩌고 얘기") is False
              and _is_stamp_prompt("히트 1") is True and _is_stamp_prompt("승격 2") is True,
              "T27f _is_stamp_prompt: 정확형만 skip(문장 속 언급은 통과)")

        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
