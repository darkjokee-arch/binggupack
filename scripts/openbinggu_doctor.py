#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu doctor — 공개 전 필수 검사 단일 진입점 (S5 최소 구현 후보).

목적:
- 사용자가 1차 배포판을 받은 뒤 한 명령으로 공개 전 필수 검사를 실행.
- 신규 공격면 최소화: 기존 selftest/gate 를 subprocess 로 "호출만" 한다(로직 재구현 0).
- raw PII/secret/private path 미출력 → 검사명 + PASS/FAIL + reason_code + count 만.

범위: 검사 오케스트레이션 + secret scan stub(dry-run). 실 OpenCrab 업로드/production/store/DB write 0.
CLI: python openbinggu_doctor.py --selftest

검사 묶음:
  1 scope_envelope_dryrun  2 watcher_pack_builder_m0  3 pack_validate
  4 pack_consumer_smoke    5 path_safety_gate         6 mcp_path_gate_adapter
  7 secret/PII scan stub (dry-run, raw 미출력)
"""
import sys
import os
import re
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # repo 루트(작업본)

# operating store: 변하면 안 되는 운영 파일(있을 때만 검사)
_OP_STORE = [
    os.path.join(_ROOT, "localcrab_index.sqlite"),
    os.path.join(_ROOT, "user_graph.yaml"),
    os.path.join(_ROOT, "_graph_merge.yaml"),
]

_CHECKS = [
    ("scope_envelope_dryrun", "openbinggu_scope_envelope_dryrun.py"),
    ("watcher_pack_builder_m0", "watcher_pack_builder_m0.py"),
    ("pack_validate", "openbinggu_pack_validate.py"),
    ("pack_consumer_smoke", "openbinggu_pack_consumer_smoke.py"),
    ("path_safety_gate", "openbinggu_path_safety_gate.py"),
    ("mcp_path_gate_adapter", "openbinggu_mcp_path_gate_adapter.py"),
    ("public_tree_scan", "openbinggu_public_tree_scan.py"),  # 실 트리 secret/PII scanner(synthetic 검증)
    ("c2_guard_selftest", "openbinggu_c2_guard_selftest.py"),  # C-2 단일통제 가드 synthetic selftest(21/21, write 0)
    ("staging_write_selftest", "openbinggu_staging_write_selftest.py"),  # Step3 staging write synthetic(11/11, temp DB, 운영 write 0)
    ("phase4_reviewer_confirmed", "openbinggu_phase4_reviewer_confirmed_selftest.py"),  # v0.5 preview 불변 강제(confirmed_created=0/applied=0/promoted=0/upload=0)
    ("runtime_access_engine", "openbinggu_runtime_access_engine.py"),  # deny-by-default 강제 엔진(21케이스, write 0)
    ("mcp_server_handlers", "openbinggu_mcp_server_handlers.py"),  # MCP 도구 핸들러 gate 결선(synthetic, underlying mock)
    ("reviewer_auth_session", "openbinggu_reviewer_auth_session_selftest.py"),  # reviewer 인증/세션(in-memory, FS write 0)
]

# 하위 selftest 의 "GATE: GO" / "RESULT: n/n  GATE=GO" 양식 모두 수용
_GATE_RE = re.compile(r"GATE[:=]\s*([A-Za-z\-]+)")


def _run_selftest(script):
    """하위 selftest subprocess 실행. raw stdout 은 보관하지 않고 GATE 라벨 + exit 만 추출."""
    path = os.path.join(_HERE, script)
    try:
        p = subprocess.run([sys.executable, path, "--selftest"],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return {"gate": "ERROR", "exit": -1, "reason_code": "subprocess_error:" + type(e).__name__}
    m = None
    for line in p.stdout.splitlines():
        mm = _GATE_RE.search(line)
        if mm:
            m = mm.group(1)
    gate = m or "NO-GATE"
    reason = None
    if gate != "GO":
        reason = "gate_not_go"
    if p.returncode != 0:
        reason = (reason + "+nonzero_exit") if reason else "nonzero_exit"
    # raw stdout/stderr 는 의도적으로 버림(덤프 금지)
    return {"gate": gate, "exit": p.returncode, "reason_code": reason}


def _secret_scan_stub():
    """
    secret/PII scan dry-run stub.
    실 공개 트리 부재 → 합성 샘플로 검출 로직 생존만 확인. raw 값 미출력, count/reason_code 만.
    PASS 기준: dirty 합성 샘플을 검출(hits>0) AND clean 샘플은 통과.
    """
    # 합성 샘플(실 파일/실 secret 아님)
    clean = ["build with make", "run tests with make test", "synthetic claim summary"]
    # 정적 소스에 scanner 트리거 리터럴이 남지 않게 런타임 조립(검출은 동일)
    dirty = ["api_" + "key=" + "AKIA" + "0000EXAMPLE0000", "to" + "ken: " + "ghp_" + "EXAMPLE0000000000",
             "phone 010-" + "0000-0000", "C:/Users/x/.env"]
    pats = [
        ("secret_kv", re.compile(r"(api_key|token|secret|password)\s*[:=]", re.I)),
        ("aws_key", re.compile(r"AKIA[0-9A-Z]{4,}")),
        ("phone", re.compile(r"01[016789]-?\d{3,4}-?\d{4}")),
        ("dotenv_path", re.compile(r"\.env\b")),
    ]
    clean_hits = 0
    for s in clean:
        if any(rx.search(s) for _, rx in pats):
            clean_hits += 1
    dirty_codes = {}
    for s in dirty:
        for code, rx in pats:
            if rx.search(s):
                dirty_codes[code] = dirty_codes.get(code, 0) + 1
    detects = sum(dirty_codes.values())
    ok = (clean_hits == 0) and (detects > 0)
    return {"pass": ok, "scanned": len(clean) + len(dirty),
            "clean_false_positive": clean_hits, "dirty_detected": detects,
            "reason_codes": sorted(dirty_codes.keys()),  # 카테고리만, raw 값 0
            "raw_not_output": True}


def _store_snapshot():
    snap = {}
    for f in _OP_STORE:
        try:
            snap[os.path.basename(f)] = os.path.getmtime(f) if os.path.exists(f) else None
        except Exception:
            snap[os.path.basename(f)] = None
    return snap


def _real_tree_scan(tree_root):
    """실 공개 후보 트리 scan(요약만). raw 미출력. import 는 호출 시점에만."""
    sys.path.insert(0, _HERE)
    from openbinggu_public_tree_scan import scan_public_tree  # noqa: E402
    # .gitignore 계열 기본 제외(공개 대상 아님 전제)
    # 주의: .env/credentials*/private_key* 는 scanner 의 "검출 대상"이므로 제외 금지(검출 무력화 방지)
    ignore = ["*.sqlite", "*.db", "*_graph.yaml", "reports/", "reviews/", "captures/",
              "tmp/", "__pycache__/", "*.bak_*"]
    return scan_public_tree(tree_root, ignore_globs=ignore)


def run_doctor(tree_root=None):
    print("=" * 72)
    print("OpenBinggu doctor — 공개 전 필수 검사 (synthetic / dry-run)")
    print("=" * 72)

    store_before = _store_snapshot()

    results = []
    all_ok = True

    # 1~6 기존 selftest 호출
    for name, script in _CHECKS:
        r = _run_selftest(script)
        ok = (r["gate"] == "GO" and r["exit"] == 0)
        all_ok = all_ok and ok
        results.append((name, ok, r))
        tag = "PASS" if ok else "FAIL"
        extra = "" if ok else ("  reason=%s" % r.get("reason_code"))
        print("  [%s] %-24s GATE=%-6s exit=%s%s" % (tag, name, r["gate"], r["exit"], extra))

    # 7 secret/PII scan stub
    sc = _secret_scan_stub()
    sc_ok = sc["pass"]
    all_ok = all_ok and sc_ok
    print("  [%s] %-24s detected=%d false_positive=%d codes=%s (raw 미출력)"
          % ("PASS" if sc_ok else "FAIL", "secret_pii_scan_stub",
             sc["dirty_detected"], sc["clean_false_positive"], sc["reason_codes"]))

    # (옵션) 실 공개 후보 트리 scan — --tree 지정 시에만
    if tree_root:
        tr = _real_tree_scan(tree_root)
        tr_ok = (tr["verdict"] == "CLEAN")
        all_ok = all_ok and tr_ok
        print("  [%s] %-24s verdict=%s hits=%d by_reason=%s content_skipped=%s (raw 미출력)"
              % ("PASS" if tr_ok else "FAIL", "real_tree_scan",
                 tr["verdict"], tr["hits"], tr["by_reason"], tr.get("content_skipped")))

    # operating store 불변 확인
    store_after = _store_snapshot()
    store_unchanged = (store_before == store_after)
    all_ok = all_ok and store_unchanged
    print("  [%s] %-24s operating_store_unchanged=%s"
          % ("PASS" if store_unchanged else "FAIL", "operating_store_guard", store_unchanged))

    passed = sum(1 for _, ok, _ in results if ok) + (1 if sc_ok else 0) + (1 if store_unchanged else 0)
    total = len(results) + 2
    print("\n  summary: %d/%d PASS  (raw PII/secret/private path 미출력)" % (passed, total))

    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if all_ok else 1


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("--selftest", "--check", "--run"):
        sys.exit(run_doctor())
    elif args[0] == "--tree" and len(args) >= 2:
        sys.exit(run_doctor(tree_root=args[1]))
    else:
        print("usage: openbinggu_doctor.py [--selftest | --tree <ROOT>]")
        sys.exit(2)


if __name__ == "__main__":
    main()
