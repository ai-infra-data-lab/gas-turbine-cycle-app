import copy
import json
import math
import unittest
from pathlib import Path

from engine import analyze, run_case, sweep
from model import InputError, default_input, input_hash, validate_input
from server import read_input_file
from thermo import (DATA, RU, AIR_MOLES_PER_KG, ATOMIC_C, ATOMIC_H, air, products, hydrogen_fraction,
                    species_properties, stoichiometric_fuel_air_ratio, T_REF_K)


class NasaTests(unittest.TestCase):
    def test_dataset_identity_ranges_and_known_coefficients(self):
        self.assertEqual(DATA["source_commit"], "985fd33368b2334c4162a586c499e0ab41d12bd3")
        self.assertEqual(DATA["source_file_sha256"], "fa7746572952d74e249e818a82a35c113829742fb421a308e167185528884363")
        self.assertEqual(RU, 8314.51)
        self.assertEqual(DATA["species"]["N2"]["ranges"][0]["coefficients"][0], 22103.71497)
        self.assertEqual(DATA["species"]["H2O"]["molecular_weight_kg_kmol"], 18.01528)
        for item in DATA["species"].values():
            self.assertEqual(len(item["ranges"]), 2)
            self.assertEqual(item["ranges"][0]["t_min_k"], 200)
            self.assertEqual(item["ranges"][-1]["t_max_k"], 6000)

    def test_polynomial_thermodynamic_derivatives(self):
        for name in DATA["species"]:
            for t in (250, 298.15, 700, 1200, 2000, 5000):
                cp, _, _ = species_properties(name, t)
                eps = .01
                dh = (species_properties(name, t+eps)[1] - species_properties(name, t-eps)[1])/(2*eps)
                ds = (species_properties(name, t+eps)[2] - species_properties(name, t-eps)[2])/(2*eps)
                self.assertAlmostEqual(dh/cp, 1, places=7)
                self.assertAlmostEqual(ds/(cp/t), 1, places=7)

    def test_temperature_boundary_continuity_and_no_extrapolation(self):
        for name in DATA["species"]:
            a, b = species_properties(name, 999.999999), species_properties(name, 1000.000001)
            for x, y in zip(a, b):
                self.assertLess(abs(x-y)/max(abs(x), 1), 2e-6)
        for t in (199.9, 6000.1, float("nan"), True):
            with self.assertRaises(InputError):
                species_properties("N2", t)

    def test_temperature_dependent_cp_gamma_and_reference_enthalpy(self):
        a = air()
        self.assertGreater(a.cp(1500), a.cp(300))
        self.assertLess(a.gamma(1500), a.gamma(300))
        self.assertAlmostEqual(a.h(T_REF_K), 0)
        self.assertAlmostEqual(a.entropy_thermal(T_REF_K), 0)
        self.assertAlmostEqual(a.gamma(300), a.cp(300)/(a.cp(300)-a.r))

    def test_element_conservation_for_all_fuel_options(self):
        for fuel in ("methane", "gasturb_generic", "gasturb_natural_gas", "custom_ch"):
            settings = {"model": "nasa9_frozen", "fuel": fuel, "fuel_hydrogen_mass_fraction": .12}
            f = .02
            g = products(f, settings)
            mol = {k: y*(1+f)/DATA["species"][k]["molecular_weight_kg_kmol"] for k, y in g.mass_fractions.items()}
            self.assertAlmostEqual(math.fsum(g.mass_fractions.values()), 1)
            self.assertAlmostEqual(mol["CO2"] - AIR_MOLES_PER_KG["CO2"], f*(1-hydrogen_fraction(settings))/ATOMIC_C)
            self.assertAlmostEqual(2*mol["H2O"], f*hydrogen_fraction(settings)/ATOMIC_H)
            self.assertAlmostEqual(2*mol["O2"]+2*mol["CO2"]+mol["H2O"], 2*(AIR_MOLES_PER_KG["O2"]+AIR_MOLES_PER_KG["CO2"]))
            self.assertAlmostEqual(mol["N2"], AIR_MOLES_PER_KG["N2"])
            self.assertAlmostEqual(mol["Ar"], AIR_MOLES_PER_KG["Ar"])

    def test_stoichiometric_boundary_and_rich_rejection(self):
        settings = default_input()["thermodynamics"]
        limit = stoichiometric_fuel_air_ratio(settings)
        self.assertGreater(limit, .05)
        self.assertLess(limit, .07)
        self.assertLess(products(limit, settings).mass_fractions.get("O2", 0), 1e-12)
        with self.assertRaises(InputError):
            products(limit*1.001, settings)
        inp = default_input()
        inp["parameters"].update(turbine_inlet_temperature_k=2000, fuel_lhv_j_kg=1e7)
        with self.assertRaises(InputError):
            analyze(inp)

    def test_entropy_solution_and_efficiency_enthalpy_definitions(self):
        inp = default_input()
        r = analyze(inp)
        m, s = r["metrics"], r["stations"]
        a, g = air(), products(m["fuel_air_ratio"], inp["thermodynamics"])
        self.assertAlmostEqual(a.entropy_thermal(m["compressor_isentropic_exit_temperature_k"])-a.entropy_thermal(s[1]["total_temperature_k"]), a.r*math.log(s[2]["total_pressure_pa"]/s[1]["total_pressure_pa"]), places=7)
        self.assertAlmostEqual(g.entropy_thermal(m["turbine_isentropic_exit_temperature_k"])-g.entropy_thermal(s[3]["total_temperature_k"]), g.r*math.log(s[4]["total_pressure_pa"]/s[3]["total_pressure_pa"]), places=7)
        self.assertAlmostEqual((a.h(m["compressor_isentropic_exit_temperature_k"])-a.h(s[1]["total_temperature_k"]))/(a.h(s[2]["total_temperature_k"])-a.h(s[1]["total_temperature_k"])), .78, places=10)
        self.assertAlmostEqual((g.h(s[3]["total_temperature_k"])-g.h(s[4]["total_temperature_k"]))/(g.h(s[3]["total_temperature_k"])-g.h(m["turbine_isentropic_exit_temperature_k"])), .85, places=10)

    def test_energy_and_mass_balance_for_nasa_fuels_and_conditions(self):
        for fuel in ("methane", "gasturb_generic", "gasturb_natural_gas"):
            for ratio in (2, 4, 10):
                inp = default_input()
                inp["thermodynamics"]["fuel"] = fuel
                inp["parameters"].update(compressor_pressure_ratio=ratio, turbine_inlet_temperature_k=1400)
                r = analyze(inp)
                b = r["energy_balance"]
                self.assertLess(abs(b["shaft_balance_residual_w"]), 2e-6)
                self.assertLess(abs(b["electric_balance_residual_w"]), 2e-6)
                self.assertLess(abs(b["mass_balance_residual_kg_s"]), 1e-12)
                self.assertLess(r["thermodynamics"]["equivalence_ratio"], 1)

    def test_target_and_sweep_with_nasa_properties(self):
        inp = default_input()
        inp["mode"] = "target_power"
        r = analyze(inp)
        self.assertAlmostEqual(r["metrics"]["net_electric_power_w"], 100000, places=6)
        points = sweep(inp, 1.01, 15, 7)
        self.assertEqual(len(points["points"]), 7)
        self.assertEqual(points["points"][0]["status"], "invalid")
        self.assertAlmostEqual(points["fixed_air_mass_flow_kg_s"], r["metrics"]["air_mass_flow_kg_s"])

    def test_nasa_does_not_use_constant_property_inputs(self):
        inp = default_input()
        baseline = analyze(inp)
        inp["parameters"].update(cp_air_j_kg_k=1200, cp_gas_j_kg_k=1500, r_air_j_kg_k=350, r_gas_j_kg_k=350)
        self.assertEqual(baseline["metrics"], analyze(inp)["metrics"])

    def test_legacy_schema_keeps_constant_model_and_old_result(self):
        path = Path(__file__).parent / "examples/flow_input.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(legacy["schema_version"], 1)
        migrated = validate_input(legacy)
        self.assertEqual(migrated["schema_version"], 6)
        self.assertEqual(migrated["thermodynamics"]["model"], "constant_cp")
        self.assertAlmostEqual(analyze(migrated)["metrics"]["net_electric_power_w"], 138723.25030905195, places=6)
        case = json.loads((path.parent / "flow_result.json").read_text(encoding="utf-8"))
        self.assertEqual(read_input_file(case)["thermodynamics"]["model"], "constant_cp")

    def test_model_fuel_and_dataset_are_in_result_and_input_hash(self):
        inp = default_input()
        case = run_case(inp)
        self.assertEqual(case["schema_version"], 6)
        self.assertEqual(case["result"]["thermodynamics"]["dataset"]["source_commit"], DATA["source_commit"])
        before = input_hash(inp)
        inp["thermodynamics"]["fuel"] = "gasturb_generic"
        self.assertNotEqual(before, input_hash(inp))
        inp["thermodynamics"]["model"] = "constant_cp"
        self.assertNotEqual(before, input_hash(inp))

    def test_invalid_thermo_settings_and_future_schema(self):
        for settings in (None, {}, {"model": "bad", "fuel": "methane", "fuel_hydrogen_mass_fraction": .2},
                         {"model": "nasa9_frozen", "fuel": "custom_ch", "fuel_hydrogen_mass_fraction": True}):
            inp = default_input()
            inp["thermodynamics"] = settings
            with self.assertRaises(InputError):
                analyze(inp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
