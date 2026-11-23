import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from model import MAX_EDGES, MAX_NODES, sample, stage2_sample, rotating_sample
from server import MAX_REQUEST_BYTES, create_server
from storage import Store
from test_engine import large_network, network


class StorageTests(unittest.TestCase):
    def test_snapshot_persists_without_overwrite(self):
        from engine import solve
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.sqlite3"
            store = Store(path)
            result = solve(sample())
            a, b = store.save(result), store.save(result)
            self.assertNotEqual(a["id"], b["id"])
            self.assertEqual(len(Store(path).list()), 2)
            self.assertEqual(Store(path).get(a["id"])["result"], result)
            with self.assertRaises(LookupError): store.get("missing")


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = create_server(0, Path(cls.tmp.name) / "api.sqlite3")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def request(self, path, data=None, headers=None, method=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=20)
        hdr = {"X-Network-Token": self.server.token, "Content-Type": "application/json"}
        hdr.update(headers or {})
        body = json.dumps(data) if isinstance(data, dict) else data
        conn.request(method or ("POST" if data is not None else "GET"), path, body, hdr)
        resp = conn.getresponse()
        status, mime, content = resp.status, resp.getheader("Content-Type"), resp.read()
        conn.close()
        return status, json.loads(content) if mime.startswith("application/json") else content.decode("utf-8")

    def test_static_sample_and_notfound(self):
        status, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("二次空気ネットワーク", html)
        self.assertNotIn("__TOKEN__", html)
        self.assertIn(f'name="network-max-nodes" content="{MAX_NODES}"', html)
        self.assertIn(f'name="network-max-edges" content="{MAX_EDGES}"', html)
        self.assertEqual(self.request("/api/sample")[0], 200)
        self.assertEqual(self.request("/app.js")[0], 200)
        self.assertEqual(self.request("/style.css")[0], 200)
        self.assertEqual(self.request("/../server.py")[0], 404)

    def test_solve_and_save_recomputes_and_reload(self):
        m = sample()
        m["name"] = "保存確認・日本語"
        status, solved = self.request("/api/solve", {"model": m})
        self.assertEqual(status, 200)
        status, saved = self.request("/api/save", {"model": m, "result": {"status":"forged"}})
        self.assertEqual(status, 200)
        self.assertEqual(saved["result"]["status"], "converged")
        self.assertEqual(saved["result"]["summary"], solved["summary"])
        self.assertEqual(self.request("/api/case?id=" + saved["id"])[1], saved)
        self.assertTrue(any(c["id"] == saved["id"] for c in self.request("/api/cases")[1]))

    def test_large_network_validate_save_and_reload(self):
        model = large_network()
        for edge in model["edges"]:
            edge["notes"] = "仮想の設計記録。" * 50
        self.assertGreater(len(json.dumps({"model": model})), 1024 * 1024)
        status, normalized = self.request("/api/validate", {"model": model})
        self.assertEqual(status, 200)
        self.assertEqual(len(normalized["nodes"]), MAX_NODES)
        self.assertEqual(len(normalized["edges"]), MAX_EDGES)
        status, saved = self.request("/api/save", {"model": normalized})
        self.assertEqual(status, 200, saved.get("error"))
        self.assertEqual(saved["result"]["status"], "converged")
        self.assertEqual(self.request("/api/case?id=" + saved["id"])[1], saved)

    def test_bad_token_origin_and_host(self):
        self.assertEqual(self.request("/api/solve", {"model": sample()}, {"X-Network-Token":"bad"})[0], 403)
        self.assertEqual(self.request("/api/solve", {"model": sample()}, {"Origin":"https://example.com"})[0], 403)
        self.assertEqual(self.request("/", headers={"Host":"malicious.example"})[0], 403)

    def test_bad_json_nan_and_invalid_model(self):
        self.assertEqual(self.request("/api/solve", "{")[0], 400)
        self.assertEqual(self.request("/api/solve", '{"model": NaN}')[0], 400)
        self.assertEqual(self.request("/api/solve", "[]")[0], 400)
        m = sample()
        m["edges"][0]["area_m2"] = -1
        self.assertEqual(self.request("/api/solve", {"model": m})[0], 400)
        self.assertEqual(self.request("/api/case?id=missing")[0], 404)

    def test_validate_and_sweep(self):
        status, model = self.request("/api/validate", {"model": sample()})
        self.assertEqual(status, 200)
        self.assertIn("cd_reverse", model["edges"][1])
        status, out = self.request("/api/sweep", {"model": sample(), "target":{"group":"nodes","id":"bleed","field":"pressure_pa"}, "values":[500000,600000]})
        self.assertEqual(status, 200)
        self.assertEqual([p["status"] for p in out["points"]], ["converged", "converged"])

    def test_failed_save_does_not_create_case(self):
        before = len(self.request("/api/cases")[1])
        m = sample()
        m["edges"][0]["area_m2"] = -1
        self.assertEqual(self.request("/api/save", {"model": m})[0], 400)
        self.assertEqual(len(self.request("/api/cases")[1]), before)

    def test_infeasible_heated_room_returns_thermal_diagnostics_without_save(self):
        model = network(200000, 200000)
        chamber = {"id": "chamber", "name": "軸受室", "kind": "junction",
                   "pressure_pa": 200000, "temperature_k": 500, "heat_w": 100}
        model["nodes"].append(chamber)
        model["edges"].append(dict(model["edges"][0], id="feed", to=chamber["id"]))
        before = self.request("/api/cases")[1]
        for path in ("/api/solve", "/api/save"):
            with self.subTest(path=path):
                status, result = self.request(path, {"model": model})
                self.assertEqual(status, 400)
                self.assertIn("定常解がありません", result["error"])
                self.assertEqual(result["diagnostics"], {"phase": "thermal", "nodes": [{
                    "node_id": "chamber", "node_name": "軸受室", "residual": 100, "tolerance": 1e-5,
                }]})
        self.assertEqual(self.request("/api/cases")[1], before)

    def test_busy_and_payload_limit(self):
        with self.server.calculation_lock:
            self.assertEqual(self.request("/api/solve", {"model": sample()})[0], 409)
        self.assertEqual(self.request("/api/solve", " ", {"Content-Length": str(MAX_REQUEST_BYTES + 1)})[0], 413)

    def test_stage2_seal_case_roundtrip_and_sweep(self):
        self.assertEqual(self.request("/api/sample-stage2")[1]["edges"][-1]["kind"],"labyrinth")
        status,saved=self.request("/api/save",{"model":stage2_sample()})
        self.assertEqual(status,200)
        loaded=self.request("/api/case?id="+saved["id"])[1]
        self.assertEqual(len(loaded["result"]["edges"][-1]["teeth"]),4)
        self.assertEqual(loaded["result"]["model"]["vortex_study"]["rpm"],30000)
        status,out=self.request("/api/sweep",{"model":stage2_sample(),"target":{"group":"edges","id":"seal","field":"clearance_m"},"values":[.0001,.0002]})
        self.assertEqual(status,200)
        self.assertTrue(all(p["status"]=="converged" for p in out["points"]))

    def test_vortex_api_validates_and_keeps_network_separate(self):
        v=stage2_sample()["vortex_study"]
        status,out=self.request("/api/vortex",{"vortex":v})
        self.assertEqual(status,200)
        self.assertEqual(len(out["profile"]),21)
        self.assertGreater(out["pressure_ratio_end_start"],1)
        self.assertNotIn("mass_flow_kg_s",out)
        self.assertEqual(self.request("/api/vortex",{"vortex":dict(v,rpm=None)})[0],400)
        self.assertEqual(self.request("/api/vortex",{"vortex":v}, {"X-Network-Token":"bad"})[0],403)

    def test_rotating_network_save_and_rotor_sweep(self):
        status,m=self.request("/api/sample-rotating")
        self.assertEqual(status,200)
        status,saved=self.request("/api/save",{"model":m})
        self.assertEqual(status,200)
        result=self.request("/api/case?id="+saved["id"])[1]["result"]
        self.assertGreater(result["summary"]["shaft_work_w"],0)
        self.assertEqual(result["nodes"][0]["frame"],"rotating")
        self.assertGreater(result["nodes"][0]["absolute_total_temperature_k"],550)
        status,out=self.request("/api/sweep",{"model":m,"target":{"group":"rotation","id":"rot_in","field":"rpm"},"values":[0,30000]})
        self.assertEqual(status,200)
        self.assertTrue(all(p["status"]=="converged" for p in out["points"]))
        self.assertEqual(out["points"][0]["summary"]["shaft_work_w"],0)

    def test_mixed_frames_fail_without_saving(self):
        m=rotating_sample();m["nodes"][0].update(frame="stationary",rpm=0)
        before=len(self.request("/api/cases")[1])
        self.assertEqual(self.request("/api/save",{"model":m})[0],400)
        self.assertEqual(len(self.request("/api/cases")[1]),before)

    def test_grid_study_security_lock_and_no_case_mutation(self):
        before=self.request("/api/cases")[1]
        data={"model":rotating_sample(),"relative_percent":.1}
        self.assertEqual(self.request("/api/grid-study",data,{"X-Network-Token":"bad"})[0],403)
        with self.server.calculation_lock:
            self.assertEqual(self.request("/api/grid-study",data)[0],409)
        status,report=self.request("/api/grid-study",data)
        self.assertEqual(status,200)
        self.assertEqual(report["status"],"completed")
        self.assertEqual(report["assessment"],"within_tolerance")
        self.assertEqual(len(report["levels"]),3)
        self.assertEqual(self.request("/api/cases")[1],before)
        data["model"]["edges"][0]["cells"]=96
        self.assertEqual(self.request("/api/grid-study",data)[0],400)


if __name__ == "__main__":
    unittest.main()
