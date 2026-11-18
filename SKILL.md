---
name: virtuoso-process-migration
description: Safely plan, execute, verify, and recover Cadence Virtuoso CellView hierarchy copies and cross-process migrations. Use for recursive schematic/symbol/Verilog-A/SystemVerilog migration, source-to-target library rebinding, MOS/passive/standard-cell mapping, collision handling, residual-process checks, or migration audit reports. Route ordinary same-process hierarchy cloning to virtuoso-librarian; use this skill when process-aware conversion or its safety gates are required.
---

# Virtuoso Process Migration

Migrate CellViews without treating a process change as a directory copy. Use a read-only inventory and a deterministic plan first; perform database writes only after all conflicts and mapping gaps are resolved.

## Scope and skill coordination

- Use `virtuoso-librarian` for an ordinary same-process hierarchy clone. Keep its hierarchy discovery, source-side property capture, rebind, and `schCheck` practices.
- Use `virtuoso` for Bridge connection, CIW/SKILL execution, CellView inspection, edits, CDF callbacks, Check & Save, and ADE work.
- Use this skill for process-aware inventory, collision decisions, view filtering, project-reference rebinding, device maps, verification gates, checkpoints, and recovery.
- Do not migrate layout, extracted/PEX views, simulation results, caches, locks, or signoff data in V1. Do not claim DRC/LVS/PEX or functional equivalence.

## Mandatory workflow

1. Read [workflow.md](references/workflow.md) and [conflicts-and-safety.md](references/conflicts-and-safety.md).
2. Confirm the source top, source and target processes/revisions, target library, project-library map, allowed views, copy-vs-reuse policy, mapping profile, backup root, and verification expectations.
3. Connect through `virtuoso`, enable dialog protection, and perform a read-only recursive inventory through schematic `instHeaders`. Record actual view names and instance-master references; never infer the hierarchy from cell names or filesystem folders.
4. Export the normalized audit JSON described in [audit-and-plan-schema.md](references/audit-and-plan-schema.md). Run `scripts/plan_migration.py` without decisions to obtain the complete conflict and mapping-gap list.
5. If any conflict exists, stop before all writes. Present one batched decision request covering every collision. Accept only explicit per-cell `overwrite`, `rename`, or `reuse` decisions. Unattended mode must also stop.
6. Re-run the planner with recorded decisions. Do not proceed unless its status is `ready`, every referenced device map is revalidated, and every MOS has a pre-write `w`/`Wfg`/`wf` semantic check plus a complete target sizing/CDF plan. A generic `width_semantics_compatible` flag is insufficient when field meanings differ.
7. Create a run manifest and checkpoint. Apply changes bottom-up through `virtuoso`. Copy only views that actually exist and are in the exact allowlist: `schematic`, `symbol`, `systemVerilog`, `veriloga`. Take full hierarchy snapshots only at frozen inventory, after all Cell batches, and final verification; between those boundaries use targeted postcondition readback.
8. Execute one logical transaction batch per writable Cell. Send the Cell's rebind, MOS CDF, callback, and body-bridge loops in one Cell-level SKILL request when practical, while keeping a stable action ID and independent postcondition for every instance. Immediately after each MOS recreation and CDF finalization, connect its recorded source body endpoint using the actual target B-pin geometry and actual target-net geometry; never try a logic-only body instTerm first.
9. Run `scripts/lint_skill_source.py` on every generated SKILL source before a live `load`. Then run bottom-up Check & Save only for created or modified schematics, read back CDF parameters, and perform all gates in [verification.md](references/verification.md). A clean Check & Save is not process legality and is not a simulation pass.
10. Write a final report distinguishing `database_operations_complete`, `process_checks_passed`, and `functional_simulation_passed`. Leave the last state false or unknown unless a regenerated target-process simulation actually passed.

## Non-negotiable safety gates

- Do not create, overwrite, rename, reuse, or rebind anything while conflicts are unresolved.
- Never overwrite an entire cell implicitly. Authorization is exact to target library/cell/view, requires a recoverable backup, and must list affected callers.
- Never modify or Check & Save a reused cell. Reuse has a dedicated read-only gate: compare the public schematic/symbol interface and validate target process plus residual source-process references. Do not compare internal topology to the source; reuse means compatible contract, not identical implementation.
- Never use global string replacement for library or cell references.
- Never invent a MOS, resistor, capacitor, or standard-cell mapping. Missing, ambiguous, illegal, or revision-mismatched maps are blockers.
- For the maintained 28 nm to 65 nm profile, use 30 nm and 60 nm as the respective minimum channel lengths. If the user supplies no MOS sizing rule, scale canonical total width with length to preserve `W/L`, then translate it through the profile's exact field semantics. The verified profile records source `w=total`, `Wfg=per-finger`, `w=Wfg*fingers`; target `w=per-finger`, `wf=total`, `wf=w*fingers`; target callback order is `l`, `fingers`, `m`, `wf`, with set-then-callback at each step. Revalidate the cached contract against every live target master/revision before writes. Keep fingers and multiplier unchanged. Example at one finger: `1u/30n -> 2u/60n`. An explicit user sizing rule takes precedence but does not waive semantic validation.
- Copy source instance properties from the source database before rebind; destination open-time remapping may drop properties. Preserve property `valueType`.
- In IC6.1.8-style flows, prefer delete/recreate rebind when direct master reassignment is unreliable. Restore instance name, transform, source properties, and terminal connectivity, then run `schCheck`.
- If an operation is interrupted, follow [recovery.md](references/recovery.md); do not blindly replay completed writes.

## Scripts

The scripts are offline planners and checkers. They do not write OpenAccess data.

- `scripts/plan_migration.py`: scan the full audit, batch conflicts, apply explicit decisions, validate device maps, and emit a deterministic plan.
- `scripts/suggest_rename.py`: generate target-library name candidates from the frozen audit; the chosen name is still re-scanned by the planner.
- `scripts/validate_plan.py`: reject a plan with unresolved conflicts, disallowed views, duplicate targets, missing confirmations, or unsafe actions.
- `scripts/verify_migration.py`: compare pre/post snapshots using endpoint connectivity, interfaces, references, residual process markers, and planned views.
- `scripts/lint_skill_source.py`: perform offline string/comment/delimiter checks on generated `.il`/`.ils` before live load. It is a preflight gate, not a replacement for the Cadence parser.

Run `python scripts/plan_migration.py --help` for input details. Use the sample profile only as evidence-backed historical data; it is never a universal mapping rule.

## Load references as needed

- Conflict classification, overwrite/rename/reuse rules: [conflicts-and-safety.md](references/conflicts-and-safety.md)
- Audit, decisions, plan, and snapshot JSON: [audit-and-plan-schema.md](references/audit-and-plan-schema.md)
- Bridge inspection and guarded database operations: [bridge-operations.md](references/bridge-operations.md)
- MOS, R, C, standard-cell, CDF, body, and pin rules: [device-mapping.md](references/device-mapping.md)
- Symbol/schematic synchronization and validation: [verification.md](references/verification.md)
- Checkpoints, interruption, idempotency, and reports: [recovery.md](references/recovery.md)
- Historical mappings requiring target revalidation: [tsmc-n28-to-n65-gp.json](references/process-maps/tsmc-n28-to-n65-gp.json)

