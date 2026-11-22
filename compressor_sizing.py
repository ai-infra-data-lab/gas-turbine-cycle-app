"""Design-point centrifugal stage sizing and editable meridional coordinates."""
import math

if __package__:
    from .elements import state
    from .engine import run_case
    from .model import default_input
    from .thermo import ConstantGas, air
else:
    from elements import state
    from engine import run_case
    from model import default_input
    from thermo import ConstantGas, air

FIELDS = (
    ("hub_ratio", "入口ハブ径／チップ径", .50, .10, .85),
    ("exit_meridional_m_s", "インペラー出口の半径方向速度 m/s", 105, 10, 300),
    ("backsweep_deg", "出口翼後傾角 °（半径方向基準）", 30, 0, 65),
    ("slip", "すべり係数（周速に対する補正）", .88, .55, 1),
    ("open_area_fraction", "有効面積／幾何面積", .95, .60, 1),
    ("tip_speed_limit_m_s", "インペラー出口周速上限 m/s", 520, 100, 750),
    ("diffuser_radius_ratio", "ディフューザー出口／入口半径比", 1.55, 1.08, 3),
    ("diffuser_exit_radial_m_s", "ディフューザー出口の半径方向速度 m/s", 65, 5, 240),
)


def sample_input():
    inp = default_input()
    inp["name"] = "単段遠心圧縮機・仮想の形状設計例"
    inp["parameters"]["compressor_pressure_ratio"] = 2.4
    inp["parameters"]["shaft_speed_rpm"] = 42000
    comp = inp["elements"]["compressor"]
    comp.update(enabled=True, inlet_velocity_m_s=115)
    stage = comp["stages"][0]
    stage["rotor_exit_velocity_m_s"] = 270
    stage["diffuser"].update(enabled=True, model="loss", coefficient=.15, exit_velocity_m_s=165)
    stage["return_channel"]["enabled"] = False
    return inp


def _assumptions(raw):
    if not isinstance(raw, dict) or set(raw) != {f[0] for f in FIELDS}:
        raise ValueError("形状設計の条件をすべて指定してください。")
    for key, label, _, low, high in FIELDS:
        value = raw[key]
        if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{label} は {low}〜{high} の範囲で指定してください。")
    return dict(raw)


def _wall(z, r, inlet, bend, radial):
    return {"start_z_m": z, "start_r_m": r, "start_angle_deg": 0,
            "segments": [{"length_m": inlet}, {"radius_m": bend, "sweep_deg": 90},
                         {"length_m": radial}]}


def _diffuser_wall(z, r, dz, dr):
    return {"start_z_m": z, "start_r_m": r,
            "start_angle_deg": math.degrees(math.atan2(dr, dz)),
            "segments": [{"length_m": math.hypot(dz, dr)}]}


def size_stage(cycle, assumptions, *, stage_number=None):
    """Size one unbled stage from a locally saved or freshly calculated cycle."""
    a = _assumptions(assumptions)
    inp, result = cycle["input"], cycle["result"]
    train = result["stage_results"]["compressor"]
    elements = train.get("elements", [])
    if stage_number is None:
        if len(train["stages"]) != 1 or len(elements) != 2:
            raise ValueError("単段・要素モード・ディフューザー有効・リターンチャネル無効のケースを選択してください。")
        stage_number = 1
    else:
        elements = [part for part in elements if part["stage"] == stage_number]
    if len(elements) < 2 or elements[0]["kind"] != "rotor" or elements[1]["name"] != "ディフューザー":
        raise ValueError(f"第{stage_number}段のインペラー・ディフューザーが必要です。")
    if inp["cooling"]["enabled"]:
        raise ValueError("この形状設計は冷却抽気を無効にしたケースに対応します。")
    rotor, diffuser = elements[:2]
    inlet, exit_target, diffuser_target = rotor["inlet"], rotor["outlet"], diffuser["outlet"]
    p = inp["parameters"]
    gas = air() if inp["thermodynamics"]["model"] == "nasa9_frozen" else ConstantGas(
        p["cp_air_j_kg_k"], p["r_air_j_kg_k"])
    mass = inlet["mass_flow_kg_s"]
    omega = math.tau*p["shaft_speed_rpm"]/60
    work = rotor["specific_work_j_kg"]
    axial = inlet["velocity_m_s"]
    c2 = a["exit_meridional_m_s"]
    lag = c2*math.tan(math.radians(a["backsweep_deg"]))
    # Euler: dh0 = U2*Ctheta2 with zero prewhirl. Ctheta2 = slip*U2 - Cm2*tan(beta2).
    u2 = (lag + math.sqrt(lag**2+4*a["slip"]*work))/(2*a["slip"])
    if u2 > a["tip_speed_limit_m_s"]:
        raise ValueError(f"必要な出口周速 {u2:.1f} m/s が指定上限を超えます。段の比仕事や出口条件を確認してください。")
    r2, tangential2 = u2/omega, work/u2
    r3 = r2*a["diffuser_radius_ratio"]
    c3, tangential3 = a["diffuser_exit_radial_m_s"], tangential2*r2/r3

    def static(target, radial, tangential):
        return state(gas, target["total_temperature_k"], target["total_pressure_pa"],
                     math.hypot(radial, tangential))

    s2, s3 = static(exit_target, c2, tangential2), static(diffuser_target, c3, tangential3)
    area1 = mass/(inlet["density_kg_m3"]*axial*a["open_area_fraction"])
    tip = math.sqrt(area1/(math.pi*(1-a["hub_ratio"]**2)))
    hub = tip*a["hub_ratio"]
    width2 = mass/(s2["density_kg_m3"]*math.tau*r2*c2*a["open_area_fraction"])
    width3 = mass/(s3["density_kg_m3"]*math.tau*r3*c3*a["open_area_fraction"])
    inlet_relative = inlet["mach"]*math.hypot(axial, omega*tip)/axial
    exit_relative = s2["mach"]*math.hypot(c2, tangential2-u2)/s2["velocity_m_s"]
    if max(inlet_relative, exit_relative, s2["mach"], s3["mach"]) >= .95:
        raise ValueError("絶対または相対マッハ数が0.95以上です。初版の亜音速モデルの適用範囲を外れます。")
    if r2 <= tip:
        raise ValueError("インペラー出口半径が入口チップ半径以下です。回転数、比仕事、入口流速を見直してください。")
    if s3["static_pressure_pa"] <= s2["static_pressure_pa"]:
        raise ValueError("仮定した出口条件ではディフューザーで静圧が回復しません。半径比と出口半径方向速度を調整してください。")

    gap = tip-hub
    turn = .45*(r2-tip)
    inlet_hub = max(.25*r2, width2)
    inlet_tip = inlet_hub+gap-width2
    radial = r2-tip-turn
    z2 = inlet_hub+gap+turn
    walls = {
        "impeller": {"hub": _wall(0, hub, inlet_hub, turn+gap, radial),
                     "shroud": _wall(0, tip, inlet_tip, turn, radial)},
        "diffuser": {"hub": _diffuser_wall(z2, r2, (width3-width2)/2, r3-r2),
                     "shroud": _diffuser_wall(z2-width2, r2, (width2-width3)/2, r3-r2)},
    }
    cp = (s3["static_pressure_pa"]-s2["static_pressure_pa"])/s2["dynamic_pressure_pa"]
    source = {key: cycle[key] for key in ("run_id", "input_hash", "module_id", "module_version")}
    source.update(name=inp["name"], context=inp["context"], stage=stage_number)
    return {
        "format": "centrifugal-stage-sizing", "schema_version": 1, "module_version": "0.1.0",
        "source": source, "assumptions": a,
        "dimensions": {"inlet_hub_diameter_m": 2*hub, "inlet_tip_diameter_m": 2*tip,
                       "impeller_exit_diameter_m": 2*r2, "impeller_exit_width_m": width2,
                       "diffuser_exit_diameter_m": 2*r3, "diffuser_exit_width_m": width3},
        "checks": {"tip_speed_m_s": u2, "inlet_tip_relative_mach": inlet_relative,
                   "impeller_exit_relative_mach": exit_relative, "impeller_exit_mach": s2["mach"],
                   "diffuser_exit_mach": s3["mach"], "impeller_exit_swirl_m_s": tangential2,
                   "diffuser_exit_swirl_m_s": tangential3,
                   "inlet_hub_metal_angle_deg": -math.degrees(math.atan2(omega*hub, axial)),
                   "inlet_tip_metal_angle_deg": -math.degrees(math.atan2(omega*tip, axial)),
                   "work_residual_j_kg": u2*(a["slip"]*u2-lag)-work,
                   "cp_at_sized_velocities": cp},
        "cycle_targets": {"mass_flow_kg_s": mass, "shaft_speed_rpm": p["shaft_speed_rpm"],
                          "impeller_work_j_kg": work, "impeller_exit_speed_m_s": exit_target["velocity_m_s"],
                          "diffuser_exit_speed_m_s": diffuser_target["velocity_m_s"],
                          "diffuser_cp": diffuser["pressure_recovery_coefficient"],
                          "inlet": inlet, "impeller_exit": exit_target, "diffuser_exit": diffuser_target},
        "sizing_states": {"impeller_exit": s2, "diffuser_exit": s3},
        "walls": walls,
    }


def sample_case():
    return run_case(sample_input())
