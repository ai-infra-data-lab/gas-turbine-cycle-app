"""Check thermal closure, observation ingestion, and rated operating-point guards."""
import sys
from importlib import import_module
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gas_turbine_cycle_app.thermo import ConstantGas
from gas_turbine_cycle_app.design_tools import combustor_module
from gas_turbine_cycle_app.tools.suite_support import compare_observations, mechanical_review

evaluate_thermal = import_module(f"{combustor_module()}.thermal").evaluate_thermal


class SuiteSupportTests(unittest.TestCase):
    def test_outlet_enthalpy_and_liner_heat_balance(self):
        result = evaluate_thermal({
            "exit_sectors": [
                {"id": "A", "mass_fraction": .25, "temperature_offset_k": 70.},
                {"id": "B", "mass_fraction": .75, "temperature_offset_k": -10.},
            ],
            "liner_segments": [{
                "id": "liner", "area_m2": .1, "gas_temperature_k": 1100., "coolant_temperature_k": 500.,
                "film_effectiveness": .5, "gas_htc_w_m2_k": 200., "coolant_htc_w_m2_k": 500.,
                "thickness_m": .002, "conductivity_w_m_k": 20., "metal_limit_k": 800.,
            }],
        }, ConstantGas(1005., 287.), 500., 1050., .7)
        profile = result["outlet_profile"]
        self.assertAlmostEqual(profile["enthalpy_residual_w_per_can"], 0., places=4)
        self.assertAlmostEqual(profile["mass_average_temperature_k"], 1050., places=6)
        self.assertAlmostEqual(profile["pattern_factor"], 60 / 550)
        wall = result["liner_segments"][0]
        self.assertAlmostEqual(wall["wall_balance_residual_k"], 0.)
        self.assertAlmostEqual(wall["heat_flux_w_m2"], 500 * (wall["cold_wall_temperature_k"] - 500.))
        self.assertAlmostEqual(wall["temperature_margin_k"], 800. - wall["hot_wall_temperature_k"])

    def test_csv_validates_units_duplicates_and_uncertainty(self):
        header = b"measurement_id,unit,value,uncertainty\n"
        arguments = {"kind": "synthetic", "reference": "Example"}
        targets = {"outlet": {"unit": "K", "calculated": 1175.}}
        value = compare_observations(header + b"outlet,K,1174,2\n", targets, **arguments)
        self.assertEqual(value["rows"][0]["difference"], 1.)
        self.assertTrue(value["rows"][0]["within_reported_uncertainty"])
        for body in (b"outlet,C,1174,2\n", b"outlet,K,nan,2\n",
                     b"outlet,K,1174,2\noutlet,K,1176,2\n", b"outlet,K,1174,-2\n"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                compare_observations(header + body, targets, **arguments)

    def test_mechanical_review_rejects_different_cycle_and_rpm(self):
        source = {"run_id": "cooled", "input_hash": "a" * 64, "context": None}
        reports = {
            "cycle-cooled": {"run_id": "cooled", "input_hash": "a" * 64,
                            "input": {"context": None, "parameters": {"shaft_speed_rpm": 39000}}},
            "thrust": {"source_cycle": source, "cases": {}},
            "rotor": {"source_cycle": {**source, "run_id": "different"}, "cases": {"rated": {"operating_rpm": 38000}}},
            "oil": {"conditions": {"定格": {"temperature_c": 70., "viscosity_cst": 10.,
                                       "bearings": {}, "intersection_residual_kpa": 0.}}},
        }
        with self.assertRaisesRegex(ValueError, "同じ冷却後"):
            mechanical_review(reports)
        reports["rotor"]["source_cycle"] = source
        with self.assertRaisesRegex(ValueError, "評価回転数"):
            mechanical_review(reports)
