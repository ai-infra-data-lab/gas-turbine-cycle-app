import json
from pathlib import Path
import unittest

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

    def test_stage_thrust_and_cut_loads(self):
        from gas_turbine_cycle_app.rotor_thrust import evaluate
        result = evaluate(self.cycle, self.setup_for("thrust"))
        self.assertEqual(len(result["stages"]), 2)
        self.assertTrue(all(row["outlet_static_pa"] > row["inlet_static_pa"] for row in result["stages"]))
        self.assertAlmostEqual(result["axial_force_residual_n"], 0.)
        self.assertAlmostEqual(result["joints"][0]["tension_n"], -result["stages"][0]["force_n"])
        for joint in result["joints"]:
            self.assertEqual(joint["strength_case"]["input"]["load"]["axial_force_n"], joint["tension_n"])

    def test_single_can_temperature_fuel_round_trip(self):
        from single_can_combustor import evaluate
        setup = self.setup_for("combustor")
        result = evaluate(self.cycle, setup)
        can = result["per_can"]
        setup.pop("target_total_temperature_k")
        setup["fuel_per_can_kg_s"] = can["fuel_kg_s"]
        fuel_controlled = evaluate(self.cycle, setup)["per_can"]
        self.assertAlmostEqual(sum(can["zones_kg_s"].values()), can["air_kg_s"])
        self.assertAlmostEqual(fuel_controlled["outlet_total_temperature_k"], can["outlet_total_temperature_k"])
        self.assertAlmostEqual(result["cycle_case"]["result"]["stations"][3]["total_pressure_pa"],
                               can["outlet_total_pressure_pa"], places=6)
        self.assertAlmostEqual(can["enthalpy_balance_residual_w"], 0., places=7)
