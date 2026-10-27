"""SI input contract for an independent, design-point single-shaft cycle module."""
import hashlib
import json
import math
import uuid

MODULE_ID = "gt.cycle.simple_single_shaft"
MODULE_VERSION = "0.3.0"
SCHEMA_VERSION = 3

# All limits are software applicability bounds, not equipment operating limits.
# key, label, UI unit, UI multiplier, SI minimum, SI maximum, SI default, group
FIELDS = [
    ("ambient_temperature_k", "大気温度", "K", 1, 200, 350, 288.15, "環境・運転点"),
    ("ambient_pressure_pa", "大気圧力（絶対圧）", "kPa", .001, 20000, 150000, 101325, "環境・運転点"),
    ("air_mass_flow_kg_s", "空気流量（全流量）", "kg/s", 1, .001, 1000, 1.2, "環境・運転点"),
    ("target_net_power_w", "目標正味発電出力", "kW", .001, 100, 1e9, 100000, "環境・運転点"),
    ("shaft_speed_rpm", "軸回転数（負荷係数用）", "rpm", 1, 100, 300000, 60000, "環境・運転点"),
    ("compressor_pressure_ratio", "圧縮機全圧比", "—", 1, 1.01, 50, 3.8, "サイクル・効率"),
    ("turbine_inlet_temperature_k", "タービン入口全温", "K", 1, 500, 2000, 1123, "サイクル・効率"),
    ("compressor_efficiency", "圧縮機全体等エントロピー効率", "%", 100, .5, 1, .78, "サイクル・効率"),
    ("turbine_efficiency", "タービン全体等エントロピー効率", "%", 100, .5, 1, .85, "サイクル・効率"),
    ("combustion_efficiency", "燃焼効率", "%", 100, .8, 1, .99, "サイクル・効率"),
    ("mechanical_efficiency", "タービン機械伝達効率", "%", 100, .8, 1, .98, "サイクル・効率"),
    ("generator_efficiency", "発電機効率", "%", 100, .5, 1, .95, "サイクル・効率"),
    ("inlet_pressure_loss", "吸気圧力損失率", "%", 100, 0, .2, .01, "圧力損失・燃料"),
    ("combustor_pressure_loss", "燃焼器圧力損失率", "%", 100, 0, .2, .04, "圧力損失・燃料"),
    ("exhaust_pressure_loss", "排気圧力損失率", "%", 100, 0, .2, .02, "圧力損失・燃料"),
    ("fuel_lhv_j_kg", "燃料の低位発熱量（LHV）", "MJ/kg", 1e-6, 1e7, 6e7, 5e7, "圧力損失・燃料"),
    ("auxiliary_power_w", "電気補機動力（固定値）", "kW", .001, 0, 1e8, 2000, "圧力損失・燃料"),
    ("cp_air_j_kg_k", "空気の定圧比熱", "kJ/(kg·K)", .001, 900, 1300, 1005, "物性（比熱一定）"),
    ("cp_gas_j_kg_k", "燃焼ガスの定圧比熱", "kJ/(kg·K)", .001, 950, 1600, 1150, "物性（比熱一定）"),
    ("r_air_j_kg_k", "空気の気体定数", "J/(kg·K)", 1, 200, 400, 287.05, "物性（比熱一定）"),
    ("r_gas_j_kg_k", "燃焼ガスの気体定数", "J/(kg·K)", 1, 200, 400, 287.05, "物性（比熱一定）"),
]


class InputError(ValueError):
    pass


def default_input(thermo_model="nasa9_frozen"):
    return {"schema_version": 3, "mode": "airflow", "name": "小型GT・設計点サンプル", "parameters": {x[0]: x[6] for x in FIELDS},
            "staging": default_staging(),
            "thermodynamics": {"model": thermo_model, "fuel": "methane", "fuel_hydrogen_mass_fraction": .25},
            "context": {"project_id": None, "component_id": None, "source_revision": None}}


def default_staging():
    return {k: {"count": 1, "distribution": "pressure", "weights": [1.0], "mean_diameters_m": None}
            for k in ("compressor", "turbine")}


def validate_staging(raw):
    if not isinstance(raw, dict) or set(raw) != {"compressor", "turbine"}:
        raise InputError("段構成はcompressorとturbineを指定してください。")
    clean = {}
    for key, label in (("compressor", "圧縮機"), ("turbine", "タービン")):
        item = raw[key]
        if not isinstance(item, dict) or set(item) != {"count", "distribution", "weights", "mean_diameters_m"}:
            raise InputError(f"{label}の段構成の項目が不正です。")
        count = item["count"]
        if type(count) is not int or not 1 <= count <= 30:
            raise InputError(f"{label}の段数は1〜30の整数にしてください。")
        if item["distribution"] not in ("pressure", "work"):
            raise InputError(f"{label}の配分方法はpressureまたはworkです。")
        weights = item["weights"]
        if not isinstance(weights, list) or len(weights) != count or any(type(x) not in (int, float) or not math.isfinite(x) or not .001 <= x <= 1000 for x in weights):
            raise InputError(f"{label}の配分重みを段数分、0.001〜1000の数値で指定してください。")
        diameters = item["mean_diameters_m"]
        if diameters is not None and (not isinstance(diameters, list) or len(diameters) != count or any(type(x) not in (int, float) or not math.isfinite(x) or not .001 <= x <= 20 for x in diameters)):
            raise InputError(f"{label}の段平均径は未指定か、段数分の0.001〜20 mにしてください。")
        clean[key] = {"count": count, "distribution": item["distribution"], "weights": [float(x) for x in weights],
                      "mean_diameters_m": [float(x) for x in diameters] if diameters is not None else None}
    return clean


def validate_input(raw):
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw.get("schema_version") not in (1, 2, 3):
        raise InputError("入力のschema_versionは1、2、3を指定してください。")
    if set(raw) - {"schema_version", "mode", "name", "parameters", "context", "thermodynamics", "staging"}:
        raise InputError("未対応の入力フィールドがあります。")
    if raw["schema_version"] < 3 and "staging" in raw:
        raise InputError("段構成を追加する場合はschema_version=3にしてください。")
    staging = validate_staging(raw.get("staging") if raw["schema_version"] == 3 else default_staging())
    settings = raw.get("thermodynamics")
    if raw["schema_version"] == 1:
        if settings is not None:
            raise InputError("旧版入力に物性モデルを追加する場合はschema_version=2へ変更してください。")
        settings = {"model": "constant_cp", "fuel": "methane", "fuel_hydrogen_mass_fraction": .25}
    if not isinstance(settings, dict) or set(settings) != {"model", "fuel", "fuel_hydrogen_mass_fraction"}:
        raise InputError("物性モデル・燃料組成の指定が不正です。")
    if settings["model"] not in ("constant_cp", "nasa9_frozen") or settings["fuel"] not in ("methane", "gasturb_generic", "gasturb_natural_gas", "custom_ch"):
        raise InputError("対応していない物性モデルまたは燃料です。")
    fraction = settings["fuel_hydrogen_mass_fraction"]
    if type(fraction) not in (int, float) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise InputError("C/H燃料の水素質量割合は0〜1で指定してください。")
    settings = {"model": settings["model"], "fuel": settings["fuel"], "fuel_hydrogen_mass_fraction": float(fraction)}
    mode = raw.get("mode")
    if mode not in ("airflow", "target_power"):
        raise InputError("計算方法はairflowまたはtarget_powerを指定してください。")
    name = raw.get("name", "")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 150:
        raise InputError("ケース名は1〜150文字で入力してください。")
    values = raw.get("parameters")
    keys = {x[0] for x in FIELDS}
    if not isinstance(values, dict) or set(values) != keys:
        raise InputError("計算パラメータに不足または未対応の項目があります。")
    clean = {}
    for key, label, unit, factor, low, high, _, _group in FIELDS:
        value = values[key]
        if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
            raise InputError(f"{label}は{low * factor:g}〜{high * factor:g} {unit}で指定してください。")
        clean[key] = float(value)
    context = raw.get("context", {})
    if not isinstance(context, dict) or set(context) - {"project_id", "component_id", "source_revision"}:
        raise InputError("統合用contextの形式が不正です。")
    refs = {}
    for key in ("project_id", "component_id"):
        value = context.get(key)
        if value is not None:
            try:
                value = str(uuid.UUID(value))
            except (ValueError, TypeError, AttributeError):
                raise InputError(f"{key}はUUIDかnullで指定してください。") from None
        refs[key] = value
    rev = context.get("source_revision")
    if rev is not None and (type(rev) is not int or rev < 1):
        raise InputError("入力元リビジョンは1以上の整数かnullで指定してください。")
    refs["source_revision"] = rev
    if (refs["component_id"] is not None or rev is not None) and refs["project_id"] is None:
        raise InputError("部品・リビジョンを指定する場合はproject_idも必要です。")
    return {"schema_version": 3, "mode": mode, "name": name.strip(), "parameters": clean, "context": refs, "thermodynamics": settings, "staging": staging}


def input_hash(value):
    canonical = validate_input(value)
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def ui_schema():
    return [{"key": k, "label": label, "unit": unit, "factor": factor, "min": low * factor, "max": high * factor,
             "default": value * factor, "group": group} for k, label, unit, factor, low, high, value, group in FIELDS]
