import copy
import unittest

from compressor_sizing import FIELDS, sample_case, size_stage
from diffuser_analysis import analyze, march
from thermo import air


class DiffuserAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cycle = sample_case()
        cls.assumptions = {key: value for key, _, value, _, _ in FIELDS}

    def test_inviscid_limit_conserves_total_pressure_swirl_and_mass(self):
        sizing = size_stage(self.cycle, self.assumptions)
        rows = march(air(), sizing, 0, 8)
        inlet = rows[0]
        self.assertAlmostEqual(inlet["radial_velocity_m_s"], self.assumptions["exit_meridional_m_s"], places=7)
        for row in rows:
            self.assertAlmostEqual(row["total_pressure_pa"], inlet["total_pressure_pa"])
            self.assertAlmostEqual(row["radius_m"]*row["swirl_velocity_m_s"],
                                   inlet["radius_m"]*inlet["swirl_velocity_m_s"])
            self.assertLess(abs(row["mass_residual_kg_s"]), 1e-8)

    def test_friction_and_fixed_cycle_conditions(self):
        original = copy.deepcopy(self.cycle)
        result = analyze(self.cycle, self.assumptions)
        self.assertEqual(self.cycle, original)
        rows = result["states"]
        for before, after in zip(rows, rows[1:]):
            self.assertLess(after["total_pressure_pa"], before["total_pressure_pa"])
            self.assertLess(after["radius_m"]*after["swirl_velocity_m_s"],
                            before["radius_m"]*before["swirl_velocity_m_s"])
        self.assertGreater(result["pressure_recovery_coefficient"], 0)
        self.assertLess(result["grid_pressure_loss_relative_pct"], .05)
        comparison = result["cycle_comparison"]
        updated = comparison["updated_case"]
        key = "compressor_pressure_ratio"
        self.assertEqual(updated["input"]["parameters"][key], original["input"]["parameters"][key])
        self.assertEqual(updated["result"]["metrics"]["air_mass_flow_kg_s"], 1.2)
        settings = updated["input"]["elements"]["compressor"]["stages"][0]["diffuser"]
        self.assertEqual(settings["model"], "loss")
        self.assertEqual(settings["coefficient"], result["loss_coefficient"])
        self.assertNotEqual(comparison["original_net_output_w"], comparison["updated_net_output_w"])

    def test_bad_friction_coefficients(self):
        for invalid in (-.01, float("inf"), True, .121):
            with self.assertRaises(ValueError):
                analyze(self.cycle, self.assumptions, invalid)

    def test_fixed_geometry_closes_work_and_both_diffuser_pressures(self):
        report = analyze(self.cycle, self.assumptions)
        fixed = report["fixed_geometry"]
        comparison = fixed["cycle_comparison"]
        ratio = report["states"][-1]["total_pressure_pa"]/report["sizing"]["cycle_targets"]["inlet"]["total_pressure_pa"]
        self.assertAlmostEqual(fixed["achieved_pressure_ratio"], ratio)
        self.assertAlmostEqual(comparison["updated_pressure_ratio"], ratio, places=8)
        self.assertAlmostEqual(comparison["updated_pressure_loss_pa"], report["pressure_loss_pa"], places=4)
        self.assertAlmostEqual(comparison["updated_compressor_work_j_kg"],
                               comparison["original_compressor_work_j_kg"], places=4)
        self.assertTrue(all(abs(value) < 1e-5 for value in fixed["residuals"].values()))

    def test_fixed_geometry_exposes_shortfall_as_friction_increases(self):
        fixed = analyze(self.cycle, self.assumptions, .035)["fixed_geometry"]
        self.assertLess(fixed["pressure_ratio_delta"], 0)
        comparison = fixed["cycle_comparison"]
        self.assertLess(comparison["updated_net_output_w"], comparison["original_net_output_w"])
