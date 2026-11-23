"""Steady, calorically perfect gas network. Standard library only.

Nodes are zero-velocity plenums. Pipe inlet is isentropic; pipe outlet static
pressure matches a downstream plenum unless choked. All coefficients are Darcy.
"""
import math
from model import ModelError, validate
from rotating import kinetic, initialize_temperatures, rotating_branch


class CalculationError(ValueError):
    pass


class ConvergenceError(CalculationError):
    def __init__(self, message, phase, nodes, residuals, tolerances):
        super().__init__(message)
        overruns = []
        for node in nodes:
            key = node["id"]
            if key not in tolerances:
                continue
            residual, tolerance = residuals[key], tolerances[key]
            if abs(residual) > tolerance:
                overruns.append({
                    "node_id": key, "node_name": node["name"],
                    "residual": residual, "tolerance": tolerance,
                })
        overruns.sort(key=lambda row: abs(row["residual"]) / row["tolerance"], reverse=True)
        self.diagnostics = {"phase": phase, "nodes": overruns}


def cp_of(gas):
    return gas["gamma"] * gas["R"] / (gas["gamma"] - 1)


def orifice(pu, tu, pd, area, cd, gas):
    gamma, r = gas["gamma"], gas["R"]
    critical = (2 / (gamma + 1)) ** (gamma / (gamma - 1))
    ratio = pd / pu
    x = max(ratio, critical)
    # expm1 avoids cancellation at a small pressure difference.
    term = x ** (2 / gamma) * (-math.expm1((gamma - 1) / gamma * math.log(x)))
    flow = cd * area * pu / math.sqrt(r * tu) * math.sqrt(max(0, 2 * gamma / (gamma - 1) * term))
    mach = min(1.0, math.sqrt(max(0, 2 / (gamma - 1) * (x ** (-(gamma - 1) / gamma) - 1))))
    return {"mass_flow_abs": flow, "outlet_total_temperature_k": tu, "heat_w": 0.0,
            "choked": ratio <= critical, "max_mach": mach, "reynolds": None,
            "friction_factor": None, "pressure_residual_pa": 0.0,
            "exit_static_pressure_pa": max(pd, critical * pu), "transition": False,
            "temperature_range": [tu, tu]}


def viscosity(t):
    return 1.716e-5 * (t / 273.15) ** 1.5 * (273.15 + 110.4) / (t + 110.4)


def friction(re, rough, diameter):
    if re <= 0:
        return 0.0
    if re <= 2300:
        return 64 / re
    turbulent = (-1.8 * math.log10((rough / diameter / 3.7) ** 1.11 + 6.9 / re)) ** -2
    if re >= 4000:
        return turbulent
    w = (re - 2300) / 1700
    return (1 - w) * 64 / re + w * turbulent


def _inlet(mass, pu, tu, area, gas):
    """Solve subsonic reservoir-to-area mass flux, via M² Newton iteration."""
    g, r = gas["gamma"], gas["R"]
    a, b = (g - 1) / 2, (g + 1) / (2 * (g - 1))
    target = mass / area * math.sqrt(r * tu) / (pu * math.sqrt(g))
    lo, hi = 0.0, 1.0
    x = min(0.95, target * target * 1.3)
    for _ in range(22):
        f = x * (1 + a * x) ** (-2 * b)
        if f < target * target:
            lo = x
        else:
            hi = x
        df = (1 + a * x) ** (-2 * b) * (1 - 2 * b * a * x / (1 + a * x))
        candidate = x - (f - target * target) / max(df, 1e-20)
        if abs(f - target * target) < 1e-14 * max(target * target, 1e-10):
            break
        x = candidate if lo < candidate < hi else (lo + hi) / 2
    ts = tu / (1 + a * x)
    velocity = math.sqrt(x * g * r * ts)
    ps = pu * (ts / tu) ** (g / (g - 1))
    return velocity, ps


def _march(mass, pu, tu, e, gas, reverse=False):
    """Finite-volume momentum/energy march, subsonic root at each station.

    dp + G*dV + fD*(dx/D)*G*Vmean/2 = 0
    cp*dT0 = dq/m ; T = T0 - V²/(2cp).
    Wall exchange uses exponential integration with the cell-inlet kinetic term.
    """
    area, d = e["area_m2"], e["diameter_m"]
    r, gamma, cp = gas["R"], gas["gamma"], cp_of(gas)
    flux = mass / area
    v, p = _inlet(mass, pu, tu, area, gas)
    t0 = tu
    n, dx = e["cells"], e["length_m"] / e["cells"]
    kval = e["k_reverse"] if reverse else e["k"]
    inlet_static = t0 - v * v / (2 * cp)
    max_mach = v / math.sqrt(gamma * r * inlet_static)
    re_sum, f_sum, qsum = 0.0, 0.0, 0.0
    tmin, tmax, transition = min(tu, inlet_static), tu, False
    for _ in range(n):
        ts = t0 - v * v / (2 * cp)
        if not 100 <= ts <= 3000:
            return None
        re = flux * d / viscosity(ts)
        f = e["friction_factor"] if e["friction"] == "fixed" else friction(re, e["roughness_m"], d)
        transition |= 2300 < re < 4000 and e["friction"] == "auto"
        resistance = f * dx / d + kval / n
        ua = e["htc_w_m2k"] * (4 * area / d) * dx
        x = ua / (mass * cp)
        decay = math.exp(-min(x, 700))
        effectiveness = -math.expm1(-min(x, 700))
        # Uniform specified heat and convective exchange in the same cell.
        heat_factor = effectiveness / x if x > 1e-12 else 1.0
        tnew = t0 + (e["wall_temperature_k"] + v * v / (2 * cp) - t0) * effectiveness
        tnew += e["heat_w"] / n / (mass * cp) * heat_factor
        if not 150 <= tnew <= 2500:
            return None
        aa = 1 - r / (2 * cp) + resistance / 4
        bb = -p / flux - v + resistance * v / 4
        cc = r * tnew
        disc = bb * bb - 4 * aa * cc
        if disc <= 0 or bb >= 0:
            return None
        vnew = 2 * cc / (-bb + math.sqrt(disc))
        tsnew = tnew - vnew * vnew / (2 * cp)
        if tsnew <= 0:
            return None
        mach = vnew / math.sqrt(gamma * r * tsnew)
        if mach >= 1:
            return None
        p = flux * r * tsnew / vnew
        qsum += mass * cp * (tnew - t0)
        v, t0 = vnew, tnew
        max_mach = max(max_mach, mach)
        tmin, tmax = min(tmin, tsnew, tnew), max(tmax, tsnew, tnew)
        re_sum += re
        f_sum += f
    return {"mass_flow_abs": mass, "outlet_total_temperature_k": t0,
            "exit_static_pressure_pa": p, "heat_w": qsum, "max_mach": max_mach,
            "reynolds": re_sum / n, "friction_factor": f_sum / n,
            "transition": transition, "temperature_range": [tmin, tmax]}


def pipe(pu, tu, pd, e, gas, reverse=False):
    upper = orifice(pu, tu, pu * 0.1, e["area_m2"], 1.0, gas)["mass_flow_abs"]
    # Heating can create a minimum flow for the supported temperature range.
    # Start above it; failures below this point are never called choking.
    available_dt = (2500 - tu) if e["heat_w"] >= 0 else (tu - 150)
    lower = max(1e-16, abs(e["heat_w"]) / (cp_of(gas) * max(available_dt, 1e-12)) * 1.001)
    if lower >= upper:
        raise CalculationError(f"{e['id']}: 熱入力に対して流量が不足し、150～2500 Kの定常解を得られません。")
    best = _march(lower, pu, tu, e, gas, reverse)
    if best is None or best["exit_static_pressure_pa"] < pd:
        raise CalculationError(f"{e['id']}: 熱入力・圧力差に対する亜音速定常解がありません。熱入力または境界条件を確認してください。")
    for _ in range(35):
        mass = (lower + upper) / 2
        out = _march(mass, pu, tu, e, gas, reverse)
        if out is not None and out["exit_static_pressure_pa"] >= pd:
            lower, best = mass, out
        else:
            upper = mass
        if upper - lower < max(1e-13, upper * 2e-9):
            break
    residual = best["exit_static_pressure_pa"] - pd
    best["choked"] = residual > max(0.5, pu * 2e-6) and best["max_mach"] > 0.98
    if residual > max(0.5, pu * 2e-6) and not best["choked"]:
        raise CalculationError(f"{e['id']}: 配管の定常状態を解けません。分割数または熱条件を確認してください。")
    best["pressure_residual_pa"] = 0.0 if best["choked"] else residual
    return best


def branch(e, pressures, temperatures, gas, nodes=None):
    if e["kind"] == "rotating_pipe":
        out=rotating_branch(e,pressures,temperatures,gas,nodes)
        return decorate(e,out,gas,nodes)
    a, b = e["from"], e["to"]
    reverse = pressures[a] < pressures[b]
    up, down = (b, a) if reverse else (a, b)
    pu, pd, tu = pressures[up], pressures[down], temperatures[up]
    if abs(pu - pd) <= max(pu, pd) * 1e-13:
        if e["heat_w"] != 0:
            raise CalculationError(f"{e['id']}: 圧力差ゼロの流路に熱入力があります。")
        out = {"mass_flow_abs": 0.0, "outlet_total_temperature_k": None,
               "heat_w": 0.0, "choked": False, "max_mach": 0.0, "reynolds": None,
               "friction_factor": None, "pressure_residual_pa": 0.0,
               "exit_static_pressure_pa": pd, "transition": False, "temperature_range": [tu, tu]}
    elif e["kind"] == "labyrinth":
        from seals import labyrinth
        out = labyrinth(pu, tu, pd, e, gas, reverse)
    elif e["kind"] in ("orifice", "leak"):
        out = orifice(pu, tu, pd, e["area_m2"], e["cd_reverse"] if reverse else e["cd"], gas)
    else:
        out = pipe(pu, tu, pd, e, gas, reverse)
    out=dict(out, id=e["id"], name=e["name"], kind=e["kind"], upstream=up,
                downstream=down, mass_flow_kg_s=(-1 if reverse else 1) * out["mass_flow_abs"],
                reversed=reverse, inlet_total_temperature_k=tu)
    return decorate(e,out,gas,nodes)


def decorate(e,out,gas,nodes):
    cp=cp_of(gas)
    up=nodes[out["upstream"]] if nodes else {}
    down=nodes[out["downstream"]] if nodes else {}
    out.update(id=e["id"],name=e["name"],kind=e["kind"],
               reference_frame=up.get("frame","stationary"),rpm=up.get("rpm",0.),
               inlet_radius_m=up.get("radius_m",0.),outlet_radius_m=down.get("radius_m",0.),
               inlet_absolute_total_temperature_k=out["inlet_total_temperature_k"]+kinetic(up)/cp,
               outlet_absolute_total_temperature_k=None if out["outlet_total_temperature_k"] is None else out["outlet_total_temperature_k"]+kinetic(down)/cp)
    out.setdefault("shaft_work_w",0.)
    return out


def linear_solve(matrix, rhs):
    """Partial-pivot Gaussian elimination for the network Jacobian."""
    n = len(rhs)
    a = [list(row) + [rhs[i]] for i, row in enumerate(matrix)]
    for k in range(n):
        pivot = max(range(k, n), key=lambda i: abs(a[i][k]))
        if abs(a[pivot][k]) < 1e-20:
            raise CalculationError("ネットワークの方程式が特異です。境界と接続を確認してください。")
        a[k], a[pivot] = a[pivot], a[k]
        pivot_row = a[k]
        for i in range(k + 1, n):
            row = a[i]
            if row[k] == 0.0:
                continue
            ratio = row[k] / pivot_row[k]
            for j in range(k + 1, n + 1):
                row[j] -= ratio * pivot_row[j]
            row[k] = 0
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (a[i][n] - sum(a[i][j] * x[j] for j in range(i + 1, n))) / a[i][i]
    return x


def balances(nodes, results, temperatures, cp):
    mass, incoming, outgoing, energy = ({n["id"]: 0.0 for n in nodes} for _ in range(4))
    node_byid={n["id"]:n for n in nodes}
    for r in results:
        a, b, flow = r["upstream"], r["downstream"], r["mass_flow_abs"]
        mass[a] -= flow
        mass[b] += flow
        outgoing[a] += flow
        incoming[b] += flow
        energy[a] -= flow * (cp * temperatures[a]+kinetic(node_byid[a]))
        if flow:
            energy[b] += flow * cp * r.get("outlet_absolute_total_temperature_k",r["outlet_total_temperature_k"])
    for n in nodes:
        energy[n["id"]] += n["heat_w"]
    return mass, incoming, outgoing, energy


def _energy_tolerance(thermal_enthalpy, flow):
    return 1e-5 + 1e-7 * thermal_enthalpy * flow


def _pressure_solve(m, p, t, trace, phase="pressure"):
    internal = [n["id"] for n in m["nodes"] if n["kind"] == "junction"]
    index = {key: i for i, key in enumerate(internal)}
    cp = cp_of(m["gas"])
    bps = [n["pressure_pa"] for n in m["nodes"] if n["kind"] == "boundary"]
    pmin, pmax = min(bps), max(bps)
    if any(e["kind"]=="rotating_pipe" for e in m["edges"]):pmin,pmax=1000.,50e6
    byid={n["id"]:n for n in m["nodes"]}
    thermal_h = {key: cp * t[key] for key in internal}
    absolute_h = {key: thermal_h[key] + kinetic(byid[key]) for key in internal}
    for iteration in range(46):
        results = [branch(e, p, t, m["gas"],byid) for e in m["edges"]]
        mass, inc, out, _ = balances(m["nodes"], results, t, cp)
        flow = {key: max(inc[key], out[key]) for key in internal}
        # Reserve half of the energy tolerance for mass imbalance times absolute
        # enthalpy so the temperature iteration can close the remaining budget.
        tolerances = {
            key: min(1e-9 + 1e-7 * flow[key],
                     0.5 * _energy_tolerance(thermal_h[key], flow[key]) / absolute_h[key])
            for key in internal
        }
        if all(abs(mass[key]) <= tolerances[key] for key in internal):
            return results
        if iteration == 45:
            raise ConvergenceError("圧力計算が45反復で収束しませんでした。",
                                   phase, m["nodes"], mass, tolerances)
        jac = [[0.0] * len(internal) for _ in internal]
        for e, result in zip(m["edges"], results):
            for colkey in (e["from"], e["to"]):
                if colkey not in index:
                    continue
                step = max(0.02, p[colkey] * 1e-6)
                pp = dict(p)
                pp[colkey] += step
                pert = branch(e, pp, t, m["gas"],byid)
                dm = (pert["mass_flow_kg_s"] - result["mass_flow_kg_s"]) / step
                for rowkey, sign in ((e["from"], -1), (e["to"], 1)):
                    if rowkey in index:
                        jac[index[rowkey]][index[colkey]] += sign * dm
        try:
            delta = linear_solve(jac, [-mass[k] for k in internal])
        except CalculationError as exc:
            raise ConvergenceError(str(exc), phase, m["nodes"], mass, tolerances) from exc
        old_abs = sum(mass[k] ** 2 for k in internal)
        accepted = False
        for line in range(18):
            damping = 0.5 ** line
            trial = dict(p)
            for key, dx in zip(internal, delta):
                trial[key] = min(pmax, max(pmin, p[key] + damping * dx))
            try:
                rr = [branch(e, trial, t, m["gas"],byid) for e in m["edges"]]
                mm = balances(m["nodes"], rr, t, cp)[0]
            except CalculationError:
                continue
            if sum(mm[k] ** 2 for k in internal) < old_abs:
                p.update(trial)
                accepted = True
                break
        if not accepted:
            raise ConvergenceError("圧力反復が停滞しました。初期圧力・熱入力・接続を確認してください。",
                                   phase, m["nodes"], mass, tolerances)
        trace["pressure_iterations"] += 1


def solve(raw):
    m = validate(raw)
    gas, nodes = m["gas"], m["nodes"]
    cp = cp_of(gas)
    p = {n["id"]: n["pressure_pa"] for n in nodes}
    t = {n["id"]: n["temperature_k"] for n in nodes}
    initialize_temperatures(m,t)
    internal = [n for n in nodes if n["kind"] == "junction"]
    # Avoid initial zero pressure differences in otherwise driven networks.
    boundaries = [n["pressure_pa"] for n in nodes if n["kind"] == "boundary"]
    pmin, pmax = min(boundaries), max(boundaries)
    if pmax > pmin:
        for i, n in enumerate(internal):
            if p[n["id"]] in (pmin, pmax):
                p[n["id"]] = pmin + (pmax - pmin) * (0.4 + 0.2 * (i + 1) / (len(internal) + 1))
    trace = {"pressure_iterations": 0, "thermal_iterations": 0}
    if any(e["heat_w"] != 0 for e in m["edges"]):
        # A zero initial pressure difference must not invalidate an otherwise
        # feasible heated network. First find a cold hydraulic pressure field.
        import copy
        cold = copy.deepcopy(m)
        for e in cold["edges"]:
            e["heat_w"] = 0
            if e["kind"] in ("pipe", "resistance"):
                e["htc_w_m2k"] = 0
        _pressure_solve(cold, p, t, trace, phase="cold_pressure")
    for thermal in range(101):
        results = _pressure_solve(m, p, t, trace)
        mass, inc, out, energy = balances(nodes, results, t, cp)
        tolerances = {
            key: _energy_tolerance(cp * t[key], max(inc[key], out[key]))
            for key in (n["id"] for n in internal)
        }
        if all(abs(energy[key]) <= tolerance for key, tolerance in tolerances.items()):
            break
        if thermal == 100:
            raise ConvergenceError("温度と流量の連成計算が100反復で収束しませんでした。",
                                   "thermal", nodes, energy, tolerances)
        newt = dict(t)
        for n in internal:
            key = n["id"]
            if inc[key] <= 1e-14:
                if abs(n["heat_w"]) > 1e-10:
                    raise ConvergenceError(f"{key}: 流入ゼロの混合室に熱入力があり、定常解がありません。",
                                           "thermal", nodes, energy, tolerances)
                continue
            incoming_enthalpy = sum(r["mass_flow_abs"] * cp * r["outlet_absolute_total_temperature_k"]
                                    for r in results if r["downstream"] == key and r["mass_flow_abs"] > 0)
            target = (incoming_enthalpy + n["heat_w"]) / (inc[key] * cp)-kinetic(n)/cp
            if not 150 <= target <= 2500:
                raise ConvergenceError(f"{key}: 混合温度が150～2500 Kを外れます。",
                                       "thermal", nodes, energy, tolerances)
            newt[key] = t[key] + 0.8 * (target - t[key])
        if newt == t:
            raise ConvergenceError("温度反復が停滞しました。熱入力・流量・境界条件を確認してください。",
                                   "thermal", nodes, energy, tolerances)
        t = newt
        trace["thermal_iterations"] = thermal + 1
    warnings = ["比熱一定の空気・各座標系で速度ゼロの完全混合室モデル。実機／市販ソフトとの数値一致は未検証です。"]
    node_results = []
    for n in nodes:
        key, boundary = n["id"], n["kind"] == "boundary"
        stagnant = not boundary and max(inc[key], out[key]) < 1e-14
        if stagnant:
            warnings.append(f"{key}: 流量ゼロのため混合温度は不定です。")
        node_results.append({"id": key, "name": n["name"], "kind": n["kind"], "pressure_pa": p[key],
                             "temperature_k": None if stagnant else t[key], "inflow_kg_s": inc[key],
                             "outflow_kg_s": out[key], "mass_residual_kg_s": None if boundary else mass[key],
                             "energy_residual_w": None if boundary else energy[key],
                             "external_mass_supply_kg_s": out[key] - inc[key] if boundary else 0,
                             "external_energy_supply_w": -energy[key] if boundary else 0})
        abs_t=t[key]+kinetic(n)/cp
        node_results[-1].update(frame=n["frame"],rpm=n["rpm"],radius_m=n["radius_m"],
                                static_temperature_k=None if stagnant else t[key],
                                absolute_total_temperature_k=None if stagnant else abs_t,
                                absolute_total_pressure_pa=None if stagnant else p[key]*(abs_t/t[key])**(gas["gamma"]/(gas["gamma"]-1)))
        if abs_t>2500:raise CalculationError(f"{key}: 絶対全温が2500 Kを超えます。")
    for e, r in zip(m["edges"], results):
        if not e["source"].strip():
            warnings.append(f"{e['id']}: 係数・形状の出典が未記入です。")
        if e["kind"] == "leak":
            warnings.append(f"{e['id']}: 校正用の等価絞りです。ラビリンス形状の漏れ予測は行いません。")
        if e["kind"] == "labyrinth":
            warnings.append(f"{e['id']}: 歯ごとの環状絞り・歯間完全散逸モデル。持越し、歯形相関、ハニカム、回転風損は未考慮です。")
        if e["kind"]=="rotating_pipe":
            warnings.append(f"{e['id']}: 完全追従・同一回転系の断熱流路。摩擦相関の回転補正、スリップ、回転風損は未考慮。分割数依存を確認してください。")
        if r["transition"]:
            warnings.append(f"{e['id']}: Re=2300～4000の摩擦係数は遷移域の補間です。")
        if r["max_mach"] > 0.8 and e["kind"] in ("pipe", "resistance"):
            warnings.append(f"{e['id']}: 高マッハ数です。分割数を増やして結果の変化を確認してください。")
        if e["kind"] in ("pipe", "resistance") and e["friction"] == "auto":
            circular_area = math.pi * e["diameter_m"] ** 2 / 4
            if abs(e["area_m2"] / circular_area - 1) > 0.01:
                warnings.append(f"{e['id']}: 非円形流路の自動層流係数は円管近似です。")
        if r["temperature_range"][0] < 250 or r["temperature_range"][1] > 800:
            warnings.append(f"{e['id']}: 250～800 Kを外れます。比熱一定近似の感度を確認してください。")
    boundary_mass = sum(n["external_mass_supply_kg_s"] for n in node_results)
    boundary_energy = sum(n["external_energy_supply_w"] for n in node_results)
    total_heat = sum(r["heat_w"] for r in results) + sum(n["heat_w"] for n in nodes)
    shaft_work=sum(r["shaft_work_w"] for r in results)
    return {"status": "converged", "model": m, "gas": dict(gas, cp_j_kgk=cp),
            "nodes": node_results, "edges": results, "iterations": trace, "warnings": warnings,
            "summary": {"supply_kg_s": sum(max(0, n["external_mass_supply_kg_s"]) for n in node_results),
                        "heat_w": total_heat, "choked_count": sum(r["choked"] for r in results),
                        "global_mass_residual_kg_s": boundary_mass,
                        "shaft_work_w":shaft_work,
                        "global_energy_residual_w": boundary_energy + total_heat + shaft_work,
                        "max_rothalpy_residual_j_kg":max((abs(r.get("rothalpy_residual_j_kg",0.)) for r in results),default=0.),
                        "max_rotating_energy_residual_w":max((abs(r.get("rotating_energy_residual_w",0.)) for r in results),default=0.),
                        "max_mass_residual_kg_s": max((abs(mass[n["id"]]) for n in internal), default=0),
                        "max_energy_residual_w": max((abs(energy[n["id"]]) for n in internal), default=0),
                        "max_branch_pressure_residual_pa": max(r["pressure_residual_pa"] for r in results)}}


def sweep(raw, target, values):
    """One parameter, independently solved operating points; failed points explicit."""
    import copy
    if not isinstance(target, dict) or not isinstance(values, list) or not 2 <= len(values) <= 15:
        raise ModelError("感度解析は2～15点、対象を指定してください。")
    group, ident, field = target.get("group"), target.get("id"), target.get("field")
    allowed = {"nodes": {"pressure_pa", "temperature_k", "heat_w"},
               "rotation":{"rpm"},
               "edges": {"area_m2", "cd", "cd_reverse", "length_m", "k", "k_reverse", "wall_temperature_k", "htc_w_m2k", "heat_w", "friction_factor", "clearance_m", "seal_diameter_m", "tooth_count"}}
    if group not in allowed or field not in allowed[group]:
        raise ModelError("感度解析の対象項目が不正です。")
    base = validate(raw)
    matches = [obj for obj in base["nodes" if group=="rotation" else group] if obj["id"] == ident]
    if not matches or field not in matches[0]:
        raise ModelError("感度解析の対象がありません。")
    rotation_ids=set()
    if group=="rotation":
        if matches[0]["frame"]!="rotating":raise ModelError("回転数の掃引対象は回転ノードです。")
        queue=[ident]
        while queue:
            key=queue.pop()
            if key in rotation_ids:continue
            rotation_ids.add(key)
            for e in base["edges"]:
                if e["from"]==key:queue.append(e["to"])
                if e["to"]==key:queue.append(e["from"])
    if group == "edges" and matches[0]["kind"] == "labyrinth":
        if field == "area_m2":
            raise ModelError("ラビリンス面積は平均径と隙間から決まります。隙間を変更してください。")
        if field == "clearance_m" and matches[0]["tooth_clearances_m"]:
            raise ModelError("個別隙間が設定されています。全歯隙間の感度解析には個別隙間を空にしてください。")
    if group == "nodes" and matches[0]["kind"] == "junction" and field != "heat_w":
        raise ModelError("内部ノードの圧力・温度は初期値です。運転点には境界ノードを選んでください。")
    points = []
    for value in values:
        candidate = copy.deepcopy(base)
        if group=="rotation":
            for n in candidate["nodes"]:
                if n["id"] in rotation_ids:n["rpm"]=value
        else:next(obj for obj in candidate[group] if obj["id"] == ident)[field] = value
        try:
            result = solve(candidate)
            points.append({"value": value, "status": "converged", "summary": result["summary"],
                           "nodes": result["nodes"], "edges": result["edges"]})
        except (ModelError, CalculationError) as exc:
            points.append({"value": value, "status": "failed", "error": str(exc)})
    return {"target": target, "points": points}
