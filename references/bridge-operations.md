# Virtuoso Bridge operations

Use the current `virtuoso` skill as the authority for connection and API syntax. This reference records migration-specific sequencing and invariants; it is not a standalone untested writer.

## Read-only inspection

- Open the source schematic read-only and enumerate `cv~>instHeaders`; record each header's master library/cell/view and all instances under it. Do not rely on only resolved instance masters.
- Enumerate actual views from the library manager/database. Preserve exact case (`systemVerilog` is not `systemverilog`).
- Snapshot each instance before any rebind: name, transform, typed properties, master, and every instance-terminal-to-net endpoint.
- Enumerate target cells, views, callers, device masters, terminals, CDF parameters, and legality metadata separately.
- Serialize large SKILL results with an explicit delimiter such as `;;`; Bridge newline serialization has been inconsistent in practice.

## View creation/copy

Use database-level CellView copy/create calls only for exact planned views. Never copy a cell directory for a cross-process migration because it can pull layout, extracted data, locks, caches, and unplanned views. Skip an allowed view when it does not exist in the source; do not create a placeholder.

For a same-process clone, hand execution to `virtuoso-librarian`. For a cross-process schematic, a copied source view is only a temporary database starting point: every project reference and process-sensitive master must be rebound before the view can pass the process gate.

## Rebind

Direct master reassignment can be unreliable in IC6.1.8 environments. Execute the established pattern as a Cell-level transaction batch, not a separate Bridge request for every step:

1. capture the source instance and typed properties from the still-open source view;
2. capture all terminal endpoints;
3. delete the destination instance and recreate it with the planned target master, original name, and transform;
4. restore properties using their source `valueType`;
5. run target CDF callbacks in the mapped order and read total/per-finger width, length, fingers, and multiplier back;
6. restore endpoint connectivity by terminal/pin map;
7. for a MOS, read actual target B-pin figure geometry and actual recorded body-net wire geometry, create the shortest unambiguous bridge immediately, and verify the B endpoint partition;
8. evaluate that instance's postcondition before continuing;
9. after the batch, save and run `schCheck`/Check & Save for the Cell.

Send the loop as one Cell-level SKILL call when practical. This combines dozens of CDF updates and body bridges into a small number of Bridge requests without weakening recovery, because every instance retains its own journal entry and postcondition. Before live `load`, run `python scripts/lint_skill_source.py generated.il`; a failed preflight is a hard stop.

Do not infer a body net from device type, VDD/VSS names, or raw symbol origin. Target pin geometry is used only to locate the B endpoint; the destination net comes from the captured source endpoint and target-net geometry. If the target B pin has multiple figures, the net has no usable nearby geometry, or the shortest bridge is ambiguous, stop that instance and the Cell transaction. Re-scan endpoint partitions after every bridge; automatic wire repair has previously produced shorts.

## Overwrite and dialogs

Before the first authorized overwrite, create and verify the planned backup and record its hash. Mutate only the authorized target library/cell/views. Enable dialog protection before batch work. If Bridge/UI state becomes unreliable, stop that lane and reconnect rather than mixing screenshot/UI traffic into a command lane.

## Check & Save

Run bottom-up so a parent sees validated child masters. Capture errors and warnings per modified view. Never Check & Save a `reuse` target. A clean result permits the next database stage but does not prove legal CDF values, target-process closure, or functional simulation.

