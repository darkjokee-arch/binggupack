#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BingguPack — promotion preview (read-only plan tool).

batch pack(candidate)을 로컬 운영형 그래프 DB로 승격하기 **전에**, 어떤 변환·write가
일어날지 미리 보여주는 **preview/plan 도구**입니다. 승격 실행기가 아닙니다.

기준: docs/BINGGUPACK_PROMOTION_PREVIEW_DESIGN.md (D1~D4 변환 규칙 + target schema contract).

read-only 보장:
  - target DB는 항상 `mode=ro` URI로만 엽니다. INSERT/UPDATE/DELETE 코드 자체가 없습니다.
  - `OPENBINGGU_OPERATING_DB` 미지정 시 synthetic temp DB를 만들어 시연합니다(사용자 데이터 0).
  - write/apply/confirmed/upload는 이 도구의 범위 밖입니다(별도 단계·별도 승인).

변환 규칙 (D1~D4):
  D1  label = sentence 80자 절단(이하면 원문)
  D2  space = node_type 자동 매핑 / label_kind = pack properties 값 / domain = 호출자가 1개 지정
  D3  verb = relation→동사 매핑표 / edge_sentence_ko = pack properties.sentence 우선, 없으면 자동 생성
      미등록 relation/node_type = fail-closed 전체 STOP(부분 승격 계획 금지)
  D4  FTS(node_search.domain_title)는 NULL 유지 — domain 값은 nodes.domain에만

CLI:
  python openbinggu_promotion_preview.py --selftest
  python openbinggu_promotion_preview.py --pack-dir <batch_pack 디렉터리> --domain <D코드> [--sample N]
      # OPENBINGGU_OPERATING_DB 지정 시 그 DB를 read-only로 대조, 미지정 시 synthetic temp DB
"""
from pathlib import Path
import os
import re
import sys
import json
import hashlib
import shutil
import sqlite3
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from watcher_batch_m1 import scan_residual_pii  # noqa: E402  (기존 엔진 무수정 재사용)

ENV_TARGET = "OPENBINGGU_OPERATING_DB"
PACK_FILES = ("manifest.json", "nodes.jsonl", "edges.jsonl", "evidence_chunk.jsonl")
DOMAIN_RE = re.compile(r"^D[1-9][0-9]?$")

# D2: node_type → space 자동 매핑 (미등록 = fail-closed)
SPACE_MAP = {"Claim": "claim", "Document": "resource", "Evidence": "evidence", "Concept": "concept"}

# D3: relation → 한국어 동사 매핑표 (단일 기준, 미등록 = fail-closed)
VERB_MAP = {
    "contains": "포함한다", "describes": "설명한다",
    "supports": "뒷받침한다", "supports_judgment": "뒷받침한다", "evidence_supports": "뒷받침한다",
    "contradicts": "모순된다", "depends_on": "의존한다", "blocks": "차단한다",
    "enables": "가능하게 한다", "refines": "정밀화한다",
}

# target schema contract (preview가 대조하는 운영형 그래프 최소 계약)
TARGET_SCHEMA = """
CREATE TABLE nodes(id TEXT PRIMARY KEY, space TEXT, node_type TEXT, label TEXT, domain TEXT,
    label_kind TEXT, sentence TEXT, candidate INTEGER, evidence_status TEXT,
    promotion_allowed INTEGER, json TEXT);
CREATE TABLE edges(id TEXT PRIMARY KEY, source TEXT, target TEXT, relation TEXT, verb TEXT,
    edge_sentence_ko TEXT, candidate INTEGER, promotion_allowed INTEGER, json TEXT);
CREATE TABLE evidence(evidence_id TEXT PRIMARY KEY, domain TEXT, kind TEXT, source_path TEXT,
    note TEXT, promotion_allowed INTEGER, json TEXT);
CREATE VIRTUAL TABLE node_search USING fts5(id, label, sentence, domain_title, content='');
CREATE VIRTUAL TABLE edge_search USING fts5(id, edge_sentence_ko, verb, relation, content='');
CREATE VIRTUAL TABLE evidence_search USING fts5(evidence_id, note, source_path, content='');
"""


def _line(c="-"):
    print(c * 78)


def load_pack_raw(pack_dir):
    """batch pack 디렉터리 raw 로드 (properties 보존, read-only). fail-closed manifest 검사."""
    d = os.path.abspath(pack_dir)
    for f in PACK_FILES:
        if not os.path.isfile(os.path.join(d, f)):
            raise FileNotFoundError("pack_file_missing:" + f)
    with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("pack_type") != "candidate":
        raise ValueError("pack_type_not_candidate")
    if manifest.get("promotion_allowed_default") is not False:
        raise ValueError("promotion_allowed_default_not_false")

    def _jsonl(name):
        with open(os.path.join(d, name), encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]
    return manifest, _jsonl("nodes.jsonl"), _jsonl("edges.jsonl"), _jsonl("evidence_chunk.jsonl")


def d1_label(sentence):
    s = sentence or ""
    return s if len(s) <= 80 else s[:80]


def transform(manifest, nodes, edges, chunks, domain):
    """D1~D4 변환 계획. 미등록 node_type/relation·label_kind 누락·refs 빈값 = stop 목록."""
    stops, pn, pe, pv = [], [], [], []
    for n in nodes:
        nt = n.get("node_type")
        if nt not in SPACE_MAP:
            stops.append(("unregistered_node_type", n["id"])); continue
        lk = (n.get("properties") or {}).get("label_kind")
        if not lk:
            stops.append(("label_kind_missing", n["id"])); continue
        sent = (n.get("properties") or {}).get("sentence", "")
        pn.append({"id": n["id"], "space": SPACE_MAP[nt], "node_type": nt,
                   "label": d1_label(sent), "domain": domain, "label_kind": lk,
                   "sentence": sent, "candidate": 1, "promotion_allowed": 0,
                   "evidence_status": "partial"})
    for e in edges:
        rel = (e.get("properties") or {}).get("relation")
        if rel not in VERB_MAP:
            stops.append(("unregistered_relation", e["id"])); continue
        if not (e.get("evidence_refs") or []):
            stops.append(("evidence_refs_missing", e["id"])); continue
        verb = VERB_MAP[rel]
        pe.append({"id": e["id"], "source": e["source"], "target": e["target"],
                   "relation": rel, "verb": verb,
                   "edge_sentence_ko": (e.get("properties") or {}).get("sentence")
                                       or f"{verb} 관계 (relation={rel})"})
    for c in chunks:
        pv.append({"evidence_id": c["item_id"], "domain": domain,
                   "note": c.get("text", ""),
                   "source_path": "hash:" + hashlib.sha256(
                       str(c.get("source", "")).encode()).hexdigest()[:8]})
    return pn, pe, pv, stops


def residual_scan(pn, pv):
    kinds = set()
    for s in [p["sentence"] for p in pn] + [p["note"] for p in pv]:
        kinds.update(scan_residual_pii(s))
    return sorted(kinds)


def make_synthetic_target(tmp):
    """OPENBINGGU_OPERATING_DB 미지정 시 사용하는 synthetic 운영형 DB (toy seed 1건씩)."""
    p = os.path.join(tmp, "synthetic_target.sqlite")
    con = sqlite3.connect(p)
    con.executescript(TARGET_SCHEMA)
    con.execute("INSERT INTO nodes VALUES('node:SEED:1','claim','Claim','시드','D1','판단','시드 문장',1,'partial',0,'{}')")
    con.execute("INSERT INTO edges VALUES('edge:SEED:1','a','b','supports','뒷받침한다','시드 엣지',1,0,'{}')")
    con.execute("INSERT INTO evidence VALUES('EVC-SEED-1','D1','seed','sp','시드 노트',0,'{}')")
    con.execute("INSERT INTO node_search VALUES('node:SEED:1','시드','시드 문장',NULL)")
    con.execute("INSERT INTO edge_search VALUES('edge:SEED:1','시드 엣지','뒷받침한다','supports')")
    con.execute("INSERT INTO evidence_search VALUES('EVC-SEED-1','시드 노트','sp')")
    con.commit(); con.close()
    return p


def preview(pack_dir, domain, target_db, sample_n=3, quiet=False):
    """preview 본체. target_db는 read-only로만 연다. 반환 = report dict."""
    out = print if not quiet else (lambda *a, **k: None)
    report = {"read_only": True}
    mt_before = os.path.getmtime(target_db)

    manifest, nodes, edges, chunks = load_pack_raw(pack_dir)
    out(f"\n[1] pack: pack_id={manifest['pack_id']}  "
        f"nodes={len(nodes)} edges={len(edges)} evidence={len(chunks)}  domain={domain}")
    pn, pe, pv, stops = transform(manifest, nodes, edges, chunks, domain)
    pii = residual_scan(pn, pv)
    out(f"    PII/secret 재스캔(kind만): {pii if pii else '0건'}")
    report.update({"pack_id": manifest["pack_id"], "stops": sorted({s[0] for s in stops}),
                   "pii_kinds": pii})
    if pii or stops:
        out(f"STOP(fail-closed): pii={pii} stops={report['stops']} — 부분 계획 금지, 전체 중단")
        report["verdict"] = "STOP"
        return report

    truncated = sum(1 for p in pn if len(p["sentence"]) > 80)
    out(f"\n[2] D1 label 변환(샘플 {sample_n}건, id·길이만):")
    for p in pn[:sample_n]:
        out(f"    {p['id']}  sentence_len={len(p['sentence'])} → label_len={len(p['label'])}")
    out(f"    절단 {truncated} / 원문 유지 {len(pn) - truncated}")
    sp = {}; lk = {}
    for p in pn:
        sp[p["space"]] = sp.get(p["space"], 0) + 1
        lk[p["label_kind"]] = lk.get(p["label_kind"], 0) + 1
    out(f"[3] D2: space={sp}  label_kind={lk}  domain={domain}(전건)")
    rel = {}
    for p in pe:
        k = f"{p['relation']}→{p['verb']}"; rel[k] = rel.get(k, 0) + 1
    out(f"[4] D3: {rel}")

    # target DB read-only 대조
    con = sqlite3.connect("file:" + target_db + "?mode=ro", uri=True)
    base = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("nodes", "edges", "evidence")}
    fts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
           for t in ("node_search", "edge_search", "evidence_search")}
    coll = (sum(1 for p in pn if con.execute("SELECT 1 FROM nodes WHERE id=?", (p["id"],)).fetchone())
            + sum(1 for p in pe if con.execute("SELECT 1 FROM edges WHERE id=?", (p["id"],)).fetchone())
            + sum(1 for p in pv if con.execute(
                "SELECT 1 FROM evidence WHERE evidence_id=?", (p["evidence_id"],)).fetchone()))
    con.close()
    out(f"\n[5] target 대조(read-only): 본 테이블 {base} / FTS {fts} / id 충돌 {coll}건")
    out(f"[6] FTS insert 계획: node_search +{len(pn)}(domain_title=NULL, D4) / "
        f"edge_search +{len(pe)} / evidence_search +{len(pv)}")
    out("    ⚠ contentless FTS(content='')는 컬럼 값을 되읽을 수 없음 — 승격 후 검증은 "
        "count + MATCH + rowid join으로 (설계 문서 참조)")
    out(f"[7] backup 계획: 승격 직전 target 파일 copy2 → snap_<checksum8>.sqlite + checksum 대조")
    out(f"[8] rollback 계획: 검증 실패 시 snapshot 복원 → checksum·count 원복 확인")
    total = (len(pn) + len(pe) + len(pv)) * 2
    out(f"[9] 실행된다면 write될 row: 본 {len(pn)+len(pe)+len(pv)} + FTS 동일 = 총 {total} "
        f"(단일 transaction 권장) — 이 도구는 실행하지 않음(read-only)")

    mt_ok = os.path.getmtime(target_db) == mt_before
    report.update({"collisions": coll, "base_counts": base, "fts_counts": fts,
                   "planned_writes": total, "target_unchanged": mt_ok,
                   "verdict": "GO" if (coll == 0 and mt_ok) else "NO-GO"})
    return report


# ── synthetic selftest ────────────────────────────────────────────────────────

def _make_synthetic_pack(tmp, pack_id="preview_toy", n=4, bad_relation=False,
                         bad_node_type=False, pii=False, bad_manifest=None):
    d = os.path.join(tmp, pack_id); os.makedirs(d, exist_ok=True)
    long_sent = "긴 문장 " * 20
    manifest = {"format_version": "opencrab-pack-v1", "pack_id": pack_id,
                "pack_type": "candidate", "promotion_allowed_default": False}
    if bad_manifest:
        manifest.update(bad_manifest)
    nodes, edges, chunks = [], [], []
    for i in range(n):
        sent = long_sent if i == 0 else f"toy 판단 {i}: 마진 확보 시 진행"
        if pii and i == 1:
            # PII-like fixture는 런타임 조각조합으로만 (정적 tree scan 미검출, 실값 아님)
            sent += " 연락처 " + "-".join(("010", "1234", "5678"))
        nid, eid, vid = f"node:PV:{i}", f"edge:PV:{i}", f"EVC-PV-{i}"
        nt = "NoSuchType" if (bad_node_type and i == 0) else "Claim"
        rl = "no_such_relation" if (bad_relation and i == 0) else "evidence_supports"
        nodes.append({"id": nid, "node_type": nt, "evidence_refs": [vid],
                      "promotion_allowed": False,
                      "properties": {"candidate": True, "label_kind": "판단", "sentence": sent}})
        edges.append({"id": eid, "source": vid, "target": nid, "evidence_refs": [vid],
                      "promotion_allowed": False,
                      "properties": {"candidate": True, "relation": rl,
                                     "sentence": f"evidence가 노드 {i}를 뒷받침한다"}})
        chunks.append({"item_id": vid, "source": f"toy_src_{i}", "text": sent})
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    for name, rows in (("nodes.jsonl", nodes), ("edges.jsonl", edges),
                       ("evidence_chunk.jsonl", chunks)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return d


def _selftest():
    print("=" * 80)
    print("BingguPack promotion preview selftest (synthetic/temp only, target read-only)")
    print("=" * 80)
    tmp = tempfile.mkdtemp(prefix="binggupack_preview_")
    results = []

    def rec(cid, name, ok):
        results.append(ok)
        print(f"  [{'OK' if ok else 'NG'}] {cid} {name}")

    try:
        target = make_synthetic_target(tmp)
        pack = _make_synthetic_pack(tmp)
        r = preview(pack, "D10", target, quiet=True)
        rec("P1", "정상 preview verdict=GO + 충돌 0", r["verdict"] == "GO" and r["collisions"] == 0)
        rec("P2", "write 계획 산출 (본+FTS 합)", r["planned_writes"] == 8 * 3)
        rec("P3", "target mtime 불변(read-only)", r["target_unchanged"])
        m, n, e, c = load_pack_raw(pack)
        pn, pe, pv, stops = transform(m, n, e, c, "D10")
        rec("P4", "D1: 80자 초과 절단 + 이하 원문",
            len(pn[0]["label"]) == 80 and pn[1]["label"] == pn[1]["sentence"])
        rec("P5", "D2: space 자동 + label_kind pack값",
            pn[0]["space"] == "claim" and pn[0]["label_kind"] == "판단")
        rec("P6", "D3: verb 매핑 + edge_sentence_ko pack값 우선",
            pe[0]["verb"] == "뒷받침한다" and "뒷받침한다" in pe[0]["edge_sentence_ko"])
        r2 = preview(_make_synthetic_pack(tmp, "pv_badrel", bad_relation=True), "D10", target, quiet=True)
        rec("P7", "미등록 relation → STOP", r2["verdict"] == "STOP"
            and "unregistered_relation" in r2["stops"])
        r3 = preview(_make_synthetic_pack(tmp, "pv_badnt", bad_node_type=True), "D10", target, quiet=True)
        rec("P8", "미등록 node_type → STOP", r3["verdict"] == "STOP"
            and "unregistered_node_type" in r3["stops"])
        r4 = preview(_make_synthetic_pack(tmp, "pv_pii", pii=True), "D10", target, quiet=True)
        rec("P9", "PII 잔존 → STOP(kind만)", r4["verdict"] == "STOP" and len(r4["pii_kinds"]) >= 1)
        try:
            load_pack_raw(_make_synthetic_pack(tmp, "pv_badmf", bad_manifest={"pack_type": "confirmed"}))
            rec("P10", "manifest pack_type 거부", False)
        except ValueError as ex:
            rec("P10", "manifest pack_type 거부", str(ex) == "pack_type_not_candidate")
        # 충돌 검출: seed id를 가진 pack
        d11 = _make_synthetic_pack(tmp, "pv_coll", n=1)
        nl = os.path.join(d11, "nodes.jsonl")
        rows = [json.loads(x) for x in Path(nl).read_text(encoding='utf-8').splitlines(keepends=True)]
        rows[0]["id"] = "node:SEED:1"
        with open(nl, "w", encoding="utf-8") as f:
            for rr in rows:
                f.write(json.dumps(rr, ensure_ascii=False) + "\n")
        r5 = preview(d11, "D10", target, quiet=True)
        rec("P11", "id 충돌 검출 → verdict NO-GO", r5["collisions"] == 1 and r5["verdict"] == "NO-GO")
        # contentless FTS 검증법: 값 되읽기 NULL + MATCH/rowid join은 동작
        con = sqlite3.connect("file:" + target + "?mode=ro", uri=True)
        tnull = con.execute("SELECT typeof(id) FROM node_search LIMIT 1").fetchone()[0]
        hit = con.execute("SELECT rowid FROM node_search WHERE node_search MATCH '시드'").fetchone()
        joined = con.execute("SELECT id FROM nodes WHERE rowid=?", (hit[0],)).fetchone() if hit else None
        con.close()
        rec("P12", "contentless FTS: 값 되읽기 null + MATCH/rowid join 동작",
            tnull == "null" and joined is not None and joined[0] == "node:SEED:1")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n_pass = sum(results)
    print("-" * 80)
    print(f"RESULT: {n_pass}/{len(results)} PASS   (write 코드 없음 · target read-only)")
    print(f"GATE: {'GO' if n_pass == len(results) else 'NO-GO'}")
    return 0 if n_pass == len(results) else 1


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        return _selftest()
    pack_dir = domain = None
    sample_n = 3
    for i, a in enumerate(args):
        if a == "--pack-dir" and i + 1 < len(args): pack_dir = args[i + 1]
        if a == "--domain" and i + 1 < len(args): domain = args[i + 1]
        if a == "--sample" and i + 1 < len(args): sample_n = int(args[i + 1])
    if not pack_dir or not domain or not DOMAIN_RE.match(domain or ""):
        print(__doc__); return 2
    _line("=")
    print("BingguPack promotion preview (read-only · 승격 실행 아님)")
    _line("=")
    env_db = os.environ.get(ENV_TARGET)
    if env_db:
        target = env_db
        print(f"target = ${ENV_TARGET} (read-only로만 엽니다)")
    else:
        tmpd = tempfile.mkdtemp(prefix="binggupack_preview_")
        target = make_synthetic_target(tmpd)
        print(f"target = synthetic temp DB (${ENV_TARGET} 미지정)")
    r = preview(pack_dir, domain, target, sample_n=sample_n)
    _line("=")
    print(f"PREVIEW VERDICT: {r['verdict']}  (이 도구는 어떤 write도 하지 않습니다)")
    _line("=")
    return 0 if r["verdict"] == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
