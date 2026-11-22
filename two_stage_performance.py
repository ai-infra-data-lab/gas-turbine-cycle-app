"""Use design-point CFD or test performance in ordinary cycle cases."""
import math
from copy import deepcopy

if __package__:
    from .elements import passive, rotor, state, temperature
    from .engine import run_case
    from .model import validate_input
    from .thermo import ConstantGas, air
    from .two_stage_design import require_two_stage_compressor
else:
    from elements import passive, rotor, state, temperature
    from engine import run_case
    from model import validate_input
    from thermo import ConstantGas, air
    from two_stage_design import require_two_stage_compressor

DESIGNS = {"equal": "均等配分案", "75-percent": "75%配分案"}
SOURCE_NAMES = {"cfd": "CFD", "test": "試験", "synthetic": "仮想"}
CONDITION_LIMITS = {
    "inlet_total_temperature_k": (200, 350),
    "inlet_total_pressure_pa": (16000, 150000),
    "air_mass_flow_kg_s": (.001, 1000),
    "shaft_speed_rpm": (100, 300000),
}
RETENTION_KEYS = ("diffuser_1", "return_channel", "diffuser_2", "outlet_guide_vane")
FORMAT = "centrifugal-two-stage-performance"


def require_keys(data, keys, label):
    if not isinstance(data, dict) or set(data) != set(keys):
        raise ValueError(f"{label}には {', '.join(keys)} を指定してください。")


def require_number(value, minimum, maximum, label):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ValueError(f"{label}は {minimum}〜{maximum} の数値にしてください。")


def validate_document(document):
    require_keys(document, ("format", "schema_version", "conditions", "designs"), "性能JSON")
    if (document["format"] != FORMAT or type(document["schema_version"]) is not int
            or document["schema_version"] != 1):
        raise ValueError("対応する2段圧縮機性能JSON（schema 1）を選択してください。")
    conditions = document["conditions"]
    require_keys(conditions, CONDITION_LIMITS, "共通の運転条件")
    for key, limits in CONDITION_LIMITS.items():
        require_number(conditions[key], *limits, key)
    require_keys(document["designs"], DESIGNS, "比較する設計案")
    for key, label in DESIGNS.items():
        design = document["designs"][key]
        require_keys(design, ("source_kind", "source_reference", "stages", "pressure_retentions"), label)
        if design["source_kind"] not in ("cfd", "test", "synthetic"):
            raise ValueError(f"{label}の出典区分は cfd、test、synthetic のいずれかを指定してください。")
        reference = design["source_reference"]
        if not isinstance(reference, str) or not 1 <= len(reference.strip()) <= 200:
            raise ValueError(f"{label}の出典を1〜200文字で指定してください。")
        stages = design["stages"]
        if not isinstance(stages, list) or len(stages) != 2:
            raise ValueError(f"{label}の性能を2段分指定してください。")
        for index, stage in enumerate(stages, 1):
            prefix = f"{label}の第{index}段"
            require_keys(stage, ("pressure_ratio", "total_to_total_efficiency"), prefix)
            ratio = stage["pressure_ratio"]
            require_number(ratio, 1, 50, prefix + "全圧比")
            if ratio <= 1:
                raise ValueError(prefix + "全圧比は1より大きくしてください。")
            require_number(stage["total_to_total_efficiency"], .5, 1, prefix + "全圧効率")
        retentions = design["pressure_retentions"]
        if retentions is not None:
            require_keys(retentions, RETENTION_KEYS, label + "の全圧残存比")
            for element, ratio in retentions.items():
                require_number(ratio, 0, 1, f"{label}の {element}")
                if ratio == 0:
                    raise ValueError(f"{label}の {element} の全圧残存比は0より大きくしてください。")


def cycle_with_performance(common, gas, key, design):
    settings = deepcopy(common)
    p = settings["parameters"]
    settings["name"] = f"{DESIGNS[key]}・{SOURCE_NAMES[design['source_kind']]}性能"
    ratios = [row["pressure_ratio"] for row in design["stages"]]
    p["compressor_pressure_ratio"] = math.prod(ratios)
    settings["staging"]["compressor"]["weights"] = [100 * math.log(ratio) for ratio in ratios]
    settings = validate_input(settings)
    compressor = settings["elements"]["compressor"]
    parts = design["pressure_retentions"] or dict.fromkeys(RETENTION_KEYS, 1.)
    # Model the second diffuser and downstream guide vane as one passive element.
    stage_losses = (
        {"diffuser": parts["diffuser_1"], "return_channel": parts["return_channel"]},
        {"diffuser": parts["diffuser_2"] * parts["outlet_guide_vane"]},
    )
    entry = state(gas, p["ambient_temperature_k"], p["ambient_pressure_pa"] * (1 - p["inlet_pressure_loss"]),
                  compressor["inlet_velocity_m_s"])
    for index, (stage, measured, losses) in enumerate(zip(compressor["stages"], design["stages"], stage_losses), 1):
        stage_ratio = measured["pressure_ratio"]
        rotor_ratio = stage_ratio / math.prod(losses.values())
        tin = entry["total_temperature_k"]
        hin, sin = gas.h(tin), gas.entropy_thermal(tin)
        ideal_stage = gas.h(temperature(gas, "entropy_thermal", sin + gas.r * math.log(stage_ratio))) - hin
        ideal_rotor = gas.h(temperature(gas, "entropy_thermal", sin + gas.r * math.log(rotor_ratio))) - hin
        eta = measured["total_to_total_efficiency"] * ideal_rotor / ideal_stage
        if eta > 1 + 1e-10:
            raise ValueError(f"第{index}段の全圧効率と静翼損失を両立するとインペラー効率が100%を超えます。")
        stage["rotor_efficiency"] = min(eta, 1.)
        entry = rotor(gas, entry, rotor_ratio, stage["rotor_efficiency"], stage["rotor_exit_velocity_m_s"], True)["outlet"]
        for element, retention in losses.items():
            part = stage[element]
            loss = (1 - retention) * entry["total_pressure_pa"]
            part.update(model="loss", coefficient=loss / entry["dynamic_pressure_pa"])
            entry = passive(gas, entry, part, element)["outlet"]
    return run_case(settings)


def compare(base, document):
    validate_document(document)
    settings = validate_input(base["input"])
    require_two_stage_compressor(settings)
    settings["mode"] = "airflow"
    p, conditions = settings["parameters"], document["conditions"]
    p.update(ambient_temperature_k=conditions["inlet_total_temperature_k"],
             ambient_pressure_pa=conditions["inlet_total_pressure_pa"] / (1 - p["inlet_pressure_loss"]),
             air_mass_flow_kg_s=conditions["air_mass_flow_kg_s"], shaft_speed_rpm=conditions["shaft_speed_rpm"])
    gas = air() if settings["thermodynamics"]["model"] == "nasa9_frozen" else ConstantGas(p["cp_air_j_kg_k"], p["r_air_j_kg_k"])
    cases = {}
    for key, label in DESIGNS.items():
        try:
            cases[key] = cycle_with_performance(settings, gas, key, document["designs"][key])
        except (ValueError, OverflowError, ZeroDivisionError) as exc:
            raise ValueError(f"{label}の性能を反映できません。{exc}") from exc
    return {"format": "centrifugal-two-stage-performance-comparison", "schema_version": 1,
            "source_run_id": base["run_id"], "source_input_hash": base["input_hash"],
            "performance": document, "cycle_cases": cases}
