#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu_backfill_evidence_locator — 기존 evidence 의 **원본 위치**를 역추적해 evidence_locator 를 채운다.

정본: docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md §1(증거 3요소 = source_id·위치·excerpt_sha)
      · 설계 v2 §1-1/§5-B/§6-3 · 재검증 NEW2.10(요약본을 원문으로 계상 금지)
스키마 정본: scripts/binggu_schema.py (evidence_locator / system_provenance · 플래그 BINGGU_EVLOC_V5)
적재 정본: scripts/openbinggu_staging_write_selftest.py (loc_row / insert_locators / mirror jsonl)

이 파일이 지키는 불변 6개
  ① **dry-run 기본**. `--apply` 없이는 대상 ledger 에 write 0(읽기 전용 연결로만 연다).
  ② `--apply` 는 `--batch-id` 필수 + **safe_backup 선행 필수**(Online Backup API · MF1.1).
     운영 ledger 가 대상이면 `--confirm-operating <batch-id>` 라는 **사람의 명시 앵커**가 추가로
     필요하다(§C-11 승인 대행 금지 — 이 스크립트가 스스로 운영에 손대지 않는다).
  ③ **기존 행 UPDATE/DELETE 0**. nodes/edges/evidence 는 읽기만. 적재는 신규 2테이블뿐이고,
     적용 전후 `integrity_probe()` 동일을 게이트로 강제한다(다르면 NO-GO).
  ④ **테이블 삭제(DROP) 금지**(MF1.3). 롤백은 `evidence_locator_<batch>.jsonl` 전량 export → 행수
     대조 통과 후에만 `DELETE WHERE batch_id=?`. excerpt 는 ledger 밖 jsonl 에 이중 보관.
  ⑤ **등급을 속이지 않는다**(NEW2.10). 원문(세션로그 대화 턴) 회수만 T1. 2차 요약본(md)·메아리
     (빙구팩 자기 렌더/저장 호출)·자기참조는 T2/T3 로 **분리 집계**하고, 회수 불가는 빈칸이 아니라
     `system_provenance` 에 `match_method='none'` + 사유로 명시 기록한다(evidence_locator 오염 금지).
  ⑥ **audit_log tail 을 오염시키지 않는다**(NEW2.7). audit 행의 before/after 는 store_checksum
     그대로(백필은 nodes/edges/evidence 를 안 건드리므로 before==after), locator 전용 앵커는
     audit_meta['evloc_head'] 에 기록한다 → verify_tail_state() 가 계속 성립.

CLI
  python scripts/binggu_backfill_evidence_locator.py                      # dry-run(운영 ledger 읽기만)
  python scripts/binggu_backfill_evidence_locator.py --ledger <copy> --sheet out.md --plan plan.json
  python scripts/binggu_backfill_evidence_locator.py --ledger <copy> --apply --batch-id bf20260727
  python scripts/binggu_backfill_evidence_locator.py --ledger <copy> --rollback --batch-id bf20260727
  python scripts/binggu_backfill_evidence_locator.py --selftest
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import binggu_platform as _plat  # noqa: E402
from binggu_schema import (  # noqa: E402
    EVLOC_FLAG_ENV, has_table, integrity_probe, locator_checksum, safe_backup, table_columns,
)
from openbinggu_staging_write_selftest import (  # noqa: E402
    StagingDB, evloc_mirror_path, excerpt_sha, insert_locators, loc_row, verify_locator_tail,
)

PARSER_ID = "binggu_backfill_evidence_locator/v1"
# 무손실 판정 축 — 백필은 audit_log/audit_meta 에는 **정당하게 1행씩 추가**하므로 전 테이블
# probe 를 그대로 쓰면 항상 '변했다'가 된다. 지켜야 할 불변은 "기존 지식 데이터 무손실"이라
# nodes/edges/evidence 3축으로 좁혀 대조한다(휘발 컬럼 제외는 integrity_probe 가 처리).
DATA_TABLES = ("nodes", "edges", "evidence")
LOC_TABLE = "evidence_locator"
PROV_TABLE = "system_provenance"
AUDIT_ACTOR = "backfill_evidence_locator"
AUDIT_APPLY = "evidence_locator_backfill"
AUDIT_ROLLBACK = "evidence_locator_rollback"

# 회수 원본 기본 경로(실측 기반 · 사용자 환경). 둘 다 CLI 로 교체 가능.
DEFAULT_SESSION_ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
DEFAULT_DOC_ROOTS = (os.path.join(os.path.expanduser("~"), ".claude", "memory"),)

# 등급 정본 — 분자/분모를 섞지 않기 위해 '1차 출처'는 session_exact/session_norm 둘뿐이다.
PRIMARY_METHODS = ("session_exact", "session_norm")
GRADE = {
    # match_method            : (confidence, 한 줄 설명)
    "session_exact":           ("T1", "세션로그 대화 턴 원문 정확일치(1차 출처)"),
    "session_norm":            ("T1", "세션로그 대화 턴 공백정규화 일치(1차 출처·원문 슬라이스 보존)"),
    "session_speaker_mismatch": ("T2", "세션로그에서 찾았으나 노드 화자와 턴 역할 불일치 — owner 확인"),
    "session_late":            ("T2", "세션로그 위치이나 저장 시각 이후 발화(재언급) — owner 확인"),
    "md_exact":                ("T2", "문서(2차 요약본) 라인 일치 — 원문 대화 아님"),
    "session_echo":            ("T3", "도구 입출력·주입 블록에서만 발견(빙구팩 자기 렌더) — 원본 아님"),
    "self_reference":          ("T3", "컨테이너가 발췌 자신과 동일(독립 원본 아님·NEW2.10 강등)"),
    "none":                    ("T4", "회수 불가 — evidence_locator 미기재, 사유만 기록"),
}
# 우선순위(작을수록 좋음) — 같은 evidence 에 여러 후보가 걸리면 이 순서로 고른다.
_METHOD_RANK = {"session_exact": 0, "session_norm": 1, "session_speaker_mismatch": 2,
                "session_late": 3, "md_exact": 4, "session_echo": 5}

_WS = re.compile(r"\s+")
_SYSREM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
SHINGLE_K = 16


def norm(s):
    return _WS.sub(" ", str(s)).strip()


def sha256_hex(s):
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ro_conn(path):
    """읽기 전용 연결(write 0 보장). URI 실패 시 query_only 폴백."""
    try:
        import pathlib
        uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except Exception:                       # noqa: BLE001 — URI 미지원/권한 등 전부 폴백 대상
        con = sqlite3.connect(path)
        con.execute("PRAGMA query_only=ON")
        return con


def is_operating_ledger(path):
    try:
        op = _plat.default_ledger()
    except Exception:                       # noqa: BLE001 — resolver 실패 시 보수적으로 '아님'
        return False
    return os.path.normcase(os.path.abspath(str(path))) == os.path.normcase(os.path.abspath(op))


# ── 원본 회수: 대상 목록 ──────────────────────────────────────────────────────
def load_targets(con):
    """evidence 전건 + 자기증빙 엣지로 연결된 노드(화자·id). read-only.

    node 는 `edges.relation='evidence_supports'` 경유(설계 §1-1 라우팅). 엣지가 없으면
    node_* 는 None 이고, 그건 '증거 없음' 이 아니라 '노드 미연결' 로 별도 표면화한다.
    """
    rows = con.execute(
        "SELECT e.evidence_id, e.sentence, e.created_at, e.pack_id, n.node_id, n.speaker "
        "FROM evidence e "
        "LEFT JOIN edges g ON g.source = e.evidence_id AND g.relation = 'evidence_supports' "
        "LEFT JOIN nodes n ON n.node_id = g.target "
        "ORDER BY e.evidence_id").fetchall()
    out = []
    for eid, sent, created_at, pack_id, node_id, speaker in rows:
        out.append({"evidence_id": eid, "sentence": sent or "", "created_at": created_at or "",
                    "pack_id": pack_id, "node_id": node_id, "speaker": speaker,
                    "key": norm(sent or "")})
    return out


class ShingleIndex:
    """부분문자열 다중 검색용 k-gram 인덱스.

    길이 >= 2k-1 인 패턴은 텍스트를 stride k 로 훑을 때 **반드시** 정렬된 k-gram 하나를 통째로
    포함한다 → 후보를 O(len/k) 로 좁힌 뒤 그 후보만 실제 `in` 으로 확인한다(오탐 0·누락 0).
    짧은 패턴은 그리드 보장이 깨지므로 별도 리스트로 전수 확인한다(건수가 적어 비용 무시 가능).
    """

    def __init__(self, keys, k=SHINGLE_K):
        self.k = k
        self.index = {}
        self.short = []
        for key in keys:
            if len(key) < 2 * k - 1:
                self.short.append(key)
                continue
            for i in range(len(key) - k + 1):
                self.index.setdefault(key[i:i + k], set()).add(key)

    def candidates(self, ntext):
        k = self.k
        out = set()
        for j in range(0, max(0, len(ntext) - k + 1), k):
            g = self.index.get(ntext[j:j + k])
            if g:
                out |= g
        for p in self.short:
            if p in ntext:
                out.add(p)
        return out


def _tolerant_slice(raw, sentence):
    """raw 안에서 sentence 의 **원문 슬라이스**를 찾는다. (excerpt, offset, exact) 또는 None.

    1) 원문 정확일치 우선. 2) 실패 시 토큰 사이 공백을 \\s+ 로 허용한 정규식(정규화 일치).
    어느 쪽이든 **원문에서 잘라낸 문자열**을 돌려준다 — 정규화한 문자열을 excerpt 로 저장하면
    '원문 발췌'가 아니게 되므로 금지.
    """
    if not sentence:
        return None
    i = raw.find(sentence)
    if i >= 0:
        return sentence, i, True
    toks = [t for t in _WS.split(norm(sentence)) if t]
    if not toks:
        return None
    try:
        pat = re.compile(r"\s+".join(re.escape(t) for t in toks))
    except re.error:
        return None
    m = pat.search(raw)
    if not m:
        return None
    return m.group(0), m.start(), False


def extract_blocks(rec):
    """세션 레코드 → [(kind, text)]. kind: user|assistant|thinking|tool_use|tool_result."""
    t = rec.get("type")
    if t not in ("user", "assistant"):
        return []
    msg = rec.get("message") or {}
    content = msg.get("content")
    role = msg.get("role") or t
    out = []
    if isinstance(content, str):
        out.append((role, content))
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and isinstance(b.get("text"), str):
                out.append((role, b["text"]))
            elif bt == "thinking" and isinstance(b.get("thinking"), str):
                out.append(("thinking", b["thinking"]))
            elif bt == "tool_use":
                try:
                    out.append(("tool_use", json.dumps(b.get("input"), ensure_ascii=False)))
                except Exception:                    # noqa: BLE001 — 직렬화 불가 입력은 건너뜀
                    pass
            elif bt == "tool_result":
                cc = b.get("content")
                if isinstance(cc, str):
                    out.append(("tool_result", cc))
                elif isinstance(cc, list):
                    for x in cc:
                        if isinstance(x, dict) and isinstance(x.get("text"), str):
                            out.append(("tool_result", x["text"]))
    return out


def _in_system_reminder(raw, off):
    """주입 블록(<system-reminder>) 안이면 True — 사람의 발화가 아니라 하네스 주입이다."""
    for m in _SYSREM.finditer(raw):
        if m.start() <= off < m.end():
            return True
    return False


def _classify_session(kind, raw, off, target, ts):
    """세션 히트 등급 판정. (match_method, note) 반환."""
    if kind in ("tool_use", "tool_result", "thinking") or _in_system_reminder(raw, off):
        return "session_echo", "kind=%s" % kind
    created = target.get("created_at") or ""
    if created and ts and ts > created:
        return "session_late", "turn_ts=%s > created_at=%s" % (ts, created)
    spk = target.get("speaker")
    if spk == "owner" and kind != "user":
        return "session_speaker_mismatch", "node.speaker=owner, turn=%s" % kind
    if spk == "ai" and kind != "assistant":
        return "session_speaker_mismatch", "node.speaker=ai, turn=%s" % kind
    return "session_exact", "kind=%s" % kind


def scan_sessions(root, targets, progress=None):
    """세션 로그 전수 스캔. 반환 (best{eid: hit}, meta{files,lines,chars,ts_min,ts_max})."""
    by_key = {}
    for t in targets:
        if len(t["key"]) >= 12:
            by_key.setdefault(t["key"], []).append(t)
    idx = ShingleIndex(by_key.keys())
    best, meta = {}, {"files": 0, "records": 0, "chars": 0, "ts_min": None, "ts_max": None,
                      "root": root, "scanned": bool(root and os.path.isdir(root))}
    if not meta["scanned"]:
        return best, meta
    files = sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))
    t0 = time.time()
    for fi, path in enumerate(files):
        meta["files"] += 1
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if len(line) < 20:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:                    # noqa: BLE001 — 손상 라인은 건너뛰되 카운트
                    continue
                meta["records"] += 1
                ts = rec.get("timestamp") or ""
                if ts:
                    meta["ts_min"] = ts if not meta["ts_min"] else min(meta["ts_min"], ts)
                    meta["ts_max"] = ts if not meta["ts_max"] else max(meta["ts_max"], ts)
                for kind, raw in extract_blocks(rec):
                    if len(raw) < 12:
                        continue
                    ntext = norm(raw)
                    meta["chars"] += len(ntext)
                    for key in idx.candidates(ntext):
                        if key not in ntext:
                            continue
                        for tg in by_key[key]:
                            sl = _tolerant_slice(raw, tg["sentence"])
                            if not sl:
                                continue
                            excerpt, off, exact = sl
                            method, note = _classify_session(kind, raw, off, tg, ts)
                            if method == "session_exact" and not exact:
                                method = "session_norm"
                            rank = (_METHOD_RANK[method], ts or "9999")
                            cur = best.get(tg["evidence_id"])
                            if cur and cur["_rank"] <= rank:
                                continue
                            best[tg["evidence_id"]] = {
                                "_rank": rank, "method": method, "note": note,
                                "source_id": "session:%s" % (rec.get("sessionId") or "unknown"),
                                "locator": "uuid:%s:off:%d:len:%d" % (
                                    rec.get("uuid") or "unknown", off, len(excerpt)),
                                "excerpt": excerpt, "container": raw, "ts": ts,
                                "file_path": path, "kind": kind,
                            }
        if progress and fi % 1500 == 0:
            progress("  sessions %d/%d  hits=%d  %.1fs" % (fi, len(files), len(best), time.time() - t0))
    return best, meta


def scan_docs(roots, targets, progress=None):
    """2차 출처(md 요약본) 스캔. 반환 (best{eid: hit}, meta)."""
    by_key = {}
    for t in targets:
        if len(t["key"]) >= 12:
            by_key.setdefault(t["key"], []).append(t)
    idx = ShingleIndex(by_key.keys())
    best, meta = {}, {"files": 0, "roots": list(roots), "scanned": False}
    files = []
    for r in roots or ():
        if r and os.path.isdir(r):
            meta["scanned"] = True
            files += glob.glob(os.path.join(r, "**", "*.md"), recursive=True)
    for path in sorted(set(files)):
        meta["files"] += 1
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for lineno, raw in enumerate(text.splitlines(), 1):
            if len(raw) < 12:
                continue
            ntext = norm(raw)
            for key in idx.candidates(ntext):
                if key not in ntext:
                    continue
                for tg in by_key[key]:
                    if tg["evidence_id"] in best:
                        continue
                    sl = _tolerant_slice(raw, tg["sentence"])
                    if not sl:
                        continue
                    excerpt, off, _exact = sl
                    best[tg["evidence_id"]] = {
                        "_rank": (_METHOD_RANK["md_exact"], ""), "method": "md_exact",
                        "note": os.path.basename(path),
                        "source_id": "file:%s" % os.path.basename(path),
                        "locator": "line:%d:off:%d:len:%d" % (lineno, off, len(excerpt)),
                        "excerpt": excerpt, "container": raw, "ts": "",
                        "file_path": path, "kind": "doc",
                    }
    if progress:
        progress("  docs files=%d hits=%d" % (meta["files"], len(best)))
    return best, meta


# ── 계획 수립 ────────────────────────────────────────────────────────────────
def build_plan(ledger_path, batch_id, session_root=DEFAULT_SESSION_ROOT,
               doc_roots=DEFAULT_DOC_ROOTS, progress=None):
    """dry-run 계획. **대상 ledger 는 읽기 전용으로만 연다**(write 0)."""
    con = _ro_conn(ledger_path)
    try:
        targets = load_targets(con)
        ev_total = con.execute("SELECT count(*) FROM evidence").fetchone()[0]
        existing = set()
        if has_table(con, LOC_TABLE):
            existing = {r[0] for r in con.execute(
                "SELECT DISTINCT evidence_id FROM %s" % LOC_TABLE)}
    finally:
        con.close()

    s_best, s_meta = scan_sessions(session_root, targets, progress)
    d_best, d_meta = scan_docs(doc_roots, targets, progress)

    # 세션 시간창(30일 롤링) 실측 — 회수 가능 구간 보고용.
    win_lo, win_hi = s_meta.get("ts_min"), s_meta.get("ts_max")
    created_at = now_iso()
    rows, prov_rows, sheet = [], [], []
    for tg in targets:
        eid = tg["evidence_id"]
        # 세션·문서 후보를 **같은 랭크 축**으로 비교한다. 세션 히트라도 메아리(session_echo·rank5)면
        # 2차 문서(md_exact·rank4)보다 나쁘다 — 무조건 세션 우선으로 두면 메아리가 문서를 밀어낸다.
        cands = [h for h in (s_best.get(eid), d_best.get(eid)) if h]
        hit = min(cands, key=lambda h: h["_rank"]) if cands else None
        in_window = bool(win_lo and tg["created_at"] and win_lo <= tg["created_at"] <= (win_hi or ""))
        if hit:
            method, note = hit["method"], hit["note"]
            container = hit["container"]
            csha = sha256_hex(container)
            # NEW2.10 자기참조 가드 — 컨테이너가 발췌 자신뿐이면 독립 원본이 아니다.
            # ★ 실시간 앞막이(live_capture)는 이 강등 대상이 아니지만, 백필은 전부 사후 회수라
            #   여기서 예외를 둘 대상이 없다(앞막이 행은 이 스크립트가 만들지 않는다).
            if csha == sha256_hex(hit["excerpt"]) and method != "session_exact":
                method, note = "self_reference", "container == excerpt (%s)" % note
            conf = GRADE[method][0]
            # 행 조립은 Unit C 정본 loc_row() 에 위임한다 — loc_id/excerpt_sha 산식과 UNIQUE
            # 참여 컬럼의 '' 정규화를 두 번 적지 않기 위함(두 번째 진실원본 금지).
            rows.append(loc_row(eid, hit["excerpt"], source_id=hit["source_id"],
                                locator=hit["locator"], container_sha=csha,
                                match_method=method, confidence=conf, verified_by="auto",
                                batch_id=batch_id, created_at=created_at))
            prov_rows.append(_prov_row(eid, batch_id, created_at, {
                "match_method": method, "confidence": conf, "note": note,
                "turn_kind": hit["kind"], "turn_ts": hit["ts"]}, hit["file_path"]))
            sheet.append({"evidence_id": eid, "node_id": tg["node_id"], "speaker": tg["speaker"],
                          "created_at": tg["created_at"], "match_method": method,
                          "confidence": conf, "source_id": hit["source_id"],
                          "locator": hit["locator"], "note": note,
                          "sentence": tg["sentence"], "excerpt": hit["excerpt"]})
        else:
            reason = "no_text_match"
            if tg["created_at"] and win_lo and tg["created_at"] < win_lo:
                reason = "out_of_retention_window(session_log_min=%s)" % win_lo
            elif len(tg["key"]) < 12:
                reason = "sentence_too_short_to_match(<12chars)"
            elif not in_window:
                reason = "created_at_outside_scanned_window"
            prov_rows.append(_prov_row(eid, batch_id, created_at, {
                "match_method": "none", "confidence": "T4", "reason": reason,
                "sentence_len": len(tg["sentence"]), "speaker": tg["speaker"],
                "evidence_created_at": tg["created_at"]}, None))
            sheet.append({"evidence_id": eid, "node_id": tg["node_id"], "speaker": tg["speaker"],
                          "created_at": tg["created_at"], "match_method": "none",
                          "confidence": "T4", "source_id": None, "locator": None,
                          "note": reason, "sentence": tg["sentence"], "excerpt": None})

    stats = _stats(sheet, ev_total, existing, targets, win_lo, win_hi)
    return {"batch_id": batch_id, "ledger": os.path.abspath(ledger_path),
            "generated_at": created_at, "rows": rows, "prov_rows": prov_rows,
            "sheet": sheet, "stats": stats,
            "sources": {"sessions": s_meta, "docs": d_meta}}


def _prov_row(evidence_id, batch_id, created_at, payload, file_path):
    """system_provenance 1행 — **증거 불인정**(evidence_eligible=0) 시스템 유래 기록."""
    key = "|".join([str(evidence_id), str(batch_id), json.dumps(payload, ensure_ascii=False,
                                                               sort_keys=True)])
    return {"prov_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:24],
            "subject_kind": "evidence", "subject_id": evidence_id, "parser": PARSER_ID,
            "file_path": file_path or "", "frontmatter_json": json.dumps(payload, ensure_ascii=False),
            "evidence_eligible": 0, "batch_id": batch_id, "created_at": created_at}


def _stats(sheet, ev_total, existing, targets, win_lo, win_hi):
    by_method, by_conf = {}, {}
    for s in sheet:
        by_method[s["match_method"]] = by_method.get(s["match_method"], 0) + 1
        by_conf[s["confidence"]] = by_conf.get(s["confidence"], 0) + 1
    primary = sum(by_method.get(m, 0) for m in PRIMARY_METHODS)
    speaker = {}
    for tg in targets:
        k = tg["speaker"] or "(NULL)"
        speaker.setdefault(k, {"total": 0, "primary": 0, "any": 0})
        speaker[k]["total"] += 1
    idx = {s["evidence_id"]: s for s in sheet}
    for tg in targets:
        s = idx.get(tg["evidence_id"])
        k = tg["speaker"] or "(NULL)"
        if s and s["match_method"] != "none":
            speaker[k]["any"] += 1
            if s["match_method"] in PRIMARY_METHODS:
                speaker[k]["primary"] += 1
    return {
        "evidence_total": ev_total, "planned_rows": sum(1 for s in sheet if s["match_method"] != "none"),
        "unmatched": by_method.get("none", 0), "already_has_locator": len(existing),
        "by_method": by_method, "by_confidence": by_conf,
        "primary_source": primary,
        "primary_ratio": round(primary / ev_total, 4) if ev_total else 0.0,
        "any_locator_ratio": round((ev_total - by_method.get("none", 0)) / ev_total, 4) if ev_total else 0.0,
        "by_speaker": speaker,
        "session_window": {"min_ts": win_lo, "max_ts": win_hi},
    }


# ── 산출물 ───────────────────────────────────────────────────────────────────
def write_sheet(plan, path):
    """검수 시트(markdown) — 사람이 T1 일괄/T2 개별/불가 기록동의를 판단할 표."""
    st = plan["stats"]
    lines = ["# evidence_locator 백필 검수 시트",
             "",
             "- batch_id: `%s`" % plan["batch_id"],
             "- ledger: `%s`" % plan["ledger"],
             "- 생성: %s" % plan["generated_at"],
             "- 세션로그 회수 가능 구간(실측): %s ~ %s (파일 %d · 레코드 %d)" % (
                 st["session_window"]["min_ts"], st["session_window"]["max_ts"],
                 plan["sources"]["sessions"].get("files", 0),
                 plan["sources"]["sessions"].get("records", 0)),
             "",
             "## 등급별 회수율",
             "",
             "| match_method | 등급 | 건수 | 비율 | 뜻 |",
             "|---|---|---|---|---|"]
    tot = st["evidence_total"] or 1
    for m, n in sorted(st["by_method"].items(), key=lambda kv: -kv[1]):
        conf, desc = GRADE.get(m, ("?", ""))
        lines.append("| %s | %s | %d | %.1f%% | %s |" % (m, conf, n, 100.0 * n / tot, desc))
    lines += ["",
              "**1차 출처(원문 대화) 회수율 = %d/%d (%.1f%%)** — 이 값만 G7 '증거 위치 보유' 분자에 넣는다."
              % (st["primary_source"], tot, 100.0 * st["primary_source"] / tot),
              "2차/메아리 포함 전체 locator 부착률 = %.1f%% (같은 축으로 합산 금지)."
              % (100.0 * st["any_locator_ratio"]),
              "",
              "## 화자별",
              "",
              "| speaker | 총 | 1차 | 전체 |", "|---|---|---|---|"]
    for k, v in sorted(st["by_speaker"].items()):
        lines.append("| %s | %d | %d | %d |" % (k, v["total"], v["primary"], v["any"]))
    lines += ["", "## 표본 (등급별 최대 3건)", ""]
    seen = {}
    for s in plan["sheet"]:
        m = s["match_method"]
        seen.setdefault(m, 0)
        if seen[m] >= 3:
            continue
        seen[m] += 1
        lines.append("- `%s` **%s/%s** %s" % (s["evidence_id"], m, s["confidence"],
                                              s["source_id"] or "-"))
        lines.append("  - locator: `%s` · note: %s" % (s["locator"] or "-", s["note"]))
        lines.append("  - 문장: %s" % (s["sentence"] or "")[:120])
    body = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def batch_export_path(ledger_path, batch_id):
    d = os.path.dirname(os.path.abspath(ledger_path))
    return os.path.join(d, "evidence_locator_%s.jsonl" % batch_id)


def export_rows(ledger_path, batch_id, rows, prov_rows):
    """excerpt 이중 보관(MF1.3) — ledger 밖 append-only jsonl. 반환 (path, 기록행수)."""
    path = batch_export_path(ledger_path, batch_id)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"_t": LOC_TABLE, **r}, ensure_ascii=False) + "\n")
            n += 1
        for r in prov_rows:
            f.write(json.dumps({"_t": PROV_TABLE, **r}, ensure_ascii=False) + "\n")
            n += 1
    return path, n


# ── 적재 ─────────────────────────────────────────────────────────────────────
def insert_provenance(con, rows):
    """system_provenance 적재. insert_locators 와 같은 규율 — **raise 0 · 사유 반환**.

    (evidence_locator 는 Unit C 정본 insert_locators 를 그대로 쓴다. 여기는 그 함수가 다루지
    않는 두 번째 테이블 전용이며, 동일하게 SAVEPOINT 격리 + 삽입 후 실재 재확인을 한다.)
    """
    rows = list(rows or [])
    rep = {"attempted": len(rows), "inserted": 0, "present": None, "skipped": False,
           "reason": None, "error": None}
    if not rows:
        rep["skipped"] = True
        rep["reason"] = "no_rows"
        return rep
    if not has_table(con, PROV_TABLE):
        rep["skipped"] = True
        rep["reason"] = "table_absent"
        return rep
    live = set(table_columns(con, PROV_TABLE))
    in_txn = bool(getattr(con, "in_transaction", False))
    sp = "sysprov_sp"
    try:
        if in_txn:
            con.execute("SAVEPOINT %s" % sp)
        n = 0
        for r in rows:
            cols = [c for c in r if c in live]
            if not cols:
                continue
            cur = con.execute("INSERT OR IGNORE INTO %s(%s) VALUES(%s)"
                              % (PROV_TABLE, ",".join(cols), ",".join("?" * len(cols))),
                              [r[c] for c in cols])
            n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        ids = [r["prov_id"] for r in rows if r.get("prov_id")]
        if ids and "prov_id" in live:
            rep["present"] = con.execute(
                "SELECT count(*) FROM %s WHERE prov_id IN (%s)"
                % (PROV_TABLE, ",".join("?" * len(ids))), ids).fetchone()[0]
            if rep["present"] < len(ids):
                rep["reason"] = "insert_dropped"
                rep["error"] = ("INSERT OR IGNORE 가 %d/%d 행을 버렸다(제약 불일치 의심)"
                                % (len(ids) - rep["present"], len(ids)))
        if in_txn:
            con.execute("RELEASE %s" % sp)
        else:
            con.commit()
        rep["inserted"] = n
    except Exception as ex:                  # noqa: BLE001 — 사유를 반환하되 저장을 막지 않는다
        rep["reason"] = "insert_failed"
        rep["error"] = "%s: %s" % (type(ex).__name__, ex)
        try:
            if in_txn:
                con.execute("ROLLBACK TO %s" % sp)
                con.execute("RELEASE %s" % sp)
            else:
                con.rollback()
        except Exception as ex2:             # noqa: BLE001
            rep["error"] += " / savepoint_rollback:%s" % type(ex2).__name__
    return rep


def _evloc_anchor(con):
    """locator 전용 앵커 갱신(NEW2.7 — audit_log tail 미점유)."""
    if has_table(con, "audit_meta"):
        con.execute("INSERT OR REPLACE INTO audit_meta(key,value) VALUES('evloc_head',?)",
                    (locator_checksum(con),))


def _guard_operating(ledger_path, batch_id, confirm):
    if is_operating_ledger(ledger_path) and confirm != batch_id:
        return ("운영 ledger 대상 write 는 사람의 명시 앵커가 필요하다 — "
                "`--confirm-operating %s` (§C-11 승인 대행 금지)" % (batch_id or "<batch-id>"))
    return None


def apply_plan(ledger_path, plan, batch_id, backup_dir=None, confirm_operating=None):
    """계획 적재. 실패는 삼키지 않고 status/reason 으로 반환한다."""
    res = {"status": "BLOCK", "reason": None, "batch_id": batch_id,
           "ledger": os.path.abspath(ledger_path)}
    guard = _guard_operating(ledger_path, batch_id, confirm_operating)
    if guard:
        res["reason"] = guard
        return res
    if not batch_id:
        res["reason"] = "batch_id_required"
        return res
    # ★ 계획 행에는 batch_id 가 이미 박혀 있다(loc_row/_prov_row 산출 시점). CLI 인자와 다르면
    #   적재는 성공하는데 `--rollback --batch-id <arg>` 가 0건을 찾는 **되돌릴 수 없는 고아 행**이
    #   생긴다(무음 유실 계열). 재태깅 대신 명시 BLOCK — 계획과 배치는 1:1 이어야 한다.
    plan_bid = plan.get("batch_id")
    if plan_bid and plan_bid != batch_id:
        res["reason"] = ("plan_batch_id_mismatch(plan=%s, arg=%s) — 같은 batch-id 로 다시 부르거나 "
                         "계획을 새로 만들어라(고아 행 방지)" % (plan_bid, batch_id))
        return res
    rows = plan["rows"]
    prov_rows = plan["prov_rows"]

    # ① 백업 선행(MF1.1) — Online Backup API + 사본 상대 대조. 실패하면 여기서 멈춘다.
    bdir = backup_dir or os.path.join(os.path.dirname(os.path.abspath(ledger_path)), "_backup")
    os.makedirs(bdir, exist_ok=True)
    bpath = os.path.join(bdir, "ledger_pre_%s.sqlite" % batch_id)
    try:
        res["backup"] = safe_backup(ledger_path, bpath)
    except Exception as ex:                  # noqa: BLE001 — 백업 실패 = 진행 금지
        res["reason"] = "backup_failed: %s: %s" % (type(ex).__name__, ex)
        return res

    # ② ledger 밖 이중 보관(excerpt) — DB write 보다 먼저.
    res["export"], res["export_rows"] = export_rows(ledger_path, batch_id, rows, prov_rows)

    db = StagingDB(ledger_path)
    try:
        if not has_table(db.con, LOC_TABLE):
            res["reason"] = ("evidence_locator 테이블 부재 — 이 프로세스에서 %s=1 로 스키마를 "
                             "먼저 만들어야 한다(플래그가 유일한 배포 스위치)" % EVLOC_FLAG_ENV)
            return res
        probe_before = integrity_probe(db.con, DATA_TABLES)
        ck_before = db.store_checksum()
        loc_before = locator_checksum(db.con)

        rep = insert_locators(db.con, rows, db_path=ledger_path)
        prep = insert_provenance(db.con, prov_rows)
        _evloc_anchor(db.con)
        db.con.commit()

        ck_after = db.store_checksum()
        # audit 행의 before/after 는 **store_checksum** 이다(NEW2.7). 백필은 nodes/edges/evidence
        # 를 안 건드리므로 before==after 이고, 그래서 verify_tail_state() 가 깨지지 않는다.
        db.audit_append(AUDIT_ACTOR, AUDIT_APPLY, batch_id, "ALLOW",
                        "loc=%d/%d prov=%d/%d" % (rep["inserted"], rep["attempted"],
                                                  prep["inserted"], prep["attempted"]),
                        ck_before, ck_after)
        probe_after = integrity_probe(db.con, DATA_TABLES)
        res.update({
            "locator_report": rep, "provenance_report": prep,
            "locator_checksum_before": loc_before,
            "locator_checksum_after": locator_checksum(db.con),
            "store_checksum_unchanged": ck_before == ck_after,
            "integrity_probe_unchanged": probe_before == probe_after,
            "audit_chain_intact": db.verify_chain(),
            "audit_tail_state": db.verify_tail_state(),
            "locator_tail_ok": verify_locator_tail(db.con),
            "mirror": evloc_mirror_path(ledger_path),
        })
        ok = (res["integrity_probe_unchanged"] and res["store_checksum_unchanged"]
              and res["audit_chain_intact"] and res["audit_tail_state"]
              and res["locator_tail_ok"]
              and rep["reason"] in (None, "no_rows") and prep["reason"] in (None, "no_rows"))
        res["status"] = "APPLIED" if ok else "PARTIAL"
        if not ok:
            res["reason"] = "verify_failed(loc=%s, prov=%s)" % (rep["reason"], prep["reason"])
        return res
    finally:
        db.close()


def rollback_batch(ledger_path, batch_id, backup_dir=None, confirm_operating=None):
    """롤백 — export(행수 대조) 후에만 DELETE. **DROP 없음**(MF1.3)."""
    res = {"status": "BLOCK", "reason": None, "batch_id": batch_id,
           "ledger": os.path.abspath(ledger_path)}
    guard = _guard_operating(ledger_path, batch_id, confirm_operating)
    if guard:
        res["reason"] = guard
        return res
    if not batch_id:
        res["reason"] = "batch_id_required"
        return res
    bdir = backup_dir or os.path.join(os.path.dirname(os.path.abspath(ledger_path)), "_backup")
    os.makedirs(bdir, exist_ok=True)
    try:
        res["backup"] = safe_backup(ledger_path, os.path.join(bdir, "ledger_pre_rb_%s.sqlite" % batch_id))
    except Exception as ex:                  # noqa: BLE001
        res["reason"] = "backup_failed: %s: %s" % (type(ex).__name__, ex)
        return res

    db = StagingDB(ledger_path)
    try:
        if not has_table(db.con, LOC_TABLE):
            res["reason"] = "table_absent"
            return res
        loc_cols = table_columns(db.con, LOC_TABLE)
        loc_rows = [dict(zip(loc_cols, r)) for r in db.con.execute(
            "SELECT %s FROM %s WHERE batch_id=?" % (",".join(loc_cols), LOC_TABLE), (batch_id,))]
        prov_rows = []
        if has_table(db.con, PROV_TABLE):
            pcols = table_columns(db.con, PROV_TABLE)
            prov_rows = [dict(zip(pcols, r)) for r in db.con.execute(
                "SELECT %s FROM %s WHERE batch_id=?" % (",".join(pcols), PROV_TABLE), (batch_id,))]
        if not loc_rows and not prov_rows:
            res["status"] = "NOOP"
            res["reason"] = "batch_not_found"
            return res

        path = os.path.join(os.path.dirname(os.path.abspath(ledger_path)),
                            "evidence_locator_rollback_%s.jsonl" % batch_id)
        with open(path, "w", encoding="utf-8") as f:
            for r in loc_rows:
                f.write(json.dumps({"_t": LOC_TABLE, **r}, ensure_ascii=False) + "\n")
            for r in prov_rows:
                f.write(json.dumps({"_t": PROV_TABLE, **r}, ensure_ascii=False) + "\n")
        exported = sum(1 for _ in open(path, "r", encoding="utf-8"))
        res["export"] = path
        res["exported"] = exported
        if exported != len(loc_rows) + len(prov_rows):
            res["reason"] = ("export 행수 불일치(%d != %d) — DELETE 하지 않음"
                             % (exported, len(loc_rows) + len(prov_rows)))
            return res

        probe_before = integrity_probe(db.con, DATA_TABLES)
        ck_before = db.store_checksum()
        d1 = db.con.execute("DELETE FROM %s WHERE batch_id=?" % LOC_TABLE, (batch_id,)).rowcount
        d2 = 0
        if has_table(db.con, PROV_TABLE):
            d2 = db.con.execute("DELETE FROM %s WHERE batch_id=?" % PROV_TABLE, (batch_id,)).rowcount
        _evloc_anchor(db.con)
        db.con.commit()
        ck_after = db.store_checksum()
        db.audit_append(AUDIT_ACTOR, AUDIT_ROLLBACK, batch_id, "ALLOW",
                        "deleted loc=%d prov=%d (export=%s)" % (d1, d2, os.path.basename(path)),
                        ck_before, ck_after)
        res.update({
            "deleted_locator": d1, "deleted_provenance": d2,
            "integrity_probe_unchanged": probe_before == integrity_probe(db.con, DATA_TABLES),
            "store_checksum_unchanged": ck_before == ck_after,
            "audit_chain_intact": db.verify_chain(),
            "audit_tail_state": db.verify_tail_state(),
            "locator_tail_ok": verify_locator_tail(db.con),
            "remaining_in_batch": db.con.execute(
                "SELECT count(*) FROM %s WHERE batch_id=?" % LOC_TABLE, (batch_id,)).fetchone()[0],
        })
        ok = (d1 == len(loc_rows) and d2 == len(prov_rows) and res["remaining_in_batch"] == 0
              and res["integrity_probe_unchanged"] and res["audit_chain_intact"]
              and res["audit_tail_state"] and res["locator_tail_ok"])
        res["status"] = "ROLLED_BACK" if ok else "PARTIAL"
        if not ok:
            res["reason"] = "verify_failed"
        return res
    finally:
        db.close()


# ── 리포트 ───────────────────────────────────────────────────────────────────
def print_dryrun(plan):
    st = plan["stats"]
    sm = plan["sources"]["sessions"]
    dm = plan["sources"]["docs"]
    tot = st["evidence_total"] or 1
    print("== 회수 가능 구간(실측) ==")
    print("  세션로그 root: %s (스캔=%s)" % (sm.get("root"), sm.get("scanned")))
    print("  파일 %d · 레코드 %d · 텍스트 %.1fM자" % (sm.get("files", 0), sm.get("records", 0),
                                                sm.get("chars", 0) / 1e6))
    print("  타임스탬프 범위: %s ~ %s" % (st["session_window"]["min_ts"], st["session_window"]["max_ts"]))
    print("  문서(2차) root: %s · 파일 %d" % (dm.get("roots"), dm.get("files", 0)))
    print("\n== 등급별 회수율 (evidence %d건 기준) ==" % st["evidence_total"])
    print("  %-26s %-4s %6s %8s  %s" % ("match_method", "등급", "건수", "비율", "뜻"))
    for m, n in sorted(st["by_method"].items(), key=lambda kv: -kv[1]):
        conf, desc = GRADE.get(m, ("?", ""))
        print("  %-26s %-4s %6d %7.1f%%  %s" % (m, conf, n, 100.0 * n / tot, desc))
    print("\n  1차 출처(원문 대화) = %d (%.1f%%)   ← G7 분자는 이것만"
          % (st["primary_source"], 100.0 * st["primary_ratio"]))
    print("  전체 locator 부착 = %d (%.1f%%)  · 미회수 = %d"
          % (st["planned_rows"], 100.0 * st["any_locator_ratio"], st["unmatched"]))
    print("\n== 화자별 (총/1차/전체) ==")
    for k, v in sorted(st["by_speaker"].items()):
        print("  %-8s %4d / %4d / %4d" % (k, v["total"], v["primary"], v["any"]))
    print("\n  이미 locator 보유 evidence: %d" % st["already_has_locator"])
    print("  적재 예정 locator 행: %d · system_provenance 행: %d"
          % (len(plan["rows"]), len(plan["prov_rows"])))


# ── selftest (temp 홈 · 운영 미접촉) ─────────────────────────────────────────
def _selftest():
    import shutil
    import tempfile

    from binggu_schema import evloc_env

    ok = tot = 0

    def chk(name, cond, extra=""):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("[PASS] " if cond else "[FAIL] ") + name + (("  " + str(extra)) if extra else ""))

    # selftest 홈 이름은 기존 파일들과 충돌하지 않는 전용 접두사(bfloc_*)를 쓴다 —
    # 과거 home5/home7 충돌로 남의 outcome 이 오염된 사고 계열 회피.
    work = tempfile.mkdtemp(prefix="bfloc_home_")
    try:
        sess = os.path.join(work, "sessions", "proj")
        docs = os.path.join(work, "docs")
        os.makedirs(sess)
        os.makedirs(docs)
        ledger = os.path.join(work, "ledger.sqlite")

        owner_sent = "회상 도장은 쓰는 순간의 AI가 가장 정확하게 판정한다."
        ai_sent = "지연 import 누락을 except 가 삼키면 무증상 결함이 된다."
        echo_sent = "번호축이 밀리면 사람이 본 preview 와 다른 문장이 저장된다."
        gone_sent = "이 문장은 어떤 원본에도 남아 있지 않은 옛 판단이다."

        with evloc_env(True):
            db = StagingDB(ledger)
            try:
                for i, (sent, spk, ca) in enumerate([
                        (owner_sent, "owner", "2026-07-20T00:00:00Z"),
                        (ai_sent, "ai", "2026-07-20T00:00:00Z"),
                        (echo_sent, "owner", "2026-07-20T00:00:00Z"),
                        (gone_sent, None, "2026-07-20T00:00:00Z")]):
                    nid, eid = "N%d" % i, "EVC-T%d" % i
                    db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,"
                                   "speaker,created_at) VALUES(?,?,?,0,'active',?,?)",
                                   (nid, "judgment", sent, spk, ca))
                    db.con.execute("INSERT INTO evidence(evidence_id,sentence,source_pointer_id,"
                                   "source_hash,redaction_policy,pack_id,created_at) "
                                   "VALUES(?,?,?,?,'v1','p1',?)",
                                   (eid, sent, "conv-self:%d" % i, "h%d" % i, ca))
                    db.con.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,"
                                   "state,evidence_refs,pack_id,created_at) "
                                   "VALUES(?,'evidence_supports',?,?,0,'active',?,'p1',?)",
                                   ("E%d" % i, eid, nid, json.dumps([eid]), ca))
                db.con.commit()
                db.audit_append("human", "insert", "p1", "ALLOW", None,
                                db.store_checksum(), db.store_checksum())
            finally:
                db.close()

        def rec(uuid, typ, role, content, ts):
            return json.dumps({"type": typ, "uuid": uuid, "sessionId": "S-TEST",
                               "timestamp": ts,
                               "message": {"role": role, "content": content}}, ensure_ascii=False)

        with open(os.path.join(sess, "a.jsonl"), "w", encoding="utf-8") as f:
            f.write(rec("u-1", "user", "user",
                        "앞뒤 맥락 정리하면서 정했다. %s 그러니 도장은 자동으로 찍자." % owner_sent,
                        "2026-07-19T10:00:00Z") + "\n")
            f.write(rec("a-1", "assistant", "assistant",
                        [{"type": "text", "text": "원인을 정리하면 이렇다. %s 그래서 백필이 필요하다." % ai_sent}],
                        "2026-07-19T11:00:00Z") + "\n")
            f.write(rec("t-1", "assistant", "assistant",
                        [{"type": "tool_use", "input": {"text": "저장 후보 1. %s" % echo_sent}}],
                        "2026-07-19T12:00:00Z") + "\n")
        with open(os.path.join(docs, "traj_x.md"), "w", encoding="utf-8") as f:
            f.write("# traj\n- 교훈: %s (요약본 라인)\n" % echo_sent)

        plan = build_plan(ledger, "bfT1", session_root=os.path.join(work, "sessions"),
                          doc_roots=[docs])
        by = {s["evidence_id"]: s for s in plan["sheet"]}
        chk("1 owner 발화 = session_exact/T1(1차 출처)",
            by["EVC-T0"]["match_method"] == "session_exact" and by["EVC-T0"]["confidence"] == "T1",
            by["EVC-T0"]["match_method"])
        chk("2 ai 발화 = session_exact/T1(assistant 턴·화자 일치)",
            by["EVC-T1"]["match_method"] == "session_exact")
        chk("3 도구 입력 안에서만 발견 → md 2차가 우선(메아리를 1차로 올리지 않음)",
            by["EVC-T2"]["match_method"] in ("md_exact", "session_echo")
            and by["EVC-T2"]["confidence"] in ("T2", "T3"), by["EVC-T2"]["match_method"])
        chk("4 원본 부재 → match_method='none'(빈칸 금지·사유 기록)",
            by["EVC-T3"]["match_method"] == "none" and bool(by["EVC-T3"]["note"]),
            by["EVC-T3"]["note"])
        chk("5 미회수는 evidence_locator 행을 만들지 않는다(증거 축 오염 금지)",
            all(r["evidence_id"] != "EVC-T3" for r in plan["rows"]))
        chk("6 미회수도 system_provenance 에는 기록된다(사유 명시)",
            any(r["subject_id"] == "EVC-T3" and "none" in r["frontmatter_json"]
                for r in plan["prov_rows"]))
        chk("7 locator 는 원본 좌표(uuid+offset+len)", by["EVC-T0"]["locator"].startswith("uuid:u-1:off:"),
            by["EVC-T0"]["locator"])
        chk("8 excerpt 는 원문 슬라이스(정규화 문자열 아님)",
            next(r for r in plan["rows"] if r["evidence_id"] == "EVC-T0")["excerpt_text"] == owner_sent)
        chk("9 container_sha != excerpt_sha(독립 컨테이너 — 자기참조 아님)",
            next(r for r in plan["rows"] if r["evidence_id"] == "EVC-T0")["container_sha"]
            != excerpt_sha(owner_sent))
        chk("10 1차 회수율 집계가 2차/메아리를 분자에 넣지 않는다",
            plan["stats"]["primary_source"] == 2, plan["stats"]["by_method"])

        # dry-run 이 ledger 를 건드리지 않았는가
        st_before = os.stat(ledger)
        plan2 = build_plan(ledger, "bfT1", session_root=os.path.join(work, "sessions"),
                           doc_roots=[docs])
        chk("11 dry-run 재실행 결정적(같은 계획·같은 loc_id)",
            [(r["evidence_id"], r["locator"], r["loc_id"]) for r in plan["rows"]]
            == [(r["evidence_id"], r["locator"], r["loc_id"]) for r in plan2["rows"]])
        chk("12 dry-run 은 대상 ledger 에 write 0(size/mtime 불변)",
            (st_before.st_size, st_before.st_mtime) == (os.stat(ledger).st_size,
                                                        os.stat(ledger).st_mtime))

        # apply
        with evloc_env(True):
            res = apply_plan(ledger, plan, "bfT1")
        chk("13 --apply status=APPLIED", res["status"] == "APPLIED", res.get("reason"))
        chk("14 safe_backup 선행(사본 검증 통과)", bool(res.get("backup", {}).get("verified")))
        chk("15 기존 nodes/edges/evidence 무손실(integrity_probe 불변)",
            res["integrity_probe_unchanged"])
        chk("16 store_checksum 불변(기존 행 UPDATE/DELETE 0)", res["store_checksum_unchanged"])
        chk("17 audit chain INTACT + tail_state 유지(NEW2.7 — locator 해시가 tail 미점유)",
            res["audit_chain_intact"] and res["audit_tail_state"])
        chk("18 locator 전용 앵커(evloc_head) 일치", res["locator_tail_ok"])
        chk("19 excerpt ledger 밖 이중 보관(batch export + mirror jsonl)",
            os.path.exists(res["export"]) and os.path.exists(res["mirror"]))

        con = sqlite3.connect(ledger)
        n_loc = con.execute("SELECT count(*) FROM evidence_locator WHERE batch_id='bfT1'").fetchone()[0]
        n_prov = con.execute("SELECT count(*) FROM system_provenance WHERE batch_id='bfT1'").fetchone()[0]
        n_elig = con.execute("SELECT count(*) FROM system_provenance WHERE evidence_eligible!=0").fetchone()[0]
        con.close()
        chk("20 계획 행수 == 적재 행수", n_loc == len(plan["rows"]), (n_loc, len(plan["rows"])))
        chk("21 system_provenance 는 전건 evidence_eligible=0(증거 불인정)", n_elig == 0)

        # 멱등
        with evloc_env(True):
            res2 = apply_plan(ledger, plan, "bfT1")
        con = sqlite3.connect(ledger)
        n_loc2 = con.execute("SELECT count(*) FROM evidence_locator WHERE batch_id='bfT1'").fetchone()[0]
        con.close()
        chk("22 재적재 멱등(UNIQUE 4튜플 → 행수 불변)", n_loc2 == n_loc, (n_loc, n_loc2))

        # 롤백
        rb = rollback_batch(ledger, "bfT1")
        con = sqlite3.connect(ledger)
        left = con.execute("SELECT count(*) FROM evidence_locator WHERE batch_id='bfT1'").fetchone()[0]
        left_p = con.execute("SELECT count(*) FROM system_provenance WHERE batch_id='bfT1'").fetchone()[0]
        ev_left = con.execute("SELECT count(*) FROM evidence").fetchone()[0]
        nd_left = con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        con.close()
        chk("23 --rollback status=ROLLED_BACK", rb["status"] == "ROLLED_BACK", rb.get("reason"))
        chk("24 export 후에만 DELETE(행수 대조 통과)", os.path.exists(rb["export"])
            and rb["exported"] == rb["deleted_locator"] + rb["deleted_provenance"])
        chk("25 배치 전량 삭제(잔여 0)", left == 0 and left_p == 0)
        chk("26 롤백이 기존 데이터 무접촉(evidence/nodes 보존)", ev_left == 4 and nd_left == 4)
        chk("27 롤백 후에도 audit chain·tail·locator 앵커 정상",
            rb["audit_chain_intact"] and rb["audit_tail_state"] and rb["locator_tail_ok"])
        chk("28 롤백은 테이블 삭제문을 쓰지 않는다(소스에 해당 DDL 리터럴 0건)",
            "DROP" + " TABLE" not in open(os.path.abspath(__file__), encoding="utf-8").read())

        # 테이블 부재(플래그 OFF) ledger — apply 는 조용히 성공하지 않고 사유를 남긴다
        led2 = os.path.join(work, "ledger_off.sqlite")
        db2 = StagingDB(led2)
        db2.close()
        plan3 = build_plan(led2, "bfT2", session_root=os.path.join(work, "sessions"), doc_roots=[docs])
        res3 = apply_plan(led2, plan3, "bfT2")
        chk("29 evidence_locator 부재 ledger → BLOCK + 플래그 안내(무음 no-op 금지)",
            res3["status"] == "BLOCK" and EVLOC_FLAG_ENV in (res3.get("reason") or ""),
            res3.get("reason"))
        chk("30 부재 ledger 에도 DDL 을 만들지 않는다(테이블 여전히 부재)",
            not has_table(sqlite3.connect(led2), LOC_TABLE))

        # 운영 ledger 앵커 가드
        res4 = apply_plan(_plat.default_ledger(), {"rows": [], "prov_rows": []}, "bfT9")
        chk("31 운영 ledger 대상은 사람 앵커(--confirm-operating) 없이는 BLOCK",
            res4["status"] == "BLOCK" and "confirm-operating" in (res4["reason"] or ""),
            res4.get("reason"))
        chk("32 앵커 가드가 백업/DDL 보다 먼저 걸린다(운영 접촉 0)", "backup" not in res4)

        sheet = write_sheet(plan, os.path.join(work, "sheet.md"))
        body = open(sheet, encoding="utf-8").read()
        chk("33 검수 시트에 등급표·1차/2차 분리 명시",
            "1차 출처" in body and "match_method" in body)

        # 계획의 batch_id 와 CLI batch-id 가 어긋나면 되돌릴 수 없는 고아 행이 생긴다 → 명시 BLOCK.
        res5 = apply_plan(ledger, plan, "bfOTHER")
        chk("34 plan batch_id != --batch-id → BLOCK(고아 행 방지)",
            res5["status"] == "BLOCK" and "plan_batch_id_mismatch" in (res5["reason"] or ""),
            res5.get("reason"))
        chk("35 불일치 BLOCK 도 백업/DDL 이전에 걸린다", "backup" not in res5)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\nRESULT: %d/%d" % (ok, tot))
    print("GATE: %s" % ("GO" if ok == tot else "BLOCK"))
    return ok == tot


def main(argv=None):
    ap = argparse.ArgumentParser(description="evidence_locator 백필 (dry-run 기본)")
    ap.add_argument("--ledger", default=None, help="대상 ledger(기본: 운영 ledger · dry-run 은 읽기 전용)")
    ap.add_argument("--sessions", default=DEFAULT_SESSION_ROOT, help="Claude 세션로그 root")
    ap.add_argument("--docs", action="append", default=None, help="2차 출처(md) root — 반복 지정 가능")
    ap.add_argument("--no-docs", action="store_true", help="2차 출처 스캔 생략")
    ap.add_argument("--no-sessions", action="store_true", help="세션로그 스캔 생략")
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--sheet", default=None, help="검수 시트(markdown) 출력 경로")
    ap.add_argument("--plan", default=None, help="계획 JSON 출력/입력 경로")
    ap.add_argument("--use-plan", default=None, help="이 계획 JSON 을 그대로 적재(재스캔 없음)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--confirm-operating", default=None,
                    help="운영 ledger write 앵커 — batch-id 와 동일 문자열을 사람이 직접 입력")
    ap.add_argument("--backup-dir", default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1

    ledger = args.ledger or _plat.default_ledger()
    if not os.path.exists(ledger):
        print(json.dumps({"status": "BLOCK", "reason": "NO_LEDGER", "detail": ledger},
                         ensure_ascii=False))
        return 2

    if args.rollback:
        res = rollback_batch(ledger, args.batch_id, args.backup_dir, args.confirm_operating)
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        return 0 if res["status"] in ("ROLLED_BACK", "NOOP") else 1

    batch_id = args.batch_id or ("dryrun_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    if args.use_plan:
        with open(args.use_plan, encoding="utf-8") as f:
            plan = json.load(f)
        batch_id = plan.get("batch_id") or batch_id
    else:
        docs = [] if args.no_docs else (args.docs if args.docs is not None else list(DEFAULT_DOC_ROOTS))
        plan = build_plan(ledger, batch_id,
                          session_root=None if args.no_sessions else args.sessions,
                          doc_roots=docs,
                          progress=None if args.quiet else (lambda m: print(m, flush=True)))
    if not args.quiet:
        print_dryrun(plan)
    if args.plan:
        with open(args.plan, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        print("\nplan → %s" % args.plan)
    if args.sheet:
        print("sheet → %s" % write_sheet(plan, args.sheet))

    if not args.apply:
        print("\nDRY-RUN — 대상 ledger write 0. 적용하려면 --apply --batch-id <id>")
        return 0
    if not args.batch_id:
        print(json.dumps({"status": "BLOCK", "reason": "batch_id_required"}, ensure_ascii=False))
        return 1
    res = apply_plan(ledger, plan, args.batch_id, args.backup_dir, args.confirm_operating)
    print(json.dumps({k: v for k, v in res.items() if k != "sheet"},
                     ensure_ascii=False, indent=2, default=str))
    return 0 if res["status"] == "APPLIED" else 1


if __name__ == "__main__":
    sys.exit(main())
