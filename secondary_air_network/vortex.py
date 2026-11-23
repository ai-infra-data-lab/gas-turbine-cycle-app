"""Isothermal radial equilibrium study, deliberately separate from flow solver."""
import copy
import math
from model import ModelError, number


def validate_vortex(raw):
    if not isinstance(raw, dict):
        raise ModelError("渦計算の入力はオブジェクトで指定してください。")
    v = copy.deepcopy(raw)
    if v.get("kind") not in ("forced", "free"):
        raise ModelError("渦の種類はforced/freeです。")
    for field, lo, hi in (("rpm",-100000,100000),("swirl_ratio",-2,2),
                          ("reference_radius_m",.001,2),("start_radius_m",.001,2),("end_radius_m",.001,2),
                          ("reference_pressure_pa",1000,50e6),("static_temperature_k",150,2500)):
        v[field] = number(v.get(field), "渦 " + field, lo, hi)
    n = v.get("points",21)
    if isinstance(n,bool) or not isinstance(n,int) or not 2 <= n <= 101:
        raise ModelError("渦の評価点数は2～101の整数です。")
    v["points"] = n
    if v["start_radius_m"] == v["end_radius_m"]:
        raise ModelError("渦の開始半径と終了半径は異なる値にしてください。")
    return v


def solve_vortex(raw, gas=None):
    v = validate_vortex(raw)
    gas = gas or {"R":287.05,"gamma":1.4}
    if not isinstance(gas,dict):
        raise ModelError("気体物性はオブジェクトで指定してください。")
    rgas = number(gas.get("R"),"気体定数 R",200,500)
    gamma = number(gas.get("gamma"),"比熱比 γ",1.05,1.67)
    cp = gamma*rgas/(gamma-1)
    omega = v["rpm"]*math.pi/30
    speed = omega*v["swirl_ratio"]
    rr, t, pref = v["reference_radius_m"],v["static_temperature_k"],v["reference_pressure_pa"]
    def at(radius):
        if v["kind"] == "forced":
            vt = speed*radius
            potential = speed*speed*(radius*radius-rr*rr)/2
        else:
            constant = speed*rr*rr
            vt = constant/radius
            potential = constant*constant*(1/(rr*rr)-1/(radius*radius))/2
        exponent = potential/(rgas*t)
        if not -20 <= exponent <= 20:
            raise ModelError("渦の圧力比が極端です。回転数・半径・スワール比を確認してください。")
        p = pref*math.exp(exponent)
        if not 1000 <= p <= 50e6:
            raise ModelError("渦の静圧が1 kPa～50 MPaを外れます。")
        total_temperature = t+vt*vt/(2*cp)
        if total_temperature > 2500:
            raise ModelError("渦の全温が2500 Kを超えます。回転数・スワール比・半径を確認してください。")
        return {"radius_m":radius,"static_pressure_pa":p,"static_temperature_k":t,
                "tangential_velocity_m_s":vt,"tangential_mach":abs(vt)/math.sqrt(gamma*rgas*t),
                "total_temperature_k":total_temperature,"density_kg_m3":p/(rgas*t)}
    profile = [at(v["start_radius_m"]+(v["end_radius_m"]-v["start_radius_m"])*i/(v["points"]-1)) for i in range(v["points"])]
    return {"status":"calculated","input":v,"gas":{"R":rgas,"gamma":gamma},"profile":profile,
            "pressure_ratio_end_start":profile[-1]["static_pressure_pa"]/profile[0]["static_pressure_pa"],
            "pressure_difference_pa":profile[-1]["static_pressure_pa"]-profile[0]["static_pressure_pa"],
            "warnings":["指定スワール・等温の半径方向静圧平衡。ネットワークの枝には直接接続していません。",
                        "全温は静温＋接線速度の運動エネルギーから算出。流量、トルク、回転仕事、風損、熱収支は未計算です。",
                        "静圧と全圧、静温と全温、絶対系と相対系を区別して境界条件を検討してください。"]}
