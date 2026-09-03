from __future__ import annotations

from binggupack.pack import (
    batch_m1,
    candidate_mvp2,
    incoming_folder,
    op_m0,
    pack_consumer,
    person_pack_sync,
)
from binggupack.safety import p1_config, pii


def test_qcore_legacy_private_facades_keep_their_public_contracts():
    assert pack_consumer._safety_checks is pack_consumer.safety_checks
    assert isinstance(person_pack_sync._PACK_HEADER, list)
    assert person_pack_sync._PACK_HEADER[0].endswith("(owner) 의사결정 원칙·판단 온톨로지")
    assert p1_config._RECALL_KEYS == (
        "risk_mid_score", "risk_high_score", "preflight_max", "recall_limit",
        "preflight_rel_min", "semantic_recall_enabled", "trace_enabled",
    )
    assert pii.__all__ == ["batch_redact", "scan_residual_pii", "CANONICAL_MODULE"]
    assert pii.batch_redact is batch_m1.batch_redact
    assert pii.scan_residual_pii is batch_m1.scan_residual_pii


def test_qcore_strangler_facades_keep_wrapper_reexports():
    assert {"mvp1", "v011", "v07loader", "lkmap", "a0"} <= set(candidate_mvp2.__all__)
    assert {"batchm1", "mvp2"} <= set(incoming_folder.__all__)
    assert {"mvp1", "mvp2", "mp"} <= set(op_m0.__all__)
    assert incoming_folder.mvp2 is candidate_mvp2
    assert op_m0.mvp2 is candidate_mvp2
