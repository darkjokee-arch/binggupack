# -*- coding: utf-8 -*-
"""seed 내장 + silent fallback 제거 회귀 테스트 (Wire B / audit-v121-hardening).

검증 축:
  1) 바이트 동일성 — binggupack/data/semantic/*.jsonl == tests/fixtures/semantic/*.jsonl
     (_seed_sha 캐시키·hosted centroids seed_hash 가 이 바이트에 묶임 → 절대 drift 금지).
  2) _resolve_seed_path — importlib.resources(binggupack.data) 우선, 부재여도 raise 0.
  3) suggest_label_kind sentinel — enabled 이나 인스턴스화 실패(False) 시 크래시 없이 None.
  4) env_check seed 게이트 — Ollama 있고 seed 없으면 ready=False + [WARN] 표면화(silent drop 제거).
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import binggu_canonical_semantic as C            # noqa: E402
import binggu_env_check as E                      # noqa: E402
import binggu_hosted_centroid_gen as H           # noqa: E402
import binggu_semantic_shadow as S               # noqa: E402

_SEEDS = ["seed_canonical_5.jsonl", "seed_candidates.jsonl"]


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# --- 1) 바이트 동일성 가드 ---
def test_seed_byte_identity_data_vs_fixture():
    for name in _SEEDS:
        pkg = os.path.join(ROOT, "binggupack", "data", "semantic", name)
        fx = os.path.join(ROOT, "tests", "fixtures", "semantic", name)
        assert os.path.exists(pkg), "패키지 내장 seed 누락: %s" % pkg
        assert os.path.exists(fx), "fixture seed 누락: %s" % fx
        assert _sha(pkg) == _sha(fx), "바이트 drift: %s (캐시키/centroid seed_hash 파손)" % name


# --- 2) 리졸버 ---
def test_resolver_returns_existing_path():
    for mod, name in ((S, "seed_candidates.jsonl"), (H, "seed_canonical_5.jsonl")):
        p = mod._resolve_seed_path(name)
        assert isinstance(p, str)
        assert os.path.exists(p), "리졸버가 실존 경로 미반환: %s" % p


def test_resolver_never_raises_on_missing():
    # 존재하지 않는 이름이라도 str 반환(import 시점 평가 안전 — raise 0)
    p = S._resolve_seed_path("no_such_seed_xyz.jsonl")
    assert isinstance(p, str)


def test_module_seed_path_constants_exist():
    for p in (C.SEED_PATH, S.SEED_PATH, H.SEED_PATH):
        assert isinstance(p, str) and os.path.exists(p)


# --- 3) suggest_label_kind sentinel ---
def test_suggest_label_kind_false_sentinel_no_crash(monkeypatch):
    monkeypatch.setattr(C, "enabled", lambda: True)
    monkeypatch.setattr(C, "_INSTANCE", False)          # 인스턴스화 실패 sentinel
    # False.classify_kind AttributeError 없이 None 이어야 함
    assert C.suggest_label_kind("정상 문장이다") is None


def test_suggest_label_kind_instantiation_failure_sets_sentinel(monkeypatch):
    monkeypatch.setattr(C, "enabled", lambda: True)
    monkeypatch.setattr(C, "_INSTANCE", None)
    monkeypatch.setattr(C, "_SEED_WARN_EMITTED", False)

    def _boom(*a, **k):
        raise RuntimeError("seed missing (test)")

    monkeypatch.setattr(C, "CanonicalSemantic", _boom)
    assert C.suggest_label_kind("정상 문장이다") is None
    assert C._INSTANCE is False                          # 실패 sentinel 로 캐시(반복 재시도 억제)


def test_suggest_label_kind_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(C, "enabled", lambda: False)
    assert C.suggest_label_kind("아무 문장") is None


# --- 4) env_check seed 게이트 ---
def test_env_check_ollama_without_seed_not_ready():
    r = E.check_env(os_name="windows", ollama_probe=True, node_probe=False, seed_probe=False)
    assert r["semantic"]["ready"] is False
    assert r["semantic"]["ollama"] is True
    assert r["semantic"]["seed"] is False


def test_env_check_warn_surface_no_silent_drop():
    rep = E.render_report(
        E.check_env(os_name="windows", ollama_probe=True, node_probe=False, seed_probe=False))
    assert "[WARN]" in rep and "seed 누락" in rep


def test_env_check_operational_only_when_ollama_and_seed():
    rep = E.render_report(
        E.check_env(os_name="windows", ollama_probe=True, node_probe=True, seed_probe=True))
    assert "[ON]" in rep and "자동 켜짐" in rep


def test_env_check_seed_resolvable_true_in_repo():
    assert E._seed_resolvable() is True
