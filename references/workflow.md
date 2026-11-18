# Core workflow

## 1. Contract

Record these inputs before inventory: source top library/cell, source process and PDK revision, target library/process/revision, project-library map, whether the top is renamed, expected views, mapping-profile ID/revision, backup root, overwrite policy, functional intent, and required verification level.

An absent value is not permission to guess. Process revision, device flavor, target standard-cell library, or sizing policy can change the electrical result.

## 2. Read-only inventory

Traverse the schematic hierarchy with `instHeaders`, because master-resolved instance queries can miss unavailable or unresolved masters. For each reachable project cell record:

- actual existing views and content fingerprints;
- terminals, directions, bus syntax, and symbol pin order;
- instance name, master library/cell/view, transform, and typed properties;
- each instance terminal and its source net endpoint;
- process-device, standard-cell, passive, general-library, and project-cell classification;
- all incoming callers and repeated instance counts.

Inventory target cells and target device masters separately. A matching name is only a candidate, not proof of compatibility.

## 3. Plan before writes

Run the planner twice: first with no decisions to produce the batched collision list; then with the user's explicit decisions. Freeze the ready plan with a hash and run ID. Any later inventory/profile/decision change invalidates the ready plan.

Plan cells bottom-up. Each action must name the exact source, target, existing view, and reason. Project cells must resolve through the library map and final rename map. PDK, standard-cell, and shared-library references may remain only when the plan explicitly classifies them as permitted external references.

Before any write, resolve MOS width into a canonical total width and verify the source relation between total width, per-finger width, and finger count. Verify the target master exposes the exact cached target fields and callback sequence. Emit final target CDF values for total width, per-finger width, length, fingers, and multiplier. Missing or contradictory `w`/`Wfg`/`wf` semantics block the entire write phase.

## 4. Apply through Virtuoso Bridge

Use a fresh, non-poisoned Bridge lane. Enable dialog protection. Create or copy only planned CellViews. For a copied schematic, replace every process-sensitive instance master and every project reference according to the plan. Capture source properties before rebind; restore property types and connectivity after delete/recreate.

Treat each writable Cell as one logical transaction. Generate one Cell-level SKILL program that loops over its planned instances, rather than one Bridge request per property or wire. Each instance still has a stable action ID, precondition, and postcondition so recovery can selectively replay only unresolved instances. For a MOS, recreate it, set CDF fields and callbacks, read the real target B-pin geometry, and immediately bridge that pin to the recorded body-net geometry inside the same loop. Run offline source lint before `load`.

For text views, preserve exact view-name case. When a cell is renamed, do not edit Verilog-A or SystemVerilog module names by blind substitution. Parse or manually verify the module declaration, includes, discipline declarations, and hierarchical references; stop if the source is not safely transformable.

## 5. Verify and report

Take full hierarchy snapshots only at phase boundaries: frozen inventory, completion of all Cell transactions, and final verification. Between boundaries, read back only affected Cell/instance postconditions, CDF values, and endpoint partitions. Run bottom-up Check & Save at Cell checkpoints, then a final full-hierarchy audit. Compare topology by terminal endpoints, not wire geometry or net labels alone. Report every skipped absent view, reused cell, renamed reference, mapped device, CDF readback, warning, and unverified claim.

Reuse follows a separate read-only branch. Compare the public interface and scan target process/residue only. Do not compare source and target internal topology, modify the reused Cell, or run Check & Save on it.

## Same-process route

If source and target process/revision are the same and no device conversion is requested, delegate the physical clone to `virtuoso-librarian`. Still use this skill's conflict scan when target names already exist or when a rename/reuse decision is needed.

