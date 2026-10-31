"""Loopback-only, offline standard-library UI for the independent cycle module."""
import argparse
import json
import secrets
import sqlite3
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if __package__:
    from .engine import run_case, sweep
    from .model import MODULE_ID, MODULE_VERSION, default_input, ui_schema, validate_input, element_stage, cooling_circuit
    from .storage import CaseStore
    from .thermo import provenance, FUEL_NAMES
    from . import shaft_joints as joints
    from .joint_store import JointStore
    from . import evaluation
else:
    from engine import run_case, sweep
    from model import MODULE_ID, MODULE_VERSION, default_input, ui_schema, validate_input, element_stage, cooling_circuit
    from storage import CaseStore
    from thermo import provenance, FUEL_NAMES
    import shaft_joints as joints
    from joint_store import JointStore
    import evaluation

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)


def read_input_file(data):
    if not isinstance(data, dict):
        raise ValueError("JSONオブジェクトを指定してください。")
    if data.get("format") == "gas-turbine-cycle-case":
        if type(data.get("schema_version")) is not int or data.get("schema_version") not in (1, 2, 3, 4, 5, 6) or data.get("module_id") != MODULE_ID or data.get("module_version") not in ("0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0", MODULE_VERSION):
            raise ValueError("このバージョンに対応した計算ケースを選択してください。")
        data = data.get("input")
    return validate_input(data)


class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, mime="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = data.encode("utf-8")
        self.send_response(status)
        for name, value in (("Content-Type", mime), ("Content-Length", str(len(data))), ("Cache-Control", "no-store"),
                            ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                            ("Referrer-Policy", "no-referrer"),
                            ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def json(self, value, status=200):
        self.send(status, json.dumps(value, ensure_ascii=False, allow_nan=False))

    def trusted(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def do_GET(self):
        if not self.trusted():
            return self.json({"error": "Invalid Host"}, 403)
        path = urlsplit(self.path)
        query = parse_qs(path.query)
        try:
            if path.path == "/":
                return self.send(200, (ROOT / "static/index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
            if path.path == '/shaft-joints':
                return self.send(200,(ROOT/'static/joints.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN),'text/html; charset=utf-8')
            if path.path == '/test-evaluation':
                return self.send(200,(ROOT/'static/evaluation.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN),'text/html; charset=utf-8')
            static = {"/app.js": ("static/app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("static/style.css", "text/css; charset=utf-8"),
                      "/guide": ("static/guide.html", "text/html; charset=utf-8"),
                      '/evaluation.js':('static/evaluation.js','text/javascript; charset=utf-8'),
                      '/evaluation.css':('static/evaluation.css','text/css; charset=utf-8'),
                      '/test-evaluation/guide':('static/evaluation-guide.html','text/html; charset=utf-8'),
                      '/joints.js':('static/joints.js','text/javascript; charset=utf-8'),
                      '/joints.css':('static/joints.css','text/css; charset=utf-8'),
                      '/shaft-joints/guide':('static/joints-guide.html','text/html; charset=utf-8')}
            if path.path in static:
                name, mime = static[path.path]
                return self.send(200, (ROOT / name).read_bytes(), mime)
            if path.path == "/api/schema":
                return self.json({"fields": ui_schema(), "default": default_input(), "module_id": MODULE_ID, "module_version": MODULE_VERSION,
                                  "thermo_dataset": provenance(), "fuels": FUEL_NAMES, "cooling_circuit_template": cooling_circuit(), "element_stage_templates": {key: element_stage(key) for key in ("compressor", "turbine")}})
            if path.path == "/api/cases":
                return self.json(self.server.store.list())
            if path.path == "/api/case":
                return self.json(self.server.store.get(query.get("id", [""])[0]))
            if path.path == '/api/joints/schema':
                return self.json({'module_id':joints.MODULE_ID,'module_version':joints.MODULE_VERSION,
                                  'fields':{k:joints.schema(k) for k in ('spline','curvic')},
                                  'defaults':{k:joints.default_input(k) for k in ('spline','curvic')}})
            if path.path == '/api/joints/cases': return self.json(self.server.joint_store.list())
            if path.path == '/api/joints/case': return self.json(self.server.joint_store.get(query.get('id',[''])[0]))
            if path.path == '/api/evaluation/schema':
                return self.json({'example':evaluation.example_input(),'units':evaluation.UNITS,'conditions':evaluation.CONDITIONS})
            if path.path == '/api/evaluation/cases': return self.json(self.server.evaluation_store.list())
            if path.path == '/api/evaluation/case': return self.json(self.server.evaluation_store.get(query.get('id',[''])[0]))
            return self.json({"error": "見つかりません。"}, 404)
        except (ValueError, LookupError) as exc:
            return self.json({"error": str(exc)}, 404)
        except (OSError, sqlite3.Error):
            traceback.print_exc()
            return self.json({"error": "保存先またはファイルを読み込めませんでした。"}, 500)

    def do_POST(self):
        if not self.trusted() or self.headers.get("X-Cycle-Token") != TOKEN or self.headers.get("Origin") not in (
                None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"):
            return self.json({"error": "このアプリの画面から操作してください。"}, 403)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 1_000_000:
                return self.json({"error": "JSONは1MB以内にしてください。"}, 413)
            data = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("非有限数は使用できません。")))
            if not isinstance(data, dict):
                raise ValueError("JSONオブジェクトを指定してください。")
            if self.path == "/api/calculate":
                return self.json(run_case(data.get("input")))
            if self.path == "/api/save":
                # Never accept a client-supplied result as a verified calculation.
                return self.json(self.server.store.save(run_case(data.get("input"))))
            if self.path == "/api/sweep":
                return self.json(sweep(data.get("input"), data.get("minimum", 1.2), data.get("maximum", 20), data.get("count", 31)))
            if self.path == "/api/import":
                return self.json({"input": read_input_file(data.get("document")), "note": "入力だけを取り込みました。結果は再計算してください。"})
            if self.path == '/api/joints/calculate': return self.json(joints.run_case(data.get('input')))
            if self.path == '/api/evaluation/calculate': return self.json(evaluation.run_case(data.get('input')))
            if self.path == '/api/evaluation/save': return self.json(self.server.evaluation_store.save(evaluation.run_case(data.get('input'))))
            if self.path == '/api/evaluation/import': return self.json({'input':evaluation.read_input_file(data.get('document'))})
            if self.path == '/api/evaluation/catalog':
                return self.json(evaluation.catalog(run_case(data.get('input'))['result']))
            if self.path == '/api/evaluation/preview':
                headers, rows=evaluation.parse_csv(data.get('csv_text'),data.get('time_column'))
                return self.json({'headers':headers,'rows':len(rows),'start_s':rows[0][0],'end_s':rows[-1][0]})
            if self.path == '/api/joints/save': return self.json(self.server.joint_store.save(joints.run_case(data.get('input'))))
            if self.path == '/api/joints/import': return self.json({'input':joints.read_input_file(data.get('document'))})
            if self.path == '/api/joints/cycle-load':
                case=self.server.store.get(data.get('case_id',''))
                return self.json(joints.cycle_load(case,data.get('position')))
            return self.json({"error": "見つかりません。"}, 404)
        except (ValueError, TypeError, KeyError, OverflowError, LookupError) as exc:
            self.json({"error": str(exc)}, 400)
        except sqlite3.Error:
            traceback.print_exc()
            self.json({"error": "計算ケースを保存できませんでした。起動ウィンドウを確認してください。"}, 500)


def make_server(data_path, port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.store = CaseStore(data_path)
    joint_path=Path(data_path).with_name(Path(data_path).stem+'.shaft-joints.sqlite3')
    server.joint_store=JointStore(joint_path)
    server.evaluation_store=evaluation.EvaluationStore(Path(data_path).with_name(Path(data_path).stem+'.test-evaluation.sqlite3'))
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--data", type=Path, default=ROOT / "data/cases.sqlite3")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        server = make_server(args.data, args.port)
    except OSError as exc:
        raise SystemExit(f"起動できません。ポートが使用中なら --port 8781 を指定してください。\n{exc}")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Cycle Studio: {url}\n保存先: {args.data.resolve()}\n終了: Ctrl+C", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
