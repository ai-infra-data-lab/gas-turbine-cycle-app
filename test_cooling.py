import copy
import json
import math
import unittest
from pathlib import Path

from engine import analyze, run_case
from model import default_input, element_stage, cooling_circuit, InputError, validate_input, input_hash
from thermo import air, Mixture, species_properties, DATA, RU
from cooling import absolute_entropy, combine
from server import read_input_file


def sample():
    x=default_input()
    for key in ('compressor','turbine'):
        x['staging'][key].update(count=2,distribution='pressure',weights=[1,1])
        x['elements'][key].update(enabled=True,stages=[element_stage(key),element_stage(key)])
    c=cooling_circuit(); c.update(name='stator',source_stage=2,injection='before_nozzle',fraction=.04,mixed_velocity_m_s=60)
    d=cooling_circuit(); d.update(name='interstage',source_stage=1,target_stage=2,fraction=.03,mixed_velocity_m_s=180)
    x['cooling'].update(enabled=True,circuits=[c,d])
    return x


class CoolingTests(unittest.TestCase):
    def test_zero_bleed_is_exact_legacy_regression(self):
        x=sample()
        for c in x['cooling']['circuits']: c['fraction']=0
        a=analyze(x)
        x['cooling']['enabled']=False
        b=analyze(x)
        self.assertEqual(a['metrics'],b['metrics'])
        self.assertEqual(a['energy_balance'],b['energy_balance'])

    def test_mixed_enthalpy_and_species_mass_are_conserved(self):
        x=sample(); r=analyze(x); ma=r['metrics']['air_mass_flow_kg_s']
        for row in r['stage_results']['turbine']['elements']:
            if row['kind']=='mixing':
                self.assertLess(abs(row['mixing_energy_residual_per_inlet_air_j_kg']),1e-7)
                self.assertAlmostEqual(row['outlet']['mass_flow_kg_s']-row['inlet']['mass_flow_kg_s'],row['coolant_mass_flow_kg_s'])
                self.assertGreaterEqual(row['entropy_generation_w_k'],-1e-6)
                self.assertEqual(row['power_w'],0)
        mg=r['metrics']['gas_mass_flow_kg_s']
        hot=r['cooling']['hot_turbine_mass_flow_kg_s']
        for k in DATA['species']:
            yin=r['thermodynamics']['combustor_product_mass_fractions'].get(k,0)
            yc=air().mass_fractions.get(k,0)
            yout=r['thermodynamics']['product_mass_fractions'].get(k,0)
            self.assertAlmostEqual(hot*yin+ma*.07*yc,mg*yout,places=10)

    def test_compressor_bleed_reduces_only_downstream_stage_mass_and_work(self):
        x=sample(); x['cooling']['circuits']=[x['cooling']['circuits'][1]]
        c=x['cooling']['circuits'][0]
        c.update(source_stage=1,target_stage=2,injection='after_diffuser',mix_pressure_loss_fraction=0)
        a=analyze(x)
        c['source_stage']=2
        b=analyze(x)
        ma=a['metrics']['air_mass_flow_kg_s']
        self.assertAlmostEqual(a['stage_results']['compressor']['stages'][0]['power_w'],b['stage_results']['compressor']['stages'][0]['power_w'])
        dh=b['stage_results']['compressor']['stages'][1]['specific_work_j_kg']
        self.assertAlmostEqual(b['metrics']['compressor_power_w']-a['metrics']['compressor_power_w'],ma*c['fraction']*dh,places=7)
        self.assertAlmostEqual(a['stage_results']['compressor']['stages'][1]['inlet']['mass_flow_kg_s'],ma*(1-c['fraction']))
        self.assertAlmostEqual(b['stage_results']['compressor']['stages'][1]['inlet']['mass_flow_kg_s'],ma)

    def test_late_cooling_does_not_join_earlier_rotor_mass(self):
        x=sample(); x['cooling']['circuits']=[x['cooling']['circuits'][0]]
        c=x['cooling']['circuits'][0]; c.update(target_stage=2,injection='after_diffuser',mix_pressure_loss_fraction=0)
        r=analyze(x)
        hot=r['cooling']['hot_turbine_mass_flow_kg_s']
        for row in r['stage_results']['turbine']['elements']:
            if row['kind']=='rotor': self.assertAlmostEqual(row['inlet']['mass_flow_kg_s'],hot)
        self.assertAlmostEqual(r['stage_results']['turbine']['states'][-1]['mass_flow_kg_s'],r['metrics']['gas_mass_flow_kg_s'])

    def test_first_rotor_temperature_mode_solves_hotter_combustor(self):
        x=sample(); r=analyze(x)
        self.assertLess(r['metrics']['first_rotor_inlet_temperature_k'],1123)
        x['cooling']['temperature_basis']='first_rotor_inlet'
        r=analyze(x)
        self.assertAlmostEqual(r['metrics']['first_rotor_inlet_temperature_k'],1123,places=5)
        self.assertGreater(r['metrics']['combustor_exit_temperature_k'],1123)
        x['cooling']['combustor_exit_limit_k']=1123
        with self.assertRaises(InputError): analyze(x)

    def test_supply_pressure_uses_local_static_pressure_and_margin(self):
        x=sample(); r=analyze(x)
        for row in r['stage_results']['turbine']['elements']:
            if row['kind']=='mixing': self.assertGreater(row['coolant_total_pressure_pa'],row['required_supply_pressure_pa'])
        x['cooling']['circuits'][0]['pressure_loss_fraction']=.5
        with self.assertRaisesRegex(InputError,'供給全圧が不足'): analyze(x)

    def test_external_heat_is_in_signed_energy_balance(self):
        for q in (-25000,25000):
            x=sample(); x['cooling']['circuits'][0]['heat_j_kg']=q
            r=analyze(x); ma=r['metrics']['air_mass_flow_kg_s']
            self.assertAlmostEqual(r['energy_balance']['cooling_external_heat_w'],ma*.04*q)
            self.assertLess(abs(r['energy_balance']['shaft_balance_residual_w']),2e-6)
            self.assertLess(abs(r['energy_balance']['electric_balance_residual_w']),2e-6)

    def test_insufficient_mixing_loss_cannot_reduce_total_entropy(self):
        x=sample(); x['parameters']['turbine_inlet_temperature_k']=900
        x['cooling']['circuits']=[x['cooling']['circuits'][1]]
        c=x['cooling']['circuits'][0]
        c.update(fraction=.2,mix_pressure_loss_fraction=0,required_pressure_margin=0)
        r=analyze(x)
        point=next(e for e in r['stage_results']['turbine']['elements'] if e['kind']=='mixing')
        route=r['cooling']['routes'][0]
        # Equal-temperature streams remove the thermal mixing contribution, exposing
        # an inadmissible rise of the low-pressure coolant to the chosen mixed Pt.
        c['heat_j_kg']=air().h(point['inlet']['total_temperature_k'])-air().h(route['source_total_temperature_k'])
        c['pressure_loss_fraction']=1-point['inlet']['total_pressure_pa']*.92/route['source_total_pressure_pa']
        with self.assertRaisesRegex(InputError,'エントロピーが減少'): analyze(x)
        c['mix_pressure_loss_fraction']=.02
        fixed=analyze(x)
        mix=next(e for e in fixed['stage_results']['turbine']['elements'] if e['kind']=='mixing')
        self.assertGreater(mix['entropy_generation_w_k'],0)

    def test_stage_element_work_sums_and_thermodynamic_efficiency(self):
        r=analyze(sample()); m=r['metrics']
        for key,power in (('compressor','compressor_power_w'),('turbine','turbine_gas_power_w')):
            x=r['stage_results'][key]
            self.assertAlmostEqual(sum(s['power_w'] for s in x['stages']),m[power],places=7)
            self.assertAlmostEqual(sum(e['power_w'] for e in x['elements']),m[power],places=7)
        self.assertIsNone(m['turbine_effective_efficiency'])
        denominator=r['cooling']['turbine_ideal_hot_power_w']+r['cooling']['turbine_ideal_coolant_power_w']
        self.assertAlmostEqual(m['turbine_thermodynamic_efficiency'],m['turbine_gas_power_w']/denominator)
        self.assertLess(abs(r['energy_balance']['mass_balance_residual_kg_s']),1e-12)
        self.assertLess(abs(r['energy_balance']['electric_balance_residual_w']),2e-6)

    def test_overall_compressor_mode_with_intermediate_bleed(self):
        x=sample(); x['elements']['compressor']['enabled']=False
        r=analyze(x)
        self.assertEqual(len(r['stage_results']['compressor']['stages']),2)
        self.assertLess(r['stage_results']['compressor']['states'][-1]['mass_flow_kg_s'],r['metrics']['air_mass_flow_kg_s'])
        self.assertLess(abs(r['energy_balance']['electric_balance_residual_w']),2e-6)

    def test_target_power_roundtrip_immutability_and_hash(self):
        x=sample(); before=copy.deepcopy(x); a=run_case(x)
        self.assertEqual(x,before)
        self.assertEqual(read_input_file(a),validate_input(x))
        h=input_hash(x); x['cooling']['circuits'][0]['fraction']=.05
        self.assertNotEqual(h,input_hash(x))
        x['mode']='target_power'; r=analyze(x)
        self.assertAlmostEqual(r['metrics']['net_electric_power_w'],100000,places=6)
        self.assertAlmostEqual(r['cooling']['routes'][0]['mass_flow_kg_s'],r['metrics']['air_mass_flow_kg_s']*.05)

    def test_schema_four_migrates_with_no_cooling(self):
        raw=json.loads((Path(__file__).parent/'examples/elements_result.json').read_text(encoding='utf-8'))
        x=read_input_file(raw)
        self.assertEqual(x['schema_version'],6)
        self.assertFalse(x['cooling']['enabled'])
        self.assertAlmostEqual(analyze(x)['metrics']['net_electric_power_w'],raw['result']['metrics']['net_electric_power_w'],places=6)

    def test_invalid_cooling_configuration_and_model_requirements(self):
        for key,value in (('source_stage',True),('source_stage',3),('target_stage',3),('fraction',float('nan')),('fraction',.31),('heat_j_kg',float('inf')),('injection','unknown')):
            x=sample(); x['cooling']['circuits'][0][key]=value
            with self.subTest(key=key),self.assertRaises(InputError): analyze(x)
        for mode in ('constant','no_elements'):
            x=sample()
            if mode=='constant': x['thermodynamics']['model']='constant_cp'
            else: x['elements']['turbine']['enabled']=False
            with self.assertRaises(InputError): analyze(x)
        x=sample(); x['cooling']['circuits']*=7
        with self.assertRaises(InputError): analyze(x)
        x=sample()
        for c in x['cooling']['circuits']: c['fraction']=.3
        with self.assertRaises(InputError): analyze(x)


if __name__=='__main__': unittest.main()
