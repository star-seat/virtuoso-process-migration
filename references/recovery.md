# Recovery and traceability

## Run directory

Create a run-specific directory outside Virtuoso libraries containing: input audit, profile copy and hash, decisions, ready plan and hash, source snapshots, target pre-write snapshots, backup manifest, action journal, checkpoint, post snapshot, verifier output, Bridge/CIW excerpts, and final report.

## Checkpoints

Use states `inventoried`, `blocked_for_decision`, `ready`, `applying`, `verifying`, `complete`, and `failed`. Journal one transaction record per writable Cell and one stable action ID/postcondition per instance inside it. On resume, re-read only the affected Cell first and reconcile every instance postcondition; selectively replay unresolved actions rather than blindly replaying the whole Cell call. Do not assume an interrupted call failed or succeeded.

## Idempotency

An action is complete only when its postcondition matches the ready plan. Skip a verified completed action. Stop if live state differs from both precondition and postcondition. A changed source, target inventory, CDF contract/profile, or decision invalidates the plan hash and requires replanning.

Full hierarchy snapshots occur only at frozen inventory, after all Cell batches, and final verification. Between phase boundaries, store targeted Cell/instance readbacks, CDF values, body endpoint partitions, and Check & Save results. This preserves traceability without repeatedly scanning the entire hierarchy.

## Rollback

Overwrite requires a verified backup before mutation. Prefer recovery by restoring only exact affected CellViews. Do not delete a whole target library or recursively remove a computed path. Renamed/new cells can be removed only after resolving their absolute library/cell/view targets and confirming no unrelated callers depend on them.

## Final report

Include inputs and revisions, plan/run hashes, all decisions, copied/skipped/reused/renamed views, mappings with evidence status, CDF readbacks, connectivity comparison, Check & Save results, residual references, backup locations, warnings, failures/recoveries, and the three independent completion states.

