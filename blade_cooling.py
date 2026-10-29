"""Lumped co-current blade heat exchanger and absolute-frame Euler coolant pump.

Coefficients are supplied, not predicted from blade geometry. The thermal cell
extracts exactly the heat given to coolant before its immediate complete mixing.
"""
import math

if __package__:
    from .model import InputError
    from .thermo import air
    from .elements import temperature
else:
    from model import InputError
    from thermo import air
    from elements import temperature


def thermal_defaults():
    return dict(enabled=False, mode="fixed_flow", gas_h_w_m2_k=1200., coolant_h_w_m2_k=2000.,
                gas_area_m2=.02, coolant_area_m2=.025, metal_thickness_m=.001,
                metal_conductivity_w_m_k=20., tbc_thickness_m=.0002,
                tbc_conductivity_w_m_k=1.5, film_effectiveness=0.,
                metal_limit_k=1000., min_fraction=.005, max_fraction=.15)


def pumping_defaults():
    return dict(enabled=False, inlet_radius_m=.025, outlet_radius_m=.07,
                inlet_swirl_ratio=1., outlet_swirl_ratio=1., isentropic_efficiency=.8)


def pump(gas, tin, pin, rpm, settings):
    if not settings["enabled"]:
        return tin, pin, 0.
    omega=rpm*2*math.pi/60
    work=omega**2*(settings["outlet_swirl_ratio"]*settings["outlet_radius_m"]**2
                   -settings["inlet_swirl_ratio"]*settings["inlet_radius_m"]**2)
    if work < 0:
        raise InputError("冷却ポンピングのEuler仕事が負です。半径・旋回比を確認してください。")
    tout=temperature(gas,"h",gas.h(tin)+work)
    tis=temperature(gas,"h",gas.h(tin)+settings["isentropic_efficiency"]*work)
    pout=pin*math.exp((gas.entropy_thermal(tis)-gas.entropy_thermal(tin))/gas.r)
    if not math.isfinite(pout):
        raise InputError("冷却ポンピングの全圧が非有限値です。")
    return tout,pout,work


def log_mean(a,b):
    if min(a,b)<=0: return 0.
    if abs(a-b)<1e-7*max(a,b): return (a+b)/2
    return (a-b)/math.log(a/b)


def heat_cell(gas, mg, tg, mc, tc, cfg, coolant=None):
    """Finite-capacity equivalent LMTD cell; SI physical areas and actual kg/s.

    Gas-side driving temperature is the supplied equivalent absolute total
    temperature, NOT a computed relative-frame recovery temperature.
    """
    if mg<=0 or mc<=0 or tg<=tc:
        raise InputError("翼熱モデルは正の主流・冷却流量と、主流温度 > 冷却入口温度が必要です。")
    a=coolant or air()
    rg=1/(cfg['gas_h_w_m2_k']*cfg['gas_area_m2'])
    rc=1/(cfg['coolant_h_w_m2_k']*cfg['coolant_area_m2'])
    rm=cfg['metal_thickness_m']/(cfg['metal_conductivity_w_m_k']*cfg['gas_area_m2'])
    rt=cfg['tbc_thickness_m']/(cfg['tbc_conductivity_w_m_k']*cfg['gas_area_m2'])
    resistance=rg+rc+rm+rt
    film=cfg['film_effectiveness']
    conductance=(1-film)/resistance
    ht=mg*gas.h(tg)+mc*a.h(tc)
    lo,hi=tc,tg
    for _ in range(38):
        mid=(lo+hi)/2
        if mg*gas.h(mid)+mc*a.h(mid)<ht: lo=mid
        else: hi=mid
    qmax=mc*(a.h((lo+hi)/2)-a.h(tc))
    lo,hi=0.,qmax
    for _ in range(34):
        q=(lo+hi)/2
        gout=temperature(gas,'h',gas.h(tg)-q/mg)
        cout=temperature(a,'h',a.h(tc)+q/mc)
        residual=q-conductance*log_mean(tg-tc,gout-cout)
        if residual>0: hi=q
        else: lo=q
    q=(lo+hi)/2
    gout=temperature(gas,'h',gas.h(tg)-q/mg)
    cout=temperature(a,'h',a.h(tc)+q/mc)
    def walls(g,c):
        taw=g-film*(g-c)
        local_q=(taw-c)/resistance
        return dict(adiabatic_wall_temperature_k=taw,
                    metal_outer_temperature_k=taw-local_q*(rg+rt),
                    metal_inner_temperature_k=c+local_q*rc,
                    coating_outer_temperature_k=taw-local_q*rg)
    first,last=walls(tg,tc),walls(gout,cout)
    metal=max(first['metal_outer_temperature_k'],last['metal_outer_temperature_k'])
    sgen=mg*(gas.entropy_thermal(gout)-gas.entropy_thermal(tg))+mc*(a.entropy_thermal(cout)-a.entropy_thermal(tc))
    if sgen < -1e-6: raise InputError("翼熱モデルでエントロピー生成が負になりました。")
    return dict(heat_w=q,gas_inlet_temperature_k=tg,gas_outlet_temperature_k=gout,
                coolant_inlet_temperature_k=tc,coolant_outlet_temperature_k=cout,
                metal_max_temperature_k=metal,metal_limit_k=cfg['metal_limit_k'],
                metal_margin_k=cfg['metal_limit_k']-metal,within_metal_limit=metal<=cfg['metal_limit_k']+.01,
                inlet_walls=first,outlet_walls=last,thermal_resistance_k_w=resistance,
                heat_entropy_generation_w_k=sgen,
                energy_residual_w=mg*(gas.h(tg)-gas.h(gout))-mc*(a.h(cout)-a.h(tc)),
                temperature_basis='equivalent_absolute_total_temperature',
                model='co_current_equivalent_LMTD_constant_supplied_coefficients')
