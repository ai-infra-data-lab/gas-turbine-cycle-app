"""Endpoint-constrained stage allocation on a constant-polytropic-efficiency path.

This is thermodynamic unstacking, not a meanline or aerodynamic stage prediction.
All states use total pressure/temperature, fixed ideal-gas composition, no interstage losses/cooling.
"""
import math

if __package__:
    from .model import InputError
    from .thermo import ConstantGas, properties_at, solve_temperature
else:
    from model import InputError
    from thermo import ConstantGas, properties_at, solve_temperature


def inverse(gas, function, value, tin, tout):
    # The legacy model is not restricted to the NASA data temperature interval.
    if isinstance(gas, ConstantGas):
        if function == "h":
            return 298.15 + value / gas.constant_cp
        return 298.15 * math.exp(value / gas.constant_cp)
    return solve_temperature(getattr(gas, function), value, lower=min(tin, tout), upper=max(tin, tout))


def allocate(gas, tin, pin, tout, pout, mass, efficiency, settings, kind, rpm, mechanical_efficiency=1.0):
    compressor = kind == "compressor"
    direction = 1 if compressor else -1
    hin, hout = gas.h(tin), gas.h(tout)
    sin, sout = gas.entropy_thermal(tin), gas.entropy_thermal(tout)
    log_ratio = math.log(pout / pin)
    eta_p = gas.r * log_ratio / (sout - sin) if compressor else (sout - sin) / (gas.r * log_ratio)
    if not 0 < eta_p <= 1 + 1e-8:
        raise InputError("段配分のポリトロープ効率が成立しません。")
    weights = settings["weights"]
    total_weight = math.fsum(weights)
    states = [{"total_temperature_k": tin, "total_pressure_pa": pin, "mass_flow_kg_s": mass, **properties_at(gas, tin)}]
    rows = []
    accumulated = 0.0
    for index, weight in enumerate(weights):
        previous = states[-1]
        accumulated += weight
        fraction = accumulated / total_weight
        if index == len(weights) - 1:
            temp, pressure = tout, pout
        elif settings["distribution"] == "pressure":
            pressure = pin * math.exp(log_ratio * fraction)
            temp = inverse(gas, "entropy_thermal", sin + (sout - sin) * fraction, tin, tout)
        else:
            temp = inverse(gas, "h", hin + (hout - hin) * fraction, tin, tout)
            pressure = pin * math.exp(log_ratio * (gas.entropy_thermal(temp) - sin) / (sout - sin))
        state = {"total_temperature_k": temp, "total_pressure_pa": pressure, "mass_flow_kg_s": mass, **properties_at(gas, temp)}
        states.append(state)
        tprev, pprev = previous["total_temperature_k"], previous["total_pressure_pa"]
        hprev = gas.h(tprev)
        t_is = inverse(gas, "entropy_thermal", gas.entropy_thermal(tprev) + gas.r * math.log(pressure / pprev),
                       tprev if compressor else 200.0, temp)
        # For turbines the isentropic endpoint is cooler than the actual endpoint;
        # for compressors it lies between the actual inlet and outlet.
        delta_h = direction * (gas.h(temp) - hprev)
        ideal_h = direction * (gas.h(t_is) - hprev)
        eta_stage = ideal_h / delta_h if compressor else delta_h / ideal_h
        diameter = settings["mean_diameters_m"][index] if settings["mean_diameters_m"] is not None else None
        speed = math.pi * diameter * rpm / 60 if diameter is not None else None
        rows.append({"stage": index + 1, "inlet": previous, "outlet": state,
                     "pressure_ratio": pressure / pprev if compressor else pprev / pressure,
                     "temperature_change_k": temp - tprev, "specific_work_j_kg": delta_h,
                     "power_w": mass * delta_h, "transmitted_power_w": mass * delta_h * mechanical_efficiency,
                     "isentropic_efficiency": eta_stage, "isentropic_exit_temperature_k": t_is,
                     "pressure_log_fraction": math.log(pressure / pprev) / log_ratio,
                     "work_fraction": delta_h / abs(hout - hin),
                     "entropy_generation_j_kg_k": gas.entropy_thermal(temp) - gas.entropy_thermal(tprev) - gas.r * math.log(pressure / pprev),
                     "mean_diameter_m": diameter, "blade_speed_m_s": speed,
                     "loading_coefficient": delta_h / speed**2 if speed is not None else None})
    overall_is = inverse(gas, "entropy_thermal", sin + gas.r * log_ratio,
                         tin if compressor else 200.0, tout)
    overall_ideal = direction * (gas.h(overall_is) - hin)
    actual = abs(hout - hin)
    recovered = overall_ideal / actual if compressor else actual / overall_ideal
    return {"count": settings["count"], "distribution": settings["distribution"],
            "method": "constant_polytropic_endpoint_unstacking", "polytropic_efficiency": eta_p,
            "specified_overall_isentropic_efficiency": efficiency, "recovered_overall_isentropic_efficiency": recovered,
            "specific_work_j_kg": actual, "total_power_w": mass * actual,
            "sum_stage_power_w": math.fsum(row["power_w"] for row in rows),
            "overall_pressure_ratio": math.exp(direction * log_ratio),
            "stages": rows, "states": states,
            "assumption": "全体効率・端点を固定し、全段で一定のポリトロープ効率を仮定した熱力学的配分。翼列・速度三角形の設計性能ではありません。"}
