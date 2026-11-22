import copy
import math
import unittest

from engine import run_case
from two_stage_design import EDGE_FIELDS, FIELDS, design, sample_cycle_input
from tools import vaned_two_stage


class TwoStageDesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = run_case(sample_cycle_input())
        assumptions = {key: value for key, _, value, _, _ in FIELDS}
        edges = {key: value for key, _, value, _, _ in EDGE_FIELDS}
        cls.stages = [assumptions, dict(assumptions)]
        cls.edges = [{**edges, "le_type":"elliptical", "te_type":"circular"},
                     {**edges, "le_type":"circular", "te_type":"cutoff"}]

    def test_selected_minimum_matches_candidate_records(self):
        baseline = copy.deepcopy(self.base)
        report = design(self.base, self.stages, self.edges)
        self.assertEqual(self.base, baseline)
        self.assertEqual(len(report["candidates"]), 27)
        candidates = [p for p in report["candidates"] if p["feasible"]]
        self.assertEqual(report["selected"]["work_j_kg"], min(p["work_j_kg"] for p in candidates))
        target = baseline["input"]["parameters"]["compressor_pressure_ratio"]
        for point in candidates:
            self.assertAlmostEqual(math.prod(point["pressure_ratios"]), target, places=8)
        self.assertEqual(report["stages"][1]["blade_edges"], self.edges[1])

    def test_manual_split_and_return_endpoints(self):
        report = design(self.base, self.stages, self.edges, .45)
        self.assertAlmostEqual(report["selected"]["stage1_share"], .45)
        first, second = [row["dimensions"] for row in report["stages"]]
        duct = report["return_channel"]
        for side in ("hub","shroud"):
            self.assertAlmostEqual(duct[f"{side}_zr_m"][0][1], first["diffuser_exit_diameter_m"]/2)
            self.assertAlmostEqual(duct[f"{side}_zr_m"][-1][0], duct["stage2_axial_offset_m"])
        self.assertAlmostEqual(duct["hub_zr_m"][-1][1], second["inlet_hub_diameter_m"]/2)
        self.assertAlmostEqual(duct["shroud_zr_m"][-1][1], second["inlet_tip_diameter_m"]/2)
        self.assertGreater(duct["cycle_total_pressure_drop_pa"], 0)

    def test_invalid_edge_and_infeasible_geometry_do_not_export(self):
        with self.assertRaises(ValueError):
            design(self.base,self.stages,[{**self.edges[0],"te_type":"unknown"},self.edges[1]],.5)
        report = design(self.base,[{**self.stages[0],"tip_speed_limit_m_s":100},self.stages[1]],self.edges,.5)
        self.assertIsNone(report["selected"])
        self.assertNotIn("cycle_case",report)
        self.assertIn("周速",report["candidates"][0]["reason"])

    def test_search_excludes_reference_outside_user_bounds(self):
        report = design(self.base,self.stages,self.edges,search_min_pct=76,search_max_pct=79)
        self.assertEqual([p["stage1_share"] for p in report["candidates"]], [.5,.76,.78,.79])
        self.assertGreaterEqual(report["selected"]["stage1_share"], .76)
        with self.assertRaises(ValueError):
            design(self.base,self.stages,self.edges,search_min_pct=95,search_max_pct=95)

    def test_vaned_export_settings_are_saved_without_changing_cycle_metrics(self):
        options = vaned_two_stage.defaults()
        options["diffuser-1"]["blade_count"] = 28
        regular = design(self.base, self.stages, self.edges, .5)
        vaned = design(self.base, self.stages, self.edges, .5, vaned_options=options)
        self.assertNotIn("vaned_options", regular)
        self.assertEqual(vaned["selected"], regular["selected"])
        self.assertEqual(vaned["cycle_case"]["input_hash"], regular["cycle_case"]["input_hash"])
        self.assertEqual(vaned["vaned_options"], options)

    def test_vaned_options_reject_noninteger_count_and_reversed_edges(self):
        options = vaned_two_stage.defaults()
        options["diffuser-1"]["blade_count"] = 24.5
        with self.assertRaisesRegex(ValueError, "翼枚数は整数"):
            design(self.base, self.stages, self.edges, vaned_options=options)
        options = vaned_two_stage.defaults()
        options["axial-guide-vane"]["le_pct"] = 90
        with self.assertRaisesRegex(ValueError, "前縁位置 < 後縁位置"):
            design(self.base, self.stages, self.edges, vaned_options=options)
