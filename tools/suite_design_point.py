"""Recalculate the suite's representative design point and write its source-linked reports."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from gas_turbine_cycle_app.design_exchange import cycle_source
from gas_turbine_cycle_app.design_tools import run_isolated
from gas_turbine_cycle_app.engine import run_case
from gas_turbine_cycle_app import evaluation
from gas_turbine_cycle_app.two_stage_design import sample_cycle_input
from .suite_support import review


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "roadmap"


def read_json(path):
    return json.loads(path.read_bytes())


def calculate(project=None, stage1_share=None, *, observation_data=None, observation_kind=None, observation_reference=None):
    initial = sample_cycle_input()
    if stage1_share is not None:
        if type(stage1_share) not in (int, float) or not .05 <= stage1_share <= .95:
            raise ValueError("第1段の対数全圧比分配は0.05～0.95で指定してください。")
        initial["staging"]["compressor"]["weights"] = [stage1_share, 1 - stage1_share]
    if project is not None:
        initial["context"] = {"project_id": project["project"]["id"],
                              "source_revision": project["revision"], "component_id": None}
    base = run_case(initial)

    def settings(name):
        return read_json(EXAMPLES / f"{name}.json")

    combustor = run_isolated("combustor", {"cycle": base, "settings": settings("combustor")})
    turbine_input = combustor["cycle_case"]
    turbine = run_isolated("turbine", {"cycle": turbine_input, "settings": settings("turbine")})
    cooling = run_isolated("cooling", {"cycle": turbine_input,
                           "settings": dict(settings("cooling-coupled"), turbine=turbine)})
    if cooling["cycle_feedback"] != "coupled_design_point":
        raise ValueError(f"通しケースの冷却反復が成立しません。{cooling['cycle_feedback']}")
    cooled = cooling["cycle_case"]
    thrust = run_isolated("thrust-seal", {"cycle": cooled,
                          "settings": dict(settings("thrust-seal"), thrust=settings("thrust"))})
    oil = run_isolated("oil", {"settings": settings("oil")})
    rotor_setup = settings("rotor")
    rated = oil["conditions"]["定格"]
    for variant in rotor_setup["cases"].values():
        variant["oil_supply"] = {key: rated[key] for key in ("temperature_c", "bearings")}
    rotor = run_isolated("rotor", {"cycle": cooled, "settings": rotor_setup})
    test_input = evaluation.example_input(cooled["input"])
    test_input.update(baseline_source="冷却連成後の仮想設計点",
                      notes="計算値に既知の差を加えた仮想CSV。試験評価の手順確認用。")
    tested = evaluation.run_case(test_input)
    more_dilution = deepcopy(settings("combustor"))
    more_dilution["air_split"].update(primary=.30, dilution=.37)
    more_dilution["loss_coefficient"] = 2.4
    combustor_option = run_isolated("combustor", {"cycle": base, "settings": more_dilution})
    reports = {"cycle-base": base, "combustor": combustor, "combustor-option": combustor_option,
            "cycle-before-cooling": turbine_input, "turbine": turbine, "cooling": cooling,
            "cycle-cooled": cooled, "thrust": thrust, "oil": oil, "rotor": rotor, "test-evaluation": tested}
    reports["support-review"] = review(reports, observation_data=observation_data, observation_kind=observation_kind,
                                       observation_reference=observation_reference)
    return reports


def write_run(output, reports, *, stage1_share=None):
    if output.exists():
        raise FileExistsError("新しい出力フォルダー名を指定してください。")
    summary = {
        "net_electric_power_w": reports["cycle-cooled"]["result"]["metrics"]["net_electric_power_w"],
        "coolant_flow_kg_s": sum(row["coolant_flow_kg_s"] for row in reports["cooling"]["injections"]),
        "coupling_iterations": len(reports["cooling"]["coupling"]["iterations"]),
        "bearing_reactions_n": {name: row["thrust"]["bearing_reaction"]["force_n"]
                                for name, row in reports["thrust"]["cases"].items()},
        "oil_supply": {name: report["bearings"] for name, report in reports["oil"]["conditions"].items()},
        "rotor_response_um": {name: row["operating_amplitude_um"]
                               for name, row in reports["rotor"]["cases"].items()},
    }
    with TemporaryDirectory(dir=output.parent, prefix="gt-suite-") as tmp:
        stage = Path(tmp) / "output"
        stage.mkdir()
        artifacts = {}
        for name, result in reports.items():
            filename = f"{name}.json"
            data = (json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
            (stage / filename).write_bytes(data)
            artifacts[name] = {"file": filename, "format": result["format"], "sha256": sha256(data).hexdigest(),
                               "source_cycle": result.get("source_cycle")}
        name = "遠心2段・単管燃焼器・軸流3段の定格設計点"
        if stage1_share is not None:
            name += f"（第1段配分 {stage1_share * 100:g}%）"
        manifest = {"format": "gas-turbine-suite-design-point", "schema_version": 1,
                    "name": name,
                    "source_kind": "synthetic", "source_cycle": cycle_source(reports["cycle-base"]),
                    "summary": summary, "artifacts": artifacts}
        (stage / "suite-report.json").write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                                                 encoding="utf-8")
        stage.rename(output)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project", type=Path, help="Optional Design Desk project export")
    parser.add_argument("--stage1-share", type=float, help="第1段の対数全圧比分配（0.05～0.95）")
    parser.add_argument("--observations", type=Path, help="熱と二次空気の照合用CSV")
    parser.add_argument("--observation-kind", choices=("test", "cfd", "synthetic"))
    parser.add_argument("--observation-reference")
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise FileExistsError("新しい出力フォルダー名を指定してください。")
        project = read_json(args.project) if args.project is not None else None
        reports = calculate(project, args.stage1_share, observation_data=args.observations.read_bytes() if args.observations else None,
                            observation_kind=args.observation_kind, observation_reference=args.observation_reference)
        report = write_run(args.output.resolve(), reports, stage1_share=args.stage1_share)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(report["summary"], ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
