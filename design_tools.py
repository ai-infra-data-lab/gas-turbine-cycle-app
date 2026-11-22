"""Entrypoint for local, file-based exchange with the independent design apps."""
import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__:
    from .design_exchange import verified_cycle
    from .engine import run_case
    from .two_stage_design import sample_cycle_input


ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    "thrust": (None, "gas_turbine_cycle_app.rotor_thrust"),
    "turbine": ("axial_turbine_designer", "cycle_handoff"),
    "cooling": ("secondary_air_network", "cycle_handoff"),
    "combustor": (None, "single_can_combustor"),
    "oil": ("pipe_pressure_loss_app", "bearing_supply"),
    "rotor": ("rotordynamics_app", "design_handoff"),
}


def calculate(tool, request):
    app, module = TARGETS[tool]
    if app is not None:
        sys.path.insert(0, str(ROOT / app))
    if tool == "combustor" and not (ROOT / "single_can_combustor" / "__init__.py").is_file():
        module = "gas_turbine_cycle_app.single_can_combustor"
    evaluate = importlib.import_module(module).evaluate
    setup = request["settings"]
    if not isinstance(setup, dict):
        raise ValueError("連携設定はJSONオブジェクトで指定してください。")
    if tool == "oil":
        return evaluate(setup)
    if request.get("demo_cycle") is True and request.get("cycle") is None:
        document = run_case(sample_cycle_input())
    else:
        document = request["cycle"]
    if tool in ("thrust", "combustor"):
        return evaluate(document, setup)
    case, source = verified_cycle(document)
    case["run_id"] = source["run_id"]
    return evaluate(case, setup)


def python_for(tool):
    app, _ = TARGETS[tool]
    if tool not in ("rotor", "oil"):
        return sys.executable
    override = os.environ.get(f"GT_WORKFLOW_{tool.upper()}_PYTHON")
    if override:
        return override
    candidates = (
        Path("C:/CodexAppEnvs") / app / "Scripts" / "python.exe",
        ROOT / app / ".venv" / "Scripts" / "python.exe",
        ROOT / app / ".venv" / "bin" / "python",
    )
    return str(next((candidate for candidate in candidates if candidate.is_file()), sys.executable))


def run_isolated(tool, request, *, timeout=100):
    if not isinstance(tool, str) or tool not in TARGETS:
        raise ValueError("連携機能を選択してください。")
    try:
        completed = subprocess.run(
            [python_for(tool), "-m", "gas_turbine_cycle_app.design_tools", tool],
            cwd=ROOT, input=json.dumps(request, ensure_ascii=False, allow_nan=False), encoding="utf-8",
            capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "PYTHONUTF8":"1", "OPENBLAS_NUM_THREADS":"1", "OMP_NUM_THREADS":"1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("連携計算が制限時間に達しました。入力規模を確認してください。") from exc
    except OSError as exc:
        raise ValueError("連携先のPython環境を起動できません。") from exc
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or "連携計算が終了しました。")
    return json.loads(completed.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=TARGETS)
    parser.add_argument("--input", type=Path, help="Exchange request JSON, otherwise read stdin.")
    parser.add_argument("--output", type=Path, help="New output JSON, otherwise write stdout.")
    args = parser.parse_args(argv)
    try:
        def reject_constant(value):
            raise ValueError("非有限数を使用できません。")

        raw = args.input.read_text(encoding="utf-8-sig") if args.input else sys.stdin.read()
        request = json.loads(raw, parse_constant=reject_constant)
        if not isinstance(request, dict):
            raise ValueError("連携リクエストはJSONオブジェクトです。")
        result = calculate(args.tool, request)
        output = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)+"\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(output)
        else:
            sys.stdout.write(output)
    except (ImportError, OSError, ValueError, TypeError, KeyError, ArithmeticError, IndexError, AttributeError) as exc:
        parser.exit(2, str(exc)+"\n")


if __name__ == "__main__":
    main()
