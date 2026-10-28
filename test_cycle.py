import copy
import json
import math
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from engine import T_REF_K, analyze, run_case, sweep
from model import InputError, default_input, input_hash, validate_input
from server import TOKEN, make_server, read_input_file
from storage import CaseStore


class CycleTests(unittest.TestCase):
    def case(self, **parameters):
        raw = default_input('constant_cp')
        raw["parameters"].update(parameters)
        return raw

    def test_independent_exact_pressure_temperature_reference(self):
        raw = self.case(ambient_temperature_k=298.15, compressor_pressure_ratio=16,
                        cp_air_j_kg_k=1000, cp_gas_j_kg_k=1000, r_air_j_kg_k=250, r_gas_j_kg_k=250,
                        turbine_inlet_temperature_k=1192.6, compressor_efficiency=1, turbine_efficiency=1,
                        combustion_efficiency=1, mechanical_efficiency=1, generator_efficiency=1,
                        inlet_pressure_loss=0, combustor_pressure_loss=0, exhaust_pressure_loss=0,
                        auxiliary_power_w=0, fuel_lhv_j_kg=30e6, air_mass_flow_kg_s=1)
        r = analyze(raw)
        # gamma=4/3, PR=16 -> temperature ratio=2 exactly.
        self.assertAlmostEqual(r["stations"][2]["total_temperature_k"], 596.3, places=10)
        self.assertAlmostEqual(r["stations"][4]["total_temperature_k"], 596.3, places=10)
        fuel = 596300 / 29105550
        self.assertAlmostEqual(r["metrics"]["fuel_air_ratio"], fuel, places=13)
        self.assertAlmostEqual(r["metrics"]["compressor_power_w"], 298150, places=7)
        self.assertAlmostEqual(r["metrics"]["net_electric_power_w"], (1 + fuel) * 596300 - 298150, places=7)

    def test_mass_and_energy_balances_across_operating_grid(self):
        for pr in (2, 4, 8, 12):
            for tit in (1000, 1300, 1600):
                with self.subTest(pr=pr, tit=tit):
                    r = analyze(self.case(compressor_pressure_ratio=pr, turbine_inlet_temperature_k=tit))
                    b = r["energy_balance"]
                    self.assertLess(abs(b["mass_balance_residual_kg_s"]), 1e-12)
                    self.assertLess(abs(b["shaft_balance_residual_w"]), 1e-6)
                    if b["electric_balance_residual_w"] is not None:
                        self.assertLess(abs(b["electric_balance_residual_w"]), 1e-6)

    def test_fuel_mass_is_included_in_turbine_power(self):
        r = analyze(default_input('constant_cp'))
        m = r["metrics"]
        self.assertGreater(r["stations"][3]["mass_flow_kg_s"], r["stations"][2]["mass_flow_kg_s"])
        expected = m["gas_mass_flow_kg_s"] * 1150 * (1123 - m["exhaust_temperature_k"])
        self.assertAlmostEqual(m["turbine_gas_power_w"], expected)

    def test_burner_energy_with_shared_reference(self):
        inp = default_input('constant_cp')
        r = analyze(inp)
        m, s, v = r["metrics"], r["stations"], inp["parameters"]
        hin = m["air_mass_flow_kg_s"] * v["cp_air_j_kg_k"] * (s[2]["total_temperature_k"] - T_REF_K)
        hout = m["gas_mass_flow_kg_s"] * v["cp_gas_j_kg_k"] * (s[3]["total_temperature_k"] - T_REF_K)
        self.assertAlmostEqual(hin + v["combustion_efficiency"] * m["fuel_heat_input_w"], hout, places=6)

    def test_target_output_and_forward_calculation_agree(self):
        raw = default_input('constant_cp')
        raw["mode"] = "target_power"
        r = analyze(raw)
        self.assertAlmostEqual(r["metrics"]["net_electric_power_w"], 100000, places=7)
        raw["mode"] = "airflow"
        raw["parameters"]["air_mass_flow_kg_s"] = r["metrics"]["air_mass_flow_kg_s"]
        self.assertAlmostEqual(analyze(raw)["metrics"]["net_electric_power_w"], 100000, places=7)

    def test_flow_scaling_with_fixed_auxiliary_power(self):
        a = analyze(self.case(air_mass_flow_kg_s=1))
        b = analyze(self.case(air_mass_flow_kg_s=2))
        self.assertAlmostEqual(b["metrics"]["fuel_mass_flow_kg_s"], 2 * a["metrics"]["fuel_mass_flow_kg_s"])
        self.assertAlmostEqual(b["metrics"]["net_electric_power_w"], 2 * a["metrics"]["net_electric_power_w"] + 2000)

    def test_shaft_generator_and_lhv_definitions(self):
        r = analyze(default_input('constant_cp'))
        m = r["metrics"]
        self.assertAlmostEqual(m["net_shaft_power_w"], .98 * m["turbine_gas_power_w"] - m["compressor_power_w"])
        self.assertAlmostEqual(m["net_electric_power_w"], .95 * m["net_shaft_power_w"] - 2000)
        self.assertAlmostEqual(m["heat_rate_kj_kwh"], 3600 / m["electric_efficiency_lhv"])
        self.assertAlmostEqual(m["specific_fuel_consumption_kg_kwh"], m["fuel_mass_flow_kg_s"] * 3600 / (m["net_electric_power_w"] / 1000))

    def test_exhaust_boundary_and_station_pressure_definitions(self):
        r = analyze(default_input('constant_cp'))
        s = r["stations"]
        self.assertAlmostEqual(s[1]["total_pressure_pa"], .99 * 101325)
        self.assertAlmostEqual(s[2]["total_pressure_pa"], s[1]["total_pressure_pa"] * 3.8)
        self.assertAlmostEqual(s[3]["total_pressure_pa"], s[2]["total_pressure_pa"] * .96)
        self.assertAlmostEqual(s[4]["total_pressure_pa"], 101325 / .98)
        self.assertEqual(s[5]["total_pressure_pa"], 101325)
        self.assertEqual(s[4]["total_temperature_k"], s[5]["total_temperature_k"])

    def test_lower_compressor_efficiency_increases_compressor_work(self):
        a = analyze(self.case(compressor_efficiency=.85))
        b = analyze(self.case(compressor_efficiency=.65))
        self.assertGreater(b["metrics"]["compressor_power_w"], a["metrics"]["compressor_power_w"])
        self.assertLess(b["metrics"]["net_electric_power_w"], a["metrics"]["net_electric_power_w"])

    def test_inlet_burner_and_exhaust_losses_reduce_available_power(self):
        base = analyze(self.case(inlet_pressure_loss=0, combustor_pressure_loss=0, exhaust_pressure_loss=0))
        for parameter in ("inlet_pressure_loss", "combustor_pressure_loss", "exhaust_pressure_loss"):
            p = dict(inlet_pressure_loss=0, combustor_pressure_loss=0, exhaust_pressure_loss=0)
            p[parameter] = .1
            self.assertLess(analyze(self.case(**p))["metrics"]["net_electric_power_w"], base["metrics"]["net_electric_power_w"])

    def test_non_generating_shaft_does_not_apply_generator_efficiency_as_motor(self):
        raw = self.case(compressor_pressure_ratio=8, turbine_inlet_temperature_k=850,
                        compressor_efficiency=.5, turbine_efficiency=.5, mechanical_efficiency=.8)
        # Compressor outlet exceeds TIT at these conditions: use a warmer TIT that still cannot supply compressor power.
        raw["parameters"]["turbine_inlet_temperature_k"] = 1100
        r = analyze(raw)
        self.assertLess(r["metrics"]["net_shaft_power_w"], 0)
        for key in ("net_electric_power_w", "gross_electric_power_w", "electric_efficiency_lhv", "heat_rate_kj_kwh"):
            self.assertIsNone(r["metrics"][key])
        self.assertEqual(r["status"], "non_generating")
        raw["mode"] = "target_power"
        with self.assertRaises(InputError):
            analyze(raw)

    def test_large_auxiliary_draw_has_no_false_efficiency(self):
        r = analyze(self.case(auxiliary_power_w=300000))
        self.assertLess(r["metrics"]["net_electric_power_w"], 0)
        self.assertIsNone(r["metrics"]["electric_efficiency_lhv"])
        self.assertIsNone(r["metrics"]["specific_fuel_consumption_kg_kwh"])

    def test_infeasible_combustor_temperature_and_expansion_are_rejected(self):
        for p in ({"compressor_pressure_ratio": 30, "turbine_inlet_temperature_k": 500},
                  {"compressor_pressure_ratio": 1.01, "inlet_pressure_loss": .2, "combustor_pressure_loss": .2, "exhaust_pressure_loss": .2}):
            with self.subTest(p=p), self.assertRaises(InputError):
                analyze(self.case(**p))

    def test_strict_numeric_and_schema_validation(self):
        for value in (True, "1.2", float("nan"), float("inf"), None, 0):
            raw = self.case(air_mass_flow_kg_s=value)
            with self.subTest(value=value), self.assertRaises(InputError):
                analyze(raw)
        raw = default_input('constant_cp')
        raw["parameters"]["unrecognized"] = 1
        with self.assertRaises(InputError):
            analyze(raw)
        raw = default_input('constant_cp')
        raw["schema_version"] = True
        with self.assertRaises(InputError):
            analyze(raw)

    def test_result_hash_and_engine_do_not_mutate_input(self):
        raw = default_input('constant_cp')
        before = copy.deepcopy(raw)
        case = run_case(raw)
        self.assertEqual(raw, before)
        self.assertEqual(case["input_hash"], input_hash(raw))
        self.assertEqual(case["input_hash"], input_hash(json.loads(json.dumps(raw))))
        raw["parameters"]["compressor_pressure_ratio"] += 1
        self.assertNotEqual(case["input_hash"], input_hash(raw))

    def test_context_reference_is_retained_and_checked(self):
        raw = default_input('constant_cp')
        raw["context"] = {"project_id": str(uuid4()), "component_id": str(uuid4()), "source_revision": 3}
        self.assertEqual(run_case(raw)["input"]["context"], raw["context"])
        raw["context"]["project_id"] = None
        with self.assertRaises(InputError):
            validate_input(raw)

    def test_speed_is_metadata_not_fake_map_prediction(self):
        a = analyze(self.case(shaft_speed_rpm=30000))
        b = analyze(self.case(shaft_speed_rpm=90000))
        self.assertEqual(a["metrics"], b["metrics"])

    def test_sweep_fixed_mass_and_failed_points(self):
        raw = default_input('constant_cp')
        raw["mode"] = "target_power"
        response = sweep(raw, 1.01, 50, 11)
        self.assertAlmostEqual(response["fixed_air_mass_flow_kg_s"], analyze(raw)["metrics"]["air_mass_flow_kg_s"])
        self.assertEqual(response["points"][0]["status"], "invalid")
        valid = [p for p in response["points"] if p["status"] == "success"]
        self.assertTrue(valid)
        for point in valid:
            forward = copy.deepcopy(raw)
            forward["mode"] = "airflow"
            forward["parameters"].update(air_mass_flow_kg_s=response["fixed_air_mass_flow_kg_s"], compressor_pressure_ratio=point["pressure_ratio"])
            self.assertAlmostEqual(point["net_electric_power_w"], analyze(forward)["metrics"]["net_electric_power_w"])

    def test_sweep_boundaries(self):
        for a, b, n in ((20, 2, 10), (1, 20, 10), (2, 60, 10), (2, 20, 10000), (2, 20, True), (float("nan"), 20, 10)):
            with self.subTest(a=a, b=b, n=n), self.assertRaises(InputError):
                sweep(default_input('constant_cp'), a, b, n)

    def test_import_uses_inputs_and_rejects_incompatible_module(self):
        case = run_case(default_input('constant_cp'))
        case["result"]["metrics"]["net_electric_power_w"] = 1e12
        inp = read_input_file(case)
        self.assertLess(run_case(inp)["result"]["metrics"]["net_electric_power_w"], 1e6)
        case["module_version"] = "999"
        with self.assertRaises(ValueError):
            read_input_file(case)

    def test_saved_cases_remain_independent_across_db_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cases.sqlite3"
            store = CaseStore(path)
            a = run_case(default_input('constant_cp'))
            store.save(a)
            a["input"]["name"] = "変更しても元は変わらない"
            b = run_case(self.case(compressor_pressure_ratio=5))
            store.save(b)
            reopened = CaseStore(path)
            self.assertEqual(len(reopened.list()), 2)
            self.assertNotEqual(reopened.get(a["run_id"])["input"]["name"], a["input"]["name"])
            self.assertAlmostEqual(reopened.get(b["run_id"])["result"]["metrics"]["fuel_air_ratio"], b["result"]["metrics"]["fuel_air_ratio"])


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = make_server(Path(cls.temp.name) / "api.sqlite3")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path, data=None, headers=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json", "X-Cycle-Token": TOKEN, **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def test_calculation_save_reload_and_input_import(self):
        status, body, _ = self.request("/api/calculate", {"input": default_input('constant_cp')})
        self.assertEqual(status, 200)
        case = json.loads(body)
        status, body, _ = self.request("/api/save", {"input": case["input"], "result": {"fabricated": True}})
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertNotIn("fabricated", saved["result"])
        self.assertEqual(self.request("/api/case?id=" + saved["run_id"])[0], 200)
        self.assertEqual(self.request("/api/import", {"document": saved})[0], 200)
        self.assertEqual(self.request("/api/cases")[0], 200)
        self.assertEqual(self.request("/api/sweep", {"input": default_input('constant_cp'), "count": 5})[0], 200)

    def test_security_and_invalid_inputs(self):
        self.assertEqual(self.request("/api/calculate", {"input": default_input('constant_cp')}, {"X-Cycle-Token": "bad"})[0], 403)
        self.assertEqual(self.request("/api/calculate", {"input": default_input('constant_cp')}, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("/api/schema", headers={"Host": "example.com"})[0], 403)
        self.assertEqual(self.request("/api/calculate", {"input": {}})[0], 400)
        self.assertEqual(self.request("/api/import", {"document": {}})[0], 400)
        self.assertEqual(self.request("/api/unknown", {})[0], 404)
        self.assertEqual(self.request("/../engine.py")[0], 404)

    def test_assets_and_response_headers(self):
        for path in ("/", "/app.js", "/style.css", "/guide", "/api/schema"):
            status, body, headers = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            if path == "/":
                self.assertIn(TOKEN.encode(), body)

    def test_nasa_http_default_and_legacy_input_migration(self):
        schema = json.loads(self.request("/api/schema")[1])
        self.assertEqual(schema["default"]["thermodynamics"]["model"], "nasa9_frozen")
        self.assertEqual(schema["module_version"], "0.4.0")
        status, data, _ = self.request("/api/calculate", {"input": schema["default"]})
        self.assertEqual(status, 200)
        result = json.loads(data)["result"]
        self.assertGreater(result["stations"][3]["cp_j_kg_k"], result["stations"][1]["cp_j_kg_k"])
        self.assertEqual(result["thermodynamics"]["dataset"]["dataset_id"], "nasa-cea-985fd33368b2-nasa9-5species")
        legacy = default_input("constant_cp")
        legacy["schema_version"] = 1
        del legacy["thermodynamics"]
        del legacy["staging"]
        del legacy["elements"]
        status, data, _ = self.request("/api/import", {"document": legacy})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["input"]["thermodynamics"]["model"], "constant_cp")

    def test_multistage_http_save_import_and_invalid_weight_count(self):
        inp = default_input()
        inp["staging"]["compressor"].update(count=4, weights=[1, 1, 1, 1])
        inp["staging"]["turbine"].update(count=3, distribution="work", weights=[4, 3.5, 2.5])
        status, data, _ = self.request("/api/save", {"input": inp})
        self.assertEqual(status, 200)
        saved = json.loads(data)
        self.assertEqual(len(saved["result"]["stage_results"]["compressor"]["stages"]), 4)
        self.assertAlmostEqual(saved["result"]["stage_results"]["turbine"]["stages"][0]["work_fraction"], .4)
        loaded = json.loads(self.request("/api/case?id="+saved["run_id"])[1])
        self.assertEqual(loaded["input"]["staging"], saved["input"]["staging"])
        imported = json.loads(self.request("/api/import", {"document": loaded})[1])
        self.assertEqual(imported["input"]["staging"], loaded["input"]["staging"])
        inp["staging"]["compressor"]["weights"] = [1]
        self.assertEqual(self.request("/api/calculate", {"input": inp})[0], 400)

    def test_element_network_http_save_roundtrip_and_reject_pressure_gain(self):
        inp = default_input()
        inp["elements"]["compressor"]["enabled"] = True
        inp["elements"]["turbine"]["enabled"] = True
        status, body, _ = self.request("/api/save", {"input": inp})
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual(saved["result"]["stage_results"]["compressor"]["method"], "coefficient_element_network")
        self.assertEqual(len(saved["result"]["stage_results"]["compressor"]["elements"]), 3)
        imported = json.loads(self.request("/api/import", {"document": saved})[1])
        self.assertEqual(imported["input"]["elements"], saved["input"]["elements"])
        loaded = json.loads(self.request("/api/case?id="+saved["run_id"])[1])
        self.assertEqual(loaded["result"]["stage_results"], saved["result"]["stage_results"])
        inp["elements"]["compressor"]["stages"][0]["diffuser"]["coefficient"] = 2
        self.assertEqual(self.request("/api/calculate", {"input": inp})[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
