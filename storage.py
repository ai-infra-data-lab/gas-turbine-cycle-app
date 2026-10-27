"""Immutable saved calculation cases, separate from the design manager database."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID


class CaseStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, document TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, case):
        with self.connect() as db:
            db.execute("INSERT INTO cases VALUES(?,?,?)", (case["run_id"], case["completed_at"], json.dumps(case, ensure_ascii=False, allow_nan=False)))
        return case

    def list(self):
        with self.connect() as db:
            results = []
            for row in db.execute("SELECT * FROM cases ORDER BY rowid DESC"):
                case = json.loads(row["document"])
                results.append({"id": row["id"], "name": case["input"]["name"], "created_at": row["created_at"],
                                "mode": case["input"]["mode"], "status": case["result"]["status"],
                                "net_power_w": case["result"]["metrics"]["net_electric_power_w"],
                                "efficiency": case["result"]["metrics"]["electric_efficiency_lhv"]})
            return results

    def get(self, run_id):
        try:
            run_id = str(UUID(run_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("計算ケースIDが不正です。") from None
        with self.connect() as db:
            row = db.execute("SELECT document FROM cases WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise LookupError("保存した計算ケースが見つかりません。")
        return json.loads(row["document"])
