# Device mapping

## Mapping profiles

Key profiles by source/target PDK name, process option, library, and revision. Each record has evidence status and must be revalidated against the live target master. Never select a mapping using a cell-name suffix alone.

## MOS

Map flavor explicitly: voltage class, threshold option, oxide/device family, well/body model, and terminal set. Define a parameter strategy per record: explicit target values, a reviewed scale rule, or preserve electrical geometry when legal. Handle `w`, `l`, fingers, multiplier, and effective parallel count separately; do not assume two PDKs use identical `nf`, `m`, or total-width semantics.

For the maintained 28 nm to 65 nm profile, the minimum legal channel lengths are 30 nm and 60 nm respectively. When the user gives no explicit sizing rule, use the profile default `preserve_w_over_l_raise_length_to_target_min`:

1. Read the source instance's actual `W` and `L` after source CDF evaluation.
2. Set `L_target = max(L_source, L_min_target)`.
3. Set `scale = L_target / L_source` and `W_target = W_source * scale` so `W/L` is preserved.
4. Keep finger count and multiplier unchanged; they are topology/parallelism parameters, not geometric scale factors.
5. Apply target grid/range rules and CDF callbacks, then read back `W`, `L`, fingers, and multiplier.

Thus `1u/30n` becomes `2u/60n`. A device already at `1u/90n` remains `1u/90n`; the default does not shrink devices during a reverse or already-legal migration. An explicit user rule always overrides this default.

Use the default only when the process profile contains the relevant minimum lengths and an exact CDF contract. Normalize source geometry to canonical total width, scale that value, and then derive every target field. A boolean “compatible” flag is acceptable only for legacy profiles where both sides truly use the same field semantics.

The maintained 28-to-65 nm contract caches these observed meanings: source `l`, total `w`, per-finger `Wfg`, `fingers`, `m`, with `w=Wfg*fingers`; target `l`, per-finger `w`, total `wf`, `fingers`, `m`, with `wf=w*fingers`. The observed target callback order is `l`, `fingers`, `m`, `wf`, and each value is set immediately before its callback. This cache avoids rediscovery but is not permission to skip live validation. Before writes, inventory the target master's fields and callback sequence, match the contract ID, and block on any discrepancy.

After setting parameters, execute target CDF callbacks in the profile-defined order, then read values back. If target limits or callbacks change a value, stop or record an explicitly accepted correction. Restore source terminal-to-net mappings, including body/bulk, from endpoints. During MOS reconstruction, use actual target B-pin geometry to immediately bridge the captured body endpoint to actual target-net geometry and verify its endpoint partition. Device type, supply-name guesses, and symbol position are not connectivity evidence.

## Resistors and capacitors

Match the requested electrical value using target CDF units and legal ranges. Record requested value, readback value, absolute/relative error, device flavor, and geometry mode. A close schematic value does not prove layout manufacturability.

## Standard cells

Mapping is mandatory and exact to library revision. Validate target master existence, pin names, directions, buses, power pins, and the profile's pin map before rebind. Similar names are not functional proof. Delay-cell names are especially unsafe: historical `DEL150... -> DEL015` and `DEL025... -> DEL005` mappings are project evidence, not numeric conversion formulas.

## Historical profile

`process-maps/tsmc-n28-to-n65-gp.json` records mappings observed in earlier PLL migrations. The `historically_verified` status means “observed and checked in that context”; `requires_target_revalidation` remains true. A different PDK/library revision must not inherit that status silently.

The historical file intentionally omits standard-cell library names and exact PDK revisions where the retained record did not establish them. Copy it into a run/site profile and fill those exact fields from live inventory; the planner will otherwise fail closed because no exact target master can be found.

