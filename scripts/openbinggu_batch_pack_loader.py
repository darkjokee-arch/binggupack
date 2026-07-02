#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BingguPack — batch pack loader (opencrab-pack-v1 jsonl directory → staging apply demo).

watcher/pack_build가 만든 batch pack 디렉터리(manifest.json + nodes/edges/evidence_*.jsonl)를
staging pack dict로 변환해, local persistence 게이트(phase2_apply)를 통해
**staging SQLite에 apply → read-back → rollback(원복)** 까지 한 번에 검증하는 실행기.

기존 엔진 무수정 재사용:
  - openbinggu_staging_write_selftest: StagingDB / staging_apply / _hash (C-2 자동검사·audit)
  - openbinggu_phase2_local_persistence_selftest: user_staging_path / phase2_apply (write OFF 기본·EMERGENCY_STOP)
  - watcher_batch_m1: scan_residual_pii (apply 직전 잔존 재스캔, kind만·raw 미출력)

불변: 운영 store write 0 · 실제 사용자 홈 write 0(--selftest는 temp HOME만) ·
      candidate-only(promotion_allowed=0) · confirmed/promote 0 · upload/push 0 ·
      write는 명시 opt-in(--enable-write) 시에만 · raw 경로/PII/secret 미출력(id·hash·count만).

freshness: batch pack에는 capture 해시가 없으므로 load 시점 text 해시를
           source_hash/captured_hash 양쪽에 동일 기록(적재 직전 재캡처 일치 기준).

CLI:
  python openbinggu_batch_pack_loader.py --selftest
      # synthetic batch pack을 temp에 만들어 load→apply→read-back→rollback 전 과정 검증 (권장 진입점)
  python openbinggu_batch_pack_loader.py --pack-dir <dir> --enable-write [--user <id>] [--keep]
      # 자기 pack 디렉터리를 OPENBINGGU_HOME staging에 적재. 기본은 rollback 원복(--keep 시 유지).
"""
import os
import sys
import json
import shutil
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import (  # noqa: E402
    StagingDB, OPERATING_PATHS, _hash,
)
from openbinggu_phase2_local_persistence_selftest import (  # noqa: E402
    resolve_home, user_staging_path, phase2_apply,
)
from watcher_batch_m1 import scan_residual_pii  # noqa: E402

PACK_FILES = ("manifest.json", "nodes.jsonl", "edges.jsonl", "evidence_chunk.jsonl")


def load_batch_pack(pack_dir):
    """opencrab-pack-v1 batch pack 디렉터리 → staging pack dict. 원본 read-only.

    fail-closed: manifest가 candidate / promotion_allowed_default=false 가 아니면 거부.
    """
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

    raw_nodes = _jsonl("nodes.jsonl")
    raw_edges = _jsonl("edges.jsonl")
    raw_chunks = _jsonl("evidence_chunk.jsonl")
    nodes = [{"id": n["id"], "type": n["node_type"], "sentence": n["properties"]["sentence"]}
             for n in raw_nodes]
    edges = [{"id": e["id"], "relation": e["properties"]["relation"], "source": e["source"],
              "target": e["target"], "evidence_refs": e["evidence_refs"]} for e in raw_edges]
    evidence = []
    for c in raw_chunks:
        th = _hash(c["text"])
        evidence.append({"id": c["item_id"], "sentence": c["text"], "source_missing": False,
                         "source_hash": th, "captured_hash": th, "redaction_policy": "v1",
                         "source_pointer_id": c.get("source", "sp")})
    content = "\n".join(sorted(
        json.dumps(x, ensure_ascii=False, sort_keys=True)
        for x in raw_nodes + raw_edges + raw_chunks))
    return {"pack_id": manifest["pack_id"], "content": content,
            "nodes": nodes, "edges": edges, "evidence": evidence}


def residual_scan(pack):
    """apply 직전 PII/secret 잔존 재스캔. kind 목록만 반환(raw 미반환)."""
    kinds = set()
    for s in [n["sentence"] for n in pack["nodes"]] + [e["sentence"] for e in pack["evidence"]]:
        kinds.update(scan_residual_pii(s))
    return sorted(kinds)


def apply_with_rollback(home, user_id, pack, keep=False):
    """phase2_apply 경유 staging apply → read-back 검증 → (기본) snapshot rollback 원복."""
    path = user_staging_path(home, user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # apply 전 snapshot (rollback 기준점)
    db = StagingDB(path)
    db.con.execute("PRAGMA wal_checkpoint(TRUNCATE)"); db.con.commit()
    before = db.store_checksum()
    snap = path + ".snap_before"
    db.close()
    shutil.copy2(path, snap)

    r = phase2_apply(home, user_id, pack, {"actor": "human"}, write_enabled=True)
    out = {"applied": bool(r.get("applied")), "reason": r.get("reason"),
           "before_checksum": before}
    if not out["applied"]:
        os.remove(snap)
        return out

    exp = (len(pack["nodes"]), len(pack["edges"]), len(pack["evidence"]))
    db = StagingDB(path)
    db.con.execute("PRAGMA wal_checkpoint(TRUNCATE)"); db.con.commit()
    got = tuple(db.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("nodes", "edges", "evidence"))
    cand_ok = db.con.execute(
        "SELECT count(*) FROM nodes WHERE candidate=1 AND promotion_allowed=0 AND state='active'"
    ).fetchone()[0]
    promo_bad = db.con.execute("SELECT count(*) FROM nodes WHERE promotion_allowed=1").fetchone()[0]
    refs_bad = db.con.execute(
        "SELECT count(*) FROM edges WHERE evidence_refs IS NULL OR evidence_refs='[]'").fetchone()[0]
    after = db.store_checksum()
    chain = db.verify_chain()
    db.close()
    out.update({"counts": dict(zip(("nodes", "edges", "evidence"), got)),
                "readback": (got == exp and cand_ok == got[0] and promo_bad == 0
                             and refs_bad == 0 and after != before and chain),
                "promotion_violations": promo_bad, "after_checksum": after})

    if keep:
        os.remove(snap)
        out["rolled_back"] = False
        return out
    # rollback: snapshot 복원 + wal/shm 제거 → checksum 원복 검증
    shutil.copy2(snap, path)
    for ext in ("-wal", "-shm"):
        if os.path.exists(path + ext):
            os.remove(path + ext)
    os.remove(snap)
    db = StagingDB(path)
    db.con.execute("PRAGMA wal_checkpoint(TRUNCATE)"); db.con.commit()
    rb = db.store_checksum()
    rb_nodes = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    db.close()
    out.update({"rolled_back": True, "rollback_restored": rb == before,
                "rollback_checksum": rb, "rollback_nodes": rb_nodes})
    return out


# ── synthetic fixture (selftest 전용, toy 데이터만) ─────────────────────────

def build_synthetic_pack_dir(root, pack_id="toy_batch_1", n=3, bad_manifest=None, pii=False):
    d = os.path.join(root, pack_id)
    os.makedirs(d, exist_ok=True)
    manifest = {"format_version": "opencrab-pack-v1", "pack_id": pack_id,
                "pack_type": "candidate", "promotion_allowed_default": False,
                "counts": {"nodes": n, "edges": n, "evidence": n}}
    if bad_manifest:
        manifest.update(bad_manifest)
    nodes, edges, chunks = [], [], []
    for i in range(n):
        sent = f"toy 판단 문장 {i}: 마진이 확보되면 진행한다"
        if pii and i == 0:
            # PII-like fixture는 런타임 조각조합으로만 생성 (정적 tree scan 미검출, 실값 아님)
            sent += " 연락처 " + "-".join(("010", "1234", "5678"))
        nid, eid, vid = f"node:TOY:{i}", f"edge:TOY:{i}", f"EVC-TOY-{i}"
        nodes.append({"id": nid, "node_type": "Claim", "evidence_refs": [vid],
                      "promotion_allowed": False,
                      "properties": {"candidate": True, "sentence": sent}})
        edges.append({"id": eid, "source": vid, "target": nid, "evidence_refs": [vid],
                      "promotion_allowed": False,
                      "properties": {"candidate": True, "relation": "evidence_supports"}})
        chunks.append({"item_id": vid, "source": f"toy_source_{i}", "text": sent})
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    for name, rows in (("nodes.jsonl", nodes), ("edges.jsonl", edges),
                       ("evidence_chunk.jsonl", chunks)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return d


def _selftest():
    print("=" * 80)
    print("BingguPack — batch pack loader selftest (synthetic/temp only, 운영 write 0)")
    print("=" * 80)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="binggupack_loader_")
    home = tempfile.mkdtemp(prefix="binggupack_home_")
    results = []

    def rec(cid, name, ok):
        results.append((cid, name, "PASS" if ok else "FAIL"))
        print(f"  [{'OK' if ok else 'NG'}] {cid} {name}")

    try:
        # L1 load: counts 일치 + content 결정적
        d = build_synthetic_pack_dir(tmp)
        p1, p2 = load_batch_pack(d), load_batch_pack(d)
        rec("L1", "load counts 일치 + 결정적 직렬화",
            len(p1["nodes"]) == 3 and len(p1["edges"]) == 3 and len(p1["evidence"]) == 3
            and p1["content"] == p2["content"])
        # L2 manifest pack_type != candidate → 거부
        d2 = build_synthetic_pack_dir(tmp, "toy_bad_type", bad_manifest={"pack_type": "confirmed"})
        try:
            load_batch_pack(d2); rec("L2", "pack_type != candidate 거부", False)
        except ValueError as ex:
            rec("L2", "pack_type != candidate 거부", str(ex) == "pack_type_not_candidate")
        # L3 promotion_allowed_default=true → 거부
        d3 = build_synthetic_pack_dir(tmp, "toy_bad_promo",
                                      bad_manifest={"promotion_allowed_default": True})
        try:
            load_batch_pack(d3); rec("L3", "promotion_default=true 거부", False)
        except ValueError as ex:
            rec("L3", "promotion_default=true 거부", str(ex) == "promotion_allowed_default_not_false")
        # L4 필수 파일 누락 → 거부
        d4 = build_synthetic_pack_dir(tmp, "toy_missing")
        os.remove(os.path.join(d4, "edges.jsonl"))
        try:
            load_batch_pack(d4); rec("L4", "필수 파일 누락 거부", False)
        except FileNotFoundError:
            rec("L4", "필수 파일 누락 거부", True)
        # L5 PII 잔존 재스캔 검출 (kind만)
        d5 = build_synthetic_pack_dir(tmp, "toy_pii", pii=True)
        kinds = residual_scan(load_batch_pack(d5))
        rec("L5", "PII 잔존 재스캔 검출(kind만)", len(kinds) >= 1)
        # L6 clean pack 재스캔 0건
        rec("L6", "clean pack 재스캔 0건", residual_scan(p1) == [])
        # L7 apply + read-back (temp HOME, candidate 전건·promotion 위반 0)
        r = apply_with_rollback(home, "local", p1)
        rec("L7", "apply+read-back (candidate 전건/promotion 0/refs/audit)",
            r["applied"] and r["readback"] and r["promotion_violations"] == 0)
        # L8 rollback 원복 (checksum == before, nodes 0)
        rec("L8", "rollback 원복", r.get("rollback_restored") and r.get("rollback_nodes") == 0)
        # L9 write 기본 OFF (opt-in 없으면 write_disabled)
        r9 = phase2_apply(home, "local", p1, {"actor": "human"}, write_enabled=False)
        rec("L9", "write 기본 OFF(write_disabled)", (not r9["applied"]) and r9["reason"] == "write_disabled")
        # L10 운영 store mtime 불변
        op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
        rec("L10", "operating_store_unchanged", op_after == op_before)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)

    n_pass = sum(1 for _, _, s in results if s == "PASS")
    print("-" * 80)
    print(f"RESULT: {n_pass}/{len(results)} PASS")
    print(f"GATE: {'GO' if n_pass == len(results) else 'NO-GO'}")
    return 0 if n_pass == len(results) else 1


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        return _selftest()
    pack_dir = user_id = None
    enable_write = keep = False
    for i, a in enumerate(args):
        if a == "--pack-dir" and i + 1 < len(args):
            pack_dir = args[i + 1]
        if a == "--user" and i + 1 < len(args):
            user_id = args[i + 1]
        if a == "--enable-write":
            enable_write = True
        if a == "--keep":
            keep = True
    if not pack_dir:
        print(__doc__)
        return 2
    if not enable_write:
        print("write is OFF by default. add --enable-write to opt in (staging only).")
        return 2
    home = resolve_home(os.environ.get("OPENBINGGU_HOME"))   # repo 내부면 fail-closed 거부
    pack = load_batch_pack(pack_dir)
    kinds = residual_scan(pack)
    if kinds:
        print(f"refused: residual PII/secret kinds detected = {kinds} (raw not shown)")
        return 5
    r = apply_with_rollback(home, user_id or "local", pack, keep=keep)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("applied") and r.get("readback") else 1


if __name__ == "__main__":
    sys.exit(main())
