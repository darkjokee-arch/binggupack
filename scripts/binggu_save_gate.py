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

v1.12.0 S4-1: write/판정 4함수(gate_record·gate_human_for·write_last_preview·gate_record_from_prompt)
및 의존 helper(_resolve_home/gate_home/gate_path/last_preview_path/_gate_path/GATE_WINDOW_SEC/_load)
정본은 binggupack.safety.gate_log (byte-identical relocation·semantic change 0). 본 파일은 import/
re-export wrapper + 동일 정의 폴백으로 유지된다. has_trigger_token/TRIGGER_TOKENS 는 미이관(잔류).

save-n 참조 바인딩(스펙 ①·④): 승격 정본 = preview_ref(pref)+선택 idx 바인딩(gate_record_ref·
gate_human_for_ref·preview_ref_for_candidates/rows) — gate_log 정본을 re-export + 동일 정의 폴백.
폴백 gate_human_for 에 정본의 미래-ts(age<0) 거부 재동기(ride-along 수리 — 폴백 drift 봉인).
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
        parse_save_indices, SAVE_TRIGGER_RE, _norm, sent_hash, _strip_embedded_regions)
except Exception:  # pragma: no cover - 폴백(외부 의존 없이 동일 정의)
    SAVE_TRIGGER_RE = re.compile(
        r"\s*(?:SAVE|저장|세이브)\s*\d+(\s*[-~]\s*\d+)?(\s*,\s*\d+(\s*[-~]\s*\d+)?)*\s*",
        re.IGNORECASE)

    _RANGE_CAP = 50

    # 혼합 도장 줄(2026-07-24) — 정본 gate_text 와 byte-identical 폴백.
    _STAMP_SEG = (r"(?:SAVE|저장|세이브|HIT|히트|MISS|미스|PROMOTE|승격)\s*\d+(?:\s*[-~]\s*\d+)?"
                  r"(?:\s*,\s*\d+(?:\s*[-~]\s*\d+)?)*(?:\s*(?:무관|이미앎|약함|낡음|맥락|최신|틀림))?")
    _STAMP_LINE_RE = re.compile(r"\s*(?:%s\s*)+" % _STAMP_SEG, re.IGNORECASE)
    _SAVE_SEG_RE = re.compile(
        r"(?:SAVE|저장|세이브)\s*(\d+(?:\s*[-~]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-~]\s*\d+)?)*)",
        re.IGNORECASE)

    _FENCE_RE = re.compile(r"^\s*(?:```|~~~)")

    def _strip_embedded_regions(text):
        out, in_fence = [], False
        for line in str(text).splitlines():
            if _FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if line.lstrip().startswith(">"):
                continue
            out.append(line)
        return "\n".join(out)

    def _expand_indices(text):
        out, seen = [], set()
        for part in re.findall(r"\d+(?:\s*[-~]\s*\d+)?", str(text)):
            nums = [int(x) for x in re.findall(r"\d+", part)]
            if len(nums) == 2:
                lo, hi = min(nums), max(nums)
                if hi - lo + 1 > _RANGE_CAP:
                    return None
                span = range(lo, hi + 1)
            else:
                span = nums
            for i in span:
                if i not in seen:
                    seen.add(i)
                    out.append(i)
        return out or None

    # L-lane 토큰(2단계 절단) — 정본 gate_text 와 동일 정의. 기존 축 불변, allow_long 시에만 사용.
    _L_TOKEN = r"(?:\d+(?:\s*[-~]\s*\d+)?|[Ll]\d+)"
    SAVE_TRIGGER_L_RE = re.compile(
        r"\s*(?:SAVE|저장|세이브)\s*" + _L_TOKEN + r"(\s*,\s*" + _L_TOKEN + r")*\s*",
        re.IGNORECASE)

    def _expand_indices_long(text):
        out, seen = [], set()
        for part in re.findall(r"\d+(?:\s*[-~]\s*\d+)?|[Ll]\d+", str(text)):
            if part[:1] in ("L", "l"):
                key = "L" + part[1:]
                if key not in seen:
                    seen.add(key)
                    out.append(key)
                continue
            nums = [int(x) for x in re.findall(r"\d+", part)]
            if len(nums) == 2:
                lo, hi = min(nums), max(nums)
                if hi - lo + 1 > _RANGE_CAP:
                    return None
                span = range(lo, hi + 1)
            else:
                span = nums
            for i in span:
                if i not in seen:
                    seen.add(i)
                    out.append(i)
        return out or None

    def parse_save_indices(prompt, allow_long=False):
        p = str(prompt or "")
        trigger = SAVE_TRIGGER_L_RE if allow_long else SAVE_TRIGGER_RE
        expand = _expand_indices_long if allow_long else _expand_indices
        if trigger.fullmatch(p):
            return expand(p)
        out, seen = [], set()
        for line in _strip_embedded_regions(p).splitlines():
            ls = line.strip()
            if not ls:
                continue
            if trigger.fullmatch(ls):
                segs = [ls]
            elif _STAMP_LINE_RE.fullmatch(ls):
                segs = _SAVE_SEG_RE.findall(ls)   # 혼합 도장 줄 → SAVE 세그먼트만
            else:
                segs = []
            for seg in segs:
                for i in expand(seg) or []:
                    if i not in seen:
                        seen.add(i)
                        out.append(i)
        return out or None

    def _norm(s):
        return re.sub(r"\s+", " ", str(s)).strip()

    def sent_hash(s):
        return hashlib.sha256(_norm(s).encode("utf-8")).hexdigest()[:16]


# v1.12.0 S4-1: write/판정 정본은 binggupack.safety.gate_log.
#   import 실패(독립 실행 등) 시 동일 정의 폴백 — byte-identical(semantic change 0).
try:
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from binggupack.safety.gate_log import (  # noqa: E402,F401
        _resolve_home, gate_home, gate_path, last_preview_path, _gate_path,
        GATE_WINDOW_SEC, _load, gate_record, gate_human_for,
        write_last_preview, gate_record_from_prompt,
        _preview_rows, preview_ref_for_rows, preview_ref_for_candidates,
        gate_record_ref, _load_refs, gate_human_for_ref)
except Exception:  # pragma: no cover - 폴백(외부 의존 없이 동일 정의)
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
        하나라도 미기록/stale 이면 False(=기존 actor 게이트 유지).
        존치(strangler selftest·기존 seed 소비자) — 승격 정본은 gate_human_for_ref 로 이관."""
        now = now if now is not None else time.time()
        sents = [s for s in (sentences or []) if _norm(s)]
        if not sents:
            return False
        rec = _load(path, now)
        for s in sents:
            ts = rec.get(sent_hash(s))
            if ts is None:
                return False
            # P1-A TAE-P2-08: 미래 ts(now-ts<0)는 "영원히 fresh" 가 아니라 무효(clock 역행/future-date
            # 앵커로 TTL 우회 차단). freshness window 초과도 무효.
            age = now - ts
            if age < 0:
                return False
            if GATE_WINDOW_SEC and age > GATE_WINDOW_SEC:
                return False
        return True

    def _preview_rows(candidates):
        """capture_preview 후보 → [{"idx","sh"}] rows — write_last_preview/preview_ref 공유 단일 빌더."""
        return [{"idx": j + 1, "sh": sent_hash(c.get("sentence", ""))}
                for j, c in enumerate(candidates or []) if _norm(c.get("sentence", ""))]

    def preview_ref_for_rows(rows):
        """preview rows([{"idx","sh"}]) → 결정론적 preview_ref(sha256 of 'idx:sh' join, 16자).
        후보 집합+순서에서 파생 — raw text 정규화 이슈 원천 회피(CLI/MCP 어디서 재계산해도 동일값)."""
        joined = "\n".join("%s:%s" % (r.get("idx"), r.get("sh")) for r in (rows or []))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

    def preview_ref_for_candidates(candidates):
        """capture_preview 후보 리스트 → preview_ref (rows 재도출 후 preview_ref_for_rows)."""
        return preview_ref_for_rows(_preview_rows(candidates))

    def write_last_preview(candidates, path=None, explicit=False, session_id=None):
        """capture_preview 후보 → idx+sentence_hash 영속(원문 미저장, hash만). SAVE hook 이 대조용으로 읽음.
        매 preview 마다 덮어씀(직전 1건만 유효). pref=save-n 참조 바인딩용 preview_ref,
        explicit=후보 재도출 모드 — save/pair/core 재승격이 기록된 모드로 동일 재계산(pref 패리티).
        session_id=세션 경계 목록 재현 힌트(save-batch 저장이 동일 session_id 로 render_preview 필터해
        앵커·마무리 preview·저장 목록 축을 통일 — 이원화 오저장 방지). pref 계산 미포함이라
        하위호환·pref 패리티 불변(session_id 없으면 필드 자체 미기록 = 구 앵커와 byte 동일)."""
        path = path or last_preview_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = _preview_rows(candidates)
        payload = {"ts": time.time(), "pref": preview_ref_for_rows(rows),
                   "explicit": bool(explicit), "items": rows}
        if session_id is not None:
            payload["session_id"] = session_id
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)  # atomic
        return len(rows)

    def gate_record_ref(pref, idxs, ts=None, source="user_prompt", path=None):
        """save-n 참조 바인딩 레코드 1행 append — {"pref","idxs","ts","source"} (승격 정본).
        반환 기록 행 수(0=pref/idxs 없음). 파일 부재 시 생성."""
        idxs = [int(i) for i in (idxs or [])]
        if not pref or not idxs:
            return 0
        path = path or gate_path()
        ts = ts if ts is not None else time.time()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"pref": pref, "idxs": idxs, "ts": ts, "source": source},
                               ensure_ascii=False) + "\n")
        return 1

    def _load_refs(path=None, now=None):
        """기록장 → {(pref, idx): 최신ts}. ref 레코드({"pref","idxs"})만 적재 — 레거시 sh 행은 _load 몫."""
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
                pref, ts = d.get("pref"), d.get("ts", 0)
                if not pref:
                    continue
                for i in (d.get("idxs") or []):
                    k = (pref, i)
                    if k not in out or ts > out[k]:
                        out[k] = ts
        return out

    def gate_human_for_ref(pref, idxs, path=None, now=None):
        """요청 (pref, idxs) 전부가 사람 save-n 발화로 기록됐고 신선도 창 이내면 True — 승격 정본.
        하나라도 미기록/stale/미래ts(age<0 무효·clock 역행 TTL 우회 차단) 이면 False(fail-closed).
        같은 pref 의 복수 발화는 (pref,idx) 단위 합산 허용(각 idx 가 개별로 사람 선택됨)."""
        now = now if now is not None else time.time()
        idxs = [int(i) for i in (idxs or [])]
        if not pref or not idxs:
            return False
        rec = _load_refs(path, now)
        for i in idxs:
            ts = rec.get((pref, i))
            if ts is None:
                return False
            age = now - ts
            if age < 0:
                return False
            if GATE_WINDOW_SEC and age > GATE_WINDOW_SEC:
                return False
        return True

    def gate_record_from_prompt(prompt, preview_path=None, gate_path=None, ts=None):
        """SAVE hook 진입점 — 발화가 'SAVE n' 정확형이면 직전 preview 의 해당 idx 를 게이트에 기록.
        신형 preview(pref 有)는 ref 레코드 1행 + 레거시 sh 행 병기 append(구 소비자 무수정 호환),
        구형(pref 無)은 레거시 sh 행만(현행 동일 동작). 반환 레거시 기록 건수(0=SAVE 발화 아님/
        후보 없음/매칭 0). 원문 미접근(hash 만)."""
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
        matched = [i for i in idxs if by_idx.get(i)]
        hashes = [by_idx[i] for i in matched]
        if not hashes:
            return 0
        gp = gate_path or _gate_path()
        ts = ts if ts is not None else time.time()
        os.makedirs(os.path.dirname(gp), exist_ok=True)
        pref = pv.get("pref")
        with open(gp, "a", encoding="utf-8") as f:
            if pref:
                f.write(json.dumps({"pref": pref, "idxs": matched, "ts": ts,
                                    "source": "user_prompt"}, ensure_ascii=False) + "\n")
            for h in hashes:
                f.write(json.dumps({"sh": h, "ts": ts, "source": "user_prompt"}, ensure_ascii=False) + "\n")
        return len(hashes)


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
    # T2l~T2p ★줄 단위 도장 + 범위(2026-07-13 owner GO — 다지시 메시지 대응)
    chk("T2l 여러 지시 + 도장 줄", parse_save_indices("머지해. 조사도 해줘\n세이브 1\n고고") == [1])
    chk("T2m 문장 속 언급은 여전히 무시(줄 일부)",
        parse_save_indices("도장은 세이브1 - 이건 왜 실패하지?") is None)
    chk("T2n 범위 '세이브 1-3'", parse_save_indices("세이브 1-3") == [1, 2, 3])
    chk("T2o 범위+콤마 혼합 '저장 1,3-5'", parse_save_indices("저장 1,3-5") == [1, 3, 4, 5])
    chk("T2p 범위 폭주 무효(상한 50)", parse_save_indices("세이브 1-9999") is None)
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

    # T19~T26 save-n 참조 바인딩(ref) — 승격 정본. ts/now 명시 주입(wall-clock 경과 가정 0).
    gp4 = os.path.join(work, "g4.jsonl")
    pp2 = os.path.join(work, "lp2.json")
    write_last_preview([{"sentence": SA}, {"sentence": SB}], path=pp2)
    with open(pp2, encoding="utf-8") as f:
        pv2 = json.load(f)
    pref = pv2.get("pref")
    chk("T19 preview pref == 후보 재계산 ref(결정론)",
        bool(pref) and pref == preview_ref_for_candidates([{"sentence": SA}, {"sentence": SB}])
        and pv2.get("explicit") is False)
    base = 1_700_000_000.0
    chk("T20 'SAVE 1,2' → 레거시 2건(이중기록 반환값 유지)",
        gate_record_from_prompt("SAVE 1,2", preview_path=pp2, gate_path=gp4, ts=base) == 2)
    chk("T20b ref 대조 통과(기록→대조)", gate_human_for_ref(pref, [1, 2], path=gp4, now=base + 10) is True)
    chk("T20c 레거시 sh 병기(구 소비자 호환)", gate_human_for([SA, SB], path=gp4, now=base + 10) is True)
    chk("T21 타 pref 불일치 → False", gate_human_for_ref("0" * 16, [1], path=gp4, now=base + 10) is False)
    chk("T22 idx subset 통과 / superset 차단",
        gate_human_for_ref(pref, [1], path=gp4, now=base + 10) is True
        and gate_human_for_ref(pref, [1, 2, 3], path=gp4, now=base + 10) is False)
    chk("T23 stale(창 밖) → False",
        gate_human_for_ref(pref, [1], path=gp4, now=base + GATE_WINDOW_SEC + 100) is False)
    chk("T24 미래 ts → False(ref·레거시 폴백 재동기)",
        gate_human_for_ref(pref, [1], path=gp4, now=base - 100) is False
        and gate_human_for([SA], path=gp4, now=base - 100) is False)
    # T25 구형 preview(pref 없음) → 레거시 sh 행만(ref 0행·무해)
    pp3 = os.path.join(work, "lp3.json")
    with open(pp3, "w", encoding="utf-8") as f:
        json.dump({"ts": base, "items": [{"idx": 1, "sh": sent_hash(SA)}]}, f, ensure_ascii=False)
    gp5 = os.path.join(work, "g5.jsonl")
    chk("T25 구형 preview → 레거시 1건",
        gate_record_from_prompt("SAVE 1", preview_path=pp3, gate_path=gp5, ts=base) == 1)
    chk("T25b ref 레코드 0(구형 무해)", _load_refs(path=gp5) == {}
        and gate_human_for([SA], path=gp5, now=base + 10) is True)
    # T26 gate_record_ref 직접 기록 → 대조
    gp6 = os.path.join(work, "g6.jsonl")
    chk("T26 gate_record_ref 직접 기록 → 대조 통과",
        gate_record_ref(pref, [1, 3], ts=base, path=gp6) == 1
        and gate_human_for_ref(pref, [1, 3], path=gp6, now=base + 10) is True
        and gate_human_for_ref(pref, [2], path=gp6, now=base + 10) is False)

    print(f"\nRESULT: {ok}/{tot} " + ("PASS" if ok == tot else "FAIL"))
    print("GATE: " + ("GO" if ok == tot else "BLOCK"))
    return ok == tot


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_save_gate — use --selftest")
