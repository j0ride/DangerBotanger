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
        self.db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()

    def language(self):
        row = self.db.execute("SELECT value FROM settings WHERE key='language'").fetchone()
        return row[0] if row and row[0] in {'br', 'en'} else 'br'

    def set_language(self, language):
        if language not in {'br', 'en'}:
            raise ValueError('Unsupported language')
        with self.db:
            self.db.execute("INSERT INTO settings(key,value) VALUES('language',?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (language,))

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

    def get(self, request_id):
        return self.db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()

    def pending(self, limit=3):
        return self.db.execute(
            "SELECT * FROM requests WHERE status='pending' ORDER BY id LIMIT ?", (limit,)).fetchall()

    def update(self, request_id, status):
        with self.db:
            self.db.execute("UPDATE requests SET status=? WHERE id=?", (status, request_id))

    def close(self):
        self.db.close()
