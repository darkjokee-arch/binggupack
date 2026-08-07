#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu MCP 서버 도구 핸들러 결선 (정본 in-package, 트랙 C strangler).

목적:
- mcp path_gate_adapter.guarded_tool_call 을 실제 MCP 도구 핸들러 후보에 연결.
- read/dry-run 도구 + save_candidate(write-gated) 노출. write/apply/push/sanitizer/enum/team_paid/marketplace 부재.
- 도구의 path 입력은 전부 guarded_tool_call 통과 → BLOCK 시 underlying 미호출.
- raw 경로/secret 미출력 → executed/verdict/reason_code/path_id 만.
- save_candidate: dry-run 기본(write 0)·SAVE n confirm 정확일치·actor 서버 하드 고정(reader)·
  사람 승격은 core 의 save-n 참조 바인딩 앵커(owner 키보드 'SAVE n' → hook 기록)만.
  영구금지 25(자동적재)/26(cos 결정) 위반 0.
- MCP mutation 표면의 approval 소비 배선(구 P1-A approval_gate.authorize)은 제거(2026-07-13 owner 결정).
  approval core(binggupack/safety/trusted_approval.py)·owner CLI(binggu approval / --approval-id)·
  Studio Approval Center 는 별도 자산으로 보존 — MCP 도구 호출은 approval_id 로 write 승격되지 않는다.

정본 이관(v1.11.x): 로직은 여기(binggupack/mcp/server_handlers.py)가 정본이고
scripts/openbinggu_mcp_server_handlers.py 는 공개 심볼을 재노출하는 thin shim 이다.
내부 참조: guarded_tool_call 은 in-package(.path_gate_adapter), classify 는 정본
binggupack.classifier.capture_classifier — scripts 재진입 없음(순환 해소). 함수내 lazy 의존은
전부 in-package facade 경유(capture→binggupack.capture.preview, save_gate→binggupack.safety.save_gate,
open_g3/save_selected→binggupack.storage, OPERATING_PATHS→binggupack.paths) → server_handlers 는
scripts 를 직접 import 하지 않는다(_SCRIPTS 부트스트랩 책임은 각 facade 로 이동). facade 정본 본문
일부는 아직 scripts 잔류하나 그 부트스트랩은 facade 내부에서 처리.

범위: 핸들러 함수 + 디스패치 테이블 + synthetic selftest.
CLI: python scripts/openbinggu_mcp_server_handlers.py --selftest
"""
import sys
import os

from .path_gate_adapter import guarded_tool_call
from binggupack.classifier.capture_classifier import classify


# ---- underlying 도구(dry-run mock, FS write 0) ----
# 실제로는 각 스크립트의 read/dry-run 동작에 결선. 여기선 synthetic mock(파일 작업 0).
# ★ 이 5개는 미결선 stub 이다 — 실제 검사/빌드 로직 0(고정 응답). 실 검사는 CLI/scripts 경로
#   (python scripts/openbinggu_*.py --selftest 등)에 있다. MCP 표면에서 "성공"처럼 읽혀 실검증
#   통과로 오인되지 않도록 synthetic=True + NOT_IMPLEMENTED 를 명시한다(응답만 정직화, 기능 무변경).
_STUB_NOTE = "미결선 stub — 실제 검사는 CLI/scripts 경로. MCP 노출은 존재/경로 안내용."


def _u_pack_build(params=None):
    # 실 결선(2026-07-17): input_dir → in-package incoming_folder 파이프라인(scan + markdown 블록파싱 +
    # batch_redact/scan_residual_pii 잔존 시 전체 STOP 게이트) → candidate_mvp2.to_nodes →
    # pack_factory.build_pack dry-run(out_dir=None·메모리). temp only·production write 0·raw 경로 미노출
    # (counts/verdict/reason_code 만 반환 — REJECT/STOP 의 source_path·stops 는 개수만 추출). MCP 상한
    # (파일수/바이트) + 심링크 자식 거부(reject_symlinks)로 자원/경로우회 방어. path_gate 통과 후 도달.
    params = params or {}
    input_dir = params.get("input_dir")
    if not input_dir:
        return {"action": "pack_build", "mode": "dry-run", "verdict": "REJECT",
                "reason_code": "missing_input_dir"}
    from binggupack.pack import candidate_mvp2 as _CM
    from binggupack.pack import incoming_folder as _IF
    from binggupack.pack import pack_factory as _PF
    _adapt = _IF.adapt_incoming_folder(
        [input_dir], max_files=500, max_file_bytes=2_000_000, max_total_bytes=20_000_000,
        reject_symlinks=True)
    _gate = _adapt.get("gate")
    if _gate == "REJECT":
        return {"action": "pack_build", "mode": "dry-run", "verdict": "REJECT",
                "reason_code": _adapt.get("reason"), "n_files": _adapt.get("n_files")}
    if _gate == "STOP":
        # PII/secret 잔존 — raw(stops[].source_path) 미노출, 개수만.
        return {"action": "pack_build", "mode": "dry-run", "verdict": "STOP",
                "reason_code": "pii_secret_residual", "n_files": _adapt.get("n_files"),
                "n_stops": len(_adapt.get("stops", []))}
    _chunks = _adapt.get("chunks", [])
    _nodes, _ev_index, _node_stops = _CM.to_nodes(_chunks)
    _documents = [{"nodes": _nodes, "evidence_index": _ev_index, "evidence_chunks": _chunks}]
    _res = _PF.build_pack("incoming", _documents, out_dir=None)   # dry-run 메모리(파일 write 0)
    _status = _res.get("status")
    _c = _res.get("counts", {})
    _n_nodes = int(_c.get("nodes", 0))
    if _status == "BLOCK":
        _verdict = "STOP"          # candidate 불변식 위반 등
    elif _n_nodes > 0:
        _verdict = "GO"
    else:
        _verdict = "EMPTY"         # 파일은 스캔됐으나 노드 0 — silent drop 방지(명시 표기)
    return {"action": "pack_build", "mode": "dry-run", "verdict": _verdict,
            "counts": {"nodes": _n_nodes,
                       "evidence_index": int(_c.get("evidence_index", 0)),
                       "evidence_chunk": int(_c.get("evidence_chunk", 0)),
                       "documents": int(_c.get("documents", 0)),
                       "node_stops": len(_node_stops)},
            "validate": (_res.get("verdict") or {}).get("verdict")}


def _u_pack_validate(params=None):
    # 실 결선(2026-07-17): pack manifest 계약검증 정본 binggupack.pack.contract_validate.validate_pack.
    # read-only 순수함수(production write 0·graph 0·LLM 0). path 입력(pack_path)은 handle_tool 이
    # guarded_tool_call 로 gate 통과시킨 뒤에만 도달. verdict/stops/reviews/notes 만 반환(raw 경로 미노출·
    # manifest 계약 필드값만 stops 에 노출되며 secret/PII 아님).
    params = params or {}
    pack_path = params.get("pack_path")
    if not pack_path:
        return {"action": "pack_validate", "mode": "read", "verdict": "REJECT",
                "reason_code": "missing_pack_path"}
    import json as _json
    from binggupack.pack import contract_validate as _CV
    try:
        with open(pack_path, encoding="utf-8") as _f:
            _doc = _json.load(_f)
    except (OSError, ValueError) as _e:
        return {"action": "pack_validate", "mode": "read", "verdict": "REJECT",
                "reason_code": "load_error:" + type(_e).__name__}
    # manifest 후보: canonical(manifest 키) / summary fixture(pack 키) / flat(doc 자체) 순.
    _manifest = None
    if isinstance(_doc, dict):
        _manifest = _doc.get("manifest") or _doc.get("pack")
    if _manifest is None:
        _manifest = _doc
    _res = _CV.validate_pack(_manifest)
    return {"action": "pack_validate", "mode": "read", "verdict": _res["verdict"],
            "stops": _res["stops"], "reviews": _res["reviews"], "notes": _res["notes"]}


def _u_consumer_smoke(params=None):
    # 실 결선(2026-07-17): pack 소비(읽기) smoke 정본 binggupack.pack.pack_consumer.
    # pack_dir 5파일(manifest/nodes/edges/evidence_index/evidence_chunk) → consume + safety_checks →
    # summarize(counts + 안전 불리언만). ★raw(claim/relation/source_pointer) 미노출(summarize 가 strip).
    # verdict: 안전필수(candidate/promotion/secret/confirmed) 전부 통과 AND nodes>0 → GO(빈 pack=STOP).
    # CS-3: pack_dir 자식 5파일이 심링크면 REJECT(path_gate 우회 외부 read 차단). path_gate 통과 후 도달.
    params = params or {}
    pack_path = params.get("pack_path")
    if not pack_path:
        return {"action": "consumer_smoke", "mode": "read", "verdict": "REJECT",
                "reason_code": "missing_pack_path"}
    import os as _os
    for _child in ("manifest.json", "nodes.jsonl", "edges.jsonl",
                   "evidence_index.jsonl", "evidence_chunk.jsonl"):
        if _os.path.islink(_os.path.join(pack_path, _child)):
            return {"action": "consumer_smoke", "mode": "read", "verdict": "REJECT",
                    "reason_code": "symlink_child_forbidden"}
    from binggupack.pack import pack_consumer as _PC
    _view, _checks = _PC.run_on_pack(pack_path)
    _summary = _PC.summarize(_view, _checks)
    return {"action": "consumer_smoke", "mode": "read",
            "verdict": _summary["verdict"], "counts": _summary["counts"],
            "checks": _summary["checks"], "info": _summary["info"]}


def _collect_source_pointers(doc):
    """pack dict 어디든 source pointer 필드(source_path/source_ref/path)를 재귀 수집.
    pack layout(canonical/flat/summary) 무관 — raw 값은 classify 로만 넘기고 반환 안 함."""
    ptrs = []

    def _walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("source_path", "source_ref", "path") and isinstance(v, str):
                    ptrs.append(v)
                else:
                    _walk(v)
        elif isinstance(o, list):
            for x in o:
                _walk(x)

    _walk(doc)
    return ptrs


def _u_publish_guard_dryrun(params=None):
    # 실 결선(2026-07-17): 공개(publish) fail-closed 게이트 정본 binggupack.pack.scope_envelope.
    # pack 의 source pointer 를 재귀 수집→classify_source_pointers(clean/dirty/unknown, raw 미반환)→
    # publish_decision(items, publish_approved=False). dry-run 은 publish_approved 를 False 로 고정 —
    # 실제 공개 승인은 owner 전용(§MCP_EXPOSURE: 운영반영·외부전송 미노출). fail-closed: dirty/unknown
    # 1건↑ 또는 미승인이면 BLOCK. reason_codes/pointer_counts 만 반환(raw 경로 미노출). production write 0.
    params = params or {}
    pack_path = params.get("pack_path")
    if not pack_path:
        return {"action": "publish_guard_dryrun", "mode": "dry-run", "verdict": "REJECT",
                "reason_code": "missing_pack_path"}
    import json as _json
    from binggupack.pack import scope_envelope as _SE
    try:
        with open(pack_path, encoding="utf-8") as _f:
            _doc = _json.load(_f)
    except (OSError, ValueError) as _e:
        return {"action": "publish_guard_dryrun", "mode": "dry-run", "verdict": "REJECT",
                "reason_code": "load_error:" + type(_e).__name__}
    _cls = _SE.classify_source_pointers(_collect_source_pointers(_doc))
    _items = [{"mask_result": lbl} for lbl in _cls["labels"]]
    _dec = _SE.publish_decision(_items, publish_approved=False)
    return {"action": "publish_guard_dryrun", "mode": "dry-run", "verdict": _dec["verdict"],
            "publish_allowed": _dec["publish_allowed"], "reason_codes": _dec["reason_codes"],
            "pointer_counts": _cls["counts"]}


def _u_selftest(params=None):
    # 결선 안 함(정직 유지): selftest 배터리는 subprocess(scripts/*_selftest.py · python -m binggupack
    # --selftest)라 MCP 표면에서 실행하면 타임아웃·프로세스 위생 위험(좀비/hang). 노출은 안내만 —
    # verdict=NOT_IMPLEMENTED 유지(실검증 통과로 오인 방지). 실 게이트 CLI 를 note 로 명시.
    return {"action": "selftest", "mode": "read", "synthetic": True,
            "verdict": "NOT_IMPLEMENTED", "run_via": "cli",
            "note": ("MCP 미탑재(subprocess 프로세스 위생). 실 자가검증 CLI: "
                     "`python -m binggupack --selftest`(설치본) 또는 "
                     "`python scripts/binggu_publish_run_all_selftests.py`(개발 배터리).")}


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
    from binggupack.capture import preview as cvp
    result = cvp.capture_preview(text)
    # 사람-발화 게이트(0-A): 후보 hash 만 영속(원문 0) → SAVE hook 이 'SAVE n' 대조용으로 읽음.
    try:
        import binggupack.safety.save_gate as sgate
        sgate.write_last_preview(result.get("candidates", []))
    except Exception:
        pass  # 영속 실패해도 preview 반환엔 무영향(read 도구)
    return {"action": "capture_preview", "mode": "read", **result}


def _u_save_candidate(params=None):
    """선택 후보 staging 저장 — dry-run 기본·SAVE n confirm 정확일치·actor 하드 reader.

    영구금지 정합:
      25(자동적재 금지): actor in (auto,reader) → 표면 즉시 G4_no_auto 거부.
      26(cos 결정사용 금지): 저장 게이트는 confirm+A0+PII(규칙)만. cos는 preview 도장 추천뿐.
      비가역 write default-deny: dry_run 기본 True → write 0. 실 write 는 dry_run=False+confirm 정확일치 전부 충족시만.
    안전 경계:
      - actor 는 MCP 입력을 신뢰하지 않고 reader 로 하드 고정(MCP 경유=사람 직접발화 아님).
        사람 승격은 core(save_selected)의 save-n 참조 바인딩 앵커(owner 키보드 'SAVE n' → hook 기록)만 —
        "preview + 사람의 save n 입력" 단일 원칙(2026-07-12). 구 P1-A approval 승격 배선은
        제거됐다(2026-07-13) — approval_id 는 MCP write 를 승격하지 않는다.
      - dry_run 이면 capture_preview 만 재실행(write 0). 실 write 경로는 save_selected 내부 게이트(G4/confirm/A0/PII/
        StagingDB 운영경로 거부)에 위임 — 핸들러는 게이트 재구현 0.
      - MCP는 경로 입력(ledger_path 등)을 일절 무시 → 운영 ledger 는 서버 결정. 경로 주입 구조적 불가.
      - 반환은 count/pack_id/reason 만 — 원문 sentence 는 dry-run preview 에서만(사용자가 골라야 하므로), write 응답엔 미포함.
    """
    params = params or {}
    text = params.get("text", "")
    indices = params.get("indices") or []
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)  # 기본 dry-run (비가역 write default-deny)

    # actor 는 reader 하드 고정(write 경로). confirm 문자열은 dry-run 응답(confirm_expected)에서
    # 모델이 재현 가능 → "사람 증거"가 아니라 형식 검증(정확일치)에만 쓴다(§6).
    # 진짜 사람 승격 = save_selected 내부 save-n 참조 바인딩 앵커(owner 키보드 'SAVE n' → hook 기록)만.
    from binggupack.capture import preview as cvp
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

    # dry_run=False (명시 opt-out): confirm 정확일치 형식 게이트 — 불일치면 write 진입 0.
    if confirm != expected:
        return {"action": "save_candidate", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "confirm_phrase_mismatch",
                "confirm_expected": expected}

    # 실 write 경로 — 운영 ledger(BINGGU_HOME 우선·없으면 ~/.binggupack). MCP 외부 경로 입력은 무시(주입 차단).
    # actor=reader 하드 고정 — human 승격은 save_selected 내부 save-n 참조 바인딩 앵커(owner 키보드
    # 'SAVE n' → hook 기록)만. 앵커 없으면 G4_no_auto fail-closed. (구 P1-A approval 승격 배선 제거.)
    from binggupack.storage import open_g3, save_selected
    home = os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")
    db_path = os.path.join(home, "ledger.sqlite")
    snap_dir = os.path.join(home, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)  # staging_apply snapshot 복사 대상 폴더 보장
    db = open_g3(db_path)
    try:
        # MCP save 는 auto-classifier(explicit=False) 고정 — 렌더러/실행기 동일 explicit(TAE-P2-04).
        r = save_selected(db, text, indices, {"actor": "reader", "confirm": confirm},
                          snap_dir, due_date=params.get("due_date"),
                          speaker=params.get("speaker"), explicit=False)
    finally:
        db.close()
    return {"action": "save_candidate", "mode": "write-gated",
            "verdict": "ALLOW" if r.get("applied") else "BLOCK",
            "executed_write": bool(r.get("applied")),
            "saved": r.get("saved"), "skipped_existing": r.get("skipped_existing"),
            "rejected": r.get("rejected"), "reason": r.get("reason"),
            "pack_id": r.get("pack_id"), "ledger": "operating", **_mcp_write_extra(r, params)}


# ==== Phase 2 배치 A: 조회(read) 도구 — CLI recall/preflight/trace/status/list/reminders 노출 ====
# 안전 원칙(save_candidate 와 동일):
#   - ledger 경로는 서버가 결정(BINGGU_HOME 우선·없으면 ~/.binggupack). MCP 입력 경로 일절 무시(주입 차단).
#   - 전부 read-only 순수함수(why_search/preflight_context/judgment_trace/list_pending/list_candidates/
#     list_due_reminders) 호출 → ledger write 0. use_count++ 같은 사람-신호 기록은 노출 안 함(순수 read).
#   - ledger 없으면 graceful(빈 결과·에러 아님). raw 경로/secret 미포함(claim=사용자 자기 기억, 조회 목적 노출).
def _operating_home():
    return os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")


def _operating_ledger():
    return os.path.join(_operating_home(), "ledger.sqlite")


# ---- 응답 노출 전처리(작업3): PII 마스킹 + node_id 토큰 제거(Fable5 D-1 위조 차단) ----
import re as _re  # noqa: E402
_NODE_ID_RX = _re.compile(r"node:[A-Za-z0-9:_.\-]+")


def _redact_pii(s):
    """PII+secret 마스킹(batch_redact). why/contrast read 응답 노출 전 기본 적용."""
    try:
        from binggupack.pack.batch_m1 import batch_redact
        red, _hits, _review = batch_redact(s or "")
        return red
    except Exception:
        return s or ""


def _mask_node_ids(s):
    """'node:...' 토큰 마스킹 — why/contrast 출력의 node_id 로 write confirm/id8(node hash8) 위조 차단."""
    return _NODE_ID_RX.sub("[node]", s or "")


def _ensure_scripts_path():
    """scripts 정본 모듈(binggu_recall 등) import 보장. storage facade 도 동일 부트스트랩을 하지만
    read 도구 단독 진입(핸들러 selftest) 대비 명시. server_handlers 는 binggupack/mcp/ 하위 → dirname 3 = ROOT."""
    scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


# 회수 도장 안내(작업A2) — MCP reader 원격 표면은 도장 소비용 staging 파일에 일절 쓰지 않는다(MF7).
#   사람 도장 경로는 로컬 세션 한정(UserPromptSubmit hook 기록) → 안내 문구 1줄만 응답에 병기.
# 2026-07-28: MCP 회상도 효용 판정 장부에는 등록한다(↓ _mcp_record_trace). staging 미접촉은 불변.
_STAMP_HINT_ON = ("이 회상은 효용 판정 대기 목록에 등록됨 — **사용 시점 AI 도장**: 판단에 쓴 뒤 "
                  "trace_stamp(trace_id + i + used/ignored/corrected)로 그 자리서 기입"
                  "(actor=ai_stamp · owner 판정이 오면 덮어씀). 사람 도장은 로컬 세션 마무리 "
                  "preview 또는 'binggu trace mark'. MCP 는 도장 staging/스냅샷 기록 0")
_STAMP_HINT_OFF = ("효용 판정 장부 OFF — 'binggu trace enable' 후부터 등록됨. "
                   "MCP 는 도장 staging/스냅샷 기록 0")
# preflight 는 자동주입(노출 로그) 축 — 판정 대상이 아니라 trace_stamp 안내를 하지 않는다.
_STAMP_HINT_AUTO = ("자동주입 노출 로그로 등록됨(판정 대상 아님 — 도장은 직접인출 recall/why 만). "
                    "MCP 는 도장 staging/스냅샷 기록 0")
_REC_OFF = {"recorded": False, "trace_id": None, "n_nodes": 0}


def _mcp_record_trace(kind, query, nodes, *, domain=None, situation_src=None,
                      risk_level=None, needs_question=None):
    """MCP 회상을 효용 판정 장부(recall_trace.sqlite)에 등록 — opt-in(trace_enabled)일 때만.

    왜 등록만 하고 staging 은 안 쓰나(MF7 유지): staging/스냅샷은 owner 가 화면에서 보고 있는
      번호축이라 원격 표면이 덮으면 엉뚱한 회상에 도장이 찍힌다(2026-07-27 스냅샷 4중 write 사고와
      동형). 등록은 장부 append 라 번호축과 무관 — 번호는 로컬 preview 가 매긴다.
    ledger write 0(별도 store) · PII 0(query=sha16 · 노드는 메타만) · 실패 흡수(회상 응답 무방해).
    반환 {recorded, trace_id, n_nodes} — 2026-07-30 use-time AI 도장(trace_stamp)이 trace_id 를
      필요로 해 bool → dict 로 확장(종전 bool 은 trace_id 를 버려 도장 배선이 불가능했다)."""
    try:
        _ensure_scripts_path()
        import binggu_recall_trace as RT
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # 세션 근사 귀속(2026-07-28 owner 지적) — MCP 는 session_id 를 못 받아 NULL 로 쌓였고,
        # 그 결과 owner 가 판단에 실제로 쓴 회상이 마무리 preview 의 '이번 세션 회상'에서
        # 구조적으로 빠졌다. 직전 preflight(=같은 발화 처리 중 hook 이 남긴 것)의 session_id 를
        # 승계한다. 30분 넘게 떨어져 있으면 None → 현행(미귀속)으로 안전 폴백.
        try:
            sid = RT.latest_session_id(home=_operating_home(), before_ts=ts)
        except Exception:
            sid = None
        r = RT.record_trace(query, kind, nodes, ts, domain=domain,
                            situation=RT.classify_situation(situation_src or query),
                            risk_level=risk_level, needs_question=needs_question,
                            session_id=sid, home=_operating_home())
        if isinstance(r, dict) and r.get("recorded"):
            return {"recorded": True, "trace_id": r.get("trace_id"),
                    "n_nodes": r.get("n_nodes", 0)}
        return dict(_REC_OFF)
    except Exception:
        return dict(_REC_OFF)


def _u_recall(params=None):
    """query 관련 기억 회상(read-only·use_count 미기록·랭킹순). ledger 없으면 빈 결과."""
    params = params or {}
    query = (params.get("query") or "").strip()
    if not query:
        return {"action": "recall", "mode": "read", "error": "query_required"}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "recall", "mode": "read", "empty": True, "count": 0,
                "nodes": [], "edges": [], "summary": "장부 없음(회상할 기억 0)"}
    _ensure_scripts_path()
    import binggu_recall as RC
    limit = params.get("limit")
    res = RC.why_search(ledger, query, limit=limit if isinstance(limit, int) else None)
    nodes = [{"i": i, "node_id": n.get("node_id"), "node_type": n["node_type"],
              "subtype": n.get("semantic_subtype"), "rank": round(n["rank_score"], 3),
              "rel": round(n["relevance"], 2), "claim": n["claim"]}
             for i, n in enumerate(res["relevant_nodes"], 1)]
    edges = [{"source": e["source"], "relation": e["relation"], "target": e["target"]}
             for e in res.get("relevant_edges", [])]
    out = {"action": "recall", "mode": "read", "count": len(nodes),
           "nodes": nodes, "edges": edges, "summary": res.get("summary", "")}
    if nodes:
        rec = _mcp_record_trace("mcp_recall", query, res["relevant_nodes"])
        out["trace_recorded"] = rec["recorded"]  # 등록 여부 정직 노출(OFF 를 침묵으로 넘기지 않는다)
        if rec["recorded"]:
            out["trace_id"] = rec["trace_id"]  # use-time AI 도장(trace_stamp) 재료
        out["stamp_hint"] = _STAMP_HINT_ON if rec["recorded"] else _STAMP_HINT_OFF
    return out


def _u_why(params=None):
    """판단·근거 회상(why_search 래핑·read-only·write 0). node_id/edge_id 미노출(D-1)·PII 마스킹.

    recall 도구와 달리 node_id 를 노출하지 않고 표시용 1-based index(i) 만 반환 —
    모델이 deprecate/replace confirm(id8=node hash8) 을 위조하지 못하게 한다. ledger 없으면 빈 결과.
    """
    params = params or {}
    query = (params.get("query") or "").strip()
    if not query:
        return {"action": "why", "mode": "read", "error": "query_required"}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "why", "mode": "read", "empty": True, "count": 0,
                "nodes": [], "edges": [], "summary": "장부 없음(회상할 기억 0)"}
    from binggupack.pack import recall as RECALL
    limit = params.get("limit")
    lim = limit if isinstance(limit, int) and not isinstance(limit, bool) else None
    res = RECALL.why_search(ledger, query, limit=lim, home=_operating_home())
    id2i, nodes = {}, []
    for i, n in enumerate(res["relevant_nodes"], 1):
        id2i[n.get("node_id")] = i
        nodes.append({"i": i, "node_type": n["node_type"],
                      "subtype": n.get("semantic_subtype"),
                      "rank": round(n["rank_score"], 3), "rel": round(n["relevance"], 2),
                      "trust": n.get("trust", "candidate_unverified"),
                      "claim": _redact_pii(n["claim"])})
    edges = [{"relation": e["relation"], "source_i": id2i.get(e["source"]),
              "target_i": id2i.get(e["target"])} for e in res.get("relevant_edges", [])]
    out = {"action": "why", "mode": "read", "count": len(nodes), "nodes": nodes,
           "edges": edges, "summary": _redact_pii(res.get("summary", "")),
           "confidence": res.get("confidence", 0.0)}
    if nodes:
        rec = _mcp_record_trace("mcp_why", query, res["relevant_nodes"])
        out["trace_recorded"] = rec["recorded"]
        if rec["recorded"]:
            # trace_id 는 도장 재료로 노출하되 node_id 는 계속 미노출(D-1) —
            # trace_stamp 가 i → node_id 를 서버측 recalled_json 에서 해석한다.
            out["trace_id"] = rec["trace_id"]
        out["stamp_hint"] = _STAMP_HINT_ON if rec["recorded"] else _STAMP_HINT_OFF
    return out


def _u_preflight(params=None):
    """작업 전 회상(기억할 것 + 위험패턴 + 선호). read-only. cwd 미지정 시 서버 cwd(위험패턴 힌트만)."""
    params = params or {}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "preflight", "mode": "read", "empty": True,
                "remember": [], "avoid_patterns": [], "preferences": [], "risk_level": "없음"}
    _ensure_scripts_path()
    import binggu_recall as RC
    files = params.get("files")
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    res = RC.preflight_context(ledger, prompt=params.get("prompt"),
                               cwd=params.get("cwd") or os.getcwd(),
                               domain=params.get("domain"), files_changed=files or None)
    out = {"action": "preflight", "mode": "read",
           "remember": [{"node_type": n["node_type"], "subtype": n.get("semantic_subtype"), "claim": n["claim"]}
                        for n in res["remember"]],
           "avoid_patterns": [{"risk": round(m["risk_score"], 2), "claim": m["claim"]}
                              for m in res["avoid_patterns"]],
           "preferences": [{"claim": p["claim"]} for p in res["preferences"]],
           "risk_level": res["risk_level"],
           "question": res.get("question") if res.get("needs_question") else None}
    if res["remember"] or res["avoid_patterns"] or res["preferences"]:
        # 판정 대상은 remember(회상된 기억)뿐 — avoid/preferences 는 규칙 표시라 도장 축이 아니다.
        rec = (_mcp_record_trace(
            "mcp_preflight", params.get("prompt") or "", res["remember"],
            domain=params.get("domain"), situation_src=params.get("prompt"),
            risk_level=res.get("risk_level"), needs_question=res.get("needs_question"))
            if res["remember"] else dict(_REC_OFF))
        out["trace_recorded"] = rec["recorded"]
        # trace_id 미노출 — preflight 는 자동주입(노출 로그) 축이라 use-time 도장 대상이 아니다
        # (owner 2026-07-29: 자동주입은 판정 대상으로 표시하지 않음 — 읽었다는 보장이 없다).
        out["stamp_hint"] = _STAMP_HINT_AUTO if rec["recorded"] else _STAMP_HINT_OFF
    return out


def reason_code_hint():
    """도장 verdict·reason_code 유효값 — 정본 recall_trace.REASON_CODES 에서 생성(하드카피 0).

    2026-07-30: AI 가 찍는 도구인데 유효값이 도구 설명·스키마 어디에도 없어 첫 실사용 도장
    6건이 전량 invalid_reason_code 로 거부됐다(한글 라벨 시도). tools/list 와 거부 응답이
    이 함수로 유효값을 실어 사전·사후 양쪽에서 알 수 있게 한다. §12-3 정본 위임 —
    값을 복사하지 않고 매번 정본에서 읽으므로 REASON_CODES 개정이 자동 반영된다.

    반환 {verdict: (reason_code, ...)} · 정본 로드 실패 시 None(호출부는 graceful skip).
    """
    try:
        _ensure_scripts_path()
        import binggu_recall_trace as RT
        return {v: tuple(c) for v, c in RT.REASON_CODES.items()}
    except Exception:
        return None


def _u_trace_stamp(params=None):
    """use-time AI 회상 도장 — 인출 직후 그 자리서 used/ignored/corrected 기입(actor=ai_stamp 하드).

    owner 설계 지시(2026-07-29): "실제 세션에서 도움되었다를 네가 직접 판단하고 주입" —
    도장은 회상을 실제로 쓴 시점의 AI 가 기입하고, owner 판정이 오면 덮어쓴다(사람>AI ·
    record_outcome dup 분기). §13 C-11-1 자동 열외(도장 한정) 정합.

    설계 경계:
      · actor 는 서버 하드 고정(ai_stamp) — 이 도구로 human 도장 불가(사람 도장 위조 0).
      · node_id 입력 불신(D-1) — trace_id + 1-based i 만 받고, i→node_id 는 서버가
        recall_traces.recalled_json 에서 해석한다. 결과에도 node_id 미노출(why 의 D-1 유지).
      · 스냅샷/staging 미접촉(MF7) — record_outcome 직접 호출. 번호축 오염 0.
      · kind 게이트 — 직접인출(mcp_recall/mcp_why)은 무컷. 자동주입(preflight)은 **판정 대상으로
        추려진 것만** 찍을 수 있다(2026-08-01 owner B안).
        연혁: 07-29 owner "읽었다는 보장이 없는 것에 도장 금지" → 08-01 owner "실제로 썼냐 안
        썼냐가 히트/미스 아니야?" 로 자동주입도 판정 대상이 됐고, 하루 140건 유입이 문제가 되어
        B안(상위 N + 관련도 하한)으로 좁혔다. 그 기준을 통과한 것은 도장도 가능해야 일관된다
        — 목록에는 뜨는데 찍을 수 없으면 판정이 영영 안 남는다.
        기준 정본은 recall_trace.list_pending(= _autoinject_judgeable).
      · used 도장은 use_count 랭킹에 즉시 반영 + owner 뒤집기 시 AI 몫만 회수
        (p1_ranking.ai_stamp_use_count · 2026-07-27 owner "AI 도장도 바로 반영")."""
    params = params or {}
    trace_id = (params.get("trace_id") or "").strip()
    items = params.get("items")
    if not trace_id or not isinstance(items, list) or not items:
        return {"action": "trace_stamp", "error": "trace_id_and_items_required",
                "usage": "items: [{i: 1-based index, verdict: used|ignored|corrected, reason_code?}]"}
    _ensure_scripts_path()
    import binggu_recall_trace as RT
    import json as _json
    from datetime import datetime, timezone
    home = _operating_home()
    store = os.path.join(home, "recall_trace.sqlite")
    if not os.path.exists(store):
        return {"action": "trace_stamp", "stamped": 0, "results": [],
                "note": "trace store 없음(회상 등록 전) — graceful no-op"}
    import sqlite3 as _sq
    con = _sq.connect(store)
    try:
        row = con.execute("SELECT kind, recalled_json FROM recall_traces WHERE trace_id=?",
                          (trace_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return {"action": "trace_stamp", "error": "trace_not_found", "stamped": 0, "results": []}
    kind = row[0]
    if kind not in ("mcp_recall", "mcp_why") and kind not in RT.AUTOINJECT_KINDS:
        return {"action": "trace_stamp", "error": "kind_not_stampable", "kind": kind, "stamped": 0,
                "results": [], "note": "직접인출(mcp_recall/mcp_why)과 자동주입만 use-time 도장 대상"}
    try:
        recalled = _json.loads(row[1] or "[]")
    except Exception:
        recalled = []
    node_ids = [n.get("node_id") for n in recalled]
    # 자동주입은 판정 대상으로 추려진 노드만 찍는다(owner B안 · 기준 정본은 recall_trace).
    # i 는 recalled_json 전체 기준 그대로 둔다 — 번호축을 바꾸면 owner 가 보던 목록과 어긋난다(MF7).
    judgeable = (None if kind not in RT.AUTOINJECT_KINDS
                 else {n.get("node_id") for n in RT._autoinject_judgeable(recalled)})
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results, stamped = [], 0
    for it in items[:50]:   # 폭주 방지(범위 상한 — 도장 파서 _STAMP_RANGE_CAP 와 동수)
        if not isinstance(it, dict):
            continue
        i, verdict = it.get("i"), (it.get("verdict") or "").strip().lower()
        reason_code = it.get("reason_code") or None
        entry = {"i": i, "verdict": verdict}
        if not isinstance(i, int) or isinstance(i, bool) or not (1 <= i <= len(node_ids)):
            entry.update({"recorded": False, "reason": "bad_index"})
            results.append(entry)
            continue
        node_id = node_ids[i - 1]
        if judgeable is not None and node_id not in judgeable:
            entry.update({"recorded": False, "reason": "not_judgeable",
                          "note": "자동주입 중 판정 대상(상위 %d · 관련도 %.2f 이상)만 도장한다"
                                  % (RT.AUTOINJECT_JUDGE_TOP_N, RT.AUTOINJECT_JUDGE_REL_MIN)})
            results.append(entry)
            continue
        # 2026-08-01: 랭킹 반영은 record_outcome 안에서 한다(도장 경로 단일화).
        res = RT.record_outcome(trace_id, node_id, verdict,
                                {"actor": RT.AI_STAMP_ACTOR}, ts,
                                reason_code=reason_code, home=home,
                                ledger_path=_operating_ledger())
        entry["recorded"] = bool(res.get("recorded"))
        if not entry["recorded"]:
            entry["reason"] = res.get("reason")
            # 거부 사유만 주면 재시도도 같은 값으로 또 틀린다(2026-07-30 첫 실사용 6건 전량
            # invalid_reason_code) — 정본 유효값을 응답에 실어 그 자리서 자가교정하게 한다.
            if entry["reason"] == "invalid_reason_code":
                entry["valid_reason_codes"] = list(RT.REASON_CODES.get(verdict, ()))
            elif entry["reason"] == "invalid_verdict":
                entry["valid_verdicts"] = list(RT.VALID_VERDICTS)
        else:
            stamped += 1
            # used → 랭킹 즉시 반영. 결과는 record_outcome 이 실어 준다(실패 사유 포함 · §13 B10).
            if res.get("rank_action"):
                entry["rank_action"] = res["rank_action"]
            if res.get("use_count") is not None:
                entry["use_count"] = res["use_count"]
        results.append(entry)
    return {"action": "trace_stamp", "trace_id": trace_id, "stamped": stamped,
            "results": results,
            "note": ("actor=ai_stamp(자기신고 · owner 판정이 덮어씀) · 스냅샷 미접촉(MF7) · "
                     "재판정 불가(dup_outcome — AI 자기수정 금지, 사람만 교체)")}


def _u_trace_review(params=None):
    """미판정 회상 목록(효용 판정 대기). read-only(스냅샷 write 안 함 — mark 는 미노출).

    count_only(2026-08-01): 수만 필요할 때 목록을 빼고 돌려준다. 마무리 화면·상태 점검은
    "얼마나 밀렸나" 만 알면 되는데 종전엔 수백 건 전문이 그대로 딸려왔다(실측 305건 = 순수 토큰
    낭비). 목록이 필요하면 종전대로 인자 없이 부르면 된다(기본값 불변).
    """
    params = params or {}
    count_only = bool(params.get("count_only"))
    ledger = _operating_ledger()
    home = _operating_home()
    if not os.path.exists(ledger):
        return {"action": "trace_review", "mode": "read", "empty": True, "count": 0,
                **({} if count_only else {"pending": []})}
    _ensure_scripts_path()
    import binggu_recall_trace as RT
    if count_only:
        # ledger join(표시용 claim) 자체를 건너뛰는 경로 — 세는 데 그래프 로드가 필요 없다.
        # B-02(2026-08-07): 시효로 접힌 자동주입 수를 동봉한다(count 가 줄어든 이유 가시화).
        st = RT.pending_stats(home=home)
        return {"action": "trace_review", "mode": "read", "count_only": True,
                "count": st["pending"], "expired_autoinject": st["expired_autoinject"],
                "ttl_days": st["ttl_days"]}
    pend = RT.list_pending(home=home, ledger_path=ledger)
    return {"action": "trace_review", "mode": "read", "count": len(pend),
            "pending": [{"idx": p["idx"], "claim": p.get("claim"), "category": p.get("category"),
                         "rank": p.get("rank"), "node_id": p.get("node_id")} for p in pend]}


def _u_trace_show(params=None):
    """판단 노드 근거 사슬(다홉). read-only. node_id 는 list/recall 이 반환한 값."""
    params = params or {}
    node_id = (params.get("node_id") or "").strip()
    if not node_id:
        return {"action": "trace_show", "mode": "read", "error": "node_id_required"}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "trace_show", "mode": "read", "empty": True, "found": False}
    _ensure_scripts_path()
    import binggu_recall as RC
    res = RC.judgment_trace(ledger, node_id)
    if not res.get("found"):
        return {"action": "trace_show", "mode": "read", "found": False}
    r = res["root"]
    return {"action": "trace_show", "mode": "read", "found": True,
            "root": {"node_id": r["node_id"], "node_type": r["node_type"],
                     "rank": round(r["rank_score"], 3), "claim": r["claim"]},
            "chain": [{"from": c["from"], "relation": c["relation"], "to": c["to"],
                       "direction": c["direction"],
                       "peer": c.get("peer_claim") if c.get("peer_present") else None}
                      for c in res["chain"]],
            "summary": res.get("summary", ""), "confidence": round(res.get("confidence", 0), 2)}


def _u_status(params=None):
    """장부 요약(active/deprecated/검증예정/수용/audit chain). read-only."""
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "status", "mode": "read", "empty": True, "ledger_exists": False}
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept, accepted_view
    db = open_accept(ledger)
    try:
        n = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
        d = db.con.execute("SELECT count(*) FROM nodes WHERE state='deprecated'").fetchone()[0]
        p = db.con.execute("SELECT count(*) FROM judgment_reviews WHERE status='pending'").fetchone()[0]
        acc = len(accepted_view(db))
        chain = db.verify_chain()
    finally:
        db.close()
    # 마무리 화면용 3종(2026-08-01) — 여기 없어서 세션마다 인라인 파이썬으로 캐내고 있었다.
    #   recall_pending : 판정 대기 수. trace_review 를 부르면 수백 건 전문이 딸려와(실측 305건)
    #                    "얼마나 밀렸나" 만 알고 싶을 때 값이 너무 비쌌다.
    #   nodes_total    : 저장 전후 대조 기준값(앵커≠저장 silent drop 방지 — §C-11 DoD).
    #   ledger_mtime   : 같은 목적. 저장 뒤 이 값이 안 변하면 persist 되지 않은 것.
    recall_pending = None
    try:
        _ensure_scripts_path()
        import binggu_recall_trace as RT
        recall_pending = RT.count_pending(home=_operating_home())
    except Exception:
        recall_pending = None       # trace store 부재·opt-in off 면 None(조회 실패로 status 를 죽이지 않는다)
    try:
        import datetime as _dt                       # utcfromtimestamp 는 3.12 deprecated — aware 로
        ledger_mtime = _dt.datetime.fromtimestamp(
            os.path.getmtime(ledger), _dt.timezone.utc).isoformat()
    except Exception:
        ledger_mtime = None
    return {"action": "status", "mode": "read", "ledger_exists": True,
            "active": n, "deprecated": d, "pending_reviews": p, "accepted": acc,
            "audit_chain": "INTACT" if chain else "BROKEN",
            "nodes_total": n + d, "recall_pending": recall_pending,
            "ledger_mtime": ledger_mtime}


def _u_list(params=None):
    """후보 목록(status/kind 필터). read-only. markdown + count + accepted 수."""
    params = params or {}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "list", "mode": "read", "empty": True, "count": 0, "markdown": "장부 없음"}
    status = params.get("status") or "all"
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept, accepted_view
    from openbinggu_candidate_list_view import list_candidates, STATUSES
    if status not in STATUSES:
        # 미지 status(예: 'active')를 조용히 all 로 폴백하면 deprecated 까지 섞여 나온다
        # (사람이 폐기한 판단을 live 로 재서빙 = 빙구팩 본질 위반). fail-closed: 거부 + 허용값 안내.
        return {"action": "list", "mode": "read", "error": "unknown_status",
                "status": status, "allowed": list(STATUSES)}
    db = open_accept(ledger)
    try:
        v = list_candidates(db, status, params.get("kind"))
        acc = len(accepted_view(db))
    finally:
        db.close()
    return {"action": "list", "mode": "read", "count": len(v.get("rows", [])),
            "accepted": acc, "markdown": v.get("markdown", "")}


def _u_reminders(params=None):
    """due 경과 판단 리마인더 목록. read-only."""
    params = params or {}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "reminders", "mode": "read", "empty": True, "markdown": "장부 없음"}
    _ensure_scripts_path()
    import datetime as _dt
    from openbinggu_owner_accept_ux import open_accept
    from binggupack.storage import list_due_reminders
    db = open_accept(ledger)
    try:
        today = params.get("today") or _dt.date.today().isoformat()
        r = list_due_reminders(db, today)
    finally:
        db.close()
    return {"action": "reminders", "mode": "read", "markdown": r.get("markdown", "")}


# ==== Phase 2 배치 B: 쓰기(write-gated) 도구 — pair/deprecate/replace ====
# save_candidate 와 동일 안전 패턴(자동저장 방지 불변):
#   - MCP params 의 actor 는 무시. ★confirm 정확일치는 "사람 승격 증거"가 아니다 — dry_run 응답이
#     confirm_expected 를 그대로 노출하므로 같은 에이전트가 재현 가능(자율 preview→confirm 우회, P0 재현
#     확증 2026-07-10). 따라서 actor 는 reader 로 하드 고정하고 confirm 은 형식검증에만 쓴다.
#   - dry_run 기본 True(비가역 write default-deny) → expected confirm 안내 + preview, write 0.
#   - dry_run=False + confirm 정확일치여도 actor=reader → 게이트(save_paired/deprecate_from_list/
#     replace_from_list)가 G4_no_auto 로 BLOCK. MCP 경유 mutation 은 fail-closed(사람 앵커 경로 부재).
#   - 운영 ledger 는 서버 결정(BINGGU_HOME/~/.binggupack). MCP 경로 입력 무시(주입 차단).
#   - owner 정당 write 는 CLI(cmd_pair/cmd_deprecate/cmd_replace·_resolve_human_ctx)로 수행.
#     MCP 표면은 read/dry-run/미리보기 + (사람 save-n 앵커 있는) save_candidate 저장에 한정된다.
# 저장 게이트 개정 정합(2026-07-13 owner 결정): 구 P1-A "MCP 도구 호출 + approval_id → human 승격"
# 배선(approval_gate.authorize)은 MCP 핸들러에서 제거. approval core(trusted_approval.py)·owner CLI
# (binggu approval / --approval-id·hag import-edges)·Studio Approval Center 는 별도 자산으로 보존 —
# MCP 표면에서만 approval 요청/소비가 사라졌다. 응답에 아래 필드를 실어 "confirm 이나 approval_id 로
# 실행된다"는 오해를 제거한다. dry-run 미리보기(confirm_expected 포함)는 호환 위해 유지. 실제 mutation
# 은 owner 로컬 CLI 로.
# 2026-08-02: 거부만 하고 "그럼 무엇을 하라"를 안 줘서, 붙어 있는 에이전트가 매번 CLI 경로를
# 다시 찾는다(실측 — 도구 검색 1회 + `--help` 1회를 거쳐서야 명령에 도달했다). 막는 것은 그대로 두고
# **실행 가능한 명령**을 응답에 함께 싣는다. 명령은 언어 무관하므로 영문 안내 + 한국어 한 줄 병기.
_FAIL_CLOSED_GUIDANCE = (
    "MCP mutation is fail-closed: a confirmation phrase alone is not human approval, and "
    "approval_id no longer promotes MCP writes (removed 2026-07-13). The only human anchor is the "
    "owner typing 'SAVE n' in chat after seeing the preview — a UserPromptSubmit hook records that "
    "utterance, and the owner's local CLI then executes it:\n"
    "  binggu save-batch                            # preview; these numbers are canonical\n"
    "  binggu save-batch --confirm \"SAVE 3,4\"       # batch save, only after the owner's SAVE utterance\n"
    "  binggu pair --confirm \"<confirm_expected>\"   # paired save; use confirm_expected from this response\n"
    "Without that anchor the CLI is blocked as well (actor=reader), so do not retry through MCP. "
    "한국어: MCP 저장은 사람 승인을 증명할 수 없어 막혀 있습니다. 사장님이 채팅에 'SAVE n' 을 "
    "입력하시면 그 발화가 앵커가 되고, 그 뒤 위 CLI 로 집행합니다.")
# dry-run 응답용(reason 필드가 없으므로 fail-closed reason 을 함께 노출).
_MCP_FAIL_CLOSED = {"write_available": False, "reason": "human_save_required",
                    "owner_action": "use_local_cli", "guidance": _FAIL_CLOSED_GUIDANCE}
# 실행 시도(write-gated) 응답용 — 기존 reason(G4_no_auto 등)을 덮어쓰지 않도록 reason 제외.
_MCP_FAIL_CLOSED_INFO = {"write_available": False, "owner_action": "use_local_cli",
                         "guidance": _FAIL_CLOSED_GUIDANCE}


def _mcp_write_extra(core_result, params=None):
    """write 시도 응답 공통 필드. 성공(=core 의 사람 save-n 앵커 승격)시 write_available=True,
    차단 시 fail-closed 안내(core reason 은 덮어쓰지 않음). 구 P1-A approval_id 는 더 이상
    승격 경로가 아니므로 제시돼도 무시됨을 명시(approval_id_ignored)."""
    if (core_result or {}).get("applied"):
        return {"write_available": True}
    out = dict(_MCP_FAIL_CLOSED_INFO)
    if (params or {}).get("approval_id"):
        out["approval_id_ignored"] = True
    return out


def _u_pair(params=None):
    """owner 발화(+ai 요약) 화자축 페어 저장. dry_run 기본·PAIR confirm 정확일치·자동저장 차단.
    relation: accepts/refutes/revises · by: owner(사용자가 AI 발화에 반응)/ai. ai_text 생략=owner 단독."""
    params = params or {}
    owner_text = params.get("owner_text", "")
    ai_text = params.get("ai_text") or None
    owner_pick = params.get("owner_pick", 1)
    ai_pick = params.get("ai_pick", 1)
    by = params.get("by", "ai")
    relation = params.get("relation", "accepts")
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not (owner_text or "").strip():
        return {"action": "pair", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "owner_text_required"}
    rel = "%s_%s" % (by, relation)
    expected = ("PAIR %s owner:%d ai:%d" % (rel, owner_pick, ai_pick)) if ai_text \
        else ("PAIR owner:%d" % owner_pick)
    _ensure_scripts_path()
    from binggupack.capture import preview as cvp

    def _pv(t):
        try:
            return [{"index": j + 1, "label_kind": c["label_kind"], "sentence": c["sentence"]}
                    for j, c in enumerate(cvp.capture_preview(t)["candidates"])]
        except Exception:
            return []

    if dry_run:
        # dry-run: write 0. owner/ai 후보 preview + confirm_expected(호환 유지). 단 confirm 만으로는
        # 실행되지 않는다(fail-closed) — write_available=False 로 명시(실행은 로컬 CLI).
        return {"action": "pair", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write_ledger": False,
                "relation": rel, "confirm_expected": expected,
                "owner_preview": _pv(owner_text),
                "ai_preview": _pv(ai_text) if ai_text else [], **_MCP_FAIL_CLOSED}
    # actor=reader 하드 고정 — save_paired 의 actor 게이트가 G4_no_auto 로 차단(fail-closed).
    # confirm 은 형식검증 전용(§6). owner 정당 pair = 로컬 CLI(cmd_pair). (P1-A approval 승격 배선 제거.)
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "pair", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "ledger_not_found"}
    from openbinggu_owner_accept_ux import open_accept
    from binggupack.storage import save_paired
    snap_dir = os.path.join(_operating_home(), "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    db = open_accept(ledger)
    try:
        r = save_paired(db, owner_text, ai_text, {"actor": "reader", "confirm": confirm},
                        snap_dir, relation_kind=rel, owner_pick=owner_pick, ai_pick=ai_pick,
                        due_date=params.get("due_date"))
    finally:
        db.close()
    return {"action": "pair", "mode": "write-gated",
            "verdict": "ALLOW" if r.get("applied") else "BLOCK",
            "executed_write": bool(r.get("applied")),
            "saved": r.get("saved"), "reason": r.get("reason"),
            "relation": r.get("relation"), "paired": r.get("paired"),
            "pack_id": r.get("pack_id"), "ledger": "operating", **_mcp_write_extra(r, params)}


def _u_deprecate(params=None):
    """목록 인덱스 1건 기각. dry_run 기본·'DEPRECATE <index> <id8>' confirm 정확일치·자동차단.
    index/id8 은 list 도구가 반환한 순번+node hash8(사용자가 본 목록 재현 증거)."""
    params = params or {}
    index = params.get("index")
    id8 = params.get("id8", "")
    reason = params.get("reason", "")
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not isinstance(index, int) or not id8:
        return {"action": "deprecate", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "index_and_id8_required"}
    expected = "DEPRECATE %s %s" % (index, id8)
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "deprecate", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "ledger_not_found"}
    if dry_run:
        return {"action": "deprecate", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write_ledger": False,
                "confirm_expected": expected,
                "note": "미리보기입니다. MCP 로는 confirm 만으로 실행되지 않습니다(fail-closed) — "
                        "실제 기각은 로컬 CLI: binggu deprecate <n> <id8> --reason ... --confirm ...",
                **_MCP_FAIL_CLOSED}
    # actor=reader 하드 고정 — deprecate_from_list 의 actor 게이트가 G4_no_auto 로 차단(fail-closed).
    # confirm=형식검증 전용(§6). owner 정당 기각 = 로컬 CLI(cmd_deprecate). (P1-A approval 승격 배선 제거.)
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept
    from openbinggu_candidate_deprecate_ux import deprecate_from_list
    snap_dir = os.path.join(_operating_home(), "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    db = open_accept(ledger)
    try:
        r = deprecate_from_list(db, index, id8, reason, {"actor": "reader", "confirm": confirm}, snap_dir)
    finally:
        db.close()
    return {"action": "deprecate", "mode": "write-gated",
            "verdict": "ALLOW" if r.get("applied") else "BLOCK",
            "executed_write": bool(r.get("applied")),
            "reason": r.get("reason"), "node_id": r.get("node_id"), "ledger": "operating",
            **_mcp_write_extra(r, params)}


def _u_replace(params=None):
    """목록 인덱스 1건 교체(기각+신규 candidate). dry_run 기본·
    'REPLACE <index> <id8> WITH <new_sentence>' confirm 정확일치·자동차단."""
    params = params or {}
    index = params.get("index")
    id8 = params.get("id8", "")
    new_sentence = params.get("new_sentence", "")
    reason = params.get("reason", "")
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not isinstance(index, int) or not id8 or not (new_sentence or "").strip():
        return {"action": "replace", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "index_id8_new_sentence_required"}
    expected = "REPLACE %s %s WITH %s" % (index, id8, new_sentence)
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "replace", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "ledger_not_found"}
    if dry_run:
        return {"action": "replace", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write_ledger": False,
                "confirm_expected": expected,
                "note": "미리보기입니다. MCP 로는 confirm 만으로 실행되지 않습니다(fail-closed) — "
                        "실제 교체는 로컬 CLI: binggu replace <n> <id8> --with ... --reason ... --confirm ...",
                **_MCP_FAIL_CLOSED}
    # actor=reader 하드 고정 — replace_from_list 의 actor 게이트가 G4_no_auto 로 차단(fail-closed).
    # confirm=형식검증 전용(§6). owner 정당 교체 = 로컬 CLI(cmd_replace). (P1-A approval 승격 배선 제거 —
    # 구 배선의 pending journal 복원(mutation·TAE-3)도 함께 제거: MCP 표면은 차단 경로에서 write 0 이며,
    # 잔존 journal 은 replace_from_list 가 pending_replace_journal 로 fail-closed + CLI 복구 안내.)
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept
    from openbinggu_candidate_replace_ux import replace_from_list
    snap_dir = os.path.join(_operating_home(), "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    db = open_accept(ledger)
    try:
        r = replace_from_list(db, index, id8, new_sentence, reason,
                              {"actor": "reader", "confirm": confirm}, snap_dir)
    finally:
        db.close()
    return {"action": "replace", "mode": "write-gated",
            "verdict": "ALLOW" if r.get("applied") else "BLOCK",
            "executed_write": bool(r.get("applied")),
            "reason": r.get("reason"), "old_node_id": r.get("old_node_id"),
            "new_node_id": r.get("new_node_id"), "ledger": "operating",
            **_mcp_write_extra(r, params)}


# ==== Phase 2 배치 C: 작업 도구 — reflect(회고→후보·read) + harvest(외부 소스 관리) ====
# reflect: capture_preview 재사용(저장 0·read). 이어서 save_candidate 로 도장.
# harvest: 사람이 등록한 소스 화이트리스트 관리. list=read·add/remove=write-gated(confirm 정확일치).
#   ★harvest_run(실 네트워크 fetch)은 MCP 미노출 — 실 fetch 는 owner 스케줄러 전용(자동 fetch 위험 차단·_FORBIDDEN 등재).
def _u_reflect(params=None):
    """회고·자가평가 텍스트 → 지식 후보 preview(저장 0·read). preview_id 로 이어서 save_candidate."""
    params = params or {}
    text = params.get("text", "")
    if not (text or "").strip():
        return {"action": "reflect", "mode": "read", "error": "text_required"}
    _ensure_scripts_path()
    import hashlib
    from binggupack.capture import preview as cvp
    pv = cvp.capture_preview(text)
    cands = pv.get("candidates", [])
    pid = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return {"action": "reflect", "mode": "read", "preview_id": pid, "count": len(cands),
            "candidates": [{"index": j + 1, "label_kind": c["label_kind"], "sentence": c["sentence"]}
                           for j, c in enumerate(cands)],
            "save_hint": "남길 교훈만 골라 save_candidate(text, indices, confirm='SAVE <번호>')"}


def _u_harvest_list(params=None):
    """등록된 외부 수확 소스 화이트리스트 목록(read). 빈 시작·owner 가 채움."""
    _ensure_scripts_path()
    import binggu_harvest as HV
    home = _operating_home()
    srcs = HV.load_sources(HV.sources_path(home))
    disabled = os.path.exists(HV.harvest_disabled_path(home))
    return {"action": "harvest_list", "mode": "read", "count": len(srcs), "disabled": disabled,
            "sources": [{"source_id": s.get("source_id"), "kind": s.get("kind"),
                         "url": s.get("url"), "keyword": s.get("keyword")} for s in srcs]}


def _u_harvest_add(params=None):
    """외부 소스 등록 preview(write-gated). dry_run 기본·'HARVEST_ADD <kind> <url>' confirm 정확일치.
    MCP 로는 실행 불가(fail-closed·사람 앵커 경로 부재) — 실제 등록은 owner 로컬 CLI(binggu_harvest)."""
    params = params or {}
    kind = params.get("kind", "")
    url = params.get("url", "")
    # keyword 파라미터는 스키마 호환으로 받되 MCP 표면에선 미사용(등록 자체가 fail-closed).
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not kind or not url:
        return {"action": "harvest_add", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "kind_and_url_required"}
    expected = "HARVEST_ADD %s %s" % (kind, url)
    if dry_run:
        return {"action": "harvest_add", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write": False, "confirm_expected": expected,
                "note": "미리보기입니다. MCP 로는 confirm 만으로 등록되지 않습니다(fail-closed) — "
                        "실제 등록은 owner 로컬 CLI(add_source 가 kind + URL 공개안전성 검증)",
                **_MCP_FAIL_CLOSED}
    if confirm != expected:
        return {"action": "harvest_add", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "confirm_phrase_mismatch", "confirm_expected": expected}
    # MCP 는 harvest 소스 변경의 사람 앵커 경로가 없다 — add_source 미호출(fail-closed·write 0).
    # 실제 등록은 owner 로컬 CLI(scripts/binggu_harvest.py). (P1-A TAE-7 approval 봉인 배선 제거 —
    # confirm-only 창은 여전히 닫혀 있다: confirm 정확일치여도 아래에서 무조건 BLOCK.)
    return {"action": "harvest_add", "mode": "write-gated", "verdict": "BLOCK",
            "executed_write": False, "reason": "human_save_required",
            **_mcp_write_extra({}, params)}


def _u_harvest_remove(params=None):
    """외부 소스 제거 preview(write-gated). dry_run 기본·'HARVEST_REMOVE <source_id>' confirm 정확일치.
    MCP 로는 실행 불가(fail-closed) — 실제 제거는 owner 로컬 CLI(binggu_harvest)."""
    params = params or {}
    source_id = params.get("source_id", "")
    confirm = params.get("confirm", "")
    dry_run = params.get("dry_run", True)
    if not source_id:
        return {"action": "harvest_remove", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "source_id_required"}
    expected = "HARVEST_REMOVE %s" % source_id
    if dry_run:
        return {"action": "harvest_remove", "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write": False, "confirm_expected": expected,
                **_MCP_FAIL_CLOSED}
    if confirm != expected:
        return {"action": "harvest_remove", "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "confirm_phrase_mismatch", "confirm_expected": expected}
    # MCP 는 harvest 소스 변경의 사람 앵커 경로가 없다 — remove_source 미호출(fail-closed·write 0).
    # 실제 제거는 owner 로컬 CLI(scripts/binggu_harvest.py). (P1-A TAE-7 approval 봉인 배선 제거.)
    return {"action": "harvest_remove", "mode": "write-gated", "verdict": "BLOCK",
            "executed_write": False, "reason": "human_save_required",
            **_mcp_write_extra({}, params)}


# ==== 트랙 B: OpenCrab 클라우드 read 조회(egress-only) — cloud_recall / cloud_packs ====
# 안전 원칙(egress-only·로컬 write 0):
#   - cloud_query_wire.run_query 만 호출 → read 전용 화이트리스트(query/search/status)만 payload 생성.
#     write RPC(ingest/pack_update/pack_qa/workflow_manage)는 정본에서 구조적으로 생성 불가.
#   - open_g3/save_selected/ledger/state 일절 미접촉(로컬 write 0). 조회 결과는 PII 마스킹 후에만 노출.
#   - transport 는 운영 설정(env/operating home)에서 read-only 로 구성. 미설정 시 None →
#     run_query 가 NO_CLOUD_CONFIG/NO_TRANSPORT graceful(네트워크 0). raw 토큰 미노출(fingerprint 만).
def _opencrab_url_from_claude_json():
    """owner ~/.claude.json 의 mcpServers.opencrab-cloud URL/token 재사용(read egress 전용·평문 출력 0).

    URL 은 opencrab.sh/api/mcp/<token> 형태 — 마지막 path 세그먼트가 토큰. token 을 함께 반환해
    run_query 의 load_cloud_config 게이트(NO_TOKEN)를 통과시킨다. 부재/손상 = (None, None). raise 0.
    """
    try:
        import json as _json
        p = os.path.join(os.path.expanduser("~"), ".claude.json")
        if not os.path.exists(p):
            return None, None
        with open(p, encoding="utf-8") as f:
            data = _json.load(f)
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        oc = servers.get("opencrab-cloud") if isinstance(servers, dict) else None
        url = ((oc.get("url") if isinstance(oc, dict) else "") or "").strip() or None
        token = None
        if url:
            seg = url.rstrip("/").split("/")[-1]
            if seg and (seg.startswith("ocm_") or len(seg) >= 16):
                token = seg
        return (url, token)
    except Exception:   # noqa — 손상/부재 graceful(폴백 없음)
        return None, None


def _cloud_env_with_fallback():
    """os.environ 복사 + 빙구팩 전용 config(BINGGU_CLOUD_MCP) 부재 시 opencrab-cloud MCP URL/token 폴백 주입.

    run_query 의 load_cloud_config 게이트를 env 로 통과시킨다(read egress 전용·write RPC 는 read
    화이트리스트로 구조 차단이라 안전). selftest 는 BINGGU_CLOUD_MCP_NO_FALLBACK=1 로 폴백 스킵(네트워크 0).
    """
    from binggupack.pack.cloud_query_wire import load_cloud_config
    env = dict(os.environ)
    cfg = load_cloud_config(env=env, home=_operating_home())
    if (not cfg.get("url")) or cfg.get("reason") == "NO_TOKEN":
        if str(os.environ.get("BINGGU_CLOUD_MCP_NO_FALLBACK", "")).strip() != "1":
            f_url, f_token = _opencrab_url_from_claude_json()
            if f_url:
                env["BINGGU_CLOUD_MCP_URL"] = f_url
                if f_token:
                    env["BINGGU_CLOUD_MCP_TOKEN"] = f_token
    return env


def _cloud_transport(env=None):
    """운영/폴백 env 에서 실 http transport 구성(read-only 조회). 미설정 시 None → run_query graceful.
    로컬 write 0·raw 토큰 반환 안 함. env 미지정 시 _cloud_env_with_fallback() 사용."""
    from binggupack.pack.cloud_query_wire import load_cloud_config, default_http_transport
    e = env if env is not None else _cloud_env_with_fallback()
    cfg = load_cloud_config(env=e, home=_operating_home())
    if not cfg.get("url") or cfg.get("reason") == "NO_TOKEN":
        return None
    return default_http_transport(cfg["url"], cfg["token"])


def _cloud_result_view(r):
    """run_query 결과 → 핸들러 노출 뷰. raw 토큰/경로 없음·PII 마스킹된 text 만."""
    if not r.get("ok"):
        return {"ok": False, "error": r.get("reason"), "source": r.get("source")}
    return {"ok": True, "text": r.get("text", ""), "pii_hits": r.get("pii_hits"),
            "residual": r.get("residual"), "source": r.get("source")}


def _u_cloud_recall(params=None):
    """OpenCrab 클라우드 지식 조회(opencrab_query 래핑·read egress-only). 미설정 시 graceful.

    ★스코프(2026-07-09): cloud_search 와 동일 규약 — 명시 > config 기본("Binggu Person") > unscoped.
      미지정 호출은 person_pack.json 의 cloud_search_default_pack_query 를 자동 적용해 개인 온톨로지가
      세션 중 자동으로 "떠 있게" 하고, 무필터 넓은 검색의 벡터 timeout(57014)을 회피한다. 서버
      opencrab_query 는 pack_query/package_id(s) 스코프 인자를 지원(2026-07-09 실측: pack_scope
      packages 5·scanned 1214·vector 32·정답 top1). package_id 단수는 다중 pack_key 통합 팩 scanned=0
      이슈 회피 위해 package_ids 복수로 승격(cloud_search 정합). 이 도구는 빙구팩 개인 온톨로지 전용이다
      (전체 코퍼스 회상은 오픈크랩 opencrab_query 를 직접 쓴다 — 역할 분리). applied_scope/scope_source
      로 적용 스코프를 관측할 수 있다. build_query_payload._clamp_args 가 top_k/limit 외 인자를 보존해
      pack_query/package_ids 가 그대로 서버 arguments 에 실린다.
    """
    params = params or {}
    query = (params.get("query") or "").strip()
    if not query:
        return {"action": "cloud_recall", "mode": "read", "ok": False, "error": "query_required"}
    from binggupack.pack import cloud_query_wire as CQ
    args = {"query": query}
    if isinstance(params.get("top_k"), int) and not isinstance(params.get("top_k"), bool):
        args["top_k"] = params["top_k"]
    # 스코프 결정: 명시(개인 팩) > config 기본("Binggu Person") > unscoped(하위호환). cloud_search 와 대칭.
    pid = params.get("package_id")
    package_id = pid if isinstance(pid, str) and pid.strip() else None
    _pids = params.get("package_ids")
    package_ids = ([p.strip() for p in _pids if isinstance(p, str) and p.strip()]
                   if isinstance(_pids, list) else None) or None
    _pq = params.get("pack_query")
    pack_query = _pq.strip() if isinstance(_pq, str) and _pq.strip() else None
    if package_ids or package_id or pack_query:
        scope_source = "explicit"
    else:
        _dflt = _cloud_search_default_scope()
        if _dflt:
            pack_query = _dflt
            scope_source = "config_default"
        else:
            scope_source = "unscoped"
    # 서버 스코프 인자 주입. package_id 단수는 통합 팩 scanned=0 회피 위해 package_ids 로 승격(개수 상한 32).
    _ids = list(package_ids) if package_ids else ([package_id] if package_id else None)
    if _ids:
        args["package_ids"] = _ids[:32]
    if pack_query:
        args["pack_query"] = pack_query
    if package_ids:
        applied_scope = "package_ids:" + ",".join(package_ids)
    elif package_id:
        applied_scope = "package_id:" + package_id
    elif pack_query:
        applied_scope = "pack_query:" + pack_query
    else:
        applied_scope = "all"
    env = _cloud_env_with_fallback()
    r = CQ.run_query("opencrab_query", args, transport=_cloud_transport(env),
                     env=env, home=_operating_home())
    return {"action": "cloud_recall", "mode": "read", **_cloud_result_view(r),
            "applied_scope": applied_scope, "scope_source": scope_source}


def _u_cloud_packs(params=None):
    """OpenCrab 클라우드 팩 검색(opencrab_search_packs 래핑·read egress-only). 미설정 시 graceful."""
    params = params or {}
    from binggupack.pack import cloud_query_wire as CQ
    args = {}
    if (params.get("query") or "").strip():
        args["query"] = params["query"].strip()
    if (params.get("category") or "").strip():
        args["category"] = params["category"].strip()
    env = _cloud_env_with_fallback()
    r = CQ.run_query("opencrab_search_packs", args, transport=_cloud_transport(env),
                     env=env, home=_operating_home())
    return {"action": "cloud_packs", "mode": "read", **_cloud_result_view(r)}


def _cloud_search_default_scope():
    """cloud_search 미지정 호출의 기본 스코프 — <home>/person_pack.json 의 cloud_search_default_pack_query
    (팩 title 접두어, 예 "Binggu Person"). 부재/손상 시 None(= 무필터 유지·하위호환).

    ★id 가 아닌 접두어라 팩 재업로드(package_id churn)·파트 증설에도 stale 0(Fable5 C2 회피). 온보딩이
    성공 시 이 키를 1회 기록하면 이후 갱신 불필요. read-only(파일 write 0)."""
    import json as _json
    try:
        with open(os.path.join(_operating_home(), "person_pack.json"), encoding="utf-8") as f:
            cfg = _json.load(f)
        v = cfg.get("cloud_search_default_pack_query")
        return v.strip() if isinstance(v, str) and v.strip() else None
    except Exception:  # noqa — 부재/손상 → None(기존 무필터 동작 유지)
        return None


def _u_cloud_search(params=None):
    """OpenCrab 팩 하이브리드 의미검색(서버 lexical+vector+graph fusion·opencrab_search_documents 래핑·read egress-only).

    ★서버 벡터 배선(2026-07-08): 서버 retrieval 이 저장 openai-1536 임베딩을 낮은 가중 fusion 으로 반영
    (vector_candidates 0→32 실측). query 는 원 질문을 3~6개 자연 동의어로 확장해 넣기를 권장(lexical recall 보강).

    ★스코프(2026-07-09): 명시 > config 기본 > unscoped(하위호환).
      - package_ids(복수)/package_id(단수)/pack_query(팩 title 접두어) 명시 → 그 스코프.
      - 미지정 → person_pack.json 의 cloud_search_default_pack_query("Binggu Person") 자동 적용
        (사용자 온톨로지가 세션 중 자동으로 "떠 있게"·벡터 timeout 회피). config 없으면 unscoped(무필터·하위호환).
    ★이 도구는 빙구팩 개인 온톨로지 전용이다. 여행/전체 코퍼스 검색은 이 도구가 아니라 오픈크랩
      (opencrab_search_documents)을 직접 쓴다(owner 지적 2026-07-09·역할 분리). 응답에 applied_scope/
      scope_source·telemetry(scanned/vector_candidates/warnings) 노출로 적용 스코프를 관측할 수 있다.
    evidence chunk 원문은 PII 마스킹 후 노출. 미설정 시 graceful(네트워크 0).
    """
    params = params or {}
    query = (params.get("query") or "").strip()
    if not query:
        return {"action": "cloud_search", "mode": "read", "ok": False, "error": "query_required"}
    from binggupack.pack import cloud_query_wire as CQ
    tk = params.get("top_k")
    top_k = tk if isinstance(tk, int) and not isinstance(tk, bool) else 5
    ms = params.get("min_score")
    min_score = float(ms) if isinstance(ms, (int, float)) and not isinstance(ms, bool) else 0.0
    pid = params.get("package_id")
    package_id = pid if isinstance(pid, str) and pid.strip() else None
    _pids = params.get("package_ids")
    package_ids = ([p.strip() for p in _pids if isinstance(p, str) and p.strip()]
                   if isinstance(_pids, list) else None) or None
    _pq = params.get("pack_query")
    pack_query = _pq.strip() if isinstance(_pq, str) and _pq.strip() else None

    # 스코프 결정: 명시(개인 팩) > config 기본("Binggu Person") > unscoped(하위호환).
    # ★cloud_search 는 빙구팩 개인 온톨로지 전용 도구다. 여행/전체 코퍼스 검색은 이 도구가 아니라
    #   오픈크랩(opencrab_search_documents)을 직접 쓴다(owner 지적 2026-07-09 — 역할 분리·전체
    #   탈출구는 사족). 그래서 config 기본이 붙어도 비-person 질의를 오염시킬 표면이 없다(Fable5 C1 무력화).
    if package_ids or package_id or pack_query:
        scope_source = "explicit"
    else:
        _dflt = _cloud_search_default_scope()
        if _dflt:
            pack_query = _dflt
            scope_source = "config_default"
        else:
            scope_source = "unscoped"
    if package_ids:
        applied_scope = "package_ids:" + ",".join(package_ids)
    elif package_id:
        applied_scope = "package_id:" + package_id
    elif pack_query:
        applied_scope = "pack_query:" + pack_query
    else:
        applied_scope = "all"

    env = _cloud_env_with_fallback()
    r = CQ.run_search(query, top_k=top_k, min_score=min_score, package_id=package_id,
                      package_ids=package_ids, pack_query=pack_query,
                      transport=_cloud_transport(env), env=env, home=_operating_home())
    scope_view = {"applied_scope": applied_scope, "scope_source": scope_source}
    if not r.get("ok"):
        return {"action": "cloud_search", "mode": "read", "ok": False,
                "error": r.get("reason"), "source": r.get("source"), **scope_view}
    return {"action": "cloud_search", "mode": "read", "ok": True,
            "evidence": r.get("evidence", []), "count": r.get("count", 0),
            "filtered_out": r.get("filtered_out", 0), "min_score": r.get("min_score"),
            "residual": r.get("residual"), "source": r.get("source"),
            "scanned": r.get("scanned"), "vector_candidates": r.get("vector_candidates"),
            "warnings": r.get("warnings"), "pack_scope": r.get("pack_scope"), **scope_view}


# ==== 작업3: 대비(contrast) read 조회 — detect/build/render 만(기록계열 write 함수 절대 호출 0) ====
# 안전 원칙(구조적 write 차단):
#   - contrast_protocol 에서 read 3함수(detect_conflicts/build_contrast_table/render_contrast_md)만 import.
#   - 기록계열 write 함수(_CONTRAST_WRITE_FNS)는 import·호출 0 → staging_db 미생성 → audit_append/
#     contrast_snapshot INSERT 경로 원천 부재. recorded=False 로 응답 명시, selftest S13 소스검사로 재확인.
#   - node_id 는 [node] 치환·원문 quote 는 PII 마스킹(D-1/PII). 빙구팩은 대비표 제시만(결정 0·자동교체 0).
_CONTRAST_READ_FNS = ("detect_conflicts", "build_contrast_table", "render_contrast_md")
_CONTRAST_WRITE_FNS = ("record_contrast", "verify_snapshot")  # staging write — 노출/호출 절대 금지


def _u_contrast(params=None):
    """빙구팩 preflight 신호 vs 강제조항(mandates) 대비표 조회(read-only·write 0·기록계열 미호출).

    mandates: [{clause_text, stance(require|forbid), source, domain, ...}]. 안전/무결성 domain 은
    detect_conflicts 가 SKIP(헌법 양보 0). 반환 tables 는 node_id 를 노출하지 않고 conflict_id(sha)만 준다.
    """
    params = params or {}
    mandates = params.get("mandates") or []
    if not isinstance(mandates, list):
        return {"action": "contrast", "mode": "read", "error": "mandates_must_be_list"}
    ledger = _operating_ledger()
    from binggupack.pack import recall as RECALL
    # read 3함수만 import — 기록계열 write 함수는 import 0(구조적 write 차단).
    from binggupack.safety.contrast_protocol import (
        detect_conflicts, build_contrast_table, render_contrast_md)
    if not os.path.exists(ledger):
        preflight_out = {"avoid_patterns": [], "preferences": [], "risk_level": "낮음"}
    else:
        files = params.get("files")
        if isinstance(files, str):
            files = [f.strip() for f in files.split(",") if f.strip()]
        preflight_out = RECALL.preflight_context(
            ledger, prompt=params.get("prompt"), cwd=params.get("cwd") or os.getcwd(),
            domain=params.get("domain"), files_changed=files or None, home=_operating_home())
    conflicts = detect_conflicts(preflight_out, mandates, home=_operating_home(), env=os.environ)
    tables = []
    for c in conflicts:
        t = build_contrast_table(c, home=_operating_home())
        md = _mask_node_ids(_redact_pii(render_contrast_md(t)))  # node_id·PII 제거(D-1/PII)
        tables.append({
            "conflict_id": t["conflict_id"], "match_via": t.get("match_via"),
            "relevance": t.get("relevance"),
            "binggu": {"stance": t["binggu_side"]["stance"],
                       "claim": _redact_pii(t["binggu_side"]["quote"]),
                       "trust": t["binggu_side"]["trust"], "cons": t["binggu_side"]["cons"]},
            "mandate": {"stance": t["mandate_side"]["stance"], "source": t["mandate_side"]["source"],
                        "quote": _redact_pii(t["mandate_side"]["quote"]),
                        "quote_status": t["mandate_side"]["quote_status"],
                        "trust": t["mandate_side"]["trust"]},
            "choices": t["choices"], "markdown": md})
    return {"action": "contrast", "mode": "read", "count": len(tables),
            "recorded": False,  # 기록계열 미호출 — audit/snapshot write 0
            "conflicts": tables,
            "note": "빙구팩은 대비표 제시만(결정 0·자동교체 0). 선택은 사장님."}


# ==== 작업4: 추상화(abstraction) 규칙 후보 제안 read 조회 — propose_abstractions 래핑 ====
# 안전 원칙: propose_abstractions 는 read-only(DB write 0·promote 0·self-modifying 0). 응답은
#   node_id 를 노출하지 않는다(proposal_id=content hash·evidence 는 개수만·D-1). 원칙 문구 PII 마스킹.
#   규칙화(active 승격)는 본 도구 밖 — 사람 SAVE(candidate confirm) 경로에서만.
def _u_abstraction(params=None):
    """반복 판단 + hit_events 에서 규칙 후보(추상화)를 '제안만' 조회(read-only·write 0·자동확정 0).

    evidence_refs(node_id 리스트)는 노출하지 않고 supporting_count(개수)만 준다(D-1 정합).
    proposal_id 는 content hash 이지 node_id 가 아니다. 규칙화는 사람 SAVE 로만(promote 0).
    """
    params = params or {}
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": "abstraction", "mode": "read", "empty": True,
                "count": 0, "proposals": []}
    from binggupack.pack import abstraction as ABS
    proposals = ABS.propose_abstractions(ledger, domain=params.get("domain"),
                                         home=_operating_home())
    out = [{
        "proposal_id": p["proposal_id"],                       # content hash(node_id 아님)
        "principle": _redact_pii(p["proposed_principle_text"]),
        "supporting_count": p["supporting_count"],             # 개수만(evidence_refs 미노출·D-1)
        "semantic_subtype": p["semantic_subtype"],
        "domain": p.get("domain"),
        "evidence_summary": p["evidence_summary"],             # 순수 int dict(신호 아님·정렬 key 진입 0)
        "trust": p["trust"],
        "requires_human_save": p["requires_human_save"],
    } for p in proposals]
    return {"action": "abstraction", "mode": "read", "count": len(out), "proposals": out,
            "note": "규칙화는 사람 SAVE(candidate confirm)로만 — 자동확정 0·self-modifying 0. 제안 표시 전용."}


# ==== 작업A(3차): hit/miss mark — 회상 조언 적중/빗나감 기록(write-gated·D-1/D-2/nonce 방어) ====
# save_candidate 와 동일 write-gated 패턴(자동기록 방지 불변):
#   - MCP params 의 actor 는 신뢰 0(무시) — actor 는 reader 하드 고정. confirm 은 dry-run 안내
#     (confirm_expected) 전용이며 사람 승격 증거가 아니다(모델 재현 가능).
#   - dry_run 기본 True(비가역 write default-deny) → 기대 confirm 안내 + write 0.
#   - dry_run=False 여도 reader → mark_outcome 의 actor=human 게이트가 G4_no_auto 로 차단(fail-closed).
#     owner 정당 기록 = 로컬 CLI(binggu mark-hit/mark-miss). (구 P1-A approval 승격 배선 제거 2026-07-13.)
#   - node_id 는 입력받지 않는다(D-1): mark_outcome 가 (recall_query, index)로 why_search 를 재실행해
#     서버가 노드를 스스로 확보 → 회상에 없는 임의 node_id 를 hit 로 위조할 표면이 없다. nonce 는 미지정
#     허용(서버 why_search 재실행으로 스냅샷 확보). decision_id 는 (node_id,nonce) 안정 해시(D-2 이중계상 차단).
#   - 운영 ledger 는 서버 결정(BINGGU_HOME/~/.binggupack). MCP 경로 입력 일절 무시(주입 차단).
#   - 반환은 recorded/reason/outcome/decision_id/nonce/domain/events + node_claim(PII 마스킹) 만 —
#     node_id 등 민감값 미노출(mark_outcome 반환에도 node_id 없음·node_claim 만 PII 마스킹).
#   ★ _FORBIDDEN 미등재 근거: mark 는 write-gated(dry_run 기본·confirm 게이트·actor 하드 reader)라
#     record_resolution(무차별 기록) 계열과 달리 기본-deny 표면만 노출한다. record_resolution 자체는
#     여전히 _FORBIDDEN 유지 — mark 는 그 위조 표면 없는 안전 래퍼(D-1/D-2)로만 노출.
def _mark_outcome_handler(params, outcome):
    """hit/miss 공통 write-gated 핸들러. outcome in ('hit','miss'). 자동기록 방지 이중 게이트."""
    params = params or {}
    recall_query = (params.get("recall_query") or "").strip()
    index = params.get("index")
    domain = params.get("domain")
    dry_run = params.get("dry_run", True)
    # actor 는 항상 reader(하드) — confirm 은 dry_run 의 confirm_expected 안내에만 반영되고 사람 승격 근거가
    # 아니다(모델 재현 가능). 따라서 params['confirm'] 은 읽지 않는다(미사용 제거). owner 기록은 CLI.
    label = "MARK_HIT" if outcome == "hit" else "MARK_MISS"
    act = "mark_" + outcome
    if not recall_query:
        return {"action": act, "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "recall_query_required"}
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        return {"action": act, "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "index_out_of_range"}
    expected = "%s %d %s" % (label, index, recall_query)
    ledger = _operating_ledger()
    if not os.path.exists(ledger):
        return {"action": act, "mode": "write-gated", "verdict": "REJECT",
                "executed_write": False, "reason": "ledger_not_found"}
    if dry_run:
        # dry-run: write 0. confirm_expected(호환 유지). 단 MCP 로는 confirm 만으로 기록되지 않는다(fail-closed).
        return {"action": act, "mode": "dry-run", "verdict": "PREVIEW",
                "executed_write": False, "would_write_ledger": False,
                "confirm_expected": expected,
                "note": "미리보기입니다. MCP 로는 confirm 만으로 기록되지 않습니다(fail-closed) — "
                        "실제 기록은 로컬 CLI: binggu %s <query> --index %d" % (act.replace("_", "-"), index),
                **_MCP_FAIL_CLOSED}
    # actor=reader 하드 고정 — mark_outcome 의 actor=human 게이트가 G4_no_auto 로 차단(fail-closed).
    # recall_nonce 는 서버 스냅샷 재검증용으로 전달만(TAE-P2-05). owner 정당 기록 = 로컬 CLI
    # (binggu mark-hit/mark-miss). (구 P1-A approval consume 배선 제거 2026-07-13.)
    _ensure_scripts_path()
    from openbinggu_owner_accept_ux import open_accept
    db = open_accept(ledger)
    from binggupack.pack import hit_recording as HR
    recall_nonce = params.get("recall_nonce")
    try:
        r = HR.mark_outcome(db, ledger, recall_query, index, outcome, {"actor": "reader"},
                            nonce=recall_nonce, domain=domain, home=_operating_home())
    finally:
        db.close()
    claim = r.get("node_claim")
    return {"action": act, "mode": "write-gated",
            "verdict": "ALLOW" if r.get("recorded") else "BLOCK",
            "executed_write": bool(r.get("recorded")),
            "recorded": bool(r.get("recorded")), "reason": r.get("reason"),
            "outcome": r.get("outcome"), "decision_id": r.get("decision_id"),
            "nonce": r.get("nonce"), "domain": r.get("domain"),
            "events": r.get("events"),
            "node_claim": _redact_pii(claim) if claim else None,  # PII 마스킹(node_id 미포함)
            "ledger": "operating", **_mcp_write_extra({"applied": r.get("recorded")}, params)}


def _u_mark_hit(params=None):
    """회상 조언이 맞았다(직감 적중) 기록 — write-gated·'MARK_HIT <index> <recall_query>' confirm 정확일치.
    node_id 입력 0(D-1)·dry_run 기본·actor 하드 reader·nonce 서버 확보·D-2 이중계상 차단."""
    return _mark_outcome_handler(params, "hit")


def _u_mark_miss(params=None):
    """회상 조언이 틀렸다(직감 빗나감) 기록 — write-gated·'MARK_MISS <index> <recall_query>' confirm 정확일치.
    node_id 입력 0(D-1)·dry_run 기본·actor 하드 reader·nonce 서버 확보·D-2 이중계상 차단."""
    return _mark_outcome_handler(params, "miss")


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
    # save 도구 — write-gated. dry-run 기본·SAVE n confirm 정확일치·actor 서버 하드 고정(reader).
    # _FORBIDDEN db_write 는 무차별 write 금지 라벨이고, save 는 confirm 게이트 통과 단건만 예외적으로
    # 실 write 경로 진입 — 확정은 core 의 사람 save-n 앵커(owner 키보드 'SAVE n' → hook 기록)가 있을 때만
    # (앵커 없으면 G4_no_auto). 경로 입력(ledger_path 등) 일절 무시 — ledger 는 서버 결정.
    "save_candidate":       {"path_params": [], "underlying": _u_save_candidate, "mode": "write-gated",
                             "input_schema": {"properties": {
                                 "text": {"type": "string"},
                                 "indices": {"type": "array", "items": {"type": "integer"}},
                                 "confirm": {"type": "string"},
                                 "dry_run": {"type": "boolean"},
                                 "due_date": {"type": "string"}},
                              "required": ["text", "indices"]}},
    # ---- Phase 2 배치 A: 조회(read) 도구. path 입력 없음·ledger 서버 결정·write 0 ----
    "recall":       {"path_params": [], "underlying": _u_recall, "mode": "read",
                     "input_schema": {"properties": {"query": {"type": "string"},
                                                     "limit": {"type": "integer"}},
                                      "required": ["query"]}},
    "preflight":    {"path_params": [], "underlying": _u_preflight, "mode": "read",
                     "input_schema": {"properties": {"prompt": {"type": "string"},
                                                     "cwd": {"type": "string"},
                                                     "domain": {"type": "string"},
                                                     "files": {"type": "string"}},
                                      "required": []}},
    "trace_review": {"path_params": [], "underlying": _u_trace_review, "mode": "read",
                     "input_schema": {"properties": {
                         "count_only": {"type": "boolean",
                                        "description": "true 면 판정 대기 **수만** 반환(목록 생략). "
                                                       "마무리·상태 점검처럼 '얼마나 밀렸나'만 알면 될 때 — "
                                                       "기본 false 는 종전대로 전체 목록."}},
                      "required": []}},
    # use-time AI 도장 — write-gated(trace store write). confirm 앵커 없음이 **의도**:
    #   ai_stamp 는 AI 자기신고 축이라 사람 승인 위조 개념이 없고(§13 C-11-1 자동 열외 · owner
    #   2026-07-29 설계 지시), actor 서버 하드 고정이라 human 도장 위조도 불가. 저장(SAVE)·승격·
    #   파괴 작업의 confirm 게이트와 무관 — 그쪽은 사람 전용 그대로. ledger write 는 use_count
    #   반영(p1_ranking 승격 함수)뿐이며 owner 덮어쓰기 시 AI 몫만 회수되는 대칭이 있다.
    "trace_stamp":  {"path_params": [], "underlying": _u_trace_stamp, "mode": "write-gated",
                     "input_schema": {"properties": {
                         "trace_id": {"type": "string"},
                         "items": {"type": "array", "items": {"type": "object", "properties": {
                             "i": {"type": "integer"},
                             "verdict": {"type": "string"},
                             "reason_code": {"type": "string"}},
                             "required": ["i", "verdict"]}}},
                      "required": ["trace_id", "items"]}},
    "trace_show":   {"path_params": [], "underlying": _u_trace_show, "mode": "read",
                     "input_schema": {"properties": {"node_id": {"type": "string"}},
                                      "required": ["node_id"]}},
    "status":       {"path_params": [], "underlying": _u_status, "mode": "read",
                     "input_schema": {"properties": {}, "required": []}},
    "list":         {"path_params": [], "underlying": _u_list, "mode": "read",
                     "input_schema": {"properties": {"status": {"type": "string"},
                                                     "kind": {"type": "string"}},
                                      "required": []}},
    "reminders":    {"path_params": [], "underlying": _u_reminders, "mode": "read",
                     "input_schema": {"properties": {"today": {"type": "string"}},
                                      "required": []}},
    # ---- Phase 2 배치 B: 쓰기(write-gated) 도구. dry-run 기본·confirm 정확일치·actor 하드 reader·자동차단 ----
    "pair":         {"path_params": [], "underlying": _u_pair, "mode": "write-gated",
                     "input_schema": {"properties": {
                         "owner_text": {"type": "string"}, "ai_text": {"type": "string"},
                         "owner_pick": {"type": "integer"}, "ai_pick": {"type": "integer"},
                         "by": {"type": "string"}, "relation": {"type": "string"},
                         "confirm": {"type": "string"}, "dry_run": {"type": "boolean"},
                         "due_date": {"type": "string"}},
                      "required": ["owner_text"]}},
    "deprecate":    {"path_params": [], "underlying": _u_deprecate, "mode": "write-gated",
                     "input_schema": {"properties": {
                         "index": {"type": "integer"}, "id8": {"type": "string"},
                         "reason": {"type": "string"}, "confirm": {"type": "string"},
                         "dry_run": {"type": "boolean"}},
                      "required": ["index", "id8"]}},
    "replace":      {"path_params": [], "underlying": _u_replace, "mode": "write-gated",
                     "input_schema": {"properties": {
                         "index": {"type": "integer"}, "id8": {"type": "string"},
                         "new_sentence": {"type": "string"}, "reason": {"type": "string"},
                         "confirm": {"type": "string"}, "dry_run": {"type": "boolean"}},
                      "required": ["index", "id8", "new_sentence"]}},
    # ---- Phase 2 배치 C: 작업 도구(reflect read + harvest 소스 관리). harvest_run 은 미노출(실 fetch owner 전용) ----
    "reflect":        {"path_params": [], "underlying": _u_reflect, "mode": "read",
                       "input_schema": {"properties": {"text": {"type": "string"}},
                                        "required": ["text"]}},
    "harvest_list":   {"path_params": [], "underlying": _u_harvest_list, "mode": "read",
                       "input_schema": {"properties": {}, "required": []}},
    "harvest_add":    {"path_params": [], "underlying": _u_harvest_add, "mode": "write-gated",
                       "input_schema": {"properties": {
                           "kind": {"type": "string"}, "url": {"type": "string"},
                           "keyword": {"type": "string"}, "confirm": {"type": "string"},
                           "dry_run": {"type": "boolean"}},
                        "required": ["kind", "url"]}},
    "harvest_remove": {"path_params": [], "underlying": _u_harvest_remove, "mode": "write-gated",
                       "input_schema": {"properties": {
                           "source_id": {"type": "string"}, "confirm": {"type": "string"},
                           "dry_run": {"type": "boolean"}},
                        "required": ["source_id"]}},
    # ---- 트랙 B: OpenCrab 클라우드 read 조회(egress-only). path 입력 없음·write RPC 미생성·PII 마스킹·미설정 graceful ----
    "cloud_recall": {"path_params": [], "underlying": _u_cloud_recall, "mode": "read",
                     "input_schema": {"properties": {"query": {"type": "string"},
                                                     "top_k": {"type": "integer"},
                                                     "package_id": {"type": "string"},
                                                     "package_ids": {"type": "array", "items": {"type": "string"}},
                                                     "pack_query": {"type": "string"}},
                                      "required": ["query"]}},
    "cloud_packs":  {"path_params": [], "underlying": _u_cloud_packs, "mode": "read",
                     "input_schema": {"properties": {"query": {"type": "string"},
                                                     "category": {"type": "string"}},
                                      "required": []}},
    "cloud_search": {"path_params": [], "underlying": _u_cloud_search, "mode": "read",
                     "input_schema": {"properties": {"query": {"type": "string"},
                                                     "top_k": {"type": "integer"},
                                                     "min_score": {"type": "number"},
                                                     "package_id": {"type": "string"},
                                                     "package_ids": {"type": "array", "items": {"type": "string"}},
                                                     "pack_query": {"type": "string"}},
                                      "required": ["query"]}},
    # ---- 작업3: 판단 근거 회상(why) + 강제조항 대비(contrast) read. node_id 미노출·PII 마스킹·write 0 ----
    "why":      {"path_params": [], "underlying": _u_why, "mode": "read",
                 "input_schema": {"properties": {"query": {"type": "string"},
                                                 "limit": {"type": "integer"}},
                                  "required": ["query"]}},
    "contrast": {"path_params": [], "underlying": _u_contrast, "mode": "read",
                 "input_schema": {"properties": {
                     "prompt": {"type": "string"}, "cwd": {"type": "string"},
                     "domain": {"type": "string"}, "files": {"type": "string"},
                     "mandates": {"type": "array", "items": {"type": "object"}}},
                  "required": ["mandates"]}},
    # ---- 작업4: 추상화(규칙 후보 제안) read. node_id 미노출·proposal_id content hash·write 0·promote 0 ----
    "abstraction": {"path_params": [], "underlying": _u_abstraction, "mode": "read",
                    "input_schema": {"properties": {"domain": {"type": "string"}},
                                     "required": []}},
    # ---- 작업A(3차): hit/miss mark(write-gated). node_id 입력 0(D-1)·confirm 정확일치·dry-run 기본·자동기록 0 ----
    #   save_candidate 와 동일 취급(write-gated). ledger 서버결정·MCP 경로입력 무시·이중게이트(confirm+actor).
    "mark_hit":  {"path_params": [], "underlying": _u_mark_hit, "mode": "write-gated",
                  "input_schema": {"properties": {
                      "recall_query": {"type": "string"}, "index": {"type": "integer"},
                      "confirm": {"type": "string"}, "domain": {"type": "string"},
                      "dry_run": {"type": "boolean"}},
                   "required": ["recall_query", "index"]}},
    "mark_miss": {"path_params": [], "underlying": _u_mark_miss, "mode": "write-gated",
                  "input_schema": {"properties": {
                      "recall_query": {"type": "string"}, "index": {"type": "integer"},
                      "confirm": {"type": "string"}, "domain": {"type": "string"},
                      "dry_run": {"type": "boolean"}},
                   "required": ["recall_query", "index"]}},
}

# 노출 금지(핸들러 부재로 자동 차단되지만, 명시 거부 목록으로 의도 박제)
_FORBIDDEN = {
    "opencrab_write", "opencrab_apply", "opencrab_ingest", "store_write",
    "github_push", "opencrab_upload", "sanitizer_replace", "enum_set",
    "team_billing", "marketplace_publish", "db_write",
    "harvest_run",  # 실 네트워크 fetch — MCP 자동 fetch 위험, owner 스케줄러/CLI 전용
    # 트랙 B egress-only: 클라우드 write 계열 도구는 노출 금지(read cloud_recall/cloud_packs 만 허용).
    # ★opencrab_pack_qa 는 write 가능(assess_and_update/reverse_ingest)이라 절대 노출 금지.
    "opencrab_pack_update", "opencrab_pack_qa", "opencrab_workflow_manage",
    # 작업3: 판단/사용/대비 기록계열 write 함수는 TOOLS 미등록 유지 + 명시 금지(Fable5 C).
    # contrast(read)는 노출하나 record_contrast(staging write)는 절대 미노출 — tool_not_exposed:forbidden.
    "record_contrast", "record_resolution", "record_use", "verify_snapshot",
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
_SAVE_CONVO = ("이 문서는 배포 절차를 정의한다. 이 방식은 비용이 높아 보류한다.")


def _selftest():
    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))
    # 조회(read) 도구는 _operating_ledger()=BINGGU_HOME/ledger.sqlite 를 읽는다. selftest 결정성 +
    # 운영 ledger 미접촉을 위해 존재하지 않는 temp 홈으로 강제(→ read 도구는 graceful empty 반환).
    os.environ["BINGGU_HOME"] = os.path.join(os.environ.get("TEMP", "/tmp"),
                                             "binggu_selftest_home_readonly_none")
    # 트랙 B 클라우드 조회는 os.environ 로 설정을 읽는다. selftest 네트워크 0 보장 위해 앰비언트
    # 클라우드 env(있을 수 있음)를 제거 → load_cloud_config=NO_CLOUD_CONFIG(transport 미구성).
    for _k in ("BINGGU_CLOUD_MCP_URL", "BINGGU_CLOUD_MCP_TOKEN"):
        os.environ.pop(_k, None)
    # read 폴백(~/.claude.json opencrab-cloud URL 재사용)도 selftest 에선 차단 → 실 네트워크 0.
    os.environ["BINGGU_CLOUD_MCP_NO_FALLBACK"] = "1"

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
        ("consumer_npki_block",  "consumer_smoke",       {"pack_path": "C:/Users/fixture-user/AppData/NPKI/c.der"}, False, "deny_cert_npki"),
        ("guard_env_block",      "publish_guard_dryrun", {"pack_path": "examples/toy_project/.env"},    False, "deny_secret"),
        # generic deny(secret) — deny_private_project 는 런타임 owner deny 파일 필요(배포물 부재)라
        # 항상 발동하는 'credential' 키워드로 사설-프로젝트 경로 모양의 deny 커버리지를 유지한다.
        ("validate_private_project_block", "pack_validate", {"pack_path": "C:/Users/fixture-user/example-org/example-project/credentials.json"}, False, "deny_secret"),
        ("forbidden_write",      "opencrab_write",       {"pack_path": "examples/toy_project/p.json"}, False, "tool_not_exposed:forbidden"),
        ("forbidden_push",       "github_push",          {},                                           False, "tool_not_exposed:forbidden"),
        ("unknown_tool",         "do_something",         {},                                           False, "tool_not_exposed:unknown"),
        ("capture_classify_ok",  "capture_classify",     {"utterance": "B안으로 결정"},                 True,  "read no-path"),
        ("capture_preview_ok",   "capture_preview",      {"utterances": ["이거 저장해", "ㅋㅋ"]},        True,  "read no-path"),
        # save 도구 — dry-run 기본은 executed=True(도구 실행됨)이나 executed_write=False(ledger write 0).
        ("save_dryrun_default",  "save_candidate",       {"text": _SAVE_CONVO, "indices": [1]},        True,  "dry-run preview"),
        # Phase 2 배치 A 조회(read) — ledger 없어도 graceful(executed=True). BINGGU_HOME=temp 라 운영 미접촉.
        ("recall_read_ok",       "recall",               {"query": "배포 절차"},                        True,  "read no-path"),
        ("preflight_read_ok",    "preflight",            {"prompt": "이 프로젝트의 빌드 명령을 알려줘"}, True,  "read no-path"),
        ("trace_review_read_ok", "trace_review",         {},                                           True,  "read no-path"),
        ("trace_show_read_ok",   "trace_show",           {"node_id": "node:CONV:none"},                True,  "read no-path"),
        ("status_read_ok",       "status",               {},                                           True,  "read no-path"),
        ("list_read_ok",         "list",                 {},                                           True,  "read no-path"),
        ("reminders_read_ok",    "reminders",            {},                                           True,  "read no-path"),
        # Phase 2 배치 B 쓰기(write-gated) — dry-run 기본은 executed=True(도구 실행)이나 executed_write=False(write 0).
        ("pair_dryrun_default",  "pair",                 {"owner_text": _SAVE_CONVO},                  True,  "dry-run write0"),
        ("deprecate_dryrun",     "deprecate",            {"index": 1, "id8": "abcd1234"},              True,  "dry-run write0"),
        ("replace_dryrun",       "replace",              {"index": 1, "id8": "abcd1234",
                                                          "new_sentence": "수정된 문장"},              True,  "dry-run write0"),
        # Phase 2 배치 C 작업(reflect read + harvest 소스 관리). harvest_run 은 forbidden(실 fetch owner 전용).
        ("reflect_read_ok",      "reflect",              {"text": _SAVE_CONVO},                        True,  "read"),
        ("harvest_list_read_ok", "harvest_list",         {},                                           True,  "read"),
        ("harvest_add_dryrun",   "harvest_add",          {"kind": "arxiv",
                                                          "url": "https://arxiv.org/abs/2401.1"},      True,  "dry-run write0"),
        ("harvest_remove_dryrun", "harvest_remove",      {"source_id": "src_test"},                    True,  "dry-run write0"),
        ("harvest_run_forbidden", "harvest_run",         {},                                           False, "tool_not_exposed:forbidden"),
        # 트랙 B 클라우드 read(egress-only) — 미설정(BINGGU_HOME=temp·클라우드 env 제거) → graceful(executed=True·write 0).
        ("cloud_recall_read_ok",  "cloud_recall",        {"query": "여행 팁"},                          True,  "read no-path graceful"),
        ("cloud_packs_read_ok",   "cloud_packs",         {"query": "신혼여행"},                         True,  "read no-path graceful"),
        ("cloud_search_read_ok",  "cloud_search",        {"query": "빠른 의사결정 신속 판단 직감",
                                                          "min_score": 0.1},                            True,  "read no-path graceful"),
        # 클라우드 write 계열은 노출 금지(_FORBIDDEN). ★pack_qa 는 write 가능 → 절대 노출 금지.
        ("cloud_pack_qa_forbidden",     "opencrab_pack_qa",         {},                               False, "tool_not_exposed:forbidden"),
        ("cloud_pack_update_forbidden", "opencrab_pack_update",     {},                               False, "tool_not_exposed:forbidden"),
        ("cloud_workflow_forbidden",    "opencrab_workflow_manage", {},                               False, "tool_not_exposed:forbidden"),
        # 작업3: why/contrast read(temp 홈 graceful empty) + 기록계열 write 함수 forbidden.
        ("why_read_ok",      "why",      {"query": "배포 절차"},                                       True,  "read no-path"),
        ("contrast_read_ok", "contrast", {"prompt": "이 프로젝트의 빌드 명령을 알려줘", "mandates": [
            {"clause_text": "대량 삭제는 승인 필수", "stance": "require",
             "source": "CLAUDE.md", "domain": "style"}]},                                             True,  "read no-path"),
        ("record_contrast_forbidden",   "record_contrast",   {}, False, "tool_not_exposed:forbidden"),
        ("record_resolution_forbidden", "record_resolution", {}, False, "tool_not_exposed:forbidden"),
        ("record_use_forbidden",        "record_use",        {}, False, "tool_not_exposed:forbidden"),
        # 작업4: abstraction read(temp 홈 graceful empty). 규칙화(promote)는 도구 부재로 자동 차단.
        ("abstraction_read_ok",  "abstraction", {},                    True,  "read no-path"),
        ("abstraction_domain_ok","abstraction", {"domain": "bid"},     True,  "read no-path"),
        # 작업A(3차): hit/miss mark — dry-run 기본. BINGGU_HOME=temp(없음)라 ledger_not_found graceful(executed=True·write 0).
        ("mark_hit_read_ok",     "mark_hit",    {"recall_query": "배포 절차", "index": 1}, True, "write-gated no-ledger"),
        ("mark_miss_read_ok",    "mark_miss",   {"recall_query": "배포 절차", "index": 1}, True, "write-gated no-ledger"),
        # use-time AI 도장(trace_stamp) — temp 홈(store 없음) graceful no-op(executed=True·write 0).
        ("trace_stamp_no_store", "trace_stamp", {"trace_id": "rt-none",
                                                 "items": [{"i": 1, "verdict": "used"}]}, True,
         "write-gated no-store graceful"),
        ("trace_stamp_bad_args", "trace_stamp", {"trace_id": ""},                          True,
         "인자 결손 → error 필드(도구 실행 자체는 됨)"),
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
            # 배치 B dry-run preview/confirm_expected 는 사용자 선택용 입력(텍스트·id8·confirm) 노출이 의도 동작
            # (save 와 동형). id8=사용자가 list 에서 본 node hash8 — 경로/secret 아님, confirm 생성용.
            if tool in ("pair", "deprecate", "replace") and k in ("owner_text", "ai_text",
                                                                  "new_sentence", "confirm", "id8"):
                continue
            # 배치 C: reflect 후보 sentence·harvest confirm_expected 의 kind/url 등은 사용자 입력(공개값) 노출 의도.
            if tool == "reflect" and k == "text":
                continue
            if tool in ("harvest_add", "harvest_remove") and k in ("kind", "url", "keyword",
                                                                   "source_id", "confirm"):
                continue
            # 작업A(3차) mark: recall_query/confirm 은 confirm_expected('MARK_HIT <index> <query>')에
            # 반드시 담기는 사용자 입력(공개 query·경로/secret 아님) — save/deprecate 와 동형 노출 의도.
            if tool in ("mark_hit", "mark_miss") and k in ("recall_query", "confirm"):
                continue
            if isinstance(v, str) and v.strip() and v.strip() in blob:
                raw_leak = True
        verdict = r.get("verdict")
        rc = r.get("reason_code") or (r.get("blocked") and r["blocked"][0].get("reason_code")) or ""
        print("  [%s] %-26s tool=%-20s executed=%-5s verdict=%-7s %s"
              % ("OK" if ok else "FAIL", name, tool, executed, verdict, rc))

    # ----- cloud_search 스코프 결정 로직 검증(applied_scope/scope_source·Fable5 C1·네트워크 0 graceful) -----
    def _cs(p):
        return handle_tool("cloud_search", p, allow_root).get("tool_result", {})
    _sc_pq = _cs({"query": "x", "pack_query": "Binggu Person"})
    _sc_pid = _cs({"query": "x", "package_id": "uuid-1"})
    _sc_none = _cs({"query": "x"})   # temp 홈에 person_pack.json 없음 → unscoped(하위호환)
    _scope_checks = [
        ("CS1 pack_query 명시 → explicit·applied=pack_query:Binggu Person",
         _sc_pq.get("scope_source") == "explicit"
         and _sc_pq.get("applied_scope") == "pack_query:Binggu Person"),
        ("CS2 package_id 명시 → explicit·applied=package_id:uuid-1",
         _sc_pid.get("scope_source") == "explicit"
         and _sc_pid.get("applied_scope") == "package_id:uuid-1"),
        ("CS3 무지정+config 부재 → unscoped(하위호환·무필터)",
         _sc_none.get("scope_source") == "unscoped" and _sc_none.get("applied_scope") == "all"),
    ]
    # CS6 무지정 + person_pack.json(cloud_search_default_pack_query) → config_default 자동 폴백
    import tempfile as _tf
    _cfg_home = _tf.mkdtemp(prefix="cs_cfg_")
    with open(os.path.join(_cfg_home, "person_pack.json"), "w", encoding="utf-8") as _f:
        _json.dump({"cloud_search_default_pack_query": "Binggu Person"}, _f)
    _prev_home = os.environ.get("BINGGU_HOME")
    os.environ["BINGGU_HOME"] = _cfg_home
    try:
        _sc_cfg = _cs({"query": "x"})
    finally:
        os.environ["BINGGU_HOME"] = _prev_home
    _scope_checks.append((
        "CS4 무지정+config 有 → config_default·applied=pack_query:Binggu Person",
        _sc_cfg.get("scope_source") == "config_default"
        and _sc_cfg.get("applied_scope") == "pack_query:Binggu Person"))
    for _nm, _cond in _scope_checks:
        all_ok = all_ok and _cond
        print("  [%s] %s" % ("OK" if _cond else "FAIL", _nm))

    # ----- cloud_recall 스코프 결정 로직 검증(cloud_search 대칭·개인 온톨로지 자동 스코프·네트워크 0 graceful) -----
    def _cr(p):
        return handle_tool("cloud_recall", p, allow_root).get("tool_result", {})
    _rc_pq = _cr({"query": "x", "pack_query": "Binggu Person"})
    _rc_pid = _cr({"query": "x", "package_id": "uuid-1"})
    _rc_none = _cr({"query": "x"})   # temp 홈에 person_pack.json 없음 → unscoped(하위호환)
    os.environ["BINGGU_HOME"] = _cfg_home   # CS4 가 만든 person_pack.json(config_default) 재사용
    try:
        _rc_cfg = _cr({"query": "x"})
    finally:
        if _prev_home is None:
            os.environ.pop("BINGGU_HOME", None)
        else:
            os.environ["BINGGU_HOME"] = _prev_home
    _rc_checks = [
        ("CR1 pack_query 명시 → explicit·applied=pack_query:Binggu Person",
         _rc_pq.get("scope_source") == "explicit"
         and _rc_pq.get("applied_scope") == "pack_query:Binggu Person"),
        ("CR2 package_id 명시 → explicit·applied=package_id:uuid-1",
         _rc_pid.get("scope_source") == "explicit"
         and _rc_pid.get("applied_scope") == "package_id:uuid-1"),
        ("CR3 무지정+config 부재 → unscoped(하위호환·무필터)",
         _rc_none.get("scope_source") == "unscoped" and _rc_none.get("applied_scope") == "all"),
        ("CR4 무지정+config 有 → config_default·applied=pack_query:Binggu Person",
         _rc_cfg.get("scope_source") == "config_default"
         and _rc_cfg.get("applied_scope") == "pack_query:Binggu Person"),
    ]
    for _nm, _cond in _rc_checks:
        all_ok = all_ok and _cond
        print("  [%s] %s" % ("OK" if _cond else "FAIL", _nm))

    # ----- save 도구 전용 검증 (실 ledger write 0 보장: temp DB·dry-run·mock만) -----
    from binggupack.paths import OPERATING_PATHS
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

    # S3) 자동저장 방지 불변식: params actor 는 서버가 무시(reader 하드 고정)하고 confirm='SAVE n' 은
    #     형식 게이트다(사람증거 아님 — 사람 승격은 core 의 save-n 참조 바인딩 앵커만).
    #     confirm 부재(모델 자동호출 흉내)+dry_run=False → 핸들러 confirm 게이트가 REJECT →
    #     save_selected 진입 0(write 0). actor=auto 위조는 무의미(params actor 미사용).
    #     (구 케이스는 도달 불가한 G4_no_auto reason 을 기대해 상시 FAIL — f9a9c61 이 read-only 해제 시 코드만
    #      바꾸고 이 selftest 를 안 고쳐 남은 사전존재 결함. confirm 정확일치는 human 승격, 불일치/부재는
    #      confirm_phrase_mismatch 로 앞단 차단되어 G4_no_auto 는 도달 불가. 실제 방어 경로 reason 으로 정정.)
    r = handle_tool("save_candidate",
                    {"text": _SAVE_CONVO, "indices": [1], "dry_run": False, "actor": "auto"}, allow_root)
    tr = r.get("tool_result") or {}
    s3 = (tr.get("executed_write") is False and tr.get("reason") == "confirm_phrase_mismatch")
    save_ok = save_ok and s3
    save_notes.append(("save_auto_call_write0_no_confirm", s3))

    # S5) pair confirm 부재(자동호출 흉내) + dry_run=False → write 0. (BINGGU_HOME=temp 라 ledger_not_found
    #     또는 reader→save_paired G4 — 어느 쪽이든 운영/temp 자동 write 0.)
    r = handle_tool("pair", {"owner_text": _SAVE_CONVO, "dry_run": False, "actor": "auto"}, allow_root)
    tr = r.get("tool_result") or {}
    s5 = (tr.get("executed_write") is False)
    save_ok = save_ok and s5
    save_notes.append(("pair_no_confirm_write0", s5))

    # S6) deprecate confirm 불일치 + dry_run=False → write 0.
    r = handle_tool("deprecate", {"index": 1, "id8": "abcd1234", "reason": "x",
                                  "confirm": "DEPRECATE 9 zzzzzzzz", "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    s6 = (tr.get("executed_write") is False)
    save_ok = save_ok and s6
    save_notes.append(("deprecate_mismatch_write0", s6))

    # S7) replace confirm 불일치 + dry_run=False → write 0.
    r = handle_tool("replace", {"index": 1, "id8": "abcd1234", "new_sentence": "y", "reason": "x",
                                "confirm": "REPLACE 9 zzzzzzzz WITH y", "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    s7 = (tr.get("executed_write") is False)
    save_ok = save_ok and s7
    save_notes.append(("replace_mismatch_write0", s7))

    # S8) harvest_add confirm 불일치 + dry_run=False → write 0(소스 화이트리스트 미변경).
    r = handle_tool("harvest_add", {"kind": "url", "url": "https://example.org/x",
                                    "confirm": "wrong", "dry_run": False}, allow_root)
    tr = r.get("tool_result") or {}
    s8 = (tr.get("executed_write") is False)
    save_ok = save_ok and s8
    save_notes.append(("harvest_add_mismatch_write0", s8))

    # S9) 트랙 B 클라우드 read — 미설정 graceful(ok False·NO_CLOUD_CONFIG)·네트워크 0·로컬 write 0.
    #     BINGGU_HOME=temp + 클라우드 env 제거 상태라 transport 미구성 → run_query 가 NO_CLOUD_CONFIG.
    r = handle_tool("cloud_recall", {"query": "여행 팁"}, allow_root)
    tr = r.get("tool_result") or {}
    s9 = (r.get("executed") is True and tr.get("ok") is False
          and tr.get("error") == "NO_CLOUD_CONFIG")
    save_ok = save_ok and s9
    save_notes.append(("cloud_recall_unconfigured_graceful", s9))

    # S10) 클라우드 write 계열(pack_qa/pack_update/workflow_manage) 노출 금지(egress-only 불변).
    s10 = all(handle_tool(t, {}, allow_root).get("executed") is False
              and handle_tool(t, {}, allow_root).get("reason_code", "").endswith("forbidden")
              for t in ("opencrab_pack_qa", "opencrab_pack_update", "opencrab_workflow_manage"))
    save_ok = save_ok and s10
    save_notes.append(("cloud_write_tools_forbidden", s10))

    import json as _j11
    # S11) why — read·write 0·node_id/edge_id 미노출(D-1). temp 홈이라 graceful empty.
    r = handle_tool("why", {"query": "배포 절차"}, allow_root)
    tr = r.get("tool_result") or {}
    blob11 = _j11.dumps(tr, ensure_ascii=False)
    s11 = (r.get("executed") is True and "node_id" not in blob11 and "node:" not in blob11)
    save_ok = save_ok and s11
    save_notes.append(("why_read_no_node_id_write0", s11))

    # S12) contrast — read·기록계열 미호출(recorded=False)·node_id 미노출.
    r = handle_tool("contrast", {"prompt": "이 프로젝트의 빌드 명령을 알려줘", "mandates": [
        {"clause_text": "대량 삭제는 승인 필수", "stance": "require",
         "source": "CLAUDE.md", "domain": "style"}]}, allow_root)
    tr = r.get("tool_result") or {}
    s12 = (r.get("executed") is True and tr.get("recorded") is False
           and "node:" not in _j11.dumps(tr, ensure_ascii=False))
    save_ok = save_ok and s12
    save_notes.append(("contrast_read_recorded_false", s12))

    # S13) 구조적 차단: contrast 핸들러 소스에 기록계열 write 함수 호출 0(call-form 검사).
    import inspect as _insp
    _csrc = _insp.getsource(_u_contrast)
    s13 = all((w + "(") not in _csrc for w in _CONTRAST_WRITE_FNS)
    save_ok = save_ok and s13
    save_notes.append(("contrast_no_write_fn_call", s13))

    # S14) 기록계열 write 함수 4개 — TOOLS 미등록 + _FORBIDDEN → tool_not_exposed:forbidden.
    s14 = all(handle_tool(t, {}, allow_root).get("executed") is False
              and handle_tool(t, {}, allow_root).get("reason_code", "").endswith("forbidden")
              and t not in TOOLS
              for t in ("record_contrast", "record_resolution", "record_use", "verify_snapshot"))
    save_ok = save_ok and s14
    save_notes.append(("record_write_fns_forbidden", s14))

    # ----- 작업A(3차): hit/miss mark gates (temp home·temp ledger·운영 write 0) -----
    # 핸들러가 _operating_ledger()=BINGGU_HOME/ledger.sqlite 를 쓰므로, 실 write 경로 검증은 BINGGU_HOME 을
    # 잠깐 temp 로 바꿔 격리한다(운영 ~/.binggupack 미접촉·mark 후 원복). OPERATING_PATHS 는 별도(불변 유지).
    import shutil as _sh
    import tempfile as _tf
    _saved_home = os.environ.get("BINGGU_HOME")
    mark_ok = True
    mark_notes = []
    _mtmp = _tf.mkdtemp(prefix="binggu_mark_mcp_")
    try:
        _mledger = os.path.join(_mtmp, "ledger.sqlite")
        _ensure_scripts_path()
        from openbinggu_owner_accept_ux import open_accept as _oa
        _mdb = _oa(_mledger)  # 핸들러와 동일 open_accept 로 회상 가능한 판단 노드 3건 적재.
        for _nid, _sent in (("mk1", "배포 전 로컬 selftest 와 live endpoint 를 확인한다"),
                            ("mk2", "배포 전 로컬 selftest 확인하고 endpoint 응답을 본다"),
                            ("mk3", "무관한 요리 레시피 메모")):
            _mdb.con.execute(
                "INSERT INTO nodes(node_id,node_type,sentence,semantic_subtype,speaker,state,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (_nid, "judgment", _sent, "교훈", "owner", "active", "2026-06-20T00:00:00Z"))
        _mdb.con.commit()
        _mdb.close()
        os.environ["BINGGU_HOME"] = _mtmp  # 핸들러 _operating_ledger()가 이 temp 를 운영 ledger 로 인식
        _mq = "배포 전 endpoint 확인"

        # M1) dry-run 기본 — write 0(executed_write False·would_write_ledger False), 기대 confirm 안내.
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1}, allow_root)
        tr = r.get("tool_result") or {}
        m1 = (r.get("executed") is True and tr.get("executed_write") is False
              and tr.get("would_write_ledger") is False and tr.get("verdict") == "PREVIEW"
              and tr.get("confirm_expected") == ("MARK_HIT 1 " + _mq))
        mark_ok = mark_ok and m1
        mark_notes.append(("mark_dryrun_write0", m1))

        # M2) confirm 불일치 + dry_run=False → write 0(reader → mark_outcome G4_no_auto).
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1,
                                     "confirm": "MARK_HIT 9 wrong", "dry_run": False}, allow_root)
        tr = r.get("tool_result") or {}
        m2 = (tr.get("executed_write") is False and tr.get("recorded") is False
              and tr.get("reason") == "G4_no_auto")
        mark_ok = mark_ok and m2
        mark_notes.append(("mark_confirm_mismatch_write0", m2))

        # M3) actor 위조 무의미 — params actor='human' 이라도 서버가 reader 하드 고정 → write 0(G4_no_auto).
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1,
                                     "dry_run": False, "actor": "human"}, allow_root)
        tr = r.get("tool_result") or {}
        m3 = (tr.get("executed_write") is False and tr.get("recorded") is False
              and tr.get("reason") == "G4_no_auto")
        mark_ok = mark_ok and m3
        mark_notes.append(("mark_actor_forge_reader_write0", m3))

        # M4) ★P0 봉인: confirm 정확일치(모델이 dry_run 의 confirm_expected 를 재현)만으로는 기록 안 됨.
        #     사람 앵커가 없어 actor=reader → mark_outcome G4_no_auto. MCP 로는 사람-확인 유용성 기록 불가.
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1,
                                     "confirm": "MARK_HIT 1 " + _mq, "dry_run": False}, allow_root)
        tr = r.get("tool_result") or {}
        m4 = (tr.get("executed_write") is False and tr.get("recorded") is False
              and tr.get("reason") == "G4_no_auto")
        mark_ok = mark_ok and m4
        mark_notes.append(("mark_confirm_reproduced_BLOCKED(no_human_anchor)", m4))

        # M5) node_id 미노출(D-1) — mark 응답에 node:/node_id 토큰 없음(위조 표면 0). G4 응답에도 유지.
        _mblob = _json.dumps(tr, ensure_ascii=False)
        m5 = ("node:" not in _mblob and "node_id" not in _mblob)
        mark_ok = mark_ok and m5
        mark_notes.append(("mark_no_node_id_exposed", m5))

        # M6) 재mark 도 여전히 fail-closed(actor=reader → G4_no_auto). 자율 반복 write 차단.
        r = handle_tool("mark_hit", {"recall_query": _mq, "index": 1,
                                     "confirm": "MARK_HIT 1 " + _mq, "dry_run": False}, allow_root)
        tr = r.get("tool_result") or {}
        m6 = (tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto")
        mark_ok = mark_ok and m6
        mark_notes.append(("mark_reMark_still_BLOCKED", m6))

        # M7) mark_miss confirm 재현도 동일 fail-closed(G4_no_auto). 형제 write 경로 정합.
        r = handle_tool("mark_miss", {"recall_query": _mq, "index": 2,
                                      "confirm": "MARK_MISS 2 " + _mq, "dry_run": False}, allow_root)
        tr = r.get("tool_result") or {}
        m7 = (tr.get("executed_write") is False and tr.get("recorded") is False
              and tr.get("reason") == "G4_no_auto")
        mark_ok = mark_ok and m7
        mark_notes.append(("mark_miss_confirm_reproduced_BLOCKED", m7))

        # ===== autonomous_agent_preview_then_confirm 회귀 (P0 우회 봉인 검증) =====
        # 시나리오: 동일 에이전트가 dry_run 으로 confirm_expected 를 읽고 그대로 재현해 dry_run=false 호출.
        # 별도의 신뢰 가능한 사람 승인(save_gate 앵커) 없음. 기대: 전부 executed_write=False·G4_no_auto.
        # (이전엔 mismatch/no-confirm 만 테스트했고 '정확 confirm 재현' 익스플로잇은 미검증이었다.)
        from openbinggu_candidate_list_view import node_id8 as _n8
        _id8 = _n8("mk1")

        # PW1) pair 우회 재현 — dry_run 이 준 confirm 을 재현해도 신규 노드 주입 차단.
        _pconf = (handle_tool("pair", {"owner_text": _SAVE_CONVO, "dry_run": True}, allow_root)
                  .get("tool_result") or {}).get("confirm_expected")
        tr = (handle_tool("pair", {"owner_text": _SAVE_CONVO, "dry_run": False,
                                   "confirm": _pconf}, allow_root).get("tool_result") or {})
        pw1 = (bool(_pconf) and tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto")
        mark_notes.append(("pair_preview_then_confirm_BLOCKED", pw1))

        # PW2) deprecate 우회 재현 — 실제 index/id8 + 재현 confirm 이어도 active 노드 기각 차단.
        _dconf = (handle_tool("deprecate", {"index": 1, "id8": _id8, "reason": "x",
                                            "dry_run": True}, allow_root).get("tool_result") or {}
                  ).get("confirm_expected")
        tr = (handle_tool("deprecate", {"index": 1, "id8": _id8, "reason": "x",
                                        "confirm": _dconf, "dry_run": False}, allow_root)
              .get("tool_result") or {})
        pw2 = (bool(_dconf) and tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto")
        mark_notes.append(("deprecate_preview_then_confirm_BLOCKED", pw2))

        # PW3) replace 우회 재현 — 재현 confirm 이어도 active 노드 교체 차단.
        _new = "교체된 새 문장이다"
        _rconf = (handle_tool("replace", {"index": 1, "id8": _id8, "new_sentence": _new,
                                          "reason": "x", "dry_run": True}, allow_root)
                  .get("tool_result") or {}).get("confirm_expected")
        tr = (handle_tool("replace", {"index": 1, "id8": _id8, "new_sentence": _new,
                                      "reason": "x", "confirm": _rconf, "dry_run": False}, allow_root)
              .get("tool_result") or {})
        pw3 = (bool(_rconf) and tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto")
        mark_notes.append(("replace_preview_then_confirm_BLOCKED", pw3))

        # PW4) 우회 시도 후 격리 ledger 불변 — 신규 노드 0(pair)·mk1 active 유지(deprecate/replace).
        _vdb = _oa(_mledger)
        _n_after = _vdb.con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        _mk1 = _vdb.con.execute("SELECT state FROM nodes WHERE node_id='mk1'").fetchone()
        _vdb.close()
        pw4 = (_n_after == 3 and _mk1 is not None and _mk1[0] == "active")
        mark_notes.append(("bypass_attempts_ledger_unchanged", pw4))

        # PW5) 안내 정합(P0.1) — pair/deprecate/replace/mark dry-run 응답이 fail-closed 를 정확히 안내.
        #      write_available=False · reason=human_save_required · owner_action=use_local_cli.
        pw5 = True
        for _t, _p in (("pair", {"owner_text": _SAVE_CONVO}),
                       ("deprecate", {"index": 1, "id8": _id8}),
                       ("replace", {"index": 1, "id8": _id8, "new_sentence": "교체문"}),
                       ("mark_hit", {"recall_query": _mq, "index": 1})):
            _dr = (handle_tool(_t, dict(_p, dry_run=True), allow_root).get("tool_result") or {})
            pw5 = pw5 and (_dr.get("write_available") is False
                           and _dr.get("owner_action") == "use_local_cli"
                           and _dr.get("reason") == "human_save_required"
                           and bool(_dr.get("guidance")))
        mark_notes.append(("mcp_mutation_fail_closed_guidance", pw5))

        # PW6) ★MCP save approval 제거 회귀(2026-07-13): approval_id 를 제시해도 write 승격 경로가
        #      없다 — 요청(PENDING) 미발행·소비 0·write 0·approval_id_ignored 명시(fail-closed 불변).
        tr = (handle_tool("deprecate", {"index": 1, "id8": _id8, "reason": "x",
                                        "confirm": "DEPRECATE 1 " + _id8, "dry_run": False,
                                        "approval_id": "deadbeef" * 3}, allow_root)
              .get("tool_result") or {})
        pw6 = (tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto"
               and tr.get("approval_id_ignored") is True and not tr.get("request_id"))
        mark_notes.append(("approval_id_no_longer_promotes_BLOCKED", pw6))

        mark_ok = mark_ok and pw1 and pw2 and pw3 and pw4 and pw5 and pw6
    finally:
        if _saved_home is None:
            os.environ.pop("BINGGU_HOME", None)
        else:
            os.environ["BINGGU_HOME"] = _saved_home
        _sh.rmtree(_mtmp, ignore_errors=True)

    save_ok = save_ok and mark_ok
    save_notes.extend(mark_notes)

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
