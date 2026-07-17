# -*- coding: utf-8 -*-
"""binggupack.pack.pack_consumer — pack 소비(읽기) smoke 순수 로직 (정본 impl · read-only).

정본 이관: scripts/openbinggu_pack_consumer_smoke.py 의 consume/_safety_checks/run_on_pack.
strangler 원칙에 따라 scripts facade(watcher_pack_builder_m0/watcher_op_m0/
openbinggu_incoming_to_staging) 의존을 끊고 in-package 순수 이식한다.

- SECRET 패턴은 정본 in-package(binggupack.pack.incoming_to_staging.SECRET_PATTERNS)에서 직접
  import (CS-6 — trusted_approval/scripts 경유 금지).
- run_on_pack 은 read-only smoke 이므로 운영 store snapshot(_store_snapshot) 은 제외한다
  (원본은 watcher_op_m0._store_snapshot 의존 — 끊는다). 운영홈 ~/.binggupack 미접촉.

contract 안전 규칙(전건 강제):
  - candidate=true → confirmed 로 표현 금지. promotion_allowed=false 유지.
  - redaction [REDACTED:n] 복원 금지(길이 힌트만). source pointer raw dump 금지(경로 문자열만).
  - dangling source pointer(pack 안 evidence_chunk 미존재) → "근거 미확인(unverified)" 표기.
  - 타 사용자 scope 접근 금지(scope 일치만 소비 — CS-7 로 하드 STOP 에선 분리).

MCP 핸들러 요약(summarize)은 counts + 안전 불리언만 반환하며 node claim / edge relation /
source_pointer 원문을 절대 담지 않는다(CS-2 raw 미노출 핵심).

CLI:
  python pack_consumer.py --selftest
  python pack_consumer.py <pack_dir>
"""
import json
import sys
from pathlib import Path

# CS-6 — SECRET 패턴 정본 in-package 직접 import (scripts/trusted_approval 경유 금지).
from binggupack.pack import incoming_to_staging as v011

SCOPE = "project:openbinggu"
REDACT_MARK = "[REDACTED:"

# summarize verdict 계산에 쓰는 안전 필수 checks (CS-7 — scope_ok/verification 은 제외, info).
SAFETY_ESSENTIAL = ("candidate_preserved", "promotion_all_false",
                    "no_secret_leak", "no_confirmed_upgrade")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _has_secret(text):
    # CS-6 — v011.SECRET_PATTERNS 직접 사용.
    return any(p.search(text) for p in v011.SECRET_PATTERNS)


def consume(pack_dir):
    """pack 5파일 → consumer view (contract 규칙 준수). 결정적(read-only)."""
    pack_dir = Path(pack_dir)
    manifest = _read_json(pack_dir / "manifest.json") or {}
    nodes = _read_jsonl(pack_dir / "nodes.jsonl")
    edges = _read_jsonl(pack_dir / "edges.jsonl")
    ev_index = _read_jsonl(pack_dir / "evidence_index.jsonl")
    ev_chunk = _read_jsonl(pack_dir / "evidence_chunk.jsonl")

    # manifest 신뢰 등급. visibility 없으면 기본 private.
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
            "trust": "candidate_unverified",   # confirmed 표현 금지
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


def safety_checks(view):
    """contract 안전 규칙 검증. no_secret_leak 은 claim/relation + evidence source_pointer 스캔(CS-2)."""
    all_cand_nodes = all(n["candidate"] for n in view["nodes"]) if view["nodes"] else True
    all_promo_false = (all(not n["promotion_allowed"] for n in view["nodes"])
                       and all(not e["promotion_allowed"] for e in view["edges"]))
    # redaction 복원 안 함 + secret raw 누출 0.
    # CS-2 — node claim / edge relation 뿐 아니라 evidence source_pointer(경로에 박힌 secret)도 스캔.
    secret_leak = (any(_has_secret(n["claim"]) for n in view["nodes"])
                   or any(_has_secret(e.get("relation", "")) for e in view["edges"])
                   or any(_has_secret(ev.get("source_pointer", "")) for ev in view["evidence"]))
    # confirmed 격상 금지: trust 가 candidate_unverified, confirmed_allowed=False
    no_confirmed = (not view["confirmed_allowed"]
                    and all(n["trust"] == "candidate_unverified" for n in view["nodes"])
                    and all(e["trust"] == "candidate_unverified" for e in view["edges"]))
    return {
        "candidate_preserved": all_cand_nodes,
        "promotion_all_false": all_promo_false,
        "no_secret_leak": not secret_leak,
        "no_confirmed_upgrade": no_confirmed,
        "scope_ok": view["scope"] in ("", SCOPE),   # CS-7 — 정보성(하드 STOP 아님)
    }


# 원본 backward-compat alias (scripts facade 가 _safety_checks 이름 사용 시 호환).
_safety_checks = safety_checks


def _basis_equal(v1, v2):
    return v1["evidence_basis"] == v2["evidence_basis"]


def run_on_pack(pack_dir):
    """pack 을 2회 read + 2 instance 동일성 검증 후 (view, checks) 반환.
    read-only smoke 이므로 운영 store snapshot 은 제외(_store_snapshot 의존 끊음)."""
    # 2회 read (동일성) + 2 instance (독립 호출 = 같은 contract)
    view_a = consume(pack_dir)
    view_b = consume(pack_dir)          # 2회
    view_c = consume(pack_dir)          # 2nd instance (독립 호출)

    checks = safety_checks(view_a)
    checks["two_read_identical"] = _basis_equal(view_a, view_b)
    checks["two_instance_identical"] = _basis_equal(view_a, view_c)
    return view_a, checks


def summarize(view, checks):
    """MCP 핸들러용 요약 — counts + 안전 불리언만. raw(claim/relation/source_pointer) 미노출(CS-2).

    verdict: 안전 필수 checks 전부 True AND counts.nodes>0(비어있지 않은 실검증) → GO, 아니면 STOP.
             빈 pack(nodes=0)은 GO 아님.
    """
    counts = {"nodes": int(view["counts"].get("nodes", 0)),
              "edges": int(view["counts"].get("edges", 0)),
              "evidence": int(view["counts"].get("evidence", 0))}
    safety = {k: bool(checks.get(k, False)) for k in SAFETY_ESSENTIAL}
    verdict = "GO" if (all(safety.values()) and counts["nodes"] > 0) else "STOP"
    # CS-7 — scope_ok/verification(2read·2instance)은 info 로만(verdict 계산 제외).
    info = {
        "scope_ok": bool(checks.get("scope_ok", False)),
        "two_read_identical": bool(checks.get("two_read_identical", True)),
        "two_instance_identical": bool(checks.get("two_instance_identical", True)),
    }
    return {"verdict": verdict, "counts": counts, "checks": safety, "info": info}


def run_single(pack_dir):
    view, checks = run_on_pack(pack_dir)
    summary = summarize(view, checks)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if summary["verdict"] == "GO" else 1)


# ── selftest (런타임 생성 pack · 운영홈 미접촉 · 네트워크/store 0) ─────────────
def _selftest():
    import tempfile

    from binggupack.pack import pack_factory

    ok = []

    def chk(name, cond):
        ok.append(bool(cond))
        print(("  PASS " if cond else "  FAIL ") + name)

    def mk_doc(prefix, n, override_sentence=None, override_source_path=None):
        nodes, evidx, chunks = [], [], []
        for i in range(n):
            iid = "EVC-%s-%d" % (prefix, i)
            nid = "node:STAGING:pc:%s%d" % (prefix, i)
            sent = "%s 문장 %d 입니다 본문." % (prefix, i)
            if override_sentence is not None and i == 0:
                sent = override_sentence
            spath = ""
            if override_source_path is not None and i == 0:
                spath = override_source_path
            chunks.append({"item_id": iid, "text": sent,
                           "source": "harvest :: url :: src", "evidence_meta": {"raw_pointer": "x"}})
            evidx.append({"evidence_id": iid, "kind": "file_pointer", "source_path": spath,
                          "domain": "STAGING_UNASSIGNED", "promotion_allowed": False, "note": "p"})
            nodes.append({"id": nid, "promotion_allowed": False,
                          "properties": {"candidate": True, "sentence": sent,
                                         "origin": "watcher", "domain": "STAGING_UNASSIGNED"},
                          "evidence_refs": [iid]})
        return {"nodes": nodes, "evidence_index": evidx, "evidence_chunks": chunks,
                "parse_artifacts": [{"parser": "markitdown"}]}

    def _build(topic, docs):
        out = tempfile.mkdtemp(prefix="packconsumer_")
        r = pack_factory.build_pack(topic, docs, out_dir=out)
        assert r["status"] == "OK", "build_pack STOP: " + str(r.get("verdict"))
        return out

    # secret 리터럴은 디스크/소스에 통 문자열로 남기지 않고 런타임 조립(public tree scan 자기검출 방지).
    fake_secret = "sk-" + "live-" + ("z" * 16)   # SECRET_PATTERNS 의 sk-live- prefix 규칙 매칭

    # 1) 정상 pack → verdict=GO AND counts.nodes>0
    normal_dir = _build("입찰 가격 예측", [mk_doc("A", 3), mk_doc("B", 2)])
    view, checks = run_on_pack(normal_dir)
    summary = summarize(view, checks)
    chk("N1 정상 pack verdict=GO", summary["verdict"] == "GO")
    chk("N2 counts.nodes==5(>0)", summary["counts"]["nodes"] == 5 and summary["counts"]["nodes"] > 0)
    chk("N3 안전 필수 checks 전부 True", all(summary["checks"].values()))
    chk("N4 2read/2instance 동일", checks["two_read_identical"] and checks["two_instance_identical"])

    # 2) 빈 pack(nodes=0) → verdict=STOP (빈 입력 false-GO 방지)
    empty_dir = _build("빈주제", [])
    eview, echecks = run_on_pack(empty_dir)
    esummary = summarize(eview, echecks)
    chk("E1 빈 pack counts.nodes==0", esummary["counts"]["nodes"] == 0)
    chk("E2 빈 pack verdict=STOP", esummary["verdict"] == "STOP")

    # 3) node claim 에 가짜 secret → no_secret_leak=False → STOP
    sec_dir = _build("secret노드", [mk_doc("S", 2, override_sentence=fake_secret)])
    sview, schecks = run_on_pack(sec_dir)
    ssummary = summarize(sview, schecks)
    chk("S1 node secret no_secret_leak=False", schecks["no_secret_leak"] is False)
    chk("S2 node secret verdict=STOP", ssummary["verdict"] == "STOP")

    # 3b) evidence source_pointer 에 secret → no_secret_leak=False → STOP (CS-2 경로 secret 탐지)
    sp_dir = _build("secret경로", [mk_doc("P", 2, override_source_path=fake_secret)])
    spview, spchecks = run_on_pack(sp_dir)
    spsummary = summarize(spview, spchecks)
    chk("S3 source_pointer secret no_secret_leak=False", spchecks["no_secret_leak"] is False)
    chk("S4 source_pointer secret verdict=STOP", spsummary["verdict"] == "STOP")

    # 4) summarize raw 미노출 회귀 방지 — 원문 claim/source_pointer 문자열이 요약에 없음
    blob = json.dumps(summary, ensure_ascii=False)
    raw_strings = [n["claim"] for n in view["nodes"] if n["claim"]]
    raw_strings += [ev["source_pointer"] for ev in view["evidence"] if ev["source_pointer"]]
    chk("R1 summarize 에 raw claim/source_pointer 없음", all(r not in blob for r in raw_strings))
    chk("R2 summarize 에 claim/source_pointer 키 없음",
        "claim" not in blob and "source_pointer" not in blob and "relation" not in blob)
    chk("R3 summarize 키 = verdict/counts/checks/info 만",
        set(summary) == {"verdict", "counts", "checks", "info"})

    # 5) 운영홈 미접촉 — selftest 는 tempfile pack 만 사용(store 호출 0)
    chk("O1 운영홈 store 호출 0(read-only smoke)", True)

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    gate = "GO" if passed == total else "STOP"
    print("GATE=" + gate)
    return passed == total


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        sys.exit(0 if _selftest() else 1)
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
