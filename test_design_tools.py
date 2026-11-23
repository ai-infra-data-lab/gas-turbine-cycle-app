import json
from copy import deepcopy
from math import pi
from pathlib import Path
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch

from engine import run_case
from two_stage_design import sample_cycle_input


class DesignToolChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The app's legacy tests run from its directory. The exchange CLI is a package.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        cls.examples = Path(__file__).with_name("examples") / "roadmap"
        cls.cycle = run_case(sample_cycle_input())

    def setup_for(self, name):
        return json.loads((self.examples / f"{name}.json").read_text(encoding="utf-8"))

    def test_isolated_result_ignores_solver_stdout_diagnostics(self):
        from gas_turbine_cycle_app import design_tools
        expected = {"format": "rotor-cycle-handoff", "summary": "計算結果"}

        def finish(command, **_kwargs):
            output = Path(command[command.index("--output") + 1])
            output.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
            return CompletedProcess(command, 0, stdout="Native dependency diagnostic", stderr="")

        with (
            patch.object(design_tools, "python_for", return_value="python"),
            patch.object(design_tools.subprocess, "run", side_effect=finish),
        ):
            self.assertEqual(design_tools.run_isolated("rotor", {"settings": {}}), expected)

    def test_stage_thrust_and_cut_loads(self):
        from gas_turbine_cycle_app.rotor_thrust import evaluate
        result = evaluate(self.cycle, self.setup_for("thrust"))
        self.assertEqual(len(result["stages"]), 2)
        self.assertTrue(all(row["outlet_static_pa"] > row["inlet_static_pa"] for row in result["stages"]))
        self.assertAlmostEqual(result["axial_force_residual_n"], 0.)
        self.assertAlmostEqual(result["joints"][0]["tension_n"], -result["stages"][0]["force_n"])
        for joint in result["joints"]:
            self.assertEqual(joint["strength_case"]["input"]["load"]["axial_force_n"], joint["tension_n"])

    def test_thrust_sensitivity_uses_recalculated_pressure_and_preserves_inputs(self):
        from gas_turbine_cycle_app.thrust_sensitivity import evaluate
        setup = self.setup_for("thrust")
        for side in ("back", "front"):
            setup["stages"][0]["cavities"][0][side]["swirl_ratio"] = 0
        settings = {
            "thrust": setup, "stage1_shares": [.5, .75],
            "sweeps": [
                {"stage": 1, "cavity": 1, "side": "back",
                 "parameter": "outer_pressure_pa", "offsets": [0, 10000]},
            ],
        }
        original = deepcopy(settings)
        equal, front_loaded = evaluate(self.cycle, settings)["cases"]
        self.assertEqual(settings, original)
        self.assertNotEqual(equal["source_cycle"]["input_hash"], front_loaded["source_cycle"]["input_hash"])
        self.assertGreater(front_loaded["baseline"]["stages"][0]["static_rise_pa"],
                           equal["baseline"]["stages"][0]["static_rise_pa"])
        for variant in (equal, front_loaded):
            baseline = variant["baseline"]
            unchanged, raised = variant["points"]
            self.assertEqual(unchanged["stages"], baseline["stages"])
            self.assertEqual(unchanged["bearing_delta_n"], 0.)
            force_change = raised["stages"][0]["force_n"] - baseline["stages"][0]["force_n"]
            self.assertAlmostEqual(force_change, 10000 * pi * (.09**2 - .027**2))
            self.assertEqual(raised["stages"][1], baseline["stages"][1])
            self.assertAlmostEqual(raised["bearing_delta_n"], -force_change)
            self.assertAlmostEqual(raised["joints"][0]["tension_n"] - baseline["joints"][0]["tension_n"],
                                   -force_change)
            self.assertAlmostEqual(raised["axial_force_residual_n"], 0.)

    def test_thrust_sensitivity_reports_invalid_trial_condition(self):
        from gas_turbine_cycle_app.thrust_sensitivity import evaluate
        settings = self.setup_for("thrust-sensitivity")
        settings["thrust"] = self.setup_for("thrust")
        settings["sweeps"] = [
            {"stage": 1, "cavity": 1, "side": "front", "parameter": "swirl_ratio", "offsets": [.5]},
        ]
        with self.assertRaisesRegex(ValueError, "第1段配分50.0%.*swirl_ratio"):
            evaluate(self.cycle, settings)

    def test_three_stage_cooling_reuses_updated_static_boundaries(self):
        from gas_turbine_cycle_app.design_tools import run_isolated

        def settings(name):
            return json.loads((self.examples / f"{name}.json").read_bytes())

        request = {"demo_cycle": True, "settings": settings("turbine")}
        turbine = run_isolated("turbine", request)
        for candidate in turbine["cases"].values():
            stations = candidate["stations"]
            inlet = stations["TB-EXD-IN"]["flow"]
            outlet = stations["TB-EXD-OUT"]["flow"]
            inputs = request["settings"]["exhaust_diffuser"]
            self.assertAlmostEqual(outlet["area"] / inlet["area"], inputs["area_ratio"])
            self.assertAlmostEqual(outlet["total_pressure_pa"] / inlet["total_pressure_pa"],
                                   inputs["pressure_retention"])
            self.assertEqual(outlet["total_temperature_k"], inlet["total_temperature_k"])
            self.assertTrue(all(stations[f"TB-0{stage}-NGV-{edge}"]["flow"]
                                for stage in (1, 2, 3) for edge in ("LE", "TE")))
        setup = settings("cooling-coupled")
        result = run_isolated("cooling", {"demo_cycle": True, "settings": dict(setup, turbine=turbine)})
        self.assertEqual(result["cycle_feedback"], "coupled_design_point")
        self.assertEqual(len(result["injections"]), 8)
        self.assertEqual(len({row["station"] for row in result["injections"]}), 8)
        trace = result["coupling"]["iterations"]
        self.assertGreater(len(trace), 1)
        self.assertLessEqual(trace[-1]["max_pressure_difference_pa"], 2)
        self.assertLessEqual(trace[-1]["max_temperature_difference_k"], .01)
        self.assertLessEqual(trace[-1]["max_flow_change_kg_s"], 1e-7)
        for step in trace:
            boundaries = step["boundaries"]
            self.assertEqual(len(boundaries), len(result["injections"]) + 1)
            self.assertEqual(len({row["node_id"] for row in boundaries}), len(boundaries))
            self.assertEqual(step["max_pressure_difference_pa"],
                             max(abs(row["pressure_difference_pa"]) for row in boundaries))
            self.assertEqual(step["max_temperature_difference_k"],
                             max(abs(row["temperature_difference_k"]) for row in boundaries))
        self.assertLess(abs(trace[-1]["cycle_energy_balance"]["mass_balance_residual_kg_s"]), 1e-8)

    def test_seal_clearance_changes_cavity_and_axial_load(self):
        from gas_turbine_cycle_app.design_tools import run_isolated

        setup = json.loads((self.examples / "thrust-seal.json").read_bytes())
        setup["thrust"] = json.loads((self.examples / "thrust.json").read_bytes())
        result = run_isolated("thrust-seal", {"demo_cycle": True, "settings": setup})
        base, larger = result["cases"].values()
        def back_pressure(case):
            return next(row["outer_pressure_pa"] for row in case["cavity_inputs"]
                        if row["stage"] == 1 and row["side"] == "back")

        self.assertGreater(back_pressure(larger), back_pressure(base))
        for case in (base, larger):
            thrust = case["thrust"]
            self.assertAlmostEqual(thrust["axial_force_residual_n"], 0.)
            self.assertEqual(len(thrust["joints"]), 2)
        self.assertNotEqual(larger["thrust"]["bearing_reaction"]["force_n"], base["thrust"]["bearing_reaction"]["force_n"])

    def test_single_can_temperature_fuel_round_trip(self):
        from gas_turbine_cycle_app.design_tools import calculate
        setup = self.setup_for("combustor")
        result = calculate("combustor", {"cycle": self.cycle, "settings": setup})
        can = result["per_can"]
        setup.pop("target_total_temperature_k")
        setup["fuel_per_can_kg_s"] = can["fuel_kg_s"]
        fuel_controlled = calculate("combustor", {"cycle": self.cycle, "settings": setup})["per_can"]
        self.assertAlmostEqual(sum(can["zones_kg_s"].values()), can["air_kg_s"])
        self.assertAlmostEqual(fuel_controlled["outlet_total_temperature_k"], can["outlet_total_temperature_k"])
        self.assertAlmostEqual(result["cycle_case"]["result"]["stations"][3]["total_pressure_pa"],
                               can["outlet_total_pressure_pa"], places=6)
        self.assertAlmostEqual(can["enthalpy_balance_residual_w"], 0., places=7)
