# -*- coding: utf-8 -*-
# Binggu Anywhere — PRIVATE Python core worker (R2 FFI adapter).
#
# Runs the v1.21-A read core directly. Reached ONLY via a Service Binding from the TS
# gateway; NO public route/domain. The gateway does auth + tenant derivation and passes
# an already-authorized (tenant, op, ...) envelope. Pure service logic lives in
# binggupack.app.service; this file only binds R2 + the workers Response.
#
#   op=invoke  : READ-ONLY. materialize the tenant's verified snapshots -> service.invoke_on_root.
#                No R2/KV put/delete on this path.
#   op=publish : OWNER WRITE. service.validate_and_canonicalize -> immutable snapshot -> current.json last.
#   op=selfcheck: in-worker conformance harness.
#
# R2 layout (owner §3):
#   tenants/<tenant-hash>/snapshots/<sha256>.pack   (immutable, one canonical pack each)
#   tenants/<tenant-hash>/current.json              ({"packs": {pack_id: {digest,size}}})
from workers import Response
import base64
import json
import tempfile

from binggupack.app import service as SVC
from binggupack.app import snapshot as S


# ── R2 FFI helpers (JS ArrayBuffer <-> Python bytes) ─────────────────────────
def _ab_to_bytes(ab):
    try:
        return bytes(ab.to_py())
    except Exception:
        import js
        return bytes(js.Uint8Array.new(ab).to_py())


def _bytes_to_js(data):
    import js
    u8 = js.Uint8Array.new(len(data))
    u8.assign(data)
    return u8


async def _r2_get_bytes(env, key):
    obj = await env.SNAPSHOTS.get(key)
    if obj is None:
        return None
    return _ab_to_bytes(await obj.arrayBuffer())


async def _r2_get_json(env, key):
    obj = await env.SNAPSHOTS.get(key)
    if obj is None:
        return None
    try:
        return json.loads(await obj.text())
    except Exception:
        return None


async def _collect_tenant_snapshots(env, th):
    """READ-ONLY: fetch + digest-verify every current pack snapshot for a tenant.

    Returns a list of verified tar byte strings. Corrupt/mismatched snapshots are skipped
    (never served); nothing is written.
    """
    cur = await _r2_get_json(env, "tenants/%s/current.json" % th)
    tars = []
    if not cur or not isinstance(cur.get("packs"), dict):
        return tars
    for _pid, meta in cur["packs"].items():
        digest = (meta or {}).get("digest")
        if not digest:
            continue
        tar = await _r2_get_bytes(env, "tenants/%s/snapshots/%s.pack" % (th, digest))
        if tar is None or S.snapshot_digest(tar) != digest:
            continue  # skip corrupt/mismatched — never serve, never mutate
        tars.append(tar)
    return tars


async def _invoke(env, tenant, tool, args):
    if tool not in SVC.READ_TOOLS:
        return {"error_code": "UNKNOWN_TOOL", "message": "unknown tool"}
    th = S.tenant_hash(tenant)
    tars = await _collect_tenant_snapshots(env, th)
    root = tempfile.mkdtemp(prefix="tenant_")
    SVC.materialize(tars, root)
    return SVC.invoke_on_root(root, tool, args)


async def _publish(env, tenant, pack_tar_b64):
    th = S.tenant_hash(tenant)
    try:
        raw = base64.b64decode(pack_tar_b64 or "", validate=True)
    except Exception:
        return {"publish_status": "rejected", "reason": "invalid_base64"}

    v = SVC.validate_and_canonicalize(raw)
    if not v.get("ok"):
        return {"publish_status": "rejected", "reason": v.get("reason", "failed_validation")}

    pack_id = v["pack_id"]
    digest = v["digest"]
    canon = v["canonical_tar"]

    # immutable finalize (idempotent: same digest -> no second copy)
    snap_key = "tenants/%s/snapshots/%s.pack" % (th, digest)
    head = await env.SNAPSHOTS.head(snap_key)
    already = head is not None
    if not already:
        await env.SNAPSHOTS.put(snap_key, _bytes_to_js(canon))

    # current.json write LAST (pointer flip). Preserve prior packs.
    cur = await _r2_get_json(env, "tenants/%s/current.json" % th)
    if not cur or not isinstance(cur.get("packs"), dict):
        cur = {"schema": 1, "packs": {}}
    prior = cur["packs"].get(pack_id, {})
    idempotent = bool(already and prior.get("digest") == digest)
    cur["packs"][pack_id] = {"digest": digest, "size": len(canon)}
    await env.SNAPSHOTS.put("tenants/%s/current.json" % th,
                            json.dumps(cur, ensure_ascii=False, sort_keys=True))

    return {
        "publish_status": "ok",
        "pack_id": pack_id,
        "revision_digest": digest,
        "size": len(canon),
        "counts": v.get("counts", {}),
        "idempotent": idempotent,
    }


def _selfcheck():
    from binggupack.app.conformance import run_conformance
    root = tempfile.mkdtemp(prefix="conf_")
    checks = run_conformance(root)
    passed = sum(1 for _, ok in checks if ok)
    return {"passed": passed, "total": len(checks),
            "gate": "GO" if passed == len(checks) else "STOP",
            "fails": [n for n, ok in checks if not ok]}


async def on_fetch(request, env):
    if request.method != "POST":
        return Response(json.dumps({"error_code": "METHOD_NOT_ALLOWED"}),
                        status=405, headers={"content-type": "application/json"})
    try:
        envelope = json.loads(await request.text())
    except Exception:
        return Response(json.dumps({"error_code": "BAD_ENVELOPE"}),
                        status=400, headers={"content-type": "application/json"})
    op = envelope.get("op")
    try:
        if op == "invoke":
            result = await _invoke(env, envelope.get("tenant"), envelope.get("tool"), envelope.get("args"))
        elif op == "publish":
            result = await _publish(env, envelope.get("tenant"), envelope.get("pack_tar_b64"))
        elif op == "selfcheck":
            result = _selfcheck()
        else:
            result = {"error_code": "UNKNOWN_OP"}
        status = 200
    except S.SnapshotError as e:
        result = {"error_code": "SNAPSHOT_ERROR", "message": str(e)}
        status = 200
    except Exception as e:  # noqa: BLE001 — never leak stack/path to caller or logs
        result = {"error_code": "CORE_ERROR"}
        status = 500
        # log only the exception type — no traceback (avoids internal R2-key/tempdir
        # path structure in worker logs; §5 log redaction). Data errors are SnapshotError.
        print("core_error type:", type(e).__name__)
    return Response(json.dumps(result, ensure_ascii=False), status=status,
                    headers={"content-type": "application/json"})
