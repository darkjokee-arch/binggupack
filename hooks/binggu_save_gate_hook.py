#!/usr/bin/env python
"""BingguPack 사람-발화 저장 게이트 hook — UserPromptSubmit (sync).

사장님이 'SAVE n'(정확형)을 키보드로 입력하면, 직전 preview 후보의 hash 를 게이트 기록장
(~/.binggupack/save_gate_log.jsonl)에 남긴다. 이 기록이 있어야 save_selected 가 actor 를
human 으로 승격한다(0-A 해법, 4cli debate 20260616_0938 REFINE 합의).

intel loop 확장: 같은 발화 경로로 회수 히트/미스('히트 2'/'미스 2')·승격('승격 1') 스탬프도
기록한다(binggupack.safety.gate_log.stamp_record_from_prompt — 기록 파일은 save 와 동일한
save_gate_log.jsonl, ref 도메인 접두로 네임스페이스 분리). 분기 순서: ① SAVE(기존 불변) →
② 스탬프. 트리거 토큰 계열이 서로소 + 파서가 줄단위 fullmatch 라 같은 줄이 두 분기에 이중
기록될 일 없고, 각 분기 독립 try 로 한쪽 실패가 다른쪽을 막지 않는다.

설계 불변:
  - sync 등록 의무: 토큰/기록이 저장 호출보다 먼저 완료돼야 함(async 레이스 회피, B 지적).
  - capture_enabled(자동수집)와 무관 — 저장 게이트는 별개 축(B 지적2). 자동수집 OFF 여도 작동.
  - AI(claude)는 UserPromptSubmit 이벤트를 못 거침 — 도장 강도는 이 hook 기록 + gate 파일
    존재/신선도 의존(로컬 사용자 권한 프로세스의 직접 write 극단은 못 막음 = 자기규율+사후감사,
    CLAUDECODE env 는 소프트 신호). 평소(도장 발화 0) 기록 0 = 자동적재 차단.
  - 원문 미접근: 직전 preview/staging 의 hash·node_id 만 기록.
  - stdout 침묵 · 항상 exit 0 · 예외 전부 흡수(세션 무방해).
"""
import json
import os
import re
import sys
from pathlib import Path

# 스탬프 빠른 차단(프리필터) — '<트리거>\s*숫자' 형태일 때만 게이트 모듈 로드. 단순 substring 검사는
# 영문 오탐("arcHITecture" 등)으로 비트리거 프롬프트마다 로드를 유발하므로 숫자 인접 조건까지 요구.
# 휴리스틱일 뿐 — 정밀 판정(fullmatch·줄단위)은 gate_log 파서 몫이라 오기록 0(로드 비용만).
_STAMP_FAST_RE = re.compile(r"(?:HIT|히트|MISS|미스|PROMOTE|승격)\s*\d", re.IGNORECASE)


def _scripts_dir():
    env = os.environ.get("BINGGU_SCRIPTS")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "scripts"


def _gate_log_module(scripts_dir):
    """스탬프 정본(binggupack.safety.gate_log) import — 설치본(site-packages) 우선,
    repo 배치(scripts 부모=root)면 root 를 sys.path 에 추가 후 재시도. 실패 시 None(무해)."""
    try:
        from binggupack.safety import gate_log
        return gate_log
    except Exception:
        pass
    try:
        root = str(Path(scripts_dir).resolve().parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from binggupack.safety import gate_log
        return gate_log
    except Exception:
        return None


def _run(data):
    try:
        if (data.get("hook_event_name") or "") != "UserPromptSubmit":
            return
        prompt = data.get("prompt", "")
        # 빠른 차단 — 트리거 없으면 모듈 로드도 안 함(비트리거 프롬프트 성능 유지).
        #   SAVE 계열 = 기존 substring 검사 그대로(결함수정 6/16 한글 토큰 포함·동작 불변).
        up = prompt.upper()
        has_save = any(t in up for t in ("SAVE", "저장", "세이브"))
        has_stamp = bool(_STAMP_FAST_RE.search(prompt))
        if not (has_save or has_stamp):
            return
        sd = str(_scripts_dir())
        # 분기 ① SAVE 도장(기존 경로·동작 불변)
        if has_save:
            try:
                if sd not in sys.path:
                    sys.path.insert(0, sd)
                import binggu_save_gate as sgate
                sgate.gate_record_from_prompt(prompt)
            except Exception:
                pass
        # 분기 ② 히트/미스/승격 스탬프 — 같은 save_gate_log.jsonl 에 도메인 접두 ref 로 기록
        if has_stamp:
            try:
                gl = _gate_log_module(sd)
                if gl is not None:
                    gl.stamp_record_from_prompt(prompt)
            except Exception:
                pass
    except Exception:
        return


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    _run(data)
    return 0  # 항상 0 · stdout 침묵


# ---------------- 셀프테스트 (subprocess end-to-end, temp home 전용) ----------------
def _selftest():
    import shutil
    import subprocess
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    tmp = Path(tempfile.mkdtemp(prefix="bgp_save_gate_hook_"))
    try:
        home = tmp / ".binggupack"
        home.mkdir(parents=True)
        scripts = str(_scripts_dir())
        self_path = str(Path(__file__).resolve())
        base_env = {**os.environ, "BINGGU_HOME": str(home),
                    "BINGGU_SCRIPTS": scripts, "PYTHONUTF8": "1"}

        def call(payload, raw=None):
            return subprocess.run(
                [sys.executable, self_path],
                input=(raw if raw is not None else json.dumps(payload)),
                capture_output=True, text=True, env=base_env)

        sys.path.insert(0, scripts)
        import binggu_save_gate as sgate
        # 직전 preview 후보 영속(원문 미저장)
        SA, SB = "선택될 후보 문장 가나다라", "선택 안 될 후보 마바사"
        sgate.write_last_preview([{"sentence": SA}, {"sentence": SB}],
                                 path=str(home / "last_preview_candidates.json"))

        gate_log = home / "save_gate_log.jsonl"

        # T1 비SAVE 발화 → 기록 0 · stdout 침묵
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "그냥 잡담이야", "cwd": "x"})
        check(r.returncode == 0 and r.stdout.strip() == "" and not gate_log.exists(),
              "T1 비SAVE 발화 → 기록 0 · stdout 침묵")

        # T2 'SAVE 1' 발화 → idx1(SA) 기록
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "SAVE 1", "cwd": "x"})
        rec_ok = gate_log.exists() and (sgate.sent_hash(SA) in gate_log.read_text(encoding="utf-8"))
        check(r.returncode == 0 and r.stdout.strip() == "" and rec_ok,
              "T2 'SAVE 1' → SA hash 기록 · stdout 침묵")

        # T3 gate_human_for(SA) True / SB False
        check(sgate.gate_human_for([SA], path=str(gate_log)) is True
              and sgate.gate_human_for([SB], path=str(gate_log)) is False,
              "T3 SA→통과 / SB→차단")

        # T3b 한글 '저장 2' 발화 → idx2(SB) 기록 (결함수정 6/16 end-to-end)
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "저장 2", "cwd": "x"})
        hangul_ok = (r.returncode == 0 and r.stdout.strip() == ""
                     and sgate.gate_human_for([SB], path=str(gate_log)) is True)
        check(hangul_ok, "T3b 한글 '저장 2' → SB hash 기록(한글 트리거)")

        # T3c 한글 '세이브 1' 도 동작 (이미 SA 기록됨 → 재대조 True 유지)
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "세이브 1", "cwd": "x"})
        check(r.returncode == 0 and sgate.gate_human_for([SA], path=str(gate_log)) is True,
              "T3c 한글 '세이브 1' → exit 0 · SA 통과 유지")

        # T4 Stop 이벤트는 무시(발급 안 함)
        before = gate_log.read_text(encoding="utf-8")
        call({"hook_event_name": "Stop"})
        check(gate_log.read_text(encoding="utf-8") == before, "T4 Stop 이벤트 무시(기록 불변)")

        # T5 깨진/빈 stdin 방어
        check(call(None, raw="{ broken").returncode == 0, "T5 깨진 stdin → exit 0")
        check(call(None, raw="").returncode == 0, "T6 빈 stdin → exit 0")

        # T7 원문 미저장(preview 파일에 원문 없음)
        lp = (home / "last_preview_candidates.json").read_text(encoding="utf-8")
        check((SA not in lp) and (SB not in lp), "T7 preview 영속 원문 미포함(hash만)")

        # T8 save-n 참조 바인딩 — 'SAVE 1' 발화가 ref 레코드(pref+idx)도 병기 append
        pref = sgate.preview_ref_for_candidates([{"sentence": SA}, {"sentence": SB}])
        check(sgate.gate_human_for_ref(pref, [1], path=str(gate_log)) is True
              and sgate.gate_human_for_ref(pref, [1, 2], path=str(gate_log)) is True
              and sgate.gate_human_for_ref("0" * 16, [1], path=str(gate_log)) is False,
              "T8 ref 레코드 병기 → gate_human_for_ref 통과(타 pref 차단)")

        # T9 구형 preview(pref 없음) → 레거시 sh 행만 기록(ref 불변·무해)
        SL = "구형 전용 문장 아자차"
        (home / "last_preview_candidates.json").write_text(json.dumps(
            {"ts": 0, "items": [{"idx": 1, "sh": sgate.sent_hash(SL)}]},
            ensure_ascii=False), encoding="utf-8")
        before_refs = dict(sgate._load_refs(path=str(gate_log)))
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "SAVE 1", "cwd": "x"})
        check(r.returncode == 0 and r.stdout.strip() == ""
              and sgate._load_refs(path=str(gate_log)) == before_refs
              and sgate.gate_human_for([SL], path=str(gate_log)) is True,
              "T9 구형 preview 무해(레거시만 기록·ref 불변)")

        # ---- T10~T15 intel loop 스탬프(히트/미스/승격) end-to-end ----
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from binggupack.safety import gate_log as gl
        rp = home / "last_recall_candidates.json"
        gl.write_last_recall(["node-aaaa1111", "node-bbbb2222"], query="테스트 질의",
                             domain="build", surface="cli", path=str(rp))
        rows = gl.load_last_recall(str(rp))["items"]

        # T10 '히트 1' 발화 → recall 스탬프 기록(같은 save_gate_log.jsonl)
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "히트 1", "cwd": "x"})
        check(r.returncode == 0 and r.stdout.strip() == ""
              and gl.recall_stamp_verdicts(rows, path=str(gate_log)) == {1: "hit"},
              "T10 '히트 1' → hit 스탬프 기록 · stdout 침묵")

        # T11 '미스 2' 추가 → verdict 병존 + all-or-nothing 대조
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "미스 2", "cwd": "x"})
        vd = gl.recall_stamp_verdicts(rows, path=str(gate_log))
        check(r.returncode == 0 and vd == {1: "hit", 2: "miss"}
              and gl.gate_human_for_recall(rows, [2], "miss", path=str(gate_log)) is True
              and gl.gate_human_for_recall(rows, [2], "hit", path=str(gate_log)) is False,
              "T11 '미스 2' → {1:hit, 2:miss} · verdict 대조 fail-closed")

        # T12 문장 속 언급은 무시(줄 일부 — fullmatch 아님)
        before = gate_log.read_text(encoding="utf-8")
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "그거 히트 3 어쩌고 승격 9 얘기", "cwd": "x"})
        check(r.returncode == 0 and gate_log.read_text(encoding="utf-8") == before,
              "T12 문장 속 '히트 3'/'승격 9' 무시(기록 불변)")

        # T13 승격 스탬프 — staging idx 밖(2)은 미기록·promote 대조 fail-closed
        pp_pr = home / "last_promote_candidates.json"
        gl.write_last_promote([{"node_id": "node-cccc3333", "claim": "승격 후보 주장"}], path=str(pp_pr))
        prows = gl.load_last_promote(str(pp_pr))["items"]
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "승격 1", "cwd": "x"})
        check(r.returncode == 0
              and gl.gate_human_for_promote(prows, [1], path=str(gate_log)) is True
              and gl.gate_human_for_promote(prows, [1, 2], path=str(gate_log)) is False,
              "T13 '승격 1' → promote 도장 통과 / 미도장 idx 포함 시 차단")

        # T14 MF5 staging 변조 → 재계산 ref mismatch(hit/miss·promote 모두 차단)
        gl.write_last_recall(["node-EVIL9999", "node-bbbb2222"], query="변조", path=str(rp))
        rows2 = gl.load_last_recall(str(rp))["items"]
        gl.write_last_promote(["node-EVIL8888"], path=str(pp_pr))
        prows2 = gl.load_last_promote(str(pp_pr))["items"]
        check(gl.recall_stamp_verdicts(rows2, path=str(gate_log)) == {}
              and gl.gate_human_for_promote(prows2, [1], path=str(gate_log)) is False,
              "T14 staging 변조 → ref mismatch(스탬프 전부 무효)")

        # T15 consumed 마킹(MF12 최소) — 기록 1행·기존 판정 무영향(반영은 후속)
        ref = gl.recall_gate_ref(rows)
        check(gl.stamp_mark_consumed(ref, [1], path=str(gate_log)) == 1
              and gl._load_consumed(path=str(gate_log)).get((ref, 1)) is not None
              and gl.recall_stamp_verdicts(rows, path=str(gate_log)) == {1: "hit", 2: "miss"},
              "T15 consumed 마킹 append · 판정 무영향(최소 마킹만)")

        # T16 파서 정확형 계약(unit) — positive/negative/상한/재도장 나중 승리
        check(gl.parse_hit_stamps("히트 2") == {"hit": [2], "miss": []}
              and gl.parse_hit_stamps("MISS 1,3-4") == {"hit": [], "miss": [1, 3, 4]}
              and gl.parse_promote_indices("승격 1,3-5") == [1, 3, 4, 5],
              "T16a 정확형 positive('히트 2'/'MISS 1,3-4'/'승격 1,3-5')")
        check(gl.parse_hit_stamps("히트다") is None
              and gl.parse_hit_stamps("히트 2 아니야?") is None
              and gl.parse_promote_indices("승격이 필요한 3가지") is None,
              "T16b 비정확형 negative('히트다'/'히트 2 아니야?'/문장 속 승격)")
        check(gl.parse_hit_stamps("히트 1-51") is None
              and len(gl.parse_hit_stamps("히트 1-50")["hit"]) == 50
              and gl.parse_promote_indices("승격 1-9999") is None,
              "T16c 범위 상한 50(1-50 허용·1-51/1-9999 무효)")
        check(gl.parse_hit_stamps("히트 2\n미스 2") == {"hit": [], "miss": [2]},
              "T16d 같은 idx 재도장 → 나중 줄 승리(정정 허용)")

        # T17 비정확형 발화 e2e — hook 이 기록 0(gate 파일 불변)
        before = gate_log.read_text(encoding="utf-8")
        for _p in ("히트다", "히트 2 아니야?", "히트 1-51"):
            r = call({"hook_event_name": "UserPromptSubmit", "prompt": _p, "cwd": "x"})
            check(r.returncode == 0 and r.stdout.strip() == "", "T17 '%s' → exit 0 · 침묵" % _p)
        check(gate_log.read_text(encoding="utf-8") == before,
              "T17e 비정확형 3종 후 gate 기록 불변(오도장 0)")

        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
