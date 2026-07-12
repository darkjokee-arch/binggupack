# -*- coding: utf-8 -*-
"""Binggu Anywhere — immutable pack snapshot artifact (shared canonical logic).

A *snapshot* is the deterministic, digest-addressed tar of a single canonical pack
directory (manifest.json + graph/nodes.jsonl + graph/edges.jsonl + evidence/index.jsonl,
flat fallback allowed). The SAME functions build snapshots on the owner upload side and
materialize them on the read-service side, so a snapshot round-trips byte/semantic-
identically to the local read core (owner §9 parity).

Design invariants:
  * deterministic tar (fixed mtime/uid/gid/mode, sorted members, PAX) → same bytes → same digest.
  * safe extraction (owner §4): reject absolute members, ``..`` traversal, symlink/hardlink,
    device/fifo, duplicate normalized members, escape outside dest, oversize.
  * conservative caps (owner §5): 2 MiB snapshot, bounded members; per-file limits are
    still enforced downstream by the read core (models.JSONL_MAX_BYTES etc.).
  * NO network / R2 / KV here — pure bytes<->filesystem. Transport binds this separately.
"""
import hashlib
import io
import os
import re
import tarfile
import unicodedata

from binggupack.app import models as M

# snapshot-level caps (owner §5 conservative). per-file caps live in the read core.
SNAPSHOT_MAX_BYTES = 2 * 1024 * 1024          # 2 MiB uncompressed tar
MAX_MEMBERS = 64                               # canonical pack has ~4-6 files
MEMBER_MAX_BYTES = M.JSONL_MAX_BYTES           # 8 MiB single-file ceiling (read core parity)

# pack_id / directory name (filesystem) — path separators / parent refs excluded.
_SAFE_PACK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
# canonical member paths a pack directory may contain (nested + flat fallback).
_ALLOWED_BASENAMES = {
    "manifest.json",
    "graph/nodes.jsonl", "graph/edges.jsonl", "evidence/index.jsonl",
    "nodes.jsonl", "edges.jsonl", "evidence_index.jsonl",
}


class SnapshotError(ValueError):
    """Raised on unsafe/malformed snapshot artifacts (safe reason code, no path leak)."""


def tenant_hash(tenant_id):
    """Stable filesystem/key-safe tenant identifier derived from the auth tenant id.

    Never expose the raw tenant id in storage keys; use this hash. Deterministic.
    """
    if not tenant_id or not isinstance(tenant_id, str):
        raise SnapshotError("invalid_tenant")
    norm = unicodedata.normalize("NFC", tenant_id).strip()
    if not norm:
        raise SnapshotError("invalid_tenant")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def snapshot_digest(tar_bytes):
    return hashlib.sha256(tar_bytes).hexdigest()


def _iter_pack_files(pack_dir):
    names = []
    for r, _, fns in os.walk(pack_dir):
        for fn in fns:
            names.append(os.path.join(r, fn))
    names.sort()
    return names


def make_pack_snapshot(pack_dir, pack_id):
    """Build the deterministic snapshot tar for one canonical pack directory.

    Returns (tar_bytes, digest). Members are stored under ``<pack_id>/...`` so that
    extraction yields a parent root containing the pack directory (PackRepository input).
    Raises SnapshotError on unsafe pack_id, disallowed member, or size overflow.
    """
    if not _SAFE_PACK_ID.match(pack_id or ""):
        raise SnapshotError("invalid_pack_id")
    files = _iter_pack_files(pack_dir)
    if not files:
        raise SnapshotError("empty_pack")
    if len(files) > MAX_MEMBERS:
        raise SnapshotError("too_many_members")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for pth in files:
            rel = os.path.relpath(pth, pack_dir).replace(os.sep, "/")
            if rel not in _ALLOWED_BASENAMES:
                raise SnapshotError("disallowed_member")
            with open(pth, "rb") as fh:
                data = fh.read()
            if len(data) > MEMBER_MAX_BYTES:
                raise SnapshotError("member_too_large")
            arc = pack_id + "/" + rel
            ti = tarfile.TarInfo(arc)
            ti.size = len(data)
            ti.mtime = 0
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.mode = 0o644
            ti.type = tarfile.REGTYPE
            tf.addfile(ti, io.BytesIO(data))
    tar_bytes = buf.getvalue()
    if len(tar_bytes) > SNAPSHOT_MAX_BYTES:
        raise SnapshotError("snapshot_too_large")
    return tar_bytes, snapshot_digest(tar_bytes)


def safe_extract_into(tar_bytes, parent_root):
    """Extract a snapshot tar into ``parent_root`` with owner §4 guards.

    Returns (pack_id, sorted_members). Every member must live under a single top-level
    ``<pack_id>/`` directory whose name is a safe pack id. Rejects absolute/traversal/
    symlink/hardlink/device/duplicate/escape/oversize members. No file is written until
    all members validate would be ideal, but we still re-check realpath containment per file.
    """
    if len(tar_bytes) > SNAPSHOT_MAX_BYTES:
        raise SnapshotError("snapshot_too_large")
    real_parent = os.path.realpath(parent_root)
    seen = set()
    top_ids = set()
    members_out = []
    try:
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
        infos = tf.getmembers()
    except (tarfile.TarError, OSError, EOFError, ValueError):
        raise SnapshotError("bad_tar")
    with tf:
        if len(infos) > MAX_MEMBERS:
            raise SnapshotError("too_many_members")
        for m in infos:
            name = m.name
            if not m.isreg():
                raise SnapshotError("non_regular_member")
            if m.size > MEMBER_MAX_BYTES:
                raise SnapshotError("member_too_large")
            if name.startswith("/") or name.startswith("\\") or (len(name) > 1 and name[1] == ":"):
                raise SnapshotError("absolute_member")
            norm = os.path.normpath(name).replace("\\", "/")
            if norm.startswith("..") or "/../" in norm or norm in (".", "") or os.path.isabs(norm):
                raise SnapshotError("traversal_member")
            parts = norm.split("/")
            if len(parts) < 2:
                raise SnapshotError("member_not_under_pack_dir")
            pid = parts[0]
            if not _SAFE_PACK_ID.match(pid):
                raise SnapshotError("invalid_pack_id")
            rel = "/".join(parts[1:])
            if rel not in _ALLOWED_BASENAMES:
                raise SnapshotError("disallowed_member")
            top_ids.add(pid)
            if len(top_ids) > 1:
                raise SnapshotError("multiple_pack_dirs")
            if norm in seen:
                raise SnapshotError("duplicate_member")
            seen.add(norm)
            target = os.path.join(parent_root, norm)
            rt = os.path.realpath(target)
            # containment: target must stay within parent_root
            if os.path.commonpath([rt, real_parent]) != real_parent:
                raise SnapshotError("escape_member")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            ex = tf.extractfile(m)
            if ex is None:
                raise SnapshotError("unreadable_member")
            with ex as src, open(target, "wb") as out:
                out.write(src.read())
            members_out.append(norm)
    if not top_ids:
        raise SnapshotError("empty_snapshot")
    return top_ids.pop(), sorted(members_out)
