# -*- coding: utf-8 -*-
"""binggu_save_gate — 사람-발화 저장 게이트 기록장 (0-A 해법).

핵심(4cli debate 20260616_0938_save_hook_gate, REFINE 합의):
- 토큰 발급/소각/TTL 메커니즘 없음(과엔지니어링·레이스 함정 회피, C·D 합의).
- UserPromptSubmit hook(사장님 키보드 원문 발화)이 "SAVE n" 감지 시 그 후보 문장 hash를
  append-only 기록장에 남긴다(gate_record). AI(claude)는 UserPromptSubmit를 못 거침 → 위조 불가.
- 저장 시점(save_selected)이 gate_human_for로 "저장할 문장이 사람 SAVE 발화로 기록됐나" 대조.
  있으면 actor=human 승격. 없으면 기존대로 G4_no_auto BLOCK.
- 대상 정확성은 기존 preview_id·confirm 게이트가, 진위는 이 기록장이, 자동적재 차단은 "기록 없으면 block"이 담당.

한계(정직 — 6/16 하이브리드 결론과 동일): AI가 로컬 사용자 권한으로 이 기록장 파일을 직접 write 하는
극단은 못 막음 = 사람 자기규율 + 사후감사. 일상 위조·자동적재(영구금지25)는 구조적으로 차단.

기록장은 append-only(사후감사 자료). 소각 0. 운영 ledger 와 별개 파일.
"""
import hashlib
import json
import os
import re
import sys
import time

# 단일 home resolver 경유 — ledger scope 와 gate scope 가 갈라지지 않도록(split-brain 차단).
#   binggu_platform.binggu_home() 가 BINGGU_HOME(opt-in)/OS별 홈을 단일 원천으로 해석한다.
#   import 가 안 되면(독립 실행 등) 동일 정책의 _resolve_home() 폴백으로 byte-identical 산출.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
try:
    sys.path.insert(0, _HERE)
    import binggu_platform as _plat  # noqa: E402
except Exception:  # pragma: no cover - 폴백(외부 의존 없이 동일 경로)
    _plat = None

# v1.11.0 S3-B: 순수 파싱 helper 정본은 binggupack.safety.gate_text (게이트 로직 무관).
#   import 실패(독립 실행 등) 시 동일 정의 폴백 — byte-identical.
try:
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from binggupack.safety.gate_text import (  # noqa: E402,F401
        parse_save_indices, SAVE_TRIGGER_RE, _norm, sent_hash)
except Exception:  # pragma: no cover - 폴백(외부 의존 없이 동일 정의)
    SAVE_TRIGGER_RE = re.compile(
        r"\s*(?:SAVE|저장|세이브)\s*\d+(\s*,\s*\d+)*\s*", re.IGNORECASE)

    def parse_save_indices(prompt):
        if not SAVE_TRIGGER_RE.fullmatch(str(prompt or "")):
            return None
        return [int(x) for x in re.findall(r"\d+", prompt)]

    def _norm(s):
        return re.sub(r"\s+", " ", str(s)).strip()

    def sent_hash(s):
        return hashlib.sha256(_norm(s).encode("utf-8")).hexdigest()[:16]


def _resolve_home():
    """장부 루트 = home 단일 resolver. binggu_platform.binggu_home() 우선,
    부재 시 동일 정책(BINGGU_HOME 우선 → ~/.binggupack)으로 폴백 — byte-identical."""
    if _plat is not None:
        return _plat.binggu_home()
    return os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")


def gate_home():
    """게이트/ledger 가 공유하는 단일 home(호출 시점 lazy)."""
    return _resolve_home()


def gate_path():
    """save_gate 기록장 경로(호출 시점 lazy, 단일 home 경유)."""
    return os.path.join(gate_home(), "save_gate_log.jsonl")


def last_preview_path():
    """직전 preview 후보 idx+hash 영속 경로(호출 시점 lazy, 단일 home 경유).
    capture_preview 가 쓰고, SAVE hook 이 'SAVE n' 대조용으로 읽음(원문 미저장)."""
    return os.path.join(gate_home(), "last_preview_candidates.json")


# gate_record_from_prompt 의 파라미터명 'gate_path'(하위호환·selftest 키워드) 가 동명 함수를
#   가리는 것을 피하기 위한 내부 별칭. 함수 본문에서 이 별칭으로 기본 경로를 해석한다.
_gate_path = gate_path


# 대조 유효 창(초). 옛 발화로 한참 뒤 자동저장하는 것 방지 — 존재+신선도 이중. 0이면 무한(존재만).
GATE_WINDOW_SEC = int(os.environ.get("BINGGU_SAVE_GATE_WINDOW", "3600"))  # 기본 1시간

# 하위호환(PEP 562): 다른 모듈이 'from binggu_save_gate import GATE_PATH' / SGATE.GATE_PATH 로
#   모듈 속성을 읽어도 계속 동작하도록 lazy 제공. 매 접근마다 현재 home 으로 재계산(상수 아님).
_LAZY_ATTRS = {
    "GATE_PATH": gate_path,
    "LAST_PREVIEW_PATH": last_preview_path,
    "_HOME": gate_home,
}


def __getattr__(name):  # noqa: D401 - PEP 562 모듈 레벨 lazy 속성
    fn = _LAZY_ATTRS.get(name)
    if fn is not None:
        return fn()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


# _norm / sent_hash 정본은 binggupack.safety.gate_text (상단 import·S3-C 이관). 게이트 로직 무관.


# 사람-발화 저장 트리거 토큰 — 영문 'SAVE' 외 한글 '저장'/'세이브' 도 동등 인정.
#   SAVE_TRIGGER_RE / parse_save_indices 정본은 binggupack.safety.gate_text (상단 import·S3-B 이관).
# hook 의 빠른 차단(모듈 로드 전 substring 체크)용 토큰. upper() 비교는 한글에 영향 0.
TRIGGER_TOKENS = ("SAVE", "저장", "세이브")


def has_trigger_token(prompt):
    """발화에 트리거 토큰(영문/한글)이 substring 으로라도 있나 — hook 빠른 차단용(정밀 판정 아님)."""
    up = str(prompt or "").upper()
    return any(t.upper() in up for t in TRIGGER_TOKENS)


def gate_record(sentences, source="user_prompt", ts=None, path=None):
    """사람 SAVE 발화 시 hook 이 호출 — 선택 후보 문장 hash 를 append-only 기록.
    반환 기록 건수. 파일 부재 시 생성."""
    path = path or gate_path()
    ts = ts if ts is not None else time.time()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for s in sentences:
            if not _norm(s):
                continue
            f.write(json.dumps({"sh": sent_hash(s), "ts": ts, "source": source}, ensure_ascii=False) + "\n")
            n += 1
    return n


def _load(path=None, now=None):
    """기록장 → {sh: 최신ts}. 창 밖(stale) 제외는 대조 시 판단."""
    path = path or gate_path()
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            sh, ts = d.get("sh"), d.get("ts", 0)
            if sh and (sh not in out or ts > out[sh]):
                out[sh] = ts
    return out


def gate_human_for(sentences, path=None, now=None):
    """저장할 문장들이 모두 사람 SAVE 발화로 기록됐고(신선도 창 이내) 비어있지 않으면 True.
    하나라도 미기록/stale 이면 False(=기존 actor 게이트 유지)."""
    now = now if now is not None else time.time()
    sents = [s for s in (sentences or []) if _norm(s)]
    if not sents:
        return False
    rec = _load(path, now)
    for s in sents:
        ts = rec.get(sent_hash(s))
        if ts is None:
            return False
        if GATE_WINDOW_SEC and (now - ts) > GATE_WINDOW_SEC:
            return False
    return True


def write_last_preview(candidates, path=None):
    """capture_preview 후보 → idx+sentence_hash 영속(원문 미저장, hash만). SAVE hook 이 대조용으로 읽음.
    매 preview 마다 덮어씀(직전 1건만 유효)."""
    path = path or last_preview_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = [{"idx": j + 1, "sh": sent_hash(c.get("sentence", ""))}
            for j, c in enumerate(candidates or []) if _norm(c.get("sentence", ""))]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "items": rows}, f, ensure_ascii=False)
    os.replace(tmp, path)  # atomic
    return len(rows)


def gate_record_from_prompt(prompt, preview_path=None, gate_path=None, ts=None):
    """SAVE hook 진입점 — 발화가 'SAVE n' 정확형이면 직전 preview 의 해당 idx hash 를 게이트에 기록.
    반환 기록 건수(0=SAVE 발화 아님/후보 없음/매칭 0). 원문 미접근(hash 만)."""
    idxs = parse_save_indices(prompt)
    if not idxs:
        return 0
    pp = preview_path or last_preview_path()
    if not os.path.exists(pp):
        return 0
    try:
        with open(pp, "r", encoding="utf-8") as f:
            pv = json.load(f)
    except Exception:
        return 0
    by_idx = {r.get("idx"): r.get("sh") for r in pv.get("items", [])}
    hashes = [by_idx[i] for i in idxs if by_idx.get(i)]
    if not hashes:
        return 0
    gp = gate_path or _gate_path()
    ts = ts if ts is not None else time.time()
    os.makedirs(os.path.dirname(gp), exist_ok=True)
    with open(gp, "a", encoding="utf-8") as f:
        for h in hashes:
            f.write(json.dumps({"sh": h, "ts": ts, "source": "user_prompt"}, ensure_ascii=False) + "\n")
    return len(hashes)


# ---------------- 셀프테스트 (temp 기록장 — 운영 기록장/ledger 미접촉) ----------------
def _selftest():
    import tempfile
    ok = 0
    tot = 0

    def chk(name, cond):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("  PASS " if cond else "  FAIL ") + name)

    work = tempfile.mkdtemp(prefix="gate_st_")
    gp = os.path.join(work, "g.jsonl")
    S1 = "사람이 명시한 첫 문장이다"
    S2 = "사람이 고르지 않은 다른 문장"

    # T1 빈 기록장 → 어떤 문장도 불통과
    chk("T1 빈 기록장 → False", gate_human_for([S1], path=gp) is False)
    # T2 SAVE 발화 파싱 정확형만
    chk("T2a 'SAVE 1' 파싱", parse_save_indices("SAVE 1") == [1])
    chk("T2b 'SAVE 1,3' 파싱", parse_save_indices("SAVE 1,3") == [1, 3])
    chk("T2c 딴얘기 속 'save 7' 무시", parse_save_indices("아 그거 save 7 말이야") is None)
    chk("T2d 부정문 무시", parse_save_indices("SAVE 안 해") is None)
    # T2e~T2h 한글 트리거 확장(결함수정 6/16) — '저장 n'·'세이브 n' 도 SAVE 와 동등
    chk("T2e '저장 1' 파싱", parse_save_indices("저장 1") == [1])
    chk("T2f '저장 1,3' 파싱", parse_save_indices("저장 1,3") == [1, 3])
    chk("T2g '세이브 2' 파싱", parse_save_indices("세이브 2") == [2])
    chk("T2h 소문자 'save 1' 파싱", parse_save_indices("save 1") == [1])
    chk("T2i 한글 부정문 무시", parse_save_indices("저장 하지마") is None)
    chk("T2j 딴얘기 속 '저장 7' 무시", parse_save_indices("그거 저장 7 어쩌고") is None)
    chk("T2k has_trigger_token 한글 감지", has_trigger_token("저장 1") and has_trigger_token("세이브 2"))
    # T3 기록 후 통과
    gate_record([S1], path=gp)
    chk("T3 기록된 문장 → True", gate_human_for([S1], path=gp) is True)
    # T4 기록 안 된 문장은 여전히 False
    chk("T4 미기록 문장 → False", gate_human_for([S2], path=gp) is False)
    # T5 일부만 기록 → 전체 False(all 요구)
    chk("T5 [기록,미기록] → False", gate_human_for([S1, S2], path=gp) is False)
    # T6 신선도 창 밖(stale) → False
    gp2 = os.path.join(work, "g2.jsonl")
    gate_record([S1], path=gp2, ts=time.time() - (GATE_WINDOW_SEC + 100))
    chk("T6 stale 기록 → False", gate_human_for([S1], path=gp2) is False)
    # T7 append-only(소각 없음) — 재대조도 True
    chk("T7 재대조 여전히 True(append-only)", gate_human_for([S1], path=gp) is True)
    # T8 빈/공백 문장 방어
    chk("T8 빈 문장 → False", gate_human_for(["   "], path=gp) is False)

    # T9~T13 preview 영속 + SAVE 발화 → 게이트 기록 end-to-end (hash만, 원문 미저장)
    gp3 = os.path.join(work, "g3.jsonl")
    pp = os.path.join(work, "lp.json")
    SA, SB = "선택된 후보 문장 가나다", "선택 안 된 후보 문장 라마바"
    write_last_preview([{"sentence": SA}, {"sentence": SB}], path=pp)
    raw = open(pp, encoding="utf-8").read()
    chk("T9 preview 파일 원문 미포함(hash만)", (SA not in raw) and (SB not in raw))
    chk("T10 'SAVE 1' → 1건 기록", gate_record_from_prompt("SAVE 1", preview_path=pp, gate_path=gp3) == 1)
    chk("T11 기록된 SA → True", gate_human_for([SA], path=gp3) is True)
    chk("T12 미선택 SB → False", gate_human_for([SB], path=gp3) is False)
    chk("T13 비SAVE 발화 → 기록 0", gate_record_from_prompt("그냥 잡담임", preview_path=pp, gate_path=gp3) == 0)

    # T14~T17 게이트 단일화 회귀 — gate_path()/last_preview_path() 가 home 단일 resolver 를 경유.
    #   같은 BINGGU_HOME → gate scope 와 ledger scope 가 같은 디렉토리(split-brain 차단 불변).
    h0 = os.environ.get("BINGGU_HOME")
    try:
        fake = os.path.join(work, "home_iso")
        os.environ["BINGGU_HOME"] = fake
        # gate_home() == 단일 resolver(binggu_platform.binggu_home or 폴백) 산출과 동일
        chk("T14 gate_home == 단일 resolver", gate_home() == _resolve_home())
        # gate_path/last_preview_path 가 동일 home(gate_home) 아래
        chk("T15 gate_path 가 gate_home 하위", os.path.dirname(gate_path()) == gate_home())
        chk("T16 last_preview_path 가 gate_home 하위", os.path.dirname(last_preview_path()) == gate_home())
        # ledger scope(가능하면 binggu_platform) 와 gate scope 가 같은 디렉토리
        if _plat is not None:
            same = os.path.dirname(gate_path()) == os.path.dirname(_plat.default_ledger())
            chk("T17 gate scope == ledger scope(같은 home)", same)
        else:
            chk("T17 (platform 폴백) gate_home==BINGGU_HOME", gate_home() == fake)
        # BINGGU_HOME 단독 동작 byte-identical 보존(현재 정책 = explicit 그대로)
        chk("T18 BINGGU_HOME 단독 → 그 경로", gate_home() == fake)
    finally:
        if h0 is None:
            os.environ.pop("BINGGU_HOME", None)
        else:
            os.environ["BINGGU_HOME"] = h0

    print(f"\nRESULT: {ok}/{tot} " + ("PASS" if ok == tot else "FAIL"))
    print("GATE: " + ("GO" if ok == tot else "BLOCK"))
    return ok == tot


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_save_gate — use --selftest")
