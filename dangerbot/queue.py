import sqlite3
from pathlib import Path


class RequestQueue:
    """Persistent outbox. 'sent' means delivered, never proof of playback."""
    def __init__(self, path="data/queue.sqlite3"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY, user TEXT NOT NULL, uri TEXT NOT NULL,
            label TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        # A crash during POST cannot safely be retried (Spotify has no idempotency key).
        self.db.execute("UPDATE requests SET status='uncertain' WHERE status='sending'")
        self.db.commit()

    def add(self, user, track):
        with self.db:
            cursor = self.db.execute("INSERT INTO requests(user,uri,label) VALUES(?,?,?)",
                                     (user, track.uri, track.label))
        return cursor.lastrowid

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM requests WHERE status IN ('pending','sending')").fetchone()[0]

    def duplicate(self, uri):
        return self.db.execute("SELECT 1 FROM requests WHERE uri=? AND status IN ('pending','sending')",
                               (uri,)).fetchone() is not None

    def next(self):
        return self.db.execute("SELECT * FROM requests WHERE status='pending' ORDER BY id LIMIT 1").fetchone()

    def update(self, request_id, status):
        with self.db:
            self.db.execute("UPDATE requests SET status=? WHERE id=?", (status, request_id))

    def close(self):
        self.db.close()
