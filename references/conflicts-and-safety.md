# Conflicts and safety

## Full preflight scan

Scan the top and every reachable project subcell before the first write. Batch all findings into one decision set. Detect at least:

- target cell/view already exists, whether fingerprints match or differ;
- two source cells mapping to the same target library/cell;
- the same cell name arriving from different source libraries;
- generated or user-provided rename colliding with an existing or planned cell;
- one source cell invoked multiple times, recorded as usage rather than silently copied repeatedly;
- missing target masters, unresolved source masters, and disallowed views;
- references that would remain in source project libraries.

## Decisions

### Overwrite

Require `confirmed: true`, exact authorized target views, a recoverable backup root, and an incoming-caller impact list. Do not infer authorization for other views or cells. Back up before changing the first authorized view and record the backup artifact and hash in the run manifest.

### Rename

Suggest a free name, but re-scan a user-provided name. Update parent references only through the frozen source-to-target cell map. For Verilog-A/SystemVerilog, module-name changes need syntax-aware review; otherwise block the text view.

### Reuse

Require explicit confirmation that the target's functional intent is acceptable, plus matching target process/revision, schematic/symbol terminals, directions, buses, pin order where relevant, and no source-process residue. Validate the public contract only: do not require internal topology or fingerprint equality. Never modify, save, or Check & Save the reused target. Rebind callers only.

## Required stops

Stop even in unattended execution for an unresolved collision, unconfirmed overwrite/reuse, unknown device, ambiguous device flavor, illegal CDF value, incompatible interface, missing backup, unresolvable hierarchy master, or topology mismatch in a copied/converted Cell. Internal-topology mismatch is not evaluated for `reuse`.

Do not convert these stops into warnings. Persist a checkpoint with status `blocked` and a stable list of decision IDs so the task can resume without rescanning unrelated completed work.

