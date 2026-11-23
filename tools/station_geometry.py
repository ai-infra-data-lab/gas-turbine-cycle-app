"""Read the six-element native compressor geometry in Blade Designer's Python."""
import argparse
import json
import math
from pathlib import Path
import sys


ROWS = (
    ("impeller-1", "CP-01-IMP-LE", "CP-01-IMP-TE"),
    ("diffuser-1", "CP-01-DIF-IN", "CP-01-DIF-OUT"),
    ("return-channel", "CP-01-RC-LE", "CP-01-RC-TE"),
    ("impeller-2", "CP-02-IMP-LE", "CP-02-IMP-TE"),
    ("diffuser-2", "CP-02-DIF-IN", "CP-02-DIF-OUT"),
    ("axial-guide-vane", "CP-AGV-LE", "CP-AGV-TE"),
)


def gross_area(points):
    hub, shroud = points
    if min(hub[1], shroud[1]) <= 0 or math.dist(hub, shroud) <= 0:
        raise ValueError("評価断面の壁座標が不正です。")
    return math.pi * (hub[1] + shroud[1]) * math.dist(hub, shroud) * 1e-6


def extract(project_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "centrifugal_blade_designer"))
    from blade_designer.fileio.project_io import load_project
    from blade_designer.geometry.blade_edge import edge_endpoints
    from blade_designer.geometry.preview_computation import prepare_row_previews
    from blade_designer.models.blade_rows import row_parameters

    project = load_project(project_path)
    if [row.identifier for row in project.blade_rows] != [entry[0] for entry in ROWS]:
        raise ValueError("遠心2段の翼付き6要素プロジェクトを指定してください。")
    previews = prepare_row_previews(project)
    if previews.errors:
        raise ValueError("翼列の形状生成に失敗しました。" + "\n".join(previews.errors))
    geometries = {identifier: geometry for identifier, _, geometry, _ in previews.entries}
    stations = {}
    for row, (_, le, te) in zip(project.blade_rows, ROWS, strict=True):
        geometry = geometries[row.identifier]
        params = row_parameters(project, row)
        edges = params.impeller_edges if row.kind == "impeller" else params.diffuser_edges
        for code, edge in ((le, edges.leading_edge), (te, edges.trailing_edge)):
            points = edge_endpoints(edge, geometry.hub, geometry.shroud).tolist()
            stations[code] = {
                "area_m2": gross_area(points), "area_basis": "翼端基準の全周グロス回転面",
                "wall_zr_mm": points,
            }
    guide = geometries["axial-guide-vane"]
    hub, shroud = (wall.segments[-1] for wall in (guide.hub, guide.shroud))
    hub_start, shroud_start = hub.start.zr.tolist(), shroud.start.zr.tolist()
    if (not math.isclose(hub_start[0], shroud_start[0], abs_tol=1e-6, rel_tol=0.)
            or any(abs(segment.start_tangent[1]) > 1e-8 for segment in (hub, shroud))):
        raise ValueError("90°転向後の軸方向直線部の入口断面を特定できません。")
    points = [hub_start, shroud_start]
    stations["CP-BEND-OUT"] = {"area_m2": gross_area(points), "area_basis": "直線部入口の全周環状面",
                               "wall_zr_mm": points}
    return stations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        area = extract(args.project)
        args.output.write_text(json.dumps(area, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    except (ValueError, ImportError, OSError, KeyError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
