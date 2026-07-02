"""BingguPack ZIP -> OpenCrab 로컬 역인제스트 실행기 (출구 교체).

빌더(cloud_pack_export / publish_p6)가 만든 ZIP을 클라우드에 업로드하는 대신,
PC 안의 로컬 OpenCrab(`opencrab ingest`)으로 역인제스트한다.

설계 원칙 (4cli C 검토 반영):
- 로컬 ingest도 OpenCrab store에 영속 write하는 비가역 행위다. "로컬이라 안전" 금지.
  -> 기본 dry-run(명령 구성만, 실행 0). 실제 실행은 execute=True 명시 + 호출자 GO 필요.
- synthetic_fixture pack은 실 적재 거부(빌더 검증용 dry-run 산출물이라 실 store 오염 금지).
- opencrab 실행 파일은 하드코딩 금지 -> 환경변수 OPENCRAB_EXE 우선, 없으면 후보 경로 탐색.
- 클라우드/DB/네트워크 전송 0. 오직 로컬 CLI 호출만.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

# ZIP 안에서 ingest 진입에 필요한 최소 산출물(빌더 산출 구조 정합)
REQUIRED_ENTRIES = ("manifest.json", "neo4j/opencrab_ingest.jsonl",
                    "graph/nodes.jsonl", "graph/edges.jsonl")
# opencrab ingest 대상 확장자(jsonl/json/md). 기본 .txt,.md,.py 외에 jsonl/json 추가.
INGEST_EXTENSIONS = ".jsonl,.json,.md"

# opencrab 실행 파일 후보(환경마다 다름 — env 우선, 절대 하드코딩 의존 X)
_EXE_CANDIDATES = (
    os.path.join(os.path.expanduser("~"), "OpenCrab", ".venv", "Scripts", "opencrab.exe"),
    os.path.join(os.path.expanduser("~"), "OpenCrab", ".venv", "bin", "opencrab"),
    "opencrab",  # PATH 등록 시
)


def find_opencrab_exe():
    """OPENCRAB_EXE env 우선, 없으면 후보 경로/PATH 탐색. 못 찾으면 None."""
    env = os.environ.get("OPENCRAB_EXE")
    if env and (os.path.exists(env) or shutil.which(env)):
        return env
    for cand in _EXE_CANDIDATES:
        if os.path.isabs(cand) and os.path.exists(cand):
            return cand
        if not os.path.isabs(cand) and shutil.which(cand):
            return cand
    return None


def extract_zip(zip_path, dest_dir=None):
    """ZIP을 dest_dir(미지정 시 temp)에 해제. 해제 경로 반환."""
    if not os.path.exists(zip_path):
        raise FileNotFoundError("zip not found: %s" % zip_path)
    dest = dest_dir or tempfile.mkdtemp(prefix="binggu_ingest_")
    with zipfile.ZipFile(zip_path) as zf:
        # zip slip 방어 — 절대경로/상위탈출 엔트리 거부
        for name in zf.namelist():
            norm = os.path.normpath(name)
            if norm.startswith("..") or os.path.isabs(norm):
                raise ValueError("unsafe zip entry: %s" % name)
        zf.extractall(dest)
    return dest


def validate_extracted(extract_dir):
    """ingest 진입 전 검증: 필수 산출물 존재 + manifest data_class 파싱.

    반환 dict: ok / missing / data_class / release_status / is_synthetic
    """
    missing = [e for e in REQUIRED_ENTRIES
               if not os.path.exists(os.path.join(extract_dir, e))]
    data_class, release_status = None, None
    mpath = os.path.join(extract_dir, "manifest.json")
    if os.path.exists(mpath):
        try:
            with open(mpath, encoding="utf-8") as f:
                m = json.load(f)
            data_class = m.get("data_class")
            release_status = m.get("release_status")
        except Exception:  # noqa
            pass
    is_synthetic = (data_class == "synthetic_fixture")
    return {"ok": not missing, "missing": missing, "data_class": data_class,
            "release_status": release_status, "is_synthetic": is_synthetic}


def build_ingest_command(exe, extract_dir):
    """opencrab ingest 명령 리스트 구성(실행 X). dry-run 표시·로그용."""
    return [exe, "ingest", extract_dir, "-r", "-e", INGEST_EXTENSIONS]


def ingest_zip(zip_path, execute=False, allow_synthetic=False, extract_dir=None):
    """ZIP -> 로컬 OpenCrab 역인제스트.

    execute=False(기본): 해제 + 검증 + 명령 구성까지만(실행 0). dry-run.
    execute=True: 실제 opencrab ingest 호출(비가역 write). 호출자 GO 책임.
    allow_synthetic=False: synthetic_fixture pack은 실 적재 차단(dry-run으로 강등).
    """
    result = {"zip": zip_path, "executed": False, "cloud_upload": False,
              "db_insert": False, "verdict": None}

    exe = find_opencrab_exe()
    result["opencrab_exe"] = exe

    try:
        ex_dir = extract_zip(zip_path, extract_dir)
    except Exception as e:  # noqa
        result["verdict"] = "BLOCK"
        result["reason"] = "extract_failed:%s" % str(e)[:80]
        return result
    result["extract_dir"] = ex_dir

    val = validate_extracted(ex_dir)
    result["validation"] = val
    if not val["ok"]:
        result["verdict"] = "BLOCK"
        result["reason"] = "missing_required:%s" % ",".join(val["missing"])
        return result

    cmd = build_ingest_command(exe or "opencrab", ex_dir)
    result["ingest_command"] = " ".join(cmd)

    # synthetic 실 적재 차단
    if val["is_synthetic"] and execute and not allow_synthetic:
        result["verdict"] = "DRYRUN"
        result["reason"] = "synthetic_fixture_execute_blocked"
        return result

    if not execute:
        result["verdict"] = "DRYRUN"
        result["reason"] = "execute=False (명령 구성만)"
        return result

    # 실제 실행 경로 — opencrab 필요
    if not exe:
        result["verdict"] = "BLOCK"
        result["reason"] = "opencrab_exe_not_found (set OPENCRAB_EXE)"
        return result

    exe_cwd = os.path.dirname(os.path.dirname(os.path.dirname(exe))) \
        if os.path.isabs(exe) else None  # opencrab_data 상대경로 해소용
    try:
        p = subprocess.run(cmd, cwd=exe_cwd, capture_output=True, text=True, timeout=600)
    except Exception as e:  # noqa
        result["verdict"] = "BLOCK"
        result["reason"] = "ingest_run_error:%s" % str(e)[:80]
        return result
    result["executed"] = True
    result["rc"] = p.returncode
    out = (p.stdout or "") + (p.stderr or "")
    result["ingest_tail"] = out[-400:]
    result["verdict"] = "DONE" if p.returncode == 0 else "BLOCK"
    if p.returncode != 0:
        result["reason"] = "ingest_nonzero_rc"
    return result


# ----------------------------------------------------------------- selftest
def _selftest():
    gates = []

    def chk(name, cond):
        gates.append((name, bool(cond)))

    # 합성 ZIP 생성(빌더 산출 구조 모방) — 실 store 무관, temp 전용
    tmp = tempfile.mkdtemp(prefix="binggu_ingest_selftest_")
    zpath = os.path.join(tmp, "toy_pack.zip")
    manifest = {"data_class": "synthetic_fixture", "release_status": "degraded",
                "format": "opencrab-pack-v1"}
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("neo4j/opencrab_ingest.jsonl", '{"op":"MERGE_NODE","id":"n1"}\n')
        zf.writestr("graph/nodes.jsonl", '{"id":"n1","sentence":"toy"}\n')
        zf.writestr("graph/edges.jsonl", '{"from":"n1","to":"n1"}\n')

    # T1 해제 성공 + 필수 검증 통과
    ex = extract_zip(zpath)
    val = validate_extracted(ex)
    chk("T1_extract_validate", val["ok"] and val["is_synthetic"])

    # T2 dry-run 기본(execute=False) — 실행 0
    r2 = ingest_zip(zpath, execute=False)
    chk("T2_default_dryrun", r2["verdict"] == "DRYRUN" and not r2["executed"]
        and not r2["cloud_upload"] and not r2["db_insert"])

    # T3 synthetic + execute=True -> 실 적재 차단(DRYRUN 강등)
    r3 = ingest_zip(zpath, execute=True, allow_synthetic=False)
    chk("T3_synthetic_execute_blocked",
        r3["verdict"] == "DRYRUN" and r3.get("reason") == "synthetic_fixture_execute_blocked"
        and not r3["executed"])

    # T4 명령 구성에 opencrab ingest + 확장자 포함
    chk("T4_command_shape",
        "ingest" in r2["ingest_command"] and INGEST_EXTENSIONS in r2["ingest_command"])

    # T5 필수 산출물 누락 ZIP -> BLOCK
    bad = os.path.join(tmp, "bad.zip")
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", "{}")
    rb = ingest_zip(bad, execute=False)
    chk("T5_missing_required_block", rb["verdict"] == "BLOCK"
        and rb["reason"].startswith("missing_required"))

    # T6 zip slip 방어
    slip = os.path.join(tmp, "slip.zip")
    with zipfile.ZipFile(slip, "w") as zf:
        zf.writestr("../evil.txt", "x")
    rs = ingest_zip(slip, execute=False)
    chk("T6_zip_slip_block", rs["verdict"] == "BLOCK")

    # T7 find_opencrab_exe 반환형(경로 문자열 or None) — 환경 비의존
    exe = find_opencrab_exe()
    chk("T7_find_exe_type", exe is None or isinstance(exe, str))

    passed = sum(1 for _, ok in gates if ok)
    total = len(gates)
    for name, ok in gates:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("=== %d/%d ===" % (passed, total))
    print("GATE=GO" if passed == total else "GATE=FAIL")
    return 0 if passed == total else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return _selftest()
    if not args:
        print("usage: localbinggu_ingest_executor.py <pack.zip> [--execute] [--allow-synthetic]")
        print("       localbinggu_ingest_executor.py --selftest")
        return 2
    zip_path = args[0]
    execute = "--execute" in args
    allow_synth = "--allow-synthetic" in args
    res = ingest_zip(zip_path, execute=execute, allow_synthetic=allow_synth)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["verdict"] in ("DRYRUN", "DONE") else 1


if __name__ == "__main__":
    sys.exit(main())
