"""Compare two-stage impeller and shaft loads with one input changed at a time."""

from copy import deepcopy

from .design_exchange import cycle_source, number, verified_cycle
from .engine import run_case
from .rotor_thrust import evaluate_case


def summarize(result):
    return {
        "stages": [
            {"stage": row["stage"], "force_n": row["force_n"],
             "static_rise_pa": row["outlet_static_pa"] - row["inlet_static_pa"]}
            for row in result["stages"]
        ],
        "bearing_reaction_n": result["bearing_reaction"]["force_n"],
        "joints": [
            {"kind": row["strength_case"]["input"]["kind"], "x_m": row["x_m"],
             "tension_n": row["tension_n"]}
            for row in result["joints"]
        ],
        "axial_force_residual_n": result["axial_force_residual_n"],
    }


def sweep_target(setup, case, sweep):
    stage = sweep.get("stage")
    cavity = sweep.get("cavity")
    parameter = sweep.get("parameter")
    side = sweep.get("side")
    if type(stage) is not int or stage not in (1, 2):
        raise ValueError("感度分析の段番号は1または2を指定してください。")
    regions = setup["stages"][stage - 1]["cavities"]
    if type(cavity) is not int or not 1 <= cavity <= len(regions):
        raise ValueError(f"第{stage}段の受圧領域番号を確認してください。")
    region = regions[cavity - 1]
    if parameter == "inner_radius_m":
        if side is not None:
            raise ValueError("受圧内半径の変更では前面・背面を指定しません。")
        return region, region[parameter]
    if side not in ("front", "back") or parameter not in ("outer_pressure_pa", "swirl_ratio"):
        raise ValueError("前面・背面と、外周絶対静圧または旋回比を指定してください。")
    spec = region[side]
    if parameter in spec:
        return spec, spec[parameter]
    rotor = next(
        row for row in case["result"]["stage_results"]["compressor"]["elements"]
        if row["kind"] == "rotor" and row["stage"] == stage
    )
    return spec, rotor[spec["reference"]]["static_pressure_pa"]


def evaluate(document, settings):
    base_case, source = verified_cycle(document)
    base = base_case["input"]
    if not base["elements"]["compressor"]["enabled"] or base["staging"]["compressor"]["count"] != 2:
        raise ValueError("感度分析には2段・要素モードの圧縮機を指定してください。")
    setup = settings["thrust"]
    shares = settings["stage1_shares"]
    sweeps = settings["sweeps"]
    if not isinstance(shares, list) or not shares:
        raise ValueError("比較する第1段の対数全圧比分配を指定してください。")
    shares = [number(share, "第1段の対数全圧比分配", .05, .95) for share in shares]
    if len(set(shares)) != len(shares):
        raise ValueError("同じ第1段配分を重複して指定できません。")
    if not isinstance(sweeps, list) or not sweeps:
        raise ValueError("変更する受圧条件を指定してください。")

    baseline_input = deepcopy(base)
    baseline_input["mode"] = "airflow"
    baseline_input["parameters"]["air_mass_flow_kg_s"] = base_case["result"]["metrics"]["air_mass_flow_kg_s"]
    cases = []
    for share in shares:
        baseline_input["staging"]["compressor"]["weights"] = [share, 1 - share]
        case = run_case(baseline_input)
        reference = cycle_source(case)
        baseline = summarize(evaluate_case(case, setup, reference))
        points = []
        for sweep in sweeps:
            if not isinstance(sweep, dict):
                raise ValueError("感度分析の変更条件はオブジェクトで指定してください。")
            offsets = sweep.get("offsets")
            if not isinstance(offsets, list) or not offsets:
                raise ValueError("基準値からの変更量を指定してください。")
            parameter = sweep.get("parameter")
            modified = deepcopy(setup)
            target, initial = sweep_target(modified, case, sweep)
            for offset in offsets:
                offset = number(offset, "基準値からの変更量")
                value = initial + offset
                target[parameter] = value
                try:
                    result = summarize(evaluate_case(case, modified, reference))
                except ValueError as exc:
                    raise ValueError(
                        f"第1段配分{share:.1%}・第{sweep['stage']}段・受圧領域{sweep['cavity']}・"
                        f"{parameter}の設定値{value:.6g}: {exc}"
                    ) from exc
                points.append({
                    "stage": sweep["stage"], "cavity": sweep["cavity"], "side": sweep.get("side"),
                    "parameter": parameter, "baseline_value": initial, "offset": offset, "value": value,
                    "bearing_delta_n": result["bearing_reaction_n"] - baseline["bearing_reaction_n"],
                    **result,
                })
        cases.append({"stage1_share": share, "source_cycle": reference, "baseline": baseline, "points": points})
    return {"format": "gas-turbine-thrust-sensitivity", "schema_version": 1,
            "source_cycle": source, "cases": cases}
