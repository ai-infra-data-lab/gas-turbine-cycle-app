"""Exchange between cooling networks and Cycle Studio at a fixed operating point."""
from copy import deepcopy
import math

from engine import solve
from gas_turbine_cycle_app.design_exchange import cycle_air, number
from gas_turbine_cycle_app.engine import run_case
from gas_turbine_cycle_app.model import cooling_circuit, element_stage


def evaluate(case, setup):
    layout = setup["turbine"]
    if layout.get("format") != "axial-turbine-cycle-handoff" or layout["source_cycle"]["input_hash"] != case["input_hash"]:
        raise ValueError("同じサイクル入力を使ったタービン連携結果を指定してください。")
    layout_case = layout["cases"][setup["turbine_case"]]
    iterate = setup.get("iterate", False)
    if type(iterate) is not bool:
        raise ValueError("サイクルとの反復を行うか指定してください。")
    if not iterate:
        return _evaluate(case, setup, layout, layout_case, {})
    if any(node.get("frame", "stationary") != "stationary" for node in setup["network"]["nodes"]):
        raise ValueError("サイクルとの反復連携では静止系の冷却回路を指定してください。")

    state, previous = {}, None
    trial = case
    history = []
    for _ in range(30):
        report = _evaluate(trial, setup, layout, layout_case, state)
        coupled_cycle = report["cycle_case"]
        if coupled_cycle is None:
            report["coupling"] = {"status": "incompatible", "iterations": history}
            return report
        mix = {row["route_index"]: row["inlet"] for row in
               coupled_cycle["result"]["stage_results"]["turbine"]["elements"] if row["kind"] == "mixing"}
        nodes = {row["id"]: row for row in report["network_result"]["model"]["nodes"]}
        new_state, p_errors, t_errors, boundaries = {}, [], [], []
        for index, injection in enumerate(setup["injections"]):
            node_id = injection["boundary_node"]
            current = nodes[node_id]
            target = mix[index]
            dp = target["static_pressure_pa"] - current["pressure_pa"]
            dt = target["total_temperature_k"] - current["temperature_k"]
            p_errors.append(abs(dp))
            t_errors.append(abs(dt))
            boundaries.append({"node_id":node_id, "station":injection["station"],
                               "pressure_difference_pa":dp, "temperature_difference_k":dt})
            new_state[node_id] = {"pressure_pa": current["pressure_pa"] + .6 * dp,
                                  "temperature_k": current["temperature_k"] + .6 * dt}
        supply = nodes[setup["supply_node"]]
        source = coupled_cycle["result"]["stage_results"]["compressor"]["stages"][setup["source_stage"]-1]["outlet"]
        p_errors.append(abs(source["total_pressure_pa"] - supply["pressure_pa"]))
        t_errors.append(abs(source["total_temperature_k"] - supply["temperature_k"]))
        boundaries.append({"node_id":supply["id"], "station":f"圧縮機第{setup['source_stage']}段抽気",
                           "pressure_difference_pa":source["total_pressure_pa"] - supply["pressure_pa"],
                           "temperature_difference_k":source["total_temperature_k"] - supply["temperature_k"]})
        flows = [row["coolant_flow_kg_s"] for row in report["injections"]]
        flow_error = None if previous is None else max(abs(a-b) for a,b in zip(flows, previous, strict=True))
        history.append({"max_pressure_difference_pa": max(p_errors),
                        "boundaries": boundaries,
                        "max_temperature_difference_k": max(t_errors), "max_flow_change_kg_s": flow_error,
                        "network_mass_residual_kg_s": report["network_result"]["summary"]["global_mass_residual_kg_s"],
                        "network_energy_residual_w": report["network_result"]["summary"]["global_energy_residual_w"],
                        "cycle_energy_balance": coupled_cycle["result"]["energy_balance"]})
        if max(p_errors) <= 2 and max(t_errors) <= .01 and flow_error is not None and flow_error <= 1e-7:
            report["cycle_feedback"] = "coupled_design_point"
            report["coupling"] = {"status": "converged", "iterations": history}
            return report
        state, previous, trial = new_state, flows, coupled_cycle
    report.update(cycle_feedback="coupled_not_converged", cycle_case=None,
                  coupling={"status": "not_converged", "iterations": history})
    return report


def _evaluate(case, setup, layout, layout_case, state_overrides):
    model = deepcopy(setup["network"])
    nodes = {row["id"]: row for row in model["nodes"]}
    frames = {node.get("frame", "stationary") for node in nodes.values()}
    rotating = frames == {"rotating"}
    if frames not in ({"stationary"}, {"rotating"}):
        raise ValueError("ネットワーク内の座標系は静止・同一回転系のいずれかに統一してください。")
    cycle_input = deepcopy(case["input"])
    results = case["result"]
    mass = results["metrics"]["air_mass_flow_kg_s"]
    compressor = results["stage_results"]["compressor"]["stages"]
    source_stage = setup["source_stage"]
    if type(source_stage) is not int or not 1 <= source_stage <= len(compressor):
        raise ValueError("有効な圧縮機の抽気段を指定してください。")
    source = compressor[source_stage-1]["outlet"]
    supply = setup["supply_node"]
    if nodes[supply]["kind"] != "boundary":
        raise ValueError("抽気接続先には圧力境界を指定してください。")
    nodes[supply].update(pressure_pa=source["total_pressure_pa"], temperature_k=source["total_temperature_k"])
    gas = cycle_air(case)
    model["gas"] = {"R": gas.r, "gamma": gas.gamma(source["total_temperature_k"])}
    frame_report = None
    if rotating:
        transfer = setup.get("matched_preswirl")
        if not isinstance(transfer, dict):
            raise ValueError("回転系への供給には周速と一致するプリスワール条件を明示してください。")
        retention = number(transfer["pressure_retention"], "プリスワール部の絶対全圧残存比", .01, 1.)
        rpm = cycle_input["parameters"]["shaft_speed_rpm"]
        for node in nodes.values():
            node["rpm"] = rpm
        gamma = model["gas"]["gamma"]
        cp = gamma*model["gas"]["R"]/(gamma-1)

        def relative_temperature(t_abs, node):
            radius = number(node["radius_m"], "回転境界の半径", 0.)
            velocity = rpm*math.tau/60*radius
            return number(t_abs-velocity**2/(2*cp), "回転系の境界相対全温", 150., 2500.)

        t_rel = relative_temperature(source["total_temperature_k"],nodes[supply])
        p_rel = source["total_pressure_pa"]*retention*(t_rel/source["total_temperature_k"])**(gamma/(gamma-1))
        nodes[supply].update(temperature_k=t_rel,pressure_pa=p_rel)
        frame_report = {"mode":"prescribed_matched_preswirl", "rpm":rpm, "pressure_retention":retention,
                        "inlet_relative_total_temperature_k":t_rel, "inlet_relative_total_pressure_pa":p_rel}

    weights = [stage["weight"] for stage in layout_case["design"]["stages"]]
    mappings = setup["injections"]
    if not mappings or len({item["boundary_node"] for item in mappings}) != len(mappings):
        raise ValueError("各注入点には別々の静止圧力境界を指定してください。")
    targets = []
    routes = {("NGV", "LE"): "before_nozzle", ("NGV", "TE"): "before_rotor",
              ("ROT", "LE"): "before_rotor", ("ROT", "TE"): "after_rotor"}
    for item in mappings:
        node_id = item["boundary_node"]
        zone = item["station"]
        point = zone.split("-")
        target = routes.get(tuple(point[2:]))
        if point[:1] != ["TB"] or target is None or not point[1].isdigit() or zone not in layout_case["stations"]:
            raise ValueError("サイクル返却用の注入点は静翼・動翼の前縁または後縁を指定してください。")
        state = layout_case["stations"][zone]["flow"]
        if node_id == supply or state is None or nodes[node_id]["kind"] != "boundary":
            raise ValueError("流れ値のあるタービン評価断面と排出側圧力境界を対応付けてください。")
        temperature = state["total_temperature_k"]
        if rotating:
            temperature = relative_temperature(temperature,nodes[node_id])
        nodes[node_id].update(pressure_pa=state["pressure"], temperature_k=temperature)
        nodes[node_id].update(state_overrides.get(node_id, {}))
        targets.append((int(point[1]), target))
    if setup.get("iterate") and len(set(targets)) != len(targets):
        raise ValueError("反復連携ではサイクル側の注入位置ごとに冷却流路を一つにまとめてください。")
    network = solve(model)
    flows = {item["id"]: item for item in network["edges"]}
    node_result = {item["id"]: item for item in network["nodes"]}
    receivers, circuits = [], []
    for item, (target_stage, injection) in zip(mappings, targets, strict=True):
        edge = flows[item["edge"]]
        if edge["downstream"] != item["boundary_node"] or edge["mass_flow_kg_s"] <= 0:
            raise ValueError("指定した冷却枝は排出境界への正方向・正流量であることを確認してください。")
        supply_pressure = node_result[edge["upstream"]]["absolute_total_pressure_pa"]
        coolant_temperature = edge["outlet_absolute_total_temperature_k"]
        circuit = cooling_circuit()
        circuit.update(name=edge["name"][:80], source_stage=source_stage, target_stage=target_stage,
                       injection=injection, fraction=edge["mass_flow_kg_s"]/mass,
                       pressure_loss_fraction=1-supply_pressure/source["total_pressure_pa"],
                       heat_j_kg=gas.h(coolant_temperature)-gas.h(source["total_temperature_k"]))
        if "mix_pressure_loss_fraction" in item:
            circuit["mix_pressure_loss_fraction"] = number(item["mix_pressure_loss_fraction"], "冷却混合の全圧損失率", 0., .1)
        circuits.append(circuit)
        receivers.append({"station":item["station"], "edge_id":item["edge"],
                          "coolant_flow_kg_s":edge["mass_flow_kg_s"], "coolant_total_temperature_k":coolant_temperature,
                          "backpressure_static_pa":nodes[item["boundary_node"]]["pressure_pa"],
                          "pre_injection_total_pressure_pa":supply_pressure})
    result = {"format":"cycle-secondary-air-handoff","schema_version":1,
              "source_cycle":layout["source_cycle"], "network_result":network, "injections":receivers,
              "frame_transfer":frame_report, "cycle_case":None,
              "operating_point":{"air_mass_flow_kg_s":mass,
                                 "shaft_speed_rpm":cycle_input["parameters"]["shaft_speed_rpm"]}}
    if rotating:
        result["cycle_feedback"] = "回転流路の軸仕事を含むため、流量・絶対全温と軸仕事を別に出力しました。"
        return result
    other_supply = any(node["id"] != supply and node["external_mass_supply_kg_s"] > 1e-8
                       for node in network["nodes"])
    if other_supply or not math.isclose(network["summary"]["supply_kg_s"],
                        math.fsum(row["coolant_flow_kg_s"] for row in receivers), rel_tol=2e-5, abs_tol=1e-8):
        result["cycle_feedback"] = "未対応の外部排出または供給があり、戻り冷却空気だけでは質量が閉じません。"
        return result
    if cycle_input["thermodynamics"]["model"] != "nasa9_frozen":
        result["cycle_feedback"] = "Cycle Studioの冷却結果反映にはNASA9物性を選んでください。"
        return result
    cycle_input.update(name="二次空気ネットワーク条件反映", mode="airflow")
    cycle_input["parameters"]["air_mass_flow_kg_s"] = mass
    cycle_input["staging"]["turbine"].update(count=len(weights), weights=weights, distribution="pressure", mean_diameters_m=None)
    turbine = cycle_input["elements"]["turbine"]
    if not turbine["enabled"] or len(turbine["stages"]) != len(weights):
        turbine.update(enabled=True, stages=[element_stage("turbine") for _ in weights])
    cycle_input["cooling"].update(enabled=True, circuits=circuits, temperature_basis="combustor_exit")
    result["return_circuits"] = circuits
    try:
        result["cycle_case"] = run_case(cycle_input)
        result["cycle_feedback"] = "design_point_once"
    except ValueError as exc:
        result["cycle_feedback"] = str(exc)
    return result
