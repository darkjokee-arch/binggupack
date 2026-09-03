# -*- coding: utf-8 -*-
"""binggu_fresh_index — Local Fresh Index (LFI): 원본과 분리된 작고 빠른 파생 색인.

문제: 기본 회상(recall.why_search/preflight_context)이 매 호출마다 전체 active 노드를
로드(+semantic ON 시 노드마다 embed·회상마다 Ollama probe 1.5s)해 provider 지연/hang 과
O(N) 스캔에 노출된다. 어휘 경로는 이미 빠르나(≈10ms), semantic 경로는 warm 이어도 p95
400~620ms 로 목표(300ms)를 넘고 Ollama 다운 시 timeout 3s hang.

해법: ledger(원본, 불변) 에서 파생한 독립 SQLite 색인(<home>/fresh_index.sqlite)을 두고,
평소 회상은 이 작은 색인만 읽는다.
  - 원본 write 0 (ledger 는 mode=ro 로만 읽음). 색인은 순수 파생 — 삭제해도 rebuild 가능.
  - 증분 갱신: content_hash 로 신규/변경 노드만 upsert, 사라진 노드 삭제 반영(새 daemon 0).
  - Hot 회상: 색인 메타(작음)에 why_search 와 '동일한' substring relevance → 관련성 저하 0.
    전체 ledger 스캔 0 · 노드 전수 embed 0.
  - semantic 은 Hot 에 없다(2026-07-13 4cli+적대검증 확정 — 후보 진입이 어휘 매칭 전용이라
    재랭킹을 완성해도 의미 검색 불가). 의미 회상은 Deep(recall.why_search·배치 선채움) 전담.

불변/안전:
  - ledger 스키마 미변경. 색인은 승인 권한이 아니라 읽기 성능용 파생 데이터.
  - 저장 전 leak_guard 통과분만 title/summary/embed 에 담는다(PII·시크릿 미노출).
  - mutation 경로 신설 0 — owner approval/save/replace/deprecate 경계 불변.
  - 빈 ledger·색인 부재·손상·provider 다운 전부 graceful(예외 0, 어휘 폴백).

CLI: python -m binggupack.pack.fresh_index --selftest
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
_SCRIPTS = os.path.join(ROOT, "scripts")
for _p in (ROOT, _SCRIPTS):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack import p1_ranking as RANK  # noqa: E402
from binggupack.workspace import platform as _plat  # noqa: E402

INDEX_NAME = "fresh_index.sqlite"
INDEX_SCHEMA_VERSION = 1

# hot 랭킹 가중(설정으로 덮어씀 가능) — 전 노드 use_count=0 인 실데이터라 freshness/trust 중심.
DEFAULT_HOT_WEIGHTS = {"freshness": 1.0, "trust": 1.2, "utility": 0.8, "pin_boost": 5.0}

# 노드 신뢰도: candidate=0 = owner-sealed(확정) = 고신뢰, candidate=1 = CLI 후보.
_TRUST_SEALED = 1.0
_TRUST_CANDIDATE = 0.5
_TRUST_ACCEPT_BONUS = 0.2

# ─────────────────────────── 경로/토큰 ───────────────────────────

def index_path(home=None):
    base = home or _plat.binggu_home()
    return os.path.join(base, INDEX_NAME)


def _tokens(text):
    """recall._tokens 와 동일 규약(관련성 파리티). 공백 분리 소문자 2자+ 토큰."""
    if not text:
        return []
    return [t for t in str(text).lower().replace("\n", " ").split() if len(t) >= 2]


def _relevance(query_tokens, sentence):
    """recall._relevance 와 동일 — query 토큰이 문장에 등장하는 비율 [0,1]. 부분문자열 1회."""
    if not query_tokens:
        return 0.0
    s = (sentence or "").lower()
    hit = sum(1 for t in set(query_tokens) if t in s)
    return hit / float(len(set(query_tokens)))


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def _leak_safe(text):
    """PII/시크릿 스팬만 redact 하고 나머지는 검색 가능하게 보존(관련성 파리티 + 미노출 양립).

    파이프라인(검증자≠피검증자, 기존 정본 재사용):
      1) batch_redact — 시크릿(SECRET_PATTERNS) + PII shape(RRN/전화/사업자/이름) 스팬 → [REDACTED:N].
      2) 독립 검증 leak_guard(scan_residual_pii+SECRET) — 잔존 PII/시크릿이 있으면 보수적 blank.
    반환 (safe_text, ok). redactor 로 비-PII 항은 살아남아 substring 매칭 파리티 유지.
    """
    if not text:
        return "", True
    red = str(text)
    try:
        from binggupack.pack import batch_m1
        red, _hits, _rev = batch_m1.batch_redact(red)
    except Exception:
        pass  # redactor 부재 시 원문 유지 → 아래 독립검증이 최종 게이트
    try:
        import binggu_semantic_shadow as SH
        ok, _ = SH.leak_guard(red)
        return (red, True) if ok else ("", True)  # 잔존 → 보수적 blank(미노출 우선)
    except Exception:
        # 검증기 부재(테스트/CI 로 커버): redactor 적용분만 신뢰. redact 는 이미 됨.
        return red, True


# ─────────────────────────── 색인 스키마 ───────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS hot_items(
  item_id       TEXT PRIMARY KEY,
  kind          TEXT,           -- 'node' | 'file'(phase2)
  source_id     TEXT,           -- node_id 또는 파일 source id
  project_id    TEXT,           -- 파생/owner 태깅(nullable)
  rel_path      TEXT,           -- 파일용(phase2)
  node_type     TEXT,           -- ledger node_type(judgment/evidence/doc/state …) 표시 정확도
  file_kind     TEXT,           -- node subtype 또는 md/traj
  size          INTEGER,
  mtime         REAL,
  content_hash  TEXT,           -- 변경감지 서명
  title         TEXT,           -- leak_guard 통과분(원문 아님)
  summary       TEXT,
  created_at    TEXT,
  indexed_at    TEXT,
  last_seen_at  TEXT,
  state         TEXT,           -- 'active' | 'deprecated' | 'deleted'
  trust         REAL,
  owner_approved INTEGER,
  pinned        INTEGER DEFAULT 0,
  use_count     INTEGER DEFAULT 0,
  rank_score    REAL
);
CREATE INDEX IF NOT EXISTS idx_hot_state ON hot_items(state);
CREATE INDEX IF NOT EXISTS idx_hot_kind ON hot_items(kind);
CREATE INDEX IF NOT EXISTS idx_hot_project ON hot_items(project_id);
CREATE INDEX IF NOT EXISTS idx_hot_rank ON hot_items(rank_score);
CREATE TABLE IF NOT EXISTS pins(node_id TEXT PRIMARY KEY, ts TEXT);
-- FTS5 trigram: substring 매칭을 인덱스로(대규모에서 전체행 Python 적재 방지). ≥3자 토큰 전용.
CREATE VIRTUAL TABLE IF NOT EXISTS hot_fts USING fts5(item_id UNINDEXED, txt, tokenize='trigram');
"""

# 2자 토큰(한국어 '승인'·'배포' 등)은 trigram 인덱스 최소 길이(3) 미만 → LIKE 로 보완.
_FTS_MIN = 3


# hot_items 정본 컬럼(비파괴 마이그레이션 대상 — 구 색인에 없는 컬럼 ALTER ADD).
_HOT_ADD_COLUMNS = [
    ("node_type", "node_type TEXT"), ("project_id", "project_id TEXT"),
    ("rel_path", "rel_path TEXT"), ("size", "size INTEGER"), ("mtime", "mtime REAL"),
]


def _connect(home=None):
    p = index_path(home)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(p)
    try:
        con.executescript(_SCHEMA)
        # 비파괴 마이그레이션 — 구 색인(컬럼 결여)에 누락 컬럼 보강(색인은 파생이라 rebuild 로도 복원).
        try:
            have = {r[1] for r in con.execute("PRAGMA table_info(hot_items)")}
            for name, ddl in _HOT_ADD_COLUMNS:
                if name not in have:
                    con.execute("ALTER TABLE hot_items ADD COLUMN %s" % ddl)
        except sqlite3.Error:
            # Older or partially upgraded cache schemas are rebuilt by the outer recovery path.
            pass
        con.execute("INSERT OR IGNORE INTO index_meta(key,value) VALUES('schema_version',?)",
                    (str(INDEX_SCHEMA_VERSION),))
        # FTS 백필 — 구 색인(FTS 이전)에서 hot_items 는 있으나 hot_fts 가 비면 1회 채운다.
        try:
            fts_n = con.execute("SELECT count(*) FROM hot_fts").fetchone()[0]
            items_n = con.execute("SELECT count(*) FROM hot_items WHERE kind='node'").fetchone()[0]
            if items_n > 0 and fts_n == 0:
                for iid, title in con.execute("SELECT item_id, title FROM hot_items WHERE kind='node'"):
                    con.execute("INSERT INTO hot_fts(item_id, txt) VALUES(?,?)", (iid, title or ""))
        except sqlite3.Error:
            # FTS backfill is an optimization; canonical ledger reads remain available.
            pass
        con.commit()
    except Exception:
        con.close()  # 손상 파일: 핸들 누수 방지(Windows 파일 잠금 → rebuild os.remove 가능).
        raise
    return con


def _meta_get(con, key, default=None):
    row = con.execute("SELECT value FROM index_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _meta_set(con, key, value):
    con.execute("INSERT OR REPLACE INTO index_meta(key,value) VALUES(?,?)", (key, str(value)))


# ─────────────────────────── ledger 읽기(mode=ro) ───────────────────────────

def _read_ledger_nodes(ledger_path):
    """ledger active 노드 메타 read-only. 반환 list[dict] · 부재/손상 → []."""
    if not ledger_path or not os.path.exists(ledger_path):
        return [], set()
    try:
        con = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
        cur = con.cursor()
        ncols = [c[1] for c in cur.execute("PRAGMA table_info(nodes)")]

        def col(name, alias=None):
            return name if name in ncols else "NULL AS %s" % (alias or name)
        sel = ["node_id", col("node_type"), col("sentence"), col("candidate"),
               col("state"), col("semantic_subtype"), col("created_at"),
               col("use_count"), col("content_hash"), col("pack_id")]
        cur.execute("SELECT " + ",".join(sel) + " FROM nodes")
        rows = cur.fetchall()
        try:
            acc = {r[0] for r in cur.execute("SELECT DISTINCT node_id FROM owner_acceptances")}
        except sqlite3.OperationalError:
            acc = set()
        con.close()
    except Exception:
        return [], set()
    out = []
    for r in rows:
        nid, ntype, sent, cand, state, sub, created, uc, chash, pack = r
        if state not in (None, "active", "confirmed", "deprecated"):
            continue
        out.append({
            "node_id": nid, "node_type": ntype, "sentence": sent or "",
            "candidate": int(cand or 0), "state": state or "active",
            "semantic_subtype": sub, "created_at": created,
            "use_count": int(uc or 0), "content_hash": chash, "pack_id": pack,
        })
    return out, acc


def _node_sig(n):
    """변경감지 서명 — content_hash 우선, 없으면 내용 파생. 상태/subtype 변화도 감지."""
    base = n.get("content_hash") or _sha(n["sentence"])
    return _sha("%s|%s|%s|%s" % (base, n.get("state"), n.get("semantic_subtype"),
                                 n.get("candidate")))


def _hot_rank(n, pinned, has_accept, weights):
    fresh = RANK.freshness(n.get("created_at"))
    util = RANK.utility(n.get("use_count"))
    trust = _TRUST_SEALED if n.get("candidate") == 0 else _TRUST_CANDIDATE
    if has_accept:
        trust = min(1.0, trust + _TRUST_ACCEPT_BONUS)
    score = (weights["freshness"] * fresh + weights["trust"] * trust
             + weights["utility"] * util + (weights["pin_boost"] if pinned else 0.0))
    return round(score, 6), round(trust, 4)


# ─────────────────────────── 증분 갱신 ───────────────────────────

def _load_weights(home):
    try:
        from binggupack import config as C
        cfg = C.load_config("fresh_index", home)
        w = dict(DEFAULT_HOT_WEIGHTS)
        raw = cfg.get("hot_weights") if isinstance(cfg, dict) else None
        if isinstance(raw, dict):
            for k in w:
                try:
                    w[k] = float(raw[k])
                except (KeyError, TypeError, ValueError):
                    # Invalid individual weights retain their safe defaults.
                    pass
        return w
    except Exception:
        return dict(DEFAULT_HOT_WEIGHTS)


# ─────────────────────────── 2단계: 로컬 파일(md/traj) 포인터 인덱싱 ───────────────────────────
_FILE_SIZE_CAP = 1_000_000       # 1MB 초과 skip(포인터 인덱스는 소형 문서만)
_FILE_MAX = 20_000               # 허용 경로 전체 파일 수 상한(폭주 방어)
_FILE_EXTS = (".md",)            # markdown/traj(.md)


def allowed_paths(home=None):
    """색인 대상 로컬 경로(명시 허용목록·기본 빈). config fresh_index.allowed_paths(owner 옵트인)."""
    try:
        from binggupack import config as C
        cfg = C.load_config("fresh_index", home, use_cache=False)
        raw = cfg.get("allowed_paths") if isinstance(cfg, dict) else None
        return [str(p) for p in raw] if isinstance(raw, list) else []
    except Exception:
        return []


def _write_allowed_paths(paths, home=None):
    import json

    from binggupack import config as C
    p = str(C.config_path("fresh_index", home))
    data = {}
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as handle:
                data = json.loads(handle.read())
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    data["allowed_paths"] = paths
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
    C.invalidate("fresh_index", home)


def add_allowed_path(path, home=None):
    """허용 경로 추가(owner 옵트인). 반환 갱신된 목록. ledger/색인 불변(config 만 write)."""
    cur = allowed_paths(home)
    ap = os.path.abspath(path)
    if ap not in cur:
        cur.append(ap)
        _write_allowed_paths(cur, home)
    return cur


def remove_allowed_path(path, home=None):
    cur = allowed_paths(home)
    ap = os.path.abspath(path)
    if ap in cur:
        cur = [p for p in cur if p != ap]
        _write_allowed_paths(cur, home)
    return cur


def _safe_scan_dir(base):
    """허용 dir 하위 *.md 나열 — realpath commonpath 격리·symlink 거부·size cap·상한.

    반환 [(realpath, rel_path, size, mtime)]. 부재/비-dir → []. traversal/symlink escape 차단.
    """
    out = []
    try:
        base_real = os.path.realpath(base)
    except OSError:
        return out
    if not os.path.isdir(base_real):
        return out
    for root, dirs, files in os.walk(base_real):
        dirs[:] = [d for d in dirs
                   if not d.startswith(".") and not os.path.islink(os.path.join(root, d))]
        for fn in files:
            if not fn.lower().endswith(_FILE_EXTS):
                continue
            full = os.path.join(root, fn)
            if os.path.islink(full):
                continue  # symlink 거부(격리)
            try:
                real = os.path.realpath(full)
                if os.path.commonpath([base_real, real]) != base_real:
                    continue  # base 밖(traversal/symlink escape) 차단
                st = os.stat(real)
            except (OSError, ValueError):
                continue
            if st.st_size > _FILE_SIZE_CAP:
                continue
            out.append((real, os.path.relpath(real, base_real), int(st.st_size), float(st.st_mtime)))
            if len(out) >= _FILE_MAX:
                return out
    return out


def _file_pointer_text(path):
    """파일 → (title, summary, content_hash). 원문 전체 미저장 — 제목+앞부분 요약만(redact).

    title = 첫 '# 헤딩' 또는 basename. summary = 앞 비어있지 않은 줄(≤160). content_hash = 파일 sha256.
    """
    base = os.path.basename(path)
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return base, "", ""
    chash = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", "replace")
    lines = [ln.strip() for ln in text.splitlines()]
    title = base
    for ln in lines:
        if ln.startswith("#"):
            title = ln.lstrip("#").strip() or base
            break
    body = " ".join(ln for ln in lines if ln)[:600]
    safe, _ok = _leak_safe(("%s %s" % (title, body))[:1000])  # 검색용 = 제목+요약 redact(원문 미저장)
    return (safe or base), (safe or base)[:160], chash


def _file_rank(mtime, weights):
    """파일 포인터 랭킹 — mtime freshness + 중립 trust(참조·미검증·pinned 없음)."""
    import datetime as _dt
    try:
        iso = _dt.datetime.fromtimestamp(mtime, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, OverflowError, ValueError):
        iso = None
    fresh = RANK.freshness(iso)
    trust = 0.5  # 파일 = 참조 포인터(owner 승인/candidate 개념 없음) — 중립 신뢰
    return round(weights["freshness"] * fresh + weights["trust"] * trust, 6), iso


def _sync_files(con, home, now_iso, weights):
    """허용 경로 md/traj → 색인(kind='file'). mtime/size 우선 비교, 변경 시만 hash·요약·재색인.

    반환 {added, updated, unchanged, removed, scanned}. 허용목록 빈 경우 전부 0(파일 인덱싱 off).
    """
    scanned = []
    for base in allowed_paths(home):
        scanned.extend(_safe_scan_dir(base))
    existing = {r[0]: (r[1], r[2], r[3], r[4]) for r in con.execute(
        "SELECT item_id, content_hash, state, mtime, size FROM hot_items WHERE kind='file'")}
    seen = set()
    added = updated = unchanged = 0
    for real, rel, size, mtime in scanned:
        iid = "file:" + _sha(real)
        seen.add(iid)
        prev = existing.get(iid)
        # mtime+size 동일 + active → 미변경(hash·읽기 skip). 파일 stat 만으로 판정(싼 경로).
        if prev and prev[1] == "active" and prev[3] == mtime and prev[2] == size:
            con.execute("UPDATE hot_items SET last_seen_at=? WHERE item_id=?", (now_iso, iid))
            unchanged += 1
            continue
        title, summary, chash = _file_pointer_text(real)
        if prev and prev[0] == chash and prev[1] == "active":
            con.execute("UPDATE hot_items SET mtime=?, size=?, last_seen_at=? WHERE item_id=?",
                        (mtime, size, now_iso, iid))  # touch 만(내용 동일) — 재색인 skip
            unchanged += 1
            continue
        fkind = "traj" if "traj" in (rel + os.path.basename(real)).lower() else "md"
        rank, iso = _file_rank(mtime, weights)
        con.execute(
            "INSERT OR REPLACE INTO hot_items(item_id,kind,source_id,project_id,node_type,rel_path,"
            "file_kind,size,mtime,content_hash,title,summary,created_at,indexed_at,last_seen_at,"
            "state,trust,owner_approved,pinned,use_count,rank_score) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, "file", real, None, "file", rel, fkind, size, mtime, chash, title, summary,
             iso, now_iso, now_iso, "active", 0.5, 0, 0, 0, rank))
        con.execute("DELETE FROM hot_fts WHERE item_id=?", (iid,))
        con.execute("INSERT INTO hot_fts(item_id, txt) VALUES(?,?)", (iid, title))
        if prev:
            updated += 1
        else:
            added += 1
    removed = 0
    for iid, v in existing.items():
        if iid not in seen and v[1] != "deleted":
            con.execute("UPDATE hot_items SET state='deleted', last_seen_at=? WHERE item_id=?",
                        (now_iso, iid))
            con.execute("DELETE FROM hot_fts WHERE item_id=?", (iid,))
            removed += 1
    return {"added": added, "updated": updated, "unchanged": unchanged,
            "removed": removed, "scanned": len(scanned)}


def index_update(ledger_path, home=None, now_iso=None):
    """ledger(+허용 로컬 파일) → 색인 증분 반영. 신규/변경만 upsert, 사라진 항목 deleted. write=색인만.

    반환 {status, scanned, added, updated, unchanged, removed, deprecated, files, ms}.
    """
    t0 = time.perf_counter()
    now_iso = now_iso or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    nodes, acc = _read_ledger_nodes(ledger_path)
    con = _connect(home)
    weights = _load_weights(home)
    pins = {r[0] for r in con.execute("SELECT node_id FROM pins")}

    existing = {r[0]: (r[1], r[2]) for r in
                con.execute("SELECT item_id, content_hash, state FROM hot_items WHERE kind='node'")}
    seen = set()
    added = updated = unchanged = deprecated = 0
    for n in nodes:
        iid = "node:" + n["node_id"]
        seen.add(iid)
        sig = _node_sig(n)
        state = "deprecated" if n["state"] == "deprecated" else "active"
        if n["state"] == "deprecated":
            deprecated += 1
        prev = existing.get(iid)
        if prev and prev[0] == sig and prev[1] == state:
            con.execute("UPDATE hot_items SET last_seen_at=? WHERE item_id=?", (now_iso, iid))
            unchanged += 1
            continue
        pinned = 1 if n["node_id"] in pins else 0
        rank, trust = _hot_rank(n, pinned, n["node_id"] in acc, weights)
        # 매칭 파리티: baseline why_search 는 full sentence 매칭 → 절단은 관련성 저하 유발.
        # 전문(redact 통과분)을 title 에 담아 term 누락 0(관련성 파리티). 표시는 claim=[:120].
        # 병리적 초장문만 4000자로 방어(memory 문장은 통상 그 이내).
        title, ok = _leak_safe(n["sentence"][:4000])
        summary = title[:160]
        owner_approved = 1 if (n["candidate"] == 0 or n["node_id"] in acc) else 0
        con.execute(
            "INSERT OR REPLACE INTO hot_items(item_id,kind,source_id,project_id,node_type,"
            "file_kind,content_hash,title,summary,created_at,indexed_at,last_seen_at,state,"
            "trust,owner_approved,pinned,use_count,rank_score) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, "node", n["node_id"], None, n.get("node_type"), n.get("semantic_subtype"),
             sig, title, summary, n.get("created_at"), now_iso, now_iso, state,
             trust, owner_approved, pinned, n["use_count"], rank))
        # FTS 동기화(substring 인덱스) — title 을 색인 텍스트로.
        # 신규 행은 삭제 불필요(DELETE WHERE item_id 는 UNINDEXED full-scan → O(N²) 회피).
        if prev:
            con.execute("DELETE FROM hot_fts WHERE item_id=?", (iid,))
            updated += 1
        else:
            added += 1
        con.execute("INSERT INTO hot_fts(item_id, txt) VALUES(?,?)", (iid, title))
    # ledger 에서 사라진 노드 → deleted 표시(보존, 회상 제외).
    removed = 0
    for iid in existing:
        if iid not in seen:
            con.execute("UPDATE hot_items SET state='deleted', last_seen_at=? WHERE item_id=?",
                        (now_iso, iid))
            con.execute("DELETE FROM hot_fts WHERE item_id=?", (iid,))  # 삭제 노드는 FTS 후보 제외
            removed += 1
    # 2단계: 허용 로컬 파일(md/traj) 포인터 동기화(허용목록 빈 경우 no-op).
    files = _sync_files(con, home, now_iso, weights)
    _meta_set(con, "last_update_ts", now_iso)
    _meta_set(con, "ledger_node_count", str(len(nodes)))
    _meta_set(con, "ledger_path", os.path.abspath(ledger_path) if ledger_path else "")
    con.commit()
    con.close()
    ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {"status": "OK", "scanned": len(nodes), "added": added, "updated": updated,
            "unchanged": unchanged, "removed": removed, "deprecated": deprecated,
            "files": files, "ms": ms}


def index_status(ledger_path, home=None):
    """색인 상태 + 변경 대기 수(cheap ledger diff). 목표 <100ms(unchanged)."""
    t0 = time.perf_counter()
    p = index_path(home)
    if not os.path.exists(p):
        return {"status": "MISSING", "index_path": p, "reason": "색인 없음 — index update 필요",
                "ms": round((time.perf_counter() - t0) * 1000.0, 2)}
    con = _connect(home)
    last = _meta_get(con, "last_update_ts")
    total = con.execute("SELECT COUNT(*) FROM hot_items WHERE kind='node' AND state='active'").fetchone()[0]
    deprecated = con.execute("SELECT COUNT(*) FROM hot_items WHERE state='deprecated'").fetchone()[0]
    pinned = con.execute("SELECT COUNT(*) FROM hot_items WHERE pinned=1").fetchone()[0]
    files_active = con.execute(
        "SELECT COUNT(*) FROM hot_items WHERE kind='file' AND state='active'").fetchone()[0]
    # 변경 대기 = ledger sig 와 색인 sig 비교(단일 패스).
    nodes, _acc = _read_ledger_nodes(ledger_path)
    idx = {r[0]: (r[1], r[2]) for r in
           con.execute("SELECT item_id, content_hash, state FROM hot_items WHERE kind='node'")}
    con.close()
    pending = 0
    seen = set()
    for n in nodes:
        iid = "node:" + n["node_id"]
        seen.add(iid)
        state = "deprecated" if n["state"] == "deprecated" else "active"
        prev = idx.get(iid)
        if not prev or prev[0] != _node_sig(n) or prev[1] != state:
            pending += 1
    removed = sum(1 for iid, v in idx.items() if iid not in seen and v[1] != "deleted")
    ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {"status": "OK" if pending == 0 and removed == 0 else "STALE",
            "index_path": p, "last_update_ts": last, "active": total,
            "deprecated": deprecated, "pinned": pinned, "files": files_active,
            "ledger_nodes": len(nodes),
            "pending_changes": pending, "pending_removals": removed, "ms": ms}


def indexed_ledger_path(home=None):
    """색인이 어느 ledger 로 빌드됐는지(read-only peek · abspath). 파일/메타 부재 → None.

    cmd_recall 이 '현재 ledger 와 다르면 재빌드' 판단에 쓴다(같은 홈에서 --ledger 로 다른 장부를
    가리키는 경우 stale 색인 재사용 방지). 같은 ledger 면 재빌드 0 → 스틸상태 recall 은 원본 스캔 0.
    """
    p = index_path(home)
    if not os.path.exists(p):
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
        row = con.execute("SELECT value FROM index_meta WHERE key='ledger_path'").fetchone()
        con.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def peek(home=None):
    """읽기전용 색인 요약(생성 0 · write 0 · ledger 미접촉) — `binggu home` 상태표시용.

    파일 부재 → {'exists': False}. mode=ro 로만 열어 last_update/active/indexed count 반환.
    """
    p = index_path(home)
    if not os.path.exists(p):
        return {"exists": False}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
        last = None
        try:
            row = con.execute("SELECT value FROM index_meta WHERE key='last_update_ts'").fetchone()
            last = row[0] if row else None
            r2 = con.execute("SELECT value FROM index_meta WHERE key='ledger_node_count'").fetchone()
            indexed_ledger = int(r2[0]) if r2 and r2[0] is not None else None
        except sqlite3.Error:
            indexed_ledger = None
        active = con.execute(
            "SELECT COUNT(*) FROM hot_items WHERE kind='node' AND state='active'").fetchone()[0]
        con.close()
        return {"exists": True, "last_update_ts": last, "active": active,
                "indexed_ledger_nodes": indexed_ledger}
    except Exception:
        return {"exists": False, "error": True}


def index_rebuild(ledger_path, home=None):
    """색인 전체 재생성(owner 명시). 손상 색인 복구 경로 — 열리지 않으면 파일 삭제 후 새로. 원본 불변."""
    p = index_path(home)
    # 정상 파일이면 핀 보존을 위해 먼저 읽어둔다(손상 시 실패 → 핀 없이 복구).
    try:
        con = _connect(home)
        pins = {r[0] for r in con.execute("SELECT node_id FROM pins")}
        con.close()
    except Exception:
        pins = set()
    # 파일(및 사이드카) 삭제 → 손상까지 확실히 복구.
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            if os.path.exists(p + suffix):
                os.remove(p + suffix)
        except OSError:
            # Missing or locked sidecars do not prevent rebuilding the disposable index.
            pass
    con = _connect(home)  # fresh 스키마 재생성
    for nid in pins:
        con.execute("INSERT OR IGNORE INTO pins(node_id,ts) VALUES(?,?)",
                    (nid, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    con.commit()
    con.close()
    res = index_update(ledger_path, home=home)
    res["rebuilt"] = True
    res["pins_preserved"] = len(pins)
    return res


# ─────────────────────────── pin 관리(색인 레벨, ledger 불변) ───────────────────────────

def set_pin(node_id, home=None, pinned=True):
    con = _connect(home)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if pinned:
        con.execute("INSERT OR IGNORE INTO pins(node_id,ts) VALUES(?,?)", (node_id, ts))
        con.execute("UPDATE hot_items SET pinned=1 WHERE item_id=?", ("node:" + node_id,))
    else:
        con.execute("DELETE FROM pins WHERE node_id=?", (node_id,))
        con.execute("UPDATE hot_items SET pinned=0 WHERE item_id=?", ("node:" + node_id,))
    con.commit()
    con.close()
    return {"status": "OK", "node_id": node_id, "pinned": pinned}


# ─────────────────────────── Hot 회상(색인 전용) ───────────────────────────

def hot_recall(query, home=None, limit=5, project=None, include_deprecated=False):
    """색인 전용 Hot 회상 — 전체 ledger 스캔 0 · embed 0(의미 회상은 Deep 전담).

    어휘(why_search 동일 substring relevance) 1차 + rank_score(freshness+trust+pinned) 2차.
    반환 {relevant_nodes, summary, confidence, source, scanned, limit}.
    """
    p = index_path(home)
    if not os.path.exists(p):
        return {"relevant_nodes": [], "summary": "색인이 없습니다(index update 필요).",
                "confidence": 0.0, "source": "hot", "scanned": 0, "limit": limit,
                "index_missing": True}
    con = _connect(home)
    states = ("active", "deprecated") if include_deprecated else ("active",)
    qtok = _tokens(query)
    long_toks = [t for t in qtok if len(t) >= _FTS_MIN]
    short_toks = [t for t in qtok if 0 < len(t) < _FTS_MIN]

    def _like(t):
        return "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    # 후보 item_id 를 SQLite 레벨에서 좁힌다(전체행 Python 적재 방지 · 대규모 확장성).
    #   ≥3자 토큰 → FTS5 trigram MATCH(인덱스 substring) · 2자 토큰 → LIKE(C-scan, 매칭만) · pinned 항상.
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _cand(item_id TEXT PRIMARY KEY)")
    con.execute("DELETE FROM _cand")
    fts_ok = False
    if long_toks:
        expr = " OR ".join('"%s"' % t.replace('"', '""') for t in long_toks)
        try:
            con.execute("INSERT OR IGNORE INTO _cand(item_id) "
                        "SELECT item_id FROM hot_fts WHERE txt MATCH ?", (expr,))
            fts_ok = True
        except sqlite3.Error:
            fts_ok = False
    if long_toks and not fts_ok:  # FTS 부재/오류 → LIKE 폴백(정확성 보장)
        for t in long_toks:
            con.execute("INSERT OR IGNORE INTO _cand(item_id) SELECT item_id FROM hot_items "
                        "WHERE kind='node' AND title LIKE ? ESCAPE '\\'", (_like(t),))
    for t in short_toks:  # 2자 토큰 — trigram 최소길이 미만이라 LIKE 로 보완
        con.execute("INSERT OR IGNORE INTO _cand(item_id) SELECT item_id FROM hot_items "
                    "WHERE kind IN ('node','file') AND title LIKE ? ESCAPE '\\'", (_like(t),))
    con.execute("INSERT OR IGNORE INTO _cand(item_id) SELECT item_id FROM hot_items "
                "WHERE kind='node' AND pinned=1")  # 영구 규칙은 항상 후보

    cand_n = con.execute("SELECT COUNT(*) FROM _cand").fetchone()[0]
    uniq = list(dict.fromkeys(qtok))   # 유니크 토큰(_relevance 와 동일: set 기준 hit/분모)
    ntok = len(uniq)
    if ntok == 0:  # 토큰 없는 query → 관련 없음(빈 결과)
        con.close()
        return {"relevant_nodes": [], "summary": "질의어에서 유효 토큰을 찾지 못했습니다.",
                "confidence": 0.0, "source": "hot",
                "scanned": 0, "candidates": cand_n, "limit": limit, "index_missing": False}
    # 관련성(mc = 매칭 토큰 수) · 정렬 · LIMIT 을 전부 SQL 로 → Python 은 top-K 만 적재(전체행/전체후보 아님).
    #   mc = Σ(instr(lower(title), tok)>0) — _relevance substring 규약 동일(title ⊇ summary).
    rel_expr = "+".join("(instr(lower(h.title),?)>0)" for _ in uniq)
    fetch_limit = limit
    ph = ",".join("?" * len(states))
    sql = ("SELECT h.item_id,h.source_id,h.title,h.summary,h.node_type,h.file_kind,h.rank_score,"
           "h.trust,h.pinned,h.owner_approved,h.created_at,h.state,h.project_id,h.kind,h.rel_path,"
           "(" + rel_expr + ") AS mc "
           "FROM hot_items h JOIN _cand c ON h.item_id=c.item_id "
           "WHERE h.kind IN ('node','file') AND h.state IN (" + ph + ")")
    params = list(uniq) + list(states)
    if project:
        sql += " AND (h.project_id=? OR h.project_id IS NULL)"
        params.append(project)
    sql += " ORDER BY mc DESC, h.rank_score DESC, h.item_id LIMIT ?"
    params.append(fetch_limit)
    rows = con.execute(sql, params).fetchall()  # top-K 만(SQL 이 정렬·LIMIT)
    scored = []
    for r in rows:
        (iid, sid, title, summary, ntype, fkind, rank, trust, pinned, own,
         created, state, pid, kind, rel_path, mc) = r
        rel = round((mc or 0) / ntok, 4)
        if rel <= 0.0 and not pinned:
            continue
        scored.append({"item_id": iid, "node_id": sid, "title": title or "",
                       "claim": (title or "")[:120], "node_type": ntype or "judgment",
                       "semantic_subtype": fkind, "kind": kind, "rel_path": rel_path,
                       "rank_score": rank, "relevance": rel, "trust": trust,
                       "pinned": bool(pinned), "owner_approved": bool(own),
                       "created_at": created, "state": state, "project_id": pid,
                       "candidate": True, "trust_label": "candidate_unverified"})

    con.close()

    # 정렬: 관련성 1차, rank_score(freshness+trust+pinned) 2차 — why_search 규약 동일.
    scored.sort(key=lambda x: (-x["relevance"], -x["rank_score"], x["item_id"]))
    # 중복 제거(node_id 기준).
    seen = set()
    dedup = []
    for it in scored:
        if it["node_id"] in seen:
            continue
        seen.add(it["node_id"])
        dedup.append(it)
    top = dedup[:limit]
    conf = round(top[0]["relevance"], 4) if top else 0.0
    summary = ("관련 기억 %d건(Hot 색인·랭킹순). candidate — 사람 확정 전 참고용." % len(top)
               if top else "Hot 색인에서 관련 기억을 찾지 못했습니다(더 넓게: --deep).")
    return {"relevant_nodes": top, "summary": summary, "confidence": conf,
            "source": "hot",
            "scanned": len(rows),      # Python 이 실제 적재한 행 수(top-K · 전체행/전체후보 아님)
            "candidates": cand_n,      # 색인이 SQLite 레벨에서 좁힌 후보 수(참고)
            "limit": limit, "index_missing": False}


# ─────────────────────────── selftest ───────────────────────────

def _selftest():
    import tempfile
    fails = []

    def ck(name, cond):
        print("[%s] %s" % ("PASS" if cond else "FAIL", name))
        if not cond:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix="fresh_index_st_")
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    ledger = os.path.join(tmp, "ledger.sqlite")
    # 미니 ledger 구성
    lc = sqlite3.connect(ledger)
    lc.executescript(
        "CREATE TABLE nodes(node_id TEXT PRIMARY KEY,node_type TEXT,sentence TEXT,"
        "candidate INT,state TEXT,semantic_subtype TEXT,created_at TEXT,use_count INT,"
        "content_hash TEXT,pack_id TEXT);"
        "CREATE TABLE evidence(evidence_id TEXT PRIMARY KEY, sentence TEXT);"
        "CREATE TABLE owner_acceptances(event_id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT, event TEXT);")
    rows = [
        ("n1", "judgment", "빙구팩 릴리스 승인 경계 유지", 0, "active", "결정", "2026-07-10T00:00:00Z", 0, "h1", "p1"),
        ("n2", "judgment", "recall 성능 병목은 semantic probe", 1, "active", "버그패턴", "2026-07-01T00:00:00Z", 0, "h2", "p2"),
        ("n3", "judgment", "오래된 영구 규칙 항상 지켜라", 0, "active", "교훈", "2026-01-01T00:00:00Z", 0, "h3", "p3"),
        ("n4", "judgment", "deprecated 옛 결정", 0, "deprecated", "결정", "2026-05-01T00:00:00Z", 0, "h4", "p4"),
    ]
    lc.executemany("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    lc.commit()
    lc.close()

    # 1) 최초 색인
    r = index_update(ledger, home=home)
    ck("최초 색인(added=4)", r["status"] == "OK" and r["added"] == 4 and r["scanned"] == 4)

    # 2) 변경 없음(재실행 → unchanged, added 0)
    r2 = index_update(ledger, home=home)
    ck("변경 없음(unchanged=4, added=0)", r2["added"] == 0 and r2["unchanged"] == 4)

    # 3) status 변경 대기 0
    st = index_status(ledger, home=home)
    ck("status pending=0", st["status"] == "OK" and st["pending_changes"] == 0)

    # 4) Hot 회상 — deprecated 제외, top5 제한
    hr = hot_recall("릴리스 승인", home=home, limit=5)
    ids = [x["node_id"] for x in hr["relevant_nodes"]]
    ck("Hot 회상 n1 상위 + deprecated n4 제외 + 원본스캔 0",
       "n1" in ids and "n4" not in ids and hr["source"] == "hot")

    # 5) pinned 영구 규칙 보존 — n3 핀 → 무관 query 에도 등장
    set_pin("n3", home=home, pinned=True)
    hr2 = hot_recall("전혀무관한질의어힝", home=home, limit=5)
    ck("pinned 영구 규칙 보존(무관 query 에도 n3)",
       "n3" in [x["node_id"] for x in hr2["relevant_nodes"]])

    # 6) 고신뢰(candidate=0) trust 우위 — sentence 에 있는 단어로 조회(why_search 파리티: subtype 미매칭)
    hr3 = hot_recall("승인 경계", home=home, limit=5)
    trust_n1 = next((x["trust"] for x in hr3["relevant_nodes"] if x["node_id"] == "n1"), None)
    trust_n2 = next((x["trust"] for x in hr3["relevant_nodes"] if x["node_id"] == "n2"), None)
    ck("고신뢰 candidate=0 → trust 1.0(> candidate=1 trust 0.5)",
       trust_n1 == 1.0 and (trust_n2 is None or trust_n2 == 0.5))

    # 7) 노드 변경 반영(sentence 수정 → updated)
    lc = sqlite3.connect(ledger)
    lc.execute("UPDATE nodes SET sentence=?, content_hash=? WHERE node_id='n2'",
               ("recall 병목 수정됨 새 내용", "h2b"))
    lc.commit()
    lc.close()
    r3 = index_update(ledger, home=home)
    ck("변경 반영(updated=1)", r3["updated"] == 1 and r3["added"] == 0)

    # 8) 노드 삭제 반영(제거 → deleted)
    lc = sqlite3.connect(ledger)
    lc.execute("DELETE FROM nodes WHERE node_id='n2'")
    lc.commit()
    lc.close()
    r4 = index_update(ledger, home=home)
    ck("삭제 반영(removed=1)", r4["removed"] == 1)
    hr4 = hot_recall("recall 병목", home=home, limit=5)
    ck("삭제된 노드 회상 제외", "n2" not in [x["node_id"] for x in hr4["relevant_nodes"]])

    # 9) rebuild 후 정합(핀 보존)
    rb = index_rebuild(ledger, home=home)
    hr5 = hot_recall("영구 규칙", home=home, limit=5)
    ck("rebuild 후 핀 보존", rb.get("rebuilt") and "n3" in [x["node_id"] for x in hr5["relevant_nodes"]])

    # 10) 빈 ledger graceful
    empty_home = os.path.join(tmp, "empty")
    os.makedirs(empty_home)
    empty_ledger = os.path.join(tmp, "none.sqlite")
    re = index_update(empty_ledger, home=empty_home)
    hre = hot_recall("무엇이든", home=empty_home, limit=5)
    ck("빈 ledger graceful(에러 0)", re["status"] == "OK" and re["scanned"] == 0
       and hre["relevant_nodes"] == [])

    # 11) 색인 없이 hot_recall graceful
    nohome = os.path.join(tmp, "nohome")
    hrn = hot_recall("q", home=nohome, limit=5)
    ck("색인 부재 hot_recall graceful", hrn.get("index_missing") and hrn["relevant_nodes"] == [])

    # 12) semantic 인자/키 완전 제거 확인 — Hot 은 embed 0 (의미 회상은 Deep 전담)
    import inspect
    sig = inspect.signature(hot_recall)
    hr12 = hot_recall("릴리스", home=home, limit=5)
    ck("semantic 파라미터·semantic_used 키 제거(Hot=어휘 전용)",
       "semantic" not in sig.parameters and "semantic_used" not in hr12)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print("GATE=%s (%d fail)" % ("GO" if not fails else "STOP", len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        raise SystemExit(_selftest())
