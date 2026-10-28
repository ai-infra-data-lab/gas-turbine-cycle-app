"""Coefficient-based thermodynamic element network, no blade geometry prediction.

Cp=(p_out-p_in)/(rho_in V_in²/2); zeta=(Pt_in-Pt_out)/(rho_in V_in²/2).
All velocities are absolute speed magnitudes, and static states use h(Tt)=h(T)+V²/2.
"""
import math
from copy import deepcopy

if __package__:
    from .model import InputError
    from .stages import inverse
    from .thermo import ConstantGas, properties_at
else:
    from model import InputError
    from stages import inverse
    from thermo import ConstantGas, properties_at


def temperature(gas, function, target):
    return inverse(gas, function, target, 200., 6000.)


def state(gas, tt, pt, velocity):
    if not math.isfinite(pt) or pt <= 0:
        raise InputError("要素の全圧が0以下です。係数・流速・圧力配分を確認してください。")
    ts = temperature(gas, "h", gas.h(tt) - velocity**2/2)
    if ts <= 0:
        raise InputError("流速に対して温度が低く、正の静温を求められません。")
    ps = pt * math.exp((gas.entropy_thermal(ts)-gas.entropy_thermal(tt))/gas.r)
    rho = ps/(gas.r*ts)
    return {"total_temperature_k": tt, "total_pressure_pa": pt, "velocity_m_s": velocity,
            "static_temperature_k": ts, "static_pressure_pa": ps, "density_kg_m3": rho,
            "dynamic_pressure_pa": .5*rho*velocity**2, "mach": velocity/math.sqrt(gas.gamma(ts)*gas.r*ts),
            **properties_at(gas, tt)}


def passive(gas, inlet, settings, name, strict=True):
    tt, pt = inlet["total_temperature_k"], inlet["total_pressure_pa"]
    velocity, coefficient = settings["exit_velocity_m_s"], settings["coefficient"]
    q = inlet["dynamic_pressure_pa"]
    if settings["model"] == "loss":
        pout = pt - coefficient*q
    else:
        ps = inlet["static_pressure_pa"] + coefficient*q
        ts = temperature(gas, "h", gas.h(tt)-velocity**2/2)
        pout = ps*math.exp((gas.entropy_thermal(tt)-gas.entropy_thermal(ts))/gas.r)
    outlet = state(gas, tt, pout, velocity)
    if strict and pout > pt*(1+1e-10):
        raise InputError(name+"で静止・断熱要素の全圧が上昇します。Cpが大きすぎるか出口流速と不整合です。")
    return {"name": name, "kind": "stationary", "coefficient_model": settings["model"], "specified_coefficient": coefficient,
            "inlet": inlet, "outlet": outlet, "specific_work_j_kg": 0.,
            "pressure_recovery_coefficient": (outlet["static_pressure_pa"]-inlet["static_pressure_pa"])/q,
            "total_pressure_loss_coefficient": (pt-pout)/q, "total_pressure_loss_pa": pt-pout,
            "entropy_generation_j_kg_k": -gas.r*math.log(pout/pt), "rotor_isentropic_efficiency": None}


def rotor(gas, inlet, pressure_ratio, eta, velocity, compressor):
    tt, pt = inlet["total_temperature_k"], inlet["total_pressure_pa"]
    pout = pt*pressure_ratio if compressor else pt/pressure_ratio
    tis = temperature(gas, "entropy_thermal", gas.entropy_thermal(tt)+gas.r*math.log(pout/pt))
    hout = gas.h(tt)+(gas.h(tis)-gas.h(tt))/eta if compressor else gas.h(tt)-eta*(gas.h(tt)-gas.h(tis))
    tout = temperature(gas, "h", hout)
    outlet = state(gas, tout, pout, velocity)
    work = (1 if compressor else -1)*(hout-gas.h(tt))
    return {"name": "インペラー" if compressor else "タービンロータ", "kind": "rotor", "coefficient_model": None,
            "specified_coefficient": None, "inlet": inlet, "outlet": outlet, "specific_work_j_kg": work,
            "pressure_recovery_coefficient": None, "total_pressure_loss_coefficient": None, "total_pressure_loss_pa": None,
            "entropy_generation_j_kg_k": gas.entropy_thermal(tout)-gas.entropy_thermal(tt)-gas.r*math.log(pout/pt),
            "rotor_isentropic_efficiency": eta, "rotor_pressure_ratio": pressure_ratio}


def run_stage(gas, inlet, settings, ratio, compressor, strict):
    rows = []
    current = inlet
    if not compressor and settings["nozzle"]["enabled"]:
        part = passive(gas, current, settings["nozzle"], "ノズル", strict)
        rows.append(part)
        current = part["outlet"]
    part = rotor(gas, current, ratio, settings["rotor_efficiency"], settings["rotor_exit_velocity_m_s"], compressor)
    rows.append(part)
    current = part["outlet"]
    for key, name in (("diffuser", "ディフューザー"), ("return_channel", "リターンチャネル")) if compressor else (("diffuser", "出口ディフューザー"),):
        if settings[key]["enabled"]:
            part = passive(gas, current, settings[key], name, strict)
            rows.append(part)
            current = part["outlet"]
    if strict:
        for row in rows:
            if max(row["inlet"]["mach"], row["outlet"]["mach"]) >= .95:
                raise InputError(row["name"]+"の流れが要素モデルの亜音速範囲（M<0.95）を外れます。")
    return current, rows


def solve_train(gas, tin, pin, pout, staging, config, kind, rpm):
    compressor = kind == "compressor"
    label = "圧縮機" if compressor else "タービン"
    current = state(gas, tin, pin, config["inlet_velocity_m_s"])
    states, stages, all_elements = [current], [], []
    whole_log = math.log(pout/pin)
    weights = staging["weights"]
    accumulated = 0.
    for i, settings in enumerate(config["stages"]):
        entry = current
        accumulated += weights[i]/math.fsum(weights)
        target = pout if i == len(weights)-1 else pin*math.exp(whole_log*accumulated)
        def evaluate(log_ratio, strict=False):
            return run_stage(gas, entry, settings, math.exp(log_ratio), compressor, strict)
        def residual(log_ratio):
            end, _ = evaluate(log_ratio)
            return (1 if compressor else -1)*math.log(end["total_pressure_pa"]/target)
        low, high = 0., max(.05, abs(math.log(target/entry["total_pressure_pa"]))+.1)
        if residual(low) > 1e-10:
            raise InputError(f"{label}{i+1}段の静止要素だけで圧力配分を超えます。係数・流速・段配分を確認してください。")
        for _ in range(12):
            if residual(high) >= 0:
                break
            if high >= math.log(100):
                raise InputError(f"{label}{i+1}段の必要ロータ圧力比が対応範囲を超えます。")
            high = min(high*1.5, math.log(100))
        else:
            raise InputError(label+"の要素圧力配分が収束しません。")
        for _ in range(42):
            mid = (low+high)/2
            if residual(mid) < 0:
                low = mid
            else:
                high = mid
        current, rows = evaluate((low+high)/2, True)
        # Do not overwrite the solved endpoint: report its independently computed pressure residual.
        pressure_error = current["total_pressure_pa"]-target
        tis = temperature(gas, "entropy_thermal", gas.entropy_thermal(entry["total_temperature_k"])+gas.r*math.log(current["total_pressure_pa"]/entry["total_pressure_pa"]))
        sign = 1 if compressor else -1
        work = sign*(gas.h(current["total_temperature_k"])-gas.h(entry["total_temperature_k"]))
        ideal = sign*(gas.h(tis)-gas.h(entry["total_temperature_k"]))
        eta = ideal/work if compressor else work/ideal
        if not 0 < eta <= 1+1e-8:
            raise InputError(label+"の段効率が成立しません。")
        diameter = staging["mean_diameters_m"][i] if staging["mean_diameters_m"] is not None else None
        speed = math.pi*diameter*rpm/60 if diameter is not None else None
        for row in rows:
            row["stage"] = i+1
        stages.append({"stage": i+1, "inlet": entry, "outlet": current,
                       "pressure_ratio": math.exp(sign*math.log(current["total_pressure_pa"]/entry["total_pressure_pa"])),
                       "specific_work_j_kg": work, "temperature_change_k": current["total_temperature_k"]-entry["total_temperature_k"],
                       "isentropic_efficiency": eta, "isentropic_exit_temperature_k": tis,
                       "pressure_log_fraction": math.log(current["total_pressure_pa"]/entry["total_pressure_pa"])/whole_log,
                       "entropy_generation_j_kg_k": gas.entropy_thermal(current["total_temperature_k"])-gas.entropy_thermal(entry["total_temperature_k"])-gas.r*math.log(current["total_pressure_pa"]/entry["total_pressure_pa"]),
                       "mean_diameter_m": diameter, "blade_speed_m_s": speed, "loading_coefficient": work/speed**2 if speed else None,
                       "outlet_pressure_residual_pa": pressure_error})
        states.append(current)
        all_elements.extend(rows)
    actual = (1 if compressor else -1)*(gas.h(current["total_temperature_k"])-gas.h(tin))
    t_is = temperature(gas, "entropy_thermal", gas.entropy_thermal(tin)+gas.r*math.log(current["total_pressure_pa"]/pin))
    ideal = (1 if compressor else -1)*(gas.h(t_is)-gas.h(tin))
    efficiency = ideal/actual if compressor else actual/ideal
    if not 0 < efficiency <= 1+1e-8:
        raise InputError(label+"全体の要素計算効率が成立しません。")
    delta_s = gas.entropy_thermal(current["total_temperature_k"])-gas.entropy_thermal(tin)
    eta_p = gas.r*whole_log/delta_s if compressor else delta_s/(gas.r*whole_log)
    for row in stages:
        row["work_fraction"] = row["specific_work_j_kg"]/actual
    return {"count": len(stages), "distribution": "pressure", "method": "coefficient_element_network", "polytropic_efficiency": eta_p,
            "specified_overall_isentropic_efficiency": None, "recovered_overall_isentropic_efficiency": efficiency,
            "specific_work_j_kg": actual, "overall_pressure_ratio": math.exp((1 if compressor else -1)*whole_log),
            "outlet_pressure_residual_pa": current["total_pressure_pa"]-pout, "stages": stages, "states": states, "elements": all_elements,
            "assumption": "段の全圧比を固定し、ロータ全圧効率・静圧回復/全圧損失係数・絶対流速から性能を計算。一定ポリトロープ経路や翼列空力予測ではありません。"}


def apply_mass(train, mass, mechanical_efficiency=1.):
    result = deepcopy(train)
    for state_row in result["states"]:
        state_row["mass_flow_kg_s"] = mass
        state_row["effective_flow_area_m2"] = mass/(state_row["density_kg_m3"]*state_row["velocity_m_s"])
    for row in result["stages"]+result["elements"]:
        row["power_w"] = mass*row["specific_work_j_kg"]
        row["transmitted_power_w"] = row["power_w"]*mechanical_efficiency
        for key in ("inlet", "outlet"):
            row[key]["mass_flow_kg_s"] = mass
            row[key]["effective_flow_area_m2"] = mass/(row[key]["density_kg_m3"]*row[key]["velocity_m_s"])
    result["total_power_w"] = mass*result["specific_work_j_kg"]
    result["sum_stage_power_w"] = math.fsum(row["power_w"] for row in result["stages"])
    return result
