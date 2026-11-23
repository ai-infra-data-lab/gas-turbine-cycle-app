"""Feed solved stationary cavity boundary conditions into the independent thrust model."""
from copy import deepcopy
import math

from engine import solve
from gas_turbine_cycle_app.design_exchange import cycle_air, cycle_source, number
from gas_turbine_cycle_app.rotor_thrust import evaluate_case


def evaluate(case, setup):
    thrust = setup["thrust"]
    model = deepcopy(setup["network"])
    nodes = {node["id"]: node for node in model["nodes"]}
    if any(node.get("frame", "stationary") != "stationary" for node in nodes.values()):
        raise ValueError("キャビティ外周静圧の連携には静止系の漏れ回路を指定してください。")
    train = case["result"]["stage_results"]["compressor"]
    rotors = {row["stage"]: row for row in train.get("elements", []) if row["kind"] == "rotor"}
    stages = {row["stage"]: row for row in thrust["stages"]}
    if not rotors or rotors.keys() != stages.keys():
        raise ValueError("圧縮機の全インペラーと軸力計算の段番号を照合してください。")
    source_nodes, temperatures = set(), []
    for link in setup["sources"]:
        node_id = link["node"]
        node = nodes[node_id]
        if node_id in source_nodes or node["kind"] != "boundary" or link["position"] not in ("inlet", "outlet"):
            raise ValueError("圧縮機断面を互いに異なる圧力境界へ割り当ててください。")
        state = rotors[link["stage"]][link["position"]]
        node.update(pressure_pa=state["static_pressure_pa"], temperature_k=state["static_temperature_k"])
        source_nodes.add(node_id)
        temperatures.append(node["temperature_k"])
    if not temperatures:
        raise ValueError("漏れ回路の圧縮機境界を指定してください。")
    gas = cycle_air(case)
    model["gas"] = {"R": gas.r, "gamma": gas.gamma(math.fsum(temperatures) / len(temperatures))}

    expected = {(i, j, side) for i, stage in stages.items()
                for j in range(1, len(stage["cavities"]) + 1) for side in ("front", "back")}
    references = {}
    for link in setup["cavities"]:
        key = (link["stage"], link["region"], link["side"])
        node_id = link["node"]
        if key not in expected or key in references or nodes[node_id]["kind"] != "junction":
            raise ValueError("各受圧面の外周圧力に混合室を一つ指定してください。")
        references[key] = node_id
    if references.keys() != expected:
        raise ValueError("すべての受圧面の外周圧力を指定してください。")

    variants = setup["cases"]
    if not isinstance(variants, dict) or not 1 <= len(variants) <= 12:
        raise ValueError("シール隙間の比較は1〜12案です。")
    source = cycle_source(case)
    cases = {}
    for name, clearances in variants.items():
        trial = deepcopy(model)
        seals = {edge["id"]: edge for edge in trial["edges"] if edge["kind"] == "labyrinth"}
        for edge_id, clearance in clearances.items():
            if edge_id not in seals or seals[edge_id].get("tooth_clearances_m"):
                raise ValueError("比較するラビリンスには全歯共通の隙間を指定してください。")
            seals[edge_id]["clearance_m"] = number(clearance, "シール半径隙間", 1e-8)
        network = solve(trial)
        calculated = {row["id"]: row for row in network["nodes"]}
        config = deepcopy(thrust)
        config_stages = {row["stage"]: row for row in config["stages"]}
        inputs = []
        for (i, j, side), node_id in references.items():
            state = calculated[node_id]
            if state["temperature_k"] is None:
                raise ValueError(f"{node_id} の流れが停滞し、キャビティ温度が定まりません。")
            values = {"outer_pressure_pa": state["pressure_pa"], "temperature_k": state["temperature_k"]}
            config_stages[i]["cavities"][j-1][side].update(values)
            inputs.append({"stage":i, "region":j, "side":side, "node":node_id, **values})
        cases[name] = {"network_result":network, "cavity_inputs":inputs,
                       "thrust":evaluate_case(case, config, source)}
    return {"format":"cycle-seal-thrust-handoff", "schema_version":1, "source_cycle":source, "cases":cases}
