"""Join native compressor and turbine geometry with station pressures."""
import argparse
import csv
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from .station_geometry import ROWS


ROOT = Path(__file__).resolve().parents[2]
COMPRESSOR_ELEMENTS = (
    (1, "インペラー"), (1, "ディフューザー"), (1, "リターンチャネル"),
    (2, "インペラー"), (2, "ディフューザー"),
)
COMPRESSOR_LOSSES = (
    ("第1段ディフューザ", "CP-01-DIF-IN", "CP-01-DIF-OUT"),
    ("リターンチャネル", "CP-01-RC-LE", "CP-01-RC-TE"),
    ("第2段ディフューザ", "CP-02-DIF-IN", "CP-02-DIF-OUT"),
    ("90°転向区間", "CP-02-DIF-OUT", "CP-BEND-OUT"),
    ("軸方向ガイドベーン", "CP-AGV-LE", "CP-AGV-TE"),
)


def positive(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label}は有限の正数で指定してください。")
    return value


def canonical_hash(document):
    data = json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return sha256(data.encode("utf-8")).hexdigest()


def native_geometry(content, python):
    candidates = (Path("C:/CodexAppEnvs/centrifugal_blade_designer/Scripts/python.exe"),
                  ROOT / "centrifugal_blade_designer" / ".venv" / "Scripts" / "python.exe",
                  ROOT / "centrifugal_blade_designer" / ".venv" / "bin" / "python")
    executable = python or next((path for path in candidates if path.is_file()), sys.executable)
    with TemporaryDirectory(prefix="gt-station-review-") as temporary:
        project = Path(temporary) / "project.json"
        result = Path(temporary) / "areas.json"
        project.write_bytes(content)
        try:
            process = subprocess.run(
                [str(executable), "-m", "gas_turbine_cycle_app.tools.station_geometry", str(project), str(result)],
                cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "PYTHONUTF8": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
            )
        except subprocess.TimeoutExpired:
            raise ValueError("Blade Designerの断面計算が制限時間に達しました。") from None
        if process.returncode:
            raise ValueError(process.stderr.strip() or "Blade Designerの断面を取得できませんでした。")
        return json.loads(result.read_bytes())


def compressor_stations(content, handoff, report, *, blade_python=None):
    digest = sha256(content).hexdigest()
    if (handoff.get("format") != "blade-designer-two-stage-handoff" or handoff.get("schema_version") != 1
            or handoff.get("project_sha256") != digest):
        raise ValueError("翼形状JSONと .handoff.json のSHA-256が一致しません。")
    if (report.get("format") != "centrifugal-two-stage-design"
            or canonical_hash(report) != handoff["source_report_content_sha256"]):
        raise ValueError("翼形状の入力元にあたる遠心2段設計一式JSONを指定してください。")
    cycle = report["cycle_case"]
    if (cycle["run_id"] != handoff["allocated_cycle_run_id"]
            or cycle["input_hash"] != handoff["allocated_cycle_input_hash"]):
        raise ValueError("遠心2段設計一式のサイクル実行IDと入力ハッシュが一致しません。")
    compressor = cycle["result"]["stage_results"]["compressor"]
    elements = {(item["stage"], item["name"]): item for item in compressor["elements"]}
    if compressor["count"] != 2 or set(elements) != set(COMPRESSOR_ELEMENTS):
        raise ValueError("遠心2段の要素別サイクル結果を指定してください。")
    geometry = native_geometry(content, blade_python)
    stations = {code: {**detail, "model_total_pressure_pa": None, "pressure_basis": None}
                for code, detail in geometry.items()}
    for (_, le, te), key in zip(ROWS[:-1], COMPRESSOR_ELEMENTS, strict=True):
        for code, end in ((le, "inlet"), (te, "outlet")):
            stations[code].update(
                model_total_pressure_pa=positive(elements[key][end]["total_pressure_pa"], code),
                pressure_basis="Cycle Studioの要素入口・出口を形状断面に割り当て",
            )
    return stations, {"geometry_sha256": digest, "run_id": cycle["run_id"], "input_hash": cycle["input_hash"],
                      "context": cycle["input"]["context"]}


def turbine_stations(report, case_name):
    if report.get("format") != "axial-turbine-cycle-handoff" or report.get("schema_version") != 1:
        raise ValueError("軸流タービンの連携結果一式を指定してください。")
    case = report["cases"].get(case_name)
    if case is None:
        raise ValueError("軸流タービンの案名を確認してください。")
    design, analysis = case["design"], case["analysis"]
    if canonical_hash(analysis["input_snapshot"]) != canonical_hash(design):
        raise ValueError("軸流タービンの形状と解析入力のスナップショットが一致しません。")
    if not design["meridional"]["enabled"] or not design["analysis"]["meridional_area"]:
        raise ValueError("軸流タービンは子午面積を反映した連携結果を指定してください。")
    stations, losses = {}, []
    for row in analysis["meridional"]["layout"]:
        role = "NGV" if row["kind"] == "stator" else "ROT"
        prefix = f"TB-{row['stage']:02d}-{role}"
        for end in ("LE", "TE"):
            code = f"{prefix}-{end}"
            reference = case["stations"][code]
            flow = reference["flow"]
            stations[code] = {
                "area_m2": positive(flow["area"], code) if flow is not None else None,
                "area_basis": "子午面の軸方向環状面" if flow is not None else "環状面の計算値を取得できませんでした",
                "model_total_pressure_pa": positive(flow["total_pressure_pa"], code) if flow is not None else None,
                "pressure_basis": ("静翼全圧一定の仮定" if role == "NGV" else "軸流タービンの段計算"),
                "x_m": reference["x_m"], "row_id": row["row_id"],
            }
        if role == "NGV":
            losses.append((f"タービン第{row['stage']}段静翼", f"{prefix}-LE", f"{prefix}-TE"))
    for code, basis in (("TB-EXD-IN", "最終動翼出口の環状面"), ("TB-EXD-OUT", "指定面積比による概略面積")):
        flow = case["stations"][code]["flow"]
        stations[code] = {"area_m2": positive(flow["area"], code), "area_basis": basis,
                          "model_total_pressure_pa": positive(flow["total_pressure_pa"], code),
                          "pressure_basis": "排気ディフューザ全圧残存比モデル"}
    losses.append(("排気ディフューザ", "TB-EXD-IN", "TB-EXD-OUT"))
    return stations, losses, {"geometry_sha256": canonical_hash(design), "case": case_name,
                              **report["source_cycle"]}


def read_pressure_csv(content, stations):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    if reader.fieldnames != ["station_code", "total_pressure_pa"]:
        raise ValueError("全圧CSVの列は station_code,total_pressure_pa です。")
    result = {}
    for row in reader:
        code = (row.get("station_code") or "").strip()
        if code not in stations or code in result or None in row:
            raise ValueError(f"全圧CSVの断面コードまたは列数が不正です（{reader.line_num}行目）。")
        result[code] = positive(float(row.get("total_pressure_pa") or "nan"), f"{code} 絶対全圧")
    if not result:
        raise ValueError("全圧CSVには評価値を1件以上記載してください。")
    return result


def assemble(stations, intervals, sources, csv_bytes=None, csv_kind=None, csv_reference=None):
    observed = {}
    if csv_bytes is not None:
        if csv_kind not in ("test", "cfd", "synthetic") or not csv_reference or not csv_reference.strip():
            raise ValueError("全圧CSVの測定・計算条件と出典区分を指定してください。")
        observed = read_pressure_csv(csv_bytes, stations)
        sources = {**sources, "pressure_csv": {"kind": csv_kind, "reference": csv_reference.strip(),
                                               "sha256": sha256(csv_bytes).hexdigest()}}
    rows = {code: {**value, "observed_total_pressure_pa": observed.get(code)} for code, value in stations.items()}

    def loss(a, b, field):
        first, last = rows[a][field], rows[b][field]
        return first - last if first is not None and last is not None else None

    return {
        "format": "gas-turbine-station-review", "schema_version": 1, "name": "遠心2段・軸流タービンの評価断面照合",
        "sources": sources, "stations": [{"code": code, **row} for code, row in rows.items()],
        "losses": [{"name": label, "inlet": a, "outlet": b,
                    "model_loss_pa": loss(a, b, "model_total_pressure_pa"),
                    "observed_loss_pa": loss(a, b, "observed_total_pressure_pa")} for label, a, b in intervals],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("blade-project", "blade-handoff", "compressor-design", "turbine-report", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--turbine-case", required=True)
    parser.add_argument("--blade-python", type=Path)
    parser.add_argument("--pressure-csv", type=Path)
    parser.add_argument("--pressure-kind", choices=("test", "cfd", "synthetic"))
    parser.add_argument("--pressure-reference")
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_bytes())
    try:
        if args.output.exists():
            raise ValueError("新しい出力ファイル名を指定してください。")
        compressor, cp_source = compressor_stations(args.blade_project.read_bytes(), read(args.blade_handoff),
                                                     read(args.compressor_design), blade_python=args.blade_python)
        turbine, tb_loss, tb_source = turbine_stations(read(args.turbine_report), args.turbine_case)
        doc = assemble({**compressor, **turbine}, (*COMPRESSOR_LOSSES, *tb_loss),
                       {"compressor": cp_source, "turbine": tb_source},
                       args.pressure_csv.read_bytes() if args.pressure_csv else None,
                       args.pressure_kind, args.pressure_reference)
        content = json.dumps(doc, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except (ValueError, TypeError, OSError, KeyError) as exc:
        parser.exit(2, f"{exc}\n")
    print(f"{len(doc['stations'])}評価断面、{len(doc['losses'])}損失区間")


if __name__ == "__main__":
    main()
