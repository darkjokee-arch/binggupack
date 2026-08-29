# -*- coding: utf-8 -*-
"""Binggu Anywhere — synthetic transport E2E (local dev or live canary).

Drives the full path against a running gateway: auth, tenant isolation, admin upload,
cross-tenant non-enumeration, idempotent republish, and MCP write-surface block. Uses
ONLY a synthetic pack — never the operating ledger or real private data.

Run recipe (local, two wrangler dev workers + seeded AUTH KV):

  cd hosted/workers
  # 1. generate a synthetic pack + three test credentials, print KV seed commands:
  python anywhere/local_e2e.py --generate --out /tmp/anywhere_e2e
  # 2. seed AUTH KV (commands printed by step 1), e.g.:
  npx wrangler kv key put --local --config wrangler.anywhere_gateway.toml --binding AUTH \
      "cred:<hash>" '<json>'
  # 3. start both dev workers:
  npx wrangler dev --config wrangler.anywhere_core.toml    --port 8799 &
  npx wrangler dev --config wrangler.anywhere_gateway.toml --port 8798 &
  # 4. run the checks:
  python anywhere/local_e2e.py --endpoint http://127.0.0.1:8798 --state /tmp/anywhere_e2e

For a LIVE canary, seed the credentials on the deployed AUTH KV (owner) and point
--endpoint at the deployed gateway. Never seed real packs — this uses a synthetic one.
"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

# import the shared canonical snapshot builder + fixtures
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "core"))
from binggupack.app import snapshot as S  # noqa: E402
from binggupack.app import conformance as C  # noqa: E402


def _synthetic_pack():
    import tempfile
    src = tempfile.mkdtemp()
    C._write_pack(
        src, "demo_bid_v1",
        [C._node("node:a:1", "마진이 낮으면 응찰을 보류한다.", ["EA1"], "판단"),
         C._node("node:a:2", "기초금액은 발주기관 기준 금액이다.", ["EA2"]),
         C._node("node:a:3", "이 방식을 채택하면 안 된다.", ["EA3"], "판단")],
        [C._edge("edge:a:1", "node:a:2", "node:a:1", "supports_judgment", ["EA1"]),
         C._edge("edge:a:2", "node:a:1", "node:a:3", "contradicts", ["EA3"])],
        [C._ev("EA1"), C._ev("EA2"), C._ev("EA3")])
    tar, digest = S.make_pack_snapshot(os.path.join(src, "demo_bid_v1"), "demo_bid_v1")
    return tar, digest


def generate(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    tar, digest = _synthetic_pack()
    with open(os.path.join(out_dir, "packA.b64"), "w") as f:
        f.write(base64.b64encode(tar).decode())
    rows = {}
    for key, (sub, tid, sc) in {
        "READ_A": ("svc_read_a", "tenantA", ["read:packs"]),
        "WRITE_A": ("owner_a", "tenantA", ["read:packs", "write:packs"]),
        "READ_B": ("svc_read_b", "tenantB", ["read:packs"]),
    }.items():
        t = secrets.token_urlsafe(32)
        h = hashlib.sha256(t.encode()).hexdigest()
        rows[key] = {"token": t, "hash": h, "rec": {"subject": sub, "tenant_id": tid, "scopes": sc}}
    with open(os.path.join(out_dir, "creds.json"), "w") as f:
        json.dump(rows, f)
    print("# synthetic pack digest:", digest)
    print("# seed AUTH KV with these (local):")
    for k, v in rows.items():
        print('npx wrangler kv key put --local --config wrangler.anywhere_gateway.toml '
              '--binding AUTH "cred:%s" %r  # %s' % (v["hash"], json.dumps(v["rec"]), k))


def _call(endpoint, path, token=None, body=None, rawbody=None):
    req = urllib.request.Request(endpoint.rstrip("/") + path, method="POST" if (body or rawbody) else "GET")
    if token:
        req.add_header("authorization", "Bearer " + token)
    data = None
    if rawbody is not None:
        data = rawbody.encode(); req.add_header("content-type", "application/json"); req.method = "POST"
    elif body is not None:
        data = json.dumps(body).encode(); req.add_header("content-type", "application/json")
    try:
        r = urllib.request.urlopen(req, data=data, timeout=180)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def run(endpoint, state_dir):
    creds = json.loads(Path(os.path.join(state_dir, 'creds.json')).read_text())
    packb64 = Path(os.path.join(state_dir, 'packA.b64')).read_text().strip()
    RA, WA, RB = creds["READ_A"]["token"], creds["WRITE_A"]["token"], creds["READ_B"]["token"]

    def mcp(tok, method, params=None):
        return _call(endpoint, "/mcp", tok, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def tj(tok, name, args):
        _, b = mcp(tok, "tools/call", {"name": name, "arguments": args})
        res = json.loads(b).get("result", {})
        return json.loads(res.get("content", [{}])[0].get("text", "{}"))

    checks = []

    def ck(name, ok):
        checks.append((name, bool(ok)))

    s, b = _call(endpoint, "/health"); ck("health_readonly", s == 200 and json.loads(b).get("mode") == "read-only")
    s, _ = _call(endpoint, "/mcp", None, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}); ck("missing_auth_401", s == 401)
    _, b = mcp(RA, "tools/list"); ck("tools_list_exact_five",
                                    sorted(t["name"] for t in json.loads(b)["result"]["tools"]) ==
                                    ["evidence_search", "handoff_context", "node_edge_lookup", "pack_list", "pack_summary"])
    s, _ = _call(endpoint, "/admin/packs", RA, {"pack_tar_b64": packb64}); ck("upload_requires_write_scope", s == 403)
    s, b = _call(endpoint, "/admin/packs", WA, {"pack_tar_b64": packb64}); ck("owner_publish", json.loads(b).get("publish_status") == "ok")
    ck("list_A_has_pack", [p["pack_id"] for p in tj(RA, "pack_list", {}).get("packs", [])] == ["demo_bid_v1"])
    ck("summary_A_exact", tj(RA, "pack_summary", {"pack_id": "demo_bid_v1"}).get("pack_id") == "demo_bid_v1")
    ck("evidence_A_hit", bool(tj(RA, "evidence_search", {"pack_id": "demo_bid_v1", "query": "마진 보류"}).get("hits")))
    ck("node_A_exact", tj(RA, "node_edge_lookup", {"pack_id": "demo_bid_v1", "node_id": "node:a:1"}).get("node", {}).get("id") == "node:a:1")
    ho = tj(RA, "handoff_context", {"pack_id": "demo_bid_v1"}).get("context_markdown", "")
    ck("handoff_candidate_contradiction", "candidate" in ho and "contradicts" in ho)
    ck("cross_tenant_list_empty", tj(RB, "pack_list", {}).get("packs", None) == [])
    ck("cross_tenant_pack_not_found", tj(RB, "pack_summary", {"pack_id": "demo_bid_v1"}).get("error_code") == "PACK_NOT_FOUND")
    ck("tenant_arg_cannot_escalate", tj(RB, "pack_summary", {"pack_id": "demo_bid_v1", "tenant_id": "tenantA"}).get("error_code") == "PACK_NOT_FOUND")
    _, b = _call(endpoint, "/admin/packs", WA, {"pack_tar_b64": packb64}); ck("same_digest_idempotent", json.loads(b).get("idempotent") is True)
    _, b = mcp(RA, "tools/call", {"name": "pack_upload", "arguments": {}}); ck("hidden_tool_call_blocked", json.loads(b).get("error", {}).get("code") == -32602)
    s, b = _call(endpoint, "/mcp", RA, rawbody="{ not json "); ck("malformed_json_safe", s == 400 and json.loads(b).get("error", {}).get("code") == -32700)
    ck("no_source_path_leak", "seed/x.md" not in json.dumps(tj(RA, "pack_summary", {"pack_id": "demo_bid_v1"}), ensure_ascii=False))

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))
    print("=== %d/%d ===" % (passed, len(checks)))
    print("GATE=%s" % ("GO" if passed == len(checks) else "STOP"))
    return 0 if passed == len(checks) else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--state", default=None)
    a = ap.parse_args(argv)
    if a.generate:
        generate(a.out or "."); return 0
    if a.endpoint and a.state:
        return run(a.endpoint, a.state)
    ap.print_help(); return 2


if __name__ == "__main__":
    sys.exit(main())
