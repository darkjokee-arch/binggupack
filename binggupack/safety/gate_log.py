# -*- coding: utf-8 -*-
"""binggupack.safety.gate_log — save_gate write/판정 함수 정본 (S4-1).

v1.12.0 save-gate 라인 S4-1: scripts/binggu_save_gate.py 의 사람-발화 게이트
write/판정 4함수(gate_record · gate_human_for · write_last_preview · gate_record_from_prompt)
및 그 의존 helper(_resolve_home/gate_home/gate_path/last_preview_path/_gate_path/GATE_WINDOW_SEC/_load)
를 이 모듈로 byte-identical relocation 했다(semantic change 0). scripts 파일은 backward-compatible
import/re-export wrapper + 동일 정의 폴백으로 유지된다(공개 심볼/문자열/반환/예외/skip/write 방식 무변).

actual write core(staging_apply/save_selected/commit_selected) 미접촉. G4_no_auto 3중·actor/confirm/token
흐름과 무관(이 모듈은 gate-log append + 사람 발화 판정 read 만 — 운영 ledger 와 별개 파일).

home resolver 는 binggupack.workspace.platform(정본) 을 경유한다. import 실패(독립 실행 등) 시
동일 정책(BINGGU_HOME 우선 → ~/.binggupack)의 _resolve_home() 폴백으로 byte-identical 산출.

save-n 참조 바인딩(스펙 ①·④): 사람 증명의 승격 정본은 preview_ref(pref)+선택 idx 바인딩
(gate_record_ref · gate_human_for_ref). write_last_preview 가 pref/explicit 를 영속하고,
gate_record_from_prompt 는 ref 레코드 1행 + 레거시 sh 행 병기 append(구 소비자 무수정 호환).
문장 hash 판정(gate_record · gate_human_for)은 존치(기존 seed 소비자·autopush) — 승격 정본 아님.

intel loop 스탬프 패밀리(하단 절): 같은 도장 규약을 회수 히트/미스·승격(promote) 스탬프로 일반화
— 기록/판독 파일은 save 와 동일한 save_gate_log.jsonl 단일 파일, ref 해시 입력의 도메인 접두
("recall|"/"promote|")로 네임스페이스 분리. 상세 확정은 해당 절 헤더 주석.
"""
import hashlib
import json
import os
import re
import time

# 단일 home resolver — binggupack.workspace.platform(정본·S1). 부재 시 None 폴백(동일 정책 산출).
try:
    from binggupack.workspace import platform as _plat
except Exception:  # pragma: no cover - 폴백(외부 의존 없이 동일 경로)
    _plat = None  # type: ignore[assignment]

# 순수 파싱 helper 정본은 binggupack.safety.gate_text(S3-B/C). import 실패 시 동일 정의 폴백 — byte-identical.
try:
    from binggupack.safety.gate_text import (  # noqa: F401
        SAVE_TRIGGER_RE,
        _norm,
        _strip_embedded_regions,
        parse_save_indices,
        sent_hash,
    )
except Exception:  # pragma: no cover - 폴백(외부 의존 없이 동일 정의)
    SAVE_TRIGGER_RE = re.compile(
        r"\s*(?:SAVE|저장|세이브)\s*\d+(\s*[-~]\s*\d+)?(\s*,\s*\d+(\s*[-~]\s*\d+)?)*\s*",
        re.IGNORECASE)

    _RANGE_CAP = 50

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

    def parse_save_indices(prompt):
        p = str(prompt or "")
        if SAVE_TRIGGER_RE.fullmatch(p):
            return _expand_indices(p)
        out, seen = [], set()
        for line in _strip_embedded_regions(p).splitlines():
            if line.strip() and SAVE_TRIGGER_RE.fullmatch(line):
                idx = _expand_indices(line)
                for i in idx or []:
                    if i not in seen:
                        seen.add(i)
                        out.append(i)
        return out or None

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


def gate_record(sentences, source="user_prompt", ts=None, path=None):
    """선택 후보 문장 hash 를 append-only 기록. 반환 기록 건수. 파일 부재 시 생성.
    존치(기존 seed 소비자·autopush 호환) — 승격 정본은 gate_record_ref(save-n 참조 바인딩)로 이관."""
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


def write_last_preview(candidates, path=None, explicit=False):
    """capture_preview 후보 → idx+sentence_hash 영속(원문 미저장, hash만). SAVE hook 이 대조용으로 읽음.
    매 preview 마다 덮어씀(직전 1건만 유효). pref=save-n 참조 바인딩용 preview_ref,
    explicit=후보 재도출 모드 — save/pair/core 재승격이 기록된 모드로 동일 재계산(pref 패리티)."""
    path = path or last_preview_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = _preview_rows(candidates)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "pref": preview_ref_for_rows(rows),
                   "explicit": bool(explicit), "items": rows}, f, ensure_ascii=False)
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


# ---------------- 히트/승격 스탬프 패밀리 (intel loop — save 도장 규약의 일반화) ----------------
# 마찰 근원("사람 증명 = 별도 터미널") 해소: owner 채팅 1-발화("히트 2"/"미스 2"/"승격 1")가
# 유일한 사람 도장이 되도록, 검증된 save-n 참조 바인딩(preview_ref+idx) 규약을 회수(hit/miss)·
# 승격(promote) 스탬프로 확장한다. 위쪽 SAVE 정규식·경로·함수는 불변 — 이 절은 순수 추가.
#
# [기록/판독 단일 확정 — 어느 파일에 쓰고 누가 읽는가]
#   기록 파일 = save 도장과 **동일한 save_gate_log.jsonl(gate_path()) 단일 파일**.
#   쓰는 자 = UserPromptSubmit hook(hooks/binggu_save_gate_hook.py → stamp_record_from_prompt).
#   읽는 자 = 스탬프 소비 API(recall_stamp_verdicts/gate_human_for_recall/gate_human_for_promote).
#            save 승격 판독자(binggu.py _resolve_human_ctx → gate_human_for_ref/_load_refs)는
#            같은 파일을 그대로 계속 읽는다 — 스탬프 레코드의 추가 필드(domain/stamp)는
#            기존 판독자가 무시(pref/idxs/ts 만 사용)하므로 병존 무해.
#   네임스페이스 = ref **해시 입력**의 도메인 접두("recall|"/"promote|") — 같은 rows 라도
#            도메인이 다르면 ref 값이 갈려 save pref ↔ 스탬프 ref 교차 재사용이 성립하지 않는다.
#
# 도장 강도(정직 서술) = SAVE 도장과 패리티: UserPromptSubmit hook 기록 + gate 파일 존재/신선도에
# 의존한다. 로컬 사용자 권한 프로세스가 gate 파일을 직접 쓰는 극단은 못 막음(사람 자기규율+사후감사),
# CLAUDECODE env 는 소프트 신호. 승격 write 성사는 여전히 core 게이트(G4_no_auto 등) 몫.

# 회수 스탬프: 'HIT 1' / '히트 1,3' / 'MISS 2' / '미스 1-3'. SAVE 도장과 동일 계약 —
# 발화 전체 또는 한 줄 전체 정확형(fullmatch)만 인정(부분문자열·인용문 무시).
HIT_TRIGGER_RE = re.compile(
    r"\s*(?:HIT|히트|MISS|미스)\s*\d+(\s*[-~]\s*\d+)?(\s*,\s*\d+(\s*[-~]\s*\d+)?)*\s*",
    re.IGNORECASE)

# 승격 스탬프: 'PROMOTE 1' / '승격 1,3-5'. 계약 동일(fullmatch·줄단위).
PROMOTE_TRIGGER_RE = re.compile(
    r"\s*(?:PROMOTE|승격)\s*\d+(\s*[-~]\s*\d+)?(\s*,\s*\d+(\s*[-~]\s*\d+)?)*\s*",
    re.IGNORECASE)

# fullmatch 통과 줄의 verdict 판별 — 선두 트리거가 HIT/히트면 hit, 아니면(MISS/미스) miss.
_HIT_WORD_RE = re.compile(r"\s*(?:HIT|히트)", re.IGNORECASE)

_STAMP_RANGE_CAP = 50  # 범위 확장 상한(오타 '1-99999' 폭주 방지 — 초과 시 그 줄 무효)


def _expand_stamp_indices(text):
    """'1,3' / '1-5' / '1,3-5' → 인덱스 리스트(중복 제거·순서 보존). 범위 폭주 → None.
    gate_text._expand_indices 와 동일 규칙(상한 50) — import 경로 무관 동작 보장용 로컬 정의."""
    out, seen = [], set()
    for part in re.findall(r"\d+(?:\s*[-~]\s*\d+)?", str(text)):
        nums = [int(x) for x in re.findall(r"\d+", part)]
        if len(nums) == 2:
            lo, hi = min(nums), max(nums)
            if hi - lo + 1 > _STAMP_RANGE_CAP:
                return None
            span = range(lo, hi + 1)
        else:
            span = nums
        for i in span:
            if i not in seen:
                seen.add(i)
                out.append(i)
    return out or None


def _stamp_chunks(pattern, prompt):
    """정확형 인식 공통부 — 발화 전체 fullmatch 또는 (fenced/blockquote 제거 후) 줄 단위 fullmatch 조각.
    save 도장과 동일 계약(2026-07-18 P0 수정): 붙여넣은 예시 안 '히트/승격 n' 독립줄은 도장 아님."""
    p = str(prompt or "")
    if pattern.fullmatch(p):
        return [p]
    return [ln for ln in _strip_embedded_regions(p).splitlines()
            if ln.strip() and pattern.fullmatch(ln)]


def parse_hit_stamps(prompt):
    """회수 도장 인식 — {"hit": [idx...], "miss": [idx...]} 또는 None.
    같은 메시지에서 같은 idx 를 재도장하면 나중 줄이 이긴다(정정 허용 — idx당 verdict 1개).
    문장 속 언급("그거 히트 3 어쩌고")은 줄 일부라 무시(SAVE 도장과 동일 오도장 차단 계약)."""
    verdicts = {}
    for chunk in _stamp_chunks(HIT_TRIGGER_RE, prompt):
        idx = _expand_stamp_indices(chunk)
        if not idx:
            continue
        v = "hit" if _HIT_WORD_RE.match(chunk) else "miss"
        for i in idx:
            verdicts[i] = v
    if not verdicts:
        return None
    out = {"hit": [], "miss": []}
    for i, v in verdicts.items():
        out[v].append(i)
    return out


def parse_promote_indices(prompt):
    """승격 도장 인식 — 인덱스 리스트 또는 None(계약은 parse_save_indices 와 동일)."""
    out, seen = [], set()
    for chunk in _stamp_chunks(PROMOTE_TRIGGER_RE, prompt):
        for i in _expand_stamp_indices(chunk) or []:
            if i not in seen:
                seen.add(i)
                out.append(i)
    return out or None


def last_recall_candidates_path():
    """직전 회수 staging 경로(호출 시점 lazy, 단일 home 경유). 회상 표면이 쓰고
    스탬프 hook/소비 API 가 읽음. 매 회상 덮어씀(직전 1건 원칙)."""
    return os.path.join(gate_home(), "last_recall_candidates.json")


def last_promote_candidates_path():
    """직전 승격 후보 staging 경로(호출 시점 lazy, 단일 home 경유). 승격 preview 가 쓰고
    스탬프 hook/소비 API 가 읽음. 매 preview 덮어씀(직전 1건 원칙)."""
    return os.path.join(gate_home(), "last_promote_candidates.json")


def _stamp_ref_for_rows(domain, rows):
    """staging rows([{"idx","node_id"}]) → 결정론적 스탬프 ref(sha256, 16자).
    해시 입력에 도메인 접두('<domain>|') — save pref 와 값 공간 분리(교차 재사용 차단)."""
    joined = "%s|" % domain + "\n".join(
        "%s:%s" % (r.get("idx"), r.get("node_id")) for r in (rows or []))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def recall_gate_ref(rows):
    """회수 staging rows → ref('recall|' 접두). preview_ref_for_rows 패턴 복제 — 후보 집합+순서에서
    파생(어디서 재계산해도 동일값). 판정은 항상 이 재계산 값만 신뢰(staging 의 ref 필드는 표시용)."""
    return _stamp_ref_for_rows("recall", rows)


def promote_gate_ref(rows):
    """승격 staging rows → ref('promote|' 접두). 규약은 recall_gate_ref 와 동일."""
    return _stamp_ref_for_rows("promote", rows)


def _write_staging(path, payload):
    """staging 원자적 덮어쓰기(tmp+os.replace) — write_last_preview 와 동일 방식."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


def write_last_recall(node_ids, query="", domain="", surface="", path=None):
    """회수 staging 영속 — 매 회상 덮어씀(직전 1건 원칙). node_ids 표시 순서 = idx(1-base).
    호출측 계약: 로컬 CLI/hook 회상 표면만 이 파일을 쓴다(MCP reader 원격 표면 기록 0).
    ★query 원문 평문 영속 — 팩/export 미포함·leak_guard 통과분만(호출측 계약).
    ref 필드는 표시/디버그용 — 판정은 소비 시점 rows 재계산(recall_gate_ref)만 신뢰."""
    path = path or last_recall_candidates_path()
    rows = [{"idx": j + 1, "node_id": str(n)}
            for j, n in enumerate(node_ids or []) if str(n or "").strip()]
    _write_staging(path, {"ts": time.time(), "query": str(query or ""),
                          "domain": str(domain or ""), "surface": str(surface or ""),
                          "ref": recall_gate_ref(rows), "items": rows})
    return len(rows)


def write_last_promote(items, path=None):
    """승격 후보 staging 영속 — 매 preview 덮어씀(직전 1건 원칙). items 항목 = node_id 문자열
    또는 {"node_id","id8","claim"}(id8 생략 시 node_id 앞 8자). claim 은 owner 표시용 —
    ref 바인딩은 idx:node_id 만(승격 대상 결정 필드). ref 필드는 표시용(판정은 재계산만 신뢰)."""
    path = path or last_promote_candidates_path()
    rows = []
    for j, it in enumerate(items or []):
        if isinstance(it, str):
            it = {"node_id": it}
        nid = str((it or {}).get("node_id") or "").strip()
        if not nid:
            continue
        rows.append({"idx": j + 1, "node_id": nid,
                     "id8": str(it.get("id8") or nid[:8]),
                     "claim": str(it.get("claim") or "")})
    _write_staging(path, {"ts": time.time(), "ref": promote_gate_ref(rows), "items": rows})
    return len(rows)


def _load_staging(path):
    """staging 판독 → dict | None(부재/파손 무해)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_last_recall(path=None):
    """직전 회수 staging → dict | None."""
    return _load_staging(path or last_recall_candidates_path())


def load_last_promote(path=None):
    """직전 승격 staging → dict | None."""
    return _load_staging(path or last_promote_candidates_path())


def stamp_record_ref(pref, idxs, stamp, domain, ts=None, source="user_prompt", path=None):
    """스탬프 참조 바인딩 레코드 1행 append — {"pref","idxs","ts","source","domain","stamp"}.
    gate_record_ref 패턴 복제 + 감사용 domain/stamp 필드(기존 판독자 _load/_load_refs 는 무시).
    반환 기록 행 수(0=인자 불충분). 파일 = save 와 동일한 save_gate_log.jsonl."""
    idxs = [int(i) for i in (idxs or [])]
    if not pref or not idxs or not stamp:
        return 0
    path = path or gate_path()
    ts = ts if ts is not None else time.time()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"pref": pref, "idxs": idxs, "ts": ts, "source": source,
                            "domain": domain, "stamp": stamp}, ensure_ascii=False) + "\n")
    return 1


def stamp_record_from_prompt(prompt, recall_path=None, promote_path=None, gate_path=None,
                             ts=None, skip_recall=False):
    """스탬프 hook 진입점 — 발화가 히트/미스/승격 정확형이면 해당 staging 의 idx 를 게이트에 기록.
    ref 는 staging 의 ref 필드가 아니라 rows 에서 재계산(도장 시점부터 저장 필드 미신뢰).
    staging 부재/파손/idx 불일치 → 기록 0(무해). 반환 append 된 ref 레코드 행 수.

    skip_recall=True → 회수(히트/미스) 분기 생략(승격 분기는 유지). 세션 마무리 preview 히트를
    recall_trace 효용 장부(mark_by_index)로 도장할 때, hook 이 last_recall 이중 기록을 막으려
    사용한다(같은 발화가 두 장부로 가는 것 차단 — owner '단일 통합' 의도)."""
    n = 0
    if not skip_recall:
        hs = parse_hit_stamps(prompt)
        if hs:
            st = load_last_recall(recall_path) or {}
            rows = st.get("items") or []
            by_idx = {r.get("idx"): r.get("node_id") for r in rows}
            if rows:
                ref = recall_gate_ref(rows)
                for verdict in ("hit", "miss"):
                    matched = [i for i in (hs.get(verdict) or []) if by_idx.get(i)]
                    if matched:
                        n += stamp_record_ref(ref, matched, verdict, "recall", ts=ts, path=gate_path)
    pidx = parse_promote_indices(prompt)
    if pidx:
        st = load_last_promote(promote_path) or {}
        rows = st.get("items") or []
        by_idx = {r.get("idx"): r.get("node_id") for r in rows}
        matched = [i for i in pidx if by_idx.get(i)]
        if rows and matched:
            n += stamp_record_ref(promote_gate_ref(rows), matched, "promote", "promote",
                                  ts=ts, path=gate_path)
    return n


def _load_stamps(path=None):
    """기록장 → {(pref, idx): (최신ts, stamp)} — 스탬프 레코드({"pref"+"stamp"} 有)만 적재.
    save ref 레코드(stamp 無)는 _load_refs 몫(같은 파일 병존·상호 무간섭)."""
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
            pref, stamp, ts = d.get("pref"), d.get("stamp"), d.get("ts", 0)
            if not pref or not stamp:
                continue
            for i in (d.get("idxs") or []):
                k = (pref, i)
                if k not in out or ts > out[k][0]:
                    out[k] = (ts, stamp)
    return out


def recall_stamp_verdicts(rows, path=None, now=None):
    """소비 시점 회수 스탬프 판독 — staging rows 에서 recall_gate_ref 를 **재계산**해 게이트 기록과
    대조(저장된 ref 필드 신뢰 금지 — 도장 후 staging 이 변조되면 재계산 ref 가 기록 pref 와 달라
    빈 dict = mismatch). 반환 {idx: "hit"|"miss"} — 신선도 창 이내·미래ts 무효·idx당 최신 승리.
    consumed 마킹은 판정 미반영(최소 마킹만 — 게이트 반영은 후속)."""
    now = now if now is not None else time.time()
    ref = recall_gate_ref(rows)
    out = {}
    for (pref, i), (ts, stamp) in _load_stamps(path).items():
        if pref != ref or stamp not in ("hit", "miss"):
            continue
        age = now - ts
        if age < 0:
            continue
        if GATE_WINDOW_SEC and age > GATE_WINDOW_SEC:
            continue
        out[i] = stamp
    return out


def gate_human_for_recall(rows, idxs, verdict, path=None, now=None):
    """요청 idx 전부가 해당 verdict("hit"/"miss")로 신선하게 도장됐으면 True(all-or-nothing·
    fail-closed). ref 는 rows 재계산 — staging 변조 시 False(mismatch)."""
    idxs = [int(i) for i in (idxs or [])]
    if not idxs or verdict not in ("hit", "miss"):
        return False
    vd = recall_stamp_verdicts(rows, path=path, now=now)
    return all(vd.get(i) == verdict for i in idxs)


def gate_human_for_promote(rows, idxs, path=None, now=None):
    """요청 idx 전부가 사람 승격 도장으로 기록됐고 신선도 창 이내면 True — 판정 규약은
    gate_human_for_ref 그대로(all-or-nothing·stale/미래ts 무효·fail-closed).
    ref 는 소비 시점 rows 재계산(promote_gate_ref) — staging 변조 시 False(mismatch).
    이 True 는 사람 도장 증명일 뿐, 실제 승격 write 는 봉인 모듈 게이트(백업 강제·G4_no_auto) 몫."""
    return gate_human_for_ref(promote_gate_ref(rows), idxs, path=path, now=now)


def stamp_mark_consumed(pref, idxs, ts=None, path=None):
    """도장 1회 소비 마킹(최소) — {"consumed_pref","idxs","ts"} 1행 append(같은 파일).
    레코드에 "pref"/"sh" 키가 없어 _load/_load_refs/_load_stamps 판정에 무영향 —
    게이트 판정에 consumed 를 반영하는 것은 후속(지금은 감사 가능한 마킹만). 반환 기록 행 수."""
    idxs = [int(i) for i in (idxs or [])]
    if not pref or not idxs:
        return 0
    path = path or gate_path()
    ts = ts if ts is not None else time.time()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"consumed_pref": pref, "idxs": idxs, "ts": ts},
                           ensure_ascii=False) + "\n")
    return 1


def _load_consumed(path=None):
    """기록장 → {(pref, idx): 최신 consumed ts} — consumed_pref 레코드만."""
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
            pref, ts = d.get("consumed_pref"), d.get("ts", 0)
            if not pref:
                continue
            for i in (d.get("idxs") or []):
                k = (pref, i)
                if k not in out or ts > out[k]:
                    out[k] = ts
    return out
