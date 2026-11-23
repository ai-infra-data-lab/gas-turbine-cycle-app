import copy
import math
import unittest

from engine import CalculationError, orifice, solve, sweep
from model import ModelError, stage2_sample, validate
from test_engine import GAS, network
from vortex import solve_vortex


def seal_network(count=4, pu=500000, pd=100000, gaps=None):
    m=network(pu,pd)
    m["edges"][0].update(kind="labyrinth",seal_diameter_m=.12,clearance_m=.00015,
                         tooth_count=count,cd=.7,cd_reverse=.7,tooth_clearances_m=gaps or [])
    return m


def vortex_input(**kwargs):
    v=dict(kind="forced",rpm=30000,swirl_ratio=.5,reference_radius_m=.08,
           start_radius_m=.08,end_radius_m=.18,reference_pressure_pa=300000,
           static_temperature_k=600,points=21)
    v.update(kwargs)
    return v


class SealTests(unittest.TestCase):
    def test_one_tooth_equals_orifice(self):
        for pd in (400000,100000):
            with self.subTest(pd=pd):
                e=solve(seal_network(1,pd=pd))["edges"][0]
                expected=orifice(500000,550,pd,math.pi*.12*.00015,.7,GAS)
                self.assertAlmostEqual(e["mass_flow_abs"],expected["mass_flow_abs"],delta=1e-10)
                self.assertEqual(e["choked"],expected["choked"])

    def test_tooth_chain_matches_explicit_network(self):
        # Independent network solve of separate orifices and mixed chambers.
        for pd in (380000,100000):
            a=solve(seal_network(4,pd=pd))
            m=network(500000,pd)
            m["nodes"] += [{"id":f"p{i}","kind":"junction","pressure_pa":500000-(500000-pd)*i/4,"temperature_k":550} for i in range(1,4)]
            ids=["a","p1","p2","p3","b"]
            m["edges"]=[dict(id=f"t{i}",name=f"tooth{i}",kind="orifice",**{"from":ids[i],"to":ids[i+1]},area_m2=math.pi*.12*.00015,cd=.7) for i in range(4)]
            b=solve(m)
            self.assertAlmostEqual(a["edges"][0]["mass_flow_abs"],b["edges"][0]["mass_flow_abs"],delta=2e-8)
            actual=a["edges"][0]["teeth"]
            for i in range(3):
                expected=next(n["pressure_pa"] for n in b["nodes"] if n["id"]==f"p{i+1}")
                self.assertAlmostEqual(actual[i]["downstream_pressure_pa"],expected,delta=.1)

    def test_more_teeth_reduce_flow_and_clearance_linear(self):
        flows=[solve(seal_network(n))["edges"][0]["mass_flow_abs"] for n in (1,2,4,8)]
        self.assertTrue(all(a>b for a,b in zip(flows,flows[1:])))
        a=seal_network()
        b=copy.deepcopy(a);b["edges"][0]["clearance_m"]*=2
        self.assertAlmostEqual(solve(b)["edges"][0]["mass_flow_abs"],2*solve(a)["edges"][0]["mass_flow_abs"],places=9)

    def test_choke_backpressure_and_mass_residual(self):
        a=solve(seal_network(pd=50000))["edges"][0]
        b=solve(seal_network(pd=20000))["edges"][0]
        self.assertTrue(a["choked"])
        self.assertTrue(a["teeth"][-1]["choked"])
        self.assertAlmostEqual(a["mass_flow_abs"],b["mass_flow_abs"],places=10)
        self.assertLess(a["max_tooth_mass_residual_kg_s"],1e-9)

    def test_critical_ratio_classification(self):
        critical=(2/2.4)**(1.4/.4)
        a=solve(seal_network(1,pd=500000*critical*(1+1e-6)))["edges"][0]
        b=solve(seal_network(1,pd=500000*critical*(1-1e-6)))["edges"][0]
        self.assertFalse(a["choked"])
        self.assertTrue(b["choked"])
        self.assertLess(abs(a["mass_flow_abs"]/b["mass_flow_abs"]-1),1e-8)

    def test_reverse_gap_order_and_reverse_cd(self):
        gaps=[.00018,.00016,.00014,.00012]
        forward=seal_network(gaps=gaps,pd=400000)
        backward=seal_network(gaps=list(reversed(gaps)),pu=400000,pd=500000)
        backward["nodes"][1]["temperature_k"]=550
        a=solve(forward)["edges"][0]
        b=solve(backward)["edges"][0]
        self.assertTrue(b["reversed"])
        self.assertEqual([t["tooth"] for t in b["teeth"]],[4,3,2,1])
        self.assertAlmostEqual(a["mass_flow_abs"],b["mass_flow_abs"],places=10)
        backward["edges"][0]["cd_reverse"] = .4
        c=solve(backward)["edges"][0]
        self.assertAlmostEqual(c["mass_flow_abs"]/b["mass_flow_abs"],.4/.7,places=7)

    def test_intermediate_choke_is_not_silent(self):
        m=seal_network(gaps=[.00001,.001,.001,.001],pd=10000)
        with self.assertRaisesRegex(CalculationError,"中間"):
            solve(m)

    def test_validation_and_derived_area(self):
        m=seal_network()
        m["edges"][0]["area_m2"]=999
        self.assertAlmostEqual(validate(m)["edges"][0]["area_m2"],math.pi*.12*.00015)
        for field,value in (("tooth_count",0),("tooth_count",2.5),("tooth_count",True),("clearance_m",0),("seal_diameter_m",0),("tooth_clearances_m",[.001])):
            mm=copy.deepcopy(m);mm["edges"][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ModelError):validate(mm)

    def test_sample_sweep_and_vortex_settings_roundtrip(self):
        m=stage2_sample();before=copy.deepcopy(m)
        result=solve(m)
        self.assertEqual(m,before)
        self.assertEqual(result["model"]["vortex_study"]["rpm"],30000)
        out=sweep(m,{"group":"edges","id":"seal","field":"clearance_m"},[.0001,.00015,.0002])
        flows=[next(e["mass_flow_abs"] for e in p["edges"] if e["id"]=="seal") for p in out["points"]]
        self.assertTrue(all(a<b for a,b in zip(flows,flows[1:])))
        self.assertLess(result["summary"]["max_mass_residual_kg_s"],1e-8)

    def test_gap_sweep_cannot_ignore_override(self):
        with self.assertRaisesRegex(ModelError,"個別隙間"):
            sweep(seal_network(gaps=[.00015]*4),{"group":"edges","id":"e","field":"clearance_m"},[.0001,.0002])
        with self.assertRaisesRegex(ModelError,"面積"):
            sweep(seal_network(),{"group":"edges","id":"e","field":"area_m2"},[1e-5,2e-5])


class VortexTests(unittest.TestCase):
    def test_forced_analytic_reference(self):
        out=solve_vortex(vortex_input())
        expected=math.exp((.5*30000*math.pi/30)**2*(.18**2-.08**2)/(2*287.05*600))
        self.assertAlmostEqual(out["pressure_ratio_end_start"],expected,places=12)
        self.assertEqual(out["profile"][0]["static_pressure_pa"],300000)
        self.assertTrue(all(p["static_temperature_k"]==600 for p in out["profile"]))

    def test_free_angular_momentum_and_differential_equilibrium(self):
        out=solve_vortex(vortex_input(kind="free",points=101))
        points=out["profile"]
        momentum=[p["radius_m"]*p["tangential_velocity_m_s"] for p in points]
        self.assertLess(max(momentum)-min(momentum),1e-12)
        # Finite difference check against dp/dr = rho*Vtheta²/r.
        for i in (20,50,80):
            a,c,b=points[i-1],points[i],points[i+1]
            dpdr=(b["static_pressure_pa"]-a["static_pressure_pa"])/(b["radius_m"]-a["radius_m"])
            rhs=c["density_kg_m3"]*c["tangential_velocity_m_s"]**2/c["radius_m"]
            self.assertLess(abs(dpdr/rhs-1),.001)

    def test_zero_rotation_swirl_and_sign(self):
        for values in ({"rpm":0},{"swirl_ratio":0}):
            o=solve_vortex(vortex_input(**values))
            self.assertEqual(o["pressure_ratio_end_start"],1)
            self.assertEqual(o["profile"][-1]["total_temperature_k"],600)
        a=solve_vortex(vortex_input(rpm=30000))
        b=solve_vortex(vortex_input(rpm=-30000))
        self.assertEqual(a["pressure_ratio_end_start"],b["pressure_ratio_end_start"])
        self.assertEqual(a["profile"][-1]["tangential_velocity_m_s"],-b["profile"][-1]["tangential_velocity_m_s"])

    def test_radial_reverse_total_temperature_and_rotation_squared(self):
        a=solve_vortex(vortex_input())
        b=solve_vortex(vortex_input(start_radius_m=.18,end_radius_m=.08))
        c=solve_vortex(vortex_input(rpm=15000))
        self.assertAlmostEqual(a["pressure_ratio_end_start"]*b["pressure_ratio_end_start"],1,places=12)
        self.assertAlmostEqual(math.log(a["pressure_ratio_end_start"]),4*math.log(c["pressure_ratio_end_start"]),places=12)
        p=a["profile"][-1];cp=1.4*287.05/.4
        self.assertAlmostEqual(p["total_temperature_k"],600+p["tangential_velocity_m_s"]**2/(2*cp),places=10)

    def test_invalid_inputs_and_immutable(self):
        raw=vortex_input();old=copy.deepcopy(raw);solve_vortex(raw);self.assertEqual(raw,old)
        for field,value in (("rpm",float("nan")),("start_radius_m",0),("points",True),("points",1),("end_radius_m",.08),("kind","unknown")):
            v=vortex_input(**{field:value})
            with self.subTest(field=field),self.assertRaises(ModelError):solve_vortex(v)

    def test_vortex_settings_do_not_change_network_results(self):
        m=stage2_sample();a=solve(m)
        m["vortex_study"]["rpm"]=60000
        b=solve(m)
        self.assertEqual(a["summary"],b["summary"])


if __name__=="__main__":unittest.main()
