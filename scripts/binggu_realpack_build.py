# -*- coding: utf-8 -*-
"""binggu_realpack_build — active 확정 노드 → worker packs.json(opencrab-pack-v1) 변환.

출구: serve (hosted worker index.real.ts 가 서빙하는 data/packs.json — claude.ai/ChatGPT 실데이터 조회용).

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
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_p3_real_ledger as P3
import binggu_p1_ranking as RANK

FORMAT_VERSION = "opencrab-pack-v1"
DEFAULT_PACK_ID = "real/owner_judgments"
DEFAULT_TITLE = "내 확정 판단(SAVE 확정분)"

# hosted worker(index.real.ts)가 lazy 로드(KV)/배포 머신이 참조하는 산출 경로(gitignore).
# 빌더 write 는 이 경로로만 — 라이브 worker/KV/Cloud 미접촉(파일 write 뿐, 배포는 별도 owner GO).
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "hosted", "workers", "data", "packs.json")


def _h8(node_id):
    """node:CONV:<h8> → h8 (마지막 콜론 뒤). 형식 외면 None."""
    m = re.match(r"^node:CONV:([0-9a-f]{6,})$", str(node_id or ""))
    return m.group(1) if m else None


def build_packs(ledger_path=P3.DEFAULT_LEDGER, pack_id=DEFAULT_PACK_ID, title=DEFAULT_TITLE,
                home=None):
    """active 노드 → worker packs 구조(dict). status BLOCK/OK. data/ write 0.

    P1 ② 랭킹: created_at(신선도)+use_count(유용성)로 rank_score pre-compute(설정 가중치) →
    properties.rank_score 에 박고 노드를 점수 내림차순 정렬 → worker 는 sort 만(read-only).
    relevance(관련성)는 query 가 있을 때 worker evidence_search 가 회상 시점에 가산.
    """
    ext = P3.extract_real_ledger(ledger_path)
    weights = None  # node_rank_score 가 설정값(ranking_weights) 로드 — home 전달
    stats = {k: ext[k] for k in ("total_nodes", "candidate_nodes", "active_nodes", "evidence_count")}
    if ext["active_nodes"] == 0:
        return {"status": "BLOCK", "reason": "NO_REAL_LEDGER_DATA", "stats": stats}

    ev_sent = {r[0]: r[1] for r in ext["evidence_rows"]}  # evidence_id → sentence

    nodes, ev_index, ev_chunk = [], [], []
    seen_ev, skipped = set(), []
    for r in ext["active_rows"]:
        node_id, node_type, sentence = r[0], r[1], r[2]
        semantic_subtype = r[6] if len(r) > 6 else None  # 보조 메타(canonical 도장 아님)
        created_at = r[7] if len(r) > 7 else None         # P1 신선도 축
        use_count = r[8] if len(r) > 8 else 0             # P1 유용성 축(로컬 회상 빈도)
        h8 = _h8(node_id)
        ev_id = ("EVC-CONV-" + h8) if h8 else None
        if not ev_id or ev_id not in ev_sent:
            skipped.append({"node_id": node_id, "reason": "evidence 미연결"})
            continue  # evidence 없는 노드는 load_packs fail → 제외
        # P1 ② rank_score pre-compute(설정 가중치). relevance=0(빌드 시 중립) — worker 가 query 시 가산.
        rank_score = RANK.node_rank_score(created_at, use_count, weights=weights, home=home)
        nodes.append({
            "id": node_id,
            "promotion_allowed": False,
            "properties": {"candidate": True, "label_kind": node_type, "sentence": sentence,
                           "semantic_subtype": semantic_subtype,
                           "created_at": created_at, "use_count": int(use_count or 0),
                           "rank_score": round(rank_score, 6)},
            "evidence_refs": [ev_id],
        })
        if ev_id not in seen_ev:
            ev_index.append({"evidence_id": ev_id})
            ev_chunk.append({"item_id": ev_id, "text": ev_sent[ev_id]})
            seen_ev.add(ev_id)

    if not nodes:
        return {"status": "BLOCK", "reason": "NO_LINKED_ACTIVE_NODES",
                "stats": stats, "skipped": skipped}

    # P1 ② 정렬: rank_score 내림차순(동점은 node_id 사전순 — 결정적). worker 는 이 순서를 보존.
    nodes.sort(key=lambda n: (-n["properties"]["rank_score"], n["id"]))

    # active edge → pack edge. src·tgt 둘 다 pack 노드에 있을 때만(dangling 제외).
    # pack edge 도 hosted candidate 규약(candidate=True·promotion_allowed=False·evidence_refs 필수).
    # edge evidence_refs = src 노드의 ev_id(ev_index 에 존재 — load_packs 게이트 통과).
    node_ids = {n["id"] for n in nodes}
    node_ev = {n["id"]: n["evidence_refs"][0] for n in nodes}
    edges = []
    for er in ext.get("active_edge_rows", []):
        edge_id, relation, source, target = er[0], er[1], er[2], er[3]
        if source not in node_ids or target not in node_ids:
            skipped.append({"edge_id": edge_id, "reason": "src/tgt 노드 pack 미포함"})
            continue
        edges.append({
            "id": edge_id, "source": source, "target": target,
            "promotion_allowed": False,
            "properties": {"candidate": True, "relation": relation},
            "evidence_refs": [node_ev[source]],
        })

    manifest = {
        "format_version": FORMAT_VERSION,
        "pack_id": pack_id,
        "title": title,
        "status": "candidate",  # validated 금지(hosted candidate 전용)
        "promotion_allowed_default": False,
        "counts": {"nodes": len(nodes), "edges": len(edges), "evidence": len(ev_index)},
    }
    pack = {"manifest": manifest, "nodes": nodes, "edges": edges,
            "evidence_index": ev_index, "evidence_chunk": ev_chunk}
    return {"status": "OK", "stats": stats, "skipped": skipped,
            "built": {"nodes": len(nodes), "edges": len(edges), "evidence": len(ev_index)},
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


def write_packs(res, out_path=DATA_PATH):
    """build_packs() 결과를 packs.json 으로 실제 write — validate_packs_obj GO(위반 0)일 때만.

    미스매치 #2 정식 해결: 수동 inline write 를 빌더 안으로. data/ write 만 — KV/Cloud/배포 0.
    반환: {"written": path, "violations": []} 또는 {"blocked": reason}.
    """
    if res.get("status") != "OK":
        return {"blocked": res.get("reason", "BUILD_NOT_OK")}
    viol = validate_packs_obj(res)
    if viol:
        return {"blocked": "LOAD_PACKS_VIOLATION", "violations": viol}
    payload = {"packs": res["packs"]}
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return {"written": out_path, "violations": []}


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
        CREATE TABLE nodes(node_id TEXT, node_type TEXT, sentence TEXT, candidate INT, state TEXT, content_hash TEXT, semantic_subtype TEXT, created_at TEXT, use_count INTEGER DEFAULT 0);
        CREATE TABLE evidence(evidence_id TEXT, sentence TEXT, source_pointer_id TEXT, source_hash TEXT);
        CREATE TABLE edges(edge_id TEXT, relation TEXT, source TEXT, target TEXT, candidate INT, state TEXT, evidence_refs TEXT);
    """)
    # 도장 5종 세분화: node_type = 저장 도장 EN 라벨(judgment/state/...) — realpack label_kind = 이 값.
    # semantic_subtype = 보조 메타(있으면 properties 전파, None 이면 None — canonical 도장과 별개 축).
    # created_at/use_count = P1 랭킹 축. aaaa=오래됐지만 자주 씀 / bbbb=최신이지만 안 씀.
    conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?)",
                 ("node:CONV:aaaaaaaa", "judgment", "확정 판단 가나다", 0, "active", "h1", "결정",
                  "2026-01-01T00:00:00Z", 15))
    conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?)",
                 ("node:CONV:bbbbbbbb", "state", "확정 상태 라마바", 0, "active", "h2", None,
                  "2026-06-17T00:00:00Z", 0))
    conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?)",
                 ("node:CONV:cccccccc", "concept", "미확정 후보 사아자", 1, None, "h3", "교훈",
                  "2026-06-17T00:00:00Z", 0))  # candidate
    conn.execute("INSERT INTO evidence VALUES(?,?,?,?)",
                 ("EVC-CONV-aaaaaaaa", "확정 판단 가나다", "conv-self:aaaaaaaa", "aaaaaaaa"))
    conn.execute("INSERT INTO evidence VALUES(?,?,?,?)",
                 ("EVC-CONV-bbbbbbbb", "확정 판단 라마바", "conv-self:bbbbbbbb", "bbbbbbbb"))
    # active edge(src·tgt 둘 다 pack 노드) + dangling edge(tgt 미존재 → skip) + candidate edge(제외)
    conn.execute("INSERT INTO edges VALUES(?,?,?,?,?,?,?)",
                 ("EDG-sync-aaaa", "supports_judgment", "node:CONV:aaaaaaaa", "node:CONV:bbbbbbbb",
                  0, "active", '["node:CONV:aaaaaaaa"]'))
    conn.execute("INSERT INTO edges VALUES(?,?,?,?,?,?,?)",
                 ("EDG-dang", "supports_judgment", "node:CONV:aaaaaaaa", "node:CONV:zzzzzzzz",
                  0, "active", "[]"))
    conn.execute("INSERT INTO edges VALUES(?,?,?,?,?,?,?)",
                 ("EDG-cand", "supports_judgment", "node:CONV:aaaaaaaa", "node:CONV:bbbbbbbb",
                  1, "candidate", "[]"))
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
    # 도장 5종: label_kind = 저장 node_type 직사용(Claim 단일 아님) — 5종 EN 라벨로 직통.
    lk = {n["properties"]["label_kind"] for n in pack["nodes"]}
    chk("T6b label_kind = 저장 node_type 5종(Claim 아님)",
        lk == {"judgment", "state"} and lk <= {"doc", "evidence", "concept", "state", "judgment"})
    # 보조 semantic_subtype: 값 있으면 properties 전파, 없으면 None(canonical 도장과 별개 축).
    sub_by_id = {n["id"]: n["properties"].get("semantic_subtype") for n in pack["nodes"]}
    chk("T6c semantic_subtype 전파(값 있는 노드=결정, 없는 노드=None)",
        sub_by_id.get("node:CONV:aaaaaaaa") == "결정" and sub_by_id.get("node:CONV:bbbbbbbb") is None)
    chk("T7 load_packs 게이트 통과(위반 0)", validate_packs_obj(r) == [])
    chk("T8 format_version", pack["manifest"]["format_version"] == FORMAT_VERSION)
    # ── edge 빌드(연결도 KV pack 에 싣기) ──
    chk("T_e1 active edge 1건 pack 포함(dangling·candidate 제외)",
        r["built"].get("edges") == 1 and len(pack["edges"]) == 1)
    chk("T_e2 dangling edge 제외(src/tgt 노드 pack 존재만)",
        all(e["target"] in {n["id"] for n in pack["nodes"]} for e in pack["edges"]))
    chk("T_e3 edge candidate:true·promotion_allowed false",
        all(e["properties"]["candidate"] and e["promotion_allowed"] is False for e in pack["edges"]))
    chk("T_e4 edge evidence_refs = src 노드 ev(ev_index 정합)",
        pack["edges"][0]["evidence_refs"][0] == "EVC-CONV-aaaaaaaa")
    chk("T_e5 counts.edges 정합", pack["manifest"]["counts"]["edges"] == 1)
    chk("T_e6 edge 포함 load_packs 게이트 통과", validate_packs_obj(r) == [])
    chk("T9 status validated 아님", pack["manifest"]["status"] != "validated")

    # ── P1 ② 랭킹(3축 pre-compute + 정렬) ──
    props_by_id = {n["id"]: n["properties"] for n in pack["nodes"]}
    chk("T_r1 rank_score / created_at / use_count properties 전파",
        all("rank_score" in p and "created_at" in p and "use_count" in p for p in props_by_id.values()))
    chk("T_r1b created_at/use_count 값 정확(aaaa=오래·자주, bbbb=최신·안씀)",
        props_by_id["node:CONV:aaaaaaaa"]["use_count"] == 15
        and props_by_id["node:CONV:bbbbbbbb"]["created_at"] == "2026-06-17T00:00:00Z")
    # 기본 가중치(전부 1.0) 환경: aaaa(오래됐지만 use_count 15)가 utility 로 점수↑ → 앞 순위
    chk("T_r2 노드가 rank_score 내림차순 정렬",
        [n["properties"]["rank_score"] for n in pack["nodes"]]
        == sorted([n["properties"]["rank_score"] for n in pack["nodes"]], reverse=True))
    chk("T_r2b 자주 쓴 노드(aaaa, use_count15)가 안 쓴 노드(bbbb)보다 점수↑(기본 가중치)",
        props_by_id["node:CONV:aaaaaaaa"]["rank_score"] > props_by_id["node:CONV:bbbbbbbb"]["rank_score"])
    chk("T_r3 rank_score 추가 후에도 load_packs 게이트 통과(위반 0)", validate_packs_obj(r) == [])

    # 가중치 설정 override: utility 가중치 0 → use_count 무시, 신선도만 → bbbb(최신)가 앞
    import tempfile as _tf
    cfg_home = os.path.join(_tf.mkdtemp(prefix="realpack_cfg_"), ".binggupack")
    os.makedirs(cfg_home)
    import binggu_p1_config as _cfg
    _cfg.save_user_config({"ranking_weights": {"freshness": 1.0, "relevance": 0.0, "utility": 0.0}}, home=cfg_home)
    r_w = build_packs(lp, home=cfg_home)
    pw = r_w["packs"][0]
    chk("T_r4 가중치 override(utility 0·freshness만) → 최신 노드(bbbb)가 1순위",
        pw["nodes"][0]["id"] == "node:CONV:bbbbbbbb")

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

    # --write 경로(temp 전용 — 라이브 data/packs.json 미접촉)
    out = os.path.join(work, "packs.json")
    r_ok = build_packs(lp)
    w = write_packs(r_ok, out)
    chk("T12 write OK(위반 0)", w.get("violations") == [] and "written" in w)
    chk("T13 packs.json 실제 생성", os.path.exists(out))
    with open(out, encoding="utf-8") as f:
        wrote = json.load(f)
    chk("T14 write 산출 = packs 키만(데이터 구조 정합)",
        list(wrote.keys()) == ["packs"] and validate_packs_obj(wrote) == [])
    # BLOCK 결과는 write 안 함(fail-closed)
    out2 = os.path.join(work, "blocked.json")
    wb = write_packs(build_packs(lp2), out2)  # lp2 = active 0 → BLOCK
    chk("T15 BLOCK 결과는 write 안 함", "blocked" in wb and not os.path.exists(out2))
    # 라이브 경로 미접촉 확증: write_packs 기본 out_path 는 DATA_PATH 지만 selftest 는 temp 만 사용
    chk("T16 selftest 는 라이브 DATA_PATH 미접촉(temp 경로만)", os.path.abspath(out) != os.path.abspath(DATA_PATH))

    print("\nRESULT: %d/%d %s" % (ok, tot, "PASS" if ok == tot else "FAIL"))
    print("GATE: %s" % ("GO" if ok == tot else "BLOCK"))
    return ok == tot


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    res = build_packs()
    if "--write" in sys.argv:
        # 명시 write 모드: build → validate GO → data/packs.json 실제 write (gitignore 경로).
        #   KV/Cloud/배포 미접촉(파일 write 뿐). 라이브 반영은 owner 가 KV 적재 후 별도 deploy.
        if res["status"] != "OK":
            print(json.dumps(res, ensure_ascii=False, indent=2))
            sys.exit(1)
        w = write_packs(res)
        if "written" in w:
            print(json.dumps({"status": "WRITTEN", "path": w["written"],
                              "built": res["built"]}, ensure_ascii=False, indent=2))
            sys.exit(0)
        print(json.dumps({"status": "BLOCK", **w}, ensure_ascii=False, indent=2))
        sys.exit(1)
    # 기본(dry-run): 실 ledger 미리보기만 — data/ write 0, 배포 0.
    if res["status"] == "OK":
        res_view = {"status": res["status"], "stats": res["stats"], "built": res["built"],
                    "skipped": res["skipped"],
                    "preview_sentences": [n["properties"]["sentence"] for n in res["packs"][0]["nodes"]],
                    "load_packs_violations": validate_packs_obj(res)}
        print(json.dumps(res_view, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
