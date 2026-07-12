# Binggu Anywhere — deployment & boundary

Binggu Anywhere serves your **explicitly uploaded, public-safe packs** to external MCP
clients over an authenticated HTTPS endpoint. It runs the v1.21-A read core directly — the
five read tools are never reimplemented in the transport.

> **Runtime note:** the private core runs on **Cloudflare Python Workers**, currently
> provided by Cloudflare as a **beta** runtime. The service UI/docs surface this.

## Architecture

```
External MCP client (ChatGPT / Claude connector)
        │  HTTPS + Bearer
        ▼
  TS gateway  (binggu-anywhere-gateway)      ← public
        │  Service Binding (CORE)
        ▼
  Python core (binggu-anywhere-core)         ← PRIVATE, no public route
        │  R2 get/put
        ▼
  R2 bucket  (binggu-anywhere-snapshots)     ← private, immutable snapshots
```

- **Gateway** (`hosted/workers/anywhere/gateway.ts`): HTTP/MCP envelope, authentication,
  tenant derivation, request validation, admin-upload orchestration, Service Binding calls.
  It performs **no** pack logic.
- **Core** (`hosted/workers/anywhere/core/entry.py`): materializes the tenant's R2 snapshots
  into an ephemeral filesystem and calls `binggupack.app.service` → `PackService`. Reached
  only via the Service Binding; it has no route/domain of its own.
- **Vendored read core** (`hosted/workers/anywhere/core/binggupack/`): a byte-identical copy
  of the read-core module closure, kept in sync by `scripts/sync_anywhere_vendor.py`
  (CI runs `--check` to fail on drift).

## Data plane vs admin plane

| | Data plane (`/mcp`) | Admin plane (`/admin/packs`) |
|---|---|---|
| who | external app/agent | owner only |
| scope | `read:packs` | `write:packs` |
| surface | exactly 5 read tools | immutable upload |
| writes | **none** | new immutable revision |

The five read tools are: `pack_list`, `pack_summary`, `evidence_search`,
`node_edge_lookup`, `handoff_context`. No other tool appears in `tools/list`, and a direct
`tools/call` to any non-read tool is rejected before the core is reached.

## Authentication & tenant isolation

- `Authorization: Bearer <token>`. The gateway resolves `sha256(token)` against the
  `AUTH` KV (`cred:<hash>` → `{subject, tenant_id, scopes}`). The raw token is never
  stored or logged.
- **`tenant_id` comes only from the auth record.** Any `tenant_id` in tool args / MCP
  metadata / query / body is ignored. Cross-tenant lookups return `PACK_NOT_FOUND` and
  cross-tenant listings are empty (non-enumerable).
- KV holds **only** auth metadata — never pack data, revision pointers, or publication truth.

## R2 storage

```
tenants/<tenant-hash>/snapshots/<sha256>.pack   # immutable, one canonical pack each
tenants/<tenant-hash>/current.json              # {"packs": {pack_id: {digest, size}}}
```

- Snapshots are deterministic, digest-addressed, immutable. Publish order: validate →
  canonicalize → immutable finalize → `current.json` written **last** (pointer flip).
- Same-digest re-upload is idempotent (no second copy). Remote delete is out of scope.
- Read requests do R2 **get only** — no put/delete on the read path.
- Conservative snapshot cap: **2 MiB** uncompressed (`SNAPSHOT_MAX_BYTES`). Per-file caps
  are enforced by the read core (`JSONL_MAX_BYTES` = 8 MiB, etc.).

## Owner upload (admin plane client)

```
export BINGGU_ANYWHERE_TOKEN=<owner write:packs token>   # never a CLI arg
binggu app upload --pack ./path/to/<pack_id>             # dry-run preview (default)
binggu app upload --pack ./path/to/<pack_id> --endpoint https://<gateway> --confirm
```

- Preview-first (dry-run default). Upload requires an **interactive TTY confirmation**
  (type the pack_id); noninteractive confirm-only is refused.
- Only an explicit canonical pack directory is uploaded — no directory discovery, no
  `~/.binggupack` sweep, no `ledger.sqlite` / conversation / capture upload.
- The response prints `pack_id` / revision digest / counts / status — never a raw storage path.

## Deploy (owner)

Deploy the private core first, then the gateway (Service Binding target must exist):

```
cd hosted/workers
# 1. create private R2 bucket + AUTH KV (owner)
npx wrangler r2 bucket create binggu-anywhere-snapshots
npx wrangler kv namespace create AUTH            # put the id into wrangler.anywhere_gateway.toml

# 2. deploy core (private), then gateway (public)
npx wrangler deploy --config wrangler.anywhere_core.toml
npx wrangler deploy --config wrangler.anywhere_gateway.toml

# 3. seed a credential (owner) — bearer hash -> {subject, tenant_id, scopes}
#    the raw token is chosen by the owner and never committed/logged.
npx wrangler kv key put --binding AUTH "cred:<sha256(token)>" \
  '{"subject":"...","tenant_id":"...","scopes":["read:packs"]}'
```

The gateway is the only public entrypoint; the core has `workers_dev = false` and no route.

## Local dev / synthetic E2E

`hosted/workers/anywhere/local_e2e.py` drives the full path (auth, tenant isolation,
admin upload, cross-tenant, idempotent, MCP write-surface block) against two local
`wrangler dev` workers. It uses only synthetic packs — never the operating ledger or
real cloud. See the script header for the run recipe.

## Honest boundary

- The service only reads packs the owner **explicitly uploaded** and that passed the
  public-safe / canonical-layout gates. It does not access the local ledger directly.
- Upload is an **owner admin action**, separate from the read MCP surface.
- Pack content stays **candidate-only** — no automatic promotion into user memory, no
  protected-writer claim.
- The core runtime is Cloudflare Python Workers (beta). A passing live client canary does
  not turn beta into a production SLA.
