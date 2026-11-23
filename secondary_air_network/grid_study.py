"""Three independently solved network grids; no extrapolated accuracy claim."""
import copy
from engine import CalculationError, solve
from model import ModelError, number, validate


# Explicit absolute comparison floors in SI units, separate from solver tolerances.
METRICS = {
    "mass_flow_kg_s": ("edges", "kg/s", 1e-8),
    "pressure_pa": ("nodes", "Pa", 1.0),
    "temperature_k": ("nodes", "K", .01),
    "absolute_total_temperature_k": ("nodes", "K", .01),
    "outlet_total_temperature_k": ("edges", "K", .01),
    "heat_w": ("edges", "W", .01),
    "shaft_work_w": ("edges", "W", .01),
    "max_mach": ("edges", "-", 1e-4),
}
DISCRETIZED = {"pipe", "resistance", "rotating_pipe"}


def compare_values(values, absolute_floor, relative_percent):
    """Symmetric relative difference; zero/undefined values never divide by zero."""
    out = {"values": values, "absolute_floor": absolute_floor}
    if any(v is None for v in values):
        out.update(status="unavailable", difference=None, relative_percent=None,
                   limit=None, trend="unavailable")
        return out
    a, b, c = values
    diff = abs(c-b)
    scale = max(abs(b), abs(c))
    limit = max(absolute_floor, scale*relative_percent/100)
    previous = abs(b-a)
    trend = "small_change" if max(previous, diff) <= absolute_floor else (
        "oscillating" if (b-a)*(c-b) < 0 else (
            "growing" if diff > previous*1.05 else "decreasing"))
    out.update(status="within_tolerance" if diff <= limit else "review_required",
               difference=diff, relative_percent=100*diff/scale if scale else None,
               limit=limit, trend=trend)
    return out


def compare_levels(levels, relative_percent):
    """Compare every node/edge, rather than hiding changes in supply totals."""
    rows, changes = [], []
    if any(level["status"] != "converged" for level in levels):
        return rows, changes, "incomplete"
    results = [level["result"] for level in levels]
    for field, (group, unit, floor) in METRICS.items():
        maps = [{obj["id"]: obj for obj in result[group]} for result in results]
        for obj in results[0][group]:
            row = compare_values([m[obj["id"]].get(field) for m in maps], floor, relative_percent)
            row.update(group=group, id=obj["id"], name=obj["name"], field=field, unit=unit)
            rows.append(row)
    for edge in results[0]["edges"]:
        samples = [next(e for e in r["edges"] if e["id"] == edge["id"]) for r in results]
        choking = [e["choked"] for e in samples]
        flow = [e["mass_flow_kg_s"] for e in samples]
        direction = [0 if abs(v) <= 1e-8 else (1 if v > 0 else -1) for v in flow]
        for field, values in (("choked", choking), ("flow_direction", direction)):
            if len(set(values)) > 1:
                changes.append({"id": edge["id"], "name": edge["name"], "field": field, "values": values})
    review = bool(changes) or any(r["status"] == "review_required" or
        r["trend"] in ("growing", "oscillating") for r in rows)
    # An unavailable result cannot establish numerical stability.
    if any(r["status"] == "unavailable" and not all(v is None for v in r["values"]) for r in rows):
        review = True
    return rows, changes, "review_required" if review else "within_tolerance"


def grid_study(raw, relative_percent=.1):
    relative_percent = number(relative_percent, "相対比較許容値 %", .000001, 10)
    base = validate(raw)
    edges = [e for e in base["edges"] if e["kind"] in DISCRETIZED]
    if not edges:
        raise ModelError("分割計算する配管・回転流路・局所抵抗がありません。")
    if any(e["cells"] > 64 for e in edges):
        raise ModelError("4倍分割を256以内にするため、対象流路の基準分割数を64以下にしてください。自動切下げはしません。")
    levels = []
    for factor in (1, 2, 4):
        model = copy.deepcopy(base)
        cells = {}
        for e in model["edges"]:
            if e["kind"] in DISCRETIZED:
                e["cells"] *= factor
                cells[e["id"]] = e["cells"]
        level = {"factor": factor, "cells": cells}
        try:
            level.update(status="converged", result=solve(model))
        except (CalculationError, ModelError) as exc:
            level.update(status="failed", error=str(exc))
        levels.append(level)
    rows, changes, assessment = compare_levels(levels, relative_percent)
    return {"kind": "grid_study", "schema_version": 1,
            "status": "incomplete" if assessment == "incomplete" else "completed",
            "assessment": assessment, "model": base,
            "relative_tolerance_percent": relative_percent,
            "comparison": "2x vs 4x; max(abs_floor, relative_tolerance * max(abs(values)))",
            "levels": levels, "metrics": rows, "state_changes": changes,
            "warnings": ["分割数による数値変化の確認です。実機精度・相関式の妥当性・設計合否を保証しません。",
                         "基準・2倍・4倍で全ネットワークを独立に解きます。入力モデル・保存ケースは変更しません。",
                         "変化率は2倍/4倍の絶対値の大きい方を分母に使用。両方0なら変化率は未定義です。",
                         "振動・変化増大・流向/チョーク切替は要確認。全段で不定の温度は比較対象外です。"]}
