"""Steady 1-D compressible wall-friction estimate for the sized vaneless diffuser."""
import copy
import math

if __package__:
    from .compressor_sizing import size_stage
    from .engine import run_case
    from .thermo import ConstantGas, air
else:
    from compressor_sizing import size_stage
    from engine import run_case
    from thermo import ConstantGas, air


def march(gas, sizing, darcy, cells):
    """Integrate total-pressure loss and swirl decay on the radial coordinate."""
    dims = sizing["dimensions"]
    r2 = dims["impeller_exit_diameter_m"]/2
    r3 = dims["diffuser_exit_diameter_m"]/2
    b2, b3 = dims["impeller_exit_width_m"], dims["diffuser_exit_width_m"]
    blockage = sizing["assumptions"]["open_area_fraction"]
    inlet = sizing["sizing_states"]["impeller_exit"]
    mass = sizing["cycle_targets"]["mass_flow_kg_s"]
    tt = inlet["total_temperature_k"]
    ht, st = gas.h(tt), gas.entropy_thermal(tt)
    j2 = r2*sizing["checks"]["impeller_exit_swirl_m_s"]
    log_pt2 = math.log(inlet["total_pressure_pa"])
    width_gradient = (b3-b2)/(r3-r2)
    wall_slope_factor = math.hypot(1, width_gradient/2)
    step = (r3-r2)/cells

    def static_temperature(speed_sq):
        target = ht-speed_sq/2
        temp = tt-speed_sq/(2*gas.cp(tt))
        for _ in range(10):
            if temp < 200:
                raise ValueError("ディフューザーの静温が物性モデルの下限を下回ります。")
            correction = (gas.h(temp)-target)/gas.cp(temp)
            temp -= correction
            if abs(correction) < 1e-8:
                if temp < 200:
                    raise ValueError("ディフューザーの静温が物性モデルの下限を下回ります。")
                return temp
        raise ValueError("ディフューザーの静温計算が収束しません。")

    # The total-Mach bound is a speed limit for this fixed total enthalpy.
    low_t, high_t = 200., tt
    def mach_limit_residual(t):
        return 2*(ht-gas.h(t))-.95**2*gas.gamma(t)*gas.r*t

    if mach_limit_residual(low_t) > 0:
        for _ in range(44):
            middle = (low_t+high_t)/2
            if mach_limit_residual(middle) > 0:
                low_t = middle
            else:
                high_t = middle
    speed_limit_sq = 2*(ht-gas.h(high_t))

    def section(radius, log_pt, j):
        width = b2+width_gradient*(radius-r2)
        area = math.tau*radius*width*blockage
        swirl = j/radius
        high_sq = speed_limit_sq-swirl**2
        if high_sq <= 0:
            raise ValueError("ディフューザーの旋回速度が亜音速モデルの上限を超えます。")

        def properties(radial):
            speed_sq = radial**2+swirl**2
            temp = static_temperature(speed_sq)
            pressure = math.exp(log_pt+(gas.entropy_thermal(temp)-st)/gas.r)
            density = pressure/(gas.r*temp)
            return density*area*radial, temp, pressure, density, speed_sq

        low, high = 0., math.sqrt(high_sq)
        if properties(high)[0] <= mass:
            raise ValueError(f"半径 {radius*1000:.2f} mm で流量を通せません（M<0.95／静温≧200 K）。流路幅と条件を確認してください。")
        for _ in range(36):
            mid = (low+high)/2
            if properties(mid)[0] < mass:
                low = mid
            else:
                high = mid
        radial = (low+high)/2
        recovered_mass, temp, pressure, density, speed_sq = properties(radial)
        return {
            "radius_m": radius, "width_m": width, "area_m2": area,
            "total_pressure_pa": math.exp(log_pt), "static_pressure_pa": pressure,
            "static_temperature_k": temp, "density_kg_m3": density,
            "radial_velocity_m_s": radial, "swirl_velocity_m_s": swirl,
            "velocity_m_s": math.sqrt(speed_sq), "mach": math.sqrt(speed_sq/(gas.gamma(temp)*gas.r*temp)),
            "dynamic_pressure_pa": density*speed_sq/2, "mass_residual_kg_s": recovered_mass-mass,
        }

    def rates(row):
        # Hydraulic diameter of a thin annulus, adjusted for both sloped walls.
        hydraulic_diameter = 2*row["width_m"]/wall_slope_factor
        velocity = row["velocity_m_s"]
        # Integrate on the mean radial plane. dL/dr = |C|/Cr.
        drag_per_radius = darcy*velocity/(2*hydraulic_diameter*row["radial_velocity_m_s"])
        return (-drag_per_radius*velocity**2/(gas.r*row["static_temperature_k"]),
                -drag_per_radius*row["radius_m"]*row["swirl_velocity_m_s"])

    log_pt, j = log_pt2, j2
    rows = [section(r2, log_pt, j)]
    for index in range(cells):
        radius = r2+index*step
        dp, dj = rates(rows[-1])
        mid = section(radius+step/2, log_pt+step*dp/2, j+step*dj/2)
        dp, dj = rates(mid)
        log_pt += step*dp
        j += step*dj
        if j <= 0:
            raise ValueError("壁面摩擦に対して旋回の積分が成立しません。摩擦係数と流路幅を見直してください。")
        rows.append(section(r2+(index+1)*step, log_pt, j))
    return rows


def _cycle_comparison(cycle, updated, diffuser_inlet):
    new_rotor, new_diffuser = updated["result"]["stage_results"]["compressor"]["elements"]
    old_rotor, old_diffuser = cycle["result"]["stage_results"]["compressor"]["elements"]
    return {
        "original_diffuser_cp": old_diffuser["pressure_recovery_coefficient"],
        "updated_diffuser_cp": new_diffuser["pressure_recovery_coefficient"],
        "original_pressure_loss_pa": old_diffuser["total_pressure_loss_pa"],
        "updated_pressure_loss_pa": new_diffuser["total_pressure_loss_pa"],
        "original_net_output_w": cycle["result"]["metrics"]["net_electric_power_w"],
        "updated_net_output_w": updated["result"]["metrics"]["net_electric_power_w"],
        "original_compressor_work_j_kg": old_rotor["specific_work_j_kg"],
        "updated_compressor_work_j_kg": new_rotor["specific_work_j_kg"],
        "diffuser_inlet_total_pressure_shift_pa": new_diffuser["inlet"]["total_pressure_pa"]-diffuser_inlet["total_pressure_pa"],
        "updated_pressure_ratio": updated["result"]["stage_results"]["compressor"]["overall_pressure_ratio"],
        "updated_case": updated,
    }


def _fixed_geometry(cycle, settings, sizing, inlet, outlet):
    """Carry the geometry/Euler work to the cycle by freeing the stage PR."""
    compressor_inlet = sizing["cycle_targets"]["inlet"]["total_pressure_pa"]
    ratio = outlet["total_pressure_pa"]/compressor_inlet
    target = cycle["input"]["parameters"]["compressor_pressure_ratio"]
    matched = {
        "target_pressure_ratio": target, "achieved_pressure_ratio": ratio,
        "pressure_ratio_delta": ratio-target,
        "euler_work_j_kg": sizing["checks"]["tip_speed_m_s"]*sizing["checks"]["impeller_exit_swirl_m_s"],
        "cycle_comparison": None,
    }
    candidate = copy.deepcopy(settings)
    candidate["name"] = cycle["input"]["name"][:125]+"・固定形状"
    candidate["parameters"]["compressor_pressure_ratio"] = ratio
    try:
        updated = run_case(candidate)
    except ValueError as exc:
        matched["cycle_error"] = str(exc)
        return matched

    comparison = _cycle_comparison(cycle, updated, inlet)
    rotor, diffuser = updated["result"]["stage_results"]["compressor"]["elements"]
    residuals = {
        "diffuser_inlet_total_pressure_pa": comparison["diffuser_inlet_total_pressure_shift_pa"],
        "diffuser_outlet_total_pressure_pa": diffuser["outlet"]["total_pressure_pa"]-outlet["total_pressure_pa"],
        "impeller_work_j_kg": rotor["specific_work_j_kg"]-matched["euler_work_j_kg"],
    }
    matched["residuals"] = residuals
    if any(abs(value) > .01 for value in residuals.values()):
        matched["cycle_error"] = "全圧またはインペラー比仕事の接続残差が許容値を超えました。"
        return matched
    matched["cycle_comparison"] = comparison
    return matched


def analyze(cycle, assumptions, darcy=0.018):
    if type(darcy) not in (float, int) or not math.isfinite(darcy) or not 0 <= darcy <= .12:
        raise ValueError("ダーシー摩擦係数は0〜0.12で指定してください。")
    sizing = size_stage(cycle, assumptions)
    original = cycle["input"]
    p = original["parameters"]
    gas = air() if original["thermodynamics"]["model"] == "nasa9_frozen" else ConstantGas(
        p["cp_air_j_kg_k"], p["r_air_j_kg_k"])
    coarse = march(gas, sizing, darcy, 32)
    rows = march(gas, sizing, darcy, 64)
    inlet, outlet = rows[0], rows[-1]
    drop = inlet["total_pressure_pa"]-outlet["total_pressure_pa"]
    q = inlet["dynamic_pressure_pa"]
    loss = drop/q
    recovery = (outlet["static_pressure_pa"]-inlet["static_pressure_pa"])/q
    settings = copy.deepcopy(original)
    settings["name"] = original["name"][:125]+"・形状損失で再計算"
    settings["mode"] = "airflow"
    settings["parameters"]["air_mass_flow_kg_s"] = sizing["cycle_targets"]["mass_flow_kg_s"]
    element = settings["elements"]["compressor"]["stages"][0]
    element["rotor_exit_velocity_m_s"] = inlet["velocity_m_s"]
    element["diffuser"].update(model="loss", coefficient=loss, exit_velocity_m_s=outlet["velocity_m_s"])
    report = {
        "format": "vaneless-diffuser-analysis", "schema_version": 1, "module_version": "0.2.0",
        "sizing": sizing, "darcy_friction_factor": darcy, "cells": 64,
        "pressure_loss_pa": drop, "loss_coefficient": loss, "pressure_recovery_coefficient": recovery,
        "grid_pressure_delta_pa": outlet["total_pressure_pa"]-coarse[-1]["total_pressure_pa"],
        "grid_pressure_loss_relative_pct": (100*abs(outlet["total_pressure_pa"]-coarse[-1]["total_pressure_pa"])/drop
                                            if drop > 0 else None),
        "peak_mach": max(row["mach"] for row in rows),
        "max_mass_residual_kg_s": max(abs(row["mass_residual_kg_s"]) for row in rows),
        "states": rows, "cycle_comparison": None,
        "fixed_geometry": _fixed_geometry(cycle, settings, sizing, inlet, outlet),
    }
    try:
        updated = run_case(settings)
    except ValueError as exc:
        report["cycle_error"] = str(exc)
        return report

    report["cycle_comparison"] = _cycle_comparison(cycle, updated, inlet)
    return report
