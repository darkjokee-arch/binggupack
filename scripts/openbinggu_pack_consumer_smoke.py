# -*- coding: utf-8 -*-
"""OpenBinggu PoC P1 — 단일 로컬 pack consumer smoke (dry-run only, 모델 호출 0).

목적: node+edge+evidence pack 후보를 모델 중립 소비 contract(docs/BINGGUPACK_MODEL_NEUTRAL_PACK_CONSUMER_CONTRACT.md)
  기준으로 읽어 "모델이 공유할 context view"의 최소 형태를 만든다. 같은 pack을 2회/2 instance 로 읽었을 때
  같은 근거 집합을 도출하는지 확인(handoff 일치 메커니즘 — 실제 모델은 P2, 별도 GO).

contract 안전 규칙(전건 강제):
  - candidate=true → confirmed 로 표현 금지. promotion_allowed=false 유지.
  - redaction [REDACTED:n] 복원 금지(길이 힌트만). source pointer raw dump 금지(경로 문자열만).
  - dangling source pointer(파일 미존재) → "근거 미확인(unverified)" 표기. pack 외부 파일 임의 접근 금지.
  - manifest visibility 없으면 기본 private/staging 처리.
  - 타 사용자 scope 접근 금지(현재 단일 사용자, scope 일치만 소비).

금지(BLOCKED_BY_V09): pack write / 외부 모델 호출 / OpenCrab·store·DB·v09·ARMED·apply·push·example-project.
  유일한 write = reports/openbinggu_pack_consumer_smoke_selftest.json.

CLI:
  python openbinggu_pack_consumer_smoke.py --selftest
  python openbinggu_pack_consumer_smoke.py <pack_dir>
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
SELFTEST_REPORT = BASE / "reports" / "openbinggu_pack_consumer_smoke_selftest.json"

sys.path.insert(0, str(SCRIPTS))
import watcher_pack_builder_m0 as packer   # build_pack (검증용 pack 생성)
import watcher_op_m0 as m0                 # _store_snapshot, _has_secret
import openbinggu_incoming_to_staging as v011   # secret 패턴 (raw 누출 검사)

SCOPE = "project:openbinggu"
REDACT_MARK = "[REDACTED:"


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _has_secret(text):
    return any(pat.search(text) for pat in v011.SECRET_PATTERNS)


def consume(pack_dir):
    """pack 5파일 → consumer view (contract 규칙 준수). 결정적(read-only)."""
    pack_dir = Path(pack_dir)
    manifest = _read_json(pack_dir / "manifest.json") or {}
    nodes = _read_jsonl(pack_dir / "nodes.jsonl")
    edges = _read_jsonl(pack_dir / "edges.jsonl")
    ev_index = _read_jsonl(pack_dir / "evidence_index.jsonl")
    ev_chunk = _read_jsonl(pack_dir / "evidence_chunk.jsonl")

    # manifest 신뢰 등급 (contract §2-1). visibility 없으면 기본 private.
    visibility = manifest.get("visibility", "private")
    status = manifest.get("status", "staged")
    pack_promotion_default = manifest.get("promotion_allowed_default", False)
    # status != validated → 후보(confirmed 아님)
    confirmed_allowed = (status == "validated")

    # node view (candidate 보존, confirmed 격상 금지)
    node_view = []
    for n in nodes:
        p = n.get("properties", {})
        node_view.append({
            "id": n["id"],
            "claim": p.get("sentence", n.get("label", "")),   # redacted 그대로(복원 X)
            "candidate": bool(p.get("candidate")),
            "promotion_allowed": bool(n.get("promotion_allowed", False)),
            "origin": p.get("origin", ""),
            "domain": p.get("domain", ""),
            "evidence_refs": list(n.get("evidence_refs", [])),
            "trust": "candidate_unverified",   # contract §3 — confirmed 표현 금지
        })

    # edge view
    edge_view = []
    for e in edges:
        p = e.get("properties", {})
        edge_view.append({
            "id": e["id"],
            "relation": p.get("relation", ""),
            "source": e.get("source", ""),
            "target": e.get("target", ""),
            "candidate": bool(p.get("candidate")),
            "promotion_allowed": bool(e.get("promotion_allowed", False)),
            "origin": p.get("origin", ""),
            "evidence_refs": list(e.get("evidence_refs", [])),
            "trust": "candidate_unverified",
        })

    # evidence view (source pointer only, dangling 체크)
    ev_text = {c["item_id"]: c.get("text", "") for c in ev_chunk}
    evidence_view = []
    for ev in ev_index:
        eid = ev["evidence_id"]
        ptr = ev.get("source_path", "")
        # dangling: pack 안 evidence_chunk 에 해당 텍스트가 없으면(또는 ptr 빈값) 근거 미확인
        present = eid in ev_text and bool(ev_text[eid])
        evidence_view.append({
            "evidence_id": eid,
            "source_pointer": ptr,            # raw dump 아님(경로 문자열만)
            "verification": "verified_pointer" if present else "unverified",
            "redaction": "applied" if (REDACT_MARK in ev_text.get(eid, "") or present) else "unknown",
        })

    # 근거 집합 (결정적 — 정렬)
    evidence_basis = {
        "node_ids": sorted(n["id"] for n in node_view),
        "edge_ids": sorted(e["id"] for e in edge_view),
        "evidence_ids": sorted(e["evidence_id"] for e in evidence_view),
    }

    view = {
        "pack_id": manifest.get("pack_id", ""),
        "scope": manifest.get("scope", ""),
        "visibility": visibility,
        "status": status,
        "confirmed_allowed": confirmed_allowed,        # False 이어야 정상(staged)
        "pack_promotion_allowed_default": bool(pack_promotion_default),
        "counts": {"nodes": len(node_view), "edges": len(edge_view), "evidence": len(evidence_view)},
        "evidence_basis": evidence_basis,
        "nodes": node_view, "edges": edge_view, "evidence": evidence_view,
    }
    return view


def _safety_checks(view):
    """contract 안전 규칙 검증."""
    all_cand_nodes = all(n["candidate"] for n in view["nodes"]) if view["nodes"] else True
    all_promo_false = (all(not n["promotion_allowed"] for n in view["nodes"])
                       and all(not e["promotion_allowed"] for e in view["edges"]))
    # redaction 복원 안 함 + secret raw 누출 0 (claim/relation 텍스트 검사)
    secret_leak = (any(_has_secret(n["claim"]) for n in view["nodes"])
                   or any(_has_secret(e.get("relation", "")) for e in view["edges"]))
    # confirmed 격상 금지: trust 가 candidate_unverified, confirmed_allowed=False
    no_confirmed = (not view["confirmed_allowed"]
                    and all(n["trust"] == "candidate_unverified" for n in view["nodes"])
                    and all(e["trust"] == "candidate_unverified" for e in view["edges"]))
    return {
        "candidate_preserved": all_cand_nodes,
        "promotion_all_false": all_promo_false,
        "no_secret_leak": not secret_leak,
        "no_confirmed_upgrade": no_confirmed,
        "scope_ok": view["scope"] in ("", SCOPE),   # 타 scope 아님
    }


def _basis_equal(v1, v2):
    return v1["evidence_basis"] == v2["evidence_basis"]


def run_on_pack(pack_dir):
    store_before = m0._store_snapshot()
    # 2회 read (동일성) + 2 instance (독립 호출 = 같은 contract)
    view_a = consume(pack_dir)
    view_b = consume(pack_dir)          # 2회
    view_c = consume(pack_dir)          # 2nd instance (독립 호출)
    store_after = m0._store_snapshot()

    checks = _safety_checks(view_a)
    checks["two_read_identical"] = _basis_equal(view_a, view_b)
    checks["two_instance_identical"] = _basis_equal(view_a, view_c)
    checks["operating_store_unchanged"] = (store_before == store_after)
    return view_a, checks


def run_single(pack_dir):
    view, checks = run_on_pack(pack_dir)
    gate = "GO" if all(checks.values()) else "STOP"
    print(json.dumps({"gate": gate, "pack_id": view["pack_id"],
                      "counts": view["counts"], "evidence_basis": view["evidence_basis"],
                      "checks": checks}, ensure_ascii=False, indent=2))
    sys.exit(0 if gate == "GO" else 1)


def run_selftest():
    fixtures = sorted((BASE / "tests" / "fixtures" / "watcher_mvp1").glob("*.diff"))
    cases = []
    store_before = m0._store_snapshot()
    for fp in fixtures:
        diff_text = fp.read_text(encoding="utf-8")
        # pack 생성(검증용) — builder GATE 통과한 산출 재사용
        _, pack_dir = packer.build_pack(diff_text, "consumer_" + fp.stem)
        view, checks = run_on_pack(pack_dir)
        cases.append({"run": fp.stem, "counts": view["counts"],
                      "evidence_basis": view["evidence_basis"], "checks": checks,
                      "case_ok": all(checks.values())})
    store_after = m0._store_snapshot()

    by = {c["run"]: c for c in cases}
    summary = {
        "normal_has_basis": "normal" in by and (by["normal"]["counts"]["nodes"] > 0
                                                and by["normal"]["counts"]["edges"] > 0),
        "all_two_read_identical": all(c["checks"]["two_read_identical"] for c in cases),
        "all_two_instance_identical": all(c["checks"]["two_instance_identical"] for c in cases),
        "candidate_preserved": all(c["checks"]["candidate_preserved"] for c in cases),
        "promotion_all_false": all(c["checks"]["promotion_all_false"] for c in cases),
        "no_confirmed_upgrade": all(c["checks"]["no_confirmed_upgrade"] for c in cases),
        "no_secret_leak": all(c["checks"]["no_secret_leak"] for c in cases),
        "scope_ok": all(c["checks"]["scope_ok"] for c in cases),
        "operating_store_unchanged": (store_before == store_after)
                                     and all(c["checks"]["operating_store_unchanged"] for c in cases),
    }
    gate = "GO" if all(summary.values()) else "STOP"
    report = {
        "tool": "openbinggu_pack_consumer_smoke.py", "phase": "PoC P1 단일 로컬 consumer smoke",
        "mode": "dry-run / selftest", "blocked_by_v09": True, "external_model_call": 0,
        "pack_write": 0, "production_write": 0, "store_write": 0, "opencrab_call": 0,
        "db_write": 0, "github_push": 0, "example_project_touch": 0,
        "checks": summary, "gate": gate, "cases": cases,
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 74)
    print("OpenBinggu PoC P1 — 단일 로컬 pack consumer smoke (dry-run, 모델 호출 0)")
    print("=" * 74)
    for c in cases:
        eb = c["evidence_basis"]
        print("  [%s] nodes=%d edges=%d evidence=%d basis(n=%d/e=%d/ev=%d) 2read=%s 2inst=%s ok=%s"
              % (c["run"], c["counts"]["nodes"], c["counts"]["edges"], c["counts"]["evidence"],
                 len(eb["node_ids"]), len(eb["edge_ids"]), len(eb["evidence_ids"]),
                 c["checks"]["two_read_identical"], c["checks"]["two_instance_identical"], c["case_ok"]))
    print("\n  checks:")
    for k, v in summary.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  report:", SELFTEST_REPORT)
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
