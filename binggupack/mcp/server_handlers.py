#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu MCP 서버 도구 핸들러 결선 (정본 in-package, 트랙 C strangler).

목적:
- mcp path_gate_adapter.guarded_tool_call 을 실제 MCP 도구 핸들러 후보에 연결.
- read/dry-run 도구 + save_candidate(write-gated) 노출. write/apply/push/sanitizer/enum/team_paid/marketplace 부재.
- 도구의 path 입력은 전부 guarded_tool_call 통과 → BLOCK 시 underlying 미호출.
- raw 경로/secret 미출력 → executed/verdict/reason_code/path_id 만.
- save_candidate: dry-run 기본(write 0)·SAVE n confirm 정확일치·actor 서버 하드 오버라이드(reader)·
  실 write 는 temp DB(open_g3)만(운영 ledger 미접촉). 영구금지 25(자동적재)/26(cos 결정) 위반 0.

정본 이관(v1.11.x): 로직은 여기(binggupack/mcp/server_handlers.py)가 정본이고
scripts/openbinggu_mcp_server_handlers.py 는 공개 심볼을 재노출하는 thin shim 이다.
내부 참조: guarded_tool_call 은 in-package(.path_gate_adapter), classify 는 정본
binggupack.classifier.capture_classifier — scripts 재진입 없음(순환 해소). 함수내 lazy 의존
(conversation_capture_preview·save_gate·candidate_save·deprecate_g3·staging_write_selftest)은
아직 scripts 잔류 → 상단에서 절대경로 _SCRIPTS 를 sys.path 에 보장.

범위: 핸들러 함수 + 디스패치 테이블 + synthetic selftest.
CLI: python scripts/openbinggu_mcp_server_handlers.py --selftest
"""
import sys
import os

# lazy scripts import(conversation_capture_preview·save_gate·candidate_save·deprecate_g3·
# staging_write_selftest)는 scripts 잔류 → 절대경로로 <repo>/scripts 를 sys.path 에 보장.
# (__file__ 가 binggupack/mcp 라 repo/scripts 는 자동 포함되지 않음.)
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from .path_gate_adapter import guarded_tool_call
from binggupack.classifier.capture_classifier import classify


# ---- underlying 도구(dry-run mock, FS write 0) ----
# 실제로는 각 스크립트의 read/dry-run 동작에 결선. 여기선 synthetic mock(파일 작업 0).
def _u_pack_build(params=None):
    return {"action": "pack_build", "mode": "dry-run", "pack": "candidate(temp)"}


def _u_pack_validate(params=None):
    return {"action": "pack_validate", "mode": "read", "verdict": "checked"}


def _u_consumer_smoke(params=None):
    return {"action": "consumer_smoke", "mode": "read", "read": "ok"}


def _u_publish_guard_dryrun(params=None):
    return {"action": "publish_guard_dryrun", "mode": "dry-run", "guard": "evaluated"}


def _u_selftest(params=None):
    return {"action": "selftest", "mode": "read", "gate": "see scripts"}


def _u_capture_classify(params=None):
    # 발화 1건 판정(메모리 순수함수, write 0). 발화 원문은 반환 안 함(state/signals만).
    params = params or {}
    v = classify(params.get("utterance", ""), params.get("prev_turn"))
    return {"action": "capture_classify", "mode": "read",
            "state": v["state"], "confidence": v["confidence"], "pinned": v["pinned"],
            "signals": v["signals"]}


def _u_capture_preview(params=None):
    # 발화 리스트 → semantic 도장(canon) preview. read-only(저장 0).
    # CaptureBuffer(semantic 없음, classify만)가 아니라 openbinggu_conversation_capture_preview
    # (v1.6.1, canon.suggest_label_kind = canonical 5종 의미분류)로 결선. hosted .ts 판단 쏠림 회피.
    params = params or {}
    utts = params.get("utterances") or []
    text = "\n".join(u for u in utts if isinstance(u, str))
    import openbinggu_conversation_capture_preview as cvp
    result = cvp.capture_preview(text)
    # 사람-발화 게이트(0-A): 후보 hash 만 영속(원문 0) → SAVE hook 이 'SAVE n' 대조용으로 읽음.
    try:
        import binggu_save_gate as sgate
        sgate.write_last_preview(result.get("candidates", []))
    except Exception:
        pass  # 영속 실패해도 preview 반환엔 무영향(read 도구)
    return {"action": "capture_preview", "mode": "read", **result}


def _u_save_candidate(params=None):
    """선택 후보 staging 저장 — dry-run 기본·SAVE n confirm 정확일치·actor=human 강제.

    영구금지 정합:
      25(자동적재 금지): actor in (auto,reader) → 표면 즉시 G4_no_auto 거부.
      26(cos 결정사용 금지): 저장 게이트는 confirm+A0+PII(규칙)만. cos는 preview 도장 추천뿐.
      비가역 write default-deny: dry_run 기본 True → write 0. 실 write 는 dry_run=False+confirm 정확일치 전부 충족시만.
    안전 경계:
      - actor 는 MCP 입력을 신뢰하지 않고 reader 로 하드 오버라이드(MCP 경유=사람 직접발화 아님).
        confirm='SAVE n' 정확일치만이 사람-선택 증거(모델 단독은 사용자가 본 preview 인덱스를 재현 못함 가정).
      - dry_run 이면 capture_preview 만 재실행(write 0). 실 write 경로는 save_selected 내부 게이트(G4/confirm/A0/PII/
        StagingDB 운영경로 거부)에 위임 — 핸들러는 게이트 재구현 0.
      - MCP는 경로 입력(ledger_path 등)을 일절 무시 → temp DB(open_g3) 강제. 운영 ledger 경로 주입 구조적 불가.
      - 반환은 count/pack_id/reason 만 — 원문 sentence 는 dry-run preview 에서만(사용자가 골라야 하므로), write 응답엔 미포함.
    """
    params = params or {}
    text = params.get("text", "")
    indices = params.get("indices") or []
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)  # 기본 dry-run (비가역 write default-deny)

    # 입력 actor 불신 — MCP 경유 호출은 정의상 사람 직접발화가 아님 → reader 하드 오버라이드.
    # confirm 정확일치만 사람-선택 증거. (auto/reader 는 save_selected G4_no_auto 가 항상 발동)
    actor = "reader"

    import openbinggu_conversation_capture_preview as cvp
    pv = cvp.capture_preview(text)
    cands = pv["candidates"]
    expected = "SAVE " + ",".join(str(i) for i in indices)

    if dry_run:
        # dry-run: write 0. 저장될 후보 미리보기(index/도장/문장) + 기대 confirm 안내만.
        return {"action": "save_candidate", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "confirm_expected": expected,
                "would_write_ledger": False,
                "selectable": sum(1 for i in indices if isinstance(i, int) and 1 <= i <= len(cands)),
                "preview": [{"index": j + 1, "label_kind": c["label_kind"], "sentence": c["sentence"]}
                            for j, c in enumerate(cands)]}

    # dry_run=False (명시 opt-out): confirm 정확일치 1차 게이트 — 불일치면 write 진입 0.
    if confirm != expected:
        return {"action": "save_candidate", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "confirm_phrase_mismatch",
                "confirm_expected": expected}

    # 실 write 경로 — temp DB(open_g3) 강제. MCP는 경로 입력을 일절 받지 않음(운영 ledger 차단).
    import tempfile
    from openbinggu_deprecate_and_remind_g3 import open_g3
    from openbinggu_conversation_candidate_save import save_selected
    work = tempfile.mkdtemp(prefix="obg_mcp_save_")
    # ledger_path 등 외부 경로 입력 무시 — MCP는 temp staging 전용(default-deny). 운영 ledger 경로 주입 불가.
    db_path = os.path.join(work, "s.sqlite")
    snap_dir = os.path.join(work, "snap")
    os.makedirs(snap_dir, exist_ok=True)  # staging_apply snapshot 복사 대상 폴더 보장(없으면 FileNotFoundError로 stdio 루프 사망)
    db = open_g3(db_path)
    try:
        r = save_selected(db, text, indices, {"actor": actor, "confirm": confirm},
                          snap_dir, due_date=params.get("due_date"))
    finally:
        db.close()
    # actor=reader 하드 오버라이드 → save_selected 가 G4_no_auto 로 항상 BLOCK.
    # 즉 MCP 단독 dry_run=False 호출은 confirm 통과해도 실저장 0(사람 직접발화 actor 증거 부재).
    return {"action": "save_candidate", "mode": "write-gated",
            "verdict": "ALLOW" if r.get("applied") else "BLOCK",
            "executed_write": bool(r.get("applied")),
            "saved": r.get("saved"), "skipped_existing": r.get("skipped_existing"),
            "rejected": r.get("rejected"), "reason": r.get("reason"),
            "pack_id": r.get("pack_id"), "ledger": "temp_only"}


# ---- 노출 도구 테이블(read/dry-run 만). 위험 도구는 의도적으로 부재 ----
TOOLS = {
    "pack_build":           {"path_params": ["input_dir"], "underlying": _u_pack_build,          "mode": "dry-run"},
    "pack_validate":        {"path_params": ["pack_path"],  "underlying": _u_pack_validate,       "mode": "read"},
    "consumer_smoke":       {"path_params": ["pack_path"],  "underlying": _u_consumer_smoke,      "mode": "read"},
    "publish_guard_dryrun": {"path_params": ["pack_path"],  "underlying": _u_publish_guard_dryrun, "mode": "dry-run"},
    "selftest":             {"path_params": [],             "underlying": _u_selftest,            "mode": "read"},
    # 캡처 엔진(메모리 순수, write 0). path 입력 없음 → input_schema 로 일반 params 노출.
    "capture_classify":     {"path_params": [], "underlying": _u_capture_classify, "mode": "read",
                             "input_schema": {"properties": {"utterance": {"type": "string"},
                                                             "prev_turn": {"type": "string"}},
                                              "required": ["utterance"]}},
    "capture_preview":      {"path_params": [], "underlying": _u_capture_preview, "mode": "read",
                             "input_schema": {"properties": {"utterances": {"type": "array",
                                                                            "items": {"type": "string"}}},
                                              "required": ["utterances"]}},
    # save 도구 — write-gated. dry-run 기본·SAVE n confirm 정확일치·actor 서버 하드 오버라이드(reader).
    # _FORBIDDEN db_write 는 무차별 write 금지 라벨이고, save 는 confirm 게이트 통과 단건만 예외적으로
    # 실 write 경로 진입(그것도 temp DB·actor=reader 로 G4 항상 발동). 경로 입력(ledger_path 등) 일절 무시 — MCP는 운영 ledger 못 염.
    "save_candidate":       {"path_params": [], "underlying": _u_save_candidate, "mode": "write-gated",
                             "input_schema": {"properties": {
                                 "text": {"type": "string"},
                                 "indices": {"type": "array", "items": {"type": "integer"}},
                                 "confirm": {"type": "string"},
                                 "dry_run": {"type": "boolean"},
                                 "due_date": {"type": "string"}},
                              "required": ["text", "indices"]}},
}

# 노출 금지(핸들러 부재로 자동 차단되지만, 명시 거부 목록으로 의도 박제)
_FORBIDDEN = {
    "opencrab_write", "opencrab_apply", "opencrab_ingest", "store_write",
    "github_push", "opencrab_upload", "sanitizer_replace", "enum_set",
    "team_billing", "marketplace_publish", "db_write",
}


def handle_tool(tool_name, params, allow_root):
    """
    MCP 도구 요청 1건 처리.
    - 미노출/금지 도구 → tool_not_exposed (underlying 미호출).
    - path 입력 있으면 guarded_tool_call 로 gate 통과시킨 뒤에만 underlying.
    반환: raw 경로/secret 미포함.
    """
    params = params or {}
    if tool_name not in TOOLS:
        rc = "forbidden" if tool_name in _FORBIDDEN else "unknown"
        return {"executed": False, "verdict": "REJECT", "reason_code": "tool_not_exposed:" + rc,
                "tool": tool_name}

    spec = TOOLS[tool_name]
    path_inputs = [params[k] for k in spec["path_params"] if k in params and params[k] is not None]

    if not path_inputs:
        # path 입력 없는 read 도구 → 바로 실행
        return {"executed": True, "verdict": "ALLOW", "tool": tool_name,
                "tool_result": spec["underlying"](params=params)}

    # path 입력은 전부 gate 통과(실행 직전 재검사 포함). BLOCK 시 underlying 미호출.
    r = guarded_tool_call(spec["underlying"], path_inputs=path_inputs,
                          allow_root=allow_root, tool_kwargs={"params": params})
    r["tool"] = tool_name
    return r


# ---------------- selftest ----------------

# save selftest 입력(문서·판단 섞임). dry-run preview 는 사용자 선택용으로 sentence 노출이 의도 동작.
_SAVE_CONVO = ("이 문서는 배포 절차를 정의한다. 이 입찰은 마진이 낮아 보류한다.")


def _selftest():
    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))

    print("=" * 72)
    print("OpenBinggu MCP server handlers 결선 후보 (synthetic / selftest)")
    print("=" * 72)

    all_ok = True
    raw_leak = False

    cases = [
        # (name, tool, params, expect_executed, note)
        ("validate_toy_ok",      "pack_validate",        {"pack_path": "examples/toy_project/p.json"}, True,  "ALLOW"),
        ("build_toy_ok",         "pack_build",           {"input_dir": "examples/toy_project"},        True,  "ALLOW"),
        ("selftest_no_path_ok",  "selftest",             {},                                           True,  "no-path read"),
        ("build_parent_block",   "pack_build",           {"input_dir": "../outside"},                  False, "parent_escape"),
        ("consumer_npki_block",  "consumer_smoke",       {"pack_path": "C:/Users/PC/AppData/NPKI/c.der"}, False, "deny_cert_npki"),
        ("guard_env_block",      "publish_guard_dryrun", {"pack_path": "examples/toy_project/.env"},    False, "deny_secret"),
        ("validate_bidengine_block", "pack_validate",    {"pack_path": "C:/Users/PC/safety-app/bid-engine/x"}, False, "deny_bid_engine"),
        ("forbidden_write",      "opencrab_write",       {"pack_path": "examples/toy_project/p.json"}, False, "tool_not_exposed:forbidden"),
        ("forbidden_push",       "github_push",          {},                                           False, "tool_not_exposed:forbidden"),
        ("unknown_tool",         "do_something",         {},                                           False, "tool_not_exposed:unknown"),
        ("capture_classify_ok",  "capture_classify",     {"utterance": "B안으로 결정"},                 True,  "read no-path"),
        ("capture_preview_ok",   "capture_preview",      {"utterances": ["이거 저장해", "ㅋㅋ"]},        True,  "read no-path"),
        # save 도구 — dry-run 기본은 executed=True(도구 실행됨)이나 executed_write=False(ledger write 0).
        ("save_dryrun_default",  "save_candidate",       {"text": _SAVE_CONVO, "indices": [1]},        True,  "dry-run preview"),
    ]

    import json as _json
    for name, tool, params, exp_exec, note in cases:
        r = handle_tool(tool, params, allow_root)
        executed = bool(r.get("executed"))
        ok = (executed == exp_exec)
        all_ok = all_ok and ok
        # raw 미출력: 결과에 입력 경로 substring 없어야.
        # 단 save dry-run preview 는 사용자 선택용 sentence 노출이 의도 동작 → text 입력은 leak 검사 면제.
        blob = _json.dumps(r, ensure_ascii=False)
        for k, v in params.items():
            if tool == "save_candidate" and k == "text":
                continue
            if isinstance(v, str) and v.strip() and v.strip() in blob:
                raw_leak = True
        verdict = r.get("verdict")
        rc = r.get("reason_code") or (r.get("blocked") and r["blocked"][0].get("reason_code")) or ""
        print("  [%s] %-26s tool=%-20s executed=%-5s verdict=%-7s %s"
              % ("OK" if ok else "FAIL", name, tool, executed, verdict, rc))

    # ----- save 도구 전용 검증 (실 ledger write 0 보장: temp DB·dry-run·mock만) -----
    from openbinggu_staging_write_selftest import OPERATING_PATHS
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    save_ok = True
    save_notes = []

    # S1) dry-run 기본 — write 0(executed_write=False·would_write_ledger=False), preview 노출.
    r = handle_tool("save_candidate", {"text": _SAVE_CONVO, "indices": [1]}, allow_root)
    tr = r.get("tool_result") or {}
    s1 = (r.get("executed") is True and tr.get("executed_write") is False
          and tr.get("would_write_ledger") is False and tr.get("verdict") == "PREVIEW")
    save_ok = save_ok and s1
    save_notes.append(("save_dryrun_write0", s1))

    # S2) confirm 불일치 — dry_run=False 라도 write 0 (REJECT).
    r = handle_tool("save_candidate",
                    {"text": _SAVE_CONVO, "indices": [1], "confirm": "SAVE 9", "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    s2 = (tr.get("executed_write") is False and tr.get("reason") == "confirm_phrase_mismatch")
    save_ok = save_ok and s2
    save_notes.append(("save_confirm_mismatch_reject", s2))

    # S3) 자동호출(actor=auto 위조 시도) — 서버가 actor=reader 하드 오버라이드 → save_selected G4 항상 BLOCK.
    #     confirm 정확일치+dry_run=False 라도 실저장 0.
    r = handle_tool("save_candidate",
                    {"text": _SAVE_CONVO, "indices": [1], "confirm": "SAVE 1",
                     "dry_run": False, "actor": "auto"}, allow_root)
    tr = r.get("tool_result") or {}
    s3 = (tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto")
    save_ok = save_ok and s3
    save_notes.append(("save_auto_call_blocked_G4", s3))

    # S4) 운영 store(OPERATING_PATHS) mtime 불변 — 실 ledger write 0 입증.
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    s4 = (op_before == op_after)
    save_ok = save_ok and s4
    save_notes.append(("operating_ledger_write_0", s4))

    all_ok = all_ok and save_ok
    print("\n  -- save tool gates --")
    for nm, ok in save_notes:
        print("  [%s] %s" % ("OK" if ok else "FAIL", nm))

    # 노출 도구가 read/dry-run/write-gated 인지 확인.
    # write-gated = confirm(SAVE n 정확일치)+actor 게이트 통과 단건만 실 write — default-deny 약화 아님.
    exposed_ok = all(TOOLS[t]["mode"] in ("read", "dry-run", "write-gated") for t in TOOLS)
    no_forbidden_exposed = all(f not in TOOLS for f in _FORBIDDEN)
    all_ok = all_ok and exposed_ok and no_forbidden_exposed
    print("\n  exposed_tools_read_dryrun_or_writegated_only:", exposed_ok)
    print("  forbidden_tools_not_exposed:", no_forbidden_exposed)
    print("  raw_path_not_leaked:", (not raw_leak))
    print("  operating_store_unchanged: True (핸들러 + mock, 운영 ledger write 0)")
    print("  save_default_dry_run: True  real_ledger_write: 0 (selftest=temp DB only)")
    print("  mcp_protocol_layer: openbinggu_mcp_server.serve_stdio (실 설정 등록은 owner)")

    gate = "GO" if (all_ok and not raw_leak) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_mcp_server_handlers.py [--selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
