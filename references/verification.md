# Verification gates

## Database and hierarchy

- Every planned CellView exists; no unplanned or excluded view was created.
- Every reachable project master resolves to the planned target library/cell.
- Rename and reuse decisions are reflected in all callers.
- No two distinct source cells collapsed into one target unless explicitly reused.
- Bottom-up Check & Save reports are recorded for all copied schematics.

## Interface and topology

- Compare schematic terminals and symbol pins by name, direction, bus expression, and ordering where netlisting requires it.
- Compare each instance terminal endpoint and the resulting endpoint partitions. Net labels and wire coordinates are secondary.
- Detect opens, shorts, missing body connections, dropped bus bits, and extra terminals.
- Pin-coordinate differences do not imply different connectivity and must not drive automatic rewiring.

## Reuse-only path

For a `reuse` decision, validate the target schematic/symbol public interface by terminal/pin name, direction, bus expression, and required order. Confirm target process/revision and scan the reused Cell for source-project, source-PDK, model, and other prohibited residue. Do not compare its internal instances, endpoint partitions, fingerprints, or topology with the source implementation. Do not modify, save, or Check & Save the reused Cell. Verify only that every migrated caller binds to it through the approved interface.

## Process legality

- All process-sensitive masters use the target PDK/standard-cell profile.
- MOS flavors and parameter readbacks are legal; fingers/multiplier semantics are accounted for.
- R/C electrical values and errors are reported.
- No source project library, source PDK master, model include, corner, or legacy process marker remains unless explicitly allowed.

## Functional verification

Check & Save is syntax/database validation only. If simulation is requested, regenerate the netlist after migration and verify model files, corners, supplies, analyses, and outputs. Mark functional simulation passed only when the requested target-process run completes and its acceptance checks pass.

Report three independent states: database operations complete, process checks passed, and functional simulation passed.

