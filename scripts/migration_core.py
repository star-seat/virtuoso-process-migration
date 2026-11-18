#!/usr/bin/env python3
"""Pure offline planning and verification primitives for the skill.

This module never imports Virtuoso Bridge and never writes OpenAccess data.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


SCHEMA_VERSION = 1
ALLOWED_VIEWS: Tuple[str, ...] = ("schematic", "symbol", "systemVerilog", "veriloga")
PROCESS_KINDS = {"mos", "resistor", "capacitor", "standard_cell"}
DECISIONS = {"overwrite", "rename", "reuse"}
DIMENSION_UNITS = {
    "": Decimal("1"),
    "m": Decimal("1e-3"),
    "mm": Decimal("1e-3"),
    "u": Decimal("1e-6"),
    "um": Decimal("1e-6"),
    "n": Decimal("1e-9"),
    "nm": Decimal("1e-9"),
    "p": Decimal("1e-12"),
    "pm": Decimal("1e-12"),
    "f": Decimal("1e-15"),
    "fm": Decimal("1e-15"),
}


class MigrationDataError(ValueError):
    """Raised when an input cannot be interpreted safely."""


def read_json(path: str | Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise MigrationDataError(f"{path}: top-level JSON value must be an object")
    return data


def write_json(path: str | Path, data: Mapping[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def cell_key(library: str, cell: str) -> str:
    return f"{library}/{cell}"


def master_key(master: Mapping[str, Any]) -> str:
    return cell_key(str(master.get("library", "")), str(master.get("cell", "")))


def stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:12]}"


def parse_dimension(value: Any) -> Decimal:
    """Parse a Cadence/SPICE-style dimension into metres."""

    if isinstance(value, (int, float, Decimal)):
        result = Decimal(str(value))
    else:
        match = re.fullmatch(
            r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([munpf]m?|)\s*",
            str(value),
        )
        if not match:
            raise MigrationDataError(f"unsupported dimension: {value!r}")
        try:
            result = Decimal(match.group(1)) * DIMENSION_UNITS[match.group(2).lower()]
        except (InvalidOperation, KeyError) as exc:
            raise MigrationDataError(f"unsupported dimension: {value!r}") from exc
    if result <= 0:
        raise MigrationDataError(f"dimension must be positive: {value!r}")
    return result


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_dimension(value_m: Decimal, unit: str) -> str:
    scale = DIMENSION_UNITS[unit]
    return f"{_decimal_text(value_m / scale)}{unit}"


def default_mos_dimensions(width: Any, length: Any, target_minimum_length_nm: Any) -> Dict[str, Any]:
    """Raise L to the target minimum and scale W equally, preserving W/L."""

    source_w = parse_dimension(width)
    source_l = parse_dimension(length)
    target_min_l = Decimal(str(target_minimum_length_nm)) * DIMENSION_UNITS["n"]
    if target_min_l <= 0:
        raise MigrationDataError("target minimum channel length must be positive")
    scale = max(Decimal("1"), target_min_l / source_l)
    target_l = source_l * scale
    target_w = source_w * scale
    return {
        "mode": "preserve_w_over_l_raise_length_to_target_min",
        "scale_factor": _decimal_text(scale),
        "w": format_dimension(target_w, "u"),
        "l": format_dimension(target_l, "n"),
        "preserve_fingers": True,
        "preserve_multiplier": True,
    }


def _positive_integer_property(inst: Mapping[str, Any], name: str, default: int | None = None) -> int:
    value = _instance_property(inst, name)
    if value is None and default is not None:
        return default
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise MigrationDataError(f"{name} must be a positive integer, got {value!r}") from exc
    if parsed <= 0:
        raise MigrationDataError(f"{name} must be a positive integer, got {value!r}")
    return parsed


def _callback_fields(sequence: Sequence[Any]) -> List[str]:
    return [str(item.get("field")) if isinstance(item, Mapping) else str(item) for item in sequence]


def _validate_and_compute_mos_cdf(
    inst: Mapping[str, Any],
    target_master: Mapping[str, Any],
    contract: Mapping[str, Any],
    target_process: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any] | None]:
    """Validate cached/live CDF semantics and create a target-ready sizing plan."""

    blockers: List[Dict[str, Any]] = []
    source = contract.get("source", {})
    target = contract.get("target", {})
    contract_id = contract.get("contract_id")
    required_contract_keys = (
        "length_field",
        "total_width_field",
        "per_finger_width_field",
        "fingers_field",
        "multiplier_field",
    )
    for side_name, side in (("source", source), ("target", target)):
        missing = [key for key in required_contract_keys if not side.get(key)]
        if missing:
            blockers.append(
                _blocker(
                    "mos_cdf_contract_incomplete",
                    instance=inst.get("name"),
                    side=side_name,
                    missing=missing,
                    contract_id=contract_id,
                )
            )
    callback_sequence = contract.get("callback_sequence", [])
    expected_callbacks = _callback_fields(callback_sequence)
    if not expected_callbacks:
        blockers.append(
            _blocker("mos_cdf_callback_sequence_missing", instance=inst.get("name"), contract_id=contract_id)
        )
    if blockers:
        return blockers, None

    source_names = {str(prop.get("name", "")).lower() for prop in inst.get("properties", [])}
    required_source_fields = [
        str(source["length_field"]),
        str(source["total_width_field"]),
        str(source["per_finger_width_field"]),
        str(source["fingers_field"]),
        str(source["multiplier_field"]),
    ]
    missing_source = [field for field in required_source_fields if field.lower() not in source_names]
    if missing_source:
        blockers.append(
            _blocker(
                "mos_source_cdf_fields_missing",
                instance=inst.get("name"),
                fields=missing_source,
                contract_id=contract_id,
            )
        )

    live = target_master.get("cdf_validation", {})
    if contract.get("requires_live_revalidation", True):
        if live.get("validated") is not True or (contract_id and live.get("contract_id") != contract_id):
            blockers.append(
                _blocker(
                    "mos_target_cdf_contract_not_revalidated",
                    instance=inst.get("name"),
                    target=master_key(target_master),
                    contract_id=contract_id,
                )
            )
        live_fields = {str(item) for item in live.get("fields", [])}
        expected_target_fields = {
            str(target["length_field"]),
            str(target["total_width_field"]),
            str(target["per_finger_width_field"]),
            str(target["fingers_field"]),
            str(target["multiplier_field"]),
        }
        if live_fields != expected_target_fields:
            blockers.append(
                _blocker(
                    "mos_target_cdf_fields_mismatch",
                    instance=inst.get("name"),
                    expected=sorted(expected_target_fields),
                    actual=sorted(live_fields),
                    contract_id=contract_id,
                )
            )
        live_callbacks = [str(item) for item in live.get("callback_sequence", [])]
        if live_callbacks != expected_callbacks:
            blockers.append(
                _blocker(
                    "mos_target_cdf_callback_order_mismatch",
                    instance=inst.get("name"),
                    expected=expected_callbacks,
                    actual=live_callbacks,
                    contract_id=contract_id,
                )
            )
    if blockers:
        return blockers, None

    try:
        source_total = parse_dimension(_instance_property(inst, str(source["total_width_field"])))
        source_per_finger = parse_dimension(_instance_property(inst, str(source["per_finger_width_field"])))
        source_length = parse_dimension(_instance_property(inst, str(source["length_field"])))
        fingers = _positive_integer_property(inst, str(source["fingers_field"]))
        multiplier = _positive_integer_property(inst, str(source["multiplier_field"]), default=1)
    except MigrationDataError as exc:
        blockers.append(
            _blocker(
                "mos_source_cdf_semantics_unreadable",
                instance=inst.get("name"),
                contract_id=contract_id,
                error=str(exc),
            )
        )
        return blockers, None

    tolerance = Decimal(str(contract.get("relation_tolerance", "1e-9")))
    expected_total = source_per_finger * Decimal(fingers)
    relative_error = abs(source_total - expected_total) / source_total
    if relative_error > tolerance:
        blockers.append(
            _blocker(
                "mos_source_width_relation_mismatch",
                instance=inst.get("name"),
                total_field=source.get("total_width_field"),
                per_finger_field=source.get("per_finger_width_field"),
                fingers_field=source.get("fingers_field"),
                relative_error=_decimal_text(relative_error),
                contract_id=contract_id,
            )
        )
        return blockers, None

    canonical = default_mos_dimensions(
        format_dimension(source_total, "u"),
        format_dimension(source_length, "n"),
        target_process.get("minimum_channel_length_nm"),
    )
    target_total = parse_dimension(canonical["w"])
    target_per_finger = target_total / Decimal(fingers)
    canonical.update(
        {
            "contract_id": contract_id,
            "canonical_width_semantics": "total_width",
            "source_fields": {
                "l": str(source["length_field"]),
                "total_width": str(source["total_width_field"]),
                "per_finger_width": str(source["per_finger_width_field"]),
                "fingers": str(source["fingers_field"]),
                "multiplier": str(source["multiplier_field"]),
            },
            "target_cdf_values": {
                str(target["length_field"]): canonical["l"],
                str(target["total_width_field"]): format_dimension(target_total, "u"),
                str(target["per_finger_width_field"]): format_dimension(target_per_finger, "u"),
                str(target["fingers_field"]): fingers,
                str(target["multiplier_field"]): multiplier,
            },
            "callback_sequence": copy.deepcopy(callback_sequence),
        }
    )
    return blockers, canonical


def _instance_property(inst: Mapping[str, Any], name: str) -> Any:
    for prop in inst.get("properties", []):
        if str(prop.get("name", "")).lower() == name.lower():
            return prop.get("value")
    return None


def _require_schema(data: Mapping[str, Any], label: str) -> None:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise MigrationDataError(f"{label}: schema_version must be {SCHEMA_VERSION}")


def _cell_index(section: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for cell in section.get("cells", []):
        key = cell_key(str(cell.get("library", "")), str(cell.get("cell", "")))
        if key in result:
            raise MigrationDataError(f"duplicate cell inventory entry: {key}")
        result[key] = cell
    return result


def _master_index(section: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for master in section.get("masters", []):
        key = master_key(master)
        if key in result:
            raise MigrationDataError(f"duplicate target master entry: {key}")
        result[key] = master
    return result


def _schematic(cell: Mapping[str, Any]) -> Mapping[str, Any]:
    return cell.get("views", {}).get("schematic", {})


def reachable_source(audit: Mapping[str, Any]) -> Tuple[List[str], Dict[str, int], List[Dict[str, Any]]]:
    """Return bottom-up reachable project cells, call counts, and blockers."""

    source = audit.get("source", {})
    index = _cell_index(source)
    project_libs = set(source.get("project_libraries", []))
    top = source.get("top", {})
    top_key = cell_key(str(top.get("library", "")), str(top.get("cell", "")))
    blockers: List[Dict[str, Any]] = []
    calls: Counter[str] = Counter()
    visiting: set[str] = set()
    visited: set[str] = set()
    order: List[str] = []

    def visit(key: str, caller: str | None = None) -> None:
        calls[key] += 1
        if key in visited:
            return
        if key in visiting:
            payload = {"type": "hierarchy_cycle", "cell": key, "caller": caller}
            payload["id"] = stable_id("block", payload)
            blockers.append(payload)
            return
        cell = index.get(key)
        if cell is None:
            payload = {"type": "missing_source_project_cell", "cell": key, "caller": caller}
            payload["id"] = stable_id("block", payload)
            blockers.append(payload)
            return
        visiting.add(key)
        for inst in _schematic(cell).get("instances", []):
            lib = str(inst.get("library", ""))
            kind = inst.get("kind")
            if kind == "project" or lib in project_libs:
                visit(cell_key(lib, str(inst.get("cell", ""))), caller=key)
        visiting.remove(key)
        visited.add(key)
        order.append(key)

    visit(top_key)
    return order, dict(sorted(calls.items())), blockers


def _view_names(cell: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    present = set(cell.get("views", {}).keys())
    included = [name for name in ALLOWED_VIEWS if name in present]
    excluded = sorted(present - set(ALLOWED_VIEWS))
    return included, excluded


def _terminal_signature(view: Mapping[str, Any]) -> List[Tuple[Any, ...]]:
    signature = []
    for terminal in view.get("terminals", []):
        signature.append(
            (
                terminal.get("name"),
                terminal.get("direction"),
                terminal.get("bus"),
                terminal.get("order"),
            )
        )
    return signature


def interface_compatible(source_cell: Mapping[str, Any], target_cell: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    for view_name in ("schematic", "symbol"):
        source_view = source_cell.get("views", {}).get(view_name)
        target_view = target_cell.get("views", {}).get(view_name)
        if source_view is None:
            continue
        if target_view is None:
            reasons.append(f"target missing {view_name}")
            continue
        if _terminal_signature(source_view) != _terminal_signature(target_view):
            reasons.append(f"{view_name} terminal signature differs")
    return not reasons, reasons


def _base_targets(audit: Mapping[str, Any], reachable: Sequence[str]) -> Dict[str, Dict[str, str]]:
    library_map = audit.get("library_map", {})
    source_index = _cell_index(audit.get("source", {}))
    targets: Dict[str, Dict[str, str]] = {}
    for key in reachable:
        source_cell = source_index[key]
        source_lib = str(source_cell.get("library", ""))
        if source_lib not in library_map:
            raise MigrationDataError(f"library_map has no target for project library {source_lib}")
        targets[key] = {"library": str(library_map[source_lib]), "cell": str(source_cell.get("cell", ""))}
    return targets


def suggest_rename_candidates(
    audit: Mapping[str, Any], target_library: str, base_cell: str, count: int = 3, reserved: Iterable[str] = ()
) -> List[str]:
    """Suggest free names from a frozen target inventory.

    Suggestions are convenience only; build_plan must re-scan the selected name.
    """

    _require_schema(audit, "audit")
    if count < 1:
        raise MigrationDataError("count must be positive")
    used = {
        str(item.get("cell"))
        for item in audit.get("target", {}).get("cells", [])
        if item.get("library") == target_library
    }
    used.update(str(item) for item in reserved)
    result: List[str] = []
    ordinal = 1
    while len(result) < count:
        candidate = f"{base_cell}_MIG" if ordinal == 1 else f"{base_cell}_MIG_{ordinal}"
        if candidate not in used:
            result.append(candidate)
            used.add(candidate)
        ordinal += 1
    return result


def _normalized_decisions(decisions_doc: Mapping[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    raw = (decisions_doc or {}).get("decisions", {})
    if not isinstance(raw, dict):
        raise MigrationDataError("decisions must be an object")
    result: Dict[str, Dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict) or value.get("action") not in DECISIONS:
            raise MigrationDataError(f"invalid decision for {key}")
        result[str(key)] = copy.deepcopy(value)
    return result


def _conflict(kind: str, source: str | None, target: str | None, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"type": kind, "source": source, "target": target, **extra}
    payload["id"] = stable_id("conflict", payload)
    return payload


def _blocker(kind: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"type": kind, **extra}
    payload["id"] = stable_id("block", payload)
    return payload


def _mapping_groups(profile: Mapping[str, Any]) -> Dict[str, List[Mapping[str, Any]]]:
    return {
        "standard_cell": list(profile.get("standard_cells", [])),
        "mos": list(profile.get("mos", [])),
        "resistor": list(profile.get("resistors", [])),
        "capacitor": list(profile.get("capacitors", [])),
    }


def _find_mapping(records: Iterable[Mapping[str, Any]], inst: Mapping[str, Any]) -> Mapping[str, Any] | None:
    candidates = []
    for record in records:
        source = record.get("source", {})
        if source.get("cell") != inst.get("cell"):
            continue
        if source.get("library") not in (None, "", inst.get("library")):
            continue
        candidates.append(record)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _terminal_names(master: Mapping[str, Any]) -> List[str]:
    return [str(item.get("name")) for item in master.get("terminals", [])]


def _validate_device_mapping(
    inst: Mapping[str, Any],
    record: Mapping[str, Any],
    target_masters: Mapping[str, Mapping[str, Any]],
    target_process: Mapping[str, Any],
    default_mos_policy: Mapping[str, Any] | None,
    profile_mos_cdf_contract: Mapping[str, Any] | None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any] | None]:
    blockers: List[Dict[str, Any]] = []
    status = record.get("status")
    if status not in {"verified", "historically_verified"}:
        blockers.append(_blocker("unverified_device_mapping", instance=inst.get("name"), source=master_key(inst)))
    target = record.get("target", {})
    target_key = master_key(target)
    target_master = target_masters.get(target_key)
    if target_master is None:
        blockers.append(_blocker("missing_target_master", instance=inst.get("name"), target=target_key))
        return blockers, None
    if record.get("requires_target_revalidation") and not target_master.get("validated", False):
        blockers.append(_blocker("target_master_not_revalidated", instance=inst.get("name"), target=target_key))
    expected_process = target_process.get("id")
    if target_master.get("process") not in (None, expected_process):
        blockers.append(
            _blocker(
                "target_master_process_mismatch",
                instance=inst.get("name"),
                target=target_key,
                expected=expected_process,
                actual=target_master.get("process"),
            )
        )

    source_pins = set(inst.get("terminals", {}).keys())
    target_pins = set(_terminal_names(target_master))
    pin_map = record.get("pin_map")
    if pin_map == "identity":
        mapped_pins = source_pins
    elif isinstance(pin_map, dict):
        if set(pin_map.keys()) != source_pins:
            blockers.append(_blocker("incomplete_pin_map", instance=inst.get("name"), source=master_key(inst)))
        mapped_pins = {str(pin_map.get(pin, "")) for pin in source_pins}
    else:
        blockers.append(_blocker("missing_pin_map", instance=inst.get("name"), source=master_key(inst)))
        mapped_pins = set()
    if mapped_pins != target_pins:
        blockers.append(
            _blocker(
                "target_pin_mismatch",
                instance=inst.get("name"),
                target=target_key,
                mapped=sorted(mapped_pins),
                actual=sorted(target_pins),
            )
        )

    effective_parameter_strategy = copy.deepcopy(record.get("parameter_strategy"))
    computed_target_params = None
    sizing_source = "mapping_record" if effective_parameter_strategy else None
    cdf_contract = record.get("mos_cdf_contract") or profile_mos_cdf_contract
    if inst.get("kind") == "mos" and cdf_contract:
        semantic_blockers, semantic_plan = _validate_and_compute_mos_cdf(
            inst, target_master, cdf_contract, target_process
        )
        blockers.extend(semantic_blockers)
        if semantic_plan is not None and not effective_parameter_strategy:
            computed_target_params = semantic_plan
            effective_parameter_strategy = copy.deepcopy(default_mos_policy)
            sizing_source = "profile_default_with_cdf_contract"
    if inst.get("kind") == "mos" and not effective_parameter_strategy:
        if not default_mos_policy or default_mos_policy.get("mode") != "preserve_w_over_l_raise_length_to_target_min":
            blockers.append(_blocker("missing_mos_parameter_strategy", instance=inst.get("name"), source=master_key(inst)))
        elif cdf_contract:
            # A present contract failed validation above. Never fall back to a
            # loose compatibility flag that could reinterpret w/Wfg/wf.
            pass
        elif record.get("width_semantics_compatible") is not True:
            blockers.append(_blocker("mos_width_semantics_not_confirmed", instance=inst.get("name"), source=master_key(inst)))
        elif target_process.get("minimum_channel_length_nm") is None:
            blockers.append(_blocker("target_minimum_channel_length_missing", instance=inst.get("name"), source=master_key(inst)))
        else:
            property_names = record.get("property_names", {})
            width_name = str(property_names.get("width", "w"))
            length_name = str(property_names.get("length", "l"))
            width = _instance_property(inst, width_name)
            length = _instance_property(inst, length_name)
            if width is None or length is None:
                blockers.append(
                    _blocker(
                        "mos_dimensions_missing",
                        instance=inst.get("name"),
                        source=master_key(inst),
                        width_property=width_name,
                        length_property=length_name,
                    )
                )
            else:
                try:
                    computed_target_params = default_mos_dimensions(
                        width, length, target_process.get("minimum_channel_length_nm")
                    )
                    effective_parameter_strategy = copy.deepcopy(default_mos_policy)
                    sizing_source = "profile_default"
                except MigrationDataError as exc:
                    blockers.append(
                        _blocker(
                            "mos_dimension_parse_failed",
                            instance=inst.get("name"),
                            source=master_key(inst),
                            error=str(exc),
                        )
                    )
    if inst.get("kind") in {"resistor", "capacitor"} and not record.get("value_strategy"):
        blockers.append(_blocker("missing_passive_value_strategy", instance=inst.get("name"), source=master_key(inst)))

    action = {
        "instance": inst.get("name"),
        "kind": inst.get("kind"),
        "source_master": master_key(inst),
        "target_master": target_key,
        "mapping_status": status,
        "pin_map": pin_map,
        "parameter_strategy": effective_parameter_strategy,
        "sizing_source": sizing_source,
        "computed_target_params": computed_target_params,
        "value_strategy": record.get("value_strategy"),
        "source_terminals": copy.deepcopy(inst.get("terminals", {})),
        "requires_cdf_readback": inst.get("kind") in {"mos", "resistor", "capacitor"},
        "mos_cdf_contract_id": cdf_contract.get("contract_id") if cdf_contract else None,
        "body_reconnect": (
            {
                "source_terminal": "B",
                "source_net": inst.get("terminals", {}).get("B"),
                "target_pin_geometry_required": True,
                "target_net_geometry_required": True,
                "infer_net_from_name_or_device_type": False,
                "postcondition": "target B instTerm is on the recorded source body-net endpoint partition",
            }
            if inst.get("kind") == "mos" and "B" in inst.get("terminals", {})
            else None
        ),
    }
    return blockers, action


def build_plan(
    audit: Mapping[str, Any], profile: Mapping[str, Any], decisions_doc: Mapping[str, Any] | None = None
) -> Dict[str, Any]:
    _require_schema(audit, "audit")
    _require_schema(profile, "profile")
    source = audit.get("source", {})
    target = audit.get("target", {})
    source_index = _cell_index(source)
    target_index = _cell_index(target)
    target_masters = _master_index(target)
    reachable, call_counts, blockers = reachable_source(audit)
    decisions = _normalized_decisions(decisions_doc)
    targets = _base_targets(audit, reachable)
    conflicts: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []

    profile_source = profile.get("source_process", {})
    profile_target = profile.get("target_process", {})
    effective_target_process = copy.deepcopy(profile_target)
    effective_target_process.update(target.get("process", {}))
    if profile_source.get("id") != source.get("process", {}).get("id"):
        blockers.append(_blocker("source_process_profile_mismatch", expected=source.get("process"), profile=profile_source))
    if profile_target.get("id") != target.get("process", {}).get("id"):
        blockers.append(_blocker("target_process_profile_mismatch", expected=target.get("process"), profile=profile_target))
    for side, expected, actual in (
        ("source", profile_source, source.get("process", {})),
        ("target", profile_target, target.get("process", {})),
    ):
        if expected.get("revision") not in (None, "project-record-unspecified", actual.get("revision")):
            blockers.append(_blocker(f"{side}_process_revision_mismatch", expected=expected, actual=actual))
        elif expected.get("revision") == "project-record-unspecified":
            notes.append({"type": f"{side}_profile_revision_unspecified", "requires_live_revalidation": True})

    # Apply user rename/reuse destinations before collision analysis.
    for key, decision in decisions.items():
        if key not in targets:
            blockers.append(_blocker("decision_for_unreachable_cell", source=key))
            continue
        if not decision.get("confirmed", False):
            blockers.append(_blocker("unconfirmed_decision", source=key, action=decision.get("action")))
        if decision.get("action") == "rename":
            new_cell = str(decision.get("new_cell", ""))
            if not new_cell:
                blockers.append(_blocker("missing_rename_target", source=key))
            else:
                targets[key]["cell"] = new_cell
        elif decision.get("action") == "reuse":
            targets[key] = {
                "library": str(decision.get("target_library", targets[key]["library"])),
                "cell": str(decision.get("target_cell", targets[key]["cell"])),
            }

    # Detect two source cells collapsing into a target.
    reverse: Dict[str, List[str]] = defaultdict(list)
    for key, target_ref in targets.items():
        reverse[cell_key(target_ref["library"], target_ref["cell"])].append(key)
    for target_key, sources in sorted(reverse.items()):
        if len(sources) > 1:
            conflict = _conflict("multiple_sources_same_target", None, target_key, sources=sorted(sources))
            conflict["resolved"] = all(
                decisions.get(source_key, {}).get("action") == "reuse"
                and decisions.get(source_key, {}).get("confirmed", False)
                for source_key in sources
            )
            conflicts.append(conflict)

    # Detect same names from different source libraries even when distinct target libraries keep them safe.
    names: Dict[str, set[str]] = defaultdict(set)
    for key in reachable:
        source_cell = source_index[key]
        names[str(source_cell.get("cell"))].add(str(source_cell.get("library")))
    for name, libs in sorted(names.items()):
        if len(libs) > 1:
            notes.append({"type": "same_name_different_source_libraries", "cell": name, "libraries": sorted(libs)})

    cell_actions: List[Dict[str, Any]] = []
    reference_actions: List[Dict[str, Any]] = []
    device_actions: List[Dict[str, Any]] = []
    mapping_groups = _mapping_groups(profile)
    allowed_external = set(audit.get("external_allow_libraries", []))

    for key in reachable:
        source_cell = source_index[key]
        target_ref = targets[key]
        target_key = cell_key(target_ref["library"], target_ref["cell"])
        target_cell = target_index.get(target_key)
        decision = decisions.get(key)
        included_views, excluded_views = _view_names(source_cell)
        if excluded_views:
            notes.append({"type": "excluded_views_skipped", "source": key, "views": excluded_views})

        existing_views = []
        target_present_views: List[str] = []
        if target_cell:
            target_present_views = sorted(target_cell.get("views", {}).keys())
            existing_views = [view for view in included_views if view in target_cell.get("views", {})]
        if target_cell:
            relation = "unknown"
            comparisons = []
            for view in existing_views:
                src_fp = source_cell.get("views", {}).get(view, {}).get("fingerprint")
                dst_fp = target_cell.get("views", {}).get(view, {}).get("fingerprint")
                comparisons.append(src_fp is not None and src_fp == dst_fp)
            if comparisons and all(comparisons):
                relation = "same_fingerprint"
            elif any(item is False for item in comparisons):
                relation = "different_fingerprint"
            conflict = _conflict(
                "target_cell_exists",
                key,
                target_key,
                existing_views=target_present_views,
                overlapping_planned_views=existing_views,
                relation=relation,
                incoming_callers=copy.deepcopy(target_cell.get("callers", [])),
            )
            resolved = False
            if decision and decision.get("confirmed"):
                action = decision.get("action")
                if action == "overwrite":
                    authorized = set(decision.get("authorized_views", []))
                    if not set(included_views).issubset(authorized):
                        blockers.append(_blocker("overwrite_views_not_authorized", source=key, required=included_views))
                    elif not decision.get("backup_root"):
                        blockers.append(_blocker("overwrite_backup_missing", source=key, target=target_key))
                    else:
                        resolved = True
                elif action == "reuse":
                    compatible, reasons = interface_compatible(source_cell, target_cell)
                    missing_reuse_views = sorted(set(included_views) - set(target_cell.get("views", {})))
                    if missing_reuse_views:
                        reasons.append(f"target missing allowed views: {missing_reuse_views}")
                        compatible = False
                    target_process = target_cell.get("process", target.get("process", {}).get("id"))
                    process_ok = target_process == target.get("process", {}).get("id")
                    residue = target_cell.get("residual_references", [])
                    if compatible and process_ok and not residue:
                        resolved = True
                    else:
                        blockers.append(
                            _blocker(
                                "reuse_incompatible",
                                source=key,
                                target=target_key,
                                reasons=reasons,
                                process_ok=process_ok,
                                residual_references=residue,
                            )
                        )
                elif action == "rename":
                    # Existing views at a renamed destination mean the rename itself collided.
                    blockers.append(_blocker("rename_target_exists", source=key, target=target_key, views=existing_views))
            conflict["resolved"] = resolved
            conflicts.append(conflict)
        elif decision and decision.get("action") == "reuse":
            blockers.append(_blocker("reuse_target_missing", source=key, target=target_key))
        elif decision and decision.get("action") == "overwrite":
            blockers.append(_blocker("overwrite_target_missing", source=key, target=target_key))

        action_name = "copy"
        if decision and decision.get("action") == "overwrite":
            action_name = "overwrite"
        elif decision and decision.get("action") == "reuse":
            action_name = "reuse"
        elif decision and decision.get("action") == "rename":
            action_name = "copy_renamed"
        view_actions = []
        if action_name != "reuse":
            for view in included_views:
                operation = "overwrite_view" if view in existing_views and action_name == "overwrite" else "copy_view"
                view_actions.append({"view": view, "operation": operation})
        cell_actions.append(
            {
                "source": key,
                "target": target_key,
                "action": action_name,
                "views": view_actions,
                "skipped_views": excluded_views,
                "backup_root": (decision or {}).get("backup_root"),
                "authorized_views": copy.deepcopy((decision or {}).get("authorized_views", [])),
            }
        )

        for inst in _schematic(source_cell).get("instances", []):
            inst_kind = inst.get("kind")
            inst_source_key = master_key(inst)
            if inst_kind == "project" or inst.get("library") in set(source.get("project_libraries", [])):
                if inst_source_key not in targets:
                    blockers.append(_blocker("project_reference_outside_reachable_plan", cell=key, instance=inst.get("name"), master=inst_source_key))
                else:
                    mapped = targets[inst_source_key]
                    reference_actions.append(
                        {
                            "cell": key,
                            "instance": inst.get("name"),
                            "source_master": inst_source_key,
                            "target_master": cell_key(mapped["library"], mapped["cell"]),
                        }
                    )
            elif inst_kind in PROCESS_KINDS:
                mapping = _find_mapping(mapping_groups[str(inst_kind)], inst)
                if mapping is None:
                    blockers.append(
                        _blocker(
                            "unknown_or_ambiguous_device_mapping",
                            cell=key,
                            instance=inst.get("name"),
                            master=inst_source_key,
                            device_kind=inst_kind,
                        )
                    )
                else:
                    mapping_blockers, mapping_action = _validate_device_mapping(
                        inst,
                        mapping,
                        target_masters,
                        effective_target_process,
                        profile.get("mos_default_sizing_policy"),
                        profile.get("mos_cdf_contract"),
                    )
                    blockers.extend(mapping_blockers)
                    if mapping_action:
                        mapping_action["cell"] = key
                        device_actions.append(mapping_action)
            elif inst_kind == "external":
                if inst.get("library") not in allowed_external:
                    blockers.append(_blocker("external_library_not_allowed", cell=key, instance=inst.get("name"), master=inst_source_key))
            else:
                blockers.append(_blocker("unclassified_instance", cell=key, instance=inst.get("name"), master=inst_source_key))

    unresolved = [item for item in conflicts if not item.get("resolved", False)]
    actions_by_cell: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for kind, actions in (("reference_rebind", reference_actions), ("device_rebuild", device_actions)):
        for item in actions:
            postcondition = {"instance": item.get("instance"), "master": item.get("target_master")}
            if kind == "device_rebuild":
                postcondition["cdf_readback_required"] = bool(item.get("requires_cdf_readback"))
                if item.get("body_reconnect"):
                    postcondition["body_endpoint"] = item["body_reconnect"]["postcondition"]
            payload = {
                "kind": kind,
                "cell": item.get("cell"),
                "instance": item.get("instance"),
                "target_master": item.get("target_master"),
            }
            actions_by_cell[str(item.get("cell"))].append(
                {
                    "action_id": stable_id("instance", payload),
                    "kind": kind,
                    "instance": item.get("instance"),
                    "postcondition": postcondition,
                }
            )
    execution_batches: List[Dict[str, Any]] = []
    for cell_action in cell_actions:
        if cell_action.get("action") == "reuse":
            continue
        source_key = str(cell_action.get("source"))
        instance_actions = actions_by_cell.get(source_key, [])
        execution_batches.append(
            {
                "batch_id": stable_id(
                    "cell-batch", {"source": source_key, "target": cell_action.get("target"), "actions": instance_actions}
                ),
                "source_cell": source_key,
                "target_cell": cell_action.get("target"),
                "transaction_scope": "one_cell_logical_transaction",
                "bridge_request_policy": "one_cell_level_skill_call_for_rebind_cdf_and_body",
                "operation_order": [
                    "capture_precondition",
                    "recreate_instance",
                    "set_target_cdf_and_run_callbacks",
                    "read_target_B_pin_geometry_and_bridge_recorded_body_net",
                    "evaluate_instance_postcondition",
                    "save_cell_checkpoint",
                ],
                "instance_actions": instance_actions,
                "recovery": "reconcile_each_instance_postcondition_before_selective_replay",
            }
        )
    plan: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked" if unresolved or blockers else "ready",
        "source_top": cell_key(str(source.get("top", {}).get("library", "")), str(source.get("top", {}).get("cell", ""))),
        "source_process": copy.deepcopy(source.get("process", {})),
        "target_process": copy.deepcopy(target.get("process", {})),
        "profile_id": profile.get("profile_id"),
        "input_hashes": {
            "audit": hashlib.sha256(json.dumps(audit, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "profile": hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "decisions": hashlib.sha256(json.dumps(decisions, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        },
        "allowed_views": list(ALLOWED_VIEWS),
        "reachable_bottom_up": reachable,
        "call_counts": call_counts,
        "cell_targets": {key: cell_key(value["library"], value["cell"]) for key, value in sorted(targets.items())},
        "cell_actions": cell_actions,
        "reference_actions": reference_actions,
        "device_actions": device_actions,
        "execution_batches": execution_batches,
        "snapshot_policy": {
            "mode": "phase_boundaries_only",
            "full_hierarchy_phases": ["inventory_frozen", "all_cell_batches_applied", "final_verification"],
            "between_phases": "targeted_instance_and_cell_postcondition_readback_only",
        },
        "conflicts": conflicts,
        "blockers": blockers,
        "notes": notes,
        "decisions_applied": decisions,
        "verification_required": [
            "planned_views_only",
            "project_reference_closure",
            "device_master_and_cdf_readback",
            "endpoint_topology_equivalence",
            "symbol_schematic_interface",
            "residual_source_process_scan",
            "bottom_up_check_and_save",
        ],
        "completion_states": {
            "database_operations_complete": False,
            "process_checks_passed": False,
            "functional_simulation_passed": None,
        },
    }
    hash_input = copy.deepcopy(plan)
    plan["plan_hash"] = hashlib.sha256(
        json.dumps(hash_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


def validate_plan(plan: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if plan.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    if plan.get("status") != "ready":
        errors.append("plan status is not ready")
    if plan.get("blockers"):
        errors.append("plan contains blockers")
    if any(not item.get("resolved", False) for item in plan.get("conflicts", [])):
        errors.append("plan contains unresolved conflicts")
    if tuple(plan.get("allowed_views", [])) != ALLOWED_VIEWS:
        errors.append("allowed view list is not the exact V1 allowlist")
    target_sources: Dict[str, List[str]] = defaultdict(list)
    for source_key, target_key in plan.get("cell_targets", {}).items():
        target_sources[target_key].append(source_key)
    actions_by_source = {item.get("source"): item for item in plan.get("cell_actions", [])}
    for target_key, source_keys in target_sources.items():
        if len(source_keys) > 1 and not all(actions_by_source.get(key, {}).get("action") == "reuse" for key in source_keys):
            errors.append(f"two non-reuse source cells map to the same target: {target_key}")
    for cell_action in plan.get("cell_actions", []):
        for view_action in cell_action.get("views", []):
            if view_action.get("view") not in ALLOWED_VIEWS:
                errors.append(f"disallowed view action: {view_action.get('view')}")
            if view_action.get("operation") not in {"copy_view", "overwrite_view"}:
                errors.append(f"unsafe view operation: {view_action.get('operation')}")
        if cell_action.get("action") == "overwrite" and not cell_action.get("backup_root"):
            errors.append(f"overwrite lacks backup: {cell_action.get('target')}")
        if cell_action.get("action") == "overwrite":
            planned = {item.get("view") for item in cell_action.get("views", [])}
            authorized = set(cell_action.get("authorized_views", []))
            if not planned.issubset(authorized):
                errors.append(f"overwrite has unauthorized views: {cell_action.get('target')}")
    expected_batch_cells = {
        item.get("source") for item in plan.get("cell_actions", []) if item.get("action") != "reuse"
    }
    actual_batch_cells = [item.get("source_cell") for item in plan.get("execution_batches", [])]
    if expected_batch_cells != set(actual_batch_cells) or len(actual_batch_cells) != len(set(actual_batch_cells)):
        errors.append("execution batches do not cover every non-reuse cell exactly")
    for batch in plan.get("execution_batches", []):
        if batch.get("transaction_scope") != "one_cell_logical_transaction":
            errors.append(f"unsafe transaction scope: {batch.get('target_cell')}")
        action_ids = [item.get("action_id") for item in batch.get("instance_actions", [])]
        if len(action_ids) != len(set(action_ids)):
            errors.append(f"duplicate instance action ID in batch: {batch.get('target_cell')}")
        if any(not item.get("postcondition") for item in batch.get("instance_actions", [])):
            errors.append(f"instance action lacks postcondition: {batch.get('target_cell')}")
    if plan.get("snapshot_policy", {}).get("mode") != "phase_boundaries_only":
        errors.append("snapshot policy is not phase-boundary-only")
    return sorted(set(errors))


def endpoint_partitions(cell: Mapping[str, Any]) -> List[List[str]]:
    schematic = _schematic(cell)
    nets: Dict[str, List[str]] = defaultdict(list)
    for terminal in schematic.get("terminals", []):
        if terminal.get("net") is not None:
            nets[str(terminal.get("net"))].append(f"@/{terminal.get('name')}")
    for inst in schematic.get("instances", []):
        for terminal, net in inst.get("terminals", {}).items():
            nets[str(net)].append(f"{inst.get('name')}/{terminal}")
    return sorted(sorted(endpoints) for endpoints in nets.values() if endpoints)


def verify_migration(audit: Mapping[str, Any], plan: Mapping[str, Any], post: Mapping[str, Any]) -> Dict[str, Any]:
    """Compare a post-migration normalized snapshot with the source and ready plan."""

    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if validate_plan(plan):
        errors.append({"type": "input_plan_not_ready", "details": validate_plan(plan)})
    source_index = _cell_index(audit.get("source", {}))
    pre_target_index = _cell_index(audit.get("target", {}))
    post_index = _cell_index(post)
    reverse_targets = plan.get("cell_targets", {})

    for action in plan.get("cell_actions", []):
        source_key = action.get("source")
        target_key = action.get("target")
        source_cell = source_index.get(source_key, {})
        target_cell = post_index.get(target_key)
        if target_cell is None:
            errors.append({"type": "missing_target_cell", "target": target_key})
            continue
        planned_views = {item.get("view") for item in action.get("views", [])}
        if action.get("action") == "reuse":
            # Reuse is a read-only binding decision. Validate interface,
            # target process, and residue only; never compare internals or
            # require the reused cell to be modified/saved.
            compatible, reasons = interface_compatible(source_cell, target_cell)
            if not compatible:
                errors.append({"type": "reuse_interface_mismatch", "target": target_key, "reasons": reasons})
            target_process = target_cell.get("process", post.get("process", {}).get("id"))
            expected_process = plan.get("target_process", {}).get("id")
            if target_process != expected_process:
                errors.append(
                    {
                        "type": "reuse_process_mismatch",
                        "target": target_key,
                        "expected": expected_process,
                        "actual": target_process,
                    }
                )
            residue = target_cell.get("residual_references", [])
            if residue:
                errors.append({"type": "reuse_residual_process_references", "target": target_key, "items": residue})
            continue
        actual_views = set(target_cell.get("views", {}))
        preexisting_extra = set(pre_target_index.get(target_key, {}).get("views", {})) - set(ALLOWED_VIEWS)
        newly_created_extra = (actual_views - set(ALLOWED_VIEWS)) - preexisting_extra
        if newly_created_extra:
            errors.append({"type": "excluded_views_present", "target": target_key, "views": sorted(newly_created_extra)})
        missing = planned_views - actual_views
        if missing:
            errors.append({"type": "planned_views_missing", "target": target_key, "views": sorted(missing)})
        compatible, reasons = interface_compatible(source_cell, target_cell)
        if not compatible:
            errors.append({"type": "interface_mismatch", "target": target_key, "reasons": reasons})
        if endpoint_partitions(source_cell) != endpoint_partitions(target_cell):
            errors.append({"type": "endpoint_topology_mismatch", "target": target_key})

    source_project_libs = set(audit.get("source", {}).get("project_libraries", []))
    residual = list(post.get("residual_references", []))
    for cell in post.get("cells", []):
        for inst in _schematic(cell).get("instances", []):
            if inst.get("library") in source_project_libs:
                residual.append({"cell": cell_key(cell.get("library", ""), cell.get("cell", "")), "master": master_key(inst)})
    if residual:
        errors.append({"type": "residual_source_references", "items": residual})

    # Verify exact project and process-device rebinds by instance name.
    for action_group, expected_field in (
        (plan.get("reference_actions", []), "target_master"),
        (plan.get("device_actions", []), "target_master"),
    ):
        for action in action_group:
            source_cell_key = action.get("cell")
            target_cell_key = plan.get("cell_targets", {}).get(source_cell_key)
            target_cell = post_index.get(target_cell_key, {})
            instance = next(
                (item for item in _schematic(target_cell).get("instances", []) if item.get("name") == action.get("instance")),
                None,
            )
            expected_master = action.get(expected_field)
            if instance is None:
                errors.append({"type": "planned_instance_missing", "cell": target_cell_key, "instance": action.get("instance")})
            elif master_key(instance) != expected_master:
                errors.append(
                    {
                        "type": "instance_master_mismatch",
                        "cell": target_cell_key,
                        "instance": action.get("instance"),
                        "expected": expected_master,
                        "actual": master_key(instance),
                    }
                )

    check_results = post.get("check_and_save", {})
    for action in plan.get("cell_actions", []):
        needs_check = any(view.get("view") == "schematic" for view in action.get("views", []))
        if action.get("action") == "reuse":
            needs_check = False
        target_key = action.get("target")
        if needs_check and check_results.get(target_key) not in ("clean", {"errors": 0, "warnings": 0}):
            warnings.append({"type": "check_and_save_not_clean_or_missing", "target": target_key, "result": check_results.get(target_key)})

    cdf = post.get("cdf_readback", {})
    for action in plan.get("device_actions", []):
        if action.get("requires_cdf_readback"):
            key = f"{plan.get('cell_targets', {}).get(action.get('cell'))}/{action.get('instance')}"
            if key not in cdf:
                errors.append({"type": "missing_cdf_readback", "instance": key})

    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if not errors and not warnings else ("failed" if errors else "passed_with_warnings"),
        "errors": errors,
        "warnings": warnings,
        "completion_states": {
            "database_operations_complete": not errors,
            "process_checks_passed": not errors and not warnings,
            "functional_simulation_passed": post.get("simulation", {}).get("passed"),
        },
    }
    return result

