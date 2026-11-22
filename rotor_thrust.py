"""Steady axial-force screen with explicit cavity boundaries and one axial support."""
import math

from .design_exchange import cycle_air, number, verified_cycle
from .shaft_joints import cycle_load, read_input_file, run_case


def cavity_pressure(spec, state, omega, gas, inner, outer):
    """Area-mean isothermal forced-vortex pressure, referenced at the outer radius."""
    p_outer = number(spec.get("outer_pressure_pa", state["static_pressure_pa"]), "キャビティ外周絶対静圧", 1.)
    temperature = number(spec.get("temperature_k", state["static_temperature_k"]), "キャビティ温度", 1.)
    swirl = number(spec["swirl_ratio"], "キャビティ旋回比", 0., 1.)
    decay = (omega * swirl)**2 * (outer**2 - inner**2) / (2 * gas.r * temperature)
    return p_outer if decay == 0 else p_outer * -math.expm1(-decay) / decay


def evaluate(document, setup):
    case, source = verified_cycle(document)
    train = case["result"]["stage_results"]["compressor"]
    rotors = [row for row in train.get("elements", []) if row["kind"] == "rotor"]
    if not rotors or len(setup["stages"]) != len(rotors):
        raise ValueError("圧縮機を要素モードにし、全インペラーの軸力条件を指定してください。")
    gas = cycle_air(case)
    omega = case["input"]["parameters"]["shaft_speed_rpm"] * math.tau / 60
    outputs, loads = [], []
    for config, row in zip(setup["stages"], rotors, strict=True):
        stages = row["stage"]
        if type(config["stage"]) is not int or config["stage"] != stages:
            raise ValueError("インペラーの段番号を流れ順に指定してください。")
        sign = number(config["axis_sign"], "軸方向の符号")
        if sign not in (-1, 1):
            raise ValueError("軸方向の符号は -1 または +1 です。")
        inlet, outlet = row["inlet"], row["outlet"]
        region_results = []
        for region in config["cavities"]:
            inner = number(region["inner_radius_m"], "キャビティ内半径・シール径の半径", 0.)
            outer = number(region["outer_radius_m"], "キャビティ外半径", 1e-6)
            if inner >= outer:
                raise ValueError("キャビティ外半径は内半径より大きくしてください。")
            pressures = []
            for side in ("back", "front"):
                spec = region[side]
                reference = {"inlet": inlet, "outlet": outlet}.get(spec["reference"])
                if reference is None:
                    raise ValueError("キャビティの基準静圧は inlet または outlet を選んでください。")
                pressures.append(cavity_pressure(spec, reference, omega, gas, inner, outer))
            area = math.pi * (outer**2 - inner**2)
            region_results.append({"area_m2": area, "back_mean_pa": pressures[0], "front_mean_pa": pressures[1],
                                   "force_n": sign * (pressures[0] - pressures[1]) * area})
        if not region_results:
            raise ValueError("インペラー前面・背面の受圧領域を指定してください。")
        projected = number(config["inlet_axial_projection"], "入口の軸方向方向余弦", -1., 1.)
        projected_out = number(config["outlet_axial_projection"], "出口の軸方向方向余弦", -1., 1.)
        mass = inlet["mass_flow_kg_s"]
        if not math.isclose(mass, outlet["mass_flow_kg_s"], rel_tol=1e-8):
            raise ValueError("インペラー入口・出口の流量が一致しません。")
        pressure = sign * (inlet["static_pressure_pa"] * inlet["effective_flow_area_m2"] * projected
                           - outlet["static_pressure_pa"] * outlet["effective_flow_area_m2"] * projected_out)
        momentum = sign * mass * (inlet["velocity_m_s"] * projected - outlet["velocity_m_s"] * projected_out)
        load = {"name": f"インペラー第{stages}段", "x_m": number(config["x_m"], "軸上位置"),
                "force_n": math.fsum(item["force_n"] for item in region_results) + pressure + momentum}
        loads.append(load)
        outputs.append({"stage": stages, "inlet_static_pa": inlet["static_pressure_pa"],
                        "outlet_static_pa": outlet["static_pressure_pa"], "cavity_forces": region_results,
                        "gas_path_pressure_n": pressure, "axial_momentum_n": momentum, **load})
    for item in setup.get("external_loads", []):
        loads.append({"name": item["name"], "x_m": number(item["x_m"], "外部荷重位置"),
                      "force_n": number(item["force_n"], "外部荷重")})
    bearing = {"name": "スラスト軸受反力", "x_m": number(setup["bearing_x_m"], "軸受位置"),
               "force_n": -math.fsum(item["force_n"] for item in loads)}
    loads.append(bearing)
    joints = []
    for joint in setup["joints"]:
        x = number(joint["cut_x_m"], "継手断面位置")
        if any(load["x_m"] == x for load in loads):
            raise ValueError("継手断面は荷重作用点と分けて指定してください。")
        tension = -math.fsum(load["force_n"] for load in loads if load["x_m"] < x)
        strength = read_input_file(joint["joint"])
        strength.pop("thrust", None)
        if "power_position" in joint:
            strength["load"].update(cycle_load(case, joint["power_position"]))
        strength["load"].update(axial_force_n=tension)
        joints.append({"x_m": x, "tension_n": tension, "strength_case": run_case(strength)})
    return {"format": "gas-turbine-rotor-thrust", "schema_version": 1, "source_cycle": source,
            "stages": outputs, "loads": loads, "bearing_reaction": bearing, "joints": joints,
            "axial_force_residual_n": math.fsum(load["force_n"] for load in loads)}
