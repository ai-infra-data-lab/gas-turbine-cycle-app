"""Optional Cantera verification of Euler pumping and two-stream blade heat balances."""
import argparse
import json
import math
from pathlib import Path
from verify_cantera import phase_for, air, ROOT
from thermo import Mixture


def verify():
    errors={'pump_temperature_k':0.,'pump_pressure_relative':0.,'heat_balance_w':0.,
            'heat_entropy_w_k':0.,'lmtd_balance_w':0.}
    count=0
    for stem in ('blade_fixed','blade_sizing','blade_no_pump'):
        result=json.loads((ROOT/f'examples/{stem}_result.json').read_text(encoding='utf-8'))['result']
        for route in result['cooling']['routes']:
            if 'thermal' not in route: continue
            p=route['input']['pumping']; thermal=route['thermal']; cfg=route['input']['thermal']
            row=next(e for e in result['stage_results']['turbine']['elements'] if e.get('route_index')==route['index'])
            phase,scale,href=phase_for(air())
            phase.TP=route['pre_pump_temperature_k'],route['pre_pump_pressure_pa']
            hin=phase.enthalpy_mass; sin=phase.entropy_mass
            work=route['pumping_specific_work_j_kg']
            phase.HP=hin+work/scale,route['supply_total_pressure_pa']
            errors['pump_temperature_k']=max(errors['pump_temperature_k'],abs(phase.T-route['supply_total_temperature_k']))
            phase.HP=hin+p['isentropic_efficiency']*work/scale,route['pre_pump_pressure_pa']
            entropy_increase=(phase.entropy_mass-sin)*scale
            predicted=route['pre_pump_pressure_pa']*math.exp(entropy_increase/air().r)
            errors['pump_pressure_relative']=max(errors['pump_pressure_relative'],abs(predicted/route['supply_total_pressure_pa']-1))
            q=thermal['heat_w']; sums=[]
            for gas,mass,tin,tout in ((Mixture(row['primary_mass_fractions']),row['inlet']['mass_flow_kg_s'],thermal['gas_inlet_temperature_k'],thermal['gas_outlet_temperature_k']),
                                      (air(),route['mass_flow_kg_s'],thermal['coolant_inlet_temperature_k'],thermal['coolant_outlet_temperature_k'])):
                ph,sc,_=phase_for(gas); ph.TP=tin,1e5; h1=ph.enthalpy_mass; s1=ph.entropy_mass
                ph.TP=tout,1e5
                sums.append((mass*(ph.enthalpy_mass-h1)*sc,mass*(ph.entropy_mass-s1)*sc))
            errors['heat_balance_w']=max(errors['heat_balance_w'],abs(sums[0][0]+q),abs(sums[1][0]-q))
            errors['heat_entropy_w_k']=max(errors['heat_entropy_w_k'],abs(sum(v[1] for v in sums)-thermal['heat_entropy_generation_w_k']))
            d1=thermal['gas_inlet_temperature_k']-thermal['coolant_inlet_temperature_k']; d2=thermal['gas_outlet_temperature_k']-thermal['coolant_outlet_temperature_k']
            lm=(d1-d2)/math.log(d1/d2)
            errors['lmtd_balance_w']=max(errors['lmtd_balance_w'],abs(q-(1-cfg['film_effectiveness'])*lm/thermal['thermal_resistance_k_w']))
            count+=1
    assert errors['pump_temperature_k']<2e-5 and errors['pump_pressure_relative']<1e-7
    assert errors['heat_balance_w']<1e-3 and errors['heat_entropy_w_k']<1e-5 and errors['lmtd_balance_w']<1e-3
    return {'reference':'Cantera 3.2 NASA9 HP and entropy evaluation; separate heat/LMTD residuals',
            'thermal_routes_checked':count,'maximum_errors':errors,
            'limitations':'Shared NASA coefficients, solved cycle inlet states and thermal outlet temperatures. Checks state inversion and balances, not heat-transfer correlations, relative-frame rotating passages, 3D peak metal temperature or measured blade performance.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    report=verify(); args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report))
