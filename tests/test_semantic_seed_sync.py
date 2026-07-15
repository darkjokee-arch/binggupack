# -*- coding: utf-8 -*-
"""semantic seed SSOT 도구(sync_semantic_seed.py) 회귀 — canonical 규칙 + 단방향 mirror 음성 테스트.

owner 확정(issue #52): 아래 변형은 모두 --check 에서 실패해야 한다 —
CRLF · 혼합 LF/CRLF · UTF-8 BOM · 마지막 LF 누락 · 마지막 LF 2개+ · source↔fixture 내용 차 ·
레코드 순서 차 · invalid JSONL · fixture 누락 · source 누락 · (source·fixture 둘 다 CRLF 여도 PASS 금지).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sync_semantic_seed as S  # noqa: E402

_GOOD = b'{"a": 1}\n{"b": 2}\n'


# ── canonical_violations 단위 ──
def test_canonical_pass():
    assert S.canonical_violations(_GOOD) == []


def test_crlf_detected():
    assert "cr-byte" in S.canonical_violations(b'{"a": 1}\r\n')


def test_mixed_eol_detected():
    assert "cr-byte" in S.canonical_violations(b'{"a": 1}\n{"b": 2}\r\n')


def test_bom_detected():
    assert "utf8-bom" in S.canonical_violations(b'\xef\xbb\xbf{"a": 1}\n')


def test_no_final_lf_detected():
    assert "no-final-lf" in S.canonical_violations(b'{"a": 1}')


def test_multiple_final_lf_detected():
    assert "multiple-final-lf" in S.canonical_violations(b'{"a": 1}\n\n')


def test_invalid_json_detected():
    assert any(v.startswith("invalid-json") for v in S.canonical_violations(b'not json\n'))


def test_non_object_detected():
    assert any(v.startswith("not-json-object") for v in S.canonical_violations(b'[1,2,3]\n'))


# ── check/write 통합(임시 홈) ──
def _setup(tmp_path, monkeypatch, data_map, fixture_map):
    d = tmp_path / "data"
    f = tmp_path / "fix"
    d.mkdir()
    f.mkdir()
    for name in S.SEEDS:
        (d / name).write_bytes(data_map.get(name, _GOOD))
        (f / name).write_bytes(fixture_map.get(name, _GOOD))
    monkeypatch.setattr(S, "DATA_DIR", str(d))
    monkeypatch.setattr(S, "FIXTURE_DIR", str(f))
    return d, f


def test_check_pass_when_identical_canonical(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {}, {})
    assert S.check(verbose=False) is True


def test_check_fail_on_content_drift(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {}, {"seed_canonical_5.jsonl": b'{"a": 999}\n{"b": 2}\n'})
    assert S.check(verbose=False) is False


def test_check_fail_on_record_order_diff(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           {"seed_canonical_5.jsonl": b'{"a": 1}\n{"b": 2}\n'},
           {"seed_canonical_5.jsonl": b'{"b": 2}\n{"a": 1}\n'})
    assert S.check(verbose=False) is False


def test_check_fail_when_both_crlf(tmp_path, monkeypatch):
    # source·fixture 가 똑같이 CRLF 여도 source canonicality 로 FAIL (byte identity 만으로 PASS 금지)
    crlf = b'{"a": 1}\r\n{"b": 2}\r\n'
    _setup(tmp_path, monkeypatch,
           {"seed_canonical_5.jsonl": crlf, "seed_candidates.jsonl": crlf},
           {"seed_canonical_5.jsonl": crlf, "seed_candidates.jsonl": crlf})
    assert S.check(verbose=False) is False


def test_check_fail_on_missing_fixture(tmp_path, monkeypatch):
    d, f = _setup(tmp_path, monkeypatch, {}, {})
    os.remove(f / "seed_canonical_5.jsonl")
    assert S.check(verbose=False) is False


def test_check_fail_on_missing_source(tmp_path, monkeypatch):
    d, f = _setup(tmp_path, monkeypatch, {}, {})
    os.remove(d / "seed_canonical_5.jsonl")
    assert S.check(verbose=False) is False


def test_write_mirrors_source_to_fixture_and_check_passes(tmp_path, monkeypatch):
    # source canonical, fixture drift → --write 후 fixture 가 source 와 동일해지고 --check PASS
    _setup(tmp_path, monkeypatch, {}, {"seed_canonical_5.jsonl": b'{"a": 999}\n'})
    assert S.write(verbose=False) is True
    assert S.check(verbose=False) is True


def test_write_refuses_non_canonical_source(tmp_path, monkeypatch):
    # source 가 CRLF(canonical 위반)면 --write 중단(fixture 오염 방지)
    _setup(tmp_path, monkeypatch, {"seed_canonical_5.jsonl": b'{"a": 1}\r\n'}, {})
    assert S.write(verbose=False) is False


def test_empty_file_detected():
    assert S.canonical_violations(b"") == ["empty-file"]
    assert S.canonical_violations(b"   \n") == ["empty-file"]


def test_blank_line_detected():
    assert any(v.startswith("blank-line")
               for v in S.canonical_violations(b'{"a": 1}\n\n{"b": 2}\n'))


# ── check_hosted 음성/양성(모듈 상수 monkeypatch) ──
def _hosted_setup(tmp_path, monkeypatch, data_bytes, seed_hash):
    import json as _j
    d = tmp_path / "data"
    d.mkdir()
    (d / S.HOSTED_SEED).write_bytes(data_bytes)
    cent = tmp_path / "centroids.json"
    cent.write_text(_j.dumps({"seed_hash": seed_hash}), encoding="utf-8")
    monkeypatch.setattr(S, "DATA_DIR", str(d))
    monkeypatch.setattr(S, "CENTROIDS", str(cent))
    return d, cent


def test_check_hosted_pass_on_match(tmp_path, monkeypatch):
    import hashlib
    b = b'{"a": 1}\n'
    _hosted_setup(tmp_path, monkeypatch, b, hashlib.sha256(b).hexdigest()[:16])
    assert S.check_hosted(verbose=False) is True


def test_check_hosted_fail_on_mismatch(tmp_path, monkeypatch):
    _hosted_setup(tmp_path, monkeypatch, b'{"a": 1}\n', "deadbeef00000000")
    assert S.check_hosted(verbose=False) is False


def test_check_hosted_fail_on_missing_centroids(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / S.HOSTED_SEED).write_bytes(b'{"a": 1}\n')
    monkeypatch.setattr(S, "DATA_DIR", str(d))
    monkeypatch.setattr(S, "CENTROIDS", str(tmp_path / "nonexistent.json"))
    assert S.check_hosted(verbose=False) is False
