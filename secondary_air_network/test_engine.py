import copy
import math
import unittest
from unittest import mock

from engine import CalculationError, ConvergenceError, _inlet, _march, branch, cp_of, friction, orifice, solve, sweep, viscosity
from model import MAX_EDGES, MAX_NODES, ModelError, sample, validate

GAS = {"R": 287.05, "gamma": 1.4}


def network(pu=500000, pd=100000, tu=550, td=800):
    return {"schema_version": 1, "name": "test", "gas": dict(GAS), "nodes": [
        {"id": "a", "name": "supply", "kind": "boundary", "pressure_pa": pu, "temperature_k": tu},
        {"id": "b", "name": "sink", "kind": "boundary", "pressure_pa": pd, "temperature_k": td}],
        "edges": [{"id": "e", "name": "hole", "from": "a", "to": "b", "kind": "orifice", "area_m2": 1e-4, "cd": .7}]}


def pipe_model(**kw):
    m = network()
    m["edges"][0].update(kind="pipe", diameter_m=.02, length_m=1.0, roughness_m=0,
                         friction="fixed", friction_factor=.02, k=0, cells=96)
    m["edges"][0].update(kw)
    return validate(m)


def large_network():
    case = network(pu=240000, pd=120000, tu=500, td=500)
    case["name"] = "大規模なシール・冷却回路の仮想例"
    case["edges"] = []
    rooms = []
    for i in range(MAX_NODES - 2):
        room = f"room-{i:03d}"
        rooms.append(room)
        case["nodes"].append({"id": room, "name": f"混合室 {i}", "kind": "junction",
                              "pressure_pa": 170000 + (i % 7) * 1500, "temperature_k": 500})
        feed = {"id": f"feed-{i}", "from": "a", "to": room, "kind": "orifice",
                "area_m2": 1e-4 + (i % 7) * 1e-6, "cd": .72}
        drain = {"id": f"drain-{i}", "from": room, "to": "b", "kind": "orifice",
                 "area_m2": 1.26e-4, "cd": .72}
        if i < 8:
            feed.update(kind="pipe", diameter_m=.012, length_m=.2, cells=16,
                        friction="fixed", friction_factor=.02, k=.3)
            drain.update(kind="labyrinth", seal_diameter_m=.12, clearance_m=.0002, tooth_count=4)
        case["edges"].extend((feed, drain))
    cross_count = MAX_EDGES - len(case["edges"])
    for i in range(cross_count):
        case["edges"].append({"id": f"cross-{i}", "from": rooms[i], "to": rooms[(i + 1) % cross_count],
                              "kind": "orifice", "area_m2": .000003, "cd": .7})
    return case


class FlowTests(unittest.TestCase):
    def test_bearing_chamber_converges_from_both_initial_pressures(self):
        model = network(pu=160000, pd=101325, tu=330, td=300)
        chamber = {"id": "chamber", "name": "軸受室", "kind": "junction", "temperature_k": 330}
        model["nodes"].append(chamber)
        model["edges"] = [
            {"id": "shaft-seal", "from": "a", "to": "chamber", "kind": "labyrinth",
             "seal_diameter_m": .12, "clearance_m": .00015, "tooth_count": 4, "cd": .7},
            {"id": "vent", "from": "chamber", "to": "b", "kind": "orifice",
             "area_m2": .00006, "cd": .72},
        ]
        pressures = []
        for guess in (125000, 130000):
            with self.subTest(initial_pressure_pa=guess):
                chamber["pressure_pa"] = guess
                result = solve(model)
                node = result["nodes"][-1]
                self.assertEqual(result["status"], "converged")
                self.assertAlmostEqual(node["pressure_pa"], 113882.366, delta=.01)
                self.assertAlmostEqual(node["temperature_k"], 330)
                self.assertAlmostEqual(node["outflow_kg_s"], .007049, delta=1e-6)
                heat_tol = 1e-5 + 1e-7 * cp_of(GAS) * 330 * max(node["inflow_kg_s"], node["outflow_kg_s"])
                self.assertLessEqual(abs(node["energy_residual_w"]), heat_tol)
                pressures.append(node["pressure_pa"])
        if len(pressures) == 2:
            self.assertAlmostEqual(*pressures, delta=.01)

    def test_pressure_failure_reports_worst_mass_balance_first(self):
        model = network()
        model["nodes"].extend(
            {"id": key, "name": name, "kind": "junction", "pressure_pa": 250000, "temperature_k": 550}
            for key, name in (("c", "小流量室"), ("d", "大流量室"))
        )
        inlet = model["edges"].pop()
        for key, factor in (("c", 1), ("d", 2)):
            model["edges"].extend([
                dict(inlet, id=f"in-{key}", to=key, area_m2=factor * 1e-4),
                dict(inlet, id=f"out-{key}", **{"from": key, "area_m2": .00005}),
            ])
        with mock.patch("engine.linear_solve", side_effect=CalculationError("圧力補正に失敗")):
            with self.assertRaisesRegex(ConvergenceError, "圧力補正に失敗") as failure:
                solve(model)
        diagnostics = failure.exception.diagnostics
        self.assertEqual(diagnostics["phase"], "pressure")
        self.assertEqual([row["node_id"] for row in diagnostics["nodes"]], ["d", "c"])
        for row in diagnostics["nodes"]:
            self.assertGreater(abs(row["residual"]), row["tolerance"])

    def test_large_mixed_network_converges_at_both_limits(self):
        result = solve(large_network())
        self.assertEqual(result["status"], "converged")
        self.assertEqual(len(result["nodes"]), MAX_NODES)
        self.assertEqual(len(result["edges"]), MAX_EDGES)
        self.assertEqual(sum(edge["kind"] == "labyrinth" for edge in result["edges"]), 8)
        self.assertEqual(sum(edge["kind"] == "pipe" for edge in result["edges"]), 8)
        self.assertLess(result["summary"]["max_mass_residual_kg_s"], 1e-8)
        self.assertTrue(all(120000 < row["pressure_pa"] < 240000 for row in result["nodes"]
                            if row["kind"] == "junction"))

    def test_nasa_choked_reference(self):
        # NASA mass-flow equation evaluated independently at M=1.
        pu, tu, area, cd, g, r = 500000, 550, 1e-4, .7, 1.4, 287.05
        expected = cd * area * pu * math.sqrt(g / (r * tu)) * (2 / (g + 1)) ** ((g + 1) / (2 * (g - 1)))
        result = solve(network())["edges"][0]
        self.assertAlmostEqual(result["mass_flow_abs"], expected, places=12)
        self.assertTrue(result["choked"])

    def test_nonchoked_reference(self):
        pu, pd, tu, a, cd = 500000, 450000, 550, 1e-4, .7
        ratio = pd / pu
        expected = cd * a * pu * math.sqrt(2 * 1.4 / (287.05 * tu * .4) * (ratio ** (2 / 1.4) - ratio ** (2.4 / 1.4)))
        out = solve(network(pu, pd))["edges"][0]
        self.assertFalse(out["choked"])
        self.assertAlmostEqual(out["mass_flow_abs"], expected, places=12)

    def test_choke_continuity_and_backpressure_independence(self):
        crit = (2 / 2.4) ** (1.4 / .4)
        x = orifice(500000, 550, 500000 * crit * 1.000001, 1e-4, .7, GAS)
        y = orifice(500000, 550, 500000 * crit * .999999, 1e-4, .7, GAS)
        z = orifice(500000, 550, 1000, 1e-4, .7, GAS)
        self.assertLess(abs(x["mass_flow_abs"] / y["mass_flow_abs"] - 1), 1e-10)
        self.assertEqual(y["mass_flow_abs"], z["mass_flow_abs"])

    def test_reverse_uses_reverse_cd_and_upstream_temperature(self):
        m = network(100000, 500000, 300, 800)
        m["edges"][0]["cd_reverse"] = .4
        out = solve(m)["edges"][0]
        expected = orifice(500000, 800, 100000, 1e-4, .4, GAS)
        self.assertTrue(out["reversed"])
        self.assertLess(out["mass_flow_kg_s"], 0)
        self.assertEqual(out["upstream"], "b")
        self.assertEqual(out["outlet_total_temperature_k"], 800)
        self.assertEqual(out["mass_flow_abs"], expected["mass_flow_abs"])

    def test_stagnant_network(self):
        m = network(200000, 200000)
        m["nodes"].append({"id": "c", "kind": "junction", "pressure_pa": 200000, "temperature_k": 400})
        m["edges"].append(dict(m["edges"][0], id="e2", to="c"))
        out = solve(m)
        self.assertEqual(out["summary"]["supply_kg_s"], 0)
        self.assertIsNone(out["nodes"][2]["temperature_k"])

    def test_symmetric_parallel_network_and_thermal_mixing(self):
        m = network(600000, 100000, 400, 700)
        m["nodes"] += [{"id": "hot", "kind": "boundary", "pressure_pa": 600000, "temperature_k": 800},
                       {"id": "mix", "kind": "junction", "pressure_pa": 350000, "temperature_k": 500, "heat_w": 50}]
        m["edges"] = [dict(m["edges"][0], id="cold", to="mix"),
                      dict(m["edges"][0], id="hotfeed", **{"from": "hot", "to": "mix"}),
                      dict(m["edges"][0], id="out", **{"from": "mix", "to": "b"}, area_m2=3e-4)]
        out = solve(m)
        a, b, c = out["edges"]
        expected = (a["mass_flow_abs"] * 400 + b["mass_flow_abs"] * 800 + 50 / cp_of(GAS)) / (a["mass_flow_abs"] + b["mass_flow_abs"])
        mix = next(n for n in out["nodes"] if n["id"] == "mix")
        self.assertAlmostEqual(mix["temperature_k"], expected, delta=1e-4)
        self.assertAlmostEqual(a["mass_flow_abs"] + b["mass_flow_abs"], c["mass_flow_abs"], delta=1e-8)
        self.assertLess(abs(out["summary"]["global_energy_residual_w"]), .02)

    def test_identical_parallel_branches(self):
        m = sample()
        m["edges"] = [dict(m["edges"][1], id="in", **{"from":"bleed", "to":"plenum"}),
                      dict(m["edges"][1], id="out1", **{"from":"plenum", "to":"gaspath"}),
                      dict(m["edges"][1], id="out2", **{"from":"plenum", "to":"gaspath"})]
        m["nodes"] = [n for n in m["nodes"] if n["id"] in ("bleed", "plenum", "gaspath")]
        out = solve(m)
        self.assertAlmostEqual(out["edges"][1]["mass_flow_abs"], out["edges"][2]["mass_flow_abs"], places=12)
        self.assertAlmostEqual(out["edges"][0]["mass_flow_abs"], 2 * out["edges"][1]["mass_flow_abs"], delta=1e-8)

    def test_sample_and_split_grid(self):
        a = solve(sample())
        m = sample()
        m["edges"][0]["cells"] = 192
        b = solve(m)
        self.assertLess(abs(a["summary"]["supply_kg_s"] / b["summary"]["supply_kg_s"] - 1), .001)
        self.assertLess(a["summary"]["max_mass_residual_kg_s"], 1e-8)
        self.assertLess(a["summary"]["max_energy_residual_w"], .02)

    def test_pipe_against_fanno_analytic(self):
        g, r, t0, p0, m1, m2, area, f, d = 1.4, 287.05, 550, 500000, .2, .6, 1e-4, .02, .02
        def fanno(m):
            return (1-m*m)/(g*m*m)+(g+1)/(2*g)*math.log((g+1)*m*m/(2+(g-1)*m*m))
        length = (fanno(m1)-fanno(m2))*d/f
        t1 = t0/(1+(g-1)/2*m1*m1)
        p1 = p0*(t1/t0)**(g/(g-1))
        pd = p1*m1/m2*math.sqrt((1+(g-1)/2*m1*m1)/(1+(g-1)/2*m2*m2))
        expected = p1/(r*t1)*m1*math.sqrt(g*r*t1)*area
        m = pipe_model(length_m=length, cells=192)
        m["nodes"][1]["pressure_pa"] = pd
        out = solve(m)["edges"][0]
        self.assertLess(abs(out["mass_flow_abs"]/expected-1), .001)
        self.assertAlmostEqual(out["max_mach"], m2, delta=.003)
        self.assertEqual(out["outlet_total_temperature_k"], t0)

    def test_pipe_choke_backpressure_independence(self):
        m = pipe_model()
        m["nodes"][1]["pressure_pa"] = 10000
        a = solve(m)["edges"][0]
        m["nodes"][1]["pressure_pa"] = 5000
        b = solve(m)["edges"][0]
        self.assertTrue(a["choked"])
        self.assertTrue(b["choked"])
        self.assertAlmostEqual(a["mass_flow_abs"], b["mass_flow_abs"], places=10)
        self.assertGreater(a["max_mach"], .99)

    def test_no_friction_pipe_equals_nozzle(self):
        m = pipe_model(friction_factor=0)
        m["nodes"][1]["pressure_pa"] = 450000
        actual = solve(m)["edges"][0]
        expected = orifice(500000, 550, 450000, 1e-4, 1, GAS)
        self.assertLess(abs(actual["mass_flow_abs"]/expected["mass_flow_abs"]-1), 1e-7)

    def test_heating_energy_and_momentum_conservation(self):
        m = pipe_model(friction_factor=0, heat_w=100, cells=96)
        e = m["edges"][0]
        out = _march(.03, 500000, 550, e, GAS)
        self.assertIsNotNone(out)
        self.assertAlmostEqual(.03*cp_of(GAS)*(out["outlet_total_temperature_k"]-550), 100, places=8)
        vi, pi = _inlet(.03, 500000, 550, 1e-4, GAS)
        flux = .03/1e-4
        p = out["exit_static_pressure_pa"]
        # cp*T0 = cp*p*v/(G*R) + v²/2, solve independently for exit v.
        cp, r = cp_of(GAS), GAS["R"]
        b = cp*p/(flux*r)
        vo = 2*cp*out["outlet_total_temperature_k"]/(b+math.sqrt(b*b+2*cp*out["outlet_total_temperature_k"]))
        self.assertAlmostEqual(pi+flux*vi, p+flux*vo, delta=.001)

    def test_specified_heat_pipe_solve(self):
        m = pipe_model(heat_w=100)
        m["nodes"][1]["pressure_pa"] = 300000
        out = solve(m)["edges"][0]
        self.assertAlmostEqual(out["heat_w"], 100, places=7)
        self.assertGreater(out["outlet_total_temperature_k"], 550)

    def test_wall_exchange_and_reverse_pipe(self):
        m = pipe_model(htc_w_m2k=80, wall_temperature_k=750)
        a = solve(m)["edges"][0]
        self.assertGreater(a["outlet_total_temperature_k"], 550)
        m["edges"][0]["from"], m["edges"][0]["to"] = "b", "a"
        b = solve(m)["edges"][0]
        self.assertTrue(b["reversed"])
        self.assertAlmostEqual(a["mass_flow_abs"], b["mass_flow_abs"], places=10)
        self.assertAlmostEqual(a["heat_w"], b["heat_w"], places=6)

    def test_heated_network_equal_initial_pressures(self):
        m = network()
        m["nodes"] += [{"id":"c", "kind":"junction", "pressure_pa":300000, "temperature_k":550},
                       {"id":"d", "kind":"junction", "pressure_pa":300000, "temperature_k":550}]
        e = m["edges"][0]
        heater = pipe_model(heat_w=100)["edges"][0]
        heater.update(id="heated", **{"from":"c", "to":"d"})
        m["edges"] = [dict(e,id="in",to="c"), heater, dict(e,id="out",**{"from":"d"})]
        out = solve(m)
        self.assertEqual(out["status"], "converged")
        self.assertLess(abs(out["summary"]["global_energy_residual_w"]), .03)

    def test_cooling_energy_and_inlet_maximum_mach(self):
        m = pipe_model(heat_w=-100, friction_factor=0)
        out = solve(m)["edges"][0]
        self.assertAlmostEqual(out["heat_w"], -100, places=7)
        self.assertLess(out["outlet_total_temperature_k"], 550)
        self.assertGreater(out["max_mach"], 0)

    def test_local_resistance_reduces_flow(self):
        m = pipe_model(kind="resistance", length_m=0, friction_factor=0)
        m["nodes"][1]["pressure_pa"] = 400000
        a = solve(m)["edges"][0]
        m["edges"][0]["k"] = 4
        b = solve(m)["edges"][0]
        self.assertGreater(a["mass_flow_abs"], b["mass_flow_abs"])

    def test_sutherland_and_laminar_factor(self):
        self.assertEqual(viscosity(273.15), 1.716e-5)
        self.assertAlmostEqual(friction(1000, 0, .02), .064)
        self.assertGreater(friction(1e5, .0001, .02), friction(1e5, 0, .02))

    def test_sweep_failure_explicit_and_no_mutation(self):
        m = network()
        original = copy.deepcopy(m)
        out = sweep(m, {"group":"edges", "id":"e", "field":"area_m2"}, [1e-4, -1, 2e-4])
        self.assertEqual([p["status"] for p in out["points"]], ["converged", "failed", "converged"])
        self.assertEqual(m, original)
        self.assertAlmostEqual(out["points"][2]["summary"]["supply_kg_s"], 2*out["points"][0]["summary"]["supply_kg_s"])

    def test_impossible_stagnant_heating_fails(self):
        m = network(200000, 200000)
        m["nodes"].append({"id":"c", "kind":"junction", "pressure_pa":200000, "temperature_k":500,"heat_w":100})
        m["edges"].append(dict(m["edges"][0],id="e2",to="c"))
        with self.assertRaises(CalculationError):
            solve(m)


class ValidationTests(unittest.TestCase):
    def test_network_beyond_each_capacity_is_rejected(self):
        case = large_network()
        too_many_nodes = copy.deepcopy(case)
        too_many_nodes["nodes"].append({"id": "extra", "kind": "junction"})
        with self.assertRaisesRegex(ModelError, str(MAX_NODES)):
            validate(too_many_nodes)
        case["edges"].append({"id": "extra", "kind": "orifice", "from": "a", "to": "b", "area_m2": 1e-4})
        with self.assertRaisesRegex(ModelError, str(MAX_EDGES)):
            validate(case)

    def test_bad_values(self):
        for value in (float("nan"), float("inf"), True, None, "100"):
            with self.subTest(value=value):
                m = network()
                m["edges"][0]["area_m2"] = value
                with self.assertRaises(ModelError):
                    validate(m)

    def test_duplicate_and_dangling(self):
        m = network()
        m["nodes"][1]["id"] = "a"
        with self.assertRaises(ModelError): validate(m)
        m = network()
        m["edges"][0]["to"] = "missing"
        with self.assertRaises(ModelError): validate(m)

    def test_disconnected_island_and_no_boundary(self):
        m = network()
        m["nodes"].append({"id":"isolated","kind":"junction"})
        with self.assertRaises(ModelError): validate(m)
        m = network()
        for n in m["nodes"]: n["kind"] = "junction"
        with self.assertRaises(ModelError): validate(m)

    def test_input_immutable(self):
        m = sample()
        before = copy.deepcopy(m)
        solve(m)
        self.assertEqual(m, before)


if __name__ == "__main__":
    unittest.main()
