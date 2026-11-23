"""Optional outlet temperature profile and local liner heat balance."""
import math

from gas_turbine_cycle_app.design_exchange import number
from gas_turbine_cycle_app.thermo import solve_temperature


def named_rows(rows, label):
    if not isinstance(rows, list) or len(rows) > 40:
        raise ValueError(f"{label}は40件までです。")
    seen = set()
    for row in rows:
        name = row.get("id") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or name in seen:
            raise ValueError(f"{label}のIDを重複しない名称で指定してください。")
        seen.add(name)
        yield name, row


def outlet_profile(rows, gas, inlet_k, outlet_k, flow):
    sectors = [(name, number(row["mass_fraction"], "出口区画の質量流量比", 1e-8, 1.),
                number(row["temperature_offset_k"], "出口区画の全温偏差", -500., 500.))
               for name, row in named_rows(rows, "出口区画")]
    if not sectors or not math.isclose(math.fsum(row[1] for row in sectors), 1., rel_tol=0., abs_tol=1e-9):
        raise ValueError("出口区画の質量流量比の合計を1にしてください。")
    total = math.fsum(row[1] for row in sectors)
    sectors = [(name, weight / total, offset) for name, weight, offset in sectors]
    lower = 200. - min(row[2] for row in sectors)
    upper = 2500. - max(row[2] for row in sectors)

    def mixed_enthalpy(reference_k):
        return math.fsum(weight * gas.h(reference_k + offset) for _, weight, offset in sectors)

    target = gas.h(outlet_k)
    base = solve_temperature(mixed_enthalpy, target, lower=lower, upper=upper)
    temperatures = [{"id": name, "mass_fraction": weight, "total_temperature_k": base + offset}
                    for name, weight, offset in sectors]
    mean = math.fsum(row["mass_fraction"] * row["total_temperature_k"] for row in temperatures)
    hottest = max(row["total_temperature_k"] for row in temperatures)
    return {"sectors": temperatures, "mass_average_temperature_k": mean, "maximum_temperature_k": hottest,
            "cycle_equivalent_temperature_k": outlet_k,
            "pattern_factor": (hottest - mean) / (mean - inlet_k) if mean > inlet_k else None,
            "enthalpy_residual_w_per_can": flow * (mixed_enthalpy(base) - target)}


def liner_heat(row):
    area = number(row["area_m2"], "ライナ面積", 1e-9)
    hot = number(row["gas_temperature_k"], "主流側の代表温度", 200., 2500.)
    cold = number(row["coolant_temperature_k"], "冷却側の代表温度", 200., 2500.)
    film = number(row["film_effectiveness"], "フィルム冷却有効度", 0., 1.)
    gas_htc = number(row["gas_htc_w_m2_k"], "主流側熱伝達率", 1e-6)
    coolant_htc = number(row["coolant_htc_w_m2_k"], "冷却側熱伝達率", 1e-6)
    thickness = number(row["thickness_m"], "ライナ厚さ", 1e-9)
    conductivity = number(row["conductivity_w_m_k"], "熱伝導率", 1e-6)
    adiabatic = hot - film * (hot - cold)
    flux = (adiabatic - cold) / (1 / gas_htc + thickness / conductivity + 1 / coolant_htc)
    wall_hot = adiabatic - flux / gas_htc
    wall_cold = cold + flux / coolant_htc
    limit = row.get("metal_limit_k")
    margin = number(limit, "材料温度上限", 200., 2500.) - max(wall_hot, wall_cold) if limit is not None else None
    return {"adiabatic_wall_temperature_k": adiabatic, "hot_wall_temperature_k": wall_hot,
            "cold_wall_temperature_k": wall_cold, "heat_flux_w_m2": flux, "heat_transfer_w_per_can": flux * area,
            "temperature_margin_k": margin, "wall_balance_residual_k": wall_hot - wall_cold - flux * thickness / conductivity}


def evaluate_thermal(settings, gas, inlet_k, outlet_k, flow):
    if not isinstance(settings, dict) or not settings or settings.keys() - {"exit_sectors", "liner_segments"}:
        raise ValueError("熱評価には出口区画またはライナ区間を指定してください。")
    result = {}
    if "exit_sectors" in settings:
        result["outlet_profile"] = outlet_profile(settings["exit_sectors"], gas, inlet_k, outlet_k, flow)
    if "liner_segments" in settings:
        segments = [{"id": name, **liner_heat(row)} for name, row in named_rows(settings["liner_segments"], "ライナ区間")]
        if not segments:
            raise ValueError("ライナ区間を1件以上指定してください。")
        result["liner_segments"] = segments
    return result
