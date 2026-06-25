# -*- coding: utf-8 -*-
"""Phase3 — strangler wrapper compatibility regression harness (read-only, write 0).

목적: phase1/2 strangler 이관(verb_edge → binggupack.schema, match → binggupack.policy)이
package import 와 scripts(PYTHONPATH) import 양형태에서 byte-identical 한 공개 심볼/값/판정을
노출하는지, public entrypoint 가 import 가능한지, save/write 경로가 binggu_platform resolver 를
경유하며 운영 home 을 미접촉하는지 자동 회귀 검증한다.

원칙(stdlib only / read-only):
  - 어떤 ledger/save_gate/store 도 write 하지 않는다(0 write).
  - save_gate 경로 검증은 임시 BINGGU_HOME 격리로만(운영 ~/.binggupack 미접촉, mtime 전후 동일).
  - 기능 로직 이관 0 — 이 파일은 harness 일 뿐, 대상 모듈을 수정하지 않는다.

CLI: python scripts/strangler_wrapper_compat_selftest.py   → GATE GO/NO-GO 출력 + exit code.
"""
import importlib
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>

_RESULTS = []  # (ok: bool, label: str, detail: str)


def chk(ok, label, detail=""):
    _RESULTS.append((bool(ok), label, str(detail)))


def _ensure_paths():
    # package import(<repo>) + scripts import(<repo>/scripts) 양형태 재현용 경로.
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)


# ---------------------------------------------------------------------------
# (a) verb_edge wrapper compat — package import vs scripts import 양형태 일치
# ---------------------------------------------------------------------------
def check_verb_edge():
    try:
        from binggupack.schema.verb_edge import (
            validate_verb_edge, VERB_EDGES, WEAK_LABELS, VALID_STATUS,
        )
    except Exception as e:  # noqa: BLE001
        chk(False, "(a) verb_edge package import", repr(e))
        return
    chk(True, "(a) verb_edge package import", "ok")

    try:
        import openbinggu_verb_edge_schema as s  # scripts import (PYTHONPATH 경유)
    except Exception as e:  # noqa: BLE001
        chk(False, "(a) verb_edge scripts import", repr(e))
        return
    chk(True, "(a) verb_edge scripts import", "ok")

    # 동일 객체(identity) — thin wrapper 가 re-export 했으므로 같은 객체여야 함.
    chk(s.VERB_EDGES is VERB_EDGES, "(a) VERB_EDGES identical object",
        "id pkg=%s scripts=%s" % (id(VERB_EDGES), id(s.VERB_EDGES)))
    chk(s.WEAK_LABELS is WEAK_LABELS, "(a) WEAK_LABELS identical object", "")
    chk(s.VALID_STATUS is VALID_STATUS, "(a) VALID_STATUS identical object", "")
    chk(s.validate_verb_edge is validate_verb_edge,
        "(a) validate_verb_edge identical fn", "")

    # 값/개수 일치: VERB_EDGES 6종, WEAK_LABELS 2종, VALID_STATUS 3종.
    chk(len(VERB_EDGES) == 6, "(a) VERB_EDGES count==6", "got %d" % len(VERB_EDGES))
    chk(len(WEAK_LABELS) == 2, "(a) WEAK_LABELS count==2", "got %d" % len(WEAK_LABELS))
    chk(len(VALID_STATUS) == 3, "(a) VALID_STATUS count==3", "got %d" % len(VALID_STATUS))
    chk(set(VERB_EDGES) == set(s.VERB_EDGES), "(a) VERB_EDGES keys equal", "")
    chk(WEAK_LABELS == s.WEAK_LABELS, "(a) WEAK_LABELS value equal",
        "%s" % sorted(WEAK_LABELS))
    chk(VALID_STATUS == s.VALID_STATUS, "(a) VALID_STATUS value equal",
        "%s" % sorted(VALID_STATUS))


# ---------------------------------------------------------------------------
# (b) match_policy wrapper compat — classify_edge_pair 동일 판정
# ---------------------------------------------------------------------------
def check_match_policy():
    try:
        from binggupack.policy.match import (
            classify_edge_pair, classify_pair, evaluate, summarize, RF,
        )
    except Exception as e:  # noqa: BLE001
        chk(False, "(b) match package import", repr(e))
        return
    chk(True, "(b) match package import", "ok")

    try:
        import localbinggu_match_policy as mp  # scripts import
    except Exception as e:  # noqa: BLE001
        chk(False, "(b) match scripts import", repr(e))
        return
    chk(True, "(b) match scripts import", "ok")

    # 동일 객체(identity) — wrapper re-export.
    chk(mp.classify_edge_pair is classify_edge_pair,
        "(b) classify_edge_pair identical fn", "")
    chk(mp.classify_pair is classify_pair, "(b) classify_pair identical fn", "")
    chk(mp.evaluate is evaluate, "(b) evaluate identical fn", "")
    chk(mp.summarize is summarize, "(b) summarize identical fn", "")
    chk(mp.RF is RF, "(b) RF identical object", "")

    # classify_edge_pair 동일 판정(양형태 동일 입력 → 동일 결과).
    e1 = {"relation": "supports_judgment", "src": "n1", "tgt": "n2", "status": "candidate"}
    e2 = {"relation": "supports_judgment", "src": "n1", "tgt": "n2", "status": "candidate"}
    try:
        r_pkg = classify_edge_pair(e1, e2)
        r_scr = mp.classify_edge_pair(e1, e2)
        chk(r_pkg == r_scr, "(b) classify_edge_pair same verdict",
            "pkg=%r scripts=%r" % (r_pkg, r_scr))
    except Exception as e:  # noqa: BLE001
        chk(False, "(b) classify_edge_pair invoke", repr(e))


# ---------------------------------------------------------------------------
# (c) public entrypoint 경로 검증 — 존재 + import 가능
# ---------------------------------------------------------------------------
def _file_exists(rel):
    return os.path.isfile(os.path.join(ROOT, rel))


def _can_import(modname, path):
    # 파일 존재만으로 import 가능 여부를 byte-side-effect 없이 확인.
    try:
        spec = importlib.util.spec_from_file_location(modname, path)
        if spec is None or spec.loader is None:
            return False, "no spec"
        # entrypoint 는 __main__ 가드가 있어 import 시 side-effect 없음을 가정.
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return True, "ok"
    except SystemExit as e:  # noqa: PERF203
        return False, "SystemExit(%r)" % e.code
    except Exception as e:  # noqa: BLE001
        return False, repr(e)


def check_entrypoints():
    targets = [
        ("scripts/smoke_test.py", "_ep_smoke_test"),
        ("scripts/install_claude_mcp.py", "_ep_install_claude_mcp"),
        ("binggu.py", "_ep_binggu"),
    ]
    for rel, modname in targets:
        exists = _file_exists(rel)
        chk(exists, "(c) exists %s" % rel, rel)
        if not exists:
            continue
        ok, detail = _can_import(modname, os.path.join(ROOT, rel))
        chk(ok, "(c) import %s" % rel, detail)


# ---------------------------------------------------------------------------
# (d) save/write 경로 변경 없음 — gate_path()/gate_home() 가 resolver 경유 +
#     임시 BINGGU_HOME 격리(운영 home 미접촉) 확인
# ---------------------------------------------------------------------------
def check_save_gate_resolver():
    try:
        import binggu_save_gate as sg
    except Exception as e:  # noqa: BLE001
        chk(False, "(d) binggu_save_gate import", repr(e))
        return
    chk(True, "(d) binggu_save_gate import", "ok")

    try:
        import binggu_platform as plat
        has_plat = True
    except Exception as e:  # noqa: BLE001
        has_plat = False
        chk(False, "(d) binggu_platform import", repr(e))

    import tempfile
    prev = os.environ.get("BINGGU_HOME")
    tmp = tempfile.mkdtemp(prefix="binggu_strangler_compat_")
    try:
        os.environ["BINGGU_HOME"] = tmp
        gh = sg.gate_home()
        gp = sg.gate_path()
        # gate_home() 이 임시 BINGGU_HOME 으로 격리되는지(resolver lazy 재계산).
        chk(os.path.normpath(gh) == os.path.normpath(tmp),
            "(d) gate_home honors temp BINGGU_HOME", "gh=%s" % gh)
        # gate_path() = <gate_home>/save_gate_log.jsonl
        chk(os.path.normpath(gp) == os.path.normpath(os.path.join(tmp, "save_gate_log.jsonl")),
            "(d) gate_path under gate_home", "gp=%s" % gp)
        # resolver 경유: binggu_platform.binggu_home() 과 일치(있을 때).
        if has_plat:
            chk(os.path.normpath(gh) == os.path.normpath(plat.binggu_home()),
                "(d) gate_home == binggu_platform.binggu_home()", "")
        # write 0 검증: gate_path 파일이 생성되지 않았는지(harness 는 read-only).
        chk(not os.path.exists(gp), "(d) no write to gate_path (read-only)", gp)
    finally:
        if prev is None:
            os.environ.pop("BINGGU_HOME", None)
        else:
            os.environ["BINGGU_HOME"] = prev
        try:
            os.rmdir(tmp)
        except OSError:
            pass  # 비어있지 않으면(예상外) 그대로 — 운영 home 아님


# ---------------------------------------------------------------------------
# 운영 ~/.binggupack mtime 전후 동일 확인 (미접촉 보증)
# ---------------------------------------------------------------------------
def _op_home():
    # BINGGU_HOME 무시한 OS 기본 운영 home.
    return os.path.join(os.path.expanduser("~"), ".binggupack")


def main():
    _ensure_paths()

    op = _op_home()
    op_mtime_before = os.path.getmtime(op) if os.path.isdir(op) else None

    check_verb_edge()
    check_match_policy()
    check_entrypoints()
    check_save_gate_resolver()

    op_mtime_after = os.path.getmtime(op) if os.path.isdir(op) else None
    chk(op_mtime_before == op_mtime_after,
        "(integrity) operational ~/.binggupack mtime unchanged",
        "before=%s after=%s" % (op_mtime_before, op_mtime_after))

    # ----- 리포트 -----
    passed = sum(1 for ok, _, _ in _RESULTS if ok)
    total = len(_RESULTS)
    for ok, label, detail in _RESULTS:
        mark = "PASS" if ok else "FAIL"
        line = "  [%s] %s" % (mark, label)
        if detail and not ok:
            line += "  -- %s" % detail
        print(line)
    print("-" * 60)
    go = passed == total
    print("SUMMARY: %d/%d checks passed" % (passed, total))
    print("GATE: %s" % ("GO" if go else "NO-GO"))
    return 0 if go else 1


if __name__ == "__main__":
    sys.exit(main())
