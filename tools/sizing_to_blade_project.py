"""Convert a sizing report with the installed Blade Designer's native serializer."""
import argparse
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

if __package__:
    from . import vaned_two_stage
else:
    import vaned_two_stage


def write_project(report, output, *, six_elements=False):
    from blade_designer.fileio.project_io import load_project, save_project

    if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        raise ValueError("対応する概略寸法設計JSONを指定してください。")
    metadata = None
    if report.get("format") == "centrifugal-two-stage-design":
        parameters, metadata = _two_stage_parameters(report, six_elements)
    elif six_elements:
        raise ValueError("--six-elements には2段設計一式JSONを指定してください。")
    elif report.get("format") == "return-channel-meridional":
        parameters = _return_parameters(report)
    elif report.get("format") == "centrifugal-stage-sizing":
        parameters = _stage_parameters(report)
    else:
        raise ValueError("対応する概略寸法設計JSONを指定してください。")
    _write_checked_project(parameters, output, load_project, save_project, metadata)


def _return_parameters(report):
    from blade_designer.models.blade_rows import row_snapshot
    from blade_designer.models.meridional import LineSegmentParameters, MeridionalCurveParameters
    from blade_designer.models.parameters import BladeParameters, BladeRow

    def render_return_wall(points):
        a, *remaining = points
        segments = []
        prior = start_angle = None
        for b in remaining:
            dz, dr = b[0]-a[0], b[1]-a[1]
            angle = math.degrees(math.atan2(dr,dz))
            if start_angle is None:
                start_angle = angle
            turn = None if prior is None else (angle-prior+180)%360-180
            segments.append(LineSegmentParameters(1000*math.hypot(dz,dr), inlet_turn_deg=turn))
            prior, a = angle, b
        z,r = points[0]
        return MeridionalCurveParameters(1000*z, 1000*r, start_angle, segments)

    channel = BladeParameters(hub=render_return_wall(report["hub_zr_m"]),
                              shroud=render_return_wall(report["shroud_zr_m"]))
    channel.blade_rows = [BladeRow("return-channel", "Return channel walls", "meridional",
                                   row_snapshot(channel), True)]
    channel.active_blade_row = "return-channel"
    return channel


def _stage_parameters(report):
    from blade_designer.models.blade_edge import BladeEdgePosition, BladeRowEdgeParameters
    from blade_designer.models.blade_rows import row_snapshot
    from blade_designer.models.flow_path import StationType
    from blade_designer.models.meridional import (
        ArcSegmentParameters, LineSegmentParameters, MeridionalCurveParameters,
    )
    from blade_designer.models.parameters import BladeParameters, BladeRow, DistributionPoint, EdgeType

    def wall(spec):
        segments = [
            LineSegmentParameters(1000*part["length_m"]) if "length_m" in part else
            ArcSegmentParameters(1000*part["radius_m"], part["sweep_deg"])
            for part in spec["segments"]
        ]
        return MeridionalCurveParameters(1000*spec["start_z_m"], 1000*spec["start_r_m"],
                                         spec["start_angle_deg"], segments)

    def row_parameters(kind):
        walls = report["walls"][kind]
        return BladeParameters(hub=wall(walls["hub"]), shroud=wall(walls["shroud"]))

    rotor = row_parameters("impeller")
    diffuser = row_parameters("diffuser")
    # Reserve end space for closed blade profiles. Angles are proposal values
    # evaluated at the sizing boundaries and applied to the nearby blade edges.
    edge = report.get("blade_edges")
    le = edge["le_fraction"] if edge else .035
    te = edge["te_fraction"] if edge else .965
    rotor.impeller_edges = BladeRowEdgeParameters(BladeEdgePosition(le, le),
                                                 BladeEdgePosition(te, te), le, te)
    rotor.flow_path.set_position(StationType.DIF_TE, .99)
    rotor.flow_path.set_position(StationType.DIF_LE, .98)
    rotor.blade_le_fraction = le
    rotor.blade_te_fraction = te
    rotor.geometry.vane_count = edge["blade_count"] if edge else 12
    dims = report["dimensions"]
    thickness_scale = min(dims["impeller_exit_diameter_m"]*1000, 200)/200
    for section, side in zip(rotor.sections, ("hub", "tip")):
        beta1 = report["checks"][f"inlet_{side}_metal_angle_deg"]
        beta2 = -report["assumptions"]["backsweep_deg"]
        section.blade_angle.points = [DistributionPoint(0, beta1),
                                      DistributionPoint(.5, (beta1+beta2)/2),
                                      DistributionPoint(1, beta2)]
        if edge:
            thickness = [(0, edge["le_thickness_mm"]), (.4, edge["max_thickness_mm"]),
                         (1, edge["te_thickness_mm"])]
            section.edge_parameters.le_type = EdgeType(edge["le_type"])
            section.edge_parameters.te_type = EdgeType(edge["te_type"])
            section.edge_parameters.le_ellipse_ratio = edge["le_ellipse_ratio"]
            section.edge_parameters.te_ellipse_ratio = edge["te_ellipse_ratio"]
        else:
            thickness = [(x, thickness_scale*t) for x,t in ((0,.4), (.4,1.6), (1,.35))]
        section.thickness.points = [DistributionPoint(x, t) for x,t in thickness]
    rotor.blade_rows = [
        BladeRow("impeller", "Impeller – initial blades", "impeller", row_snapshot(rotor), True),
        BladeRow("diffuser", "Vaneless diffuser", "meridional", diffuser, True, "impeller",
                 diffuser.hub.start_angle_deg-90, diffuser.shroud.start_angle_deg-90),
    ]
    rotor.active_blade_row = "impeller"
    return rotor


def _two_stage_parameters(report, six_elements):
    from blade_designer.geometry.meridional_segments import generate_meridional, validate_flow_path
    from blade_designer.models.blade_rows import row_parameters

    selected = report.get("selected")
    if not isinstance(selected, dict) or selected.get("feasible") is not True:
        raise ValueError("成立した配分を含む2段設計一式JSONを指定してください。")
    stages = report["stages"]
    duct = report["return_channel"]
    cycle = report["cycle_case"]
    if (not isinstance(stages, list) or len(stages) != 2
            or any(not isinstance(stage, dict) or stage.get("format") != "centrifugal-stage-sizing"
                   or stage.get("schema_version") != 1 or not isinstance(stage.get("blade_edges"), dict)
                   for stage in stages)
            or not isinstance(duct, dict) or duct.get("format") != "return-channel-meridional"
            or duct.get("schema_version") != 1):
        raise ValueError("2段の寸法とリターンチャネル壁を含む設計一式JSONを指定してください。")
    cycle_stages = cycle["result"]["stage_results"]["compressor"]["stages"]
    ratios = selected["pressure_ratios"]
    if (len(ratios) != 2 or len(cycle_stages) != 2
            or any(not math.isclose(ratio, row["pressure_ratio"], rel_tol=1e-10)
                   for ratio, row in zip(ratios, cycle_stages))
            or any(stage["source"]["stage"] != index
                   or stage["source"]["input_hash"] != cycle["input_hash"]
                   or stage["source"]["run_id"] != cycle["run_id"]
                   for index, stage in enumerate(stages, 1))):
        raise ValueError("段の順番、圧力比、またはサイクルの参照元が一致しません。")

    first, second = [_stage_parameters(stage) for stage in stages]
    return_row = _return_parameters(duct).blade_rows[0]
    for index, stage in enumerate((first, second), 1):
        rotor, diffuser = stage.blade_rows
        rotor.identifier, rotor.name = f"impeller-{index}", f"Impeller {index} – initial blades"
        diffuser.identifier, diffuser.name = f"diffuser-{index}", f"Vaneless diffuser {index}"
    axial_offset_mm = 1000 * duct["stage2_axial_offset_m"]
    for row in second.blade_rows:
        row.parameters.hub.start_z += axial_offset_mm
        row.parameters.shroud.start_z += axial_offset_mm
    rows = [*first.blade_rows, return_row, *second.blade_rows]
    vaned = None
    if "vaned_options" in report or six_elements:
        options = vaned_two_stage.clean(report.get("vaned_options", vaned_two_stage.defaults()))
        vaned = vaned_two_stage.add_geometry(rows, options)
        vaned.update(options=options, options_origin="cycle_report" if "vaned_options" in report else "example_preset",
                     profile_basis="two_stage_vaned_axial_outlet")
    walls = {row.identifier: [generate_meridional(curve) for curve in (row.parameters.hub, row.parameters.shroud)]
             for row in rows}
    for hub, shroud in walls.values():
        validate_flow_path(hub, shroud)

    junctions = []
    for upstream, row in zip(rows, rows[1:]):
        junction = {"from": upstream.identifier, "to": row.identifier, "walls": {}}
        row.upstream_row = upstream.identifier
        for side, previous, following in zip(("hub", "shroud"), walls[upstream.identifier], walls[row.identifier]):
            outlet = previous.segments[-1]
            inlet = following.segments[0]
            gap_mm = math.dist(outlet.end.zr, inlet.start.zr)
            if gap_mm > 1e-8:
                raise ValueError(f"{upstream.name} → {row.name} の {side} 壁の端点が一致しません ({gap_mm:.6g} mm)。")
            before = math.degrees(math.atan2(outlet.end_tangent[1], outlet.end_tangent[0]))
            after = math.degrees(math.atan2(inlet.start_tangent[1], inlet.start_tangent[0]))
            turn_deg = (after - before + 180) % 360 - 180
            setattr(row, f"{side}_inlet_turn_deg", turn_deg)
            junction["walls"][side] = {"endpoint_gap_mm": gap_mm, "inlet_turn_deg": turn_deg,
                                       "upstream_zr_mm": outlet.end.zr.tolist(),
                                       "downstream_zr_mm": inlet.start.zr.tolist()}
        junctions.append(junction)

    first.blade_rows = rows
    first.active_blade_row = rows[0].identifier
    for row in rows:
        resolved = row_parameters(first, row)
        # Compare every segment boundary after resolving the native connection.
        for original, curve in zip(walls[row.identifier], (resolved.hub, resolved.shroud)):
            resolved_wall = generate_meridional(curve)
            if (len(original.segments) != len(resolved_wall.segments)
                    or math.dist(original.segments[0].start.zr, resolved_wall.segments[0].start.zr) > 1e-8
                    or any(math.dist(a.end.zr, b.end.zr) > 1e-8
                           for a, b in zip(original.segments, resolved_wall.segments))):
                raise ValueError(f"接続後に {row.name} の壁座標が変わりました。")

    canonical_report = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    metadata = {
        "format": "blade-designer-two-stage-handoff", "schema_version": 1,
        "source_report_content_sha256": hashlib.sha256(canonical_report.encode("utf-8")).hexdigest(),
        "source_run_id": report["source_run_id"], "source_input_hash": report["source_input_hash"],
        "allocated_cycle_run_id": cycle["run_id"], "allocated_cycle_input_hash": cycle["input_hash"],
        "cycle_module_version": cycle["module_version"], "cycle_context": cycle["input"].get("context"),
        "selection": report["selection"], "stage1_share": selected["stage1_share"],
        "stage_total_pressure_ratios": ratios, "compressor_work_j_kg": selected["work_j_kg"],
        "mass_flow_kg_s": stages[0]["cycle_targets"]["mass_flow_kg_s"],
        "shaft_speed_rpm": stages[0]["cycle_targets"]["shaft_speed_rpm"],
        "stage2_axial_offset_mm": axial_offset_mm, "junctions": junctions,
        "rows": [{"id": row.identifier, "geometry": "walls_only" if row.kind == "meridional" else "initial_blade"}
                 for row in rows],
        "pending_geometry": [] if vaned is not None else ["return_channel_guide_vanes", "outlet_turning_duct", "axial_guide_vane"],
    }
    if vaned is not None:
        metadata["vaned_geometry"] = vaned
    return first, metadata


def _write_checked_project(parameters, output, load_project, save_project, metadata=None):
    # Verify native save and reopen before publishing a newly named file.
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        candidate = Path(scratch)/"stage.json"
        save_project(candidate, parameters)
        reopened = load_project(candidate)
        if metadata is not None and "vaned_geometry" in metadata:
            metadata["row_diagnostics"] = vaned_two_stage.diagnose(reopened)
        payload = candidate.read_bytes()
        if metadata is None:
            _write_new_file(output, payload)
            return
        metadata["project_file"] = output.name
        metadata["project_sha256"] = hashlib.sha256(payload).hexdigest()
        companion = output.with_suffix(".handoff.json")
        _write_new_file(companion, (json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
        try:
            _write_new_file(output, payload)
        except BaseException:
            companion.unlink()
            raise


def _write_new_file(path, payload):
    stream = path.open("xb")
    try:
        with stream:
            stream.write(payload)
    except BaseException:
        path.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--six-elements", action="store_true", help="Use the six-element preset for a report without vane settings.")
    parser.add_argument("--designer", type=Path,
                        default=Path(__file__).resolve().parents[2]/"centrifugal_blade_designer")
    args = parser.parse_args()
    if args.report.resolve() == args.output.resolve() or args.output.exists():
        parser.error("未使用の出力ファイル名を指定してください。")
    if not (args.designer/"blade_designer"/"fileio"/"project_io.py").is_file():
        parser.error("--designer で遠心翼設計アプリのフォルダーを指定してください。")
    sys.path.append(str(args.designer.absolute()))
    try:
        write_project(json.loads(args.report.read_text(encoding="utf-8-sig")), args.output, six_elements=args.six_elements)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, ArithmeticError, ImportError, OSError) as exc:
        parser.exit(1, f"変換できませんでした: {exc}\n")
    print(args.output)


if __name__ == "__main__":
    main()
