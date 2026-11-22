import copy
import json
import math
import unittest
from pathlib import Path

from engine import run_case
from server import read_input_file
from two_stage_design import sample_cycle_input
from two_stage_performance import compare


class TwoStagePerformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        example = Path(__file__).with_name("examples") / "two_stage_performance_sample.json"
        cls.example = json.loads(example.read_text(encoding="utf-8"))
        cls.base = run_case(sample_cycle_input())

    def test_import_reproduces_stage_performance_and_preserves_source(self):
        before = copy.deepcopy(self.base)
        result = compare(self.base, self.example)
        self.assertEqual(self.base, before)
        self.assertEqual(result["source_input_hash"], self.base["input_hash"])
        self.assertEqual(result["source_run_id"], self.base["run_id"])
        self.assertEqual(result["performance"], self.example)
        hashes = set()
        for key, case in result["cycle_cases"].items():
            hashes.add(case["input_hash"])
            inp = case["input"]
            self.assertEqual(inp["context"], self.base["input"]["context"])
            self.assertEqual(inp["thermodynamics"], self.base["input"]["thermodynamics"])
            self.assertEqual(inp["parameters"]["turbine_inlet_temperature_k"],
                             self.base["input"]["parameters"]["turbine_inlet_temperature_k"])
            self.assertEqual(run_case(read_input_file(case))["result"], case["result"])
            actual = case["result"]["stage_results"]["compressor"]
            design = self.example["designs"][key]
            for stage, measured in zip(actual["stages"], design["stages"]):
                self.assertAlmostEqual(stage["pressure_ratio"], measured["pressure_ratio"], places=9)
                self.assertAlmostEqual(stage["isentropic_efficiency"], measured["total_to_total_efficiency"], places=9)
            parts = design["pressure_retentions"]
            expected = [parts["diffuser_1"], parts["return_channel"], parts["diffuser_2"] * parts["outlet_guide_vane"]]
            stators = [part for part in actual["elements"] if part["kind"] == "stationary"]
            for part, retention in zip(stators, expected, strict=True):
                self.assertAlmostEqual(part["outlet"]["total_pressure_pa"] / part["inlet"]["total_pressure_pa"], retention)
            metrics = case["result"]["metrics"]
            self.assertAlmostEqual(metrics["compressor_power_w"], actual["specific_work_j_kg"] * metrics["air_mass_flow_kg_s"])
            self.assertAlmostEqual(metrics["specific_fuel_consumption_kg_kwh"],
                                   3.6e6 * metrics["fuel_mass_flow_kg_s"] / metrics["net_electric_power_w"])
        self.assertEqual(len(hashes), 2)

    def test_stage_only_constant_cp_matches_explicit_enthalpy_balance(self):
        settings = sample_cycle_input()
        settings["thermodynamics"]["model"] = "constant_cp"
        doc = copy.deepcopy(self.example)
        doc["conditions"].update(inlet_total_temperature_k=294., inlet_total_pressure_pa=98000.,
                                 air_mass_flow_kg_s=.65, shaft_speed_rpm=40000.)
        for design in doc["designs"].values():
            design.update(source_kind="test", pressure_retentions=None)
        result = compare(run_case(settings), doc)
        cp, r = (settings["parameters"][key] for key in ("cp_air_j_kg_k", "r_air_j_kg_k"))
        for key, case in result["cycle_cases"].items():
            params = case["input"]["parameters"]
            metrics = case["result"]["metrics"]
            train = case["result"]["stage_results"]["compressor"]
            self.assertEqual(metrics["air_mass_flow_kg_s"], .65)
            self.assertEqual(params["shaft_speed_rpm"], 40000.)
            self.assertAlmostEqual(train["states"][0]["total_pressure_pa"], 98000.)
            expected = 294.
            for stage in doc["designs"][key]["stages"]:
                expected += expected * (math.pow(stage["pressure_ratio"], r/cp) - 1) / stage["total_to_total_efficiency"]
            self.assertAlmostEqual(metrics["compressor_power_w"], cp * (expected - 294.) * .65, places=6)
            self.assertTrue(all(part["total_pressure_loss_pa"] == 0 for part in train["elements"]
                                if part["kind"] == "stationary"))

    def test_incompatible_loss_and_efficiency_rejects_comparison(self):
        doc = copy.deepcopy(self.example)
        doc["designs"]["75-percent"]["stages"][0]["total_to_total_efficiency"] = 1.
        with self.assertRaisesRegex(ValueError, "75%配分案.*100%"):
            compare(self.base, doc)

    def test_rejects_ambiguous_or_nonfinite_input(self):
        for invalid in (True, float("nan"), float("inf"), 1, None):
            doc = copy.deepcopy(self.example)
            doc["designs"]["equal"]["stages"][0]["pressure_ratio"] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "全圧比"):
                compare(self.base, doc)
