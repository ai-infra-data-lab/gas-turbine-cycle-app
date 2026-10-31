"""Numerical comparisons, independence rules, CSV integrity and API persistence."""
import copy
import hashlib
import json
import math
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import evaluation as e
from model import default_input
from engine import run_case
from server import make_server, TOKEN


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample=e.example_input()

    def fixture(self):
        v=copy.deepcopy(self.sample)
        v['baseline_input']=default_input('constant_cp')
        v['csv_text']='time_s,T_C\n0,100\n1,104\n2,NaN\n3,\n4,102\n'
        v['start_s']=0;v['end_s']=4
        v['channels']=[{'column':'T_C','role':'validation','unit':'degC','definition':'total_temperature',
                        'target':'station:2:total_temperature_k','location':'圧縮機出口・全温',
                        'measurement_u':3,'model_u':4,'absolute_tolerance':10,'relative_tolerance_pct':None}]
        return v

    def test_statistics_and_uncertainty_hand_values(self):
        v=self.fixture();r=e.run_case(v)['result']['comparisons'][0]
        self.assertAlmostEqual(r['mean_si'],375.15)
        self.assertAlmostEqual(r['stddev_si'],2)
        self.assertEqual((r['n'],r['missing_or_invalid']),(3,2))
        self.assertEqual(r['combined_u_si'],5)
        self.assertAlmostEqual(r['difference_si'],375.15-r['predicted_si'])
        self.assertAlmostEqual(r['normalized_difference'],r['difference_si']/5)
        self.assertEqual(r['tolerance_si'],10)
        # Independent straight-line regression on t=[0,1,4], T=[100,104,102].
        self.assertAlmostEqual(r['slope_si_per_s'],3/13)

    def test_gauge_pressure_and_uncertainty_scaling(self):
        v=self.fixture();v['csv_text']='time_s,Pg\n0,0\n1,10\n'
        v['channels'][0].update(column='Pg',unit='kPa(g)',definition='absolute_total_pressure',
                               target='station:2:total_pressure_pa',gauge_reference_pa=100000)
        r=e.run_case(v)['result']['comparisons'][0]
        self.assertEqual(r['mean_si'],105000);self.assertEqual(r['combined_u_si'],5000)
        v['channels'][0]['gauge_reference_pa']=None
        with self.assertRaises(ValueError):e.run_case(v)

    def test_missing_uncertainty_is_not_zero(self):
        v=self.fixture();v['channels'][0]['model_u']=None
        r=e.run_case(v)['result']['comparisons'][0]
        self.assertIsNone(r['combined_u_si']);self.assertIsNone(r['within_uncertainty'])
        v['channels'][0].update(model_u=0,measurement_u=0)
        r=e.run_case(v)['result']['comparisons'][0]
        self.assertIsNone(r['normalized_difference']);self.assertFalse(r['within_uncertainty'])

    def test_no_tolerance_means_no_judgement(self):
        v=self.fixture();v['channels'][0]['absolute_tolerance']=None
        self.assertIsNone(e.run_case(v)['result']['comparisons'][0]['within_tolerance'])

    def test_condition_recalculation_and_duplicate_constraint_exclusion(self):
        v=self.fixture();v['csv_text']='time_s,a,b\n0,20,20\n1,20,20\n'
        ch=v['channels'][0];ch.update(column='a',role='condition',parameter='ambient_temperature_k')
        v['channels'].append({**ch,'column':'b','role':'validation','target':'station:1:total_temperature_k'})
        doc=e.run_case(v);r=doc['result']
        self.assertEqual(r['cycle_case']['input']['parameters']['ambient_temperature_k'],293.15)
        self.assertEqual(r['comparisons'][1]['status'],'reference_only')
        self.assertFalse(r['comparisons'][1]['independent'])

    def test_calibration_excludes_independent_validation(self):
        v=copy.deepcopy(self.sample);v['channels'][3]['role']='calibration'
        self.assertFalse(any(x['independent'] for x in e.run_case(v)['result']['comparisons']))
        v=self.fixture();v['calibrated_model']=True
        with self.assertRaises(ValueError):e.run_case(v)
        v['calibration_note']='同一試験で効率調整'
        self.assertFalse(e.run_case(v)['result']['comparisons'][0]['independent'])

    def test_one_sample_has_no_scatter_or_trend(self):
        v=self.fixture();v['start_s']=v['end_s']=1
        r=e.run_case(v)['result']['comparisons'][0]
        self.assertEqual(r['n'],1);self.assertIsNone(r['stddev_si']);self.assertIsNone(r['slope_si_per_s'])

    def test_csv_structure_and_time_errors(self):
        for s in ('','t,t\n1,2','t,x\n1,2,3','t,x\n2,1\n1,1','t,x\n1,1\n1,2','t,x\nNaN,1','t,x\n,1'):
            with self.assertRaises(ValueError,msg=s):e.parse_csv(s,'t')

    def test_negative_physics_and_all_missing_rejected(self):
        v=self.fixture()
        for s in ('time_s,T_C\n0,-274\n1,500','time_s,T_C\n0,\n1,foo'):
            v['csv_text']=s
            with self.assertRaises(ValueError):e.run_case(v)

    def test_unit_definition_target_and_mode_guards(self):
        for key,value in [('definition','static_temperature'),('target','station:2:total_pressure_pa'),('unit','nonsense'),('measurement_u',math.inf)]:
            v=self.fixture();v['channels'][0][key]=value
            with self.assertRaises(ValueError,msg=key):e.run_case(v)
        v=self.fixture();v['baseline_input']['mode']='target_power'
        with self.assertRaises(ValueError):e.run_case(v)

    def test_original_crlf_bom_preserved_and_roundtrip_ignores_result(self):
        v=self.fixture();v['csv_text']='\ufeff'+v['csv_text'].replace('\n','\r\n');before=copy.deepcopy(v)
        case=e.run_case(v);self.assertEqual(v,before)
        self.assertEqual(case['input']['csv_text'],before['csv_text'])
        self.assertEqual(case['result']['source_sha256'],hashlib.sha256(v['csv_text'].encode()).hexdigest())
        case['result']={'fabricated':True}
        restored=e.read_input_file(json.loads(json.dumps(case)))
        self.assertNotIn('fabricated',e.run_case(restored)['result'])

    def test_stage_catalog_and_sample_expected_differences(self):
        r=e.run_case(self.sample)['result'];rows={x['column']:x for x in r['comparisons']}
        for key,delta in [('T2_C',3),('T4_C',8),('P2_kPa',-1000),('electric_kW',-2000)]:
            self.assertAlmostEqual(rows[key]['difference_si'],delta)
        self.assertEqual(rows['fuel_kg_h']['missing_or_invalid'],1)
        self.assertIn('compressor:0:total_temperature_k',{x['key'] for x in e.catalog(r['cycle_case']['result'])})


class EvaluationAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.server=make_server(Path(cls.temp.name)/'cycle.sqlite3')
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,data=None,token=TOKEN):
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,
                                  headers={'Content-Type':'application/json','X-Cycle-Token':token})
        try:
            with urllib.request.urlopen(req) as r:return r.status,r.read()
        except urllib.error.HTTPError as exc:return exc.code,exc.read()

    def test_assets(self):
        for path in ('/test-evaluation','/evaluation.js','/evaluation.css','/test-evaluation/guide','/api/evaluation/schema'):
            self.assertEqual(self.request(path)[0],200,path)

    def test_save_recomputes_and_separate_db_restore(self):
        v=e.example_input();code,body=self.request('/api/evaluation/save',{'input':v,'result':{'fabricated':True}})
        self.assertEqual(code,200,body);case=json.loads(body)
        self.assertNotIn('fabricated',case['result'])
        self.assertEqual(json.loads(self.request('/api/evaluation/case?id='+case['run_id'])[1]),case)
        self.assertEqual(self.request('/api/case?id='+case['run_id'])[0],404)
        self.assertEqual(len(self.server.store.list()),0)
        self.assertEqual(len(self.server.joint_store.list()),0)
        self.assertNotIn('result',json.loads(self.request('/api/evaluation/import',{'document':case})[1]))
        self.assertTrue(Path(self.temp.name,'cycle.test-evaluation.sqlite3').exists())

    def test_invalid_contract_security_and_preview(self):
        v=e.example_input()
        self.assertEqual(self.request('/api/evaluation/calculate',{'input':v},token='wrong')[0],403)
        v['channels'][0]['definition']='static_temperature'
        self.assertEqual(self.request('/api/evaluation/calculate',{'input':v})[0],400)
        self.assertEqual(self.request('/api/evaluation/import',{'document':run_case(default_input())})[0],400)
        code,body=self.request('/api/evaluation/preview',{'csv_text':'t,x\n0,2\n1,3','time_column':'t'})
        self.assertEqual(code,200);self.assertEqual(json.loads(body)['rows'],2)


if __name__=='__main__':unittest.main()
