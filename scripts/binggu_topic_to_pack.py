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
import binggu_discover as DISC               # noqa: E402
import binggu_harvest as HV                  # noqa: E402
import binggu_pack_factory as FAC            # noqa: E402
import binggu_subtopic_decompose as SUB      # noqa: E402  (항목 A — 주제 세분화·네트워크0 import)
import binggu_pack_edges as EDG              # noqa: E402  (항목 C — pack 간 edges·순수함수)
# 항목 D(binggu_cloud_ingest_wire)는 클라우드 경로라 lazy import(기존 BPL/WR 스타일).


def topic_to_pack(topic, provider=None, fetch_runner=None, home=None, out_dir=None,
                  min_score=0.0, max_sources=10, opencrab_export=False,
                  recommend_workflow=False, execute=False, confirm=False, staging_home=None,
                  subtopics=False, max_subtopics=8, subtopic_use_search=False, llm_runner=None,
                  subtopic_transport=None,
                  pack_edges=False, peer_packs=None,
                  sync_workflow=False, workflow_transport=None, workflow_action="create",
                  workflow_name=None, workflow_id=None,
                  cloud_ingest=False, cloud_env=None, cloud_transport=None,
                  create_workflow=False, workflow_spec=None):
    """주제 → pack → (opencrab export) → (workflow 추천) 전체 파이프라인.

    OpenCrab import 2모드(제품 설계):
      - dry-run(기본): import 가능성 자동 검증(read-only, 저장소 미변경)
      - real import(execute=True AND confirm=True): 사용자 명시 확정 → 실제 staging 저장소 write(commit).
        execute=True 라도 confirm 없으면 dry-run 유지(실수 방지). real 전 pack_validate+PII gate+importable 강제.

    배선 옵션(전부 기본 OFF — 기존 단일 파이프라인 동작 불변):
      - subtopics(A): True 면 binggu_subtopic_decompose 로 주제 세분화 → 각 subtopic.query 로 discover
        그물을 확장(B 의 provider 그대로 사용)해 후보를 더 정밀하게 모은다. 누적 후보로 승급.
        subtopic_transport(A2): 저수준 LLM 콜백 transport(payload)->response 주입(기본 None=룰기반).
        미주입이면 템플릿/검색 빈출어 룰기반만(기존 동작 100% 불변). 실 네트워크는 transport 책임.
      - pack_edges(C): True 면 빌드된 pack(+peer_packs)으로 binggu_pack_edges.infer_edges 호출 →
        pack-간 workflow edges 추론 결과를 반환에 첨부(노드 엣지 불변·별개 차원).
        sync_workflow(C2): True 면 추론 edges 를 binggu_pack_edges.sync_edges_to_workflow 로
        OpenCrab workflow_manage 에 동기화. 기본 dry-run(payload 계획만·네트워크0). live 는
        execute&confirm + env BINGGU_WORKFLOW_SYNC=1 + workflow_transport 주입 + cloud url 4중 충족시만.
      - cloud_ingest(D): True 면 binggu_cloud_ingest_wire.ingest_pack 호출. 기본 dry-run(네트워크0).
        confirm tri-state 를 그대로 전달(D2). live 는 execute&confirm + env BINGGU_CLOUD_INGEST=1
        + transport 주입(owner GO) 충족시에만.
    반환 dict(status/discovered/promoted/harvested/skipped/verdict/pack/opencrab_import/workflow
              /pack_edges/workflow_sync/cloud_ingest)."""
    home = home or HV._home()
    sp = HV.sources_path(home)
    dp = DISC.discover_path(home)

    # ① 발견 — provider 검색 → vet → 랭킹 → discover_candidates.json
    #   (A) subtopics=True 면 주제를 세분화해 [원주제]+[subtopic.query…] 그물로 확장(B provider 재사용).
    #       discover(merge=True)가 동일 discover_path 에 누적 → 누적 후보 전체를 승급 대상으로 로드.
    if subtopics:
        subs = SUB.decompose(topic, provider=provider, max_subtopics=max_subtopics,
                             use_search=subtopic_use_search, llm_runner=llm_runner,
                             transport=subtopic_transport)   # A2 — 저수준 LLM 위임(기본 None=룰기반)
        queries, seen_q = [topic], {topic}
        for s in subs:
            q = s.get("query")
            if q and q not in seen_q:
                seen_q.add(q)
                queries.append(q)
        for q in queries:
            DISC.discover(q, provider=provider, home=home)   # merge=True 누적(persist)
        agg = sorted(DISC.load_discoveries(dp), key=lambda c: c.get("score", 0), reverse=True)
        disc = {"status": "OK", "topic": topic, "candidates": agg, "n_found": len(agg),
                "subtopics": subs}
    else:
        disc = DISC.discover(topic, provider=provider, home=home)

    # ② 승급 — min_score 이상 후보를 harvest 화이트리스트로(add_source 게이트 통과분만)
    promoted = []
    promoted_sids = set()   # 이번 run 승급 source_id — ③수확을 이 topic 으로 격리(cross-topic 오염 차단)
    for c in disc["candidates"]:
        if c.get("score", 0) < min_score:
            continue
        pr = DISC.promote_discovery(c["source_id"], sources_path_=sp, discover_path_=dp)
        if pr.get("status") == "OK":
            promoted.append(pr["promoted_url"])
            promoted_sids.add(c["source_id"])
        if len(promoted) >= max_sources:
            break

    # ③ 수확 — 이번 run 에 승급한 소스만(누적 화이트리스트 전체가 아니라 현재 topic 만).
    #   sp 에 다른 topic 의 기존 등록 소스가 있어도 이 pack 에 섞이지 않음(topic 격리·오염 차단).
    #   candidate.source_id 와 add_source 의 source_id 는 둘 다 source_id_for(norm_url) → 매칭 보장.
    documents, skipped = [], []
    for s in HV.load_sources(sp):
        if s.get("source_id") not in promoted_sids:
            continue
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

    # ⑧ pack 간 edges(C) — 빌드된 pack(+peer_packs)으로 pack-level workflow edges 추론(결정적·preview).
    #    단일 pack 만이면 self 금지로 edges=0(무해). peer_packs 주면 관계 추론(related/adjacent/…).
    pe = None
    metas = None
    if pack_edges and res["status"] == "OK":
        metas = ([res["pack"]] if res.get("pack") else []) + list(peer_packs or [])
        pe = EDG.infer_edges(metas)

    # ⑧' pack-간 edges → OpenCrab workflow_manage 동기화(C2) — 기본 dry-run(payload 계획만·네트워크0).
    #     EDG 내부 4중 게이트(dry_run=False + BINGGU_WORKFLOW_SYNC=1 + transport + cloud url)로 live 차단.
    ws = None
    if sync_workflow and pe is not None and pe.get("status") == "OK":
        wdry = not (execute and confirm)        # cloud ingest 와 동일 토글 — 미확정이면 dry-run
        ws = EDG.sync_edges_to_workflow(pe, pack_metas=metas, transport=workflow_transport,
                                        env=cloud_env, home=home, dry_run=wdry,
                                        action=workflow_action, workflow_name=workflow_name,
                                        workflow_id=workflow_id)

    # ⑨ 클라우드 ingest 배선(D) — 기본 dry-run(계획만·네트워크0). live 는 owner 3중 게이트.
    ci = None
    if cloud_ingest and res["status"] == "OK":
        import binggu_cloud_ingest_wire as CW
        dry = not (execute and confirm)        # real import 확정과 동일 토글 — 미확정이면 dry-run
        ci = CW.ingest_pack(documents, transport=cloud_transport, env=cloud_env, home=home,
                            dry_run=dry, confirm=confirm,   # D2 — confirm tri-state 명시 전달
                            create_workflow=create_workflow, workflow_spec=workflow_spec)

    return {"status": res["status"], "topic": topic,
            "discovered": disc["n_found"], "promoted": len(promoted),
            "harvested_docs": len(documents), "skipped": skipped,
            "verdict": res["verdict"]["verdict"], "counts": res["counts"],
            "pack": res["pack"], "written": res.get("written"),
            "opencrab_import": oc, "workflow": wf,
            "pack_edges": pe, "workflow_sync": ws, "cloud_ingest": ci}


def decompose_to_packs(topic, provider=None, fetch_runner=None, home=None, out_dir=None,
                       max_subtopics=6, subtopic_use_search=False, llm_runner=None,
                       subtopic_transport=None,
                       min_score=0.0, max_sources=10, infer_pack_edges=True,
                       topic_jaccard_min=0.34, cloud_ingest=False, cloud_env=None,
                       cloud_transport=None, execute=False, confirm=False):
    """A→B→수집/정제→C(팩 간 edges)→D(클라우드 dry-run) 전체 비전 오케스트레이션.

    주제 → 세분화(A·binggu_subtopic_decompose) → 각 subtopic 별 discover/harvest/pack(B+수집·
    topic_to_pack 재사용) → pack 간 edges(C·binggu_pack_edges) → (opt) 클라우드 ingest 계획
    (D·기본 dry-run·네트워크0). 각 subtopic 은 격리 sub-home 으로 discover/harvest 상태 분리
    (운영 ~/.binggupack 미접촉 — 호출자가 temp home 주면 전부 temp 하위).

    반환 {status, topic, n_subtopics, subpacks:[…요약], pack_edges, cloud_ingest}.
    """
    base_home = home or HV._home()

    # A — 주제 세분화(무손실·결정적). 빈 결과면 원주제 1건으로 폴백.
    subs = SUB.decompose(topic, provider=provider, max_subtopics=max_subtopics,
                         use_search=subtopic_use_search, llm_runner=llm_runner,
                         transport=subtopic_transport)   # A2 — 저수준 LLM 위임(기본 None=룰기반)
    if not subs:
        subs = [{"subtopic": topic, "query": topic, "rationale": "원주제(세분화 폴백)"}]

    sub_summaries, packs = [], []
    for i, s in enumerate(subs):
        # 격리 sub-home — subtopic 마다 discover/harvest 상태 분리(pack 들이 서로 다른 소스셋 보존)
        sub_home = os.path.join(base_home, "subtopics", "%02d_%s" % (i, FAC._slug(s["subtopic"])))
        os.makedirs(sub_home, exist_ok=True)
        sub_out = os.path.join(out_dir, "sub_%02d" % i) if out_dir else None
        r = topic_to_pack(s["query"], provider=provider, fetch_runner=fetch_runner,
                          home=sub_home, out_dir=sub_out, min_score=min_score,
                          max_sources=max_sources)
        if r.get("pack"):
            packs.append(r["pack"])
        sub_summaries.append({"subtopic": s["subtopic"], "query": s["query"],
                              "status": r["status"], "discovered": r["discovered"],
                              "harvested_docs": r["harvested_docs"],
                              "verdict": r["verdict"], "pack_id": (r.get("pack") or {})
                              .get("manifest", {}).get("pack_id"),
                              "written": r.get("written")})

    # C — pack 간 edges(여러 세분화 pack 의 메타 신호 → pack-level workflow edges)
    pe = EDG.infer_edges(packs, topic_jaccard_min=topic_jaccard_min) if infer_pack_edges else None

    # D — 클라우드 ingest 계획(기본 dry-run·네트워크0). pack 별 ingest 계획 수집.
    ci = None
    if cloud_ingest:
        import binggu_cloud_ingest_wire as CW
        dry = not (execute and confirm)
        ci = []
        for pk in packs:
            ci.append(CW.ingest_pack(pk, transport=cloud_transport, env=cloud_env,
                                     home=base_home, dry_run=dry, confirm=confirm))

    ok_any = any(x["status"] == "OK" for x in sub_summaries)
    return {"status": "OK" if ok_any else "EMPTY", "topic": topic,
            "n_subtopics": len(subs), "n_packs": len(packs),
            "subpacks": sub_summaries, "pack_edges": pe, "cloud_ingest": ci}


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

    # E12 — cross-topic 오염 차단: sp 에 다른 topic 소스를 선등록(운영 home 누적 시뮬)한 뒤 새 topic 실행 →
    #   기존 소스는 이번 run 승급분이 아니므로 수확 제외(수리 전이면 3건, 수리 후 2건).
    home_iso = os.path.join(tempfile.mkdtemp(prefix="t2p_iso_"), ".binggupack")
    os.makedirs(home_iso)
    HV.add_source("url", "https://other.example.com/old-topic.html",
                  keyword="다른주제", path=HV.sources_path(home_iso))
    out_iso = tempfile.mkdtemp(prefix="t2p_iso_pack_")
    r_iso = topic_to_pack("입찰 가격 예측", provider=provider, fetch_runner=fetch_runner,
                          home=home_iso, out_dir=out_iso)
    chk("E12 cross-topic 오염 차단(기존 sp 소스 미수확)",
        r_iso["harvested_docs"] == 2 and r_iso["promoted"] == 3)

    # ── F. 배선 통합(A/B/C/D) — 전부 mock·temp·네트워크0 ─────────────────
    # F1 (A+B) subtopics=True → 세분화 query 그물 확장(누적 후보 ≥ 단일 발견). 기존 단일경로 불변 검증은 E2.
    home_a = os.path.join(tempfile.mkdtemp(prefix="t2p_a_"), ".binggupack")
    os.makedirs(home_a)
    out_a = tempfile.mkdtemp(prefix="t2p_a_pack_")
    ra = topic_to_pack("입찰 가격 예측", provider=provider, fetch_runner=fetch_runner,
                       home=home_a, out_dir=out_a, subtopics=True, max_subtopics=4)
    chk("F1 (A) subtopics 세분화 후 status OK", ra["status"] == "OK")
    chk("F1b (A) 세분화 그물 후보 누적(>=단일 3)", ra["discovered"] >= 3 and ra["counts"]["nodes"] > 0)

    # F2 (D) cloud_ingest dry-run → 계획만(네트워크0·transport 미주입이어도 mode=dry-run)
    home_d = os.path.join(tempfile.mkdtemp(prefix="t2p_d_"), ".binggupack")
    os.makedirs(home_d)
    out_d = tempfile.mkdtemp(prefix="t2p_d_pack_")
    rd = topic_to_pack("입찰 가격 예측", provider=provider, fetch_runner=fetch_runner,
                       home=home_d, out_dir=out_d, cloud_ingest=True, cloud_env={})
    chk("F2 (D) cloud_ingest dry-run 계획·네트워크0",
        rd["cloud_ingest"] is not None and rd["cloud_ingest"]["mode"] == "dry-run")

    # F3 (C) pack_edges — 단일 pack + peer_pack 주입 → related/adjacent 추론
    home_c = os.path.join(tempfile.mkdtemp(prefix="t2p_c_"), ".binggupack")
    os.makedirs(home_c)
    rc = topic_to_pack("입찰 가격 예측", provider=provider, fetch_runner=fetch_runner,
                       home=home_c, pack_edges=True,
                       peer_packs=[{"manifest": {"pack_id": "topic/peer",
                                                  "topic": "입찰 가격 예측 분석"}}])
    chk("F3 (C) pack_edges 추론 status OK·peer 관계 검출",
        rc["pack_edges"] is not None and rc["pack_edges"]["status"] == "OK"
        and rc["pack_edges"]["counts"]["edges"] >= 1)

    # F4 (A→B→수집→C→D) decompose_to_packs 전체 비전 — 다중 pack + pack-간 edges + cloud dry-run
    home_f = os.path.join(tempfile.mkdtemp(prefix="t2p_f_"), ".binggupack")
    os.makedirs(home_f)
    out_f = tempfile.mkdtemp(prefix="t2p_f_pack_")
    rf = decompose_to_packs("입찰 가격 예측", provider=provider, fetch_runner=fetch_runner,
                            home=home_f, out_dir=out_f, max_subtopics=3,
                            topic_jaccard_min=0.3,   # 세분화 pack 들은 공통 토큰만 공유(긴 query) → 임계 조정
                            cloud_ingest=True, cloud_env={})
    chk("F4 (full) decompose_to_packs 다중 pack 생성", rf["status"] == "OK" and rf["n_packs"] >= 2)
    chk("F4b (C) 세분화 pack 간 edges 추론(related 등 >=1)",
        rf["pack_edges"] is not None and rf["pack_edges"]["counts"]["edges"] >= 1)
    chk("F4c (D) pack 별 cloud ingest dry-run 계획",
        isinstance(rf["cloud_ingest"], list) and len(rf["cloud_ingest"]) == rf["n_packs"]
        and all(p["mode"] == "dry-run" for p in rf["cloud_ingest"]))
    chk("F4d (A) 세분화 결과 subpack 메타 보존",
        len(rf["subpacks"]) == rf["n_subtopics"] and rf["n_subtopics"] >= 2)

    # ── G. A2/C2/D2 신규 배선(저수준 transport·workflow 동기화·confirm tri-state) ──
    # G1 (A2) subtopic_transport 주입 → decompose 가 저수준 transport 호출(룰기반 그물 불변)
    a2_calls = {"n": 0}

    def _sub_t(payload):
        a2_calls["n"] += 1
        return [{"subtopic": "입찰 가격 예측 변동성", "rationale": "리스크",
                 "query": "입찰 가격 예측 변동성 분석"}]

    home_g1 = os.path.join(tempfile.mkdtemp(prefix="t2p_g1_"), ".binggupack")
    os.makedirs(home_g1)
    rg1 = topic_to_pack("입찰 가격 예측", provider=provider, fetch_runner=fetch_runner,
                        home=home_g1, subtopics=True, max_subtopics=6,
                        subtopic_transport=_sub_t)
    chk("G1 (A2) subtopic_transport 주입 → transport 호출·status OK",
        a2_calls["n"] >= 1 and rg1["status"] == "OK" and rg1["discovered"] >= 3)

    # G2 (C2) sync_workflow=True 기본 dry-run → workflow payload 계획만·transport 미호출(네트워크0)
    wf_calls = {"n": 0}

    def _wf_t(payload):
        wf_calls["n"] += 1
        return {"result": {"isError": False}}

    home_g2 = os.path.join(tempfile.mkdtemp(prefix="t2p_g2_"), ".binggupack")
    os.makedirs(home_g2)
    rg2 = topic_to_pack("입찰 가격 예측", provider=provider, fetch_runner=fetch_runner,
                        home=home_g2, pack_edges=True, sync_workflow=True,
                        workflow_transport=_wf_t,
                        peer_packs=[{"manifest": {"pack_id": "topic/peer",
                                                  "topic": "입찰 가격 예측 분석"}}])
    chk("G2 (C2) sync_workflow dry-run 계획·transport 미호출·payload 동봉(네트워크0)",
        rg2["workflow_sync"] is not None and rg2["workflow_sync"]["mode"] == "dry-run"
        and wf_calls["n"] == 0 and bool(rg2["workflow_sync"]["payload"]))

    # G2b (C2) sync_workflow 기본 OFF → workflow_sync None(기존 단일경로 불변)
    chk("G2b (C2) sync_workflow 미지정 → workflow_sync None", rc["workflow_sync"] is None)

    # G3 (D2) cloud_ingest confirm tri-state 배선 → 기본 dry-run·confirmed False·transport 미호출
    d2_calls = {"n": 0}

    def _ci_t(payload):
        d2_calls["n"] += 1
        return {"result": {"isError": False}}

    home_g3 = os.path.join(tempfile.mkdtemp(prefix="t2p_g3_"), ".binggupack")
    os.makedirs(home_g3)
    rg3 = topic_to_pack("입찰 가격 예측", provider=provider, fetch_runner=fetch_runner,
                        home=home_g3, cloud_ingest=True, cloud_env={}, cloud_transport=_ci_t)
    chk("G3 (D2) cloud_ingest confirm 배선 → dry-run·confirmed False·transport 미호출",
        rg3["cloud_ingest"]["mode"] == "dry-run"
        and rg3["cloud_ingest"]["confirmed"] is False and d2_calls["n"] == 0)

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
