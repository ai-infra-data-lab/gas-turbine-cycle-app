import math
import unittest

from tools.station_geometry import gross_area
from tools.station_review import assemble, read_pressure_csv


class StationReviewTests(unittest.TestCase):
    def setUp(self):
        self.stations = {
            "CP-AGV-LE": {"area_m2": .004, "model_total_pressure_pa": None},
            "CP-AGV-TE": {"area_m2": .003, "model_total_pressure_pa": None},
            "TB-EXD-IN": {"area_m2": .01, "model_total_pressure_pa": 105000},
            "TB-EXD-OUT": {"area_m2": .02, "model_total_pressure_pa": 103500},
        }
        self.intervals = [("案内翼", "CP-AGV-LE", "CP-AGV-TE"), ("排気ディフューザ", "TB-EXD-IN", "TB-EXD-OUT")]

    def test_revolved_line_area(self):
        self.assertAlmostEqual(gross_area(((0, 15), (0, 25))), math.pi * (25**2 - 15**2) * 1e-6)
        self.assertGreater(gross_area(((0, 15), (3, 25))), gross_area(((0, 15), (0, 25))))

    def test_csv_keeps_missing_model_loss_and_calculates_observed_loss(self):
        csv = b"station_code,total_pressure_pa\nCP-AGV-LE,140000\nCP-AGV-TE,138000\nTB-EXD-IN,106000\n"
        report = assemble(self.stations, self.intervals, {}, csv, "synthetic", "試験用の値")
        first, second = report["losses"]
        self.assertIsNone(first["model_loss_pa"])
        self.assertEqual(first["observed_loss_pa"], 2000)
        self.assertEqual(second["model_loss_pa"], 1500)
        self.assertIsNone(second["observed_loss_pa"])
        self.assertEqual(report["sources"]["pressure_csv"]["kind"], "synthetic")

    def test_csv_rejects_duplicate_unknown_and_nonfinite_values(self):
        header = "station_code,total_pressure_pa\n"
        for data in ("CP-90-TE,140000", "CP-AGV-LE,NaN", "CP-AGV-LE,140000\nCP-AGV-LE,138000"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                read_pressure_csv((header + data).encode(), self.stations)
