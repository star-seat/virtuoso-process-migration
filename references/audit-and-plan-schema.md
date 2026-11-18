# Audit and plan JSON

The offline tools use JSON so they have no optional parser dependency. Schema version is `1`.

## Audit

Top-level fields:

- `schema_version`: `1`.
- `source`: `process`, `project_libraries`, `top`, and reachable `cells`.
- `target`: `process`, `project_libraries`, existing `cells`, and device `masters`.
- `library_map`: exact source-project-library to target-library mapping.
- `external_allow_libraries`: general libraries allowed to remain referenced.

Each cell has `library`, `cell`, and `views`. A view may include `fingerprint`, `terminals`, and `instances`. Each terminal has `name`, `direction`, optional `bus`, and optional `order`. Each instance has `name`, `library`, `cell`, `view`, `kind`, `terminals`, and `properties`. Supported kinds are `project`, `mos`, `resistor`, `capacitor`, `standard_cell`, and `external`.

Instance `terminals` map master terminal names to source net names. This endpoint mapping is the topology source of truth. Fingerprints are useful for classification but never authorize reuse or overwrite.

## Decisions

Decision JSON contains a `decisions` object keyed by `sourceLibrary/sourceCell`:

```json
{
  "decisions": {
    "SRC/TOP": {"action": "rename", "new_cell": "TOP_MIG", "confirmed": true},
    "SRC/LEAF": {"action": "overwrite", "authorized_views": ["schematic", "symbol"], "backup_root": "/safe/run-42", "confirmed": true},
    "SRC/SHARED": {"action": "reuse", "target_library": "DST", "target_cell": "SHARED", "confirmed": true}
  }
}
```

An empty or missing decision is unresolved. User confirmation cannot be supplied by a default.

## Profile

The profile identifies exact source/target processes and revisions and contains mapping records. A mapping has exact source and target master IDs, status, required target revalidation, pin map, parameter strategy, and optional notes. Historical evidence is not universal verification.

Process objects may include `minimum_channel_length_nm`. A profile may define `mos_default_sizing_policy` with mode `preserve_w_over_l_raise_length_to_target_min` and a `mos_cdf_contract`. The contract names source/target length, total-width, per-finger-width, fingers, and multiplier fields; records their algebraic relation; lists ordered set/callback steps; and has a stable `contract_id`. A target-master audit supplies `cdf_validation` with `validated`, matching `contract_id`, exact field names, and callback sequence. The planner blocks before writes if source values contradict the width relation or the live target validation differs from the cached contract.

The resulting MOS action records canonical total width plus exact target CDF values, callback sequence, unchanged fingers/multiplier, and a body-reconnect postcondition. Legacy mappings may use `width_semantics_compatible: true` only when both PDKs genuinely share the same width meaning; it is not valid for `w`/`Wfg`/`wf` translations.

## Plan

The planner emits `blocked` or `ready`, a stable plan hash, reachable source cells, call counts, copied/skipped views, full conflicts, blockers, cell targets, reference actions, device actions, `execution_batches`, `snapshot_policy`, and required verification gates. Each non-reuse Cell has exactly one logical transaction batch. Its instance actions have stable IDs and independent postconditions; the batch requests one Cell-level SKILL loop for rebind, CDF/callback, immediate geometry-based body bridging, and targeted readback. `snapshot_policy.mode` is `phase_boundaries_only`. A ready plan must still be applied through `virtuoso`; it is not evidence that database writes occurred.

## Post snapshot

The verifier expects a target snapshot with the same normalized cell/view/terminal/instance form plus `cdf_readback`, `check_and_save`, `residual_references`, and optional `simulation`. Net names may differ; endpoint partitions must not for copied/converted Cells. Reused Cells use the dedicated read-only path: public interface plus target-process/residue checks only, with no internal-topology comparison and no Check & Save requirement.

