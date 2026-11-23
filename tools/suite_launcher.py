"""Common Windows entry point and isolated timing checks for the design apps."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from gas_turbine_cycle_app.design_tools import app_directory


ROOT = Path(__file__).resolve().parents[2]
# Label, app directory, entry point, default web port, native flags
APPS = {
    "desk": ("Design Desk", "gas_turbine_manager", "server.py", 8770, ()),
    "cycle": ("Cycle Studio", "gas_turbine_cycle_app", "server.py", 8780, ()),
    "turbine": ("軸流タービン", "axial_turbine_designer", "server.py", 8782, ("--open",)),
    "secondary-air": ("二次空気", "secondary_air_network", "server.py", 8790, ("--open",)),
    "rotor": ("RotorLab", "rotordynamics_app", "server.py", 8765, ()),
    "blade": ("遠心翼設計", "centrifugal_blade_designer", "main.py", None, ()),
    "oil": ("油配管", "pipe_pressure_loss_app", "main.py", None, ()),
    "combustor": ("単管燃焼器", "single_can_combustor", "__main__.py", None, ()),
}
NATIVE_ENVS = {"blade", "oil", "rotor"}


def python_for(app):
    directory = APPS[app][1]
    override = os.environ.get(f"GT_SUITE_{app.upper().replace('-', '_')}_PYTHON")
    if override:
        return Path(override)
    if app not in NATIVE_ENVS:
        return Path(sys.executable)
    targets = (Path("C:/CodexAppEnvs") / directory / "Scripts" / "python.exe",
               app_directory(directory) / ".venv" / "Scripts" / "python.exe",
               app_directory(directory) / ".venv" / "bin" / "python")
    return next((path for path in targets if path.is_file()), Path(sys.executable))


def start(app, port):
    is_combustor = app == "combustor"
    if is_combustor:
        # The combustor's interactive entry is the existing Cycle Studio handoff screen.
        import webbrowser
        from urllib.request import urlopen
        chosen = port if port is not None else APPS["cycle"][3]
        url = f"http://127.0.0.1:{chosen}/design-tools?tool=combustor"
        try:
            with urlopen(url, timeout=2) as response:
                if b"id=\"tool\"" not in response.read():
                    raise ValueError("指定ポートにCycle Studioの設計アプリ連携がありません。")
        except OSError:
            app = "cycle"
        else:
            webbrowser.open(url)
            return 0
    label, directory, entry, default_port, extras = APPS[app]
    path = app_directory(directory)
    if port is not None and default_port is None:
        raise ValueError("ポートはWebアプリに指定してください。")
    args = [str(python_for(app)), str(path / entry), *extras]
    if default_port is not None:
        args.extend(("--port", str(default_port if port is None else port)))
    if is_combustor:
        args.extend(("--open-tool", "combustor"))
    print(f"{label} · {args[0]}", flush=True)
    return subprocess.call(args, cwd=path, env={**os.environ, "PYTHONUTF8":"1"})


def web_probe(app, directory):
    import secrets
    from threading import Thread
    from urllib.request import urlopen
    start = perf_counter()
    import server
    db = directory / "probe.sqlite3"
    if app in ("desk", "cycle"):
        service = server.make_server(db, 0)
    elif app == "turbine":
        service = server.make_server(0, db)
    else:
        service = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        if app == "secondary-air":
            service.store = server.Store(db)
            service.token = secrets.token_urlsafe(16)
    with service:
        thread = Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{service.server_port}/", timeout=10) as response:
                if not response.read():
                    raise RuntimeError("起動時のHTMLが空です。")
            startup = perf_counter() - start
            before = perf_counter()
            if app == "desk":
                project = service.store.create("起動検証", template="single_shaft")
                solution = service.store.configure_stations(project["project"]["id"], 1,
                            {"compressor_stages":2, "turbine_stages":3, "axial_guide_vanes":True})
                operation, evidence = "仮案件の評価断面を作成", {"stations":len(solution["project"]["stations"])}
            elif app == "cycle":
                from engine import run_case
                from model import default_input
                solution = run_case(default_input())
                operation, evidence = "仮想サイクル設計点", {"status":solution["result"]["status"]}
            elif app == "turbine":
                from core import new_design, solve
                solution = solve(new_design())
                operation, evidence = "軸流タービンの1D設計点", {"stages":len(solution["stages"])}
            elif app == "secondary-air":
                solution = server.solve(server.sample())
                operation, evidence = "仮想二次空気ネットワーク", {"status":solution["status"]}
            else:
                from engine import analyze
                from model import sample_model
                solution = analyze(sample_model(), include_shapes=False)
                operation, evidence = "仮想ロータのCampbell計算", {"operating_index":solution["operating_index"]}
            return "HTTP最初のHTMLを受信", startup, operation, perf_counter() - before, evidence
        finally:
            service.shutdown()
            thread.join()


def desktop_probe(app, directory):
    os.environ.update(QT_QPA_PLATFORM="offscreen", MPLCONFIGDIR=str(directory / "matplotlib"))
    start = perf_counter()
    from PySide6.QtWidgets import QApplication
    qt = QApplication([])
    if app == "blade":
        from main import create_window, parse_arguments
        window = create_window(parse_arguments([]))
    else:
        from examples import full_demo
        from ui import MainWindow
        project = full_demo()
        window = MainWindow(project)
    try:
        window.show()
        qt.processEvents()
        startup = perf_counter() - start
        before = perf_counter()
        if app == "blade":
            from blade_designer.fileio.project_io import load_project
            from blade_designer.geometry.preview_computation import prepare_preview
            params = load_project(ROOT / "centrifugal_blade_designer" / "blade_designer/defaults/default_project.json")
            result = prepare_preview(params, "impeller")
            if result.selected_error or result.area is None:
                raise ValueError(result.selected_error or "代表翼の面積が得られません。")
            operation, evidence = "遠心翼と流路面積の計算", {"area_stations":len(result.area.stations)}
        else:
            from solver import solve
            result = solve(project)
            if not result["valid"]:
                raise ValueError("給油ネットワークの例が成立しません。")
            operation, evidence = "仮想油配管の定常計算", {"valid":True}
        return "Qtオフスクリーンの最初のイベント処理", startup, operation, perf_counter() - before, evidence
    finally:
        window.close()
        qt.processEvents()


def probe(app, directory):
    sys.path.insert(0, str(app_directory(APPS[app][1])))
    if app in ("blade", "oil"):
        return desktop_probe(app, directory)
    if app == "combustor":
        start = perf_counter()
        from gas_turbine_cycle_app.design_tools import calculate
        settings = json.loads((ROOT / "gas_turbine_cycle_app/examples/roadmap/combustor.json").read_bytes())
        ready = perf_counter()
        solution = calculate("combustor", {"demo_cycle":True, "settings":settings})
        return ("燃焼器モジュール・仮想条件の読込", ready-start, "熱収支・出口分布・ライナの計算",
                perf_counter()-ready, {"pattern_factor":solution["thermal"]["outlet_profile"]["pattern_factor"]})
    return web_probe(app, directory)


def check(apps, output):
    if output.exists():
        raise ValueError("新しい検証レポート名を指定してください。")
    results = {}
    with TemporaryDirectory(prefix="gt-suite-check-") as temp:
        for app in apps:
            path = Path(temp) / app
            path.mkdir()
            report = path / "probe.json"
            executable = python_for(app)
            elapsed = perf_counter()
            try:
                completed = subprocess.run(
                    [str(executable), "-m", "gas_turbine_cycle_app.tools.suite_launcher", "_probe", app, str(path)],
                    cwd=ROOT, env={**os.environ, "PYTHONUTF8":"1", "OPENBLAS_NUM_THREADS":"1", "OMP_NUM_THREADS":"1"},
                    capture_output=True, encoding="utf-8", errors="replace", timeout=240,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode or not report.is_file():
                    raise ValueError(completed.stderr.strip()[-2000:] or "検証プロセスが終了しました。")
                results[app] = json.loads(report.read_bytes())
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                results[app] = {"status":"error", "reason":str(exc)}
            results[app].update(python=str(executable), subprocess_elapsed_s=perf_counter()-elapsed)
            print(f"{APPS[app][0]} · {results[app]['status']}", flush=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump({"format":"gas-turbine-suite-startup-check", "schema_version":1, "apps":results},
                  stream, ensure_ascii=False, allow_nan=False, indent=2)
    return int(any(row["status"] != "ok" for row in results.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="command")
    modes.add_parser("list")
    launcher = modes.add_parser("start")
    launcher.add_argument("app", choices=APPS)
    launcher.add_argument("--port", type=int)
    bench = modes.add_parser("check")
    bench.add_argument("apps", nargs="*")
    bench.add_argument("--output", required=True, type=Path)
    worker = modes.add_parser("_probe", help=argparse.SUPPRESS)
    worker.add_argument("app", choices=APPS)
    worker.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "start":
            return start(args.app, args.port)
        if args.command == "check":
            apps = list(dict.fromkeys(args.apps or APPS))
            if any(app not in APPS for app in apps):
                raise ValueError("list にあるアプリ名を指定してください。")
            return check(apps, args.output)
        if args.command == "_probe":
            readiness, startup, operation, calculation, evidence = probe(args.app, args.directory)
            _, directory, entry, _, _ = APPS[args.app]
            (args.directory / "probe.json").write_text(json.dumps({
                "status":"ok", "readiness":readiness, "startup_s":startup,
                "operation":operation, "operation_s":calculation, "evidence":evidence,
                "entry_sha256":sha256((app_directory(directory) / entry).read_bytes()).hexdigest(),
                "python_version":sys.version.split()[0],
            }, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            return 0
        for key, (label, *_) in APPS.items():
            print(f"{key:14} {label}")
        print("起動  start <app>    検証  check --output <new.json>")
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
