# -*- coding: utf-8 -*-
"""Cross-adapter E2E 회귀 — '모델 A 저장 → 모델 B recall/explain' 로컬 참조구현.

이 2-proc-shared-home 은 repo 최초 패턴이라 동작을 가정하지 않고 로컬+CI green 으로만 증명한다.
느림(공개 CLI subprocess 다수) — Windows 로컬은 pytest foreground 로만 검증(background 255 회피).
"""
from benchmark.contracts import Cap
from benchmark.reference.e2e_cross_adapter import _WRITE_CAPS, run_e2e
from benchmark.reference.reader_adapter import ReaderOnlyAdapter


def test_reader_declares_only_read_caps():
    caps = ReaderOnlyAdapter().capabilities()
    assert caps == {Cap.RECALL, Cap.RECALL_FRESH, Cap.EXPLAIN}
    assert Cap.SAVE not in caps
    assert not (caps & _WRITE_CAPS)  # write/mutation cap 미선언


def test_reader_rejects_write_ops():
    reader = ReaderOnlyAdapter()
    home = reader.bind_home(".")
    for op in (Cap.SAVE, Cap.SUPERSEDE, Cap.PAIR, Cap.UNAUTHORIZED_WRITE, Cap.INIT):
        try:
            reader.observe(home, op, text="x")
        except ValueError:
            continue
        raise AssertionError("reader 가 write op 을 거부하지 않음: %s" % op)


def test_reader_new_home_forbidden():
    try:
        ReaderOnlyAdapter().new_home("nonexistent")
    except NotImplementedError:
        return
    raise AssertionError("reader.new_home 이 금지되지 않음")


def test_cross_adapter_e2e_go():
    receipt = run_e2e()
    s = receipt["summary"]
    # 단일 버스·격리홈·운영홈 불변 하드게이트
    assert receipt["decision"] == "GO", receipt["results"]
    assert s["FAIL"] == 0, [r for r in receipt["results"] if r["verdict"] == "FAIL"]
    assert receipt["operating_fingerprint_equal"] is True
    assert receipt["kat_vectors_exit"] == 0

    by_id = {r["id"]: r for r in receipt["results"]}
    # 핵심 cross-adapter 단언들이 PASS
    for sid in ("E2E-A-SAVE", "E2E-NOAUTH", "E2E-B-RECALL", "E2E-B-EXPLAIN", "E2E-BUS", "E2E-KAT"):
        assert by_id[sid]["verdict"] == "PASS", by_id[sid]
    # Hosted 최종저장은 실 worker 미실행 — 정직하게 UNSUPPORTED
    assert by_id["E2E-HOSTED"]["execution_status"] == "UNSUPPORTED"
    assert by_id["E2E-HOSTED"]["verdict"] == "UNSUPPORTED"
