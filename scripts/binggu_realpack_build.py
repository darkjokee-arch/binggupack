# -*- coding: utf-8 -*-
"""binggu_realpack_build — active 확정 노드 → worker packs.json(opencrab-pack-v1) 변환.

PC-mediated read 공유: 로컬 ledger 의 active(SAVE 확정) 노드를 hosted worker(index.real.ts)가
읽는 packs.json 형식으로 변환. 폰/claude.ai/ChatGPT 에서 실 데이터 조회 가능하게 하는 빌드 단계.

불변(설계·load_packs.ts 스펙 정합):
- ledger read-only(mode=ro) — 실 ledger write 0.
- active 만(candidate 미SAVE 제외). active 0 이면 NO_REAL_LEDGER_DATA BLOCK.
- hosted 는 candidate 전용 → active 도 candidate:true 로 표시(promotion_allowed=false 강제). 확정 도장은 PC 에만.
- node↔evidence 연결 = node_id 'node:CONV:<h8>' ↔ evidence_id 'EVC-CONV-<h8>' (실 저장 규칙). 미연결 노드 제외.
- counts 정합 + evidence_refs ⊆ evidence_index (load_packs fail-closed 통과).
- cloud upload / 배포 / data/ write 0 — 이 모듈은 packs 구조 생성·반환만. 실제 배포는 별도 owner GO.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_p3_real_ledger as P3

FORMAT_VERSION = "opencrab-pack-v1"
DEFAULT_PACK_ID = "real/owner_judgments"
DEFAULT_TITLE = "내 확정 판단(SAVE 확정분)"


def _h8(node_id):
    """node:CONV:<h8> → h8 (마지막 콜론 뒤). 형식 외면 None."""
    m = re.match(r"^node:CONV:([0-9a-f]{6,})$", str(node_id or ""))
    return m.group(1) if m else None


def build_packs(ledger_path=P3.DEFAULT_LEDGER, pack_id=DEFAULT_PACK_ID, title=DEFAULT_TITLE):
    """active 노드 → worker packs 구조(dict). status BLOCK/OK. data/ write 0."""
    ext = P3.extract_real_ledger(ledger_path)
    stats = {k: ext[k] for k in ("total_nodes", "candidate_nodes", "active_nodes", "evidence_count")}
    if ext["active_nodes"] == 0:
        return {"status": "BLOCK", "reason": "NO_REAL_LEDGER_DATA", "stats": stats}

    ev_sent = {r[0]: r[1] for r in ext["evidence_rows"]}  # evidence_id → sentence

    nodes, ev_index, ev_chunk = [], [], []
    seen_ev, skipped = set(), []
    for r in ext["active_rows"]:
        node_id, node_type, sentence = r[0], r[1], r[2]
        h8 = _h8(node_id)
        ev_id = ("EVC-CONV-" + h8) if h8 else None
        if not ev_id or ev_id not in ev_sent:
            skipped.append({"node_id": node_id, "reason": "evidence 미연결"})
            continue  # evidence 없는 노드는 load_packs fail → 제외
        nodes.append({
            "id": node_id,
            "promotion_allowed": False,
            "properties": {"candidate": True, "label_kind": node_type, "sentence": sentence},
            "evidence_refs": [ev_id],
        })
        if ev_id not in seen_ev:
            ev_index.append({"evidence_id": ev_id})
            ev_chunk.append({"item_id": ev_id, "text": ev_sent[ev_id]})
            seen_ev.add(ev_id)

    if not nodes:
        return {"status": "BLOCK", "reason": "NO_LINKED_ACTIVE_NODES",
                "stats": stats, "skipped": skipped}

    manifest = {
        "format_version": FORMAT_VERSION,
        "pack_id": pack_id,
        "title": title,
        "status": "candidate",  # validated 금지(hosted candidate 전용)
        "promotion_allowed_default": False,
        "counts": {"nodes": len(nodes), "edges": 0, "evidence": len(ev_index)},
    }
    pack = {"manifest": manifest, "nodes": nodes, "edges": [],
            "evidence_index": ev_index, "evidence_chunk": ev_chunk}
    return {"status": "OK", "stats": stats, "skipped": skipped,
            "built": {"nodes": len(nodes), "evidence": len(ev_index)},
            "packs": [pack]}


def validate_packs_obj(obj):
    """load_packs.ts 게이트를 Python 으로 모사 — 배포 전 자가 검증(위반 목록 반환, 빈=통과)."""
    errs = []
    packs = obj.get("packs")
    if not isinstance(packs, list) or not (1 <= len(packs) <= 70):
        errs.append("packs count out of range")
        return errs
    seen = set()
    for p in packs:
        m = p.get("manifest") or {}
        pid = m.get("pack_id")
        if m.get("format_version") != FORMAT_VERSION:
            errs.append("format_version mismatch")
        if pid in seen:
            errs.append("duplicate pack_id: %s" % pid)
        seen.add(pid)
        if m.get("status") == "validated":
            errs.append("validated forbidden: %s" % pid)
        if m.get("promotion_allowed_default"):
            errs.append("promotion_allowed_default must be false: %s" % pid)
        nodes, edges = p.get("nodes", []), p.get("edges", [])
        evidx = p.get("evidence_index", [])
        evchunk = p.get("evidence_chunk", [])
        c = m.get("counts") or {}
        if c.get("nodes") != len(nodes) or c.get("edges") != len(edges) or c.get("evidence") != len(evidx):
            errs.append("counts mismatch: %s" % pid)
        ev_ids = {e.get("evidence_id") for e in evidx}
        for ch in evchunk:
            if ch.get("item_id") not in ev_ids:
                errs.append("chunk item_id not in index: %s" % pid)
        for kind, items in (("node", nodes), ("edge", edges)):
            for it in items:
                if it.get("promotion_allowed"):
                    errs.append("%s promotion_allowed must be false: %s" % (kind, it.get("id")))
                if not (it.get("properties") or {}).get("candidate"):
                    errs.append("%s must be candidate: %s" % (kind, it.get("id")))
                refs = it.get("evidence_refs") or []
                if not refs:
                    errs.append("%s without evidence_refs: %s" % (kind, it.get("id")))
                for rr in refs:
                    if rr not in ev_ids:
                        errs.append("evidence_ref not in index: %s" % it.get("id"))
    return errs


# ---------------- 셀프테스트 (synthetic ledger 주입 — 실 ledger/배포 미접촉) ----------------
def _selftest():
    import sqlite3
    import tempfile
    ok = 0
    tot = 0

    def chk(name, cond):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("  PASS " if cond else "  FAIL ") + name)

    # synthetic ledger 생성: active 2 + candidate 1 + evidence
    work = tempfile.mkdtemp(prefix="realpack_st_")
    lp = os.path.join(work, "ledger.sqlite")
    conn = sqlite3.connect(lp)
    conn.executescript("""
        CREATE TABLE nodes(node_id TEXT, node_type TEXT, sentence TEXT, candidate INT, state TEXT, content_hash TEXT);
        CREATE TABLE evidence(evidence_id TEXT, sentence TEXT, source_pointer_id TEXT, source_hash TEXT);
    """)
    conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?)",
                 ("node:CONV:aaaaaaaa", "Claim", "확정 판단 가나다", 0, "active", "h1"))
    conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?)",
                 ("node:CONV:bbbbbbbb", "Claim", "확정 판단 라마바", 0, "active", "h2"))
    conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?)",
                 ("node:CONV:cccccccc", "Claim", "미확정 후보 사아자", 1, None, "h3"))  # candidate
    conn.execute("INSERT INTO evidence VALUES(?,?,?,?)",
                 ("EVC-CONV-aaaaaaaa", "확정 판단 가나다", "conv-self:aaaaaaaa", "aaaaaaaa"))
    conn.execute("INSERT INTO evidence VALUES(?,?,?,?)",
                 ("EVC-CONV-bbbbbbbb", "확정 판단 라마바", "conv-self:bbbbbbbb", "bbbbbbbb"))
    conn.commit()
    conn.close()

    r = build_packs(lp)
    chk("T1 status OK", r["status"] == "OK")
    chk("T2 active 2만 빌드(candidate 제외)", r["built"]["nodes"] == 2)
    pack = r["packs"][0]
    chk("T3 candidate:true 강제(active→후보 표시)", all(n["properties"]["candidate"] for n in pack["nodes"]))
    chk("T4 promotion_allowed false", all(n["promotion_allowed"] is False for n in pack["nodes"]))
    chk("T5 counts 정합", pack["manifest"]["counts"]["nodes"] == 2 and pack["manifest"]["counts"]["evidence"] == 2)
    chk("T6 evidence_refs 연결", pack["nodes"][0]["evidence_refs"][0].startswith("EVC-CONV-"))
    chk("T7 load_packs 게이트 통과(위반 0)", validate_packs_obj(r) == [])
    chk("T8 format_version", pack["manifest"]["format_version"] == FORMAT_VERSION)
    chk("T9 status validated 아님", pack["manifest"]["status"] != "validated")

    # active 0 → BLOCK
    lp2 = os.path.join(work, "empty.sqlite")
    c2 = sqlite3.connect(lp2)
    c2.executescript("CREATE TABLE nodes(node_id TEXT,node_type TEXT,sentence TEXT,candidate INT,state TEXT,content_hash TEXT);"
                     "CREATE TABLE evidence(evidence_id TEXT,sentence TEXT,source_pointer_id TEXT,source_hash TEXT);")
    c2.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?)", ("node:CONV:dddddddd", "Claim", "후보뿐", 1, None, "h"))
    c2.commit()
    c2.close()
    chk("T10 active 0 → NO_REAL_LEDGER_DATA", build_packs(lp2)["reason"] == "NO_REAL_LEDGER_DATA")

    # evidence 미연결 노드 → 제외
    lp3 = os.path.join(work, "noev.sqlite")
    c3 = sqlite3.connect(lp3)
    c3.executescript("CREATE TABLE nodes(node_id TEXT,node_type TEXT,sentence TEXT,candidate INT,state TEXT,content_hash TEXT);"
                     "CREATE TABLE evidence(evidence_id TEXT,sentence TEXT,source_pointer_id TEXT,source_hash TEXT);")
    c3.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?)", ("node:CONV:eeeeeeee", "Claim", "증거없는 확정", 0, "active", "h"))
    c3.commit()
    c3.close()
    chk("T11 evidence 미연결 active → 제외(BLOCK NO_LINKED)", build_packs(lp3)["reason"] == "NO_LINKED_ACTIVE_NODES")

    print("\nRESULT: %d/%d %s" % (ok, tot, "PASS" if ok == tot else "FAIL"))
    print("GATE: %s" % ("GO" if ok == tot else "BLOCK"))
    return ok == tot


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # 실 ledger 미리보기(빌드만 — data/ write 0, 배포 0)
    import json
    res = build_packs()
    if res["status"] == "OK":
        res_view = {"status": res["status"], "stats": res["stats"], "built": res["built"],
                    "skipped": res["skipped"],
                    "preview_sentences": [n["properties"]["sentence"] for n in res["packs"][0]["nodes"]],
                    "load_packs_violations": validate_packs_obj(res)}
        print(json.dumps(res_view, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
