# -*- coding: utf-8 -*-
"""OpenBinggu Watcher MVP2 — Step2 Candidate (evidence_chunk → incoming_nodes, dry-run only).

범위(MVP2 고정): evidence_chunk → incoming_nodes.jsonl + incoming_evidence_index.jsonl 만.
  - incoming_edges 생성 금지(MVP2.1 분리). Step3 merge preview(match_policy) 호출 금지.
  - 출력 = temp dir(BASE/tmp/watcher_mvp2/) only. 운영 store write 0.
  - MVP1(watcher_capture_mvp1) 재사용해 evidence_chunk 생성 → 노드 변환.

강제(전건): candidate=true / promotion_allowed=false / origin=watcher / domain=STAGING_UNASSIGNED /
  evidence_refs=[item_id] + incoming_evidence_index 동시생성(매칭) / 출력 키 whitelist(forced_domain·
  coverage·pattern_id·source_type 등 미생성).
STOP: secret 3차 잔존 / redacted-only·6자미만·공백없는 짧은 sentence / (구조상) D1~D9 domain·whitelist 외 키 0.

검증: v0.7 loader(localbinggu_incoming_loader) 7불변식 PASS + candidate 전건 true + promotion 전건 false
  + evidence_refs 전건 index 매칭 + secret raw 잔존 0 + empty→노드0 + 2회 byte 동일 + 운영 store write 0
  + Step3 호출 0(match_policy import 안 함).

CLI:
  python watcher_candidate_mvp2.py --selftest
  python watcher_candidate_mvp2.py <diff_text_file>
"""
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"   # MVP1 diff fixture 재사용
TMP_OUT = BASE / "tmp" / "watcher_mvp2"
SELFTEST_REPORT = BASE / "reports" / "watcher_mvp2_selftest.json"

sys.path.insert(0, str(SCRIPTS))
import watcher_capture_mvp1 as mvp1            # Step0+1 (capture/to_evidence) 재사용
import openbinggu_incoming_to_staging as v011  # secret 패턴 재사용 (_has_secret)
import localbinggu_incoming_loader as v07loader  # v0.7 7불변식 검증 (Step3 아님)
import openbinggu_label_kind_map as lkmap      # G0 — 5종 분류 + 한영 매핑 단일 정본
import openbinggu_a0_node_dryrun as a0         # G0 — 노드 헌법 shadow 판정 (기록만, stop은 기존 가드)
# 주의: localbinggu_match_policy(Step3) 는 import 하지 않는다.

DOMAIN = "STAGING_UNASSIGNED"
REDACT_RE = re.compile(r"\[REDACTED:\d+\]")

# G0 — 생성 주체 attribution (PROV). 멱등 유지를 위해 timestamp 미포함(deterministic 값만).
GENERATED_BY = {"extractor": "watcher_candidate_mvp2", "rule_version": "g0.1"}

# 출력 키 whitelist (이 외 키는 절대 생성 안 함)
NODE_KEYS = {"id", "space", "node_type", "label", "properties", "evidence_refs", "promotion_allowed"}
PROP_KEYS = {"label_kind", "sentence", "domain", "candidate", "evidence_status", "origin",
             "rule_id", "generated_by", "a0_verdict"}
EVIDX_KEYS = {"evidence_id", "kind", "source_path", "domain", "promotion_allowed", "note"}


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _has_secret(text):
    return any(pat.search(text) for pat in v011.SECRET_PATTERNS)


def _meaningful(sentence):
    """redacted-only / 6자미만 / 공백없는 짧은 문장 거부 (loader 휴리스틱 + redacted 제거)."""
    stripped = REDACT_RE.sub("", sentence).strip()
    if len(stripped) < 6:
        return False
    if " " not in stripped and len(stripped) < 12:
        return False
    return True


def to_nodes(chunks):
    """evidence_chunk[] → (nodes, evidence_index, stops). 노드만(엣지 미생성)."""
    nodes, ev_index, stops = [], [], []
    for c in chunks:
        item_id = c["item_id"]
        sent = c["text"]
        if not _meaningful(sent):
            stops.append({"item_id": item_id, "reason": "short/redacted-only sentence"})
            continue
        if _has_secret(sent):  # 3차 재검사
            stops.append({"item_id": item_id, "reason": "secret residual (3rd scan)"})
            continue
        # G0 — deterministic 5종 분류 (매칭 실패 = 판단 fallback, 현행 동일값)
        kind, rule_id = lkmap.classify_label_kind(sent)
        # node_type = OpenCrab space 노드타입(Document/Evidence/Concept/Claim) — v0.7 loader VALID_NTYPE 계약.
        #   conv 경로의 KO2EN 5종 도장(state/judgment)과 다른 스키마 층: 여기 node_type 은 OpenCrab 적재용이므로
        #   상태·판단→Claim 붕괴가 정상(loader 가 TitleCase 4종만 허용). 5종 도장은 A0 validator(아래 KO2EN) 전용.
        space, ntype = lkmap.KIND_TO_SPACE_NTYPE[kind]
        # G0 — A0 노드 헌법 shadow 판정 (기록만. 캡처 문장 품질 개선 전까지 stop 미적용)
        a0_res = a0.classify_node(
            {"id": "node:STAGING:wch:" + _sha8(item_id), "sentence": sent,
             "node_type": lkmap.KO2EN[kind], "evidence_refs": [item_id]},
            status="candidate")
        node = {
            "id": "node:STAGING:wch:" + _sha8(item_id),
            "space": space,
            "node_type": ntype,
            "label": sent,
            "properties": {
                "label_kind": kind,
                "sentence": sent,
                "domain": DOMAIN,
                "candidate": True,
                "evidence_status": "partial",
                "origin": "watcher",
                "rule_id": rule_id,
                "generated_by": dict(GENERATED_BY),
                "a0_verdict": a0_res["verdict"],
            },
            "evidence_refs": [item_id],
            "promotion_allowed": False,
        }
        ev = {
            "evidence_id": item_id,
            "kind": "file_pointer",
            "source_path": c.get("evidence_meta", {}).get("raw_pointer", ""),
            "domain": DOMAIN,
            "promotion_allowed": False,
            "note": "watcher capture pointer (원문 미복사)",
        }
        # whitelist 강제(이 외 키 차단)
        assert set(node) <= NODE_KEYS and set(node["properties"]) <= PROP_KEYS, "node key whitelist 위반"
        assert set(ev) <= EVIDX_KEYS, "evidence_index key whitelist 위반"
        nodes.append(node)
        ev_index.append(ev)
    return nodes, ev_index, stops


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")


def process_one(diff_text, name):
    events = mvp1.capture(diff_text, "git diff :: " + name)
    chunks, _ = mvp1.to_evidence(events)
    nodes, ev_index, stops = to_nodes(chunks)
    out_dir = TMP_OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "incoming_nodes.jsonl", nodes)
    _write_jsonl(out_dir / "incoming_evidence_index.jsonl", ev_index)
    # incoming_edges.jsonl 은 생성하지 않음(MVP2.1)
    # v0.7 loader 7불변식 검증 (Step3 아님, read-only)
    loader_res = v07loader.load_incoming(str(out_dir), known_evidence_ids=None)
    refs_all_matched = all(
        all(r in {e["evidence_id"] for e in ev_index} for r in n["evidence_refs"]) for n in nodes)
    return {
        "name": name, "n_chunks": len(chunks), "n_nodes": len(nodes), "n_stops": len(stops),
        "stops": stops,
        "candidate_all_true": all(n["properties"]["candidate"] is True for n in nodes),
        "promotion_all_false": all(n["promotion_allowed"] is False for n in nodes),
        "origin_all_watcher": all(n["properties"]["origin"] == "watcher" for n in nodes),
        "domain_all_staging": all(n["properties"]["domain"] == DOMAIN for n in nodes),
        "no_d_domain": all(not re.fullmatch(r"D[1-9]", n["properties"]["domain"]) for n in nodes),
        "evidence_refs_matched": refs_all_matched,
        "any_secret_residual": any(_has_secret(n["properties"]["sentence"]) for n in nodes),
        "loader_schema_valid": loader_res["schema_valid"],
        "loader_violations": loader_res["violations"],
        "loader_nodes_accepted": loader_res["counts"]["nodes_accepted"],
        "loader_edges_in": loader_res["counts"]["edges_in"],
        "out_dir": str(out_dir),
    }


def run_selftest():
    fixtures = sorted(FIXTURE_DIR.glob("*.diff"))
    cases = []
    for fp in fixtures:
        diff_text = fp.read_text(encoding="utf-8")
        r1 = process_one(diff_text, fp.stem)
        b1 = (TMP_OUT / fp.stem / "incoming_nodes.jsonl").read_bytes()
        r2 = process_one(diff_text, fp.stem)
        b2 = (TMP_OUT / fp.stem / "incoming_nodes.jsonl").read_bytes()
        r1["idempotent"] = (b1 == b2)
        cases.append(r1)

    checks = {
        "normal_has_nodes": any(c["name"] == "normal" and c["n_nodes"] > 0 for c in cases),
        "empty_zero_nodes": any(c["name"] == "empty" and c["n_nodes"] == 0 for c in cases),
        "candidate_all_true": all(c["candidate_all_true"] for c in cases),
        "promotion_all_false": all(c["promotion_all_false"] for c in cases),
        "origin_all_watcher": all(c["origin_all_watcher"] for c in cases),
        "domain_all_staging_no_d": all(c["domain_all_staging"] and c["no_d_domain"] for c in cases),
        "evidence_refs_matched": all(c["evidence_refs_matched"] for c in cases),
        "loader_7invariant_pass": all(c["loader_schema_valid"] for c in cases),
        "loader_edges_zero": all(c["loader_edges_in"] == 0 for c in cases),
        "no_secret_residual": all(not c["any_secret_residual"] for c in cases),
        "all_idempotent": all(c["idempotent"] for c in cases),
    }
    gate = "GO" if all(checks.values()) else "STOP"
    report = {
        "tool": "watcher_candidate_mvp2.py", "phase": "MVP2 Step2 candidate (nodes only)",
        "mode": "dry-run / selftest", "blocked_by_v09": True,
        "incoming_edges": "NOT_GENERATED (MVP2.1)", "step3_match_policy_called": False,
        "write_locations": [str(TMP_OUT), str(SELFTEST_REPORT)],
        "operating_store_write": 0, "production_write": 0, "merge_call": 0, "apply_call": 0,
        "opencrab_call": 0, "github_push": 0, "db_write": 0,
        "checks": checks, "gate": gate, "cases": cases,
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 70)
    print("OpenBinggu Watcher MVP2 — Step2 Candidate (nodes only, dry-run)")
    print("=" * 70)
    for c in cases:
        print("  [%s] chunks=%d nodes=%d stops=%d loader_valid=%s edges_in=%d idem=%s sec=%s"
              % (c["name"], c["n_chunks"], c["n_nodes"], c["n_stops"], c["loader_schema_valid"],
                 c["loader_edges_in"], c["idempotent"], c["any_secret_residual"]))
        for s in c["stops"]:
            print("        STOP:", s["reason"], "@", s["item_id"])
    print("\n  checks:")
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  temp out:", TMP_OUT, "\n  report  :", SELFTEST_REPORT)
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def run_single(path):
    diff_text = Path(path).read_text(encoding="utf-8")
    print(json.dumps(process_one(diff_text, Path(path).stem), ensure_ascii=False, indent=2))
    sys.exit(0)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
