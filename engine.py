"""Design-point open Brayton cycle with constant-cp or NASA9 frozen-mixture properties."""
import copy
import math
import uuid
from datetime import datetime, timezone

if __package__:
    from .elements import solve_train, apply_mass
    from .stages import allocate
    from .model import InputError, MODULE_ID, MODULE_VERSION, input_hash, validate_input
    from .thermo import ConstantGas, air, products, provenance, properties_at, solve_temperature, stoichiometric_fuel_air_ratio, hydrogen_fraction
else:
    from elements import solve_train, apply_mass
    from stages import allocate
    from model import InputError, MODULE_ID, MODULE_VERSION, input_hash, validate_input
    from thermo import ConstantGas, air, products, provenance, properties_at, solve_temperature, stoichiometric_fuel_air_ratio, hydrogen_fraction

T_REF_K = 298.15


def analyze(raw):
    inp = validate_input(raw)
    if inp["cooling"]["enabled"] and any(c["fraction"] > 0 for c in inp["cooling"]["circuits"]):
        if __package__:
            from .cooling import analyze_cooled
        else:
            from cooling import analyze_cooled
        return analyze_cooled(inp)
    v = inp["parameters"]
    ta, pa = v["ambient_temperature_k"], v["ambient_pressure_pa"]
    settings = inp["thermodynamics"]
    nasa = settings["model"] == "nasa9_frozen"
    t1 = ta
    p1 = pa * (1 - v["inlet_pressure_loss"])
    p2 = p1 * v["compressor_pressure_ratio"]
    t3 = v["turbine_inlet_temperature_k"]
    p3 = p2 * (1 - v["combustor_pressure_loss"])
    p4 = pa / (1 - v["exhaust_pressure_loss"])
    if p3 <= p4:
        raise InputError("圧力損失を考慮するとタービンが膨張できません。圧力比と損失率を確認してください。")
    if nasa:
        airmix = air()
        t2s = solve_temperature(airmix.entropy_thermal, airmix.entropy_thermal(t1) + airmix.r * math.log(p2/p1), lower=t1)
        h2 = airmix.h(t1) + (airmix.h(t2s) - airmix.h(t1)) / v["compressor_efficiency"]
        t2 = solve_temperature(airmix.h, h2, lower=t1)
    else:
        airmix = ConstantGas(v["cp_air_j_kg_k"], v["r_air_j_kg_k"])
        t2s = t1 * (p2 / p1) ** (airmix.r / airmix.constant_cp)
        t2 = t1 + (t2s - t1) / v["compressor_efficiency"]
        h2 = airmix.h(t2)
    compressor_train = None
    if inp["elements"]["compressor"]["enabled"]:
        compressor_train = solve_train(airmix, t1, p1, p2, inp["staging"]["compressor"], inp["elements"]["compressor"], "compressor", v["shaft_speed_rpm"])
        t2 = compressor_train["states"][-1]["total_temperature_k"]
        p2 = compressor_train["states"][-1]["total_pressure_pa"]
        p3 = p2*(1-v["combustor_pressure_loss"])
        h2 = airmix.h(t2)
    if t3 <= t2:
        raise InputError("タービン入口全温が圧縮機出口全温以下です。入口全温を上げるか圧力比を下げてください。")
    if nasa:
        low, high = 0.0, stoichiometric_fuel_air_ratio(settings)
        def burner_residual(ratio):
            return (1 + ratio) * products(ratio, settings).h(t3) - h2 - ratio * v["combustion_efficiency"] * v["fuel_lhv_j_kg"]
        if burner_residual(high) >= 0:
            raise InputError("指定TITには量論比以上の燃料が必要です。燃料過濃・不完全燃焼は未対応のため、TIT・燃料組成・LHVを確認してください。")
        for _ in range(65):
            middle = (low + high) / 2
            if burner_residual(middle) > 0:
                low = middle
            else:
                high = middle
        f = (low + high) / 2
        gasmix = products(f, settings)
        t4s = solve_temperature(gasmix.entropy_thermal, gasmix.entropy_thermal(t3) + gasmix.r * math.log(p4/p3), upper=t3)
        h4 = gasmix.h(t3) - v["turbine_efficiency"] * (gasmix.h(t3) - gasmix.h(t4s))
        t4 = solve_temperature(gasmix.h, h4, lower=t4s, upper=t3)
    else:
        gasmix = ConstantGas(v["cp_gas_j_kg_k"], v["r_gas_j_kg_k"])
        h3 = gasmix.h(t3)
        denominator = v["combustion_efficiency"] * v["fuel_lhv_j_kg"] - h3
        if denominator <= 0 or h3 <= h2:
            raise InputError("比熱・LHV・全温の組合せから正の燃料比を求められません。")
        f = (h3 - h2) / denominator
        if not 0 < f <= .2:
            raise InputError("燃空比が簡易モデルの範囲（0より大きく0.2以下）を外れています。")
        t4s = t3 * (p4 / p3) ** (gasmix.r / gasmix.constant_cp)
        t4 = t3 - v["turbine_efficiency"] * (t3 - t4s)
    turbine_train = None
    if inp["elements"]["turbine"]["enabled"]:
        turbine_train = solve_train(gasmix, t3, p3, p4, inp["staging"]["turbine"], inp["elements"]["turbine"], "turbine", v["shaft_speed_rpm"])
        t4 = turbine_train["states"][-1]["total_temperature_k"]
        p4 = turbine_train["states"][-1]["total_pressure_pa"]
    effective_eta_c = (airmix.h(t2s)-airmix.h(t1))/(airmix.h(t2)-airmix.h(t1))
    effective_eta_t = (gasmix.h(t3)-gasmix.h(t4))/(gasmix.h(t3)-gasmix.h(t4s))
    wc_specific = airmix.h(t2) - airmix.h(t1)
    wt_specific = (1 + f) * (gasmix.h(t3) - gasmix.h(t4))
    shaft_specific = v["mechanical_efficiency"] * wt_specific - wc_specific
    ma = v["air_mass_flow_kg_s"]
    if inp["mode"] == "target_power":
        if shaft_specific <= 0:
            raise InputError("この条件では正の軸出力を得られず、目標出力から流量を逆算できません。")
        ma = (v["target_net_power_w"] + v["auxiliary_power_w"]) / (v["generator_efficiency"] * shaft_specific)
        if not .001 <= ma <= 1000:
            raise InputError("必要空気流量が0.001〜1000 kg/sの対応範囲を外れます。")
    mf, mg = ma * f, ma * (1 + f)
    wc, wt = ma * wc_specific, ma * wt_specific
    shaft = v["mechanical_efficiency"] * wt - wc
    gross = shaft * v["generator_efficiency"]
    net = gross - v["auxiliary_power_w"]
    fuel_heat = mf * v["fuel_lhv_j_kg"]
    combustor_loss = (1 - v["combustion_efficiency"]) * fuel_heat
    mechanical_loss = (1 - v["mechanical_efficiency"]) * wt
    # Generator efficiencies cannot represent motoring; keep electrical quantities undefined if shaft <= 0.
    generating = shaft > 0
    if not generating:
        gross = net = None
    generator_loss = shaft * (1 - v["generator_efficiency"]) if generating else None
    exhaust_energy = mg * gasmix.h(t4) - ma * airmix.h(ta)
    balance = fuel_heat - (shaft + mechanical_loss + combustor_loss + exhaust_energy)
    electric_balance = (fuel_heat - (net + v["auxiliary_power_w"] + generator_loss + mechanical_loss + combustor_loss + exhaust_energy)) if generating else None
    warnings = [("NASA9温度依存物性・乾燥空気・希薄完全燃焼の凍結組成です。燃焼の化学平衡・解離・湿度・冷却抽気は未対応で、GasTurbとの数値一致は未検証です。" if nasa else "比熱一定の旧モデルです。温度依存物性・燃料組成・解離・冷却抽気・性能マップは含みません。"),
                "回転数は任意の段平均径と組み合わせた周速・負荷係数の算出に用います。サイクル性能の変化や軸系マッチングは計算しません。"]
    if not generating:
        warnings.append("圧縮機動力を賄えず、発電運転が成立しない計算条件です。発電量・発電効率は未定義です。")
    elif net <= 0:
        warnings.append("補機消費を差し引いた正味発電出力が0以下です。熱効率・熱消費率・燃料消費率は未定義です。")
    if f > .06:
        warnings.append("燃空比が0.06を超えます。燃料組成や燃焼成立性を別途確認してください。")
    metrics = {"air_mass_flow_kg_s": ma, "fuel_mass_flow_kg_s": mf, "gas_mass_flow_kg_s": mg, "fuel_air_ratio": f,
               "compressor_power_w": wc, "turbine_gas_power_w": wt, "turbine_shaft_power_w": wt * v["mechanical_efficiency"],
               "net_shaft_power_w": shaft, "gross_electric_power_w": gross, "net_electric_power_w": net,
               "fuel_heat_input_w": fuel_heat, "shaft_efficiency_lhv": shaft / fuel_heat if shaft > 0 else None,
               "electric_efficiency_lhv": net / fuel_heat if generating and net > 0 else None,
               "heat_rate_kj_kwh": fuel_heat / net * 3600 if generating and net > 0 else None,
               "specific_fuel_consumption_kg_kwh": mf / net * 3.6e6 if generating and net > 0 else None,
               "exhaust_temperature_k": t4, "turbine_expansion_ratio": p3 / p4,
               "compressor_effective_efficiency": effective_eta_c, "turbine_effective_efficiency": effective_eta_t,
               "back_work_ratio": wc / (wt * v["mechanical_efficiency"]), "gamma_air": airmix.gamma(t1), "gamma_gas": gasmix.gamma(t3),
               "compressor_isentropic_exit_temperature_k": t2s, "turbine_isentropic_exit_temperature_k": t4s}
    stations = []
    for key, name, t, p, mass, fluid in (("0", "大気", ta, pa, ma, "air"), ("1", "圧縮機入口", t1, p1, ma, "air"),
                                       ("2", "圧縮機出口", t2, p2, ma, "air"), ("3", "タービン入口", t3, p3, mg, "gas"),
                                       ("4", "タービン出口", t4, p4, mg, "gas"), ("5", "排気境界", t4, pa, mg, "gas")):
        stations.append({"id": key, "name": name, "total_temperature_k": t, "total_pressure_pa": p,
                         "mass_flow_kg_s": mass, "fluid": fluid, **properties_at(airmix if fluid == "air" else gasmix, t)})
    metadata = {"model": settings["model"], "fuel": settings["fuel"] if nasa else None,
                "dataset": provenance() if nasa else None, "air_mass_fractions": airmix.mass_fractions if nasa else None,
                "product_mass_fractions": gasmix.mass_fractions if nasa else None,
                "fuel_element_mass_fractions": {"C": 1-hydrogen_fraction(settings), "H": hydrogen_fraction(settings)} if nasa else None,
                "stoichiometric_fuel_air_ratio": stoichiometric_fuel_air_ratio(settings) if nasa else None,
                "equivalence_ratio": f / stoichiometric_fuel_air_ratio(settings) if nasa else None}
    stage_results = {"compressor": allocate(airmix, t1, p1, t2, p2, ma, v["compressor_efficiency"], inp["staging"]["compressor"], "compressor", v["shaft_speed_rpm"]),
                     "turbine": allocate(gasmix, t3, p3, t4, p4, mg, v["turbine_efficiency"], inp["staging"]["turbine"], "turbine", v["shaft_speed_rpm"], v["mechanical_efficiency"])}
    if compressor_train:
        stage_results["compressor"] = apply_mass(compressor_train, ma)
    if turbine_train:
        stage_results["turbine"] = apply_mass(turbine_train, mg, v["mechanical_efficiency"])
    for key, start, end in (("compressor", 1, 2), ("turbine", 3, 4)):
        if inp["elements"][key]["enabled"]:
            for station_index, local in ((start, stage_results[key]["states"][0]), (end, stage_results[key]["states"][-1])):
                stations[station_index].update({k: value for k, value in local.items() if k not in ("id", "name", "fluid")})
    result = {"schema_version": 5, "module_id": MODULE_ID, "module_version": MODULE_VERSION,
              "status": "success" if generating and net > 0 else "non_generating",
              "model": "nasa9_frozen_complete_combustion" if nasa else "constant_cp_ideal_gas_fuel_mass", "enthalpy_reference_temperature_k": T_REF_K,
              "thermodynamics": metadata,
              "metrics": metrics, "stations": stations, "stage_results": stage_results,
              "energy_balance": {"combustion_loss_w": combustor_loss, "mechanical_loss_w": mechanical_loss,
                                 "generator_loss_w": generator_loss, "auxiliary_power_w": v["auxiliary_power_w"],
                                 "exhaust_net_sensible_energy_w": exhaust_energy,
                                 "shaft_balance_residual_w": balance, "electric_balance_residual_w": electric_balance,
                                 "mass_balance_residual_kg_s": mg - ma - mf}, "warnings": warnings}
    if not all(math.isfinite(x) for x in metrics.values() if x is not None):
        raise InputError("数値計算が非有限値になりました。入力条件を確認してください。")
    return result


def run_case(raw):
    inp = validate_input(raw)
    started = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    result = analyze(inp)
    return {"format": "gas-turbine-cycle-case", "schema_version": 5, "module_id": MODULE_ID, "module_version": MODULE_VERSION,
            "run_id": str(uuid.uuid4()), "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "input_hash": input_hash(inp), "input": inp, "result": result}


def sweep(raw, minimum=1.2, maximum=20, count=31):
    inp = validate_input(raw)
    if type(count) is not int or not 3 <= count <= 101:
        raise InputError("スイープ点数は3〜101の整数で指定してください。")
    if any(type(x) not in (float, int) or not math.isfinite(x) for x in (minimum, maximum)) or not 1.01 <= minimum < maximum <= 50:
        raise InputError("圧力比の範囲は1.01〜50で、下限 < 上限にしてください。")
    base = analyze(inp)
    fixed = copy.deepcopy(inp)
    fixed["mode"] = "airflow"
    fixed["parameters"]["air_mass_flow_kg_s"] = base["metrics"]["air_mass_flow_kg_s"]
    points = []
    for index in range(count):
        ratio = minimum + (maximum - minimum) * index / (count - 1)
        fixed["parameters"]["compressor_pressure_ratio"] = ratio
        try:
            result = analyze(fixed)
            points.append({"pressure_ratio": ratio, "status": result["status"], "net_electric_power_w": result["metrics"]["net_electric_power_w"],
                           "electric_efficiency_lhv": result["metrics"]["electric_efficiency_lhv"], "reason": ""})
        except InputError as exc:
            points.append({"pressure_ratio": ratio, "status": "invalid", "net_electric_power_w": None,
                           "electric_efficiency_lhv": None, "reason": str(exc)})
    return {"input_hash": input_hash(inp), "fixed_air_mass_flow_kg_s": base["metrics"]["air_mass_flow_kg_s"], "points": points,
            "note": "基準点の空気流量・TIT・効率・損失を固定した設計点感度解析です。性能マップによるオフデザイン解析ではありません。"}
