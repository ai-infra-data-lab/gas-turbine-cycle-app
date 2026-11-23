"""Design-point, steady single-can combustor heat balance."""
from copy import deepcopy
import math

def evaluate(document, setup):
    from gas_turbine_cycle_app.design_exchange import cycle_air, number, verified_cycle
    from gas_turbine_cycle_app.engine import run_case
    from gas_turbine_cycle_app.thermo import ConstantGas, products, solve_temperature, stoichiometric_fuel_air_ratio
    from .thermal import evaluate_thermal

    baseline, source = verified_cycle(document)
    inputs = deepcopy(baseline["input"])
    if inputs["cooling"]["enabled"]:
        raise ValueError("単管燃焼器の初期連携では、サイクルの冷却抽気を無効にしてください。")
    n = setup["identical_cans"]
    if type(n) is not int or not 1 <= n <= 100:
        raise ValueError("均等分配する缶数は1〜100です。")
    shares = setup["air_split"]
    roles = ("primary", "secondary", "dilution", "liner_cooling")
    if set(shares) != set(roles):
        raise ValueError("一次・二次・希釈・ライナ冷却の空気配分を指定してください。")
    shares = {role: number(shares[role], role, 0., 1.) for role in roles}
    if shares["primary"] <= 0 or not math.isclose(math.fsum(shares.values()), 1., abs_tol=1e-9):
        raise ValueError("一次空気を正とし、空気配分の合計を1にしてください。")
    metrics = baseline["result"]["metrics"]
    station = baseline["result"]["stations"][2]
    airflow = metrics["air_mass_flow_kg_s"] / n
    tin, pin = station["total_temperature_k"], station["total_pressure_pa"]
    gas_in = cycle_air(baseline)
    p = inputs["parameters"]
    eta = number(setup.get("combustion_efficiency", p["combustion_efficiency"]), "燃焼効率", .8, 1.)
    lhv = p["fuel_lhv_j_kg"]
    nasa = inputs["thermodynamics"]["model"] == "nasa9_frozen"
    if nasa:
        thermo = inputs["thermodynamics"]
        limit = stoichiometric_fuel_air_ratio(thermo)

        def gas_out(fuel_air_ratio):
            return products(fuel_air_ratio, thermo)
    else:
        gas = ConstantGas(p["cp_gas_j_kg_k"], p["r_gas_j_kg_k"])
        limit = .2

        def gas_out(fuel_air_ratio):
            return gas

    hin = gas_in.h(tin)
    target = setup.get("target_total_temperature_k")
    specified_fuel = setup.get("fuel_per_can_kg_s")
    if (target is None) == (specified_fuel is None):
        raise ValueError("出口全温または1缶あたり燃料流量のいずれかを指定してください。")
    if specified_fuel is None:
        tout = number(target, "出口全温", tin, 2000.)

        def residual(ratio):
            return (1 + ratio) * gas_out(ratio).h(tout) - hin - ratio * eta * lhv

        lo, hi = 0., limit
        if residual(lo) <= 0 or residual(hi) >= 0:
            raise ValueError("出口全温に対応する希薄燃焼の燃料流量を求められません。")
        for _ in range(62):
            mid = (lo + hi) / 2
            if residual(mid) > 0:
                lo = mid
            else:
                hi = mid
        ratio = (lo + hi) / 2
    else:
        ratio = number(specified_fuel, "1缶あたり燃料流量", 0.) / airflow
        if not 0 < ratio < limit:
            raise ValueError("希薄燃焼範囲内の正の燃料流量を指定してください。")
        tout = solve_temperature(gas_out(ratio).h, (hin + ratio * eta * lhv) / (1 + ratio), lower=tin, upper=2000.)
    area = number(setup["reference_area_per_can_m2"], "1缶あたりの基準流路面積", 1e-9)
    zeta = number(setup["loss_coefficient"], "入口動圧基準の全圧損失係数", 0.)
    density = pin / (gas_in.r * tin)
    velocity = airflow / (density * area)
    inlet_mach = velocity / math.sqrt(gas_in.gamma(tin) * gas_in.r * tin)
    if inlet_mach > .3:
        raise ValueError("入口の密度一定近似はMach 0.3以下で使用してください。")
    dynamic_pressure = density * velocity**2 / 2
    pressure_loss = zeta * dynamic_pressure
    fuel = ratio * airflow
    zone_flows = {role: airflow*share for role, share in shares.items()}
    inputs.update(name="単管燃焼器の出口条件反映", mode="airflow")
    p.update(air_mass_flow_kg_s=airflow*n, turbine_inlet_temperature_k=tout,
             combustor_pressure_loss=pressure_loss/pin, combustion_efficiency=eta)
    combined = run_case(inputs)
    result = {"format": "single-can-combustor-result", "schema_version": 1, "source_cycle": source,
            "setup": setup, "per_can": {"air_kg_s": airflow, "fuel_kg_s": fuel, "outlet_flow_kg_s": airflow+fuel,
                "zones_kg_s": zone_flows, "air_split": shares,
                "zone_mass_residual_kg_s": math.fsum(zone_flows.values())-airflow,
                "primary_equivalence_ratio_indicator": ratio/(shares["primary"] * limit) if nasa else None,
                "inlet_total_pressure_pa": pin, "inlet_total_temperature_k": tin,
                "outlet_total_pressure_pa": pin-pressure_loss, "outlet_total_temperature_k": tout,
                "total_pressure_loss_pa": pressure_loss, "total_pressure_loss_fraction": pressure_loss/pin,
                "inlet_mach": inlet_mach, "reference_dynamic_pressure_pa": dynamic_pressure,
                "enthalpy_balance_residual_w": (airflow+fuel)*gas_out(ratio).h(tout)-airflow*hin-fuel*eta*lhv},
            "cycle_case": combined}
    if "thermal" in setup:
        result["thermal"] = evaluate_thermal(setup["thermal"], gas_out(ratio), tin, tout, airflow+fuel)
    return result
