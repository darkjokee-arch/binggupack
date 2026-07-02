#!/usr/bin/env python
"""BingguPack preflight 자동주입 hook (opt-in) — UserPromptSubmit.

작업 시작 전(=사람이 프롬프트를 보내는 순간) 관련 판단·과거 위험패턴을 회상해
**대화 상단에 정보로 자동 주입**한다(설계 §7-4 L5 Preflight / 헌법 §4·§6 안전벨트).

이 hook 이 하는 일은 "정보 표시"뿐 — 강제·차단·저장은 0이다.
  - read-only 회상(binggu_recall.preflight_context) 만 호출 → ledger / 운영 store write 0.
  - stdout 으로 출력하면 Claude Code 가 그 텍스트를 컨텍스트로 주입한다(=상단 자동주입).
  - 차단 0: 항상 exit 0. 사람이 읽고 판단·진행한다(무승인 자동적용 0).

안전 불변 (전부 --selftest 로 증명):
  - 기본 OFF: ~/.binggupack/preflight_enabled 플래그가 없으면 즉시 종료(타 세션 무부담, import 전 차단).
  - read-only: preflight_context 는 ledger 를 mode=ro 로만 연다(write 0). 운영 store 불변.
  - 빈 그래프 graceful: 신규 사용자(장부 없음/노드 0) → 출력 0 · 에러 0.
  - 무관 작업: 관련 기억 0 → 출력 0(소음 0). 관련 있을 때만 상단 블록 주입.
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


def _run(data):
    # 1) 기본 OFF 빠른 차단 (import 전 — 플래그 없으면 타 세션에 부담 0)
    try:
        if (data.get("hook_event_name") or "") != "UserPromptSubmit":
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
        cwd = data.get("cwd") or os.getcwd()
        res = RC.preflight_context(str(ledger), prompt=prompt, cwd=cwd)
        _maybe_record_trace(prompt, res)  # Phase 2: opt-in 일 때만 회상 메타 기록(원문 0·실패 흡수)
        block = render_block(res)
        trust = _render_trust(str(ledger))  # 회수 1단: 양방향 신뢰도(표본 충분 시만·read-only·소음 0)
        if trust:
            block = (block + "\n\n" + trust) if block else trust
        return block
    except Exception:
        return None


def _maybe_record_trace(prompt, res):
    """회상 효용 trace 기록(Phase 2) — opt-in(binggu trace enable / env / config)일 때만.

    ledger 회상은 read-only 그대로 — trace 는 별도 store(recall_trace.sqlite)에만 write.
    어떤 예외도 흡수(hook 무방해 · 항상 정상 진행). 기본 OFF 면 즉시 반환(부담 0)."""
    try:
        import binggu_recall_trace as RT
        h = str(_home())
        if not RT.trace_enabled(home=h):
            return
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        RT.trace_from_preflight(prompt, res, ts, home=h)
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
        self_path = str(Path(__file__).resolve())
        ledger = home / "ledger.sqlite"
        base_env = {**os.environ, "BINGGU_HOME": str(home),
                    "BINGGU_SCRIPTS": scripts, "PYTHONUTF8": "1"}

        def call(payload, raw=None):
            return subprocess.run(
                [sys.executable, self_path],
                input=(raw if raw is not None else json.dumps(payload)),
                capture_output=True, text=True, env=base_env)

        repo_cwd = "C:/Users/PC/binggupack"

        # ---- temp ledger 구성 (운영 미접촉 · binggu_recall._load_graph 스키마와 동일) ----
        #   스키마/ node_type 값은 binggu_recall._selftest 와 정확히 일치해야 _load_graph 가 읽는다
        #   (evidence=evidence_id/sentence/source_pointer_id/source_hash · node_type='judgment').
        def build_ledger():
            con = sqlite3.connect(str(ledger))
            con.executescript(
                "CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
                " candidate INT, state TEXT, content_hash TEXT, created_at TEXT,"
                " semantic_subtype TEXT, use_count INTEGER DEFAULT 0);"
                "CREATE TABLE evidence(evidence_id TEXT, sentence TEXT, source_pointer_id TEXT,"
                " source_hash TEXT);"
                "CREATE TABLE edges(edge_id TEXT, relation TEXT, source TEXT, target TEXT,"
                " candidate INT, state TEXT, evidence_refs TEXT);"
                "CREATE TABLE hit_events(node_id TEXT, speaker TEXT, kind TEXT, outcome TEXT,"
                " subtype TEXT, ts TEXT, domain TEXT, context_hash TEXT, decision_id TEXT);")

            def add(nid, ntype, sent, sub, used=0):
                con.execute(
                    "INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                    "created_at,semantic_subtype,use_count) VALUES(?,?,?,?,?,?,?,?,?)",
                    (nid, ntype, sent, 0, "active", "h", "2026-06-01T00:00:00Z", sub, used))
                con.execute("INSERT INTO evidence VALUES(?,?,?,?)",
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

        # T2 위험작업 → 상단 블록 주입(버그패턴 + 반문)
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "검증 없이 바로 배포하려고 한다 endpoint", "cwd": repo_cwd})
        out = r.stdout
        check(r.returncode == 0 and "빙구팩 preflight" in out
              and "하면 안 되는 과거 패턴" in out and "위험도" in out,
              "T2 위험작업 → 상단 블록 + 버그패턴 주입")
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
        r = call({"hook_event_name": "UserPromptSubmit",
                  "prompt": "검증 없이 바로 배포 endpoint", "cwd": repo_cwd})
        check("양방향 신뢰도" in r.stdout and "내 직감(owner) 적중률" in r.stdout,
              "T14 hit_events 표본 충분(N>=5) → 양방향 신뢰도 trust 블록 주입(회수 1단)")
        # T15 trust 조회도 read-only(sqlite mode=ro) — ledger mtime/size 불변
        mt0 = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        call({"hook_event_name": "UserPromptSubmit",
              "prompt": "검증 없이 배포 endpoint", "cwd": repo_cwd})
        mt1 = (ledger.stat().st_mtime_ns, ledger.stat().st_size)
        check(mt0 == mt1, "T15 trust(both_sides) 조회 후 ledger 불변(read-only · write 0)")

        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
