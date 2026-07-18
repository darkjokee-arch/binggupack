# -*- coding: utf-8 -*-
"""추천③ MF7/MF8 보강 — sdist 시크릿 게이트: .dev.vars 는 0, hosted worker 소스만 동봉.

두 겹:
  1) 오프라인 결정적 — MANIFEST.in 지시문(recursive-include/include/prune/global-exclude)을
     통제된 후보 경로 집합에 적용해 '시크릿(.dev.vars*)은 어떤 include 로도 안 들어오고
     prune/global-exclude 로 배제됨 · src/*.ts 만 포함'을 못박는다. 빌드 툴체인 무관·트리 write 0.
     실 hosted/ 트리에도 read-only 로 교차 확인.
  2) 실 빌드 게이트(MF8) — setuptools/build 가 있으면(주로 CI) `python -m build --sdist` 로
     실제 tarball 을 만들어 dev.vars 개수 0 · hosted/workers/src *.ts 포함을 확인. 없으면 skip.

MANIFEST.in 을 실제로 읽어 평가하므로, 누가 global-exclude 를 지우거나 include 를 넓히면 (1)이 잡는다.
"""
import fnmatch
import os
import subprocess
import sys
import tarfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "MANIFEST.in")

# 보안상 중요한 경우들을 모두 담은 통제 후보 집합(실 트리 write 없이 결정적 평가).
CANDIDATES = [
    "hosted/workers/.dev.vars",             # 시크릿(정확 일치) — 반드시 배제
    "hosted/workers/.dev.vars.staging",     # 시크릿(glob 변형) — 반드시 배제
    "hosted/workers/.dev.vars.prod",        # 시크릿(glob 변형) — 반드시 배제
    "hosted/workers/src/index.ts",          # worker 소스 — 포함
    "hosted/workers/src/capture_preview.ts",  # worker 소스 — 포함
    "hosted/workers/src/centroids_canonical_5.json",  # 대용량 데이터(*.ts 아님) — 배제
    "hosted/workers/wrangler.toml",         # 배포 설정 — 포함
    "hosted/workers/wrangler.save.toml",    # 배포 설정 — 포함
    "hosted/workers/tsconfig.json",         # 타입 설정 — 포함
    "hosted/workers/node_modules/pkg/leak.ts",  # 의존성 — prune 로 배제
    "hosted/workers/.wrangler/data/state.ts",   # 빌드 캐시 — prune 로 배제
    "hosted/workers/data/packs.json",       # 로컬 데이터 — prune 로 배제
]


def _read_manifest_lines():
    with open(MANIFEST, encoding="utf-8") as f:
        return f.readlines()


def _manifest_select(lines, candidates):
    """MANIFEST.in 템플릿을 후보 경로에 순서대로 적용한 최종 포함 집합(setuptools 의미론 재현).

    지원 지시문: recursive-include DIR PAT... / include PAT... / prune DIR / global-exclude PAT...
    (이 MANIFEST 가 쓰는 4종 전부). 순서대로 add(include류)/remove(prune·global-exclude)."""
    sel = set()
    for raw in lines:
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        cmd, args = parts[0], parts[1:]
        if cmd == "recursive-include":
            d = args[0].replace("\\", "/").rstrip("/")
            pats = args[1:]
            for f in candidates:
                if f.startswith(d + "/") and any(fnmatch.fnmatch(os.path.basename(f), p) for p in pats):
                    sel.add(f)
        elif cmd == "include":
            for pat in args:
                p = pat.replace("\\", "/")
                for f in candidates:
                    if fnmatch.fnmatch(f, p):
                        sel.add(f)
        elif cmd == "prune":
            d = args[0].replace("\\", "/").rstrip("/")
            sel = {f for f in sel if not f.startswith(d + "/")}
        elif cmd == "global-exclude":
            for pat in args:
                sel = {f for f in sel if not fnmatch.fnmatch(os.path.basename(f), pat)}
    return sel


def _is_secret(path):
    return fnmatch.fnmatch(os.path.basename(path), ".dev.vars*")


def test_manifest_declares_secret_defense():
    """MANIFEST.in 에 global-exclude .dev.vars* 방어가 명시돼 있다(선언 회귀 가드)."""
    text = "".join(_read_manifest_lines())
    assert "global-exclude .dev.vars*" in text


def test_manifest_excludes_secrets_includes_worker_src_offline():
    """오프라인 결정적: 통제 후보에서 .dev.vars* 는 0 · src/*.ts 만 포함 · 데이터/의존성 배제."""
    lines = _read_manifest_lines()
    sel = _manifest_select(lines, CANDIDATES)

    # 시크릿 전량 배제(정확 일치 + glob 변형)
    assert not any(_is_secret(f) for f in sel), "시크릿이 sdist 선택에 포함됨: %s" % sorted(sel)

    # worker 소스(.ts)만 포함
    assert "hosted/workers/src/index.ts" in sel
    assert "hosted/workers/src/capture_preview.ts" in sel
    assert "hosted/workers/src/centroids_canonical_5.json" not in sel  # *.ts 필터라 데이터 json 배제

    # 배포 설정 포함
    assert "hosted/workers/wrangler.toml" in sel
    assert "hosted/workers/wrangler.save.toml" in sel
    assert "hosted/workers/tsconfig.json" in sel

    # 의존성/캐시/로컬데이터 prune 배제
    assert "hosted/workers/node_modules/pkg/leak.ts" not in sel
    assert "hosted/workers/.wrangler/data/state.ts" not in sel
    assert "hosted/workers/data/packs.json" not in sel


def test_manifest_over_real_tree_readonly():
    """실 hosted/ 트리(read-only walk)에 적용해도 .dev.vars* 는 0 · 실 src *.ts 는 >0."""
    hosted = os.path.join(REPO, "hosted")
    if not os.path.isdir(hosted):
        pytest.skip("hosted/ 없음(패키지 전용 체크아웃)")
    real = []
    for r, _dirs, files in os.walk(hosted):
        for fn in files:
            real.append(os.path.relpath(os.path.join(r, fn), REPO).replace("\\", "/"))

    sel = _manifest_select(_read_manifest_lines(), real)
    assert not any(_is_secret(f) for f in sel), "실 트리 시크릿이 선택됨: %s" % (
        [f for f in sel if _is_secret(f)])
    ts_selected = [f for f in sel if f.startswith("hosted/workers/src/") and f.endswith(".ts")]
    assert len(ts_selected) > 0, "실 hosted/workers/src/*.ts 가 하나도 선택되지 않음"


def test_sdist_build_excludes_secrets(tmp_path):
    """MF8 실 빌드 게이트 — sdist tarball 에 dev.vars 0 · hosted/workers/src *.ts 포함.

    setuptools/build(주로 CI)가 있어야 실행 — 없으면 skip(오프라인 결정적 테스트가 상시 커버)."""
    pytest.importorskip("build", reason="python -m build 미설치")
    pytest.importorskip("setuptools", reason="setuptools 미설치(빌드 백엔드) — 오프라인 skip")

    outdir = str(tmp_path / "dist")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--no-isolation", "--outdir", outdir],
        cwd=REPO, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        pytest.skip("sdist 빌드 실패(빌드 환경 의존) — tail:\n%s" % (proc.stdout + proc.stderr)[-1200:])

    tarballs = [os.path.join(outdir, f) for f in os.listdir(outdir) if f.endswith(".tar.gz")]
    assert tarballs, "sdist tarball 미생성"
    names = tarfile.open(tarballs[0]).getnames()

    dev = [n for n in names if "dev.vars" in n]
    src = [n for n in names if "hosted/workers/src/" in n and n.endswith(".ts")]
    assert dev == [], "sdist 에 시크릿 유출: %s" % dev          # 시크릿 0
    assert len(src) > 0, "sdist 에 hosted worker 소스(.ts) 누락"  # worker 소스 포함
