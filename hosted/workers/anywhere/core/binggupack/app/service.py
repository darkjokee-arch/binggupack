# -*- coding: utf-8 -*-
"""Binggu Anywhere — transport-independent service logic (pure, testable).

Sits between the snapshot artifact layer (snapshot.py) and the read core
(read_core.PackService). Contains NO network/R2/KV/env — the Cloudflare worker
entry binds those. Everything here is exercised by conformance + unit tests so the
remote service semantics equal the local read core (owner §9 parity).

  * dispatch(svc, tool, args)             — the 5 read tools, exact allowlist.
  * invoke_on_root(root, tool, args)      — run a tool over an already-materialized repo root.
  * materialize(snapshots, root)          — safely extract verified snapshot bytes into a root.
  * validate_and_canonicalize(tar_bytes)  — owner upload server-side revalidation + canonicalize.
"""
import os
import tempfile

from binggupack.app import models as M
from binggupack.app import snapshot as S
from binggupack.app.read_core import PackRepository, PackService

READ_TOOLS = ("pack_list", "pack_summary", "evidence_search", "node_edge_lookup", "handoff_context")


def dispatch(svc, tool, args):
    """Run exactly one of the 5 read tools. Unknown tool -> error dict (no exception leak)."""
    a = args or {}
    if tool == "pack_list":
        return svc.list_packs(cursor=a.get("cursor"), limit=a.get("limit", M.LIST_LIMIT_DEFAULT))
    if tool == "pack_summary":
        return svc.get_pack_summary(a.get("pack_id"))
    if tool == "evidence_search":
        return svc.search_evidence(a.get("pack_id"), a.get("query"), limit=a.get("limit", M.SEARCH_LIMIT_DEFAULT))
    if tool == "node_edge_lookup":
        return svc.lookup_node_edges(a.get("pack_id"), node_id=a.get("node_id"), keyword=a.get("keyword"))
    if tool == "handoff_context":
        return svc.build_handoff_context(a.get("pack_id"), topic=a.get("topic"),
                                         max_nodes=a.get("max_nodes", M.HANDOFF_MAX_NODES_DEFAULT))
    return {"error_code": "UNKNOWN_TOOL", "message": "unknown tool"}


def invoke_on_root(root, tool, args):
    """Instantiate PackService over a materialized parent root and run one tool."""
    if tool not in READ_TOOLS:
        return {"error_code": "UNKNOWN_TOOL", "message": "unknown tool"}
    svc = PackService(PackRepository(root))
    return dispatch(svc, tool, args)


def materialize(snapshots, root):
    """Extract verified snapshot tars into ``root`` (one pack each). READ-ONLY on inputs.

    ``snapshots`` is an iterable of already-digest-verified tar byte strings. Corrupt or
    unsafe artifacts are skipped (never served, no partial service). Returns the pack_ids
    successfully materialized.
    """
    ok = []
    for tar in snapshots:
        try:
            pid, _ = S.safe_extract_into(tar, root)
            ok.append(pid)
        except S.SnapshotError:
            continue
    return ok


def validate_and_canonicalize(tar_bytes):
    """Owner upload server-side revalidation (owner §7).

    Returns on success:
      {"ok": True, "pack_id", "canonical_tar", "digest", "counts"}
    On rejection:
      {"ok": False, "reason": <safe code>}
    Never raises for unsafe input; folds SnapshotError / read-core rejection into a reason.
    """
    if not isinstance(tar_bytes, (bytes, bytearray)):
        return {"ok": False, "reason": "invalid_bytes"}
    if len(tar_bytes) > S.SNAPSHOT_MAX_BYTES:
        return {"ok": False, "reason": "snapshot_too_large"}
    stage = tempfile.mkdtemp(prefix="publish_")
    try:
        pack_id, _members = S.safe_extract_into(bytes(tar_bytes), stage)
    except S.SnapshotError as e:
        return {"ok": False, "reason": "unsafe_pack:%s" % str(e)}
    # read-core validation: validate_pack STOP / oversize / PII all fold into get_pack_summary.
    svc = PackService(PackRepository(stage))
    summ = svc.get_pack_summary(pack_id)
    if summ.get("error_code"):
        return {"ok": False, "reason": "failed_validation"}
    pack_dir = os.path.join(stage, pack_id)
    try:
        canon, digest = S.make_pack_snapshot(pack_dir, pack_id)
    except S.SnapshotError as e:
        return {"ok": False, "reason": "canonicalize_failed:%s" % str(e)}
    return {"ok": True, "pack_id": pack_id, "canonical_tar": canon, "digest": digest,
            "counts": summ.get("counts", {})}
