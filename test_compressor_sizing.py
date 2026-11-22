import math
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from compressor_sizing import FIELDS, sample_case, size_stage
from engine import run_case
from model import default_input
from server import TOKEN, make_server


class CompressorSizingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = sample_case()
        cls.settings = {key: value for key, _, value, _, _ in FIELDS}

    def test_euler_continuity_and_traceability(self):
        d = size_stage(self.case, self.settings)
        targets = d["cycle_targets"]
        geometry = d["dimensions"]
        speed = d["checks"]["tip_speed_m_s"]
        self.assertAlmostEqual(speed*d["checks"]["impeller_exit_swirl_m_s"], targets["impeller_work_j_kg"])
        for location, radial in (("impeller", "exit_meridional_m_s"), ("diffuser", "diffuser_exit_radial_m_s")):
            rho = d["sizing_states"][location+"_exit"]["density_kg_m3"]
            area = math.pi*geometry[location+"_exit_diameter_m"]*geometry[location+"_exit_width_m"]
            self.assertAlmostEqual(area*rho*self.settings[radial]*self.settings["open_area_fraction"],
                                   targets["mass_flow_kg_s"])
        self.assertEqual(d["source"]["input_hash"], self.case["input_hash"])
        self.assertNotEqual(targets["impeller_exit_speed_m_s"], d["sizing_states"]["impeller_exit"]["velocity_m_s"])

    def test_walls_join_both_sides_at_sized_width(self):
        d = size_stage(self.case, self.settings)
        for side in ("hub", "shroud"):
            rotor = d["walls"]["impeller"][side]
            diffuser = d["walls"]["diffuser"][side]
            line, arc, radial = rotor["segments"]
            self.assertAlmostEqual(rotor["start_z_m"]+line["length_m"]+arc["radius_m"], diffuser["start_z_m"])
            self.assertAlmostEqual(rotor["start_r_m"]+arc["radius_m"]+radial["length_m"], diffuser["start_r_m"])
        diffuser = d["walls"]["diffuser"]
        self.assertAlmostEqual(diffuser["hub"]["start_z_m"]-diffuser["shroud"]["start_z_m"],
                               d["dimensions"]["impeller_exit_width_m"])

    def test_rejects_invalid_or_incompatible_designs(self):
        for override in ({"tip_speed_limit_m_s": 300}, {"hub_ratio": True}, {"diffuser_radius_ratio": float("nan")}):
            with self.assertRaises(ValueError):
                size_stage(self.case, {**self.settings, **override})
        with self.assertRaises(ValueError):
            size_stage(run_case(default_input()), self.settings)

    def test_saved_cycle_is_the_source_used_by_the_screen_endpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            with make_server(Path(folder)/"cases.sqlite3") as server:
                server.store.save(self.case)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    with urlopen(base+"/compressor-sizing", timeout=10) as response:
                        self.assertIn(b"/compressor-sizing.js", response.read())
                    request = Request(base+"/api/sizing/calculate", headers={
                        "Content-Type": "application/json", "X-Cycle-Token": TOKEN,
                    }, data=json.dumps({"case_id": self.case["run_id"],
                                        "assumptions": self.settings}).encode())
                    with urlopen(request, timeout=10) as response:
                        result = json.load(response)
                    self.assertEqual(result["source"]["run_id"], self.case["run_id"])
                    self.assertEqual(result["dimensions"], size_stage(self.case, self.settings)["dimensions"])
                    analysis_request = Request(base+"/api/sizing/diffuser", headers={
                        "Content-Type": "application/json", "X-Cycle-Token": TOKEN,
                    }, data=json.dumps({"case_id": self.case["run_id"], "assumptions": self.settings,
                                        "darcy_friction_factor": .018}).encode())
                    with urlopen(analysis_request, timeout=20) as response:
                        prediction = json.load(response)
                    self.assertEqual(prediction["sizing"]["source"]["run_id"], self.case["run_id"])
                    self.assertEqual(len(prediction["states"]), 65)
                    self.assertIsNotNone(prediction["cycle_comparison"])
                    matched = prediction["fixed_geometry"]
                    case = matched["cycle_comparison"]["updated_case"]
                    self.assertAlmostEqual(case["input"]["parameters"]["compressor_pressure_ratio"],
                                           matched["achieved_pressure_ratio"])
                    self.assertEqual(len(server.store.list()), 1)
                finally:
                    server.shutdown()
                    thread.join()
