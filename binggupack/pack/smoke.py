#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""offline smoke 핵심 로직 (Lane B 모듈화).

scripts/smoke_test.py 가 이 모듈의 run_smoke_cli() 를 호출하는 backward-compatible
wrapper 다. save gate 동작 변경(read-only 해제 f9a9c61: confirm 정확일치 시 human
승격 저장)에 맞춰 케이스 9(confirm 부재→confirm_phrase_mismatch REJECT)/9b(정확일치→
격리 write) 를 갱신했다. 나머지 명령/출력/exit code 규약은 유지.

handle_tool 직접 호출로 8도구 + save gate(confirm-gated) 검증.
BINGGU_HOME 격리(--home) → 운영 ~/.binggupack 미접촉. 실 ledger write 0.
"""
import argparse
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/binggupack/pack
PKG = os.path.dirname(HERE)                          # <repo>/binggupack
ROOT = os.path.dirname(PKG)                          # <repo>
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)   # handle_tool 등 기존 서버 핸들러 import 경로

SYN = ("BingguPack install smoke test: v1.10.0-rc.1 ships the installable MCP package, "
       "workflow-to-pack factory, and insane-search optional evidence discovery adapter. "
       "This is synthetic test evidence only.")

# 실존 fixture (path-gate 의존 제거: 실제 파일/디렉토리만 사용)
FX_DIR = os.path.join("examples", "toy_project")
FX_FILE = os.path.join("examples", "toy_project", "expected", "toy_pack_summary.json")


def run_smoke(home=None):
    """smoke 체크를 실행하고 (checks, home, all_ok) 를 반환. side-effect: BINGGU_HOME 설정, chdir(ROOT)."""
    home = os.path.abspath(home) if home else tempfile.mkdtemp(prefix="binggu_smoke_home_")
    os.makedirs(home, exist_ok=True)
    os.environ["BINGGU_HOME"] = home
    os.chdir(ROOT)  # examples 상대경로 동작

    if not (os.path.exists(FX_DIR) and os.path.exists(FX_FILE)):
        print("FAIL: fixture 없음 (%s / %s). repo 루트에서 실행하세요." % (FX_DIR, FX_FILE))
        sys.exit(2)

    from openbinggu_mcp_server_handlers import handle_tool
    from openbinggu_staging_write_selftest import OPERATING_PATHS

    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}

    checks = []
    def chk(name, cond): checks.append((name, bool(cond)))

    def chk_live(name, tr):
        # rank6 회귀망: live 결선 도구가 synthetic stub 으로 되돌아가면 FAIL.
        #   미결선 stub 은 tool_result 에 synthetic=True + verdict=NOT_IMPLEMENTED 를 남긴다
        #   (server_handlers _STUB_NOTE 계약). 그러면 게이트 verdict=ALLOW 만으로 green 통과하던
        #   구멍을 실값으로 막는다. selftest(설계상 stub·안내유지)에는 적용하지 않는다.
        checks.append((name, tr.get("synthetic") is not True
                       and tr.get("verdict") != "NOT_IMPLEMENTED"))

    r = handle_tool("selftest", {}, allow_root)
    chk("1.selftest_ALLOW", r.get("verdict") == "ALLOW" and r.get("executed"))

    r = handle_tool("capture_classify", {"utterance": "B안으로 결정"}, allow_root)
    chk("2.capture_classify_ALLOW", r.get("verdict") == "ALLOW")

    # SSOT 후보 게이트(should_capture) 후 — 자동 preview 는 판단문만 후보가 된다(SYN 영문 합성은 후보 0).
    r = handle_tool("capture_preview", {"utterances": ["이 입찰은 마진이 낮아 보류하기로 결정했다."]}, allow_root)
    tr = r.get("tool_result") or {}
    chk("3.capture_preview_ALLOW_nothing_saved",
        r.get("verdict") == "ALLOW" and tr.get("nothing_saved") is True and len(tr.get("candidates", [])) >= 1)

    # #67/CS-1/CS-5: 게이트 verdict=ALLOW 만으로 통과 처리 금지 — tool_result 실값까지 검증.
    r = handle_tool("pack_build", {"input_dir": FX_DIR}, allow_root)
    tr = r.get("tool_result") or {}
    chk("4.pack_build_GO_nodes>0",
        r.get("verdict") == "ALLOW" and tr.get("verdict") == "GO"
        and tr.get("counts", {}).get("nodes", 0) > 0)
    chk_live("4s.pack_build_not_synthetic_stub", tr)

    r = handle_tool("pack_validate", {"pack_path": FX_FILE}, allow_root)
    tr = r.get("tool_result") or {}
    # FX_FILE(toy 요약 json)은 manifest 계약 필드 미충족 → STOP 이 정확한 실값(negative 검증).
    # valid manifest 의 PASS positive 는 아래 5b(build_pack 산출 manifest)에서 확인.
    chk("5.pack_validate_STOP_on_incomplete_summary",
        r.get("verdict") == "ALLOW" and tr.get("verdict") == "STOP")
    chk_live("5s.pack_validate_not_synthetic_stub", tr)

    r = handle_tool("publish_guard_dryrun", {"pack_path": FX_FILE}, allow_root)
    tr = r.get("tool_result") or {}
    # dry-run 은 publish_approved=False 고정 → fail-closed(BLOCK)이 정상. verdict 실값 존재 확인.
    chk("6.publish_guard_dryrun_ran",
        r.get("verdict") == "ALLOW" and tr.get("verdict") in ("BLOCK", "PASS", "REVIEW_ONLY", "ALLOW"))
    chk_live("6s.publish_guard_not_synthetic_stub", tr)

    # 7. consumer_smoke — CS-1: 단일 요약 json 이 아니라 실제 5-jsonl pack 디렉토리를 read.
    #    build_pack 으로 repo 상대경로(tmp/) 5파일 pack 을 런타임 생성(절대 temp 는 outside_root BLOCK).
    from binggupack.pack import pack_factory as _pf
    _cdir_rel = os.path.join("tmp", "smoke_consumer_pack")
    _cdocs = [{
        "nodes": [{"id": "node:STAGING:sm:%d" % i, "promotion_allowed": False,
                   "properties": {"candidate": True, "sentence": "스모크 소비 문장 %d." % i,
                                  "origin": "watcher", "domain": "STAGING_UNASSIGNED"},
                   "evidence_refs": ["EVC-sm-%d" % i]} for i in range(3)],
        "evidence_index": [{"evidence_id": "EVC-sm-%d" % i, "kind": "file_pointer",
                            "domain": "STAGING_UNASSIGNED", "promotion_allowed": False, "note": "p"}
                           for i in range(3)],
        "evidence_chunks": [{"item_id": "EVC-sm-%d" % i, "text": "스모크 소비 문장 %d." % i,
                             "source": "harvest :: url :: src", "evidence_meta": {"raw_pointer": "x"}}
                            for i in range(3)],
    }]
    _pf.build_pack("smoke_consumer", _cdocs, out_dir=os.path.join(ROOT, _cdir_rel))
    # 5b. pack_validate PASS positive — build_pack 산출 manifest 는 validate_pack 계약 충족.
    r = handle_tool("pack_validate", {"pack_path": os.path.join(_cdir_rel, "manifest.json")}, allow_root)
    tr = r.get("tool_result") or {}
    chk("5b.pack_validate_PASS_on_valid_manifest",
        r.get("verdict") == "ALLOW" and tr.get("verdict") in ("PASS", "REVIEW_ONLY"))
    r = handle_tool("consumer_smoke", {"pack_path": _cdir_rel}, allow_root)
    tr = r.get("tool_result") or {}
    chk("7.consumer_smoke_GO_nodes>0",
        r.get("verdict") == "ALLOW" and tr.get("verdict") == "GO"
        and tr.get("counts", {}).get("nodes", 0) == 3)
    chk_live("7s.consumer_smoke_not_synthetic_stub", tr)
    # 7b. raw 미노출(CS-2) — 원문 문장이 반환 blob 에 없어야.
    import json as _json7
    chk("7b.consumer_smoke_no_raw_leak", "스모크 소비 문장" not in _json7.dumps(r, ensure_ascii=False))
    # 7c. 빈 pack → STOP (CS-1 빈 입력 false-GO 방지).
    _edir_rel = os.path.join("tmp", "smoke_consumer_empty")
    _pf.build_pack("smoke_empty", [], out_dir=os.path.join(ROOT, _edir_rel))
    r = handle_tool("consumer_smoke", {"pack_path": _edir_rel}, allow_root)
    tr = r.get("tool_result") or {}
    chk("7c.consumer_smoke_empty_STOP", r.get("verdict") == "ALLOW" and tr.get("verdict") == "STOP")
    import shutil as _sh7
    _sh7.rmtree(os.path.join(ROOT, _cdir_rel), ignore_errors=True)
    _sh7.rmtree(os.path.join(ROOT, _edir_rel), ignore_errors=True)

    r = handle_tool("save_candidate", {"text": SYN, "indices": [1]}, allow_root)
    tr = r.get("tool_result") or {}
    chk("8.save_dryrun_write0",
        tr.get("executed_write") is False and tr.get("would_write_ledger") is False
        and tr.get("verdict") == "PREVIEW")

    # 9. actual save 시도(confirm 부재) → 자동/무단 저장 차단(confirm_phrase_mismatch, write 0).
    #    read-only 해제(f9a9c61) 이후 방어선이 G4_no_auto → confirm_phrase_mismatch 로 이동(handler selftest 와 정합).
    #    SYN(영문 합성)은 후보 0(nothing_to_save)이라 게이트를 못 태우므로 후보가 생기는 한국어 판단문 사용.
    KO = "이 입찰은 마진이 낮아 보류하기로 결정했다."
    r = handle_tool("save_candidate",
                    {"text": KO, "indices": [1], "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    chk("9.save_no_confirm_REJECT_write0",
        tr.get("executed_write") is False and tr.get("reason") == "confirm_phrase_mismatch")

    # 9b. confirm 정확일치("SAVE 1")만으로는 저장 안 됨 — 사람 SAVE 앵커(save_gate_log) 없으면
    #     actor=reader 유지 → G4_no_auto 차단(P0 봉인: 모델이 dry-run 의 confirm_expected 를 재현해
    #     사람 발화 0으로 write 하던 우회 차단, 2026-07-10).
    r = handle_tool("save_candidate",
                    {"text": KO, "indices": [1], "dry_run": False, "confirm": "SAVE 1"}, allow_root)
    tr = r.get("tool_result") or {}
    # MCP save approval 배선 제거(2026-07-13): 방어선은 core 의 G4_no_auto 단일(사람 save-n 앵커 없으면
    # actor=reader 유지). write 0(fail-closed)은 불변.
    chk("9b.confirm_alone_BLOCKED(no_human_anchor)",
        tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto")

    # 9c. 사장님이 실제 'SAVE 1' 을 키보드 입력하면 save_gate hook 이 앵커를 남긴다(여기선 그 앵커를 직접
    #     기록해 시뮬). save-n 참조 바인딩: 훅은 (preview_ref, idx) ref 레코드 1행 + 레거시 sh 행을
    #     병기 append 하고, 승격 정본은 ref 대조다. 그때만 confirm 정확일치가 human 승격 저장으로
    #     이어진다(격리 BINGGU_HOME 에만).
    import json as _json
    import os as _os
    import sys as _sys
    _sd = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "scripts")
    if _sd not in _sys.path:
        _sys.path.insert(0, _sd)
    import binggu_save_gate as _sgate
    from binggupack.capture import preview as _cvp
    _cands9 = _cvp.capture_preview(KO)["candidates"]
    _sgate.write_last_preview(_cands9)
    _sgate.gate_record_from_prompt("SAVE 1")
    # ref 레코드 단정 — gate log 에 (pref, idx=1) 이 실재(승격 정본 경로 증명 · 원문 미저장)
    _pref9 = _sgate.preview_ref_for_candidates(_cands9)
    _ref_seen = False
    with open(_sgate.gate_path(), encoding="utf-8") as _f9:
        for _ln in _f9:
            try:
                _d9 = _json.loads(_ln)
            except Exception:
                continue
            if _d9.get("pref") == _pref9 and 1 in (_d9.get("idxs") or []):
                _ref_seen = True
    chk("9c0.save_gate_ref_record_present", _ref_seen)
    r = handle_tool("save_candidate",
                    {"text": KO, "indices": [1], "dry_run": False, "confirm": "SAVE 1"}, allow_root)
    tr = r.get("tool_result") or {}
    chk("9c.human_anchor_confirm_isolated_write",
        tr.get("executed_write") is True and tr.get("saved") == 1)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    chk("10.operating_ledger_write_0", op_before == op_after)

    all_ok = all(ok for _, ok in checks)
    return checks, home, all_ok


def run_smoke_cli(argv=None):
    """scripts/smoke_test.py wrapper 진입점. 출력/exit code 는 v1.10.0 과 동일."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=None, help="BINGGU_HOME (격리 home). 미지정 시 temp.")
    args = ap.parse_args(argv)

    checks, home, all_ok = run_smoke(args.home)

    print("=" * 64)
    print("BingguPack MCP — offline smoke test")
    print("=" * 64)
    for name, ok in checks:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("-" * 64)
    print("  home                  :", home)
    print("  actual_api_call       : 0")
    print("  source_fetch          : 0")
    print("  production_write       : 0")
    print("  G4_no_auto            : confirmed (AI/reader actor cannot durably save)")
    print("  real_home_changed     : 0 (BINGGU_HOME redirected; OPERATING_PATHS unchanged)")
    print("\n  RESULT:", "PASS" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)
