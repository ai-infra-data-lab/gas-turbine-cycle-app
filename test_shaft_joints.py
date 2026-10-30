import copy
import json
import math
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import shaft_joints as j
from engine import run_case as cycle_case
from model import default_input as cycle_input
from joint_store import JointStore
from server import make_server,TOKEN


class JointEngineTests(unittest.TestCase):
    def test_spline_hand_calculation(self):
        r=j.analyze(j.default_input())
        self.assertAlmostEqual(r['design_torque_n_m'],750)
        self.assertAlmostEqual(r['tangential_force_n'],25000)
        self.assertAlmostEqual(r['effective_tooth_count'],12)
        self.assertAlmostEqual(r['tooth_peak_tangential_force_n'],2500)
        checks={c['key']:c for c in r['checks']}
        self.assertAlmostEqual(checks['bearing']['demand']/1e6,41.6666666667)
        self.assertAlmostEqual(checks['root_bending_a']['demand']/1e6,83.3333333333)
        self.assertAlmostEqual(checks['root_shear_a']['demand']/1e6,27.7777777778)
        self.assertAlmostEqual(checks['bearing']['utilization'],41.6666666667*1.5/250)

    def test_power_and_reverse_torque_same_static_magnitude(self):
        a=j.default_input(); b=copy.deepcopy(a); b['load']['mode']='power'
        self.assertAlmostEqual(j.nominal_torque(b),500)
        r=j.analyze(a); p=j.analyze(b)
        for x,y in zip(r['checks'],p['checks']): self.assertAlmostEqual(x['demand'],y['demand'],places=5)
        a['load']['torque_n_m']=-500
        self.assertEqual(j.analyze(a)['checks'],r['checks'])

    def test_shaft_annulus_and_hub_lame(self):
        x=j.default_input(); r=j.analyze(x); g=x['geometry']
        tau=16*750*.05/(math.pi*(.05**4-.02**4))*1.3
        self.assertAlmostEqual(r['shaft_shear_stress_pa'],tau,places=6)
        radial=25000*math.tan(math.pi/6)
        pressure=radial/(math.pi*.066*.03)
        hoop=pressure*(.1**2+.066**2)/(.1**2-.066**2)
        self.assertAlmostEqual(r['hub_equivalent_internal_pressure_pa'],pressure)
        self.assertAlmostEqual(next(c['demand'] for c in r['checks'] if c['key']=='hub_hoop'),hoop)
        x['geometry']['shaft_inner_m']=0
        self.assertLess(j.analyze(x)['shaft_shear_stress_pa'],tau)

    def test_load_sharing_and_length_scaling(self):
        x=j.default_input(); a=j.analyze(x)
        x['factors']['engaged_fraction']=1
        b=j.analyze(x)
        self.assertAlmostEqual(b['roots'][0]['bending_pa'],a['roots'][0]['bending_pa']/2)
        x=j.default_input(); x['geometry']['engagement_length_m']*=2; b=j.analyze(x)
        self.assertAlmostEqual(b['roots'][0]['shear_pa'],a['roots'][0]['shear_pa']/2)
        self.assertEqual(b['shaft_shear_stress_pa'],a['shaft_shear_stress_pa'])

    def test_minimum_length_and_preload_sizing(self):
        x=j.default_input(); r=j.analyze(x)
        self.assertAlmostEqual(r['minimum_engagement_length_m'],.0125)
        x['geometry']['engagement_length_m']=r['minimum_engagement_length_m']
        self.assertEqual(j.analyze(x)['status'],'within_preliminary_limits')
        x=j.default_input('curvic'); r=j.analyze(x)
        x['clamp']['preload_n']=r['minimum_assembly_preload_n']
        sized=j.analyze(x)
        check=next(c for c in sized['checks'] if c['key']=='clamp_reserve')
        self.assertAlmostEqual(check['utilization'],1)
        self.assertTrue(check['passed'])

    def test_curvic_preload_wedge_annular_opening_and_bolt(self):
        x=j.default_input('curvic'); r=j.analyze(x)
        ft=750/.0425
        self.assertAlmostEqual(r['separating_force_torque_n'],ft*math.tan(math.pi/6))
        self.assertAlmostEqual(r['retained_preload_min_n'],59800)
        self.assertAlmostEqual(r['assembly_preload_max_n'],83000)
        area=math.pi*(.05**2-.035**2); inertia=math.pi*(.05**4-.035**4)/4
        opening_moment=75*area*.05/inertia
        expected=ft*math.tan(math.pi/6)+1500+opening_moment
        self.assertAlmostEqual(r['separating_force_total_n'],expected)
        self.assertAlmostEqual(r['required_retained_preload_n'],expected*1.75)
        self.assertAlmostEqual(r['minimum_annular_pressure_pa'],(59800-1500-ft*math.tan(math.pi/6))/area-75*.05/inertia)
        bolt=(83000+.2*expected)/.00025
        self.assertAlmostEqual(next(c['demand'] for c in r['checks'] if c['key']=='bolt_tension'),bolt)

    def test_preload_increases_contact_stress_and_opening_reserve(self):
        x=j.default_input('curvic'); a=j.analyze(x); x['clamp']['preload_n']*=1.2; b=j.analyze(x)
        bearing=lambda r:next(c['demand'] for c in r['checks'] if c['key']=='bearing')
        self.assertGreater(bearing(b),bearing(a))
        self.assertGreater(b['minimum_annular_pressure_pa'],a['minimum_annular_pressure_pa'])

    def test_curvic_separation_reports_failure_and_qualification(self):
        x=j.default_input('curvic'); x['clamp']['preload_n']=1000
        r=j.analyze(x)
        self.assertFalse(r['contact_model_valid']); self.assertTrue(r['flank_lift_predicted'])
        self.assertEqual(r['status'],'exceeds_preliminary_limits')
        self.assertLess(r['minimum_annular_pressure_pa'],0)
        self.assertIn('離間',r['qualifications'][-1])

    def test_zero_preload_cannot_be_declared_within_limits(self):
        x=j.default_input('curvic'); x['load'].update(torque_n_m=0,axial_force_n=0,bending_moment_n_m=0)
        x['clamp']['thermal_change_n']=-1e6
        r=j.analyze(x)
        self.assertEqual(r['status'],'exceeds_preliminary_limits')
        self.assertEqual(r['allowable_nominal_torque_n_m'],0)

    def test_capacity_boundary_for_both_joint_types(self):
        for kind in ('spline','curvic'):
            x=j.default_input(kind); r=j.analyze(x)
            cap=r['allowable_nominal_torque_n_m']
            self.assertIsNotNone(cap); self.assertGreater(cap,0)
            x['load'].update(mode='torque',torque_n_m=cap*.999999)
            self.assertEqual(j.analyze(x)['status'],'within_preliminary_limits')
            x['load']['torque_n_m']=cap*1.000001
            self.assertEqual(j.analyze(x)['status'],'exceeds_preliminary_limits')

    def test_zero_load_null_safety_and_finite_serialization(self):
        x=j.default_input(); x['load'].update(torque_n_m=0,axial_force_n=0,bending_moment_n_m=0)
        r=j.analyze(x)
        self.assertTrue(all(c['safety_factor'] is None for c in r['checks']))
        json.dumps(r,allow_nan=False)

    def test_input_immutability_hash_context_and_roundtrip(self):
        for kind in ('spline','curvic'):
            x=j.default_input(kind); original=copy.deepcopy(x); h=j.input_hash(x)
            case=j.run_case(x)
            self.assertEqual(x,original); self.assertEqual(case['input_hash'],h)
            self.assertEqual(j.read_input_file(json.loads(json.dumps(case))),j.validate_input(x))
            x['material']['basis']='別の根拠'; self.assertNotEqual(j.input_hash(x),h)
        x=j.default_input(); x['context']['component_id']='not-a-uuid'
        with self.assertRaises(ValueError):j.validate_input(x)

    def test_validation_boundaries_and_unknown_fields(self):
        invalid=[('load','torque_n_m',True),('load','speed_rpm',0),('load','power_w',math.inf),('load','service_factor',.5),
                 ('geometry','tooth_count',24.),('geometry','shaft_inner_m',.05),('geometry','hub_outer_m',.06),
                 ('geometry','root_thickness_a_m',.1),('factors','engaged_fraction',0),('material','contact_allowable_pa',0)]
        for group,key,value in invalid:
            x=j.default_input();x[group][key]=value
            with self.assertRaises(ValueError,msg=(group,key)):j.validate_input(x)
        x=j.default_input();x['unknown']=1
        with self.assertRaises(ValueError):j.validate_input(x)
        x=j.default_input('curvic');x['geometry']['inner_diameter_m']=.1
        with self.assertRaises(ValueError):j.validate_input(x)

    def test_material_temperature_is_recorded_not_automatically_corrected(self):
        x=j.default_input(); a=j.analyze(x); x['material']['temperature_k']=1000
        self.assertEqual(a['checks'],j.analyze(x)['checks'])

    def test_cycle_power_requires_explicit_location(self):
        case=cycle_case(cycle_input())
        for position,key in [('compressor','compressor_power_w'),('turbine','turbine_shaft_power_w'),('net_shaft','net_shaft_power_w')]:
            data=j.cycle_load(case,position)
            self.assertEqual(data['power_w'],case['result']['metrics'][key])
            self.assertIn(case['run_id'],data['source_note'])
        with self.assertRaises(ValueError):j.cycle_load(case,'unknown')

    def test_joint_store_preserves_immutable_cases_across_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'joints.sqlite3'; store=JointStore(path); case=j.run_case(j.default_input())
            store.save(case); saved=JointStore(path).get(case['run_id'])
            self.assertEqual(saved,case); self.assertEqual(len(store.list()),1)


class JointAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); cls.server=make_server(Path(cls.temp.name)/'cycle.sqlite3')
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,data=None,token=TOKEN):
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-Cycle-Token':token})
        try:
            with urllib.request.urlopen(req) as r:return r.status,r.read()
        except urllib.error.HTTPError as e:return e.code,e.read()

    def test_assets_and_schema(self):
        for path in ('/shaft-joints','/joints.js','/joints.css','/shaft-joints/guide','/api/joints/schema'):
            self.assertEqual(self.request(path)[0],200,path)

    def test_save_recalculates_and_separates_databases(self):
        inp=j.default_input(); status,body=self.request('/api/joints/save',{'input':inp,'result':{'fabricated':True}})
        self.assertEqual(status,200); case=json.loads(body)
        self.assertNotIn('fabricated',case['result'])
        self.assertEqual(json.loads(self.request('/api/joints/case?id='+case['run_id'])[1]),case)
        self.assertEqual(self.request('/api/case?id='+case['run_id'])[0],404)
        imported=json.loads(self.request('/api/joints/import',{'document':case})[1])
        self.assertEqual(imported['input'],case['input'])
        self.assertNotIn('result',imported)
        self.assertGreaterEqual(len(json.loads(self.request('/api/joints/cases')[1])),1)

    def test_invalid_input_and_token(self):
        x=j.default_input();x['geometry']['tooth_count']=0
        self.assertEqual(self.request('/api/joints/calculate',{'input':x})[0],400)
        self.assertEqual(self.request('/api/joints/save',{'input':j.default_input()},token='bad')[0],403)
        self.assertEqual(self.request('/api/joints/case?id=invalid')[0],404)

    def test_cycle_load_endpoint_and_independent_case_contract(self):
        status,body=self.request('/api/save',{'input':cycle_input()});self.assertEqual(status,200)
        case=json.loads(body)
        status,body=self.request('/api/joints/cycle-load',{'case_id':case['run_id'],'position':'compressor'})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['power_w'],case['result']['metrics']['compressor_power_w'])
        self.assertEqual(self.request('/api/joints/import',{'document':case})[0],400)


if __name__=='__main__':unittest.main()
