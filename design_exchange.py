"""Read a design point from Cycle Studio without trusting embedded results."""
import math

from .engine import run_case
from .model import input_hash
from .thermo import ConstantGas, air


def number(value, label, lower=None, upper=None):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or lower is not None and value < lower
            or upper is not None and value > upper):
        raise ValueError(f"{label}の数値と範囲を確認してください。")
    return float(value)


def verified_cycle(document):
    if not isinstance(document, dict):
        raise ValueError("Cycle Studioのケースまたは入力JSONを指定してください。")
    if document.get("format") != "gas-turbine-cycle-case":
        case = run_case(document)
        return case, cycle_source(case)
    if document.get("module_id") != "gt.cycle.simple_single_shaft":
        raise ValueError("Cycle Studioのケースを指定してください。")
    if input_hash(document["input"]) != document.get("input_hash"):
        raise ValueError("サイクル入力と保存時のハッシュが一致しません。")
    return run_case(document["input"]), cycle_source(document)


def cycle_source(case):
    return {"run_id": case["run_id"], "input_hash": case["input_hash"],
            "context": case["input"]["context"]}


def cycle_air(case):
    params = case["input"]["parameters"]
    if case["input"]["thermodynamics"]["model"] == "nasa9_frozen":
        return air()
    return ConstantGas(params["cp_air_j_kg_k"], params["r_air_j_kg_k"])
