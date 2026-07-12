# -*- coding: utf-8 -*-
"""Binggu Anywhere — owner upload CLI gate + safety (synthetic, no network)."""
import os
import tempfile

import pytest

from binggupack.app import upload as U
from binggupack.app import conformance as C


def _pack_dir(pid="up_v1"):
    root = tempfile.mkdtemp()
    C._write_pack(root, pid,
                  [C._node("n:a", "예정가격 산정은 복수예비가격 방식을 쓴다.", ["E1"], "개념"),
                   C._node("n:b", "낙찰하한율 미만은 무효처리한다.", ["E2"], "판단")],
                  [C._edge("e1", "n:a", "n:b", "supports_judgment", ["E1"])],
                  [C._ev("E1"), C._ev("E2")])
    return os.path.join(root, pid)


def test_prepare_valid_preview():
    preview, tar = U.prepare(_pack_dir())
    assert preview["pack_id"] == "up_v1"
    assert preview["counts"] == {"nodes": 2, "edges": 1, "evidence": 2}
    assert preview["scan_verdict"] in ("clean", "CLEAN")
    assert len(tar) > 0 and preview["revision_digest"]


def test_upload_requires_owner_local_gate(monkeypatch):
    # noninteractive_upload_blocked: no TTY, no injected confirm -> refused
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: False)})())
    with pytest.raises(U.UploadError) as ei:
        U.run(_pack_dir(), "https://example.invalid", dry_run=False, out=lambda s: None)
    assert "interactive" in str(ei.value)


def test_raw_ledger_upload_blocked():
    # a sqlite file (or any non-directory) cannot be uploaded
    fd, path = tempfile.mkstemp(suffix="ledger.sqlite")
    os.close(fd)
    with pytest.raises(U.UploadError):
        U.prepare(path)


def test_raw_capture_upload_blocked():
    # an arbitrary directory that is not a canonical pack fails local validation
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "capture.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"raw":"conversation"}\n')
    with pytest.raises(U.UploadError):
        U.prepare(d)


def test_dry_run_performs_no_upload(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(U, "do_upload", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    res = U.run(_pack_dir(), "https://example.invalid", dry_run=True, out=lambda s: None)
    assert res["dry_run"] is True and called["n"] == 0


def test_confirm_mismatch_aborts(monkeypatch):
    monkeypatch.setenv(U.TOKEN_ENV, "unused")
    monkeypatch.setattr(U, "do_upload", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not upload")))
    with pytest.raises(U.UploadError) as ei:
        U.run(_pack_dir(), "https://example.invalid", dry_run=False, out=lambda s: None,
              tty_confirm=lambda prompt: "WRONG")
    assert "confirmation" in str(ei.value)


def test_upload_success_with_injected_confirm(monkeypatch):
    monkeypatch.setenv(U.TOKEN_ENV, "test-cred-value")
    captured = {}

    def fake_upload(endpoint, token, tar_bytes, timeout=180):
        captured["endpoint"] = endpoint
        captured["has_token"] = bool(token)
        return 200, {"publish_status": "ok", "pack_id": "up_v1",
                     "revision_digest": "d" * 64, "size": len(tar_bytes),
                     "counts": {"nodes": 2, "edges": 1, "evidence": 2},
                     "idempotent": False,
                     # server must never leak these; the client must not echo them either
                     "storage_key": "tenants/abc/snapshots/x.pack"}

    lines = []
    monkeypatch.setattr(U, "do_upload", fake_upload)
    res = U.run(_pack_dir(), "https://gw.example", dry_run=False, out=lines.append,
                tty_confirm=lambda prompt: "up_v1")
    assert res["result"]["publish_status"] == "ok"
    # response projection: raw storage path never printed
    joined = "\n".join(lines)
    assert "storage_key" not in joined and "tenants/" not in joined
    assert captured["has_token"] and captured["endpoint"] == "https://gw.example"


def test_token_required_from_env(monkeypatch):
    monkeypatch.delenv(U.TOKEN_ENV, raising=False)
    with pytest.raises(U.UploadError) as ei:
        U.run(_pack_dir(), "https://gw.example", dry_run=False, out=lambda s: None,
              tty_confirm=lambda prompt: "up_v1")
    assert U.TOKEN_ENV in str(ei.value)
