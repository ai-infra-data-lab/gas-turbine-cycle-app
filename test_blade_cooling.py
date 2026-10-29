import copy
import json
import math
import unittest

from model import InputError, validate_input, input_hash
from engine import analyze, run_case
from thermo import air, ConstantGas
from blade_cooling import pump, heat_cell, thermal_defaults, pumping_defaults
from test_cooling import sample
from server import read_input_file


def blade_sample():
    x=sample()
    c=x['cooling']['circuits'][1]
    c.update(name='動翼内部冷却',injection='after_rotor')
    c['thermal'].update(enabled=True,gas_area_m2=.08,coolant_area_m2=.10,metal_limit_k=800.)
    c['pumping'].update(enabled=True,inlet_radius_m=.015,outlet_radius_m=.04)
    return x


class BladeCoolingTests(unittest.TestCase):
    def test_euler_work_and_pressure_with_nasa_no_half_factor(self):
        a=air(); p=pumping_defaults(); p['enabled']=True
        t,pt,w=pump(a,500,2e5,30000,p)
        expected=(30000*2*math.pi/60)**2*(.07**2-.025**2)
        self.assertAlmostEqual(w,expected,places=8)
        self.assertAlmostEqual(a.h(t)-a.h(500),w,places=5)
        self.assertGreater(pt,2e5)
        self.assertGreaterEqual(a.entropy_thermal(t)-a.entropy_thermal(500)-a.r*math.log(pt/2e5),-1e-6)
        p['inlet_radius_m']=p['outlet_radius_m']
        self.assertAlmostEqual(pump(a,500,2e5,30000,p)[2],0)
        p['inlet_swirl_ratio']=0
        self.assertGreater(pump(a,500,2e5,30000,p)[2],0)

    def test_heat_cell_analytic_parallel_flow_and_wall_resistances(self):
        # Constant properties give a separate closed-form parallel-flow solution.
        a=air(); cfg=thermal_defaults(); cp=a.cp(600)
        gas=ConstantGas(cp,a.r)
        out=heat_cell(gas,1,1100,.1,500,cfg,coolant=gas)
        ua=1/out['thermal_resistance_k_w']; inv_capacity=1/cp+1/(.1*cp)
        expected=(1-math.exp(-ua*inv_capacity))/inv_capacity*600
        self.assertAlmostEqual(out['heat_w'],expected,places=4)
        self.assertGreater(out['heat_w'],0)
        self.assertGreater(out['coolant_outlet_temperature_k'],500)
        self.assertLess(out['gas_outlet_temperature_k'],1100)
        self.assertLess(abs(out['energy_residual_w']),1e-4)
        self.assertGreaterEqual(out['heat_entropy_generation_w_k'],-1e-6)
        for wall in (out['inlet_walls'],out['outlet_walls']):
            self.assertGreater(wall['coating_outer_temperature_k'],wall['metal_outer_temperature_k'])
            self.assertGreater(wall['metal_outer_temperature_k'],wall['metal_inner_temperature_k'])
        c0=copy.deepcopy(cfg); c0['tbc_thickness_m']=0
        self.assertGreater(heat_cell(gas,1,1100,.1,500,c0)['metal_max_temperature_k'],out['metal_max_temperature_k'])
        film=copy.deepcopy(cfg); film['film_effectiveness']=.4
        self.assertLess(heat_cell(gas,1,1100,.1,500,film)['metal_max_temperature_k'],out['metal_max_temperature_k'])

    def test_fixed_flow_internal_heat_cancels_in_cycle(self):
        x=blade_sample(); x['cooling']['circuits'][1]['pumping']['enabled']=False
        r=analyze(x); t=r['cooling']['routes'][1]['thermal']
        self.assertGreater(t['heat_w'],0)
        x['cooling']['circuits'][1]['thermal']['enabled']=False
        old=analyze(x)
        self.assertEqual(r['metrics']['net_electric_power_w'],old['metrics']['net_electric_power_w'])
        self.assertLess(abs(r['energy_balance']['shaft_balance_residual_w']),1e-4)

    def test_pumping_work_counted_once_and_total_balance(self):
        x=blade_sample(); r=analyze(x); m=r['metrics']
        p=sum(c['pumping_power_w'] for c in r['cooling']['routes'])
        self.assertGreater(p,0)
        self.assertAlmostEqual(m['net_shaft_power_w'],m['turbine_gas_power_w']*.98-m['compressor_power_w']-p,places=7)
        self.assertLess(abs(r['energy_balance']['shaft_balance_residual_w']),1e-4)
        self.assertLess(abs(r['energy_balance']['electric_balance_residual_w']),1e-4)
        self.assertTrue(all(e.get('post_heat_mixing_entropy_generation_w_k',0)>=-1e-5 for e in r['stage_results']['turbine']['elements']))

    def test_target_power_uses_actual_area_and_flow(self):
        x=blade_sample(); x['mode']='target_power'; x['parameters']['target_net_power_w']=100000
        r=analyze(x)
        self.assertAlmostEqual(r['metrics']['net_electric_power_w'],100000,places=6)
        x['mode']='airflow'; x['parameters']['air_mass_flow_kg_s']=r['metrics']['air_mass_flow_kg_s']
        forward=analyze(x)
        self.assertAlmostEqual(r['cooling']['routes'][1]['thermal']['metal_max_temperature_k'],forward['cooling']['routes'][1]['thermal']['metal_max_temperature_k'],places=6)

    def test_metal_limit_inverse_and_impossible_bound(self):
        x=blade_sample(); c=x['cooling']['circuits'][1]; c['thermal'].update(mode='metal_limit',metal_limit_k=730.)
        r=analyze(x); f=r['cooling']['flow_sizing']['fraction']; t=r['cooling']['routes'][1]['thermal']
        self.assertEqual(r['cooling']['flow_sizing']['status'],'metal_limit_solved')
        self.assertLessEqual(t['metal_max_temperature_k'],730.01)
        self.assertGreaterEqual(f,c['thermal']['min_fraction'])
        c['thermal']['mode']='fixed_flow'; c['fraction']=f
        self.assertAlmostEqual(analyze(x)['metrics']['net_electric_power_w'],r['metrics']['net_electric_power_w'],places=5)
        c['thermal'].update(mode='metal_limit',metal_limit_k=300.)
        with self.assertRaisesRegex(InputError,'上限'): analyze(x)

    def test_inverse_with_target_power_and_first_rotor_temperature(self):
        x=blade_sample(); x['mode']='target_power'; x['cooling']['temperature_basis']='first_rotor_inlet'
        x['cooling']['circuits'][1]['thermal'].update(mode='metal_limit',metal_limit_k=730.)
        r=analyze(x)
        self.assertAlmostEqual(r['metrics']['net_electric_power_w'],100000,places=5)
        self.assertAlmostEqual(r['metrics']['first_rotor_inlet_temperature_k'],1123,places=4)
        self.assertLessEqual(r['cooling']['routes'][1]['thermal']['metal_max_temperature_k'],730.01)
        self.assertLess(abs(r['energy_balance']['electric_balance_residual_w']),1e-4)

    def test_schema_five_migration_and_input_immutability(self):
        x=sample(); x['schema_version']=5
        for c in x['cooling']['circuits']:
            del c['thermal']; del c['pumping']
        migrated=validate_input(x)
        self.assertFalse(migrated['cooling']['circuits'][0]['thermal']['enabled'])
        current=blade_sample(); saved=copy.deepcopy(current); h=input_hash(current)
        case=run_case(current)
        self.assertEqual(current,saved); self.assertEqual(input_hash(current),h)
        self.assertEqual(read_input_file(json.loads(json.dumps(case))),validate_input(current))

    def test_validation_and_unavailable_cooling(self):
        for key,value in [('gas_area_m2',0),('film_effectiveness',1),('metal_conductivity_w_m_k',True),('mode','unknown')]:
            x=blade_sample(); x['cooling']['circuits'][1]['thermal'][key]=value
            with self.assertRaises(InputError): validate_input(x)
        x=blade_sample(); x['cooling']['circuits'][1]['injection']='before_rotor'
        with self.assertRaisesRegex(InputError,'ロータ後'): validate_input(x)
        x=blade_sample(); x['cooling']['circuits'][1]['pumping']['inlet_radius_m']=1
        with self.assertRaises(InputError): validate_input(x)
        with self.assertRaises(InputError): heat_cell(air(),1,400,.03,500,thermal_defaults())


if __name__=='__main__': unittest.main()
