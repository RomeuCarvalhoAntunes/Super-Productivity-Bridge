"""Persist import links and serialize writes across importer instances."""
import fcntl
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Journal:
    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock_path = directory / "import.lock"
        self.db = sqlite3.connect(directory / "imports.sqlite3")
        self.db.execute("""CREATE TABLE IF NOT EXISTS imports (
            external_id TEXT PRIMARY KEY, task_id TEXT UNIQUE,
            status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
        )""")
        # Translate records from builds that stored status values in Portuguese.
        self.db.executemany(
            "UPDATE imports SET status = ? WHERE status = ?",
            (("pending", "pendente"), ("linked", "vinculada"), ("completed", "concluída")),
        )
        self.db.commit()

    @contextmanager
    def lock(self):
        with self.lock_path.open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another instance is applying an import.") from None
            yield

    def get(self, external_id):
        return self.db.execute(
            "SELECT task_id, status FROM imports WHERE external_id = ?", (external_id,)
        ).fetchone()

    def owner(self, task_id):
        return self.db.execute(
            "SELECT external_id FROM imports WHERE task_id = ?", (task_id,)
        ).fetchone()

    def record(self, external_id, task_id, status, detail=""):
        with self.db:
            self.db.execute(
                """INSERT INTO imports VALUES (?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET task_id=excluded.task_id,
                status=excluded.status, detail=excluded.detail""",
                (external_id, task_id, status, detail),
            )

    def clear_pending(self, external_id):
        with self.db:
            self.db.execute(
                "DELETE FROM imports WHERE external_id = ? AND status = 'pending'", (external_id,)
            )


def local_journal() -> Journal:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return Journal(base / "super-productivity-bridge")
