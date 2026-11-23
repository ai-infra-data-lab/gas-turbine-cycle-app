"""Stationary annular tooth chain; complete inter-pocket kinetic dissipation.

Not a Vermes correlation, rotating seal, honeycomb or carry-over model.
Uses engine's calibrated compressible nozzle function at each tooth.
"""
import math


def labyrinth(pu, tu, pd, e, gas, reverse=False):
    # Local import avoids a module-level circular dependency.
    from engine import _inlet, orifice
    count = e["tooth_count"]
    gaps = e["tooth_clearances_m"] or [e["clearance_m"]] * count
    tooth_ids = list(range(1, count + 1))
    if reverse:
        gaps, tooth_ids = list(reversed(gaps)), list(reversed(tooth_ids))
    areas = [math.pi * e["seal_diameter_m"] * c for c in gaps]
    cd = e["cd_reverse"] if reverse else e["cd"]
    gamma = gas["gamma"]
    critical = (2 / (gamma + 1)) ** (gamma / (gamma - 1))
    capacity_per_pressure = cd * math.sqrt(gamma / (gas["R"] * tu)) * (2 / (gamma + 1)) ** ((gamma + 1) / (2 * (gamma - 1)))

    def march(mass):
        p, pressures, machs = pu, [pu], []
        for area in areas:
            if mass > capacity_per_pressure * area * p:
                return None
            v, pnext = _inlet(mass, p, tu, area * cd, gas)
            # Static temperature at the minimum section, before pocket mixing.
            cp = gamma * gas["R"] / (gamma - 1)
            ts = tu - v*v/(2*cp)
            machs.append(v / math.sqrt(gamma * gas["R"] * ts))
            pressures.append(pnext)
            p = pnext
        return pressures, machs

    lower, upper = 0.0, capacity_per_pressure * areas[0] * pu
    best = ([pu] * (count + 1), [0.0] * count)
    for _ in range(42):
        trial = (lower + upper) / 2
        candidate = march(trial)
        if candidate is not None and candidate[0][-1] >= pd:
            lower, best = trial, candidate
        else:
            upper = trial
        if upper-lower <= max(1e-14, upper*1e-10):
            break
    pressures, machs = best
    residual = pressures[-1] - pd
    choked = residual > max(0.02, pu * 2e-7)
    # A small upstream gap can choke before the last tooth; downstream pockets
    # then require a separate expansion/mixing model. Fail explicitly.
    limiting = max(range(count), key=lambda i: machs[i])
    if choked and limiting != count - 1:
        from engine import CalculationError
        raise CalculationError(f"{e['id']}: 歯{tooth_ids[limiting]}が中間でチョークします。下流膨張・混合は未対応です。個別隙間や背圧を確認してください。")
    if choked and max(machs) < .995:
        from engine import CalculationError
        raise CalculationError(f"{e['id']}: 歯間圧力の計算が収束しません。")
    teeth = []
    for i in range(count):
        pout = pd if choked and i == count-1 else pressures[i+1]
        actual = orifice(pressures[i], tu, pout, areas[i], cd, gas)
        teeth.append({"tooth":tooth_ids[i], "clearance_m":gaps[i], "area_m2":areas[i],
                      "upstream_pressure_pa":pressures[i], "downstream_pressure_pa":pout,
                      "throat_static_pressure_pa":max(pout, critical*pressures[i]),
                      "mass_flow_kg_s":actual["mass_flow_abs"], "mach":actual["max_mach"],
                      "choked":actual["choked"]})
    # Classify from the actual final pressure ratio, including values immediately
    # above the critical ratio where a mass-based bisection is ill-conditioned.
    choked = teeth[-1]["choked"]
    max_mach = max(t["mach"] for t in teeth)
    min_static = tu/(1+(gamma-1)/2*max_mach**2)
    return {"mass_flow_abs":lower, "outlet_total_temperature_k":tu, "heat_w":0.0,
            "choked":choked, "max_mach":max_mach, "reynolds":None, "friction_factor":None,
            "pressure_residual_pa":0.0 if choked else abs(residual),
            "exit_static_pressure_pa":pressures[-1], "transition":False,
            "temperature_range":[min_static,tu], "teeth":teeth,
            "max_tooth_mass_residual_kg_s":max(abs(t["mass_flow_kg_s"]-lower) for t in teeth),
            "seal_model":"annular_nozzle_chain_complete_dissipation"}
