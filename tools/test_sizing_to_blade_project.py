"""Run with Blade Designer's Python using unittest discovery in tools/.

The Cycle Studio server and its standard-library test suite remain independent.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from sizing_to_blade_project import write_project
import vaned_two_stage


CYCLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CYCLE.parent / "centrifugal_blade_designer"))
from blade_designer.fileio.project_io import load_project
from blade_designer.geometry.meridional_segments import generate_meridional
from blade_designer.models.blade_rows import row_parameters


class TwoStageHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = json.loads((CYCLE / "examples/two_stage_design_sample.json").read_text(encoding="utf-8-sig"))

    def test_native_project_connections_and_provenance(self):
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "project.json"
            write_project(self.sample, path)
            project = load_project(path)
            metadata = json.loads(path.with_suffix(".handoff.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["project_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

        ids = ["impeller-1", "diffuser-1", "return-channel", "impeller-2", "diffuser-2"]
        self.assertEqual([row.identifier for row in project.blade_rows], ids)
        self.assertEqual([row.upstream_row for row in project.blade_rows], [None, *ids[:-1]])
        self.assertEqual(project.active_blade_row, ids[0])
        self.assertEqual(metadata["stage_total_pressure_ratios"], self.sample["selected"]["pressure_ratios"])
        self.assertEqual(metadata["allocated_cycle_input_hash"], self.sample["cycle_case"]["input_hash"])
        self.assertIn("axial_guide_vane", metadata["pending_geometry"])

        resolved = [row_parameters(project, row) for row in project.blade_rows]
        for index, source in zip((0, 3), self.sample["stages"]):
            row = resolved[index]
            edge = source["blade_edges"]
            self.assertEqual(row.geometry.vane_count, edge["blade_count"])
            self.assertEqual(row.impeller_edges.reference_le_fraction, edge["le_fraction"])
            self.assertEqual(row.impeller_edges.reference_te_fraction, edge["te_fraction"])
            for section in row.sections:
                self.assertEqual(section.edge_parameters.le_type.value, edge["le_type"])
                self.assertEqual(section.edge_parameters.te_type.value, edge["te_type"])
        duct_data = self.sample["return_channel"]
        for side in ("hub", "shroud"):
            walls = [generate_meridional(getattr(row, side)) for row in resolved]
            for upstream, downstream, junction in zip(walls, walls[1:], metadata["junctions"]):
                outlet, inlet = upstream.segments[-1], downstream.segments[0]
                self.assertLessEqual(math.dist(outlet.end.zr, inlet.start.zr), 1e-8)
                cross = outlet.end_tangent[0] * inlet.start_tangent[1] - outlet.end_tangent[1] * inlet.start_tangent[0]
                dot = sum(a * b for a, b in zip(outlet.end_tangent, inlet.start_tangent))
                self.assertAlmostEqual(math.degrees(math.atan2(cross, dot)), junction["walls"][side]["inlet_turn_deg"], places=8)
            segments = walls[2].segments
            native_duct = [segments[0].start.zr, *(segment.end.zr for segment in segments)]
            self.assertEqual(len(native_duct), len(duct_data[f"{side}_zr_m"]))
            for actual, expected in zip(native_duct, duct_data[f"{side}_zr_m"]):
                self.assertLessEqual(math.dist(actual, [1000 * value for value in expected]), 1e-8)

    def test_rejects_disconnected_stage_without_outputs(self):
        report = deepcopy(self.sample)
        report["return_channel"]["stage2_axial_offset_m"] += 0.001
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "project.json"
            with self.assertRaisesRegex(ValueError, "端点が一致しません"):
                write_project(report, path)
            self.assertEqual(list(Path(scratch).iterdir()), [])

    def test_rejects_mismatched_cycle_and_preserves_existing_companion(self):
        report = deepcopy(self.sample)
        report["selected"]["pressure_ratios"][0] *= 1.01
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "project.json"
            companion = path.with_suffix(".handoff.json")
            with self.assertRaisesRegex(ValueError, "参照元が一致しません"):
                write_project(report, path)
            self.assertFalse(companion.exists())
            companion.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                write_project(self.sample, path)
            self.assertFalse(path.exists())
            self.assertEqual(companion.read_bytes(), b"existing")

    def test_six_element_preset_reproduces_the_axial_outlet_example(self):
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "project.json"
            write_project(self.sample, path, six_elements=True)
            project = load_project(path)
            handoff = json.loads(path.with_suffix(".handoff.json").read_text("utf-8"))

        self.assertEqual([row.kind for row in project.blade_rows],
                         ["impeller", "diffuser", "diffuser", "impeller", "diffuser", "diffuser"])
        self.assertEqual([row.parameters.geometry.vane_count for row in project.blade_rows], [12, 24, 24, 12, 24, 32])
        self.assertEqual(handoff["vaned_geometry"]["options_origin"], "example_preset")
        self.assertEqual(handoff["vaned_geometry"]["return_axial_runout_mm"], 5)
        self.assertEqual(handoff["pending_geometry"], [])
        self.assertEqual(len(handoff["junctions"]), 5)
        self.assertEqual(len(handoff["row_diagnostics"]), 6)
        self.assertTrue(all(not row["flow_area_warnings"] for row in handoff["row_diagnostics"]))
        self.assertTrue(all(row["inlet_area_mm2"] > 0 and row["outlet_area_mm2"] > 0
                            for row in handoff["row_diagnostics"]))
        geometry = row_parameters(project, project.blade_rows[-1])
        for wall in (geometry.hub, geometry.shroud):
            turn, straight = generate_meridional(wall).segments
            self.assertAlmostEqual(turn.sweep_rad, -math.pi / 2)
            self.assertAlmostEqual(straight.length, 40)
        guide = handoff["row_diagnostics"][-1]
        for le, te in zip(guide["le_zr_mm"], guide["te_zr_mm"]):
            self.assertAlmostEqual(te[0] - le[0], 28)
            self.assertAlmostEqual(te[1], le[1])

    def test_embedded_options_configure_the_native_stators_and_outlet(self):
        report = deepcopy(self.sample)
        options = vaned_two_stage.defaults()
        options["diffuser-1"].update(blade_count=28, le_angle_deg=22, le_thickness_mm=.55)
        options["axial-guide-vane"].update(le_pct=20, te_pct=80)
        options["outlet"].update(hub_bend_radius_mm=20, axial_length_mm=50)
        report["vaned_options"] = options
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "project.json"
            write_project(report, path)
            project = load_project(path)
            handoff = json.loads(path.with_suffix(".handoff.json").read_text("utf-8"))
        diffuser = project.blade_rows[1].parameters
        self.assertEqual(diffuser.geometry.vane_count, 28)
        self.assertEqual(diffuser.diffuser_profile.blade_angle.points[0].value, 22)
        self.assertEqual(diffuser.diffuser_profile.thickness.points[0].value, .55)
        self.assertEqual(handoff["vaned_geometry"]["options_origin"], "cycle_report")
        self.assertEqual(handoff["vaned_geometry"]["options"], options)
        self.assertEqual(handoff["vaned_geometry"]["outlet_bend_radii_mm"]["hub"], 20)
        guide = handoff["row_diagnostics"][-1]
        self.assertAlmostEqual(guide["te_zr_mm"][0][0] - guide["le_zr_mm"][0][0], 30)


if __name__ == "__main__":
    unittest.main()
