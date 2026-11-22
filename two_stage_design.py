"""Two-stage centrifugal compressor design-point allocation and geometry."""
import copy
import math

if __package__:
    from .compressor_sizing import FIELDS, sample_input, size_stage
    from .engine import run_case
    from .tools import vaned_two_stage
else:
    from compressor_sizing import FIELDS, sample_input, size_stage
    from engine import run_case
    from tools import vaned_two_stage

EDGE_FIELDS = (
    ("blade_count", "翼枚数", 12, 6, 32),
    ("le_fraction", "LE 子午面位置比", .035, .03, .15),
    ("te_fraction", "TE 子午面位置比", .965, .82, .97),
    ("le_thickness_mm", "LE厚さ mm", .35, .1, 3),
    ("te_thickness_mm", "TE厚さ mm", .30, .1, 3),
    ("max_thickness_mm", "最大厚さ mm", 1.25, .2, 5),
    ("le_ellipse_ratio", "LE楕円比", 1.4, .5, 3),
    ("te_ellipse_ratio", "TE楕円比", 1.0, .5, 3),
)
EDGE_TYPES = {"le_type": ("circular", "elliptical"),
              "te_type": ("circular", "elliptical", "cutoff")}


def sample_cycle_input():
    settings = sample_input()
    settings["name"] = "2段遠心圧縮機・仮想設計例"
    settings["parameters"].update(compressor_pressure_ratio=3.2, shaft_speed_rpm=39000,
                                  air_mass_flow_kg_s=.7)
    settings["staging"]["compressor"].update(count=2, weights=[1, 1])
    first = settings["elements"]["compressor"]["stages"][0]
    second = copy.deepcopy(first)
    first["return_channel"].update(enabled=True, coefficient=.28, exit_velocity_m_s=105)
    second["rotor_efficiency"] = .78
    settings["elements"]["compressor"]["stages"].append(second)
    return settings


def clean_edges(raw):
    if not isinstance(raw, dict) or set(raw) != {f[0] for f in EDGE_FIELDS} | EDGE_TYPES.keys():
        raise ValueError("各段のLE・TE・厚さをすべて指定してください。")
    for key, label, _, lo, hi in EDGE_FIELDS:
        value = raw[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"{label} は {lo}〜{hi} の有限値にしてください。")
    if type(raw["blade_count"]) is not int:
        raise ValueError("翼枚数は整数で指定してください。")
    for key, allowed in EDGE_TYPES.items():
        if raw[key] not in allowed:
            raise ValueError("対応するLE・TE断面を選択してください。")
    if raw["max_thickness_mm"] <= max(raw["le_thickness_mm"], raw["te_thickness_mm"]):
        raise ValueError("最大厚さはLE・TE厚さより大きくしてください。")
    return dict(raw)


def _crosses(first, second, same=False):
    def side(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

    for i, (a, b) in enumerate(zip(first, first[1:])):
        for j, (c, d) in enumerate(zip(second, second[1:])):
            if same and j <= i+1:
                continue
            if any(max(a[k],b[k]) < min(c[k],d[k]) or max(c[k],d[k]) < min(a[k],b[k])
                   for k in (0,1)):
                continue
            if side(a,b,c)*side(a,b,d) <= 0 and side(c,d,a)*side(c,d,b) <= 0:
                return True
    return False


def _return_channel(first, second, element):
    """Dimensioned return duct; views use separate machine stations joined here."""
    d1, d2 = first["dimensions"], second["dimensions"]
    r3 = d1["diffuser_exit_diameter_m"]/2
    b3 = d1["diffuser_exit_width_m"]
    hub2, tip2 = (d2[f"inlet_{side}_diameter_m"]/2 for side in ("hub", "tip"))
    eye = (hub2+tip2)/2
    gap = tip2-hub2
    wall = first["walls"]["diffuser"]["hub"]
    z = wall["start_z_m"]+(b3-d1["impeller_exit_width_m"])/2-b3/2
    # A monotone axial route outside the diffuser then back to the next eye.
    turn = max(1.4*max(b3, gap), .14*r3)
    axial = 6*turn
    controls = (
        ((z,r3), (z,r3+turn), (z+2*turn,r3+2*turn), (z+2*turn,r3)),
        ((z+2*turn,r3), (z+2*turn,r3-turn), (z+3*turn,eye), (z+axial,eye)),
    )
    center, hub, shroud = [], [], []
    for segment, (a,b,c,d) in enumerate(controls):
        for i in range(0 if segment == 0 else 1, 25):
            t = i/24
            u = 1-t
            point = [u**3*a[k]+3*u*u*t*b[k]+3*u*t*t*c[k]+t**3*d[k] for k in (0,1)]
            tangent = [3*(u*u*(b[k]-a[k])+2*u*t*(c[k]-b[k])+t*t*(d[k]-c[k])) for k in (0,1)]
            length = math.hypot(*tangent)
            fraction = (segment+t)/2
            width = b3+(gap-b3)*fraction
            normal = [tangent[1]/length, -tangent[0]/length]
            center.append(point)
            hub.append([point[k]+width*normal[k]/2 for k in (0,1)])
            shroud.append([point[k]-width*normal[k]/2 for k in (0,1)])
    if min(p[1] for p in hub+shroud) <= 0:
        raise ValueError("リターンチャネルが回転軸を横切ります。")
    if _crosses(hub, shroud) or _crosses(hub, hub, same=True) or _crosses(shroud, shroud, same=True):
        raise ValueError("リターンチャネルの壁が交差します。段間の入口・出口幅を確認してください。")
    return {
        "format": "return-channel-meridional", "schema_version": 1, "centerline_zr_m": center,
        "hub_zr_m": hub, "shroud_zr_m": shroud, "stage2_axial_offset_m": z+axial,
        "inlet_width_m": b3, "outlet_width_m": gap,
        "outlet_annulus_area_m2": math.pi*(tip2**2-hub2**2),
        "cycle_total_pressure_drop_pa": element["total_pressure_loss_pa"],
        "cycle_exit_velocity_m_s": element["outlet"]["velocity_m_s"],
        "inlet_flow_angle_from_meridional_deg": math.degrees(math.atan2(
            first["checks"]["diffuser_exit_swirl_m_s"], first["assumptions"]["diffuser_exit_radial_m_s"])),
        "design_exit_swirl_m_s": 0.,
    }


def require_two_stage_compressor(inp):
    rows = inp["elements"]["compressor"]["stages"]
    if (not inp["elements"]["compressor"]["enabled"] or len(rows) != 2 or inp["cooling"]["enabled"]
            or not all(row["diffuser"]["enabled"] for row in rows)
            or not rows[0]["return_channel"]["enabled"] or rows[1]["return_channel"]["enabled"]):
        raise ValueError("圧縮機を2段・要素モード、両段ディフューザーあり、第1段のみリターンあり、冷却抽気なしにしてください。")


def design(base, stages, edge_inputs, manual_share=None, search_min_pct=25, search_max_pct=75, *, vaned_options=None):
    if not isinstance(stages, list) or len(stages) != 2 or not isinstance(edge_inputs, list) or len(edge_inputs) != 2:
        raise ValueError("第1段と第2段の入力を指定してください。")
    edges = [clean_edges(item) for item in edge_inputs]
    if vaned_options is not None:
        vaned_options = vaned_two_stage.clean(vaned_options)
    inp = base["input"]
    require_two_stage_compressor(inp)
    if manual_share is not None and (type(manual_share) not in (int,float) or not .05 <= manual_share <= .95):
        raise ValueError("第1段の対数全圧比配分は5〜95%で指定してください。")
    if (type(search_min_pct) is not int or type(search_max_pct) is not int
            or not 5 <= search_min_pct < search_max_pct <= 95):
        raise ValueError("探索範囲は5〜95%の整数で、下限を上限より小さくしてください。")
    comparison = []
    settings = copy.deepcopy(inp)
    settings["parameters"]["air_mass_flow_kg_s"] = base["result"]["metrics"]["air_mass_flow_kg_s"]
    settings["mode"] = "airflow"
    best = None
    # Include equal split for reference even during manual evaluation.
    search_shares = {i/100 for i in range(search_min_pct,search_max_pct+1,2)}
    search_shares.add(search_max_pct/100)
    if search_min_pct <= 50 <= search_max_pct:
        search_shares.add(.5)
    eligible = search_shares if manual_share is None else {manual_share}
    shares = eligible | {.5}
    for share in sorted(shares):
        settings["staging"]["compressor"]["weights"] = [share, 1-share]
        record = {"stage1_share": share, "feasible": False}
        comparison.append(record)
        try:
            case = run_case(settings)
            train = case["result"]["stage_results"]["compressor"]
            sized = [size_stage(case, item, stage_number=i+1) for i,item in enumerate(stages)]
            duct = _return_channel(*sized, next(p for p in train["elements"] if p["name"] == "リターンチャネル"))
        except ValueError as exc:
            record["reason"] = str(exc)
            continue
        ratios = [row["pressure_ratio"] for row in train["stages"]]
        work = train["total_power_w"]/case["result"]["metrics"]["air_mass_flow_kg_s"]
        record.update(feasible=True, pressure_ratios=ratios, work_j_kg=work,
                      net_power_w=case["result"]["metrics"]["net_electric_power_w"])
        if share not in eligible:
            continue
        if best is None or (work, abs(share-.5)) < (best[0]["work_j_kg"], abs(best[0]["stage1_share"]-.5)):
            best = record, case, sized, duct
    report = {"format": "centrifugal-two-stage-design", "schema_version": 1,
              "source_run_id": base["run_id"], "source_input_hash": base["input_hash"],
              "selection": "manual" if manual_share is not None else "minimum_work_in_grid",
              "search_bounds_pct": [search_min_pct, search_max_pct],
              "candidates": comparison, "selected": None}
    if best is not None:
        record, case, sized, duct = best
        for item, edge in zip(sized, edges):
            item["blade_edges"] = edge
        report.update(selected=record, cycle_case=case, stages=sized, return_channel=duct)
        if vaned_options is not None:
            report["vaned_options"] = vaned_options
    return report
