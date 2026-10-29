import copy
import json
import math
import unittest
from pathlib import Path

from engine import analyze, run_case
from model import InputError, default_input, validate_input, input_hash
from server import read_input_file


def configure(inp, count=4, mode="pressure", weights=None):
    for key in ("compressor", "turbine"):
        inp["staging"][key] = {"count": count, "distribution": mode, "weights": weights or [1]*count, "mean_diameters_m": None}
    return inp


class StageTests(unittest.TestCase):
    def test_single_stage_matches_component_efficiency_and_power(self):
        for model in ("constant_cp", "nasa9_frozen"):
            r = analyze(default_input(model))
            for key, eta, power in (("compressor", .78, "compressor_power_w"), ("turbine", .85, "turbine_gas_power_w")):
                x = r["stage_results"][key]
                self.assertAlmostEqual(x["stages"][0]["isentropic_efficiency"], eta, places=9)
                self.assertAlmostEqual(x["total_power_w"], r["metrics"][power], places=7)

    def test_constant_cp_independent_analytic_pressure_allocation(self):
        inp = configure(default_input("constant_cp"), 3, weights=[1, 2, 1])
        r = analyze(inp)
        for key, start, end in (("compressor", 1, 2), ("turbine", 3, 4)):
            a, b = r["stations"][start], r["stations"][end]
            # Exact polytropic path for constant cp: T/Tin=(Tout/Tin)^cumulative_weight.
            for i, frac in enumerate((0, .25, .75, 1)):
                state = r["stage_results"][key]["states"][i]
                self.assertAlmostEqual(state["total_temperature_k"], a["total_temperature_k"]*(b["total_temperature_k"]/a["total_temperature_k"])**frac, places=8)
                self.assertAlmostEqual(state["total_pressure_pa"], a["total_pressure_pa"]*(b["total_pressure_pa"]/a["total_pressure_pa"])**frac, places=7)

    def test_nasa_grid_closure_entropy_and_local_efficiency(self):
        for mode in ("pressure", "work"):
            for count in (2, 5, 30):
                inp = configure(default_input(), count, mode, list(range(1, count+1)))
                baseline = analyze(default_input())
                r = analyze(inp)
                self.assertEqual(r["metrics"], baseline["metrics"])
                for key, start, end, power in (("compressor", 1, 2, "compressor_power_w"), ("turbine", 3, 4, "turbine_gas_power_w")):
                    x = r["stage_results"][key]
                    self.assertEqual(x["states"][0]["total_temperature_k"], r["stations"][start]["total_temperature_k"])
                    self.assertEqual(x["states"][-1]["total_pressure_pa"], r["stations"][end]["total_pressure_pa"])
                    self.assertAlmostEqual(x["sum_stage_power_w"], r["metrics"][power], places=7)
                    self.assertAlmostEqual(math.prod(s["pressure_ratio"] for s in x["stages"]), x["overall_pressure_ratio"], places=10)
                    self.assertAlmostEqual(x["recovered_overall_isentropic_efficiency"], inp["parameters"][key+"_efficiency"], places=9)
                    for row in x["stages"]:
                        self.assertGreater(row["specific_work_j_kg"], 0)
                        self.assertGreaterEqual(row["entropy_generation_j_kg_k"], -1e-8)
                        self.assertTrue(0 < row["isentropic_efficiency"] <= 1+1e-9)
                        self.assertIsNone(row["loading_coefficient"])

    def test_work_weights_and_pressure_weights_have_distinct_effects(self):
        a = analyze(configure(default_input(), 3, "pressure", [1, 2, 1]))
        b = analyze(configure(default_input(), 3, "work", [1, 2, 1]))
        for key in ("compressor", "turbine"):
            for row, target in zip(a["stage_results"][key]["stages"], (.25, .5, .25)):
                self.assertAlmostEqual(row["pressure_log_fraction"], target, places=10)
            for row, target in zip(b["stage_results"][key]["stages"], (.25, .5, .25)):
                self.assertAlmostEqual(row["work_fraction"], target, places=10)
            self.assertNotAlmostEqual(a["stage_results"][key]["stages"][0]["work_fraction"], .25, places=3)

    def test_ideal_stages_and_global_efficiency_are_one(self):
        inp = configure(default_input(), 6)
        inp["parameters"].update(compressor_efficiency=1, turbine_efficiency=1)
        r = analyze(inp)
        for x in r["stage_results"].values():
            self.assertAlmostEqual(x["polytropic_efficiency"], 1, places=9)
            for s in x["stages"]:
                self.assertAlmostEqual(s["isentropic_efficiency"], 1, places=8)
                self.assertAlmostEqual(s["entropy_generation_j_kg_k"], 0, places=7)

    def test_diameter_speed_and_loading_do_not_change_cycle(self):
        inp = configure(default_input(), 2, "work")
        inp["staging"]["compressor"]["mean_diameters_m"] = [.1, .12]
        a = analyze(inp)
        s = a["stage_results"]["compressor"]["stages"][0]
        self.assertAlmostEqual(s["blade_speed_m_s"], math.pi*.1*60000/60)
        self.assertAlmostEqual(s["loading_coefficient"], s["specific_work_j_kg"] / s["blade_speed_m_s"]**2)
        inp["parameters"]["shaft_speed_rpm"] *= 2
        b = analyze(inp)
        self.assertEqual(a["metrics"], b["metrics"])
        self.assertAlmostEqual(b["stage_results"]["compressor"]["stages"][0]["loading_coefficient"], s["loading_coefficient"]/4)
        t = a["stage_results"]["turbine"]
        self.assertAlmostEqual(sum(s["transmitted_power_w"] for s in t["stages"]), a["metrics"]["turbine_shaft_power_w"])

    def test_old_schema_versions_default_to_one_stage_and_keep_model(self):
        for file in ("flow_input.json", "nasa_methane_input.json", "nasa_methane_result.json"):
            raw = json.loads((Path(__file__).parent/"examples"/file).read_text(encoding="utf-8"))
            migrated = read_input_file(raw)
            self.assertEqual(migrated["schema_version"], 6)
            self.assertEqual(migrated["staging"]["compressor"]["count"], 1)
            expected = "constant_cp" if file == "flow_input.json" else "nasa9_frozen"
            self.assertEqual(migrated["thermodynamics"]["model"], expected)

    def test_stage_input_validation_and_version_boundary(self):
        invalid = [{"count": True}, {"count": 0}, {"count": 31}, {"count": 2.0}, {"weights": [0]},
                   {"weights": [float("nan")]}, {"weights": [True]}, {"weights": [1, 2]},
                   {"weights": ["1"]}, {"weights": [1e300]}, {"distribution": "both"},
                   {"mean_diameters_m": [.1, .2]}, {"mean_diameters_m": [True]}, {"mean_diameters_m": [0]}]
        for change in invalid:
            inp = default_input()
            inp["staging"]["compressor"].update(change)
            with self.subTest(change=change), self.assertRaises(InputError):
                validate_input(inp)
        inp = default_input()
        del inp["staging"]
        with self.assertRaises(InputError):
            validate_input(inp)
        inp = default_input()
        inp["schema_version"] = 2
        with self.assertRaises(InputError):
            validate_input(inp)

    def test_roundtrip_hash_immutability_and_target_flow(self):
        inp = configure(default_input(), 4, "work", [1, 3, 2, 1])
        before = copy.deepcopy(inp)
        case = run_case(inp)
        self.assertEqual(inp, before)
        self.assertEqual(read_input_file(case), validate_input(inp))
        h = input_hash(inp)
        inp["staging"]["turbine"]["weights"][0] = 2
        self.assertNotEqual(input_hash(inp), h)
        inp["mode"] = "target_power"
        r = analyze(inp)
        self.assertAlmostEqual(r["metrics"]["net_electric_power_w"], 100000, places=6)
        self.assertAlmostEqual(r["stage_results"]["compressor"]["sum_stage_power_w"], r["metrics"]["compressor_power_w"], places=7)


if __name__ == "__main__":
    unittest.main()
