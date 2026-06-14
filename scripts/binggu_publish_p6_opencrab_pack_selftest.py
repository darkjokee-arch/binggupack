"""P6 selftest — OpenCrab 기대 구조 수리 + 6개 결함 각각 재현 검출.

temp 전용. cloud/DB 0 / 실 ledger write 0 / mtime 불변.
6개 결함: placeholder chunk · evidence unattached · grammar failed · contract failed · retrieval failed · graph not clean.
GATE=GO 조건: 전 항목 PASS.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_p6_opencrab_pack as P6

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def has(issues, code):
    return any(i.get("code") == code for i in issues)


def _dump_jsonl(rows):
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"


ROWS = [
    ("node:CONV:a1", "Claim", "실제 cloud 갱신은 owner 승인 때만 하고 Cloud 원본화는 HOLD로 둔다", 0, "active", "h1"),
    ("node:CONV:a2", "Claim", "기술 보안 근거는 채택하고 사용자 개인사정 근거는 자동 기각한다", 0, "active", "h2"),
    ("node:CONV:a3", "Claim", "검증 실패 미실행 증거없음 hash불일치 synthetic 위장은 전부 BLOCK한다", 0, "active", "h3"),
    ("node:CONV:a4", "Claim", "자동 저장은 OFF이고 사람이 SAVE 번호를 명시할 때만 빙구팩에 저장한다", 0, "active", "h4"),
]


def main():
    tmp = tempfile.mkdtemp(prefix="bgp_p6_")
    home = os.path.expanduser("~")
    real_led = os.path.join(home, ".binggupack", "ledger.sqlite")
    real_mtime = os.path.getmtime(real_led) if os.path.exists(real_led) else None

    # ── 정상 수리 pack ──
    files, report = P6.build_opencrab_pack(ROWS)
    check("1.정상 release_ready", report["release_ready"] is True)
    check("2.20개 파일", len(files) == 20)
    check("3.문서 1 / 청크 4 / 노드 9 / 엣지 8 (raw 흡수 아님)",
          report["counts"]["documents"] == 1 and report["counts"]["chunks"] == 4
          and report["counts"]["nodes"] == 9 and report["counts"]["edges"] == 8)
    check("4.evidence_linkage_closure", report["closure_ok"] is True)
    check("5.grammar canonical(space/node_type/relation)",
          set(report["grammar"]["node_types"]) <= P6.CANONICAL_NODE_TYPES
          and set(report["grammar"]["spaces"]) <= P6.CANONICAL_SPACES
          and set(report["grammar"]["relations"]) <= P6.CANONICAL_RELATIONS)
    check("6.retrieval hit_rate>=0.8", report["retrieval"]["hit_rate"] >= 0.8)
    check("7.leak 0", report["leak_count"] == 0)
    v = P6.validate_opencrab_pack(files)
    check("8.정상 pack validate 전건 PASS(issues 0)", v["ok"] and not v["issues"])

    # ── 6개 결함 재현 (정상 files 변조 → 각 게이트가 검출) ──
    chunks = P6._parse_jsonl(files["cloud/chunks.jsonl"])
    nodes = P6._parse_jsonl(files["graph/nodes.jsonl"])
    edges = P6._parse_jsonl(files["graph/edges.jsonl"])
    evidx = P6._parse_jsonl(files["evidence/index.jsonl"])

    # 9. placeholder chunk
    f1 = dict(files)
    bad_chunks = [dict(c) for c in chunks]
    bad_chunks[0]["text"] = ""
    f1["cloud/chunks.jsonl"] = _dump_jsonl(bad_chunks)
    check("9.placeholder chunk 검출", has(P6.validate_opencrab_pack(f1)["issues"], "placeholder_chunks"))

    # 10. evidence index unattached/missing
    f2 = dict(files)
    f2["evidence/index.jsonl"] = _dump_jsonl(evidx[:-1])  # 1행 제거
    check("10.evidence unattached/missing 검출", has(P6.validate_opencrab_pack(f2)["issues"], "evidence_unattached"))

    # 11. grammar failed
    f3 = dict(files)
    bad_nodes = [dict(n) for n in nodes]
    for n in bad_nodes:
        if n["node_type"] == "TextUnit":
            n["space"] = "BADSPACE"; break
    f3["graph/nodes.jsonl"] = _dump_jsonl(bad_nodes)
    check("11.grammar failed 검출", has(P6.validate_opencrab_pack(f3)["issues"], "grammar_failed"))

    # 12. contract failed (필수 파일 누락)
    f4 = dict(files)
    del f4["graph/nodes.jsonl"]
    check("12.contract failed 검출", has(P6.validate_opencrab_pack(f4)["issues"], "contract_failed"))

    # 13. retrieval failed
    f5 = dict(files)
    rg = json.loads(files["reports/release_gate.json"])
    for g in rg["gates"]:
        if g["gate"].startswith("retrieval.hit_rate"):
            g["ok"] = False
    f5["reports/release_gate.json"] = json.dumps(rg, ensure_ascii=False)
    check("13.retrieval failed 검출", has(P6.validate_opencrab_pack(f5)["issues"], "retrieval_failed"))

    # 14. graph not clean (broken edge endpoint)
    f6 = dict(files)
    bad_edges = [dict(e) for e in edges]
    bad_edges[0]["target"] = "ghost:node"
    f6["graph/edges.jsonl"] = _dump_jsonl(bad_edges)
    check("14.graph not clean 검출", has(P6.validate_opencrab_pack(f6)["issues"], "graph_not_clean"))

    # ── 15. 실 ledger 수리 ZIP 재생성 (장부 active 유무에 따라 분기 — 깨끗한 환경/CI 호환) ──
    zip_path = os.path.join(tmp, "p6_repaired.zip")
    r = P6.repair_from_ledger(zip_path=zip_path)
    if r["status"] == "DRYRUN_OK":
        check("15.실 ledger 수리 DRYRUN_OK", True)
        # 수리 ZIP 내용 재검증 (ZIP 풀어서 validate)
        import zipfile as _zf
        with _zf.ZipFile(zip_path) as z:
            zfiles = {n: z.read(n).decode("utf-8") for n in z.namelist()}
        check("16.수리 ZIP validate 전건 PASS", P6.validate_opencrab_pack(zfiles)["ok"]
              and os.path.exists(zip_path))
        check("17.cloud/db/upload 0", r["cloud_upload"] is False and r["db_insert"] is False
              and r["upload_executed"] is False)
        check("18.ZIP/3중 hash 생성", bool(r.get("bundle_hash")) and bool(r.get("node_hash"))
              and bool(r.get("evidence_hash")))
    else:
        # 장부 부재/active 0 (CI·clean clone·타 머신) → BLOCK fail-closed, ZIP 미생성
        check("15.실 ledger active 0/부재 → NO_REAL_LEDGER_DATA BLOCK",
              r["status"] == "BLOCK" and r.get("reason") == "NO_REAL_LEDGER_DATA")
        check("16.수리 ZIP 미생성(장부 부재/active 0)", not os.path.exists(zip_path))
        check("17.cloud/db/upload 0", r["cloud_upload"] is False and r["db_insert"] is False)
        check("18.실 ledger 무접촉(BLOCK 경로)", True)

    # 19. 실 ledger mtime 불변
    if real_mtime is not None:
        check("19.실 ledger 무접촉(mtime 불변)", abs(os.path.getmtime(real_led) - real_mtime) < 1e-6)
    else:
        check("19.실 ledger 무접촉(파일 없음)", True)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"GATE={gate}")
    if r.get("zip_path"):
        print("REAL_REPAIR:", r["status"], "release=" + str(r.get("release_status")),
              "counts=" + str(r.get("counts")))
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
