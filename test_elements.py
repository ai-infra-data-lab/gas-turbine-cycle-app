import copy
import json
import math
import unittest
from pathlib import Path

from engine import analyze, run_case, sweep
from model import InputError, default_input, element_stage, validate_input, input_hash
from elements import state, passive
from thermo import ConstantGas, air
from server import read_input_file


def enabled(model="nasa9_frozen", count=1):
    inp = default_input(model)
    for key in ("compressor", "turbine"):
        inp["staging"][key].update(count=count, distribution="pressure", weights=[1]*count)
        inp["elements"][key].update(enabled=True, stages=[element_stage(key) for _ in range(count)])
    return inp


class ElementTests(unittest.TestCase):
    def test_static_state_matches_independent_constant_cp_formula(self):
        gas = ConstantGas(1000, 250)
        s = state(gas, 400, 200000, 200)
        self.assertAlmostEqual(s["static_temperature_k"], 380)
        self.assertAlmostEqual(s["static_pressure_pa"], 200000*(380/400)**4)
        self.assertAlmostEqual(s["density_kg_m3"], s["static_pressure_pa"]/(250*380))
        self.assertAlmostEqual(s["dynamic_pressure_pa"], .5*s["density_kg_m3"]*200**2)
        # Compressible static-total relation differs from the incompressible Pt=p+q approximation.
        self.assertGreater(abs(s["total_pressure_pa"]-s["static_pressure_pa"]-s["dynamic_pressure_pa"]), 100)

    def test_cp_and_loss_coefficient_definitions_conserve_total_enthalpy(self):
        gas = air()
        start = state(gas, 500, 300000, 200)
        for mode, value in (("cp", .5), ("loss", .2)):
            row = passive(gas, start, {"model": mode, "coefficient": value, "exit_velocity_m_s": 80}, "test")
            end = row["outlet"]
            self.assertEqual(end["total_temperature_k"], start["total_temperature_k"])
            self.assertLess(end["total_pressure_pa"], start["total_pressure_pa"])
            self.assertGreater(end["static_pressure_pa"], start["static_pressure_pa"])
            self.assertGreater(row["entropy_generation_j_kg_k"], 0)
            self.assertAlmostEqual(row["pressure_recovery_coefficient"] if mode == "cp" else row["total_pressure_loss_coefficient"], value, places=11)
            self.assertAlmostEqual(gas.h(end["static_temperature_k"])+.5*80**2, gas.h(500), places=7)

    def test_zero_stationary_loss_raises_static_pressure_without_total_gain(self):
        gas = ConstantGas(1000, 250)
        start = state(gas, 400, 200000, 200)
        row = passive(gas, start, {"model": "loss", "coefficient": 0, "exit_velocity_m_s": 80}, "diffuser")
        self.assertEqual(row["total_pressure_loss_pa"], 0)
        self.assertGreater(row["pressure_recovery_coefficient"], 0)
        self.assertAlmostEqual(row["entropy_generation_j_kg_k"], 0)

    def test_inconsistent_recovery_and_nonpositive_pressure_are_rejected(self):
        gas = air()
        start = state(gas, 500, 300000, 200)
        for model, coefficient, velocity in (("cp", 2, 200), ("loss", 10, 80)):
            with self.subTest(model=model), self.assertRaises(InputError):
                passive(gas, start, {"model": model, "coefficient": coefficient, "exit_velocity_m_s": velocity}, "diffuser")
        with self.assertRaises(InputError):
            state(gas, 200, 100000, 600)

    def test_train_pressure_work_entropy_area_and_cycle_energy_closure(self):
        for model in ("nasa9_frozen", "constant_cp"):
            for count in (1, 2, 4):
                inp = enabled(model, count)
                r = analyze(inp)
                self.assertLess(abs(r["energy_balance"]["shaft_balance_residual_w"]), 2e-6)
                self.assertLess(abs(r["energy_balance"]["electric_balance_residual_w"]), 2e-6)
                for key, power in (("compressor", "compressor_power_w"), ("turbine", "turbine_gas_power_w")):
                    x = r["stage_results"][key]
                    self.assertEqual(x["method"], "coefficient_element_network")
                    self.assertLess(abs(x["outlet_pressure_residual_pa"]), 1e-5)
                    self.assertAlmostEqual(sum(p["power_w"] for p in x["elements"]), r["metrics"][power], places=6)
                    self.assertAlmostEqual(x["recovered_overall_isentropic_efficiency"], r["metrics"][key+"_effective_efficiency"], places=9)
                    entropy_sum = sum(p["entropy_generation_j_kg_k"] for p in x["elements"])
                    self.assertAlmostEqual(entropy_sum, sum(s["entropy_generation_j_kg_k"] for s in x["stages"]), places=7)
                    for row in x["elements"]:
                        self.assertGreaterEqual(row["entropy_generation_j_kg_k"], -1e-7)
                        for s in (row["inlet"], row["outlet"]):
                            self.assertAlmostEqual(s["density_kg_m3"]*s["velocity_m_s"]*s["effective_flow_area_m2"], s["mass_flow_kg_s"], places=10)
                    if key == "compressor":
                        self.assertAlmostEqual(x["overall_pressure_ratio"], inp["parameters"]["compressor_pressure_ratio"], places=10)

    def test_more_return_channel_loss_increases_power_and_reduces_output(self):
        inp = enabled()
        a = analyze(inp)
        inp["elements"]["compressor"]["stages"][0]["return_channel"]["coefficient"] = 1.5
        b = analyze(inp)
        self.assertGreater(b["metrics"]["compressor_power_w"], a["metrics"]["compressor_power_w"])
        self.assertLess(b["metrics"]["net_electric_power_w"], a["metrics"]["net_electric_power_w"])
        self.assertLess(b["metrics"]["compressor_effective_efficiency"], a["metrics"]["compressor_effective_efficiency"])

    def test_more_recovery_reduces_required_impeller_power(self):
        inp = enabled()
        a = analyze(inp)
        inp["elements"]["compressor"]["stages"][0]["diffuser"]["coefficient"] = .7
        b = analyze(inp)
        self.assertLess(b["metrics"]["compressor_power_w"], a["metrics"]["compressor_power_w"])

    def test_turbine_nozzle_loss_reduces_work_at_fixed_total_expansion(self):
        inp = enabled()
        a = analyze(inp)
        inp["elements"]["turbine"]["stages"][0]["nozzle"]["coefficient"] = .8
        b = analyze(inp)
        self.assertLess(b["metrics"]["turbine_gas_power_w"], a["metrics"]["turbine_gas_power_w"])

    def test_no_passives_single_rotor_matches_original_whole_component(self):
        base = default_input()
        base["parameters"].update(compressor_efficiency=.84, turbine_efficiency=.9)
        expected = analyze(base)
        inp = enabled()
        for key in ("compressor", "turbine"):
            for name in ("diffuser", "return_channel" if key == "compressor" else "nozzle"):
                inp["elements"][key]["stages"][0][name]["enabled"] = False
        actual = analyze(inp)
        for key in ("net_electric_power_w", "compressor_power_w", "turbine_gas_power_w"):
            self.assertAlmostEqual(actual["metrics"][key], expected["metrics"][key], places=5)

    def test_high_pressure_ratio_can_bracket_rotor_ratio_up_to_limit(self):
        inp = enabled()
        inp["parameters"].update(compressor_pressure_ratio=50, turbine_inlet_temperature_k=2000)
        stage = inp["elements"]["compressor"]["stages"][0]
        stage["rotor_exit_velocity_m_s"] = 400
        stage["diffuser"]["coefficient"] = 0
        r = analyze(inp)
        train = r["stage_results"]["compressor"]
        rotor_ratio = train["elements"][0]["rotor_pressure_ratio"]
        self.assertGreater(rotor_ratio, 60)
        self.assertLess(rotor_ratio, 100)
        self.assertLess(abs(train["outlet_pressure_residual_pa"]), 1e-4)

    def test_target_scaling_legacy_efficiencies_ignored_and_input_immutable(self):
        inp = enabled()
        before = copy.deepcopy(inp)
        a = analyze(inp)
        self.assertEqual(inp, before)
        inp["parameters"].update(compressor_efficiency=.5, turbine_efficiency=1)
        b = analyze(inp)
        self.assertEqual(a["metrics"], b["metrics"])
        inp["mode"] = "target_power"
        r = analyze(inp)
        self.assertAlmostEqual(r["metrics"]["net_electric_power_w"], 100000, places=6)
        self.assertAlmostEqual(r["stage_results"]["compressor"]["total_power_w"], r["metrics"]["compressor_power_w"], places=6)

    def test_old_version_three_is_migrated_and_elements_affect_hash(self):
        raw = json.loads((Path(__file__).parent/"examples/multistage_result.json").read_text(encoding="utf-8"))
        migrated = read_input_file(raw)
        self.assertEqual(migrated["schema_version"], 6)
        self.assertFalse(migrated["elements"]["compressor"]["enabled"])
        self.assertEqual(migrated["staging"]["compressor"]["count"], 4)
        x = enabled()
        first = input_hash(x)
        x["elements"]["compressor"]["stages"][0]["diffuser"]["coefficient"] = .65
        self.assertNotEqual(first, input_hash(x))
        self.assertEqual(read_input_file(run_case(x)), validate_input(x))

    def test_invalid_elements_count_model_values_and_supersonic_states(self):
        for path, value in (("enabled", 1), ("inlet_velocity_m_s", True), ("inlet_velocity_m_s", float("nan")), ("stages", [])):
            inp = enabled()
            inp["elements"]["compressor"][path] = value
            with self.assertRaises(InputError):
                analyze(inp)
        for change in ({"coefficient": True}, {"model": "unknown"}, {"exit_velocity_m_s": 0}):
            inp = enabled()
            inp["elements"]["compressor"]["stages"][0]["diffuser"].update(change)
            with self.assertRaises(InputError):
                analyze(inp)
        inp = enabled()
        inp["staging"]["compressor"]["count"] = 2
        inp["staging"]["compressor"]["weights"] = [1,1]
        with self.assertRaises(InputError):
            analyze(inp)
        inp = enabled()
        inp["staging"]["compressor"]["distribution"] = "work"
        with self.assertRaises(InputError):
            analyze(inp)
        inp = enabled()
        inp["elements"]["compressor"]["inlet_velocity_m_s"] = 400
        with self.assertRaises(InputError):
            analyze(inp)


if __name__ == "__main__":
    unittest.main()
