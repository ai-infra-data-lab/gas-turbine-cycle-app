"""Append-only local case snapshots. Recompute before a snapshot is saved."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,name,created FROM cases ORDER BY created DESC")]

    def get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM cases WHERE id=?", (key,)).fetchone()
        if row is None:
            raise LookupError("保存ケースが見つかりません。")
        return {"id": row["id"], "name": row["name"], "created": row["created"], "result": json.loads(row["payload"])}

    def save(self, result):
        key, created = str(uuid.uuid4()), datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("INSERT INTO cases VALUES (?,?,?,?)", (key, result["model"]["name"], created, json.dumps(result, ensure_ascii=False, allow_nan=False)))
        return self.get(key)
