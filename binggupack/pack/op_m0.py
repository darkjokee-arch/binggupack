# -*- coding: utf-8 -*-
"""OpenBinggu Watcher 운영모드 M0 — Step3 review-only 검증 transform (정본 impl · dry-run only).

v1.16 strangler Phase2: 순수 transform(_sha8/_has_secret/verify_step3_review_only/_per_run_gate)을
scripts/watcher_op_m0.py 에서 byte-identical 이관. scripts/watcher_op_m0.py 는 이 모듈을 re-export
하는 backward-compatible thin wrapper(__file__ 경로상수 + ONTOLOGY/OPERATING_STORES/_store_snapshot/
_write_jsonl/process_one/run_selftest/run_single/main/CLI 오케스트레이션 잔류)다.

capture(MVP1) → evidence → nodes(MVP2) 소비 후 Step3(match_policy) 를 read-only 호출해 watcher
노드의 auto_merge 자격이 review 로 강등됨을 검증한다. write/merge/apply 0. sibling 정본 패키지
재사용(capture_mvp1/candidate_mvp2/policy.match). transform 본문은 파일 I/O·__file__ 무관(실제
graph/store write 없음).
"""
import hashlib

from binggupack.pack import candidate_mvp2 as mvp2  # Step2 to_nodes 재사용
from binggupack.pack import capture_mvp1 as mvp1  # Step0+1 capture/to_evidence 재사용
from binggupack.policy import match as mp  # Step3 review-only 검증(read-only)

__all__ = ["_sha8", "_has_secret", "verify_step3_review_only", "_per_run_gate",
           "mvp1", "mvp2", "mp"]


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _has_secret(text):
    return any(pat.search(text) for pat in mvp1.v011.SECRET_PATTERNS)


def verify_step3_review_only(nodes):
    """Step3(match_policy) read-only 호출로 watcher 노드의 auto_merge 자격 박탈(review 강등) 확인.
       write/merge/apply 0. 반환: dict(검증 지표)."""
    norm = mp.normalize_nodes(nodes)
    # (a) 실제 capture 노드 페어와이즈: auto_merge 후보 0 이어야 함.
    buckets, fuzzy, cda = mp.evaluate(norm)
    s = mp.summarize(buckets, fuzzy, cda)
    # (b) 합성 duplicate(동일 sentence watcher 노드 2개) → wrapper 강등 작동 직접 증명.
    synth_auto, synth_review, synth_tested = None, None, False
    if norm:
        a = dict(norm[0])
        b = dict(a)
        b["id"] = a["id"] + ":synthdup"
        b["evidence_refs"] = set(a["evidence_refs"])  # set 공유 회피
        bs, bf, bc = mp.evaluate([a, b])
        ss = mp.summarize(bs, bf, bc)
        synth_auto = ss["auto_merge_allowed_count"]
        synth_review = ss["localbinggu_review_candidate_count"]
        synth_tested = True
    return {
        "capture_auto_merge_allowed": s["auto_merge_allowed_count"],
        "capture_cross_domain_auto_merge": s["cross_domain_auto_merge_count"],
        "synthetic_dup_tested": synth_tested,
        "synthetic_dup_auto_merge": synth_auto,   # 0 기대 (watcher override 강등)
        "synthetic_dup_review_candidate": synth_review,  # >=1 기대
        "rapidfuzz_available": mp.RF,
    }


def _per_run_gate(report):
    """단일 run 안전 게이트."""
    s3 = report["step3_review_only"]
    checks = {
        "no_secret_residual": not report["any_secret_residual"],
        "candidate_all_true": report["candidate_all_true"],
        "promotion_all_false": report["promotion_all_false"],
        "origin_all_watcher": report["origin_all_watcher"],
        "domain_all_staging": report["domain_all_staging"],
        "no_edges": report["edges_generated"] == 0,
        "step3_capture_auto_merge_zero": s3["capture_auto_merge_allowed"] == 0,
        "step3_synthetic_dup_review_only": (
            (not s3["synthetic_dup_tested"])
            or (s3["synthetic_dup_auto_merge"] == 0 and s3["synthetic_dup_review_candidate"] >= 1)),
        "operating_store_unchanged": report["operating_store_unchanged"],
    }
    return checks
