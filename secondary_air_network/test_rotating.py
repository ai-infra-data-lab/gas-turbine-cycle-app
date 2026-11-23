import copy
import math
import unittest
from engine import solve,sweep,cp_of
from model import ModelError,rotating_sample,validate
from rotating import kinetic
from test_engine import network,pipe_model,GAS


def radial_network(rpm=30000,pu=400000,pd=300000):
    m=rotating_sample()
    m["nodes"]=[m["nodes"][0],m["nodes"][-1]]
    for n in m["nodes"]:n["rpm"]=rpm
    m["nodes"][0]["pressure_pa"]=pu
    m["nodes"][1]["pressure_pa"]=pd
    m["edges"]=[dict(m["edges"][0],to="cool_out")]
    return m


class RotatingTests(unittest.TestCase):
    def test_outward_pumps_above_source_and_conserves_energy(self):
        out=solve(rotating_sample());r=out["edges"][0]
        a,b=out["nodes"][:2];cp=cp_of(GAS)
        self.assertGreater(b["pressure_pa"],a["pressure_pa"])
        delta=(30000*math.pi/30)**2*(.18**2-.08**2)
        self.assertAlmostEqual(b["temperature_k"]-a["temperature_k"],delta/(2*cp),places=6)
        self.assertAlmostEqual(r["shaft_work_w"],r["mass_flow_abs"]*delta,places=8)
        self.assertAlmostEqual(b["absolute_total_temperature_k"]-a["absolute_total_temperature_k"],delta/cp,places=6)
        self.assertLess(abs(out["summary"]["global_energy_residual_w"]),.01)
        self.assertLess(abs(r["rothalpy_residual_j_kg"]),1e-7)

    def test_zero_rotation_equals_stationary_pipe(self):
        m=radial_network(0,pd=350000)
        r=solve(m)["edges"][0]
        stationary=copy.deepcopy(m)
        for n in stationary["nodes"]:n["frame"]="stationary"
        stationary["edges"][0]["kind"]="pipe"
        s=solve(stationary)["edges"][0]
        self.assertAlmostEqual(r["mass_flow_abs"],s["mass_flow_abs"],delta=1e-8)
        self.assertEqual(r["shaft_work_w"],0)

    def test_equal_radius_has_zero_work(self):
        m=radial_network(pd=350000)
        m["nodes"][1]["radius_m"]=.08
        r=solve(m)["edges"][0]
        self.assertEqual(r["shaft_work_w"],0)
        self.assertAlmostEqual(r["outlet_total_temperature_k"],550,places=9)

    def test_inward_reverse_flow_extracts_work(self):
        m=radial_network(pu=100000,pd=900000)
        # Boundary temperatures deliberately have equal rothalpy.
        cp=cp_of(GAS)
        m["nodes"][1]["temperature_k"]=550+(kinetic(m["nodes"][1])-kinetic(m["nodes"][0]))/cp
        r=solve(m)["edges"][0]
        self.assertTrue(r["reversed"])
        self.assertLess(r["shaft_work_w"],0)
        self.assertAlmostEqual(r["outlet_total_temperature_k"],550,places=7)
        self.assertLess(abs(r["rotating_energy_residual_w"]),1e-7)

    def test_rotation_sign_keeps_work_and_reverses_torque(self):
        a=solve(radial_network())["edges"][0]
        b=solve(radial_network(-30000))["edges"][0]
        self.assertAlmostEqual(a["mass_flow_abs"],b["mass_flow_abs"],places=10)
        self.assertAlmostEqual(a["shaft_work_w"],b["shaft_work_w"],places=8)
        self.assertAlmostEqual(a["torque_nm"],-b["torque_nm"],places=9)

    def test_inviscid_isentropic_reference(self):
        # At a given mass the exact solution preserves entropy and rothalpy.
        from engine import _inlet
        m=validate(radial_network())
        e=m["edges"][0];e.update(friction_factor=0,k=0,k_reverse=0)
        r=solve(m)["edges"][0]
        cp=cp_of(GAS);g=1.4
        t0out=550+(kinetic(m["nodes"][1])-kinetic(m["nodes"][0]))/cp
        p0out=400000*(t0out/550)**(g/(g-1))
        _,reference_pd=_inlet(.03,p0out,t0out,e["area_m2"],GAS)
        m["nodes"][1]["pressure_pa"]=reference_pd
        r=solve(m)["edges"][0]
        w,pout=_inlet(r["mass_flow_abs"],p0out,t0out,e["area_m2"],GAS)
        self.assertFalse(r["choked"])
        self.assertAlmostEqual(pout,reference_pd,delta=.02)
        self.assertAlmostEqual(r["mass_flow_abs"],.03,delta=1e-8)
        self.assertAlmostEqual(r["outlet_total_temperature_k"],t0out,places=8)

    def test_adiabatic_hydrostatic_zero_flow(self):
        m=radial_network()
        cp=cp_of(GAS)
        tout=550+(kinetic(m["nodes"][1])-kinetic(m["nodes"][0]))/cp
        m["nodes"][1]["temperature_k"]=tout
        m["nodes"][1]["pressure_pa"]=400000*(tout/550)**3.5
        r=solve(m)["edges"][0]
        self.assertEqual(r["mass_flow_abs"],0)
        self.assertEqual(r["shaft_work_w"],0)

    def test_grid_refinement(self):
        m=radial_network(pd=650000)
        values=[]
        for cells in (48,96,192):
            m["edges"][0]["cells"]=cells
            values.append(solve(m)["edges"][0]["mass_flow_abs"])
        self.assertLess(abs(values[-1]/values[-2]-1),.003)
        self.assertLess(abs(values[-1]-values[-2]),abs(values[1]-values[0]))

    def test_co_rotating_orifice_absolute_energy(self):
        m=network()
        for n in m["nodes"]:n.update(frame="rotating",rpm=30000,radius_m=.1)
        out=solve(m);cp=cp_of(GAS)
        self.assertGreater(out["nodes"][0]["absolute_total_temperature_k"],550)
        self.assertAlmostEqual(out["edges"][0]["outlet_absolute_total_temperature_k"],550+kinetic(m["nodes"][0])/cp)
        self.assertLess(abs(out["summary"]["global_energy_residual_w"]),1e-8)

    def test_rotor_sweep_updates_whole_component(self):
        m=rotating_sample();before=copy.deepcopy(m)
        points=sweep(m,{"group":"rotation","id":"rot_in","field":"rpm"},[0,15000,30000])["points"]
        self.assertTrue(all(p["status"]=="converged" for p in points))
        self.assertEqual(m,before)
        self.assertEqual(points[0]["summary"]["shaft_work_w"],0)
        self.assertGreater(points[-1]["summary"]["shaft_work_w"],points[1]["summary"]["shaft_work_w"])
        self.assertTrue(all(n["rpm"]==15000 for n in points[1]["nodes"]))

    def test_heat_at_rotating_mixing_node_is_accounted_separately(self):
        m=rotating_sample();m["nodes"][1]["heat_w"]=100
        out=solve(m)
        self.assertAlmostEqual(out["summary"]["heat_w"],100,places=8)
        self.assertGreater(out["nodes"][1]["temperature_k"],out["edges"][0]["outlet_total_temperature_k"])
        self.assertLess(abs(out["summary"]["global_energy_residual_w"]),.02)

    def test_interface_wrong_radius_and_heat_rejected(self):
        cases=[]
        m=radial_network();m["nodes"][1]["rpm"]=15000;cases.append(m)
        m=radial_network();m["nodes"][1].update(frame="stationary",rpm=0);cases.append(m)
        m=radial_network();m["edges"][0]["kind"]="pipe";cases.append(m)
        m=radial_network();m["edges"][0]["heat_w"]=100;cases.append(m)
        m=radial_network();m["edges"][0]["length_m"]=.01;cases.append(m)
        for c in cases:
            with self.assertRaises(ModelError):validate(c)

    def test_incompatible_boundary_thermal_drives_are_rejected(self):
        from engine import CalculationError
        # Do not silently report a zero flow in an unresolved thermal drive band.
        for pd,tout in ((720000,1000),(960000,500)):
            m=radial_network(pd=pd);m["nodes"][1]["temperature_k"]=tout
            with self.assertRaises(CalculationError):solve(m)


if __name__=="__main__":unittest.main()
