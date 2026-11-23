"""Loopback-only UI/API. No external dependencies or cloud requests."""
import argparse
import json
import secrets
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from engine import ConvergenceError, solve, sweep
from model import MAX_EDGES, MAX_NODES, ModelError, sample, stage2_sample, rotating_sample, validate
from vortex import solve_vortex
from grid_study import grid_study
from storage import Store

ROOT = Path(__file__).resolve().parent
MAX_REQUEST_BYTES = 32 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    def send(self, value, status=200, mime="application/json; charset=utf-8"):
        if not isinstance(value, bytes):
            value = (json.dumps(value, ensure_ascii=False, allow_nan=False) if mime.startswith("application/json") else value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(value)

    def trusted(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def do_GET(self):
        if not self.trusted():
            return self.send({"error": "Invalid Host"}, 403)
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/":
                html = (ROOT / "static/index.html").read_text(encoding="utf-8")
                html = (html.replace("__TOKEN__", self.server.token)
                            .replace("__MAX_NODES__", str(MAX_NODES))
                            .replace("__MAX_EDGES__", str(MAX_EDGES)))
                return self.send(html, mime="text/html; charset=utf-8")
            static = {"/app.js": "text/javascript; charset=utf-8", "/style.css": "text/css; charset=utf-8"}
            if parsed.path in static:
                return self.send((ROOT / "static" / parsed.path[1:]).read_bytes(), mime=static[parsed.path])
            if parsed.path == "/api/sample":
                return self.send(sample())
            if parsed.path == "/api/sample-stage2":
                return self.send(stage2_sample())
            if parsed.path == "/api/sample-rotating":
                return self.send(rotating_sample())
            if parsed.path == "/api/cases":
                return self.send(self.server.store.list())
            if parsed.path == "/api/case":
                return self.send(self.server.store.get(parse_qs(parsed.query).get("id", [""])[0]))
            return self.send({"error": "見つかりません。"}, 404)
        except LookupError as exc:
            return self.send({"error": str(exc)}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self.trusted() or self.headers.get("X-Network-Token") != self.server.token or origin not in (
                None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"):
            return self.send({"error": "このアプリの画面から操作してください。"}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_REQUEST_BYTES:
                return self.send({"error": f"入力JSONは{MAX_REQUEST_BYTES // (1024 * 1024)} MiB以内で指定してください。"}, 413)
            def bad_constant(_):
                raise ModelError("NaN/Infinityは使用できません。")
            data = json.loads(self.rfile.read(size), parse_constant=bad_constant)
            if not isinstance(data, dict):
                raise ModelError("JSONオブジェクトを指定してください。")
            if self.path == "/api/validate":
                return self.send(validate(data.get("model")))
            if self.path == "/api/vortex":
                return self.send(solve_vortex(data.get("vortex"), data.get("gas")))
            if self.path not in ("/api/solve", "/api/save", "/api/sweep", "/api/grid-study"):
                return self.send({"error": "見つかりません。"}, 404)
            if not self.server.calculation_lock.acquire(blocking=False):
                return self.send({"error": "他の計算を実行中です。完了後に操作してください。"}, 409)
            try:
                if self.path == "/api/grid-study":
                    result = grid_study(data.get("model"), data.get("relative_percent", .1))
                elif self.path == "/api/sweep":
                    result = sweep(data.get("model"), data.get("target"), data.get("values"))
                else:
                    result = solve(data.get("model"))
                    if self.path == "/api/save":
                        result = self.server.store.save(result)
                return self.send(result)
            finally:
                self.server.calculation_lock.release()
        except ConvergenceError as exc:
            return self.send({"error": str(exc), "diagnostics": exc.diagnostics}, 400)
        except (ValueError, TypeError) as exc:
            return self.send({"error": str(exc)}, 400)
        except Exception:
            traceback.print_exc()
            return self.send({"error": "処理に失敗しました。起動ウィンドウの診断を確認してください。"}, 500)


def create_server(port=8790, db_path=None):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.token = secrets.token_urlsafe(32)
    server.calculation_lock = threading.Lock()
    server.store = Store(db_path or ROOT / "data/cases.sqlite3")
    return server


def main():
    parser = argparse.ArgumentParser(description="Secondary Air Network / local steady solver")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--db", type=Path, default=ROOT / "data/cases.sqlite3")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    with create_server(args.port, args.db) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Secondary Air Network: {url}\nStop: Ctrl+C", flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
