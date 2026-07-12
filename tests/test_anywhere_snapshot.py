# -*- coding: utf-8 -*-
"""Binggu Anywhere — snapshot artifact safety + determinism (synthetic, read-only)."""
import io
import os
import tarfile
import tempfile

import pytest

from binggupack.app import snapshot as S
from binggupack.app import conformance as C


def _valid_pack(root, pid="fx_snap"):
    C._write_pack(root, pid,
                  [C._node("node:a", "마진이 낮으면 응찰을 보류한다.", ["E1"], "판단"),
                   C._node("node:b", "기초금액은 발주기관 기준 금액이다.", ["E2"])],
                  [C._edge("edge:a", "node:b", "node:a", "supports_judgment", ["E1"])],
                  [C._ev("E1"), C._ev("E2")])
    return os.path.join(root, pid)


def _tar_with(members):
    """members: list of (name, size_bytes_or_type). Build a raw tar for extraction tests."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, kind in members:
            ti = tarfile.TarInfo(name)
            if kind == "symlink":
                ti.type = tarfile.SYMTYPE
                ti.linkname = "target"
                tf.addfile(ti)
            elif isinstance(kind, int):
                ti.size = kind
                tf.addfile(ti, io.BytesIO(b"x" * kind))
            else:
                data = kind if isinstance(kind, bytes) else b"x"
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_snapshot_roundtrip():
    src = tempfile.mkdtemp()
    _valid_pack(src)
    tar, digest = S.make_pack_snapshot(os.path.join(src, "fx_snap"), "fx_snap")
    dst = tempfile.mkdtemp()
    pid, members = S.safe_extract_into(tar, dst)
    assert pid == "fx_snap"
    assert "fx_snap/manifest.json" in members
    assert os.path.isfile(os.path.join(dst, "fx_snap", "manifest.json"))


def test_snapshot_deterministic_digest():
    # immutable_revision: identical content -> identical digest (idempotent republish basis)
    src = tempfile.mkdtemp()
    _valid_pack(src)
    _, d1 = S.make_pack_snapshot(os.path.join(src, "fx_snap"), "fx_snap")
    _, d2 = S.make_pack_snapshot(os.path.join(src, "fx_snap"), "fx_snap")
    assert d1 == d2 and len(d1) == 64


def test_reject_traversal_member():
    with pytest.raises(S.SnapshotError):
        S.safe_extract_into(_tar_with([("fx/../../evil.txt", b"x")]), tempfile.mkdtemp())


def test_reject_absolute_member():
    with pytest.raises(S.SnapshotError):
        S.safe_extract_into(_tar_with([("/etc/evil", b"x")]), tempfile.mkdtemp())


def test_reject_symlink_member():
    with pytest.raises(S.SnapshotError):
        S.safe_extract_into(_tar_with([("fx/manifest.json", "symlink")]), tempfile.mkdtemp())


def test_reject_duplicate_member():
    with pytest.raises(S.SnapshotError):
        S.safe_extract_into(
            _tar_with([("fx/manifest.json", b"a"), ("fx/manifest.json", b"b")]),
            tempfile.mkdtemp())


def test_reject_multiple_pack_dirs():
    with pytest.raises(S.SnapshotError):
        S.safe_extract_into(
            _tar_with([("fx1/manifest.json", b"a"), ("fx2/manifest.json", b"b")]),
            tempfile.mkdtemp())


def test_reject_disallowed_member():
    with pytest.raises(S.SnapshotError):
        S.safe_extract_into(_tar_with([("fx/secret.env", b"x")]), tempfile.mkdtemp())


def test_reject_oversize_snapshot():
    big = b"P" * (S.SNAPSHOT_MAX_BYTES + 1)
    with pytest.raises(S.SnapshotError):
        S.safe_extract_into(big, tempfile.mkdtemp())


def test_tenant_hash_deterministic_and_distinct():
    a1 = S.tenant_hash("tenantA")
    a2 = S.tenant_hash("tenantA")
    b = S.tenant_hash("tenantB")
    assert a1 == a2 and a1 != b and len(a1) == 32
    with pytest.raises(S.SnapshotError):
        S.tenant_hash("")
