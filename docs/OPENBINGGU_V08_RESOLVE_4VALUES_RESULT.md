# OpenBinggu v0.8 — feedback resolve loop: 4-outcome verification result (2026-06-11)

The v0.8 personal write loop ends with a human feedback step: a judgment node saved with a
review due date is later resolved by a human with one of **4 outcomes** —
`성공 / 실패 / 불확실 / 판정불가`. This document records the verification that closed that
final segment, without waiting for the real due date (test fixtures used past due dates;
the real scheduled review row was never touched).

## What was verified

Runner: `scripts/openbinggu_v08_review_resolve_4values.py`

```
python scripts/openbinggu_v08_review_resolve_4values.py --selftest     # temp SQLite only
python scripts/openbinggu_v08_review_resolve_4values.py --real-once    # private staging env only
```

- `--selftest` (**16/16 GATE GO**, runnable from a clean clone): fixture judgment nodes with a
  past due date → reminder listing → all 4 outcomes resolved → negative cases
  (invalid outcome enum, `actor=auto`, empty reason, double-resolve — all BLOCK).
- `--real-once` (**12/12 GATE GO** on the persistent staging DB): snapshot first, fixture rows
  created through the human-only gate (`set_review_due`), all 4 outcomes recorded, full-table
  before/after diff, then restored from the snapshot (checksum-exact) after evidence capture.
  In a public clone this mode fails safely with `ModuleNotFoundError` — the private staging
  config does not ship.

## Invariants (proven by checks, not promised)

- **resolve is record-only**: node `state` / `candidate` / `sentence` byte-identical before and
  after every outcome — including `실패` (failure does **not** demote the node).
- **no automatic state changes**: 0 new `deprecations` rows (demotion stays a separate explicit
  human action), `confirmed` 0, `promotion_allowed=0` everywhere.
- **pre-existing rows untouched**: every before-row still present (`before ⊆ after`); the real
  scheduled pending review (future due) stayed `pending` with its due date unchanged.
- **reminder is read-only**: listing due reviews changes nothing (checksum-equal). Past-due
  pre-existing rows appearing in the list is correct behavior.
- **audit chain INTACT** throughout; raw conversation text never stored.
- gates re-confirmed: auto actor BLOCK, outcome outside the 4-value enum BLOCK, resolve reason
  required, no pending review → BLOCK.

## Rollback / cleanup procedure (exercised, not theoretical)

1. Before any staging write the runner copies the DB to
   `snapshots/snap_v08resolve_before_<checksum>.sqlite`.
2. Cleanup = remove `-wal`/`-shm` sidecars, copy the snapshot back over the DB,
   re-open and compare `store_checksum()` to the snapshot name.
3. This procedure was executed twice during verification; both times the checksum matched
   exactly, fixture rows count returned to 0, and the scheduled review row plus audit chain
   were verified alive afterwards.

Test fixtures are **not** kept in the staging DB after verification — evidence lives in this
document and the run transcripts only. Real staging data, snapshots, and fixture rows are
never committed to this repository.

## Unchanged

Hosted/live worker (read-only 6 tools) untouched — live smoke 12/12 GO after verification.
No OpenCrab calls, no hosted write exposure, no deploy, no token rotation.
