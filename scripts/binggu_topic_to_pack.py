"""binggu_topic_to_pack — 빙구팩 2차 라인 통합 오케스트레이터(MVP end-to-end).

"주제 하나 → 자동 소스 발견 → (승인) → 크롤 → 전방위 파싱 → evidence chunk → 근거 기반 pack".
빠져 있던 비전 앞단을 기존 harvest/pack-validate 위에 최소침습으로 엮는다.

  discover(주제→후보)  → promote(화이트리스트 승급)  → harvest(크롤+파싱+후보화)
    → pack_factory(evidence→pack)  → validate_pack(완료 기준)

실 네트워크는 provider/fetch_runner 주입(owner 스케줄러). selftest 는 전부 mock — 실 네트워크 0.
운영 store/ledger 미접촉(temp home·out_dir 만). 검색 provider 는 교체 가능(중심은 수집→파싱→팩).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_discover as DISC       # noqa: E402
import binggu_harvest as HV          # noqa: E402
import binggu_pack_factory as FAC    # noqa: E402


def topic_to_pack(topic, provider=None, fetch_runner=None, home=None, out_dir=None,
                  min_score=0.0, max_sources=10, opencrab_export=False,
                  recommend_workflow=False, execute=False, confirm=False, staging_home=None):
    """주제 → pack → (opencrab export) → (workflow 추천) 전체 파이프라인.

    OpenCrab import 2모드(제품 설계):
      - dry-run(기본): import 가능성 자동 검증(read-only, 저장소 미변경)
      - real import(execute=True AND confirm=True): 사용자 명시 확정 → 실제 staging 저장소 write(commit).
        execute=True 라도 confirm 없으면 dry-run 유지(실수 방지). real 전 pack_validate+PII gate+importable 강제.
    반환 dict(status/discovered/promoted/harvested/skipped/verdict/pack/opencrab_import/workflow)."""
    home = home or HV._home()
    sp = HV.sources_path(home)
    dp = DISC.discover_path(home)

    # ① 발견 — provider 검색 → vet → 랭킹 → discover_candidates.json
    disc = DISC.discover(topic, provider=provider, home=home)

    # ② 승급 — min_score 이상 후보를 harvest 화이트리스트로(add_source 게이트 통과분만)
    promoted = []
    for c in disc["candidates"]:
        if c.get("score", 0) < min_score:
            continue
        pr = DISC.promote_discovery(c["source_id"], sources_path_=sp, discover_path_=dp)
        if pr.get("status") == "OK":
            promoted.append(pr["promoted_url"])
        if len(promoted) >= max_sources:
            break

    # ③ 수확 — 등록 소스마다 크롤+파싱+후보화. 파싱 실패는 그 소스만 skip(조건3, 전체 안 죽음)
    documents, skipped = [], []
    for s in HV.load_sources(sp):
        one = HV.harvest_one(s, runner=fetch_runner, sources_path_=sp, home=home)
        if one["status"] == "OK":
            # 원본 evidence_chunks 사용 — evidence_meta(source/raw_pointer/raw_sha256/parser/derivative) 보존.
            documents.append({"nodes": one["nodes"], "evidence_index": one["evidence_index"],
                              "evidence_chunks": one.get("evidence_chunks", []),
                              "parse_artifacts": one.get("parse_artifacts", [])})
        else:
            # B4 — parse 실패는 parse_error.type, fetch 실패는 reason(FETCH_ERROR 등)로 typed 보장(null 0).
            etype = (one.get("parse_error") or {}).get("type") or one.get("reason") or one["status"]
            skipped.append({"source_id": s.get("source_id"), "status": one["status"],
                            "error": etype, "detail": one.get("detail")})

    # ④ 팩 생성 + ⑤ validate(완료 기준)
    res = FAC.build_pack(topic, documents, out_dir=out_dir)

    # ⑥ OpenCrab export/import — pack dir 이 곧 import artifact(opencrab-pack-v1). dry-run/real 분리.
    oc = None
    if opencrab_export and res["status"] == "OK" and res.get("written"):
        import openbinggu_batch_pack_loader as BPL
        try:
            loaded = BPL.load_batch_pack(res["written"])         # read-only 검증(import 가능성)
            residual = BPL.residual_scan(loaded)                  # import 전 PII/secret 잔존 게이트
            oc = {"mode": "real" if (execute and confirm) else "dry-run",
                  "importable": not residual, "pack_id": loaded["pack_id"],
                  "nodes": len(loaded["nodes"]), "evidence": len(loaded["evidence"]),
                  "residual_pii": residual, "artifact_dir": res["written"]}
            if residual:
                oc["reason"] = "RESIDUAL_PII_BLOCK"          # PII gate — real import 강제 차단
            elif execute and not confirm:
                # real import 의도지만 확정(--yes) 없음 → dry-run 유지(실수 방지). 저장소 미변경.
                oc["blocked"] = "real import 는 --yes(confirm) 명시 필요(실제 저장소 write)"
            elif execute and confirm:
                # real import — 사용자 명시 확정(--execute --yes). 여기 도달 = pack_validate PASS +
                # PII gate(residual 0) + importable 통과. apply_with_rollback 이 적재 전 snapshot 백업 후 commit.
                # staging_home 미지정 시 운영 경로 '<home>/oc_staging'(기본 ~/.binggupack/oc_staging).
                # home 인자를 따르므로 호출자가 home=temp 주면 격리 검증 가능. 명시 temp 는 staging_home 으로.
                shome = staging_home or os.path.join(home, "oc_staging")
                os.makedirs(shome, exist_ok=True)
                ap = BPL.apply_with_rollback(shome, "owner", loaded, keep=True)
                oc["apply"] = {"committed": bool(ap.get("applied")), "reason": ap.get("reason"),
                               "staging_home": shome}
            else:
                oc["next"] = "real import(실제 저장소 write)는 --execute --yes 로 사용자 최종 확정 시 수행"
            oc["import_cmd"] = ("load_batch_pack('%s') → residual_scan(PII gate) → "
                                "[dry-run 검증] / --execute 시 apply_with_rollback(keep=True) commit" % res["written"])
        except Exception as e:
            oc = {"mode": "dry-run", "importable": False, "error": str(e)[:200]}

    # ⑦ workflow 추천 — pack 내용 기반 spec(추천만, 실행 0)
    wf = None
    if recommend_workflow and res["status"] == "OK":
        import binggu_workflow_recommend as WR
        wf = WR.recommend(res["pack"])

    return {"status": res["status"], "topic": topic,
            "discovered": disc["n_found"], "promoted": len(promoted),
            "harvested_docs": len(documents), "skipped": skipped,
            "verdict": res["verdict"]["verdict"], "counts": res["counts"],
            "pack": res["pack"], "written": res.get("written"),
            "opencrab_import": oc, "workflow": wf}


# ── selftest (provider/fetch 전부 mock · 실 네트워크 0 · temp 만) ──────
def _selftest():
    import tempfile
    os.environ["BINGGU_PARSER_CLI_OFF"] = "1"   # selftest 결정성 — parser 실 CLI 0(plain 폴백)
    ok = []

    def chk(name, cond):
        ok.append(cond)
        print(("  PASS " if cond else "  FAIL ") + name)

    home = os.path.join(tempfile.mkdtemp(prefix="t2p_"), ".binggupack")
    os.makedirs(home)

    # mock 검색 provider — 3 소스(html 파싱 / txt 원문보존 / pdf 파싱실패)
    hits = [
        {"url": "https://arxiv.org/paper.html", "title": "입찰 가격 예측 그래프 모델",
         "snippet": "입찰 가격 예측 정확도 향상"},
        {"url": "https://data.go.kr/notice.txt", "title": "조달 공고 데이터",
         "snippet": "입찰 공고 원문"},
        {"url": "https://example.org/broken.pdf", "title": "보고서",
         "snippet": "입찰 분석 보고서"},
    ]
    provider = DISC._mock_provider(hits)

    # mock fetch — url 확장자별 raw 반환(실 네트워크 0)
    HTML = (b"<html><body><p>\xea\xb7\xb8\xeb\x9e\x98\xed\x94\x84 \xea\xb8\xb0\xeb\xb0\x98 "
            b"\xec\x9e\x85\xec\xb0\xb0 \xea\xb0\x80\xea\xb2\xa9 \xec\x98\x88\xec\xb8\xa1 "
            b"\xeb\xaa\xa8\xeb\x8d\xb8\xec\x9d\x84 \xec\xa0\x9c\xec\x95\x88\xed\x95\x9c\xeb\x8b\xa4."
            b"</p></body></html>")
    TXT = ("입찰 공고 원문 본문 첫 문단입니다.\n\n두 번째 문단도 보존되어야 한다.").encode("utf-8")
    PDF = b"%PDF-1.4 broken-not-real"

    def fetch_runner(url, timeout=30):
        if url.endswith(".html"):
            raw, ct = HTML, "text/html"
        elif url.endswith(".txt"):
            raw, ct = TXT, "text/plain"
        else:
            raw, ct = PDF, "application/pdf"
        return {"ok": True, "text": raw.decode("utf-8", "replace"),
                "url": url, "final_url": url, "raw_bytes": raw, "content_type": ct}

    out = tempfile.mkdtemp(prefix="t2p_pack_")
    r = topic_to_pack("입찰 가격 예측", provider=provider, fetch_runner=fetch_runner,
                      home=home, out_dir=out)

    chk("E1 전체 파이프라인 status OK", r["status"] == "OK")
    chk("E2 발견 3건", r["discovered"] == 3)
    chk("E3 승급 3건(화이트리스트)", r["promoted"] == 3)
    chk("E4 수확 문서 2건(html+txt, pdf는 skip)", r["harvested_docs"] == 2)
    chk("E5 pdf 파싱실패 격리(전체 안 죽음·조건3)",
        any(s["status"] == "PARSE_SKIP" for s in r["skipped"]))
    chk("E6 pack validate 완료기준 통과", r["verdict"] in ("PASS", "REVIEW_ONLY"))
    chk("E7 노드>0(근거 기반 팩)", r["counts"]["nodes"] > 0)
    chk("E8 전방위 파서 출처 기록", "markitdown" in r["counts"]["parsers"] or "plain" in str(r["counts"]["parsers"]) or r["counts"]["parsers"])
    chk("E9 pack 파일 기록됨", r["written"] and os.path.exists(os.path.join(out, "manifest.json")))
    # 기록 manifest 독립 재검증(pack_validate 직접 호출)
    import json
    import openbinggu_pack_validate as PV
    mani = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
    chk("E10 기록 manifest 독립 재검증 PASS", PV.validate_pack(mani)["verdict"] in ("PASS", "REVIEW_ONLY"))
    chk("E11 promotion_allowed_default=false(안전 불변)", mani["promotion_allowed_default"] is False)

    total, passed = len(ok), sum(ok)
    print("\n[counts] %s" % r["counts"])
    print("[skipped] %s" % r["skipped"])
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


def _main(argv):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="주제→발견→수확→파싱→pack→OpenCrab export→workflow 추천 (E2E)")
    ap.add_argument("--topic", help="수집 주제 (예: '입찰 가격 예측')")
    ap.add_argument("--out", default=None, help="pack 출력 디렉토리(없으면 미기록)")
    ap.add_argument("--max-sources", type=int, default=5, help="승급/수확할 최대 소스 수")
    ap.add_argument("--opencrab-export", action="store_true", help="OpenCrab import 가능성 검증/export")
    ap.add_argument("--recommend-workflow", action="store_true", help="pack 기반 workflow 추천")
    ap.add_argument("--execute", action="store_true",
                    help="real import 의도(실제 저장소 write). --yes 와 함께여야 실제 commit, 미지정 시 dry-run")
    ap.add_argument("--yes", action="store_true",
                    help="real import 최종 확정(--execute 와 함께·실제 저장소 write 동의)")
    ap.add_argument("--staging-home", default=None,
                    help="real import 대상 staging home(미지정 시 운영 경로 ~/.binggupack/oc_staging)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1
    if not args.topic:
        ap.error("--topic 필요 (또는 --selftest)")

    r = topic_to_pack(args.topic, out_dir=args.out, max_sources=args.max_sources,
                      opencrab_export=args.opencrab_export,
                      recommend_workflow=args.recommend_workflow, execute=args.execute,
                      confirm=args.yes, staging_home=args.staging_home)
    # 요약 출력(pack 본문 제외 — 큰 nodes 배열 생략)
    summary = {k: v for k, v in r.items() if k != "pack"}
    summary["pack_counts"] = r["counts"]
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if r["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
