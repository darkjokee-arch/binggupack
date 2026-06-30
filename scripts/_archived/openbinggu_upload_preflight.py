# -*- coding: utf-8 -*-
"""OpenCrab private upload preflight — G1~G7 fail-closed chain (dry-run, 전송 미구현).

설계 정본: docs/BINGGUPACK_OPENCRAB_PRIVATE_UPLOAD_PREFLIGHT_DESIGN.md (r2)
fixture 정본: docs/BINGGUPACK_OPENCRAB_UPLOAD_PREFLIGHT_FIXTURE_SPEC.md

체인(순차·fail-closed — 첫 FAIL 에서 BLOCK + reason_code, 이후 게이트 미실행):
  G1 schema/validate          (openbinggu_pack_validate.validate_pack 어댑터 + 코드 승격)
  G2 source pointer 전건 clean (신규 수집기: 키 부재 row skip + classify_source_pointers)
  G3 SECRET + PII hit 0        (incoming_to_staging.SECRET_PATTERNS + realpack_gate.PII_PATTERNS)
  G4 ephemeral/conv-self 제외   (신규 check_ephemeral_excluded — 메타 부재 = fail-closed)
  G5 candidate-only            (신규 check_candidate_only — temp SQLite 전용, PRAGMA query_only=ON)
  G6 직렬화 leak + 크기 캡       (consume() view + realpack_gate.LEAK_PATTERNS + 20K cap)
  G7 owner 승인 문구 정확 일치    (strict + publish_decision 재사용)

전 게이트 PASS 시에만: staged bundle 생성(temp) + bundle_hash8 + 승인 문구 + 1회용 승인 토큰.
bundle_hash8 = sha256( Σ (filename + b"\\0" + raw bytes), PACK_FILES 고정 순서 ) 앞 8 hex.
승인 문구 기본형 = "UPLOAD <pack_id> <bundle_hash8> IRREVERSIBLE"
  — 비가역 미입증 = 비가역 간주(fail-closed). 가역 입증(별도 실측 GO) 시에만 IRREVERSIBLE 생략.

금지(전 구간): real staging DB(tmp/real_staging) 접근 0 · live/deploy/외부 네트워크 0 ·
  OpenCrab MCP 호출 0 · confirmed 자동 생성 0 · git 0 · temp 는 tempfile.mkdtemp 만.
전송(send_staged_bundle)은 **의도적으로 미구현**(NotImplementedError) — 실 업로드 = 별도 owner GO.

CLI:
  python openbinggu_upload_preflight.py --selftest
  python openbinggu_upload_preflight.py <pack_dir> [<temp_staging_db>]
"""
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "workers_port"))

from openbinggu_pack_validate import validate_pack                      # noqa: E402
from openbinggu_scope_envelope_dryrun import (                          # noqa: E402
    classify_source_pointers, publish_decision, PUBLISH_REGRESSION_STATE)
from openbinggu_pack_consumer_smoke import consume                      # noqa: E402
import openbinggu_incoming_to_staging as v011                           # noqa: E402
try:
    # private 빌드 트리(workers_port) 동봉 환경 — 정본 그대로 사용
    from realpack_gate import (                                         # noqa: E402
        PII_PATTERNS, LEAK_PATTERNS, PACK_FILES, MAX_VIEW_CHARS)
except ImportError:
    # 공개 clone fallback — 정본과 동일 값 내장(마커 문자열은 조각 결합 — scanner 자기검출 회피).
    PACK_FILES = ["manifest.json", "nodes.jsonl", "edges.jsonl",
                  "evidence_index.jsonl", "evidence_chunk.jsonl"]
    MAX_VIEW_CHARS = 20000
    PII_PATTERNS = [
        ("pii_bizno_fmt", re.compile(r"\d{3}-\d{2}-\d{5}")),
        ("pii_bizno_bare", re.compile(r"(?<!\d)\d{10}(?!\d)")),
        ("pii_rrn", re.compile(r"\b\d{6}-\d{7}\b")),
        ("pii_phone", re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")),
        ("pii_email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ]
    LEAK_PATTERNS = [
        ("win_abs_path", re.compile(r"[A-Za-z]:\\\\?")),
        ("unix_home_path", re.compile(r"/(?:Users|home)/[A-Za-z0-9_]+")),
        ("backup_marker", re.compile("_bac" "kup")),
        ("cloud_reset_marker", re.compile("cloud_" "reset_" r"\d+")),
    ]

FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "synthetic" / "upload_preflight"
BASE_POK = FIXTURE_ROOT / "base_pok"
CASES_DIR = FIXTURE_ROOT / "cases"
OTHER_PACK_ID = "other_synth_pack"

# 승인 문구 1차 거름 regex — 이후 기대 문자열과 정확 일치 비교(regex 만으로 PASS 금지)
_PHRASE_RX = re.compile(r"^UPLOAD [A-Za-z0-9_\-]+ [0-9a-f]{8}( IRREVERSIBLE)?$")
_EPHEMERAL_MARKS = ("conv_self", "ephemeral")
DEFAULT_DB_ROWS = [{"pack": "self", "status": "candidate"},
                   {"pack": "self", "status": "validated"},
                   {"pack": "self", "status": "candidate"}]


def _gres(gate, ok, reason_codes, counts):
    """GateResult — raw 값 미포함(reason_code/count 만)."""
    return {"gate": gate, "ok": bool(ok), "reason_codes": list(reason_codes), "counts": counts}


# ---------------------------------------------------------------- G1
def gate_g1_schema(manifest):
    """validate_pack 어댑터 — hard flag stop 은 G1_HARD_FLAG_TRUE 로 승격, REVIEW_ONLY 도 FAIL."""
    res = validate_pack(manifest)
    codes = []
    if res["verdict"] == "PASS":
        return _gres("G1", True, [], {"stops": 0, "reviews": 0})
    if res["stops"]:
        hard = [s for s in res["stops"] if str(s).startswith("hard-default 위반")]
        other = [s for s in res["stops"] if not str(s).startswith("hard-default 위반")]
        if hard:
            codes.append("G1_HARD_FLAG_TRUE")
        if other:
            codes.append("G1_SCHEMA_STOP")
    elif res["verdict"] == "REVIEW_ONLY":
        codes.append("G1_REVIEW_ONLY")
    return _gres("G1", False, codes,
                 {"stops": len(res["stops"]), "reviews": len(res["reviews"])})


# ---------------------------------------------------------------- G2
def collect_source_pointers(pack_payload):
    """신규 수집기 — 키 부재 row 는 skip (G2_SRCPTR_EMPTY_SET / UNKNOWN reason 구분용)."""
    pointers = []
    for row in pack_payload.get("evidence_index", []):
        if "source_path" in row:
            pointers.append(row["source_path"])
    for row in pack_payload.get("evidence_chunk", []):
        meta = row.get("evidence_meta") or {}
        if "raw_pointer" in meta:
            pointers.append(meta["raw_pointer"])
    return pointers


def gate_g2_source_pointers(pack_payload):
    pointers = collect_source_pointers(pack_payload)
    if not pointers:
        return _gres("G2", False, ["G2_SRCPTR_EMPTY_SET"], {"pointers": 0})
    agg = classify_source_pointers(pointers)
    counts = dict(agg["counts"])
    counts["pointers"] = len(pointers)
    codes = []
    if counts.get("dirty", 0):
        codes.append("G2_SRCPTR_DIRTY")
    if counts.get("unknown", 0):
        codes.append("G2_SRCPTR_UNKNOWN")
    return _gres("G2", not codes, codes, counts)


# ---------------------------------------------------------------- G3
def gate_g3_secret_pii(raw_texts):
    """pack 파일 5종 raw 전수 — SECRET + PII hit 0 (매치 원문 미기록, count 만)."""
    hits = {}
    for _fname, text in raw_texts.items():
        for pat in v011.SECRET_PATTERNS:
            if pat.search(text):
                hits["secret_pat"] = hits.get("secret_pat", 0) + 1
        for code_, rx in PII_PATTERNS:
            n = len(rx.findall(text))
            if n:
                hits[code_] = hits.get(code_, 0) + n
    codes = []
    if hits.get("secret_pat"):
        codes.append("G3_SECRET_HIT")
    if any(k.startswith("pii_") for k in hits):
        codes.append("G3_PII_HIT")
    return _gres("G3", not hits, codes, {"hit_kinds": sorted(hits), "hit_total": sum(hits.values())})


# ---------------------------------------------------------------- G4
def check_ephemeral_excluded(pack_payload):
    """conv-self/ephemeral chunk 0건 + source_kind 메타 전건 존재 (부재 = fail-closed)."""
    scanned = 0
    ephemeral = 0
    flag_missing = 0
    for row in pack_payload.get("evidence_chunk", []):
        scanned += 1
        meta = row.get("evidence_meta") or {}
        kind = meta.get("source_kind")
        if kind is None:
            flag_missing += 1
        elif any(m in str(kind) for m in _EPHEMERAL_MARKS):
            ephemeral += 1
    return {"ok": ephemeral == 0 and flag_missing == 0,
            "scanned": scanned, "ephemeral": ephemeral, "flag_missing": flag_missing}


def gate_g4_ephemeral(pack_payload):
    r = check_ephemeral_excluded(pack_payload)
    codes = []
    if r["ephemeral"]:
        codes.append("G4_EPHEMERAL_INCLUDED")
    if r["flag_missing"]:
        codes.append("G4_EXCLUDE_FLAG_MISSING")
    return _gres("G4", r["ok"], codes,
                 {"scanned": r["scanned"], "ephemeral": r["ephemeral"],
                  "flag_missing": r["flag_missing"]})


# ---------------------------------------------------------------- G5
def check_candidate_only(db_path, pack_id):
    """temp SQLite 전용 read-only 전수 쿼리. real staging DB 미접촉(호출자 책임 + temp 경로만).
    PASS = pack_id row > 0 AND 전건 status IN (candidate, validated) AND confirmed/promotion 플래그 0."""
    if not db_path or not Path(db_path).exists():
        return {"ok": False, "reason": "G5_DB_UNREADABLE", "total": 0, "non_candidate": 0}
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA query_only=ON")
            total = conn.execute(
                "SELECT COUNT(*) FROM staging_nodes WHERE pack_id=?", (pack_id,)).fetchone()[0]
            non_candidate = conn.execute(
                "SELECT COUNT(*) FROM staging_nodes WHERE pack_id=? AND "
                "(status NOT IN ('candidate','validated') OR promotion_allowed<>0 OR confirmed<>0)",
                (pack_id,)).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return {"ok": False, "reason": "G5_DB_UNREADABLE", "total": 0, "non_candidate": 0}
    if total == 0:
        return {"ok": False, "reason": "G5_EMPTY_PACK", "total": 0, "non_candidate": 0}
    if non_candidate:
        return {"ok": False, "reason": "G5_NON_CANDIDATE_FOUND",
                "total": total, "non_candidate": non_candidate}
    return {"ok": True, "reason": None, "total": total, "non_candidate": 0}


def gate_g5_candidate_only(db_path, pack_id):
    r = check_candidate_only(db_path, pack_id)
    return _gres("G5", r["ok"], [r["reason"]] if r["reason"] else [],
                 {"total": r["total"], "non_candidate": r["non_candidate"]})


# ---------------------------------------------------------------- G6
def gate_g6_serialized_leak(pack_dir):
    """업로드 직전 직렬화 본문(consume view) 재스캔 — leak 0 AND 크기 캡 이하."""
    view = consume(pack_dir)
    view_text = json.dumps(view, ensure_ascii=False, separators=(", ", ": "))
    leak_kinds = [code_ for code_, rx in LEAK_PATTERNS if rx.search(view_text)]
    codes = []
    if leak_kinds:
        codes.append("G6_SERIALIZED_LEAK")
    if len(view_text) > MAX_VIEW_CHARS:
        codes.append("G6_VIEW_OVERSIZE")
    return _gres("G6", not codes, codes,
                 {"view_chars": len(view_text), "cap": MAX_VIEW_CHARS,
                  "leak_kinds": leak_kinds})


# ---------------------------------------------------------------- G7
def expected_approval_phrase(pack_id, bundle_hash8, irreversible):
    suffix = " IRREVERSIBLE" if irreversible else ""
    return "UPLOAD %s %s%s" % (pack_id, bundle_hash8, suffix)


def check_approval_phrase(approval_input, pack_id, bundle_hash8, irreversible):
    """strict — 대소문자·단일 공백·전후 공백 0. regex 1차 거름 후 정확 일치 비교."""
    if approval_input is None:
        return {"ok": False, "missing": True, "reason": None}
    expected = expected_approval_phrase(pack_id, bundle_hash8, irreversible)
    if not _PHRASE_RX.match(approval_input):
        return {"ok": False, "missing": False, "reason": "G7_CONFIRM_MISMATCH"}
    if approval_input != expected:
        return {"ok": False, "missing": False, "reason": "G7_CONFIRM_MISMATCH"}
    return {"ok": True, "missing": False, "reason": None}


def gate_g7_approval(approval_input, pack_id, bundle_hash8, irreversible,
                     regression_state, prior_gate_names):
    ph = check_approval_phrase(approval_input, pack_id, bundle_hash8, irreversible)
    codes = []
    if ph["reason"]:
        codes.append(ph["reason"])
    items = [{"item_id": g, "mask_result": "clean"} for g in prior_gate_names]
    state = regression_state if regression_state is not None else dict(PUBLISH_REGRESSION_STATE)
    dec = publish_decision(items, ph["ok"], state)
    for c in dec["reason_codes"]:
        if c in ("NOT_APPROVED", "REGRESSION_FAIL") and c not in codes:
            codes.append(c)
    ok = ph["ok"] and dec["verdict"] == "ALLOW"
    return _gres("G7", ok, codes,
                 {"publish_verdict": dec["verdict"], "irreversible": bool(irreversible)})


# ---------------------------------------------------------------- bundle hash / staged / 토큰
# 4cli 12지시 §6 (R3 결론): hash8 = owner 타이핑용 **표시 전용**. pinning·audit·검증 = full SHA-256.
def compute_bundle_hash_full(pack_dir):
    """sha256( Σ (filename + b"\\0" + raw bytes), PACK_FILES 고정 순서 ) full 64 hex. 결정적.
    검증·audit·토큰 바인딩의 유일 기준 — hash8 만으로 통과하는 경로 없음."""
    pack_dir = Path(pack_dir)
    h = hashlib.sha256()
    for fname in PACK_FILES:
        h.update(fname.encode("utf-8"))
        h.update(b"\0")
        h.update((pack_dir / fname).read_bytes())
    return h.hexdigest()


def compute_bundle_hash8(pack_dir):
    """표시 전용 단축형 — 승인 문구(owner 타이핑)에만 사용. 검증 기준 아님."""
    return compute_bundle_hash_full(pack_dir)[:8]


def verify_rehash_before_send(staged_dir, approved_hash_full):
    """전송 직전 재해시 — **full SHA-256 비교** (불일치 = ABORT, 승인~전송 사이 변경 = 승인 무효)."""
    if not isinstance(approved_hash_full, str) or len(approved_hash_full) != 64:
        return {"ok": False, "reason_code": "BUNDLE_HASH_MISMATCH_ABORT"}  # hash8 전달 = 즉시 거부
    actual = compute_bundle_hash_full(staged_dir)
    if actual != approved_hash_full:
        return {"ok": False, "reason_code": "BUNDLE_HASH_MISMATCH_ABORT"}
    return {"ok": True, "reason_code": None}


def issue_approval_tok(pack_id, bundle_hash_full):
    """1회용 승인 토큰 — full hash 바인딩. APPROVED→UPLOADING 진입 시 소모."""
    ts = time.strftime("%Y%m%dT%H%M%S")
    tid = hashlib.sha256(("%s|%s|%s" % (pack_id, bundle_hash_full, ts)).encode("utf-8")).hexdigest()[:16]
    return {"tok_id": tid, "pack_id": pack_id,
            "pack_sha_full": bundle_hash_full, "pack_sha8": bundle_hash_full[:8], "ts": ts}


def send_staged_bundle(staged_dir, appr_tok, consumed_registry, approved_hash_full):
    """전송 진입점 — 토큰 1회성 + full hash 재해시까지만 구현. 실 전송은 의도적 미구현.

    실 업로드(OpenCrab 호출·네트워크)는 **별도 owner GO** — 본 구현은 자리만 고정(fail-closed).
    참고(용어 분리·12지시 §8): live 워커의 버전 rollback 은 가능하지만,
    OpenCrab 업로드의 취소·삭제·비공개 전환은 **불가**(read-only 실측) — 업로드는 비가역이다.
    """
    tid = appr_tok["tok_id"]
    if tid in consumed_registry:
        return {"ok": False, "sent": False, "reason_code": "APPROVAL_TOKEN_CONSUMED"}
    consumed_registry.add(tid)  # UPLOADING 진입 = 토큰 소모 (ABORT 여도 소각 유지)
    re_h = verify_rehash_before_send(staged_dir, approved_hash_full)
    if not re_h["ok"]:
        return {"ok": False, "sent": False, "reason_code": re_h["reason_code"]}
    # --- 전송 미구현: 실 업로드는 별도 GO (owner 실시간 승인 + 비가역성 실측 후) ---
    raise NotImplementedError("upload transport intentionally not implemented — 별도 GO")


# ---------------------------------------------------------------- preflight 본체
def preflight(pack_dir, staging_db_path=None, approval_input=None,
              regression_state=None, irreversible=True, staged_root=None):
    """G1~G7 순차 fail-closed. 첫 FAIL 에서 BLOCK + reason_code, 이후 게이트 미실행.
    전 게이트 PASS 시에만 staged bundle(temp) 생성 + 승인 토큰 발급.
    irreversible 기본 True — 비가역 미입증 = IRREVERSIBLE 문구 의무 (fail-closed)."""
    pack_dir = Path(pack_dir)
    gates, codes = [], []
    result = {"verdict": "BLOCK", "pack_id": None, "gates": gates, "reason_codes": codes,
              "bundle_hash8": None, "bundle_hash_full": None, "expected_phrase": None,
              "staged_dir": None, "approval_tok": None, "fail_open": False}

    def push(g):
        gates.append(g)
        for c in g["reason_codes"]:
            if c not in codes:
                codes.append(c)
        return g["ok"]

    missing = [f for f in PACK_FILES if not (pack_dir / f).exists()]
    if missing:
        push(_gres("LOAD", False, ["G1_SCHEMA_STOP"], {"missing_files": len(missing)}))
        return result

    raw_texts = {f: (pack_dir / f).read_text(encoding="utf-8") for f in PACK_FILES}
    try:
        manifest = json.loads(raw_texts["manifest.json"])
    except ValueError:
        push(_gres("LOAD", False, ["G1_SCHEMA_STOP"], {"manifest_parse": "error"}))
        return result
    pack_id = manifest.get("pack_id", "unknown_pack") if isinstance(manifest, dict) else "unknown_pack"
    result["pack_id"] = pack_id

    if not push(gate_g1_schema(manifest)):
        return result

    payload = {"manifest": manifest}
    for fname in ("nodes.jsonl", "edges.jsonl", "evidence_index.jsonl", "evidence_chunk.jsonl"):
        key = fname.replace(".jsonl", "")
        payload[key] = [json.loads(ln) for ln in raw_texts[fname].splitlines() if ln.strip()]

    if not push(gate_g2_source_pointers(payload)):
        return result
    if not push(gate_g3_secret_pii(raw_texts)):
        return result
    if not push(gate_g4_ephemeral(payload)):
        return result
    if not push(gate_g5_candidate_only(staging_db_path, pack_id)):
        return result
    if not push(gate_g6_serialized_leak(pack_dir)):
        return result

    # 재해시 검증 1/2 — 승인 직전: full hash 고정(검증 기준) + hash8(표시·문구용) 파생
    bundle_hash_full = compute_bundle_hash_full(pack_dir)
    bundle_hash8 = bundle_hash_full[:8]
    result["bundle_hash_full"] = bundle_hash_full
    result["bundle_hash8"] = bundle_hash8
    result["expected_phrase"] = expected_approval_phrase(pack_id, bundle_hash8, irreversible)

    if not push(gate_g7_approval(approval_input, pack_id, bundle_hash8, irreversible,
                                 regression_state, [g["gate"] for g in gates])):
        return result

    # 전 게이트 PASS — staged bundle 생성 (temp 만)
    staged = Path(tempfile.mkdtemp(prefix="upf_staged_", dir=str(staged_root) if staged_root else None))
    for fname in PACK_FILES:
        shutil.copy2(pack_dir / fname, staged / fname)
    re_h = verify_rehash_before_send(staged, bundle_hash_full)
    if not re_h["ok"]:
        codes.append(re_h["reason_code"])
        return result
    # sidecar 폐기/상태 마킹 — pack 본문 5종 무수정. audit 은 full hash (hash8 은 표시 동반만)
    sidecar = {"state": "APPROVED", "pack_sha_full": bundle_hash_full, "pack_sha8": bundle_hash8,
               "approval_ts": time.strftime("%Y%m%dT%H%M%S"),
               "upload_response_ids": [], "abort_reason_code": None}
    (pack_dir / "UPLOAD_STATE.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")
    result["staged_dir"] = str(staged)
    result["approval_tok"] = issue_approval_tok(pack_id, bundle_hash_full)
    result["verdict"] = "ALLOW"
    return result


# ---------------------------------------------------------------- fixture materialize
def _resolve_value(v):
    """delta 명세 value — {"join": [...]} 조각 결합 (재귀)."""
    if isinstance(v, dict):
        if set(v.keys()) == {"join"}:
            return "".join(v["join"])
        return {k: _resolve_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve_value(x) for x in v]
    return v


def _walk_set(obj, dotted, value=None, delete=False, ignore_missing=False):
    parts = dotted.split(".")
    cur = obj
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            if delete and ignore_missing:
                return
            if delete:
                raise KeyError(dotted)
            cur[p] = {}
        cur = cur[p]
    leaf = parts[-1]
    if delete:
        if isinstance(cur, dict) and leaf in cur:
            del cur[leaf]
        elif not ignore_missing:
            raise KeyError(dotted)
    else:
        cur[leaf] = value


def _pad_node(i):
    s = "합성 패딩 문장 %04d 입찰 검토용(합성)." % i
    return {"id": "node:UPF:pad:p%04d" % i, "space": "concept", "node_type": "Concept",
            "label": s,
            "properties": {"label_kind": "개념", "sentence": s, "domain": "SEED_BID",
                           "candidate": True, "origin": "synthetic_fixture"},
            "evidence_refs": ["EVU-1"], "promotion_allowed": False}


def materialize_case(case_spec, base_dir, dst_dir):
    """base 5파일 복사 → delta ops 적용 (join 조각 결합 포함). temp 밖 write 0 (호출자 책임)."""
    base_dir, dst_dir = Path(base_dir), Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    for fname in PACK_FILES:
        shutil.copy2(base_dir / fname, dst_dir / fname)
    if not case_spec:
        return dst_dir
    for op in case_spec.get("ops", []):
        fpath = dst_dir / op["file"]
        kind = op["op"]
        value = _resolve_value(op.get("value"))
        if op["file"] == "manifest.json":
            data = json.loads(fpath.read_text(encoding="utf-8"))
            if kind == "set":
                _walk_set(data, op["path"], value)
            elif kind == "del":
                _walk_set(data, op["path"], delete=True)
            else:
                raise ValueError("manifest op 미지원: %s" % kind)
            fpath.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            lines = [json.loads(ln) for ln in fpath.read_text(encoding="utf-8").splitlines()
                     if ln.strip()]
            if kind in ("set", "del"):
                idx_s, dotted = op["path"].split(":", 1)
                _walk_set(lines[int(idx_s)], dotted, value, delete=(kind == "del"))
            elif kind == "del_key_all":
                for row in lines:
                    _walk_set(row, op["path"], delete=True, ignore_missing=True)
            elif kind == "append_line":
                lines.append(value)
            elif kind == "pad":
                for i in range(int(value["count"])):
                    lines.append(_pad_node(i))
            else:
                raise ValueError("jsonl op 미지원: %s" % kind)
            fpath.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in lines) + "\n",
                encoding="utf-8")
    return dst_dir


def seed_temp_db(db_path, rows, self_pack_id):
    """temp SQLite 시드 — selftest 전용 (real staging DB 미접촉)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE staging_nodes("
                     "pack_id TEXT, node_id TEXT, status TEXT, "
                     "promotion_allowed INTEGER DEFAULT 0, confirmed INTEGER DEFAULT 0)")
        for i, r in enumerate(rows):
            pid = self_pack_id if r.get("pack", "self") == "self" else OTHER_PACK_ID
            conn.execute("INSERT INTO staging_nodes VALUES(?,?,?,?,?)",
                         (pid, "n%02d" % i, r.get("status", "candidate"),
                          int(r.get("promotion_allowed", 0)), int(r.get("confirmed", 0))))
        conn.commit()
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------- selftest
def _tree_sha(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).replace("\\", "/").encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
    return h.hexdigest()


def run_selftest():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=" * 70)
    print("OpenCrab private upload preflight — G1~G7 fail-closed selftest")
    print("=" * 70)

    checks = []

    def add(name, ok, note=""):
        checks.append((name, bool(ok), note))
        print("  [%s] %-34s %s" % ("OK " if ok else "FAIL", name, note))

    if not BASE_POK.is_dir() or not CASES_DIR.is_dir():
        print("  [FAIL] fixture 디렉토리 없음: %s" % FIXTURE_ROOT)
        sys.exit(1)

    base_manifest = json.loads((BASE_POK / "manifest.json").read_text(encoding="utf-8"))
    pok_pack_id = base_manifest["pack_id"]
    fx_sha_before = _tree_sha(FIXTURE_ROOT)
    work = Path(tempfile.mkdtemp(prefix="upf_selftest_"))
    fail_open = 0
    pok_res = None
    try:
        # ---- 양성 P-OK: 전 게이트 PASS → ALLOW + staged bundle
        pok_dir = materialize_case(None, BASE_POK, work / "pok")
        pok_db = seed_temp_db(work / "pok.sqlite", DEFAULT_DB_ROWS, pok_pack_id)
        pok_h8 = compute_bundle_hash8(pok_dir)
        pok_phrase = expected_approval_phrase(pok_pack_id, pok_h8, True)
        pok_res = preflight(pok_dir, pok_db, pok_phrase, None, True, staged_root=work)
        add("P-OK_allow",
            pok_res["verdict"] == "ALLOW" and len(pok_res["gates"]) == 7
            and all(g["ok"] for g in pok_res["gates"]),
            "verdict=%s gates=%d" % (pok_res["verdict"], len(pok_res["gates"])))
        staged_ok = (pok_res["staged_dir"]
                     and all((Path(pok_res["staged_dir"]) / f).exists() for f in PACK_FILES)
                     and re.fullmatch(r"[0-9a-f]{8}", pok_res["bundle_hash8"] or ""))
        add("P-OK_staged_bundle", staged_ok, "hash8=%s" % pok_res["bundle_hash8"])

        # ---- 음성 22 — 전건 기대 reason_code BLOCK
        case_files = sorted(CASES_DIR.glob("N_*.json"))
        add("cases_present_22", len(case_files) == 22, "found=%d" % len(case_files))
        for cf in case_files:
            spec = json.loads(cf.read_text(encoding="utf-8"))
            cid = spec["case_id"]
            exp = spec["expected_reason_code"]
            cdir = materialize_case(spec, BASE_POK, work / cf.stem)
            db_rows = (spec.get("db_setup") or {}).get("rows") or DEFAULT_DB_ROWS
            cdb = seed_temp_db(work / (cf.stem + ".sqlite"), db_rows, pok_pack_id)
            ch8 = compute_bundle_hash8(cdir)
            appr = spec.get("approval_input")
            if isinstance(appr, str):
                appr = appr.replace("{pack_id}", pok_pack_id).replace("{hash8}", ch8)
            irrev = spec.get("irreversible", True)
            regs = spec.get("regression_state")

            if spec.get("replay_pok"):
                # N-X1: P-OK 경로 ALLOW → 토큰 1회 소모 → 동일 토큰 2회째 = BLOCK (full hash 바인딩)
                r1 = preflight(cdir, cdb, appr, regs, irrev, staged_root=work)
                consumed = set()
                reached_transport = False
                second = {"ok": True}
                if r1["verdict"] == "ALLOW":
                    try:
                        send_staged_bundle(Path(r1["staged_dir"]), r1["approval_tok"],
                                           consumed, r1["bundle_hash_full"])
                    except NotImplementedError:
                        reached_transport = True
                    second = send_staged_bundle(Path(r1["staged_dir"]), r1["approval_tok"],
                                                consumed, r1["bundle_hash_full"])
                ok = (r1["verdict"] == "ALLOW" and reached_transport
                      and second.get("reason_code") == exp and not second.get("ok"))
                if second.get("ok"):
                    fail_open += 1
                add(cid, ok, "2nd=%s" % second.get("reason_code"))
            else:
                res = preflight(cdir, cdb, appr, regs, irrev, staged_root=work)
                blocked = res["verdict"] != "ALLOW"
                ok = blocked and exp in res["reason_codes"]
                if not blocked:
                    fail_open += 1
                add(cid, ok, "expect=%s got=%s" % (exp, ",".join(res["reason_codes"]) or "-"))

        # ---- 승인 문구 단위검사 (strict)
        add("phrase_exact_reversible",
            check_approval_phrase("UPLOAD pk_1 ab12cd34", "pk_1", "ab12cd34", False)["ok"])
        add("phrase_exact_irreversible",
            check_approval_phrase("UPLOAD pk_1 ab12cd34 IRREVERSIBLE", "pk_1", "ab12cd34", True)["ok"])
        add("phrase_trailing_space_reject",
            not check_approval_phrase("UPLOAD pk_1 ab12cd34 ", "pk_1", "ab12cd34", False)["ok"])
        add("phrase_double_space_reject",
            not check_approval_phrase("UPLOAD  pk_1 ab12cd34", "pk_1", "ab12cd34", False)["ok"])
        add("phrase_regex_only_no_pass",
            not check_approval_phrase("UPLOAD pk_1 deadbeef", "pk_1", "ab12cd34", False)["ok"])

        # ---- 재해시 2곳 (full hash 일치 통과 / 변조 ABORT / hash8 단독 전달 = 즉시 거부)
        staged = Path(pok_res["staged_dir"])
        add("rehash_match_ok_fullhash",
            verify_rehash_before_send(staged, pok_res["bundle_hash_full"])["ok"])
        add("fullhash_form_and_hash8_derived",
            re.fullmatch(r"[0-9a-f]{64}", pok_res["bundle_hash_full"] or "")
            and pok_res["bundle_hash8"] == pok_res["bundle_hash_full"][:8]
            and pok_res["approval_tok"]["pack_sha_full"] == pok_res["bundle_hash_full"])
        add("hash8_only_path_removed",
            not verify_rehash_before_send(staged, pok_res["bundle_hash8"])["ok"],
            "hash8 전달 = BUNDLE_HASH_MISMATCH_ABORT")
        with open(staged / "nodes.jsonl", "a", encoding="utf-8") as fh:
            fh.write("\n")  # 변조(승인~전송 사이 변경 시뮬레이션 — temp 안)
        tampered = verify_rehash_before_send(staged, pok_res["bundle_hash_full"])
        add("rehash_tamper_abort",
            not tampered["ok"] and tampered["reason_code"] == "BUNDLE_HASH_MISMATCH_ABORT",
            tampered["reason_code"] or "")

        # ---- 무해성
        add("fail_open_zero", fail_open == 0, "fail_open=%d" % fail_open)
        add("fixture_tree_unchanged", _tree_sha(FIXTURE_ROOT) == fx_sha_before)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    add("temp_cleaned", not work.exists())

    n_total = len(checks)
    n_ok = sum(1 for _, ok, _ in checks if ok)
    gate = "GO" if n_ok == n_total else "STOP"
    print("\n  checks=%d ok=%d fail=%d" % (n_total, n_ok, n_total - n_ok))
    print("  GATE: %s  (전 체크 통과 = GO · 실 업로드/전송 = 미구현, 별도 GO)" % gate)
    sys.exit(0 if gate == "GO" else 1)


def run_single(pack_dir, db_path=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    res = preflight(pack_dir, db_path)
    out = {k: res[k] for k in ("verdict", "pack_id", "reason_codes",
                               "bundle_hash8", "expected_phrase")}
    out["gates"] = [{"gate": g["gate"], "ok": g["ok"], "reason_codes": g["reason_codes"]}
                    for g in res["gates"]]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    sys.exit(0 if res["verdict"] == "ALLOW" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0], args[1] if len(args) > 1 else None)


if __name__ == "__main__":
    main()
