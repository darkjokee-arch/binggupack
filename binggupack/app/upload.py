# -*- coding: utf-8 -*-
"""Binggu Anywhere — owner-only pack upload (admin plane client).

Prepares a canonical pack directory for upload to the Anywhere admin endpoint:
local validation via the read core (the same gate the service applies), an explicit
public/private scan, a deterministic snapshot + content digest, a preview/dry-run, and
an owner TTY confirmation before any network call. The MCP data plane never exposes
upload; this is a separate owner action (owner §4/§8).

Guardrails (owner §8):
  * only an explicit canonical pack directory is uploaded — no directory discovery,
    no ~/.binggupack sweep, no ledger.sqlite / conversation / capture upload.
  * upload requires an interactive TTY confirmation; noninteractive confirm-only is refused.
  * the bearer token is read from the environment (never a CLI arg → not in shell history)
    and is never printed/logged.
  * the response prints pack_id / revision digest / counts / status — never a raw storage path.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

from binggupack.app import snapshot as S
from binggupack.app.read_core import PackRepository, PackService

TOKEN_ENV = "BINGGU_ANYWHERE_TOKEN"


class UploadError(Exception):
    pass


def prepare(pack_dir):
    """Validate + scan + snapshot a canonical pack directory. Returns a preview dict + tar bytes.

    Raises UploadError with a safe reason (no raw path) on any local failure.
    """
    pack_dir = os.path.abspath(pack_dir)
    if not os.path.isdir(pack_dir):
        raise UploadError("pack directory not found")
    parent = os.path.dirname(pack_dir)
    pack_id = os.path.basename(pack_dir)
    if not S._SAFE_PACK_ID.match(pack_id):
        raise UploadError("unsafe pack directory name")

    # 1) local validation through the read core (validate_pack STOP / oversize / PII fold in).
    svc = PackService(PackRepository(parent))
    summ = svc.get_pack_summary(pack_id)
    if summ.get("error_code"):
        raise UploadError("failed local validation: pack is not public-safe or malformed")

    # 2) explicit public/private scan (owner-visible verdict; read core already gates .jsonl).
    scan_verdict = "unknown"
    scan_reasons = {}
    try:
        from binggupack.safety.public_tree_scan import scan_public_tree
        scan = scan_public_tree(pack_dir)
        scan_verdict = scan.get("verdict", "unknown")
        scan_reasons = scan.get("by_reason", {})
    except Exception:
        scan_verdict = "unavailable"
    if scan_verdict == "dirty":
        raise UploadError("public scan flagged secret/PII — upload blocked")

    # 3) deterministic snapshot + content digest.
    tar_bytes, digest = S.make_pack_snapshot(pack_dir, pack_id)

    preview = {
        "pack_id": pack_id,
        "counts": summ.get("counts", {}),
        "title": summ.get("title"),
        "pack_type": summ.get("pack_type"),
        "risk_level": summ.get("risk_level"),
        "snapshot_size": len(tar_bytes),
        "revision_digest": digest,
        "scan_verdict": scan_verdict,
        "scan_reasons": scan_reasons,
    }
    return preview, tar_bytes


def preview_text(preview):
    c = preview.get("counts", {})
    lines = [
        "── Binggu Anywhere upload preview ──",
        "  pack_id       : %s" % preview["pack_id"],
        "  title         : %s" % (preview.get("title") or "-"),
        "  type / risk   : %s / %s" % (preview.get("pack_type") or "-", preview.get("risk_level") or "-"),
        "  nodes/edges/ev: %s / %s / %s" % (c.get("nodes"), c.get("edges"), c.get("evidence")),
        "  snapshot size : %d bytes (cap %d)" % (preview["snapshot_size"], S.SNAPSHOT_MAX_BYTES),
        "  revision      : %s" % preview["revision_digest"],
        "  public scan   : %s %s" % (preview["scan_verdict"],
                                     ("(%s)" % preview["scan_reasons"]) if preview["scan_reasons"] else ""),
        "  candidate-only: yes (no promotion into user memory)",
    ]
    return "\n".join(lines)


def do_upload(endpoint, token, tar_bytes, timeout=180):
    """POST the snapshot to the admin endpoint. Returns the whitelisted server response."""
    url = endpoint.rstrip("/") + "/admin/packs"
    body = json.dumps({"pack_tar_b64": base64.b64encode(tar_bytes).decode("ascii")}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("authorization", "Bearer " + token)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"publish_status": "error", "reason": "http_%d" % e.code}


def resolve_token():
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise UploadError("credential not set — export %s (never pass tokens as CLI args)" % TOKEN_ENV)
    return token


def run(pack_dir, endpoint, dry_run=True, out=None, tty_confirm=None):
    """Owner upload flow. Returns a result dict. Never prints raw tokens or storage paths."""
    out = out or (lambda s: print(s))
    preview, tar_bytes = prepare(pack_dir)
    out(preview_text(preview))
    if dry_run:
        out("  [dry-run] no upload performed. re-run with --confirm to upload.")
        return {"dry_run": True, "preview": preview}

    if not endpoint:
        raise UploadError("--endpoint required for upload")
    # owner TTY confirmation — noninteractive confirm-only is refused (owner §8).
    if tty_confirm is None:
        if not sys.stdin.isatty():
            raise UploadError("upload requires an interactive terminal (owner confirmation)")
        tty_confirm = lambda prompt: input(prompt)  # noqa: E731
    ans = tty_confirm("Type the pack_id to confirm upload (%s): " % preview["pack_id"])
    if (ans or "").strip() != preview["pack_id"]:
        raise UploadError("confirmation mismatch — upload aborted")

    token = resolve_token()
    status, resp = do_upload(endpoint, token, tar_bytes)
    # server response is a whitelisted projection (no raw path). echo the safe fields.
    safe = {k: resp.get(k) for k in ("publish_status", "pack_id", "revision_digest", "size", "counts", "idempotent")}
    out("── server result ──")
    out("  " + json.dumps(safe, ensure_ascii=False))
    if resp.get("publish_status") != "ok":
        raise UploadError("server rejected upload: %s" % resp.get("reason", "unknown"))
    return {"dry_run": False, "status": status, "result": safe}
