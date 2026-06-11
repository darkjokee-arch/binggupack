# OpenBinggu v1.0 — candidate management full-cycle verification result (2026-06-11)

The v1.0 personal candidate-management UX is now complete and verified end-to-end:
**view → reject → replace → accept → retract → feedback resolve**, exercised both on a temp
DB (publicly reproducible) and once on the persistent private staging DB (evidence below).

## Runner

`scripts/openbinggu_v1_candidate_cycle_real_once.py`

```
python scripts/openbinggu_v1_candidate_cycle_real_once.py --dry-run-temp   # temp only — runnable from a clean clone
python scripts/openbinggu_v1_candidate_cycle_real_once.py                  # private staging env only (lazy import)
```

- `--dry-run-temp` (**17/17 GATE GO**): seeds a temp DB with one pre-existing candidate and a
  future-due review (mirroring the real ledger's scheduled review), then runs the identical
  cycle, then proves: private config module never imported, `tmp/real_staging` untouched,
  temp directory removed.
- real mode (**13/13 GATE GO**, executed 2026-06-11 under explicit owner GO): same cycle on the
  persistent staging DB. In a public clone this mode fails safely — the private config does
  not ship.

## The cycle (every step human-gated)

1. **view** — read-only listing (store checksum identical before/after)
2. **reject** — `DEPRECATE <n> <id8>`: physical preservation + excluded from active view
3. **replace** — `REPLACE <n> <id8> WITH <sentence>`: predecessor deprecated with a
   `replaced_by:` back-link, successor saved as a fresh candidate through the full save gates
   (constitution re-verdict, PII rescan, canonical-hash dedup) — never an in-place edit
4. **accept** — `ACCEPT <n> <id8>`: append-only acceptance event; the candidate row itself is
   byte-identical before and after
5. **retract** — `UNACCEPT <n> <id8>`: a preserving event (nothing deleted); current state is
   the latest event per node
6. **feedback** — review due registered and resolved (`성공`) through the existing G3 runner —
   record-only, no state change on the node

## Invariants (proven by checks)

- every pre-existing row preserved (`before ⊆ after` per table); the pre-existing scheduled
  review stayed `pending` untouched
- raw input text never stored (sentence excerpts only) · `confirmed` 0 · `promotion_allowed=0`
  everywhere · no OpenCrab calls · operating stores untouched · audit chain INTACT throughout
- **rollback exercised, not promised**: the runner finishes by restoring its own pre-cycle
  snapshot — checksum matched exactly, zero fixture residue, scheduled review alive, chain
  INTACT. The ledger ends the run unpolluted; the evidence is this record and the run output.
- hosted/live worker unaffected (read-only 6 tools; live smoke 12/12 after the real run)

## Completion criteria — candidate management part (declared complete by owner, 2026-06-11)

| criterion | evidence |
|---|---|
| temp selftest per module | list view 13 · deprecate 15 · replace 16 · accept 16 · resolve 16 checks, all GATE GO |
| integrated temp cycle | `--dry-run-temp` 17/17 |
| real staging demonstration | real mode 13/13 (owner GO) |
| rollback | snapshot restore executed twice across the line, checksum-exact both times |
| clean-clone reproducibility | `--dry-run-temp` requires no private files |
| public tree scan | CLEAN |

Out of scope (separate GO lines): hosted write exposure · OpenCrab upload/apply ·
confirmed promotion · marketplace/payment.
