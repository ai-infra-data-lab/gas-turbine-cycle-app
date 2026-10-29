"""Optional independent HP/SP, sensible mixing and absolute mixing entropy checks."""
import argparse
import json
from pathlib import Path
from verify_cantera import phase_for, air, products, DATA
from verify_elements_cantera import reference_state
from model import default_input, element_stage, cooling_circuit
from engine import analyze
from thermo import Mixture


def sample():
    x=default_input()
    for key in ('compressor','turbine'):
        x['staging'][key].update(count=2,distribution='pressure',weights=[1,1])
        x['elements'][key].update(enabled=True,stages=[element_stage(key),element_stage(key)])
    c=cooling_circuit(); c.update(source_stage=2,injection='before_nozzle',fraction=.04,mixed_velocity_m_s=60)
    d=cooling_circuit(); d.update(source_stage=1,target_stage=2,fraction=.03,mixed_velocity_m_s=180)
    x['cooling'].update(enabled=True,circuits=[c,d])
    return x


def h_s(gas,tt,pt):
    phase,scale,href=phase_for(gas)
    phase.TP=tt,pt
    return (phase.enthalpy_mass-href)*scale,phase.entropy_mass*scale


def verify():
    records=[]; nt=nm=0; max_t=max_p=max_s=max_w=0.
    for basis in ('combustor_exit','first_rotor_inlet'):
        for heat in (0.,20000.):
            inp=sample(); inp['cooling']['temperature_basis']=basis
            inp['cooling']['circuits'][0]['heat_j_kg']=heat
            r=analyze(inp); m=r['metrics']; a=air()
            gas=products(m['fuel_mass_flow_kg_s']/m['combustor_air_mass_flow_kg_s'],inp['thermodynamics'])
            first=r['stage_results']['turbine']['states'][0]
            current=reference_state(gas,first['total_temperature_k'],first['total_pressure_pa'],first['velocity_m_s'])
            mass=r['cooling']['hot_turbine_mass_flow_kg_s']; work=0.
            for row in r['stage_results']['turbine']['elements']:
                pt=current['total_pressure_pa']; tt=current['total_temperature_k']; velocity=row['outlet']['velocity_m_s']
                if row['kind']=='mixing':
                    route=r['cooling']['routes'][row['route_index']]
                    mc=route['mass_flow_kg_s']; tc=route['supply_total_temperature_k']; pc=route['supply_total_pressure_pa']
                    hg,sg=h_s(gas,tt,pt); hc,sc=h_s(a,tc,pc)
                    newgas=Mixture({k:mass*gas.mass_fractions.get(k,0)+mc*a.mass_fractions.get(k,0) for k in DATA['species']})
                    target_h=(mass*hg+mc*hc)/(mass+mc)
                    pout=pt*(1-route['input']['mix_pressure_loss_fraction'])
                    phase,scale,href=phase_for(newgas)
                    phase.HP=target_h/scale+href,pout
                    tout=phase.T
                    sgen=(mass+mc)*phase.entropy_mass*scale-mass*sg-mc*sc
                    max_s=max(max_s,abs(sgen-row['entropy_generation_w_k']))
                    current=reference_state(newgas,tout,pout,velocity)
                    gas=newgas; mass+=mc; nm+=1
                elif row['kind']=='rotor':
                    phase,scale,_=phase_for(gas)
                    phase.TP=tt,pt; hi=phase.enthalpy_mass; si=phase.entropy_mass
                    pout=pt/row['rotor_pressure_ratio']
                    phase.SP=si,pout
                    hout=hi-row['rotor_isentropic_efficiency']*(hi-phase.enthalpy_mass)
                    phase.HP=hout,pout
                    work+=mass*(hi-hout)*scale
                    current=reference_state(gas,phase.T,pout,velocity)
                else:
                    if row['coefficient_model']=='loss': pout=pt-row['specified_coefficient']*current['dynamic_pressure_pa']
                    else:
                        ps=current['static_pressure_pa']+row['specified_coefficient']*current['dynamic_pressure_pa']
                        unit=reference_state(gas,tt,100000,velocity)
                        pout=ps/(unit['static_pressure_pa']/100000)
                    current=reference_state(gas,tt,pout,velocity)
                for key in ('total_temperature_k','static_temperature_k'): max_t=max(max_t,abs(current[key]-row['outlet'][key]))
                for key in ('total_pressure_pa','static_pressure_pa'): max_p=max(max_p,abs(current[key]-row['outlet'][key])/current[key])
                nt+=1
            max_w=max(max_w,abs(work-m['turbine_gas_power_w'])/abs(work))
            records.append({'temperature_basis':basis,'route_external_heat_j_kg':heat,'turbine_reference_work_w':work,
                            'combustor_exit_k':m['combustor_exit_temperature_k'],'first_rotor_inlet_k':m['first_rotor_inlet_temperature_k']})
    assert max_t<2e-5 and max_p<2e-7 and max_s<2e-5 and max_w<2e-7
    return {'reference':'Cantera NASA9 HP/SP and absolute entropy including ideal mixing','dataset_id':DATA['dataset_id'],
            'element_states_checked':nt,'mixing_points_checked':nm,'maximum_temperature_error_k':max_t,
            'maximum_pressure_relative_error':max_p,'maximum_mixing_entropy_error_w_k':max_s,
            'maximum_turbine_work_relative_error':max_w,'cases':records,
            'limitations':'Shares NASA data, gas composition/FAR and solved rotor pressure ratios with the app. Independently propagates mixed states and checks mixing entropy and turbine work. Not an independent full-cycle root solver, CFD or real-engine validation.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    report=verify(); args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k.startswith('maximum') or k.endswith('checked')}))
