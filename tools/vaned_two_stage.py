"""Six-element handoff inputs and Blade Designer geometry for a two-stage case."""
import math


WALLS = ("hub", "shroud")
ROWS = (
    ("diffuser-1", "第1段ディフューザー", "壁全長", 24, 14, 84, (20, 12, 5)),
    ("return-channel", "リターン案内翼", "戻り側の点列区間", 24, 100 * 2.5 / 24, 100 * 11.5 / 24, (30, 18, 5)),
    ("diffuser-2", "第2段ディフューザー", "壁全長", 24, 14, 84, (20, 12, 5)),
    ("axial-guide-vane", "軸方向ガイドベーン", "90°転向後の直線部", 32, 15, 85, (20, 10, 0)),
)
OUTLET_FIELDS = (
    ("hub_bend_radius_mm", "出口90°曲がり Hub半径 mm", 15, 0.001, None),
    ("axial_length_mm", "転向後の軸方向直線長 mm", 40, 0.001, None),
)


def schema():
    rows = []
    for identifier, label, region, count, le, te, angles in ROWS:
        fields = [
            ("blade_count", "翼枚数", count, 1, None),
            ("le_pct", f"前縁位置 %（{region}）", le, 0, 100),
            ("te_pct", f"後縁位置 %（{region}）", te, 0, 100),
        ]
        for name, title, value in zip(("le", "mid", "te"), ("前縁", "中央", "後縁"), angles):
            fields.append((f"{name}_angle_deg", f"{title}の翼角 °", value, -89, 89))
        fields.extend([
            ("le_thickness_mm", "前縁厚さ mm", .5, .001, None),
            ("max_thickness_mm", "最大厚さ mm", 1.2, .001, None),
            ("te_thickness_mm", "後縁厚さ mm", .35, .001, None),
        ])
        rows.append({"id": identifier, "label": label, "fields": fields})
    return {"rows": rows, "outlet_fields": OUTLET_FIELDS}


def defaults():
    definition = schema()
    result = {row["id"]: {name: initial for name, _, initial, _, _ in row["fields"]}
              for row in definition["rows"]}
    result["outlet"] = {name: initial for name, _, initial, _, _ in definition["outlet_fields"]}
    return result


def _clean_fields(raw, fields, label):
    if not isinstance(raw, dict) or set(raw) != {field[0] for field in fields}:
        raise ValueError(f"{label}の6要素設定をすべて指定してください。")
    for name, title, _, minimum, maximum in fields:
        value = raw[name]
        if (type(value) not in (int, float) or not math.isfinite(value)
                or value < minimum or (maximum is not None and value > maximum)):
            raise ValueError(f"{label}の{title}が指定範囲外です。")
    return dict(raw)


def clean(raw):
    definition = schema()
    if not isinstance(raw, dict) or set(raw) != {"outlet", *(row["id"] for row in definition["rows"])}:
        raise ValueError("4つの静翼列と出口流路の設定を指定してください。")
    options = {"outlet": _clean_fields(raw["outlet"], definition["outlet_fields"], "出口流路")}
    for row in definition["rows"]:
        label = row["label"]
        values = _clean_fields(raw[row["id"]], row["fields"], label)
        if type(values["blade_count"]) is not int:
            raise ValueError(f"{label}の翼枚数は整数で指定してください。")
        if not 0 < values["le_pct"] < values["te_pct"] < 100:
            raise ValueError(f"{label}は 0 < 前縁位置 < 後縁位置 < 100% としてください。")
        if values["max_thickness_mm"] < max(values["le_thickness_mm"], values["te_thickness_mm"]):
            raise ValueError(f"{label}の最大厚さは前後縁の厚さ以上としてください。")
        options[row["id"]] = values
    return options


def _angle(tangent):
    return math.degrees(math.atan2(tangent[1], tangent[0]))


def _set_vanes(row, name, settings, fractions):
    from blade_designer.models.blade_edge import BladeEdgePosition, BladeRowEdgeParameters
    from blade_designer.models.parameters import BladeSection, DistributionPoint

    row.name, row.kind = name, "diffuser"
    parameters = row.parameters
    parameters.geometry.vane_count = settings["blade_count"]
    profile = BladeSection(span=0.)
    profile.blade_angle.points = [DistributionPoint(x, settings[f"{name}_angle_deg"])
                                  for x, name in ((0., "le"), (.5, "mid"), (1., "te"))]
    profile.thickness.points = [DistributionPoint(x, settings[f"{name}_thickness_mm"])
                                 for x, name in ((0., "le"), (.4, "max"), (1., "te"))]
    parameters.diffuser_profile = profile
    leading, trailing = (BladeEdgePosition(*fractions(settings[name] / 100)) for name in ("le_pct", "te_pct"))
    parameters.diffuser_edges = BladeRowEdgeParameters(leading, trailing)


def _align_return_outlet(curves, walls):
    from blade_designer.models.meridional import LineSegmentParameters

    # Short axial ends preserve both outlet points and the stage-2 inlet plane.
    runout = min(5., .8 * min(wall.segments[-1].end.z - wall.segments[-1].start.z for wall in walls))
    if runout <= 0:
        raise ValueError("リターンチャネルの出口には下流向きの軸方向長さが必要です。")
    for curve, wall in zip(curves, walls):
        final = wall.segments[-1]
        dz = final.end.z - runout - final.start.z
        dr = final.end.r - final.start.r
        angle = _angle((dz, dr))
        curve.segments[-1] = LineSegmentParameters(math.hypot(dz, dr),
                                                  inlet_turn_deg=angle - _angle(wall.segments[-2].end_tangent))
        curve.segments.append(LineSegmentParameters(runout, inlet_turn_deg=-angle))
    return runout


def add_geometry(rows, options):
    """Extend a dimensioned five-element assembly; units inside Blade Designer are mm."""
    from blade_designer.geometry.meridional_segments import generate_meridional
    from blade_designer.models.blade_rows import row_snapshot
    from blade_designer.models.meridional import ArcSegmentParameters, LineSegmentParameters, MeridionalCurveParameters
    from blade_designer.models.parameters import BladeRow

    _, diffuser1, channel, _, diffuser2 = rows
    for row, index in ((diffuser1, 1), (diffuser2, 2)):
        _set_vanes(row, f"Vaned Diffuser {index}", options[row.identifier], lambda value: (value, value))

    curves = (channel.parameters.hub, channel.parameters.shroud)
    original = [generate_meridional(curve) for curve in curves]
    segments = len(original[0].segments)
    if segments < 2 or segments % 2 or len(original[1].segments) != segments:
        raise ValueError("リターン案内翼の位置指定には、等分点列による往路・戻り側の二つの区間が必要です。")
    runout = _align_return_outlet(curves, original)
    adjusted = [generate_meridional(curve) for curve in curves]

    def return_fractions(value):
        first_return_segment = segments // 2
        offset, fraction = divmod(value * first_return_segment, 1)
        index = first_return_segment + int(offset)
        result = []
        for before, after in zip(original, adjusted):
            length = sum(segment.length for segment in before.segments[:index]) + fraction * before.segments[index].length
            if length >= sum(segment.length for segment in after.segments[:-2]):
                raise ValueError("リターン案内翼の前後縁は出口整列部より上流に置いてください。")
            result.append(length / after.length)
        return result

    _set_vanes(channel, "Vaned Return Channel", options[channel.identifier], return_fractions)

    outlet = [generate_meridional(getattr(diffuser2.parameters, side)).segments[-1].end for side in WALLS]
    gap = outlet[0].z - outlet[1].z
    if gap <= 0 or not math.isclose(outlet[0].r, outlet[1].r, rel_tol=0, abs_tol=1e-8):
        raise ValueError("出口90°流路には、同じ半径にあるディフューザー出口端点が必要です。")
    settings = options["outlet"]
    length = settings["axial_length_mm"]
    radii = (settings["hub_bend_radius_mm"], settings["hub_bend_radius_mm"] + gap)
    parameters = row_snapshot(diffuser2.parameters)
    for side, point, radius in zip(WALLS, outlet, radii):
        setattr(parameters, side, MeridionalCurveParameters(point.z, point.r, 90., [
            ArcSegmentParameters(radius, 90., clockwise=True), LineSegmentParameters(length),
        ]))
    guide = BladeRow("axial-guide-vane", "90 deg Turn + Axial Guide Vane", "diffuser", parameters, True)

    def guide_fractions(value):
        turn_lengths = (math.pi * radius / 2 for radius in radii)
        return tuple((turn + value * length) / (turn + length) for turn in turn_lengths)

    _set_vanes(guide, guide.name, options[guide.identifier], guide_fractions)
    rows.append(guide)
    return {"return_axial_runout_mm": runout, "outlet_bend_deg": 90,
            "outlet_bend_radii_mm": dict(zip(WALLS, radii)), "outlet_axial_length_mm": length}


def diagnose(project):
    """Read the actual exported geometry using Blade Designer's own analysis."""
    from blade_designer.geometry.blade_edge import edge_endpoints
    from blade_designer.geometry.flow_area import calculate_flow_area
    from blade_designer.geometry.preview_computation import prepare_row_previews
    from blade_designer.models.blade_rows import row_parameters

    previews = prepare_row_previews(project)
    if previews.errors:
        raise ValueError("6要素の翼形状を生成できません。" + "\n".join(previews.errors))
    geometries = {identifier: geometry for identifier, _, geometry, _ in previews.entries}
    diagnostics = []
    for row in project.blade_rows:
        geometry = geometries[row.identifier]
        hub, shroud = geometry.hub, geometry.shroud
        parameters = row_parameters(project, row)
        edges = parameters.impeller_edges if row.kind == "impeller" else parameters.diffuser_edges
        area = calculate_flow_area(hub, shroud, parameters.flow_path, points=parameters.resolution.area_points,
                                   mean_points=parameters.resolution.mean_points, strict=False)
        named = area.named_stations
        diagnostics.append({
            "id": row.identifier, "blade_count": parameters.geometry.vane_count,
            "le_zr_mm": edge_endpoints(edges.leading_edge, hub, shroud).tolist(),
            "te_zr_mm": edge_endpoints(edges.trailing_edge, hub, shroud).tolist(),
            "inlet_area_mm2": named["INLET"].gross_area if named["INLET"] is not None else None,
            "outlet_area_mm2": named["OUTLET"].gross_area if named["OUTLET"] is not None else None,
            "flow_area_warnings": area.errors,
        })
    guide = geometries["axial-guide-vane"]
    for section, wall in zip(guide.sections, (guide.hub, guide.shroud)):
        axial = wall.segments[-1]
        for part in (section.le, section.ps, section.te, section.ss):
            z = part.xyz[:, 2]
            if z.min() <= axial.start.z or z.max() >= axial.end.z:
                raise ValueError("軸方向ガイドベーンの輪郭を90°転向後の直線部内に収めてください。")
    return diagnostics
