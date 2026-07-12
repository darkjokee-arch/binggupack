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
        parse_save_indices,
        sent_hash,
    )
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
