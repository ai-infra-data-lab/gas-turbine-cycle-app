"""Join combustor, cooling, and mechanical design-point evidence."""
import csv
from hashlib import sha256
import io
import math

from gas_turbine_cycle_app.design_exchange import cycle_source, number


def mechanical_review(reports):
    cooled = reports["cycle-cooled"]
    speed = cooled["input"]["parameters"]["shaft_speed_rpm"]
    source = cycle_source(cooled)
    for report in (reports["thrust"], reports["rotor"]):
        if report["source_cycle"] != source:
            raise ValueError("軸力とロータは同じ冷却後サイクルの運転点で評価してください。")
    shaft = {}
    for name, case in reports["thrust"]["cases"].items():
        result = case["thrust"]
        if result["source_cycle"] != source:
            raise ValueError("軸力ケースの入力元が一致しません。")
        joints = []
        for joint in result["joints"]:
            strength = joint["strength_case"]
            criteria = strength["result"]
            joints.append({"kind":strength["input"]["kind"], "axial_position_m":joint["x_m"],
                           "tension_n":joint["tension_n"], "torque_n_m":criteria["nominal_torque_n_m"],
                           "maximum_utilization":criteria["maximum_utilization"],
                           "governing_check":criteria["governing_check"], "status":criteria["status"]})
        shaft[name] = {"stages":[{"stage":row["stage"], "force_n":row["force_n"]} for row in result["stages"]],
                       "bearing_reaction_n":result["bearing_reaction"]["force_n"],
                       "axial_force_residual_n":result["axial_force_residual_n"], "joints":joints}
    conditions = reports["oil"]["conditions"]
    oil = {name: {key: row[key] for key in ("temperature_c", "viscosity_cst", "bearings", "intersection_residual_kpa")}
           for name, row in conditions.items()}
    rated = oil.get("定格")
    if rated is None:
        raise ValueError("ロータ連携の確認には定格の給油条件を指定してください。")
    rotor = {}
    for name, case in reports["rotor"]["cases"].items():
        if case["operating_rpm"] != speed:
            raise ValueError("ロータの評価回転数がサイクルと一致しません。")
        model_bearings = case["model"]["bearings"]
        supplies = case["oil_supply"]
        if (len(supplies) != len(model_bearings) or {row["bearing_index"] for row in supplies} != set(range(len(model_bearings)))
                or len({row["bearing_id"] for row in supplies}) != len(supplies)):
            raise ValueError("定格給油とロータの軸受IDの対応を確認してください。")
        bearings = []
        for feed in supplies:
            key = feed["bearing_id"]
            expected = rated["bearings"].get(key)
            if expected is None or feed["temperature_c"] != rated["temperature_c"] or any(
                    feed.get(field) != value for field, value in expected.items()):
                raise ValueError(f"軸受 {key} の給油条件が定格と一致しません。")
            coefficient = model_bearings[feed["bearing_index"]]
            bearings.append({"bearing_id":key, "node":coefficient["node"], "source":coefficient["source"],
                             "coefficients":{key: coefficient[key] for key in
                                             ("kxx","kyy","kxy","kyx","cxx","cyy","cxy","cyx")}})
        rotor[name] = {"nearest_critical":case["nearest_critical"], "separation_margin_fraction":case["separation_margin_fraction"],
                       "operating_amplitude_um":case["operating_amplitude_um"], "bearings":bearings}
    return {"shaft_speed_rpm":speed, "axial_cases":shaft, "oil_conditions":oil, "rotor_cases":rotor,
            "rotor_bearing_basis":reports["rotor"]["bearing_coefficients"]}


def reported_measurements(combustor, cooling, thrust):
    measurements = {}

    def record(measurement_id, unit, value):
        if measurement_id in measurements:
            raise ValueError(f"測定対象IDが重複しています。{measurement_id}")
        if value is not None:
            measurements[measurement_id] = {"unit":unit, "calculated":number(value, measurement_id)}

    thermal = combustor.get("thermal", {})
    if "outlet_profile" in thermal:
        for row in thermal["outlet_profile"]["sectors"]:
            record(f"combustor.exit.{row['id']}", "K", row["total_temperature_k"])
    for row in thermal.get("liner_segments", []):
        record(f"combustor.liner.{row['id']}", "K", row["hot_wall_temperature_k"])
    record("combustor.pressure_loss", "Pa", combustor["per_can"]["total_pressure_loss_pa"])
    for row in cooling["injections"]:
        name = f"cooling.{row['station']}.{row['edge_id']}"
        record(f"{name}.flow", "kg/s", row["coolant_flow_kg_s"])
        record(f"{name}.temperature", "K", row["coolant_total_temperature_k"])
        record(f"{name}.backpressure", "Pa", row["backpressure_static_pa"])
    for case_name, variant in thrust["cases"].items():
        for edge in variant["network_result"]["edges"]:
            record(f"seal.{case_name}.{edge['id']}", "kg/s", edge["mass_flow_kg_s"])
    return measurements


def compare_observations(data, measurements, *, kind, reference):
    if kind not in ("test", "cfd", "synthetic") or not isinstance(reference, str) or not reference.strip():
        raise ValueError("測定・計算条件と出典区分を指定してください。")
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    if reader.fieldnames != ["measurement_id", "unit", "value", "uncertainty"]:
        raise ValueError("測定CSVの列は measurement_id,unit,value,uncertainty です。")
    rows, seen = [], set()
    for row in reader:
        key = (row.get("measurement_id") or "").strip()
        unit = (row.get("unit") or "").strip()
        if key not in measurements or key in seen or None in row or unit != measurements[key]["unit"]:
            raise ValueError(f"測定CSVのID・単位・列を確認してください（{reader.line_num}行目）。")
        seen.add(key)
        value = number(float(row.get("value") or "nan"), "測定値")
        uncertainty = row.get("uncertainty")
        uncertainty = number(float(uncertainty), "測定不確かさ", 0) if uncertainty and uncertainty.strip() else None
        calculated = measurements[key]["calculated"]
        difference = calculated - value
        if not math.isfinite(difference):
            raise ValueError("数値の差が計算範囲を超えました。")
        rows.append({"measurement_id":key, "unit":unit, "calculated":calculated, "observed":value,
                     "difference":difference, "uncertainty":uncertainty,
                     "within_reported_uncertainty":None if uncertainty is None else abs(difference) <= uncertainty})
    if not rows:
        raise ValueError("測定CSVには値を1件以上指定してください。")
    return {"kind":kind, "reference":reference.strip(), "sha256":sha256(data).hexdigest(), "rows":rows}


def review(reports, *, observation_data=None, observation_kind=None, observation_reference=None):
    cooled = reports["cycle-cooled"]
    cooling = reports["cooling"]
    measurements = reported_measurements(reports["combustor"], cooling, reports["thrust"])
    observations = compare_observations(observation_data, measurements, kind=observation_kind,
                                        reference=observation_reference) if observation_data is not None else None
    return {"format":"gas-turbine-suite-support-review", "schema_version":1, "source_cycle":cycle_source(cooled),
            "operating_point":cooling["operating_point"], "combustor_thermal":reports["combustor"].get("thermal", {}),
            "cooling":{"injections":cooling["injections"], "coupling":cooling.get("coupling"),
                       "cycle_feedback":cooling["cycle_feedback"]},
            "mechanical":mechanical_review(reports), "measurement_targets":measurements, "observations":observations}
