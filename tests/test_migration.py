from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from migration_core import (  # noqa: E402
    ALLOWED_VIEWS,
    build_plan,
    default_mos_dimensions,
    suggest_rename_candidates,
    validate_plan,
    verify_migration,
)
from lint_skill_source import lint_text  # noqa: E402


def terminals(*names: str, coordinates: bool = False):
    result = []
    for index, name in enumerate(names):
        item = {"name": name, "direction": "input" if name != "Y" else "output", "order": index}
        if coordinates:
            item.update({"x": 100 + index, "y": 200 + index})
        result.append(item)
    return result


def cell(lib: str, name: str, instances=None, extra_views=None, coords=False):
    views = {
        "schematic": {
            "fingerprint": f"{lib}-{name}-sch",
            "terminals": [
                {**item, "net": "n_in" if item["name"] == "A" else "n_out"}
                for item in terminals("A", "Y", coordinates=coords)
            ],
            "instances": list(instances or []),
        },
        "symbol": {"fingerprint": f"{lib}-{name}-sym", "terminals": terminals("A", "Y", coordinates=coords)},
    }
    views.update(extra_views or {})
    return {"library": lib, "cell": name, "views": views}


def project_inst(name: str, lib: str, master: str):
    return {
        "name": name,
        "library": lib,
        "cell": master,
        "view": "symbol",
        "kind": "project",
        "terminals": {"A": "n_in", "Y": "n_out"},
        "properties": [],
    }


def process_inst(name: str, kind: str, lib: str, master: str, pins=None):
    return {
        "name": name,
        "library": lib,
        "cell": master,
        "view": "symbol",
        "kind": kind,
        "terminals": pins or {"D": "n_out", "G": "n_in", "S": "vss", "B": "vss"},
        "properties": [
            {"name": "w", "value": "500n", "valueType": "string"},
            {"name": "l", "value": "30n", "valueType": "string"},
        ],
    }


def base_audit():
    leaf = cell("SRC", "LEAF")
    top = cell("SRC", "TOP", [project_inst("XLEAF", "SRC", "LEAF")])
    return {
        "schema_version": 1,
        "source": {
            "process": {"id": "n28", "revision": "r1"},
            "project_libraries": ["SRC"],
            "top": {"library": "SRC", "cell": "TOP"},
            "cells": [top, leaf],
        },
        "target": {
            "process": {"id": "n65", "revision": "r2"},
            "project_libraries": ["DST"],
            "cells": [],
            "masters": [],
        },
        "library_map": {"SRC": "DST"},
        "external_allow_libraries": ["analogLib", "basic"],
    }


def base_profile():
    return {
        "schema_version": 1,
        "profile_id": "test-n28-n65",
        "source_process": {"id": "n28", "revision": "r1"},
        "target_process": {"id": "n65", "revision": "r2"},
        "standard_cells": [],
        "mos": [],
        "resistors": [],
        "capacitors": [],
    }


def decision(action: str, **kwargs):
    return {"action": action, "confirmed": True, **kwargs}


class ConflictTests(unittest.TestCase):
    def test_top_conflict_is_batched_and_blocks(self):
        audit = base_audit()
        audit["target"]["cells"].append(cell("DST", "TOP"))
        plan = build_plan(audit, base_profile())
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any(item["source"] == "SRC/TOP" for item in plan["conflicts"]))

    def test_leaf_conflict_blocks_before_any_copy(self):
        audit = base_audit()
        audit["target"]["cells"].append(cell("DST", "LEAF"))
        plan = build_plan(audit, base_profile())
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any(item["source"] == "SRC/LEAF" for item in plan["conflicts"]))

    def test_multiple_conflicts_return_together(self):
        audit = base_audit()
        audit["target"]["cells"] = [cell("DST", "TOP"), cell("DST", "LEAF")]
        plan = build_plan(audit, base_profile())
        sources = {item["source"] for item in plan["conflicts"] if item["type"] == "target_cell_exists"}
        self.assertEqual(sources, {"SRC/TOP", "SRC/LEAF"})

    def test_existing_cell_with_only_excluded_view_still_requires_decision(self):
        audit = base_audit()
        audit["target"]["cells"].append(
            {"library": "DST", "cell": "TOP", "views": {"layout": {"fingerprint": "existing-layout"}}}
        )
        plan = build_plan(audit, base_profile())
        self.assertEqual(plan["status"], "blocked")
        conflict = next(item for item in plan["conflicts"] if item["source"] == "SRC/TOP")
        self.assertEqual(conflict["existing_views"], ["layout"])

    def test_overwrite_choice_requires_exact_views_and_backup(self):
        audit = base_audit()
        audit["target"]["cells"].append(cell("DST", "TOP"))
        decisions = {
            "decisions": {
                "SRC/TOP": decision(
                    "overwrite",
                    authorized_views=["schematic", "symbol"],
                    backup_root="C:/safe/run-1",
                )
            }
        }
        plan = build_plan(audit, base_profile(), decisions)
        self.assertEqual(plan["status"], "ready")
        self.assertFalse(validate_plan(plan))
        top_action = next(item for item in plan["cell_actions"] if item["source"] == "SRC/TOP")
        self.assertTrue(all(item["operation"] == "overwrite_view" for item in top_action["views"]))

    def test_rename_choice_updates_target(self):
        audit = base_audit()
        audit["target"]["cells"].append(cell("DST", "TOP"))
        decisions = {"decisions": {"SRC/TOP": decision("rename", new_cell="TOP_MIG")}}
        plan = build_plan(audit, base_profile(), decisions)
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["cell_targets"]["SRC/TOP"], "DST/TOP_MIG")

    def test_user_rename_collision_stops(self):
        audit = base_audit()
        audit["target"]["cells"].append(cell("DST", "TOP_MIG"))
        decisions = {"decisions": {"SRC/TOP": decision("rename", new_cell="TOP_MIG")}}
        plan = build_plan(audit, base_profile(), decisions)
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any(item["type"] == "rename_target_exists" for item in plan["blockers"]))

    def test_rename_suggestions_avoid_existing_and_reserved_names(self):
        audit = base_audit()
        audit["target"]["cells"] = [cell("DST", "TOP_MIG"), cell("DST", "TOP_MIG_2")]
        suggestions = suggest_rename_candidates(audit, "DST", "TOP", count=2, reserved=["TOP_MIG_3"])
        self.assertEqual(suggestions, ["TOP_MIG_4", "TOP_MIG_5"])

    def test_reuse_requires_compatible_target_and_never_copies_it(self):
        audit = base_audit()
        reused = cell("DST", "LEAF")
        reused["process"] = "n65"
        reused["residual_references"] = []
        audit["target"]["cells"].append(reused)
        decisions = {"decisions": {"SRC/LEAF": decision("reuse", target_library="DST", target_cell="LEAF")}}
        plan = build_plan(audit, base_profile(), decisions)
        self.assertEqual(plan["status"], "ready")
        action = next(item for item in plan["cell_actions"] if item["source"] == "SRC/LEAF")
        self.assertEqual(action["action"], "reuse")
        self.assertEqual(action["views"], [])

    def test_reuse_missing_target_stops(self):
        audit = base_audit()
        decisions = {"decisions": {"SRC/LEAF": decision("reuse", target_library="DST", target_cell="MISSING")}}
        plan = build_plan(audit, base_profile(), decisions)
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any(item["type"] == "reuse_target_missing" for item in plan["blockers"]))

    def test_same_target_from_two_source_libraries_stops(self):
        audit = base_audit()
        audit["source"]["project_libraries"].append("SRC2")
        audit["library_map"]["SRC2"] = "DST"
        audit["source"]["cells"].append(cell("SRC2", "LEAF"))
        audit["source"]["cells"][0]["views"]["schematic"]["instances"].append(project_inst("XOTHER", "SRC2", "LEAF"))
        plan = build_plan(audit, base_profile())
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any(item["type"] == "multiple_sources_same_target" for item in plan["conflicts"]))

    def test_two_sources_can_explicitly_reuse_one_compatible_target(self):
        audit = base_audit()
        audit["source"]["project_libraries"].append("SRC2")
        audit["library_map"]["SRC2"] = "DST"
        audit["source"]["cells"].append(cell("SRC2", "LEAF"))
        audit["source"]["cells"][0]["views"]["schematic"]["instances"].append(project_inst("XOTHER", "SRC2", "LEAF"))
        reused = cell("DST", "LEAF")
        reused.update({"process": "n65", "residual_references": []})
        audit["target"]["cells"].append(reused)
        decisions = {
            "decisions": {
                "SRC/LEAF": decision("reuse", target_library="DST", target_cell="LEAF"),
                "SRC2/LEAF": decision("reuse", target_library="DST", target_cell="LEAF"),
            }
        }
        plan = build_plan(audit, base_profile(), decisions)
        self.assertEqual(plan["status"], "ready")
        self.assertFalse(validate_plan(plan))


class ReferenceAndViewTests(unittest.TestCase):
    def test_parent_reference_updates_when_leaf_renamed(self):
        audit = base_audit()
        decisions = {"decisions": {"SRC/LEAF": decision("rename", new_cell="LEAF_MIG")}}
        plan = build_plan(audit, base_profile(), decisions)
        action = next(item for item in plan["reference_actions"] if item["instance"] == "XLEAF")
        self.assertEqual(action["target_master"], "DST/LEAF_MIG")

    def test_project_references_close_over_h14_to_h18_library_map(self):
        audit = base_audit()
        audit["source"]["project_libraries"] = ["PLL_H14", "CMN_H14"]
        audit["source"]["top"] = {"library": "PLL_H14", "cell": "SPD"}
        audit["source"]["cells"] = [
            cell("PLL_H14", "SPD", [project_inst("XCDAC", "PLL_H14", "CDAC"), project_inst("XAMUX", "CMN_H14", "AMUX")]),
            cell("PLL_H14", "CDAC"),
            cell("CMN_H14", "AMUX"),
        ]
        audit["library_map"] = {"PLL_H14": "PLL_H18", "CMN_H14": "CMN_H18"}
        plan = build_plan(audit, base_profile())
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(
            {item["target_master"] for item in plan["reference_actions"]},
            {"PLL_H18/CDAC", "CMN_H18/AMUX"},
        )

    def test_only_exact_allowed_views_are_planned(self):
        audit = base_audit()
        audit["source"]["cells"][0]["views"].update(
            {
                "layout": {"fingerprint": "layout"},
                "av_extracted": {"fingerprint": "pex"},
                "systemVerilog": {"fingerprint": "sv"},
                "veriloga": {"fingerprint": "va"},
            }
        )
        plan = build_plan(audit, base_profile())
        action = next(item for item in plan["cell_actions"] if item["source"] == "SRC/TOP")
        self.assertEqual([item["view"] for item in action["views"]], list(ALLOWED_VIEWS))
        self.assertEqual(action["skipped_views"], ["av_extracted", "layout"])


class DeviceMappingTests(unittest.TestCase):
    def test_profile_cdf_contract_translates_total_and_per_finger_width(self):
        audit = base_audit()
        mos = process_inst("M0", "mos", "PDK28", "nmos28")
        mos["properties"] = [
            {"name": "w", "value": "1u", "valueType": "string"},
            {"name": "Wfg", "value": "250n", "valueType": "string"},
            {"name": "l", "value": "30n", "valueType": "string"},
            {"name": "fingers", "value": 4, "valueType": "int"},
            {"name": "m", "value": 2, "valueType": "int"},
        ]
        audit["source"]["cells"][1]["views"]["schematic"]["instances"].append(mos)
        contract = {
            "contract_id": "test-w-Wfg-to-w-wf",
            "requires_live_revalidation": True,
            "source": {
                "length_field": "l",
                "total_width_field": "w",
                "per_finger_width_field": "Wfg",
                "fingers_field": "fingers",
                "multiplier_field": "m",
            },
            "target": {
                "length_field": "l",
                "total_width_field": "wf",
                "per_finger_width_field": "w",
                "fingers_field": "fingers",
                "multiplier_field": "m",
            },
            "callback_sequence": ["l", "fingers", "m", "wf"],
        }
        audit["target"]["masters"].append(
            {
                "library": "PDK65",
                "cell": "nmos65",
                "process": "n65",
                "validated": True,
                "terminals": [{"name": name} for name in ("D", "G", "S", "B")],
                "cdf_validation": {
                    "validated": True,
                    "contract_id": contract["contract_id"],
                    "fields": ["l", "wf", "w", "fingers", "m"],
                    "callback_sequence": ["l", "fingers", "m", "wf"],
                },
            }
        )
        profile = base_profile()
        profile["target_process"]["minimum_channel_length_nm"] = 60
        profile["mos_default_sizing_policy"] = {"mode": "preserve_w_over_l_raise_length_to_target_min"}
        profile["mos_cdf_contract"] = contract
        profile["mos"].append(
            {
                "source": {"library": "PDK28", "cell": "nmos28"},
                "target": {"library": "PDK65", "cell": "nmos65"},
                "status": "verified",
                "requires_target_revalidation": True,
                "pin_map": "identity",
            }
        )
        plan = build_plan(audit, profile)
        self.assertEqual(plan["status"], "ready")
        sizing = plan["device_actions"][0]["computed_target_params"]
        self.assertEqual(sizing["target_cdf_values"]["wf"], "2u")
        self.assertEqual(sizing["target_cdf_values"]["w"], "0.5u")
        self.assertEqual(sizing["target_cdf_values"]["fingers"], 4)
        self.assertEqual(sizing["callback_sequence"], ["l", "fingers", "m", "wf"])

        audit["target"]["masters"][0]["cdf_validation"]["callback_sequence"] = ["l", "wf", "fingers", "m"]
        blocked = build_plan(audit, profile)
        self.assertEqual(blocked["status"], "blocked")
        self.assertTrue(
            any(item["type"] == "mos_target_cdf_callback_order_mismatch" for item in blocked["blockers"])
        )

    def test_default_28_to_65_sizing_preserves_w_over_l(self):
        audit = base_audit()
        mos = process_inst("M0", "mos", "PDK28", "nmos28")
        mos["properties"] = [
            {"name": "w", "value": "1u", "valueType": "string"},
            {"name": "l", "value": "30n", "valueType": "string"},
            {"name": "nf", "value": 4, "valueType": "int"},
            {"name": "m", "value": 2, "valueType": "int"},
        ]
        audit["source"]["cells"][1]["views"]["schematic"]["instances"].append(mos)
        audit["target"]["masters"].append(
            {
                "library": "PDK65",
                "cell": "nmos65",
                "process": "n65",
                "validated": True,
                "terminals": [{"name": name} for name in ("D", "G", "S", "B")],
            }
        )
        profile = base_profile()
        profile["source_process"]["minimum_channel_length_nm"] = 30
        profile["target_process"]["minimum_channel_length_nm"] = 60
        profile["mos_default_sizing_policy"] = {
            "mode": "preserve_w_over_l_raise_length_to_target_min",
            "preserve_fingers": True,
            "preserve_multiplier": True,
        }
        profile["mos"].append(
            {
                "source": {"library": "PDK28", "cell": "nmos28"},
                "target": {"library": "PDK65", "cell": "nmos65"},
                "status": "verified",
                "requires_target_revalidation": True,
                "pin_map": "identity",
                "width_semantics_compatible": True,
            }
        )
        plan = build_plan(audit, profile)
        self.assertEqual(plan["status"], "ready")
        action = plan["device_actions"][0]
        self.assertEqual(action["sizing_source"], "profile_default")
        self.assertEqual(action["computed_target_params"]["w"], "2u")
        self.assertEqual(action["computed_target_params"]["l"], "60n")
        self.assertEqual(action["computed_target_params"]["scale_factor"], "2")
        self.assertTrue(action["computed_target_params"]["preserve_fingers"])
        self.assertTrue(action["computed_target_params"]["preserve_multiplier"])

    def test_default_sizing_does_not_shrink_already_legal_device(self):
        result = default_mos_dimensions("1u", "90n", 60)
        self.assertEqual(result["w"], "1u")
        self.assertEqual(result["l"], "90n")
        self.assertEqual(result["scale_factor"], "1")

    def test_default_sizing_requires_compatible_width_semantics(self):
        audit = base_audit()
        audit["source"]["cells"][1]["views"]["schematic"]["instances"].append(
            process_inst("M0", "mos", "PDK28", "nmos28")
        )
        audit["target"]["masters"].append(
            {
                "library": "PDK65",
                "cell": "nmos65",
                "process": "n65",
                "validated": True,
                "terminals": [{"name": name} for name in ("D", "G", "S", "B")],
            }
        )
        profile = base_profile()
        profile["target_process"]["minimum_channel_length_nm"] = 60
        profile["mos_default_sizing_policy"] = {"mode": "preserve_w_over_l_raise_length_to_target_min"}
        profile["mos"].append(
            {
                "source": {"library": "PDK28", "cell": "nmos28"},
                "target": {"library": "PDK65", "cell": "nmos65"},
                "status": "verified",
                "requires_target_revalidation": True,
                "pin_map": "identity"
            }
        )
        plan = build_plan(audit, profile)
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any(item["type"] == "mos_width_semantics_not_confirmed" for item in plan["blockers"]))

    def test_mos_mapping_and_parameter_strategy(self):
        audit = base_audit()
        audit["source"]["cells"][1]["views"]["schematic"]["instances"].append(process_inst("M0", "mos", "PDK28", "nmos28"))
        audit["target"]["masters"].append(
            {
                "library": "PDK65",
                "cell": "nmos65",
                "process": "n65",
                "validated": True,
                "terminals": [{"name": name} for name in ("D", "G", "S", "B")],
            }
        )
        profile = base_profile()
        profile["mos"].append(
            {
                "source": {"library": "PDK28", "cell": "nmos28"},
                "target": {"library": "PDK65", "cell": "nmos65"},
                "status": "verified",
                "requires_target_revalidation": True,
                "pin_map": "identity",
                "parameter_strategy": {"mode": "explicit", "w": "1u", "l": "60n", "nf": 1, "m": 1},
            }
        )
        plan = build_plan(audit, profile)
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["device_actions"][0]["target_master"], "PDK65/nmos65")
        self.assertTrue(plan["device_actions"][0]["requires_cdf_readback"])

    def test_standard_cell_mapping_checks_target_pins(self):
        audit = base_audit()
        pins = {"A": "n_in", "Y": "n_out", "VDD": "vdd", "VSS": "vss"}
        audit["source"]["cells"][1]["views"]["schematic"]["instances"].append(
            process_inst("U0", "standard_cell", "STD28", "INVD3BWP30P140", pins)
        )
        audit["target"]["masters"].append(
            {
                "library": "STD65",
                "cell": "INVD3",
                "process": "n65",
                "validated": True,
                "terminals": [{"name": name} for name in pins],
            }
        )
        profile = base_profile()
        profile["standard_cells"].append(
            {
                "source": {"library": "STD28", "cell": "INVD3BWP30P140"},
                "target": {"library": "STD65", "cell": "INVD3"},
                "status": "historically_verified",
                "requires_target_revalidation": True,
                "pin_map": "identity",
            }
        )
        self.assertEqual(build_plan(audit, profile)["status"], "ready")
        audit["target"]["masters"][0]["terminals"].pop()
        bad = build_plan(audit, profile)
        self.assertEqual(bad["status"], "blocked")
        self.assertTrue(any(item["type"] == "target_pin_mismatch" for item in bad["blockers"]))

    def test_unknown_process_device_stops_safely(self):
        audit = base_audit()
        audit["source"]["cells"][1]["views"]["schematic"]["instances"].append(process_inst("M0", "mos", "PDK28", "unknown"))
        plan = build_plan(audit, base_profile())
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any(item["type"] == "unknown_or_ambiguous_device_mapping" for item in plan["blockers"]))


class VerificationTests(unittest.TestCase):
    def test_plan_uses_one_transaction_batch_per_non_reuse_cell(self):
        plan = build_plan(base_audit(), base_profile())
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(
            {item["source_cell"] for item in plan["execution_batches"]},
            {"SRC/TOP", "SRC/LEAF"},
        )
        self.assertEqual(plan["snapshot_policy"]["mode"], "phase_boundaries_only")
        self.assertFalse(validate_plan(plan))

    def test_reuse_ignores_internal_topology_and_requires_no_check_save(self):
        audit = base_audit()
        reused = cell("DST", "LEAF", [process_inst("RANDOM_INTERNAL", "external", "analogLib", "res")])
        reused.update({"process": "n65", "residual_references": []})
        audit["target"]["cells"].append(reused)
        decisions = {"decisions": {"SRC/LEAF": decision("reuse", target_library="DST", target_cell="LEAF")}}
        plan = build_plan(audit, base_profile(), decisions)
        post = {
            "process": {"id": "n65", "revision": "r2"},
            "cells": [cell("DST", "TOP", [project_inst("XLEAF", "DST", "LEAF")]), reused],
            "residual_references": [],
            "check_and_save": {"DST/TOP": "clean"},
            "cdf_readback": {},
        }
        result = verify_migration(audit, plan, post)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(any(item.get("target") == "DST/LEAF" for item in result["warnings"]))

    def test_pin_coordinate_differences_do_not_change_connectivity(self):
        audit = base_audit()
        plan = build_plan(audit, base_profile())
        self.assertEqual(plan["status"], "ready")
        post_top = cell("DST", "TOP", [project_inst("XLEAF", "DST", "LEAF")], coords=True)
        post_leaf = cell("DST", "LEAF", coords=True)
        post = {
            "process": {"id": "n65", "revision": "r2"},
            "cells": [post_top, post_leaf],
            "residual_references": [],
            "check_and_save": {"DST/TOP": "clean", "DST/LEAF": "clean"},
            "cdf_readback": {},
        }
        result = verify_migration(audit, plan, post)
        self.assertEqual(result["status"], "passed")

    def test_excluded_layout_or_pex_in_post_fails(self):
        audit = base_audit()
        plan = build_plan(audit, base_profile())
        post_top = cell("DST", "TOP", [project_inst("XLEAF", "DST", "LEAF")], extra_views={"layout": {}})
        post = {
            "cells": [post_top, cell("DST", "LEAF")],
            "residual_references": [],
            "check_and_save": {"DST/TOP": "clean", "DST/LEAF": "clean"},
            "cdf_readback": {},
        }
        result = verify_migration(audit, plan, post)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any(item["type"] == "excluded_views_present" for item in result["errors"]))


class CliTests(unittest.TestCase):
    def test_skill_source_lint_blocks_common_load_failures(self):
        self.assertEqual(lint_text('(procedure (x) (printf "ok"))')["status"], "passed")
        failed = lint_text('(procedure (x) (printf "broken")')
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(any(item["type"] == "unclosed_delimiter" for item in failed["errors"]))

    def test_plan_and_validate_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit_path = root / "audit.json"
            profile_path = root / "profile.json"
            plan_path = root / "plan.json"
            audit_path.write_text(json.dumps(base_audit()), encoding="utf-8")
            profile_path.write_text(json.dumps(base_profile()), encoding="utf-8")
            planner = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "plan_migration.py"),
                    "--audit",
                    str(audit_path),
                    "--profile",
                    str(profile_path),
                    "--output",
                    str(plan_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(planner.returncode, 0, planner.stderr)
            validator = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_plan.py"), str(plan_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(validator.returncode, 0, validator.stdout + validator.stderr)


if __name__ == "__main__":
    unittest.main()

